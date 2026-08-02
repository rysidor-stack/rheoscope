<!-- TEMPLATE PROVENANCE (Rheoscope harness, not part of the ratified doctrine text):
     The section below this comment block, from the H1 through the "Grounding artifacts"
     footer, is manifest-driven-builds.md v2.1 as RATIFIED by Ryan on 2026-07-16 in the
     live business control plane, carried VERBATIM per the ratification's hand-verbatim
     instruction. Do not edit the ratified text; propose changes upstream (the control
     plane) as v2.2 candidates. The harness-side operational contract that implements it
     here is core/methodology/manifest-format.md. The Harness Incorporation Annex at the
     bottom of this file (clearly demarcated) is template-authored and maps the doctrine's
     §12 directives to where each landed in this harness.
     READER NOTE (2026-07-28): §4.3's "(seven)" RESERVED count reads as of ratification and
     stays byte-intact; the live list is deploy/manifest-layers.yaml, which holds NINE
     RESERVED layers since 2026-07-23 (+ rendering-fit, config — Amendment Addendum items
     14 and 16; backlog v3.0-71).
     VENTURE-SCRUB NOTE (2026-08-01, backlog v3.0-89, operator ruling): venture-identifying
     names in this file were genericized for template publication. The ratified byte-verbatim
     original is preserved in the dev repo at capabilities/knowledge-os/design-history/
     certified-originals/manifest-driven-builds.md and in dev-repo git history at this path;
     the doctrine TEXT is otherwise untouched -- doctrine changes still go upstream as v2.2
     candidates, never here.
     verified-against: 3.0 (2026-07-20) -->

# Manifest-Driven Builds — doctrine, format, and harness incorporation (v2.1)

> **Provenance without manifests is documented ambiguity; manifests without provenance are
> unauditable assertion. The template ships both or it ships neither.**

> **Status:** v2.1 **RATIFIED** — Ryan, 2026-07-16
> (`raw/2026-07-16-ryan-mdd-v21-ratified-supersedes-v1.md`). This file lives at the canonical
> doctrine path and **supersedes v1**, retired the same day (full v1 text retrievable from git
> history at this path). v2.0 was session-authored 2026-07-14 after a full reasoning pass over
> the v1 doctrine, the MDD paper, all five exemplar manifests, the parity seed, the mandate
> raws, and the Rheoscope v3.0 dogfood fork (repo identifiers genericized at template
> publication). v2.1 additions session-authored 2026-07-15 from the post-build-lifecycle
> session with Ryan (§9.1 three-kinds-of-change taxonomy, §10 twin-build instrument + inverted
> coverage, registry layers 16–17, directives 13–14; companion raw
> `raw/2026-07-15-session-mdd-post-build-taxonomy-twin-build.md`; same material in the MDD
> paper v0.2). Core doctrine originally declared by Ryan 2026-07-13
> (`raw/2026-07-13-ryan-manifest-doctrine-all-layers.md`).
> **Wiring:** this doctrine gates all build and design work — CLAUDE.md § Handoffs and
> Dispatches carries the gate; `dispatches/README.md` and `skills/dispatch-hub/SKILL.md`
> enforce it per dispatch; design freezes are incomplete without their manifests.
> **Portability:** written to be handed verbatim to the Rheoscope harness-template build
> (v3.0). §12 carries the incorporation directives. Everything before §12 is the doctrine.

---

## 0. What changed from v1

| Area | v1 | v2 |
|---|---|---|
| Taxonomy | Fixed list of 6 layers | Layer **registry** with statuses (CORE / ACTIVE / RESERVED) and an admission rule |
| Accessibility, viewport, copy | Unaddressed / implicit | **Dimensions** of interaction rows (columns + variant matrix), not new files |
| Build gate | "Every layer it touches" (undefined depth) | Touch-based gate with **tier-scaled certification depth** |
| Row format | Described by example | **Canonical schema**: frontmatter, row fields, controlled flags, evidence modality, two row kinds |
| Amendments | Inline strikethrough (exemplar practice) | **Append-only amendment log** + same-commit currency rule (GOLD-2 generalized) |
| Seed | One pinned fixture | Versioned **seed set**; goldens pin the seed version they compute over |
| Sweeps | One full replay | **Smoke tier + full tier** with declared cadence |
| Non-UI extraction | Undertheorized | Per-layer **"what do you drive"** table; legacy system = frozen artifact for integration |
| Coverage claims | "Rows replayed / rows total" | Same, plus **denominator honesty**: receipts cite the completeness hunts that pressured the total |
| Existing manifests | — | **Grandfathered**; canonical format applies to new extractions; retrofit optional |

**v2.1 additions (2026-07-15):** post-ship change taxonomy — every change is one of three
checkable moves, and the drift sensor must *classify* red rows (declared vs undeclared
nonconformance), not just detect them (§9.1); twin-build divergence as the fifth, empirical
completeness hunt, plus inverted coverage named as a concept (§10); construction/architecture
standards and dependency added to the registry as RESERVED (§4.3); two new incorporation
directives (§12, items 13–14).

The evidence base and the compression argument are unchanged from v1 and are kept short here —
the full treatment lives in the MDD paper (`methodology/manifest-driven-development-paper.md`,
§2 and §4; v1's long-form evidence sections are carried there, and v1 itself is in git
history).

## 1. The evidence, in two sentences

The scheduler was built from a strong 429-line spec with authoritative design exports attached,
and still needed 299 retrofitted interaction states, a conformance sweep that found ~90
problems, six fix waves, a design-recovery pass, and an 8-hour QA marathon. The ordering suite
was built *after* its manifests existed (101 screen states, 12 rules, 11 goldens, a pinned
seed) and shipped once, passing its gates as it landed.

## 2. Why specs fail AI builders, in one paragraph

