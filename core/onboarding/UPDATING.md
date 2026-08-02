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
