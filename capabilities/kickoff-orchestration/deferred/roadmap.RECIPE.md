# Recipe: /roadmap (deferred)

## 1. WHAT IT IS

Refines the project arc into per-phase detail. Runs after /init (or after INIT.md manual walkthrough). Authors governance docs in full, fleshes out roadmap phase articles with assumptions and dependencies, identifies cross-phase risks.

## 2. WHEN A PROJECT NEEDS IT

The skeleton phase list from INIT.md needs expansion into substantive per-phase articles with assumptions and dependencies.

## 3. WHEN A PROJECT DOESN'T

Project has only 1–2 phases. Operator authors roadmap manually.

## 4. STATUS

deferred.

## 5. PROVENANCE

Designed in v1 build plan Phase 4 (v1 lines 1246–1362). Drafted but never validated.

## 6. DEPENDENCIES

- core/governance/
- knowledge-os (roadmap structure)
- stress-testing (would reuse /preflight's interview pattern)

## 7. AUTHORING GUIDE

The v1 design has these steps:
1. Read CLAUDE.md, project.yaml, skeleton governance, skeleton roadmap.
2. Interview operator per phase: assumptions (with types), dependencies, blockers.
3. Author each phase article in full. **Every phase article ships with its §5 assumption
   table at birth (v3.0-102(b))** — the exact `#/Assumption/Type/Status/Evidence/Wiki
   Source` shape from `wiki-schema.md` §5. When the interview yields no confirmed
   assumptions yet, mint at least one starter row with `Status: unvalidated` from the
   phase's own premise — /audit's object of study must exist from the article's first
   commit, or every future audit reports "nothing to grade" against a live cadence flag.
4. Cross-phase risk scan: surface dependencies and shared assumptions.
5. Update CONTEXT.md inline as terms sharpen.
6. Write receipt, append to changelog.

**Anti-patterns:**

- Don't author phase content without operator confirmation per phase.
- Don't run on an already-populated roadmap unless operator explicitly invokes a reconfigure mode with explicit before/after diff display.
- Don't conflate strategic (cross-phase) and tactical (within-phase) work. /roadmap is strategic. Tactical work happens via /preflight against a single phase article.

## 8. KNOWN LESSONS

None.

## 9. OPEN QUESTIONS

- Should /roadmap and /preflight (phase-kickoff mode) be the same skill? /preflight already does phase-kickoff per its design. /roadmap could be /preflight's "strategic pass" mode. Or they remain separate: /roadmap is strategic; /preflight is tactical.
- How does /roadmap handle a major mid-project pivot (a phase that no longer makes sense; a new phase needs to be inserted)? V1 design didn't address this clearly.

## 10. MIGRATION STEPS

(Empty — not yet built.)
