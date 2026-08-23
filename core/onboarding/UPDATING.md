# UPDATING.md — how a Rheoscope instance stays current

*Shipped with v3.0.12 (backlog v3.0-59). Plain-English first; the mechanics follow.*

## The one-sentence version

Ask any agent session **"check for updates to the harness"** — it runs the update check,
and if newer releases exist it builds the adoption worklist itself from the template's own
diffs. You approve; it adopts file-level; nothing is ever `git pull`ed.

## How the check works

`project.yaml` carries two facts stamped at init: `template_source` (the public template's
address) and `template_release` (the release tag this instance actually runs — distinct from
`template_version`, which tracks the major/minor contract and deliberately does not move on
patch adoption). The check:

```
py core/governance/check-template-updates.py --check
```

lists the source's release tags and compares. Exit 0 = up to date (or not yet configured —
pre-v3.0.12 instances backfill below). Exit 1 = updates available (a signal, not an error).
Exit 2 = inconclusive (offline / git unavailable) — never silently green. `/sweep` runs it
on cadence; any session can run it on demand.

## The adoption method (file-level, always)

**Never `git pull` the template into an instance** — it smears fresh template files over
your populated governance, project.yaml, and content. The method every adoption uses:

1. Clone `template_source` read-only into scratch; check out the target tag.
2. `git diff <your template_release> <target tag> --stat` in that clone is the worklist.
3. **New files:** copy verbatim into matching paths (skills under `core/skills/<name>/` land
   at `.claude/skills/<name>/`; knowledge-os engine files under
   `capabilities/knowledge-os/extracted/deploy/` land at `deploy/`).
4. **Changed plain files** (shipped without template placeholder markers): copy verbatim.
5. **Changed substituted files** (shipped as `.template` — your local copy carries real
   values where the template carries double-brace placeholder markers): apply the diff
   hunks to your local file, never copy. If a hunk doesn't apply cleanly, stop and
   report rather than improvise. (This file describes the markers in words rather than
   showing one so the placeholder validator never flags its own documentation —
   backlog v3.0-61.)
6. **Never copy `harness-backlog.md`** — your local entries live there (see the
   numbering note below).
7. Run the verifiers: any new sensor's `--self-test`, then doctor, then init-validate.
8. **Advance `template_release` to the target tag in the same commit as the adoption** —
   that's what keeps the next "check for updates" honest.

**Backfill for pre-v3.0.12 instances:** add the two lines to `project.yaml` by hand —
`template_source: "<the public template URL>"` and `template_release: "<the tag you last
adopted>"` — then the check works from the next run.

## Backlog numbering note (v3.0-58)

The shipped `harness-backlog.md` carries the template's entries and their numbers; your
instance logs its own. Both increment `v3.0-N` independently, so collisions are expected —
when one happens, **your local entry renumbers** to `v3.0-local-N` with a provenance line
(the template side never renumbers; shipped doctor FIX lines cite its numbers). New
instance-local entries use `v3.0-local-N` from the start.

## Trust surfaces are operator-signed, and adoption sessions never write them (v3.0-120, v3.0.46)

A short list of files decides what a session may do: the security hooks and their
allowlist, `allowed_signers`, `trust-surfaces.txt`, `deploy/safe-allowlist.yaml`, the
`deploy/evidence/operator-*.md` authorization artifacts, `deploy/rulings/**`, the verifier
and the HUMAN-GATE consumers (`deploy/trust.py`, `compile-driver.py`,
`compile-backends.py`, `audit-content.py`), and `.claude/settings*.json`. Since v3.0.46
that list is a named **class** (`core/security/hooks/trust-surfaces.txt`) with three
properties you should know when adopting a release:

1. **A session cannot write them through either tool lane.** The Edit/Write guard and the
   Bash/PowerShell guard both deny (not ask) any write-shaped command naming a class path.
   So when a MIGRATION recipe says "copy the new hook" or "copy `deploy/trust.py`", the
   session will be denied — **that copy is yours to run, in your own terminal**, from the
   template's bytes (the recipe names the file and the template path). The session reads,
   diffs, and proposes; it does not apply.
2. **Every honest consumer refuses a trust surface that is not committed-identical, and
   (under `trust_surface_signing: required`) not operator-signed.** "Committed" is checked
   against git, never claimed in prose. Which of the two authority modes your project runs is
   YOUR one-time choice (v3.0.49; ADR #11 condition 4 as amended 2026-08-22): under
   `visible` a trust-surface change you apply lands as an ordinary commit and shows up in the
   next sweep's table until you have read it; under `required` it lands as a commit made with
   `git commit -S` under your presence-requiring (FIDO `sk-`) SSH key — one physical key touch
   per trust-surface edit (software keys do not count: they are ignored by every verifier and
   `/doctor` fails the pin if one is listed). A project that never recorded a choice runs in
   migration-only `warn` with content retirement disabled, and `/doctor` says so.
3. **`/doctor` check 16 and `/sweep` step 17 show you every trust-surface change within one
   sweep** — last commit, author, signature, and whether the working tree still matches
   HEAD. A row you do not recognize is the finding.

The hardware-key setup (choose the key, set the repo-local signing config, commit the pin with
one touch, set `required`) is MIGRATION v3.0.45 → v3.0.46, marked **[your call]** — optional
hardening since v3.0.49. The one-time mode choice itself is MIGRATION v3.0.48 → v3.0.49 (also
**[your call]**); init asks it on every new instance. Retirement (ADR #11 Release 2) publishes
under `required` only by your signed tag and under `visible` only by your exact-digest promote
action — never by a flag, and never at all while no mode is recorded.

## The pre-commit scanner rides updates by hand (v3.0-112)

Adopting a newer template updates `core/security/hooks/scan-staged-secrets.sh` in your
tree, but git hooks are per-clone and untracked: the RUNNING copy at
`.git/hooks/pre-commit` stays whatever was installed the day it was installed. After any
adoption that touches the scanner, reinstall it — **before** you commit the adoption
(the ordering matters: the new scanner's own pattern table can trip the OLD installed
hook; reinstalled first, the commit passes):

```
cp core/security/hooks/scan-staged-secrets.sh .git/hooks/pre-commit
```

(Chained hooks: refresh your chained copy instead.) `/doctor`'s `precommit-scanner`
check verifies the installed hook is present and byte-current — a WARN there after an
update means this step was missed.
