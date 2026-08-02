# /flight-plan Examples

One worked example showing a mid-phase briefing (knowledge-os enabled). For a build-only project (knowledge-os disabled), the same briefing omits the **Wiki Health** and **Coverage Gaps** sections per Step 0.6.

---

## Example — Mid-phase briefing with deltas, staleness, and overdue /audit

Session opens at 2026-05-22. The last /flight-plan ran 2026-05-19. Since then:
- One /compile run processed 2 raw files, created 1 article, updated 3.
- No /audit run for 18 days (overdue per the 1-2 week cadence).
- REVIEW.md has 1 blocking, 4 standard, 2 background items.
- Two articles in `wiki/integrations/` haven't been updated in 10+ days.
- `wiki/flight-plans/` contains one active flight plan: `2026-05-15-payment-integration.md`.

**`/flight-plan` walks Steps 1-6.** Output briefing:

```markdown
# Flight-Plan Briefing — 2026-05-22

(knowledge-os present — wiki health assessed)

## What Changed (since last briefing)
- /compile run 2026-05-21 processed 2 raw files (1 article created: `wiki/integrations/payment-gateway.md`; 3 updated). Confidence on `wiki/integrations/api-overview.md` upgraded from medium to high.
- 1 new REVIEW entry created (blocking) — see Attention Needed.

## Wiki Health
- **Articles:** 47 across integrations (12), workflows (8), systems (15), operations (7), governance (5)
- **Unprocessed raw:** 0
- **Staleness alerts:** `wiki/integrations/auth-flow.md` (10 days), `wiki/integrations/error-handling.md` (12 days)

## Active Flight Plans
| Flight plan | Phase | Status | Blocker | Next Action |
|-------------|-------|--------|---------|-------------|
| Payment Integration | Phase 2 | Vendor sandbox available; integration tests passing on happy path | none | Author webhook retry test cases |

## Attention Needed
- **[blocking]** REVIEW.md entry from /compile run 2026-05-21: assumption #3 in `roadmap/phase-2-payments.md` (vendor rate limit) contradicted by production observation. See REVIEW.md for full details.
- **/audit overdue:** Last audit ran 2026-05-04 (18 days ago). Cadence is 1-2 weeks.
- 2 articles stale 10+ days (see Wiki Health).

## Coverage Gaps (top 3)
- `wiki/governance/` has no entry for the new vendor contract terms (signed 2026-05-10, no raw file yet).
- `wiki/operations/` lacks a deploy-day runbook (3 deploys have happened without one).
- `wiki/integrations/` is missing an error-taxonomy article (every vendor handles errors differently).

## Recommendation
- Run /audit immediately — the blocking REVIEW entry is the kind of finding /audit would have caught a week ago. The 18-day gap is the biggest hygiene risk this week.
- Then read the blocking REVIEW entry and decide phase-2 status with the operator.
- Two stale articles can wait for the next compile-driven update.
```

**Receipt fragment (Step 7):**

```yaml
---
type: flight-plan
timestamp: 2026-05-22T09:14:33
duration_minutes: 3
articles_at_briefing: 47
unprocessed_raw: 0
review_items:
  blocking: 1
  standard: 4
  background: 2
overdue_orchestrators: [/audit]
staleness_alerts: 2
active_flight_plans: 1
recommendation: "Run /audit immediately; the blocking REVIEW entry is /audit-shaped and 18-day gap is the biggest hygiene risk."
---
```

**Changelog entry (Step 8):**

```
[2026-05-22] /flight-plan — 47 articles, 0 unprocessed raw. Run /audit; 18-day gap and blocking REVIEW entry.
```

The briefing lets a cold session immediately see (a) what changed since last time, (b) what needs attention, (c) what to do next. Total read time: under 60 seconds.