A spec is a compression artifact. Human teams could afford compression because humans
decompressed it — shared taste, design reviews, QA instinct. An AI worker has no ambient
context: its whole world is the packet, and every behavior the packet doesn't enumerate gets
filled from generic priors. Attaching the design doesn't fix it — **greppable ground truth is
not an enumerated contract**; the builder samples what's attached, and attention goes where the
prose points. A manifest turns passive truth into rows: each row is an obligation the builder
must discharge and a check a verifier can replay.

## 3. The doctrine

**No build dispatch fires until every layer it touches has a manifest.** The build spec
survives, demoted to the assembly plan: sequencing, gates, environments, worker logistics. A
design freeze without its manifests is an incomplete freeze.

**The gate is touch-based and tier-scaled.** A build needs manifests only for the layers it
actually touches, at a certification depth set by the increment's tier:

| Tier | Required manifest state for touched layers |
|---|---|
| T1 / T2 | CERTIFIED (adversarial completeness review done, receipt on file) |
| T3 | EXTRACTED (enumeration complete; certification may be pending) |
| T4 | Exempt, with the exemption and its reason named in the receipt |

Without this proportionality the gate gets bypassed in practice, and a bypassed gate is worse
than none.

## 4. The layer registry

v1's fixed six-layer list becomes a registry. Three statuses:

- **CORE** — the six layers every product surface has. Mandatory subjects of the gate.
- **ACTIVE** — layers with a named incurred cost in this program. Mandatory **when touched**
  (a build that ships a migration must have a migration manifest; one with background jobs
  must have an async manifest).
- **RESERVED** — named now so nobody reinvents them later; row schemas deferred until first
  need.

**Admission rule** (mirrors the security-hook rule): a new layer enters the registry only with
(a) a named incurred cost or certain future risk, (b) a row schema, and (c) a replay
mechanism. A proposed layer that would replay through an existing layer's mechanism is a
*dimension* of that layer, not a new one. Layers without a named risk get pruned.

### 4.1 CORE layers (six)

1. **Interaction** — every user-reachable state: id, name, replay path, exact assertions,
   variant. Includes empty states, errors, no-ops, drag/keyboard paths, UNREACHABLE and
   SURPRISE rows. Exemplars: `specs/build/mockups/ordering-web-SCREEN-MANIFEST.md` (101),
   `scheduler/design-exports/INTERACTION-MANIFEST-*.md` (299).
2. **Logic** — normative derivation rules; golden outputs over a pinned seed
   (mockup-verified before build; a golden that can't be reproduced escalates to the hub,
   never silently obeys either source); per-entity transition legality **as a
   `from | to | trigger | guard` table** (v2 change: chain-notation prose is not
   machine-checkable); empty-state copy; capability→enforcement map. Exemplar:
   `ordering-web-LOGIC-MANIFEST.md`.
3. **Design / style** — tokens, type scale, spacing/density, palette (with colorblind check),
   per-component visual states, motion. The thinnest layer in practice and the one the
   scheduler paid for (dark-theme pass, design-recovery pass). Exact-match rows where values
   are pinnable (tokens — the W3C design-token format is the natural schema); RUBRIC rows
   (§5) for feel and motion. Seed form: `scheduler/design-directives.md`.
4. **Data** — entities, fields, constraints, derivations, lifecycle **as rows** (today this
   content lives only in build-spec DDL — that is assembly-plan territory and must be
   extracted); the versioned seed set is its executable half (§7).
5. **Authorization** — capability × role × surface × **enforcement point**, with DENIED rows
   enumerated explicitly. Hiding a tile is not enforcement; the route must be gated where the
   request lands. Field-level contracts count (the `cost.view` rule: absent from every
   response — not null). Today exists only as §5 of the ordering logic manifest; a standalone
   reference exemplar is owed.
6. **Integration** — cross-module contracts: API shapes, events, sync rules, freshness,
   failure semantics, seam ownership. Zero exemplars today; the scheduler's "range mapping
   OPEN, 3 seams" residue is what this layer's absence looks like. Row shape:
   `seam | owner | request/event shape | freshness | failure semantics | idempotency | golden exchange`.

### 4.2 ACTIVE layers (four — named incurred costs)

7. **Failure / resilience** — timeout, 500, offline, partial failure, retry semantics.
   Named risk: the vendor platform writes on error pages (retry = duplicates); a count app offline
   mid-count. Rows: `failure trigger | injection method | expected user-visible state |
   expected system state | recovery path`. Replay = fault injection.
8. **Async / eventing** — SSE pushes, background jobs, outbox, mirrors, notification
   triggers. Named risk: the day-board's SSE behavior broke the sweep method
   ("networkidle never resolves"); the "orange pulse" mechanic never got a row because no
   fresh-load click path produces it. Rows: `trigger event | cadence or latency window |
   observable | idempotency assertion`. Replay = event injection + clock control.
9. **Migration / cutover** — additive migrations, backfill, rollback, legacy-vs-new
   divergence. Named risk: migration 010 was a build-time adjudication outside any manifest;
   the whole program is a strangler cutover. Rows: `migration id | precondition | transform |
   verify query | rollback | approved divergences`. Replay = dual-run differential — the
   Rheoscope Differential Oracle / approved-divergences ledger already *is* this layer's
   sweep; naming the layer mostly promotes existing machinery.
10. **Concurrency / multi-user** — two terminals, same slot. Not observable by driving one
    browser. Rows: `contention scenario | session script pair | expected winner | expected
    loser experience | invariant preserved`. Replay = scripted dual sessions. Needed at first
    multi-terminal deploy — for a POS, effectively launch.

### 4.3 RESERVED layers (seven — names registered, schemas deferred)

11. **Performance** — budget rows (the 250ms capture-freeze gate is already one, unnamed).
    RUBRIC-graded, not exact-match.
12. **Observability / telemetry** — `event | trigger | fields | retention | consumer`. The
    enumerated half of DATA-POLICY; feeds the trust engine's future production-telemetry
    corpus source; PCI audit-trail obligations land here.
13. **Data protection / compliance** — sensitive-field × surface × handling
    (mask / omit / retain). Distinct from authorization. The PAN field-provenance gate is
    this layer's first row, currently living in hook code.
