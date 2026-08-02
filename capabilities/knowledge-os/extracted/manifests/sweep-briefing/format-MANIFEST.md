---
manifest: format
surface: sweep-briefing
version: "1.0"
status: CERTIFIED
source_artifacts:
  - path: .claude/skills/sweep/SKILL.md
    sha256: 898f965fe6067579f166c605e1f781879e2ffc96051cc6ec4501de3e5481945b
  - path: manifests/sweep-briefing/source/exemplar-1.md
    sha256: b3e551b93f699d5c682f3076d4b825827cada4e331b6e3f7f5053477b99fbddc
  - path: manifests/sweep-briefing/source/exemplar-2.md
    sha256: 5110d4fca671062ef407b4073eba211d50a5e3f24da2285381f99fddf485dea7
  - path: manifests/sweep-briefing/source/defect-sections-present.md
    sha256: 10e3c5f21e2f7fb4c029a0edfa6f2a4785c6ce345f523f1245fc3cb1e267d6b8
  - path: manifests/sweep-briefing/source/defect-sections-order.md
    sha256: 24922675706d690e3db0e6eccae5054f761b00e6ce14d156f26805365ab3b399
  - path: manifests/sweep-briefing/source/defect-all-clear-one-sentence.md
    sha256: f430d2dcfcdde1890734525d8b2bd942e59af60e81bf5aef4d48ae2cce4591e6
  - path: manifests/sweep-briefing/source/defect-attention-numbered.md
    sha256: 80373fe1ecb729def5cbd86eb359924cf7562b16f3cbc0c95ee257a0b70d7960
  - path: manifests/sweep-briefing/source/defect-attention-three-sentences.md
    sha256: 44832341f0a056d7efd1b8bacee57e5ab78877938c3c22bb430a7e24ed770ec5
  - path: manifests/sweep-briefing/source/defect-attention-no-inline-path.md
    sha256: 3ddfdb2de1a20e7dc303700b14c5c44dec2738307725584570655feeec4b17cf
  - path: manifests/sweep-briefing/source/defect-attention-details-tail-form.md
    sha256: 00258cca4510de8175021b0ad4044dc22bf52fb540db8181ba78d032e2d4baa2
  - path: manifests/sweep-briefing/source/defect-watching-dashed-list.md
    sha256: 9ef72f25266b33c85e07445e69400befa788538588c6c640dbf0164158f282d5
  - path: manifests/sweep-briefing/source/defect-no-raw-output-leakage.md
    sha256: 24e42c1f0cbef64ca94188fb911653084d5bfb1f12a8d578878e102bd0d07fdd
  - path: manifests/sweep-briefing/source/defect-unclassifiable-in-attention.md
    sha256: 64ee8867ff47604ccc9a5808a02ab33a9f3e9a5de7f94b9bb4b21e5e7581f0be
  - path: manifests/sweep-briefing/source/defect-no-placeholder-tokens.md
    sha256: a52ada7d1c833eb31c3f63b40a6f621588e564b701a7184984aef62dc8b52844
  - path: manifests/sweep-briefing/source/defect-no-script-names-in-prose.md
    sha256: 43d18bb1e4c7bf6503953362a21764c264ee745603df5877d4c3b3bd85b62bfb
  - path: manifests/sweep-briefing/source/defect-no-preamble-before-all-clear.md
    sha256: b1daccc461cf681f04424c0e56f138c8a0ccb506b7cb077e325ef212d4c86f47
  - path: manifests/sweep-briefing/source/SEED-README.md
    sha256: 6717d81a6aa5a0647ac50fc641ae7119adc7bde318d9a58cd072807c7a2941f3
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
harness-surfaces dogfood (`harness-v3.0/specs/session-d-design-brief-2026-07-23.md` Part 1),
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
| `attention-three-sentences` | Each attention item's prose is exactly three sentences | Run `python deploy/check-briefing-format.py --file <artifact>`; row `attention-three-sentences` in its report | Each Needs-your-attention item's prose (its numbered text, excluding any trailing `(details: ...)`-shaped tail) is exactly three sentences: what's wrong, what happens if ignored, and what the fix is | — | - | validator-output | EXACT |
| `attention-no-inline-path` | No attention item's sentence carries an inline path | Run `python deploy/check-briefing-format.py --file <artifact>`; row `attention-no-inline-path` in its report | No Needs-your-attention item's sentence prose (excluding a trailing tail) contains a file-path-shaped token — a backtick-quoted span, a slash-containing path, or a bare filename carrying a `.md`/`.yaml`/`.yml`/`.json`/`.jsonl`/`.log`/`.txt` extension | — | - | validator-output | EXACT |
| `attention-details-tail-form` | A path reference lives only in a well-formed details tail | Run `python deploy/check-briefing-format.py --file <artifact>`; row `attention-details-tail-form` in its report | When a Needs-your-attention item carries a trailing parenthetical, it is exactly one `(details: <reference>)` span at the very end of the item — the literal word `details:` followed by non-empty content; any other keyword, or an unparenthesized reference, is a violation | — | - | validator-output | EXACT |
| `watching-dashed-list` | Watching entries are a dashed list | Run `python deploy/check-briefing-format.py --file <artifact>`; row `watching-dashed-list` in its report | Every non-blank line of the Watching section's body is a dash-prefixed list item (`- ` at the start of the line, followed by content); Watching items are never numbered | — | - | validator-output | EXACT |
| `no-raw-output-leakage` | No raw sensor/tool output leaks into the briefing | Run `python deploy/check-briefing-format.py --file <artifact>`; row `no-raw-output-leakage` in its report | The briefing's prose (every trailing `(word: ...)`-shaped tail excluded) contains no raw sensor/tool-output signature — no bracketed level tag (`[PASS]`/`[FAIL]`/`[WARN]`/`[SKIP]`/`[NOTE]`/`[REFUSE]`), no Python traceback header, no shell-prompt line | — | - | validator-output | EXACT |
| `unclassifiable-in-attention` | Unclassifiable output surfaces only in Needs-your-attention | Run `python deploy/check-briefing-format.py --file <artifact>`; row `unclassifiable-in-attention` in its report | If the briefing's source material carried any output the skill-runner could not recognize or classify, that surfaces as a Needs-your-attention item; a literal unclassifiable/unrecognized-output marker phrase appearing in the All-clear or Watching section content instead is a violation — SKILL.md: "never drop it silently, and never guess at what it meant" | — | - | validator-output | EXACT |
| `no-placeholder-tokens` | No placeholder/skeleton filler ships in a real briefing | Run `python deploy/check-briefing-format.py --file <artifact>`; row `no-placeholder-tokens` in its report | The briefing contains no placeholder/skeleton filler — no literal `TODO`, `TBD`, `XXX`, an angle-bracket `<...>` placeholder, or `lorem ipsum` — SKILL.md: "a real briefing names real things, never placeholders like these" | — | - | validator-output | EXACT |
| `no-script-names-in-prose` | Script filenames never appear in readable prose | Run `python deploy/check-briefing-format.py --file <artifact>`; row `no-script-names-in-prose` in its report | The briefing's prose — every sentence outside a trailing `(details: ...)`-style tail — never names a `.py` script filename directly; a script filename is exactly the kind of check-name jargon SKILL.md excludes from the readable sentence, and belongs (if anywhere) only inside a details tail | — | - | validator-output | EXACT |
| `no-preamble-before-all-clear` | Nothing precedes the All-clear heading | Run `python deploy/check-briefing-format.py --file <artifact>`; row `no-preamble-before-all-clear` in its report | Nothing but whitespace may precede the `**All clear:**` heading — no title line, no preamble; the briefing starts at All clear, because downstream parsers (e.g. `deploy/gen-desk.py`) section-parse this artifact from its top | — | - | validator-output | EXACT |
| `plain-english-business-terms` | All-clear and attention prose read as plain English, in business terms | Independent-grader session (never the authoring session) reads the full briefing against this row's criteria; per-criterion reasoning in the grading receipt, never a bare scalar (manifest-format.md §4) | Every sentence in All-clear and Needs-your-attention describes findings the way a non-technical operator would hear them — impact and action in plain language — with no script/tool filenames or internal-schema vocabulary (e.g. "check-frontmatter.py", "frontmatter", "sha256", "derivation block") outside a details tail — plain check-CATEGORY names ("environment", "structural sensors", "manifest structure", "conformance smoke", "workspace hygiene", "the deadline register") are sanctioned vocabulary, per the skill's own skeleton; a reader who has never opened this repo's code understands what's wrong and what to do without asking what a term means. Grade PASS/FAIL per sentence-group (All-clear; each attention item), naming the specific jargon term or opaque phrase for any FAIL | — | - | independent-grader-review | RUBRIC |
| `all-clear-names-checks` | All-clear names what was actually checked, not just "fine" | Independent-grader session reads the artifact against this row's criteria; per-criterion reasoning in the grading receipt | The All-clear sentence names, in substance, which categories of check ran (e.g. environment, structural sensors, manifest structure, conformance smoke, deadline register) — not merely "everything is fine" with no content; a bare count with zero category content is a FAIL. Name the missing content for any FAIL | — | - | independent-grader-review | RUBRIC |
| `attention-sentence-roles` | Each attention item's three sentences map to their required roles | Independent-grader session reads the artifact against this row's criteria; per-criterion reasoning in the grading receipt | Independently of the three-sentence COUNT (validator row `attention-three-sentences`), grade whether sentence 1 states what's wrong in business terms, sentence 2 states the consequence of ignoring it, and sentence 3 states the fix and whether the system can self-apply it next session with a yes — SKILL.md's "The briefing" text verbatim. A three-sentence item whose sentences do not map to these roles is a FAIL for that item, named specifically | — | - | independent-grader-review | RUBRIC |
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
  `24922675706d690e3db0e6eccae5054f761b00e6ce14d156f26805365ab3b399`. Status stays CERTIFIED
  — the amendment rides the certification per §8; the twin-build pilot receipt records the
  re-replay.

