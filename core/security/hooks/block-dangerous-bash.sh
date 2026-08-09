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

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

HOOK_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ALLOWLIST="$HOOK_DIR/egress-allowlist.txt"

# ---------------------------------------------------------------- Tier 1: DENY
# Destructive / unrecoverable. Checked FIRST: a command that both egresses and
# destroys is denied, never asked.
DENY_PATTERNS=(
  'rm[[:space:]]+-rf[[:space:]]+/'
  'git[[:space:]]+reset[[:space:]]+--hard'
)

for pat in "${DENY_PATTERNS[@]}"; do
  if echo "$COMMAND" | grep -Eqi "$pat"; then
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

for entry in "${ASK_PATTERNS[@]}"; do
  label="${entry%%|*}"
  pat="${entry#*|}"
  if echo "$COMMAND" | grep -Eqi "$pat"; then
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
