---
name: preflight
description: Stress-test a plan, spec, or governance doc against repo evidence, CONTEXT.md, and documented decisions before it ossifies. Sits between the roadmap and the flight plan — preflight the roadmap phase article, then author the flight plan from the preflighted result. Sharpens terminology, updates CONTEXT.md inline, stamps and annotates the artifact, proposes ADRs sparingly. (Renamed from /grill 2026-07-09.)
---

# /preflight

Interrogate a document before work gets built on it. /preflight verifies the artifact's factual claims against the repo, interviews the operator on everything evidence can't settle, sharpens the project's language, and leaves durable marks — a stamped artifact, dated glossary resolutions, and (rarely) an ADR — so downstream authoring inherits the findings instead of re-deriving them.

**Where it sits in the pipeline:** roadmap phase article → `/preflight` → `/flight-plan`. The standard kickoff session runs the health check, preflights the phase article, then authors the flight plan from the freshly preflighted roadmap — all in one session. /preflight is also used standalone on specs and governance docs whenever one feels soft.

## Protocol

### 1. Evidence sweep — before any questions

The interview is the second phase of a preflight, not the first.

1. **Inventory the claims.** Read the artifact once and list every factual claim it makes about the system — code structure, config, data files, workflows, live behavior — and every assumption a scope item silently rests on. ("CSS convergence will churn CSP hashes." "All six generators have dated content." "The nightly workflow picks up new pages automatically.")
2. **Fan out read-only evidence agents** to verify them: mechanical sweeps on the cheapest adequate model tier at low effort, reading and inventory legs on a mid-tier model; every prompt self-contained; agents never spawn agents; all judgment stays with the preflight session. Group the claims into a few evidence domains and dispatch one agent per domain. (Example from a real run: four domains — styling/policy inventory; data/generator/schedule inventory; URL facts; indexation status.)
3. **Check the deliverable, not just the docs.** Governance artifacts (`wiki/`, `roadmap/`, `docs/adr/`, `core/`) can agree with each other and still be wrong about the system. The evidence surface includes the deliverable tree, CI workflows, deploy config, and — when rendered or live behavior matters — the live deliverable via the project's live-check tooling (an internal preview browser typically can't reach prod or see deploy-layer policy).
4. **Triage the findings:**
   - **Contradiction** (artifact asserts X, evidence shows Y) — surface to the operator immediately, with `file:line` evidence. These are the highest-value preflight output; the two landmines of this skill's first instance run (a deploy-layer policy silently killing an assumed feature; a scheduled workflow's hardcoded page list that would silently orphan new content) were found exactly this way.
   - **Confirmed** — annotate the artifact (see § Preflight trace) so the flight plan inherits the evidence.
   - **Unverifiable from the repo** — becomes an interview question.

### 2. Interview

Interview the operator relentlessly about every aspect of the artifact until shared understanding is reached. Walk down each branch of the design tree, resolving dependencies between decisions one by one. For each question, propose a recommended answer. Ask one question at a time and wait for the answer before continuing.

Never ask the operator a question the repo can answer — bring the operator only judgment calls, trade-offs, and facts no file can settle.

While interviewing:

- **Challenge against the glossary.** When the operator uses a term that conflicts with `CONTEXT.md`, call it out immediately: "Your glossary defines 'cancellation' as X, but you seem to mean Y — which is it?"
- **Sharpen fuzzy language.** When a term is vague or overloaded, propose a precise canonical one: "You're saying 'account' — do you mean the Customer or the User?"
- **Stress-test with concrete scenarios.** Invent scenarios that probe edge cases and force precision about the boundaries between concepts.
- **Cross-reference every claim.** When the operator states how something works, check whether the project's artifacts — and, for system claims, the deliverable itself — agree. Surface contradictions: "The roadmap says X is the source of truth for Y, but you just said Z owns it — which is right?"

### 3. Challenge scope (RTDT)

Terminology is not the only thing under test. Check the artifact's scope both ways against its own exit criteria (or stated goal):

