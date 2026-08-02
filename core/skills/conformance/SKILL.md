---
name: conformance
description: Replay behavioral-manifest rows against the live build. Smoke tier on cadence, full tier at freezes and certification. Coverage counted, never vibes.
---

# /conformance — Behavioral-Manifest Conformance Sweep

*verified-against: 3.0 (2026-07-20)*

/conformance is the standing orchestrator peer to `/compile` and `/audit` — but for the
manifests layer, not the wiki. A sweep replays manifest rows (`manifest-format.md`) against
the live build and counts coverage. A defect is a row; a fix is a row turned green; nothing
ships on a feeling.

## When to use

- **Smoke tier**, on cadence — a designated drift-likely subset. Each surface's
  `MANIFEST-INDEX.md` may name its own smoke set; sweep that.
- **Full tier**, at design freezes, pre-certification, and after fix waves.
- **After any projection-change (refactor) dispatch** — before/after sweep invariance is that
  move's done-bar.

## Tiers

- **Smoke** is a named subset, not a sample — the surface's `MANIFEST-INDEX.md` should name
  which rows belong to it (drift-likely rows: recently amended, historically flaky, or
  covering a surface's highest-traffic path). If no smoke set is named, smoke tier does not
  exist for that surface yet; run full or name the set first. Never invent a smoke set by
  guessing which rows "probably matter."
- **Full** is every row in every in-scope layer manifest, no sampling. Full tier is what a
  coverage claim is allowed to be computed against; smoke tier never produces a coverage
  claim, only a cheap health check.

## Protocol

1. **Scope.** Read `manifests/<surface>/MANIFEST-INDEX.md`; pick the tier (smoke or full) and
   the layer manifests it puts in scope. Run `deploy/check-manifest.py` first if present —
   structural health before behavioral replay; a manifest failing its own structure check
   cannot be swept honestly.

2. **Replay.** Per layer, per row, drive the row's own replay path with its own evidence
   modality: browser drive for interaction/design rows, golden replay over the pinned seed for
   logic/data rows, request probes for authorization rows, fault/event injection for
   failure/async rows. Fan rows out to subagents — this is the same workflow-subagent
   substrate the twin-build mode uses (`manifest-format.md` §11); the orchestrator itself never
   drives a row, it dispatches and adjudicates the returned evidence. RUBRIC rows are graded
   against the criteria stated in the row itself — nothing else; a RUBRIC row with no stated
   criteria is a manifest defect, not a call for the sweep to improvise one. Grading protocol
   (`manifest-format.md` §4, decision #9-as-amended): a RUBRIC row is graded by a session other
   than the one that produced the artifact under grade — the builder/verifier firewall
   generalized to judgment rows; the grade lands in the receipt with per-criterion reasoning,
   never a bare scalar; grading disagreements escalate to the hub, never average out.

3. **Classify every red row** via the amendment log (`manifest-format.md` §8). A red row with
   an open amendment behind it is **DECLARED nonconformance** — a work-queue item, red by
   design. A red row with no amendment behind it is **UNDECLARED nonconformance** — a defect.
   The sweep reports the two classes separately: eliminating undeclared nonconformance is the
   goal, declared red is healthy. Row lifecycle (PROPOSED vs. pinned) is read from the `DRAFT`
   flag plus the amendment log only (`manifest-format.md` §5, decision #4-as-amended) — never
   from an ID prefix, which is an optional, non-authoritative birth-record.

4. **Receipt.** Name it `<surface>-conformance-bless-rN` (rounds increment N). It carries:

   - rows replayed / passed / failed, by ID, per layer
   - the two red classes, counted separately (declared vs undeclared)
   - coverage arithmetic: rows-replayed / rows-total, per layer and rolled up
   - the completeness-hunt receipts behind that denominator — source cross-check, variant
     matrix, precondition sweep, adversarial review, and twin-build divergence where run
   - the surface's OPEN-marker count; at certification, each OPEN individually dispositioned
     as an acceptable-unknown with a named owner (`manifest-format.md` §4, decision
     #5-as-amended) — an undispositioned OPEN blocks CERTIFIED

   "100% coverage" over an unpressured denominator is the new "QA passed" — this skill never
   emits that claim bare. A receipt that reports coverage without naming which completeness
   hunts pressured the denominator is incomplete, not conservative.

5. **Findings become fix waves.** Fix waves re-sweep to green, or to declared-red with
   amendments on file. Stop-at-boundary is success — a sweep that stops because everything
   in scope is either green or declared-red is done, not abandoned.

## Blocking semantics

At certification time, a FULL-tier sweep MAY block certification. This deliberately differs
from the flight-plan sensors (degrade-never-block) and from the mutation pass's caution — "a
gate calibrated on one run is noise enforcing itself" governs STATISTICAL gates that need a
baseline. Manifest rows are not that: each row was individually verified against the frozen
design before any build consumed it, so a red row is a contract violation, not statistical
noise. There is no baseline to distrust — the row was already the baseline.

Smoke-tier runs never block anything, at any time. They exist to be cheap enough to actually
run — a blocking cheap check gets skipped under pressure, which defeats the point of having a
cheap tier at all.

## What this is NOT

- **Not the structural sensor.** That's `deploy/check-manifest.py` — frontmatter, row counts,
  ID uniqueness, flag vocabulary. It checks that a manifest is well-formed; /conformance checks
  that the build matches it.
- **Not QA-by-feel.** The manifest is the stopping criterion. Done means every in-scope row
  exercised, not "looks right."
- **Not an approver.** Certification decisions cite the receipt; humans and the hub still
  decide. /conformance produces evidence, not verdicts.

## Where it sits

/conformance is a core skill, peer to `/compile` and `/audit` — those keep the wiki and
roadmap honest, this keeps the manifests-to-build correspondence honest. It consumes
`manifest-format.md`'s contract directly: §3 frontmatter, §4 row discipline, §8 amendments,
§12 tooling and wiring. Its receipts are what `MANIFEST-INDEX.md` `certified_by` fields and
gate declarations point at — a `CERTIFIED` layer without a conformance receipt behind its
`certified_by` field is a claim, not a fact.

Twin-build certification (§11) is a sibling completeness hunt, not a substitute for this
sweep: a twin-build diff adjudicates to either a missing row (which this sweep's next round
picks up as an amendment) or a recorded don't-care. Reserve it for T1/keystone surfaces per
§11's STANDING OBLIGATION; every other surface's completeness case rests on this sweep plus
the other four hunts named in the receipt (step 4).
