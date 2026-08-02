# Init-Script Audit — Phase 7 Amendment 3

*Targeted audit of `init.ps1`, `init.sh`, `init-validate.ps1`, and `init-validate.sh` covering the four categories surfaced in `verifier-reviews/phase-5-review.md` Amendment 3. Triggered by the cluster of three Phase 0 hotfixes — `7c3016a` (UTF-8 encoding), `14e744c` (Windows backslash normalization), `012e50a` (deployment_config stub values) — each surfaced as a latent bug exposed by a later phase's substitution test.*

---

## Audit categories

Per phase-5-review.md §Amendment 3:

- **(a)** Conditional dict-building paths under non-default `project.yaml` settings.
- **(b)** Path-handling edge cases (backslash, encoding, spaces).
- **(c)** Deployment-config conditional blocks with all valid `deployment_target` values (currently `none` and `mkdocs-vps`).
- **(d)** Encoding handling (UTF-8 vs ANSI on PowerShell 5.1 paths not yet exercised).

---

## Methodology

Eight test permutations were authored as scratch `project.yaml` files at harness root and exercised against `init.ps1 -DryRun`. For each permutation: zero unresolved mustache placeholders in `dry-run-output/` (mechanical grep), JSON validity of substituted `dry-run-output/core/security/settings.local.json.example` (via `jq .`), plus a targeted spot-check on the feature under test. `init.sh` was smoke-tested on permutation A (failed cleanly at the pre-flight `yq` check; Windows machine has no `yq` installed — see audit category (d) caveat below). The init scripts were also read end-to-end for static analysis.

Test inputs at the time of audit: scratch `project.yaml` files only (not tracked in git). All scratch artifacts and `dry-run-output/` directories were removed between permutations and after the audit completed.

### Permutations exercised

| # | Shape | Purpose |
|---|---|---|
| A | `project.yaml.example` verbatim — mostly-true caps, `deployment_target: none`, single-personnel example | Default smoke. Re-validates the steady-state used by Phase 5 dry-run. |
| B | All 5 caps `true`; `deployment_target: mkdocs-vps`; full `deployment_config`; two-personnel; populated `wiki_domains`; populated `governance_docs` | Conditional dict path: mkdocs-vps branch; positive case for every computed list. |
| C | All 5 caps `false`; `deployment_target: none`; empty `wiki_domains`/`governance_docs` arrays; single-personnel | Empty-state stubs across the board. The most-stripped instantiation. |
| D | Caps default; personnel with multi-byte UTF-8 in `name` and `role` (em-dash, accented Latin: ç/ü/é/ó/ñ, Japanese kanji 山田 太郎) | Re-confirms UTF-8 encoding fix `7c3016a` against personnel data it did not originally exercise. |
| E (folded into B) | Populated `wiki_domains` + `knowledge-os: true` | Positive case for `wiki_domains_table`. Covered by B. |
| F | Populated `wiki_domains` + `knowledge-os: false` | Edge: PS line 201 / sh line 194 require *both* `knowledge-os` AND non-empty domains for the real table; expect fallback stub. |
| G | `domains_owned: ['*', 'systems']` (wildcard + specific) plus `['**']` and `[systems, vendors]` siblings | Cross-platform parity for the wildcard branch. PowerShell uses array-membership (`-contains`); bash joined-string-equality (pre-fix). |
| H | `wiki_domains` and `governance_docs` keys entirely absent from `project.yaml` (not just empty arrays) | Behavior under missing optional keys vs `[]`. |

---

## Findings

### Finding 1 — Cross-platform `domains_owned` wildcard divergence between init.ps1 and init.sh.  **STATUS: LATENT BUG, FIXED.**

**Where (pre-fix):** `init.sh` lines 176–181.

```bash
dom_joined=$(yq eval ".personnel[$i].domains_owned | join(\", \")" "$PROJECT_YAML")
if [[ "$dom_joined" == "*" || "$dom_joined" == "**" ]]; then
    dom_display="all"
else
    dom_display="$dom_joined"
fi
```

**Where (PowerShell, correct):** `init.ps1` lines 188–194.

```powershell
$domainsList = @($p.domains_owned | ForEach-Object { [string]$_ })
$domains = if ($domainsList -contains '*' -or $domainsList -contains '**') {
    'all'
} else {
    ($domainsList -join ', ')
}
```