14. **Peripherals / hardware** — printers, scanners, cash drawers, card readers. Structurally
    integration rows with a device sub-genre; reserve the name, don't build yet.
15. **i18n / localization** — future SaaS productization. Name only.
16. **Construction / architecture standards** (v2.1) — module boundaries, dependency
    direction, layering rules, forbidden patterns, lint rules. Carries a structural wrinkle no
    other layer has: its rows constrain the **projection**, not the definition — so its
    obligations do NOT survive regeneration (§9.1, move 2) unless carried explicitly across
    it, and it is what separates a refactor from silent architectural drift. Lint configs are
    the seed form and classical software's one universally adopted manifest-shaped artifact —
    today practiced everywhere as *manifests without provenance* (cargo-culted configs,
    inline-suppression churn, dogma-or-drift). The admission-rule discipline applies **per
    row**: every rule carries the named risk it prevents (the security-hook rule generalized
    to the construction layer); unnameable rules pruned. Replay = the linter itself — the one
    layer whose sweep already exists as mechanical tooling. RESERVED pending a named incurred
    cost in this program.
17. **Dependency** (v2.1) — each dependency carries why it exists and which rows it
    discharges; an upgrade is an amendment plus re-sweep, not an `npm update` and a prayer.
    Name only.

### 4.4 Dimensions, not layers

**Accessibility**, **viewport/responsive**, and **copy** replay through the same browser drive
as interaction rows, so they are dimensions of the interaction (and design) manifests:

- **Accessibility**: keyboard-path and focus/aria assertions become row fields. The extraction
  instrument is already the accessibility tree — these assertions are nearly free at
  extraction time and cost a retrofit wave when skipped (the v3.3 a11y wave: focus-trap gap
  across 10 dialogs; aria-pressed/selected and keyboard-for-drag still open debt).
- **Viewport**: a declared frontmatter field plus a variant-matrix axis. "Breakpoints are the
  build's" is a hole in totality — unpinned means the model's priors own it. Named risk: the
  count-web mobile overflow defect shipped to a live count day.
- **Copy**: already covered by exact-text assertions and empty-state sections. No action.

### 4.5 A note on the seven-terms/six-layers discrepancy

