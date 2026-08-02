# decision-inbox — MANIFEST-INDEX

Surface 2 of the v3.0-44 harness-surfaces dogfood (session-d-design-brief-2026-07-23.md
Part 1). Per the round-1 vocabulary fold: this surface's **file** status is EXTRACTED
while every **row** carries the DRAFT flag — the enum and the lifecycle carrier are
distinct and never conflated (manifest-format.md §5 vs §7).

```yaml
surface: decision-inbox
updated: 2026-07-23
layers:
  format:
    file: manifests/decision-inbox/format-MANIFEST.md
    status: EXTRACTED
    rows: 12
    certified_by: null
gate:
  next_increment: "session D v3.0-44 -- decision-inbox format certification (surface 2, after the sweep-briefing pilot)"
  tier: T3
  touched_layers: [format]
  state: CLOSED
  date: 2026-07-23
```

## Notes

- **No validator owed at this stage.** Per the design brief, only surface 1
  (`sweep-briefing`) is required to reach CERTIFIED in session D; this surface's
  `deploy/check-briefing-format.py`-equivalent validator and pinned defective-fixture
  seed are future work, landing when this surface is scheduled for certification.
  Nothing here self-certifies.
- **`source_artifacts` pins the producer, not an instance.** `format-MANIFEST.md` pins
  `deploy/decision-inbox.py` by sha256 (`e4cd031329...360f78ec22`) — the stable contract.
  The live `DECISIONS-PENDING.md` at the fork root was read for cross-reference while
  authoring the rows but is never pinned itself: it is a regenerated projection, so
  pinning an instance would go stale on the next `/sweep`/`/standing-loop` run.
- **Concurrent-edit hazard, discovered mid-extraction — read this before trusting the
  pin.** `deploy/decision-inbox.py` carried an uncommitted, in-progress change while
  this manifest was being authored: another session was landing v3.0-39 item 2 ("Inbox
  ages" — an age-tail render backed by `receipts/desk/inbox-first-seen.json`) live, on
  the same design brief this build was scoped from. The sha256 pin above was
  re-computed twice during authoring as the file changed underneath this extraction;
  the rows now describe the age-tracking feature as observed once its self-tests
  reached case (25)+ (first generation, regeneration, retirement, revival, corrupt-
  sidecar tolerance — a complete, well-tested feature, not a half-finished stub). **If
  that file has changed again since this manifest was written, re-run
  `python deploy/check-manifest.py`** — a `sha256 mismatch` FAIL there is expected and
  means this manifest is due a same-commit amendment (manifest-format.md §8), not a
  defect in the manifest as authored.
- **Section-order and candidate-first rows track live doctrine.** `di-section-order`
  and `di-candidate-section-first` transcribe `decision-inbox.py`'s `_SECTIONS` tuple
  and its own comment citing `harness-v3.0/specs/r1-build-decisions-2026-07-22.md`
  Part 7 verbatim — the candidate section is placed first by design, not incidentally.
- **`di-age-tail-and-identity` and `di-sidecar-write-read-split` replace the OPEN row
  this manifest would otherwise have carried** for the not-yet-built age feature named
  in the design brief — by the time of authoring, the feature already existed in the
  producer, so the honest EXTRACTED contract states it directly rather than flagging
  it as missing. No OPEN markers remain in this manifest (0, per `check-manifest.py`'s
  open-markers count).
