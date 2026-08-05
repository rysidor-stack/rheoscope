#!/usr/bin/env python3
"""compile-driver.py -- the shipped compile driver: /compile's single entry point
into the memory engine (backlog v3.0-65; build spec
harness-v3.0/specs/compile-engine-wiring-build-spec-2026-07-28.md sec.B-1).

WHY THIS FILE EXISTS. `compile-v2.py --run` is hardwired to FixtureAbsorbBackend
(compile-v2.py:3298) and can never carry live content; the live seam is
compile-backends.run_guarded() + FileHandoffAbsorbBackend over a staging dir,
which until now had NO shipped CLI -- only the throwaway-driver pattern in
docs/engine/OPERATIONS.md. This module is that CLI: thin, stdlib-only,
self-testing. It does NO judgment and NO content authoring -- the plan and the
absorb answers are authored by the /compile skill's model session and handed
over as files in `--staging`.

  compile-driver.py --run --root DIR --staging DIR --authorization PATH [--sections]
  compile-driver.py --reverify --root DIR --seq N --staging DIR --authorization PATH
  compile-driver.py --revert --root DIR --seq N [--reason TEXT]
  compile-driver.py --reconcile --root DIR
  compile-driver.py --self-test
  Exit: 0 clean | 1 validation/gate failure | 2 inconclusive/usage | 3 lock held

STAGING CONTRACT (what the skill must produce; see emit_packets()/stamp_dispatch()
in compile-backends.py, which are the sanctioned way to produce it):
  <staging>/plan.json                {"items": [{"view", "events", "event_class"}]}
  <staging>/dispatch-manifest.json   emit_packets() output, THEN stamp_dispatch()ed
                                     (F17 attestation; an unstamped manifest is
                                     refused by FileHandoffAbsorbBackend and by
                                     BridgeVerifyBackend alike)
  <staging>/answers/NN-<slug>.json   one absorb answer per manifest packet entry,
                                     {"new_text", "manifest", "corpus_support", "noops"}

PRE-WRITE FAIL-CLOSED SEQUENCE (build spec B-1, corrected per the 2026-07-28
round-2 cross-vendor review -- authorization is validated BEFORE anything is
written). In order, and NOTHING is written or committed until all of them pass:
  1. argv parse. `--authorization` is REQUIRED for `--run`. **There is no
     `--no-verify` flag** -- an argv carrying one is a parse error, so no flag
     combination exists that runs a live absorb without verify.
  2. authorization artifact validation (validate_authorization() below).
  3. staging-dir validation (validate_staging(): plan parses, manifest parses AND
     carries a well-formed F17 dispatch stamp, every answer file exists/parses/
     carries the four required keys, manifest views == plan views).
  4. a SIDE-EFFECT-FREE dry run of compile-backends.dispatch_guard("verify", ...)
     over the (event, view) pairs the plan will actually transmit -- so a
     hard-refusal disposition (free-selection egress / untrusted origin_max) is
     discovered before the absorb, not after. dispatch_guard's own refusal
     inside verify_run_guarded() remains as defense-in-depth behind this check,
     not as the first line.
  5. startup reconciliation (see below).
Only then is FileHandoffAbsorbBackend constructed and run_guarded() called.

AUTHORIZATION VALIDATION -- exactly what is checked (build spec B-1: "a documented
substring/marker check is acceptable -- document what you check"). The shipped
grant is deploy/evidence/operator-standing-verify-authorization-2026-07-28.md.
  (a) the path resolves inside the repo (compile-backends' path-containment);
  (b) its repo-relative POSIX form is in the AUTHORIZATION-ARTIFACT CLASS --
      directory exactly `deploy/evidence`, basename `operator-*.md`, case-
      sensitive. This reuses compile-backends._is_authorization_artifact_class
      so the driver's pre-write check and dispatch_guard's own check can never
      diverge;
  (c) the file exists and is non-empty;
  (d) NON-REVOCATION: no line of the file, ignoring markdown emphasis/list
      markers, starts with "REVOKED" (the revocation shape this artifact class
      documents: "this file gains a dated REVOKED line");
  (e) COVERAGE of the pending dispatch, as two required case-insensitive
      markers in the file's text: the word "verify" (this is a verify-leg
      grant, not some other operator decision) AND the literal string
      "compile-driver.py" (it covers THIS wired compile path, not a different
      dispatch). A grant that names neither is "non-covering" and refused;
  (f) a VERBATIM QUOTE to hand dispatch_guard: the first double-quoted span
      (straight or typographic quotes) on/after a line containing
      "Verbatim grant:". dispatch_guard requires a quote that appears verbatim
      in the artifact -- extracting it from the artifact's own declared grant
      line keeps the driver from inventing one, and a file with no such line is
      refused rather than defaulted.
This is a MARKER check, deliberately: judging whether prose authorizes an action
is not a mechanical act, so the driver refuses everything that is not obviously
in class and leaves the rest to the operator who wrote the artifact.

ATOMICITY RULE (build spec B-1, normative, added per the round-3 review). The
absorb commit precedes verify -- verify_run() grades a COMMITTED run -- so the
driver guarantees no run ever ENDS holding an unverified absorption:
  * verify leg COMPLETES with a non-confirm verdict (revised/rejected): that is
    verified content; the verdict is journaled data. Exit 1, NO revert -- the run
    branch stays unmergeable until the non-confirm is adjudicated. The SHIPPED
    adjudication path is `--revert --seq N` (below): it reverts the run commit
    (journal record restored, revert journaled), after which the correction is
    re-absorbed through a fresh `--run` over corrected answers -- the full
    validate/absorb/verify road, never a hand-edit of the written views. Added
    2026-08-03 (backlog v3.0-local-5's second finding, reported from the first
    live all-rejected run): the skill instructed "fix the view text and re-run
    the verify leg", an operation nothing on this CLI performed -- corrections
    ended committed-but-unverified, and the only honest exits were a manual
    revert or shipping unverified text.
  * verify leg DOES NOT COMPLETE (bridge error, timeout, DispatchRefused
    mid-run, unparseable verdict, or an absorption-verify leg missing for an
    absorbed view): a leg's outcome is judged BY ITS VERDICT VALUE, not by
    whether verify_run() returned -- see classify_verdict() for the allowlist
    and the enumerated transport classes, and for the live defect (journal seq
    103) that made this necessary. The absorption is unverified -- the driver
    automatically
    reverts the run commit (`git revert -n` + a stage-only follow-up commit that
    RESTORES the run's journal record, because the journal is append-only and
    the reverted run must stay visible), journals the revert with its reason,
    leaves the staging dir untouched for a clean re-run, and exits 1.
  * A CONFLICTED revert is itself non-terminal: the conflict is aborted, the
    failure is journaled with status "revert-failed", and startup reconciliation
    blocks every later run until a human resolves it.

CRASH-WINDOW HONESTY + STARTUP RECONCILIATION (round-4 review). Process death
between the absorb commit and a completed revert CAN leave an unverified commit
on the branch -- the atomicity rule is crash-window-bounded, not absolute. So on
every `--run` (and on demand via `--reconcile`), before accepting work, the
driver reads the journal and refuses if the NEWEST run record lacks a terminal
verify disposition (a later record with verifies_seq == that seq, or a journaled
driver revert naming it). Unverified absorption is then a detected-and-blocking
state, never a silent one. The merge bars remain the outer backstop.
  *Documented scope decision (spec ambiguity, 2026-07-28):* the spec says "the
  newest run record on this branch lacking a terminal verify disposition". Read
  literally over ALL history that would block forever on this fork -- journal
  seq 68 is a pre-driver run with no verify record. The driver therefore GATES on
  the newest run record only (the crash-window case the rule exists for) and
  REPORTS older non-terminal runs as an advisory line instead of blocking on
  them. Historical unverified runs are the merge bars' business, not this run's.

VERIFY TIMEOUT: BridgeVerifyBackend resolves VERIFY_TIMEOUT_MS from the env only
when the caller passes no explicit pin (compile-backends.py:707-721, v3.0-20 --
env honored, explicit pin wins, 0 defers to verify-cli's own default). The driver
therefore reads the env itself and passes the value as an EXPLICIT pin, defaulting
to 540000 (the proven bridge posture) when the env is unset or unparseable.

Module loading: compile-v2.py / compile-backends.py have hyphens in their
filenames, so they are loaded exactly the way compile-backends.py itself loads
compile-v2 -- importlib.util.spec_from_file_location against this file's own
directory (see _load below). Loading is LAZY so `--self-test` can inject fakes
and never touch the real bridge.
"""

import json
import os
import posixpath
import re
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_INCONCLUSIVE = 2
EXIT_LOCK_HELD = 3

DEFAULT_VERIFY_TIMEOUT_MS = 540000

# Coverage markers for (e) above -- both required, case-insensitively.
AUTH_COVERAGE_MARKERS = ("verify", "compile-driver.py")

USAGE = (
    "compile-driver.py --run --root DIR --staging DIR --authorization PATH "
    "[--sections]\n"
    "compile-driver.py --reverify --root DIR --seq N --staging DIR "
    "--authorization PATH\n"
    "compile-driver.py --revert --root DIR --seq N [--reason TEXT]\n"
    "compile-driver.py --reconcile --root DIR\n"
    "compile-driver.py --self-test\n"
    "Exit: 0 clean | 1 validation/gate failure | 2 inconclusive | 3 lock held"
)


# --------------------------------------------------------------- module loading
def _load(basename, alias):
    """Same mechanism compile-backends.py uses to load compile-v2.py (hyphenated
    filenames are not importable as modules)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        alias, os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ENGINE_MODS = {}


def _backends():
    """compile-backends (which itself loads compile-v2, check-substrate, ...).
    Lazy + cached: --self-test never needs the real thing loaded for its fake
    paths, and a load failure must surface as a refusal, not an import crash."""
    if "cb" not in _ENGINE_MODS:
        _ENGINE_MODS["cb"] = _load("compile-backends.py", "compile_backends_drv")
    return _ENGINE_MODS["cb"]


def _core():
    if "core" not in _ENGINE_MODS:
        _ENGINE_MODS["core"] = _load("compile-core.py", "compile_core_drv")
    return _ENGINE_MODS["core"]


# --------------------------------------------------------------- errors
class UsageError(Exception):
    """argv did not parse (includes the --no-verify refusal)."""


class AuthorizationError(Exception):
    """--authorization missing, out of class, revoked, or non-covering."""


class StagingError(Exception):
    """--staging is not a well-formed file-handoff staging dir."""


class ReconciliationBlocked(Exception):
    """A prior run on this branch has no terminal verify disposition."""


# --------------------------------------------------------------- git helpers
def _git(repo, *args, **kw):
    """Returns (returncode, stdout, stderr). Never raises on a nonzero git."""
    p = subprocess.run(["git", "-C", repo] + list(args), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", **kw)
    return p.returncode, p.stdout or "", p.stderr or ""


def _is_git_repo(repo):
    rc, out, _ = _git(repo, "rev-parse", "--is-inside-work-tree")
    return rc == 0 and out.strip() == "true"


def _commit_files(repo, sha):
    rc, out, _ = _git(repo, "show", "--name-only", "--format=", sha)
    if rc != 0:
        return []
    return [ln.strip().replace("\\", "/") for ln in out.splitlines() if ln.strip()]


def _worktree_clean(repo):
    rc, out, _ = _git(repo, "status", "--porcelain")
    return rc == 0 and out.strip() == ""


# --------------------------------------------------------------- argv parsing
_VALUE_FLAGS = ("--root", "--staging", "--authorization", "--seq", "--reason")
_BOOL_FLAGS = ("--run", "--reconcile", "--reverify", "--revert", "--self-test",
               "--sections")


def parse_args(argv):
    """Strict parse. Unknown flags are refused (an unrecognized flag on a
    trust-boundary CLI must never be silently ignored), and `--no-verify` is
    refused BY NAME: live runs cannot skip verify by construction (build spec
    B-1, 'There is NO --no-verify flag on the public CLI')."""
    args = list(argv)
    if "--no-verify" in args:
        raise UsageError(
            "--no-verify is not a flag on this CLI and never will be: every "
            "live absorption rides a cross-vendor verify leg (runbook standing "
            "invariant 4). Nothing was run.")
    out = {"mode": None, "root": None, "staging": None, "authorization": None,
           "seq": None, "reason": None, "sections": False}
    i = 0
    modes = []
    while i < len(args):
        a = args[i]
        if a in _VALUE_FLAGS:
            if i + 1 >= len(args) or args[i + 1].startswith("--"):
                raise UsageError("%s requires a value" % a)
            out[a[2:]] = args[i + 1]
            i += 2
            continue
        if a in _BOOL_FLAGS:
            if a == "--sections":
                out["sections"] = True
            else:
                modes.append(a[2:])
            i += 1
            continue
        raise UsageError("unknown argument %r\n%s" % (a, USAGE))
    if len(modes) > 1:
        raise UsageError("pick exactly one mode: %s" % ", ".join(
            "--" + m for m in modes))
    out["mode"] = modes[0] if modes else None
    if out["mode"] == "run":
        missing = [f for f in ("root", "staging", "authorization")
                   if not out[f]]
        if missing:
            raise UsageError(
                "--run requires %s (authorization is REQUIRED and validated "
                "BEFORE anything is written -- there is no path to a live "
                "absorb without it). Nothing was run."
                % ", ".join("--" + m for m in missing))
    if out["mode"] == "reconcile" and not out["root"]:
        raise UsageError("--reconcile requires --root")
    if out["mode"] == "reverify":
        missing = [f for f in ("root", "seq", "staging", "authorization")
                   if not out[f]]
        if missing:
            raise UsageError(
                "--reverify requires %s (the authorization is validated before "
                "the verify dispatch, exactly as on --run; --staging is the "
                "original run's staging dir, whose stamped "
                "dispatch-manifest.json carries the F17 absorb identity the "
                "verify legs must cite)"
                % ", ".join("--" + m for m in missing))
        try:
            out["seq"] = int(out["seq"])
        except (TypeError, ValueError):
            raise UsageError("--seq must be an integer journal sequence number")
    if out["mode"] == "revert":
        missing = [f for f in ("root", "seq") if not out[f]]
        if missing:
            raise UsageError(
                "--revert requires %s (it reverts ONE named run commit; the "
                "journal record is restored and the revert is journaled, so "
                "the reverted run stays visible)"
                % ", ".join("--" + m for m in missing))
        try:
            out["seq"] = int(out["seq"])
        except (TypeError, ValueError):
            raise UsageError("--seq must be an integer journal sequence number")
    return out


# --------------------------------------------------------------- authorization
_QUOTE_RE = re.compile(u'["“]([^"”]+)["”]')


def _extract_verbatim_quote(text):
    """The first double-quoted span on or after a line naming 'Verbatim grant:'
    (see (f) in the module docstring). Returns None when the artifact declares
    no such grant line/quote."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if "verbatim grant:" in ln.lower():
            for cand in lines[i:i + 4]:
                m = _QUOTE_RE.search(cand)
                if m and m.group(1).strip():
                    return m.group(1).strip()
            return None
    return None


def _is_revoked(text):
    for ln in text.splitlines():
        stripped = ln.strip().lstrip("*_-# ").strip()
        if stripped.upper().startswith("REVOKED"):
            return True
    return False


