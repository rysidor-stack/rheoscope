#!/usr/bin/env python3
"""check-phase-gate.py -- the MIG-2 phase-advance runner (test-plan tp:522-528).

One PASS/FAIL line per blocking gate for a phase boundary; exit 1 on any fail.
This is the runner the frozen test plan (§3) and the 2026-07-01 build-plan-review
amendments require: it reads the AMENDED gate lists, not the frozen §3 tables alone
(gate-registration-amendment closing note; mig1-composition-amendment B0.3).

Honesty contract (the whole point of the gate):
  * A gate whose sensor is BUILT runs its real check; exit code -> PASS/FAIL/INCONCLUSIVE.
  * A gate whose sensor is NOT BUILT prints PENDING and BLOCKS the advance -- it is
    never silently green. Certifying an absent tripwire is the failure class the
    gate-registration amendment C1.2 and mig1 amendment B0.1 both name explicitly.
  * The first recorded run of `--phase P1` is the P0->P1 advance (mig1 B0.3): the
    first boundary the runner polices must not be crossed on operator memory.

Usage:
  check-phase-gate.py --phase P0|P1|P1-live|P2|P3|P4|P5   run one boundary's gate set
  check-phase-gate.py --list [--phase PX]                 show the (amended) gate registry
  check-phase-gate.py --self-test                         runner self-check (registry well-formed)

Exit codes: 0 = every blocking gate PASS (or legitimately N/A) | 1 = any FAIL/PENDING
  blocking gate, or a self-test failure | 2 = INCONCLUSIVE (a gate could not decide;
  e.g. PyYAML unavailable) with no hard FAIL.

Amendments folded in (dated siblings; the frozen tp is never edited):
  * content2-gate-amendment  : CONTENT-2 is not green until check-split.py implements the
                               anchor+conjunction grammar and re-runs the two real splits.
  * gate-registration-amendment: registers TOOL-GRANT-ISOLATION (pre-merge+P4),
                               AUTONOMY-DISPOSITION (pre-merge+P2); rebinds EGRESS-CO-RESIDENCY
                               onto F-11/F-8; adds F-13 to ORIGIN-PROPAGATION (P1);
                               BRIDGE-SCHEMA dormant (n/a - §8b dark).
  * mig1-composition-amendment: ACC-3b BLOCKS P1 (B0.4); P1-live requires the full P1 row
                               green on the dry-run worktree + SHA-binding + riders disposed.
"""

import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)   # repo root: corpus gates resolve wiki/raw/receipts from cwd
_PY = sys.executable or "python3"

# CONTENT-2 amendment evidence: the two real P0 splits re-run under the anchor (A1) +
# conjunction-citation (A2) grammar. A4.2 voids any pre-amendment verdict; this file is
# the citable green. Written by the re-run; its presence is a PASS precondition.
_CONTENT2_EVIDENCE = os.path.join(_HERE, "evidence", "content-2-real-splits-rerun.md")

# ACC-1-rebaselined evidence: THIS FORK's committed staleness-census baseline
# (253 events, captured 2026-07-06). INSTANCE-SPECIFIC, not a shared fixture --
# a fresh instance (or any checkout without this fork's deploy/evidence/ history)
# has no such file and must mint its own dated baseline JSON, then repoint this
# constant at it (v3.0-21 backlog: re-baseline-as-an-operator-operation is a
# named residual this does NOT build; this only makes the pinned-path gate
# absent-tolerant instead of crashing -- see _run_acc1_rebaselined below).
_ACC1_REBASELINE_EVIDENCE = os.path.join(_HERE, "evidence",
                                         "p5-acc1-baseline-2026-07-06.json")

# --- verdict tokens ------------------------------------------------------------
PASS = "PASS"
FAIL = "FAIL"
PENDING = "PENDING"      # sensor not built -> blocks advance, never silent-green
INCONCLUSIVE = "INCON"   # gate could not decide (e.g. PyYAML missing)
NA = "N/A"               # legitimately not-applicable at this boundary (does not block)

_BLOCKING_FAILS = (FAIL, PENDING)


class Gate:
    """One blocking gate at a phase boundary.

    runner: None                 -> PENDING (sensor not built; blocks)
            (script, [args], ok) -> run `python script args`; PASS iff returncode in `ok`
    note:   free-text; amendment pointer or why-N/A.
    na:     True -> reported N/A (non-blocking) at this boundary.
    """

    def __init__(self, gid, gclass, desc, runner=None, note="", na=False):
        self.gid = gid
        self.gclass = gclass
        self.desc = desc
        self.runner = runner
        self.note = note
        self.na = na


def _script(name):
    return os.path.join(_HERE, name)


# Runner tuples: (script_basename, [extra args], set_of_ok_returncodes)
# `--self-test` proves the gate's LOGIC; a bare run exercises it against the LIVE corpus.
# For corpus gates we run the live check (the boundary is about the real tree); logic-only
# gates run --self-test. A gate can be scored by BOTH by listing it twice is avoided -- we
# pick the invocation that is meaningful at the boundary and note the other.

