# Rheoscope — Operator Manual

A project bootstrapping template for LLM-orchestrated projects, combining a methodology kernel, governance, handoffs, cross-vendor verification, and a security perimeter with opt-in capabilities (knowledge OS, code-conventions) plus docs-only capabilities (kickoff-orchestration, operate-sentinel, decorrelated-review).

## What this is

A reusable scaffold for kicking off a new long-running project that an operator directs Claude Code (or equivalent) to execute on. The harness ships a **core** (methodology, governance, handoffs, security) that every project of this shape needs, plus a catalog of **capabilities** an operator opts into per project via `project.yaml`. Instantiation is a one-shot scripted step (`init.ps1` / `init.sh`) that substitutes per-project values into `*.template` files and wires only the capabilities you enabled.

This harness is designed for **non-coding systems orchestrators** who direct LLM agents on multi-month projects. It assumes the operator has a mature project thesis already — it bootstraps execution, not project formation.

## Who it's for

Operators who already have a mature project thesis. **Not for project formation.** Project formation — the months-of-conversation phase that produces a thesis — requires a different shape; this harness assumes that work is done.

If you can answer the INIT.md Step 2a question ("describe your architecture in one paragraph"), you're ready. If you can't, you're still in formation. Spend more time in conversation first; the harness will wait.

## When NOT to use this harness

Skip this harness if any of the following are true:

- **Solo experiments under 2 weeks.** The setup overhead doesn't amortize.
- **Single-file projects.** A script that fits in one file doesn't need a methodology kernel.
- **Projects without compounding knowledge** — one-shot writing, transient research, processing a fixed dataset once. The wiki/roadmap pipeline has nothing to compound on.
- **Projects in formation.** No mature thesis yet. INIT.md Step 2a will be unanswerable; the kickoff interview will feel wrong because the project hasn't earned a thesis yet. Spend more conversation time first.

The harness has opinions. Some projects benefit from those opinions; some don't. Knowing which you have is on you.

## How instantiation works

