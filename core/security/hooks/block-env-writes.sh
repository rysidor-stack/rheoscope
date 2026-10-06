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
# Extended v3.0.56 (backlog v3.0-197, fleet inbox #19): a CREDENTIAL-FILE class by
# basename -- the files Google's client libraries and quickstarts write into a project
# tree (credentials.json, token.json, token.pickle, client_secret*.json, service-account
# key JSON) and key material (*.pem, *.key, *.ppk, *.p12, *.pfx). Same basis as the
# .env rule: secrets live in the OS vault via the broker (core/security/CREDENTIALS.md),
# and gitignored is not an exemption. Matched case-insensitively. A template copy whose
# name carries a `.example.`/`.sample.` segment (or ends .example/.sample) is exempt for
# the JSON/pickle names; key material has no exemption (parity with the commit scanner,
# which blocks a staged *.pem whatever it is called). Honest limit: a library that writes
# its token at RUNTIME (a script the session runs) is not an Edit/Write call -- the
# pre-commit scanner, which blocks the same names, is the gate on that lane.
# Also v3.0.56 (v3.0-198, fleet inbox #20): deploy/credential-bindings.yaml joins the
# trust-surface floor -- the one file that decides where a stored secret may be delivered.
set -euo pipefail

HOOK_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CLASS_FILE="$HOOK_DIR/trust-surfaces.txt"

