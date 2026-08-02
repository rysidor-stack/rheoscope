#!/usr/bin/env python3
"""catalog.py -- entity-vocabulary governance sensor (memory-engine v3, P1).

ROUTE-1: the integrity tripwire on the governed entity vocabulary deploy/entities.yaml
(test-plan ROUTE-1). The vocabulary is the routing source of truth -- M(E), the set of
views matching an event E, is `subscribes.entities match` UNION `cascades_to naming`, and
GOLD-2 freezes its expected routing against this file. A broken vocabulary silently
mis-routes events, so this is a blocking governance check, not a degrade-only sensor.

Four properties (test-plan ROUTE-1):
  (1) NO alias maps to two entities (case-insensitive after normalisation); an alias also
      may not collide with a different entity's canonical slug.
  (2) every entity is subscribed by >=1 view OR carries an explicit `parked: <reason>`
      (dead vocabulary is named, never silent).
  (3) every `cascades_to` target resolves to an existing view file OR a known entity
      (dangling targets FAIL, named).
  (4) the file parses and round-trips (safe_load -> safe_dump -> safe_load equality).

deploy/entities.yaml shape:
  entities:
    <slug>:
      aliases:     [<alias>, ...]          # optional; each alias -> exactly one entity
      views:       [<wiki/....md>, ...]     # subscribing views; >=1 unless parked
      cascades_to: [<entity-or-view>, ...]  # optional; each must resolve
      parked:      <reason>                 # optional; permits 0 views
  known_holes: [<receipt>, ...]             # ACC-2 allowlist may consolidate here
  hub_ok:      {<entity>: <reason>}         # ROUTE-2 allowlist (not ROUTE-1's concern)

Usage:
  catalog.py                    validate deploy/entities.yaml against the live tree
  catalog.py --self-test        run embedded fixtures (valid + each violation)
  catalog.py PATH               validate a specific entities.yaml

Exit codes: 0 = valid vocabulary | 1 = ROUTE-1 violation(s) or a self-test failure
  | 2 = inconclusive (PyYAML unavailable, or entities.yaml missing/unreadable).
"""

import os
import re
import sys

try:  # cp1252 consoles + unicode corpus content: never let an encode error mask a verdict.
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENTITIES_FILE = os.path.join(_HERE, "entities.yaml")


def normalize(s):
    """The documented alias normalisation: lower-case, trim, collapse internal
    whitespace to single hyphens. Case-insensitive comparison keys off this."""
    return re.sub(r"\s+", "-", str(s).strip().lower())


# ---------------------------------------------------------------------------
# ROUTE-1: the four vocabulary-integrity properties (pure over a parsed dict).
# ---------------------------------------------------------------------------

def _entities(data):
    ents = data.get("entities") if isinstance(data, dict) else None
    return ents if isinstance(ents, dict) else {}


def check_alias_uniqueness(data):
    """(1) no normalised alias maps to two entities, and no alias collides with a
    different entity's canonical slug."""
    out = []
    ents = _entities(data)
    slugs = {normalize(s): s for s in ents}
    alias_owner = {}
    for slug, spec in ents.items():
        spec = spec or {}
        for a in (spec.get("aliases") or []):
            na = normalize(a)
            # alias colliding with a DIFFERENT entity's slug
            if na in slugs and slugs[na] != slug:
                out.append("alias '%s' (entity '%s') collides with entity slug '%s'"
                           % (a, slug, slugs[na]))
            prev = alias_owner.get(na)
            if prev is not None and prev != slug:
                out.append("alias '%s' maps to two entities: '%s' and '%s'"
                           % (a, prev, slug))
            else:
                alias_owner.setdefault(na, slug)
    return out


def check_subscribed_or_parked(data):
    """(2) every entity has >=1 view OR an explicit parked reason."""
    out = []
    for slug, spec in _entities(data).items():
        spec = spec or {}
        views = [v for v in (spec.get("views") or []) if v]
        if not views and not spec.get("parked"):
            out.append("entity '%s' has no subscribing view and is not parked" % slug)
    return out


