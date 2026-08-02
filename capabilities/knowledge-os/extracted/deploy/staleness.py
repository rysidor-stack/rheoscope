#!/usr/bin/env python3
"""staleness.py -- conservation tripwire + journal-hole accounting (memory-engine v3, P1).

The absorption-conservation core of the memory engine (test-plan ACC-1 + ACC-2,
spec sec.6). Every ledger event (raw/*.md) resolves to EXACTLY ONE of seven classes
via an ORDERED, first-match-wins decision table -- the no-silent-loss invariant:

  1 REF-SKIPPED            source: ref event (skipped regardless of entity match, F1)
  2 SUPERSEDED-UNCONSUMED  a later event supersedes E and E was never absorbed
  3 CONSUMED               M(E) != {} and ALL matched views absorbed E
  4 PENDING_NOOP_CANDIDATE M(E) != {} and a matched view journaled a no-op pending VERIFY
  5 PENDING                M(E) != {} and a matched view has not journaled E (work queue)
  6 UNROUTED               M(E) == {} and E is not on the frozen residue list
  7 RESIDUE                E is on the frozen pre-engine residue-p1.yaml snapshot

ACC-2: a legacy-prose receipt that fails whole-block YAML parse becomes a named
"journal hole" -- its absorptions are treated as ABSENT (events re-present as PENDING;
the conservative direction is always re-present, never assume-absorbed). A hole on the
known-holes allowlist is a NOTE (exit 0); a NEW hole is exit 1.

P5 NOTE (2026-07-06, dated sibling: harness-v3.0/specs/memory-engine-v3-p5-typed-events-
design-2026-07-06.md, component C2, adjudication 2; DEFAULT FLIPPED 2026-07-06, the
atomic-flip commit, adjudication 6): `load_ledger` / `run_census` take an `enlarged`
parameter, now DEFAULTING TO TRUE -- the standard census/load path runs ENLARGED. When
True, the ledger ALSO enumerates top-level `receipts/*.md` (excluding the
`receipts/journal/` and `receipts/registrations/` engine sidecar dirs) as events, and
every enlarged-ledger member is annotated from `registrations.load_registrations(root)`
with `pointer_class` (True iff its registration carries `asserts_corpus_state: true`)
plus the registration record itself. Priority 1 of the ordered table (REF-SKIPPED) is
extended: an event is skipped at priority 1 when its frontmatter carries `source: ref`
(unchanged) OR its registration is pointer_class (adjudication 2) -- reported under the
SAME `REF-SKIPPED` class, with a new census sub-bucket (`ref` vs `pointer-class`) so
receipts are countable without inventing an 8th class. The class table stays 7-wide;
the partition stays total+disjoint. Enlarged mode is FAIL LOUD, never silent-clean: a
missing registrations store, a broken registration chain, or ANY enlarged-ledger member
(receipt OR raw/ event) lacking a registration raises `EnlargementViolation` (full
coverage of the enlarged ledger is mandated by the design; a receipt in L without a
registration is a conservation-accounting defect, not a skippable). The pre-P5
BYTE-IDENTICAL behavior (raw/*.md only, no registration lookups, no import of
registrations.py) remains
available as an explicit opt-out via `enlarged=False` (archaeology only -- the minted
registration chain at `receipts/registrations/` is now a live, committed prerequisite of
the default path, per adjudication 6: "there must be no window where the ledger is
enlarged but unbaselined or vice versa" -- both landed together in this commit).
`--baseline-write PATH` / `--baseline-check PATH` mint/verify the enlarged-ledger census
as a re-runnable JSON receipt (OPS-4 `--verdict` precedent: re-runnable, never memory)
-- see `baseline_census`/`write_baseline`/`check_baseline` below.

Doctrine: this is a conservation TRIPWIRE, not a degrade-only sensor -- a new journal
hole, a partition failure, or an orphan journal claim (a journaled view or event that
resolves to no file) is a real FAIL (exit 1). PENDING is NOT a failure (it is the work
queue); PENDING age > PARTIAL_CANDIDATE_DAYS prints a WARN line, and --strict escalates
any WARN to exit 1.

PRECEDENCE NOTE (RESIDUE vs UNROUTED, flagged for MIG-1 cross-check): the table lists
UNROUTED (6) before RESIDUE (7), yet RESIDUE is described as "the catch for known
pre-engine events that would otherwise be UNROUTED." For RESIDUE to be reachable under
first-match-wins, UNROUTED must exclude residue members. This engine therefore treats
the M(E)=={} case as: RESIDUE if E is on the frozen list, else UNROUTED.

PRE-SEED NOTE (flagged): until P1 seeding writes the receipts/journal/<seq>.json
sidecars, the journal is BOOTSTRAPPED from legacy-prose receipts (raw_inputs x the wiki
views in articles_modified, projection targets INDEX/REVIEW/HEALTH excluded per GOLD-1).
Until the derivation backfill, no view carries subscribes.entities, so M(E)=={} for
every event and the census is all-UNROUTED -- the correct pre-engine state.

Usage:
  staleness.py                      run conservation census over the live tree (raw/, wiki/, receipts/)
  staleness.py --report             emit the census + holes as JSON (preflight delegation)
  staleness.py --strict             exit 1 if any WARN (e.g. PENDING age > PARTIAL_CANDIDATE_DAYS)
  staleness.py --self-test          run embedded fixtures (classify per-class + ACC-2 holes)
  staleness.py --baseline-write PATH  (P5/C2) mint the enlarged-ledger census baseline at PATH
  staleness.py --baseline-check PATH  (P5/C2) re-run the enlarged-ledger census, diff vs PATH;
                                       exit 0 on match, exit 1 with named diffs on mismatch

Exit codes: 0 = clean / PENDING reported (work queue) | 1 = conservation FAIL
  (new journal hole, non-total partition, orphan claim) or a WARN under --strict, or a
  self-test failure | 2 = inconclusive (PyYAML unavailable; cannot parse the receipt journal).
"""

import hashlib
import importlib.util
import json
import os
import re
import sys
from datetime import date, datetime

try:  # cp1252 consoles + unicode corpus content: never let an encode error mask a verdict.
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

_HERE = os.path.dirname(os.path.abspath(__file__))
_KNOWN_HOLES_FILE = os.path.join(_HERE, "known-holes.yaml")
_ENTITIES_FILE = os.path.join(_HERE, "entities.yaml")
_RESIDUE_FILE = os.path.join(
    _HERE, "test-fixtures", "memory-engine", "consumed-sets", "residue-p1.yaml")


