# The Tour

*How to use this: read Stages 1–3 in order, on day one, before you touch anything. Stage 4 is
not a narrative — keep it open as a reference and jump straight to the matching row when
something happens. Nothing here substitutes for the canonical doc it points at; every claim
below cites one, and the citation is the authority, not this page.*

*Paths here are as they exist in your project once setup has run. If you're reading inside the
raw template instead, see `TEMPLATE-README.md`'s file-layout table for where each thing starts
out; files that exist only in the raw template are marked "(template-only)".*

*verified-against: 3.0 (2026-07-24)*

---

## Stage 1 — WHY

### The positive case

This harness exists because knowledge that lives only in a chat transcript evaporates, decisions
made in a hurry get re-litigated forever, and "looks done" is not the same claim as "is done."
Four things it buys a project that runs for months rather than an afternoon:

- **Compounding verified knowledge.** Raw facts land in `raw/`, get compiled into wiki articles,
  and — where the memory engine is enabled — every absorption is checked by a mechanical census
  that proves nothing was silently lost, and by a substrate-different model that checks the
  absorption was faithful. See `capabilities/knowledge-os/RECIPE.md` § 1 (template-only) and
  `docs/engine/OPERATIONS.md` for what "compounding" actually means mechanically, not just
  narratively.
- **Decision firewalls.** Hard-to-reverse decisions don't get graded by the same session that
  made them. The handoff protocol routes them to a substrate-separated verifier before they lock
  — see `core/handoffs/README.md` and `core/governance/CLAUDE.md` § Session discipline →
  Decision-lock substrate firewall.
- **Honest sensors.** `/doctor`, the conservation census, and the frontmatter/derivation sensors
  are built to fail loud with an actionable `FIX:` line, never to fail silently or bare. See
  `.claude/skills/doctor/SKILL.md` § How to interpret results.
- **Cross-vendor verification.** A same-vendor session grading its own work is a correlated
  failure risk; `/cross-check` and the memory engine's VERIFY leg call a genuinely different
  model, on purpose. See `.claude/skills/cross-check/SKILL.md` and
  `docs/engine/OPERATIONS.md` § 7.

### The failure modes this exists to prevent

These are not hypothetical — the harness found each of these happening to *itself* during a
2026-07-10 self-audit, which is why this tour exists at all (see `HARNESS-CHANGELOG.md` § v3.0,
Theme D).

- **Silent drift.** A doc describes a system that has since moved on, and nothing flags the gap
  until someone hits it live. The harness's own human-facing docs sat frozen at old prose while
  the `VERSION` file had already moved — the exact staleness class the census is built to catch
  in wiki content, found uncaught in the harness's own documentation. Cite
  `HARNESS-CHANGELOG.md` § v3.0 intro paragraph.
- **Unverified memory.** A wiki article can assert something a raw event never actually
  established — an omission, a fabrication, a stale contradiction left in place after the thing
  it described changed. Cross-vendor VERIFY exists because this happens and a same-substrate
  read-back does not reliably catch it. Cite `docs/engine/OPERATIONS.md`, opening paragraph.
- **Homonym rot.** The same name drifting to mean two different things across a corpus until
  nobody can say which is authoritative. The harness carried exactly this — `/grill` vs.
  `/preflight` disagreeing across five-plus files — until the 2026-07-09/07-10 rename swept it.
  Cite `HARNESS-CHANGELOG.md` § v3.0 Theme C and Theme D.
- **Component-gates-green-loop-open.** A checker can pass its own self-test while the thing it
  is supposed to validate was never actually wired in — a green gate proving only that the
  checker works, not that the checked thing does. Cite
  `capabilities/knowledge-os/RECIPE.md` § 8, "The loop-closure-gate lesson" (template-only).

None of this is marketing. Every one of the four failure modes above is a class the harness
caught happening to its own corpus, cited to the changelog entry or recipe line that recorded
it.

---

## Stage 2 — WHAT

### Core vs. capabilities

