<!-- Canonical handoff-receiving protocol. SUPERSEDED AS AN OPERATOR SURFACE by /handoff (v3.0-78, 2026-07-31): bridge answer legs and --file-external now run this phase. Historical orchestrator: the retired /handoff-receive skill
     (.claude/skills/handoff-receive/SKILL.md, removed) was the operational orchestrator for this spec.
     Methodology evolved and generalized from a production project; reconciled into the harness in v1.1. -->

# Handoff Receiving Spec

*Read this if you are filing or producing a handoff round output. You are the Verifier in a Builder/Verifier exchange (or filing a Verifier's work). `/handoff`'s answer leg walks these steps (the retired `/handoff-receive` skill once did).*

## Two modes

A round output reaches the folder one of two ways:

- **Mode A — you are the verifier.** The operator opened a fresh session on a substrate that satisfies the brief's `target_substrate`, pointed it at the handoff folder (or pasted `packet-round-N.md`). You produce the output.
- **Mode B — file external output.** The operator ran the verifier elsewhere (e.g. a different frontier model family) and is pasting the result back to be filed.

If the mode is unclear, ask the operator.

## Determine the round

Scan the folder for existing `output-round-*.md`. The new output is the next sequential round. If `meta.yaml.rounds_completed` disagrees with the file count, trust the file count and flag the discrepancy.

## Read order (Mode A)

1. `meta.yaml` — provenance, tier, target substrate, prior-round substrates.
2. `context.md` — project compass (§ 1), handoff-specific context (§ 2), deeper pointers (§ 3, optional).
3. `brief.md` — the question + deliverable shape, including any round addenda.
4. Prior `output-round-*.md` files (round N+1 only).
5. `packet-round-N.md` for this round, if present (reference materials inlined).
6. Referenced files you can fetch; for the rest, work from what's inlined in the packet.

### Substrate-separation check
Confirm your substrate satisfies `meta.yaml.target_substrate` **and** differs from every prior round's substrate (`meta.yaml.answered_by`). If you cannot satisfy the constraint (e.g. you are on the same model family as a prior round), flag it to the operator before producing output. Do not proceed silently — a same-substrate "second opinion" defeats the purpose.

## Your job (Mode A)

Answer the brief and honor its deliverable shape. Standard elements:

- **Verdict tag at the top:** `confirmed` / `revised` / `rejected` with respect to the hypothesis under test.
- **Take a position.** Don't list pros/cons and refuse to recommend. State a position even if the case is close.
- **Challenge the hypothesis where warranted.** It's named explicitly so you can attack it. If it's wrong, say so plainly.
- **Cite primary sources** for non-obvious claims, with retrieval dates.
- **Honest uncertainty disclosure:** distinguish "confident" / "needs operational data the operator has and I don't" / "reasonable engineers disagree."
- **Substrate signature at the bottom:** your identifier + retrieval dates + extended-thinking on/off.

## Mode B — filing external output

Validate before writing. Does the pasted output have a verdict tag? Address the load-bearing questions? Carry a substrate signature? Cite sources where claims are non-obvious? If validation fails, flag it specifically before writing (silent filing of malformed output is worse than no filing). Gather the substrate identifier, generation date, and extended-thinking state from the operator. Do not generate analytical content in Mode B — you are filing, and the external substrate's voice must stay intact.

## Closure (both modes)

1. **Write `output-round-N.md`** in this folder. Rounds are immutable — never write over an existing `output-round-N.md`; if revision is needed, that's a new round authored via `/handoff`.
2. **Update `meta.yaml`:** `status: answered`; `answered: <today>`; append the substrate to `answered_by`; set `rounds_completed: N`; set `hypothesis_outcome` to match the verdict.
3. **Update `core/handoffs/INDEX.md`:** change the row to `answered`.

If your session has no filesystem access (Mode A on a non-CC substrate), state that at the top of your response so the operator can file the output via `/handoff` (Mode B filing).

## Anti-patterns

- Pretending to be a different substrate than you are (Mode A substrate honesty).
- Filing Mode B output without checking it has the expected shape.
- Writing over an existing `output-round-N.md` (rounds are immutable).
- Producing analytical content in Mode B (you are filing, not generating).

## Scope boundary

`handoff-receive` does NOT: write the decision raw file (that's `handoff-close`, landing at `docs/adr/`), update flight plans (that's `handoff-close`, and only when a flight plan exists), edit brief/context/README/prior outputs (immutable), set `status: locked` (close only), or run convergence checks (those run at author-round-N+1 and at close). Your scope is bounded for substrate-separation reasons: the locking session decides, you analyze.
