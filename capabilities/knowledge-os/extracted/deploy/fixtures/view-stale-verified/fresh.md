---
title: Fixture -- Fresh Verified View
domain: fixtures
scope: build
---
# --- derivation (engine-managed; strip region) ---
schema_version: 3.2
view: topic
summary: CONTENT-3 fixture -- verified non-null, body unchanged since HEAD, clean.
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

This body has NOT changed since the verify stamp above was written. Diffed
against this file's own HEAD commit, the body outside the derivation region
is byte-identical, so check-derivation.py --stale-only must NOT flag this
file -- it is the non-violating counterpart to stale.md in this pair.
