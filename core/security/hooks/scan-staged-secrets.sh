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
#      three-segment JWTs
#   3. embedded-credential URLs -- scheme://user:password@host, password not a
#      named placeholder shape
#   4. credential FILES by staged path -- .env* (except .env.example/.env.sample,
#      byte-parity with block-env-writes.sh), *.pem/*.key/*.ppk, credentials.json.
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
  'JWT|(^|[^A-Za-z0-9._-])eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'
  'embedded credential URL|[a-z][a-z0-9+.-]*://[^/:@[:space:]]+:[^@[:space:]]{3,}@[A-Za-z0-9.-]+'
)

# A matched VALUE whose bytes carry a named placeholder shape passes. Checked
# against the matched region only, never the whole line (a real key on a line
# that also says "example usage" must still block).
PLACEHOLDER_RE='EXAMPLE|REDACTED|PLACEHOLDER|CHANGE[-_]?ME|your-[a-z0-9-]+-here|<[A-Za-z][A-Za-z0-9 _-]*>|\{\{[^}]+\}\}|[Xx]{6,}|\.\.\.'

# Staged PATHS that are credential homes. Basename-matched, extended regex.
PATH_BLOCK_RE='(^|/)(\.env(\..*)?|[^/]*\.(pem|ppk)|credentials\.json)$'
PATH_KEY_RE='(^|/)[^/]*\.key$'
PATH_ALLOW_RE='(^|/)\.env\.(example|sample)$'
# The perimeter's own fixture dir -- hard-coded, never configurable.
EXEMPT_RE='(^|/)core/security/hooks/test-inputs/'
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

_fail() {
  echo "COMMIT BLOCKED by scan-staged-secrets.sh: $1" >&2
  echo "  Secrets never enter git history: once pushed, a leaked value is public even if deleted later (core/security/CREDENTIALS.md -- values live in the OS vault, never the repo)." >&2
  echo "  If this is a FALSE POSITIVE: the operator (never an agent -- mechanically barred) may bypass ONCE with 'git commit --no-verify', and should report the pattern so it learns." >&2
  exit 1
}

