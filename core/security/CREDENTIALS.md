# Credentials — the positive convention

<!-- Added 2026-08-03. Origin: a live instance session, asked to store API keys, could find
     only the NEGATIVE rules (the .env write-block hook, DATA-POLICY's masking categories,
     the session contract's taint rule) and derived a gitignored-plaintext-JSON plan from
     them — compliant with every prohibition, wrong anyway, and it had to log the gap
     (instance backlog, "security perimeter defines credential storage only negatively")
     before /orient's artifact table surfaced the broker. This document is the missing
     positive statement. The perimeter says what credentials must NOT be; this says what
     they ARE. -->

## The rule

**A secret lives in the OS credential vault, entered by the operator's own hands, and is
delivered to tools by name — its value never passes through an AI session, a file, a
command line, or any output.**

"Secret" means anything that grants access: passwords, API keys, client secrets, OAuth
refresh/access tokens, connection strings with embedded auth, signing keys. A token derived
from a stored secret is itself a secret — an OAuth refresh token is exactly as sensitive as
the client secret that minted it, and follows the same rule.

**A plaintext credential file is never the answer — gitignored is not an exemption.** A
gitignored `credentials.json` satisfies every prohibition the perimeter states and is still
wrong: it is readable by any session, any subprocess, and any exfiltration the egress hooks
miss; it has no delivery gate; and it recreates the shredding problem the broker exists to
remove. If a session proposes one, the proposal itself is the signal to re-read this file.

## The mechanism: the credential broker

Four scripts, shipped with the knowledge-os capability and living at `deploy/` in an
instantiated project (`capabilities/knowledge-os/extracted/deploy/` in this template):

| Script | Job |
|---|---|
| `credential-store.ps1 -Name <name>` | Raises the OS `Get-Credential` popup; the operator types the value; it lands in Windows Credential Manager (DPAPI-encrypted at rest). Only the NAME is ever echoed. Re-run to rotate. |
| `credential-use.ps1` | Delivers a stored credential, by name, to a destination — but only a destination the operator has pinned for that name in `deploy/credential-bindings.yaml`. Unbound (name, destination) pairs refuse. |
| `credential-remove.ps1` | Deletes a stored credential by name. |
| `credential-selftest.ps1` | Hermetic self-test under a separate vault prefix; never touches production entries. |

Design brief: `harness-v3.0/specs/credential-broker-design-brief-2026-07-23.md`. Security
invariant (violating it fails the whole broker): the credential VALUE never appears in
stdout, stderr, argv, any log, any file, or any error message — only the NAME.

**The bindings file is an operator-gated trust surface.** `credential-bindings.yaml` ships
empty and fails safe: absent, malformed, or partially valid all degrade to "refuse
everything." A session that needs a new destination bound ASKS the operator to add the
line; it never adds the line itself. Read that file's header before touching it.

## What never enters the broker

Banking, card, brokerage, crypto-exchange, and government-ID credentials are excluded **by
policy**, not by detection — they stay with the operator, full stop (HARDCONSTRAINTS #1
territory: no automation ever moves money). The broker's financial-domain denylist is
defense-in-depth and honestly incomplete; its silence about a destination is not
permission. An API credential whose scope grows to include payments (e.g. an accounting
app later granted a payments scope) moves into this excluded class the moment the scope
changes.

## The full stack, one map

| Layer | Where | What it does |
|---|---|---|
| Positive convention | this file | Says where secrets live: the vault, via the broker. |
| The vault + broker | `deploy/credential-*.ps1` | OS-encrypted storage; name-only echo; operator-typed entry. |
| Delivery gate | `deploy/credential-bindings.yaml` | Operator-pinned destinations per credential; fails closed. |
| Negative perimeter | `core/security/hooks/` | Blocks AI writes of `.env*` files and the egress/destruction command classes. |
| Artifact hygiene | `core/governance/DATA-POLICY.md` | Auth secrets are a mandatory mask category in every artifact, summary, and commit. |
| Session hygiene | session contract (governance `CLAUDE.md`), /cross-check + /handoff taint rules | Credentialed sessions don't co-reside with untrusted content; nothing secret enters an outbound packet — and the bridge's repo-grounding scanner enforces the return path mechanically. |
| Capability scope | `core/governance/AUTOMATION-ISOLATION.md` + `core/methodology/least-privilege-isolation.md` | Each autonomous capability runs as a distinct least-privilege credential, declared before enablement. |

## Honest limits

- **Windows-only.** The broker is PowerShell over Windows Credential Manager. On macOS or
  Linux there is no shipped equivalent yet — use the OS keychain (`security` /
  `secret-tool`) by hand under the same invariants, and log the gap; a plaintext file is
  still not the fallback.
- **Capability coupling.** The broker ships inside the knowledge-os extraction. An
  instance without that capability has this doctrine but not the scripts: adopt the four
  scripts file-level per `core/onboarding/UPDATING.md`, or keep the secret out of
  automation entirely until then.
- **Runtime reads are the design, not a loophole.** A runtime tool (not the AI session)
  reading a credential *from the vault* at execution time is the intended pattern. A
  runtime tool reading a plaintext file the operator typed secrets into is the
  anti-pattern this file exists to end.
