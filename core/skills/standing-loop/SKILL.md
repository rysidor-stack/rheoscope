---
name: standing-loop
description: The scheduled write-side session that runs the knowledge housekeeping end to end — sweep, decision-inbox regeneration, and a compile-on-branch when raw content is waiting — leaving the operator only yes/no absorption moments. Phase B discipline — everything on a branch, nothing merges itself.
---

# /standing-loop — The Write-Side Autopilot (Phase B: everything on a branch, nothing merges itself)

*verified-against: 3.0 (2026-07-21)*

/standing-loop is the scheduled session that does the knowledge housekeeping end to end and
leaves the operator only yes/no moments. Where `/sweep` is the read-only check-everything
button, /standing-loop is what runs the write-side chores /sweep can only flag: it reads the
briefing, regenerates the decision inbox, and — when there is unabsorbed raw content and no
outstanding compile already waiting — runs the compile loop on a disposable branch. Nothing it
does ever reaches the project's default branch by itself. Plain English throughout, same as
every other operator-facing surface in this harness: no sentence here should need a second
reading.

## Two write classes

Everything /standing-loop touches is one of two kinds, and the distinction is load-bearing:

- **Report artifacts** — `SWEEP-BRIEFING.md`, `DECISIONS-PENDING.md`. These are derived
  projections, never hand-edited by anyone, the same way a census output is derived. The loop
  may regenerate these directly at the project root, on the default branch, every run — there is
  nothing to protect here because the sources always win and the file is disposable by
  definition.
- **Truth** — `wiki/`, `raw/`, the roadmap, and every index or health file compile touches. The
  loop only ever touches these on a dedicated branch named `standing-loop/compile-YYYYMMDD`,
  never on the default branch, and it never merges that branch into anything. Truth changes only
  when a human says yes.

Confusing the two is the one mistake this skill exists to make structurally impossible: report
artifacts regenerate freely because nothing is lost if they're wrong; truth only ever changes
behind a branch and a human yes.

## Activation gate

Before ANY run does work, three things are checked mechanically. Any one missing means the loop
reports "not armed" and exits without writing anything — not even a report artifact.

1. **The rollback rehearsal receipt exists** — a file matching
   `deploy/evidence/rollback-rehearsal-receipt-*.md`, recording a rehearsal THIS instance
   actually ran. No receipt, no run. The rehearsal, conducted on a **scratch clone of this
   project** (never the live tree), is three rounds:
   - **Round 1 — clean abort:** create a `standing-loop/compile-<date>` branch, commit junk on
     it, delete it (`git branch -D`); verify the baseline is byte-identical
     (`git status --porcelain` empty, `git rev-parse HEAD` unchanged).
   - **Round 2 — crash-recovery abort:** same branch plus uncommitted damage in the working
     tree; recover with `git checkout -- .` + `git clean -fd` + switch away + `git branch -D`;
     verify as in round 1.
   - **Round 3 — post-merge revert:** merge the branch, then `git revert -m 1 <merge-sha>
     --no-edit`; verify the content tree matches the pre-merge baseline byte-for-byte.
   The receipt records the date, the scratch clone's path, and each round's actual commands
   and verification output. A receipt not backed by a rehearsal this instance ran is
   **fabrication** — its evidence comes from the run itself, and the arming review checks that
   it names this project's clone. (The dev fork's receipt and its millisecond timings are the
   fork's own; they prove nothing about your instance and must never be copied.)