scan_repo() {
  # $1 = repo dir. Returns 0 clean, 1 blocked (message on stderr).
  local repo="$1"
  local paths added line label pat region

  # v3.0-110: ACMR, not ACR -- the original perimeter enumerated Added/Copied/
  # Renamed only, so a secret pasted into an already-tracked file was NEVER
  # scanned (caught 2026-08-17 by the v3.0-109 fix's end-to-end evidence: the
  # real hook-mediated append-tamper commit passed while every direct-call test
  # staged new files). Class 4 (credential files by path) also wants M: a
  # tracked file RENAMED onto a credential name arrives as R, but content
  # landing in an existing .env-class path must block too.
  paths=$(git -C "$repo" diff --cached --name-only --diff-filter=ACMR 2>/dev/null) || return 0

  # ---- class 4: credential files by path --------------------------------------
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    echo "$p" | grep -Eq "$EXEMPT_RE" && continue
    echo "$p" | grep -Eq "$PATH_ALLOW_RE" && continue
    if echo "$p" | grep -Eq "$PATH_BLOCK_RE" || echo "$p" | grep -Eq "$PATH_KEY_RE"; then
      _fail "staged file '$p' is a credential-file class (.env*/key material/credentials.json). Unstage it (git restore --staged '$p'); .env.example/.env.sample are exempt."
    fi
  done <<EOF
$paths
EOF

  # ---- classes 1-3: added lines, per staged file (exempt dir skipped) ---------
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    echo "$p" | grep -Eq "$EXEMPT_RE" && continue
    added=$(git -C "$repo" diff --cached -U0 -- "$p" 2>/dev/null | grep -E '^\+' | grep -Ev '^\+\+\+' | cut -c2-)
    [ -z "$added" ] && continue
    if echo "$p" | grep -Eq "$OWN_PATH_RE"; then
      # KNOWN-OWN-LINES (v3.0-109): keep only added lines NOT present verbatim
      # in the running script; those residual lines are scanned normally.
      added=$(echo "$added" | grep -Fxv -f "$0" || true)
      [ -z "$added" ] && continue
    fi
    for entry in "${CONTENT_PATTERNS[@]}"; do
      label="${entry%%|*}"
      pat="${entry#*|}"
      # -e guards patterns that BEGIN with '-' (the PEM block rule) from being
      # parsed as grep options -- caught by this battery's own first run.
      # EVERY match is judged, not just the first: a placeholder-shaped match
      # must never shadow a later REAL value of the same class in the same file
      # (cross-vendor review catch, 2026-08-11 -- the first draft checked only
      # the first match and a doc's redacted example would have masked a live
      # key below it).
      regions=$(echo "$added" | grep -Eo -e "$pat" || true)
      [ -z "$regions" ] && continue
      while IFS= read -r region; do
        [ -z "$region" ] && continue
        if echo "$region" | grep -Eq -e "$PLACEHOLDER_RE"; then
          continue  # named placeholder shape -- a redacted example, not a value
        fi
        _fail "staged change in '$p' matches secret pattern '$label'. Remove the value (repo-committed config points at the vault by NAME, never by value), re-stage, and commit again."
      done <<INNEREOF
$regions
INNEREOF
    done
  done <<EOF
$paths
EOF
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
  mkrepo; stage "a.txt" "j=eyJ$(printf 'f%.0s' $(seq 1 12)).$(printf 'g%.0s' $(seq 1 12)).$(printf 'h%.0s' $(seq 1 12))" ; expect "three-segment JWT blocks" 1
  mkrepo; stage "a.txt" "url=https://svc:hunter2pass@db.example.com/x";                    expect "embedded-credential URL blocks" 1
  mkrepo; stage ".env" "SECRET=1";                                                        expect "staged .env blocks by path" 1
  mkrepo; stage "keys/deploy.pem" "not even a key";                                       expect "staged *.pem blocks by path" 1
  mkrepo; stage "conf/credentials.json" "{}";                                             expect "staged credentials.json blocks by path" 1

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
  total=$((total+1))
  if git -C "$e2e_repo" commit -q -m adoption >/dev/null 2>&1; then
    case_ "E2E: real hook-mediated ADOPTION commit of own file succeeds" ok
  else
    case_ "E2E: real hook-mediated ADOPTION commit of own file succeeds" XX "commit refused"
  fi
  printf 'k=sk-ant-%s\n' "$(printf 'q%.0s' $(seq 1 24))" >> "$e2e_repo/core/security/hooks/scan-staged-secrets.sh"
  git -C "$e2e_repo" add core/security/hooks/scan-staged-secrets.sh
  total=$((total+1))
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
  total=$((total+1))
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
  total=$((total+1))
  readd_shape=$(git -C "$e2e_repo" diff --cached --name-status --diff-filter=A | grep -c "scan-staged-secrets.sh")
  [ "$readd_shape" = "1" ] && readd_shape="A"
  if [ "$readd_shape" = "A" ] && ! git -C "$e2e_repo" commit -q -m readd >/dev/null 2>&1; then
    case_ "E2E: real hook-mediated DELETE-THEN-RE-ADD (verified A-shape) with embedded secret is refused" ok
  else
    case_ "E2E: real hook-mediated DELETE-THEN-RE-ADD (verified A-shape) with embedded secret is refused" XX "shape=$readd_shape or commit succeeded"
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
  total=$((total+1))
  if git -C "$e2e_repo" commit -q -m paste >/dev/null 2>&1; then
    case_ "E2E: secret pasted into an ordinary tracked file refused at a REAL commit (v3.0-110)" XX "commit succeeded"
  else
    case_ "E2E: secret pasted into an ordinary tracked file refused at a REAL commit (v3.0-110)" ok
  fi
  rm -rf "$e2e_repo" 2>/dev/null || true

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
