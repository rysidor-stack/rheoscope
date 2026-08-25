---
name: orient
description: Grounded Q&A about THIS project's harness — answers come from reading the installed artifacts, never from model memory. Use whenever you or the operator asks "what is X", "how do I do Y", "why does this work this way", "what happened here", or has a vague sense something's wrong and needs a starting point. Complements /flight-plan (which reports project state) by explaining the harness mechanism itself.
---

# /orient

*verified-against: 3.0 (2026-07-24)*

/orient answers questions about the harness by reading the artifacts this project actually has
installed and citing what it read — `file:line` where the claim lives. It never answers from
training data about "how templates like this usually work." If no installed artifact settles a
question, /orient says so explicitly instead of guessing plausibly.

## When to use

- **what-is-X** — "what's a derivation block", "what does UNROUTED mean", "what's a tier".
- **how-do-I** — "how do I run my first compile", "how do I open a handoff", "how do I add a
  wiki domain".
- **why-is-it-like-this** — "why is VERIFY gated but ABSORB isn't", "why does /preflight own
  `CONTEXT.md` and not `/compile`".
- **what-happened** — "when did `/grill` become `/preflight`", "what shipped in v3.0", "has this
  come up before".
- **is-this-broken** — route to `/doctor` first (see § Protocol, step 5); /orient explains once
  `/doctor` has told you what's actually wrong.

## Protocol

1. **Classify the question** into one of the five shapes above. If it's genuinely ambiguous,
   ask which shape before searching — a mis-classified question sends you to the wrong source.

2. **Route to a source**, in this order:
   - `core/onboarding/GLOSSARY.md` — single-term definitions, alphabetical.
   - `core/onboarding/TOUR.md` — staged walkthrough (WHY / WHAT / FIRST WEEK / WHEN-X-HAPPENS);
     Stage 4 is the fastest route for a "something's wrong" question.
   - `core/onboarding/SYSTEM-MAP.html` — the interactive system map, for spatial/relationship
     questions the prose glossary doesn't answer well.
   - The artifact table below, for anything the onboarding docs don't cover directly.

3. **Read the source.** Do not answer from the routing step alone — open the file, find the
   actual passage, and answer with a `file:line` (or `file § heading`) citation the operator
   can go verify themselves. Answer in the operator's own words — a smart non-engineer must
   understand it on one reading; the citation is for checking, never required reading.

4. **If no installed artifact answers it, say so explicitly.** Do not fill the gap from general
   knowledge about templates, methodologies, or memory engines in general — that is exactly the
   failure mode this skill exists to prevent. A doc gap is a harness defect: point at
   `/log-backlog` so it gets captured rather than silently re-answered differently next time.

5. **is-this-broken questions route to `/doctor` first.** Run it (or ask the operator to) before
   attempting an explanation — a live readiness failure is faster to fix than to reason about,
   and /orient's artifact-reading protocol is not a substitute for the sensor.

## Artifact table

Paths below are runtime paths — where the artifact lives in an *instantiated* (post-init)
project, which is the only context a running `/orient` invocation ever executes in. If you are
reading this file from the harness template repo itself (pre-init), the knowledge-os-derived
rows resolve to their `capabilities/knowledge-os/extracted/...` source instead — see
`core/onboarding/TOUR.md` § Stage 2 for the pre-init/post-init path pairs.

| Looking for | Read |
|---|---|
| A single term, one-line definition | `core/onboarding/GLOSSARY.md` |
| The staged walkthrough / first-week sequence / trouble table | `core/onboarding/TOUR.md` |
| The interactive system map | `core/onboarding/SYSTEM-MAP.html` |
| Memory-engine contract and mechanics | `docs/engine/memory-engine-v3-spec.md`, `docs/engine/OPERATIONS.md`, `docs/engine/memory-engine-v3-test-plan.md`, `docs/engine/memory-engine-v3-tool-grant-tcb-spec.md` |
| Whether a specific sensor works | This session runs its self-test and reports pass/fail in plain words (mechanics: `deploy/<sensor>.py --help` and `--self-test` — every sensor is self-test-first) |
| The wiki/raw/receipt structural contract | `docs/wiki-schema.md` |
| Why a capability exists, what it needs, known lessons | `docs/recipes/<capability>/*.RECIPE.md` (deferred recipes; docs-only capabilities carry the full `RECIPE.md` too) |
| Session contract, core directories, session discipline | `core/governance/CLAUDE.md` |
| Driving a web UI in the browser pane (read BEFORE the first click) | `core/governance/CLAUDE.md` § Session discipline → Browser-pane automation |
| The decision-inquiry protocol (authoring/receiving/closing) | `core/handoffs/README.md` and the three `HANDOFF-*.md` specs alongside it |
| What a specific orchestrator run actually did | `receipts/` (machine-readable; `changelog.md` is the human-readable narrative companion) |
| What shipped, when, and why (release history) | `HARNESS-CHANGELOG.md` |
| Known open issues and gaps in the harness itself | `harness-backlog.md` |
| The ratified manifest doctrine + incorporation annex | `core/methodology/manifest-driven-builds.md` |
| The operational manifest contract (naming firewall, format, gate table) | `core/methodology/manifest-format.md` |
| A surface's per-layer manifest gate state | `manifests/<surface>/MANIFEST-INDEX.md` |
| The row-replay conformance sweep | `.claude/skills/conformance/SKILL.md` |
| The one-command janitorial health briefing (doctor + census + sensors + smoke + audit spot check) | `.claude/skills/sweep/SKILL.md` |
| The scheduled write-side autopilot (compile-on-branch, human-merged) | `.claude/skills/standing-loop/SKILL.md` |
| What's waiting on the operator (the decision inbox) | `deploy/decision-inbox.py` (writes `DECISIONS-PENDING.md`; `/sweep` only checks it for staleness) |
| Where credentials live — the positive convention (vault-only, broker-delivered, never a plaintext file) | `core/security/CREDENTIALS.md` |
| Storing, using, or removing a Windows-vault credential | `deploy/credential-store.ps1`, `deploy/credential-use.ps1`, `deploy/credential-remove.ps1` |
| Workspace zones, birth certificates, and the reaper | `core/governance/WORKSPACE.md.template` and `deploy/check-workspace.py` |
| The R-1 session-loop candidate pipeline (harvest/stage/register/promote) | `deploy/candidates.py`, `harvest-candidates.py`, `register-candidates.py`, `check-candidates.py`, `promote-candidate.py`; `docs/engine/OPERATIONS.md` § "Honest gaps" |

## Boundaries

- **Read-only.** /orient never edits a file, stamps a doc, or writes to `CONTEXT.md` — it
  answers questions. If an answer implies a fix, name the fix and point at the skill that owns
  it (`/preflight` for terminology, `/log-backlog` for template defects, `/doctor` for
  environment gaps).
- **T1-class questions route onward, not to a same-session answer.** If a question is really a
  hard-to-reverse decision in disguise ("should we change X"), don't settle it here — flag it
  and offer `/handoff`, per `core/governance/CLAUDE.md` § Session discipline →
  Decision-lock substrate firewall.
- **Never model memory.** If you find yourself about to answer from what memory engines or
  Claude Code templates "generally" do rather than from a file you just read in this project,
  stop — that's the exact drift this skill exists to prevent. Say the artifact is missing
  instead.

## Where it sits

/orient explains; `/flight-plan` reports. `/flight-plan` tells you what changed and what needs
attention in this project's *state*; /orient tells you what a mechanism *means* and why it's
built the way it is. Run `/flight-plan` for "what should I do this session"; run /orient for
"what is this thing I'm looking at, and why does it work this way."
