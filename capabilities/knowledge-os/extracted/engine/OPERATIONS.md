# Memory Engine — Operations (validated practice)

> **Status:** distilled ONLY from what the engine actually did on the proving-ground fork
> (the dogfood fork), live-operations sessions 1–10 (2026-07-07 → 2026-07-10) — 39 build gates
> green before live-ops opened, then the loop ran on real content. Every practice below is cited
> to its source (the design brief or the ledger entry that recorded it happening); where the fork
> hit a wall, that wall is named in Honest Gaps, not papered over. Primary sources: the fork's
> `specs/memory-engine-v3-steady-state-operations-brief-2026-07-08.md` and
> `engine-verification-ledger.md` § LIVE OPERATIONS.

*verified-against: 3.0 (2026-07-24)*

## Who this is for, and the model in one paragraph

A project operator, or the Claude session orchestrating a compile cycle, opening this file with
no prior context on the memory engine. It assumes `docs/wiki-schema.md` §17 has been read (the
*contract*: derivation block, sensors, invariants) — this document is the *practice*. The engine's
job: get a new raw event from "written" to "faithfully absorbed into a wiki view, and a
substrate-different model has confirmed that faithfully" — with a mechanical census that proves
nothing was silently lost. Every stage below exists because some part of that sentence broke once
on the fork and got hardened. Cross-vendor VERIFY is not decoration: on the fork it caught real
omissions, fabrications, and stale contradictions — non-confirms are the system working, not
noise to route around.

## The loop

Run this sequence every time new raw events need absorbing. Steps 2–8 are what
`deploy/compile-v2.py` + `deploy/compile-backends.py` + the sensors mechanize; step 1 and the
authorization in step 5 are always human/operator acts.

### 1. Raw event lands

A file appears under `raw/`, frontmattered and dated, per `docs/wiki-schema.md` §2. Nothing
downstream happens automatically — the engine has no daemon. A session (or the operator) decides
it's time to run the loop.

### 2. `register-intake.py` — delta registration

Every event needs a typed registration record (`origin`, `event_class`, `asserts_corpus_state`)
before anything else can see it — ~17–19% of raw events have unparseable frontmatter, and the
registration record is the append-only substrate that carries typing for those too (spec §4).
`deploy/register-intake.py` diffs the raw population against the existing registration chain and
appends records for the delta ONLY — never re-mints, never touches an existing record. Origin is
derived mechanically (personnel-tagged events → `human`; session/observation-authored → `corpus`;
unparseable → `unknown`, non-blocking, surfaced as a data-quality note, upgradeable only by
explicit operator attestation — never auto-upgraded). The whole delta batch is pre-validated
before any record is written: one malformed record refuses the entire batch, never a silent
partial mint. On the fork's first real batch: 92 events registered 46 human / 44 corpus / 2
unknown, in one all-or-nothing append.

### 3. Routing / triage

The census (`deploy/staleness.py`) sorts every registered event into exactly one of seven
classes (an ordered, first-match-wins table — REF-SKIPPED, SUPERSEDED-UNCONSUMED, CONSUMED,
PENDING_NOOP_CANDIDATE, PENDING, UNROUTED, RESIDUE; spec §6). An event lands **UNROUTED** when
its tags/entities match no view's `subscribes.entities` — that is a routing gap, not a defect,
and it is never silent: it is a named, countable census bucket.

**UNROUTED handling.** Triage each UNROUTED event as one of: (a) **extend the vocabulary** — add
an alias or a new entity to `deploy/entities.yaml` so the event routes (operator-gated; the
fork's real gap here was structural, not a missing alias — an early routing function ignored
`entities.yaml` entirely, so confirm alias resolution is actually wired, not just declared); (b)
mark `compile: false` if the event is archival/process, not knowledge; (c) park it — a small,
genuinely-unrouted residue is honest and expected, not a defect to eliminate at all costs.

