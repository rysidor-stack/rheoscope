<!-- Ported verbatim 2026-07-11 from the dogfood proving-ground fork, where the
     memory engine was built and proven. The fork remains the proving ground; template-bound
     updates flow fork -> template.
     [2026-08-01, backlog v3.0-89 operator ruling: venture identifiers genericized for template
      publication; the byte-verbatim certified original is preserved in the dev repo at
      capabilities/knowledge-os/design-history/certified-originals/ and in dev-repo git history.] -->

# Memory Engine v3.0 — Verification & Test Plan

Companion to `memory-engine-v3-spec.md` (now **v3.2**) and `memory-engine-v3-verification-brief.md`; build-ready test specifications. Authored 2026-06-11. **Amended 2026-06-11 to align with spec v3.1** (cross-vendor verification gate results applied per `memory-engine-v3-verification-results.md` §6): ordered conservation decision table, no-op scope in ACC-4/LLM-2, full checkpoint state in ACC-6, legacy-assumed seeding in GOLD-1, and six new tests (ACC-3b, OPS-4, OPS-5, OPS-6, LLM-5, LLM-6, ECO-7). **Amended 2026-06-11 to align with spec v3.2** (scoped re-pass findings F1–F18 applied per `memory-engine-v3.1-scoped-reverify-results.md`): REF-SKIPPED to priority 1 (F1); checkpoint disposition fields (F3); LOCK-COMMON-DIR prev_record_hash chain (F4); sidecar quarantine recovery (F5); conservative event_class default (F6); taint quarantine by task type (F8); new ORIGIN-PROPAGATION test (F9); OPS-5 NTFS/junction fixtures (F10); new EGRESS-CO-RESIDENCY test (F11); consumed_status in derivation block (F12); content audit under VERIFY harness (F13); absorbed-without-source on audit ladder (F14); standalone routing census (F15); verbatim support_lines (F16); substrate fields from invocation metadata, vendor/model_id separation (F17/F18).

This plan's gates define what "proven" means for the v3.0 fork before any live-wiki cutover: no migration phase advances and no cutover proceeds without the applicable gates listed here passing with evidence.

---

## 0. House conventions every new test follows (verified against the live repo)

These are extracted from `deploy/check-frontmatter.py`, `deploy/check-loop-state.py`, `deploy/check-reference-integrity.py`, `deploy/compile-preflight.py`, `deploy/compile-review-sweep.py`, and `.claude/hooks/`:

1. **Exit codes:** `0` PASS, `1` FAIL (violations, each printed two-space-indented), `2` INCONCLUSIVE (could not run — e.g., PyYAML missing). Final line always `RESULT: PASS|FAIL|INCONCLUSIVE — <summary>`. (`deploy/README.md` § Sensors; modeled in all three `check-*.py`.)
2. **`--self-test` flag** validates committed fixtures and never touches the live tree (model: `check-loop-state.py run_self_test`, fixtures `deploy/test-fixtures/loop-state/{handoff,dispatch}-{valid,invalid}.yaml`, each invalid fixture carrying a header comment enumerating the violations it must trip).
3. **Fixture layout:** new fixtures under `deploy/test-fixtures/memory-engine/` (subdirs `receipts/ events/ views/ entities/ descriptors/ consumed-sets/`). Naming `<thing>-<expectation>.{md,yaml,json}`; invalid fixtures state their expected violations in a leading comment.
4. **Sensors are detect-only.** Repair is a human/orchestrator action. Report-vs-write split where a script must write (`--report` JSON default, `--write` opt-in — model: `compile-review-sweep.py`).
5. **Delegation pattern:** `compile-preflight.py` shells to sub-sensors and embeds their JSON (`review()` → `compile-review-sweep.py --report`). New per-compile facts ride preflight via the same pattern; preflight itself stays exit-0-with-degraded-sections, the standalone sensor is what gates.
6. **Windows reality (all verified):** console is cp1252 and live receipts contain `③④` — every script starts with `sys.stdout.reconfigure(encoding="utf-8")`; file reads use `encoding="utf-8-sig"` (BOM reality); `.gitattributes` is `* text=auto` with `core.autocrlf=true` so working-tree text is CRLF, index is LF — any byte-denominated measurement must define normalization (see ECO-3); PID liveness has no POSIX `kill -0` — use `ctypes.windll.kernel32.OpenProcess` or `tasklist /FI "PID eq N"`.
7. **Drills run in worktrees, never the live tree.** Multiple operator sessions demonstrably share this tree (current `git status` shows ~50 unrelated dirty/untracked paths). Drill scripts `deploy/drill-<name>.py` do: `git worktree add ../<wiki>-drill-<name> HEAD` → operate → assert → `git worktree remove --force`. Exit codes as above. A drill that finds the worktree dir already present exits 2 (stale prior drill — manual cleanup).
8. **Hooks** remain reserved for the named-risk perimeter (stdin JSON, exit 2 blocks; fixtures `.claude/hooks/test-inputs/test-<x>-<desc>.json`). The engine's commit gates live in preflight/sensors, **not** PreToolUse hooks — a PreToolUse hook sees tool input, not file semantics, and the CLAUDE.md rule ("every hook needs a named risk") forbids vibes-hooks.