**Root cause:** PowerShell tests array membership before joining; bash joins first, then string-compares the joined value against the exact tokens `"*"` and `"**"`. For any `domains_owned` array containing a wildcard alongside specific names — e.g., `['*', 'systems']` — bash produces the joined string `"*, systems"` which matches neither pattern, falling through to render the literal joined string in `personnel_compass_block`. PowerShell on the same input emits `"all"`.

**Impact:** Operators on Unix substrates using `['*', 'systems']`-style entries (a documented pattern — see `project.yaml.example` line 32 comment: "Use `['*']` for solo projects (all domains)") would get a divergent `PROJECT-COMPASS.md` "Domains:" line compared to the same `project.yaml` instantiated on Windows. Single-wildcard `['*']` works on both substrates because the join produces `"*"` which matches the bash check; the divergence is specific to wildcard-plus-specific arrays.

**Empirical evidence:**
- PowerShell side (init.ps1) — permutation G dry-run output, `PROJECT-COMPASS.md` lines 26–28:
  ```
  - **Alice** (tag: alice) - Principal. Domains: all.        # ['*', 'systems'] -> all
  - **Bob** (tag: bob) - Co-lead. Domains: all.              # ['**'] -> all
  - **Carol** (tag: carol) - Specialist. Domains: systems, vendors.   # [systems, vendors] -> joined
  ```
- Bash side (init.sh): not empirically runnable on this Windows machine (`yq` not installed — see audit category (d) caveat); divergence confirmed by static code reading.

**Fix applied:** `init.sh` lines 173–183 rewritten to filter array elements via `yq eval … | select(. == "*" or . == "**")` and test the result for non-empty before joining. Mirrors `init.ps1` array-membership semantics. Net change: +3 comment lines, replaces `dom_joined` derivation with a wildcard-test then conditional join; same number of executable lines either way.

