#!/usr/bin/env bash
# Extracted and generalized from a production project.
# Modified per Decision V2-14: allow .env.example and .env.sample edits.
# Extended v3.0.36 (backlog v3.0-98(a)): the security perimeter's own files are
# write-guarded -- a session could otherwise append one permissive regex to
# egress-allowlist.txt (or widen a hook's exemption) and quietly loosen the
# perimeter it runs under. Operator edits happen outside sessions, the same
# doctrine as credential-bindings.yaml. Honest limit (README): this guards the
# Edit/Write tool path; shell-redirection writes ride the Bash/PowerShell path,
# where the sweep's allowlist surfacing is the backstop.
set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // ""')
BASENAME=$(basename "$FILE_PATH")

# ---- perimeter write-guard (v3.0-98(a)): core/security/hooks/** is operator-owned
NORM_PATH=$(echo "$FILE_PATH" | tr '\\' '/')
if echo "$NORM_PATH" | grep -Eq '(^|/)core/security/hooks/'; then
  echo "Blocked: '$FILE_PATH' is inside the security perimeter (core/security/hooks/). These files are operator-edited only -- a session proposes the change in chat and the operator applies it outside the session (same doctrine as credential-bindings.yaml). This includes egress-allowlist.txt: a standing allowance is the operator's grant to record, never a session's." >&2
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
