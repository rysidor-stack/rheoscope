# Architecture

*The Rheoscope — a kernel + capabilities template for LLM-orchestrated projects. This document explains the model, what's in it, and what it deliberately is not.*

*verified-against: 3.0 (2026-07-28) — this stamp is checked by `/doctor` (docs-stamps) in every instance; refresh this document from the template at each migration (MIGRATION.md standing step; backlog v3.0-75).*

---

## Overview

The harness is organized as **core + capabilities**.

**Core** is the invariant kernel: methodology (execution discipline + verification architecture), governance (orientation documents every project needs), handoffs (substrate-separated decision authoring), security perimeter (PreToolUse hooks), and a single canonical glossary (`CONTEXT.md`). Core ships in every instantiation. There is no toggle.

**Capabilities** are opt-in. Each capability has a `RECIPE.md` and one of three states:

- **extracted** — working code lifted from a real project. High confidence; ships with migration steps.
- **prototype** — working content shipped without validation from a real project. Medium confidence; the example is illustrative, not load-bearing.
- **deferred** — recipe only, no code. The thinking is captured; the build happens when a real project pulls it onto its roadmap.

**Inclusion is the toggle.** Each project's `project.yaml.capabilities` block declares which capabilities pull in at instantiation. The `init.{ps1,sh}` script copies enabled capabilities' `extracted/` contents to runtime locations, propagates each enabled capability's `deferred/*.RECIPE.md` files to `docs/recipes/<capability>/`, and deletes the `capabilities/` catalog from the instantiated project. There is no separate enable/disable mechanism — the toggle is whether init copies the capability or not.

This shape was chosen over v1's layer-toggle design because v1's "conditional on layer enabled" was specified but never implemented; capabilities-as-directories makes the toggle structural rather than logical.

---

## Core (Zone 1)

Six elements ship in every instantiation. Each is here because it is invariant — every project the harness is meant for benefits from it identically. See `core/<element>/README.md` (where present) for element-level orientation.

### methodology — `core/methodology/`

The Flight Plan / Builder–Verifier–Runner / tiered verification kernel. Files: `execution-engine.md.template`, `verification-architecture.md.template`, `flight-plan-template-v6.md`, `tier-definitions.md.template`, `five-pass-method.md`, plus `HOW-TO-USE-FLIGHT-PLAN.md` and `INDEX.md`. The per-increment specs ship under `core/methodology/specs/` (`build-spec.md.template`, `verification-spec.md.template`).

Methodology is core because every project the harness targets produces something that benefits from explicit verification — tiered effort, substrate separation between Builder and Verifier, and Read-the-Damn-Thermometer scope discipline. There is no project shape in scope where the methodology kernel adds nothing.

### governance — `core/governance/`

Three orientation documents authored fresh per project: `CLAUDE.md.template` (the session contract read by every agent session — the root `AGENTS.md` pointer routes non-Claude agents here), `PROJECT-COMPASS.md.template` (decision authority, escalation paths, personnel), `HARDCONSTRAINTS.md.template` (invariants that cannot be violated). All `*.template` — operators populate during the INIT.md walkthrough.

Governance is core because every project needs a place where canonical decisions live. Without `CLAUDE.md` the first session has no orientation; without `PROJECT-COMPASS.md` the first decision has no recorded authority; without `HARDCONSTRAINTS.md` invariants live in conversation and rot.

### handoffs — `core/handoffs/`

The three-document substrate-separation ritual for runtime decisions: `HANDOFF-AUTHORING.md` (when to open a handoff), `HANDOFF-RECEIVING.md` (Builder/Verifier substrate boundaries), `HANDOFF-CLOSING.md` (ADR landing). Plus `README.md` orienting the three files together.

Handoffs are core because decisions have no executable test: for code, deterministic verification is the primary firewall and substrate separation is defense-in-depth — but for decisions, substrate-separated review is the primary firewall; nothing else can catch a wrong one. Toggling handoffs off would remove that firewall. There is no "no handoffs" mode.

Decisions emerging from a closed handoff land under `docs/adr/<YYYY-MM-DD>-<n>-<slug>.md`, with `informed_by:` and `decision_method:` (one of `handoff | preflight | direct` — pre-2026-07-09 ADRs carry `grill`; same skill) frontmatter fields.

