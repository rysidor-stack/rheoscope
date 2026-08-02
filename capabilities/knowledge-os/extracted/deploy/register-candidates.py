#!/usr/bin/env python3
"""register-candidates.py -- R-1 candidate reconciler (memory-engine v3, R-1
leg C). The GUARANTEE, per Q1's resolution: harvest-candidates.py is a plain,
idempotent CLI safe to call from either end, but the reconciler run in a
healthy session (flight-plan step 5's unprocessed-work scan, /standing-loop)
is what actually makes correctness durable across a crashed or never-run
harvester.

Spec: harness-v3.0/specs/r1-build-decisions-2026-07-22.md Part 5 (this
module's algorithm, verbatim) -- read Part 3/4 in deploy/candidates.py for the
data shapes; Part 8 acceptance criterion 4 ("conservation by chain") is a
sibling leg's drill (check-candidates.py), but this module's job -- never
silently leaving an orphan file or a stale 'staged' candidate behind -- is
what makes that drill's green outcome achievable at all.

FOUR THINGS THIS MODULE DOES, IN ORDER, EVERY RUN (Part 5 steps 2-4):
  1. any staged .md file with NO chain entry (an orphan -- almost always a
     harvest that crashed between its file-write and its record-append) is
     picked up: a valid file gets a 'staged' record (converging the crash);
     an invalid file gets a 'quarantined' record naming the schema error.
  2. any candidate whose effective state is 'staged' (INCLUDING one just
     minted by step 1, in the SAME pass -- see compute_actions()'s docstring
     for why this is deliberate, not an oversight) whose file still
     validates is promoted to 'registered'.
  3. any 'registered' candidate whose file's own harvested_at is more than
     14 days before the run date is marked 'expired' -- a ledger STATE, never
     a deletion (Part 1 Q3: the census never blocks on an un-dispositioned
     candidate, only on conservation violations).
  4. a census by state is printed, naming every quarantined file individually.

TWO DIFFERENT KINDS OF "BAD", NOT TO BE CONFUSED (read this before touching
the pre-flight below): a MALFORMED CANDIDATE FILE is an EXPECTED input class
here -- quarantined one-by-one, counted, and named; it never blocks anything
else in the batch. A MALFORMED LEDGER RECORD -- this engine itself assembling
a record that fails candidates.validate_candidate_record's schema gate --
would be a BUG in this module, not a fact about someone's input, and gets the
opposite treatment: the WHOLE planned batch is schema-validated BEFORE any
append (mirroring register-intake.py's D4/D5 fail-closed pre-flight), and a
single bad record refuses everything, nothing written. These two gates sit at
completely different layers on purpose.

Usage:
  register-candidates.py --root DIR [--dry-run]
  register-candidates.py --self-test
Exit: 0 ok (reconciled, or "nothing to do") |
      1 violation (a malformed planned RECORD -- an engine bug, not a
        malformed candidate FILE -- or a write failure mid-loop) |
      2 inconclusive (PyYAML unavailable -- candidates.validate_candidate_file
        needs it to parse frontmatter at all).
"""

import argparse
import datetime
import hashlib
import importlib.util
import os
import re
import sys

