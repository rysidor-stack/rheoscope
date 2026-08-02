<!-- CONTEXT.md — canonical glossary for HARNESS-DEVELOPMENT (the template itself),
     not for an instantiated project. Format per CONTEXT.md.template (Pocock-derived,
     MIT, Decision V2-8).

     Why this cannot leak into projects: init substitutes CONTEXT.md.template and
     writes the result to CONTEXT.md, OVERWRITING this file in the instantiated copy.
     The dev glossary is therefore dev-only by construction — no exclusion list needed.
     (Verified as part of the v2.0 #10 permutation runs.)

     Created by v2.0 #10b (AC7): the v2.0 sharpening gate had to run /grill's protocol
     against an ad-hoc glossary because this file did not exist. Now it does.
     (Note, 2026-07-09: /grill was renamed /preflight; this provenance note describes
     what happened at the time and is left as-is.) -->

# Rheoscope (harness-dev)

This is the working glossary for sessions that develop the harness **template itself** — meta-development, where the product is the template other projects instantiate from. Terms here resolve ambiguity during harness-dev sharpening, build, and review sessions; `/preflight`-on-self uses this file as its glossary (see below).

## Language

### The code firewall (actors)

**Builder**:
The session that writes an increment's implementation.
_Avoid_: author-session, coder, implementer.

**Verifier**:
The session-isolated actor that writes the verification tests from the verification spec — blind to the Builder's code, before or concurrently with the build; the Runner, not the Verifier, executes them.
_Avoid_: reviewer (ambiguous with handoff rounds), checker, and conflation with **verifier review (harness-dev)** below.

**verifier review (harness-dev)**:
The harness-dev build-firewall practice — a fresh substrate-separated session reviews a Builder session's *diff* and writes a verdict to `verifier-reviews/` (ADR #1 G2). A diff-review, not a test-author: deliberately a different mechanism than the code firewall's Verifier.
_Avoid_: calling this session "the Verifier" unqualified.

**Runner**:
The session or step that executes the verified tests and gates — it runs, it never writes code or tests.
_Avoid_: executor, test-session.

**Controller**:
The orchestrating session that sequences increments and dispatches Builder→Verifier→Runner flows; the harness's only orchestrator.
_Avoid_: manager, scheduler (reserved for the sentinel's trigger mechanism).

### Units and structure

**increment**:
The unit of build work — one scoped change that passes its own verification gate before the next begins.
_Avoid_: ticket, story, task.

**tier**:
The blast-radius class (T1–T4) of a change, which selects the verification depth it must receive; T1 is the hard-to-reverse / silent-error money tier.
_Avoid_: priority, severity (those describe backlog entries, not changes).

**capability**:
An opt-in template module (catalogued under `capabilities/`) that init wires into a project and then deletes from the catalog; contrast with **core**, which always ships.
_Avoid_: plugin, feature flag.

**sensor**:
A deterministic runtime check that re-derives a spec-vs-reality gap on every run so drift cannot be silently forgotten; most are detect-only with a human reconcile (REVIEW auto-compaction is the one auto-reconciling exception in the v1.3 trio).
_Avoid_: linter, monitor (monitors watch runtime systems; sensors watch the control plane).

### Verification and decision machinery

**firewall**:
A separation discipline that keeps the producer of an artifact from certifying it. Two engines share the one pattern: the **code firewall** (Builder/Verifier/Runner, per-increment, binary PASS/FAIL) and the **decision firewall** (handoff protocol, multi-round, human-paced, nuanced verdict). They are deliberately NOT one engine (ADR #1 commitment f: #5 deferred — shared envelopes only, no shared engine; merging them requires a new handoff).
_Avoid_: "the firewall" without qualifying which.

**handoff**:
The decision firewall's artifact bundle — packet, round outputs, `meta.yaml`, close record — through which a contested decision is independently reviewed on a separate substrate and locked.
_Avoid_: review (underspecified), sync.

**substrate**:
The model family executing a session. Decision firewall rounds require substrate separation (author-family ≠ verifier-family) at T1; **verify legs inside a compile are a different rule** — see **substrate gate**.
_Avoid_: model (a substrate is the family, not the checkpoint); treating the T1 decision-lock rule as if it also governed compile verify legs (it does not, and that conflation caused a live incident 2026-07-29).

**substrate gate**:
The tiered separation test a *verify leg* must satisfy, decided mechanically by `substrate_gate_ok()` in `deploy/check-substrate.py`: a **routine** leg passes on a different `model_id` (same vendor is compliant, including OpenAI-verifying-OpenAI); **content-audit** and **design-gate** legs require a different vendor (the cross-substrate firewall); an unknown gate kind fails closed. Raising a leg's tier is a decision, not a fix.
_Avoid_: substrate separation unqualified (it hides the tier); cross-vendor gate as the default shape of a verify leg.

### Execution isolation and operate phase

**worktree**:
An isolated git checkout that lets a Builder work without touching the main tree; the unit of execution isolation for (future, v2.1 #1) parallel builds. Worktrees isolate the *checkout*, not secrets, data, or runtime state.
_Avoid_: branch (a worktree holds a branch; they are not the same), sandbox.

**sentinel**:
The planned (v2.1 #2) operate-phase execution mode: a *scheduled Controller* that runs existing monitors unattended and may open gated PRs; explicitly **not** a new firewall actor.
_Avoid_: agent, daemon, bot.

### Harness-dev specifics

**harness-dev**:
Work on the template repo itself, as opposed to work inside an instantiated project. Dev-only artifacts live at root level (`handoffs/`, `verifier-reviews/`, `adr/`, `harness-v2.0/`), never under `core/` or `capabilities/` (those instantiate).
_Avoid_: meta-project, upstream (reserve for Pocock-fork lineage).

**VERSION**:
The template repo's single version source — a one-line root file holding the **in-development release** (the release that will absorb current work). Read at runtime by `init.ps1`/`init.sh` and the harness-dev backlog discipline; consumed (deleted) by init so a finished project carries its version only in `project.yaml.template_version`.
_Avoid_: template_version (that is the *project's* field, stamped from the operator's project.yaml and validated against VERSION).

**operator**:
The human who owns every T1 gate. In this repo: a non-coding orchestrator — surface trade-offs in plain terms; never assume.
_Avoid_: user, admin.

## Relationships

- The **Controller** dispatches **Builders**; a **Verifier** writes tests blind to the Builder's code; a **Runner** never writes what it runs.
- An **increment** carries exactly one **tier**; the tier selects the verification depth and whether the **firewall** must be on.
- The two **firewall** engines share one pattern (independent substrate → immutable artifact → reconciliation) but keep separate lifecycles; a **handoff** is an instance of the decision engine.
- A **capability** is wired by init and then its catalog source is deleted; **core** always ships.
- A **sensor** watches the control plane; a **sentinel** (planned) schedules monitors that watch the runtime.
- A **worktree** isolates a Builder's checkout; it does not isolate data or secrets (that is v2.1 #11's job).
- **VERSION** governs the template; `project.yaml.template_version` governs the instantiated project; init validates one against the other, then consumes VERSION.
- **harness-dev** decisions live in root `adr/` (see `adr/DECISIONS.md`); instantiated-project decisions live in that project's `docs/adr/`.

## /preflight-on-self

`/preflight` (renamed from `/grill` 2026-07-09) is a harness **core skill** (`core/skills/preflight/`), not an installed session skill in this repo (harness-dev is never instantiated — `.claude/skills/` does not exist here). To stress-test a harness-dev artifact (a spec, a changelog entry, a proposed increment):

1. Open `core/skills/preflight/SKILL.md` and apply its protocol **manually** (evidence sweep, terminology-vs-glossary challenge, scope tightening, the three-criteria ADR gate).
2. Use **this file** as the glossary, plus the methodology docs (`core/methodology/execution-engine*`, `verification-architecture*`, `tier-definitions*`) and `HARNESS-CHANGELOG.md` for terms not yet promoted here.
3. When a preflight resolves an ambiguity, record the term (or the flagged ambiguity) here — this file is the accumulating resolution record for harness-dev language.
4. ADRs that a preflight warrants go to root `adr/` (harness-dev), not `docs/adr/` (that path is the *instantiated project* convention).

This replaces the ad-hoc-glossary method the v2.0 sharpening gate had to use (recorded in `harness-v2.0/specs/v2.0-sharpened-specs.md`, Method note).

The same *-on-self* pattern applies to `/log-backlog`: it is not an installed skill in this repo either — apply `core/skills/log-backlog/SKILL.md.template` manually (its Step 1 already covers the template-repo branch: read the root `VERSION` file for the prefix).

## Flagged ambiguities

- **"firewall"** was used for both engines interchangeably before v2.0; resolved — always qualify (*code firewall* / *decision firewall*) unless naming the shared pattern itself.
- **"Verifier"** collides across the two senses above: the code-firewall actor (writes tests from spec, never sees the Builder's code) vs. the harness-dev diff-reviewer (reads exactly that code and verdicts on it). Resolved — "Verifier" unqualified means the firewall actor; the harness-dev practice is "verifier review". The first draft of this very file made that conflation; caught by the #10 adversarial review.
- **"self-healing sensors"** (the v1.3 changelog's label for the trio) overstates two of the three: reference-integrity and hook-parity are detect-only with human reconcile; only REVIEW auto-compaction auto-reconciles (the Memory Tiers brief re-files it as the first *forget-down* instance).
- **"current version"** was ambiguous between shipped and in-development; resolved by v2.0 #10a — for backlog purposes it is the **in-development release** in this repo (the VERSION file) and the **running version** (`template_version`) in an instantiated project. Recorded in `adr/2026-06-09-2-v2.0-versioning-and-migration.md`.
- **"sentinel"** risked reading as a new firewall actor; resolved at the v2.0 sharpening gate — it is a scheduled Controller mode (capability), denied a role in the firewall.

---

## How to maintain this file

Per the upstream format rules (`CONTEXT.md.template`): be opinionated; one-sentence definitions (what it IS); flag conflicts explicitly; only harness-dev-specific terms — general programming concepts don't belong. Group under subheadings as clusters emerge.