### security perimeter — `core/security/`

Two PreToolUse hooks (`block-dangerous-bash.sh`, `block-env-writes.sh`) that block egress and `.env*` writes from Claude Code sessions, plus the `settings.local.json.example.template` that wires them in. `.env.example` and `.env.sample` are exempt from the env-write block by design (Decision V2-14). Test fixtures live under `hooks/test-inputs/`.

Security is core because defense-in-depth applies regardless of which capabilities a project enables. The hooks block at the substrate boundary — they don't depend on knowledge-os or anything else.

### CONTEXT.md — `CONTEXT.md.template` (harness root)

A single canonical glossary at the project root, adopted from `mattpocock/skills` Pocock-format (MIT) per Phase 2.1. `/preflight` (renamed from `/grill` 2026-07-09) is the only orchestrator that writes to `CONTEXT.md` (Decision V2-8, carries over under the new name); `/compile` is read-only against it. Operators may also edit manually during sessions — including `INIT.md`'s kickoff-interview population of the file, which is the same human-mediated case: correct and expected, not an exception to Decision V2-8. V2-8 scopes which *orchestrator* writes (one, `/preflight`); it has never restricted the human operator's own hand.

CONTEXT.md is core because every project needs one canonical place for "what does this term mean here." Without it, terminology drift across sessions becomes invisible.

### onboarding — `core/onboarding/`

A self-contained orientation surface for an operator new to the harness: `TOUR.md` (a staged walkthrough — WHY / WHAT / FIRST-WEEK / WHEN-X-HAPPENS), `GLOSSARY.md` (a human-readable glossary of harness terms, distinct from the project's own `CONTEXT.md`), and `SYSTEM-MAP.html` (a self-contained, double-click-to-open interactive map of the harness's zones and flows). Ad-hoc questions route to `/orient`, a core skill that answers only by reading the installed artifacts, citations required.

Onboarding is core because every operator starts a stranger to the harness once; without a self-explaining surface they reconstruct the model from source, file by file. Governed by the same docs-truth discipline as the rest of core — the tour and map describe the installed harness, not an aspirational one.

---

## Capabilities (Zone 2)

Six capability slots ship in `capabilities/`. Two are toggled via `project.yaml.capabilities` (`knowledge-os`, `code-conventions`); three are documentation-only and not toggled (`kickoff-orchestration`, `operate-sentinel`, `decorrelated-review`); one (`stress-testing`) is retired — its key is still accepted in `project.yaml.capabilities` but wires nothing, the payload having graduated to the `/preflight` core skill. See `capabilities/INDEX.md` for the toggle table and per-capability `RECIPE.md` files for full provenance, dependencies, known lessons, and migration steps. (`/flight-plan`, the `/handoff` orchestrator with `/handoff-close` as its close-leg protocol (v3.0-78 collapse), `/log-backlog`, `/preflight`, `/reason`, `/orient`, `/cross-check`, `/cross-check-loop`, `/doctor`, `/conformance`, `/sweep`, and `/standing-loop` are **core skills** wired to `.claude/skills/` unconditionally — not capabilities; `bridge`, the transport library the cross-vendor skills call, is core too, with no slash command of its own.)

### knowledge-os (extracted + deferred)

The compounding-knowledge pipeline. **Extracted:** `/compile` (raw intake → wiki article), `/audit` (article-quality review), `/discover` (relate/derive/gap/trace/introspect over the corpus), `wiki-schema.md` (the structural contract), and the engine deploy layer (fork-proven v3 memory engine: registration chain, compile pipeline with cross-vendor verify, conservation census, read-path assembler, sensor+drill suite; specs at `docs/engine/`). **Deferred:** `history` (receipt compaction), `recompile` (article rewrite). See `capabilities/knowledge-os/RECIPE.md` and the two remaining `deferred/*.RECIPE.md` files for the design content captured for v1.x build.

### build-orchestration — retired (now core)

The build methodology kernel (`core/methodology/`) is always loaded. `/flight-plan` — the per-session briefing cockpit that was this capability's only payload — now ships as a **core skill** (wired to `.claude/skills/flight-plan/` unconditionally, degrading gracefully when knowledge-os is off). There is no `build-orchestration` toggle.

