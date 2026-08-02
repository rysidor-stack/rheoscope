# Recipe: /init (deferred)

## 1. WHAT IT IS

Eventual one-shot kickoff orchestrator. Produces project.yaml population, skeleton governance docs, skeleton CONTEXT.md, named-phase list, and the first session log raw file.

## 2. WHEN A PROJECT NEEDS IT

When INIT.md has been run manually 2–3+ times and operators want the walkthrough automated.

## 3. WHEN A PROJECT DOESN'T

Until that validated practice exists. Use INIT.md manually.

## 4. STATUS

deferred.

## 5. PROVENANCE

Designed in v1 build plan Phase 4 (specifically v1 lines 956–1240). Full skill prose drafted there but never validated against a real instantiation.

## 6. DEPENDENCIES

- core/governance/
- knowledge-os (if enabled)
- CONTEXT.md at project root

## 7. AUTHORING GUIDE

The v1 design has nine question modules (mirrored in INIT.md Steps 2a–2i):
- 2a Architecture sketch
- 2b Endgame
- 2c Hard constraints
- 2d Phase identification
- 2e References intake
- 2f Wiki domains (if knowledge-os enabled)
- 2g Governance docs
- 2h Glossary seeds
- 2i Initial ADRs

**Anti-patterns from v1 (must avoid in the eventual build):**

1. **Don't bundle into a single monolithic skill without considering whether /init should be one skill or three.** The reformulation rejected v1's "three contexts in one skill" pattern for /preflight. The same concern applies: /init has identity-gathering, arc-defining, and references-intake stages that may benefit from separate skills.

2. **Don't make /init re-runnable destructively.** v1's "re-running overwrites skeleton governance" was a destructive footgun. Re-run = hard fail unless `--force-reinit` flag with explicit operator confirmation.

3. **Don't assume project clarity.** v1's design asks for a one-paragraph architecture sketch in Step 2a. Operators with mature project theses can answer; operators in formation mode can't (per verifier review §9 of v1 review). Either: (a) add explicit "project-formation mode" with lighter outputs and prompts that elicit thesis through interview, or (b) document explicitly that /init assumes a mature project thesis and operators in formation use a different ritual entirely.

4. **Don't conflate /init with /roadmap.** /init produces SKELETONS — named phases with one-line goals, governance docs with placeholder bodies, glossary seeds. /roadmap fills in the substance. Keep the line clear.

**Pre-flight requirements (must be in SKILL.md Step 0):**
- Refuse to run if any non-skeleton content exists in `docs/governance/`, `roadmap/`, `CONTEXT.md`, or `raw/`. /init is single-use; mid-project use is destructive.
- Verify `project.yaml` is populated with identity (name, slug, description) and at least one personnel entry — these are operator-filled pre-init prerequisites.

## 8. KNOWN LESSONS

None — not yet built. Manual INIT.md walkthrough lessons inform the future build:
- The 2a "architecture sketch" question is the hardest. Operators who can't answer in one paragraph need a longer formation ritual first.
- The 2e references intake is easy to skip but valuable to capture — explicit ask even when operator says "no references yet."

## 9. OPEN QUESTIONS

- One skill or three (per v1's three-mode anti-pattern)?
- Project-formation mode vs mature-thesis assumption?
- How to detect "operator's answer is incomplete" mid-interview and re-prompt without restarting the whole thing?

## 10. MIGRATION STEPS

(Empty — not yet built.)
