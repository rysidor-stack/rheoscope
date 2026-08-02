#!/usr/bin/env python3
"""drill-migration-p1.py -- MIG-1 P1 dry-run gate (test-plan tp:512-520) under the
2026-07-01 mig1-composition amendment (B1-B7). THE OPERATOR HOLD POINT.

Rehearses the P1 seeding on a WORKTREE (never the live tree): backfill -> seed from
sources: -> GOLD-1 -> check-frontmatter (born-clean) -> ACC-1 conservation over the
seeded journal -> re-baselined census (B1) -> origin-assignment census (B2) -> injection
list (F14) -> residue snapshot -> abort criteria. Emits the residue snapshot + a
SHA-stamped receipt for operator review. **It NEVER seeds P1-live** -- that is a separate,
operator-gated step this drill exists to make safe.

Amendment coverage:
  B1 census re-baseline : recompute N_events/receipts/holes/sources-pairs/informed_by-locks
                          from the tree at the pinned SHA; residue bound = ceil(0.17*N_events).
  B2 F7 origin census   : per-origin counts, rule-derived vs judgment split, attested list;
                          ABORT if any human/corpus event is unparseable + unattested.
  B3 conservative inherit: legacy-assumed pairs carry the content-audit obligation (reported).
  B4 SHA-binding        : the residue snapshot + receipt stamp the worktree HEAD SHA.
  B5 rollback           : the worktree is disposable; the derivation region is strippable.
  B6 origin intake       : human-mint is non-forgeable (origin.py keys on committed history).
  B7 riders             : standing re-verify riders reported as undisposed -> operator action.

P5 NOTE (2026-07-06, drill-migration-p1 residual, discharged under the atomic-flip sibling
adjudication -- deploy/evidence/p5-atomic-flip-2026-07-06.md, "residue defect" /
"drill-replay-bench" sections): staleness.load_ledger's default is ENLARGED (129 receipts
enter the ledger as pointer-class events, adjudication 2/component C2). This drill touches
the ledger at two sites, both fixed the SAME way sibling drills were (never pin
enlarged=False; pointer-class members explicitly exempted where the drill's contract
concerns content/absorption, and every exemption COUNTED in a named diagnostic, never
silently dropped):
  - _seeded_journal's sources: membership test now narrows to non-pointer-class ledger
    members (a sources: ref naming a receipt is not a legitimate content citation); any
    such ref is additionally surfaced in `sources_ref_pointer_class` (JUDGMENT CALL,
    flagged for orchestrator review -- expected empty on the live tree).
  - ACC-1's per-class counts sub-bucket REF-SKIPPED into ref vs pointer-class (mirrors
    staleness.run_census's own `ref_skipped_subbucket`); the B1 census's N_events is
    ledger CONTENT events only (pointer-class receipts counted separately and named,
    `N_ledger_pointer_class_skipped`, so residue_bound's ceil(0.17*N) is not silently
    inflated by the enlargement); B2's origin-assignment census exempts pointer-class
    receipts (engine sidecar records have no content provenance to attest), named
    `pointer_class_skipped` in `origin_census`.
This drill has no --self-test harness pre-P5; one was added (--self-test) covering: an
enlarged root with full registration coverage runs clean with the named diagnostics
populated; a receipt-bearing root missing registrations fails loud (EnlargementViolation);
a sources: ref naming a pointer-class receipt is skipped and separately surfaced.

Usage:
  drill-migration-p1.py --root WORKTREE --sha SHA [--json]
  drill-migration-p1.py --self-test
Exit codes: 0 = dry-run PASS (all abort criteria clear) | 1 = dry-run ABORT (>=1 criterion
  tripped -- P1-live BLOCKED; the correct outcome to STOP on) | 2 = INCONCLUSIVE.
"""

import json
import math
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import origin          # noqa: E402
import staleness       # noqa: E402
import importlib.util  # noqa: E402

try:
    import yaml        # noqa: E402
except ImportError:    # pragma: no cover
    yaml = None

_PY = sys.executable or "python3"
RESIDUE_FRACTION = 0.17   # mig1 B1: proportional bound = ceil(0.17 * N_events) (reported)
RESIDUE_ABSOLUTE = 12     # D1 (operator sign-off 2026-07-03): the GATE is the stricter
#                           absolute bound; the proportional bound stays as a labelled
#                           diagnostic. Loosening back is a dated amendment.


def _load_module(basename, alias):
    path = os.path.join(_HERE, basename)
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sh(args, cwd=None):
    return subprocess.run([_PY] + args, cwd=cwd or _HERE, capture_output=True, text=True)


