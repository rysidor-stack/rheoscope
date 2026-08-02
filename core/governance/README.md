# Governance — core/governance/

This directory holds the six governance templates that ship as core: present in every instantiated project, regardless of which capabilities are enabled.

## Files

- **CLAUDE.md** — Schema and orientation. Every Claude Code session reads it first. Extracted and generalized from a production project, restructured for the core + capabilities model (Decision V2-3); per-capability schemas live inside their capability directories per Decision V2-19.
- **PROJECT-COMPASS.md** — Operator-facing project orientation: What this is / Architecture / Endgame / Hard constraints / Operator / Methodology. Embedded into every handoff's `context.md` Section 1 at authoring time, so historical handoffs preserve project state at the time of their authoring.
- **HARDCONSTRAINTS.md** — Immutable thou-shalt-not list. Constraints arrive with a one-sentence statement, rationale, test, and lock date.
- **DATA-POLICY.md** — What harness-produced artifacts (screenshots, diffs, logs, seeds) may contain, how long they live, and how purge is receipted. Mask-at-capture rules, retention windows.
- **KILL-SWITCH.md** — Operator-maintained registry of halt actions for each enabled autonomous capability. Conditional: completed when the project enables its first autonomous capability, not at INIT. A project with no automation leaves it empty.
- **AUTOMATION-ISOLATION.md** — Operator-maintained registry of credential scope, environment/data posture, declared high-blast-radius surfaces, and spend cap/attribution for each enabled autonomous capability. Conditional: completed at first enablement, not at INIT. A project with no automation leaves it empty.

## Population

CLAUDE.md, PROJECT-COMPASS.md, and HARDCONSTRAINTS.md populate during the INIT.md kickoff interview. Init substitutes the project's identity, personnel, and capability wiring; the operator fills the prose sections (architecture, endgame, hard-constraint entries) during INIT.md Steps 2a-2c. DATA-POLICY.md ships with binding defaults — no interview step; the operator tunes retention windows per its own edit rules. KILL-SWITCH.md and AUTOMATION-ISOLATION.md ship empty; the operator completes them when the project enables its first autonomous capability, not at INIT.

## Edit policy

- **CLAUDE.md** and **PROJECT-COMPASS.md** — operator may edit anytime as the project evolves. No ADR required; just session-level honesty about what changed and why (typically captured in a session note or handoff).
- **HARDCONSTRAINTS.md** — edits require an explicit governance ADR in `docs/adr/`. Adding, modifying, or retiring a constraint is a governance act, not a content edit. The ADR captures rationale and links to the constraint touched.
- **DATA-POLICY.md** — tightening (shorter windows, more masked regions) needs no ceremony; weakening a hard rule or lengthening a retention window requires a governance ADR, same as HARDCONSTRAINTS.md.
- **KILL-SWITCH.md** — operator adds entries when enabling autonomous capabilities and retires them when disabling. No governance ADR required; entries are operational records, not governance decisions.
- **AUTOMATION-ISOLATION.md** — operator adds entries when enabling autonomous capabilities and retires them when disabling; tightening a credential scope or lowering a spend cap needs no ceremony. Broadening a declared credential scope, raising a spend cap beyond its declared envelope, or removing a declared high-blast-radius surface requires a governance ADR, same as HARDCONSTRAINTS.md.
