# Glossary

*The terms a stranger hits in the first week of operating this harness, one tight definition
each, alphabetical. This is a human glossary about the harness itself — not `CONTEXT.md`, the
project's own canonical glossary for project-specific terms (Pocock format,
`CONTEXT.md.template` at the project root; post-init path: `CONTEXT.md`). Where a term has a `CONTEXT.md` entry too, this page
explains the mechanism; `CONTEXT.md` records this project's specific usage. Paths under
`docs/`, `deploy/`, `receipts/`, `raw/`, `wiki/`, `roadmap/`, and `.claude/skills/` exist only
after `init.ps1`/`init.sh` has run — not marked per entry below, since this whole file describes
runtime, not the raw template clone. Likewise, a `*.template` citation names a pre-init source
file that does not survive init — the suffix drops and the substituted file takes its place at
the same path; entries below mark this via "(post-init path: ...)" where the substitution isn't
already obvious from context.*

*verified-against: 3.0 (2026-07-24)*

**Backlog** — `harness-backlog.md`: the append-only log of issues/gaps that are properties of the
harness template itself, not this project's content, numbered `v<template_version>-N` and
logged via `/log-backlog`. See `core/governance/CLAUDE.md.template` § Session discipline →
Backlog logging (post-init path: `core/governance/CLAUDE.md`).

**Behavioral manifest** — a per-layer enumerated behavioral contract (rows: id, replay path,
exact expected observable) that a build must discharge and a verifier can replay; lives in
`manifests/<surface>/`. The build gate reads its surface's MANIFEST-INDEX. Not to be confused
with the dispatch manifest or result manifest (see those entries). Doctrine:
`manifest-driven-builds.md`; contract: `manifest-format.md`.

**Bridge** — the shared transport library (`verify-cli.js` + two contained verifier servers +
`repo-grounding.js`) that `/cross-check`, `/cross-check-loop`, and the memory engine's VERIFY leg
all call to reach a substrate-different model. No slash command of its own. See
`core/skills/bridge/README.md`.

**Capability vs. core skill** — **core** ships unconditionally, no toggle; a **capability** is
opt-in via `project.yaml.capabilities` and can be entirely absent from a project that never
enabled it. `/flight-plan`, `/preflight`, `/doctor`, the handoff skills, `/log-backlog`,
`/cross-check`, `/cross-check-loop`, and `bridge` are core, not capabilities. See
`ARCHITECTURE.md` § Overview and `capabilities/INDEX.md` § "Note on core skills" (pre-init
path — `capabilities/` is deleted once init consumes it).

**Census** — `deploy/staleness.py`'s conservation check: sorts every registered ledger event
into exactly one of seven ordered classes and fails loud (`problems`/`new_holes`) the moment the
accounting doesn't add up. "Census green" is the memory engine's bar for trustworthy. See
`docs/engine/OPERATIONS.md` § 8 "Census green check".

**Compile loop** — the full register → route → compile (absorb) → verify → census sequence a
raw event travels to become trustworthy wiki content. See `docs/engine/OPERATIONS.md` § "The
loop".

**Conformance sweep** — `/conformance`: replaying behavioral-manifest rows against the live
build; smoke tier on cadence, full tier at freezes/certification; receipts
`<surface>-conformance-bless-rN`; red rows classify as declared vs undeclared nonconformance via
the amendment log. See `core/skills/conformance/SKILL.md` and
`core/methodology/manifest-format.md` § 12.

**Credential broker** — the three-script Windows-credential toolset (`credential-store.ps1`,
`credential-use.ps1`, `credential-remove.ps1`) that keeps secrets in the OS-native Credential
Manager instead of a file: the operator's hands raise the popup to write a value, a session can
fetch-and-inject it by name into a destination, but the value itself never touches
stdout/stderr/logs/transcript. Broker tools are classified credential-class in
`safe-allowlist.yaml`. See `capabilities/knowledge-os/extracted/deploy/credential-store.ps1`.

**Decision inbox** — `deploy/decision-inbox.py`'s `DECISIONS-PENDING.md`: a derived,
regenerated-never-hand-edited projection of every open item that needs the operator, one line
each in plain English with what-happens-if-ignored and a yes/no framing. `/sweep` only checks it
for staleness (`--check`, read-only); regenerating it is a write-side act owned by
`/standing-loop` or an interactive session. See `deploy/decision-inbox.py`.

**Desk / empire desk** — the per-project desk (`SWEEP-BRIEFING.md`, the decision inbox, the
deadline register, `deploy/desk-metrics.py`'s append-only trend history at
`receipts/desk/metrics-history.jsonl`, and `deploy/gen-desk.py`'s static, actuator-free
`DESK.html`) is one project's read-only status glass. The **empire desk**
(`deploy/empire-desk.py`) rolls every project registered in the workspace's `projects.yaml` into
a single cross-project `EMPIRE-DESK.md` — a generated projection, no daemon; built 2026-07-23,
activation gated on at least two additional live instances. See `deploy/desk-metrics.py`,
`deploy/gen-desk.py`, and `deploy/empire-desk.py`.

