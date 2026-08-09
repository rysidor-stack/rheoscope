---
name: doctor
description: Run the unified environment-readiness sensor for an instantiated project-os-harness project — bridge wiring, node/codex/jq presence and auth, every knowledge-os sensor's --self-test, security-hook wiring, and superseded-skill drift. Use whenever something in the environment feels off, right after any machine or dependency change, at session start if the flight plan lists it as a step, or any time you'd rather get a full readiness report (the readiness check writes nothing) than chase one live failure back to its setup cause.
---

# /doctor

/doctor is the environment truth-teller. It runs every setup-gap check the template
otherwise leaves invisible until a live failure: an unauthenticated codex, a missing `jq`,
security hooks that were never wired, a superseded skill (`grill`) shadowing its successor
(`preflight`), or a knowledge-os sensor silently failing its own `--self-test`. A project can
look fully instantiated in the chat transcript and still be missing any of these — /doctor is
how you find out before you hit the failure live (backlog v3.0-26, W2 of
`harness-v3.0/specs/template-self-truth-and-onboarding-brief-2026-07-10.md`).

## When to use

- Anything in the environment "feels off" — a tool that should work doesn't.
- Right after installing or upgrading node, codex, jq, or python, or after re-running init.
- At the start of a session, if the flight plan lists `/doctor` as a step (init-end + on
  demand is its cadence — never overdue by time, run it when the environment changed).
- Before relying on `/cross-check`, `/cross-check-loop`, or a knowledge-os sensor for the
  first time in a session, if you're not sure the substrate is live.

## How to run it

From the project root (an instantiated project, i.e. post-init):

```
python .claude/skills/doctor/doctor.py
```

`init.sh` / `init.ps1` already run this automatically at the end of instantiation and print
its verdict — a doctor FAIL never fails init, it only surfaces issues to fix before the first
working session. Re-running by hand is always safe: **in normal readiness-check mode doctor
writes nothing**. It does *invoke* each deploy sensor's `--self-test` and
`check-derivation.py --gate`, which are contract-bound to be side-effect-free (embedded
fixtures / report-only scans) — doctor relies on that contract; it cannot independently
guarantee it for a locally modified sensor.

Pass `--root PATH` to check a different tree (e.g. `--root C:\path\to\project` from
elsewhere). `--self-test` runs the sensor's own embedded fixtures and needs no live
node/codex/jq/deploy — use it to sanity-check the sensor itself, not the project. This is
the one mode that writes anything: its fixture files land only inside an auto-cleaned
temporary directory, never the project tree.

## How to interpret results

Every line is `[PASS|FAIL|WARN|SKIP] <check-name>: <detail>`, followed by a one-line summary.
Every FAIL and WARN carries an actionable `FIX:` instruction inline. **Relay every FAIL and
WARN to the operator without dropping or softening any** — but translated, per
`core/governance/CLAUDE.md` § Reporting to the operator: lead with what it means for them and
what stays broken if ignored, then quote the exact `FIX:` line as the how (the quote is the
mechanism; the translation is the message). Never omit a finding, never soften its status —
the old rule here said "verbatim, don't paraphrase" to stop sessions laundering failures
away; the ban was always on OMISSION, not on translation (clarified v3.0.25). A SKIP means the check's
subject is absent by design (e.g. no `deploy/` because the `knowledge-os` capability was
never enabled for this project) — it is not a problem.

A FAIL on `codex-auth` or `bridge-wired`/`bridge-cli` means the cross-vendor substrate
(`/cross-check`, `/cross-check-loop`) is **inert, not broken** — those skills fail loud and
harmlessly if invoked before the prerequisite is fixed; nothing else in the project is
affected. Don't treat a codex/bridge FAIL as a reason to distrust the rest of the report.

Exit code 2 means at least one FAIL occurred; 0 means everything is PASS/SKIP or WARN-only;
1 is reserved for a `--self-test` failure (a bug in the sensor itself, not the environment).

## Check catalog

