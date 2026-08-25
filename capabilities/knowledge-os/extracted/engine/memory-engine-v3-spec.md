<!-- Ported verbatim 2026-07-11 from the dogfood proving-ground fork, where the
     memory engine was built and proven. The fork remains the proving ground; template-bound
     updates flow fork -> template.
     [2026-08-01, backlog v3.0-89 operator ruling: venture identifiers genericized for template
      publication; the byte-verbatim certified original is preserved in the dev repo at
      capabilities/knowledge-os/design-history/certified-originals/ and in dev-repo git history.] -->

# Memory Engine — v3.2 Specification

**Status:** Build brief for the Project OS Harness **v3.0** fork. Not yet built.
Authored 2026-06-11 from three adversarial design passes against the v2.x reference instance
(the live business wiki), revised to **v3.1** after a two-substrate cross-vendor gate, and to **v3.2** after a
scoped same-family re-pass on the rebuilt sections (verifier: Claude Fable 5; full record:
**memory-engine-v3.1-scoped-reverify-results.md**). This document + **memory-engine-v3-test-plan.md**
are the build inputs; the convergence discipline (§17) holds this to be the last design pass.

**Adoption stance:** the reference instance is **not migrated in place.** v3.0 is built and proven on a
fork; the instance conforms only after the plan is proven and the cutover is gated and reversible. The
engine-independent relief slice bankable in production now is **memory-engine-phase-0-plan.md**.

**Altitude:** a **template-level** capability, written generic. The live business wiki supplies the adversarial
evidence; the mechanism belongs to the template.

## Revision log

**v3.2 (2026-06-11)** folds the scoped re-pass findings F1–F18 (all WEAKEN-FIX, zero KILLs; four were
regressions the rebuilds introduced). The architecture is unchanged; these are bounded corrections.
- **Journal spine (§6):** REF-SKIPPED moves to priority 1 in the decision table (F1); a PENDING event
  with an active superseder carries the superseder in its absorb packet (F2); the checkpoint enumerates
  per-`(event,view)` **disposition class + content-audit status** and ACC-6 compares them (F3); chain
  integrity moves to a **journal `prev_record_hash`** — `parent_git_sha` is forensic metadata only (F4,
  a regression: the git-topology check false-positived on legal concurrent worktrees); a malformed
  sidecar gets a **governed quarantine** allowlist, not ad-hoc deletion (F5).
- **No-op / classification (§4, §7):** an event whose `event_class` was judgment-assigned (the ~17%
  YAML-hostile case) is treated as **lock-class for no-op purposes** — conservative default mirroring the
  `origin` rule (F6).
- **Security (§9, §11):** quarantine keys on **task type (build/fix), not view tier** (F8); `origin`
  **propagates through the agent loop** — a session-authored event's origin ≥ the `origin_max` of the
  packet it consumed (F9); the P1 origin backfill needs a **named mechanical rule or operator
  attestation**, gated in MIG-1 (F7); `resolve_within` realpaths **both** arguments and rejects NTFS
  alternate-data-stream paths (F10); the egress dependency becomes **mechanical**, and the
  **taint/credential/egress co-residency invariant** is promoted to CLAUDE.md (F11).
- **Seed / audit / verify (§7, §13):** **`absorbed-without-source` joins the audit ladder** and its
  injection list is a gated MIG-1 review section — closing the inverted-trust-ladder that re-opened
  silent loss (F14, the worst); `consumed_status` is stamped into the derivation block at P1 so the gate
  binds before P4 (F12); content audits **run under the VERIFY harness** with a planted-defect floor
  (F13); the routing census is emitted by a **standalone reproducible script**, hash-pinned in the
  journal (F15); `corpus_support` pins **verbatim quoted text** and the verify packet embeds the
  resolved excerpt (F16); substrate fields are **derived from invocation metadata**, not
  orchestrator-supplied strings (F17); `vendor` and `model_id` are recorded separately with a named
  gate policy (F18).
- **Template/instance layering (§13):** added a *Template-layer deliverables* enumeration separating the
  v3.2 harness changes (constitution, perimeter, skills, rules, sensors) from instance data — built on
  the fork, not inherited from the migrating wiki.

**v3.1** folded the cross-vendor gate (memory-engine-v3-verification-results.md). The v3.0/v3.1 texts
the verifiers reviewed are in git history.

---

## 1. The problem

