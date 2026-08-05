# sweep-briefing — MANIFEST-INDEX

Surface 1 of the v3.0-44 harness-surfaces dogfood
(the session-D design brief (dev-repo record, not shipped) Part 1) — the FIRST surface, and
the certification + twin-build-pilot target. Only this surface is required to reach CERTIFIED
in session D.

```yaml
surface: sweep-briefing
updated: 2026-07-23
layers:
  format:
    file: manifests/sweep-briefing/format-MANIFEST.md
    status: CERTIFIED
    rows: 18
    certified_by: receipts/sweep-briefing-conformance-bless-r2.md
smoke:
  layer: format
  rows: [sections-present, sections-order, all-clear-one-sentence, attention-numbered, watching-dashed-list]
gate:
  next_increment: "session D v3.0-44 -- sweep-briefing format certification + twin-build pilot"
  tier: T1
  touched_layers: [format]
  state: OPEN
  date: 2026-07-23
```

## Smoke set (named per `/conformance`'s naming requirement)

The five cheapest, most-structural VALIDATOR rows — presence/order/shape checks that run in
microseconds and catch the coarsest drift first, per `.claude/skills/conformance/SKILL.md`
("drift-likely rows: ... covering a surface's highest-traffic path"; here, the highest-traffic
path is simply "does the briefing still look like a briefing at all"):

- `sections-present`
- `sections-order`
- `all-clear-one-sentence`
- `attention-numbered`
- `watching-dashed-list`

`/sweep` step 6 replays this named subset against the LIVE current `SWEEP-BRIEFING.md` (absent
= skip-note) — never the whole 18-row set, and never a guessed subset. The remaining eight
VALIDATOR rows (`attention-three-sentences`, `attention-no-inline-path`,
`attention-details-tail-form`, `no-raw-output-leakage`, `unclassifiable-in-attention`,
`no-placeholder-tokens`, `no-script-names-in-prose`, `no-preamble-before-all-clear`) and all
five RUBRIC rows are full-tier only.

## Notes

- **Tier T1, twin-build-eligible.** This is the "first program surface whose manifest set
  reaches CERTIFIED at small scale" the manifest-format.md §11 STANDING OBLIGATION names —
  the twin-build pilot is owed here, not deferred.
- **RUBRIC rows never enter smoke or the mechanical coverage denominator.** The five RUBRIC
  rows (`plain-english-business-terms`, `all-clear-names-checks`, `attention-sentence-roles`,
  `watching-tone-distinguishes-planned`, `all-clear-defers-findings`) are graded by an
  independent-grader session at full-tier certification only, per-criterion, and reported as
  their own class — never folded into "N/18 rows replayed" arithmetic (manifest-format.md §4,
  §12; `/conformance` step 4).
- **CERTIFIED 2026-07-23** (receipt chain: bless-r1 -> receipts/sweep-briefing-conformance-bless-r2.md, the post-A1 18-row certification).
  `declared_rows: 18` reflects the format manifest's `A1` amendment (twin-build pilot round 1
  — two new rows: `no-preamble-before-all-clear` VALIDATOR, `all-clear-defers-findings`
  RUBRIC), 13 VALIDATOR + 5 RUBRIC; the amendment rides the certification per §8, the
  twin-build pilot receipt records the re-replay.
