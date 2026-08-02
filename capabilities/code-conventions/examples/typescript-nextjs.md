# Code Conventions — TypeScript + Next.js (Example)

This is an example conventions file for projects using TypeScript on Next.js 14+ App Router. Operators replace or extend it with their own conventions. Conventions are about style and project-specific shape — they are NOT a verification spec (correctness lives elsewhere).

## 1. Naming

- **Variables and functions:** `camelCase`. Booleans read as predicates: `isLoading`, `hasError`, `canEdit`.
- **Types, interfaces, components:** `PascalCase`. Prefer interfaces over type aliases for object shapes; use type aliases for unions and primitives.
- **Constants (true compile-time):** `UPPER_SNAKE_CASE`. Runtime configuration uses `camelCase`.
- **Filenames:** `kebab-case` for utilities (`format-date.ts`), `PascalCase` for React components (`UserCard.tsx`). Tests colocated with `*.test.ts` suffix.
- **Server actions and route handlers:** verb-first names — `createOrder`, `cancelOrder`, `listOrders`.

## 2. File Organization (App Router)

```
app/
├── (auth)/                 # Route groups for layout sharing without URL impact
│   ├── login/page.tsx
│   └── signup/page.tsx
├── api/                    # Route handlers (server-only HTTP endpoints)
│   └── webhooks/[provider]/route.ts
├── (dashboard)/
│   ├── layout.tsx          # Shared layout
│   └── orders/
│       ├── page.tsx        # Server component by default
│       ├── orders-table.tsx     # Client island if needed; mark with "use client"
│       └── actions.ts      # Server actions colocated with the route
├── layout.tsx              # Root layout
└── globals.css

lib/
├── types/                  # Shared TypeScript types
├── utils/                  # Pure utility functions
├── db/                     # Database access layer
└── auth/                   # Auth helpers

components/                 # Cross-route shared components
└── ui/                     # Primitives (Button, Input, Card)
```

- Server components by default. Add `"use client"` only when a component needs browser APIs, state, or event handlers.
- Colocate route-specific code (components, actions, tests) inside the route folder. Cross-route code lives in `lib/` or `components/`.
- Tests live next to the code they test: `format-date.ts` paired with `format-date.test.ts`. Integration tests under `__tests__/` at the route or repo root.

## 3. Styling

**Prefer Tailwind className strings over inline-style objects.** Tailwind composes better and stays legible inside JSX.

```tsx
// Preferred
<button className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600">
  Submit
</button>

// Avoid (inline style object literals — the JSX double-brace shape)
// Pattern: <div style=( ( padding: 16 ) )>...</div>
// (escaped here to avoid the literal pattern; in real code it would be two open braces and two close braces)
```

If a one-off inline style is unavoidable (a CSS custom property bound to a runtime value, e.g.), extract it to a styled component or use the `style` prop with a CSS variable on a parent that has the Tailwind class doing the surrounding work.

For component-scoped CSS that exceeds Tailwind's expressiveness, use CSS modules (`*.module.css`) rather than global styles. Avoid `styled-components` or `emotion` in new code — runtime CSS-in-JS is a perf and bundle-size cost the App Router does not need.

## 4. Error Handling

- **Throw at boundaries, catch at boundaries.** Internal functions return `Result<T, E>` patterns (e.g., from `neverthrow`) or named error types. Throw only at server-action entry points, route handlers, and top-level UI error boundaries.
- **No silent catches.** A `catch` block must either re-throw, log with context, or convert to an explicit error result. Empty catches are a code-review reject.
- **Typed errors.** Custom error classes for known failure modes: `NotFoundError`, `ValidationError`, `AuthError`, `RateLimitError`. The class name carries semantics that string-matching does not.
- **Server actions return typed results.** Don't throw across the server/client boundary — return `( success: true, data )` or `( success: false, error: ErrorCode )`. Client code pattern-matches on the result.

```ts
type ActionResult<T> =
  | (readonly [{ ok: true; data: T }])[number]
  | (readonly [{ ok: false; error: string }])[number];

export async function createOrder(input: CreateOrderInput): Promise<ActionResult<Order>> {
  const validated = OrderSchema.safeParse(input);
  if (!validated.success) return { ok: false, error: "VALIDATION_FAILED" };
  const order = await db.order.create({ data: validated.data });
  return { ok: true, data: order };
}
```

## 5. Tests

