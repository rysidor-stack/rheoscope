#!/usr/bin/env python3
"""harvest-candidates.py -- R-1 session-candidate harvester (memory-engine v3,
R-1 leg C).

Spec: harness-v3.0/specs/r1-build-decisions-2026-07-22.md Part 5 (this module's
algorithm, verbatim) -- read Part 3 (candidate file shape + id) and Part 4 (the
candidate ledger) in deploy/candidates.py for the data shapes this leans on;
Part 8's acceptance criterion 3 ("crash-replay is exactly-once") is a done-bar
THIS module's ordering must make achievable -- proven here by --self-test, not
just asserted.

WHAT THIS MODULE IS: one candidate per invocation, idempotent by candidate_id,
safe to call from either a live session (Q1's "harvester end") or a script. It
is NOT the guarantee -- register-candidates.py (the reconciler, run in a
healthy session) is. This module only needs to be idempotent and convergent; it
is explicitly allowed to leave a visible gap on a crash, because the reconciler
closes it. See run_harvest()'s docstring for why the write order is load-bearing.

WHY THE SPAN COMES FROM A FILE, NOT ARGV (Part 5): argv mangles newlines and
shell-quotes on every platform this might run on (this repo targets Windows).
--span-file is read BYTE-EXACTLY (Part 5: "never stripped or normalised") --
see _read_span_file()'s docstring for the newline="" detail that makes this
true even for a CRLF-containing span, which Python's default text-mode read
would otherwise silently rewrite.

WHY THE LEDGER LOOKUP HAPPENS BEFORE ANY WRITE (Part 5 step 2, three ways this
run can end without touching the filesystem): a terminal candidate is refused
re-harvest even if its file is long gone (it already had its say -- promoted,
expired, discarded, or quarantined, none of which this module may undo); a
non-terminal candidate whose staged file still exists is a pure no-op (nothing
to converge); a non-terminal candidate whose staged file has VANISHED is
re-materialised from the ledger record's own fields plus THIS invocation's
session_id/transcript_pointer (the ledger record does not carry either of
those -- see run_harvest()'s inline comment) with NO new record appended,
because the ledger's claim about this candidate has not changed, only the
filesystem is catching back up to it.

Usage:
  harvest-candidates.py --root DIR --session-id ID --class CLASS \\
      --disposition DISP --span-file PATH [--origin-max ORIGIN] \\
      [--transcript-pointer TEXT] [--dry-run]
  harvest-candidates.py --self-test
Exit: 0 ok (harvested, no-op, converged, or terminal-skip) |
      1 violation/refusal (bad session_id charset, bad span-file path, a
        malformed ledger encountered mid-lookup, etc.) |
      2 inconclusive (PyYAML unavailable -- gated defensively for parity with
        register-candidates.py's exit-code contract, even though this
        module's own happy paths never call the YAML-dependent parser; see
        the build report for why this is a judgement call, not a hard need).
"""

import argparse
import importlib.util
import os
import re
import sys
import time

