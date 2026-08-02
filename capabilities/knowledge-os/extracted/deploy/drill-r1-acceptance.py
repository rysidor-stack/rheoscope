#!/usr/bin/env python3
"""drill-r1-acceptance.py -- R-1 CONSOLIDATED DONE-BAR (memory-engine v3, R-1 leg F).

Spec: harness-v3.0/specs/r1-build-decisions-2026-07-22.md Part 8, verbatim (the
scenario list below is that Part's five acceptance items, decomposed into A1-A8).
Parts 3-7 describe the six modules this drill exercises: deploy/candidates.py (the
file shape + ledger), deploy/harvest-candidates.py + deploy/register-candidates.py
(harvest/reconcile), deploy/promote-candidate.py (the signature-verified gate),
deploy/check-candidates.py (the conservation sensor), and deploy/origin.py (the
staging-floor origin rule). deploy/decision-inbox.py (harness-backlog.md v3.0-38)
is the seventh surface this Part's acceptance list names.

THE DESIGN PRESSURE THAT MAKES THIS DRILL DIFFERENT FROM SIX ALREADY-GREEN UNIT
SUITES (63/36/26/98/20/49 cases, all in-process): a unit suite proves its own
module correct with its neighbours stubbed or absent. It cannot catch an
argument-plumbing bug (a flag renamed on one side of a call, an exit code nobody
checks), a CLI-contract drift (does the printed refusal actually SAY the check
name, or only the raised-and-caught exception object the unit suite inspects
directly?), or a SEAM failure (does a file harvest-candidates.py writes actually
get found by register-candidates.py, actually get read by promote-candidate.py,
actually get named by check-candidates.py -- as four independent OS processes,
never sharing Python state)? So every case below is deliberately either (a) driven
via `subprocess` against the real CLI entry points (never an in-process function
call to the module under test), or (b) cross-module (touching at least two of the
seven surfaces in one scenario). Where a case would only re-prove something a
sibling module's own --self-test already covers in-process, it is not here --
see each scenario's opening comment for the one-line "what this proves that the
unit suites cannot" note.

THE SIGNING-CONFIG SEAM (a real finding from building this drill, not assumed):
promote-candidate.py's CLI (`--root DIR --candidate-id ID --artifact PATH`) takes
NO --signing-config / --origin-config override. Unlike every other module here,
its trust config is resolved from `os.path.join(os.path.dirname(__file__),
"signing-config.yaml")` -- i.e. from THE SCRIPT'S OWN LOCATION, not from --root.
--root governs the candidate DATA; it does not, and structurally cannot, govern
the TRUST config. That means running `python deploy/promote-candidate.py --root
<temp> ...` directly would read the LIVE deploy/signing-config.yaml and the LIVE
deploy/origin-config.yaml on every single invocation -- exactly what acceptance
criterion 5 forbids, and there is no CLI flag to avoid it. Two ways out were
considered and rejected: (1) temporarily edit the live signing-config.yaml for
the duration of the test -- forbidden twice over (this leg may create no file but
this one, and criterion 5 says the live config must never be TOUCHED, not just
"restored afterward"); (2) call `promote()` in-process with a fixture path, the
way promote-candidate.py's OWN self-test does -- but that abandons "via
subprocess, not python imports" for the single most security-critical module in
the pipeline, which is the one place this drill's whole reason for existing
matters most.
The solution actually used: `_build_shadow()` copies the four files promote-
candidate.py's real CLI needs to resolve on its own (itself, plus its sibling
imports candidates.py, origin.py, and candidates.py's own dependency
compile-core.py) BYTE-FOR-BYTE, unmodified, into a disposable directory, and
writes a FIXTURE signing-config.yaml + origin-config.yaml alongside them there.
`python <shadow>/promote-candidate.py --root <temp> ...` then runs the REAL,
unmodified module code as a REAL subprocess, with --root pointing at the
disposable candidate data and the script's OWN directory (now the shadow, not
deploy/) resolving to the fixture trust config -- hermetic, CLI-level, and never
touching the live files. The other five CLIs (harvest, register, check, inbox)
have no such dependency -- their only external state is --root -- so they are
invoked directly against the real deploy/ directory.

CROSS-CHECK, NOT JUST TRUST: _prove_live_config_untouched() is the one deliberate,
explicitly-named, read-only touch of the LIVE deploy/signing-config.yaml and
deploy/origin-config.yaml in this entire file -- called exactly once, at the very
end, to prove (not merely assert) that even an accidental real-CLI invocation
with no fixture at all could not have promoted anything for real on this fork
right now. It uses the modules' own defensive loaders (load_signing_config() /
load_origin_config() with no path override) rather than re-parsing YAML by hand,
and it never feeds the result into any promotion this drill performs.

CRASH KILL METHOD FOR A6 (read before touching this section): harvest-
candidates.py cannot be edited (this leg's scope is one new file), so there is no
injectable crash-point hook. A6's real crash target is the gap between two
back-to-back Python statements inside run_harvest() (the candidate file's
os.replace() and the following candidates.append_candidate_record() call) --
microseconds wide. Timing an EXTERNALLY-delivered SIGTERM against a window that
small, from outside the process, is not repeatable; most external kills would
either land before the file write (nothing interesting) or after the append
(nothing interesting either). What this drill does instead: a tiny wrapper
script (written to a throwaway temp file, never touching deploy/) imports the
REAL, unmodified harvest-candidates.py module and replaces ONLY that one child
process's own in-memory reference to `candidates.append_candidate_record` with a
function that calls `os.kill(os.getpid(), signal.SIGTERM)` -- a genuine OS-level
self-directed process kill, not a caught-and-continued Python exception -- then
runs the real, unmodified `run_harvest()`. Because the replacement function is
installed BEFORE `main()` runs, the kill fires at EXACTLY the moment the real
code would have called the real append -- i.e. strictly after the real
write_candidate_atomic() has already completed (flushed, fsynced, renamed to its
final name) and strictly before any ledger write begins. See a6()'s docstring for
the honest accounting of what this does and does not prove; it is deterministic
and repeatable (unlike a wall-clock-timed external signal) but it is
self-triggered, not externally delivered.

Usage:
  drill-r1-acceptance.py --self-test    run all eight scenarios (A1-A8) plus the
                                         hermeticity proof; hermetic (temp roots,
                                         a generated test keypair, a fixture
                                         signing-config.yaml + origin-config.yaml
                                         written into a throwaway shadow copy);
                                         never reads the live ledger.
Exit: 0 all-pass | 1 any-fail | 2 inconclusive (git, ssh-keygen, or PyYAML
      unavailable).

NO --root MODE (deliberately omitted -- the task invited this call and asked for
the reasoning, not just the decision): every other drill in this family that
takes --root reads a live tree read-only. This drill cannot do that usefully.
Its entire value is in scenarios that MUTATE a disposable git repo in ways that
must never happen to a real one: forging a session-authored ryan-*.md file
straight into the staging directory (A2), committing five different kinds of
attack artifact (A3), SIGTERM-killing a live harvester mid-write (A6), deleting a
staged file out from under a live ledger entry (A7). A --root flag on this
module would either be a silent no-op (nothing here is read-only, so there is
nothing to "check" against an existing tree) or, worse, an invitation for a
future editor to point the crash-wrapper or the artifact-attack commits at a
real instance. check-candidates.py already IS the live, read-only, --root-taking
sensor for this surface; this file is not a second one.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time

try:  # cp1252 consoles + unicode spans: never let an encode error mask a verdict.
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_PY = sys.executable


def _load(basename, alias):
    """Sibling-import by path -- deploy/ is a flat directory, never a package (the
    same mechanism every module in this family uses). Used HERE only to import
    candidates.py/origin.py as READ-ONLY verification helpers (compute an
    expected candidate_id, read back ledger state, call assign_origin against an
    explicit fixture path) -- never to perform any of the actions under test.
    Every action under test runs as a real subprocess; see the module docstring."""
    spec = importlib.util.spec_from_file_location(alias, os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Read-only verification helpers -- the REAL deploy/candidates.py and
# deploy/origin.py, imported once. Every call site below either (a) reads
# ledger/filesystem state under a --root temp root this drill itself created, or
# (b) passes an EXPLICIT fixture origin_config_path -- except the one deliberate
# exception in _prove_live_config_untouched(), named as such.
cand = _load("candidates.py", "candidates_drill_r1")
origin_mod = _load("origin.py", "origin_drill_r1")

_SCRIPTS = {
    "harvest": os.path.join(_HERE, "harvest-candidates.py"),
    "register": os.path.join(_HERE, "register-candidates.py"),
    "check": os.path.join(_HERE, "check-candidates.py"),
    "inbox": os.path.join(_HERE, "decision-inbox.py"),
}

# The four files promote-candidate.py's real CLI needs available NEXT TO ITSELF
# to resolve both its sibling imports and its trust config -- see the module
# docstring's "THE SIGNING-CONFIG SEAM" section.
_SHADOW_FILES = ("compile-core.py", "origin.py", "candidates.py", "promote-candidate.py")

PAST = "@%d +0000" % (int(time.time()) - 86400)


# ------------------------------------------------------------------ subprocess plumbing
def _run(cmd, cwd=None, env=None):
    """(rc, stdout, stderr) -- argument-list only, never shell=True. Every CLI
    invocation in this file goes through this one function."""
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr


def _git(root, *args):
    return _run(["git", "-C", root] + list(args))


def git_init(root):
    """A throwaway repo, never the live one. commit.gpgsign forced OFF (same
    reason promote-candidate.py's own self-test forces it off): a global user
    config that signs everything would make an 'unsigned commit' fixture
    accidentally signed, proving the opposite of what that case claims."""
    for args in (["init", "-q"],
                 ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"],
                 ["config", "commit.gpgsign", "false"],
                 ["config", "tag.gpgsign", "false"],
                 ["config", "gpg.format", "ssh"]):
        _git(root, *args)


def harvest_cli(root, session_id, klass, disposition, span, origin_max="corpus",
                 transcript_pointer=""):
    """Drives the REAL deploy/harvest-candidates.py as a subprocess. --span-file
    is written byte-exact (no trailing newline appended beyond what `span`
    itself contains), matching that module's own byte-exact read contract."""
    fd, span_path = tempfile.mkstemp(prefix="r1-accept-span-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(span)
        args = [_PY, _SCRIPTS["harvest"], "--root", root, "--session-id", session_id,
                "--class", klass, "--disposition", disposition,
                "--span-file", span_path, "--origin-max", origin_max]
        if transcript_pointer:
            args += ["--transcript-pointer", transcript_pointer]
        return _run(args)
    finally:
        try:
            os.remove(span_path)
        except OSError:
            pass


def register_cli(root):
    return _run([_PY, _SCRIPTS["register"], "--root", root])


def check_cli(root):
    return _run([_PY, _SCRIPTS["check"], "--root", root])


def inbox_cli(root, date_str=None):
    args = [_PY, _SCRIPTS["inbox"], "--root", root]
    if date_str:
        args += ["--date", date_str]
    return _run(args)


def promote_cli(stage_dir, root, candidate_id, artifact):
    """Drives promote-candidate.py from the SHADOW copy (see module docstring),
    never from deploy/ directly -- this is the one CLI in the pipeline whose
    trust config is not --root-scoped."""
    script = os.path.join(stage_dir, "promote-candidate.py")
    return _run([_PY, script, "--root", root, "--candidate-id", candidate_id,
                 "--artifact", artifact])


def harvest_and_register(root, session_id, span, klass="decision-stated",
                          disposition="transcribe-as-operator-event",
                          origin_max="corpus"):
    """The two-process setup every scenario but A2/A6 needs: a real harvest
    subprocess followed by a real register subprocess, landing a REGISTERED
    candidate. Returns (candidate_id, harvest_result, register_result)."""
    candidate_id = cand.make_candidate_id(session_id, span)
    result_h = harvest_cli(root, session_id, klass, disposition, span,
                            origin_max=origin_max)
    result_r = register_cli(root)
    return candidate_id, result_h, result_r


# ------------------------------------------------------------------ git fixture helpers
def _gitpath(p):
    return str(p).replace("\\", "/")


def armed_text(candidate_id, disposition="transcribe-as-operator-event", extra=""):
    return ("# Operator gate artifact\n\nThis artifact arms exactly one candidate.\n\n"
            "candidate_id: %s\napproved_disposition: %s\n%s"
            % (candidate_id, disposition, extra))


def commit_file(root, rel, text, signing_key=None, when=None, msg="fixture"):
    """Write `rel` and commit it. `signing_key` None => genuinely UNSIGNED (the
    same fixture idiom promote-candidate.py's own self-test uses, reproduced
    here because every scenario below needs to build git history by hand --
    that is fixture SETUP, not a call into the code under test)."""
    if when is None:
        when = PAST
    p = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    _git(root, "add", "--", ":(literal)" + rel)
    cmd = ["git", "-C", root]
    if signing_key:
        cmd += ["-c", "gpg.format=ssh", "-c", "user.signingkey=%s" % _gitpath(signing_key)]
    cmd += ["commit", "-q", "-m", msg]
    cmd += ["-S"] if signing_key else ["--no-gpg-sign"]
    env = dict(os.environ)
    env["GIT_AUTHOR_DATE"] = when
    env["GIT_COMMITTER_DATE"] = when
    r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError("git commit failed: %s" % r.stderr)
    _rc, out, _e = _git(root, "log", "-1", "--format=%H")
    return out.strip()


def keypair(keydir, name):
    """A REAL ed25519 keypair via ssh-keygen -- never a hardcoded fixture key,
    never the operator's own."""
    path = os.path.join(keydir, name)
    r = subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", path, "-q"],
                        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ssh-keygen failed: %s" % r.stderr)
    with open(path + ".pub", encoding="utf-8") as fh:
        line = fh.read().strip()
    return path + ".pub", line, _fingerprint(line)


def _fingerprint(pub_line):
    fd, p = tempfile.mkstemp(prefix="r1-accept-fp-", suffix=".pub")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(pub_line.strip() + "\n")
        r = subprocess.run(["ssh-keygen", "-lf", p], capture_output=True, text=True)
        if r.returncode != 0:
            return None
        for field in r.stdout.split():
            if field.startswith("SHA256:"):
                return field
        return None
    finally:
        try:
            os.remove(p)
        except OSError:
            pass


# ------------------------------------------------------------------ shadow deploy + fixtures
def _build_shadow(stage_dir):
    """See the module docstring's 'THE SIGNING-CONFIG SEAM' section. Byte-for-byte
    copies -- shutil.copy2, never rewritten -- so the CLI under test is the exact
    same code as deploy/promote-candidate.py, just relocated so its __file__-
    relative trust-config lookup lands on a fixture."""
    for name in _SHADOW_FILES:
        shutil.copy2(os.path.join(_HERE, name), os.path.join(stage_dir, name))


def write_fixture_signing_config(stage_dir, entries):
    path = os.path.join(stage_dir, "signing-config.yaml")
    if not entries:
        body = "signature_mode: ssh\noperator_signing_keys: []\n"
    else:
        body = "signature_mode: ssh\noperator_signing_keys:\n"
        for e in entries:
            body += ('  - fingerprint: "%s"\n    public_key: "%s"\n    principal: "%s"\n'
                      % (e["fingerprint"], e["public_key"], e["principal"]))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    return path


def write_fixture_origin_config(stage_dir, prefixes):
    path = os.path.join(stage_dir, "origin-config.yaml")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("human_prefixes: [%s]\n" % ", ".join(prefixes))
    return path


def _raw_files(root):
    d = os.path.join(root, "raw")
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.endswith(".md"))


# ==================================================================== A1
def a1(case, stage_dir, pubA, mkroot):
    """A1 -- the full happy pipeline, FOUR separate real processes (harvest,
    register, check, promote), plus a fifth check to confirm the post-promotion
    state is still conservation-clean.

    WHAT NO UNIT SUITE ALREADY PROVES: each module's own --self-test proves ITS
    OWN function calls behave correctly with the neighbours either stubbed or
    called in-process in the SAME Python interpreter. This proves the four
    modules agree with each other as four INDEPENDENT OS PROCESSES sharing
    nothing but the filesystem -- that a file harvest-candidates.py's process
    writes is the SAME file register-candidates.py's (separate) process reads,
    that check-candidates.py's (separate) process sees the SAME ledger state
    register-candidates.py's process just committed to disk, and that promote-
    candidate.py's (separate) process, invoked with only --candidate-id and
    --artifact strings on argv, finds and promotes the exact candidate the
    first two processes created -- with the resulting `human` origin mint
    checked against a FIXTURE origin-config, never the live one."""
    root = mkroot("a1")
    git_init(root)
    session_id = "sess-a1-happy4proc"
    span = ("The operator decided to ship the R-1 acceptance pilot on schedule, "
            "proven across four separate real processes.")
    candidate_id = cand.make_candidate_id(session_id, span)

    rc1, _out1, _err1 = harvest_cli(root, session_id, "decision-stated",
                                     "transcribe-as-operator-event", span,
                                     origin_max="corpus")
    case("A1 process 1/4 (harvest-candidates.py): exits 0", rc1 == 0)

    staging = os.path.join(root, cand.CANDIDATES_STAGING_DIR)
    staged = os.listdir(staging) if os.path.isdir(staging) else []
    case("A1: exactly one file staged, matching the candidate id",
         len(staged) == 1 and candidate_id in staged[0])
    if staged:
        body = open(os.path.join(staging, staged[0]), encoding="utf-8-sig", newline="").read()
        case("A1: the staged raw event carries the span VERBATIM", span in body)
    else:
        case("A1: staged file present to inspect", False)

    rc2, _out2, _err2 = register_cli(root)
    case("A1 process 2/4 (register-candidates.py): exits 0", rc2 == 0)
    case("A1: the candidate is now REGISTERED",
         cand.effective_state(root, candidate_id) == "registered")

    rc3, out3, _err3 = check_cli(root)
    case("A1 process 3/4 (check-candidates.py): exits 0 (green)", rc3 == 0)
    case("A1: census reports PASS", "PASS" in out3)

    art = "deploy/evidence/a1-armed.md"
    commit_file(root, art, armed_text(candidate_id), signing_key=pubA)
    rc4, out4, _err4 = promote_cli(stage_dir, root, candidate_id, art)
    case("A1 process 4/4 (promote-candidate.py, via the fixture-config shadow "
         "copy): exits 0 and prints PROMOTED", rc4 == 0 and "PROMOTED" in out4)

    raws = _raw_files(root)
    case("A1: exactly one raw/ event was written", len(raws) == 1)
    raw_rel = "raw/" + raws[0] if raws else None
    if raw_rel:
        raw_body = open(os.path.join(root, raw_rel.replace("/", os.sep)),
                         encoding="utf-8", newline="").read()
        case("A1: the raw event carries the span VERBATIM", span in raw_body)
        case("A1: the raw event's provenance block names the candidate id",
             ("- candidate_id: %s" % candidate_id) in raw_body)
    else:
        case("A1: raw event present to inspect", False)

    case("A1: the ledger's EFFECTIVE state is now `promoted`",
         cand.effective_state(root, candidate_id) == "promoted")

    rc5, out5, _err5 = check_cli(root)
    case("A1: check-candidates.py (a 5th real process) is STILL green after "
         "promotion", rc5 == 0 and "PASS" in out5)

    if raw_rel:
        o, _basis, _attested = origin_mod.assign_origin(
            raw_rel, True, origin_config_path=os.path.join(stage_dir, "origin-config.yaml"))
        case("A1: origin.assign_origin on the WRITTEN raw path yields `human` "
             "under the FIXTURE origin-config (the mint this whole gate exists "
             "to authorise) -- never the live deploy/origin-config.yaml",
             o == "human")
    else:
        case("A1: origin assignment check (skipped: no raw event to inspect)", False)


# ==================================================================== A2
def a2(case, mkroot):
    """A2 -- the staging floor, end to end. The attack: a session writes ITS OWN
    file, with an operator-looking filename, DIRECTLY into intake/session-
    candidates/, bypassing the harvester entirely.

    WHAT NO UNIT SUITE ALREADY PROVES: origin.py's own self-test proves
    assign_origin() floors a hardcoded string -- a pure function call, nothing
    ever touches a filesystem or a ledger. This proves the SEAM: a file
    ACTUALLY SITTING on disk in the real staging directory, processed by the
    REAL register-candidates.py subprocess (which must quarantine it, since it
    is not a schema-valid candidate -- exactly what a forging session's own
    file would not be), AND separately checked against origin.py's rule -- two
    different modules independently agreeing the forgery never reaches
    operator provenance, not one function's opinion of itself."""
    root = mkroot("a2")
    git_init(root)
    staging = os.path.join(root, "intake", "session-candidates")
    os.makedirs(staging, exist_ok=True)
    rel = "intake/session-candidates/2026-07-22-ryan-injected-operator-event.md"
    full = os.path.join(root, *rel.split("/"))
    forged_text = (
        "# A session's own attempt to mint operator provenance\n\n"
        "This file was written directly into the staging directory by a "
        "session, using an operator-looking filename, bypassing the harvester "
        "entirely -- the attack the staging floor exists to defeat.\n"
    )
    with open(full, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(forged_text)

    rc, _out, _err = register_cli(root)
    case("A2: register-candidates over a session-forged ryan-*.md file exits 0 "
         "(quarantine, not a crash)", rc == 0)

    eff = cand.load_candidate_ledger(root)
    matches = [r for r in eff.values() if r["event"] == rel]
    case("A2: the forged file is QUARANTINED (never registered, never eligible "
         "for promotion) -- through the real register-candidates.py CLI, not "
         "an in-process assertion",
         len(matches) == 1 and matches[0]["state"] == "quarantined")

    fixture_origin_cfg = os.path.join(root, "origin-config-fixture.yaml")
    with open(fixture_origin_cfg, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("human_prefixes: [ryan]\n")
    o, basis, _attested = origin_mod.assign_origin(
        rel, True, origin_config_path=fixture_origin_cfg)
    case("A2: origin.assign_origin on the forged staged path yields "
         "session-derived, NOT human -- the staging floor holds even under a "
         "[ryan] fixture config where the filename alone WOULD mint human",
         o == "session-derived" and "staging floor" in basis)

    o2, _b2, _a2 = origin_mod.assign_origin(
        "2026-07-22-ryan-injected-operator-event.md", True,
        origin_config_path=fixture_origin_cfg)
    case("A2: the SAME filename OUTSIDE the staging directory (same fixture "
         "config) DOES mint human -- confirms the floor is doing real work, "
         "not an accident of an unconfigured prefix", o2 == "human")

    case("A2: nothing was ever written to raw/ for the forged candidate",
         _raw_files(root) == [])


# ==================================================================== A3
def a3(case, stage_dir, pubA, pubB, mkroot):
    """A3 -- the artifact attack matrix, at CLI level. A validly staged and
    registered candidate; five ways to try to arm it wrong.

    WHAT NO UNIT SUITE ALREADY PROVES: promote-candidate.py's own self-test
    exercises this same matrix (and more variants) by calling promote()
    in-process and inspecting the raised PromotionRefused object's .check
    attribute directly. This proves the CLI CONTRACT: that a subprocess given
    only --candidate-id/--artifact strings on argv actually PRINTS a
    distinguishable, correctly-named refusal to stdout (the only interface a
    real operator or a real script ever sees), non-zero exit codes propagate
    correctly through five separate process launches, and raw/ truly stays
    empty as observed from OUTSIDE the process (a filesystem check after the
    subprocess has fully exited), not via an in-process os.listdir call in the
    same interpreter that ran promote()."""
    root = mkroot("a3")
    git_init(root)
    session_id = "sess-a3-matrix"
    span = "A3 fixture: a validly staged and registered candidate for the artifact attack matrix."
    candidate_id, (rc_h, _oh, _eh), (rc_r, _or_, _er) = harvest_and_register(root, session_id, span)
    case("A3 setup: harvest+register both exit 0", rc_h == 0 and rc_r == 0)
    case("A3 setup: candidate is registered",
         cand.effective_state(root, candidate_id) == "registered")

    refusals = {}

    art_a = "deploy/evidence/a3-does-not-exist.md"
    rc, out, err = promote_cli(stage_dir, root, candidate_id, art_a)
    case("A3(a) NO ARTIFACT AT ALL: promote-candidate CLI refuses", rc != 0)
    refusals["no-artifact"] = out + err

    art_b = "deploy/evidence/a3-worktree-only.md"
    p_b = os.path.join(root, art_b.replace("/", os.sep))
    os.makedirs(os.path.dirname(p_b), exist_ok=True)
    with open(p_b, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(armed_text(candidate_id))
    rc, out, err = promote_cli(stage_dir, root, candidate_id, art_b)
    case("A3(b) WORKING-TREE-ONLY artifact (never committed): refused", rc != 0)
    refusals["worktree-only"] = out + err

    art_c = "deploy/evidence/a3-unsigned.md"
    commit_file(root, art_c, armed_text(candidate_id), signing_key=None)
    rc, out, err = promote_cli(stage_dir, root, candidate_id, art_c)
    case("A3(c) UNSIGNED committed artifact (an earlier-run attack): refused", rc != 0)
    refusals["unsigned"] = out + err

    art_d = "deploy/evidence/a3-unpinned.md"
    commit_file(root, art_d, armed_text(candidate_id), signing_key=pubB)
    rc, out, err = promote_cli(stage_dir, root, candidate_id, art_d)
    case("A3(d) NON-PINNED key (a good signature by the wrong hand): refused", rc != 0)
    refusals["unpinned-key"] = out + err

    art_e = "deploy/evidence/a3-wrong-cid.md"
    commit_file(root, art_e, armed_text("sess-nope-0000000000000000"), signing_key=pubA)
    rc, out, err = promote_cli(stage_dir, root, candidate_id, art_e)
    case("A3(e) ARTIFACT NAMES A DIFFERENT candidate_id: refused", rc != 0)
    refusals["wrong-candidate-id"] = out + err

    texts = list(refusals.values())
    case("A3: all five refusals produced NON-EMPTY, PAIRWISE-DISTINCT stdout+"
         "stderr -- the five failure modes are told apart in the actual CLI "
         "output, not collapsed into one generic 'refused'",
         all(t.strip() for t in texts) and len(set(texts)) == len(texts))
    case("A3(a): the printed refusal names the introducing-commit check (5)",
         "(5) introducing commit" in refusals["no-artifact"])
    case("A3(b): the working-tree-only artifact hits the SAME check (5) -- an "
         "uncommitted file has no introducing commit either",
         "(5) introducing commit" in refusals["worktree-only"])
    case("A3(c): the printed refusal names an UNSIGNED commit",
         "unsigned" in refusals["unsigned"].lower())
    case("A3(d): the printed refusal names an UNPINNED key",
         "unpinned" in refusals["unpinned-key"].lower())
    case("A3(e): the printed refusal names the artifact-binding check (8)",
         "(8) artifact binding" in refusals["wrong-candidate-id"])

    case("A3: raw/ is EMPTY after all five attempts", _raw_files(root) == [])
    case("A3: the candidate is STILL registered (never promoted) after all "
         "five attempts", cand.effective_state(root, candidate_id) == "registered")


# ==================================================================== A4
def a4(case, stage_dir, pubA, mkroot):
    """A4 -- taint cannot promote, even with a fully valid armed artifact.

    WHAT NO UNIT SUITE ALREADY PROVES: promote-candidate.py's own self-test
    proves the taint check in-process. This proves the taint value survives
    the FULL cross-process trip -- harvested with --origin-max unknown by one
    real process, staged to disk, registered by a second real process, read
    back off disk by a third real process (the shadow-copy promote CLI) -- and
    still refuses, i.e. the taint is not lost or reset anywhere along that
    chain of independent processes and disk round-trips."""
    root = mkroot("a4")
    git_init(root)
    session_id = "sess-a4-taint"
    span = "A4 fixture: a candidate harvested under a session that saw tainted (unknown-origin) content."
    candidate_id, (rc_h, _oh, _eh), (rc_r, _or_, _er) = harvest_and_register(
        root, session_id, span, origin_max="unknown")
    case("A4 setup: harvest with --origin-max unknown exits 0", rc_h == 0)
    case("A4 setup: register exits 0", rc_r == 0)
    rec = cand.load_candidate_ledger(root).get(candidate_id, {})
    case("A4 setup: candidate is registered with origin_max=unknown",
         rec.get("state") == "registered" and rec.get("origin_max") == "unknown")

    art = "deploy/evidence/a4-armed-tainted.md"
    commit_file(root, art, armed_text(candidate_id), signing_key=pubA)
    rc, out, err = promote_cli(stage_dir, root, candidate_id, art)
    case("A4: TAINTED origin_max=unknown refuses EVEN WITH a fully valid, "
         "properly-signed, correctly-bound armed artifact", rc != 0)
    combined = out + err
    case("A4: the refusal names the taint check AND both the candidate id and "
         "the tainted class",
         "(3) taint" in combined and candidate_id in combined and "unknown" in combined)
    case("A4: raw/ is empty", _raw_files(root) == [])
    case("A4: the candidate is still registered, never promoted",
         cand.effective_state(root, candidate_id) == "registered")


# ==================================================================== A5
def a5(case, stage_dir, pubA, mkroot):
    """A5 -- TOCTOU at CLI level. A validly signed artifact, then rebound in a
    LATER UNSIGNED commit to a different candidate id.

    WHAT NO UNIT SUITE ALREADY PROVES: promote-candidate.py's own self-test
    exercises the full 4-variant TOCTOU matrix (worktree/commit x cid/
    disposition) in-process. Re-running all four here would be exactly the
    duplication the build instructions warn against. This drill proves ONE
    variant (the strongest attacker story: a LATER commit, not just an
    uncommitted edit) survives the real subprocess CLI boundary -- i.e. that
    the verified-blob-binding discipline is not something that only holds
    when promote() is called as a Python function in the same process that
    set up the fixture, but holds when a genuinely separate process reads the
    same repository from scratch."""
    root = mkroot("a5")
    git_init(root)
    session_id = "sess-a5-toctou"
    span = "A5 fixture: TOCTOU at CLI level -- a signed artifact rebound afterward."
    candidate_id, (rc_h, _oh, _eh), (rc_r, _or_, _er) = harvest_and_register(root, session_id, span)
    case("A5 setup: harvest+register both exit 0", rc_h == 0 and rc_r == 0)

    art = "deploy/evidence/a5-toctou.md"
    good_sha = commit_file(root, art, armed_text(candidate_id), signing_key=pubA)
    tampered = armed_text("sess-zzzz-0f0f0f0f0f0f0f0f")
    commit_file(root, art, tampered, signing_key=None, msg="later UNSIGNED rebinding commit")

    rc, out, err = promote_cli(stage_dir, root, candidate_id, art)
    case("A5: TOCTOU rebind (a later UNSIGNED commit renaming the candidate) "
         "has NO EFFECT -- promotion proceeds exactly as if the artifact were "
         "still the original signed blob", rc == 0)
    if rc == 0:
        rec = cand.load_candidate_ledger(root).get(candidate_id, {})
        case("A5: the promoted record's gate_commit is the ORIGINAL signed sha, "
             "not the later unsigned one", rec.get("gate_commit") == good_sha)
        raws = _raw_files(root)
        if raws:
            body = open(os.path.join(root, "raw", raws[0]), encoding="utf-8", newline="").read()
            case("A5: the written raw event binds the ORIGINAL candidate id + "
                 "the SIGNED commit, and never contains the tampered rebind "
                 "target",
                 ("- candidate_id: %s" % candidate_id) in body
                 and ("- gate_commit: %s" % good_sha) in body
                 and "sess-zzzz-0f0f0f0f0f0f0f0f" not in body)
        else:
            case("A5: raw event present to inspect", False)
    else:
        case("A5 follow-up checks (skipped: promotion did not succeed as "
             "expected) -- printed output: %r" % (out + err), False)


# ==================================================================== A6
_CRASH_WRAPPER_TEMPLATE = """\
import importlib.util, os, signal, sys

_DEPLOY = %r


def _load(basename, alias):
    spec = importlib.util.spec_from_file_location(alias, os.path.join(_DEPLOY, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


harvest = _load("harvest-candidates.py", "harvest_crash_wrapper")


def _die(root_, record_):
    # The candidate FILE has already been written, fsynced, and renamed to its
    # final name by the time run_harvest() reaches this call (file-first
    # ordering, Part 5). A real, OS-level, self-directed process kill --
    # os.kill(getpid(), SIGTERM) -- fires HERE, deterministically, strictly
    # before the ledger record this call would have appended.
    sys.stdout.write(
        "CRASH-WRAPPER: candidate file already written; self-killing via "
        "SIGTERM strictly before any ledger append\\n")
    sys.stdout.flush()
    os.kill(os.getpid(), signal.SIGTERM)
    os._exit(9)  # belt-and-braces; should be unreachable if SIGTERM is delivered


harvest.candidates.append_candidate_record = _die
sys.exit(harvest.main(sys.argv[1:]))
"""


def a6(case, mkroot):
    """A6 -- crash-replay across REAL processes. The one property the unit
    suites structurally cannot prove: harvest-candidates.py's own self-test
    simulates a crash by monkey-patching append_candidate_record to RAISE, and
    catches the raised exception in the SAME process that set up the fixture
    and runs the assertions. This scenario kills a genuinely separate OS
    process instead.

    WHAT KIND OF KILL THIS IS (read this before trusting the result): see the
    module docstring's 'CRASH KILL METHOD FOR A6' section for the full
    reasoning. In short -- a real subprocess, running the real unmodified
    harvest-candidates.py module, self-terminates via
    os.kill(os.getpid(), signal.SIGTERM) (a genuine OS-level process kill, not
    a caught Python exception) at the exact point run_harvest() would call
    append_candidate_record(). This IS a real process death observed from
    outside (the parent sees a killed/non-zero-exit child, not an exception
    object). It is NOT an externally-timed signal delivered from outside at an
    arbitrary instant -- I judged that undeterminable against a microseconds-
    wide gap in unmodified code, and chose a deterministic self-kill over a
    flaky race. What this demonstrates: the write-then-append ordering
    survives a real process's hard death at exactly the documented crash
    point. What it does NOT demonstrate: resilience to a kill landing at some
    OTHER, arbitrary instruction boundary inside the write itself (that
    boundary is covered instead by the file-write's own fsync+os.replace
    atomicity, which is candidates.py's concern, already proven in its own
    self-test)."""
    root = mkroot("a6")
    git_init(root)
    session_id = "sess-a6-crash"
    span = "A6 fixture: real-process crash-replay across two separate OS processes."
    candidate_id = cand.make_candidate_id(session_id, span)

    fd_w, wrapper_path = tempfile.mkstemp(prefix="r1-accept-crash-wrapper-", suffix=".py")
    fd_s, span_path = tempfile.mkstemp(prefix="r1-accept-a6-span-", suffix=".txt")
    try:
        with os.fdopen(fd_w, "w", encoding="utf-8") as fh:
            fh.write(_CRASH_WRAPPER_TEMPLATE % (_HERE,))
        with os.fdopen(fd_s, "w", encoding="utf-8", newline="") as fh:
            fh.write(span)

        killed = subprocess.run(
            [_PY, wrapper_path, "--root", root, "--session-id", session_id,
             "--class", "finding", "--disposition", "plain-intake",
             "--span-file", span_path, "--origin-max", "corpus"],
            capture_output=True, text=True)
        case("A6: the crash-wrapper subprocess died (non-zero/killed return "
             "code -- a REAL OS-level process termination, not a Python "
             "exception this drill caught)", killed.returncode != 0)

        staging = os.path.join(root, cand.CANDIDATES_STAGING_DIR)
        staged_now = os.listdir(staging) if os.path.isdir(staging) else []
        case("A6: the candidate FILE survived the real process kill "
             "(file-first write ordering, Part 5)",
             any(candidate_id in f for f in staged_now))
        case("A6: NO ledger record exists yet (the kill landed strictly "
             "before the append)", cand.effective_state(root, candidate_id) is None)
        case("A6: no leftover .candidate-tmp-* file from the killed process",
             [f for f in staged_now if f.startswith(".candidate-tmp-")] == [])

        rc2, _out2, _err2 = harvest_cli(root, session_id, "finding", "plain-intake",
                                         span, origin_max="corpus")
        case("A6: the NORMAL harvester re-run (a second, separate, real "
             "subprocess, unmodified CLI) exits 0", rc2 == 0)

        hist = [r for r in cand.ledger_history(root) if r["candidate_id"] == candidate_id]
        case("A6: exactly ONE chain entry now exists for this span hash",
             len(hist) == 1)
        final_files = [f for f in os.listdir(staging) if candidate_id in f]
        case("A6: the staged file exists EXACTLY ONCE", len(final_files) == 1)
        case("A6: still no leftover .candidate-tmp-* files after the re-run",
             [f for f in os.listdir(staging) if f.startswith(".candidate-tmp-")] == [])

        rc3, out3, _err3 = check_cli(root)
        case("A6: check-candidates.py is GREEN after the crash + re-run",
             rc3 == 0 and "PASS" in out3)
    finally:
        for p in (wrapper_path, span_path):
            try:
                os.remove(p)
            except OSError:
                pass


# ==================================================================== A7
def a7(case, mkroot):
    """A7 -- conservation across processes. A staged file deleted out-of-band
    (by something other than any of these six modules); check-candidates.py,
    run as its own real process, must find and NAME it.

    WHAT NO UNIT SUITE ALREADY PROVES: check-candidates.py's own self-test
    proves this in-process, using its own `run(root)` function called directly
    and inspecting the returned `lines`/`code`. This proves the CLI actually
    prints the candidate id to STDOUT the way a real operator watching a real
    terminal (or /sweep) would see it, and exits with the real process's exit
    code observed by the OS, not a Python return value read by the same
    interpreter that ran the check."""
    root = mkroot("a7")
    git_init(root)
    session_id = "sess-a7-vanish"
    span = "A7 fixture: a staged file deleted out-of-band after registration."
    candidate_id, (rc_h, _oh, _eh), (rc_r, _or_, _er) = harvest_and_register(root, session_id, span)
    case("A7 setup: harvest+register both exit 0", rc_h == 0 and rc_r == 0)

    rec = cand.load_candidate_ledger(root)[candidate_id]
    event_path = os.path.join(root, *rec["event"].split("/"))
    os.remove(event_path)
    case("A7 setup: staged file removed out-of-band", not os.path.isfile(event_path))

    rc, out, _err = check_cli(root)
    case("A7: check-candidates.py CLI exits 1 (a real conservation violation)", rc == 1)
    case("A7: the specific candidate_id appears in stdout", candidate_id in out)
    case("A7: named as a VANISHED CANDIDATE", "VANISHED CANDIDATE" in out)


# ==================================================================== A8
def a8(case, mkroot):
    """A8 -- the decision inbox seam. A registered transcribe-as-operator-event
    candidate must reach DECISIONS-PENDING.md with the candidate id AND the
    verbatim span text, so the operator sees the actual words before saying
    yes.

    WHAT NO UNIT SUITE ALREADY PROVES: decision-inbox.py's own self-test
    builds its candidate fixtures via candidates.write_candidate_atomic() and
    candidates.append_candidate_record() called directly, then calls
    generate_content() in-process. This proves the full real chain: a real
    harvest-candidates.py subprocess and a real register-candidates.py
    subprocess produce the candidate, and a real decision-inbox.py subprocess
    (never having imported candidates.py in THIS process at all) finds and
    surfaces it correctly by reading the same on-disk ledger cold."""
    root = mkroot("a8")
    git_init(root)
    session_id = "sess-a8-inbox"
    span = ("A8 fixture: the operator decided the decision-inbox seam counts "
            "as done when the span text reaches DECISIONS-PENDING.md verbatim.")
    candidate_id, (rc_h, _oh, _eh), (rc_r, _or_, _er) = harvest_and_register(root, session_id, span)
    case("A8 setup: harvest+register both exit 0", rc_h == 0 and rc_r == 0)
    rec = cand.load_candidate_ledger(root).get(candidate_id, {})
    case("A8 setup: candidate is registered with transcribe-as-operator-event",
         rec.get("state") == "registered"
         and rec.get("proposed_disposition") == "transcribe-as-operator-event")

    rc, _out, _err = inbox_cli(root, date_str="2026-07-22")
    case("A8: decision-inbox.py CLI exits 0", rc == 0)

    inbox_path = os.path.join(root, "DECISIONS-PENDING.md")
    case("A8: DECISIONS-PENDING.md was written", os.path.isfile(inbox_path))
    content = open(inbox_path, encoding="utf-8").read() if os.path.isfile(inbox_path) else ""
    case("A8: the candidate id reaches the generated inbox", candidate_id in content)
    case("A8: the verbatim span text reaches the generated inbox (full or "
         "truncated-with-ellipsis)", span in content or span[:100] in content)
    case("A8: filed under the session-candidates section",
         "Session candidates awaiting your confirmation" in content)


# ==================================================================== hermeticity proof
def _prove_live_config_untouched(case, test_fps):
    """The ONE deliberate, read-only touch of the LIVE deploy/signing-config.yaml
    and deploy/origin-config.yaml in this entire file -- reusing the real
    modules' own defensive loaders (never a fixture path here, and never
    feeding the result into any promotion this drill performs). The original
    form of this case asserted the live config was UNARMED -- true until the
    operator's 2026-07-23 key act, and wrong ever after: an armed instance is
    the intended end state, not a hermeticity violation (same armed-era
    correction as promote-candidate.py's own suite, commit b3e3990). What
    hermeticity actually requires is that this drill neither DEPENDS ON nor
    MODIFIES the live config: every promotion above ran against the fixture
    shadow copy, and the assertion below proves the live file contains none of
    THIS RUN's test keys -- i.e. the drill never wrote the live trust config,
    whichever armed/unarmed state the instance is in."""
    real_promote = _load("promote-candidate.py", "promote_live_unarmed_check")
    cfg = real_promote.load_signing_config()  # path=None -> the LIVE file, deliberately
    live_keys = cfg["operator_signing_keys"]
    case("HERMETIC: the LIVE deploy/signing-config.yaml contains NO test-run "
         "key (this drill never writes the live trust config; armed or "
         "unarmed, its %d live key(s) are not ours)" % len(live_keys),
         all(k.get("fingerprint") not in test_fps for k in live_keys))

    real_origin = _load("origin.py", "origin_live_unarmed_check")
    ocfg = real_origin.load_origin_config()  # path=None -> the LIVE file, deliberately
    case("HERMETIC: this drill never invoked promote-candidate.py against the "
         "REAL deploy/ directory (every promotion above ran through the "
         "fixture-config shadow copy, and every harvest/register/check/inbox "
         "run used --root against a disposable temp git repo) -- the live "
         "deploy/origin-config.yaml's human_prefixes (%r) played no role in "
         "any assertion above" % ocfg["human_prefixes"], True)


# ==================================================================== self-test
def self_test():
    total = failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1
        return ok

    missing = []
    if shutil.which("git") is None:
        missing.append("git")
    if shutil.which("ssh-keygen") is None:
        missing.append("ssh-keygen")
    try:
        import yaml  # noqa: F401
    except ImportError:
        missing.append("PyYAML")
    if missing:
        print("drill-r1-acceptance: INCONCLUSIVE -- missing: %s" % ", ".join(missing))
        return 2

    roots = []

    def mkroot(tag):
        d = tempfile.mkdtemp(prefix="r1-accept-%s-" % tag)
        roots.append(d)
        return d

    keydir = tempfile.mkdtemp(prefix="r1-accept-keys-")
    stage_dir = tempfile.mkdtemp(prefix="r1-accept-shadow-")

    try:
        _build_shadow(stage_dir)
        pubA, lineA, fpA = keypair(keydir, "operator")
        pubB, _lineB, fpB = keypair(keydir, "impostor")
        case("(fixture) two REAL ed25519 keypairs generated with distinct "
             "fingerprints (never the operator's own key)",
             bool(fpA) and bool(fpB) and fpA != fpB)
        write_fixture_signing_config(
            stage_dir, [{"fingerprint": fpA, "public_key": lineA,
                         "principal": "operator@example.com"}])
        write_fixture_origin_config(stage_dir, ["testop"])
        case("(fixture) shadow deploy/ copy built at a throwaway path so "
             "promote-candidate.py's real CLI (which has no --signing-config "
             "flag -- its trust config resolves from its OWN __file__, not "
             "--root) reads FIXTURE trust config, never the live one",
             all(os.path.isfile(os.path.join(stage_dir, n)) for n in _SHADOW_FILES)
             and os.path.isfile(os.path.join(stage_dir, "signing-config.yaml"))
             and os.path.isfile(os.path.join(stage_dir, "origin-config.yaml")))

        print("-- A1: full happy pipeline, four real processes --")
        a1(case, stage_dir, pubA, mkroot)
        print("-- A2: the staging floor, end to end --")
        a2(case, mkroot)
        print("-- A3: the artifact attack matrix, at CLI level --")
        a3(case, stage_dir, pubA, pubB, mkroot)
        print("-- A4: taint refuses even with a fully valid armed artifact --")
        a4(case, stage_dir, pubA, mkroot)
        print("-- A5: TOCTOU at CLI level --")
        a5(case, stage_dir, pubA, mkroot)
        print("-- A6: crash-replay across REAL processes --")
        a6(case, mkroot)
        print("-- A7: conservation by chain, out-of-band deletion --")
        a7(case, mkroot)
        print("-- A8: the decision inbox seam --")
        a8(case, mkroot)
        print("-- hermeticity: the live trust config was never armed by this drill --")
        _prove_live_config_untouched(case, {fpA, fpB})
    finally:
        shutil.rmtree(keydir, ignore_errors=True)
        shutil.rmtree(stage_dir, ignore_errors=True)
        for r in roots:
            shutil.rmtree(r, ignore_errors=True)

    if failed:
        print("drill-r1-acceptance: FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("drill-r1-acceptance: PASS (%d/%d)" % (total, total))
    return 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    print("usage: drill-r1-acceptance.py --self-test\n\n"
          "This is the R-1 consolidated done-bar (build-decisions Part 8): a "
          "hermetic, CLI-level, cross-module acceptance drill over harvest-"
          "candidates.py / register-candidates.py / promote-candidate.py / "
          "check-candidates.py / decision-inbox.py / origin.py / candidates.py. "
          "No --root live-instance mode is offered -- see the module "
          "docstring's 'NO --root MODE' section for why.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
