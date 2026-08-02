# Capability: decorrelated-review

## 1. WHAT IT IS

Two opt-in orchestrators that productize the harness's decorrelation ladder for *reasoning*, not just decision-verification: `/harden` (checkable-loop — drive an artifact with checkable defects to a mechanically-cleared survivor via decorrelated critics) and `/frame` (open-ended map — fan diverse whole-problem framings into an unreconciled disagreement map the human synthesizes). They are the **generative sibling of the handoff engine**: same ladder (`handoff-engine-spec.md` §11), same verdict schema, same wide-then-deep portfolio shape (§2/Move 1) — pointed at producing and pressure-testing reasoning instead of locking decisions.

## 2. WHEN A PROJECT NEEDS IT

When sessions repeatedly hand-crank depth — re-prompting a model several times to escape a first, generic ("centroid") answer — or hand-run ad-hoc subagent panels to pressure-test an artifact (the 7-agent reverify-panel pattern, `wf_932f328e-e21`). Once that pattern is frequent enough to earn a standing recipe.

## 3. WHEN A PROJECT DOESN'T

- Solo projects where the operator is also the reviewer and substrate-separation has no leverage (same exclusion as `/preflight`, `core/skills/preflight/SKILL.md` § Boundaries).
- Quick one-shot questions where the centroid answer is fine — both skills are expensive (N agents × rounds); price the question first.
- Anything `/preflight` (single-context terminology/spec interview) or the deterministic stack (tests/regression/mutation) already covers.

## 4. STATUS

**deferred (entirely — docs-only, not toggled).** No SKILL.md ships. The mechanical core both skills depend on — a packaged spawn-N-decorrelated primitive + verdict schema (= handoff build-items 2–3), and automated cross-vendor (= build-item 1 / rung 3) — is specced-not-built and gate-blocked. Per the harness anti-pattern (`kickoff-orchestration/RECIPE.md:35`), no skill prose is authored until that core exists and is validated from practice.

