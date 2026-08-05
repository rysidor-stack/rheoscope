<!-- Extracted and generalized from a production project, with personnel
     references genericized (operator references at Hold Point Protocol checks).
     Otherwise project-neutral; no template-variable substitutions needed. -->

# Project Flight Plan — Template v6.0
## One File. One Truth. The Cockpit.

**Purpose:** Single source of truth for any project. Organized for the AI that reads it cold every session, not for the human who already lives the project. Duplicate this file, rename it `[project-name]-flight-plan.md`, and start working.

**Structure:** Four layers, ordered by how often they change.

**Methodology docs (loaded by role, not stored per-project):**
- `execution-engine.md` — How builds run. Load when executing increments.
- `verification-architecture.md` — How correctness is proven. Load when writing verification specs or planning verification.
- `code-conventions.md` — Project-specific code patterns. Created during first build, grows organically. Loaded by code quality reviewers.

---

## LAYER 1 — DASHBOARD
<!-- AI: Read this first. Rewrite this entire section every session before closing. -->
<!-- Human: If the AI reads nothing else, this must be enough to act. -->

**Project:** [name]
**Phase:** [0-Intent | 1-Discovery | 2-Validation | 3-Scaffold | 4-Build | 5-Harden | 6-Operate]
**Status:** [one line — what's true right now]
**Blocker:** [active blocker or "None"]
**Next action:** [the single next thing to do]
**Spec:** [inline below | `[project]-spec.md` (last updated YYYY-MM-DD)]
**Last session:** YYYY-MM-DD — [session type: Interactive/CC/CC-Controller] — [one line summary]
**Regression health:** [PASS (date) | FAIL (date — details in Layer 2) | NOT YET RUN]

---

## LAYER 2 — ACTIVE WORK
<!-- Updated when things change. This is the working surface. -->

### Current Build Increment

| # | Increment | Behavior When Working | Tier | Verification Status |
|---|-----------|----------------------|------|-------------------|
| → | *[active]* | *[what "working" looks like]* | T[1-4] | Spec: [written/not] · Tests: [written/not] · Last run: [date] · [PASS/FAIL/—] |
| | *[next]* | | T[1-4] | — |

**Tier key:**
- **T1** — Money, inventory, anything where silent errors cost dollars. Full firewall. Separate Builder/Verifier/Runner.
- **T2** — Cascading workflows where one action triggers downstream actions. Full firewall for end-to-end cascade. Builder may self-test individual steps.
- **T3** — Data integrity, customer records, stateful operations. Spec review + code quality review. Builder writes own tests.
- **T4** — UI, notifications, display logic, low-risk features. Builder self-test + regression gate only.

**Tier assignment rule:** When in doubt, go higher. The cost of over-verification is one extra CC session. The cost of under-verification is a production bug. If you have to think about whether it's T2 or T3, it's T2.

### Open Questions & Known Risks

<!-- Items enter when discovered. Items leave when resolved. No ceremony. -->

| # | Item | Type | Status | Notes |
|---|------|------|--------|-------|
| 1 | *[what's unresolved]* | Q / R / A | OPEN | |

Type key: **Q** = question (need answer), **R** = risk (could go wrong), **A** = assumption (unverified belief)

### Recent Decisions

<!-- Decisions that affect future work. Pruned at hold points. -->

| Date | Decision | Rationale |
|------|----------|-----------|
| | | |

---

## LAYER 3 — REFERENCE
<!-- Write-once or write-rarely. Scroll past unless you need it. -->

### Intent (Phase 0)

**Problem:** [one sentence — who has what problem]
**Success:** [how you know it's working — outcome, not features]
**Not:** [what this project is NOT]

### Spec

<!-- Inline here if under ~100 lines. Extract to satellite file if longer. -->
<!-- If extracted: delete this section body and update the Spec pointer in Layer 1. -->

[spec content goes here]

### Design Direction

<!-- Skip for utility projects. Fill for anything user-facing. -->

- **Palette:** [colors or "N/A"]
- **Typography:** [direction or "N/A"]
- **Mood:** [one sentence or "N/A"]
- **References:** [links/screenshots or "N/A"]

### Methodology

<!-- Points to the methodology files. Tells the session what to load based on role. -->

| Role | Load These | When |
|------|-----------|------|
| Interactive | This flight plan only | Always |
| Interactive + Planning | This flight plan + `verification-architecture.md` | When writing verification specs |
| CC Controller | This flight plan + `execution-engine.md` | When executing build increments |
| CC Verifier | `verification-architecture.md` + `specs/verification/[increment].md` | When authoring verification tests |
| CC Builder | `execution-engine.md` (principles section) + `specs/build/[increment].md` | When dispatched by Controller |
| CC Runner | Test suite paths + deployed build | When dispatched by Controller |
| Code Quality Reviewer | `code-conventions.md` (created during the first build) + diff under review | When dispatched by Controller |

### Dependencies

| Service | Purpose | Limits | Fallback | Verified? |
|---------|---------|--------|----------|-----------|
| | | | | |

### Code Conventions

<!-- Created during first build session. Grows as decisions are made. -->
<!-- Can be inline here if short, or extracted to code-conventions.md if it grows past ~30 lines. -->

[conventions content — or path to extracted file]

### Completed Phases

<!-- Compressed summaries. One line per phase. Added as phases complete. -->

| Phase | Completed | Summary |
|-------|-----------|---------|
| | | |

---

## LAYER 4 — SESSION LOG
<!-- Append-only. Each session adds one entry. Compact at hold points. -->
<!-- This is the black box. Even when everything else gets stale, this stays current. -->

### Log

```
[YYYY-MM-DD] [Interactive|CC|CC-Controller] ———
Did: [what was accomplished]
Decided: [any decisions made, or "—"]
Verified: [what was tested, results, or "—"]
Flagged: [anything surfaced for future attention, or "—"]
Next: [what the next session should do]
```

---

## HOLD POINT PROTOCOL
<!-- Not a phase. Fires at every phase transition. -->
<!-- The AI runs this. The human confirms go/no-go. -->

When reaching a phase transition, run these four checks in order. Zero items on any check is valid — don't manufacture concerns.

**A — Depth Check**
Surface specific items that are undercooked. Point to the item by name, state why it's risky, and recommend either a quick re-look (3-pass) or a full teardown-and-rebuild of the item (5-pass) — the operator says which they want, or "neither".

**B — Connection Surfacing**
Surface connections between items that weren't explicitly designed for. Each connection must reference two or more specific items. Totality pass reasons about the project as a whole.

**C — Verification Audit**
Review verification status for every increment in the phase:
- Any T1–T2 increment without a passing verification run is a **blocker**. Phase does not advance.
- Any T3 increment without at least a runner report is a **flag**. Discuss before advancing.
- T4 increments pass on builder self-test + passing regression gate.
- Review runtime monitors if any are deployed. Any active alerts are blockers.

**D — RTDT Cut** (RTDT — Read the Damn Thermometer: if nothing needs it, it goes)
Surface anything overbuilt, unnecessary, or solvable with something simpler. State what the project loses without it, and whether that loss matters for the success definition.

**Gate rule:** Phase advances when the AI and the operator both agree the phase is done. No checklist theater. The judgment is: "would a cold session starting from this file have everything it needs to do the next phase's work?"

---

## COMPACTION PROTOCOL
<!-- Fires at hold points to prevent unbounded growth. -->

At each hold point:
1. Session log entries since last hold point get compressed into one summary entry.
2. Resolved items in Open Questions & Known Risks get moved to a `### Resolved` sub-section or deleted.
3. Recent Decisions older than two phases get pruned (they're in the session log archive if needed).
4. Layer 1 Dashboard gets rewritten fresh.
5. Verification status for completed increments gets compressed to one line in Completed Phases.

The file should never exceed ~300 lines of active content. If it does, something that should be static is being treated as active. RTDT.

---

## SESSION CONTRACTS

### Interactive Session
1. Read Layer 1 and Layer 2. That's your briefing.
2. State what you understand the current state to be and what you think the next action is.
3. If anything in Layer 1 seems stale or contradicts Layer 2, say so.
4. Wait for confirmation before working.

**Closing:**
1. Rewrite Layer 1 Dashboard completely. Don't patch — replace.
2. Update Layer 2 if anything changed (build status, new questions, new decisions).
3. Append one session log entry to Layer 4.

### CC Controller Session (autonomous build execution)
1. Read Layer 1 and Layer 2. Identify increments that are IN PROGRESS or QUEUED.
2. Load `execution-engine.md`.
3. For each increment, read its Tier assignment from Layer 2.
4. Execute using the tier-appropriate protocol from the execution engine.
5. **Hard gate:** After completing each increment, update Layer 2 (increment status, verification status) and append to Layer 4 BEFORE proceeding to the next increment. This is not optional. This is the handoff.
6. On session end (normal or crash recovery): update Layer 2 for any in-progress work, append to Layer 4.
7. Do NOT rewrite Layer 1 Dashboard — that's for the next interactive session.

### CC Verifier Session (autonomous test authoring)
1. Read `verification-architecture.md` and the assigned `specs/verification/[increment].md`.
2. Do NOT read build specs, codebase, or any feature code.
3. Write tests to `tests/verified/[increment]/`.
4. Each test documents: what business rule it verifies, what input, what expected output, why.
5. Produce completion markers per Principles 1–6 (instantiated in the verification architecture).
6. On completion: append session log entry to the flight plan's Layer 4.

### CC Builder Session (dispatched by Controller)
1. Read the build spec for your assigned increment.
2. Read Principles 1–6 as instantiated in your prompt.
3. Do NOT read or access `tests/verified/`. Do NOT read verification specs.
4. Build. Write incremental artifacts. Produce completion markers. Commit.
5. Report back to Controller.

### CC Runner Session (dispatched by Controller)
1. Run the specified test suite against the deployed/built artifact.
2. Log results to the results directory.
3. Report PASS/FAIL to Controller. Do NOT fix anything. Do NOT interpret results.

---

## PROJECT FILE STRUCTURE

<!-- Filled in during Phase 1 or Phase 3 (Scaffold). Shows where everything lives. -->

```
project-root/
├── [project]-flight-plan.md              # This file
├── core/methodology/                      # Universal — same across projects
│   ├── execution-engine.md
│   └── verification-architecture.md
├── specs/
│   ├── build/                             # Per-increment build specs
│   │   └── [increment-name].md
│   └── verification/                      # Per-increment verification specs
│       └── [increment-name].md
├── tests/
│   ├── verified/                          # Written by Verifier — Builder cannot access
│   │   └── [increment-name]/
│   ├── builder/                           # Written by Builder — supplemental only
│   │   └── [increment-name]/
│   └── monitors/                          # Runtime monitors (post-deployment)
│       └── [monitor-name].js
├── results/                               # Produced by Runner
│   ├── verification-run-[date].json
│   ├── regression-run-pre-[date].json
│   ├── regression-run-post-[date].json
│   └── monitor-logs/
└── code-conventions.md                    # Project-specific (or inline in Layer 3)
```

---

*Template version: 6.0*
*Created: April 2026*
*Origin: v5.0 + unified build architecture merger. Added verification columns, tier routing, CC Controller session contract, verification audit at hold points, methodology routing table. Absorbed autonomous execution principles loading into session contracts. Absorbed subagent skill governance into CC Controller contract.*
*Philosophy: One file. Layered for the machine. The cockpit routes to the methodology — it doesn't contain it. Auto-update is structural, not optional.*
