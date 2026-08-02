# Behavioral Manifests — format contract (Rheoscope harness)

*This is the operational contract implementing `manifest-driven-builds.md` §§3–10 in this
harness — sessions and `check-manifest.py` parse what this file defines.*

> **Provenance without manifests is documented ambiguity; manifests without provenance are
> unauditable assertion. The template ships both or it ships neither.**

*verified-against: 3.0 (2026-07-20)*

---

## 1. Naming firewall (three concepts, never a bare "manifest")

Three distinct artifacts share the word "manifest" in this harness. None of them is ever
called a bare "manifest" in prose or code where the referent could be ambiguous — say the
qualified name.

- **behavioral manifest** — a per-layer enumerated behavioral contract. This document's
  subject. Lives under `manifests/` (§2).
- **dispatch manifest** — `dispatch-manifest.json`, the memory engine's compile-pipeline
  transport artifact. F17-attested; consumed by `compile-backends.py`. It carries what a
  build packet assembled, not what a surface must behave like. Never confuse with a
  behavioral manifest.
- **result manifest** — the execution engine's parallel-build crash-recovery record
  (`execution-engine.md` Part 7). A killed Builder's committed work plus this record is the
  only state the merge queue trusts; it says what a Builder did, not what a surface must do.

**Rule**: prose and code say the qualified name wherever the referent could be ambiguous.
Greppability is the point — a reader or a `grep` for "manifest" across this repo must be able
to tell which of the three concepts a hit belongs to without opening the file.

**A second orthogonality, inside behavioral manifests themselves**: different registry layers
(`deploy/manifest-layers.yaml`) measure different things about the same surface, and one never
substitutes for another. Conformance-to-source (interaction/design: does the build match the
frozen export) and real-data-fitness (rendering-fit: does the build still hold up once real
content replaces demo-length placeholders) are a worked example — a row can be `EXACT` in one
and still fail the other; passing one proves nothing about the other. Same-shape orthogonality
holds for data vs. config (schema/lifecycle rules vs. provisioned per-value business content).
The taxonomy is expected to grow the same way it always has — by the layer-registry admission
rule (a named incurred cost, a row schema, a replay mechanism, §12), never by inventing a
variant column on an existing layer or folding a new concern into one that already exists.

## 2. Where manifests live

- `manifests/<surface>/` per project surface; one file per touched registry layer, named
  `<layer>-MANIFEST.md` (e.g. `manifests/orders-web/interaction-MANIFEST.md`); one
  `MANIFEST-INDEX.md` per surface, at `manifests/<surface>/MANIFEST-INDEX.md`.
- The directory is created on first extraction, not at init. A freshly instantiated project
  carries no `manifests/` tree; init-validate does not sentinel it, and its absence is not a
  defect.
- Frozen source artifacts (mockups, exports, legacy corpus snapshots) live beside or under the
  surface directory — e.g. `manifests/orders-web/source/orders-web-checkout-v4.html`. Every
  manifest pins them by sha256 in `source_artifacts` (§3); the frozen copy and the pin must
  agree, or the sensor's frontmatter check fails.
- Extraction-drive evidence (state screenshots, accessibility-tree dumps, probe logs) lives at
  `manifests/<surface>/evidence/<run-date>/` — beside the rows it proves, one folder per
  extraction run, never loose at any root (workspace-governance rule, 2026-07-22).

## 3. Frontmatter schema

Every manifest opens with YAML frontmatter. Full synthetic example (surface `orders-web`, a
fictional ordering UI used only for illustration in this document):

```yaml
manifest: interaction              # registry layer key
surface: orders-web                # project surface
version: "1.2"                     # string, not a bare number
status: EXTRACTED                  # DRAFT | EXTRACTED | CERTIFIED | LIVE | SUPERSEDED
source_artifacts:
  - path: manifests/orders-web/source/orders-web-checkout-v4.html
    sha256: 9f2b1c4e7a3d5f608b1c9e2a4d7f0361c8e5a29b4d6f7013c2a5e8b9d0f1a2b3
  - path: manifests/orders-web/source/orders-web-cart-v4.html
    sha256: 1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809
  - path: exports/checkout-2026-06-01.json
    repo: orders-legacy-export
    ref: "git:4f8a9c2d:exports/checkout-2026-06-01.json"
extracted: 2026-06-02
amended: 2026-06-14 (A1-A3)        # only present once any amendment exists
toolchain: playwright-mcp
viewports: [1280x800, 390x844]
variant_axes: [role, cart-state]
seed: orders-web-SEED.json@v2
declared_rows: 84
confidence: source-crosschecked    # source-crosschecked | interaction-only | probe-derived
row_shape: table                   # table | sections | hybrid
schema_extensions: []
```