- **Framework:** Vitest. (Faster than Jest for monorepos; better ESM support than Jest's current state.)
- **Naming:** `*.test.ts` colocated with code under test. Integration tests in `__tests__/` at the route root or repo root.
- **Coverage policy:** No coverage threshold gate. Instead: every bug fix lands with a test that would have caught it. Every server action and route handler has at least one happy-path test and one error-path test.
- **Avoid mocking the database in unit tests.** Use a test database (Postgres in Docker, or a SQLite memory instance for read-only paths). Mocks lie; integration tests against real DB schemas catch real bugs.
- **Snapshot tests are a smell.** Used sparingly for true serialization shape (API response envelopes); never for component render output.

## 6. Imports

- **Absolute imports from `@/`** for everything inside the project: `import { db } from "@/lib/db"`. Configure in `tsconfig.json` paths.
- **Import sort order** (enforced by eslint-plugin-import or similar):
  1. Node built-ins
  2. External packages
  3. Absolute project imports (`@/...`)
  4. Relative imports (`./...`, `../...`)
  5. Type-only imports (with `import type`)
- **No barrel-file re-export chains.** `lib/index.ts` re-exporting half the project bloats the dependency graph and confuses tree-shaking. Import directly from the source file.
- **`import type` for type-only imports.** Helps the bundler strip type-only files entirely.

## 7. Logging

- **Structured logging.** Use `pino` or equivalent — JSON log lines, severity levels, correlation IDs.
- **No `console.log` in production paths.** ESLint rule enforces. Test code may use `console.log` for diagnostic output.
- **Log at the boundary, not inside business logic.** Server action entry/exit, route handler entry/exit, external API calls. Internal pure functions don't log.
- **Never log secrets.** Even at debug level. Allowlist log fields, don't denylist.
- **Correlation IDs propagated through async context.** Use `AsyncLocalStorage` or the framework's request-context helper. Every log line in a request should share an ID.

## 8. Comments

- **Prefer expressive code over comments.** A function named `validateOrderAgainstInventory` is self-documenting; `// validate the order` is noise.
- **Comments explain *why*, not *what*.** "Use POST here because the gateway rejects PUT with bodies > 4KB" is useful. "POST the data" is not.
- **JSDoc for public APIs.** Functions exported from `lib/` get JSDoc with `@param`, `@returns`, and `@throws` (or `@returns ActionResult` for typed-result functions). Internal helpers don't need JSDoc.
- **`TODO(name)` and `FIXME(name)`** — actionable comments include the responsible person's tag, e.g., `// TODO(alice): handle the rate-limit retry`. Comments without an owner rot.
- **Delete commented-out code.** Source control remembers; the comment doesn't help.

## 9. Type Safety

- **No `any`.** ESLint rule enforces. Use `unknown` with a type guard, or define a proper type.
- **`strict: true` in `tsconfig.json`.** Including `noImplicitAny`, `strictNullChecks`, `strictFunctionTypes`, `strictBindCallApply`.
- **`as` casts are red flags.** Every `as` should have a comment justifying why the type system can't infer the shape. Most uses of `as` indicate a missing type guard.
- **Zod (or equivalent) for runtime validation at boundaries.** Server actions, route handlers, and database deserialization all run input through Zod schemas. The compile-time type comes from `z.infer<typeof Schema>`.

## 10. Performance

- **Server components for non-interactive UI.** Reduces client bundle, removes hydration cost.
- **`React.cache` for de-duplicating request-scoped data fetches.** Multiple components fetching the same data within a request share one fetch.
- **Streaming where it helps.** Suspense boundaries around slow data fetches let the shell paint immediately.
- **`next/image` for all images.** Never `<img>` directly except for SVGs imported as React components.
- **`next/font` for fonts.** Eliminates layout shift from late-loading webfonts.

## 11. Dependencies and Lock-In

- **Pin major versions.** `^x.y.z` is fine for libraries with semver discipline; lock major versions in CI for the rest. A drift to a new major in `next`, `react`, or `typescript` should be a deliberate upgrade with a flight plan, not a `npm install` accident.
- **`package-lock.json` (or `pnpm-lock.yaml`) is committed.** Reproducible installs across machines.
- **Audit new dependencies before adding.** Bundle size, license, maintenance signal. A 500KB dep for a 20-line helper is a bad trade.
- **Prefer the platform.** `URL`, `URLSearchParams`, `fetch`, `crypto.subtle`, `AbortController`, `structuredClone` — all browser+Node now. Reach for utility libraries only when the platform doesn't cover the case.
- **No legacy compatibility shims you don't need.** `core-js` polyfills targeting IE11 add cost for zero benefit if the project's browser-support matrix is evergreen-only.

## 12. What this file is NOT

- **Not a verification spec.** Verification covers correctness (does the code do the right thing); this covers style and project shape (does the code look like the project's other code).
- **Not a security policy.** Security constraints live in the project's hard-constraints document.
- **Not a religion.** Conventions exist to reduce decision load. When a rule above conflicts with a specific situation, do the right thing for that situation and surface the deviation in code review.

---

*This is a worked example. Operators should replace or extend with their own conventions. The example is illustrative, not load-bearing.*
