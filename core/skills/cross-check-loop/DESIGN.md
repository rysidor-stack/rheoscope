# cross-check-loop — design rationale

The managed cross-vendor verification **loop** between single-shot `/cross-check` (one claim, one
verdict, fold in) and a full locked `handoffs/` handoff (multi-round, substrate-separated, locked).
It exists for the case single-shot can't hold: a decision or build-completion resting on **several
interlocking sub-claims**, where correcting one piece can invalidate another and you'd never see the
ripple.

## The shape I chose: a thin mechanical runner + a judgment skill

Two files, a deliberate split along the line of *what a script must enforce* vs *what only judgment
can do*:

- **`converge.js`** — the **round runner + honesty gate + ledger keeper**. It runs one round (N
  parallel `verify-cli` calls, one per active claim), records each verdict + the SHA of the evidence
  that produced it, enforces the gates, and proposes (never decides) continue/converge/escalate.
- **`SKILL.md`** — the **judgment half**. Decompose into claims, gather/re-ground primary artifacts,
  integrate verdicts, **name the cascade**, decide converged-vs-escalate, write the escalation packet.

Why both, and not just a SKILL.md like `cross-check`? Because the mission's own hint is right: "what's
new this round?" and "which claims cascade?" are judgments, but **the things that keep the loop honest
are mechanical** — and a session running the loop by hand will, under pressure, skip exactly those. A
script can refuse. The single-shot skill needed no script because it has one claim and one call;
a multi-round, multi-claim loop has bookkeeping whose *integrity* is the whole point.

Why not a heavier engine (the prior-art recipe's shape)? The prior-art directory was empty — nothing
to inherit — so I built minimal from first principles. The runner is ~290 lines; the ledger is plain
session-authored JSON; there are six claim states and three proposals. No daemon, no DB, no schema DSL.

## The central challenge — keeping the loop honest across rounds — and how it's solved

> "A loop that feeds round N+1 the round-N verdict as evidence has automated rubber-stamping."

The load-bearing check every round is the **same cross-vendor call** (`verify-cli` → GPT) the proven
single-shot uses — never same-vendor reasoning with a verify veneer. On top of that, **defense in
depth against the echo**, enforced by `converge.js`, not promised by prose:

1. **`verdicts/` is write-only-by-script**; an evidence file inside it is HARD-BLOCKED.
2. **Verdict-shaped evidence is HARD-BLOCKED** — and the check is widened past the naive form so a
   verdict with `verifier`/`uncertainty` *stripped off* (the cheap bypass) is still caught.
3. **Evidence outside the run dir is HARD-BLOCKED** (also the taint containment).
4. **The one a loop must nail:** a `recheck` claim whose evidence is **byte-identical (same SHA) to the
   artifact it was last verified against** is HARD-BLOCKED. A re-check on the same bytes re-verifies the
   prior conclusion, not the correction — *this is the exact false-convergence path*, and it is now a
   gate, not an honor-system note. (This was the HIGH finding from the adversarial review; the first
   build only *recorded* SHAs and never *compared* them — recording is an audit trail, comparing is the
   guarantee.)
5. **Convergence is qualitative and state-driven, never a number.** The proposal reads only current
   state: a fresh revision lives in `needs-action` (must be processed), a cascaded re-check lives in
   `recheck` (active, must be re-run on fresh evidence), and "converged" means *nothing left to do* —
   not "we agreed again." There is no confidence percentage anywhere.
6. **Forcing functions:** the next round refuses to run while any claim is `needs-action` (you can't
   sprint past a correction) or `escalate` (that's the handoff signal); a `recheck` with no
   `recheck_reason` is blocked (a re-check must name *why*).

What the script **cannot** do, stated honestly in the SKILL: detect narrative prose that mimics a real
artifact and carries a plausible provenance line. That residual is the session's evidence-contract
obligation (provenance gate, inherited from `cross-check`), and the GPT verifier's skeptic mandate is
the **behavioral** backstop — proven in the demo, where it rejected every over-scoped/under-evidenced
claim.

## What I left OUT (and why)

- **No confidence scores / deltas / metrics / verdict database.** Convergence is qualitative by rule;
  load-bearing results get recorded where the session already records things (`raw/`, a receipt).
- **No automatic decomposition or cascade detection.** Both are judgments. The script only enforces
  that a cascade is *named* (`recheck_reason`) and that `depends_on` references real claims; it surfaces
  declared dependents as a hint when a claim breaks, but never infers the cascade. (`depends_on` earns
  its place as a scaffold, not as routing.)
- **No locking and no approval.** Verification ≠ approval; the loop is not the locker (the evaluator
  never locks — the handoff protocol). Escalation produces a *stub*, not a locked handoff.
- **No cap override.** the handoff protocol allows a named override inside a handoff; the loop deliberately does
  not — hitting the cap is precisely when you should escalate, so the cap is hard. T1 → handoff; T4 →
  don't loop.
- **No repo-grounded verifier and no Codex-side mirror.** Evidence is inlined raw artifacts (same as
  `cross-check`); the verifier reads nothing. Both are explicitly later, separate builds.
- **No new state for cosmetics.** Six states, three proposals, one optional `kind`/`tier`. Everything
  that didn't drive control flow was cut.

## What the build went through

- 26-case deterministic self-test (`selftest.sh`) covering every gate offline (no GPT), incl. the two
  anti-echo holes (stale-recheck SHA, stripped verdict) and a full mocked round.
- A 42-agent adversarial review (8 dimensions → per-finding cross-verification → synthesis): 33 raw
  findings, 31 confirmed. Its verdict on the first build was blunt and correct — *"the central honesty
  guarantee is BROKEN"* by the stale-recheck hole. All 31 confirmed findings are now fixed or
  consciously acknowledged in prose.
- A live end-to-end run on a real multi-claim build-completion: 2 rounds, a revision cascading into a
  re-check (with the cascade judgment recorded, including a *no-cascade* decision for one claim), the
  anti-echo SHA gate enforcing genuine re-grounding, and a clean **escalate-to-handoff** when the T2 cap
  was reached with the evidence still not satisfying an independent skeptic.
