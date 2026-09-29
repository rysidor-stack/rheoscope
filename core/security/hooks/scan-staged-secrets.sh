#!/usr/bin/env bash
# scan-staged-secrets.sh -- git pre-commit secret scanner (v3.0.36, backlog v3.0-12;
# design: harness-v3.0/specs/secret-scanner-perimeter-mini-pass-2026-08-11.md, ratified
# option 1: gates EVERY commit, the operator's own included).
#
# THE LANE THIS CLOSES: since v3.0.33 egress ASKS and push is deliberately un-denied
# (v3.0.19), the one unguarded exfiltration path was commit-then-push -- secret-shaped
# content lands in a commit with no gate anywhere, and the push that carries it
# off-machine is sanctioned. This hook is that lane's gate, at the git layer, where
# no session has a vote.
#
# WHAT IT SCANS: the STAGED diff only -- added lines (git diff --cached, +lines) and
# newly staged paths. Committed history is out of scope (rewriting history is an
# operator act); worktree noise never blocks a commit that doesn't stage it.
#
# WHAT IT BLOCKS (exit 1, naming file + pattern label + remedy):
#   1. key material   -- PEM/OpenSSH private-key blocks, PuTTY PPK headers
#   2. known-prefix tokens WITH LENGTH/CHARSET TEETH (prose can't trip them):
#      AWS AKIA..., GitHub ghp_/gho_/ghs_/github_pat_..., Anthropic sk-ant-...,
#      OpenAI sk-..., Slack xox[baprs]-..., Stripe sk_live_..., Google AIza...,
#      three-segment JWTs, and (v3.0.56, v3.0-197) Google OAuth's own shapes: the
#      client secret GOCSPX-..., the access token ya29.... and the refresh token
#      1//0... -- so a real value in an example-NAMED file, or pasted into any file,
#      still blocks where the path class cannot see it
#   3. embedded-credential URLs -- scheme://user:password@host, password not a
#      named placeholder shape
#   4. credential FILES by staged path -- .env* (except .env.example/.env.sample,
#      byte-parity with block-env-writes.sh), key material *.pem/*.key/*.ppk/*.p12/*.pfx,
#      and (v3.0.56, v3.0-197) the files Google's client libraries write:
#      credentials.json, token.json, token.pickle, client_secret*.json, service-account
#      key JSON -- template copies with a .example./.sample. name segment exempt for
#      those JSON/pickle names only. All of class 4 is matched case-insensitively (.env*
#      included since v3.0.56 -- `.ENV` is `.env` on Windows).
#      credential-bindings.yaml is deliberately NOT blocked (committed by design;
#      holds destinations, never values -- core/security/CREDENTIALS.md).
#
# WHAT IT NEVER BLOCKS (the false-positive story, hard-coded here on purpose --
# a config file would be a loosening surface, which is exactly what the v3.0-98
# write-guard exists to deny):
#   - the perimeter's own fixtures/recipes: any staged path under
#     core/security/hooks/test-inputs/
#   - placeholder-shaped values: EXAMPLE / REDACTED / PLACEHOLDER / your-...-here /
#     <angle-bracket> / mustache-style double-brace template markers / xxx-runs --
#     checked per matched value, so CREDENTIALS.md (which NAMES token prefixes
#     without values) commits clean. (This file spells the double-brace shape in
#     words on purpose: init-validate's placeholder scan reads a literal one as an
#     unresolved substitution -- caught by this release's own stranger-test run.)
#
# BYPASS: `git commit --no-verify` -- git's own, kept for the OPERATOR's hands.
# Agent sessions are barred from it mechanically (block-dangerous-bash.sh DENY
# tier, same release). A false positive costs the operator one deliberate flag,
# and the README asks that each such bypass be reported so the pattern learns.
#
# Self-test: scan-staged-secrets.sh --self-test (scratch git repos; fixtures are
# GENERATED at run time, never committed -- committed secret-shaped bytes would
# trip GitHub push protection on the public mirror and read as a real leak).
set -uo pipefail

# --------------------------------------------------------------- pattern tables
# "<label>|<extended regex>" -- content classes, matched against ADDED lines.
CONTENT_PATTERNS=(
  'private key block|-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----'
  'PuTTY private key|^PuTTY-User-Key-File-[0-9]+:'
  'AWS access key id|(^|[^A-Z0-9])AKIA[0-9A-Z]{16}([^A-Z0-9]|$)'
  'GitHub token|(^|[^A-Za-z0-9_])(ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{36,}'
  'GitHub fine-grained token|(^|[^A-Za-z0-9_])github_pat_[A-Za-z0-9_]{60,}'
  'Anthropic API key|(^|[^A-Za-z0-9_-])sk-ant-[A-Za-z0-9_-]{20,}'
  'OpenAI-style API key|(^|[^A-Za-z0-9_-])sk-[A-Za-z0-9_-]{32,}'
  'Slack token|(^|[^A-Za-z0-9_-])xox[baprs]-[A-Za-z0-9-]{10,}'
  'Stripe live secret key|(^|[^A-Za-z0-9_-])sk_live_[A-Za-z0-9]{16,}'
  'Google API key|(^|[^A-Za-z0-9_-])AIza[A-Za-z0-9_-]{35}'
  'Google OAuth client secret|(^|[^A-Za-z0-9_-])GOCSPX-[A-Za-z0-9_-]{24,}'
  'Google OAuth access token|(^|[^A-Za-z0-9_-])ya29\.[A-Za-z0-9_-]{20,}'
  'Google OAuth refresh token|(^|[^A-Za-z0-9_-])1//0[A-Za-z0-9_-]{40,}'
  'JWT|(^|[^A-Za-z0-9._-])eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'
  'embedded credential URL|[a-z][a-z0-9+.-]*://[^/:@[:space:]]+:[^@[:space:]]{3,}@[A-Za-z0-9.-]+'
)

# A matched VALUE whose bytes carry a named placeholder shape passes. Checked
# against the matched region only, never the whole line (a real key on a line
# that also says "example usage" must still block).
PLACEHOLDER_RE='EXAMPLE|REDACTED|PLACEHOLDER|CHANGE[-_]?ME|your-[a-z0-9-]+-here|<[A-Za-z][A-Za-z0-9 _-]*>|\{\{[^}]+\}\}|[Xx]{6,}|\.\.\.'

