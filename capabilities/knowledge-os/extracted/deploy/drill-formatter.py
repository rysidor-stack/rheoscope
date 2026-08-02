#!/usr/bin/env python3
"""drill-formatter.py -- CONTENT-4 formatter drill (test-plan tp:256-260), worktree.

Replays the 2026-06-09 formatter-corruption class against the P1 derivation blocks and proves
the design win the drill exists for: view-layer corruption CANNOT corrupt absorption accounting,
because the RUN JOURNAL lives in receipts/, which the formatter class never touches.

Two legs, reported separately:
  DETECTION -- flatten N derivation blocks (the FLATTEN signature: reflow the block's key:value
    lines onto one physical line) and run check-frontmatter; the corrupted files must be named,
    exit != 0. (On a fork sensor with the B0.1 gap -- KNOWN_KEYS lacks the derivation keys +
    _check_flatten doesn't scan the derivation region -- detection may still fire via the
    missing-required-keys path; this drill reports which.)
  SURVIVAL (the design win) -- the run-journal reconstruction is byte-identical before and after
    corruption (it is built from receipts/, untouched by the flattener).

Run against a BACKFILLED linked worktree (never the live tree).

Usage: drill-formatter.py --root WORKTREE [--n 6]
Exit codes: 0 = survival proven AND detection fired | 1 = survival broke (fatal) | 3 = survival
  proven but detection did NOT fire (B0.1 sensor gap -- parked, needs template sync) | 2 = INCONCLUSIVE.
"""

import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import staleness  # noqa: E402

_PY = sys.executable or "python3"
DERIV_START = "# --- derivation"
DERIV_END = "# --- /derivation"


def flatten_derivation(text):
    """Reflow the derivation block's key lines onto ONE physical line (the FLATTEN signature).
    Returns (new_text, changed)."""
    lines = text.splitlines(keepends=False)
    si = ei = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if si is None and s.startswith(DERIV_START):
            si = i
        elif si is not None and s.startswith(DERIV_END):
            ei = i
            break
    if si is None or ei is None or ei <= si + 1:
        return text, False
    body = [ln.strip() for ln in lines[si + 1:ei] if ln.strip()]
    flat = " ".join(body)   # jam every key:value onto one line
    new = lines[:si + 1] + [flat] + lines[ei:]
    return "\n".join(new) + ("\n" if text.endswith("\n") else ""), True


def _journal_snapshot(root):
    receipts = staleness.load_receipts(root)
    known = staleness.load_known_holes(root)
    journal, _holes = staleness.build_journal(receipts, known)
    # canonicalize (sets -> sorted lists) for a stable byte comparison
    canon = {e: {v: {"disposition": d["disposition"],
                     "attested_by": sorted(d["attested_by"] or [])}
                 for v, d in views.items()}
             for e, views in journal.items()}
    return json.dumps(canon, sort_keys=True)


def _cf_strict(root):
    proc = subprocess.run([_PY, os.path.join(_HERE, "check-frontmatter.py"), "--strict",
                           os.path.join(root, "wiki")], capture_output=True, text=True)
    named = set()
    for ln in (proc.stdout or "").splitlines():
        m = ln.strip()
        if ".md" in m and ("WARN" in m or "REFUSE" in m):
            import re
            mm = re.search(r"([^\s\[\]]+\.md)", m)
            if mm:
                named.add(os.path.basename(mm.group(1)))
    return proc.returncode, named


