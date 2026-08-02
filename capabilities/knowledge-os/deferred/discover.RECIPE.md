# Recipe: /discover (SUPERSEDED — now ships extracted 2026-07-11)

## Superseded by extracted/discover/

This recipe designed `/discover` as a deferred capability (article-count-triggered connection
finder). It was subsequently **built and proven** on the dogfood fork — first as
`/rhymes` (live-operations SESSION 4, 2026-07-10, the operator's half-remembered Karpathy
capability), then rebuilt through further operator-directed reasoning passes into `/discover`,
the engine-fit maximal form (ledger SESSIONS 5–6) — and now ships **extracted**, not deferred, as
part of the knowledge-os engine backport (2026-07-11).

- Live skill doc: `capabilities/knowledge-os/extracted/discover/SKILL.md`
- Landing record: fork `engine-verification-ledger.md` § LIVE OPERATIONS, SESSION 4 (`/rhymes`
  built) through SESSIONS 5–6 (rebuilt to `/discover`)

This file is retained as a supersession pointer, per the `stress-testing` RECIPE precedent
(`capabilities/stress-testing/RECIPE.md`).

## Lineage (preserved for provenance — original v1 design)

Designed during v1 build plan authoring (v1 Phase 5 `/discover` section, lines 1584–1675 of the
v1 plan): a cross-article connection finder scoring candidate pairs by strength (1–3) × confidence
(high/medium/low), filtering at strength ≥ 2 AND confidence ≥ medium, with a circuit breaker at 10
new connection articles per run. **The built `/discover` does not implement this scoring
algorithm** — it is a read-only, proof-carrying, five-mode inference layer (relate/derive/gap/
trace/introspect) filing draft intake events, a materially different design. This section is kept
for historical provenance only; do not treat it as the current capability's authoring guide.
