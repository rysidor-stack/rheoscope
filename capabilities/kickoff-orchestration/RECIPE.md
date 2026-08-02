# Capability: kickoff-orchestration

## 1. WHAT IT IS

Orchestrators for the project start lifecycle: `/init` (kickoff interview) and `/roadmap` (phase arc refinement). Both deferred in v1.0 — the manual INIT.md protocol replaces them.

## 2. WHEN A PROJECT NEEDS IT

When operators have run INIT.md manually 2–3+ times and the pattern stabilizes enough that the orchestrators can be authored from validated practice.

## 3. WHEN A PROJECT DOESN'T

Right now: nobody. The INIT.md manual protocol is the v1.0 substitute. When the eventual /init exists, projects that want fully-automated kickoff use it; projects that prefer manual walkthrough keep using INIT.md.

## 4. STATUS

deferred (entirely — no /init or /roadmap code ships in v1.0).

## 5. PROVENANCE

Designed during v1 build plan authoring (v1 Phase 4). Three orchestrators originally planned (/init, /roadmap, /grill). /grill kept and lightly Pocock-forked (now in `capabilities/stress-testing`) (since renamed /preflight and graduated to `core/skills/preflight/`). /init and /roadmap dropped in v1.0 per the reformulation because:
- Their design was the most speculative part of v1 — no working version in the source project.
- Manual INIT.md proves the question framework first; the orchestrator gets authored from validated practice.

## 6. DEPENDENCIES

- core/governance/ (CLAUDE.md, PROJECT-COMPASS, HARDCONSTRAINTS) — what /init and /roadmap populate
- knowledge-os (CONTEXT.md, wiki structure) — what /init seeds
- /preflight — already exists; /init and /roadmap would reuse the interview pattern

## 7. AUTHORING GUIDE

See per-orchestrator recipes in `deferred/init.RECIPE.md` and `deferred/roadmap.RECIPE.md`.

**Anti-pattern from v1 (must avoid):** don't author SKILL.md files for orchestrators that have no working version anywhere. v1's most aggressive failure was shipping full SKILL.md prose for /init, /roadmap, /grill, /discover, /history, /recompile, /product-scan when only 3 of 10 had working versions in the source project. Build from validated practice, not from imagined practice.

## 8. KNOWN LESSONS

- Don't author until validated practice exists.
- The kickoff is the first interaction an operator has with a project after instantiation — its UX matters more than internal-orchestrator polish.

## 9. OPEN QUESTIONS

- Should /init be one orchestrator with multiple modes, or three skills (/init-identity, /init-arc, /init-references)? The reformulation rejected v1's "three contexts in one skill" pattern for /preflight; same concern applies here.
- Does /roadmap need a separate skill, or is it /preflight's "phase-kickoff against the first phase article" mode?

## 10. MIGRATION STEPS

(Empty — capability not yet built.)
