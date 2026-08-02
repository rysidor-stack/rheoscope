# Capabilities Catalog

The Rheoscope ships these capabilities. Each toggled capability is opt-in via `project.yaml.capabilities`.

| Capability | Status | Toggled? | What it does |
|------------|--------|----------|--------------|
| knowledge-os | extracted (compile, audit, wiki-schema, discover, engine deploy layer) + deferred (history, recompile) | yes | Raw intake → compiled wiki → roadmap → session briefing, backed by the proven v3 memory engine — ledger + derived views, cross-vendor verified absorption, conservation census (see `docs/engine/OPERATIONS.md`) |
| stress-testing | **retired 2026-07-10** — graduated to the `/preflight` core skill | key still accepted, wires nothing | No-op. `/preflight` (renamed from `/grill` 2026-07-09) ships unconditionally as a core skill instead; see `core/skills/preflight/`. |
| code-conventions | prototype | yes | Worked example of project-specific code conventions |
| kickoff-orchestration | deferred (init, roadmap) | **no — docs-only** | Eventual /init and /roadmap orchestrators (recipes only in v1.0). Recipes propagate unconditionally to `docs/recipes/kickoff-orchestration/` regardless of any toggle. |
| operate-sentinel | phase-1 deferred recipe (adoptable); phase-2 designed bounds only (not adoptable) | **no — docs-only** | Scheduled Controller in operate mode: runs the project's existing monitors unattended (read-only), one receipt per run; write-back remediation is phase-2, separately bounded and gated. Recipes propagate unconditionally to `docs/recipes/operate-sentinel/`. |
| decorrelated-review | deferred entirely (docs-only, no SKILL.md ships) | **no — docs-only** | `/harden` (checkable-loop) and `/frame` (open-ended framing-map) orchestrators — the generative sibling of the handoff engine. Recipes only; propagate unconditionally to `docs/recipes/decorrelated-review/`. |

Read the `RECIPE.md` inside each capability subdirectory for full provenance, dependencies, known lessons, and migration steps.

**Note on core skills:** Some skills are NOT capabilities — they ship to `.claude/skills/` unconditionally because they're invariant across projects. They live at `core/skills/<name>/` in the template and are consumed into `.claude/skills/` at init (see `init.ps1`/`init.sh` "Part C"). The set:
- `handoff` (single entry point since v3.0-78; `handoff-close` ships as its close-leg protocol) — the substrate-separation decision-inquiry protocol (docs at `core/handoffs/`). Toggling it off would remove the decision firewall — substrate separation is primary for decisions with no executable test.
- `flight-plan` — the per-session cockpit. Was formerly the sole payload of a `build-orchestration` capability; that capability is retired and `/flight-plan` is now core (it degrades gracefully when knowledge-os is disabled).
- `log-backlog` — the harness-backlog logging ritual (see `core/governance/CLAUDE.md` § Session discipline).
- `preflight` — the stress-test / evidence-interview orchestrator, renamed from `/grill` 2026-07-09 when the `stress-testing` capability retired into core.
- `cross-check`, `cross-check-loop` — cross-vendor verification (a fast single-shot check and a managed multi-round convergence loop over the same transport), per ADR #7.
- `doctor` — the environment readiness sensor, run at init-end, on demand, and as a flight-plan step.
- `conformance` — replays behavioral-manifest rows against the live build (smoke tier on cadence, full tier at freezes/certification), landed with the manifest layer (2026-07-20/21).
- `sweep` — the read-only janitorial umbrella: runs every health check (doctor, census, structural sensors, manifest structure, conformance smoke, the read-only half of audit) in one pass and returns one plain-English briefing; never changes anything.
- `standing-loop` — the scheduled write-side session that runs `/sweep`, regenerates the decision inbox, and compiles pending raw content on a disposable branch; Phase-B posture, unarmed by default until a committed operator-authorization artifact plus a passed rollback rehearsal receipt arm it.
- `bridge` — the transport library the cross-vendor skills call. Not a slash command.

There is no `handoffs` or `build-orchestration` key in `project.yaml.capabilities`.

**Note on docs-only capabilities (`kickoff-orchestration`, `operate-sentinel`, `decorrelated-review`):** these ship no `extracted/` content — only a `RECIPE.md` and deferred recipes. They are documentation that always travels with the instantiated project: NOT in `project.yaml.capabilities` (no toggle), propagated unconditionally to `docs/recipes/<capability>/` regardless of what the operator enabled (init's Part B carries the docs-only list — adding a capability to that class means extending the list in both init scripts).

**Authoring rule (where doctrine may live):** doctrine must keep its canonical home on a surface that always ships — `core/`, or a docs-only capability's recipes. A *toggled* capability's docs may point at doctrine, never own it: a toggled-off capability is deleted at init and its text never reaches the project, so any rule whose only home is there silently fails to ship.