- A scope item that no exit criterion needs is a **cut candidate** — name it explicitly and propose the cut, per `core/governance/CLAUDE.md` § Session discipline → RTDT. (A real run proposed three of its phase's scope items as cuts on exactly this ground.)
- An exit criterion that no scope item carries is a **gap** — surface it.

Proposed cuts are recorded as open questions for the operator or the hold point, not decided by /preflight.

### 4. Unconstrained ideal

Before the report, one deliberate widening: if current constraints didn't exist, what would ideal look like — and which upgrade paths does this artifact's approach foreclose? This is cartography, not scope change: the destination stays what the constraint demands (RTDT), the map records what is being chosen against. Land it as a short `*Unconstrained ideal:*` entry in the artifact's preflight-notes section — the ideal shape in a sentence or two, plus any foreclosed path worth naming. Skippable only for trivially-obvious or time-critical preflights, and the skip is recorded with its reason (`*Unconstrained ideal: skipped — <reason>*`), never silent. Doctrine: `core/methodology/five-pass-method.md` (Pass 3).

## Landing the results

### CONTEXT.md — update inline

When a term is resolved, update `CONTEXT.md` right there; don't batch. Use the format in [CONTEXT-FORMAT.md](./CONTEXT-FORMAT.md); where the live `CONTEXT.md`'s own "How to maintain this file" section has sharpened a rule (e.g. one-sentence definitions), the live file's rules govern. /preflight is the only writer to `CONTEXT.md` (Decision V2-8; /compile logs unresolved terms for /preflight to pick up).

When a preflight resolves a flagged ambiguity, record it in the entry itself as `*Resolution (YYYY-MM-DD preflight):* ...` — dated, so future sessions can tell a settled ambiguity from an open one. (Entries from before 2026-07-09 carry `grill` in the tag; they stay as written.)

`CONTEXT.md` is a glossary and nothing else — no implementation details, no spec fragments, no scratch notes. (The scaffold ships at project root from instantiation; if it were ever missing, create it when the first term is resolved.)

### ADRs — sparingly

Only offer an ADR when all three are true:

1. **Hard to reverse** — the cost of changing your mind later is meaningful
2. **Surprising without context** — a future reader will wonder "why did they do it this way?"
3. **The result of a real trade-off** — there were genuine alternatives and you picked one for specific reasons

If any is missing, skip it. Use the template body in [ADR-FORMAT.md](./ADR-FORMAT.md). Filename: `docs/adr/<YYYY-MM-DD>-<n>-<slug>.md`, `<n>` sequential within the day. Frontmatter: `decision_method: preflight` (pre-rename ADRs carry `grill`; both mean this skill) and `informed_by:` (typically empty for preflight-led decisions; populated when the preflight is downstream of a handoff). Create `docs/adr/` lazily if it doesn't exist.

### Preflight trace — the durable marks

- **Stamp the artifact.** Its status line gains the date — e.g. a roadmap article's `**Status:**` line reads `preflighted YYYY-MM-DD — ...`. Future sessions read the stamp to know the artifact survived interrogation. (Phases 1–3 carry the historical `grilled YYYY-MM-DD` stamp; same meaning, leave them.)
- **Annotate inline.** Findings that qualify a specific scope item attach to it as `*Preflight note:* ...` (evidence or sequencing that sharpens the item) or `*Preflight blocker surfaced:* ...` (a discovered precondition). Standalone findings collect under a `## Preflight notes (YYYY-MM-DD, evidence-based)` section. (Historical artifacts use `Grill note` / `Grill notes`; leave them.)
- **Close with a report** to the operator: claims verified and contradictions found (with evidence), terms resolved and CONTEXT.md updates made, ADRs written (if any), cuts proposed, the unconstrained-ideal note (or its recorded skip), and every open question with where it was routed (operator ruling, hold point, or handoff).

## Routing questions that outgrow the interview

- **T1-class decisions** (hard-to-reverse / silent-error class, per `core/methodology/tier-definitions.md`): never settle these in-session — the decision-lock substrate firewall (`core/governance/CLAUDE.md` § Session discipline) requires distinct substrates for authoring and locking. Flag the decision, capture what the preflight learned about it, and offer `/handoff`.
- **Harness-template issues** (defects in the harness itself, not this project's content) surfaced while preflighting: log via `/log-backlog` in the same session, per the backlog-logging discipline.

## Boundaries

- **Never write code or implement features.** /preflight authors documents — questions, glossary updates, ADRs, sharpened prose in the artifact under test. Code and structural changes belong to a fresh Builder session per the Builder/Verifier firewall.
- **No business-rule verification drills.** /preflight sharpens terminology and surfaces ambiguity; the systematic six-category drill (boundaries, partial ops, failures, concurrency, bad data, business logic traps) belongs to the verification spec authoring pass. Flag drill-shaped questions and defer.
- **/preflight ends with its report.** It does not itself author the flight plan — that's `/flight-plan`, which the kickoff flow invokes next (same session is the norm; the plan wants the preflight evidence fresh).

## Multi-context note

Assume a single root `CONTEXT.md` until a `CONTEXT-MAP.md` exists at the root (rare; only for large multi-domain efforts). If a map exists, read it to find which context the current topic belongs to; if unclear, ask.

---

*Lineage: the interview loop and the two format files ([CONTEXT-FORMAT.md](./CONTEXT-FORMAT.md), [ADR-FORMAT.md](./ADR-FORMAT.md)) descend from [mattpocock/skills grill-with-docs](https://github.com/mattpocock/skills/tree/main/skills/engineering/grill-with-docs), MIT-licensed — see `LICENSE-mattpocock.txt`. Rebuilt as a harness-native orchestrator and renamed /grill → /preflight on 2026-07-09; no longer tracks upstream. Pre-rebuild history is in git; historical artifacts keep their `grilled` stamps.*