**`entities.yaml` governance.** Append-only and Directory-Preservation-style (`docs/wiki-schema.md`
§17.3): no entity or planned view drops or renames without explicit operator authorization.
`catalog.py` (ROUTE-1) is the integrity tripwire — run it whenever `entities.yaml` changes.

### 4. Compile plan

Author a plan: `{"items": [{"view": <path>, "events": [<raw paths>], ...}]}` — one item per view
needing a delta absorbed. **A plan over an already-registered event must mirror that event's
registered `event_class`/`origin`** — it cannot downgrade a registered judgment-class event to
`explicit`; `check_plan_precedence` refuses the run if it tries (hit on the fork's first
post-registration compile).

### 5. dispatch-check — the HUMAN-GATE

Before compile writes anything, `dispatch_guard()` decides the run's disposition. **Compile
(ABSORB) is AUTONOMOUS** — no gate. **VERIFY is a HUMAN-GATE**: satisfiable ONLY by a recorded
authorization artifact, never by standing memory of a prior "go ahead." The artifact must live at
`deploy/evidence/operator-*.md` (a structural class check — exact directory, no nested paths, no
traversal), and the code checks for a **verbatim quote** from that file actually being present
(e.g. `{"path": "deploy/evidence/operator-2026-0X-XX-....md", "quote": "go on everything"}`).
Anything else — a design-status note, a paraphrase, an unrecorded verbal grant — is refused
(`DispatchRefused`): on the fork, an agent once tried the wrong document class and was bounced.

### 6. Absorb via `compile-backends`

The absorb backend (LLM, dispatched per-view with the delta events + routing rules + the §9
untrusted-data rule) drafts new view text + a merge manifest + `corpus_support` (verbatim quoted
lines for any shipped-state claim). The orchestrator validates mechanically before journaling
anything: parses, manifest matches the real diff section-for-section, every `corpus_support` line
appears verbatim in its cited artifact, the deletion floor holds (headings never removed, body
never shrinks >70%).

**The derivation region is the engine's, not the author's (v3.0.29, closing backlog v3.0-69).**
A view CREATED by a run gets its engine-managed region minted by the absorb path, after
validation and before the write — authors neither write one nor are asked to. This closes a
silent, fleet-wide break in the verify chain's last link: `verified:` is stamped strictly inside
that region, so a region-less view could never RECORD a verification. Reproduced mechanically
2026-08-06 — a new view drew a `confirmed` verdict from the cross-vendor leg and the engine
recorded zero confirmations, a paid approval produced and then discarded. The minted shape is
`backfill-derivation.py`'s `render_region` (called, never re-implemented, so the two minters
cannot drift): tier T1, `consumed_status: legacy-assumed`, `verified: null`, empty
entities/subscribes, `origin_max` computed over the view's `sources:`. `backfill-derivation.py`
stays the migration path for EXISTING region-less corpora — run it once per instance, on a
worktree. `check-derivation` now reports region-PRESENCE (and refuses under `--gate`), closing
the blind spot that let the sensor call a tree clean while verification was impossible on it. **Absorb doctrine, matured through live catches (binding for every leg):**
(i) scoped totality (v3.0.29, closing backlog v3.0-63) — every load-bearing claim the plan's
**claim routing** assigns this view is represented or implied in it; when the plan declares no
routing for an event, the old rule stands unchanged: every load-bearing claim in EACH routed
view. The routing is the plan's deliberate split of a wide source across views (each claim owned
by exactly one view this run, or deferred with named targets into the receipt's
`pending_cascade`) — the engine refuses, pre-write, any claim owned by nobody, so narrowing a
view's scope can never silently drop a claim; (ii) diffs 100% event-traceable — add nothing the event doesn't establish;
(iii) reconcile what the event supersedes — a stale contradicting statement left in place is
itself an absorb defect; (iv) no cross-links unless event-established; (v) never forecast other
events; (vi) headings are immutable once written.

### 7. Cross-vendor verify