### stress-testing — retired (now core)

`/preflight` — the stress-testing / evidence-interview orchestrator that was this capability's only payload — graduated to a **core skill** on 2026-07-10, wired to `.claude/skills/preflight/` unconditionally (renamed from `/grill` 2026-07-09; same Pocock-forked lineage, evidence-sweep-first protocol). The `stress-testing` key is still **accepted** in `project.yaml.capabilities` but wires nothing at init — a no-op with a notice, the same pattern documented above for `build-orchestration`. There is no working `stress-testing` toggle.

### code-conventions (prototype)

A single worked example: `examples/typescript-nextjs.md`. Prototype status because the content is illustrative, not validated as load-bearing by any real project. Operators are expected to replace or extend it. See `capabilities/code-conventions/RECIPE.md`.

### kickoff-orchestration (deferred entirely, docs-only)

No extracted code. `/init` and `/roadmap` are deferred recipes — the manual `INIT.md` protocol substitutes for them in v1.0. See `capabilities/kickoff-orchestration/RECIPE.md` for the capability-level orientation and the two `deferred/*.RECIPE.md` files for design content. This capability is **not toggled** in `project.yaml.capabilities`; its recipes propagate unconditionally to `docs/recipes/kickoff-orchestration/` regardless of operator selection.

### operate-sentinel (docs-only)

The operate-phase execution mode: a scheduled Controller that runs the project's *existing* runtime monitors unattended on a cron/CI trigger, writes a receipt per run, and — in a later, separately-gated phase — opens gated pull requests for low-blast-radius maintenance. A sentinel is not a new review role: it is the Controller running on a schedule instead of in a session, and any remediation it proposes still flows through the normal tier-appropriate build-and-verify path. Phase 1 (read-only receipts) is adoptable now via `capabilities/operate-sentinel/deferred/sentinel-phase-1.RECIPE.md`; phase 2 (write-back remediation) is designed-bounds-only, not yet adoptable. See `capabilities/operate-sentinel/RECIPE.md`. **Not toggled** in `project.yaml.capabilities`; its recipes propagate unconditionally to `docs/recipes/operate-sentinel/`.

### decorrelated-review (docs-only, deferred entirely)