# The hard-coded FLOOR of the class. Same list in block-dangerous-bash.sh,
# deploy/trust.py and doctor.py; the battery below pins trust-surfaces.txt == floor.
TRUST_FLOOR=(
  'core/security/hooks/**'
  'deploy/safe-allowlist.yaml'
  'deploy/credential-bindings.yaml'   # v3.0-198 (v3.0.56): the credential delivery gate
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
# CASE-INSENSITIVE (v3.0.56 firewall round 1, 2026-09-28, REVISED: on a case-insensitive
# filesystem `deploy/CREDENTIAL-BINDINGS.YAML` names the protected file, and the match here
# was case-sensitive -- pre-existing for every class member since v3.0.46; the Bash lane
# has always lowercased). A case-variant path on a case-sensitive host is denied too: a
# harmless over-match, never a loosening.
trust_match() {
  local p="$1" g
  for g in "${CLASS[@]}"; do
    if printf '%s' "$p" | grep -Eqi "$(glob_to_re "$g")"; then
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
  run_case DENY  'deploy/credential-bindings.yaml'                     '198-bindings'
  run_case DENY  'capabilities/knowledge-os/extracted/deploy/credential-bindings.yaml' '198-bindings-dev-source-path'
  run_case DENY  'C:\proj\deploy\credential-bindings.yaml'            '198-bindings-winpath'
  run_case allow 'deploy/credential-bindings.yaml.example'             '198-bindings-example'
  run_case allow 'deploy/credential-use.ps1.md'                         '198-broker-lookalike'
  # firewall round 1 (2026-09-28): letter case never changes class membership
  run_case DENY  'deploy/CREDENTIAL-BINDINGS.YAML'                     'r1-case-bindings-upper'
  run_case DENY  'Deploy\Credential-Bindings.yaml'                     'r1-case-bindings-mixed-winpath'
  run_case DENY  'DEPLOY/TRUST.PY'                                     'r1-case-trust-py'
  run_case DENY  'Core/Security/Hooks/Trust-Surfaces.txt'              'r1-case-class-file'
  run_case DENY  '.CLAUDE/settings.local.JSON'                         'r1-case-settings-local'
  CLAUDE_PROJECT_DIR='C:/proj' run_case DENY 'C:\PROJ\Deploy\Credential-Bindings.yaml' 'r1-case-abs-inside-root'
  run_case allow 'Deploy/Credential-Bindings.yaml.EXAMPLE'             'r1-case-example-still-allow'
  # firewall round 1: Windows name aliases (trailing dot/space, ::$DATA) are stripped first
  run_case DENY  'deploy/credential-bindings.yaml.'                    'r1-alias-trailing-dot-surface'
  run_case DENY  'deploy/trust.py '                                    'r1-alias-trailing-space-surface'
  run_case DENY  'deploy/safe-allowlist.yaml::$DATA'                   'r1-alias-ads-surface'
  run_case DENY  'connectors/token.json. .'                            'r1-alias-cred-trailing-dots-spaces'
  run_case DENY  'client_secret.json::$data'                           'r1-alias-cred-ads-lowercase'
  run_case DENY  '.env '                                               'r1-alias-dotenv-trailing-space'
  # firewall round 4: per-COMPONENT trailing dots/spaces, and the device-namespace prefix
  run_case DENY  'deploy./credential-bindings.yaml'                    'r4-component-trailing-dot'
  run_case DENY  'deploy /credential-bindings.yaml'                    'r4-component-trailing-space'
  run_case DENY  'core. /security../hooks /allowed_signers'            'r4-several-components'
  run_case DENY  'secrets./token.json'                                 'r4-component-alias-cred'
  run_case DENY  'config ./.env'                                       'r4-component-alias-dotenv'
  run_case DENY  '\\?\C:\proj\deploy\trust.py'                      'r4-device-namespace-prefix'
  CLAUDE_PROJECT_DIR='C:/proj' run_case DENY '\\?\C:\proj\deploy\credential-bindings.yaml' 'r4-device-prefix-inside-root'
  CLAUDE_PROJECT_DIR='C:/proj' run_case allow '\\?\D:\other\deploy\trust.py' 'r4-device-prefix-other-root-allow'
  run_case DENY  'x/../deploy/trust.py'                                'r4-parent-segment-untouched-by-alias-strip'
  # firewall round 5: a `.` or `..` component carrying trailing spaces, and all-dots components
  run_case DENY  'deploy/. /credential-bindings.yaml'                  'r5-dot-space-component'
  run_case DENY  'deploy/x/.. /credential-bindings.yaml'               'r5-dotdot-space-component'
  run_case DENY  'deploy/.../credential-bindings.yaml'                 'r5-three-dots-component'
  run_case DENY  'deploy/x/. . /trust.py'                              'r5-dot-space-dot-space-reads-as-parent'
  run_case DENY  'deploy .  /x/../trust.py'                            'r5-mixed-run-then-parent'
  run_case allow 'deploy/../other/trust.py'                            'r5-real-parent-still-leaves'
  run_case allow 'deploy/credential-bindings.yaml.example.'            'r1-alias-example-stays-example'
  # firewall round 2: repeated separators and `.` segments collapse before the match
  run_case DENY  'deploy//credential-bindings.yaml'                    'r2-double-slash-bindings'
  run_case DENY  'deploy/./credential-bindings.yaml'                   'r2-dot-segment-bindings'
  run_case DENY  'deploy\\credential-bindings.yaml'                    'r2-double-backslash-bindings'
  run_case DENY  '././deploy/././trust.py'                             'r2-repeated-dot-segments'
  run_case DENY  'core//security///hooks/allowed_signers'              'r2-run-of-slashes-hooks-dir'
  run_case DENY  'x/../deploy/trust.py'                                'r2-dotdot-already-anchored'
  CLAUDE_PROJECT_DIR= run_case DENY 'C:/proj/deploy//trust.py'                          'r2-abs-no-env-double-slash'
  CLAUDE_PROJECT_DIR='C:/proj' run_case DENY 'C:/proj//deploy/./credential-bindings.yaml' 'r2-abs-inside-root-slashes-dots'
  run_case allow 'deploy//credential-bindings.yaml.example'            'r2-example-still-allow'
  # firewall round 3: internal `seg/..` pairs collapse before the match
  run_case DENY  'deploy/x/../credential-bindings.yaml'                'r3-internal-parent-bindings'
  run_case DENY  'deploy/a/b/../../credential-bindings.yaml'           'r3-two-internal-parents'
  run_case DENY  'core/security/x/../hooks/allowed_signers'            'r3-internal-parent-hooks-dir'
  run_case DENY  'deploy\x\..\trust.py'                               'r3-internal-parent-backslash'
  run_case DENY  'deploy/./x/.././credential-bindings.yaml'            'r3-dots-and-parents-mixed'
  CLAUDE_PROJECT_DIR= run_case DENY 'C:/proj/deploy/x/../trust.py'    'r3-abs-no-env-internal-parent'
  run_case allow 'deploy/credential-bindings.yaml/../notes.md'         'r3-parent-leaves-the-class-allow'
  run_case allow 'core/security/hooks/../README-other.md'              'r3-parent-out-of-hooks-dir-allow'
  CLAUDE_PROJECT_DIR='C:/proj' run_case allow 'C:/proj/../other/core/security/hooks/x.sh' 'r1-alias-traversal-unchanged'
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
  # -- v3.0-166 (v3.0.55): anchoring to the project root, both directions
  CLAUDE_PROJECT_DIR='C:\proj' run_case DENY 'C:\proj\core\security\hooks\scan-staged-secrets.sh' '166-abs-inside-root-DENY'
  CLAUDE_PROJECT_DIR='C:/proj'  run_case DENY 'C:/proj/core/security/hooks/allowed_signers'           '166-abs-inside-root-fwd-DENY'
  CLAUDE_PROJECT_DIR='C:/proj'  run_case allow 'C:/other-repo/core/security/hooks/scan-staged-secrets.sh' '166-abs-OTHER-repo-allow'
  CLAUDE_PROJECT_DIR='C:/proj'  run_case allow 'D:/dev/harness/core/security/hooks/block-env-writes.sh' '166-abs-other-drive-allow'
  CLAUDE_PROJECT_DIR='C:/proj'  run_case DENY 'C:/other-repo/.env'                                      '166-abs-other-repo-dotenv-still-DENY'
  CLAUDE_PROJECT_DIR='C:/proj'  run_case DENY 'core/security/hooks/trust-surfaces.txt'                '166-relative-still-DENY'
  CLAUDE_PROJECT_DIR='C:/proj'  run_case allow 'C:/proj-two/core/security/hooks/x.sh'                  '166-prefix-collision-proj-two-is-outside'
  CLAUDE_PROJECT_DIR=           run_case DENY 'C:/other-repo/core/security/hooks/scan-staged-secrets.sh' '166-no-env-legacy-fail-closed'
  # -- firewall round 1 (2026-09-27): traversal is canonicalized before the compare
  CLAUDE_PROJECT_DIR='C:/proj'  run_case DENY 'C:/other/../proj/core/security/hooks/x.sh'              '166-traversal-INTO-root-DENY'
  CLAUDE_PROJECT_DIR='C:/proj'  run_case DENY 'C:/proj/x/../core/security/hooks/x.sh'                 '166-traversal-inside-root-DENY'
  CLAUDE_PROJECT_DIR='C:/proj'  run_case allow 'C:/proj/../other/core/security/hooks/x.sh'              '166-traversal-OUT-of-root-allow'
  CLAUDE_PROJECT_DIR='C:/proj'  run_case DENY 'C:/proj/./core/security/hooks/./trust-surfaces.txt'     '166-dot-segments-inside-DENY'
  # -- firewall round 3 (2026-09-27): the LEXICAL fallback, forced, incl. drive-letter root climbs
  #    (MSYS_NO_PATHCONV: Git for Windows would rewrite a POSIX-looking argument before jq sees it)
  RHEOSCOPE_TEST_NO_REALPATH=1 CLAUDE_PROJECT_DIR='C:/proj' run_case DENY 'C:/other/../proj/core/security/hooks/x.sh'   '166-lexical-traversal-INTO-root-DENY'
  RHEOSCOPE_TEST_NO_REALPATH=1 CLAUDE_PROJECT_DIR='C:/proj' run_case allow 'C:/proj/../other/core/security/hooks/x.sh'  '166-lexical-traversal-OUT-allow'
  RHEOSCOPE_TEST_NO_REALPATH=1 CLAUDE_PROJECT_DIR='C:/proj' run_case DENY 'C:/../other/core/security/hooks/x.sh'        '166-lexical-drive-root-climb-fail-closed-DENY'
  MSYS_NO_PATHCONV=1 RHEOSCOPE_TEST_NO_REALPATH=1 CLAUDE_PROJECT_DIR='/proj'   run_case DENY '/../other/core/security/hooks/x.sh'           '166-lexical-posix-root-climb-fail-closed-DENY'
  MSYS_NO_PATHCONV=1 RHEOSCOPE_TEST_NO_REALPATH=1 CLAUDE_PROJECT_DIR='/proj'   run_case DENY '/other/../proj/core/security/hooks/x.sh'      '166-lexical-posix-traversal-INTO-root-DENY'
  MSYS_NO_PATHCONV=1 RHEOSCOPE_TEST_NO_REALPATH=1 CLAUDE_PROJECT_DIR='/proj'   run_case allow '/proj/../other/core/security/hooks/x.sh'     '166-lexical-posix-traversal-OUT-allow'
  run_case allow 'wiki/deploy/trust.py.md'                             'lookalike-not-class'
  # -- v3.0-197 (v3.0.56): the credential-file class, both directions
  run_case DENY  'credentials.json'                                    '197-credentials-json'
  run_case DENY  'connectors/gmail/token.json'                         '197-token-json'
  run_case DENY  'secrets/token.pickle'                                '197-token-pickle'
  run_case DENY  'client_secret.json'                                  '197-client-secret-bare'
  # the Google download name is assembled from fragments: a literal client-ID shape made GitHub push
  # protection refuse the public mirror (v3.0.56.1); the hook sees the same bytes either way
  run_case DENY  "client_secret_1234-abcd.apps.google""usercontent.com.json" '197-client-secret-google-download-name'
  run_case DENY  'keys/service-account.json'                           '197-service-account'
  run_case DENY  'service_account_key.json'                            '197-service-account-underscore'
  run_case DENY  'serviceAccountKey.json'                              '197-service-account-firebase-name'
  run_case DENY  'Token.JSON'                                          '197-case-insensitive'
  run_case DENY  'C:\proj\connectors\token.json'                     '197-winpath'
  run_case DENY  'keys/deploy.pem'                                     '197-pem'
  run_case DENY  'signing.key'                                         '197-key'
  run_case DENY  'putty.ppk'                                           '197-ppk'
  run_case DENY  'legacy-service-key.p12'                              '197-p12'
  run_case DENY  'cert.PFX'                                            '197-pfx-uppercase'
  run_case DENY  'server.example.pem'                                  '197-key-material-has-no-example-exemption'
  CLAUDE_PROJECT_DIR='C:/proj' run_case DENY 'C:/other-repo/token.json' '197-outside-project-still-DENY'
  run_case allow 'client_secret.example.json'                          '197-example-client-secret'
  run_case allow 'service-account.sample.json'                         '197-sample-service-account'
  run_case allow 'credentials.json.example'                            '197-credentials-example-suffix'
  run_case allow 'token.json.sample'                                   '197-token-sample-suffix'
  run_case allow 'tokens.json'                                         '197-design-tokens-not-class'
  run_case allow 'docs/token.json.md'                                  '197-doc-about-token-not-class'
  run_case allow 'src/credentials.py'                                  '197-credentials-module-not-class'
  run_case allow 'keys/README.md'                                      '197-keys-readme'
  # -- the .env rules stay
  run_case DENY  '.env'                                                'env'
  run_case DENY  'config/.env.production'                              'env-dotted'
  run_case allow '.env.example'                                        'env-example'
  run_case allow '.env.sample'                                         'env-sample'
  run_case DENY  '.ENV'                                                'env-uppercase'
  run_case DENY  'config/.Env.Production'                              'env-mixed-case-dotted'
  run_case allow '.ENV.EXAMPLE'                                        'env-example-uppercase'
  # -- v3.0-199 (v3.0.62): direnv's .envrc joins the class, same exemption
  run_case DENY  '.envrc'                                              '199-envrc'
  run_case DENY  'sub/.EnvRC'                                          '199-envrc-mixed-case-nested'
  run_case DENY  '.envrc.local'                                        '199-envrc-dotted'
  run_case allow '.envrc.example'                                      '199-envrc-example'
  run_case allow '.envrc.sample'                                       '199-envrc-sample'
  run_case allow '.environment'                                        '199-other-env-prefix-outside'
  run_case allow 'docs/envrc.md'                                       '199-named-after-envrc'
  run_case allow 'src/main.py'                                         'plain'
  # -- v3.0.56 stranger-test fold: an ALLOWED call is silent on stderr (a lone `tr '\'` made
  #    GNU tr warn on every invocation; verdicts were right, the noise reached every session)
  for qp in 'src/app.py' 'C:\proj\notes.md' 'deploy/credential-bindings.yaml.example'; do
    set +e; qerr=$(jq -n --arg p "$qp" '{tool_input:{file_path:$p}}' | bash "$SELF_PATH" 2>&1 >/dev/null); qrc=$?; set -e
    if [ "$qrc" -eq 0 ] && [ -z "$qerr" ]; then pass=$((pass+1)); else
      fail=$((fail+1)); echo "FAIL [quiet-stderr $qp] rc=$qrc stderr=$qerr" >&2; fi
  done
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
# v3.0.56 (firewall round 1, 2026-09-28): Windows name ALIASES a plain tool call can carry
# name the same file -- a trailing dot or space (Windows drops both) and the default-stream
# suffix `::$DATA`. Every rule below reads the path with them stripped. A final `.`/`..`
# SEGMENT is left alone (only dots after another character in the segment are stripped),
# so traversal handling is unchanged. Out of reach, stated: 8.3 short names
# (DEPLOY~1\...) and symlink aliases on a host without realpath -- the adaptive-spelling
# class, same boundary as every tripwire here.
# firewall round 4 (2026-09-28, REJECTED): Win32 drops trailing dots/spaces from EVERY path
# component, not only the last (`deploy./credential-bindings.yaml`, `deploy /x`); and the
# device-namespace prefix `\\?\` / `\\.\` names the same file. Both are stripped here.
# firewall round 5 (2026-09-28, REJECTED): the component rule needed a non-dot character
# before the run, so `deploy/. /x` (a `.` plus a space) kept its space and never became `/./`.
# Now ONE pass per component, the way Win32 reads it: a component made only of dots and
# spaces reads as `..` when its dots are exactly two and `.` otherwise (all spaces: empty);
# any other component loses its trailing run of dots and spaces, in any mix. `.`/`..` are
# then left for the segment collapse below.
FILE_PATH_N=$(printf '%s' "$FILE_PATH" | tr '\\' '/' | sed -E 's#^//[?.]/(unc/)?##I; s#::\$[Dd][Aa][Tt][Aa]$##' \
  | awk -F/ -v OFS=/ '{ for (i = 1; i <= NF; i++) { c = $i
      if (c ~ /^[. ]+$/) { d = c; gsub(/ /, "", d); $i = (d == ".." ? ".." : (d == "" ? "" : ".")) }
      else { sub(/[. ]+$/, "", c); $i = c } }
    print }')
BASENAME=$(basename "$FILE_PATH_N")

# ---- trust-surface class write-guard (v3.0-98(a) generalized by v3.0-120)
NORM_PATH=$(printf '%s' "$FILE_PATH_N" | sed -E 's#^\./##')
# v3.0-166 (v3.0.55): an ABSOLUTE path that lies OUTSIDE this project is another
# repository's file, not this perimeter. When the host names the project root
# (CLAUDE_PROJECT_DIR, set on every hook call), the class is matched against the
# path RELATIVE to that root, and an absolute path under a DIFFERENT root passes
# this guard (the .env rules below still apply by basename). Without the env var
# the legacy whole-path match stands -- fail-closed, never wider. The Bash lane
# anchored its trust DENY to write TARGETS in v3.0.51 (v3.0-144); this is the
# Edit/Write lane's counterpart.
OUTSIDE_PROJECT=0
PROJECT_ROOT_NORM=$(printf '%s' "${CLAUDE_PROJECT_DIR:-}" | tr '\\' '/' | sed -E 's#/+#/#g; s#/+$##')
# CANONICALIZE both sides before the compare (cross-vendor firewall round 1,
# 2026-09-27, REJECTED: a lexical prefix test read `/other/../proj/core/security/
# hooks/x.sh` as outside the project). Resolution order, stated exactly (round 2):
# `realpath -m` when it succeeds (follows symlinks, resolves the parent of a
# not-yet-existing file); otherwise a LEXICAL collapse of `.` and `..` segments,
# which classifies the path without symlink knowledge; only a `..` that climbs past
# the root is FAIL-CLOSED to the legacy whole-path match. Documented residual: on a
# host without `realpath`, a symlink alias into the perimeter is judged by its
# spelled path.
_canon() {
  local p="$1" c
  # RHEOSCOPE_TEST_NO_REALPATH=1 exercises the lexical fallback on a host that has
  # realpath (board only; the lexical path is the stricter one, never a loosening)
  if [ -z "${RHEOSCOPE_TEST_NO_REALPATH:-}" ] && c=$(realpath -m -- "$p" 2>/dev/null) && [ -n "$c" ]; then printf '%s' "$c"; return 0; fi
  # firewall round 3 (2026-09-27): a drive letter (`C:`) is a ROOT exactly like the
  # empty first segment of `/...`; `..` above either is unresolved -> fail closed
  printf '%s' "$p" | awk -F'/' '{
    n = 0
    for (i = 1; i <= NF; i++) {
      s = $i
      if (s == "." || (s == "" && i > 1)) continue
      if (s == "..") {
        if (n >= 1 && (a[n] == "" || a[n] ~ /^[A-Za-z]:$/)) { print "__UNRESOLVED__"; exit }
        if (n >= 1) n--; else { print "__UNRESOLVED__"; exit }
        continue
      }
      a[++n] = s
    }
    out = ""; for (i = 1; i <= n; i++) out = out (i > 1 ? "/" : "") a[i]; print out }'
}
case "$NORM_PATH" in
  /*|[A-Za-z]:/*)
    if [ -n "$PROJECT_ROOT_NORM" ]; then
      cp=$(_canon "$NORM_PATH"); cr=$(_canon "$PROJECT_ROOT_NORM")
      case "$cp$cr" in
        *__UNRESOLVED__*) : ;;                       # fail closed: legacy match below
        *)
          lp=$(printf '%s' "$cp" | tr 'A-Z' 'a-z')
          lr=$(printf '%s' "$cr" | tr 'A-Z' 'a-z')
          case "$lp" in
            "$lr"/*) NORM_PATH=${cp:$(( ${#cr} + 1 ))} ;;
            *) OUTSIDE_PROJECT=1 ;;
          esac ;;
      esac
    fi
    ;;
esac
# v3.0.56 firewall round 2 (2026-09-28, REJECTED): repeated separators and `.` segments
# name the same file (`deploy//trust.py`, `deploy/./trust.py`; a doubled backslash from a
# PowerShell or JSON string arrives as `//`) and broke the contiguous class match -- pre-
# existing for every member. Collapse them lexically (pure bash, no fork) before matching;
# `..` needs nothing here: the class regex is anchored at any `/`, so `x/../deploy/trust.py`
# already matches, and an absolute path was canonicalized above.
while [[ "$NORM_PATH" == *//* ]]; do NORM_PATH=${NORM_PATH//\/\//\/}; done
while [[ "$NORM_PATH" == */./* ]]; do NORM_PATH=${NORM_PATH//\/.\//\/}; done
while [[ "$NORM_PATH" == ./* ]]; do NORM_PATH=${NORM_PATH#./}; done
# v3.0.56 firewall round 3 (2026-09-28, REJECTED): a `seg/..` pair INSIDE a path
# (`deploy/x/../credential-bindings.yaml`) names the guarded file and broke the contiguous
# match; round 2's note that `..` needs nothing was wrong for internal pairs. Every `seg/..`
# pair is collapsed lexically, leftmost first, until none is left -- pure bash (no fork),
# entered only when the text contains `/..`. A leading `../` run collapses too
# (`../../deploy/x` reads as `deploy/x`): that can only ADD a match, never remove one the
# anchored regex already had, so over-collapse is a harmless over-match, never a loosening.
_PARENT_RE='([^/[:space:]]+)/\.\.(/|$)'
while [[ "$NORM_PATH" == */..* ]] && [[ "$NORM_PATH" =~ $_PARENT_RE ]]; do
  NORM_PATH=${NORM_PATH/"${BASH_REMATCH[0]}"/}
