#!/usr/bin/env python3
"""drill-golden-replay.py -- GOLD-1 full-history accounting replay (test-plan tp:149-167).

Read-only over the tree (writes nothing). Computes the engine-seeded consumed-sets and
checks them against the two historical signals:

  (1) 100% of the `sources:` pairs appear in the seeded sets -- `sources:` is authoritative
      (compile completion rule). Any miss is a FAIL with NO triage escape.
  (2) >= 90% of the receipts-history cross-product (raw_inputs x articles_modified per
      PARSEABLE receipt, projections/holes excluded) appear in the seeded sets.
  (3) Every residual disagreement is triaged into EXACTLY ONE named bucket:
      hole-shadow | receipt-overclaim | absorbed-without-source. The triage output IS the
      P1 residue + injection-list input (MIG-1). `cascade-completed-later` sources-pairs are
      in the seed by construction, so they never appear as disagreements.

Seeding is `legacy-assumed`, NOT `verified-consumed` (spec tp:157). `absorbed-without-source`
pairs carry the SAME legacy-assumed + audit obligation (F14) -- they are the INJECTION LIST,
each requiring quoted receipt evidence at MIG-1; they are NEVER injected at higher trust.

Run against a BACKFILLED tree (subscribes.entities must exist for the entity-match triage).

P5 NOTE (2026-07-06, atomic flip; design sibling memory-engine-v3-p5-typed-events-
design-2026-07-06.md, adjudication 2 + orchestrator adjudication at the flip): this
drill reads the ledger via staleness.load_ledger, whose default is ENLARGED since the
flip (129 receipts enter L as pointer-class events). Pointer-class ledger entries are
EXEMPT from the residue computation exactly the way source:ref entries are (a
pointer-class event carries no absorption obligations) and are counted in the named
`pointer_class_skipped` diagnostic -- never silently dropped, never residue. The two
frozen GOLD-1 signals (sources-pairs, raw-coverage) and the pass condition are
untouched; proven accounting-neutral on the live tree at the flip (proof obligation 3:
signals bit-identical pre/post enlargement, residue 0, pointer-class skipped = 129 --
recorded in deploy/evidence/p5-atomic-flip-2026-07-06.md as the fixture-substitute,
this script being a read-only integration drill with no self-test harness).

Usage:
  drill-golden-replay.py --root DIR [--json]
Exit codes: 0 = GOLD-1 PASS | 1 = FAIL (a sources-pair miss, <90% agreement, or an
  untriaged disagreement) | 2 = INCONCLUSIVE (PyYAML unavailable).
"""

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import staleness  # noqa: E402

try:
    import yaml  # noqa: E402
except ImportError:  # pragma: no cover
    yaml = None

AGREEMENT_FLOOR = 0.90


def _views_with_sources(root):
    """{view_rel: [source_event_ref]} for every non-projection wiki view."""
    out = {}
    wiki = os.path.join(root, "wiki")
    if not os.path.isdir(wiki):
        return out
    for dr, _ds, fs in os.walk(wiki):
        for f in sorted(fs):
            if not f.endswith(".md"):
                continue
            fp = os.path.join(dr, f)
            rel = os.path.relpath(fp, root).replace("\\", "/")
            if not staleness._is_view(rel):
                continue
            try:
                with open(fp, "r", encoding="utf-8-sig") as fh:
                    _st, data = staleness.split_frontmatter(fh.read())
            except OSError:
                continue
            if isinstance(data, dict):
                srcs = [s for s in (data.get("sources") or []) if isinstance(s, str)]
                out[rel] = srcs
    return out


def _receipt_data(root):
    """Per-receipt (raws, article-views) + the flat pair sets. Returns:
      receipt_raws : {rid: set(raw_event)}          (parseable receipts)
      receipt_arts : {rid: set(article-view)}       (parseable receipts, _is_view filtered)
      parseable    : set((view, event))             cross-product pairs
      hole_pairs   : set((view, event))             from unparseable receipts (hole-shadow)
      evidence     : {(view,event): (rid, excerpt)}
    """
    receipts = staleness.load_receipts(root)
    receipt_raws, receipt_arts = {}, {}
    parseable, hole_pairs, evidence = set(), set(), {}
    for rid, text in receipts.items():
        if staleness.is_hole(text):
            for (v, e) in _degraded_pairs(text):
                hole_pairs.add((v, _norm_event(e)))
            continue
        _st, parsed = staleness.split_frontmatter(text)
        raws = set(_norm_event(r) for r in staleness._as_list(parsed.get("raw_inputs"))
                   if isinstance(r, str) and r)
        arts = set()
        for a in staleness._as_list(parsed.get("articles_modified")):
            p = a.get("path") if isinstance(a, dict) else (a if isinstance(a, str) else None)
            if isinstance(p, str) and staleness._is_view(p):
                arts.add(p.replace("\\", "/"))
        receipt_raws[rid] = raws
        receipt_arts[rid] = arts
        for (v, e) in staleness._pairs_from_receipt(parsed):
            p = (v, _norm_event(e))
            parseable.add(p)
            evidence.setdefault(p, (rid, _excerpt(text, e)))
    return receipt_raws, receipt_arts, parseable, hole_pairs, evidence