Two opt-in orchestrators — `/harden` (checkable-loop: drive an artifact with checkable defects to a mechanically-cleared survivor via decorrelated critics) and `/frame` (open-ended map: fan diverse whole-problem framings into an unreconciled disagreement map the human synthesizes) — the generative sibling of the handoff engine, riding the same decorrelation ladder, verdict schema, and wide-then-deep portfolio shape, pointed at producing and pressure-testing reasoning instead of locking decisions. **Status: deferred entirely** — no `SKILL.md` ships. One of its two core dependencies (automated cross-vendor verification) is now built and in-repo as the `/cross-check`/`/cross-check-loop` core skills (ADR #7); the other (a packaged spawn-N-decorrelated primitive + verdict schema) is still specced-not-built, so both skills stay deferred. See `capabilities/decorrelated-review/RECIPE.md`. **Not toggled** in `project.yaml.capabilities`; its two deferred recipes propagate unconditionally to `docs/recipes/decorrelated-review/`.

**Not a capability: handoffs.** `handoffs` ships in `core/`. It is not in the `project.yaml.capabilities` block; there is no toggle.

---

## Recipe format

Every capability's `RECIPE.md` and every deferred orchestrator's `<name>.RECIPE.md` follows a ten-field structure:

1. **WHAT IT IS** — one paragraph, no jargon.
2. **WHEN A PROJECT NEEDS IT** — explicit positive triggers.
3. **WHEN A PROJECT DOESN'T** — anti-triggers (RTDT discipline).
4. **STATUS** — `extracted | prototype | deferred`.
5. **PROVENANCE** — extracted: source project + commit; deferred: the conversations/patterns informing the idea.
6. **DEPENDENCIES** — other capabilities or core elements required.
7. **AUTHORING GUIDE** — deferred only: what to build, what to validate, anti-patterns from prior attempts.
8. **KNOWN LESSONS** — gotchas. For extracted, from production use; for deferred, from the thinking that earned the recipe its place.
9. **OPEN QUESTIONS** — explicit gaps in current understanding.
10. **MIGRATION STEPS** — extracted only: how to wire into a new project.

Extracted recipes have substantive Field 10 and empty Field 7. Deferred recipes have the inverse. Both have all other fields.

**Discipline rule (from the reformulation):** a recipe earns its place by capturing thinking that would be expensive to recreate. It is *not* a brainstorm holding pen. Reject "comprehensive feature inventory" — comprehensive becomes idea bloat, which is harder to clean than feature bloat. A deferred recipe gets written when the idea has been explained twice or more in conversation, or the feature was nearly built and then deferred. Both indicate the thinking has earned preservation.

---

## Instantiation flow

`init.{ps1,sh}` is the one-shot setup script. Operator workflow:

1. Copy `project.yaml.example` → `project.yaml` at harness root. Fill in identity, capabilities toggles, personnel, tier examples, and (if knowledge-os enabled) wiki domains.
2. Run `init.ps1` (Windows) or `init.sh` (Unix).
3. The script: parses `project.yaml`; validates against the schema; builds a substitution dictionary from project fields plus computed values; substitutes every `*.template` file in-place (dropping the `.template` suffix); copies each enabled capability's `extracted/` contents to its runtime location (`.claude/skills/<orchestrator>`, `docs/wiki-schema.md`, `methodology/code-conventions.examples`); propagates each enabled capability's `deferred/*.RECIPE.md` to `docs/recipes/<capability>/`; unconditionally propagates `capabilities/kickoff-orchestration/` to `docs/recipes/kickoff-orchestration/`; deletes the `capabilities/` catalog; stamps `instantiated_date` and `instantiated_capabilities` back to `project.yaml`.
4. Run `init-validate.{ps1,sh}` to confirm no unresolved mustache placeholders or leftover `*.template` files remain, and that each enabled capability's runtime files exist.
5. Open `INIT.md` and run the manual kickoff interview in a fresh Claude session (INIT.md itself is the authority for its step list).

Init is **not re-runnable**. Once `project.yaml.instantiated_date` is stamped, init refuses to run. `init.{ps1,sh} -DryRun` / `--dry-run` is non-destructive and intended for firewall checkpoints during the harness build; it does not stamp metadata or delete the catalog.

---

## Substitution mechanism

Every file containing mustache-style double-curly-brace placeholders is suffixed `.template`. The init script's substitution pipeline walks every `*.template` file in the tree, applies a regex-based substitution against the runtime-built dictionary, writes the substituted content to the same path without the `.template` suffix, and deletes the source. Unknown variables cause a hard exit with the offending file path and variable name in the error message.

The `.template` suffix convention was chosen for **mechanical enforceability**. At every firewall checkpoint, the substitution-dictionary contract is checked bidirectionally: every mustache placeholder in every `*.template` file must appear in the contract, and every contract entry must be referenced by at least one `*.template` file. Without the suffix convention, this check requires walking every file in the repository; with it, the check is a `find … -name '*.template'` plus regex.

A small set of placeholders are substituted at authoring time rather than at instantiation: the Pocock fork commit hash (substituted into `capabilities/stress-testing/RECIPE.md` PROVENANCE) and the Pocock LICENSE year (substituted into three Phase 5/7 outputs). These use angle-bracket placeholder forms documented in the v2.7 build plan substitution dictionary contract, and are explicitly excluded from runtime substitution because the values are fixed at fork time, not at operator time.

---

## Substrate separation

The harness separates the evaluator from the evaluated, scoped by domain: for code, deterministic verification (tests, regression gates) is the primary firewall and substrate separation is defense-in-depth against correlated failure; for decisions with no executable oracle, substrate separation is primary. This applies in two places.

### Runtime (the handoffs core)

For decisions made during a project's lifetime, the three-document handoff ritual (`HANDOFF-AUTHORING.md`, `HANDOFF-RECEIVING.md`, `HANDOFF-CLOSING.md`) defines who reads what, who writes what, and where the decision lands. The Builder substrate authors; the Verifier substrate critiques; the Runner connects them; the operator interprets. Decisions land under `docs/adr/<YYYY-MM-DD>-<n>-<slug>.md` with `informed_by:` and `decision_method:` frontmatter.

### Build time (the firewall checkpoints)

For the harness's own build, the Builder/Verifier firewall is on at three phase transitions: Phase 1 (core methodology), Phase 5 (extracted capabilities), Phase 7 (documentation pass). At each checkpoint the operator opens a fresh substrate-separated verifier session against `verifier-session-kickoff.md` and the verifier writes its verdict to `verifier-reviews/phase-<N>-review.md`. The Builder session cannot proceed past the checkpoint until the operator approves the verdict. The closed checkpoints' verdicts are preserved under `verifier-reviews/` for audit.

This section documents the **build-time** firewall, which is a property of the harness's own production. Runtime substrate separation is the responsibility of the `handoffs` core; the two mechanisms are independent.

---

## The bottleneck principle

The operator is the direction-setter, and often the direction-setter for several projects at
once — so every manual step a harness leaves in place multiplies across all of them. The
standing principle (operator-declared, 2026-07-22): **any work that does not genuinely need a
human is automated.** Read-only work (health checks, censuses, structural sensors, drift
sweeps) runs fully automatically and reports in plain English. Write-bearing work (compiling
knowledge, regenerating indexes) runs automatically on a branch and reduces the human's role
to plain-English yes/no moments. Only judgment stays human — decisions, ratifications, GO
calls on irreversible actions, and answers only the operator knows. A manual invocation
surviving in any workflow design is a defect to justify, not a default to accept. The
delivered form of this principle is the standing loop (`/sweep` → the decision inbox →
`/standing-loop`); its rollout ladder and safety rails are recorded in
`harness-v3.0/specs/standing-loop-automation-brief-2026-07-20.md`.

One corollary, decided with it: status surfaces are **generated projections from files**
(briefings, inboxes, static pages), never a second actuator — no dashboard application that
calls the tooling on its own. Files are the state; conversation is the actuator; anything
else is a drift surface waiting to lie.

---

## What this harness is not

The harness is opinionated. It is not the right shape for every project, and it does not pretend to be.

### Not for project formation

`INIT.md` assumes the operator arrives with a mature project thesis — a paragraph of architecture, a list of personnel, a sense of which capabilities matter. The harness is for *executing projects-already-conceived*, not for *forming projects from ambient uncertainty*. Operators in genuine flux about what they're building will find the kickoff interview asks for outputs they cannot yet produce. The eventual `/init` orchestrator (deferred) is intended to address this; in v1.0, the manual `INIT.md` is the substitute, and operators in early formation should iterate in conversation before running the kickoff. See `TEMPLATE-README.md` for the "When to use this harness" prose.

### Not language-agnostic methodology

The methodology kernel uses a specific, opinionated vocabulary: Flight Plan, Builder/Verifier firewall, tier verification, hold points, substrate separation, RTDT ("Read the Damn Thermometer"). These terms are hardened and load-bearing, but they are not a generic universal standard. Operators inheriting the harness adopt the vocabulary or fork it. There is no translation layer.

### Not for solo experiments, single-file projects, or projects without compounding knowledge

Per Decision V2-23: the harness assumes a project that runs for months, accumulates decisions, and benefits from substrate-separated verification. Solo experiments under two weeks, single-file utilities, one-shot research, and projects whose entire knowledge layer is the active Claude session are not in scope. The instantiation overhead (kickoff interview, governance authoring, capability wiring) exceeds the marginal benefit for those shapes.

---

## Upgrade story

In v1.0, the harness is a **one-way fork** for every project that instantiates it. Improvements to extracted capabilities — bug fixes, new lessons, refinements to a SKILL.md — do not auto-propagate to already-instantiated projects. Operators tracking the upstream harness for fixes must port them manually.

This is a deliberate v1.0 limitation per the reformulation. The `/sync-capability` deferred orchestrator (no recipe in v1.0; a Field 9 open question in `capabilities/code-conventions/RECIPE.md` and elsewhere) is intended to address propagation when at least three active projects have instantiated the harness and the manual sync burden is empirically painful. Until that threshold is reached, manual fork-sync is acceptable.

---

## Pocock attribution

`/preflight` (renamed from `/grill` 2026-07-09) is forked from `mattpocock/skills/skills/engineering/grill-with-docs` at commit `b8be62ffacb0118fa3eaa29a0923c87c8c11985c` (MIT License, Copyright (c) 2026 Matt Pocock — year read from the cloned LICENSE per Decision V2-6). The full upstream LICENSE ships verbatim at `core/skills/preflight/LICENSE-mattpocock.txt` and travels with the skill to its runtime location at `.claude/skills/preflight/`. The attribution block at the top of `SKILL.md` lists the eight adaptations applied for harness conventions (operator-language normalization, ADR-path override, codebase-exploration adaptation, no-code-authoring rule, scope-boundary section, CONTEXT.md scaffold note, frontmatter adjustments, structural XML tags).

Two upstream files are copied verbatim: `CONTEXT-FORMAT.md` (the glossary format that `CONTEXT.md.template` adopts harness-wide) and `ADR-FORMAT.md` (the ADR template body; harness overrides only the filename pattern to `docs/adr/<YYYY-MM-DD>-<n>-<slug>.md` for consistency with the handoffs core). One upstream file — `EXAMPLES.md` — is documented as absent at the pinned commit (verified by `find` against the cloned tree); the absence is logged in `capabilities/stress-testing/RECIPE.md` PROVENANCE and BUILD-LOG.md Phase 5.

Future harness versions may pin to a tagged upstream release if Pocock cuts one. Until then, the fork is pinned by commit hash, and any drift between this fork and upstream is the operator's responsibility (or `/sync-capability`'s, when it exists).

