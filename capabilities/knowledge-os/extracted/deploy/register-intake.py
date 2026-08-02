#!/usr/bin/env python3
"""register-intake.py -- B-1 delta registration (trusted-batch mode); memory-engine v3
steady-state operations layer (harness-v3.0/specs/memory-engine-v3-steady-state-
operations-brief-2026-07-08.md, section B-1).

PROBLEM THIS SOLVES: the P5 registration chain (receipts/registrations/, C1) is
growth-ready append-only, but backfill-registrations.py's ONLY entry point is the
one-time bulk mint, which refuses outright the moment the chain already has records
(idempotence = refusal, by design -- see that module's docstring). That refusal is
correct for the bulk mint, but it also means there is NO sanctioned path to register
raw events staged onto the ledger AFTER the mint (e.g. the 92-event first-live
compile-v2 pilot batch). This module is that sanctioned incremental path.

D1 -- DELTA, NOT RE-MINT. Enumerate the SAME populations backfill-registrations.py
does (receipts/*.md minus engine sidecars, raw/*.md) via
backfill_registrations.build_records(root) -- imported, NEVER reimplemented, so
origin/event_class/asserts_corpus_state derivation is byte-identical to the bulk
mint. Diff against registrations.load_registrations(root)'s effective map (event id
-> record) and register ONLY the events not already present. Existing records are
NEVER touched, NEVER overwritten -- this is a pure append onto the existing chain via
registrations.append_registration, which allocates the next seq and chains
prev_record_hash the same way the bulk mint's own per-record appends do.

D2 -- ORIGIN IS MECHANICAL. build_records already computes origin via origin.py's
assign_origin (ryan-* -> human, session-*/observation-*/other-parseable -> corpus,
unparseable -> unknown). This module applies NO upgrade, NO special-casing: whatever
build_records derives is what gets registered. Trusted-batch mode: no F9
session-intake floor is applied here (no session intake record exists for a
hand-delivered batch); the operator vouches for the batch, and the committed batch
attestation (e.g. deploy/evidence/operator-*-pilot.md) is the record of that.

D3 -- unknown IS A VALID, CONSERVATIVE ORIGIN, NOT A BLOCKER. An unparseable new
event registers with origin "unknown" exactly like the bulk mint would have minted
it, and the registration succeeds (never refused, never blocked on parseability --
registration's entire point, per registrations.py's docstring, is that it never
requires the event file to parse). The unknown-origin subset of the delta is
surfaced by name in the run's report as a data-quality note; upgrading any of them
(attestation) is a separate, optional, later operator act -- never required here.

D4/D5 -- FAIL-CLOSED ATOMICITY. Before any record is written, the WHOLE delta batch
is pre-flight schema-validated (registrations.validate_registration against each
would-be record) -- a single malformed record refuses the ENTIRE batch, nothing is
written (true all-or-nothing for the validation-failure class, mirroring the bulk
mint's own idempotence-is-refusal all-or-nothing posture). If a write itself still
fails mid-loop for an unanticipated reason (the append-only chain substrate itself
cannot be "rolled back" without violating append-only -- an already-successful
append is a committed record, same as a bulk-mint record), the run STOPS
immediately and raises IntakeViolation naming exactly how many records succeeded,
which one failed, and why -- a half-mint is always LOUD and fully named, never a
silent partial state. This is the honest ceiling of "atomicity" available under an
append-only chain (see the module's --self-test "(atomicity...)" cases for both
failure classes proven separately).

Usage:
  register-intake.py --root DIR [--dry-run]
  register-intake.py --self-test
Exit: 0 ok (delta registered, or "no new events to register") |
      1 violation (malformed delta record / write failure / broken chain) |
      2 inconclusive (PyYAML unavailable).
"""

import argparse
import importlib.util
import os
import sys