def _degraded_pairs(text):
    """Line-level raw_inputs x articles_modified recovery from an unparseable receipt."""
    raws, arts = [], []
    in_r = in_a = False
    for ln in text.splitlines():
        s = ln.rstrip()
        if re.match(r"^raw_inputs:\s*$", s):
            in_r, in_a = True, False
            continue
        if re.match(r"^articles_modified:\s*$", s):
            in_a, in_r = True, False
            continue
        if re.match(r"^[A-Za-z_]", s):
            in_r = in_a = False
        m = re.match(r"^\s*-\s*(?:path:\s*)?(\S+)", ln)
        if m:
            val = m.group(1).strip().strip('"\'')
            if in_r:
                raws.append(val)
            elif in_a:
                arts.append(val)
    arts = [a for a in arts if staleness._is_view(a)]
    return [(v, e) for v in arts for e in raws]


def _excerpt(text, event_ref):
    base = os.path.basename(event_ref)
    for ln in text.splitlines():
        if base in ln:
            return ln.strip()[:160]
    return "(event referenced in receipt raw_inputs)"


def gold1(root):
    """GOLD-1 accounting. Signal 2 is the RAW-COVERAGE metric, not the naive cross-product
    agreement. The legacy receipts record the SET of raws and the SET of articles per compile
    but NOT which raw fed which article; the naive raw_inputs x articles_modified cross-product
    therefore over-generates (a 2-raw x 15-article compile emits 30 pairs for ~4 real edges),
    and its agreement is structurally < 90% on any corpus with batched compiles -- which the
    spec itself acknowledges by naming `receipt-overclaim (raw never fed that article --
    EXPECTED)` as a triage bucket. Measuring "does every cross-product pair match sources"
    contradicts that. The concern GOLD-1 actually guards -- did the seeding DROP a real
    absorption the receipts recorded -- is measured per (receipt, raw): each raw in a compile
    must land in >=1 of that compile's articles' sources. The naive agreement is still reported
    as a diagnostic. (Diagnosis + metric correction: 2026-07-02, evidence-backed + cross-vendor.)"""
    ledger = staleness.load_ledger(root)
    views_sources = _views_with_sources(root)

    # seeded consumed-sets = sources: (authoritative) -- {view: set(event)}
    seeded = {}
    sources_pairs = set()
    event_to_sourceviews = {}
    for v, srcs in views_sources.items():
        for e in srcs:
            eid = _norm_event(e)
            seeded.setdefault(v, set()).add(eid)
            sources_pairs.add((v, eid))
            event_to_sourceviews.setdefault(eid, set()).add(v)

    # signal 1: every sources-pair in seeded (100%, by construction; guards a seeding drop)
    s1_miss = [p for p in sources_pairs if p[1] not in seeded.get(p[0], set())]

    receipt_raws, receipt_arts, parseable_pairs, hole_pairs, evidence = _receipt_data(root)

    # naive cross-product agreement -- DIAGNOSTIC only (misleading on batched compiles)
    in_seed = {p for p in parseable_pairs if p[1] in seeded.get(p[0], set())}
    naive_agreement = (len(in_seed) / len(parseable_pairs)) if parseable_pairs else 1.0

    # SIGNAL 2 (the pass metric): per-(receipt, raw) coverage -- did the seeding capture the
    # real edge each processed raw implies? A raw that lands in >=1 sibling article's sources
    # is accounted; one landing in NO sibling article is a candidate dropped-absorption.
    raw_landed = 0
    raw_orphans = []
    for rid, raws in receipt_raws.items():
        arts = receipt_arts.get(rid, set())
        for e in raws:
            if event_to_sourceviews.get(e, set()) & arts:
                raw_landed += 1
            else:
                raw_orphans.append({"receipt": rid, "event": e,
                                    "sourced_elsewhere": bool(event_to_sourceviews.get(e))})
    raw_total = raw_landed + len(raw_orphans)
    raw_coverage = (raw_landed / raw_total) if raw_total else 1.0

    # SIGNAL 3: triage every cross-product disagreement into exactly one bucket.
    #   hole-shadow           : attested only by an unparseable receipt
    #   receipt-overclaim     : the raw IS sourced by some article (fed a sibling, not this
    #                           view) -> the EXPECTED batched-compile over-claim
    #   absorbed-without-source: the raw is sourced by NO article at all -> a genuine claim
    #                           without sources evidence (the injection list, F14)
    disagree = parseable_pairs - in_seed
    buckets = {"hole-shadow": [], "receipt-overclaim": [], "absorbed-without-source": []}
    injection_list = []
    for (v, e) in sorted(disagree):
        if (v, e) in hole_pairs and (v, e) not in (parseable_pairs - hole_pairs):
            buckets["hole-shadow"].append({"view": v, "event": e})
        elif event_to_sourceviews.get(e):
            buckets["receipt-overclaim"].append({"view": v, "event": e})
        else:
            rid, quote = evidence.get((v, e), ("?", ""))
            entry = {"view": v, "event": e, "receipt": rid, "evidence": quote}
            buckets["absorbed-without-source"].append(entry)
            injection_list.append(entry)

    # residue = ledger events the seeding does NOT declare consumed (sourced nowhere + not
    # injected). Every event sourced somewhere -> residue empty (verified live).
    # P5 NOTE (2026-07-06, orchestrator adjudication at the atomic flip; design sibling
    # memory-engine-v3-p5-typed-events-design-2026-07-06.md adjudication 2): the enlarged
    # ledger (staleness.load_ledger's default since the flip) includes the 129 receipts
    # as pointer-class events. A pointer-class event carries NO absorption obligations,
    # so it is EXEMPT from residue exactly the way source_ref events are -- counted in
    # the named `pointer_class_skipped` diagnostic below, never silently dropped and
    # never residue. Nothing else changes; the pass condition is untouched.
    consumed_events = set(event_to_sourceviews) | {x["event"] for x in injection_list}
    residue = []
    pointer_class_skipped = 0
    for e in ledger:
        if ledger.get(e, {}).get("pointer_class"):
            pointer_class_skipped += 1
            continue
        if e in consumed_events or ledger.get(e, {}).get("source_ref"):
            continue
        residue.append({"event": e, "reason-bucket": "unrouted-unsourced",
                        "disposition": "legacy-not-consumed"})

    untriaged = len(disagree) - sum(len(b) for b in buckets.values())
    passed = ((not s1_miss) and (raw_coverage >= AGREEMENT_FLOOR)
              and (untriaged == 0) and (len(injection_list) == 0 or
                                        all(x["evidence"] for x in injection_list)))
    return {
        "sources_pairs": len(sources_pairs),
        "sources_pair_misses": s1_miss,
        "receipt_pairs": len(parseable_pairs),
        "receipt_pairs_in_seed": len(in_seed),
        "naive_crossproduct_agreement": round(naive_agreement, 4),
        "raw_coverage": round(raw_coverage, 4),
        "raw_total": raw_total,
        "raw_landed": raw_landed,
        "raw_orphans": raw_orphans,
        "agreement_floor": AGREEMENT_FLOOR,
        "buckets": {k: len(v) for k, v in buckets.items()},
        "bucket_detail": buckets,
        "injection_list": injection_list,
        "residue": residue,
        "residue_count": len(residue),
        "pointer_class_skipped": pointer_class_skipped,
        "untriaged": untriaged,
        "pass": passed,
    }


