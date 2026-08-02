# Recipe: /harden (deferred)

## 1. WHAT IT IS

A checkable-loop orchestrator. Point it at an artifact whose defects are checkable against the artifact itself (spec section, code, proof, citation set, internal-consistency argument). It spawns N decorrelated critics that each produce a checkable defect (quoted span + defeater), adjudicates each break with a second independent critic before it reaches the reviser, revises, tracks killed defects in an append-only ledger as regression checks, and exits on a mechanical **dry** predicate (K rounds adding zero live confirmed defects). The **stop** (dry) is wired apart from the **clear** (a separate fresh-context acceptance gate, cross-vendor for high stakes). Output: the hardened survivor + the kill-ledger + a blind-spot map. The operator ratifies; they do not re-reason.

## 2. WHEN A PROJECT NEEDS IT

When a T1/keystone artifact with checkable defects must be driven to a defensible state and hand-running a subagent panel each time is the current practice (the reverify-panel pattern, `wf_932f328e-e21`). The checkable subclass is where the "recognition > generation" floor actually holds.

## 3. WHEN A PROJECT DOESN'T

- Open-ended / no-oracle deliberation where the frame may be wrong — that's `/frame`. Running `/harden` there **selects for fluent centroid-wrongness** (a polished, self-consistent draft is hardest to break, so it goes dry fastest).
- Anything the deterministic stack (tests/regression/mutation) already clears — don't pay model prices to recheck what a script checks free (`handoff-engine-spec.md` §2/Move 1).
- Terminology/spec interviews — that's `/preflight`.
- Low-stakes questions where the centroid is fine — price the loop first.

## 4. STATUS

deferred. Buildable to **high** strength on same-family-only once the packaged spawn-N primitive (handoff item 2) exists; its loop is already demonstrated hand-run (`memory-engine-v3-pillars-reverify-panel.md`). Only the high-stakes cross-vendor **acceptance** gate is blocked (needs rung 3 / build-item 1), and it degrades gracefully to a manual rung-4 paste packet.

## 5. PROVENANCE

The productization of the hand-run detect→adjudicate→revise→fold verification loop the project already executes (`reverify-panel.md`, `wf_932f328e-e21`: verifier + per-finding refuter + composition critic, walled off from prior passes). The in-repo realization of handoff build-items 2 (portfolio Workflow) + 3 (verdict schema). Verdict shape adopted from `harness-v2.0/cross-model-review-trial-protocol.md` §3 (`verdict_tag {concur|advisory-findings|escalate}` + premise table with `evidence_ref` + per-finding confidence + `named_disagreements` + null-safe spend block) and the v3.0 verdict-table tags `[KILL|FATAL|SERIOUS|WEAKEN-FIX|MINOR]` + `CONVERGED/NOT-CONVERGED` (`pillars-cross-vendor-prompt-v2.md`, proven conformant across 3 vendors). Stress-tested in the 2026-06-19 reasoning pass, which killed the unconditioned "loop-until-dry converges on correctness" claim and forced the stop≠clear separation.

## 6. DEPENDENCIES

- Packaged spawn-N-decorrelated primitive = handoff build-item 2 (rung-1 fan-out is Workflow/Agent-native now; the wrapper is not).
- Verdict schema (v2.0 §3 + v3.0 tags).
- Checkable-evidence leg — the memory-engine `corpus_support` + VERIFY pattern (defect = claim + verbatim span re-resolved at a pinned SHA by a repo-blind verifier; `memory-engine-v3-spec.md` §7/§10). If the memory engine (P1–P5) slips, reimplement the evidence-pin pattern standalone — it is fully specified.
- Structural decorrelation — the verifier≠author-substrate rule enforced from invocation metadata (memory spec §5/§14; v2.0 §2 firewall).
- High-stakes acceptance gate — rung 3 (API transport) or rung 4 (manual paste) for a ≥2-family clear.
- Composes with `/audit`'s detect leg (quote-required defect queue); `code-review` / `security-review` / `verify` compose as wrappable checkable-defect critics.

