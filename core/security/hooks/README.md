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

## The trust-surface class (v3.0.46, backlog v3.0-120 — ADR #11's Release-2 enablement gate)

A short list of paths decides what a session may do. Before v3.0.46 three of them were
writable by the session they govern (shell redirection rode the Bash lane past the
Edit/Write guard; `deploy/safe-allowlist.yaml` had a header ban and no enforcement; the
HUMAN-GATE `operator-*.md` artifacts claimed "committed" in prose and were checked
nowhere; the hook WIRING in `.claude/settings*.json` was in no guard at all). They are
now one named class, **`trust-surfaces.txt`** beside this file (one glob per line; itself
in the class), read by both hooks, by `deploy/trust.py` and by `/doctor` check 16 — always
by fixed relative path, always in UNION with the same hard-coded floor each reader
carries, so an absent or emptied file never narrows the class (fail-closed; the hook
batteries pin file == floor).

| Layer | What it does | What it does NOT do |
|---|---|---|
| **Mediated lanes refuse** (both hooks) | Edit/Write on any class path → DENY. Bash/PowerShell: any *write-shaped* command naming a class path → DENY (`>`/`>>` except the harmless `2>/dev/null`/`2>&1`/`>NUL`; tee/cp/mv/rm/install/truncate/ln/dd/rsync; `sed -i`/`perl -i`; Set-Content/Add-Content/Out-File/Copy-Item/Move-Item/Remove-Item/New-Item/Clear-Content/Rename-Item + aliases; `[IO.File]::Write*`; `py/python -c` / `node -e` that so much as mentions the path). Reads pass (cat/grep/ls/diff/git show/log/diff/`sed -n`, running the sensor as a script). DENY not ASK: perimeter edits are operator-out-of-session; there is no in-session "yes". | Stop a composed spelling (`$(echo …)`, concatenated fragments, a script the agent writes and then runs). A regex is a tripwire, never a bar. |
| **Unmediated writes are non-authoritative** (`deploy/trust.py`) | Every HUMAN-GATE consumer calls `gate_artifact`: the artifact must be **committed-identical** (tracked, worktree == HEAD) — always — and **operator-signed** (its newest commit's SSH signature verifies against `allowed_signers` with a presence-requiring `sk-` key; software keys are filtered OUT of the pin before ssh-keygen sees them) — refused under `project.yaml: trust_surface_signing: required`, surfaced under `warn`, not expected under `visible` (v3.0.49: the authority mode is an explicit one-time operator choice recorded at init; ABSENT = migration-only `warn` with retirement disabled — `trust.py` refuses publication, doctor 16 and sweep 17 name the missing choice). The pin's own history is chain-verified: a commit changing `allowed_signers` must be signed by a key its PARENT's pin lists (bootstrap = the first such commit, signed by a key it lists itself); merges carrying a pin change, deletions and delete-recreate are refused. | Survive an agent patching `trust.py` or the consumer — both are in the class, so the patch is caught by check 16(a) when an honest sensor runs, but a tampered verifier can lie. That is why this layer is not the root. |
| **The root under `required`: the operator's physical key** (optional hardening since v3.0.49 — under `visible`, ADR #11 condition 4 as amended 2026-08-22, the root is reversibility + visibility: every change is journaled and surfaced until acknowledged, and a retirement waits for one exact-digest promote action outside the session, Release 2) | A trust-surface change, and (Release 2) a retirement, is authoritative only as an operator-signed commit/tag — `git commit -S` / `git tag -s retire/<seq> C` with a FIDO sk key, in the operator's own terminal, after inspecting the exact immutable object. `trust.py --check-publish` permits the production fast-forward only when the tag verifies, names `C` exactly (embedded tag name == ref name), `C` is a single commit atop the production head, and `C`'s tree carries the retire record whose `proposal_digest` equals the digest of the proposal in the same tree, not yet consumed. All reads use `git --no-replace-objects`. | Prevent a same-user agent from moving a ref locally. It cannot make the result authoritative: authority is the tag, not the ref, and the sweep names any `retire` record without a verified tag as an unpublished proposal. Server-side branch protection remains the stricter option on record. |

Battery: `bash block-env-writes.sh --self-test` (53: every surface deny + allow-sibling, path
spellings, `.env` rules, class-file == floor, all fixtures), `bash block-dangerous-bash.sh
--self-test` (169: the prior 84 + write-shaped × surface, read negatives, the Remove-Item
rider on `COMMAND_NORM`, 19 new fixtures), `python deploy/trust.py --self-test` (92 —
see its closing NOTE on what a battery can and cannot exercise without a FIDO token).
Acceptance artifact: `harness-v3.0/stranger-tests/v3.0.46-adversarial-e2e-*.md` (dev repo).

## Hooks in this perimeter

| Hook | Matchers required | Behavior | Risk |
|------|--------------------|--------|------|
| block-dangerous-bash.sh | `Bash`, `PowerShell` (both) | **Two tiers (v3.0.33, backlog v3.0-95 — the v3.0.19 deny-only-the-unrecoverable doctrine applied to the hook layer).** **DENY (exit 2):** destructive commands — `rm -rf /`, `git reset --hard`, the PowerShell analog `Remove-Item -Recurse -Force <bare drive/POSIX root>` (matched on the quote-normalized command since v3.0.46), the agent `--no-verify` bar, and — since v3.0.46 (v3.0-120) — **any write-shaped command naming a trust-surface path** (see the class section above). No allowlist, no ask — there is no legitimate unattended "yes" to these. **ASK (exit 0 + PreToolUse `"ask"` JSON) — UNATTENDED runs only since v3.0.47 (backlog v3.0-134; the 2026-08-22 five-pass run): in an ATTENDED session every egress-shaped call is ALLOWED and appended to `.claude/egress-log.jsonl` (per-machine, gitignored, path fixed relative to the hook), which `/sweep` step 18 renders as one table per host; unattended := the scheduled wrapper sets `RHEOSCOPE_UNATTENDED=1` or the payload's `permission_mode` is `dontAsk`, and there the ask fails closed as before. Every ask and every deny is logged too (v3.0-136 telemetry). Reason, stated plainly: the threat on a one-operator harness is a misled or wrong session, not an adaptive adversary; the per-call prompt never stopped a composed spelling and its tax trained a reflexive Allow. The ask tier's original contract, now unattended-only:** network egress — `curl`/`wget`/`nc`/`netcat`, PowerShell `Invoke-WebRequest`/`Invoke-RestMethod`/`Start-BitsTransfer`/`irm`/`iwr`, and interpreter one-liners (`py`/`python`/`python3 -c`, `node -e`) **when they carry a network-shaped token** (v3.0.43, backlog v3.0-124 — operator-ratified): `urllib`/`requests`/`socket`/`http(s)`/`fetch`/`axios`/`require(net|http|tls|dgram)`/etc., matched on the quote-normalized command. A provably-local one-liner (`py -c "import json; json.load(open('x.json'))"`) passes silently — asking on every inline call taxed the operator with false positives, and prompt fatigue trains a reflexive Allow, which erodes the tier it was meant to strengthen. **Stated honestly: this NARROWS ask coverage** — pre-v3.0.43 every inline `-c`/`-e` prompted; a token-absent egress spelling (exotic stdlib API, composed import) now passes where it previously asked. That is a deliberate operator-ratified loosening (v3.0-124), same trade class as v3.0.33's deny→ask; the token list is a floor of common spellings, not an enumeration. Sessions should still prefer the Read tool / `jq` over interpreter one-liners for local file reads. The operator reviews the exact command and approves or declines that one call; **an unanswered ask fails closed**, so unattended runs stay fully perimetered. Standing allowances live in `egress-allowlist.txt` beside the script (one extended regex per line, consulted by the ASK tier only, matched-command allowed silently) — **operator-edited only**, same doctrine as `credential-bindings.yaml`; the path is fixed relative to the script on purpose (an env-settable path would let a session point the hook at its own permissive file). A command matching both tiers is DENIED (deny checked first). All matching case-insensitive. **Battery:** `bash block-dangerous-bash.sh --self-test` (v3.0.43; 84 assertions, grown to 169 in v3.0.46) — embedded driver + all committed fixtures pinned, intercepted before the stdin read so the PreToolUse path is byte-unaffected. | Egress can exfiltrate — including the cmdlet and inline-interpreter bypasses of the named-tool curl/wget match — but it is reviewable-before-run, so it asks instead of dead-ending authorized work into "disable the hook and restart" (the v3.0-95 incident). Destructive commands can wipe state and are unrecoverable, so they stay denied. Inline `-c`/`-e` only (scripts like `python build.py` are allowed); the root-targeting rule matches only a bare root token (`C:\`, `/`, ...) with nothing after it — inert-unless-real-risk. |
| block-env-writes.sh | `Edit\|Write` | Blocks Edit/Write operations on `.env*` files **except `.env.example` and `.env.sample`** (exit 2), and — since v3.0.36 (backlog v3.0-98(a)), generalized by v3.0.46 (v3.0-120) — **every path in the trust-surface class** (`trust-surfaces.txt` ∪ the embedded floor: this directory, `deploy/safe-allowlist.yaml`, `deploy/evidence/operator-*.md`, `deploy/rulings/**`, `deploy/trust.py` + the three HUMAN-GATE consumers, `.claude/settings*.json`, `.git/hooks/**`, `.gitattributes`): operator-edited only; a session proposes, the operator applies and signs. **Honest limit:** this guards the Edit/Write tool path; the Bash/PowerShell lane has its own DENY rule (above); an unmediated write is caught by `/doctor` check 16 and refused by every honest consumer (`deploy/trust.py`). Adoption sessions no longer `cp` hook files either — **the operator copies** the template's bytes and commits them with `-S` (MIGRATION v3.0.45→46). | Defense in depth on `.env`; perimeter-integrity on the whole class — one appended allowlist regex, one widened safe-allowlist entry, one self-authored authorization artifact, or one unwired hook entry would each loosen the perimeter a session runs under (the session contract's "enforce mechanically" doctrine, applied to the perimeter itself). |
| scan-staged-secrets.sh | **none — a real `git` pre-commit hook**, installed by init's hooks step into `.git/hooks/pre-commit` | **Fails any commit (exit 1) whose staged diff adds secret-shaped content** — key-material blocks (PEM/PPK), known-prefix tokens with length/charset teeth (AWS/GitHub/Anthropic/OpenAI/Slack/Stripe/Google/JWT), embedded-credential URLs, and credential files by path (`.env*` except example/sample, `*.pem`/`*.key`/`*.ppk`, `credentials.json`; `credential-bindings.yaml` deliberately passes — destinations, never values). Placeholder-shaped values (`REDACTED`, `<angle-bracket>`, mustache-style double-brace markers, `xxx…`, …) pass, checked against the matched value only; the perimeter's own `test-inputs/` dir is exempt by hard-coded path. The scanner's own source at its canonical path gets the **known-own-lines rule** (v3.0-104/v3.0-109): added lines present verbatim in the RUNNING hook script pass as its own content; every other added line — an appended secret, an embedded line in a re-added copy — is scanned normally, so the file can never be a smuggling channel even through the shell-write path the Edit/Write guard doesn't mediate; the same bytes under any other path still block. Gates EVERY commit — the operator's own included (ratified option 1, 2026-08-11). **Bypass:** `git commit --no-verify` is git's own and stays in the operator's hands; agent sessions are barred from it mechanically (the DENY tier above). Battery: `bash scan-staged-secrets.sh --self-test` (47 cases, both directions per class, incl. six END-TO-END hook-mediated commits; fixtures GENERATED at run time — committed secret-shaped bytes would trip GitHub push protection on the public mirror and read as a real leak, so the fixture-commitment rule is satisfied by the embedded battery here, deliberately). | Closes the last unguarded exfiltration lane: since v3.0.33 egress asks and push is sanctioned (v3.0.19), secret-in-commit-then-push was the one silent path off-machine. |

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

