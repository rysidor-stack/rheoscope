# How to use the Flight Plan

Operational guide for working with the per-project Flight Plan. Companion to `flight-plan-template-v6.md`.

## When to create a flight plan

- **At project start.** Duplicate `flight-plan-template-v6.md` to `wiki/flight-plans/<project-slug>-flight-plan.md` (or to your project's flight-plan directory).
- **At major phase boundaries.** For multi-phase projects, one flight plan per phase: `<project-slug>-phase-<N>-<slug>-flight-plan.md`. Phase-N's flight plan inherits decisions from phase-(N-1) but starts with a fresh Layer 1 Dashboard.

**Authoring the plan is the kickoff session's job, not any orchestrator's.** `/preflight` (core skill) stress-tests and stamps the roadmap phase article but stops at its report — it does not itself author the flight plan (see its own Boundaries). `/flight-plan` is read-only except for regenerating `SESSION-BRIEFING.md` — it only ever reads a flight plan that already exists (see its Rules). The kickoff session is what closes the gap: after `/preflight` stamps the phase article, the same session duplicates `flight-plan-template-v6.md` and fills it in using the preflighted article as source — Layer 2 in particular draws directly from preflight's findings. `/flight-plan` then surfaces the authored plan every session after.

## The four layers, ordered by volatility

1. **Dashboard (Layer 1)** — what's true right now. Rewritten every session before closing. Never patched, always replaced. If a session reads only Layer 1 and acts on it, the session should not produce a corrupted result.
2. **Active Work (Layer 2)** — increments in progress, open questions, recent decisions. Updated when things change.
3. **Reference (Layer 3)** — intent, spec pointer, dependencies, methodology routing, code conventions, completed phases. Write-once or write-rarely.
4. **Session Log (Layer 4)** — append-only black box. Every session writes one entry: Did / Decided / Verified / Flagged / Next.

## The single rule that matters every session

**Before closing, rewrite Layer 1 fresh.** Patching Layer 1 produces drift — fields contradict each other, "next action" doesn't match "status". Rewriting forces re-synthesis of the project's current state.

## Hold points

A hold point fires at every phase transition. Four checks run, in order:

- **A — Depth Check.** What's undercooked? Name items, state why each is risky, recommend a deepening pass. Operator decides.
- **B — Connection Surfacing.** What connections weren't designed for but matter? Each connection references two or more specific items.
- **C — Verification Audit.** T1–T2 increments without passing verification runs are blockers. T3 increments without runner reports are flags. T4 increments pass on builder self-test + regression. Active runtime-monitor alerts are blockers.
- **D — RTDT Cut.** What's overbuilt? State what the project loses if cut, and whether the loss matters for the success definition.

The phase advances when AI and operator both agree the phase is done. No checklist theater. The judgment is: "would a cold session starting from this flight plan have everything it needs to do the next phase's work?"

## Compaction at hold points

The flight plan grows unbounded if nothing is pruned. At each hold point:

1. Layer 4 session entries since the last hold point compress into one summary entry.
2. Resolved Open Questions move to a `### Resolved` sub-section or get deleted.
3. Recent Decisions older than two phases prune (they live in the Layer 4 archive).
4. Layer 1 Dashboard rewrites fresh.
5. Verification status for completed increments compresses to one line in `Completed Phases`.

Target: under ~300 lines of active content. If you exceed that, something static is being treated as active. RTDT.

## Session contracts (summary)

| Session type | Opens by reading | Closes by writing |
|---|---|---|
| Interactive | Layer 1 + Layer 2 | Layer 1 (rewritten fresh) + Layer 2 (if changed) + Layer 4 (one entry) |
| CC Controller | Layer 1 + Layer 2 + `execution-engine.md` | Layer 2 (per-increment status) + Layer 4 (per session). Does NOT rewrite Layer 1 — that's for the next interactive session. |
| CC Verifier | `verification-architecture.md` + verification spec | Tests under `tests/verified/[increment]/`, plus Layer 4 entry. Does NOT see build specs or feature code. |
| CC Builder | `execution-engine.md` (principles) + build spec | Code + Layer 4 entry. Does NOT see verification tests or specs at T1–T2. |
| CC Runner | Test suite paths + build | Results under `results/`. Reports PASS/FAIL only. Does NOT fix or interpret. |

## When the flight plan stops being useful

If the flight plan feels noisy or stale every session, it's compacting too rarely. Force a hold point. Apply the compaction protocol. If after compaction it still feels wrong, the project may have outgrown the single-file cockpit — consider whether you need a second flight plan for a separate workstream.

If you find yourself updating Layer 3 every session, that's a sign Layer 3 contains active state, not reference. Move it.

## Cross-references

- `execution-engine.md` — what CC sessions actually do during build
- `verification-architecture.md` — how verification specs are authored
- `tier-definitions.md` — T1–T4 reference, with customizable per-project examples
