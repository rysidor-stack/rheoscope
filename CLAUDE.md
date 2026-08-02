# CLAUDE.md — root pointer (session orientation)

Claude Code auto-loads this file at session start. It is a **pointer, not the contract** — canonical content lives in the files below (single-home rule; duplicating it here would drift). Non-Claude agents receive the identical orientation from root `AGENTS.md`; the filename convention differs per tool, the contract does not.

Before any work, read in order:

1. `core/governance/CLAUDE.md` — the session contract: session discipline, directory-preservation and single-writer rules, the decision-lock firewall. (In the template dev repo this exists as `CLAUDE.md.template`; in every instantiated project it is the populated contract.)
2. `core/governance/PROJECT-COMPASS.md` — what this project is, decision authority, escalation paths.
3. `core/governance/HARDCONSTRAINTS.md` — invariants that can never be violated.
4. `CONTEXT.md` — the project glossary. Use its terms; respect its `_Avoid_:` lists.
5. The current flight plan under `wiki/flight-plans/` (if one exists).

Session reflexes (the contract's session discipline, compressed — the contract governs where they differ):

- **Open** substantive sessions with `/flight-plan` (the cockpit); use `/orient` for questions about how the harness itself works.
- **During work:** new knowledge lands as `raw/` intake (never written directly into `wiki/`); reasoning that should outlive the chat is run through `/reason` so its findings land as records.
- **Decisions:** hard-to-reverse (T1) decisions are never locked in the session that authored them — flag and route to `/handoff` (T1s lock via its headless cross-vendor close leg, never in the authoring session). Others land per the contract's decision discipline.
- **Harness defects** noticed in passing → `/log-backlog`, same session.
- **Close** of major work: land the raw intake, update the records you touched, and run `/sweep` (or the specific sensor) so the next session inherits sensors, not memories.
