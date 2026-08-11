#!/usr/bin/env bash
# init.sh — Rheoscope instantiation script (Unix / bash).
#
# Reads project.yaml in this directory, substitutes *.template files, wires
# enabled capabilities into runtime locations, deletes the capabilities/ catalog,
# and stamps instantiated metadata.
#
# Flags:
#   --dry-run   Non-destructive. Writes substituted output to dry-run-output/,
#               leaves *.template sources in place, does NOT wire capabilities,
#               does NOT delete capabilities/, does NOT stamp project.yaml.
#   --whatif    Parse-only check; exits 0 after argument parsing. Used for the
#               Phase 0 exit-criterion syntax check (bash -n init.sh).
#   --hooks     Wire security hooks into .claude/settings.local.json without
#               prompting (see §6b).
#   --no-hooks  Skip wiring security hooks without prompting (see §6b).
#
# Hard dependencies: yq (mike farah's go version) per Decision V2-11,
#                    perl (in macOS/Linux core), base64.
#
# Authored per the v2 build plan §Phase 0 (off-tree authoring artifact, not committed;
# the committed record of that phase is BUILD-LOG.md Phase 0).

set -euo pipefail

DRY_RUN=0
WHAT_IF=0
HOOKS_FLAG=0
NO_HOOKS_FLAG=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --whatif|--what-if) WHAT_IF=1 ;;
        --hooks) HOOKS_FLAG=1 ;;
        --no-hooks) NO_HOOKS_FLAG=1 ;;
        -h|--help)
            cat <<EOF
Usage: init.sh [--dry-run] [--whatif] [--hooks] [--no-hooks]

  --dry-run   Substitute templates to dry-run-output/ without modifying source.
  --whatif    Parse-only check; exits 0.
  --hooks     Wire security hooks into .claude/settings.local.json without prompting.
  --no-hooks  Skip wiring security hooks without prompting.

See INIT.md for the post-init kickoff protocol.
EOF
            exit 0
            ;;
        *)
            echo "ERROR: unknown argument: $arg" >&2
            exit 1
            ;;
    esac
done

if [[ "$HOOKS_FLAG" -eq 1 && "$NO_HOOKS_FLAG" -eq 1 ]]; then
    echo "ERROR: cannot combine --hooks and --no-hooks" >&2
    exit 1
fi

if [[ "$WHAT_IF" -eq 1 ]]; then
    echo "init.sh: --whatif set; parse-only check passed. No actions performed."
    exit 0
fi

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_YAML="$SCRIPT_ROOT/project.yaml"

fail() { echo "ERROR: $1" >&2; exit 1; }
info() { echo "$1"; }

# ---------- 1. Pre-flight ----------

[[ -f "$PROJECT_YAML" ]] || fail "project.yaml not found at $PROJECT_YAML. Copy project.yaml.example to project.yaml and edit before running init."
command -v yq >/dev/null 2>&1 || fail "yq not installed. Install: 'brew install yq' (macOS) or your distro's equivalent."
# Pre-flight: require Mike Farah yq v4+ (Go). init.sh uses the 'eval' subcommand and v4+
# filters (e.g. select(. == "*")). The Python yq (kislyuk) and Mike Farah v3 lack 'eval'
# and would fail mid-run with confusing errors -- detect and reject early (v1.1-1).
printf 'probe: ok\n' | yq eval '.probe' - >/dev/null 2>&1 \
    || fail "init.sh requires Mike Farah yq v4+ (the Go 'yq' with the 'eval' subcommand). The detected yq does not support 'yq eval' -- likely the Python yq or Mike Farah v3. Install from https://github.com/mikefarah/yq ('brew install yq' on macOS)."
command -v perl >/dev/null 2>&1 || fail "perl not found. perl is required for template substitution."
command -v base64 >/dev/null 2>&1 || fail "base64 not found."
[[ -w "$SCRIPT_ROOT" ]] || fail "target directory not writable: $SCRIPT_ROOT"

# Windows MAX_PATH mitigation, parity with init.ps1 (stranger-test RUN 1, 2026-07-24,
# Finding 1): harmless on POSIX, where there is no path-length limit to work around.
# Local repo config only -- never --global/--system.
if command -v git >/dev/null 2>&1 && [[ -d "$SCRIPT_ROOT/.git" ]]; then
    git -C "$SCRIPT_ROOT" config core.longpaths true
    info "git config core.longpaths true (local to this repo only -- Windows long-path mitigation, parity with init.ps1)"
fi

# stranger-test RUN 2 (2026-07-24), Finding 1 added a pre-flight MAX_PATH guard to
# init.ps1 (fails loud before any file operation on a Windows host without long-path
# support, once the destination path is deep enough to collide with this repo's own
# deepest shipped fixture path). Not mirrored here: POSIX filesystems have no comparable
# path-length ceiling for init.sh's own file operations to run into.

# ---------- 2. Idempotency check ----------

if [[ "$DRY_RUN" -eq 0 ]]; then
    INST_DATE=$(yq eval '.instantiated_date // ""' "$PROJECT_YAML")
    if [[ -n "$INST_DATE" && "$INST_DATE" != "null" ]]; then
        fail "project.yaml shows this project is already instantiated (date: $INST_DATE). Init is not re-runnable."
    fi
fi

# ---------- 3. Schema validation (defensive re-check) ----------

