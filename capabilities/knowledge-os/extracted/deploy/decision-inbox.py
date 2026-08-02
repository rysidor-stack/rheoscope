#!/usr/bin/env python3
"""decision-inbox.py -- the decision inbox projection generator (harness-backlog.md v3.0-38).

A PROJECTION GENERATOR, like a census: derived, regenerated, never hand-edited. The
sources listed below always win -- this script only ever reads them and writes one
output file, DECISIONS-PENDING.md, at the project root.

SOURCES scanned (each degrades silently-with-a-note when absent -- no source is
required for this script to run clean):

  0. receipts/candidates/ (the R-1 session-candidate ledger; spec harness-v3.0/specs/
     r1-build-decisions-2026-07-22.md Part 7) -- every candidate whose EFFECTIVE
     (highest-seq) state is 'registered' and whose proposed_disposition is
     'transcribe-as-operator-event' -> 'Session candidate <id>: "<verbatim span,
     truncated>"'. Listed FIRST (see _SECTIONS): the operator's "yes" here is the one
     that actually starts something (an operator-signed commit that arms a drafted
     raw/ artifact), so it leads the file. deploy/candidates.py is imported lazily,
     from inside scan_candidates only -- an instance that has never harvested a
     candidate, or whose candidates machinery is absent/broken, degrades to zero
     items plus a note, never a crash.
  1. harness-backlog.md -- entries ("### v<X>-<N> -- <title>") whose body matches
     the words operator-gated (either all-lowercase-with-separator or ALL-CAPS) and
     does NOT contain the word RESOLVED anywhere in the entry -> one pending item
     per entry: "Backlog <id>: <title>".
  2. SWEEP-BRIEFING.md -- numbered lines under a "Needs your attention" heading;
     the first sentence of each is captured -> "Sweep finding: <sentence> (sweep
     dated <file date>)". The file's own date is its filesystem modified-date.
     If the heading is present with non-empty content beneath it but the parser
     recognizes zero numbered items there, that parse failure is itself surfaced
     as a single drift item -- never silently dropped (see _SWEEP_DRIFT_TEXT).
  3. wiki/flight-plans/*.md -- lines shaped like a Blocker field (optionally
     bulleted, optionally bold, e.g. "**Blocker:** ...") whose value is not empty
     and is not exactly "None" -> "Flight-plan blocker (<file stem>): <value>".
  4. Any root-level *.md file or any wiki/*.md file (one level deep only, not
     wiki/flight-plans/) containing the literal marker "DECISION-PENDING:" on a
     line -> "Flagged: <first 200 characters after the marker>". DECISIONS-
     PENDING.md and SWEEP-BRIEFING.md are excluded from this scan even when they
     sit at the root -- both are projections/reports this script itself produces
     or consumes, never sources, and scanning either back would risk a
     self-reference loop.

Output shape: an HTML header comment (never hand-edit; the sources win; the
regeneration date) followed by "# Waiting on you" and one markdown section per
source class that actually produced an item, each item a checkbox line with a
"(details: <path>)" tail pointing back at its source. When nothing is pending the
file is exactly the header plus the single line "Nothing is waiting on you. All
clear."

AGE TRACKING (harness-backlog.md v3.0-39 item 2): every rendered item also carries
a plain age tail -- "-- new today" or "-- waiting N days" -- backed by a sidecar,
receipts/desk/inbox-first-seen.json, that survives regeneration (append-only: a
key is stamped once, kept, and moved to a `retired` sub-map when its item stops
appearing -- never deleted). STABLE IDENTITY per source class (the most durable
field each scanner already has, documented beside each scan_* function below):
  0. candidate  -- the ledger's own candidate_id (already the ledger's primary key)
  1. backlog    -- the entry id (e.g. "v3.0-38"), the heading's own stable handle
  2. sweep      -- a hash of the finding SENTENCE only (never the date suffix, so
                   the same finding keeps the same identity across sweep runs);
                   the one-per-file format-drift item uses a fixed singleton key
  3. flight_plan -- the file stem plus a hash of the Blocker value text
  4. marker     -- the file's root-relative path plus a hash of the flagged text
This sidecar is written ONLY by a real regeneration (do_generate); --check reads
it (to render what regeneration WOULD show) but never writes it -- the same
read-only/write-side split as the rest of this project's projections.

Usage:
  decision-inbox.py [--root PATH]     regenerate DECISIONS-PENDING.md at PATH
                                       (default: current directory)
  decision-inbox.py --check           regenerate to memory only and diff against
                                       the existing DECISIONS-PENDING.md; exit 0 if
                                       current, 1 with a note if stale or missing
  decision-inbox.py --self-test       run embedded hermetic fixtures; exit 0 if all
                                       pass
  decision-inbox.py --date YYYY-MM-DD pin the "Regenerated:" date instead of using
                                       today (datetime.date.today())

Exit codes: 0 = wrote (or matched, under --check) cleanly | 1 = --check found the
file stale/missing, or a self-test failure.
"""

import datetime
import hashlib
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(basename, alias):
    """Sibling-import by path -- deploy/ is a flat directory, never a package (the same
    mechanism deploy/candidates.py itself uses for compile-core.py/origin.py). Used ONLY
    by scan_candidates (Source 0, R-1 candidate section, see build-decisions Part 7),
    called LAZILY from inside that function -- never at module import time -- so an
    instance whose deploy/candidates.py is absent, stripped, or broken leaves this whole
    script fully functional; only the candidate section degrades to zero items."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(alias, os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Regexes (stdlib-only; ASCII source -- non-ASCII characters are always the
# escaped \uXXXX form, never a literal byte in this file).
# ---------------------------------------------------------------------------

# The heading separator is an em dash (codepoint 0x2014) in real entries, or
# ASCII "--" in ASCII-authored ones (same either-spelling tolerance
# check-manifest.py's OPEN marker uses) -- built from chr(0x2014), never a
# literal byte, to keep this source file itself ASCII-only.
_EM_DASH = chr(0x2014)
_BACKLOG_HEADING_RE = re.compile(
    r"^###\s+(v[\w.]+-\d+)\s*(?:" + _EM_DASH + r"|--)\s*(.*)$")
_OPERATOR_GATED_RE = re.compile(r"operator.gated|OPERATOR.GATED")
_RESOLVED_MARKER = "RESOLVED"

_ATTENTION_HEADING_RE = re.compile(
    r"^[#>\s]*\**\s*needs your attention\s*:?\s*\**\s*$", re.IGNORECASE)
_BOLD_HEADING_RE = re.compile(r"^\s*\*\*[^*]+\*\*\s*:?\s*$")
_MD_HEADING_RE = re.compile(r"^\s*#")
_NUMBERED_RE = re.compile(r"^\s*\d+\.\s+(.*)$")
_SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)")

# Real flight-plan authoring bolds the field name AND its colon together
# ("**Blocker:**"), so a closing "**" commonly falls right after the colon
# rather than before it -- the trailing \**\s* absorbs that so the captured
# value is clean prose, not "** <value>".
_BLOCKER_RE = re.compile(r"^\s*[-*]?\s*\**Blocker\**\s*:\s*\**\s*(.*)$")

_MARKER_TOKEN = "DECISION-PENDING:"

_HEADER_TMPL = ("<!-- DERIVED PROJECTION -- regenerated by deploy/decision-inbox.py; "
                "never hand-edit; the sources win. Regenerated: %s. -->")
_ALL_CLEAR_LINE = "Nothing is waiting on you. All clear."
_OUTPUT_NAME = "DECISIONS-PENDING.md"
_SWEEP_BRIEFING_NAME = "SWEEP-BRIEFING.md"

# ---------------------------------------------------------------------------
# First-seen age tracking (v3.0-39 item 2). See the module docstring's "AGE
# TRACKING" section for the identity-key choice per source class.
# ---------------------------------------------------------------------------

_SIDECAR_REL_PATH = os.path.join("receipts", "desk", "inbox-first-seen.json")
# The one-per-file sweep format-drift item (fix B1) has no content of its own to
# hash -- it's a singleton alert, so a fixed key stands in for a hash.
_SWEEP_DRIFT_IDENTITY = "sweep:drift"


def _short_hash(text):
    """A short, stable hex digest of text -- the identity-key ingredient for
    source classes with no natural id of their own (sweep findings, flight-plan
    blockers, generic markers). sha256, truncated to 16 hex chars: ample
    collision resistance for the handful of items one inbox ever holds, short
    enough to keep the sidecar JSON readable by a human."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