The harness is externalized memory for a multi-month build run by a succession of context-bounded
sessions. Today that memory is prose articles that only grow (reference instance: a 304 KB / 2,221-line
article), and consolidation reads each target in full before editing. Cost scales with **project
history**, not current-state size: runs reach 30–90 minutes and have overflowed a context window
mid-run. The redesign makes maintenance cost scale with **the delta**.

## 2. The principle — ledger and lens

- **Ledger (authored, cold, append-only).** Decisions, observations, corrections, run-receipts, loop
  locks. Immutable; the file path is the event id. The only thing written *as truth*.
- **Views (derived, hot, projected).** Knowledge articles, dashboards, indexes, briefings. Bounded,
  disposable, regenerable. Stored only as a **cache** of a query over the ledger.
- **The run journal (the transform record).** What each run absorbed — machine data, append-only,
  replayable. Cold/authored state; its correctness is the spine (§6).

**derive-up** (recompute every run — you can't drift from a copy you never made) and **forget-down**
(relocate detail to cold; keep the current slice + a link) keep the hot tier bounded. The shape recurs
in databases (log + view), build systems (sources + lazy DAG), git (objects + refs), event sourcing
(log + projections + checkpoints) — made explicit, with judgment transforms (LLM calls) that are
therefore **verified**.

## 3. Architecture at a glance

```
LEDGER (append-only)            ENGINE (deterministic scripts)        VIEWS (projected, bounded)
  raw/ events  ───────────────► staleness.py  (what is stale) ──────► topic articles
  receipts/ + journal/         catalog.py    (retrieval index) ─────► dashboards / indexes
  handoff & dispatch locks     project.py    (render projections) ──► HEALTH / briefing
        ▲                      assemble.py   (task → packet) ───────► (ephemeral) context packet
        │   JUDGMENT (LLM, tiered): ABSORB · RECONCILE · VERIFY                  │
        └───────────────  sessions append new events  ◄── corpus repos (ground truth, SHA-pinned)
```

## 4. Ledger contract (events)

Append-only intake, **extended, never loosened**. Frontmatter gains `entities`, `cascades_to`,
`supersedes` (clause-granular, §8), `origin` (human | corpus | vendor-ref | external-scrape | unknown),
`asserts_corpus_state`, `event_class`.

- **~17–19% of existing events have unparseable/absent frontmatter** and are append-only (never
  repairable). So **normalized entities, routing, `origin`, AND `event_class` live in an append-only
  routing-registration record** (one per event, schema-gated, `seq`-ordered — §6 substrate), not the
  event file. Conservative defaults for judgment-assigned values: **missing/unparseable `origin` →
  `unknown`** (≥ `external-scrape` in restrictiveness); **judgment-assigned `event_class` is treated as
  `lock`-class for no-op purposes** (§7) — neither defaults to a trusting value (F6, F7). The
  registration carries `origin`, `origin_evidence`, `event_class`, and `asserts_corpus_state`.
- **`origin` propagates through the agent loop (F9):** a session-authored event's registration origin is
  **at least the `origin_max` of the packet that session consumed** (or `unknown` when unrecorded). Only
  an operator-authored decision file with a matching receipt trail gets `human`. Without this, a tainted
  view launders to clean origin in one agent hop (tainted view → session-authored raw → trusted).
- **Filenames are date-only** → order never inferred from filenames; absorption is set membership (§6);
  ordering uses the journal `seq`.
- **Cascade intent** (~1/3 of events) is a first-class, greppable, preflight-validated mandatory
  staleness input; a not-yet-existing target must match a **planned-views registry** entry; an
  unresolvable one auto-files a REVIEW item, never routes to void.

## 5. View contract (derivation block)

A **single mechanically-strippable delimited region** (engine layer removable in one pass —
reversibility, §13):

```yaml
# --- derivation (engine-managed; strip region) ---
schema_version: 3.2
view: topic              # topic | dashboard | index | briefing
summary: One line for the catalog.
entities: [work-orders, line-items]
status: active           # active | superseded
tier: T1                 # T1 = top-tier absorb + mandatory adversarial verify; T3 = sonnet + sampled
consumed_status: verified-consumed   # verified-consumed | legacy-assumed | audit-pending  (F12, visible to readers)
origin_max: human         # = max(canonical origin of consumed events); checked by ACC-3
subscribes:
  entities: [work-orders, line-items]
  corpus: [platform-repo:migrations/sqitch.plan]   # ARTIFACT-scoped, never repo-HEAD
bundle: [wiki/systems/schema-foundations-commitments.md]   # retrieval closure (§11), catalog-derived
verified:                 # null until VERIFY passes; resets on rebuild
  status: passed
  at: 2026-06-10T18:22:00Z
  verifier_vendor: <vendor>    # vendor + model_id recorded SEPARATELY (F18); derived from invocation
  verifier_model_id: <id>      #   metadata, never orchestrator-supplied strings (F17)
  absorb_vendor: <vendor>
  absorb_model_id: <id>
  packet_hash: <…>
  artifact: receipts/verify/<…>
# --- /derivation ---
```

**Substrate-gate policy (F18), named here so tests check the real rule:** routine T1 verify gates on
`model_id` difference (absorb ≠ verify model); **migration-era content audits and design-gate
verifications gate on `vendor` difference** (the cross-substrate firewall). This matches the project's
backend-selection practice (routine legs hub-fired Claude-on-Claude; keystone legs operator-fired
cross-vendor). Bounds are byte-denominated, per-type, measured on the git blob (LF-normalized).

## 6. The run journal & staleness (the spine)

**Absorption is set membership recorded in the journal** — never a watermark, never view frontmatter,
never receipt prose. Each run appends a schema-validated machine record (`receipts/journal/<seq>.json`,
`seq` monotonic under the lock) carrying: `absorbed` (view, events, pre/post blob, manifest,
**`corpus_support`** with verbatim support text), `noop_candidates` (verified flag), `pins`,
`registrations` (entities, cascades, origin, event_class), `corrections`, **`prev_record_hash`** (hash
of record `seq−1`), and `parent_git_sha` (**forensic metadata only**).

- **Substrate.** Schema-validated JSON that **refuses malformed output** at write time (not corruptible
  receipt prose). The journal directory is in the corruption sensor's scope and its own failure modes are
  tested. A malformed record exits loud by filename — **with a governed recovery (F5):** a
  `journal_quarantine:` named-allowlist (the `LEGACY_LENIENT_FOLDERS` pattern), each entry requiring an
  operator decision raw file; quarantined seqs' absorptions revert to PENDING (the conservative
  direction). No ad-hoc deletion of append-only history.
