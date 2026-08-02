# Recipe: /recompile (deferred)

## 1. WHAT IT IS

A wiki article re-compilation orchestrator. When the raw files informing an article change significantly, /recompile re-runs the synthesis for that article. **T1 destructive** — overwrites article content.

## 2. WHEN A PROJECT NEEDS IT

A wiki article's source raw files have changed substantially (operator corrected facts; new raw files added on the same topic). The article needs re-synthesis rather than incremental editing.

## 3. WHEN A PROJECT DOESN'T

Article changes are minor (incremental edits via /compile suffice). Article correction is via explicit operator instruction, not synthesis.

## 4. STATUS

deferred. **T1 destructive — gates must be enforced before authoring.**

## 5. PROVENANCE

Referenced in the source project's CLAUDE.md as a future orchestrator; never authored because no article in the source project has needed it. Design captured in v1 Phase 5 `/recompile` section (lines 1758–1819).

## 6. DEPENDENCIES

- knowledge-os.compile (must exist; /recompile re-runs /compile's synthesis path)
- `receipts/` for backup persistence

## 7. AUTHORING GUIDE

**Gate requirements (must be in SKILL.md Step 0):**

- /recompile NEVER runs without explicit operator instruction. No periodic or auto-triggered recompilation.
- Pre-check: target article exists, has citations to raw files, has been edited by /compile at least once. Refuse if not.
- **Pre-write backup:** dump current article content to `receipts/YYYY-MM-DDTHHMMSS-recompile-backup-<article-slug>.md`. Operator can roll back if synthesis is worse.

**Process:**
1. Operator names the article to recompile.
2. Load all raw files cited as sources for that article.
3. Re-run the synthesis logic from /compile against those raw files.
4. Compare new synthesis to existing article. Surface diff to operator.
5. Operator approves or rejects. If approved, write new content. If rejected, keep existing.
6. Either way, write receipt with diff.

**Anti-patterns to avoid:**
- Don't auto-approve. /recompile is T1; every run gates on explicit operator confirmation.
- Don't lose the prior version. Backup before write is non-negotiable.
- Don't run on articles that haven't been compiled before — those should be initial /compile, not /recompile.

## 8. KNOWN LESSONS

None — capability has not been built.

## 9. OPEN QUESTIONS

- Multi-article recompilation: should /recompile support `--all-affected` or only single-article?
- What's the right way to surface a synthesis diff to the operator (line-by-line, semantic diff, or "here's the new article, you decide")?

## 10. MIGRATION STEPS

(Empty — not yet built.)
