# The Five-Pass Method

The research-and-reasoning discipline. `flight-plan-template-v6.md` governs how work *executes*; this document governs how options get *thought through* before anything is worth executing. Five-Pass informs the flight plan; it never replaces it.

**Why it works:** ask a capable model a question once and you get its default answer — fluent, sensible, shaped like every answer it has seen before. That is not the ceiling of what the model can do; it is the floor of what the question demanded. The depth of the response is set by the depth of the ask. The five passes put five distinct demands on the reasoning — enumerate exhaustively, attack the framing, design without limits, decide under real constraints, synthesize across everything learned — and the later passes reason about the earlier ones, which is where the insights live that no single prompt can reach. The method is not really about research; it is about refusing to accept the first pass of a system that always has more in it.

## The five passes

### 1. Landscape — *what exists*

Survey the field. Map the options, build the comparison table. No judgment yet — the goal is completeness, not evaluation.

*Done when:* you can name every serious option and describe what each one actually is, without ranking them.

### 2. Break Template — *what's hidden or assumed*

Challenge the framing the landscape pass took for granted. What categories are wrong? What is everyone optimizing that might not matter? What is nobody talking about that does? This is where the insight that changes the decision lives — the one that changes it, not the one that confirms it. **If this pass produces nothing uncomfortable, you have not run it yet.**

*Done when:* at least one assumption from Pass 1 has been named and tested.

### 3. Reason Without Constraint — *what's possible if current limitations didn't exist*

Ignore budget, skill, timeline, existing tooling: what would ideal look like? The working question is simply *what is the unconstrained ideal?* This is cartography, not fantasy — you are mapping the territory you *could* occupy so you know what you are choosing not to build, and what the upgrade path is. It is where architecture-level dead ends get caught before constraint reasoning papers over them.

*Done when:* you can state the ideal design and the specific gap between it and what you are about to build.

### 4. Reason With Constraint — *what we actually do given who we are*

Apply reality: who is building, what budget, what timeline, what already exists. Make the decision. Lock it. Move on. This pass is allowed to be decisive precisely because the previous three were not.

*Done when:* a decision is made and recorded, along with the constraint that drove it.

### 5. Reason About Totality — *what emerges from everything learned*

Applies when multiple related passes or decisions have completed. Step back from the individual decisions and look at the system they form together: where do they interact, where does coherence break, what pattern crosses domains that no single pass could see? Passes 1–4 are analytical; this one is synthetic — and it is where system-level intelligence lives.

*Done when:* you can name at least one property of the combined system that no individual pass could have surfaced.

**Each pass should be willing to kill the previous one.** Break Template can invalidate the landscape framing; unconstrained reasoning can reveal the constrained decision builds toward a dead end; totality can expose that individually-correct decisions form a collectively-broken system. When a later pass contradicts an earlier one, the later pass wins.

## How the harness codifies passes

The passes are tools, not a liturgy — rigid sequence is for automatons, and a mandated *sequence* would be performed in form and skipped in substance. The harness therefore codifies **what the output must survive, never the order of thought**. A pass is codified when it has four properties:

1. **A name** — so sessions and receipts can refer to it.
2. **A required artifact** — the pass leaves evidence, not a mood.
3. **A refusable test** — the artifact can *fail*; "think harder" cannot fail, "every term carries an `_Avoid_:` line naming what it displaces" can.
4. **An explicit skip rule** — skipping is permitted where the skip table below says so, and the skip is recorded with its reason. Silence is never a pass.

The design criterion for any such test: **faking the pass must cost more than running it.** Pocock's three ADR criteria qualify (a candidate can fail them); the show-the-rejects convention qualifies (fabricating a plausible reject pile is more work than actually killing candidates); a bare instruction to "reason about totality" does not qualify until the artifact must contain either a named cross-cutting finding or an explicit no-findings attestation.