The 2026-07-13 raw lists seven terms ("UI interaction, UX, design/style, logic, data,
authorization, integration"); the doctrine has six. **UX was deliberately folded** into
interaction (behavioral half) and design/style (feel half). Recorded here so no future reader
concludes a layer was dropped.

### 4.6 What you drive, per layer

Extraction is observation — but v1 only said what to observe for UI. The frozen artifact,
generalized:

| Layer | Frozen artifact you drive | Instrument |
|---|---|---|
| Interaction, Design | Frozen mockup / HTML export | Browser automation + a11y tree + screenshots |
| Logic | Mockup as reference implementation | Golden drives over the pinned seed |
| Data | Pinned DDL + seed set | Schema read + seed replay |
| Authorization | Mockup + route probes | Request-level probes per role |
| Integration | **The legacy system itself** | Corpus observation — probe sessions ARE extraction |
| Migration | Legacy + new, side by side | Dual-run differential |
| Failure | The build, under injection | Fault injection |
| Async | The build, under event injection | Event injection + clock control |
| Concurrency | The build, two sessions | Scripted dual-session drives |

The integration row is the unification: for a strangler program, vendor-platform probe sessions
(posajax checkout capture, item-create probes) already produce integration-manifest rows.
Extraction is observation; conformance is re-observation; drift is the diff. This is the same
loop as § Corpus Observation in CLAUDE.md, extended to manifests.

## 5. The row discipline

A manifest is a set of rows. Every row is **enumerable** (names one discrete state, rule,
token, entity, capability, or contract), **individually checkable**, **observable-bearing**
(carries its exact expected observable), and **replayable** (specifies its own reproduction
path). A row you cannot replay is a row you cannot verify — rewrite it until you can.

**Two row kinds** (v2):

- **EXACT** — the observable is matched literally (visible text, number, state, payload).
- **RUBRIC** — the observable is graded against stated criteria (motion feel, latency budget,
  ranking quality). Rubric rows must state their grading criteria in the row; "looks right" is
  not a rubric. Needed so the design and performance layers can be honest instead of
  pretending everything is exact-matchable.

**Controlled flag vocabulary** (v2 — today's flags are free text and cannot be counted or
filtered without reading prose):

`UNREACHABLE | UNREACHABLE-BY-DESIGN | SURPRISE | NOOP | BUILD-MUST-DIVERGE | TIME-SENSITIVE | REAL-MODE-ONLY`

**Evidence modality** is a row field, not a footnote: `a11y-tree | screenshot |
computed-style | source-read | request-probe`. Some exemplar rows are not replayable as
literally written (modals that never appear in the a11y snapshot); a conformance runner must
know which instrument each assertion was made with.

**Stable IDs**: kebab-case semantic slugs, never reused, never renumbered. Rows promoted from
completeness cross-checks keep the `gap-` prefix. Findings, fixes, receipts, and amendments
reference rows by ID the way issue trackers reference tickets.

## 6. The canonical format

None of the five exemplars has machine-readable metadata; the flagship manifest even carries a
declared-vs-actual row-count defect (prose says 16 gap rows, body holds 15) that no sensor
could catch because there is nothing structured to check. The canonical format fixes this.

**Frontmatter** (YAML, required on every new manifest):

```yaml
manifest: interaction            # registry layer
surface: ordering-web            # project surface
version: 1.0
status: EXTRACTED                # DRAFT | EXTRACTED | CERTIFIED | LIVE | SUPERSEDED
source_artifact: ordering-web-mockup-v12.html
source_sha256: <hash>            # byte counts alone are a weak pin
extracted: 2026-07-14
toolchain: playwright-mcp        # playwright-mcp | node-playwright | request-probe | ...
viewports: [1280x800]
variant_axes: [role]             # what the variant column ranges over
seed: ordering-web-PARITY-SEED.json@v3
declared_rows: 101
confidence: source-crosschecked  # source-crosschecked | interaction-only
```

`confidence: interaction-only` marks manifests whose totality could not be pressure-tested
against readable source (the coach-cockpit case) — weaker by construction, and the gate should
know it.

**Row fields**: `id | name | replay path | expected observable | variant | flags | evidence | kind`.
The unstable fifth column of the exemplars ("Role" / "State context" / which-coach) resolves
into one `variant` field whose meaning is declared by `variant_axes`.

**MANIFEST-INDEX** — one per surface, the machine-checkable face of the build gate:

```yaml
surface: ordering-web
layers:
  interaction: {file: ..., status: CERTIFIED, rows: 101, certified_by: <receipt>}
  logic:       {file: ..., status: CERTIFIED, rows: 27,  certified_by: <receipt>}
  design:      {file: ..., status: DRAFT}
  data:        {status: MISSING}
  ...
```

The gate reads this file. "A design freeze without manifests is an incomplete freeze" becomes
a one-line check instead of a judgment call.

**Self-check sensor** — `check-manifest.py`, in the existing sensor pattern (re-derived every
run, detection wired to correction): declared vs computed row count, ID uniqueness, flag
vocabulary conformance, frontmatter completeness, MANIFEST-INDEX ↔ file coherence.

**Grandfathering**: the five existing exemplars remain valid as authored. The canonical format
binds new extractions; retrofit is optional, per-surface, and never a reason to delay a build
that their content already covers. Formats may be extended, never loosened — v1 defined no
canonical format, so defining one is an extension.

## 7. Seed governance

The seed is load-bearing: every golden points into it, so a wrong seed silently rots every
golden at once.

- Seeds are **versioned**; manifests and goldens pin the seed version they compute over.
- A surface owns a **seed set**, not one seed — a single fixture cannot host every
  precondition (the mockup never renders empty; a different day, an empty table, an error
  state each need their own seed or an explicit `REAL-MODE-ONLY` flag with an owner).
- The `_seed_authoring_rules` pattern is canonical: seed corrections documented inline, with
  the math shown (`_reconciled`), dead rows tombstoned rather than deleted, and honest
  `GUESS` flags tracked until a credentialed probe resolves them.

## 8. The amendment protocol

Inline strikethrough (the exemplar practice) survives exactly one correction cycle. v2
generalizes the Rheoscope engine's GOLD-2 discipline ("answer keys flip in the same commit as
any change to the state they assert against — never a stale answer key"):

- Every manifest carries an **append-only `## Amendments` log**. An entry names the row ID,
  the prior value, the new value, the forcing evidence or decision (raw/decision link), and
  the date. The row cell carries the current value plus the amendment ID.
- **Same-commit currency**: a change to the source artifact, the seed, or the build contract
  that invalidates a row must land in the same commit as the row's amendment. A stale manifest
  is a defect, not a backlog item.
- Rows are never silently deleted: superseded rows get a `SUPERSEDED-BY <amendment-id>` flag,
  same discipline as wiki knowledge.

## 9. The lifecycle

1. **Design** — freeze the artifacts. A freeze without manifests is an incomplete freeze.
2. **Variant-matrix pre-check** (v2) — before driving anything, enumerate the variant
   cross-product (role × surface × viewport × precondition) as a checklist. Every
   completeness cross-check to date independently rediscovered the same gap class — whole
   role variants never driven — because drive-once-per-surface structurally misses
   cross-products. Catch it at the cheap end.
3. **Extraction** — dedicated sessions whose only job is enumeration, driving the frozen
   artifact per the §4.6 table. The extraction session's receipt records its **method**:
   toolchain, viewports, evidence modalities, whether source cross-check was possible.
   Method determines confidence; today that provenance lives in inconsistent header prose.
4. **Precondition sweep** (v2) — enumerate the seed-states the surface can occupy; seed each
   or flag the rows `REAL-MODE-ONLY` with an owner. Source-grep completeness checks only see
   what is reachable from documented entry points with the pinned seed — anything needing a
   different precondition is structurally invisible to them (the fixtures-only-certification
   lesson, applied to manifests).
5. **Adversarial manifest review** — a second substrate asks: *does this manifest set fully
   determine the artifact?* Hunts underdetermined behavior, not errors in what is written.
   Produces the certification receipt the MANIFEST-INDEX cites.
6. **Build** — the kickoff consumes manifests as required reading; execution step one is
   manifest certification (cheap: replay the goldens against the pinned seed before writing
   code); parity gates are defined as manifest-row replay.
7. **Conformance sweep** — two tiers (v2):
   - **Smoke tier**: a designated subset (the rows most likely to drift), re-driven on
     cadence. Cheap enough that it actually runs.
   - **Full tier**: every row, at freezes, pre-certification, and after fix waves.
   Findings become fix waves; fix waves re-sweep to green. Coverage is countable.
8. **Exhaustive QA** — every interaction a real operator would perform; **the manifest is the
   stopping criterion** — done means every row exercised, not "the session felt thorough."
9. **Standing drift sensor** — post-ship, manifest-vs-build divergence is a defect by
   definition: either the build is wrong, or the manifest is amended deliberately, with
   provenance (§8). An unrun sensor is a checklist, and checklists rot — hence the smoke tier.

### 9.1 After the build: the three kinds of change (v2.1)

The drift sensor (stage 9) generalizes into a complete post-ship taxonomy. If the manifest set
is the definition and the code its projection, every change to a shipped system — forever — is
exactly one of three moves:

1. **Definition amendment.** Manifest rows change first, via §8, with provenance. Feature work
   is this move, and the reframing is the discipline: **the amendment IS the feature.** The
   moment its rows are certified, the system is deliberately out of conformance — rows exist
   that no code discharges — and the build that follows is conformance restoration against a
   known row set, not interpretation. All ambiguity is spent at amendment time, supervised and
   reviewable, never at build time inside a worker's priors.
2. **Projection change.** Code changes; the full-tier sweep proves **zero row deltas**. This is
   the first checkable definition of a refactor: a projection change whose sweep is invariant.
   Refactoring needs no manifest of its own — it is governed by all of them (plus layer 16,
   the one manifest over the projection itself); provenance records only why the projection
   moved (performance, readability, a dying dependency). In the limit this move dissolves into
   **regeneration**: re-render the projection from the manifest set + seed on a stronger
   model, gated by the same sweep — subsuming dependency migration and framework replacement
   as the same operation with the same gate.
3. **Conformance restoration.** The ordinary defect fix: the build brought back to an
   unchanged row.

Consequences the harness must encode:

- **Feature-add and defect-fix are the same mechanical operation** — turn red rows green,
  same sweep, same coverage arithmetic, same receipts — differing only in which side moved
  (definition stepped deliberately ahead vs projection drifted).
- **The drift sensor must classify red, not merely detect it.** A red row with an open
  amendment-log entry behind it (§8) is *declared* nonconformance — a work queue, red by
  design. A red row with no amendment behind it is *undeclared* nonconformance — a defect.
  Same observable, opposite meaning; the amendment log is the discriminator. What the
  taxonomy eliminates is not nonconformance but undeclared nonconformance.
- **A change that fits none of the three moves is rejected** — it is by construction either an
  undocumented requirement change or unproven drift, refused on the same grounds as an
  unquantified "QA passed."

## 10. Coverage honesty

Enumeration totality cannot be certified, only pressured. Coverage arithmetic must define its
denominator honestly:

- A coverage claim is `rows replayed / rows total` **plus the receipts of the completeness
  hunts that pressured the total** (source cross-check, variant matrix, precondition sweep,
  adversarial review). "100% coverage" over an unpressured denominator is the new "QA passed."
- `confidence: interaction-only` manifests state their weaker footing in frontmatter, and the
  gate may require deeper adversarial review for them.
- The method's honest claim: it moves the residual from "unknown unknowns across the whole
  surface" to "whatever survived the completeness hunts" — a large practical improvement, not
  a proof.

**The fifth hunt — twin-build divergence (v2.1, the empirical one).** The four hunts above are
judgment-based; completeness can also be *measured*. Hand one certified manifest set + seed to
two independent builder sessions and diff the two products **through the contract** —
observable behavior, never code text. Byte identity is neither expected nor desirable: the
manifest defines exactly which differences count, and both builds landing in the same
behavioral equivalence class *is* the success condition. Wherever the builds coincide, the
manifests determined the behavior; every observable divergence adjudicates to exactly one of
two outcomes — a **missing row** (amend, §8) or a genuine **don't-care** (recorded as
deliberately free, with provenance). Iterating build–diff–amend until independent builds
cannot be told apart through the contract is the operational meaning of "fully determines."
(Mechanically this is N-version programming inverted: NVP ran independent implementations to
mask runtime faults and found spec ambiguity driving inter-version divergence — an unwanted
byproduct that MDD re-purposes as the measurement, with AI paying for the N builds that made
NVP prohibitive.) It is the most expensive hunt — reserve it for keystone/T1 surfaces; a
single small-surface pilot also calibrates how much the four cheap hunts miss.

