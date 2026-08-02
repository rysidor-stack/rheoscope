#!/usr/bin/env bash
# Extracted and generalized from a production project.
# Project-neutral - no substitutions needed.
# One genericization from source: "ask the operator" (Phase 1 / Phase 3 amendment precedent).
set -euo pipefail

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

# Deny patterns - network egress and dangerous commands
DENY_PATTERNS=(
  '(^|[[:space:]`$(])curl[[:space:]]'
  '(^|[[:space:]`$(])wget[[:space:]]'
  '(^|[[:space:]`$(])nc[[:space:]]'
  '(^|[[:space:]`$(])netcat[[:space:]]'
  'rm[[:space:]]+-rf[[:space:]]+/'
  'git[[:space:]]+reset[[:space:]]+--hard'
  # PowerShell egress cmdlets + aliases (matched case-insensitively below) — these bypass
  # the named-tool curl/wget hooks above by reaching the network from a different verb.
  '(^|[[:space:]`$(])(invoke-webrequest|invoke-restmethod|start-bitstransfer)([[:space:]]|$)'
  '(^|[[:space:]`$(])(irm|iwr)[[:space:]]'
  # Interpreter-driven egress: an inline one-liner can import a socket/http lib and
  # exfiltrate past every named-tool hook. Blocks only the -c/-e inline form, not scripts.
  '(^|[[:space:]`$(])(py|python|python3)[[:space:]]+-c'
  '(^|[[:space:]`$(])node[[:space:]]+-e'
)

for pat in "${DENY_PATTERNS[@]}"; do
  if echo "$COMMAND" | grep -Eqi "$pat"; then
    echo "Blocked: command matches denied pattern '$pat'. Network egress and destructive commands are denied by policy. If this is legitimate work, ask the operator to temporarily disable the hook or whitelist the specific command." >&2
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
  echo "Blocked: command matches denied pattern 'Remove-Item -Recurse -Force <drive/posix root>'. Root-targeting recursive force-deletes are denied by policy. If this is legitimate work, ask the operator to temporarily disable the hook or whitelist the specific command." >&2
  exit 2
fi

exit 0