> **Update 2026-06-26 ([ADR #7](../../adr/2026-06-26-7-cross-vendor-verify-core-skills.md)):** one of the two core dependencies — **automated cross-vendor (build-item 1 / rung 3)** — is now **built and in-repo** as the `cross-check` / `cross-check-loop` core skills (`core/skills/{cross-check,cross-check-loop}` + `core/skills/bridge`). The **other** core dependency (the packaged spawn-N-decorrelated primitive + verdict schema) is still specced-not-built, so `/harden` and `/frame` **stay deferred** — but their cross-vendor acceptance leg now has a real in-repo Transport to ride, no longer "specced-not-built."

## 5. PROVENANCE

Designed across a multi-turn design conversation (2026-06-19) that started from a recurring operator pain — models defaulting to fast, generic answers and needing several manual iterations to reach depth — plus an experiment fanning an idea out to focused subagents. Two same-family adversarial Workflow passes informed the design:

- a **reasoning pass on the design itself** (5 diverse-lens framings → break-only refutation → synthesis) that broke the naive "one skill, loop-until-dry, machine-synthesized survivor" sketch and forced the checkable/open-ended partition;
- a **substrate-fit investigation** (6 subsystem readers → synthesis → fresh-context verification) that confirmed, verbatim against source, the spawn-primitive status and the four-rung decorrelation ladder.

Both passes were same-family (Opus); per the break-vs-clear asymmetry they **broke** the design but did not **clear** it — a cross-vendor pass is owed before build (ADR #5).

The load-bearing design conclusion: the regress-stopper *"recognition is easier than generation"* only holds where a defect is **checkable** against the artifact. That splits the work in two — a verification loop where the floor holds (`/harden`) and a no-oracle deliberation where it doesn't, so the machine must **not** synthesize and the human is the synthesizer (`/frame`). These map directly onto `handoff-engine-spec.md` §11 (routing criterion: evidence-groundable → evidence legs; judgment/priors → climb the ladder) and §1 (the four error classes).

## 6. DEPENDENCIES

- **handoff-engine** (`specs/handoff-engine-spec.md`) — the decorrelation ladder (§11), the wide-then-deep portfolio (§2/Move 1), the build order both skills ride. *Not yet built or gate-verified.*
- **Packaged spawn-N-decorrelated primitive** = handoff build-item 2 (parallel-portfolio Workflow). Rung-1 same-family fan-out is Workflow/Agent-native today (`handoff-engine-spec.md:176`); the packaged wrapper is not.
- **Verdict schema** — adoptable from `harness-v2.0/cross-model-review-trial-protocol.md` §3 + the v3.0 verdict-table tags (`memory-engine-v3-pillars-cross-vendor-prompt-v2.md`).
- **Automated cross-vendor** (rung 3 / build-item 1 / phase-0 transport) — required for `/harden`'s high-stakes acceptance gate and `/frame`'s ≥2-family bar. **Built + in-repo 2026-06-26** ([ADR #7](../../adr/2026-06-26-7-cross-vendor-verify-core-skills.md)): the `cross-check`/`cross-check-loop` core skills over `core/skills/bridge` (Claude → an OpenAI GPT verifier). *(Was: "specced-not-built; plan lives on the live instance, not this repo" — that is now resolved.)*
- **core/methodology** substrate-separation (verifier≠author) and the **memory-engine autonomy model** (agent-gets-no-vote / mechanical-disposition; irreducible-human frame-steering) — the structural enforcement these skills inherit.

## 7. AUTHORING GUIDE

Per-skill recipes: `deferred/harden.RECIPE.md`, `deferred/frame.RECIPE.md`.

**Shared anti-patterns (must avoid):**

1. **One skill with a mode-classifier.** `/harden` and `/frame` have structurally *opposed* gates (harden clears mechanically; frame forbids any machine clear). Folding them behind one classifier repeats v1's three-mode failure the reformulation already corrected (`stress-testing/RECIPE.md:5`). Two skills, gates wired apart.
2. **Decorrelation-by-prompt.** Diversity/independence must be **structural** — enforced by how workers are spawned (fresh context that sees output not reasoning; stances/panel fixed at spawn and never actor-influenced, autonomy §76; a divergence gate that reads actual draft content not self-reported divergence, autonomy §8a) — never a SKILL.md asking a model to "be diverse" or "be critical." A skill describing decorrelation it cannot structurally enforce is the asking-nicely the *enforce-mechanically* directive bans.
3. **Same-family clear.** Same-family fan-out can break but never clear. Never let `/harden`'s high-stakes acceptance or `/frame`'s ≥2-family bar be satisfied by same-family panels; those need rung 3 (API) or rung 4 (manual paste). Shipping `/frame` in-session before rung 3 exists makes it silently fall back to single-family while appearing to work — the failure to avoid.
4. **Stop wired into clear.** The loop's dry-predicate (a stop) must be a separate mechanical check from acceptance (a clear). Wiring them together smuggles the banned same-family clear into the exit.

**Build order (rides the handoff-engine build order, §10):**

0. **GATE FIRST** — clear the two cross-vendor adversarial design passes on the handoff-engine brief (ADR #5). Until then it is "the captured target, not a build license."
1. **These recipes** (docs-only) — this artifact. Zero gated dependency.
2. **Transport script** (handoff item 1, rung 3) — unblocks `/frame`'s ≥2-family and `/harden`'s automated high-stakes gate.
3. **Parallel-portfolio Workflow** (handoff item 2) — the packaged spawn-N primitive; `/harden`'s loop is its in-repo realization. Promote `/harden` to a toggled capability here, wiring the dry-predicate as a deterministic checker **script** (the flight-plan → `check-reference-integrity.py` pattern), not prose.
4. **Verdict schema + retro-scoring** (handoff item 3).
5. **Premise/defect ledger** (handoff item 4) — converge on memory-engine `corpus_support`, do not invent a parallel one.
6. **Promote `/frame` to toggled** once Transport makes ≥2-family real and (open question) back-ports to the template.

Each docs-only→toggled promotion routes through a fresh handoff (`README.md:65-70`; these findings are evidence, not authorization).

## 8. KNOWN LESSONS

- The two skills are **not new scope** — they are the generative lens on the handoff engine already specced. Build cost is mostly the handoff build plus two thin shells + two checker scripts.
- The Workflow/Agent runtime exposes a tier/model selector but **no model-family selector** (model options are all one vendor family). So `/frame`'s ≥2-family bar stays blocked even after the portfolio Workflow exists — the cross-vendor Transport is the only family-selection mechanism. This is why the two skills graduate on different schedules.
- The strongest evidence comes from the artifact, not the panel: `/harden`'s checkable-defect leg should reuse the `corpus_support` + VERIFY pattern (a defect = claim + verbatim quoted span re-resolved at a pinned SHA by a repo-blind verifier).
- Overlap discipline: a one-line scope cross-reference in each of `/preflight`, `/harden`, `/frame` so an operator picks the right tool — preflight = single-context human interview / terminology; harden = multi-agent checkable-defect loop with machine clear; frame = multi-agent no-oracle unreconciled map, human clears.

## 9. OPEN QUESTIONS

- ~~Does the rung-3 Transport land in **this** template repo or only on the live business instance?~~ **RESOLVED 2026-06-26 ([ADR #7](../../adr/2026-06-26-7-cross-vendor-verify-core-skills.md)): it lands in-repo** as the `cross-check`/`cross-check-loop` core skills + `core/skills/bridge`. So `/frame`'s ≥2-family bar and `/harden`'s high-stakes acceptance now *can* be fully-automated template skills (once their spawn-N primitive + verdict schema are built); the Transport is no longer the blocker (`handoff-engine-spec.md:195`).
- Does the Workflow/Agent runtime ever expose a model-**family** selector, or is the Transport the sole family-selection path? (`handoff-engine-spec.md:176` vs autonomy §76)
- One capability dir or split into two siblings at promotion? Staged as one dir now; split if harden/frame schedules diverge (the family-selector constraint says they will).
- Where do the dry-predicate checker (harden) and divergence-gate checker (frame) live as deterministic scripts, against what fixture catalog (cf. autonomy §8 `--self-test`)? Net-new; owned by nobody yet.
- Does promotion trip reopen-trigger-6 (merging firewall/orchestration engines) → full fresh-handoff, or a lightweight one? (`README.md:65-70`)

## 10. MIGRATION STEPS

(Empty — capability not yet built. Docs-only: when staged into init's Part B unconditional-propagation list, these recipes propagate to `docs/recipes/decorrelated-review/`. That init wiring is a separate step, **not yet done** — it changes instantiation behavior and should land with, or after, the build-order step-0 gate.)
