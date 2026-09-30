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
- `codex` and/or `claude` CLIs on PATH and logged in (subscriptions). `cross-check` (Claude→GPT) needs
  `codex`; the future Codex-side mirror (Codex→Claude) uses `claude`. With neither installed the skills
  **fail loud** — they never silently pass.
- `init` runs a **preflight** at instantiation that prints whether `node`/`codex` are present, so you
  know up front whether these skills are ready or inert (a missing prereq leaves them inert, not broken).

## Verify (from the project root, post-init)
```
node .claude/skills/bridge/verify-cli.js --help
bash .claude/skills/cross-check-loop/selftest.sh         # 26 offline gate tests, no network
# live smoke (codex logged in): expect verdict + verifier.vendor=openai
node .claude/skills/bridge/verify-cli.js --claim "Array.prototype.flat() defaults to depth 2" \
     --evidence "ECMAScript: Array.prototype.flat() default depth is 1." --tier T4
```

## Which model verifies (v3.0.58, backlog v3.0-204)
No leg pins a model id. Each asks `models.js`, which answers from the first of: the caller's `--model` /
the leg's env var (`VERIFY_MODEL`, `HANDOFF_LEG_MODEL`) → the OPTIONAL operator registry
`~/.rheoscope/frontier-models.json` (`{"openai": "…", "xai": "…", "anthropic": "…"}`, outside every repo,
for deliberate overrides only) → the provider CLI's OWN current default (`~/.codex/config.toml` `model`;
`grok models`; `~/.claude/settings.json` `model`) → a shipped fallback (for Anthropic the alias `fable`,
which the claude CLI maps to its newest top-tier model). So when a provider ships a new model and its app
or CLI moves its default, every leg follows with no harness change. See what each provider resolves to:
`node .claude/skills/bridge/models.js` (the doctor's check 18 shows the same, and WARNs when a registry
override lags the CLI's own default). The CLI binary is resolved the same way: the NEWEST installed
codex / claude CLI wins (the desktop apps keep bundled, current copies under `%LOCALAPPDATA%\OpenAI\Codex\bin\`
and `%APPDATA%\Claude\claude-code\`), because a new model usually needs a new CLI.

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
