# Capability: stress-testing (RETIRED 2026-07-10)

## Superseded by /preflight (core skill)

This capability's sole payload — `/grill`, the stress-testing orchestrator — was renamed and rebuilt as **`/preflight`** on 2026-07-09. `/preflight` now ships **unconditionally** as a core skill at `core/skills/preflight/` (SKILL.md, CONTEXT-FORMAT.md, ADR-FORMAT.md, LICENSE-mattpocock.txt), wired to `.claude/skills/preflight/` regardless of any capability toggle.

- Live skill doc: `core/skills/preflight/SKILL.md`
- Retirement/landing record: `HARNESS-CHANGELOG.md` § v3.0, Theme C — "`/preflight` core skill (renamed + rebuilt from `/grill`, 2026-07-09)"

The `capabilities.stress-testing` key in `project.yaml` remains accepted for backward compatibility and now wires nothing.

## Lineage (preserved for provenance)

`/grill` was forked from `mattpocock/skills/skills/engineering/grill-with-docs` at commit `b8be62ffacb0118fa3eaa29a0923c87c8c11985c`, MIT License (Copyright (c) 2026 Matt Pocock). Attribution and the license file traveled with the rename and continue to ship with `/preflight` at `core/skills/preflight/LICENSE-mattpocock.txt`.

This directory's former `extracted/grill/` payload (the pre-rename fork) has been deleted; it is fully superseded by `core/skills/preflight/`.
