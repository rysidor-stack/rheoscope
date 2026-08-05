<!-- Canonical handoff-authoring protocol. SUPERSEDED AS AN OPERATOR SURFACE by /handoff (v3.0-78, 2026-07-31): the /handoff skill runs this authoring phase in its single pass. Historical orchestrator: the retired /handoff-author skill
     (.claude/skills/handoff-author/SKILL.md, removed) was the operational orchestrator for this spec.
     Methodology evolved and generalized from a production project; reconciled into the harness in v1.1. -->

# Handoff Authoring Spec

*Read this before authoring a handoff. You are an orchestrator session creating a directed, substrate-separated inquiry. `/handoff`'s authoring phase walks these steps (the retired `/handoff-author` skill once did); this doc is the canonical protocol and the reasoning behind it.*

## What a handoff is

A handoff is the substrate-separation principle applied to inquiry routing. The orchestrator (Builder) authors a question + hypothesis. A receiving session on a *different* substrate (Verifier) returns analysis. A locking session uses the analysis to make the decision. Three roles, separated substrates, one decision.

A handoff may run for **multiple rounds**. Each round is a fresh substrate-separated pass that either *extends* the inquiry (new questions) or *pressure-tests* the prior round's findings. Rounds accumulate as `output-round-1.md`, `output-round-2.md`, … in one folder; the decision is locked against the whole sequence at close.

Reach for a handoff when:
- A T1 or T2 decision is open and the orchestrator's working hypothesis would benefit from a substrate-different challenge.
- The question requires research or recon the current session can't perform well (deep web search, external recon, large-corpus reading).
- The orchestrator notices itself anchoring on a position and wants verification before locking.

Do NOT reach for a handoff when:
- The decision is reversible and low-stakes (T3/T4) — friction outweighs benefit. (T4 must not handoff.)
- The question is operational, not architectural — write code, don't write briefs.
- The orchestrator already knows the answer and wants ratification — RTDT and just decide.

## Folder structure

```
core/handoffs/<YYYY-MM-DD>-<slug>/
├── README.md            ← orients the receiving session (reading order, file map)
├── meta.yaml            ← provenance + status + round model + decision links
├── context.md           ← project compass + handoff-specific context + deeper pointers
├── brief.md             ← question + hypothesis + deliverable shape (+ round addenda)
├── packet-round-N.md    ← self-contained bundle to paste into the verifier substrate
├── output-round-N.md    ← the verifier's answer for round N (one per round)
├── confidence-audit.md  ← written at close: per-finding confidence + failure mode
└── HALT.md              ← only if the handoff halts on an upstream blocker
```

Slug is the decision being informed if known, else the topic. Examples: `decision-3-migration-and-hosting`, `vendor-dark-zone-sweep`.

## The round model

**Round 1** establishes the inquiry: tier, hypothesis, load-bearing questions, target substrate, scope, references, anti-patterns.

**Round N+1** extends or pressure-tests. Two rules govern it:

1. **Substrate-separation across rounds.** The round N+1 target substrate must differ from *every* prior round's substrate (tracked in `meta.yaml.answered_by`). A second opinion from the same substrate isn't separation.
2. **Convergence check (anti-asymptotic-confidence trap).** Before authoring round N+1, require an explicit answer to "what's *new* this round?" If the only answer is "more confidence on the same items," warn the operator: refining confidence rather than discovering new issues is the asymptotic-confidence trap. State the tier's default round cap (T1: up to 3; T2: up to 2; T3: 1 is usually enough). The operator may override — capture the override reason in `meta.yaml.round_overrides`. Do not proceed silently.

## meta.yaml

**This block is the canonical meta shape, and this file is its one home.** The enforcing
sensor is `deploy/check-loop-state.py`: it requires exactly these keys on current-protocol
folders and treats unknown keys as violations, and its `--self-test` asserts key-for-key
agreement with this very block — a shape change lands in both files in the same commit or the
self-test fails. (Reconciled 2026-08-05: this block had drifted from the sensor — four
missing keys, three retired ones — so a meta authored from it drew seven violations.)

