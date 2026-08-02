---
source: ryan
date: 2026-07-01
tags: [fixture]
summary: Deliberately YAML-hostile line: Stripe pricing is $0.50/transaction, not free.
domain: fixture
informed_by: 2026-07-01-fdec
---

# Fixture: YAML-hostile frontmatter, degraded-fallback proof (inline form)

This file's frontmatter block deliberately fails whole-block PyYAML parse
(the `summary:` value contains an unquoted colon inside prose -- the same
real-world archetype found on this fork's own raw/ corpus during the port;
see check-loop-state.py's module header "FORK REALITY DISCOVERED DURING THE
PORT" and the build report's named finding on the degraded-fallback's
INLINE-vs-BLOCK-LIST asymmetry). `informed_by` here uses the INLINE form
(`informed_by: value` on one line), which is the form raw_informed_by()'s
degraded fallback extracts CORRECTLY even under whole-block parse failure
(confirmed against this fork's two real block-list-form-under-parse-failure
cases, which the fallback mis-extracts with a leading "- " -- a genuine
pre-existing defect in the ported function, named as a finding, not
silently patched here since the build charge is to preserve
raw_informed_by() verbatim).

This fixture is named "invalid-*" for directory-naming symmetry with the
other violation-class trees, but its EXPECTED OUTCOME IS EXIT 0 (clean) --
it proves the degraded-parse fallback's INLINE-form path, not a violation.
Registered lock-class in _registrations.json below.
