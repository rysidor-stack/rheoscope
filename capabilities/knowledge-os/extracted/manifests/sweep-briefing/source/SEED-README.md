# sweep-briefing format seed — pinned fixture bundle

Pinned seed for `manifests/sweep-briefing/format-MANIFEST.md` (manifest-format.md §9: "a
surface owns a versioned seed set, not a single fixture"). Every file below is pinned by
sha256 in the manifest's `source_artifacts`; the frozen copy here and that pin must agree, or
`check-manifest.py`'s sha256-pins check fails. `deploy/check-briefing-format.py --self-test`
replays the whole set hermetically: both exemplars must pass every VALIDATOR row, and each
`defect-<rowid>.md` must fail EXACTLY the row named in its filename and pass every other row
(single-defect isolation — this is load-bearing, not a nicety: it is what proves the
validator is actually sensitive to each row, one row at a time, rather than a single
broad-strokes check that happens to catch everything at once).

None of this content is real operational data — every briefing below is invented,
plausible-but-generic filler (a scheduled-run gap, a certificate-renewal countdown, a
mirror-parity skip) built only to exercise the format contract.

| file | purpose | sha256 |
|---|---|---|
| `exemplar-1.md` | A healthy run: all-clear only, an explicit empty "(none)" Needs-your-attention, two declared/expected Watching items. Must pass all 12 VALIDATOR rows. | `b3e551b93f699d5c682f3076d4b825827cada4e331b6e3f7f5053477b99fbddc` |
| `exemplar-2.md` | A run with two Needs-your-attention items (each numbered, exactly three sentences, a well-formed `(details: ...)` tail) plus two Watching items. Must pass all 12 VALIDATOR rows. | `5110d4fca671062ef407b4073eba211d50a5e3f24da2285381f99fddf485dea7` |
| `defect-sections-present.md` | Drops the `**Watching:**` heading and its content entirely. Violates `sections-present` only. | `10e3c5f21e2f7fb4c029a0edfa6f2a4785c6ce345f523f1245fc3cb1e267d6b8` |
| `defect-sections-order.md` | Reorders the three (still-present, still-well-formed) sections to All clear, Watching, Needs your attention — All-clear stays first (so `no-preamble-before-all-clear` stays green) while the other two are swapped. Violates `sections-order` only. | `24922675706d690e3db0e6eccae5054f761b00e6ce14d156f26805365ab3b399` |
| `defect-all-clear-one-sentence.md` | All-clear body carries two sentences instead of one. Violates `all-clear-one-sentence` only. | `f430d2dcfcdde1890734525d8b2bd942e59af60e81bf5aef4d48ae2cce4591e6` |
| `defect-attention-numbered.md` | The two attention items are both numbered `1.` (a repeat, not `1.`/`2.`). Violates `attention-numbered` only. | `80373fe1ecb729def5cbd86eb359924cf7562b16f3cbc0c95ee257a0b70d7960` |
| `defect-attention-three-sentences.md` | Item 1's first two sentences are merged into one (two sentences instead of three). Violates `attention-three-sentences` only. | `44832341f0a056d7efd1b8bacee57e5ab78877938c3c22bb430a7e24ed770ec5` |
| `defect-attention-no-inline-path.md` | Item 2's second sentence carries an inline path (`receipts/desk/latest`) in the sentence text itself, with no details tail at all. Violates `attention-no-inline-path` only. | `3ddfdb2de1a20e7dc303700b14c5c44dec2738307725584570655feeec4b17cf` |
| `defect-attention-details-tail-form.md` | Item 1's trailing parenthetical uses `(see: ...)` instead of `(details: ...)`. Violates `attention-details-tail-form` only. | `00258cca4510de8175021b0ad4044dc22bf52fb540db8181ba78d032e2d4baa2` |
| `defect-watching-dashed-list.md` | The first Watching item is numbered (`1.`) instead of dash-prefixed. Violates `watching-dashed-list` only. | `9ef72f25266b33c85e07445e69400befa788538588c6c640dbf0164158f282d5` |
| `defect-no-raw-output-leakage.md` | A Watching item carries a raw `[WARN]` sensor-output bracket tag. Violates `no-raw-output-leakage` only. | `24e42c1f0cbef64ca94188fb911653084d5bfb1f12a8d578878e102bd0d07fdd` |
| `defect-unclassifiable-in-attention.md` | A Watching item mentions output "we could not classify" — the unclassifiable-output marker appears outside Needs-your-attention. Violates `unclassifiable-in-attention` only. | `64ee8867ff47604ccc9a5808a02ab33a9f3e9a5de7f94b9bb4b21e5e7581f0be` |
| `defect-no-placeholder-tokens.md` | A Watching item contains a literal `TODO:` placeholder token. Violates `no-placeholder-tokens` only. | `a52ada7d1c833eb31c3f63b40a6f621588e564b701a7184984aef62dc8b52844` |
| `defect-no-script-names-in-prose.md` | The All-clear sentence names a script file (`check-manifest.py`) directly in prose (still one sentence). Violates `no-script-names-in-prose` only. | `43d18bb1e4c7bf6503953362a21764c264ee745603df5877d4c3b3bd85b62bfb` |
| `defect-no-preamble-before-all-clear.md` | A `# Sweep Briefing` title line precedes the `**All clear:**` heading; otherwise identical to `exemplar-1.md`. Violates `no-preamble-before-all-clear` only. | `b1daccc461cf681f04424c0e56f138c8a0ccb506b7cb077e325ef212d4c86f47` |

The contract source these fixtures were built against, also pinned in the manifest's
`source_artifacts`:

| file | sha256 |
|---|---|
| `.claude/skills/sweep/SKILL.md` (section "The briefing" and the unclassifiable-output rule beneath it) | `9f242d184f0c3c8c4e746c5fd5d50fd2649bee821cee6fe07af5ffcbbe4e3cc1` |

RUBRIC rows (`plain-english-business-terms`, `all-clear-names-checks`, `attention-sentence-
roles`, `watching-tone-distinguishes-planned`) are graded by an independent-grader session
against the criteria stated in each row directly — they have no fixture here and are never
replayed by `deploy/check-briefing-format.py` (see that script's own docstring).