def assert_rehearsal_worktree(root):
    """Writer-side interlock (mig1 amendment B4): this drill MUTATES the tree at `root`
    (mints derivation blocks, writes the residue snapshot). It must NEVER run against the
    primary/live worktree -- only a disposable LINKED git worktree. A linked worktree's
    git-dir lives under `<common>/worktrees/<name>`; the primary worktree's git-dir is the
    repo `.git`. Refuse (exit 2) on the primary worktree or a non-git root -- fail closed.
    Returns None on success; raises SystemExit(2) with a clear message otherwise.
    (Surfaced by the 2026-07-02 cross-vendor check: the drill relied on the caller passing a
    disposable worktree by convention rather than enforcing it.)"""
    gd = subprocess.run(["git", "-C", root, "rev-parse", "--git-dir"],
                        capture_output=True, text=True)
    if gd.returncode != 0:
        print("REFUSED -- %s is not inside a git repository (fail-closed)." % root)
        raise SystemExit(2)
    git_dir = (gd.stdout or "").strip().replace("\\", "/")
    is_linked = "/worktrees/" in git_dir or git_dir.endswith("/worktrees")
    # also confirm root is not the repo's primary working tree
    common = subprocess.run(["git", "-C", root, "rev-parse", "--path-format=absolute",
                             "--git-common-dir"], capture_output=True, text=True)
    primary_toplevel = os.path.dirname((common.stdout or "").strip()) if common.returncode == 0 else ""
    root_abs = os.path.abspath(root)
    if not is_linked or (primary_toplevel and os.path.abspath(primary_toplevel) == root_abs):
        print("REFUSED -- %s is the primary/live worktree, not a disposable rehearsal "
              "worktree.\n  The MIG-1 dry-run must NEVER mutate the live tree (amendment B4).\n"
              "  Create one first, e.g.:\n"
              "    git worktree add --detach <TMP>/p1-rehearsal <SHA>\n"
              "    python deploy/drill-migration-p1.py --root <TMP>/p1-rehearsal --sha <SHA>"
              % root)
        raise SystemExit(2)


def _check_frontmatter_findings(root):
    """Return the set of finding lines (default/non-strict) over root/wiki, path-normalized."""
    proc = _sh([os.path.join(_HERE, "check-frontmatter.py"), "--strict",
                os.path.join(root, "wiki")])
    out = set()
    refuse = 0
    for ln in (proc.stdout or "").splitlines():
        ln = ln.strip()
        if "REFUSE" in ln:
            refuse += 1
        if ln.startswith("[") or "WARN" in ln or "REFUSE" in ln:
            # normalize the absolute path prefix out so pre/post are comparable
            out.add(re.sub(r".*?(wiki[\\/].*?\.md)", r"\1", ln).replace("\\", "/"))
    return out, refuse


def _seeded_journal(root):
    """Build the sources:-seeded journal (legacy-assumed, attested_by=None) merged with the
    receipts-bootstrap journal. Only ledger CONTENT events are seeded (a sources: ref outside
    raw/ -- e.g. a snapshots/ path -- is NOT a ledger event and would be an orphan claim; it is
    skipped here and reported by GOLD-1 as a data-quality note). Returns (journal, seeded,
    skipped_nonledger, sources_ref_pointer_class).

    P5 NOTE (2026-07-06, atomic-flip sibling adjudication): staleness.load_ledger's default is
    ENLARGED (129 receipts enter L as pointer-class events, per adjudication 2/component C2).
    This function's membership test is about "does this sources: ref name a real ledger
    CONTENT event", the same question it asked pre-flip -- a pointer-class receipt carries no
    absorption obligations (it is not sourceable content), so it must NOT silently start
    passing this membership test just because load_ledger's key-set grew. The test is narrowed
    to non-pointer-class ledger members (`content_ledger_ids`); a sources: ref naming a
    pointer-class/receipt path is treated the SAME as any other non-ledger ref (skipped,
    reported), but ALSO surfaced separately in `sources_ref_pointer_class` -- named, never
    silently folded into the ordinary nonledger-skip bucket, since "sources: names a receipt"
    is a more specific and more suspicious condition than "sources: names a stray path" and
    warrants its own visibility (JUDGMENT CALL: flagged for orchestrator review -- on the live
    tree this list is expected empty, since no wiki view's sources: currently names a receipt)."""
    receipts = staleness.load_receipts(root)
    known = staleness.load_known_holes(root)
    journal, _holes = staleness.build_journal(receipts, known)
    ledger = staleness.load_ledger(root)
    content_ledger_ids = set(e for e, meta in ledger.items() if not meta.get("pointer_class"))
    pointer_class_ids = set(e for e, meta in ledger.items() if meta.get("pointer_class"))
    seeded_pairs = set()
    skipped = []
    sources_ref_pointer_class = []
    wiki = os.path.join(root, "wiki")
    for dr, _ds, fs in os.walk(wiki):
        for f in sorted(fs):
            if not f.endswith(".md"):
                continue
            rel = os.path.relpath(os.path.join(dr, f), root).replace("\\", "/")
            if not staleness._is_view(rel):
                continue
            try:
                with open(os.path.join(dr, f), "r", encoding="utf-8-sig") as fh:
                    _st, data = staleness.split_frontmatter(fh.read())
            except OSError:
                continue
            if not isinstance(data, dict):
                continue
            for e in (data.get("sources") or []):
                if not isinstance(e, str):
                    continue
                e = e.replace("\\", "/")
                if e not in content_ledger_ids:
                    skipped.append((rel, e))
                    if e in pointer_class_ids:
                        sources_ref_pointer_class.append((rel, e))
                    continue
                journal.setdefault(e, {}).setdefault(
                    rel, {"disposition": "absorbed", "attested_by": None})
                seeded_pairs.add((rel, e))
    return journal, seeded_pairs, skipped, sources_ref_pointer_class


