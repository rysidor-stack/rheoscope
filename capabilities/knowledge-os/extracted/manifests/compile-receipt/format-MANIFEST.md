---
manifest: format
surface: compile-receipt
version: "1.0"
status: EXTRACTED
source_artifacts:
  - path: docs/wiki-schema.md
    sha256: 5c27d6ce1ea66f92eda13ce792bb6305b1ccbbdd617af794ae990ac0d7777f38
  - path: deploy/check-frontmatter.py
    sha256: 56367ffa90d0cb6b9384f30f891db0f4dac7f7aa76000e50b7588d4f60e8886b
extracted: 2026-07-23
toolchain: source-read
confidence: source-crosschecked
row_shape: table
declared_rows: 11
schema_extensions: []
---

# compile-receipt format manifest

Format-layer behavioral contract for a `/compile` run's receipt
(`receipts/<YYYY-MM-DDTHHMMSS>-compile.md`) — surface 3 of the v3.0-44 harness-surfaces
dogfood (session-D design brief Part 1 -- a dev-repo design record, not shipped). Per that
brief this surface is "partially machine-checked already": the rows below mostly
transcribe an existing de-facto contract rather than inventing one. Every row carries
`DRAFT` (the row-lifecycle carrier, manifest-format.md §5); this manifest sits at
EXTRACTED with no validator and no pinned fixture seed owed yet.

**No single script emits this file.** Unlike `sweep-briefing`/`decision-inbox`
(each has one `deploy/*.py` producer), a compile receipt is authored by the session
running the `/compile` skill, per the schema `docs/wiki-schema.md` §7 states in prose
(`.claude/skills/compile/SKILL.md` cites it directly). `deploy/check-frontmatter.py`'s
`RECEIPT_KEYS` envelope is the one piece of this contract that already runs as code —
it validates the shared `type`/`timestamp` envelope plus a `lenient` allow-list of
per-type optional keys, but never the *internal shape* of a list-valued key (e.g.
whether an `articles_modified` entry has a `path`/`operation` pair) — that finer
contract lives only in prose today, which is exactly the gap this manifest starts
closing. `source_artifacts` pins the schema document and the checker; a concrete
instance, `receipts/2026-07-23T163000-compile.md`, was read for cross-reference while
authoring the rows below but is not pinned (an instance is a moving corpus member, not
the stable contract — and, per the ambiguity notes in MANIFEST-INDEX.md, that
particular instance already diverges from the canonical shape on two fields).

## Rows