| # | Check | What it verifies |
|---|-------|-------------------|
| 1 | `bridge-wired` | `.claude/skills/bridge/verify-cli.js` exists |
| 2 | `node` | node on PATH, `node --version` >= 18 |
| 3 | `bridge-cli` | `node verify-cli.js --help` exits 0 (SKIP if #1 or #2 failed) |
| 4 | `codex-auth` | codex on PATH **and** authenticated (`codex login status`) |
| 5 | `jq` | jq on PATH (runtime dependency of the security hook scripts) |
| 6 | `python-sensors` | every `deploy/*.py` advertising `--self-test` passes it |
| 7 | `hooks-wired` | `.claude/settings.local.json` exists, is valid JSON, references both `block-dangerous-bash.sh` and `block-env-writes.sh`, **and** wires `block-dangerous-bash.sh` under both a `Bash` and a `PowerShell` matcher and `block-env-writes.sh` under an `Edit`/`Write` matcher (matcher coverage, not just script presence — see `core/security/hooks/README.md`) |
| 8 | `skill-drift` | superseded-skill probe — today: stale `.claude/skills/grill` alongside its successor `/preflight` |
| 9 | `derivation-gate` | `deploy/check-derivation.py --gate`, if the sensor is present |
| 10 | `docs-stamps` | teaching docs (`TOUR.md`, `GLOSSARY.md`, `SYSTEM-MAP.html`, `docs/engine/OPERATIONS.md`, `.claude/skills/orient/SKILL.md`, `core/methodology/manifest-driven-builds.md`, `core/methodology/manifest-format.md`, `.claude/skills/conformance/SKILL.md`, root `ARCHITECTURE.md` — the last added per backlog v3.0-75, migration having never refreshed it) carry a `verified-against: <VERSION> (<date>)` stamp that matches this instance's `project.yaml` `template_version` |
| 11 | `version-drift` | `deploy/environment-manifest.yaml`, if present: runs each row's `probe` command and compares its output to the row's recorded `version_verified` |
| 12 | `sensor-reachability` | every `deploy/*.py` is reachable from an executable surface (skills, init scripts, `.cmd` wrappers, deploy registers; transitive over deploy→deploy dynamic loads); anything else WARNs UNACCOUNTED — the check demands a disposition, never prescribes wiring (backlog v3.0-80; the dormant register that used to excuse the template's own dev drills was retired 2026-08-08 when those drills stopped shipping) |
| 13 | `skill-adapters` | `deploy/gen-skill-adapters.py --check` — the generated `.agents/skills/` discovery adapters (how Codex and other non-Claude agents find repository skills) are current against `.claude/skills/`; drift WARNs with the regenerate command (backlog v3.0-79). SKIP when the generator isn't wired |
| 14 | `corpus-reachability` | every execution corpus declared in `project.yaml` — the `corpus_sources` list, or the legacy singular `corpus_source` + `corpus_config` — is present and readable at its `clone_path` (`git rev-parse <branch>`, read-only, no credentials). One result per corpus: a declared-but-unreachable corpus **FAILs** (partial observation is never silent); both binding forms declared at once FAILs as a config error; no binding declared → SKIP (v3.0.18, backlog v3.0-88) |

`docs-stamps` enforces the docs-truth discipline (`harness-v3.0/specs/template-self-truth-and-onboarding-brief-2026-07-10.md` §W5): a teaching doc's `verified-against` stamp is advisory truth-hygiene, not a build gate — a missing or stale stamp WARNs with a FIX (add the stamp, or re-verify per `MAINTENANCE.md`) but never fails the run, and a doc absent by design (not every instance ships every candidate) is silently skipped per-file, with a single summary SKIP only if none of the candidates exist at all.

`version-drift` watches the toolchain named in `deploy/environment-manifest.yaml` (schema: `tools: [{tool, probe, version_verified, date}]`, see `deploy/environment-manifest.yaml.example`) for drift since it was last hand-verified — a probe's output changing from `version_verified` WARNs naming both versions and the verified date; a probe that fails or a tool no longer on PATH WARNs "tool unreachable"; no manifest (or no PyYAML to parse it) SKIPs as "version drift unwatched", never FAILs — an unadopted manifest is not a defect (session-C build decision D5 -- a dev-repo design record, not shipped; the decision's content is stated in full here).

## Where /doctor sits

`/doctor` is a core skill (`core/skills/doctor/` → `.claude/skills/doctor/` at init, same as
every other core skill). It has two standing call sites: `init.sh`/`init.ps1` invoke it once
at the very end of instantiation, and `/flight-plan`'s cadence table lists it as an
init-end-plus-on-demand step. Both call sites treat a doctor FAIL as informational, never
fatal — /doctor reports, it doesn't gate.