def _origin_census(root, ledger):
    """B2 origin-assignment census over ledger CONTENT events. P5 NOTE (2026-07-06,
    atomic-flip sibling adjudication): `ledger` is the enlarged ledger (129 pointer-class
    receipts alongside raw/ content events). B2 origin assignment is about the provenance
    of ledger CONTENT (what feeds origin_max/the boot-time capability wall) -- a
    pointer-class receipt is an engine sidecar record, not content with a provenance to
    attest, so it is EXEMPT from this census exactly the way drill-golden-replay.py exempts
    pointer-class members from its residue computation and ACC-1 exempts them via
    REF-SKIPPED. Exempted members are counted in the named `pointer_class_skipped` return
    key -- never silently dropped from the caller's view."""
    events = []
    pointer_class_skipped = 0
    for eid, meta in ledger.items():
        if meta.get("pointer_class"):
            pointer_class_skipped += 1
            continue
        fp = os.path.join(root, eid.replace("/", os.sep))
        parseable = True
        try:
            with open(fp, "r", encoding="utf-8-sig") as fh:
                st, _ = staleness.split_frontmatter(fh.read())
                parseable = (st == "ok")
        except OSError:
            pass
        events.append({"id": eid, "parseable": parseable})
    # B2 operator attestations (2026-07-03 sign-off session) resolve judgment-class
    # events -- see deploy/origin-attestations.yaml + the evidence record
    result = origin.census(events, attestations=origin.load_attestations())
    result["pointer_class_skipped"] = pointer_class_skipped
    return result


def _informed_by_count(root):
    """B1: recomputed informed_by lock count via degraded line-level extraction over ALL
    ledger files (mig1 B3.2 raw_informed_by fallback)."""
    n = 0
    rawd = os.path.join(root, "raw")
    if not os.path.isdir(rawd):
        return 0
    for f in os.listdir(rawd):
        if not f.endswith(".md"):
            continue
        try:
            with open(os.path.join(rawd, f), "r", encoding="utf-8-sig") as fh:
                text = fh.read()
        except OSError:
            continue
        if re.search(r"^\s*informed_by\s*:", text, re.MULTILINE):
            n += 1
    return n