**Validation:** by-inspection only on this Windows machine. The fix preserves behavior for all input shapes that already work:
- `['*']` (single wildcard) → yq filter emits `*` → non-empty → "all". ✓
- `['**']` (double wildcard) → yq filter emits `**` → non-empty → "all". ✓
- `[systems, vendors]` (no wildcard) → yq filter emits nothing → empty → re-join → "systems, vendors". ✓
- `[]` (empty) → yq filter emits nothing → empty → join emits "" → "" (same as pre-fix behavior; not a meaningful state since schema requires `domains_owned` present, but empty array passes validation).
- `['*', 'systems']` (the fix's target) → yq filter emits `*` → non-empty → "all". ✓ (previously: "*, systems".)

First-Unix-instantiation empirical validation is required for full confidence. Flagged in `v1.1-backlog.md` (Phase 8) if any deviation surfaces during dogfood.

**Why a Phase 7 hotfix (separate commit before the Phase 7 commit):** follows the established `7c3016a` / `14e744c` / `012e50a` precedent for latent init-script bugs caught between phases. The Amendment 3 verifier recommendation explicitly named the cluster; Phase 7 closes the cluster.

---

### Finding 2 — Conditional dict-building paths under non-default project.yaml settings.  **STATUS: NO BUG.**

**Audit category (a).**

Six conditional branches in the substitution dictionary construction were exercised:

| Branch | init.ps1 line | init.sh line | Permutations covering | Result |
|---|---:|---:|---|---|
| `deployment_target == mkdocs-vps` (deployment_config dict path) | 161 | 144 | B (positive), A/C/D/F/G/H (negative) | Both branches produce the documented behavior. The `<not-configured>` stub introduced by `012e50a` resolves to inert template content under non-mkdocs targets. |
| `knowledge-os AND wiki_domains.Count > 0` (wiki_domains_table) | 201 | 194 | B (true/non-empty), F (false/non-empty), H (true/absent), C (true/empty) | Table renders only when both conditions hold; otherwise the documented `(No domains declared yet ...)` stub. |
| `governance_docs.Count > 0` (governance_docs_list) | 213 | 209 | B (populated), C (empty), H (absent) | Renders list when non-empty; stub `(No governance docs declared yet.)` otherwise. Absent-key and empty-array branches both resolve to stub. |
| `enabled.Count > 0` (enabled_capabilities_list) | 226 | 241 | A/B/D (some enabled), C (none enabled) | Renders bulleted list when any cap enabled; stub `(No capabilities enabled.)` when all five false. |
| `neutral_source_tags.Count > 0` (source_tags_csv fallback) | 179 | 162 | All permutations have it populated; static-read shows default `[session, ref, field, system]` fires when key absent or empty. | No mismatch surfaced; defaults symmetric across PS and bash. |
| `personnel_compass_block` per-entry domains_owned wildcard | 189 | 177 | G | **Diverged** — see Finding 1. |

The first five branches behave identically across PowerShell and bash. The sixth is Finding 1.

**Static-analysis observation (informational, not a bug):** Both init scripts treat absent optional keys (`wiki_domains`, `governance_docs`, `neutral_source_tags`) and empty arrays identically, which is the intended behavior per the empty-state stub design. Permutation H confirms this for `wiki_domains` and `governance_docs`; `neutral_source_tags` was not exercised with absent key but the PS conditional (`$py.neutral_source_tags -and $py.neutral_source_tags.Count -gt 0`) and bash (`yq … // empty`) both short-circuit cleanly on absence.

---

### Finding 3 — Path-handling edge cases (backslash, encoding, spaces).  **STATUS: NO BUG (hotfixes hold).**

**Audit category (b).**

- **Windows backslash → forward-slash for `project_root_path`** (init.ps1 line 154): re-verified by permutation A producing JSON-valid `settings.local.json.example` containing `Read(C:/Users/Foresight Sports/Documents/Claude/Project OS Harness/harness/**)`. `jq .` parses cleanly under every permutation; `14e744c` hotfix holds.
- **Spaces in `$scriptRoot`/`$SCRIPT_ROOT`**: the harness build path itself contains a space (`Foresight Sports`). Every permutation's substitution produces correct paths in `settings.local.json.example`; no quoting failures observed.
- **`base64` + `perl` byte-clean pipeline (init.sh)**: static-read only on this Windows machine. Phase 0 BUILD-LOG hotfix `7c3016a` audit flagged this as "yq is UTF-8 native; base64 + perl pipeline is byte-clean. Not tested on this Windows machine — flagged for Unix-side verification at next Linux/macOS run." Carried forward unchanged. The fix in Finding 1 does not affect the base64/perl substitution pipeline; it only modifies the dict-building stage before serialization.

---

### Finding 4 — Deployment-config conditional blocks with all valid deployment_target values.  **STATUS: NO BUG (hotfixes hold).**

**Audit category (c).**

The two currently-valid values are `none` and `mkdocs-vps` (per `project.yaml.example` line 61 comment).

- **`deployment_target: mkdocs-vps`** (permutation B): `deployment_config.vps_host` and `deployment_config.vps_path` substitute to the supplied values (`5.78.188.130`, `/opt/all-caps`). Substituted output in `dry-run-output/capabilities/deployment/extracted/mkdocs-vps/mkdocs-vps.md` and `post-receive-hook.sh` is well-formed.
- **`deployment_target: none`** (permutations A, C, D, F, G, H): `deployment_config.vps_host` and `deployment_config.vps_path` substitute to `<not-configured>` per `012e50a`. The substituted files exist under `dry-run-output/capabilities/deployment/extracted/mkdocs-vps/` but would not be wired to runtime locations because the migration step's condition (`init.ps1` line 309, `init.sh` line 353) requires `deployment_target == 'mkdocs-vps'`. Stubs are inert.

Other `deployment_target` values mentioned in the project.yaml.example comment (`vercel`, `netlify`, `static-html`, `custom`) are not implemented in v1.0 per Decision V2-16. They are explicitly out of scope for this audit and surfaced as "not implemented" entries in `capabilities/deployment/RECIPE.md` Field 9.

---

### Finding 5 — Encoding handling (UTF-8 vs ANSI on PowerShell 5.1).  **STATUS: NO BUG (hotfix holds; new path exercised).**

**Audit category (d).**

Permutation D pushed multi-byte UTF-8 into personnel `name` and `role` fields — paths not exercised by the original `7c3016a` test fixture (which used an em-dash only in `role`). All survived intact through the substitution pipeline:

- Latin-script accented characters (ç, ü, é, ó, ñ) — preserved.
- Japanese kanji (山田 太郎) — preserved.
- Em-dashes (U+2014) compounded in `role` — preserved.

Spot-check output from permutation D, `dry-run-output/core/governance/PROJECT-COMPASS.md`:

```
- **Aliçe Müller** (tag: alice) - Principal — systems architect — primary decision-maker. Domains: all.
- **山田 太郎** (tag: hiro) - Operations — east-region oversight. Domains: systems.
- **José Núñez** (tag: jose) - Co-lead — vendor relations. Domains: vendors, operations.
```

`init-validate.ps1` line 57 (the symmetric `Get-Content … -Raw -Encoding UTF8` call from `7c3016a`) remains in place and is not exercised at dry-run time, but its behavior is identical to `init.ps1` line 75. No regression.

**Caveat (Unix substrate, not exercised on this Windows machine):** `init.sh` and `init-validate.sh` rely on `yq` (Mike Farah's Go version) for YAML reads, which is UTF-8 native. The byte-clean `base64 + perl` substitution pipeline is documented in Phase 0 BUILD-LOG and remains untested on this Windows machine. yq is not installed; init.sh dies cleanly at the pre-flight check (`ERROR: yq not installed`) without doing anything destructive. The Unix-side smoke test is deferred to first Unix instantiation per the Phase 0 hotfix `7c3016a` audit note. No change in deferral status; no new evidence either way.

---

## Init-validate scripts (init-validate.ps1, init-validate.sh)

Audited by static read. Neither validator processes `domains_owned`; the divergence in Finding 1 is isolated to the init scripts. Both validators:

- Use UTF-8 explicit encoding on YAML reads (init-validate.ps1:57 per `7c3016a`; init-validate.sh uses `yq` natively).
- Check for unresolved mustache placeholders post-instantiation.
- Check for leftover `*.template` files.
- Check capability runtime paths under `.claude/skills/`, `methodology/`, `deployment/`, `docs/wiki-schema.md`.

No findings beyond what was already documented in earlier phases.

---

## Out-of-scope observations (flagged for `v1.1-backlog.md` at Phase 8)

Per audit scope discipline — these were noticed during the audit but are not in the four named categories.

1. **init.sh: `yq`-syntax dependency in Finding 1 fix.** The fix uses `yq eval ".personnel[$i].domains_owned[] | select(. == \"*\" or . == \"**\")"`. The `[]` flattening and `select(...)` filter are Mike Farah yq v4+ syntax. If operators install an older yq, the filter would error. Phase 0 BUILD-LOG documents yq as a hard dependency but does not pin a version. Add to v1.1-backlog: pin yq version in INIT.md/TEMPLATE-README troubleshooting section, or have init.sh detect and reject incompatible yq versions at pre-flight.

2. **No empirical validation of init.sh on Windows.** The Finding 1 fix is by-inspection only. If the Phase 8 dogfood project runs on Mac/Linux, this becomes the first real bash invocation; if it runs on Windows-only, the fix remains validated only by code reading until v1.1 dogfood on a Unix substrate.

3. **`template_version` substring match.** init.ps1 line 104 and init.sh line 89 both do exact-string equality on `"1.0"`. Future v1.1 instantiation against a v1.0-stamped project.yaml would error cleanly, but the error message ("template_version must be '1.0'") is wrong from a v1.1 operator's perspective. v1.1 work should handle the version-mismatch UX.

4. **TEMPLATE-README jq runtime dependency missing** (carried forward from Phase 4 BUILD-LOG): TEMPLATE-README doesn't list jq alongside powershell-yaml/yq as a per-OS runtime dependency. Phase 4 hooks require it. Not a Phase 7 fix item per scope.

---

## Audit summary

| Category | Result |
|---|---|
| (a) Conditional dict-building | One latent bug (Finding 1, cross-platform divergence). FIXED. |
| (b) Path-handling | No bug. Existing hotfixes hold. |
| (c) Deployment-config conditionals | No bug. `012e50a` hotfix holds. |
| (d) Encoding | No bug. `7c3016a` hotfix holds; new path exercised on personnel fields. |

**Files modified by this audit:**
- `init.sh` lines 176–181 → 173–183 (Finding 1 fix; separate hotfix commit before the Phase 7 commit).

**No files modified:**
- `init.ps1` (no PowerShell-side findings).
- `init-validate.ps1`, `init-validate.sh` (validators have no domains_owned handling; no new findings).

**No regressions:** PowerShell side untouched. Bash side change by inspection mirrors PS logic 1:1 for all input shapes including the previously-working ones.

**Cluster closed:** The three-hotfix pattern (`7c3016a` / `14e744c` / `012e50a`) plus this audit's Finding 1 forms a tight set of init-script issues that surfaced one phase late each time. No additional latent issues found within the four audit categories.

---

*Audit version: 1*
*Authored: 2026-05-22 during Phase 7 by Claude Code session*
*Triggered by: `verifier-reviews/phase-5-review.md` §Amendment 3*
*Sign-off: Audit complete. Phase 7 may proceed to documentation pass (Scope A).*