try:  # cp1252 consoles + unicode candidate ids/spans: never mask a verdict.
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(basename, alias):
    spec = importlib.util.spec_from_file_location(alias, os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Sibling-import by path -- deploy/ is a flat directory, never a package.
# Distinct alias from candidates.py's own internal _load calls and from
# harvest-candidates.py's -- each _load() mints a fresh module object.
candidates = _load("candidates.py", "candidates_register")

try:
    import yaml
except ImportError:  # pragma: no cover -- optional-yaml idiom, matches the rest of deploy/.
    yaml = None


class ReconcileViolation(Exception):
    """Raised for the two loud-refusal classes this module owns: a planned
    ledger record that fails schema validation (an engine bug, caught by the
    pre-flight before any write), and a write that fails mid-loop after some
    records already landed (an append-only chain cannot be rolled back, so
    this names exactly what happened instead)."""
    pass


_EXPIRY_DAYS = 14

# Recovers a real candidate_id from a malformed staged file's own FILENAME
# (best-effort, for a human reading the ledger) -- the frontmatter is exactly
# what failed to parse, so this is the only place a real id could still come
# from. Falls back to a deterministic hash of the path when the filename
# itself doesn't even match the expected shape.
_EVENT_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-candidate-(.+)\.md$")


def _quarantine_meta(root, rel_path, reason):
    """Build a schema-satisfying synthetic `meta` dict for a staged file that
    FAILED TO PARSE at all -- there is no real candidate_class / origin_max /
    proposed_disposition / span to recover from it, so every field below is a
    DELIBERATE, DOCUMENTED PLACEHOLDER chosen for safety, never a guess at
    the file's actual (unreadable) content. The real reason lives in the
    ledger record's `note`, not in these fields.

      candidate_id          recovered from the FILENAME's own
                             <date>-candidate-<id>.md shape when it matches;
                             a hash-of-path fallback otherwise. Idempotence
                             does NOT depend on this value being "the" real
                             id -- orphan detection below is by literal event
                             PATH membership in the ledger history, never by
                             candidate_id, so a synthetic id here still
                             converges cleanly on a second run.
      candidate_class        'finding' -- an arbitrary schema-satisfying
                             member; CANDIDATE_CLASSES has no "unclassifiable"
                             option, so this can never be more than a filler.
      origin                 'session-derived' -- the floor itself, the most
                             restrictive value ANY legitimate candidate record
                             may ever carry, so a quarantined record can never
                             read as MORE trustworthy than the taxonomy allows.
      origin_max              'unknown' -- the single most-tainted member of
                             ORIGIN_ORDER: a file that cannot even be parsed
                             gets the worst-case taint assumption, never the
                             benefit of the doubt.
      proposed_disposition   'discard-candidate' -- a quarantined record must
                             NEVER be eligible for the decision-inbox surface
                             (which filters on proposed_disposition ==
                             'transcribe-as-operator-event'); `state` being
                             terminal already excludes it independently, this
                             is belt and braces.
      span_sha256             sha256 of the RAW FILE BYTES on disk -- NOT a
                             verified, schema-checked span (there isn't one).
                             Ties the record to the actual bytes for forensic
                             traceability only; it carries none of the
                             guarantee a well-formed record's span_sha256 does.
    """
    m = _EVENT_ID_RE.match(os.path.basename(rel_path))
    if m:
        candidate_id = m.group(1)
    else:
        candidate_id = "malformed-" + hashlib.sha256(
            rel_path.encode("utf-8")).hexdigest()[:16]
    full = os.path.join(root, *rel_path.split("/"))
    with open(full, "rb") as fh:
        raw = fh.read()
    return {
        "candidate_id": candidate_id,
        "candidate_class": "finding",
        "origin": "session-derived",
        "origin_max": "unknown",
        "proposed_disposition": "discard-candidate",
        "span_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _harvested_date(harvested_at_str):
    """The first 10 characters are a guaranteed ISO calendar date (candidates.
    py's _HARVESTED_AT_RE enforces this at write time) -- the 14-day expiry
    check is day-granularity per the spec text ('more than 14 days before the
    run date'), so any time-of-day component is deliberately ignored here."""
    return datetime.date.fromisoformat(harvested_at_str[:10])


def _preflight(rec):
    """The engine-bug pre-flight gate (module docstring): validate_candidate_
    record needs seq/prev_record_hash present (engine-owned fields new_record
    deliberately omits) -- supply placeholders for the schema check only,
    exactly the idiom register-intake.py uses against registrations.
    validate_registration."""
    candidates.validate_candidate_record(dict(rec, seq=1, prev_record_hash=None))


# ------------------------------------------------------------------ planning
def compute_actions(root, now=None):
    """Build the reconciler's FULL prospective batch of ledger-record appends
    from a single snapshot of the world (Part 5 steps 1-3), pre-flight-
    validating every one as it is planned (see _preflight -- an engine-bug
    gate, NOT the same thing as an individual file's malformed-candidate
    quarantine). Returns (actions, report, effective_after):
      actions          ordered list of record dicts ready for
                       append_candidate_record, in the exact order they must
                       be appended (orphan staged/quarantined first, then
                       staged->registered, then registered->expired -- Part
                       5's own step order).
      report            dict of path lists for the human-readable plan/log.
      effective_after   the PLANNED end-state effective map (candidate_id ->
                       record-shaped dict) -- used for the final census
                       whether or not `actions` is actually written yet (a
                       --dry-run reports what WOULD be true).

    WHY STEP 2 (staged->registered) SEES STEP 1's OWN NEW 'staged' ENTRIES,
    IN THE SAME PASS: Part 5's self-test requirement is explicit -- "an
    orphan staged file (valid, no chain entry) gets a `staged` record then a
    `registered` record" in ONE reconciler run, not two. This mirrors the
    ledger's own supersedes-by-later-seq mechanic: `effective` here is
    mutated as each planned transition is added, so a later step in this same
    function sees the PLANNED state, not just what was already on disk when
    the run started."""
    if now is None:
        now = datetime.date.today()

    staged_paths = candidates.staged_files(root)
    history = candidates.ledger_history(root)
    effective = candidates.load_candidate_ledger(root)  # candidate_id -> record
    known_events = {r["event"] for r in history}

    actions = []
    report = {
        "orphan_staged": [], "quarantined": [], "registered": [],
        "expired": [], "skipped_unreadable": [],
    }

    # ---- step 1 (Part 5 step 2): orphan staged files, no chain entry at all --
    for rel in staged_paths:
        if rel in known_events:
            continue
        full = os.path.join(root, *rel.split("/"))
        try:
            meta, _span = candidates.validate_candidate_file(full)
        except candidates.CandidateViolation as e:
            q_meta = _quarantine_meta(root, rel, str(e))
            rec = candidates.new_record(q_meta, "quarantined",
                                         "quarantined: %s" % e, rel)
            _preflight(rec)
            actions.append(rec)
            effective[q_meta["candidate_id"]] = rec
            report["quarantined"].append(rel)
            continue
        except OSError:
            # A file listed a moment ago by staged_files() vanished before we
            # got to it (a genuine TOCTOU race, not a malformed-candidate
            # class). Nothing to converge to -- the NEXT run's staged_files()
            # call simply will not list it anymore. Not a chain event.
            continue

        candidate_id = meta["candidate_id"]
        existing = effective.get(candidate_id)
        if existing is not None and existing["event"] != rel:
            # candidate_id collision: a live chain entry already claims this
            # id under a DIFFERENT file. This should not happen under normal
            # operation (candidate_id is embedded in the filename, so two
            # files could only collide via a hand-crafted/duplicated file) --
            # refuse to mint a second 'staged' record for the same id and
            # surface it for operator review rather than silently letting one
            # candidate_id's effective state be decided by file-processing
            # order.
            note = ("candidate_id %s already has a live chain entry pointing "
                     "at a DIFFERENT staged file (%s) -- refusing to mint a "
                     "second 'staged' record for the same id under %s "
                     "(id collision, quarantined for operator review)"
                     % (candidate_id, existing["event"], rel))
            rec = candidates.new_record(meta, "quarantined", note, rel)
            _preflight(rec)
            actions.append(rec)
            report["quarantined"].append(rel)
            continue

        rec = candidates.new_record(
            meta, "staged",
            "orphan staged file converged by register-candidates (no chain "
            "entry found -- a crashed harvest, most likely)", rel)
        _preflight(rec)
        actions.append(rec)
        report["orphan_staged"].append(rel)
        effective[candidate_id] = rec

    # ---- step 2 (Part 5 step 3): staged -> registered -------------------------
    for candidate_id, rec in list(effective.items()):
        if rec["state"] != "staged":
            continue
        rel = rec["event"]
        full = os.path.join(root, *rel.split("/"))
        try:
            meta, _span = candidates.validate_candidate_file(full)
        except (candidates.CandidateViolation, OSError):
            # A previously-accepted staged candidate's file no longer
            # validates (hand-corrupted after staging) or has vanished. NOT
            # this reconciler's job to quarantine a file out from under a
            # LIVE chain entry -- that is check-candidates.py's conservation-
            # census territory (a sibling leg, Part 7). Leave it exactly as
            # it is this run; it will be picked up again next run if it
            # starts validating again.
            report["skipped_unreadable"].append(rel)
            continue
        rec2 = candidates.new_record(
            meta, "registered", "registered by register-candidates reconciler",
            rel)
        _preflight(rec2)
        actions.append(rec2)
        report["registered"].append(rel)
        effective[candidate_id] = rec2

    # ---- step 3 (Part 5 step 4): registered -> expired at 14 days -------------
    for candidate_id, rec in list(effective.items()):
        if rec["state"] != "registered":
            continue
        rel = rec["event"]
        full = os.path.join(root, *rel.split("/"))
        try:
            meta, _span = candidates.validate_candidate_file(full)
        except (candidates.CandidateViolation, OSError):
            report["skipped_unreadable"].append(rel)
            continue
        age_days = (now - _harvested_date(meta["harvested_at"])).days
        if age_days <= _EXPIRY_DAYS:
            continue
        rec3 = candidates.new_record(
            meta, "expired", "expired to plain intake at 14 days", rel)
        _preflight(rec3)
        actions.append(rec3)
        report["expired"].append(rel)
        effective[candidate_id] = rec3

    return actions, report, effective


def _census(effective_after):
    counts = {}
    quarantined = []
    for rec in effective_after.values():
        counts[rec["state"]] = counts.get(rec["state"], 0) + 1
        if rec["state"] == "quarantined":
            quarantined.append(rec["event"])
    return counts, sorted(quarantined)


def _print_census(counts, quarantined_names):
    print("  census by state: %s" % counts)
    if quarantined_names:
        print("  quarantined file(s) (%d), named individually:" % len(quarantined_names))
        for f in quarantined_names:
            print("    %s" % f)


# ------------------------------------------------------------------ run
def run_register_candidates(root, dry_run=False, now=None):
    actions, report, effective_after = compute_actions(root, now=now)
    counts, quarantined_names = _census(effective_after)

    if not actions:
        print("register-candidates: nothing to do (idempotent) -- 0 records "
              "appended, chain unchanged")
        _print_census(counts, quarantined_names)
        return 0

    if dry_run:
        print("DRY-RUN (nothing written): %d action(s) planned "
              "(%d orphan->staged, %d quarantined, %d staged->registered, "
              "%d registered->expired)"
              % (len(actions), len(report["orphan_staged"]),
                 len(report["quarantined"]), len(report["registered"]),
                 len(report["expired"])))
        _print_census(counts, quarantined_names)
        return 0

    appended = []
    for rec in actions:
        try:
            seq, _path = candidates.append_candidate_record(root, rec)
        except Exception as e:
            raise ReconcileViolation(
                "append FAILED for candidate %r (event %r) after successfully "
                "appending %d/%d planned record(s) -- STOPPED immediately, "
                "this is a NAMED partial mint, never a silent one. Appended "
                "before the failure: %s. Underlying error (%s): %s"
                % (rec.get("candidate_id"), rec.get("event"), len(appended),
                   len(actions),
                   [(s, r.get("candidate_id")) for s, r in appended],
                   type(e).__name__, e))
        appended.append((seq, rec))

    n = candidates.check_candidate_chain(root)
    print("register-candidates: appended %d record(s); chain check green "
          "(%d total)" % (len(actions), n))
    _print_census(counts, quarantined_names)
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

    def _mint_staged(root, session_id, span, candidate_class="finding",
                      origin_max="corpus",
                      proposed_disposition="transcribe-as-operator-event",
                      harvested_at="2026-07-22T00:00:00",
                      transcript_pointer="fixture", append_record=True):
        """Fixture helper: write a well-formed candidate file directly via
        candidates.write_candidate_atomic and, unless told otherwise, mint its
        'staged' chain record -- the same two-step sequence harvest-
        candidates.py's own fresh-harvest path performs, reproduced here
        rather than importing that sibling module (this module's only
        sanctioned dependency is candidates.py, per the build instructions)."""
        candidate_id = candidates.make_candidate_id(session_id, span)
        meta = {
            "kind": "session-candidate",
            "candidate_id": candidate_id,
            "session_id": session_id,
            "candidate_class": candidate_class,
            "origin": "session-derived",
            "origin_max": origin_max,
            "proposed_disposition": proposed_disposition,
            "transcript_pointer": transcript_pointer,
            "harvested_at": harvested_at,
            "span_sha256": candidates.span_hash(span),
        }
        path = candidates.write_candidate_atomic(root, meta, span)
        event_rel = os.path.relpath(path, root).replace("\\", "/")
        if append_record:
            rec = candidates.new_record(meta, "staged", "fixture: staged directly",
                                         event_rel)
            candidates.append_candidate_record(root, rec)
        return candidate_id, event_rel, path

    # ============================================================ (A)/(D):
    # staged -> registered, then idempotence on the SAME root
    baseA = tempfile.mkdtemp(prefix="register-cand-A-")
    try:
        _git_init(baseA)
        cidA, eventA, pathA = _mint_staged(baseA, "sess-regA",
                                            "span for registration test")
        case("(A) setup: candidate is 'staged' before the run",
             candidates.effective_state(baseA, cidA) == "staged")
        rc = run_register_candidates(baseA, dry_run=False)
        case("(A) register-candidates exits 0", rc == 0)
        case("(A) the staged candidate is now 'registered'",
             candidates.effective_state(baseA, cidA) == "registered")

        n_before_idem = candidates.check_candidate_chain(baseA)
        buf_idem = io.StringIO()
        with contextlib.redirect_stdout(buf_idem):
            rc_idem = run_register_candidates(baseA, dry_run=False)
        out_idem = buf_idem.getvalue()
        case("(D) second immediate run exits 0", rc_idem == 0)
        case("(D) second immediate run appends ZERO records",
             candidates.check_candidate_chain(baseA) == n_before_idem)
        case("(D) second immediate run SAYS SO in its output",
             "nothing to do" in out_idem.lower())
    finally:
        shutil.rmtree(baseA, ignore_errors=True)

    # ============================================================ (B): orphan
    # staged file (valid, no chain entry) -> staged THEN registered, same run
    baseB = tempfile.mkdtemp(prefix="register-cand-B-")
    try:
        _git_init(baseB)
        cidB, eventB, pathB = _mint_staged(baseB, "sess-orphanB",
                                            "span for orphan test",
                                            append_record=False)
        case("(B) setup: orphan file has NO chain entry at all",
             candidates.effective_state(baseB, cidB) is None)
        rc = run_register_candidates(baseB, dry_run=False)
        case("(B) register-candidates over an orphan exits 0", rc == 0)
        hist_states = [r["state"] for r in candidates.ledger_history(baseB)
                       if r["candidate_id"] == cidB]
        case("(B) the orphan gets a 'staged' record then a 'registered' "
             "record, in the SAME run",
             hist_states == ["staged", "registered"])
        case("(B) effective state is 'registered'",
             candidates.effective_state(baseB, cidB) == "registered")
    finally:
        shutil.rmtree(baseB, ignore_errors=True)

    # ============================================================ (C): a
    # MALFORMED staged file -> quarantined, named in the output, never registered
    baseC = tempfile.mkdtemp(prefix="register-cand-C-")
    try:
        _git_init(baseC)
        staging_c = os.path.join(baseC, candidates.CANDIDATES_STAGING_DIR)
        os.makedirs(staging_c, exist_ok=True)
        rel_bad = ("intake/session-candidates/"
                   "2026-07-22-candidate-badbadbad-0000000000000000.md")
        bad_path = os.path.join(baseC, *rel_bad.split("/"))
        with open(bad_path, "w", encoding="utf-8") as fh:
            fh.write("this is not a valid candidate file at all\n")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = run_register_candidates(baseC, dry_run=False)
        out = buf.getvalue()
        case("(C) register-candidates over a malformed file exits 0 "
             "(quarantine, not a crash)", rc == 0)
        case("(C) the malformed file's effective state is 'quarantined'",
             candidates.effective_state(baseC, "badbadbad-0000000000000000")
             == "quarantined")
        case("(C) the quarantined file is NAMED individually in the output",
             rel_bad in out)

        n_before_c = candidates.check_candidate_chain(baseC)
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            run_register_candidates(baseC, dry_run=False)
        case("(C) re-running over an already-quarantined file appends "
             "NOTHING (never re-quarantined)",
             candidates.check_candidate_chain(baseC) == n_before_c)
        case("(C) the file is NEVER registered",
             candidates.effective_state(baseC, "badbadbad-0000000000000000")
             == "quarantined")
    finally:
        shutil.rmtree(baseC, ignore_errors=True)

    # ============================================================ (E): expiry
    # boundary (15 days expires, 13 does not) + expiry does not delete the file
    baseE = tempfile.mkdtemp(prefix="register-cand-E-")
    try:
        _git_init(baseE)
        now = datetime.date(2026, 7, 22)
        harvested_15 = (now - datetime.timedelta(days=15)).isoformat() + "T00:00:00"
        harvested_13 = (now - datetime.timedelta(days=13)).isoformat() + "T00:00:00"

        cid15, event15, path15 = _mint_staged(baseE, "sess-exp15",
                                               "span aged 15 days",
                                               harvested_at=harvested_15)
        cid13, event13, path13 = _mint_staged(baseE, "sess-exp13",
                                               "span aged 13 days",
                                               harvested_at=harvested_13)

        rc = run_register_candidates(baseE, dry_run=False, now=now)
        case("(E) expiry-aware run exits 0", rc == 0)
        case("(E) the 15-day-old candidate is 'expired'",
             candidates.effective_state(baseE, cid15) == "expired")
        case("(E) the 13-day-old candidate is NOT expired (still "
             "'registered')", candidates.effective_state(baseE, cid13)
             == "registered")
        case("(E) expiry does NOT delete the staged file",
             os.path.isfile(path15))
        m15, s15 = candidates.validate_candidate_file(path15)
        case("(E) the expired candidate's file still parses (state lives "
             "only in the ledger)", s15 == "span aged 15 days")
    finally:
        shutil.rmtree(baseE, ignore_errors=True)

    # ============================================================ (F): mid-loop
    # write-failure reports N/M and stops
    baseF = tempfile.mkdtemp(prefix="register-cand-F-")
    try:
        _git_init(baseF)
        _mint_staged(baseF, "sess-midfailX", "span X for midloop test",
                     append_record=False)
        _mint_staged(baseF, "sess-midfailY", "span Y for midloop test",
                     append_record=False)

        orig_append = candidates.append_candidate_record
        calls = {"n": 0}

        def _flaky(root_, record_):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated write failure on the 2nd append")
            return orig_append(root_, record_)

        candidates.append_candidate_record = _flaky
        try:
            try:
                run_register_candidates(baseF, dry_run=False)
                case("(F) a write failure after 1 success stops immediately "
                     "and reports (never silently continues)", False)
            except ReconcileViolation as e:
                case("(F) a write failure after 1 success stops immediately "
                     "and reports (never silently continues)", "1/4" in str(e))
        finally:
            candidates.append_candidate_record = orig_append
        n_after_f = candidates.check_candidate_chain(baseF)
        case("(F) exactly 1 successful append landed (reported, not hidden -- "
             "true rollback is impossible append-only)", n_after_f == 1)
    finally:
        shutil.rmtree(baseF, ignore_errors=True)

    # ============================================================ --dry-run
    # writes nothing but reports the plan
    baseG = tempfile.mkdtemp(prefix="register-cand-G-")
    try:
        _git_init(baseG)
        _mint_staged(baseG, "sess-dryG", "span for dry-run test",
                     append_record=False)
        n_before_g = candidates.check_candidate_chain(baseG)
        buf_g = io.StringIO()
        with contextlib.redirect_stdout(buf_g):
            rc_g = run_register_candidates(baseG, dry_run=True)
        out_g = buf_g.getvalue()
        case("--dry-run exits 0", rc_g == 0)
        case("--dry-run writes NOTHING (chain count unchanged)",
             candidates.check_candidate_chain(baseG) == n_before_g)
        case("--dry-run reports the planned action count",
             "action(s) planned" in out_g)
    finally:
        shutil.rmtree(baseG, ignore_errors=True)

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

    if failed:
        print("register-candidates self-test: FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("register-candidates self-test: PASS (%d/%d)" % (total, total))
    return 0


# ------------------------------------------------------------------ CLI
_USAGE = ("register-candidates.py --root DIR [--dry-run]\n"
          "       register-candidates.py --self-test")


def main(argv):
    p = argparse.ArgumentParser(
        prog="register-candidates.py", usage=_USAGE,
        description="R-1 candidate reconciler: converges crashed harvests, "
                     "promotes staged candidates to registered, expires "
                     "candidates older than 14 days, and quarantines "
                     "malformed staged files (Part 5).")
    p.add_argument("--root", default=".", help="repo root (default: .)")
    p.add_argument("--dry-run", action="store_true",
                    help="compute + print the planned batch, write NOTHING")
    p.add_argument("--self-test", action="store_true",
                    help="offline self-test (temp dirs only, no repo access)")
    args = p.parse_args(argv)

    if args.self_test:
        return self_test()
    if yaml is None:
        print("RESULT: INCONCLUSIVE -- PyYAML unavailable")
        return 2
    root = os.path.abspath(args.root)
    try:
        return run_register_candidates(root, dry_run=args.dry_run)
    except ReconcileViolation as e:
        print("REFUSED: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