def run(root, sha, as_json=False, adopt_amendment=False):
    if yaml is None:
        print("RESULT: INCONCLUSIVE -- PyYAML unavailable")
        return 2
    assert_rehearsal_worktree(root)   # B4 writer-side interlock: never mutate the live tree
    report = {"sha": sha, "root": root, "criteria": {}}

    # --- 1. baseline check-frontmatter findings BEFORE backfill ---------------
    # (the worktree is a clean checkout at the pinned SHA when the drill starts)
    base, _base_refuse = _check_frontmatter_findings(root)

    # --- 2. backfill the worktree (mint derivation blocks) --------------------
    bf = _sh([os.path.join(_HERE, "backfill-derivation.py"), "--root", root])
    report["backfill_rc"] = bf.returncode

    # --- 2b. check-frontmatter born-clean delta (dirty-backfill abort) --------
    post, refuse = _check_frontmatter_findings(root)
    new_findings = sorted(post - base)
    report["check_frontmatter"] = {"new_findings": new_findings, "refuse": refuse,
                                   "pre_existing": len(base)}
    report["criteria"]["dirty-backfill"] = {
        "abort": bool(new_findings) or refuse > 0,
        "detail": "%d new finding(s), %d REFUSE (pre-existing %d ignored)"
                  % (len(new_findings), refuse, len(base))}

    # --- 3. GOLD-1 ------------------------------------------------------------
    g = _sh([os.path.join(_HERE, "drill-golden-replay.py"), "--root", root, "--json"])
    try:
        gold = json.loads(g.stdout)
    except Exception:
        gold = {"pass": False, "agreement": 0, "sources_pair_misses": ["<parse-error>"],
                "injection_list": [], "residue": [], "buckets": {}}
    report["gold1"] = {k: gold.get(k) for k in
                       ("sources_pairs", "sources_pair_misses", "receipt_pairs",
                        "raw_coverage", "naive_crossproduct_agreement", "raw_landed",
                        "raw_total", "buckets", "residue_count", "untriaged", "pass",
                        # P5 (adjudication 2): GOLD-1's own named pointer-class-skipped
                        # diagnostic (129 receipts exempted from ITS residue), passed
                        # through so MIG-1's report surfaces both drills' counts together.
                        "pointer_class_skipped")}
    report["criteria"]["gold1-sources-miss"] = {
        "abort": bool(gold.get("sources_pair_misses")),
        "detail": "%d sources-pair miss (100%% required, no triage escape)"
                  % len(gold.get("sources_pair_misses") or [])}
    naive = gold.get("naive_crossproduct_agreement", 0)
    rawc = gold.get("raw_coverage", 0)
    if adopt_amendment:
        g2_abort = rawc < 0.90
        g2_gov = ("raw-coverage %.1f%% vs 90%% [gold1-metric amendment ADOPTED "
                  "2026-07-03, operator sign-off -- the default since; naive cross-product "
                  "%.1f%% retained as a labelled diagnostic, never the gate]"
                  % (rawc * 100, naive * 100))
    else:
        g2_abort = naive < 0.90
        g2_gov = ("FROZEN cross-product agreement %.1f%% < 90%% [--frozen-gold1-metric "
                  "override: pre-adoption behavior for archaeology only]" % (naive * 100))
    report["gold1_metric_amendment_adopted"] = adopt_amendment
    report["criteria"]["gold1-signal2"] = {
        "abort": g2_abort,
        "detail": "%s | diagnosis: the naive %.1f%% is a batched-compile cross-product artifact "
                  "(0 genuine orphans; all events sourced; raw-coverage %.1f%%) -- "
                  "evidence gold1-agreement-diagnosis-2026-07-02.md, empirical part cross-vendor "
                  "CONFIRMED; metric change ADOPTED by operator 2026-07-03 "
                  "(specs/memory-engine-v3-gold1-metric-amendment.md adoption record)."
                  % (g2_gov, naive * 100, rawc * 100)}

    # --- 4. ACC-1 conservation over the seeded journal ------------------------
    # P5 NOTE (2026-07-06, atomic-flip sibling adjudication): load_ledger's default is
    # ENLARGED -- `ledger` here carries the 129 pointer-class receipts alongside raw/
    # content events (never pinned enlarged=False; see staleness.py's module P5 NOTE and
    # the adjudication precedent at deploy/evidence/p5-atomic-flip-2026-07-06.md).
    ledger = staleness.load_ledger(root)
    journal, seeded_pairs, skipped_nonledger, sources_ref_pointer_class = _seeded_journal(root)
    report["nonledger_sources"] = [{"view": v, "ref": e} for (v, e) in skipped_nonledger]
    # JUDGMENT CALL (flagged for orchestrator review): a sources: ref that names a
    # pointer-class receipt path is surfaced separately from ordinary nonledger skips --
    # see _seeded_journal's docstring. Expected empty on the live tree.
    report["sources_ref_pointer_class"] = [
        {"view": v, "ref": e} for (v, e) in sources_ref_pointer_class]
    matches = staleness.load_matches(root, ledger)
    receipts = staleness.load_receipts(root)
    known = staleness.load_known_holes(root)
    _j, holes_report = staleness.build_journal(receipts, known)
    holes = set(rid for (rid, _a, _ok) in holes_report)

    def view_exists(v):
        return os.path.isfile(os.path.join(root, v))
    result, problems = staleness.conservation_audit(
        ledger, journal, matches, set(), holes, view_exists=view_exists)
    counts = {}
    for e, c in result.items():
        counts[c] = counts.get(c, 0) + 1
    # P5 (adjudication 2): sub-bucket REF-SKIPPED into ref vs pointer-class -- the SAME
    # named-diagnostic exemption drill-golden-replay.py applies to its residue computation,
    # applied here to ACC-1's own class counts so the 129 pointer-class receipts are
    # COUNTED (never silently absorbed into a bare "REF-SKIPPED" total). counts/problems/
    # new_holes are otherwise byte-identical in shape to pre-flip.
    ref_skip_sub = staleness._ref_skip_subbucket(ledger, [e for e, c in result.items()
                                                          if c == "REF-SKIPPED"])
    pointer_class_skipped = len(ref_skip_sub["pointer-class"])
    new_holes = staleness.new_holes(holes_report)
    report["acc1"] = {
        "counts": counts, "problems": problems, "new_holes": new_holes,
        "ref_skipped_subbucket": {"ref": len(ref_skip_sub["ref"]),
                                  "pointer-class": pointer_class_skipped}}
    report["criteria"]["acc1"] = {
        "abort": bool(problems) or bool(new_holes),
        "detail": "%d conservation problem(s), %d new hole(s)" % (len(problems), len(new_holes))}

    # --- 5. B1 re-baselined census -------------------------------------------
    # N_events = ledger CONTENT events (raw/), matching the drill's pre-P5 B1 contract
    # ("recompute N_events ... from the tree" -- always meant raw/ content events, never
    # engine-sidecar receipt pointers). The 129 enlarged-ledger pointer-class receipts are
    # counted separately and NAMED (never silently folded into N_events, never silently
    # dropped) -- this keeps residue_bound's ceil(0.17*N) proportional-diagnostic bound
    # anchored to the same population it always was, so the flip does not silently inflate
    # it by +129.
    n_events_total = len(ledger)
    n_events = n_events_total - pointer_class_skipped
    n_receipts = len(receipts)
    n_holes = len(holes)
    n_sources = gold.get("sources_pairs", 0)
    n_locks = _informed_by_count(root)
    residue_bound = math.ceil(RESIDUE_FRACTION * n_events)
    residue = gold.get("residue", [])
    gold1_pointer_class_skipped = gold.get("pointer_class_skipped")
    report["census_rebaseline"] = {
        "N_events": n_events, "N_events_ledger_total_enlarged": n_events_total,
        "N_ledger_pointer_class_skipped": pointer_class_skipped,
        "N_receipts": n_receipts, "N_journal_holes": n_holes,
        "N_sources_pairs": n_sources, "N_informed_by_locks": n_locks,
        "residue_bound_ceil(0.17*N)": residue_bound,
        "residue_bound_absolute": RESIDUE_ABSOLUTE, "residue_count": len(residue),
        "gold1_pointer_class_skipped": gold1_pointer_class_skipped}
    report["criteria"]["residue-bound"] = {
        "abort": len(residue) > RESIDUE_ABSOLUTE,
        "detail": "residue %d vs GATE absolute-%d [D1 signed 2026-07-03: gate on the "
                  "stricter absolute; proportional ceil(0.17*%d)=%d reported as diagnostic]"
                  % (len(residue), RESIDUE_ABSOLUTE, n_events, residue_bound)}

    # --- 6. B2 origin-assignment census --------------------------------------
    oc = _origin_census(root, ledger)
    unparseable_unattested = [u for u in oc["unattested"] if u["origin"] == "unknown"]
    report["origin_census"] = {"counts": oc["counts"], "rule_derived": oc["rule_derived"],
                               "judgment": oc["judgment"],
                               "unparseable_unattested": len(unparseable_unattested),
                               "pointer_class_skipped": oc["pointer_class_skipped"]}
    # B2 abort: any event assigned human/corpus that is unparseable + unattested. Our rule
    # routes unparseables to unknown (never human/corpus) so this abort is structurally clear,
    # but the judgment events REMAIN for operator attestation (surfaced, not silently passed).
    human_corpus_unparseable = [u for u in oc["unattested"]
                                if u["origin"] in ("human", "corpus")]
    report["criteria"]["origin-unattested"] = {
        "abort": bool(human_corpus_unparseable),
        "detail": "%d human/corpus unparseable-unattested (abort); %d unknown-unparseable "
                  "await operator attestation (B2 review, not an abort)"
                  % (len(human_corpus_unparseable), len(unparseable_unattested))}

    # --- 7. injection list (F14) ---------------------------------------------
    inj = gold.get("injection_list", [])
    inj_no_evidence = [p for p in inj if not p.get("evidence")]
    report["injection_list"] = {"count": len(inj), "without_evidence": len(inj_no_evidence)}
    report["criteria"]["injection-evidence"] = {
        "abort": bool(inj_no_evidence),
        "detail": "%d injection pair(s), %d lacking receipt evidence (T1/lock -> abort)"
                  % (len(inj), len(inj_no_evidence))}

    # --- 8. B7 standing riders (DISPOSED 2026-07-03, operator decision: RUN) --
    report["criteria"]["riders-disposed"] = {
        "abort": False, "detail": "DISPOSED (run, not waived): (a) CL-1 v1.7/v1.4 delta "
        "re-verify discharged by the ADJUDICATED 2026-06-23 cross-vendor pass (GPT-5.2 "
        "REVISED / Gemini adjudicated -> REVISED, no standing design KILL; "
        "contract-layer-deltas-report-gpt.md); (b) the 2026-06-23 contract-fold re-verify "
        "fired 2026-07-03, gpt-5.5 confirmed/confident over the landed fold text "
        "(deploy/evidence/contract-fold-reverify-2026-07-03.json)"}

    # --- write residue snapshot + receipt (SHA-stamped, B4) into the worktree --
    cs_dir = os.path.join(root, "deploy", "test-fixtures", "memory-engine", "consumed-sets")
    os.makedirs(cs_dir, exist_ok=True)
    residue_path = os.path.join(cs_dir, "residue-p1.yaml")
    with open(residue_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# residue-p1.yaml -- P1 dry-run snapshot (NOT live). SHA-bound (B4).\n")
        fh.write("rehearsal_sha: %s\n" % sha)
        fh.write("residue_bound: %d  # ceil(0.17 * %d events)\n" % (residue_bound, n_events))
        fh.write("residue:\n")
        for r in residue:
            fh.write("  - {event: %s, reason-bucket: %s, disposition: %s}\n"
                     % (r["event"], r["reason-bucket"], r["disposition"]))
    report["residue_snapshot"] = os.path.relpath(residue_path, root).replace("\\", "/")

    aborts = [k for k, v in report["criteria"].items() if v["abort"]]
    report["dry_run_pass"] = not aborts
    report["aborts"] = aborts

    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True, default=list))
    else:
        _print_report(report)
    return 0 if report["dry_run_pass"] else 1