# THE WHOLE BOARD IN ONE COMMAND (v3.0.43, extended v3.0.46): 169 assertions -- 96
# embedded cases (DENY + its negatives, named-tool ASK, inline-with-token ASK,
# inline-without-token silent, trust-surface writes per surface + their read
# negatives, the Remove-Item rider) plus every committed fixture (73) against its
# pinned expectation. The driver lives in the hook itself, like
# scan-staged-secrets.sh --self-test.
./hooks/block-dangerous-bash.sh --self-test
# expect: "block-dangerous-bash self-test: N passed, 0 failed" (N printed by the board; 169 at
# v3.0.46, higher from v3.0.47 when the attended/log cases joined), exit 0
# The board runs from a TEMP copy of the repo shape so the hook's fixed-relative egress log
# lands in the temp tree; egress cases run UNATTENDED (RHEOSCOPE_UNATTENDED=1) where the ASK
# tier lives, and an attended block pins allow+log with the row shape.

# v3.0.47: the same curl ATTENDED is silent and logged; UNATTENDED it asks
# echo '{"tool_input":{"command":"curl -s https://api.example.com/v1"}}' | ./hooks/block-dangerous-bash.sh            # exit 0, empty; one row in .claude/egress-log.jsonl
# echo '{"tool_input":{"command":"curl -s https://api.example.com/v1"}}' | RHEOSCOPE_UNATTENDED=1 ./hooks/block-dangerous-bash.sh  # ask JSON
# (degrades honestly with a NOTE to embedded cases only if test-inputs/ is absent)