The harness ships **core** — methodology, governance, handoffs, security perimeter, a canonical
glossary — unconditionally, in every instantiation. **Capabilities** are opt-in per
`project.yaml.capabilities` (the schema requires exactly three keys: `knowledge-os`,
`stress-testing` (retired no-op, key still required), and `code-conventions`; three more —
`kickoff-orchestration`, `operate-sentinel`, `decorrelated-review` — are docs-only and always
propagate regardless of any toggle). See `ARCHITECTURE.md` § Overview and § Core (Zone 1) /
§ Capabilities (Zone 2) for the full model, and `capabilities/INDEX.md` for the toggle table
(template-only — deleted once init consumes it).

### The skills you'll actually type

These are the tour stops — the skills a first week actually touches — not the complete
roster. The full core set is whatever `core/skills/` ships, enumerated in
`TEMPLATE-README.md`'s file-layout table (Core skills row); enabled capabilities add their
own on top.

| Skill | One sentence | Full doc |
|---|---|---|
| `/flight-plan` | Generates a delta-first session briefing — what changed, what's healthy, what needs attention; the cockpit for in-progress work. | `.claude/skills/flight-plan/SKILL.md` |
| `/preflight` | Stress-tests a plan, spec, or governance doc against repo evidence and documented decisions before it ossifies, sharpening terminology into `CONTEXT.md` along the way. | `.claude/skills/preflight/SKILL.md` |
| `/compile` | Compiles raw intake files into wiki articles, updates cross-links, cascades to the roadmap, regenerates indexes and health. | `.claude/skills/compile/SKILL.md` (`knowledge-os` only) |
| `/audit` | Grades roadmap assumptions against wiki evidence and logs gaps/contradictions to `REVIEW.md`. | `.claude/skills/audit/SKILL.md` (`knowledge-os` only) |
| `/discover` | Shows what the corpus implies but nowhere states — five modes (relate/derive/gap/trace/introspect) — filing proof-carrying draft findings back into the normal intake pipeline. | `.claude/skills/discover/SKILL.md` (`knowledge-os` only) |
| `/doctor` | Runs the unified environment-readiness sensor — bridge wiring, node/codex/jq presence and auth, every knowledge-os sensor's `--self-test`, security-hook wiring, skill drift. | `.claude/skills/doctor/SKILL.md` |
| `/cross-check` | Gets a fast, substrate-different (cross-vendor) second opinion on a build-completion claim or a reversible decision. | `.claude/skills/cross-check/SKILL.md` |
| `/orient` | Answers grounded questions about *this project's harness* by reading the installed artifacts and citing them — never from model memory. | `.claude/skills/orient/SKILL.md` |
| Handoffs (`/handoff`) | The substrate-separation ritual for decisions that have no executable test — one pass: author, cross-vendor bridge answer leg, lock (T1 via a headless close leg + one operator yes). | `core/handoffs/README.md` |
| `/log-backlog` | Appends a formatted entry to `harness-backlog.md` for any harness-template (not project-content) issue you hit. | `.claude/skills/log-backlog/SKILL.md` |

### The memory engine, in five sentences

1. Every raw fact is an append-only **ledger event**; every wiki article is a **lens** — a
   derived, rebuildable view over that ledger, never itself the source of truth
   ("ledger-and-lens," `docs/engine/memory-engine-v3-spec.md` § 2).
2. Getting a new event from written to trustworthy runs one loop — **register** it onto the
   chain, **route** it to the views its tags/entities match, **compile** (absorb) it into those
   views, **verify** the absorption with a substrate-different model, then re-run the
   **census** — per `docs/engine/OPERATIONS.md` § "The loop".
3. Compile (ABSORB) is autonomous; VERIFY is not — it fires only against a recorded, verbatim
   operator authorization, never standing memory of a prior go-ahead (the **HUMAN-GATE**,
   `docs/engine/OPERATIONS.md` § 5).
4. The **census** sorts every registered event into exactly one of seven ordered classes and
   fails loud — `problems: []` / `new_holes: []` — the moment the accounting stops adding up
   (`docs/engine/OPERATIONS.md` § "Census green check").
5. Census green means the wiki content in front of you has been mechanically proven non-lost and
   cross-vendor-confirmed faithful — not merely "compiled and hoped honest"
   (`docs/engine/OPERATIONS.md` § "What green looks like").

