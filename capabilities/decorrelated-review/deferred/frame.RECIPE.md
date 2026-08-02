# Recipe: /frame (deferred)

## 1. WHAT IT IS

An open-ended disagreement-map orchestrator for no-oracle deliberation, where the thing that may be wrong is the **frame**, not a checkable defect. It spawns N proposers that each take the *whole* problem from a mechanically-diverse frame (≥2 model families; opposed stances assigned at spawn; a post-hoc divergence gate that re-spawns drafts clustering too close), then cross-compares — agreements, forks, the load-bearing assumption under each fork, the angle nobody took — and ships that map **unreconciled**. It runs no critic-as-filter, no revise loop, and explicitly **no machine synthesis**. The human is the synthesizer.

## 2. WHEN A PROJECT NEEDS IT

When working through a complex topic and the model keeps returning the first, generic ("centroid") answer — the recurring pain that motivated the capability. The deliverable that escapes the centroid is genuine **spread** (divergent frames side by side), not a machine-averaged answer.

## 3. WHEN A PROJECT DOESN'T

- Checkable artifacts — that's `/harden`. `/frame` deliberately produces no clear.
- When you need a decision **locked** — that's the handoff protocol (drive-to-lock). `/frame` is its front-end (map the disagreement), not a substitute.
- Solo projects with no substrate-separation leverage.
- **Before the cross-vendor Transport exists** — see STATUS; an in-session `/frame` would silently violate its own ≥2-family bar.

## 4. STATUS

deferred, and the **more blocked** of the two. Its load-bearing requirement — mechanical ≥2-model-family diversity assigned at spawn — cannot be satisfied in-session (the Workflow/Agent runtime has no model-family selector; cross-vendor is rung-4 manual paste only, `engine-verification-ledger.md` §5 item 3). It becomes a fully-automated template skill only after the rung-3 Transport is built **and** back-ported to the template (open question). Until then: an operator-driven recipe that stages the multi-family charge for rung-4 paste — never a silent single-family in-session fallback.

## 5. PROVENANCE

Surfaced in the 2026-06-19 reasoning pass as the honest answer to the open-ended case: because there is no oracle, any machine synthesis is an untrusted first-pass **clear** the harness rules forbid — so the machine must not reconcile, and the human is the synthesizer. **Affirmed** (not merely permitted) by the autonomy model: "Direction-setting remains the human's job — catching that the whole frame is wrong" is the irreducible-human set (`memory-engine-v3-autonomy-model-spec.md:93,103`, T-none). The structural pieces are existing doctrine: opposed-stances-at-spawn and harness-fixed panels/prompts (autonomy §76: "Panel membership and the refute-prompt are harness-fixed, never actor-influenced"); the divergence gate must read actual draft content, not self-reported divergence (autonomy §8a: self-report is defense-in-depth, never the boundary).

## 6. DEPENDENCIES

- **Mechanical ≥2-family spawn** = the rung-3 Transport / handoff build-item 1. *Specced-not-built; the single hard blocker.*
- The same packaged spawn-N primitive (handoff item 2) for the proposer fan-out and the divergence gate.
- The **divergence gate** as a deterministic checker (pairwise draft similarity → re-spawn), reading actual content (autonomy §8a). Net-new script.
- The unreconciled-map **output format** — authorable as a fenced SKILL.md format block (the `flight-plan` SKILL.md fenced-block pattern).
- The **handoff protocol** — `/frame` is its no-oracle front-end (map → human ratifies).

## 7. AUTHORING GUIDE

The flow:

1. **Diverse proposers.** N agents, each the whole problem from a different frame. Diversity is **mechanical**: ≥2 model families; opposed stances assigned at spawn (not requested in the prompt body); a post-hoc divergence gate that compares the N drafts and re-spawns any clustering too close.
2. **Cross-compare, don't synthesize.** Extract agreements (probably robust), forks, the load-bearing assumption driving each fork, and a reframe-attack pass ("what frame did none of these take?"). **No winner picked, no merge.**
3. **Ship unreconciled.** Output = the disagreement/tensions map. The operator resolves the forks — that is the synthesis, and it is theirs.

**Anti-patterns (must avoid):**

1. **Any machine clear.** Picking a winner, merging framings, or emitting a single "answer" re-smuggles the untrusted generative clear. The output stays a map. (This is the *inverse* of `/compile`'s single-writer synthesis and RECONCILE's bounded synthesis — do **not** compose with those.)
2. **Silent single-family fallback.** The most dangerous failure: with no ≥2-family path the skill falls back to one family and looks like it works while violating its own bar. Hard-fail, or downgrade to an operator-fired rung-4 paste packet — never silently proceed.
3. **Diversity-by-prompt.** "Take a different angle" in the prompt is the asking-nicely diversity the directive bans; same-base proposers cluster and the map collapses toward the centroid. Enforce via spawn (families/stances) + the content-reading divergence gate.
4. **Divergence gate on self-report.** The gate reads actual draft content; a model's self-assessed "I'm divergent" is defense-in-depth only (autonomy §8a).

## 8. KNOWN LESSONS

Not yet built. From the reasoning pass:

- Prompted diversity is false diversity; raising N without mechanical family/stance diversity amplifies a shared bias and inflates apparent confidence without adding coverage.
- The map is the product. Resisting the urge to add a "summary recommendation" is the whole discipline — a summary is a clear.
- `/frame`'s value is the spread; present the forks and their driving assumptions, not a ranking.

## 9. OPEN QUESTIONS

- Does the divergence gate actually fire / catch clustering in practice? Build it **instrumented** (log pairwise similarity, count re-spawns) before trusting it — provisional and same-family-unverified.
- Does "cross-comparison without synthesis" actually stay clear, or does "which agreements to surface / how to phrase a fork" re-smuggle a generative clear under a different name? (frontier item; needs a cross-vendor pass)
- Will the rung-3 Transport back-port to the template, or does `/frame` stay an operator-driven recipe permanently? (`handoff-engine-spec.md:195`)
- Who classifies a target as open-ended vs checkable at intake without that classifier itself being a same-family clear? (the operator's choice-at-invocation is the current answer — keep it human.)

## 10. MIGRATION STEPS

(Empty — not yet built, and gated behind the Transport. On promotion at build-step 6, this field documents wiring `extracted/frame/SKILL.md` + the divergence-gate checker + the family-selection path into `.claude/skills/`.)
