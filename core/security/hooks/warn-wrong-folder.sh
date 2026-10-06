#!/usr/bin/env bash
# warn-wrong-folder.sh -- a USER-LEVEL SessionStart hook (v3.0.61, backlog v3.0-217).
#
# Named risk: a session opened in the wrong folder (one level ABOVE a project, e.g. after a
# folder move) runs with no project hooks, settings or skills -- the perimeter is silently
# absent, and the project's own hooks cannot warn because they are what failed to load.
# Five sessions in a row ran that way on one instance before anyone noticed.
#
# This script is installed by the OPERATOR in their user-level settings (~/.claude/), outside
# every repo -- see README.md § When the hooks are not loaded at all. It reads the
# SessionStart JSON on stdin (its "cwd"; falls back to $PWD), and when the working directory
# is not itself a hooked project but a CHILD folder is one, it prints a warning naming that
# project. Otherwise it prints nothing. It never blocks and never fails the session (exit 0).
#
# Self-test: bash warn-wrong-folder.sh --self-test

hooked_project() {   # $1 = a directory; true when it carries a wired project settings file
  local s="$1/.claude/settings.local.json"
  [ -f "$s" ] && grep -q "block-dangerous-bash" "$s" 2>/dev/null
}

shown() {            # a path as the operator knows it: under Git Bash, /c/... or /tmp/... as C:/... (v3.0-229)
  case "$1" in
    /*) if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1" 2>/dev/null && return; fi ;;
  esac
  printf '%s' "$1"
}

check_dir() {        # $1 = the session's working directory; prints the warning or nothing
  local cwd="$1" hits="" d
  [ -n "$cwd" ] && [ -d "$cwd" ] || return 0
  hooked_project "$cwd" && return 0
  for d in "$cwd"/*/; do
    d="${d%/}"
    [ -d "$d" ] || continue
    if hooked_project "$d"; then
      hits="${hits:+$hits, }$(shown "$d")"
    fi
  done
  [ -n "$hits" ] || return 0
  local msg="This session opened in $(shown "$cwd"), which is not a project folder, so no project safety hooks, settings or skills are loaded. Projects one level down: $hits. Reopen the session in the project you meant before doing any work."
  # systemMessage shows the operator; additionalContext tells the session to stop and say so
  msg="${msg//\\/\\\\}"; msg="${msg//\"/\\\"}"
  printf '{"systemMessage": "%s", "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "WRONG FOLDER: %s Tell the operator this before anything else."}}\n' "$msg" "$msg"
}

if [ "${1:-}" = "--self-test" ]; then
  pass=0; fail=0
  t() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "  FAIL: $1 (got: $2)"; fi; }
  T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
  mkdir -p "$T/above/proj/.claude" "$T/above/other" "$T/plain"
  printf '{"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"command":"core/security/hooks/block-dangerous-bash.sh"}]}]}}\n' \
    > "$T/above/proj/.claude/settings.local.json"
  out=$(check_dir "$T/above"); t "one level above a hooked project -> warns, naming it" \
    "$(echo "$out" | grep -c 'proj')" "1"
  t "...as JSON with a systemMessage" "$(echo "$out" | grep -c '"systemMessage"')" "1"
  t "inside the hooked project itself -> silent" "$(check_dir "$T/above/proj")" ""
  t "a folder with no project below it -> silent" "$(check_dir "$T/plain")" ""
  mkdir -p "$T/above/stub/.claude"; echo '{}' > "$T/above/stub/.claude/settings.local.json"
  t "a child whose settings wire no hook is not a hooked project" \
    "$(check_dir "$T/above" | grep -c 'stub')" "0"
  out=$(printf '{"cwd": "%s"}' "$T/above" | bash "$0"); t "reads cwd from the SessionStart JSON on stdin" \
    "$(echo "$out" | grep -c 'proj')" "1"
  echo "warn-wrong-folder self-test: $pass passed, $fail failed"
  [ "$fail" -eq 0 ]; exit $?
fi

input=$(cat 2>/dev/null || true)
cwd=$(printf '%s' "$input" | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
cwd="${cwd//\\\\/\\}"
[ -n "$cwd" ] || cwd="$PWD"
check_dir "$cwd"
exit 0
