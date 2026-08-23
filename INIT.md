# INIT.md — Manual Kickoff Protocol

This is the manual kickoff protocol for a freshly instantiated Rheoscope. Replaces the eventual `/init` orchestrator (deferred to v1.x — see `docs/recipes/kickoff-orchestration/init.RECIPE.md` for the design that the orchestrator will eventually implement).

**You run this in a fresh agent session.** Claude web/desktop or any capable agentic chat surface (Codex included) that can read files in your project directory. Not Claude Code or another CLI build agent — this is a conversation, not a build.

## Pre-flight checklist

Before opening Claude, confirm:

- [ ] `project.yaml` is populated with identity (name, slug, description) and at least one personnel entry.
- [ ] `init.ps1` (Windows) or `init.sh` (Unix) has been run and exited 0. Along the way it asked for consent to wire the security hooks (`core/security/settings.local.json.example` → `.claude/settings.local.json`; `--hooks`/`--no-hooks` skip the prompt) — know what you answered. Declining leaves the example file in place, unwired. It also asked the one-time **authority-mode** question (`visible` or `required`, v3.0.49; `--authority-mode=`/`-AuthorityMode` skips the prompt) — if you pressed Enter to decide later, content retirement stays disabled and `/doctor` keeps saying so until `project.yaml` carries `trust_surface_signing: visible` or `required`.
- [ ] `init-validate.ps1` / `init-validate.sh` reports **PASS**.
- [ ] The `capabilities/` directory has been removed from the target (init deletes it on success).
- [ ] All enabled capabilities' runtime files are at their canonical locations (under `.claude/skills/`, `docs/`, etc.).
- [ ] Init's end-of-run `/doctor` check was green, or you've addressed the FIX instructions it printed. Re-run any time with `python .claude/skills/doctor/doctor.py`.
- [ ] You have at least 60–90 minutes uninterrupted. The interview is paced and reflective; rushing produces shallow answers that become anchors.

If any of these is not true, do not open the kickoff session. Resolve first.

## Kickoff interview

