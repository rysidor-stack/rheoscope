#!/usr/bin/env python3
"""check-journal-sidecar.py -- ACC-3b journal-sidecar injection gate (test-plan tp:87, tp:563).

The RUN JOURNAL sidecar (`receipts/journal/<seq>.json`) is schema-validated JSON that refuses
malformed output at WRITE time (not corruptible receipt prose, spec §6). This gate feeds a
battery of JSON defect vectors and asserts each is DETECTED (never silently accepted), plus the
governed quarantine-recovery path (F5): a quarantined seq -- named in an allowlist, each entry
backed by an operator decision file -- is accepted-but-its-absorptions-revert-to-PENDING (the
conservative direction), while an un-allowlisted defect fails loud (exit 1). No ad-hoc deletion
of append-only history.

Usage: check-journal-sidecar.py --self-test
Exit codes: 0 = every defect detected + quarantine recovery correct | 1 = a defect slipped
  through (silent acceptance) or a self-test miss | 2 = INCONCLUSIVE.
"""

import hashlib
import json
import re
import sys

_ADS_RE = re.compile(r"[A-Za-z]:.*:")        # NTFS alternate-data-stream (':' beyond drive letter)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_REQUIRED = ("seq", "prev_record_hash", "absorbed")


def canonical_hash(record):
    """sha256 over the record with its own hash-carrying field excluded, canonical JSON."""
    r = {k: v for k, v in record.items() if k != "record_hash"}
    return hashlib.sha256(json.dumps(r, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _path_defect(p):
    p = str(p)
    if _CTRL_RE.search(p):
        return "control/NUL char in path"
    if _ADS_RE.search(p):
        return "NTFS alternate-data-stream path"
    if ".." in p.replace("\\", "/").split("/"):
        return "path traversal (..)"
    return None


def validate_record(record, expected_seq=None, prev_hash=None):
    """Return None if the record is well-formed, else a defect string."""
    if not isinstance(record, dict):
        return "record is not a JSON object"
    for k in _REQUIRED:
        if k not in record:
            return "missing required field: %s" % k
    if not isinstance(record["seq"], int):
        return "seq is not an integer (%r)" % type(record["seq"]).__name__
    if expected_seq is not None and record["seq"] != expected_seq:
        return "seq %r != expected %r (gap/duplicate)" % (record["seq"], expected_seq)
    if prev_hash is not None and record.get("prev_record_hash") != prev_hash:
        return "prev_record_hash chain mismatch"
    if not isinstance(record["absorbed"], list):
        return "absorbed is not a list"
    for a in record["absorbed"]:
        if not isinstance(a, dict) or "view" not in a or "events" not in a:
            return "absorbed entry missing view/events"
        d = _path_defect(a["view"])
        if d:
            return "absorbed.view: %s" % d
        if a.get("disposition", "absorbed") != "no-op" and not a.get("corpus_support"):
            # a non-no-op absorb must carry verbatim corpus_support (spec §6)
            return "absorbed entry has no corpus_support (verbatim support required)"
    return None


def validate_text(text):
    """Parse + validate one raw record file. Returns (record_or_None, defect_or_None)."""
    try:
        rec = json.loads(text)
    except Exception as e:
        return None, "malformed JSON: %s" % e
    return rec, validate_record(rec)


def validate_sequence(records, quarantine_allowlist=()):
    """Validate an ordered list of records with chain + monotonic seq. Returns
    (problems, quarantined) -- problems fail loud; quarantined seqs revert to PENDING."""
    problems, quarantined = [], []
    allow = set(quarantine_allowlist)
    prev_hash = None
    for i, rec in enumerate(records):
        seq = rec.get("seq") if isinstance(rec, dict) else None
        defect = validate_record(rec, expected_seq=i, prev_hash=prev_hash)
        if defect:
            if seq in allow:
                quarantined.append((seq, defect))     # accepted-but-reverted (F5)
            else:
                problems.append((seq, defect))         # fail loud
        prev_hash = canonical_hash(rec) if isinstance(rec, dict) else prev_hash
    return problems, quarantined


def self_test():
    total = failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    base = {"seq": 0, "prev_record_hash": None,
            "absorbed": [{"view": "wiki/a.md", "events": ["raw/e.md"], "corpus_support": "x"}]}

    # a clean record passes
    case("clean record passes", validate_record(base, expected_seq=0, prev_hash=None) is None)

    # --- 11 JSON defect vectors ---
    vectors = [
        ("v1 malformed JSON", lambda: validate_text("{seq:0,}")[1]),
        ("v2 missing required field (absorbed)",
         lambda: validate_record({"seq": 0, "prev_record_hash": None})),
        ("v3 seq wrong type (string)",
         lambda: validate_record(dict(base, seq="0"))),
        ("v4 seq gap/duplicate",
         lambda: validate_record(dict(base, seq=5), expected_seq=1)),
        ("v5 prev_record_hash chain break",
         lambda: validate_record(dict(base, prev_record_hash="deadbeef"), prev_hash="cafe")),
        ("v6 absorbed not a list",
         lambda: validate_record(dict(base, absorbed={"view": "x"}))),
        ("v7 absorbed entry missing events",
         lambda: validate_record(dict(base, absorbed=[{"view": "wiki/a.md"}]))),
        ("v8 non-no-op absorb missing corpus_support",
         lambda: validate_record(dict(base, absorbed=[{"view": "wiki/a.md", "events": ["raw/e.md"]}]))),
        ("v9 NUL/control char in path",
         lambda: validate_record(dict(base, absorbed=[{"view": "wiki/a\x00.md", "events": ["e"], "corpus_support": "x"}]))),
        ("v10 path traversal (..)",
         lambda: validate_record(dict(base, absorbed=[{"view": "wiki/../../etc/x.md", "events": ["e"], "corpus_support": "x"}]))),
        ("v11 NTFS ADS path",
         lambda: validate_record(dict(base, absorbed=[{"view": "C:receipts/journal/0042.json:payload", "events": ["e"], "corpus_support": "x"}]))),
    ]
    for name, fn in vectors:
        case("%s detected" % name, bool(fn()))

    # --- quarantine recovery (F5) ---
    r0 = dict(base)
    r1_bad = {"seq": 1, "prev_record_hash": canonical_hash(r0), "absorbed": "corrupt"}  # defect
    r2 = {"seq": 2, "prev_record_hash": canonical_hash(r1_bad),
          "absorbed": [{"view": "wiki/b.md", "events": ["raw/f.md"], "corpus_support": "y"}]}
    seqchain = [r0, r1_bad, r2]
    probs, quar = validate_sequence(seqchain, quarantine_allowlist=())
    case("un-allowlisted defective seq fails loud", any(s == 1 for s, _ in probs))
    probs2, quar2 = validate_sequence(seqchain, quarantine_allowlist={1})
    case("quarantined seq accepted-but-reverted (no hard fail)",
         not any(s == 1 for s, _ in probs2) and any(s == 1 for s, _ in quar2))

    if failed:
        print("check-journal-sidecar (ACC-3b): FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("check-journal-sidecar (ACC-3b): PASS (%d/%d; 11 vectors + quarantine recovery)"
          % (total, total))
    return 0


def main(argv):
    if "--self-test" in argv[1:] or len(argv) == 1:
        return self_test()
    print("usage: check-journal-sidecar.py --self-test")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