def _print_report(r):
    print("=" * 72)
    print("MIG-1 P1 DRY-RUN (rehearsal @ %s) -- OPERATOR HOLD POINT" % r["sha"][:12])
    print("=" * 72)
    c = r["census_rebaseline"]
    print("B1 re-baselined census: %d events · %d receipts · %d holes · %d sources-pairs · "
          "%d informed_by-locks" % (c["N_events"], c["N_receipts"], c["N_journal_holes"],
                                    c["N_sources_pairs"], c["N_informed_by_locks"]))
    print("  (P5 enlarged ledger: %d total ledger member(s), %d pointer-class receipt(s) "
          "excluded from N_events -- named, never silently folded in)"
          % (c["N_events_ledger_total_enlarged"], c["N_ledger_pointer_class_skipped"]))
    g = r["gold1"]
    print("GOLD-1: sources 100%% (%d miss) · raw-coverage %.1f%% (naive x-product %.1f%%) · "
          "buckets %s · %s"
          % (len(g["sources_pair_misses"]), g["raw_coverage"] * 100,
             g["naive_crossproduct_agreement"] * 100, json.dumps(g["buckets"]),
             "PASS" if g["pass"] else "FAIL"))
    if g.get("pointer_class_skipped") is not None:
        print("  GOLD-1 pointer-class skipped (exempt from residue like source:ref): %d"
              % g["pointer_class_skipped"])
    a = r["acc1"]
    print("ACC-1 (seeded journal): %s · %d problem(s) · %d new-hole(s)"
          % (json.dumps(a["counts"]), len(a["problems"]), len(a["new_holes"])))
    print("  ACC-1 REF-SKIPPED sub-bucket: ref=%d pointer-class=%d"
          % (a["ref_skipped_subbucket"]["ref"], a["ref_skipped_subbucket"]["pointer-class"]))
    if r.get("sources_ref_pointer_class"):
        print("  ** JUDGMENT: %d sources: ref(s) name a pointer-class receipt path -- "
              "flagged for orchestrator review **" % len(r["sources_ref_pointer_class"]))
    o = r["origin_census"]
    print("B2 origin census: %s · rule-derived %d · judgment %d · unparseable-unattested %d"
          % (json.dumps(o["counts"]), o["rule_derived"], o["judgment"],
             o["unparseable_unattested"]))
    print("  B2 pointer-class skipped (receipts exempt from origin-provenance census): %d"
          % o["pointer_class_skipped"])
    print("Injection list (F14): %d pair(s), %d without evidence"
          % (r["injection_list"]["count"], r["injection_list"]["without_evidence"]))
    print("Residue: %d vs bound %d · snapshot %s"
          % (c["residue_count"], c["residue_bound_ceil(0.17*N)"], r["residue_snapshot"]))
    print("-" * 72)
    print("ABORT CRITERIA:")
    for k, v in r["criteria"].items():
        tag = "ABORT" if v["abort"] else " ok  "
        print("  [%s] %-22s %s" % (tag, k, v["detail"]))
    print("-" * 72)
    if r["dry_run_pass"]:
        print("DRY-RUN RESULT: PASS -- all abort criteria clear. P1-live remains GATED on "
              "operator sign-off (D1 residue policy, B7 riders, origin attestations).")
    else:
        print("DRY-RUN RESULT: ABORT (%d criterion) -- P1-live BLOCKED. This is the correct "
              "STOP: resolve below, re-run the drill, THEN operator sign-off." % len(r["aborts"]))
        print("  tripped: %s" % ", ".join(r["aborts"]))