---

*Architecture document version: 3.0 (2026-07-24; version history restructured 2026-07-28, backlog v3.0-72)*

*Reflects harness state as of v3.0 (SHIPPED 2026-07-24). The core + capabilities model is unchanged since v1.1; in-prose "in v1.0" notes describe behavior unchanged since v1.0.*

**Version history**

- **v1.1** — core skills + handoff-doc reconciliation; `build-orchestration` retired into core.
- **v1.2 / v1.3** — corpus observation; drift-governance sensors. Parameterized and self-gating; no new capabilities or schema fields.
- **v2.0** — verification-stack depth (differential oracle, mutation pass, visual evidence, failure diagnosis); the DATA-POLICY governance core; the domain-scoped substrate doctrine; version/backlog self-governance. No new capabilities or schema fields.
- **v2.1** — execution isolation (worktree parallel build / serial integration); runtime-safety core methodology (rollback/kill-switch, secret/data isolation, spend telemetry); the operate-phase `operate-sentinel` capability, shipping docs-only (recipes, no `project.yaml` toggle). No new substitution variables or schema fields.
- **v3.0 (SHIPPED 2026-07-24)** — cross-vendor verification as core skills (`bridge`/`cross-check`/`cross-check-loop`, ADR #7); `/grill` renamed `/preflight` (2026-07-09, retiring `stress-testing` into core); the `/doctor` readiness sensor; consent-prompted security hooks at init; contract-layer sensors under knowledge-os. By wave:
  - *2026-07-11* — fork-proven memory-engine v3 deploy layer, `/discover`, and engine docs (`docs/engine/`) under knowledge-os; bridge synced to the F17+gpt-5.6-sol global state.
  - *2026-07-13* — the onboarding surface (`TOUR.md`/`GLOSSARY.md`/`SYSTEM-MAP.html`/`/orient`); the docs-truth discipline (`MAINTENANCE.md`); the stranger-test gate.
  - *2026-07-20/21* — the behavioral-manifest layer (doctrine, format contract, tier-scaled gate) with its `/conformance` replay skill; `/sweep` (the read-only janitorial umbrella); `/standing-loop` (the scheduled write-side session, unarmed by default).
  - *2026-07-22/23* — the R-1 session-loop candidate pipeline (signing-gated promotion, shipped unarmed); the credential broker; workspace governance (zones, birth certificates, the reaper); the deadline-and-trigger register; `/doctor`'s version-drift and mirror/instance-parity sensors; the desk (`desk-metrics`, `DESK.html`) and the cross-project empire desk.
