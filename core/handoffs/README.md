# Handoffs

> **Skill collapse (v3.0-78, 2026-07-31):** the three orchestrator skills named below
> (`/handoff-author`, `/handoff-receive`, `/handoff-close`-as-operator-surface) collapsed
> into the single entry point **`/handoff`** — one pass authors, dispatches the
> cross-vendor answer leg over the contained bridge, files the output, and locks (T2/T3
> in-pass; T1 via a headless close leg + one operator yes). `/handoff-close` survives as
> the close leg's protocol document. The three-phase PROTOCOL below is unchanged — only
> its execution collapsed. Where a project carries `handoffs/METHODOLOGY.md`, that file
> supersedes the three phase docs here.

Handoffs are the substrate-separation principle applied to inquiry routing. When a decision is open and the orchestrator's working hypothesis would benefit from a substrate-different challenge, the orchestrator authors a directed inquiry; another session on a different substrate answers it; a third (or the orchestrator, post-cooldown) locks the decision against the answer.

Three sessions, three substrates, one decision.

## Lifecycle

A handoff moves through three phases and may run for multiple rounds. Each phase has a canonical spec (this directory) and an operational orchestrator skill (`.claude/skills/handoff-*`):

1. **Authoring** — `HANDOFF-AUTHORING.md` / `/handoff-author`. The orchestrator creates `core/handoffs/<YYYY-MM-DD>-<slug>/` with `meta.yaml` (status: `open`), `context.md`, `brief.md`, `README.md`, and a self-contained `packet-round-N.md` to hand to the verifier substrate. The hypothesis is named explicitly so the verifier can attack it. Round N+1 runs a convergence check (anti-asymptotic-confidence trap) before extending.
2. **Receiving** — `HANDOFF-RECEIVING.md` / `/handoff-receive`. A different-substrate session reads the brief, takes a position, writes `output-round-N.md`, and flips status to `answered`. (Mode B files output produced on an external substrate.)
3. **Closing** — `HANDOFF-CLOSING.md` / `/handoff-close`. A locking session (preferably fresh for T1) diffs the rounds (convergence check), runs deliberation with the operator, writes the ADR plus a `confidence-audit.md`, and flips status to `locked`. If a load-bearing upstream blocker surfaces, it takes the HALT path instead.

## Where decisions land

Decisions resulting from handoffs land at `docs/adr/<YYYY-MM-DD>-<n>-<slug>.md` with two metadata fields in the front matter:

- `informed_by:` — list of `handoff_id`(s) that informed this decision
- `decision_method:` — one of `handoff`, `grill`, or `direct`

`handoff` indicates a decision routed through this protocol. `grill` indicates a decision routed through the stress-testing capability (if enabled). `direct` indicates a decision made without a structured intermediary.

## Where this fits

Handoffs are a core capability of the harness, not opt-in. `core/governance/CLAUDE.md` lists `core/handoffs/` among the always-loaded core directories. The methodology layer (`core/methodology/execution-engine.md`, `verification-architecture.md`) references handoffs as the canonical mechanism for substrate-separated inquiry on T1 and T2 decisions.

## Files in this directory

- `HANDOFF-AUTHORING.md` — canonical authoring protocol (round model, packet bundle, meta.yaml)
- `HANDOFF-RECEIVING.md` — canonical receiving protocol (two modes, substrate-separation check)
- `HANDOFF-CLOSING.md` — canonical closing protocol (convergence check, confidence audit, HALT path)
- `INDEX.md` — the Active/Archive table of handoffs (the authoring skill creates it on first handoff)
- `README.md` — this file

These three specs are the canonical protocol; the `.claude/skills/handoff-author|receive|close` skills are the operational orchestrators that walk them. Docs and skills are kept in agreement — harness v1.1 reconciled the elaborate (multi-round) methodology into these docs.
