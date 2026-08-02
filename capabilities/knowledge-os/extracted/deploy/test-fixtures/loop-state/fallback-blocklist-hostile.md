---
source: ryan
date: 2026-07-06
summary: Deliberately YAML-hostile line: an unquoted colon in prose, so whole-block parse fails.
informed_by:
  - handoffs/2026-07-06-fixture-blocklist-case/
  - 2026-07-06-second-value
---

# Fixture: degraded-fallback BLOCK-LIST extraction (the fixed bug)

Whole-block YAML parse of the frontmatter above fails (unquoted colon in
`summary:`), forcing raw_informed_by() onto its line-level degraded
fallback. `informed_by` uses the BLOCK-LIST form -- the exact shape the
snapshot's fallback regex mis-extracted (leading "- " kept). The fixed
fallback must return BOTH values CLEAN (no leading dash):
  ['handoffs/2026-07-06-fixture-blocklist-case/', '2026-07-06-second-value']
Asserted directly by run_self_test's fallback-fix cases.