done
load_class
if [ "$OUTSIDE_PROJECT" -eq 0 ] && glob=$(trust_match "$NORM_PATH"); then
  echo "Blocked: '$FILE_PATH' is a TRUST SURFACE (class entry '$glob', core/security/hooks/trust-surfaces.txt). These files decide what a session may do and are operator-edited only: a session proposes the change in chat; the operator applies it outside the session and commits it -- signed with \`git commit -S\` under the pinned presence-requiring key (core/security/hooks/allowed_signers) when project.yaml says \`trust_surface_signing: required\`; under \`visible\`, an ordinary commit that stays on the pending list until an attended sweep shows it. Every honest consumer refuses a trust surface that is not committed-identical (and, under \`required\`, operator-signed), so an unmediated write here is non-authoritative, not a shortcut." >&2
  exit 2
fi

# ---- credential-file class by basename (v3.0-197, v3.0.56) -- applies wherever the
# path lies (a secret file is a secret file in any repository), case-insensitive.
CRED_BASE=${FILE_PATH_N##*/}
CRED_LC=$(printf '%s' "$CRED_BASE" | tr 'A-Z' 'a-z')
CRED_KIND=''
case "$CRED_LC" in
  *.pem|*.key|*.ppk|*.p12|*.pfx) CRED_KIND='key material' ;;
  *.example|*.sample|*.example.*|*.sample.*) : ;;   # a template copy of a JSON/pickle name
  credentials.json|token.json|token.pickle|client_secret*.json|service[-_]account*.json|serviceaccount*.json)
    CRED_KIND='OAuth client / token / service-account file' ;;
