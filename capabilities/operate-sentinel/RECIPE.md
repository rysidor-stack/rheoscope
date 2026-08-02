# Capability: operate-sentinel

## 1. WHAT IT IS

The operate-phase execution mode: a **scheduled Controller** that runs the project's *existing*
runtime monitors unattended on a cron/CI trigger, writes a receipt per run, and — in its later,
separately-gated phase — opens gated pull requests for low-blast-radius maintenance.

A sentinel is **not a new review role**. The project's verification actors (Builder, Verifier,
Runner, Controller) and their separation rules are unchanged: a sentinel is the Controller
running on a schedule instead of in a session. Any remediation it ever proposes flows through
the normal tier-appropriate build-and-verify path; nothing about scheduling exempts work from it.

## 2. WHEN A PROJECT NEEDS IT

When the project is live enough that drift happens *between* sessions: deployed behavior to
watch, reconciliation monitors worth running daily, dependencies aging. The named symptom: you
open a session to check on things rather than to build things.

## 3. WHEN A PROJECT DOESN'T

Pre-launch, or while every monitor is still being run by hand inside build sessions. A schedule
adds credentials, spend, and noise; it earns its keep only when the monitors themselves are
already proven and somebody reads what they report.

## 4. STATUS

- **Phase 1 (read-only) — adoptable now** via `deferred/sentinel-phase-1.RECIPE.md`: a
  scheduler trigger runs existing monitors read-only and writes receipts. It changes nothing,
  writes nothing back, opens nothing. Adoption is gated by the recipe's **minimal gate**
  (verified read-only credential · environment-scoped secret · spend/time cap · PII-clean
  receipts) plus a wired halt action.
- **Phase 2 (gated write-back) — designed boundaries only, NOT adoptable.** No orchestration
  code ships, no recipe ships. The boundaries below are binding on any future phase-2 design;
  they are recorded here so an adopter cannot discover them late.

## 5. PHASE-2 BOUNDS (binding on any future write-back design)

1. **Gated PR only, never auto-merge.** A sentinel may open a pull request; a human merges it.
   No exception, including "trivial" changes.
2. **Never a PR touching the project's highest-blast-radius surfaces** (for a typical system:
   payments, schema migrations, tenant isolation, auth, deploy configuration). Those stay
   build-phase and human-gated regardless of how routine the change looks.
3. **Eligible classes only:** dependency upgrades, security-patch triage, test-coverage
   backfill, tech-debt/lint paydown. The list is closed until practice reopens it.
4. **Token scope is exact** (per `core/methodology/least-privilege-isolation.md`): enable
   PR-creation by automation explicitly (it is off by default) and grant exactly
   `contents: write` + `pull-requests: write` — never merge or admin scope.
5. **Kill switch and revert path exist before first enable** (per
   `core/methodology/rollback-kill-switch.md`): one documented action disables all scheduled
   runs; the revert procedure for a sentinel-opened branch/PR is written down where the
   operator can find it under stress.
6. **Promotion is an operator decision, recorded.** Phase 2 may be enabled only after phase-1
   receipts demonstrate signal over noise across a stated review window, and the enabling
   decision is recorded in the project's decision log (ADR).

## 6. DEPENDENCIES

- The project's existing runtime monitors (this capability schedules them; it ships none).
- `core/methodology/rollback-kill-switch.md` — the halt-action and revert doctrine.
- `core/methodology/least-privilege-isolation.md` — credential scoping doctrine.
- `core/methodology/spend-governance.md` — the telemetry floor fields and cap discipline.
- `core/governance/DATA-POLICY.md` — receipt retention (`results/sentinel-receipts/`, 365
  days, receipted purge) and the PII rules receipts must satisfy.

## 7. AUTHORING GUIDE

Phase-1 adoption: `deferred/sentinel-phase-1.RECIPE.md` (complete, self-contained).
Phase-2: do not author until phase-1 receipts exist; design against §5 above.

## 8. KNOWN LESSONS

- Start with **one** sentinel. Monitoring research and operating experience agree that noise
  scales faster than value beyond the first one or two; a sentinel nobody reads is alarm
  fatigue with a credential attached.
- The receipt is the product. A run that writes no receipt did not happen, for the same reason
  an unverified build is not done.
- Schedules outlive intentions: the halt action gets wired *before* the first scheduled run,
  not after the first incident.

## 9. OPEN QUESTIONS

- Per-project monitor inventory and noise profile (drives whether phase 2 is ever worth it).
- The promotion threshold from phase 1 to phase 2 (what "signal over noise" means numerically
  for this project) — set it when phase-1 receipts exist, not before.

## 10. MIGRATION STEPS

(Phase 1 is recipe-only; nothing to migrate. If a future harness version ships an extracted
orchestrator, its RECIPE.md documents the migration from a hand-wired phase-1 schedule.)
