**All clear:** 5 of 7 checks ran clean — environment, structural sensors, manifest structure, conformance smoke, and workspace hygiene all healthy.

**Needs your attention:**
1. The overnight scheduled run did not finish last night. Until someone reruns it, tonight's metrics history will show a one-day gap, though nothing else in the project is affected. Restarting the scheduled task fixes this — the system can retry it automatically next session with a yes.
   (details: `.claude/sweep-schedule.log`)
2. The deadline register shows a certificate renewal window closing soon. Ignoring it risks an expired certificate interrupting the nightly build via receipts/desk/latest silently. Renewing the certificate now clears it — this step needs a person, the system cannot renew it itself.

**Watching:**
- Three behavioral-manifest rows are red because a feature change is deliberately in progress on the ordering surface — expected, not a problem.
- The mirror-parity check has no template path configured for this project yet, so it is skipped — expected until that gets set up.
