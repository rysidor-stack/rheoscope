---
source: ryan
date: 2026-07-01
tags: [fixture]
summary: Fixture lock raw registered non-lock-class in the manifest below.
domain: fixture
informed_by:
  - 2026-07-01-fdec
---

# Fixture Decision Lock (registered non-lock-class)

Fixture content only. This raw exists on disk and correctly names the
handoff_id in its informed_by frontmatter, but its _registrations.json
manifest entry deliberately registers it with event_class "compile" (not
in {informed_by, lock}). Trips BOTH extension check (a) (this raw carries
informed_by but is not registered informed_by/lock) AND extension check (b)
(the locked handoff's locked_by_raw_file resolves here, same non-lock-class
fact) -- both firing from the one underlying fixture condition is expected,
not a bug in the fixture.
