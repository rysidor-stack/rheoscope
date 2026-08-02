# Recipe: Sentinel Phase 1 — scheduled read-only monitoring

**What you get:** the project's existing runtime monitors run unattended on a schedule, with a
receipt written per run. **What it never does in phase 1:** write to the repo, open PRs, change
any system it watches, or touch production data. Observation and receipts only.

**Form:** a recipe, not code. You wire your own scheduler to your own monitor scripts using the
reference implementation below. The harness ships no orchestrator for this on purpose — the
schedule is a thin trigger around monitors you already trust.

---

## 1. What a sentinel is (and is not)

A sentinel is the **Controller running on a schedule** instead of in an interactive session.
It is not a new actor in the verification machinery and holds no review authority. In phase 1
it runs read-only monitors and reports; anything those reports motivate goes through the
normal session-based, tier-appropriate build-and-verify path, dispatched by a human.

## 2. The minimal gate (complete it BEFORE the first scheduled run)

This gate is the adoption precondition for any scheduled automation, however small. It is the
operational form of three shipped doctrines — stop it (`core/methodology/rollback-kill-switch.md`),
scope it (`core/methodology/least-privilege-isolation.md`), cap it
(`core/methodology/spend-governance.md`) — plus the data-safety floor
(`core/governance/DATA-POLICY.md`). All four clauses, no substitutions:

1. **Stop it — the halt action is wired and written down.** One documented, single action
   disables all scheduled runs (see §5 for the per-scheduler command). Write it in the same
   place your other emergency procedures live, per the rollback/kill-switch doctrine. Token
   revocation is the hard stop for an in-flight run — disabling a schedule does not kill a run
   already started; note the revocation path next to the halt action.
2. **Scope it — the credential is verified read-only and environment-scoped.** *Verify*, not
   assume: attempt a write with the sentinel's credential and confirm refusal, before the first
   run. Hold the credential as an environment-scoped secret dedicated to the sentinel — never
   repo-scoped, never a credential reused from interactive work, never one that can reach
   production data the monitors don't need.
3. **Cap it — a spend/time cap exists before the schedule does.** A provider-side spend cap is
   in place (account- or key-level), and the run declares a soft ceiling for itself (wall-time;
   tokens too if any model session is involved). A scheduled loop must never be the thing that
   discovers your spend limit for you.
4. **Receipts are PII-clean.** Receipts and any findings detail follow the data policy's
   telemetry rule, quoted verbatim from `core/governance/DATA-POLICY.md`:
   *"Logs are PII-minimized and reference entities by ID. Per-feature attribution keys must not
   embed customer identifiers. 365-day retention with receipted purge. No customer-data runs
   until these hold."*

## 3. The receipt (one JSON file per run — the run's only mandatory output)

Location: `results/sentinel-receipts/run-YYYY-MM-DD-HHMM.json` (declared in the data policy's
retention table: 365 days, receipted purge). A run that writes no receipt did not happen.

The four telemetry floor fields from `core/methodology/spend-governance.md` — **model, tokens,
wall-time, outcome** — are mandatory keys even when their value is null (a pure-script monitor
run has no model and no tokens; the keys stay, null, so the record shape never forks):

```json
{
  "run_id": "sentinel-2026-06-15-0600",
  "scheduled_by": "<workflow/task name>",
  "started_utc": "2026-06-15T06:00:02Z",
  "ended_utc": "2026-06-15T06:03:41Z",
  "wall_seconds": 219,
  "model": null,
  "vendor": null,
  "tokens_in": null,
  "tokens_out": null,
  "outcome": "findings",
  "monitors_run": ["reconciliation", "reference-integrity"],
  "findings_count": 1,
  "findings_ref": "results/sentinel-receipts/run-2026-06-15-0600.findings.md",
  "caps": { "provider_cap": "account-level, see secrets manager", "soft_ceiling_wall_seconds": 600 },
  "halt_action_ref": "<where your halt action is documented>"
}
```

`outcome` is closed: `green` | `findings` | `error` | `halted`. Findings detail references
entities **by ID only** — the receipt and findings files are subject to the PII rule above.

## 4. Adoption steps (numbered; do them in order)

1. **Pick ONE monitor.** Start with a single sentinel running your most-proven monitor. Do not
   schedule two until the first has survived a review cycle (see §6).
2. **Complete the minimal gate** (§2, all four clauses). The gate is the adoption decision —
   if a clause can't be satisfied, the project isn't ready for scheduled automation yet.
3. **Wire the halt action and write it down** (§2.1, §5). Before the first run, not after.
4. **Localize the scheduler config** (§5): replace every `<angle-bracket>` placeholder with
   your project's exact values. Generic commands are not a runbook — the exact-commands rule
   from the rollback/kill-switch doctrine applies.
5. **Run it once attended.** Watch the first scheduled run end-to-end; confirm the receipt
   appears, is PII-clean, and the halt action actually halts (test it once, live).
6. **Record the adoption** in the project's decision log: which monitor, which schedule, where
   the halt action is documented, who reviews receipts and when.

## 5. Scheduler wiring — reference implementation and fallback

**Reference: a CI scheduled workflow (GitHub Actions shown).** Minimal shape:

```yaml
name: sentinel-monitors
on:
  schedule:
    - cron: "0 6 * * 1-5"   # weekday mornings; pick your cadence deliberately
permissions:
  contents: read             # phase 1 is read-only; this line is load-bearing
concurrency:
  group: sentinel
  cancel-in-progress: false  # never two overlapping runs
jobs:
  monitors:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<full-commit-SHA>   # pin by SHA, never by tag
      - run: <your monitor entrypoint> --receipt-dir results/sentinel-receipts/
```

- **Halt action:** `gh workflow disable sentinel-monitors` (verify the exact workflow name).
  Re-enable deliberately with `gh workflow enable sentinel-monitors`.
- Secrets, if a monitor needs any, are **environment-scoped** (per the least-privilege
  doctrine) — never repository-scoped.
- Third-party actions are pinned by full commit SHA.

**Fallback: the local OS scheduler** (a machine you control, no CI):

- cron: `0 6 * * 1-5 <absolute path to monitor entrypoint> --receipt-dir <absolute path>`
  — halt action: remove or comment the crontab line (`crontab -e`).
- Windows Task Scheduler:
  `schtasks /Create /TN "sentinel-monitors" /SC DAILY /ST 06:00 /TR "<command>"`
  — halt action: `schtasks /Change /TN "sentinel-monitors" /DISABLE`.

Either way, the chosen halt action — the exact command, localized — is what step 3 of §4
writes down. One declared action, tested once, findable under stress.

## 6. Reading the receipts (the part that keeps this honest)

Schedule a standing review (weekly is a sane start): read the receipts, triage findings into
the project's normal issue/backlog flow, and ask the alarm-fatigue question — *did this
sentinel tell me anything a session wouldn't have?* Receipts feed the project's spend review
(they carry the telemetry floor fields for exactly that purpose).

**Scaling rule:** stay at one sentinel until the receipt history demonstrates it catches real
drift at acceptable noise. Add a second only on evidence, never on enthusiasm. If noise or
spend exceeds what you declared, the halt action exists — use it; halting a noisy sentinel is
the system working, not a failure.

## 7. What comes after phase 1

Write-back (a sentinel opening gated maintenance PRs) is **phase 2** — a separate, bounded
design with hard exclusions, documented in this capability's `RECIPE.md` § PHASE-2 BOUNDS. It
is not adoptable from this recipe, and phase-1 receipts are the evidence that decision will be
made on. Nothing in phase 1 commits you to phase 2.