# The Edit/Write lane has its own board (v3.0.46): every trust surface deny +
# allow-sibling, path spellings, the .env rules, class-file == floor, all fixtures.
./hooks/block-env-writes.sh --self-test
# expect: "block-env-writes self-test: 53 passed, 0 failed", exit 0

# Trust-surface writes on the Bash/PowerShell lane: DENY; the same paths read: silent
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-trust-bash-redirect-allowlist.json   # DENY (2)
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-trust-ps-setcontent-driver.json      # DENY (2)
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-trust-py-c-open-write-allowlist.json # DENY (2)
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-trust-bash-cat-allowlist-passing.json        # allow (0)
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-trust-bash-redirect-example-sibling-passing.json # allow (0)
./hooks/block-env-writes.sh < hooks/test-inputs/test-trust-safe-allowlist-write.json          # DENY (2)
./hooks/block-env-writes.sh < hooks/test-inputs/test-trust-safe-allowlist-example-passing.json # allow (0)

# Inline interpreters: ASK only with a network-shaped token (v3.0.43, v3.0-124)
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-py-c.json               # ASK (urllib)
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-node-e.json             # ASK (http)
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-py-c-local-passing.json   # allow-silent (local json.load)
./hooks/block-dangerous-bash.sh < hooks/test-inputs/test-node-e-local-passing.json # allow-silent (local fs read)

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
bash hooks/scan-staged-secrets.sh --self-test   # expect PASS (47/47)
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