**Inverted coverage (v2.1, named but not yet built).** Coverage counts rows discharged by the
build; the converse — does every unit of built code discharge some row? — is *inverted
coverage*, and it is unmeasured. Unobligated projection is either a missing row (behavior
exists that nobody enumerated — the scheduler failure mode pointed the other way) or code that
should not exist; a detector would subsume dead-code analysis and much of refactor review. No
sensor exists; the concept is named here so coverage receipts stop implying it.

## 11. The economics, in one paragraph

Enumerating 299 states cost a session-day; not enumerating them cost ~90 findings, six fix
waves, a design-recovery pass, and an 8-hour QA dig. Full context is now the cheap path. The
limiting factor in AI software development is not code generation — it is **context
production**: spend frontier capacity on manifests, extraction drives, adversarial review, and
sweeps, because those are the inputs code quality is downstream of. The manifest set is the
software's definition; the code is its current rendering — regenerable, by any future model,
better. The method scales with model capability instead of being obsoleted by it.

### 11.1 The unconstrained ideal (retained from v1 §7)

Every layer manifested before any build touches it. Every manifest adversarially certified as
*fully determining* its artifact. Builds consuming total context — because with AI you can, and
therefore you must. Conformance measured as row coverage, never vibes; QA terminating on
enumeration exhaustion, never fatigue. Drift a standing sensor, not an audit finding. And every
manifest row carrying provenance back to the decision that shaped it — so the two halves close
into one system: **the trust engine keeps WHY (decisions, receipts, corrections); the manifest
layer keeps exactly-WHAT (the total behavioral, visual, logical, and authorization contract).**
Decisions flow down into manifest rows; manifest rows flow forward into builds, sweeps, and
receipts; a defect is a row, a fix is a row turned green, and nothing ships on a feeling.

## 12. Incorporation directives — Rheoscope harness template v3.0

The fork has no behavioral-manifest layer today, but it is not virgin territory. These
directives replace v1 §8 and resolve the four real collisions found in the template.

1. **Naming: "behavioral manifests," directory `manifests/`.** The fork already has a
   load-bearing, security-gated artifact named `dispatch-manifest.json` (compile-pipeline
   output; `BridgeVerifyBackend` hard-fails without it; validated by the F17 attestation
   gate). That is a *transport* manifest — a different concept. Engine code and prose never
   say bare "manifest" where the two could be confused. Getting this wrong corrupts
   greppability permanently.
2. **Amend Firewall Rule 3 explicitly.** `verification-architecture.md` currently declares
   the *spec* the interface ("If they're not precise enough, that's a spec problem"). v3.0
   states: **the manifest set is upstream of both specs** — build spec and verification spec
   are projections of it. Leaving both supremacy claims standing means future sessions obey
   whichever they read last.
3. **First-class `manifests/` layer** — per project surface, one manifest per touched
   registry layer, canonical format (§6), stable row IDs, MANIFEST-INDEX per surface.
   Contracts, parsed by sessions: formats may be extended, never loosened.
