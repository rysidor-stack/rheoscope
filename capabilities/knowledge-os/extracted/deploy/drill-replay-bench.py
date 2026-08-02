#!/usr/bin/env python3
"""drill-replay-bench.py -- ACC-5 replay-cost benchmark (test-plan tp:127-137).

Property: the full RUN-JOURNAL replay (parse all receipts -> consumed-sets ->
conservation census) completes in < 2 s wall at the live receipt count and < 10 s at a
synthetic 1,000-receipt corpus, and scales ~linearly: t(1000)/t(100) <= 15 (the ratio
rejects accidental O(n^2) without pinning a fragile absolute on varying hardware).

Mechanics: generates synthetic corpora at n = 100 / 300 / 1000 into a temp dir --
receipt template = the healthy 2026-06-11T074500-compile.md frontmatter shape, ~6 KB
each (matching the real 607 KB x10 corpus shape), event/view names drawn from a
catalog-shaped pool; the 3 ACC-2 hole archetypes inserted every 100 receipts (holes
must not break scaling); times the replay via time.perf_counter, 3 repetitions, median.
Deterministic: seeded PRNG, no wall-clock in the corpus content.

Replay under test = staleness.load_receipts -> build_journal -> load_ledger ->
load_matches -> conservation_audit (the same pipeline every session start and every
compile runs).

P5 NOTE (2026-07-06, atomic flip; design sibling memory-engine-v3-p5-typed-events-
design-2026-07-06.md + orchestrator adjudication at the flip): staleness.load_ledger's
default is ENLARGED since the flip, so the live replay path now includes the receipts
population + the registration-chain load/check. ACC-5 is the live replay-cost
tripwire: post-flip the live path IS the enlarged path, so this bench benchmarks what
the system actually runs at EVERY tier -- the synthetic-tree generator therefore mints
a registration record for every synthetic ledger member (events AND receipts, holes
included) through registrations.append_registration (the canonical chained form, never
hand-rolled JSON), and each synthetic tree is git-inited (the seq lock lives at the
git common dir). Pinning enlarged=False here would benchmark a retired code path and
hide a real cost increase from exactly the sensor built to catch it. The budgets
(<2s live, <10s @1000, ratio <=15) are FIXED BOUNDS, not baselines-to-match --
deliberately NOT tuned at the flip. Determinism is about tree SHAPE/content of the
event+receipt files (seeded PRNG, unchanged); registration registered_at timestamps
may vary run-to-run, which is fine (they are engine sidecar records, not corpus
content).

Usage:
  drill-replay-bench.py [--root DIR]   full benchmark (+ live-tree timing when --root)
  drill-replay-bench.py --self-test    generator + replay correctness on a small corpus
Exit codes: 0 = all budgets + ratio met | 1 = a budget/ratio missed (timing table
printed) | 2 = INCONCLUSIVE (PyYAML/staleness unavailable).
"""

import os
import random
import shutil
import statistics
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    import staleness  # noqa: E402
except Exception:  # pragma: no cover
    staleness = None

try:
    import registrations  # noqa: E402  (P5: synthetic trees mint real chains)
except Exception:  # pragma: no cover
    registrations = None

try:
    import yaml  # noqa: E402
except ImportError:  # pragma: no cover
    yaml = None

LIVE_BUDGET_S = 2.0
SYNTH_1000_BUDGET_S = 10.0
RATIO_BOUND = 15.0
REPS = 3

_HOLE_ARCHETYPES = [
    # (name, receipt text) -- the three ACC-2 archetypes
    ("no-frontmatter", "type: compile\nno fence at all\n"),
    ("unclosed-fence", "---\ntype: compile\ntimestamp: 2026-01-01T00:00:00\n"
                       "raw_inputs:\n  - raw/ev-0.md\nno closing fence\n"),
    ("parse-fail", "---\ntype: compile\nnote: \"unterminated quote breaks the parse\n"
                   "raw_inputs: [raw/ev-0.md\n---\nbody\n"),
]


def _pad_operation(rng):
    words = ["reconcile", "sources", "cascade", "regenerated", "updated", "split",
             "cross-links", "corpus", "lock", "supersedes", "routing", "census"]
    return " ".join(rng.choice(words) for _ in range(40))


def _init_git(base):
    """git-init the synthetic tree: compile-core's seq lock (used by the P5
    registration mint below) lives at the git common dir."""
    import subprocess
    subprocess.run(["git", "-C", base, "init", "-q"], capture_output=True)
    subprocess.run(["git", "-C", base, "config", "user.email", "t@t"], capture_output=True)
    subprocess.run(["git", "-C", base, "config", "user.name", "t"], capture_output=True)


