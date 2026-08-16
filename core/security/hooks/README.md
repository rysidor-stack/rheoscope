# Security Perimeter — Claude Code Hooks

This directory holds PreToolUse hooks that block named risks before they reach the filesystem or network.

**This perimeter is deliberately negative — it says what must not happen.** The positive
convention (where credentials DO live: the OS-vault credential broker, operator-typed,
delivered by name through operator-pinned bindings) is `core/security/CREDENTIALS.md`.
Read it before designing any credential storage; deriving a storage plan from these
hooks' prohibitions alone produced a wrong-but-compliant plaintext plan in a live
session (2026-08-03), which is why that file exists.

## Matcher scope — read this before assuming coverage

A hook only mediates the **tools its `matcher` names**. Claude Code exposes separate `Bash`
and `PowerShell` tools (the PowerShell tool is a distinct code path, not a subprocess wrapped
by `Bash`), so a settings file that wires `block-dangerous-bash.sh` under `"matcher": "Bash"`
alone leaves every PowerShell-tool call **completely unmediated** — even though the script's
own deny patterns already cover PowerShell command forms (`Invoke-WebRequest`, `irm`, `iwr`,
`Remove-Item -Recurse -Force`, ...). The patterns being present in the script is not the same
as the script running. **Both matcher entries are required for real coverage on Windows**:

```json
{ "matcher": "Bash", "hooks": [{ "type": "command", "command": ".../block-dangerous-bash.sh" }] },
{ "matcher": "PowerShell", "hooks": [{ "type": "command", "command": ".../block-dangerous-bash.sh" }] }
```

`block-env-writes.sh` is tool-agnostic already (`Edit|Write` covers both tools' file-write
path), so it needs only the one matcher entry.

`core/security/settings.local.json.example.template` (and each instantiation's
`.../settings.local.json.example`) ships both `block-dangerous-bash.sh` entries. `/doctor`
check 7 (hooks-wired) verifies both matchers are present, not just that the script is
referenced somewhere in the file — see `core/skills/doctor/doctor.py`.

## Hooks in this perimeter