A substrate-different model (the fork defaults to `gpt-5.6-sol` via the bridge; routine T1 gates
on model-id difference, migration/design-gate work on vendor difference — spec §5) receives the
full event + the current view body and answers whether the absorption is faithful.

**The verifier's charge is plan-scoped (v3.0.29, closing backlog v3.0-63).** When the compile
record carries a claim routing, the packet embeds it (DECLARED CLAIM ROUTING section) and the
checker grades **two questions**: (1) does this view faithfully carry every claim it OWNS under
the declared routing — per-view fidelity to declared scope; (2) is any load-bearing claim of the
event absent from the declared routing altogether — enumeration completeness, rejected with
reason class `enumeration-incomplete`. A claim owned by a sibling view or deferred is declared
scope, never an omission, so a correctly-narrowed view confirms even while siblings carry the
rest. The run-level union — every claim owned by exactly one view or named in `pending_cascade`
— is checked mechanically, pre-write, by the engine (`check_claim_routing`): a claim routed to
nobody refuses the whole run before anything is written. One verdict per leg, the same enum as
always; the reason sentence names which question failed. Records without a routing (older plans,
staged re-rides) keep the total-coverage charge unchanged — nothing is loosened either way.
**When every leg of a run rejects with the enumeration-incomplete reason, that is ONE plan
defect — fix the claim table and re-ride the run — never N article defects; an all-reject wave
of this shape is a plan-level correction, not grounds to doubt the verifiers or the articles.**

**The packet's "before" is always the view's real baseline, named (v3.0.29, closing backlog
v3.0-67).** The diff base is, in order: the last machine-verified state; else the last
operator-adjudicated state (below); else the view's real pre-absorb content — with reverted
runs' ghosts excluded, so a rejected-then-reverted creation can never make a later update read
as created-from-nothing. A genuinely NEW view verifies from empty and the packet says so in so
many words. Every packet's diff section opens by naming its baseline; the baseline advances on
machine-verification or on an operator set-aside ruling, and **never on a bare rejection**.

**Verdicts are data, not instructions** — a `revised`/`rejected` verdict is the honesty layer catching a real
defect (omission, fabrication, stale contradiction, over-certainty); adjudicate it through the
correction cycle (`--revert` → corrected answers → fresh `--run`; §7a below), never argue with or
route around it. Read the FULL verdict, not just the top-level field: a
`substrate-gated` outer verdict can still carry a substantive nested `bridge_verdict` (a real
`rejected` with reasoning) that every top-level tally otherwise misses (v3.0-23) — check both.

