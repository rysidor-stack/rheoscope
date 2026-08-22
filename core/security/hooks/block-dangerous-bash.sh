#!/usr/bin/env bash
# Extracted and generalized from a production project.
# Project-neutral - no substitutions needed.
#
# TWO TIERS (v3.0.33, backlog v3.0-95 -- operator-approved redesign 2026-08-09).
# The v3.0.19 deny-list doctrine, applied to the hook layer: deny ONLY the
# unrecoverable; everything else that needs a human eye ASKS for one.
#   DENY (exit 2): destructive commands -- rm -rf /, git reset --hard, and the
#     root-targeting Remove-Item analog. Unrecoverable; there is no legitimate
#     unattended "yes", so this tier has no allowlist and no ask.
#   ASK (exit 0 + PreToolUse "ask" JSON): network egress -- the named tools,
#     the PowerShell egress cmdlets/aliases, and inline interpreter one-liners.
#     Egress is reviewable-before-run: the operator sees the exact command and
#     approves or declines that one call. In an unattended session nobody
#     answers, so an unanswered ask FAILS CLOSED -- the perimeter holds exactly
#     where prompt-injection risk is highest. The old behavior (hard deny) sent
#     authorized work into "disable the hook and restart", which drops the
#     whole perimeter to make one call.
# STANDING ALLOWANCES: egress-allowlist.txt beside this script, one extended
# regex per line (comments #, blanks ignored). A command matching a line is
# allowed silently -- "yes once per destination", recorded in a reviewable
# file. OPERATOR-EDITED ONLY (same doctrine as credential-bindings.yaml):
# a session proposes a line in chat; the operator commits it. The path is
# deliberately fixed relative to this script -- no env override, because an
# env-settable path would let a session point the hook at its own permissive
# file. Consulted by the ASK tier only; the DENY tier ignores it.
set -euo pipefail