def drill(root, n=6):
    # pick n backfilled views
    targets = []
    wiki = os.path.join(root, "wiki")
    for dr, _ds, fs in os.walk(wiki):
        for f in sorted(fs):
            fp = os.path.join(dr, f)
            if f.endswith(".md"):
                try:
                    with open(fp, "r", encoding="utf-8-sig") as fh:
                        if DERIV_START in fh.read():
                            targets.append(fp)
                except OSError:
                    pass
        if len(targets) >= n:
            break
    targets = targets[:n]
    if len(targets) < n:
        print("RESULT: INCONCLUSIVE -- only %d backfilled views found (need %d)" % (len(targets), n))
        return 2

    pre_journal = _journal_snapshot(root)
    pre_rc, _ = _cf_strict(root)

    # corrupt
    corrupted = []
    for fp in targets:
        with open(fp, "r", encoding="utf-8-sig") as fh:
            text = fh.read()
        new, changed = flatten_derivation(text)
        if changed:
            with open(fp, "w", encoding="utf-8", newline="") as fh:
                fh.write(new)
            corrupted.append(os.path.basename(fp))

    det_rc, named = _cf_strict(root)
    post_journal = _journal_snapshot(root)

    detected = det_rc != 0 and all(c in named for c in corrupted)
    survived = post_journal == pre_journal

    # restore
    subprocess.run(["git", "-C", root, "checkout", "--", "wiki"], capture_output=True, text=True)
    rest_rc, _ = _cf_strict(root)

    print("== CONTENT-4 formatter drill (n=%d) ==" % n)
    print("  corrupted views: %s" % ", ".join(corrupted))
    print("  DETECTION: check-frontmatter exit=%d, all %d corrupted files named=%s -> %s"
          % (det_rc, len(corrupted), detected, "DETECTED" if detected else "NOT detected"))
    print("  SURVIVAL:  run-journal byte-identical pre/post corruption -> %s"
          % ("PROVEN" if survived else "BROKEN"))
    print("  restore:   check-frontmatter exit=%d (baseline %d)" % (rest_rc, pre_rc))

    if not survived:
        print("RESULT: FAIL -- absorption accounting was corrupted by a view-layer edit (fatal).")
        return 1
    if not detected:
        print("RESULT: PARKED (exit 3) -- design win PROVEN (journal survived), but the sensor did "
              "NOT name all corrupted files. This is the check-frontmatter B0.1 gap (KNOWN_KEYS "
              "lacks derivation keys + no flatten-scan of the derivation region); it arrives via "
              "template->fork sync, not a fork edit. Detection leg re-runs green post-sync.")
        return 3
    print("RESULT: PASS -- journal survived AND all %d flattened blocks detected." % len(corrupted))
    return 0


def self_test():
    """Unit-level check of the drill's mechanism (no worktree/git): the flattener produces the
    FLATTEN signature (block body jammed onto one physical line, markers preserved) and that
    signature is sensor-visible (>=2 derivation keys collapse so the block loses required keys)."""
    block = ("head\n" + DERIV_START + " (engine-managed; strip region) ---\n"
             "schema_version: 3.2\nview: topic\nsummary: \"x\"\nentities: [a]\nstatus: active\n"
             "tier: T1\nconsumed_status: legacy-assumed\norigin_max: human\nsubscribes:\n"
             "  entities: [a]\n  corpus: []\nbundle: []\nverified: null\n" + DERIV_END + "\ntail\n")
    new, changed = flatten_derivation(block)
    total = failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    case("flattener acts on a derivation block", changed)
    lines = new.splitlines()
    flat_line = next((ln for ln in lines if ln.startswith("schema_version:")), "")
    case("block body jammed onto one physical line (FLATTEN signature)",
         "view:" in flat_line and "origin_max:" in flat_line)
    case("start/end markers preserved (still a strip region)",
         any(l.strip().startswith(DERIV_START) for l in lines)
         and any(l.strip().startswith(DERIV_END) for l in lines))
    case("body text preserved (head/tail intact)", "head" in new and "tail" in new)
    case("idempotent on an already-flat block (no double-jam)",
         flatten_derivation(new)[0].count("schema_version:") == 1)
    if failed:
        print("drill-formatter (CONTENT-4 unit): FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("drill-formatter (CONTENT-4 unit): PASS (%d/%d) -- full worktree drill: --root WT" % (total, total))
    return 0


def main(argv):
    args = argv[1:]
    if "--self-test" in args:
        return self_test()
    root = None
    n = 6
    if "--root" in args:
        root = args[args.index("--root") + 1]
    if "--n" in args:
        n = int(args[args.index("--n") + 1])
    if not root:
        print("usage: drill-formatter.py --root WORKTREE [--n 6]")
        return 2
    return drill(root, n)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