for f in project_name project_slug project_description capabilities personnel tier_examples template_version; do
    v=$(yq eval ".$f // \"__MISSING__\"" "$PROJECT_YAML")
    [[ "$v" != "__MISSING__" ]] || fail "project.yaml missing required field: $f"
done

PROJECT_NAME=$(yq eval '.project_name' "$PROJECT_YAML")
PROJECT_SLUG=$(yq eval '.project_slug' "$PROJECT_YAML")
PROJECT_DESC=$(yq eval '.project_description' "$PROJECT_YAML")
TEMPLATE_VERSION=$(yq eval '.template_version' "$PROJECT_YAML")

[[ "$PROJECT_SLUG" =~ ^[a-z0-9][a-z0-9-]*$ ]] \
    || fail "project_slug must match ^[a-z0-9][a-z0-9-]*\$ (got: $PROJECT_SLUG)"

# Single-source version gate (v2.0 #10a): the expected version is read at runtime from
# the VERSION file at the repo root -- never hardcoded here. VERSION is the template's
# single version source; project.yaml.template_version is the instantiated project's.
# Init consumes VERSION at step 8 so a finished project carries exactly one.
VERSION_FILE="$SCRIPT_ROOT/VERSION"
if [[ ! -f "$VERSION_FILE" ]]; then
    # On an already-instantiated project VERSION is correctly absent (consumed at init).
    # Real mode never reaches here instantiated (the idempotency check exits first), so
    # this branch exists for post-init --dry-run: gate against the stamped version rather
    # than advising a restore that init-validate check 8 would then flag.
    INST_DATE_GATE=$(yq eval '.instantiated_date // ""' "$PROJECT_YAML")
    if [[ -n "$INST_DATE_GATE" && "$INST_DATE_GATE" != "null" ]]; then
        EXPECTED_VERSION="$TEMPLATE_VERSION"
    else
        fail "VERSION file not found at $VERSION_FILE. The harness template ships a one-line VERSION file (the template's single version source); restore it from the template you cloned."
    fi
else
    # First line only; strip a UTF-8 BOM (ReadAllText does this implicitly on the
    # Windows side -- mirror it) and all whitespace.
    EXPECTED_VERSION="$(head -n1 "$VERSION_FILE" | sed $'s/^\xef\xbb\xbf//' | tr -d '[:space:]')"
fi
[[ "$EXPECTED_VERSION" =~ ^[0-9]+(\.[0-9]+){1,2}$ ]] \
    || fail "VERSION file at $VERSION_FILE must contain a bare version on one line (e.g. 2.0). Got: '$EXPECTED_VERSION'. Check for stray characters or a UTF-8 BOM."
# YAML parsers coerce an unquoted 2.0 to a number, and the trailing zero is lost when
# stringified ("2") -- the comparison below would then fail with a baffling message.
# Require the quoted-string form the schema declares and the example ships. yq itself
# preserves the text "2.0", but the YAML tag is still a number -- reject on the tag so
# the same malformed input fails on both platforms (parity with init.ps1's type guard).
TV_TAG=$(yq eval '.template_version | tag' "$PROJECT_YAML")
[[ "$TV_TAG" == "!!str" ]] \
    || fail "template_version must be a quoted string in project.yaml (e.g. template_version: \"$EXPECTED_VERSION\"). YAML parsed it as a number, which loses trailing zeros (2.0 becomes 2)."
[[ "$TEMPLATE_VERSION" == "$EXPECTED_VERSION" ]] \
    || fail "template_version mismatch -- this harness is v$EXPECTED_VERSION but project.yaml declares '$TEMPLATE_VERSION'. project.yaml's template_version must match the harness you're instantiating from. If your project was created on an older harness, init is not a migration tool (it runs once); use the matching harness version or follow a documented migration."

VALID_CAPS=(knowledge-os stress-testing code-conventions)

# Reject unknown capability keys
while IFS= read -r k; do
    [[ -z "$k" ]] && continue
    found=0
    for v in "${VALID_CAPS[@]}"; do
        [[ "$k" == "$v" ]] && { found=1; break; }
    done
    [[ "$found" -eq 1 ]] || fail "capabilities.$k is not a valid capability key. Valid: ${VALID_CAPS[*]}. (handoffs is core; kickoff-orchestration and operate-sentinel are documentation-only — none of these is toggled.)"
done < <(yq eval '.capabilities | keys | .[]' "$PROJECT_YAML")

# Require all valid caps present. Read the raw value: a missing key yields "null" (fails the
# boolean check below). Do NOT use 'a // default' here -- yq's `//` treats a literal `false`
# as empty and would coerce every false-valued capability to the default, failing valid input
# (e.g. a capability set to false). This is a Unix-only failure mode (init.ps1 uses
# ContainsKey + [bool]); surfaced by the v1.1-2 init.sh end-to-end run.
for cap in "${VALID_CAPS[@]}"; do
    v=$(yq eval ".capabilities[\"$cap\"]" "$PROJECT_YAML")
    [[ "$v" == "true" || "$v" == "false" ]] || fail "capabilities.$cap is required (boolean)."
done

P_COUNT=$(yq eval '.personnel | length' "$PROJECT_YAML")
[[ "$P_COUNT" -ge 1 ]] || fail "personnel must contain at least one entry."

for i in $(seq 0 $((P_COUNT - 1))); do
    for f in tag name role domains_owned; do
        v=$(yq eval ".personnel[$i].$f // \"__MISSING__\"" "$PROJECT_YAML")
        [[ "$v" != "__MISSING__" ]] || fail "personnel[$i] missing required field: $f"
    done