4. **The build gate** — tier-scaled and touch-based (§3), reading the MANIFEST-INDEX,
   modeled on the PRE-REQ banner pattern. This edits all four tier protocols,
   `tier-definitions.md`, and the builder prompt template — a multi-file, non-additive
   change; plan it as such.
5. **Relationship to the Verification Spec Drill**: the drill's five prose sections
   (Invariants / State Transitions / Boundary Conditions / Data Shapes / Failure Modes) are
   *derived from* the manifests for touched layers; the drill becomes the interrogation that
   finds what the manifests missed, not a substitute for them.
6. **Coverage-bearing receipts** — a build/verify/QA receipt cites rows
   replayed/passed/failed **and** the completeness-hunt receipts behind the denominator
   (§10). "QA passed" without row coverage is not a claim the harness accepts.
7. **The conformance sweep as a standing orchestrator** — peer to /compile and /audit; smoke
   tier on cadence, full tier at freezes and pre-certification. Adopt the existing
   `*-conformance-bless-rN` receipt naming (already muscle memory in the fork) rather than
   inventing a third scheme. On blocking: the mutation-pass doc's caution ("a gate calibrated
   on one run is noise enforcing itself") does not apply here — manifest rows are
   pre-certified contracts, not statistics needing a baseline — so the full-tier sweep MAY
   block certification; state this argument in the doc rather than leaving the tension
   unaddressed.
8. **Amendment protocol = GOLD-2 generalized** (§8): append-only amendment logs,
   same-commit currency, provenance links. Retire non-replayable checklists the manifests
   supersede.
9. **`check-manifest.py` sensor** in the existing sensor pattern (§6), wired into
   /flight-plan's sensor step.
10. **Memory-engine hook** — manifests are packet-assembly inputs: a build packet without the
    manifests for its touched surfaces is malformed, the same way a tainted packet is.
    Extraction sessions get the frozen artifact; builders get manifests and seed; nobody
    builds from prose alone.
