# Methodology

Project-neutral methodology docs. Loaded by role.

- **`execution-engine.md`** — How builds run. Controller behavior, the four tier protocols, the six autonomous-execution principles, prompt templates, recovery, red flags. Load when executing build increments.
- **`verification-architecture.md`** — How correctness is proven. The Builder/Verifier/Runner firewall, verification spec format, the Verification Spec Drill, verifier agent behavior, runtime monitor patterns, business-logic vs sync-adapter layer separation. Load when authoring verification specs or planning verification.
- **`manifest-driven-builds.md`** — the ratified manifest doctrine (v2.1, verbatim) + harness incorporation annex. Load when scoping which layers a build or design freeze touches.
- **`manifest-format.md`** — the operational behavioral-manifest contract: frontmatter, rows, INDEX, amendments, twin-build, tooling. Load when authoring, certifying, or sweeping a surface's manifests.
- **`flight-plan-template-v6.md`** — Blank-form template for a per-project Flight Plan. Duplicate to `wiki/flight-plans/<phase-slug>-flight-plan.md` when starting a project or phase.
- **`tier-definitions.md`** — Standalone reference for T1–T4 tier definitions, with examples customizable via `project.yaml.tier_examples`.
- **`materialization-doctrine.md`** — When prose earns a machine layer: the second-query trigger, the paired-drift-sensor requirement, sources-win, never-ahead-of-demand. Load when considering a derived projection, index, or generated artifact over the corpus.
- **`five-pass-method.md`** — The research/reasoning discipline: five passes (landscape, break template, unconstrained, constrained, totality), per-pass Done-when exit tests, the refusability doctrine for codifying depth (show-the-rejects, totality read-back, unconstrained-ideal note), and where each pass lives in the harness. Load when researching options or reasoning through a decision that is not yet an execution plan. Runnable on demand as the `/reason` core skill.
- **`HOW-TO-USE-FLIGHT-PLAN.md`** — Operational guide. How operators and CC sessions interact with the flight plan in practice.
- **`rollback-kill-switch.md`** — Enablement precondition doctrine for autonomous capabilities. Defines the required kill-switch shape and rollback shape. Load before enabling any autonomous capability.
- **`least-privilege-isolation.md`** — Sibling enablement precondition: credential scope and data isolation for autonomous capabilities. Non-production default, least privilege, pinned automation config, declared high-blast-radius surfaces. Load before enabling any autonomous capability.
- **`spend-governance.md`** — Third sibling enablement precondition: provider-side spend cap, soft per-run ceiling, stop conditions, and per-capability spend attribution. Two-layer model (live tally + provider hard cap). Load before enabling any autonomous capability.
- **`specs/`** — Per-increment spec templates.
  - `build-spec.md.template` — What to build. Fed to Builder sessions.
  - `verification-spec.md.template` — What "correct" means. Authored via the Verification Spec Drill (see `verification-architecture.md` Part 3). Fed to Verifier sessions.

## Loading order by role

| Role | Load |
|------|------|
| Operator (interactive) | flight plan only |
| Operator (writing a verification spec) | flight plan + `verification-architecture.md` |
| Operator (enabling any autonomous capability) | `rollback-kill-switch.md` + `least-privilege-isolation.md` + `spend-governance.md` + `core/governance/KILL-SWITCH.md` + `core/governance/AUTOMATION-ISOLATION.md` |
| CC Controller | flight plan + `execution-engine.md` |
| CC Controller (autonomous capability active) | flight plan + `execution-engine.md` + `rollback-kill-switch.md` + `least-privilege-isolation.md` + `spend-governance.md` |
| CC Verifier | `verification-architecture.md` + the specific verification spec |
| CC Builder | `execution-engine.md` (principles section) + the specific build spec |
| CC Runner | test suite paths + deployed/built artifact |
| Code Quality Reviewer | `code-conventions.md` (if present) + diff under review |
| Any session researching options or reasoning through a decision | `five-pass-method.md` |
