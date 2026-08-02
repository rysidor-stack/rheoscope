# compile-receipt — MANIFEST-INDEX

Surface 3 of the v3.0-44 harness-surfaces dogfood (session-d-design-brief-2026-07-23.md
Part 1) — "partially machine-checked already", per the brief, since
`deploy/check-frontmatter.py` already enforces the shared receipt envelope. Per the
round-1 vocabulary fold: this surface's **file** status is EXTRACTED while every
**row** carries the DRAFT flag — the enum and the lifecycle carrier are distinct and
never conflated (manifest-format.md §5 vs §7).

```yaml
surface: compile-receipt
updated: 2026-07-23
layers:
  format:
    file: manifests/compile-receipt/format-MANIFEST.md
    status: EXTRACTED
    rows: 11
    certified_by: null
gate:
  next_increment: "session D v3.0-44 -- compile-receipt format certification (surface 3, after the sweep-briefing pilot and the decision-inbox surface)"
  tier: T3
  touched_layers: [format]
  state: CLOSED
  date: 2026-07-23
```

## Notes

- **No validator owed at this stage.** Only `sweep-briefing` (surface 1) is required
  to reach CERTIFIED in session D; this surface's validator and pinned
  defective-fixture seed are future work. Nothing here self-certifies.
- **There is no single writer script for this surface**, unlike `sweep-briefing`/
  `decision-inbox`. A compile receipt is authored by the session running the
  `/compile` skill, against the prose contract in `docs/wiki-schema.md` §7 (which
  `.claude/skills/compile/SKILL.md` cites directly, e.g. "Keep the receipt's
  frontmatter YAML-parseable"). `source_artifacts` therefore pins the schema document
  AND `deploy/check-frontmatter.py` (the one piece of this contract that already runs
  as code, validating the shared `type`/`timestamp` envelope + a lenient per-type
  key allow-list — but never a key's internal shape). `receipts/2026-07-23T163000-
  compile.md` was read as a concrete instance while authoring these rows but is
  deliberately not pinned in `source_artifacts` — an instance is a corpus member, not
  the stable contract.
- **Two genuine contract-drift findings surfaced while extracting this manifest**,
  carried as OPEN rows rather than silently resolved one way or the other:
  - `cr-scope-tags-shape` — docs/wiki-schema.md §7 fixes `scope_tags` as a list of
    `{path, scope, changed}` mappings, but the live `receipts/2026-07-23T163000-
    compile.md` instance instead carries a flat list of bare tag strings
    (`scope_tags: [scheduler, deploy, auth, ordering, receiving, count, work-orders,
    gateway, migrations, email-assistant]`). Both forms are present across the
    `receipts/` corpus (spot-checked: block-mapping form in 61 files, flat-list form
    in 15) and `check-frontmatter.py`'s `lenient: True` receipt class does not
    discriminate between them.
  - `cr-cross-links-changed-shape` — same coexistence gap: §7 fixes `{from, to,
    operation}` mappings, but the same 2026-07-23 instance instead carries free-text
    strings like `"wiki/systems/auth-stack.md + wiki/systems/scheduler.md (added)"`.
  - Neither drift is a defect this manifest fixes — that decision (canonicalize on
    the schema, canonicalize on the observed form, or admit both as a declared
    `schema_extensions` variant) belongs to whoever certifies this surface, with the
    corpus-frequency evidence above informing the call.
- **`pending_cascade` is the one field this manifest asserts is machine-consumed, not
  merely descriptive** (`cr-pending-cascade-machine-read`) — the next `/compile`
  session's Step 1 reads it as authoritative re-entry work
  (`.claude/skills/compile/SKILL.md` line 28). A future validator for this row would
  need two receipts in sequence to check, not one — worth flagging for whoever designs
  the certification fixture seed.
