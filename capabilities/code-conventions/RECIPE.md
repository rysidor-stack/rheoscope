# Capability: code-conventions

## 1. WHAT IT IS

A worked example of project-specific code conventions, plus a stub template the operator fills with their own. Not really a capability in the orchestrator sense — a documentation pattern shipped with one example.

## 2. WHEN A PROJECT NEEDS IT

Whenever the project writes code. Code-conventions get authored once and referenced by every code-generating session.

## 3. WHEN A PROJECT DOESN'T

Pure-knowledge projects with no code component.

## 4. STATUS

prototype. Working content (the example file) shipped without validation from a real project — fits the gradient's `prototype` slot for unvalidated working code.

## 5. PROVENANCE

Authored from general knowledge of 2026-era TypeScript+Next.js best practices. Not extracted from a validated project. Reclassified as `prototype` to fit the maturity gradient — should advance to `extracted` only after a real project validates these conventions as load-bearing.

## 6. DEPENDENCIES

None. Self-contained reference.

## 7. AUTHORING GUIDE

(Not applicable.)

## 8. KNOWN LESSONS

- Conventions go stale. Plan to revisit annually.
- Code-conventions != verification spec. Conventions are style; verification is correctness. Don't conflate.
- A `prototype`-status capability ships working content from an unvalidated source. The example file is illustrative, not load-bearing — operators are expected to replace or extend it, not adopt as-is.
- The `prototype` → `extracted` advancement criterion is "a real project validated these conventions as load-bearing." Until then, ship as example with the prototype marker.
- The substitution-scan hazard is real: Tailwind's `className` strings avoid the JSX double-curly pattern that init's substitution regex matches. Documenting this in the example file itself prevents copy-paste regression in operator-authored conventions files.

## 9. OPEN QUESTIONS

- Should the harness ship more language stacks (Python, Rust, Go)? Currently only TypeScript+Next.js. Defer to operator demand.
- Should this capability include a linter/formatter config (eslint, prettier) alongside the prose conventions? Currently no — operators wire their own toolchain. Surface to v1.x if cross-project consistency becomes a pain point.
- Should code-conventions reference verification-spec authoring? They're related (both shape what code looks like) but operate on different axes (style vs correctness). Currently kept disjoint per Field 8 lesson.
- Should the example file ship a paired "anti-patterns" section explicitly calling out common drifts? Currently the example is positive-only ("do this") — adding negative examples ("avoid this") may help operators recognize when their own code deviates. Defer to first real-project use.

## 10. MIGRATION STEPS

1. Copy `capabilities/code-conventions/examples/typescript-nextjs.md` → `methodology/code-conventions.examples/typescript-nextjs.md`.
2. Operator: author `methodology/code-conventions.md` (no `.template`) — either by editing a copy of the example or from scratch.
3. Reference from CLAUDE.md or governance docs if conventions are load-bearing.
