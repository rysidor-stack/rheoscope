#!/usr/bin/env bash
# Extracted and generalized from a production project.
# Modified per Decision V2-14: allow .env.example and .env.sample edits.
# Extended v3.0.36 (backlog v3.0-98(a)): the security perimeter's own files are
# write-guarded -- a session could otherwise append one permissive regex to
# egress-allowlist.txt (or widen a hook's exemption) and quietly loosen the
# perimeter it runs under. Operator edits happen outside sessions, the same
# doctrine as credential-bindings.yaml.
# Extended v3.0.46 (backlog v3.0-120, brief section 2): the guard now covers the whole
# TRUST-SURFACE CLASS -- every path that decides what a session may do -- read from
# trust-surfaces.txt beside this script (fixed relative path, no env override) in
# UNION with the hard-coded floor below, so an absent/emptied file never narrows the
# class (fail-closed). Honest limit, unchanged in kind: this mediates the Edit/Write
# tool lane; the Bash/PowerShell lane has its own DENY rule in block-dangerous-bash.sh;
# an UNMEDIATED write (a script the agent writes and runs) is caught by /doctor check
# 16 and -- decisively -- is non-authoritative: every honest consumer refuses a trust
# surface that is not committed-identical and operator-signed (deploy/trust.py). The
# root of trust is the operator's presence-requiring key, never this regex.
set -euo pipefail

HOOK_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CLASS_FILE="$HOOK_DIR/trust-surfaces.txt"

# The hard-coded FLOOR of the class. Same list in block-dangerous-bash.sh,
# deploy/trust.py and doctor.py; the battery below pins trust-surfaces.txt == floor.
TRUST_FLOOR=(
  'core/security/hooks/**'
  'deploy/safe-allowlist.yaml'
  'deploy/evidence/operator-*.md'
  'deploy/rulings/**'
  'deploy/trust.py'
  'deploy/compile-driver.py'
  'deploy/compile-backends.py'
  'deploy/audit-content.py'
  'deploy/retire.py'
  'deploy/promote.py'
  'deploy/pending.py'
  '.claude/settings.json'
  '.claude/settings.local.json'
  '.git/hooks/**'
  '.gitattributes'
)