# Staged PATHS that are credential homes. Basename-matched, extended regex.
PATH_BLOCK_RE='(^|/)\.env(\..*)?$'
PATH_ALLOW_RE='(^|/)\.env\.(example|sample)$'
# key material, case-insensitive, no exemption (a staged *.pem blocks whatever it is called)
PATH_KEY_RE='(^|/)[^/]*\.(pem|key|ppk|p12|pfx)$'
# v3.0-197 (v3.0.56): OAuth client / token / service-account files, case-insensitive
PATH_CRED_RE='(^|/)(credentials\.json|token\.json|token\.pickle|client_secret[^/]*\.json|service[-_]?account[^/]*\.json)$'
PATH_CRED_TEMPLATE_RE='(^|/)[^/]*\.(example|sample)(\.[^/]*)?$'
# The perimeter's own fixture dir -- hard-coded, never configurable.
# anchored at the repository ROOT since v3.0.56 (firewall round 4, 2026-09-28: the unanchored
# form exempted any path CONTAINING the directory -- x/core/security/hooks/test-inputs/.env)
EXEMPT_RE='^core/security/hooks/test-inputs/'
# The scanner's own source at its canonical path gets the KNOWN-OWN-LINES rule
# instead of an exemption (v3.0-104 fixed the adoption self-block; v3.0-109
# caught the fix's hole: a blanket path exemption composed with the
# write-guard's shell-path honest limit into an unscanned commit lane). Rule:
# added lines that exist VERBATIM in the running scanner script ($0 -- the
# installed hook at commit time) are its own known content and pass; every
# other added line is scanned normally. Adoption passes (the staged file IS
# the just-installed hook); an appended or embedded secret is never in the
# running script's text, so it blocks. A session that can rewrite the hook
# itself can neuter any pre-commit defense -- outside a hook's threat model;
# the tracked-file lane is what this closes.
OWN_PATH_RE='(^|/)core/security/hooks/scan-staged-secrets\.sh$'

# v3.0.54 (backlog v3.0-164, fleet inbox #13): the scan is ONE pass, not one subprocess
# tree per staged file. The original loop spawned `git diff` plus ~15 greps PER FILE, and
# on an msys host (fork is expensive there) the fleet's newest instance met its birth
# commit with an eight-minute silence it read as a hang. Now: one name-only listing plus
# one `git diff --cached` over the whole index, one awk to split it into <path><TAB><added
# line> rows (git QUOTES a path with a tab, control or non-ASCII byte in the header --
# `+++ "b/..."` -- and the awk reads that form too, so such a file is scanned; the old
# per-path loop could not even address it and silently skipped it), two greps
# per pattern over every added line at once (the match, then the placeholder filter --
# twenty-four greps total, whatever the file count), and a progress line on stderr once the staged set is large enough to take noticeable
# time. Detection is the same battery: same patterns, same placeholder rule judged per
# matched region, same path classes, same exempt dir, same known-own-lines rule for the
# scanner's own file. Binary members carry no added text lines, so they cost nothing.
PROGRESS_FROM=20   # staged-file count at which the progress line prints
_SCAN_TMP=()

_cleanup() { rm -f -- ${_SCAN_TMP[@]+"${_SCAN_TMP[@]}"}; }

_fail() {
  _cleanup
  echo "COMMIT BLOCKED by scan-staged-secrets.sh: $1" >&2
  echo "  Secrets never enter git history: once pushed, a leaked value is public even if deleted later (core/security/CREDENTIALS.md -- values live in the OS vault, never the repo)." >&2
  echo "  If this is a FALSE POSITIVE: the operator (never an agent -- mechanically barred) may bypass ONCE with 'git commit --no-verify', and should report the pattern so it learns." >&2
  exit 1
}

