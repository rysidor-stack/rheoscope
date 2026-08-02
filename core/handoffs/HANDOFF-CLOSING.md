<!-- Canonical handoff-closing protocol. Since v3.0-78 (2026-07-31) executed by /handoff (T2/T3 inline; T1 headless close leg). The /handoff-close skill
     (.claude/skills/handoff-close/SKILL.md) remains its protocol document, executed by the leg.
     Methodology evolved and generalized from a production project; reconciled into the harness in v1.1. -->

# Handoff Closing Spec

*Read this when locking a decision informed by one or more handoff rounds. You are running the deliberation phase. `/handoff-close` walks these steps.*

## Session selection (substrate check)

Identify which session you are relative to this handoff:

- If `meta.yaml.authored_by` matches your substrate → you authored the brief (you have a hypothesis anchor).
- If any `output-round-*.md` was written by your substrate → you have an anchor in that round.
- If neither → you are fresh substrate.

Tier-dependent rule:
- **T1** — prefer a fresh session to close. If you authored the brief or any output round, recommend the operator open a fresh session.
- **T2 / T3** — the orchestrator session is acceptable; a receiving substrate that authored an output should not close.

State which case applies in your first response. Do not proceed silently.

## Read order

1. `meta.yaml` — confirm `status: answered`; review tier, round count, prior outcomes, substrates used.
2. `brief.md` — including round addenda.
3. `context.md` — the constraints the answers were given against.
4. `output-round-1.md` … `output-round-N.md` — the full verifier voice, in order.
5. Any companion artifacts (orchestrator-drafted confidence audit, primary-source verification files).

## Convergence check

Diff round N's load-bearing findings against round N-1's:

- **New findings in round N** — list them; if load-bearing, name explicitly.
- **Refinements** — round N tightened or quantified something round N-1 already named.
- **Reversals** — round N contradicted round N-1; list with each round's reasoning.

State a convergence verdict in writing (it goes in the ADR raw file):
- **Convergence reached at round N-1; round N is refinement-only** → lock now.
- **Not converged; round N surfaced a new load-bearing finding** → decide whether that finding is the *surface the lock acknowledges* (with a reopen trigger) or whether another round is warranted.
- **Reversal at round N** → load-bearing for the lock; the raw file must name what reversed and why.

**RTDT:** at T2/T3 with two convergent rounds and a new finding in round N, the new finding is usually the surface the lock acknowledges (with a reopen trigger), not a reason for another round. Say so explicitly when it applies.

## Locking deliberation (with the operator)

1. **What the rounds settled** — the locked answer to the brief's hypothesis.
2. **What they challenged** — what the hypothesis got wrong, if anything.
3. **What remains open** — operational data the operator has and the verifiers didn't, or genuine reasonable-disagreement points.
4. **Anchoring-risk audit** — did the hypothesis get rationalized through the outputs, or genuinely tested? If `hypothesis_outcome` is `confirmed` and the verifier reasoning looks thin, push back before locking.
5. **Reopen triggers** — the operational signals that would revisit this lock.
6. **Articulate the lock** as a clear position whose reasoning survives being read in 6 months.

## Closure protocol

When the operator locks, perform these filesystem operations (single-writer rule — this session is the only one editing during the lock):

1. **Write the decision raw file** at `docs/adr/<YYYY-MM-DD>-<n>-<slug>.md`. `<n>` is the next sequential decision number across the project (unpadded — scan `docs/adr/` for the highest; start at 1 if none). The canonical ADR shape (Decision / Reasoning / Commitments / Re-open triggers / Operational verifications / What does NOT change / Methodology notes) carries front-matter fields including `Informed by:` (the handoff_id(s)), `Decision method: handoff`, `Supersedes:`, and a **Substrate sequence** line naming every substrate that contributed across rounds (e.g. "model family A (author) → a different family (round 1) → family A again (round 2 pressure-test) → fresh family A session (close)"). See ADR-1 / `docs/governance/DECISIONS.md` for the in-project convention.
2. **Write `confidence-audit.md`** in the handoff folder — per-finding confidence (high ~85–95% / moderate ~70–80% / lower <70%), each with its failure mode if wrong and recovery cost (small/moderate/large). If an orchestrator-drafted `confidence-audit.md` already exists, integrate rather than overwrite.
3. **Update `meta.yaml`:** `status: locked`; `locked: <today>`; `locked_by_raw_file: <relative path to the ADR>`; confirm `hypothesis_outcome`.
4. **Update `core/handoffs/INDEX.md`:** move the row from Active to Archive (`locked`, with a one-word outcome and a link to the raw file).
5. **Append to `docs/governance/DECISIONS.md`:** a table row pointing at the ADR (create the file with a header + table schema if it doesn't exist yet — first row sets the example).
6. **Update the flight plan (conditional):** `/flight-plan` is a core skill, but a project only has flight plans once one is authored. If any exist (`wiki/flight-plans/*.md`), mark the decision locked inline (date + one-line summary + cross-link to the ADR); don't restructure the plan. If none exist, this step is a no-op — say so in the closeout.

## HALT path (alternative to locking)

If closing deliberation surfaces a load-bearing upstream dependency that blocks the lock:

1. Write `HALT.md` in the folder — the blocker, what would unblock it, what work is preserved as audit trail.
2. Update `meta.yaml`: `status: halted`.
3. Update `INDEX.md`: status → a halt reason (e.g. `halted-provider-dependency`; legend in the INDEX header).
4. Do NOT write the decision raw file — no decision was locked.
5. Note the block in the flight plan (if one exists), linking to `HALT.md`.

The lock resumes later via a new handoff or by re-running `/handoff-close` after the upstream resolves.

## After lock

The handoff folder is immutable. A later revision is a *new* handoff or a successor ADR with `Supersedes:` linkage — never an edit to the locked one.

## Scope boundary

`handoff-close` does NOT: update wiki articles (that's `/compile`), author new handoffs (`/handoff-author`), edit prior outputs/brief/context (immutable), or re-litigate prior round verdicts (they're inputs; the lock is downstream). The raw file is the locker's synthesis in the locker's voice — verifier voice lives immutably in `output-round-*.md`, not paraphrased into the ADR.