try:  # cp1252 consoles + unicode spans: never let an encode error mask a verdict.
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(basename, alias):
    spec = importlib.util.spec_from_file_location(alias, os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Sibling-import by path -- deploy/ is a flat directory, never a package (the
# same mechanism candidates.py/register-intake.py use). Distinct alias from
# candidates.py's own internal "origin_candidates" _load call: each _load()
# mints a fresh module object, so nothing here shares mutable state with it.
candidates = _load("candidates.py", "candidates_harvest")
origin = _load("origin.py", "origin_harvest")

try:
    import yaml
except ImportError:  # pragma: no cover -- optional-yaml idiom, matches the rest of deploy/.
    yaml = None


class HarvestViolation(Exception):
    """Raised by this module's own logic (a ledger record whose event path does
    not match the expected filename shape, etc.) -- distinct from
    candidates.CandidateViolation, which is the imported library's own
    trust-boundary exception. Both are caught the same way at the CLI edge."""
    pass


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


_EVENT_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-candidate-(.+)\.md$")


def _date_from_event(event_rel, candidate_id):
    """The re-materialise path (Part 5 step 2's third branch) MUST land the
    reconstructed file at EXACTLY the path the ledger record already names --
    never a freshly-computed 'today' date -- or a second run would mint a
    SECOND, differently-named file for the same candidate_id, orphaning the
    original chain entry instead of converging it. Extract the date back out of
    the record's own `event` path (its filename is always
    `<date>-candidate-<candidate_id>.md`, per write_candidate_atomic) rather
    than trusting the current clock."""
    base = event_rel.rsplit("/", 1)[-1]
    m = _EVENT_DATE_RE.match(base)
    if not m or m.group(2) != candidate_id:
        raise HarvestViolation(
            "ledger record event path %r does not match the expected "
            "'<date>-candidate-%s.md' shape -- refusing to guess a date for "
            "re-materialisation" % (event_rel, candidate_id))
    return m.group(1)


def _read_span_file(path):
    """Byte-exact read (Part 5: 'the span is used byte-exactly, never stripped
    or normalised'). newline="" disables Python's universal-newline
    translation -- WITHOUT it, a CRLF-containing span file would be silently
    rewritten to LF on read, which is exactly the normalisation the spec
    forbids (this would happen BEFORE span_sha256 is even computed, so the
    corruption would be invisible -- the hash would just be of the wrong
    bytes). Mirrors write_candidate_atomic's own newline="" write-side idiom.
    utf-8-sig tolerates (and strips) a leading BOM, matching
    candidates.validate_candidate_file's convention, and changes nothing else
    about the bytes."""
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return fh.read()


# ------------------------------------------------------------------ harvest
def run_harvest(root, session_id, candidate_class, disposition, span, origin_max_val,
                 transcript_pointer="", dry_run=False):
    """Part 5's algorithm, verbatim order -- the ordering is load-bearing.

    Step 1: compute span_sha256 and candidate_id (make_candidate_id also
    enforces the session_id charset -- a path-traversing session_id is
    refused here, before anything else runs).

    Step 2: ledger lookup on candidate_id, three ways to end without a write:
      (a) effective state terminal -> report, exit 0, nothing written;
      (b) entry exists AND the staged file exists -> no-op, exit 0;
      (c) entry exists, non-terminal, staged file GONE -> re-materialise from
          the record's own fields (candidate_class/origin_max/
          proposed_disposition/span_sha256, and the date embedded in its
          `event` path) plus THIS invocation's session_id/transcript_pointer
          (the ledger record does not carry either of those -- they exist
          only in the candidate FILE's frontmatter, which is exactly what is
          missing in this branch). Appends NO new record: the ledger's claim
          about this candidate has not changed.

    Step 3 (otherwise, no ledger entry at all): fresh harvest. FILE FIRST,
    RECORD SECOND -- never the reverse. A crash between the two leaves a
    staged file with no chain entry: a VISIBLE conservation gap that
    register-candidates.py converges on its next run by minting the missing
    'staged' record (Part 5's own reconciler step 2). The opposite ordering
    (record first) would leave an append-only chain entry pointing at a file
    that was NEVER written -- a claim the chain can never retract, and
    nothing short of a name-by-name existence sweep would even notice.
    Visible-and-convergent beats silent-and-uncorrectable."""
    span_sha = candidates.span_hash(span)
    candidate_id = candidates.make_candidate_id(session_id, span)

    # ---- step 2: ledger lookup -------------------------------------------
    state = candidates.effective_state(root, candidate_id)

    if state is not None:
        if candidates.is_terminal(state):
            print("candidate %s is already TERMINAL (state=%s) -- not "
                  "re-harvested, nothing written" % (candidate_id, state))
            return 0

        ledger = candidates.load_candidate_ledger(root)
        record = ledger[candidate_id]
        event_rel = record["event"]
        event_abs = os.path.join(root, *event_rel.split("/"))

        if os.path.isfile(event_abs):
            print("candidate %s already harvested at %s (state=%s) -- no-op, "
                  "nothing written" % (candidate_id, event_rel, state))
            return 0

        # (c) entry exists, non-terminal, file GONE -> converge.
        harvested_at = _date_from_event(event_rel, candidate_id)
        meta = {
            "kind": "session-candidate",
            "candidate_id": candidate_id,
            "session_id": session_id,
            "candidate_class": record["candidate_class"],
            "origin": "session-derived",
            "origin_max": record["origin_max"],
            "proposed_disposition": record["proposed_disposition"],
            "transcript_pointer": transcript_pointer,
            "harvested_at": harvested_at,
            "span_sha256": record["span_sha256"],
        }
        if dry_run:
            print("DRY-RUN: candidate %s has a %s ledger record but its staged "
                  "file is GONE (%s) -- would RE-MATERIALISE it, append NO new "
                  "record; nothing written" % (candidate_id, state, event_rel))
            return 0
        path = candidates.write_candidate_atomic(root, meta, span)
        print("re-materialised %s for candidate %s (ledger record unchanged, "
              "no new record appended)" % (path, candidate_id))
        return 0

    # ---- step 3: fresh harvest --------------------------------------------
    harvested_at = _now_iso()
    meta = {
        "kind": "session-candidate",
        "candidate_id": candidate_id,
        "session_id": session_id,
        "candidate_class": candidate_class,
        "origin": "session-derived",
        "origin_max": origin_max_val,
        "proposed_disposition": disposition,
        "transcript_pointer": transcript_pointer,
        "harvested_at": harvested_at,
        "span_sha256": span_sha,
    }

    if dry_run:
        date_part = harvested_at[:10]
        prospective = os.path.join(
            root, candidates.CANDIDATES_STAGING_DIR,
            "%s-candidate-%s.md" % (date_part, candidate_id))
        print("DRY-RUN: would harvest NEW candidate %s -> %s; would append ONE "
              "'staged' ledger record; nothing written"
              % (candidate_id, prospective))
        return 0

    # FILE FIRST -- see run_harvest's own docstring for why this ordering (not
    # the reverse) is deliberate.
    path = candidates.write_candidate_atomic(root, meta, span)
    event_rel = os.path.relpath(path, root).replace("\\", "/")
    record = candidates.new_record(
        meta, "staged", "harvested from session %s" % session_id, event_rel)
    seq, _p = candidates.append_candidate_record(root, record)
    print("harvested new candidate %s -> %s (chain seq %d, state=staged)"
          % (candidate_id, event_rel, seq))
    return 0


# ------------------------------------------------------------------ self-test
def self_test():
    import contextlib
    import io
    import shutil
    import subprocess
    import tempfile

    total = failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    def _git_init(base):
        subprocess.run(["git", "-C", base, "init", "-q"], capture_output=True)
        subprocess.run(["git", "-C", base, "config", "user.email", "t@t"],
                       capture_output=True)
        subprocess.run(["git", "-C", base, "config", "user.name", "t"],
                       capture_output=True)

    # ============================================================ (1)(2)(3):
    # fresh harvest / idempotence / one-char span diff -> different id + 2nd record
    base1 = tempfile.mkdtemp(prefix="harvest-fresh-")
    try:
        _git_init(base1)
        span_a = "the operator decided to ship the R-1 pilot on schedule"
        rc = run_harvest(base1, "sess-fresh1", "decision-stated",
                          "transcribe-as-operator-event", span_a, "corpus")
        case("(1) fresh harvest exits 0", rc == 0)
        cid_a = candidates.make_candidate_id("sess-fresh1", span_a)
        staging = os.path.join(base1, candidates.CANDIDATES_STAGING_DIR)
        files1 = os.listdir(staging)
        case("(1) fresh harvest writes EXACTLY one file",
             len(files1) == 1 and cid_a in files1[0])
        case("(1) fresh harvest appends EXACTLY one 'staged' record",
             candidates.check_candidate_chain(base1) == 1
             and candidates.effective_state(base1, cid_a) == "staged")

        rc2 = run_harvest(base1, "sess-fresh1", "decision-stated",
                           "transcribe-as-operator-event", span_a, "corpus")
        case("(2) identical re-run exits 0", rc2 == 0)
        case("(2) identical re-run appends NOTHING (chain count unchanged)",
             candidates.check_candidate_chain(base1) == 1)
        case("(2) identical re-run leaves exactly one file",
             len(os.listdir(staging)) == 1)

        span_b = span_a[:-1] + "D"  # last character changed
        cid_b = candidates.make_candidate_id("sess-fresh1", span_b)
        case("(3) one-character span change yields a DIFFERENT candidate_id",
             cid_a != cid_b)
        rc3 = run_harvest(base1, "sess-fresh1", "decision-stated",
                           "transcribe-as-operator-event", span_b, "corpus")
        case("(3) harvesting the changed span exits 0", rc3 == 0)
        case("(3) a SECOND chain record now exists for the new id",
             candidates.check_candidate_chain(base1) == 2
             and candidates.effective_state(base1, cid_b) == "staged")
    finally:
        shutil.rmtree(base1, ignore_errors=True)

    # ============================================================ (4) crash-replay
    base2 = tempfile.mkdtemp(prefix="harvest-crash-")
    try:
        _git_init(base2)
        session_id = "sess-crash1"
        span = "a span used to prove crash-replay is exactly-once"
        cid = candidates.make_candidate_id(session_id, span)

        orig_append = candidates.append_candidate_record

        def _boom(root_, record_):
            raise RuntimeError("simulated crash between file-write and "
                                "record-append")

        candidates.append_candidate_record = _boom
        try:
            try:
                run_harvest(base2, session_id, "finding", "plain-intake", span,
                            "corpus")
                case("(4) simulated crash propagates (never silently swallowed)",
                     False)
            except RuntimeError:
                case("(4) simulated crash propagates (never silently swallowed)",
                     True)
        finally:
            candidates.append_candidate_record = orig_append

        staging2 = os.path.join(base2, candidates.CANDIDATES_STAGING_DIR)
        after_crash = os.listdir(staging2) if os.path.isdir(staging2) else []
        case("(4) the file WAS written despite the append failing "
             "(file-first ordering)", any(cid in f for f in after_crash))
        case("(4) NO chain entry exists yet (the append never completed)",
             candidates.effective_state(base2, cid) is None)

        rc_replay = run_harvest(base2, session_id, "finding", "plain-intake",
                                 span, "corpus")
        case("(4) re-run after the simulated crash exits 0", rc_replay == 0)

        hist = [r for r in candidates.ledger_history(base2)
                if r["candidate_id"] == cid]
        case("(4) the ledger ends with EXACTLY ONE record for this candidate",
             len(hist) == 1)
        final_files = [f for f in os.listdir(staging2) if cid in f]
        case("(4) the staged file exists EXACTLY ONCE", len(final_files) == 1)
        tmp_leftover = [f for f in os.listdir(staging2)
                        if f.startswith(".candidate-tmp-")]
        case("(4) no leftover .candidate-tmp-* files", tmp_leftover == [])
    finally:
        shutil.rmtree(base2, ignore_errors=True)

    # ============================================================ (5) converge:
    # staged file deleted out-of-band, non-terminal -> re-materialised, NO new record
    base3 = tempfile.mkdtemp(prefix="harvest-converge-")
    try:
        _git_init(base3)
        session_id = "sess-converge1"
        span = "a span used to prove the converge path"
        run_harvest(base3, session_id, "correction", "plain-intake", span,
                    "corpus", transcript_pointer="ptr-1")
        cid = candidates.make_candidate_id(session_id, span)
        rec_before = candidates.load_candidate_ledger(base3)[cid]
        event_path = os.path.join(base3, *rec_before["event"].split("/"))
        case("(5) setup: staged file exists before deletion",
             os.path.isfile(event_path))
        os.remove(event_path)
        case("(5) setup: staged file is now gone", not os.path.isfile(event_path))

        n_before = candidates.check_candidate_chain(base3)
        rc5 = run_harvest(base3, session_id, "correction", "plain-intake", span,
                           "corpus", transcript_pointer="ptr-1")
        case("(5) re-run after out-of-band deletion exits 0", rc5 == 0)
        case("(5) the file is RE-MATERIALISED", os.path.isfile(event_path))
        case("(5) NO new record was appended",
             candidates.check_candidate_chain(base3) == n_before)
        m5, s5 = candidates.validate_candidate_file(event_path)
        case("(5) the re-materialised file parses and matches the original span",
             s5 == span)
        case("(5) the re-materialised file lands at the SAME path the ledger "
             "already named (never a fresh-dated filename)",
             os.path.relpath(event_path, base3).replace("\\", "/")
             == rec_before["event"])
    finally:
        shutil.rmtree(base3, ignore_errors=True)

    # ============================================================ (6) terminal
    # state -> never re-harvested, even if the file is missing
    base4 = tempfile.mkdtemp(prefix="harvest-terminal-")
    try:
        _git_init(base4)
        session_id = "sess-terminal1"
        span = "a span used to prove terminal states are never re-harvested"
        run_harvest(base4, session_id, "directive", "discard-candidate", span,
                    "corpus")
        cid = candidates.make_candidate_id(session_id, span)
        rec = candidates.load_candidate_ledger(base4)[cid]
        event_path = os.path.join(base4, *rec["event"].split("/"))
        os.remove(event_path)  # gone, to prove even this doesn't trigger a rewrite

        meta = {
            "kind": "session-candidate",
            "candidate_id": cid,
            "session_id": session_id,
            "candidate_class": "directive",
            "origin": "session-derived",
            "origin_max": "corpus",
            "proposed_disposition": "discard-candidate",
            "transcript_pointer": "",
            "harvested_at": "2026-07-22T00:00:00",
            "span_sha256": candidates.span_hash(span),
        }
        term_rec = candidates.new_record(meta, "discarded",
                                          "fixture: manually discarded", rec["event"])
        candidates.append_candidate_record(base4, term_rec)
        n_before = candidates.check_candidate_chain(base4)
        case("(6) setup: candidate is now terminal (discarded)",
             candidates.effective_state(base4, cid) == "discarded")

        rc6 = run_harvest(base4, session_id, "directive", "discard-candidate",
                           span, "corpus")
        case("(6) re-harvesting a terminal candidate exits 0", rc6 == 0)
        case("(6) NO new record appended for a terminal candidate",
             candidates.check_candidate_chain(base4) == n_before)
        case("(6) the missing file is NOT re-materialised for a terminal "
             "candidate (terminal short-circuits before any filesystem write)",
             not os.path.isfile(event_path))
    finally:
        shutil.rmtree(base4, ignore_errors=True)

    # ============================================================ (7) --dry-run
    # writes nothing, on BOTH the fresh-harvest branch and the converge branch
    base5 = tempfile.mkdtemp(prefix="harvest-dryrun-")
    try:
        _git_init(base5)
        session_id = "sess-dryrun1"
        span = "a span used to prove --dry-run writes nothing"
        staging5 = os.path.join(base5, candidates.CANDIDATES_STAGING_DIR)
        rc_dry = run_harvest(base5, session_id, "finding", "plain-intake", span,
                              "corpus", dry_run=True)
        case("(7) --dry-run (fresh) exits 0", rc_dry == 0)
        case("(7) --dry-run (fresh) writes NOTHING (chain empty)",
             candidates.check_candidate_chain(base5) == 0)
        case("(7) --dry-run (fresh) creates no files",
             not os.path.isdir(staging5) or os.listdir(staging5) == [])

        run_harvest(base5, session_id, "finding", "plain-intake", span, "corpus")
        cid7 = candidates.make_candidate_id(session_id, span)
        rec7 = candidates.load_candidate_ledger(base5)[cid7]
        event_path7 = os.path.join(base5, *rec7["event"].split("/"))
        os.remove(event_path7)
        n_before7 = candidates.check_candidate_chain(base5)
        rc_dry2 = run_harvest(base5, session_id, "finding", "plain-intake", span,
                               "corpus", dry_run=True)
        case("(7) --dry-run (converge branch) exits 0", rc_dry2 == 0)
        case("(7) --dry-run (converge branch) does NOT re-materialise the file",
             not os.path.isfile(event_path7))
        case("(7) --dry-run (converge branch) appends NOTHING",
             candidates.check_candidate_chain(base5) == n_before7)
    finally:
        shutil.rmtree(base5, ignore_errors=True)

    # ============================================================ CLI edges
    buf_h = io.StringIO()
    with contextlib.redirect_stdout(buf_h):
        try:
            main(["-h"])
            rc_h = 0
        except SystemExit as e:
            rc_h = e.code
    out_h = buf_h.getvalue()
    case("-h exits 0 and prints usage, never runs anything live",
         rc_h == 0 and "usage" in out_h.lower())

    buf_err = io.StringIO()
    with contextlib.redirect_stderr(buf_err):
        try:
            main([])
            ok_noargs = False
        except SystemExit as e:
            ok_noargs = e.code != 0
    case("no arguments -> argparse refuses (required-arg error), never runs live",
         ok_noargs)

    buf_err2 = io.StringIO()
    with contextlib.redirect_stderr(buf_err2):
        try:
            main(["--root", ".", "--session-id", "s", "--class", "not-a-class",
                  "--disposition", "plain-intake", "--span-file", "nope.txt"])
            ok_badclass = False
        except SystemExit as e:
            ok_badclass = e.code != 0
    case("an out-of-enum --class is refused at the argparse level",
         ok_badclass)

    if failed:
        print("harvest-candidates self-test: FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("harvest-candidates self-test: PASS (%d/%d)" % (total, total))
    return 0


# ------------------------------------------------------------------ CLI
_USAGE = ("harvest-candidates.py --root DIR --session-id ID --class CLASS "
          "--disposition DISP --span-file PATH [--origin-max ORIGIN] "
          "[--transcript-pointer TEXT] [--dry-run]\n"
          "       harvest-candidates.py --self-test")


def main(argv):
    # --self-test is handled BEFORE the argparse parser is even built: every
    # other flag below is required=True (Part 5's CLI grammar has no brackets
    # around --root/--session-id/--class/--disposition/--span-file), and
    # argparse validates "required" before any code of ours can inspect
    # args.self_test. Scanning argv directly sidesteps that ordering problem
    # without weakening the required-arg refusal for real invocations. -h is
    # NOT intercepted here, so it still reaches argparse and gets argparse's
    # own well-formed usage + exit 0.
    if "--self-test" in argv:
        return self_test()

    p = argparse.ArgumentParser(prog="harvest-candidates.py", usage=_USAGE,
        description="R-1 session-candidate harvester: one candidate per "
                     "invocation, idempotent by candidate_id (Part 5).")
    p.add_argument("--root", required=True, help="repo root")
    p.add_argument("--session-id", required=True, dest="session_id",
                    help="the harvesting session's id (becomes part of the "
                         "candidate_id and the filename -- charset-restricted)")
    p.add_argument("--class", required=True, dest="candidate_class",
                    choices=candidates.CANDIDATE_CLASSES, help="candidate class")
    p.add_argument("--disposition", required=True,
                    choices=candidates.DISPOSITIONS, help="proposed disposition")
    p.add_argument("--span-file", required=True, dest="span_file",
                    help="path to a file containing the verbatim span "
                         "(read byte-exactly, utf-8 BOM-tolerant)")
    p.add_argument("--origin-max", default="session-derived", dest="origin_max",
                    choices=origin.ORIGIN_ORDER,
                    help="the harvesting session's inherited taint ceiling "
                         "(default: session-derived)")
    p.add_argument("--transcript-pointer", default="", dest="transcript_pointer",
                    help="free-form, advisory-only locator")
    p.add_argument("--dry-run", action="store_true",
                    help="print what would happen, write NOTHING")
    p.add_argument("--self-test", action="store_true",
                    help="offline self-test (temp dirs only, no repo access) "
                         "-- kept here only so --help documents it; the real "
                         "dispatch happens before this parser is built")
    args = p.parse_args(argv)

    if yaml is None:
        print("RESULT: INCONCLUSIVE -- PyYAML unavailable")
        return 2

    root = os.path.abspath(args.root)
    try:
        span = _read_span_file(args.span_file)
    except OSError as e:
        print("REFUSED: could not read --span-file %r: %s" % (args.span_file, e))
        return 1

    try:
        return run_harvest(root, args.session_id, args.candidate_class,
                            args.disposition, span, args.origin_max,
                            transcript_pointer=args.transcript_pointer,
                            dry_run=args.dry_run)
    except (candidates.CandidateViolation, HarvestViolation) as e:
        print("REFUSED: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
