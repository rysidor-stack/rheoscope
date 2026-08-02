# Rheoscope

Most AI-run projects share the same weakness: the work happens fast, and nobody can prove what actually happened. Knowledge lives in chat transcripts that evaporate. Builds get declared done on a feeling. Decisions get re-argued every month because nobody wrote down why.

Rheoscope is a trust machine: an operating system for projects where AI does the work and a human makes only the decisions. Every fact in its knowledge base traces to a dated source. Every build starts from a written, checkable spec, and the machinery refuses to start without one. Every claim that matters gets challenged by a different vendor's AI before it counts, and the verdict is kept as a receipt. The human sees one plain-English briefing a day, plus the handful of calls that genuinely need a person.

It is a template: unpack it, answer a founding interview about your venture, and the whole factory (the knowledge engine, the spec-gated build discipline, the verification machinery, the sensors, the security perimeter) is yours, wired for that project alone.

**New here? Start with [TEMPLATE-README.md](TEMPLATE-README.md): the operator manual; the guided tour lives at `core/onboarding/TOUR.md`.**

**Template maintainers** (working on this repo itself, not an instantiated project): see [MAINTENANCE.md](MAINTENANCE.md) for the docs-truth discipline and the release ritual.

## What makes it different

This template calls itself a trust machine, and the phrase is literal. Everything in it exists to
make two kinds of claims checkable instead of asserted: what the project knows, and what it's
building. Provenance carries the first: every fact in the knowledge base traces to a dated source,
not to a model's memory of a conversation. Manifests carry the second: every build gets a
checkable spec of what it must do before anyone writes code.

> Provenance without manifests is documented ambiguity; manifests without provenance are
> unauditable assertion. The template ships both or it ships neither.

### Provenance: the knowledge engine

Most project memory lives in a chat transcript and evaporates the moment the window closes. Here,
raw notes land as append-only ledger events in `raw/`; wiki articles are compiled views over that
ledger (lenses, not the truth itself), so nothing gets to be the last word except the ledger.
Every event registers onto a chain before anything treats it as fact, which means a claim in the
wiki isn't just plausible: it's traceable back to the dated source that established it.

The harder discipline is what happens after compiling. Absorbing an event into a wiki view runs
on its own; no one has to click through routine synthesis. Checking that the absorption didn't
fabricate, omit, or leave a stale contradiction standing is the part that doesn't get to run
unsupervised: it calls a genuinely different vendor's model, and only against a recorded,
verbatim operator authorization, never a standing memory of an earlier yes. A same-vendor model
grading its own summary would pass its own mistakes; that's the failure this is built to avoid.

Underneath both is a conservation census that sorts every registered event into one of seven
accounting classes on every single run. `problems: []` and `new_holes: []` aren't fields to eyeball:
the run fails loud the moment the count stops adding up. Silent loss of a fact isn't a risk
managed down here; it's a state the census is built not to produce.

### Manifests: behavior written down before the build

The usual failure mode in AI-built software isn't bad code: it's an unstated behavior an AI
worker fills in from generic priors, because a prose spec is a compression artifact that only
works when a human decompresses it with taste and instinct a model doesn't have. The fix is
blunt: behavior gets written down as checkable rows (one row per state, rule, token, or contract)
before any build touches it, and **no build dispatch fires until every layer it touches has a
manifest.** The gate is mechanical rather than a judgment call: how deep a manifest has to be
certified scales with the tier of the work, from full adversarial certification down to a named
exemption on throwaway increments.

The layer registry that a manifest belongs to isn't a spec someone wrote up front and defended
forever: it grows the way an incident list grows. Six layers (interaction, logic, design, data,
authorization, integration) are core because every surface needs them from day one. Five more
(failure, async, migration, concurrency, format) earned ACTIVE status because something already
went wrong without them: `format` was admitted after a generated briefing quietly drifted from its
own contract; the rest trace to their own named incidents the same way. Nine more sit RESERVED:
performance, observability, data-protection, peripherals, i18n, construction, dependency,
rendering-fit, config. Each is named and reasoned about, but not built out, because no project has
incurred their cost yet. `rendering-fit` is the clean example: a build can match its frozen design
pixel-for-pixel and still truncate real content at real density, because conformance-to-design and
real-data-fitness measure different things. That layer got named the day a live production board
actually did that, not before.

