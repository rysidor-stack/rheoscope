#!/usr/bin/env bash
# init-validate.sh — Post-substitution & post-wiring validator (Unix).
#
# Runs AFTER init.sh. Verifies the instantiated harness is clean:
#   - No {{...}} placeholders remain anywhere (Decision V2-2: no allow-list).
#   - No *.template files remain.
#   - project.yaml.instantiated_date is populated.
#   - capabilities/ has been removed from the target.
#   - Each enabled capability's runtime files exist at expected locations.
#
# Exits 0 on PASS, 1 on FAIL.

set -uo pipefail

WHAT_IF=0
for arg in "$@"; do
    case "$arg" in
        --whatif|--what-if) WHAT_IF=1 ;;
        -h|--help)
            echo "Usage: init-validate.sh [--whatif]"
            exit 0
            ;;
        *)
            echo "ERROR: unknown argument: $arg" >&2
            exit 1
            ;;
    esac
done

if [[ "$WHAT_IF" -eq 1 ]]; then
    echo "init-validate.sh: --whatif set; parse-only check passed."
    exit 0
fi

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FAILURES=()
add_fail() { FAILURES+=("$1"); }

# Load placeholder-scan exempt list (META docs that legitimately contain literal {{...}}).
# Config-driven via project.yaml.placeholder_scan_exempt (v1.1: was hardcoded).
EXEMPT=()
if [[ -f "$SCRIPT_ROOT/project.yaml" ]] && command -v yq >/dev/null 2>&1; then
    while IFS= read -r e; do
        [[ -n "$e" && "$e" != "null" ]] && EXEMPT+=("$e")
    done < <(yq eval '.placeholder_scan_exempt[]' "$SCRIPT_ROOT/project.yaml" 2>/dev/null)