def validate_authorization(repo, auth_path, class_check=None):
    """Checks (a)-(f) of the module docstring. Returns the dispatch_guard
    authorization dict {"path": <repo-relative posix>, "quote": <verbatim>};
    raises AuthorizationError otherwise. Writes NOTHING and reads only the
    named artifact."""
    if not auth_path:
        raise AuthorizationError("no --authorization supplied")
    # (a) containment
    cand = auth_path
    if os.path.isabs(cand):
        try:
            rel = os.path.relpath(cand, repo)
        except ValueError:
            raise AuthorizationError(
                "authorization path is not inside the repo: %s" % auth_path)
    else:
        rel = cand
    rel_posix = posixpath.normpath(rel.replace("\\", "/"))
    if rel_posix.startswith("..") or posixpath.isabs(rel_posix):
        raise AuthorizationError(
            "authorization path escapes the repo: %s" % auth_path)
    # (b) artifact class -- the SAME structural check dispatch_guard applies
    if class_check is None:
        class_check = _backends()._is_authorization_artifact_class
    if not class_check(rel_posix):
        raise AuthorizationError(
            "authorization file is not in the operator-artifact class "
            "deploy/evidence/operator-*.md (directory must be exactly "
            "deploy/evidence, basename operator-*.md): %s" % rel_posix)
    # (c) exists, non-empty
    abs_path = os.path.join(repo, rel_posix.replace("/", os.sep))
    if not os.path.isfile(abs_path):
        raise AuthorizationError("authorization file not found: %s" % rel_posix)
    try:
        text = open(abs_path, encoding="utf-8").read()
    except OSError as e:
        raise AuthorizationError("authorization file unreadable: %s (%s)"
                                 % (rel_posix, e))
    if not text.strip():
        raise AuthorizationError("authorization file is empty: %s" % rel_posix)
    # (d) non-revocation
    if _is_revoked(text):
        raise AuthorizationError(
            "authorization artifact carries a REVOKED line: %s" % rel_posix)
    # (e) coverage markers
    low = text.lower()
    missing = [m for m in AUTH_COVERAGE_MARKERS if m.lower() not in low]
    if missing:
        raise AuthorizationError(
            "authorization does not cover the pending verify dispatch for the "
            "wired compile path -- required marker(s) absent from %s: %s"
            % (rel_posix, ", ".join(repr(m) for m in missing)))
    # (f) verbatim quote
    quote = _extract_verbatim_quote(text)
    if not quote:
        raise AuthorizationError(
            "authorization artifact declares no 'Verbatim grant:' quote, so "
            "there is no verbatim string to satisfy dispatch_guard with: %s"
            % rel_posix)
    if quote not in text:      # belt-and-braces; the extractor took it from text
        raise AuthorizationError(
            "extracted grant quote is not verbatim in %s" % rel_posix)
    return {"path": rel_posix, "quote": quote}


# --------------------------------------------------------------- staging
def validate_staging(staging, stamp_check=None):
    """Validates the file-handoff staging dir and returns
    {"plan", "manifest", "manifest_path", "views", "events_views"}.
    Raises StagingError. Reads only; writes nothing."""
    if not staging or not os.path.isdir(staging):
        raise StagingError("staging dir does not exist: %s" % staging)
    plan_path = os.path.join(staging, "plan.json")
    if not os.path.isfile(plan_path):
        raise StagingError("no plan.json in staging dir %s" % staging)
    try:
        plan = json.load(open(plan_path, encoding="utf-8"))
    except (ValueError, TypeError, OSError) as e:
        raise StagingError("plan.json unparseable: %s" % e)
    items = plan.get("items") if isinstance(plan, dict) else None
    if not isinstance(items, list) or not items:
        raise StagingError(
            "plan.json must carry a non-empty 'items' list "
            '({"items": [{"view", "events", "event_class"}]})')
    for n, item in enumerate(items):
        if not isinstance(item, dict) or not item.get("view"):
            raise StagingError("plan.json items[%d] has no 'view'" % n)
        if not isinstance(item.get("events"), list) or not item["events"]:
            raise StagingError(
                "plan.json items[%d] (%s) has no non-empty 'events' list"
                % (n, item.get("view")))

    manifest_path = os.path.join(staging, "dispatch-manifest.json")
    if not os.path.isfile(manifest_path):
        raise StagingError(
            "no dispatch-manifest.json in staging dir %s -- produce the staging "
            "dir with compile-backends.emit_packets() then stamp_dispatch()"
            % staging)
    try:
        manifest = json.load(open(manifest_path, encoding="utf-8"))
    except (ValueError, TypeError, OSError) as e:
        raise StagingError("dispatch-manifest.json unparseable: %s" % e)

    # F17 attestation stamp -- the same check FileHandoffAbsorbBackend and
    # BridgeVerifyBackend both run; refused here so it fails pre-write.
    if stamp_check is None:
        stamp_check = _backends()._verify_dispatch_stamp
    ok, reason = stamp_check(manifest)
    if not ok:
        raise StagingError(
            "dispatch-manifest.json refused (F17 dispatch-record attestation): "
            "%s -- run compile-backends.stamp_dispatch(manifest_path, model, "
            "vendor, identity_source=...) after authoring the answers; "
            "identity_source records how the identity was obtained "
            "(attestation:<record> | operator-attested:<date> | "
            "scheduled-invocation:<task>), never typed from memory" % reason)

    packets = manifest.get("packets") or []
    if not packets:
        raise StagingError("dispatch-manifest.json carries no packet entries")
    man_views = []
    for entry in packets:
        view = entry.get("view")
        answer_rel = entry.get("answer")
        if not view or not answer_rel:
            raise StagingError(
                "dispatch-manifest packet entry missing view/answer: %r" % entry)
        man_views.append(view)
        answer_path = os.path.join(staging, answer_rel.replace("/", os.sep))
        if not os.path.isfile(answer_path):
            raise StagingError("answer file missing for view %s: %s"
                               % (view, answer_rel))
        try:
            answer = json.load(open(answer_path, encoding="utf-8"))
        except (ValueError, TypeError, OSError) as e:
            raise StagingError("answer file %s unparseable: %s"
                               % (answer_rel, e))
        missing = [k for k in ("new_text", "manifest", "corpus_support", "noops")
                   if k not in answer]
        if missing:
            raise StagingError("answer file %s missing required key(s): %s"
                               % (answer_rel, ", ".join(missing)))

    plan_views = [i["view"] for i in items]
    if sorted(set(plan_views)) != sorted(set(man_views)):
        raise StagingError(
            "plan.json views and dispatch-manifest views disagree "
            "(plan: %s; manifest: %s) -- the manifest must be emit_packets() "
            "output over THIS plan"
            % (sorted(set(plan_views)), sorted(set(man_views))))

    events_views = []
    for item in items:
        for erel in item["events"]:
            events_views.append((erel, item["view"]))
    return {"plan": plan, "manifest": manifest, "manifest_path": manifest_path,
            "views": sorted(set(plan_views)), "events_views": events_views}


# --------------------------------------------------------------- journal / reconciliation
def load_journal(repo):
    """{seq: record} for every receipts/journal/<seq>.json in the working tree
    (which IS this branch's view of the append-only journal). Unreadable or
    non-numeric files are skipped honestly rather than crashing the gate --
    compile-core.check_chain is the integrity authority, not this reader."""
    jd = os.path.join(repo, "receipts", "journal")
    recs = {}
    if not os.path.isdir(jd):
        return recs
    for name in os.listdir(jd):
        if not name.endswith(".json"):
            continue
        stem = name[:-5]
        if not stem.isdigit():
            continue
        try:
            recs[int(stem)] = json.load(
                open(os.path.join(jd, name), encoding="utf-8"))
        except (ValueError, TypeError, OSError):
            continue
    return recs


def _run_seqs(recs):
    return sorted(s for s, r in recs.items()
                  if str(r.get("run_type", "")).lower() == "compile")


def classify_verdict(verdict):
    """Did this verify leg COMPLETE (a real verdict from the verifier), or did
    it fail in transport (no verdict at all)? Returns (completed, label).

    ALLOWLIST, not denylist -- fail-closed by construction. Only these count as
    completed:
      * confirmed / revised / rejected -- the ONLY values verify-cli.js will
        ever put on stdout (its VALID_VERDICTS allowlist,
        .claude/skills/bridge/verify-cli.js:170); anything else makes it die
        nonzero, which the backend turns into a transport class below;
      * substrate-gated WITH a usable nested bridge_verdict in that same set --
        the F17/substrate gates run AFTER a real verdict came back, so the leg
        completed even though the gate withheld the stamp (OPERATIONS.md: read
        the FULL verdict, a substrate-gated outer can carry a real inner
        rejected -- v3.0-23).

    Everything else is INCOMPLETE -- transport failure recorded as data, not a
    verdict. The transport classes actually produced today, enumerated from
    compile-backends.py rather than guessed:
      * "bridge-error" -- BridgeVerifyBackend._fail_closed's only verdict value,
        emitted at four call sites (compile-backends.py:814, 838, 845, 854):
        packet with no CLAIM line, bridge exited nonzero (which is how
        verify-cli reports isError/timeout/unparseable/usage -- its exit codes
        2/3/4/64), empty stdout, and unparseable stdout;
      * "substrate-gated" with no usable inner verdict;
      * a missing/empty/None verdict field, or any value outside the allowlist
        (e.g. "timeout"/"unparseable"/"error" -- the strings verify-cli.js and
        the verify servers use internally and that a schema drift or a
        different backend could surface).

    THIS IS THE 2026-07-28 FIX (found by the first supervised live run, journal
    seq 103): both legs returned "bridge-error" from a transient upstream 400,
    and the driver counted them as completed non-confirm verdicts -- so it did
    NOT revert, and reconciliation called the run terminal. A transport failure
    leaves the absorption UNVERIFIED; it must take the auto-revert path."""
    if not isinstance(verdict, dict):
        return False, "verdict artifact is not an object"
    raw = verdict.get("verdict")
    v = str(raw or "").strip().lower()
    if v.startswith("confirm") or v in ("revised", "rejected"):
        return True, v
    if v == "substrate-gated":
        inner = verdict.get("bridge_verdict")
        iv = ""
        if isinstance(inner, dict):
            iv = str(inner.get("verdict") or "").strip().lower()
        if not iv:
            iv = str(verdict.get("gated_inner_verdict") or "").strip().lower()
        if iv.startswith("confirm") or iv in ("revised", "rejected"):
            return True, "substrate-gated(%s)" % iv
        return False, "substrate-gated with no usable inner verdict"
    return False, (v or "no verdict field")