Coverage is counted the same unglamorous way: rows replayed over rows total, reported alongside
the receipts of the completeness hunts that pressured the total in the first place. That's a
deliberately worse-sounding claim than "QA passed," and that's the point: "QA passed" is a claim
about how a session felt, and a feeling isn't falsifiable. A row count is. Amendments follow the
same logic: a change that invalidates a pinned row has to land in the same declared change as
every fixture it invalidates (the golden, the e2e assertion, the conformance row), not as a
manifest edit with the fallout cleaned up later. And the discipline gets checked against itself,
not just asserted: a certified 18-row surface ran the template's own twin-build experiment, two
independent builders working from one manifest set, diffed at the behavior level, and the
measurement came back at 2 rows out of 18 missed, about 11% that the cheap completeness hunts
alone would have let through (the 2026-07-23 sweep-briefing pilot; the folded result ships in
`core/methodology/manifest-format.md` §11, and the full run receipt lives beside the surface
that ran it — `receipts/sweep-briefing-twin-build-pilot-r1.md` in the dogfood instance). That
is one experiment on one surface, and the doctrine carries it at exactly that weight: a first
calibration point cited with its n, not a proven rate. The replication isn't left to memory
either — a trigger-register row proposes the second twin-build run whenever a certified surface
big enough to bear one exists, and fired the day it was declared, because one already did.

### Verification and honesty

None of this works if the model checking the work is the model that did it: same-vendor grading
is a correlated failure risk, not a hypothetical. So `/cross-check` and the compile engine's
verify leg call out to a genuinely different vendor's model through a builder/verifier firewall,
and every verdict gets kept as a receipt rather than argued over once and forgotten. Claims that
don't resolve in one round escalate: `/cross-check` for a fast single-shot opinion,
`/cross-check-loop` when a decision has several load-bearing claims and needs convergence across
rounds, and a full substrate-separated handoff when neither settles it, which locks to an ADR
that then stays settled, with the reasoning on the record, instead of getting re-litigated by
whichever session touches it next.

The template turns the same scrutiny on itself. It found real drift in its own docs this way
(prose frozen a full version behind the actual `VERSION` file, a renamed command still answering
to its old name in five-plus files), which is why a four-lens self-sweep (gap coverage,
retired-name bleed, cross-doc consistency, stamp audit) now runs before every version stamp, and
why a release doesn't ship until a fresh, zero-context instantiation gets driven twice: once to
find what breaks, once to confirm the fix actually held. Teaching docs carry `verified-against`
stamps that only get reset after someone actually re-checks the claims, never bumped as
decoration.

### Safety and operator load

The operating principle is blunt: any work that doesn't genuinely need a human gets automated,
full stop. Near five dozen read-only sensors, most self-tested, run under one umbrella
(`/sweep`) and report a single plain-English briefing that changes nothing on its own; `/doctor`
does the same for the environment: transport wiring, tool auth, every sensor's own self-test,
both security hooks' matcher coverage, doc staleness, version drift, in one pass. Everything that
reads is fully automatic. Everything that writes (compiling knowledge, regenerating indexes) runs
on a branch and asks the operator one yes/no question instead of walking them through the
mechanics. What's left for a human is what should be: decisions, ratifications, and GO calls on
anything irreversible, collected in one decision inbox instead of scattered across a census, a
candidate queue, and a deadline register the operator would otherwise have to check separately.
That register also watches approximate-month deadlines, not just exact dates, so a commitment due
months out doesn't go quiet until it's suddenly due next week.

The security perimeter runs the same logic in the other direction: assume a session will
eventually try something it shouldn't, on purpose or not, and block it at the command level
instead of trusting discipline. Two PreToolUse hooks mediate both the Bash and PowerShell paths
Claude Code exposes (wiring only one leaves the other completely open, which is exactly the gap
`/doctor` checks for), and every hook ships with committed positive and negative test cases, not
assurances remembered from development. Secrets live in an OS-native, DPAPI-encrypted vault,
referenced by name and never written to a plaintext file or printed back to a session. The signing
gate that lets a session-authored span of work count as the operator's own word ships **unarmed by
default**. That default is deliberate: installing the file changes nothing, and arming it is a
separate, explicit act. The alternative is automation that's one misconfiguration away from
acting with authority nobody actually granted it. And unarmed-by-default is a shipping invariant,
not a parking brake: every surface that ships staged — the standing loop, the candidate
pipeline's promotion gate — carries a register-watched graduation condition, so an instance
either arms it by a dated act or records the decision not to. Nothing stays half-shipped by
drift.

