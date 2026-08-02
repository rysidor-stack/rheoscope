#!/usr/bin/env bash
# Extracted and generalized from a production project.
# Modified per Decision V2-14: allow .env.example and .env.sample edits.
set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // ""')
BASENAME=$(basename "$FILE_PATH")

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