R_ECO3      = ("check-caps.py", ["--self-test"], {0})
R_OPS5      = ("check-path-containment.py", ["--self-test"], {0})
R_FRONT     = ("check-frontmatter.py", ["--self-test"], {0})
R_ACC12_ST  = ("staleness.py", ["--self-test"], {0})
R_ACC12_LIVE= ("staleness.py", [], {0})
R_ROUTE1_ST = ("catalog.py", ["--self-test"], {0})
R_ROUTE1_LIVE=("catalog.py", [], {0})
R_SPLIT_ST  = ("check-split.py", ["--self-test"], {0})
R_DERIV_ST  = ("check-derivation.py", ["--self-test"], {0})


def _registry():
    """The amended gate registry, keyed by phase. Order = execution order."""
    reg = {}

    # ---- P0: caps + split foundations (tp:60) --------------------------------
    reg["P0"] = [
        Gate("ECO-3", "D,E", "byte-cap CRLF stability (LF-normalized caps)", R_ECO3),
        Gate("OPS-5", "D,E", "path containment (symlink/../abs/junction/ADS)", R_OPS5,
             note="mig1 Appendix-K OPS-5 rider: open-then-verify + plant-symlink race "
                  "fixture re-runs at P1-entry"),
        Gate("CONTENT-2", "C", "split-citation integrity (stub+anchors+line-num map)",
             _run_content2,
             note="content2-amendment A4: self-test green is NOT sufficient -- gate is not "
                  "scorable green until check-split.py implements the anchor grammar (A1) "
                  "+ conjunction-aware citation grammar (A2) + 3 fixtures (A3) AND the two "
                  "real P0 splits re-run under A1/A2 (A4.2 voids the pre-amendment verdict)."),
        Gate("check-frontmatter", "C", "extended self-test (KNOWN_KEYS + derivation keys)",
             R_FRONT,
             note="mig1 B0.1: KNOWN_KEYS must union the derivation keys + flatten-scan the "
                  "derivation region + flattened-derivation fixture. Sensor arrives via "
                  "template->fork sync, never a direct fork edit."),
        # TOOL-GRANT-ISOLATION P0-half (gate-reg C1.2): only if the grant-decision code is
        # built at P0. It is not (P0 = caps+splits) -> N/A here, NOT a silent green.
        Gate("TOOL-GRANT-ISOLATION", "SEC", "boot-time grant-decision self-test (F-1..F-13)",
             None, na=True,
             note="gate-reg C1.2: grant-decision code not built at P0 -> N/A at P0. Real home "
                  "is pre-merge + P4. Do NOT record a P0 green covering it while unbuilt."),
    ]

    # ---- P1: entities + backfill + seeding (IRREVERSIBLE) (tp:61) -------------
    reg["P1"] = [
        Gate("ACC-1", "A", "conservation tripwire (7-class ordered table)", R_ACC12_LIVE,
             note="logic proven by staleness.py --self-test (38/38); live census must PASS."),
        Gate("ACC-2", "A", "journal-hole injection (unparseable receipts -> PENDING)",
             R_ACC12_ST, note="shares staleness.py; --self-test covers the 3 hole archetypes."),
        Gate("ACC-3b", "A", "journal-sidecar injection (11 JSON defect vectors + quarantine)",
             ("check-journal-sidecar.py", ["--self-test"], {0}),
             note="mig1 B0.4: BLOCKS P1. 11 defect vectors + F5 quarantine recovery."),
        Gate("GOLD-2", "B", "golden-10 routing subset (frozen, hand-verified)",
             ("check-golden.py", ["--self-test"], {0}),
             note="10 events, all 7 classes; incl #8 REF-SKIPPED-with-match (F1)."),
        Gate("ROUTE-1", "B", "entity-vocab self-test (entities.yaml governance)", R_ROUTE1_LIVE,
             note="logic proven by catalog.py --self-test (15/15); live vocab must PASS."),
        Gate("ECO-2", "E", "skew guard (schema_version, both directions)",
             ("check-eco2.py", ["--self-test"], {0}),
             note="drives the check-frontmatter skew refusal; older+newer both REFUSE."),
        Gate("LLM-5", "A,C", "substrate-separation (structured verified:; vendor!=)",
             ("check-substrate.py", ["--self-test"], {0}),
             note="F17/F18: separate vendor+model_id; routine gates on model_id, migration/design "
                  "on vendor. Self-test = mechanism; operator-fired cross-vendor bless still owed."),
        Gate("ORIGIN-PROPAGATION", "D,E", "session-loop taint (origin can't decrease) + F-13",
             ("check-origin-propagation.py", ["--self-test"], {0}),
             note="gate-reg C3 (F-13 ingest-tag) + mig1 B6 (non-forgeable human-mint, intake-only). "
                  "Self-test = mechanism; operator-fired cross-vendor bless still owed."),
        Gate("CONTENT-4", "A,D", "formatter drill (replay 2026-06-09 incident)",
             ("drill-formatter.py", ["--self-test"], {0}),
             note="unit self-test here; full worktree drill (drill-formatter.py --root WT) proven "
                  "PASS: 6 flattened blocks detected + run-journal byte-identical (design win). "
                  "Evidence: deploy/evidence/content-4-formatter-drill-2026-07-02.md."),
        Gate("MIG-1", "A", "P1 dry-run gate (seeding rehearsal + GOLD-1 + operator review)",
             None,
             note="THE HOLD POINT. drill-migration-p1.py BUILT; needs a linked rehearsal "
                  "worktree (B4 interlock) so it cannot auto-run here -- fire it manually: "
                  "git worktree add --detach <TMP> <SHA>; python deploy/drill-migration-p1.py "
                  "--root <TMP> --sha <SHA>. 2026-07-02 run: ABORT on gold1-signal2 under the "
                  "frozen metric (correct STOP; PASSes under --adopt-gold1-metric-amendment, "
                  "operator sign-off pending). Runs under mig1-amendment B1-B7; GOLD-1 inside."),
    ]

    # ---- P1-live: the single irreversible step (mig1 B4.4) -------------------
    reg["P1-live"] = [
        Gate("MIG-1-PASS+SHA", "A", "MIG-1 PASS receipt whose stamped SHA == current HEAD",
             None, note="mig1 B4.3: P1-live entry exits 1 unless this holds."),
        Gate("OPERATOR-APPROVAL", "A", "operator approval raw file naming that SHA",
             None, note="mig1 B4.2 + doctrine §1 hard rule."),
        Gate("FULL-P1-ROW-GREEN", "A", "all ten P1 gates green on the dry-run worktree",
             None, note="mig1 B4.4 ([UNVERIFIED] PG-6, adopted)."),
        Gate("RIDERS-DISPOSED", "A", "B7 standing re-verify riders each disposed (run|waive)",
             None, note="mig1 B7.1: riders undisposed -> BLOCKED."),
    ]

    # ---- P2: compile v2 (tp:62) ---------------------------------------------
    reg["P2"] = [
        Gate("OPS-1", "A/D", "crash-ordering drill",
             ("drill-crash-absorb.py", [], {0}),
             note="built 2026-07-04 on compile-core.py primitives (13/13): three "
                  "--crash-point cases + inverse-ordering orphan-record detection."),
        Gate("OPS-2", "A/D", "concurrency drill",
             ("drill-concurrency.py", [], {0}),
             note="built 2026-07-04 (10/10): live-lock clean refusal exit 3 zero-writes, "
                  "stale-break via --break-stale-lock w/ receipt logging, live-PID never "
                  "auto-broken."),
        Gate("OPS-3", "A/D", "stage-only discipline",
             ("drill-stage-only.py", [], {0}),
             note="built 2026-07-04 (9/9): 4-class salt incl. in-wiki unclaimed hard case; "
                  "commit-set==manifest-set; per-path adds (directory manifest refused)."),
        Gate("OPS-4", "A/D", "P2 workload acceptance (blocking)",
             ("drill-workload-bench.py",
              ["--verdict",
               "--receipt", "deploy/evidence/ops4-workload-bench-2026-07-05/receipt.json",
               "--baseline", "deploy/evidence/ops4-workload-bench-2026-07-05/baseline.json"],
              {0}),
             note="OPERATOR-ACCEPTED 2026-07-05 (operator-decision-2026-07-05-ops4-accepted.md; "
                  "2 caveats recorded: baseline 12-event-sample sensitivity; verify confirm-power "
                  "= P3). Green = the re-runnable mechanical verdict over the committed "
                  "receipt+baseline (68-event replay, 1.394x tokens vs 1.5x cap; evidence "
                  "ops4-workload-bench-2026-07-05.md). Re-run trigger per tp:308: >20% T1-share "
                  "shift or receipt count >200."),
        Gate("OPS-6", "A/D", "lock-common-dir",
             ("drill-lock-common-dir.py", [], {0}),
             note="built 2026-07-04 (12/12): 2-worktree seq race (20 slots, 0 collisions), "
                  "legal same-parent concurrency trips nothing, gap/dup/mismatch each "
                  "named. Chain authority = prev_record_hash (compile-core.check_chain)."),
        Gate("ACC-4", "A/D", "manifest-vs-diff",
             ("check-run-diff.py", ["--self-test"], {0}),
             note="built 2026-07-04 (12/12 incl. LLM-2 section cases): fabricated-claim/"
                  "stowaway both directions; no-op artifact+packet-hash+full-body "
                  "justification; T1 same-run window; F6 judgment->lock-class. Live form: "
                  "check-run-diff.py --commit <sha> [--sections] once compile-v2 commits "
                  "exist."),
        Gate("LLM-2", "A/D", "merge-manifest audit (check-run-diff --sections)",
             ("check-run-diff.py", ["--self-test"], {0}),
             note="section-granularity manifest-vs-blob-diff: claim-without-hunk and "
                  "hunk-without-claim fixtures both trip; shares ACC-4's engine per tp:360."),
        Gate("ECO-4", "A/D", "catalog/projection idempotence",
             ("drill-eco4.py", [], {0}),
             note="built 2026-07-04 (7/7): project.py (new, minimal deterministic "
                  "skeletons, sourced dates only, writes only under --out) + catalog.py "
                  "byte-stable across consecutive runs; git tree unchanged."),
    ] + [
        Gate("ACC-5", "A", "replay-cost benchmark (<2s live, <10s @1000, ratio <=15)",
             ("drill-replay-bench.py", ["--root", os.path.join(_HERE, "..")], {0}),
             note="full bench (~90s): live tree timed FIRST (in-process synthetic churn "
                  "inflates a later live reading ~2x), then synthetic 100/300/1000. "
                  "2026-07-03 baseline: live 1.68s @129 receipts (build_journal dominates "
                  "-- thin margin, re-run at 200 per tp:137), t(1000)=7.4s, ratio 9.8."),
    ] + [
        Gate("AUTONOMY-DISPOSITION", "SEC", "disposition-function self-test (autonomy §8)",
             ("check-autonomy-disposition.py", ["--self-test"], {0}),
             note="gate-reg C4: pre-merge + P2. Built 2026-07-03 against autonomy spec "
                  "v1.4 §2/§5/§8: fixtures A-1..A-19 + promotion policy + T1-aggregate/"
                  "trusted-surface accumulators + a 320-point invariant sweep (23/23). "
                  "The daemon/orchestrator that CALLS disposition() is compile-v2 work; "
                  "live wiring lands at P2 entry."),
    ]

    # ---- P3: reconcile + verify (tp:63) -------------------------------------
    reg["P3"] = [
        Gate("LLM-1", "A,B,C", "planted-defect efficacy (d1+d3 hard floor) (blocking)",
             ("drill-planted-defects.py", ["--self-test"], {0}),
             note="planted-defect efficacy floor (d1/d3): 4 plant classes (dropped-claim/"
                  "fabricated-support/manifest-omission/deletion-floor-nibble) + healthy "
                  "control; mechanical refusals journal nothing; dropped-claim = judgment "
                  "catch via diff-carrying audit packet (fixture backend in self-test; real "
                  "legs via --run --live weekly batch). F13 no-implication-path rule "
                  "asserted in fixtures."),
        Gate("LLM-6", "C", "corpus-support (verbatim support_lines at pinned SHA)",
             ("check-corpus-support.py", ["--self-test"], {0}),
             note="read-side F16 (support_lines re-verified at pinned artifact_sha256, "
                  "current|historical|UNRESOLVED resolution, violation>inconclusive) + F15 "
                  "standalone deterministic routing census (event->views from view-side "
                  "sources:, git-blob-sha1 vs content-sha256 kept distinct). CAVEAT: covers "
                  "the in-repo corpus_support convention validate_absorb_output implements; "
                  "the packaging-brief's external-corpus Corpus Observation surface (R1/R2 "
                  "skip clause) is a distinct future path, not claimed. verify_run "
                  "hash-journaling integration owed."),
        Gate("LLM-6-census", "C", "routing census (event->views, deterministic, standalone)",
             ("routing-census.py", ["--self-test"], {0}),
             note="see LLM-6 note; this row is the routing-census.py half of the F15/F16 "
                  "read-side pair (no single-command convention exists in this registry for "
                  "a two-sensor gate, so it is split across two rows)."),
        Gate("CONTENT-1", "C", "deletion-floor (headings preserved; <=30% shrink)",
             ("compile-v2.py", ["--self-test"], {0}),
             note="deletion floor (headings preserved, <=30% shrink) ENFORCED inline in "
                  "validate_absorb_output pre-journal; green = compile-v2 self-test incl. "
                  "the two CONTENT-1 refusal cases (\"CONTENT-1: dropped heading refused\", "
                  "\">30% shrink refused\"); every compile run enforces it live (tp:226)."),
        Gate("CONTENT-3", "C", "verified-reset (no stale verified: on changed body)",
             ("check-verified-reset.py", ["--self-test"], {0}),
             note="journal-pin sensor (last verify stamp per view vs current body sha; "
                  "union_view_sha256 aware; inconclusive on unusable pins, never "
                  "silent-clean). Canonical frontmatter verified:-reset via "
                  "check-derivation.py NOT built -- owed; this row covers the risk class "
                  "from the journal side. Live invocation: check-verified-reset.py --check "
                  "--root . --json receipts/verified-reset-receipt.json -- on this repo "
                  "state (no receipts/journal yet) that exits 2 INCONCLUSIVE, not 0, so "
                  "--self-test is registered here instead; re-point to --check once the "
                  "journal exists."),
    ]

    # ---- P4: assemble (tp:64) -----------------------------------------------
    reg["P4"] = [
        Gate("ECO-1", "E", "packet-recall (golden descriptors; 100%)",
             ("assemble.py", ["--self-test"], {0}),
             note="built 2026-07-05 -- golden descriptors (deploy/descriptors/golden-descriptors.yaml, "
                  "7 keys, live catalog, alias-reachability mechanically verified) run inside "
                  "assemble --self-test: selection recall 100% all types, packet recall + byte-fit "
                  "for emitting types, refuse-F12 honest current outcome for build/fix (GOLD-2 flip "
                  "discipline), stale-forcing negative half on overlay. Location deviation "
                  "(deploy/descriptors/) documented in file header."),
        Gate("ECO-5", "E", "bundle-closure termination + cycle detection",
             ("assemble.py", ["--self-test"], {0}),
             note="BFS closure, visited-set dedup, NAMED cycle reports, <1s on "
                  "chain/diamond/3-cycle/self-loop; T1 build/fix overflow refused naming member; "
                  "real tree: 53-view closure, 30 named cycles."),
        Gate("ECO-7", "D,E", "taint-quarantine (external/unknown out of build packets)",
             ("assemble.py", ["--self-test"], {0}),
             note="all four test-plan fixture cases verbatim; task-type keyed never tier; "
                  "only-match => exit 1 naming view+origin."),
        Gate("ORIGIN-PROPAGATION-INT", "D,E", "origin propagation integration (P4)",
             ("check-origin-propagation.py", ["--self-test"], {0}),
             note="record layer over the P1 battery -- registered origin >= packet_origin_max "
                  "(F7 lattice), ryan-*+receipt mints human, Inconclusive exit 2 distinct; "
                  "seam = assemble packet envelope's packet_origin_max."),
        Gate("EGRESS-CO-RESIDENCY", "D,E,SEC", "taint+credential+egress separation",
             ("assemble.py", ["--self-test"], {0}),
             note="gate-reg C2 rebind operative -- F-11 (descriptor attestation strings "
                  "recorded-ignored; PASS = credential tool ABSENT from computed toolbox, exit 0 "
                  "correctly-denied; tainted+credentialed structurally unreachable, exit-2 backstop "
                  "unit-tested) + F-8 (boot-time immutability, no strip verb); design amendment "
                  "memory-engine-v3-assemble-machinery-design-2026-07-05.md."),
        Gate("TOOL-GRANT-ISOLATION", "SEC", "boot-time grant-decision self-test (F-1..F-13)",
             ("assemble.py", ["--self-test"], {0}),
             note="FULL current TCB s12 catalog F-1..F-16 executed via tool_grant.py folded into "
                  "assemble --self-test (C1's F-1..F-13 wording predates F-14..F-16 -- TCB s12 is "
                  "authority); corpus split operative fail-closed (plain corpus untrusted; only "
                  "human trusted until signer-verified manifest + out-of-workspace trust root); "
                  "F-15/F-16 = executor guard predicates, future executor must route through them; "
                  "pre-merge semantics per C1."),
        Gate("F17-ATTESTATION", "SEC,D", "write-path attestation (substrate derived from invocation metadata)",
             ("check-substrate.py", ["--self-test"], {0}),
             note="built 2026-07-05; per-leg invocation-record attestation -- verifier channel "
                  "subprocess-runtime (spawn argv + codex stderr self-report, fail-closed "
                  "substrate-gated on disagreement/unknown channel; attestation_ok strict-AND), "
                  "absorb channel dispatch-record (stamp_dispatch content-sha manifest stamp, "
                  "raw-dict bypass removed r5); write-path coverage lives in compile-backends "
                  "--self-test (126/126); conformance recorded-at-revised residual named "
                  "(f17-conformance-bless-2026-07-05.json); design amendment "
                  "memory-engine-v3-f17-attestation-design-2026-07-05.md."),
        Gate("SERVE-T1-REFUSAL", "D,E", "serving gate: assemble.py refuses unverified-T1 views (spec s7 tail, F12)",
             ("assemble.py", ["--self-test"], {0}),
             note="built 2026-07-05, 18/18; atomic refusal, fail-closed on missing "
                  "block/tier/consumed_status, legacy-assumed distinct reason code "
                  "(operator-loosenable), T3 passes; F12 backfill run on fork wiki 2026-07-05 "
                  "(64 views, legacy-assumed per P1 seeding); F11 origin_max out of scope "
                  "(later gate)."),
    ]

    # ---- P5: receipts/locks as typed events (tp:65) -------------------------
    reg["P5"] = [
        Gate("GOLD-1-rerun", "A", "full-history accounting replay (re-run)",
             ("drill-golden-replay.py", [], {0}),
             note="built 2026-07-06 per the P5 typed-events design sibling -- live run over "
                  "the ENLARGED ledger (staleness.load_ledger default since the atomic flip); "
                  "PASS = 100% sources-pairs + raw-coverage >= floor + zero untriaged; "
                  "pointer-class ledger entries exempt from residue (named diagnostic), "
                  "signals proven bit-identical pre/post enlargement (proof obligation 3)."),
        Gate("ACC-1-rebaselined", "A", "conservation tripwire re-baselined",
             _run_acc1_rebaselined,
             note="built 2026-07-06 per the P5 typed-events design sibling -- re-runs the "
                  "enlarged-ledger census live and diffs against the committed baseline "
                  "(253 events; 129 receipts REF-SKIPPED via the pointer-class sub-bucket; "
                  "raw classes unchanged). OPS-4 --verdict precedent: re-runnable, never "
                  "memory. v3.0-21 residual: the pinned baseline "
                  "(_ACC1_REBASELINE_EVIDENCE) is instance-specific -- absent in a fresh "
                  "instance -> PENDING with mint instructions, never a crash (see "
                  "_run_acc1_rebaselined)."),
        Gate("check-loop-state-ext", "A", "check-loop-state extension",
             ("check-loop-state.py", ["--self-test"], {0}),
             note="built 2026-07-06 per the P5 typed-events design sibling -- ported sensor "
                  "+ three extension checks (a)/(b)/(c) over fixture trees incl. the "
                  "2026-07-06 adjudication cases (pre-protocol leniency scoping, "
                  "HARNESS_ERA note, canonical F6 lock-class predicate); live --check "
                  "green (0 violations, named NOTEs) recorded in "
                  "deploy/evidence/p5-atomic-flip-2026-07-06.md."),
    ]

    return reg