def check_cascades_resolve(data, view_exists):
    """(3) every cascades_to target resolves to a known entity slug or an existing view."""
    out = []
    ents = _entities(data)
    known = set(normalize(s) for s in ents)
    for slug, spec in ents.items():
        spec = spec or {}
        for tgt in (spec.get("cascades_to") or []):
            t = str(tgt).strip()
            if normalize(t) in known:
                continue
            if t.endswith(".md") and view_exists(t):
                continue
            out.append("entity '%s' cascades_to '%s' which resolves to no entity or view"
                       % (slug, tgt))
    return out


def check_round_trip(text):
    """(4) the file parses and round-trips (safe_load -> dump -> load equality)."""
    if yaml is None:
        return ["PyYAML unavailable; cannot verify round-trip"]
    try:
        a = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return ["entities.yaml does not parse: %s" % e]
    try:
        b = yaml.safe_load(yaml.safe_dump(a, sort_keys=True))
    except yaml.YAMLError as e:
        return ["entities.yaml does not round-trip: %s" % e]
    return [] if a == b else ["entities.yaml round-trip changed the semantic content"]


def check_views_exist(data, view_exists):
    """Live-only: every declared subscribing view must resolve to a file (a dangling
    subscription silently drops routing)."""
    out = []
    for slug, spec in _entities(data).items():
        spec = spec or {}
        for v in (spec.get("views") or []):
            if v and not view_exists(str(v)):
                out.append("entity '%s' subscribes view '%s' which is not a file" % (slug, v))
    return out


def validate(text, data, view_exists, live=False):
    """Return the full ROUTE-1 violation list. `live` adds the on-disk view-resolution check."""
    problems = []
    problems += check_round_trip(text)
    problems += check_alias_uniqueness(data)
    problems += check_subscribed_or_parked(data)
    problems += check_cascades_resolve(data, view_exists)
    if live:
        problems += check_views_exist(data, view_exists)
    return problems


# ---------------------------------------------------------------------------
# Alias resolution (exposed for staleness.py routing reuse).
# ---------------------------------------------------------------------------

def alias_map(data):
    """{normalised-alias-or-slug: entity-slug} for tag normalisation."""
    m = {}
    for slug, spec in _entities(data).items():
        m[normalize(slug)] = slug
        for a in ((spec or {}).get("aliases") or []):
            m.setdefault(normalize(a), slug)
    return m


def normalize_tag(tag, amap):
    """Resolve a raw event tag to a canonical entity slug, or None if unknown."""
    return amap.get(normalize(tag))


# ---------------------------------------------------------------------------
# Live / IO.
# ---------------------------------------------------------------------------