def _self_test_init_git(base):
    subprocess.run(["git", "-C", base, "init", "-q"], capture_output=True)
    subprocess.run(["git", "-C", base, "config", "user.email", "t@t"], capture_output=True)
    subprocess.run(["git", "-C", base, "config", "user.name", "t"], capture_output=True)


def _self_test_write(base, rel, text):
    p = os.path.join(base, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def self_test():
    """Minimal self-test for the P5 enlarged-ledger residual (this drill had no
    --self-test pre-P5). Exercises the two fixed call sites directly against
    synthetic tempdir roots -- never the live tree, never a subprocess invocation of
    the sibling scripts run() shells out to (backfill-derivation.py,
    drill-golden-replay.py, check-frontmatter.py are out of scope for this residual;
    they are separately owned/tested). Covers:
      (a) an enlarged root WITH full registration coverage runs _seeded_journal/ACC-1
          cleanly, with the pointer-class members counted in the named diagnostics
          and excluded from content populations (N_events / seeding membership);
      (b) a sources: ref naming a pointer-class receipt is skipped as content AND
          surfaced separately (sources_ref_pointer_class);
      (c) a receipt-bearing root MISSING its registration chain fails LOUD
          (EnlargementViolation), never silently."""
    import shutil
    import tempfile

    failed = 0
    total = 0

    def case(name, ok):
        nonlocal failed, total
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    regs = _load_module("registrations.py", "registrations_mig1_selftest")

    # --- (a)+(b): enlarged root, full registration coverage -------------------
    base = tempfile.mkdtemp(prefix="mig1-p5-selftest-")
    try:
        _self_test_init_git(base)
        _self_test_write(base, "raw/e1.md",
                          "---\nsource: session\ndate: 2026-01-01\n---\nbody\n")
        _self_test_write(base, "receipts/r1.md",
                          "---\ntype: compile\ntimestamp: 2026-01-01T000000\nraw_inputs:\n"
                          "  - raw/e1.md\narticles_modified:\n  - path: wiki/v.md\n"
                          "    operation: created\n---\n")
        # a view whose sources: legitimately cites the raw/ content event ...
        _self_test_write(base, "wiki/v.md",
                          "---\ntitle: v\nsources:\n  - raw/e1.md\n  - receipts/r1.md\n"
                          "---\nbody\n")
        # ... and ALSO (deliberately, for case (b)) names the pointer-class receipt.
        regs.append_registration(base, {
            "kind": "registration", "event": "raw/e1.md", "origin": "corpus",
            "origin_evidence": "t", "event_class": "session", "event_class_origin": "judgment",
            "asserts_corpus_state": False, "registered_at": "2026-01-01T00:00:00"})
        regs.append_registration(base, {
            "kind": "registration", "event": "receipts/r1.md", "origin": "corpus",
            "origin_evidence": "t", "event_class": "compile", "event_class_origin": "explicit",
            "asserts_corpus_state": True, "registered_at": "2026-01-01T00:00:00"})

        ledger = staleness.load_ledger(base)
        case("enlarged root with full registration coverage: load_ledger runs clean "
             "(no EnlargementViolation)", set(ledger) == {"raw/e1.md", "receipts/r1.md"})
        case("the receipt is annotated pointer_class True",
             ledger["receipts/r1.md"]["pointer_class"] is True)

        journal, seeded, skipped, ptr_refs = _seeded_journal(base)
        case("sources: raw/e1.md (real content event) IS seeded",
             ("wiki/v.md", "raw/e1.md") in seeded)
        case("sources: receipts/r1.md (pointer-class) is NOT seeded as content",
             ("wiki/v.md", "receipts/r1.md") not in seeded
             and ("wiki/v.md", "receipts/r1.md") in skipped)
        case("the pointer-class sources: ref is separately named/counted "
             "(sources_ref_pointer_class), never silently folded into an ordinary skip",
             ptr_refs == [("wiki/v.md", "receipts/r1.md")])

        matches = staleness.load_matches(base, ledger)
        receipts = staleness.load_receipts(base)
        known = staleness.load_known_holes(base)
        _j, holes_report = staleness.build_journal(receipts, known)
        holes = set(rid for (rid, _a, _ok) in holes_report)

        def view_exists(v):
            return os.path.isfile(os.path.join(base, v))
        result, problems = staleness.conservation_audit(
            ledger, journal, matches, set(), holes, view_exists=view_exists)
        case("ACC-1 over the enlarged ledger has zero side-condition problems",
             problems == [])
        case("the pointer-class receipt classifies REF-SKIPPED (never CONSUMED, "
             "despite being named in sources:)", result["receipts/r1.md"] == "REF-SKIPPED")

        ref_skip_sub = staleness._ref_skip_subbucket(
            ledger, [e for e, c in result.items() if c == "REF-SKIPPED"])
        case("ACC-1's REF-SKIPPED sub-bucket counts the receipt under pointer-class, "
             "not ref", ref_skip_sub["pointer-class"] == ["receipts/r1.md"]
             and ref_skip_sub["ref"] == [])

        n_events_total = len(ledger)
        n_events = n_events_total - len(ref_skip_sub["pointer-class"])
        case("B1 N_events excludes the pointer-class receipt from the content count "
             "(named separately, never silently inflating the count)",
             n_events == 1 and n_events_total == 2)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    # --- (c): receipt present but registration chain missing -> LOUD failure ---
    base_missing = tempfile.mkdtemp(prefix="mig1-p5-selftest-missing-")
    try:
        _self_test_init_git(base_missing)
        _self_test_write(base_missing, "raw/e1.md",
                          "---\nsource: session\ndate: 2026-01-01\n---\nbody\n")
        _self_test_write(base_missing, "receipts/r1.md",
                          "---\ntype: compile\ntimestamp: 2026-01-01T000000\nraw_inputs:\n"
                          "  - raw/e1.md\narticles_modified:\n  - path: wiki/v.md\n"
                          "    operation: created\n---\n")
        # NO registrations minted at all -- the chain is entirely absent.
        try:
            staleness.load_ledger(base_missing)
            case("a receipt-bearing root with NO registration chain fails LOUD "
                 "(EnlargementViolation), never silent-clean", False)
        except staleness.EnlargementViolation:
            case("a receipt-bearing root with NO registration chain fails LOUD "
                 "(EnlargementViolation), never silent-clean", True)

        try:
            _seeded_journal(base_missing)
            case("_seeded_journal on a registration-less receipt root ALSO propagates "
                 "the loud failure (never swallowed by the call site)", False)
        except staleness.EnlargementViolation:
            case("_seeded_journal on a registration-less receipt root ALSO propagates "
                 "the loud failure (never swallowed by the call site)", True)
    finally:
        shutil.rmtree(base_missing, ignore_errors=True)

    if failed:
        print("drill-migration-p1 self-test: FAIL (%d/%d)" % (failed, total))
        return 1
    print("drill-migration-p1 self-test: PASS (%d/%d)" % (total, total))
    return 0


def main(argv):
    args = argv[1:]
    if "--self-test" in args:
        return self_test()
    root = sha = None
    if "--root" in args:
        root = args[args.index("--root") + 1]
    if "--sha" in args:
        sha = args[args.index("--sha") + 1]
    if not root:
        print("usage: drill-migration-p1.py --root WORKTREE --sha SHA [--json]")
        return 2
    if not sha:
        p = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                           capture_output=True, text=True)
        sha = (p.stdout or "unknown").strip()
    # gold1-metric amendment ADOPTED 2026-07-03 (operator sign-off; recorded in the
    # amendment file + ledger) -> raw-coverage is the default; --frozen-gold1-metric
    # re-runs the pre-adoption frozen metric for archaeology only.
    return run(root, sha, as_json="--json" in args,
               adopt_amendment="--frozen-gold1-metric" not in args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
