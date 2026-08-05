# Handoffs

> **Skill collapse (v3.0-78, 2026-07-31):** the three orchestrator skills named below
> (`/handoff-author`, `/handoff-receive`, `/handoff-close`-as-operator-surface) collapsed
> into the single entry point **`/handoff`** — one pass authors, dispatches the
> cross-vendor answer leg over the contained bridge, files the output, and locks (T2/T3
> in-pass; T1 via a headless close leg + one operator yes). `/handoff-close` survives as
> the close leg's protocol document. The three-phase PROTOCOL below is unchanged — only
> its execution collapsed. Where a project carries `handoffs/METHODOLOGY.md`, that file
> supersedes the three phase docs here.

Handoffs are the substrate-separation principle applied to inquiry routing. When a decision is open and the orchestrator's working hypothesis would benefit from a substrate-different challenge, `/handoff` runs the whole inquiry in one pass: it authors a directed inquiry, dispatches it over the contained bridge to a substrate-different verifier for the answer, files that answer, and locks the decision against it. T2/T3 lock in-pass; a T1 lock stays firewalled — a headless cross-vendor close leg deliberates, then the lock waits for one operator yes.

Three phases, one pass, one decision — the answering (and, for T1, closing) substrate is never the one that authored the hypothesis, which is the whole point.

## Lifecycle

A handoff moves through three phases and may run for multiple rounds. Each phase keeps its canonical spec in this directory; the single `/handoff` skill executes all three (the retired `/handoff-author` and `/handoff-receive` skills once walked phases 1 and 2 by hand; `/handoff-close` survives as the close leg's protocol document, not an operator surface):

1. **Authoring** — `HANDOFF-AUTHORING.md`. `/handoff` creates `core/handoffs/<YYYY-MM-DD>-<slug>/` with `meta.yaml` (status: `open`), `context.md`, `brief.md`, `README.md`, and a self-contained `packet-round-N.md` to hand to the verifier substrate. The hypothesis is named explicitly so the verifier can attack it. Round N+1 runs a convergence check (anti-asymptotic-confidence trap) before extending.
2. **Receiving** — `HANDOFF-RECEIVING.md`. `/handoff`'s cross-vendor answer leg reads the brief, takes a position, and writes `output-round-N.md`; `/handoff` files it and flips status to `answered`. (Mode B files output produced on an external substrate the operator ran by hand.)
3. **Closing** — `HANDOFF-CLOSING.md`. The close leg diffs the rounds (convergence check), runs deliberation with the operator, writes the ADR plus a `confidence-audit.md`, and flips status to `locked` — for T1 the deliberation runs headless on the cross-vendor substrate and the lock lands only on the operator's single yes. If a load-bearing upstream blocker surfaces, it takes the HALT path instead.

## Where decisions land

Decisions resulting from handoffs land at `docs/adr/<YYYY-MM-DD>-<n>-<slug>.md` with two metadata fields in the front matter:

- `informed_by:` — list of `handoff_id`(s) that informed this decision
- `decision_method:` — one of `handoff`, `preflight`, or `direct`

`handoff` indicates a decision routed through this protocol. `preflight` indicates a decision stress-tested via the `/preflight` core skill (renamed from the retired `/grill`). `direct` indicates a decision made without a structured intermediary.

## Where this fits

Handoffs are a core capability of the harness, not opt-in. `core/governance/CLAUDE.md` lists `core/handoffs/` among the always-loaded core directories. The methodology layer (`core/methodology/execution-engine.md`, `verification-architecture.md`) references handoffs as the canonical mechanism for substrate-separated inquiry on T1 and T2 decisions.

## Files in this directory

- `HANDOFF-AUTHORING.md` — canonical authoring protocol (round model, packet bundle, meta.yaml)
- `HANDOFF-RECEIVING.md` — canonical receiving protocol (two modes, substrate-separation check)
- `HANDOFF-CLOSING.md` — canonical closing protocol (convergence check, confidence audit, HALT path)
- `INDEX.md` — the Active/Archive table of handoffs (the authoring skill creates it on first handoff)
- `README.md` — this file

These three specs are the canonical protocol; the single `.claude/skills/handoff` skill is the operational orchestrator that walks all three in one pass (v3.0-78 — before the collapse, separate `handoff-author`/`handoff-receive`/`handoff-close` skills walked them). Docs and skills are kept in agreement — harness v1.1 reconciled the elaborate (multi-round) methodology into these docs.
