# Capability: knowledge-os

## 1. WHAT IT IS

A compounding-knowledge pipeline, in two layers.

**Content layer** (what an operator interacts with): raw session inputs and references flow into
`raw/`, get compiled into structured wiki articles by `/compile`, get graded against the roadmap
by `/audit`, and surface in per-session briefings. `/discover` reads the accumulated corpus and
surfaces connections, gaps, and drift it implies but nowhere states, filing proof-carrying draft
events back into the same pipeline. `docs/wiki-schema.md` governs the shape of all of it.

**Engine layer** (what makes the content layer honest and cheap to maintain at scale): a
deterministic pipeline under `deploy/` — the memory engine (v3) — that registers every raw event,
compiles view deltas through mechanically-validated ABSORB/RECONCILE/VERIFY stages, cross-vendor
verifies every absorption, and runs a conservation census that proves nothing was silently lost.
The wiki accumulates knowledge over months; the engine is what keeps that accumulation provably
honest instead of merely hoped-honest. See `docs/engine/OPERATIONS.md` for how to run it. Its
major pieces, one sentence each:

- **`compile-v2.py` / `compile-backends.py` / `compile-core.py`** — the compile orchestration loop
  (ABSORB/RECONCILE/VERIFY over injectable LLM backends), the live absorb/verify dispatch + the
  HUMAN-GATE, and the shared primitives (lockfile, journal, stage-only commit) underneath both.
- **`staleness.py`** — the conservation census: sorts every event into exactly one of seven
  ordered classes and fails loud (`problems`/`new_holes`) if any accounting doesn't add up.
- **`register-intake.py`** (+ `backfill-registrations.py`, `origin.py`) — appends the typed,
  append-only registration record (origin, event class) for new events; the one-time bulk mint is
  a separate, distinct operation from this incremental delta path.
- **`catalog.py` + `entities.yaml`** — the governed entity vocabulary and its integrity tripwire
  (no alias collisions, no dangling routing targets, dead vocabulary named not silent).
- **`assemble.py`** — the read-path packet assembler; refuses to serve an unverified-T1 view to a
  build/fix task.
- **Sensors** (`check-*.py`, ~17 scripts) — per-gate conformance checks (frontmatter, derivation
  staleness, substrate separation, corpus-support re-verification, run-diff, phase-gate scoring)
  — see `docs/wiki-schema.md` §17.2 for the family summary.
- **Drills** (`drill-*.py`) — fixture-driven end-to-end rehearsals proving the above hold under
  concurrency, crash injection, migration replay, and adversarial planted defects.

## 2. WHEN A PROJECT NEEDS IT

- The project runs for months or longer.
- Knowledge accumulates across multiple sessions and people.
- Decisions made in week 4 need to be findable in month 8.
- The project has identifiable "domains" of knowledge that benefit from organization.
- (Engine layer specifically) the corpus is large or fast-growing enough that "re-read everything
  before every edit" is no longer cheap, and provable non-loss matters more than convenience.

## 3. WHEN A PROJECT DOESN'T

- Solo experiments under 2 weeks.
- Single-file projects.
- Projects where the AI session is the entire knowledge layer (transient research, one-shot writing).
- Projects without compounding knowledge (e.g., processing a fixed dataset once).
- The engine layer specifically is heavier than most projects need on day 1 — a fresh instance can
  run `/compile` + `/audit` alone for a long time before the engine's conservation guarantees earn
  their operational overhead (see § 10 Day-1 vs. migration below).

## 4. STATUS

**Content layer — extracted:** `/compile`, `/audit`, `docs/wiki-schema.md`, `/discover`.
**Engine layer — extracted 2026-07-11** from the proven dogfood fork: the full
`deploy/` sensor+pipeline layer (compile orchestration, the conservation census, delta
registration, the entity catalog, the read-path assembler, the sensor and drill families) plus
the `docs/engine/*.md` contract specs. **Deferred:** `history`, `recompile` (see
`deferred/*.RECIPE.md`).