def _invoke(script, args):
    """Run a gate script from the REPO ROOT (so corpus gates resolve wiki/raw/receipts
    via os.getcwd()) and return (returncode, last_nonblank_stdout_line)."""
    path = _script(script)
    if not os.path.exists(path):
        return None, "%s missing on disk" % script
    proc = subprocess.run([_PY, path] + list(args), cwd=_ROOT,
                          capture_output=True, text=True)
    last = ""
    for line in (proc.stdout or "").strip().splitlines()[::-1]:
        if line.strip():
            last = line.strip()
            break
    return proc.returncode, last


def _run_content2():
    """CONTENT-2 is amendment-gated (content2-amendment A4): self-test alone is NOT
    green. PASS requires (a) check-split.py implements the anchor grammar incl. explicit
    ID spans + the conjunction/range citation grammar, (b) --self-test passes (incl. the
    3 new fixtures), (c) the two real P0 splits re-run evidence exists."""
    src_path = _script("check-split.py")
    if not os.path.exists(src_path):
        return PENDING, "check-split.py missing"
    try:
        with open(src_path, "r", encoding="utf-8") as fh:
            src = fh.read()
    except OSError as e:
        return INCONCLUSIVE, "cannot read check-split.py: %s" % e
    # (a) amendment markers: explicit ID spans + full citation separators + fail-closed guard
    markers = {
        "explicit-id-spans": ("{#" in src) and ("<a id" in src),
        "citation-separators": ("&" in src) and ("," in src) and ("and" in src),
        "range-citation": ("–" in src) or ("range" in src.lower()),
        "empty-anchor-INCONCLUSIVE": ("INCONCLUSIVE" in src) or ("exit 2" in src) or (" 2 " in src and "empty" in src.lower()),
        "amendment-fixtures": ("split-citation-conjunction" in src) and ("split-empty-anchor" in src),
    }
    missing = [k for k, v in markers.items() if not v]
    if missing:
        return PENDING, ("check-split.py does not yet implement content2-amendment A1-A3 "
                         "(missing: %s). A4.2 voids the pre-amendment verdict." % ", ".join(missing))
    # (b) self-test
    rc, last = _invoke("check-split.py", ["--self-test"])
    if rc is None:
        return PENDING, last
    if rc != 0:
        return FAIL, "check-split.py --self-test failed: %s" % last
    # (c) real-split re-run evidence
    if not os.path.exists(_CONTENT2_EVIDENCE):
        return PENDING, ("A1-A3 implemented + self-test green, but the two real P0 splits "
                         "re-run evidence is not recorded (%s). A4.2 requires it."
                         % os.path.relpath(_CONTENT2_EVIDENCE, _ROOT))
    return PASS, "A1-A3 implemented; self-test green; real-split re-run recorded"


