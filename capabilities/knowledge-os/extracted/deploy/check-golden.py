#!/usr/bin/env python3
"""check-golden.py -- GOLD-2 golden-10 routing subset (test-plan tp:169, tp:536).

A FROZEN, hand-verified set of 10 events exercising every branch of the ordered conservation
decision table (spec §6 / ACC-1). Each event's expected class is reasoned independently from
the decision table (NOT copied from classify() output -- that would be circular), then
classify() must reproduce it exactly. A stable regression anchor + pre-merge / session-start
gate. Includes #8 = REF-SKIPPED WITH an entity match (F1: `source: ref` skips regardless of
M(E)) -- the case a naive "match => route" classifier gets wrong.

Usage:
  check-golden.py --self-test   run the golden-10 through classify() and assert
Exit codes: 0 = all 10 route as hand-verified | 1 = any mismatch (a routing regression).
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import staleness  # noqa: E402


# The frozen golden-10. Each entry: id, ledger-meta, matched views M(E), journal dispositions,
# residue membership, and the HAND-VERIFIED expected class + the one-line reason it holds.
def _golden():
    return [
        # 1. plain consume: one matched view absorbed it.
        {"id": "g01-consumed", "meta": {}, "matches": {"wiki/a.md"},
         "journal": {"wiki/a.md": "absorbed"}, "residue": False,
         "expect": "CONSUMED", "why": "M(E)={a}, a absorbed E -> universal-quantifier holds"},
        # 2. multi-view consume: EVERY matched view absorbed it.
        {"id": "g02-consumed-multi", "meta": {}, "matches": {"wiki/a.md", "wiki/b.md"},
         "journal": {"wiki/a.md": "absorbed", "wiki/b.md": "absorbed"}, "residue": False,
         "expect": "CONSUMED", "why": "both matched views absorbed E"},
        # 3. pending: matched but one view has no journal entry (the work queue).
        {"id": "g03-pending", "meta": {}, "matches": {"wiki/a.md", "wiki/b.md"},
         "journal": {"wiki/a.md": "absorbed"}, "residue": False,
         "expect": "PENDING", "why": "b matches E but never absorbed it -> not universal"},
        # 4. pending no-op candidate: an unverified no-op on a matched view.
        {"id": "g04-noop", "meta": {}, "matches": {"wiki/a.md"},
         "journal": {"wiki/a.md": "noop_unverified"}, "residue": False,
         "expect": "PENDING_NOOP_CANDIDATE", "why": "matched view carries an unverified no-op"},
        # 5. unrouted: no entity match, not in residue.
        {"id": "g05-unrouted", "meta": {}, "matches": set(), "journal": {}, "residue": False,
         "expect": "UNROUTED", "why": "M(E)=empty and not a residue member"},
        # 6. residue: no match but a frozen residue member.
        {"id": "g06-residue", "meta": {}, "matches": set(), "journal": {}, "residue": True,
         "expect": "RESIDUE", "why": "M(E)=empty and a committed residue-p1 member"},
        # 7. superseded-unconsumed: a superseder exists AND E was never absorbed.
        {"id": "g07-superseded", "meta": {"superseded_by": "g07b"}, "matches": {"wiki/a.md"},
         "journal": {}, "residue": False,
         "expect": "SUPERSEDED-UNCONSUMED", "why": "superseder exists and E absorbed nowhere"},
        # 8. REF-SKIPPED with an entity match (F1): source:ref wins over M(E), priority 1.
        {"id": "g08-refskip-matched", "meta": {"source_ref": True}, "matches": {"wiki/a.md"},
         "journal": {"wiki/a.md": "absorbed"}, "residue": False,
         "expect": "REF-SKIPPED", "why": "F1: source:ref skips at priority 1 regardless of match/absorb"},
        # 9. superseded BUT consumed: superseder exists yet E was absorbed -> NOT superseded-unconsumed.
        {"id": "g09-superseded-but-consumed", "meta": {"superseded_by": "g09b"},
         "matches": {"wiki/a.md"}, "journal": {"wiki/a.md": "absorbed"}, "residue": False,
         "expect": "CONSUMED", "why": "superseded-unconsumed requires NOT-absorbed; E was absorbed"},
        # 10. hole-shadowed: the only attestation is a hole receipt -> absorption dropped -> re-present.
        {"id": "g10-hole-shadow", "meta": {}, "matches": {"wiki/a.md"},
         "journal": {"wiki/a.md": ("absorbed", {"receipts/hole.md"})}, "residue": False,
         "expect": "PENDING", "why": "pair attested ONLY by a hole receipt reads as absent (ACC-2)"},
    ]


def _build_inputs(golden):
    """Translate the golden-10 into the (ledger, journal, matches, residue, holes) staleness
    classify() consumes."""
    ledger, journal, matches, residue = {}, {}, {}, set()
    holes = {"receipts/hole.md"}
    for g in golden:
        e = g["id"]
        ledger[e] = dict(g["meta"])
        matches[e] = set(g["matches"])
        if g["residue"]:
            residue.add(e)
        for v, disp in g["journal"].items():
            if isinstance(disp, tuple):
                d, att = disp
            else:
                d, att = disp, set()
            dispo = {"absorbed": "absorbed", "noop_unverified": "noop_pending_verification"}.get(d, d)
            journal.setdefault(e, {})[v] = {"disposition": dispo, "attested_by": att}
    return ledger, journal, matches, residue, holes


def self_test():
    golden = _golden()
    ledger, journal, matches, residue, holes = _build_inputs(golden)
    result = staleness.classify(ledger, journal, matches, residue, holes)
    failed = 0
    for g in golden:
        got = result.get(g["id"])
        ok = (got == g["expect"])
        print("  %s %-26s -> %-24s (%s)"
              % ("ok " if ok else "XX ", g["id"], got if ok else "%s != %s" % (got, g["expect"]),
                 g["why"]))
        if not ok:
            failed += 1
    # also assert the partition is total + every class in the table is exercised
    classes_seen = set(result.values())
    for needed in ("REF-SKIPPED", "SUPERSEDED-UNCONSUMED", "CONSUMED",
                   "PENDING_NOOP_CANDIDATE", "PENDING", "UNROUTED", "RESIDUE"):
        if needed not in classes_seen:
            print("  XX  golden-10 does not exercise class %s" % needed)
            failed += 1
    if failed:
        print("check-golden (GOLD-2): FAIL (%d/%d)" % (len(golden) - failed, len(golden)))
        return 1
    print("check-golden (GOLD-2): PASS (%d/%d, all 7 classes exercised)" % (len(golden), len(golden)))
    return 0


def main(argv):
    if "--self-test" in argv[1:] or len(argv) == 1:
        return self_test()
    print("usage: check-golden.py --self-test")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