| id | name | replay path | expected observable | variant | flags | evidence | kind |
|---|---|---|---|---|---|---|---|
| `cr-filename-pattern` | Filename encodes the same timestamp as the frontmatter | Match the receipt's basename against `receipts/<YYYY-MM-DDTHHMMSS>-compile.md` (docs/wiki-schema.md §7 "Naming") | Filename timestamp (colon-free) and the frontmatter `timestamp:` value name the same instant to the second | — | DRAFT | source-read | EXACT |
| `cr-envelope-required-keys` | `type`/`timestamp` envelope is present per the shared-envelope rule | Read the frontmatter's top-level keys (`deploy/check-frontmatter.py` `RECEIPT_KEYS["required"]`) | `type` is present with the literal value `compile`; `timestamp` is present as ISO `YYYY-MM-DDTHH:MM:SS` | — | DRAFT | source-read | EXACT |
| `cr-raw-inputs-shape` | `raw_inputs`, when present, is a flat list of repo-relative paths | Parse the `raw_inputs:` block | A YAML list of scalar strings, each a repo-relative path (conventionally under `raw/`) — never a list of mappings | — | DRAFT | source-read | EXACT |
| `cr-articles-modified-shape` | `articles_modified` entries are `{path, operation}` mappings with a closed operation vocabulary | Parse each `articles_modified:` list entry (docs/wiki-schema.md §7 code block) | Each entry is a mapping with keys `path` and `operation`; `operation` is one of `created`, `updated`, `removed`; `removed` appears ONLY when `notes` also quotes an operator authorization (§7 "Authorized directory removal" — that is the sole path by which a receipt may report a removal) | — | DRAFT | source-read | EXACT |
| `cr-scope-tags-shape` | `scope_tags`, when present, is a list of `{path, scope, changed}` mappings | Parse each `scope_tags:` list entry | `OPEN — missing: reconciliation between the canonical shape (docs/wiki-schema.md §7: a list of `{path, scope, changed}` mappings, `scope` in `{build, domain, mixed}`) and the flat tag-string-list form observed in receipts/2026-07-23T163000-compile.md (`scope_tags: [scheduler, deploy, auth, ...]`) — both forms coexist in the live corpus and `check-frontmatter.py` does not discriminate between them (lenient class, no sub-shape check)` | — | DRAFT | source-read | EXACT |
| `cr-cross-links-changed-shape` | `cross_links_changed`, when present, is a list of `{from, to, operation}` mappings | Parse each `cross_links_changed:` list entry | `OPEN — missing: same coexistence gap as cr-scope-tags-shape — docs/wiki-schema.md §7 fixes `{from, to, operation}` with `operation` in `{added, removed}`, but receipts/2026-07-23T163000-compile.md instead carries free-text strings like `"wiki/systems/auth-stack.md + wiki/systems/scheduler.md (added)"`` | — | DRAFT | source-read | EXACT |
| `cr-boolean-fields` | `meaningful_change`/`circuit_breaker_hit` are bare YAML booleans | Parse the two keys' raw scalar values | When present, each value is the bare token `true` or `false` — never a quoted string, never `yes`/`no`/`1`/`0` | — | DRAFT | source-read | EXACT |
| `cr-review-compacted-shape` | `review_compacted`, when present, is a `{count, entries}` mapping | Parse the `review_compacted:` block | `count` is a non-negative integer; `entries` is a list of strings (terminal REVIEW.md entry titles, e.g. `"<title> (APPLIED)"`); the field is absent or `{count: 0, entries: []}`-equivalent exactly when nothing was swept this run (docs/wiki-schema.md §7) | — | DRAFT | source-read | EXACT |
| `cr-pending-cascade-machine-read` | `pending_cascade`, when non-empty, is read as authoritative re-entry work by the next `/compile` run | Parse `pending_cascade:` entries as `{raw_file, targets_remaining}` mappings, then check the NEXT compile receipt's `raw_inputs`/`notes` for evidence the file re-entered the pipeline | Each `raw_file` re-enters processing on the next `/compile` run even though it may already appear in some article's `sources:` list — this is authoritative carry-over, not a heuristic hint (`.claude/skills/compile/SKILL.md` Step 1 "Re-entry from the last receipt"; docs/wiki-schema.md §7 -- section names, not line numbers: the old "line 28" had already drifted) | — | DRAFT | source-read | EXACT |
| `cr-notes-unresolved-term-marker` | `notes` carries the literal `[unresolved-term: <term>]` marker for terms outside CONTEXT.md | Scan `notes:` for the marker token | When compilation encountered a load-bearing term not in CONTEXT.md, `notes` contains `[unresolved-term: <term>]` verbatim — a future `/grill` (`/preflight`) session resolves it into CONTEXT.md (docs/wiki-schema.md §7; `.claude/skills/compile/SKILL.md` Step 8 -- section names, not line numbers) | — | DRAFT | source-read | EXACT |
| `cr-envelope-lenient-unknown-keys` | A key outside the RECEIPT_KEYS envelope is tolerated, not flagged | Add a frontmatter key not in the union of `RECEIPT_KEYS["required"]` and `RECEIPT_KEYS["optional"]` and run `deploy/check-frontmatter.py` | The receipt class is `lenient: True` (`deploy/check-frontmatter.py` `RECEIPT_KEYS`) — an unrecognized top-level key produces no WARN/FAIL from that sensor, since per-type receipt schemas vary and only the shared envelope (`type`, `timestamp`) is common across every orchestrator (docs/wiki-schema.md §7 "Shared envelope") | — | DRAFT | source-read | EXACT |

## Amendments

- 2026-08-09 — the `docs/wiki-schema.md` source pin re-hashed to the current
  `capabilities/knowledge-os/extracted/wiki-schema.md.template` bytes (backlog v3.0-94:
  the pin's meaning is now DEFINED as the sha256 of the harness-shipped template source,
  resolved dev-layout by `check-manifest.py`'s sha256-pins check — the recorded
  `d3d76d21…` value had gone stale unverifiably across the v3.0.29+ wiki-schema edits
  because the sensor could only SKIP an instance-form path in the dev tree). No row
  content changed.
