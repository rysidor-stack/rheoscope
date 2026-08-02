---
source: ryan
date: 2026-07-06
summary: Deliberately YAML-hostile line: an unquoted colon in prose, so whole-block parse fails.
informed_by: 2026-07-06-fixture-inline-case
---

# Fixture: degraded-fallback INLINE regression case

Whole-block YAML parse of the frontmatter above fails, forcing the
line-level degraded fallback. `informed_by` uses the INLINE form
(`informed_by: value` on one line) -- the form the snapshot's fallback
already handled correctly. The fixed fallback must STILL return
  ['2026-07-06-fixture-inline-case']
(regression guard for the [ \t]* fix). Asserted directly by
run_self_test's fallback-fix cases.