def _load_verdict_artifact(repo, rel):
    p = os.path.join(repo, str(rel).replace("/", os.sep))
    if not os.path.isfile(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except (ValueError, TypeError, OSError):
        return None


def verify_record_legs(repo, vrec):
    """Classify every leg a verify record actually fired. Returns
    {"legs": [{"artifact", "label", "completed"}], "incomplete": [str, ...]}.

    Which entries were fired THIS record:
      * absorption_verified[]        -- confirmed by construction (the engine
                                        only stamps on a confirm);
      * absorption_verify_attempts[] -- every non-confirmed absorption leg,
                                        carrying its verdict artifact;
      * noop_candidates[] that are still unverified but carry an artifact --
                                        the no-op union legs that did not flip.
    A referenced artifact that is missing or unparseable is INCOMPLETE
    (fail-closed: absence is not proof of a verdict)."""
    legs = []
    incomplete = []

    def add(artifact, verdict, source):
        completed, label = classify_verdict(verdict)
        legs.append({"artifact": artifact, "label": label,
                     "completed": completed})
        if not completed:
            incomplete.append("%s %s -> %s" % (source, artifact or "(no "
                                               "artifact)", label))

    for av in vrec.get("absorption_verified") or []:
        legs.append({"artifact": av.get("artifact"), "label": "confirmed",
                     "completed": True})
    for at in vrec.get("absorption_verify_attempts") or []:
        art = at.get("artifact")
        add(art, _load_verdict_artifact(repo, art) if art else None,
            "absorption leg")
    for nc in vrec.get("noop_candidates") or []:
        if nc.get("verified") or not nc.get("artifact"):
            continue
        art = nc["artifact"]
        add(art, _load_verdict_artifact(repo, art), "no-op leg")
    return {"legs": legs, "incomplete": incomplete}


def _terminal_seqs(recs, repo=None):
    """Seqs with a terminal verify disposition: a verify record covers them AND
    every leg that record fired actually COMPLETED, or a driver revert record
    names them as reverted.

    The leg check is the 2026-07-28 fix: verify_run() commits its record once
    the loop finishes, whether or not the legs got verdicts, so the record's
    mere existence is NOT proof of verification (journal seq 103 -- both legs
    bridge-error, record committed, reconciliation waved the run through)."""
    terminal = set()
    for rec in recs.values():
        vs = rec.get("verifies_seq")
        if isinstance(vs, int):
            if repo is None or not verify_record_legs(repo, rec)["incomplete"]:
                terminal.add(vs)
        dr = rec.get("driver_revert")
        if isinstance(dr, dict) and dr.get("status") == "reverted":
            rs = dr.get("reverts_seq")
            if isinstance(rs, int):
                terminal.add(rs)
    return terminal


def _incomplete_verify_note(repo, recs, seq):
    """Human-readable 'why is this run still not terminal' detail."""
    for rec in recs.values():
        if rec.get("verifies_seq") == seq:
            bad = verify_record_legs(repo, rec)["incomplete"]
            if bad:
                return (" -- a verify record exists but %d of its legs never "
                        "completed: %s" % (len(bad), "; ".join(bad[:3])))
    return ""


def reconcile_state(repo, recs=None):
    """The startup-reconciliation verdict. Returns
    {"blocked", "seq", "reason", "stale_older": [seq...]}.
    Gates on the NEWEST run record only (see the module docstring's documented
    scope decision); older non-terminal runs are reported, not blocking."""
    leg_repo = repo
    if recs is None:
        recs = load_journal(repo)
    else:
        leg_repo = None     # caller-supplied records: no artifacts to resolve
    runs = _run_seqs(recs)
    terminal = _terminal_seqs(recs, repo=leg_repo)
    if not runs:
        return {"blocked": False, "seq": None, "reason": "no run records",
                "stale_older": []}
    newest = runs[-1]
    stale_older = [s for s in runs[:-1] if s not in terminal]
    if newest in terminal:
        return {"blocked": False, "seq": newest,
                "reason": "newest run seq %d has a terminal verify disposition"
                          % newest,
                "stale_older": stale_older}
    revert_note = ""
    for rec in recs.values():
        dr = rec.get("driver_revert")
        if isinstance(dr, dict) and dr.get("reverts_seq") == newest:
            revert_note = " (a driver revert for it is journaled with status "\
                          "%r -- non-terminal)" % dr.get("status")
    if not revert_note and leg_repo is not None:
        revert_note = _incomplete_verify_note(leg_repo, recs, newest)
    return {"blocked": True, "seq": newest,
            "reason": "run seq %d has no terminal verify disposition%s"
                      % (newest, revert_note),
            "stale_older": stale_older}


def _reconcile_advice(seq):
    return (
        "Reconcile it before running again -- either (a) re-fire its verify\n"
        "  legs over that run:  py deploy/compile-driver.py --reverify --root .\n"
        "  --seq %d --staging <the run's staging dir> --authorization <path>\n"
        "  (use this when the absorption itself is sound and only the transport\n"
        "  failed), or (b) revert its run commit and journal the revert:\n"
        "  py deploy/compile-driver.py --revert --root . --seq %d\n"
        "  Nothing was written by this invocation." % (seq, seq))


# --------------------------------------------------------------- revert
def _stage_only_commit_allowing_deletes(repo, paths, message):
    """Stage-only commit that tolerates DELETED paths. compile-core's
    stage_only_commit refuses a manifest path that is missing on disk, which is
    exactly what a revert of a view-creating run produces -- so the revert path
    uses this sibling: per-path `git add -A -- <path>` (never a bare directory
    add) then one `git commit -- <explicit pathspecs>`, which commits those
    paths and nothing else. Same stage-only discipline, deletion-aware."""
    for p in paths:
        if os.path.isdir(os.path.join(repo, p)):
            raise RuntimeError("manifest path is a directory: %s" % p)
    for p in paths:
        rc, _o, err = _git(repo, "add", "-A", "--", p)
        if rc != 0:
            raise RuntimeError("git add failed for %s: %s" % (p, err[-200:]))
    rc, out, err = _git(repo, "commit", "-m", message, "--only", "--", *paths)
    if rc != 0:
        raise RuntimeError("git commit failed: %s" % (err or out)[-300:])
    rc, out, _e = _git(repo, "rev-parse", "HEAD")
    return out.strip()


def _restore_path_from_commit(repo, sha, rel_path):
    """Restore ONE path to its exact bytes at `sha` and stage it. Deliberately
    `git show <sha>:<path>` + a byte-for-byte write, NOT `git checkout <sha> --
    <path>`: checkout applies the repo's eol/filter settings, and on a
    core.autocrlf host that rewrites a journal record's newlines -- which
    changes the record's bytes, which breaks compile-core's prev_record_hash
    chain at the NEXT record. The journal is hash-chained bytes, so it must be
    restored as bytes. Returns (ok, error_detail)."""
    p = subprocess.run(["git", "-C", repo, "show", "%s:%s" % (sha, rel_path)],
                       capture_output=True)
    if p.returncode != 0:
        return False, (p.stderr or b"").decode("utf-8", "replace")[-200:]
    abs_path = os.path.join(repo, rel_path.replace("/", os.sep))
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as fh:
        fh.write(p.stdout)
    rc, _o, err = _git(repo, "add", "--", rel_path)
    if rc != 0:
        return False, err[-200:]
    return True, ""


def revert_run_commit(repo, run_sha, seq, reason, out=print):
    """Auto-revert of an unverified run commit (atomicity rule, incomplete-leg
    branch). Returns (ok, detail).

    The run commit carries BOTH the view writes and the run's journal record.
    A plain `git revert` would delete the journal record too -- but the journal
    is append-only ("the reverted run stays visible") and deleting record N
    would break compile-core's chain (a gap below N+1). So: `git revert -n`,
    then restore the run's journal file(s) from the run commit, then append a
    revert record, then ONE stage-only commit carrying the reverted views + the
    restored record + the new revert record.

    A conflicted revert is aborted and journaled with status "revert-failed":
    non-terminal, so startup reconciliation blocks every later run until a human
    resolves it."""
    journal_files = [f for f in _commit_files(repo, run_sha)
                     if re.match(r"receipts/journal/\d+\.json$", f)]
    other_files = [f for f in _commit_files(repo, run_sha)
                   if f not in journal_files]

    rc, _o, err = _git(repo, "revert", "-n", run_sha)
    if rc != 0:
        _git(repo, "revert", "--abort")
        _git(repo, "revert", "--quit")
        detail = "git revert refused/conflicted: %s" % err.strip()[-300:]
        try:
            _journal_revert(repo, seq, run_sha, "revert-failed",
                            "%s | original reason: %s" % (detail, reason))
        except Exception as e:                              # noqa: BLE001
            detail += " | AND the revert-failure record could not be journaled: %s" % e
        out("  revert FAILED: %s" % detail)
        return False, detail

    for jf in journal_files:
        ok, err = _restore_path_from_commit(repo, run_sha, jf)
        if not ok:
            detail = "could not restore journal record %s: %s" % (jf, err)
            out("  revert FAILED: %s" % detail)
            return False, detail

    try:
        rseq, rrel = _journal_revert(repo, seq, run_sha, "reverted", reason,
                                     commit_paths=other_files + journal_files)
    except Exception as e:                                  # noqa: BLE001
        detail = "revert applied but could not be journaled/committed: %s" % e
        out("  revert FAILED: %s" % detail)
        return False, detail
    _git(repo, "revert", "--quit")
    out("  reverted run commit %s; revert journaled at seq %d (%s)"
        % (run_sha[:12], rseq, rrel))
    return True, "reverted at journal seq %d" % rseq


def _journal_revert(repo, seq, run_sha, status, reason, commit_paths=None):
    """Append a driver-revert record and commit it (stage-only). Returns
    (revert_seq, journal_rel)."""
    core = _core()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    rc, head, _e = _git(repo, "rev-parse", "HEAD")
    rec = core.minimal_record("driver-revert", head.strip())
    rec["run_window"] = {"start": now, "end": now}
    rec["driver_revert"] = {
        "reverts_seq": seq, "reverts_commit": run_sha, "status": status,
        "reason": reason, "at": now,
        "driver": "deploy/compile-driver.py",
    }
    rseq, jpath = core.append_record(repo, rec)
    jrel = os.path.relpath(jpath, repo).replace(os.sep, "/")
    paths = list(commit_paths or []) + [jrel]
    seen, ordered = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    _stage_only_commit_allowing_deletes(
        repo, ordered,
        "compile-driver: %s of run seq %d (%s) -- %s"
        % ("revert" if status == "reverted" else status, seq, run_sha[:12],
           reason[:120]))
    return rseq, jrel


# --------------------------------------------------------------- engine seam
class RealEngine:
    """The shipped engine seam. --self-test injects a fake with the same four
    methods, so the self-test NEVER constructs a bridge backend or transmits
    anything."""

    def dispatch_guard(self, repo, kind, events_views, authorization):
        return _backends().dispatch_guard(kind, repo,
                                          events_views=events_views,
                                          authorization=authorization)

    def absorb_backend(self, staging):
        return _backends().FileHandoffAbsorbBackend(staging)

    def verify_backend(self, repo, manifest_path, timeout_ms):
        return _backends().BridgeVerifyBackend(
            repo, dispatch_manifest_path=manifest_path, gate_kind="routine",
            tier="T2", timeout_ms=timeout_ms)

    def run_guarded(self, repo, plan, backend, authorization):
        return _backends().run_guarded(repo, plan, backend,
                                       authorization=authorization)

    def verify_run_guarded(self, repo, seq, backend, authorization):
        return _backends().verify_run_guarded(repo, seq, backend,
                                              authorization=authorization)


# --------------------------------------------------------------- bridge probe
# Mirror of resolveCodexBin() in .claude/skills/bridge/codex-verify-server.js
# (which carries the same not-drift note and the same CODEX_MIN_VERSION floor).
# Kept as a DUPLICATE rather than a shell-out to the server, on purpose: the
# probe must answer "what will the child leg resolve?" before any child exists.
# THE TWO MUST NOT DRIFT -- change both or neither. One deliberate asymmetry,
# documented on both sides: this side has a known-folder syscall candidate that
# the JS side does not need (Node's os.homedir() already syscall-falls-back;
# Python's os.path.expanduser does not), and the JS side keeps a bare-name last
# resort that this side deliberately refuses (a probe that "succeeds" on an
# unresolvable name is worse than a refusal).
_NPM_VENDOR_TAIL = ("npm", "node_modules", "@openai", "codex", "node_modules",
                    "@openai", "codex-win32-x64", "vendor",
                    "x86_64-pc-windows-msvc", "bin", "codex.exe")

PROBE_TIMEOUT_S = 10

# VERSION FLOOR (backlog v3.0-68, second live finding 2026-07-28). The native
# Codex install on this instance is 0.142.3, which predates the GPT-5.6 family:
# the API rejects it outright with "requires a newer version of Codex" (an
# instant 400 that reads as a transport blip). The known-good binary is the
# npm-installed 0.144.1 the bridge server's own note names. A candidate is
# therefore accepted ONLY if it exists AND reports codex-cli >= this floor --
# existence alone silently steered a whole live run onto the gated binary, and
# the probe then reported that as SUCCESS. Unparseable --version output is a
# rejection, not a pass (fail-closed: we cannot show the floor is met).
CODEX_MIN_VERSION = (0, 144)

_CODEX_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def parse_codex_version(text):
    """First dotted version in `codex --version` output (e.g. 'codex-cli
    0.144.1' -> (0, 144, 1)). None when nothing parses."""
    m = _CODEX_VERSION_RE.search(text or "")
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def _npm_vendor_exe_under(roaming_root):
    if not roaming_root:
        return None
    return os.path.join(roaming_root, *_NPM_VENDOR_TAIL)


def _known_folder_roaming_appdata():
    """%APPDATA% from the OS, not from the environment: SHGetKnownFolderPath
    (FOLDERID_RoamingAppData) via stdlib ctypes.

    This exists because the environment lies under scrubbing. Node's
    os.homedir() syscall-falls-back when USERPROFILE is absent; Python's
    os.path.expanduser does NOT -- under the scrubbed `py deploy/...` profile it
    returns a garbage root, so BOTH npm candidates missed and resolution fell
    through to the version-gated native install. The known-folder syscall is
    immune to a scrubbed APPDATA/USERPROFILE. Windows-only; every other platform
    (and any failure) returns None and the walk simply continues."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        class _GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong),
                        ("Data2", ctypes.c_ushort),
                        ("Data3", ctypes.c_ushort),
                        ("Data4", ctypes.c_ubyte * 8)]

        # FOLDERID_RoamingAppData {3EB685DB-65F9-4CF6-A03A-E3EF65729F3D}
        fid = _GUID(0x3EB685DB, 0x65F9, 0x4CF6,
                    (ctypes.c_ubyte * 8)(0xA0, 0x3A, 0xE3, 0xEF,
                                         0x65, 0x72, 0x9F, 0x3D))
        ptr = ctypes.c_wchar_p()
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(fid), 0, None, ctypes.byref(ptr))
        if hr != 0:
            return None
        value = ptr.value
        try:
            ctypes.windll.ole32.CoTaskMemFree(ptr)
        except Exception:                                   # noqa: BLE001
            pass
        return value or None
    except Exception:                                       # noqa: BLE001
        return None


def _default_version_runner(args, timeout):
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    return p.returncode, p.stdout or "", p.stderr or ""


def resolve_codex_bin(env=None, homedir=None, isfile=None, which=None,
                      runner=None, known_folder=None):
    """Resolve the codex binary the verify legs will actually run. Walk order:
    CODEX_BIN env -> APPDATA-derived npm exe -> known-folder-syscall-derived npm
    exe -> expanduser-derived npm exe -> where/which.

    EVERY candidate is version-checked IN THE WALK (`<bin> --version` against
    CODEX_MIN_VERSION), not once at the end: a candidate that exists but is
    below the floor is REJECTED and the walk CONTINUES to the next one. That
    ordering is the whole point -- the 2026-07-28 crash-window demo had both npm
    candidates miss under scrubbing, `which` find the gated native 0.142.3, and
    the probe report it as success while exporting it as CODEX_BIN, actively
    steering the legs onto the binary the API refuses.

    Returns (resolved_path_or_None, chain) where chain is a list of
    (label, candidate, accepted, detail) rows -- detail is the reported version
    on acceptance, or why the candidate was rejected. The refusal prints the
    chain verbatim, so an operator sees "native 0.142.3 found but below the
    0.144 floor; npm candidates missed" rather than a bare failure."""
    env = os.environ if env is None else env
    isfile = os.path.isfile if isfile is None else isfile
    runner = runner or _default_version_runner
    known_folder = (_known_folder_roaming_appdata if known_folder is None
                    else known_folder)
    if which is None:
        import shutil
        which = shutil.which

    def check(path):
        """(accepted, detail) for one existing candidate path."""
        try:
            rc, stdout, stderr = runner([path, "--version"], PROBE_TIMEOUT_S)
        except Exception as e:                              # noqa: BLE001
            return False, "could not run --version (%s: %s)" % (
                type(e).__name__, e)
        text = (stdout or "") + (" " + stderr if stderr else "")
        if rc != 0:
            return False, "--version exited %s: %s" % (
                rc, text.strip()[-120:] or "(no output)")
        ver = parse_codex_version(text)
        if ver is None:
            return False, "unparseable --version output: %r" % (
                text.strip()[:80],)
        shown = ".".join(str(n) for n in ver)
        if ver[:2] < CODEX_MIN_VERSION:
            return False, ("codex-cli %s is BELOW the %s floor -- "
                           "version-gated by the API"
                           % (shown, ".".join(str(n)
                                              for n in CODEX_MIN_VERSION)))
        return True, "codex-cli " + shown

    candidates = []
    pinned = env.get("CODEX_BIN")
    candidates.append(("CODEX_BIN env", pinned, "(CODEX_BIN unset)"))
    candidates.append(("APPDATA-derived npm exe",
                       _npm_vendor_exe_under(env.get("APPDATA")),
                       "(APPDATA unset -- candidate skipped)"))
    kf = None
    try:
        kf = known_folder()
    except Exception:                                       # noqa: BLE001
        kf = None
    candidates.append(("known-folder npm exe (SHGetKnownFolderPath)",
                       _npm_vendor_exe_under(kf),
                       "(known-folder syscall unavailable)"))
    home = homedir if homedir is not None else os.path.expanduser("~")
    candidates.append(("expanduser npm exe",
                       _npm_vendor_exe_under(
                           os.path.join(home, "AppData", "Roaming")
                           if home else None),
                       "(no home directory)"))
    found_path = None
    try:
        found_path = which("codex")
    except Exception:                                       # noqa: BLE001
        found_path = None
    candidates.append(("where/which codex", found_path, "(not on PATH)"))

    chain = []
    for label, path, absent_note in candidates:
        if not path:
            chain.append((label, absent_note, False, "skipped"))
            continue
        if not isfile(path):
            chain.append((label, path, False, "not on disk"))
            continue
        accepted, detail = check(path)
        chain.append((label, path, accepted, detail))
        if accepted:
            return path, chain
    return None, chain


def _render_chain(chain):
    """One line per candidate: label, what was tried, and either the accepted
    version or why it was rejected. Rows are 4-tuples since the version floor
    landed; 3-tuples are still rendered for any older caller."""
    lines = []
    for row in chain:
        label, cand, accepted = row[0], row[1], row[2]
        detail = row[3] if len(row) > 3 else ""
        lines.append("    %-46s %-4s %s%s"
                     % (label, "USE" if accepted else "--", cand,
                        ("  [%s]" % detail) if detail else ""))
    return "\n".join(lines)


def probe_bridge(repo, out=print, resolver=None, runner=None, env=None):
    """PRE-WRITE bridge probe (backlog v3.0-68). Resolves the bridge dir the way
    compile-backends.BridgeVerifyBackend._bridge_dir() does, then resolves the
    codex binary the way codex-verify-server.js does -- version-checking EVERY
    candidate against CODEX_MIN_VERSION as it walks (see resolve_codex_bin). On
    failure it REFUSES before anything is written and prints the whole chain
    WITH the versions it saw, so "the native install was found but is
    version-gated, and the npm candidates missed" is visible at a glance rather
    than inferred. On success it EXPORTS the resolved path into
    os.environ["CODEX_BIN"], so the driver's probe result and the bridge's own
    runtime resolution can never disagree -- the child leg is pinned to exactly
    the binary that was probed.

    HONEST LIMIT: this is a no-token, no-network probe. It proves a binary of a
    sufficient version exists and is runnable; it CANNOT catch server-side
    gating (an API that rejects an otherwise-current binary), a revoked
    credential, or an upstream outage. Those still surface as transport-class
    verdicts at verify time, and the atomicity rule's auto-revert remains their
    backstop. The probe's job is narrower: turn the most common, most silent
    failure -- a scrubbed environment resolving the wrong binary or none at all
    -- from a wasted absorb-then-revert cycle into a refusal that costs nothing.

    Returns (ok, detail)."""
    env = os.environ if env is None else env
    bridge_dir = env.get("CROSS_VENDOR_BRIDGE_DIR") or os.path.join(
        repo, ".claude", "skills", "bridge")
    cli = os.path.join(bridge_dir, "verify-cli.js")
    if not os.path.isfile(cli):
        out("REFUSED (bridge probe): no verify-cli.js at %s" % bridge_dir)
        out("  The verify legs have nothing to call. Nothing was written.")
        return False, "no verify-cli.js at %s" % bridge_dir

    resolver = resolver or resolve_codex_bin
    binpath, chain = resolver(env=env, runner=runner)
    if not binpath:
        out("REFUSED (bridge probe): no codex binary met the >= %s floor. "
            "Tried:" % ".".join(str(n) for n in CODEX_MIN_VERSION))
        out(_render_chain(chain))
        out("  Set CODEX_BIN to a codex >= %s executable, or install the npm "
            "@openai/codex package. Nothing was written."
            % ".".join(str(n) for n in CODEX_MIN_VERSION))
        return False, "no codex binary met the version floor"

    version = "(version not recorded)"
    for row in chain:
        if row[2]:
            version = row[3] if len(row) > 3 else version
            break
    env["CODEX_BIN"] = binpath      # pin the child legs to the probed binary
    out("bridge probe: %s at %s" % (version, binpath))
    return True, version


def verify_timeout_ms():
    """Explicit pin resolution (see the module docstring's VERIFY TIMEOUT note)."""
    raw = os.environ.get("VERIFY_TIMEOUT_MS")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return DEFAULT_VERIFY_TIMEOUT_MS


# --------------------------------------------------------------- post-run sensors
def run_sensors(repo, run_sha, sections):
    """Census (staleness.py) + check-run-diff.py over the run commit, as
    subprocesses. Returns {"census": (rc, tail), "diff": (rc, tail)}."""
    def _run(args):
        p = subprocess.run(args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=repo)
        text = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
        return p.returncode, "\n".join(text[-12:])

    census = _run([sys.executable, os.path.join(_HERE, "staleness.py")])
    diff_args = [sys.executable, os.path.join(_HERE, "check-run-diff.py"),
                 "--commit", run_sha, "--repo", repo]
    if sections:
        diff_args.append("--sections")
    diff = _run(diff_args)
    return {"census": census, "diff": diff}


# --------------------------------------------------------------- the run
def execute_run(root, staging, auth_path, sections=False, engine=None,
                sensors=None, probe=None, out=print):
    """The whole --run flow. Returns an exit code. `engine`/`sensors`/`probe`
    are the self-test injection seams (defaults are the real engine, the real
    sensor subprocesses and the real bridge probe)."""
    engine = engine or RealEngine()
    sensors = sensors or run_sensors
    probe = probe or probe_bridge
    repo = os.path.abspath(root)

    if not os.path.isdir(repo) or not _is_git_repo(repo):
        out("REFUSED: --root %s is not a git repository. Nothing was run." % root)
        return EXIT_FAIL

    # ---- 2. authorization (PRE-WRITE, before any backend exists)
    try:
        authorization = validate_authorization(repo, auth_path)
    except AuthorizationError as e:
        out("REFUSED (authorization): %s" % e)
        out("Nothing was written and nothing was committed.")
        return EXIT_FAIL

    # ---- 2b. bridge probe (v3.0-68): can the verify legs reach a runnable
    # codex at all? A no-token, no-network check -- see probe_bridge().
    ok, _detail = probe(repo, out=out)
    if not ok:
        return EXIT_FAIL

    # ---- 3. staging
    try:
        staged = validate_staging(staging)
    except StagingError as e:
        out("REFUSED (staging): %s" % e)
        out("Nothing was written and nothing was committed.")
        return EXIT_FAIL

    # ---- 4. side-effect-free dispatch_guard dry run over the pending verify
    try:
        guard = engine.dispatch_guard(repo, "verify", staged["events_views"],
                                      authorization)
    except Exception as e:                                  # noqa: BLE001
        out("REFUSED (dispatch guard could not be evaluated): %s" % e)
        return EXIT_FAIL
    if not guard.get("permit"):
        out("REFUSED (authorization does not cover the pending verify "
            "dispatch): %s" % guard.get("reason"))
        out("Nothing was written and nothing was committed.")
        return EXIT_FAIL

    # ---- 5. startup reconciliation
    state = reconcile_state(repo)
    if state["blocked"]:
        out("REFUSED (startup reconciliation): %s" % state["reason"])
        out(_reconcile_advice(state["seq"]))
        return EXIT_FAIL
    if state["stale_older"]:
        out("NOTE: older run record(s) without a terminal verify disposition: "
            "%s (advisory -- the merge bars are the backstop; not blocking "
            "this run)" % ", ".join(str(s) for s in state["stale_older"]))

    # ---- ABSORB (the first thing that writes)
    timeout_ms = verify_timeout_ms()
    try:
        backend = engine.absorb_backend(staging)
        result = engine.run_guarded(repo, staged["plan"], backend,
                                    authorization)
    except Exception as e:                                  # noqa: BLE001
        name = type(e).__name__
        if name == "LockHeld":
            out("LOCK HELD: %s -- another run holds the compile lock. Nothing "
                "was written." % e)
            return EXIT_LOCK_HELD
        out("ABSORB REFUSED (%s): %s" % (name, e))
        out("The engine refuses pre-journal on a validation failure -- nothing "
            "journaled, nothing committed.")
        return EXIT_FAIL

    run_sha = result.get("sha")
    run_seq = result.get("seq")
    absorbed_views = sorted({a.get("view") for a in _absorbed_of(repo, run_seq)
                             if a.get("view")})
    out("ABSORB: journal seq %s, run commit %s, %d view(s) absorbed."
        % (run_seq, (run_sha or "?")[:12], len(absorbed_views)))

    # ---- VERIFY (every absorbed view; the same pre-validated authorization)
    verify_result = None
    incomplete_reason = None
    try:
        vbackend = engine.verify_backend(repo, staged["manifest_path"],
                                         timeout_ms)
        verify_result = engine.verify_run_guarded(repo, run_seq, vbackend,
                                                  authorization)
    except Exception as e:                                  # noqa: BLE001
        incomplete_reason = "verify leg did not complete (%s): %s" % (
            type(e).__name__, e)

    if verify_result is not None:
        checked = verify_result.get("absorption_checked", 0)
        if checked < len(absorbed_views):
            incomplete_reason = (
                "verify covered %d of %d absorbed view(s) -- runbook standing "
                "invariant 4 requires a leg on EVERY absorption"
                % (checked, len(absorbed_views)))
        else:
            # 2026-07-28 fix (live seq 103): verify_run() commits its record
            # whether or not the legs got verdicts, so returning normally is
            # NOT proof of verification. Classify each leg by its verdict
            # value; a transport class (bridge-error &c) means the leg did not
            # complete, which is the auto-revert branch. Mixed run: incomplete
            # wins -- the run as a whole is unverified.
            bad = verify_record_legs(
                repo, load_journal(repo).get(verify_result.get("seq"), {})
            )["incomplete"]
            if bad:
                incomplete_reason = (
                    "%d verify leg(s) never completed (transport failure "
                    "recorded as a verdict, not a verdict): %s"
                    % (len(bad), "; ".join(bad)))

    if incomplete_reason is not None:
        # ---- atomicity rule, incomplete branch: AUTO-REVERT
        out("VERIFY INCOMPLETE: %s" % incomplete_reason)
        out("Auto-reverting the run commit so the branch holds no unverified "
            "absorption (staging dir left untouched for a clean re-run).")
        ok, detail = revert_run_commit(repo, run_sha, run_seq,
                                       incomplete_reason, out=out)
        if not ok:
            out("The unreverted run is now a BLOCKING state: startup "
                "reconciliation will refuse further runs until it is resolved "
                "by hand.")
        out(_summary_block(run_seq, run_sha, absorbed_views, None,
                           {"census": (None, "not run -- verify incomplete"),
                            "diff": (None, "not run -- verify incomplete")},
                           reverted=ok))
        return EXIT_FAIL

    # ---- verdict grading (the leg COMPLETED)
    confirmed = verify_result.get("absorption_confirmed", 0)
    checked = verify_result.get("absorption_checked", 0)
    noop_confirmed = verify_result.get("confirmed", 0)
    noop_checked = verify_result.get("checked", 0)
    non_confirm = (confirmed != checked) or (noop_confirmed != noop_checked)

    if non_confirm:
        # verified content with a non-confirm verdict: journaled data. NO
        # revert -- invariant 4 wanted a verify on every absorption and one
        # happened. The branch stays unmergeable until adjudicated.
        out("VERIFY NON-CONFIRM: %d of %d absorption leg(s) confirmed, %d of "
            "%d no-op leg(s) confirmed. The verdicts are journaled data -- "
            "read them in receipts/verify/, then adjudicate: `--revert --seq "
            "N` this run, correct the answers in the (untouched) staging dir, "
            "and re-run `--run` so the correction lands validated and "
            "re-verified. Never hand-edit the written views. NOT reverted "
            "here: the absorption IS verified, it just did not pass."
            % (confirmed, checked, noop_confirmed, noop_checked))
        # v3.0-84: name the failure CLASS per leg. "confirmed but stamp
        # refused" (e.g. no derivation region on a legacy view) is a different
        # repair than a verifier rejection, and the two used to print
        # identically.
        for att in verify_result.get("absorption_attempts", []) or []:
            if att.get("view") or att.get("reason"):
                out("  non-confirm leg: %s -- %s"
                    % (att.get("view", "?"),
                       att.get("reason", "(no reason recorded)")))
        results = sensors(repo, run_sha, sections)
        out(_summary_block(run_seq, run_sha, absorbed_views, verify_result,
                           results, reverted=False))
        return EXIT_FAIL

    results = sensors(repo, run_sha, sections)
    out(_summary_block(run_seq, run_sha, absorbed_views, verify_result,
                       results, reverted=False))
    if results["diff"][0] not in (0, None):
        out("check-run-diff FAILED on the run commit -- merge bar 3 is red.")
        return EXIT_FAIL
    return EXIT_OK


def _absorbed_of(repo, seq):
    """The absorbed[] entries of run record `seq` (read back from the journal so
    the count reflects what was actually journaled, not what was planned)."""
    recs = load_journal(repo)
    rec = recs.get(seq) or {}
    return rec.get("absorbed") or []


def _summary_block(seq, sha, absorbed_views, verify_result, results,
                   reverted=False):
    """ONE plain-English block: absorbed / no-ops / verify verdicts / census /
    diff-check, so the calling skill re-states results without re-deriving
    them."""
    lines = ["", "===== COMPILE RUN SUMMARY =====",
             "Journal seq:   %s" % seq,
             "Run commit:    %s%s" % ((sha or "?")[:12],
                                      "  (REVERTED)" if reverted else "")]
    if absorbed_views:
        lines.append("Absorbed:      %d view(s):" % len(absorbed_views))
        for v in absorbed_views:
            lines.append("                 - %s" % v)
    else:
        lines.append("Absorbed:      nothing (all plan items were no-ops)")
    if verify_result is None:
        lines.append("Verify:        DID NOT COMPLETE -- see the reason above")
    else:
        lines.append(
            "Verify:        %d/%d absorption leg(s) confirmed; %d/%d no-op "
            "leg(s) confirmed"
            % (verify_result.get("absorption_confirmed", 0),
               verify_result.get("absorption_checked", 0),
               verify_result.get("confirmed", 0),
               verify_result.get("checked", 0)))
    for label, key in (("Census", "census"), ("Diff-check", "diff")):
        rc, tail = results.get(key, (None, ""))
        verdict = ("skipped" if rc is None else
                   ("clean (exit 0)" if rc == 0 else "PROBLEM (exit %d)" % rc))
        lines.append("%-14s %s" % (label + ":", verdict))
        if tail:
            for ln in tail.splitlines()[-6:]:
                lines.append("                 | %s" % ln)
    lines.append("===============================")
    return "\n".join(lines)


# --------------------------------------------------------------- reverify mode
def validate_manifest_only(staging, stamp_check=None):
    """The subset of validate_staging() that --reverify needs: the F17-stamped
    dispatch-manifest.json, which is the ONLY staging artifact a verify pass
    consumes (verify_run() rebuilds its packets from the journal record and the
    current view bodies -- it never reads the answers). Returns the manifest
    path. Deliberately does NOT require plan.json/answers: on a re-verify those
    have already been absorbed and may legitimately be gone."""
    if not staging or not os.path.isdir(staging):
        raise StagingError("staging dir does not exist: %s" % staging)
    manifest_path = os.path.join(staging, "dispatch-manifest.json")
    if not os.path.isfile(manifest_path):
        raise StagingError("no dispatch-manifest.json in %s" % staging)
    try:
        manifest = json.load(open(manifest_path, encoding="utf-8"))
    except (ValueError, TypeError, OSError) as e:
        raise StagingError("dispatch-manifest.json unparseable: %s" % e)
    if stamp_check is None:
        stamp_check = _backends()._verify_dispatch_stamp
    ok, reason = stamp_check(manifest)
    if not ok:
        raise StagingError(
            "dispatch-manifest.json refused (F17 dispatch-record attestation): "
            "%s" % reason)
    return manifest_path


def execute_reverify(root, seq, staging, auth_path, engine=None, probe=None,
                     out=print):
    """Re-fire the verify legs for an ALREADY-COMMITTED run whose absorption
    commit still stands. This is the reconciliation action for the case the
    2026-07-28 live run hit: a transient transport failure after a perfectly
    good absorb, where reverting and re-absorbing byte-identical content would
    be pure waste. It also exists because runs journaled BEFORE that fix (e.g.
    seq 103) sit in exactly this state.

    It NEVER reverts and NEVER absorbs: the only write it causes is the fresh
    verify record verify_run() appends. If the re-fired legs fail again, the
    run simply stays non-terminal and reconciliation keeps blocking -- the same
    state it was already in, with one more journaled attempt on the record.

    Authorization is validated BEFORE the dispatch, exactly as on --run, and so
    is the bridge probe -- re-firing legs into an unreachable or mis-resolved
    codex is the exact waste this mode exists to avoid."""
    engine = engine or RealEngine()
    probe = probe or probe_bridge
    repo = os.path.abspath(root)
    if not os.path.isdir(repo) or not _is_git_repo(repo):
        out("REFUSED: --root %s is not a git repository." % root)
        return EXIT_FAIL

    try:
        authorization = validate_authorization(repo, auth_path)
    except AuthorizationError as e:
        out("REFUSED (authorization): %s" % e)
        out("Nothing was dispatched.")
        return EXIT_FAIL
    ok, _detail = probe(repo, out=out)
    if not ok:
        return EXIT_FAIL
    try:
        manifest_path = validate_manifest_only(staging)
    except StagingError as e:
        out("REFUSED (staging): %s" % e)
        out("Nothing was dispatched.")
        return EXIT_FAIL

    recs = load_journal(repo)
    rec = recs.get(seq)
    if rec is None:
        out("REFUSED: no journal record at seq %d." % seq)
        return EXIT_FAIL
    if str(rec.get("run_type", "")).lower() != "compile":
        out("REFUSED: journal seq %d is a %r record, not a compile run."
            % (seq, rec.get("run_type")))
        return EXIT_FAIL
    for r in recs.values():
        dr = r.get("driver_revert")
        if isinstance(dr, dict) and dr.get("reverts_seq") == seq \
                and dr.get("status") == "reverted":
            out("REFUSED: run seq %d was already reverted -- its absorption no "
                "longer stands, so there is nothing to verify. Re-absorb "
                "instead." % seq)
            return EXIT_FAIL
    if seq in _terminal_seqs(recs, repo=repo):
        out("Nothing to do: run seq %d already has a terminal verify "
            "disposition. No dispatch was made." % seq)
        return EXIT_OK

    events_views = []
    for a in rec.get("absorbed") or []:
        for e in a.get("events") or []:
            events_views.append((e, a.get("view")))
    for nc in rec.get("noop_candidates") or []:
        if not nc.get("verified"):
            events_views.append((nc.get("event"), nc.get("view")))
    try:
        guard = engine.dispatch_guard(repo, "verify", events_views,
                                      authorization)
    except Exception as e:                                  # noqa: BLE001
        out("REFUSED (dispatch guard could not be evaluated): %s" % e)
        return EXIT_FAIL
    if not guard.get("permit"):
        out("REFUSED (authorization does not cover this verify dispatch): %s"
            % guard.get("reason"))
        return EXIT_FAIL

    absorbed_views = sorted({a.get("view") for a in rec.get("absorbed") or []
                             if a.get("view")})
    out("REVERIFY: re-firing verify legs for run seq %d (%d absorbed view(s)). "
        "No absorb, no revert." % (seq, len(absorbed_views)))
    try:
        vbackend = engine.verify_backend(repo, manifest_path,
                                         verify_timeout_ms())
        vres = engine.verify_run_guarded(repo, seq, vbackend, authorization)
    except Exception as e:                                  # noqa: BLE001
        name = type(e).__name__
        if name == "LockHeld":
            out("LOCK HELD: %s" % e)
            return EXIT_LOCK_HELD
        out("REVERIFY FAILED (%s): %s" % (name, e))
        out("Run seq %d stays non-terminal; reconciliation keeps blocking."
            % seq)
        return EXIT_FAIL

    new_rec = load_journal(repo).get(vres.get("seq"), {})
    bad = verify_record_legs(repo, new_rec)["incomplete"]
    checked = vres.get("absorption_checked", 0)
    confirmed = vres.get("absorption_confirmed", 0)
    if bad:
        out("REVERIFY INCOMPLETE: %d leg(s) still never completed: %s"
            % (len(bad), "; ".join(bad)))
        out("Nothing reverted. Run seq %d stays non-terminal and "
            "reconciliation keeps blocking -- retry when the transport is "
            "healthy, or revert the run by hand." % seq)
        return EXIT_FAIL
    if confirmed != checked or vres.get("confirmed", 0) != vres.get("checked", 0):
        out("REVERIFY NON-CONFIRM: the legs completed with real verdicts, but "
            "%d of %d absorption leg(s) confirmed. The verdicts are journaled "
            "data -- read receipts/verify/, then adjudicate with `--revert "
            "--seq %d` and re-absorb corrected answers via `--run`. Not "
            "reverted here." % (confirmed, checked, seq))
        return EXIT_FAIL
    out("REVERIFY CLEAN: every leg completed and confirmed; run seq %d now has "
        "a terminal verify disposition (new verify record at seq %s)."
        % (seq, vres.get("seq")))
    return EXIT_OK


# --------------------------------------------------------------- revert mode
def _find_run_commit(repo, seq):
    """The commit that ADDED receipts/journal/<seq>.json -- the run commit.
    (--diff-filter=A: the journal is append-only, so the record file is added
    exactly once; a later revert RESTORES it but never re-adds it.)"""
    rel = "receipts/journal/%d.json" % seq
    rc, out, _e = _git(repo, "log", "--diff-filter=A", "--format=%H", "--", rel)
    if rc != 0 or not out.strip():
        return None
    return out.split()[-1]


def execute_revert(root, seq, reason=None, out=print):
    """Operator adjudication of a run that must not stand: revert its run
    commit through the SAME machinery the auto-revert path uses
    (revert_run_commit: `git revert -n`, journal record restored, revert
    journaled, one stage-only commit). Two states qualify:

      * a run whose verify legs COMPLETED with non-confirm verdicts
        (rejected/revised) -- the atomicity rule leaves that run committed and
        the branch unmergeable "until adjudicated"; THIS is the shipped
        adjudication. The correction then rides a fresh --run over corrected
        answers (the staging dir is untouched), so it lands validated,
        journaled, and re-verified -- never as a hand-edit of written views.
      * a run with NO terminal verify disposition (crashed run, or a pre-fix
        record whose legs never completed) -- this mechanizes reconcile advice
        option (b), which used to end "by hand".

    It REFUSES a fully-confirmed run: there is nothing to adjudicate, and a
    confirmed absorption that later proves wrong is corrected by a new raw
    event through a new run, not by rewriting history's disposition.

    Added 2026-08-03 (v3.0-local-5, second finding): without this mode the
    only exits from an all-rejected run were a manual revert or corrections
    committed outside the engine -- the first live all-rejected run took the
    second exit and shipped 14 corrected-but-unverified views."""
    repo = os.path.abspath(root)
    if not os.path.isdir(repo) or not _is_git_repo(repo):
        out("REFUSED: --root %s is not a git repository." % root)
        return EXIT_FAIL
    if not _worktree_clean(repo):
        out("REFUSED: the worktree is not clean. The revert must land as its "
            "own stage-only commit; commit or stash your changes first. "
            "Nothing was reverted.")
        return EXIT_FAIL

    recs = load_journal(repo)
    rec = recs.get(seq)
    if rec is None:
        out("REFUSED: no journal record at seq %d." % seq)
        return EXIT_FAIL
    if str(rec.get("run_type", "")).lower() != "compile":
        out("REFUSED: journal seq %d is a %r record, not a compile run."
            % (seq, rec.get("run_type")))
        return EXIT_FAIL
    for r in recs.values():
        dr = r.get("driver_revert")
        if isinstance(dr, dict) and dr.get("reverts_seq") == seq \
                and dr.get("status") == "reverted":
            out("REFUSED: run seq %d is already reverted (journal has a "
                "'reverted' driver-revert record for it). Re-absorb the "
                "corrected answers with --run." % seq)
            return EXIT_FAIL

    # Verify disposition: which of the two qualifying states is this -- or is
    # it a fully-confirmed run, which is refused?
    vrecs = [r for r in recs.values() if r.get("verifies_seq") == seq]
    disposition = "unverified (no verify record covers this run)"
    if vrecs:
        newest = max(vrecs, key=lambda r: r.get("seq", 0))
        legs = verify_record_legs(repo, newest)
        if legs["incomplete"]:
            disposition = ("unverified (%d verify leg(s) never completed)"
                           % len(legs["incomplete"]))
        else:
            nonconfirm = [l for l in legs["legs"]
                          if not (l["label"].startswith("confirm")
                                  or l["label"].startswith(
                                      "substrate-gated(confirm"))]
            if not nonconfirm:
                out("REFUSED: run seq %d is fully confirmed -- every verify "
                    "leg completed and confirmed, so there is nothing to "
                    "adjudicate. A confirmed absorption that later proves "
                    "wrong is corrected by a NEW raw event through a new run, "
                    "never by reverting a confirmed disposition." % seq)
                return EXIT_FAIL
            disposition = ("non-confirm verify verdict(s): %s"
                           % ", ".join(sorted({l["label"]
                                               for l in nonconfirm})))

    run_sha = _find_run_commit(repo, seq)
    if not run_sha:
        out("REFUSED: could not locate the commit that added "
            "receipts/journal/%d.json -- the run commit is not on this "
            "branch's history. Nothing was reverted." % seq)
        return EXIT_FAIL

    reason = reason or ("operator adjudication via --revert: %s -- corrected "
                        "re-absorb to follow" % disposition)
    out("REVERT: run seq %d (%s) -- %s" % (seq, run_sha[:12], disposition))
    ok, detail = revert_run_commit(repo, run_sha, seq, reason, out=out)
    if not ok:
        out("REVERT FAILED: %s" % detail)
        out("If the revert conflicted, the failure is journaled as "
            "'revert-failed' (non-terminal): reconciliation blocks every "
            "later run until the conflict is resolved by hand.")
        return EXIT_FAIL
    out("REVERT CLEAN: %s. The staging dir (if kept) is untouched -- correct "
        "the answers there and re-run --run so the correction lands "
        "validated, journaled, and verified." % detail)
    return EXIT_OK


# --------------------------------------------------------------- reconcile mode
def execute_reconcile(root, out=print):
    repo = os.path.abspath(root)
    if not os.path.isdir(repo) or not _is_git_repo(repo):
        out("--reconcile: %s is not a git repository" % root)
        return EXIT_FAIL
    state = reconcile_state(repo)
    if state["stale_older"]:
        out("Older run record(s) with no terminal verify disposition "
            "(advisory): %s" % ", ".join(str(s) for s in state["stale_older"]))
    if state["blocked"]:
        out("BLOCKED: %s" % state["reason"])
        out(_reconcile_advice(state["seq"]))
        return EXIT_FAIL
    out("Reconciliation clean: %s. New runs are accepted." % state["reason"])
    return EXIT_OK


# --------------------------------------------------------------- self-test
def self_test():                                            # noqa: C901
    import shutil
    import tempfile

    total = failed = 0

    def case(name, ok, detail=""):
        nonlocal total, failed
        total += 1
        print("  %s %s%s" % ("ok " if ok else "XX ", name,
                             ("  << " + repr(detail)) if (not ok and detail != "")
                             else ""))
        if not ok:
            failed += 1

    def write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    # ---------------------------------------------------------------- fixtures
    GRANT = "\n".join([
        "# Operator standing authorization -- verify legs for wired compiles",
        "",
        "**Verbatim grant:** \"yes to all 3\" -- given in session.",
        "",
        "Covers cross-vendor VERIFY dispatches fired by deploy/compile-driver.py",
        "on every absorption.",
        "",
    ])

    def make_repo(prefix="cdrv-"):
        base = tempfile.mkdtemp(prefix=prefix)
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"]):
            _git(base, *args)
        write(os.path.join(base, "wiki", "a.md"), "# View A\n\nbody\n")
        _git(base, "add", "-A")
        _git(base, "commit", "-qm", "seed")
        return base

    def make_grant(repo, name="operator-standing-verify-2026-07-28.md",
                   text=GRANT):
        write(os.path.join(repo, "deploy", "evidence", name), text)
        return "deploy/evidence/" + name

    def make_staging(repo, view="wiki/a.md", events=("raw/e1.md",)):
        st = os.path.join(repo, ".batch-run", "t1")
        os.makedirs(os.path.join(st, "answers"), exist_ok=True)
        for e in events:
            write(os.path.join(repo, e.replace("/", os.sep)), "event body\n")
        plan = {"items": [{"view": view, "events": list(events),
                           "event_class": {e: {"class": "observation",
                                               "origin": "judgment"}
                                           for e in events}}]}
        write(os.path.join(st, "plan.json"), json.dumps(plan, indent=1))
        write(os.path.join(st, "answers", "01-view.json"), json.dumps(
            {"new_text": "# View A\n\nbody\n\nabsorbed\n", "manifest": [],
             "corpus_support": [], "noops": []}, indent=1))
        manifest = {"packets": [{"packet": "packets/01-view.md",
                                 "answer": "answers/01-view.json",
                                 "view": view, "events": list(events),
                                 "lock_class": False,
                                 "model": "m", "vendor": "v"}],
                    "created": "2026-07-28T00:00:00"}
        # stamp it the way stamp_dispatch does, without importing the engine:
        # sha256 over the canonical manifest bytes with `dispatch` excluded.
        import hashlib
        blob = json.dumps(manifest, indent=1, sort_keys=True).encode("utf-8")
        manifest["dispatch"] = {
            "model": "m", "vendor": "v",
            "identity_source": "attestation:self-test-fixture",
            "stamped_at": "2026-07-28T00:00:00",
            "manifest_sha256_at_stamp": hashlib.sha256(blob).hexdigest()}
        write(os.path.join(st, "dispatch-manifest.json"),
              json.dumps(manifest, indent=1, sort_keys=True))
        return st

    def stamp_check_local(manifest):
        import hashlib
        d = manifest.get("dispatch")
        if not isinstance(d, dict):
            return False, "manifest has no well-formed top-level 'dispatch' stamp"
        for k in ("model", "vendor", "identity_source", "stamped_at",
                  "manifest_sha256_at_stamp"):
            if not d.get(k):
                return False, "dispatch stamp missing field(s): %s" % k
        check = dict(manifest)
        check.pop("dispatch", None)
        blob = json.dumps(check, indent=1, sort_keys=True).encode("utf-8")
        if hashlib.sha256(blob).hexdigest() != d["manifest_sha256_at_stamp"]:
            return False, "dispatch stamp sha256 mismatch"
        return True, "ok"

    def class_check_local(rel):
        import fnmatch
        d, b = posixpath.split(rel)
        return d == "deploy/evidence" and fnmatch.fnmatchcase(b, "operator-*.md")

    # The verdict RECORDS a real BridgeVerifyBackend hands back. The transport
    # classes are the point: the backend returns these as data (it does not
    # raise), which is exactly how the live seq-103 defect slipped past the
    # first build -- so the fake returns them too.
    VERDICTS = {
        "confirm": {"verdict": "confirmed", "reason": "faithful"},
        "revised": {"verdict": "revised", "reason": "omission in section 2"},
        "rejected": {"verdict": "rejected", "reason": "fabrication"},
        "bridge-error": {"verdict": "bridge-error",
                         "reason": "bridge exited 1: upstream 400"},
        "timeout": {"verdict": "timeout",
                    "reason": "no verdict within 540000ms"},
        "unparseable": {"verdict": "unparseable", "reason": ""},
        "no-verdict": {"reason": "nothing came back"},
        "gated-real": {"verdict": "substrate-gated",
                       "reason": "F17 attestation gate",
                       "bridge_verdict": {"verdict": "rejected",
                                          "reason": "stale contradiction"},
                       "gated_inner_verdict": "rejected"},
        "gated-confirm": {"verdict": "substrate-gated",
                          "reason": "F17 attestation gate",
                          "bridge_verdict": {"verdict": "confirmed"},
                          "gated_inner_verdict": "confirmed"},
        "gated-bare": {"verdict": "substrate-gated",
                       "reason": "F17 attestation gate"},
    }

    class FakeEngine:
        """Same four methods as RealEngine. Its run_guarded performs a REAL
        absorb-shaped write + journal append + stage-only commit (via
        compile-core, stdlib+git only), and its verify_run_guarded writes REAL
        verdict artifacts + a real verify record -- so the revert,
        classification and reconciliation logic under test runs against real
        git, a real chained journal and real on-disk verdicts. It NEVER touches
        a bridge."""

        def __init__(self, verify="confirm", permit=True, view="wiki/a.md",
                     absorption_checked=None, lock_held=False):
            self.verify = verify
            self.permit = permit
            self.view = view
            self.absorption_checked = absorption_checked
            self.lock_held = lock_held
            self.verify_calls = 0

        def dispatch_guard(self, repo, kind, events_views, authorization):
            return {"disposition": "HUMAN-GATE", "permit": self.permit,
                    "reason": "fake guard" if self.permit
                              else "fake guard -- non-covering authorization",
                    "authorization": authorization}

        def absorb_backend(self, staging):
            return {"staging": staging}

        def verify_backend(self, repo, manifest_path, timeout_ms):
            self.timeout_ms = timeout_ms
            return {"manifest": manifest_path, "timeout_ms": timeout_ms}

        def run_guarded(self, repo, plan, backend, authorization):
            core = _core()
            if self.lock_held:
                raise core.LockHeld({"pid": 1, "hostname": "h",
                                     "started_iso": "now",
                                     "run_type": "compile"})
            vp = os.path.join(repo, self.view.replace("/", os.sep))
            os.makedirs(os.path.dirname(vp), exist_ok=True)
            old = open(vp, encoding="utf-8").read() if os.path.isfile(vp) else ""
            with open(vp, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(old + "absorbed line\n")
            rec = core.minimal_record("compile", "0" * 40)
            rec["absorbed"] = [{"view": self.view, "events": ["raw/e1.md"],
                                "pre_blob": "a" * 40, "post_blob": "b" * 40,
                                "manifest": [], "corpus_support": []}]
            rec["run_window"] = {"start": "t0", "end": "t1"}
            seq, jpath = core.append_record(repo, rec)
            jrel = os.path.relpath(jpath, repo).replace(os.sep, "/")
            sha = core.stage_only_commit(repo, [self.view, jrel],
                                         "fake compile run seq %d" % seq)
            return {"sha": sha, "seq": seq, "flags": [], "noop_candidates": [],
                    "rebuilds": 1}

        def verify_run_guarded(self, repo, seq, backend, authorization):
            self.verify_calls += 1
            if self.verify == "raise":
                raise RuntimeError("bridge timeout after 540000 ms")
            core = _core()
            checked = 1 if self.absorption_checked is None \
                else self.absorption_checked
            verdict = VERDICTS[self.verify]
            is_confirm = str(verdict.get("verdict", "")).startswith("confirm")
            confirmed = checked if is_confirm else 0
            vrec = core.minimal_record("verify", "0" * 40)
            vrec["verifies_seq"] = seq
            vrec["run_window"] = {"start": "t0", "end": "t1"}
            artifacts = []
            if checked:
                art_rel = "receipts/verify/absorb-seq%d-v0.json" % seq
                ap = os.path.join(repo, art_rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(ap), exist_ok=True)
                with open(ap, "w", encoding="utf-8", newline="\n") as fh:
                    json.dump(verdict, fh, indent=1, sort_keys=True)
                artifacts.append(art_rel)
                entry = {"view": self.view, "events": ["raw/e1.md"],
                         "artifact": art_rel, "packet_sha256": "d" * 64}
                if is_confirm:
                    entry["verified_at"] = "t1"
                    vrec["absorption_verified"] = [entry]
                else:
                    entry["reason"] = verdict.get("reason", "")
                    vrec["absorption_verify_attempts"] = [entry]
            vseq, jpath = core.append_record(repo, vrec)
            jrel = os.path.relpath(jpath, repo).replace(os.sep, "/")
            core.stage_only_commit(repo, artifacts + [jrel],
                                   "fake verify seq %d over %d" % (vseq, seq))
            return {"sha": "x", "seq": vseq, "confirmed": 0, "checked": 0,
                    "events_checked": 0, "events_confirmed": 0,
                    "absorption_checked": checked,
                    "absorption_confirmed": confirmed}

    def quiet_sensors(repo, sha, sections):
        return {"census": (0, "census: problems: [] new_holes: []"),
                "diff": (0, "check-run-diff: CLEAN")}

    def silent(*_a, **_k):
        pass

    def quiet_probe(repo, out=print, **_kw):
        """Default probe stand-in: the self-test NEVER spawns a real codex."""
        return True, "0.144.1 (fixture)"

    def run_driver(*a, **kw):
        """execute_run with the self-test's injection seams defaulted in."""
        kw.setdefault("probe", quiet_probe)
        return execute_run(*a, **kw)

    def reverify_driver(*a, **kw):
        kw.setdefault("probe", quiet_probe)
        return execute_reverify(*a, **kw)

    # ------------------------------------------------------- A. argv parsing
    try:
        parse_args(["--run", "--root", ".", "--staging", "s",
                    "--authorization", "a", "--no-verify"])
        case("--no-verify is a parse error (no live absorb can skip verify)",
             False)
    except UsageError as e:
        case("--no-verify is a parse error (no live absorb can skip verify)",
             "--no-verify" in str(e))
    case("--no-verify appears nowhere in the usage string",
         "--no-verify" not in USAGE)
    try:
        parse_args(["--run", "--root", ".", "--staging", "s"])
        case("--run without --authorization refuses", False)
    except UsageError as e:
        case("--run without --authorization refuses",
             "authorization" in str(e).lower())
    try:
        parse_args(["--run", "--root", ".", "--authorization", "a"])
        case("--run without --staging refuses", False)
    except UsageError as e:
        case("--run without --staging refuses", "staging" in str(e))
    try:
        parse_args(["--run", "--root", ".", "--staging", "s",
                    "--authorization", "a", "--force"])
        case("unknown flag refused (never silently ignored)", False)
    except UsageError as e:
        case("unknown flag refused (never silently ignored)", "--force" in str(e))
    parsed = parse_args(["--run", "--root", "r", "--staging", "s",
                         "--authorization", "a", "--sections"])
    case("valid --run argv parses (mode/root/staging/authorization/sections)",
         parsed == {"mode": "run", "root": "r", "staging": "s",
                    "authorization": "a", "seq": None, "reason": None,
                    "sections": True},
         parsed)
    case("--self-test parses as its own mode",
         parse_args(["--self-test"])["mode"] == "self-test")
    case("--reconcile parses as its own mode",
         parse_args(["--reconcile", "--root", "r"])["mode"] == "reconcile")
    case("no arguments -> no mode (main maps this to exit 2)",
         parse_args([])["mode"] is None)

    # ------------------------------------------------- B. authorization checks
    repo_b = make_repo("cdrv-auth-")
    try:
        good = make_grant(repo_b)
        auth = validate_authorization(repo_b, good, class_check=class_check_local)
        case("valid grant accepted; verbatim quote extracted from the grant line",
             auth["quote"] == "yes to all 3"
             and auth["path"] == good, auth)
        for name, path, needle in (
            ("missing file refused",
             "deploy/evidence/operator-nope.md", "not found"),
            ("wrong directory refused (must be exactly deploy/evidence)",
             "docs/operator-x.md", "operator-artifact class"),
            ("nested path under deploy/evidence refused",
             "deploy/evidence/operator-sub/nested.md", "operator-artifact class"),
            ("non-operator basename refused",
             "deploy/evidence/decision-2026-07-28.md", "operator-artifact class"),
            ("path traversal refused", "../operator-x.md", "escapes"),
        ):
            try:
                validate_authorization(repo_b, path,
                                       class_check=class_check_local)
                case(name, False)
            except AuthorizationError as e:
                case(name, needle in str(e), str(e))
        noncov = make_grant(repo_b, "operator-unrelated-2026-07-28.md",
                            "# Some other decision\n\n"
                            "**Verbatim grant:** \"go ahead\"\n\n"
                            "About something else entirely.\n")
        try:
            validate_authorization(repo_b, noncov, class_check=class_check_local)
            case("non-covering authorization refused (missing markers)", False)
        except AuthorizationError as e:
            case("non-covering authorization refused (missing markers)",
                 "does not cover" in str(e), str(e))
        revoked = make_grant(repo_b, "operator-revoked-2026-07-28.md",
                             GRANT + "\nREVOKED 2026-07-29 by operator.\n")
        try:
            validate_authorization(repo_b, revoked,
                                   class_check=class_check_local)
            case("REVOKED grant refused", False)
        except AuthorizationError as e:
            case("REVOKED grant refused", "REVOKED" in str(e), str(e))
        noquote = make_grant(repo_b, "operator-noquote-2026-07-28.md",
                             "# Grant\n\nCovers verify legs fired by "
                             "deploy/compile-driver.py.\n")
        try:
            validate_authorization(repo_b, noquote,
                                   class_check=class_check_local)
            case("grant with no 'Verbatim grant:' quote refused", False)
        except AuthorizationError as e:
            case("grant with no 'Verbatim grant:' quote refused",
                 "Verbatim grant" in str(e), str(e))
        try:
            validate_authorization(repo_b, None, class_check=class_check_local)
            case("empty --authorization refused", False)
        except AuthorizationError:
            case("empty --authorization refused", True)
    finally:
        shutil.rmtree(repo_b, ignore_errors=True)

    # --------------------------------------------------- B2. the SHIPPED grant
    live_root = os.path.dirname(_HERE)
    live_grant = ("deploy/evidence/"
                  "operator-standing-verify-authorization-2026-07-28.md")
    if os.path.isfile(os.path.join(live_root, live_grant.replace("/", os.sep))):
        try:
            a = validate_authorization(live_root, live_grant,
                                       class_check=class_check_local)
            case("the SHIPPED standing grant validates and yields a quote",
                 bool(a["quote"]), a)
        except AuthorizationError as e:
            case("the SHIPPED standing grant validates and yields a quote",
                 False, str(e))

    # ------------------------------------------------------- C. staging checks
    repo_c = make_repo("cdrv-stg-")
    try:
        st = make_staging(repo_c)
        info = validate_staging(st, stamp_check=stamp_check_local)
        case("valid staging accepted (plan + stamped manifest + answers)",
             info["views"] == ["wiki/a.md"]
             and info["events_views"] == [("raw/e1.md", "wiki/a.md")], info)
        try:
            validate_staging(os.path.join(repo_c, "no-such-dir"),
                             stamp_check=stamp_check_local)
            case("missing staging dir refused", False)
        except StagingError as e:
            case("missing staging dir refused", "does not exist" in str(e))
        os.replace(os.path.join(st, "plan.json"),
                   os.path.join(st, "plan.json.bak"))
        try:
            validate_staging(st, stamp_check=stamp_check_local)
            case("staging without plan.json refused", False)
        except StagingError as e:
            case("staging without plan.json refused", "plan.json" in str(e))
        os.replace(os.path.join(st, "plan.json.bak"),
                   os.path.join(st, "plan.json"))
        man_path = os.path.join(st, "dispatch-manifest.json")
        man = json.load(open(man_path, encoding="utf-8"))
        unstamped = dict(man)
        unstamped.pop("dispatch")
        write(man_path, json.dumps(unstamped, indent=1, sort_keys=True))
        try:
            validate_staging(st, stamp_check=stamp_check_local)
            case("unstamped dispatch-manifest refused (F17 attestation)", False)
        except StagingError as e:
            case("unstamped dispatch-manifest refused (F17 attestation)",
                 "F17" in str(e), str(e))
        write(man_path, json.dumps(man, indent=1, sort_keys=True))
        os.remove(os.path.join(st, "answers", "01-view.json"))
        try:
            validate_staging(st, stamp_check=stamp_check_local)
            case("missing answer file refused", False)
        except StagingError as e:
            case("missing answer file refused", "answer file missing" in str(e))
        write(os.path.join(st, "answers", "01-view.json"),
              json.dumps({"new_text": "x", "manifest": []}))
        try:
            validate_staging(st, stamp_check=stamp_check_local)
            case("answer file missing required keys refused", False)
        except StagingError as e:
            case("answer file missing required keys refused",
                 "required key" in str(e))
        write(os.path.join(st, "answers", "01-view.json"), json.dumps(
            {"new_text": "x", "manifest": [], "corpus_support": [],
             "noops": []}))
        plan = json.load(open(os.path.join(st, "plan.json"), encoding="utf-8"))
        plan["items"][0]["view"] = "wiki/other.md"
        write(os.path.join(st, "plan.json"), json.dumps(plan, indent=1))
        try:
            validate_staging(st, stamp_check=stamp_check_local)
            case("plan/manifest view disagreement refused", False)
        except StagingError as e:
            case("plan/manifest view disagreement refused",
                 "disagree" in str(e), str(e))
    finally:
        shutil.rmtree(repo_c, ignore_errors=True)

    # ------------------------------------------- D. PRE-WRITE: nothing written
    repo_d = make_repo("cdrv-prewrite-")
    try:
        st = make_staging(repo_d)
        good = make_grant(repo_d)
        _git(repo_d, "add", "-A")
        _git(repo_d, "commit", "-qm", "fixtures")
        head_before = _git(repo_d, "rev-parse", "HEAD")[1].strip()
        rc = run_driver(repo_d, st, "deploy/evidence/operator-nope.md",
                         engine=FakeEngine(), sensors=quiet_sensors, out=silent)
        case("--run with a missing authorization exits 1", rc == EXIT_FAIL)
        case("...and wrote NOTHING (no journal, HEAD unmoved, tree clean)",
             not os.path.isdir(os.path.join(repo_d, "receipts", "journal"))
             and _git(repo_d, "rev-parse", "HEAD")[1].strip() == head_before
             and _worktree_clean(repo_d))
        noncov = make_grant(repo_d, "operator-elsewhere-2026-07-28.md",
                            "# Other\n\n**Verbatim grant:** \"ok\"\n")
        _git(repo_d, "add", "-A")
        _git(repo_d, "commit", "-qm", "noncov")
        head_before = _git(repo_d, "rev-parse", "HEAD")[1].strip()
        rc = run_driver(repo_d, st, noncov, engine=FakeEngine(),
                         sensors=quiet_sensors, out=silent)
        case("--run with a non-covering authorization exits 1 pre-write",
             rc == EXIT_FAIL
             and not os.path.isdir(os.path.join(repo_d, "receipts", "journal"))
             and _git(repo_d, "rev-parse", "HEAD")[1].strip() == head_before)
        rc = run_driver(repo_d, st, good,
                         engine=FakeEngine(permit=False),
                         sensors=quiet_sensors, out=silent)
        case("dispatch_guard refusal (dry run) stops the run pre-write",
             rc == EXIT_FAIL
             and not os.path.isdir(os.path.join(repo_d, "receipts", "journal")))
        rc = run_driver(repo_d, os.path.join(repo_d, "no-staging"), good,
                         engine=FakeEngine(), sensors=quiet_sensors, out=silent)
        case("bad staging dir refuses pre-write, nothing journaled",
             rc == EXIT_FAIL
             and not os.path.isdir(os.path.join(repo_d, "receipts", "journal")))
        rc = run_driver(repo_d, st, good, engine=FakeEngine(lock_held=True),
                         sensors=quiet_sensors, out=silent)
        case("a held compile lock maps to exit 3", rc == EXIT_LOCK_HELD)
    finally:
        shutil.rmtree(repo_d, ignore_errors=True)

    # ------------------------------------------------- E. round trip: CONFIRM
    repo_e = make_repo("cdrv-confirm-")
    try:
        st = make_staging(repo_e)
        good = make_grant(repo_e)
        _git(repo_e, "add", "-A")
        _git(repo_e, "commit", "-qm", "fixtures")
        eng = FakeEngine(verify="confirm")
        rc = run_driver(repo_e, st, good, sections=True, engine=eng,
                         sensors=quiet_sensors, out=silent)
        case("fixture round-trip through run_guarded + verify: exit 0",
             rc == EXIT_OK)
        recs = load_journal(repo_e)
        case("round-trip journaled a compile record and a verify record",
             any(r.get("run_type") == "compile" for r in recs.values())
             and any(r.get("verifies_seq") for r in recs.values()), sorted(recs))
        case("no revert record on the confirm path",
             not any(r.get("driver_revert") for r in recs.values()))
        case("absorbed content is still on disk after a confirmed run",
             "absorbed line" in open(os.path.join(repo_e, "wiki", "a.md"),
                                     encoding="utf-8").read())
        case("verify backend was pinned with an explicit timeout (540000 "
             "default when VERIFY_TIMEOUT_MS is unset)",
             eng.timeout_ms == verify_timeout_ms())
        state = reconcile_state(repo_e)
        case("a completed run+verify leaves reconciliation unblocked",
             state["blocked"] is False, state)
    finally:
        shutil.rmtree(repo_e, ignore_errors=True)

    # --------------------------------------------- F. NON-CONFIRM: no revert
    repo_f = make_repo("cdrv-nonconfirm-")
    try:
        st = make_staging(repo_f)
        good = make_grant(repo_f)
        _git(repo_f, "add", "-A")
        _git(repo_f, "commit", "-qm", "fixtures")
        rc = run_driver(repo_f, st, good, engine=FakeEngine(verify="revised"),
                         sensors=quiet_sensors, out=silent)
        case("non-confirm verdict exits 1", rc == EXIT_FAIL)
        recs = load_journal(repo_f)
        case("non-confirm verdict does NOT revert (no revert record)",
             not any(r.get("driver_revert") for r in recs.values()))
        case("non-confirm leaves the absorbed content in place (it IS verified)",
             "absorbed line" in open(os.path.join(repo_f, "wiki", "a.md"),
                                     encoding="utf-8").read())
        log = _git(repo_f, "log", "--oneline")[1]
        case("non-confirm leaves the run commit in history",
             "fake compile run" in log, log)
    finally:
        shutil.rmtree(repo_f, ignore_errors=True)

    # ------------------------------------ G. INCOMPLETE verify: auto-revert
    repo_g = make_repo("cdrv-incomplete-")
    try:
        st = make_staging(repo_g)
        good = make_grant(repo_g)
        _git(repo_g, "add", "-A")
        _git(repo_g, "commit", "-qm", "fixtures")
        before = open(os.path.join(repo_g, "wiki", "a.md"),
                      encoding="utf-8").read()
        staging_files_before = sorted(os.listdir(st))
        rc = run_driver(repo_g, st, good, engine=FakeEngine(verify="raise"),
                         sensors=quiet_sensors, out=silent)
        case("incomplete verify leg exits 1", rc == EXIT_FAIL)
        case("incomplete verify AUTO-REVERTS the run commit (view restored)",
             open(os.path.join(repo_g, "wiki", "a.md"),
                  encoding="utf-8").read() == before)
        recs = load_journal(repo_g)
        reverts = [r for r in recs.values() if r.get("driver_revert")]
        case("the revert is journaled with its reason",
             len(reverts) == 1
             and reverts[0]["driver_revert"]["status"] == "reverted"
             and "did not complete" in reverts[0]["driver_revert"]["reason"],
             reverts)
        case("the reverted run's own journal record SURVIVES (append-only)",
             any(r.get("run_type") == "compile" for r in recs.values()),
             sorted(recs))
        case("journal chain is still intact after the revert",
             _core().check_chain(repo_g) == len(recs))
        case("staging dir preserved untouched for a clean re-run",
             sorted(os.listdir(st)) == staging_files_before)
        case("worktree is clean after the auto-revert",
             _worktree_clean(repo_g), _git(repo_g, "status", "--porcelain")[1])
        state = reconcile_state(repo_g)
        case("a journaled revert makes the run terminal (not blocking)",
             state["blocked"] is False, state)
    finally:
        shutil.rmtree(repo_g, ignore_errors=True)

    # ------------------------- H. coverage shortfall counts as INCOMPLETE
    repo_h = make_repo("cdrv-coverage-")
    try:
        st = make_staging(repo_h)
        good = make_grant(repo_h)
        _git(repo_h, "add", "-A")
        _git(repo_h, "commit", "-qm", "fixtures")
        before = open(os.path.join(repo_h, "wiki", "a.md"),
                      encoding="utf-8").read()
        rc = run_driver(repo_h, st, good,
                         engine=FakeEngine(verify="confirm",
                                           absorption_checked=0),
                         sensors=quiet_sensors, out=silent)
        case("a verify covering fewer views than were absorbed is INCOMPLETE",
             rc == EXIT_FAIL)
        case("...and triggers the auto-revert (invariant 4: every absorption)",
             open(os.path.join(repo_h, "wiki", "a.md"),
                  encoding="utf-8").read() == before
             and any(r.get("driver_revert") for r in load_journal(repo_h).values()))
        case("the restored journal record is byte-identical (chain intact "
             "across a revert that sits UNDER a later record)",
             _core().check_chain(repo_h) == len(load_journal(repo_h)))
    finally:
        shutil.rmtree(repo_h, ignore_errors=True)

    # --------------------------------------------- I. startup reconciliation
    repo_i = make_repo("cdrv-reconcile-")
    try:
        st = make_staging(repo_i)
        good = make_grant(repo_i)
        _git(repo_i, "add", "-A")
        _git(repo_i, "commit", "-qm", "fixtures")
        core = _core()
        rec = core.minimal_record("compile", "0" * 40)
        rec["absorbed"] = [{"view": "wiki/a.md", "events": ["raw/e1.md"],
                            "pre_blob": "a" * 40, "post_blob": "b" * 40,
                            "manifest": [], "corpus_support": []}]
        seq, jpath = core.append_record(repo_i, rec)
        core.stage_only_commit(
            repo_i, [os.path.relpath(jpath, repo_i).replace(os.sep, "/")],
            "crashed run seq %d (no verify record)" % seq)
        state = reconcile_state(repo_i)
        case("an unterminated run blocks reconciliation",
             state["blocked"] and state["seq"] == seq, state)
        head_before = _git(repo_i, "rev-parse", "HEAD")[1].strip()
        rc = run_driver(repo_i, st, good, engine=FakeEngine(),
                         sensors=quiet_sensors, out=silent)
        case("startup reconciliation refuses a new --run (exit 1)",
             rc == EXIT_FAIL)
        case("...and the refusal wrote nothing (HEAD unmoved, tree clean)",
             _git(repo_i, "rev-parse", "HEAD")[1].strip() == head_before
             and _worktree_clean(repo_i))
        case("--reconcile reports the block and exits 1",
             execute_reconcile(repo_i, out=silent) == EXIT_FAIL)
        vrec = core.minimal_record("verify", "0" * 40)
        vrec["verifies_seq"] = seq
        vseq, vpath = core.append_record(repo_i, vrec)
        core.stage_only_commit(
            repo_i, [os.path.relpath(vpath, repo_i).replace(os.sep, "/")],
            "reconciling verify seq %d" % vseq)
        case("reconciling the stale run unblocks it",
             reconcile_state(repo_i)["blocked"] is False)
        case("--reconcile now exits 0",
             execute_reconcile(repo_i, out=silent) == EXIT_OK)
        rc = run_driver(repo_i, st, good, engine=FakeEngine(),
                         sensors=quiet_sensors, out=silent)
        case("a reconciled branch accepts new runs again", rc == EXIT_OK)
    finally:
        shutil.rmtree(repo_i, ignore_errors=True)

    # ---------------------------------------- J. reconciliation unit shapes
    recs = {1: {"run_type": "compile"}, 2: {"run_type": "verify",
                                            "verifies_seq": 1},
            3: {"run_type": "compile"}}
    st3 = reconcile_state(".", recs=recs)
    case("newest-run gating: newest unterminated run blocks",
         st3["blocked"] and st3["seq"] == 3, st3)
    recs[4] = {"run_type": "driver-revert",
               "driver_revert": {"reverts_seq": 3, "status": "reverted"}}
    st4 = reconcile_state(".", recs=recs)
    case("a 'reverted' driver_revert record is a terminal disposition",
         st4["blocked"] is False, st4)
    recs[4]["driver_revert"]["status"] = "revert-failed"
    st5 = reconcile_state(".", recs=recs)
    case("a 'revert-failed' driver_revert record is NOT terminal (blocks)",
         st5["blocked"] is True, st5)
    recs2 = {1: {"run_type": "compile"}, 2: {"run_type": "compile"},
             3: {"run_type": "verify", "verifies_seq": 2}}
    st6 = reconcile_state(".", recs=recs2)
    case("an OLDER unterminated run is advisory, not blocking",
         st6["blocked"] is False and st6["stale_older"] == [1], st6)

    # ------------------------- L. verdict classification (the seq-103 defect)
    for key, want, why in (
        ("confirm", True, "confirmed is a completed leg"),
        ("revised", True, "revised is a completed leg (a real verdict)"),
        ("rejected", True, "rejected is a completed leg (a real verdict)"),
        ("bridge-error", False,
         "bridge-error is TRANSPORT, not a verdict -> incomplete"),
        ("timeout", False, "timeout is transport -> incomplete"),
        ("unparseable", False, "unparseable is transport -> incomplete"),
        ("no-verdict", False, "a missing verdict field -> incomplete"),
        ("gated-real", True,
         "substrate-gated carrying a real nested rejected -> completed"),
        ("gated-confirm", True,
         "substrate-gated carrying a nested confirmed -> completed"),
        ("gated-bare", False,
         "substrate-gated with no usable inner verdict -> incomplete"),
    ):
        got, label = classify_verdict(VERDICTS[key])
        case("classify_verdict: %s" % why, got is want, (key, got, label))
    case("classify_verdict: a non-dict artifact is incomplete (fail-closed)",
         classify_verdict(None)[0] is False)
    case("classify_verdict: an unknown verdict string is incomplete "
         "(allowlist, not denylist)",
         classify_verdict({"verdict": "probably-fine"})[0] is False)

    # ------------- M. a transport-class verdict on a FRESH run -> auto-revert
    for transport in ("bridge-error", "timeout", "unparseable", "gated-bare"):
        repo_m = make_repo("cdrv-transport-")
        try:
            st = make_staging(repo_m)
            good = make_grant(repo_m)
            _git(repo_m, "add", "-A")
            _git(repo_m, "commit", "-qm", "fixtures")
            before = open(os.path.join(repo_m, "wiki", "a.md"),
                          encoding="utf-8").read()
            staging_before = sorted(os.listdir(st))
            rc = run_driver(repo_m, st, good,
                             engine=FakeEngine(verify=transport),
                             sensors=quiet_sensors, out=silent)
            recs_m = load_journal(repo_m)
            case("verdict %r on a fresh run: exit 1 AND auto-revert fires "
                 "(NOT counted as a non-confirm verdict)" % transport,
                 rc == EXIT_FAIL
                 and open(os.path.join(repo_m, "wiki", "a.md"),
                          encoding="utf-8").read() == before
                 and any(r.get("driver_revert") for r in recs_m.values()),
                 rc)
            case("verdict %r: staging preserved, chain intact" % transport,
                 sorted(os.listdir(st)) == staging_before
                 and _core().check_chain(repo_m) == len(recs_m))
        finally:
            shutil.rmtree(repo_m, ignore_errors=True)

    repo_m2 = make_repo("cdrv-gatedreal-")
    try:
        st = make_staging(repo_m2)
        good = make_grant(repo_m2)
        _git(repo_m2, "add", "-A")
        _git(repo_m2, "commit", "-qm", "fixtures")
        rc = run_driver(repo_m2, st, good,
                         engine=FakeEngine(verify="gated-real"),
                         sensors=quiet_sensors, out=silent)
        case("substrate-gated WITH a real nested verdict is a completed "
             "non-confirm: exit 1, NO revert",
             rc == EXIT_FAIL
             and not any(r.get("driver_revert")
                         for r in load_journal(repo_m2).values()))
    finally:
        shutil.rmtree(repo_m2, ignore_errors=True)

    # ---------- N. the seq-103 state: verify record whose legs never completed
    def plant_seq103(repo, verdict_key="bridge-error"):
        """Reproduce the pre-fix live state by hand: a compile run commit, then
        a verify record whose only leg carries a transport-class verdict, with
        NO revert. This is exactly what journal seq 103 looks like."""
        core = _core()
        vp = os.path.join(repo, "wiki", "a.md")
        old = open(vp, encoding="utf-8").read()
        with open(vp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(old + "absorbed line\n")
        rec = core.minimal_record("compile", "0" * 40)
        rec["absorbed"] = [{"view": "wiki/a.md", "events": ["raw/e1.md"],
                            "pre_blob": "a" * 40, "post_blob": "b" * 40,
                            "manifest": [], "corpus_support": []}]
        rec["run_window"] = {"start": "t0", "end": "t1"}
        cseq, cpath = core.append_record(repo, rec)
        core.stage_only_commit(
            repo, ["wiki/a.md",
                   os.path.relpath(cpath, repo).replace(os.sep, "/")],
            "live-shaped run seq %d" % cseq)
        art_rel = "receipts/verify/absorb-seq%d-v0.json" % cseq
        ap = os.path.join(repo, art_rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(ap), exist_ok=True)
        with open(ap, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(VERDICTS[verdict_key], fh, indent=1, sort_keys=True)
        vrec = core.minimal_record("verify", "0" * 40)
        vrec["verifies_seq"] = cseq
        vrec["absorption_verify_attempts"] = [
            {"view": "wiki/a.md", "events": ["raw/e1.md"],
             "artifact": art_rel, "packet_sha256": "d" * 64,
             "reason": VERDICTS[verdict_key].get("reason", "")}]
        vseq, vpath = core.append_record(repo, vrec)
        core.stage_only_commit(
            repo, [art_rel,
                   os.path.relpath(vpath, repo).replace(os.sep, "/")],
            "live-shaped verify seq %d over %d" % (vseq, cseq))
        return cseq

    repo_n = make_repo("cdrv-seq103-")
    try:
        st = make_staging(repo_n)
        good = make_grant(repo_n)
        _git(repo_n, "add", "-A")
        _git(repo_n, "commit", "-qm", "fixtures")
        cseq = plant_seq103(repo_n)
        state = reconcile_state(repo_n)
        case("a verify record whose legs never completed is NON-terminal "
             "(the seq-103 regression)",
             state["blocked"] and state["seq"] == cseq, state)
        case("...and the refusal reason names the incomplete legs",
             "never completed" in state["reason"], state["reason"])
        case("--reconcile exits 1 on that state",
             execute_reconcile(repo_n, out=silent) == EXIT_FAIL)
        head_before = _git(repo_n, "rev-parse", "HEAD")[1].strip()
        rc = run_driver(repo_n, st, good, engine=FakeEngine(),
                         sensors=quiet_sensors, out=silent)
        case("a new --run is refused while that run is unverified",
             rc == EXIT_FAIL
             and _git(repo_n, "rev-parse", "HEAD")[1].strip() == head_before)

        # --reverify refusals, before the happy path
        rc = reverify_driver(repo_n, cseq, st,
                              "deploy/evidence/operator-nope.md",
                              engine=FakeEngine(), out=silent)
        case("--reverify refuses without a valid authorization (pre-dispatch)",
             rc == EXIT_FAIL)
        eng_probe = FakeEngine()
        rc = reverify_driver(repo_n, cseq, os.path.join(repo_n, "nope"), good,
                              engine=eng_probe, out=silent)
        case("--reverify refuses an unusable staging dir, nothing dispatched",
             rc == EXIT_FAIL and eng_probe.verify_calls == 0)
        rc = reverify_driver(repo_n, 9999, st, good, engine=FakeEngine(),
                              out=silent)
        case("--reverify refuses an unknown seq", rc == EXIT_FAIL)
        rc = reverify_driver(repo_n, cseq + 1, st, good, engine=FakeEngine(),
                              out=silent)
        case("--reverify refuses a seq that is not a compile run",
             rc == EXIT_FAIL)

        # a re-fire that ALSO fails transport: no revert, still blocking
        rc = reverify_driver(repo_n, cseq, st, good,
                              engine=FakeEngine(verify="bridge-error"),
                              out=silent)
        case("--reverify that fails transport again exits 1 and never reverts",
             rc == EXIT_FAIL
             and not any(r.get("driver_revert")
                         for r in load_journal(repo_n).values()))
        case("...and the run is still non-terminal",
             reconcile_state(repo_n)["blocked"] is True)

        # the happy path
        before_n = len(load_journal(repo_n))
        rc = reverify_driver(repo_n, cseq, st, good,
                              engine=FakeEngine(verify="confirm"), out=silent)
        recs_n = load_journal(repo_n)
        case("--reverify with healthy transport exits 0 and appends a fresh "
             "verify record", rc == EXIT_OK and len(recs_n) > before_n)
        case("...which terminalizes the run (reconciliation unblocked)",
             reconcile_state(repo_n)["blocked"] is False)
        case("...without absorbing or reverting anything",
             not any(r.get("driver_revert") for r in recs_n.values())
             and len([r for r in recs_n.values()
                      if r.get("run_type") == "compile"]) == 1)
        case("journal chain intact across the whole reverify sequence",
             _core().check_chain(repo_n) == len(recs_n))
        rc = reverify_driver(repo_n, cseq, st, good, engine=eng_probe,
                              out=silent)
        case("--reverify on an already-terminal run is a no-op (exit 0, no "
             "dispatch)", rc == EXIT_OK and eng_probe.verify_calls == 0)
        rc = run_driver(repo_n, st, good, engine=FakeEngine(),
                         sensors=quiet_sensors, out=silent)
        case("the branch accepts new runs again after --reverify",
             rc == EXIT_OK)
    finally:
        shutil.rmtree(repo_n, ignore_errors=True)

    repo_n2 = make_repo("cdrv-reverify-reverted-")
    try:
        st = make_staging(repo_n2)
        good = make_grant(repo_n2)
        _git(repo_n2, "add", "-A")
        _git(repo_n2, "commit", "-qm", "fixtures")
        run_driver(repo_n2, st, good, engine=FakeEngine(verify="raise"),
                    sensors=quiet_sensors, out=silent)
        eng_probe2 = FakeEngine()
        rc = reverify_driver(repo_n2, 1, st, good, engine=eng_probe2,
                              out=silent)
        case("--reverify refuses a run that was already reverted (its "
             "absorption no longer stands)",
             rc == EXIT_FAIL and eng_probe2.verify_calls == 0)
    finally:
        shutil.rmtree(repo_n2, ignore_errors=True)

    # ---------- N3. --revert: operator adjudication of a non-confirm verdict
    # (v3.0-local-5 second finding: the skill said "fix the view text and
    # re-run the verify leg" but no CLI mode performed it -- the first live
    # all-rejected run ended with hand-edited, unverified views. --revert is
    # the shipped adjudication: revert the run, correct the STAGED answers,
    # re-run --run.)
    repo_p = make_repo("cdrv-revert-")
    try:
        st = make_staging(repo_p)
        good = make_grant(repo_p)
        _git(repo_p, "add", "-A")
        _git(repo_p, "commit", "-qm", "fixtures")
        before_p = open(os.path.join(repo_p, "wiki", "a.md"),
                        encoding="utf-8").read()
        cseq_p = plant_seq103(repo_p, "rejected")
        case("a completed 'rejected' verdict is terminal (this is the state "
             "--revert exists to adjudicate)",
             reconcile_state(repo_p)["blocked"] is False)
        case("--revert refuses an unknown seq",
             execute_revert(repo_p, 9999, out=silent) == EXIT_FAIL)
        case("--revert refuses a seq that is not a compile run",
             execute_revert(repo_p, cseq_p + 1, out=silent) == EXIT_FAIL)
        junk = os.path.join(repo_p, "dirty.txt")
        with open(junk, "w", encoding="utf-8") as fh:
            fh.write("uncommitted\n")
        case("--revert refuses a dirty worktree (the revert must land alone)",
             execute_revert(repo_p, cseq_p, out=silent) == EXIT_FAIL)
        os.remove(junk)
        rc = execute_revert(repo_p, cseq_p, out=silent)
        recs_p = load_journal(repo_p)
        case("--revert over a rejected run exits 0 and restores the view",
             rc == EXIT_OK
             and open(os.path.join(repo_p, "wiki", "a.md"),
                      encoding="utf-8").read() == before_p)
        case("--revert journals a 'reverted' driver-revert record, chain "
             "intact",
             any(isinstance(r.get("driver_revert"), dict)
                 and r["driver_revert"].get("reverts_seq") == cseq_p
                 and r["driver_revert"].get("status") == "reverted"
                 for r in recs_p.values())
             and _core().check_chain(repo_p) == len(recs_p))
        case("--revert keeps the run's own journal record visible "
             "(append-only survives the revert)",
             os.path.isfile(os.path.join(repo_p, "receipts", "journal",
                                         "%d.json" % cseq_p)))
        case("--revert refuses an already-reverted run",
             execute_revert(repo_p, cseq_p, out=silent) == EXIT_FAIL)
        case("reconciliation stays clean after --revert",
             reconcile_state(repo_p)["blocked"] is False)
        rc = run_driver(repo_p, st, good, engine=FakeEngine(),
                        sensors=quiet_sensors, out=silent)
        case("the branch accepts the corrected re-absorb after --revert",
             rc == EXIT_OK)
    finally:
        shutil.rmtree(repo_p, ignore_errors=True)

    repo_q = make_repo("cdrv-revert-confirmed-")
    try:
        make_staging(repo_q)
        make_grant(repo_q)
        _git(repo_q, "add", "-A")
        _git(repo_q, "commit", "-qm", "fixtures")
        cseq_q = plant_seq103(repo_q, "confirm")
        rc = execute_revert(repo_q, cseq_q, out=silent)
        case("--revert refuses a fully-confirmed run (nothing to adjudicate)",
             rc == EXIT_FAIL
             and not any(r.get("driver_revert")
                         for r in load_journal(repo_q).values()))
    finally:
        shutil.rmtree(repo_q, ignore_errors=True)

    # ------------------------------------------------ O. --reverify argv
    try:
        parse_args(["--reverify", "--root", "."])
        case("--reverify without seq/staging/authorization refuses", False)
    except UsageError as e:
        case("--reverify without seq/staging/authorization refuses",
             "--seq" in str(e) and "--authorization" in str(e), str(e))
    try:
        parse_args(["--reverify", "--root", ".", "--seq", "abc",
                    "--staging", "s", "--authorization", "a"])
        case("--reverify with a non-integer --seq refuses", False)
    except UsageError as e:
        case("--reverify with a non-integer --seq refuses", "integer" in str(e))
    p = parse_args(["--reverify", "--root", "r", "--seq", "103",
                    "--staging", "s", "--authorization", "a"])
    case("--reverify argv parses with an int seq",
         p["mode"] == "reverify" and p["seq"] == 103, p)
    try:
        parse_args(["--reverify", "--root", ".", "--seq", "103",
                    "--staging", "s", "--authorization", "a", "--no-verify"])
        case("--reverify cannot smuggle --no-verify either", False)
    except UsageError:
        case("--reverify cannot smuggle --no-verify either", True)

    # ------------------------------------------------ O2. --revert argv
    try:
        parse_args(["--revert", "--root", "."])
        case("--revert without --seq refuses", False)
    except UsageError as e:
        case("--revert without --seq refuses", "--seq" in str(e), str(e))
    try:
        parse_args(["--revert", "--root", ".", "--seq", "abc"])
        case("--revert with a non-integer --seq refuses", False)
    except UsageError as e:
        case("--revert with a non-integer --seq refuses", "integer" in str(e))
    p = parse_args(["--revert", "--root", "r", "--seq", "7",
                    "--reason", "operator adjudication"])
    case("--revert argv parses with an int seq and optional --reason",
         p["mode"] == "revert" and p["seq"] == 7
         and p["reason"] == "operator adjudication", p)
    try:
        parse_args(["--revert", "--root", ".", "--seq", "7", "--no-verify"])
        case("--revert cannot smuggle --no-verify either", False)
    except UsageError:
        case("--revert cannot smuggle --no-verify either", True)

    # ------------------- P. codex resolution + bridge probe (backlog v3.0-68)
    NPM_TAIL = os.path.join(*_NPM_VENDOR_TAIL)
    APPDATA_EXE = os.path.join("C:\\ad", NPM_TAIL)
    KF_EXE = os.path.join("C:\\kf\\Roaming", NPM_TAIL)
    HOME_EXE = os.path.join("C:\\home\\u", "AppData", "Roaming", NPM_TAIL)
    NATIVE_EXE = ("C:\\home\\u\\AppData\\Local\\Programs\\OpenAI\\Codex\\bin\\"
                  "codex.EXE")

    def versions_runner(table, default="codex-cli 0.144.1"):
        """--version stand-in: {path: (rc, stdout)}; anything unlisted reports
        `default`. The self-test never spawns a real codex."""
        def run(args, timeout):
            rc, text = table.get(args[0], (0, default))
            if isinstance(text, Exception):
                raise text
            return rc, text, ""
        return run

    def resolver_probe(env, present, which_result=None, homedir="C:\\home\\u",
                       kf=None, versions=None):
        return resolve_codex_bin(
            env=env, homedir=homedir,
            isfile=lambda p: p in present,
            which=lambda _n: which_result,
            known_folder=lambda: kf,
            runner=versions_runner(versions or {}))

    case("version parse: 'codex-cli 0.144.1' -> (0, 144, 1)",
         parse_codex_version("codex-cli 0.144.1") == (0, 144, 1))
    case("version parse: a two-part version tolerates a missing patch",
         parse_codex_version("codex 0.144") == (0, 144, 0))
    case("version parse: unparseable output -> None",
         parse_codex_version("some banner with no version") is None)
    case("version floor is the documented 0.144", CODEX_MIN_VERSION == (0, 144))

    b, chain = resolver_probe({"CODEX_BIN": "C:\\pin\\codex.exe"},
                              {"C:\\pin\\codex.exe"})
    case("resolve: CODEX_BIN env wins when it points at a real, current binary",
         b == "C:\\pin\\codex.exe" and chain[0][0] == "CODEX_BIN env", chain)
    b, chain = resolver_probe({"APPDATA": "C:\\ad"}, {APPDATA_EXE})
    case("resolve: APPDATA-derived npm exe is preferred over PATH",
         b == APPDATA_EXE, chain)
    b, chain = resolver_probe({}, {KF_EXE}, kf="C:\\kf\\Roaming")
    case("resolve: APPDATA scrubbed -> the SHGetKnownFolderPath candidate "
         "finds the npm exe (v3.0-68 round 4)", b == KF_EXE, chain)
    case("...and the known-folder candidate is a named row in the chain",
         any("known-folder" in row[0] for row in chain), chain)
    b, chain = resolver_probe({}, {HOME_EXE}, kf=None)
    case("resolve: with no known-folder either, expanduser still gets a turn",
         b == HOME_EXE, chain)
    b, chain = resolver_probe({}, {NATIVE_EXE}, which_result=NATIVE_EXE)
    case("resolve: falls back to where/which when no npm exe exists",
         b == NATIVE_EXE, chain)

    # THE ROUND-4 CASE: everything scrubbed, `which` finds the GATED native
    # 0.142.3. Existence alone used to accept it and export it as CODEX_BIN.
    b, chain = resolver_probe(
        {}, {NATIVE_EXE}, which_result=NATIVE_EXE, kf=None,
        versions={NATIVE_EXE: (0, "codex-cli 0.142.3\n")})
    case("resolve: a candidate BELOW the version floor is rejected, not "
         "returned (the gated native 0.142.3)", b is None, chain)
    case("...and its chain row names the version and why it lost",
         any(row[1] == NATIVE_EXE and not row[2] and "0.142.3" in row[3]
             and "BELOW" in row[3] for row in chain), chain)
    # ...and the walk CONTINUES past a floor failure to a good candidate
    b, chain = resolver_probe(
        {"CODEX_BIN": NATIVE_EXE}, {NATIVE_EXE, HOME_EXE},
        which_result=NATIVE_EXE, kf=None,
        versions={NATIVE_EXE: (0, "codex-cli 0.142.3\n"),
                  HOME_EXE: (0, "codex-cli 0.144.1\n")})
    case("resolve: a below-floor candidate does not stop the walk -- the next "
         "acceptable candidate wins", b == HOME_EXE, chain)
    case("...and the accepted row records the version it accepted",
         any(row[1] == HOME_EXE and row[2] and "0.144.1" in row[3]
             for row in chain), chain)
    b, chain = resolver_probe(
        {}, {HOME_EXE}, versions={HOME_EXE: (0, "no version here\n")})
    case("resolve: unparseable --version is a REJECTION (fail-closed, never an "
         "assumed pass)", b is None, chain)
    case("...naming it as unparseable in the chain",
         any("unparseable" in row[3] for row in chain), chain)
    b, chain = resolver_probe(
        {}, {HOME_EXE}, versions={HOME_EXE: (1, "boom")})
    case("resolve: a candidate whose --version exits nonzero is rejected",
         b is None, chain)
    b, chain = resolver_probe(
        {}, {HOME_EXE}, versions={HOME_EXE: (0, OSError("spawn ENOENT"))})
    case("resolve: a candidate that cannot be spawned is rejected, never a "
         "crash", b is None, chain)
    b, chain = resolver_probe({}, set(), which_result=None)
    case("resolve: nothing found -> None (never a bare name the probe would "
         "'succeed' on)", b is None and len(chain) == 5, chain)
    case("...and every candidate is still reported, skipped ones included",
         all(len(row) == 4 for row in chain)
         and any("skipped" in row[3] for row in chain), chain)
    b, chain = resolver_probe({"CODEX_BIN": "C:\\pin\\gone.exe"}, {HOME_EXE})
    case("resolve: a CODEX_BIN pointing at a missing file does not win",
         b == HOME_EXE
         and any("not on disk" in row[3] for row in chain), chain)

    repo_p = make_repo("cdrv-probe-")
    try:
        os.makedirs(os.path.join(repo_p, ".claude", "skills", "bridge"))
        write(os.path.join(repo_p, ".claude", "skills", "bridge",
                           "verify-cli.js"), "// fixture\n")
        env_p = {}
        ok, detail = probe_bridge(
            repo_p, out=silent, env=env_p,
            resolver=lambda **kw: (HOME_EXE, [
                ("expanduser npm exe", HOME_EXE, True, "codex-cli 0.144.1")]))
        case("probe: success reports the accepted version",
             ok and "0.144.1" in detail, detail)
        case("probe: success EXPORTS the resolved binary as CODEX_BIN so the "
             "child legs cannot resolve something else",
             env_p.get("CODEX_BIN") == HOME_EXE, env_p)
        lines = []
        ok, _d = probe_bridge(
            repo_p, out=lines.append, env={},
            resolver=lambda **kw: (None, [
                ("CODEX_BIN env", "(CODEX_BIN unset)", False, "skipped"),
                ("APPDATA-derived npm exe",
                 "(APPDATA unset -- candidate skipped)", False, "skipped"),
                ("known-folder npm exe (SHGetKnownFolderPath)",
                 "(known-folder syscall unavailable)", False, "skipped"),
                ("expanduser npm exe", HOME_EXE, False, "not on disk"),
                ("where/which codex", NATIVE_EXE, False,
                 "codex-cli 0.142.3 is BELOW the 0.144 floor -- "
                 "version-gated by the API")]))
        case("probe: nothing meets the floor -> refusal", ok is False)
        blob = "\n".join(lines)
        case("...and the refusal shows the whole chain WITH versions, so the "
             "gated native install is visible by name",
             "0.142.3" in blob and "BELOW" in blob and "APPDATA" in blob
             and "known-folder" in blob and NATIVE_EXE in blob, lines)
        case("...and it names the floor in its remedy line",
             "0.144" in blob and "CODEX_BIN" in blob, lines)
        ok, _d = probe_bridge(
            os.path.join(repo_p, "no-bridge-here"), out=silent, env={},
            resolver=lambda **kw: (HOME_EXE, []))
        case("probe: a missing verify-cli.js -> refusal", ok is False)

        # end-to-end: a failing probe refuses PRE-WRITE
        st = make_staging(repo_p)
        good = make_grant(repo_p)
        _git(repo_p, "add", "-A")
        _git(repo_p, "commit", "-qm", "fixtures")
        head_before = _git(repo_p, "rev-parse", "HEAD")[1].strip()
        eng_probe3 = FakeEngine()
        rc = run_driver(repo_p, st, good, engine=eng_probe3,
                        sensors=quiet_sensors, out=silent,
                        probe=lambda repo, out=print, **kw: (False, "no codex"))
        case("--run refuses PRE-WRITE when the bridge probe fails",
             rc == EXIT_FAIL and eng_probe3.verify_calls == 0
             and not os.path.isdir(os.path.join(repo_p, "receipts", "journal"))
             and _git(repo_p, "rev-parse", "HEAD")[1].strip() == head_before
             and _worktree_clean(repo_p))
        cseq = plant_seq103(repo_p)
        eng_probe4 = FakeEngine()
        rc = reverify_driver(repo_p, cseq, st, good, engine=eng_probe4,
                             out=silent,
                             probe=lambda repo, out=print, **kw: (False, "x"))
        case("--reverify refuses when the bridge probe fails, nothing "
             "dispatched", rc == EXIT_FAIL and eng_probe4.verify_calls == 0)
    finally:
        shutil.rmtree(repo_p, ignore_errors=True)

    # ---- Q. source-level guards on the repo-local bridge server (JS, so these
    # are assertions over the shipped file, not executions of it)
    js = os.path.join(os.path.dirname(_HERE), ".claude", "skills", "bridge",
                      "codex-verify-server.js")
    if os.path.isfile(js):
        jstext = open(js, encoding="utf-8").read()
        case("bridge server: resolveCodexBin has a homedir-derived candidate "
             "(survives a scrubbed APPDATA)",
             "os.homedir()" in jstext and "AppData" in jstext
             and "npmVendorExeUnder" in jstext)
        case("bridge server: a version-gated failure names the resolved binary "
             "and the fix",
             "requires a newer version of Codex" in jstext
             and "resolved ' + CODEX_BIN" in jstext
             and "codex >= 0.144" in jstext)
        case("bridge server: carries the note that the user-global copy is "
             "owed the same fix",
             "~/.claude/skills/bridge" in jstext)
        case("bridge server: has the same 0.144 version floor as this module",
             "CODEX_MIN_VERSION = [0, 144]" in jstext
             and CODEX_MIN_VERSION == (0, 144))
        case("bridge server: version-checks candidates and keeps walking on a "
             "floor failure (not a single check after resolution)",
             "codexVersionOk" in jstext
             and "if (codexVersionOk(cand)) return cand;" in jstext)
        case("bridge server: keeps the bare-name last resort (degrades no "
             "worse than before the floor)",
             "return 'codex';" in jstext)
        case("both resolvers carry the must-not-drift note",
             "MUST NOT DRIFT" in jstext
             and "MUST NOT DRIFT" in open(
                 os.path.join(_HERE, "compile-driver.py"),
                 encoding="utf-8").read())

    # ---- R. source-level guard on main()'s own dispatch: the CLI must route to
    # the module-level execute_* functions, never to self-test-local helpers
    # (regression 2026-07-28: main() briefly called the self_test-scoped
    # run_driver/reverify_driver -> NameError on every real --run; self-tests
    # stayed green because they call execute_* directly).
    own = open(os.path.join(_HERE, "compile-driver.py"), encoding="utf-8").read()
    main_src = own[own.index("def main("):]
    case("main() dispatches --run to execute_run",
         "return execute_run(" in main_src)
    case("main() dispatches --reverify to execute_reverify",
         "return execute_reverify(" in main_src)
    case("main() dispatches --reconcile to execute_reconcile",
         "return execute_reconcile(" in main_src)
    case("main() dispatches --revert to execute_revert",
         "return execute_revert(" in main_src)

    # ------------------------------------------------------- K. exit mapping
    case("main() with no arguments is inconclusive (exit 2)",
         main([]) == EXIT_INCONCLUSIVE)
    case("main() with --no-verify exits 1 (usage refusal, nothing run)",
         main(["--run", "--root", ".", "--staging", "s",
               "--authorization", "a", "--no-verify"]) == EXIT_FAIL)

    print("compile-driver self-test: %s (%d/%d)"
          % ("PASS" if failed == 0 else "FAIL", total - failed, total))
    return 0 if failed == 0 else 1


# --------------------------------------------------------------- CLI
def main(argv):
    args = argv[1:] if argv and str(argv[0]).endswith(".py") else list(argv)
    try:
        opts = parse_args(args)
    except UsageError as e:
        print("REFUSED: %s" % e)
        return EXIT_FAIL
    if opts["mode"] == "self-test":
        return self_test()
    if opts["mode"] == "run":
        return execute_run(opts["root"], opts["staging"],
                           opts["authorization"], sections=opts["sections"])
    if opts["mode"] == "reconcile":
        return execute_reconcile(opts["root"])
    if opts["mode"] == "reverify":
        return execute_reverify(opts["root"], opts["seq"], opts["staging"],
                                opts["authorization"])
    if opts["mode"] == "revert":
        return execute_revert(opts["root"], opts["seq"],
                              reason=opts["reason"])
    print(USAGE)
    return EXIT_INCONCLUSIVE


if __name__ == "__main__":
    sys.exit(main(sys.argv))