def generate_corpus(base, n_receipts, seed=42, n_views=100):
    """Synthetic tree: raw/ events (1 per receipt), wiki views, receipts/ (~6 KB each,
    healthy compile shape), with the 3 hole archetypes cycling every 100 receipts.
    P5 (2026-07-06 adjudication, module docstring): every synthetic ledger member
    (event AND receipt, holes included) gets a real chained registration record via
    registrations.append_registration, so the bench exercises the ACTUAL enlarged
    replay path (registration load + chain check included) at every tier."""
    rng = random.Random(seed)
    raw_d = os.path.join(base, "raw")
    rec_d = os.path.join(base, "receipts")
    wiki_d = os.path.join(base, "wiki", "topic")
    for d in (raw_d, rec_d, wiki_d):
        os.makedirs(d, exist_ok=True)
    _init_git(base)

    views = ["wiki/topic/v-%03d.md" % i for i in range(n_views)]
    for v in views:
        with open(os.path.join(base, v.replace("/", os.sep)), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("---\ntitle: %s\n---\nbody\n" % os.path.basename(v))

    events = []
    for i in range(n_receipts):
        ev = "raw/ev-%04d.md" % i
        events.append(ev)
        with open(os.path.join(base, ev.replace("/", os.sep)), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("---\ndate: 2026-01-01\ntags: [t%d]\n---\nevent body %d\n"
                     % (i % 7, i))

    n_holes = 0
    receipt_ids = []   # (rid, is_hole) -- registration mint below
    for i in range(n_receipts):
        rid = "receipts/2026-01-01T%02d%02d%02d-compile-%04d.md" % (
            i // 3600 % 24, i // 60 % 60, i % 60, i)
        fp = os.path.join(base, rid.replace("/", os.sep))
        if i and i % 100 == 0:
            name, text = _HOLE_ARCHETYPES[(i // 100 - 1) % len(_HOLE_ARCHETYPES)]
            with open(fp, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            n_holes += 1
            receipt_ids.append((rid, True))
            continue
        receipt_ids.append((rid, False))
        raws = rng.sample(events, k=min(rng.randint(1, 4), len(events)))
        arts = rng.sample(views, k=rng.randint(3, 12))
        lines = ["---", "type: compile", "timestamp: 2026-01-01T00:00:00",
                 "duration_minutes: %d" % rng.randint(5, 60),
                 "token_cost: ~%d000" % rng.randint(10, 120), "raw_inputs:"]
        lines += ["  - %s" % r for r in raws]
        lines.append("articles_modified:")
        for a in arts:
            lines.append("  - path: %s" % a)
            lines.append("    operation: \"%s\"" % _pad_operation(rng))
        lines += ["scope_tags: []", "cross_links_changed: []",
                  "confidence_changes: []", "---", ""]
        body = "\n".join(lines)
        # pad to ~6 KB with a notes section (real receipts carry prose)
        while len(body) < 6000:
            body += "note: %s\n" % _pad_operation(rng)
        with open(fp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)

    # ---- P5: mint one real chained registration per synthetic ledger member
    # (adjudication 2026-07-06 -- canonical append_registration, never hand-rolled
    # JSON; holes registered like any other receipt, origin unknown / judgment).
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for rid, is_hole in receipt_ids:
        registrations.append_registration(base, {
            "kind": "registration", "event": rid,
            "origin": "unknown" if is_hole else "corpus",
            "origin_evidence": ("synthetic bench hole (unparseable)" if is_hole
                                else "synthetic bench receipt"),
            "event_class": "unknown-hole" if is_hole else "compile",
            "event_class_origin": "judgment" if is_hole else "explicit",
            "asserts_corpus_state": True, "registered_at": now,
        })
    for ev in events:
        registrations.append_registration(base, {
            "kind": "registration", "event": ev, "origin": "corpus",
            "origin_evidence": "synthetic bench event", "event_class": "session",
            "event_class_origin": "judgment", "asserts_corpus_state": False,
            "registered_at": now,
        })
    return n_holes


def replay(root):
    """The pipeline under test -- mirrors the session-start / compile-time sweep."""
    receipts = staleness.load_receipts(root)
    known = staleness.load_known_holes(root)
    journal, holes_report = staleness.build_journal(receipts, known)
    ledger = staleness.load_ledger(root)
    matches = staleness.load_matches(root, ledger)
    holes = {rid for (rid, _a, _ok) in holes_report}

    def view_exists(v):
        return os.path.isfile(os.path.join(root, v.replace("/", os.sep)))
    result, problems = staleness.conservation_audit(
        ledger, journal, matches, set(), holes, view_exists=view_exists)
    return len(receipts), len(result), len(holes), problems


def _time_replay(root):
    import gc
    ts = []
    for _ in range(REPS):
        gc.collect()
        t0 = time.perf_counter()
        replay(root)
        ts.append(time.perf_counter() - t0)
    return statistics.median(ts)


def bench(live_root=None):
    if staleness is None or yaml is None or registrations is None:
        print("RESULT: INCONCLUSIVE -- staleness/PyYAML/registrations unavailable")
        return 2
    # measure the LIVE tree FIRST: timing it after three synthetic corpora have churned
    # the same interpreter inflates the reading (~2x observed) -- heap/page-cache noise,
    # not replay cost
    live_t = live_n = None
    if live_root and os.path.isdir(os.path.join(live_root, "receipts")):
        live_t = _time_replay(live_root)
        live_n = len(staleness.load_receipts(live_root))

    base = tempfile.mkdtemp(prefix="acc5-bench-")
    rows = []
    try:
        for n in (100, 300, 1000):
            d = os.path.join(base, "n%d" % n)
            generate_corpus(d, n)
            rows.append((n, _time_replay(d)))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    t = dict(rows)
    ratio = t[1000] / t[100] if t[100] > 0 else float("inf")
    failures = []
    if t[1000] > SYNTH_1000_BUDGET_S:
        failures.append("t(1000)=%.2fs > %.0fs budget" % (t[1000], SYNTH_1000_BUDGET_S))
    if ratio > RATIO_BOUND:
        failures.append("t(1000)/t(100)=%.1f > %.0f (super-linear scaling)"
                        % (ratio, RATIO_BOUND))

    if live_t is not None and live_t > LIVE_BUDGET_S:
        failures.append("live replay %.2fs > %.1fs budget at %d receipts"
                        % (live_t, LIVE_BUDGET_S, live_n))

    print("== ACC-5 replay-cost benchmark (median of %d reps) ==" % REPS)
    for n, tt in rows:
        print("  synthetic n=%4d : %7.3f s" % (n, tt))
    print("  ratio t(1000)/t(100) = %.2f (bound %.0f)" % (ratio, RATIO_BOUND))
    if live_t is not None:
        print("  live tree (n=%d)  : %7.3f s (budget %.1f s)"
              % (live_n, live_t, LIVE_BUDGET_S))
    if failures:
        print("RESULT: FAIL -- %s" % "; ".join(failures))
        return 1
    print("RESULT: PASS -- all budgets + the linearity ratio met")
    return 0


def self_test():
    if staleness is None or yaml is None or registrations is None:
        print("RESULT: INCONCLUSIVE -- staleness/PyYAML/registrations unavailable")
        return 2
    total = failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    base = tempfile.mkdtemp(prefix="acc5-selftest-")
    try:
        n_holes = generate_corpus(os.path.join(base, "c"), 201, seed=7)
        c = os.path.join(base, "c")
        case("generator plants 2 holes at n=201 (every 100th)", n_holes == 2)
        n_receipts, n_classified, n_holes_seen, problems = replay(c)
        case("all 201 receipts load", n_receipts == 201)
        case("both hole archetypes surface as journal holes", n_holes_seen == 2)
        # P5 (2026-07-06 adjudication): replay runs ENLARGED end-to-end -- the
        # census now covers events AND receipts (201 + 201 = 402). Premise of the
        # old '== 201' expectation legitimately changed by the default flip.
        case("census classifies every enlarged-ledger member (201 events + "
             "201 receipts)", n_classified == 402)
        case("replay raises no conservation problems on a healthy corpus",
             not problems)
        # P5: the synthetic tree carries a VALID chained registration store
        # covering every ledger member (402 records), and the enlarged ledger
        # marks receipts pointer-class / events not.
        case("synthetic registration chain green (402 records, one per member)",
             registrations.check_registration_chain(c) == 402)
        led = staleness.load_ledger(c)  # post-flip default: enlarged
        case("enlarged ledger includes receipts, pointer-class (healthy + hole)",
             led.get("receipts/2026-01-01T000001-compile-0001.md", {}).get(
                 "pointer_class") is True
             and led.get("receipts/2026-01-01T000140-compile-0100.md", {}).get(
                 "pointer_class") is True)
        case("enlarged ledger marks synthetic raw events NOT pointer-class",
             led.get("raw/ev-0000.md", {}).get("pointer_class") is False)
        # determinism: same seed -> byte-identical receipt
        d2 = os.path.join(base, "c2")
        generate_corpus(d2, 201, seed=7)
        f = "receipts/2026-01-01T000001-compile-0001.md".replace("/", os.sep)
        case("generation is deterministic (seeded PRNG)",
             open(os.path.join(c, f), encoding="utf-8").read()
             == open(os.path.join(d2, f), encoding="utf-8").read())
        # a receipt is ~6 KB (the real corpus shape)
        case("receipt size ~6 KB (corpus-shape honest)",
             5500 <= os.path.getsize(os.path.join(c, f)) <= 8000)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failed:
        print("drill-replay-bench (ACC-5) self-test: FAIL (%d/%d)"
              % (total - failed, total))
        return 1
    print("drill-replay-bench (ACC-5) self-test: PASS (%d/%d)" % (total, total))
    return 0


def main(argv):
    args = argv[1:]
    if "--self-test" in args:
        return self_test()
    root = None
    if "--root" in args:
        i = args.index("--root")
        if i + 1 < len(args):
            root = args[i + 1]
    return bench(live_root=root)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