## 7. AUTHORING GUIDE

The loop:

1. **Critics (break leg).** N fresh-context critics in refute mode, spawned structurally (see output, not reasoning). Each finding **must** be a checkable defect: a quoted span + a stated defeater confirmable against the artifact (contradiction, gap, dropped requirement, broken citation, failing repro). An objection without a checkable referent is recorded as an open disagreement, never applied as an edit.
2. **Adjudicate gate (the drift-stopper).** A break does not reach the reviser until a **second independent critic** confirms it from the quoted span. Type each objection: `falsifiable-and-checked` = mandatory fix; `unverifiable-judgment` = open disagreement, not an edit. Maps to autonomy §4 ("a single in-family LLM PASS is insufficient; unverifiable-and-consequential ⇒ escalate").
3. **Revise + defend.** Each round spawns a defend-the-draft agent arguing the revision degraded the artifact — a restoring force, so the loop is a two-sided contest, not a one-way ratchet toward the hedging centroid.
4. **Defect ledger.** Append-only, keyed by defect identity. Every killed defect becomes a regression check the next revision must not reintroduce (the reasoning pass found a real in-repo killed-defect resurrection — autonomy §11 rung-A; the ledger is what prevents it).
5. **Dry predicate (the STOP).** Dry = K rounds adding zero **new live confirmed** defects, where "live" = the current survivor still exhibits it (repro fires), **not** a judge's similarity call. Severity-weight: low nits never block exit or reset the counter. This is a scheduling decision the agent gets **no vote** in (autonomy §2: pure harness-computed disposition).
6. **Acceptance gate (the CLEAR, separate).** Promotion is gated by a **separate** fresh-context pass — cross-vendor for high stakes (rung 3, else rung-4 paste). **Fast-dry is a RED flag** (correlated capture), not green. Never wire dry → acceptance directly.

**Anti-patterns (must avoid):**

1. **Dry = clear.** If the dry-detector's output feeds acceptance with no independent gate between, stopping *is* the banned same-family clear. Keep them apart.
2. **Same-family acceptance.** The clear leg must be cross-vendor (or manual paste); same-family can break, never clear.
3. **Judge-based dedup.** "Is this defect new?" answered by an LLM equivalence judge re-imports the centroid. Use repro/checkability, not similarity.
4. **Dry-predicate in prose.** It must be a deterministic checker **script** (cf. flight-plan → `check-reference-integrity.py`), not a SKILL.md instruction.
5. **Running it on no-oracle targets.** Partition at intake; route open-ended targets to `/frame`.

**Pre-flight (SKILL.md Step 0, when built):** refuse to run on a target with no checkable-defect form (route to `/frame`); require the artifact + the pinned SHA / source it is checkable against.

## 8. KNOWN LESSONS

Not yet built. From the hand-run precedent and the reasoning pass:

- The detect→adjudicate gate is the load-bearing piece; without it, refute-mode critics manufacture objections at a nonzero rate and revisions drift a correct draft toward the hedging centroid.
- The kill-log manufactures confidence proportional to effort, not correctness. When presenting: lead with the blind-spot map and residual live-defects; put "survived N attacks" last, labeled "confers no positive evidence" (break-vs-clear at the presentation layer).
- Tier the legs — never put judge/verify legs on a cheap tier (`handoff-engine-spec.md` §7).

## 9. OPEN QUESTIONS

- Where does the dry-predicate checker script live, and against what fixture catalog (cf. autonomy §8 `--self-test`)?
- Couple to the memory engine's `corpus_support` substrate, or reimplement the evidence-pin standalone to avoid schedule-coupling to an unbuilt engine?
- Does the adjudicate gate's "second independent critic" need to be cross-family, or is same-family fresh-context enough for the **break**-confirmation leg (it is a break, not a clear)?

## 10. MIGRATION STEPS

(Empty — not yet built. On promotion to a toggled capability at build-step 3, this field documents wiring `extracted/harden/SKILL.md` + the dry-predicate checker script into `.claude/skills/` and the deterministic-check path.)
