# MIGRATION.md — manual upgrade recipes for instantiated projects

*The harness instantiates as a one-way fork (see `TEMPLATE-README.md` § Why no upgrade path). This file is the manual recipe for operators who want to pull a newer harness's changes into an already-instantiated project. It is **per-surface and opt-in** — adopt what you need; nothing here re-runs init (init is one-shot by contract). Automated migration is deferred: revisit at ≥3 instantiated projects (ADR #1 reopen trigger 5; `adr/2026-06-09-2-v2.0-versioning-and-migration.md`).*

*General method for any version step: clone the new harness next to your project, `diff -r` the surfaces named in its changelog entry, and copy what you adopt — substituting your project's values by hand where the template uses `{{...}}` variables (your values are all in your `project.yaml`).*

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

1. **R-1 — the session-loop intake-and-promotion pipeline** (closes backlog `v3.0-21`'s "never built" residual). Copy `candidates.py`, `harvest-candidates.py`, `register-candidates.py`, `check-candidates.py`, `promote-candidate.py`, `signing-config.yaml.example` into your `deploy/`, and fold the `decision-inbox.py` candidate section + the `assemble.py`/`origin.py`/`registrations.py`/`tool_grant.py` deltas from the same commits (template `9146c10`, `ae52693`, `b433d4b`, `9071df4`). Ships **UNARMED by design** — no fingerprint is pinned in `signing-config.yaml.example`; promotion to an operator-authored event refuses until *you* generate a dedicated SSH signing key in your own hands and pin its `SHA256:` fingerprint (do this only when you're actually ready to run the pipeline — the absence is the fail-safe, same doctrine as `origin-config`). Run `python deploy/drill-r1-acceptance.py --self-test` to confirm (70/70 at this template's HEAD) and wire the census step into your `/sweep`.
2. **Credential broker.** Copy `credential-store.ps1`, `credential-use.ps1`, `credential-remove.ps1`, `credential-bindings.yaml.example` into your `deploy/`; add the broker entries to your `safe-allowlist.yaml` (credential-class). Bindings start empty (fail-safe) — add one binding per credential name + destination as you adopt each. Run `credential-selftest.ps1` to confirm (24/24 at this template's HEAD, incl. the redirect-refusal and universal leak-scan cases). Windows-only (Windows Credential Manager via P/Invoke); no recipe exists yet for other platforms.
3. **Workspace governance.** Copy `core/governance/WORKSPACE.md.template` (instantiate with your own zone names if you don't use the four defaults) + `core/governance/projects.yaml.example` (the workspace registry — also the empire-desk rollup's input, see item 6) + `deploy/check-workspace.py` into your project; wire it as a `/sweep` step (this template runs it as step 9). Report-only by design — it never deletes; adoption on a live machine starts with a classify-everything reap report, never a machine reorganization.
4. **Deadline-and-trigger register.** Copy `deploy/check-deadlines.py` + `deadline-register.yaml.example`; seed the register with your own project's real clocks (cert rotations, token expiries, if-triggers) rather than carrying the fork's example rows across. Wire as a `/sweep` step (step 10 here).
5. **Environment manifest + doctor version-drift.** Copy `environment-manifest.yaml.example` into your project and seed it with your own toolchain's live-probed versions; `/doctor`'s `check_version_drift` (already core, no copy needed if you're on this template's `/doctor`) picks it up automatically and WARNs on drift, never fails.
6. **Mirror/instance parity.** Copy `deploy/check-parity.py` and wire it as a `/sweep` step (step 11 here) **only if** your project maintains a fork/mirror relationship to a template or another instance — it SHA-256-compares your engine mirror + skill inventory against that source. Not applicable to a project with no such relationship.
7. **Desk enrichment + the empire desk.** Copy `deploy/desk-metrics.py` (append-only per-run metrics history — appends belong to WRITE-SIDE sessions only; `/sweep` itself stays read-only) + the `decision-inbox.py` first-seen-age sidecar logic + `deploy/gen-desk.py` (static `DESK.html`, actuator-free by design — never wire a form, button, or script into it). If you run ≥2 harness-instantiated projects sharing one `projects.yaml` registry (item 3), also copy `deploy/empire-desk.py` to generate one cross-project `EMPIRE-DESK.md` rollup; it is a read-only projection over each project's own `DECISIONS-PENDING.md` + `SWEEP-BRIEFING.md`, never a daemon.
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