11. **Promote the engine folklore.** The `deploy/` engine already practices
    manifest-adjacent machinery undocumented in the methodology tier: golden descriptors
    with GOLD-2, the named-allowlist discipline (`known-holes.yaml`: "extend ONLY with named
    entries, never a glob"), checkout-invariant measurement contracts (`engine-caps.yaml`).
    The incorporation is the moment to name these in methodology docs. `entities.yaml` is a
    consolidation target for engine config — the `manifests/` layer is a **sibling**, not a
    member; say so to avoid a third "eventually merge these" pending item.
12. **Migration path for instantiated projects.** Per the template's own rule, partial
    adoption doesn't bump `template_version`; the manifest layer needs its own MIGRATION.md
    section with numbered steps for retrofitting live instances (like the legacy business wiki itself).
13. **Change classification in the execution engine** (v2.1). Every dispatch declares which
    §9.1 move it is, and the engine gates each differently: **feature dispatches are
    amendment-first** — the gate refuses a feature dispatch whose rows are not already
    amended and certified (the amendment is the feature; the build restores conformance);
    **refactor dispatches are sweep-invariance-gated** — full-tier sweep before and after,
    zero row deltas, or it was not a refactor; **defect-fix dispatches cite the red rows**
    they turn green. The drift sensor classifies every red row via the amendment log:
    declared nonconformance (open amendment, build pending — a work queue) vs undeclared
    (no amendment behind it — a defect). A dispatch that declares no move, or a change that
    fits none, is malformed.
14. **Twin-build certification mode** (v2.1). An optional highest-assurance rung above
    adversarial review (§10, fifth hunt): two independent builders from the same certified
    manifest set + seed, contract-level behavioral diff, every divergence adjudicated to a
    missing row or a recorded don't-care. Reserve for T1/keystone surfaces; run one
    small-surface pilot early to calibrate the miss rate of the four cheap hunts. The
    workflow-subagent substrate (two isolated worker sessions, one hub adjudicating the
    diff) is already the right machinery.
15. **Keep the framing unified**: Rheoscope is a trust compiler. Provenance is what it
    compiles *from*; manifests are what it compiles *against*. The byline is the closing
    test: *provenance without manifests is documented ambiguity; manifests without
    provenance are unauditable assertion. The template ships both or it ships neither.*

---

*Grounding artifacts (this repo): v1 of this file (superseded 2026-07-15, in git history at
this path; ratification: `raw/2026-07-16-ryan-mdd-v21-ratified-supersedes-v1.md`),
`methodology/manifest-driven-development-paper.md` (MDD paper v0.2 — §3.6 and §8 items 6–7
carry the paper-side form of the v2.1 additions),
`raw/2026-07-15-session-mdd-post-build-taxonomy-twin-build.md` (v2.1 session raw),
`specs/build/mockups/ordering-web-SCREEN-MANIFEST.md`, `ordering-web-LOGIC-MANIFEST.md`,
`ordering-web-PARITY-SEED.json`, `scheduler/design-exports/INTERACTION-MANIFEST-*.md`
(3 files, 299 states), `scheduler/BUILD-SPEC-scheduler-module.md` (the counter-example),
`scheduler/design-directives.md`, `raw/2026-07-11-ryan-scheduler-interaction-manifests-mandated.md`,
`raw/2026-07-13-ryan-manifest-doctrine-all-layers.md`,
`raw/2026-07-11-session-logic-manifest-prebuild-gap-close.md`,
`raw/2026-07-12-session-scheduler-v33-manifest-conformance-tagged.md`.
Fork artifacts (the Rheoscope dogfood fork):
`core/methodology/verification-architecture.md` (Firewall Rule 3, Differential Oracle,
mutation-pass caution), `core/methodology/execution-engine.md` (tier protocols, enforcement
rule), `deploy/descriptors/golden-descriptors.yaml` (GOLD-2), `deploy/known-holes.yaml`,
`deploy/compile-backends.py` (dispatch-manifest.json collision), `MIGRATION.md`.*

---

## Harness Incorporation Annex (template-authored — NOT part of the ratified v2.1 text)

*verified-against: 3.0 (2026-07-20)*

This annex maps §12's fifteen incorporation directives to where each landed in the
Rheoscope harness template. The incorporation design brief (with its cross-vendor
verification log) is `harness-v3.0/specs/behavioral-manifest-incorporation-brief-2026-07-20.md`.

### Reference resolution for this copy

- §12's "fork artifacts" paths refer to the Rheoscope dogfood fork
  (the dogfood fork); the template mirrors them under `core/methodology/` and
  `capabilities/knowledge-os/extracted/deploy/`.
- The "Grounding artifacts (this repo)" footer above refers to the upstream live control
  plane, where the doctrine was authored and ratified. Those exemplar artifacts are NOT
  shipped with the template (they carry live business content); the canonical format doc
  carries synthetic examples instead.

### Directive → landing map

| # | Directive | Landed at |
|---|---|---|
| 1 | Naming firewall | `manifest-format.md` §1 (three-way: behavioral / dispatch / result manifest); `execution-engine.md` Part 7 disambiguation note; GLOSSARY entries |
| 2 | Firewall Rule 3 amendment | `verification-architecture.md` Rule 3 ("The Manifest Set Is Upstream; Specs Are Its Projections", amended 2026-07-20) |
| 3 | First-class manifests layer | `manifest-format.md` (directory layout, frontmatter, row discipline, MANIFEST-INDEX) |
| 4 | Tier-scaled build gate | `tier-definitions.md` per-tier manifest-state rows; `execution-engine.md` Part 2 step-0 gate + canonical gate banner; Builder Prompt Template Manifests block |
| 5 | Verification Spec Drill relationship | `verification-architecture.md` Parts 2–3 (spec sections derived from manifests; drill hunts what manifests missed) |
| 6 | Coverage-bearing receipts | `execution-engine.md` report formats; `verification-architecture.md` receipt language |
| 7 | Conformance sweep orchestrator | `core/skills/conformance/SKILL.md` (smoke + full tiers; `<surface>-conformance-bless-rN` receipts; may-block argument stated) |
| 8 | Amendment protocol (GOLD-2 generalized) | `manifest-format.md` § Amendments |
| 9 | check-manifest.py sensor | `deploy/check-manifest.py` (ships with the knowledge-os capability; hermetic self-tests; wired into /flight-plan sensors and /doctor check 6) |
| 10 | Memory-engine hook | `deploy/assemble.py` (declare-or-exempt, tier-restricted, fail-closed manifest coverage on build/fix packets) |
| 11 | Engine folklore promotion | `manifest-format.md` § Manifest-shaped machinery already in the engine |
| 12 | Migration path | `MIGRATION.md` § v3.0 → v3.0 + behavioral manifests (partial adoption) |
| 13 | Change classification | `execution-engine.md` (every dispatch declares its §9.1 move; per-move gating) |
| 14 | Twin-build certification mode | `manifest-format.md` § Twin-build certification mode (protocol shipped; the early small-surface pilot is deferred-with-named-reason — it requires a CERTIFIED manifest set + pinned seed for a real product surface; the standing obligation clause lives in that section) |
| 15 | Unified framing | The byline heads this file; trust-compiler framing in TOUR, GLOSSARY, SYSTEM-MAP, and `/orient`'s artifact table |

### Capability note

The doctrine and format contract are core (every instantiation ships them). The
*mechanical* enforcement — `check-manifest.py`, `manifest-layers.yaml`, the `assemble.py`
packet gate — rides the knowledge-os capability, which wires `deploy/`. A project
instantiated without knowledge-os still carries the doctrine, the format contract, the
build gate in its tier protocols, and the conformance skill; it lacks only the automated
sensors, and its MANIFEST-INDEX discipline is enforced by session practice + /doctor's
docs checks rather than by the engine.

---

## v2.2 Amendment Addendum (ratified 2026-07-20 — delegated authority; v2.1 text above is untouched)

Ratification record + full reasoning + cross-vendor log:
`harness-v3.0/specs/mdd-v22-ratification-2026-07-20.md` (template repo). Thirteen
amendments, numbered per the incorporation brief §6; the v2.1 body above remains byte-intact
per the append-only discipline (§8 applied to the doctrine itself). Summary:
1. Naming firewall widened three-way: behavioral / dispatch / RESULT manifest (§12.1).
2. Frontmatter `schema_extensions` + `row_shape` — "extended, never loosened" made
   sensor-checkable (§6).
3. Canonical amendment record `A<n> | date | row | prior | new | provenance`; narrative
   grandfathered (§8).
4. Row lifecycle: the DRAFT flag (+ amendment log) is the sole canonical carrier; ID
   prefixes optional and non-authoritative (§5) — AMENDED from the proposed dual form.
5. The `OPEN — missing: <fact>` field marker admitted; OPENs are countable and
   certification dispositions each one (§5) — AMENDED with countability.
6. `confidence: probe-derived` added as the third enum value (§6).
7. Canonical `source_artifacts` pin schema: path/repo/ref/sha256 requiredness (§6).
8. Layer registry SHOULD be a machine-validated artifact; reference implementation
   `deploy/manifest-layers.yaml` (§4) — AMENDED to SHOULD.
9. RUBRIC grading protocol: substrate-separated grader, per-criterion reasoning in the
   receipt, hub on disagreement (§5) — AMENDED with the concrete form.
10. Gate semantics: LIVE satisfies a CERTIFIED requirement; SUPERSEDED never satisfies
    any tier (§3).
11. Cross-repo same-commit currency: the amendment commit binds the manifest repo;
    provenance links the foreign `git:<sha>` pin (§8).
12. Manifests and seeds carry origin like any packet content in a trust-boundary program;
    quoting tainted content does not launder it (§7) — AMENDED to portable phrasing.
13. Inverted coverage stays named-not-built; receipts' per-change row citations are the
    zero-cost approximation path (§10).

---

## v2.2 Amendment Addendum, continued (2026-07-24 hand-over incorporation)

Source: 2026-07-24 MDD hand-over bundle (live control plane +
the scheduler port repo (private; exact commit pinned in the maintainer-side hand-over record)), adjudicated and incorporated this session per the
operator's direction. Same numbering series as the thirteen entries above; the v2.1 body and
items 1–13 remain byte-intact per the append-only discipline (§8 applied to the doctrine
itself).

14. Layer registry gains two RESERVED entries (`deploy/manifest-layers.yaml`, §4 admission
    rule): `rendering-fit` — real-data rendering invariants, orthogonal to conformance-to-design
    (a build can be pixel-faithful to the frozen export and still truncate or overflow real
    content at real density); admission rationale cites the scheduler's RENDERING-FIT manifest
    class, born 2026-07-23 from a live-board operator observation. `config` — provisioned/seeded
    business configuration shipping without per-value provenance (a bad value indistinguishable
    from a deliberate business decision once live), replayed by schema-read plus
    operator-sign-off rather than browser/golden replay — a materially different replay
    mechanism; admission rationale cites the tenant-provisioning exemplar, whose full manifest →
    sign-off → migration → conformance loop ran end-to-end 2026-07-23. Both stay RESERVED (name
    + rationale only) until a project
    in *this* program incurs the named cost; exemplars preserved at
    `harness-v3.0/mdd-handover-2026-07-24/` (template repo, dev-history, excluded from the
    release artifact).
15. The `CONFLICT — <source A> says X, <source B> says Y` row marker admitted as a sibling of
    the `OPEN` marker (`manifest-format.md` §4): same treatment — countable, blocks CERTIFIED
    with a nonzero count — but remediated specifically by an operator ruling recorded via the
    amendment log (§8), never dispositioned as an acceptable-unknown the way OPEN is. Adapted,
    not adopted verbatim: the hand-over bundle's exemplar resolves CONFLICTs inline in prose
    tables; the harness generalizes that into the existing flag/marker mechanism rather than
    adding a second row-lifecycle carrier (consistent with §5's "one canonical carrier" rule
    above).
16. Same-commit currency (`manifest-format.md` §8) tightened to state explicitly that an
    amendment ships atomically with **every** pinned artifact it invalidates — e2e/golden/
    conformance fixtures and pinned tests outside the manifest file included — in the same
    declared change, not a row-scoped subset of it. Demonstrating case: the scheduler's
    RF-DB-008 fluid-width amendment (2026-07-23), which named this exact discipline for its own
    geometry-pinned e2e assertions and conformance rows.
17. Orthogonal-classes principle stated directly (`manifest-format.md` §1): manifest classes
    (registry layers) measure different things about the same surface — conformance-to-source
    vs. real-data-fitness is the worked example (rendering-fit vs. interaction/design) — and
    neither substitutes for the other; the taxonomy grows only via the layer-registry admission
    rule, never by inventing a variant column on an existing layer.
18. **Wholesale import declined, general rules adopted** (recorded here rather than silently
    dropped, per invariant-4 discipline): the hand-over bundle's 91-manifest scheduler corpus,
    its `SOURCE-LOCK.md` exact-commit/git-lock provenance mechanism, and its
    `check-manifests.mjs` validator are **not ported wholesale** into this harness — but
    `check-manifests.mjs`'s two general-purpose rules **were** adopted into
    `deploy/check-manifest.py` as checks 9–10 (bidirectional amendment↔row linkage;
    cross-surface row-ID uniqueness; 69/69 self-test). The corpus is project (business) content
    and stays in its own repo — a template ships doctrine and format contracts, not another
    program's manifests. `SOURCE-LOCK.md`'s lock is a heavier, cross-machine, multi-repo
    exact-commit/sha256 ledger keyed to paths on a specific contributor's machine; it
    duplicates, at far higher weight, what `manifest-format.md` §3's `source_artifacts` pin
    (`path`/`repo`/`ref`/`sha256`) already covers for this harness's needs, and its paths do
    not generalize across instantiations — that project-specific machinery is what stays
    declined. `check-manifests.mjs` itself remains scheduler-corpus-specific tooling parallel
    to (not a replacement for) `deploy/check-manifest.py`, which already ships here; only its
    two general rules, not the script, crossed over.

    **Two upstream-candidate findings** (flagged for upstream intake to the live control plane, not a
    harness change): comparing the hand-over bundle's `control-plane-wiring/` reference against
    this harness's own mechanism surfaced that (a) the harness's execution-engine per-move
    refusal logic (directive 13 above: feature/refactor/defect-fix dispatches gated
    differently, mechanically, per move) and (b) `deploy/assemble.py`'s fail-closed manifest
    coverage gate (declare-or-exempt, tier-restricted, refuses to assemble on a gap) are both
    **stricter** than the control plane's manifest gate, which is enforced through dispatch-hub
    session discipline (`dispatch-hub-SKILL.md` § Author) rather than a mechanical refusal. This
    is named as a candidate for the control plane to adopt, not a finding that requires any
    change here.

## v2.2 Amendment Addendum, continued (2026-07-28 registry-count reconciliation)

Same numbering series as the fifteen entries above; the v2.1 ratified body remains
byte-intact (the reader pointer lands in the template-authored provenance comment at the
top of this file, never in the ratified span).

16. §4.3's header and enumeration read "(seven)" as of the v2.1 ratification; the registry
    (`deploy/manifest-layers.yaml`) has held **nine** RESERVED layers since item 14's two
    admissions landed registry-side (2026-07-23: `rendering-fit`, `config`) without the
    doctrine's snapshot being reconciled — a five-day prose-vs-registry drift caught by an
    external README-level critique on 2026-07-28 (backlog v3.0-71). Resolution: the registry
    is the single live home of the RESERVED list; the provenance comment atop this file
    carries a READER NOTE pointing at it; the template's `MAINTENANCE.md` self-sweep lens (c)
    gains a registry-vs-prose layer-count agreement check so the next admission cannot
    recreate this class.