# --------------------------------------------------------------- SELF-TEST
# `bash block-dangerous-bash.sh --self-test` (v3.0.43, the cross-vendor leg's
# round-7 demand: the fixtures were committed but their DRIVER and expectations
# lived only in README prose, so no one could run the board in one command the
# way `scan-staged-secrets.sh --self-test` already allowed). Two phases: an
# embedded case table (self-contained, survives fixture-dir loss) and a pass
# over every committed fixture against its pinned expectation. Intercepted
# BEFORE the stdin read, so the PreToolUse path is byte-unaffected.
if [ "${1:-}" = "--self-test" ]; then
  SELF_PATH="${BASH_SOURCE[0]}"
  SELF_DIR=$(cd "$(dirname "$SELF_PATH")" && pwd)
  pass=0; fail=0
  # rc is captured on the line AFTER the assignment and NOTHING may intervene --
  # an intervening command clobbers $? (this bit the v3.0.43 evidence script).
  run_case() {
    expect="$1"; cmd="$2"; label="$3"
    json=$(jq -n --arg c "$cmd" '{tool_input:{command:$c}}')
    set +e
    out=$(printf '%s' "$json" | bash "$SELF_PATH" 2>/dev/null)
    rc=$?
    set -e
    kind=silent
    [ "$rc" -eq 2 ] && kind=DENY
    [ -n "$out" ] && kind=ASK
    if [ "$kind" = "$expect" ]; then
      pass=$((pass+1))
    else
      fail=$((fail+1))
      echo "FAIL [$label] expected=$expect got=$kind rc=$rc" >&2
    fi
  }

  # -- DENY tier (destructive + agent commit-bypass, incl. global-option/quoted)
  run_case DENY 'rm -rf /' 'rm-rf-root'
  run_case DENY 'git reset --hard origin/main' 'git-reset-hard'
  run_case DENY 'git commit --no-verify -m x' 'commit-no-verify'
  run_case DENY 'git commit -n -m x' 'commit-n-alias'
  run_case DENY 'git -C /repo commit --no-verify -m x' 'commit-global-option'
  run_case DENY 'git -c user.name=x commit -n -m y' 'commit-global-config'
  run_case DENY "git 'commit' --no-verify -m x" 'commit-quoted'
  run_case DENY 'Remove-Item -Recurse -Force C:\' 'removeitem-root'
  run_case DENY 'Remove-Item -Recurse -Force /' 'removeitem-posix-root'
  # -- DENY negatives (the words that must NOT match)
  run_case silent 'git commit -m "fix the parser"' 'commit-passing'
  # The DOCUMENTED accepted false positive (v3.0.36 deny-over-ask trade): a commit
  # MESSAGE containing -n trips the bypass pattern. Pinned so the trade stays a
  # deliberate, visible choice rather than a surprise someone "fixes" unknowingly.
  run_case DENY 'git commit -m "fix -n handling"' 'commit-msg-dash-n-accepted-fp'
  run_case silent 'git commit-tree -n abc123' 'commit-tree-passing'
  run_case silent 'git push -n origin main' 'push-dry-run'
  run_case silent 'Remove-Item -Recurse -Force ./build' 'removeitem-relative'
  run_case silent 'Remove-Item ./one-file.txt' 'removeitem-single-file'
  run_case silent 'ls -la && echo done' 'plain-passing'
  # -- ASK tier: named egress tools (unconditional, unchanged by v3.0.43)
  run_case ASK 'curl -s https://api.example.com/v1' 'curl'
  run_case ASK "curl -s 'https://api.example.com/v1'" 'curl-quoted'
  run_case ASK 'wget https://example.com/f.tar' 'wget'
  run_case ASK 'nc -l 4444' 'nc'
  run_case ASK 'netcat host 80' 'netcat'
  run_case ASK 'Invoke-WebRequest -Uri https://example.com' 'ps-iwr-cmdlet'
  run_case ASK 'iwr https://example.com -OutFile f' 'ps-iwr-alias'
  run_case ASK 'Invoke-RestMethod https://example.com/api' 'ps-irm'
  # -- ASK tier: inline interpreters WITH a network token (v3.0.43 fire direction)
  run_case ASK 'python -c "import urllib.request; urllib.request.urlopen(u)"' 'py-urllib'
  run_case ASK 'py -c "import requests; requests.get(u)"' 'py-requests'
  run_case ASK 'py -c "import socket as s; s.create_connection((h,80))"' 'py-socket'
  run_case ASK 'python -c "from http import client; client.HTTPConnection(h)"' 'py-from-http-import'
  run_case ASK 'python -c "from multiprocessing.connection import Client; Client((h,80))"' 'py-mp-connection'
  run_case ASK 'python -c "from asyncio import open_connection; open_connection(h,80)"' 'py-asyncio-open-connection'
  run_case ASK 'py -c "import ssl; ssl.create_default_context()"' 'py-ssl'
  run_case ASK 'python -c "import asyncore; asyncore.loop()"' 'py-asyncore'
  run_case ASK 'node -e "require(\"http\").get(u)"' 'node-http'
  run_case ASK "node -e \"import https from 'node:https'; https.get(u)\"" 'node-node-https'
  run_case ASK "node -e \"import {connect} from 'tls'; connect(443,h)\"" 'node-import-from-tls'
  run_case ASK "node -e \"import dns from 'dns/promises'; dns.resolve(h)\"" 'node-dns-promises'
  run_case ASK 'node -e "fetch(process.argv[1])"' 'node-fetch'
  # -- allow-silent: inline interpreters with NO network token (the v3.0.43 point)
  run_case silent 'py -c "import json; d=json.load(open(\"x.json\")); print(d)"' 'py-local-json'
  run_case silent 'python3 -c "import re,sys; print(len(sys.stdin.read()))"' 'py-local-re-sys'
  run_case silent 'py -c "import struct; print(struct.calcsize(fmt))"' 'py-local-struct'
  run_case silent "node -e \"const fs=require('fs'); console.log(fs.readFileSync(p).length)\"" 'node-local-fs'
  run_case silent "node -e \"import fs from 'node:fs'; console.log(fs.statSync(p).size)\"" 'node-local-node-fs'
  run_case silent 'python build.py --release' 'py-script-not-inline'
  # -- DENY tier: trust-surface writes (v3.0.46, v3.0-120 brief section 3), per surface
  run_case DENY 'echo "curl .*" >> core/security/hooks/egress-allowlist.txt' 'ts-append-allowlist'
  run_case DENY 'cat /tmp/x > deploy/safe-allowlist.yaml' 'ts-redirect-safe-allowlist'
  run_case DENY 'printf "x" | tee deploy/evidence/operator-grant.md' 'ts-tee-evidence'
  run_case DENY 'cp /tmp/h.sh core/security/hooks/block-env-writes.sh' 'ts-cp-hook'
  run_case DENY 'sed -i "s/sk-/ssh-/" core/security/hooks/allowed_signers' 'ts-sed-i-pin'
  run_case DENY 'perl -i -pe "s/a/b/" core/security/hooks/trust-surfaces.txt' 'ts-perl-i-class'
  run_case DENY 'mv /tmp/t.py deploy/trust.py' 'ts-mv-trust'
  run_case DENY 'rm core/security/hooks/allowed_signers' 'ts-rm-pin'
  run_case DENY 'ln -s /tmp/x deploy/audit-content.py' 'ts-ln-audit'
  run_case DENY 'install -m 644 /tmp/d.py deploy/compile-driver.py' 'ts-install-driver'
  run_case DENY 'truncate -s 0 deploy/compile-backends.py' 'ts-truncate-backends'
  run_case DENY 'echo x > deploy/rulings/retire-1/proposal.md' 'ts-redirect-rulings'
  run_case DENY 'Set-Content -Path deploy/compile-driver.py -Value x' 'ts-ps-setcontent'
  run_case DENY 'Add-Content .claude\settings.json "{}"' 'ts-ps-addcontent-winpath'
  run_case DENY '"x" | Out-File .git\hooks\pre-commit' 'ts-ps-outfile-githook'
  run_case DENY 'Copy-Item C:\tmp\s.json .claude\settings.local.json' 'ts-ps-copyitem-settings'
  run_case DENY 'Move-Item a deploy/safe-allowlist.yaml' 'ts-ps-moveitem'
  run_case DENY 'Remove-Item deploy/evidence/operator-x.md' 'ts-ps-removeitem-single-surface'
  run_case DENY 'New-Item -Path deploy/rulings/retire-2 -ItemType Directory' 'ts-ps-newitem'
  run_case DENY 'Clear-Content core/security/hooks/egress-allowlist.txt' 'ts-ps-clearcontent'
  run_case DENY 'Rename-Item deploy/trust.py trust-old.py' 'ts-ps-renameitem'
  run_case DENY 'sc deploy/safe-allowlist.yaml x' 'ts-ps-sc-alias'
  run_case DENY '[IO.File]::WriteAllText("deploy/trust.py","x")' 'ts-ps-io-file'
  run_case DENY 'python -c "open(\"deploy/safe-allowlist.yaml\",\"w\").write(\"\")"' 'ts-py-c-open-write'
  run_case DENY 'py -c "import os; print(os.stat(\"deploy/trust.py\"))"' 'ts-py-c-mentions-path'
  run_case DENY "node -e \"require('fs').writeFileSync('.claude/settings.json','')\"" 'ts-node-e-write'
  run_case DENY 'echo x > capabilities/knowledge-os/extracted/deploy/trust.py' 'ts-dev-repo-source-path'
  run_case DENY 'echo "" > ./core/security/hooks/egress-allowlist.txt' 'ts-dot-slash'
  run_case DENY 'echo x > "deploy/evidence/operator-new.md"' 'ts-quoted-path'
  run_case DENY 'cat a.txt | tee -a core/security/hooks/test-inputs/new.json' 'ts-tee-fixture'
  # -- DENY negatives: reads and non-class siblings on the same paths must pass
  run_case silent 'cat core/security/hooks/egress-allowlist.txt' 'ts-read-cat'
  run_case silent 'grep -n safe deploy/safe-allowlist.yaml' 'ts-read-grep'
  run_case silent 'ls -la core/security/hooks 2>/dev/null' 'ts-read-ls-stderr-null'
  run_case silent 'cat deploy/trust.py 2>&1 | head' 'ts-read-stderr-to-stdout'
  run_case silent 'diff /tmp/a core/security/hooks/README.md' 'ts-read-diff'
  run_case silent 'git show HEAD:core/security/hooks/allowed_signers' 'ts-read-git-show'
  run_case silent 'git diff HEAD -- deploy/evidence/operator-x.md' 'ts-read-git-diff'
  run_case silent 'git log -1 -- .claude/settings.json' 'ts-read-git-log'
  run_case silent 'sed -n 1,20p deploy/compile-driver.py' 'ts-read-sed-n'
  run_case silent 'python deploy/trust.py --self-test' 'ts-run-sensor-as-script'
  run_case silent 'python deploy/compile-driver.py --run --authorization deploy/evidence/operator-x.md' 'ts-run-driver'
  run_case silent 'bash core/security/hooks/block-dangerous-bash.sh --self-test' 'ts-run-hook-battery'
  run_case silent 'git add core/security/hooks/allowed_signers && git commit -S -m "pin"' 'ts-git-commit-signed'
  run_case silent 'echo x > deploy/safe-allowlist.yaml.example' 'ts-write-example-sibling'
  run_case silent 'cp /tmp/r.md deploy/evidence/README.md' 'ts-write-evidence-readme'
  run_case silent 'echo x > deploy/retire-manifest.py' 'ts-write-deploy-sibling'
  run_case silent 'Get-Content deploy/compile-driver.py' 'ts-ps-getcontent'
  run_case silent 'Set-Content -Path docs/notes.md -Value x' 'ts-ps-setcontent-elsewhere'
  run_case silent 'echo x > wiki/deploy/trust.py.md' 'ts-lookalike-path'
  run_case silent 'cat deploy/trust.py > /dev/null' 'ts-read-to-devnull'
  run_case silent 'type deploy\trust.py > NUL' 'ts-read-to-nul'
  # -- Remove-Item root rule now matched on COMMAND_NORM (v3.0.46 rider)
  run_case DENY "'Remove-Item' -Recurse -Force C:\\" 'removeitem-quoted-cmdlet-root'
  run_case DENY "Remove-Item -Recurse '-Force' 'C:\\'" 'removeitem-quoted-flags-root'

  # -- Phase 2: every committed fixture against its pinned expectation.
  fixture_expect() {
    case "$1" in
      test-rm-rf.json|test-git-reset-hard.json|test-git-commit-no-verify.json|\
test-git-commit-n-alias.json|test-git-C-commit-no-verify.json|\
test-git-config-commit-n-alias.json|test-git-quoted-commit-no-verify.json|\
test-ps-removeitem-root.json|test-ps-removeitem-root-alias.json|\
test-ps-removeitem-root-path-flag.json|test-ps-removeitem-posix-root.json|\
test-ps-removeitem-quoted-cmdlet-root.json|test-trust-bash-redirect-allowlist.json|\
test-trust-bash-tee-evidence.json|test-trust-bash-sed-i-trust.json|\
test-trust-bash-cp-hook.json|test-trust-ps-setcontent-driver.json|\
test-trust-ps-copyitem-settings.json|test-trust-ps-outfile-githook.json|\
test-trust-py-c-open-write-allowlist.json|test-trust-node-e-write-settings.json) echo DENY ;;
      test-a-curl.json|test-quoted-curl-ask.json|test-wget.json|test-nc.json|\
