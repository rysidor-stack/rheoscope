---
name: sweep
description: Run every read-only health check at once and give one plain-English briefing — the janitorial umbrella so no sensor is ever invoked by hand.
---

# /sweep — The Check-Everything Button

*verified-against: 3.0 (2026-07-20)*

/sweep is the "check everything, tell me what matters" button. It is read-only, always safe,
and can be run as often as you like — it changes nothing. It exists so the operator never has
to remember whether today's question is a `/doctor` question, a census question, a conformance
question, or an `/audit` question: one command, one briefing, in plain English. There is no
trust reason any of the checks below stay manual — the only reason they ever were is that
nothing had wired them together yet.

## What it runs

In order, all read-only, each **degrade-never-block** — a broken checker is itself a finding,
never a crash that stops the sweep. Every script below is invoked the same way `/flight-plan`
Steps 5.6–5.9 already invoke its sensors: try interpreters in order until one runs (`py` →
`python` → `python3`), and if a script is simply absent from this project (a capability was
never enabled), skip it and say so — that's a NOTE, not a finding:

1. **Environment + wiring health** — `python .claude/skills/doctor/doctor.py
   --fast-selftests`. Read the exit code plus every `FAIL`/`WARN` line
   (`.claude/skills/doctor/SKILL.md`). The flag runs sensor self-tests as a stated
   date-keyed rotation instead of the full 60-second-per-sensor battery (v3.0.27) — the
   report says exactly what ran; init-end and manual `/doctor` checkups stay full-battery.
2. **Knowledge-base conservation census** — `python deploy/staleness.py`, if present
   (knowledge-os only).
3. **Session-candidate conservation census** — `python deploy/check-candidates.py`, if
   present (R-1 only) — the same category of check as the census above, one entry later:
   every staged session candidate has a ledger entry and every open candidate's file is
   still there.
4. **Structural sensors**, each if present: `python deploy/check-frontmatter.py`;
   `python core/governance/check-reference-integrity.py --sweep <governing docs>` — ONE
   combined invocation (v3.0.27): the full-tree citation sweep (every path-shaped citation
   in every shipped doc, py docstring, and FIX string, classified against both layouts,
   grouped shipped-docs-first; dangling citations feed Needs-your-attention grouped by
   phantom target, not one row per citer) plus the working-tree classes for the governing
   docs — the doc list is `core/governance/CLAUDE.md` plus every `governance_docs[].path`
   from `project.yaml`, per `core/skills/flight-plan/SKILL.md.template` Step 5.6. (The old
   two separate invocations re-walked the same tree.) Do NOT run `check-derivation.py
   --gate` here — step 1's doctor already ran it (its check 9); read doctor's line instead
   of re-running (v3.0.27, the same-session dedupe rule). `python deploy/check-knowledge-debt.py
   --check` (compile:false canonical-integrity + REVIEW aging debt — flight-plan Step 5.10).
5. **Behavioral-manifest structure** — `python deploy/check-manifest.py`, if present.
6. **Conformance SMOKE tier** — only if some `manifests/<surface>/MANIFEST-INDEX.md` names a
   smoke set. Replay it per `/conformance`'s smoke rules (`core/skills/conformance/SKILL.md`)
   — but skip its "run check-manifest.py first" preamble when step 5 above already ran it
   this sweep (v3.0.27; same tree, minutes apart, identical result). /sweep never runs the
   FULL tier — that stays a deliberate, separately-invoked act.
7. **Evidence-vs-roadmap spot check** — the read-only half of `/audit`: look for roadmap
   assumptions whose Status column contradicts current wiki evidence. Flag what's found; write
   nothing — no REVIEW.md entries, no roadmap edits. That's `/audit`'s job, not this one's.
8. **Decision-inbox staleness** — `python deploy/decision-inbox.py --check`, if present. This
   only reports whether `DECISIONS-PENDING.md` is stale against its sources — /sweep never
   regenerates it, the same read-only rule as every other check here. A stale inbox goes to
   **Watching** ("the decision list is a step behind and will refresh on the next scheduled
   run"), NOT Needs-your-attention (v3.0.26 — a projection being a day behind is a fact with
   zero decisions in it, and reporting it as attention had it echoing back into the very
   inbox whose staleness it described). Escalate to Needs-your-attention only when the
   staleness has persisted across two or more consecutive sweeps — that means the
   regeneration loop itself is broken, which IS someone's problem.