```yaml
handoff_id: <YYYY-MM-DD>-<slug>
status: open                  # open | answered | locked | halted | superseded
tier: T2                      # T1 | T2 | T3 (T4 does not handoff)
authored: <YYYY-MM-DD>
authored_by: <substrate>      # e.g. "model family and version (Claude Code orchestrator)"
answered: null                # date the round's output was filed
answered_by: []               # per round: the substrate that produced output-round-N.md
decision_under_investigation: "<one sentence: the decision this handoff informs>"
parent_phase: <phase-slug>    # the flight-plan phase this decision belongs to (null if none)
hypothesis_carried: "<one-sentence hypothesis the brief carries>"
hypothesis_outcome: pending   # confirmed | revised | rejected | pending
target_substrate: <desc>      # substrate this round's brief is routed to
rounds_completed: 0           # bumped when a round's output is filed
round_modes: []               # per round, e.g. ["round 1: initial", "round 2: pressure-test"]
round_overrides: []           # convergence-check overrides, with reasons
locked: null                  # date the decision locked
locked_by_raw_file: null      # relative path to the lock raw at close
supersedes: null
superseded_by: null
```

Optional keys the sensor also accepts: `packet_modes` (per-round `inline`|`reference`),
`locked_by` (the locking substrate identifier, copied from the close leg's attestation),
and `close: pending` — the park marker for a dead headless close leg, legal ONLY alongside
`status: answered`.

Retired keys (pre-2026-07 shape; the sensor flags them as unknown): `informs_decisions` and
`hypothesis` were renamed to `decision_under_investigation` and `hypothesis_carried`;
`governing_documents` was dropped — governing docs ride `context.md` Section 2. The legacy
`verdict_summary` is likewise deprecated — decision content lives in the lock raw at close,
not in meta.

## context.md — three sections

**Section 1 — Project compass.** Paste the canonical block from `core/governance/PROJECT-COMPASS.md` verbatim. Identical across handoffs at authoring time. If the compass is wrong, fix the canonical file first, then paste.

**Section 2 — Handoff-specific context.** The architectural constraints, locked decisions (cite the `docs/adr/` files), and methodology constraints relevant to *this* question. Be specific: name the locked decisions, the open ones, and the constraints that bound the answer space.

**Section 3 — Deeper context pointers.** Relative paths + one-line descriptions of files a filesystem-equipped receiving session can fetch. Sessions without filesystem access ignore this; sessions with access use it as a reading queue.

## brief.md — six sections

1. **Instructions to receiving agent** — calibration (operator's reading depth, technical level), web-search expectation, role framing. Written in second person, *to* the verifier.
2. **The decision being informed** — which decision in which plan/phase; why it matters.
3. **The hypothesis being tested** — the orchestrator's current position, named explicitly so the verifier can attack it.
4. **The load-bearing questions** — numbered, specific, falsifiable where possible.
5. **Deliverable shape** — the section list of expected output (verdict tag, analysis, recommendations, uncertainty disclosure).
6. **Anti-patterns to avoid** — failure modes specific to this inquiry.

For **round N+1**, do not rewrite `brief.md` — append a `## Round N addendum` naming what's new, the mode (extension vs pressure-test), and the substrate constraint. Prior outputs, README, and context are immutable.

## packet-round-N.md — the self-contained bundle

Because the verifier may run on a substrate with no filesystem access, every round produces a `packet-round-N.md` that inlines everything the verifier needs, in six sections:

- **§ 0** Receiving protocol — a summary of `HANDOFF-RECEIVING.md`.
- **§ 1** Project Compass — verbatim from `core/governance/PROJECT-COMPASS.md`.
- **§ 2** Handoff-specific context — from this folder's `context.md § 2`.
- **§ 3** Brief — `brief.md` in full, including round addenda.
- **§ 4** Required prior artifacts — for round N+1, all prior `output-round-*.md` verbatim, clearly separated.
- **§ 5** Required reference materials — inlined files; URLs flagged for web fetch.
- **§ 6** Meta-summary — tier, round number, mode, prior substrates (so the verifier can confirm separation), prior-round outcomes.

**Inlining rule:** walk references one level deep and paste verbatim (never paraphrase — substrate separation requires the verifier see the actual text). Confirm inclusions with the operator before finalizing. **Token budget:** if a packet exceeds ~80K tokens, offer to truncate § 5 or compress brief sections — *never* truncate prior outputs in § 4.

## After authoring

Hand `packet-round-N.md` to the chosen substrate — `/handoff`'s contained bridge leg does this in-pass; pasting into an external tool is the manual (Mode B) alternative. Authoring ends here: filing the returned output is the receiving phase (`HANDOFF-RECEIVING.md`) and locking is the closing phase (`HANDOFF-CLOSING.md`), both run by the same `/handoff` pass.

## Style notes

- `brief.md` is written to the verifier, in second person.
- Name the hypothesis explicitly — burying it weakens the substrate-separation effect.
- Deliverable shape should be specific enough that a well-formed answer is recognizable.
- Dense beats exhaustive. Keep `brief.md` under ~1,500 words.