# glob -> anchored extended regex: `**` spans directories, `*` stays inside a segment;
# anchored at a path-segment start so an instance path (deploy/trust.py) and the dev
# repo's source path (capabilities/knowledge-os/extracted/deploy/trust.py) both match.
glob_to_re() {
  local g="$1"
  g=${g//./\\.}
  g=${g//\*\*/__DS__}
  g=${g//\*/[^/]*}
  g=${g//\?/[^/]}
  g=${g//__DS__/.*}
  printf '(^|/)%s$' "$g"
}

load_class() {
  CLASS=("${TRUST_FLOOR[@]}")
  if [ -r "$CLASS_FILE" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      line=${line%%#*}
      line=$(printf '%s' "$line" | tr '\\' '/' | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')
      [ -n "$line" ] || continue
      local seen=0
      for g in "${CLASS[@]}"; do [ "$g" = "$line" ] && seen=1 && break; done
      if [ $seen -eq 0 ]; then CLASS+=("$line"); fi
    done < "$CLASS_FILE"
  fi
  return 0
}

# Returns 0 (and prints the glob) when the normalized path is in the class.
trust_match() {
  local p="$1" g
  for g in "${CLASS[@]}"; do
    if printf '%s' "$p" | grep -Eq "$(glob_to_re "$g")"; then
      printf '%s' "$g"
      return 0
    fi
  done
  return 1
}

# --------------------------------------------------------------- SELF-TEST
# `bash block-env-writes.sh --self-test` (v3.0.46): embedded cases per surface, both
# directions (deny + allow-sibling), then every committed fixture that carries a
# file_path against its pinned expectation. Intercepted before the stdin read.
if [ "${1:-}" = "--self-test" ]; then
  SELF_PATH="${BASH_SOURCE[0]}"
  pass=0; fail=0
  run_case() {
    expect="$1"; path="$2"; label="$3"
    json=$(jq -n --arg p "$path" '{tool_input:{file_path:$p}}')
    set +e
    printf '%s' "$json" | bash "$SELF_PATH" >/dev/null 2>&1
    rc=$?
    set -e
    kind=allow; [ "$rc" -eq 2 ] && kind=DENY
    if [ "$kind" = "$expect" ]; then pass=$((pass+1)); else
      fail=$((fail+1)); echo "FAIL [$label] expected=$expect got=$kind rc=$rc" >&2; fi
  }
  # -- the class, one deny + one allow-sibling per surface (brief section 2 fixtures)
  run_case DENY  'core/security/hooks/egress-allowlist.txt'            'hooks-allowlist'
  run_case DENY  'core/security/hooks/trust-surfaces.txt'              'hooks-class-file-itself'
  run_case DENY  'core/security/hooks/allowed_signers'                 'hooks-pin'
  run_case DENY  'core/security/hooks/test-inputs/new.json'            'hooks-fixture'
  run_case allow 'core/security/CREDENTIALS.md'                        'security-doc-sibling'
  run_case DENY  'deploy/safe-allowlist.yaml'                          'safe-allowlist'
  run_case allow 'deploy/safe-allowlist.yaml.example'                  'safe-allowlist-example'
  run_case DENY  'deploy/evidence/operator-x.md'                       'evidence-operator'
  run_case allow 'deploy/evidence/README.md'                           'evidence-readme'
  run_case allow 'deploy/evidence/operator-sub/nested.md'              'evidence-nested-not-class'
  run_case DENY  'deploy/rulings/retire-1/proposal.md'                 'rulings'
  run_case DENY  'deploy/trust.py'                                     'trust-py'
  run_case DENY  'deploy/compile-driver.py'                            'compile-driver'
  run_case DENY  'deploy/compile-backends.py'                          'compile-backends'
  run_case DENY  'deploy/audit-content.py'                             'audit-content'
  run_case DENY  'deploy/retire.py'                                     'retire-verb'
  run_case DENY  'deploy/promote.py'                                    'promote-action'
  run_case DENY  'deploy/pending.py'                                    'pending-list'
  run_case allow 'deploy/audit-content-v2.py'                          'audit-content-v2-not-class'
  run_case allow 'deploy/retire-manifest.py'                           'deploy-sibling'
  run_case DENY  '.claude/settings.json'                               'settings-json'
  run_case DENY  '.claude/settings.local.json'                         'settings-local'
  run_case allow '.claude/skills/x/SKILL.md'                           'skill-sibling'
  run_case DENY  '.git/hooks/pre-commit'                               'git-hook'
  run_case allow '.github/workflows/x.yml'                             'github-sibling'
  # -- path spellings: absolute, Windows, dev-repo source path, dot-slash
  run_case DENY  'C:\proj\deploy\safe-allowlist.yaml'                  'winpath-absolute'
  run_case DENY  '/home/u/proj/.claude/settings.local.json'            'posix-absolute'
  run_case DENY  'capabilities/knowledge-os/extracted/deploy/trust.py' 'dev-repo-source-path'
  run_case DENY  './core/security/hooks/block-env-writes.sh'           'dot-slash'
  run_case allow 'wiki/deploy/trust.py.md'                             'lookalike-not-class'
  # -- the .env rules stay
  run_case DENY  '.env'                                                'env'
  run_case DENY  'config/.env.production'                              'env-dotted'
  run_case allow '.env.example'                                        'env-example'
  run_case allow '.env.sample'                                         'env-sample'
  run_case allow 'src/main.py'                                         'plain'
  # -- the class file equals the floor (four homes, one content)
  if [ -r "$CLASS_FILE" ]; then
    file_lines=$(sed -E 's/#.*//; s/^[[:space:]]+|[[:space:]]+$//g' "$CLASS_FILE" | grep -v '^$' | sort)
    floor_lines=$(printf '%s\n' "${TRUST_FLOOR[@]}" | sort)
    if [ "$file_lines" = "$floor_lines" ]; then pass=$((pass+1)); else
      fail=$((fail+1)); echo "FAIL [class-file-equals-floor] trust-surfaces.txt drifted from the embedded floor" >&2; fi
  else
    echo "NOTE: trust-surfaces.txt absent -- floor only" >&2
  fi
  # -- Phase 2: committed fixtures carrying a file_path
  if [ -d "$HOOK_DIR/test-inputs" ]; then
    for f in "$HOOK_DIR"/test-inputs/*.json; do
      b=$(basename "$f")
      fp=$(jq -r '.tool_input.file_path // ""' "$f")
      [ -n "$fp" ] || continue
      case "$b" in *-passing.json|test-env-example-edit.json) exp=allow ;; *) exp=DENY ;; esac
      set +e
      bash "$SELF_PATH" < "$f" >/dev/null 2>&1
      rc=$?
      set -e
      kind=allow; [ "$rc" -eq 2 ] && kind=DENY
      if [ "$kind" = "$exp" ]; then pass=$((pass+1)); else
        fail=$((fail+1)); echo "FAIL [fixture $b] expected=$exp got=$kind" >&2; fi
    done
  fi
  echo "block-env-writes self-test: $pass passed, $fail failed"
  [ "$fail" -eq 0 ] || exit 1
  exit 0
fi

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // ""')
BASENAME=$(basename "$FILE_PATH")

# ---- trust-surface class write-guard (v3.0-98(a) generalized by v3.0-120)
NORM_PATH=$(printf '%s' "$FILE_PATH" | tr '\\' '/' | sed -E 's#^\./##')
load_class
if glob=$(trust_match "$NORM_PATH"); then
  echo "Blocked: '$FILE_PATH' is a TRUST SURFACE (class entry '$glob', core/security/hooks/trust-surfaces.txt). These files decide what a session may do and are operator-edited only: a session proposes the change in chat; the operator applies it outside the session and commits it with \`git commit -S\` under the pinned presence-requiring key (core/security/hooks/allowed_signers). Every honest consumer refuses a trust surface that is not committed-identical and operator-signed, so an unmediated write here is non-authoritative, not a shortcut." >&2
  exit 2
fi

# Allow .env.example and .env.sample (template files for operators to fill in).
# Block .env and all other .env.* (real secrets).
case "$BASENAME" in
  .env.example|.env.sample)
    exit 0
    ;;
  .env|.env.*)
    echo "Blocked: writes to '$BASENAME' are denied by policy. Secrets live in environment, never in the repo. .env.example and .env.sample are exempt." >&2
    exit 2
    ;;
  *)
    exit 0
    ;;
esac
