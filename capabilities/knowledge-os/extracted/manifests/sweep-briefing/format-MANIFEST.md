---
manifest: format
surface: sweep-briefing
version: "1.0"
status: CERTIFIED
source_artifacts:
  - path: .claude/skills/sweep/SKILL.md
    sha256: 8da84cfbccd1fe148df515947d4100477ac960456bc7d196f8d0d0f592ab9b66
  - path: manifests/sweep-briefing/source/exemplar-1.md
    sha256: 7bc6f7c180da8a08f9f6782393cb617e512af2f2f4d65893bf216da9568b392f
  - path: manifests/sweep-briefing/source/exemplar-2.md
    sha256: fb6f831dbc72f3acc52338b8f66d9a7e1b9170e2d3cffd9b4dbdb74406092e4a
  - path: manifests/sweep-briefing/source/defect-sections-present.md
    sha256: 06af054474ce7e80ecaef672d641bf927366522f36800ba7d106fb015b6e6df2
  - path: manifests/sweep-briefing/source/defect-sections-order.md
    sha256: 436ebd635e5fc91c9eadf5fc58058b585c181a0bea9d72c1b98331c8694a8197
  - path: manifests/sweep-briefing/source/defect-all-clear-one-sentence.md
    sha256: 4ef8305a94a2800c5f2a80488d6ad0e36bd72b8fef16a547e9e6e03b2ef3870c
  - path: manifests/sweep-briefing/source/defect-attention-numbered.md
    sha256: 7b958c410dd54581a18a08de8aa44dbec67757f59f151e683b6aa99dece65d0b
  - path: manifests/sweep-briefing/source/defect-attention-three-sentences.md
    sha256: 05b8d2e72d1663a786c46d4eb5446102da552782a0c5c7d5726ce4a77ef1c43a
  - path: manifests/sweep-briefing/source/defect-attention-no-inline-path.md
    sha256: e9126d951b04d5d00db69cc2ff3bc40c35fe8bb910168bf9288b919f2c5b603f
  - path: manifests/sweep-briefing/source/defect-attention-details-tail-form.md
    sha256: 040583e40fd7315d90c40811bf915d3b464b9190790c09db3601ee1247272164
  - path: manifests/sweep-briefing/source/defect-watching-dashed-list.md
    sha256: 4fe9b4b05f372ad107617ff219cc9ec25e7032039197ea0ac6f56b31d131d336
  - path: manifests/sweep-briefing/source/defect-no-raw-output-leakage.md
    sha256: 1f49e2077ca67f6c96da12bb3c7b5cadbf23f8da68fd273d88062e295a40362c
  - path: manifests/sweep-briefing/source/defect-unclassifiable-in-attention.md
    sha256: dfe582687f9ff00d952bb4c1ff297a8b37adea16c6b9fd50f5e257670439fa43
  - path: manifests/sweep-briefing/source/defect-no-placeholder-tokens.md
    sha256: aa1f69ec9d5f8d9edd205134a380e0087f6d71bdce591344f0fa7b5ebf2019e9
  - path: manifests/sweep-briefing/source/defect-no-script-names-in-prose.md
    sha256: d5b85d79be4e99f6e653f16519558efc5441f5a214036336d6f48d90b0944383
  - path: manifests/sweep-briefing/source/defect-no-preamble-before-all-clear.md
    sha256: 0ed5e7a2afe46cac182a46f1d9c4a50d345bff9623b656772cec22a0ddc55341
  - path: manifests/sweep-briefing/source/SEED-README.md
    sha256: db20511efb4eebbb239d74948c474d76a586bcd5aa7c9a7a54fb5a5896bd2856
extracted: 2026-07-23
toolchain: source-read
confidence: source-crosschecked
row_shape: table
declared_rows: 18
schema_extensions: [validator-output, independent-grader-review]
seed: manifests/sweep-briefing/source@v1
---

# sweep-briefing format manifest