test-netcat.json|test-ps-invoke-webrequest.json|test-ps-iwr.json|\
test-py-c.json|test-node-e.json|test-py-c-from-http-import.json|\
test-node-e-node-https.json) echo ASK ;;
      *) echo silent ;;   # every *-passing fixture, the env-guard fixtures, and
                          # the two v3.0.43 local one-liners
    esac
  }
  if [ -d "$SELF_DIR/test-inputs" ]; then
    for f in "$SELF_DIR"/test-inputs/*.json; do
      b=$(basename "$f")
      exp=$(fixture_expect "$b")
      set +e
      out=$(bash "$SELF_PATH" < "$f" 2>/dev/null)
      rc=$?
      set -e
      kind=silent
      [ "$rc" -eq 2 ] && kind=DENY
      [ -n "$out" ] && kind=ASK
      if [ "$kind" = "$exp" ]; then
        pass=$((pass+1))
      else
        fail=$((fail+1))
        echo "FAIL [fixture $b] expected=$exp got=$kind rc=$rc" >&2
      fi
    done
  else
    echo "NOTE: test-inputs/ absent -- embedded cases only" >&2
  fi

  echo "block-dangerous-bash self-test: $pass passed, $fail failed"
  [ "$fail" -eq 0 ] || exit 1
  exit 0
fi

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')
# Quote-normalized twin for pattern matching (v3.0.41, cross-vendor catch): shell
# quoting is invisible to the shell's own word-splitting -- `git 'commit' --no-verify`
# IS `git commit --no-verify` -- but a literal regex never sees it. Stripping quote
# characters before matching kills that entire evasion class in one move. Honest
# boundary, stated where the patterns live: a regex tier is a TRIPWIRE against
# straightforward and quoted spellings, never a categorical bar on an adversarial
# composer (token concatenation, $(echo ...), variable indirection remain expressible;
# so does simply not using the mediated tool). Matching the NORMALIZED string can only
# widen what's caught; the -n-in-a-commit-message class of false positive stays the
# accepted deny-over-ask trade (v3.0.36).
COMMAND_NORM=$(printf '%s' "$COMMAND" | tr -d "'\"")

HOOK_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ALLOWLIST="$HOOK_DIR/egress-allowlist.txt"

# ---------------------------------------------------------------- Tier 1: DENY
# Destructive / unrecoverable. Checked FIRST: a command that both egresses and
# destroys is denied, never asked.
DENY_PATTERNS=(
  'rm[[:space:]]+-rf[[:space:]]+/'
  'git[[:space:]]+reset[[:space:]]+--hard'
  # Agent bypass of the pre-commit secret scanner (v3.0.36, backlog v3.0-12): git's
  # own --no-verify (and commit's -n alias) stays available to the OPERATOR's hands;
  # a session never reaches it. Commit-scoped on purpose -- `git push -n` is a
  # dry-run, not a bypass, and stays untouched. The space after `commit` is
  # load-bearing: `git commit-tree -n` / `git commitment -n` are different words
  # and must not match (cross-vendor review catch, 2026-08-11). The optional
  # middle group before `commit` covers git GLOBAL options -- `git -C <repo>
  # commit --no-verify`, `git -c k=v commit -n` -- which the original
  # git-adjacent form missed (cross-vendor review catch, 2026-08-17, v3.0.41;
  # the group excludes command separators so an unrelated later command's
  # `commit` cannot be reached across a pipe or `;`).
  'git[[:space:]]+([^|;&]*[[:space:]])?commit[[:space:]]([^|;&]*[[:space:]])?(--no-verify|-n)([[:space:]]|$)'
)

for pat in "${DENY_PATTERNS[@]}"; do
  if echo "$COMMAND_NORM" | grep -Eqi "$pat"; then
    echo "Blocked: command matches denied pattern '$pat'. Destructive commands are denied by policy (unrecoverable -- the v3.0.19 doctrine's deny class). This tier has no allowlist and no ask; if the operation is truly intended, the operator runs it themselves, deliberately." >&2
    exit 2
  fi
done

# ---------------------------------------------------------- DENY: trust surfaces
# v3.0.46 (backlog v3.0-120, brief section 3): a WRITE-SHAPED command that names a path
# in the trust-surface class is denied. The class = the hard-coded floor below in UNION
# with core/security/hooks/trust-surfaces.txt beside this script (fixed relative path;
# the file can only widen; absent/emptied = floor, never fail-open). DENY, not ASK:
# perimeter edits are operator-out-of-session by standing doctrine -- there is no
# legitimate in-session "yes". Write-shaped = a redirection (`>`/`>>`, ignoring the
# harmless `2>/dev/null` / `2>&1` / `>NUL` forms), the file-writing tools (tee, cp, mv,
# rm, install, truncate, ln, dd, rsync, `sed -i`, `perl -i`), the PowerShell writers +
# aliases (Set-Content/sc, Add-Content/ac, Out-File, Copy-Item/cpi, Move-Item/mi,
# Remove-Item/ri/del, New-Item/ni, Clear-Content/clc, Rename-Item/rni, [IO.File]::Write*),
# and an interpreter one-liner (`py/python -c`, `node -e`) that so much as mentions the
# path. Pure reads (cat, grep, ls, diff, git show/log/diff, sed -n, running the sensor
# as a script) pass. Tripwire honesty, same as every rule here: composed spellings are out
# of a regex's reach -- deploy/trust.py's signing rule is what makes them non-authoritative.
TRUST_FLOOR=(
  'core/security/hooks/**'
  'deploy/safe-allowlist.yaml'
  'deploy/evidence/operator-*.md'
  'deploy/rulings/**'
  'deploy/trust.py'
  'deploy/compile-driver.py'
  'deploy/compile-backends.py'
  'deploy/audit-content.py'
  '.claude/settings.json'
  '.claude/settings.local.json'
  '.git/hooks/**'
  '.gitattributes'
)
TRUST_CLASS=("${TRUST_FLOOR[@]}")
if [ -r "$HOOK_DIR/trust-surfaces.txt" ]; then
  while IFS= read -r tline || [ -n "$tline" ]; do
    tline=${tline%%#*}; tline=${tline//\//}
    tline="${tline#"${tline%%[![:space:]]*}"}"; tline="${tline%"${tline##*[![:space:]]}"}"
    [ -n "$tline" ] || continue
    tseen=0
    for tg in "${TRUST_CLASS[@]}"; do if [ "$tg" = "$tline" ]; then tseen=1; break; fi; done
    if [ $tseen -eq 0 ]; then TRUST_CLASS+=("$tline"); fi
  done < "$HOOK_DIR/trust-surfaces.txt"
fi
# Normalized twin for PATH matching: quotes stripped (NORM), backslashes -> /, lowercased.
COMMAND_PATHS=$(printf '%s' "$COMMAND_NORM" | tr '\\' '/'); COMMAND_PATHS=${COMMAND_PATHS,,}
TRUST_WRITE_RE='(^|[[:space:]|;&(`])(tee|cp|mv|rm|install|truncate|ln|dd|rsync|set-content|add-content|out-file|copy-item|move-item|remove-item|new-item|clear-content|rename-item|sc|ac|cpi|mi|ni|clc|rni|ri|del)([[:space:]]|$)|(^|[[:space:]|;&(`])sed[[:space:]]+(-[a-z]*i|--in-place)|(^|[[:space:]|;&(`])perl[[:space:]]+-[a-z]*i|\[io\.file\]::write|(^|[[:space:]`$(])(py|python|python3)[[:space:]]+-c|(^|[[:space:]`$(])node[[:space:]]+-e'
# One alternation over the whole class, built in pure bash (no subshells -- the battery
# runs this hook ~170 times on Windows, where every process costs).
TRUST_RE=''
for tg in "${TRUST_CLASS[@]}"; do
  tg=${tg//./\.}; tg=${tg//\*\*/__DS__}; tg=${tg//\*/[^[:space:]\/;|\&<>]*}; tg=${tg//__DS__/[^[:space:];|\&<>]*}  # \& : bash 5.2 patsub_replacement treats a bare & as the match
  TRUST_RE="${TRUST_RE:+$TRUST_RE|}$tg"
done
TRUST_RE="(^|[^a-z0-9_.-])(\./)?($TRUST_RE)([^a-z0-9_.-]|$)"
trust_write_shaped=0
case "$COMMAND_PATHS" in
  *'>'*)
    # Redirections, with the harmless stderr/null forms removed before the `>` test.
    redir=$(printf '%s' "$COMMAND_PATHS" | sed -E 's#[0-9]*>>?[[:space:]]*(/dev/null|nul([^a-z0-9]|$)|&[0-9]+)##g')
    case "$redir" in *'>'*) trust_write_shaped=1 ;; esac ;;
