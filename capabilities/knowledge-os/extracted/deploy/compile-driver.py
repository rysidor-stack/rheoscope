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
  compile-driver.py --set-aside --root DIR --seq N --view PATH --ruling TEXT
  compile-driver.py --set-aside --root DIR --seq N --union-event PATH --ruling TEXT
  compile-driver.py --baseline-reset --root DIR (--view PATH | --views-file PATH)
                    --refresh-commit SHA --provenance TEXT --ruling TEXT
  compile-driver.py --reconcile --root DIR
  compile-driver.py --self-test
  Exit: 0 clean | 1 validation/gate failure | 2 inconclusive/usage | 3 lock held

SET-ASIDE (v3.0.29, the operator's OTHER adjudication path -- OPERATIONS.md
sec.7 names exactly two dispositions for a non-confirm verdict: correct
through the cycle (--revert + corrected answers + --run), or the operator
sets the verdict aside). --set-aside is the shipped form of the second:
it journals the operator's ruling (their words, verbatim, via --ruling) as
an absorption_adjudicated[] record for ONE view of ONE non-confirmed run,
and that record ALSO advances the view's verify baseline -- pinned to the
run commit's content the operator ruled on -- so the next update to the
view diffs from the adjudicated state instead of from birth. The verify
packet then names that baseline "adjudicated <date> by operator ruling,
not machine-verified", in so many words: nothing is dropped, its status is
named. A bare rejection with no operator ruling advances NOTHING. This
mode never reverts, never edits a view, and refuses: a confirmed leg
(nothing to adjudicate), a reverted run (nothing stands), a transport
failure (that is --reverify's case, not an adjudication), and a missing
--ruling (an unrecorded ruling is standing memory, which the HUMAN-GATE
doctrine refuses).

UNION-LEG SET-ASIDE (v3.0.39, closing backlog v3.0-105 per the ratified
verify-doors design). A union no-op leg (joint-citation no-op check) can
complete with a genuine non-confirm verdict, but its subject is a run+event
pair, never an absorbed view -- so `--set-aside --seq N --union-event
<raw path>` is the second addressing mode, mutually exclusive with --view.
The journaled absorption_adjudicated[] entry carries the pseudo-view subject
`union:<event>` (exactly the identity the verify ledger and the trajectory
drill already key union rows by) plus what the checker actually graded: the
event hash and the per-view union shas from the leg's own justification,
and the kept verdict artifact. It carries NO baseline pin fields
(baseline_commit / view_sha256 are ABSENT): a union leg absorbed nothing,
so there is no content whose baseline could advance. The guards mirror the
absorbed-view path clause for clause; the mixed-run --reverify decline is
untouched (re-firing against a standing rejection would be re-rolling a
verdict).

BASELINE-RESET (v3.0.39, closing backlog v3.0-106 per the ratified
verify-doors design). An out-of-engine wholesale refresh (a corpus
photograph) rewrites articles with no per-event provenance; the verify
packet's baseline ladder had no rung for it, so every post-refresh packet
diffed from a pre-photograph base -- a false fabrication-class rejection
guaranteed before the checker read a word. `--baseline-reset` is the
operator's explicit repair: it pins a view's verify baseline at a NAMED
out-of-engine refresh commit (`git show <refresh_commit>:<view>`,
hash-locked at journal time), journaling provenance and ruling verbatim as
a `baseline-reset` record with baseline_reset[] entries. Bulk form:
--views-file (one view path per line); the record carries a refused[] list
naming every view that did not pass and why -- journal-only truth includes
the refusals. The guard chain, in refusal order: G1 operator words or
nothing (empty --ruling or --provenance refuses); G2 worktree clean; G3 the
refresh commit exists and is an ancestor of HEAD; G4 the refresh commit is
not engine-authored history (it must not touch any receipts/journal/*.json
-- an import is never a run commit); G5 the view exists AT the refresh
commit (its bytes there are what gets pinned, never the worktree); G6
no-rewind (a stamp whose pinned commit is a descendant of, identical to, or
incomparable with the refresh commit refuses -- only a strict-ancestor
stamp, or no stamp, proceeds; fail-closed when ancestry cannot be
determined); G7 one reset per (view, refresh_commit). A reset adjudicates
NOTHING: it touches no ledger row and no leg state; every open non-confirm
verdict stays exactly as open as it was. Reset articles are not "verified"
-- the packet names the baseline "reset to imported snapshot by operator
ruling, not machine-verified" in so many words.

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
  (g) TRUST-SURFACE INTEGRITY (v3.0-120, v3.0.46): the artifact is COMMITTED-
      IDENTICAL (tracked; working tree == HEAD) -- always refused otherwise --
      and OPERATOR-SIGNED per deploy/trust.py (its newest commit verifies
      against the pinned presence-requiring key): refused under project.yaml
      `trust_surface_signing: required`, accepted and SURFACED as a
      "WARNING (trust-surface)" line under `warn` (the adoption default).
      "Committed" is now checked, never claimed.
This is a MARKER check, deliberately: judging whether prose authorizes an action
is not a mechanical act, so the driver refuses everything that is not obviously
in class and leaves the rest to the operator who wrote the artifact.

ATOMICITY RULE (build spec B-1, normative, added per the round-3 review). The
absorb commit precedes verify -- verify_run() grades a COMMITTED run -- so the
driver guarantees no run ever ENDS holding an unverified absorption:
  * verify leg COMPLETES with a non-confirm verdict (revised/rejected): that is
    verified content; the verdict is journaled data. What happens next splits by
    the leg's RECORD-TIME disposition (verifier demotion, operator-approved
    design 2026-08-09 -- the one sanctioned loosening, scoped to the
    completeness/scope class; the class is journaled by the engine at record
    time and this driver NEVER re-derives it from the verdict artifact):
      - ANY leg journaled `blocking` (fabrication/contradiction/over-certainty,
        `unclassified`, `stamp-refused`, every pre-demotion record): exit 1,
        NO revert -- byte-identical to the pre-demotion behavior below.
      - ALL non-confirm legs journaled `recorded` (scope-omission /
        enumeration-incomplete): the run COMPLETES, exit 0, with a mandatory
        RECORDED SIGNALS band naming each leg -- the articles stay absorbed
        and live, the signals ride the compile skill's Step 3c into
        DECISIONS-PENDING, and adjudication (`--revert` redo / `--set-aside`
        accept) stays available at the operator's pace.
    For the blocking case: exit 1, NO revert -- the run
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
    "compile-driver.py --set-aside --root DIR --seq N --view PATH "
    "--ruling TEXT\n"
    "compile-driver.py --set-aside --root DIR --seq N --union-event PATH "
    "--ruling TEXT\n"
    "compile-driver.py --baseline-reset --root DIR (--view PATH | "
    "--views-file PATH)\n"
    "                  --refresh-commit SHA --provenance TEXT --ruling TEXT\n"
    "compile-driver.py --reconcile --root DIR\n"
    "compile-driver.py --verify-ledger --root DIR [--since YYYY-MM-DD]\n"
    "                                        # read-only builder-verifier\n"
    "                                        # agreement ledger, journal only\n"
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


def _trust():
    """deploy/trust.py (v3.0-120): committed-identical + operator-signed checks
    on HUMAN-GATE artifacts. Itself a trust surface; see its docstring for what
    it can and cannot guarantee from inside the agent's process."""
    if "trust" not in _ENGINE_MODS:
        _ENGINE_MODS["trust"] = _load("trust.py", "trust_drv")
    return _ENGINE_MODS["trust"]


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
_VALUE_FLAGS = ("--root", "--staging", "--authorization", "--seq", "--reason",
                "--view", "--ruling", "--since", "--union-event",
                "--refresh-commit", "--provenance", "--views-file")
_BOOL_FLAGS = ("--run", "--reconcile", "--reverify", "--revert", "--set-aside",
               "--baseline-reset", "--verify-ledger", "--self-test",
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
           "seq": None, "reason": None, "view": None, "ruling": None,
           "since": None, "sections": False, "union-event": None,
           "refresh-commit": None, "provenance": None, "views-file": None}
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
    if out["mode"] == "verify-ledger":
        if not out["root"]:
            raise UsageError("--verify-ledger requires --root")
        if out["since"] is not None and not re.match(
                r"^\d{4}-\d{2}-\d{2}$", out["since"]):
            raise UsageError("--since takes YYYY-MM-DD")
    if out["mode"] == "set-aside":
        missing = [f for f in ("root", "seq", "ruling") if not out[f]]
        if missing:
            raise UsageError(
                "--set-aside requires %s (it records the operator's ruling "
                "on ONE flagged subject of ONE non-confirmed run; --ruling is "
                "the operator's own words, verbatim -- an unrecorded ruling "
                "is standing memory, which the HUMAN-GATE doctrine refuses)"
                % ", ".join("--" + m for m in missing))
        if bool(out["view"]) == bool(out["union-event"]):
            raise UsageError(
                "--set-aside takes exactly one of --view (an absorbed view's "
                "non-confirm leg) or --union-event (a union no-op leg, "
                "addressed by run seq + event; v3.0-105) -- the two "
                "addressing modes are mutually exclusive")
        try:
            out["seq"] = int(out["seq"])
        except (TypeError, ValueError):
            raise UsageError("--seq must be an integer journal sequence number")
    if out["mode"] == "baseline-reset":
        missing = [f for f in ("root", "refresh-commit", "provenance",
                               "ruling") if not out[f]]
        if missing:
            raise UsageError(
                "--baseline-reset requires %s (the ruling and the provenance "
                "are the operator's own words, journaled verbatim -- an "
                "unrecorded ruling is standing memory, and a reset that "
                "cannot name what was imported is the escape-hatch shape the "
                "firewall forbids)"
                % ", ".join("--" + m for m in missing))
        if bool(out["view"]) == bool(out["views-file"]):
            raise UsageError(
                "--baseline-reset takes exactly one of --view (one view) or "
                "--views-file (bulk: one view path per line)")
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


def validate_authorization(repo, auth_path, class_check=None, trust_gate=None):
    """Checks (a)-(g) of the module docstring. Returns the dispatch_guard
    authorization dict {"path": <repo-relative posix>, "quote": <verbatim>,
    "trust_warnings": [...]}; raises AuthorizationError otherwise. Writes
    NOTHING; reads the named artifact and git's view of it (check (g)).

    (g) TRUST-SURFACE INTEGRITY (v3.0-120, brief sections 4-5): the artifact
    must be COMMITTED-IDENTICAL (tracked, working tree == HEAD) -- an
    authorization that exists but is not committed, or differs from HEAD, is
    refused naming the diff; and OPERATOR-SIGNED (its newest commit verifies
    against core/security/hooks/allowed_signers with a presence-requiring sk
    key) -- refused under project.yaml `trust_surface_signing: required`,
    accepted-and-surfaced (trust_warnings) under `warn`, the adoption default.
    `trust_gate` is the self-test injection seam; default deploy/trust.py."""
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
    # (c) exists, non-empty -- and (g) FIRST (v3.0-120, cross-vendor round 12): the
    # trust gate runs before any content check and hands back the exact HEAD blob it
    # verified; every check below parses THAT, never the file on disk, so there is no
    # window between "what was checked" and "what was read".
    abs_path = os.path.join(repo, rel_posix.replace("/", os.sep))
    if not os.path.isfile(abs_path):
        raise AuthorizationError("authorization file not found: %s" % rel_posix)
    if trust_gate is None:
        trust_gate = _trust().gate_artifact
    gate = trust_gate(repo, rel_posix)
    if not gate.get("ok"):
        raise AuthorizationError(gate.get("refuse") or "trust gate refused")
    blob = gate.get("blob")
    if not isinstance(blob, (bytes, bytearray)):
        raise AuthorizationError("trust gate returned no verified bytes for %s -- refusing "
                                 "to parse the working-tree file instead" % rel_posix)
    text = bytes(blob).decode("utf-8-sig", errors="replace")
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
    return {"path": rel_posix, "quote": quote,
            "trust_warnings": list(gate.get("warnings") or [])}


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


def _journal_label_completed(label):
    """Did the JOURNALED verdict_label record a COMPLETED verdict? Mirrors
    classify_verdict's allowlist over the label the engine journaled at
    record time (incl. the substrate-gated(inner) form). Transport-shaped
    labels ('bridge-error', 'no-verdict-field', ...) journal as themselves
    and are NOT completed."""
    l = str(label)
    if l in _COMPLETED_LABELS:
        return True
    if l.startswith("substrate-gated(") and l.endswith(")"):
        return l[len("substrate-gated("):-1] in _COMPLETED_LABELS
    return False


def _confirm_shaped(label):
    """Both confirm spellings a completed leg can carry: bare `confirmed`
    and `substrate-gated(confirmed)` (a bare startswith('confirm') test
    would leave the gated shape invisible -- design K2)."""
    return (label.startswith("confirm")
            or label.startswith("substrate-gated(confirm"))


def verify_record_legs(repo, vrec):
    """Classify every leg a verify record actually fired. Returns
    {"legs": [{"artifact", "label", "completed", "stamped"}],
     "incomplete": [str, ...]}.

    JOURNAL-FIRST (v3.0-74): journal placement is the confirmation truth. A
    leg is confirmed iff an absorption_verified[] entry exists for it --
    `stamped: True`, the engine's own record. Every attempts-leg and
    unverified no-op union leg is `stamped: False` whatever its artifact
    says. The artifact (or the journaled verdict_label, preferred where
    present) supplies only two things: COMPLETION -- did a real verdict
    happen at all (the transport axis, unchanged) -- and the FORENSIC label
    for display. The axes are deliberately separate: a discarded-approval
    leg is COMPLETED (a real verdict exists) but NOT CONFIRMED (the journal
    holds no stamp). A completed stampless leg whose label is confirm-shaped
    is relabelled `stamp-refused(<original label>)` -- the state sec.7's
    BLOCKED row already names.

    Where the journal carries the demotion fields (verdict_label), THEY are
    read and the artifact is never opened -- post-demotion records are fully
    journal-read here. Legacy records (no journaled fields) still open the
    artifact, but ONLY for the completion axis and the forensic label, never
    to grant confirmation: absence of an artifact stays fail-closed
    INCOMPLETE, and no artifact value can ever produce a confirmed leg.

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

    def add(entry, source):
        artifact = entry.get("artifact")
        jl = entry.get("verdict_label")
        if jl is not None:
            completed, label = _journal_label_completed(jl), str(jl)
        else:
            completed, label = classify_verdict(
                _load_verdict_artifact(repo, artifact) if artifact else None)
        if completed and _confirm_shaped(label):
            label = "stamp-refused(%s)" % label
        legs.append({"artifact": artifact, "label": label,
                     "completed": completed, "stamped": False})
        if not completed:
            incomplete.append("%s %s -> %s" % (source, artifact or "(no "
                                               "artifact)", label))

    for av in vrec.get("absorption_verified") or []:
        legs.append({"artifact": av.get("artifact"), "label": "confirmed",
                     "completed": True, "stamped": True})
    for at in vrec.get("absorption_verify_attempts") or []:
        add(at, "absorption leg")
    for nc in vrec.get("noop_candidates") or []:
        if nc.get("verified") or not nc.get("artifact"):
            continue
        add(nc, "no-op leg")
    return {"legs": legs, "incomplete": incomplete}


def _partition_nonconfirm_legs(vrec):
    """Verifier demotion (2026-08-09): partition a verify record's completed
    non-confirm legs by their RECORD-TIME disposition, from the JOURNAL
    ALONE -- the verdict artifact is forensics, never state (v3.0-74's
    lesson). A leg with no journaled disposition (every pre-demotion record)
    is BLOCKING: byte-identical to the semantics it had when written.
    Only reached on runs whose legs all COMPLETED (the incomplete branch --
    auto-revert -- is checked first and is untouched).
    Returns (blocking, recorded): lists of
    {"subject", "reason", "classes"}."""
    blocking, recorded = [], []
    for at in vrec.get("absorption_verify_attempts") or []:
        leg = {"subject": at.get("view", "?"),
               "reason": at.get("reason", ""),
               "classes": at.get("reason_classes") or ["unclassified"]}
        (recorded if at.get("disposition") == "recorded"
         else blocking).append(leg)
    seen_events = set()
    for nc in vrec.get("noop_candidates") or []:
        if nc.get("verified") or not nc.get("artifact"):
            continue
        ev = nc.get("event", "?")
        if ev in seen_events:      # one union leg per event, not per candidate
            continue
        seen_events.add(ev)
        leg = {"subject": "union:%s" % ev, "reason": "",
               "classes": nc.get("reason_classes") or ["unclassified"]}
        (recorded if nc.get("verify_disposition") == "recorded"
         else blocking).append(leg)
    return blocking, recorded


_COMPLETED_LABELS = ("confirmed", "revised", "rejected")


def execute_verify_ledger(root, since=None, out=print):
    """Read-only builder-verifier agreement ledger (verifier demotion
    2026-08-09, design sec.6): one row per verify leg, walked from the
    JOURNAL ALONE -- this function never opens a verdict artifact, so it can
    never contradict the engine's own record (v3.0-74). This is the
    30/60/90-day check on the demotion: run with --since at each mark.
    Reading the summary: the demotion was RIGHT if recorded-class signals
    mostly end accepted (or age out untouched); WRONG -- re-promote the
    class -- if a majority end redone with real content added. Blocking-class
    volume is the control: the demotion should not move it."""
    repo = os.path.abspath(root)
    jd = os.path.join(repo, "receipts", "journal")
    if not os.path.isdir(jd):
        out("INCONCLUSIVE: no journal at %s -- is --root the project root?"
            % jd)
        return EXIT_INCONCLUSIVE
    recs = load_journal(repo)

    reverted_runs = set()
    adjudicated = set()                    # (run seq, view)
    absorbs_by_view = {}                   # view -> [run seq, ...]
    confirmed_cover = {}                   # (run seq, view) -> newest verify
    #                                        seq holding a stamp for the pair
    for seq in sorted(recs):
        rec = recs[seq]
        dr = rec.get("driver_revert")
        if isinstance(dr, dict) and dr.get("status") == "reverted" \
                and isinstance(dr.get("reverts_seq"), int):
            reverted_runs.add(dr["reverts_seq"])
        for adj in rec.get("absorption_adjudicated") or []:
            if isinstance(adj.get("adjudicates_seq"), int):
                adjudicated.add((adj["adjudicates_seq"], adj.get("view")))
        for ab in rec.get("absorbed") or []:
            absorbs_by_view.setdefault(ab.get("view"), []).append(seq)
        if isinstance(rec.get("verifies_seq"), int):
            for av in rec.get("absorption_verified") or []:
                key = (rec["verifies_seq"], av.get("view"))
                confirmed_cover[key] = max(confirmed_cover.get(key, -1), seq)

    rows = []
    for seq in sorted(recs):
        vrec = recs[seq]
        run_seq = vrec.get("verifies_seq")
        if not isinstance(run_seq, int):
            continue
        started = str((vrec.get("run_window") or {}).get("start", ""))[:10]
        if since and (not started or started < since):
            continue

        def _outcome(view, label, disposition):
            if (run_seq, view) in adjudicated:
                return "set-aside"
            if run_seq in reverted_runs:
                if label is not None and label not in _COMPLETED_LABELS:
                    return "auto-reverted-transport"
                later = any(s > run_seq for s in absorbs_by_view.get(view, []))
                return "reverted-re-ridden" if later else "reverted"
            if label == "confirmed" and disposition is None:
                return "confirmed"
            # Supersession (v3.0-74 design sec.2.3): a LATER covering verify
            # record holding an absorption_verified entry for the same view
            # supersedes this record's open reading for the (run, view) pair
            # -- the view re-earned its stamp through a live leg (the narrow
            # --reverify recovery, or a transport re-fire). Journal-only, no
            # rewrite; without this the old row would read open-blocking
            # forever and the drain's zero-non-confirms exit could never be
            # reached.
            if confirmed_cover.get((run_seq, view), -1) > seq:
                return "superseded-confirmed"
            return "open-%s" % (disposition or "blocking")

        for av in vrec.get("absorption_verified") or []:
            rows.append({"seq": run_seq, "view": av.get("view", "?"),
                         "label": "confirmed", "classes": [],
                         "disposition": None,
                         "outcome": _outcome(av.get("view"), "confirmed",
                                             None)})
        for at in vrec.get("absorption_verify_attempts") or []:
            label = at.get("verdict_label")     # None on legacy records
            rows.append({"seq": run_seq, "view": at.get("view", "?"),
                         "label": label if label is not None else "(legacy)",
                         "classes": at.get("reason_classes")
                         or ["unclassified"],
                         "disposition": at.get("disposition") or "blocking",
                         "outcome": _outcome(at.get("view"), label,
                                             at.get("disposition")
                                             or "blocking")})
        seen_ev = set()
        for nc in vrec.get("noop_candidates") or []:
            if nc.get("verified") or not nc.get("artifact"):
                continue
            ev = nc.get("event", "?")
            if ev in seen_ev:
                continue
            seen_ev.add(ev)
            label = nc.get("verdict_label")
            rows.append({"seq": run_seq, "view": "union:%s" % ev,
                         "label": label if label is not None else "(legacy)",
                         "classes": nc.get("reason_classes")
                         or ["unclassified"],
                         "disposition": nc.get("verify_disposition")
                         or "blocking",
                         # v3.0-105: the union row's adjudication subject is
                         # the same pseudo-view string the row itself carries
                         # -- view=None here meant nothing could ever match,
                         # so a union set-aside could never read set-aside.
                         "outcome": _outcome("union:%s" % ev, label,
                                             nc.get("verify_disposition")
                                             or "blocking")})

    if not rows:
        out("verify-ledger: no verify legs in the journal%s."
            % (" since %s" % since if since else ""))
        return EXIT_OK

    out("VERIFY LEDGER%s -- %d leg(s), journal-derived only"
        % (" since %s" % since if since else "", len(rows)))
    for r in rows:
        out("  seq %-4d %-40s %-10s %-28s %s"
            % (r["seq"], r["view"], r["label"],
               ",".join(r["classes"]) if r["classes"] else "-",
               r["outcome"]))

    # Agreement: confirmed vs CLASSIFIED completed non-confirm legs.
    # Excluded and said so: legacy legs (no record-time fields),
    # stamp-refused (engine defect class, not a verifier judgment),
    # transport legs (no verdict happened).
    confirmed = [r for r in rows if r["label"] == "confirmed"]
    classified = [r for r in rows
                  if r["label"] in ("revised", "rejected")
                  and r["classes"] != ["unclassified"]
                  and "stamp-refused" not in r["classes"]]
    legacy = [r for r in rows if r["label"] == "(legacy)"]
    denom = len(confirmed) + len(classified)
    out("SUMMARY: %d confirmed / %d classified non-confirm -> agreement "
        "%s%s" % (len(confirmed), len(classified),
                  ("%d%%" % round(100.0 * len(confirmed) / denom))
                  if denom else "n/a",
                  "; %d legacy leg(s) excluded (no record-time class)"
                  % len(legacy) if legacy else ""))
    per_class = {}
    for r in classified:
        for c in r["classes"]:
            per_class[c] = per_class.get(c, 0) + 1
    if per_class:
        out("  by class: " + ", ".join(
            "%s=%d" % (c, n) for c, n in sorted(per_class.items())))
    rec_rows = [r for r in rows if r["disposition"] == "recorded"]
    if rec_rows:
        acc = sum(1 for r in rec_rows if r["outcome"] == "set-aside")
        redo = sum(1 for r in rec_rows
                   if r["outcome"].startswith("reverted"))
        opn = sum(1 for r in rec_rows if r["outcome"].startswith("open"))
        out("  recorded-class outcomes: accepted=%d redone=%d open=%d "
            "(mostly accepted -> demotion right; mostly redone -> "
            "re-promote the class)" % (acc, redo, opn))
    return EXIT_OK


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
    for w in authorization.get("trust_warnings", []):
        out("WARNING (trust-surface): %s" % w)

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

    # v3.0-63: the plan's deferred claim rows ride every summary block, so
    # the skill copies them into the receipt's pending_cascade.
    deferred_claims = []
    for erel, entry in (staged["plan"].get("claim_routing") or {}).items():
        if isinstance(entry, dict):
            for d in entry.get("deferred") or []:
                deferred_claims.append((erel, d.get("id"), d.get("text"),
                                        [str(t) for t in
                                         (d.get("targets") or [])]))

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
                           reverted=ok, deferred_claims=deferred_claims))
        return EXIT_FAIL

    # ---- verdict grading (the leg COMPLETED)
    confirmed = verify_result.get("absorption_confirmed", 0)
    checked = verify_result.get("absorption_checked", 0)
    noop_confirmed = verify_result.get("confirmed", 0)
    noop_checked = verify_result.get("checked", 0)
    non_confirm = (confirmed != checked) or (noop_confirmed != noop_checked)

    if non_confirm:
        # Verifier demotion (2026-08-09): partition the completed non-confirm
        # legs by the disposition the ENGINE journaled at record time --
        # never re-derived from the verdict artifacts (v3.0-74). Any
        # blocking leg (incl. unclassified, stamp-refused, and every
        # pre-demotion record) keeps the exit-1 path below byte-identical.
        vrec_j = load_journal(repo).get(verify_result.get("seq"), {})
        blocking_legs, recorded_legs = _partition_nonconfirm_legs(vrec_j)

        if recorded_legs and not blocking_legs:
            # completeness/scope class only: the run COMPLETES. The articles
            # are absorbed, live, and unverified-and-say-so (no stamp, no
            # baseline advance -- a bare rejection still advances nothing).
            out("VERIFY RECORDED SIGNALS (verifier demotion, completeness/"
                "scope class): %d non-confirm leg(s), all recorded-class -- "
                "the run completes; nothing blocks."
                % len(recorded_legs))
            for leg in recorded_legs:
                out("  recorded signal: %s -- [%s] %s"
                    % (leg["subject"], ",".join(leg["classes"]),
                       leg["reason"]))
            out("These articles are absorbed and live; the verdicts are "
                "journaled data. Land each signal in the operator's inbox "
                "(compile skill Step 3c) -- a recorded signal that never "
                "reaches DECISIONS-PENDING is a signal declared away. "
                "Adjudication stays available at the operator's pace: "
                "`--revert --seq N` (redo through the correction cycle) or "
                "`--set-aside` (their ruling recorded beside the verdict).")
            results = sensors(repo, run_sha, sections)
            out(_summary_block(run_seq, run_sha, absorbed_views,
                               verify_result, results, reverted=False,
                               deferred_claims=deferred_claims))
            if results["diff"][0] not in (0, None):
                out("check-run-diff FAILED on the run commit -- merge bar 3 "
                    "is red.")
                return EXIT_FAIL
            return EXIT_OK

        # verified content with a non-confirm verdict: journaled data. NO
        # revert -- invariant 4 wanted a verify on every absorption and one
        # happened. The branch stays unmergeable until adjudicated.
        out("VERIFY NON-CONFIRM: %d of %d absorption leg(s) confirmed, %d of "
            "%d no-op leg(s) confirmed. The verdicts are journaled data -- "
            "read them in receipts/verify/, then adjudicate: `--revert --seq "
            "N` this run, correct the answers in the (untouched) staging dir, "
            "and re-run `--run` so the correction lands validated and "
            "re-verified. Never hand-edit the written views. If the OPERATOR "
            "rules a verdict itself wrong, record their ruling per view: "
            "`--set-aside --seq N --view <path> --ruling \"<their words>\"` "
            "(operator-only; advances the view's baseline as adjudicated). "
            "NOT reverted here: the absorption IS verified, it just did not "
            "pass." % (confirmed, checked, noop_confirmed, noop_checked))
        # v3.0-84: name the failure CLASS per leg. "confirmed but stamp
        # refused" (e.g. no derivation region on a legacy view) is a different
        # repair than a verifier rejection, and the two used to print
        # identically.
        for att in verify_result.get("absorption_attempts", []) or []:
            if att.get("view") or att.get("reason"):
                cls = att.get("reason_classes")
                out("  non-confirm leg: %s -- %s%s"
                    % (att.get("view", "?"),
                       att.get("reason", "(no reason recorded)"),
                       " [class: %s]" % ",".join(cls) if cls else ""))
        if recorded_legs:
            # a mixed run blocks on its blocking legs, but the recorded
            # sibling legs' signal is not swallowed: named here, and they
            # still ride Step 3c into the operator's inbox.
            out("Also on this run, %d recorded-class signal(s) (blocked from "
                "completing by the leg(s) above; still land in the "
                "operator's inbox via compile skill Step 3c):"
                % len(recorded_legs))
            for leg in recorded_legs:
                out("  recorded signal: %s -- [%s] %s"
                    % (leg["subject"], ",".join(leg["classes"]),
                       leg["reason"]))
        results = sensors(repo, run_sha, sections)
        out(_summary_block(run_seq, run_sha, absorbed_views, verify_result,
                           results, reverted=False,
                           deferred_claims=deferred_claims))
        return EXIT_FAIL

    results = sensors(repo, run_sha, sections)
    out(_summary_block(run_seq, run_sha, absorbed_views, verify_result,
                       results, reverted=False,
                       deferred_claims=deferred_claims))
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
                   reverted=False, deferred_claims=None):
    """ONE plain-English block: absorbed / no-ops / verify verdicts / census /
    diff-check, so the calling skill re-states results without re-deriving
    them. `deferred_claims` (v3.0-63): the plan's deferred claim rows, listed
    here so the skill copies them into the receipt's pending_cascade -- a
    deferred claim that never reaches the receipt is a claim declared away."""
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
    if deferred_claims:
        lines.append("Deferred:      %d claim(s) routed to a later run -- "
                     "these MUST land in the receipt's pending_cascade:"
                     % len(deferred_claims))
        for erel, cid, text, targets in deferred_claims:
            lines.append("                 - [%s / %s] %s -> %s"
                         % (erel, cid, text, ", ".join(targets)))
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
    for w in authorization.get("trust_warnings", []):
        out("WARNING (trust-surface): %s" % w)
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
        # v3.0-74 narrow gate: a TERMINAL run qualifies for re-fire iff the
        # newest covering verify record's completed non-confirm legs are ALL
        # stamp-refused-shaped (>=1 exists) -- a discarded APPROVAL re-rolls
        # nothing (there is no adverse verdict to shop around), and this is
        # the cheapest recovery that ends machine-verified: one dispatch,
        # no revert of correct content, the stamp lands through the one
        # existing stamping path (its OWN fresh verdict decides). Rejected
        # or mixed runs stay declined with the standing message -- re-firing
        # legs against a standing rejection would be re-rolling the dice on
        # a verdict, which no-self-adjudication forbids.
        vrecs = [r for r in recs.values() if r.get("verifies_seq") == seq]
        stamp_refused = []
        if vrecs:
            newest = max(vrecs, key=lambda r: r.get("seq", 0))
            open_legs = [l for l in verify_record_legs(repo, newest)["legs"]
                         if l["completed"] and not l["stamped"]]
            if open_legs and all(l["label"].startswith("stamp-refused(")
                                 for l in open_legs):
                stamp_refused = open_legs
        if not stamp_refused:
            out("Nothing to do: run seq %d already has a terminal verify "
                "disposition. No dispatch was made." % seq)
            return EXIT_OK
        out("REVERIFY (v3.0-74 narrow gate): run seq %d is terminal, but its "
            "newest verify record carries %d stamp-refused leg(s) -- a "
            "verifier approval the engine could not stamp. Re-firing those "
            "legs; the fresh verdict decides." % (seq, len(stamp_refused)))

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
            # v3.0-74: a leg is confirmed iff STAMPED (absorption_verified
            # entry -- the journal's own record). A completed stampless leg
            # whose artifact approved reads stamp-refused(confirmed) and
            # QUALIFIES the run for adjudication -- the discarded-approval
            # reopening.
            nonconfirm = [l for l in legs["legs"] if not l["stamped"]]
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

    # COLLISION PRE-CHECK (v3.0-local-10, reported live 2026-08-06 and
    # reproduced upstream the same day). The re-ride recipe assumes the
    # rejected run is still the last word on its own articles. On a project
    # that simply kept working it is not: normal commits may have modified --
    # or split -- the same files since. `git revert` then conflicts, and the
    # failure path is worse than a refusal would be: the conflict is aborted
    # and journaled "revert-failed", which is NON-TERMINAL, so startup
    # reconciliation blocks every later compile until a human untangles it
    # (and in one reproduction the failure record could not be journaled at
    # all, leaving a doubled git error and no record). Detected BEFORE
    # anything is written, by blob comparison against the run commit --
    # exact, side-effect-free, and fail-closed on any path git cannot resolve
    # on either side.
    moved_on = []
    for path in _commit_files(repo, run_sha):
        if re.match(r"receipts/journal/\d+\.json$", path):
            continue        # restored by the revert itself, never a conflict
        rc_a, then_blob, _ea = _git(repo, "rev-parse",
                                    "%s:%s" % (run_sha, path))
        rc_b, now_blob, _eb = _git(repo, "rev-parse", "HEAD:%s" % path)
        if rc_a != 0 or rc_b != 0 or then_blob.strip() != now_blob.strip():
            moved_on.append(path)
    if moved_on:
        out("REFUSED: run seq %d is no longer the last word on its own "
            "articles -- %d of the file(s) it wrote have changed since "
            "(normal work, a later compile, or an article split):"
            % (seq, len(moved_on)))
        for path in moved_on:
            out("  - %s" % path)
        out("Reverting would undo that later work too, and a conflicted "
            "revert leaves a journaled failure that blocks every future "
            "compile. Nothing was reverted and nothing was journaled.")
        out("THE AGED CASE -- correct FORWARD instead of rewinding: the "
            "rejected absorption is already superseded on disk, so take each "
            "article's CURRENT text as the base, fix what the verdict named "
            "in a fresh plan and staging dir, and --run that. The rejection "
            "stays on the record as history, which is correct -- it did "
            "happen. Use --revert only while the run is still the newest "
            "thing to have touched its articles.")
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


# --------------------------------------------------------------- set-aside mode
def execute_set_aside(root, seq, view, ruling, union_event=None, out=print):
    """Operator set-aside of a non-confirm verdict on ONE subject (v3.0.29;
    see the module docstring's SET-ASIDE section for the doctrine). Two
    addressing modes, mutually exclusive:

      * --view: an absorbed view's leg. Journals the ruling as an
        absorption_adjudicated[] record whose baseline pin is the RUN
        COMMIT's content for the view -- exactly what the verifier graded
        and the operator ruled on -- so later verify passes diff updates
        from the adjudicated state, named as such, never from birth and
        never from a bare rejection.
      * --union-event (v3.0-105): a union no-op leg, identified by (run seq,
        event). The journaled entry's subject is the pseudo-view string
        `union:<event>` and it carries NO baseline pin fields -- a union leg
        absorbed nothing, so there is no content whose baseline could
        advance. What it pins instead is what the checker actually graded:
        the event hash and per-view union shas from the leg's own
        justification, plus the kept verdict artifact."""
    import hashlib
    repo = os.path.abspath(root)
    if not os.path.isdir(repo) or not _is_git_repo(repo):
        out("REFUSED: --root %s is not a git repository." % root)
        return EXIT_FAIL
    if not str(ruling or "").strip():
        out("REFUSED: --ruling is empty. The ruling is the operator's own "
            "words, recorded verbatim; nothing was journaled.")
        return EXIT_FAIL
    if not _worktree_clean(repo):
        out("REFUSED: the worktree is not clean. The adjudication record "
            "must land as its own stage-only commit; commit or stash first. "
            "Nothing was journaled.")
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
            out("REFUSED: run seq %d was reverted -- its absorption no "
                "longer stands, so there is no verdict left to set aside."
                % seq)
            return EXIT_FAIL
    # The adjudication subject: a real view path, or the union pseudo-view
    # string `union:<event>` (v3.0-105) -- the same key the verify ledger
    # and the trajectory drill already use for union rows, so one ruling
    # per verdict is enforced over one shared key space.
    subject = view if union_event is None else "union:%s" % union_event
    if union_event is None:
        absorbed_views = {a.get("view") for a in rec.get("absorbed") or []}
        if view not in absorbed_views:
            out("REFUSED: run seq %d absorbed no view %r (absorbed: %s)."
                % (seq, view,
                   ", ".join(sorted(v for v in absorbed_views if v))
                   or "(none)"))
            return EXIT_FAIL
    for r in recs.values():
        for aj in r.get("absorption_adjudicated") or []:
            if aj.get("view") == subject and aj.get("adjudicates_seq") == seq:
                out("REFUSED: seq %d's %s already carries an "
                    "operator adjudication (journal seq %s). One ruling per "
                    "verdict."
                    % (seq, ("absorption of %s" % view)
                       if union_event is None
                       else ("union leg for %s" % union_event), r.get("seq")))
                return EXIT_FAIL

    # The verdict being set aside must EXIST, be COMPLETE, and be a
    # NON-CONFIRM for this view. No verify record -> nothing to rule on;
    # incomplete legs -> transport failure, which is --reverify's case;
    # a confirmed leg -> nothing to adjudicate.
    vrecs = [r for r in recs.values() if r.get("verifies_seq") == seq]
    if not vrecs:
        out("REFUSED: no verify record covers run seq %d -- there is no "
            "verdict to set aside. (--set-aside adjudicates a real "
            "non-confirm verdict, never the absence of one.)" % seq)
        return EXIT_FAIL
    newest = max(vrecs, key=lambda r: r.get("seq", 0))
    legs = verify_record_legs(repo, newest)
    if legs["incomplete"]:
        out("REFUSED: %d verify leg(s) for run seq %d never completed -- a "
            "transport failure is not a verdict, so there is nothing to set "
            "aside. Re-fire the legs with --reverify instead."
            % (len(legs["incomplete"]), seq))
        return EXIT_FAIL
    if union_event is not None:
        # v3.0-105: the subject is the union leg for (seq, event). The
        # confirm-authority check transposed: a `verified: true` union leg
        # has nothing to adjudicate; an unverified entry with an artifact is
        # the leg the ledger and the drill already key `union:<event>`.
        ncs = [nc for nc in newest.get("noop_candidates") or []
               if nc.get("event") == union_event]
        if any(nc.get("verified") for nc in ncs):
            out("REFUSED: the union leg for %s CONFIRMED -- there is "
                "nothing to adjudicate." % union_event)
            return EXIT_FAIL
        candidates = [nc for nc in ncs
                      if not nc.get("verified") and nc.get("artifact")]
        if not candidates:
            out("REFUSED: run seq %d fired no unverified union leg for "
                "event %s (covering verify record seq %s). Nothing was "
                "journaled." % (seq, union_event, newest.get("seq")))
            return EXIT_FAIL
        attempt = candidates[0]
    else:
        if any(av.get("view") == view
               for av in newest.get("absorption_verified") or []):
            out("REFUSED: the verify leg for %s CONFIRMED -- there is "
                "nothing to adjudicate." % view)
            return EXIT_FAIL
        attempt = None
        for at in newest.get("absorption_verify_attempts") or []:
            if at.get("view") == view:
                attempt = at
                break
        if attempt is None:
            out("REFUSED: the covering verify record (seq %s) carries no "
                "non-confirm absorption leg for %s."
                % (newest.get("seq"), view))
            return EXIT_FAIL
    # v3.0-74: the confirm authority is the absorption_verified check above
    # (the journal's own stamp record) -- NOT the artifact's label. What
    # remains here is the completion axis only: journal-first (the demotion
    # fields where journaled, the artifact as legacy forensics), and a
    # COMPLETED attempts-leg is adjudicable regardless of its artifact's
    # label. When the artifact reads confirmed, the real state is named: the
    # verifier approved but the engine recorded no stamp (stamp-refused).
    jl = attempt.get("verdict_label")
    if jl is not None:
        completed, label = _journal_label_completed(jl), str(jl)
    else:
        art = attempt.get("artifact")
        verdict = _load_verdict_artifact(repo, art) if art else None
        completed, label = classify_verdict(verdict)
    if not completed:
        out("REFUSED: the verify leg for %s did not complete (%s) -- a "
            "transport failure is not a verdict, so there is nothing to "
            "set aside. Re-fire the legs with --reverify instead."
            % (subject, label))
        return EXIT_FAIL
    if _confirm_shaped(label):
        out("NOTE: the verifier approved %s but the engine recorded no "
            "stamp (stamp-refused) -- this ruling adjudicates that leg."
            % subject)

    core = _core()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    rc, head, _e = _git(repo, "rev-parse", "HEAD")
    arec = core.minimal_record("verify-adjudication", head.strip())
    arec["run_window"] = {"start": now, "end": now}
    if union_event is not None:
        # v3.0-105: NO baseline pin fields (baseline_commit / view_sha256
        # ABSENT) -- a union leg absorbed nothing, so there is no content
        # whose baseline could advance. What IS pinned is what the checker
        # graded: the event hash and per-view union shas from the leg's own
        # justification, plus the kept verdict artifact.
        just = attempt.get("justification") or {}
        arec["absorption_adjudicated"] = [{
            "view": subject, "union_event": union_event,
            "union_views": just.get("union_views")
            or sorted({nc.get("view") for nc in candidates
                       if nc.get("view")}),
            "event_sha256": just.get("event_sha256"),
            "union_view_sha256": just.get("union_view_sha256"),
            "adjudicates_seq": seq, "at": now,
            "ruling": ruling, "adjudicated_by": "operator",
            "rejected_artifact": attempt.get("artifact"),
            "driver": "deploy/compile-driver.py",
        }]
    else:
        run_sha = _find_run_commit(repo, seq)
        if not run_sha:
            out("REFUSED: could not locate the run commit that added "
                "receipts/journal/%d.json. Nothing was journaled." % seq)
            return EXIT_FAIL
        p = subprocess.run(["git", "-C", repo, "show",
                            "%s:%s" % (run_sha, view)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if p.returncode != 0:
            out("REFUSED: git show %s:%s failed (%s) -- the adjudicated "
                "baseline content could not be pinned. Nothing was journaled."
                % (run_sha[:12], view, (p.stderr or "").strip()[-160:]))
            return EXIT_FAIL
        view_sha = hashlib.sha256(p.stdout.encode("utf-8")).hexdigest()
        arec["absorption_adjudicated"] = [{
            "view": view, "adjudicates_seq": seq, "at": now,
            "ruling": ruling, "adjudicated_by": "operator",
            "rejected_artifact": attempt.get("artifact"),
            "baseline_commit": run_sha, "view_sha256": view_sha,
            "driver": "deploy/compile-driver.py",
        }]
    aseq, jpath = core.append_record(repo, arec)
    jrel = os.path.relpath(jpath, repo).replace(os.sep, "/")
    core.stage_only_commit(
        repo, [jrel],
        "compile-driver: operator set-aside of seq %d verdict on %s "
        "(adjudication journaled at seq %d)" % (seq, subject, aseq))
    if union_event is not None:
        out("SET-ASIDE RECORDED: the operator's ruling on the union leg for "
            "%s (run seq %d) is journaled at seq %d, with the rejected "
            "verdict kept on the record (%s). No baseline moves -- a union "
            "leg absorbed nothing; the ledger row reads set-aside and the "
            "drill state reads ADJUDICATED."
            % (union_event, seq, aseq,
               attempt.get("artifact") or "no artifact path"))
    else:
        out("SET-ASIDE RECORDED: the operator's ruling on %s (run seq %d) is "
            "journaled at seq %d, with the rejected verdict kept on the "
            "record (%s). The view's verify baseline now advances to this "
            "adjudicated state -- future verify packets will name it "
            "'adjudicated %s by operator ruling, not machine-verified'."
            % (view, seq, aseq,
               attempt.get("artifact") or "no artifact path", now[:10]))
    return EXIT_OK


# --------------------------------------------------------------- baseline-reset
def _newest_baseline_stamp(recs, view):
    """The view's newest baseline stamp across the ladder's three rungs
    (machine-verified / adjudicated / baseline-reset), newest by JOURNAL seq
    -- the same competition _absorption_trigger_state runs. Returns
    (journal_seq, kind, pinned_commit) or (None, None, None). Union
    adjudication entries (carrying `union_event`) are skipped: they pin no
    content, so they are not baseline stamps (v3.0-105 / the cross-check
    correction)."""
    newest = (None, None, None)
    for seq in sorted(recs):
        rec = recs[seq]
        for av in rec.get("absorption_verified") or []:
            if av.get("view") == view:
                newest = (seq, "machine-verified", av.get("verify_commit"))
        for aj in rec.get("absorption_adjudicated") or []:
            if aj.get("union_event"):
                continue
            if aj.get("view") == view:
                newest = (seq, "adjudicated", aj.get("baseline_commit"))
        for br in rec.get("baseline_reset") or []:
            if br.get("view") == view:
                newest = (seq, "baseline-reset", br.get("refresh_commit"))
    return newest


def execute_baseline_reset(root, view, views_file, refresh_commit,
                           provenance, ruling, out=print):
    """Operator baseline reset for out-of-engine refreshes (v3.0.39, closing
    backlog v3.0-106; see the module docstring's BASELINE-RESET section for
    the doctrine and the named guard chain G1-G7). Appends ONE
    `baseline-reset` journal record: baseline_reset[] entries for every view
    that passed the guards, plus a refused[] list naming every view that did
    not and why -- journal-only truth includes the refusals. A reset
    adjudicates NOTHING (no ledger row, no leg state moves) and pins only
    content already in history at a commit the engine did not author."""
    import hashlib
    repo = os.path.abspath(root)
    if not os.path.isdir(repo) or not _is_git_repo(repo):
        out("REFUSED: --root %s is not a git repository." % root)
        return EXIT_FAIL
    # G1 -- operator words or nothing.
    if not str(ruling or "").strip():
        out("REFUSED: --ruling is empty. The ruling is the operator's own "
            "words, recorded verbatim; nothing was journaled.")
        return EXIT_FAIL
    if not str(provenance or "").strip():
        out("REFUSED: --provenance is empty. A reset must name what was "
            "imported, when, and from where -- scope-locked to declared "
            "imports (G1); nothing was journaled.")
        return EXIT_FAIL
    # G2 -- worktree clean; the record lands as its own stage-only commit.
    if not _worktree_clean(repo):
        out("REFUSED: the worktree is not clean. The baseline-reset record "
            "must land as its own stage-only commit; commit or stash first. "
            "Nothing was journaled.")
        return EXIT_FAIL

    if views_file is not None:
        vf = views_file if os.path.isabs(views_file) \
            else os.path.join(repo, views_file)
        try:
            lines = open(vf, encoding="utf-8").read().splitlines()
        except OSError as e:
            out("REFUSED: --views-file %s could not be read (%s). Nothing "
                "was journaled." % (views_file, e))
            return EXIT_FAIL
        views = [ln.strip() for ln in lines if ln.strip()]
        if not views:
            out("REFUSED: --views-file %s names no views. Nothing was "
                "journaled." % views_file)
            return EXIT_FAIL
    else:
        views = [view]

    # G3 -- the refresh commit must exist and be an ancestor of HEAD;
    # fail-closed on any git error.
    rc, full, err = _git(repo, "rev-parse", "--verify",
                         "%s^{commit}" % refresh_commit)
    if rc != 0 or not full.strip():
        out("REFUSED (G3): --refresh-commit %s does not name a commit in "
            "this repository (%s). Nothing was journaled."
            % (refresh_commit, (err or "").strip()[-160:]))
        return EXIT_FAIL
    full = full.strip()
    rc, _o, err = _git(repo, "merge-base", "--is-ancestor", full, "HEAD")
    if rc != 0:
        out("REFUSED (G3): --refresh-commit %s is not an ancestor of HEAD "
            "(or ancestry could not be determined: %s) -- fail-closed. "
            "Nothing was journaled."
            % (refresh_commit, (err or "").strip()[-160:] or "not an "
               "ancestor"))
        return EXIT_FAIL
    # G4 -- the refresh commit must not be engine-authored history: a commit
    # that touches any receipts/journal/<seq>.json is a journaled run/record
    # commit, never "an import" (the narrow-scope rule made mechanical).
    jre = re.compile(r"^receipts/journal/\d+\.json$")
    if any(jre.match(f) for f in _commit_files(repo, full)):
        out("REFUSED (G4): --refresh-commit %s touches receipts/journal/ -- "
            "engine-authored history is never an import. A reset pins a "
            "declared out-of-engine refresh, identified by a commit the "
            "engine did not author. Nothing was journaled." % refresh_commit)
        return EXIT_FAIL

    recs = load_journal(repo)
    entries, refused = [], []
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for v in views:
        # Per-view guards run in the spec's refusal order: G5, G6, G7 --
        # for EVERY line, repeats included (a repeated line re-earns its
        # refusal under whichever guard fires first).
        # G5 -- the view must exist at the refresh commit; its bytes THERE
        # are what gets pinned -- never the current worktree.
        p = subprocess.run(["git", "-C", repo, "show", "%s:%s" % (full, v)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if p.returncode != 0:
            refused.append({"view": v, "guard": "G5",
                            "reason": "not present at the refresh commit "
                                      "(%s)" % (p.stderr or "")
                            .strip()[-120:]})
            continue
        vsha = hashlib.sha256(p.stdout.encode("utf-8")).hexdigest()
        # G6 -- no rewind: only a stamp whose pinned commit is a STRICT
        # ancestor of the refresh commit (or no stamp at all) proceeds.
        # Descendant, identical, or incomparable-ancestry stamps refuse;
        # fail-closed when ancestry cannot be determined.
        sseq, skind, scommit = _newest_baseline_stamp(recs, v)
        if sseq is not None:
            if not scommit:
                refused.append({"view": v, "guard": "G6",
                                "reason": "newest stamp (%s, journal seq %d) "
                                          "pins no commit -- ancestry "
                                          "cannot be determined, fail-closed"
                                          % (skind, sseq)})
                continue
            rc, sfull, _e = _git(repo, "rev-parse", "--verify",
                                 "%s^{commit}" % scommit)
            if rc != 0:
                refused.append({"view": v, "guard": "G6",
                                "reason": "newest stamp's pinned commit %s "
                                          "is unresolvable -- ancestry "
                                          "cannot be determined, fail-closed"
                                          % scommit})
                continue
            sfull = sfull.strip()
            if sfull == full:
                refused.append({"view": v, "guard": "G6",
                                "reason": "newest stamp (%s, journal seq %d) "
                                          "already pins the refresh commit "
                                          "itself" % (skind, sseq)})
                continue
            rc, _o, _e = _git(repo, "merge-base", "--is-ancestor",
                              sfull, full)
            if rc != 0:
                refused.append({"view": v, "guard": "G6",
                                "reason": "baseline already advanced past "
                                          "(or independently of) the "
                                          "refresh commit -- newest stamp "
                                          "(%s, journal seq %d) pins %s, "
                                          "not a strict ancestor of the "
                                          "refresh commit; a reset would "
                                          "regress it"
                                          % (skind, sseq, sfull[:12])})
                continue
        # G7 -- one reset per (view, refresh_commit); a LATER photograph is
        # a different refresh_commit and may reset again. The duplicate scan
        # covers the journal AND the entries already accepted THIS
        # invocation, so a view repeated inside one --views-file refuses
        # here rather than journaling the same pair twice.
        dup = None
        for r in recs.values():
            for br in r.get("baseline_reset") or []:
                if br.get("view") == v \
                        and br.get("refresh_commit") == full:
                    dup = r.get("seq")
        if dup is not None:
            refused.append({"view": v, "guard": "G7",
                            "reason": "already reset at this refresh commit "
                                      "(journal seq %s) -- one ruling per "
                                      "fact" % dup})
            continue
        if any(e["view"] == v for e in entries):
            refused.append({"view": v, "guard": "G7",
                            "reason": "duplicate view in this views-file -- "
                                      "the pair already resets in this "
                                      "record; one ruling per fact"})
            continue
        entries.append({"view": v, "at": now,
                        "refresh_commit": full, "view_sha256": vsha,
                        "provenance": provenance, "ruling": ruling,
                        "reset_by": "operator",
                        "driver": "deploy/compile-driver.py"})

    for rj in refused:
        out("REFUSED (%s) %s: %s" % (rj["guard"], rj["view"], rj["reason"]))
    if not entries:
        out("REFUSED: no view passed the guards -- nothing was journaled "
            "(%d refusal(s) above)." % len(refused))
        return EXIT_FAIL

    core = _core()
    rc, head, _e = _git(repo, "rev-parse", "HEAD")
    brec = core.minimal_record("baseline-reset", head.strip())
    brec["run_window"] = {"start": now, "end": now}
    brec["baseline_reset"] = entries
    brec["refused"] = refused
    aseq, jpath = core.append_record(repo, brec)
    jrel = os.path.relpath(jpath, repo).replace(os.sep, "/")
    core.stage_only_commit(
        repo, [jrel],
        "compile-driver: operator baseline-reset of %d view(s) at refresh "
        "commit %s (journaled at seq %d; %d refusal(s) on the record)"
        % (len(entries), full[:12], aseq, len(refused)))
    out("BASELINE-RESET RECORDED: %d view(s) reset at refresh commit %s, "
        "journaled at seq %d with provenance and ruling verbatim%s. A reset "
        "closes no verdict row and verifies nothing -- future verify packets "
        "open '(baseline: reset to imported snapshot by operator ruling, "
        "not machine-verified -- ...)'."
        % (len(entries), full[:12], aseq,
           ("; %d view(s) refused, named in the record" % len(refused))
           if refused else ""))
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
                   text=GRANT, commit=True):
        rel = "deploy/evidence/" + name
        write(os.path.join(repo, "deploy", "evidence", name), text)
        if commit:  # v3.0-120: an uncommitted grant is refused by check (g)
            _git(repo, "add", "--", rel)
            _git(repo, "commit", "-q", "-m", "grant %s" % name, "--", rel)
        return rel

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
        # verifier demotion (2026-08-09): post-demotion verdicts, as the
        # ENGINE would journal them (compile-v2 classify_reason_classes is
        # battery-tested there; the FakeEngine mirrors its record-time
        # journaling below so THIS battery pins the driver's split).
        "rejected-scope": {"verdict": "rejected",
                           "reason": "scope-omission: the rho claim is "
                                     "not represented",
                           "reason_classes": ["scope-omission"]},
        "rejected-enum": {"verdict": "rejected",
                          "reason": "reason class: enumeration-incomplete",
                          "reason_classes": ["enumeration-incomplete"]},
        "rejected-fab": {"verdict": "rejected",
                         "reason": "fabrication: asserts what no event "
                                   "supports",
                         "reason_classes": ["fabrication"]},
        "rejected-mixed-leg": {"verdict": "rejected",
                               "reason": "scope-omission and fabrication "
                                         "on one leg",
                               "reason_classes": ["scope-omission",
                                                  "fabrication"]},
        "rejected-classless-new": {"verdict": "rejected",
                                   "reason": "no class token anywhere",
                                   "reason_classes": []},
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
            # verifier demotion (2026-08-09): self.verify may be a LIST of
            # fixture keys -> one leg per key (mixed-run fixtures). A single
            # key keeps the original one-leg shape byte-identical.
            keys = (list(self.verify)
                    if isinstance(self.verify, (list, tuple))
                    else [self.verify])
            checked = len(keys) if self.absorption_checked is None \
                else self.absorption_checked
            vrec = core.minimal_record("verify", "0" * 40)
            vrec["verifies_seq"] = seq
            vrec["run_window"] = {"start": "t0", "end": "t1"}
            artifacts = []
            verified, attempts = [], []
            confirmed = 0
            if checked:
                for v_idx, key in enumerate(keys):
                    verdict = VERDICTS[key]
                    is_confirm = str(verdict.get("verdict", "")).startswith(
                        "confirm")
                    view = self.view if v_idx == 0 \
                        else "wiki/leg%d.md" % v_idx
                    art_rel = "receipts/verify/absorb-seq%d-v%d.json" % (
                        seq, v_idx)
                    ap = os.path.join(repo, art_rel.replace("/", os.sep))
                    os.makedirs(os.path.dirname(ap), exist_ok=True)
                    with open(ap, "w", encoding="utf-8",
                              newline="\n") as fh:
                        json.dump(verdict, fh, indent=1, sort_keys=True)
                    artifacts.append(art_rel)
                    entry = {"view": view, "events": ["raw/e1.md"],
                             "artifact": art_rel, "packet_sha256": "d" * 64}
                    if is_confirm:
                        entry["verified_at"] = "t1"
                        verified.append(entry)
                        confirmed += 1
                    else:
                        entry["reason"] = verdict.get("reason", "")
                        # Mirror the ENGINE's record-time journaling
                        # (classify_reason_classes, battery-tested in
                        # compile-v2) for post-demotion fixture keys: a
                        # verdict carrying `reason_classes` journals the
                        # three fields; the plain legacy keys journal none
                        # (the pre-demotion record shape).
                        rcs = verdict.get("reason_classes")
                        if rcs is not None:
                            norm = [str(c) for c in rcs] or ["unclassified"]
                            entry["verdict_label"] = str(
                                verdict.get("verdict"))
                            entry["reason_classes"] = norm
                            entry["disposition"] = (
                                "recorded" if all(
                                    c in ("scope-omission",
                                          "enumeration-incomplete")
                                    for c in norm) else "blocking")
                        attempts.append(entry)
                if verified:
                    vrec["absorption_verified"] = verified
                if attempts:
                    vrec["absorption_verify_attempts"] = attempts
            vseq, jpath = core.append_record(repo, vrec)
            jrel = os.path.relpath(jpath, repo).replace(os.sep, "/")
            core.stage_only_commit(repo, artifacts + [jrel],
                                   "fake verify seq %d over %d" % (vseq, seq))
            return {"sha": "x", "seq": vseq, "confirmed": 0, "checked": 0,
                    "events_checked": 0, "events_confirmed": 0,
                    "absorption_checked": checked,
                    "absorption_confirmed": confirmed,
                    "absorption_attempts": [
                        {"view": a.get("view", ""),
                         "reason": a.get("reason", ""),
                         "verdict_label": a.get("verdict_label"),
                         "reason_classes": a.get("reason_classes"),
                         "disposition": a.get("disposition")}
                        for a in attempts]}

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
                    "view": None, "ruling": None, "since": None,
                    "sections": True, "union-event": None,
                    "refresh-commit": None, "provenance": None,
                    "views-file": None},
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
        # ------------------------------ (g) trust-surface integrity (v3.0-120)
        case("(g) committed, unsigned grant under the default warn mode is ACCEPTED "
             "with a surfaced trust warning",
             "trust_warnings" in auth and len(auth["trust_warnings"]) == 1
             and "not operator-signed" in auth["trust_warnings"][0], auth)
        unc = make_grant(repo_b, "operator-uncommitted-2026-07-28.md", commit=False)
        try:
            validate_authorization(repo_b, unc, class_check=class_check_local)
            case("(g) an UNCOMMITTED grant is refused even under warn", False)
        except AuthorizationError as e:
            case("(g) an UNCOMMITTED grant is refused even under warn",
                 "not committed-identical" in str(e) and "not tracked" in str(e), str(e))
        write(os.path.join(repo_b, "deploy", "evidence",
                           "operator-standing-verify-2026-07-28.md"), GRANT + "\nedited\n")
        try:
            validate_authorization(repo_b, good, class_check=class_check_local)
            case("(g) a committed grant whose worktree DIFFERS from HEAD is refused", False)
        except AuthorizationError as e:
            case("(g) a committed grant whose worktree DIFFERS from HEAD is refused",
                 "differs from HEAD" in str(e), str(e))
        _git(repo_b, "checkout", "-q", "--", good)
        write(os.path.join(repo_b, "project.yaml"), "trust_surface_signing: required\n")
        try:
            validate_authorization(repo_b, good, class_check=class_check_local)
            case("(g) under trust_surface_signing: required an unsigned grant is REFUSED",
                 False)
        except AuthorizationError as e:
            case("(g) under trust_surface_signing: required an unsigned grant is REFUSED",
                 "not operator-signed" in str(e) and "required" in str(e), str(e))
        os.remove(os.path.join(repo_b, "project.yaml"))
        stub_calls = []

        def gate_stub(repo, rel):
            stub_calls.append(rel)
            rc, out, _ = _git(repo, "show", "HEAD:" + rel)
            return {"ok": True, "warnings": [], "refuse": None, "blob": out.encode("utf-8")}
        a2 = validate_authorization(repo_b, good, class_check=class_check_local,
                                    trust_gate=gate_stub)
        case("(g) the trust gate is called with the repo-relative posix path and its "
             "verdict is honored", stub_calls == [good] and a2["trust_warnings"] == [])

        def gate_stub_noblob(repo, rel):
            return {"ok": True, "warnings": [], "refuse": None}
        try:
            validate_authorization(repo_b, good, class_check=class_check_local,
                                   trust_gate=gate_stub_noblob)
            case("(g) a gate that returns no verified bytes is refused -- the driver never "
                 "falls back to parsing the working-tree file (round-12 swap window)", False)
        except AuthorizationError as e:
            case("(g) a gate that returns no verified bytes is refused -- the driver never "
                 "falls back to parsing the working-tree file (round-12 swap window)",
                 "no verified bytes" in str(e), str(e))

        def gate_stub_swapped(repo, rel):
            # the gate verified HEAD's bytes; the file on disk says something else
            write(os.path.join(repo, rel), GRANT.replace("yes to all 3", "yes to EVERYTHING"))
            rc, out, _ = _git(repo, "show", "HEAD:" + rel)
            return {"ok": True, "warnings": [], "refuse": None, "blob": out.encode("utf-8")}
        a3 = validate_authorization(repo_b, good, class_check=class_check_local,
                                    trust_gate=gate_stub_swapped)
        case("(g) the quote comes from the gate's verified blob, not from the (swapped) file "
             "on disk", a3["quote"] == "yes to all 3", a3)
        _git(repo_b, "checkout", "-q", "--", good)
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

    # ------------- P. verifier demotion (2026-08-09): the exit split
    # ACCEPTANCE (1): a scope-class rejection ABSORBS, journals `recorded`,
    # and the run completes -- exit 0 with the mandatory band.
    repo_p1 = make_repo("cdrv-demotion-rec-")
    try:
        st = make_staging(repo_p1)
        good = make_grant(repo_p1)
        _git(repo_p1, "add", "-A")
        _git(repo_p1, "commit", "-qm", "fixtures")
        buf = []
        rc = run_driver(repo_p1, st, good,
                         engine=FakeEngine(verify="rejected-scope"),
                         sensors=quiet_sensors, out=buf.append)
        text = "\n".join(str(b) for b in buf)
        recs_p1 = load_journal(repo_p1)
        att_p1 = [a for r in recs_p1.values()
                  for a in r.get("absorption_verify_attempts") or []]
        case("ACCEPTANCE (1): scope-class rejection -> exit 0, absorption "
             "stands (no revert), disposition journaled `recorded`",
             rc == EXIT_OK
             and "absorbed line" in open(
                 os.path.join(repo_p1, "wiki", "a.md"),
                 encoding="utf-8").read()
             and not any(r.get("driver_revert") for r in recs_p1.values())
             and att_p1 and att_p1[0].get("disposition") == "recorded",
             rc)
        case("ACCEPTANCE (1): the RECORDED SIGNALS band is mandatory and "
             "names the leg, its class, and Step 3c",
             "VERIFY RECORDED SIGNALS" in text
             and "recorded signal: wiki/a.md -- [scope-omission]" in text
             and "Step 3c" in text
             and "signal declared away" in text, text[:400])
        case("ACCEPTANCE (1): no verified stamp -- the article is live but "
             "NOT machine-verified (baseline advances on nothing)",
             not any(r.get("absorption_verified")
                     for r in recs_p1.values()))
        # the redo verb: --revert accepts a recorded-only run unchanged
        rc_rv = execute_revert(repo_p1, _run_seqs(recs_p1)[-1], out=silent)
        case("recorded-only run: `--revert` (the operator's REDO) accepts "
             "it -- semantics byte-identical",
             rc_rv == EXIT_OK
             and any(r.get("driver_revert")
                     for r in load_journal(repo_p1).values()))
    finally:
        shutil.rmtree(repo_p1, ignore_errors=True)

    # ACCEPTANCE (2): fabrication-class still refuses -- exit 1, no revert,
    # the pre-demotion path byte-identical.
    # ACCEPTANCE (3): a classless verdict BLOCKS (fail-closed) -- both the
    # post-demotion `unclassified` record and the legacy no-fields record.
    for key, why in (
        ("rejected-fab", "fabrication-class: still refuses (exit 1)"),
        ("rejected-mixed-leg", "mixed classes on one leg: blocking wins"),
        ("rejected-classless-new",
         "classless verdict journals unclassified and BLOCKS (fail-closed)"),
        ("rejected", "legacy record shape (no class fields) still BLOCKS"),
    ):
        repo_p2 = make_repo("cdrv-demotion-blk-")
        try:
            st = make_staging(repo_p2)
            good = make_grant(repo_p2)
            _git(repo_p2, "add", "-A")
            _git(repo_p2, "commit", "-qm", "fixtures")
            buf = []
            rc = run_driver(repo_p2, st, good, engine=FakeEngine(verify=key),
                             sensors=quiet_sensors, out=buf.append)
            text = "\n".join(str(b) for b in buf)
            case("demotion: %s" % why,
                 rc == EXIT_FAIL
                 and "VERIFY NON-CONFIRM" in text
                 and "VERIFY RECORDED SIGNALS" not in text
                 and not any(r.get("driver_revert")
                             for r in load_journal(repo_p2).values()), rc)
        finally:
            shutil.rmtree(repo_p2, ignore_errors=True)

    # Mixed RUN: one blocking + one recorded leg -> blocks, but the recorded
    # sibling's signal is named, not swallowed.
    repo_p3 = make_repo("cdrv-demotion-mix-")
    try:
        st = make_staging(repo_p3)
        good = make_grant(repo_p3)
        _git(repo_p3, "add", "-A")
        _git(repo_p3, "commit", "-qm", "fixtures")
        buf = []
        rc = run_driver(repo_p3, st, good,
                         engine=FakeEngine(
                             verify=["rejected-fab", "rejected-scope"]),
                         sensors=quiet_sensors, out=buf.append)
        text = "\n".join(str(b) for b in buf)
        case("mixed run: blocking leg governs (exit 1); recorded sibling "
             "still named for Step 3c",
             rc == EXIT_FAIL and "VERIFY NON-CONFIRM" in text
             and "recorded-class signal(s)" in text
             and "recorded signal: wiki/leg1.md -- [scope-omission]" in text,
             rc)
    finally:
        shutil.rmtree(repo_p3, ignore_errors=True)

    # Recorded-only, multi-leg + the ACCEPT verb + the ledger.
    repo_p4 = make_repo("cdrv-demotion-ledger-")
    try:
        st = make_staging(repo_p4)
        good = make_grant(repo_p4)
        _git(repo_p4, "add", "-A")
        _git(repo_p4, "commit", "-qm", "fixtures")
        rc = run_driver(repo_p4, st, good,
                         engine=FakeEngine(
                             verify=["rejected-scope", "rejected-enum"]),
                         sensors=quiet_sensors, out=silent)
        run_seq_p4 = _run_seqs(load_journal(repo_p4))[-1]
        case("recorded-only multi-leg run completes (exit 0)",
             rc == EXIT_OK, rc)
        buf = []
        rc_lg = execute_verify_ledger(repo_p4, out=buf.append)
        text = "\n".join(str(b) for b in buf)
        case("--verify-ledger: journal-only rows, both legs open-recorded",
             rc_lg == EXIT_OK and text.count("open-recorded") == 2
             and "scope-omission" in text
             and "enumeration-incomplete" in text, text)
        rc_sa = execute_set_aside(repo_p4, run_seq_p4, "wiki/a.md",
                                  "operator fixture ruling", out=silent)
        buf = []
        execute_verify_ledger(repo_p4, out=buf.append)
        text = "\n".join(str(b) for b in buf)
        case("recorded leg: `--set-aside` (the operator's ACCEPT) works "
             "unchanged, and the ledger's outcome flips to set-aside",
             rc_sa == EXIT_OK and "set-aside" in text
             and "accepted=1" in text and text.count("open-recorded") == 1,
             text)
        case("--verify-ledger --since after every record filters to "
             "nothing, honestly",
             execute_verify_ledger(repo_p4, since="2099-01-01",
                                   out=silent) == EXIT_OK)
    finally:
        shutil.rmtree(repo_p4, ignore_errors=True)

    # Legacy legs: listed, excluded from agreement, named as excluded.
    repo_p5 = make_repo("cdrv-demotion-legacy-")
    try:
        st = make_staging(repo_p5)
        good = make_grant(repo_p5)
        _git(repo_p5, "add", "-A")
        _git(repo_p5, "commit", "-qm", "fixtures")
        run_driver(repo_p5, st, good, engine=FakeEngine(verify="rejected"),
                    sensors=quiet_sensors, out=silent)
        buf = []
        execute_verify_ledger(repo_p5, out=buf.append)
        text = "\n".join(str(b) for b in buf)
        case("--verify-ledger: a legacy leg is listed as (legacy)/"
             "unclassified and excluded from the agreement rate",
             "(legacy)" in text and "legacy leg(s) excluded" in text, text)
    finally:
        shutil.rmtree(repo_p5, ignore_errors=True)

    try:
        parse_args(["--verify-ledger"])
        case("--verify-ledger without --root refuses", False)
    except UsageError:
        case("--verify-ledger without --root refuses", True)
    try:
        parse_args(["--verify-ledger", "--root", ".", "--since", "yesterday"])
        case("--since takes YYYY-MM-DD only", False)
    except UsageError:
        case("--since takes YYYY-MM-DD only", True)

    # ---------- N. the seq-103 state: verify record whose legs never completed
    def plant_seq103(repo, verdict_key="bridge-error", stamped=False):
        """Reproduce the pre-fix live state by hand: a compile run commit, then
        a verify record whose leg(s) carry the named verdict(s), with NO
        revert. This is exactly what journal seq 103 looks like.
        verdict_key may be a tuple -> one legacy attempts-leg per key (views
        wiki/a.md, wiki/leg1.md, ...). stamped=True journals the single leg
        as absorption_verified[] instead -- the genuinely-confirmed record
        shape (v3.0-74: the journal's own stamp)."""
        core = _core()
        keys = (list(verdict_key)
                if isinstance(verdict_key, (list, tuple)) else [verdict_key])
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
        art_rels, entries = [], []
        for v_idx, key in enumerate(keys):
            art_rel = "receipts/verify/absorb-seq%d-v%d.json" % (cseq, v_idx)
            ap = os.path.join(repo, art_rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(ap), exist_ok=True)
            with open(ap, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(VERDICTS[key], fh, indent=1, sort_keys=True)
            art_rels.append(art_rel)
            view = "wiki/a.md" if v_idx == 0 else "wiki/leg%d.md" % v_idx
            entries.append({"view": view, "events": ["raw/e1.md"],
                            "artifact": art_rel, "packet_sha256": "d" * 64,
                            "reason": VERDICTS[key].get("reason", "")})
        vrec = core.minimal_record("verify", "0" * 40)
        vrec["verifies_seq"] = cseq
        if stamped:
            vrec["absorption_verified"] = [
                dict(entries[0], verified_at="t1")]
        else:
            vrec["absorption_verify_attempts"] = entries
        vseq, vpath = core.append_record(repo, vrec)
        core.stage_only_commit(
            repo, art_rels
            + [os.path.relpath(vpath, repo).replace(os.sep, "/")],
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

    # ---------- N2b. v3.0-74 narrow gate: --reverify reopens a TERMINAL run
    # whose newest covering record's open legs are ALL stamp-refused-shaped;
    # a genuine rejection (or a mixed run) stays declined -- re-firing legs
    # against a standing rejection would re-roll a verdict, which
    # no-self-adjudication forbids. Re-firing a discarded APPROVAL re-rolls
    # nothing, and the recovery ends machine-verified through the one
    # existing stamping path.
    repo_n3 = make_repo("cdrv-reverify-narrow-")
    try:
        st = make_staging(repo_n3)
        good = make_grant(repo_n3)
        _git(repo_n3, "add", "-A")
        _git(repo_n3, "commit", "-qm", "fixtures")
        cseq_n3 = plant_seq103(repo_n3, "confirm")   # discarded approval
        # Terminality invariance (design K1/sec.7.3 -- the cross-check's
        # demanded operational proof): the relabel moves the CONFIRMATION
        # axis only; the leg stays completed/non-incomplete, so the run
        # stays TERMINAL and reconciliation blocks nothing new.
        case("terminality invariance: a discarded-approval run is TERMINAL "
             "under the relabel (completed, non-incomplete)",
             reconcile_state(repo_n3)["blocked"] is False)
        vrec_n3 = max((r for r in load_journal(repo_n3).values()
                       if r.get("verifies_seq") == cseq_n3),
                      key=lambda r: r.get("seq", 0))
        legs_n3 = verify_record_legs(repo_n3, vrec_n3)
        case("terminality invariance: the relabelled leg reads "
             "stamp-refused(confirmed)/completed/not-stamped and the "
             "incomplete list is empty",
             legs_n3["incomplete"] == []
             and len(legs_n3["legs"]) == 1
             and legs_n3["legs"][0]["label"] == "stamp-refused(confirmed)"
             and legs_n3["legs"][0]["completed"] is True
             and legs_n3["legs"][0]["stamped"] is False, str(legs_n3))
        eng_n3 = FakeEngine(verify="confirm")
        buf_n3 = []
        rc = reverify_driver(repo_n3, cseq_n3, st, good, engine=eng_n3,
                             out=buf_n3.append)
        case("--reverify REOPENS (narrow gate) on the stamp-refused-only "
             "terminal run and dispatches",
             rc == EXIT_OK and eng_n3.verify_calls == 1
             and any("narrow gate" in l for l in buf_n3), "; ".join(buf_n3))
        recs_n3 = load_journal(repo_n3)
        case("...the recovery ends MACHINE-VERIFIED: a fresh "
             "absorption_verified record now covers the run",
             any(r.get("verifies_seq") == cseq_n3
                 and r.get("absorption_verified")
                 for r in recs_n3.values()))
        eng_probe3 = FakeEngine()
        rc = reverify_driver(repo_n3, cseq_n3, st, good, engine=eng_probe3,
                             out=silent)
        case("...and the gate CLOSES after the recovery (no-op again, no "
             "dispatch)", rc == EXIT_OK and eng_probe3.verify_calls == 0)
        # Supersession (design sec.2.3): the old attempts-row's open reading
        # is superseded by the later covering record's stamp for the same
        # (run, view) -- ledger outcome superseded-confirmed, no other row
        # moved.
        buf_lg = []
        execute_verify_ledger(repo_n3, out=buf_lg.append)
        sup_rows = [l for l in buf_lg if "superseded-confirmed" in l]
        case("--verify-ledger: the superseded open reading flips to "
             "superseded-confirmed (exactly one such row)",
             len(sup_rows) == 1 and "wiki/a.md" in sup_rows[0],
             "; ".join(buf_lg))
        case("--verify-ledger: no open-blocking row remains for the "
             "recovered run",
             not any("open-blocking" in l for l in buf_lg), "; ".join(buf_lg))
        # A genuine rejection stays declined by the narrow gate...
        cseq_rej = plant_seq103(repo_n3, "rejected")
        eng_probe4 = FakeEngine()
        rc = reverify_driver(repo_n3, cseq_rej, st, good, engine=eng_probe4,
                             out=silent)
        case("--reverify stays SHUT on a genuine rejection (terminal, no "
             "dispatch -- no verdict re-roll)",
             rc == EXIT_OK and eng_probe4.verify_calls == 0)
        # ...and so does a MIXED run (a stamp-refused leg beside a rejection).
        cseq_mx = plant_seq103(repo_n3, ("confirm", "rejected"))
        eng_probe5 = FakeEngine()
        rc = reverify_driver(repo_n3, cseq_mx, st, good, engine=eng_probe5,
                             out=silent)
        case("--reverify stays SHUT on a mixed run (stamp-refused beside a "
             "rejection, no dispatch)",
             rc == EXIT_OK and eng_probe5.verify_calls == 0)
        case("journal chain intact across the narrow-gate sequence",
             _core().check_chain(repo_n3) == len(load_journal(repo_n3)))
    finally:
        shutil.rmtree(repo_n3, ignore_errors=True)

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
        # v3.0-74: confirmed iff STAMPED. A run whose leg holds an
        # absorption_verified entry (the journal's own record) is fully
        # confirmed and stays refused -- the never-loosen direction.
        cseq_q = plant_seq103(repo_q, "confirm", stamped=True)
        rc = execute_revert(repo_q, cseq_q, out=silent)
        case("--revert refuses a fully-confirmed (STAMPED) run (nothing to "
             "adjudicate)",
             rc == EXIT_FAIL
             and not any(r.get("driver_revert")
                         for r in load_journal(repo_q).values()))
        # v3.0-74 reopening: a discarded approval -- artifact says confirmed,
        # journal holds only an attempt -- is NOT confirmed; --revert
        # qualifies the run and names the real state in its disposition.
        cseq_q2 = plant_seq103(repo_q, "confirm")
        buf_q = []
        rc = execute_revert(repo_q, cseq_q2, out=buf_q.append)
        case("--revert REOPENS for the discarded-approval shape "
             "(stamp-refused(confirmed) named in the disposition)",
             rc == EXIT_OK
             and any("stamp-refused(confirmed)" in l for l in buf_q),
             "; ".join(buf_q))
        # ...and the substrate-gated confirm spelling counts too (design K2:
        # a bare startswith('confirm') filter would leave it invisible).
        cseq_q3 = plant_seq103(repo_q, "gated-confirm")
        buf_q3 = []
        rc = execute_revert(repo_q, cseq_q3, out=buf_q3.append)
        case("--revert REOPENS for the substrate-gated(confirmed) discarded "
             "shape",
             rc == EXIT_OK
             and any("stamp-refused(substrate-gated(confirmed))" in l
                     for l in buf_q3),
             "; ".join(buf_q3))
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

    # -------------------------------- O3. --set-aside (v3.0.29, v3.0-63/67)
    try:
        parse_args(["--set-aside", "--root", ".", "--seq", "7"])
        case("--set-aside without --view/--ruling refuses", False)
    except UsageError as e:
        case("--set-aside without --view/--ruling refuses",
             "--ruling" in str(e), str(e))
    p = parse_args(["--set-aside", "--root", "r", "--seq", "7",
                    "--view", "wiki/a.md", "--ruling", "let it stand"])
    case("--set-aside argv parses (mode/seq/view/ruling)",
         p["mode"] == "set-aside" and p["seq"] == 7
         and p["view"] == "wiki/a.md" and p["ruling"] == "let it stand", p)
    try:
        parse_args(["--set-aside", "--root", ".", "--seq", "7",
                    "--view", "v", "--ruling", "r", "--no-verify"])
        case("--set-aside cannot smuggle --no-verify either", False)
    except UsageError:
        case("--set-aside cannot smuggle --no-verify either", True)

    repo_sa = make_repo("cdrv-setaside-")
    try:
        _git(repo_sa, "add", "-A")
        _git(repo_sa, "commit", "-qm", "fixtures")
        case("--set-aside refuses an unknown seq",
             execute_set_aside(repo_sa, 9999, "wiki/a.md", "let it stand",
                               out=silent) == EXIT_FAIL)
        # transport-failure record: nothing to set aside, --reverify's case
        cseq_t = plant_seq103(repo_sa, "bridge-error")
        case("--set-aside refuses a transport-failure record (that is "
             "--reverify's case, not an adjudication)",
             execute_set_aside(repo_sa, cseq_t, "wiki/a.md",
                               "let it stand", out=silent) == EXIT_FAIL)
    finally:
        shutil.rmtree(repo_sa, ignore_errors=True)

    repo_sb = make_repo("cdrv-setaside2-")
    try:
        _git(repo_sb, "add", "-A")
        _git(repo_sb, "commit", "-qm", "fixtures")
        cseq_r = plant_seq103(repo_sb, "rejected")
        case("--set-aside refuses an empty ruling (unrecorded = standing "
             "memory)",
             execute_set_aside(repo_sb, cseq_r, "wiki/a.md", "   ",
                               out=silent) == EXIT_FAIL)
        case("--set-aside refuses a view the run never absorbed",
             execute_set_aside(repo_sb, cseq_r, "wiki/nope.md",
                               "let it stand", out=silent) == EXIT_FAIL)
        rc = execute_set_aside(repo_sb, cseq_r, "wiki/a.md",
                               "the checker misread the deprecation note; "
                               "the article is right -- let it stand",
                               out=silent)
        case("--set-aside on a completed REJECTED leg records the "
             "adjudication (exit 0)", rc == EXIT_OK)
        adj_recs = [r for r in load_journal(repo_sb).values()
                    if r.get("absorption_adjudicated")]
        case("--set-aside journals absorption_adjudicated with the "
             "ruling VERBATIM, the rejected artifact kept on record, and "
             "a real baseline pin",
             len(adj_recs) == 1
             and adj_recs[0]["absorption_adjudicated"][0]["ruling"]
             .startswith("the checker misread")
             and adj_recs[0]["absorption_adjudicated"][0][
                 "rejected_artifact"]
             and adj_recs[0]["absorption_adjudicated"][0][
                 "baseline_commit"]
             and adj_recs[0]["absorption_adjudicated"][0]["view_sha256"],
             adj_recs)
        case("--set-aside baseline pin matches the run commit's actual "
             "view content (git-show recomputed)",
             __import__("hashlib").sha256(subprocess.run(
                 ["git", "-C", repo_sb, "show",
                  "%s:wiki/a.md" % adj_recs[0]["absorption_adjudicated"][0][
                      "baseline_commit"]],
                 capture_output=True, text=True, encoding="utf-8")
                 .stdout.encode("utf-8")).hexdigest()
             == adj_recs[0]["absorption_adjudicated"][0]["view_sha256"])
        case("--set-aside refuses a SECOND ruling on the same view/seq "
             "(one ruling per verdict)",
             execute_set_aside(repo_sb, cseq_r, "wiki/a.md", "again",
                               out=silent) == EXIT_FAIL)
        case("reconciliation stays clean after --set-aside (the completed "
             "non-confirm was already terminal; the adjudication adds no "
             "blocking state)",
             reconcile_state(repo_sb)["blocked"] is False)
    finally:
        shutil.rmtree(repo_sb, ignore_errors=True)

    # ------------------ O4. --revert collision pre-check (v3.0-local-10)
    repo_rc = make_repo("cdrv-revert-collision-")
    try:
        make_staging(repo_rc)
        make_grant(repo_rc)
        _git(repo_rc, "add", "-A")
        _git(repo_rc, "commit", "-qm", "fixtures")
        cseq_rc = plant_seq103(repo_rc, "rejected")
        # NORMAL WORK: the project keeps going and edits the same article
        write(os.path.join(repo_rc, "wiki", "a.md"),
              "# View A\n\nbody\nabsorbed line\nlater normal edit\n")
        _git(repo_rc, "add", "-A")
        _git(repo_rc, "commit", "-qm", "normal work on the same article")
        head_before = _git(repo_rc, "rev-parse", "HEAD")[1].strip()
        rc = execute_revert(repo_rc, cseq_rc, out=silent)
        case("--revert REFUSES when the run is no longer the last word on "
             "its articles (v3.0-local-10: the re-ride recipe's step used "
             "to conflict here)", rc == EXIT_FAIL)
        case("...and the refusal wrote NOTHING -- no commit, no "
             "driver_revert record, no blocking state",
             _git(repo_rc, "rev-parse", "HEAD")[1].strip() == head_before
             and not any(r.get("driver_revert")
                         for r in load_journal(repo_rc).values())
             and reconcile_state(repo_rc)["blocked"] is False)
        case("...and the worktree is clean (no half-applied revert left "
             "behind)", _worktree_clean(repo_rc))
    finally:
        shutil.rmtree(repo_rc, ignore_errors=True)

    # the pre-check must NOT fire when the run IS still the last word
    repo_rd = make_repo("cdrv-revert-nocollision-")
    try:
        make_staging(repo_rd)
        make_grant(repo_rd)
        _git(repo_rd, "add", "-A")
        _git(repo_rd, "commit", "-qm", "fixtures")
        cseq_rd = plant_seq103(repo_rd, "rejected")
        rc = execute_revert(repo_rd, cseq_rd, out=silent)
        case("--revert still WORKS on an untouched run (the pre-check is "
             "not a blanket refusal)", rc == EXIT_OK
             and any(r.get("driver_revert", {}).get("status") == "reverted"
                     for r in load_journal(repo_rd).values()))
    finally:
        shutil.rmtree(repo_rd, ignore_errors=True)

    repo_sc = make_repo("cdrv-setaside3-")
    try:
        _git(repo_sc, "add", "-A")
        _git(repo_sc, "commit", "-qm", "fixtures")
        # v3.0-74: the confirm authority is the absorption_verified check --
        # a STAMPED leg stays refused; a discarded-approval attempts-leg is
        # adjudicable, with the real state (stamp-refused) named.
        cseq_st = plant_seq103(repo_sc, "confirm", stamped=True)
        case("--set-aside refuses a STAMPED (absorption_verified) leg "
             "(nothing to adjudicate)",
             execute_set_aside(repo_sc, cseq_st, "wiki/a.md",
                               "let it stand", out=silent) == EXIT_FAIL)
        cseq_c = plant_seq103(repo_sc, "confirm")
        buf_sa = []
        rc = execute_set_aside(repo_sc, cseq_c, "wiki/a.md",
                               "the on-file approval is genuine",
                               out=buf_sa.append)
        case("--set-aside ADJUDICATES the discarded-approval leg (v3.0-74 "
             "zero-dispatch recovery), naming stamp-refused",
             rc == EXIT_OK
             and any("stamp-refused" in l for l in buf_sa)
             and any(aj.get("adjudicates_seq") == cseq_c
                     for r in load_journal(repo_sc).values()
                     for aj in r.get("absorption_adjudicated") or []),
             "; ".join(buf_sa))
        cseq_r2 = plant_seq103(repo_sc, "rejected")
        rc = execute_revert(repo_sc, cseq_r2, out=silent)
        case("--set-aside refuses a REVERTED run (nothing stands)",
             rc == EXIT_OK
             and execute_set_aside(repo_sc, cseq_r2, "wiki/a.md",
                                   "let it stand",
                                   out=silent) == EXIT_FAIL)
    finally:
        shutil.rmtree(repo_sc, ignore_errors=True)

    # ---------------- O5. v3.0.39: union set-aside + baseline-reset (105/106)
    try:
        parse_args(["--set-aside", "--root", ".", "--seq", "7",
                    "--view", "wiki/a.md", "--union-event", "raw/e.md",
                    "--ruling", "r"])
        case("--set-aside refuses --view AND --union-event together "
             "(mutually exclusive addressing modes)", False)
    except UsageError as e:
        case("--set-aside refuses --view AND --union-event together "
             "(mutually exclusive addressing modes)",
             "mutually exclusive" in str(e), str(e))
    try:
        parse_args(["--set-aside", "--root", ".", "--seq", "7",
                    "--ruling", "r"])
        case("--set-aside with NEITHER --view nor --union-event refuses",
             False)
    except UsageError:
        case("--set-aside with NEITHER --view nor --union-event refuses",
             True)
    p = parse_args(["--set-aside", "--root", "r", "--seq", "7",
                    "--union-event", "raw/e.md", "--ruling", "let it close"])
    case("--set-aside --union-event argv parses (mode/seq/event/ruling)",
         p["mode"] == "set-aside" and p["seq"] == 7
         and p["union-event"] == "raw/e.md"
         and p["ruling"] == "let it close", p)
    try:
        parse_args(["--baseline-reset", "--root", ".", "--view", "v"])
        case("--baseline-reset without refresh-commit/provenance/ruling "
             "refuses", False)
    except UsageError as e:
        case("--baseline-reset without refresh-commit/provenance/ruling "
             "refuses", "--refresh-commit" in str(e)
             and "--provenance" in str(e) and "--ruling" in str(e), str(e))
    try:
        parse_args(["--baseline-reset", "--root", ".", "--view", "v",
                    "--views-file", "f", "--refresh-commit", "c",
                    "--provenance", "p", "--ruling", "r"])
        case("--baseline-reset refuses --view AND --views-file together",
             False)
    except UsageError:
        case("--baseline-reset refuses --view AND --views-file together",
             True)
    p = parse_args(["--baseline-reset", "--root", "r", "--views-file",
                    "views.txt", "--refresh-commit", "abc123",
                    "--provenance", "the import", "--ruling", "reset"])
    case("--baseline-reset argv parses (bulk form)",
         p["mode"] == "baseline-reset" and p["views-file"] == "views.txt"
         and p["refresh-commit"] == "abc123"
         and p["provenance"] == "the import" and p["ruling"] == "reset", p)

    def plant_union(repo, verdict_key="rejected", event="raw/ue.md",
                    disposition=None):
        """A compile run whose covering verify record stamps its one
        absorbed view AND carries an unverified union no-op leg for EVENT
        with the named verdict -- the two live fork fixtures' shape
        (journal 123/125: rejected and revised)."""
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
            "union-shaped run seq %d" % cseq)
        art_rel = "receipts/verify/noop-seq%d-e0.json" % cseq
        ap = os.path.join(repo, art_rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(ap), exist_ok=True)
        with open(ap, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(VERDICTS[verdict_key], fh, indent=1, sort_keys=True)
        nc = {"view": "wiki/a.md", "event": event, "artifact": art_rel,
              "verified": False,
              "verdict_label": VERDICTS[verdict_key].get("verdict",
                                                         "no-verdict-field"),
              "reason_classes": ["contradiction"],
              "justification": {"event_sha256": "e" * 64,
                                "view_sha256": "f" * 64,
                                "union_views": ["wiki/a.md"],
                                "union_view_sha256": {"wiki/a.md": "f" * 64},
                                "note": "fixture union"}}
        if disposition:
            nc["verify_disposition"] = disposition
        vrec = core.minimal_record("verify", "0" * 40)
        vrec["verifies_seq"] = cseq
        vrec["absorption_verified"] = [{"view": "wiki/a.md",
                                        "events": ["raw/e1.md"],
                                        "artifact": "",
                                        "verified_at": "t1"}]
        vrec["noop_candidates"] = [nc]
        vseq, vpath = core.append_record(repo, vrec)
        core.stage_only_commit(
            repo, [art_rel,
                   os.path.relpath(vpath, repo).replace(os.sep, "/")],
            "union verify seq %d over %d" % (vseq, cseq))
        return cseq

    repo_u = make_repo("cdrv-union-")
    try:
        _git(repo_u, "add", "-A")
        _git(repo_u, "commit", "-qm", "fixtures")
        cseq_u = plant_union(repo_u, "rejected")
        case("--set-aside --union-event refuses an event the run fired no "
             "union leg for",
             execute_set_aside(repo_u, cseq_u, None, "close it",
                               union_event="raw/nope.md",
                               out=silent) == EXIT_FAIL)
        # ledger BEFORE: the union row reads open-blocking (direction 1)
        lines_b = []
        execute_verify_ledger(repo_u, out=lines_b.append)
        row_b = [ln for ln in lines_b if "union:raw/ue.md" in ln]
        case("verify-ledger: the un-adjudicated union row reads "
             "open-blocking", row_b and "open-blocking" in row_b[0],
             "\n".join(lines_b))
        rc = execute_set_aside(repo_u, cseq_u, None,
                               "the event's extra claim is out of scope "
                               "for this corpus -- let the row close",
                               union_event="raw/ue.md", out=silent)
        case("--set-aside --union-event LANDS on the live rejected shape "
             "(fork journal 123)", rc == EXIT_OK)
        adj_u = [aj for r in load_journal(repo_u).values()
                 for aj in r.get("absorption_adjudicated") or []]
        case("union adjudication record: pseudo-view subject, union_event, "
             "graded hashes and artifact kept, ruling VERBATIM",
             len(adj_u) == 1
             and adj_u[0]["view"] == "union:raw/ue.md"
             and adj_u[0]["union_event"] == "raw/ue.md"
             and adj_u[0]["union_views"] == ["wiki/a.md"]
             and adj_u[0]["event_sha256"] == "e" * 64
             and adj_u[0]["union_view_sha256"] == {"wiki/a.md": "f" * 64}
             and adj_u[0]["rejected_artifact"].startswith("receipts/verify/")
             and adj_u[0]["ruling"].startswith("the event's extra claim"),
             adj_u)
        case("union adjudication record carries NO baseline pin fields "
             "(baseline_commit / view_sha256 ABSENT -- a union leg "
             "absorbed nothing)",
             "baseline_commit" not in adj_u[0]
             and "view_sha256" not in adj_u[0], adj_u)
        # ledger AFTER: the row flips to set-aside (direction 2 -- the :886
        # argument fix pinned both directions)
        lines_a = []
        execute_verify_ledger(repo_u, out=lines_a.append)
        row_a = [ln for ln in lines_a if "union:raw/ue.md" in ln]
        case("verify-ledger: the adjudicated union row flips open-blocking "
             "-> set-aside", row_a and "set-aside" in row_a[0]
             and "open-blocking" not in row_a[0], "\n".join(lines_a))
        case("--set-aside --union-event refuses a SECOND ruling on the "
             "same (seq, event) (one ruling per verdict)",
             execute_set_aside(repo_u, cseq_u, None, "again",
                               union_event="raw/ue.md",
                               out=silent) == EXIT_FAIL)
        case("--set-aside --union-event refuses an empty ruling",
             execute_set_aside(repo_u, cseq_u, None, "   ",
                               union_event="raw/ue2.md",
                               out=silent) == EXIT_FAIL)
        # the view-mode duplicate check is NOT confused by the union ruling:
        # the view leg (stamped) still refuses on its own confirm ground
        case("the union ruling does not shadow the view key space (the "
             "stamped view leg still refuses as confirmed, not as "
             "already-adjudicated)",
             execute_set_aside(repo_u, cseq_u, "wiki/a.md", "r",
                               out=silent) == EXIT_FAIL)
    finally:
        shutil.rmtree(repo_u, ignore_errors=True)

    repo_u2 = make_repo("cdrv-union2-")
    try:
        _git(repo_u2, "add", "-A")
        _git(repo_u2, "commit", "-qm", "fixtures")
        cseq_rv = plant_union(repo_u2, "revised")
        rc = execute_set_aside(repo_u2, cseq_rv, None,
                               "completeness-class; recorded and closed",
                               union_event="raw/ue.md", out=silent)
        case("--set-aside --union-event LANDS on the live revised shape "
             "(fork journal 125)", rc == EXIT_OK)
        cseq_cf = plant_union(repo_u2, "confirm", event="raw/uc.md")
        # verified: true union leg -- flip the fixture's entry
        recs_u2 = load_journal(repo_u2)
        vs = [s for s, r in recs_u2.items()
              if r.get("verifies_seq") == cseq_cf]
        vpath_cf = os.path.join(repo_u2, "receipts", "journal",
                                "%d.json" % vs[0])
        vrec_cf = json.load(open(vpath_cf, encoding="utf-8"))
        vrec_cf["noop_candidates"][0]["verified"] = True
        with open(vpath_cf, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(vrec_cf, fh, indent=1, sort_keys=True)
        case("--set-aside --union-event refuses a CONFIRMED "
             "(verified: true) union leg -- nothing to adjudicate",
             execute_set_aside(repo_u2, cseq_cf, None, "r",
                               union_event="raw/uc.md",
                               out=silent) == EXIT_FAIL)
        vrec_cf["noop_candidates"][0]["verified"] = False
        with open(vpath_cf, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(vrec_cf, fh, indent=1, sort_keys=True)
        buf_un = []
        rc = execute_set_aside(repo_u2, cseq_cf, None,
                               "the on-file union approval is genuine",
                               union_event="raw/uc.md", out=buf_un.append)
        case("--set-aside --union-event ADJUDICATES the discarded-union-"
             "approval shape, naming stamp-refused (the NOTE path)",
             rc == EXIT_OK and any("stamp-refused" in ln for ln in buf_un),
             "; ".join(buf_un))
        cseq_tr = plant_union(repo_u2, "bridge-error", event="raw/ut.md")
        case("--set-aside --union-event refuses an INCOMPLETE union leg "
             "toward --reverify (a transport failure is not a verdict)",
             execute_set_aside(repo_u2, cseq_tr, None, "r",
                               union_event="raw/ut.md",
                               out=silent) == EXIT_FAIL)
    finally:
        shutil.rmtree(repo_u2, ignore_errors=True)

    # mixed-run --reverify decline: byte-untouched behavior, pinned on both
    # sides of a union adjudication (design sec.2.4 / hard requirement 1).
    repo_u3 = make_repo("cdrv-union3-")
    try:
        st_u3 = make_staging(repo_u3)
        good_u3 = make_grant(repo_u3)
        _git(repo_u3, "add", "-A")
        _git(repo_u3, "commit", "-qm", "fixtures")
        cseq_m = plant_union(repo_u3, "rejected")
        eng_m = FakeEngine()
        buf_m1 = []
        rc = reverify_driver(repo_u3, cseq_m, st_u3, good_u3, engine=eng_m,
                             out=buf_m1.append)
        case("mixed run (stamped view + open union rejection): --reverify "
             "declines BEFORE the union adjudication (terminal, not "
             "stamp-refused-only)",
             rc == EXIT_OK and eng_m.verify_calls == 0
             and any("Nothing to do" in ln for ln in buf_m1),
             "; ".join(buf_m1))
        rc = execute_set_aside(repo_u3, cseq_m, None, "close the union row",
                               union_event="raw/ue.md", out=silent)
        buf_m2 = []
        rc2 = reverify_driver(repo_u3, cseq_m, st_u3, good_u3, engine=eng_m,
                              out=buf_m2.append)
        case("mixed run: --reverify STILL declines identically AFTER the "
             "union adjudication (the v3.0-74 narrow gate is "
             "byte-untouched; adjudication is not a re-fire ticket)",
             rc == EXIT_OK and rc2 == EXIT_OK and eng_m.verify_calls == 0
             and buf_m2 == buf_m1, "%s vs %s" % (buf_m1, buf_m2))
    finally:
        shutil.rmtree(repo_u3, ignore_errors=True)

    # ------------------------- baseline-reset: the G1-G7 guard chain
    repo_br = make_repo("cdrv-breset-")
    try:
        _git(repo_br, "add", "-A")
        _git(repo_br, "commit", "-qm", "fixtures")
        # the out-of-engine photograph: rewrite a.md, add b.md + c.md
        write(os.path.join(repo_br, "wiki", "a.md"),
              "# View A\n\nphotograph body\n")
        write(os.path.join(repo_br, "wiki", "b.md"),
              "# View B\n\nphotograph body b\n")
        write(os.path.join(repo_br, "wiki", "c.md"),
              "# View C\n\nphotograph body c\n")
        _git(repo_br, "add", "-A")
        _git(repo_br, "commit", "-qm",
             "corpus photograph (out-of-engine import)")
        refresh_sha = _git(repo_br, "rev-parse", "HEAD")[1].strip()
        case("G1: empty --ruling refuses",
             execute_baseline_reset(repo_br, "wiki/b.md", None, refresh_sha,
                                    "the import", "  ",
                                    out=silent) == EXIT_FAIL)
        case("G1: empty --provenance refuses (scope-locked to declared "
             "imports)",
             execute_baseline_reset(repo_br, "wiki/b.md", None, refresh_sha,
                                    "", "reset it",
                                    out=silent) == EXIT_FAIL)
        with open(os.path.join(repo_br, "wiki", "b.md"), "a",
                  encoding="utf-8") as fh:
            fh.write("dirt\n")
        case("G2: a dirty worktree refuses (the record lands as its own "
             "stage-only commit)",
             execute_baseline_reset(repo_br, "wiki/b.md", None, refresh_sha,
                                    "the import", "reset it",
                                    out=silent) == EXIT_FAIL)
        _git(repo_br, "checkout", "--", ".")
        case("G3: an unknown --refresh-commit refuses",
             execute_baseline_reset(repo_br, "wiki/b.md", None,
                                    "deadbeef" * 5, "the import", "reset it",
                                    out=silent) == EXIT_FAIL)
        rc_ct, orphan, _e = _git(repo_br, "commit-tree", "-m", "off-line",
                                 "-p", "HEAD", "HEAD^{tree}")
        case("G3: a commit that is NOT an ancestor of HEAD refuses "
             "(fail-closed ancestry)",
             rc_ct == 0
             and execute_baseline_reset(repo_br, "wiki/b.md", None,
                                        orphan.strip(), "the import",
                                        "reset it",
                                        out=silent) == EXIT_FAIL)
        case("G5: a view absent at the refresh commit refuses (single "
             "mode: nothing journaled)",
             execute_baseline_reset(repo_br, "wiki/nope.md", None,
                                    refresh_sha, "the import", "reset it",
                                    out=silent) == EXIT_FAIL)
        chain_before = _core().check_chain(repo_br)
        case("...and the guard refusals journaled NOTHING",
             _core().check_chain(repo_br) == chain_before
             and _worktree_clean(repo_br))
        rc = execute_baseline_reset(repo_br, "wiki/b.md", None, refresh_sha,
                                    "fixture photograph, imported "
                                    "2026-08-16 outside the engine",
                                    "reset the window", out=silent)
        case("--baseline-reset LANDS per-view (exit 0, stage-only commit, "
             "worktree clean)", rc == EXIT_OK and _worktree_clean(repo_br))
        br_recs = [r for r in load_journal(repo_br).values()
                   if r.get("run_type") == "baseline-reset"]
        ent = br_recs[0]["baseline_reset"][0] if br_recs \
            and br_recs[0].get("baseline_reset") else {}
        case("baseline-reset record: new run_type, entry pins the refresh "
             "commit + content sha, provenance and ruling VERBATIM, "
             "reset_by operator",
             len(br_recs) == 1 and ent.get("view") == "wiki/b.md"
             and ent.get("refresh_commit") == refresh_sha
             and ent.get("provenance", "").startswith("fixture photograph")
             and ent.get("ruling") == "reset the window"
             and ent.get("reset_by") == "operator", br_recs)
        case("baseline-reset pin matches the refresh commit's actual "
             "content (git-show recomputed, never the worktree)",
             __import__("hashlib").sha256(subprocess.run(
                 ["git", "-C", repo_br, "show",
                  "%s:wiki/b.md" % refresh_sha],
                 capture_output=True, text=True, encoding="utf-8")
                 .stdout.encode("utf-8")).hexdigest()
             == ent.get("view_sha256"))
        buf_g6i = []
        case("a second reset of the same (view, refresh_commit) refuses -- "
             "caught by G6-identical in the spec's refusal order (the "
             "newest stamp already pins the refresh commit itself); G7 "
             "remains the backstop for the pair",
             execute_baseline_reset(repo_br, "wiki/b.md", None, refresh_sha,
                                    "the import", "again",
                                    out=buf_g6i.append) == EXIT_FAIL
             and any("G6" in ln and "refresh commit itself" in ln
                     for ln in buf_g6i), "; ".join(buf_g6i))
        # engine-authored history: a run commit (adds receipts/journal/)
        cseq_g = plant_seq103(repo_br, "rejected")
        run_sha_g = _find_run_commit(repo_br, cseq_g)
        case("G4: a journaled run commit refuses (engine-authored history "
             "is never an import)",
             execute_baseline_reset(repo_br, "wiki/a.md", None, run_sha_g,
                                    "the import", "reset it",
                                    out=silent) == EXIT_FAIL)
        # G6 refuse direction: wiki/a.md's newest stamp (a set-aside pinned
        # at the run commit) is a DESCENDANT of the photograph -- the
        # baseline already advanced past it; a reset would regress it.
        rc = execute_set_aside(repo_br, cseq_g, "wiki/a.md",
                               "let it stand", out=silent)
        buf_g6 = []
        case("G6 (no-rewind, refuse direction): a stamp pinning a "
             "DESCENDANT of the refresh commit refuses the reset -- the "
             "baseline-already-advanced shape",
             rc == EXIT_OK
             and execute_baseline_reset(repo_br, "wiki/a.md", None,
                                        refresh_sha, "the import",
                                        "reset it",
                                        out=buf_g6.append) == EXIT_FAIL
             and any("G6" in ln for ln in buf_g6), "; ".join(buf_g6))
        # G6 proceed direction + a LATER photograph: wiki/b.md's newest
        # stamp is the reset at refresh_sha, a STRICT ANCESTOR of photo2.
        write(os.path.join(repo_br, "wiki", "b.md"),
              "# View B\n\nsecond photograph body b\n")
        _git(repo_br, "add", "-A")
        _git(repo_br, "commit", "-qm", "second photograph")
        photo2_sha = _git(repo_br, "rev-parse", "HEAD")[1].strip()
        case("G6 (proceed direction): a stamp pinning a STRICT ANCESTOR "
             "of the refresh commit proceeds -- a LATER photograph may "
             "reset again (G7 keys the pair)",
             execute_baseline_reset(repo_br, "wiki/b.md", None, photo2_sha,
                                    "second fixture photograph",
                                    "advance to the new photograph",
                                    out=silent) == EXIT_OK)
        # bulk form: one passing view, two refused with guards named
        write(os.path.join(repo_br, "views.txt"),
              "wiki/c.md\nwiki/a.md\nwiki/nope.md\n")
        _git(repo_br, "add", "-A")
        _git(repo_br, "commit", "-qm", "views list")
        buf_blk = []
        rc = execute_baseline_reset(repo_br, None, "views.txt", refresh_sha,
                                    "fixture photograph, bulk",
                                    "bulk reset", out=buf_blk.append)
        br_recs2 = [r for r in load_journal(repo_br).values()
                    if r.get("run_type") == "baseline-reset"]
        newest_blk = max(br_recs2, key=lambda r: r.get("seq", 0))
        case("bulk --views-file: the passing view resets, exit 0",
             rc == EXIT_OK
             and [e["view"] for e in newest_blk["baseline_reset"]]
             == ["wiki/c.md"], "; ".join(buf_blk))
        case("bulk --views-file: the record's refused[] names every view "
             "that did not move and why (journal-only truth includes the "
             "refusals)",
             sorted((rj["view"], rj["guard"])
                    for rj in newest_blk.get("refused") or [])
             == [("wiki/a.md", "G6"), ("wiki/nope.md", "G5")],
             newest_blk.get("refused"))
        # G6 fail-closed directions the ancestry walk cannot resolve:
        # a stamp pinning an UNRESOLVABLE commit, and a stamp pinning NO
        # commit at all -- both refuse, never guess.
        write(os.path.join(repo_br, "wiki", "d.md"),
              "# View D\n\nphotograph body d\n")
        write(os.path.join(repo_br, "wiki", "e.md"),
              "# View E\n\nphotograph body e\n")
        _git(repo_br, "add", "-A")
        _git(repo_br, "commit", "-qm", "third photograph (d + e)")
        photo3_sha = _git(repo_br, "rev-parse", "HEAD")[1].strip()
        core_g6 = _core()
        bogus = core_g6.minimal_record(
            "verify-adjudication",
            _git(repo_br, "rev-parse", "HEAD")[1].strip())
        bogus["run_window"] = {"start": "t0", "end": "t1"}
        bogus["absorption_adjudicated"] = [
            {"view": "wiki/d.md", "adjudicates_seq": 1, "at": "t",
             "ruling": "fixture", "adjudicated_by": "operator",
             "baseline_commit": "0" * 40, "view_sha256": "a" * 64},
            {"view": "wiki/e.md", "adjudicates_seq": 1, "at": "t",
             "ruling": "fixture", "adjudicated_by": "operator"}]
        _bseq, bpath = core_g6.append_record(repo_br, bogus)
        core_g6.stage_only_commit(
            repo_br, [os.path.relpath(bpath, repo_br).replace(os.sep, "/")],
            "bogus-pin adjudication fixture")
        buf_g6u = []
        case("G6 fail-closed: a stamp pinning an UNRESOLVABLE commit "
             "refuses the reset (ancestry cannot be determined)",
             execute_baseline_reset(repo_br, "wiki/d.md", None, photo3_sha,
                                    "the import", "reset it",
                                    out=buf_g6u.append) == EXIT_FAIL
             and any("G6" in ln and "unresolvable" in ln
                     for ln in buf_g6u), "; ".join(buf_g6u))
        buf_g6n = []
        case("G6 fail-closed: a stamp pinning NO commit refuses the reset "
             "(a pin-less stamp is incomparable, never treated as absent)",
             execute_baseline_reset(repo_br, "wiki/e.md", None, photo3_sha,
                                    "the import", "reset it",
                                    out=buf_g6n.append) == EXIT_FAIL
             and any("G6" in ln and "pins no commit" in ln
                     for ln in buf_g6n), "; ".join(buf_g6n))
        # a reset closes NO verdict row: a fresh rejected leg stays
        # open-blocking through a later reset of a DIFFERENT view.
        cseq_o = plant_seq103(repo_br, "rejected")
        rc = execute_baseline_reset(repo_br, "wiki/c.md", None, photo2_sha,
                                    "second fixture photograph",
                                    "advance c too", out=silent)
        lines_o = []
        execute_verify_ledger(repo_br, out=lines_o.append)
        case("a reset record closes NO verdict row (the fresh rejected "
             "leg still reads open-blocking after a later reset -- a "
             "reset adjudicates nothing)",
             rc == EXIT_OK
             and any("open-blocking" in ln for ln in lines_o
                     if "wiki/a.md" in ln), "\n".join(lines_o))
        # G7 reached on its own ground: wiki/c.md was reset at photo2; land
        # a NEWER stamp pinning a strict ANCESTOR of photo2 (the v3.0-107
        # regression shape), so a second photo2 reset passes G5 AND G6 --
        # only G7's (view, refresh_commit) memory refuses the duplicate.
        regress = _core().minimal_record(
            "verify-adjudication",
            _git(repo_br, "rev-parse", "HEAD")[1].strip())
        regress["run_window"] = {"start": "t0", "end": "t1"}
        regress["absorption_adjudicated"] = [
            {"view": "wiki/c.md", "adjudicates_seq": 1, "at": "t",
             "ruling": "fixture regression stamp",
             "adjudicated_by": "operator",
             "baseline_commit": refresh_sha, "view_sha256": "b" * 64}]
        _rseq, rpath = _core().append_record(repo_br, regress)
        _core().stage_only_commit(
            repo_br, [os.path.relpath(rpath, repo_br).replace(os.sep, "/")],
            "regression-stamp fixture")
        buf_g7 = []
        case("G7 refuses on its own ground (G5 and G6 both pass -- the "
             "newest stamp pins a strict ancestor -- and the "
             "(view, refresh_commit) pair is already on the journal)",
             execute_baseline_reset(repo_br, "wiki/c.md", None, photo2_sha,
                                    "the import", "again",
                                    out=buf_g7.append) == EXIT_FAIL
             and any("G7" in ln and "one ruling per fact" in ln
                     for ln in buf_g7), "; ".join(buf_g7))
        # G7 intra-invocation: the same view twice in ONE --views-file must
        # not journal the same (view, refresh_commit) pair twice. Every
        # line -- repeats included -- walks G5->G6->G7, so the passing
        # view's repeat refuses under G7 (the pair already resets in this
        # record) while a repeated MISSING view earns G5 both times.
        write(os.path.join(repo_br, "views-dup.txt"),
              "wiki/b.md\nwiki/b.md\nwiki/nope.md\nwiki/nope.md\n")
        _git(repo_br, "add", "-A")
        _git(repo_br, "commit", "-qm", "dup views list")
        rc = execute_baseline_reset(repo_br, None, "views-dup.txt",
                                    photo3_sha, "third fixture photograph",
                                    "advance b", out=silent)
        dup_rec = max((r for r in load_journal(repo_br).values()
                       if r.get("run_type") == "baseline-reset"),
                      key=lambda r: r.get("seq", 0))
        case("G7 intra-invocation: a view listed twice in one --views-file "
             "journals ONE entry, the repeat refused under G7 after "
             "re-walking G5/G6 (one ruling per fact)",
             rc == EXIT_OK
             and [e["view"] for e in dup_rec["baseline_reset"]]
             == ["wiki/b.md"]
             and ("wiki/b.md", "G7") in
             [(rj["view"], rj["guard"])
              for rj in dup_rec.get("refused") or []], dup_rec)
        case("...and a repeated MISSING view earns its G5 refusal on every "
             "line (the guard chain runs per line; no pre-check shadows "
             "G5/G6)",
             [(rj["view"], rj["guard"])
              for rj in dup_rec.get("refused") or []]
             == [("wiki/b.md", "G7"), ("wiki/nope.md", "G5"),
                 ("wiki/nope.md", "G5")], dup_rec.get("refused"))
        case("reconciliation stays clean across resets (no blocking "
             "state minted)", reconcile_state(repo_br)["blocked"] is False)
        case("journal chain intact across the whole battery",
             _core().check_chain(repo_br)
             == len(load_journal(repo_br)))
    finally:
        shutil.rmtree(repo_br, ignore_errors=True)

    # v3.0-63: deferred claims ride the summary block, named as
    # pending_cascade-bound.
    sm = _summary_block(7, "a" * 40, ["wiki/a.md"], None,
                        {"census": (0, ""), "diff": (0, "")},
                        deferred_claims=[("raw/wide.md", "c3",
                                          "the gamma claim",
                                          ["wiki/c.md"])])
    case("v3.0-63: summary block lists deferred claims and names "
         "pending_cascade as where they MUST land",
         "Deferred:      1 claim(s)" in sm
         and "MUST land in the receipt's pending_cascade" in sm
         and "[raw/wide.md / c3] the gamma claim -> wiki/c.md" in sm)
    sm2 = _summary_block(7, "a" * 40, ["wiki/a.md"], None,
                         {"census": (0, ""), "diff": (0, "")})
    case("v3.0-63: summary block without deferred claims is unchanged "
         "(no Deferred band)", "Deferred:" not in sm2)

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
    case("main() dispatches --verify-ledger to execute_verify_ledger",
         "return execute_verify_ledger(" in main_src)
    case("main() dispatches --baseline-reset to execute_baseline_reset",
         "return execute_baseline_reset(" in main_src)
    case("main() passes --union-event through to execute_set_aside",
         "union_event=opts[\"union-event\"]" in main_src)

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
    if opts["mode"] == "set-aside":
        return execute_set_aside(opts["root"], opts["seq"], opts["view"],
                                 opts["ruling"],
                                 union_event=opts["union-event"])
    if opts["mode"] == "baseline-reset":
        return execute_baseline_reset(opts["root"], opts["view"],
                                      opts["views-file"],
                                      opts["refresh-commit"],
                                      opts["provenance"], opts["ruling"])
    if opts["mode"] == "verify-ledger":
        return execute_verify_ledger(opts["root"], since=opts["since"])
    print(USAGE)
    return EXIT_INCONCLUSIVE


if __name__ == "__main__":
    sys.exit(main(sys.argv))
