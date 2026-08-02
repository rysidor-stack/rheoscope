#!/usr/bin/env python3
"""check-path-containment.py -- OPS-5 path-containment sensor (memory-engine v3, P0).

Every path the engine opens as an LLM-AUTHORED path (a receipt field, a journal
record, a frontmatter `subscribes`/`bundle`/`sources` entry) must route through
`resolve_within(root, candidate)` before any open. This sensor is that function +
its adversarial self-test (test-plan OPS-5: 5 attack vectors).

`resolve_within` enforces, in this order:
  1. ADS reject -- any ':' beyond a leading drive letter (NTFS alternate data
     stream, e.g. `receipts/journal/0042.json:payload`) is rejected BEFORE any
     filesystem call (the ADS hides bytes from git and every sensor).
  2. literal `..` reject -- any parent-traversal path component.
  3. realpath-before-commonpath -- realpath() BOTH the root and the candidate,
     THEN require commonpath([root, candidate]) == root. realpath() resolves
     symlinks/junctions, so an in-root symlink whose target escapes the root is
     caught here (its string form would pass commonpath; its realpath does not),
     and an absolute path outside the root is caught the same way. realpath() is
     applied to the ROOT too, so a root reached via a junction/subst (literal
     path != realpath) does not falsely reject its own legitimate descendants.

All three are FATAL: the caller exits 1, names the path, opens nothing.

TOCTOU (hardened 2026-07-03, the mig1 Appendix-K OPS-5 rider): `resolve_within`
alone still carries the check-then-open window (spec §16's stated residual).
`open_within` CLOSES it for file opens -- open, then verify on the handle
(post-open re-resolution + containment re-check + fstat/stat identity match),
with a deterministic plant-symlink race fixture in the self-test. Engine code
opening an LLM-authored path must use `open_within`, not resolve-then-open.

Usage:
  check-path-containment.py --self-test           run the 5-vector adversarial battery
                                                  + the open-then-verify race fixtures
  check-path-containment.py --check ROOT PATH...   resolve each PATH within ROOT (runtime)

Exit codes: 0 = all contained / clean | 1 = a containment violation, or a
self-test failure | 2 = inconclusive (could not plant a reparse-point fixture).

Stdlib-only (no PyYAML) -- matches the contract layer's no-new-dependency rule.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

try:  # cp1252 consoles + unicode paths: never let an encode error mask a verdict.
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# A ':' that is NOT the drive-letter colon (position 1, e.g. "C:"). Anything else
# is an NTFS alternate-data-stream specifier and is rejected before any FS call.
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")


class PathContainmentError(Exception):
    """Raised by resolve_within when a candidate path escapes the root."""


def _has_ads(candidate):
    """True if candidate carries an NTFS ADS ':' beyond an optional drive letter."""
    rest = _DRIVE_PREFIX_RE.sub("", candidate, count=1)
    return ":" in rest


def _has_dotdot(candidate):
    """True if any path component is exactly '..' (literal parent traversal)."""
    norm = candidate.replace("\\", "/")
    return any(part == ".." for part in norm.split("/"))


def resolve_within(root, candidate):
    """Return the realpath of `candidate` resolved within `root`, or raise
    PathContainmentError. `candidate` is an untrusted LLM-authored path string
    (relative to root, or absolute). Order is load-bearing: ADS first (no FS
    call), then literal '..', then realpath-both + commonpath."""
    if _has_ads(candidate):
        raise PathContainmentError("NTFS alternate-data-stream ':' in path: %r" % candidate)
    if _has_dotdot(candidate):
        raise PathContainmentError("literal '..' parent-traversal component: %r" % candidate)

    root_real = os.path.realpath(root)
    target = candidate if os.path.isabs(candidate) else os.path.join(root, candidate)
    target_real = os.path.realpath(target)

    try:
        common = os.path.commonpath([root_real, target_real])
    except ValueError:
        # Different drives / mixed absolute+relative roots -> definitionally outside.
        raise PathContainmentError("path is not under root (different volume): %r" % candidate)
    if common != root_real:
        raise PathContainmentError(
            "resolved path escapes root: %r -> %s (root %s)" % (candidate, target_real, root_real))
    return target_real


def open_within(root, candidate, mode="r", encoding=None,
                _race_hook=None, _post_open_hook=None):
    """OPEN-THEN-VERIFY (the mig1 Appendix-K OPS-5 rider, 2026-07-03): open the
    candidate and verify containment + identity ON THE OPENED HANDLE, closing the
    resolve_within/open TOCTOU window that spec §16 previously accepted.

    Sequence (each step's failure closes the handle and raises):
      1. resolve_within(root, candidate)  -- all static rejects (ADS/../abs) +
         the pre-open realpath containment check.
      2. open() the resolved path.
      3. POST-OPEN re-resolution: realpath(candidate) again and re-run the
         containment check. A link swapped to an out-of-root target between
         steps 1 and 2 fails HERE (the fresh realpath now escapes).
      4. IDENTITY: os.fstat(handle) (race-free for an open handle) must equal
         os.stat(fresh_realpath) on (st_dev, st_ino). A swap-BACK attack (link ->
         evil before open, restored before step 3, so step 3 sees the innocent
         target) fails HERE: the handle's identity is the evil file, the fresh
         path's identity is the innocent one.
    An attacker swapping the link at any point therefore loses to step 3 or
    step 4. `_race_hook` / `_post_open_hook` are TEST-ONLY injection points
    (called between check/open and after open respectively) so the self-test's
    plant-symlink race fixture is deterministic, not timing-dependent.

    Returns the open file object; the caller owns closing it."""
    pre_real = resolve_within(root, candidate)
    if _race_hook is not None:
        _race_hook()                       # the check->open window, made explicit
    fh = open(pre_real, mode, encoding=encoding)
    try:
        if _post_open_hook is not None:
            _post_open_hook()              # the open->verify window
        root_real = os.path.realpath(root)
        target = candidate if os.path.isabs(candidate) else os.path.join(root, candidate)
        fresh_real = os.path.realpath(target)
        try:
            common = os.path.commonpath([root_real, fresh_real])
        except ValueError:
            raise PathContainmentError(
                "post-open re-resolution left the root volume: %r" % candidate)
        if common != root_real:
            raise PathContainmentError(
                "post-open re-resolution escapes root (raced link?): %r -> %s"
                % (candidate, fresh_real))
        hid = os.fstat(fh.fileno())
        pid = os.stat(fresh_real)
        if (hid.st_dev, hid.st_ino) != (pid.st_dev, pid.st_ino):
            raise PathContainmentError(
                "opened-handle identity mismatch (raced link swap-back?): %r "
                "handle=(%s,%s) path=(%s,%s)"
                % (candidate, hid.st_dev, hid.st_ino, pid.st_dev, pid.st_ino))
    except BaseException:
        fh.close()
        raise
    return fh


# ---------------------------------------------------------------------------
# Runtime mode
# ---------------------------------------------------------------------------

def check_paths(root, candidates):
    violations = 0
    for c in candidates:
        try:
            resolved = resolve_within(root, c)
            print("  ok     %s -> %s" % (c, resolved))
        except PathContainmentError as e:
            violations += 1
            print("  REJECT %s" % e)
    if violations:
        print("RESULT: FAIL -- %d path-containment violation(s)" % violations)
        return 1
    print("RESULT: PASS -- %d path(s) contained" % len(candidates))
    return 0


# ---------------------------------------------------------------------------
# Reparse-point fixture planting (the "plant-symlink fixture" obligation, F10).
# POSIX: os.symlink (no privilege). Windows: a directory junction via `mklink /J`
# (no admin). Both are reparse points realpath() must resolve through.
# ---------------------------------------------------------------------------

def _plant_reparse_dir(link_path, target_dir):
    """Make link_path a reparse point onto target_dir. Returns the mechanism used,
    or raises OSError/subprocess error if it cannot be planted."""
    if os.name == "nt":
        # /J = directory junction; no administrator privilege required on NTFS.
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link_path, target_dir],
                           capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(link_path):
            raise OSError("mklink /J failed: %s" % (r.stderr or r.stdout).strip())
        return "junction"
    os.symlink(target_dir, link_path, target_is_directory=True)
    return "symlink"


def _unplant(link_path):
    """Remove a reparse point without touching its target (rmdir on a junction/
    dir-symlink removes the link only)."""
    try:
        if os.path.isdir(link_path):
            os.rmdir(link_path)
        elif os.path.lexists(link_path):
            os.unlink(link_path)
    except OSError:
        pass


def self_test():
    """Plant the 5 OPS-5 attack vectors in a temp tree and assert each verdict.
    Pure-string vectors (ADS, '..', absolute) always run; reparse-point vectors
    (symlink/junction escape, junction-root) need a plantable reparse point ->
    INCONCLUSIVE (exit 2) if the platform refuses one, never a false PASS."""
    base = tempfile.mkdtemp(prefix="ops5-")
    failed = 0
    inconclusive = 0
    total = 0

    def case(name, ok):
        nonlocal failed, total
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    def rejects(root, cand):
        try:
            resolve_within(root, cand)
            return False
        except PathContainmentError:
            return True

    def accepts(root, cand):
        try:
            resolve_within(root, cand)
            return True
        except PathContainmentError:
            return False

    try:
        root = os.path.join(base, "root")
        outside = os.path.join(base, "outside")
        os.makedirs(os.path.join(root, "wiki", "systems"))
        os.makedirs(outside)
        with open(os.path.join(root, "wiki", "systems", "x.md"), "w") as fh:
            fh.write("ok\n")
        with open(os.path.join(outside, "secret.md"), "w") as fh:
            fh.write("secret\n")

        # 1. literal '..' (pure string; rejected before any FS call)
        case("dotdot-event ('../../raw/escape.md')", rejects(root, "../../raw/escape.md"))

        # 2. absolute path outside root
        abs_evil = "C:/Windows/System32/evil.txt" if os.name == "nt" else "/etc/passwd"
        case("abs-path-event ('%s')" % abs_evil, rejects(root, abs_evil))

        # 3. NTFS ADS colon (rejected before any FS call)
        case("ads-path-event ('receipts/journal/0042.json:payload')",
             rejects(root, "receipts/journal/0042.json:payload"))

        # sanity: a legitimate in-root path is ACCEPTED (no false positives)
        case("legal in-root ('wiki/systems/x.md')", accepts(root, "wiki/systems/x.md"))

        # 4. reparse-point ESCAPE: a link inside root resolves OUT of root.
        #    Its string form ('escape_dir/secret.md') would pass commonpath; its
        #    realpath does not -> proves realpath-before-commonpath ordering.
        link = os.path.join(root, "escape_dir")
        try:
            mech = _plant_reparse_dir(link, outside)
            case("symlink-escape via %s ('escape_dir/secret.md')" % mech,
                 rejects(root, "escape_dir/secret.md"))
            _unplant(link)
        except (OSError, subprocess.SubprocessError) as e:
            inconclusive += 1
            print("  ?? symlink-escape INCONCLUSIVE (cannot plant reparse point: %s)" % e)

        # 5. junction-ROOT: the root itself reached via a reparse point (literal
        #    path != realpath). A legitimate descendant must still be ACCEPTED ->
        #    proves realpath() is applied to the ROOT argument, not only candidate.
        jroot = os.path.join(base, "jroot")
        try:
            mech = _plant_reparse_dir(jroot, root)
            case("junction-root legal descendant ACCEPTED via %s" % mech,
                 accepts(jroot, "wiki/systems/x.md"))
            # and an escape through the junction-root is still rejected
            link2 = os.path.join(root, "escape_dir2")
            try:
                _plant_reparse_dir(link2, outside)
                case("junction-root + escape still REJECTED",
                     rejects(jroot, "escape_dir2/secret.md"))
                _unplant(link2)
            except (OSError, subprocess.SubprocessError):
                pass
            _unplant(jroot)
        except (OSError, subprocess.SubprocessError) as e:
            inconclusive += 1
            print("  ?? junction-root INCONCLUSIVE (cannot plant reparse point: %s)" % e)
        # --- OPS-5 rider (mig1 Appendix-K, built 2026-07-03): open-then-verify ---
        # The plant-symlink RACE fixture: the attacker swaps a directory component
        # of the ALREADY-RESOLVED path for a reparse point between the containment
        # check and the open (deterministic via the test-only hooks, not timing).
        sub = os.path.join(root, "sub")
        os.makedirs(sub, exist_ok=True)
        with open(os.path.join(sub, "good.md"), "w") as fh:
            fh.write("good\n")
        with open(os.path.join(outside, "good.md"), "w") as fh:
            fh.write("evil twin\n")
        moved = sub + "_moved"

        def swap_sub_to_outside():
            os.rename(sub, moved)
            _plant_reparse_dir(sub, outside)

        def restore_sub():
            _unplant(sub)
            os.rename(moved, sub)

        try:
            # benign: open-then-verify returns a readable handle
            fh = open_within(root, "sub/good.md")
            case("open_within benign path opens + verifies", fh.read() == "good\n")
            fh.close()

            # race A: component swapped to a junction between check and open ->
            # the open lands on the evil twin; the POST-OPEN re-resolution rejects
            try:
                fh = open_within(root, "sub/good.md", _race_hook=swap_sub_to_outside)
                fh.close()
                case("race fixture: check->open swap REJECTED (post-open re-resolution)",
                     False)
            except PathContainmentError:
                case("race fixture: check->open swap REJECTED (post-open re-resolution)",
                     True)
            finally:
                restore_sub()

            # race B: swap-BACK -- evil during the open, innocent again before the
            # verify; the handle/path IDENTITY comparison rejects
            try:
                fh = open_within(root, "sub/good.md",
                                 _race_hook=swap_sub_to_outside,
                                 _post_open_hook=restore_sub)
                fh.close()
                case("race fixture: swap-BACK REJECTED (handle identity mismatch)",
                     False)
            except PathContainmentError as e:
                case("race fixture: swap-BACK REJECTED (handle identity mismatch)",
                     "identity mismatch" in str(e))
        except (OSError, subprocess.SubprocessError) as e:
            inconclusive += 1
            print("  ?? race fixtures INCONCLUSIVE (cannot plant reparse point: %s)" % e)
            if os.path.isdir(moved) and not os.path.isdir(sub):
                os.rename(moved, sub)
    finally:
        # Unplant any survivors, then remove the temp tree (all under `base`).
        for leftover in ("root/escape_dir", "root/escape_dir2", "jroot", "root/sub"):
            _unplant(os.path.join(base, *leftover.split("/")))
        shutil.rmtree(base, ignore_errors=True)

    if failed:
        print("check-path-containment self-test: FAIL (%d/%d)" % (failed, total))
        return 1
    if inconclusive:
        print("check-path-containment self-test: INCONCLUSIVE (%d vector(s) unplantable; %d/%d passed)"
              % (inconclusive, total, total))
        return 2
    print("check-path-containment self-test: PASS (%d/%d)" % (total, total))
    return 0


def main(argv):
    args = argv[1:]
    if "--self-test" in args:
        return self_test()
    if "--check" in args:
        rest = [a for a in args if a != "--check"]
        if len(rest) < 2:
            print("usage: check-path-containment.py --check ROOT PATH [PATH ...]")
            return 1
        return check_paths(rest[0], rest[1:])
    print(__doc__.strip().splitlines()[0])
    print("run with --self-test or --check ROOT PATH...")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