esac
if [ $trust_write_shaped -eq 0 ] && printf '%s' "$COMMAND_PATHS" | grep -Eq "$TRUST_WRITE_RE"; then
  trust_write_shaped=1
fi
if [ $trust_write_shaped -eq 1 ] && printf '%s' "$COMMAND_PATHS" | grep -Eq "$TRUST_RE"; then
  echo "Blocked: write-shaped command names a TRUST SURFACE (see core/security/hooks/trust-surfaces.txt). Trust surfaces are operator-edited only, outside the session, and committed with \`git commit -S\` under the pinned presence-requiring key; every honest consumer refuses one that is not committed-identical and operator-signed. Read it freely (cat/grep/git show); propose the change in chat." >&2
  exit 2
fi

# ROOT-TARGETING Remove-Item (PowerShell analog of the `rm -rf /` rule above). Requires
# ALL THREE independently: the cmdlet/alias, -Recurse, -Force, and a target token that is
# a bare drive/POSIX root with nothing after it (no extra path segments). AND-of-three
# rather than one line because flag order varies in real invocations (-Recurse -Force vs.
# -Force -Recurse vs. -Path X -Recurse -Force). Deliberately does NOT match a root token
# followed by more path characters, so a scratchpad-scoped
# `Remove-Item -Recurse -Force C:\Users\...\Temp\...\scratchpad\foo` is left alone --
# inert-unless-real-risk, same principle as every other rule in this file.
# v3.0.46 rider (v3.0-120): matched on COMMAND_NORM, the quote-normalized twin, like
# every other rule -- `'Remove-Item' -Recurse -Force C:\` is the same command to the
# shell and must be the same command to this regex.
ROOT_TARGET_RE='(^|[[:space:]])['\''"]?([A-Za-z]:)?[\\/]['\''"]?([[:space:]]|$)'
if echo "$COMMAND_NORM" | grep -Eqi '(^|[[:space:]`$(])(remove-item|rm|ri)[[:space:]]' \
   && echo "$COMMAND_NORM" | grep -Eqi -- '-recurse\b' \
   && echo "$COMMAND_NORM" | grep -Eqi -- '-force\b' \
   && echo "$COMMAND_NORM" | grep -Eqi -- "$ROOT_TARGET_RE"; then
  echo "Blocked: command matches denied pattern 'Remove-Item -Recurse -Force <drive/posix root>'. Root-targeting recursive force-deletes are denied by policy (unrecoverable). This tier has no allowlist and no ask." >&2
  exit 2