**Provenance of the extraction, cited:** 39 build gates (P0–P5, all green) before live operation
began; then live-operations sessions 1–10 (2026-07-07 → 2026-07-10) ran the full loop on real
content — 92 real events registered onto the chain, a proven subset (26 of the 92) routed,
compiled, and cross-vendor verified across the pilot + two batches, closing with **zero
unverified absorption debt** anywhere in the wiki at that point (every view the engine had
touched, including its three "parked" partially-absorbed spans, closed via co-absorption; every
non-confirm across the run, checked by hand, was a substantively correct catch — omission,
fabrication, stale contradiction — never a false positive). The remaining ~66 registered-but-
uncompiled events were an operator-decided posture change to **available-on-demand**, not a gap —
the loop had already proven itself across every path class. See `docs/engine/OPERATIONS.md` for
the operating practice and `harness-backlog.md` v3.0-20 through v3.0-23 for the residuals that
stayed honest gaps rather than getting papered over.

**Day-1 vs. migration** (see § 10 for the mechanics): a fresh instance with no existing corpus
starts directly in engine steady-state — register each raw event, then compile it through the
loop. A project migrating an **existing** wiki corpus onto the engine is a different, heavier
operation (backfills: origin, entity registration, derivation blocks; phase gates: MIG-1 and
siblings) — per `docs/engine/*.md` specs, not this RECIPE.

## 5. PROVENANCE

Content layer extracted from a production project, where `/compile` and `/audit` ran in
production for months; `docs/wiki-schema.md` is the generalized form of that project's wiki
structure documentation. `/discover` was designed deferred, then built and proven on the
dogfood fork (2026-07-10) and now ships extracted (see
`deferred/discover.RECIPE.md` for the supersession record). The engine layer is a from-scratch
v3 design (`docs/engine/memory-engine-v3-spec.md`, authored 2026-06-11, revised to v3.2 after two
cross-vendor design passes) built and proven on the same fork, not inherited from any production
wiki — the fork **is** the proving ground, not a source corpus being migrated.

## 6. DEPENDENCIES

- `core/methodology/` (for tier definitions)
- `CONTEXT.md` at project root (canonical glossary, Pocock format — only `/preflight` writes per
  Decision V2-8)
- `raw/`, `wiki/`, `roadmap/` directories
- `references/` directory + `references/README.md` for catalog
- **python 3** — required by every `deploy/` engine script and sensor (stdlib-only; no runtime
  package dependency).
- **node >= 18** and the **codex CLI** — required for cross-vendor VERIFY legs (the engine's
  honesty layer calls the same `bridge` transport the `/cross-check` core skills use). `codex`
  requires a ChatGPT/GPT subscription and a one-time `codex login`. The engine layer is installed
  but inert without these — compile/register still work; VERIFY legs fail loud until present.
- **git** — the engine's stage-only commit discipline and worktree-per-shard practice
  (`docs/engine/OPERATIONS.md`) assume a real git working tree; linked worktrees are the
  recommended shard-isolation mechanism.

## 7. AUTHORING GUIDE

(Not applicable — capability is extracted.) Deferred capabilities (`history`, `recompile`) carry
their own authoring guides in their `deferred/*.RECIPE.md` files.

## 8. KNOWN LESSONS

- The compile pipeline routes raw files by their `source:` tag and any `domain:` frontmatter.
  Operators MUST populate `wiki_domains` in project.yaml for routing to work — empty
  `wiki_domains` means /compile has nowhere to route knowledge.
- `/audit` produces a REVIEW.md queue, not direct article edits. Operators must process the queue
  (50 unprocessed entries is worse than no audit).
- Article-count thresholds (50, 100, 150) trigger different concerns. Past 150, terminology drift
  becomes the dominant maintenance load.
- The receipts directory grows unbounded. Plan for compaction at month 3+ (per verifier review §5).
- **The loop-closure-gate lesson (engine layer, backlog v3.0-21).** Every one of the engine's 39
  build gates proved the frozen SEED — the one-time bulk mint, drills in fixture roots, rehearsals
  in discarded worktrees. None of them proved the LOOP could close on genuinely new content. A
  validator's own self-test (`check-origin-propagation.py`) went green while the production writer
  it was meant to validate had never shipped — a component gate can pass with the component
  absent. The generalizable rule: **a gate that only exercises the checker, not the thing being
  checked, is not evidence the thing works.** Only a real end-to-end cycle (intake → register →
  route/triage → compile → verify → census → journal, on content the corpus hadn't already
  absorbed) is a loop-closure gate. Apply this skepticism to any new engine gate before trusting it.