fi
is_scan_exempt() {
    local rp="$1" e
    [[ ${#EXEMPT[@]} -eq 0 ]] && return 1
    for e in "${EXEMPT[@]}"; do
        [[ "$rp" == "$e" || "$rp" == "$e"* ]] && return 0
    done
    return 1
}

# Dependency / generated-dir skip (v3.0-14): single-sourced from project.yaml.dependency_scan_skip;
# matched on any path SEGMENT, CASE-SENSITIVELY (these dir names appear at any depth), unlike the
# prefix-matched exempt list. The PowerShell validator matches identically (-ccontains, list-only).
# Prevents false {{...}} failures from vendored/generated dirs. (.git skipped below.)
DEP_SKIP=()
if [[ -f "$SCRIPT_ROOT/project.yaml" ]] && command -v yq >/dev/null 2>&1; then
    while IFS= read -r d; do
        [[ -n "$d" && "$d" != "null" ]] && DEP_SKIP+=("$d")
    done < <(yq eval '.dependency_scan_skip[]' "$SCRIPT_ROOT/project.yaml" 2>/dev/null)
fi
is_dep_skipped() {
    local rp="$1" seg d
    [[ ${#DEP_SKIP[@]} -eq 0 ]] && return 1
    local IFS='/'
    for seg in $rp; do
        for d in "${DEP_SKIP[@]}"; do
            [[ "$seg" == "$d" ]] && return 0
        done
    done
    return 1
}

# 1. No remaining {{...}} placeholders (skipping .git/ and configured exemptions)
while IFS= read -r -d '' f; do
    rel="${f#$SCRIPT_ROOT/}"
    is_scan_exempt "$rel" && continue
    is_dep_skipped "$rel" && continue
    while IFS= read -r line; do
        # grep -oIE: -I treats binary files as non-matching (v3.0-61: a tracked PNG's
        # "Binary file ... matches" line was reported as a placeholder)
        if [[ -n "$line" ]]; then
            add_fail "Unresolved placeholder $line in $f"
        fi
    done < <(grep -oIE '\{\{[a-zA-Z0-9_.-]+\}\}' "$f" 2>/dev/null)
done < <(find "$SCRIPT_ROOT" -type f -not -path "$SCRIPT_ROOT/.git/*" -not -path "$SCRIPT_ROOT/.claude/worktrees/*" -print0)
# .claude/worktrees/ excluded (v3.0-61): tool-managed scratch worktrees are git-excluded
# debris the scanner has no business walking — 92 false failures on a live instance.

# 2. No *.template files remain
while IFS= read -r -d '' tf; do
    add_fail "Leftover .template file: $tf"
done < <(find "$SCRIPT_ROOT" -type f -name '*.template' -print0)

# 3. project.yaml stamped
PROJECT_YAML="$SCRIPT_ROOT/project.yaml"
if [[ ! -f "$PROJECT_YAML" ]]; then
    add_fail "project.yaml not found at $PROJECT_YAML"
else
    if ! command -v yq >/dev/null 2>&1; then
        add_fail "yq required but not installed"
    else
        INST_DATE=$(yq eval '.instantiated_date // ""' "$PROJECT_YAML")
        if [[ -z "$INST_DATE" || "$INST_DATE" == "null" ]]; then
            add_fail "project.yaml.instantiated_date is empty - init did not stamp metadata"
        fi

        # 5. Per-capability runtime checks
        check_cap_paths() {
            local cap="$1"; shift
            local enabled
            enabled=$(yq eval ".capabilities[\"$cap\"] // false" "$PROJECT_YAML")
            [[ "$enabled" == "true" ]] || return 0
            for p in "$@"; do
                [[ -e "$SCRIPT_ROOT/$p" ]] || add_fail "Capability $cap is enabled but runtime path missing: $p"
            done
        }

        check_cap_paths "knowledge-os" ".claude/skills/compile" ".claude/skills/audit" "docs/wiki-schema.md" "deploy/check-frontmatter.py" "deploy/check-derivation.py" ".claude/skills/discover" "docs/engine/memory-engine-v3-spec.md" "docs/engine/OPERATIONS.md" "deploy/compile-v2.py" "deploy/staleness.py" "deploy/register-intake.py" "deploy/entities.yaml" "deploy/origin-config.yaml" "deploy/check-manifest.py" "deploy/manifest-layers.yaml" "wiki/HEALTH.md" "wiki/REVIEW.md" "wiki/INDEX.md" "SESSION-BRIEFING.md"
        # stress-testing: retired 2026-07-10 -- /preflight ships as a core skill
        # (superseded /grill). The key is still accepted (schema parity with existing
        # project.yaml files) but wires nothing, so no runtime paths are required.
        check_cap_paths "stress-testing"
        check_cap_paths "code-conventions" "methodology/code-conventions.examples"
    fi
fi

# 4. capabilities/ must not exist
if [[ -d "$SCRIPT_ROOT/capabilities" ]]; then
    add_fail "capabilities/ directory still exists - init did not delete it"
fi

# 6. Required directories exist
for d in core/methodology core/governance core/handoffs core/security/hooks core/onboarding; do
    [[ -d "$SCRIPT_ROOT/$d" ]] || add_fail "Required directory missing: $d"
done

# 7. Core skills MUST exist post-init (always wired, independent of capability toggles).
#    core/skills/ is consumed at wiring time, so it must NOT remain either.
for s in flight-plan handoff-author handoff-receive handoff-close log-backlog cross-check cross-check-loop preflight doctor orient conformance sweep standing-loop; do
    [[ -f "$SCRIPT_ROOT/.claude/skills/$s/SKILL.md" ]] || add_fail "Core skill missing: .claude/skills/$s/SKILL.md"
done
# bridge is the transport library the cross-check skills call into -- it has no SKILL.md
# of its own (it's not invoked directly as a skill), so it's checked by its entry script.
[[ -f "$SCRIPT_ROOT/.claude/skills/bridge/verify-cli.js" ]] || add_fail "Core skill missing: .claude/skills/bridge/verify-cli.js"
[[ -d "$SCRIPT_ROOT/core/skills" ]] && add_fail "core/skills/ still exists - init did not consume it into .claude/skills/"

# 8. VERSION must NOT exist post-init (consumed at instantiation; the project's single
#    version source is project.yaml.template_version, per v2.0 #10a).
[[ -f "$SCRIPT_ROOT/VERSION" ]] && add_fail "VERSION file still exists - init did not consume it (project.yaml.template_version is the project's version source)"

if [[ ${#FAILURES[@]} -gt 0 ]]; then
    echo "init-validate: FAIL (${#FAILURES[@]} issue(s))"
    for f in "${FAILURES[@]}"; do
        echo "  - $f"
    done
    exit 1
else
    echo "init-validate: PASS"
    exit 0
fi
