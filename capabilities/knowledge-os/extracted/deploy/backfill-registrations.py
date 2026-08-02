#!/usr/bin/env python3
"""backfill-registrations.py -- one-time mint of the P5 registration chain (C1).

Enumerates the FULL enlarged ledger (spec sec.4, adjudication 4, REVISED at pre-check
r1 which rejected a receipts+locks-only mint): all `receipts/*.md` (129 today) + all
`raw/*.md` events (124 today, the 33 `informed_by:` handoff locks included). Mints one
registration record per event through registrations.append_registration (compile-core's
schema-gated, seq-ordered, prev_record_hash-chained append_record machinery -- see
registrations.py's module docstring for the full contract). Counts are MEASURED at
enumeration time, never hardcoded as a pass condition.

Per-event typing (adjudications 1 and 4):
  RECEIPTS         asserts_corpus_state = True (pointer-class, spec sec.10). event_class
                   is the receipt's own `type:` frontmatter where parseable (explicit);
                   the 3 known-hole receipts (deploy/known-holes.yaml) cannot be read, so
                   they register event_class "unknown-hole" / event_class_origin
                   "judgment" -- REGISTERED regardless (registration never requires the
                   event file to parse; that is its entire sec.4 purpose).
  RAW / informed_by locks   detected via a degraded LINE-LEVEL regex fallback over the
                   raw frontmatter text (mirrors staleness._event_frontmatter /
                   check-loop-state's raw_informed_by pattern -- YAML-hostile frontmatter
                   must not crash the mint). event_class = "informed_by" (explicit: the
                   frontmatter key itself is the evidence), asserts_corpus_state = False.
  RAW / other      event_class from a recognized class key in frontmatter (`type:`) where
                   parseable (explicit); else "judgment" (event_class_origin "judgment" --
                   F6: judgment routes to lock-class treatment downstream, in the
                   consumer, not re-encoded here). asserts_corpus_state = False (every
                   raw/ event, lock or not, is content-bearing per adjudication 1b).

origin + origin_evidence: computed by origin.py's assign_origin (imported, never
reimplemented) -- origin_evidence records WHICH rule/basis fired. Unparseable events
still register (origin.py routes unparseable -> unknown, or an operator attestation /
B6 ryan-* rule resolves it -- either way the registration is minted; parse failure of
the event file never blocks its own registration).

IDEMPOTENCE = REFUSAL. A chain that already has records (check_registration_chain(root)
> 0) is treated as already-minted: the tool refuses to append more, loudly, appendonly
-- never an overwrite, never a silent skip. --dry-run prints the would-be census
(counts by population x event_class_origin x asserts_corpus_state x origin) and writes
NOTHING (no directory created, no records appended) -- verified by the caller diffing
`git status` before/after.

Usage:
  backfill-registrations.py --root DIR [--dry-run]
  backfill-registrations.py --self-test
Exit: 0 ok | 1 refusal/violation | 2 inconclusive (PyYAML unavailable).
"""