# Emitted by scan_sweep when the "Needs your attention" heading is present with
# non-empty content beneath it but the parser recognized zero numbered items in
# that content -- a format-drift signal, never a silent drop (fix B1). Built
# from _EM_DASH, not a literal byte, per this file's ASCII-only-source rule.
_SWEEP_DRIFT_TEXT = (
    "The sweep briefing has attention items this inbox could not parse " +
    _EM_DASH +
    " read SWEEP-BRIEFING.md directly and report the format drift")

_SECTIONS = (
    # Placed FIRST (build-decisions Part 7): a candidate awaiting confirmation is the
    # single most actionable thing in this file -- it is a drafted-but-unarmed artifact
    # one signed commit away from becoming truth, not a passive flag someone else should
    # notice eventually. The operator reads top-down, so it leads.
    ("candidate", "Session candidates awaiting your confirmation"),
    ("backlog", "Backlog items awaiting a decision"),
    ("sweep", "From the last sweep"),
    ("flight_plan", "Flight-plan blockers"),
    ("marker", "Flagged in a document"),
)


def _first_sentence(text):
    """The first sentence of text (through its terminal . ! or ?), or the whole
    (stripped) text if no sentence-ending punctuation is found."""
    text = text.strip()
    m = _SENTENCE_END_RE.search(text)
    if m:
        return text[:m.end()]
    return text


def _is_none_ish(value):
    """True for an empty Blocker value, or one that is exactly "None" (trailing
    period ignored, case-insensitive) -- the two forms this source excludes."""
    v = value.strip()
    if not v:
        return True
    return v.rstrip(".").strip().lower() == "none"


def _file_date(path):
    """A source file's own date -- its filesystem last-modified date, as an ISO
    YYYY-MM-DD string. Used for sources (like a sweep briefing) with no reliable
    in-body date stamp of their own."""
    ts = os.path.getmtime(path)
    return datetime.date.fromtimestamp(ts).isoformat()


def _rel(path, root):
    """A root-relative display path, forward-slash always (Windows-safe display,
    matches this codebase's repo-relative path convention)."""
    return os.path.relpath(path, root).replace(os.sep, "/")


# ---------------------------------------------------------------------------
# Source 0: the R-1 session-candidate ledger (receipts/candidates/, staged files under
# intake/session-candidates/). Spec: harness-v3.0/specs/r1-build-decisions-2026-07-22.md
# Part 7.
# ---------------------------------------------------------------------------

_CANDIDATE_SPAN_PREVIEW_LEN = 140


def _candidate_span_preview(cand, root, event_path):
    """The verbatim span from a staged candidate file, truncated to a single readable
    line with an ellipsis when it runs long, newlines/repeated whitespace collapsed to
    single spaces (a multi-line span must not break the checkbox-line output format).
    The operator must see the actual words before saying yes (build-decisions Part 7) --
    a bare candidate id or a count is not enough.

    Degrades to a bracketed explanatory placeholder on ANY failure reading or parsing
    THIS ONE candidate's file (missing file, PyYAML absent, a tampered/malformed staged
    file) -- never raises. scan_candidates' own degrade-to-zero-items posture is for a
    whole-scan failure (the ledger itself unreadable); one candidate's unreadable file
    must not take the rest of the section down with it."""
    if not event_path:
        return "[no staged file recorded for this candidate]"
    full_path = os.path.join(root, *event_path.split("/"))
    if not os.path.isfile(full_path):
        return "[staged file missing -- see check-candidates.py for the diagnosis]"
    try:
        _meta, span = cand.validate_candidate_file(full_path)
    except Exception as e:
        return "[could not read the verbatim span: %s: %s]" % (type(e).__name__, e)
    collapsed = " ".join(span.split())
    if len(collapsed) > _CANDIDATE_SPAN_PREVIEW_LEN:
        return collapsed[:_CANDIDATE_SPAN_PREVIEW_LEN].rstrip() + "..."
    return collapsed


def scan_candidates(root, notes):
    """Every session candidate whose EFFECTIVE (highest-seq) ledger state is
    'registered' and whose proposed_disposition is 'transcribe-as-operator-event' -- the
    one candidate class where a "yes" from the operator actually starts something (an
    operator-signed commit that arms a drafted raw/ artifact; see promote-candidate.py).
    Every other state/disposition combination is either not yet actionable or already
    resolved, so it stays out of this section.

    ROBUSTNESS (this scanner runs on EVERY sweep, including instances that have never
    harvested a candidate at all -- it must degrade to zero items plus a note on any
    failure, never a crash, never a traceback that takes the whole inbox down):
      - deploy/candidates.py is imported LAZILY here, via the _load() path-import idiom,
        called only from inside this function -- never at module import time.
      - a missing receipts/candidates/ directory is reported as a NOTE (matching every
        other source's absent-source convention) and yields zero items, not an error.
      - a broken/tampered chain, an import failure, or any other exception from the
        candidates module is caught here and turned into one explanatory NOTE, never
        propagated -- this scanner's own broken state must never crash the file every
        other source's items depend on. (check-candidates.py is the dedicated sensor for
        diagnosing a broken candidate ledger; this scanner only needs to not die on one.)
    """
    items = []
    try:
        cand = _load("candidates.py", "candidates_decision_inbox")
    except Exception as e:
        notes.append(
            "NOTE: session-candidate machinery (deploy/candidates.py) could not be "
            "loaded (%s: %s) -- candidate section skipped" % (type(e).__name__, e))
        return items

    ledger_dir = cand.candidates_ledger_dir(root)
    if not os.path.isdir(ledger_dir):
        notes.append("NOTE: receipts/candidates/ absent -- nothing to check")
        return items

    try:
        effective = cand.load_candidate_ledger(root)
    except Exception as e:
        notes.append(
            "NOTE: the session-candidate ledger at receipts/candidates/ could not be "
            "read (%s: %s) -- candidate section skipped (run check-candidates.py for "
            "the full diagnosis)" % (type(e).__name__, e))
        return items

    for candidate_id in sorted(effective):
        rec = effective[candidate_id]
        if rec.get("state") != "registered":
            continue
        if rec.get("proposed_disposition") != "transcribe-as-operator-event":
            continue
        event_path = rec.get("event", "")
        preview = _candidate_span_preview(cand, root, event_path)
        items.append((
            'Session candidate %s: "%s"' % (candidate_id, preview),
            event_path or "receipts/candidates/",
            # Identity: the ledger's own candidate_id -- already that ledger's
            # primary key (candidates.py's effective-state lookup is keyed on
            # it), so it is stable for the life of the candidate regardless of
            # how the preview text or event path render.
            "candidate:%s" % candidate_id,
        ))
    return items


# ---------------------------------------------------------------------------
# Source 1: harness-backlog.md.
# ---------------------------------------------------------------------------

def scan_backlog(root, notes):
    path = os.path.join(root, "harness-backlog.md")
    if not os.path.isfile(path):
        notes.append("NOTE: harness-backlog.md absent -- nothing to check")
        return []
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        lines = fh.read().splitlines()
    n = len(lines)
    headings = []
    for i, ln in enumerate(lines):
        m = _BACKLOG_HEADING_RE.match(ln)
        if m:
            headings.append((i, m.group(1), m.group(2).strip()))
    items = []
    for idx, (line_i, entry_id, title) in enumerate(headings):
        end = headings[idx + 1][0] if idx + 1 < len(headings) else n
        body = "\n".join(lines[line_i + 1:end])
        if _OPERATOR_GATED_RE.search(body) and _RESOLVED_MARKER not in body:
            items.append((
                "Backlog %s: %s" % (entry_id, title), "harness-backlog.md",
                # Identity: the entry id (e.g. "v3.0-38") -- the heading's own
                # stable handle, unaffected by later title edits.
                "backlog:%s" % entry_id,
            ))
    return items


