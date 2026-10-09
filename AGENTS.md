# AGENTS.md — orientation pointer for agent sessions

This project runs on the Rheoscope harness. Its governance is substrate-neutral: the same rules bind every AI agent working in this tree — Claude, Codex/GPT, Gemini, or anything else that loads this file by convention. A non-Claude agent here is a first-class actor, not a guest: the harness's own verification doctrine *requires* non-Claude sessions (cross-vendor legs, handoff verifiers), and working sessions on any substrate inherit the same contract.

**This file is a pointer, not a copy.** Canonical content lives in the files below; duplicating it here would drift (single-home rule). Read them in this order before doing any work:

1. `core/governance/CLAUDE.md` — the session contract: orientation, session discipline, directory-preservation and single-writer rules. The filename is a Claude Code convention; the contents bind every agent.
2. `core/governance/PROJECT-COMPASS.md` — what this project is, decision authority, escalation paths.
3. `core/governance/HARDCONSTRAINTS.md` — invariants that can never be violated.
4. `CONTEXT.md` — the project glossary. Use its terms; respect its `_Avoid_:` lists.
5. The current flight plan under `wiki/flight-plans/` (if one exists) — what this phase is doing and where work stands.

Session reflexes — the same ones root `CLAUDE.md` gives Claude Code (v3.0.63, backlog v3.0-232: they were missing here, so a Codex-led project was never told to close with the attended sweep that acknowledges pending items):

- **Open** substantive sessions with `/flight-plan` (read `.claude/skills/flight-plan/SKILL.md`, or its `.agents/skills/` adapter, and follow it); use `/orient` for questions about how the harness itself works.
- **During work:** new knowledge lands as `raw/` intake; reasoning that should outlive the chat runs through `/reason` so its findings land as records.
- **Decisions:** hard-to-reverse (T1) decisions never lock in the session that authored them — route them to `/handoff`.
- **Harness defects** noticed in passing → `/log-backlog`, same session.
- **Close** of major work: land the raw intake, update the records you touched, and run `/sweep` — attended (you, with the operator present). Only an attended sweep acknowledges the pending items; skip it and the missed-cycle alarms accumulate.

Conventions that most often trip up sessions arriving without orientation:

- **Knowledge intake goes through `raw/` + `/compile`** (when the knowledge-os capability is enabled) — never write wiki articles directly. See `.claude/skills/compile/SKILL.md` where present.
- **Skills are protocols, not Claude features.** `.claude/skills/<name>/SKILL.md` files are executable instructions any agent can read and follow; the directory name is a Claude Code convention, nothing more.
- **Substrate identity is load-bearing, and the separation rule is TIERED — do not apply the strict tier by default.** Handoff and verification records store which vendor/model authored and answered (`authored_by` / `answered_by`). Three separations exist, and conflating them has caused real damage:
  - **Routine verify legs** (the ordinary compile / absorption-verify gate) require only a **different `model_id`**. Same-vendor is explicitly compliant — OpenAI-verifying-OpenAI and Claude-verifying-Claude both pass, provided the models differ.
  - **Content audits and design-gate verifications** require a **different vendor** (the cross-substrate firewall). Same vendor is not enough here even if the models differ.
  - **T1 decision locks** require a **distinct substrate (family) from the authoring session** — enforced by the `/handoff-close` protocol reading `meta.yaml.authored_by` (since v3.0-78 executed by `/handoff`'s headless cross-vendor close leg), not by the verify gate.

  The canonical, executable statement of the first two is `substrate_gate_ok()` in `deploy/check-substrate.py` (where the knowledge-os capability is enabled); the third lives in `core/governance/CLAUDE.md` § Session discipline. **Read the relevant one before concluding that a leg is misrouted.** Never "harden" a routine gate into a vendor gate to settle a doubt or to satisfy an operator's surprise: a routine leg can fire locally against a tool-capable verifier, whereas a vendor gate on the same leg forces every verification into an outbound evidence packet — that conversion is how one compile becomes a queue of egress approvals. If you believe a gate's tier is wrong, say so and stop; changing a tier is a decision, not a fix. Always record your real vendor/model; never fill another substrate's role under your own.
- **T1 decisions never lock in the session that authored them** — the decision-lock firewall in `core/governance/CLAUDE.md` § Session discipline applies to every substrate.
- **The security perimeter is two layers, and only one of them is per tool** (v3.0-232). CALL-TIME: the PreToolUse hooks in `.claude/settings.local.json` mediate Claude Code tool calls only; the template does not yet ship Codex hooks (backlog v3.0-235), so a Codex session meets no call-time denial — it can write `.env`/credential files and protected files, and skip the commit scanner with `git commit --no-verify`. COMMIT-TIME: the git pre-commit scanner and `deploy/trust.py`'s committed-identity rule hold for every agent. Honor the deny patterns in `core/security/hooks/` by policy, never use `--no-verify`, and say plainly in your first report that this session has no call-time guards. `/doctor`'s `perimeter-live` row reports which layer is live for your session.