## 9. OPEN QUESTIONS

- Should /compile write to CONTEXT.md? Currently /preflight is the only writer (Decision V2-8).
  Revisit in v1.x if terminology drift becomes painful.
- Wiki article maximum size — the source project has no enforced limit. Some projects may benefit
  from compaction at fixed thresholds.
- **Engine residuals, honest and current (not fixed by this extraction):**
  - **R-1 · production session-loop registration writer is not built.** Registration today is
    driver- or operator-mediated (`deploy/register-intake.py`, trusted-batch/delta mode), run
    deliberately after events land — not an automatic writer stamping origin at the moment a live
    session loop authors a raw event. Cite backlog v3.0-21.
  - **Re-baseline is not an operator-runnable operation.** Re-minting the census baseline (ACC-1)
    for an instance is a manual, code-level act today, not a shipped command. Cite backlog v3.0-21.
  - **Golden descriptors must be authored per-instance.** The fork's alias-reachability fixtures
    (`deploy/descriptors/`) encode its own vocabulary; a fresh instance starts with none.
  - Vocabulary-aging sensors (ROUTE-3/LLM-4) remain spec'd, not built; UNROUTED-triage is manual.

## 10. MIGRATION STEPS

When wiring this capability into a fresh project (init.ps1/init.sh do steps 1–4 automatically):

1. Copy `capabilities/knowledge-os/extracted/compile/` → `.claude/skills/compile/`
2. Copy `capabilities/knowledge-os/extracted/audit/` → `.claude/skills/audit/`
3. Copy `capabilities/knowledge-os/extracted/discover/` → `.claude/skills/discover/`
4. Copy `capabilities/knowledge-os/extracted/wiki-schema.md` → `docs/wiki-schema.md` (after
   substitution)
5. Copy `capabilities/knowledge-os/extracted/deploy/` → `deploy/` (the engine: compile
   orchestration, the conservation census, delta registration, the entity catalog, the read-path
   assembler, sensors, drills)
6. Copy `capabilities/knowledge-os/extracted/engine/*.md` → `docs/engine/` (the contract specs +
   `OPERATIONS.md`, the validated-practice runbook)
7. Operator during INIT.md walkthrough: populate `wiki_domains` in project.yaml.
8. Operator during INIT.md walkthrough: drop initial reference material into `references/` and
   catalogue in `references/README.md`.
9. First `/compile` run happens after first session writes a raw file.

**Day 1 (fresh instance, no prior corpus):** steps 1–9 above are the whole story. The engine
starts in steady-state from event zero — register each raw event as it lands
(`deploy/register-intake.py`), then run the compile loop (`docs/engine/OPERATIONS.md`). There is
no backfill because there is nothing to back-fill.

**Choosing the lighter start.** A project may reasonably start at the content-layer `/compile`
alone (§ 3 above) and adopt the engine later — fine for small projects, and the honest cost is
worth naming: views created without a derivation block accrue backfill debt from the very first
article, not just once the corpus is "large enough to notice." That debt is exactly what a
migration (below) has to pay down later — origin backfill, registration minting, derivation
blocks on existing views, the MIG-1 phase gates — so deferring the engine defers the payment, it
doesn't cancel it. Running `deploy/register-intake.py` from day 1 regardless of which layer a
project is otherwise using keeps the registration ledger current either way and shrinks whatever
migration eventually follows. See `docs/engine/OPERATIONS.md` for the operating practice this
trades against.

**Migration (existing wiki corpus adopting the engine mid-project):** heavier and out of scope
for this RECIPE's day-1 steps — it requires backfilling `origin` (the F7 rule), minting
registration records for the whole existing ledger (the one-time bulk mint, distinct from
day-1's incremental path), backfilling derivation blocks onto existing views, and passing the
phase-boundary gates (MIG-1 and siblings) before the engine is trusted against that corpus. Follow
`docs/engine/memory-engine-v3-spec.md` §13 (the phase table) and `docs/engine/memory-engine-v3-
test-plan.md` for the gate sequence; do not improvise a migration path.