**Dispatch manifest** — `dispatch-manifest.json`, the memory engine's compile-pipeline transport
artifact (F17-attested). Says what a packet carried, not what a surface must do. See
`core/methodology/manifest-format.md` § 1.

**HOLD / PENDING / UNROUTED** — three distinct "waiting" states, easy to conflate. A **hold
point** is a methodology gate firing at every phase transition (four checks, operator confirms
go/no-go) — see `core/methodology/flight-plan-template-v6.md` § "HOLD POINT PROTOCOL". **PENDING**
and **UNROUTED** are two of the census's seven event classes — PENDING is registered but not yet
absorbed; UNROUTED means the event's tags/entities match no view's `subscribes.entities` (a
routing gap, not a defect). See `docs/engine/OPERATIONS.md` § 3 "Routing / triage".

**HUMAN-GATE** — the rule that VERIFY (never ABSORB/compile) requires a recorded,
verbatim-quoted operator authorization artifact before it runs; standing memory of a prior "go
ahead" never satisfies it. See `docs/engine/OPERATIONS.md` § 5 "dispatch-check — the
HUMAN-GATE".

**Journal** — the chained run journal (`receipts/journal/`), `prev_record_hash`-linked, recording
what each compile/verify run absorbed as set membership, so a broken or tampered chain is
refused rather than silently trusted. See
`capabilities/knowledge-os/extracted/wiki-schema.md.template` § 17.4 (post-init path:
`docs/wiki-schema.md`).

**Ledger-and-lens** — the memory engine's core principle: every raw fact is an append-only
ledger event; every wiki article is a lens, a derived and rebuildable view over that ledger,
never itself the source of truth. See
`capabilities/knowledge-os/extracted/engine/memory-engine-v3-spec.md` § 2 (post-init path:
`docs/engine/memory-engine-v3-spec.md`).

**MANIFEST GATE** — the tier-scaled check that no build increment fires until every layer it
touches has a behavioral manifest at the required status (T1/T2 CERTIFIED, T3 EXTRACTED, T4
exempt-with-named-reason); read from the surface's MANIFEST-INDEX. Printed as a banner at Step
0 of every tier protocol, and enforced mechanically by the engine's packet assembler. See
`core/methodology/manifest-format.md` § 7.

**MANIFEST-INDEX** — one per surface: the machine-checkable face of the build gate; per-layer
status (DRAFT/EXTRACTED/CERTIFIED/LIVE/SUPERSEDED), row counts, certification receipts, and the
gate state for the next increment. See `core/methodology/manifest-format.md` § 6.

**OPEN marker** — the literal field value `OPEN — missing: <fact>` marking a row blocked on a
missing operational fact; not a flag. Countable — certification must disposition every OPEN
with a named owner. See `core/methodology/manifest-format.md` § 4.

**Orchestrator / subagent** — an **orchestrator** is a Claude Code skill driving a whole protocol
end-to-end (`/compile`, `/preflight`, `/flight-plan` — see `core/governance/CLAUDE.md.template`
§ "Orchestrator inventory"; post-init path: `core/governance/CLAUDE.md`); a **subagent** is a role-scoped session dispatched for one bounded
task (e.g. a Spec Reviewer, or a preflight evidence-sweep agent) that never spawns further
agents itself. See `core/methodology/tier-definitions.md.template` § T3 (post-init path:
`core/methodology/tier-definitions.md`) and `core/skills/preflight/SKILL.md` § "Evidence sweep".

**Origin (human / corpus / unknown)** — the mechanically-derived provenance every registered
event carries: personnel-tagged → `human`; session/observation-authored → `corpus`; unparseable
frontmatter → `unknown` (non-blocking, upgradeable only by explicit operator attestation, never
automatically). Origin only rises across a view's lineage, never falls. See
`docs/engine/OPERATIONS.md` § 2.

**Preflight trace** — the durable marks `/preflight` leaves on an interrogated artifact: a dated
`preflighted YYYY-MM-DD` status stamp, inline `Preflight note:` annotations, and a closing report
of claims verified, terms resolved, and open questions routed. See
`core/skills/preflight/SKILL.md` § "Preflight trace — the durable marks".

**R-1 (session-loop candidate pipeline)** — the harvest → stage → register → promote pipeline
(`candidates.py`, `harvest-candidates.py`, `register-candidates.py`, `check-candidates.py`,
`promote-candidate.py`) that turns a session-authored span into a signing-gated,
operator-authored registered event: promotion refuses without an `ARMED` artifact from a pinned
SSH key, is single-use, and is TOCTOU-proofed. Ships **UNARMED** — `signing-config.yaml.example`
carries no pinned fingerprint. Closes the gap the frozen test plan named R-1. See
`docs/engine/OPERATIONS.md` § "Honest gaps" and `capabilities/knowledge-os/extracted/deploy/candidates.py`.

**Raw event** — a frontmattered file under `raw/`, the unit of intake before anything downstream
(registration, compile, verify) happens to it — nothing runs automatically; a session or
operator decides when to run the loop over it. See `docs/wiki-schema.md` § 2 "Raw Intake"
(post-init path; pre-init source `capabilities/knowledge-os/extracted/wiki-schema.md.template`).