### Getting started

Follow the path described in **How to instantiate** below: open the unpacked folder in Claude
Code and tell it to set the project up. Underneath, init is one-shot; once
`project.yaml.instantiated_date` is stamped, it refuses to run again, so there's no ambiguity
about whether a project has already been initialized. From there, `INIT.md`'s nine-step kickoff
interview turns the conversation into the project's actual governing artifacts (session
orientation, decision authority, hard constraints, a glossary, the first roadmap phase) instead of
leaving them to be reconstructed later from memory. The standard first session runs the health
check, the first-phase preflight, and flight-plan authoring back to back, in one sitting.

## The levers

**Daily**

| Command | What it does |
|---|---|
| `/flight-plan` | Opens your session with a briefing: what changed, what's healthy, what needs you. |
| `/compile` | Turns dropped notes in `raw/` into governed wiki articles, with links and indexes updated automatically. |
| `/sweep` | Runs every health check at once and hands back one plain-English report. Nothing changes. |
| `/preflight` | Stress-tests a plan against everything on file before anyone starts building on it. |
| `/orient` | Answers how your own project works, reading straight from your files, never from memory. |
| `/doctor` | Checks whether the machine itself is set up right: tools, hooks, every sensor's own self-test. |

**Escalation**

| Command | What it does |
|---|---|
| `/cross-check`, `/cross-check-loop`, or a full handoff | A quick second opinion, a managed multi-round challenge, or a full case file ending in a locked decision record, in that escalating order. |

**Occasional**

| Command | What it does |
|---|---|
| `/conformance` | Replays every spec row against the live build and counts what actually passed. |
| `/standing-loop` | Runs the nightly knowledge housekeeping on its own branch, off until you arm it. |
| `/discover` | Surfaces what your knowledge base implies but never actually states. |
| `/audit` | Re-grades your roadmap assumptions against the evidence currently on file. |

None of this needs memorizing. Plain English usually finds the right one anyway; these are shortcuts, and [TEMPLATE-README.md](TEMPLATE-README.md) carries the full detail.

## Architecture

Organized as **core + capabilities**. Core ships in every instantiation; capabilities are opt-in via `project.yaml.capabilities`. Each capability has a `RECIPE.md` and ships in one of three maturity states: `extracted` (working code), `prototype` (working content, unvalidated), or `deferred` (recipe only). See [ARCHITECTURE.md](ARCHITECTURE.md) for the full model.

## How to instantiate

Open the unpacked folder in Claude Code and tell it to set up the project. That's the real path,
not typing YAML by hand. It reads `project.yaml.example`, interviews you for what actually varies
per project (identity, which capabilities you want, personnel), writes `project.yaml`, and runs
`init.ps1` or `init.sh` for whatever platform you're on. Along the way it asks exactly one consent
question: whether to wire the security hooks (recommended yes), because that's the one step with
a real security tradeoff, not a formality to click through. Then it runs `init-validate` to
confirm the substitution actually landed clean, and finishes with `/doctor` as a readiness check
before handing you into `INIT.md`'s kickoff interview.

**By hand:** run `init.ps1` (Windows) or `init.sh` (Unix) yourself against a filled-in
`project.yaml`, then `init-validate.{ps1,sh}`. Hard dependencies: `powershell-yaml` (Windows) or
`yq` Mike Farah Go v4+ (Unix), plus `jq` for the security hooks. Full manual sequence:
[TEMPLATE-README.md](TEMPLATE-README.md).

## License

MIT (see [LICENSE](LICENSE)), copyright 2026 Ryan Sidor. The `/preflight` core skill (renamed from `/grill` 2026-07-09) is adapted from `mattpocock/skills` (MIT): see `core/skills/preflight/LICENSE-mattpocock.txt` pre-instantiation (the file travels with the skill to `.claude/skills/preflight/LICENSE-mattpocock.txt` post-instantiation).

## Version

3.0 (2026-07-24)

## Attribution

`/preflight` (renamed from `/grill` 2026-07-09) is adapted from `mattpocock/skills/skills/engineering/grill-with-docs` (MIT License, Copyright (c) 2026 Matt Pocock, year substituted per Decision V2-6) at commit `b8be62ffacb0118fa3eaa29a0923c87c8c11985c`.