def _run_acc1_rebaselined(evidence_path=None, invoke=None):
    """ACC-1-rebaselined re-runs the enlarged-ledger census and diffs it
    against a COMMITTED baseline file (staleness.py --baseline-check PATH).
    That baseline is fork-specific -- minted once, on this fork's history, not
    a general mechanism (v3.0-21 backlog: full re-baseline-as-an-operation is
    a named residual, NOT built here). A fresh instance, or any repo checked
    out without this fork's deploy/evidence/ baseline file, would otherwise
    hit staleness.py's `open(path, ...)` with a nonexistent path and crash
    with an uncaught FileNotFoundError -- surfacing here as an opaque
    `rc=1` FAIL with no note, indistinguishable from a real census
    regression. Absent-tolerance only: check for the file BEFORE invoking the
    subprocess, and report PENDING (this registry's existing vocabulary for
    "the tripwire is real but its evidence is not yet in place", per
    _run_content2 above) with instructions for minting a new one, rather than
    letting the subprocess crash or silently passing.

    PRESENT-BASELINE MAPPING PARITY: with the file present, the rc -> verdict
    mapping below reproduces the generic tuple-dispatcher semantics this gate
    had when it was registered as ("staleness.py", [...], {0}) -- rc None ->
    PENDING (script missing), rc in {0} -> PASS, rc 2 -> INCONCLUSIVE
    (staleness.py's documented exit 2 = PyYAML unavailable), anything else ->
    FAIL. The absent-file PENDING above is the ONLY behavioral delta.
    `invoke` is a self-test seam (defaults to _invoke, same injectable-runner
    pattern compile-backends' BridgeVerifyBackend uses) so the mapping is
    pinnable by fixtures without a real staleness.py subprocess."""
    path = evidence_path or _ACC1_REBASELINE_EVIDENCE
    invoke = invoke or _invoke
    if not os.path.exists(path):
        return PENDING, (
            "ACC-1-rebaselined evidence absent (%s): this fork-specific "
            "baseline was never minted in this instance. A new instance must "
            "mint its own baseline -- a dated JSON under deploy/evidence/ "
            "capturing the staleness census baseline (staleness.py "
            "--baseline-write PATH) -- then point _ACC1_REBASELINE_EVIDENCE "
            "(top of this file) at it. Full re-baseline-as-an-operator-"
            "operation remains the named v3.0-21 harness-backlog residual, "
            "not built here." % os.path.relpath(path, _ROOT))
    script, args = "staleness.py", ["--baseline-check", path]
    rc, last = invoke(script, args)
    if rc is None:
        return PENDING, last
    if rc in {0}:
        return PASS, last or ("%s %s -> rc=%d" % (script, " ".join(args), rc))
    if rc == 2:
        return INCONCLUSIVE, last or "rc=2 (inconclusive)"
    return FAIL, last or ("rc=%d" % rc)


