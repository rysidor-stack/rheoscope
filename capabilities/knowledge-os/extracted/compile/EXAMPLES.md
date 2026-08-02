# /compile Examples

Two worked examples showing routing decisions. Both assume `project.yaml.wiki_domains` is populated and includes the domains referenced. *(Since the engine wiring — backlog v3.0-65, 2026-07-28 — the merge judgments described below land as SKILL Step 2.5 absorb answers that the memory engine validates and writes; "merges", "adds", "updates" below describe the content of that judgment, not a direct hand-edit of `wiki/`.)*

---

## Example 1 — Tier-1 routing (clear single-domain raw file)

A session writes a raw file documenting a vendor-specific workflow:

**Raw file:** `raw/2026-05-15T143022-vendor-acme-pricing.md`

Frontmatter:
```yaml
source: alice
date: 2026-05-15
tags: [vendors, pricing, acme]
summary: "Acme volume pricing tiers and how to negotiate"
```

Body: ~300 words on how Acme's pricing tiers work, what triggers the volume discount, who to contact for re-negotiation.

**Routing decision:**

- `vendors` tag matches the `vendors` wiki domain (declared in `project.yaml.wiki_domains`).
- `pricing` and `acme` tags are content-discriminators within `vendors/`.
- Routing target: `wiki/vendors/acme.md` (single destination, clear domain match).

**Action:**

The article `wiki/vendors/acme.md` already exists with prior content. /compile reads it, merges the new pricing information into the existing "Pricing" section, adds `raw/2026-05-15T143022-vendor-acme-pricing.md` to the `sources:` frontmatter list, updates `last_updated: 2026-05-15`, and bumps `confidence: high` (was `medium` — the new source corroborates the existing claims about volume thresholds).

No REVIEW.md entry needed. Receipt records `articles_modified: [vendors/acme.md]`, `meaningful_change: true`, `scope_tags: [vendors]`, `cross_links_changed: 0`.

---

## Example 2 — Tier-3 routing (ambiguous, requires REVIEW.md entry)

A session writes a raw file with mixed signals:

**Raw file:** `raw/2026-05-15T161544-billing-cancellation-edge-case.md`

Frontmatter:
```yaml
source: bob
date: 2026-05-15
tags: [billing, customer-intake, edge-case]
summary: "A partial-cancellation scenario surfaced during customer call"
```

Body: ~400 words. The scenario touches billing rules, customer-intake workflow, AND an unclassified concept ("late-stage refund") that isn't in any existing wiki article or in CONTEXT.md.

**Routing decision:**

- `billing` tag matches the `billing` wiki domain.
- `customer-intake` tag matches the `workflows` wiki domain.
- `edge-case` is a meta-tag (not a domain).
- The raw file should route to both `wiki/billing/` AND `wiki/workflows/`. But the "late-stage refund" concept doesn't have an article in either, AND it isn't in CONTEXT.md.

**Action:**

1. /compile routes the workflow content to `wiki/workflows/customer-intake.md` (existing article, merge into "Cancellation handling" section).
2. /compile routes the billing rules to `wiki/billing/refund-policy.md` (existing article, append to the "Edge cases" section).
3. /compile does NOT add "late-stage refund" to CONTEXT.md (per Step 8 rule — /preflight is the only CONTEXT.md writer).
4. /compile logs `[unresolved-term: late-stage refund]` to the receipt's `notes:` field. A future /preflight session will resolve this term against the project's existing language.
5. /compile creates a REVIEW.md entry of type `connection`: "The 'late-stage refund' scenario in `raw/2026-05-15T161544-billing-cancellation-edge-case.md` may indicate a missing cross-link between `wiki/billing/refund-policy.md` and `wiki/workflows/customer-intake.md`. Operator confirms or rejects."

Receipt records `articles_modified: [billing/refund-policy.md, workflows/customer-intake.md]`, `meaningful_change: true`, `scope_tags: [billing, workflows]`, `cross_links_changed: 0`, `notes: "[unresolved-term: late-stage refund]"`.

The REVIEW.md entry plus the receipt note give the operator (and /preflight at the next session) two follow-up items to resolve in the right session shape.
