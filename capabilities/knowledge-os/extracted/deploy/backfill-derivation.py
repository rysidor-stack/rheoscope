#!/usr/bin/env python3
"""backfill-derivation.py -- one-time mechanical derivation-region backfill for legacy
wiki views (backlog v3.0-87; MIG-1 P1 seeding step, test-plan tp:512-520 under the
mig1-composition amendment B1-B7).

THE GAP THIS CLOSES: absorption verifies journal and confirm, but compile-v2's
_stamp_verified_block writes `verified:` strictly inside DERIV_START..DERIV_END --
a view with no derivation region can NEVER reach confirmed/stamped state. Legacy
(hand-era) views predate the engine and carry no region, so the verify-convergence
loop silently caps out for them. This script gives every region-less wiki view a
MINIMAL, CONSERVATIVE, engine-managed derivation region -- script-authored, never
hand-typed, validated through the shipped sensor before a byte lands.

It was designed into the MIG-1 drill from the start (drill-migration-p1.py step 2
shells out to this exact filename) but never built -- the same phantom class as the
check-loop-state port note. Built 2026-08-01 (backlog v3.0-87).

CONSERVATIVE DEFAULTS (the tier-T1-conservative doctrine the sibling scripts already
cite from this file's design):
  tier: T1                        -- conservative; the strictest verify obligation.
                                     A later operator/engine pass may lower it.
  consumed_status: legacy-assumed -- B3 conservative inherit: the view carries the
                                     migration content-audit obligation (F13), reported
                                     by check-derivation, never silently cleared.
  origin_max: computed            -- origin.census() over the view's `sources:` events
                                     (the shipped B2 machinery, never re-implemented).
                                     A source that is missing or unparseable assigns
                                     "unknown" (most restrictive) -- conservative by
                                     construction, surfaced per view in the report.
  verified: null                  -- null until a VERIFY pass stamps it (spec s5).
  entities/subscribes: empty      -- routing entities are engine/operator knowledge;
                                     minting none is honest, minting guesses is not.

VALIDATION (single source of truth): after minting, each file's full text is run
through check-frontmatter.py loaded as a library (the check-eco2 pattern). ANY new
finding vs the file's pre-mint findings -> that file is REVERTED and reported as a
violation. The backfill can only ever leave a file born-clean or untouched.

SAFETY: writes only inside the given --root's wiki/ tree; files that already carry a
region are untouched (idempotent); the region is engine-managed and strippable in one
pass (B5 rollback), so the whole backfill reverses mechanically. Run it on a WORKTREE
or branch, never directly on a live tree (drill-migration-p1.py enforces this for the
rehearsal path).

Usage:
  backfill-derivation.py --root DIR [--check] [--json] [--origin-config PATH]
  backfill-derivation.py --self-test
Exit codes: 0 = every region-less view minted clean (or none needed / --check listing)
  | 1 = >=1 violation (validation failure -> reverted, or unparseable view frontmatter)
  | 2 = INCONCLUSIVE (sensor unloadable).
"""

import argparse
import importlib.util
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import origin  # noqa: E402

DERIV_START = "# --- derivation"
DERIV_END = "# --- /derivation"

# wiki files that are regenerated projections, not knowledge views -- never backfilled
# (INDEX/HEALTH/REVIEW are rebuilt wholesale by /compile; a region there would be
# destroyed on the next regeneration and is not part of the 87/97 gap).
PROJECTION_BASENAMES = {"INDEX.md", "HEALTH.md", "REVIEW.md"}