**Real fixture material discovered (use these, don't invent):**

| Artifact | Use |
|---|---|
| `receipts/2026-04-21T061500-phase-3-restructure.md` — no frontmatter at all (starts `# Phase 3 …`) | journal-hole archetype 1 |
| `receipts/2026-05-27T200000-compile.md` — opening `---` fence never closes (291 lines, fence only at line 0) | journal-hole archetype 2 |
| `receipts/2026-06-10T235900-compile.md` — ScannerError "mapping values are not allowed here" line 22 col 61 (`operation: updated (corpus reconcile — Layer 1 Dashboard: Session ③ …)` — unquoted colon) | journal-hole archetype 3 |
| 267 `raw/ → wiki/` pairs in 44 articles' `sources:` frontmatter (all currently YAML-clean) | P1 seeding ground truth |
| `receipts/2026-06-11T074500-compile.md` — documents 2 unprocessed raws + 6 partial candidates with full genuine/prose-only adjudication, plus a real `parse_ok=false` degradation note | golden-routing answer key + hole-handling precedent |
| `raw/2026-06-10-ryan-bookings-entity-schema.md` — cites `schema-foundations.md` "lines 1622 and 661" | real line-number-citation breakage case for split tests |
| `wiki/systems/schema-foundations.md` = 308,020 B; `wiki/flight-plans/plan-4.7-schema-sequence.md` = 175,136 B | P0 cap/split fixtures |
| 2026-04-27→06-09 formatter incident, repaired `2c3b6ac` + body pass `ecdeef9`; FLATTEN signature = ≥2 known keys joined per line | formatter drill script |

---

## 1. Test catalog

Format per test — **Property** (the invariant, quantified) / **Mechanics** (fixtures, where it runs) / **Pass** / **Class** (A–E) / **Gates** (when).

### Family ACC — absorption accounting (failure class A)

#### ACC-1 · CONSERVATION-TRIPWIRE (the no-silent-loss property)

**Property.** Let L = the event ledger (today: `raw/*.md`; at P5 also receipts and handoff locks as typed events). Let J = the RUN JOURNAL = ⋃ over **parseable** receipts of recorded pairs (view V, event E) including explicit no-ops (a no-op with reason **is** a journal entry). The RUN JOURNAL is a machine-written, schema-validated record stored at `receipts/journal/<seq>.json` — absorption state lives there, NOT in receipt prose. Let M(E) = the set of views matching E (subscribes.entities match ∪ `cascades_to` naming). Then every E ∈ L resolves to **exactly one** class under the following **ordered decision table (first-match-wins; classes are disjoint by construction)**:

| Priority | Class | Condition |
|---|---|---|
| 1 | `REF-SKIPPED` | Event frontmatter carries `source: ref` (existing preflight class) — skipped regardless of entity match. Must run first: a ref event with M(E) ≠ ∅ would otherwise age to PENDING forever and cause permanent rebuild churn; a ref with M(E) = ∅ would flood the unrouted queue. |
| 2 | `SUPERSEDED-UNCONSUMED` | A later event's `supersedes:` targets E AND E was never absorbed; report carries the superseder's id. |
| 3 | `CONSUMED` | M(E) ≠ ∅ AND ∀V ∈ M(E): (V,E) ∈ J (all matched views have absorbed E). |
| 4 | `PENDING_NOOP_CANDIDATE` | M(E) ≠ ∅ AND ∃V ∈ M(E) with (V,E) journaled as `no-op` pending verification (T1 / correction / lock / `informed_by` event not yet verifier-confirmed). |
| 5 | `PENDING` | M(E) ≠ ∅ AND ∃V ∈ M(E) with (V,E) ∉ J; reported with event age. Age > 14 days prints a warning (mirrors `PARTIAL_CANDIDATE_DAYS`). |
| 6 | `UNROUTED` | M(E) = ∅; must appear verbatim in the unrouted-queue output. |
| 7 | `RESIDUE` | Member of the frozen pre-engine list `deploy/test-fixtures/memory-engine/consumed-sets/residue-p1.yaml` (committed at P1; see MIG-1). |

This ordered table **is the CONSERVATION INVARIANT**: every ledger event resolves to exactly one class; apparent overlaps are resolved by priority order, never by ad-hoc logic. An event resolving to zero classes is a FAIL, full stop.

Side conditions: (i) RESIDUE is frozen — any event classed RESIDUE that is not in the committed snapshot is a FAIL (the list may only shrink); (ii) ∀(V,E) ∈ J: V resolves to an existing view file and E to an existing ledger file (no orphan journal claims); (iii) multi-view consumption is normal — "exactly one" quantifies over classes, never view count; (iv) residue snapshots containing events with `source: ref`, events already-consumed, or events already in `SUPERSEDED-UNCONSUMED` are **rejected at snapshot-write time** (a snapshot admitting such events is itself a FAIL).

**Mechanics.** Live tripwire inside `staleness.py` (default mode prints the class census; `--report` emits JSON consumed by `compile-preflight.py` via the delegation pattern). Pure-function core `classify(ledger, journal, matches, residue, holes) -> dict[event, class]` unit-tested against `deploy/test-fixtures/memory-engine/events/` (one fixture per class, including `event-unroutable.md` and `event-superseding.md` synthetics) via `staleness.py --self-test`.

**Pass.** Exit 0 iff partition total+disjoint, residue frozen, no orphan claims, and no **new** journal hole (ACC-2). PENDING is not a failure — it is the work queue — but PENDING age > 14 days prints a warning line (mirrors `PARTIAL_CANDIDATE_DAYS`).

**Class.** A (all sub-modes: unseen-marked-absorbed, seeding over-claims, drift). **Gates.** Every staleness run = every compile (preflight) and every `/flight-plan` (session start), forever. Self-test gates pre-merge of any `staleness.py` change.

#### ACC-2 · JOURNAL-HOLE-INJECTION (unparseable receipts)

**Property.** For every receipt R that fails whole-block YAML parse, `staleness.py` (a) does not crash, (b) treats R's absorptions as **absent** — any event only absorbed per R reverts to PENDING and is re-presented (the conservative direction is always re-present, never assume-absorbed), (c) names R in a `journal_holes` report section, (d) exits 1 iff R is not on the known-hole allowlist.

**Mechanics.** Three fixtures modeled byte-for-byte on the real failures: `receipts/hole-no-frontmatter.md`, `hole-unclosed-fence.md`, `hole-unquoted-colon.md` (the line-22 ScannerError shape, em-dash and `③` characters included — they also smoke-test the cp1252 trap). Allowlist = a `known_holes:` list in `deploy/entities.yaml` (or a sibling governed file), seeded with the 3 historic receipts, extended only with named entries (the `LEGACY_LENIENT_FOLDERS` pattern from `check-loop-state.py` — explicit, never wildcarded). Unit: `--self-test` feeds each fixture through the journal parser and asserts hole classification + the re-present behavior on a 2-event mini-ledger. Integration: run against the live repo — must report exactly the 3 known holes at exit 0.

**Pass.** Self-test: 3/3 holes detected, absorptions dropped, mini-ledger event re-presented. Live: known holes → NOTE lines + exit 0; injected fourth hole (drill copy) → exit 1.

**Class.** A (absorption-ledger hole). **Gates.** Pre-merge of engine code; live behavior on every staleness run.

#### ACC-3 · JOURNAL-COHERENCE (journal ↔ frontmatter ↔ commit diff)

**Property.** For every view V under `wiki/` (projections INDEX/HEALTH/REVIEW excluded): (1) V carries a parseable derivation block with `schema_version` = current and legal `type`/`status`/`tier`; (2) V's pins equal the latest `pins_granted` recorded for V in the RUN JOURNAL (`receipts/journal/<seq>.json`); (3) V's `last_run` stamp equals max(run id naming V in the RUN JOURNAL) — **this is the contract that makes both git-restore rollback and journal-before-content writes detectable** (a view whose `last_run` lags the journal was rolled back or never written; the test imposes the `last_run` stamp as an engine requirement); (4) every `status: superseded` view's stub redirect target exists; (5) inverse of ACC-1(ii): every view file has ≥1 journal lineage or is flagged `never-absorbed` (legal for P1-backfilled views until their first v2 compile, allowlisted by the P1 receipt); (6) `origin_max(V) == max(canonical origin of V's consumed events)` — the derivation block's `origin_max` field must equal the most restrictive canonical `origin` recorded for the events the journal has consumed into V (checked via the routing-registration record; `unknown` ≥ `external-scrape` in restrictiveness ordering; a mismatch is a FAIL naming the event whose registration diverges).

**Mechanics.** New sensor `deploy/check-derivation.py` (or a `--check` mode of `staleness.py` — implementer's choice, one binary decision: keep it a separate file to match the `check-*` family naming). Fixtures: `views/view-v2-valid.md`, `views/view-rolled-back.md` (last_run older than journal), `receipts/journal-claims-unwritten-view.md`. `--self-test` per convention.

**Pass.** Exit 0 on live repo post-P1; each invalid fixture trips its named violation.

**Class.** A (journal-vs-frontmatter drift) + D (git-restore rollback). **Gates.** Every compile (preflight delegation) + flight-plan; self-test pre-merge.

#### ACC-3b · JOURNAL-SIDECAR-INJECTION (JSON-substrate corruption; EXTENDS ACC-2's substrate scope)

ACC-2 exercises legacy prose receipt holes. This test covers the **new JSON sidecar substrate's own failure modes** — distinct corruption vectors that prose-hole tests cannot reach. ACC-2's legacy-prose-hole fixtures are not replaced; they remain in force. This test adds the JSON layer.

**Property.** For each of the following injected sidecar defects, `staleness.py` (a) does not crash, (b) fails LOUD by filename (the offending file named in the error output), and (c) never degrades to "consumed nothing" (partial-parse silently clearing absorptions is a FAIL). Additionally: a valid prose receipt with a companion JSON sidecar must not lose the sidecar's absorptions due to a prose hole in the receipt — the sidecar absorptions survive independently of receipt YAML parseability.

Defect inventory (one fixture per row, placed under `deploy/test-fixtures/memory-engine/receipts/sidecars/`):

| Fixture file | Defect |
|---|---|
| `sidecar-malformed-json.json` | Not valid JSON (truncated mid-object) |
| `sidecar-truncated-json.json` | Valid JSON prefix but object not closed |
| `sidecar-seq-mismatch.json` | `seq` field does not match the numeric filename (e.g., file `0042.json`, `seq: 99`) |
| `sidecar-dup-seq.json` + `sidecar-dup-seq-2.json` | Two sidecars with the same `seq` value |
| `sidecar-dup-view-event-conflict.json` | Duplicate `(view, event)` pair with conflicting `disposition` values (e.g., first `absorbed`, second `no-op`) |
| `sidecar-without-receipt.json` | Sidecar exists with no matching `.md` receipt file |
| `receipt-without-sidecar.md` | Receipt frontmatter references a `journal_seq` but the `.json` sidecar is absent |
| `sidecar-hostile-path.json` | `view` or `event` field contains a path traversal (`../`, absolute path, or symlink) |
| `sidecar-unknown-field.json` | Top-level field not in the schema (`"__proto__": {}` included) |
| `sidecar-invalid-correction-target.json` | `corrections` entry whose `targets` field resolves to a non-existent view |
| `sidecar-schema-version-mismatch.json` | `schema_version` field does not match the tooling's supported version |

**Mechanics.** `staleness.py --self-test` feeds each fixture through the journal parser and asserts: (1) the named violation is reported; (2) exit 1 (not 0, not 2); (3) no partial absorption leaks through. The prose-sidecar independence test: pair `hole-unclosed-fence.md` (from ACC-2) with a valid `0000-valid.json` sidecar; assert the sidecar absorptions appear in the consumed-set even though the prose receipt is a known hole.

**Recovery case (F5).** A malformed sidecar whose filename is entered into the `journal_quarantine:` named-allowlist (following the `LEGACY_LENIENT_FOLDERS` pattern — each entry requires an operator decision raw file naming the seq and the reason) must: (a) suppress the exit-1 on that seq alone; (b) revert the quarantined seq's absorptions to PENDING (conservative direction — the ACC-2 known-holes behavior applied to JSON substrate); (c) NOT admit any seq to the allowlist without a corresponding operator decision raw file (an entry without a raw file backing is itself a FAIL on `--self-test`). No ad-hoc deletion of append-only history is ever the recovery path. Fixture: `sidecar-quarantined.json` in the defect set, entered into a fixture allowlist with a fixture operator decision file; assert exit 0 + absorptions reverted to PENDING.

**Pass.** 11/11 defects detected LOUD by filename; no crash; no silent consumed-nothing degradation; sidecar absorptions survive a prose-hole companion receipt; quarantined sidecar reverts absorptions to PENDING at exit 0; allowlist entry without backing raw file trips FAIL.

**Class.** A (journal-spine corruption via JSON substrate). **Gates.** Pre-merge of journal sidecar code (P2 entry); live behavior on every staleness run from P2 on.

#### ACC-4 · MANIFEST-VS-DIFF (journal-before-content made detectable; no-ops in scope)

**Property.** For the receipt of run R committed in commit C: (a) every (V, E) in R's RUN JOURNAL (`receipts/journal/<seq>.json`) with disposition ≠ no-op ⇒ C's diff touches V's file; conversely every wiki view file touched in C appears in R's manifest (as absorb, rebuild, split, or projection-regeneration). A receipt claiming an absorption whose commit diff never touched the view = an ordering violation or fabrication; a touched view absent from the manifest = silent absorption. (b) **No-ops are in scope.** Every journaled no-op entry must carry a verifier artifact reference + packet hash; the justification must cite the full event body AND the full view body (not an empty diff). A no-op entry missing these fields is a FAIL. For T1 / correction / lock / `informed_by` events, the verifier artifact must have been produced in the same run (same `run` field in `<seq>.json`), never left to weekly sampling; a T1 no-op whose `verified` timestamp does not fall in the current run's window is a FAIL. (c) **Conservative `event_class` default (F6).** An event whose `event_class` was judgment-assigned (i.e. the event's registration record carries `class_source: judgment` — the ~17% YAML-hostile case where frontmatter could not be parsed) is treated as lock-class for no-op purposes: its no-ops are always routed to `PENDING_NOOP_CANDIDATE`, never `CONSUMED`, regardless of the assigned class value. This mirrors the `origin: unknown` conservative default and prevents a misclassified lock event from being silently consumed through the no-op path. Fixture: a YAML-hostile event whose frontmatter contains an unclosed YAML fence (the `hole-unclosed-fence` archetype), registered `event_class: observation` by backfill, with a journal record journaling a no-op against `wiki/systems/schema-foundations.md`; the test asserts the no-op routes to `PENDING_NOOP_CANDIDATE` (not `CONSUMED`) because `class_source: judgment`.

**Mechanics.** Post-commit tripwire `deploy/check-run-diff.py`: locate the newest receipt, find its commit (`git log --diff-filter=A -- receipts/<file>`), compare `git show --name-only <C>` against the manifest. Both directions, set equality modulo a declared `projection_paths` exception list (INDEX/HEALTH/briefing/changelog — regenerations may be summarized as one manifest line). No-op audit: for each `noop_candidates` entry in `<seq>.json`, assert `view_hash` is non-empty, `verified` is `true`, and `artifact` resolves to an existing receipts path. Unit fixtures: a fabricated-claim receipt + a stowaway-file commit (simulated via static file lists, no git needed in self-test mode) + a no-op-missing-artifact fixture + a T1-no-op-unverified-same-run fixture.

**Pass.** Live: exit 0 for the latest compile commit. Fixtures: both manifest violation directions caught; no-op-missing-artifact trips; T1-no-op-unverified trips.

**Class.** A (silent absorption; ordering; unverified no-ops) + C (content lies — fabricated claims have a mechanical floor). **Gates.** Every compile, as the final step before push (the compile skill's Step-10 analog); pre-merge self-test.

#### ACC-5 · REPLAY-COST-BENCHMARK

**Property.** Full RUN JOURNAL replay (parse all receipts → consumed-sets → conservation census) completes in **< 2 s wall at the current 96 receipts and < 10 s at a synthetic 1,000-receipt corpus**, and scales ~linearly: t(1000)/t(100) ≤ 15.

Why 10 s: `staleness.py` runs at **every session start** (flight-plan) and **every compile**; a sensor slower than ~10 s gets skipped by agents under pressure (the repo's own "noisy sensor gets muted" lesson generalizes to slow), and 10 s keeps 12× headroom under the 120 s default Bash timeout orchestrators run it with. Why the ratio test: it rejects accidental O(n²) (re-scanning wiki per receipt) without requiring a fragile absolute number on varying hardware.

**Mechanics.** `deploy/drill-replay-bench.py`: generates 1,000 synthetic receipts into a temp dir (template = the healthy `2026-06-11T074500-compile.md` frontmatter, randomized view/event names drawn from the real catalog; ~6 KB each ≈ 6 MB total, matching the real 607 KB×10 corpus shape); inserts the 3 hole archetypes every 100 receipts (holes must not break scaling); times replay at n=100/300/1000 via `time.perf_counter`, 3 repetitions, median.

**Pass.** Both absolute budgets + ratio bound met. FAIL prints the timing table.

**Class.** A (replay cost scaling). **Gates.** Pre-merge of any `staleness.py` change touching the replay loop; re-run at P2 acceptance and whenever real receipt count crosses 200/500/1,000.

#### ACC-6 · SNAPSHOT-EQUIVALENCE (conditional — only if checkpointing is adopted)

**Property.** For every prefix split point k of the receipt sequence: `replay(receipts[0..n]) ≡ replay_from(snapshot(receipts[0..k]), receipts[k+1..n])` — the equivalence test compares the **entire deterministic staleness state**, not a subset. The checkpoint must serialize ALL of: consumed pairs **with their per-`(event,view)` consumption DISPOSITION CLASS (`verified-consumed` | `legacy-assumed` | `absorbed-without-source`) and CONTENT-AUDIT STATUS**, no-op candidates, pins, registrations, `origin`/taint, cascade targets, supersession chains, correction-target map, **citation-translation records**, hole list, and schema version. Any checkpoint that omits one of these fields and produces a divergent result on the delta-replay side is a FAIL — the equivalence property only holds when the serialized state is complete. A checkpoint serializing bare `(event,view)` pairs without disposition class or content-audit status is a FAIL regardless of replay equivalence (the stripped state enables assemble.py to serve an audit-pending T1 view as if verified). Snapshot staleness is self-announcing: a snapshot whose embedded last-receipt-id is not an ancestor of the receipt list (receipt renamed/removed) forces full replay, never a silent partial.

**Mechanics.** Unit-level: run on the golden-10 mini-corpus with k ∈ {0, 3, 7, 10}; integration: live repo with k = n−5. The comparison is byte-identical JSON dumps (sorted keys) over the complete state object — a partial comparison (e.g., only consumed-sets + pins + queue + holes) is insufficient and the test setup must verify the serialized field list matches the spec §6 enumeration (which explicitly includes disposition class, content-audit status, and citation-translation records). Adoption trigger (designed now, built later): adopt checkpointing only when ACC-5's live time exceeds 5 s.

**Pass.** Byte-identical JSON dumps (sorted keys) of the full state object — full replay vs snapshot+delta — for every k.

**Class.** A. **Gates.** Pre-merge of the checkpoint feature; thereafter weekly with the drill batch.

### Family GOLD — replay grounding (required items 1, plus the decision)

#### GOLD-1 · FULL-HISTORY ACCOUNTING REPLAY — and the design decision

**Decision: hybrid. Full replay of the deterministic accounting layers over all 69 events × 96 receipts; a frozen 10-event golden subset for routing semantics; no LLM-layer replay at all.** Justification: (a) historic compiles ran the *old* three-tier tag-routing spec — replaying routing over 69 events and diffing against the current wiki would diff two different specifications and produce noise dressed as signal; (b) prose is non-reproducible, so "compare against the current wiki" is only meaningful for set-valued artifacts; (c) the deterministic layers (journal replay, consumed-sets, staleness verdicts, conservation census, catalog) are pure functions and replay exactly, at negligible cost — those are the layers where accounting lies (class A) live, which is what replay is for. LLM routing/merge quality is tested where it can be measured: planted defects (LLM-1) and golden routing (GOLD-2).

**Property.** Engine-computed seeded consumed-sets over the full real history agree with the two historical signals as follows: (1) **100%** of the 267 `sources:` pairs (44 articles) appear in the seeded consumed-sets — `sources:` is authoritative by the completion rule ("only add a source if the article was actually updated", compile SKILL Step 3); any seeded-set missing a sources-pair is a FAIL with no triage escape. (2) Of the receipts-history cross-product (raw_inputs × articles_modified per receipt), after excluding projection paths (the existing `NEVER_SOURCE_TARGETS` regex: `INDEX|REVIEW|HEALTH`), `infrastructure_modified` entries, and the 3 hole receipts: **≥ 90%** of pairs appear in the seeded sets. (3) Every residual disagreement is triaged into exactly one named bucket: `receipt-overclaim` (receipt listed an article the raw never fed — expected; receipts batch INDEX-adjacent edits), `cascade-completed-later` (sources-pair added by a later run than the receipt), `hole-shadow` (pair attested only by an unparseable receipt), `absorbed-without-source` (a genuine historical merge confirmed to exist but absent from `sources:` — injected into the initial checkpoint as `CONSUMED` so it does not storm-rebuild on first run). The triage output **is** the P1 residue list input (MIG-1).

Seeding assertions are `legacy-assumed`, NOT `verified-consumed`. The seeded pairs declare that the article was updated from the event — they do not certify that the article's prose currently carries the event's load-bearing claims. The distinction is mechanically enforced: the journal's `legacy-assumed` flag causes `assemble.py` to treat the view as **unverified for build/fix** until the content audit (below) passes. **`absorbed-without-source` pairs carry the SAME `legacy-assumed` obligation (F14).** They are NOT injected as `CONSUMED` — they are seeded as `legacy-assumed` with the same `(event,view)` content-audit requirement for T1/correction/lock views. The inverted trust ladder (sourceless pairs at higher effective trust than sourced pairs, bypassing the audit gate) is a silent-loss fatal and is explicitly forbidden by this property.

Residue is keyed at `(event, view, obligation_reason)` level, not event level — a single event may generate separate residue entries per view obligation (reflecting G5). Each entry in `residue-p1.yaml` carries `{event, view, obligation_reason, reason-bucket, disposition}`.

**Content-audit gate (T1 / correction / lock views).** Before a view whose consumed-set is `legacy-assumed` or `absorbed-without-source` is served to a build/fix task, a one-time content audit must confirm the event's load-bearing claims actually appear in the view (event claims → view hunks check). Until this audit passes, the view is gated as `audit-pending`. **`consumed_status` is stamped into the derivation block at P1** (`verified-consumed` | `legacy-assumed` | `audit-pending`) — reader-visible in the frontmatter from day one, not deferred to `assemble.py` (P4). `check-derivation.py` reports `audit-pending` T1 views in the flight-plan sweep; the dispatch-hub skill gains the rule: no build/fix dispatch against an `audit-pending` T1 view. The GOLD-1 drill reports the audit-pending count per view category; the P1 residue snapshot records audit status per `(event, view)` pair.

**Mechanics.** Repo-level integration script `deploy/drill-golden-replay.py`, read-only over the live tree (no worktree needed — it writes nothing). Emits the agreement table + per-bucket counts as JSON. The expected-agreement JSON freezes as `consumed-sets/p1-seed-expected.json` after MIG-1 review, turning later runs into regression checks.

**Pass.** 100% / ≥90% / zero untriaged disagreements; every seeded pair asserting `legacy-assumed` (not `verified-consumed`); `absorbed-without-source` entries injected as CONSUMED; content-audit-pending count reported per view tier.

**Class.** A (migration seeding declares unconsumed history absorbed). **Gates.** P1 dry-run (MIG-1, blocking) and re-run at P2/P5 phase advances.

#### GOLD-2 · GOLDEN-10 ROUTING SUBSET (frozen, hand-verified)

**Property.** For each of 10 frozen events, the engine's **deterministic** routing output (normalized entities → matched view set → staleness verdicts → conservation class) equals the hand-authored `expected.yaml` exactly.

**Mechanics.** Copies (fixtures, not intake — `raw/` immutability untouched) under `events/golden-10/` + `expected.yaml`. The 8 real picks, chosen for class coverage, with the answer key grounded in the documented adjudication of `receipts/2026-06-11T074500-compile.md`:

1. `2026-06-10-ryan-disconnect-mkdocs.md` — explicit cascade targets (3 named; all satisfied per receipt).
2. `2026-06-10-ryan-backend-split-hub-fired-legs.md` — legal no-op (its own frontmatter declares no wiki cascade).
3. `2026-06-10-ryan-bookings-entity-schema.md` — T1 lock, `informed_by`, 19 tags; expected: genuine cascade to schema-customers (customer_id NULL correction), sources-link to schema-work-orders (trigger #11), primary to schema-bookings.
4. `2026-06-05-ryan-products-spike-bigint-correction.md` — correction class; sources-link-only to schema-foundations.
5. `2026-06-09-ryan-work-orders-entity-schema.md` — entity-group lock later partially superseded-in-effect by event 3 (trigger-11 discharge) — exercises supersedes/cascade interplay.
6. `2026-06-08-ryan-decision-12-gateway-write-path.md` — decision lock.
7. One 2026-04-08/10-era file (pick at freeze time; criterion: tags predating the current vocabulary) — vocabulary aging.
8. One `source: ref` file whose tags match an existing entity (criterion-pick; synthesize if none exists) — `REF-SKIPPED` class (priority 1 in the ordered table; expected.yaml must assert `conservation_class: REF-SKIPPED` despite M(E) ≠ ∅, confirming that priority 1 fires before any entity-match path).
9. Synthetic `event-unroutable.md` — plausible prose, tags matching no entity → UNROUTED.
10. Synthetic `event-superseding.md` — `supersedes:` aimed at event 4 after consumption → staleness clause (c) fires on schema-foundations.

`expected.yaml` per event: `normalized_entities`, `matched_views`, `cascade_targets`, `conservation_class`, `staleness_reasons`. Runs as `staleness.py --self-test` cases (deterministic, no LLM). The governed entity vocabulary lives in `deploy/entities.yaml` — any edit to that file that alters an expected routing output requires updating `expected.yaml` **in the same commit**; drift between them is the test failing as designed.

**Pass.** 10/10 exact matches.

**Class.** B (routing lies). **Gates.** Pre-merge of `staleness.py`/`catalog.py`/`deploy/entities.yaml` changes; every compile is too expensive only if slow — it won't be (<1 s), so wire it into the self-test that flight-plan's sensor sweep runs each session.

### Family ROUTE — relevance routing (failure class B)

#### ROUTE-1 · ENTITY-VOCAB SELF-TEST (`entities.yaml` governance)

**Property.** (1) No alias maps to two entities (∀ alias a: |{entity e : a ∈ aliases(e)}| ≤ 1, case-insensitive after the documented normalization); (2) every entity is subscribed by ≥ 1 view OR carries an explicit `parked: <reason>` field — dead vocabulary is named, not silent; (3) every `cascades_to` target across the ledger resolves to an existing view or entity (dangling targets are FAIL with the event named); (4) the file parses and round-trips (`yaml.safe_load` → dump → load equality on the semantic content). The governed entity vocabulary is `deploy/entities.yaml` — this test is the tripwire on that file's integrity.

**Mechanics.** `catalog.py --self-test` + live mode. Fixtures: `entities/entities-valid.yaml`, `entities-dup-alias.yaml`, `entities-dangling-cascade` event fixture (reuses `events/event-dangling-cascade.md`).

**Pass.** Live exit 0; each invalid fixture trips its violation.

**Class.** B. **Gates.** Every compile + pre-merge of any `deploy/entities.yaml` edit (cheap enough to run on flight-plan too).

#### ROUTE-2 · HUB-ENTITY FAN-OUT WATCH

**Property.** No entity's fan-out (count of subscribing views) exceeds **40% of total views**, and no single ledger event stales more than 40% of views, without a named allowlist entry (`hub_ok: <reason>` on the entity in `deploy/entities.yaml`). Fan-out deltas vs the last committed catalog are printed every run (regression watch: an entity that doubles its fan-out in one compile is exactly how "everything is stale" begins).

**Mechanics.** Computed inside `catalog.py` (it already builds per-entity fan-out); threshold + allowlist in `deploy/entities.yaml`. Fixture `entities/entities-hub-fanout.yaml` (one entity subscribed by 6 of 8 fixture views) must trip.

**Pass.** Live exit 0; fixture trips.

**Class.** B (hub entities staling everything). **Gates.** Every compile; the delta print is informational, the threshold is FAIL.

#### ROUTE-3 · UNKNOWN-TAG RATE (vocabulary aging tripwire)

**Property.** Over the trailing 30 days of ledger events, the fraction of tags that resolve to no entity (post-normalization) is < 25%; every unresolved tag is listed with its event. Rising unresolved-tag rate is the mechanical signature of vocabulary aging — synonyms accumulating outside `deploy/entities.yaml`.

**Mechanics.** Part of `staleness.py --report` (it already touches every event's tags); threshold check + list. The real `bookings-entity-schema` event's hyper-specific tags (`wo-trigger-11-discharged`, `emit-catalog-audit`, …) are the realistic fixture for "specific tags that should alias to entities or be consciously unrouted."

**Pass.** Below threshold → exit 0 with the list printed; above → FAIL directing an alias-review.

**Class.** B. **Gates.** Every compile; reviewed in the weekly drill batch.

### Family CONTENT — content protection (failure class C)

#### CONTENT-1 · DELETION-FLOOR (mechanical, LLM-independent)

**Property.** For every view modified in a run: (1) the set of H2/H3 headings in the prior committed version ⊆ headings in the new version ∪ the receipt manifest's explicit `removed_sections` declarations; (2) net byte shrinkage ≤ 30% unless the manifest declares `split` or `supersession` for that view; (3) a `## Corrections` / corrections-trail section, once present, never shrinks in entry count without an explicit declaration. Cap-pressured agents may compress prose; they may not silently drop sections — removal must be **declared** to be legal, which makes it auditable.