fi

# ----------------------------------------------------------------- Tier 2: ASK
# Network egress: named tools, PowerShell egress cmdlets + aliases (these reach
# the network by a different verb than curl/wget), and interpreter-driven
# egress (an inline one-liner can import a socket/http lib and exfiltrate past
# every named-tool pattern; blocks only the -c/-e inline form, not scripts).
# Each entry is "<label>|<pattern>" -- the label keeps the ask reason valid
# JSON and human-readable (raw patterns carry regex punctuation).
ASK_PATTERNS=(
  'curl|(^|[[:space:]`$(])curl[[:space:]]'
  'wget|(^|[[:space:]`$(])wget[[:space:]]'
  'nc|(^|[[:space:]`$(])nc[[:space:]]'
  'netcat|(^|[[:space:]`$(])netcat[[:space:]]'
  'PowerShell egress cmdlet|(^|[[:space:]`$(])(invoke-webrequest|invoke-restmethod|start-bitstransfer)([[:space:]]|$)'
  'PowerShell egress alias|(^|[[:space:]`$(])(irm|iwr)[[:space:]]'
  'inline python|(^|[[:space:]`$(])(py|python|python3)[[:space:]]+-c'
  'inline node|(^|[[:space:]`$(])node[[:space:]]+-e'
)

