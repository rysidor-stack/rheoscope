#!/usr/bin/env python3
"""compile-v2.py -- the memory-engine v3 compile orchestration loop (P2).

Implements spec sec.7's judgment-verb ORCHESTRATION with the LLM stages behind
injectable backends, so every mechanical discipline is real and gate-checkable
before any model is wired in (fixture-first doctrine):

  ABSORB     for each work item (stale view + delta events) call the absorb
             backend; VALIDATE its output mechanically (new text parses/bounded,
             merge manifest matches the REAL diff section-for-section, every
             corpus_support line appears verbatim in the cited artifact); no-op
             answers route through the PENDING_NOOP_CANDIDATE discipline -- an
             event whose event_class is T1/correction/lock/informed_by, OR whose
             class was judgment-assigned (F6 conservative default), is NEVER
             consumed on a no-op: it lands as a noop_candidate with verified=false
             until VERIFY confirms against the full event body. Circuit breaker:
             15 rebuilds per run.
  RECONCILE  scripts flag, the backend judges ONLY flags (entity-overlapping
             changed pairs; shipped-state prose changed without corpus_support).
  VERIFY     packet assembly + verdict ingestion behind the verify backend;
             a noop_candidate flips verified=true only on a confirmed verdict
             whose substrate satisfies check-substrate (wired at P2-entry).

Run mechanics (all real, all gate-checked):
  lockfile -> work -> content writes -> ONE journal record (run_window, absorbed
  entries with real pre/post blobs written into the git odb, per-event section
  manifests, no-op justification hashes) -> per-path stage-only commit carrying
  views + record -> release lock. check-run-diff.py --commit <sha> --sections
  passes on every commit this loop produces (asserted by the self-test).

Backends: AbsorbBackend.absorb(view_rel, view_text, events:{rel:text}) ->
  {"new_text": str|None (None = no-op for all events),
   "manifest": [{"event", "section"}], "corpus_support": [{"artifact",
   "support_lines": [str]}], "noops": [{"event", "justification_note"}]}
The FIXTURE backend used by --self-test is deterministic; the agent-dispatch
backend is P2-entry wiring (LLM-2/LLM-1 live legs), not this file's scope.

Usage: compile-v2.py --self-test | --run --root DIR --plan PLAN.json
  [--run-type compile] [--break-stale-lock]
PLAN.json: {"items": [{"view": rel, "events": [rel...],
  "event_class": {rel: {"class": str, "origin": "explicit"|"judgment"}}}]}
Exit: 0 clean run | 3 lock held | 1 validation/gate failure | 2 inconclusive.

2026-07-06 P5 note (memory-engine-v3-p5-typed-events-design-2026-07-06.md,
component C4): this file gains the REGISTRATION CONSULTATION SEAM +
POINTER-CLASS WRITE CEILING + PLAN-PRECEDENCE RULE (adjudications 3 and 4
of the dated design sibling). PRE-MINT INERTNESS: `receipts/registrations/`
does not exist on the live tree yet (the mint is a separate, not-yet-run
step -- C1/backfill-registrations.py). `run()` loads the effective
registration map ONCE per run via `_load_registration_seam` -- if the store
is absent, the map is `{}` and every rule below is inert, so ALL existing
behavior for events without a registration (which today is EVERY event) is
byte-identical to pre-P5. Inertness ends the moment the atomic-flip commit
(component C6/adjudication 6) mints the store and flips the enlargement
default; nothing in this file needs to change at that point.

PLAN-PRECEDENCE RULE (adjudication 4, pre-journal, fixture-first): at
`run()` entry, for every event named in the plan that HAS a registration,
the plan's supplied event_class must not LOOSEN the registered treatment.
"Registered lock-class treatment" = registered event_class in
LOCK_CLASSES, OR registered event_class_origin == "judgment" (F6's
conservative rule, mirrored from the registration side). "Loosens" = the
plan supplies a class NOT in LOCK_CLASSES with origin "explicit" (an
explicit, deliberate downgrade). Loosening -> refused pre-journal, nothing
written, lock released, naming the event/registered class/plan class.
Plan stricter than or equal to the registration -> passes silently.
Unregistered event -> today's behavior exactly (no seam consulted).

POINTER-CLASS WRITE CEILING (adjudication 3 / spec sec.10, pre-journal,
inside `validate_absorb_output`): for an absorb answer whose event(s)
carry a registration with `asserts_corpus_state: true`, the ONLY changes
`new_text` may make relative to `old_text` are (a) link-line changes
(a changed/added Markdown line matching `_LINK_LINE_RE`) and (b)
status-table row changes (a changed/added Markdown table row matching
`_TABLE_ROW_RE`). Any other changed line (prose, heading, anything else)
is refused, naming the section and this ceiling rule. An ADDED
status-table row/cell whose content is attributable to a pointer-class
event must carry the sec.10 attribution form VERBATIM:
  "reported by receipt <event>"
(the literal words "reported by receipt" immediately followed by the
event's repo-relative path -- pinned mechanical form, `_ATTRIBUTION_RE`;
rendered as a claim ABOUT the receipt, never as implementation fact).
Detection is deliberately conservative: a changed line that is ambiguous
between prose and link/table-row is FAIL-CLOSED (refused, named). This
ADDS a ceiling on top of every existing validate_absorb_output check
(CONTENT-1, last_updated, manifest-vs-diff, corpus_support, F16
shipped-state gate) -- it relaxes none of them. Non-pointer events: zero
behavior change (every branch below is gated on the event's registration
carrying asserts_corpus_state=true).
"""