For the full system map, open `core/onboarding/SYSTEM-MAP.html`. For every term above defined in
one place, see `core/onboarding/GLOSSARY.md`.

### The manifest layer, in four sentences

A manifest is the checklist of exact behaviors a build must demonstrate — rows a builder must
discharge and a verifier can replay. Nothing ships until its checklist is checked: no build
fires until every layer it touches has a manifest at the tier's required depth. Every later
change says which row it changes — one of three declared moves (amend / projection-change /
restore) — so undeclared drift is a defect by definition. Provenance says where every fact came
from; manifests say what every build must do — the template ships both. See
`core/methodology/manifest-driven-builds.md` and `core/methodology/manifest-format.md`.

---

## Stage 3 — FIRST WEEK

The do-this-now sequence, in order. Each step names what you do, what green looks like, and
where the full instructions live.

### 1. Kickoff interview

**Do:** Open `INIT.md` in a fresh Claude session (a conversation, not a Claude Code build
session) after `init.ps1`/`init.sh` has run and `init-validate` reports PASS. Walk the
interview steps in order (INIT.md is the authority for the list), recording each answer in
`raw/YYYY-MM-DD-session-project-init.md`.
**Green:** every interview step answered (INIT.md's list), governance/roadmap skeletons created,
the log's frontmatter carries `compile: false` (it's a process record, not knowledge intake).
**Full doc:** `INIT.md` §§ "Kickoff interview" through "Post-interview validation".

### 2. CONTEXT.md population

**Do:** Step 2h of the same interview — name the first 5–10 terms that need pinning down and
define each in Pocock format.
**Green:** `CONTEXT.md` has at least the seed terms from 2h (checked in INIT.md's
post-interview validation list).
**Full doc:** `INIT.md` § 2h; format at `.claude/skills/preflight/CONTEXT-FORMAT.md`.

### 3. `/preflight` the first roadmap artifact

**Do:** Run `/preflight` against the first `roadmap/phase-N-<slug>.md` skeleton the kickoff
created — it sweeps evidence first, interviews you only on what the repo can't settle, and
sharpens `CONTEXT.md` inline as it goes.
**Green:** the artifact is stamped `preflighted YYYY-MM-DD`, `CONTEXT.md` gained any resolved
terms, and you have a closing report (claims verified, terms resolved, open questions routed).
**Full doc:** `.claude/skills/preflight/SKILL.md` § Protocol and § Landing the results.

### 4. `/flight-plan`

**Do:** Author the flight plan from the freshly preflighted roadmap phase — the standard kickoff
session runs the health check, preflights the phase article, then authors the flight plan from
it, all in one sitting.
**Green:** Layer 1 Dashboard exists and reflects the phase's real current state; `/doctor`
appears in the plan's cadence as an init-end-plus-on-demand step.
**Full doc:** `.claude/skills/flight-plan/SKILL.md`;
`core/methodology/flight-plan-template-v6.md`;
`core/methodology/HOW-TO-USE-FLIGHT-PLAN.md`.

### 5. First raw event

**Do:** The first working session writes a frontmattered file to `raw/` — `source`, `date`,
`tags`, `summary`, per the naming and frontmatter rules.
**Green:** the file opens with a valid YAML frontmatter block and a `source:` tag from the
declared valid set.
**Full doc:** `docs/wiki-schema.md` § 2 "Raw Intake".

### 6. First compile cycle

**Do:** If `knowledge-os` is enabled, run the loop over that first raw event: `register-intake.py`
(delta registration) → routing/triage against the census → author a compile plan → the
dispatch-check HUMAN-GATE → absorb via the compile backend → cross-vendor verify → re-run the
census.
**Green:** the just-compiled event shows `CONSUMED` in the census, every other event's class is
byte-for-byte unchanged, `problems: []`, `new_holes: []`.
**Full doc:** `docs/engine/OPERATIONS.md` § "The loop" and § "What green looks like";
`.claude/skills/compile/SKILL.md` for the content-layer half of the same cycle if the engine
isn't yet in scope for this event.

### 7. First `/cross-check`