**No self-adjudication (2026-08-04; from a live incident in which a session ruled six external
rejections a "verifier defect" and closed them itself, with no rule saying who may).** The
session whose absorption was graded never rules a non-confirm verdict a verifier defect, a
false positive, or otherwise void — grading the grader is not its call. Exactly two
dispositions exist: (a) correct through the correction cycle (§7a), or (b) if the session
believes the verdict itself is wrong, **stop** — leave the run in its exit-1 state and put the
full verdict plus the contrary evidence in front of the operator. The operator is the only
party who may set a verdict aside, and that ruling is recorded — the shipped form (v3.0.29) is
`compile-driver.py --set-aside --seq N --view <path> --ruling "<the operator's words>"`, which
journals the ruling beside the rejected verdict and advances the view's verify baseline as an
**adjudicated** baseline (later packets name it "adjudicated <date> by operator ruling, not
machine-verified" — nothing is dropped, its status is named) — before any re-run. There is no
third disposition. The escalation
message itself is spec'd in the compile skill's exit-1 section and follows
`core/governance/CLAUDE.md` § Reporting to the operator (v3.0.25): plain words, the
verifier's reason sentence quoted verbatim inside them, the full record via a details tail —
a paraphrase alone is never the basis for the operator's ruling.

**Co-absorption / joint citation for multi-event spans.** Absorption-verify grades **per run**. If
a view accumulates content from several events across separate compile runs without an
intervening confirmed verify, later runs on it are unverifiable alone — cite the whole
accumulated span (prior events + the new one) in ONE verify run so the verdict grades the joint
claim. On the fork this closed three "parked" view-spans, one in a single joint leg — and measured
cheaper per-event than solo verify (~0.6 legs/event on a 10-event joint span vs. ~1–2 solo).

**Corrections from verify verdicts (§7a — reworked 2026-08-03).** A `revised` or `rejected`
verdict that demands a content correction is closed out in the **same** compile session — it is
never routed as a new raw event. The mechanism is the driver's **correction cycle**, not a direct
edit (the old wording, "fix the view directly, re-run the verify leg," named an operation no
driver mode performs — a completed verdict is a terminal disposition, so `--reverify` rightly
declines it, and the first live all-rejected run ended with hand-edited, unverified views):
`compile-driver.py --revert --seq N` reverts the run commit (journal record restored, revert
journaled — the rejection stays on the record), the answers are corrected **in the staging dir**
under the same per-view isolation that authored them, and a fresh `--run` re-absorbs them — so the
correction re-rides the full validate/absorb/verify road and its confirming verdict is a fresh
verify record, never an overwrite. This is a faithfulness fix: the triggering event already
established the fact, the view just failed to render it faithfully, and the verdict caught that.
**New information** — a fact the triggering event never established — is a different case
entirely: that goes through the normal pipeline as its own raw event (step 1), never folded into
a correction.

### 8. Census green check

Re-run `deploy/staleness.py`. It must show: the events just compiled reclassified from
PENDING/UNROUTED to CONSUMED; zero collateral change to any other event's class; `problems: []`;
`new_holes: []`. The census additionally projects the compile-v2 run journal (not just legacy
receipt prose), gated by a chain-integrity check (`compile-core.check_chain`) before it trusts
the journal at all — a tampered or broken journal is refused, never silently trusted.

### Stage-only commit, worktree-per-shard discipline

Every compile/verify run commits stage-only (per-path adds; a bare directory add is refused) —
`deploy/check-run-diff.py --sections` passes on every commit the loop produces. Run each shard (a
batch of events being absorbed together) in its own linked worktree/branch, not the main working
tree — this let the fork park half-closed spans, retry, and diagnose without touching the trunk,
and keeps a refused mid-plan run's leftover working-tree mutations (see Failure handling)
contained to a throwaway tree.

## The driver: `deploy/compile-driver.py`

**Superseded 2026-07-28 (backlog v3.0-65, build spec `harness-v3.0/specs/compile-engine-wiring-
build-spec-2026-07-28.md` § B-1):** the throwaway-driver pattern below is no longer how a compile
RUN is fired. The shipped, self-testing driver is `deploy/compile-driver.py`, and it is `/compile`'s
single entry point:

```
py deploy/compile-driver.py --run --root . --staging <dir> \
   --authorization deploy/evidence/operator-<...>.md [--sections]
py deploy/compile-driver.py --reverify --root . --seq N --staging <dir> \
   --authorization <path>                               # transport failed; absorption stands
py deploy/compile-driver.py --revert --root . --seq N [--reason TEXT]
                                        # adjudicate a non-confirm verdict, or complete a
                                        # crashed run's revert; correction re-rides --run (§7a)
py deploy/compile-driver.py --set-aside --root . --seq N --view PATH --ruling TEXT
                                        # the operator's OTHER adjudication (§7): journal their
                                        # set-aside ruling; advances the view's baseline as
                                        # adjudicated (v3.0.29)
py deploy/compile-driver.py --reconcile --root .        # maintenance: is a run unterminated?
py deploy/compile-driver.py --self-test
Exit: 0 clean | 1 validation/gate failure | 2 inconclusive | 3 lock held
```

What it guarantees, and why it exists rather than a per-session script: `--authorization` is
REQUIRED and validated **before anything is written** (a missing, out-of-class, revoked, or
non-covering artifact refuses with nothing written and nothing committed); **there is no
`--no-verify` flag** — every live absorption rides a verify leg (standing invariant 4); a verify
leg that completes with a non-confirm verdict is journaled data (exit 1, no auto-revert — the
operator adjudicates it with `--revert` and the correction re-rides `--run`, §7a), while a
verify leg that does NOT complete auto-reverts the run commit, journals the revert, and preserves
the staging dir; and startup reconciliation refuses new work while the newest run record lacks a
terminal verify disposition (the crash-window case). Read that file's module docstring for the
normative statement of each rule.

*Fork-side edit 2026-07-28; the template-side mirror (`extracted/engine/`) is owed.*

### The primitives (still current, and what the driver calls)

The primitives documentation below stays accurate — it is what `compile-driver.py` itself does, and
it is still the right shape for the one job the driver does not do: **generating and attesting a
staging dir** (`emit_packets` + `stamp_dispatch`), which a session runs once and discards. Do not
use it to fire a run.

```python
# run_event.py -- one compile pass through the guarded pipeline
cb = load("compile-backends.py")                          # import the shipped module
staging = ".batch-run/<tag>"                                # throwaway per-shard staging dir
manifest = cb.emit_packets(repo, plan, staging, routing_rules_text)
# ... write one answer JSON per manifest entry (the absorb backend's output) ...
cb.stamp_dispatch(manifest_path, model=..., vendor=..., identity_source=...)
#   ^ identity_source is REQUIRED (2026-08-05): operator-attested:<date> |
#     scheduled-invocation:<task> | attestation:<record> -- how the identity
#     was OBTAINED. Self-belief typed from memory refuses; see the compile
#     skill's Step 3a for the per-mode procedure.
backend = cb.FileHandoffAbsorbBackend(staging)
try:
    result = cb.run_guarded(repo, plan, backend)            # ABSORB is AUTONOMOUS -- no gate
except cb.DispatchRefused as e:
    ...                                                      # handle refusal, don't retry blind

# verify_event.py -- a separate invocation, over the seq run_event.py just produced
backend = cb.BridgeVerifyBackend(repo, dispatch_manifest_path=manifest_path)
authorization = {"path": "deploy/evidence/operator-<date>-<slug>.md", "quote": "<verbatim>"}
result = cb.verify_run_guarded(repo, seq, backend, authorization=authorization)  # HUMAN-GATE
```

**Known traps:** an ambient `CROSS_VENDOR_BRIDGE_DIR` env var can silently point verify legs at a
stale, unattested bridge copy — unset it before a repo-local run.

**`VERIFY_TIMEOUT_MS` posture (v3.0-20 CLOSED 2026-07-13 — this supersedes the "not honored
everywhere / named residual" claim that stood here until 2026-07-28):** an unpinned caller's
`VERIFY_TIMEOUT_MS` export IS honored — `BridgeVerifyBackend.__init__` resolves it from the env
when no explicit `timeout_ms` is passed, defaulting to 180000 when the env is unset. An explicit
caller pin always wins and skips the env resolution entirely. A pin of `0` means "emit no
`--timeout-ms` flag", so verify-cli falls back to its own env/180000 default server-side — `0`
defers, it does not disable. `compile-driver.py` reads the env itself and passes an EXPLICIT pin,
defaulting to 540000 (the proven bridge posture from the v3.0-20/21 reviews).

*Fork-side edit 2026-07-28; the template-side mirror (`extracted/engine/`) is owed.*

## What green looks like

- Census: `problems: []`, `new_holes: []`; the just-absorbed events show CONSUMED; every other
  event's class is byte-for-byte unchanged.
- Journal: chain-integrity check passes (`prev_record_hash` links unbroken); every run's record
  present.
- Zero unverified absorption debt: no view carries content from an event that hasn't been through
  a confirmed (or honestly-parked, named) verify leg. The fork reached this state explicitly at
  live-ops SESSION 4 close-out, after closing its last parked spans via co-absorption — cite that
  as the bar, not an aspiration.
- Every non-confirm, checked by hand, was substantively correct (the fork's running tally across
  ~60+ live verify legs over two verifier generations: essentially zero false-positive
  non-confirms).

## Failure handling

- **A refusal is the system working, not an error to route around.** `DispatchRefused`,
  `EnlargementViolation`, a rejected/revised verify verdict, a plan-precedence refusal — each
  names exactly what's wrong. Fix the named thing; never loosen the gate.
- **Census non-green = stop and adjudicate.** Do not compile further or force a re-run; a
  `problems` or `new_holes` entry means something doesn't add up — find out why first.
- **A mid-plan `ValidationError` never touches the working tree.** The run loop validates every
  plan item first and only writes+commits after all of them pass, so a later item's
  `ValidationError` refuses the whole run before a single view file changes — nothing journaled,
  nothing committed, nothing dirty to revert. (Historical note: before the two-phase fix,
  validate-as-you-write could leave an earlier item's edits uncommitted in the working tree;
  closed by backlog v3.0-22.)
- **Read the nested verdict.** A `substrate-gated` verify record can still carry a real
  `bridge_verdict` underneath — don't let the outer gate swallow a genuine `rejected` (v3.0-23).

## Honest gaps

Real, current limits — not deferred-forever, but not built. Do not claim otherwise.

- **R-1 · the session-loop candidate harvest/register/promote pipeline shipped 2026-07-23.** The
  frozen test plan's ongoing-writer gap is closed: `candidates.py`, `harvest-candidates.py`,
  `register-candidates.py`, `check-candidates.py`, and `promote-candidate.py` carry a staged span
  from harvest through signing-gated promotion — an `ARMED` artifact from a pinned SSH key is
  required before a span becomes an operator-authored event, refused on adversarial classes
  (unsigned, non-pinned key, wrong candidate/disposition, replayed artifact), single-use,
  TOCTOU-proofed, and crash-replay exactly-once (`drill-r1-acceptance.py --self-test`, 70/70).
  Ships **UNARMED** — `signing-config.yaml.example` carries no pinned fingerprint,
  absence-is-the-fail-safe, same doctrine as `origin-config`. Cite backlog v3.0-21 (PARTIALLY
  RESOLVED) and `HARNESS-CHANGELOG.md` Theme J, Session A. Shipping unarmed is the *invariant*
  (a fresh instance must never arrive armed); *staying* unarmed is not — each instance owes a
  dated arm-or-don't decision, kept dated by the `r1-arming-review` trigger-register seed row
  (backlog v3.0-70). Arming is an operator-only key act per the `TO ARM` steps in the example
  file.
- **Re-baseline is not an operator-runnable operation.** If the census baseline (ACC-1) ever needs
  re-minting for this instance, there is no built "re-baseline" command — a manual, code-level
  act today. Cite backlog v3.0-21.
- **Golden descriptors are fork-specific and must be authored per instance.** The fork's alias-
  reachability golden fixtures (`deploy/descriptors/`) encode its own entity vocabulary; a fresh
  instance starts with none and authors its own as `entities.yaml` grows.
- **Vocabulary-aging sensors (ROUTE-3/LLM-4) are spec'd, not built.** UNROUTED-triage today is the
  manual step in §3; the census's UNROUTED count is the interim signal. Cite backlog v3.0-21.

## References

`docs/engine/memory-engine-v3-spec.md` (the frozen contract: §4 ledger, §5 view/derivation, §6
journal/census, §7 judgment verbs, §9 security, §11 read path, §13 phases); `docs/engine/
memory-engine-v3-test-plan.md` (the gate definitions this practice satisfies); `docs/engine/
memory-engine-v3-tool-grant-tcb-spec.md` + `docs/engine/memory-engine-v3-autonomy-model-spec.md`
(the security/autonomy model behind the step-5 HUMAN-GATE); `docs/wiki-schema.md` §17 (derivation
block + sensors); `harness-backlog.md` v3.0-20/21/22/23 (every named gap above, verbatim).