import hashlib
import importlib.util
import json
import os
import posixpath
import re
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(basename, alias):
    spec = importlib.util.spec_from_file_location(alias,
                                                  os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _load("compile-core.py", "compile_core_v2")
crd = _load("check-run-diff.py", "check_run_diff_v2")
rcensus = _load("routing-census.py", "routing_census_v2")
ccs = _load("check-corpus-support.py", "check_corpus_support_v2")
asm = _load("assemble.py", "assemble_v2")
regs = _load("registrations.py", "registrations_v2")

CIRCUIT_BREAKER_REBUILDS = 15
LOCK_CLASSES = {"t1", "correction", "lock", "informed_by"}
VIEW_BYTE_CAP = 200_000

# Shipped-state section detector -- ONE home for both the refusing site
# (validate_absorb_output) and the flagging site (reconcile_flags), which
# carried byte-identical copies of this expression.
#
# WORD-ANCHORED (v3.0-70, 2026-08-06). The unanchored form matched "live" as
# a bare substring, so it fired on every heading merely CONTAINING those
# letters: verified against a live heading set, "Deliverable Dates",
# "Delivery Schedule", "Deliverables", "Olive oil sourcing", "Lively debate"
# and "Livelihood" all matched on `live` -- LAMPS renamed accurate headings
# twice to get past it. Anchoring loses NO coverage: "Shipped state",
# "As-built notes" and "Currently live in production" all still match. This
# closes the substring defect only; whether a heading using "live" as an
# ordinary adjective ("Live lane") should trip the guard at all is the open
# half of v3.0-70, held for an operator ruling -- it still matches here.
_SHIPPED_STATE_RE = re.compile(r"\b(shipped|live|as-built)\b", re.I)


class ValidationError(Exception):
    pass


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git(repo, *args):
    p = subprocess.run(["git", "-C", repo] + list(args), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise ValidationError("git %s: %s" % (args[0], (p.stderr or "")[-200:]))
    return p.stdout


def _blob_of_text(repo, text):
    """Write text into the object db (so blob-vs-blob diffs work pre-commit)."""
    p = subprocess.run(["git", "-C", repo, "hash-object", "-w", "--stdin"],
                       input=text, capture_output=True, text=True,
                       encoding="utf-8")
    return p.stdout.strip()


def changed_sections(repo, pre_blob, post_blob):
    return crd.changed_sections(repo, pre_blob, post_blob)


def _strip_derivation_region(text):
    """Body with the derivation region (DERIV_START..DERIV_END markers
    inclusive) removed -- same convention check-derivation.py's
    _strip_derivation_region uses ("Body" = full text minus the
    engine-managed derivation region). Absent/malformed region: returns
    text unchanged. Used so a `verified:` stamp (which only ever rewrites
    bytes STRICTLY INSIDE this region -- see _stamp_verified_block) never
    shows up as body content in a verify packet or as diff/pin noise: the
    stamp is engine metadata about the body, not a body edit."""
    lines = text.splitlines(keepends=True)
    start = end = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if start is None and s.startswith(asm.DERIV_START):
            start = i
        elif start is not None and s.startswith(asm.DERIV_END):
            end = i
            break
    if start is not None and end is not None and end > start:
        return "".join(lines[:start] + lines[end + 1:])
    return text


_LAST_UPDATED_RE = re.compile(r"(?m)^last_updated:\s*(\S.*?)\s*$")


def _frontmatter_last_updated(text):
    """Extract the `last_updated:` frontmatter value from view text.
    Returns None if the field is absent. Otherwise returns
    (parsed_date_or_None, raw_value_str) -- parsed is None on a present but
    unparseable value (caller fails closed on that case)."""
    m = _LAST_UPDATED_RE.search(text or "")
    if not m:
        return None
    raw = m.group(1).strip()
    try:
        parsed = time.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return (None, raw)
    return (parsed, raw)


# --------------------------------------------------------------- P5 registration seam
def _load_registration_seam(repo):
    """Load the effective registration map for this run, ONCE. Pre-mint
    inertness: if receipts/registrations/ does not exist under `repo`, the
    store has not been minted yet -- return {} and every P5 rule downstream
    is inert. If the directory DOES exist, `registrations.load_registrations`
    runs the FULL chain check; a broken chain is a loud pre-journal refusal
    of the WHOLE run (nothing journaled, lock released by run()'s existing
    try/finally) -- never a silent partial map."""
    if not os.path.isdir(regs.registrations_dir(repo)):
        return {}
    return regs.load_registrations(repo)


def _registered_is_lock_class(record):
    """Mirrors is_lock_class's F6 conservative rule against a REGISTRATION
    record (not a plan event_class entry): registered lock-class treatment
    is event_class in LOCK_CLASSES, OR event_class_origin == 'judgment'."""
    if str(record.get("event_class_origin", "")).lower() == "judgment":
        return True
    return str(record.get("event_class", "")).lower() in LOCK_CLASSES


def check_plan_precedence(plan, registrations_map):
    """Adjudication 4's precedence rule, at plan-intake time, pre-journal.
    For every event named anywhere in the plan that HAS a registration:
    if the registration carries lock-class treatment (see
    `_registered_is_lock_class`) and the plan's OWN event_class entry for
    that event names a non-lock class with origin 'explicit', the plan is
    LOOSENING a registered lock-class event -- refused, naming the event,
    the registered class, and the plan class. Plan stricter-than-or-equal
    to the registration passes silently. An event absent from
    `registrations_map` (inert store, or simply never registered) is
    untouched -- today's behavior exactly. Raises ValidationError on the
    first violation found (deterministic order: items, then events, both
    as given in the plan)."""
    if not registrations_map:
        return
    for item in plan.get("items", []):
        classes = item.get("event_class") or {}
        for erel in item.get("events", []):
            reg = registrations_map.get(erel)
            if reg is None:
                continue
            if not _registered_is_lock_class(reg):
                continue    # registration itself is not lock-class: inert
            plan_entry = classes.get(erel)
            if not plan_entry:
                continue    # no plan class supplied for this event: inert
            plan_class = str(plan_entry.get("class", "")).lower()
            plan_origin = str(plan_entry.get("origin", "")).lower()
            if plan_origin == "explicit" and plan_class not in LOCK_CLASSES:
                raise ValidationError(
                    "plan-precedence: event %r loosens a registered "
                    "lock-class treatment (registered event_class=%r, "
                    "event_class_origin=%r) with plan event_class=%r "
                    "origin=explicit -- refused pre-journal"
                    % (erel, reg.get("event_class"),
                       reg.get("event_class_origin"), plan_class))


# --------------------------------------------------------------- claim routing (v3.0-63)
def check_claim_routing(plan):
    """Plan-scoped totality, mechanical half (backlog v3.0-63), at plan-intake
    time, pre-journal -- same refusal discipline as check_plan_precedence.

    The plan MAY carry a top-level `claim_routing` block: per raw event, the
    source's load-bearing claims and where each one goes --

      "claim_routing": {
        "raw/<file>.md": {
          "claims":   [{"id": "<plan-local slug>", "text": "<one sentence>",
                        "owner": "wiki/<view>.md"}],
          "deferred": [{"id": "...", "text": "...",
                        "targets": ["wiki/<other>.md", ...]}]}}

    Rules (ValidationError on the first violation, deterministic order):
      * every claim carries a non-empty id, text, and owner;
      * ids are unique per event across claims + deferred (exactly-one-owner
        is checked by identity, so a duplicated id would make it vacuous);
      * every owner is a view of a plan item whose events list NAMES this
        event (an owner that never receives the event cannot absorb it);
      * every deferred claim carries a non-empty `targets` list naming its
        future destination views (this is what the receipt's pending_cascade
        carries forward -- a deferral to nowhere is a claim declared away);
      * every event key resolves to an event named in some plan item.

    A claim with NO owner and NO deferral cannot be expressed in this shape
    at all (each claim lives in exactly one of the two lists) -- the refusals
    above close the remaining declare-away shapes (empty owner, empty
    targets, dangling event). Events absent from `claim_routing` -- and plans
    with no block at all -- keep today's behavior exactly: the verifier
    grades them against total event coverage (backward compatibility for
    staged runs and re-rides authored before v3.0.29). Nothing here loosens:
    routing is opt-in per event, and opting in only ADDS refusal surface and
    verifier scope, never removes any."""
    routing = plan.get("claim_routing")
    if routing is None:
        return
    if not isinstance(routing, dict):
        raise ValidationError("claim_routing must be an object keyed by "
                              "event path")
    plan_events = set()
    views_by_event = {}
    for item in plan.get("items", []):
        for erel in item.get("events", []):
            plan_events.add(erel)
            views_by_event.setdefault(erel, set()).add(item.get("view"))
    for erel in routing:
        entry = routing[erel]
        if erel not in plan_events:
            raise ValidationError(
                "claim_routing names event %r which no plan item's events "
                "list carries -- routing for an event outside this run is "
                "meaningless" % erel)
        if not isinstance(entry, dict):
            raise ValidationError(
                "claim_routing[%r] must be an object with 'claims' and/or "
                "'deferred' lists" % erel)
        seen_ids = set()
        for c in entry.get("claims") or []:
            cid = str(c.get("id") or "").strip()
            text = str(c.get("text") or "").strip()
            owner = str(c.get("owner") or "").strip()
            if not cid or not text:
                raise ValidationError(
                    "claim_routing[%r]: every claim needs a non-empty id "
                    "and text (got id=%r)" % (erel, c.get("id")))
            if cid in seen_ids:
                raise ValidationError(
                    "claim_routing[%r]: duplicate claim id %r -- ids must "
                    "be unique per event so exactly-one-owner is checkable"
                    % (erel, cid))
            seen_ids.add(cid)
            if not owner:
                raise ValidationError(
                    "claim_routing[%r]: claim %r has no owner and is not "
                    "deferred -- a claim routed to no view in the run and "
                    "absent from the deferral list is a refused plan, "
                    "full stop" % (erel, cid))
            if owner not in views_by_event.get(erel, set()):
                raise ValidationError(
                    "claim_routing[%r]: claim %r names owner %r, but no "
                    "plan item routes this event to that view -- an owner "
                    "that never receives the event cannot absorb its claim"
                    % (erel, cid, owner))
        for d in entry.get("deferred") or []:
            did = str(d.get("id") or "").strip()
            text = str(d.get("text") or "").strip()
            targets = d.get("targets") or []
            if not did or not text:
                raise ValidationError(
                    "claim_routing[%r]: every deferred claim needs a "
                    "non-empty id and text (got id=%r)" % (erel, d.get("id")))
            if did in seen_ids:
                raise ValidationError(
                    "claim_routing[%r]: id %r appears in both claims and "
                    "deferred -- a claim is owned this run or deferred, "
                    "never both" % (erel, did))
            seen_ids.add(did)
            if not isinstance(targets, list) or not [t for t in targets
                                                     if str(t).strip()]:
                raise ValidationError(
                    "claim_routing[%r]: deferred claim %r names no target "
                    "view(s) -- a deferral to nowhere is a claim declared "
                    "away; name where it will land, and the receipt's "
                    "pending_cascade carries it there" % (erel, did))


def _view_claim_scope(routing, view, events):
    """Derive one view's declared claim scope from the per-event routing
    block: {"owned": [(event, id, text)], "elsewhere": [(event, id, owner)],
    "deferred": [(event, id, targets)]} over exactly `events`. Returns None
    when routing is absent or covers none of these events (the legacy-path
    signal: callers must then change NOTHING about today's behavior)."""
    if not isinstance(routing, dict):
        return None
    owned, elsewhere, deferred = [], [], []
    covered = False
    for erel in events:
        entry = routing.get(erel)
        if not isinstance(entry, dict):
            continue
        covered = True
        for c in entry.get("claims") or []:
            if c.get("owner") == view:
                owned.append((erel, c.get("id"), c.get("text")))
            else:
                elsewhere.append((erel, c.get("id"), c.get("owner")))
        for d in entry.get("deferred") or []:
            deferred.append((erel, d.get("id"), d.get("targets") or []))
    if not covered:
        return None
    return {"owned": owned, "elsewhere": elsewhere, "deferred": deferred}


def _render_claim_routing_section(scope, view):
    """Render the DECLARED CLAIM ROUTING packet section for one view's scope
    (from _view_claim_scope, never None here). Additive-only: this section
    is appended after the packet's legacy sections and appears ONLY when the
    plan declared routing for the view's events (v3.0-63)."""
    lines = ["## DECLARED CLAIM ROUTING (plan-scoped, v3.0-63)"]
    lines.append(
        "The compile plan deliberately split the routed events' load-bearing "
        "claims across the run's views. This view is graded against the "
        "scope declared below -- a claim listed as owned by a SIBLING view "
        "or as deferred is declared scope, NOT an omission from this view. "
        "A load-bearing claim of the events missing from this table "
        "entirely IS a defect: reject with reason class "
        "'enumeration-incomplete', naming the claim.")
    lines.append("")
    lines.append("Claims THIS VIEW OWNS (each must be represented or "
                 "implied in the view; a missing one is a rejection):")
    if scope["owned"]:
        for erel, cid, text in scope["owned"]:
            lines.append("- [%s / %s] %s" % (erel, cid, text))
    else:
        lines.append("- (none -- this view owns no claim of these events "
                     "this run)")
    lines.append("")
    lines.append("Claims routed to SIBLING views this run (not this view's "
                 "scope):")
    if scope["elsewhere"]:
        for erel, cid, owner in scope["elsewhere"]:
            lines.append("- [%s / %s] -> %s" % (erel, cid, owner))
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("Claims DEFERRED to a later run (named in the run "
                 "receipt's pending_cascade):")
    if scope["deferred"]:
        for erel, cid, targets in scope["deferred"]:
            lines.append("- [%s / %s] -> %s" % (erel, cid,
                                                ", ".join(targets)))
    else:
        lines.append("- (none)")
    return "\n".join(lines)


# --------------------------------------------- verifier demotion (2026-08-09 design)
# Reason-class vocabulary, CLOSED. The boundary principle (operator-ratified):
# RECORDED = absence-shaped ("the article may say too little" -- missing
# content can be added later without unwriting anything); BLOCKING =
# falsity-shaped ("the article may say something false" -- poisons the wiki,
# which is future context). over-certainty is blocking ON PURPOSE: an
# overstated claim reads as false confidence to every future session.
# Anything outside the vocabulary, absent, or malformed is `unclassified`
# and BLOCKS (fail-closed). The class is the VERIFIER's to assign (inside
# its verdict); this engine normalizes it exactly once, at record time, and
# journals both the classes and the resulting disposition on the leg entry
# -- after that moment no reader may re-derive either from the verdict
# artifact (the v3.0-74 lesson made a rule: the journal is the engine's
# record; the artifact is forensics).
REASON_CLASSES_RECORDED = ("scope-omission", "enumeration-incomplete")
REASON_CLASSES_BLOCKING = ("fabrication", "contradiction", "over-certainty")

_REASON_CLASS_SECTION = """## REASON CLASS (verifier demotion, 2026-08-09)
On a NON-CONFIRM verdict, include a `reason_classes` field (JSON list) naming
every defect found, from exactly this vocabulary:
- `scope-omission` -- a claim this view owns (or, absent a declared routing,
  a load-bearing claim of the events) is absent from the view
- `enumeration-incomplete` -- a load-bearing claim of the events is missing
  from the declared claim routing altogether
- `fabrication` -- the view asserts content the events do not support
  (including any diff change unaccounted for by the events)
- `contradiction` -- the view contradicts the events or retains stale
  content the events supersede
- `over-certainty` -- the view states a claim with materially more
  confidence than the events carry
Also name the token(s) in your reason sentence. A verdict without a
recognizable class is treated as blocking."""

_COMPLETED_VERDICTS = ("confirmed", "revised", "rejected")


def classify_reason_classes(verdict):
    """Normalize a non-confirm verdict's reason class at RECORD TIME.
    Returns (verdict_label, reason_classes, disposition):
      * verdict_label -- the completed verdict value ('revised'/'rejected';
        'confirmed' never reaches here -- stamp refusals are classed at the
        call site), or the raw non-completed value (transport classes keep
        their string so the ledger can name them);
      * reason_classes -- normalized closed-vocabulary list, or
        ['unclassified'];
      * disposition -- 'recorded' iff every named class is in the recorded
        vocabulary, else 'blocking' (mixed verdicts: strictest wins).
    Resolution order (fail-closed at every step): the structured
    `reason_classes` list if present and EVERY member is recognized (one
    unrecognized member poisons the whole list -- no cherry-picking a
    parseable subset); else an exact-token scan of the reason sentence
    (the v3.0.29 'reason class: enumeration-incomplete' prose convention,
    generalized); else unclassified. On a substrate-gated outer verdict the
    label AND the classes are read from the same object the usable inner
    verdict came from (mirrors compile-driver's classify_verdict)."""
    known = set(REASON_CLASSES_RECORDED) | set(REASON_CLASSES_BLOCKING)
    label, src = None, None
    if isinstance(verdict, dict):
        v = str(verdict.get("verdict") or "").strip().lower()
        if v in _COMPLETED_VERDICTS:
            label, src = v, verdict
        elif v == "substrate-gated":
            inner = verdict.get("bridge_verdict")
            if isinstance(inner, dict):
                iv = str(inner.get("verdict") or "").strip().lower()
                if iv in _COMPLETED_VERDICTS:
                    label, src = iv, inner
            else:
                iv = str(verdict.get("gated_inner_verdict")
                         or "").strip().lower()
                if iv in _COMPLETED_VERDICTS:
                    label, src = iv, verdict
        if label is None:
            label = v or "no-verdict-field"
    else:
        label = "no-verdict-artifact"
    if src is None:
        # not a completed verdict: transport-shaped. The driver's
        # completeness classification (artifact-based, untouched) governs
        # what happens to the RUN; these journaled fields exist so the
        # ledger can still name the leg.
        return label, ["unclassified"], "blocking"

    classes = None
    raw = src.get("reason_classes")
    if isinstance(raw, list) and raw:
        norm = [str(c).strip().lower() for c in raw]
        classes = norm if all(c in known for c in norm) else ["unclassified"]
    if classes is None:
        reason = str(src.get("reason") or "")
        found = [t for t in REASON_CLASSES_RECORDED + REASON_CLASSES_BLOCKING
                 if t in reason]
        classes = found or ["unclassified"]
    recorded = set(REASON_CLASSES_RECORDED)
    disposition = ("recorded"
                   if all(c in recorded for c in classes) else "blocking")
    return label, classes, disposition


# --------------------------------------------------------------- P5 pointer-class ceiling
# Conservative, line-level detectors (spec sec.10 / adjudication 3): a
# changed line must POSITIVELY match one of these to be allowed on a
# pointer-class event; anything ambiguous is refused (fail-closed).
_LINK_LINE_RE = re.compile(
    r"\[[^\]\n]*\]\([^)\n]+\)"          # [text](target) inline link
    r"|^\s*\[[^\]\n]+\]:\s*\S+"         # [ref]: target reference-style link
)
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")   # a Markdown table row: | ... |
_ATTRIBUTION_RE = re.compile(r"reported by receipt\s+\S+")


def _pointer_class_changed_lines(repo, old_text, new_text):
    """Every changed (added or removed) non-blank line between old_text and
    new_text, as (sign, line_text_without_marker) pairs, via a real git
    diff (blob-vs-blob, same mechanism changed_sections already uses).
    Context/unchanged lines are excluded."""
    pre_blob = _blob_of_text(repo, old_text)
    post_blob = _blob_of_text(repo, new_text)
    out = _git(repo, "diff", "--no-color", pre_blob, post_blob)
    changed = []
    for ln in out.splitlines():
        if ln.startswith("+++") or ln.startswith("---") or ln.startswith("@@"):
            continue
        if ln.startswith("+") or ln.startswith("-"):
            content = ln[1:]
            if content.strip():
                changed.append((ln[0], content))
    return changed


def _is_allowed_pointer_line(line_text):
    """A changed line is allowed under the pointer-class ceiling iff it
    POSITIVELY matches a link line or a status-table row. Ambiguous/other
    lines are NOT allowed (fail-closed per adjudication 3)."""
    if _TABLE_ROW_RE.match(line_text):
        return True
    if _LINK_LINE_RE.search(line_text):
        return True
    return False


def check_pointer_class_ceiling(repo, view_rel, old_text, new_text, events,
                                registrations_map):
    """Adjudication 3 / spec sec.10, pre-journal, same refusal discipline as
    validate_absorb_output's other checks. Applies ONLY when at least one of
    `events` (this absorb answer's delta events) has a registration with
    asserts_corpus_state=true -- non-pointer events are zero behavior change
    (this function is a no-op: returns immediately). When it DOES apply:
      - every changed line (added or removed) between old_text and new_text
        must be a link line or a status-table row (see
        `_is_allowed_pointer_line`); the first disallowed line found is
        refused, naming the offending line and the ceiling rule (fail-closed
        on ambiguity, never permissive).
      - every ADDED status-table row must carry the literal attribution
        form "reported by receipt <event>" (see `_ATTRIBUTION_RE`) --
        missing it is refused, naming the row.
    `registrations_map` is the pre-mint-inert seam: an empty map makes this
    whole check inert (no events can have a pointer-class registration)."""
    if not registrations_map:
        return
    pointer_events = [e for e in events
                     if registrations_map.get(e, {}).get(
                         "asserts_corpus_state") is True]
    if not pointer_events:
        return
    changed = _pointer_class_changed_lines(repo, old_text, new_text)
    for sign, content in changed:
        if not _is_allowed_pointer_line(content):
            raise ValidationError(
                "pointer-class write ceiling: view %r changed by pointer-"
                "class event(s) %s may only change link lines and status-"
                "table rows; disallowed line: %r"
                % (view_rel, pointer_events, content[:120]))
        if (sign == "+" and _TABLE_ROW_RE.match(content)
                and not _ATTRIBUTION_RE.search(content)):
            raise ValidationError(
                "pointer-class write ceiling: view %r has an ADDED status-"
                "table row from pointer-class event(s) %s missing the "
                "sec.10 attribution form ('reported by receipt <event>'): "
                "%r" % (view_rel, pointer_events, content[:120]))


# --------------------------------------------------------------- output validation
RETIREMENTS_START = "# --- retirements"
RETIREMENTS_END = "# --- /retirements"


def retirements_block(text):
    """The engine-owned redirect block inside the derivation region (ADR #11 Release 2,
    v3.0.50, deploy/retire.py): the lines from `# --- retirements` to `# --- /retirements`
    inclusive, or None. An absorb may NEVER change a byte of it (brief R2-C1/R3-C1: no
    later ordinary absorb can drop or edit a redirect map) -- validate_absorb_output
    refuses. Only the retirement verb writes it, in its own prepared commit."""
    lines = text.replace("\r\n", "\n").split("\n")
    s = e = None
    for i, ln in enumerate(lines):
        st = ln.strip()
        if s is None and st == RETIREMENTS_START:
            s = i
        elif s is not None and st == RETIREMENTS_END:
            e = i
            break
    if s is None:
        return None
    if e is None:
        return "\n".join(lines[s:])  # unterminated: still owned, still compared
    return "\n".join(lines[s:e + 1])


_split_mod = None


def _split():
    """check-split.py, loaded lazily (v3.0-141): the citation grammar's single home --
    the same CITE_FIND_RE/_parse_citation the manifest and the verb use. v3.0.52
    (v3.0-150): the sibling's interface version is pinned -- a two-lane MIGRATION copy
    can transiently pair this file with an OLDER check-split.py, and that state must
    refuse NAMED (the first fork's v3.0.51 adoption hit the bare-AttributeError version of
    this in retire.py, mid-ceremony)."""
    global _split_mod
    if _split_mod is None:
        _split_mod = _load("check-split.py", "check_split_v2")
    if getattr(_split_mod, "SPLIT_IFACE", 0) < 2:
        raise ValidationError(
            "deploy/check-split.py is OLDER than this engine (interface %s < 2: the "
            "v3.0.51+ single-home citation symbols are missing) -- complete the "
            "SESSION-lane copy first (retire-manifest.py, compile-v2.py, check-split.py, "
            "assemble.py from the same tag), then re-run (v3.0-150)"
            % getattr(_split_mod, "SPLIT_IFACE", "none"))
    return _split_mod


def strip_retirements_block(text):
    """The view text without its engine-owned retirements block (markers inclusive).
    Byte-parity with retire.strip_block (pinned in the battery): stripped only when BOTH
    markers are present; an unterminated block stays in the text."""
    lines = text.replace("\r\n", "\n").split("\n")
    s = e = None
    for i, ln in enumerate(lines):
        st = ln.strip()
        if s is None and st == RETIREMENTS_START:
            s = i
        elif s is not None and st == RETIREMENTS_END:
            e = i
            break
    if s is not None and e is not None:
        lines = lines[:s] + lines[e + 1:]
    return "\n".join(lines)


def _gen_hash(text):
    """The GENERATION identity of a view (retire.gen_hash parity, battery-pinned):
    sha256 of the LF text with the retirements block stripped."""
    return hashlib.sha256(strip_retirements_block(text).encode("utf-8")).hexdigest()


_rm_mod = None


def _rm():
    """retire-manifest.py, loaded lazily (v3.0.52, ADR #11 Release 3): splice_sections'
    one home -- the span grammar the retirement gate uses is the grammar the engine
    splices with (the v3.0-132 parity discipline)."""
    global _rm_mod
    if _rm_mod is None:
        _rm_mod = _load("retire-manifest.py", "retire_manifest_v2")
    return _rm_mod


_debt_mod = None


def _debt():
    """debt.py, loaded lazily (v3.0.52, ADR #11 Release 3): lineage-stable cap episodes
    and the absorb brake, computed from git objects on every question."""
    global _debt_mod
    if _debt_mod is None:
        _debt_mod = _load("debt.py", "debt_v2")
    return _debt_mod


def apply_section_scoped(old_text, out):
    """v3.0.52 (ADR #11 Release 3): SECTION-SCOPED ABSORB -- an author may return
    {"sections": {title: replacement}} instead of new_text and the ENGINE splices
    (retire-manifest.splice_sections), so the model rewrites only what changed and the
    untouched sections cannot drift. The spliced result then walks the exact validation
    every full-text absorb walks (minting, floors, manifest-vs-diff, the brake). A
    refusal from the splice contract is a ValidationError like any other pre-journal
    refusal. Output carrying BOTH new_text and sections refuses (two authors of one
    view state)."""
    if not out.get("sections"):
        return out
    if out.get("new_text") is not None:
        raise ValidationError("absorb output carries BOTH new_text and sections -- one "
                              "authoring shape per view")
    try:
        return dict(out, new_text=_rm().splice_sections(old_text, out["sections"]))
    except ValueError as e:
        raise ValidationError("section-scoped absorb: %s" % e)


# A bare colon-form view citation: `view.md:80` NOT already carrying a generation tag.
# `(?![0-9@])` blocks the backtrack that would read `view.md:80@...`'s `8` as line 8,
# and leaves any malformed short tag alone rather than double-tagging it.
_BARE_COLON_CITE_RE = re.compile(r"([A-Za-z0-9._-]+\.md):(\d+)(?![0-9@])")


def _wiki_view_index(repo):
    """{basename: [repo-relative paths]} over wiki/**/*.md, cold tier excluded -- the
    same universe shape the manifest and the verb use for citation resolution."""
    idx = {}
    for dp, _dns, fns in os.walk(os.path.join(repo, "wiki")):
        rel_dp = os.path.relpath(dp, repo).replace("\\", "/")
        if rel_dp == "wiki/cold" or rel_dp.startswith("wiki/cold/"):
            continue
        for fn in fns:
            if fn.endswith(".md"):
                idx.setdefault(fn, []).append(rel_dp + "/" + fn)
    return idx


def mint_citation_tags(repo, view_rel, old_text, new_text):
    """v3.0.51 (v3.0-141, brief v4 [R3-C1]): the ENGINE mints the generation tag on every
    NEW colon-form citation of a wiki view -- `view.md:80` becomes `view.md:80@<gen8>`,
    gen8 = the first 8 hex of the cited view's CURRENT generation hash (its on-disk text
    at absorb time, retirements block excluded), so the citation binds the generation it
    was made against and can never become the bare post-Release-2 citation that blocks a
    later retirement. Only citations on lines NEW relative to old_text are minted --
    pre-existing lines are the frozen-registry legacy population and are never rewritten.
    Self-citations are exempt (they move with the view; the stub/redirect heals them at
    retirement). A basename resolving to zero wiki views is left alone (not a view
    citation); an ambiguous one is left for the validator to refuse. Returns the text.
    "Pre-existing" is counted by OCCURRENCE, not set membership (cross-vendor round-1
    catch: a NEW line whose text duplicates an old line would otherwise ride as legacy
    and become exactly the bare citation this mint exists to prevent)."""
    old_budget = {}
    for oln in old_text.splitlines():
        old_budget[oln] = old_budget.get(oln, 0) + 1
    idx = _wiki_view_index(repo)
    own = os.path.basename(view_rel)

    def _tag(m):
        base = m.group(1)
        if base == own:
            return m.group(0)
        homes = idx.get(base) or []
        if len(homes) != 1:
            return m.group(0)
        vp = os.path.join(repo, homes[0].replace("/", os.sep))
        try:
            vtext = open(vp, encoding="utf-8-sig").read()
        except OSError:
            return m.group(0)
        return "%s:%s@%s" % (base, m.group(2), _gen_hash(vtext)[:8])

    out_lines = []
    for ln in new_text.split("\n"):
        if old_budget.get(ln, 0) > 0:
            old_budget[ln] -= 1
            out_lines.append(ln)
        elif ".md:" not in ln:
            out_lines.append(ln)
        else:
            out_lines.append(_BARE_COLON_CITE_RE.sub(_tag, ln))
    return "\n".join(out_lines)


def check_new_citations_tagged(repo, view_rel, old_text, new_text):
    """v3.0.51 (v3.0-141): refusals the mint cannot repair. Raises ValidationError on
    (a) a new PROSE-form citation of a wiki view ("view.md lines 80" -- the legacy
    grammar has no tagged spelling; write the colon form and the engine mints the tag),
    (b) a new colon-form citation whose basename resolves to MORE than one wiki view
    (nothing can mint an ambiguous citation), (c) a new bare colon-form citation that
    reached validation unminted (a write path that bypassed mint_citation_tags).
    Pre-existing lines are the frozen-registry legacy population and pass untouched --
    counted by OCCURRENCE, not set membership (a NEW duplicate of an old line is new);
    self-citations are exempt; a basename matching no wiki view is not a view citation.
    The prose-form window is the FULL-TEXT 200-character window after the basename,
    newlines included -- the exact association check-split's citation grammar (and so
    the retirement gate's citation universe) uses (cross-vendor round-2 catch: a
    per-line window missed a basename whose 'lines N' continuation sits on the next
    line). Stated residual: a citation formed by adding a 'lines N' continuation after
    a basename on an UNCHANGED old line is not refused here (the anchor line is legacy);
    it lands in the safe direction -- the retirement gate refuses it as unregistered."""
    idx = _wiki_view_index(repo)
    own = os.path.basename(view_rel)
    old_budget = {}
    for oln in old_text.splitlines():
        old_budget[oln] = old_budget.get(oln, 0) + 1
    sp = _split()
    new_lines = new_text.split("\n")
    is_new = []
    for ln in new_lines:
        if old_budget.get(ln, 0) > 0:
            old_budget[ln] -= 1
            is_new.append(False)
        else:
            is_new.append(True)
    for ln_no, ln in enumerate(new_lines, 1):
        if not is_new[ln_no - 1] or ".md:" not in ln:
            continue
        # colon-form citations are single tokens -- never cross-line
        for m in _BARE_COLON_CITE_RE.finditer(ln):
            base = m.group(1)
            if base == own or base not in idx:
                continue
            if len(idx[base]) > 1:
                raise ValidationError(
                    "new citation %s:%s on line %d is AMBIGUOUS (%d wiki views share the "
                    "basename) -- no generation tag can be minted; cite an unambiguous "
                    "view (v3.0-141)" % (base, m.group(2), ln_no, len(idx[base])))
            raise ValidationError(
                "new bare citation %s:%s on line %d carries no generation tag -- post-"
                "Release-2 citations are minted `%s:%s@<gen8>` (v3.0-141, brief [R3-C1]);"
                " this write path bypassed mint_citation_tags"
                % (base, m.group(2), ln_no, base, m.group(2)))
    # prose-form: full-text windows (check-split parity, newlines included), anchored at
    # basenames sitting on NEW lines
    for bm in re.finditer(r"[A-Za-z0-9._-]+\.md", new_text):
        base = bm.group(0)
        if base == own or base not in idx:
            continue
        ln_no = new_text.count("\n", 0, bm.start()) + 1
        if not is_new[ln_no - 1]:
            continue
        window = new_text[bm.end():bm.end() + 200]
        for cm in sp.CITE_FIND_RE.finditer(window):
            d, r = sp._parse_citation(cm.group(1))
            if d or r:
                raise ValidationError(
                    "new PROSE-form citation of %s on line %d ('lines %s') cannot "
                    "carry a generation tag -- write the tagged colon form "
                    "`%s:<line>@<gen8>` (the engine mints the tag) (v3.0-141)"
                    % (base, ln_no, cm.group(1), base))


def validate_absorb_output(repo, view_rel, old_text, out, events,
                           registrations_map=None):
    """The orchestrator validates BEFORE anything is journaled (spec sec.7:
    'parses, bounds, manifest matches the real diff, corpus_support lines
    actually appear in the cited artifact'). `registrations_map` (P5,
    optional, defaults to None/treated as {}) gates the pointer-class write
    ceiling -- see check_pointer_class_ceiling; absent/empty leaves this
    function's behavior exactly as it was pre-P5."""
    if out.get("new_text") is None:
        for m in out.get("manifest") or []:
            raise ValidationError("no-op output carries a merge manifest")
        return None
    new_text = out["new_text"]
    if not isinstance(new_text, str) or not new_text.strip():
        raise ValidationError("new_text empty/not a string")
    if len(new_text.encode("utf-8")) > VIEW_BYTE_CAP:
        raise ValidationError("new_text exceeds byte cap (%d)" % VIEW_BYTE_CAP)
    if new_text == old_text:
        raise ValidationError("new_text identical to old (should be a no-op)")
    # ADR #11 Release 2 (v3.0.50): the retirements block is engine-owned and immutable
    # to absorb -- dropped, edited, moved or newly minted by an absorb, all refused.
    old_block, new_block = retirements_block(old_text), retirements_block(new_text)
    if old_block != new_block:
        raise ValidationError("retirements block is engine-owned: an absorb may not %s it "
                              "(only deploy/retire.py writes redirect entries)"
                              % ("drop" if new_block is None else
                                 "mint" if old_block is None else "edit"))
    # CONTENT-1 deletion floor (test-plan ~230): headings preserved, <=30% shrink
    # line-set membership, NOT substring containment: "## X" is a substring
    # of "### X", so a heading DEMOTION would slip a containment check
    # (red-team catch, 2026-07-05)
    old_heads = [ln.strip() for ln in old_text.splitlines()
                 if ln.lstrip().startswith("#")]
    new_head_lines = {ln.strip() for ln in new_text.splitlines()
                      if ln.lstrip().startswith("#")}
    missing_heads = [h for h in old_heads if h not in new_head_lines]
    if missing_heads:
        raise ValidationError("deletion floor: heading(s) dropped: %s"
                              % missing_heads[:3])
    if old_text and len(new_text) < 0.7 * len(old_text):
        raise ValidationError("deletion floor: view shrank >30%% (%d -> %d)"
                              % (len(old_text), len(new_text)))
    # last_updated must never backdate: new value = max(existing, event date).
    # Missing on either side = no check (field is non-validated otherwise);
    # a present-but-unparseable value fails closed.
    old_lu = _frontmatter_last_updated(old_text)
    new_lu = _frontmatter_last_updated(new_text)
    if old_lu is not None and new_lu is not None:
        old_lu_val, old_lu_raw = old_lu
        new_lu_val, new_lu_raw = new_lu
        if old_lu_val is None:
            raise ValidationError(
                "last_updated: existing view value unparseable: %r"
                % old_lu_raw)
        if new_lu_val is None:
            raise ValidationError(
                "last_updated: new value unparseable: %r" % new_lu_raw)
        if new_lu_val < old_lu_val:
            raise ValidationError(
                "last_updated backdated: existing=%s new=%s (new_text must "
                "not set last_updated earlier than the existing view's "
                "value)" % (old_lu_raw, new_lu_raw))
    # manifest vs REAL diff, section granularity, both directions
    pre_blob = _blob_of_text(repo, old_text)
    post_blob = _blob_of_text(repo, new_text)
    actual = changed_sections(repo, pre_blob, post_blob)
    claimed = {(m.get("section") or "").strip() for m in out.get("manifest") or []}
    if claimed - actual:
        raise ValidationError("manifest claims unchanged section(s): %s"
                              % sorted(claimed - actual))
    if actual - claimed:
        raise ValidationError("diff touches unclaimed section(s): %s"
                              % sorted(actual - claimed))
    for me in out.get("manifest") or []:
        if me.get("event") not in events:
            raise ValidationError("manifest cites non-delta event %r"
                                  % me.get("event"))
    # corpus_support (F16): every support line is an EXACT line of the cited
    # artifact, and the entry pins the artifact's content hash
    for cs in out.get("corpus_support") or []:
        art = cs.get("artifact", "")
        ap = os.path.join(repo, art.replace("/", os.sep))
        if not os.path.isfile(ap):
            raise ValidationError("corpus_support artifact missing: %s" % art)
        body = open(ap, encoding="utf-8", errors="replace").read()
        want_sha = cs.get("artifact_sha256")
        if not want_sha:
            raise ValidationError("corpus_support entry for %s lacks "
                                  "artifact_sha256 pin" % art)
        if want_sha != _sha256(body):
            raise ValidationError("corpus_support artifact_sha256 stale for %s"
                                  % art)
        art_lines = set(body.splitlines())
        for ln in cs.get("support_lines") or []:
            if ln not in art_lines:   # EXACT line, whitespace included
                raise ValidationError("support line is not an exact line of %s:"
                                      " %r" % (art, ln[:80]))
    # shipped-state prose REQUIRES corpus_support at emit time (spec sec.7 ABSORB);
    # the RECONCILE flag additionally catches prose-level cases post-hoc
    shippy = [m for m in out.get("manifest") or []
              if _SHIPPED_STATE_RE.search(str(m.get("section", "")))]
    if shippy and not out.get("corpus_support"):
        raise ValidationError("shipped-state section(s) %s changed with no "
                              "corpus_support entry"
                              % sorted({m["section"] for m in shippy}))
    # v3.0.51 (v3.0-141): every NEW citation of a wiki view must be generation-tagged
    # (the orchestrator mints colon-form citations via mint_citation_tags BEFORE this
    # validator runs; what refuses here is what minting cannot repair).
    check_new_citations_tagged(repo, view_rel, old_text, new_text)
    # P5 pointer-class write ceiling (adjudication 3 / spec sec.10): applied
    # LAST, after every pre-existing check has already passed, so it is
    # purely additive on top of them (never relaxes an existing check, and
    # never masks an existing refusal with a different one).
    check_pointer_class_ceiling(repo, view_rel, old_text, new_text, events,
                                registrations_map or {})
    # ADR #11 Release 3 (v3.0.52, condition 7): the cap-debt BRAKE -- during an open or
    # escalated cap episode an ordinary absorb may not increase the view's LF-normalized
    # bytes. deploy/debt.py recomputes episodes from git objects on every question
    # (lineage-stable: rename, split, recreation all carry debt), and its refusal names
    # the ADR's outs (net-zero, retire.py --splice pairing, a committed operator
    # exception). Applied LAST, like the pointer ceiling: purely additive on top of
    # every pre-existing check. Degrades stated: a deploy/ without debt.py is
    # pre-Release-3; an unresolvable branch means no history and therefore no debt; an
    # unreadable cap table degrades exactly as check-caps does (INCONCLUSIVE, the sweep's
    # cap sensor reports it). Every OTHER failure inside the computation refuses --
    # fail-closed, because an unanswerable brake question is not permission.
    old_lf = len(old_text.replace("\r\n", "\n").encode("utf-8"))
    new_lf = len(new_text.replace("\r\n", "\n").encode("utf-8"))
    if new_lf > old_lf and os.path.isdir(os.path.join(repo, ".git")) \
            and os.path.isfile(os.path.join(_HERE, "debt.py")):
        try:
            bv = _debt().brake(repo, view_rel, old_lf, new_lf)
        except Exception as e:
            if "does not resolve" in str(e) or "unresolvable" in str(e) \
                    or "cap config" in str(e) or "PyYAML" in str(e):
                bv = {"allowed": True}
            else:
                raise ValidationError("cap-debt brake could not answer for %s: %s "
                                      "(fail-closed)" % (view_rel, e))
        if not bv["allowed"]:
            raise ValidationError("brake (ADR #11 condition 7): %s" % bv["reason"])
    return pre_blob, post_blob


def is_lock_class(event_class_entry):
    """F6 conservative default: judgment-assigned -> lock-class regardless."""
    if not event_class_entry:
        return True     # unknown class = conservative
    if str(event_class_entry.get("origin", "")).lower() == "judgment":
        return True
    return str(event_class_entry.get("class", "")).lower() in LOCK_CLASSES


# --------------------------------------------------------------- reconcile flags
def reconcile_flags(absorbed_rows, entity_index=None, repo=None):
    """Scripts flag, backends judge only flags -- the FULL spec sec.7 flag set:
    (a) entity-overlapping view pairs that BOTH changed this run;
    (b) roadmap rows whose cited sources changed (roadmap/flight-plan views that
        reference a raw absorbed this run);
    (c) supersession chains (a changed view or absorbed event that carries
        supersession markers);
    (d) pathological hub entities (an entity fanning out over >= HUB_FANOUT
        changed views this run);
    (e) any changed view whose diff touched shipped-state prose without a
        matching corpus_support entry."""
    HUB_FANOUT = 4
    flags = []
    ent = entity_index or {}
    changed = [r for r in absorbed_rows if r.get("post_blob")]
    changed_views = {r["view"] for r in changed}
    absorbed_events = sorted({e for r in changed for e in r.get("events", [])})
    # (a) entity pairs
    for i in range(len(changed)):
        for j in range(i + 1, len(changed)):
            a, b = changed[i], changed[j]
            shared = set(ent.get(a["view"], [])) & set(ent.get(b["view"], []))
            if shared:
                flags.append({"kind": "entity-pair", "views": [a["view"],
                              b["view"]], "entities": sorted(shared)})
    # (b) roadmap rows citing this run's events
    if repo:
        roots = ["wiki/flight-plans", "wiki/roadmap", "roadmap"]
        for rootrel in roots:
            rd = os.path.join(repo, rootrel.replace("/", os.sep))
            if not os.path.isdir(rd):
                continue
            for f in sorted(os.listdir(rd)):
                if not f.endswith(".md"):
                    continue
                vrel = rootrel + "/" + f
                body = open(os.path.join(rd, f), encoding="utf-8",
                            errors="replace").read()
                cited = [e for e in absorbed_events if e in body]
                if cited and vrel not in changed_views:
                    flags.append({"kind": "roadmap-cited-source-changed",
                                  "view": vrel, "events": cited})
    # (c) supersession chains
    if repo:
        for e in absorbed_events:
            ep = os.path.join(repo, e.replace("/", os.sep))
            if os.path.isfile(ep):
                head = open(ep, encoding="utf-8", errors="replace").read(4000)
                if re.search(r"supersede[sd]?|superseded_by", head, re.I):
                    flags.append({"kind": "supersession-chain", "event": e})
    # (d) pathological hub entities
    fanout = {}
    for v in changed_views:
        for en in ent.get(v, []):
            fanout.setdefault(en, set()).add(v)
    for en, vs in sorted(fanout.items()):
        if len(vs) >= HUB_FANOUT:
            flags.append({"kind": "hub-entity", "entity": en,
                          "views": sorted(vs)})
    # (e) shipped-state prose without corpus_support
    for r in changed:
        shippy = [m for m in r.get("manifest", [])
                  if _SHIPPED_STATE_RE.search(str(m.get("section", "")))]
        if shippy and not r.get("corpus_support"):
            flags.append({"kind": "shipped-state-no-support", "view": r["view"],
                          "sections": sorted({m["section"] for m in shippy})})
    return flags


# ------------------------------------------- derivation minting (v3.0-69)
# Regenerated projections are rebuilt wholesale by /compile, so a region there
# would be destroyed on the next regeneration -- same exclusion backfill uses.
_PROJECTION_BASENAMES = {"INDEX.md", "HEALTH.md", "REVIEW.md"}


def _mint_derivation_region(repo, view_rel, text):
    """Mint the engine-managed derivation region for a view this run CREATED
    (backlog v3.0-69). Returns the text with the region inserted, or the text
    UNCHANGED when it cannot be minted honestly.

    WHY THIS EXISTS. `_stamp_verified_block` writes `verified:` strictly
    inside DERIV_START..DERIV_END, so a view with no region can never record
    a verification -- and the absorb path never created one (it only strips,
    or writes into, an existing region; every region-writing site in this
    file was a self-test fixture hand-authoring a view that already had one,
    which is why the batteries never caught it). Reproduced 2026-08-06: a new
    view whose author-supplied text carried no region drew a `confirmed`
    verdict from the verify backend and the engine recorded ZERO
    confirmations -- a paid cross-vendor approval produced and then discarded.
    backfill-derivation.py closed this for LEGACY hand-era corpora; a project
    born on the engine had no minter at all.

    SINGLE-HOMED SHAPE: the region text, its conservative defaults, and the
    origin_max computation are backfill-derivation.py's `render_region` /
    `view_kind` / `compute_origin_max` -- called, never re-implemented, so
    the two minters can never drift. `consumed_status` is therefore
    `legacy-assumed`, deliberately: of the three legal values it is the only
    one that closes this defect without side effects. `audit-pending` would
    trip check-derivation's F12 gate on every engine-born view forever (a
    permanent doctor FAIL), and `verified-consumed` would assert a
    verification that has not happened yet -- the verify leg runs AFTER this
    write. The honest residue (an engine-born view labelled "legacy") is
    filed as v3.0-71: nothing today ever TRANSITIONS consumed_status, so
    fixing the label properly is new machinery, not a value change here.

    ORDERING: called from run() AFTER validate_absorb_output has passed, so
    the manifest-vs-diff contract is graded against exactly what the author
    wrote -- the engine's region is never a section the author must claim.
    The caller recomputes post_blob over the minted text so the journal pins
    what actually lands on disk.

    UNMINTABLE CASES (text returned unchanged, no new refusal): a regenerated
    projection basename, and a view whose leading frontmatter block does not
    parse -- the latter is already a check-frontmatter finding and papering
    over it with a guessed region would be worse than leaving it visible."""
    if os.path.basename(view_rel) in _PROJECTION_BASENAMES:
        return text
    try:
        bfd = _load("backfill-derivation.py", "backfill_derivation_v2")
        cfm = _load("check-frontmatter.py", "check_frontmatter_v2")
    except Exception:                                       # noqa: BLE001
        return text
    parsed = bfd.parse_view(text)
    if parsed is None:
        return text
    fm_end, title, summary, sources = parsed
    try:
        omax, _counts = bfd.compute_origin_max(repo, sources)
    except Exception:                                       # noqa: BLE001
        omax = "unknown"        # most restrictive, matching backfill's rule
    region = bfd.render_region(
        cfm.SCHEMA_VERSION, bfd.view_kind(view_rel),
        # A non-empty summary keeps render_region off its own legacy-view
        # fallback string, which would be a false statement about a view the
        # engine just created.
        summary or title or "(engine-born view; summary pending)",
        omax, view_rel,
        # v3.0-71: the mint's provenance, recorded at birth -- this view was
        # born through the validated absorb path, so a later confirmed verify
        # may advance its consumed_status (engine-born population only).
        minted_by="engine")
    lines = text.splitlines()
    new_lines = lines[:fm_end + 1] + ["", region] + lines[fm_end + 1:]
    return "\n".join(new_lines) + ("\n" if text.endswith("\n") else "")


# --------------------------------------------------------------- the run
def run(repo, plan, absorb_backend, run_type="compile", break_stale=False,
        entity_index=None):
    """One compile run. Returns {"sha", "seq", "flags", "noop_candidates",
    "rebuilds"}; raises core.LockHeld / ValidationError."""
    t0 = time.strftime("%Y-%m-%dT%H:%M:%S")
    _lp, broken = core.acquire_lock(repo, run_type, break_stale=break_stale)
    try:
        # P5 registration consultation seam (adjudication 4): loaded ONCE per
        # run, pre-mint inert (see _load_registration_seam's docstring). A
        # broken registration chain raises loudly here, before anything is
        # journaled -- the existing try/finally below still releases the
        # lock on the way out.
        registrations_map = _load_registration_seam(repo)
        check_plan_precedence(plan, registrations_map)
        # v3.0-63 plan-scoped totality, mechanical half: a declared claim
        # routing is validated pre-journal (exactly-one-owner, owner in
        # plan, deferrals carry named targets). Plans without the block
        # behave exactly as before this check existed.
        check_claim_routing(plan)
        # v3.0-22 structural refusal, plan-intake time, pre-journal (same
        # discipline as check_plan_precedence above): two plan items
        # resolving to the SAME view path would make the later item absorb
        # against the view's ORIGINAL on-disk text (the earlier item's write
        # is pending under the two-phase discipline below) and the later
        # deferred write would silently clobber the earlier one. Proven
        # practice never plans this (multi-event-per-view = joint citation
        # in ONE item) -- refused structurally rather than left to luck.
        # The dedup KEY is canonicalized, never the raw spelling: the write
        # loop below resolves item["view"] as os.path.join(repo,
        # view.replace("/", os.sep)), so aliased spellings ("wiki/./a.md",
        # backslash variants, and -- on case-insensitive filesystems -- case
        # variants) all land on ONE file. The key normalizes exactly the way
        # _harvest_verdict_evidence_paths does (posixpath.normpath over
        # posix separators, the 2026-07-06 fail-closed convention: check
        # what the path RESOLVES to, not its surface spelling) plus
        # casefold() -- KEY only, the item's own spelling is what run()
        # keeps using; a case-aliased duplicate is refused even on a
        # case-SENSITIVE host (stricter is safer, costs nothing legitimate).
        # The dedup key is LEXICAL -- symlinked or platform-exotic aliases
        # (trailing dots/spaces) are outside its scope; plan views are
        # repo-relative engine-authored paths and the write side is governed
        # by path-containment, so those spellings are adversarial-plan
        # territory, not operating territory.
        seen_views = set()
        for item in plan["items"]:
            vkey = posixpath.normpath(
                item["view"].replace("\\", "/")).casefold()
            if vkey in seen_views:
                raise ValidationError(
                    "duplicate view target: %r resolves to the same view "
                    "file as an earlier plan item -- absorb multiple events "
                    "into a view via joint citation in a SINGLE item"
                    % item["view"])
            seen_views.add(vkey)
        rebuilds = 0
        absorbed = []
        noop_candidates = []
        touched_paths = []
        # v3.0-22 fix: (vp, new_text) pairs, flushed to the working tree ONLY
        # after every item below has validated -- see the write loop below
        # the item loop for why (a mid-plan ValidationError must leave NO
        # earlier item's edit on disk, not even uncommitted).
        pending_writes = []
        for item in plan["items"]:
            if rebuilds >= CIRCUIT_BREAKER_REBUILDS:
                raise ValidationError("circuit breaker: %d rebuilds"
                                      % CIRCUIT_BREAKER_REBUILDS)
            view = item["view"]
            vp = os.path.join(repo, view.replace("/", os.sep))
            view_existed = os.path.isfile(vp)
            old = open(vp, encoding="utf-8").read() if view_existed else ""
            events = {}
            for erel in item["events"]:
                ep = os.path.join(repo, erel.replace("/", os.sep))
                if not os.path.isfile(ep):
                    raise ValidationError("delta event missing: %s" % erel)
                events[erel] = open(ep, encoding="utf-8").read()
            out = absorb_backend.absorb(view, old, events)
            # ADR #11 Release 3 (v3.0.52): section-scoped output is spliced by the
            # ENGINE before anything else sees it -- from here on it is a new_text
            # absorb like any other.
            out = apply_section_scoped(old, out)
            # v3.0.51 (v3.0-141): the engine MINTS generation tags on new colon-form view
            # citations before validation -- deterministic normalization, the same class
            # as the derivation-region minter below, but pre-validation because the
            # validator refuses un-tagged new citations.
            if out.get("new_text") is not None:
                out = dict(out, new_text=mint_citation_tags(repo, view, old,
                                                            out["new_text"]))
            blobs = validate_absorb_output(repo, view, old, out, events,
                                          registrations_map=registrations_map)
            classes = item.get("event_class") or {}
            for noop in out.get("noops") or []:
                erel = noop["event"]
                lockish = is_lock_class(classes.get(erel))
                noop_candidates.append({
                    "view": view, "event": erel,
                    "verified": False,
                    "disposition": "PENDING_NOOP_CANDIDATE" if lockish
                                   else "CONSUMED",
                    "event_class": str((classes.get(erel) or {}).get("class",
                                                                    "unknown")),
                    "event_class_origin": str((classes.get(erel) or {}).get(
                        "origin", "judgment")),
                    "artifact": "",       # VERIFY fills artifact + hash + time
                    "packet_sha256": "",
                    "justification": {
                        "event_sha256": _sha256(events[erel]),
                        "view_sha256": _sha256(out.get("new_text") or old),
                        "note": noop.get("justification_note", "")},
                })
            if blobs is None:
                continue    # pure no-op item: nothing written, nothing rebuilt
            pre_blob, post_blob = blobs
            final_text = out["new_text"]
            # v3.0-69: a view this run CREATES gets its engine-managed
            # derivation region minted now -- otherwise the cross-vendor
            # confirm has nowhere to be stamped and is produced then
            # discarded. Existing region-less views stay backfill's job
            # (the migration path); this covers the born-on-the-engine case
            # that had no minter at all. Minted AFTER validation, so the
            # author's manifest contract is untouched.
            if not view_existed and asm.DERIV_START not in final_text:
                minted = _mint_derivation_region(repo, view, final_text)
                if minted != final_text:
                    final_text = minted
                    post_blob = _blob_of_text(repo, final_text)
            pending_writes.append((vp, final_text))
            rebuilds += 1
            touched_paths.append(view)
            absorbed.append({"view": view,
                             "events": sorted(events),
                             "pre_blob": pre_blob, "post_blob": post_blob,
                             "manifest": out.get("manifest") or [],
                             "corpus_support": out.get("corpus_support") or []})
        # v3.0-22 fix: every item above validated (nothing raised) -- ONLY
        # NOW do we touch the working tree. Previously this write happened
        # inline per-item, so a LATER item's ValidationError left an EARLIER
        # item's view already written to disk (uncommitted, since the whole
        # run still refuses -- the journal/commit below are never reached).
        # Same write order as before (item order); still writes nothing at
        # all for a run that ends up all-no-op (pending_writes empty).
        # Documented residual: a write-phase I/O failure (OSError mid-loop)
        # can still leave a PARTIALLY written tree -- nothing is journaled
        # or committed (the stage-only discipline holds), recovery is `git
        # checkout --` of the touched paths. Full write-atomicity (temp
        # overlay) was considered and rejected as beyond-minimal for this
        # hygiene-class defect.
        for vp, new_text in pending_writes:
            os.makedirs(os.path.dirname(vp), exist_ok=True)
            with open(vp, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(new_text)
        flags = reconcile_flags(absorbed, entity_index, repo=repo)
        # ADR #11 Release 3 (v3.0.52, brief 2.3): the warn-with-obligation pre-gate.
        # Every touched view's open cap episodes are journaled on the run record --
        # episodes are COMPUTED (deploy/debt.py), so recording them is visibility, never
        # state: the brake in validate_absorb_output is the enforcement; the census and
        # the sweep surface the obligation until retirement discharges it.
        cap_episodes = []
        if touched_paths and os.path.isfile(os.path.join(_HERE, "debt.py")):
            for tv in touched_paths:
                try:
                    for ep in _debt().episodes_for_view(repo, tv):
                        cap_episodes.append({k: ep.get(k) for k in (
                            "view", "view_id", "kind", "state", "remaining",
                            "obligation_id", "deadline")})
                except Exception:
                    pass  # visibility only; the brake already ruled on every write
        rec = core.minimal_record(run_type,
                                  _git(repo, "rev-parse", "HEAD").strip())
        if cap_episodes:
            rec["cap_episodes"] = cap_episodes
        rec["absorbed"] = absorbed
        rec["noop_candidates"] = noop_candidates
        # v3.0-63: journal the validated claim routing on the compile record
        # itself, so verify passes (and any later re-verify) read the
        # declared scope from the append-only journal -- never from the
        # throwaway staging dir. Absent block -> absent key -> legacy
        # packets, byte-identical.
        if plan.get("claim_routing"):
            rec["claim_routing"] = plan["claim_routing"]
        rec["run_window"] = {"start": t0,
                             "end": time.strftime("%Y-%m-%dT%H:%M:%S")}
        rec["reconcile_flags"] = flags
        if broken:
            rec["notes"] = {"broken_stale_lock": broken}
        seq, jpath = core.append_record(repo, rec)
        sha = core.stage_only_commit(
            repo, touched_paths
            + [os.path.relpath(jpath, repo).replace(os.sep, "/")],
            "compile-v2 run seq %d (%d view(s), %d no-op candidate(s))"
            % (seq, len(absorbed), len(noop_candidates)))
        return {"sha": sha, "seq": seq, "flags": flags,
                "noop_candidates": noop_candidates, "rebuilds": rebuilds}
    finally:
        core.release_lock(repo)


# --------------------------------------------------------------- verify stage
def _routed_views_for_event(rec, event):
    """Mechanically derive event E's routed views from the VERIFIED compile
    record: every noop_candidates[].view naming E, PLUS every absorbed[]
    entry whose events list contains E (that view's post-absorb state
    carries E's content). Sorted, deduplicated."""
    views = set()
    for nc in rec.get("noop_candidates", []):
        if nc.get("event") == event:
            views.add(nc["view"])
    for a in rec.get("absorbed", []):
        if event in (a.get("events") or []):
            views.add(a["view"])
    return sorted(views)


# ---------------------------------------------------- F15: routing census journal
def _run_routing_census(repo, events):
    """F15: compute the routing census over EVENTS (the verify pass's ledger
    slice: every event this verify_run touches) via the STANDALONE
    routing-census.py primitive -- never self-graded inside this file. Returns
    (census_input_hash, census_output_hash) -- sha256 of routing-census's own
    canonical input_manifest / output serializations, exactly as that script
    computes and would print them (spec sec.7 full-VERIFY: 'the standalone
    reproducible routing-census script with journaled input/output hashes').
    Empty ledger slice (no pending events this pass) -> both hashes empty
    string, clearly distinguishable from a real (possibly-empty) census."""
    if not events:
        return "", ""
    _im, ims, _out, osha = rcensus.compute_census(repo, events)
    return ims, osha


# ---------------------------------------------------- F16: corpus excerpt embed
def _corpus_excerpts_for_event(repo, rec, event, routed_views, context=2):
    """F16 read-side: for EVENT's routed views, collect every corpus_support
    entry (from absorbed[] entries whose events list contains EVENT) and
    mechanically resolve each cited artifact AT ITS PINNED artifact_sha256,
    reusing check-corpus-support.py's resolve_artifact (current/historical/
    UNRESOLVED, incl. the rename-heuristic-independent diff-tree fallback --
    see that script's docstring for why --name-status R-lines are never
    parsed here). Returns a list of section dicts:
      {"view", "artifact", "artifact_sha256", "resolution", "excerpt"|None,
       "support_line"}
    UNRESOLVED pins are returned with resolution="UNRESOLVED" and
    excerpt=None -- the caller renders these as an explicit UNRESOLVED
    declaration, never silently drops them (fail-honest)."""
    out = []
    for a in rec.get("absorbed", []):
        if event not in (a.get("events") or []):
            continue
        if a.get("view") not in routed_views:
            continue
        for cs in a.get("corpus_support") or []:
            artifact = cs.get("artifact", "")
            pinned = cs.get("artifact_sha256", "")
            resolution, body = ccs.resolve_artifact(repo, artifact, pinned)
            lines = (cs.get("support_lines") or [])
            if resolution == "UNRESOLVED" or body is None:
                for ln in lines or [""]:
                    out.append({"view": a["view"], "artifact": artifact,
                               "artifact_sha256": pinned,
                               "resolution": "UNRESOLVED",
                               "support_line": ln, "excerpt": None})
                continue
            body_lines = body.splitlines()
            for ln in lines:
                excerpt = ln
                if ln in body_lines:
                    idx = body_lines.index(ln)
                    lo = max(0, idx - context)
                    hi = min(len(body_lines), idx + context + 1)
                    excerpt = "\n".join(body_lines[lo:hi])
                out.append({"view": a["view"], "artifact": artifact,
                           "artifact_sha256": pinned, "resolution": resolution,
                           "support_line": ln, "excerpt": excerpt})
    return out


def _render_corpus_excerpt_section(excerpts):
    """Render F16's ADDITIVE 'CORPUS EXCERPTS' packet section: one block per
    resolved/UNRESOLVED corpus_support entry. Placed AFTER the mandated
    FULL EVENT BODY / FULL VIEW BODY sections (additive-only packet change;
    never before them, never replacing them)."""
    if not excerpts:
        return ""
    blocks = ["## CORPUS EXCERPTS (F16 mechanically-resolved, read-side)"]
    for e in excerpts:
        if e["resolution"] == "UNRESOLVED":
            blocks.append(
                "### UNRESOLVED artifact=%s artifact_sha256=%s\n"
                "support_line: %s\n"
                "UNRESOLVED: pinned artifact_sha256 could not be resolved to "
                "any candidate blob (current or historical)."
                % (e["artifact"], e["artifact_sha256"], e["support_line"]))
        else:
            blocks.append(
                "### view=%s artifact=%s artifact_sha256=%s resolution=%s\n"
                "support_line: %s\n"
                "excerpt:\n%s"
                % (e["view"], e["artifact"], e["artifact_sha256"],
                   e["resolution"], e["support_line"], e["excerpt"]))
    return "\n\n".join(blocks)


def _render_absorption_excerpt_section(excerpts):
    """Render the ABSORPTION packet's section 5 (2026-07-06 categorical-
    sections amendment): unlike the no-op union packet's F16 section (still
    OMITTED when there are no excerpts -- a different contract, LLM-6
    integration, untouched here), the amendment mandates sections 1-6
    categorically on every absorption-verify packet. This section header is
    therefore ALWAYS rendered; when there are no resolvable corpus_support
    entries for these events the body is the single explicit line below,
    never a silently-omitted section (fail-honest, matches the F16
    UNRESOLVED convention of never silently dropping an absent case)."""
    header = "## CORPUS EXCERPTS (F16 mechanically-resolved, read-side)"
    if not excerpts:
        return header + "\n\n(no corpus_support entries for these events)"
    blocks = [header]
    for e in excerpts:
        if e["resolution"] == "UNRESOLVED":
            blocks.append(
                "### UNRESOLVED artifact=%s artifact_sha256=%s\n"
                "support_line: %s\n"
                "UNRESOLVED: pinned artifact_sha256 could not be resolved to "
                "any candidate blob (current or historical)."
                % (e["artifact"], e["artifact_sha256"], e["support_line"]))
        else:
            blocks.append(
                "### view=%s artifact=%s artifact_sha256=%s resolution=%s\n"
                "support_line: %s\n"
                "excerpt:\n%s"
                % (e["view"], e["artifact"], e["artifact_sha256"],
                   e["resolution"], e["support_line"], e["excerpt"]))
    return "\n\n".join(blocks)


# ---------------------------------------------- absorption-verify (full VERIFY)
def _iter_all_journal_records(repo):
    """Yield (seq, record) for EVERY journal record, in seq order. Used only
    to derive absorption-verify trigger/diff state -- never to re-validate
    chain integrity (append_record/check_chain already own that); missing
    files are simply absent (best-effort read of an already-append-only
    store)."""
    jd = core.journal_dir(repo)
    if not os.path.isdir(jd):
        return
    names = [f for f in os.listdir(jd) if f.endswith(".json")]
    seqs = sorted(int(f[:-5]) for f in names if f[:-5].isdigit())
    for seq in seqs:
        with open(os.path.join(jd, "%d.json" % seq), encoding="utf-8") as fh:
            yield seq, json.load(fh)


def _absorption_trigger_state(repo, compile_seq):
    """Mechanically derive per-view trigger state used by the absorption-
    verify pass over `compile_seq`:

    (a) the seq of the view's LAST absorption_verified[] stamp anywhere in
        journal history (or None if never verified) and the view_sha256
        pinned there (the post-absorb body AT THAT VERIFY) -- this must
        scan the FULL journal, not just seq<=compile_seq, because a verify
        record covering an EARLIER compile record always lives at a HIGHER
        seq than the compile record it verifies (verify is a follow-up
        record); capping this scan at compile_seq would make a view's own
        prior verification invisible to a later re-check of the SAME
        compile record (idempotent re-run safety) or to a subsequent
        compile record's trigger decision.
    (b) the pre_blob of the view's FIRST-EVER absorption on or before
        compile_seq (the pre-first-absorb blob, used as the never-verified
        diff base per the amendment) and every seq (<= compile_seq) at
        which it was (re-)absorbed.

    Returns {view: {"last_verified_seq": int|None,
    "last_verified_view_sha256": str|None, "last_verified_commit": str|None,
    "last_verified_kind": "machine-verified"|"adjudicated"|
                          "baseline-reset"|None,
    "last_verified_at": str|None, "last_verified_provenance": str|None,
    "first_pre_blob": str, "first_pre_seq": int|None,
    "absorbed_seqs": [seq...] sorted}}.

    last_verified_commit (2026-07-06 recovery-fix amendment): the git commit
    sha of the verify record that produced the last_verified_seq stamp --
    recovery (_recover_verified_body) reads the pinned body via `git show
    <commit>:<view>`, NEVER by re-parsing packet files (packets are named by
    the COMPILE seq being verified, not the verify record's own seq, so a
    seq-keyed packet lookup silently mismatches; see verify_commit below).
    Older journal records written before this amendment carry no
    verify_commit field -- last_verified_commit is then None and recovery
    fails honest (CUMULATIVE DIFF UNAVAILABLE), never a silent wrong guess.

    REVERTED-RUN EXCLUSION (v3.0-67, 2026-08-06). absorbed[] entries from a
    compile record that a journaled driver revert names as reverted
    (driver_revert.status == "reverted") are SKIPPED when deriving
    first_pre_blob/absorbed_seqs: a reverted run's absorption never landed,
    so its pre-absorb blob is a ghost. The live failure this closes: a
    rejected run that CREATED a view was reverted (correctly), the journal
    kept its record (correctly, append-only), and the empty-blob "created
    from nothing" baseline then poisoned every later verify of that view --
    each update-diff read as creating the whole article from an empty file
    (Ultrapak 2026-08-05, mechanically confirmed instance-side).

    ADJUDICATED BASELINES (v3.0.29 amendment). An operator set-aside ruling
    (`compile-driver.py --set-aside`, journaled as absorption_adjudicated[])
    ALSO advances the baseline: the operator is the only party who may set a
    verdict aside, and once they have, the content they ruled on is the
    honest diff base for the next update -- otherwise a set-aside view diffs
    from birth forever. The stamp kind is carried ("adjudicated" vs
    "machine-verified") so the verify packet names what the baseline is; a
    bare rejection with no ruling advances NOTHING.

    UNION ADJUDICATIONS ARE SKIPPED (v3.0.39 / v3.0-105, the cross-check
    correction made code). A union-leg set-aside journals an
    absorption_adjudicated[] entry whose `view` is the pseudo-view string
    `union:<event>` and which carries `union_event` -- it pins no content
    (no baseline_commit / view_sha256), because a union leg absorbed
    nothing. This reader MUST skip such entries: without the skip it would
    mint and advance a state entry keyed by the `union:` string, and
    nothing enforces that the `union:` namespace is disjoint from real
    view paths. The skip keys on the `union_event` field, not on a string
    prefix -- a real view path that happened to begin with `union:` still
    gets its baseline tracked correctly.

    BASELINE-RESET RUNG (v3.0.39 / v3.0-106). An operator baseline reset
    (`compile-driver.py --baseline-reset`, journaled as baseline_reset[])
    is the ladder's third rung: it advances the baseline to the view's
    content at a NAMED out-of-engine refresh commit, kind "baseline-reset",
    competing newest-by-journal-seq with the other two rungs exactly as
    machine-verified and adjudicated compete with each other. The entry's
    provenance is carried so the packet can name it out loud."""
    reverted = set()
    for _seq, rec in _iter_all_journal_records(repo):
        dr = rec.get("driver_revert")
        if isinstance(dr, dict) and dr.get("status") == "reverted" \
                and isinstance(dr.get("reverts_seq"), int):
            reverted.add(dr["reverts_seq"])

    def _blank():
        return {"last_verified_seq": None,
                "last_verified_view_sha256": None,
                "last_verified_commit": None,
                "last_verified_kind": None,
                "last_verified_at": None,
                "last_verified_provenance": None,
                "first_pre_blob": "", "first_pre_seq": None,
                "absorbed_seqs": []}

    state = {}
    for seq, rec in _iter_all_journal_records(repo):
        if seq <= compile_seq and seq not in reverted:
            for a in rec.get("absorbed", []):
                v = a.get("view")
                if not v:
                    continue
                st = state.setdefault(v, _blank())
                if st["first_pre_seq"] is None:
                    st["first_pre_blob"] = a.get("pre_blob", "")
                    st["first_pre_seq"] = seq
                st["absorbed_seqs"].append(seq)
        for av in rec.get("absorption_verified", []):
            v = av.get("view")
            if not v:
                continue
            st = state.setdefault(v, _blank())
            if st["last_verified_seq"] is None or seq > st["last_verified_seq"]:
                st["last_verified_seq"] = seq
                st["last_verified_view_sha256"] = av.get("view_sha256")
                st["last_verified_commit"] = av.get("verify_commit")
                st["last_verified_kind"] = "machine-verified"
                st["last_verified_at"] = av.get("verified_at")
                st["last_verified_provenance"] = None
        for aj in rec.get("absorption_adjudicated", []):
            # v3.0-105 REQUIRED skip (cross-check correction): a union-leg
            # adjudication (entry carries `union_event`, view is the
            # pseudo-view `union:<event>`) pins no content and must never
            # mint or advance a state entry -- keyed on the FIELD, never on
            # a string prefix (namespace disjointness is unenforced).
            if aj.get("union_event"):
                continue
            v = aj.get("view")
            if not v:
                continue
            st = state.setdefault(v, _blank())
            if st["last_verified_seq"] is None or seq > st["last_verified_seq"]:
                st["last_verified_seq"] = seq
                st["last_verified_view_sha256"] = aj.get("view_sha256")
                st["last_verified_commit"] = aj.get("baseline_commit")
                st["last_verified_kind"] = "adjudicated"
                st["last_verified_at"] = aj.get("at")
                st["last_verified_provenance"] = None
        for br in rec.get("baseline_reset", []):
            # v3.0-106: the ladder's third rung -- an operator baseline
            # reset at a named out-of-engine refresh commit, competing
            # newest-by-journal-seq with the other two rungs.
            v = br.get("view")
            if not v:
                continue
            st = state.setdefault(v, _blank())
            if st["last_verified_seq"] is None or seq > st["last_verified_seq"]:
                st["last_verified_seq"] = seq
                st["last_verified_view_sha256"] = br.get("view_sha256")
                st["last_verified_commit"] = br.get("refresh_commit")
                st["last_verified_kind"] = "baseline-reset"
                st["last_verified_at"] = br.get("at")
                st["last_verified_provenance"] = br.get("provenance")
    for st in state.values():
        st["absorbed_seqs"].sort()
    return state


def _triggered_absorption_views(repo, compile_seq, rec):
    """Views to absorption-verify THIS pass: every view named in `rec`'s own
    absorbed[] (the compile record verify_run is covering) whose last-
    absorbed seq (compile_seq itself, since it's IN absorbed[] here) is
    greater than its last-verified seq, or that has never been verified.
    Sorted, deduplicated -- this pass's absorbed[] is always seq==compile_seq
    so the condition reduces to: trigger unless already verified AT this
    exact seq or later (idempotent re-run safety)."""
    state = _absorption_trigger_state(repo, compile_seq)
    views = sorted({a["view"] for a in rec.get("absorbed", []) if a.get("view")})
    triggered = []
    for v in views:
        st = state.get(v) or {}
        lvs = st.get("last_verified_seq")
        if lvs is None or compile_seq > lvs:
            triggered.append(v)
    return triggered


def _absorbed_events_for_view(rec, view):
    """Sorted, deduplicated event list absorbed into VIEW by this compile
    record (may span more than one absorbed[] entry if the view was
    rebuilt more than once in a single pass -- defensive, not the common
    case)."""
    events = set()
    for a in rec.get("absorbed", []):
        if a.get("view") == view:
            events.update(a.get("events") or [])
    return sorted(events)


def _cumulative_diff(repo, view, current_body, state):
    """Unified diff of view's BODY (derivation region stripped on both
    sides -- see _strip_derivation_region) from its diff-BASE to CURRENT.
    Base = last-verified-or-adjudicated body, recovered via git-show against
    the journaled commit (see _recover_verified_body), if any such stamp
    exists; else the pre-first-SURVIVING-absorb blob (a real git blob sha,
    diffable directly; also stripped for symmetry with the verified-base
    branch -- reverted runs' ghosts are already excluded by
    _absorption_trigger_state, v3.0-67). Fail-honest: if a stamped base body
    cannot be recovered (no commit on record, commit unreachable, or
    recovered content disagrees with the pinned hash), the diff section says
    so explicitly (UNAVAILABLE marker) rather than silently diffing from
    empty or from the wrong base.

    BASELINE NAMING (v3.0-67): every return names its baseline in the first
    line, in words -- the checker must never have to guess what the "before"
    is. A genuinely NEW view says "NEW VIEW ... verifies from empty" out
    loud; an operator-adjudicated baseline says "adjudicated <date> by
    operator ruling, not machine-verified" out loud; an existing view can
    never again silently diff from an empty file."""
    st = state.get(view) or {}
    lvs = st.get("last_verified_seq")
    if lvs is None:
        pre_blob = st.get("first_pre_blob", "")
        if not pre_blob:
            return "(baseline: NEW VIEW -- never verified, no surviving "\
                   "prior absorption on record; cumulative diff is the "\
                   "full body)\n" + current_body
        pre_text = _git(repo, "cat-file", "-p", pre_blob)
        if not pre_text:
            base_line = ("(baseline: NEW VIEW -- this view was created by "
                         "the run under verify, at journal seq %s; no "
                         "pre-absorb content exists, so it legitimately "
                         "verifies from empty)"
                         % st.get("first_pre_seq"))
        else:
            base_line = ("(baseline: the view's real pre-absorb content "
                         "before journal seq %s -- never machine-verified; "
                         "reverted runs excluded)" % st.get("first_pre_seq"))
        cur_blob = _blob_of_text(repo, _strip_derivation_region(current_body))
        pre_stripped_blob = _blob_of_text(
            repo, _strip_derivation_region(pre_text))
        return base_line + "\n" + _git(repo, "diff", "--no-color",
                                       pre_stripped_blob, cur_blob)
    base_sha256 = st.get("last_verified_view_sha256")
    verify_commit = st.get("last_verified_commit")
    base_body, reason = _recover_verified_body(repo, view, verify_commit,
                                               base_sha256)
    if base_body is None:
        return ("(CUMULATIVE DIFF UNAVAILABLE: last-verified body at seq %d "
                "could not be recovered -- %s; full current body follows)\n"
                "%s" % (lvs, reason, current_body))
    if st.get("last_verified_kind") == "adjudicated":
        base_line = ("(baseline: adjudicated %s by operator ruling, not "
                     "machine-verified -- journal seq %d)"
                     % (st.get("last_verified_at") or "(date unrecorded)",
                        lvs))
    elif st.get("last_verified_kind") == "baseline-reset":
        # v3.0-106, named in the v3.0-67 out-loud style: the checker must
        # never have to guess what the "before" is.
        base_line = ("(baseline: reset to imported snapshot by operator "
                     "ruling, not machine-verified -- %s, journal seq %d)"
                     % (st.get("last_verified_provenance")
                        or "(provenance unrecorded)", lvs))
    else:
        base_line = ("(baseline: last machine-verified state, journal seq "
                     "%d)" % lvs)
    pre_blob = _blob_of_text(repo, _strip_derivation_region(base_body))
    cur_blob = _blob_of_text(repo, _strip_derivation_region(current_body))
    return base_line + "\n" + _git(repo, "diff", "--no-color", pre_blob,
                                   cur_blob)


def _recover_verified_body(repo, view, verify_commit, expected_sha256):
    """Recover the FULL body of VIEW as it was pinned by the absorption
    verify that produced `verify_commit` (2026-07-06 recovery-fix amendment
    -- REPLACES the earlier packet-file lookup, which keyed off the verify
    RECORD's seq but packet files on disk are named by the COMPILE seq being
    verified; those two seqs are different numbers, so the old lookup could
    silently miss or -- worse -- match the wrong packet. git-show against
    the journaled verify_commit has no such seq-identity ambiguity: it reads
    the exact commit the stamp was made in.

    Returns (body, None) on success, or (None, reason_str) fail-honest --
    NEVER a silent wrong guess:
      - no verify_commit on record (older journal, pre-amendment): reason
        names that.
      - `git show <verify_commit>:<view>` fails (commit/path unreachable):
        reason carries git's own error.
      - recovered content's sha256 (post-stamp, matching how the pin itself
        is computed -- see absorption_verified[].view_sha256) disagrees with
        the journaled pin: reason says so explicitly (corrupt pin or
        rewritten history), never treated as a fuzzy/best-effort match."""
    if not verify_commit:
        return None, "no verify_commit recorded for this view's last stamp"
    p = subprocess.run(
        ["git", "-C", repo, "show", "%s:%s" % (verify_commit, view)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        return None, ("git show %s:%s failed: %s"
                      % (verify_commit, view, (p.stderr or "").strip()[-200:]))
    body = p.stdout
    if expected_sha256 and _sha256(body) != expected_sha256:
        return None, ("recovered body at commit %s has sha256 %s, does not "
                      "match the journaled pin %s"
                      % (verify_commit, _sha256(body), expected_sha256))
    return body, None


_VERIFIED_BLOCK_RE = re.compile(
    r"(?ms)^verified:[ \t]*(?:\S.*)?\n((?:^[ \t]+.*\n?)*)")


def _stamp_verified_block(text, status, at, verifier_vendor, verifier_model_id,
                          absorb_vendor, absorb_model_id, packet_hash,
                          artifact):
    """Rewrite the `verified:` sub-block inside the view's derivation region
    (spec sec.5) IN PLACE -- every other top-level key (tier, entities,
    consumed_status, ...) is left byte-untouched. Fails closed
    (ValidationError) if the view carries no derivation region or no
    `verified:` key inside it (never silently appends a second one)."""
    region_lines = asm._extract_derivation(text)
    if region_lines is None:
        raise ValidationError(
            "cannot stamp verified: view has no derivation region")
    region = "\n".join(region_lines)
    if not re.search(r"(?m)^verified:", region):
        raise ValidationError(
            "cannot stamp verified: derivation region has no verified: key")
    new_block = (
        "verified:\n"
        "  status: %s\n"
        "  at: %s\n"
        "  verifier_vendor: %s\n"
        "  verifier_model_id: %s\n"
        "  absorb_vendor: %s\n"
        "  absorb_model_id: %s\n"
        "  packet_hash: %s\n"
        "  artifact: %s\n"
        % (status, at, verifier_vendor, verifier_model_id, absorb_vendor,
           absorb_model_id, packet_hash, artifact))
    new_region, n = _VERIFIED_BLOCK_RE.subn(new_block, region + "\n", count=1)
    if n != 1:
        raise ValidationError(
            "cannot stamp verified: verified: key found but block pattern "
            "did not match (malformed derivation region)")
    new_region = new_region[:-1] if new_region.endswith("\n") else new_region
    si = text.find(asm.DERIV_START)
    ei = text.find(asm.DERIV_END, si)
    if si == -1 or ei == -1:
        raise ValidationError(
            "cannot stamp verified: derivation delimiters not found "
            "verbatim (unexpected -- _extract_derivation found a region)")
    region_start = text.index("\n", si) + 1
    return text[:region_start] + new_region + "\n" + text[ei:]


_MINTED_BY_RE = re.compile(r"(?m)^minted_by:[ \t]*(\S+)[ \t]*$")
_CONSUMED_STATUS_LINE_RE = re.compile(r"(?m)^consumed_status:[ \t]*(\S+)[ \t]*$")


def _advance_consumed_status(text):
    """v3.0-71: advance `consumed_status: legacy-assumed` -> `verified-consumed`
    iff the region carries `minted_by: engine` -- the engine-born population
    only. Called at the view-write site AFTER _stamp_verified_block succeeds,
    so the advance rides the same atomic write as the stamp; the two helpers
    stay single-purpose and _stamp_verified_block's every-other-key-untouched
    contract stays true of that function. Returns (text, advanced).

    FAIL-CLOSED on everything else -- text returned unchanged, never an
    error, and the stamp still lands:
      * no derivation region, or no minted_by key (every region minted
        before the field existed, including the v3.0.29-.36 genuine engine
        mints -- conservative labels kept, ratified default 3: no relabel);
      * minted_by: backfill (the F13/B3 migration-audit obligation is the
        backfilled population's, and clearing it on a verify confirm is the
        precise loosening v3.0-71's entry forbids) or any unknown value;
      * any consumed_status other than exactly `legacy-assumed` -- in
        particular `audit-pending` NEVER advances: that is the F12
        obligation, cleared only by an actual audit."""
    region_lines = asm._extract_derivation(text)
    if region_lines is None:
        return text, False
    region = "\n".join(region_lines)
    m = _MINTED_BY_RE.search(region)
    if not m or m.group(1) != "engine":
        return text, False
    c = _CONSUMED_STATUS_LINE_RE.search(region)
    if not c or c.group(1) != "legacy-assumed":
        return text, False
    new_region = (region[:c.start()] + "consumed_status: verified-consumed"
                  + region[c.end():])
    si = text.find(asm.DERIV_START)
    ei = text.find(asm.DERIV_END, si)
    if si == -1 or ei == -1:
        return text, False
    region_start = text.index("\n", si) + 1
    return text[:region_start] + new_region + "\n" + text[ei:], True


def _absorb_substrate_fields(verdict):
    """Pull verifier/absorb vendor+model_id STRICTLY from the verdict's own
    substrate block (F17 attestation channel) -- never an orchestrator
    string. Returns None if the verdict carries no usable substrate block
    (e.g. a non-confirm/substrate-gated verdict) -- callers must not stamp
    in that case."""
    sub = verdict.get("substrate")
    if not isinstance(sub, dict):
        return None
    fields = ("verifier_vendor", "verifier_model_id", "absorb_vendor",
             "absorb_model_id")
    if not all(sub.get(f) for f in fields):
        return None
    return {f: sub[f] for f in fields}


def _harvest_verdict_evidence_paths(repo, verdict):
    """Mechanically harvest repo-relative evidence artifact paths a verify
    backend's verdict dict may carry, so verify_run can fold them into the
    SAME stage-only commit as the verdict/journal records (2026-07-06
    evidence-copy folding -- see verify_run's docstring). Purely mechanical:
    reads exactly two documented keys, never judges packet contents.

      - verdict.get("evidence_file")
      - verdict.get("substrate", {}).get("attestation", {}).get("artifact")
        (guarded: "substrate"/"attestation" may be absent or not a dict on
        any verdict shape that isn't BridgeVerifyBackend's -- e.g. the
        FixtureVerifyBackend used by --self-test carries neither key, and
        that absence must never fail the run)

    A candidate is folded only if it is a non-empty string, repo-relative
    (not absolute, no drive letter, does not start with ".."), and names a
    file that actually exists under `repo` -- never absolute/escaping paths,
    and never a path a fixture backend simply chose not to write (a missing
    evidence/attestation artifact is silently skipped, not an error: this
    harvest augments the commit, it never gates the run over what a backend
    did or didn't return). Returns a list of repo-relative POSIX paths,
    deduplicated and order-preserving.

    FAIL-CLOSED NORMALIZATION (2026-07-06, closes an embedded-traversal gap
    found by cross-vendor review): the candidate is posixpath.normpath'd
    BEFORE the reject checks below run, and the NORMALIZED form is what gets
    checked, joined, and returned. Checking the raw string alone let an
    embedded-traversal shape like "receipts/../../outside.md" slip past --
    it does not start with ".." or look absolute as written, but normalizes
    to something that walks above `repo`. Normalizing first means the reject
    checks see what the path actually resolves to, not its surface spelling."""
    out = []
    if not isinstance(verdict, dict):
        return out
    candidates = [verdict.get("evidence_file")]
    substrate_block = verdict.get("substrate")
    if isinstance(substrate_block, dict):
        attestation_block = substrate_block.get("attestation")
        if isinstance(attestation_block, dict):
            candidates.append(attestation_block.get("artifact"))
    for cand in candidates:
        if not isinstance(cand, str) or not cand:
            continue
        posix_cand = posixpath.normpath(cand.replace("\\", "/"))
        if posix_cand == ".." or posix_cand.startswith("../"):
            continue
        if os.path.isabs(posix_cand):
            continue
        # drive-letter absolute path (e.g. "C:/foo") -- os.path.isabs is
        # sufficient on Windows but this guards the check on POSIX hosts too
        if re.match(r"^[A-Za-z]:", posix_cand):
            continue
        ap = os.path.join(repo, posix_cand.replace("/", os.sep))
        if not os.path.isfile(ap):
            continue
        if posix_cand not in out:
            out.append(posix_cand)
    return out


def verify_run(repo, compile_seq, verify_backend, run_type="verify"):
    """VERIFY pass over a compile record's PENDING_NOOP_CANDIDATEs, unioned
    per EVENT. Hub events whose content routes to sibling views (via other
    no-op candidates or via absorption elsewhere) get ONE packet per event,
    covering the UNION of every routed view's CURRENT body -- never a
    per-(event,view) no-op claim that ignores where the event's content
    actually landed. For each event E: routed views = every noop_candidates
    view naming E plus every absorbed view whose events list contains E
    (sorted). ONE backend call per event, over that union packet. A
    confirmed verdict flips ALL of E's pending candidates together (same
    artifact, same packet_sha256); any other verdict leaves all of them
    PENDING with the attempt recorded. Journal is append-only: the flips
    land in a NEW record (run_type=verify) whose noop_candidates carry
    verified=true, artifact, packet_sha256, verified_at -- committed
    stage-only with the artifacts.

    EVIDENCE-COPY FOLDING (2026-07-06, closes the untracked-copies nit queued
    2026-07-05/06): a verify backend's returned verdict dict may carry its
    OWN evidence artifacts alongside the no-op/absorption verdict JSON this
    function already writes -- e.g. BridgeVerifyBackend.verify() returns
    verdict["evidence_file"] (the packet copy it staged) and, on a good
    attestation, verdict["substrate"]["attestation"]["artifact"] (the F17
    attest record). Those paths are NOT produced by this function, so they
    must be harvested mechanically from each verdict dict and folded into
    the SAME stage-only commit as the verdict/journal records -- otherwise
    they are untracked worktree files that the committed journal references
    by path but that never entered git history (evidence loss on worktree
    cleanup, observed live twice). `_harvest_verdict_evidence_paths` performs
    this harvest for every verify_backend.verify() call below (both the
    no-op union loop and the absorption-verify loop): journal-referenced
    evidence paths must resolve at the verify commit -- self-contained
    history, never a separate untracked copy."""
    t0 = time.strftime("%Y-%m-%dT%H:%M:%S")
    _lp, _b = core.acquire_lock(repo, run_type)
    try:
        jd = core.journal_dir(repo)
        rec = json.load(open(os.path.join(jd, "%d.json" % compile_seq),
                             encoding="utf-8"))
        outdir = os.path.join("receipts", "verify")
        os.makedirs(os.path.join(repo, outdir), exist_ok=True)

        ncs = rec.get("noop_candidates", [])
        pending_idx = [i for i, nc in enumerate(ncs)
                      if not (nc.get("verified")
                             or nc.get("disposition") == "CONSUMED")]
        events = sorted({ncs[i]["event"] for i in pending_idx})

        out_ncs = list(ncs)   # start as a copy; pending entries get replaced
        artifacts = []
        confirmed_candidates = 0
        events_confirmed = 0

        def _body(rel):
            p = os.path.join(repo, rel.replace("/", os.sep))
            return open(p, encoding="utf-8").read() if os.path.isfile(p) else ""

        for e_idx, event in enumerate(events):
            routed_views = _routed_views_for_event(rec, event)
            ebody = _body(event)
            view_bodies = {v: _body(v) for v in routed_views}

            claim = ("CLAIM: event %s carries zero load-bearing claims absent "
                     "from the union of its routed views: %s."
                     % (event, ", ".join(routed_views)))
            sections = ["## FULL VIEW BODY: %s\n%s" % (v, view_bodies[v])
                       for v in routed_views]
            packet = ("# NOOP VERIFY PACKET seq%d-e%d\n\n%s\n\n"
                      "## FULL EVENT BODY\n%s\n%s"
                      % (compile_seq, e_idx, claim, ebody,
                         "\n".join(sections)))

            # F16 (additive): mechanically-resolved corpus excerpts for this
            # event's routed views, appended AFTER the mandated sections above.
            excerpts = _corpus_excerpts_for_event(repo, rec, event,
                                                  routed_views)
            excerpt_section = _render_corpus_excerpt_section(excerpts)
            if excerpt_section:
                packet = packet + "\n\n" + excerpt_section
            # Verifier demotion (2026-08-09): same REASON CLASS instruction
            # as the absorption packets, additive and last.
            packet = packet + "\n\n" + _REASON_CLASS_SECTION

            verdict = verify_backend.verify(packet)
            art_rel = "%s/noop-seq%d-e%d.json" % (
                outdir.replace(os.sep, "/"), compile_seq, e_idx)
            with open(os.path.join(repo, art_rel.replace("/", os.sep)), "w",
                      encoding="utf-8", newline="\n") as fh:
                json.dump(verdict, fh, indent=1, sort_keys=True)
            artifacts.append(art_rel)
            for ev_path in _harvest_verdict_evidence_paths(repo, verdict):
                if ev_path not in artifacts:
                    artifacts.append(ev_path)

            packet_sha = _sha256(packet)
            union_view_sha256 = {v: _sha256(view_bodies[v])
                                 for v in routed_views}
            confirm = str(verdict.get("verdict", "")).lower().startswith(
                "confirm")
            verified_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            # Verifier demotion (2026-08-09): non-confirm union legs journal
            # the same record-time class fields as absorption legs. The
            # verify disposition gets its OWN key here (`verify_disposition`)
            # because `disposition` on a noop_candidates entry already means
            # the CONSUMED lifecycle -- documented divergence, not drift.
            if not confirm:
                nv_label, nv_classes, nv_disp = classify_reason_classes(
                    verdict)

            for i in pending_idx:
                nc = ncs[i]
                if nc["event"] != event:
                    continue
                vbody_own = _body(nc["view"])
                nc2 = dict(nc, artifact=art_rel, packet_sha256=packet_sha,
                          justification=dict(
                              nc.get("justification") or {},
                              event_sha256=_sha256(ebody),
                              view_sha256=_sha256(vbody_own),
                              union_views=routed_views,
                              union_view_sha256=union_view_sha256))
                if confirm:
                    nc2["verified"] = True
                    nc2["verified_at"] = verified_at
                    nc2["disposition"] = "CONSUMED"
                    confirmed_candidates += 1
                else:
                    nc2["verdict_label"] = nv_label
                    nc2["reason_classes"] = nv_classes
                    nc2["verify_disposition"] = nv_disp
                out_ncs[i] = nc2
            if confirm:
                events_confirmed += 1

        # ---------------------------------------------- absorption-verify
        # (spec sec.7 full VERIFY; amendment 2026-07-05). Assembled AFTER the
        # no-op union packets above, same run, same journal record. One
        # packet per triggered T1 view: a view appears in THIS record's
        # absorbed[] and has never been verified, or was last verified at a
        # seq < compile_seq.
        trigger_state = _absorption_trigger_state(repo, compile_seq)
        triggered_views = _triggered_absorption_views(repo, compile_seq, rec)
        absorption_verified = []
        absorption_verify_attempts = []
        absorption_checked = 0
        absorption_confirmed = 0
        touched_view_paths = []

        packet_dir = os.path.join(repo, "receipts", "verify", "packets")
        os.makedirs(packet_dir, exist_ok=True)

        for v_idx, view in enumerate(triggered_views):
            absorption_checked += 1
            abs_events = _absorbed_events_for_view(rec, view)
            current_body = _body(view)
            cumulative_diff = _cumulative_diff(repo, view, current_body,
                                               trigger_state)

            # v3.0-63: when the compile record journaled a claim routing
            # covering this view's events, the charge is plan-scoped -- two
            # graded questions (owned-claim fidelity; enumeration
            # completeness). Records without routing (all history, and every
            # plan that omits the block) keep the legacy total-coverage
            # charge BYTE-IDENTICAL.
            claim_scope = _view_claim_scope(rec.get("claim_routing"), view,
                                            abs_events)
            if claim_scope is not None:
                claim = (
                    "CLAIM: view %s at post-absorb state faithfully carries "
                    "its DECLARED SCOPE of events %s, per the DECLARED "
                    "CLAIM ROUTING section below: every manifest claim is "
                    "supported by the absorbed events; every claim this "
                    "view OWNS is represented or implied in the view "
                    "(compression and paraphrase are permitted -- the "
                    "represent-or-imply bar of the F13 precision "
                    "amendment, NOT verbatim reproduction); the cumulative "
                    "diff shown contains no change unaccounted for by "
                    "these events; and no load-bearing claim of the events "
                    "is absent from the declared routing altogether -- a "
                    "claim routed to a sibling view or deferred is "
                    "declared scope, not an omission from this view; a "
                    "load-bearing claim missing from the routing entirely "
                    "is a rejection (reason class: enumeration-incomplete)."
                    % (view, ", ".join(abs_events)))
            else:
                claim = ("CLAIM: view %s at post-absorb state faithfully "
                         "absorbs "
                         "events %s: every manifest claim is supported by the "
                         "absorbed events; every load-bearing claim in the "
                         "events is represented or implied in the view "
                         "(compression and paraphrase are permitted -- the "
                         "represent-or-imply bar of the F13 precision "
                         "amendment, NOT verbatim reproduction); and the "
                         "cumulative diff shown contains no change unaccounted "
                         "for by these events."
                         % (view, ", ".join(abs_events)))

            event_sections = []
            for e in abs_events:
                event_sections.append(
                    "## ABSORBED EVENT: %s\n%s" % (e, _body(e)))

            manifest_rows = []
            for a in rec.get("absorbed", []):
                if a.get("view") == view:
                    manifest_rows.extend(a.get("manifest") or [])
            manifest_text = "\n".join(
                json.dumps(m, sort_keys=True) for m in manifest_rows)

            excerpts = []
            for e in abs_events:
                excerpts.extend(_corpus_excerpts_for_event(repo, rec, e,
                                                            [view]))
            excerpt_section = _render_absorption_excerpt_section(excerpts)

            census_input_hash_v, census_output_hash_v = _run_routing_census(
                repo, abs_events)
            census_slice = [e for e in abs_events]
            census_section = (
                "## ROUTING CENSUS (F15)\ncensus_input_hash: %s\n"
                "census_output_hash: %s\ncensus_events: %s"
                % (census_input_hash_v, census_output_hash_v,
                   ", ".join(census_slice)))

            # Amendment section order (2026-07-05): 1 diff, 2 full body,
            # 3 absorbed events, 4 manifest claims, 5 CORPUS EXCERPTS (F16),
            # 6 ROUTING CENSUS (F15) -- F16 mechanically-resolved excerpts
            # MUST precede F15's census per the amendment's mandated order.
            # Sections 1-6 are MANDATED CATEGORICALLY (2026-07-06 categorical-
            # sections amendment): section 5 is ALWAYS rendered below, never
            # conditionally omitted -- when there are no resolvable
            # corpus_support entries for these events its body is the
            # explicit none-line from _render_absorption_excerpt_section,
            # not a missing section. (The no-op union packet's OWN F16
            # section, built by _render_corpus_excerpt_section above in
            # verify_run's earlier loop, is a separate contract -- LLM-6
            # integration -- and keeps its additive/omit-when-empty
            # behavior unchanged.)
            # FULL VIEW BODY (POST-ABSORB) shows the body with the
            # derivation region stripped (2026-07-06 recovery-fix
            # amendment): the verified: stamp -- which lands INSIDE that
            # region, conditionally, only once a confirm verdict is known --
            # is engine metadata about the body, never a body edit
            # (_stamp_verified_block touches nothing else). Showing the
            # stripped body here means what the verifier reads is exactly
            # the content whose fate this verdict decides, with no
            # not-yet-known-verdict metadata in view; the diff section above
            # is computed on the same stripped convention (_cumulative_diff)
            # so the two sections are symmetric. The journaled pin
            # (view_sha256) still covers the FULL post-stamp file, per spec.
            packet = (
                "# ABSORPTION VERIFY PACKET seq%d-v%d\n\n%s\n\n"
                "## CUMULATIVE DIFF SINCE LAST VERIFIED\n%s\n\n"
                "## FULL VIEW BODY (POST-ABSORB)\n%s\n\n%s\n\n"
                "## MANIFEST CLAIMS\n%s"
                % (compile_seq, v_idx, claim, cumulative_diff,
                   _strip_derivation_region(current_body),
                   "\n".join(event_sections), manifest_text))
            packet = packet + "\n\n" + excerpt_section
            packet = packet + "\n\n" + census_section
            # v3.0-63: DECLARED CLAIM ROUTING, additive-only and LAST --
            # every legacy section keeps its mandated 1-6 position, and the
            # section exists only when the record journaled routing for
            # this view's events.
            if claim_scope is not None:
                packet = packet + "\n\n" + _render_claim_routing_section(
                    claim_scope, view)
            # Verifier demotion (2026-08-09): the REASON CLASS instruction
            # rides every absorption packet, additive and strictly LAST --
            # legacy sections 1-6 and the routing section keep their
            # mandated order and bytes.
            packet = packet + "\n\n" + _REASON_CLASS_SECTION

            verdict = verify_backend.verify(packet)
            art_rel = "receipts/verify/absorb-seq%d-v%d.json" % (compile_seq,
                                                                  v_idx)
            with open(os.path.join(repo, art_rel.replace("/", os.sep)), "w",
                      encoding="utf-8", newline="\n") as fh:
                json.dump(verdict, fh, indent=1, sort_keys=True)
            artifacts.append(art_rel)
            for ev_path in _harvest_verdict_evidence_paths(repo, verdict):
                if ev_path not in artifacts:
                    artifacts.append(ev_path)

            packet_rel = "receipts/verify/packets/packet-absorb-seq%d-v%d.md" \
                % (compile_seq, v_idx)
            with open(os.path.join(repo, packet_rel.replace("/", os.sep)),
                      "w", encoding="utf-8", newline="\n") as fh:
                fh.write(packet)
            artifacts.append(packet_rel)

            packet_sha = _sha256(packet)
            confirm_v = str(verdict.get("verdict", "")).lower().startswith(
                "confirm")
            verified_at = time.strftime("%Y-%m-%dT%H:%M:%S")

            stamp_refusal_reason = None
            if confirm_v:
                substrate_fields = _absorb_substrate_fields(verdict)
                if substrate_fields is None:
                    confirm_v = False   # fail-closed: no usable attestation
                    stamp_refusal_reason = ("no usable F17 attestation "
                                            "substrate block on the verdict")

            if confirm_v:
                try:
                    new_text = _stamp_verified_block(
                        current_body, "passed", verified_at,
                        substrate_fields["verifier_vendor"],
                        substrate_fields["verifier_model_id"],
                        substrate_fields["absorb_vendor"],
                        substrate_fields["absorb_model_id"],
                        packet_sha, art_rel)
                except ValidationError as e:
                    # Fail-closed PER VIEW, not per run: a malformed/missing
                    # derivation region on THIS view must not crash the
                    # whole verify pass (the no-op union flips already
                    # computed above must still land). Record the attempt,
                    # stamp nothing, move on to the next triggered view.
                    confirm_v = False
                    stamp_refusal_reason = "stamp refused: %s" % e
                else:
                    # v3.0-71: transition at stamp time, same atomic write --
                    # the engine-born population advances to
                    # verified-consumed; everything else fail-closed
                    # unchanged (see _advance_consumed_status).
                    new_text, cs_advanced = _advance_consumed_status(new_text)
                    vp = os.path.join(repo, view.replace("/", os.sep))
                    with open(vp, "w", encoding="utf-8", newline="\n") as fh:
                        fh.write(new_text)
                    touched_view_paths.append(view)
                    av_entry = {
                        "view": view, "events": abs_events,
                        "verified_at": verified_at, "artifact": art_rel,
                        "packet_sha256": packet_sha,
                        "view_sha256": _sha256(new_text),
                        "substrate": verdict.get("substrate")}
                    if cs_advanced:
                        # Additive, absent wherever the advance did not fire,
                        # so legacy records read byte-identical; no lifecycle
                        # reader consumes it (the view file is the serving
                        # truth) -- it puts the record-time truth of the
                        # transition on the journal.
                        av_entry["consumed_status_advanced"] = True
                    absorption_verified.append(av_entry)
                    absorption_confirmed += 1

            if not confirm_v:
                # Verifier demotion (2026-08-09): class + disposition are
                # journaled HERE, once, at record time -- the lifecycle
                # (driver exit split, --verify-ledger, adjudication guards)
                # reads them from the journal alone, never re-derived from
                # the artifact. A stamp refusal on a confirmed verdict is
                # NOT a verifier rejection: classed `stamp-refused`,
                # blocking (v3.0-84's distinction, kept), excluded from
                # agreement stats by the ledger.
                if stamp_refusal_reason is not None:
                    v_label, r_classes, leg_disp = (
                        "confirmed", ["stamp-refused"], "blocking")
                else:
                    v_label, r_classes, leg_disp = classify_reason_classes(
                        verdict)
                absorption_verify_attempts.append({
                    "view": view, "events": abs_events,
                    "artifact": art_rel, "packet_sha256": packet_sha,
                    "reason": stamp_refusal_reason or verdict.get(
                        "reason", ""),
                    "verdict_label": v_label,
                    "reason_classes": r_classes,
                    "disposition": leg_disp})

        # F15: journal the standalone routing-census's input/output hashes for
        # this verify pass's own ledger slice (the events this pass checked) --
        # NEVER computed/graded by the LLM verify path itself, only recorded.
        census_input_hash, census_output_hash = _run_routing_census(
            repo, events)

        vrec = core.minimal_record(run_type,
                                   _git(repo, "rev-parse", "HEAD").strip())
        vrec["noop_candidates"] = out_ncs
        vrec["run_window"] = {"start": t0,
                              "end": time.strftime("%Y-%m-%dT%H:%M:%S")}
        vrec["verifies_seq"] = compile_seq
        vrec["census_input_hash"] = census_input_hash
        vrec["census_output_hash"] = census_output_hash
        if absorption_verified:
            vrec["absorption_verified"] = absorption_verified
        if absorption_verify_attempts:
            vrec["absorption_verify_attempts"] = absorption_verify_attempts
        seq, jpath = core.append_record(repo, vrec)
        jrel = os.path.relpath(jpath, repo).replace(os.sep, "/")
        sha = core.stage_only_commit(
            repo, touched_view_paths + artifacts + [jrel],
            "compile-v2 verify seq %d over seq %d (%d confirmed / %d checked, "
            "%d events confirmed / %d events checked, %d absorption "
            "confirmed / %d absorption checked)"
            % (seq, compile_seq, confirmed_candidates, len(pending_idx),
               events_confirmed, len(events), absorption_confirmed,
               absorption_checked))

        # verify_commit annotation (2026-07-06 recovery-fix amendment): the
        # commit that just landed (`sha`) is the ONE place the stamped view
        # bodies and this record live together -- exactly the commit later
        # recovery (_recover_verified_body) needs to `git show <sha>:<view>`
        # against. But `sha` is only known AFTER stage_only_commit returns,
        # and the record's own bytes (committed as part of that same commit)
        # cannot name a hash of themselves -- no journal record can predict
        # its own resulting commit sha (the hash depends on the very bytes
        # being written). So this is NOT foldable into the commit above by
        # construction; it is captured in a small SEPARATE follow-up commit
        # that touches ONLY the journal file (never a view), annotating each
        # absorption_verified[] entry with verify_commit=sha (the CONTENT
        # commit's real, permanent, reachable sha -- never rewritten,
        # amend is deliberately NOT used here so `sha` stays valid and
        # reachable, not a dangling predecessor object). This keeps
        # stage_only_commit's one-real-commit-per-content-change discipline
        # intact: `sha` (returned below, and what check-run-diff.check_acc4
        # validates) still names exactly the commit that carries the views
        # + this record; the follow-up commit is pure metadata annotation on
        # top, never inspected by ACC-4 (it touches no wiki/ view).
        if absorption_verified:
            # Re-read the AS-WRITTEN record from disk rather than reusing
            # the pre-append `vrec` in memory: append_record (compile-core)
            # builds its own dict copy carrying the engine-owned seq/
            # prev_record_hash fields that were never written back onto
            # `vrec` -- patching `vrec` directly and re-dumping it would
            # silently drop those two required fields from the file.
            with open(jpath, encoding="utf-8") as fh:
                on_disk_rec = json.load(fh)
            for entry in on_disk_rec.get("absorption_verified", []):
                entry["verify_commit"] = sha
            with open(jpath, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(on_disk_rec, fh, indent=1, sort_keys=True)
            core.stage_only_commit(
                repo, [jrel],
                "compile-v2 verify seq %d: annotate verify_commit=%s on "
                "%d absorption_verified stamp(s)"
                % (seq, sha, len(absorption_verified)))

        return {"sha": sha, "seq": seq, "confirmed": confirmed_candidates,
                "checked": len(pending_idx),
                "events_checked": len(events),
                "events_confirmed": events_confirmed,
                "census_input_hash": census_input_hash,
                "census_output_hash": census_output_hash,
                "absorption_checked": absorption_checked,
                "absorption_confirmed": absorption_confirmed,
                # v3.0-84: per-view non-confirm reasons, surfaced so the driver
                # can say WHICH failure class each leg hit -- a verifier
                # rejection and a confirmed-verdict-whose-stamp-refused (e.g. a
                # legacy view with no derivation region) previously printed
                # identically as "0 of N confirmed", which cost a live session
                # its diagnosis on 2026-07-31 (the verdict artifact said
                # confirmed; the summary said non-confirm; nothing said why).
                "absorption_attempts": [
                    {"view": a.get("view", ""), "reason": a.get("reason", ""),
                     # verifier demotion (2026-08-09): surfaced for the
                     # driver's per-leg lines and RECORDED SIGNALS band;
                     # the journal record remains the authority the driver
                     # partitions on.
                     "verdict_label": a.get("verdict_label"),
                     "reason_classes": a.get("reason_classes"),
                     "disposition": a.get("disposition")}
                    for a in absorption_verify_attempts]}
    finally:
        core.release_lock(repo)


class FixtureVerifyBackend:
    """Deterministic: confirms iff the packet's event body contains
    'already represented'; rejects otherwise."""

    def verify(self, packet):
        ok = "already represented" in packet
        return {"verdict": "confirmed" if ok else "rejected",
                "reason": "fixture", "uncertainty": "confident",
                "verifier": {"vendor": "openai", "model": "fixture"}}


# --------------------------------------------------------------- fixture backend
class FixtureAbsorbBackend:
    """Deterministic absorber for gates/self-test: appends each event's body
    under a per-event section; events named *noop* are no-ops."""

    def absorb(self, view_rel, view_text, events):
        new = view_text if view_text.endswith("\n") or not view_text \
            else view_text + "\n"
        manifest, noops, support = [], [], []
        changedany = False
        for erel in sorted(events):
            base = os.path.basename(erel)
            if "noop" in base:
                noops.append({"event": erel,
                              "justification_note": "fixture no-op"})
                continue
            sec = "Absorbed %s" % base
            body = events[erel].strip().splitlines()
            first = body[-1] if body else ""
            new += "\n## %s\n%s\n" % (sec, first)
            manifest.append({"event": erel, "section": sec})
            support.append({"artifact": erel,
                            "artifact_sha256": _sha256(events[erel]),
                            "support_lines": [first]})
            changedany = True
        return {"new_text": new if changedany else None,
                "manifest": manifest if changedany else [],
                "corpus_support": support if changedany else [],
                "noops": noops}


class BrokenManifestBackend(FixtureAbsorbBackend):
    """Claims a section it never edits -- must be REFUSED pre-journal."""

    def absorb(self, view_rel, view_text, events):
        out = super().absorb(view_rel, view_text, events)
        if out["new_text"] is not None:
            out["manifest"].append({"event": sorted(events)[0],
                                    "section": "Phantom Section"})
        return out


class FabricatedSupportBackend(FixtureAbsorbBackend):
    def absorb(self, view_rel, view_text, events):
        out = super().absorb(view_rel, view_text, events)
        if out["new_text"] is not None:
            e0 = sorted(events)[0]
            out["corpus_support"].append(
                {"artifact": e0, "artifact_sha256": _sha256(events[e0]),
                 "support_lines": ["this line exists nowhere in the artifact"]})
        return out


# --------------------------------------------------------------- self-test
def self_test():
    import shutil
    import tempfile
    total = failed = 0
    _RENDERED = {}

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    base = tempfile.mkdtemp(prefix="cv2-")
    try:
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", base] + args, capture_output=True)
        os.makedirs(os.path.join(base, "wiki"))
        os.makedirs(os.path.join(base, "raw"))
        open(os.path.join(base, "wiki", "a.md"), "w", newline="\n").write(
            "# A\n\n## Intro\nhello\n")
        open(os.path.join(base, "wiki", "b.md"), "w", newline="\n").write(
            "# B\n\n## Intro\nworld\n")
        open(os.path.join(base, "raw", "e1.md"), "w", newline="\n").write(
            "fact one\n")
        open(os.path.join(base, "raw", "e2.md"), "w", newline="\n").write(
            "fact two\n")
        open(os.path.join(base, "raw", "e3-noop.md"), "w", newline="\n").write(
            "already represented\n")
        open(os.path.join(base, "salt.md"), "w").write("salt\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "seed"],
                       capture_output=True)

        plan = {"items": [
            {"view": "wiki/a.md", "events": ["raw/e1.md", "raw/e3-noop.md"],
             "event_class": {"raw/e1.md": {"class": "t3", "origin": "explicit"},
                             "raw/e3-noop.md": {"class": "t1",
                                                "origin": "explicit"}}},
            {"view": "wiki/b.md", "events": ["raw/e2.md"],
             "event_class": {"raw/e2.md": {"class": "t3",
                                           "origin": "judgment"}}},
        ]}
        open(os.path.join(base, "salt.md"), "a").write("dirty\n")

        res = run(base, plan, FixtureAbsorbBackend())
        case("run completes: 2 rebuilds, seq 1",
             res["rebuilds"] == 2 and res["seq"] == 1)
        case("lock released after run",
             not os.path.isfile(core.lock_path(base)))
        # the produced commit passes the REAL gates -- PENDING_NOOP_CANDIDATE
        # entries are pre-VERIFY (verified=false), so check-run-diff's PENDING
        # exemption means no artifact/hash gaps are reported here at all; VERIFY
        # fills artifact+hash in its own append-only follow-up record below.
        probs, _rec = crd.check_acc4(base, res["sha"])
        case("ACC-4 on the produced commit: clean (PENDING no-op exemption)",
             probs == [])
        sprobs = crd.check_sections(base, res["sha"])
        case("LLM-2 sections on the produced commit: clean", sprobs == [])
        case("chain intact after run", core.check_chain(base) == 1)
        # PENDING_NOOP_CANDIDATE discipline
        ncs = res["noop_candidates"]
        case("T1 no-op -> PENDING_NOOP_CANDIDATE, never consumed",
             ncs and ncs[0]["disposition"] == "PENDING_NOOP_CANDIDATE"
             and ncs[0]["verified"] is False)
        case("no-op justification carries full event+view hashes",
             ncs[0]["justification"]["event_sha256"]
             and ncs[0]["justification"]["view_sha256"])
        case("salt untouched by the run commit",
             "dirty" in open(os.path.join(base, "salt.md")).read()
             and "salt.md" in subprocess.run(
                 ["git", "-C", base, "status", "--porcelain"],
                 capture_output=True, text=True).stdout)

        # VERIFY stage: pending no-op flips via a NEW append-only record
        vres = verify_run(base, res["seq"], FixtureVerifyBackend())
        case("verify run: 1 checked, 1 confirmed (fixture)",
             vres["checked"] == 1 and vres["confirmed"] == 1)
        case("verify record chains cleanly", core.check_chain(base) == 2)
        vrec = json.load(open(os.path.join(core.journal_dir(base),
                                           "%d.json" % vres["seq"]),
                              encoding="utf-8"))
        nc2 = vrec["noop_candidates"][0]
        case("flipped candidate: verified + artifact + packet hash + same-run "
             "timestamp", nc2["verified"] and nc2["artifact"]
             and nc2["packet_sha256"]
             and vrec["run_window"]["start"] <= nc2["verified_at"]
             <= vrec["run_window"]["end"])
        vprobs, _ = crd.check_acc4(base, vres["sha"])
        case("ACC-4 clean on the verify commit", vprobs == [])
        case("verdict artifact committed with the record",
             any("receipts/verify/" in f
                 for f in crd.commit_files(base, vres["sha"])))

        # ------------------------------- evidence-copy folding (2026-07-06)
        # A fixture verify backend that writes its OWN evidence artifacts
        # (mirroring BridgeVerifyBackend: a packet copy at "evidence_file"
        # and, on confirm, an F17-style attest record at
        # substrate.attestation.artifact) under receipts/verify/ in the
        # fixture repo. Both paths must be folded into the SAME stage-only
        # verify commit -- never left as untracked worktree files the
        # journal merely references by path.
        class _EvidenceWritingVerifyBackend:
            def __init__(self, repo_root):
                self.repo_root = repo_root
                self.n = 0

            def verify(self, packet):
                self.n += 1
                evdir = os.path.join(self.repo_root, "receipts", "verify",
                                     "staging")
                os.makedirs(evdir, exist_ok=True)
                ev_rel = "receipts/verify/staging/packet-fold-%d.md" % self.n
                with open(os.path.join(self.repo_root,
                                       ev_rel.replace("/", os.sep)), "w",
                          encoding="utf-8", newline="\n") as fh:
                    fh.write(packet)
                attdir = os.path.join(self.repo_root, "receipts", "verify",
                                      "attest")
                os.makedirs(attdir, exist_ok=True)
                att_rel = "receipts/verify/attest/fold-%d.attest.json" % self.n
                with open(os.path.join(self.repo_root,
                                       att_rel.replace("/", os.sep)), "w",
                          encoding="utf-8", newline="\n") as fh:
                    json.dump({"channel": "subprocess-runtime"}, fh)
                return {"verdict": "confirmed", "reason": "fixture-fold",
                        "uncertainty": "confident",
                        "verifier": {"vendor": "openai", "model": "fixture"},
                        "evidence_file": ev_rel,
                        "substrate": {"attestation": {"artifact": att_rel}}}

        # a fresh compile so this verify pass has its own pending no-op
        # candidate to fire the evidence-writing backend over (via the
        # NO-OP UNION loop -- FixtureAbsorbBackend treats an event as a
        # no-op iff "noop" appears in its basename)
        open(os.path.join(base, "raw", "e-fold-noop.md"), "w",
             newline="\n").write("already represented\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "fold fixture"],
                       capture_output=True)
        fold_plan = {"items": [
            {"view": "wiki/a.md", "events": ["raw/e-fold-noop.md"],
             "event_class": {"raw/e-fold-noop.md": {"class": "t1",
                                                     "origin": "explicit"}}},
        ]}
        fold_res = run(base, fold_plan, FixtureAbsorbBackend())
        fold_backend = _EvidenceWritingVerifyBackend(base)
        fold_vres = verify_run(base, fold_res["seq"], fold_backend)
        fold_commit_files = crd.commit_files(base, fold_vres["sha"])
        case("evidence-copy folding: verdict evidence_file path tracked in "
            "the verify commit",
             "receipts/verify/staging/packet-fold-1.md" in fold_commit_files)
        case("evidence-copy folding: substrate.attestation.artifact path "
            "tracked in the verify commit",
             "receipts/verify/attest/fold-1.attest.json" in fold_commit_files)

        # verdict carrying evidence_file="" and no substrate/attestation must
        # not break the run (harvest silently skips both)
        class _NoEvidenceVerifyBackend:
            def verify(self, packet):
                return {"verdict": "confirmed", "reason": "no-evidence",
                        "uncertainty": "confident",
                        "verifier": {"vendor": "openai", "model": "fixture"},
                        "evidence_file": ""}

        open(os.path.join(base, "raw", "e-fold2-noop.md"), "w",
             newline="\n").write("already represented\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "fold fixture 2"],
                       capture_output=True)
        fold_plan2 = {"items": [
            {"view": "wiki/a.md", "events": ["raw/e-fold2-noop.md"],
             "event_class": {"raw/e-fold2-noop.md": {"class": "t1",
                                                      "origin": "explicit"}}},
        ]}
        fold_res2 = run(base, fold_plan2, FixtureAbsorbBackend())
        try:
            fold_vres2 = verify_run(base, fold_res2["seq"],
                                    _NoEvidenceVerifyBackend())
            case("evidence-copy folding: empty evidence_file / absent "
                "attestation does not break the run",
                 fold_vres2["checked"] == 1)
        except Exception as e:  # pragma: no cover -- must never raise
            case("evidence-copy folding: empty evidence_file / absent "
                "attestation does not break the run (raised %r)" % (e,),
                 False)

        # embedded-traversal evidence_file (2026-07-06 cross-vendor finding):
        # a verdict naming "receipts/../../outside.md" reads, as WRITTEN,
        # repo-relative (no leading "..", not absolute) -- the pre-fix checks
        # ran against that raw spelling and reached os.path.isfile against a
        # path that actually resolves OUTSIDE `base` entirely. A real file is
        # planted a directory above `base` so the pre-fix code would have
        # harvested (and the verify commit would have staged) a file the
        # fixture repo never owns; post-fix this candidate must be dropped
        # silently, same as any other missing/invalid candidate -- never a
        # raised error, never a tracked path.
        outside_dir = os.path.dirname(base)
        outside_path = os.path.join(outside_dir, "cv2-outside-traversal.md")
        open(outside_path, "w", newline="\n").write(
            "content that must never enter the fixture repo's history\n")
        try:
            class _TraversalVerifyBackend:
                def verify(self, packet):
                    return {"verdict": "confirmed", "reason": "traversal",
                            "uncertainty": "confident",
                            "verifier": {"vendor": "openai",
                                        "model": "fixture"},
                            "evidence_file": "receipts/../../"
                                             "cv2-outside-traversal.md"}

            open(os.path.join(base, "raw", "e-fold3-noop.md"), "w",
                 newline="\n").write("already represented\n")
            subprocess.run(["git", "-C", base, "add", "-A"],
                           capture_output=True)
            subprocess.run(["git", "-C", base, "commit", "-qm",
                            "fold fixture 3"], capture_output=True)
            fold_plan3 = {"items": [
                {"view": "wiki/a.md", "events": ["raw/e-fold3-noop.md"],
                 "event_class": {"raw/e-fold3-noop.md": {
                     "class": "t1", "origin": "explicit"}}},
            ]}
            fold_res3 = run(base, fold_plan3, FixtureAbsorbBackend())
            try:
                fold_vres3 = verify_run(base, fold_res3["seq"],
                                        _TraversalVerifyBackend())
                case("embedded-traversal evidence_file: run completes "
                    "without error (fail-closed skip, not a raised "
                    "failure)", fold_vres3["checked"] == 1)
                fold_commit_files3 = crd.commit_files(base, fold_vres3["sha"])
                case("embedded-traversal evidence_file: NOT harvested into "
                    "the verify commit",
                     not any("cv2-outside-traversal.md" in f
                             for f in fold_commit_files3))
            except Exception as e:  # pragma: no cover -- must never raise
                case("embedded-traversal evidence_file: run completes "
                    "without error (raised %r)" % (e,), False)
        finally:
            if os.path.isfile(outside_path):
                os.remove(outside_path)

        # evidence-copy folding over the ABSORPTION-VERIFY loop (2026-07-06
        # cross-vendor finding): the folding fixtures above only ever drove
        # the NO-OP UNION loop (a T1 no-op event trips FixtureAbsorbBackend's
        # noop path, never absorbed[]). This leaves the absorption-verify
        # loop's OWN call to _harvest_verdict_evidence_paths (verify_run's
        # second loop, around the "absorb-seq%d-v%d.json" artifact writes)
        # unproven -- a real absorb (view lands in the compile record's
        # absorbed[], never verified) that the verify backend confirms while
        # ALSO returning evidence_file + substrate.attestation.artifact.
        # Independent view/event pair (never touches the av.md timeline
        # exercised elsewhere) so this is a self-contained fixture.
        os.makedirs(os.path.join(base, "wiki", "fold2"), exist_ok=True)
        os.makedirs(os.path.join(base, "raw", "fold2"), exist_ok=True)
        afold_view_text = (
            "---\ntitle: AFold\n---\n"
            "# --- derivation (engine-managed; strip region) ---\n"
            "schema_version: 3.2\nview: topic\nsummary: \"AFold\"\n"
            "entities: []\nstatus: active\ntier: T1\n"
            "consumed_status: legacy-assumed\norigin_max: human\n"
            "subscribes:\n  entities: []\n  corpus: []\nbundle: []\n"
            "verified: null\n"
            "# --- /derivation ---\n\n## Intro\noriginal body\n")
        open(os.path.join(base, "wiki", "fold2", "afold.md"), "w",
             newline="\n").write(afold_view_text)
        open(os.path.join(base, "raw", "fold2", "eafold.md"), "w",
             newline="\n").write("absorb fold fact\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "afold fixture"],
                       capture_output=True)

        class _AbsorbBackendFold(FixtureAbsorbBackend):
            def absorb(self, view_rel, view_text, events):
                new = view_text.rstrip("\n") + "\n\n## Absorbed\nfold fact\n"
                return {"new_text": new,
                        "manifest": [{"event": "raw/fold2/eafold.md",
                                     "section": "Absorbed"}],
                        "corpus_support": [], "noops": []}

        afold_plan = {"items": [{"view": "wiki/fold2/afold.md",
                                 "events": ["raw/fold2/eafold.md"],
                                 "event_class": {"raw/fold2/eafold.md": {
                                     "class": "t3", "origin": "explicit"}}}]}
        afold_res = run(base, afold_plan, _AbsorbBackendFold())

        class _EvidenceWritingAbsorbVerifyBackend:
            """Mirrors BridgeVerifyBackend: a confirm verdict that carries
            BOTH the substrate fields _stamp_verified_block needs (so the
            absorption-verify loop actually confirms and stamps) AND its own
            evidence_file / substrate.attestation.artifact paths (so the
            harvest inside that SAME loop has something real to fold)."""
            def __init__(self, repo_root):
                self.repo_root = repo_root
                self.n = 0

            def verify(self, packet):
                self.n += 1
                evdir = os.path.join(self.repo_root, "receipts", "verify",
                                     "staging")
                os.makedirs(evdir, exist_ok=True)
                ev_rel = "receipts/verify/staging/absorb-fold-%d.md" % self.n
                with open(os.path.join(self.repo_root,
                                       ev_rel.replace("/", os.sep)), "w",
                          encoding="utf-8", newline="\n") as fh:
                    fh.write(packet)
                attdir = os.path.join(self.repo_root, "receipts", "verify",
                                      "attest")
                os.makedirs(attdir, exist_ok=True)
                att_rel = ("receipts/verify/attest/absorb-fold-%d.attest.json"
                          % self.n)
                with open(os.path.join(self.repo_root,
                                       att_rel.replace("/", os.sep)), "w",
                          encoding="utf-8", newline="\n") as fh:
                    json.dump({"channel": "subprocess-runtime"}, fh)
                return {"verdict": "confirmed", "reason": "fixture-abs-fold",
                        "uncertainty": "confident",
                        "verifier": {"vendor": "openai", "model": "fixture"},
                        "substrate": {
                            "verifier_vendor": "openai",
                            "verifier_model_id": "gpt-5.5",
                            "absorb_vendor": "anthropic",
                            "absorb_model_id": "claude-opus-4-8",
                            "substrate_source": "invocation-metadata",
                            "attestation": {"artifact": att_rel}},
                        "evidence_file": ev_rel}

        afold_backend = _EvidenceWritingAbsorbVerifyBackend(base)
        afold_vres = verify_run(base, afold_res["seq"], afold_backend)
        case("absorption-verify evidence-copy folding: the absorb view "
            "actually confirmed (loop really fired, not skipped)",
             afold_vres.get("absorption_checked") == 1
             and afold_vres.get("absorption_confirmed") == 1)
        afold_commit_files = crd.commit_files(base, afold_vres["sha"])
        case("absorption-verify evidence-copy folding: verdict evidence_file "
            "path tracked in the verify commit",
             "receipts/verify/staging/absorb-fold-1.md" in afold_commit_files)
        case("absorption-verify evidence-copy folding: "
            "substrate.attestation.artifact path tracked in the verify "
            "commit",
             "receipts/verify/attest/absorb-fold-1.attest.json"
             in afold_commit_files)

        # ------------------------------------------------- per-EVENT union-verify
        # Hub event E routes to multiple views (as a pending no-op AND/OR via
        # absorption elsewhere). VERIFY must fire the backend ONCE per event,
        # over the UNION of all routed views' CURRENT bodies, not once per
        # (event,view) candidate.
        os.makedirs(os.path.join(base, "wiki", "hub"), exist_ok=True)
        os.makedirs(os.path.join(base, "raw", "hub"), exist_ok=True)
        open(os.path.join(base, "wiki", "hub", "va.md"), "w",
             newline="\n").write("# VA\nalready represented in A\n")
        open(os.path.join(base, "wiki", "hub", "vb.md"), "w",
             newline="\n").write("# VB\nalready represented in B\n")
        open(os.path.join(base, "wiki", "hub", "vc.md"), "w",
             newline="\n").write("# VC\npost-absorb content for C\n")
        open(os.path.join(base, "raw", "hub", "eh.md"), "w",
             newline="\n").write("hub event body\n")
        open(os.path.join(base, "raw", "hub", "eh2.md"), "w",
             newline="\n").write("second hub event body\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "hub fixture"],
                       capture_output=True)

        class _CallCountingBackend:
            def __init__(self, confirm=True):
                self.calls = []
                self.confirm = confirm

            def verify(self, packet):
                self.calls.append(packet)
                return {"verdict": "confirmed" if self.confirm else "rejected",
                        "reason": "fixture-union", "uncertainty": "confident",
                        "verifier": {"vendor": "openai", "model": "fixture"}}

        def _hub_rec(seq, confirm_backend):
            rec = core.minimal_record(
                "compile", _git(base, "rev-parse", "HEAD").strip())
            rec["noop_candidates"] = [
                {"view": "wiki/hub/va.md", "event": "raw/hub/eh.md",
                 "verified": False, "disposition": "PENDING_NOOP_CANDIDATE",
                 "event_class": "t1", "event_class_origin": "explicit",
                 "artifact": "", "packet_sha256": "",
                 "justification": {"event_sha256": "", "view_sha256": "",
                                   "note": "hub a"}},
                {"view": "wiki/hub/vb.md", "event": "raw/hub/eh.md",
                 "verified": False, "disposition": "PENDING_NOOP_CANDIDATE",
                 "event_class": "t1", "event_class_origin": "explicit",
                 "artifact": "", "packet_sha256": "",
                 "justification": {"event_sha256": "", "view_sha256": "",
                                   "note": "hub b"}},
                {"view": "wiki/hub/va.md", "event": "raw/hub/eh2.md",
                 "verified": False, "disposition": "PENDING_NOOP_CANDIDATE",
                 "event_class": "t1", "event_class_origin": "explicit",
                 "artifact": "", "packet_sha256": "",
                 "justification": {"event_sha256": "", "view_sha256": "",
                                   "note": "eh2 only"}},
            ]
            # partial-absorb: event eh also absorbed into vc.md (post-absorb
            # state), so eh's routed views are {va, vb, vc} even though vc
            # never appears as a noop_candidate. corpus_support carries one
            # RESOLVABLE pin (current body of raw/hub/eh.md) and one
            # UNRESOLVABLE pin (bogus sha256), to exercise F16's excerpt
            # embedding + fail-honest UNRESOLVED declaration.
            rec["absorbed"] = [{"view": "wiki/hub/vc.md",
                               "events": ["raw/hub/eh.md"],
                               "pre_blob": "", "post_blob": "x",
                               "manifest": [],
                               "corpus_support": [
                                   {"artifact": "raw/hub/eh.md",
                                    "artifact_sha256": _sha256(
                                        "hub event body\n"),
                                    "support_lines": ["hub event body"]},
                                   {"artifact": "raw/hub/eh.md",
                                    "artifact_sha256": "0" * 64,
                                    "support_lines": ["phantom line"]},
                               ]}]
            rec["run_window"] = {"start": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                 "end": time.strftime("%Y-%m-%dT%H:%M:%S")}
            return core.append_record(base, rec)

        # (a)/(a2): hub event pending against 2 noop views + 1 absorbed view
        # -> ONE backend call, packet contains all THREE view bodies. The
        # SAME record's absorbed[] also names wiki/hub/vc.md, never before
        # verified -- absorption-verify (2026-07-05 amendment) additively
        # fires ONE MORE call for that view's own packet on the SAME
        # verify_backend, same run, after the no-op union packets. So the
        # backend sees 2 no-op calls (one per distinct event) PLUS 1
        # absorption call (one per triggered view) = 3 total.
        confirm_backend = _CallCountingBackend(confirm=True)
        hub_seq, _hp = _hub_rec("compile-hub-1", confirm_backend)
        hres = verify_run(base, hub_seq, confirm_backend)
        noop_calls = [p for p in confirm_backend.calls
                     if p.startswith("# NOOP VERIFY PACKET")]
        absorb_calls = [p for p in confirm_backend.calls
                       if p.startswith("# ABSORPTION VERIFY PACKET")]
        case("hub union-verify: ONE backend call total for 2 distinct events "
             "(1 call/event)", len(noop_calls) == 2)
        case("absorption-verify: ONE additive backend call for the record's "
             "single triggered view (wiki/hub/vc.md, never verified)",
             len(absorb_calls) == 1)
        case("absorption-verify: additive calls land AFTER the no-op union "
             "calls in the SAME backend/run (assembly ordering)",
             confirm_backend.calls.index(absorb_calls[0])
             > max(confirm_backend.calls.index(c) for c in noop_calls))
        eh_calls = [p for p in confirm_backend.calls
                   if "CLAIM: event raw/hub/eh.md " in p]
        case("hub union-verify: exactly one call for hub event eh.md",
             len(eh_calls) == 1)
        packet = eh_calls[0]
        case("hub union-verify: packet contains va.md body",
             "already represented in A" in packet)
        case("hub union-verify: packet contains vb.md body",
             "already represented in B" in packet)
        case("hub union-verify: packet contains vc.md POST-absorb body "
             "(partial-absorb routed view)",
             "post-absorb content for C" in packet)
        case("hub union-verify: union CLAIM line names all routed views, "
             "sorted, comma-joined",
             re.search(r"^CLAIM: event raw/hub/eh\.md carries zero "
                       r"load-bearing claims absent from the union of its "
                       r"routed views: wiki/hub/va\.md, wiki/hub/vb\.md, "
                       r"wiki/hub/vc\.md\.$", packet, re.M) is not None)
        case("hub union-verify: exactly one line starts with 'CLAIM: '",
             sum(1 for ln in packet.splitlines()
                 if ln.startswith("CLAIM: ")) == 1)
        hvrec = json.load(open(os.path.join(core.journal_dir(base),
                                            "%d.json" % hres["seq"]),
                               encoding="utf-8"))
        eh_ncs = [nc for nc in hvrec["noop_candidates"]
                 if nc["event"] == "raw/hub/eh.md"]
        case("hub union-verify: confirm flips BOTH of eh.md's candidates",
             len(eh_ncs) == 2 and all(nc["verified"] for nc in eh_ncs))
        case("hub union-verify: both flipped candidates share the SAME "
             "artifact path", len({nc["artifact"] for nc in eh_ncs}) == 1)
        case("hub union-verify: both flipped candidates share the SAME "
             "packet_sha256", len({nc["packet_sha256"] for nc in eh_ncs}) == 1)
        case("hub union-verify: union pins recorded (union_views + "
             "union_view_sha256)",
             all(nc["justification"].get("union_views") ==
                 ["wiki/hub/va.md", "wiki/hub/vb.md", "wiki/hub/vc.md"]
                 for nc in eh_ncs)
             and all(set(nc["justification"].get("union_view_sha256", {}))
                    == {"wiki/hub/va.md", "wiki/hub/vb.md", "wiki/hub/vc.md"}
                    for nc in eh_ncs))

        # F16: mechanically-resolved corpus excerpts, additive section AFTER
        # the mandated FULL EVENT BODY / FULL VIEW BODY sections.
        case("F16: CORPUS EXCERPTS section present in the union packet",
             "## CORPUS EXCERPTS (F16 mechanically-resolved" in packet)
        case("F16: section lands AFTER the last FULL VIEW BODY section "
             "(additive, never before mandated content)",
             packet.index("## CORPUS EXCERPTS")
             > packet.rindex("## FULL VIEW BODY:"))
        case("F16: resolvable pin embeds a verbatim excerpt containing the "
             "support line", "hub event body" in packet.split(
                 "## CORPUS EXCERPTS", 1)[1])
        case("F16: resolvable pin's resolution=current recorded in packet",
             "resolution=current" in packet)
        case("F16: unresolvable pin declared UNRESOLVED, not silently "
             "dropped (fail-honest)",
             "UNRESOLVED artifact=raw/hub/eh.md artifact_sha256="
             + "0" * 64 in packet
             and "phantom line" in packet)
        case("F16: exactly one CLAIM line even with excerpt section appended "
             "(invariant preserved)",
             sum(1 for ln in packet.splitlines()
                 if ln.startswith("CLAIM: ")) == 1)

        # F15: routing-census input/output hashes journaled on the verify
        # record as a whole (standalone script, never self-graded here).
        case("F15: census_input_hash/census_output_hash returned from "
             "verify_run", hres.get("census_input_hash")
             and hres.get("census_output_hash"))
        case("F15: census hashes journaled on the verify record itself",
             hvrec.get("census_input_hash") == hres["census_input_hash"]
             and hvrec.get("census_output_hash") == hres["census_output_hash"])
        _im_check, ims_check, _out_check, os_check = rcensus.compute_census(
            base, ["raw/hub/eh.md", "raw/hub/eh2.md"])
        case("F15: journaled census hashes match an independent recompute "
             "over the same ledger slice (byte-identical, standalone "
             "script)", ims_check == hres["census_input_hash"]
             and os_check == hres["census_output_hash"])
        eh2_ncs = [nc for nc in hvrec["noop_candidates"]
                  if nc["event"] == "raw/hub/eh2.md"]
        case("hub union-verify: two distinct events -> two packets; "
             "confirming does not touch eh2.md's own candidate identity",
             len(eh2_ncs) == 1)
        case("hub union-verify: events_checked/events_confirmed keys present "
             "(additive)", hres.get("events_checked") == 2
             and hres.get("events_confirmed") == 2)
        case("hub union-verify: checked/confirmed stay CANDIDATE counts "
             "(receipt-schema compat)", hres["checked"] == 3
             and hres["confirmed"] == 3)

        # save rendered packet text for the report
        _RENDERED["union_packet"] = packet

        # (b) non-confirm -> both stay PENDING, attempt recorded
        reject_backend = _CallCountingBackend(confirm=False)
        hub_seq2, _hp2 = _hub_rec("compile-hub-2", reject_backend)
        hres2 = verify_run(base, hub_seq2, reject_backend)
        hvrec2 = json.load(open(os.path.join(core.journal_dir(base),
                                             "%d.json" % hres2["seq"]),
                                encoding="utf-8"))
        eh_ncs2 = [nc for nc in hvrec2["noop_candidates"]
                  if nc["event"] == "raw/hub/eh.md"]
        case("hub union-verify: non-confirm -> both candidates stay PENDING",
             len(eh_ncs2) == 2
             and all(not nc["verified"] for nc in eh_ncs2)
             and all(nc["disposition"] == "PENDING_NOOP_CANDIDATE"
                    for nc in eh_ncs2))
        case("hub union-verify: non-confirm attempt still records "
             "artifact+packet_sha256 (attempt recorded)",
             all(nc["artifact"] and nc["packet_sha256"] for nc in eh_ncs2))
        case("hub union-verify: non-confirm events_confirmed == 0",
             hres2.get("events_confirmed") == 0
             and hres2.get("events_checked") == 2)
        case("verifier demotion: a non-confirm union leg journals the "
             "record-time class fields (classless fixture reason -> "
             "unclassified/blocking; the CONSUMED-lifecycle `disposition` "
             "key untouched)",
             all(nc.get("verdict_label") == "rejected"
                 and nc.get("reason_classes") == ["unclassified"]
                 and nc.get("verify_disposition") == "blocking"
                 and nc["disposition"] == "PENDING_NOOP_CANDIDATE"
                 for nc in eh_ncs2))
        case("verifier demotion: the union packet carries the REASON CLASS "
             "instruction, additively",
             "## REASON CLASS (verifier demotion, 2026-08-09)"
             in reject_backend.calls[0])

        # (c) mixed-verdict -> per-event outcome: eh.md confirmed (both its
        # candidates flip), eh2.md rejected (its candidate stays PENDING)
        class _MixedVerdictBackend:
            def __init__(self):
                self.calls = []

            def verify(self, packet):
                self.calls.append(packet)
                if "CLAIM: event raw/hub/eh.md " in packet:
                    return {"verdict": "confirmed", "reason": "fixture-mixed",
                            "uncertainty": "confident",
                            "verifier": {"vendor": "openai",
                                        "model": "fixture"}}
                return {"verdict": "rejected", "reason": "fixture-mixed",
                        "uncertainty": "confident",
                        "verifier": {"vendor": "openai", "model": "fixture"}}

        mixed_backend = _MixedVerdictBackend()
        hub_seq3, _hp3 = _hub_rec("compile-hub-3", mixed_backend)
        hres3 = verify_run(base, hub_seq3, mixed_backend)
        hvrec3 = json.load(open(os.path.join(core.journal_dir(base),
                                             "%d.json" % hres3["seq"]),
                                encoding="utf-8"))
        eh_ncs3 = [nc for nc in hvrec3["noop_candidates"]
                  if nc["event"] == "raw/hub/eh.md"]
        eh2_ncs3 = [nc for nc in hvrec3["noop_candidates"]
                   if nc["event"] == "raw/hub/eh2.md"]
        case("mixed-verdict: both eh.md candidates verified/CONSUMED",
             len(eh_ncs3) == 2
             and all(nc["verified"] for nc in eh_ncs3)
             and all(nc["disposition"] == "CONSUMED" for nc in eh_ncs3))
        case("mixed-verdict: eh2.md candidate stays PENDING_NOOP_CANDIDATE, "
             "unverified", len(eh2_ncs3) == 1
             and not eh2_ncs3[0]["verified"]
             and eh2_ncs3[0]["disposition"] == "PENDING_NOOP_CANDIDATE")
        case("mixed-verdict: eh2.md rejection still records artifact+"
             "packet_sha256 (attempt recorded)",
             eh2_ncs3[0]["artifact"] and eh2_ncs3[0]["packet_sha256"])
        case("mixed-verdict: events_checked==2, events_confirmed==1",
             hres3.get("events_checked") == 2
             and hres3.get("events_confirmed") == 1)
        case("mixed-verdict: checked/confirmed CANDIDATE counts (3 total, "
             "2 confirmed)",
             hres3["checked"] == 3 and hres3["confirmed"] == 2)

        # ============================================= absorption-verify
        # (spec sec.7 full VERIFY; amendment 2026-07-05): one packet per
        # triggered T1 view, additive after the no-op union packets above.
        os.makedirs(os.path.join(base, "wiki", "abs"), exist_ok=True)
        os.makedirs(os.path.join(base, "raw", "abs"), exist_ok=True)
        av_view_text = (
            "---\ntitle: AV\n---\n"
            "# --- derivation (engine-managed; strip region) ---\n"
            "schema_version: 3.2\nview: topic\nsummary: \"AV\"\n"
            "entities: []\nstatus: active\ntier: T1\n"
            "consumed_status: legacy-assumed\norigin_max: human\n"
            "subscribes:\n  entities: []\n  corpus: []\nbundle: []\n"
            "verified: null\n"
            "# --- /derivation ---\n\n## Intro\noriginal body\n")
        open(os.path.join(base, "wiki", "abs", "av.md"), "w",
             newline="\n").write(av_view_text)
        open(os.path.join(base, "raw", "abs", "ea1.md"), "w",
             newline="\n").write("absorb fact one\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "av fixture"],
                       capture_output=True)

        class AbsorbBackendAV(FixtureAbsorbBackend):
            def absorb(self, view_rel, view_text, events):
                new = view_text.rstrip("\n") + "\n\n## Absorbed\nfact one\n"
                return {"new_text": new,
                        "manifest": [{"event": "raw/abs/ea1.md",
                                     "section": "Absorbed"}],
                        "corpus_support": [
                            {"artifact": "raw/abs/ea1.md",
                             "artifact_sha256": _sha256(
                                 "absorb fact one\n"),
                             "support_lines": ["absorb fact one"]},
                        ], "noops": []}

        av_plan = {"items": [{"view": "wiki/abs/av.md",
                              "events": ["raw/abs/ea1.md"],
                              "event_class": {"raw/abs/ea1.md": {
                                  "class": "t3", "origin": "explicit"}}}]}
        av_res = run(base, av_plan, AbsorbBackendAV())

        class _GoodAttestBackend:
            def __init__(self, confirm=True):
                self.calls = []
                self.confirm = confirm

            def verify(self, packet):
                self.calls.append(packet)
                return {"verdict": "confirmed" if self.confirm else "rejected",
                        "reason": "fixture-absorb", "uncertainty": "confident",
                        "verifier": {"vendor": "openai", "model": "gpt-5.5"},
                        "substrate": {
                            "verifier_vendor": "openai",
                            "verifier_model_id": "gpt-5.5",
                            "absorb_vendor": "anthropic",
                            "absorb_model_id": "claude-opus-4-8",
                            "substrate_source": "invocation-metadata"}}

        av_backend1 = _GoodAttestBackend(confirm=True)
        av_vres1 = verify_run(base, av_res["seq"], av_backend1)
        case("absorption-verify: additive keys present on the return shape",
             av_vres1.get("absorption_checked") == 1
             and av_vres1.get("absorption_confirmed") == 1)
        av_packet1 = av_backend1.calls[0]
        case("absorption-verify: header format seq<N>-v<M>",
             av_packet1.startswith(
                 "# ABSORPTION VERIFY PACKET seq%d-v0" % av_res["seq"]))
        case("absorption-verify: exactly one CLAIM line",
             sum(1 for ln in av_packet1.splitlines()
                 if ln.startswith("CLAIM: ")) == 1)
        case("absorption-verify: CLAIM text matches the amendment verbatim "
             "shape (view/events named)",
             re.search(r"^CLAIM: view wiki/abs/av\.md at post-absorb state "
                       r"faithfully absorbs events raw/abs/ea1\.md: every "
                       r"manifest claim is supported by the absorbed "
                       r"events; every load-bearing claim in the events is "
                       r"represented or implied in the view \(compression "
                       r"and paraphrase are permitted -- the "
                       r"represent-or-imply bar of the F13 precision "
                       r"amendment, NOT verbatim reproduction\); and the "
                       r"cumulative diff shown contains no change "
                       r"unaccounted for by these events\.$",
                       av_packet1, re.M) is not None)
        _STRUCTURAL_HEADERS = ("## CUMULATIVE DIFF SINCE LAST VERIFIED",
                              "## FULL VIEW BODY (POST-ABSORB)",
                              "## ABSORBED EVENT:", "## MANIFEST CLAIMS",
                              "## CORPUS EXCERPTS",
                              "## ROUTING CENSUS (F15)")
        sec_order = [ln for ln in av_packet1.splitlines()
                    if any(ln.startswith(h) for h in _STRUCTURAL_HEADERS)]
        case("absorption-verify: sections 1-4 in order (diff, full body, "
             "absorbed event, manifest claims)",
             sec_order[:4] == ["## CUMULATIVE DIFF SINCE LAST VERIFIED",
                               "## FULL VIEW BODY (POST-ABSORB)",
                               "## ABSORBED EVENT: raw/abs/ea1.md",
                               "## MANIFEST CLAIMS"])
        case("absorption-verify: routing census section present (F15)",
             "## ROUTING CENSUS (F15)" in av_packet1)
        case("absorption-verify: FULL section order 1-6 per the amendment "
             "-- CORPUS EXCERPTS (F16, sec.5) strictly BEFORE ROUTING "
             "CENSUS (F15, sec.6), all six headers present at strictly "
             "increasing positions",
             [av_packet1.index(h) for h in
              ("## CUMULATIVE DIFF SINCE LAST VERIFIED",
               "## FULL VIEW BODY (POST-ABSORB)",
               "## ABSORBED EVENT:", "## MANIFEST CLAIMS",
               "## CORPUS EXCERPTS (F16 mechanically-resolved",
               "## ROUTING CENSUS (F15)")]
             == sorted(av_packet1.index(h) for h in
                       ("## CUMULATIVE DIFF SINCE LAST VERIFIED",
                        "## FULL VIEW BODY (POST-ABSORB)",
                        "## ABSORBED EVENT:", "## MANIFEST CLAIMS",
                        "## CORPUS EXCERPTS (F16 mechanically-resolved",
                        "## ROUTING CENSUS (F15)")))
        case("absorption-verify: never-verified diff base is the "
             "pre-first-absorb body (original body visible in the diff "
             "section, not just the post-absorb body)",
             "original body" in av_packet1.split(
                 "## CUMULATIVE DIFF SINCE LAST VERIFIED", 1)[1].split(
                 "## FULL VIEW BODY", 1)[0])
        case("absorption-verify: full post-absorb body section carries the "
             "new content", "fact one" in av_packet1.split(
                 "## FULL VIEW BODY (POST-ABSORB)", 1)[1])
        case("absorption-verify: absorbed event section carries the full "
             "event body", "absorb fact one" in av_packet1)
        case("absorption-verify: manifest claims section carries the "
             "verbatim manifest row",
             "Absorbed" in av_packet1.split("## MANIFEST CLAIMS", 1)[1])
        case("absorption-verify HAS-EXCERPTS: section 5 header present and "
             "carries the resolved corpus_support excerpt (2026-07-06 "
             "categorical-sections amendment)",
             "## CORPUS EXCERPTS (F16 mechanically-resolved" in av_packet1
             and "absorb fact one" in av_packet1.split(
                 "## CORPUS EXCERPTS (F16 mechanically-resolved", 1)[1].split(
                 "## ROUTING CENSUS", 1)[0])

        # CONFIRM stamping: derivation verified: block populated from the
        # verdict's OWN substrate (F17 attestation-derived), never an
        # orchestrator string; journal gains absorption_verified[].
        av_view_path = os.path.join(base, "wiki", "abs", "av.md")
        av_body_after = open(av_view_path, encoding="utf-8").read()
        av_deriv = asm.parse_derivation(av_body_after)
        case("absorption-verify CONFIRM: verified: stamped status=passed "
             "via parse_derivation (assemble.py's own reader)",
             av_deriv["has_block"] and av_deriv["consumed_status"]
             == "legacy-assumed")
        case("absorption-verify CONFIRM: verified_at present",
             re.search(r"(?m)^\s*at: \S", av_body_after) is not None)
        case("absorption-verify CONFIRM: verifier/absorb vendor+model_id "
             "match the verdict's substrate block EXACTLY (never an "
             "orchestrator-supplied string)",
             "verifier_vendor: openai" in av_body_after
             and "verifier_model_id: gpt-5.5" in av_body_after
             and "absorb_vendor: anthropic" in av_body_after
             and "absorb_model_id: claude-opus-4-8" in av_body_after)
        case("absorption-verify CONFIRM: packet_hash + artifact stamped",
             ("packet_hash: %s" % _sha256(av_packet1)) in av_body_after
             and "artifact: receipts/verify/absorb-seq%d-v0.json"
             % av_res["seq"] in av_body_after)
        case("absorption-verify CONFIRM: non-derivation body content "
             "untouched by the stamp",
             "## Absorbed" in av_body_after and "fact one" in av_body_after)
        av_vrec1 = json.load(open(os.path.join(core.journal_dir(base),
                                               "%d.json" % av_vres1["seq"]),
                                  encoding="utf-8"))
        case("absorption-verify CONFIRM: journal record carries "
             "absorption_verified[] with the documented fields",
             len(av_vrec1.get("absorption_verified", [])) == 1
             and av_vrec1["absorption_verified"][0]["view"]
                 == "wiki/abs/av.md"
             and av_vrec1["absorption_verified"][0]["events"]
                 == ["raw/abs/ea1.md"]
             and av_vrec1["absorption_verified"][0]["packet_sha256"]
                 == _sha256(av_packet1)
             and av_vrec1["absorption_verified"][0]["view_sha256"]
                 == _sha256(av_body_after))
        case("v3.0-71: a confirm on a PRE-PROVENANCE region (no minted_by) "
             "advances nothing -- consumed_status stays legacy-assumed and "
             "the journal entry carries NO consumed_status_advanced key "
             "(additive field, absent where the advance did not fire)",
             "consumed_status_advanced"
             not in av_vrec1["absorption_verified"][0]
             and "minted_by" not in av_body_after)
        av_probs, _ = crd.check_acc4(base, av_vres1["sha"])
        case("absorption-verify CONFIRM: produced verify commit passes "
             "check-run-diff (derivation-only stamp exemption)",
             av_probs == [])

        # already-verified-no-new-absorb: re-running verify_run over the
        # SAME compile seq again must NOT re-trigger (idempotent -- the
        # view's last-verified seq now == this compile seq, not less than).
        av_vres_again = verify_run(base, av_res["seq"], _GoodAttestBackend())
        case("absorption-verify: already-verified-no-new-absorb -> no "
             "re-trigger on a second verify_run over the same compile seq",
             av_vres_again.get("absorption_checked") == 0
             and av_vres_again.get("absorption_confirmed") == 0)

        # non-confirm -> nothing flips, nothing stamped, attempt recorded
        av_res2 = run(base, av_plan, AbsorbBackendAV())
        av_backend_reject = _GoodAttestBackend(confirm=False)
        av_vres2 = verify_run(base, av_res2["seq"], av_backend_reject)
        case("absorption-verify non-confirm: absorption_confirmed == 0, "
             "absorption_checked == 1",
             av_vres2.get("absorption_checked") == 1
             and av_vres2.get("absorption_confirmed") == 0)
        av_vrec2 = json.load(open(os.path.join(core.journal_dir(base),
                                               "%d.json" % av_vres2["seq"]),
                                  encoding="utf-8"))
        case("absorption-verify non-confirm: NO absorption_verified[] entry "
             "(nothing flips, nothing stamped)",
             not av_vrec2.get("absorption_verified"))
        case("absorption-verify non-confirm: attempt recorded in "
             "absorption_verify_attempts[] with artifact + packet_sha256 + "
             "reason",
             len(av_vrec2.get("absorption_verify_attempts", [])) == 1
             and av_vrec2["absorption_verify_attempts"][0]["artifact"]
             and av_vrec2["absorption_verify_attempts"][0]["packet_sha256"]
             and av_vrec2["absorption_verify_attempts"][0]["reason"])
        av_body_unchanged = open(av_view_path, encoding="utf-8").read()
        case("absorption-verify non-confirm: view body's verified: block "
             "STILL reflects the earlier CONFIRM (non-confirm never "
             "touches the stamp)",
             "verifier_model_id: gpt-5.5" in av_body_unchanged)

        # cumulative-diff-from-pinned-blob correctness: a THIRD absorption
        # onto the same view, now confirmed again -- the cumulative diff
        # must be computed from the LAST-VERIFIED body (av_body_after, the
        # seq-1 stamped state), not from the pre-first-absorb original body
        # and not from an empty diff.
        class AbsorbBackendAV2(FixtureAbsorbBackend):
            def absorb(self, view_rel, view_text, events):
                new = view_text.rstrip("\n") + "\n\n## Absorbed2\nfact two\n"
                return {"new_text": new,
                        "manifest": [{"event": "raw/abs/ea2.md",
                                     "section": "Absorbed2"}],
                        "corpus_support": [], "noops": []}

        open(os.path.join(base, "raw", "abs", "ea2.md"), "w",
             newline="\n").write("absorb fact two\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "ea2 fixture"],
                       capture_output=True)
        av_plan2 = {"items": [{"view": "wiki/abs/av.md",
                               "events": ["raw/abs/ea2.md"],
                               "event_class": {"raw/abs/ea2.md": {
                                   "class": "t3", "origin": "explicit"}}}]}
        # NOTE: av_res2's absorption never confirmed (non-confirm case
        # above), so its last-verified state is STILL av_vres1/seq
        # av_res["seq"] -- this third absorption is triggered again from
        # that same pinned baseline, not from av_res2's unconfirmed body.
        av_res3 = run(base, av_plan2, AbsorbBackendAV2())
        av_backend3 = _GoodAttestBackend(confirm=True)
        av_vres3 = verify_run(base, av_res3["seq"], av_backend3)
        av_packet3 = av_backend3.calls[0]
        diff_section3 = av_packet3.split(
            "## CUMULATIVE DIFF SINCE LAST VERIFIED", 1)[1].split(
            "## FULL VIEW BODY", 1)[0]
        # NOTE ON FIXTURE TIMELINE (2026-07-06 recovery-fix amendment):
        # av_res2's absorption (the one whose VERIFY was rejected above)
        # still WROTE a real second "## Absorbed\nfact one" block to the
        # view -- absorb and verify are separate gates; a rejected verify
        # never un-writes already-absorbed content, it only withholds the
        # stamp (see run()'s unconditional content write vs verify_run's
        # conditional stamp). So the TRUE diff from the av_vres1-pinned
        # baseline to the current (post-av_res3) body legitimately contains
        # BOTH that duplicate "## Absorbed\nfact one" block (real content
        # change since the pin, from av_res2) AND the new "## Absorbed2\n
        # fact two" block (from av_res3) -- asserted here as the EXACT
        # expected hunk body, not a substring/presence check (the r2
        # cross-vendor conformance catch: a substring check can't tell a
        # correctly-recovered diff from a UNAVAILABLE-fallback's raw body
        # dump, since both happen to contain the word "fact one" somewhere).
        expected_hunk_lines = [
            " ", " ## Absorbed", " fact one", "+", "+## Absorbed",
            "+fact one", "+", "+## Absorbed2", "+fact two"]
        diff_body_lines = [ln for ln in diff_section3.splitlines()
                           if ln and ln[0] in " +-" and not
                           ln.startswith(("---", "+++"))]
        case("cumulative-diff-from-pinned-blob: diff is a REAL git-recovered "
             "diff (has a hunk header @@, not the UNAVAILABLE fallback's "
             "raw current-body dump)",
             "@@" in diff_section3
             and "CUMULATIVE DIFF UNAVAILABLE" not in diff_section3)
        case("cumulative-diff-from-pinned-blob: diff hunk body is EXACTLY "
             "the expected lines (both the real av_res2 content-change "
             "since the pin AND the new av_res3 content), not merely a "
             "substring match that a broken recovery could accidentally "
             "satisfy via the raw-body fallback",
             diff_body_lines[-len(expected_hunk_lines):]
             == expected_hunk_lines)
        case("cumulative-diff-from-pinned-blob: confirmed again, new "
             "view_sha256 pin advances past the first stamp",
             av_vres3.get("absorption_confirmed") == 1)

        # NO-EXCERPTS fixture (2026-07-06 categorical-sections amendment,
        # r4 conformance catch): av_res3/av_packet3 absorbed raw/abs/ea2.md
        # via AbsorbBackendAV2, whose corpus_support is [] -- this event
        # carries no resolvable corpus_support entries at all. The amendment
        # mandates sections 1-6 categorically on EVERY absorption-verify
        # packet, so section 5's header must still be present here, with the
        # explicit none-line as its body -- never a silently-omitted
        # section. Scope: ABSORPTION packets only; the no-op union packet's
        # own F16 section (a different contract, LLM-6 integration) keeps
        # its additive/omit-when-empty behavior and is untouched.
        case("absorption-verify NO-EXCERPTS: section 5 header present even "
             "though this view's absorbed events carry no corpus_support "
             "entries (categorical sections, never silently omitted)",
             "## CORPUS EXCERPTS (F16 mechanically-resolved" in av_packet3)
        case("absorption-verify NO-EXCERPTS: body is the single explicit "
             "none-line, not an empty/missing section",
             av_packet3.split(
                 "## CORPUS EXCERPTS (F16 mechanically-resolved", 1)[1]
             .split("## ROUTING CENSUS", 1)[0].strip().splitlines()[-1]
             == "(no corpus_support entries for these events)")
        case("absorption-verify NO-EXCERPTS: FULL section order 1-6 still "
             "strictly increasing with section 5 present-but-empty (the "
             "same categorical order the has-excerpts case holds)",
             [av_packet3.index(h) for h in
              ("## CUMULATIVE DIFF SINCE LAST VERIFIED",
               "## FULL VIEW BODY (POST-ABSORB)",
               "## ABSORBED EVENT:", "## MANIFEST CLAIMS",
               "## CORPUS EXCERPTS (F16 mechanically-resolved",
               "## ROUTING CENSUS (F15)")]
             == sorted(av_packet3.index(h) for h in
                       ("## CUMULATIVE DIFF SINCE LAST VERIFIED",
                        "## FULL VIEW BODY (POST-ABSORB)",
                        "## ABSORBED EVENT:", "## MANIFEST CLAIMS",
                        "## CORPUS EXCERPTS (F16 mechanically-resolved",
                        "## ROUTING CENSUS (F15)")))

        # verify_commit annotation + git-show recovery, checked directly
        # (2026-07-06 recovery-fix amendment): av_vrec1's stamp names a
        # real, reachable commit whose OWN content at that path hashes to
        # exactly the journaled view_sha256 pin -- the actual mechanism
        # _recover_verified_body uses, exercised here at the unit level
        # rather than only inferred from packet text.
        av_vrec1_commit = av_vrec1["absorption_verified"][0].get(
            "verify_commit")
        case("verify_commit annotation: present on the journaled stamp, a "
             "real non-empty commit sha",
             isinstance(av_vrec1_commit, str) and len(av_vrec1_commit) == 40)
        recovered, why = _recover_verified_body(
            base, "wiki/abs/av.md", av_vrec1_commit,
            av_vrec1["absorption_verified"][0]["view_sha256"])
        case("verify_commit annotation: git-show against the stored commit "
             "recovers a body whose sha256 matches the journaled pin "
             "exactly (the real recovery mechanism, not packet-file "
             "parsing)",
             why is None and recovered is not None
             and _sha256(recovered)
             == av_vrec1["absorption_verified"][0]["view_sha256"])

        # NEGATIVE fixture (r2-mandated): corrupt the stored pin so the
        # recovered content's sha256 can never match -- recovery must fail
        # HONEST (explicit reason), never silently accept a mismatched body.
        bad_recovered, bad_why = _recover_verified_body(
            base, "wiki/abs/av.md", av_vrec1_commit, "0" * 64)
        case("NEGATIVE: corrupted stored pin (sha256 mismatch) -> recovery "
             "refuses, names the mismatch explicitly, never returns a body",
             bad_recovered is None and "does not match" in bad_why)

        # NEGATIVE fixture: verify_commit pointing at a commit that does not
        # carry the view at all (simulates a missing/unreachable commit ref)
        # -> recovery refuses, names the git failure, never crashes/guesses.
        missing_commit = "0" * 40
        miss_recovered, miss_why = _recover_verified_body(
            base, "wiki/abs/av.md", missing_commit,
            av_vrec1["absorption_verified"][0]["view_sha256"])
        case("NEGATIVE: verify_commit names an unreachable/missing commit -> "
             "recovery refuses, names the git failure honestly",
             miss_recovered is None and "git show" in miss_why)

        # NEGATIVE fixture, full packet-level (r2-mandated): drop the
        # verify_commit field entirely (simulating a pre-amendment journal
        # record) and confirm the packet's diff section carries the EXACT
        # UNAVAILABLE marker text, never a silent empty/wrong diff. This
        # must corrupt av_vres3's record specifically -- that is the
        # CURRENT last-verified stamp for wiki/abs/av.md at this point in
        # the fixture timeline ("last stamp by seq wins": av_vres3 confirmed
        # and so its stamp supersedes av_vrec1's for trigger/diff purposes),
        # not av_vrec1's now-superseded one.
        av_vrec3_path = os.path.join(core.journal_dir(base),
                                     "%d.json" % av_vres3["seq"])
        av_vrec3_ondisk = json.load(open(av_vrec3_path, encoding="utf-8"))
        av_vrec3_ondisk["absorption_verified"][0].pop("verify_commit")
        with open(av_vrec3_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(av_vrec3_ondisk, fh, indent=1, sort_keys=True)

        open(os.path.join(base, "raw", "abs", "ea3.md"), "w",
             newline="\n").write("absorb fact three\n")
        subprocess.run(["git", "-C", base, "add", "-A"],
                       capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "ea3 fixture"],
                       capture_output=True)
        av_plan4 = {"items": [{"view": "wiki/abs/av.md",
                               "events": ["raw/abs/ea3.md"],
                               "event_class": {"raw/abs/ea3.md": {
                                   "class": "t3", "origin": "explicit"}}}]}

        class AbsorbBackendAV3(FixtureAbsorbBackend):
            def absorb(self, view_rel, view_text, events):
                new = view_text.rstrip("\n") + "\n\n## Absorbed3\nfact three\n"
                return {"new_text": new,
                        "manifest": [{"event": "raw/abs/ea3.md",
                                     "section": "Absorbed3"}],
                        "corpus_support": [], "noops": []}

        av_res4 = run(base, av_plan4, AbsorbBackendAV3())
        av_backend4 = _GoodAttestBackend(confirm=True)
        av_vres4 = verify_run(base, av_res4["seq"], av_backend4)
        av_packet4 = av_backend4.calls[0]
        diff_section4 = av_packet4.split(
            "## CUMULATIVE DIFF SINCE LAST VERIFIED", 1)[1].split(
            "## FULL VIEW BODY", 1)[0]
        case("NEGATIVE (packet-level): missing verify_commit on the prior "
             "stamp -> packet's diff section carries the EXACT "
             "'CUMULATIVE DIFF UNAVAILABLE' marker, loudly, never a silent "
             "empty or fabricated diff",
             "CUMULATIVE DIFF UNAVAILABLE: last-verified body at seq %d "
             "could not be recovered -- no verify_commit recorded for this "
             "view's last stamp" % av_vres3["seq"] in diff_section4)
        case("NEGATIVE (packet-level): even with the diff unavailable, "
             "verify still proceeds and can CONFIRM (the marker degrades "
             "the diff section only, never crashes the pass)",
             av_vres4.get("absorption_confirmed") == 1)

        # mixed pass: no-op union candidates AND an absorption view in the
        # SAME verify_run call (same journal record) -- both mechanisms
        # fire correctly side by side.
        os.makedirs(os.path.join(base, "wiki", "mix"), exist_ok=True)
        os.makedirs(os.path.join(base, "raw", "mix"), exist_ok=True)
        open(os.path.join(base, "wiki", "mix", "vm.md"), "w",
             newline="\n").write("# VM\nalready represented\n")
        open(os.path.join(base, "raw", "mix", "em.md"), "w",
             newline="\n").write("mix noop event\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "mix fixture"],
                       capture_output=True)

        def _mixed_rec():
            rec = core.minimal_record(
                "compile", _git(base, "rev-parse", "HEAD").strip())
            rec["noop_candidates"] = [
                {"view": "wiki/mix/vm.md", "event": "raw/mix/em.md",
                 "verified": False, "disposition": "PENDING_NOOP_CANDIDATE",
                 "event_class": "t1", "event_class_origin": "explicit",
                 "artifact": "", "packet_sha256": "",
                 "justification": {"event_sha256": "", "view_sha256": "",
                                   "note": "mix noop"}}]
            rec["absorbed"] = [{"view": "wiki/abs/av.md",
                               "events": ["raw/abs/ea1.md"],
                               "pre_blob": "", "post_blob": "x",
                               "manifest": [{"event": "raw/abs/ea1.md",
                                            "section": "Absorbed"}],
                               "corpus_support": []}]
            rec["run_window"] = {"start": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                 "end": time.strftime("%Y-%m-%dT%H:%M:%S")}
            return core.append_record(base, rec)

        mixed_seq, _mp = _mixed_rec()
        mixed_backend = _GoodAttestBackend(confirm=True)
        mixed_vres = verify_run(base, mixed_seq, mixed_backend)
        case("mixed pass: no-op union path fires (1 checked/confirmed)",
             mixed_vres["checked"] == 1 and mixed_vres["confirmed"] == 1)
        case("mixed pass: absorption-verify ALSO fires in the SAME run "
             "(re-triggered: av.md was absorbed again at this seq, > its "
             "last-verified seq)",
             mixed_vres.get("absorption_checked") == 1
             and mixed_vres.get("absorption_confirmed") == 1)
        mixed_noop_calls = [p for p in mixed_backend.calls
                           if p.startswith("# NOOP VERIFY PACKET")]
        mixed_absorb_calls = [p for p in mixed_backend.calls
                              if p.startswith("# ABSORPTION VERIFY PACKET")]
        case("mixed pass: both packet kinds distinguishable and both fired",
             len(mixed_noop_calls) == 1 and len(mixed_absorb_calls) == 1)

        # CONTENT-1 deletion floor refusals
        class DeleterBackend(FixtureAbsorbBackend):
            def absorb(self, view_rel, view_text, events):
                out = super().absorb(view_rel, view_text, events)
                if out["new_text"]:
                    out["new_text"] = out["new_text"].replace("## Intro\n", "")
                return out

        class ShrinkerBackend(FixtureAbsorbBackend):
            def absorb(self, view_rel, view_text, events):
                heads = [ln for ln in view_text.splitlines()
                         if ln.lstrip().startswith("#")]
                return {"new_text": "\n".join(heads) + "\n", "manifest": [],
                        "corpus_support": [], "noops": []}

        open(os.path.join(base, "raw", "e9.md"), "w", newline="\n").write(
            "fact nine\n")
        plan9 = {"items": [{"view": "wiki/b.md", "events": ["raw/e9.md"],
                            "event_class": {"raw/e9.md": {
                                "class": "t3", "origin": "explicit"}}}]}
        try:
            run(base, plan9, DeleterBackend())
            case("CONTENT-1: dropped heading refused", False)
        except ValidationError as e:
            case("CONTENT-1: dropped heading refused", "heading" in str(e))
        try:
            run(base, plan9, ShrinkerBackend())
            case("CONTENT-1: >30% shrink refused", False)
        except ValidationError as e:
            case("CONTENT-1: >30% shrink refused", "shrank" in str(e))

        # ADR #11 Release 2 (v3.0.50): the retirements block is immutable to absorb
        RB = ("# --- derivation (engine-managed; strip region) ---\n# --- retirements\n"
              '# {"seq":2,"pre_hash":"a","post_hash":"b","span":[3,9],"stub":[3,5],"shift":-4,'
              '"target":"wiki/cold/b/intro--x.md","mode":"cold","title":"Intro","i":0}\n'
              "# --- /retirements\n# --- /derivation ---\n")
        rb_old = "# B\n\n## Intro\nworld\n\n" + RB
        ok_out = {"new_text": rb_old.replace("world", "world and more"), "manifest": [], "corpus_support": []}
        try:
            validate_absorb_output(base, "wiki/b.md", rb_old, dict(ok_out, manifest=[]), ["raw/e9.md"])
            case("retirements block: an absorb that leaves the block byte-identical passes "
                 "the block rule (other rules decide the rest)", True)
        except ValidationError as e:
            case("retirements block: an absorb that leaves the block byte-identical passes "
                 "the block rule (other rules decide the rest)", "engine-owned" not in str(e))
        for label, mutate in (("edit", lambda t: t.replace('"shift":-4', '"shift":-3')),
                              ("drop", lambda t: t.replace(RB, "# --- derivation (engine-managed; strip region) ---\n# --- /derivation ---\n")),
                              ("drop-one-row", lambda t: t.replace("# --- retirements\n# {", "# --- retirements\n# --- /retirements\n# {", 1))):
            try:
                validate_absorb_output(base, "wiki/b.md", rb_old,
                                       {"new_text": mutate(rb_old), "manifest": [], "corpus_support": []},
                                       ["raw/e9.md"])
                case("retirements block: absorb that would %s the block refused" % label, False)
            except ValidationError as e:
                case("retirements block: absorb that would %s the block refused" % label,
                     "engine-owned" in str(e))
        try:
            validate_absorb_output(base, "wiki/b.md", "# B\n\n## Intro\nworld\n",
                                   {"new_text": rb_old, "manifest": [], "corpus_support": []}, ["raw/e9.md"])
            case("retirements block: an absorb that MINTS a block where none existed refused", False)
        except ValidationError as e:
            case("retirements block: an absorb that MINTS a block where none existed refused",
                 "mint" in str(e))

        # 2026-07-05 red-team catch: heading DEMOTION (## X -> ### X) slipped
        # the substring-containment check ("## X" in "### X" is True)
        class DemoterBackend(FixtureAbsorbBackend):
            def absorb(self, view_rel, view_text, events):
                out = super().absorb(view_rel, view_text, events)
                if out["new_text"]:
                    out["new_text"] = out["new_text"].replace("## Intro",
                                                              "### Intro")
                return out

        try:
            run(base, plan9, DemoterBackend())
            case("CONTENT-1: heading demotion (## -> ###) refused", False)
        except ValidationError as e:
            case("CONTENT-1: heading demotion (## -> ###) refused",
                 "heading" in str(e))

        # ABSORB-contract nit (2026-07-05): last_updated must never backdate;
        # new value = max(existing value, event date). Exercised directly
        # against validate_absorb_output (unit-level, not via run()) so each
        # case is isolated from manifest/corpus_support plumbing.
        lu_old = "# LU\nlast_updated: 2026-06-01\n\n## Intro\nhello there\n"

        def _lu_out(new_text, sections=()):
            manifest = [{"event": "e", "section": s} for s in sections]
            return {"new_text": new_text, "manifest": manifest,
                    "corpus_support": [], "noops": []}

        _lu_events = {"e": "body"}

        try:
            validate_absorb_output(base, "wiki/lu.md", lu_old,
                                   _lu_out("# LU\nlast_updated: 2026-05-01\n\n"
                                           "## Intro\nhello there\n",
                                           ["LU", "Intro"]), _lu_events)
            case("last_updated backdated refused", False)
        except ValidationError as e:
            case("last_updated backdated refused",
                 "backdated" in str(e) and "2026-06-01" in str(e)
                 and "2026-05-01" in str(e))

        try:
            r = validate_absorb_output(
                base, "wiki/lu.md", lu_old,
                _lu_out("# LU\nlast_updated: 2026-06-01\n\n## Intro\n"
                        "hello there again\n", ["Intro"]), _lu_events)
            case("last_updated equal to existing passes", r is not None)
        except ValidationError as e:
            case("last_updated equal to existing passes", False)

        try:
            r = validate_absorb_output(
                base, "wiki/lu.md", lu_old,
                _lu_out("# LU\nlast_updated: 2026-07-05\n\n## Intro\n"
                        "hello there later\n", ["LU", "Intro"]), _lu_events)
            case("last_updated later than existing passes", r is not None)
        except ValidationError as e:
            case("last_updated later than existing passes", False)

        try:
            r = validate_absorb_output(
                base, "wiki/lu.md", lu_old,
                _lu_out("# LU\n\n## Intro\nhello there, no lu field at all "
                        "here\n", ["LU", "Intro"]), _lu_events)
            case("last_updated missing on new side: no check, passes",
                 r is not None)
        except ValidationError as e:
            case("last_updated missing on new side: no check, passes", False)

        try:
            validate_absorb_output(
                base, "wiki/lu.md", lu_old,
                _lu_out("# LU\nlast_updated: not-a-date\n\n## Intro\n"
                        "hello there unparseable\n", ["LU", "Intro"]),
                _lu_events)
            case("last_updated unparseable refused fail-closed", False)
        except ValidationError as e:
            case("last_updated unparseable refused fail-closed",
                 "unparseable" in str(e) and "not-a-date" in str(e))

        # validation refusals happen BEFORE journal/commit
        open(os.path.join(base, "raw", "e4.md"), "w", newline="\n").write(
            "fact four\n")
        plan2 = {"items": [{"view": "wiki/a.md", "events": ["raw/e4.md"],
                            "event_class": {"raw/e4.md": {
                                "class": "t3", "origin": "explicit"}}}]}
        pre_chain = core.check_chain(base)
        try:
            run(base, plan2, BrokenManifestBackend())
            case("broken manifest refused pre-journal", False)
        except ValidationError as e:
            case("broken manifest refused pre-journal",
                 "unchanged section" in str(e))
        case("refusal journaled NOTHING (chain length unchanged)",
             core.check_chain(base) == pre_chain)
        case("refusal released the lock",
             not os.path.isfile(core.lock_path(base)))
        try:
            run(base, plan2, FabricatedSupportBackend())
            case("fabricated corpus_support refused", False)
        except ValidationError as e:
            case("fabricated corpus_support refused",
                 "not an exact line" in str(e))

        # v3.0-22 regression: a mid-plan ValidationError must leave NO
        # earlier item's view written to disk -- not even uncommitted.
        # Pre-fix, the per-item loop wrote each view AS it validated, so
        # item 1's edit landed on disk before item 2's failure refused the
        # whole run (live batch-10 event 10: driver had to `git checkout --`
        # the file). This backend absorbs FIRST_VIEW normally (passes
        # validate_absorb_output) but claims a phantom section -- same
        # mismatch BrokenManifestBackend exercises above -- on any OTHER
        # view, so item 2 is the one refused.
        class SecondItemBrokenBackend(FixtureAbsorbBackend):
            def __init__(self, first_view):
                self.first_view = first_view

            def absorb(self, view_rel, view_text, events):
                out = super().absorb(view_rel, view_text, events)
                if view_rel != self.first_view and out["new_text"] is not None:
                    out["manifest"].append({"event": sorted(events)[0],
                                            "section": "Phantom Section"})
                return out

        before_a = open(os.path.join(base, "wiki", "a.md"),
                       encoding="utf-8").read()
        pre_chain2 = core.check_chain(base)
        plan3 = {"items": [
            {"view": "wiki/a.md", "events": ["raw/e4.md"],
             "event_class": {"raw/e4.md": {"class": "t3",
                                           "origin": "explicit"}}},
            {"view": "wiki/b.md", "events": ["raw/e4.md"],
             "event_class": {"raw/e4.md": {"class": "t3",
                                           "origin": "explicit"}}},
        ]}
        try:
            run(base, plan3, SecondItemBrokenBackend("wiki/a.md"))
            case("v3.0-22: 2-item plan, item 2 broken, whole run refused",
                False)
        except ValidationError as e:
            case("v3.0-22: 2-item plan, item 2 broken, whole run refused",
                "unchanged section" in str(e))
        after_a = open(os.path.join(base, "wiki", "a.md"),
                      encoding="utf-8").read()
        case("v3.0-22: item 1's already-validated view was NEVER written to "
            "disk after item 2's refusal (content byte-identical)",
             after_a == before_a)
        case("v3.0-22: refusal journaled nothing (chain length unchanged)",
             core.check_chain(base) == pre_chain2)
        case("v3.0-22: refusal left wiki/a.md with no working-tree diff at "
            "all (not even an uncommitted mutation)",
             "wiki/a.md" not in subprocess.run(
                 ["git", "-C", base, "status", "--porcelain"],
                 capture_output=True, text=True).stdout)

        # v3.0-22 structural refusal: two plan items targeting the SAME view
        # path are refused at plan-intake time (pre-absorb, pre-journal) --
        # under the two-phase discipline the later item would absorb against
        # the view's ORIGINAL on-disk text and its deferred write would
        # silently clobber the earlier one. Joint citation in ONE item is
        # the supported shape.
        before_a2 = open(os.path.join(base, "wiki", "a.md"),
                        encoding="utf-8").read()
        pre_chain3 = core.check_chain(base)
        plan_dup = {"items": [
            {"view": "wiki/a.md", "events": ["raw/e4.md"],
             "event_class": {"raw/e4.md": {"class": "t3",
                                           "origin": "explicit"}}},
            {"view": "wiki/a.md", "events": ["raw/e1.md"],
             "event_class": {"raw/e1.md": {"class": "t3",
                                           "origin": "explicit"}}},
        ]}
        try:
            run(base, plan_dup, FixtureAbsorbBackend())
            case("v3.0-22: duplicate view target across plan items refused",
                False)
        except ValidationError as e:
            case("v3.0-22: duplicate view target across plan items refused",
                "duplicate view target" in str(e)
                 and "wiki/a.md" in str(e))
        case("v3.0-22: duplicate-view refusal wrote nothing (view content "
            "byte-identical)",
             open(os.path.join(base, "wiki", "a.md"),
                  encoding="utf-8").read() == before_a2)
        case("v3.0-22: duplicate-view refusal journaled nothing (chain "
            "length unchanged)",
             core.check_chain(base) == pre_chain3)

        # v3.0-22 canonicalized dedup KEY: aliased spellings of one view
        # path (dot-segment, backslash separators, case variants) must not
        # evade the refusal -- the write phase resolves every one of them
        # to the SAME file (see the seen_views comment in run()).
        for alias in ("wiki/./a.md", "wiki\\a.md", "wiki/A.md"):
            plan_alias = {"items": [
                {"view": "wiki/a.md", "events": ["raw/e4.md"],
                 "event_class": {"raw/e4.md": {"class": "t3",
                                               "origin": "explicit"}}},
                {"view": alias, "events": ["raw/e1.md"],
                 "event_class": {"raw/e1.md": {"class": "t3",
                                               "origin": "explicit"}}},
            ]}
            try:
                run(base, plan_alias, FixtureAbsorbBackend())
                case("v3.0-22: aliased duplicate view %r refused" % alias,
                    False)
            except ValidationError as e:
                case("v3.0-22: aliased duplicate view %r refused" % alias,
                    "duplicate view target" in str(e))
        # sanity: two genuinely DIFFERENT views in one plan still pass the
        # dedup and the run completes end-to-end.
        os.makedirs(os.path.join(base, "wiki", "dup"), exist_ok=True)
        for v in ("wiki/dup/da.md", "wiki/dup/db.md"):
            open(os.path.join(base, v.replace("/", os.sep)), "w",
                 newline="\n").write("# %s\n\n## Intro\nx\n"
                                     % os.path.basename(v)[:-3].upper())
        subprocess.run(["git", "-C", base, "add", "wiki/dup"],
                       capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "dup fixture"],
                       capture_output=True)
        plan_distinct = {"items": [
            {"view": "wiki/dup/da.md", "events": ["raw/e4.md"],
             "event_class": {"raw/e4.md": {"class": "t3",
                                           "origin": "explicit"}}},
            {"view": "wiki/dup/db.md", "events": ["raw/e4.md"],
             "event_class": {"raw/e4.md": {"class": "t3",
                                           "origin": "explicit"}}},
        ]}
        res_distinct = run(base, plan_distinct, FixtureAbsorbBackend())
        case("v3.0-22: two genuinely different views still pass the dedup "
            "(run completes, 2 rebuilds)",
             res_distinct["rebuilds"] == 2)

        class StaleShaBackend(FixtureAbsorbBackend):
            def absorb(self, view_rel, view_text, events):
                out = super().absorb(view_rel, view_text, events)
                for cs in out["corpus_support"]:
                    cs["artifact_sha256"] = "0" * 64
                return out
        try:
            run(base, plan2, StaleShaBackend())
            case("stale corpus_support artifact pin refused", False)
        except ValidationError as e:
            case("stale corpus_support artifact pin refused",
                 "stale" in str(e))

        # second run: lock contention honored via core
        _lp, _b = core.acquire_lock(base, "other-run")
        try:
            run(base, plan2, FixtureAbsorbBackend())
            case("run refuses while another holds the lock", False)
        except core.LockHeld:
            case("run refuses while another holds the lock", True)
        core.release_lock(base)

        # circuit breaker
        os.makedirs(os.path.join(base, "wiki", "many"), exist_ok=True)
        items = []
        for i in range(16):
            v = "wiki/many/v%02d.md" % i
            open(os.path.join(base, v.replace("/", os.sep)), "w",
                 newline="\n").write("# V%d\n\n## Intro\nx\n" % i)
            items.append({"view": v, "events": ["raw/e4.md"],
                          "event_class": {"raw/e4.md": {"class": "t3",
                                                        "origin": "explicit"}}})
        subprocess.run(["git", "-C", base, "add", "wiki/many"],
                       capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "many"],
                       capture_output=True)
        try:
            run(base, {"items": items}, FixtureAbsorbBackend())
            case("circuit breaker trips at 15 rebuilds", False)
        except ValidationError as e:
            case("circuit breaker trips at 15 rebuilds",
                 "circuit breaker" in str(e))

        # reconcile flags: entity-pair + shipped-state-no-support
        rows = [{"view": "wiki/a.md", "post_blob": "x",
                 "manifest": [{"event": "e", "section": "Shipped state"}],
                 "corpus_support": []},
                {"view": "wiki/b.md", "post_blob": "y",
                 "manifest": [{"event": "e", "section": "Other"}],
                 "corpus_support": [{"artifact": "raw/e1.md",
                                     "support_lines": []}]}]
        fl = reconcile_flags(rows, {"wiki/a.md": ["stripe"],
                                    "wiki/b.md": ["stripe"]})
        case("reconcile: entity-overlapping changed pair flagged",
             any(f["kind"] == "entity-pair" for f in fl))
        case("reconcile: shipped-state prose without corpus_support flagged",
             any(f["kind"] == "shipped-state-no-support" for f in fl))
        # (b)/(c)/(d): roadmap citation, supersession marker, hub entity
        os.makedirs(os.path.join(base, "wiki", "flight-plans"), exist_ok=True)
        open(os.path.join(base, "wiki", "flight-plans", "fp.md"), "w",
             newline="\n").write("# FP\n| row | raw/e1.md |\n")
        open(os.path.join(base, "raw", "esup.md"), "w", newline="\n").write(
            "---\nsuperseded_by: raw/e2.md\n---\nold\n")
        rows2 = [{"view": "wiki/a.md", "post_blob": "x",
                  "events": ["raw/e1.md", "raw/esup.md"], "manifest": [],
                  "corpus_support": []}]
        hubent = {"wiki/a.md": ["stripe"], "wiki/b.md": ["stripe"],
                  "wiki/c.md": ["stripe"], "wiki/d.md": ["stripe"]}
        rows_hub = [{"view": v, "post_blob": "x", "events": [], "manifest": [],
                     "corpus_support": []} for v in hubent]
        fl2 = reconcile_flags(rows2, {}, repo=base)
        case("reconcile: roadmap row citing an absorbed event flagged",
             any(f["kind"] == "roadmap-cited-source-changed" for f in fl2))
        case("reconcile: supersession marker flagged",
             any(f["kind"] == "supersession-chain" for f in fl2))
        fl3 = reconcile_flags(rows_hub, hubent)
        case("reconcile: hub entity (fan-out >= 4) flagged",
             any(f["kind"] == "hub-entity" for f in fl3))
        # append refuses on a corrupt chain (full pre-check)
        jd2 = core.journal_dir(base)
        seqs_now = sorted(int(f[:-5]) for f in os.listdir(jd2))
        mid = os.path.join(jd2, "%d.json" % seqs_now[0])
        saved = open(mid, "rb").read()
        os.remove(mid)
        try:
            core.append_record(base, core.minimal_record("compile"))
            case("append_record refuses onto a corrupt chain", False)
        except core.JournalViolation:
            case("append_record refuses onto a corrupt chain", True)
        open(mid, "wb").write(saved)
        # F6: judgment-assigned -> lock-class
        case("F6: judgment-assigned class routes lock-class",
             is_lock_class({"class": "t3", "origin": "judgment"})
             and not is_lock_class({"class": "t3", "origin": "explicit"})
             and is_lock_class(None))

        # =========================================================== P5 (C4)
        # pointer-class write ceiling + plan-precedence rule. `base` has no
        # receipts/registrations/ directory anywhere above -- confirmed
        # inert first, then a SEPARATE tempdir builds a real registration
        # chain via regs.append_registration (never touching `base`/the
        # live tree), per the build brief's fixture discipline.

        # --- absent store: seam is inert (regression case) ---
        case("P5 seam: absent receipts/registrations/ -> _load_registration_"
             "seam returns {} (inert)",
             _load_registration_seam(base) == {})
        case("P5 seam: inert map -> check_plan_precedence is a no-op "
             "(never raises)",
             check_plan_precedence(plan2, {}) is None)
        case("P5 seam: inert map -> check_pointer_class_ceiling is a no-op",
             check_pointer_class_ceiling(base, "wiki/a.md", "old\n",
                                         "totally different prose\n",
                                         ["raw/e4.md"], {}) is None)

        # --- unregistered event: unchanged behavior (regression case) ---
        unreg_map = {"raw/other-event.md": dict(
            regs._minimal("raw/other-event.md", event_class="lock",
                          event_class_origin="explicit"), seq=1,
            prev_record_hash=None)}
        case("P5 precedence: event absent from a NON-empty registration map "
             "-> untouched, today's behavior exactly",
             check_plan_precedence(plan2, unreg_map) is None)
        case("P5 ceiling: event absent from a NON-empty registration map "
             "-> ceiling inert for that event",
             check_pointer_class_ceiling(base, "wiki/a.md", "old\n",
                                         "totally different prose\n",
                                         ["raw/e4.md"], unreg_map) is None)

        # --- build a real registration chain in its OWN tempdir (never base) ---
        reg_base = tempfile.mkdtemp(prefix="cv2-regs-")
        try:
            subprocess.run(["git", "-C", reg_base, "init", "-q"],
                           capture_output=True)
            subprocess.run(["git", "-C", reg_base, "config", "user.email",
                            "t@t"], capture_output=True)
            subprocess.run(["git", "-C", reg_base, "config", "user.name",
                            "t"], capture_output=True)
            regs.append_registration(
                reg_base, regs._minimal(
                    "raw/lock-event.md", event_class="lock",
                    event_class_origin="explicit", asserts_corpus_state=False))
            regs.append_registration(
                reg_base, regs._minimal(
                    "raw/judgment-event.md", event_class="t3",
                    event_class_origin="judgment",
                    asserts_corpus_state=False))
            regs.append_registration(
                reg_base, regs._minimal(
                    "receipts/pointer-event.md", event_class="compile",
                    event_class_origin="explicit",
                    asserts_corpus_state=True))
            good_map = regs.load_registrations(reg_base)

            case("P5 fixture chain: 3 registrations loaded",
                 len(good_map) == 3)

            # --- plan-precedence: loosening refused pre-journal ---
            loosen_plan = {"items": [
                {"view": "wiki/a.md", "events": ["raw/lock-event.md"],
                 "event_class": {"raw/lock-event.md":
                                 {"class": "t3", "origin": "explicit"}}}]}
            try:
                check_plan_precedence(loosen_plan, good_map)
                case("P5 precedence: loosening a registered lock-class "
                     "event refused pre-journal", False)
            except ValidationError as e:
                case("P5 precedence: loosening a registered lock-class "
                     "event refused pre-journal",
                     "plan-precedence" in str(e)
                     and "raw/lock-event.md" in str(e)
                     and "lock" in str(e) and "t3" in str(e))

            loosen_judgment_plan = {"items": [
                {"view": "wiki/a.md", "events": ["raw/judgment-event.md"],
                 "event_class": {"raw/judgment-event.md":
                                 {"class": "t3", "origin": "explicit"}}}]}
            try:
                check_plan_precedence(loosen_judgment_plan, good_map)
                case("P5 precedence: loosening a judgment-origin "
                     "registration refused pre-journal", False)
            except ValidationError as e:
                case("P5 precedence: loosening a judgment-origin "
                     "registration refused pre-journal",
                     "raw/judgment-event.md" in str(e))

            # --- plan-precedence: stricter-than-registered passes ---
            strict_plan = {"items": [
                {"view": "wiki/a.md",
                 "events": ["receipts/pointer-event.md"],
                 "event_class": {"receipts/pointer-event.md":
                                 {"class": "lock", "origin": "explicit"}}}]}
            case("P5 precedence: plan stricter than registration passes",
                 check_plan_precedence(strict_plan, good_map) is None)

            # --- plan-precedence: matching passes ---
            match_plan = {"items": [
                {"view": "wiki/a.md", "events": ["raw/lock-event.md"],
                 "event_class": {"raw/lock-event.md":
                                 {"class": "lock", "origin": "explicit"}}}]}
            case("P5 precedence: plan matching the registration passes",
                 check_plan_precedence(match_plan, good_map) is None)

            # --- plan-precedence: judgment-origin PLAN class never counts
            # as a loosening (F6 stays conservative on the plan side too) ---
            judgment_plan_plan = {"items": [
                {"view": "wiki/a.md", "events": ["raw/lock-event.md"],
                 "event_class": {"raw/lock-event.md":
                                 {"class": "t3", "origin": "judgment"}}}]}
            case("P5 precedence: plan supplying judgment-origin (not "
                 "explicit) is never treated as a loosening",
                 check_plan_precedence(judgment_plan_plan, good_map) is None)

            # --- run()-level precedence wiring: refused pre-journal via a
            # LIVE receipts/registrations/ directory copied onto `base` ---
            base_regs_dir = regs.registrations_dir(base)
            shutil.copytree(regs.registrations_dir(reg_base), base_regs_dir)
            open(os.path.join(base, "raw", "lock-event.md"), "w",
                 newline="\n").write("lock event body\n")
            subprocess.run(["git", "-C", base, "add", "-A"],
                           capture_output=True)
            subprocess.run(["git", "-C", base, "commit", "-qm",
                            "P5 registrations + lock-event fixture"],
                           capture_output=True)
            pre_chain_p5 = core.check_chain(base)
            try:
                run(base, loosen_plan, FixtureAbsorbBackend())
                case("P5 precedence: run() itself refuses a loosening plan "
                     "pre-journal", False)
            except ValidationError as e:
                case("P5 precedence: run() itself refuses a loosening plan "
                     "pre-journal", "plan-precedence" in str(e))
            case("P5 precedence: run()'s refusal journaled NOTHING",
                 core.check_chain(base) == pre_chain_p5)
            case("P5 precedence: run()'s refusal released the lock",
                 not os.path.isfile(core.lock_path(base)))

            # --- pointer-class ceiling: prose change refused ---
            pv_old = "# Pointer View\n\n## Status\nsome prose here\n"
            pv_new = "# Pointer View\n\n## Status\nDIFFERENT prose here\n"
            try:
                check_pointer_class_ceiling(
                    base, "wiki/pointer.md", pv_old, pv_new,
                    ["receipts/pointer-event.md"], good_map)
                case("P5 ceiling: prose change from a pointer-class event "
                     "refused", False)
            except ValidationError as e:
                case("P5 ceiling: prose change from a pointer-class event "
                     "refused",
                     "pointer-class write ceiling" in str(e)
                     and "wiki/pointer.md" in str(e))

            # --- pointer-class ceiling: status-table row edited WITH
            # attribution passes ---
            pt_old = ("# Pointer View\n\n## Status Table\n"
                      "| Item | State |\n| --- | --- |\n"
                      "| Alpha | old |\n")
            pt_new_ok = ("# Pointer View\n\n## Status Table\n"
                        "| Item | State |\n| --- | --- |\n"
                        "| Alpha | reported by receipt "
                        "receipts/pointer-event.md |\n")
            case("P5 ceiling: status-table row edited WITH the sec.10 "
                 "attribution form passes",
                 check_pointer_class_ceiling(
                     base, "wiki/pointer.md", pt_old, pt_new_ok,
                     ["receipts/pointer-event.md"], good_map) is None)

            # --- pointer-class ceiling: status-table row ADDED WITHOUT
            # attribution refused ---
            pt_new_bad = ("# Pointer View\n\n## Status Table\n"
                         "| Item | State |\n| --- | --- |\n"
                         "| Alpha | old |\n"
                         "| Beta | new, no attribution |\n")
            try:
                check_pointer_class_ceiling(
                    base, "wiki/pointer.md", pt_old, pt_new_bad,
                    ["receipts/pointer-event.md"], good_map)
                case("P5 ceiling: ADDED status-table row WITHOUT attribution "
                     "refused", False)
            except ValidationError as e:
                case("P5 ceiling: ADDED status-table row WITHOUT attribution "
                     "refused",
                     "attribution" in str(e) and "Beta" in str(e))

            # --- pointer-class ceiling: link line added passes ---
            pl_old = "# Pointer View\n\n## Links\nsome text\n"
            pl_new = ("# Pointer View\n\n## Links\nsome text\n"
                     "[receipt](../receipts/pointer-event.md)\n")
            case("P5 ceiling: added link line passes",
                 check_pointer_class_ceiling(
                     base, "wiki/pointer.md", pl_old, pl_new,
                     ["receipts/pointer-event.md"], good_map) is None)

            # --- pointer-class ceiling wired end-to-end through
            # validate_absorb_output: prose-changing absorb answer for a
            # pointer-class event refused pre-journal, journal untouched ---
            class PointerProseBackend:
                def absorb(self, view_rel, view_text, events):
                    return {"new_text": pv_new, "manifest":
                            [{"event": "receipts/pointer-event.md",
                              "section": "Status"}],
                            "corpus_support": [], "noops": []}

            os.makedirs(os.path.join(base, "wiki"), exist_ok=True)
            open(os.path.join(base, "wiki", "pointer.md"), "w",
                 newline="\n").write(pv_old)
            os.makedirs(os.path.join(base, "receipts"), exist_ok=True)
            open(os.path.join(base, "receipts", "pointer-event.md"), "w",
                 newline="\n").write("receipt body: prose lives here\n")
            subprocess.run(["git", "-C", base, "add", "-A"],
                           capture_output=True)
            subprocess.run(["git", "-C", base, "commit", "-qm",
                            "pointer view + receipt fixture"],
                           capture_output=True)
            pointer_plan = {"items": [
                {"view": "wiki/pointer.md",
                 "events": ["receipts/pointer-event.md"],
                 "event_class": {"receipts/pointer-event.md":
                                 {"class": "compile", "origin": "explicit"}}}]}
            pre_chain_ptr = core.check_chain(base)
            try:
                run(base, pointer_plan, PointerProseBackend())
                case("P5 ceiling end-to-end: pointer event's prose-changing "
                     "absorb answer refused pre-journal via run()", False)
            except ValidationError as e:
                case("P5 ceiling end-to-end: pointer event's prose-changing "
                     "absorb answer refused pre-journal via run()",
                     "pointer-class write ceiling" in str(e))
            case("P5 ceiling end-to-end: refusal journaled NOTHING",
                 core.check_chain(base) == pre_chain_ptr)
            case("P5 ceiling end-to-end: view file untouched by the refused "
                 "run", open(os.path.join(base, "wiki", "pointer.md"),
                            encoding="utf-8").read() == pv_old)

            # --- broken fixture chain: loud whole-run refusal, nothing
            # journaled (separate tempdir, never mutates good_map/reg_base) ---
            tamper_base = tempfile.mkdtemp(prefix="cv2-regs-tamper-")
            try:
                subprocess.run(["git", "-C", tamper_base, "init", "-q"],
                               capture_output=True)
                subprocess.run(["git", "-C", tamper_base, "config",
                                "user.email", "t@t"], capture_output=True)
                subprocess.run(["git", "-C", tamper_base, "config",
                                "user.name", "t"], capture_output=True)
                regs.append_registration(
                    tamper_base, regs._minimal("raw/x.md"))
                bad_path = os.path.join(
                    regs.registrations_dir(tamper_base), "1.json")
                bad_rec = json.load(open(bad_path, encoding="utf-8"))
                bad_rec["prev_record_hash"] = "f" * 64
                open(bad_path, "w", encoding="utf-8").write(
                    json.dumps(bad_rec))
                try:
                    _load_registration_seam(tamper_base)
                    case("P5 seam: broken registration chain fails loud "
                         "(_load_registration_seam)", False)
                except regs._cc.JournalViolation:
                    case("P5 seam: broken registration chain fails loud "
                         "(_load_registration_seam)", True)

                # wire through run(): a broken chain must refuse the WHOLE
                # run, pre-journal, nothing written.
                os.makedirs(os.path.join(tamper_base, "wiki"))
                os.makedirs(os.path.join(tamper_base, "raw"), exist_ok=True)
                open(os.path.join(tamper_base, "wiki", "a.md"), "w",
                     newline="\n").write("# A\n\n## Intro\nhello\n")
                open(os.path.join(tamper_base, "raw", "x.md"), "w",
                     newline="\n").write("fact\n")
                subprocess.run(["git", "-C", tamper_base, "add", "-A"],
                               capture_output=True)
                subprocess.run(["git", "-C", tamper_base, "commit", "-qm",
                                "seed"], capture_output=True)
                tamper_plan = {"items": [
                    {"view": "wiki/a.md", "events": ["raw/x.md"],
                     "event_class": {"raw/x.md":
                                     {"class": "t3", "origin": "explicit"}}}]}
                try:
                    run(tamper_base, tamper_plan, FixtureAbsorbBackend())
                    case("P5 seam: run() over a broken registration chain "
                         "refuses loud, nothing journaled", False)
                except regs._cc.JournalViolation:
                    case("P5 seam: run() over a broken registration chain "
                         "refuses loud, nothing journaled",
                         not os.path.isdir(core.journal_dir(tamper_base))
                         or core.check_chain(tamper_base) == 0)
                case("P5 seam: run()'s refusal over a broken chain released "
                     "the lock",
                     not os.path.isfile(core.lock_path(tamper_base)))
            finally:
                shutil.rmtree(tamper_base, ignore_errors=True)
        finally:
            shutil.rmtree(reg_base, ignore_errors=True)

        # ================================== v3.0-63 / v3.0-67 (v3.0.29)
        # Plan-scoped totality + real pre-absorb baselines. The mechanical
        # half (check_claim_routing) unit cases first, then the shipped
        # ACCEPTANCE FIXTURE: (a) a correctly-narrowed view CONFIRMS while
        # a sibling carries the rest; (b) a view missing an OWNED claim
        # REJECTS; (c) a claim owned by nobody refuses the run pre-write;
        # (d) an UPDATE to an existing view -- with a reverted creation
        # ghost in journal history, the exact Ultrapak 2026-08-05 shape --
        # gets its real substantial baseline in the packet and CONFIRMS
        # with narrowed scope. Plus: NEW-view declared-from-empty, and the
        # operator-adjudicated baseline (set-aside advances it, named).

        def _cr_plan(routing):
            return {"items": [
                {"view": "wiki/a.md", "events": ["raw/e1.md"],
                 "event_class": {"raw/e1.md": {"class": "t3",
                                               "origin": "explicit"}}},
                {"view": "wiki/b.md", "events": ["raw/e1.md"],
                 "event_class": {"raw/e1.md": {"class": "t3",
                                               "origin": "explicit"}}}],
                "claim_routing": routing}

        case("claim-routing: valid routing passes",
             check_claim_routing(_cr_plan({"raw/e1.md": {
                 "claims": [{"id": "c1", "text": "alpha", "owner": "wiki/a.md"},
                            {"id": "c2", "text": "beta", "owner": "wiki/b.md"}],
                 "deferred": [{"id": "c3", "text": "gamma",
                               "targets": ["wiki/c.md"]}]}})) is None)
        try:
            check_claim_routing(_cr_plan({"raw/e1.md": {
                "claims": [{"id": "c1", "text": "alpha", "owner": ""}]}}))
            case("claim-routing: claim with NO owner refused, full stop", False)
        except ValidationError as e:
            case("claim-routing: claim with NO owner refused, full stop",
                 "full stop" in str(e) and "c1" in str(e))
        try:
            check_claim_routing(_cr_plan({"raw/e1.md": {
                "claims": [{"id": "c1", "text": "alpha", "owner": "wiki/a.md"},
                           {"id": "c1", "text": "beta", "owner": "wiki/b.md"}]}}))
            case("claim-routing: duplicate claim id refused", False)
        except ValidationError as e:
            case("claim-routing: duplicate claim id refused",
                 "duplicate claim id" in str(e))
        try:
            check_claim_routing(_cr_plan({"raw/e1.md": {
                "claims": [{"id": "c1", "text": "alpha",
                            "owner": "wiki/never-planned.md"}]}}))
            case("claim-routing: owner that never receives the event refused",
                 False)
        except ValidationError as e:
            case("claim-routing: owner that never receives the event refused",
                 "cannot absorb" in str(e))
        try:
            check_claim_routing(_cr_plan({"raw/e1.md": {
                "deferred": [{"id": "c9", "text": "gamma", "targets": []}]}}))
            case("claim-routing: deferral to nowhere refused (declared away)",
                 False)
        except ValidationError as e:
            case("claim-routing: deferral to nowhere refused (declared away)",
                 "declared away" in str(e))
        try:
            check_claim_routing(_cr_plan({"raw/phantom.md": {
                "claims": [{"id": "c1", "text": "alpha",
                            "owner": "wiki/a.md"}]}}))
            case("claim-routing: routing for an event outside the plan "
                 "refused", False)
        except ValidationError as e:
            case("claim-routing: routing for an event outside the plan "
                 "refused", "no plan item" in str(e))
        try:
            check_claim_routing(_cr_plan({"raw/e1.md": {
                "claims": [{"id": "c1", "text": "alpha",
                            "owner": "wiki/a.md"}],
                "deferred": [{"id": "c1", "text": "alpha again",
                              "targets": ["wiki/c.md"]}]}}))
            case("claim-routing: id in both claims and deferred refused",
                 False)
        except ValidationError as e:
            case("claim-routing: id in both claims and deferred refused",
                 "never both" in str(e))

        # --- shared fixtures for the acceptance runs ---
        DERIV = ("# --- derivation (engine-managed; strip region) ---\n"
                 "schema_version: 3.2\nview: topic\nsummary: \"S\"\n"
                 "entities: []\nstatus: active\ntier: T1\n"
                 "consumed_status: legacy-assumed\norigin_max: human\n"
                 "subscribes:\n  entities: []\n  corpus: []\nbundle: []\n"
                 "verified: null\n"
                 "# --- /derivation ---\n")

        def _scoped_view(title, body):
            return ("---\ntitle: %s\n---\n%s\n## Intro\n%s\n"
                    % (title, DERIV, body))

        class ScopedAbsorbBackend:
            """Adds one 'Absorbed' section per view with the exact text the
            fixture assigns it (None = leave the claim OUT, the case-(b)
            defect)."""

            def __init__(self, adds):
                self.adds = adds

            def absorb(self, view_rel, view_text, events):
                add = self.adds[view_rel]
                new = (view_text.rstrip("\n") + "\n\n## Absorbed\n"
                       + (add if add is not None else "unrelated filler")
                       + "\n")
                return {"new_text": new,
                        "manifest": [{"event": e, "section": "Absorbed"}
                                     for e in sorted(events)],
                        "corpus_support": [], "noops": []}

        class ScopedClaimVerifyBackend:
            """Deterministic scoped grader -- implements graded question 1
            mechanically over the packet the engine built: every claim the
            DECLARED CLAIM ROUTING section says THIS VIEW OWNS must appear
            in the FULL VIEW BODY section. Confirms iff none is missing;
            rejects naming the missing claim. Carries the F17 substrate
            block so confirms can stamp."""

            def __init__(self):
                self.calls = []

            def verify(self, packet):
                self.calls.append(packet)
                verdict = {"reason": "scoped-fixture",
                           "uncertainty": "confident",
                           "verifier": {"vendor": "openai",
                                        "model": "fixture"},
                           "substrate": {
                               "verifier_vendor": "openai",
                               "verifier_model_id": "gpt-5.5",
                               "absorb_vendor": "anthropic",
                               "absorb_model_id": "claude-fable-5",
                               "substrate_source": "invocation-metadata"}}
                if "## DECLARED CLAIM ROUTING" not in packet:
                    verdict["verdict"] = "rejected"
                    verdict["reason"] = "no declared claim routing section"
                    return verdict
                seg = packet.split("Claims THIS VIEW OWNS", 1)[1].split(
                    "Claims routed to SIBLING", 1)[0]
                owned = [ln.split("] ", 1)[1] for ln in seg.splitlines()
                         if ln.startswith("- [") and "] " in ln]
                body = packet.split("## FULL VIEW BODY (POST-ABSORB)",
                                    1)[1].split("## ABSORBED EVENT", 1)[0]
                missing = [t for t in owned if t not in body]
                if missing:
                    verdict["verdict"] = "rejected"
                    verdict["reason"] = ("owned-claim-missing: %s"
                                         % "; ".join(missing))
                else:
                    verdict["verdict"] = "confirmed"
                return verdict

        os.makedirs(os.path.join(base, "wiki", "scoped"), exist_ok=True)
        os.makedirs(os.path.join(base, "raw", "scoped"), exist_ok=True)
        for name in ("sa", "sb"):
            open(os.path.join(base, "wiki", "scoped", "%s.md" % name), "w",
                 newline="\n").write(_scoped_view(name.upper(),
                                                  "original %s body" % name))
        open(os.path.join(base, "raw", "scoped", "wide.md"), "w",
             newline="\n").write(
            "wide source: the alpha claim text, the beta claim text, and "
            "the gamma claim text\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "scoped fixture"],
                       capture_output=True)

        def _scoped_plan(views, routing):
            return {"items": [
                {"view": v, "events": ["raw/scoped/wide.md"],
                 "event_class": {"raw/scoped/wide.md": {
                     "class": "t3", "origin": "explicit"}}} for v in views],
                "claim_routing": routing}

        ROUTING_AB = {"raw/scoped/wide.md": {
            "claims": [{"id": "c1", "text": "the alpha claim text",
                        "owner": "wiki/scoped/sa.md"},
                       {"id": "c2", "text": "the beta claim text",
                        "owner": "wiki/scoped/sb.md"}],
            "deferred": [{"id": "c3", "text": "the gamma claim text",
                          "targets": ["wiki/scoped/sc.md"]}]}}

        # --- ACCEPTANCE (a): correctly-narrowed views CONFIRM ---
        plan_a = _scoped_plan(["wiki/scoped/sa.md", "wiki/scoped/sb.md"],
                              ROUTING_AB)
        res_a = run(base, plan_a, ScopedAbsorbBackend({
            "wiki/scoped/sa.md": "the alpha claim text",
            "wiki/scoped/sb.md": "the beta claim text"}))
        rec_a = json.load(open(os.path.join(core.journal_dir(base),
                                            "%d.json" % res_a["seq"]),
                               encoding="utf-8"))
        case("v3.0-63: run() journals the claim routing on the compile "
             "record", rec_a.get("claim_routing") == ROUTING_AB)
        sv_backend = ScopedClaimVerifyBackend()
        vres_a = verify_run(base, res_a["seq"], sv_backend)
        case("ACCEPTANCE (a): both correctly-narrowed views CONFIRM while "
             "the sibling carries the rest",
             vres_a.get("absorption_checked") == 2
             and vres_a.get("absorption_confirmed") == 2)
        pk_a = [p for p in sv_backend.calls
                if "view wiki/scoped/sa.md" in p][0]
        case("v3.0-63: scoped packet carries the DECLARED CLAIM ROUTING "
             "section", "## DECLARED CLAIM ROUTING (plan-scoped, "
             "v3.0-63)" in pk_a)
        case("v3.0-63: scoped CLAIM names DECLARED SCOPE and the "
             "enumeration-incomplete reason class",
             "faithfully carries its DECLARED SCOPE" in pk_a
             and "enumeration-incomplete" in pk_a)
        case("v3.0-63: scoped packet still has exactly one CLAIM line",
             sum(1 for ln in pk_a.splitlines()
                 if ln.startswith("CLAIM: ")) == 1)
        case("v3.0-63: sibling-owned claim listed with its owner (declared "
             "scope, not an omission)",
             "[raw/scoped/wide.md / c2] -> wiki/scoped/sb.md" in pk_a)
        case("v3.0-63: deferred claim listed with its pending_cascade "
             "target", "[raw/scoped/wide.md / c3] -> wiki/scoped/sc.md"
             in pk_a)
        case("v3.0-63: legacy sections 1-6 keep their mandated order with "
             "the routing section strictly LAST (additive-only)",
             pk_a.index("## DECLARED CLAIM ROUTING")
             > pk_a.index("## ROUTING CENSUS (F15)")
             > pk_a.index("## CORPUS EXCERPTS")
             > pk_a.index("## MANIFEST CLAIMS"))

        # --- ACCEPTANCE (b): a view missing an OWNED claim REJECTS ---
        for name in ("sa2", "sb2"):
            open(os.path.join(base, "wiki", "scoped", "%s.md" % name), "w",
                 newline="\n").write(_scoped_view(name.upper(),
                                                  "original %s body" % name))
        open(os.path.join(base, "raw", "scoped", "wide2.md"), "w",
             newline="\n").write(
            "second wide source: the alpha claim text and the beta claim "
            "text\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "scoped b"],
                       capture_output=True)
        plan_b = {"items": [
            {"view": v, "events": ["raw/scoped/wide2.md"],
             "event_class": {"raw/scoped/wide2.md": {
                 "class": "t3", "origin": "explicit"}}}
            for v in ("wiki/scoped/sa2.md", "wiki/scoped/sb2.md")],
            "claim_routing": {"raw/scoped/wide2.md": {
                "claims": [{"id": "c1", "text": "the alpha claim text",
                            "owner": "wiki/scoped/sa2.md"},
                           {"id": "c2", "text": "the beta claim text",
                            "owner": "wiki/scoped/sb2.md"}]}}}
        res_b = run(base, plan_b, ScopedAbsorbBackend({
            "wiki/scoped/sa2.md": None,        # the case-(b) defect
            "wiki/scoped/sb2.md": "the beta claim text"}))
        sv_backend_b = ScopedClaimVerifyBackend()
        vres_b = verify_run(base, res_b["seq"], sv_backend_b)
        case("ACCEPTANCE (b): the view missing its OWNED claim REJECTS; "
             "the faithful sibling still CONFIRMS",
             vres_b.get("absorption_checked") == 2
             and vres_b.get("absorption_confirmed") == 1)
        vrec_b = json.load(open(os.path.join(core.journal_dir(base),
                                             "%d.json" % vres_b["seq"]),
                                encoding="utf-8"))
        case("ACCEPTANCE (b): the rejection names the missing owned claim",
             any(a.get("view") == "wiki/scoped/sa2.md"
                 and "owned-claim-missing" in a.get("reason", "")
                 for a in vrec_b.get("absorption_verify_attempts", [])))

        # --- ACCEPTANCE (c): a claim owned by nobody refuses pre-write ---
        pre_chain_cr = core.check_chain(base)
        before_sa = open(os.path.join(base, "wiki", "scoped", "sa.md"),
                         encoding="utf-8").read()
        plan_c = _scoped_plan(["wiki/scoped/sa.md", "wiki/scoped/sb.md"],
                              {"raw/scoped/wide.md": {
                                  "claims": [
                                      {"id": "c1",
                                       "text": "the alpha claim text",
                                       "owner": "wiki/scoped/sa.md"},
                                      {"id": "c2",
                                       "text": "the beta claim text",
                                       "owner": ""}]}})
        try:
            run(base, plan_c, ScopedAbsorbBackend({
                "wiki/scoped/sa.md": "the alpha claim text",
                "wiki/scoped/sb.md": "the beta claim text"}))
            case("ACCEPTANCE (c): claim owned by nobody refuses the whole "
                 "run", False)
        except ValidationError as e:
            case("ACCEPTANCE (c): claim owned by nobody refuses the whole "
                 "run", "full stop" in str(e))
        case("ACCEPTANCE (c): the refusal journaled NOTHING and wrote "
             "NOTHING", core.check_chain(base) == pre_chain_cr
             and open(os.path.join(base, "wiki", "scoped", "sa.md"),
                      encoding="utf-8").read() == before_sa)

        # --- ACCEPTANCE (d): UPDATE baseline survives a reverted creation
        # ghost (v3.0-67, the Ultrapak shape) ---
        open(os.path.join(base, "wiki", "scoped", "su.md"), "w",
             newline="\n").write(_scoped_view(
                 "SU", "substantial existing body that predates the ghost"))
        open(os.path.join(base, "raw", "scoped", "eu.md"), "w",
             newline="\n").write("update source: the delta claim text\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "su fixture"],
                       capture_output=True)
        # the ghost: a compile record that CREATED su.md from empty (the
        # canonical empty blob), later named reverted by a driver revert.
        empty_blob = _blob_of_text(base, "")
        ghost = core.minimal_record("compile",
                                    _git(base, "rev-parse", "HEAD").strip())
        ghost["absorbed"] = [{"view": "wiki/scoped/su.md",
                              "events": ["raw/scoped/eu.md"],
                              "pre_blob": empty_blob, "post_blob": "x",
                              "manifest": [], "corpus_support": []}]
        ghost["run_window"] = {"start": "t0", "end": "t1"}
        ghost_seq, _gp = core.append_record(base, ghost)
        grev = core.minimal_record("driver-revert",
                                   _git(base, "rev-parse", "HEAD").strip())
        grev["run_window"] = {"start": "t0", "end": "t1"}
        grev["driver_revert"] = {"reverts_seq": ghost_seq,
                                 "reverts_commit": "0" * 40,
                                 "status": "reverted",
                                 "reason": "fixture ghost", "at": "t1",
                                 "driver": "deploy/compile-driver.py"}
        core.append_record(base, grev)
        plan_d = {"items": [
            {"view": "wiki/scoped/su.md", "events": ["raw/scoped/eu.md"],
             "event_class": {"raw/scoped/eu.md": {
                 "class": "t3", "origin": "explicit"}}}],
            "claim_routing": {"raw/scoped/eu.md": {
                "claims": [{"id": "u1", "text": "the delta claim text",
                            "owner": "wiki/scoped/su.md"}]}}}
        res_d = run(base, plan_d, ScopedAbsorbBackend(
            {"wiki/scoped/su.md": "the delta claim text"}))
        sv_backend_d = ScopedClaimVerifyBackend()
        vres_d = verify_run(base, res_d["seq"], sv_backend_d)
        pk_d = sv_backend_d.calls[-1]
        diff_d = pk_d.split("## CUMULATIVE DIFF SINCE LAST VERIFIED",
                            1)[1].split("## FULL VIEW BODY", 1)[0]
        case("ACCEPTANCE (d): the packet baseline is the view's REAL "
             "pre-absorb content, never the reverted ghost's empty blob",
             "the view's real pre-absorb content" in diff_d
             and "NEW VIEW" not in diff_d)
        case("ACCEPTANCE (d): the diff base carries the substantial "
             "existing body (an update-diff, not created-from-nothing)",
             "substantial existing body that predates the ghost" in diff_d)
        case("ACCEPTANCE (d): the update with correct baseline + narrowed "
             "scope CONFIRMS",
             vres_d.get("absorption_confirmed") == 1)

        # --- NEW view: declared from-empty, in so many words ---
        open(os.path.join(base, "raw", "scoped", "en.md"), "w",
             newline="\n").write("new-view source: the nova claim text\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "en fixture"],
                       capture_output=True)

        class NewViewBackend:
            def absorb(self, view_rel, view_text, events):
                new = _scoped_view("SN", "the nova claim text")
                secs = changed_sections(base,
                                        _blob_of_text(base, view_text or ""),
                                        _blob_of_text(base, new))
                e0 = sorted(events)[0]
                return {"new_text": new,
                        "manifest": [{"event": e0, "section": s}
                                     for s in sorted(secs)],
                        "corpus_support": [], "noops": []}

        plan_n = {"items": [
            {"view": "wiki/scoped/sn.md", "events": ["raw/scoped/en.md"],
             "event_class": {"raw/scoped/en.md": {
                 "class": "t3", "origin": "explicit"}}}]}
        res_n = run(base, plan_n, NewViewBackend())
        sv_backend_n = _GoodAttestBackend(confirm=True)
        verify_run(base, res_n["seq"], sv_backend_n)
        pk_n = sv_backend_n.calls[-1]
        case("v3.0-67: a genuinely NEW view's packet says so out loud "
             "(verifies from empty, declared)",
             "baseline: NEW VIEW" in pk_n
             and "legitimately verifies from empty" in pk_n)

        # --- adjudicated baseline: a set-aside record advances the base,
        # and the packet names it (not machine-verified) ---
        adj_content = open(os.path.join(base, "wiki", "scoped", "sa2.md"),
                           encoding="utf-8").read()
        adj_commit = _git(base, "rev-parse", "HEAD").strip()
        adj = core.minimal_record("verify-adjudication", adj_commit)
        adj["run_window"] = {"start": "t0", "end": "t1"}
        adj["absorption_adjudicated"] = [{
            "view": "wiki/scoped/sa2.md", "adjudicates_seq": res_b["seq"],
            "at": "2026-08-06T12:00:00", "ruling": "fixture ruling",
            "adjudicated_by": "operator",
            "baseline_commit": adj_commit,
            "view_sha256": _sha256(adj_content)}]
        core.append_record(base, adj)
        open(os.path.join(base, "raw", "scoped", "ej.md"), "w",
             newline="\n").write("post-ruling source: the judged claim "
                                 "text\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "ej fixture"],
                       capture_output=True)
        plan_j = {"items": [
            {"view": "wiki/scoped/sa2.md", "events": ["raw/scoped/ej.md"],
             "event_class": {"raw/scoped/ej.md": {
                 "class": "t3", "origin": "explicit"}}}]}

        class AdjUpdateBackend:
            def absorb(self, view_rel, view_text, events):
                new = (view_text.rstrip("\n")
                       + "\n\n## Post Ruling\nthe judged claim text\n")
                return {"new_text": new,
                        "manifest": [{"event": e, "section": "Post Ruling"}
                                     for e in sorted(events)],
                        "corpus_support": [], "noops": []}

        res_j = run(base, plan_j, AdjUpdateBackend())
        sv_backend_j = _GoodAttestBackend(confirm=True)
        verify_run(base, res_j["seq"], sv_backend_j)
        pk_j = sv_backend_j.calls[-1]
        diff_j = pk_j.split("## CUMULATIVE DIFF SINCE LAST VERIFIED",
                            1)[1].split("## FULL VIEW BODY", 1)[0]
        case("v3.0.29 set-aside: an operator adjudication ADVANCES the "
             "baseline and the packet names it in so many words",
             "baseline: adjudicated 2026-08-06T12:00:00 by operator "
             "ruling, not machine-verified" in diff_j)
        case("v3.0.29 set-aside: the adjudicated-base diff shows only the "
             "post-ruling delta, not the whole view from birth",
             "the judged claim text" in diff_j
             and "original sa2 body" not in "".join(
                 ln for ln in diff_j.splitlines()
                 if ln.startswith("+")))

        # ================== v3.0.39: union skip + baseline-reset (105/106)
        # The cross-check correction made operational: a union-leg set-aside
        # record (view = `union:<event>`, carries union_event, NO pin
        # fields) must never mint or advance a trigger-state entry.
        u_adj = core.minimal_record("verify-adjudication",
                                    _git(base, "rev-parse", "HEAD").strip())
        u_adj["run_window"] = {"start": "t0", "end": "t1"}
        u_adj["absorption_adjudicated"] = [{
            "view": "union:raw/scoped/eu.md",
            "union_event": "raw/scoped/eu.md",
            "union_views": ["wiki/scoped/su.md"],
            "event_sha256": "e" * 64,
            "union_view_sha256": {"wiki/scoped/su.md": "f" * 64},
            "adjudicates_seq": res_d["seq"], "at": "2026-08-16T12:00:00",
            "ruling": "union fixture ruling", "adjudicated_by": "operator",
            "rejected_artifact": "receipts/verify/noop-fixture.json"}]
        core.append_record(base, u_adj)
        st_u = _absorption_trigger_state(base, 10 ** 6)
        case("v3.0-105 REQUIRED skip: a union adjudication record mints NO "
             "trigger-state entry for its pseudo-view",
             "union:raw/scoped/eu.md" not in st_u)
        case("v3.0-105 REQUIRED skip: a union set-aside advances no view "
             "baseline (su.md's stamp is untouched by the union record)",
             (st_u.get("wiki/scoped/su.md") or {}).get("last_verified_kind")
             == "machine-verified")
        # Disjointness regression: the skip keys on the union_event FIELD,
        # never on the `union:` string prefix -- a real view path that
        # happened to begin with `union:` still gets its baseline tracked.
        odd_adj = core.minimal_record("verify-adjudication",
                                      _git(base, "rev-parse",
                                           "HEAD").strip())
        odd_adj["run_window"] = {"start": "t0", "end": "t1"}
        odd_adj["absorption_adjudicated"] = [{
            "view": "union:odd-but-real-path.md",
            "adjudicates_seq": res_d["seq"], "at": "2026-08-16T12:01:00",
            "ruling": "odd-path fixture ruling",
            "adjudicated_by": "operator",
            "baseline_commit": _git(base, "rev-parse", "HEAD").strip(),
            "view_sha256": "a" * 64}]
        core.append_record(base, odd_adj)
        st_o = _absorption_trigger_state(base, 10 ** 6)
        case("v3.0-105 disjointness regression: a PIN-BEARING entry whose "
             "real path merely starts with 'union:' is still tracked "
             "(the skip is field-keyed, not prefix-keyed)",
             (st_o.get("union:odd-but-real-path.md") or {})
             .get("last_verified_kind") == "adjudicated")

        # --- v3.0-106: the baseline-reset rung -- wins by seq, packet
        # names it out loud, recovery stays fail-honest, and a post-reset
        # confirm supersedes it.
        sa2_now = open(os.path.join(base, "wiki", "scoped", "sa2.md"),
                       encoding="utf-8").read()
        reset_commit = _git(base, "rev-parse", "HEAD").strip()
        brec = core.minimal_record("baseline-reset", reset_commit)
        brec["run_window"] = {"start": "t0", "end": "t1"}
        brec["baseline_reset"] = [{
            "view": "wiki/scoped/sa2.md", "at": "2026-08-16T13:00:00",
            "refresh_commit": reset_commit,
            "view_sha256": _sha256(sa2_now),
            "provenance": "fixture corpus photograph, imported "
                          "2026-08-16 outside the engine",
            "ruling": "fixture reset ruling", "reset_by": "operator",
            "driver": "deploy/compile-driver.py"}]
        brec["refused"] = []
        core.append_record(base, brec)
        st_r = _absorption_trigger_state(base, 10 ** 6)
        case("v3.0-106: the reset rung ADVANCES the ladder (newest by "
             "journal seq beats the older machine-verified stamp), kind "
             "baseline-reset, commit = the refresh commit",
             (st_r.get("wiki/scoped/sa2.md") or {})
             .get("last_verified_kind") == "baseline-reset"
             and (st_r.get("wiki/scoped/sa2.md") or {})
             .get("last_verified_commit") == reset_commit)
        diff_r = _cumulative_diff(base, "wiki/scoped/sa2.md", sa2_now, st_r)
        case("v3.0-106: the packet names the reset baseline in so many "
             "words, provenance included",
             "baseline: reset to imported snapshot by operator ruling, "
             "not machine-verified -- fixture corpus photograph"
             in diff_r)
        # fail-honest recovery: a reset entry whose pinned sha disagrees
        # with the recovered content must say so, never silently diff.
        brec2 = core.minimal_record("baseline-reset",
                                    _git(base, "rev-parse", "HEAD").strip())
        brec2["run_window"] = {"start": "t0", "end": "t1"}
        brec2["baseline_reset"] = [{
            "view": "wiki/scoped/su.md", "at": "2026-08-16T13:05:00",
            "refresh_commit": reset_commit,
            "view_sha256": "0" * 64,
            "provenance": "fixture bad-pin photograph",
            "ruling": "fixture reset ruling", "reset_by": "operator",
            "driver": "deploy/compile-driver.py"}]
        brec2["refused"] = []
        core.append_record(base, brec2)
        st_bad = _absorption_trigger_state(base, 10 ** 6)
        su_now = open(os.path.join(base, "wiki", "scoped", "su.md"),
                      encoding="utf-8").read()
        diff_bad = _cumulative_diff(base, "wiki/scoped/su.md", su_now,
                                    st_bad)
        case("v3.0-106: recovery stays fail-honest on a sha mismatch "
             "(CUMULATIVE DIFF UNAVAILABLE, never a silent wrong base)",
             "CUMULATIVE DIFF UNAVAILABLE" in diff_bad)
        # a post-reset confirm supersedes the reset rung (newest by seq)
        open(os.path.join(base, "raw", "scoped", "ek.md"), "w",
             newline="\n").write("post-reset source: the kappa claim "
                                 "text\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "ek fixture"],
                       capture_output=True)
        plan_k = {"items": [
            {"view": "wiki/scoped/sa2.md", "events": ["raw/scoped/ek.md"],
             "event_class": {"raw/scoped/ek.md": {
                 "class": "t3", "origin": "explicit"}}}]}

        class ResetUpdateBackend:
            def absorb(self, view_rel, view_text, events):
                new = (view_text.rstrip("\n")
                       + "\n\n## Post Reset\nthe kappa claim text\n")
                return {"new_text": new,
                        "manifest": [{"event": e, "section": "Post Reset"}
                                     for e in sorted(events)],
                        "corpus_support": [], "noops": []}

        res_k = run(base, plan_k, ResetUpdateBackend())
        sv_backend_k = _GoodAttestBackend(confirm=True)
        verify_run(base, res_k["seq"], sv_backend_k)
        pk_k = sv_backend_k.calls[-1]
        case("v3.0-106: the packet fired OVER the reset baseline names it "
             "(integration: the run's own verify leg saw the reset rung)",
             "baseline: reset to imported snapshot by operator ruling"
             in pk_k)
        st_k = _absorption_trigger_state(base, 10 ** 6)
        case("v3.0-106: a post-reset confirm SUPERSEDES the reset rung "
             "(the ladder never shadows the live verify lifecycle)",
             (st_k.get("wiki/scoped/sa2.md") or {})
             .get("last_verified_kind") == "machine-verified")

        # ===================================== verifier demotion (2026-08-09)
        # Record-time class normalization (unit) + the journaled leg fields
        # (integration) + the packet instruction. The engine's half of the
        # design; the exit split and the ledger live in compile-driver.
        crc = classify_reason_classes
        case("demotion: structured recorded class -> recorded",
             crc({"verdict": "rejected", "reason": "x",
                  "reason_classes": ["scope-omission"]})
             == ("rejected", ["scope-omission"], "recorded"))
        case("demotion: mixed classes on one leg -> strictest wins "
             "(blocking)",
             crc({"verdict": "rejected", "reason": "x",
                  "reason_classes": ["scope-omission", "fabrication"]})
             == ("rejected", ["scope-omission", "fabrication"], "blocking"))
        case("demotion: one unrecognized member poisons the whole list "
             "(no cherry-picking) -> unclassified/blocking",
             crc({"verdict": "rejected", "reason": "x",
                  "reason_classes": ["scope-omission", "probably-fine"]})
             == ("rejected", ["unclassified"], "blocking"))
        case("demotion: prose-token fallback (the v3.0.29 convention, "
             "generalized) -> enumeration-incomplete records",
             crc({"verdict": "rejected",
                  "reason": "reason class: enumeration-incomplete, claim "
                            "c3 missing from the routing"})
             == ("rejected", ["enumeration-incomplete"], "recorded"))
        case("demotion: no parseable class anywhere -> unclassified/"
             "blocking (fail-closed)",
             crc({"verdict": "revised", "reason": "omission in section 2"})
             == ("revised", ["unclassified"], "blocking"))
        case("demotion: over-certainty is BLOCKING by design "
             "(falsity-shaped)",
             crc({"verdict": "revised", "reason": "x",
                  "reason_classes": ["over-certainty"]})
             == ("revised", ["over-certainty"], "blocking"))
        case("demotion: substrate-gated outer reads label AND classes from "
             "the same usable inner object",
             crc({"verdict": "substrate-gated", "reason": "outer",
                  "bridge_verdict": {"verdict": "rejected", "reason": "y",
                                     "reason_classes": ["scope-omission"]}})
             == ("rejected", ["scope-omission"], "recorded"))
        case("demotion: an empty structured list falls through to the "
             "prose scan",
             crc({"verdict": "rejected", "reason": "fabrication found",
                  "reason_classes": []})
             == ("rejected", ["fabrication"], "blocking"))
        case("demotion: a transport-class verdict is unclassified/blocking "
             "(run completeness stays the driver's call, untouched)",
             crc({"verdict": "bridge-error", "reason": "exit 7"})
             == ("bridge-error", ["unclassified"], "blocking"))

        # integration: a rejecting leg whose verdict carries a structured
        # recorded class journals all three fields on the attempts entry.
        open(os.path.join(base, "raw", "scoped", "er.md"), "w",
             newline="\n").write("recorded-signal source: the rho claim "
                                 "text\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "er fixture"],
                       capture_output=True)

        class _ClassingRejectBackend:
            def __init__(self, classes):
                self.calls = []
                self.classes = classes

            def verify(self, packet):
                self.calls.append(packet)
                return {"verdict": "rejected",
                        "reason": "scope-omission: the rho claim text is "
                                  "not represented",
                        "reason_classes": list(self.classes),
                        "uncertainty": "confident",
                        "verifier": {"vendor": "openai", "model": "gpt-5.5"}}

        plan_r = {"items": [
            {"view": "wiki/scoped/sb.md", "events": ["raw/scoped/er.md"],
             "event_class": {"raw/scoped/er.md": {
                 "class": "t3", "origin": "explicit"}}}]}
        res_r = run(base, plan_r, ScopedAbsorbBackend(
            {"wiki/scoped/sb.md": "unrelated to rho"}))
        rj_backend = _ClassingRejectBackend(["scope-omission"])
        vres_r = verify_run(base, res_r["seq"], rj_backend)
        vrec_r = json.load(open(os.path.join(core.journal_dir(base),
                                             "%d.json" % vres_r["seq"]),
                                encoding="utf-8"))
        att_r = (vrec_r.get("absorption_verify_attempts") or [{}])[0]
        case("demotion: the attempts entry journals verdict_label + "
             "reason_classes + disposition at record time",
             att_r.get("verdict_label") == "rejected"
             and att_r.get("reason_classes") == ["scope-omission"]
             and att_r.get("disposition") == "recorded")
        case("demotion: verify_run's return surfaces the same fields for "
             "the driver's band (journal stays the authority)",
             (vres_r.get("absorption_attempts") or [{}])[0].get(
                 "disposition") == "recorded")
        pk_r = rj_backend.calls[-1]
        case("demotion: the absorption packet carries the REASON CLASS "
             "instruction, strictly last",
             pk_r.rstrip().endswith("treated as blocking.")
             and "## REASON CLASS (verifier demotion, 2026-08-09)" in pk_r)

        # ===================================== v3.0-69: derivation minting
        # The defect: the absorb path never CREATED a region, so a view born
        # on the engine could never record a verification -- the checker's
        # confirm was produced and discarded. Note these fixtures author
        # text with NO region (what a real author produces; the ANSWER
        # CONTRACT never asks for one) -- authoring one is exactly the
        # fixture habit that hid this defect from the batteries.
        open(os.path.join(base, "raw", "scoped", "eb.md"), "w",
             newline="\n").write("born-on-engine source: the borne claim\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "eb fixture"],
                       capture_output=True)

        BORN_TEXT = ("---\ntitle: Borne\ndomain: topic\nscope: domain\n"
                     "confidence: medium\nsources:\n  - raw/scoped/eb.md\n"
                     "---\n\n# Borne\n\n## Intro\nthe borne claim\n")

        class RegionlessNewViewBackend:
            def absorb(self, view_rel, view_text, events):
                secs = changed_sections(
                    base, _blob_of_text(base, view_text or ""),
                    _blob_of_text(base, BORN_TEXT))
                e0 = sorted(events)[0]
                return {"new_text": BORN_TEXT,
                        "manifest": [{"event": e0, "section": s}
                                     for s in sorted(secs)],
                        "corpus_support": [], "noops": []}

        plan_born = {"items": [
            {"view": "wiki/scoped/borne.md", "events": ["raw/scoped/eb.md"],
             "event_class": {"raw/scoped/eb.md": {"class": "t3",
                                                  "origin": "explicit"}}}]}
        res_born = run(base, plan_born, RegionlessNewViewBackend())
        born_disk = open(os.path.join(base, "wiki", "scoped", "borne.md"),
                         encoding="utf-8").read()
        case("v3.0-69: a view CREATED by the engine gets a derivation "
             "region minted, though its author wrote none",
             asm.DERIV_START in born_disk and asm.DERIV_END in born_disk)
        case("v3.0-69: the minted region carries the conservative defaults "
             "(tier T1, consumed_status legacy-assumed, verified null) and "
             "is NOT labelled a legacy view in its summary",
             "tier: T1" in born_disk
             and "consumed_status: legacy-assumed" in born_disk
             and "verified: null" in born_disk
             and "(legacy view; summary pending)" not in born_disk)
        case("v3.0-69: the author's own body survives the mint verbatim",
             "the borne claim" in born_disk and "# Borne" in born_disk)
        born_rec = json.load(open(os.path.join(core.journal_dir(base),
                                               "%d.json" % res_born["seq"]),
                                  encoding="utf-8"))
        case("v3.0-69: the journal's post_blob pins the MINTED text (what "
             "actually landed on disk), not the pre-mint author text",
             _git(base, "cat-file", "-p",
                  born_rec["absorbed"][0]["post_blob"]) == born_disk)
        born_backend = _GoodAttestBackend(confirm=True)
        born_vres = verify_run(base, res_born["seq"], born_backend)
        case("v3.0-69 ACCEPTANCE: the confirm is now RECORDED -- an "
             "engine-born view reaches verified state (this returned 0 "
             "confirmed before the fix, with the verdict artifact saying "
             "'confirmed')",
             born_vres.get("absorption_checked") == 1
             and born_vres.get("absorption_confirmed") == 1)
        born_vrec = json.load(open(os.path.join(core.journal_dir(base),
                                                "%d.json" % born_vres["seq"]),
                                   encoding="utf-8"))
        born_after = open(os.path.join(base, "wiki", "scoped", "borne.md"),
                          encoding="utf-8").read()
        case("v3.0-69: the verification is stamped into the view and "
             "journaled as an absorption_verified entry",
             len(born_vrec.get("absorption_verified", [])) == 1
             and "status: passed" in born_after)
        # v3.0-71: provenance at mint, transition at stamp -- the engine-born
        # population's label fix, both halves in one trajectory.
        case("v3.0-71: the engine mint records minted_by: engine in the "
             "region (provenance at birth)",
             "minted_by: engine" in born_disk
             and "consumed_status: legacy-assumed" in born_disk)
        case("v3.0-71 ACCEPTANCE: the confirmed verify ADVANCES the "
             "engine-born view to verified-consumed in the same write as "
             "the stamp",
             "consumed_status: verified-consumed" in born_after
             and "consumed_status: legacy-assumed" not in born_after
             and "minted_by: engine" in born_after)
        case("v3.0-71: the advance is journaled at record time "
             "(consumed_status_advanced: true on the absorption_verified "
             "entry)",
             born_vrec["absorption_verified"][0].get(
                 "consumed_status_advanced") is True)
        born_probs, _ = crd.check_acc4(base, born_vres["sha"])
        case("v3.0-71: the stamp+advance verify commit still passes "
             "check-run-diff (derivation-only exemption holds for the "
             "composed write)", born_probs == [])

        # v3.0-71 fail-closed sweep: _advance_consumed_status refuses every
        # non-engine-born shape -- text byte-unchanged, never an error.
        def _region_text(minted_by_line, consumed="legacy-assumed"):
            return ("---\ntitle: T\n---\n\n"
                    "# --- derivation (engine-managed; strip region) ---\n"
                    "schema_version: 3\nview: topic\nsummary: \"s\"\n"
                    "entities: []\nstatus: active\ntier: T1\n"
                    "consumed_status: %s\n%s"
                    "origin_max: human\nsubscribes:\n  entities: []\n"
                    "  corpus: []\nbundle: [wiki/x.md]\nverified: null\n"
                    "# --- /derivation ---\n\n# X\nbody\n"
                    % (consumed,
                       (minted_by_line + "\n") if minted_by_line else ""))
        for shape, txt in (
                ("minted_by: backfill (F13/B3 audit debt stays open)",
                 _region_text("minted_by: backfill")),
                ("no minted_by key (pre-provenance region reads as legacy)",
                 _region_text("")),
                ("unknown minted_by value",
                 _region_text("minted_by: wat")),
                ("audit-pending never advances (F12 stands)",
                 _region_text("minted_by: engine", consumed="audit-pending")),
                ("already verified-consumed (idempotent)",
                 _region_text("minted_by: engine",
                              consumed="verified-consumed")),
                ("no derivation region",
                 "---\ntitle: T\n---\n\n# X\nbody\n")):
            out_txt, adv = _advance_consumed_status(txt)
            case("v3.0-71 fail-closed: %s -> no advance, text byte-unchanged"
                 % shape, adv is False and out_txt == txt)
        eng_txt = _region_text("minted_by: engine")
        adv_txt, adv = _advance_consumed_status(eng_txt)
        case("v3.0-71: engine + legacy-assumed -> the advance fires and "
             "EXACTLY the one line changes (every other byte untouched)",
             adv is True
             and adv_txt == eng_txt.replace(
                 "consumed_status: legacy-assumed",
                 "consumed_status: verified-consumed"))

        # unmintable shapes: unchanged text, NO new refusal
        NOFM_TEXT = "# No Frontmatter\n\n## Intro\nthe unfrontmattered claim\n"

        class NoFrontmatterBackend:
            def absorb(self, view_rel, view_text, events):
                secs = changed_sections(
                    base, _blob_of_text(base, view_text or ""),
                    _blob_of_text(base, NOFM_TEXT))
                e0 = sorted(events)[0]
                return {"new_text": NOFM_TEXT,
                        "manifest": [{"event": e0, "section": s}
                                     for s in sorted(secs)],
                        "corpus_support": [], "noops": []}

        plan_nofm = {"items": [
            {"view": "wiki/scoped/nofm.md", "events": ["raw/scoped/eb.md"],
             "event_class": {"raw/scoped/eb.md": {"class": "t3",
                                                  "origin": "explicit"}}}]}
        try:
            run(base, plan_nofm, NoFrontmatterBackend())
            nofm_disk = open(os.path.join(base, "wiki", "scoped", "nofm.md"),
                             encoding="utf-8").read()
            case("v3.0-69: a view with no parseable frontmatter is left "
                 "UNMINTED and raises no new refusal (already a "
                 "check-frontmatter finding; a guessed region would be "
                 "worse than a visible one)",
                 asm.DERIV_START not in nofm_disk
                 and "the unfrontmattered claim" in nofm_disk)
        except ValidationError as e:
            case("v3.0-69: a view with no parseable frontmatter is left "
                 "UNMINTED and raises no new refusal (raised %r)" % (e,),
                 False)

        # ===================================== v3.0-70(a): word anchoring
        _ship_events = {"raw/scoped/eb.md": "body"}
        _ship_old = "# S\n\n## Deliverable Dates\nold\n"

        def _ship_out(new_text, section):
            return {"new_text": new_text,
                    "manifest": [{"event": "raw/scoped/eb.md",
                                  "section": section}],
                    "corpus_support": [], "noops": []}

        try:
            r = validate_absorb_output(
                base, "wiki/ship.md", _ship_old,
                _ship_out("# S\n\n## Deliverable Dates\nnew dates\n",
                          "Deliverable Dates"), _ship_events)
            case("v3.0-70: 'Deliverable Dates' no longer trips the "
                 "shipped-state guard (it only ever matched the letters "
                 "l-i-v-e inside 'deliverable')", r is not None)
        except ValidationError as e:
            case("v3.0-70: 'Deliverable Dates' no longer trips the "
                 "shipped-state guard (raised %r)" % (e,), False)
        for heading in ("Delivery Schedule", "Olive oil sourcing",
                        "Lively debate", "Livelihood"):
            old_h = "# S\n\n## %s\nold\n" % heading
            try:
                validate_absorb_output(
                    base, "wiki/ship.md", old_h,
                    _ship_out("# S\n\n## %s\nnew\n" % heading, heading),
                    _ship_events)
                case("v3.0-70: %r passes the shipped-state guard" % heading,
                     True)
            except ValidationError:
                case("v3.0-70: %r passes the shipped-state guard" % heading,
                     False)
        # coverage NOT lost: real shipped-state headings still refuse
        for heading in ("Shipped state", "As-built notes",
                        "Currently live in production"):
            old_h = "# S\n\n## %s\nold\n" % heading
            try:
                validate_absorb_output(
                    base, "wiki/ship.md", old_h,
                    _ship_out("# S\n\n## %s\nnew\n" % heading, heading),
                    _ship_events)
                case("v3.0-70: %r STILL refuses without corpus_support "
                     "(no coverage lost)" % heading, False)
            except ValidationError as e:
                case("v3.0-70: %r STILL refuses without corpus_support "
                     "(no coverage lost)" % heading,
                     "shipped-state" in str(e))
        case("v3.0-70: the flagging site reads the SAME single-homed "
             "expression as the refusing site",
             not any(f["kind"] == "shipped-state-no-support" for f in
                     reconcile_flags([{"view": "wiki/s.md", "post_blob": "x",
                                       "events": [], "corpus_support": [],
                                       "manifest": [{"event": "e",
                                                     "section":
                                                     "Deliverables"}]}]))
             and any(f["kind"] == "shipped-state-no-support" for f in
                     reconcile_flags([{"view": "wiki/s.md", "post_blob": "x",
                                       "events": [], "corpus_support": [],
                                       "manifest": [{"event": "e",
                                                     "section":
                                                     "Shipped state"}]}])))
        # ---- v3.0.51 (v3.0-141): generation-tag minting + bare-citation refusals ----
        mint_root = tempfile.mkdtemp(prefix="cv2-mint-")
        try:
            os.makedirs(os.path.join(mint_root, "wiki", "topic"))
            os.makedirs(os.path.join(mint_root, "wiki", "cold", "x"))
            cited = ("---\ntitle: Cited\n---\n\n# Cited\n\nBody line.\n\n"
                     "# --- retirements\n"
                     '# {"seq":1,"i":0,"pre_hash":"a","post_hash":"b","span":[1,2],'
                     '"stub":[1,2],"shift":0,"target":"x","mode":"cold","title":"T"}\n'
                     "# --- /retirements\n")
            open(os.path.join(mint_root, "wiki", "topic", "cited.md"), "w",
                 encoding="utf-8", newline="\n").write(cited)
            open(os.path.join(mint_root, "wiki", "cold", "x", "cited.md"), "w",
                 encoding="utf-8", newline="\n").write("cold copy -- excluded\n")
            g8 = _gen_hash(cited)[:8]
            # parity pin: _gen_hash == retire.gen_hash (the verb's identity), block-stripped
            try:
                _retire_pin = _load("retire.py", "retire_v2_genpin")
                case("v3.0-141: _gen_hash is byte-parity with retire.gen_hash "
                     "(single generation identity)",
                     _retire_pin.gen_hash(cited) == _gen_hash(cited))
            except Exception:
                case("v3.0-141: retire.py loadable for the gen-hash parity pin", False)
            old_v = "# V\n\nsee cited.md:6 already here\n"
            new_v = old_v + "\nnew fact, see cited.md:6 for the source\n"
            minted = mint_citation_tags(mint_root, "wiki/v.md", old_v, new_v)
            case("v3.0-141: a NEW colon-form citation is MINTED with the cited view's "
                 "current gen8 (cold tier excluded from resolution)",
                 "new fact, see cited.md:6@%s for the source" % g8 in minted)
            case("v3.0-141: a PRE-EXISTING bare citation line is never rewritten "
                 "(the frozen-registry legacy population)",
                 "see cited.md:6 already here" in minted)
            # cross-vendor round-1 catch: a NEW line duplicating an old bare-citation
            # line is NEW (occurrence-counted, not set-membership) -- it is minted, and
            # unminted it refuses
            dup_new = old_v + "see cited.md:6 already here\n"
            dup_minted = mint_citation_tags(mint_root, "wiki/v.md", old_v, dup_new)
            case("v3.0-141: a NEW duplicate of an old bare-citation line is MINTED "
                 "(occurrence budget, not line-set membership)",
                 dup_minted.count("see cited.md:6 already here") == 1
                 and "see cited.md:6@%s already here" % g8 in dup_minted)
            try:
                check_new_citations_tagged(mint_root, "wiki/v.md", old_v, dup_new)
                case("v3.0-141: unminted duplicate refused", False)
            except ValidationError as e:
                case("v3.0-141: the unminted duplicate line is refused by the validator "
                     "too", "generation tag" in str(e))
            case("v3.0-141: minted output passes the validator's citation check",
                 (check_new_citations_tagged(mint_root, "wiki/v.md", old_v, minted)
                  is None))
            case("v3.0-141: an already-tagged citation is left alone (no double tag)",
                 mint_citation_tags(mint_root, "wiki/v.md", "",
                                    "see cited.md:6@deadbeef x\n")
                 == "see cited.md:6@deadbeef x\n")
            case("v3.0-141: a SELF-citation is exempt from minting",
                 mint_citation_tags(mint_root, "wiki/topic/cited.md", "",
                                    "see cited.md:6 here\n") == "see cited.md:6 here\n")
            case("v3.0-141: a basename matching no wiki view is not a view citation "
                 "(left bare, passes)",
                 mint_citation_tags(mint_root, "wiki/v.md", "", "see ghost.md:3\n")
                 == "see ghost.md:3\n"
                 and check_new_citations_tagged(mint_root, "wiki/v.md", "",
                                               "see ghost.md:3\n") is None)
            try:
                check_new_citations_tagged(mint_root, "wiki/v.md", old_v, new_v)
                case("v3.0-141: unminted bare citation refused", False)
            except ValidationError as e:
                case("v3.0-141: a new bare colon citation reaching validation UNMINTED "
                     "is refused naming the tag shape", "generation tag" in str(e))
            try:
                check_new_citations_tagged(mint_root, "wiki/v.md", "",
                                           "per cited.md lines 6 and 7\n")
                case("v3.0-141: prose-form refused", False)
            except ValidationError as e:
                case("v3.0-141: a new PROSE-form citation ('lines N') of a wiki view is "
                     "refused toward the tagged colon form", "PROSE-form" in str(e))
            # cross-vendor round-2 catch: the prose window is FULL-TEXT (newlines
            # included), the same association check-split's grammar uses -- a basename
            # on one new line with its 'lines N' continuation on the NEXT line refuses
            try:
                check_new_citations_tagged(mint_root, "wiki/v.md", "",
                                           "per cited.md\nlines 6 and 7 above\n")
                case("v3.0-141: cross-line prose citation refused", False)
            except ValidationError as e:
                case("v3.0-141: a CROSS-LINE prose citation (basename, newline, "
                     "'lines N') is refused -- window parity with check-split",
                     "PROSE-form" in str(e))
            os.makedirs(os.path.join(mint_root, "wiki", "other"))
            open(os.path.join(mint_root, "wiki", "other", "cited.md"), "w",
                 encoding="utf-8", newline="\n").write("# twin\n")
            case("v3.0-141: an ambiguous basename is NOT minted",
                 mint_citation_tags(mint_root, "wiki/v.md", "", "see cited.md:6\n")
                 == "see cited.md:6\n")
            try:
                check_new_citations_tagged(mint_root, "wiki/v.md", "", "see cited.md:6\n")
                case("v3.0-141: ambiguous refused", False)
            except ValidationError as e:
                case("v3.0-141: an ambiguous basename is refused (nothing can mint it)",
                     "AMBIGUOUS" in str(e))
        finally:
            shutil.rmtree(mint_root, ignore_errors=True)

        # ---- ADR #11 Release 3 (v3.0.52): section-scoped absorb + the cap-debt brake
        out_s = apply_section_scoped("## A\n\nold body.\n\n## B\n\nkeep.\n",
                                     {"sections": {"A": "## A\n\nnew body.\n"}})
        case("Release 3: {'sections': ...} output is spliced by the ENGINE into new_text "
             "(retire-manifest.splice_sections, the retirement gate's span grammar)",
             out_s.get("sections") and "new body." in out_s["new_text"]
             and "old body." not in out_s["new_text"] and "keep." in out_s["new_text"])
        try:
            apply_section_scoped("## A\n\nx.\n", {"sections": {"Ghost": "## Ghost\n\ny\n"}})
            case("Release 3: unknown section refused", False)
        except ValidationError as e:
            case("Release 3: a splice naming an unknown section refuses pre-journal",
                 "no span titled" in str(e))
        try:
            apply_section_scoped("## A\n\nx.\n", {"sections": {"A": "## A\n\ny\n"},
                                                  "new_text": "z"})
            case("Release 3: both shapes refused", False)
        except ValidationError as e:
            case("Release 3: output carrying BOTH new_text and sections refuses",
                 "BOTH" in str(e))
        r3root = tempfile.mkdtemp(prefix="cv2-release3-")
        dbt = _debt()
        try:
            def g3(*a):
                return subprocess.run(["git", "-C", r3root] + list(a), capture_output=True)
            g3("init", "-q", "-b", "main")
            g3("config", "user.email", "t@t")
            g3("config", "user.name", "t")
            g3("config", "commit.gpgsign", "false")
            os.makedirs(os.path.join(r3root, "wiki", "topic"))
            REG3 = ("# --- derivation (engine-managed; strip region) ---\n"
                    "schema_version: 3.2\nview: topic\nview_id: v-brake\n"
                    "# --- /derivation ---\n")
            pad3 = "x" * 300
            over_old = "---\ntitle: b\n---\n" + REG3 + "\n## Sec\n\n" + pad3 + "\n"
            with open(os.path.join(r3root, "wiki", "topic", "over.md"), "w",
                      encoding="utf-8", newline="\n") as fh:
                fh.write(over_old)
            with open(os.path.join(r3root, "project.yaml"), "w", encoding="utf-8",
                      newline="\n") as fh:
                fh.write("trust_surface_signing: visible\n")
            g3("add", "-A")
            g3("commit", "-q", "-m", "seed over-cap view")
            dbt._CAPS_OVERRIDE = {"topic": 200, "default": 200}
            grown = over_old.replace(pad3, pad3 + "yyyy")
            out_g = {"new_text": grown, "manifest": [{"event": "raw/e.md",
                                                      "section": "Sec"}]}
            try:
                validate_absorb_output(r3root, "wiki/topic/over.md", over_old, out_g,
                                       {"raw/e.md": "e"})
                case("Release 3: brake growth refusal missing", False)
            except ValidationError as e:
                case("Release 3: the BRAKE refuses an ordinary absorb that grows a view "
                     "under an open cap episode, naming the outs (condition 7)",
                     "condition 7" in str(e) and "--splice" in str(e))
            shrunk = over_old.replace(pad3, pad3[:-8])
            out_ok = {"new_text": shrunk, "manifest": [{"event": "raw/e.md",
                                                        "section": "Sec"}]}
            case("Release 3: the brake allows a net-shrinking absorb of the same view "
                 "(the episode stays open; growth is what refuses)",
                 validate_absorb_output(r3root, "wiki/topic/over.md", over_old, out_ok,
                                        {"raw/e.md": "e"}) is not None)
            # cross-vendor round-2 fold (c3): the degradation policy is fault-injected,
            # both directions -- an unreadable CAP TABLE degrades exactly as check-caps
            # does (the cap sensor owns that report); any OTHER computation error
            # refuses (fail-closed: an unanswerable brake question is not permission)
            saved_brake = dbt.brake

            def _capcfg_err(*_a, **_k):
                raise RuntimeError("cap config has no 'default' cap")

            dbt.brake = _capcfg_err
            try:
                case("Release 3 (r2 fold): an unreadable cap table DEGRADES -- the "
                     "growth absorb passes and the cap sensor owns the finding",
                     validate_absorb_output(r3root, "wiki/topic/over.md", over_old,
                                            out_g, {"raw/e.md": "e"}) is not None)
            finally:
                dbt.brake = saved_brake

            def _boom(*_a, **_k):
                raise RuntimeError("boom")

            dbt.brake = _boom
            try:
                try:
                    validate_absorb_output(r3root, "wiki/topic/over.md", over_old,
                                           out_g, {"raw/e.md": "e"})
                    case("Release 3 (r2 fold): brake error fail-closed", False)
                except ValidationError as e:
                    case("Release 3 (r2 fold): any OTHER brake computation error "
                         "REFUSES the absorb (fail-closed)", "fail-closed" in str(e))
            finally:
                dbt.brake = saved_brake
        finally:
            dbt._CAPS_OVERRIDE = None
            shutil.rmtree(r3root, ignore_errors=True)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    if _RENDERED.get("union_packet"):
        print("\n----- RENDERED UNION VERIFY PACKET (fixture a) -----")
        print(_RENDERED["union_packet"])
        print("----- END RENDERED UNION VERIFY PACKET -----\n")

    if failed:
        print("compile-v2 orchestration: FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("compile-v2 orchestration: PASS (%d/%d)" % (total, total))
    return 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    if "--run" in argv:
        root = argv[argv.index("--root") + 1]
        plan = json.load(open(argv[argv.index("--plan") + 1], encoding="utf-8"))
        rt = argv[argv.index("--run-type") + 1] if "--run-type" in argv \
            else "compile"
        try:
            res = run(root, plan, FixtureAbsorbBackend(),
                      run_type=rt, break_stale="--break-stale-lock" in argv)
        except core.LockHeld as e:
            print("LOCK HELD: %s" % e)
            return core.EXIT_LOCK_HELD
        except ValidationError as e:
            print("VALIDATION FAIL: %s" % e)
            return 1
        print(json.dumps(res, indent=1))
        return 0
    print(__doc__.strip().split("\n\n")[-1])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
