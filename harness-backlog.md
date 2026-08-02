# Harness Backlog

*Canonical log of issues, gaps, and improvement candidates that are properties of the **harness template** (not this project's content), surfaced during real use. Each entry is a candidate contribution back to the harness upstream. Append entries as you hit them — the `/log-backlog` skill formats and numbers them for you. (In the template repo itself the skill is not installed; apply `core/skills/log-backlog/SKILL.md.template` manually — see `CONTEXT.md` § /preflight-on-self.)*

*What belongs here vs. project content: see `core/governance/CLAUDE.md` § Session discipline → Backlog logging.*

## Entry format

```markdown
### v<version>-N — Short title
- **Surfaced:** date and context (which phase / step)
- **Where:** file paths in the harness affected
- **Severity:** blocking / standard / nice-to-have
- **Symptom:** what the operator hits
- **Patched (project-local):** workaround applied, if any (omit the field if none)
- **Proposed fix:** how to address upstream
- **References:** commits / docs / session logs that discuss this
```

## Versioning and numbering

- **The version prefix is read at runtime, never hardcoded.** In an instantiated project, `<version>` is `template_version` from `project.yaml` — the harness version the project actually runs (e.g. `v2.0-3` on a v2.0 harness). It changes only when the project migrates to a newer harness. In the harness template repo itself, `<version>` is the root `VERSION` file — the in-development release that will absorb the entries.
- **Numbering is sequential per version prefix** (`v<version>-N`, unpadded, restarting at 1 for each version). The prefix records which harness version an issue was found on; entries become candidate fixes for the harness's next release.
- **The file name is deliberately version-free** so it can never go stale; the version lives only in entry numbers. (Renamed from `v1.1-backlog.md` in v2.0 — see `adr/` in the harness template repo.)

## Severity meanings

- **blocking** — operator cannot proceed without a workaround
- **standard** — operator can proceed but has to invent a workaround or accept noise
- **nice-to-have** — quality-of-life or polish; no real blockage

## Process notes

- **Logging discipline.** Log a harness-template issue in the session it surfaces, via `/log-backlog`. Don't let issues scatter across commit messages and adaptation notes.
- **Stable numbering.** Once a `v<version>-N` number is assigned, it's permanent. Supersede with a new entry carrying `Supersedes: v<version>-N` rather than renumbering or rewriting — including across versions (a v2.1 entry may supersede a v2.0 one).
- **Append-only.** `/log-backlog` never rewrites existing entries; one entry per invocation.
- **Contribution back.** When the harness opens its next development cycle, this file is reviewed entry-by-entry — items either land, get reclassified, or get merged with related items.
- **Cross-project signal.** If a future project hits the same issue independently, note it in the entry's References. Repeated discovery is strong prioritization signal.
- **In the template repo:** this file doubles as harness-dev's own live backlog. At each release it is triaged entry-by-entry (land / defer to changelog / supersede) and emptied, so it ships empty to instantiated projects.

---

*(v2.0 triage, 2026-06-10, at ship: v2.0-1 closed — documented exclusion, recorded in the sensor docstring and the changelog #10 entry; v2.0-2 closed — fixed by the release-hardening commit, G2-verified; v2.0-3 closed — all four amendments landed via #9 and #7; v2.0-4/5/6/7 carried forward as v2.1-1…4 with `Supersedes:` links. Full entry texts remain in git history at the pre-ship commit.)*

*(v2.1 triage, 2026-06-12, at ship — tag `v2.1`: **closed** — v2.1-2 (capability-doc authoring rule, landed in `capabilities/INDEX.md` via #2), v2.1-3 (#4 execution-engine doc-polish trio, landed via #1), v2.1-4 (hardening-G2 nits + driver version pins, resolved via #2 and #14), v2.1-5 (minimal gate, landed as the operate-sentinel phase-1 recipe via #2). **Carried forward** to the next cycle (to be re-filed with `Supersedes:` links at cycle open): v2.1-1 (CLAUDE.md firewall-heading rename), v2.1-6 (residual universal PAN/CVV assertion in verification-architecture — agnosticism residual, standard), v2.1-7 (trio precondition back-link reciprocity), v2.1-8 (instantiating text cites harness-dev ADRs/item numbers — standard; the trio "backlog v2.1-5" pointers were resolved via #2, the ADR/item-number grounding remains), v2.1-9 + v2.1-10 (pilot-kit dev-tooling pointers, actionable only if the pilot-kit pattern is reused). Full entry texts remain in git history at the pre-ship commit.)*

---