- **Chain integrity is journal-native (F4).** `seq` is allocated under a lock at
  `git rev-parse --git-common-dir` (covers all worktrees of one repo). Integrity is enforced on
  **`prev_record_hash`** — gap, duplicate, or hash mismatch fails loud — which is collision-free
  regardless of git topology. (The earlier `parent_git_sha` chain check was a regression: two legal
  concurrent worktrees branching from one HEAD share a parent and tripped a false "fork.")
- **Checkpoint = full deterministic state (F3).** A periodic committed checkpoint serializes the entire
  staleness state — consumed pairs **with their per-`(event,view)` disposition class
  (`verified-consumed` | `legacy-assumed` | `absorbed-without-source`) and content-audit status**, no-op
  candidates, pins, registrations, origin/taint, cascade targets, supersession chains, correction-target
  map, citation-translation records, hole list, schema version — so replay = checkpoint + suffix is
  provably equal to full replay. ACC-6 compares all of it.

**Conservation invariant (every-run tripwire).** An **ordered decision table** (first match wins →
disjoint by construction). Every ledger event resolves to exactly one of, in order: **`REF-SKIPPED`**
(any `source: ref` event, skipped regardless of entity match — priority 1, F1) → `SUPERSEDED-UNCONSUMED`
→ `CONSUMED` → `PENDING_NOOP_CANDIDATE` → `PENDING` → `UNROUTED` → `RESIDUE`. Residue snapshots
containing `source: ref` / already-consumed / superseded events are rejected. Zero-class = FAIL.

A view is **stale** iff: (a) an event **not in its consumed-set** matches its `subscribes.entities` or
names it in `cascades_to`; (b) a subscribed corpus **artifact-path** differs from its pin; or (c) a
consumed event gained an amendment aimed at a **clause** it depends on (§8). When a PENDING event has an
active superseder, its absorb packet carries the superseder (F2).

## 7. The judgment verbs