1. **Clone this harness** into a directory you trust (see "Audit before instantiation" below). Prefer a **release tag**: an untagged `main` is a development snapshot — the prose version markers (this file's footer, README, ARCHITECTURE) describe the last *shipped* release and may lag the `VERSION` file, and `harness-backlog.md` may carry pending dev entries that are emptied at each release.

   **Windows long-path note.** This harness's deepest shipped path (a test fixture under `capabilities/knowledge-os/`) was shortened at the source (2026-07-25) and is now safe to clone or unpack at any location whose own path is up to roughly 127 characters — comfortably covers a normal profile path, including most corporate/enterprise ones. Only a genuinely pathological destination (deeply nested beyond that) still needs a short path or a directory junction; `init.ps1`'s pre-flight guard checks this for you and fails loud with next steps if your destination is one of those. `init.ps1` also sets `git config core.longpaths true` for the cloned repo (local, git-level only), which covers most git operations; it does not change the OS-level `LongPathsEnabled` registry setting, which stays your own call to make (`HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled`) — this harness never flips it for you. If you do use the directory-junction workaround, re-check `.claude/settings.local.json` once init has run: its Read-allow permission is substituted from the directory you ran init in, so it will carry the junction alias path and should be updated to the real path once the junction is removed (the hook wiring itself is unaffected, since it resolves paths via `$CLAUDE_PROJECT_DIR` at runtime).
2. **Copy** `project.yaml.example` → `project.yaml`.
3. **Edit** `project.yaml`: identity (name, slug, description), capabilities (which opt-in modules you want), personnel (at least one entry), tier examples (customize for your domain).
4. **Run** `init.ps1` (Windows) or `init.sh` (Unix). The script substitutes `{{variables}}` across all `*.template` files, wires the capabilities you enabled into runtime locations, and deletes the `capabilities/` catalog from your project. It asks one authority-mode question (`visible` — reversible-and-visible, no hardware key — or `required` — hardware-key root; `--authority-mode=visible` / `-AuthorityMode visible` skips the prompt; there is no silent default). Along the way it prompts for consent to wire the security hooks — copying `core/security/settings.local.json.example` → `.claude/settings.local.json` (an interactive terminal defaults to Yes on empty input; a non-interactive run wires by default with a loud notice; `--hooks`/`-Hooks` and `--no-hooks`/`-NoHooks` skip the prompt either way; declining leaves the example file in place, unwired). At the very end of the run, init invokes `/doctor` as a readiness check and prints its verdict — this never fails init, it only surfaces issues to fix before your first working session. Init's own final output also prints its recommended next steps — `git init; git config core.longpaths true` *(Windows long-path safety — harmless elsewhere; see the note above)* `; git add -A; git commit` — right here, before you ever open `INIT.md`.
5. **Validate** by running `init-validate.ps1` / `init-validate.sh`. Confirm PASS — any remaining `{{...}}` placeholder, any leftover `*.template` file, or any missing capability runtime path is a hard fail.
6. **Commit** — the interim commit init's own output just recommended. Doing it now, before the kickoff interview, gives committed-doc-dependent sensors (e.g. `check-reference-integrity.py`) a HEAD to check against.
7. **Open** `INIT.md` and walk its kickoff interview in a fresh Claude session — INIT.md itself is the authority for the step list. Record answers; populate skeleton governance docs; seed `CONTEXT.md`; create the first phase articles.
8. **Commit again** — `INIT.md`'s own Commit section covers this second, final commit: the same message, with `— kickoff complete` appended. Begin work.

**New to the harness?** Before or during the kickoff interview, walk `core/onboarding/TOUR.md` (a staged walkthrough) or open `core/onboarding/SYSTEM-MAP.html` (double-click it — a self-contained map). For ad-hoc questions any time, ask `/orient` — it answers by reading the installed docs, citations required.

**Restart Claude Code after init — a new chat is not enough.** Init creates `.claude/skills/`. Claude Code only scans for top-level skill directories that existed when it started: per the Claude Code docs, *"Creating a top-level skills directory that did not exist when the session started requires restarting Claude Code."* Opening a new chat in the same running instance will **not** discover the freshly-wired skills — the entire core set (enumerated in the Core skills row of the file-layout table below) plus, with knowledge-os enabled, `/compile`, `/audit`, and `/discover`. Fully restart Claude Code after step 4, then run the INIT.md kickoff. See https://code.claude.com/docs/en/skills.md.

The whole instantiation is one-shot. Init is **not re-runnable** — it refuses to run on an already-stamped project (the `instantiated_date` field in `project.yaml` is the guard). To re-instantiate, start with a fresh harness clone.

## Audit before instantiation

**Clone this harness into a directory you trust before running init.**

CVE-2025-59536 and CVE-2026-21852 (Check Point disclosures, Feb 2026) cover Claude Code hooks in cloned untrusted repos firing before trust dialogs surface. This harness ships PreToolUse hooks (`core/security/hooks/`) that block network egress and `.env*` writes. Those hooks defend against the kinds of mistakes Claude Code might make during normal use — they do **not** defend against the CVE attack class, where a malicious harness fires its own hooks before you've granted execute permission.

So: review `core/security/hooks/` (a few dozen lines of bash) and the init scripts (`init.ps1`, `init.sh`) before granting execute permission on a fresh machine. The audit takes ~10 minutes. The audit is per-version: once you've reviewed a release's `core/security/hooks/` and init scripts, that review carries until you adopt a newer harness version that changes them.

*These CVEs motivate audit-before-instantiation; they do NOT motivate the runtime behavior of the hooks themselves.*

## Architecture summary

The harness uses a **core + capabilities** model:

- **Core** (always present): methodology kernel, governance (CLAUDE.md, PROJECT-COMPASS, HARDCONSTRAINTS), handoffs (the single-pass `/handoff` decision inquiry — one pass authors, gets a cross-vendor answer, and locks, with `/handoff-close` surviving as the close leg's protocol), security perimeter (PreToolUse hooks), and **core skills** wired to `.claude/skills/` unconditionally. Init wires whatever `core/skills/` ships, by directory glob, so that directory is the single source of the skill list; the Core skills row of the file-layout table below enumerates the current set. Loaded regardless of `project.yaml`.
- **Capabilities** (opt-in): each declared in `project.yaml.capabilities`. Three maturity states: `extracted` (working code lifted from a real project), `prototype` (working code, unvalidated as a generalizable pattern), `deferred` (recipe only, no code). The harness ships extracted code for knowledge-os, stress-testing, and a `code-conventions` prototype.

See `ARCHITECTURE.md` for the full model, the recipe format (10 fields), and the substitution mechanism (`.template` suffix convention, mechanically enforced).

## Drift-governance sensors

The harness ships three self-healing sensors (added in v1.3) that re-derive a spec-vs-reality gap every run, so drift can't be silently forgotten:

- **Reference integrity** (core, always on) — `/flight-plan` Step 5.6 runs `core/governance/check-reference-integrity.py` every session: it flags markdown links in your **committed** governing docs (`core/governance/CLAUDE.md` plus every `governance_docs[].path` in `project.yaml`) that point at files missing from the committed tree. Read-only and best-effort (Python is a soft dependency — if it's absent the briefing continues); the fix is a one-line `git commit`. Runs in build-only projects too.
- **REVIEW auto-compaction** (knowledge-os) — `/compile` Step 6a sweeps terminally-resolved `wiki/REVIEW.md` entries (`APPLIED`/`WONT-FIX`/`SUPERSEDED`/`RESOLVED`/`DUPLICATE`) every run, preserving unresolved and live-deferred entries; swept titles are logged in the compile receipt (`review_compacted`). Inert when knowledge-os is disabled (no `/compile`).

## File layout

Externally-authored skills often assume a flat layout (`CLAUDE.md` at the project root). This harness uses the core + capabilities model, so the canonical paths are:

| Artifact | Canonical path |
|----------|----------------|
| Project schema / session contract | `core/governance/CLAUDE.md` (**not** the repo root) |
| Claude Code orientation | `CLAUDE.md` (repo root — a pointer only, auto-loaded by Claude Code; canonical content stays in `core/governance/`) |
| Non-Claude agent orientation | `AGENTS.md` (repo root — a pointer only; canonical content stays in `core/governance/`) |
| Update check + adoption method | `core/governance/check-template-updates.py` + `core/onboarding/UPDATING.md` (v3.0.12 — "check for updates to the harness" is self-serve; adoption is file-level, never `git pull`) |
| Operator-facing orientation | `core/governance/PROJECT-COMPASS.md` |
| Immutable constraints | `core/governance/HARDCONSTRAINTS.md` |
| Locked-decision index | `docs/governance/DECISIONS.md` |
| Architecture Decision Records | `docs/adr/<YYYY-MM-DD>-<n>-<slug>.md` |
| Methodology kernel | `core/methodology/` |
| Handoff protocol (docs) | `core/handoffs/` |
| Onboarding | `core/onboarding/` — `TOUR.md` (staged walkthrough), `GLOSSARY.md` (human glossary), `SYSTEM-MAP.html` (self-contained system map) |
| Core skills | `.claude/skills/` — `flight-plan`, `handoff` (single-pass decision handoff, v3.0-78), `handoff-close` (its close-leg protocol), `log-backlog`, `preflight`, `reason`, `orient`, `cross-check`, `cross-check-loop`, `doctor`, `conformance`, `sweep`, `standing-loop`, `bridge` (transport library — no slash command). *This row is the enumerating home for the core-skill set — every other doc points here; init derives the actual wiring from the `core/skills/` directory glob* |
| Capability skills (if enabled) | `.claude/skills/` — `compile`, `audit`, `discover` (knowledge-os) |
| Canonical glossary | `CONTEXT.md` (repo root) |
| Wiki schema (knowledge-os) | `docs/wiki-schema.md` |
| Engine docs (knowledge-os) | `docs/engine/` — fork-proven v3 memory-engine specs (registration chain, compile pipeline, conservation census); the deploy layer's Python (`deploy/compile-v2.py`, `deploy/staleness.py`, `deploy/register-intake.py`, `deploy/entities.yaml`) is the executable counterpart |
| Compiled knowledge | `wiki/<domain>/` |
| Raw intake | `raw/` |
| Roadmap | `roadmap/` |
| Reference material | `references/` |
| Behavioral manifests | `manifests/<surface>/` — one file per touched registry layer plus `MANIFEST-INDEX.md`; written by manifest extraction, read by the build gate (`core/methodology/manifest-format.md`) |
| Handoff records (instance) | `handoffs/` — the project's decision-handoff folders (`<YYYY-MM-DD>-<slug>/`), written by `/handoff`; the protocol docs stay in `core/handoffs/` |
| Run receipts *(knowledge-os only)* | `receipts/` — engine-written run state: chained journal, registration chain, cross-vendor VERIFY artifacts, discover receipts (`docs/wiki-schema.md` § 17.4), plus human-authored receipt files |
| Candidate staging *(knowledge-os / R-1 only)* | `intake/` — the R-1 candidate pipeline's staging area; `deploy/candidates.py` writes harvested session-authored spans to `intake/session-candidates/` to await the signed promotion gate |
| Capability recipes (deferred) | `docs/recipes/<capability>/` |

When adapting an external skill that references "CLAUDE.md at the project root," repoint it at `core/governance/CLAUDE.md`. The directory-preservation and single-writer rules documented there apply to all skills.

## Dependencies

Required:

- **Claude Code.** The skills under `.claude/skills/` and the hooks under `core/security/hooks/` are Claude Code mechanisms.
- **PowerShell 5.1+** (Windows) or **bash 4+** (Unix).
- **powershell-yaml module** (Windows). Install with: `Install-Module powershell-yaml -Scope CurrentUser -Force`
- **yq** — Mike Farah's Go version (Unix). Install with: `brew install yq` (macOS) or your distro's equivalent.
- **jq** — required by the PreToolUse security hooks under `core/security/hooks/` to parse Claude Code's JSON stdin payload. Install with: `winget install jqlang.jq` (Windows), `brew install jq` (macOS), or your distro's equivalent. Both hooks fail closed without jq, so install before first session.
- **Git.** All commits are operator-driven; no auto-push.
- **perl** and **base64** (Unix). Ship with macOS and every Linux distro by default.
- **python 3** — required by the knowledge-os deploy sensors and by the `/doctor` readiness sensor. Install from your platform's usual source.

Runtime-only (installed-but-inert without them — init places the skills regardless):

- **node >= 18** and the **codex CLI** — runtime prerequisites of the `/cross-check` and `/cross-check-loop` core skills (they call the `bridge` transport out to a cross-vendor verifier). The skills are wired unconditionally at init but fail loud if invoked before these are present. `codex` requires a ChatGPT subscription and a one-time `codex login`.

## Troubleshooting

**PowerShell execution policy is Restricted by default on Windows.** First-time operators need to run, once, in a normal user PowerShell window:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

This allows local, signed scripts (which our init scripts are not — they're local and unsigned, so `-ExecutionPolicy Bypass` on the invocation is the alternative).

**`powershell-yaml` missing.** PowerShell 5.1 and PowerShell 7 (`pwsh`) keep modules in different directories (`~\Documents\WindowsPowerShell\Modules\` vs `~\Documents\PowerShell\Modules\`). Install from the same shell you intend to run init from. Most operators want it under Windows PowerShell 5.1:

```powershell
Install-Module powershell-yaml -Scope CurrentUser -Force
```

If `Install-Module` errors with TLS handshake failures, enable TLS 1.2 first:

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
```

**Cross-user-home paths.** If the harness is cloned into one Windows user's home and references files in another's, default Windows ACLs block reads. Copy the source files into the same user as the build before running init. The harness assumes single-user layout.

**Init refuses to run because the project is already instantiated.** That's intentional. Init is one-shot. To start over, clone a fresh harness; do not try to coerce a re-run. The check is `project.yaml.instantiated_date` being non-empty.

**Substitution errored: `unknown {{variable}}`.** Either: (a) a `*.template` file references a variable that isn't in the Substitution Dictionary Contract (a harness bug — file an issue), or (b) you authored your own `*.template` and forgot to add the variable. Run `init.ps1 --dry-run` first to surface these without modifying state.

## Why no upgrade path in v1.0

This harness instantiates as a **one-way fork**. Once you've run init, your project is yours; the harness can evolve independently. There is no `/sync-capability` orchestrator in v1.0 that pulls upstream harness improvements into an instantiated project.

The decision: **manual sync is fine for 2–3 active projects; automated sync becomes worth building at ≥3.** Until that threshold is reached, the design effort to author `/sync-capability` correctly is harder to justify than the manual diff-and-merge effort. When ≥3 projects are running v1.x harnesses, the deferred `/sync-capability` recipe (under `capabilities/` in the harness repo) gets authored from validated practice and shipped as v1.x.

If you need an upgrade story now, you have two options: (a) manually diff your instantiated project against the latest harness clone and copy what you want, or (b) fork the harness, apply your customizations, and treat your fork as the upgrade source for your projects.

**As of v2.0 there is a written manual recipe for option (a):** see `MIGRATION.md` at the harness repo root. It walks a v1.x project through the v2.0 changes surface-by-surface and lists the moves that cannot be reversed. The automated path remains deferred (revisit at ≥3 instantiated projects, per `adr/2026-06-09-2-v2.0-versioning-and-migration.md`).

---

*Template version: 3.0 (2026-07-24)*

*(Template maintainers — not instance operators: the docs-truth discipline that keeps this version stamp honest lives in `MAINTENANCE.md` at the harness repo root.)*
