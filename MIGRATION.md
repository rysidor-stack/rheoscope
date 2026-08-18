# MIGRATION.md — upgrade recipes for instantiated projects

**You don't do any of this by hand (v3.0.26).** Open a session in your project, point it at this file, and say "adopt v3.0.NN" — the numbered steps below are the SESSION's instructions, written in its language. Your part is small and real: every entry marked **[your call]** is a decision only you can make (arming reviews, permission changes, anything that alters what runs unattended); the session must stop and ask you those in plain words, and everything else it does and reports back per `core/governance/CLAUDE.md` § Reporting to the operator.

*The harness instantiates as a one-way fork (see `TEMPLATE-README.md` § Why no upgrade path). This file is the per-surface, opt-in recipe for pulling a newer harness's changes into an already-instantiated project — adopt what you need; nothing here re-runs init (init is one-shot by contract). Automated migration is deferred: revisit at ≥3 instantiated projects (ADR #1 reopen trigger 5; `adr/2026-06-09-2-v2.0-versioning-and-migration.md`).*

*General method for any version step: clone the new harness next to your project, `diff -r` the surfaces named in its changelog entry, and copy what you adopt — substituting your project's values where the template uses `{{...}}` variables (your values are all in your `project.yaml`).*

**Standing step, every migration (added 2026-07-28, backlog v3.0-75): refresh the root
orientation docs.** Copy the new harness's `ARCHITECTURE.md` over your project's root copy in
the same adoption pass — it carries no `{{...}}` variables and describes the harness, not your
project, so the copy is always safe. Instantiation copies it in and nothing else ever touches
it, so an un-refreshed instance keeps confidently describing the harness it was born with (the
fork ran a v3.0.x harness while its root ARCHITECTURE.md still said "version 2.1", and a
reviewer grounding against it inherited a version-old model). `/doctor`'s docs-stamps check now
WARNs when the doc's `verified-against:` stamp trails your `template_version`.

---

## v1.x → v2.0