| Hook | Matchers required | Behavior | Risk |
|------|--------------------|--------|------|
| block-dangerous-bash.sh | `Bash`, `PowerShell` (both) | **Two tiers (v3.0.33, backlog v3.0-95 — the v3.0.19 deny-only-the-unrecoverable doctrine applied to the hook layer).** **DENY (exit 2):** destructive commands — `rm -rf /`, `git reset --hard`, and the PowerShell analog `Remove-Item -Recurse -Force <bare drive/POSIX root>`. No allowlist, no ask — there is no legitimate unattended "yes" to these. **ASK (exit 0 + PreToolUse `"ask"` JSON):** network egress — `curl`/`wget`/`nc`/`netcat`, PowerShell `Invoke-WebRequest`/`Invoke-RestMethod`/`Start-BitsTransfer`/`irm`/`iwr`, and interpreter one-liners (`py`/`python`/`python3 -c`, `node -e`). The operator reviews the exact command and approves or declines that one call; **an unanswered ask fails closed**, so unattended runs stay fully perimetered. Standing allowances live in `egress-allowlist.txt` beside the script (one extended regex per line, consulted by the ASK tier only, matched-command allowed silently) — **operator-edited only**, same doctrine as `credential-bindings.yaml`; the path is fixed relative to the script on purpose (an env-settable path would let a session point the hook at its own permissive file). A command matching both tiers is DENIED (deny checked first). All matching case-insensitive. | Egress can exfiltrate — including the cmdlet and inline-interpreter bypasses of the named-tool curl/wget match — but it is reviewable-before-run, so it asks instead of dead-ending authorized work into "disable the hook and restart" (the v3.0-95 incident). Destructive commands can wipe state and are unrecoverable, so they stay denied. Inline `-c`/`-e` only (scripts like `python build.py` are allowed); the root-targeting rule matches only a bare root token (`C:\`, `/`, ...) with nothing after it — inert-unless-real-risk. |
| block-env-writes.sh | `Edit\|Write` | Blocks Edit/Write operations on `.env*` files **except `.env.example` and `.env.sample`** (exit 2), and — since v3.0.36 (backlog v3.0-98(a)) — **every path under `core/security/hooks/`** (the hook scripts, fixtures, and `egress-allowlist.txt`): the perimeter's own files are operator-edited only; a session proposes, the operator applies. **Honest limit:** this guards the Edit/Write tool path; a shell-redirection write (`echo >> …`) rides Bash/PowerShell instead — the sweep's allowlist-surfacing line (sweep SKILL step 17) is the backstop that shows the operator any allowlist change once. Adoption sessions copying UPDATED hook files from a newer template do it via shell `cp` (upstream-authored bytes, visible in the diff), never by authoring hook content in-session. | Defense in depth on `.env`; perimeter-integrity on the hooks dir — one appended allowlist regex would convert the ask tier back to silent-allow for a chosen destination (the session contract's "enforce mechanically" doctrine, applied to the perimeter itself). |
| scan-staged-secrets.sh | **none — a real `git` pre-commit hook**, installed by init's hooks step into `.git/hooks/pre-commit` | **Fails any commit (exit 1) whose staged diff adds secret-shaped content** — key-material blocks (PEM/PPK), known-prefix tokens with length/charset teeth (AWS/GitHub/Anthropic/OpenAI/Slack/Stripe/Google/JWT), embedded-credential URLs, and credential files by path (`.env*` except example/sample, `*.pem`/`*.key`/`*.ppk`, `credentials.json`; `credential-bindings.yaml` deliberately passes — destinations, never values). Placeholder-shaped values (`REDACTED`, `<angle-bracket>`, mustache-style double-brace markers, `xxx…`, …) pass, checked against the matched value only; the perimeter's own `test-inputs/` dir and the scanner's own source at its canonical path are exempt by hard-coded path (v3.0-104 — the scanner's pattern table looks secret-shaped to itself, and the hooks dir is agent-write-guarded, so its own file cannot be a smuggling channel; the same bytes under any other path still block). Gates EVERY commit — the operator's own included (ratified option 1, 2026-08-11). **Bypass:** `git commit --no-verify` is git's own and stays in the operator's hands; agent sessions are barred from it mechanically (the DENY tier above). Battery: `bash scan-staged-secrets.sh --self-test` (31 cases, both directions per class; fixtures GENERATED at run time — committed secret-shaped bytes would trip GitHub push protection on the public mirror and read as a real leak, so the fixture-commitment rule is satisfied by the embedded battery here, deliberately). | Closes the last unguarded exfiltration lane: since v3.0.33 egress asks and push is sanctioned (v3.0.19), secret-in-commit-then-push was the one silent path off-machine. |

## How to test a hook

Hooks are pure stdin → (exit code + stdout) functions. Three observable outcomes for
`block-dangerous-bash.sh` since v3.0.33: **DENY** = exit 2, refusal on stderr; **ASK** = exit 0
with the PreToolUse `"ask"` JSON on stdout; **allow-silent** = exit 0, empty stdout.

```bash
# Egress family: ASK since v3.0.33 (was DENY) -- exit 0 + ask JSON on stdout
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-a-curl.json
# expect: {"hookSpecificOutput":{...,"permissionDecision":"ask",...}} ; Exit 0

./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-wget.json      # ASK
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-nc.json        # ASK
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-netcat.json    # ASK (distinct
                  # pattern entry from nc above; a command naming only "nc" does not
                  # exercise this pattern and vice versa)

./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-passing.json
echo "Exit: $?"  # expect 0, empty stdout (allow-silent)

# PowerShell-tool coverage (matcher scope, see above) -- same script, PowerShell command forms
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-ps-invoke-webrequest.json  # ASK
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-ps-iwr.json                # ASK (alias)

# Standing allowance (ASK tier only): copy the hook into a scratch dir with an
# egress-allowlist.txt beside it -- the path is fixed relative to the script by design,
# so testing an allowlist never means planting one in the real perimeter dir:
#   T=$(mktemp -d); cp hooks/block-dangerous-bash.sh "$T/"
#   printf 'curl[[:space:]]+-s[[:space:]]+https://api\\.replicate\\.com/\n' > "$T/egress-allowlist.txt"
#   echo '{"tool_input":{"command":"curl -s https://api.replicate.com/v1/models"}}' | "$T/block-dangerous-bash.sh"
#   # expect: exit 0, EMPTY stdout (allowed silently); any other curl still ASKs

# Destructive family: DENY, unchanged -- exit 2, no allowlist, no ask
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-rm-rf.json
echo "Exit: $?"  # expect 2 (blocked)

./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-git-reset-hard.json
echo "Exit: $?"  # expect 2 (blocked)

# A command matching BOTH tiers is denied, never asked:
echo '{"tool_input":{"command":"curl -s http://x/ && rm -rf /"}}' | ./hooks/block-dangerous-bash.sh
echo "Exit: $?"  # expect 2 (deny checked first)

./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-ps-removeitem-root.json
echo "Exit: $?"  # expect 2 (blocked -- Remove-Item -Recurse -Force C:\)

./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-ps-removeitem-root-path-flag.json
echo "Exit: $?"  # expect 2 (blocked -- -Path C:\ -Recurse -Force, flags reordered)

./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-ps-removeitem-root-alias.json
echo "Exit: $?"  # expect 2 (blocked -- rm alias for Remove-Item)

./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-ps-removeitem-posix-root.json
echo "Exit: $?"  # expect 2 (blocked -- bare POSIX root)

./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-ps-removeitem-scratchpad-passing.json
echo "Exit: $?"  # expect 0 (allowed -- scratchpad-scoped, not a bare root; inert-unless-real-risk)

./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-ps-removeitem-relative-passing.json
echo "Exit: $?"  # expect 0 (allowed -- relative path)

./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-ps-removeitem-single-file-passing.json
echo "Exit: $?"  # expect 0 (allowed -- no -Recurse, single file)

./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-ps-getchilditem-passing.json
echo "Exit: $?"  # expect 0 (allowed -- not Remove-Item at all)

./hooks/block-env-writes.sh < hooks/test-inputs/test-env-example-edit.json
echo "Exit: $?"  # expect 0 (allowed — .env.example is exempt)

./hooks/block-env-writes.sh < hooks/test-inputs/test-env-edit.json
echo "Exit: $?"  # expect 2 (blocked)

# Agent --no-verify bar (v3.0.36): commit-scoped DENY; push -n is a dry-run and passes
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-git-commit-no-verify.json   # DENY (2)
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-git-commit-n-alias.json     # DENY (2)
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-git-commit-passing.json     # allow (0)
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-git-push-dry-run-passing.json # allow (0)

# Perimeter write-guard (v3.0.36, v3.0-98(a)): the hooks dir is operator-owned
./hooks/block-env-writes.sh < hooks/test-inputs/test-hooks-allowlist-write.json        # DENY (2)
./hooks/block-env-writes.sh < hooks/test-inputs/test-hooks-script-write-winpath.json   # DENY (2)
./hooks/block-env-writes.sh < hooks/test-inputs/test-write-elsewhere-passing.json      # allow (0)

# Pre-commit secret scanner (v3.0.36): full battery, scratch repos, both directions
bash hooks/scan-staged-secrets.sh --self-test   # expect PASS (31/31)
```

## Adding a new hook

1. Identify the named risk. Hooks without a named risk get pruned at next review.
2. Author as stdin → exit-code bash script in `hooks/`.
3. Add test input fixtures — **both positive (allowed) and negative (blocked) cases**.
4. Wire in `settings.local.json` under `hooks.PreToolUse`.
5. Document risk and tests in this README.

## Rules

- Hooks are mechanism, not vibes. If you can't state what they prevent, they don't belong.
- Hooks must have committed test fixtures covering both blocked AND legitimate edge cases.
- Hooks must run on Windows (Git Bash or WSL) and Unix bash. Avoid GNU-specific flags.