def _load_sibling(basename, alias):
    """Same sibling-import mechanism registrations.py uses to import compile-core.py:
    load-by-path via importlib, never a package import (this repo's deploy/ modules are
    not a package). Used ONLY by the enlarged-mode path (registrations.py), so the
    default (enlarged=False) code path never pays this import cost."""
    spec = importlib.util.spec_from_file_location(alias, os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class EnlargementViolation(Exception):
    """P5 enlarged-mode (load_ledger(root, enlarged=True)) FAIL LOUD condition: a
    missing registrations store, a broken registration chain, or an enlarged-ledger
    member (receipt or raw/ event) lacking a registration record. Never silent-clean --
    the design mandates full coverage over the enlarged ledger, whatever its size."""
    pass

SCHEMA_VERSION = "3.2"
PARTIAL_CANDIDATE_DAYS = 14

CLASSES = (
    "REF-SKIPPED",
    "SUPERSEDED-UNCONSUMED",
    "CONSUMED",
    "PENDING_NOOP_CANDIDATE",
    "PENDING",
    "UNROUTED",
    "RESIDUE",
)

# Journal disposition normalisation. The live build_journal emits only "absorbed"; the
# post-seed JSON-sidecar substrate (receipts/journal/<seq>.json) carries the spec sec.6
# per-(event,view) disposition enum. Map those to the classifier's two internal states
# here so an UNMAPPED value degrades conservatively (re-present), never crashes the census.
# (The legacy-assumed audit obligation rides on consumed_status, a separate per-VIEW stamp;
# for the per-EVENT conservation CLASS a legacy-assumed pair is in J = absorbed.)
_ABSORBED_DISPOSITIONS = frozenset((
    "absorbed", "verified-consumed", "legacy-assumed", "absorbed-without-source"))
_NOOP_DISPOSITIONS = frozenset((
    "noop_pending_verification", "no-op", "noop"))

DERIV_START = "# --- derivation"
DERIV_END = "# --- /derivation"

_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_NEVER_SOURCE_RE = re.compile(r"INDEX|REVIEW|HEALTH")


# ---------------------------------------------------------------------------
# ACC-1: the pure conservation classifier.
# ---------------------------------------------------------------------------
# Data shapes (all plain dicts/sets so the self-test injects them without disk):
#   ledger : {event_id: {"source_ref": bool, "superseded_by": event_id|None, ...}}
#   journal: {event_id: {view_id: {"disposition": <str>, "attested_by": set(receipt_id)|None}}}
#            -- the EFFECTIVE absorption record. `attested_by` lets the classifier honour
#               `holes`: a pair attested ONLY by hole receipts reads as ABSENT (conservative).
#               attested_by=None marks a seeded/synthetic pair (never hole-shadowable).
#   matches: {event_id: set(view_id)}   -- M(E) = subscribes.entities match U cascades_to naming
#   residue: set(event_id)              -- the frozen pre-engine residue-p1.yaml (event projection)
#   holes  : set(receipt_id)            -- receipts that failed whole-block YAML parse


def _present(journal, e, v, holes):
    """Effective disposition of pair (e, v): "absorbed" | "noop_pending_verification" |
    None (absent). A pair attested ONLY by hole receipts reads as absent (ACC-2
    conservative re-present). An UNRECOGNISED disposition degrades to absent (conservative),
    never crashes."""
    entry = journal.get(e, {}).get(v)
    if not entry:
        return None
    att = entry.get("attested_by")
    if att and set(att) <= set(holes):  # solely hole-attested -> drop the absorption
        return None
    d = entry.get("disposition", "absorbed")
    if d in _NOOP_DISPOSITIONS:
        return "noop_pending_verification"
    if d in _ABSORBED_DISPOSITIONS:
        return "absorbed"
    return None


def _absorbed_anywhere(journal, e, holes):
    return any(_present(journal, e, v, holes) == "absorbed" for v in journal.get(e, {}))


def _ref_skip_reason(meta):
    """P5 adjudication 2: priority-1 REF-SKIPPED fires on EITHER condition. Returns
    "ref" | "pointer-class" | None so the census can sub-bucket without a new class."""
    if meta.get("source_ref"):
        return "ref"
    if meta.get("pointer_class"):
        return "pointer-class"
    return None


def _classify_one(e, ledger, journal, matches, residue, holes):
    meta = ledger.get(e, {})
    # 1 REF-SKIPPED -- keyed on the frontmatter flag OR (P5, adjudication 2) the
    # event's registration carrying asserts_corpus_state true (pointer_class).
    # Ignores M(E) and J either way (F1); the reason is sub-bucketed at census time.
    if _ref_skip_reason(meta) is not None:
        return "REF-SKIPPED"
    # 2 SUPERSEDED-UNCONSUMED -- a superseder exists AND E was never absorbed by any view.
    if meta.get("superseded_by") and not _absorbed_anywhere(journal, e, holes):
        return "SUPERSEDED-UNCONSUMED"
    m = matches.get(e) or set()
    if m:
        disp = {v: _present(journal, e, v, holes) for v in m}
        # 3 CONSUMED -- universal quantifier: every matched view absorbed E.
        if all(disp[v] == "absorbed" for v in m):
            return "CONSUMED"
        # 4 PENDING_NOOP_CANDIDATE -- an unverified no-op on a lock/T1/correction view.
        if any(disp[v] == "noop_pending_verification" for v in m):
            return "PENDING_NOOP_CANDIDATE"
        # 5 PENDING -- a matched view has no journal entry at all (the work queue).
        # _present normalises every disposition to one of the three states above/here, so
        # a matched non-CONSUMED event always has a no-op or an absent view; this is the
        # conservative catch-all (re-present) and never crashes the census.
        return "PENDING"
    # M(E) == {}: priority 7 (RESIDUE) is reachable only by excluding residue members
    # from UNROUTED (priority 6) -- see PRECEDENCE NOTE in the module docstring.
    if e in residue:
        return "RESIDUE"
    # 6 UNROUTED
    return "UNROUTED"


def classify(ledger, journal, matches, residue, holes):
    """Return {event_id: class}. Total over `ledger`, disjoint by first-match-wins."""
    residue = set(residue or ())
    holes = set(holes or ())
    out = {}
    for e in ledger:
        cls = _classify_one(e, ledger, journal, matches, residue, holes)
        if cls not in CLASSES:  # zero-class is a FAIL, full stop (ACC-1)
            raise AssertionError("event %r resolved to no class" % e)
        out[e] = cls
    return out


def conservation_audit(ledger, journal, matches, residue, holes, view_exists=None):
    """Run classify and the ACC-1 side conditions. Return (result, problems).
    view_exists, when provided, resolves a view id to True/False (side condition (ii));
    it is omitted in pure self-tests where journal views are synthetic."""
    problems = []
    result = classify(ledger, journal, matches, residue, holes)
    # Totality: every ledger event classified.
    for e in ledger:
        if e not in result:
            problems.append("event not classified: %s" % e)
    # Side condition (ii): no orphan journal claims -- E resolves to a ledger event AND
    # (when view_exists is provided) V resolves to an existing view file.
    for e, views in journal.items():
        if e not in ledger:
            problems.append("orphan journal claim: event %s not in ledger" % e)
        if view_exists is not None:
            for v in views:
                if not view_exists(v):
                    problems.append("orphan journal claim: view %s not a file (event %s)" % (v, e))
    # Side condition (iv): residue must not admit ref / consumed / superseded events.
    for e in residue:
        meta = ledger.get(e, {})
        if meta.get("source_ref"):
            problems.append("residue admits source:ref event: %s" % e)
        elif result.get(e) == "CONSUMED":
            problems.append("residue admits already-consumed event: %s" % e)
        elif meta.get("superseded_by") and not _absorbed_anywhere(journal, e, holes):
            problems.append("residue admits superseded-unconsumed event: %s" % e)
    return result, problems


# ---------------------------------------------------------------------------
# ACC-2: legacy-prose receipt parsing + journal-hole detection.
# ---------------------------------------------------------------------------

def _as_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _is_view(path):
    """A journal view is a wiki article, never a projection target (INDEX/REVIEW/HEALTH)
    -- mirrors GOLD-1's NEVER_SOURCE_TARGETS exclusion."""
    p = str(path).replace("\\", "/")
    if not p.startswith("wiki/"):
        return False
    return not _NEVER_SOURCE_RE.search(os.path.basename(p))


def split_frontmatter(text):
    """Return (status, payload). status in {"ok","no-frontmatter","unclosed-fence",
    "parse-fail","inconclusive"}. For "ok", payload is the parsed dict (or {}). The three
    non-ok hole statuses are the three ACC-2 archetypes."""
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "no-frontmatter", None          # archetype 1
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return "unclosed-fence", None          # archetype 2
    block = "\n".join(lines[1:end])
    if yaml is None:
        return "inconclusive", None
    try:
        return "ok", (yaml.safe_load(block) or {})
    except Exception:                          # archetype 3 (ScannerError et al.)
        return "parse-fail", None


def is_hole(text):
    """A receipt is a journal hole iff it fails whole-block YAML parse. Returns the
    archetype name or None."""
    status, _ = split_frontmatter(text)
    if status in ("no-frontmatter", "unclosed-fence", "parse-fail"):
        return status
    return None


def _pairs_from_receipt(parsed):
    """The (view, event) absorptions a parseable receipt attests: the cross-product of
    raw_inputs x the wiki VIEWS in articles_modified (the migration-bootstrap journal,
    pre-sidecar). raw_inputs / articles_modified may be a scalar, a list, or (for
    articles) a list of {path: ...} maps -- coerce defensively via _as_list so a scalar
    string is ONE element, never iterated character-by-character."""
    if not isinstance(parsed, dict):
        return []
    raws = [r for r in _as_list(parsed.get("raw_inputs")) if isinstance(r, str) and r]
    arts = []
    for a in _as_list(parsed.get("articles_modified")):
        path = a.get("path") if isinstance(a, dict) else (a if isinstance(a, str) else None)
        if isinstance(path, str) and _is_view(path):
            arts.append(path)
    return [(v, e) for v in arts for e in raws]


def build_journal(receipts, known_holes):
    """Parse the legacy-prose receipts into the effective journal + the hole report.
    Returns (journal, holes_report) where holes_report = [(receipt_id, archetype, allowlisted)].
    A hole's absorptions are simply never added (conservative re-present)."""
    journal = {}
    holes_report = []
    known = set(known_holes or ())
    for rid, text in receipts.items():
        arche = is_hole(text)
        if arche:
            holes_report.append((rid, arche, rid in known))
            continue
        _status, parsed = split_frontmatter(text)
        for (v, e) in _pairs_from_receipt(parsed):
            journal.setdefault(e, {}).setdefault(v, {
                "disposition": "absorbed", "attested_by": set()})
            att = journal[e][v]["attested_by"]
            if att is not None:
                att.add(rid)
    return journal, holes_report


def new_holes(holes_report):
    """Receipts that are holes but NOT on the allowlist -> exit-1 condition."""
    return [rid for (rid, _arche, ok) in holes_report if not ok]


# ---------------------------------------------------------------------------
# Live corpus loading (degrades gracefully; routing is a no-op until catalog.py
# + entities.yaml + the derivation backfill land).
# ---------------------------------------------------------------------------

def _read(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return fh.read()


def _iter_md(root):
    for r, _dirs, files in os.walk(root):
        if os.sep + ".git" in r:
            continue
        for f in files:
            if f.endswith(".md"):
                yield os.path.join(r, f)


def _rel(path, root):
    return os.path.relpath(path, root).replace("\\", "/")


def _event_frontmatter(text):
    """Parse a raw event's leading frontmatter degrade-tolerantly: PyYAML if it parses,
    else a line-level fallback for the YAML-hostile (~9%) events (mirrors
    check-loop-state.raw_informed_by degraded parse). The fallback gathers `  - item`
    block-sequences into lists so a multi-line supersedes/tags survives a parse failure."""
    status, parsed = split_frontmatter(text)
    if status == "ok" and isinstance(parsed, dict):
        return parsed
    fm = {}
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return fm
    body = []
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        body.append(ln)
    i = 0
    while i < len(body):
        m = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", body[i])
        if m:
            key, inline = m.group(1), m.group(2).strip()
            if inline:
                if inline.startswith("[") and inline.endswith("]"):
                    # an inline YAML flow-list -> a real list, never a comma-joined string
                    fm[key] = [x.strip() for x in inline[1:-1].split(",") if x.strip()]
                else:
                    fm[key] = inline
                i += 1
                continue
            items = []
            j = i + 1
            while j < len(body):
                mm = re.match(r"^\s+-\s+(.*)$", body[j])
                if not mm:
                    break
                items.append(mm.group(1).strip())
                j += 1
            fm[key] = items if items else ""
            i = j
            continue
        i += 1
    return fm


def _resolve_event(target, ledger_ids):
    """Resolve a supersedes target id to a ledger event id, in PRIORITY order: exact
    path, then unique path-suffix, then unique basename. Returns None on no match or an
    ambiguous loose match -- never lets a basename collision override an explicit path."""
    if target in ledger_ids:
        return target
    suffix = [e for e in ledger_ids if e.endswith("/" + target)]
    if len(suffix) == 1:
        return suffix[0]
    if suffix:
        return None  # ambiguous suffix -> refuse to guess
    base = os.path.basename(target)
    bmatch = [e for e in ledger_ids if os.path.basename(e) == base]
    if len(bmatch) == 1:
        return bmatch[0]
    return None  # no match or ambiguous basename


def _apply_supersession(ledger, supersedes):
    """Mark superseded_by for EVERY target of EVERY superseder (multi-target safe)."""
    ledger_ids = list(ledger)
    for superseder, targets in supersedes.items():
        for target in targets:
            e = _resolve_event(target, ledger_ids)
            if e is not None and e != superseder:
                ledger[e]["superseded_by"] = superseder


def _iter_receipts_only(root):
    """P5 enlarged mode: top-level receipts/*.md, EXCLUDING every engine
    machine-sidecar dir named in registrations.ENGINE_SIDECAR_DIRS
    (receipts/journal/, receipts/registrations/, receipts/verify/, ...) -- JSON
    records / per-leg verify packets, never legacy-prose ledger events. Delegates
    to registrations.list_receipts_population(root), the SAME function
    check-loop-state.py's extension check (c) calls for its own receipts-population
    count, so the two sensors' receipts populations are PROVABLY identical (B-2,
    2026-07-09). Before this fix this function carried its own local 2-item
    exclusion tuple ("journal", "registrations") and check-loop-state.py had an
    independently-defined os.listdir population -- they had silently drifted:
    receipts/verify/packets/*.md, written by every live compile verify leg, was
    being swept into the enlarged ledger unregistered -> EnlargementViolation
    crashing the census on the engine's own output."""
    regs_mod = _load_sibling("registrations.py", "registrations_staleness_receipts")
    for rel in regs_mod.list_receipts_population(root):
        yield os.path.join(root, rel), rel


def load_ledger(root, enlarged=True):
    """Build the ledger dict from raw/*.md, with source_ref / supersedes / date flags.

    enlarged=True (DEFAULT as of the P5 atomic flip, 2026-07-06, adjudication 6): the
    standard census/load path runs ENLARGED -- see the module P5 NOTE above. Pass
    enlarged=False explicitly to opt out (archaeology / pre-P5 byte-identical behavior:
    raw/*.md only, no registration lookups, no import of registrations.py).

    enlarged=True (P5, adjudication 2 + component C2): the ledger ALSO enumerates
    top-level receipts/*.md (excluding the journal/registrations sidecars) as events.
    Every enlarged-ledger member (receipt AND raw/ event) is annotated with
    `pointer_class` (True iff its registration's asserts_corpus_state is true) and a
    `registration` field carrying the full registration record. FAIL LOUD (never
    silent-clean, per the design's adjudication-2/C2 mandate):
      - registrations.load_registrations(root) raising (missing store / broken chain)
        propagates as EnlargementViolation, never swallowed.
      - ANY enlarged-ledger member without a matching registration -> EnlargementViolation
        naming every such event (a receipt or raw/ event in L with no registration is a
        conservation-accounting defect, not a skippable; full coverage of the enlarged
        ledger is mandated, whatever its size)."""
    ledger = {}
    supersedes = {}  # superseder_event -> [target_event, ...]
    raw_root = os.path.join(root, "raw")
    if os.path.isdir(raw_root):
        for fp in _iter_md(raw_root):
            rel = _rel(fp, root)
            try:
                fm = _event_frontmatter(_read(fp))
            except (OSError, UnicodeDecodeError):
                fm = {}
            src = str(fm.get("source", "")).split("#", 1)[0].strip()
            ledger[rel] = {
                "source_ref": src == "ref", "superseded_by": None,
                "tags": fm.get("tags"), "cascades_to": fm.get("cascades_to"),
                "date": fm.get("date"),
            }
            tgts = [str(t).strip() for t in _as_list(fm.get("supersedes")) if str(t).strip()]
            if tgts:
                supersedes[rel] = tgts

    if not enlarged:
        _apply_supersession(ledger, supersedes)
        return ledger

    # -------------------------------------------------------- enlarged-mode addition
    for fp, rel in _iter_receipts_only(root):
        ledger[rel] = {
            "source_ref": False, "superseded_by": None,
            "tags": None, "cascades_to": None, "date": None,
        }

    try:
        registrations_mod = _load_sibling("registrations.py", "registrations_staleness")
        effective = registrations_mod.load_registrations(root)
    except Exception as e:  # missing store, broken chain, import failure -- all LOUD
        raise EnlargementViolation(
            "enlarged ledger requires a clean registrations store at "
            "receipts/registrations/ -- %s: %s. "
            "FIX: run `python deploy/register-intake.py` to mint the registrations store, "
            "then re-run." % (type(e).__name__, e))

    missing = sorted(e for e in ledger if e not in effective)
    if missing:
        raise EnlargementViolation(
            "enlarged-ledger member(s) lacking a registration (conservation-accounting "
            "defect, full coverage required): %s. "
            "FIX: run `python deploy/register-intake.py` to register the missing event(s) "
            "-- on a fresh instance where it has never run, this is expected: registration "
            "must happen before the census can see any raw event." % missing)

    for e, rec in effective.items():
        if e in ledger:
            ledger[e]["registration"] = rec
            ledger[e]["pointer_class"] = bool(rec.get("asserts_corpus_state"))
        # a registration for an event not in this ledger build (e.g. a raw/ event
        # excluded by some future filter) is not this function's concern -- coverage
        # is checked ledger -> registrations, never the reverse, matching adjudication
        # 4's "one record per LEDGER event" framing.

    _apply_supersession(ledger, supersedes)
    return ledger


def load_receipts(root):
    """Legacy-prose receipt population for build_journal's ACC-2 hole scan. B-2
    (steady-state-ops, 2026-07-09): enumerates via
    registrations.list_receipts_population(root) -- the SAME shared function
    _iter_receipts_only (above) already uses for the enlarged-ledger receipts
    population, loaded the same sibling-import way -- rather than the raw
    _iter_md recursive walk this used before. _iter_md was UNFILTERED: after a
    live compile, the verify leg's own receipts/verify/packets/*.md (plain
    markdown, no frontmatter) was swept in as a "receipt" and flagged a NEW
    journal hole (is_hole -> "no-frontmatter") even though it is an engine
    machine sidecar, never a legacy-prose ledger receipt -- staleness exited
    FAIL on the engine's own output. list_receipts_population already excludes
    every ENGINE_SIDECAR_DIRS member (receipts/journal/, receipts/registrations/,
    receipts/verify/) and never raises on a missing receipts/ -- no legitimate
    legacy-prose receipt is ever written under those dirs, so the exclusion is
    safe. Return shape ({rel: text}) is unchanged."""
    receipts = {}
    if not os.path.isdir(os.path.join(root, "receipts")):
        return receipts
    regs_mod = _load_sibling("registrations.py", "registrations_staleness_load_receipts")
    for rel in regs_mod.list_receipts_population(root):
        try:
            receipts[rel] = _read(os.path.join(root, rel))
        except (OSError, UnicodeDecodeError):
            receipts[rel] = ""
    return receipts


def load_known_holes(root):
    """Merge entities.yaml `known_holes:` (if present) with the sibling known-holes.yaml."""
    holes = set()
    if yaml is None:
        return holes
    for f in (_ENTITIES_FILE, _KNOWN_HOLES_FILE):
        if os.path.isfile(f):
            try:
                data = yaml.safe_load(_read(f)) or {}
                for h in (data.get("known_holes") or []):
                    holes.add(str(h).strip())
            except (OSError, UnicodeDecodeError, yaml.YAMLError):
                continue
    return holes


def _load_alias_map(path=None):
    """R-3 (alias-aware routing): parse entities.yaml's governed vocabulary into
    {alias_lower: entity_name_lower}, so load_matches can route an event tagged with a
    registered ALIAS (e.g. `gateway-ops`) to a view that subscribes to the ENTITY
    (`gateway`), not just the literal tag M(E) compared before. Same defensive-loader
    contract as load_known_holes above: yaml unavailable, or the file missing /
    unreadable / malformed, never crashes the census -- degrades to ({}, []), and
    routing falls back to literal-tag-only matching (today's behavior, unchanged).
    `path` defaults to _ENTITIES_FILE; overridable so the self-test can point this at a
    fixture file without touching the real repo entities.yaml.

    COLLISION RULE: entities.yaml's own header says each alias resolves to EXACTLY ONE
    entity. If an alias is claimed by two different entities (including an alias that
    collides with a second entity's own name), it is DROPPED from the map entirely --
    conservative: an ambiguous alias must not silently pick a side and mis-route; the
    literal tag can still match a subscribes entry directly, unaffected. Returns
    (alias_map, collisions) where collisions is a sorted list of human-readable strings
    for run_census to surface as census warnings (never a hard failure -- a governed-
    vocabulary defect, not a conservation problem)."""
    path = _ENTITIES_FILE if path is None else path
    if yaml is None or not os.path.isfile(path):
        return {}, []
    try:
        data = yaml.safe_load(_read(path)) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return {}, []
    entities = data.get("entities") if isinstance(data, dict) else None
    if not isinstance(entities, dict):
        return {}, []
    claims = {}  # alias_lower -> set of entity_lower claiming it
    for ent_name, ent_body in entities.items():
        ent = str(ent_name).strip().lower()
        if not ent:
            continue
        claims.setdefault(ent, set()).add(ent)
        # STRUCTURAL hardening (cross-vendor review finding): a syntactically-valid
        # entities.yaml can still be structurally malformed (entity value a scalar,
        # `aliases: 5`, aliases a dict) -- those shapes parse cleanly, so the
        # try/except above never sees them, and a bare `for a in aliases` would
        # TypeError and crash the census. Degrade instead: a non-dict entity body has
        # no aliases; a non-list aliases value contributes none; items str()-coerce.
        aliases = ent_body.get("aliases") if isinstance(ent_body, dict) else None
        if not isinstance(aliases, list):
            aliases = []
        for a in aliases:
            a = str(a).strip().lower()
            if a:
                claims.setdefault(a, set()).add(ent)
    alias_map = {}
    collisions = []
    for alias, ents in claims.items():
        if len(ents) == 1:
            alias_map[alias] = next(iter(ents))
        else:
            collisions.append(
                "alias '%s' claimed by entities %s"
                % (alias, " and ".join("'%s'" % e for e in sorted(ents))))
    return alias_map, sorted(collisions)


def load_residue(root):
    residue = set()
    if yaml is None or not os.path.isfile(_RESIDUE_FILE):
        return residue
    try:
        data = yaml.safe_load(_read(_RESIDUE_FILE)) or {}
        entries = data.get("residue") if isinstance(data, dict) else data
        for ent in (entries or []):
            if isinstance(ent, dict) and ent.get("event"):
                residue.add(str(ent["event"]).strip())
            elif isinstance(ent, str):
                residue.add(ent.strip())
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        pass
    return residue


def _norm_entities(tags):
    return set(str(t).strip().lower() for t in _as_list(tags))


def _derivation_subscribes(text):
    """Extract subscribes.entities from a view's derivation block (or empty set)."""
    lines = text.splitlines()
    start = end = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if start is None and s.startswith(DERIV_START):
            start = i
        elif start is not None and s.startswith(DERIV_END):
            end = i
            break
    if start is None or end is None:
        return set()
    in_subscribes = False
    out = set()
    for ln in lines[start + 1:end]:
        if re.match(r"^subscribes:\s*$", ln):
            in_subscribes = True
            continue
        if in_subscribes:
            if ln[:1] not in (" ", "\t"):
                in_subscribes = False
            else:
                m = re.match(r"^\s+entities:\s*\[(.*)\]\s*$", ln)
                if m:
                    for t in m.group(1).split(","):
                        t = t.strip().lower()
                        if t:
                            out.add(t)
    return out


def load_matches(root, ledger):
    """M(E) = subscribes.entities match U cascades_to naming. Pre-backfill / pre-entities
    this is mostly empty (no view carries a derivation block yet) -> events read UNROUTED,
    the correct pre-engine state. Built defensively so it never crashes the census."""
    view_subs = {}
    wiki_root = os.path.join(root, "wiki")
    if os.path.isdir(wiki_root):
        for fp in _iter_md(wiki_root):
            rel = _rel(fp, root)
            try:
                subs = _derivation_subscribes(_read(fp))
            except (OSError, UnicodeDecodeError):
                continue
            if subs:
                view_subs[rel] = subs
    matches = {}
    alias_map, _collisions = _load_alias_map()
    for e, meta in ledger.items():
        m = set()
        ents = _norm_entities(meta.get("tags"))
        # R-3 (alias-aware routing): resolve each raw tag through the governed
        # entities.yaml alias vocabulary before intersecting with subscribes.entities --
        # an event tagged with a registered ALIAS (e.g. `gateway-ops`) now routes the
        # same as one tagged with the ENTITY itself (`gateway`). UNION, not replacement
        # (cross-vendor review finding): the LITERAL tag is KEPT alongside the resolved
        # entity, so a view that literally subscribes to the alias string itself keeps
        # matching. Alias awareness only ever WIDENS M(E) -- additive-only; a match
        # that existed pre-R-3 can never be lost.
        ents = ents | set(alias_map[t] for t in ents if t in alias_map)
        for view, subs in view_subs.items():
            if ents & subs:
                m.add(view)
        for c in _as_list(meta.get("cascades_to")):
            m.add(str(c).strip())
        matches[e] = m
    return matches


def _event_age_days(event_id, meta, now):
    """Days since the event's date (from `date:` frontmatter, else the filename prefix).
    None if undeterminable."""
    raw = None
    d = meta.get("date")
    if d:
        m = _DATE_RE.search(str(d))
        if m:
            raw = m.group(0)
    if raw is None:
        m = _DATE_RE.search(os.path.basename(event_id))
        if m:
            raw = m.group(0)
    if raw is None:
        return None
    try:
        ev = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (now - ev).days


# ---------------------------------------------------------------------------
# B-3 (steady-state-ops, 2026-07-09): project the compile-v2 JSON run-journal.
# ---------------------------------------------------------------------------
# The PRE-SEED NOTE (module docstring ~60-64) flagged this as deferred: until
# now `journal` (consulted by _classify_one via _present) was built ONLY from
# legacy-prose receipts (build_journal, above) -- receipts/journal/<seq>.json,
# the REAL compile-v2 run journal P1 seeding writes, was never read, so a
# genuinely compiled+journaled event stayed UNROUTED/PENDING forever. This
# section reads that JSON run journal and projects it into the SAME
# {event: {view: {"disposition":..., "attested_by":...}}} shape build_journal
# already produces, so run_census can merge the two additively (see the
# wiring inside run_census, below).

def _iter_journal_records(root):
    """Yield (seq, record) for every receipts/journal/<seq>.json sidecar
    (compile-core.py's JOURNAL_DIR), ascending seq order. A record that fails
    to parse (malformed JSON / unreadable file) is SKIPPED, never raised --
    the same conservative direction every other loader in this module takes:
    an unreadable record's claims are simply absent (its events re-present),
    never assumed-absorbed, and this never crashes the census."""
    jroot = os.path.join(root, "receipts", "journal")
    if not os.path.isdir(jroot):
        return
    seqs = []
    for f in os.listdir(jroot):
        if f.endswith(".json") and f[:-5].isdigit():
            seqs.append(int(f[:-5]))
    for seq in sorted(seqs):
        p = os.path.join(jroot, "%d.json" % seq)
        try:
            with open(p, "r", encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if isinstance(rec, dict):
            yield seq, rec


def project_run_journal(root):
    """Project receipts/journal/*.json into the classifier's journal shape:
    {event: {view: {"disposition": "absorbed"|"noop_pending_verification",
    "attested_by": set|None}}}. Built ENTIRELY from the JSON run journal --
    independent of, and additive to, build_journal's legacy-prose bootstrap
    (see the wiring inside run_census). An event/view with no sidecar
    contributes nothing (empty dict), so a non-journaled event is
    byte-unchanged.

    Schema (read verbatim, not guessed -- `git show live-compile-pilot:
    receipts/journal/1.json`, a "compile" record, and `:2.json`, the "verify"
    record covering it; compile-v2.py's run()/verify_run() writers;
    compile-core.py's RECORD_REQUIRED/ABSORBED_REQUIRED write-time schema
    gate):

      compile-core.minimal_record seeds EVERY record (compile or verify) with
      `absorbed: []` and `noop_candidates: []`; validate_record requires both
      as lists. A "verify" record's `absorbed` is always [] in practice
      (verify_run never populates it) -- only a "compile" record's absorbed[]
      is ever non-empty.

      * absorbed[] entries: {view, events, pre_blob, post_blob, manifest,
        corpus_support} -- EVERY event in `events` was absorbed into `view`
        this run. -> disposition "absorbed" for each (event, view) pair,
        attested_by=None until a verify record confirms it (see
        absorption_verified below).

      * noop_candidates[] entries: {view, event, verified, disposition, ...}
        where `disposition` is compile-v2's OWN "CONSUMED" /
        "PENDING_NOOP_CANDIDATE" vocabulary -- a DIFFERENT namespace from this
        module's per-pair disposition strings (_ABSORBED_DISPOSITIONS /
        _NOOP_DISPOSITIONS, ~142-145). A non-lockish no-op is final at compile
        time ("CONSUMED", verified False -- compile-v2.run()'s is_lock_class
        branch) -> projects to "absorbed" (an unattested compile-time
        judgement call; nothing further gates it). A lockish no-op
        ("PENDING_NOOP_CANDIDATE") projects to "noop_pending_verification"
        until a LATER record's noop_candidates[] shows verified=True /
        disposition CONSUMED for the same pair. A verify record's own
        noop_candidates[] is a FULL replacement copy of the compile record's
        list (compile-v2.verify_run's out_ncs), so processing every record's
        noop_candidates[] in ascending seq order under an upgrade-only merge
        (never downgrades an already-"absorbed" pair) lets the flip land
        correctly without separately tracking verifies_seq bookkeeping.

      * a verify record's absorption_verified[] entries confirm specific
        (view, events) pairs FROM AN EARLIER COMPILE RECORD's absorbed[]:
        {view, events, verified_at, artifact, packet_sha256, view_sha256,
        substrate}. substrate["verifier_vendor"] / substrate["verifier_
        model_id"] name the verifier identity (e.g. "openai" / "gpt-5.5" in
        the real seq-2 record -- cross-substrate from the seq-1 absorb
        backend's "anthropic" / "claude-sonnet-5"). A confirmed pair's
        attested_by gains "<vendor>:<model_id>" (falling back to the artifact
        path if substrate is absent -- still a legitimate receipt-like
        identifier, the same shape attested_by already carries for
        legacy-prose pairs).

    SCHEMA SURPRISE (flagged, not silently papered over): a noop_candidates[]
    verified flip carries NO substrate/vendor-model field on the entry itself
    -- only artifact/packet_sha256/verified_at (compile-v2.verify_run folds
    the verdict's substrate into the REFERENCED ARTIFACT FILE, never into the
    noop_candidate entry). So a noop-sourced "absorbed" pair is always
    attested_by=None (unattested) here -- ONLY the absorbed[] /
    absorption_verified[] path (a full-view absorption confirm) yields a
    populated attested_by. This is a real, confirmed schema asymmetry between
    the two confirmation paths, not an oversight in this projection."""
    out = {}

    def upsert(event, view, disposition):
        if not event or not view:
            return
        views = out.setdefault(event, {})
        entry = views.get(view)
        if entry is None:
            views[view] = {"disposition": disposition, "attested_by": None}
        elif entry["disposition"] != "absorbed":
            entry["disposition"] = disposition

    for _seq, rec in _iter_journal_records(root):
        for a in rec.get("absorbed") or []:
            view = a.get("view")
            for e in (a.get("events") or []):
                upsert(e, view, "absorbed")
        for nc in rec.get("noop_candidates") or []:
            view, event = nc.get("view"), nc.get("event")
            final = nc.get("verified") is True or nc.get("disposition") == "CONSUMED"
            upsert(event, view, "absorbed" if final else "noop_pending_verification")
        for av in rec.get("absorption_verified") or []:
            view = av.get("view")
            sub = av.get("substrate") or {}
            vendor, model = sub.get("verifier_vendor"), sub.get("verifier_model_id")
            ident = ("%s:%s" % (vendor, model)) if (vendor or model) else av.get("artifact")
            for e in (av.get("events") or []):
                entry = out.get(e, {}).get(view)
                if entry is not None and entry["disposition"] == "absorbed" and ident:
                    if entry["attested_by"] is None:
                        entry["attested_by"] = set()
                    entry["attested_by"].add(ident)
    return out


# ---------------------------------------------------------------------------
# Runtime census + report.
# ---------------------------------------------------------------------------

def _ref_skip_subbucket(ledger, census_ref_skipped):
    """P5 (adjudication 2): partition the REF-SKIPPED bucket into `ref` vs
    `pointer-class` without inventing an 8th class. Additive census output only --
    existing `counts`/`census` keys are untouched by this helper's caller."""
    sub = {"ref": [], "pointer-class": []}
    for e in census_ref_skipped:
        reason = _ref_skip_reason(ledger.get(e, {}))
        # reason is never None here (e is in REF-SKIPPED by construction), but degrade
        # to "ref" rather than crash if some future caller feeds an inconsistent ledger.
        sub[reason or "ref"].append(e)
    return sub


def run_census(root, now=None, enlarged=True):
    """Load the live tree and run the conservation audit. Returns a result dict.

    enlarged=True (DEFAULT as of the P5 atomic flip, adjudication 6): the standard
    census path runs ENLARGED. Pass enlarged=False explicitly for the pre-P5
    byte-identical behavior (archaeology / opt-out) -- see load_ledger's
    docstring. P5 enlarged-ledger census (component C2); adds the
    REF-SKIPPED `ref`/`pointer-class` sub-bucket under a new `ref_skipped_subbucket`
    report key (additive; every existing key/shape is unchanged)."""
    now = now or date.today()
    ledger = load_ledger(root, enlarged=enlarged)
    receipts = load_receipts(root)
    known = load_known_holes(root)
    residue = load_residue(root)
    journal, holes_report = build_journal(receipts, known)
    holes = set(rid for (rid, _a, _ok) in holes_report)
    matches = load_matches(root, ledger)
    # R-3 (alias-aware routing): load_matches already loads its OWN copy of the alias
    # map internally to do the routing; this second (cheap, small-file) load is just to
    # get the collisions list back out for warning surfacing below, without changing
    # load_matches's return shape (still plain {event: {view...}}) or its sole call
    # site -- the smaller diff over threading collisions through load_matches's return.
    _, _alias_collisions = _load_alias_map()

    # B-3 (steady-state-ops, 2026-07-09): project the compile-v2 JSON run
    # journal on top of the legacy-prose bootstrap -- AUGMENTS matches/journal,
    # never replaces them. A genuinely compiled+journaled event routes to
    # where it ACTUALLY went (ground truth from receipts/journal/, independent
    # of tag-routing); hole detection and the conservation_audit side
    # conditions below run over the (now-augmented) journal exactly as they
    # already did -- neither is suppressed by this projection.
    #
    # INTEGRITY GATE (steady-state-ops, 2026-07-09, cross-vendor review finding): the
    # projection above must NOT trust receipts/journal/*.json unconditionally -- a
    # forged/tampered sidecar (a hand-edited absorbed[] claim, a spliced-in record
    # with a wrong/omitted prev_record_hash, a gap, a duplicate seq) could otherwise
    # launder an event to CONSUMED on the conservation census with no verification at
    # all. compile-core.check_chain is the SAME hash-chain integrity gate
    # compile-backends.py's absorb/verify legs already trust (contiguous seqs from 1,
    # each prev_record_hash == sha256 of the PRIOR record's raw bytes; gap/duplicate/
    # mismatch/schema-invalid -> JournalViolation) -- the conservation gate must hold
    # the run journal to the SAME bar before treating it as ground truth, never a
    # lower one. FAIL CLOSED: on a JournalViolation, the projection loop below is
    # skipped ENTIRELY -- never partially trusted -- so every event simply
    # re-presents via the legacy-prose/tag-routed view exactly as it did pre-B-3
    # (conservative: never falsely absorbed); the violation is appended to
    # `problems` once conservation_audit returns it below (the same field
    # _exit_code checks), so a tampered journal makes staleness exit 1 with a named
    # reason, never a silent drop to the legacy view. A MISSING receipts/journal/
    # dir is NOT a violation -- check_chain(root) returns 0 cleanly, same as always.
    #
    # RESIDUAL, named not closed here: check_chain proves the CHAIN is unbroken
    # (every existing record is byte-identical to what its successor's
    # prev_record_hash commits to, seqs contiguous from 1) -- it does NOT prove any
    # one record's CONTENT is true. A forged record freshly appended with a
    # correctly-computed prev_record_hash (i.e. minted through the real write path,
    # or hand-crafted to match) chains cleanly and is indistinguishable here from a
    # genuine compile-v2 output. That is the SAME trust model legacy-prose receipts
    # already carry (a syntactically well-formed receipt is trusted at face value),
    # and is the named scope of test-plan ACC-3 * JOURNAL-COHERENCE (journal <->
    # frontmatter <-> commit diff -- does the journal's absorbed[] claim match what
    # the view file / commit actually shows), not of this gate -- out of B-3 scope,
    # flagged rather than silently assumed away.
    _compile_core = _load_sibling("compile-core.py", "compile_core_staleness")
    journal_chain_problem = None
    try:
        _compile_core.check_chain(root)
    except _compile_core.JournalViolation as ex:
        journal_chain_problem = "run-journal chain integrity failure: %s" % ex

    if journal_chain_problem is None:
        for e, views in project_run_journal(root).items():
            for v, entry in views.items():
                matches.setdefault(e, set()).add(v)
                journal.setdefault(e, {})[v] = entry

    def view_exists(v):
        return os.path.isfile(os.path.join(root, v))

    result, problems = conservation_audit(
        ledger, journal, matches, residue, holes, view_exists=view_exists)
    if journal_chain_problem:
        problems.append(journal_chain_problem)
    census = {c: [] for c in CLASSES}
    for e, c in sorted(result.items()):
        census[c].append(e)
    warnings = []
    for e in census["PENDING"]:
        age = _event_age_days(e, ledger.get(e, {}), now)
        if age is not None and age > PARTIAL_CANDIDATE_DAYS:
            warnings.append({"event": e, "age_days": age})
    # R-3 (alias-aware routing): an alias claimed by two entities is a governed-
    # vocabulary defect, not a conservation problem -- surfaced as a WARNING (never
    # appended to `problems`, never fails the census on its own) alongside the
    # existing age-based PENDING warnings above.
    for _c in _alias_collisions:
        warnings.append("entities.yaml alias collision: " + _c)
    report = {
        "schema_version": SCHEMA_VERSION,
        "ledger_events": len(ledger),
        "receipts": len(receipts),
        "census": census,
        "counts": {c: len(census[c]) for c in CLASSES},
        "journal_holes": [
            {"receipt": rid, "archetype": a, "allowlisted": ok}
            for (rid, a, ok) in sorted(holes_report)
        ],
        "new_holes": sorted(new_holes(holes_report)),
        "warnings": warnings,
        "problems": problems,
    }
    if enlarged:
        sub = _ref_skip_subbucket(ledger, census["REF-SKIPPED"])
        report["ref_skipped_subbucket"] = {
            "ref": sorted(sub["ref"]),
            "pointer-class": sorted(sub["pointer-class"]),
            "counts": {"ref": len(sub["ref"]), "pointer-class": len(sub["pointer-class"])},
        }
    return report


def _exit_code(report, strict=False):
    if report.get("problems"):
        return 1
    if report.get("new_holes"):
        return 1
    if strict and report.get("warnings"):
        return 1
    return 0


def run(root, report_json=False, strict=False):
    if yaml is None:
        print("RESULT: INCONCLUSIVE -- PyYAML unavailable; cannot parse the receipt journal")
        return 2
    # P5 atomic flip (adjudication 6): the standard census/load path runs ENLARGED
    # by default now -- explicit here for readability (run_census's own default
    # already carries this; see its docstring).
    #
    # Stranger-test RUN 1 (2026-07-24), Finding 5: on a fresh instance, before
    # deploy/register-intake.py has ever run, the registrations store is empty and
    # EVERY ledger event is "missing a registration" -- EnlargementViolation, uncaught
    # here, surfaced as a bare Python traceback. That violates the fail-loud-with-a-
    # FIX-line contract TOUR.md Stage 1 promises for this sensor. Catch it and report
    # the same way every other FAIL does (RESULT line + FIX:), never a stack trace --
    # the EnlargementViolation message itself already carries the FIX: text (see
    # load_ledger).
    try:
        report = run_census(root, enlarged=True)
    except EnlargementViolation as e:
        print("RESULT: FAIL -- %s" % e)
        return 1
    code = _exit_code(report, strict=strict)
    if report_json:
        print(json.dumps(report, indent=2, sort_keys=True, default=list))
        return code
    for c in CLASSES:
        n = report["counts"][c]
        if n:
            print("  %-22s %d" % (c, n))
    for h in report["journal_holes"]:
        tag = "NOTE" if h["allowlisted"] else "NEW-HOLE"
        print("  %-8s %s (%s)" % (tag, h["receipt"], h["archetype"]))
    for w in report["warnings"]:
        # R-3: warnings is now heterogeneous -- the original age-based PENDING dicts,
        # PLUS plain alias-collision strings appended in run_census. Never crash either
        # shape.
        if isinstance(w, dict):
            print("  WARN     PENDING %s (age %d days > %d)"
                  % (w["event"], w["age_days"], PARTIAL_CANDIDATE_DAYS))
        else:
            print("  WARN     %s" % w)
    for p in report["problems"]:
        print("  PROBLEM  %s" % p)
    if code == 0:
        print("RESULT: PASS -- %d event(s) classified, %d known hole(s), 0 new, %d warning(s)"
              % (report["ledger_events"], len(report["journal_holes"]), len(report["warnings"])))
    else:
        print("RESULT: FAIL -- %d new hole(s), %d conservation problem(s)"
              % (len(report["new_holes"]), len(report["problems"])))
    return code


# ---------------------------------------------------------------------------
# P5 (component C2): ACC-1 re-baseline gate runner surface. Baseline = the enlarged-
# ledger census (counts per class + the ref/pointer-class sub-bucket + total + a
# content hash of the sorted per-event class map), as JSON. This is the OPS-4
# `--verdict` precedent applied here: a committed baseline file is a re-runnable
# receipt, never a memory -- --baseline-check re-derives the census from the live
# tree and diffs, it never trusts a cached verdict.
# ---------------------------------------------------------------------------

def _class_map_hash(result):
    """Content hash of the sorted per-event class map -- changes iff any event's
    resolved class changes (add/remove/reclassify), stable under key reordering."""
    blob = json.dumps(dict(sorted(result.items())), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def baseline_census(root, now=None):
    """Build the ACC-1-rebaselined baseline payload: the ENLARGED-ledger census
    (enlarged=True always -- this is the P5 re-baseline surface, adjudication 6/C2).
    Raises EnlargementViolation (propagated, not swallowed) under the same conditions
    load_ledger(enlarged=True) does -- a baseline minted over a broken/incomplete
    registration chain is not a baseline."""
    report = run_census(root, now=now, enlarged=True)
    ledger = load_ledger(root, enlarged=True)
    # Recompute the raw per-event class map (report["census"] is class->[events]) for
    # the content hash -- invert once here rather than thread an extra return value
    # through run_census's existing signature.
    class_map = {}
    for c, events in report["census"].items():
        for e in events:
            class_map[e] = c
    return {
        "schema_version": SCHEMA_VERSION,
        "enlarged": True,
        "ledger_events": report["ledger_events"],
        "counts": dict(report["counts"]),
        "ref_skipped_subbucket": dict(report["ref_skipped_subbucket"]),
        "total": len(ledger),
        "class_map_hash": _class_map_hash(class_map),
    }


def write_baseline(root, path, now=None):
    """Mint the baseline JSON at `path`. Does not itself decide whether the mint is
    appropriate for the live tree (deploy/evidence/ vs a fixture) -- the caller
    controls the destination path; this wave writes only to fixture/tempdir paths
    (the live deploy/evidence/ baseline is minted at the atomic flip, out of scope
    here per the design's component-C2 boundary)."""
    payload = baseline_census(root, now=now)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    return payload


class BaselineMismatch(Exception):
    """--baseline-check: the re-run census differs from the committed baseline."""
    pass


def _diff_baseline(committed, current):
    """Named differences between a committed baseline and a freshly recomputed one.
    Returns a list of human-readable diff strings (empty list = match)."""
    diffs = []
    for key in ("schema_version", "ledger_events", "total", "class_map_hash"):
        if committed.get(key) != current.get(key):
            diffs.append("%s: committed=%r current=%r" % (key, committed.get(key), current.get(key)))
    for key in ("counts", "ref_skipped_subbucket"):
        cv, kv = committed.get(key, {}), current.get(key, {})
        if cv != kv:
            diffs.append("%s: committed=%r current=%r" % (key, cv, kv))
    return diffs


def check_baseline(root, path, now=None):
    """Re-run the enlarged-ledger census and diff against the committed baseline at
    `path`. Returns (ok: bool, diffs: list[str]). Never mutates `path`."""
    with open(path, "r", encoding="utf-8") as fh:
        committed = json.load(fh)
    current = baseline_census(root, now=now)
    diffs = _diff_baseline(committed, current)
    return (not diffs), diffs


# ---------------------------------------------------------------------------
# Self-test: classify per-class fixtures + the 3 ACC-2 hole archetypes + mini-ledger
# + regression guards for the adversarial-verification findings.
# ---------------------------------------------------------------------------

# Byte-faithful ACC-2 hole archetypes (em-dash + circled-3 smoke-test the cp1252 trap).
_HOLE_NO_FRONTMATTER = "# Phase 3 restructure\n\nNo frontmatter at all -- starts with a heading.\n"
_HOLE_UNCLOSED_FENCE = "---\ntype: compile\nnote: opening fence never closes\nmore: lines\n"
_HOLE_UNQUOTED_COLON = (
    "---\n"
    "type: compile\n"
    "timestamp: 2026-06-10T23:59:00\n"
    "articles_modified:\n"
    "  - path: wiki/x.md\n"
    "    operation: updated (corpus reconcile — Layer 1 Dashboard: Session ③ ...)\n"
    "---\n"
    "# body\n"
)
_RECEIPT_OK = (
    "---\n"
    "type: compile\n"
    "timestamp: 2026-04-08T00:00:00\n"
    "raw_inputs:\n"
    "  - raw/e-mini.md\n"
    "articles_modified:\n"
    "  - path: wiki/v-mini.md\n"
    "    operation: created\n"
    "---\n"
)
# a YAML-hostile event (unterminated quote -> whole-block parse genuinely fails) that ALSO
# carries an inline tags flow-list AND a multi-target block-sequence supersedes -- exercises
# the degraded line-level fallback for real (the unterminated quote makes PyYAML raise).
_DEGRADED_EVENT_WITH_SUPERSEDES = (
    "---\n"
    "source: ryan\n"
    "date: 2026-06-10\n"
    "tags: [alpha, beta]\n"
    'note: "unterminated quote breaks whole-block YAML parse\n'
    "supersedes:\n"
    "  - raw/a.md\n"
    "  - raw/b.md\n"
    "---\n"
    "body\n"
)


def self_test():
    global _ENTITIES_FILE, yaml  # R-3 fixtures below monkeypatch-and-restore both
    failed = 0
    total = 0

    def case(name, ok):
        nonlocal failed, total
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    V = "wiki/v.md"
    absorbed = {"disposition": "absorbed", "attested_by": None}
    noop = {"disposition": "noop_pending_verification", "attested_by": None}

    def cls(e, ledger, journal, matches, residue=(), holes=()):
        return classify(ledger, journal, matches, set(residue), set(holes))[e]

    # --- ACC-1: one fixture per class ---
    case("REF-SKIPPED fires on source:ref even with M(E) != {}",
         cls("r", {"r": {"source_ref": True}}, {}, {"r": {V}}) == "REF-SKIPPED")
    case("SUPERSEDED-UNCONSUMED: superseded and never absorbed",
         cls("s", {"s": {"source_ref": False, "superseded_by": "later"}},
             {}, {"s": {V}}) == "SUPERSEDED-UNCONSUMED")
    case("CONSUMED: all matched views absorbed",
         cls("c", {"c": {}}, {"c": {V: absorbed}}, {"c": {V}}) == "CONSUMED")
    case("PENDING_NOOP_CANDIDATE: a matched view has an unverified no-op",
         cls("n", {"n": {}}, {"n": {V: noop}}, {"n": {V}}) == "PENDING_NOOP_CANDIDATE")
    case("PENDING: a matched view has no journal entry",
         cls("p", {"p": {}}, {}, {"p": {V}}) == "PENDING")
    case("UNROUTED: M(E) == {} and not on the residue list",
         cls("u", {"u": {}}, {}, {"u": set()}) == "UNROUTED")
    case("RESIDUE: M(E) == {} and on the frozen residue list",
         cls("g", {"g": {}}, {}, {"g": set()}, residue={"g"}) == "RESIDUE")

    # --- ACC-1 precedence ---
    case("REF-SKIPPED outranks supersession AND journal entries",
         cls("r2", {"r2": {"source_ref": True, "superseded_by": "later"}},
             {"r2": {V: absorbed}}, {"r2": {V}}) == "REF-SKIPPED")
    case("SUPERSEDED but absorbed-by-one-view is NOT SUPERSEDED-UNCONSUMED",
         cls("s2", {"s2": {"superseded_by": "later"}}, {"s2": {V: absorbed}},
             {"s2": {V}}) == "CONSUMED")
    case("CONSUMED is universal: one matched view absent -> PENDING",
         cls("c2", {"c2": {}}, {"c2": {V: absorbed}}, {"c2": {V, "wiki/v2.md"}}) == "PENDING")
    case("no-op outranks plain pending across matched views",
         cls("c3", {"c3": {}}, {"c3": {V: noop}}, {"c3": {V, "wiki/v2.md"}})
         == "PENDING_NOOP_CANDIDATE")
    led = {"r": {"source_ref": True}, "u": {}, "p": {}}
    res = classify(led, {}, {"p": {V}}, set(), set())
    case("partition is total (every ledger event classified)",
         set(res) == set(led) and all(v in CLASSES for v in res.values()))

    # --- ACC-1 regression: §6 disposition enum normalises (no AssertionError crash) ---
    for d in ("verified-consumed", "legacy-assumed", "absorbed-without-source"):
        case("disposition %r normalises to absorbed (-> CONSUMED, no crash)" % d,
             cls("d", {"d": {}}, {"d": {V: {"disposition": d, "attested_by": None}}},
                 {"d": {V}}) == "CONSUMED")
    case("an unrecognised disposition degrades to absent (-> PENDING, no crash)",
         cls("d2", {"d2": {}}, {"d2": {V: {"disposition": "weird", "attested_by": None}}},
             {"d2": {V}}) == "PENDING")

    # --- ACC-2: the 3 hole archetypes ---
    case("archetype 1 detected: no-frontmatter", is_hole(_HOLE_NO_FRONTMATTER) == "no-frontmatter")
    case("archetype 2 detected: unclosed-fence", is_hole(_HOLE_UNCLOSED_FENCE) == "unclosed-fence")
    case("archetype 3 detected: unquoted-colon", is_hole(_HOLE_UNQUOTED_COLON) == "parse-fail")
    case("a well-formed receipt is NOT a hole", is_hole(_RECEIPT_OK) is None)

    # --- ACC-2 regression: scalar raw_inputs / articles_modified are NOT char-iterated ---
    case("scalar raw_inputs is one element, not char-iterated",
         _pairs_from_receipt({"raw_inputs": "raw/e.md",
                              "articles_modified": [{"path": "wiki/v.md"}]})
         == [("wiki/v.md", "raw/e.md")])
    case("scalar articles_modified string is one element, not char-iterated",
         _pairs_from_receipt({"raw_inputs": ["raw/e.md"], "articles_modified": "wiki/v.md"})
         == [("wiki/v.md", "raw/e.md")])
    case("non-wiki + projection articles are excluded from the journal",
         _pairs_from_receipt({"raw_inputs": ["raw/e.md"], "articles_modified":
                              ["roadmap/x.md", "wiki/INDEX.md", "CLAUDE.md", "wiki/keep.md"]})
         == [("wiki/keep.md", "raw/e.md")])

    # --- ACC-2: mini-ledger re-present ---
    journal, holes_report = build_journal(
        {"receipts/good.md": _RECEIPT_OK, "receipts/bad.md": _HOLE_UNQUOTED_COLON},
        known_holes=set())
    holes = set(rid for (rid, _a, _ok) in holes_report)
    mini = classify({"raw/e-mini.md": {}}, journal, {"raw/e-mini.md": {"wiki/v-mini.md"}},
                    set(), holes)
    case("parseable receipt seeds the journal (mini-ledger event consumed)",
         mini["raw/e-mini.md"] == "CONSUMED")
    case("the unparseable receipt is reported as a NEW (un-allowlisted) hole",
         "receipts/bad.md" in new_holes(holes_report))

    j2 = {"raw/e-mini.md": {"wiki/v-mini.md": {
        "disposition": "absorbed", "attested_by": {"receipts/bad.md"}}}}
    m2 = classify({"raw/e-mini.md": {}}, j2, {"raw/e-mini.md": {"wiki/v-mini.md"}},
                  set(), {"receipts/bad.md"})
    case("a pair attested only by a hole receipt re-presents as PENDING",
         m2["raw/e-mini.md"] == "PENDING")
    _, hr3 = build_journal({"receipts/bad.md": _HOLE_UNQUOTED_COLON},
                           known_holes={"receipts/bad.md"})
    case("an allowlisted hole is NOT a new hole (stays exit 0)", new_holes(hr3) == [])

    # --- supersession regression: multi-target, basename-collision, degraded list ---
    led2 = {"raw/superseder.md": {"superseded_by": None},
            "raw/a.md": {"superseded_by": None}, "raw/b.md": {"superseded_by": None}}
    _apply_supersession(led2, {"raw/superseder.md": ["raw/a.md", "raw/b.md"]})
    case("multi-target supersedes marks ALL targets (not just the last)",
         led2["raw/a.md"]["superseded_by"] == "raw/superseder.md"
         and led2["raw/b.md"]["superseded_by"] == "raw/superseder.md")
    case("exact path beats a basename collision in another directory",
         _resolve_event("raw/sub2/note.md",
                        ["raw/sub1/note.md", "raw/sub2/note.md"]) == "raw/sub2/note.md")
    case("an ambiguous basename refuses to guess (no false supersession)",
         _resolve_event("note.md", ["raw/sub1/note.md", "raw/sub2/note.md"]) is None)
    # guard against a vacuous test: the fixture MUST genuinely fail whole-block parse
    case("the degraded fixture genuinely fails whole-block YAML parse (test is non-vacuous)",
         is_hole(_DEGRADED_EVENT_WITH_SUPERSEDES) == "parse-fail")
    fm = _event_frontmatter(_DEGRADED_EVENT_WITH_SUPERSEDES)
    case("degraded-parse fallback recovers a multi-line supersedes list",
         fm.get("supersedes") == ["raw/a.md", "raw/b.md"])
    case("degraded-parse fallback recovers an inline tags flow-list as a real list",
         fm.get("tags") == ["alpha", "beta"])

    # --- side conditions ---
    _r, probs = conservation_audit(
        {"c": {}}, {"c": {V: absorbed}}, {"c": {V}}, residue={"c"}, holes=set())
    case("residue admitting an already-consumed event is flagged",
         any("already-consumed" in p for p in probs))
    _r, probs2 = conservation_audit(
        {"x": {}}, {"ghost": {V: absorbed}}, {"x": set()}, residue=set(), holes=set())
    case("orphan journal claim (event not in ledger) is flagged",
         any("orphan" in p and "event" in p for p in probs2))
    _r, probs3 = conservation_audit(
        {"x": {}}, {"x": {"wiki/ghost.md": absorbed}}, {"x": set()}, residue=set(),
        holes=set(), view_exists=lambda v: False)
    case("orphan journal claim (view not a file) is flagged when view_exists given",
         any("orphan" in p and "view" in p for p in probs3))

    # --- age-warn helper ---
    ref_now = date(2026, 6, 30)
    case("event age computed from the filename date prefix",
         _event_age_days("raw/2026-06-01-x.md", {}, ref_now) == 29)
    case("a stale PENDING event exceeds PARTIAL_CANDIDATE_DAYS",
         _event_age_days("raw/2026-06-01-x.md", {}, ref_now) > PARTIAL_CANDIDATE_DAYS)

    # --- P5 (component C2): priority-1 extension (adjudication 2) -- pure classifier ---
    case("pointer-class event (no source:ref) skipped at priority 1 -> REF-SKIPPED",
         cls("pc", {"pc": {"pointer_class": True}}, {}, {"pc": {V}}) == "REF-SKIPPED")
    case("pointer-class event WITH matched views + full absorption still skipped at "
         "priority 1 (never reaches CONSUMED)",
         cls("pc2", {"pc2": {"pointer_class": True}},
             {"pc2": {V: absorbed}}, {"pc2": {V}}) == "REF-SKIPPED")
    case("a lock raw (asserts_corpus_state False, i.e. not pointer_class) classifies "
         "unchanged -- CONSUMED, not swept into REF-SKIPPED",
         cls("lock", {"lock": {"pointer_class": False}},
             {"lock": {V: absorbed}}, {"lock": {V}}) == "CONSUMED")
    case("_ref_skip_reason distinguishes ref vs pointer-class vs neither",
         _ref_skip_reason({"source_ref": True}) == "ref"
         and _ref_skip_reason({"pointer_class": True}) == "pointer-class"
         and _ref_skip_reason({}) is None)
    sub = _ref_skip_subbucket(
        {"r": {"source_ref": True}, "pc": {"pointer_class": True}},
        ["r", "pc"])
    case("census sub-bucket partitions REF-SKIPPED into ref vs pointer-class",
         sub["ref"] == ["r"] and sub["pointer-class"] == ["pc"])

    # --- P5 (component C2): load_ledger(enlarged=...) over tempdir fixture repos ---
    import shutil
    import subprocess
    import tempfile

    def _init_git(base):
        subprocess.run(["git", "-C", base, "init", "-q"], capture_output=True)
        subprocess.run(["git", "-C", base, "config", "user.email", "t@t"], capture_output=True)
        subprocess.run(["git", "-C", base, "config", "user.name", "t"], capture_output=True)

    def _write(base, rel, text):
        p = os.path.join(base, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    regs = _load_sibling("registrations.py", "registrations_staleness_selftest")
    # B-3 integrity gate (2026-07-09): the SAME compile-core.py module the run_census
    # gate itself sibling-loads, used here to MINT fixture journal records through the
    # real compile_core.append_record write path -- so every B-3 fixture below builds
    # a genuinely CHAIN-VALID receipts/journal/ (seq/prev_record_hash engine-assigned,
    # never hand-typed) rather than hand-writing JSON with placeholder hash strings
    # that check_chain would now (correctly) refuse.
    compile_core = _load_sibling("compile-core.py", "compile_core_staleness_selftest")

    # (a) enlarged=False is BYTE-IDENTICAL to today: a regression fixture proving the
    # default path is untouched by any of this -- no receipts/ population, no
    # registration lookup, no import of registrations.py triggered.
    base_default = tempfile.mkdtemp(prefix="staleness-p5-default-")
    try:
        _init_git(base_default)
        _write(base_default, "raw/e1.md", "---\nsource: session\ndate: 2026-01-01\n---\nbody\n")
        _write(base_default, "receipts/r1.md",
               "---\ntype: compile\ntimestamp: 2026-01-01T000000\nraw_inputs:\n"
               "  - raw/e1.md\narticles_modified:\n  - path: wiki/v.md\n"
               "    operation: created\n---\n")
        led_default = load_ledger(base_default, enlarged=False)
        case("enlarged=False: default ledger has ONLY the raw/ event (no receipts, "
             "no pointer_class/registration keys, no registrations.py touch required)",
             set(led_default) == {"raw/e1.md"}
             and "pointer_class" not in led_default["raw/e1.md"]
             and "registration" not in led_default["raw/e1.md"])
        # calling default mode with NO registrations store present at all must not raise
        # -- proves the default path never imports/relies on registrations.py
        case("enlarged=False never raises even with zero registrations store present",
             True)  # reaching here without an exception IS the assertion
    finally:
        shutil.rmtree(base_default, ignore_errors=True)

    # (b) enlarged=True, full registration coverage: receipts enter L as pointer_class;
    # a raw/ lock event registers asserts_corpus_state False (not pointer_class).
    base_full = tempfile.mkdtemp(prefix="staleness-p5-enlarged-")
    try:
        _init_git(base_full)
        _write(base_full, "raw/e1.md", "---\nsource: session\ndate: 2026-01-01\n---\nbody\n")
        _write(base_full, "raw/lock1.md",
               "---\ninformed_by: handoffs/x/output-round-1.md\ndate: 2026-01-02\n---\nbody\n")
        _write(base_full, "receipts/r1.md",
               "---\ntype: compile\ntimestamp: 2026-01-01T000000\nraw_inputs:\n"
               "  - raw/e1.md\narticles_modified:\n  - path: wiki/v.md\n"
               "    operation: created\n---\n")
        regs.append_registration(base_full, {
            "kind": "registration", "event": "raw/e1.md", "origin": "corpus",
            "origin_evidence": "t", "event_class": "session", "event_class_origin": "judgment",
            "asserts_corpus_state": False, "registered_at": "2026-01-01T00:00:00"})
        regs.append_registration(base_full, {
            "kind": "registration", "event": "raw/lock1.md", "origin": "human",
            "origin_evidence": "t", "event_class": "informed_by", "event_class_origin": "explicit",
            "asserts_corpus_state": False, "registered_at": "2026-01-02T00:00:00"})
        regs.append_registration(base_full, {
            "kind": "registration", "event": "receipts/r1.md", "origin": "corpus",
            "origin_evidence": "t", "event_class": "compile", "event_class_origin": "explicit",
            "asserts_corpus_state": True, "registered_at": "2026-01-01T00:00:00"})

        led_full = load_ledger(base_full, enlarged=True)
        case("enlarged=True: ledger includes raw/ events AND the receipt",
             set(led_full) == {"raw/e1.md", "raw/lock1.md", "receipts/r1.md"})
        case("enlarged=True: receipt is pointer_class (asserts_corpus_state True)",
             led_full["receipts/r1.md"]["pointer_class"] is True)
        case("enlarged=True: informed_by lock raw is NOT pointer_class "
             "(asserts_corpus_state False) -- classes unchanged for lock raws",
             led_full["raw/lock1.md"]["pointer_class"] is False)
        case("enlarged=True: plain raw event is NOT pointer_class",
             led_full["raw/e1.md"]["pointer_class"] is False)

        # classify end-to-end: the receipt skips at priority 1 even though it would
        # otherwise match/consume; the lock raw's CONSUMED/PENDING classification is
        # unaffected by enlargement.
        journal_full = {"raw/e1.md": {V: absorbed}}
        matches_full = {"raw/e1.md": {V}, "raw/lock1.md": {V}, "receipts/r1.md": {V}}
        result_full = classify(led_full, journal_full, matches_full, set(), set())
        case("end-to-end: receipt (pointer-class) classifies REF-SKIPPED despite a "
             "full-match view set", result_full["receipts/r1.md"] == "REF-SKIPPED")
        case("end-to-end: plain raw event classifies CONSUMED as before (unaffected "
             "by enlargement)", result_full["raw/e1.md"] == "CONSUMED")
    finally:
        shutil.rmtree(base_full, ignore_errors=True)

    # (c) enlarged=True with a MISSING registration for a ledger member -> loud failure
    base_missing = tempfile.mkdtemp(prefix="staleness-p5-missing-")
    try:
        _init_git(base_missing)
        _write(base_missing, "raw/e1.md", "---\nsource: session\ndate: 2026-01-01\n---\nbody\n")
        _write(base_missing, "receipts/r1.md",
               "---\ntype: compile\ntimestamp: 2026-01-01T000000\nraw_inputs:\n"
               "  - raw/e1.md\narticles_modified:\n  - path: wiki/v.md\n"
               "    operation: created\n---\n")
        # register ONLY the raw/ event -- the receipt has no registration.
        regs.append_registration(base_missing, {
            "kind": "registration", "event": "raw/e1.md", "origin": "corpus",
            "origin_evidence": "t", "event_class": "session", "event_class_origin": "judgment",
            "asserts_corpus_state": False, "registered_at": "2026-01-01T00:00:00"})
        try:
            load_ledger(base_missing, enlarged=True)
            case("enlarged=True: a receipt lacking a registration raises "
                 "EnlargementViolation (loud, never silent-clean)", False)
        except EnlargementViolation as e:
            case("enlarged=True: a receipt lacking a registration raises "
                 "EnlargementViolation (loud, never silent-clean)",
                 "receipts/r1.md" in str(e))
    finally:
        shutil.rmtree(base_missing, ignore_errors=True)

    # (d) enlarged=True with NO registrations store at all (missing chain) -> loud failure
    base_nostore = tempfile.mkdtemp(prefix="staleness-p5-nostore-")
    try:
        _init_git(base_nostore)
        _write(base_nostore, "raw/e1.md", "---\nsource: session\ndate: 2026-01-01\n---\nbody\n")
        try:
            load_ledger(base_nostore, enlarged=True)
            case("enlarged=True: missing registrations store raises EnlargementViolation",
                 False)
        except EnlargementViolation:
            case("enlarged=True: missing registrations store raises EnlargementViolation",
                 True)
    finally:
        shutil.rmtree(base_nostore, ignore_errors=True)

    # (d2) Stranger-test RUN 1 (2026-07-24), Finding 5: the run() CLI entry point --
    # what /sweep and a bare `python deploy/staleness.py` actually invoke -- must FAIL
    # LOUD with a FIX line on this exact pre-registration state, never a bare traceback.
    # Reproduces the fresh-instance failure a stranger operator hit before
    # register-intake.py had ever been run: same setup as (d) (a raw event, no
    # registrations store), but exercised through run() instead of load_ledger directly.
    base_freshrun = tempfile.mkdtemp(prefix="staleness-p5-freshrun-")
    try:
        _init_git(base_freshrun)
        _write(base_freshrun, "raw/e1.md", "---\nsource: session\ndate: 2026-01-01\n---\nbody\n")
        import contextlib
        import io
        buf_fr = io.StringIO()
        with contextlib.redirect_stdout(buf_fr):
            rc_fr = run(base_freshrun)
        out_fr = buf_fr.getvalue()
        case("(d2) run() on a fresh instance with no registrations store returns 1 "
             "cleanly -- never an uncaught EnlargementViolation traceback", rc_fr == 1)
        case("(d2) the failure names the FIX (register-intake.py), not a bare "
             "traceback", "RESULT: FAIL" in out_fr and "FIX:" in out_fr
             and "register-intake.py" in out_fr)
        case("(d2) the stale hardcoded '253-member' figure is gone from the "
             "failure text", "253" not in out_fr)
    finally:
        shutil.rmtree(base_freshrun, ignore_errors=True)

    # (e) B-2 (steady-state-ops brief, 2026-07-08/09): a NEW engine sidecar dir
    # (receipts/verify/packets/) is EXCLUDED from the enlarged-ledger receipts
    # population -- planting a verify-leg sidecar file must NOT raise
    # EnlargementViolation and must NOT appear in the ledger (it is an engine
    # artifact, never a corpus event -- D6: exclusion is the honest boundary,
    # never engine self-attestation).
    base_sidecar = tempfile.mkdtemp(prefix="staleness-p5-sidecar-")
    try:
        _init_git(base_sidecar)
        _write(base_sidecar, "raw/e1.md", "---\nsource: session\ndate: 2026-01-01\n---\nbody\n")
        _write(base_sidecar, "receipts/r1.md",
               "---\ntype: compile\ntimestamp: 2026-01-01T000000\nraw_inputs:\n"
               "  - raw/e1.md\narticles_modified:\n  - path: wiki/v.md\n"
               "    operation: created\n---\n")
        _write(base_sidecar, "wiki/v.md", "# v\n\nplaceholder view (so the receipt's "
               "claimed absorption target resolves -- no orphan-view problem).\n")
        _write(base_sidecar, "receipts/verify/packets/plant.md",
               "# verify leg packet\n\nnot a corpus event -- an engine machine sidecar.\n")
        regs.append_registration(base_sidecar, {
            "kind": "registration", "event": "raw/e1.md", "origin": "corpus",
            "origin_evidence": "t", "event_class": "session", "event_class_origin": "judgment",
            "asserts_corpus_state": False, "registered_at": "2026-01-01T00:00:00"})
        regs.append_registration(base_sidecar, {
            "kind": "registration", "event": "receipts/r1.md", "origin": "corpus",
            "origin_evidence": "t", "event_class": "compile", "event_class_origin": "explicit",
            "asserts_corpus_state": True, "registered_at": "2026-01-01T00:00:00"})
        # NOTE: receipts/verify/packets/plant.md is DELIBERATELY left unregistered --
        # proving the exclusion means it is never required to be.
        try:
            led_sidecar = load_ledger(base_sidecar, enlarged=True)
            case("(e) load_ledger(enlarged=True) does NOT raise EnlargementViolation "
                 "for an unregistered receipts/verify/packets/ sidecar file", True)
        except EnlargementViolation as ex:
            led_sidecar = {}
            case("(e) load_ledger(enlarged=True) does NOT raise EnlargementViolation "
                 "for an unregistered receipts/verify/packets/ sidecar file (got: %s)"
                 % ex, False)
        case("(e) receipts/verify/packets/plant.md is EXCLUDED from the enlarged "
             "ledger (engine sidecar, not a corpus event)",
             "receipts/verify/packets/plant.md" not in led_sidecar)
        case("(e) real ledger members (receipt + raw) still present, sidecar not "
             "swept in", set(led_sidecar) == {"raw/e1.md", "receipts/r1.md"})
        try:
            report_sidecar = run_census(base_sidecar, enlarged=True)
            case("(e) run_census (the full enlarged conservation census) also does "
                 "NOT raise for the unregistered sidecar", True)
            case("(e) census's enlarged ledger counts exactly the 2 real members "
                 "(1 receipt + 1 raw), the sidecar is not swept in",
                 report_sidecar["ledger_events"] == 2)
            # B-2 completion (2026-07-09): the ORIGINAL gap this case never covered --
            # load_receipts (the ACC-2 hole-scanner's own population) also excludes the
            # verify-leg sidecar, so plant.md (frontmatter-less) is never even offered
            # to is_hole -- it must NOT show up as a NEW journal hole, and the census
            # must exit clean (0), not FAIL, on a live compile's own verify output.
            case("(e) load_receipts excludes the verify-leg sidecar too -- it is "
                 "NEVER offered to is_hole, so it reports NO new journal hole",
                 report_sidecar["new_holes"] == [])
            case("(e) the census exits CLEAN (0) -- a live compile's own "
                 "receipts/verify/packets/*.md sidecar must never FAIL staleness",
                 _exit_code(report_sidecar) == 0)
        except EnlargementViolation as ex:
            case("(e) run_census (the full enlarged conservation census) also does "
                 "NOT raise for the unregistered sidecar (got: %s)" % ex, False)
    finally:
        shutil.rmtree(base_sidecar, ignore_errors=True)

    # --- P5 (component C2): --baseline-write / --baseline-check round trip ---
    base_baseline = tempfile.mkdtemp(prefix="staleness-p5-baseline-")
    try:
        _init_git(base_baseline)
        _write(base_baseline, "raw/e1.md", "---\nsource: session\ndate: 2026-01-01\n---\nbody\n")
        _write(base_baseline, "receipts/r1.md",
               "---\ntype: compile\ntimestamp: 2026-01-01T000000\nraw_inputs:\n"
               "  - raw/e1.md\narticles_modified:\n  - path: wiki/v.md\n"
               "    operation: created\n---\n")
        regs.append_registration(base_baseline, {
            "kind": "registration", "event": "raw/e1.md", "origin": "corpus",
            "origin_evidence": "t", "event_class": "session", "event_class_origin": "judgment",
            "asserts_corpus_state": False, "registered_at": "2026-01-01T00:00:00"})
        regs.append_registration(base_baseline, {
            "kind": "registration", "event": "receipts/r1.md", "origin": "corpus",
            "origin_evidence": "t", "event_class": "compile", "event_class_origin": "explicit",
            "asserts_corpus_state": True, "registered_at": "2026-01-01T00:00:00"})

        bpath = os.path.join(base_baseline, "baseline.json")
        payload = write_baseline(base_baseline, bpath)
        case("--baseline-write mints a JSON baseline with the expected shape",
             os.path.isfile(bpath) and payload["total"] == 2
             and "class_map_hash" in payload)

        ok, diffs = check_baseline(base_baseline, bpath)
        case("--baseline-check round-trips clean against its own freshly written baseline",
             ok and diffs == [])

        # mutate the tree (add a new raw event with its own registration) -> mismatch
        _write(base_baseline, "raw/e2.md", "---\nsource: session\ndate: 2026-01-02\n---\nbody\n")
        regs.append_registration(base_baseline, {
            "kind": "registration", "event": "raw/e2.md", "origin": "corpus",
            "origin_evidence": "t", "event_class": "session", "event_class_origin": "judgment",
            "asserts_corpus_state": False, "registered_at": "2026-01-02T00:00:00"})
        ok2, diffs2 = check_baseline(base_baseline, bpath)
        case("--baseline-check detects a mismatch (new event added) with named diffs",
             not ok2 and len(diffs2) > 0
             and any("ledger_events" in d or "class_map_hash" in d for d in diffs2))
    finally:
        shutil.rmtree(base_baseline, ignore_errors=True)

    # --- B-3 (steady-state-ops, 2026-07-09): project receipts/journal/*.json (the
    # REAL compile-v2 run journal) into the classifier -- the PRE-SEED NOTE's
    # deferred wiring. Schema mirrors `git show live-compile-pilot:receipts/
    # journal/{1,2}.json` verbatim. ---
    base_journal = tempfile.mkdtemp(prefix="staleness-b3-journal-")
    try:
        _init_git(base_journal)
        _write(base_journal, "raw/e1.md", "---\nsource: session\ndate: 2026-01-01\n---\nbody\n")
        _write(base_journal, "raw/e2.md", "---\nsource: session\ndate: 2026-01-02\n---\nbody\n")
        _write(base_journal, "wiki/v.md", "# v\n\nplaceholder view.\n")
        regs.append_registration(base_journal, {
            "kind": "registration", "event": "raw/e1.md", "origin": "corpus",
            "origin_evidence": "t", "event_class": "session", "event_class_origin": "judgment",
            "asserts_corpus_state": False, "registered_at": "2026-01-01T00:00:00"})
        regs.append_registration(base_journal, {
            "kind": "registration", "event": "raw/e2.md", "origin": "corpus",
            "origin_evidence": "t", "event_class": "session", "event_class_origin": "judgment",
            "asserts_corpus_state": False, "registered_at": "2026-01-02T00:00:00"})

        # (1) BASELINE: no receipts/journal/ sidecar at all -> M(E) == {} for both
        # events -> both UNROUTED (the PRE-SEED NOTE's documented pre-engine state).
        report_pre = run_census(base_journal, enlarged=True)
        case("B-3 (1) baseline, no journal sidecar: raw/e1.md is UNROUTED",
             "raw/e1.md" in report_pre["census"]["UNROUTED"])
        case("B-3 (1) baseline, no journal sidecar: raw/e2.md is UNROUTED",
             "raw/e2.md" in report_pre["census"]["UNROUTED"])
        case("B-3 direct: project_run_journal over a tree with no receipts/journal/ "
             "at all returns {} (never crashes, contributes nothing)",
             project_run_journal(base_journal) == {})

        # B-3 integrity gate (2026-07-09): minted through compile_core.append_record
        # (never hand-written JSON) so seq/prev_record_hash are engine-assigned and
        # the chain is genuinely valid -- run_census's new check_chain gate (see
        # above) would otherwise refuse a hand-typed placeholder hash.
        compile_rec = {
            "run_type": "compile", "parent_git_sha": "d" * 40,
            "pins": [], "registrations": {},
            "corrections": [], "reconcile_flags": [],
            "run_window": {"start": "2026-07-09T00:00:00", "end": "2026-07-09T00:00:01"},
            "absorbed": [{"view": "wiki/v.md", "events": ["raw/e1.md"],
                          "pre_blob": "a" * 40, "post_blob": "b" * 40,
                          "manifest": [], "corpus_support": []}],
            "noop_candidates": [],
        }
        compile_core.append_record(base_journal, compile_rec)

        # (1) PROJECTED: only raw/e1.md (named in absorbed[]) flips; raw/e2.md
        # (no journal sidecar at all) is byte-unchanged -- the regression guard.
        report_post = run_census(base_journal, enlarged=True)
        case("B-3 (1) projected: raw/e1.md (absorbed[] names it) now classifies "
             "CONSUMED -- the projection is what flips it",
             "raw/e1.md" in report_post["census"]["CONSUMED"])
        case("B-3 (1) raw/e1.md leaves UNROUTED entirely (total partition, "
             "first-match-wins, no double-classification)",
             "raw/e1.md" not in report_post["census"]["UNROUTED"])
        case("B-3 (3) regression: raw/e2.md (no journal sidecar names it) "
             "classifies EXACTLY as before -- still UNROUTED, untouched",
             "raw/e2.md" in report_post["census"]["UNROUTED"])

        # (2) ATTESTATION: a verify record's absorption_verified[] confirming the
        # SAME (event, view) pair sets attested_by to the verifier identity.
        proj_before_verify = project_run_journal(base_journal)
        case("B-3 (2) before a verify record exists, the absorbed pair is "
             "unattested (attested_by None)",
             proj_before_verify["raw/e1.md"]["wiki/v.md"]["attested_by"] is None)

        verify_rec = {
            "run_type": "verify", "parent_git_sha": "d" * 40,
            "pins": [], "registrations": {},
            "corrections": [], "verifies_seq": 1,
            "run_window": {"start": "2026-07-09T00:00:02", "end": "2026-07-09T00:00:03"},
            "absorbed": [], "noop_candidates": [],
            "absorption_verified": [{
                "view": "wiki/v.md", "events": ["raw/e1.md"],
                "verified_at": "2026-07-09T00:00:03",
                "artifact": "receipts/verify/absorb-seq1-v0.json",
                "packet_sha256": "d" * 64, "view_sha256": "e" * 64,
                "substrate": {"verifier_vendor": "openai",
                              "verifier_model_id": "gpt-5.5",
                              "absorb_vendor": "anthropic",
                              "absorb_model_id": "claude-sonnet-5"},
            }],
        }
        # engine-assigned seq/prev_record_hash (chains onto compile_rec above) --
        # same append_record write path as compile_rec, see the note above it.
        compile_core.append_record(base_journal, verify_rec)
        proj_after_verify = project_run_journal(base_journal)
        att = proj_after_verify["raw/e1.md"]["wiki/v.md"]["attested_by"]
        case("B-3 (2) a verify record's absorption_verified[] attests the pair "
             "(attested_by set, non-empty)", bool(att))
        case("B-3 (2) the attested identity names the VERIFIER (vendor:model), "
             "cross-substrate from the absorb backend", att == {"openai:gpt-5.5"})
        report_attested = run_census(base_journal, enlarged=True)
        case("B-3 (2) still classifies CONSUMED once attested (attestation "
             "changes who vouches for it, not the disposition)",
             "raw/e1.md" in report_attested["census"]["CONSUMED"])

        # (4) the projection does NOT suppress real conservation problems or
        # legacy hole detection: plant an unrelated legacy-prose hole receipt
        # alongside the projected absorption -- both behaviors must hold at once.
        _write(base_journal, "receipts/bad.md", _HOLE_NO_FRONTMATTER)
        regs.append_registration(base_journal, {
            "kind": "registration", "event": "receipts/bad.md", "origin": "corpus",
            "origin_evidence": "t", "event_class": "compile", "event_class_origin": "explicit",
            "asserts_corpus_state": False, "registered_at": "2026-01-03T00:00:00"})
        report_hole = run_census(base_journal, enlarged=True)
        case("B-3 (4) a real legacy-prose hole planted alongside the projected "
             "absorption is STILL reported as a NEW hole (not suppressed)",
             "receipts/bad.md" in report_hole["new_holes"])
        case("B-3 (4) the census still FAILs on the new hole (exit 1) -- the "
             "projection does not launder a real conservation problem",
             _exit_code(report_hole) == 1)
        case("B-3 (4) the projected event STILL classifies CONSUMED alongside "
             "the unrelated hole (neither behavior suppresses the other)",
             "raw/e1.md" in report_hole["census"]["CONSUMED"])
    finally:
        shutil.rmtree(base_journal, ignore_errors=True)

    # --- B-3 extension (beyond the 5 mandated self-tests): noop_candidates[]
    # projection -- the schema DOES distinguish a no-op/pending-verification
    # record from a full absorption (compile-v2's OWN "CONSUMED" /
    # "PENDING_NOOP_CANDIDATE" vocabulary on noop_candidates[].disposition, a
    # DIFFERENT namespace from this module's own per-pair disposition strings).
    base_noop = tempfile.mkdtemp(prefix="staleness-b3-noop-")
    try:
        # B-3 integrity gate (2026-07-09): _init_git + compile_core.append_record,
        # same reasoning as base_journal above -- project_run_journal itself doesn't
        # require a valid chain (run_census's gate is the only caller that does), but
        # a fixture claiming to model a real run-journal should BE one, not a
        # hand-typed lookalike with placeholder hashes.
        _init_git(base_noop)
        compile_core.append_record(base_noop, {
            "run_type": "compile", "parent_git_sha": "f" * 40,
            "pins": [], "registrations": {},
            "corrections": [], "absorbed": [],
            "noop_candidates": [
                {"view": "wiki/plain.md", "event": "raw/plain.md",
                 "verified": False, "disposition": "CONSUMED"},
                {"view": "wiki/lock.md", "event": "raw/lock.md",
                 "verified": False, "disposition": "PENDING_NOOP_CANDIDATE"},
            ],
        })
        proj1 = project_run_journal(base_noop)
        case("B-3 noop projection: a non-lockish no-op (compile-v2 disposition "
             "CONSUMED, verified False) projects to 'absorbed' -- final at "
             "compile time, nothing further gates it",
             proj1["raw/plain.md"]["wiki/plain.md"]["disposition"] == "absorbed")
        case("B-3 noop projection: a lockish no-op (PENDING_NOOP_CANDIDATE, "
             "unverified) projects to 'noop_pending_verification' -- still "
             "needs a verify pass",
             proj1["raw/lock.md"]["wiki/lock.md"]["disposition"]
             == "noop_pending_verification")

        # a later verify record's noop_candidates[] (a FULL replacement copy,
        # per compile-v2.verify_run's out_ncs) flips the lockish pair once
        # confirmed -- upgrade-only merge, ordering handles it without tracking
        # verifies_seq bookkeeping explicitly.
        compile_core.append_record(base_noop, {
            "run_type": "verify", "parent_git_sha": "f" * 40,
            "pins": [], "registrations": {},
            "corrections": [], "verifies_seq": 1, "absorbed": [],
            "noop_candidates": [
                {"view": "wiki/plain.md", "event": "raw/plain.md",
                 "verified": False, "disposition": "CONSUMED"},
                {"view": "wiki/lock.md", "event": "raw/lock.md",
                 "verified": True, "verified_at": "2026-07-09T00:00:05",
                 "disposition": "CONSUMED",
                 "artifact": "receipts/verify/noop-seq1-e0.json"},
            ],
        })
        proj2 = project_run_journal(base_noop)
        case("B-3 noop projection: a verify record's flip (verified True) "
             "upgrades the pair from noop_pending_verification to absorbed",
             proj2["raw/lock.md"]["wiki/lock.md"]["disposition"] == "absorbed")
        case("B-3 noop projection: SCHEMA SURPRISE -- a noop-sourced 'absorbed' "
             "pair carries no substrate/vendor-model field, so attested_by "
             "stays None (unattested) even once verified",
             proj2["raw/lock.md"]["wiki/lock.md"]["attested_by"] is None)
    finally:
        shutil.rmtree(base_noop, ignore_errors=True)

    # --- B-3 INTEGRITY GATE (steady-state-ops, 2026-07-09, cross-vendor review
    # finding): run_census must NOT trust receipts/journal/*.json unconditionally --
    # a forged/tampered sidecar must never launder an event to CONSUMED. Build a
    # VALID 2-record chain that makes raw/e1.md CONSUMED, confirm the clean state,
    # THEN tamper seq-1's bytes IN PLACE (forge an extra absorbed event onto an
    # already-chained record) so seq-2's prev_record_hash no longer matches -- and
    # assert (a) the projection is refused (raw/e1.md reverts to UNROUTED, never
    # falsely CONSUMED) and (b) the violation is surfaced LOUD as a named
    # conservation problem with _exit_code == 1 (never a silent drop to the legacy
    # view). ---
    base_tamper = tempfile.mkdtemp(prefix="staleness-b3-tamper-")
    try:
        _init_git(base_tamper)
        _write(base_tamper, "raw/e1.md", "---\nsource: session\ndate: 2026-01-01\n---\nbody\n")
        _write(base_tamper, "wiki/v.md", "# v\n\nplaceholder view.\n")
        regs.append_registration(base_tamper, {
            "kind": "registration", "event": "raw/e1.md", "origin": "corpus",
            "origin_evidence": "t", "event_class": "session", "event_class_origin": "judgment",
            "asserts_corpus_state": False, "registered_at": "2026-01-01T00:00:00"})

        compile_core.append_record(base_tamper, {
            "run_type": "compile", "parent_git_sha": "e" * 40,
            "pins": [], "registrations": {}, "corrections": [],
            "absorbed": [{"view": "wiki/v.md", "events": ["raw/e1.md"],
                          "pre_blob": "a" * 40, "post_blob": "b" * 40,
                          "manifest": [], "corpus_support": []}],
            "noop_candidates": [],
        })
        # seq 2 carries no new claims -- it exists only to chain onto seq 1 via
        # prev_record_hash, giving the tamper below something to invalidate.
        compile_core.append_record(base_tamper, {
            "run_type": "verify", "parent_git_sha": "e" * 40,
            "pins": [], "registrations": {}, "corrections": [], "verifies_seq": 1,
            "absorbed": [], "noop_candidates": [],
        })

        report_valid = run_census(base_tamper, enlarged=True)
        case("B-3 integrity gate: a VALID 2-record chain projects normally -- "
             "raw/e1.md classifies CONSUMED",
             "raw/e1.md" in report_valid["census"]["CONSUMED"])
        case("B-3 integrity gate: a valid chain reports NO conservation problem "
             "and exits clean (0) -- the gate is silent when there is nothing "
             "to complain about",
             report_valid["problems"] == [] and _exit_code(report_valid) == 0)

        # TAMPER: forge an extra absorbed event into seq-1's ON-DISK bytes -- the
        # exact attack this gate closes (a hand-edited/corrupted sidecar claiming an
        # extra absorption). seq-2 already committed to sha256(original seq-1
        # bytes) via prev_record_hash, so mutating seq-1 in place breaks that link.
        p1 = os.path.join(base_tamper, "receipts", "journal", "1.json")
        with open(p1, "r", encoding="utf-8") as fh:
            rec1 = json.load(fh)
        rec1["absorbed"][0]["events"].append("raw/forged-event.md")
        with open(p1, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rec1, indent=1, sort_keys=True))

        try:
            compile_core.check_chain(base_tamper)
            case("B-3 integrity gate: tampering seq-1's bytes IS detected by "
                 "compile_core.check_chain (prev_record_hash mismatch at seq 2)",
                 False)
        except compile_core.JournalViolation as ex:
            case("B-3 integrity gate: tampering seq-1's bytes IS detected by "
                 "compile_core.check_chain (prev_record_hash mismatch at seq 2)",
                 "mismatch" in str(ex))

        report_tampered = run_census(base_tamper, enlarged=True)
        case("B-3 integrity gate (a): a tampered journal is FAIL-CLOSED -- "
             "raw/e1.md no longer classifies CONSUMED (projection refused, "
             "conservative -- never falsely absorbed)",
             "raw/e1.md" not in report_tampered["census"]["CONSUMED"])
        case("B-3 integrity gate (a): raw/e1.md re-presents exactly as it did "
             "pre-projection (UNROUTED) -- not laundered, not lost either",
             "raw/e1.md" in report_tampered["census"]["UNROUTED"])
        case("B-3 integrity gate (b): the violation is surfaced LOUD as a named "
             "conservation problem (never a silent drop to the legacy view)",
             any(p.startswith("run-journal chain integrity failure")
                 for p in report_tampered["problems"]))
        case("B-3 integrity gate (b): a tampered run-journal makes staleness "
             "exit 1 (loud, not silent)",
             _exit_code(report_tampered) == 1)
    finally:
        shutil.rmtree(base_tamper, ignore_errors=True)

    # --- R-3 (alias-aware routing): entities.yaml aliases resolve into M(E) ---
    base_r3 = tempfile.mkdtemp(prefix="staleness-r3-alias-")
    try:
        _write(base_r3, "raw/e1.md",
               "---\nsource: session\ndate: 2026-01-01\ntags: [foometa]\n---\nbody\n")
        _write(base_r3, "wiki/v.md",
               "# --- derivation (engine-managed; strip region) ---\n"
               "schema_version: 3.2\n"
               "view: topic\n"
               "entities: [foo]\n"
               "subscribes:\n"
               "  entities: [foo]\n"
               "  corpus: []\n"
               "# --- /derivation ---\n")
        _write(base_r3, "entities.yaml",
               "entities:\n"
               "  foo:\n"
               "    aliases: [foometa]\n"
               "    views: [wiki/v.md]\n")
        led_r3 = load_ledger(base_r3, enlarged=False)

        _orig_entities_file = _ENTITIES_FILE
        _ENTITIES_FILE = os.path.join(base_r3, "entities.yaml")
        try:
            matches_r3 = load_matches(base_r3, led_r3)
        finally:
            _ENTITIES_FILE = _orig_entities_file
        case("R-3: an event tagged with a registered ALIAS ('foometa') routes to a "
             "view subscribing to the ENTITY it resolves to ('foo')",
             "wiki/v.md" in matches_r3.get("raw/e1.md", set()))
    finally:
        shutil.rmtree(base_r3, ignore_errors=True)

    # (b) collision: an alias claimed by two different entities is DROPPED from the
    # map entirely (conservative -- an ambiguous alias must not route), and the
    # collision is named in the returned list for run_census to surface as a warning.
    base_r3b = tempfile.mkdtemp(prefix="staleness-r3-collision-")
    try:
        _write(base_r3b, "entities.yaml",
               "entities:\n"
               "  e1:\n"
               "    aliases: [dup]\n"
               "    views: [wiki/v.md]\n"
               "  e2:\n"
               "    aliases: [dup]\n"
               "    views: [wiki/v.md]\n")
        alias_map_b, collisions_b = _load_alias_map(
            path=os.path.join(base_r3b, "entities.yaml"))
        case("R-3: an alias claimed by two entities is DROPPED from the alias map "
             "(conservative -- ambiguous alias must not route)",
             "dup" not in alias_map_b)
        case("R-3: the collision is reported as a sorted, human-readable string",
             collisions_b == ["alias 'dup' claimed by entities 'e1' and 'e2'"])

        _write(base_r3b, "raw/e1.md",
               "---\nsource: session\ndate: 2026-01-01\ntags: [dup]\n---\nbody\n")
        _write(base_r3b, "wiki/v.md",
               "# --- derivation (engine-managed; strip region) ---\n"
               "schema_version: 3.2\n"
               "view: topic\n"
               "entities: [e1]\n"
               "subscribes:\n"
               "  entities: [e1]\n"
               "  corpus: []\n"
               "# --- /derivation ---\n")
        led_r3b = load_ledger(base_r3b, enlarged=False)
        _orig_entities_file_b = _ENTITIES_FILE
        _ENTITIES_FILE = os.path.join(base_r3b, "entities.yaml")
        try:
            matches_r3b = load_matches(base_r3b, led_r3b)
        finally:
            _ENTITIES_FILE = _orig_entities_file_b
        case("R-3: the dropped/colliding alias does NOT route the tagged event to a "
             "view subscribing to one of the claimant entities",
             "wiki/v.md" not in matches_r3b.get("raw/e1.md", set()))
    finally:
        shutil.rmtree(base_r3b, ignore_errors=True)

    # (c) degradation: no entities file present -> alias_map {}, no crash, no collisions.
    case("R-3: a missing entities file degrades _load_alias_map to ({}, []) "
         "(routing falls back to literal-tag-only matching, unchanged)",
         _load_alias_map(path=os.path.join(
             tempfile.gettempdir(), "staleness-r3-does-not-exist.yaml")) == ({}, []))

    # (d) degradation: yaml unavailable -> same empty-map degrade, never a crash.
    _orig_yaml = yaml
    yaml = None
    try:
        case("R-3: yaml unavailable degrades _load_alias_map to ({}, []) (no crash)",
             _load_alias_map(path=_ENTITIES_FILE) == ({}, []))
    finally:
        yaml = _orig_yaml

    # (e) UNION regression (cross-vendor review, defect 1): alias mapping must WIDEN
    # M(E), never replace -- a view that literally subscribes to the ALIAS string
    # itself must keep matching an event carrying that alias tag even after the alias
    # resolves to its entity.
    base_r3e = tempfile.mkdtemp(prefix="staleness-r3-union-")
    try:
        _write(base_r3e, "raw/e1.md",
               "---\nsource: session\ndate: 2026-01-01\ntags: [foometa]\n---\nbody\n")
        _write(base_r3e, "wiki/v-literal.md",
               "# --- derivation (engine-managed; strip region) ---\n"
               "schema_version: 3.2\n"
               "view: topic\n"
               "entities: [foometa]\n"
               "subscribes:\n"
               "  entities: [foometa]\n"
               "  corpus: []\n"
               "# --- /derivation ---\n")
        _write(base_r3e, "entities.yaml",
               "entities:\n"
               "  foo:\n"
               "    aliases: [foometa]\n"
               "    views: [wiki/v-literal.md]\n")
        led_r3e = load_ledger(base_r3e, enlarged=False)
        _orig_entities_file_e = _ENTITIES_FILE
        _ENTITIES_FILE = os.path.join(base_r3e, "entities.yaml")
        try:
            matches_r3e = load_matches(base_r3e, led_r3e)
        finally:
            _ENTITIES_FILE = _orig_entities_file_e
        case("R-3 union regression: a view subscribing LITERALLY to the alias string "
             "still matches after alias mapping (widening, never replacement)",
             "wiki/v-literal.md" in matches_r3e.get("raw/e1.md", set()))
    finally:
        shutil.rmtree(base_r3e, ignore_errors=True)

    # (f) STRUCTURAL degradation (cross-vendor review, defect 2): a syntactically-
    # valid but structurally-malformed entities.yaml (scalar aliases, scalar entity
    # body) parses cleanly past the YAMLError guard -- the loader must degrade shape-
    # by-shape, never TypeError-crash the census.
    base_r3f = tempfile.mkdtemp(prefix="staleness-r3-malformed-")
    try:
        _write(base_r3f, "aliases-scalar.yaml",
               "entities:\n"
               "  foo:\n"
               "    aliases: 5\n"
               "  bar:\n"
               "    aliases: [ok-alias]\n")
        am_f1, coll_f1 = _load_alias_map(
            path=os.path.join(base_r3f, "aliases-scalar.yaml"))
        case("R-3 malformed vocabulary: `aliases: 5` never raises -- the bad entity "
             "contributes no aliases; well-formed siblings still map",
             am_f1.get("ok-alias") == "bar" and coll_f1 == [])
        _write(base_r3f, "entity-scalar.yaml",
               "entities:\n"
               "  foo: just-a-string\n"
               "  bar:\n"
               "    aliases: [ok-alias]\n")
        am_f2, _coll_f2 = _load_alias_map(
            path=os.path.join(base_r3f, "entity-scalar.yaml"))
        case("R-3 malformed vocabulary: a scalar entity body never raises -- treated "
             "as having no aliases (its name still self-maps)",
             am_f2.get("foo") == "foo" and am_f2.get("ok-alias") == "bar")
    finally:
        shutil.rmtree(base_r3f, ignore_errors=True)

    # --- -h/--help falls through to usage, never to the live census (CWD-independent) ---
    import contextlib
    import io

    buf_h = io.StringIO()
    with contextlib.redirect_stdout(buf_h):
        rc_h = main(["staleness.py", "-h"])
    out_h = buf_h.getvalue()
    case("-h returns 0, prints usage, and does not run the live census",
         rc_h == 0 and "usage" in out_h.lower() and "RESULT:" not in out_h)

    buf_help = io.StringIO()
    with contextlib.redirect_stdout(buf_help):
        rc_help = main(["staleness.py", "--help"])
    out_help = buf_help.getvalue()
    case("--help returns 0, prints usage, and does not run the live census",
         rc_help == 0 and "usage" in out_help.lower() and "RESULT:" not in out_help)

    if failed:
        print("staleness self-test: FAIL (%d/%d)" % (failed, total))
        return 1
    print("staleness self-test: PASS (%d/%d)" % (total, total))
    return 0


def _arg_value(args, flag):
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return None


def main(argv):
    args = argv[1:]
    if "-h" in args or "--help" in args:
        print("usage: staleness.py [--self-test | --baseline-write PATH | --baseline-check PATH | --report | --strict]")
        print("  --self-test              offline self-test (temp dirs only, no repo access)")
        print("  --baseline-write PATH    mint the enlarged-ledger census baseline")
        print("  --baseline-check PATH    diff the census against a committed baseline")
        print("  --report                 emit the census as JSON")
        print("  --strict                 WARNs escalate to exit 1")
        print("  (default, no args)       live read-only census over raw/ receipts/ wiki/ of the CWD")
        return 0
    if "--self-test" in args:
        return self_test()
    root = os.getcwd()

    baseline_write = _arg_value(args, "--baseline-write")
    if baseline_write is not None:
        try:
            payload = write_baseline(root, baseline_write)
        except EnlargementViolation as e:
            print("REFUSED: %s" % e)
            return 1
        print("baseline written: %s" % baseline_write)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    baseline_check = _arg_value(args, "--baseline-check")
    if baseline_check is not None:
        try:
            ok, diffs = check_baseline(root, baseline_check)
        except EnlargementViolation as e:
            print("REFUSED: %s" % e)
            return 1
        if ok:
            print("RESULT: PASS -- enlarged-ledger census matches baseline %s" % baseline_check)
            return 0
        print("RESULT: FAIL -- enlarged-ledger census diverges from baseline %s" % baseline_check)
        for d in diffs:
            print("  DIFF  %s" % d)
        return 1

    report_json = "--report" in args
    strict = "--strict" in args
    return run(root, report_json=report_json, strict=strict)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