**Do:** Once you have a build-completion claim or a reversible decision to sanity-check, run
`/cross-check` against it.
**Green:** a verdict comes back and gets folded into your reasoning — a `revised`/`rejected`
verdict is the honesty layer working, not a result to argue with or route around.
**Full doc:** `.claude/skills/cross-check/SKILL.md`.

### 8. Manifest awareness before the first build

**Do:** Before your first build increment, open `manifests/` awareness — read
`core/methodology/manifest-format.md` §2 and §7, and know that the build gate will ask for
manifests on every layer the increment touches, at your tier's required depth. If none exist
yet, your first build-adjacent task is the extraction, not the build.
**Green:** You can name, for the surface about to be touched, what
`manifests/<surface>/MANIFEST-INDEX.md`'s gate says today — OPEN or CLOSED — and which layers
still need extraction before the increment can fire.
**Full doc:** `core/methodology/manifest-format.md` §§ 2, 7;
`core/methodology/manifest-driven-builds.md` § 3.

---

## Stage 4 — WHEN-X-HAPPENS

Keep this open. Jump to the matching row; don't read it top to bottom.

| Symptom | What it means | First move | Doc |
|---|---|---|---|
| Not sure if anything needs attention today | No isolated symptom yet — a general "is everything OK" question, the class `/sweep` exists to answer in one shot instead of chasing `/doctor`, the census, and conformance one at a time. | Run `/sweep` — one briefing, plain English, nothing changed. | `core/skills/sweep/SKILL.md` |
| You want the housekeeping (sweep, the decision inbox, pending compiles) to just run on a schedule instead of by hand | Exactly what `/standing-loop` is for — Phase-B, unarmed by default, everything lands on a disposable branch, nothing merges itself. | Read the arming requirements before switching it on: a rehearsal receipt plus a committed operator-authorization artifact. | `core/skills/standing-loop/SKILL.md` |
| Not sure what's actually waiting on you right now | A general "what needs my yes" question — the class the decision inbox exists to answer in one place instead of scanning the census, the candidate queue, and the deadline register separately. | Read `DECISIONS-PENDING.md`; `/sweep` checks it for staleness (read-only), `/standing-loop` regenerates it. | `deploy/decision-inbox.py` |
| You run more than one project on this harness and want one glance across all of them | Working as designed — no single project's desk shows the others; the empire desk is the rollup one level above. | Run `deploy/empire-desk.py` against the workspace's `projects.yaml` registry (activation is gated on at least two additional live instances). | `deploy/empire-desk.py`; `deploy/desk-metrics.py` |
| A session needs to use a secret (a login, an API key) without it ever showing up in plain text | Not a gap — the credential broker exists for exactly this: the value lives in the OS credential vault, never in a file or on stdout. | Store it with `credential-store.ps1`, use it by name with `credential-use.ps1`, remove it with `credential-remove.ps1`. | `capabilities/knowledge-os/extracted/deploy/credential-store.ps1` |
| Your machine's workspace is filling up with loose clones, worktrees, and one-off folders | The class `core/governance/WORKSPACE.md` exists to end — four named zones, a `WHY.md` birth certificate per non-permanent folder, and a report-only reaper that names drift without ever deleting anything itself. | Adopt the four zones; run `deploy/check-workspace.py` (wired as a `/sweep` step). | `core/governance/WORKSPACE.md.template`; `deploy/check-workspace.py` |
| A session wrote something that should count as the operator's own word, not the session's | The gap the frozen test plan named R-1 — a pipeline that promotes a session-authored span to an operator-authored event, but only against a real, pinned-key `ARMED` artifact. | Harvest the span, then promote it — nothing is promoted without a signed artifact; ships unarmed until an operator pins a signing key. | `docs/engine/OPERATIONS.md` § "Honest gaps"; `capabilities/knowledge-os/extracted/deploy/candidates.py` |
| `/doctor` red | A FAIL check found a real setup gap — an unauthenticated codex, a missing `jq`, unwired hooks, a self-test failure. | Read the `FIX:` line for that check verbatim and act on it; a codex/bridge FAIL only means cross-vendor tools are inert, not that the rest of the project is broken. | `core/skills/doctor/SKILL.md` § "How to interpret results" |
| Census not green (`problems`/`new_holes` non-empty) | An accounting discrepancy in the memory engine — something doesn't add up. | Stop compiling further; do not force a re-run. Adjudicate the named entry first. | `docs/engine/OPERATIONS.md` § "Failure handling" (post-init path) |
| A VERIFY leg rejects or comes back `revised` | The cross-vendor honesty layer caught a real defect — omission, fabrication, or a stale contradiction left in place. | Fix the view and re-verify. Never argue with or route around the verdict; read the full nested verdict, not just the top-level field. | `docs/engine/OPERATIONS.md` § 7 "Cross-vendor verify" and § "Failure handling" (post-init path) |
| UNROUTED events pile up | Events whose tags/entities match no view's `subscribes.entities` — a routing gap, not a defect. | Triage each: extend `entities.yaml`'s vocabulary (operator-gated), mark `compile: false` if it's archival, or park it as honest residue. | `docs/engine/OPERATIONS.md` § 3 "Routing / triage" (post-init path) |
| A skill feels stale or contradicts a doc | The homonym-drift / skill-drift class this tour exists to end. | Run `/doctor`'s skill-drift check first, then `/log-backlog` the contradiction so it's tracked. | `core/skills/doctor/SKILL.md` check catalog #8; `core/skills/log-backlog/SKILL.md.template` |
| You want to change a trust surface (allowlist / origin config / hooks) | Editing `core/security/hooks/`, `entities.yaml`'s allowlist, or the origin model is a hard-to-reverse, security-relevant move. | Treat it as a decision, not an edit — `/preflight` it first; open a handoff if it's genuinely hard to reverse and surprising without context. | `core/security/hooks/README.md`; `core/handoffs/README.md` |
| Context runs long mid-work | A single session's context window is filling and the work isn't done. | Hand off — author a handoff packet so a fresh substrate continues with full context — or split the work via worktree-per-shard for parallel build. | `core/handoffs/README.md`; `docs/engine/OPERATIONS.md` § "Stage-only commit, worktree-per-shard discipline" (post-init path) |
| Something feels wrong and you don't know what | Vague unease, no isolated symptom yet. | Ask `/orient` a grounded question first. If it's still unresolved, run `/discover introspect` over the corpus for reflexive drift. | `core/skills/orient/SKILL.md`; `capabilities/knowledge-os/extracted/discover/SKILL.md` (post-init: `.claude/skills/discover/SKILL.md`) |
| Your project's knowledge is all `scope: domain` and the roadmap never updates from compiles | Working as designed, not a defect — the roadmap cascade only fires for `scope: build`/`scope: mixed` articles. A domain-only project gets no automatic cascade. | Reconcile roadmap assumptions against domain articles manually; don't wait for `/compile` to do it. | `capabilities/knowledge-os/extracted/compile/SKILL.md.template` Step 5 (Roadmap Cascade — honest note); `harness-backlog.md` v3.0-30 (the open design question) |
| About to fire a build and unsure the contract is complete | The build gate is touch-based and tier-scaled — every layer the increment touches needs a behavioral manifest at the tier's required depth. | Read `manifests/<surface>/MANIFEST-INDEX.md`; if the gate says CLOSED, the next dispatch is the extraction (`manifest-format.md` § 7). | `core/methodology/manifest-format.md` §§ 6–7; `core/methodology/manifest-driven-builds.md` § 3 |

---

*Sources cited throughout: `TEMPLATE-README.md`, `ARCHITECTURE.md`, `INIT.md`,
`capabilities/INDEX.md`, `capabilities/knowledge-os/RECIPE.md`,
`capabilities/knowledge-os/extracted/engine/OPERATIONS.md`, `core/skills/doctor/SKILL.md`,
`core/skills/preflight/SKILL.md`, `core/handoffs/README.md`, `core/security/hooks/README.md`,
`HARNESS-CHANGELOG.md`. Paths in the body are as they exist in an initialized project;
"(template-only)" marks files that exist only in the raw template clone and do not survive
`init.ps1`/`init.sh`.*
