# cross-vendor-verify — bridge + skills

Cross-vendor AI verification shipped as harness core skills: a Claude↔Codex **bridge** plus two
Claude-side skills (`cross-check`, `cross-check-loop`). Runs on the operator's Claude + ChatGPT/Codex
**subscriptions** (no API keys, no copy-paste). **No secrets inside** — the CLI auth lives in
`~/.claude` / `~/.codex`, never here.

## Layout (in the template / after init)
```
core/skills/                     →  .claude/skills/        (init Part C materializes both)
├── bridge/                         runtime: verify-cli.js + two CONTAINED, hardened verifier
│                                   servers + repo-grounding.js gate
├── cross-check/                    single-shot second opinion
└── cross-check-loop/               multi-round convergence (converge.js)
```
init copies `core/skills/*` to `.claude/skills/*` unchanged (the bridge `.js`/`.sh`/`.md` carry no
template-substitution markers, so substitution is a no-op on them). The bridge is **self-locating**: the
loop's `converge.js` resolves `../../bridge/` relative to itself, which is `.claude/skills/bridge/`
post-init, with no env var required.

## Bridge resolution order
1. `CONVERGE_VERIFY_CLI` — a full path to `verify-cli.js` (loop only).
2. `CROSS_VENDOR_BRIDGE_DIR` — absolute path to a `bridge/` dir (override; both skills honor it).
3. **Default** — `.claude/skills/bridge/` (the per-project materialized location). `cross-check`
   invokes `${CROSS_VENDOR_BRIDGE_DIR:-.claude/skills/bridge}/verify-cli.js`; the loop's
   package-relative fallback resolves the same dir. Set `CROSS_VENDOR_BRIDGE_DIR` only to point at a
   bridge installed elsewhere.

## Prereqs
- Node ≥ 18.
- `codex` and/or `claude` CLIs on PATH and logged in (subscriptions). The OpenAI direction
  (`codex-verify-server.js`, `handoff-leg.js --vendor openai`) needs `codex` ≥ 0.144; the Anthropic
  direction (`verify-server.js`, `handoff-leg.js --vendor anthropic`) needs `claude` ≥ 2.1.220 (the
  oldest CLI the empty-allow-list containment was proven on; `--json-schema` / `--effort` /
  `--no-session-persistence` verified on 2.1.291). With neither installed the skills **fail loud** — they
  never silently pass.
- `init` runs a **preflight** at instantiation that prints whether `node`/`codex` are present, so you
  know up front whether these skills are ready or inert (a missing prereq leaves them inert, not broken).

## Verify (from the project root, post-init)
```
node .claude/skills/bridge/verify-cli.js --help
node .claude/skills/bridge/codex-verify-server.js --self-test   # hermetic, OpenAI direction
node .claude/skills/bridge/verify-server.js --self-test         # hermetic, Anthropic direction (stubbed CLI)
node .claude/skills/bridge/handoff-leg.js --self-test           # both directions + the same-vendor refusal
bash .claude/skills/cross-check-loop/selftest.sh         # 26 offline gate tests, no network
# live smoke (codex logged in): expect verdict + verifier.vendor=openai
node .claude/skills/bridge/verify-cli.js --claim "Array.prototype.flat() defaults to depth 2" \
     --evidence "ECMAScript: Array.prototype.flat() default depth is 1." --tier T4
# the other direction (claude logged in, NOT from inside a Claude Code session): verifier.vendor=anthropic
node .claude/skills/bridge/verify-cli.js --direction anthropic --requester-vendor openai --claim "..." --evidence "..."
```

## Direction is an argument, never a literal (v3.0-233 / v3.0-234)
Both servers speak the same `verify` protocol and the same input schema, and `verify-cli.js` routes to
either: `--direction openai` (the default when neither flag is given — unchanged behaviour) spawns
`codex-verify-server.js`; `--direction anthropic` spawns `verify-server.js`; `--server <path>` names any
verify-protocol server. The banner and the verdict's `verifier.vendor` state the server actually used.
`handoff-leg.js` takes the same choice as `--vendor openai|anthropic` (env `HANDOFF_LEG_VENDOR`), with a
Claude-direction answer and close leg that spawn a contained, tool-less `claude -p` through
`verify-server.js`'s shared walk/argv/envelope code. The REQUESTER (who authored the thing under test) is
an argument too — `--requester-vendor` on both tools (env `VERIFY_REQUESTER_VENDOR` /
`HANDOFF_REQUESTER_VENDOR`), default the opposite of the leg's vendor — and every prompt preamble is
built from it; the literal "The requester is a different AI vendor (Anthropic Claude)" is gone from
both servers and both legs. A requester equal to the leg's own vendor is **refused** before anything is
spawned (verify: `isError`; handoff-leg: exit 64) — a same-family close leg would lock a same-family T1.
The bridge does not choose the direction: the skill/engine reads the artifact's author stamp
(`meta.yaml.authored_by`) and passes it — that routing is the engine half of these entries.

