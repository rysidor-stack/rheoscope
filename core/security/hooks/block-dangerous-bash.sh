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
#   ASK (exit 0 + PreToolUse "ask" JSON) -- UNATTENDED RUNS ONLY since v3.0.47
#     (backlog v3.0-134): network egress -- the named tools, the PowerShell
#     egress cmdlets/aliases, and inline interpreter one-liners carrying a
#     network token. In an unattended run nobody answers, so the ask FAILS
#     CLOSED -- the perimeter holds exactly where prompt-injection risk is
#     highest. In an ATTENDED session the same call is ALLOWED and LOGGED
#     (.claude/egress-log.jsonl, surfaced by /sweep step 18): the per-call
#     prompt was never a bar against a deliberate composer and its tax trained
#     a reflexive Allow (v3.0-124); after-the-fact review replaces it. The
#     v3.0.33 history: hard deny -> ask (v3.0-95); ask -> log-and-surface when
#     attended (v3.0-134, the 2026-08-22 five-pass run).
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
  ORIG_PATH="${BASH_SOURCE[0]}"
  SELF_DIR=$(cd "$(dirname "$ORIG_PATH")" && pwd)
  # v3.0.47: run the board from a TEMP TREE shaped like the repo, so the hook's fixed
  # relative log path (repo/.claude/egress-log.jsonl) lands in the temp tree, never in
  # the real one -- the same copy-to-scratch pattern the README uses for allowlist tests.
  TT=$(mktemp -d)
  mkdir -p "$TT/core/security/hooks"
  cp "$ORIG_PATH" "$TT/core/security/hooks/block-dangerous-bash.sh"
  [ -f "$SELF_DIR/trust-surfaces.txt" ] && cp "$SELF_DIR/trust-surfaces.txt" "$TT/core/security/hooks/"
  SELF_PATH="$TT/core/security/hooks/block-dangerous-bash.sh"
  LOG="$TT/.claude/egress-log.jsonl"
  pass=0; fail=0
  # Egress cases run UNATTENDED by default (RHEOSCOPE_UNATTENDED=1): that is where the
  # ASK tier still lives. Attended behaviour (allow + log) is pinned in its own block.
  export RHEOSCOPE_UNATTENDED=1
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
  # -- DENY tier: in-session promotion (v3.0.50, ADR #11 cond. 4 as amended / v3.0-139)
  run_case DENY 'py deploy/promote.py 3f9a1c0e7b2d4e6f' 'promote-py'
  run_case DENY 'python deploy/promote.py --root . 3f9a1c0e7b2d' 'promote-python-root'
  run_case DENY 'cd deploy && python promote.py 3f9a1c0e7b2d4e6f' 'promote-cd-relative'
  run_case DENY 'py capabilities/knowledge-os/extracted/deploy/promote.py abc123abc123' 'promote-dev-repo-path'
  run_case DENY 'git tag -a -m "promotion" retire/2 abc123' 'promote-git-tag-record'
  run_case DENY 'git tag retire/7 HEAD' 'promote-git-tag-lightweight'
  run_case DENY "git -C . tag -a -m 'x' 'retire/12' abc" 'promote-git-tag-quoted-C'
  run_case DENY 'git tag -s retire/3 abc -m "retire 3"' 'promote-git-tag-signed-in-session'
  run_case DENY 'git tag -a -m "promotion" retire/batch/5-6 abc123' 'promote-git-tag-batch-record'
  run_case DENY 'git tag -s retire/batch/12-14 abc -m "batch"' 'promote-git-tag-batch-signed'
  run_case DENY 'py deploy/promote.py 3f9a1c0e7b2d4e6f --halt-after 1' 'promote-batch-halt'
  run_case DENY 'py deploy/promote.py --rollback 3f9a1c0e7b2d4e6f' 'promote-rollback'
  run_case silent 'py deploy/retire.py --propose wiki/topic/view.md --span "Section A"' 'retire-propose-allowed'
  run_case silent 'py deploy/retire.py --recover' 'retire-recover-allowed'
  run_case silent 'py deploy/pending.py --root . --render' 'pending-render-allowed'
  run_case silent 'cat deploy/promote.py' 'promote-read-allowed'
  run_case silent 'grep -n Refuse deploy/promote.py | head' 'promote-grep-allowed'
  run_case silent 'git show HEAD:deploy/promote.py' 'promote-git-show-allowed'
  run_case DENY 'deploy/promote.py 3f9a1c0e7b2d4e6f' 'promote-direct-exec'
  run_case DENY './deploy/promote.py 3f9a1c0e7b2d4e6f' 'promote-direct-exec-dotslash'
  run_case DENY 'cd x; python3 ../deploy/promote.py 3f9a1c0e7b2d4e6f' 'promote-after-semicolon'
  run_case silent 'git tag -l "retire/*"' 'git-tag-list-allowed'
  run_case silent 'git tag -v retire/2' 'git-tag-verify-allowed'
  run_case silent 'git tag -v retire/batch/5-6' 'git-tag-verify-batch-allowed'
  run_case silent 'git tag -l "retire/batch/*"' 'git-tag-list-batch-allowed'
  run_case silent 'git tag v3.0.50' 'git-tag-release-allowed'
  run_case silent 'python deploy/promote.py --self-test' 'promote-selftest-allowed'
  # -- DENY tier: trust-surface writes (v3.0.46, v3.0-120 brief section 3), per surface
  run_case DENY 'echo x > deploy/retire.py' 'ts-redirect-retire-verb'
  run_case DENY 'cp /tmp/p.py deploy/pending.py' 'ts-cp-pending'
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
  # -- v3.0.51 (v3.0-144, fleet inbox #4): the class token matches WRITE TARGETS only --
  # prose that merely MENTIONS a trust-surface path is knowledge intake, not a perimeter
  # write; a write whose TARGET is a surface still denies. Both directions pinned.
  run_case silent 'echo "the verifier is deploy/trust.py" >> raw/note.md' 'ts-prose-mention-echo-body'
  run_case silent 'printf "%s\n" "mentions core/security/hooks/README.md" > raw/note.md' 'ts-prose-mention-printf-body'
  run_case silent 'cat >> raw/2026-08-23-note.md <<EOF