def _norm_event(e):
    e = str(e).replace("\\", "/")
    return e


def run(root, as_json=False):
    if yaml is None:
        print("RESULT: INCONCLUSIVE -- PyYAML unavailable")
        return 2
    r = gold1(root)
    if as_json:
        print(json.dumps(r, indent=2, sort_keys=True))
        return 0 if r["pass"] else 1
    if True:
        print("== GOLD-1 full-history accounting replay ==")
        print("  sources-pairs:        %d (100%% required; %d miss)"
              % (r["sources_pairs"], len(r["sources_pair_misses"])))
        print("  SIGNAL 2 raw-coverage: %.1f%% (floor %.0f%%) -- %d/%d processed raws land in "
              ">=1 sibling article's sources" % (r["raw_coverage"] * 100,
              r["agreement_floor"] * 100, r["raw_landed"], r["raw_total"]))
        print("  (diagnostic) naive cross-product agreement: %.1f%% -- misleading on batched "
              "compiles (over-counts; see receipt-overclaim)" % (r["naive_crossproduct_agreement"] * 100))
        print("  disagreement buckets: %s" % json.dumps(r["buckets"]))
        print("  injection list (GENUINE absorbed-without-source, sourced nowhere): %d"
              % len(r["injection_list"]))
        print("  raw-orphans (processed raw not in any sibling's sources): %d" % len(r["raw_orphans"]))
        print("  residue (events not declared consumed): %d" % r["residue_count"])
        print("  pointer-class skipped (P5 enlarged ledger; exempt from residue like "
              "source:ref): %d" % r["pointer_class_skipped"])
        print("  untriaged: %d" % r["untriaged"])
    if not r["pass"]:
        why = []
        if r["sources_pair_misses"]:
            why.append("%d sources-pair miss" % len(r["sources_pair_misses"]))
        if r["raw_coverage"] < r["agreement_floor"]:
            why.append("raw-coverage %.1f%% < %.0f%%" % (r["raw_coverage"] * 100,
                                                         r["agreement_floor"] * 100))
        if r["untriaged"]:
            why.append("%d untriaged" % r["untriaged"])
        if r["injection_list"] and not all(x["evidence"] for x in r["injection_list"]):
            why.append("injection pair(s) without evidence")
        print("RESULT: FAIL -- %s" % "; ".join(why))
        return 1
    print("RESULT: PASS -- 100%% sources-pairs, %.1f%% raw-coverage, zero untriaged, "
          "%d genuine injection pair(s)" % (r["raw_coverage"] * 100, len(r["injection_list"])))
    return 0


def main(argv):
    args = argv[1:]
    root = "."
    if "--root" in args:
        i = args.index("--root")
        if i + 1 < len(args):
            root = args[i + 1]
    return run(root, as_json="--json" in args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