try:  # cp1252 consoles + unicode event names: never let an encode error mask a verdict.
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(basename, alias):
    spec = importlib.util.spec_from_file_location(alias, os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Sibling-import by path -- the same mechanism backfill-registrations.py itself uses
# to import registrations.py/staleness.py/origin.py (deploy/ is a flat directory,
# never a package).
backfill = _load("backfill-registrations.py", "backfill_registrations_intake")
regs = _load("registrations.py", "registrations_intake")

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


class IntakeViolation(Exception):
    pass


# ------------------------------------------------------------------ delta computation
def compute_delta(root, origin_config_path=None):
    """Return (delta, records, receipts, raws). `records`/`receipts`/`raws` are
    backfill_registrations.build_records(root)'s full-population output (REUSED
    verbatim, never reimplemented -- guarantees byte-identical origin/event_class/
    asserts_corpus_state derivation to the bulk mint). `delta` is the subset of
    `records` whose `event` is NOT already a key in
    registrations.load_registrations(root)'s effective map -- i.e. the events the
    bulk mint (or a prior register-intake run) has never registered.

    load_registrations(root) returns {} for a repo whose registration store has
    never been minted at all (registrations.check_registration_chain treats a
    missing directory as 0 records, not an error) -- so this function works
    unchanged whether the chain already exists or not. It RAISES (propagated,
    never swallowed) for a PRESENT but broken/tampered chain -- computing a delta
    against a corrupt chain is refused, not silently attempted.

    `origin_config_path` is a self-test seam threaded through build_records to
    origin.assign_origin (which origin-config file names the human_prefixes) --
    production callers never pass it, so a live delta always reads the live
    deploy/origin-config.yaml."""
    records, receipts, raws = backfill.build_records(
        root, origin_config_path=origin_config_path)
    effective = regs.load_registrations(root)
    delta = [r for r in records if r["event"] not in effective]
    return delta, records, receipts, raws


def census_by_origin(delta):
    c = {}
    for r in delta:
        c[r["origin"]] = c.get(r["origin"], 0) + 1
    return c


def unknown_events(delta):
    return sorted(r["event"] for r in delta if r["origin"] == "unknown")


def _print_unknown(unk):
    if unk:
        print("  unknown-origin events (data-quality surface, %d):" % len(unk))
        for e in unk:
            print("    %s" % e)


def _delta_population_counts(delta):
    receipts_n = sum(1 for r in delta if r["event"].split("/", 1)[0] == "receipts")
    return receipts_n, len(delta) - receipts_n


# ------------------------------------------------------------------ register
def run_register_intake(root, dry_run=False, origin_config_path=None):
    delta, records, receipts, raws = compute_delta(
        root, origin_config_path=origin_config_path)

    if not delta:
        print("no new events to register")
        return 0

    by_origin = census_by_origin(delta)
    unk = unknown_events(delta)
    d_receipts, d_raws = _delta_population_counts(delta)

    if dry_run:
        print("DRY-RUN DELTA CENSUS (nothing written): %d new event(s) of %d total "
              "ledger member(s) (%d already registered)"
              % (len(delta), len(records), len(records) - len(delta)))
        print("  delta by_population      {'receipts': %d, 'raw': %d}" % (d_receipts, d_raws))
        print("  delta by_origin           %s" % by_origin)
        _print_unknown(unk)
        return 0

    # D4/D5 -- fail-closed pre-flight: validate the WHOLE delta's schema before any
    # write. A single malformed record refuses the entire batch, nothing written
    # (all-or-nothing, like the bulk mint's own idempotence-is-refusal guard).
    for rec in delta:
        try:
            regs.validate_registration(dict(rec, seq=1, prev_record_hash=None))
        except regs.RegistrationViolation as e:
            raise IntakeViolation(
                "refusing to register ANY of the %d-event delta -- record for %r "
                "fails schema validation (all-or-nothing pre-flight, nothing "
                "written): %s" % (len(delta), rec.get("event"), e))

    registered = []
    for rec in delta:
        try:
            seq, _path = regs.append_registration(root, rec)
        except Exception as e:
            raise IntakeViolation(
                "append FAILED at %r after successfully registering %d/%d delta "
                "record(s) -- STOPPED immediately, this is a NAMED partial mint, "
                "never a silent one. Registered before the failure: %s. "
                "Underlying error (%s): %s"
                % (rec.get("event"), len(registered), len(delta),
                   [ev for _s, ev in registered], type(e).__name__, e))
        registered.append((seq, rec["event"]))

    n = regs.check_registration_chain(root)  # assert green -- raises loud if not
    print("registered %d new registration record(s); chain check green (%d total)"
          % (len(delta), n))
    print("  delta by_population      {'receipts': %d, 'raw': %d}" % (d_receipts, d_raws))
    print("  delta by_origin           %s" % by_origin)
    _print_unknown(unk)
    return 0


# ------------------------------------------------------------------ self-test
def self_test():
    import contextlib
    import io
    import shutil
    import tempfile
    total = failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    def _write(base, rel, text):
        p = os.path.join(base, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    # ---- (a)/(c)/(d): bulk-mint the fixture repo (reusing
    # backfill_registrations._make_fixture_repo -- the committed fixture source at
    # deploy/test-fixtures/memory-engine/registrations/), add K=3 new raw events
    # spanning ryan-*/session-*/unparseable, run register-intake, assert EXACTLY
    # K registered and the chain stays green.
    base = tempfile.mkdtemp(prefix="register-intake-")
    try:
        backfill._make_fixture_repo(base)
        # Origin-config self-sufficiency: case (d) below asserts ryan-* -> human,
        # which must hold regardless of what the LIVE deploy/origin-config.yaml
        # says (a fresh instance configured for a different operator, or none at
        # all, must not turn this suite red) -- so this block's mint + delta both
        # read their OWN [ryan] fixture config (backfill's shared fixture helper)
        # via the origin_config_path seam.
        cfg = backfill._make_origin_config_fixture(base)
        rc0 = backfill.run_backfill(base, dry_run=False, origin_config_path=cfg)
        case("(setup) bulk mint over the fixture repo exits 0", rc0 == 0)
        n0 = regs.check_registration_chain(base)
        case("(setup) fixture bulk-mints 5 records (2 receipts + 3 raw)", n0 == 5)

        _write(base, "raw/2026-07-09-ryan-new-decision.md",
               "---\nsource: ryan\ndate: 2026-07-09\n---\nnew ryan decision body\n")
        _write(base, "raw/2026-07-09-session-9-new-note.md",
               "---\nsource: session\ndate: 2026-07-09\n---\nnew session note body\n")
        _write(base, "raw/2026-07-09-mystery-event.md",
               "no frontmatter at all -- starts with plain prose.\n")

        rc1 = run_register_intake(base, dry_run=False, origin_config_path=cfg)
        case("(a) register-intake over the delta exits 0", rc1 == 0)
        n1 = regs.check_registration_chain(base)
        case("(a) registers EXACTLY K=3 new records onto the existing chain (5 -> 8)",
             n1 == 8)

        loaded = regs.load_registrations(base)
        case("(a) all 3 new events are present in the effective registration map",
             {"raw/2026-07-09-ryan-new-decision.md",
              "raw/2026-07-09-session-9-new-note.md",
              "raw/2026-07-09-mystery-event.md"} <= set(loaded))
        case("(a) the pre-existing 5 events are untouched (map has exactly 8 total)",
             len(loaded) == 8)

        case("(d) a ryan-* new event registers origin human",
             loaded["raw/2026-07-09-ryan-new-decision.md"]["origin"] == "human")
        case("(d) a session-* new event registers origin corpus",
             loaded["raw/2026-07-09-session-9-new-note.md"]["origin"] == "corpus")
        case("(c) an unparseable-frontmatter new event registers origin unknown "
             "(never human/corpus)",
             loaded["raw/2026-07-09-mystery-event.md"]["origin"] == "unknown")

        # ---- (b): re-running immediately registers 0 ("no new events")
        buf_b = io.StringIO()
        with contextlib.redirect_stdout(buf_b):
            rc2 = run_register_intake(base, dry_run=False)
        case("(b) re-running immediately after registers 0 (exits 0)", rc2 == 0)
        case("(b) re-run prints the 'no new events to register' message",
             "no new events to register" in buf_b.getvalue())
        n2 = regs.check_registration_chain(base)
        case("(b) chain count is unchanged after the no-op re-run (still 8)", n2 == 8)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    # ---- (c) the delta census names the unknown-origin event by path (data-quality
    # surface), on a fresh fixture so the capture is isolated.
    base_c = tempfile.mkdtemp(prefix="register-intake-census-")
    try:
        backfill._make_fixture_repo(base_c)
        backfill.run_backfill(base_c, dry_run=False)
        _write(base_c, "raw/2026-07-09-mystery-event-2.md",
               "no frontmatter at all -- starts with plain prose.\n")
        delta_c, _records_c, _r_c, _w_c = compute_delta(base_c)
        unk_c = unknown_events(delta_c)
        case("(c) the delta census names the unknown-origin event by path",
             unk_c == ["raw/2026-07-09-mystery-event-2.md"])

        buf_c = io.StringIO()
        with contextlib.redirect_stdout(buf_c):
            run_register_intake(base_c, dry_run=False)
        case("(c) the live run's stdout also names the unknown-origin event",
             "raw/2026-07-09-mystery-event-2.md" in buf_c.getvalue())
    finally:
        shutil.rmtree(base_c, ignore_errors=True)

    # ---- (e) --dry-run writes NOTHING (chain count unchanged) but reports the delta
    base_d = tempfile.mkdtemp(prefix="register-intake-dryrun-")
    try:
        backfill._make_fixture_repo(base_d)
        backfill.run_backfill(base_d, dry_run=False)
        n_before = regs.check_registration_chain(base_d)
        _write(base_d, "raw/2026-07-09-ryan-dry-run-check.md",
               "---\nsource: ryan\ndate: 2026-07-09\n---\nbody\n")

        buf_dry = io.StringIO()
        with contextlib.redirect_stdout(buf_dry):
            rc_dry = run_register_intake(base_d, dry_run=True)
        out_dry = buf_dry.getvalue()
        n_after = regs.check_registration_chain(base_d)
        case("(e) --dry-run exits 0", rc_dry == 0)
        case("(e) --dry-run writes NOTHING (chain count unchanged)", n_after == n_before)
        case("(e) --dry-run reports the delta (1 new event) in its output",
             "1 new event" in out_dry)
        case("(e) --dry-run output says nothing was written",
             "nothing written" in out_dry.lower())
    finally:
        shutil.rmtree(base_d, ignore_errors=True)

    # ---- fail-closed atomicity (pre-flight): a malformed record anywhere in the
    # delta refuses the WHOLE batch before any write -- true all-or-nothing (D4/D5).
    base_atomic = tempfile.mkdtemp(prefix="register-intake-atomic-")
    try:
        backfill._make_fixture_repo(base_atomic)
        backfill.run_backfill(base_atomic, dry_run=False)
        n_before_a = regs.check_registration_chain(base_atomic)
        _write(base_atomic, "raw/2026-07-09-ryan-good-one.md",
               "---\nsource: ryan\ndate: 2026-07-09\n---\nbody\n")

        orig_build_records = backfill.build_records

        def _bad_build_records(root, origin_config_path=None):
            recs, r, w = orig_build_records(root,
                                            origin_config_path=origin_config_path)
            # Corrupt the record for the just-written NEW delta event specifically
            # (found by event key, not by list position) -- positional recs[-1]
            # is NOT stable: it depends on the alphabetical sort of the pre-existing
            # fixture filenames, which is fixture content, not test contract (2026-07-25,
            # exposed when the memory-engine fixture filenames were shortened for the
            # Windows MAX_PATH fix and no longer happened to sort before this delta event).
            idx = next(i for i, rec in enumerate(recs)
                       if rec.get("event") == "raw/2026-07-09-ryan-good-one.md")
            bad = dict(recs[idx])
            bad["origin"] = "not-a-real-origin"  # outside origin.ORIGIN_ORDER
            return recs[:idx] + [bad] + recs[idx + 1:], r, w

        backfill.build_records = _bad_build_records
        try:
            try:
                run_register_intake(base_atomic, dry_run=False)
                case("(atomicity, pre-flight) a malformed delta record refuses the "
                     "WHOLE batch", False)
            except IntakeViolation as e:
                case("(atomicity, pre-flight) a malformed delta record refuses the "
                     "WHOLE batch", "all-or-nothing" in str(e))
        finally:
            backfill.build_records = orig_build_records
        n_after_a = regs.check_registration_chain(base_atomic)
        case("(atomicity, pre-flight) the refusal wrote NOTHING (chain count "
             "unchanged)", n_after_a == n_before_a)
    finally:
        shutil.rmtree(base_atomic, ignore_errors=True)

    # ---- fail-closed atomicity (mid-loop): a write failure after N successful
    # appends STOPS immediately and reports EXACTLY what succeeded -- never a
    # silent half-mint. True rollback of an already-committed append-only record
    # is not attempted (and is not what D4/D5 asks for): "fail-closed" here means
    # loud + fully named, never hidden.
    base_mid = tempfile.mkdtemp(prefix="register-intake-midfail-")
    try:
        backfill._make_fixture_repo(base_mid)
        backfill.run_backfill(base_mid, dry_run=False)
        n_before_m = regs.check_registration_chain(base_mid)
        _write(base_mid, "raw/2026-07-09-ryan-first.md",
               "---\nsource: ryan\ndate: 2026-07-09\n---\nbody\n")
        _write(base_mid, "raw/2026-07-09-ryan-second.md",
               "---\nsource: ryan\ndate: 2026-07-09\n---\nbody\n")

        orig_append = regs.append_registration
        calls = {"n": 0}

        def _flaky_append(root, rec):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated write failure on the 2nd append")
            return orig_append(root, rec)

        regs.append_registration = _flaky_append
        try:
            try:
                run_register_intake(base_mid, dry_run=False)
                case("(atomicity, mid-loop) a write failure after 1 success stops "
                     "immediately and reports (never silently continues)", False)
            except IntakeViolation as e:
                case("(atomicity, mid-loop) a write failure after 1 success stops "
                     "immediately and reports (never silently continues)",
                     "1/2" in str(e))
        finally:
            regs.append_registration = orig_append
        n_after_m = regs.check_registration_chain(base_mid)
        case("(atomicity, mid-loop) exactly the 1 successful append landed "
             "(reported, not hidden -- true rollback is impossible append-only)",
             n_after_m == n_before_m + 1)
    finally:
        shutil.rmtree(base_mid, ignore_errors=True)

    # ---- -h/--help falls through to usage, never the live run (argparse default)
    buf_h = io.StringIO()
    with contextlib.redirect_stdout(buf_h):
        try:
            main(["-h"])
            rc_h = 0
        except SystemExit as e:
            rc_h = e.code
    out_h = buf_h.getvalue()
    case("-h exits 0 and prints usage, never runs the live delta",
         rc_h == 0 and "usage" in out_h.lower())

    if failed:
        print("register-intake: FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("register-intake: PASS (%d/%d)" % (total, total))
    return 0


# ------------------------------------------------------------------ CLI
def main(argv):
    p = argparse.ArgumentParser(
        prog="register-intake.py",
        description="B-1 delta registration (trusted-batch): register raw events "
                     "not yet in the P5 registration chain, without re-minting.")
    p.add_argument("--root", default=".", help="repo root (default: .)")
    p.add_argument("--dry-run", action="store_true",
                    help="compute + print the delta census, write NOTHING")
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
        return run_register_intake(root, dry_run=args.dry_run)
    except IntakeViolation as e:
        print("REFUSED: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