**Receipt** — the machine-readable record every orchestrator run produces in `receipts/`, one
file per run, a shared envelope with type-specific fields; `changelog.md` is its human-readable
narrative companion and is not machine-read. See
`capabilities/knowledge-os/extracted/wiki-schema.md.template` § 7 (post-init path:
`docs/wiki-schema.md`).

**Registration chain** — the typed, append-only per-event record (`origin`, `event_class`,
`asserts_corpus_state`) every raw event needs before anything downstream can see it, written by
`deploy/register-intake.py` as a delta-only append — never re-minting, never touching an
existing record. See `docs/engine/OPERATIONS.md` § 2.

**RECIPE** — the ten-field document (`RECIPE.md`) every capability carries: what it is, when a
project needs/doesn't need it, status, provenance, dependencies, known lessons, open questions,
and (extracted only) migration steps. See `ARCHITECTURE.md` § "Recipe format". Toggled
capabilities' `RECIPE.md` lives at `capabilities/<name>/RECIPE.md` pre-init only (deleted at
init); their *deferred* sub-recipes propagate to `docs/recipes/<name>/`. Docs-only capabilities
propagate their full `RECIPE.md` to `docs/recipes/<name>/` too.

**Result manifest** — the execution engine's parallel-build crash-recovery record (files
touched, tests run, completion status). Says what a Builder did. See
`core/methodology/manifest-format.md` § 1.

**Stage-only commit** — every compile/verify run commits per-path adds only, never a bare
directory add, mechanically checked by `deploy/check-run-diff.py --sections`. See
`docs/engine/OPERATIONS.md` § "Stage-only commit, worktree-per-shard discipline".

**Standing loop** — `/standing-loop`: the scheduled write-side autopilot. Runs `/sweep`,
regenerates the decision inbox, and compiles pending raw content on a disposable
`standing-loop/compile-YYYYMMDD` branch — never the default branch, never a merge. Armed only by
a committed operator authorization artifact plus a passed rollback rehearsal receipt; absorption
into truth still requires a human "yes" in an interactive session. See
`core/skills/standing-loop/SKILL.md`.

**Substrate separation** — evaluator kept structurally distinct from the evaluated. For code,
deterministic verification (tests) is the primary firewall and substrate separation is
defense-in-depth; for decisions with no executable test, substrate separation is primary. See
`ARCHITECTURE.md` § "Substrate separation".

**Sweep** — `/sweep`: the check-everything button. Runs every read-only health check (doctor,
census, structural sensors, manifest structure, conformance smoke, and the read-only half of
audit) in one pass and returns one plain-English briefing; never changes anything. See
`core/skills/sweep/SKILL.md`.

**Three moves (change taxonomy)** — every post-ship change is exactly one of: definition
amendment (rows change first — the amendment IS the feature), projection change (code changes,
sweep proves zero row deltas — the checkable definition of a refactor), or conformance
restoration (defect fix turning a red row green). See
`core/methodology/manifest-driven-builds.md` § 9.1.

**Tiers (T1–T4)** — the four verification-effort levels assigned at planning, never downgraded
mid-build: T1 full firewall (money/irrecoverable-trust class), T2 full firewall with cascade
tests, T3 spec review + code quality (no separate Verifier), T4 builder self-verified. Each tier
also sets the behavioral-manifest gate depth: T1/T2 CERTIFIED, T3 EXTRACTED, T4
exempt-with-named-reason. See `core/methodology/tier-definitions.md.template` (post-init path:
`core/methodology/tier-definitions.md`).

**Verify leg / cross-vendor** — one invocation of the cross-vendor honesty check: a
substrate-different model reads the event and the current view and answers whether the
absorption is faithful. Verdicts are data, not instructions — `revised`/`rejected` means a real
defect was caught. See `docs/engine/OPERATIONS.md` § 7 "Cross-vendor verify".

**View / derivation block** — a wiki article is a **view**; its optional **derivation block** is
the engine-managed, mechanically-strippable region (delimited by
`# --- derivation (engine-managed; strip region) ---` / `# --- /derivation ---`) recording how
the view was derived — humans never hand-edit it. See
`capabilities/knowledge-os/extracted/wiki-schema.md.template` § 17.1 (post-init path:
`docs/wiki-schema.md`).

**Workspace governance** — the standard for the shared machine workspace one level above any
single repo: four root zones (`repos/`, `worktrees/`, `scratch/`, `archive/`), a `WHY.md` birth
certificate with a death date per non-permanent folder, and a `projects.yaml` registry (shared
with the empire desk). `deploy/check-workspace.py` is the report-only reaper — it names drift
(merged/deleted worktrees, missing WHY.md fields, naming notes, past-lifetime folders); deletion
stays a human word. See `core/governance/WORKSPACE.md.template` and
`deploy/check-workspace.py`.

**Worktree-per-shard** — running each batch of events being absorbed together (a "shard") in its
own linked git worktree/branch rather than the main working tree, so a parked or retried run
never touches the trunk. See `docs/engine/OPERATIONS.md` § "Stage-only commit,
worktree-per-shard discipline".