def _read(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return fh.read()


def run(path, root=None):
    if yaml is None:
        print("RESULT: INCONCLUSIVE -- PyYAML unavailable; cannot read entities.yaml")
        return 2
    if not os.path.isfile(path):
        print("RESULT: INCONCLUSIVE -- entities.yaml not found: %s" % path)
        return 2
    try:
        text = _read(path)
    except (OSError, UnicodeDecodeError) as e:
        print("RESULT: INCONCLUSIVE -- entities.yaml unreadable: %s" % e)
        return 2
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        print("  VIOLATION entities.yaml does not parse: %s" % e)
        print("RESULT: FAIL -- 1 ROUTE-1 violation(s)")
        return 1
    root = root or os.getcwd()

    def view_exists(v):
        return os.path.isfile(os.path.join(root, v))

    problems = validate(text, data, view_exists, live=True)
    for p in problems:
        print("  VIOLATION %s" % p)
    n_ent = len(_entities(data))
    if problems:
        print("RESULT: FAIL -- %d ROUTE-1 violation(s) over %d entit(y/ies)" % (len(problems), n_ent))
        return 1
    print("RESULT: PASS -- %d entit(y/ies), vocabulary integral" % n_ent)
    return 0


# ---------------------------------------------------------------------------
# Self-test: embedded fixtures (valid + one per violation class).
# ---------------------------------------------------------------------------

_VALID = """
entities:
  work-orders:
    aliases: [wo, work-order, ticket-item]
    views: [wiki/systems/schema-work-orders.md, wiki/workflows/work-orders.md]
    cascades_to: [customers]
  customers:
    aliases: [customer]
    views: [wiki/systems/schema-customers.md]
  methodology:
    parked: no dedicated view yet; tracked for future intake
known_holes:
  - receipts/2026-04-21T061500-phase-3-restructure.md
"""
_DUP_ALIAS = _VALID.replace("aliases: [customer]", "aliases: [customer, wo]")
_SLUG_COLLIDE = _VALID.replace("aliases: [wo, work-order, ticket-item]",
                               "aliases: [wo, work-order, customers]")
_ORPHAN = _VALID.replace(
    "  methodology:\n    parked: no dedicated view yet; tracked for future intake",
    "  orphan-entity:\n    aliases: [orphan]")
_DANGLING = _VALID.replace("cascades_to: [customers]", "cascades_to: [ghost-entity]")


def self_test():
    failed = 0
    total = 0

    def case(name, ok):
        nonlocal failed, total
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    def load(t):
        return yaml.safe_load(t) or {}

    # fixture views resolve; unknown ones do not (deterministic, no disk)
    known_views = {
        "wiki/systems/schema-work-orders.md", "wiki/workflows/work-orders.md",
        "wiki/systems/schema-customers.md",
    }
    vx = lambda v: v in known_views

    if yaml is None:
        print("catalog self-test: INCONCLUSIVE (PyYAML unavailable)")
        return 2

    case("valid vocabulary passes all four properties",
         validate(_VALID, load(_VALID), vx) == [])
    case("normalisation is case/space-insensitive",
         normalize("Work Orders") == "work-orders" and normalize(" WO ") == "wo")
    case("alias resolves a tag to its canonical entity",
         normalize_tag("Work-Order", alias_map(load(_VALID))) == "work-orders")
    case("an unknown tag resolves to None",
         normalize_tag("nonesuch", alias_map(load(_VALID))) is None)

    # (1) alias uniqueness
    case("dup alias across two entities trips (1)",
         any("two entities" in p for p in check_alias_uniqueness(load(_DUP_ALIAS))))
    case("alias colliding with another entity's slug trips (1)",
         any("collides with entity slug" in p for p in check_alias_uniqueness(load(_SLUG_COLLIDE))))
    case("a clean vocabulary has no alias violation",
         check_alias_uniqueness(load(_VALID)) == [])

    # (2) subscribed-or-parked
    case("an entity with no view and not parked trips (2)",
         any("no subscribing view" in p for p in check_subscribed_or_parked(load(_ORPHAN))))
    case("a parked entity with no view is allowed (2)",
         check_subscribed_or_parked(load(_VALID)) == [])

    # (3) cascades resolve
    case("a dangling cascades_to target trips (3)",
         any("resolves to no entity or view" in p
             for p in check_cascades_resolve(load(_DANGLING), vx)))
    case("a cascades_to naming a known entity resolves (3)",
         check_cascades_resolve(load(_VALID), vx) == [])
    case("a cascades_to naming an existing view resolves (3)",
         check_cascades_resolve(
             load(_VALID.replace("cascades_to: [customers]",
                                 "cascades_to: [wiki/systems/schema-customers.md]")), vx) == [])

    # (4) round-trip
    case("a well-formed file round-trips (4)", check_round_trip(_VALID) == [])
    case("malformed YAML is caught by (4)",
         any("does not parse" in p for p in check_round_trip("entities:\n  x: [unterminated")))

    # live view-existence
    case("a dangling subscribing view trips the live check",
         any("is not a file" in p for p in check_views_exist(
             load(_VALID.replace("wiki/systems/schema-customers.md", "wiki/systems/ghost.md")),
             vx)))

    if failed:
        print("catalog self-test: FAIL (%d/%d)" % (failed, total))
        return 1
    print("catalog self-test: PASS (%d/%d)" % (total, total))
    return 0


def main(argv):
    args = argv[1:]
    if "--self-test" in args:
        return self_test()
    paths = [a for a in args if not a.startswith("--")]
    path = paths[0] if paths else _ENTITIES_FILE
    return run(path)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