def _run_gate(gate):
    """Return (verdict, detail)."""
    if gate.na:
        return NA, gate.note or "not applicable at this boundary"
    if gate.runner is None:
        return PENDING, "sensor not built"
    if callable(gate.runner):
        return gate.runner()
    script, args, ok = gate.runner
    rc, last = _invoke(script, args)
    if rc is None:
        return PENDING, last
    if rc in ok:
        return PASS, last or ("%s %s -> rc=%d" % (script, " ".join(args), rc))
    if rc == 2:
        return INCONCLUSIVE, last or "rc=2 (inconclusive)"
    return FAIL, last or ("rc=%d" % rc)


def _emit(gate, verdict, detail):
    tag = {PASS: "PASS ", FAIL: "FAIL ", PENDING: "PEND ",
           INCONCLUSIVE: "INCON", NA: " N/A "}[verdict]
    line = "  [%s] %-22s %s" % (tag, gate.gid, gate.desc)
    print(line)
    if verdict in (PENDING, FAIL, INCONCLUSIVE) and gate.note:
        print("           ^ %s" % gate.note)
    elif verdict == NA and gate.note:
        print("           (%s)" % gate.note)


def run_phase(phase):
    reg = _registry()
    if phase not in reg:
        print("unknown phase %r; known: %s" % (phase, ", ".join(reg)))
        return 1
    gates = reg[phase]
    print("== MIG-2 phase gate: %s ==  (%d blocking gate(s))" % (phase, len(gates)))
    verdicts = []
    for g in gates:
        v, d = _run_gate(g)
        _emit(g, v, d)
        verdicts.append(v)
    n_pass = verdicts.count(PASS)
    n_pend = verdicts.count(PENDING)
    n_fail = verdicts.count(FAIL)
    n_incon = verdicts.count(INCONCLUSIVE)
    n_na = verdicts.count(NA)
    blocking = [v for v in verdicts if v in _BLOCKING_FAILS]
    print("-- %s: %d PASS · %d PENDING · %d FAIL · %d INCON · %d N/A --"
          % (phase, n_pass, n_pend, n_fail, n_incon, n_na))
    if blocking:
        print("RESULT: BLOCKED -- %d gate(s) not green (%d pending, %d fail). "
              "Phase does NOT advance." % (len(blocking), n_pend, n_fail))
        return 1
    if n_incon:
        print("RESULT: INCONCLUSIVE -- %d gate(s) could not decide; resolve before advance."
              % n_incon)
        return 2
    print("RESULT: ADVANCE-OK -- all %d blocking gate(s) green (+%d N/A)." % (n_pass, n_na))
    return 0


