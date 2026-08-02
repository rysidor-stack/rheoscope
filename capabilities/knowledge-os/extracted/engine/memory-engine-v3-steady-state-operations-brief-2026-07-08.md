<!-- Ported verbatim 2026-07-11 from the dogfood proving-ground fork, where the
     memory engine was built and proven. The fork remains the proving ground; template-bound
     updates flow fork -> template.
     [2026-08-01, backlog v3.0-89 operator ruling: venture identifiers genericized for template
      publication; the byte-verbatim certified original is preserved in the dev repo at
      capabilities/knowledge-os/design-history/certified-originals/ and in dev-repo git history.] -->

# Memory-engine v3 — steady-state operations layer (design brief, 2026-07-08)

**Status:** dated sibling brief (the frozen spec + test plan are immutable — this is a *completion* against them, per the README Rules of the road). Addresses harness-backlog **v3.0-21**. Authored at live-ops session 2 after the first-live compile-v2 pilot blocked at registration.

## Problem (recap)

The P0–P5 build proved the SEED; it never closed the LOOP. Every gate ran against the frozen 253-member corpus (drills in fixture roots, rehearsals in discarded worktrees). The state substrates (registration chain, journal, allowlists) are growth-ready append-only, but the **write paths and baselines** around them are mint-once/refuse-twice. So the first real new content (92 events) cannot be registered, the engine's own verify-leg output would crash its census, the ACC-1 baseline has no re-mint operation, and vocabulary-aging sensors don't exist.

## Scoping decision (what this brief authorizes NOW vs. defers)

The operator clarified (2026-07-08): **hand-delivered batches are a test-harness convenience only; production raw is always machine-created (session-loop); hand-offs in this trusted test env derive origin mechanically.** That removes the adversarial framing from the immediate build.

**BUILD NOW (unblocks the pilot, proving-ground scope):**
- **B-1 · delta registration (trusted-batch mode)** — register new raw events onto the existing chain.
- **B-2 · census population boundary** — stop the engine's own machine sidecars from crashing the conservation census.

**DEFER (named residuals, not blocking the pilot; carried in v3.0-21):**
- **R-1 · production session-loop registration writer** — the P1-specified F9-floor-from-intake-record writer (`test-plan.md:490`). A *cutover-era* concern: it matters when raw events are machine-authored by credentialed session loops in the live wiki. B-1 is structured so this is a later *mode*, not a rebuild.
- **R-2 · re-baseline as an operator operation** (ACC-1 baseline mint + gate-pointer).
- **R-3 · vocabulary aging** — ROUTE-3 sensor + UNROUTED-triage loop step (14/92 currently unrouted).
- **LOOP-1 closure gate** — the pilot's own completion is its first pass; formalize + make backport-blocking as a follow-on.

## B-1 — delta registration (trusted-batch)

**Shape.** A new writer (working name `register-intake.py`) that appends registration records for raw events not yet in the chain, using the existing growth-ready `registrations.append_registration` substrate (the chain is fine; only `backfill-registrations.py`'s one-time-mint refusal blocks us).

**Decisions:**
- **D1 · delta, not re-mint.** Enumerate raw events, diff against the effective registration map, register ONLY the unregistered delta. Never touches existing records (append-only; `backfill-registrations`'s idempotence-is-refusal is preserved for the *bulk* mint — this is the sanctioned incremental path).
- **D2 · origin is mechanical.** Derive via the existing `origin.py` (`ryan-*`→`human`, `session-*`/`observation-*`→`corpus`, unparseable→`unknown`). No auto-upgrade of `unknown`. Trusted-batch: no F9 floor applied (no session intake record exists for hand-offs; the operator vouches, and the committed `operator-2026-07-08-first-live-compile-pilot.md` is the batch attestation of record).
- **D3 · unparseable → `unknown`, non-blocking.** `unknown` is a valid, conservative origin — the event registers and the census passes. Attestation (upgrading `unknown`) is a separate, optional operator act, never required to run. Surface the unparseable set as a data-quality note.
- **D4 · atomicity (production shape).** The production entry point lands files + registration in one commit (no enlarged-but-unbaselined window). For the pilot, the 92 are already staged (isolated branch `ef37743`), so the pilot invocation registers-already-present; the atomic file+registration path is exercised by fixtures.
- **D5 · fail-closed + guard.** Refuse partial mints (all-or-nothing per invocation, like the bulk mint); run under the same `--self-test`/conformance discipline; cross-check the built writer (new engine code touching the origin substrate).

## B-2 — census population boundary

**Problem.** `staleness.py` (`os.walk`, recursive) sweeps `receipts/verify/packets/*.md` — written by every live compile verify leg — into the enlarged ledger; unregistered → `EnlargementViolation`. `check-loop-state.py` (`os.listdir`, non-recursive) silently disagrees about the receipts population.

**Decisions:**
- **D6 · one shared named exclusion list.** A single source of truth (e.g. an `ENGINE_SIDECAR_DIRS` constant / a governed key) naming engine-written machine-sidecar dirs (`receipts/journal/`, `receipts/registrations/`, `receipts/verify/`, …), consumed identically by `staleness.py` and `check-loop-state.py` — closing the definition mismatch. Engine self-attestation (minting registrations for its own machine output) is deliberately avoided; exclusion is the honest boundary (these are engine artifacts, not corpus events).
- **D7 · named, never wildcarded** — same discipline as `known-holes.yaml` (explicit entries, extend by adding a name).

## Verification plan

- B-1 + B-2 each fixture-first with `--self-test` (mint delta / refuse re-mint of existing / origin derivation incl. unparseable→unknown / census excludes sidecars / population parity between the two sensors).
- Cross-vendor read on B-1 (origin-substrate touch) per the operating-mode cross-check rule; B-2 is mechanical (conformance-only likely sufficient — orchestrator's call).
- **Loop closure = the pilot:** register the 92 → compile the 5 → verify → census green → journal CLEAN on both commits. That end-to-end green IS LOOP-1's first pass and the real acceptance of this brief.

## Records
Backlog v3.0-21 (the class + this solution); `backport-scope.md` row when built; ledger LIVE OPERATIONS on completion. All B-items + residuals are template-bound (ride the knowledge-os backport).