done

# ---------- 4. Build substitution dictionary ----------

declare -A DICT

DICT["project_name"]="$PROJECT_NAME"
DICT["project_slug"]="$PROJECT_SLUG"
DICT["project_description"]="$PROJECT_DESC"
DICT["template_version"]="$TEMPLATE_VERSION"
DICT["instantiated_date"]="$(date +%Y-%m-%d)"
DICT["project_root_path"]="$SCRIPT_ROOT"

for t in T1 T2 T3 T4; do
    DICT["tier_examples.$t"]=$(yq eval ".tier_examples.$t" "$PROJECT_YAML")
done

# Personnel tags
TAGS=()
while IFS= read -r t; do TAGS+=("$t"); done < <(yq eval '.personnel[].tag' "$PROJECT_YAML")
P_TAGS_CSV=$(IFS=', '; echo "${TAGS[*]}")
DICT["personnel_tags_csv"]="$P_TAGS_CSV"

NEUTRAL=()
while IFS= read -r t; do [[ -n "$t" ]] && NEUTRAL+=("$t"); done < <(yq eval '.neutral_source_tags[]' "$PROJECT_YAML" 2>/dev/null)
if [[ ${#NEUTRAL[@]} -eq 0 ]]; then
    NEUTRAL=(session ref field system)
fi
ALL_TAGS=("${TAGS[@]}" "${NEUTRAL[@]}")
ALL_TAGS_CSV=$(IFS=', '; echo "${ALL_TAGS[*]}")
DICT["source_tags_csv"]="$ALL_TAGS_CSV"

# personnel_compass_block (multi-line)
COMPASS=""
for i in $(seq 0 $((P_COUNT - 1))); do
    pn=$(yq eval ".personnel[$i].name" "$PROJECT_YAML")
    pt=$(yq eval ".personnel[$i].tag" "$PROJECT_YAML")
    pr=$(yq eval ".personnel[$i].role" "$PROJECT_YAML")
    # Wildcard ('*' or '**') anywhere in the array maps to "all" (mirrors init.ps1
    # array-membership check); without this, ['*', 'systems'] would join to "*, systems"
    # and bypass the wildcard branch. yq filters elements; bash tests non-empty.
    has_wild=$(yq eval ".personnel[$i].domains_owned[] | select(. == \"*\" or . == \"**\")" "$PROJECT_YAML" | head -1)
    if [[ -n "$has_wild" ]]; then
        dom_display="all"
    else
        dom_display=$(yq eval ".personnel[$i].domains_owned | join(\", \")" "$PROJECT_YAML")
    fi
    line="- **$pn** (tag: $pt) - $pr. Domains: $dom_display."
    if [[ -z "$COMPASS" ]]; then
        COMPASS="$line"
    else
        COMPASS="$COMPASS"$'\n'"$line"
    fi
done
DICT["personnel_compass_block"]="$COMPASS"

# wiki_domains_table
KO_ENABLED=$(yq eval '.capabilities["knowledge-os"]' "$PROJECT_YAML")
WD_COUNT=$(yq eval '.wiki_domains | length' "$PROJECT_YAML")
if [[ "$KO_ENABLED" == "true" && "$WD_COUNT" -gt 0 ]]; then
    TABLE="| Domain | Scope | Description |"$'\n'"|---|---|---|"
    for i in $(seq 0 $((WD_COUNT - 1))); do
        nm=$(yq eval ".wiki_domains[$i].name" "$PROJECT_YAML")
        sc=$(yq eval ".wiki_domains[$i].default_scope" "$PROJECT_YAML")
        dsc=$(yq eval ".wiki_domains[$i].description" "$PROJECT_YAML")
        TABLE="$TABLE"$'\n'"| $nm | $sc | $dsc |"
    done
    DICT["wiki_domains_table"]="$TABLE"
else
    DICT["wiki_domains_table"]="(No domains declared yet - populate during INIT.md walkthrough.)"
fi

# governance_docs_list
GD_COUNT=$(yq eval '.governance_docs | length' "$PROJECT_YAML")
if [[ "$GD_COUNT" -gt 0 ]]; then
    GD_LIST=""
    for i in $(seq 0 $((GD_COUNT - 1))); do
        path=$(yq eval ".governance_docs[$i].path" "$PROJECT_YAML")
        desc=$(yq eval ".governance_docs[$i].description" "$PROJECT_YAML")
        line="- $path - $desc"
        if [[ -z "$GD_LIST" ]]; then
            GD_LIST="$line"
        else
            GD_LIST="$GD_LIST"$'\n'"$line"
        fi
    done
    DICT["governance_docs_list"]="$GD_LIST"
else
    DICT["governance_docs_list"]="(No governance docs declared yet.)"
fi

# enabled_capabilities_list + instantiated set
INSTANTIATED=()
EN_LIST=""
for cap in "${VALID_CAPS[@]}"; do
    v=$(yq eval ".capabilities[\"$cap\"]" "$PROJECT_YAML")
    if [[ "$v" == "true" ]]; then
        INSTANTIATED+=("$cap")
        line="- $cap"
        if [[ -z "$EN_LIST" ]]; then
            EN_LIST="$line"
        else
            EN_LIST="$EN_LIST"$'\n'"$line"
        fi
    fi
done
[[ -n "$EN_LIST" ]] || EN_LIST="(No capabilities enabled.)"
DICT["enabled_capabilities_list"]="$EN_LIST"

# ---------- 5. Substitute *.template files ----------

# Serialize dict to a temp file as base64-encoded values (one record per line):
#   key<TAB>base64(value)
DICT_FILE=$(mktemp -t projos-init-dict.XXXXXX)
trap 'rm -f "$DICT_FILE"' EXIT
for key in "${!DICT[@]}"; do
    enc=$(printf '%s' "${DICT[$key]}" | base64 | tr -d '\n')
    printf '%s\t%s\n' "$key" "$enc" >> "$DICT_FILE"
done

DRY_RUN_ROOT="$SCRIPT_ROOT/dry-run-output"

# Dry-run: clean any prior dry-run-output/ so stale files from a previous run don't
# mislead inspection (v1.1-18).
if [[ "$DRY_RUN" -eq 1 && -d "$DRY_RUN_ROOT" ]]; then
    rm -rf "$DRY_RUN_ROOT"
    info "[dry-run] cleaned previous dry-run-output/"
fi

substitute_one() {
    local src="$1" dst="$2"
    DICT_FILE="$DICT_FILE" SRC_FILE="$src" perl -e '
        use strict;
        use warnings;
        use MIME::Base64;
        my %h;
        open(my $fh, "<", $ENV{"DICT_FILE"}) or die "cannot read dict: $!";
        while (my $line = <$fh>) {
            chomp $line;
            my ($k, $v) = split(/\t/, $line, 2);
            $h{$k} = decode_base64($v);
        }
        close $fh;

        local $/;
        open(my $in, "<", $ENV{"SRC_FILE"}) or die "cannot read $ENV{SRC_FILE}: $!";
        my $c = <$in>;
        close $in;

        my @unk;
        $c =~ s/\{\{([a-zA-Z0-9_.\-]+)\}\}/exists $h{$1} ? $h{$1} : do { push @unk, $1; "" }/ge;

        if (@unk) {
            my %seen; my @uniq = grep { !$seen{$_}++ } @unk;
            print STDERR "ERROR: unknown {{variable}}(s) in $ENV{SRC_FILE}: " . join(", ", @uniq) . "\n";
            exit 1;
        }

        print $c;
    ' > "$dst"
}

while IFS= read -r -d '' tf; do
    case "$tf" in
        "$DRY_RUN_ROOT"/*) continue ;;
    esac

    rel="${tf#$SCRIPT_ROOT/}"
    target_rel="${rel%.template}"

    if [[ "$DRY_RUN" -eq 1 ]]; then
        out="$DRY_RUN_ROOT/$target_rel"
        mkdir -p "$(dirname "$out")"
        substitute_one "$tf" "$out"
        info "[dry-run] substituted: $target_rel"
    else
        out="$SCRIPT_ROOT/$target_rel"
        mkdir -p "$(dirname "$out")"
        substitute_one "$tf" "$out"
        rm -f "$tf"
        info "substituted: $target_rel"
    fi
done < <(find "$SCRIPT_ROOT" -type f -name '*.template' -print0)

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo ""
    echo "DRY RUN - substituted output written to dry-run-output/. Source .template files untouched. Capabilities not wired. project.yaml not stamped."
    exit 0
fi

# ---------- 6. Wire capabilities (real mode only) ----------

wire_copy() {
    local src="$1" dst="$2"
    if [[ ! -e "$src" ]]; then
        info "skip (source absent): ${src#$SCRIPT_ROOT/}"
        return
    fi
    mkdir -p "$(dirname "$dst")"
    if [[ -d "$src" ]]; then
        rm -rf "$dst"
        cp -R "$src" "$dst"
    else
        cp "$src" "$dst"
    fi
    info "wired: ${src#$SCRIPT_ROOT/} -> ${dst#$SCRIPT_ROOT/}"
}

for cap in "${VALID_CAPS[@]}"; do
    v=$(yq eval ".capabilities[\"$cap\"]" "$PROJECT_YAML")
    [[ "$v" == "true" ]] || continue

    case "$cap" in
        knowledge-os)
            wire_copy "$SCRIPT_ROOT/capabilities/knowledge-os/extracted/compile"     "$SCRIPT_ROOT/.claude/skills/compile"
            wire_copy "$SCRIPT_ROOT/capabilities/knowledge-os/extracted/audit"       "$SCRIPT_ROOT/.claude/skills/audit"
            wire_copy "$SCRIPT_ROOT/capabilities/knowledge-os/extracted/wiki-schema.md" "$SCRIPT_ROOT/docs/wiki-schema.md"
            wire_copy "$SCRIPT_ROOT/capabilities/knowledge-os/extracted/deploy"          "$SCRIPT_ROOT/deploy"
            wire_copy "$SCRIPT_ROOT/capabilities/knowledge-os/extracted/discover"        "$SCRIPT_ROOT/.claude/skills/discover"
            wire_copy "$SCRIPT_ROOT/capabilities/knowledge-os/extracted/engine"          "$SCRIPT_ROOT/docs/engine"

            # Empty-state artifacts (fixes day-1 misdetection, backlog W6-1): /flight-plan's
            # Step 0.6 knowledge-os detection reads wiki/HEALTH.md + wiki/REVIEW.md; without them
            # present, a freshly-wired project misdetects as build-only on its very first
            # /flight-plan run. docs/wiki-schema.md § 11 documents these files' empty states as
            # existing artifacts but nothing created them -- this closes that gap.
            #
            # deploy/project.py's skeleton CLI was evaluated for this and does NOT fit: run in a
            # scratch dir, it writes ECO-4 determinism-check skeletons under --out/projection/
            # (never the live wiki/ locations -- by design, per its own docstring), in a format
            # that doesn't match § 11's documented empty states. So these are generated inline
            # here, matching § 11 verbatim.
            mkdir -p "$SCRIPT_ROOT/wiki"

            if [[ ! -e "$SCRIPT_ROOT/wiki/HEALTH.md" ]]; then
                cat > "$SCRIPT_ROOT/wiki/HEALTH.md" <<'EOF'
# Wiki Health

Not yet generated. `/compile` overwrites this file entirely on its first run — see `docs/wiki-schema.md` § 9.
EOF
                info "created (empty state): wiki/HEALTH.md"
            fi

            if [[ ! -e "$SCRIPT_ROOT/wiki/REVIEW.md" ]]; then
                cat > "$SCRIPT_ROOT/wiki/REVIEW.md" <<'EOF'
# Wiki Review Queue
EOF
                info "created (empty state): wiki/REVIEW.md"
            fi

            if [[ ! -e "$SCRIPT_ROOT/wiki/INDEX.md" ]]; then
                cat > "$SCRIPT_ROOT/wiki/INDEX.md" <<EOF
# Wiki Index

**What lives here:** Top-level index across all wiki domains for $PROJECT_NAME. Points to each domain's own INDEX.md and summarizes its current state. Populated once wiki domains are declared and \`/compile\` has run.

## Known Gaps

- No wiki domains declared yet — populate \`project.yaml.wiki_domains\` during the INIT.md walkthrough (§ 2f) before the first \`/compile\` run.
EOF
                info "created (empty state): wiki/INDEX.md"
            fi

            if [[ ! -e "$SCRIPT_ROOT/SESSION-BRIEFING.md" ]]; then
                cat > "$SCRIPT_ROOT/SESSION-BRIEFING.md" <<'EOF'
# Session Briefing

**Last compiled:** (not yet compiled)
**Governing documents:** CLAUDE.md (governance), core/methodology/ (methodology kernel), this wiki's INDEX

## Architectural Context

(Not yet populated — run /compile.)

## Active Workstreams

(Not yet populated — run /compile.)

## Hold Points

(Not yet populated — run /compile.)

## Governance Reminders

(Not yet populated — run /compile.)

## Quick Reference

| Resource | Location | What it is |
|----------|----------|------------|
| CLAUDE.md | CLAUDE.md | Project governance |
| Wiki Schema | docs/wiki-schema.md | This file |
| REVIEW.md | wiki/REVIEW.md | Open issues and action items |
| HEALTH.md | wiki/HEALTH.md | Wiki coverage and staleness stats |
| Handoffs Index | handoffs/INDEX.md | Substrate-separation inquiries |
EOF
                info "created (empty state): SESSION-BRIEFING.md"
            fi
            ;;
        stress-testing)
            info "stress-testing: retired 2026-07-10 — /preflight ships as a core skill (superseded /grill); nothing to wire."
            ;;
        code-conventions)
            wire_copy "$SCRIPT_ROOT/capabilities/code-conventions/examples" "$SCRIPT_ROOT/methodology/code-conventions.examples"
            ;;
    esac

    # Per-capability deferred recipes
    deferred_dir="$SCRIPT_ROOT/capabilities/$cap/deferred"
    if [[ -d "$deferred_dir" ]]; then
        recipes_dir="$SCRIPT_ROOT/docs/recipes/$cap"
        mkdir -p "$recipes_dir"
        for r in "$deferred_dir"/*.RECIPE.md; do
            [[ -f "$r" ]] || continue
            cp "$r" "$recipes_dir/$(basename "$r")"
            info "wired recipe: docs/recipes/$cap/$(basename "$r")"
        done
    fi
done

# Part B: documentation-only capabilities, unconditional (not toggled).
# These ship their RECIPE.md + deferred recipes into every instantiated project
# regardless of capability toggles; the catalog source is deleted with
# capabilities/ at step 7. To add one, extend the list — no other wiring.
DOCS_ONLY_CAPS=(kickoff-orchestration operate-sentinel decorrelated-review)
for docs_cap in "${DOCS_ONLY_CAPS[@]}"; do
    docs_src="$SCRIPT_ROOT/capabilities/$docs_cap"
    [[ -d "$docs_src" ]] || continue
    docs_dst="$SCRIPT_ROOT/docs/recipes/$docs_cap"
    mkdir -p "$docs_dst"
    if [[ -f "$docs_src/RECIPE.md" ]]; then
        cp "$docs_src/RECIPE.md" "$docs_dst/RECIPE.md"
        info "wired (unconditional): docs/recipes/$docs_cap/RECIPE.md"
    fi
    if [[ -d "$docs_src/deferred" ]]; then
        for r in "$docs_src/deferred"/*.RECIPE.md; do
            [[ -f "$r" ]] || continue
            cp "$r" "$docs_dst/$(basename "$r")"
            info "wired (unconditional): docs/recipes/$docs_cap/$(basename "$r")"
        done
    fi
done

# Part C: core skills (unconditional — always wired regardless of capability toggles).
# Core skills live at core/skills/<name>/ (substituted in place by step 5). They are
# copied to .claude/skills/<name>/ and the core/skills/ source is then consumed, so the
# only post-init copy is the runtime one (installed-skills-as-source-of-truth, per ADR-4).
core_skills_dir="$SCRIPT_ROOT/core/skills"
if [[ -d "$core_skills_dir" ]]; then
    for skill_path in "$core_skills_dir"/*/; do
        [[ -d "$skill_path" ]] || continue
        skill_name="$(basename "$skill_path")"
        dst="$SCRIPT_ROOT/.claude/skills/$skill_name"
        mkdir -p "$(dirname "$dst")"
        rm -rf "$dst"
        cp -R "$core_skills_dir/$skill_name" "$dst"
        info "wired (core skill): core/skills/$skill_name -> .claude/skills/$skill_name"
    done
    rm -rf "$core_skills_dir"
    info "deleted: core/skills/ (consumed into .claude/skills/)"
fi

# Part D: core scaffold files (unconditional — references/ is core per TEMPLATE-README.md's
# file layout table, not capability-gated. INIT.md Step 2e says "Update references/README.md
# to catalog each entry," but nothing shipped or created that file, so a fresh instance had
# no references/ slot to update. Empty-state generated inline here, same convention as the
# knowledge-os wiki/ empty-state files above, but unconditional since references/ carries no
# capability gate.
REFERENCES_README="$SCRIPT_ROOT/references/README.md"
if [[ ! -e "$REFERENCES_README" ]]; then
    mkdir -p "$SCRIPT_ROOT/references"
    cat > "$REFERENCES_README" <<'EOF'
# References

**What lives here:** Source material for this project — existing research, papers, external documentation. For each entry: source URL (or path), date added, why it matters.

(No references catalogued yet — populate during the INIT.md walkthrough, Step 2e.)
EOF
    info "created (empty state): references/README.md"
fi

# deliverables/ scaffold (backlog v3.0-54a: declared artifact home for operator-facing
# synthesized outputs; same empty-state convention as references/ above).
DELIVERABLES_README="$SCRIPT_ROOT/deliverables/README.md"
if [[ ! -e "$DELIVERABLES_README" ]]; then
    mkdir -p "$SCRIPT_ROOT/deliverables"
    cat > "$DELIVERABLES_README" <<'EOF'
# Deliverables

**What lives here:** Operator-facing synthesized outputs — briefs, checklists, filled forms, runbooks, small committed binaries. Every deliverable names its sources (a "Derived from:" line, or a sibling `<name>.provenance.md` for binaries). Not knowledge intake: new knowledge goes through `raw/`. See `docs/wiki-schema.md` § Artifact homes.

(No deliverables yet.)
EOF
    info "created (empty state): deliverables/README.md"
fi

# template_source / template_release stamping (v3.0.12, backlog v3.0-59): give the
# instance the public template's address and its own patch level, so "check for updates"
# is self-serve (core/governance/check-template-updates.py). Append-if-absent — an
# operator-authored value is never overwritten.
TEMPLATE_SOURCE_DEFAULT="https://github.com/rysidor-stack/rheoscope"
RELEASE_TAG="$(cat "$SCRIPT_ROOT/RELEASE" 2>/dev/null | tr -d '[:space:]')"
[[ -z "$RELEASE_TAG" ]] && RELEASE_TAG="v$(cat "$SCRIPT_ROOT/VERSION" 2>/dev/null | tr -d '[:space:]')"
if ! grep -q '^template_source:[[:space:]]*"..*"' "$SCRIPT_ROOT/project.yaml"; then
    if grep -q '^template_source:' "$SCRIPT_ROOT/project.yaml"; then
        sed -i.bak "s|^template_source:.*|template_source: \"$TEMPLATE_SOURCE_DEFAULT\"|" "$SCRIPT_ROOT/project.yaml" && rm -f "$SCRIPT_ROOT/project.yaml.bak"
    else
        printf 'template_source: "%s"\n' "$TEMPLATE_SOURCE_DEFAULT" >> "$SCRIPT_ROOT/project.yaml"
    fi
    info "stamped: template_source = $TEMPLATE_SOURCE_DEFAULT"
fi
if ! grep -q '^template_release:[[:space:]]*"..*"' "$SCRIPT_ROOT/project.yaml"; then
    if grep -q '^template_release:' "$SCRIPT_ROOT/project.yaml"; then
        sed -i.bak "s|^template_release:.*|template_release: \"$RELEASE_TAG\"|" "$SCRIPT_ROOT/project.yaml" && rm -f "$SCRIPT_ROOT/project.yaml.bak"
    else
        printf 'template_release: "%s"\n' "$RELEASE_TAG" >> "$SCRIPT_ROOT/project.yaml"
    fi
    info "stamped: template_release = $RELEASE_TAG"
fi

# ---------- 6b. Wire security hooks (real mode only) ----------
# Fixes the silent-security gap (backlog v3.0-25): until this step, nothing wired the
# PreToolUse hooks (dangerous-bash / env-writes guards) into .claude/settings.local.json —
# a project could sit unprotected with no signal that anything was missing. core/security/
# survives init (init-validate.sh Check 6 requires it), so the wired file's
# $CLAUDE_PROJECT_DIR/core/security/hooks/*.sh references stay valid at runtime.
# Consent: --hooks / --no-hooks decide without asking. Absent both, an interactive
# terminal is prompted (default YES on empty input); a non-interactive terminal wires by
# default with a loud notice, because silently NOT wiring would recreate the exact defect
# this step exists to fix, and the copy is trivially reversible (delete the file, or
# re-run with --no-hooks next time).

HOOKS_SRC="$SCRIPT_ROOT/core/security/settings.local.json.example"
HOOKS_DST="$SCRIPT_ROOT/.claude/settings.local.json"

if [[ -e "$HOOKS_DST" ]]; then
    # Detect the specific defect from stranger-test RUN 2 (2026-07-24), Finding 4: a
    # pre-existing settings.local.json (e.g. carried into a scratch copy by a plain
    # file copy instead of a real `git clone`, which would correctly have excluded a
    # gitignored file) silently shadows the --hooks/--no-hooks consent decision below —
    # the operator can pass --hooks (or accept the interactive default Yes) and still
    # end up with no security hooks wired, with only the info line below as the tell.
    # Warn LOUDLY when the existing file doesn't already reference the hooks this step
    # would have wired; never modify the file automatically — consent-flow behavior
    # below is otherwise unchanged. (Provenance — the silent-shadowing defect and its
    # confirmation by stranger-test RUN 2, Finding 4 — lives in this comment; the
    # operator-facing warning below stays plain.)
    if ! grep -q 'block-dangerous-bash' "$HOOKS_DST" 2>/dev/null || ! grep -q 'block-env-writes' "$HOOKS_DST" 2>/dev/null; then
        echo "WARNING: your security protections were NOT switched on — a settings file already exists without them. If you didn't set that file up on purpose: delete .claude/settings.local.json and run init again, or ask a Claude session to merge the protections in (from core/security/settings.local.json.example). If it's yours on purpose, ignore this." >&2
    fi
    info "settings.local.json already exists — left untouched; hooks example at core/security/settings.local.json.example"
elif [[ ! -e "$HOOKS_SRC" ]]; then
    info "skip (source absent): core/security/settings.local.json.example"
else
    WIRE_HOOKS=0
    if [[ "$HOOKS_FLAG" -eq 1 ]]; then
        WIRE_HOOKS=1
    elif [[ "$NO_HOOKS_FLAG" -eq 1 ]]; then
        WIRE_HOOKS=0
    elif [[ -t 0 ]]; then
        read -r -p "Wire security hooks (PreToolUse guards for dangerous bash + .env writes) into .claude/settings.local.json? [Y/n] " reply
        case "$reply" in
            [nN]*) WIRE_HOOKS=0 ;;
            *) WIRE_HOOKS=1 ;;
        esac
    else
        WIRE_HOOKS=1
        echo "NOTICE: wired by default in non-interactive mode; remove .claude/settings.local.json or re-run with --no-hooks to opt out." >&2
    fi

    if [[ "$WIRE_HOOKS" -eq 1 ]]; then
        mkdir -p "$(dirname "$HOOKS_DST")"
        cp "$HOOKS_SRC" "$HOOKS_DST"
        info "wired: core/security/settings.local.json.example -> .claude/settings.local.json"
        # jq is a runtime dependency of the wired hooks (block-dangerous-bash.sh,
        # block-env-writes.sh both parse the PreToolUse JSON payload with it). This is a
        # non-fatal presence note, not an init dependency: init itself never invokes jq.
        if command -v jq >/dev/null 2>&1; then
            info "jq: found (hooks runtime dependency satisfied)"
        else
            info "NOTE: jq not found on PATH — the wired hooks require jq at runtime. Install jq before they will run correctly; this does not affect init."
        fi
        # Pre-commit secret scanner (v3.0.36, backlog v3.0-12): installed under the SAME
        # consent as the PreToolUse hooks -- one hooks decision, one perimeter. Copies into
        # the repo's own pre-commit slot; an EXISTING pre-commit hook is never overwritten
        # (warn instead -- composing with someone's hook is their call, not init's).
        SCANNER_SRC="$SCRIPT_ROOT/core/security/hooks/scan-staged-secrets.sh"
        PRECOMMIT_DST="$SCRIPT_ROOT/.git/hooks/pre-commit"
        if [[ ! -f "$SCANNER_SRC" ]]; then
            info "skip (source absent): core/security/hooks/scan-staged-secrets.sh"
        elif [[ ! -d "$SCRIPT_ROOT/.git" ]]; then
            echo "WARNING: commit scanning NOT installed — this folder is not a git repository yet. After 'git init', copy core/security/hooks/scan-staged-secrets.sh to .git/hooks/pre-commit (and make it executable) to turn it on." >&2
        elif [[ -e "$PRECOMMIT_DST" ]]; then
            echo "WARNING: commit scanning NOT installed — a pre-commit hook already exists at .git/hooks/pre-commit. To add secret scanning, chain core/security/hooks/scan-staged-secrets.sh from your existing hook, or replace it if it's not yours on purpose." >&2
        else
            mkdir -p "$(dirname "$PRECOMMIT_DST")"
            cp "$SCANNER_SRC" "$PRECOMMIT_DST"
            chmod +x "$PRECOMMIT_DST" 2>/dev/null || true
            info "installed: core/security/hooks/scan-staged-secrets.sh -> .git/hooks/pre-commit (every commit is scanned for secret-shaped content; operator bypass: git commit --no-verify)"
        fi
    else
        info "skipped: security hooks not wired (.claude/settings.local.json not created)"
    fi
fi

# ---------- 7. Delete capabilities/ ----------

if [[ -d "$SCRIPT_ROOT/capabilities" ]]; then
    rm -rf "$SCRIPT_ROOT/capabilities"
    info "deleted: capabilities/"
fi

# ---------- 8. Stamp metadata back to project.yaml ----------

yq eval -i ".instantiated_date = \"${DICT[instantiated_date]}\"" "$PROJECT_YAML"

# Rewrite instantiated_capabilities as an explicit YAML array.
# Build an inline flow-style array and assign in one operation.
inst_json="["
first=1
for c in "${INSTANTIATED[@]}"; do
    if [[ "$first" -eq 1 ]]; then
        inst_json="$inst_json\"$c\""
        first=0
    else
        inst_json="$inst_json,\"$c\""
    fi
done
inst_json="$inst_json]"
yq eval -i ".instantiated_capabilities = $inst_json" "$PROJECT_YAML"

# Consume VERSION: from here on, the project's single version source is
# project.yaml.template_version (validated against VERSION above). Leaving a second
# version-bearing file in the project invites exactly the drift class the
# single-source design exists to kill.
rm -f "$VERSION_FILE" \
    || fail "init completed but could not delete VERSION (file in use?). Delete it manually, then run init-validate.sh."
info "deleted: VERSION (consumed; project.yaml template_version is the project's version source)"

# Consume RELEASE the same way (backlog v3.0-76): it was read above to stamp
# project.yaml.template_release; leaving it makes a second version-bearing file that
# nothing reads again and no recipe updates -- it goes permanently stale post-adoption.
if [[ -f "$SCRIPT_ROOT/RELEASE" ]]; then
    rm -f "$SCRIPT_ROOT/RELEASE" \
        || fail "init completed but could not delete RELEASE (file in use?). Delete it manually, then run init-validate.sh."
    info "deleted: RELEASE (consumed; project.yaml template_release is the project's release source)"
fi

# ---------- 9. Print next-steps ----------

if [[ ${#INSTANTIATED[@]} -gt 0 ]]; then
    caps_list=$(IFS=', '; echo "${INSTANTIATED[*]}")
else
    caps_list="(none)"
fi

echo ""
echo "Harness instantiated for project: $PROJECT_NAME"
echo "Capabilities wired: $caps_list"
echo ""

# cross-vendor-verify READINESS CHECK: report the /cross-check skills' RUNTIME prerequisites, explicitly,
# so the operator knows their state at instantiation. init only PLACES files; the skills need
# node>=18 + the codex CLI (a ChatGPT/GPT subscription) at runtime. Presence-only and NEVER fatal —
# a missing prereq leaves the skills inert (they fail loud if invoked), it does not fail init.
if [[ -d "$SCRIPT_ROOT/.claude/skills/bridge" ]]; then
    if command -v node >/dev/null 2>&1; then node_msg="found ($(node --version 2>/dev/null))"; node_ok=1; else node_msg="NOT FOUND on PATH (need >=18)"; node_ok=0; fi
    if command -v codex >/dev/null 2>&1; then codex_msg="found ($(command -v codex))"; codex_ok=1; else codex_msg="NOT FOUND on PATH"; codex_ok=0; fi
    echo "cross-vendor-verify readiness check (runtime prerequisites for the /cross-check skills):"
    echo "  node:  $node_msg"
    echo "  codex: $codex_msg"
    if [[ "$node_ok" -eq 1 && "$codex_ok" -eq 1 ]]; then
        echo "  => prerequisites present. Ensure 'codex login' is done (a GPT subscription), then smoke-test:"
        echo "       node .claude/skills/bridge/verify-cli.js --help"
    else
        echo "  => the /cross-check + /cross-check-loop skills are INSTALLED but INERT until node>=18 AND the"
        echo "     codex CLI (a ChatGPT/GPT subscription, then 'codex login') are present. They fail loud if"
        echo "     invoked before then; nothing else in this project is affected."
    fi
    echo ""
fi

# /doctor: post-init sanity check. Degrades gracefully — the doctor.py file is being
# authored in a parallel work leg, so this invocation silently skips if it hasn't landed
# yet. A doctor failure NEVER fails init; it only surfaces issues to fix before first use.
DOCTOR_PY="$SCRIPT_ROOT/.claude/skills/doctor/doctor.py"
if [[ -f "$DOCTOR_PY" ]]; then
    PY_BIN=""
    if command -v python3 >/dev/null 2>&1; then
        PY_BIN="python3"
    elif command -v python >/dev/null 2>&1; then
        PY_BIN="python"
    fi
    if [[ -n "$PY_BIN" ]]; then
        echo "Running /doctor ($PY_BIN .claude/skills/doctor/doctor.py)..."
        # Pass the project root explicitly: init may be invoked from outside the project
        # root, and doctor.py defaults to cwd — --root pins it to the right directory.
        if ! "$PY_BIN" "$DOCTOR_PY" --root "$SCRIPT_ROOT"; then
            echo "doctor reported issues above — fix before your first working session."
        fi
        echo ""
    else
        echo "python not found — install it, then run: python .claude/skills/doctor/doctor.py (the /doctor skill)"
        echo ""
    fi
fi

echo "New to this harness? core/onboarding/TOUR.md is a staged walkthrough (WHY / WHAT /"
echo "FIRST-WEEK / WHEN-X-HAPPENS); core/onboarding/SYSTEM-MAP.html is an interactive map"
echo "(double-click it to open). Ask /orient in a Claude session for ad-hoc questions —"
echo "answers cite the installed docs."
echo ""

echo "Recommended next steps:"
echo "  git init"
echo "  git add -A"
echo "  git commit -m \"instantiated rheoscope-harness v$TEMPLATE_VERSION\""
echo ""
echo "Then open INIT.md and run through the manual kickoff interview in a fresh Claude session."

exit 0