2. **A committed operator authorization artifact exists** — `deploy/evidence/operator-standing-
   loop-*.md`, carrying the operator's verbatim activation quote (the HUMAN-GATE pattern: a
   recorded, quoted "go ahead," never standing memory of one). It must be committed — predating
   the run — so a same-run forgery can't satisfy it. No artifact, no run.
   **Minting it is a first-need, in-session ceremony — never operator homework** (same pattern
   as the compile skill's dispatch-grant question): at arming time, ask the operator ONE plain
   question naming what arming means — scheduled unattended write-side runs; compile on
   disposable branches; nothing merges itself; the supervised first run (item 3). On the yes,
   YOU mint `deploy/evidence/operator-standing-loop-arming-<date>.md` (it matches the glob
   above) carrying the verbatim quote, the date, and the item-3 first-run commitment; commit
   it; proceed. Arming is a SEPARATE consent from the cross-vendor dispatch grant the loop's
   compile step rides — if the project has no dispatch grant yet, the compile skill's own
   first-contact question fires at the same moment: two consents, two artifacts, each in the
   operator's own words. Arming never silently inherits, and never fabricates, either one. A
   no is recorded as a dated decision (the `standing-loop-arming-review` register row keeps it
   dated).
3. **The first armed run is supervised** — the same operator authorization artifact from item 2
   must also record that the operator will watch that first run live, or review its complete log
   immediately after, before the schedule is left to take over unattended on later ticks. A
   one-time condition on the *first* run only; once satisfied and recorded, later ticks don't
   re-check it.

Note: signature-gating this artifact (a pinned operator signing key, verified on the introducing
commit) is the named hardening R-1's design review established for promotion gates of this
shape — the same TOCTOU-resistant, verified-blob-binding pattern. Adopt it here once R-1 lands;
until then, "committed and predates the run" is the control this gate relies on.

**Staged-unarmed, not indefinitely-unarmed (backlog v3.0-70).** Unarmed-by-default is this
skill's *shipping* state, never its resting state. An instance either arms it by the acts above
or records a dated decision not to run it — a trigger-register row (`standing-loop-arming-review`,
seeded in `trigger-register.yaml.example`) keeps that decision dated instead of drifting, and
once armed becomes the re-affirmation cadence. Contrast the R-1 signing gate, whose
unarmed-by-default is the same shipping invariant with the same per-instance graduation duty
(`r1-arming-review`).

## The run, in order

1. Run `/sweep` (read-only) to produce `SWEEP-BRIEFING.md`.
2. **Parked-handoff retry (v3.0-78):** scan the handoff envelope for current-protocol
   folders at `status: answered, close: pending` (a T1 headless close leg died) or
   `status: open` with a `packet-round-N.md` but no `output-round-N.md` (an answer leg
   died). For each, re-dispatch the leg per `/handoff` Step 0 (bridge transport; the
   standing handoff dispatch grant covers it — no per-send approval. No grant on file →
   do NOT dispatch and do not ask, nobody is present: the leg stays parked with the reason
   in the run summary, and the next interactive `/handoff` asks its one grant question). A
   close deliberation that
   lands here CANNOT take the operator's lock yes (nobody is present): leave the
   deliverable in the folder, clear nothing — the next interactive `/handoff` invocation
   surfaces it for the single yes. A leg that fails again simply stays parked; report it
   in the run summary as information, never as an operator task.
3. Regenerate `DECISIONS-PENDING.md` via `deploy/decision-inbox.py`.
4. **Dirty-tree guard:** if `git status --porcelain` is non-empty (the operator left work in
   flight), SKIP the compile step entirely this tick and report it — never branch from, stash, or
   touch a dirty tree. Otherwise, if unprocessed raw content exists AND no prior standing-loop
   branch is still outstanding (the **single-outstanding-branch rule** — never stack a second
   unabsorbed compile; report the conflict instead and stop there): create
   `standing-loop/compile-YYYYMMDD`, run `/compile` on
   that branch per its own skill, regenerate indexes and health, run `/audit`, run the census;
   commit on the branch with a plain-English summary. Never merge. Never push the default
   branch. V1 deliberately runs ONE compile batch per tick on one dated branch; the runbook's
   per-shard worktree pattern applies once parallel compile shards return, and the
   single-outstanding-branch rule is its degenerate case (review fold 2026-07-21: declared
   narrowing, not an oversight).
5. Record what the branch would teach the wiki, either as a `DECISION-PENDING:` marker or
   directly in the compile branch's commit summary: "The wiki learned: `<one sentence per
   item>`. Absorb = say 'absorb it' in any session."
5b. **Dashboard reconcile (v3.0.26 — on the compile branch only, before its commit):** if
   step 4 ran a compile, update each affected flight plan's Layer-1 dashboard lines
   (Status / Next action / Last session) to match what the branch just did — a dashboard
   that still says "N raw files await /compile" after the compile IS the recurring
   briefing noise the operator flagged; the orchestrator that changed the counts owns
   the one-line reconcile (flight plans are direct-editable; `core/governance/CLAUDE.md`
   § Single-writer rule names the exception). No compile this tick → touch nothing.
6. Append a run summary to `STANDING-LOOP-LOG.md` (a report artifact, same as the two above).

A skeleton of a step-5 note, for shape only (a real run names the real branch and items):

```markdown
The wiki learned: the vendor MCP's rate limit is per-tenant, not global (raw/2026-07-22-...).
Absorb = say "absorb it" in any session.
```

## How absorption happens (the human moment)

The operator says yes, in plain language, in any interactive session — "absorb it" is enough.
That session then checks the branch against the runbook's Phase-B merge bar (review fold
2026-07-21): census green AND `/doctor` green AND `python deploy/check-run-diff.py` CLEAN on the
branch result. Any of the three failing means no merge — the loop deletes the branch next tick,
same as any other failed compile. Only when all three pass does the session merge it, delete the
branch, and record the decision. /standing-loop itself never merges anything, ever; the
interactive yes is the whole of the Phase-B human merge step. Nothing here shortcuts that: no
setting, no repeated schedule, no accumulated trust makes the loop merge on its own.

## Rollback

Exactly the rehearsed procedure, no improvisation: `git branch -D` for a clean abort, or
`git checkout -- .` + `git clean -fd` + branch switch + `git branch -D` when the branch also
carries uncommitted damage — rounds 1 and 2 of the rehearsal your own receipt records
(activation gate item 1). If census or `/doctor` comes back red on the branch at any point, the loop itself
deletes the branch and reports the failure — fail-closed. A broken compile never sits around
waiting to be absorbed by mistake; it is gone before the next tick, with the reason in the log.

Post-merge recovery is a different case, rehearsed separately (round 3 of the rehearsal):
once a branch has actually been absorbed and only later found bad, there's no branch left
to delete, so recovery is the rehearsed `git revert -m 1 <merge-sha> --no-edit` of the merge
commit — content restored byte-for-byte, but the history stays append-only (the merge and its
revert both remain in the log, rather than being erased). This is distinct from, not a
replacement for, the pre-merge branch deletion above.

## Spend + safety rails

- **One run per tick.** The loop does not catch up on missed ticks by compressing several
  runs into one.
- **Single outstanding branch.** See step 3 above — a second raw batch waits for the first
  branch to be absorbed or rolled back, it never stacks.
- **Kill switch respected.** If `core/governance/KILL-SWITCH.md` exists and any of its
  enabled-capability halt conditions are tripped, the loop exits at the activation gate, before
  touching anything.
- **Append-only-or-branch.** Every write this loop makes is either a new file, an append to a
  report artifact, or a commit on a disposable branch — never an in-place edit to truth on the
  default branch. This is a standing invariant of this skill, and this section is its home.

## Scheduling recipe

Mirror the nightly-sweep pattern already in place: a `.cmd` wrapper plus a Task Scheduler entry,
for example 05:30 daily, running just after the sweep. That's the whole recipe — nothing about
/standing-loop itself needs to change to run on a schedule.

Arming this loop SUPERSEDES the sweep-only schedule — the loop runs `/sweep` itself as step 1;
disable the "Rheoscope Nightly Sweep" task when this one activates (two schedules = double spend
and briefing races).

Stated plainly because it matters: creating that schedule, and committing the operator
authorization artifact from the activation gate, are BOTH operator actions. This skill ships
ready to run; it is armed only by those two acts, never by installing it.

## What this is NOT

- **Not an approver.** It never decides content is right — it compiles a candidate and waits.
- **Not a merger.** Phase B's whole point is that nothing merges itself; see § How absorption
  happens.
- **Not R-1.** Raw-event harvesting from live sessions is a separate build, landing separately;
  this skill only compiles what is already sitting in `raw/`.
- **Not active until armed.** Installing this file changes nothing by itself — see § Activation
  gate.

## Where it sits

/standing-loop is the scheduled counterpart to `/sweep` and `/compile`: `/sweep` reads and
reports, `/compile` is the manual, interactive compile any session can invoke, and
/standing-loop is what runs both of those (plus the decision inbox) unattended, on the schedule
described above, stopping at a branch every time truth would change. In the reversible-cutover
model this harness grew up on, that is **Phase B** — "compile on branch, human-merged" —
relocated onto a timer instead of a session someone remembered to run (Phase A is the same
work run attended and interactively; Phase C, standing autonomy where merges need no human,
is permanently gated and NOT a phase this skill takes). Nothing here merges, and nothing here
should ever be asked to.
