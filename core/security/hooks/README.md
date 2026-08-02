# Security Perimeter — Claude Code Hooks

This directory holds PreToolUse hooks that block named risks before they reach the filesystem or network.

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

| Hook | Matchers required | Blocks | Risk |
|------|--------------------|--------|------|
| block-dangerous-bash.sh | `Bash`, `PowerShell` (both) | Network egress — `curl`/`wget`/`nc`/`netcat`, PowerShell `Invoke-WebRequest`/`Invoke-RestMethod`/`irm`/`iwr`/`Start-BitsTransfer`, and interpreter one-liners (`py`/`python`/`python3 -c`, `node -e`) — and destructive commands (`rm -rf /`, `git reset --hard`, and the PowerShell analog `Remove-Item -Recurse -Force <bare drive/POSIX root>`). Matched case-insensitively. | Defense in depth — egress can exfiltrate, including the cmdlet and inline-interpreter bypasses of the named-tool curl/wget match; destructive commands can wipe state. Inline `-c`/`-e` only (scripts like `python build.py` are allowed); the root-targeting rule matches only a bare root token (`C:\`, `/`, ...) with nothing after it, so scratchpad-scoped `Remove-Item -Recurse -Force` calls are deliberately left alone — inert-unless-real-risk. |
| block-env-writes.sh | `Edit\|Write` | Edit/Write operations on `.env*` files **except `.env.example` and `.env.sample`** | Defense in depth — `.env` should be gitignored but belt-and-suspenders prevents accidental commits or AI-generated overwrites. |

## How to test a hook

Hooks are pure stdin → exit-code functions. Pipe a JSON payload and check the exit code:

```bash
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-a-curl.json
echo "Exit: $?"  # expect 2 (blocked)

# Named network-egress tools, each its own DENY_PATTERNS entry (round-2 fixture-count
# reconciliation, 2026-07-25): these three had no committed fixture before this session --
# verified only by regex reasoning during development, never captured as reproducible cases.
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-wget.json
echo "Exit: $?"  # expect 2 (blocked)

./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-nc.json
echo "Exit: $?"  # expect 2 (blocked)

./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-netcat.json
echo "Exit: $?"  # expect 2 (blocked -- distinct DENY_PATTERNS entry from nc above; a command
                  # naming only "nc" does not exercise this pattern and vice versa)

./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-passing.json
echo "Exit: $?"  # expect 0 (allowed)

# PowerShell-tool coverage (matcher scope, see above) -- same script, PowerShell command forms
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-ps-invoke-webrequest.json
echo "Exit: $?"  # expect 2 (blocked -- Invoke-WebRequest full cmdlet name; distinct
                  # DENY_PATTERNS entry from the irm/iwr alias pattern below -- also had no
                  # committed fixture before this session, same reconciliation as above)

./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-ps-iwr.json
echo "Exit: $?"  # expect 2 (blocked -- Invoke-WebRequest alias)

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