**Mechanics.** Extends ACC-4's run-diff machinery (`git show <C>:<view>` vs working version, heading extraction = `^##{1,2} ` outside fences — reuse `FENCE_RE` from `check-frontmatter.py`). Fixtures: prior/new view pairs for each violation + one legal declared-split pair.

**Pass.** Live compile exit 0; fixtures trip.

**Class.** C (cap-pressured deletion; corrections-trail compression). **Gates.** Every compile, before commit; pre-merge self-test.

#### CONTENT-2 · SPLIT-CITATION INTEGRITY

**Property.** After any split of view F into parts P1..Pn: (1) a stub exists at F's original path with `status: superseded` and a redirect map; (2) every clause-ID anchor present in F-before appears in **exactly one** Pi (zero = dropped knowledge, two = ambiguous citation); (3) for every file in `raw/` and `handoffs/` whose text cites F by name (grep for the basename), if the citation carries a line number (regex `lines? \d+`), the stub carries a line→part mapping entry covering it OR a REVIEW entry names it — raw files are immutable (invariant 2), so the stub is where the citation is healed. The real case to fixture: `raw/2026-06-10-ryan-bookings-entity-schema.md` cites `schema-foundations.md` lines 1622 and 661; SHA-pinned handoff packets have the same exposure.

**Mechanics.** `deploy/check-split.py <old-path>` run as the P0 split's acceptance check and self-tested with a 3-file fixture split. The P0 split of `wiki/systems/schema-foundations.md` (308,020 B) and `wiki/flight-plans/plan-4.7-schema-sequence.md` (175,136 B) is the first live run.