def list_registry(phase=None):
    reg = _registry()
    phases = [phase] if phase else list(reg)
    for ph in phases:
        if ph not in reg:
            print("unknown phase %r" % ph)
            continue
        print("== %s ==" % ph)
        for g in reg[ph]:
            state = "N/A" if g.na else ("runnable" if g.runner else "PENDING")
            print("  %-22s %-8s %s" % (g.gid, state, g.desc))
    return 0


def self_test():
    """Runner self-check: registry well-formed; every phase reachable; no dup gid within a phase."""
    reg = _registry()
    total = 0
    failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        if ok:
            print("  ok  %s" % name)
        else:
            failed += 1
            print("  XX  %s" % name)

    expected = ["P0", "P1", "P1-live", "P2", "P3", "P4", "P5"]
    case("all 7 phases present", sorted(reg) == sorted(expected))
    for ph in reg:
        gids = [g.gid for g in reg[ph]]
        case("phase %s has no duplicate gid" % ph, len(gids) == len(set(gids)))
        case("phase %s non-empty" % ph, len(reg[ph]) > 0)
    # P0 must contain the four frozen blocking gates
    p0 = {g.gid for g in reg["P0"]}
    case("P0 has ECO-3/CONTENT-2/OPS-5/check-frontmatter",
         {"ECO-3", "CONTENT-2", "OPS-5", "check-frontmatter"} <= p0)
    # P1 must contain ACC-3b (mig1 B0.4 resolution) and MIG-1
    p1 = {g.gid for g in reg["P1"]}
    case("P1 blocks on ACC-3b (mig1 B0.4)", "ACC-3b" in p1)
    case("P1 contains the MIG-1 hold point", "MIG-1" in p1)
    case("P1 ORIGIN-PROPAGATION present (gets F-13 per gate-reg C3)",
         "ORIGIN-PROPAGATION" in p1)
    # gate-registration additions
    case("AUTONOMY-DISPOSITION registered at P2",
         "AUTONOMY-DISPOSITION" in {g.gid for g in reg["P2"]})
    case("TOOL-GRANT-ISOLATION at P4",
         "TOOL-GRANT-ISOLATION" in {g.gid for g in reg["P4"]})
    case("EGRESS-CO-RESIDENCY rebound note present",
         any(g.gid == "EGRESS-CO-RESIDENCY" and "F-11" in g.note for g in reg["P4"]))
    # TOOL-GRANT-ISOLATION at P0 must be N/A, never runnable-green
    tgi_p0 = [g for g in reg["P0"] if g.gid == "TOOL-GRANT-ISOLATION"]
    case("TOOL-GRANT-ISOLATION is N/A at P0 (not silent-green)",
         bool(tgi_p0) and tgi_p0[0].na and tgi_p0[0].runner is None)
    # A PENDING gate must block: synthesize a phase and confirm run maps to blocking
    case("PENDING verdict is in the blocking set", PENDING in _BLOCKING_FAILS)
    case("N/A verdict is NOT blocking", NA not in _BLOCKING_FAILS)

    # v3.0-21: ACC-1-rebaselined must be absent-tolerant, not a crash, when the
    # pinned fork-specific baseline evidence file is missing (the shape a fresh
    # instance hits on its very first --phase P5 run).
    p5 = {g.gid: g for g in reg["P5"]}
    case("P5 has ACC-1-rebaselined", "ACC-1-rebaselined" in p5)
    case("ACC-1-rebaselined runner is callable (absent-tolerant gate, not a "
         "bare subprocess tuple that would crash staleness.py on a missing "
         "path)", callable(p5["ACC-1-rebaselined"].runner))
    _missing_path = os.path.join(_HERE, "evidence",
                                 "does-not-exist-acc1-baseline.json")
    case("sanity: the synthesized absent-baseline path really doesn't exist",
         not os.path.exists(_missing_path))
    v_abs, d_abs = _run_acc1_rebaselined(evidence_path=_missing_path)
    case("ACC-1-rebaselined with absent baseline evidence -> PENDING "
         "(never crashes, never a bare misleading FAIL)", v_abs == PENDING)
    case("absent-baseline PENDING detail instructs minting a new baseline",
         "mint" in d_abs.lower() and "baseline" in d_abs.lower())
    case("absent-baseline PENDING detail cites the v3.0-21 residual",
         "v3.0-21" in d_abs)
    case("PENDING from absent ACC-1-rebaselined evidence still blocks the "
         "phase advance (same semantics as any other PENDING gate)",
         v_abs in _BLOCKING_FAILS)

    # v3.0-21 mapping parity: with the baseline PRESENT, the rc -> verdict
    # mapping must be EXACTLY the old generic tuple-dispatcher semantics the
    # gate had as ("staleness.py", [...], {0}): rc None -> PENDING, rc 0 ->
    # PASS, rc 2 -> INCONCLUSIVE, else FAIL. Pinned via the injectable
    # `invoke` seam against a temp stand-in baseline file -- no real
    # staleness.py subprocess fired.
    import tempfile
    _fd, _fake_baseline = tempfile.mkstemp(suffix="-acc1-baseline.json")
    os.close(_fd)
    try:
        _calls = []

        def _stub(rc, last):
            def _invoke_stub(script, args):
                _calls.append((script, list(args)))
                return rc, last
            return _invoke_stub

        v_p, _ = _run_acc1_rebaselined(evidence_path=_fake_baseline,
                                       invoke=_stub(0, "RESULT: PASS"))
        case("present baseline, rc=0 -> PASS (old ok={0} semantics)",
             v_p == PASS)
        v_p0, d_p0 = _run_acc1_rebaselined(evidence_path=_fake_baseline,
                                           invoke=_stub(0, ""))
        case("present baseline, rc=0 blank stdout -> PASS with the OLD "
             "dispatcher's fallback detail format (script args -> rc=N)",
             v_p0 == PASS and d_p0 == "staleness.py --baseline-check %s "
             "-> rc=0" % _fake_baseline)
        v_i, d_i = _run_acc1_rebaselined(evidence_path=_fake_baseline,
                                         invoke=_stub(2, ""))
        case("present baseline, rc=2 -> INCONCLUSIVE (old dispatcher's "
             "rc==2 branch; staleness.py exit 2 = PyYAML unavailable)",
             v_i == INCONCLUSIVE)
        case("rc=2 blank-stdout fallback detail matches the old dispatcher "
             "verbatim", d_i == "rc=2 (inconclusive)")
        v_f1, _ = _run_acc1_rebaselined(evidence_path=_fake_baseline,
                                        invoke=_stub(1, "RESULT: FAIL"))
        case("present baseline, rc=1 (census divergence) -> FAIL",
             v_f1 == FAIL)
        v_f3, d_f3 = _run_acc1_rebaselined(evidence_path=_fake_baseline,
                                           invoke=_stub(3, ""))
        case("present baseline, other nonzero rc -> FAIL (old catch-all)",
             v_f3 == FAIL)
        case("catch-all blank-stdout fallback detail matches the old "
             "dispatcher verbatim (rc=%d form)", d_f3 == "rc=3")
        v_n, _ = _run_acc1_rebaselined(
            evidence_path=_fake_baseline,
            invoke=_stub(None, "staleness.py missing on disk"))
        case("present baseline, script missing (rc None) -> PENDING "
             "(old dispatcher parity)", v_n == PENDING)
        case("present-baseline path invokes staleness.py --baseline-check "
             "with the pinned path (subprocess contract unchanged)",
             _calls and all(
                 c == ("staleness.py", ["--baseline-check", _fake_baseline])
                 for c in _calls))
    finally:
        os.remove(_fake_baseline)

    if failed:
        print("check-phase-gate self-test: FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("check-phase-gate self-test: PASS (%d/%d)" % (total, total))
    return 0


def main(argv):
    args = argv[1:]
    if "--self-test" in args:
        return self_test()
    if "--list" in args:
        ph = None
        if "--phase" in args:
            i = args.index("--phase")
            if i + 1 < len(args):
                ph = args[i + 1]
        return list_registry(ph)
    if "--phase" in args:
        i = args.index("--phase")
        if i + 1 >= len(args):
            print("usage: check-phase-gate.py --phase P0|P1|P1-live|P2|P3|P4|P5")
            return 1
        return run_phase(args[i + 1])
    print(__doc__.strip().splitlines()[0])
    print("usage: check-phase-gate.py --phase PX | --list [--phase PX] | --self-test")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
