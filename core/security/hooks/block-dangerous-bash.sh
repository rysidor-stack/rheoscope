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

  # -- Phase 2: every committed fixture against its pinned expectation.
  fixture_expect() {
    case "$1" in
      test-rm-rf.json|test-git-reset-hard.json|test-git-commit-no-verify.json|\
test-git-commit-n-alias.json|test-git-C-commit-no-verify.json|\
test-git-config-commit-n-alias.json|test-git-quoted-commit-no-verify.json|\
test-ps-removeitem-root.json|test-ps-removeitem-root-alias.json|\
test-ps-removeitem-root-path-flag.json|test-ps-removeitem-posix-root.json) echo DENY ;;
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

# ROOT-TARGETING Remove-Item (PowerShell analog of the `rm -rf /` rule above). Requires
# ALL THREE independently: the cmdlet/alias, -Recurse, -Force, and a target token that is
# a bare drive/POSIX root with nothing after it (no extra path segments). AND-of-three
# rather than one line because flag order varies in real invocations (-Recurse -Force vs.
# -Force -Recurse vs. -Path X -Recurse -Force). Deliberately does NOT match a root token
# followed by more path characters, so a scratchpad-scoped
# `Remove-Item -Recurse -Force C:\Users\...\Temp\...\scratchpad\foo` is left alone --
# inert-unless-real-risk, same principle as every other rule in this file.
ROOT_TARGET_RE='(^|[[:space:]])['\''"]?([A-Za-z]:)?[\\/]['\''"]?([[:space:]]|$)'
if echo "$COMMAND" | grep -Eqi '(^|[[:space:]`$(])(remove-item|rm|ri)[[:space:]]' \
   && echo "$COMMAND" | grep -Eqi -- '-recurse\b' \
   && echo "$COMMAND" | grep -Eqi -- '-force\b' \
   && echo "$COMMAND" | grep -Eqi -- "$ROOT_TARGET_RE"; then
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