import argparse
import importlib.util
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(basename, alias):
    spec = importlib.util.spec_from_file_location(alias, os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


regs = _load("registrations.py", "registrations_backfill")
staleness = _load("staleness.py", "staleness_backfill")
origin = _load("origin.py", "origin_backfill")

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

_INFORMED_BY_RE = re.compile(r"^\s*informed_by\s*:", re.MULTILINE)


class BackfillViolation(Exception):
    pass


# ------------------------------------------------------------------ enumeration
def _iter_md_sorted(root, subdir):
    d = os.path.join(root, subdir)
    if not os.path.isdir(d):
        return []
    out = []
    for r, _dirs, files in os.walk(d):
        if os.sep + ".git" in r:
            continue
        for f in files:
            if f.endswith(".md"):
                out.append(os.path.relpath(os.path.join(r, f), root).replace("\\", "/"))
    return sorted(out)


def _receipts_only(root):
    """receipts/*.md EXCLUDING engine-written machine sidecars (receipts/journal/,
    receipts/registrations/, receipts/verify/, ... -- ENGINE_SIDECAR_DIRS).

    Delegates to registrations.list_receipts_population so the bulk mint, the delta
    writer (register-intake.py, via build_records), staleness.py's enlarged ledger,
    and check-loop-state.py's coverage check ALL share ONE receipts-population
    definition (B-2, 2026-07-09). Previously this hardcoded a 2-item exclusion that
    omitted `verify/`, so the mint/delta population diverged from the sensors' -- a
    live-compile verify sidecar would be registered by the writer yet excluded by the
    census. Single source of truth closes that drift."""
    return regs.list_receipts_population(root)


def enumerate_populations(root):
    return _receipts_only(root), _iter_md_sorted(root, "raw")


# ------------------------------------------------------------------ typing
def _read(root, rel):
    with open(os.path.join(root, rel), "r", encoding="utf-8-sig") as fh:
        return fh.read()


def type_receipt(root, rel, known_holes):
    """-> (event_class, event_class_origin, parseable: bool)."""
    text = _read(root, rel)
    status, parsed = staleness.split_frontmatter(text)
    if status == "ok" and isinstance(parsed, dict) and parsed.get("type"):
        return str(parsed["type"]), "explicit", True
    # unparseable (or parseable-but-no-type, defensive): judgment, but STILL registered
    # (known-hole or not -- registration never requires the event file to parse).
    is_known = rel in known_holes
    label = "unknown-hole" if status != "ok" else "untyped"
    return label, "judgment", status == "ok"


def _has_informed_by(text):
    """Degraded line-level detection -- mirrors staleness._event_frontmatter /
    check-loop-state's raw_informed_by fallback: a plain regex over the raw frontmatter
    text, so YAML-hostile frontmatter (whole-block parse failure) never hides the key
    and never crashes the mint."""
    return bool(_INFORMED_BY_RE.search(text))


def type_raw(root, rel):
    """-> (event_class, event_class_origin, parseable: bool)."""
    text = _read(root, rel)
    if _has_informed_by(text):
        return "informed_by", "explicit", True  # explicit regardless of parse status
    status, parsed = staleness.split_frontmatter(text)
    if status == "ok" and isinstance(parsed, dict) and parsed.get("type"):
        return str(parsed["type"]), "explicit", True
    return "judgment", "judgment", status == "ok"


# ------------------------------------------------------------------ origin
def _origin_for(root, rel, parseable, attestations, origin_config_path=None):
    is_receipt = rel.split("/", 1)[0] == "receipts"
    has_receipt = False  # receipts/raw cross-reference not tracked at this granularity;
    # origin.py's has_receipt flag only affects the ryan-* basis STRING, never the value
    o, basis, _attested = origin.assign_origin(
        rel, parseable, has_receipt=has_receipt,
        attestation=attestations.get(rel),
        origin_config_path=origin_config_path)
    return o, basis


# ------------------------------------------------------------------ mint
def build_records(root, origin_config_path=None):
    """Compute the full set of registration payloads (no seq/prev_record_hash --
    engine-owned) for every enlarged-ledger member. Returns a list of dicts in a
    STABLE order (receipts first sorted, then raw sorted) so re-running --dry-run is
    deterministic. `origin_config_path` is a self-test seam threaded to
    origin.assign_origin (which origin-config file names the human_prefixes) --
    production callers never pass it, so a live mint always reads the live
    deploy/origin-config.yaml."""
    known_holes = staleness.load_known_holes(root) | {
        rel.replace("\\", "/") for rel in _read_known_holes_yaml(root)
    }
    attestations = origin.load_attestations()
    receipts, raws = enumerate_populations(root)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    records = []
    for rel in receipts:
        event_class, eco, parseable = type_receipt(root, rel, known_holes)
        o, basis = _origin_for(root, rel, parseable, attestations,
                               origin_config_path=origin_config_path)
        records.append({
            "kind": "registration", "event": rel, "origin": o,
            "origin_evidence": basis, "event_class": event_class,
            "event_class_origin": eco, "asserts_corpus_state": True,
            "registered_at": now,
        })
    for rel in raws:
        event_class, eco, parseable = type_raw(root, rel)
        o, basis = _origin_for(root, rel, parseable, attestations,
                               origin_config_path=origin_config_path)
        records.append({
            "kind": "registration", "event": rel, "origin": o,
            "origin_evidence": basis, "event_class": event_class,
            "event_class_origin": eco, "asserts_corpus_state": False,
            "registered_at": now,
        })
    return records, receipts, raws


def _read_known_holes_yaml(root):
    """The deploy/known-holes.yaml sibling file's list (repo-relative paths)."""
    path = os.path.join(_HERE, "known-holes.yaml")
    if yaml is None or not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = yaml.safe_load(fh.read()) or {}
    return [str(h).strip() for h in (data.get("known_holes") or [])]


def census(records):
    """{population, event_class_origin, asserts_corpus_state, origin} counts."""
    out = {
        "total": len(records),
        "by_population": {"receipts": 0, "raw": 0},
        "by_event_class_origin": {"explicit": 0, "judgment": 0},
        "by_asserts_corpus_state": {"true": 0, "false": 0},
        "by_origin": {},
    }
    for r in records:
        pop = "receipts" if r["event"].split("/", 1)[0] == "receipts" else "raw"
        out["by_population"][pop] += 1
        out["by_event_class_origin"][r["event_class_origin"]] += 1
        out["by_asserts_corpus_state"]["true" if r["asserts_corpus_state"] else "false"] += 1
        out["by_origin"][r["origin"]] = out["by_origin"].get(r["origin"], 0) + 1
    return out


def print_census(c, label="CENSUS"):
    print("%s: %d record(s)" % (label, c["total"]))
    print("  by_population           %s" % c["by_population"])
    print("  by_event_class_origin   %s" % c["by_event_class_origin"])
    print("  by_asserts_corpus_state %s" % c["by_asserts_corpus_state"])
    print("  by_origin               %s" % c["by_origin"])


def run_backfill(root, dry_run=False, origin_config_path=None):
    records, receipts, raws = build_records(root,
                                            origin_config_path=origin_config_path)
    c = census(records)
    if dry_run:
        print_census(c, label="DRY-RUN CENSUS (nothing written)")
        print("would mint: %d receipts + %d raw = %d total"
              % (len(receipts), len(raws), len(records)))
        return 0
    existing = regs.check_registration_chain(root)
    if existing > 0:
        raise BackfillViolation(
            "refusing: registration chain already has %d record(s) at %s -- "
            "idempotence = refusal, never overwrite/skip (append-only)"
            % (existing, regs.registrations_dir(root)))
    for rec in records:
        regs.append_registration(root, rec)
    n = regs.check_registration_chain(root)
    print("minted %d registration record(s); chain check green (%d)" % (len(records), n))
    print_census(c, label="MINT CENSUS")
    return 0


# ------------------------------------------------------------------ self-test
# Fixture SOURCE lives on disk at deploy/test-fixtures/memory-engine/registrations/
# (2 receipts incl. one unparseable hole, 3 raw events incl. one ryan-* and one
# informed_by lock with YAML-hostile frontmatter). The self-test COPIES this fixture
# repo into a disposable tempdir and git-inits it there -- it never reads/writes the
# live tree; the on-disk copy under test-fixtures/ is the reviewable, versioned
# fixture content itself.
_FIXTURE_SRC = os.path.join(_HERE, "test-fixtures", "memory-engine", "registrations")


def _init_git(base):
    import subprocess
    subprocess.run(["git", "-C", base, "init", "-q"], capture_output=True)
    subprocess.run(["git", "-C", base, "config", "user.email", "t@t"], capture_output=True)
    subprocess.run(["git", "-C", base, "config", "user.name", "t"], capture_output=True)


def _make_fixture_repo(base):
    import shutil
    for sub in ("receipts", "raw"):
        shutil.copytree(os.path.join(_FIXTURE_SRC, sub), os.path.join(base, sub))
    _init_git(base)


def _make_origin_config_fixture(base):
    """Write a `human_prefixes: [ryan]` origin-config FIXTURE into the fixture repo
    (self-sufficiency: the on-disk fixture events are named ryan-*, so the suite must
    carry its OWN config saying 'ryan' rather than read the live deploy/origin-config.
    yaml -- an instance whose live config names a different operator, or none at all,
    must not turn this suite red). Returns the fixture path, for build_records/
    run_backfill's origin_config_path seam. Also reused by register-intake.py's
    self-test (same fixture events, same required config)."""
    p = os.path.join(base, "origin-config-fixture.yaml")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("human_prefixes: [ryan]\n")
    return p


def self_test():
    import shutil
    import tempfile
    total = failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    base = tempfile.mkdtemp(prefix="backfill-regs-")
    try:
        _make_fixture_repo(base)
        # Origin-config self-sufficiency: this mint's origin assertions (case 7:
        # ryan-* -> human) must hold regardless of what the LIVE deploy/
        # origin-config.yaml says (e.g. a fresh instance configured for a
        # different operator, or no config at all) -- so the mint reads its OWN
        # [ryan] fixture config via the origin_config_path seam.
        cfg = _make_origin_config_fixture(base)

        # (1) full mint over the fixture repo, chain check green end-to-end
        rc = run_backfill(base, dry_run=False, origin_config_path=cfg)
        case("(1) full mint over fixture repo exits 0", rc == 0)
        n = regs.check_registration_chain(base)
        case("(1) chain check green end-to-end (5 records: 2 receipts + 3 raw)", n == 5)

        loaded = regs.load_registrations(base)
        case("(1) all 5 events registered",
             set(loaded) == {
                 "receipts/compile-ok.md",
                 "receipts/phase3-hole.md",
                 "raw/2026-04-08-fix-a.md",
                 "raw/ryan-fix-b.md",
                 "raw/fix-c-lock.md",
             })

        # (3) re-mint refusal (idempotence = refusal)
        try:
            run_backfill(base, dry_run=False)
            case("(3) re-mint against an already-minted chain refuses loudly", False)
        except BackfillViolation as e:
            case("(3) re-mint against an already-minted chain refuses loudly",
                 "already has" in str(e) and "5" in str(e))
        case("(3) re-mint refusal did not add records (still 5)",
             regs.check_registration_chain(base) == 5)

        # (4) judgment-class F6 marking present (the plain non-lock raw has no
        # recognized class key -> judgment)
        case("(4) plain raw with no recognized class key -> event_class_origin judgment",
             loaded["raw/2026-04-08-fix-a.md"]["event_class_origin"] == "judgment")

        # (5) unparseable receipt registered origin unknown
        hole_rec = loaded["receipts/phase3-hole.md"]
        case("(5) unparseable (hole) receipt is registered (never dropped)",
             "receipts/phase3-hole.md" in loaded)
        case("(5) unparseable receipt's origin is unknown (B2 conservative default)",
             hole_rec["origin"] == "unknown")
        case("(5) unparseable receipt's event_class_origin is judgment",
             hole_rec["event_class_origin"] == "judgment")

        # (6) receipts get asserts_corpus_state true / locks get informed_by
        # explicit + false
        ok_rec = loaded["receipts/compile-ok.md"]
        case("(6) parseable receipt: asserts_corpus_state True, event_class from type:, explicit",
             ok_rec["asserts_corpus_state"] is True
             and ok_rec["event_class"] == "compile"
             and ok_rec["event_class_origin"] == "explicit")
        lock_rec = loaded["raw/fix-c-lock.md"]
        case("(6) informed_by lock (YAML-hostile frontmatter): event_class informed_by, "
             "explicit, asserts_corpus_state False",
             lock_rec["event_class"] == "informed_by"
             and lock_rec["event_class_origin"] == "explicit"
             and lock_rec["asserts_corpus_state"] is False)

        # (7) ryan-* raw mints human via origin.py
        ryan_rec = loaded["raw/ryan-fix-b.md"]
        case("(7) ryan-* raw event mints origin human via origin.py",
             ryan_rec["origin"] == "human")

        # non-lock, non-ryan raw origin: session/corpus source -> corpus
        plain_rec = loaded["raw/2026-04-08-fix-a.md"]
        case("plain session raw event mints origin corpus via origin.py",
             plain_rec["origin"] == "corpus")

        case("all 5 records carry asserts_corpus_state per population (receipts True, raw False)",
             all(loaded[e]["asserts_corpus_state"] is True
                 for e in loaded if e.startswith("receipts/"))
             and all(loaded[e]["asserts_corpus_state"] is False
                     for e in loaded if e.startswith("raw/")))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    # (2) chain-tamper fixture fails LOUD (fresh repo, isolate from the reuse above)
    base2 = tempfile.mkdtemp(prefix="backfill-regs-tamper-")
    try:
        _make_fixture_repo(base2)
        run_backfill(base2, dry_run=False)
        p2 = os.path.join(regs.registrations_dir(base2), "2.json")
        import json
        rec2 = json.load(open(p2, encoding="utf-8"))
        rec2["prev_record_hash"] = "f" * 64
        open(p2, "w", encoding="utf-8").write(json.dumps(rec2))
        try:
            regs.check_registration_chain(base2)
            case("(2) chain-tamper fixture fails LOUD", False)
        except regs._cc.JournalViolation as e:
            case("(2) chain-tamper fixture fails LOUD", "mismatch" in str(e))
    finally:
        shutil.rmtree(base2, ignore_errors=True)

    # (8) load_registrations supersedes-by-later-seq mechanic (direct on registrations.py,
    # already covered there too, but confirmed reachable from this module's import)
    base3 = tempfile.mkdtemp(prefix="backfill-regs-supersede-")
    try:
        _init_git(base3)
        regs.append_registration(base3, {
            "kind": "registration", "event": "raw/x.md", "origin": "corpus",
            "origin_evidence": "t", "event_class": "judgment", "event_class_origin": "judgment",
            "asserts_corpus_state": False, "registered_at": "2026-01-01T00:00:00"})
        regs.append_registration(base3, {
            "kind": "registration", "event": "raw/x.md", "origin": "corpus",
            "origin_evidence": "t (corrected)", "event_class": "informed_by",
            "event_class_origin": "explicit", "asserts_corpus_state": False,
            "registered_at": "2026-01-02T00:00:00"})
        eff = regs.load_registrations(base3)
        case("(8) load_registrations supersedes-by-later-seq (correction visible)",
             eff["raw/x.md"]["event_class"] == "informed_by" and eff["raw/x.md"]["seq"] == 2)
    finally:
        shutil.rmtree(base3, ignore_errors=True)

    # (9) validate_registration refuses unknown keys and bad origin (via registrations.py,
    # confirmed importable/usable from this module)
    try:
        regs.validate_registration({"kind": "registration", "seq": 1, "event": "x",
                                    "origin": "not-a-real-origin", "origin_evidence": "t",
                                    "event_class": "c", "event_class_origin": "explicit",
                                    "asserts_corpus_state": True, "registered_at": "t",
                                    "prev_record_hash": None})
        case("(9) validate_registration refuses a bad origin", False)
    except regs.RegistrationViolation:
        case("(9) validate_registration refuses a bad origin", True)
    try:
        regs.validate_registration({"kind": "registration", "seq": 1, "event": "x",
                                    "origin": "corpus", "origin_evidence": "t",
                                    "event_class": "c", "event_class_origin": "explicit",
                                    "asserts_corpus_state": True, "registered_at": "t",
                                    "prev_record_hash": None, "bogus": 1})
        case("(9) validate_registration refuses an unknown key", False)
    except regs.RegistrationViolation:
        case("(9) validate_registration refuses an unknown key", True)

    if failed:
        print("backfill-registrations self-test: FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("backfill-registrations self-test: PASS (%d/%d)" % (total, total))
    return 0


def main(argv):
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument("--root", default=".")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args(argv)

    if args.self_test:
        return self_test()
    if yaml is None:
        print("RESULT: INCONCLUSIVE -- PyYAML unavailable")
        return 2
    root = os.path.abspath(args.root)
    try:
        return run_backfill(root, dry_run=args.dry_run)
    except BackfillViolation as e:
        print("REFUSED: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