scan_repo() {
  # $1 = repo dir. Returns 0 clean, 1 blocked (message on stderr).
  local repo="$1"
  local paths n_paths t0 hit diff rows_f lines_f paths_f own p label pat ln

  # v3.0-110: ACMR, not ACR -- the original perimeter enumerated Added/Copied/
  # Renamed only, so a secret pasted into an already-tracked file was NEVER
  # scanned (caught 2026-08-17 by the v3.0-109 fix's end-to-end evidence: the
  # real hook-mediated append-tamper commit passed while every direct-call test
  # staged new files). Class 4 (credential files by path) also wants M: a
  # tracked file RENAMED onto a credential name arrives as R, but content
  # landing in an existing .env-class path must block too.
  paths=$(git -C "$repo" diff --cached --name-only --diff-filter=ACMR 2>/dev/null) || return 0
  [ -z "$paths" ] && return 0
  n_paths=$(printf '%s\n' "$paths" | grep -c .)
  t0=$(date +%s)
  if [ "$n_paths" -ge "$PROGRESS_FROM" ]; then
    echo "scan-staged-secrets: scanning $n_paths staged file(s) in one pass (a large commit takes seconds; this is not a hang -- v3.0-164)" >&2
  fi

  # ---- class 4: credential files by path -- ONE grep over the path list -------
  # (git quotes a name with a non-ASCII or control byte -- `"caf\303\251/.env"` -- and the
  # trailing quote defeated the $-anchored class patterns in the old loop too; strip the
  # quotes first, cross-vendor round-3 fold)
  local cand
  cand=$(printf '%s\n' "$paths" | sed -e 's/^"//' -e 's/"$//' | grep -Ev -e "$EXEMPT_RE" || true)
  # .env* in any letter case too (v3.0.56: `.ENV` is `.env` on Windows; the release's own
  # differential run found it passing both versions), the exemption likewise
  hit=$(printf '%s\n' "$cand" | grep -Evi -e "$PATH_ALLOW_RE" | grep -Ei -e "$PATH_BLOCK_RE" | head -n 1 || true)
  [ -n "$hit" ] || hit=$(printf '%s\n' "$cand" | grep -Ei -e "$PATH_KEY_RE" | head -n 1 || true)
  [ -n "$hit" ] || hit=$(printf '%s\n' "$cand" | grep -Ei -e "$PATH_CRED_RE" | grep -Evi -e "$PATH_CRED_TEMPLATE_RE" | head -n 1 || true)
  if [ -n "$hit" ]; then
    _fail "staged file '$hit' is a credential-file class (.env*, key material, or an OAuth client/token/service-account file such as credentials.json, token.json, client_secret*.json). Unstage it (git restore --staged '$hit') and keep the secret in the OS vault via the credential broker (core/security/CREDENTIALS.md); .env.example/.env.sample and *.example.json/*.sample.json copies of the OAuth names are exempt."
  fi

  # ---- classes 1-3: added lines, ONE diff over the whole index -----------------
  # --no-renames: a renamed file arrives as delete + add, so its full content is
  # scanned exactly as the per-path loop saw it (a rename-paired diff would show
  # only the delta and let moved content ride through unscanned).
  diff=$(git -C "$repo" diff --cached -U0 --no-color --no-ext-diff --no-renames \
         --diff-filter=ACMR 2>/dev/null) || diff=""
  [ -z "$diff" ] && return 0
  # scratch files: a failed mktemp FAILS CLOSED (cross-vendor round-1 fold: an
  # unwritable TMPDIR left the line file empty and the scan returned clean)
  rows_f=$(mktemp) && lines_f=$(mktemp) && paths_f=$(mktemp) \
    || _fail "cannot create scratch files (mktemp failed -- TMPDIR unwritable?); the scan did not run, so the commit is refused rather than passed unscanned. Fix the environment and retry; the operator may bypass ONCE with --no-verify."
  _SCAN_TMP=("$rows_f" "$lines_f" "$paths_f" "$rows_f.own" "$rows_f.rest")
  # <path><TAB><added line> rows; the exempt dir dropped here, per path
  # A `+++` line is a file header ONLY before the file's first `@@` hunk (git prints every
  # header before the first hunk); inside a hunk it is an added line that begins `++`
  # (cross-vendor round-5 catch: an added content line `++ /dev/null` used to be mistaken
  # for a header, blanking the path so the secret on the next line went unscanned).
  printf '%s\n' "$diff" | awk -v exempt="$EXEMPT_RE" '
    /^diff --git /        { path = ""; inhunk = 0; next }
    /^@@ /                { inhunk = 1; next }
    /^\+\+\+ / && !inhunk { p = $0; sub(/^\+\+\+ "?b\//, "", p); sub(/"$/, "", p); path = (p == "/dev/null") ? "" : p; next }
    /^\+/                 { if (!inhunk || path == "" || path ~ exempt) next; print path "\t" substr($0, 2) }
  ' > "$rows_f"
  # KNOWN-OWN-LINES (v3.0-109) for the scanner at its canonical path: keep only
  # added lines NOT present verbatim in the running script; the residual lines
  # are scanned normally with everything else.
  own=$(cut -f1 "$rows_f" | sort -u | grep -E -e "$OWN_PATH_RE" || true)
  if [ -n "$own" ]; then
    while IFS= read -r p; do
      [ -z "$p" ] && continue
      awk -F'\t' -v P="$p" '$1 == P' "$rows_f" | cut -f2- | grep -Fxv -f "$0" \
        | awk -v P="$p" '{ print P "\t" $0 }' > "$rows_f.own" || true
      awk -F'\t' -v P="$p" '$1 != P' "$rows_f" > "$rows_f.rest"
      cat "$rows_f.rest" "$rows_f.own" > "$rows_f"
    done <<EOF
$own
EOF
  fi
  cut -f1 "$rows_f" > "$paths_f"
  cut -f2- "$rows_f" > "$lines_f"
  if [ ! -s "$lines_f" ]; then _cleanup; return 0; fi
  for entry in "${CONTENT_PATTERNS[@]}"; do
    label="${entry%%|*}"
    pat="${entry#*|}"
    # -e guards patterns that BEGIN with '-' (the PEM block rule) from being
    # parsed as grep options -- caught by this battery's own first run.
    # EVERY match is judged, not just the first: `-o` emits one line per match
    # (`<line-no>:<region>`), and a placeholder-shaped region is dropped by the
    # second grep -- so a doc's redacted example can never shadow a later REAL
    # value of the same class (cross-vendor review catch, 2026-08-11).
    hit=$(grep -Eon -e "$pat" "$lines_f" | grep -Ev -e "^[0-9]+:.*($PLACEHOLDER_RE)" | head -n 1 || true)
    [ -z "$hit" ] && continue
    ln="${hit%%:*}"
    p=$(sed -n "${ln}p" "$paths_f")
    # v3.0-123 (v3.0.55): the SAME-FILE SIBLING rule. A flagged value usually lives in
    # the file again in shapes no pattern names (`const password = "<the same literal>"`
    # beside the flagged URL). An operator who rules "redact" on the flagged line
    # believes the file is clean; the siblings ride in. So the refusal names every
    # line of the staged file that carries the flagged VALUE, and one ruling covers
    # them all. The value is the matched region (for a credential URL, its password);
    # short values (< 8 chars) are not searched -- too many honest collisions.
    region="${hit#*:}"
    # The value VERBATIM (firewall round 1, 2026-09-27, REVISED: stripping punctuation
    # shrank `!abcdefg!` under the 8-character floor and skipped it). A credential URL's
    # password is everything between the first `:` after the user and the `@`; every
    # other class's pattern captures exactly ONE leading boundary character (its
    # `(^|[^...])` group), which is dropped -- nothing trailing is touched.
    case "$label" in
      'embedded credential URL')
        secret=$(printf '%s' "$region" | sed -E 's#^[a-z][a-z0-9+.-]*://[^/:@[:space:]]+:([^@[:space:]]+)@.*$#\1#') ;;
      *)
        # one leading AND one trailing boundary character (the AWS class captures both);
        # exactly one at each end, never a run -- a token value is alphanumeric by its
        # own pattern, so this is exact (firewall round 2 board catch, 2026-09-27)
        secret=$(printf '%s' "$region" | sed -E 's/^[^A-Za-z0-9_-]//; s/[^A-Za-z0-9_.=-]$//') ;;
    esac
    sib_note=''
    if [ "${#secret}" -ge 8 ]; then
      sib=$(git -C "$repo" show ":$p" 2>/dev/null | grep -nF -- "$secret" | cut -d: -f1 | tr '\n' ' ' | sed -E 's/ $//')
      case "$sib" in *' '*) sib_note=" The SAME value also appears in '$p' at staged line(s) $sib (v3.0-123: shapes no pattern names -- one ruling covers every occurrence; redact them all, not only the flagged line)." ;; esac
    fi
    _fail "staged change in '$p' matches secret pattern '$label'.${sib_note} Remove the value (repo-committed config points at the vault by NAME, never by value), re-stage, and commit again."
  done
  _cleanup
  if [ "$n_paths" -ge "$PROGRESS_FROM" ]; then
    echo "scan-staged-secrets: clean -- $n_paths file(s) in $(( $(date +%s) - t0 ))s" >&2
  fi
  return 0
}

