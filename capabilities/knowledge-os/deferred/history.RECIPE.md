# Recipe: /history (deferred)

## 1. WHAT IT IS

A provenance walker. Operator asks "why did we end up here?" /history walks receipts, changelog, raw files, ADRs, and synthesizes an answer citing specific files and timestamps.

## 2. WHEN A PROJECT NEEDS IT

Project has run long enough (3+ months) that the trail of past decisions is genuinely hard to reconstruct from memory. Operator wants on-demand diagnostic without manually grepping receipts.

## 3. WHEN A PROJECT DOESN'T

Project is recent (operator remembers context). Receipts directory is small. Manual `grep` over receipts suffices.

## 4. STATUS

deferred.

## 5. PROVENANCE

Designed during v1 build plan authoring (v1 Phase 5 `/history` section, lines 1677–1758 of v1 plan). Read-only walker; on-demand diagnostic, not periodic.

## 6. DEPENDENCIES

- knowledge-os (raw files, receipts, changelog must exist)
- `docs/adr/` directory

## 7. AUTHORING GUIDE

**Inputs:** a specific operator question. Examples:
- "When did we lock the decision on X?"
- "Why does wiki/Y/Z.md say W?"
- "What raw files contributed to article A?"

**Process:**
1. Read all receipts in date order.
2. Read changelog.md.
3. Read ADRs.
4. Read raw files mentioned in any of the above.
5. Walk the citation graph: which raw files informed which wiki articles; which wiki articles informed which roadmap decisions.
6. Synthesize an answer citing specific files and timestamps.

**Anti-patterns to avoid:**
- Don't speculate. If the trail doesn't answer the question, say so explicitly. "I cannot find a receipt or ADR resolving this" is better than confabulating a plausible-sounding reconstruction.
- Don't summarize broadly. Cite specific files and lines.

**Critical operational concern: receipts directory growth.** Per verifier review §5, receipts grow unbounded — year 1 ≈ ~3,000 files; reading all of them blows context windows. The eventual /history must include or coordinate with a compactor that archives receipts older than 90 days into `receipts/archive/YYYY-MM/` digest files. Two options:
- (a) /history reads `receipts/archive/` digests for old context and `receipts/` directly for recent.
- (b) A separate `/compact-receipts` orchestrator runs the compaction; /history just reads whatever exists.

Option (b) is cleaner — single-responsibility orchestrators.

## 8. KNOWN LESSONS

None — capability has not been built.

## 9. OPEN QUESTIONS

- Compaction granularity: per-month archive or per-quarter?
- Should /history be allowed to read full raw files, or only their summaries?
- How to handle when raw files have been edited (corrections) — present-state or historical state at the time of a downstream decision?

## 10. MIGRATION STEPS

(Empty — not yet built.)
