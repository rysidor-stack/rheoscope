# /audit Examples

One worked example showing assumption grading and REVIEW.md queue authoring.

---

## Example — Mid-phase audit surfacing a contradiction and a phase-level rollup

A project's roadmap has three active phase articles. `/audit` runs against all three. The most consequential finding is in phase-1.

**Input — `roadmap/phase-1-data-import.md` assumption table excerpt:**

| # | Assumption | Type | Status | Evidence | Wiki Source |
|---|------------|------|--------|----------|-------------|
| 3 | The upstream API rate-limits to 60 req/min | threshold | confirmed | "Documented in vendor portal Q1 2026" | `wiki/integrations/upstream-api.md` |
| 4 | Bulk import completes under 30 minutes for 10k rows | continuous | mostly confirmed | "Measured 22 min on 8k-row test in dev" | `wiki/integrations/upstream-api.md` |

**/audit Step 4 — grading:**

Assumption 3 (threshold): Reads `wiki/integrations/upstream-api.md`. The current article body now contains a section "Rate-limit observations" dated 2026-04-22 stating "Production runs hit 30 req/min hard cap, despite vendor documentation claiming 60." The wiki contradicts the assumption.

Grading verdict: status should change from `confirmed` to `refuted`. Evidence column updated to quote the production observation. Severity: blocking (phase is active).

Assumption 4 (continuous): Reads the same wiki article. The "Bulk import" section now reports a production run of 13k rows taking 47 minutes — exceeds the 30-minute threshold. The "mostly confirmed" status was based on dev-environment data; production reality is different.

Grading verdict: status changes from `mostly confirmed` to `at-risk`. Evidence column updated to reflect the production timing. Severity: blocking (phase is active).

**/audit Step 5 — contradictions detected:** 2 contradictions in phase-1 alone.

**/audit Step 7 — cross-phase rollup:**

Phase-1 now has at least one `refuted` assumption (#3). Per the Step 7 rule, /audit flags the phase as `blocked` and logs a phase-level REVIEW entry. /audit does NOT change the phase's top-level `status:` frontmatter — that's the operator's call. The REVIEW entry surfaces the evidence and recommends the operator move phase-1 from `active` to `blocked` pending re-design around the production rate limit.

**/audit Step 8 — REVIEW.md entries written:**

Three entries appended to `wiki/REVIEW.md`:

```yaml
- type: audit
  severity: blocking
  created: 2026-05-22
  stale_after: 2026-05-25
  source: /audit run 2026-05-22
  details: |
    roadmap/phase-1-data-import.md assumption 3 marked `confirmed` but
    wiki/integrations/upstream-api.md "Rate-limit observations" section
    (2026-04-22) states "Production runs hit 30 req/min hard cap, despite
    vendor documentation claiming 60." Assumption status updated to
    `refuted`. Evidence column updated. Operator decides phase impact.

- type: audit
  severity: blocking
  created: 2026-05-22
  stale_after: 2026-05-25
  source: /audit run 2026-05-22
  details: |
    roadmap/phase-1-data-import.md assumption 4 marked `mostly confirmed`
    but production data in wiki/integrations/upstream-api.md "Bulk import"
    section shows 13k rows took 47 minutes — exceeds 30-minute threshold.
    Status updated to `at-risk`. Evidence column updated to production
    timing.

- type: audit
  severity: blocking
  created: 2026-05-22
  stale_after: 2026-05-25
  source: /audit run 2026-05-22
  details: |
    Phase-1 has 1 refuted assumption (#3 — rate limit) and 1 at-risk
    assumption (#4 — bulk import timing). Recommend operator move phase-1
    from `active` to `blocked` and re-design around the production rate
    limit before re-opening. /audit did not modify the phase's status:
    frontmatter — that decision is the operator's.
```

**Receipt fragment:**

```yaml
contradictions_found: 2
orphan_wiki_sources_found: 0
review_entries_created: 3
phases_flagged: [roadmap/phase-1-data-import.md]
circuit_breaker_hit: false
notes: |
  Phase-1 is on shaky ground. Both consequential assumptions about the
  upstream API contradict production evidence. The dev-environment numbers
  that earlier graded "mostly confirmed" don't survive contact with prod.
  Recommend reading the REVIEW entries top-to-bottom in this order;
  assumption #3 is the dominant constraint.
```

The operator reads the REVIEW.md queue, decides whether to move phase-1 to `blocked`, and authorizes the re-design — /audit's job is done.