**ABSORB** — rebuild a stale view. Orchestrator runs staleness, routes unrouted events, spawns one agent
per stale view with a **slim contract** (view + delta events + routing rules + invariant 4 verbatim + the
§9 untrusted-data rule). T3 → sonnet; **T1 → top tier**. The agent emits a per-event merge manifest and,
for shipped-state prose, a **`corpus_support`** entry (artifact + SHA + **verbatim quoted support lines**,
F16). It may **no-op** — but a no-op on a **T1 / correction / lock / `informed_by`** event (where
`event_class` includes the conservative lock-class default for judgment-assigned events, F6) is a
`PENDING_NOOP_CANDIDATE`, **not consumed**, until VERIFY confirms (against the full event body, not the
empty diff) it carries zero load-bearing claims. The orchestrator validates output (parses, bounds,
manifest matches the real diff, `corpus_support` lines actually appear in the cited artifact) then writes
the journal record. Cap breach → retirement proposals (forget-down, §2; ADR #11 — the manifest ranks
retireable spans by bytes and the cap episode's brake refuses ordinary growth until hot bytes retire);
split only for the genuine multi-topic case, and a split transfers cap debt to its parts (v3.0.52
amendment per the ratified brief §2.3 — the original text read "split proposal as a dispatched
session", which conserves mass and cannot discharge a breach). Circuit breaker: 15 rebuilds/run.

**Content audit (migration-era, §13).** Before a `legacy-assumed` or `absorbed-without-source` T1 /
correction / lock view is served to a build/fix task, a one-time audit (event claims → view hunks) must
pass. It is **stamped `consumed_status` in the derivation block at P1** (F12) so the gate binds for every
reader from P1 on — not only at `assemble.py` (P4); `check-derivation.py` reports `audit-pending` T1 views
in the flight-plan sweep, and the dispatch-hub skill forbids a build/fix dispatch against one. **The audit
runs under the VERIFY harness (F13):** substrate ≠ the agent that wrote the view lineage (vendor-gated per
§5), recorded in the structured form, with one **planted-defect audit** in the batch as the efficacy floor
— the audit is itself a verified judgment, not a same-substrate batch rubber-stamp.

**RECONCILE** — scripts flag, the LLM judges only flags: entity-overlapping pairs that both changed;
roadmap rows whose cited sources changed; supersession chains; pathological hub entities; and any view
diff that changed shipped-state prose without a matching `corpus_support`. Top tier — never haiku. A
bounded synthesis pass owns connection views and samples entity-disjoint pairs.

**VERIFY** — for T1 views, sampled for T3, and mandatory for every `PENDING_NOOP_CANDIDATE`. The packet is
**orchestrator-assembled** and includes the cumulative diff since last-verified blob; the absorbed events;
the manifests as claims under test; the **mechanically-resolved corpus excerpt** at the pinned SHA next to
each shipped-state hunk (F16, so a repo-blind verifier can judge support); and the **routing census** —
emitted by a **standalone reproducible script** whose inputs (ledger slice, `entities.yaml` blob SHA) and
output hash are journaled, so VERIFY/CI re-runs and compares hashes (F15: the census must not be
self-graded by the matcher whose drops it audits). It answers — recall, retention, manifest+support
honesty, boundary (no matched-but-omitted event in the census) — and stamps `verified:` only on all four.
**Substrate fields are derived from the agent invocation's response metadata** (the same channel
`token_cost` uses), never from orchestrator-supplied strings; cross-vendor paste legs record the
pasted-response file + operator attestation (F17). The gate fails per the §5 policy; `verified:` resets on
rebuild; `assemble.py` refuses an unverified-T1 view to a build task.

## 8. Amendment granularity (clause-level)

The dominant real amendment is **clause-level with `supersedes: null`**. So **decision clauses are
first-class entities** (`decision-9-q`, `n2-boundary`, …); amendment events tag the clause; staleness
clause (c) is clause-scoped; a diamond resolves by the clause's `seq`-ordered history. The P0 split that
moves clause line numbers emits a **citation-translation record** (journaled, §6 checkpoint state) so
locker-verified trails and grep-based consumption checks stay replayable.

## 9. Security model (a boundary, not a hope)

Untrusted text could flow ingest → absorb → view → packet → a credentialed worker with no human read
between. Closed at the **schema layer, at P1** (un-retrofittable):

- **Canonical `origin`** in the registration record; `unknown` ≥ `external-scrape`;
  `origin_max(view) == max(origin of consumed events)` is an ACC-3 check; origin propagates through the
  agent loop (§4, F9). **The P1 origin backfill (F7):** all 69 legacy events lack `origin`; strict
  defaulting makes them `unknown` and bricks every build, so backfill must assign — but an upgrade from
  `unknown` requires a **named mechanical rule** (event predates any scraping channel; operator-authored
  decision file with a matching receipt trail) **or per-event operator attestation**. The assignment list
  with evidence is a **mandatory MIG-1 review section** (the quarantine decision is not a silent batch).
- **Quarantine by task type, not view tier (F8).** Any **build/fix** packet excludes
  `external-scrape`/`unknown`-origin content **regardless of the view's tier** — untrusted text never
  reaches a code-writing worker. Banners (not exclusion) remain only for recon/verify tasks. (Full
  per-span taint provenance, needed only to safely *include* untrusted spans, is deferred to the template.)
- **Path containment (F10).** `resolve_within(root, p)` realpaths **both** the root and the candidate
  before the `commonpath` check (junction/`subst`-resolved), and **rejects any `:` beyond the drive
  letter** (NTFS alternate-data-stream paths, which would hide content from git and every sensor). An
  escaping symlink is fatal. Check-then-open TOCTOU is an accepted residual at single-machine scale (§16).
- **Egress is mechanical, not just declared (F11).** `assemble.py` **refuses to emit a packet whose
  `origin_max` exceeds `human` to a credentialed session profile** unless a sandbox attestation is present;
  sandbox default-deny egress remains a required dependency and the egress hooks defense-in-depth. This is
  enforced by the **co-residency invariant** (next), promoted to CLAUDE.md because it binds the whole
  harness, not just the engine.
- **Taint/credential/egress co-residency invariant.** Untrusted-derived memory content, live credentials,
  and a sanctioned egress channel (`git push vps`, the MCP gateway) **never share one session.** The named
  risk: injected text in a scraped-origin view steers a credentialed session to write secrets into a raw
  file that the next `/compile`'s push carries off-machine — exfiltration through sanctioned channels, no
  blocked tool invoked, every hook green. Enforcement: a session whose assembled packet carries any
  non-`human`/`corpus` origin content is stripped of credentialed tools and of push/egress capability (or
  the tainted content is excluded). Mirrored in CLAUDE.md § Security Perimeter.
- **`.agents/` / `.codex/` mirror harnesses** are deleted or sensored before P1 (skew + invisibility).

## 10. Invariant-3 protection (no transcription of shipped state)

- **`asserts_corpus_state`** as a canonical ingestion property — not token-sniffing. A so-marked event is
  pointer-class: it may update **links and status tables only**, and a status-table cell derived from it
  renders as **"claim reported by receipt &lt;id&gt;," never as implementation fact.**
- Any **shipped-state prose** change requires a **`corpus_support`** entry (verbatim quoted lines, F16)
  whose cited artifact is a view-subscribed corpus path and whose lines actually contain the claim — VERIFY
  tests the support against the embedded excerpt, RECONCILE flags prose changed without it. A
  recorded-but-irrelevant corpus read does not satisfy it.

## 11. Read path (assemble, bundles, packets)

`assemble.py --task "<descriptor>"` → a token-budgeted packet, with provenance and staleness banners.
Read-only (rebuilds are compile-owned).

- **Bundles depth-limited and role-shaped** (the schema cross-links form a clique): target full-text +
  one-hop summaries + a bounded commitments index, full-text on named demand; catalog-derived from
  declared edges.
- **Fail loud, never truncate.** A T1 task pulls its full bundle or refuses.
- **Stale/audit/taint policy is mechanical, by task type.** Stale T1 view + build/fix = **hard stop**;
  stale + verify/recon = proceed with banner. An **`audit-pending` (legacy-assumed / absorbed-without-
  source) T1 view = hard stop** for build/fix. A **build/fix packet excludes `external-scrape`/`unknown`
  content** regardless of tier (§9). A blocking REVIEW entry naming an entity/view refuses build-class
  work against it.
- **Corpus safety for builds.** An unreadable subscribed corpus artifact for a **T1 build/fix task** is a
  blocking REVIEW item + hard stop; best-effort corpus reads remain for T3 / briefing / recon / verify.

## 12. Skew & doctrine

`schema_version` in derivation blocks and skill frontmatter; a preflight that **refuses on mismatch** (a
documented exception to "sensors degrade, never block"). `check-frontmatter.py` grows to cover the
derivation keys **before any block is minted.**

## 13. Migration phases (on the fork; the instance cutover is the last, gated step)

| Phase | Delivers | Gate | Reversible by |
|---|---|---|---|
| **P0** | Bounds + split the two mega-files (knowledge-preserving, stub+clause-map). **Engine-independent — bankable in the live instance now.** | ECO, CONTENT (split-citation) | knowledge-preserving + git-tracked |
| **P1** | `entities.yaml` (+ clause entities) + canonical `origin` backfill (named-rule/attestation, F7) + `event_class` registration (lock-default, F6) + derivation backfill (incl. `consumed_status`, F12) + `staleness.py`/`catalog.py`. **Seed from `sources:` ONLY, `legacy-assumed`**; obligations `(event,view)`-level; **`absorbed-without-source` pairs are ALSO `legacy-assumed` and audit-gated (F14)** — never injected at higher trust than sourced pairs | **MIG-1** (GOLD-1 + content-audit gate + the **origin-assignment** and **injection-list** review sections with abort criteria, F7/F14), ACC, ROUTE, PATH-CONTAINMENT, SUBSTRATE-SEPARATION | Strip the derivation region; drop `journal/` |
| **P2** | Compile skill v2 (slim absorb, manifests, journal, no-op candidates, `prev_record_hash`). Acceptance = **replayed 30-day workload** vs old-path `token_cost` | OPS (crash/concurrency/**LOCK-COMMON-DIR with the F4 hash-chain check**), LLM, ACC, JOURNAL-SIDECAR-INJECTION | Restore pre-engine skill from its git tag |
| **P3** | RECONCILE + VERIFY (orchestrator-assembled, routing census via reproducible script, corpus_support test) | CONTENT, LLM, CORPUS-SUPPORT | — |
| **P4** | `assemble.py` + bundles + packets + TAINT-QUARANTINE (by task type) | ECO, TAINT-QUARANTINE | — |
| **P5** | Receipts/locks typed as ledger events (pointer-class via `asserts_corpus_state`) | GOLD-1 re-run over enlarged ledger | — |

**F14 and F4 land before MIG-1 and OPS-6 are authored** (else the inverted ladder and the false-positive
chain check get built into the gates). **Reversibility is a tested property:** pre-engine skill archived
at a git tag before P2; derivation blocks one strippable region; receipts old-schema-readable; "discard
the engine" = strip regions + drop `journal/` + checkout the tag.

### Template-layer deliverables (what the fork carries beyond engine scripts)

v3.2 is a **harness template**; the reference instance is an instance whose *data* (`wiki/`, `raw/`,
`receipts/`, `roadmap/`) migrates **into** the freshly-built template. So every item below is a
**template build deliverable** — built on the fork, not patched into the instance — or it is lost when
the wiki migrates in. The engine scripts (§3, §6) are the obvious half; this is the other half, which
the phase table above expresses only as prose. The pre-existing v2.x harness (the curl/wget/nc/`.env`
hooks, the loop-envelope machinery, the existing skills/rules/sensors) carries forward with the fork
automatically; what follows is the **v3.2-specific** harness layer:

- **Constitution (`CLAUDE.md`):** the taint co-residency invariant (§9); a governing Memory-Engine
  section so new state complies by default; the ABSORB/RECONCILE/VERIFY verbs in the Orchestrators
  table; `origin` propagation (§4) and `asserts_corpus_state` pointer-class typing (§10) named at the
  invariant layer; the substrate-gate policy (§5); `deploy/entities.yaml` + the planned-views registry
  governed exactly like the Domains list (append-only, Directory-Preservation-style).
- **Security perimeter (`.claude/hooks/` + `settings`):** a PowerShell egress matcher
  (`Invoke-WebRequest`/`-RestMethod`/`irm`/`iwr`/`Start-BitsTransfer`) and interpreter-driven egress
  (`py -c`, `node -e`) added to the deny set; a secret-scanner pre-commit gate; co-residency enforcement
  (a session whose packet carries a taint banner is stripped of credentialed tools + push capability);
  the `.agents/`/`.codex/` mirrors deleted or sensored. Each new hook carries a **named risk + committed
  fixture** (the perimeter's own rule).
- **Skills (`.claude/skills/`, `skills/`):** compile → v2 (absorb / journal / no-op-candidate);
  flight-plan → reports `audit-pending` T1 views and consumes packets; dispatch-hub → no build/fix
  dispatch against an `audit-pending` T1 view + a fire-time staleness check; audit → stub-traversal and
  the rule that `verified:` is never evidence (corpus/article text is).
- **Rules (`.claude/rules/`):** wiki-articles → the derivation-block contract; receipts → the run
  journal; raw-intake → `origin`/`event_class`/`cascades_to` and the routing-registration record.
- **Sensors (`deploy/`):** `check-derivation.py` (new); `check-frontmatter.py` extended for the
  derivation keys + `schema_version`; `emit-routing-census.py`; alongside the engine scripts.

**Already live-patched in the instance (bank-now, like Phase 0 — still a template deliverable):** the
co-residency invariant in `CLAUDE.md § Security Perimeter`. It guards the live instance today, so it is
banked early; the template build must carry it into the fork's constitution regardless — the migration
does not inherit it from the instance.

## 14. Invariant mapping (all eight survive; five strengthen)

1. **Single writer** — absorb drafts, orchestrator writes; `assemble.py` read-only. Unchanged.
2. **`raw/` append-only** — formalized as the ledger; routing/`origin`/`event_class` in append-only
   registration records. *Strengthened.*
3. **Never transcribe shipped state** — `asserts_corpus_state` + `corpus_support`-verified prose. *Strengthened.*
4. **Never delete knowledge** — supersede + link; cap breach never trims; retention is a verify question;
   journal corruption is quarantined, never deleted (§6). *Strengthened.*
5. **Verifier ≠ author substrate** — recorded from invocation metadata and gated by the §5 vendor/model
   policy. *Strengthened.*
6. **Fired dispatch immutable** — splits add stubs. Unchanged.
7. **Evidence-grounded** — machine-checkable provenance (journal + pins + corpus_support). *Strengthened.*
8. **Corpus reads best-effort** — degrade per-source, except T1 build/fix tasks hard-stop on an unreadable
   subscribed artifact (§11). Scoped, not weakened.

Plus the new harness-level **taint co-residency invariant** (§9), mirrored in CLAUDE.md.

## 15. Design decisions under adversarial pressure (attack the survivors)

The v3.0/v3.1 rebuild table (set-membership consumed-sets; `cascades_to` mandatory input; orchestrator-
validated journal; byte caps + split protocol; sources-only `legacy-assumed` seed; `PENDING_NOOP_CANDIDATE`;
ordered-table conservation; full-state checkpoint; artifact-scoped corpus pins; canonical `origin` +
quarantine; `realpath` containment; `asserts_corpus_state` + `corpus_support`; routing census; structured
`verified:`; depth-limited bundles; clause entities) — plus the v3.2 corrections folded above (F1–F18:
REF-SKIPPED priority; `prev_record_hash` chain; `event_class` lock-default; quarantine by task type; origin
loop-propagation; `absorbed-without-source` on the audit ladder; content-audit under VERIFY; reproducible
census; metadata-derived substrate fields; vendor/model gate; co-residency invariant). The v3.0/v3.1 text
the verifiers attacked is in git history.

## 16. Residual risks & open questions

- **Absorb + audit quality on dense T1 content** — tested by planted-defect catch rate (now including the
  content audit, F13) and the P2 workload acceptance; the empirical floor is unproven until measured.
- **Cost honesty** — wall-clock now, token savings mainly in T3-heavy phases and at scale; the P2
  acceptance is a 30-day workload replay vs old-path `token_cost`.
- **The proving ground proves the engine, not the cutover** — the migration is gated separately (MIG /
  GOLD-1) and re-run against live state before cutover.
- **Accepted single-machine residuals:** check-then-open path TOCTOU (F10); the git-common-dir lock covers
  one machine's worktrees, not many machines (distributed `seq` consensus is a template item).
- **Template/scale items deferred past the first build:** full per-span taint provenance; engine-provided
  egress sandboxing; distributed `seq` consensus; network-FS replay optimization.

## 17. Verification status

The design has passed a **two-substrate cross-vendor gate** on v3.0 (23 findings) and a **scoped
same-family re-pass** on the v3.1 rebuilt sections (18 findings, zero KILLs, four regressions caught;
memory-engine-v3.1-scoped-reverify-results.md). All findings are folded above. Per the convergence
discipline — bounded mechanical corrections, no surviving relocated-break, no architecture impact — **this
is the last design pass; build on the fork, with the test-plan gates carrying residual risk.** The one
optional further check is a **focused cross-vendor handoff on the §9 co-residency invariant** (the
highest-stakes finding from a same-family reviewer), at the operator's discretion. Phase 0 in production
remains independent and bankable throughout.
