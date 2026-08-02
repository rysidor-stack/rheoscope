---
title: Fixture -- Stale Verified View
domain: fixtures
scope: build
---
# --- derivation (engine-managed; strip region) ---
schema_version: 3.2
view: topic
summary: CONTENT-3 fixture -- body mutated after verify stamp, must flag.
entities: [fixture]
status: active
tier: T1
consumed_status: verified-consumed
origin_max: human
subscribes:
  entities: [fixture]
bundle: [wiki/x.md]
verified:
  status: passed
  at: 2026-06-10T18:22:00Z
  verifier_vendor: anthropic
  verifier_model_id: claude-opus
  absorb_vendor: anthropic
  absorb_model_id: claude-sonnet
  packet_hash: deadbeef
  artifact: receipts/verify/fixture.json
# --- /derivation ---

This body has been edited AFTER the verify stamp above was written, without
resetting `verified:` to null. check-derivation.py --stale-only must flag
this file as a CONTENT-3 violation when compared against its HEAD commit
(the committed version of this fixture has different body text -- see the
companion fresh.md for the non-violating counterpart, and note this file is
only meaningful once committed and then edited in a working tree; the
embedded self-test cases in check-derivation.py exercise the same logic
hermetically without needing git history).