Format-layer behavioral contract for the `/sweep` briefing (`SWEEP-BRIEFING.md` when
scheduled, or the briefing text a session prints interactively) — surface 1 of the v3.0-44
harness-surfaces dogfood (the session-D design brief (dev-repo record, not shipped) Part 1),
and the **certification + twin-build-pilot target**. The contract is `.claude/skills/sweep/
SKILL.md` section "The briefing" (and the unclassifiable-output rule immediately beneath it),
transcribed here as behavioral rows and pinned above by sha256. Unlike `decision-inbox` and
`compile-receipt` (this session's other two format-layer surfaces, both landing DRAFT-row/
EXTRACTED-file with no validator owed yet), this surface is the one required to reach
CERTIFIED in session D — it owns the new `deploy/check-briefing-format.py` validator and a
pinned fixture seed (`manifests/sweep-briefing/source/`, indexed in `SEED-README.md`) rather
than carrying `DRAFT`-flagged rows.

**Row partition (round-1 fold, `manifest-layers.yaml`'s `format` entry).** Rows split into
two classes, reported separately and never pooled into one coverage number:

- **VALIDATOR rows** (13, `kind: EXACT`) — mechanical assertions over an artifact instance,
  replayed 1:1 by `deploy/check-briefing-format.py`. Certification full-tier replays the
  pinned seed (both exemplars pass every row; each `defect-<rowid>.md` fails exactly its own
  row); smoke tier replays the named subset (`MANIFEST-INDEX.md`) against the live current
  artifact.
- **RUBRIC rows** (5) — judgment calls the validator cannot and does not check (plain-English/
  business-terms phrasing, whether the All-clear sentence actually names what ran, whether an
  attention item's three sentences map to their required roles, whether a Watching entry reads
  as planned-not-a-problem, whether the All-clear sentence defers finding-substance to the
  sections that own it). Each states its grading criteria in the row itself; a RUBRIC row
  is graded by a session other than the one that produced the artifact under grade
  (manifest-format.md §4), per-criterion reasoning in the receipt, never a bare scalar. RUBRIC
  rows are never counted as mechanical coverage.

**Evidence-modality extension.** `validator-output` (a `deploy/check-briefing-format.py` run
against a pinned or live artifact) and `independent-grader-review` (a RUBRIC grading session's
verdict) are new evidence modalities beyond the doctrine's five-value enum
(manifest-format.md §4: "a new modality is added only via `schema_extensions`, never used
bare") — declared above.

## Rows

| id | name | replay path | expected observable | variant | flags | evidence | kind |
|---|---|---|---|---|---|---|---|
| `sections-present` | The three section headings each appear exactly once | Run `python deploy/check-briefing-format.py --file <artifact>`; row `sections-present` in its report | The three canonical section headings — `**All clear:**`, `**Needs your attention:**`, `**Watching:**` — each appear in the artifact exactly once, verbatim (bold markdown, exact wording and colon) | — | - | validator-output | EXACT |
| `sections-order` | The three sections appear in the fixed order | Run `python deploy/check-briefing-format.py --file <artifact>`; row `sections-order` in its report | Among whichever of the three headings are present, their first-occurrence order in the document is All clear, then Needs your attention, then Watching — never reordered | — | - | validator-output | EXACT |
| `all-clear-one-sentence` | All-clear is exactly one sentence | Run `python deploy/check-briefing-format.py --file <artifact>`; row `all-clear-one-sentence` in its report | The All-clear section's body (the text following its heading, up to the next section) is exactly one sentence ending in terminal punctuation; counts are permitted inside it, a second sentence is not | — | - | validator-output | EXACT |
| `attention-numbered` | Needs-your-attention items are numbered sequentially | Run `python deploy/check-briefing-format.py --file <artifact>`; row `attention-numbered` in its report | Needs-your-attention items are introduced by a sequential arabic numeral marker (`1.`, `2.`, `3.`, ...) starting at 1 with no gaps, repeats, or reordering; an empty section (0 items) trivially satisfies this | — | - | validator-output | EXACT |
| `attention-three-sentences` | Each attention item's prose is two to four sentences | Run `python deploy/check-briefing-format.py --file <artifact>`; row `attention-three-sentences` in its report | Each Needs-your-attention item's prose (its numbered text, excluding any trailing `(details: ...)`-shaped tail) is two to four sentences (amended 2026-08-05, A2 — the old exactly-three rule forced padding on naturally-two-sentence items); the three required ROLES — what's wrong, what happens if ignored, what the fix is — are judged by RUBRIC row `attention-sentence-roles`, never by this count. Row id kept for fixture and smoke-set continuity | — | - | validator-output | EXACT |
| `attention-no-inline-path` | No attention item's sentence carries an inline path | Run `python deploy/check-briefing-format.py --file <artifact>`; row `attention-no-inline-path` in its report | No Needs-your-attention item's sentence prose (excluding a trailing tail) contains a file-path-shaped token — a backtick-quoted span, a slash-containing path, or a bare filename carrying a `.md`/`.yaml`/`.yml`/`.json`/`.jsonl`/`.log`/`.txt` extension | — | - | validator-output | EXACT |
| `attention-details-tail-form` | A path reference lives only in a well-formed details tail | Run `python deploy/check-briefing-format.py --file <artifact>`; row `attention-details-tail-form` in its report | When a Needs-your-attention item carries a trailing parenthetical, it is exactly one `(details: <reference>)` span at the very end of the item — the literal word `details:` followed by non-empty content; any other keyword, or an unparenthesized reference, is a violation | — | - | validator-output | EXACT |
| `watching-dashed-list` | Watching entries are a dashed list | Run `python deploy/check-briefing-format.py --file <artifact>`; row `watching-dashed-list` in its report | Every non-blank line of the Watching section's body is a dash-prefixed list item (`- ` at the start of the line, followed by content); Watching items are never numbered | — | - | validator-output | EXACT |
| `no-raw-output-leakage` | No raw sensor/tool output leaks into the briefing | Run `python deploy/check-briefing-format.py --file <artifact>`; row `no-raw-output-leakage` in its report | The briefing's prose (every trailing `(word: ...)`-shaped tail excluded) contains no raw sensor/tool-output signature — no bracketed level tag (`[PASS]`/`[FAIL]`/`[WARN]`/`[SKIP]`/`[NOTE]`/`[REFUSE]`), no Python traceback header, no shell-prompt line | — | - | validator-output | EXACT |
| `unclassifiable-in-attention` | Unclassifiable output surfaces only in Needs-your-attention | Run `python deploy/check-briefing-format.py --file <artifact>`; row `unclassifiable-in-attention` in its report | If the briefing's source material carried any output the skill-runner could not recognize or classify, that surfaces as a Needs-your-attention item; a literal unclassifiable/unrecognized-output marker phrase appearing in the All-clear or Watching section content instead is a violation — SKILL.md: "never drop it silently, and never guess at what it meant" | — | - | validator-output | EXACT |
| `no-placeholder-tokens` | No placeholder/skeleton filler ships in a real briefing | Run `python deploy/check-briefing-format.py --file <artifact>`; row `no-placeholder-tokens` in its report | The briefing contains no placeholder/skeleton filler — no literal `TODO`, `TBD`, `XXX`, an angle-bracket `<...>` placeholder, or `lorem ipsum` — SKILL.md: "a real briefing names real things, never placeholders like these" | — | - | validator-output | EXACT |
| `no-script-names-in-prose` | Script filenames never appear in readable prose | Run `python deploy/check-briefing-format.py --file <artifact>`; row `no-script-names-in-prose` in its report | The briefing's prose — every sentence outside a trailing `(details: ...)`-style tail — never names a `.py` script filename directly; a script filename is exactly the kind of check-name jargon SKILL.md excludes from the readable sentence, and belongs (if anywhere) only inside a details tail | — | - | validator-output | EXACT |
| `no-preamble-before-all-clear` | Nothing precedes the All-clear heading | Run `python deploy/check-briefing-format.py --file <artifact>`; row `no-preamble-before-all-clear` in its report | Nothing but whitespace may precede the `**All clear:**` heading — no title line, no preamble; the briefing starts at All clear, because downstream parsers section-parse this artifact from its top (amended 2026-08-08, A3: the illustrative parser this row used to name, the desk generator, was removed from the template; the contract itself is unchanged) | — | - | validator-output | EXACT |
| `plain-english-business-terms` | All-clear and attention prose read as plain English, in business terms | Independent-grader session (never the authoring session) reads the full briefing against this row's criteria; per-criterion reasoning in the grading receipt, never a bare scalar (manifest-format.md §4) | Every sentence in All-clear and Needs-your-attention describes findings the way a non-technical operator would hear them — impact and action in plain language — with no script/tool filenames or internal-schema vocabulary (e.g. "check-frontmatter.py", "frontmatter", "sha256", "derivation block") outside a details tail — plain check-CATEGORY names ("environment", "structural checks", "spec-file health", "report spot-check", "workspace hygiene", "the deadline register") are sanctioned vocabulary, per the skill's own skeleton (amended 2026-08-05, A2: the former sanctioned terms "structural sensors" / "manifest structure" / "conformance smoke" were themselves repo-jargon a stranger cannot parse; renamed in skill, skeleton, and seed alike); a reader who has never opened this repo's code understands what's wrong and what to do without asking what a term means. Additionally (A2, adopting 2026-07-23 grader CANDIDATE (a)): a sentence-group that conveys no finding-specific content — boilerplate that would fit any project on any day — is a FAIL even when jargon-free; vacuity does not pass. Grade PASS/FAIL per sentence-group (All-clear; each attention item), naming the specific jargon term, opaque phrase, or vacuous filler for any FAIL | — | - | independent-grader-review | RUBRIC |
| `all-clear-names-checks` | All-clear names what was actually checked, not just "fine" | Independent-grader session reads the artifact against this row's criteria; per-criterion reasoning in the grading receipt | The All-clear sentence names, in substance, which categories of check ran (e.g. environment, structural checks, spec-file health, report spot-check, deadline register) — not merely "everything is fine" with no content; a bare count with zero category content is a FAIL. Name the missing content for any FAIL | — | - | independent-grader-review | RUBRIC |
| `attention-sentence-roles` | Each attention item's three sentences map to their required roles | Independent-grader session reads the artifact against this row's criteria; per-criterion reasoning in the grading receipt | Independently of the sentence COUNT (validator row `attention-three-sentences`, a 2-4 range since A2), grade whether the item's sentences TOGETHER state: what's wrong in business terms, the consequence of ignoring it, and the fix plus whether the system can self-apply it next session with a yes — SKILL.md's "The briefing" text. One sentence may carry two roles (A2: role coverage, not positional mapping). An item missing any of the three roles is a FAIL for that item, named specifically | — | - | independent-grader-review | RUBRIC |
| `watching-tone-distinguishes-planned` | Watching entries read as declared/expected, not as live problems | Independent-grader session reads the artifact against this row's criteria; per-criterion reasoning in the grading receipt | Each Watching entry's wording lets the operator tell "planned work I don't need to act on" apart from "a problem" at a glance — SKILL.md: "This is how the operator learns the difference between planned work and a problem." An entry that reads as alarming, or is ambiguous about its own expectedness, is a FAIL for that entry, named specifically | — | - | independent-grader-review | RUBRIC |
| `all-clear-defers-findings` | All-clear names what was checked and the healthy scope only, never a finding's substance | Independent-grader session reads the artifact against this row's criteria; per-criterion reasoning in the grading receipt | The All-clear sentence names what was checked and the healthy scope only — an "all clean except the items below"-style deferral is fine — but it never RESTATES a finding's substance: counts of drift findings, days-remaining, or failure specifics belong to Needs-your-attention/Watching, not All-clear. Grade PASS/FAIL, naming any smuggled finding | — | - | independent-grader-review | RUBRIC |

## Amendments

- **2026-07-23 (pre-certification, first full-tier grading pass):** row
  `plain-english-business-terms` criterion amended — the independent grader FAILED both
  exemplars on it and traced the cause to a contradiction in the SOURCE contract itself:
  /sweep SKILL.md forbade "check names" while its own normative skeleton used check-CATEGORY
  names as All-clear content, making this row and `all-clear-names-checks` jointly
  unsatisfiable. Adjudication: category names are sanctioned plain-language vocabulary; the
  ban targets script/tool filenames and internal-schema terms. BOTH the skill text and this
  row were amended to say so (skill edit carries its own dated clarification note; its
  source_artifacts pin re-pinned to 898f965f...). The dogfood's first catch — recorded here
  per §8. Grader observations recorded as CANDIDATE amendments, not adopted: (a) row
  `plain-english-business-terms` has no informativeness floor (vacuity passes); (b) row
  `all-clear-names-checks` has no truthfulness/freshness cross-check; (c) row
  `attention-sentence-roles` rewards literal echoing of its own phrasing; (d) row
  `watching-tone-distinguishes-planned` can be satisfied by boilerplate suffix. Candidates
  await the twin-build pilot's evidence before any adoption decision.

- **A1** | date 2026-07-23 | rows: 2 new (`no-preamble-before-all-clear`, VALIDATOR;
  `all-clear-defers-findings`, RUBRIC) | prior: 16 rows (12 VALIDATOR + 4 RUBRIC) | new: 18
  rows (13 VALIDATOR + 5 RUBRIC) | provenance: twin-build pilot round 1, divergences: leg-B
  title line (leg-B's build printed a `# Sweep Briefing` title line ahead of the
  `**All clear:**` heading; leg-A did not — the source contract never said which was
  required, so the two builds were contract-indistinguishable until diffed); A/B divergent
  All-clear under partial failure (leg-A's All-clear sentence, run against a seed with open
  Needs-your-attention items, restated a finding's substance — a drift count — inside the
  All-clear sentence itself; leg-B's did not). Both adjudicated as missing rows per
  manifest-format.md §11 (not don't-cares) and amended in. Implementing
  `no-preamble-before-all-clear` required re-authoring the pinned `defect-sections-order.md`
  fixture (reordered to Watching-before-Needs-your-attention while keeping All-clear first,
  so it still violates exactly `sections-order` and not the new row too — single-defect
  isolation preserved); its `source_artifacts` pin above is re-hashed to
  `436ebd635e5fc91c9eadf5fc58058b585c181a0bea9d72c1b98331c8694a8197`. Status stays CERTIFIED
  — the amendment rides the certification per §8; the twin-build pilot receipt records the
  re-replay.

- **A2** | date 2026-08-05 | rows: 2 amended (`attention-three-sentences`, VALIDATOR — count
  becomes a 2-4 range; `plain-english-business-terms`, RUBRIC — three sanctioned category
  terms renamed to plain replacements + the 2026-07-23 CANDIDATE (a) informativeness floor
  ADOPTED), plus consequential wording in `all-clear-names-checks` (example categories) and
  `attention-sentence-roles` (role COVERAGE, not positional mapping) | rows: 18 unchanged
  (13 VALIDATOR + 5 RUBRIC) | provenance: the 2026-08-05 plain-language sweep
  (`audits/2026-08-05-plain-language-sweep.md` findings C8, C15): the exactly-three rule
  forced padding on naturally-two-sentence items, and three sanctioned vocabulary terms
  ("structural sensors", "manifest structure", "conformance smoke") were repo-jargon inside
  the very row that promises plain English. Both fixes land BEFORE this contract is promoted
  repo-universal by `core/governance/CLAUDE.md` § Reporting to the operator (v3.0.25) — fix
  the ruler, then measure with it. Implementing the range required re-authoring the pinned
  `defect-attention-three-sentences.md` fixture (its old defect item had two sentences, legal
  under the range; the new one has five — single-defect isolation preserved) and renaming the
  category vocabulary across the seed (every touched pin re-hashed above; validator
  self-test 17/17 including two new hermetic `--prose-scan` cases). The `--prose-scan` mode
  itself (any-layout leakage rows applied to SESSION-BRIEFING.md / DECISIONS-PENDING.md,
  wired as /sweep step 16) is validator capability, not a row change — row count unchanged.
  Status stays CERTIFIED per §8; certified_by unchanged.

- **A3** | date 2026-08-08 | rows: 1 amended (`no-preamble-before-all-clear`, VALIDATOR —
  rationale wording only: the illustrative downstream parser the row named, the desk
  generator, was removed from the template in the 2026-08-08 structural-audit remediation;
  the row now says "downstream parsers" without naming one) | rows: 18 unchanged
  (13 VALIDATOR + 5 RUBRIC) | provenance: operator decision 2026-08-08 — the desk surfaces
  (metrics recorder, desk generator, empire-desk rollup) were deleted entirely, never used
  on any instance. The contract observable is byte-unchanged; no fixture re-authored. The
  `/sweep` SKILL.md source pin is re-hashed above (its Scheduling recipe dropped the two
  desk write-steps — outside the pinned contract's "The briefing" section, but the pin
  covers the whole file). Status stays CERTIFIED per §8; certified_by unchanged.