# ---------------------------------------------------------------------------
# Source 2: SWEEP-BRIEFING.md.
# ---------------------------------------------------------------------------

def scan_sweep(root, notes):
    path = os.path.join(root, _SWEEP_BRIEFING_NAME)
    if not os.path.isfile(path):
        notes.append("NOTE: SWEEP-BRIEFING.md absent -- nothing to check")
        return []
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        text = fh.read()
    date_str = _file_date(path)
    items = []
    in_section = False
    saw_content = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if not in_section:
            if _ATTENTION_HEADING_RE.match(stripped):
                in_section = True
            continue
        if _BOLD_HEADING_RE.match(raw) or _MD_HEADING_RE.match(raw):
            break
        if stripped:
            saw_content = True
        m = _NUMBERED_RE.match(raw)
        if m:
            sentence = _first_sentence(m.group(1))
            items.append((
                "Sweep finding: %s (sweep dated %s)" % (sentence, date_str),
                _SWEEP_BRIEFING_NAME,
                # Identity: a hash of the SENTENCE only, never the "(sweep dated
                # ...)" suffix -- the date changes every run even when the same
                # finding persists, and first-seen tracking needs the finding's
                # identity to survive that daily churn.
                "sweep:%s" % _short_hash(sentence),
            ))
    if in_section and saw_content and not items:
        # Fix B1: the heading is there, there's content under it, but nothing this
        # parser recognizes -- surface the parse failure itself rather than
        # silently reporting "nothing to see."
        items.append((_SWEEP_DRIFT_TEXT, _SWEEP_BRIEFING_NAME, _SWEEP_DRIFT_IDENTITY))
    return items


# ---------------------------------------------------------------------------
# Source 3: wiki/flight-plans/*.md.
# ---------------------------------------------------------------------------

def scan_flight_plans(root, notes):
    dir_path = os.path.join(root, "wiki", "flight-plans")
    if not os.path.isdir(dir_path):
        notes.append("NOTE: wiki/flight-plans/ absent -- nothing to check")
        return []
    items = []
    for name in sorted(os.listdir(dir_path)):
        if not name.endswith(".md"):
            continue
        fpath = os.path.join(dir_path, name)
        if not os.path.isfile(fpath):
            continue
        stem = os.path.splitext(name)[0]
        with open(fpath, "r", encoding="utf-8-sig", errors="replace") as fh:
            for raw in fh:
                m = _BLOCKER_RE.match(raw.rstrip("\n").rstrip("\r"))
                if not m:
                    continue
                value = m.group(1).strip()
                if _is_none_ish(value):
                    continue
                items.append((
                    "Flight-plan blocker (%s): %s" % (stem, value),
                    "wiki/flight-plans/%s" % name,
                    # Identity: file stem + a hash of the Blocker value text --
                    # there is no other id, and a changed blocker value is
                    # legitimately a different item (its own first-seen date).
                    "flight_plan:%s:%s" % (stem, _short_hash(value)),
                ))
    return items


# ---------------------------------------------------------------------------
# Source 4: generic DECISION-PENDING: marker in root-level *.md and wiki/*.md.
# ---------------------------------------------------------------------------

def _scan_marker_file(fpath, root, items):
    rel_path = _rel(fpath, root)
    with open(fpath, "r", encoding="utf-8-sig", errors="replace") as fh:
        for raw in fh:
            idx = raw.find(_MARKER_TOKEN)
            if idx == -1:
                continue
            text = raw[idx + len(_MARKER_TOKEN):][:200].strip()
            items.append((
                "Flagged: %s" % text, rel_path,
                # Identity: root-relative path + a hash of the flagged text --
                # there is no other id, and a changed flagged text is
                # legitimately a different item (its own first-seen date).
                "marker:%s:%s" % (rel_path, _short_hash(text)),
            ))


def scan_markers(root, notes):
    items = []
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            # Fix B2: DECISIONS-PENDING.md (this script's own output) and
            # SWEEP-BRIEFING.md (another projection this script only reads
            # elsewhere, via scan_sweep) are both reports, never sources --
            # scanning either back into the marker source would risk a
            # self-reference loop.
            if not name.endswith(".md") or name in (_OUTPUT_NAME, _SWEEP_BRIEFING_NAME):
                continue
            fpath = os.path.join(root, name)
            if os.path.isfile(fpath):
                _scan_marker_file(fpath, root, items)
    wiki_dir = os.path.join(root, "wiki")
    if os.path.isdir(wiki_dir):
        for name in sorted(os.listdir(wiki_dir)):
            if not name.endswith(".md"):
                continue
            fpath = os.path.join(wiki_dir, name)
            if os.path.isfile(fpath):
                _scan_marker_file(fpath, root, items)
    else:
        notes.append("NOTE: wiki/ absent -- marker scan skipped for wiki/*.md")
    return items


# ---------------------------------------------------------------------------
# FYI source (v3.0-78, handoff collapse): handoff folders parked at
# `status: answered, close: pending` -- a T1 headless close leg died and the
# close auto-retries on the next /handoff invocation or nightly standing-loop
# tick. The spec's contract: visible here AS INFORMATION, never as a task --
# so these render in their own no-checkbox FYI section, are excluded from the
# waiting-on-you total (the all-clear line still shows), and carry no
# first-seen sidecar identity (nothing is waiting on the operator).
# Line-level parse, not YAML: meta.yaml is machine-written by /handoff, and
# this projection must not require PyYAML (module-wide degrade posture).
# ---------------------------------------------------------------------------

_HANDOFF_STATUS_ANSWERED_RE = re.compile(r"(?m)^status:\s*answered\b")
_HANDOFF_CLOSE_PENDING_RE = re.compile(r"(?m)^close:\s*pending\b")


def scan_handoff_closes(root, notes):
    """[(line_text, detail_rel_path)] for every handoff folder whose meta.yaml
    carries both `status: answered` and `close: pending`. Checks the two
    envelope homes this codebase uses (handoffs/, core/handoffs/); an absent
    envelope is silent (most projects have exactly one)."""
    items = []
    for env in ("handoffs", os.path.join("core", "handoffs")):
        hdir = os.path.join(root, env)
        if not os.path.isdir(hdir):
            continue
        for entry in sorted(os.listdir(hdir)):
            mpath = os.path.join(hdir, entry, "meta.yaml")
            if not os.path.isfile(mpath):
                continue
            try:
                with open(mpath, "r", encoding="utf-8-sig", errors="replace") as fh:
                    text = fh.read()
            except OSError as e:
                notes.append("NOTE: unreadable meta.yaml skipped: %s (%s)" % (mpath, e))
                continue
            if (_HANDOFF_CLOSE_PENDING_RE.search(text)
                    and _HANDOFF_STATUS_ANSWERED_RE.search(text)):
                items.append((
                    ("Handoff %s: T1 close leg parked (close: pending) " % entry)
                    + _EM_DASH
                    + " auto-retried by the next /handoff invocation or nightly tick;"
                      " nothing for you to do",
                    "%s/%s/meta.yaml" % (env.replace(os.sep, "/"), entry),
                ))
    return items


_FYI_SECTION_TITLE = ("FYI " + _EM_DASH +
                      " parked handoff closes (auto-retrying; not waiting on you)")


# ---------------------------------------------------------------------------
# First-seen sidecar: load, compute, write. Load and compute are read-only
# (safe for --check); write is do_generate's alone.
# ---------------------------------------------------------------------------

def _sidecar_path(root):
    return os.path.join(root, _SIDECAR_REL_PATH)


def _load_sidecar(root, notes):
    """Read receipts/desk/inbox-first-seen.json. A MISSING file is normal (first
    generation ever, or a fresh clone) and degrades silently to an empty tracker
    -- no NOTE, matching this script's other absent-source sources. A file that
    EXISTS but fails to parse as the expected {"items": {...}, "retired": {...}}
    shape (corrupt JSON, truncated write, hand-edited into something else) also
    degrades to the same empty tracker, but that is unexpected, so it DOES
    surface a NOTE -- the operator should know ages reset this run."""
    path = _sidecar_path(root)
    empty = {"items": {}, "retired": {}}
    if not os.path.isfile(path):
        return empty
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("sidecar root is not a JSON object")
        items = data.get("items", {})
        retired = data.get("retired", {})
        if not isinstance(items, dict) or not isinstance(retired, dict):
            raise ValueError("'items'/'retired' are not JSON objects")
        return {"items": dict(items), "retired": dict(retired)}
    except Exception as e:
        notes.append(
            "NOTE: receipts/desk/inbox-first-seen.json is unreadable (%s: %s) -- "
            "regenerating it fresh; item ages reset to 'new today' this run"
            % (type(e).__name__, e))
        return empty