Open a fresh chat with your agent of choice. Tell the session to read `core/governance/CLAUDE.md` and `core/governance/PROJECT-COMPASS.md` first so it has orientation (paste their contents only if your chat tool can't read files; non-Claude agents: the root `AGENTS.md` pointer lists the same reading order). Before or while you get going, offer the operator the tour: "Want the tour first? `core/onboarding/TOUR.md` is a staged walkthrough of the harness; `core/onboarding/SYSTEM-MAP.html` is an interactive map (double-click to open); ad-hoc questions any time — ask `/orient`." Then walk these ten steps in order. For each, record the answer in a session log at `raw/YYYY-MM-DD-session-project-init.md` (create `raw/` if absent).

**Write the session log's frontmatter first.** `docs/wiki-schema.md` § 2 requires every `raw/` file to open with a YAML frontmatter block, and the kickoff log is a *process record* (its knowledge is canonical in `docs/governance/` + `docs/adr/` + `roadmap/`), not knowledge intake — so mark it `compile: false`. Without this, the first `/compile` run can't route the file, archives nothing, and logs a blocking REVIEW. Open the log with exactly this block before recording any answers:

```yaml
---
source: session
date: <YYYY-MM-DD>
tags: [kickoff, governance]
summary: Project kickoff interview — architecture, hard constraints, wiki domains, governance decisions.
compile: false
---
```

**List-producing steps show their rejects.** Steps 2f–2i each generate an enumerable list (wiki domains, governance docs, glossary terms, ADRs). For each, present not only the surviving candidates but the ones considered and killed, with the reason each died. An empty reject pile on a first draft is the thinness tripwire — it means the step produced a survey, not a judgment; run the draft through its test before presenting it. Doctrine: `core/methodology/five-pass-method.md`.

### 2a. Architecture sketch

> "Describe in one paragraph what you're building. Architecture-level, not feature-level. What are the components? How do they relate?"

Record verbatim. This becomes the seed for `docs/governance/project-thesis.md`. Also drop this paragraph into `core/governance/PROJECT-COMPASS.md`'s "Filled during INIT.md Step 2a" architecture prose — the compass file bills itself as the canonical project orientation embedded into every handoff's context, so it goes stale immediately if only the thesis seed is updated.

If you cannot answer in one paragraph: stop. The harness assumes a mature project thesis. Spend more time in conversation before resuming.

### 2b. Endgame

> "What does 'done' or 'successful' look like? Not a feature list — a project-arc-level answer."

Record verbatim. Also update `core/governance/PROJECT-COMPASS.md`'s "Filled during INIT.md Step 2b" endgame prose from this same answer.

### 2c. Hard constraints

> "What are the hard constraints? Security, compliance, portability, performance — the 'thou shalt not' list. What can never be true?"

Record as bullets. Each constraint should be testable or observable. "Don't be evil" is not a constraint; "Never log PII to stdout" is. Populate `core/governance/HARDCONSTRAINTS.md` with these (the file is already substituted post-init). Also update `core/governance/PROJECT-COMPASS.md`'s "Filled during INIT.md Step 2c" hard-constraints prose from the same bullets — HARDCONSTRAINTS.md and the compass's prose are two separate files the interview populates in parallel; both need the answer, not just one. If you plan to enable any autonomous capability, also complete `core/governance/KILL-SWITCH.md` and `core/governance/AUTOMATION-ISOLATION.md` at enablement time — not now (see `core/methodology/rollback-kill-switch.md` for the required halt-action properties; `core/methodology/least-privilege-isolation.md` for the credential-scope and data-isolation declarations; and `core/methodology/spend-governance.md` for the provider spend cap, soft per-run ceiling, and stop conditions — all three preconditions must be satisfied before the capability first runs).

### 2d. Phase identification

> "Name 3–5 phases. Not features, not increments — modules of work with a beginning, middle, and end. For each: one-line goal, position in the arc, dependencies."

Create skeleton roadmap articles at `roadmap/phase-N-<slug>.md` for each. Layer-1 stubs only; phase content gets filled in during the eventual `/roadmap` orchestrator pass (deferred — see the recipe in `docs/recipes/kickoff-orchestration/`).

### 2e. References intake

> "Do you have existing research, papers, source material? Drop files in `references/` and record each: source URL, date added, why it matters."

Update `references/README.md` to catalog each entry. Explicit ask even when the answer is "no references yet" — establishes the slot in the project shape.

### 2f. Wiki domains *(only if `knowledge-os` capability is enabled)*

> "What wiki subdirectories should this project have? For each: default scope (domain / build / mixed), one-sentence description."

Update `project.yaml.wiki_domains` and create `wiki/<domain>/INDEX.md` skeletons. `wiki_domains` empty post-instantiation means `/compile` has nowhere to route knowledge — populate at least one domain or expect `/compile` to surface REVIEW.md entries with no route. Also refresh `docs/wiki-schema.md` § 1's domains table from the same list: init substitutes it once, at instantiation time, before any domain exists, so it still reads the literal "(No domains declared yet — populate during INIT.md walkthrough.)" placeholder until you replace it here — nothing else in the documented flow ever corrects it. Signpost: on a fresh instance, every registered event shows UNROUTED in the mechanical census until `deploy/entities.yaml` gains entity/view bindings — that's expected and honest, and `docs/engine/OPERATIONS.md` § 3 covers the triage when you're ready; day-to-day content routing via `/compile` works without it.

### 2g. Governance docs

> "What governance documents should this project have? Things treated as canonical authority. Always recommend `docs/governance/project-thesis.md` at minimum (seeded with 2a, 2b, 2c)."

Update `project.yaml.governance_docs`. Create skeleton stubs at the declared paths.

### 2h. Glossary seeds

> "What are the first 5–10 terms in this project that need pinning down? For each: term, definition."

Every seed term must pass all three legs of the **glossary term test** before it lands (this promotes `CONTEXT-FORMAT.md`'s "only terms specific to this context" rule from buried prose to a per-term gate):

1. **Project-specific** — the term would be wrong or meaningless in a neighboring project. "Acceptance gate" and "source asset" fail this leg in almost any project; they are general concepts wearing project clothes.
2. **Behavior-changing** — the distinction it marks changes what a session does downstream (routes work differently, blocks something, alters an output). If nothing changes when the term is confused with its neighbor, it is not a seed term.
3. **Displaces a named near-miss** — its `_Avoid_:` line names the generic term(s) it replaces. A term with nothing to displace usually fails leg 1 too.

Reject candidates that fail any leg and show them in the reject pile. Populate `CONTEXT.md` using the Pocock format (see `.claude/skills/preflight/CONTEXT-FORMAT.md` — core, always present), `_Avoid_:` lines included.

### 2i. Initial ADRs

> "Are there hard-to-reverse decisions already made that future sessions need to know about?"

For each that passes Pocock's three criteria — **hard to reverse**, **surprising without context**, **real trade-off** — create `docs/adr/<YYYY-MM-DD>-<n>-<slug>.md`. The `<n>` is an **unpadded** sequential decision number (1, 2, 3…, not 001), scoped **per day**: scan `docs/adr/` for existing files carrying today's date and increment `<n>` by one (start at 1 if none). This is the harness's operative ADR-numbering convention — see `.claude/skills/preflight/ADR-FORMAT.md` § Numbering (also the convention `/preflight`-authored ADRs follow). Reject candidates that don't meet all three criteria; they're not ADRs, they're just facts.

### 2j. Totality read-back

No new question — a synthesis step. Re-read the nine answers as one system and ask what they mean *together*: where do answers conflict; what risk or dependency emerges only across steps; what does a hard constraint (2c) forbid that a phase (2d) quietly assumes; which glossary term (2h) does the architecture sketch (2a) strain? Record findings in the session log under a `## Totality read-back` heading. If nothing surfaces, write `No cross-answer findings — attested` explicitly — silence is not a pass. This is Pass 5 of `core/methodology/five-pass-method.md`, the pass with no natural forcing function, which is why the protocol forces it.

## Post-interview validation

Re-run the validator:

```bash
# Windows
powershell -NoProfile -File init-validate.ps1

# Unix
bash init-validate.sh
```

Then manually confirm:

- Wiki domains created (if knowledge-os enabled).
- `docs/wiki-schema.md` § 1's domains table matches `project.yaml.wiki_domains` (2f) — no leftover "(No domains declared yet...)" placeholder.
- Governance stubs exist at the declared paths.
- `core/governance/PROJECT-COMPASS.md`'s Architecture / Endgame / Hard constraints prose matches the 2a/2b/2c answers, not the unfilled template prompts.
- `CONTEXT.md` has at least the seed terms from 2h, and every seed term carries an `_Avoid_:` line (the term test's third leg is visible in the file itself).
- The session log contains a `## Totality read-back` section (2j) — findings or the explicit no-findings attestation, never absent.
- `references/README.md` catalogues whatever was dropped in 2e.

## Commit

```bash
git add -A
# Use the commit message init printed at the end of its run, with "— kickoff complete"
# appended. Example for a v2.0 harness:
git commit -m "instantiated rheoscope-harness v2.0 — kickoff complete"
```

## Next steps

Begin work.

- The first phase of work typically starts by producing a flight plan: run `/preflight` (a core skill) against the first roadmap phase article, then — same session — author `wiki/flight-plans/<project-slug>-flight-plan.md` from `core/methodology/flight-plan-template-v6.md` using the preflighted article as source. This authoring step belongs to the kickoff session, not to `/preflight` or `/flight-plan` (neither authors the plan — see `core/methodology/HOW-TO-USE-FLIGHT-PLAN.md`). `/flight-plan` (also core) then surfaces the authored plan each session. See `.claude/skills/preflight/SKILL.md` and `.claude/skills/flight-plan/SKILL.md`.
- If `knowledge-os` is enabled, the first `/compile` run happens after the first session writes a raw file. `git init` is a hard prerequisite for any `register-intake` or `/compile` run — the registration chain lives in git, not just versioning hygiene — so make sure the Commit step above has run at least once before your first knowledge-capture cycle. See `.claude/skills/compile/SKILL.md`.

## Troubleshooting

**How to re-run kickoff.** INIT.md is not auto-replayable in v1.0. If you need to redo the interview, manually delete the skeleton files (`docs/governance/*`, `roadmap/*`, `CONTEXT.md`, `raw/YYYY-MM-DD-session-project-init.md`) before reopening the session. The harness does not detect partial kickoffs.

**How to add a wiki domain mid-project.** Manual edit. Update `project.yaml.wiki_domains`, create `wiki/<new-domain>/INDEX.md` with the empty-state convention from `docs/wiki-schema.md`, and append to any cross-references. Do not delete or rename existing domains without explicit operator decision — the directory preservation rule in `CLAUDE.md` is load-bearing.

**How to disable a capability after the fact.** Manual cleanup in v1.0. Remove the capability's runtime files (`.claude/skills/<name>/` and any docs/runtime artifacts), set the capability to `false` in `project.yaml`, and document the deprecation in a governance ADR. The deferred `/init` recipe (`docs/recipes/kickoff-orchestration/init.RECIPE.md`) discusses the eventual reconfigure-mode orchestrator that will automate this — until built, manual.

**The kickoff felt incomplete or rushed.** That's a signal. The point of a slow, paced interview is to surface gaps you didn't know you had. If 2a was unanswerable, the project isn't ready. If 2c had no constraints, the project hasn't earned its scope yet. Don't bulldoze through. Close the session, sit with the discomfort, and resume when answers come.

---

*v1.0 manual protocol. Eventual `/init` orchestrator: see `docs/recipes/kickoff-orchestration/init.RECIPE.md`.*