**Pass.** All three conditions; the bookings-raw citation specifically resolves through the stub map.

**Class.** C (splits breaking citations). **Gates.** P0 acceptance (blocking); thereafter on any compile whose manifest declares a split.

#### CONTENT-3 · VERIFIED-RESET

**Property.** For every view whose **body** differs from HEAD in the working tree or in the run commit: `verified:` is null or bears this run's VERIFY stamp — never a surviving older date. Quantified: ∄ view V: body_changed(V) ∧ verified(V) = verified_HEAD(V) ≠ null.

**Mechanics.** Part of `deploy/check-derivation.py` (ACC-3's sensor): diff body (frontmatter excluded) vs HEAD; compare `verified`. Fixture: `views/view-stale-verified.md` pair.

**Pass.** Live exit 0; fixture trips.

**Class.** C (verified flag surviving rebuilds). **Gates.** Every compile pre-commit.

#### CONTENT-4 · FORMATTER DRILL (replay the 2026-06-09 incident — scheduled, not one-off)

**Property.** End-to-end: (1) flattening N=6 derivation blocks (join keys onto one line — the exact FLATTEN signature `deploy/check-frontmatter.py` documents, ≥2 known keys after the leading key) is **detected** by the extended `check-frontmatter.py` with all 6 files named, exit 1; (2) the compile preflight gate refuses to proceed while the sensor fails; (3) the canonical repair (`git restore --source=<last-clean-ancestor> -- <files>`) clears it; (4) post-repair, RUN JOURNAL replay reconstructs consumed-sets **byte-identically** to the pre-corruption snapshot — zero absorption-state loss, because the RUN JOURNAL lives in `receipts/journal/`, which the formatter class never touched. Item 4 is the design win the drill exists to prove: view-layer corruption cannot corrupt absorption accounting.

**Mechanics.** `deploy/drill-formatter.py`, worktree per convention §0.7. Steps: snapshot `staleness.py --report` JSON → corrupt 6 views with the flattener (port the corruption as a function, seeded from the real `2c3b6ac` diff shape) → run extended `check-frontmatter.py` (expect 1) → run `staleness.py` (expect it to still complete — degraded views flagged, journal intact) → `git restore` → re-run both (expect 0) → diff the two staleness JSONs (sorted keys).

Two load-bearing prerequisites this drill enforces on the build: (a) **`KNOWN_KEYS` in `check-frontmatter.py` must gain every derivation-block key** (`view`/`type`, `summary`, `entities`, `status`, `built_from`, `subscribes`, `verified`, `tier`, `bundle`, `schema_version`) — FLATTEN detection works by counting known keys per line, so unlisted keys silently weaken the tripwire; (b) wiki/ `EXPECTED` keys gain a v2 branch keyed on `schema_version` presence.

**Pass.** All four steps; final JSON diff empty.

**Class.** D (formatter corruption of machine-critical frontmatter) + A (state survival). **Gates.** Must pass on the worktree **before P1 backfill** (P1 mints 44 derivation blocks — the new attack surface); thereafter **monthly scheduled drill**.

### Family OPS — operational drills (failure class D)

#### OPS-1 · CRASH-ORDERING DRILL (kill between content write and journal write)

**Property — the write-ordering contract this test enforces:** an event E counts consumed by view V **iff** a parseable receipt records (V,E) in the RUN JOURNAL (`receipts/journal/<seq>.json`); content writes happen **before** the receipt is written; the receipt is written exactly once, at end of run, and lands in the same commit as the views. Therefore: (forward crash) a run killed after V's file write but before the receipt write leaves (V,E) ∉ J → next staleness run lists E as PENDING for V and re-presents it — **must**, with the half-written view content still on disk (re-absorb is safe: merge is idempotent-by-manifest and no-op is legal). (Inverse) journal-before-content is **structurally excluded** (single receipt at end of run — there is no earlier journal write site) and **detected** if violated anyway: ACC-4 (receipt names V, commit diff doesn't touch V) + ACC-3 (V's `last_run` lags J).

**Mechanics.** `deploy/drill-crash-absorb.py`, worktree. The LLM agent can't be killed deterministically, so the drill replays the **mechanical write sequence** with an injectable crash point: `--crash-point {after-content, after-journal, after-commit}`. after-content: apply a content patch to a fixture view, write no receipt, run `staleness.py` → assert E PENDING and a dirty-view warning. after-journal (the violation, simulated to prove detection): write a receipt claiming (V,E) without touching V, commit → assert ACC-4 exit 1 and ACC-3 exit 1. after-commit: clean state, assert E CONSUMED.

**Pass.** All three crash points produce exactly the specified verdicts.

**Class.** D (crash mid-absorb) + A. **Gates.** Pre-merge of compile-v2 (P2 entry gate); monthly drill thereafter.

#### OPS-2 · CONCURRENCY DRILL (lockfile + stale lock + override)

**Property.** (1) With run 1 holding the run lockfile (contract: a single lockfile at repo root or `.claude/`, content = `{pid, hostname, started_iso, run_type}` JSON), run 2 started 10 minutes later **refuses cleanly** — exit code distinct from crash, message naming the holder, zero writes performed. (2) A stale lock (holder PID not alive — checked via `OpenProcess`/`tasklist`, §0.6) is detected and the documented override (`--break-stale-lock`, which logs the broken lock's content into the new run's receipt notes) succeeds. (3) A live-PID lock is **never** auto-broken — override of a live lock requires a different flag (`--force`) that the orchestrator skills never use.

**Mechanics.** `deploy/drill-concurrency.py`, worktree. Run 1 = a stub process (`py -c "import time; time.sleep(120)"`) whose PID is written into a lock; run 2 = the real lock-acquire function. Stale case: write a lock with a known-dead PID (spawn+wait a process, reuse its PID). Assert refusal exit code, override behavior, and the receipt-note logging.

**Pass.** All three behaviors exact.

**Class.** D (concurrent runs corrupting shared tree; lockfile staleness). **Gates.** Pre-merge of the lockfile code (P2 entry); quarterly drill (cheap, low drift risk).

#### OPS-3 · STAGE-ONLY DISCIPLINE (salted-tree commit test)

**Property.** The run's commit step, executed in a tree salted with unrelated work, commits **exactly** the run's declared paths and nothing else; every salt file survives on disk, unstaged and uncommitted. Quantified: commit-file-set == manifest-path-set; salt-set ∩ commit-set = ∅; salt files' bytes unchanged.

**Mechanics.** `deploy/drill-stage-only.py`, worktree. Salt = the real contamination pattern from the live tree's current status: an untracked script (`scripts/zz-drill-salt.ts`), an untracked root .md, a **modified tracked file** outside `wiki/` (e.g., touch a roadmap file), and a modified tracked file **inside** `wiki/` that the run does not claim (the hard case — `git add wiki/` would swallow it; the discipline must be per-path `git add <file>`, never directory adds). Execute the engine's commit function with a fixture manifest; inspect `git show --name-only` + `git status --porcelain`.

**Pass.** Set equality + salt untouched, including the in-wiki unclaimed file.

**Class.** D (stage-only vs other sessions' uncommitted files). **Gates.** Pre-merge of compile-v2 commit code (P2); included in the monthly drill batch.

#### OPS-4 · P2-WORKLOAD-ACCEPTANCE (30-day replayed workload cost benchmark)

**Property.** P2 acceptance is not a single-event smoke test — it is a **replayed 30-day workload benchmark** that reflects the real distribution of work. The benchmark must: (a) preserve the T1/T3 event distribution from the trailing 30 days of the ledger; (b) preserve the real route fan-out (how many views each event actually stales); (c) require mandatory T1 verify (every T1 event gets a full VERIFY pass, not a sampled one); (d) apply sampled T3 verify (1-in-5 sample, consistent with the weekly drill cadence). The benchmark reports **p50 and p95 wall-clock time** plus **total `token_cost`** for the replay, compared against the equivalent old-path baseline (current compile skill processing the same 30-day event set). A single real T1 lock event is retained as an end-to-end smoke test only — it does not substitute for the benchmark. FAIL criteria: p95 wall-clock > 2× old-path p95, OR `token_cost` > 1.5× old-path (the engine's value proposition is bounded cost, not unbounded cost). A benchmark that exceeds the threshold blocks P2 advance until the absorb loop is profiled and the offending hotspot addressed.

**Mechanics.** `deploy/drill-workload-bench.py`, worktree. Pulls the real 30-day ledger slice (events from `git log --since="30 days ago" -- raw/`); fans out staleness per the real entities.yaml; fires absorb agents (haiku for fan-out probes, sonnet for T3 absorbs, top-tier for T1 absorbs, per Model Economy); fires mandatory T1 VERIFY legs (non-haiku); records wall-clock via `time.perf_counter` and `token_cost` from each agent invocation's response metadata; emits a drill receipt (`type: workload-bench`) with the p50/p95 table and old-path comparison. Old-path baseline is measured by running the current compile skill in dry-run mode (no actual writes) over the same event set, recording wall-clock and token usage.

**Pass.** p95 wall-clock ≤ 2× old-path p95 AND `token_cost` ≤ 1.5× old-path, for the 30-day window. The end-to-end smoke T1 event must also exit CONSUMED with a valid VERIFY artifact.

**Class.** D/E (cost honesty; a P2 that is accepted on a single easy event and then burns the meter in production is an operations failure). **Gates.** P2 acceptance (blocking — the phase does not advance on a single-event pass alone); re-run whenever the event distribution shifts significantly (> 20% T1 share change) or the real receipt count crosses 200.

#### OPS-5 · PATH-CONTAINMENT (symlink escape, traversal, absolute path)

**Property.** Every path opened by the engine as an LLM-authored path (receipt, journal record, or frontmatter field) routes through `resolve_within(root, p)`. The function must: (1) run `realpath` on the candidate path **before** `commonpath` comparison — a symlink that resolves to an out-of-root target is caught before the boundary check, not after; (2) reject `..` components (literal traversal); (3) reject absolute paths that do not descend from the repo root; (4) treat all three cases as **fatal violations** — exit 1, name the path, write nothing. A view file that is a symlink to an out-of-root target must be rejected, not opened, silently skipped, or partially read.

**Mechanics.** `deploy/check-path-containment.py --self-test`. Fixtures under `deploy/test-fixtures/memory-engine/paths/`:

| Fixture | Attack vector |
|---|---|
| `symlink-escape/` — a view symlinked to `../../outside.md` | symlink traversal |
| `dotdot-event.md` path field `"../../raw/escape.md"` | literal `..` |
| `abs-path-event.md` path field `"C:/Windows/System32/evil.txt"` (or `/etc/passwd` on POSIX) | absolute path |
| `junction-root/` — the repo root itself reached via a Windows junction or `subst` drive letter, so the literal root path diverges from its `realpath` | root-via-junction; ensures `resolve_within` realpaths the root, not just the candidate |
| `ads-path-event.md` path field `"receipts/journal/0042.json:payload"` | NTFS alternate-data-stream path (`:` beyond the drive letter) — hides content from git and all sensors |

Each fixture: the path-containment check rejects it (exit 1, path named), the engine never opens it. The `realpath`-before-`commonpath` order is verified by constructing a symlink whose `commonpath` would pass but `realpath` fails — the test asserts failure, not silence. The junction-root fixture verifies `realpath` is applied to **both** arguments (the root and the candidate) before comparison. The ADS fixture verifies that any `:` beyond the Windows drive letter (e.g., beyond `C:`) is rejected before any filesystem call. Check-then-open TOCTOU between the containment check and the subsequent open is an **accepted single-machine residual** — stated explicitly in the test and in spec §16.

**Pass.** All five attack vectors rejected with named path in output. Symlink fixture explicitly tests `realpath`-first ordering on the candidate. Junction-root fixture explicitly tests `realpath`-first ordering on the root. ADS fixture rejected on the `:` rule before any filesystem call.

**Class.** D/E (security boundary — path containment is a pre-condition for the taint model). **Gates.** P1 (before any LLM-authored path is opened) — a path-containment violation before P1 means untrusted text can reach any file on the machine; pre-merge of any code that opens LLM-authored paths.

#### OPS-6 · LOCK-COMMON-DIR (worktree `seq` isolation and parent-chain integrity)

**Property.** With two git worktrees on one repo running concurrently: (1) `seq` numbers do not collide — the lock under `git rev-parse --git-common-dir` serializes allocation across both worktrees; (2) each journal record embeds `prev_record_hash` (hash of the record at `seq−1`); `staleness.py` enforces a **strict journal hash-chain** and fails loud on a gap (missing `seq`), a duplicate `seq`, or a `prev_record_hash` mismatch — chain integrity is on the journal, not git topology. `parent_git_sha` is **forensic metadata only** and is never used for chain enforcement; two concurrent worktrees branching from the same HEAD commit are a **legal success case** — they produce non-colliding `seq` values and a clean `prev_record_hash` chain, and must NOT trip any chain check. (3) A race fixture (two simultaneous lock-acquire calls in the same test process) results in exactly one winner per `seq` slot — the loser retries or exits, never silently claims the same `seq`.

**Mechanics.** `deploy/drill-lock-common-dir.py`, worktree drill. Sets up two git worktrees (`../wiki-drill-wt1`, `../wiki-drill-wt2`). Fires two lock-acquire threads simultaneously with a configurable sleep-jitter. Asserts no `seq` collision across both worktrees' journal files. **Legal-concurrency assertion:** two worktrees branching from the same HEAD commit run to completion and the drill asserts exit 0 with a valid `prev_record_hash` chain — no false fork. Then injects three chain violations into a fixture sequence: a gap (removes `seq` 3 from a 5-record sequence), a duplicate `seq` (two records claiming `seq` 4), and a `prev_record_hash` mismatch (record 4 carries the wrong hash for record 3); runs `staleness.py --check-chain` on each; asserts exit 1 with the violation named. Both worktrees cleaned up at exit.

**Pass.** Zero `seq` collisions across concurrent worktrees; legal concurrent-worktree case exits 0 (clean chain, no false fork); gap injection detected (exit 1, seq named); duplicate-seq injection detected (exit 1, seq named); hash-mismatch injection detected (exit 1, seq and expected vs actual hash named).

**Class.** D (split-brain journal from worktree-local lock; chain integrity). **Gates.** P2 (the lock mechanism ships with compile-v2); monthly drill thereafter.

### Family LLM — absorb/verify quality harness (failure classes A/B/C, LLM layer)

#### LLM-1 · PLANTED-DEFECT EFFICACY (VERIFY catch rate)

**Property.** Across a batch of K=5 planted defects — one per class: (d1) dropped clause (a commitment sentence deleted), (d2) wrong-section merge (content landed under an unrelated heading), (d3) fabricated claim (a "shipped"/quantitative statement with no source event), (d4) stale pin advanced (corpus pin moved without the artifact changing — or vice versa), (d5) no-op'd relevant event (a genuinely relevant event journaled as no-op) — a fresh-substrate VERIFY agent **with the prior-view diff in its packet** catches ≥ 4/5 per batch, and over any two consecutive batches catches 2/2 of {d1 dropped clause, d3 fabricated claim} (the two outright-corruption classes get a hard floor). Below threshold → T1 absorbs **halt** until the VERIFY protocol/prompt is revised and a fresh batch passes — the SEAM-CERTIFIED ratchet pattern, applied to the verifier itself.

The packet-content requirement is itself part of the property: VERIFY without the prior-view diff is structurally blind to d1 (deletions) — the `assemble.py` / VERIFY packet spec must include `git diff <prior>..<current> -- <view>`, and the harness fails the batch setup (INCONCLUSIVE, exit 2) if it doesn't.

**Mechanics.** Worktree drill `deploy/drill-planted-defects.py` prepares the 5 defect states (deterministic text mutations of real views — fixture mutation specs committed under `views/defects/`); a hub session fires 5 VERIFY legs (subagents per the Model Economy table — verification legs **never** haiku); results recorded in a drill receipt (`receipts/<ts>-drill-verify.md`, normal receipt envelope, `type: drill-verify`, catch matrix in frontmatter).

**Migration-era content audit (F13).** The migration content audit (event claims → view hunks, run over `legacy-assumed` and `absorbed-without-source` T1/correction/lock views at migration time) **runs under this same VERIFY harness** — it is not a standalone LLM batch. Requirements: (a) substrate must differ from the agent that wrote or maintains the view lineage (vendor-gated per spec §5, same rule as structured `verified:` blocks — LLM-5); (b) the audit is recorded in the same structured form as any VERIFY pass; (c) the batch must include **one planted-defect audit** — a fixture view from which an event's load-bearing claim has been deliberately removed — as the efficacy floor. The planted-defect audit must be caught (exit 1 on the fixture) before the batch result is accepted. A content audit batch that omits the planted-defect case is INCONCLUSIVE (exit 2). The catch-rate floor for the content audit batch mirrors LLM-1: ≥4/5 on the regular views + catch of the planted-defect fixture.

**Pass.** ≥4/5 and the d1/d3 floor (VERIFY). Content audit under the harness: ≥4/5 + planted-defect caught; wrong-substrate or missing planted-defect → INCONCLUSIVE (exit 2).

**Class.** C (deletions, fabrication), A (silent absorption via d5), B (d5 relevance). **Gates.** P3 entry (RECONCILE+VERIFY phase cannot advance without one passing batch); thereafter **weekly batch** (cost-bounded: 5 sonnet/top-tier legs/week, not per-compile). Content audit: gate before any `legacy-assumed`/`absorbed-without-source` T1 view is cleared to `verified-consumed` (migration phase — run once, not weekly).

#### LLM-2 · MERGE-MANIFEST AUDIT (mechanical half; no-ops in scope)

**Property.** Every ABSORB receipt's per-event merge manifest is mechanically consistent with the actual diff: each claimed insertion/modification names a section that exists in the post-state and whose text changed in the run commit; no diff hunk in the view falls outside the union of manifest claims (modulo whitespace). This is ACC-4 at section granularity — and yes, it is mechanically diffable: manifest claims carry heading anchors; hunks map to headings by line range. **No-ops are in scope:** every journaled no-op is justified against the full event body AND the full view body (not an empty diff); the verifier artifact + packet hash must be present in the `noop_candidates` journal entry. T1 / correction / lock / `informed_by` no-ops must carry a verifier artifact produced in the same run (not weekly sampling). A no-op whose justification cites only an empty diff is a FAIL on this test. **Conservative `event_class` default (F6, same rule as ACC-4(c)):** a no-op on a judgment-assigned-class event (`class_source: judgment`) routes to `PENDING_NOOP_CANDIDATE` regardless of the assigned class value; this is mechanically verifiable from the registration record. The LLM-2 fixture set includes a judgment-assigned-class event whose no-op is accepted by the manifest check but must still be flagged PENDING_NOOP_CANDIDATE by this rule.

**Mechanics.** Extension of `deploy/check-run-diff.py` (`--sections` mode). Unit fixtures: manifest+diff pairs (consistent / claim-without-hunk / hunk-without-claim) + a no-op-full-body-justified fixture (pass) + a no-op-empty-diff fixture (fail) + a T1-no-op-missing-same-run-artifact fixture (fail).

**Pass.** Live compile exit 0; fixtures trip.

**Class.** A (agent advances state over partial merge — the unclaimed-hunk and claimed-but-absent cases are precisely partial-merge signatures; unverified no-ops are silent absorption). **Gates.** Every compile from P2 on.

#### LLM-3 · NO-OP LEGITIMACY SAMPLING

**Property.** Of the no-ops journaled in the trailing week, a random sample of min(5, all) re-judged by a fresh agent (event + view + the no-op reason) agrees "legitimately irrelevant/duplicate" for ≥ 4/5. Persistent disagreement on the same (entity, view) pair across two samples → that pair's routing is escalated to REVIEW.md.

**Mechanics.** Part of the weekly drill batch (rides LLM-1's session); sample drawn deterministically (seed = ISO week) from the RUN JOURNAL so it's reproducible; results in the same drill receipt.

**Pass.** ≥4/5 agreement.

**Class.** A (silent absorption via false no-op) + B. **Gates.** Weekly.

#### LLM-4 · INTAKE-ROUTING AUDIT (wrongly-plausibly-tagged events)

**Property.** Weekly sample of 5 routed events re-routed blind by a fresh agent given only the event body (no tags); the tag-derived routing and the blind routing agree on the primary target for ≥ 4/5. Disagreements don't auto-fail — each becomes a named triage line (tag vocabulary vs judgment) in the drill receipt; two consecutive failing weeks → FAIL gating the next compile until vocabulary review.

**Mechanics.** Same weekly batch as LLM-1 and LLM-3.

**Class.** B (plausibly-tagged events routed without judgment; semantic misses the tag channel can't see). **Gates.** Weekly.

#### LLM-5 · SUBSTRATE-SEPARATION (verifier must differ from absorb substrate; structured verified block)

**Property.** For every view that has been through VERIFY: (1) the `verified:` block must be present as a **structured block** (per spec §5) — a bare date string is not sufficient; (2) `verifier_vendor` and `verifier_model_id` must be present as **separate fields** (F18 — `vendor` and `model_id` recorded separately, never collapsed into a single `verifier_substrate` string); similarly `absorb_vendor` and `absorb_model_id` as separate fields; (3) `packet_hash` and `artifact` must be non-empty and the artifact path must resolve to an existing receipt; (4) **substrate fields must be derived from the agent invocation's response metadata** (the same channel `token_cost` uses in OPS-4), never from orchestrator-supplied strings — an orchestrator that writes `verifier_vendor: gpt-5` after spawning a verify leg with no model override (so it inherited the absorb session's model) passes the string check but fails the provenance check; (5) the gate fails per the **named policy (F18):** routine T1 verify gates on `verifier_model_id ≠ absorb_model_id` (model-id difference); migration-era content audits and design-gate verifications gate on `verifier_vendor ≠ absorb_vendor` (vendor difference — the cross-substrate firewall). The gate also fails if any required field is missing or if the `verifier_vendor`/`absorb_vendor` comparison uses collapsed family strings instead of the separate field pair.

**Mechanics.** `deploy/check-derivation.py` (--substrate-check mode, or integrated into the existing derivation check). Fixtures:

| Fixture | Defect |
|---|---|
| `views/view-verified-valid.md` | All fields present, verifier_model_id ≠ absorb_model_id, fields separate — passes routine T1 policy |
| `views/view-verified-valid-cross-vendor.md` | verifier_vendor ≠ absorb_vendor, separate fields — passes migration-era policy |
| `views/view-verified-bare-date.md` | `verified: 2026-06-10` (old bare-date format) — fails |
| `views/view-verified-same-model-id.md` | `verifier_model_id: claude-sonnet-4-6`, `absorb_model_id: claude-sonnet-4-6` — fails routine T1 gate |
| `views/view-verified-same-vendor-design-gate.md` | `verifier_vendor: anthropic`, `absorb_vendor: anthropic` — fails migration-era/design-gate policy |
| `views/view-verified-missing-artifact.md` | `artifact` field empty or absent — fails |
| `views/view-verified-orchestrator-spoofed.md` | `verifier_vendor: gpt-5` with no invocation metadata provenance record — fails; the journal record must carry the raw invocation metadata, and a mismatch between the `verified:` block and the metadata is a FAIL |

The spoofed-substrate fixture explicitly tests that the check reads substrate from invocation metadata, not from the `verified:` block's self-reported strings. The `--substrate-check` mode must: retrieve the journal record for this VERIFY pass, confirm the metadata-derived vendor/model matches the `verified:` block's fields, and fail if they diverge or if no metadata record exists.

**Pass.** Both valid fixtures pass their respective policies; all five defect fixtures trip their named violation; spoofed-substrate fixture caught via metadata comparison.

**Class.** A/C (a self-graded verification is no verification; "recorded, not attested" upgrades G13 only when the record is derived from unforgeable metadata). **Gates.** P1 (the derivation block format ships at P1 — the structured `verified:` block with separate vendor/model_id fields must be in the schema from day 1); every compile from P1 on; pre-merge of any change to the `verified:` schema or the invocation-metadata harvesting code.

#### LLM-6 · CORPUS-SUPPORT (shipped-state prose must match the cited artifact)

**Property.** For every shipped-state prose hunk in a view (identified by `corpus_support` entries in the journal record, or by RECONCILE-flagged prose that changed without a `corpus_support` entry): (1) the cited artifact must be a path subscribed by the view (`subscribes.corpus` list); (2) the cited SHA must match the pin recorded in the journal; (3) the `support_lines` must be **verbatim quoted text** (not a line range or a prose summary) and must actually appear verbatim in the artifact at the pinned SHA — a recorded-but-irrelevant corpus read does not satisfy the requirement (the read must support the specific hunk). A hunk whose journal records a corpus read of an **unrelated artifact** (one not in `subscribes.corpus`) fails. A hunk with no `corpus_support` entry at all when the prose makes a shipped-state claim also fails. **VERIFY packet embed (F16):** the assembled VERIFY packet must include the mechanically-resolved artifact excerpt (retrieved via `git show <sha>:<artifact>` from the pinned SHA) next to each shipped-state hunk — so that a cross-vendor, repo-blind verifier can judge the support claim without access to the corpus repos. A VERIFY packet that omits the resolved excerpt is INCONCLUSIVE (exit 2) for the shipped-state hunk in question.

**Routing census (F15).** The routing census — the table of (ledger-event, matched-view) pairs used to bound the VERIFY packet and audit absorption completeness — is emitted by a **standalone reproducible script** (`deploy/emit-routing-census.py` or an isolated `staleness.py --emit-census` mode), not assembled inline by the orchestrator. Its inputs (the ledger slice SHA + `entities.yaml` blob SHA) and its output hash are both journaled in `<seq>.json`'s `census_record` field. VERIFY (or CI) re-runs the script with the same inputs and compares the output hash — a mismatch means the census was tampered with or assembled by a different code path. A census where the matcher can both drop a route and also control the census that audits the drop is not an independent check. Fixture: run `emit-routing-census.py` twice with identical inputs, assert identical output hash.

**Mechanics.** Extension of `deploy/check-run-diff.py` (`--corpus-support` mode) or a separate `deploy/check-corpus-support.py`. For each `corpus_support` entry in `<seq>.json`: resolve the artifact path + SHA via `git show <sha>:<artifact>` in the cited corpus repo (from the Corpus Observation table in CLAUDE.md: each corpus repo at its registered clone path); verify `support_lines` appears verbatim in the retrieved blob; fail if not found or if it is a line range rather than quoted text. Fixtures: `corpus_support` entries for (valid: verbatim support lines present in artifact at SHA) + (invalid: support lines absent) + (invalid: support_lines is a line range, not verbatim text) + (invalid: artifact unrelated to view's subscribes.corpus) + (invalid: no corpus_support on a shipped-state hunk) + (invalid: VERIFY packet missing the resolved excerpt — exits INCONCLUSIVE).

**Pass.** Live compile exit 0 (every corpus_support entry valid); all three invalid fixtures trip.

**Class.** C (shipped-state prose fabricated or unsupported — the "a corpus read happened" loophole closed). **Gates.** P3 (RECONCILE + VERIFY phase, when shipped-state prose claims first become systematically checked); every compile from P3 on.

### Family ECO — ecosystem guards (failure class E + leftovers)

#### ECO-1 · PACKET-RECALL (golden task descriptors)

**Property.** For every descriptor in `descriptors/golden-descriptors.yaml` (≥ 5 at adoption, e.g. "author purchase-orders Sqitch DDL", "brief a session on gateway write-seam state", "answer: which entities carry public_id?"): `assemble.py` returns a packet containing **100% of the hand-authored REQUIRED view list**, reports byte-budget fit, and includes the bundle closure (no member of the transitive closure missing). Negative half: a descriptor whose required view is forced stale → T1: `assemble.py` **refuses** (exit 1, names the stale view); T3: packet emitted **with the stale banner string present**.

**Mechanics.** Repo-level integration, `assemble.py --self-test` running descriptors against the live catalog; the stale-forcing uses a fixture overlay (temp journal omitting one absorption), never live-state mutation. Answer keys hand-authored once, updated in the same commit as any bundle/catalog change (GOLD-2 discipline).

**Pass.** 100% required-recall on every descriptor; both negative behaviors exact.

**Class.** E (packet recall misses; stale-served views consumed by build dispatches — `assemble.py` is the sanctioned packet channel and this is its contract). **Gates.** Pre-merge of `assemble.py` (P4 entry); re-run whenever bundles, catalog, or `deploy/entities.yaml` change.

#### ECO-2 · SKEW GUARD (schema_version)

**Property.** (1) Every derivation block carries `schema_version`; preflight and `deploy/check-derivation.py` refuse (exit 1) any view whose version ≠ the tooling's supported version. (2) Old-skill simulation: an edit that writes a v1-shaped frontmatter (no derivation block / legacy keys only) onto a v2 view is caught by extended `check-frontmatter.py`/`check-derivation.py` **before commit** — the v2 EXPECTED-keys branch flags the now-missing derivation keys. (3) New-skill-on-old-state: v2 tooling encountering a view with no `schema_version` exits 1 naming it un-backfilled (legal only during P1, allowlisted by the P1 receipt's backfill list).

**Mechanics.** Fixtures `views/view-v2-valid.md`, `views/view-v2-skew.md` (version: 1), `views/view-v1-legacy.md`. The old-skill simulation is a fixture state, not an actual old skill run: overwrite a v2 fixture's frontmatter with the v1 shape and run the sensor.

**Pass.** All three directions caught.

**Class.** E (version skew, both directions). **Gates.** Every compile + flight-plan from P1 on; pre-merge self-test.

#### ECO-3 · BYTE-CAP CRLF STABILITY

**Property.** Cap verdicts are checkout-invariant: for any content x, verdict(x with LF endings) == verdict(x with CRLF endings). Therefore caps are **defined on LF-normalized UTF-8 byte length** (read binary, `replace(b"\r\n", b"\n")`, measure) — never `os.path.getsize` — because this repo has `* text=auto` + `core.autocrlf=true` (verified): the same file is CRLF in the Windows working tree and LF in the index/VPS checkout, differing by one byte per line (~+3,400 bytes on a 500-line T1 view — enough to flip a boundary verdict).

**Mechanics.** Unit test in the cap-checker's `--self-test`: fixture `views/view-cap-boundary-lf.md` committed at exactly cap bytes (LF); the test materializes its CRLF twin in a temp file and asserts identical verdicts at cap and cap+1. Parametric over the cap table (caps per view type live in `deploy/entities.yaml` or the engine config — the test reads them, never hardcodes).

**Pass.** Verdict equality both sides of the boundary.

**Class.** D/E (a cap that fails only on one platform is an operations lie). **Gates.** Pre-merge of cap enforcement (P0 — caps are the first thing built).

#### ECO-4 · CATALOG/PROJECTION IDEMPOTENCE — f(f(x)) = f(x)

**Property.** With no state change, a second consecutive run of `catalog.py` and `project.py` produces byte-identical outputs (CATALOG.md/json, INDEX/HEALTH/changelog skeletons, briefing skeleton). Implies: no embedded wall-clock timestamps except sourced ones (generation time, if kept, comes from the receipt being projected, not `now()`), stable sort orders, no accreting sections.

**Mechanics.** Integration: run twice in-place (both are read-only-derive scripts writing only their own outputs), `git diff --stat` between runs must be empty; unit form in each script's `--self-test` over fixture state.

**Pass.** Empty diff.

**Class.** A (drift via projection churn) + E (Ryan-facing surface stability). **Gates.** Pre-merge of `catalog.py`/`project.py`; every compile (it's one extra invocation, ~free).

#### ECO-5 · BUNDLE-CLOSURE TERMINATION + CYCLE DETECTION

**Property.** Bundle closure over view→view references terminates on every input, deduplicates, and on a cyclic graph (A→B→C→A) returns the cycle as a **named report** (not a hang, not a stack overflow, not silent truncation). T1 "full bundle or refuse" honors the closure: if any closure member exceeds budget, `assemble.py` fails loud (exit 1, names the overflowing member) — never emits a partial T1 bundle.

**Mechanics.** Pure-function unit in `assemble.py --self-test`: fixture graphs (chain, diamond, 3-cycle, self-loop) as small view fixtures with derivation-block references.

**Pass.** Termination < 1 s on all fixtures; cycle named; T1 overflow refused.

**Class.** E (bundle incompleteness — and its dual, unbounded bundles). **Gates.** Pre-merge of `assemble.py` (P4).

#### ECO-6 · SURFACE-FRESHNESS (Ryan-facing projections)

**Property.** `SESSION-BRIEFING.md` and `FLIGHTDECK.md` each embed the run-id/timestamp of the state they were projected from; that stamp is ≥ the newest receipt's timestamp minus the runs allowed to skip regeneration (per their contracts: briefing regenerates on every compile and no-arg flight-plan). A briefing older than the newest compile receipt = degraded Ryan-facing surface, reported by flight-plan's sensor sweep.

**Mechanics.** Lightweight check inside `deploy/check-derivation.py` or `project.py --check`: parse the embedded stamp (the projection format already carries a generated line), compare with max receipt timestamp.

**Pass.** Stamp current → 0; lagging → 1 with the gap printed.

**Class.** E (briefing/FLIGHTDECK degrading). **Gates.** Every flight-plan (i.e., every session start).

#### ECO-7 · TAINT-QUARANTINE (external-scrape / unknown-origin content excluded from T1 packets)

**Property.** `assemble.py`, when assembling any **build/fix packet** (regardless of view tier), must exclude any view whose `origin_max` is `external-scrape` or `unknown`. The quarantine keys on **task type, not view tier** — most views are T3, so a tier-keyed quarantine passes tainted content to routine build workers with only a banner, and a banner is not a security boundary against injection in a code-writing session. Quantified: for every build/fix task descriptor (T1 or T3), the assembled packet contains zero views with `origin_max ∈ {external-scrape, unknown}`. If such a view is the only view satisfying the descriptor, `assemble.py` exits 1 naming the excluded view and its origin — it does not silently drop the view and proceed with an incomplete packet. For recon/verify tasks, the quarantine does **not** apply (the prompt carries a taint banner instead).

**Mechanics.** `assemble.py --self-test` (a `--taint-quarantine` case): (1) a fixture catalog with one view whose `origin_max: external-scrape` and one whose `origin_max: human`, both matching a **T1** build/fix descriptor; packet must contain only the human-origin view. (2) A **T3** build/fix descriptor matching an `origin_max: external-scrape` view — `assemble.py` must EXCLUDE the view (exit 1, name it and its origin), not banner it; this confirms the quarantine keys on task type, not tier. (3) A T1 descriptor whose only matching view has `origin_max: unknown` — exit 1, view and origin named, no packet emitted. (4) A recon/verify descriptor matching the same external-scrape view — packet must include the view with a taint banner.

**Pass.** T1 build/fix packet: zero tainted views; T3 build/fix packet: tainted view excluded (exit 1 named), not bannered; only-tainted-view build/fix: exit 1 named; recon/verify packet: tainted view included with banner.

**Class.** E/D (security boundary — tainted content reaching a credentialed build worker is an injection pathway). **Gates.** P4 (TAINT-QUARANTINE is named in the spec §13 P4 gate column); pre-merge of `assemble.py`.

#### ORIGIN-PROPAGATION · SESSION-LOOP TAINT (F9; ECO group)

**Property.** A session-authored event's registration `origin` must be ≥ the `origin_max` of the packet the session consumed — origin cannot decrease through a session loop. Quantified: if the session's assembled packet carries `origin_max: external-scrape`, any raw event the session appends is registered with `origin` = `external-scrape` (or `unknown`), never `human`, `corpus`, or `vendor-ref`. Only an operator-authored decision file with a matching receipt trail in `raw/` earns `origin: human`; session-authored raws consuming tainted context do not. This closes the taint-laundering path: tainted view → session-authored raw → trusted origin → clean build packet.

**Mechanics.** `deploy/check-path-containment.py` or a separate `deploy/check-origin-propagation.py --self-test`. Registration is validated at sidecar write time: the engine reads the assembled packet's `origin_max` from the session's intake record (or the dispatch envelope's declared packet) and gates the registration record's `origin` field. Fixtures: (a) a fixture session intake record with `packet_origin_max: external-scrape` + a fixture raw event appended by that session; the registration must stamp `origin: external-scrape` (or `unknown`), never `human` — PASS; (b) same setup but the engine stamps `origin: human` without an operator decision file backing — FAIL (the violation is named in the registration audit); (c) a raw event whose filename matches the `ryan-*` / operator-decision-file pattern AND has a matching receipt entry — permitted to be stamped `origin: human` — PASS. The `--self-test` feeds each fixture through the registration writer and asserts the correct outcome.

**Pass.** Fixture (a): origin propagated correctly (external-scrape or unknown, not human); fixture (b): stamping human without backing raw file detected as FAIL; fixture (c): operator decision file with receipt trail permits human.

**Class.** E/D (taint laundering through the session loop; the quarantine perimeter). **Gates.** P1 (registration records ship at P1; origin propagation must be enforced from first registration); pre-merge of the registration writer code.

#### EGRESS-CO-RESIDENCY · TAINT + CREDENTIAL + EGRESS SEPARATION (F11; ECO/SEC group)

**Property.** `assemble.py` must refuse to emit a packet whose `origin_max` exceeds `human` (i.e. is `external-scrape` or `unknown`) to a **credentialed session profile** unless a sandbox attestation is present in the descriptor. Credentialed session profiles are those whose tool list includes any MCP credential tool (`mcp__bizzflo__*`) or push/egress capability (`git push` allowlisted). A session carrying a taint banner (i.e. consuming a packet with non-human/non-corpus origin) is additionally stripped of credentialed tools and push/egress capability — the taint/credential/egress co-residency invariant (spec §9). This closes the injection pathway: scraped content in a T3 packet steers a credentialed session to write secrets into a raw file that the next compile's `git push vps master` carries off-machine — no blocked hook, every sensor green.

**Mechanics.** `assemble.py --self-test` (`--egress-co-residency` case). Fixtures: (a) a tainted packet (`origin_max: external-scrape`) + a credentialed session profile (tool list includes `mcp__bizzflo__search_customers`) + no sandbox attestation → `assemble.py` must **refuse** (exit 1, naming the packet origin and the credentialed profile); (b) same tainted packet + credentialed profile + a fixture sandbox attestation present in the descriptor → allowed (exit 0); (c) a clean packet (`origin_max: human`) + credentialed profile + no sandbox attestation → allowed (exit 0). The credential-stripping case: given a session that has already consumed a tainted packet (taint banner in its receipt), a subsequent assemble call must strip credentialed tools and push capability from the returned packet envelope, named in the output.

**Pass.** Fixture (a): refused, exit 1, origin and profile named; fixture (b): allowed with sandbox attestation; fixture (c): clean packet allowed; credential-stripping: credentialed tools and push removed from taint-bannered session packet.

**Class.** E/D/SEC (the co-residency invariant is the outermost security boundary — its failure is exfiltration through sanctioned channels). **Gates.** P4 (`assemble.py` ships at P4; egress enforcement must ship with it); pre-merge of any change to the session profile schema or the packet emission path.

### Family MIG — migration gates

#### MIG-1 · P1 DRY-RUN GATE (seeding rehearsal with operator review)

**Property.** P1 (entities.yaml + derivation backfill on 44 articles + consumed-set seeding) executes **first on a worktree**; the live P1 may run only after: (1) GOLD-1 passes on the worktree output (100% sources-pairs, ≥90% filtered receipt-pairs, zero untriaged); (2) the residue list — every ledger event the seeding does **not** declare consumed — is snapshotted to `consumed-sets/residue-p1.yaml`, each entry carrying `{event, reason-bucket, disposition}`; (3) **Ryan reviews the residue snapshot before live P1** (presented compactly per the gate-presentation memory: counts per bucket + the full T1 list, not 69 raw lines); (4) **Ryan reviews the injection list before live P1 (F14, mandatory)** — the injection list is the full set of `absorbed-without-source` pairs (pairs claimed consumed without sources-frontmatter evidence), presented with per-pair quoted receipt evidence (the exact excerpt from the supporting receipt that justifies the pair). The injection list is strictly more dangerous than the residue list — it is what is claimed consumed, not what was excluded — and requires its own operator review section in the gate drill output. **Abort criteria (any → live P1 blocked):** residue > 12 events (>17% of the 69-event ledger); OR any event with `informed_by:` (a handoff lock — 26 exist) in residue without an explicit per-event explanation; OR any GOLD-1 sources-pair miss; OR `check-frontmatter.py` fails on the backfilled worktree (the 44 new derivation blocks must be born clean); OR **any T1/lock pair in the injection list lacks quoted receipt evidence** (the pair is not merely counted — the receipt excerpt must be present; any T1/lock pair without it → BLOCKED).

**Mechanics.** `deploy/drill-migration-p1.py`, worktree; runs the real P1 procedure + GOLD-1 + extended `check-frontmatter.py` + ACC-1; emits the residue snapshot + agreement report; the operator gate is a designed human node (never absorbed under delegation). The content-origin field (`origin:` — the taint field marking an article's authoritative source) is part of the derivation block schema validated during this gate.

**Pass.** All four criteria + explicit operator approval recorded as a raw decision file.

**Class.** A (migration seeding declares unconsumed history absorbed — the single most dangerous step in the whole migration). **Gates.** Blocking gate between P1-dry and P1-live.

#### MIG-2 · PHASE-ADVANCE CHECKLIST (P0→P5)

**Property.** Each migration phase advances only when its named gate set passes (table in §3). Mechanically: a `deploy/check-phase-gate.py --phase P<n>` runner that executes the phase's gate list and prints one PASS/FAIL line per gate — no phase advance on memory or vibes (invariant 7: outcomes with evidence).

**Mechanics.** Thin orchestration over the tests above; exit 1 if any gate fails.

**Gates.** Pre-phase-advance, P0 through P5.

---

## 2. Required-coverage cross-check

| Required item | Covered by |
|---|---|
| 1 Golden replay + nondeterminism decision | GOLD-1 (full deterministic accounting replay; legacy-assumed seeding + content-audit gate + absorbed-without-source on audit ladder F14; consumed_status stamped at P1 F12; (event,view,obligation_reason) residue keys; mandatory injection-list review section F14) + GOLD-2 (frozen 10-event routing subset; pick #8 REF-SKIPPED with entity match F1) — hybrid decision justified in GOLD-1 |
| 2 Conservation invariant | ACC-1 (ordered decision table, first-match-wins, 7 classes; REF-SKIPPED at priority 1 F1; PENDING_NOOP_CANDIDATE; residue snapshot rejection; zero-class FAIL) |
| 3 Journal integrity | ACC-2 (real-mode prose-hole injection), ACC-3b (JSON-sidecar corruption; 11 defect vectors + quarantine recovery case F5), ACC-3 (coherence + origin_max check), ACC-5 (1,000-receipt benchmark), ACC-6 (snapshot ≡ full deterministic state: disposition class + content-audit status + citation-translation records F3) |
| 4 Crash injection | OPS-1 (write-ordering contract, both directions) |
| 5 Concurrency drill | OPS-2 (lockfile/stale/override) + OPS-3 (stage-only vs salted tree) + OPS-6 (LOCK-COMMON-DIR: seq isolation across worktrees + prev_record_hash chain — legal concurrent-worktree case is PASS F4; gap/dup/mismatch fail loud) |
| 6 Formatter drill | CONTENT-4 (monthly; proves zero absorption-state loss) |
| 7 Absorb quality harness | LLM-1 (planted defects, ≥4/5 + d1/d3 floor, halt-below-threshold; migration content audit under VERIFY harness with planted-defect floor F13), LLM-2 (manifest-vs-diff + no-ops in scope: full body justified, T1 same-run artifact; conservative event_class default F6), LLM-3 (no-op sampling), LLM-4 (intake audit); cadence weekly batch, not per-compile |
| 8 Packet recall | ECO-1 (100% required + stale-T1 refusal negative) |
| 9 Migration dry-run gate | MIG-1 (abort criteria: >12 residue, unexplained lock-event residue, any sources-pair miss, dirty backfill; residue keyed (event,view,obligation_reason); mandatory injection-list review section with per-pair quoted receipt evidence + T1/lock-pair abort criterion F14) |
| 10 Skew guard | ECO-2 (both skew directions, pre-commit) |
| 11 No-op verification hole | ACC-4 + LLM-2 (no-ops in scope: full body + verifier artifact; T1 same-run; empty-diff no-op is FAIL; conservative event_class default F6) |
| 12 Substrate separation recorded | LLM-5 (structured verified block; verifier_vendor + verifier_model_id separate fields F18; derived from invocation metadata F17; spoofed-substrate fixture; named gate policy: model_id diff for routine T1, vendor diff for migration/design-gate) |
| 13 Corpus support (shipped-state prose) | LLM-6 (support_lines verbatim quoted text F16; must appear in cited artifact at pinned SHA; verify packet embeds resolved excerpt F16; standalone routing census script with input+output hash journaled F15; VERIFY re-runs and compares hash) |
| 14 Path containment | OPS-5 (symlink escape, .., absolute path, junction/subst root F10, NTFS ADS colon F10 — realpath both args before commonpath; fatal reject; TOCTOU accepted residual noted) |
| 15 Taint quarantine | ECO-7 (external-scrape/unknown excluded from ANY build/fix packet regardless of tier F8; T3 build/fix excluded, not bannered; only-tainted → exit 1) + ORIGIN-PROPAGATION (session-loop taint laundering closed F9) + EGRESS-CO-RESIDENCY (tainted packet + credentialed profile refused without sandbox attestation F11) |
| 16 P2 cost acceptance | OPS-4 (30-day replayed workload benchmark: p50/p95 wall-clock + token_cost vs old-path baseline; T1/T3 distribution preserved; not a single-event test) |
| Extras the failure classes demanded | ROUTE-1/2/3 (vocab, hub fan-out, aging), CONTENT-1 (mechanical deletion floor), CONTENT-2 (split citations — real line-1622 case), CONTENT-3 (verified reset), ECO-3 (CRLF caps), ECO-4 (idempotence), ECO-5 (cycle detection), ECO-6 (surface freshness), ACC-4 (manifest-vs-diff commit audit; conservative event_class default F6) |

---

## 3. TEST EXECUTION MAP

**Phase gates (run by MIG-2 `--phase`):**

| Phase | Blocking gates before advance |
|---|---|
| **P0** (caps + split foundations/plan-4.7) | ECO-3 (CRLF cap stability) · CONTENT-2 (split-citation integrity on both real splits) · OPS-5 (PATH-CONTAINMENT self-test: 5 attack vectors including junction-root + NTFS ADS F10 — must pass before any LLM-authored path is opened) · extended `check-frontmatter.py` self-test (KNOWN_KEYS grown — prerequisite, see CONTENT-4) |
| **P1** (entities.yaml + backfill + staleness/catalog) | MIG-1 dry-run gate (contains GOLD-1 with legacy-assumed + content-audit gate + absorbed-without-source on audit ladder F14 + mandatory injection-list review section F14 + consumed_status in derivation block F12) · ACC-1/ACC-2 self-tests (REF-SKIPPED at priority 1 F1) · ACC-3b (JOURNAL-SIDECAR-INJECTION self-test + quarantine recovery case F5) · GOLD-2 (pick #8 REF-SKIPPED-with-entity-match F1) · ROUTE-1 · ECO-2 · LLM-5 (SUBSTRATE-SEPARATION — separate vendor+model_id fields F18, invocation-metadata derivation F17 — derivation block schema ships at P1) · ORIGIN-PROPAGATION self-test (registration writer ships at P1 F9) · CONTENT-4 first run |
| **P2** (compile v2; acceptance = 30-day workload benchmark per OPS-4) | OPS-1 · OPS-2 · OPS-3 · OPS-4 (workload benchmark: p50/p95 + token_cost — replaces single-event acceptance) · OPS-6 (LOCK-COMMON-DIR: prev_record_hash chain + legal-concurrent-worktree PASS case F4) · ACC-4 (incl. conservative event_class default fixture F6) · LLM-2 (incl. conservative event_class default fixture F6) · ACC-5 benchmark · ECO-4 · plus one end-to-end smoke T1 lock with ACC-1 exit 0 |
| **P3** (reconcile + verify) | LLM-1 first passing batch (with the d1/d3 floor + migration content audit under VERIFY harness with planted-defect floor F13) · LLM-6 (CORPUS-SUPPORT: verbatim support_lines F16 + verify-packet-embeds-excerpt F16 + standalone routing-census script + hash comparison F15) · CONTENT-1 · CONTENT-3 |
| **P4** (assemble) | ECO-1 (all descriptors + negative) · ECO-5 · ECO-7 (TAINT-QUARANTINE: by task type F8, T3 build/fix excluded not bannered) · ORIGIN-PROPAGATION integration · EGRESS-CO-RESIDENCY (assemble.py refuses tainted packet + credentialed profile without sandbox attestation F11) |
| **P5** (receipts/locks as typed events) | GOLD-1 re-run over the enlarged ledger · ACC-1 re-baselined · `check-loop-state.py` extension self-test (reuse its `raw_informed_by` extractor — see below) |

**Every compile, forever (tripwires, all < ~10 s total):** ACC-1 conservation census (ordered decision table; 7 classes; REF-SKIPPED priority 1) · ACC-2 hole semantics · ACC-3b JSON-sidecar injection (from P2) · ACC-3 journal coherence + origin_max check · ACC-4 + LLM-2 manifest-vs-diff incl. no-op audit + conservative event_class default (pre-push) · LLM-5 substrate-separation check: vendor+model_id separate, invocation-metadata provenance (from P1) · CONTENT-1 deletion floor · CONTENT-3 verified reset · ECO-2 skew · ECO-4 idempotence echo · ROUTE-1/2/3 · extended `check-frontmatter.py` · `check-derivation.py` audit-pending T1 view report (from P1). **Every session start (flight-plan sweep):** ACC-1, ACC-3 (incl. consumed_status + audit-pending report), ECO-2, ECO-6, LLM-5, GOLD-2 self-test, `check-frontmatter.py`, `check-reference-integrity.py`, `check-loop-state.py` (existing).

**Scheduled drills (worktree, never live tree):** weekly — LLM-1 batch + LLM-3 + LLM-4 (one hub session, ~10 agent legs, sonnet for prep / non-haiku for verify per Model Economy); monthly — CONTENT-4 formatter, OPS-1 crash, OPS-3 stage-only, OPS-6 LOCK-COMMON-DIR (incl. legal-concurrent-worktree pass case); quarterly — OPS-2 concurrency, OPS-4 workload re-benchmark (if event distribution shifts), ACC-5 replay re-benchmark, golden-descriptor review (ECO-1 answer keys still current), EGRESS-CO-RESIDENCY drill.

**Implementation order — the three that buy the most safety first:**
1. **Extend `check-frontmatter.py`** (KNOWN_KEYS + v2 EXPECTED branch + schema_version) **before any derivation block exists** — the formatter class is the repo's one proven corpus-wide killer, and P1 mints 44 new machine-critical blocks into its blast radius. Smallest diff, highest leverage.
2. **`staleness.py` core: ACC-1 + ACC-2** (conservation predicate, hole semantics, the 3 real-mode fixtures, `--self-test`, `--report`) — every other test references its verdicts; nothing else is trustworthy until the accounting is.
3. **MIG-1 dry-run gate (with GOLD-1 inside)** — P1 seeding is the single irreversible step that can declare history absorbed wholesale; it must be rehearsed and operator-gated before it happens, not audited after.

**Extends vs duplicates (existing `deploy/` sensors):**

| Existing sensor | Relationship |
|---|---|
| `check-frontmatter.py` | **Extend** (KNOWN_KEYS, v2 EXPECTED branch, schema_version). Never build a second formatter sensor — one tripwire, one truth. |
| `check-reference-integrity.py` | **No change.** Its docstring deliberately keeps it narrow ("a near-silent sensor everyone trusts"). Dangling `cascades_to`/`built_from` checks belong to ROUTE-1/ACC-3, not here. |
| `check-loop-state.py` | **Extend at P5 only** — lock-events typing reuses its `raw_informed_by()` extractor, whose line-level fallback for YAML-hostile frontmatter is exactly the degraded-parse pattern `staleness.py` needs for the ledger. Its `LEGACY_LENIENT_FOLDERS` named-allowlist pattern is the template for ACC-2's known-holes list. |
| `compile-preflight.py` | **Extend by delegation** — add staleness/conservation JSON sections shelling to `staleness.py --report`, exactly as `review()` shells to `compile-review-sweep.py --report`. Preflight stays exit-0-degrade; the standalone sensors gate. Its `NEVER_SOURCE_TARGETS` regex and `parse_ok:false` fallback are inherited semantics, not duplicated logic. |
| `.claude/hooks/*` | **Unchanged.** Engine gates are preflight/sensor-layer; hooks stay the named-risk security perimeter. |

**New files this plan implies (for the build session's inventory):** `deploy/staleness.py`, `deploy/catalog.py`, `deploy/project.py`, `deploy/assemble.py` (engine — each with `--self-test`); `deploy/check-derivation.py`, `deploy/check-run-diff.py`, `deploy/check-split.py`, `deploy/check-phase-gate.py`, `deploy/check-path-containment.py`, `deploy/check-corpus-support.py`, `deploy/emit-routing-census.py` (sensors — standalone routing census, inputs+output hash journaled F15); `deploy/drill-{formatter,crash-absorb,concurrency,stage-only,replay-bench,planted-defects,golden-replay,migration-p1,workload-bench,lock-common-dir}.py` (drills); `deploy/test-fixtures/memory-engine/{receipts/sidecars,events,views,entities,descriptors,consumed-sets,paths}/` (fixtures, including the frozen `residue-p1.yaml`, `golden-10/expected.yaml`, 12 sidecar defect fixtures including quarantine-recovery F5, 5 path-containment attack fixtures including junction-root + NTFS ADS F10, ORIGIN-PROPAGATION fixtures F9, EGRESS-CO-RESIDENCY fixtures F11, LLM-5 spoofed-substrate fixture F17, LLM-5 same-model-id and same-vendor-design-gate fixtures F18).