# ------------------------------------------------------------------- self-test
self_test() {
  local total=0 failed=0 T repo out rc

  case_() {
    total=$((total+1))
    if [ "$2" = "ok" ]; then echo "  ok  $1"; else echo "  XX  $1  << $3"; failed=$((failed+1)); fi
  }

  mkrepo() {
    repo=$(mktemp -d)
    git -C "$repo" init -q
    git -C "$repo" config user.email t@t
    git -C "$repo" config user.name t
    git -C "$repo" config core.autocrlf false   # fixture repos: no CRLF noise
  }

  stage() { # $1 rel path, $2 content
    mkdir -p "$repo/$(dirname "$1")"
    printf '%s\n' "$2" > "$repo/$1"
    git -C "$repo" add "$1"
  }

  expect() { # $1 name, $2 want_rc (0 pass / 1 block)
    out=$( (scan_repo "$repo") 2>&1 ); rc=$?
    if [ "$rc" -eq "$2" ]; then case_ "$1" ok; else case_ "$1" XX "rc=$rc want=$2 :: $out"; fi
    git -C "$repo" reset -q 2>/dev/null || true
    rm -rf "$repo" 2>/dev/null || true
  }
  expect_out() { # $1 name, $2 want_rc, $3 substring the refusal must carry, $4 substring it must NOT
    out=$( (scan_repo "$repo") 2>&1 ); rc=$?
    if [ "$rc" -eq "$2" ] && printf '%s' "$out" | grep -qF -- "$3" && ! { [ -n "${4:-}" ] && printf '%s' "$out" | grep -qF -- "$4"; }; then
      case_ "$1" ok; else case_ "$1" XX "rc=$rc want=$2 :: $out"; fi
    git -C "$repo" reset -q 2>/dev/null || true
    rm -rf "$repo" 2>/dev/null || true
  }

  # BLOCK direction -- one per content class (values generated here, never committed)
  mkrepo; stage "a.txt" "-----BEGIN RSA PRIVATE KEY-----";                                 expect "PEM private-key block blocks" 1
  mkrepo; stage "a.txt" "PuTTY-User-Key-File-3: ssh-rsa";                                  expect "PuTTY PPK header blocks" 1
  mkrepo; stage "a.txt" "key = AKIA$(printf 'ABCDEFGHIJKLMNOP')";                          expect "AWS AKIA token blocks" 1
  mkrepo; stage "a.txt" "tok: ghp_$(printf 'a%.0s' $(seq 1 36))";                          expect "GitHub ghp_ token blocks" 1
  mkrepo; stage "a.txt" "k=sk-ant-$(printf 'b%.0s' $(seq 1 24))";                          expect "Anthropic key blocks" 1
  mkrepo; stage "a.txt" "k=sk-$(printf 'c%.0s' $(seq 1 40))";                              expect "OpenAI-style key blocks" 1
  mkrepo; stage "a.txt" "s=xoxb-1234567890-abcdefghij";                                    expect "Slack token blocks" 1
  mkrepo; stage "a.txt" "s=sk_live_$(printf 'd%.0s' $(seq 1 20))";                         expect "Stripe live key blocks" 1
  mkrepo; stage "a.txt" "g=AIza$(printf 'E%.0s' $(seq 1 35))";                             expect "Google API key blocks" 1
  # v3.0-197 (v3.0.56): Google OAuth's own value shapes
  mkrepo; stage "a.txt" "\"client_secret\": \"GOCSPX-$(printf 'k%.0s' $(seq 1 28))\"";   expect "v3.0-197: Google OAuth client secret (GOCSPX-) blocks" 1
  mkrepo; stage "a.txt" "\"token\": \"ya29.$(printf 'm%.0s' $(seq 1 60))\"";              expect "v3.0-197: Google OAuth access token (ya29.) blocks" 1
  mkrepo; stage "a.txt" "\"refresh_token\": \"1//0$(printf 'n%.0s' $(seq 1 60))\"";      expect "v3.0-197: Google OAuth refresh token (1//0) blocks" 1
  mkrepo; stage "client_secret.example.json" "{\"client_secret\": \"GOCSPX-$(printf 'k%.0s' $(seq 1 28))\"}"; expect "v3.0-197: an example-NAMED file carrying a REAL client secret still blocks (content rule)" 1
  mkrepo; stage "client_secret.example.json" "{\"client_secret\": \"GOCSPX-""XXXXXXXXXXXXXXXXXXXXXXXXXXXX\"}"; expect "v3.0-197: an example file with a placeholder secret passes" 0
  mkrepo; stage "docs/oauth.md" "the refresh token looks like 1//0... and the access token like ya29.<value>"; expect "v3.0-197: prose naming the Google prefixes passes" 0
  # firewall round 1 (2026-09-28): the boundary/format matrix -- realistic mixed-character
  # shapes (assembled at run time from fragments; never literal in this file) in the places
  # a value actually lands
  _gcs_real="GOCSPX-""4bC_d9Ef-Gh1Jk2Lm3No4Pq5Rs6T"; _gat_real="ya29.""a0AfB_byC2dE3fG4hI5jK6lM7nO8pQ9rS0tU1vW2xY3zA4-bC5dE6fG7hI8"
  _grt_real="1//0""gLpQ7rS8tU9vW0xY1zA2bC3dE4fG5hI6jK7lM8nO9pQ0rS1tU2vW3xY4zA5-bC6_dE7"
  for _case in "slash-before-refresh:path/$_grt_real" "dot-before-access:x.$_gat_real" "bearer-header:Authorization: Bearer $_gat_real" \
               "url-query:https://h.example/cb?refresh_token=$_grt_real&x=1" "json-secret:{\"client_secret\":\"$_gcs_real\"}" \
               "yaml-secret:client_secret: $_gcs_real" "env-style:GOOGLE_REFRESH_TOKEN=$_grt_real" "python-dict:{'token': '$_gat_real'}"; do
    mkrepo; stage "src/m.txt" "${_case#*:}"; expect "r1 matrix: ${_case%%:*} blocks" 1
  done
  mkrepo; stage "docs/n.md" "prefix GOCSPX- alone, ya29. alone, 1//0 alone, and a short ya29.abc"; expect "r1 matrix: bare prefixes and short tails pass" 0
  mkrepo; stage "a.txt" "j=eyJ$(printf 'f%.0s' $(seq 1 12)).$(printf 'g%.0s' $(seq 1 12)).$(printf 'h%.0s' $(seq 1 12))" ; expect "three-segment JWT blocks" 1
  mkrepo; stage "a.txt" "url=https://svc:hunter2pass@db.example.com/x";                    expect "embedded-credential URL blocks" 1
  # v3.0-123 (v3.0.55): same-file siblings of the flagged VALUE are named in the refusal
  mkrepo; stage "src/qa.ts" "$(printf '%s\n' 'const url = "postgres://scheduler:QaOnlyPassword-12345@localhost/db";' 'export const password = "QaOnlyPassword-12345";' 'const other = "unrelated";' 'process.env.PW = "QaOnlyPassword-12345"')"
  expect_out "v3.0-123: a credential URL's password is named at its sibling lines (2 and 4) in the same staged file" 1 "staged line(s) 1 2 4"
  mkrepo; stage "a.txt" "url=https://svc:hunter2pass@db.example.com/x";                    expect_out "v3.0-123: a value with NO sibling carries no sibling note" 1 "matches secret pattern" "SAME value also appears"
  mkrepo; stage "a.txt" "k=sk-ant-$(printf 'b%.0s' $(seq 1 24))
again: sk-ant-$(printf 'b%.0s' $(seq 1 24))";                                              expect_out "v3.0-123: a key class names its repeat too (region is the value)" 1 "staged line(s) 1 2"
  # firewall round 1 (2026-09-27): a punctuation-bounded password keeps its full length
  mkrepo; stage "src/qa.ts" "$(printf '%s\n' 'const url = "postgres://svc:!abcdefg!@localhost/db";' 'export const password = "!abcdefg!";')"
  expect_out "v3.0-123 fold: a 9-char password bounded by punctuation is named at its sibling (not shrunk under the floor)" 1 "staged line(s) 1 2"
  mkrepo; stage "src/qa.ts" "$(printf '%s\n' 'const url = "postgres://svc:ab!cd@localhost/db";' 'const other = "ab!cd";')"
  expect_out "v3.0-123 fold: a 5-char password blocks (URL class) but is NOT sibling-searched (under the floor)" 1 "matches secret pattern" "SAME value also appears"
  # firewall round 2 (2026-09-27): every token class -- the value is the matched region minus
  # its one leading boundary character, so a punctuation-bounded repeat is still found
  _v_aws="AKIA$(printf 'ABCDEFGHIJKLMNOP')"; _v_gh="ghp_$(printf 'a%.0s' $(seq 1 36))"; _v_ant="sk-ant-$(printf 'b%.0s' $(seq 1 24))"
  _v_slack="xoxb-1234567890-abcdefghij"; _v_stripe="sk_live_$(printf 'd%.0s' $(seq 1 20))"; _v_goog="AIza$(printf 'E%.0s' $(seq 1 35))"
  _v_jwt="eyJ$(printf 'f%.0s' $(seq 1 12)).$(printf 'g%.0s' $(seq 1 12)).$(printf 'h%.0s' $(seq 1 12))"
  _v_oai="sk-$(printf 'c%.0s' $(seq 1 40))"; _v_ghfg="github_pat_$(printf 'g%.0s' $(seq 1 60))"
  _v_gcs="GOCSPX-$(printf 'k%.0s' $(seq 1 28))"; _v_gat="ya29.$(printf 'm%.0s' $(seq 1 60))"; _v_grt="1//0$(printf 'n%.0s' $(seq 1 60))"
  for _cls in "AWS:$_v_aws" "GitHub:$_v_gh" "GitHub-fine-grained:$_v_ghfg" "Anthropic:$_v_ant" "OpenAI-style:$_v_oai" "Slack:$_v_slack" "Stripe:$_v_stripe" "Google:$_v_goog" "JWT:$_v_jwt" "Google-OAuth-client-secret:$_v_gcs" "Google-OAuth-access-token:$_v_gat" "Google-OAuth-refresh-token:$_v_grt"; do
    _name="${_cls%%:*}"; _val="${_cls#*:}"
    mkrepo; stage "src/c.txt" "$(printf '%s\n' "key=(\"$_val\")" "again: [$_val];" "third \"$_val\",")"
    expect_out "v3.0-123 round 2: $_name value bounded by punctuation on every line is named at lines 1 2 3" 1 "staged line(s) 1 2 3"
    # round 3: at the START of a line (no leading boundary character exists) and at the END
    mkrepo; stage "src/d.txt" "$(printf '%s\n' "$_val" "x=$_val" "($_val)")"
    expect_out "v3.0-123 round 3: $_name bare at line start, bare at line end, and parenthesized -> lines 1 2 3" 1 "staged line(s) 1 2 3"
  done
  mkrepo; stage ".env" "SECRET=1";                                                        expect "staged .env blocks by path" 1
  mkrepo; stage "keys/deploy.pem" "not even a key";                                       expect "staged *.pem blocks by path" 1
  mkrepo; stage "conf/credentials.json" "{}";                                             expect "staged credentials.json blocks by path" 1
  # v3.0-197 (v3.0.56): the Google client-library files and the widened key-material names
  mkrepo; stage "connectors/gmail/token.json" "{}";                                       expect "v3.0-197: staged token.json blocks by path" 1
  mkrepo; stage "secrets/token.pickle" "x";                                               expect "v3.0-197: staged token.pickle blocks by path" 1
  # the Google download name is assembled from fragments: a literal client-ID shape made GitHub push
  # protection refuse the public mirror (v3.0.56.1); the scanner sees the same staged path either way
  mkrepo; stage "client_secret_1234-abcd.apps.google""usercontent.com.json" "{}";           expect "v3.0-197: Google's downloaded client_secret_*.json blocks by path" 1
  mkrepo; stage "keys/service-account.json" "{}";                                        expect "v3.0-197: service-account.json blocks by path" 1
  mkrepo; stage "serviceAccountKey.json" "{}";                                           expect "v3.0-197: Firebase's serviceAccountKey.json blocks (case-insensitive)" 1
  mkrepo; stage "conf/Credentials.JSON" "{}";                                            expect "v3.0-197: credentials.json blocks in any letter case" 1
  mkrepo; stage "legacy.p12" "x";                                                        expect "v3.0-197: *.p12 key material blocks" 1
  mkrepo; stage "certs/site.PEM" "x";                                                    expect "v3.0-197: *.PEM blocks (key material is case-insensitive now)" 1
  mkrepo; stage "certs/server.example.pem" "x";                                          expect "v3.0-197: key material has no example exemption" 1
  mkrepo; stage "client_secret.example.json" "{}";                                       expect "v3.0-197: an .example. copy of a client_secret name passes" 0
  mkrepo; stage "service-account.sample.json" "{}";                                      expect "v3.0-197: a .sample. copy of a service-account name passes" 0
  mkrepo; stage "tokens.json" "{}";                                                      expect "v3.0-197: design tokens (tokens.json) are not the class" 0
  mkrepo; stage ".env.example.json" "x";                                                 expect "v3.0-197: the new template exemption never reaches the .env rule" 1
  mkrepo; stage ".ENV" "SECRET=1";                                                         expect "v3.0.56: .ENV blocks (case-insensitive .env rule)" 1
  mkrepo; stage "conf/.Env.Local" "SECRET=1";                                              expect "v3.0.56: .Env.Local blocks" 1
  mkrepo; stage ".ENV.EXAMPLE" "SECRET=";                                                  expect "v3.0.56: .ENV.EXAMPLE passes (the exemption is case-insensitive too)" 0
  mkrepo; stage "x/core/security/hooks/test-inputs/.env" "SECRET=1";                        expect "v3.0.56 r4: a NESTED lookalike of the fixture dir is not exempt (path class)" 1
  mkrepo; stage "x/core/security/hooks/test-inputs/a.txt" "-----BEGIN RSA PRIVATE KEY-----"; expect "v3.0.56 r4: a NESTED lookalike of the fixture dir is not exempt (content class)" 1

  # PASS direction -- placeholders, exemptions, ordinary content
  mkrepo; stage "doc.md" "set ANTHROPIC_API_KEY (an sk-ant-... value) in your vault";      expect "prose naming a prefix without a value passes" 0
  mkrepo; stage "doc.md" "example: sk-ant-REDACTEDREDACTEDREDACTED";                       expect "REDACTED placeholder passes" 0
  mkrepo; stage "doc.md" "token: ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx";               expect "xxxx placeholder passes" 0
  mkrepo; stage "doc.md" "url=https://user:<password>@host.example.com/";                 expect "angle-bracket placeholder URL passes" 0
  LB='{'; RB='}'  # built at run time: a literal double-brace in this file would
                  # read as an unresolved substitution to init-validate's scan
  mkrepo; stage "doc.md" "url=https://user:${LB}${LB}db_password${RB}${RB}@host/";         expect "template placeholder URL passes" 0
  mkrepo; stage ".env.example" "SECRET=fill-me-in";                                       expect ".env.example passes (parity with block-env-writes)" 0
  mkrepo; stage ".env.sample" "SECRET=";                                                  expect ".env.sample passes" 0
  mkrepo; stage "core/security/hooks/test-inputs/fx.txt" "-----BEGIN RSA PRIVATE KEY-----"; expect "perimeter fixture dir is exempt (hard-coded)" 0
  mkrepo; stage "src/app.py" "def main():  # reads key by NAME from the vault";           expect "ordinary code passes" 0
  mkrepo; stage "docs/signing.key" "even an empty-looking key file blocks";               expect "a *.key path blocks wherever it sits" 1
  mkrepo; stage "docs/hookskey.md" "the monkey.key naming precedent in prose";            expect "a .md merely MENTIONING a .key name passes" 0
  mkrepo; stage "core/security/credential-bindings.yaml" "replicate: REPLICATE_API_TOKEN"; expect "credential-bindings.yaml passes (destinations, never values)" 0

  # Same bytes OUTSIDE the exempt path block (the exemption is the path, not the bytes)
  mkrepo; stage "elsewhere/fx.txt" "-----BEGIN RSA PRIVATE KEY-----";                      expect "fixture bytes outside the exempt dir still block" 1

  # v3.0-104 + v3.0-109: the KNOWN-OWN-LINES rule at the canonical path. The
  # verifier's acceptance criterion made executable: adoption passes; every
  # agent-reachable tamper-and-commit path for this one file blocks.
  mkrepo; mkdir -p "$repo/core/security/hooks"; cp "$0" "$repo/core/security/hooks/scan-staged-secrets.sh"
  git -C "$repo" add core/security/hooks/scan-staged-secrets.sh;                          expect "adoption: the scanner's own real bytes at the canonical path pass" 0
  mkrepo; mkdir -p "$repo/core/security/hooks"; cp "$0" "$repo/core/security/hooks/scan-staged-secrets.sh"
  printf 'k=sk-ant-%s\n' "$(printf 'z%.0s' $(seq 1 24))" >> "$repo/core/security/hooks/scan-staged-secrets.sh"
  git -C "$repo" add core/security/hooks/scan-staged-secrets.sh;                          expect "tamper: own bytes PLUS an appended secret line block (append AND delete/re-add shapes)" 1
  mkrepo; stage "core/security/hooks/scan-staged-secrets.sh" "PAT='-----BEGIN RSA PRIVATE KEY-----'"; expect "a foreign pattern-shaped line alone at the canonical path blocks (no free pass from path)" 1
  mkrepo; stage "tools/scan-staged-secrets.sh" "PAT='-----BEGIN RSA PRIVATE KEY-----'";    expect "scanner-named file OUTSIDE the canonical path still blocks" 1

  # v3.0-110 regression pins: MODIFIED tracked files are scanned (the original
  # ACR enumeration never saw them -- both directions)
  mkrepo; stage "doc.md" "hello, nothing secret"
  git -C "$repo" -c core.hooksPath=/dev/null commit -q -m base >/dev/null 2>&1
  printf 'tok: ghp_%s\n' "$(printf 'a%.0s' $(seq 1 36))" >> "$repo/doc.md"
  git -C "$repo" add doc.md;                                                              expect "a secret ADDED TO AN EXISTING tracked file blocks (v3.0-110)" 1
  mkrepo; stage "doc.md" "hello"
  git -C "$repo" -c core.hooksPath=/dev/null commit -q -m base >/dev/null 2>&1
  printf 'more ordinary prose\n' >> "$repo/doc.md"
  git -C "$repo" add doc.md;                                                              expect "an ordinary modification to a tracked file passes" 0

  # (Counting note, v3.0.54: these END-TO-END blocks used to bump `total` themselves
  # and then call case_, which bumps it again -- the summary over-counted by six
  # from v3.0.41 to v3.0.53; caught by the v3.0.54 cross-vendor review.)
  # v3.0-109 round-2 operational evidence (cross-vendor demand, 2026-08-17):
  # END-TO-END through a real `git commit` with THIS scanner installed as the
  # repo's .git/hooks/pre-commit -- not a direct scan_repo call. Covers the
  # adoption commit, the append-tamper commit, and the delete-then-re-add
  # commit sequence as three real hook-mediated commits.
  e2e_repo=$(mktemp -d)
  git -C "$e2e_repo" init -q
  git -C "$e2e_repo" config user.email t@t; git -C "$e2e_repo" config user.name t
  git -C "$e2e_repo" config core.autocrlf false
  mkdir -p "$e2e_repo/core/security/hooks"
  cp "$0" "$e2e_repo/core/security/hooks/scan-staged-secrets.sh"
  cp "$0" "$e2e_repo/.git/hooks/pre-commit"; chmod +x "$e2e_repo/.git/hooks/pre-commit"
  git -C "$e2e_repo" add core/security/hooks/scan-staged-secrets.sh
  if git -C "$e2e_repo" commit -q -m adoption >/dev/null 2>&1; then
    case_ "E2E: real hook-mediated ADOPTION commit of own file succeeds" ok
  else
    case_ "E2E: real hook-mediated ADOPTION commit of own file succeeds" XX "commit refused"
  fi
  printf 'k=sk-ant-%s\n' "$(printf 'q%.0s' $(seq 1 24))" >> "$e2e_repo/core/security/hooks/scan-staged-secrets.sh"
  git -C "$e2e_repo" add core/security/hooks/scan-staged-secrets.sh
  if git -C "$e2e_repo" commit -q -m tamper >/dev/null 2>&1; then
    case_ "E2E: real hook-mediated APPEND-TAMPER commit is refused" XX "commit succeeded"
  else
    case_ "E2E: real hook-mediated APPEND-TAMPER commit is refused" ok
  fi
  # (round-2 verifier catch: after the refused commit the secret stays STAGED,
  # so the naive cleanup left `git rm` failing and the "re-add" case was really
  # a second modified-file block. Restore index+worktree from HEAD, assert the
  # removal commit REALLY lands, assert the re-add is REALLY an A-shape.)
  git -C "$e2e_repo" checkout -q HEAD -- core/security/hooks/scan-staged-secrets.sh
  git -C "$e2e_repo" rm -q core/security/hooks/scan-staged-secrets.sh
  if git -C "$e2e_repo" commit -q -m "remove scanner" >/dev/null 2>&1 \
     && [ "$(git -C "$e2e_repo" rev-list --count HEAD)" = "2" ] \
     && [ -z "$(git -C "$e2e_repo" ls-files core/security/hooks/scan-staged-secrets.sh)" ]; then
    case_ "E2E: hook-mediated REMOVAL commit lands (deletion is not content)" ok
  else
    case_ "E2E: hook-mediated REMOVAL commit lands (deletion is not content)" XX "removal commit did not land cleanly"
  fi
  mkdir -p "$e2e_repo/core/security/hooks"
  { cat "$0"; printf 'k=sk-ant-%s\n' "$(printf 'r%.0s' $(seq 1 24))"; } > "$e2e_repo/core/security/hooks/scan-staged-secrets.sh"
  git -C "$e2e_repo" add core/security/hooks/scan-staged-secrets.sh
  readd_shape=$(git -C "$e2e_repo" diff --cached --name-status --diff-filter=A | grep -c "scan-staged-secrets.sh")
  [ "$readd_shape" = "1" ] && readd_shape="A"
  if [ "$readd_shape" = "A" ] && ! git -C "$e2e_repo" commit -q -m readd >/dev/null 2>&1; then
    case_ "E2E: real hook-mediated DELETE-THEN-RE-ADD (verified A-shape) with embedded secret is refused" ok
  else
    case_ "E2E: real hook-mediated DELETE-THEN-RE-ADD (verified A-shape) with embedded secret is refused" XX "shape=$readd_shape or commit succeeded"
  fi
  # v3.0-112 rider (2026-08-17 hunt, shape 3 #1): the UPDATE path. MIGRATION's
  # reinstall-BEFORE-commit ordering is load-bearing: with the new bytes already
  # installed as the hook, committing the same new bytes as a MODIFICATION passes
  # (every added line is in $0). The battery pins the flow so a recipe reorder
  # can't silently resurrect the update self-block.
  git -C "$e2e_repo" checkout -q -- . 2>/dev/null || true
  printf '# a new pattern line: -----BEGIN FAKE UPDATE KEY-----\n' >> "$e2e_repo/.git/hooks/pre-commit"
  cp "$e2e_repo/.git/hooks/pre-commit" "$e2e_repo/notes.md.update-src" 2>/dev/null || true
  rm -f "$e2e_repo/notes.md.update-src"
  # simulate: template shipped an updated scanner (adds a self-tripping line);
  # adopter reinstalls FIRST (hook already updated above), then stages the same
  # updated bytes at the canonical path and commits.
  mkdir -p "$e2e_repo/core/security/hooks"
  cp "$e2e_repo/.git/hooks/pre-commit" "$e2e_repo/core/security/hooks/scan-staged-secrets.sh"
  git -C "$e2e_repo" add core/security/hooks/scan-staged-secrets.sh
  if git -C "$e2e_repo" commit -q -m update >/dev/null 2>&1; then
    case_ "E2E: UPDATE with reinstall-first passes (recipe ordering is load-bearing and pinned)" ok
  else
    case_ "E2E: UPDATE with reinstall-first passes (recipe ordering is load-bearing and pinned)" XX "update commit refused"
  fi

  # v3.0-110 through the REAL hook too: a secret pasted into an ordinary
  # already-tracked file is refused at an actual commit (round-2 demand).
  git -C "$e2e_repo" reset -q HEAD -- core/security/hooks/scan-staged-secrets.sh 2>/dev/null || true
  rm -f "$e2e_repo/core/security/hooks/scan-staged-secrets.sh"
  printf 'notes line one\n' > "$e2e_repo/notes.md"
  git -C "$e2e_repo" add notes.md
  git -C "$e2e_repo" commit -q -m notes >/dev/null 2>&1
  printf 'tok: ghp_%s\n' "$(printf 's%.0s' $(seq 1 36))" >> "$e2e_repo/notes.md"
  git -C "$e2e_repo" add notes.md
  if git -C "$e2e_repo" commit -q -m paste >/dev/null 2>&1; then
    case_ "E2E: secret pasted into an ordinary tracked file refused at a REAL commit (v3.0-110)" XX "commit succeeded"
  else
    case_ "E2E: secret pasted into an ordinary tracked file refused at a REAL commit (v3.0-110)" ok
  fi
  rm -rf "$e2e_repo" 2>/dev/null || true

  # v3.0-164 (fleet inbox #13): a large staged set completes in ONE pass with a
  # progress line -- the birth-commit shape, both directions (a clean set passes
  # and says so; a secret buried in the large set still blocks, naming its file).
  mkrepo; for i in $(seq 1 60); do stage "many/f$i.txt" "ordinary line $i"; done
  out=$( (scan_repo "$repo") 2>&1 ); rc=$?
  if [ "$rc" -eq 0 ] && echo "$out" | grep -q "scanning 60 staged file(s)"; then
    case_ "v3.0-164: 60 clean staged files pass in one pass WITH the progress line" ok
  else
    case_ "v3.0-164: 60 clean staged files pass in one pass WITH the progress line" XX "rc=$rc :: $out"
  fi
  rm -rf "$repo"
  mkrepo; for i in $(seq 1 60); do stage "many/f$i.txt" "ordinary line $i"; done
  stage "many/f37.txt" "tok: ghp_$(printf 'a%.0s' $(seq 1 36))"
  out=$( (scan_repo "$repo") 2>&1 ); rc=$?
  if [ "$rc" -eq 1 ] && echo "$out" | grep -q "many/f37.txt"; then
    case_ "v3.0-164: one secret among 60 staged files still blocks, naming its file" ok
  else
    case_ "v3.0-164: one secret among 60 staged files still blocks, naming its file" XX "rc=$rc :: $out"
  fi
  rm -rf "$repo"

  # v3.0-164 cross-vendor round-2 fold: paths git QUOTES in the diff header (a tab, a
  # non-ASCII byte under the default core.quotePath) are scanned and attributed; the old
  # per-path loop could not address them by their quoted name and skipped them silently.
  # (A tab- or control-character-named fixture cannot exist on NTFS -- git add refuses
  # the pathspec -- so the quoted-header path is exercised through a non-ASCII name,
  # which git quotes by the same mechanism under the default core.quotePath.)
  mkrepo; stage "caf$(printf '\303\251').txt" "k=sk-ant-$(printf 'd%.0s' $(seq 1 24))"
  out=$( (scan_repo "$repo") 2>&1 ); rc=$?
  if [ "$rc" -eq 1 ] && echo "$out" | grep -q "caf"; then
    case_ "v3.0-164: a secret in a git-QUOTED (non-ASCII) file name blocks and is attributed to that file" ok
  else
    case_ "v3.0-164: a secret in a git-QUOTED (non-ASCII) file name blocks and is attributed to that file" XX "rc=$rc :: $out"
  fi
  rm -rf "$repo"
  mkrepo; stage "caf$(printf '\303\251')-clean.txt" "nothing secret here";                expect "a clean git-QUOTED file name passes (no false refusal from the quoted header)" 0
  mkrepo; stage "caf$(printf '\303\251')/.env" "SECRET=1";                                expect "a credential-class path under a git-QUOTED directory blocks (the quote no longer defeats the class anchor)" 1
  mkrepo; stage "caf$(printf '\303\251')/deploy.pem" "not even a key";                     expect "a *.pem under a git-QUOTED directory blocks" 1

  # v3.0-164 cross-vendor round-5 catch: a header-SHAPED added content line must not
  # blank the path for the secret that follows it
  mkrepo; stage "notes.md" "++ /dev/null
k=sk-ant-$(printf 'e%.0s' $(seq 1 24))";                                                  expect "an added line shaped like a diff header (+++ /dev/null) followed by a secret still blocks" 1
  mkrepo; stage "notes.md" "++ b/other.txt
k=sk-ant-$(printf 'f%.0s' $(seq 1 24))";                                                  expect "an added line shaped like a +++ b/ header followed by a secret still blocks (attributed to the real file)" 1

  # v3.0-164 cross-vendor round-1 fold: scratch-file creation failure FAILS CLOSED
  mkrepo; stage "a.txt" "k=sk-ant-$(printf 'b%.0s' $(seq 1 24))"
  out=$( (TMPDIR=/nonexistent/no-such-dir scan_repo "$repo") 2>&1 ); rc=$?
  if [ "$rc" -eq 1 ] && echo "$out" | grep -q "mktemp failed"; then
    case_ "v3.0-164: an unwritable TMPDIR refuses the commit (fail closed), naming the cause" ok
  else
    case_ "v3.0-164: an unwritable TMPDIR refuses the commit (fail closed), naming the cause" XX "rc=$rc :: $out"
  fi
  rm -rf "$repo"
  mkrepo; stage "a.txt" "ordinary"
  out=$( (TMPDIR=/nonexistent/no-such-dir scan_repo "$repo") 2>&1 ); rc=$?
  if [ "$rc" -eq 1 ] && echo "$out" | grep -q "mktemp failed"; then
    case_ "v3.0-164: ...and a clean stage is refused the same way (never passed unscanned)" ok
  else
    case_ "v3.0-164: ...and a clean stage is refused the same way (never passed unscanned)" XX "rc=$rc :: $out"
  fi
  rm -rf "$repo"

  # Cross-vendor review regressions (2026-08-11): a placeholder match must never
  # shadow a later REAL value of the same class in the same file
  mkrepo; stage "doc.md" "example: ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
real:    ghp_$(printf 'a%.0s' $(seq 1 36))";                                              expect "placeholder first, real secret later, SAME class: still blocks" 1
  mkrepo; stage "doc.md" "u1=https://user:<password>@h1.example.com/
u2=https://svc:hunter2pass@h2.example.com/";                                              expect "placeholder URL first, real credential URL later: still blocks" 1

  if [ "$failed" -gt 0 ]; then
    echo "scan-staged-secrets self-test: FAIL ($((total-failed))/$total)"
    return 1
  fi
  echo "scan-staged-secrets self-test: PASS ($total/$total)"
  return 0
}

if [ "${1:-}" = "--self-test" ]; then
  self_test
  exit $?
fi

# Live pre-commit invocation: scan the repo this hook runs in.
scan_repo "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