def _load_sensor():
    path = os.path.join(_HERE, "check-frontmatter.py")
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location("check_frontmatter_bf", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _findings(sensor, text, relpath):
    return sorted("%s|%s" % (getattr(f, "level", "?"), getattr(f, "message", f))
                  for f in sensor.check_text(text, relpath))


_FM_SOURCE_RE = re.compile(r"^\s*-\s*(raw/\S+)\s*$")
_FM_SCALAR_RE = re.compile(r"^(title|summary):\s*(.+?)\s*$")


def parse_view(text):
    """Minimal, tolerant frontmatter read: (fm_end_line_index, title, summary, sources)
    or None when the file has no leading frontmatter block (unmintable -- a wiki view
    without frontmatter is already a check-frontmatter finding; we never paper over it)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    title, summary, sources = "", "", []
    in_sources = False
    for i in range(1, len(lines)):
        s = lines[i]
        if s.strip() == "---":
            return i, title, summary, sources
        m = _FM_SCALAR_RE.match(s)
        if m:
            in_sources = False
            if m.group(1) == "title":
                title = m.group(2).strip().strip('"').strip("'")
            else:
                summary = m.group(2).strip().strip('"').strip("'")
            continue
        if re.match(r"^sources:\s*$", s):
            in_sources = True
            continue
        if in_sources:
            m = _FM_SOURCE_RE.match(s)
            if m:
                sources.append(m.group(1))
            elif s.strip() and not s.startswith(" "):
                in_sources = False
    return None


def view_kind(relpath):
    base = os.path.basename(relpath)
    if base == "INDEX.md":
        return "index"
    if base in ("HEALTH.md", "REVIEW.md"):
        return "dashboard"
    return "topic"


def compute_origin_max(root, sources, origin_config_path=None):
    """origin.census() over the view's sources -- the shipped B2 machinery. A source
    absent from disk or without a leading frontmatter fence is parseable=False ->
    'unknown' (most restrictive). Empty sources -> 'human' (origin.origin_max identity:
    no consumed events, no taint)."""
    events = []
    for sid in sources:
        p = os.path.join(root, sid.replace("/", os.sep))
        parseable = False
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                parseable = fh.readline().strip() == "---"
        except OSError:
            parseable = False
        events.append({"id": sid, "parseable": parseable})
    if not events:
        return "human", {"total": 0, "judgment": 0}
    cen = origin.census(events, attestations=origin.load_attestations(),
                        origin_config_path=origin_config_path)
    per_event = [e["origin"] for e in cen["attested_list"] + cen["unattested"]]
    return origin.origin_max(per_event), {"total": cen["total"], "judgment": cen["judgment"]}


def mint_view_id(self_relpath):
    """The deterministic logical-identity mint (v3.0.52, one home -- render_region and
    deploy/debt.py's legacy-view fallback both call this, so a legacy region without the
    key resolves to the SAME id its next mint would write)."""
    import hashlib
    rel = self_relpath.replace("\\", "/")
    return "v-" + hashlib.sha256(("view_id:" + rel).encode("utf-8")).hexdigest()[:16]


def render_region(schema_version, kind, summary, origin_max_value, self_relpath,
                  minted_by):
    """The minted region: every DERIVATION_KEYS-required key, conservative values,
    summary JSON-quoted (valid YAML scalar, hostile characters inert).
    minted_by (v3.0-71): the mint's provenance, `engine` or `backfill`,
    recorded in the region at birth -- the single fact that decides whether a
    later confirmed verify may advance consumed_status (engine-born only;
    backfill keeps the F13 audit obligation). Both minters call THIS function,
    so the two can never drift.
    view_id (v3.0.52, ADR #11 Release 3, backlog v3.0-129): the view's stable LOGICAL
    identity, minted deterministically from the BIRTH path (`v-` + sha256 prefix).
    Rename/move carry it in the region, so cap debt follows lineage, not path
    (deploy/debt.py). A later view born at the SAME path shares the id -- path-reuse
    inheritance is the designed behavior (brief section 2.2 [R2-C2]); the collision with
    a still-live renamed sibling over-attributes debt conservatively (the brake refuses
    growth, never discharges), recorded as the documented edge."""
    return "\n".join([
        "# --- derivation (engine-managed; strip region) ---",
        "schema_version: %s" % schema_version,
        "view: %s" % kind,
        "view_id: %s" % mint_view_id(self_relpath),
        "summary: %s" % json.dumps(summary or "(legacy view; summary pending)"),
        "entities: []",
        "status: active",
        "tier: T1",
        "consumed_status: legacy-assumed",
        "minted_by: %s" % minted_by,
        "origin_max: %s" % origin_max_value,
        "subscribes:",
        "  entities: []",
        "  corpus: []",
        "bundle: [%s]" % self_relpath.replace("\\", "/"),
        "verified: null",
        "# --- /derivation ---",
    ])


def backfill(root, check_only=False, origin_config_path=None):
    sensor = _load_sensor()
    if sensor is None:
        return 2, {"error": "check-frontmatter.py unloadable"}
    wiki = os.path.join(root, "wiki")
    report = {"minted": [], "skipped_regioned": 0, "skipped_projection": 0,
              "skipped_cold": 0, "unparseable": [], "violations": [],
              "candidates": []}
    for dirpath, _dirs, files in os.walk(wiki):
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            fp = os.path.join(dirpath, name)
            rel = os.path.relpath(fp, root).replace("\\", "/")
            # v3.0-157 (fleet inbox #9): wiki/cold/** is retire.py's
            # content-addressed store -- the journal sha256 and the filename
            # bind the exact bytes, so minting a region into a cold object
            # would break digest verification for every published retirement.
            # This tool must never touch (or even list) a cold path.
            if rel == "wiki/cold" or rel.startswith("wiki/cold/"):
                report["skipped_cold"] += 1
                continue
            with open(fp, "r", encoding="utf-8", newline="") as fh:
                text = fh.read()
            if DERIV_START in text:
                report["skipped_regioned"] += 1
                continue
            if name in PROJECTION_BASENAMES:
                report["skipped_projection"] += 1
                continue
            parsed = parse_view(text)
            if parsed is None:
                report["unparseable"].append(rel)
                continue
            report["candidates"].append(rel)
            if check_only:
                continue
            fm_end, title, summary, sources = parsed
            omax, ocounts = compute_origin_max(root, sources, origin_config_path)
            region = render_region(sensor.SCHEMA_VERSION, view_kind(rel),
                                   summary or title, omax, rel,
                                   minted_by="backfill")
            lines = text.splitlines()
            new_lines = lines[:fm_end + 1] + ["", region] + lines[fm_end + 1:]
            new_text = "\n".join(new_lines) + ("\n" if text.endswith("\n") else "")
            before = _findings(sensor, text, rel)
            after = _findings(sensor, new_text, rel)
            introduced = sorted(set(after) - set(before))
            if introduced:
                report["violations"].append({"view": rel, "introduced": introduced})
                continue  # never write a dirty mint; original file untouched
            with open(fp, "w", encoding="utf-8", newline="") as fh:
                fh.write(new_text)
            report["minted"].append({"view": rel, "origin_max": omax,
                                     "sources_censused": ocounts["total"],
                                     "judgment_class": ocounts["judgment"]})
    rc = 1 if (report["violations"] or report["unparseable"]) else 0
    return rc, report


# ----------------------------------------------------------------------------------
# Self-test (hermetic fixtures in a temp dir; drives the real sensor + origin module)
# ----------------------------------------------------------------------------------

def self_test():
    import shutil
    import tempfile
    failed = 0
    total = 0

    def case(name, ok):
        nonlocal failed, total
        total += 1
        if not ok:
            failed += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))

    sensor = _load_sensor()
    if sensor is None:
        print("backfill-derivation: INCONCLUSIVE -- check-frontmatter.py unloadable")
        return 2
    td = tempfile.mkdtemp(prefix="bfderiv-")
    try:
        wiki = os.path.join(td, "wiki", "systems")
        os.makedirs(wiki)
        os.makedirs(os.path.join(td, "raw"))
        # a parseable source raw + a missing one
        with open(os.path.join(td, "raw", "2026-01-01-op-note.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("---\nsource: op\ndate: 2026-01-01\n---\nbody\n")
        legacy = ("---\ntitle: Legacy view\ndomain: systems\nscope: build\n"
                  "last_updated: 2026-01-02\nsources:\n  - raw/2026-01-01-op-note.md\n"
                  "confidence: high\n---\n\n# Legacy: a title with colons\n\nBody.\n")
        with open(os.path.join(wiki, "legacy.md"), "w", encoding="utf-8",
                  newline="") as fh:
            fh.write(legacy)
        regioned = legacy.replace(
            "---\n\n# Legacy",
            "---\n\n# --- derivation (engine-managed; strip region) ---\n"
            "schema_version: %s\nview: topic\nsummary: x\nentities: []\nstatus: active\n"
            "tier: T1\nconsumed_status: verified-consumed\norigin_max: human\n"
            "subscribes:\n  entities: []\n  corpus: []\nbundle: [wiki/systems/done.md]\n"
            "verified: null\n# --- /derivation ---\n\n# Legacy" % sensor.SCHEMA_VERSION)
        with open(os.path.join(wiki, "done.md"), "w", encoding="utf-8", newline="") as fh:
            fh.write(regioned)
        with open(os.path.join(wiki, "INDEX.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ntitle: idx\n---\nprojection\n")
        with open(os.path.join(wiki, "broken.md"), "w", encoding="utf-8") as fh:
            fh.write("no frontmatter at all\n")
        missing_src = legacy.replace("raw/2026-01-01-op-note.md",
                                     "raw/2026-01-01-op-gone.md")
        with open(os.path.join(wiki, "missing-src.md"), "w", encoding="utf-8",
                  newline="") as fh:
            fh.write(missing_src)
        # v3.0-157: a cold object (no frontmatter, no region -- retire.py's
        # shape) must be skipped entirely: never a candidate, never
        # "unparseable", never minted into.
        cold_dir = os.path.join(td, "wiki", "cold", "some-view")
        os.makedirs(cold_dir)
        cold_fp = os.path.join(cold_dir, "section-a--" + "c" * 64 + ".md")
        cold_bytes = "Retired span bytes, verbatim.\n"
        with open(cold_fp, "w", encoding="utf-8", newline="") as fh:
            fh.write(cold_bytes)

        rc, rep = backfill(td, check_only=True)
        case("--check lists candidates without writing",
             sorted(x for x in rep["candidates"]) ==
             ["wiki/systems/legacy.md", "wiki/systems/missing-src.md"]
             and DERIV_START not in open(os.path.join(wiki, "legacy.md"),
                                         encoding="utf-8").read())
        case("--check still reports the unparseable view (exit 1)",
             rc == 1 and rep["unparseable"] == ["wiki/systems/broken.md"])
        case("v3.0-157: cold object skipped -- not a candidate, not unparseable",
             rep["skipped_cold"] == 1
             and not any("cold" in p for p in rep["candidates"])
             and not any("cold" in p for p in rep["unparseable"]))

        rc, rep = backfill(td)
        case("live run exits 1 ONLY for the unparseable view; mints the rest",
             rc == 1 and len(rep["minted"]) == 2 and not rep["violations"])
        minted = open(os.path.join(wiki, "legacy.md"), encoding="utf-8").read()
        case("minted region present with engine-managed markers",
             DERIV_START in minted and DERIV_END in minted)
        case("minted region is born-clean per the shipped sensor",
             _findings(sensor, minted, "wiki/systems/legacy.md") == [])
        case("every required derivation key present",
             all(re.search(r"^%s:" % k, minted, re.M) or ("%s:" % k) in minted
                 for k in sensor.DERIVATION_KEYS["required"]))
        case("conservative defaults: T1 + legacy-assumed + verified null",
             "tier: T1" in minted and "consumed_status: legacy-assumed" in minted
             and "verified: null" in minted)
        case("v3.0-71: the backfill mint records minted_by: backfill "
             "(provenance at birth -- this population's F13 audit obligation "
             "stays open; a verify confirm never advances it)",
             "minted_by: backfill" in minted
             and "minted_by: engine" not in minted)
        m_legacy = next(x for x in rep["minted"] if x["view"] == "wiki/systems/legacy.md")
        m_missing = next(x for x in rep["minted"]
                         if x["view"] == "wiki/systems/missing-src.md")
        case("origin_max censused via origin.py (parseable source -> not unknown)",
             m_legacy["origin_max"] in origin.ORIGIN_ORDER
             and m_legacy["origin_max"] != "unknown")
        case("missing source -> conservative 'unknown' origin_max, judgment-counted",
             m_missing["origin_max"] == "unknown" and m_missing["judgment_class"] == 1)
        case("already-regioned view untouched",
             open(os.path.join(wiki, "done.md"), encoding="utf-8").read() == regioned)
        case("projection INDEX.md untouched",
             DERIV_START not in open(os.path.join(wiki, "INDEX.md"),
                                     encoding="utf-8").read())

        case("v3.0-157: live run left the cold object byte-identical",
             open(cold_fp, encoding="utf-8", newline="").read() == cold_bytes)

        rc2, rep2 = backfill(td)
        case("idempotent: second run mints nothing",
             rep2["minted"] == [] and rep2["skipped_regioned"] >= 3)

        # B5 strip test: removing the region restores the original byte-for-byte
        lines = minted.splitlines()
        s = next(i for i, l in enumerate(lines) if l.startswith(DERIV_START))
        e = next(i for i, l in enumerate(lines) if l.startswith(DERIV_END))
        stripped = "\n".join(lines[:s - 1] + lines[e + 1:]) + "\n"
        case("region strips in one pass back to the original (B5 rollback)",
             stripped == legacy)
    finally:
        shutil.rmtree(td, ignore_errors=True)

    status = "PASS" if failed == 0 else "FAIL"
    print("backfill-derivation self-test: %s (%d/%d)" % (status, total - failed, total))
    return 0 if failed == 0 else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--check", action="store_true",
                    help="report-only: list region-less views, write nothing")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--origin-config", default=None,
                    help="override deploy/origin-config.yaml (self-test seam)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    rc, report = backfill(os.path.abspath(args.root), check_only=args.check,
                          origin_config_path=args.origin_config)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        if rc == 2:
            print("RESULT: INCONCLUSIVE -- %s" % report.get("error"))
            return rc
        print("backfill-derivation: %d minted, %d already-regioned, %d projections "
              "skipped, %d candidate(s)%s"
              % (len(report["minted"]), report["skipped_regioned"],
                 report["skipped_projection"], len(report["candidates"]),
                 " (--check, nothing written)" if args.check else ""))
        for v in report["unparseable"]:
            print("  UNPARSEABLE (no frontmatter; not minted): %s" % v)
        for v in report["violations"]:
            print("  VIOLATION (mint reverted): %s -> %s"
                  % (v["view"], "; ".join(v["introduced"])))
        if rc == 0 and not args.check:
            print("RESULT: PASS -- every region-less view minted born-clean")
        elif rc == 1:
            print("RESULT: %d view(s) need operator attention"
                  % (len(report["unparseable"]) + len(report["violations"])))
    return rc


if __name__ == "__main__":
    sys.exit(main())