9. **Workspace hygiene** — `python deploy/check-workspace.py`, if present. A workspace with no
   `projects.yaml` registry at its root is a NOTE ("workspace governance not adopted here"),
   never a failure — most projects haven't opted into `core/governance/WORKSPACE.md` yet.
10. **Deadline register** — `python deploy/check-deadlines.py`, if present. Dated
    rows due within 30 days or already past their date flow into the briefing's
    Needs-your-attention section; watched `if` conditions flow into Watching — the register
    doesn't evaluate a condition itself, it only keeps it visible until a human does.
11. **Mirror/instance parity** — `python deploy/check-parity.py`, if present. A project with no
    `template_path` set in `project.yaml` has nothing to compare against — that's a skip-note,
    not a finding.
12. **Template-update check** (backlog v3.0-59) — `python core/governance/check-template-updates.py --check`,
    if present. Exit 1 means updates available — a Needs-your-attention line naming the
    newer tags and pointing at `core/onboarding/UPDATING.md`; exit 2 (offline) is a
    skip-note; "not configured" on a pre-v3.0.12 instance is a Watching note with the
    backfill pointer, not a finding. Network: one `git ls-remote`, read-only.
13. **Top-level layout honesty** (backlog v3.0-54c) — list the tracked top-level directories
    (`git ls-tree -d --name-only HEAD`) and compare against the documented layout
    (`TEMPLATE-README.md`'s file-layout table plus `docs/wiki-schema.md` § Artifact homes).
    Any tracked top-level directory in neither place is an **informational** finding, never
    blocking: an unrecognized directory usually means an artifact class that needs a declared
    home — an instance invention to adopt or route, not a mistake to delete. Name each one in
    the briefing's Watching section with a one-line guess at what it holds.
14. **Trigger register** (backlog v3.0-64) — `python deploy/check-triggers.py`, if present.
    Declared conditions → proposed actions, propose-only by design: each MET row flows into
    the briefing's Needs-your-attention section in the standard three-sentence form (what
    condition fired, what it proposes, and that the system can run the proposed action next
    session with a yes); evaluated-but-unmet rows flow into Watching as a count only. A
    schema violation (unknown predicate or authorization value — the sensor exits 1) is
    itself a Needs-your-attention item, never a silent skip. Distinct from step 10:
    deadlines are dates a human owns; triggers are conditions the script evaluates itself.

15. **Verify-routing register** (backlog v3.0-77) — `python deploy/check-verify-routing.py`,
    if present. Asserts every verify-leg routing surface still carries its declared tier
    anchor (`deploy/verify-routing-register.yaml`); a missing anchor is the tier-promotion
    class that produced the 2026-07-29 incident and lands as a Needs-your-attention item
    verbatim (the finding text names the row, the file, and the authorizing decision-ref).
    SKIP (register not adopted, or PyYAML absent) flows to Watching as "routing unwatched".

16. **Operator-file prose scan** (v3.0.25) — `python deploy/check-briefing-format.py
    --prose-scan <file>` over each operator-read projection present at the root:
    `SESSION-BRIEFING.md`, `DECISIONS-PENDING.md`, plus `SWEEP-BRIEFING.md` when a prior
    scheduled run left one. This mechanically enforces the reporting contract's floor
    (`core/governance/CLAUDE.md` § Reporting to the operator) on files this sweep does not
    itself author: raw sensor codes (`[PASS]`/`[FAIL]`/`[WARN]` lines), script filenames in
    prose, and placeholder tokens are findings — a session wrote engineer output into an
    operator file, and the fix is a rewrite of that file by the session that owns it.