**Status note:** v2.0 **shipped 2026-06-10** (tag `v2.0`; see `HARNESS-CHANGELOG.md`). This section covers the increments that landed against it needing a migration recipe (currently **#10 — versioning + backlog discipline** is the only one with its own subsection below; v2.0's other items were additive-only and need no recipe). Each increment that needs one gets its own subsection.

### Provenance rule (read first)

`project.yaml.template_version` records what you instantiated from. **A partial, surface-by-surface adoption does not change it.** Set it to `"2.0"` only if you adopt the full v2.0 surface set wholesale; otherwise leave it at your instantiated version and record what you adopted in your project's decision log (`docs/adr/`). Your backlog entry prefix follows `template_version` — that is by design (entries record the version you actually run).

### #10 — version-relative backlog discipline (irreversible moves marked ⚠)

1. ⚠ **Rename the backlog file:** `git mv v1.1-backlog.md harness-backlog.md`. Keep every existing `v1.1-N` entry verbatim — assigned numbers are permanent and the old prefix remains valid history.
2. **Replace the file's documentation header** (everything above your first entry) with the v2.0 `harness-backlog.md` header (entry format, "Versioning and numbering", severity meanings, process notes). Your entries stay; only the discipline text updates.
3. ⚠ **New entries use the version-relative numbering:** `v<template_version>-N`, prefix read from your `project.yaml` at logging time, `N` restarting at 1 for each version. Do not continue the `v1.1-N` sequence.
4. **Update the installed skill:** copy the new harness's `core/skills/log-backlog/SKILL.md.template` over your `.claude/skills/log-backlog/SKILL.md`, then hand-substitute any `{{...}}` variables using your `project.yaml` values (the v2.0 file has none beyond what v1.x had).
5. **Update `CLAUDE.md` § Session discipline:** replace the two `v1.1-backlog.md` mentions and the `v1.1-N` numbering sentence with the v2.0 wording (see the new template's § Backlog logging).
6. **Update `project.yaml` → `placeholder_scan_exempt`:** change the `v1.1-backlog.md` entry to `harness-backlog.md`.
7. **No `VERSION` file lands in your project.** It is a template-repo artifact that init consumes; after migration your version still lives only in `project.yaml.template_version`. If you cloned a v2.0 harness for diffing, do not copy `VERSION` across.
8. **Schema title de-versioning (ADR #2 ledger row 5): no action on your side.** The schema validates `project.yaml` *before* init and is inert in an already-instantiated project — listed so the recipe visibly covers every ledger row.

### Why your old project.yaml fails against a v2.0 clone (expected)

The v2.0 init gate reads the template's `VERSION` file and requires `template_version` to match it. A v1.x `project.yaml` therefore fails fast with a migration-aware message. That is the gate working — init is not a migration tool; this recipe is.

---

## v2.x → v3.0

**Not yet defined.** v3.0 **shipped 2026-07-24** (tag `v3.0`; see `HARNESS-CHANGELOG.md`'s STATUS blockquote for the release-ritual record) after its surface kept moving through 2026-07-23 — most recently the R-1/credential-broker/workspace-governance/dogfood-manifest wave below, closed out by session E's release ritual. The memory-engine v3 backport itself **landed 2026-07-11** (`HARNESS-CHANGELOG.md` Theme E), so it is no longer the open item this note originally described; a full v2.x → v3.0 migration recipe is still owed post-ship, per the changelog discipline above — track it there, not here.

---

## v3.0 → v3.0 + behavioral manifests (partial adoption)

The behavioral-manifest layer (doctrine: `core/methodology/manifest-driven-builds.md`, ratified upstream 2026-07-16; harness contract: `core/methodology/manifest-format.md`) can be adopted surface-by-surface by any instantiated project. Per the provenance rule above, partial adoption does NOT change `project.yaml.template_version`. Record what you adopted in your project's decision log.

1. Copy `core/methodology/manifest-driven-builds.md` and `core/methodology/manifest-format.md` from the template into your project's `core/methodology/` (or `methodology/` — wherever your instance renders methodology docs), and add both to your methodology index if you keep one.
2. Amend your rendered `verification-architecture.md` Firewall Rule 3 to the template's amended form ("The Manifest Set Is Upstream; Specs Are Its Projections") — copy the rule text verbatim from the template's `.template` source, dropping any substitution placeholders your instance already resolved.
3. Apply the template's manifest-gate edits to your rendered `execution-engine.md` (tier-protocol Step 0 + Builder Prompt Manifests block + Report Format coverage lines) and `tier-definitions.md` (per-tier gate lines) — copy from the template's `.template` sources. Also port Part 7's result-manifest naming-disambiguation parenthetical (part of the naming firewall). Where your rendered skill/doc copies use different step numbering than the template's current sources, renumber the inserted block's internal cross-references to your file's actual siblings (first proof run: the fork's flight-plan had steps 5.5–5.7, so the sensor step landed as 5.8 with its range citations adjusted).
4. Copy `core/skills/conformance/` into your `.claude/skills/conformance/`.
5. If your project runs the knowledge-os capability: copy `deploy/check-manifest.py` and `deploy/manifest-layers.yaml` into your `deploy/`, run `python deploy/check-manifest.py --self-test` to confirm, and add the sensor to your flight-plan briefing's sensor step. If not: skip — the contract binds session practice without the sensors (`manifest-format.md` §12).
6. On your NEXT design freeze or build increment: create `manifests/<surface>/` per `manifest-format.md` §2, extract manifests for the layers the increment touches, and let the gate govern from there. Do not retrofit manifests for surfaces you are not touching — retrofit is per-surface, on touch, never a reason to delay work their content already covers (grandfathering, doctrine §6).
7. Pre-existing manifest-shaped artifacts (checklists, state inventories, spec appendices) that the new manifests supersede: retire each with a pointer to the manifest that replaces it — never leave two sources of truth standing (the amended Rule 3 exists to prevent exactly that).

This recipe was first executed against the Rheoscope dogfood fork (the template's own proving ground); the incorporation records live in `harness-v3.0/specs/behavioral-manifest-incorporation-brief-2026-07-20.md`.

---

## v3.0 → v3.0 + the finishing-plan wave (partial adoption)

The v3.0 finishing-plan wave (`harness-v3.0/V3-FINISHING-PLAN.md`; `HARNESS-CHANGELOG.md` Theme J, sessions A–D, 2026-07-22/23) adds several independent surfaces to `capabilities/knowledge-os/extracted/deploy/` and `core/governance/`. Each is opt-in and per-surface, same provenance rule as above — adopting one does not change `project.yaml.template_version`; record what you adopt in your project's decision log.

1. **R-1 — the session-loop intake-and-promotion pipeline** (closes backlog `v3.0-21`'s "never built" residual). Copy `candidates.py`, `harvest-candidates.py`, `register-candidates.py`, `check-candidates.py`, `promote-candidate.py`, `signing-config.yaml.example` into your `deploy/`, and fold the `decision-inbox.py` candidate section + the `assemble.py`/`origin.py`/`registrations.py`/`tool_grant.py` deltas from the same commits (template `9146c10`, `ae52693`, `b433d4b`, `9071df4`). Ships **UNARMED by design** — no fingerprint is pinned in `signing-config.yaml.example`; promotion to an operator-authored event refuses until *you* generate a dedicated SSH signing key in your own hands and pin its `SHA256:` fingerprint (do this only when you're actually ready to run the pipeline — the absence is the fail-safe, same doctrine as `origin-config`). Wire the census step into your `/sweep`. (2026-08-08 note: the acceptance drill this item used to have you run, drill-r1-acceptance, is a harness-dev tool and no longer ships; the pipeline's own scripts carry their `--self-test` modes.)
2. **Credential broker.** Copy `credential-store.ps1`, `credential-use.ps1`, `credential-remove.ps1`, `credential-bindings.yaml.example` into your `deploy/`; add the broker entries to your `safe-allowlist.yaml` (credential-class). Bindings start empty (fail-safe) — add one binding per credential name + destination as you adopt each. Run `credential-selftest.ps1` to confirm (24/24 at this template's HEAD, incl. the redirect-refusal and universal leak-scan cases). Windows-only (Windows Credential Manager via P/Invoke); no recipe exists yet for other platforms.
3. **Workspace governance.** Copy `core/governance/WORKSPACE.md.template` (instantiate with your own zone names if you don't use the four defaults) + `core/governance/projects.yaml.example` (the workspace registry) + `deploy/check-workspace.py` into your project; wire it as a `/sweep` step (this template runs it as step 9). Report-only by design — it never deletes; adoption on a live machine starts with a classify-everything reap report, never a machine reorganization.
4. **Deadline-and-trigger register.** Copy `deploy/check-deadlines.py` + `deadline-register.yaml.example`; seed the register with your own project's real clocks (cert rotations, token expiries, if-triggers) rather than carrying the fork's example rows across. Wire as a `/sweep` step (step 10 here).
5. **Environment manifest + doctor version-drift.** Copy `environment-manifest.yaml.example` into your project and seed it with your own toolchain's live-probed versions; `/doctor`'s `check_version_drift` (already core, no copy needed if you're on this template's `/doctor`) picks it up automatically and WARNs on drift, never fails.
6. **Mirror/instance parity.** Copy `deploy/check-parity.py` and wire it as a `/sweep` step (step 11 here) **only if** your project maintains a fork/mirror relationship to a template or another instance — it SHA-256-compares your engine mirror + skill inventory against that source. Not applicable to a project with no such relationship.
7. **Desk enrichment + the empire desk — REMOVED from the template 2026-08-08 (operator decision).** The three desk scripts this item used to name were deleted: never used on any instance, never live-tested, and the empire desk's ≥3-instance activation trigger never fired. Do not adopt them; the design survives only in the template's git history (last present at tag `v3.0.30`). The `decision-inbox.py` first-seen-age sidecar mentioned here is NOT part of the removal — it is live and ships with the decision inbox itself.
8. **Harness-surface behavioral manifests.** If you've adopted the behavioral-manifest layer above (§ "v3.0 → v3.0 + behavioral manifests") and you also run `/sweep`'s briefing, the decision inbox, or `/compile`'s receipts, the certified/extracted manifest sets under `capabilities/knowledge-os/extracted/manifests/{sweep-briefing,decision-inbox,compile-receipt}/` plus `deploy/check-briefing-format.py` are copyable per manifest-format.md's own per-surface, on-touch convention (doctrine §6) — do not retrofit manifests for a surface you haven't touched. Note the `compile-receipt` surface's `scope_tags`/`cross_links_changed` fields have two competing live shapes in the corpus this template's own dogfood is drawn from (61 vs. 15 files, held OPEN) — resolve that fork against your own corpus before certifying, don't assume either shape.
9. **MDD hand-over fold-in (2026-07-24), if you've adopted the behavioral-manifest layer above.** Purely additive to what you already copied — no existing manifest needs editing. Re-copy `core/methodology/manifest-driven-builds.md` (now carrying v2.2 Addendum entries 14–18) and `deploy/manifest-layers.yaml` (two new RESERVED entries: `rendering-fit`, `config` — name-and-rationale only, no row schema yet) + `deploy/check-manifest.py` (checks 9–10: bidirectional amendment↔row linkage, cross-surface row-ID uniqueness, plus `CONFLICT`-marker counting/gating — `manifest-format.md` §4's new `OPEN` sibling, opt-in on any manifest, never required). Run `python deploy/check-manifest.py --self-test` (76/76 at this template's HEAD) and your own live sweep to confirm nothing regresses.

This recipe was first executed against the Rheoscope dogfood fork; the build records live in `harness-v3.0/V3-FINISHING-PLAN.md` and the specs it names (`r1-session-intake-writer-design-brief-2026-07-21.md`, `r1-build-decisions-2026-07-22.md`, `credential-broker-design-brief-2026-07-23.md`, `workspace-governance-brief-2026-07-22.md`, `long-horizon-seam-census-2026-07-22.md`, `harness-v3.0/mdd-handover-2026-07-24/RESPONSE.md`).

---

## v3.0 → v3.0.1 (patch adoption)

The 2026-07-25 patch (`HARNESS-CHANGELOG.md` v3.0.1 entry) is narrow and security-relevant enough to adopt regardless of what else of v3.0 you've adopted — it does not require the behavioral-manifest layer or the finishing-plan wave above. All additive; re-running `/doctor` (or the relevant sensor's own `--self-test`) is the only verification step, and per the provenance rule this does not change `project.yaml.template_version`.

1. **PowerShell hook-matcher coverage — the security-relevant fix.** If you have security hooks wired (`.claude/settings.local.json`, from init §6b's consent-prompted wiring or your own copy of `core/security/settings.local.json.example`), add the `"PowerShell"` matcher entry alongside your existing `"Bash"` one — the delta shape:
   ```json
   { "matcher": "Bash",
     "hooks": [{ "type": "command", "command": "$CLAUDE_PROJECT_DIR/core/security/hooks/block-dangerous-bash.sh" }] },
   { "matcher": "PowerShell",
     "hooks": [{ "type": "command", "command": "$CLAUDE_PROJECT_DIR/core/security/hooks/block-dangerous-bash.sh" }] },
   ```
   Re-copy `core/security/hooks/block-dangerous-bash.sh` itself (it gained a root-targeting `Remove-Item -Recurse -Force <bare drive/POSIX root>` deny rule — the PowerShell analog of the existing `rm -rf /` rule) and `core/security/hooks/README.md` (documents the matcher-scope gap this closes).
2. **Re-copy `core/skills/doctor/doctor.py`.** Check 7 (`hooks-wired`) is now matcher-aware; a Bash-only wiring that previously PASSed will now WARN with a `FIX:` line naming the missing `PowerShell` matcher. Run `/doctor` (or `python core/skills/doctor/doctor.py --self-test` — 44/44 at this template's HEAD) to confirm.
3. **If you run the knowledge-os capability and have handoffs:** re-copy `deploy/check-loop-state.py` — it now mechanically enforces substrate separation on handoff rounds (self-test 28/28). Run it live once after adopting; expect WARNs (never FAILs) on any pre-2026-07-25 round whose `answered_by` predates the rule or isn't a parseable substrate identifier — informational, no action required unless you choose to tighten your own `answered_by` convention going forward.
4. **Re-copy `core/skills/preflight/SKILL.md`.** Step 2's fan-out line no longer pins specific model tiers or cites a governance-doc heading this template doesn't carry.

Adjudicated from an external Opus-5 skills assessment of a sibling repo; first executed against the Rheoscope dogfood fork, cross-vendor leg at the fork's `receipts/verify/opus5-assessment-2026-07-25/`.

---

## v3.0.34 → v3.0.35 (release integrity: the mechanized stranger test)

Nothing mechanical travels — both changes live in the template maintainer's release tooling
(`make-release.py`, which is never in your tree). One optional cleanup. No **[your call]**
entries beyond it.

1. **Optional cleanup: delete `audits/` from your project root if instantiation left one
   there.** Release artifacts v3.0.22 through v3.0.34 mistakenly shipped the template
   maintainer's audit records (files like `audits/2026-08-04-silence-sweep.md`) — they
   describe the harness template's own history, not your project, and nothing in your
   instance reads them. Harmless to keep; delete for tidiness. From v3.0.35 the artifact
   no longer carries the directory.
2. **Nothing else changes for instances.** The mechanized stranger test (v3.0-62) runs at
   export time in the template repo: surface-touching releases now refuse to export without
   a receipted fresh-instantiation run, and "stranger test not required" skip lines are
   derived from the release diff instead of asserted from memory. You see the effect as
   better-tested releases, not as a file in your tree.

## v3.0.35 → v3.0.36 (the commit scanner — every commit checked for secrets)

**One [your call] entry, and it is the whole release.**

1. **[your call] Adopt the commit scanner.** Every `git commit` in your project — yours and
   any session's — gets scanned for secret-shaped content (key files, API tokens, passwords
   in URLs) and refused if any is found, with the file and pattern named. Your bypass for a
   false alarm is `git commit --no-verify` (AI sessions are mechanically barred from using
   it). If you'd rather commits stay unscanned, skip this whole section.
2. **Copy the new files** (shell copy from the new template clone, not in-session editing —
   the perimeter dir is now write-guarded): `core/security/hooks/scan-staged-secrets.sh`,
   the updated `block-dangerous-bash.sh` and `block-env-writes.sh`, the new `test-inputs/`
   fixtures, and the hooks `README.md`. Then install the scanner:
   `cp core/security/hooks/scan-staged-secrets.sh .git/hooks/pre-commit` (if you already
   have a pre-commit hook, chain it instead). Run
   `bash core/security/hooks/scan-staged-secrets.sh --self-test` — expect 29/29 at this
   recipe's tag (31/31 once v3.0.38 is adopted; an earlier draft of this line said 27/27,
   a pre-ship candidate count).
   **Known wall at this tag (fixed in v3.0.38):** the scanner blocks the very commit that
   adds its own source file — its pattern table looks secret-shaped to itself. If you adopt
   v3.0.36 or v3.0.37 directly, the sanctioned way through is a one-time operator-run
   `git commit --no-verify` of that one file (or adopt v3.0.38+, where the scanner's own
   canonical path is exempt and the commit just works).
3. **Sweep step:** copy the new `/sweep` skill (step 17 surfaces any egress-allowlist change
   to you once per sweep). No other wiring changes; restart Claude Code so hooks reload.
4. **Also in this release (no action):** the v3.0.35 release-integrity gate and audits/
   packaging fix ride any adoption of `make-release`-era tooling automatically; instances
   that pulled a v3.0.22–v3.0.34 zip may delete any `audits/` directory that arrived with
   it — it was the template maintainer's own record, shipped by mistake (v3.0-103).

## v3.0.36 → v3.0.37 (lifecycle completion — discarded approvals recoverable, engine-born labels honest)

If you run the knowledge-os engine. All drop-over copies; no **[your call]** entries at
adoption time — the recovery acts themselves stay yours at use time (each one is a fresh
cross-vendor check or your recorded ruling; nothing recovers silently).

1. **Copy the four engine files** into your `deploy/`: `compile-driver.py` (journal-first
   leg classification — a view is confirmed iff the journal holds an `absorption_verified`
   entry; the verdict artifact is forensics, never state — plus the three reopened repair
   doors and the ledger/trajectory supersession rule), `compile-v2.py` (mint provenance +
   the consumed_status advance at stamp time), `backfill-derivation.py` (regions it mints
   now record `minted_by: backfill`), `check-frontmatter.py` (the optional `minted_by`
   key + enum). Run each `--self-test` to confirm (206/217/14/36 at this template's HEAD).
2. **Copy `engine/OPERATIONS.md`** — §7's BLOCKED row now names the third recovery verb for
   stamp-refused-only runs (`--reverify` re-fires the legs; its fresh verdict decides — not
   a disposition; the two-disposition rule stands).
3. **What this unlocks on your instance:** any pre-v3.0.29 discarded approval (a verify leg
   whose artifact says `confirmed` but the engine recorded no stamp) stops reading as
   confirmed and its repair doors open — `--revert` + re-ride, operator `--set-aside`
   (zero-dispatch, your ruling recorded), or the narrow `--reverify` (discarded-approval
   shape ONLY; a genuine rejection stays declined — no verdict re-rolls). Views the engine
   creates from now on carry `minted_by: engine` and flip to `consumed_status:
   verified-consumed` when a verify confirms, making them T1-servable.
4. **Honest boundaries, so they're no surprise:** recovered approvals on BACKFILLED views
   re-earn their `verified:` stamp but stay `legacy-assumed` — the migration content-audit
   obligation (F13) is theirs and a verify confirm never clears it, so they remain barred
   from T1 serving until a real content audit. Engine-born views minted BEFORE this release
   carry no `minted_by` line and also stay conservatively `legacy-assumed` after confirming
   (no relabel ships; a journal-verified relabel remains possible later if it ever
   matters). `audit-pending` never advances. No journal rewrite, no backfill from
   artifacts, nothing loosens.

## v3.0.37 → v3.0.38 (the scanner stops blocking its own adoption)

One file, no **[your call]** entries — a tightening-preserving false-positive fix
(backlog v3.0-104, surfaced live at a fork adoption 2026-08-12).

1. **Copy the updated scanner** (shell copy, the hooks dir is write-guarded):
   `cp <template>/core/security/hooks/scan-staged-secrets.sh core/security/hooks/` and
   reinstall it: `cp core/security/hooks/scan-staged-secrets.sh .git/hooks/pre-commit`
   (re-chain if you chained it). Run
   `bash core/security/hooks/scan-staged-secrets.sh --self-test` — expect 31/31.
2. **What changed:** the scanner's own source file at its canonical path
   (`core/security/hooks/scan-staged-secrets.sh`) is now exempt from its own scan — its
   pattern table looks secret-shaped to itself, which blocked the adoption commit on
   every instance (you may have hit this as a one-time operator `--no-verify`). Safe by
   construction: agents are mechanically barred from writing anywhere under
   `core/security/hooks/` (the v3.0.36 write-guard), and the same bytes under any OTHER
   path still block — both directions battery-pinned. Nothing else loosens.

## v3.0.38 → v3.0.39 (the two verify-lifecycle doors: union-leg set-aside + operator baseline-reset)

If you run the knowledge-os engine. Drop-over copies at adoption time; both doors are
**operator verbs at use time** — every act through them is your recorded ruling, journaled
verbatim, and a session whose work is being verified never runs either one (the
no-self-adjudication bright line, extended to `--baseline-reset` in OPERATIONS §7).

1. **Copy the two engine files** into your `deploy/`: `compile-driver.py` (the
   `--set-aside --union-event` addressing mode, the `--baseline-reset` verb with guard
   chain G1–G7, and the verify-ledger union-row fix) and `compile-v2.py` (the trigger-state
   union skip — REQUIRED, per the design's cross-vendor correction — plus the baseline
   ladder's third rung and its packet naming). Run each `--self-test` to confirm
   (253/225 at this template's HEAD).
2. **Copy `engine/OPERATIONS.md`** — §7's BLOCKED/RECORDED rows now name the union
   addressing form, the ADJUDICATED row names the `union:<event>` subject, the baseline
   paragraph carries the third rung, and the no-self-adjudication paragraph extends the
   bright line to `--baseline-reset`. No new state; the eight-state table and "there is no
   third disposition" stand unchanged.
3. **What door 105 unlocks:** a union no-op leg that completed with a genuine non-confirm
   verdict (`--verify-ledger` rows reading `union:raw/... open-blocking` forever) is now
   addressable: `--set-aside --seq N --union-event <raw path> --ruling "<your words>"`
   closes the row (ledger `set-aside`, drill ADJUDICATED). No baseline moves — a union leg
   absorbed nothing. The mixed-run `--reverify` decline is byte-untouched: adjudicating a
   union row is not a re-fire ticket for its run's other legs.
4. **What door 106 unlocks:** after an out-of-engine corpus refresh (a photograph that
   rewrote articles with no per-event provenance), `--baseline-reset --view <path>` (or
   `--views-file <list>`) `--refresh-commit <sha> --provenance "<what/when/where>"
   --ruling "<your words>"` pins the verify baseline at the refresh commit, ending the
   guaranteed-false window-rejection class for those articles' FUTURE verifies. The verb is
   scope-locked to declared imports (the commit must exist in history, be an ancestor of
   HEAD, and not be engine-authored), refuses to rewind past any stamp the lifecycle
   already earned (G6), and journals every bulk refusal by name.
5. **Honest boundaries, so they're no surprise:** a union set-aside advances no baseline;
   a baseline reset closes no open verdict row (prior rejections still each cost a ruling)
   and verifies nothing — packets open "(baseline: reset to imported snapshot by operator
   ruling, not machine-verified — <your provenance>, journal seq N)"; T1 serving,
   `consumed_status`, and migration-audit debt are untouched by both doors; a view whose
   stamp is incomparable with the refresh commit fails closed (the remedy is a fresh
   photograph commit, not a guard exception).
6. **The dogfood fork's close-out, previewed (runs in the fork's own session after it
   adopts this release — spec §7.2 as amended by its §10, the 2026-08-17 operator
   ruling):** the two standing union rows close by two `--set-aside --union-event`
   rulings (runs 122/124 — the ledger's exit census moves 2 → 0 open-blocking,
   discharging cutover eligibility criterion 2's asterisk), then a bulk
   `--baseline-reset --views-file` over the refreshed views at photograph commit
   `1f41621` — with **one flight-plan article EXCLUDED from the list (and from any
   per-view reset) by operator ruling** (named in the spec's §10 amendment): the fork's
   drain left that article's newest stamp pinning pre-photograph content one journal seq
   AFTER its run-204 post-photograph adjudication, so a reset would proceed and silently
   rewind behind run 204 (the v3.0-107 shape; G6 cannot see it — the stamp it compares
   against already pre-dates the photograph). Views whose stamps already sit past the
   photograph (`memory-engine` among them — its runs post-date the photograph commit)
   are EXPECTED in the record's `refused[]` under G6; those refusals appearing is part
   of the acceptance, not a failure.

## v3.0.42 → v3.0.43 (the egress ask stops taxing local one-liners)

One hook + its docs, no **[your call]** entries — an operator-ratified tuning of the ASK
tier (backlog v3.0-124, ratified 2026-08-18: prompt fatigue on provably-local interpreter
one-liners trains a reflexive Allow, which erodes the tier it was meant to strengthen).

1. **Re-copy the hook** (shell copy, upstream-authored bytes):
   `core/security/hooks/block-dangerous-bash.sh`. Inline `py/python/python3 -c` and
   `node -e` now ASK only when the command carries a network-shaped token (`urllib`,
   `requests`, `socket`, `http(s)`, `fetch`, `axios`, `require(net|http|tls|dgram)`, …,
   matched quote-normalized); a local one-liner (`py -c "import json; …"`) passes
   silently. Named tools (`curl`/`wget`/`nc`/PowerShell cmdlets) ask exactly as before;
   the DENY tier is byte-untouched. Honest boundary: this NARROWS ask coverage — a
   token-absent egress spelling that previously prompted now passes; that regression
   IS the ratified trade (prompt fatigue was training a reflexive Allow), and the
   token list is a floor of common spellings, not an enumeration.
2. **Fixtures:** copy the two new passing fixtures
   `core/security/hooks/test-inputs/test-py-c-local-passing.json` and
   `test-node-e-local-passing.json` (the ASK-direction fixtures `test-py-c.json` /
   `test-node-e.json` already carry network tokens and are unchanged).
3. **Verify the copy in one command** (new in v3.0.43): `bash
   core/security/hooks/block-dangerous-bash.sh --self-test` → expect
   `84 passed, 0 failed`. The driver is embedded in the hook (the sibling pattern to
   `scan-staged-secrets.sh --self-test`) and is intercepted before the stdin read, so
   the PreToolUse path is byte-unaffected. A count below 84 means the fixture copy in
   step 2 is incomplete.
4. **Restart Claude Code** after copying — hooks load at session start.

## v3.0.41 → v3.0.42 (the class-hunt six: census exit for the accept door, the scanner actually installed, honest clocks and lit registers)

The 2026-08-17 pre-cutover defect-class hunt's fix-first set. No **[your call]** entries;
every change is a repair of something already ratified. **This is the fleet fan-out
basis — adopt this one even if you skip the window above.**

1. **Engine census (v3.0-111):** re-copy `deploy/staleness.py`. The union-leg ACCEPT
   door (v3.0.39) now exits the conservation census: an operator union set-aside closes
   the pending pair on every covered view. Before this, accepting left the event
   pending forever and census green was unreachable. Self-test 117/117.
2. **The scanner, actually installed (v3.0-112):** re-copy `deploy`-adjacent perimeter
   files `core/security/hooks/scan-staged-secrets.sh` (battery 47/47 — the update flow
   is now pinned: reinstall the hook BEFORE committing a scanner update) and the
   `/doctor` skill (new `precommit-scanner` check: WARNs when the installed hook is
   absent or stale — run it now; most instances that instantiated before this release
   will WARN, and the FIX line is one `cp`). Init now runs `git init` itself when
   needed so a fresh instance's FIRST commit is scanned; `core/onboarding/UPDATING.md`
   gains the reinstall-on-update section.
3. **Honest clocks (v3.0-116a):** re-copy `deploy/check-triggers.py` — `file_age` now
   reads the newest commit date touching the path (mtime only outside git, and it says
   so), so a clone/checkout no longer silently resets freshness triggers. Self-test
   25/25.
4. **Registers lit (v3.0-115):** copy `deploy/deadline-register.yaml.example` →
   `deploy/deadline-register.yaml` and `deploy/verify-routing-register.yaml.example` →
   `deploy/verify-routing-register.yaml` if you don't have live ones (init now does
   this for fresh instances). The deadline scaffold reports honestly empty until you
   add clocks; the verify-routing rows are instance-generic and work verbatim.
5. **The briefing validators get their seed (v3.0-114):** copy the template's
   `capabilities/knowledge-os/extracted/manifests/` to `manifests/` at your root (init
   now wires it). Until you do, `check-briefing-format --self-test` is an honest NOTE
   no-op; after, its 30-row exemplar/defect battery actually runs.
6. **Windows note (v3.0-113):** if your instance was created by `init.ps1`, check
   `project.yaml` for mojibake (`â€"` where an em-dash should be) — the stamping block
   read the file in the wrong encoding on every fresh ps1 instantiation. Fix is a
   hand-repair of the garbled characters (compare `project.yaml.example`); the
   installer is fixed going forward.

## v3.0.40 → v3.0.41 (the scanner's own file is scanned after all — known-own-lines)

One file, no **[your call]** entries — a perimeter re-tightening found by the pre-cutover
stacked-window review (backlog v3.0-109) and fixed the same day on the operator's word.

1. **Re-copy the updated scanner and reinstall it** (shell copy):
   `cp <template>/core/security/hooks/scan-staged-secrets.sh core/security/hooks/` then
   `cp core/security/hooks/scan-staged-secrets.sh .git/hooks/pre-commit` (re-chain if
   chained). Run `bash core/security/hooks/scan-staged-secrets.sh --self-test` — expect
   45/45.
2. **What changed:** v3.0.38 skipped the scanner's own file entirely, reasoning the
   hooks write-guard protected it — but that guard doesn't see shell-redirection writes
   (its documented limit), so a tampered scanner file could carry a secret into a commit
   unscanned. Now the file is scanned with the **known-own-lines rule**: lines that
   exist verbatim in your installed hook pass as the scanner's own content; anything
   else — an appended secret, an embedded line — is scanned normally. Adoption commits
   still pass (v3.0-104 stays fixed). One rare edge: a FUTURE scanner update that adds
   new detection patterns may self-block once at commit; the one-time operator
   `git commit --no-verify` is the sanctioned path, same as ever.
3. **The bigger fix riding this recipe (v3.0-110):** since v3.0.36 the scanner only ever
   scanned NEWLY ADDED files — a secret pasted into an already-tracked file was never
   inspected (the enumeration asked git for added/copied/renamed paths only). Found by
   this release's end-to-end evidence, fixed by including modified files. This is the
   accident-shaped hole (pasting a key into an existing config or doc), so adopt this
   recipe promptly even if you skip everything else in the window.

## v3.0.39 → v3.0.40 (housekeeping: /audit's honest empty mode, the manifest question, the live trigger register)

Three closed promises, one **[your call]** entry. All copies are drop-over.

1. **Re-copy the /audit skill** (`.claude/skills/audit/` from the template's
   `capabilities/knowledge-os/extracted/audit/`, rendered): Step 0's schema citation now
   points at the real §5 heading, and Step 2 gains the no-table mode — a roadmap with no
   assumption tables gets an honest inventory receipt (satisfying the audit cadence)
   plus a decision-inbox item to mint tables, instead of "nothing to grade" against a
   live overdue flag (v3.0-102(a)/(c)).
2. **Re-copy the /preflight and /flight-plan skills** (`core/skills/preflight/`,
   `core/skills/flight-plan/` rendered): both now carry the mandatory manifest question
   at build-increment planning ("which manifest layers does this increment touch, and do
   they exist?") with the doctrine's own on-touch rule quoted — asked-and-answered in
   /preflight satisfies /flight-plan (v3.0-101(a)). Roadmap-authoring guidance
   (`docs/recipes/kickoff-orchestration/roadmap.RECIPE.md`) now mints the §5 assumption
   table at article birth, starter row minimum (v3.0-102(b)).
3. **[your call] Instantiate the live trigger register** (v3.0-101(b) — init now does
   this for fresh projects; adopting instances decide it deliberately because it changes
   what the next sweep reports): `cp deploy/trigger-register.yaml.example
   deploy/trigger-register.yaml` (skip if you already have one — never overwrite). Rows
   are propose-only by contract: a live register arms nothing, it only lets the
   trigger sensor report instead of degrading with "no register, nothing to report."
4. **No action:** the dev-side mirror-publish driver (v3.0-96(b)) is maintainer tooling
   and never ships.

## v3.0.33 → v3.0.34 (housekeeping + the trajectory guard)

Small, all opt-in, nothing changes behavior on its own. No **[your call]** entries.

1. **If you run the knowledge-os engine:** copy the new
   `deploy/register-candidates.py` (its self-test was date-dependent and began
   failing everywhere on 2026-08-05 — your `/doctor` python-sensors line clears),
   `deploy/check-manifest.py` (sha256 pins gain dev-layout resolution; no
   behavior change on an instance), and the compile skill (one added sentence in
   the engine-absent degradation path). All three are drop-over copies.
2. **If you adopted the trigger register (v3.0.15):** add the new
   `verifier-demotion-review` row from `deploy/trigger-register.yaml.example`
   to your live register — it watches the verifier demotion's standing
   30/60/90-day review (v3.0.32's one sanctioned loosening) instead of leaving
   it as a prose promise. First firing walks you through the reading; recording
   a dated `demotion-review` receipt re-arms it.
3. **Nothing else travels.** The trajectory-replay battery (v3.0-91) is
   dev-only by design and is refused from the release artifact; the
   MAINTENANCE.md mirror ritual is a dev-repo doc.

## v3.0.32 → v3.0.33 (the egress hook learns to ask)

Every instance with the security hooks wired. **One [your call] entry, and it is the whole
release:**

1. **[your call] Adopt the two-tier hook.** Network commands (curl/wget/nc, the PowerShell
   web cmdlets, inline `python -c`/`node -e`) stop being hard-blocked and instead **ask you**
   — you see the exact command and click allow or decline, per call. Unattended runs have
   nobody to answer, so they stay exactly as protected as today (an unanswered ask fails
   closed). Destructive commands stay hard-blocked, unchanged. This is a loosening of the
   attended-session egress posture from "impossible" to "your one click away" — if you'd
   rather keep the hard block, skip this section.
2. **Copy the new files:** `core/security/hooks/block-dangerous-bash.sh` over your instance's
   copy (wherever your `settings.local.json` points), and the hooks `README.md`. No wiring
   changes — matchers, settings entries, and `/doctor` check 7 are untouched.
3. **Optional, per workflow:** create `egress-allowlist.txt` beside the hook with one extended
   regex per line for destinations you've decided are standing-approved (e.g. a line matching
   your `curl -s https://api.yourservice.com/` probes). Matching commands run without asking.
   **You edit this file, never a session** — treat it like `credential-bindings.yaml`.
4. **Restart Claude Code** — hooks load at session start.

## v3.0.31 → v3.0.32 (the verifier demotion)

Knowledge-os instances only. **One [your call] entry, and it is the whole release:**

1. **[your call] Adopt the demotion.** This release loosens exactly one thing, deliberately:
   a second-vendor rejection whose ONLY complaint is completeness ("the article may say too
   little" — `scope-omission` / `enumeration-incomplete`) stops blocking the run and becomes
   a recorded signal in your DECISIONS-PENDING inbox, answered at your pace (*redo* or
   *accept*). Everything falsity-shaped (`fabrication` / `contradiction` / `over-certainty`),
   every unclassifiable verdict, and every pre-adoption record keeps blocking exactly as
   today. If you say no, skip this whole section; nothing else in the release matters to you.
2. **Take the updated surfaces:** `deploy/compile-v2.py`, `deploy/compile-driver.py`,
   `deploy/decision-inbox.py`, the compile skill `SKILL.md` (exit table, Step 3c, Step 12),
   `docs/engine/OPERATIONS.md` §7 (the state table + the blocking/recorded split), and the
   decision-inbox `format-MANIFEST.md` (amendment A3 — the inbox marker scan now carries the
   full item line; no identity/age churn).
3. **Nothing to run against your journal.** Pre-adoption verify records read as blocking-class
   under the new lifecycle — byte-identical to what they meant when written; nothing is
   rewritten or backfilled (the journal is append-only, and classes are never re-derived from
   verdict artifacts). The first recorded-class verdict AFTER adoption is the first behavior
   change you will see.
4. **Calendar the demotion's own review** (30/60/90 days from adoption):
   `py deploy/compile-driver.py --verify-ledger --root . --since <adoption date>` — the
   summary states its own reading (mostly *accepted* → the demotion was right; mostly
   *redone* → tell a session to re-promote the class).

## v3.0.30 → v3.0.31 (the subtraction pass)

Removal-heavy and safe to adopt in one sitting; nothing here changes gates, verdicts, or
schema. Per-surface as always.

1. **Delete your stale root `RELEASE` file [one command].** It was consumed into
   `project.yaml.template_release` the day you instantiated (or should have been); the copy at
   your root has been frozen at that day's tag ever since and nothing reads it (backlog
   v3.0-76). New instances never see one — init now consumes it like VERSION, and
   init-validate refuses a leftover.
2. **If your `deploy/` carries the desk scripts** (`desk-metrics.py`, `gen-desk.py`,
   `empire-desk.py`) **or the harness-dev drills** (check-eco2/golden/journal-sidecar/
   origin-propagation/phase-gate, drill-concurrency/crash-absorb/formatter/lock-common-dir/
   planted-defects/r1-acceptance/stage-only/workload-bench) **or
   `dormant-register.yaml`:** delete them. The desk was removed from the template outright
   (operator decision — never used anywhere); the drills are harness-dev tools that no longer
   ship; the register's only job was excusing them. None of these is load-bearing on any
   instance. KEEP `check-split.py` and `audit-content.py` — both still ship (the first is the
   split-acceptance gate, the second is a library v2 imports).
3. **Take the updated skills + sensors:** `doctor.py` + doctor `SKILL.md` (check 12 loses the
   register machinery; self-test 62/62), `sweep/SKILL.md` (scheduled-run recipe shrinks to the
   briefing save), `orient/SKILL.md`, the compile skill (Step 4 gains the split-acceptance
   step wiring `check-split.py`), `check-reference-integrity.py`, the sweep-briefing
   `format-MANIFEST.md` (amendment A3, re-hashed pin), `wiki-schema` §sensors, GLOSSARY, TOUR,
   ARCHITECTURE (standing refresh step).
4. **Backlog convention (optional but recommended):** adopt the closure-convention section of
   `harness-backlog.md` and add a `- **Status:** OPEN` / `CLOSED <date> — <disposition>` line
   to your own entries; your numbering and existing text are untouched.

Instances that skip this release keep working and merely keep carrying dead files.

## v3.0.29 → v3.0.30 (the re-ride survives a project that kept working)

Knowledge-os instances only; no **[your call]** entries. **If you have not yet adopted
v3.0.29, take that recipe below and this one together — this release fixes a blocking
defect in v3.0.29's own re-ride step, so adopting .29 without .30 walks you into it.**

1. **Re-copy two files** and confirm each `--self-test` reports **PASS**:
   `deploy/compile-driver.py` and `deploy/check-derivation.py`. (176 and 19 at this
   template's HEAD — informational, not targets; see the note in the v3.0.29 recipe.)
2. **Nothing else to do.** No data migration, no re-run, no re-verification. If you have
   already adopted v3.0.29 and re-ridden successfully, you are unaffected; this only
   changes what happens when a `--revert` would have collided.
3. **What changed for you:** `--revert` now checks, before writing anything, whether the
   run you are reverting is still the last word on its own articles. If ordinary work has
   modified them since, it refuses cleanly and tells you to correct forward instead —
   rather than conflicting, journaling a failure, and blocking every future compile. And
   the derivation-region check no longer flags hand-written flight plans.

## v3.0.28 → v3.0.29 (plan-scoped verify, and the verify chain's last link)

Knowledge-os instances only; no **[your call]** entries — everything here is session work,
and nothing changes what runs unattended. After adoption, a compile plan that deliberately
splits a wide source across articles DECLARES the split (`claim_routing` in `plan.json`),
the second-vendor checker grades each article against its declared scope instead of the
whole source, rejected-run re-rides stop reading updates as created-from-nothing, and —
the one every knowledge-os instance needs regardless of routing — articles created by the
engine can finally RECORD the checker's approval instead of having it discarded.

1. **Re-copy the engine trio** into your `deploy/` and confirm each `--self-test`
   reports **PASS**: `deploy/compile-v2.py`, `deploy/compile-backends.py`,
   `deploy/compile-driver.py`. (Counts at this template's HEAD are 191, 176 and 176 —
   treat them as informational, not as a target: some cases are conditional on what a
   project has installed, so a project with the cross-vendor bridge present will
   legitimately report a different total. **PASS is the criterion; the number is not.**)
   Behavior for plans without a `claim_routing` block is byte-identical — staged runs and
   re-rides authored before this release run exactly as before.
1b. **Re-copy `deploy/check-derivation.py`** (PASS; 19 at this template's HEAD) — it gains
   the region-presence check described in step 4.
2. **Re-copy the doctrine + skill:** `docs/engine/OPERATIONS.md` (from
   `capabilities/knowledge-os/extracted/engine/OPERATIONS.md` — §6 scoped totality, §7
   two-question charge + the one-plan-defect rule + `--set-aside`),
   `.claude/skills/compile/SKILL.md` (from `compile/SKILL.md.template`, hand-substituting
   `{{...}}`: Step 2 claim routing, Step 2.5 scoped totality, Step 3 per-agent scratch
   paths, Step 3b set-aside wiring, Step 10 pending_cascade citation), and
   `docs/wiki-schema.md` § 7's `pending_cascade.claims_deferred` rows (from
   `wiki-schema.md.template` — the shape's one home).
3. **Re-riding previously rejected runs** (the whole point): the staging dirs your rejected
   runs left behind predate claim routing, so author a FRESH plan per re-ride — same views
   and events, plus the `claim_routing` table the old plan never had.

   **Which route depends on whether the rejected run is still the last word on its
   articles** (found live 2026-08-06, backlog v3.0-73 — the original wording of this step
   assumed it always is, and on a project that simply kept working it is not):
   - *Still the last word* — nothing has touched those articles since. Rewind: `--revert
     --seq <N>`, fresh `emit_packets` + re-stamp, then `--run`.
   - *Moved on* — normal work, a later compile, or an article split has changed them since.
     **Do not revert**; it would undo that later work too. Correct FORWARD instead: take
     each article's current text as the base, fix what the verdict named in a fresh plan
     and staging dir, and `--run` that. The rejection stays on the record as history, which
     is right — it did happen.

   You do not have to work out which case you are in: `--revert` now checks before it
   writes anything and refuses with the forward route named if the articles have moved on.
   If the operator has ALREADY ruled a specific rejection wrong, record it now:
   `py deploy/compile-driver.py --set-aside --root . --seq <N> --view <article> --ruling
   "<their words>"` — the ruling is journaled beside the verdict and the article's verify
   baseline advances as adjudicated.
4. **Mint derivation regions for articles you ALREADY have — do this BEFORE step 3.**
   From this release the engine mints one for every article it creates, but articles
   written before adoption may have none, and an article without one can never record a
   verification: the checker approves it and the approval is discarded for lack of
   anywhere to stamp it. Run `python deploy/check-derivation.py --root .` first — it now
   names any article missing a region. If it lists any, run
   `python deploy/backfill-derivation.py --root .` **on a worktree or branch, never
   directly on a live tree** (that script's own safety rule), review the diff, then merge.
   It is idempotent, skips regenerated INDEX/HEALTH/REVIEW files, validates every mint and
   reverts any file that regresses. Re-riding before this step means spending paid
   cross-vendor legs on approvals the engine will throw away.

## v3.0.27 → v3.0.28 (the prose scan learns what prose is)

One file, no decisions: re-copy `deploy/check-briefing-format.py` (self-test **19/19**).
Clears the standing "the decision list's own machine header trips the plain-language
scan" Watching item every v3.0.27 instance sees — the scan now ignores invisible HTML
comments and keeps the full ban on script names in sentences you can actually read.

## v3.0.26 → v3.0.27 (sensor voice + speed + onboarding)

Every instance takes items 1–2; knowledge-os instances also take item 3.

1. **Re-copy the re-voiced sensors and doctor** (self-tests to expect after copying:
   check-frontmatter **33/33**, check-knowledge-debt **7/7**, check-loop-state **36/36**,
   check-triggers **23/23**, staleness **114/114**, doctor **63/63**,
   reference-integrity **4/4**): `deploy/check-frontmatter.py`, `deploy/check-knowledge-debt.py`,
   `deploy/check-loop-state.py`, `deploy/check-triggers.py` + `deploy/trigger-register.yaml.example`,
   `deploy/staleness.py`, `.claude/skills/doctor/doctor.py` + `.claude/skills/doctor/SKILL.md`,
   `core/governance/check-reference-integrity.py`. Your sensor findings change voice, not
   coverage — every code and count moves to a bracket tail, nothing stops being checked.
2. **Re-copy the re-paced skills and onboarding docs** (hand-substitute `{{...}}` in
   templates): `.claude/skills/sweep/SKILL.md` (one combined citation call; doctor runs
   `--fast-selftests` at session open — full battery stays the default everywhere else),
   `.claude/skills/flight-plan/SKILL.md` (argument check first; bounded receipts read;
   byte-identical briefing writes skipped), `.claude/skills/handoff/SKILL.md` +
   `.claude/skills/handoff-close/SKILL.md` + `.claude/skills/audit/SKILL.md` (narrowed
   re-reads), `.claude/skills/standing-loop/SKILL.md` + `.claude/skills/orient/SKILL.md`,
   `core/onboarding/TOUR.md` + `core/onboarding/GLOSSARY.md`,
   `core/methodology/flight-plan-template-v6.md` + `core/methodology/HOW-TO-USE-FLIGHT-PLAN.md`,
   `core/governance/PROJECT-COMPASS.md.template` (affects future edits only), `INIT.md`,
   `init.sh` + `init.ps1` + `init-validate.sh` + `init-validate.ps1` (message strings only).
3. **Knowledge-os instances:** re-copy `.claude/skills/compile/SKILL.md` (census quoted to
   you via its plain footer; the model-identity question now asks ONCE and records the
   answer at `deploy/evidence/model-identity-attestation.md` — expect one final ask at your
   next compile, then silence) and the five `manifests/**` files whose citations and pins
   this release fixed (`compile-receipt/format-MANIFEST.md` + `MANIFEST-INDEX.md`,
   `sweep-briefing/format-MANIFEST.md` + `MANIFEST-INDEX.md`,
   `decision-inbox/format-MANIFEST.md` + `MANIFEST-INDEX.md`) — after which
   `python deploy/check-manifest.py` should report **0 FAIL** for the first time since July.

## v3.0.25 → v3.0.26 (the decision surface + ownership)

Every instance takes items 1–2; knowledge-os instances also take items 3–4.

1. **Re-copy `deploy/decision-inbox.py`** (run `python deploy/decision-inbox.py --self-test`,
   expect **119/119**), then regenerate once: `python deploy/decision-inbox.py`. Your
   DECISIONS-PENDING.md changes shape — full item prose instead of clipped first sentences,
   no checkboxes (the action channel is saying "yes to" an item in any session), a new
   "Fixes the system can do on your yes" section, and item ages that survive rewording. If
   your instance carries `manifests/decision-inbox/`, re-copy its `format-MANIFEST.md` +
   `MANIFEST-INDEX.md` (amendment A2 rides along and heals that surface's stale source pin).
2. **Re-copy `.claude/skills/doctor/doctor.py`** (self-test **63/63**) and — knowledge-os —
   **`deploy/dormant-register.yaml`** (NEW, ships pre-populated). If you already minted your
   own register rows: MERGE, don't overwrite — keep your rows, add the shipped 14; a row
   duplicated is harmless, a row lost re-opens its warning. If you never made one, the copy
   alone ends the "N unaccounted scripts" wall on every /doctor run.
3. **Knowledge-os instances — re-copy the re-owned skills** (hand-substitute `{{...}}`):
   `.claude/skills/compile/SKILL.md` (new Step 7.6 dashboard reconcile + the ask-once
   syntax-fix exception on raw/), `.claude/skills/audit/SKILL.md` (blocking entries carry
   the DECISION-PENDING marker; unvalidated findings batch per phase),
   `.claude/skills/standing-loop/SKILL.md` (step 5b dashboard reconcile),
   `.claude/skills/sweep/SKILL.md` and `.claude/skills/flight-plan/SKILL.md` (stale-inbox
   to Watching; the preflight cadence row keys on the `preflighted` stamp — your standing
   "/preflight overdue" alarm disappears; that was the fix, not a regression).
4. **Optional but recommended:** re-copy this MIGRATION.md itself — its banner now tells
   future adoptions to run as session work with **[your call]** decision tags.

## v3.0.24 → v3.0.25 (the report contract — plain language to the operator)

Every instance takes items 1–3; knowledge-os instances also take item 4. After adopting,
your sessions' reports change voice, not content: everything technical still lands in
receipts and `(details: ...)` tails — chat just stops carrying it.

1. **Adopt the contract's single home:** re-render `CLAUDE.md` § Session discipline from the
   new `core/governance/CLAUDE.md.template` — it adds § **Reporting to the operator** (the
   one home of the reporting rules; every skill cites it) and one corollary sentence on the
   silence rule (stops must be phrased so a non-engineer can answer). Adopt this even if you
   adopt nothing else in the release.
2. **Re-copy the re-voiced core skills** (hand-substitute `{{...}}` where the file is a
   `.template`): `.claude/skills/sweep/SKILL.md` (renamed check categories, 2–4 sentence
   items, new step 16 prose scan, flight-plan MUST-reuse rule), `.claude/skills/flight-plan/SKILL.md`
   (same-session sweep dedupe; translated sensor findings; the briefing's new leading
   **Waiting on You** section and three-bucket Attention Needed), `.claude/skills/doctor/SKILL.md`
   (translate-then-quote relay rule), `.claude/skills/handoff/SKILL.md` + `.claude/skills/handoff-close/SKILL.md`
   (plain Lock-it preamble + the close-out report), `.claude/skills/cross-check/SKILL.md` +
   `.claude/skills/cross-check-loop/SKILL.md` (plain-words verdict reporting, quoted reasons,
   SHAs to the record).
3. **Re-copy the amended validator + seed:** `deploy/check-briefing-format.py` (run
   `python deploy/check-briefing-format.py --self-test`, expect **17/17**) and, if your
   instance carries `manifests/sweep-briefing/`, its `format-MANIFEST.md` + `source/` seed
   (amendment A2 — re-pinned hashes ride along; your certification status is unaffected).
4. **Knowledge-os instances:** re-copy `.claude/skills/compile/SKILL.md` (Step 1 is
   frontmatter-only now; the driver summary block goes to the receipt, and the new Step 12
   is the operator report — expect your next compile to close with a readable summary
   instead of seq/commit lines), `docs/engine/OPERATIONS.md` (§7 escalation-message
   pointer), and `docs/wiki-schema.md` (§10: SESSION-BRIEFING's leading Waiting-on-You
   section; regenerate SESSION-BRIEFING.md at the next /flight-plan run to pick it up).

## v3.0.23 → v3.0.24 (citation sweep + single-homing + the silence rule)

Every instance takes items 1–3; knowledge-os instances also take item 4.

1. **Re-copy `core/governance/check-reference-integrity.py`** (run
   `python core/governance/check-reference-integrity.py --self-test`, expect **4/4**) and
   `.claude/skills/sweep/SKILL.md`. Then run the new mode once:
   `python core/governance/check-reference-integrity.py --sweep`. Findings are dangling
   citations in YOUR tree — docs citing files that exist in neither the template nor the
   instance layout. Some are inherited from the template (a known baseline the template is
   burning down release by release); a finding in a doc you authored is yours to fix. Never
   silence a finding by deleting the sentence that carries it — fix the citation or log the
   gap.
2. **Re-copy the reconciled docs**: `TEMPLATE-README.md`, `README.md`, `ARCHITECTURE.md`,
   `core/onboarding/TOUR.md`, `core/handoffs/README.md` + the three `HANDOFF-*.md` phase
   docs, `.claude/skills/cross-check-loop/SKILL.md` (hand-substitute `{{...}}`). If your
   rendered `CLAUDE.md` session contract still says "three-session protocol," re-render that
   bullet from the new `core/governance/CLAUDE.md.template` — and add its new **silence
   rule** section (stop-and-ask on any harness silence; every escalation names the
   operator). That section is the audit's whole lesson in one place; adopt it even if you
   adopt nothing else in this release.
3. **Re-copy `.claude/skills/doctor/doctor.py`** (STAMPED_DOCS now watches WORKSPACE.md,
   standing-loop, sweep; self-test **63/63**) — expect up to three new stamp-drift WARNs if
   those docs' stamps trail your `template_version`; that is the check working, refresh the
   docs rather than the stamps.
4. **Knowledge-os instances**: re-copy `.claude/skills/compile/SKILL.md` (Step 10 receipt
   field list reconciled to `docs/wiki-schema.md` §7 — if your receipts carried `duration`,
   canon is `duration_minutes`), `deploy/check-frontmatter.py` (**33/33**; receipts carrying
   `journal_seq`/`run_commit` are now known keys), `deploy/staleness.py` (**114/114**;
   includes the SCHEMA_VERSION parity case), and `docs/wiki-schema.md` (artifact-homes
   section now defers to TEMPLATE-README's layout table — if your /sweep flagged `intake/`
   or `receipts/` as undeclared, this is the fix).

## v3.0.22 → v3.0.23 (emergency batch: honest gates, honest sensors)

Every instance takes the two core skills (item 3's first two files); knowledge-os instances
take everything.

1. **Re-copy the sensor family** — `deploy/`: `staleness.py`, `check-loop-state.py`,
   `check-derivation.py`, `check-frontmatter.py`, `routing-census.py`, `check-split.py`,
   `compile-backends.py`, `compile-driver.py`, `drill-planted-defects.py`,
   `drill-workload-bench.py` (2026-08-08 note: the two drill-* files are harness-dev tools
   and no longer ship — skip them if your release doesn't carry them). Run each
   `--self-test`; expected: staleness **113/113**,
   check-loop-state **36/36**, check-derivation **14/14**, check-frontmatter **33/33**,
   routing-census **19/19**, check-split **20/20**, compile-backends **167/167**,
   compile-driver **156/156**. Then run each sensor live once with `--root .` — an
   INCONCLUSIVE naming your root means the sensor could not LOCATE its subject tree; fix the
   root/invocation, never silence the message.
2. **Exit-code changes** for any local wiring that reads them: check-derivation exits **3**
   (INCONCLUSIVE, wiki/ not located — new, in every mode incl. `--gate`); check-frontmatter
   exits **2** for the same class (incl. `--strict`); routing-census exits **3** for the same
   class and **2** for events named but absent on disk. Re-copy
   `.claude/skills/doctor/doctor.py` — it renders the new derivation code as WARN
   (self-test **63/63**).
3. **Re-copy the skills and docs**: `.claude/skills/standing-loop/SKILL.md`,
   `.claude/skills/handoff/SKILL.md` and `.claude/skills/compile/SKILL.md` (hand-substitute
   any `{{...}}` from your `project.yaml`), `core/handoffs/HANDOFF-AUTHORING.md`,
   `docs/engine/OPERATIONS.md`.
4. **[your call] If your instance ever armed the standing loop** on the strength of a copied,
   inherited, or absent rehearsal receipt: disarm, have the session run the three-round
   rehearsal in the skill on a scratch clone of YOUR project, and mint your own
   `deploy/evidence/rollback-rehearsal-receipt-<date>.md`. The fork's receipt and its
   timings prove nothing about your instance. (The disarm/re-arm is your decision; the
   rehearsal itself is session work.)
5. **Your next wired compile's `stamp_dispatch` call now requires `identity_source`** — a
   staging script copied from the old skill text will refuse with the legal forms printed.
   The compile skill's Step 3a says how to obtain identity per mode; never type it from
   memory.
6. **If a raw file carrying `canonical:` was drawing a check-frontmatter unknown-key WARN**
   (the class Ultrapak hit): re-copying the sensor clears it. The field stays required by
   check-knowledge-debt — deleting it was never the fix.
7. **First `/handoff` after adopting**: if the project has no committed dispatch-grant
   artifact expressly covering handoff bridge legs, the skill now asks its one first-need
   question and mints the grant — that question appearing once is the fix working, not a
   regression to per-send approvals.

## v3.0.21 → v3.0.22 (envelope resolution + no-self-adjudication)

Knowledge-os instances take three files; instances without the capability take nothing.

1. **Re-copy `deploy/check-loop-state.py`** — run `python deploy/check-loop-state.py --self-test`,
   expect **35/35**. Then run it live once (`python deploy/check-loop-state.py --root .`): if your
   instance keeps handoff records at `core/handoffs/` and this sensor previously reported nothing
   or INCONCLUSIVE, this is the fix — it now finds your records. If it reports records in BOTH
   `handoffs/` and `core/handoffs/`, that is a real defect state: consolidate to one envelope
   before trusting any of its results.
2. **Re-copy `.claude/skills/compile/SKILL.md`** (hand-substitute any `{{...}}` variables from
   your `project.yaml`) **and `docs/engine/OPERATIONS.md`** — the no-self-adjudication rule
   (§7): a session never rules a verify rejection a verifier defect; it corrects through the
   correction cycle or stops and escalates to the operator.
3. **If your instance carries verify rejections a session closed on its own authority**
   ("verifier defect," "false positive," or similar), those verdicts are unadjudicated. Re-ride
   each through the correction cycle (`--revert` → corrected staged answers → fresh `--run`), or
   put the verdicts in front of the operator for an explicit recorded ruling.

## v3.0.20 → v3.0.21 (positive credential convention)

Fully additive; four files, no behavior change. Adoption: copy `core/security/CREDENTIALS.md`
(new), and re-copy `core/security/hooks/README.md`, `.claude/skills/orient/SKILL.md`, and
`core/governance/DATA-POLICY.md` (pointer edits only). Then check your instance for the
anti-pattern the doc exists to end: any operator-typed plaintext credential file (gitignored
included) should migrate to `deploy/credential-store.ps1` + an operator-added
`credential-bindings.yaml` line, and an instance backlog entry of the
"credential storage defined only negatively" class can be resolved as fixed-upstream.
Instances without the knowledge-os capability get the doctrine but not the broker scripts —
the doc's "Honest limits" section covers that state.

## v3.0.19 → v3.0.20 (compile correction cycle + isolated authoring)

Knowledge-os instances take five files; others take only the last one.

1. **Re-copy the engine trio** — `deploy/compile-driver.py` (new `--revert` mode; run
   `py deploy/compile-driver.py --self-test` after copying, expect **156/156**),
   `.claude/skills/compile/SKILL.md` (isolated per-view authoring, pre-dispatch self-check,
   5-view batch cap, clone-sync guard, the `--revert` correction cycle), and
   `docs/engine/OPERATIONS.md` (§7/§7a reconciled to the correction cycle).
2. **If your instance holds corrected-but-unverified views from a rejected run** (the state
   the old skill text produced): `--revert` the rejected seq, restore the corrected content
   as staged answers, and re-run `--run` — the corrections finally get real verify verdicts.
3. **All instances: re-copy `init-validate.sh` + `init-validate.ps1`** (core-skill list
   reconciled to the v3.0-78 handoff collapse + the missing `reason` requirement). If your
   adoption of v3.0.17+ turned the validator red while doctor sat green, this is the fix;
   delete any local patch of the same lines in favor of the upstream files.

One file, one decision — **[your call]**. The shipped permission baseline no longer denies `git push`
(operator ruling, backlog v3.0-90 — deny is for the unrecoverable or secret only).
Existing instances: `.claude/settings.local.json` is untracked and per-machine, so no
pull delivers this — delete the two lines `"Bash(git push *)",` and
`"PowerShell(git push *)",` from the `deny` list on each machine where you want
frictionless pushes. Instances running an ARMED standing loop should make that choice
deliberately at the arming review (push will run with no prompt at all once removed,
because `git *` is allowlisted). Everything else in the deny list stays.

## v3.0.17 → v3.0.18 (bare-harness scrub + multi-corpus + backfill route)

Fully additive; no skill retirements, no init-script changes. Adoption:

1. **Multi-corpus instances only (v3.0-88):** if your execution plane spans more than one
   sibling repo, convert `project.yaml` to the `corpus_sources` list form (per-repo `id`,
   `source`, `clone_path`, `branch`, optional `probe`) and set `corpus_source: none` —
   declaring both forms is a config error every consumer refuses. Single-repo and
   `none` instances change **nothing** (the singular form stays valid; back-compat is
   self-tested). Take the updated `/flight-plan`, `/compile`, `/audit` skill texts and
   `docs/wiki-schema.md` § 16 with the swap; `wiki/HEALTH.md`'s per-corpus id-keyed rows
   are seeded by the next `/compile` (first-observation pass per corpus), never by hand.
2. **Take doctor** (`core/skills/doctor/doctor.py` → `.claude/skills/doctor/doctor.py`):
   new check 14 `corpus-reachability` — every declared corpus readable at its clone_path,
   one result per corpus, unreachable = FAIL. Inert (SKIP) for instances with no binding.
3. **Knowledge-os instances: take `deploy/backfill-derivation.py`** with the engine
   cluster. It is the MIG-1 P1 seeding step (`drill-migration-p1.py` step 2 — previously a
   phantom reference); run it only on a worktree/branch (`--check` first), per the drill.
   Legacy views minted by it are `tier: T1` / `consumed_status: legacy-assumed` — they
   carry the content-audit obligation visibly and become stampable by absorption verifies
   (the v3.0-87 convergence blocker).
4. **Venture-scrub deltas ride the normal file-level adoption diff** — comments/examples
   only; the one rename is under `deploy/test-fixtures/memory-engine/content-audit/`,
   where the venture-named planted-defect fixture became `planted-d1-dropped-clause.yaml`
   (referenced by nothing at runtime; delete the old fixture when you copy the new one).

Instances that skip this release lose nothing functional today — but a multi-repo project
silently under-observes its corpus (the exact v3.0-88 gap), and legacy region-less views
stay permanently un-stampable.

## v3.0.16 → v3.0.17 (the handoff collapse)

Additive machinery plus one deliberate SKILL RETIREMENT. Adoption:

1. **Swap the handoff skill set.** Copy `core/skills/handoff/SKILL.md.template` →
   `.claude/skills/handoff/SKILL.md` and the updated
   `core/skills/handoff-close/SKILL.md.template` → `.claude/skills/handoff-close/SKILL.md`,
   then **DELETE `.claude/skills/handoff-author/` and `.claude/skills/handoff-receive/`** —
   doctor's `skill-drift` check FAILs on either lingering beside `/handoff`. Copy
   `core/skills/bridge/handoff-leg.js` beside your existing bridge files. Before the swap,
   confirm no live handoff sits at `status: open`/`answered` (a halted folder is fine,
   grandfathered); an in-flight one should close under the old flow first or be re-run
   through `/handoff` after.
2. **If skill adapters are wired** (v3.0.16 step 3), re-run
   `python deploy/gen-skill-adapters.py` — expect the adapter count to drop by one (the two
   retirees out, `/handoff` in); doctor check 13 confirms.
3. **Knowledge-os instances: take the sensor updates with the engine cluster** —
   `check-loop-state.py` (the `close: pending` park marker; anything else in that field, or
   pending on a non-answered folder, is a violation), `decision-inbox.py` (parked closes
   render as a no-checkbox FYI section — never a task), the standing-loop SKILL (run-step 2
   retries parked legs), `check-run-diff.py` (owed-verification contract),
   `check-frontmatter.py` (parse-first FLATTEN), `compile-v2.py`/`compile-driver.py`
   (non-confirm leg reasons), and optionally `check-verify-routing.py` + its
   `verify-routing-register.yaml.example` (v3.0-77's router-side backstop) with `/sweep`
   step 15.
4. **Consent check before the first bridge handoff leg fires:** `/handoff` rides the same
   standing dispatch-grant class as the wired compile's verify legs. If your instance's
   grant artifact predates this release, its express scope names compile dispatches only —
   the first `/handoff` occasion should mint the one-sentence extension through the same
   first-contact question (consent is express or it isn't; never per-send approvals).

Instances that skip this release keep the three-skill flow working, but every T1 decision
continues to cost the operator packet couriering, per-send approvals, and a separate close
session — the exact slog this release deletes (T2/T3 = zero touches; T1 = one, the lock).

## v3.0.15 → v3.0.16 (substrate-tier truth + upward sensors)

Additive, with one behavior-relevant doc correction every instance should take. Adoption:

1. **Take the corrected orientation docs** — `AGENTS.md` (tiered substrate bullet) and the
   `CONTEXT.md` `substrate` / `substrate gate` glossary terms. These correct a live-incident
   class: the old unqualified "substrate separation" prose instructed sessions to break
   compliant same-vendor routine verify legs. If your instance's CONTEXT.md predates a
   populated glossary, append the two shipped terms rather than replacing your file.
2. **Take the updated doctor** (`core/skills/doctor/doctor.py` + SKILL.md → your
   `.claude/skills/doctor/`): checks 12 (`sensor-reachability`) and 13 (`skill-adapters`).
   Check 12 will WARN UNACCOUNTED on any deploy script reachable from nothing — that WARN
   is a demand for a decision (wire it, or add a `deploy/dormant-register.yaml` row from the
   shipped `.example`), never an instruction to wire.
3. **Optionally wire skill adapters for non-Claude agents:** copy
   `capabilities/knowledge-os/extracted/deploy/gen-skill-adapters.py` to `deploy/`, run it
   once, commit the generated `.agents/skills/` tree. Codex-family sessions then discover
   repository skills natively; doctor check 13 keeps the adapters honest thereafter.
4. **Review the new permission baseline** (`core/security/settings.local.json.example.template`)
   against your instance's `.claude/settings.local.json`. The old one-Read baseline prompts
   on every routine action, which trains rubber-stamping; the new baseline pre-authorizes
   reversible repo-scoped work and denies by class. Merge deliberately — your deny list may
   be stricter; keep the stricter line wherever they differ.
5. **Take the `check-substrate.py` anti-promotion pin** (self-test 21 → 23 cases) with the
   engine cluster if knowledge-os is enabled.

Instances that skip this release keep working, but inherit the incident class: their docs
still instruct sessions to over-harden routine gates, and their doctor still reports green
on orphaned capability.

## v3.0.14 → v3.0.15 (trigger register)

Additive only. Adoption: copy `capabilities/knowledge-os/extracted/deploy/check-triggers.py`
to `deploy/`, seed `deploy/trigger-register.yaml` from the shipped `.example` (edit rows to
the instance's real cadences), and take the updated sweep skill (step 14 + step-10 retitle).
No schema changes, no migration-relevant breakage; instances that skip it lose nothing they
had before.

## v3.0 → v3.0.14 (wired /compile adoption)

The v3.0.14 patch (`HARNESS-CHANGELOG.md` v3.0.14 entry) replaces the knowledge-os `/compile` skill with its **wired-engine form** (backlog v3.0-65): the v3 memory engine now drives every wiki-article write, and every absorption rides a cross-vendor verify leg with no skip flag. Adopting the new skill is per-surface and does not change `project.yaml.template_version`, but unlike prior patches it carries **prerequisites** — the skill refuses (or degrades, honestly) without them:

1. **The engine cluster in `deploy/`.** If your instance predates the engine extraction, copy `capabilities/knowledge-os/extracted/deploy/` per `core/onboarding/UPDATING.md`'s adoption method — the wired path needs `compile-driver.py`, `compile-backends.py`, `compile-v2.py`, `compile-core.py`, `register-intake.py`, `staleness.py`, `check-run-diff.py` and their library siblings. Verify with `py deploy/compile-driver.py --self-test` (148/148 at this template's HEAD).
2. **The cross-vendor bridge.** The verify legs run through `.claude/skills/bridge/` (`codex-verify-server.js` — resolves a `codex-cli >= 0.144`; the driver's pre-write bridge probe refuses loudly if none resolves, printing the versioned resolution chain).
3. **`docs/engine/`** (wired from `capabilities/knowledge-os/extracted/engine/` at init; older instances copy it in) — the skill and this entry both lean on `OPERATIONS.md`'s driver and HUMAN-GATE contracts.
4. **An operator-minted standing verify-authorization artifact** under `deploy/evidence/` (the HUMAN-GATE pattern: a recorded, quoted "go ahead", never standing memory of one). Without it the driver refuses pre-write; there is no absorb-only mode.
5. **The wiki-schema receipt fields:** take the § 7 hunks from `wiki-schema.md.template` (`journal_seq`, `run_commit`) into your `docs/wiki-schema.md`.

Adopting the skill **without** the engine cluster is a supported *migration state*, not a destination: the skill's "Engine absent" section degrades to the pre-wiring hand-merge flow with `journal_seq: null` receipts and a logged backlog entry. First wired run is worth supervising — the fork's supervised first runs surfaced and fixed v3.0-67 (transport-failure verdicts misclassified as terminal) and v3.0-68 (scrubbed-profile bridge degradation) the same day.

---

*Maintained as part of each release's changelog discipline. If a step here contradicts the changelog, the changelog wins — and that contradiction is a backlog entry.*