Field table:

| Field | Meaning |
|---|---|
| `manifest` | The registry layer key this file is written against (interaction, logic, design, data, authorization, integration, or an ACTIVE/RESERVED layer once admitted). Validated against the layer registry. |
| `surface` | The project surface this manifest belongs to; must match the directory it lives under. |
| `version` | String. Bumped on any frontmatter or row-shape change; not the same counter as amendment IDs. |
| `status` | One of the five-value enum, §7. |
| `source_artifacts` | List of `{path, repo?, ref?, sha256?}`. **Pin rules**: `sha256` is REQUIRED for a working-tree path (a plain `path` with no `ref`). For a `git:<sha>:<path>` ref, the commit pin substitutes for content pinning and `sha256` MAY be omitted. `repo` is required whenever the artifact lives outside the manifest's own repo (as in the legacy-export row above); omit it for artifacts in-repo. |
| `extracted` | Date the extraction session ran. |
| `amended` | Date plus the amendment ID range, present only once `## Amendments` (§8) holds at least one entry. |
| `toolchain` | What drove the extraction: `playwright-mcp`, `node-playwright`, `request-probe`, etc. |
| `viewports` | List of viewport dimensions exercised. Applies to interaction/design layers. |
| `variant_axes` | List declaring what the row `variant` field ranges over (e.g. `[role, cart-state]`); a row's `variant` cell is meaningless without this declaration. |
| `seed` | `<path>@<version>`, for layers computing over a seed (logic, data, and any layer with goldens). |
| `declared_rows` | Integer. The sensor checks this against the computed row count for the file's `row_shape`; mismatch is a finding (the flagship-manifest defect class this format exists to catch). |
| `confidence` | `source-crosschecked` (readable source pressured the enumeration) \| `interaction-only` (no readable source was available to cross-check against) \| `probe-derived` (extracted via corpus observation / request probes, not a driven UI — **HARNESS EXTENSION** of the doctrine's two-value enum, proposed upstream for v2.2). |
| `row_shape` | `table` (canonical `id \| ... \|` rows) \| `sections` (`### <id>` subsection rows, e.g. migration/failure layers) \| `hybrid` (table plus labeled fields, e.g. integration) — **HARNESS EXTENSION**: tells the sensor how to count rows for the `declared_rows` check. |
| `schema_extensions` | List of declared extra columns or fields beyond the canonical row set (§4). **HARNESS EXTENSION**: makes the doctrine's "extended, never loosened" rule machine-checkable — a column or field present in the body that is not declared here is a sensor finding, not a silent pass. |

## 4. Row discipline

**Canonical row fields**: `id | name | replay path | expected observable | variant | flags |
evidence | kind`.

Layer-specific shapes replace `name` / `replay path` / `expected observable` with their own
layer fields when the doctrine's §4 per-layer row schema calls for it — migration's
`precondition | transform | verify-query | rollback`, async's `trigger | cadence | observable
| idempotency`, and so on. A layer using its own shape declares the substituted fields in
`schema_extensions` and states its shape in `row_shape`; `id`, `flags`, `evidence`, and `kind`
are never replaced — they are the columns every sensor check and every cross-reference (§5,
§8) depends on.

Two illustrative rows from a synthetic `orders-web` interaction manifest:

| id | name | replay path | expected observable | variant | flags | evidence | kind |
|---|---|---|---|---|---|---|---|
| `checkout-empty-cart` | Empty cart state | Navigate to `/cart` with seed `empty-cart` | Body text reads "Your cart is empty" | role=guest | — | a11y-tree | EXACT |
| `checkout-submit-oos-item` | Submit blocked when an item goes out of stock mid-checkout | Add item to cart, flip its stock to 0 via admin seed, attempt submit | Submit button disabled; banner text "This item is no longer available" | role=customer | `BUILD-MUST-DIVERGE [A2]` | screenshot | EXACT |

**Two kinds**:

- **EXACT** — the observable is matched literally (visible text, a number, a state, a
  payload).
- **RUBRIC** — the observable is graded against criteria stated in the row itself. "Looks
  right" is not a rubric; a RUBRIC row that does not state its grading criteria is malformed.
  Grading protocol (ratified 2026-07-20, decision #9-as-amended): a RUBRIC row is graded by a
  session other than the one that produced the artifact under grade — the builder/verifier
  firewall generalized to judgment rows; the grade lands in the receipt with per-criterion
  reasoning, never a bare scalar; grading disagreements escalate to the hub, never average out.

**Controlled flags** (closed vocabulary): `UNREACHABLE | UNREACHABLE-BY-DESIGN | SURPRISE |
NOOP | BUILD-MUST-DIVERGE | TIME-SENSITIVE | REAL-MODE-ONLY | DRAFT`. `DRAFT` is the harness's
canonical row-lifecycle carrier — see §5; it is not a description of row quality, it is a
lifecycle state. The flags cell may stack flags semicolon-separated, and any flag may carry a
bracketed pointer to the amendment or target row that resolves it, e.g.
`BUILD-MUST-DIVERGE [A3]` (points at the amendment that will retire the divergence) or
`SUPERSEDED-BY [checkout-submit-oos-item-v2]` (points at the row that replaces this one).

**The OPEN marker**: a row blocked on a missing operational fact carries NO invented flag.
Instead, whichever field is blocked contains the literal marker `OPEN — missing: <fact>`.
Example:

| id | name | replay path | expected observable | variant | flags | evidence | kind |
|---|---|---|---|---|---|---|---|
| `checkout-guest-promo-field` | Promo code field visibility for guest checkout | Navigate to `/checkout` as guest, seed `promo-eligible-cart` | `OPEN — missing: confirmed guest-checkout promo policy` | role=guest | — | source-read | EXACT |

This is a **HARNESS EXTENSION** blessing a practice already observed in the exemplar corpus
(one manifest improvised an equivalent marker to avoid inventing an eighth flag); proposed
upstream for v2.2. OPEN markers are countable: a certification receipt states the surface's
OPEN count, and CERTIFIED with a nonzero count requires each OPEN individually dispositioned
as an acceptable-unknown with a named owner in the receipt — an OPEN field is underdetermined
behavior, exactly what adversarial review hunts. (Ratified 2026-07-20, decision
#5-as-amended.)

**The CONFLICT marker**: a row where two sources disagree carries NO invented flag and no
silent pick of one source over the other. Instead, whichever field is contested contains the
literal marker `CONFLICT — <source A> says X, <source B> says Y`. Example:

| id | name | replay path | expected observable | variant | flags | evidence | kind |
|---|---|---|---|---|---|---|---|
| `checkout-dual-promo-stacking` | Promo stacking behavior when two codes both qualify | Navigate to `/checkout` with seed `dual-promo-cart` | `CONFLICT — pricing-spec.md says only the highest-value promo applies, legacy-pricing-engine.js says promos stack additively` | role=customer | — | source-read | EXACT |

This is a **HARNESS EXTENSION** generalizing a practice already run in the field (the
tenant-provisioning exemplar surfaced three in-manifest CONFLICTs, each resolved by an
explicit operator ruling recorded in the manifest — 2026-07-24 hand-over bundle). CONFLICT
markers are countable exactly like OPEN markers: a certification receipt states the surface's
CONFLICT count, and CERTIFIED with a nonzero count requires each CONFLICT individually
resolved — never silently picked by a session — by an operator ruling recorded via the
amendment log (§8), the row's `prior`/`new` values carrying the two disputed readings and the
ruling that broke the tie. A CONFLICT field is a live disagreement between sources, exactly
what adversarial review hunts. (Harness incorporation, 2026-07-24 — see
`manifest-driven-builds.md` v2.2 Amendment Addendum item 15.)

**Evidence modality** is a row field, not a footnote: `a11y-tree | screenshot |
computed-style | source-read | request-probe`. A new modality is added only via
`schema_extensions`, never used bare.

**Stable IDs**: kebab-case semantic slugs. An ID is never reused and never renumbered once a
manifest reaches EXTRACTED. Rows promoted from a completeness cross-check (§10 of the
doctrine's coverage-honesty discussion) carry the `gap-` prefix. Findings, fixes, receipts,
and amendments reference rows by ID exactly the way an issue tracker references tickets — a
row ID is a permanent handle.

## 5. Row lifecycle (one canonical carrier)

- The `DRAFT` flag, resolved through the amendment log (§8), is the SOLE canonical lifecycle
  carrier: a row is PROPOSED iff it carries `DRAFT` (or its `DRAFT` was graduated by a named
  amendment — read graduation via the log). Machine truth lives in the flags cell plus the
  log, nowhere else.
- ID prefixes (`obs-`/`prop-`, or a layer's own like `seam-`/`async-`) are an OPTIONAL
  birth-convention: they record what a row WAS at authoring and are explicitly
  NON-AUTHORITATIVE — IDs are immutable (§4), so a graduated `prop-` row keeps its prefix
  forever; never read lifecycle state from a prefix. (Ratified 2026-07-20, decision
  #4-as-amended: the earlier prefix-AND-flag form desyncs by construction.)
- Graduation from PROPOSED to pinned is recorded **once**, as a single amendment-log entry
  covering the whole batch (§8's bulk-graduation pattern) — never by editing N flag cells one
  at a time. After a graduation amendment lands, every `DRAFT` token it names is read as
  resolved via that amendment, not via the flag cell alone.

Do NOT invent a per-manifest lifecycle column. The exemplar corpus independently improvised
five different carriers for this same concept — a `DRAFT` flag in one manifest, a
`row_status` column in another, a `provenance` column in a third, ID prefixes in a fourth, and
section headings in a fifth. All five collapse to the FLAG mechanism above in every new
manifest written against this contract.

## 6. MANIFEST-INDEX schema

One `MANIFEST-INDEX.md` per surface, holding a single YAML block. Full synthetic example:

```yaml
surface: orders-web
updated: 2026-07-18
layers:
  interaction:
    file: manifests/orders-web/interaction-MANIFEST.md
    status: CERTIFIED
    rows: 84
    certified_by: receipts/orders-web-interaction-certify-r1.md
  logic:
    file: manifests/orders-web/logic-MANIFEST.md
    status: EXTRACTED
    rows: 22
    certified_by: null
  design:
    file: manifests/orders-web/design-MANIFEST.md
    status: DRAFT
    rows: 0
    certified_by: null
  data:
    status: MISSING
  authorization:
    file: manifests/orders-web/authorization-MANIFEST.md
    status: CERTIFIED
    rows: 14
    certified_by: receipts/orders-web-authorization-certify-r1.md
gate:
  next_increment: "orders-web checkout redesign"
  tier: T2
  touched_layers: [interaction, logic, design]
  state: CLOSED
  date: 2026-07-18
```

Each `layers.<key>` entry is `{file, status, rows, certified_by}`; `certified_by` (a receipt
path) is REQUIRED once `status` is `CERTIFIED` or `LIVE`, and null below that. A layer with no
manifest yet is `status: MISSING` and carries no other fields. The `gate` section names the
next increment, its tier, the layers it touches, and whether the gate is `OPEN` or `CLOSED`
for that increment as of the stated date — this is the one-line check that replaces "a design
freeze without manifests is an incomplete freeze" as a judgment call.

**The INDEX is the machine-checkable face of the build gate; the gate reads this file.** Any
derived graph, board, or dashboard built from manifests is a projection of the INDEX and the
manifest files, never an independent source. The INDEX itself may be regenerated from the
manifest files or hand-maintained — either is fine — but the manifests are always the truth
the sensor checks the INDEX against, not the reverse. Files win.

## 7. Status enum and gate semantics

`DRAFT` (being authored) → `EXTRACTED` (enumeration complete) → `CERTIFIED` (adversarial
completeness review done, receipt on file) → `LIVE` (certified, and the surface has shipped;
the standing drift sensor now applies) → `SUPERSEDED` (replaced by a newer manifest for the
same layer/surface; never satisfies any gate).

Gate table (doctrine §3), restated for this harness:

| Tier | Required manifest state for touched layers |
|---|---|
| T1 / T2 | `CERTIFIED` (`LIVE` also satisfies — it is certified-plus-shipped) |
| T3 | `EXTRACTED` or better |
| T4 | Exempt, with the exemption and its reason named in the receipt |

`SUPERSEDED` never satisfies any tier's gate, at any tier — a superseded layer manifest reads
as `MISSING` for gate purposes until a replacement is EXTRACTED or better. The `LIVE`/
`SUPERSEDED` gate semantics stated here are a **HARNESS clarification** of the doctrine's §3
table (which enumerates `CERTIFIED`/`EXTRACTED` but is silent on the other three enum values);
proposed upstream for v2.2.

## 8. Amendments (GOLD-2 generalized)

Every manifest carries an append-only `## Amendments` log. Canonical record shape (a
**HARNESS canonicalization** of the corpus's best-practice manifest, generalized):

```
**A<n>** | date <YYYY-MM-DD> | row: <id> | prior: <value> | new: <value> | provenance: <raw/decision/receipt link>
```

Example, for the OPEN row shown in §4 once the missing fact is resolved:

```
**A1** | date 2026-06-14 | row: checkout-guest-promo-field | prior: OPEN — missing: confirmed guest-checkout promo policy | new: promo field hidden for guest checkout | provenance: decisions/2026-06-13-guest-promo-policy.md
```

A multi-row amendment groups under one `A<n>` with per-row sub-entries rather than minting an
ID per row:

```
**A3** | date 2026-07-01 | rows: bulk graduation, 12 rows (prop-cart-* → pinned, cart layer) | prior: flags: DRAFT | new: DRAFT removed, rows pinned | provenance: receipts/orders-web-cart-extraction-r2.md
```

Narrative-form amendments in pre-existing (grandfathered) manifests are not rewritten to this
shape retroactively; the fixed shape binds new amendments going forward.

**Same-commit currency**: a change to the source artifact, the seed, or the build contract
that invalidates a row lands in the **same commit** as that row's amendment. A stale manifest
is a defect, not a backlog item. This atomicity is total, not row-scoped: when an amendment
invalidates any pinned artifact outside the manifest file itself — an e2e assertion, a golden
fixture, a conformance-manifest row, a pinned test living anywhere in the repo — that artifact
updates in the **same declared change** as the amendment, never as a follow-up commit or a
backlog item. Demonstrating case (2026-07-24 hand-over bundle): the scheduler's RF-DB-008
fluid-width design amendment (2026-07-23) — adopted after the frozen export's fixed pixel
geometry was found to be a review-canvas artifact rather than a ratified business requirement
— states explicitly that every geometry-pinned e2e assertion and every touched
conformance-manifest row amends in the same declared change as the width/density amendment
itself, not deferred to a follow-on fix wave. (Harness incorporation, 2026-07-24 — see
`manifest-driven-builds.md` v2.2 Amendment Addendum item 16.) **Cross-repo form**: when the
invalidating change lives in
another repo, same-commit binds this repo's amendment commit — the change itself lands
wherever its own repo requires — and the amendment's `provenance` field links the foreign
commit via a `git:<sha>` pin, e.g. `provenance: git:4f8a9c2d — legacy checkout export
regenerated`.

Rows are never silently deleted. A row replaced by a newer one carries a `SUPERSEDED-BY
<amendment-id>` flag (§4) and stays in the file.

Two more named amendment patterns:

- **Bulk graduation** (§5) — one amendment entry for a whole DRAFT batch, as shown above.
- **Source-pin supersession** — re-point and re-hash a `source_artifacts` entry, with the old
  pin left retrievable in git history, and the amendment states whether the content delta
  affects any row and why:

```
**A4** | date 2026-07-10 | row: source_artifacts (interaction manifest) | prior: orders-web-checkout-v4.html @ sha256 9f2b1c4e... | new: orders-web-checkout-v5.html @ sha256 <new-hash> | provenance: decisions/2026-07-09-checkout-v5-redesign.md — content delta: promo-field markup added; row checkout-guest-promo-field re-verified against v5, no row change required
```

**Red rows and the three moves** (doctrine §9.1): a red row (build diverges from a manifest
row) **with** an open amendment-log entry behind it is *declared* nonconformance — a work
queue item, red by design because the definition stepped ahead of the build on purpose. A red
row **without** one behind it is *undeclared* nonconformance — a defect. Same observable,
opposite meaning; the amendment log is the discriminator every conformance sweep (§12) reads
to tell the two apart.

## 9. Seed governance

A surface owns a versioned **seed set**, not a single fixture — an empty-state, an
error-state, a different-day seed, or an explicit `REAL-MODE-ONLY` flag with a named owner,
each as its own seed. Goldens and manifest rows that compute over a seed pin it as
`<path>@<version>` (§3's `seed` field); a golden that cannot be reproduced against its pinned
seed escalates to the hub, never silently obeys either the seed or the manifest.

The `_seed_authoring_rules` pattern is canonical: corrections are written inline with the math
shown (`_reconciled`), dead rows are tombstoned rather than deleted, and honest `GUESS` flags
are tracked to resolution rather than left standing.

**Origin note** (restated 2026-07-20, decision #12-as-amended, portable form): manifests and
seeds are packet/context content in any program with a trust boundary, and they carry origin
like any other served content; quoting tainted content does not launder it. Programs with
mechanical origin machinery (this harness: `origin.py` / `taint_quarantine`) enforce this
mechanically; programs without such machinery enforce it by session discipline instead.

## 10. Manifest-shaped machinery already in the engine

The `deploy/` engine already practices this discipline in three places, undocumented as such
until now:

- **`deploy/descriptors/golden-descriptors.yaml`** (GOLD-2) — answer keys flip in the same
  commit as the state they assert against. The same-commit currency rule of §8 **is** this
  discipline, generalized from goldens to every manifest row.
- **`deploy/known-holes.yaml`** — a named allowlist: "extend ONLY with named entries, never a
  glob." This is the named-risk admission rule (doctrine §4, the layer-registry admission
  rule) in miniature — nothing enters without a name and a reason.
- **`deploy/engine-caps.yaml`** — checkout-invariant measurement contracts: LF-normalized byte
  length, never raw filesystem size. A measurement contract stated once and replayed
  identically on every checkout, exactly like a manifest row's replay path.

Stated plainly so no future session invents a merge: **`deploy/entities.yaml`** is the
governed entity vocabulary and a consolidation target for engine config. The `manifests/`
layer is its **sibling**, not a member. Neither absorbs the other, and there is no
"eventually merge these" pending item for this pair.

## 11. Twin-build certification mode (optional highest-assurance rung)

Protocol: two independent builder sessions build from one CERTIFIED manifest set plus one
pinned seed. Diff the two products **through the contract** — observable behavior only; the
manifest set defines which differences count, and byte identity is neither expected nor
desirable. Every observable divergence adjudicates to exactly one of two outcomes: a
**missing row** (amend it, §8) or a genuine **don't-care** (recorded in the surface's
don't-care ledger with provenance — the approved-divergences ledger pattern, generalized).
Iterate build → diff → amend until the two builds are contract-indistinguishable. Reserve this
mode for T1/keystone surfaces — it is the most expensive completeness hunt available.

Machinery: two isolated worker sessions plus one hub adjudicating the diff — the harness's
workflow-subagent substrate already is the right shape for this; no new machinery is owed.

**STANDING OBLIGATION** (verbatim): "The first program surface whose manifest set reaches
CERTIFIED at small scale owes the twin-build pilot (doctrine §12.14 — deferred 2026-07-20 with
named reason: no CERTIFIED manifest set + seed existed in the program); record its receipt
beside the certification receipt and fold the measured miss-rate of the four cheap hunts back
into this section."

**DISCHARGED 2026-07-23** on the `sweep-briefing` format surface (the program's first
CERTIFIED small-scale set; receipt `receipts/sweep-briefing-twin-build-pilot-r1.md` beside
the bless receipt, on the proving fork). **Measured miss-rate of the cheap hunts: 2/18
(~11%)** — sixteen rows from source cross-check + ambiguity recording, two more found by
twin-build in round 1, zero further in round 2. Both misses shared one blind spot worth
naming for every future extraction: **behavior under input conditions the source prose
never described** (an artifact element the contract never mentioned — a preamble; a state
the skeleton never exemplified — All-clear under partial failure). When running the cheap
hunts on a text surface, explicitly ask "what does the contract say when the inputs are
unhealthy/absent/mixed?" — that question would have caught both misses without a pilot.

## 12. Tooling and wiring

- **`deploy/check-manifest.py`** (ships with the knowledge-os capability): checks frontmatter
  completeness and vocabulary; validates the `manifest:` layer key against
  `deploy/manifest-layers.yaml` (the doctrine's §4 layer registry, as a machine artifact);
  checks `declared_rows` against the computed count, per the file's declared `row_shape`;
  checks ID uniqueness; checks that every flag token is a member of the controlled vocabulary
  union the file's declared `schema_extensions`; verifies `source_artifacts` sha256 pins for
  in-repo working-tree paths (SKIP-with-note for absent files and for `git:`/external-repo
  refs — absence can be by design); checks MANIFEST-INDEX ↔ file coherence (status, row
  count, path, and that each INDEX layer key matches its referenced file's `manifest:`
  field); checks the append-only `## Amendments` section is present (§8; an empty stub is
  fine); counts OPEN markers (§4) — informational on DRAFT/EXTRACTED, a FAIL on
  CERTIFIED/LIVE (certification must disposition every OPEN; v2.2 decision #5). It
  **degrades, never blocks**, at flight-plan briefing time — same posture as every other
  sensor in that step.
- The **conformance sweep** (`/conformance`, a core skill) is different: it runs at
  certification time, and there it **may block**. Smoke tier runs on cadence; full tier runs
  at freezes, pre-certification, and after fix waves. Receipts are named
  `<surface>-conformance-bless-rN`. A coverage claim cites rows replayed/passed/failed **plus**
  the completeness-hunt receipts behind the denominator — source cross-check, variant matrix,
  precondition sweep, adversarial review, and twin-build divergence where it was run.
  "100% coverage" over an unpressured denominator is the new "QA passed," and this contract
  does not accept it as a claim. The reason the sweep is allowed to block where the sensor is
  not: manifest rows are pre-certified contracts, not statistics needing a baseline — the
  mutation-pass caution ("a gate calibrated on one run is noise enforcing itself") governs
  statistical gates and does not apply here.
- **Build packets**: `deploy/assemble.py` refuses to assemble a build or fix packet that lacks
  gate-satisfying manifest coverage for its declared surfaces. The rule is declare-or-exempt,
  tier-restricted, and fail-closed — a descriptor with neither a `surfaces:` declaration nor a
  named `manifest_exempt` reason is malformed and refused; an exemption is honored only at T4,
  per the gate table in §7.

  **Worked example (the engine's own output shapes):**

  ```yaml
  text: "orders-web checkout: add promo-code field"
  task_type: build
  tier: T3
  surfaces: [orders-web]
  touched_layers: [interaction]
  required_views: []
  ```

  Invocation: `python deploy/assemble.py --descriptor <path> --root .`

  Open: `BEHAVIORAL-MANIFEST GATE -- orders-web @ T3 -- OPEN`
  Refused: `assemble.py: REFUSED (exit 1) -- behavioral-manifest gate: surface orders-web layer interaction status DRAFT -- required EXTRACTED|CERTIFIED|LIVE (tier T3)`
- **Projects without the knowledge-os capability**: the contract in this file still binds
  session practice — the naming firewall, the frontmatter schema, the row discipline, the
  amendment log, the INDEX, and the gate table all apply exactly as written. Only the
  automated sensors (`check-manifest.py`, the `assemble.py` refusal) are absent; a project in
  this state enforces the gate through session discipline and `/doctor`'s documentation
  checks rather than through engine code.