17. **The pending list — every retirement and trust-surface change, outstanding until you
    have read it** (v3.0.36 backlog v3.0-98(b) → whole class v3.0.46 / v3.0-120 → the
    DURABLE pending list with batched acknowledgement and the missed-sweep alarm, v3.0.50 /
    ADR #11 condition 4 as amended 2026-08-22, backlog v3.0-139). Three mechanical moves,
    in this order, when `deploy/pending.py` exists:

    (a) **Heartbeat, first thing in the sweep:** `python deploy/pending.py --root .
    --heartbeat open --run-id <sweep-run-id>` (any unique id — the UTC timestamp is fine).
    This row, and its closing twin in (c), are the "previous sweep receipt" every other
    step refers to (`receipts/pending/sweeps.jsonl`; the term was undefined before
    v3.0.50). A sweep that opens and never closes is a FAILED cycle the observer alarms on.

    (b) **Render:** `python deploy/pending.py --root . --render` and put its table in the
    briefing verbatim: one row per pending item — **retirements** (the journal record's
    seq, view, proposal digest, whether it was PUBLISHED — by your promote action or a
    verified tag — or is an UNPUBLISHED PROPOSAL that some path journaled without you) and
    **trust-surface changes** (the class in `core/security/hooks/trust-surfaces.txt`; the
    hooks dir, `deploy/safe-allowlist.yaml`, `deploy/evidence/operator-*.md`,
    `deploy/rulings/**`, `deploy/trust.py`, `retire.py`, `promote.py`, `pending.py`, the
    three HUMAN-GATE consumers, `.claude/settings.json`), each with its commit, parent,
    author, date and the paths touched — plus any **alarm** the nightly observer wrote
    (missed observation window, failed cycle). The list is RECONSTRUCTED from git objects
    on the production branch every run, never read from a file a session could edit: a
    deleted journal record still appears, a removed acknowledgement reopens its item.
    Then the deltas, as before: for each pending trust-surface item quote the added and
    removed lines verbatim (`git diff <parent>..<commit> -- <path>`), and for each pending
    retirement the span title(s), bytes moved, and the destination (the full preimage is
    `python deploy/retire.py --show <digest>`). Alongside, the class-wide table from
    `python deploy/trust.py --root . --report` (HEAD-identity, signature status, the
    recorded authority mode, the pin) as the per-surface state line.

    (c) **Acknowledge, then close — attended sweeps only:** after the briefing has been
    written, `python deploy/pending.py --root . --ack --run-id <id> --briefing
    SWEEP-BRIEFING.md` (or whatever file the briefing landed in), then `--heartbeat ok
    --run-id <id>` (or `failed` if any step above could not run). The ack rows record that
    these items were SHOWN to the operator in this briefing — reading the sweep IS the
    acknowledgement ("acknowledge in the sweep, nothing else"); nothing is asked. An
    UNATTENDED sweep (the scheduled wrapper sets `RHEOSCOPE_UNATTENDED=1`) runs (a) and (b)
    and the closing heartbeat but `--ack` REFUSES — the items persist until a sweep you
    actually read, which is what makes "unread item persists" true rather than claimed.
    These receipt-class rows (`receipts/pending/*.jsonl`, append-only; commit them with the
    briefing) are the ONE documented exception to this skill's read-only rule: the sweep
    records that it ran and what it showed, nothing about the project's content.

    **NEEDS-YOU items, phrased per the reporting contract:** an UNPUBLISHED retirement
    proposal on the branch ("a content retirement was journaled without your promote
    action — if you did not run `promote.py`, a session wrote it by an alternate path:
    inspect with `git show`, then either promote it from your terminal or revert the
    record"); a surface that is not HEAD-identical ("someone edited X in the working tree
    and did not commit it — if it wasn't you, restore it with `git checkout -- X`"); under
    `required` a surface whose newest commit is not operator-signed ("re-commit it with
    `git commit -S`"; under `visible` no signature is expected — the pending row IS the
    review); **no authority mode recorded** ("this project never chose between `visible`
    and `required`; content retirement is disabled until you add the line to
    `project.yaml`"); a non-presence key in `allowed_signers`; a **missed observation
    window or failed cycle** ("no sweep you read has closed in N days (window W) — changes
    may be sitting unread; this one clears it"); and any ledger FINDING from
    `pending.py` (an acknowledgement naming no item in history, or dated before its item:
    "an acknowledgement exists that no sweep wrote — delete the row; the item reopens,
    which is the safe direction"). Every other pending row is a Worth-knowing item: the
    operator reads it, and that is the whole ceremony.

    Fallbacks: no `deploy/pending.py` but `deploy/trust.py` present → the v3.0.49 table
    (`trust.py --report`, quote deltas since the last heartbeat row or all) and a
    Watching line "pending list unavailable (pre-v3.0.50)"; neither → `git log -1
    --format='%h %an %ci %G?' -- <path>` + `git diff --quiet HEAD -- <path>` per class
    member. Nothing in the class present = one silent line skipped, never a finding.

18. **Egress log surfacing** (v3.0.47, backlog v3.0-134 / v3.0-136a) — if
    `.claude/egress-log.jsonl` exists: read the rows appended since the previous sweep receipt's
    timestamp (no prior receipt = all rows) and put ONE table in the briefing, grouped by host:
    host · calls · kinds (`egress` allowed-and-logged in an attended session, `egress-ask`
    asked in an unattended run, `destructive-deny`, `trust-deny`) · first/last seen · one
    example command. Hosts never seen in any earlier sweep are marked **new**. This is the
    after-the-fact review that replaced the per-call ask in attended sessions: the operator
    reads where sessions reached out, at their own pace. Phrased per the reporting contract: a
    NEW host is a Worth-knowing item ("sessions reached <host> N times this week — expected?");
    any `trust-deny` or `destructive-deny` row is a NEEDS-YOU item (a session tried to write a
    trust surface or run a destructive command — what, when, which command). Telemetry line
    (v3.0-136a): asks since last sweep and the running total; an ASK tier nobody ever declines is
    a rubber stamp — the count is what makes that visible. Log absent = one silent line skipped.
    The table is mechanical, never summarized from memory — build it with:
    `jq -r --arg since "<prev-receipt-ts>" 'select(.ts > $since) | [.host, .kind, .ts, .command] | @tsv' .claude/egress-log.jsonl | sort`
    then group by host (`cut -f1 | sort | uniq -c`), and the telemetry line with
    `jq -r 'select(.kind=="egress-ask") | .ts' .claude/egress-log.jsonl | wc -l`. Stated
    boundary (inherited from v3.0.43): the hook logs egress-SHAPED commands — named tools,
    PowerShell egress cmdlets, inline `-c`/`-e` with a network token; a script file that
    reaches the network (`python build.py`) was never in the tier and produces no row. The
    upgrade path that closes that is OS-level per-process egress control, recorded in the
    2026-08-22 five-pass run, not a regex.

This ordering follows the engine's own loop doctrine (`docs/engine/OPERATIONS.md` § "The
loop"): readiness before content, structural sensors before behavioral ones, deterministic
checks before anything judgment-based — cheapest, most-foundational signal first, so a reader
never has to sit through a slow behavioral replay to learn the environment itself is broken.

**Know your own reflection:** if you read `.claude/sweep-schedule.log` or
`receipts/pending/sweeps.jsonl` during the sweep, the unfinished `sweep run` / `open` entry at
its tail may be THIS run's own opening line — a run-in-progress marker, not a crash. Only a
*previous* run's opening line with no matching `sweep done` / `close` row counts as a failed
run worth reporting (and `pending.py --observe` applies the same rule with a 6-hour grace).

## The briefing

The briefing is the ONLY output the operator sees — not raw tool output, not file paths in the
sentence. Plain English, three sections, in this order:

**All clear:** one sentence naming what was checked and that it's healthy. Counts are fine
("6 checks ran clean"), and so are plain check-CATEGORY names ("environment", "workspace
hygiene", "the deadline register", "structural checks", "spec-file health", "report
spot-check" — the vocabulary the skeleton below uses); what's not fine is script/tool
filenames and internal-schema vocabulary ("check-frontmatter.py", "frontmatter", "sha256").
(Clarified 2026-07-23: the first certification pass of this briefing's behavioral manifest
caught the earlier wording — "jargon and check names are not [fine]" — contradicting the
skeleton's own category-name usage; category names were always the intent. Renamed
2026-08-05, manifest amendment A2: the plain-language sweep found three of the old sanctioned
categories — "structural sensors", "manifest structure", "conformance smoke" — were
themselves repo-jargon a stranger can't parse; the sanctioned list now carries their plain
replacements above.)

**Needs your attention:** numbered items. Each item is two to four sentences that together
state: what's wrong, in business terms; what happens if it's ignored; and what the fix is,
plus whether the system can do it itself next session with a yes. (Amended 2026-08-05, A2:
the old exactly-three rule forced padding on naturally-two-sentence items — the three ROLES
are required, the count is a range.) No file paths in the sentence itself — paths go in a
small "(details: path)" tail at the end of the item.

**Watching:** declared/expected reds — rows that are red by design because a feature amendment
is open, a capability was never enabled, or a smoke set hasn't been named yet. This is how the
operator learns the difference between "planned work" and "a problem."

If any check emits output the skill-runner doesn't recognize or can't classify, that confusion
is itself a Needs-your-attention item. Never drop it silently, and never guess at what it meant.

A skeleton, for shape only (a real briefing names real things, never placeholders like these):

```markdown
**All clear:** 6 checks ran clean — environment, census, structural checks, spec-file
health, report spot-check, and roadmap-vs-evidence all healthy.

**Needs your attention:**
1. The environment check found a missing tool the cross-vendor second-opinion feature needs.
   Until it's installed, that feature won't work, though nothing else in the project is
   affected. Installing it fixes this — the system can't do that step itself.
   (details: `codex-auth` in the doctor report)

**Watching:**
- Two spec checks are red because a feature change is deliberately in progress —
  expected, not a problem.
```

## When it runs

- **Manually**, anytime — it's always safe and changes nothing.
- **Automatically at session start**, when a session begins with substantive work. Sessions
  SHOULD open with `/sweep` instead of ad-hoc `/doctor` or census calls.
- **As the payload of a scheduled standing run** — see Scheduling below.

## Scheduling (recipe, not activation)

A nightly scheduled run is a recipe, not something this skill turns on by itself: a scheduled
task or cron entry invokes a headless session whose entire prompt is "run /sweep and save the
briefing to SWEEP-BRIEFING.md, overwriting."

That's the whole recipe — /sweep itself needs no changes to support it. The briefing save
belongs to the WRITE-SIDE scheduled session, never to `/sweep` itself: a direct, manual
`/sweep` invocation still performs zero writes — the read-only rule above stands unchanged.

Turning this on is an **operator decision**, not a default. It creates standing automation and
spends model time on a fixed schedule whether or not anything is wrong. Nothing here activates
it.

Write-side automation — auto-compile, or anything that touches the wiki on a schedule — is a
separate concern, governed by the reversible-cutover runbook, and is explicitly NOT part of
`/sweep`. This skill only ever reads.

## What this is NOT

- **Not a fixer.** /sweep changes nothing in the project, ever — every check it runs is
  read-only. The one documented exception (v3.0.50, step 17): it appends its own heartbeat
  and acknowledgement rows to `receipts/pending/*.jsonl` — a receipt that it ran and what it
  showed, never a change to content, config, or a trust surface.
- **Not `/compile`.** It writes nothing to the wiki, no matter what it finds.
- **Not the full conformance sweep.** Step 6 above is smoke tier only; full tier stays a
  deliberate, separately-invoked act at freezes and certification.
- **Not a substitute for the flight-plan briefing.** `/flight-plan` is about YOUR work in
  flight — deltas, blockers, next actions. /sweep is about the SYSTEM's health. When a sweep
  ran earlier in the SAME session, or a sweep briefing file is fresh (<24h), `/flight-plan`'s
  sensor steps MUST point at those results instead of re-running every check (v3.0.25 — the
  double battery was the largest fixed time cost of every session open); it re-runs only a
  sensor whose subject tree this session has since written to.

## Where it sits

/sweep is a core skill, peer to `/doctor` and `/conformance` — it doesn't replace either, it
runs both (plus the other checks above) and reduces them to one briefing. Where `/doctor`
reports on the environment alone and `/conformance` reports on manifest-to-build conformance
alone, `/sweep` is the umbrella over every read-only check in the template, so no sensor is
ever invoked by hand.