# Inline-interpreter refinement (v3.0.43, backlog v3.0-124 -- operator-ratified
# 2026-08-18): the named tools above exist to reach the network, so they always
# ask; an interpreter one-liner only MIGHT. Asking on every `py -c` taxed the
# operator with false positives on provably-local commands (a json.load of a
# local file drew the same prompt as an exfiltration), and prompt fatigue is
# itself a perimeter cost: it trains a reflexive Allow. So the inline entries
# ask ONLY when the command carries a network-shaped token, matched against the
# NORMALIZED string so quoting games don't hide one.
# STATED HONESTLY (cross-vendor round-4 correction): this NARROWS ASK coverage.
# Pre-v3.0.43, every inline -c/-e spelling fired the ask, token or no token; now a
# token-absent egress spelling (exotic stdlib API, composed import) passes silently
# where it previously prompted. That is a deliberate, operator-ratified LOOSENING
# (v3.0-124, 2026-08-18) -- the same trade class as v3.0.33's deny->ask -- bought
# to end false-positive prompt fatigue, which was training a reflexive Allow. The
# floor comment below bounds what the gate still promises.
# Token-list bias is WIDE on purpose: over-ask costs one prompt, under-ask misses
# egress. "http" rides bare (cross-vendor round-1 catch, 2026-08-18: the dotted form
# missed `https.get`, `node:https`, `http2`, `from http import client`); the four
# builtin module names ride word-bounded bare (round-2 catch: dotted/prefixed forms
# missed `import {connect} from 'tls'` and `dns/promises`), which subsumes the
# node:/require() spellings in every quoting; the connection-verb group (round-2
# catch: `from asyncio import open_connection` names no module token at all).
# `node:fs`/`require(fs)` stay local and silent -- fs is not in the token list.
NET_TOKEN_RE='urllib|requests|http|socket|ftplib|smtplib|poplib|imaplib|telnetlib|websocket|pycurl|paramiko|xmlrpc|fetch[[:space:](]|axios|xmlhttprequest|open_connection|create_connection|getaddrinfo|multiprocessing\.connection|asyncore|\b(net|tls|dgram|dns|ssl)\b'
# The token list is a FLOOR, not an enumeration (cross-vendor round-3 adjudication,
# 2026-08-18): the stdlib egress-API surface is unbounded (round 3's
# multiprocessing.connection is folded above; its class is not closeable by listing).
# The gate's contract is the common spellings an injected prompt or a convenience
# habit would actually produce. Spellings outside the floor previously DID prompt
# and now do not -- that regression is the ratified trade (above), accepted because
# an ASK prompt informs the operator but never binds a deliberate composer, and a
# prompt stream that is mostly noise stops informing anyone.

for entry in "${ASK_PATTERNS[@]}"; do
  label="${entry%%|*}"
  pat="${entry#*|}"
  if echo "$COMMAND_NORM" | grep -Eqi "$pat"; then
    case "$label" in
      "inline python"|"inline node")
        if ! echo "$COMMAND_NORM" | grep -Eqi "$NET_TOKEN_RE"; then
          continue
        fi
        ;;
    esac
    # Standing allowance? (operator-committed file; ASK tier only)
    if [ -f "$ALLOWLIST" ]; then
      while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in ''|'#'*) continue ;; esac
        if echo "$COMMAND" | grep -Eq -- "$line"; then
          exit 0
        fi
      done < "$ALLOWLIST"
    fi
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"Network egress (%s). Review the destination and payload, then allow or decline this one call. Unattended runs fail closed. For a standing allowance, ask the operator to add an exact pattern line to core/security/hooks/egress-allowlist.txt (operator-edited only)."}}\n' "$label"
    exit 0
  fi
done

exit 0