esac
if [ -n "$CRED_KIND" ]; then
  echo "Blocked: '$CRED_BASE' is a CREDENTIAL FILE by name ($CRED_KIND; v3.0-197). A secret never lives in a file in the tree -- gitignored is not an exemption (core/security/CREDENTIALS.md). Store it in the OS vault with deploy/credential-store.ps1 and deliver it by name with deploy/credential-use.ps1; for a Google API client that means the client secret and the refresh token, never the library's default credentials.json/token.json. A template copy named *.example.json / *.sample.json is exempt for the JSON/pickle names." >&2
  exit 2
fi

# Allow .env.example and .env.sample (template files for operators to fill in).
# Block .env and all other .env.* (real secrets). Matched case-insensitively since v3.0.56
# (`.ENV` is `.env` on Windows; the old match was case-sensitive -- found by the release's
# own differential run), the exemption included, byte-parity with the scanner. Since
# v3.0.62 (v3.0-199) direnv's .envrc and .envrc.* are the same class with the same
# exemption: by convention they hold `export SECRET=...` in plaintext.
case "$(printf '%s' "$BASENAME" | tr 'A-Z' 'a-z')" in
  .env.example|.env.sample|.envrc.example|.envrc.sample)
    exit 0
    ;;
  .env|.env.*|.envrc|.envrc.*)
    echo "Blocked: writes to '$BASENAME' are denied by policy. Secrets live in environment, never in the repo. .env.example and .env.sample (and the same for .envrc) are exempt." >&2
    exit 2
    ;;
  *)
    exit 0
    ;;
esac