Not every pass needs machinery. Passes 1 and 4 happen by default — every session surveys and every session decides under constraint. Pass 2 is forced indirectly wherever item-level tests exist (the ADR criteria *are* a Break Template pass wearing a test costume). **Only passes 3 and 5 have no natural forcing function** — nothing in ordinary work ever makes a session ask "what is the unconstrained ideal" or "what do these decisions mean together" — so they are the two the harness forces explicitly.

## The conventions

**Show the rejects.** Any step that generates an enumerable list (glossary terms, ADR candidates, wiki domains, governance docs) presents the survivors *and* the candidates considered and killed, with the reason each died. An empty reject pile on a first draft is the thinness tripwire: it means the step produced a survey, not a judgment. This extends an existing harness invariant — adoption decisions already record ADOPTED/ADAPTED/RESERVED/DECLINED — down to first drafts.

**Totality read-back.** When a bounded set of related answers or decisions completes (a kickoff interview, a multi-decision phase), one synthesis step re-reads them as a system and records cross-cutting findings — or the explicit attestation `No cross-answer findings — attested`. See `INIT.md` Step 2j for the kickoff instance.

**Unconstrained-ideal note.** `/preflight` records, per artifact, what ideal would look like and which upgrade paths the constrained approach forecloses — skippable only with a recorded reason. See `core/skills/preflight/SKILL.md` § 4.

**The manual trigger is a skill.** `/reason` (core skill, `.claude/skills/reason/SKILL.md`) is the invocation protocol for on-demand runs — subject naming, pass selection with recorded skips, the Done-when table, and the landing rules for findings. The ambient wirings cover the fixed protocol points; `/reason` covers the mid-stream step-back nothing scheduled.

**Thinness backstop rides cross-check.** No new verifier machinery: when a draft needs an independent depth check, phrase it as an ordinary falsifiable claim for `/cross-check` — e.g. *"No term in this glossary would survive unchanged in a neighboring project."* The existing bridge, verdict table, and receipts apply unchanged.

## Where each pass lives

| Pass | Codified home |
|---|---|
| 1. Landscape | Default behavior — every evidence sweep and survey step |
| 2. Break Template | Item-level tests: Pocock's three ADR criteria (`INIT.md` 2i, `/preflight` § ADRs); the glossary term test (`INIT.md` 2h); show-the-rejects wherever lists are drafted |
| 3. Without Constraint | `/preflight` § 4 (unconstrained-ideal note) |
| 4. With Constraint | Default behavior — RTDT and the flight plan govern it |
| 5. Totality | `INIT.md` 2j (kickoff read-back); `/discover` introspect and relate modes (the corpus-level instance — ships with the `knowledge-os` capability, so present only where that capability is enabled) |

## When to skip

| Pass | Always? | When to skip |
|---|---|---|
| 1. Landscape | Yes | Never |
| 2. Break Template | Yes | Never |
| 3. Without Constraint | Usually | Decision trivially obvious or time-critical — skip recorded with reason |
| 4. With Constraint | Yes | Never |
| 5. Totality | When multiple related passes complete | Single isolated decisions |

**RTDT governs passes 3 and 5.** Unconstrained and totality reasoning create scope-creep risk; Read the Damn Thermometer applies. The ideal informs the *map*, not the *destination* — note the ideal architecture, file the upgrade path, build what the constraint demands.

## Deferred instrumentation

Named forward capabilities, deliberately not built (see backlog v3.0-49): a generic-vocabulary sensor over glossary drafts (sensor-class sibling of the vocabulary-aging family); pass-payoff telemetry (how often a codified pass changed an outcome — the empirical liturgy-pruner); depth-grading as a standing dimension on cross-vendor legs. Until then, depth checks are ordinary cross-check claims.

---

*Lineage: distilled from the operator's five-pass research method (personal methodology note 2026-02-23, evolved from a three-iteration decision method; rewritten 2026-07 as a standalone method document, whose depth-of-ask framing and per-pass "Done when" criteria this file carries). Incorporated with the refusability doctrine 2026-07-26; refreshed against the rewrite 2026-07-27. The harness codifies artifacts and tests, never thought order.*