see deploy/trust.py for the gate
EOF' 'ts-prose-mention-heredoc-body'
  run_case silent 'python export.py --config deploy/safe-allowlist.yaml > /tmp/out.log' 'ts-mention-with-elsewhere-redirect'
  run_case DENY 'cat /tmp/x | tee core/security/hooks/egress-allowlist.txt' 'ts-tee-target-still-denies'
  run_case DENY 'echo prose about raw/note.md > deploy/trust.py' 'ts-redirect-target-still-denies'
  # the standing composed-spelling boundary (out of a regex's reach; committed-identity
  # is the layer behind it) -- pinned as the boundary, not as coverage:
  run_case silent 'echo x > "$(echo deploy/trust.py)"' 'ts-composed-target-standing-boundary'
  run_case DENY 'sed -i "s#raw/note.md#raw/other.md#" core/security/hooks/trust-surfaces.txt' 'ts-sed-i-target-operand'
  run_case silent 'sed -i "s#x#y#" raw/note.md' 'ts-sed-i-elsewhere-target'
  # accepted conservative FP: a sed/perl SCRIPT operand naming a class path (deny-over-
  # allow on the perimeter; the target parser does not parse sed scripts)
  run_case DENY 'sed -i "s#a#deploy/trust.py#" raw/note.md' 'ts-sed-script-mention-accepted-fp'
  # cross-vendor round-1 folds (v3.0.51): the `>|` clobber redirect is a target zone;
  # a multiword PowerShell -Value naming a surface in PROSE passes (the value's grouping
  # is gone after quote-stripping, so every token to the next flag is value); a
  # herestring is not an output redirect.
  run_case DENY 'echo x >| deploy/trust.py' 'ts-clobber-redirect-target'
  run_case silent 'echo x >| raw/note.md' 'ts-clobber-redirect-elsewhere'
  run_case silent 'Set-Content -Path raw/note.md -Value "the verifier is deploy/trust.py"' 'ts-ps-value-prose-mention-passes'
  run_case DENY 'Set-Content -Value "prose" -Path deploy/compile-driver.py' 'ts-ps-value-then-path-target-denies'
  run_case silent 'cat <<< "see deploy/trust.py for the gate"' 'ts-herestring-not-a-redirect'
  # accepted conservative over-match (round-2, documented): a write-tool SOURCE operand
  # naming a surface stays denied (mv/rm destroy sources; cp is kept uniform)
  run_case DENY 'cp deploy/trust.py /tmp/backup.py' 'ts-cp-surface-source-accepted-overmatch'
  # -- Remove-Item root rule now matched on COMMAND_NORM (v3.0.46 rider)
  run_case DENY "'Remove-Item' -Recurse -Force C:\\" 'removeitem-quoted-cmdlet-root'
  run_case DENY "Remove-Item -Recurse '-Force' 'C:\\'" 'removeitem-quoted-flags-root'

  # -- v3.0.47 (v3.0-134): ATTENDED sessions allow egress and LOG it; unattended ASKs.
  # Each attended case must be silent AND append exactly one row with the right shape.
  attended_case() {  # cmd label expected_kind expected_allowlisted
    before=$(wc -l < "$LOG" 2>/dev/null || echo 0)
    json=$(jq -n --arg c "$1" '{tool_input:{command:$c}}')
    set +e
    out=$(printf '%s' "$json" | env -u RHEOSCOPE_UNATTENDED bash "$SELF_PATH" 2>/dev/null)
    rc=$?
    set -e
    after=$(wc -l < "$LOG" 2>/dev/null || echo 0)
    row=$(tail -n 1 "$LOG" 2>/dev/null || echo '{}')
    kind=$(printf '%s' "$row" | jq -r '.kind // ""')
    mode=$(printf '%s' "$row" | jq -r '.mode // ""')
    allow=$(printf '%s' "$row" | jq -r '.allowlisted | tostring')
    if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ $((after-before)) -eq 1 ] && [ "$kind" = "$3" ] \
       && [ "$mode" = "attended" ] && [ "$allow" = "$4" ]; then
      pass=$((pass+1))
    else
      fail=$((fail+1))
      echo "FAIL [attended $2] rc=$rc out=${out:0:30} rows+=$((after-before)) kind=$kind mode=$mode allow=$allow" >&2
    fi
  }
  attended_case 'curl -s https://api.example.com/v1' 'curl-allowed-logged' egress false
  attended_case 'wget https://files.example.org/f.tar' 'wget-allowed-logged' egress false
  attended_case 'Invoke-WebRequest -Uri https://example.com' 'ps-iwr-allowed-logged' egress false
  attended_case 'python -c "import urllib.request; urllib.request.urlopen(u)"' 'py-urllib-allowed-logged' egress false
  row=$(tail -n 1 "$LOG"); host=$(printf '%s' "$row" | jq -r '.host')
  run_case silent 'ls -la' 'plain-attended-noop'   # (unattended export still set; plain never logs)
  # the host column: the first host-shaped token
  json=$(jq -n --arg c 'curl -s https://api.example.com/v1/x' '{tool_input:{command:$c}}')
  printf '%s' "$json" | env -u RHEOSCOPE_UNATTENDED bash "$SELF_PATH" >/dev/null 2>&1 || true
  host=$(tail -n 1 "$LOG" | jq -r '.host')
  if [ "$host" = "api.example.com" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL [host-extraction] got=$host" >&2; fi
  # the standing allowance is logged with allowlisted=true (and never asked, any mode)
  printf 'curl[[:space:]]+-s[[:space:]]+https://api\\.replicate\\.com/\n' > "$TT/core/security/hooks/egress-allowlist.txt"
  attended_case 'curl -s https://api.replicate.com/v1/models' 'allowlisted-logged' egress true
  before=$(wc -l < "$LOG"); run_case silent 'curl -s https://api.replicate.com/v1/models' 'allowlisted-unattended-silent'
  rm -f "$TT/core/security/hooks/egress-allowlist.txt"
  # telemetry (v3.0-136a): an unattended ASK and a DENY are logged with their kinds
  # (join("|") not a "/" literal in the jq filter: MSYS would path-convert a leading slash)
  before=$(wc -l < "$LOG"); run_case ASK 'curl -s https://api.example.com/v1' 'unattended-ask-logged'
  if [ "$(tail -n 1 "$LOG" | jq -r '[.kind,.mode]|join("|")')" = "egress-ask|unattended" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL [ask-telemetry] $(tail -n 1 "$LOG")" >&2; fi
  run_case DENY 'rm -rf /' 'deny-logged'
  if [ "$(tail -n 1 "$LOG" | jq -r '.kind')" = "destructive-deny" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL [deny-telemetry]" >&2; fi
  run_case DENY 'echo x > deploy/safe-allowlist.yaml' 'trust-deny-logged'
  if [ "$(tail -n 1 "$LOG" | jq -r '.kind')" = "trust-deny" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL [trust-deny-telemetry]" >&2; fi
  # payload permission_mode dontAsk counts as unattended even without the env marker
  json=$(jq -n --arg c 'curl -s https://api.example.com/v1' '{tool_input:{command:$c},permission_mode:"dontAsk"}')
  set +e; out=$(printf '%s' "$json" | env -u RHEOSCOPE_UNATTENDED bash "$SELF_PATH" 2>/dev/null); rc=$?; set -e
  if [ -n "$out" ] && [ "$rc" -eq 0 ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL [dontAsk-is-unattended]" >&2; fi
  # a log that cannot be written (a FILE where the .claude dir should be) never crashes the
  # hook and never passes SILENTLY: the attended call falls back to ASK (round-2 catch)
  LT=$(mktemp -d); mkdir -p "$LT/core/security/hooks"; cp "$SELF_PATH" "$LT/core/security/hooks/"; : > "$LT/.claude"
  json=$(jq -n --arg c 'curl -s https://api.example.com/v1' '{tool_input:{command:$c}}')
  set +e; out=$(printf '%s' "$json" | env -u RHEOSCOPE_UNATTENDED bash "$LT/core/security/hooks/block-dangerous-bash.sh" 2>/dev/null); rc=$?; set -e
  if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q '"ask"' && printf '%s' "$out" | grep -q 'could not be LOGGED'; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL [log-failure-asks] rc=$rc out=${out:0:60}" >&2; fi
  json=$(jq -n --arg c 'ls -la' '{tool_input:{command:$c}}')
  set +e; out=$(printf '%s' "$json" | env -u RHEOSCOPE_UNATTENDED bash "$LT/core/security/hooks/block-dangerous-bash.sh" 2>/dev/null); rc=$?; set -e
  if [ "$rc" -eq 0 ] && [ -z "$out" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL [log-failure-plain-still-silent] rc=$rc" >&2; fi
  # ...and an ALLOWLISTED call with an unwritable log asks too (round-3 catch)
  printf 'curl[[:space:]]+-s[[:space:]]+https://api\.replicate\.com/
' > "$LT/core/security/hooks/egress-allowlist.txt"
  json=$(jq -n --arg c 'curl -s https://api.replicate.com/v1/models' '{tool_input:{command:$c}}')
  set +e; out=$(printf '%s' "$json" | env -u RHEOSCOPE_UNATTENDED bash "$LT/core/security/hooks/block-dangerous-bash.sh" 2>/dev/null); rc=$?; set -e
  if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q 'Allowlisted egress' ; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL [log-failure-allowlisted-asks] rc=$rc out=${out:0:60}" >&2; fi
  rm -rf "$LT"
  # every row is valid JSON with the six fields
  if jq -e 'select(.ts and .kind and .mode and (.command|type=="string") and (.allowlisted|type=="boolean"))' "$LOG" >/dev/null 2>&1 \
     && [ "$(jq -c . "$LOG" | wc -l)" = "$(wc -l < "$LOG")" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL [log-shape]" >&2; fi

  # -- Phase 2: every committed fixture against its pinned expectation (UNATTENDED), then
  #    every ASK fixture again ATTENDED, where it must be silent.
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
    for f in "$SELF_DIR"/test-inputs/*.json; do
      b=$(basename "$f")
      [ "$(fixture_expect "$b")" = "ASK" ] || continue
      before=$(wc -l < "$LOG")
      set +e
      out=$(env -u RHEOSCOPE_UNATTENDED bash "$SELF_PATH" < "$f" 2>/dev/null)
      rc=$?
      set -e
      after=$(wc -l < "$LOG")
      if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ $((after-before)) -eq 1 ]          && [ "$(tail -n 1 "$LOG" | jq -r '[.kind,.mode]|join("|")')" = "egress|attended" ]; then pass=$((pass+1)); else
        fail=$((fail+1)); echo "FAIL [fixture $b attended] expected silent + exactly one egress/attended row, got rc=$rc out=${out:0:30} rows+=$((after-before))" >&2; fi
    done
  else
    echo "NOTE: test-inputs/ absent -- embedded cases only" >&2
  fi
  rm -rf "$TT"

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

# ------------------------------------------------ v3.0.47: log-and-surface (v3.0-134)
# ATTENDED sessions no longer ASK for egress: the call is allowed and appended to an
# append-only log the sweep surfaces (one line per new host). UNATTENDED runs keep the
# ASK, which nobody can answer, so they fail closed exactly as before. Why (the five-pass
# run of 2026-08-22): the threat on a one-operator harness is a misled or wrong session,
# not an adaptive adversary; the ask was never a bar against composed spellings and its
# prompt tax trained a reflexive Allow (v3.0-124). The DENY tier is untouched.
# Unattended := the scheduled wrappers set RHEOSCOPE_UNATTENDED=1 (standing-loop and
# nightly-sweep recipes), OR the PreToolUse payload reports permission_mode "dontAsk".
# The log path is FIXED relative to this script (repo root/.claude/, per-machine,
# gitignored) -- no env override, because an env-settable path would let a session
# point the log at /dev/null. Every ASK and every DENY is logged too (v3.0-136 telemetry:
# a tier nobody ever declines is a rubber stamp).
PERMISSION_MODE=$(echo "$INPUT" | jq -r '.permission_mode // ""')
UNATTENDED=0
if [ -n "${RHEOSCOPE_UNATTENDED:-}" ] || [ "$PERMISSION_MODE" = "dontAsk" ]; then UNATTENDED=1; fi
LOG_DIR="$HOOK_DIR/../../../.claude"
LOG_FILE="$LOG_DIR/egress-log.jsonl"
log_row() {  # kind label allowlisted -> returns 1 when the row could NOT be written
  local host
  host=$(printf '%s' "$COMMAND_NORM" | grep -Eoi '([a-z0-9-]+\.)+[a-z]{2,}(:[0-9]+)?' | head -1 || true)
  mkdir -p "$LOG_DIR" 2>/dev/null || return 1
  jq -n -c --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg kind "$1" --arg label "$2" \
     --arg host "${host:-}" --argjson allow "$3" --arg mode "$([ $UNATTENDED -eq 1 ] && echo unattended || echo attended)" \
     --arg cmd "$(printf '%s' "$COMMAND" | cut -c1-400)" \
     '{ts:$ts,kind:$kind,label:$label,host:$host,allowlisted:$allow,mode:$mode,command:$cmd}' \
     >> "$LOG_FILE" 2>/dev/null || return 1
  return 0
}

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
    log_row destructive-deny "$pat" false || true
    echo "Blocked: command matches denied pattern '$pat'. Destructive commands are denied by policy (unrecoverable -- the v3.0.19 doctrine's deny class). This tier has no allowlist and no ask; if the operation is truly intended, the operator runs it themselves, deliberately." >&2
    exit 2
  fi
done

# ---------------------------------------------------------- DENY: in-session promotion
# v3.0.50 (ADR #11 condition 4 as amended, binding item 3; backlog v3.0-139): the visible-
# mode promote action is the OPERATOR'S, from their own terminal -- running deploy/
# promote.py, or hand-writing its promotion record (`git tag ... retire/<seq>`), from
# inside a session is denied outright. Not ASK: the boundary IS that it never happens in
# the session that prepared the proposal. promote.py refuses under the session env
# markers too (belt and braces on the mediated lane); v3.0.52: the BATCH promotion
# record (`git tag ... retire/batch/<first>-<last>`) and the rollback flags ride the
# same rule -- promote.py in any spelling, and any retire/* tag write, are the
# operator's; an unmediated spelling leaves a
# durable pending item the next sweep shows (deploy/pending.py) -- refuse OR notice.
# Reads of either file, `retire.py --propose/--list/--recover`, and `git tag -l` pass.
PROMOTE_RE='(^|[[:space:]|;&(`])(py|python3?|pythonw)([[:space:]]+-[^[:space:]|;&]+)*[[:space:]]+[^[:space:]|;&]*promote\.py([[:space:]]|$)|(^|[|;&(`][[:space:]]*)(\./)?[^[:space:]|;&]*/promote\.py([[:space:]]|$)|(^|[[:space:]|;&(`])git[[:space:]]+([^|;&]*[[:space:]])?tag[[:space:]]+([^|;&]*[[:space:]])?["'"'"']?retire/(batch/)?[0-9]+'
if printf '%s' "$COMMAND_NORM" | grep -Eqi "$PROMOTE_RE" \
   && ! printf '%s' "$COMMAND_NORM" | grep -Eqi '(^|[[:space:]])git[[:space:]]+([^|;&]*[[:space:]])?tag[[:space:]]+(-l|--list|-v|--verify|-n)' \
   && ! printf '%s' "$COMMAND_NORM" | grep -Eq -- '--self-test'; then
  log_row trust-deny promote false || true
  echo "Blocked: the retirement promote action is the operator's, run from THEIR terminal (\`py deploy/promote.py <proposal-digest>\`), never from a session; the same goes for writing its promotion record by hand (\`git tag retire/<seq>\`). A session PREPARES (\`py deploy/retire.py --propose ...\`) and stops; the next sweep shows what was prepared. Propose in chat; nothing here can publish." >&2
  exit 2
fi

# ---------------------------------------------------------- DENY: trust surfaces
# v3.0.46 (backlog v3.0-120, brief section 3), matching narrowed to WRITE TARGETS in
# v3.0.51 (backlog v3.0-144, fleet inbox #4): a write-shaped command whose TARGET is a
# path in the trust-surface class is denied. The class = the hard-coded floor below in
# UNION with core/security/hooks/trust-surfaces.txt beside this script (fixed relative
# path; the file can only widen; absent/emptied = floor, never fail-open). DENY, not
# ASK: perimeter edits are operator-out-of-session by standing doctrine -- there is no
# legitimate in-session "yes".
# WHY targets, not the whole text (v3.0-144): the v3.0.46 rule matched the class token
# ANYWHERE in a write-shaped command, so ordinary knowledge intake ABOUT the perimeter
# (a raw/ note, a receipt, a sweep briefing citing deploy/trust.py by path) was refused
# on the Bash lane -- prose that mentions a surface is not a write TO it. Target zones:
#   - every redirection target (the token after `>`/`>>`; the harmless `2>/dev/null` /
#     `2>&1` / `>NUL` forms removed first),
#   - the operands of the file-writing tools (tee, cp, mv, rm, install, truncate, ln,
#     dd, rsync) and the PowerShell writers + aliases (Set-Content/sc, Add-Content/ac,
#     Out-File, Copy-Item/cpi, Move-Item/mi, Remove-Item/ri/del, New-Item/ni,
#     Clear-Content/clc, Rename-Item/rni) -- every non-flag token after the tool word in
#     its command segment, skipping -Value/-InputObject values (to the next flag) and
#     -Encoding/-ItemType-class single values. ALL operands, sources included, on
#     purpose: mv/rm/Rename-Item destroy their source, and distinguishing cp's
#     read-only source from its destination per tool buys little against a deny that
#     costs nothing legitimate -- `cp deploy/trust.py /tmp/x` stays denied (an accepted
#     conservative over-match, pinned; read a surface with cat/git show instead),
#   - `sed -i` / `perl -i`: their non-flag operands (the script text included -- a
#     script operand naming a class path is a conservative over-match, kept: deny-over-
#     allow on the perimeter, pinned as an accepted false positive).
# Interpreter one-liners (`py/python -c`, `node -e`, [IO.File]::Write*) keep WHOLE-TEXT
# matching: the write target hides inside opaque code no regex can parse, so a one-liner
# that so much as mentions the path stays denied (the documented accepted FP).
# Pure reads (cat, grep, ls, diff, git show/log/diff, sed -n, running the sensor as a
# script) pass; so does prose naming a surface while writing elsewhere. Tripwire honesty,
# same as every rule here: composed spellings -- `> "$(echo deploy/trust.py)"`, variable
# indirection, a path arriving via stdin (`... | xargs rm`) -- are DATA FLOW a regex
# cannot see and are out of this tier's reach, exactly as documented since v3.0.41;
# deploy/trust.py's committed-identity rule is what makes them non-authoritative.
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
INTERP_WRITE_RE='\[io\.file\]::write|(^|[[:space:]`$(])(py|python|python3)[[:space:]]+-c|(^|[[:space:]`$(])node[[:space:]]+-e'
# One alternation over the whole class, built in pure bash (no subshells -- the battery
# runs this hook ~170 times on Windows, where every process costs).
TRUST_RE=''
for tg in "${TRUST_CLASS[@]}"; do
  tg=${tg//./\.}; tg=${tg//\*\*/__DS__}; tg=${tg//\*/[^[:space:]\/;|\&<>]*}; tg=${tg//__DS__/[^[:space:];|\&<>]*}  # \& : bash 5.2 patsub_replacement treats a bare & as the match
  TRUST_RE="${TRUST_RE:+$TRUST_RE|}$tg"
done
TRUST_RE="(^|[^a-z0-9_.-])(\./)?($TRUST_RE)([^a-z0-9_.-]|$)"
# TARGET-ZONE extraction (v3.0-144). Zone 1: redirection targets.
TRUST_TARGETS=''
case "$COMMAND_PATHS" in
  *'>'*)
    redir=$(printf '%s' "$COMMAND_PATHS" | sed -E 's#[0-9]*>>?[[:space:]]*(/dev/null|nul([^a-z0-9]|$)|&[0-9]+)##g')
    rt=$(printf '%s' "$redir" | grep -Eo '>>?\|?[[:space:]]*[^[:space:];|&<>]+' | sed -E 's/^>>?\|?[[:space:]]*//') || true
    [ -n "$rt" ] && TRUST_TARGETS="$rt"
    ;;
esac
# Zone 2: operands of the write-shaped tools, per simple-command segment (split on
# | ; & \` and newlines -- heredoc BODY lines become their own segments with no tool
# word, so prose in them is never an operand; parens are NOT separators, because the
# quote-stripped twin exposes formerly-quoted parens inside sed/awk scripts -- a tool
# word behind `(` is caught by stripping its leading parens instead). Pure bash inside
# one substitution.
TOOL_TARGETS=$(printf '%s' "$COMMAND_PATHS" | tr '|;&`' '\n\n\n\n' | {
  out=''
  while IFS= read -r seg || [ -n "$seg" ]; do
    tool=''; skipnext=0; valmode=0; sedperl=0; saw_i=0; pending=''
    set -f
    # shellcheck disable=SC2086
    set -- $seg
    set +f
    for tok in "$@"; do
      if [ -z "$tool" ]; then
        while :; do case "$tok" in \(*) tok=${tok#\(} ;; *) break ;; esac; done
        case "$tok" in
          tee|cp|mv|rm|install|truncate|ln|dd|rsync|set-content|add-content|out-file|copy-item|move-item|remove-item|new-item|clear-content|rename-item|sc|ac|cpi|mi|ni|clc|rni|ri|del) tool=$tok ;;
          sed|perl) tool=$tok; sedperl=1 ;;
        esac
        continue
      fi
      if [ "$skipnext" -eq 1 ]; then skipnext=0; continue; fi
      if [ "$valmode" -eq 1 ]; then
        case "$tok" in -*) valmode=0 ;; *) continue ;; esac
      fi
      case "$tok" in
        -value|-inputobject) valmode=1 ;;
        -encoding|-itemtype|-stream|-filter|-newname) skipnext=1 ;;
        --in-place*) saw_i=1 ;;
        -*) if [ "$sedperl" -eq 1 ]; then case "$tok" in -*i*) saw_i=1 ;; esac; fi ;;
        *) pending="$pending
$tok" ;;
      esac
    done
    # sed/perl are writers only with -i; every other listed tool always is
    if [ -n "$tool" ] && { [ "$sedperl" -eq 0 ] || [ "$saw_i" -eq 1 ]; }; then
      out="$out$pending"
    fi
  done
  printf '%s' "$out"
})
[ -n "$TOOL_TARGETS" ] && TRUST_TARGETS="$TRUST_TARGETS$TOOL_TARGETS"
deny_trust=0
if [ -n "$TRUST_TARGETS" ] && printf '%s' "$TRUST_TARGETS" | grep -Eq "$TRUST_RE"; then
  deny_trust=1
fi
# interpreter one-liners: whole-text matching kept (target unparseable inside code)
if [ "$deny_trust" -eq 0 ] && printf '%s' "$COMMAND_PATHS" | grep -Eq "$INTERP_WRITE_RE" \
   && printf '%s' "$COMMAND_PATHS" | grep -Eq "$TRUST_RE"; then
  deny_trust=1
fi
if [ "$deny_trust" -eq 1 ]; then
  log_row trust-deny trust-surface false || true
  echo "Blocked: write-shaped command TARGETS a trust surface (see core/security/hooks/trust-surfaces.txt). Trust surfaces are operator-edited only, outside the session, and committed with \`git commit -S\` under the pinned presence-requiring key; every honest consumer refuses one that is not committed-identical and operator-signed. Read it freely (cat/grep/git show), cite it freely in prose you write elsewhere (v3.0-144); propose the change itself in chat." >&2
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
  log_row destructive-deny removeitem-root false || true
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
    # Standing allowance? (operator-committed file; ASK tier only) -- logged, never asked
    if [ -f "$ALLOWLIST" ]; then
      while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in ''|'#'*) continue ;; esac
        if echo "$COMMAND" | grep -Eq -- "$line"; then
          # logged like any other egress; an unwritable log falls back to ASK here too
          # (cross-vendor round-3 catch: "never silence" must include the allowlist path)
          if log_row egress "$label" true; then exit 0; fi
          printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"Allowlisted egress (%s) could not be LOGGED (.claude/egress-log.jsonl unwritable), so this one call asks instead of passing silently. Fix the log path."}}
' "$label"
          exit 0
        fi
      done < "$ALLOWLIST"
    fi
    # v3.0.47: attended -> allow + log; unattended -> ask (fails closed, nobody answers).
    # If the row CANNOT be written, an attended call falls back to ASK (cross-vendor
    # round-2 catch): the design fails toward visibility, never toward silence.
    if [ $UNATTENDED -eq 0 ]; then
      if log_row egress "$label" false; then
        exit 0
      fi
      printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"Network egress (%s) could not be LOGGED (.claude/egress-log.jsonl unwritable), so this one call asks instead of passing silently. Fix the log path; attended egress is normally allowed and logged."}}\n' "$label"
      exit 0
    fi
    log_row egress-ask "$label" false || true
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"Network egress (%s) in an UNATTENDED run: no operator is present to review it, so it fails closed. Attended sessions allow and log egress (.claude/egress-log.jsonl, surfaced by /sweep). For a standing allowance, ask the operator to add an exact pattern line to core/security/hooks/egress-allowlist.txt (operator-edited only)."}}\n' "$label"
    exit 0
  fi
done

exit 0