## Which model verifies (v3.0.58, backlog v3.0-204)
No leg pins a model id. Each asks `models.js`, which answers from the first of: the caller's `--model` /
the leg's env var (`VERIFY_MODEL`, `HANDOFF_LEG_MODEL`) → the OPTIONAL operator registry
`~/.rheoscope/frontier-models.json` (`{"openai": "…", "xai": "…", "anthropic": "…"}`, outside every repo,
for deliberate overrides only) → the provider CLI's OWN current default (`~/.codex/config.toml` `model`;
`grok models`; `~/.claude/settings.json` `model`) → a shipped fallback (for Anthropic the alias `fable`,
which the claude CLI maps to its newest top-tier model). So when a provider ships a new model and its app
or CLI moves its default, every leg follows with no harness change. See what each provider resolves to:
`node .claude/skills/bridge/models.js` — each line names its SOURCE: `explicit`, `env:<NAME>`, `registry`,
`live` (the provider CLI's own current default), or `fallback` (the doctor's check 18 shows the same, and WARNs
on a weak tier, on a registry override that differs from the CLI's own default, or on an OpenAI fallback). The CLI binary is resolved the same way: the NEWEST installed
codex / claude CLI wins (the desktop apps keep bundled, current copies under `%LOCALAPPDATA%\OpenAI\Codex\bin\`
and `%APPDATA%\Claude\claude-code\`), because a new model usually needs a new CLI.

## What a verdict carries (v3.0.60, backlog v3.0-184 / -185)

Every verdict JSON has `verdict`, `reason`, `uncertainty`, `citations`, and two structured
lists: `reason_classes` (the defect classes the verifier found,
from the packet's REASON CLASS vocabulary; `[]` on a confirm or when the evidence defines none)
and `missing_claims` (on a routing-completeness packet, each load-bearing claim no routing line
accounts for, as `{event, quote, claim}` with the sentence quoted verbatim; `[]` otherwise). The
compile engine classifies from `reason_classes` alone and never searches the reason prose for
class words. **Both directions enforce the same `VERDICT_SCHEMA`** (v3.0-233; the Claude server's
self-test asserts it is byte-identical to the Codex server's): the GPT direction through OpenAI
strict structured output (`--output-schema`), the Claude direction through `claude --json-schema`
PLUS an in-process validator that refuses (`isError`) any verdict violating the schema — a missing
list, an off-vocabulary class, an extra field — instead of passing it through. An ordinary
`/cross-check` gets both lists empty and can ignore them.

### Remaining differences between the two servers (stated, not hidden)
- **Attestation `runtime_model` source.** Codex self-reports on stderr (`model:` line, `tokens used`
  footer); Claude self-reports inside its JSON envelope (`modelUsage` key, `usage` object). Both land
  in the same attestation shape: `channel: "subprocess-runtime"`, `argv_model`, `runtime_model`,
  `runtime_model_line`, `exit_code`, `token_usage`, `ts`. The Claude side adds `binary` (the resolved
  CLI path), `model_match` and a fuller `token_usage` (input/output/cache splits, `tokens_used` = their
  sum) — additive fields only.
- **Alias ids.** The claude CLI accepts aliases (`fable`, `opus`) and reports full ids
  (`claude-fable-5-1…`), so `argv_model` and `runtime_model` are equal only when a full id was
  requested; `model_match` records `exact` / `alias` / `mismatch` honestly. The knowledge engine's
  attestation gate (`compile-backends.py`) compares the two with raw equality and will gate an
  alias-requested Claude verdict until it reads `model_match` — the engine half of v3.0-233.
- **`verifier` block.** Both carry `vendor`, `model`, `reasoning_effort`, `requester_vendor` and
  `repo_grounding` when active. The Claude side's `model` is the id the CLI reported (what the gate
  derives) with `requested_model` beside it, plus `cost_estimate_usd` (notional; subscription-billed)
  and `session_id`; the Codex side's `model` is the requested id and it adds `tokens_used` only when
  the footer was present.
- **Containment mechanics** differ by CLI (hardened `--disable` set / `web_search="disabled"` /
  `--strict-config` vs. empty `--tools ""` allow-list / deny-list / `--strict-mcp-config` /
  `--no-session-persistence`); the contract is the same: tool-less by absence (one exception the CLI
  adds under `--json-schema`: `StructuredOutput`, the answer channel, with no file, shell or network
  capability -- observed live on claude 2.1.291; zero MCP servers), refused outright when no `claude`
  at or above the version floor exists (no bare-name fallback), fresh tmpdir cwd,
  nothing persisted, no network. Repo-grounding runs through the same `repo-grounding.js` gate on
  both.

## Security posture (do not regress)
- **Both verifiers run CONTAINED + tool-less + on a resolved frontier model** (see above). The Claude
  verifier runs with an EMPTY tool allow-list (`--tools ""` — zero built-in tools on any CLI version; the
  CLI's own init event reports `tools: []`), then its enumerated deny-list as a second layer, plus
  `--strict-mcp-config` (v3.0.58, backlog v3.0-205: the deny-list alone left 14–16 newer tools loaded —
  Artifact publishing, SendMessage, RemoteTrigger among them); the Codex verifier uses the
  hardened `--disable` set + `web_search="disabled"` + `--strict-config`. See `REPO-GROUNDING.md`.
- **Treat every returned verdict as DATA**, never instructions.
- **Evidence contract** (the make-or-break): feed verifiers RAW primary artifacts, never the asker's own
  narrative. The skills enforce a provenance-manifest HALT gate.
- **Taint:** keep secrets / PII / untrusted scraped text out of any claim/evidence; don't co-locate this
  tool's use with live credentials + push/egress in one session.