def _compute_sidecar_update(existing, current_keys, date_str):
    """Given the sidecar as loaded (never mutated -- a fresh dict is returned)
    and the identity keys present in THIS generation, returns the updated
    sidecar: a new key is stamped date_str; a key already tracked keeps its
    stored first-seen date untouched; a key no longer present moves into
    'retired' (append-only -- a retirement record is ADDED, nothing is ever
    deleted); a key that reappears after being retired is REVIVED into 'items'
    using its ORIGINAL first-seen date -- the date it was actually first seen
    survives a retire/revive cycle rather than resetting to "today" just
    because the item dropped out for a run or two. Revival is the one
    exception to 'append-only': the retired ENTRY itself is popped out of
    'retired' and moved back into 'items' (removed from 'retired', never
    left behind as a duplicate) -- so no first_seen date is ever lost, in
    either direction."""
    old_items = existing.get("items", {})
    old_retired = existing.get("retired", {})
    current_set = set(current_keys)

    new_items = {}
    new_retired = dict(old_retired)  # append-only: start from what's already there

    for key in current_keys:
        if key in old_items:
            new_items[key] = old_items[key]
        elif key in new_retired:
            record = new_retired.pop(key)
            first_seen = date_str
            if isinstance(record, dict) and record.get("first_seen"):
                first_seen = record["first_seen"]
            new_items[key] = first_seen
        else:
            new_items[key] = date_str

    for key, first_seen in old_items.items():
        if key not in current_set:
            new_retired[key] = {"first_seen": first_seen, "retired": date_str}

    return {"items": new_items, "retired": new_retired}


def _write_sidecar(root, sidecar):
    """Persist the sidecar. Called ONLY from do_generate -- --check and the
    in-process self-test's plain gen() helper must never reach this."""
    path = _sidecar_path(root)
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    payload = json.dumps(sidecar, indent=2, sort_keys=True) + "\n"
    with open(path, "wb") as fh:
        fh.write(payload.encode("utf-8"))


def _age_tail(date_str, first_seen):
    """The plain-English age suffix appended to a rendered item line: "new
    today" for an item first seen on this same generation date, or "waiting N
    days" for one that has persisted. Never raises -- a missing/unparseable
    first-seen date (should not happen given _compute_sidecar_update's
    guarantee that every current key is present, but defended anyway per this
    module's degrade-never-crash convention) renders as "new today" rather than
    taking the whole generation down."""
    days = None
    if first_seen:
        try:
            days = (datetime.date.fromisoformat(date_str)
                    - datetime.date.fromisoformat(first_seen)).days
        except Exception:
            days = None
    if days is None or days <= 0:
        return " " + _EM_DASH + " new today"
    return " " + _EM_DASH + " waiting %d day%s" % (days, "" if days == 1 else "s")


# ---------------------------------------------------------------------------
# Assembly.
# ---------------------------------------------------------------------------

def generate_content(root, date_str):
    """Returns (content_str, notes_list, sidecar_update). content_str always ends
    with a single trailing newline; notes_list is printed by the caller, never
    embedded in the file itself (mirrors this project's other sensors: absence
    is a stdout NOTE, not a finding baked into the artifact).

    sidecar_update is the in-memory receipts/desk/inbox-first-seen.json state
    this generation implies (see _compute_sidecar_update) -- this function only
    READS the on-disk sidecar (via _load_sidecar) to compute item ages for
    rendering; it never writes anything. Persisting sidecar_update is
    do_generate's job alone: --check calls this same function to compute what
    regeneration WOULD produce (content included) without writing either
    output file."""
    notes = []
    if not os.path.isdir(root):
        notes.append("NOTE: root path not found: %s" % root)
        by_class = {key: [] for key, _ in _SECTIONS}
        fyi_items = []
    else:
        by_class = {
            "candidate": scan_candidates(root, notes),
            "backlog": scan_backlog(root, notes),
            "sweep": scan_sweep(root, notes),
            "flight_plan": scan_flight_plans(root, notes),
            "marker": scan_markers(root, notes),
        }
        fyi_items = scan_handoff_closes(root, notes)

    current_keys = [identity for key, _ in _SECTIONS
                    for (_line, _detail, identity) in by_class[key]]
    existing_sidecar = _load_sidecar(root, notes)
    sidecar_update = _compute_sidecar_update(existing_sidecar, current_keys, date_str)

    header = _HEADER_TMPL % date_str
    lines = [header, ""]
    total = sum(len(by_class[key]) for key, _ in _SECTIONS)
    if total == 0:
        lines.append(_ALL_CLEAR_LINE)
    else:
        lines.append("# Waiting on you")
        for key, title in _SECTIONS:
            entries = by_class[key]
            if not entries:
                continue
            lines.append("")
            lines.append("## %s" % title)
            for line_text, detail, identity in entries:
                tail = _age_tail(date_str, sidecar_update["items"].get(identity))
                lines.append("- [ ] %s%s *(details: %s)*" % (line_text, tail, detail))
    # FYI items render LAST, checkbox-free, and never count toward the waiting-
    # on-you total -- information, not a task (v3.0-78; see scan_handoff_closes).
    if fyi_items:
        lines.append("")
        lines.append("## %s" % _FYI_SECTION_TITLE)
        for line_text, detail in fyi_items:
            lines.append("- %s *(details: %s)*" % (line_text, detail))
    content = "\n".join(lines) + "\n"
    return content, notes, sidecar_update


def do_generate(root, date_str):
    content, notes, sidecar_update = generate_content(root, date_str)
    for note in notes:
        print(note)
    out_path = os.path.join(root, _OUTPUT_NAME)
    with open(out_path, "wb") as fh:
        fh.write(content.encode("utf-8"))
    _write_sidecar(root, sidecar_update)
    print("decision-inbox: wrote %s" % out_path)
    return 0


def do_check(root, date_str):
    # sidecar_update is discarded here ON PURPOSE: --check computes what
    # regeneration WOULD show (ages included) purely to compare against the
    # file on disk, but it is the read-only path /sweep calls -- it must never
    # persist the sidecar. See _write_sidecar's docstring.
    content, notes, _sidecar_update = generate_content(root, date_str)
    for note in notes:
        print(note)
    out_path = os.path.join(root, _OUTPUT_NAME)
    if not os.path.isfile(out_path):
        print("STALE: %s does not exist yet -- run without --check to generate it. "
              "(details: %s)" % (_OUTPUT_NAME, _OUTPUT_NAME))
        return 1
    with open(out_path, "rb") as fh:
        existing = fh.read()
    if existing == content.encode("utf-8"):
        print("CURRENT: %s matches what regeneration would produce." % _OUTPUT_NAME)
        return 0
    print("STALE: %s is out of date -- run without --check to regenerate. "
          "(details: %s)" % (_OUTPUT_NAME, _OUTPUT_NAME))
    return 1


# ---------------------------------------------------------------------------
# Self-test (hermetic): every fixture is built under tempfile.mkdtemp(). Never
# reads the live harness-backlog.md, SWEEP-BRIEFING.md, or wiki/ tree.
# ---------------------------------------------------------------------------

def self_test():
    import contextlib
    import io
    import shutil
    import tempfile

    total = 0
    failed = 0
    tmp_dirs = []

    def case(name, ok, detail=""):
        nonlocal total, failed
        total += 1
        status = "ok " if ok else "XX "
        print("  %s %s%s" % (status, name, ("  << " + detail) if (not ok and detail) else ""))
        if not ok:
            failed += 1

    def mkroot():
        d = tempfile.mkdtemp(prefix="decision-inbox-selftest-")
        tmp_dirs.append(d)
        return d

    def write(path, content):
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)

    def gen(root, date_str="2026-07-20"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            content, notes, _sidecar_update = generate_content(root, date_str)
        return content, notes, buf.getvalue()

    def gen_full(root, date_str="2026-07-20"):
        """Like gen(), but also returns the in-memory sidecar_update -- only the
        age-tracking cases below need to inspect the computed sidecar state
        itself, not just the rendered text."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            content, notes, sidecar_update = generate_content(root, date_str)
        return content, notes, buf.getvalue(), sidecar_update

    # ---- candidate-section fixtures (Source 0) -----------------------------------
    # Hermetic real candidate ledgers, built through deploy/candidates.py's own real
    # write/append path (never hand-crafted JSON) -- matches that module's own
    # self-test convention and check-candidates.py's. append_candidate_record needs a
    # real git repo (compile-core's seq lock resolves the git common dir), so every
    # root used below is git-inited first.
    def git_init(root):
        import subprocess
        subprocess.run(["git", "-C", root, "init", "-q"], capture_output=True)
        subprocess.run(["git", "-C", root, "config", "user.email", "t@t"],
                       capture_output=True)
        subprocess.run(["git", "-C", root, "config", "user.name", "t"],
                       capture_output=True)

    def stage_candidate(cand, root, session_id, span, **kw):
        meta = cand._fixture_meta(session_id=session_id, span=span, **kw)
        path = cand.write_candidate_atomic(root, meta, span)
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        return meta, rel

    def register_candidate(cand, root, meta, rel, state, note="self-test fixture"):
        rec = cand.new_record(meta, state, note, rel)
        seq, _p = cand.append_candidate_record(root, rec)
        return seq

    try:
        # --- (1) empty root: all-clear file content, generation succeeds --------
        root = mkroot()
        content, notes, _ = gen(root)
        case("(1) empty root: all-clear sentence present",
             _ALL_CLEAR_LINE in content, content)
        case("(1) empty root: no 'Waiting on you' heading",
             "# Waiting on you" not in content, content)
        rc = do_generate(root, "2026-07-20")
        case("(1) empty root: do_generate exit 0", rc == 0)
        case("(1) empty root: file actually written",
             os.path.isfile(os.path.join(root, _OUTPUT_NAME)))

        # --- (2) backlog with one operator-gated entry ---------------------------
        root = mkroot()
        write(os.path.join(root, "harness-backlog.md"), (
            "### v9.9-1 -- An operator-gated decision nobody made yet\n"
            "- **Surfaced:** 2026-07-01, test fixture.\n"
            "- **Where:** nowhere real.\n"
            "- **Severity:** standard.\n"
            "- **Symptom:** the extend-vocabulary step is operator-gated and nobody has said yes.\n"
            "- **Proposed fix:** ask the operator.\n"
        ))
        content, notes, _ = gen(root)
        case("(2) operator-gated entry: item present with id",
             "Backlog v9.9-1:" in content, content)
        case("(2) operator-gated entry: title captured",
             "An operator-gated decision nobody made yet" in content, content)
        case("(2) operator-gated entry: details tail cites harness-backlog.md",
             "(details: harness-backlog.md)" in content, content)
        case("(2) operator-gated entry: under the backlog section",
             "## Backlog items awaiting a decision" in content, content)

        # --- (3) operator-gated entry WITH RESOLVED -> excluded -------------------
        root = mkroot()
        write(os.path.join(root, "harness-backlog.md"), (
            "### v9.9-2 -- A once-pending operator-gated item\n"
            "- **RESOLVED 2026-07-02:** the operator said yes; this closed.\n"
            "- **Symptom:** was operator-gated, now resolved.\n"
        ))
        content, notes, _ = gen(root)
        case("(3) RESOLVED entry excluded: all-clear",
             content.strip() == (_HEADER_TMPL % "2026-07-20") + "\n\n" + _ALL_CLEAR_LINE,
             content)

        # --- (3b) entry boundaries: RESOLVED in a later entry doesn't leak back ---
        root = mkroot()
        write(os.path.join(root, "harness-backlog.md"), (
            "### v9.9-3 -- Still pending, operator-gated\n"
            "- **Symptom:** operator-gated, no resolution yet.\n"
            "\n"
            "### v9.9-4 -- A different, resolved item\n"
            "- **RESOLVED 2026-07-03:** this one is done. Also operator-gated once, fine now.\n"
        ))
        content, notes, _ = gen(root)
        case("(3b) entry boundary: v9.9-3 included",
             "Backlog v9.9-3:" in content, content)
        case("(3b) entry boundary: v9.9-4 excluded",
             "Backlog v9.9-4:" not in content, content)

        # --- (3c) parked handoff close -> FYI section, never a task (v3.0-78) -----
        root = mkroot()
        write(os.path.join(root, "handoffs", "2026-07-31-t1-parked", "meta.yaml"), (
            "handoff_id: 2026-07-31-t1-parked\n"
            "status: answered\n"
            "tier: T1\n"
            "close: pending\n"
        ))
        write(os.path.join(root, "handoffs", "2026-07-30-locked-fine", "meta.yaml"), (
            "handoff_id: 2026-07-30-locked-fine\n"
            "status: locked\n"
            "tier: T2\n"
        ))
        content, notes, _ = gen(root)
        case("(3c) parked close: FYI section present",
             ("## " + _FYI_SECTION_TITLE) in content, content)
        case("(3c) parked close: folder named, no checkbox",
             "- Handoff 2026-07-31-t1-parked:" in content
             and "[ ] Handoff" not in content, content)
        case("(3c) parked close: NOT a waiting-on-you item (all-clear intact)",
             _ALL_CLEAR_LINE in content and "# Waiting on you" not in content,
             content)
        case("(3c) locked folder not listed",
             "2026-07-30-locked-fine" not in content, content)

        # --- (4) sweep briefing with 2 attention items -----------------------------
        root = mkroot()
        sweep_path = os.path.join(root, "SWEEP-BRIEFING.md")
        write(sweep_path, (
            "**All clear:** 5 checks ran clean.\n"
            "\n"
            "**Needs your attention:**\n"
            "1. The environment check found a missing tool. Until it is installed one "
            "feature will not work.\n"
            "   (details: `codex-auth`)\n"
            "2. A structural sensor found a broken cross-link. Fixing it is a one-line edit.\n"
            "\n"
            "**Watching:**\n"
            "- Nothing else to note.\n"
        ))
        content, notes, _ = gen(root)
        case("(4) sweep: both items captured",
             "Sweep finding: The environment check found a missing tool." in content
             and "Sweep finding: A structural sensor found a broken cross-link." in content,
             content)
        case("(4) sweep: no third/spurious item",
             content.count("Sweep finding:") == 2, content)
        case("(4) sweep: dated using the file's own mtime",
             "(sweep dated %s)" % _file_date(sweep_path) in content, content)

        # --- (5) sweep briefing with only All-clear -> none captured ---------------
        root = mkroot()
        write(os.path.join(root, "SWEEP-BRIEFING.md"),
              "**All clear:** 6 checks ran clean -- nothing else to report.\n")
        content, notes, _ = gen(root)
        case("(5) sweep all-clear only: no sweep findings",
             "Sweep finding:" not in content, content)
        case("(5) sweep all-clear only: overall all-clear",
             _ALL_CLEAR_LINE in content, content)

        # --- (5b) sweep: heading + content the parser can't shape -> drift item (B1) -
        root = mkroot()
        write(os.path.join(root, "SWEEP-BRIEFING.md"), (
            "**All clear:** 4 checks ran clean.\n"
            "\n"
            "**Needs your attention:**\n"
            "Something is wrong but nobody numbered it, so the parser has nothing to\n"
            "grab onto here -- unstructured prose, not a numbered list.\n"
            "\n"
            "**Watching:**\n"
            "- Nothing else to note.\n"
        ))
        content, notes, _ = gen(root)
        case("(5b) sweep format drift: drift item appears",
             _SWEEP_DRIFT_TEXT in content, content)
        case("(5b) sweep format drift: details tail cites SWEEP-BRIEFING.md",
             "(details: SWEEP-BRIEFING.md)" in content, content)
        case("(5b) sweep format drift: no spurious 'Sweep finding:' alongside it",
             "Sweep finding:" not in content, content)
        case("(5b) sweep format drift: filed under the sweep section",
             "## From the last sweep" in content, content)

        # --- (5c) sweep: heading present but truly empty beneath it -> no drift item -
        root = mkroot()
        write(os.path.join(root, "SWEEP-BRIEFING.md"), (
            "**All clear:** 6 checks ran clean.\n"
            "\n"
            "**Needs your attention:**\n"
            "\n"
            "**Watching:**\n"
            "- Nothing else to note.\n"
        ))
        content, notes, _ = gen(root)
        case("(5c) sweep heading with no content: no drift item, stays all-clear",
             _ALL_CLEAR_LINE in content and _SWEEP_DRIFT_TEXT not in content, content)

        # --- (6) flight-plan Blocker: None -> excluded ------------------------------
        root = mkroot()
        write(os.path.join(root, "wiki", "flight-plans", "widget.md"), (
            "# Widget plan\n\n**Blocker:** None\n"
        ))
        content, notes, _ = gen(root)
        case("(6) Blocker: None excluded: all-clear",
             _ALL_CLEAR_LINE in content, content)

        # --- (7) flight-plan Blocker: real -> included ------------------------------
        root = mkroot()
        write(os.path.join(root, "wiki", "flight-plans", "widget.md"), (
            "# Widget plan\n\n**Blocker:** Waiting on the vendor contract to be signed.\n"
        ))
        content, notes, _ = gen(root)
        case("(7) real Blocker included",
             "Flight-plan blocker (widget): Waiting on the vendor contract to be signed."
             in content, content)
        case("(7) real Blocker: details tail names the file",
             "(details: wiki/flight-plans/widget.md)" in content, content)

        # --- (7b) bulleted Blocker line also matches --------------------------------
        root = mkroot()
        write(os.path.join(root, "wiki", "flight-plans", "widget.md"), (
            "- **Blocker:** Needs a second signature.\n"
        ))
        content, notes, _ = gen(root)
        case("(7b) bulleted Blocker line included",
             "Flight-plan blocker (widget): Needs a second signature." in content, content)

        # --- (7c) a file with no Blocker line at all contributes nothing, no crash --
        root = mkroot()
        write(os.path.join(root, "wiki", "flight-plans", "quiet.md"),
              "# Quiet plan\n\nNothing to see here.\n")
        content, notes, _ = gen(root)
        case("(7c) no Blocker line: all-clear, no crash",
             _ALL_CLEAR_LINE in content, content)

        # --- (7d) Blocker with a literal em-dash round-trips intact, not mojibake ---
        # Regression guard for the platform-default-encoding bug: every read/write in
        # this module must be explicit UTF-8 (reads: utf-8-sig + errors=replace; writes:
        # utf-8), never the interpreter's locale-default codec (cp1252 on this box),
        # which would turn the 3-byte UTF-8 em dash (\xe2\x80\x94) into the classic
        # "\xc3\xa2\xe2\x82\xac\xe2\x80\x9d" mojibake once re-encoded on write.
        root = mkroot()
        blocker_value = "Emergency stop" + _EM_DASH + "vendor unreachable until Monday."
        write(os.path.join(root, "wiki", "flight-plans", "emdash.md"), (
            "# Emdash plan\n\n**Blocker:** %s\n" % blocker_value
        ))
        content, notes, _ = gen(root)
        content_bytes = content.encode("utf-8")
        case("(7d) em-dash Blocker: phrase present verbatim in the projection",
             ("Flight-plan blocker (emdash): %s" % blocker_value) in content, content)
        case("(7d) em-dash Blocker: correct UTF-8 em-dash bytes present (not mojibake)",
             b"\xe2\x80\x94" in content_bytes, repr(content_bytes))
        case("(7d) em-dash Blocker: mojibake byte sequence absent",
             b"\xc3\xa2\xe2\x82\xac\xe2\x80\x9d" not in content_bytes, repr(content_bytes))

        # --- (8) DECISION-PENDING marker captured -----------------------------------
        root = mkroot()
        write(os.path.join(root, "NOTES.md"),
              "Some prose.\nDECISION-PENDING: pick a vendor for the new gateway cert.\nMore prose.\n")
        content, notes, _ = gen(root)
        case("(8) marker captured",
             "Flagged: pick a vendor for the new gateway cert." in content, content)
        case("(8) marker: details tail names the file",
             "(details: NOTES.md)" in content, content)

        # --- (8b) marker found in a top-level wiki/*.md file too --------------------
        root = mkroot()
        write(os.path.join(root, "wiki", "REVIEW.md"),
              "DECISION-PENDING: retire the legacy import path or keep it.\n")
        content, notes, _ = gen(root)
        case("(8b) marker in wiki/*.md captured",
             "Flagged: retire the legacy import path or keep it." in content, content)
        case("(8b) marker in wiki/*.md: details tail is wiki-relative",
             "(details: wiki/REVIEW.md)" in content, content)

        # --- (8c) the generated output file itself is never re-scanned for markers -
        # Pinning test for fix B2: DECISIONS-PENDING.md was already excluded from
        # the marker scan (name == _OUTPUT_NAME) before this audit fix landed; this
        # case pins that behavior so it can never silently regress.
        root = mkroot()
        write(os.path.join(root, _OUTPUT_NAME),
              "DECISION-PENDING: this is stale leftover content, not a live source.\n")
        content, notes, _ = gen(root)
        case("(8c) DECISIONS-PENDING.md excluded from its own marker scan",
             "stale leftover content" not in content, content)

        # --- (8d) SWEEP-BRIEFING.md is also excluded from the marker scan (fix B2) --
        # Unlike (8c), this exclusion did NOT exist before fix B2 -- SWEEP-BRIEFING.md
        # is a report, not a source, so a marker literally sitting in it (outside any
        # "Needs your attention" numbered line) must never surface as a "Flagged:"
        # item via the generic marker scan.
        root = mkroot()
        write(os.path.join(root, _SWEEP_BRIEFING_NAME), (
            "**All clear:** 6 checks ran clean.\n"
            "DECISION-PENDING: stale note embedded in a report, not a source.\n"
        ))
        content, notes, _ = gen(root)
        case("(8d) SWEEP-BRIEFING.md excluded from the marker scan",
             "stale note embedded in a report" not in content, content)
        case("(8d) SWEEP-BRIEFING.md marker exclusion: overall all-clear",
             _ALL_CLEAR_LINE in content, content)

        # --- (9) --check current: exit 0 --------------------------------------------
        root = mkroot()
        write(os.path.join(root, "harness-backlog.md"), (
            "### v9.9-5 -- Pending operator-gated item\n"
            "- **Symptom:** operator-gated, unresolved.\n"
        ))
        rc = do_generate(root, "2026-07-20")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_check = do_check(root, "2026-07-20")
        case("(9) --check current: generate exit 0", rc == 0)
        case("(9) --check current: check exit 0", rc_check == 0, buf.getvalue())

        # --- (10) --check stale: exit 1 ----------------------------------------------
        root = mkroot()
        write(os.path.join(root, "harness-backlog.md"), (
            "### v9.9-6 -- Pending operator-gated item\n"
            "- **Symptom:** operator-gated, unresolved.\n"
        ))
        rc = do_generate(root, "2026-07-20")
        # source changes after generation: a second pending item appears
        write(os.path.join(root, "harness-backlog.md"), (
            "### v9.9-6 -- Pending operator-gated item\n"
            "- **Symptom:** operator-gated, unresolved.\n"
            "\n"
            "### v9.9-7 -- A second pending operator-gated item\n"
            "- **Symptom:** operator-gated, unresolved too.\n"
        ))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_check = do_check(root, "2026-07-20")
        case("(10) --check stale: exit 1", rc_check == 1, buf.getvalue())
        case("(10) --check stale: STALE note printed", "STALE" in buf.getvalue(), buf.getvalue())

        # --- (10b) --check against a never-generated root: exit 1, no crash ----------
        root = mkroot()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_check = do_check(root, "2026-07-20")
        case("(10b) --check never-generated: exit 1", rc_check == 1)
        case("(10b) --check never-generated: note explains", "does not exist yet" in buf.getvalue(),
             buf.getvalue())

        # --- (11) missing all sources: notes not errors, still all-clear -------------
        root = mkroot()
        content, notes, printed = gen(root)
        case("(11) missing all sources: no exception raised (reached this line)", True)
        case("(11) missing all sources: still all-clear",
             _ALL_CLEAR_LINE in content, content)
        case("(11) missing all sources: backlog note present",
             any("harness-backlog.md absent" in n for n in notes), notes)
        case("(11) missing all sources: sweep note present",
             any("SWEEP-BRIEFING.md absent" in n for n in notes), notes)
        case("(11) missing all sources: flight-plan note present",
             any("wiki/flight-plans/ absent" in n for n in notes), notes)
        case("(11) missing all sources: wiki-marker note present",
             any("wiki/ absent" in n for n in notes), notes)

        # --- (12) regeneration is idempotent (fixed --date; two runs, same bytes) ---
        root = mkroot()
        write(os.path.join(root, "harness-backlog.md"), (
            "### v9.9-8 -- Pending operator-gated item\n"
            "- **Symptom:** operator-gated, unresolved.\n"
        ))
        write(os.path.join(root, "wiki", "flight-plans", "widget.md"),
              "**Blocker:** Waiting on the vendor.\n")
        content_a, _, _ = gen(root, date_str="2026-07-15")
        content_b, _, _ = gen(root, date_str="2026-07-15")
        case("(12) idempotent: identical bytes across two runs",
             content_a == content_b)
        case("(12) idempotent: date line reflects the pinned --date",
             "Regenerated: 2026-07-15." in content_a, content_a)

        # --- (13) multiple source classes together -> multiple ordered sections -----
        root = mkroot()
        write(os.path.join(root, "harness-backlog.md"), (
            "### v9.9-9 -- Pending operator-gated item\n"
            "- **Symptom:** operator-gated, unresolved.\n"
        ))
        write(os.path.join(root, "wiki", "flight-plans", "widget.md"),
              "**Blocker:** Waiting on the vendor.\n")
        write(os.path.join(root, "NOTES.md"),
              "DECISION-PENDING: retire or keep the legacy path.\n")
        content, notes, _ = gen(root)
        i_backlog = content.find("## Backlog items awaiting a decision")
        i_flight = content.find("## Flight-plan blockers")
        i_marker = content.find("## Flagged in a document")
        case("(13) all three present sections appear",
             i_backlog != -1 and i_flight != -1 and i_marker != -1, content)
        case("(13) sections appear in fixed source order",
             i_backlog < i_flight < i_marker, content)
        case("(13) sweep section absent when sweep source absent",
             "## From the last sweep" not in content, content)

        # --- (14) a registered transcribe-as-operator-event candidate: appears, span
        # text visible ------------------------------------------------------------------
        cand = _load("candidates.py", "candidates_decision_inbox_selftest")
        root = mkroot()
        git_init(root)
        span14 = "the operator decided to ship the v3 pilot on Tuesday morning"
        m14, r14 = stage_candidate(cand, root, "sessC1401", span14)
        register_candidate(cand, root, m14, r14, "staged")
        register_candidate(cand, root, m14, r14, "registered")
        content, notes, _ = gen(root)
        case("(14) registered transcribe-as-operator-event candidate: id appears",
             m14["candidate_id"] in content, content)
        case("(14) registered candidate: verbatim span text visible",
             span14 in content, content)
        case("(14) registered candidate: filed under the candidate section",
             "## Session candidates awaiting your confirmation" in content, content)

        # --- (15) staged-but-not-registered candidate: does NOT appear -----------------
        root = mkroot()
        git_init(root)
        m15, r15 = stage_candidate(
            cand, root, "sessC1501", "a decision that was only staged, never registered")
        register_candidate(cand, root, m15, r15, "staged")
        content, notes, _ = gen(root)
        case("(15) staged-but-not-registered candidate: excluded",
             m15["candidate_id"] not in content, content)
        case("(15) staged-but-not-registered candidate: overall all-clear",
             _ALL_CLEAR_LINE in content, content)

        # --- (16) a registered plain-intake candidate: does NOT appear -----------------
        root = mkroot()
        git_init(root)
        m16, r16 = stage_candidate(
            cand, root, "sessC1601",
            "a finding that is plain intake, not an operator event",
            proposed_disposition="plain-intake")
        register_candidate(cand, root, m16, r16, "staged")
        register_candidate(cand, root, m16, r16, "registered")
        content, notes, _ = gen(root)
        case("(16) registered plain-intake candidate: excluded",
             m16["candidate_id"] not in content, content)
        case("(16) registered plain-intake candidate: overall all-clear",
             _ALL_CLEAR_LINE in content, content)

        # --- (17) an instance with no candidates machinery at all: the other four
        # sections still generate intact --------------------------------------------
        root = mkroot()
        write(os.path.join(root, "harness-backlog.md"), (
            "### v9.9-10 -- Pending operator-gated item\n"
            "- **Symptom:** operator-gated, unresolved.\n"
        ))
        write(os.path.join(root, "wiki", "flight-plans", "widget.md"),
              "**Blocker:** Waiting on the vendor.\n")
        write(os.path.join(root, "NOTES.md"),
              "DECISION-PENDING: retire or keep the legacy path.\n")
        content, notes, _ = gen(root)
        case("(17) no candidates machinery: candidate note present",
             any("receipts/candidates" in n for n in notes), notes)
        case("(17) no candidates machinery: candidate section absent (no items)",
             "## Session candidates awaiting your confirmation" not in content, content)
        case("(17) no candidates machinery: backlog section still present",
             "## Backlog items awaiting a decision" in content, content)
        case("(17) no candidates machinery: flight-plan section still present",
             "## Flight-plan blockers" in content, content)
        case("(17) no candidates machinery: marker section still present",
             "## Flagged in a document" in content, content)

        # --- (18) candidate section is FIRST when multiple sections are present --------
        root = mkroot()
        git_init(root)
        m18, r18 = stage_candidate(
            cand, root, "sessC1801", "a registered decision that should surface first")
        register_candidate(cand, root, m18, r18, "staged")
        register_candidate(cand, root, m18, r18, "registered")
        write(os.path.join(root, "harness-backlog.md"), (
            "### v9.9-11 -- Pending operator-gated item\n"
            "- **Symptom:** operator-gated, unresolved.\n"
        ))
        content, notes, _ = gen(root)
        i_candidate = content.find("## Session candidates awaiting your confirmation")
        i_backlog2 = content.find("## Backlog items awaiting a decision")
        case("(18) candidate section appears before the backlog section",
             i_candidate != -1 and i_backlog2 != -1 and i_candidate < i_backlog2, content)

        # --- (19) a long span is truncated with an ellipsis, not dumped whole ----------
        root = mkroot()
        git_init(root)
        long_span = ("word " * 60).strip()
        m19, r19 = stage_candidate(cand, root, "sessC1901", long_span)
        register_candidate(cand, root, m19, r19, "staged")
        register_candidate(cand, root, m19, r19, "registered")
        content, notes, _ = gen(root)
        case("(19) long span is truncated with an ellipsis",
             "..." in content and long_span not in content, content)

        # --- (20)-(25) first-seen age tracking (v3.0-39 item 2) -----------------------

        def write_backlog_item(root, entry_id="v9.9-20"):
            write(os.path.join(root, "harness-backlog.md"), (
                "### %s -- An age-tracking fixture item\n"
                "- **Symptom:** operator-gated, unresolved.\n" % entry_id
            ))

        # --- (20) first generation: new item stamped "new today", sidecar written --
        root = mkroot()
        write_backlog_item(root)
        rc = do_generate(root, "2026-07-01")
        case("(20) first generation: do_generate exit 0", rc == 0)
        sidecar_path = os.path.join(root, "receipts", "desk", "inbox-first-seen.json")
        case("(20) first generation: sidecar file written",
             os.path.isfile(sidecar_path))
        with open(sidecar_path, "r", encoding="utf-8") as fh:
            sidecar_20 = json.load(fh)
        key_20 = "backlog:v9.9-20"
        case("(20) first generation: sidecar stamps today's date",
             sidecar_20.get("items", {}).get(key_20) == "2026-07-01", repr(sidecar_20))
        with open(os.path.join(root, _OUTPUT_NAME), "r", encoding="utf-8") as fh:
            inbox_20 = fh.read()
        case("(20) first generation: rendered item says 'new today'",
             ("Backlog v9.9-20: An age-tracking fixture item " + _EM_DASH + " new today")
             in inbox_20, inbox_20)

        # --- (21) regeneration preserves the stored first-seen date -----------------
        rc = do_generate(root, "2026-07-13")  # 12 days later, same source unchanged
        case("(21) regeneration: do_generate exit 0", rc == 0)
        with open(sidecar_path, "r", encoding="utf-8") as fh:
            sidecar_21 = json.load(fh)
        case("(21) regeneration: first-seen date is UNCHANGED (still 2026-07-01)",
             sidecar_21.get("items", {}).get(key_20) == "2026-07-01", repr(sidecar_21))
        with open(os.path.join(root, _OUTPUT_NAME), "r", encoding="utf-8") as fh:
            inbox_21 = fh.read()
        case("(21) regeneration: rendered age is 'waiting 12 days'",
             ("Backlog v9.9-20: An age-tracking fixture item " + _EM_DASH +
              " waiting 12 days") in inbox_21, inbox_21)

        # --- (22) disappearance retires the key, never deletes it -------------------
        write(os.path.join(root, "harness-backlog.md"), "")  # source item removed
        rc = do_generate(root, "2026-07-15")
        case("(22) disappearance: do_generate exit 0", rc == 0)
        with open(sidecar_path, "r", encoding="utf-8") as fh:
            sidecar_22 = json.load(fh)
        case("(22) disappearance: key no longer under 'items'",
             key_20 not in sidecar_22.get("items", {}), repr(sidecar_22))
        retired_22 = sidecar_22.get("retired", {}).get(key_20)
        case("(22) disappearance: key preserved under 'retired' (append-only)",
             retired_22 is not None, repr(sidecar_22))
        case("(22) disappearance: retired record keeps the ORIGINAL first-seen date",
             isinstance(retired_22, dict) and retired_22.get("first_seen") == "2026-07-01",
             repr(retired_22))
        case("(22) disappearance: retired record stamps the retirement date",
             isinstance(retired_22, dict) and retired_22.get("retired") == "2026-07-15",
             repr(retired_22))

        # --- (23) revival after retirement restores the ORIGINAL first-seen date ----
        write_backlog_item(root)  # the exact same item reappears
        rc = do_generate(root, "2026-07-20")
        case("(23) revival: do_generate exit 0", rc == 0)
        with open(sidecar_path, "r", encoding="utf-8") as fh:
            sidecar_23 = json.load(fh)
        case("(23) revival: key back under 'items' with its ORIGINAL date, not today",
             sidecar_23.get("items", {}).get(key_20) == "2026-07-01", repr(sidecar_23))
        case("(23) revival: key removed from 'retired' once revived",
             key_20 not in sidecar_23.get("retired", {}), repr(sidecar_23))

        # --- (24) corrupt sidecar is tolerated: regenerated fresh, noted, no crash --
        root = mkroot()
        write_backlog_item(root, entry_id="v9.9-24")
        corrupt_path = os.path.join(root, "receipts", "desk", "inbox-first-seen.json")
        write(corrupt_path, "{ this is not valid json ]]]")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = do_generate(root, "2026-07-01")
        printed_24 = buf.getvalue()
        case("(24) corrupt sidecar: do_generate exit 0 (no crash)", rc == 0)
        case("(24) corrupt sidecar: a NOTE about it is printed",
             "unreadable" in printed_24, printed_24)
        with open(corrupt_path, "r", encoding="utf-8") as fh:
            sidecar_24 = json.load(fh)  # must now be valid JSON again
        case("(24) corrupt sidecar: rewritten as valid JSON with the item fresh",
             sidecar_24.get("items", {}).get("backlog:v9.9-24") == "2026-07-01",
             repr(sidecar_24))
        with open(os.path.join(root, _OUTPUT_NAME), "r", encoding="utf-8") as fh:
            inbox_24 = fh.read()
        case("(24) corrupt sidecar: item still renders 'new today' despite the corruption",
             (_EM_DASH + " new today") in inbox_24, inbox_24)

        # --- (25) --check writes nothing: sidecar bytes and mtime unchanged ---------
        root = mkroot()
        write_backlog_item(root, entry_id="v9.9-25")
        do_generate(root, "2026-07-01")
        check_sidecar_path = os.path.join(root, "receipts", "desk", "inbox-first-seen.json")
        before_bytes = open(check_sidecar_path, "rb").read()
        before_mtime = os.path.getmtime(check_sidecar_path)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            do_check(root, "2026-07-08")  # a later date -> would render a different age
        after_bytes = open(check_sidecar_path, "rb").read()
        after_mtime = os.path.getmtime(check_sidecar_path)
        case("(25) --check: sidecar bytes unchanged", before_bytes == after_bytes)
        case("(25) --check: sidecar mtime unchanged", before_mtime == after_mtime)

        # --- (25b) --check on a NEVER-generated root never even creates the sidecar -
        root = mkroot()
        write_backlog_item(root, entry_id="v9.9-26")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            do_check(root, "2026-07-01")
        never_gen_sidecar_dir = os.path.join(root, "receipts", "desk")
        case("(25b) --check on a never-generated root: no receipts/desk/ directory "
             "is created", not os.path.isdir(never_gen_sidecar_dir))

        # --- (25c) gen_full() sanity: --check's own in-memory computation matches ----
        # what a real regeneration on the same date would have produced (the
        # function is pure/read-only either way; this just pins that fact).
        root = mkroot()
        write_backlog_item(root, entry_id="v9.9-27")
        content_gen, _, _, sidecar_gen = gen_full(root, "2026-07-01")
        case("(25c) gen_full: sidecar_update computed without touching disk",
             not os.path.isdir(os.path.join(root, "receipts")))
        case("(25c) gen_full: computed first-seen matches the pinned date",
             sidecar_gen.get("items", {}).get("backlog:v9.9-27") == "2026-07-01",
             repr(sidecar_gen))

        # --- Parser unit checks (in-memory, no disk) ---------------------------------
        case("_first_sentence: stops at first period",
             _first_sentence("One. Two. Three.") == "One.")
        case("_first_sentence: no terminator returns whole text",
             _first_sentence("No terminator here") == "No terminator here")
        case("_is_none_ish: bare None (any case) is none-ish",
             _is_none_ish("None") and _is_none_ish("none") and _is_none_ish("NONE."))
        case("_is_none_ish: empty value is none-ish", _is_none_ish("   "))
        case("_is_none_ish: elaborated None is NOT none-ish (exact-match only)",
             not _is_none_ish("None -- but input needed first"))
        case("_BACKLOG_HEADING_RE: matches a real entry heading",
             _BACKLOG_HEADING_RE.match("### v3.0-21 " + _EM_DASH + " Some title") is not None)
        case("_BACKLOG_HEADING_RE: does not match the template placeholder line",
             _BACKLOG_HEADING_RE.match("### v<version>-N " + _EM_DASH + " Short title") is None)
        case("_BACKLOG_HEADING_RE: ASCII '--' separator also matches",
             _BACKLOG_HEADING_RE.match("### v2.1-5 -- Some title") is not None)
        case("_BLOCKER_RE: rejects 'Blocker (build):' (text before the colon)",
             _BLOCKER_RE.match("**Blocker (build):** NONE.") is None)

    finally:
        for d in tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)

    if failed:
        print("decision-inbox self-test: FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("%d/%d self-tests passed" % (total, total))
    return 0


def main(argv):
    args = argv[1:]
    if "--self-test" in args:
        return self_test()

    root = os.getcwd()
    if "--root" in args:
        i = args.index("--root")
        if i + 1 < len(args):
            root = args[i + 1]

    date_str = None
    if "--date" in args:
        i = args.index("--date")
        if i + 1 < len(args):
            date_str = args[i + 1]
    if date_str is None:
        date_str = datetime.date.today().isoformat()

    if "--check" in args:
        return do_check(root, date_str)
    return do_generate(root, date_str)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
