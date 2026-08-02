#!/usr/bin/env python3
"""candidates.py -- R-1 session-candidate schema + append-only candidate ledger
(memory-engine v3, R-1 leg B).

Spec: harness-v3.0/specs/r1-build-decisions-2026-07-22.md Parts 3 (candidate file
shape + id) and 4 (candidate ledger) -- verbatim; the design brief (r1-session-
intake-writer-design-brief-2026-07-21.md) is the *why*. Parts 5/6/7 (harvest-
candidates.py, register-candidates.py, promote-candidate.py, check-candidates.py,
decision-inbox.py) are SIBLING legs -- they import this module; this module
imports none of them.

WHY A TYPED FILE, NOT A TRANSCRIPT SCRAPE (design brief, "Design inheritance" #1,
the DA-1 lesson, twice cross-vendor confirmed): a prose bridge that lets model-
mediated content acquire `human` origin launders semantic injection. A candidate
is a FACT FORM that cannot authorize -- fixed enum fields, a verbatim quote, and
nothing else. No summary, no prose bridge, because prose is exactly the thing
that reads as an authorization. `origin: session-derived` is a FIXED LITERAL in
the file format itself (parse_candidate refuses any other value) -- the "cannot
mint trust" invariant is enforced at the schema layer, not left to a caller's
discipline.

THIS MODULE IS A TRUST-BOUNDARY PARSER. `parse_candidate` reads text that may
have been staged by a session that saw untrusted content (the co-residency
scenario). Every check below is a REFUSAL, never a downgrade-and-proceed: an
unknown key, a wrong-shaped hash, a rewritten span, a spoofed candidate_id --
each degrades to a NAMED CandidateViolation, never a crash and never a partial
accept. Malformed input is an EXPECTED input class here (register-candidates.py's
quarantine path depends on this module refusing cleanly, not raising something
uncatchable).

THE LEDGER (Part 4) mirrors registrations.py's chain ONE-FOR-ONE: an append-only
record store at receipts/candidates/<seq>.json, built by swapping
compile_core.JOURNAL_DIR + compile_core.validate_record for the duration of a
call (compile-core.py itself is untouched -- see _CandidateJournal). Record
schema (kind: "candidate"), one per lifecycle transition (adjudication: never an
edit, always a new record -- "Corrections are new records, never edits."):
  seq                   int, engine-owned (allocated by append_record)
  candidate_id          str, "%s-%s" % (session_id[:8], span_sha256[:16])
  event                 str, the staged file's repo-relative path, forward slashes
  state                 str, one of STATES
  candidate_class       str, one of CANDIDATE_CLASSES
  origin                str, in origin.ORIGIN_ORDER -- for every legitimately-
                        minted candidate record this is ALWAYS "session-derived"
                        (it is carried through unchanged from the candidate
                        file's own fixed-literal origin field, via new_record);
                        validate_candidate_record additionally refuses any value
                        LESS RESTRICTIVE than session-derived (human/corpus),
                        which is the mechanical form of "a candidate record can
                        never mint an un-taint it didn't earn"
  origin_max            str, in origin.ORIGIN_ORDER -- the harvesting session's
                        inherited taint ceiling (co-residency propagation); NOT
                        floor-checked (a candidate can legitimately be tainted)
  proposed_disposition  str, one of DISPOSITIONS
  span_sha256           str, 64 lowercase hex
  gate_artifact         str | None -- set only on `promoted` (leg d's concern)
  gate_commit           str | None -- the signature-verified sha (leg d)
  note                  str, human-readable reason, always populated
  recorded_at           str, ISO-8601
  prev_record_hash      str | None -- chained by append_record, None only seq 1

Effective state = the highest-seq record for a candidate_id (load_candidate_ledger,
the same supersedes-by-later-seq mechanic registrations.load_registrations uses).
ledger_history returns EVERY record in seq order (not just the effective map) --
the promotion gate (leg d) needs to scan the FULL history for single-use
enforcement (has this gate_artifact already been consumed by a NOW-superseded
record?), which the effective map alone cannot answer.

Unknown top-level keys are refused at validate_candidate_record (write-time
schema gate, matches compile_core.validate_record's and
registrations.validate_registration's posture).

ORIGIN_ORDER TEMPORAL NOTE (read before touching the floor check or its self-
test): as of this leg landing, deploy/origin.py's ORIGIN_ORDER does NOT yet
contain "session-derived" -- a concurrent leg is adding it (Part 2 of the build-
decisions spec: `human < corpus < session-derived < vendor-ref < ...`). This
module's floor check looks the value up BY RANK (`origin.ORIGIN_ORDER.index(...)`
style, never a hardcoded integer), and degrades to a documented no-op (never a
crash, never a silent wrong answer) when "session-derived" is absent from the
order -- see validate_candidate_record's floor-check comment. The self-test
mirrors this: cases that depend on the floor being ENFORCEABLE detect the
condition at run time and SKIP (never fail) when it is not yet met.
"""

import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import time

try:
    import yaml
except ImportError:  # pragma: no cover -- optional-yaml idiom (origin.py,
    yaml = None       # register-intake.py): absent PyYAML degrades, never crashes.

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(basename, alias):
    spec = importlib.util.spec_from_file_location(alias, os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Sibling-import by path (deploy/ is a flat directory, never a package) -- the
# same mechanism registrations.py itself uses. Distinct aliases from
# registrations.py's own _load calls: each _load() mints a FRESH module object,
# so this module's _cc.JOURNAL_DIR swap is fully isolated from registrations.py's
# (or compile-v2.py's) own separate compile-core.py instance, even if all three
# are imported in the same process.
_cc = _load("compile-core.py", "compile_core_candidates")
_origin = _load("origin.py", "origin_candidates")


# ------------------------------------------------------------------ constants
CANDIDATES_LEDGER_DIR = os.path.join("receipts", "candidates")
CANDIDATES_STAGING_DIR = os.path.join("intake", "session-candidates")

CANDIDATE_CLASSES = ("decision-stated", "finding", "directive", "correction")
DISPOSITIONS = ("transcribe-as-operator-event", "plain-intake", "discard-candidate")
STATES = ("staged", "registered", "promoted", "expired", "discarded", "quarantined")
TERMINAL_STATES = frozenset({"promoted", "expired", "discarded", "quarantined"})


class CandidateViolation(Exception):
    """Raised by both the file-shape parser (parse_candidate) and the ledger
    schema gate (validate_candidate_record) -- one exception class for the whole
    trust-boundary surface this module owns, per the build spec."""
    pass


# ================================================================== Part 3:
# the candidate FILE -- schema, id, render, parse, atomic write
# ==================================================================

# Exact frontmatter key set, IN THE ORDER render_candidate writes them (stable
# key order -- deterministic output, never a dict-iteration-order accident).
_FM_KEYS_ORDER = ("kind", "candidate_id", "session_id", "candidate_class", "origin",
                  "origin_max", "proposed_disposition", "transcript_pointer",
                  "harvested_at", "span_sha256")
_ALLOWED_FM_KEYS = set(_FM_KEYS_ORDER)

_SPAN_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_FENCE_RE = re.compile(r"^`{3,}$")
_VERBATIM_HEADER = "## Verbatim span"

# ---------------------------------------------------------------- id charset
# THE IDS BECOME A FILENAME, SO THEIR CHARSET IS A SECURITY BOUNDARY, not
# cosmetics. write_candidate_atomic() builds
# intake/session-candidates/<date>-candidate-<candidate_id>.md, and the same id
# becomes the ledger record's `event` path. candidate_id is derived as
# session_id[:8] + "-" + span_hash[:16], and session_id is CALLER-SUPPLIED (the
# harvester's --session-id) -- so an unconstrained session_id of "../../etc"
# yields candidate_id "../../e-<hex>" and the write escapes the staging
# directory entirely. Recomputing candidate_id from session_id (as
# parse_candidate does) does NOT catch this: the traversal id recomputes to
# itself perfectly.
#
# The fix is a charset, enforced at every entry point: no "/" or "\" (the only
# characters that can traverse), no NUL, must START with an alphanumeric (so an
# id can never be ".." or a dotfile, and can never collide with the
# ".candidate-tmp-*" temp-file prefix). write_candidate_atomic ALSO asserts
# path containment afterwards -- belt and braces, because a charset bug here
# would be silent and the containment check would not be.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CANDIDATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,143}$")

# harvested_at's first 10 characters become the filename's date part, so a
# free-form string there is a garbage filename rather than a refusal. Require at
# minimum a real ISO-8601 calendar date prefix; anything after it (a "T" time,
# an offset) is free-form and unconstrained.
_HARVESTED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ].*)?$")


def span_hash(span):
    """sha256 of the verbatim span's utf-8 bytes -- the id's non-forgeable half."""
    return hashlib.sha256(span.encode("utf-8")).hexdigest()


def make_candidate_id(session_id, span):
    """Idempotent across re-runs and crash-replays (build-decisions Part 3): same
    session + same span => same id => same filename => ledger lookup finds it.
    The id alone proves neither atomicity nor dedup (Sol R1 fold) -- the atomic
    write (write_candidate_atomic) and the ledger (Part 4) do.

    Refuses a session_id outside _SESSION_ID_RE (see that constant): the id ends
    up in a filesystem path, so this is the chokepoint where a path-traversing
    session_id is stopped, not a style rule."""
    if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
        raise CandidateViolation(
            "session_id %r is outside the permitted charset (must start "
            "alphanumeric, then [A-Za-z0-9._-] only, <=128 chars) -- the id "
            "becomes a filesystem path, so '/', '\\\\' and a leading '.' are "
            "refused at the source" % (session_id,))
    return "%s-%s" % (session_id[:8], span_hash(span)[:16])


def _pick_fence(span):
    """The shortest all-backtick fence (>=3) that is STRICTLY LONGER than the
    longest run of consecutive backticks anywhere in `span`. Standard fenced-
    code-block collision avoidance (the same discipline Markdown/CommonMark
    fencing uses): with this guarantee, no line inside `span` can ever equal the
    chosen fence string, so parse_candidate's exact-string closing-fence search
    can never mistake a line of the span's OWN content for the block's close."""
    max_run = 0
    run = 0
    for ch in span:
        if ch == "`":
            run += 1
            if run > max_run:
                max_run = run
        else:
            run = 0
    return "`" * max(3, max_run + 1)


def render_candidate(meta, span):
    """The full candidate file text -- deterministic, stable key order, `\\n`
    newlines only for every STRUCTURAL line (frontmatter, header, fence lines).
    `span` itself is inserted verbatim (byte-for-byte, whatever line endings or
    exotic content it carries) between the opening and closing fence -- see
    parse_candidate's docstring for why this round-trips exactly even when span
    contains CRLF, backticks, or a nested triple-backtick fence.

    Frontmatter values are rendered via json.dumps() (a JSON double-quoted
    string is ALSO a valid YAML double-quoted scalar -- a well-known, stdlib-
    only trick) rather than depending on PyYAML for writing: this module's write
    path must not require PyYAML at all (only the trust-boundary PARSE path
    does, per the optional-yaml idiom), and json.dumps already handles every
    escape (quotes, backslashes, control characters) a frontmatter value could
    contain (transcript_pointer is free-form, advisory-only text)."""
    missing = [k for k in _FM_KEYS_ORDER if k not in meta]
    if missing:
        raise CandidateViolation("render_candidate: meta missing field(s): %s" % missing)
    fence = _pick_fence(span)
    lines = ["---"]
    for k in _FM_KEYS_ORDER:
        lines.append("%s: %s" % (k, json.dumps(str(meta[k]), ensure_ascii=False)))
    lines.append("---")
    lines.append("")
    lines.append(_VERBATIM_HEADER)
    lines.append("")
    lines.append(fence)
    prefix = "\n".join(lines) + "\n"
    return prefix + span + "\n" + fence + "\n"


def parse_candidate(text):
    """(meta_dict, span_str). Raises CandidateViolation, named, on ANYTHING
    malformed -- this is the trust-boundary parser; be strict, be paranoid,
    never crash, never partially accept.

    BYTE-FIDELITY TECHNIQUE: the whole text is split on "\\n" via str.split
    (never .splitlines(), which normalises/erases \\r\\n vs \\n vs \\r and would
    make CRLF-preserving round-trips impossible). "\\n".join(text.split("\\n"))
    == text ALWAYS, exactly, for any string and any separator character --
    so structural lines (frontmatter fences, the header, code fences) are
    matched by comparing each split part with a trailing \\r stripped (tolerant
    of a CRLF-authored file), while the SPAN itself is reassembled by
    "\\n".join()-ing the exact slice of parts between the fences, which
    reproduces the original bytes exactly, \\r and all, because split/join with
    a single non-overlapping separator is a lossless round-trip.

    FENCE MATCHING: the opening fence's exact backtick-run STRING (not merely
    "3 or more backticks") is what the closing search looks for, and the search
    is early-terminating "\\n" + fence_string + "\\n" ... actually: line-exact
    equality, not a generic >=3-backtick regex. This matters because a
    well-formed span may itself contain a SHORTER backtick run (render_candidate
    always picks a fence one backtick longer than the span's longest run) --
    matching only the SAME-LENGTH string means the span's own shorter fence-like
    lines are correctly read as ordinary span content, never mistaken for the
    real close."""
    if text.startswith("﻿"):  # tolerate a stray BOM even though
        text = text[1:]            # validate_candidate_file's utf-8-sig already strips one
    lines = text.split("\n")

    # ---- frontmatter fence ----
    if not lines or lines[0].rstrip("\r") != "---":
        raise CandidateViolation(
            "candidate file missing opening frontmatter fence '---' as line 1")
    close_i = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r") == "---":
            close_i = i
            break
    if close_i is None:
        raise CandidateViolation(
            "candidate file frontmatter fence never closed (no second '---' line)")

    if yaml is None:
        raise CandidateViolation(
            "PyYAML unavailable -- cannot parse candidate frontmatter "
            "(refusing cleanly, not crashing)")
    fm_text = "\n".join(l.rstrip("\r") for l in lines[1:close_i])
    try:
        meta = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        raise CandidateViolation("frontmatter is not valid YAML: %s" % e)
    if not isinstance(meta, dict):
        raise CandidateViolation(
            "frontmatter is not a YAML mapping (got %s)" % type(meta).__name__)

    extra = set(meta) - _ALLOWED_FM_KEYS
    if extra:
        raise CandidateViolation("unknown frontmatter key(s): %s" % sorted(extra))
    missing = _ALLOWED_FM_KEYS - set(meta)
    if missing:
        raise CandidateViolation("missing frontmatter key(s): %s" % sorted(missing))
    for k in _FM_KEYS_ORDER:
        if not isinstance(meta[k], str):
            raise CandidateViolation(
                "frontmatter field %s has wrong type %s (must be str)"
                % (k, type(meta[k]).__name__))

    if meta["kind"] != "session-candidate":
        raise CandidateViolation(
            "kind must be 'session-candidate', got %r" % meta["kind"])
    if meta["origin"] != "session-derived":
        # THE FIXED LITERAL (build-decisions Part 3): "any other value is a
        # refusal (this is the 'cannot mint trust' invariant in the file format
        # itself)". A declared_origin: human here must NOT be able to lower it.
        raise CandidateViolation(
            "origin must be exactly 'session-derived' (a candidate file can "
            "never declare a more-trusted origin for itself), got %r"
            % meta["origin"])
    if meta["origin_max"] not in _origin.ORIGIN_ORDER:
        raise CandidateViolation(
            "origin_max %r not in origin.ORIGIN_ORDER" % meta["origin_max"])
    if meta["candidate_class"] not in CANDIDATE_CLASSES:
        raise CandidateViolation(
            "candidate_class %r not in %s" % (meta["candidate_class"], CANDIDATE_CLASSES))
    if meta["proposed_disposition"] not in DISPOSITIONS:
        raise CandidateViolation(
            "proposed_disposition %r not in %s"
            % (meta["proposed_disposition"], DISPOSITIONS))
    if not _SPAN_HASH_RE.match(meta["span_sha256"]):
        raise CandidateViolation(
            "span_sha256 is not 64 lowercase hex characters: %r" % meta["span_sha256"])
    # Charset gates BEFORE the recompute below. Order matters: recomputing
    # candidate_id from a traversing session_id reproduces the traversing id
    # exactly, so the equality check downstream would happily pass it -- only
    # the charset stops it. See the _SESSION_ID_RE comment block.
    if not _SESSION_ID_RE.match(meta["session_id"]):
        raise CandidateViolation(
            "session_id %r is outside the permitted charset (the id becomes a "
            "filesystem path)" % meta["session_id"])
    if not _CANDIDATE_ID_RE.match(meta["candidate_id"]):
        raise CandidateViolation(
            "candidate_id %r is outside the permitted charset (the id becomes a "
            "filesystem path)" % meta["candidate_id"])
    if not _HARVESTED_AT_RE.match(meta["harvested_at"]):
        raise CandidateViolation(
            "harvested_at %r is not an ISO-8601 date (YYYY-MM-DD[...]) -- its "
            "first 10 characters become the staged filename's date part, so a "
            "free-form value here is a silently malformed filename, not a "
            "cosmetic defect" % meta["harvested_at"])

    # ---- body: exactly one fenced block introduced by '## Verbatim span' ----
    body_lines = lines[close_i + 1:]
    idx = 0
    while idx < len(body_lines) and body_lines[idx].rstrip("\r").strip() == "":
        idx += 1
    if idx >= len(body_lines) or body_lines[idx].rstrip("\r") != _VERBATIM_HEADER:
        raise CandidateViolation(
            "body is missing the '%s' header line (no summary, no prose bridge "
            "allowed -- DA-1)" % _VERBATIM_HEADER)
    idx += 1
    while idx < len(body_lines) and body_lines[idx].rstrip("\r").strip() == "":
        idx += 1
    if idx >= len(body_lines):
        raise CandidateViolation(
            "body has ZERO fenced code blocks (no fence found after the header)")
    open_line = body_lines[idx].rstrip("\r")
    if not _FENCE_RE.match(open_line):
        raise CandidateViolation(
            "body: expected a fenced code block (```) immediately after the "
            "header, found %r" % open_line)
    fence = open_line
    open_idx = idx

    close_idx = None
    for j in range(open_idx + 1, len(body_lines)):
        if body_lines[j].rstrip("\r") == fence:
            close_idx = j
            break
    if close_idx is None:
        raise CandidateViolation(
            "body: UNTERMINATED fenced code block (no closing '%s' found)" % fence)
    for j in range(close_idx + 1, len(body_lines)):
        if body_lines[j].rstrip("\r").strip() != "":
            raise CandidateViolation(
                "body: content found after the verbatim-span fenced block "
                "(exactly one fenced block expected -- a second block or "
                "trailing prose is a refusal)")

    span = "\n".join(body_lines[open_idx + 1:close_idx])

    if span_hash(span) != meta["span_sha256"]:
        raise CandidateViolation(
            "span_sha256 in frontmatter does not match sha256 of the body's "
            "verbatim span (a rewritten span is a different candidate)")
    if make_candidate_id(meta["session_id"], span) != meta["candidate_id"]:
        raise CandidateViolation(
            "candidate_id does not equal the recomputed "
            "session_id[:8]-span_hash[:16] value (mismatch refused)")

    return meta, span


def validate_candidate_file(path):
    """Convenience wrapper: read `path` as utf-8-sig (BOM-tolerant, matches
    origin.py's _read/staleness.py's convention), parse_candidate it."""
    with open(path, "r", encoding="utf-8-sig") as fh:
        text = fh.read()
    return parse_candidate(text)


def write_candidate_atomic(root, meta, span):
    """Render, then tempfile in the SAME directory -> flush -> fsync ->
    os.replace onto intake/session-candidates/<harvested_at date>-candidate-
    <candidate_id>.md. A half-written candidate must never exist under its
    final name (build-decisions Part 3's write discipline). Returns the
    filesystem path written.

    render_candidate() is called FIRST, before any filesystem touch -- a
    malformed `meta` (missing field) raises CandidateViolation with nothing
    ever created on disk, trivially satisfying "no temp file, no final file" on
    that failure class. `newline=""` on the fdopen'd handle is Windows-load-
    bearing: without it, Python's text-mode universal-newline translation would
    rewrite every bare "\\n" this module wrote to "\\r\\n" on Windows, silently
    corrupting a span that is supposed to be byte-exact."""
    text = render_candidate(meta, span)  # may raise -- nothing written yet
    date_part = str(meta.get("harvested_at", ""))[:10]
    candidate_id = str(meta["candidate_id"])
    # Charset gate, repeated here rather than assumed: this function is a public
    # entry point and a caller may reach it without having gone through
    # parse_candidate (the harvester assembles meta itself).
    if not _CANDIDATE_ID_RE.match(candidate_id):
        raise CandidateViolation(
            "candidate_id %r is outside the permitted charset -- refusing to "
            "build a filesystem path from it" % candidate_id)
    if not _HARVESTED_AT_RE.match(str(meta.get("harvested_at", ""))):
        raise CandidateViolation(
            "harvested_at %r is not an ISO-8601 date -- refusing to build a "
            "filesystem path from it" % (meta.get("harvested_at"),))
    dir_abs = os.path.join(root, CANDIDATES_STAGING_DIR)
    os.makedirs(dir_abs, exist_ok=True)
    final_path = os.path.join(dir_abs, "%s-candidate-%s.md" % (date_part, candidate_id))

    # CONTAINMENT ASSERTION (belt and braces to the charset gates above). A
    # charset bug would be silent; this is not. realpath both sides so a
    # symlinked staging directory, a case-insensitive volume, or any "..\" that
    # slipped through resolves before the comparison.
    dir_real = os.path.realpath(dir_abs)
    if os.path.dirname(os.path.realpath(final_path)) != dir_real:
        raise CandidateViolation(
            "refusing to write a candidate OUTSIDE the staging directory: %r "
            "resolves outside %r (path-containment violation)"
            % (final_path, dir_abs))

    fd, tmp_path = tempfile.mkstemp(prefix=".candidate-tmp-", suffix=".md", dir=dir_abs)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, final_path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    return final_path


# ================================================================== Part 4:
# the candidate LEDGER -- append-only chain, mirroring registrations.py
# ==================================================================

CANDIDATE_RECORD_REQUIRED = {
    "kind": str,
    "seq": int,
    "candidate_id": str,
    "event": str,
    "state": str,
    "candidate_class": str,
    "origin": str,
    "origin_max": str,
    "proposed_disposition": str,
    "span_sha256": str,
    "gate_artifact": (str, type(None)),
    "gate_commit": (str, type(None)),
    "note": str,
    "recorded_at": str,
    "prev_record_hash": (str, type(None)),
}
_ALLOWED_RECORD_KEYS = set(CANDIDATE_RECORD_REQUIRED)


def validate_candidate_record(record):
    """Schema gate at WRITE time (mirrors compile_core.validate_record's and
    registrations.validate_registration's posture). Refuses: unknown keys,
    missing/mistyped fields, kind != "candidate", state outside STATES,
    origin/origin_max outside origin.ORIGIN_ORDER, candidate_class/
    proposed_disposition outside their enums, a span_sha256 that isn't 64 hex.

    THE FLOOR CHECK (last, deliberately): `origin` may never be LESS
    RESTRICTIVE than "session-derived" -- a candidate record with origin
    "human" or "corpus" is refused, mechanically enforcing "a candidate cannot
    mint an un-taint it didn't earn" at the ledger layer too (the file-format
    fixed-literal in parse_candidate is the OTHER half of this same invariant).
    Looked up BY RANK (origin.ORIGIN_ORDER.index of both values), never a
    hardcoded integer, per the build instructions -- so this keeps working
    unchanged the moment the concurrent origin.py leg inserts "session-derived"
    into ORIGIN_ORDER. If "session-derived" is not YET present in
    ORIGIN_ORDER (this leg landing before that one), the floor cannot be
    EXPRESSED at all -- there is nothing to rank against -- so this check is a
    documented no-op in that state (never a crash, never a false-refuse, never
    a silently-wrong "pass"): every OTHER check above it still runs in full."""
    if not isinstance(record, dict):
        raise CandidateViolation("record is not an object")
    extra = set(record) - _ALLOWED_RECORD_KEYS
    if extra:
        raise CandidateViolation("unknown key(s): %s" % sorted(extra))
    for key, typ in CANDIDATE_RECORD_REQUIRED.items():
        if key not in record:
            raise CandidateViolation("missing field: %s" % key)
        if not isinstance(record[key], typ):
            raise CandidateViolation(
                "field %s has wrong type %s" % (key, type(record[key]).__name__))
    if record["kind"] != "candidate":
        raise CandidateViolation("kind must be 'candidate', got %r" % record["kind"])
    if record["state"] not in STATES:
        raise CandidateViolation("state %r not in STATES" % record["state"])
    order = _origin.ORIGIN_ORDER
    if record["origin"] not in order:
        raise CandidateViolation("origin %r not in origin.ORIGIN_ORDER" % record["origin"])
    if record["origin_max"] not in order:
        raise CandidateViolation(
            "origin_max %r not in origin.ORIGIN_ORDER" % record["origin_max"])
    if record["candidate_class"] not in CANDIDATE_CLASSES:
        raise CandidateViolation(
            "candidate_class %r not in %s" % (record["candidate_class"], CANDIDATE_CLASSES))
    if record["proposed_disposition"] not in DISPOSITIONS:
        raise CandidateViolation(
            "proposed_disposition %r not in %s"
            % (record["proposed_disposition"], DISPOSITIONS))
    if not _SPAN_HASH_RE.match(record["span_sha256"]):
        raise CandidateViolation(
            "span_sha256 is not 64 lowercase hex characters: %r" % record["span_sha256"])

    floor = "session-derived"
    if floor in order:  # see docstring: a documented no-op when not yet true
        rank = {o: i for i, o in enumerate(order)}
        if rank[record["origin"]] < rank[floor]:
            raise CandidateViolation(
                "origin %r is LESS RESTRICTIVE than the session-derived floor "
                "-- a candidate record may never carry human/corpus origin "
                "(mint-cannot-lower-trust invariant)" % record["origin"])


# ------------------------------------------------------------------ journal-dir
# parameterization (compile-core is untouched; JOURNAL_DIR there is a module
# constant, so the candidate chain is minted by pointing compile_core's
# directory helpers at CANDIDATES_LEDGER_DIR for the duration of the call).
class _CandidateJournal(object):
    """Wraps compile_core's append_record/check_chain against
    CANDIDATES_LEDGER_DIR instead of compile_core.JOURNAL_DIR, without editing
    compile_core.py. Not thread-safe across concurrent DIFFERENT directories in
    one process (fine: compile-core's own _SeqLock already serializes at the
    git-common-dir level, and this repo's build never runs candidate-chain +
    run-journal/registration-chain mints in the same process concurrently)."""

    def __init__(self, repo_root):
        self.repo_root = repo_root

    def __enter__(self):
        self._orig = _cc.JOURNAL_DIR
        _cc.JOURNAL_DIR = CANDIDATES_LEDGER_DIR
        return self

    def __exit__(self, *exc):
        _cc.JOURNAL_DIR = self._orig
        return False


def append_candidate_record(repo_root, record):
    """Mint one candidate record through compile_core.append_record, chained
    under receipts/candidates/. `record` supplies every field except seq and
    prev_record_hash (engine-owned, exactly compile_core's append_record
    contract). Validates the FULL candidate schema before handing to
    compile_core (compile_core's own validate_record expects RUN-JOURNAL
    fields a candidate record does not have) by monkey-patching
    compile_core.validate_record to validate_candidate_record for the duration
    of the call, restored immediately after (compile-core.py itself is
    untouched on disk; only the imported module object's attribute is swapped
    in-process)."""
    with _CandidateJournal(repo_root):
        _orig_validate = _cc.validate_record
        _cc.validate_record = validate_candidate_record
        try:
            return _cc.append_record(repo_root, record)
        finally:
            _cc.validate_record = _orig_validate


def check_candidate_chain(repo_root):
    """check_chain over receipts/candidates/ -- same gap/dup/hash-mismatch
    integrity compile_core.check_chain gives the run journal. Swaps
    validate_record the same way append_candidate_record does, so a
    mismatched-schema record among the candidates fails the SAME loud way a
    run-journal mismatch would. A missing directory is 0 records (compile_core.
    check_chain's own convention), never an error."""
    with _CandidateJournal(repo_root):
        _orig_validate = _cc.validate_record
        _cc.validate_record = validate_candidate_record
        try:
            return _cc.check_chain(repo_root)
        finally:
            _cc.validate_record = _orig_validate


def candidates_ledger_dir(repo_root):
    return os.path.join(repo_root, CANDIDATES_LEDGER_DIR)


def load_candidate_ledger(root):
    """Read receipts/candidates/<seq>.json in seq order, running the FULL chain
    check first (gap/dup/prev_record_hash mismatch -> loud CandidateViolation-
    wrapping compile_core.JournalViolation, never silent). Returns
    {candidate_id: record} -- the EFFECTIVE map: a later seq for the same
    candidate_id supersedes an earlier one (corrections are new records, never
    edits -- module docstring). A missing receipts/candidates/ directory is 0
    records, not an error (check_candidate_chain's own convention)."""
    n = check_candidate_chain(root)
    effective = {}
    cd = candidates_ledger_dir(root)
    for seq in range(1, n + 1):
        path = os.path.join(cd, "%d.json" % seq)
        with open(path, "r", encoding="utf-8") as fh:
            rec = json.load(fh)
        effective[str(rec["candidate_id"])] = rec  # later seq overwrites
    return effective


def ledger_history(root):
    """ALL candidate records in seq order (not just the effective/superseding
    map) -- the promotion gate (leg d) needs to scan every record ever written
    for single-use enforcement: has this gate_artifact already been consumed by
    an EARLIER record, even one that has since been superseded? The effective
    map alone cannot answer that; only the full history can."""
    n = check_candidate_chain(root)
    cd = candidates_ledger_dir(root)
    out = []
    for seq in range(1, n + 1):
        path = os.path.join(cd, "%d.json" % seq)
        with open(path, "r", encoding="utf-8") as fh:
            out.append(json.load(fh))
    return out


def effective_state(root, candidate_id):
    """The effective (highest-seq) state for `candidate_id`, or None if the
    candidate has no chain entry at all."""
    rec = load_candidate_ledger(root).get(str(candidate_id))
    return rec["state"] if rec else None


def is_terminal(state):
    return state in TERMINAL_STATES


def staged_files(root):
    """Sorted repo-relative .md paths directly under intake/session-candidates/
    (NOT recursive -- a nested subdirectory's files were never staged as
    candidates by this module and must never be swept in as if they were).
    Forward-slash normalised (Windows-safe). Missing directory -> [] (never
    raises -- an instance that has never harvested a candidate is a normal,
    not-inconclusive, state)."""
    d = os.path.join(root, CANDIDATES_STAGING_DIR)
    if not os.path.isdir(d):
        return []
    out = []
    for name in os.listdir(d):
        full = os.path.join(d, name)
        if os.path.isfile(full) and name.endswith(".md"):
            out.append(os.path.relpath(full, root).replace("\\", "/"))
    return sorted(out)


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def new_record(meta, state, note, event, gate_artifact=None, gate_commit=None,
               recorded_at=None):
    """Build a well-formed candidate ledger record dict from a PARSED candidate
    `meta` (as returned by parse_candidate/validate_candidate_file), a lifecycle
    `state`, and a human-readable `note` -- so callers (harvest-candidates.py,
    register-candidates.py, promote-candidate.py) never hand-assemble the
    record dict field-by-field and drift from the schema over time. `event` is
    the staged file's repo-relative path (forward-slash normalised here, so a
    caller on Windows never has to remember to do it). `origin`/`origin_max`/
    `candidate_class`/`proposed_disposition`/`span_sha256`/`candidate_id` are
    all carried through UNCHANGED from `meta` -- this function never invents or
    upgrades a value, it only reshapes the same facts into the ledger's field
    names. seq/prev_record_hash are deliberately absent -- engine-owned,
    supplied by append_candidate_record via compile_core.append_record."""
    return {
        "kind": "candidate",
        "candidate_id": meta["candidate_id"],
        "event": str(event).replace("\\", "/"),
        "state": state,
        "candidate_class": meta["candidate_class"],
        "origin": meta["origin"],
        "origin_max": meta["origin_max"],
        "proposed_disposition": meta["proposed_disposition"],
        "span_sha256": meta["span_sha256"],
        "gate_artifact": gate_artifact,
        "gate_commit": gate_commit,
        "note": note,
        "recorded_at": recorded_at or _now_iso(),
    }


# ------------------------------------------------------------------ test fixtures
def _fixture_meta(session_id="sess1234-abcd", span="a decision was stated",
                   candidate_class="decision-stated", origin_max="corpus",
                   proposed_disposition="transcribe-as-operator-event",
                   transcript_pointer="fixture-pointer",
                   harvested_at="2026-07-22T00:00:00"):
    """A well-formed candidate FILE meta dict for self-test fixtures. `origin` is
    always the fixed literal "session-derived" -- that part of the schema is
    unconditional, independent of origin.py's current ORIGIN_ORDER contents."""
    return {
        "kind": "session-candidate",
        "candidate_id": make_candidate_id(session_id, span),
        "session_id": session_id,
        "candidate_class": candidate_class,
        "origin": "session-derived",
        "origin_max": origin_max,
        "proposed_disposition": proposed_disposition,
        "transcript_pointer": transcript_pointer,
        "harvested_at": harvested_at,
        "span_sha256": span_hash(span),
    }


def _minimal_candidate_record(candidate_id, event, state="staged", note="fixture",
                               candidate_class="decision-stated", origin=None,
                               origin_max="corpus",
                               proposed_disposition="transcribe-as-operator-event",
                               span_sha256=None, gate_artifact=None, gate_commit=None,
                               recorded_at="2026-07-22T00:00:00"):
    """A well-formed LEDGER record for self-test PLUMBING fixtures (chain/seq/
    tamper/supersede mechanics -- orthogonal to the session-derived floor).
    `origin` defaults ADAPTIVELY: "session-derived" when origin.ORIGIN_ORDER
    already contains it, else "corpus" (the conservative, always-valid-today
    fallback) -- so the chain-plumbing self-test cases below pass BOTH before
    and after the concurrent origin.py leg lands, never needing to be rewritten
    when it does."""
    if origin is None:
        origin = "session-derived" if "session-derived" in _origin.ORIGIN_ORDER else "corpus"
    if span_sha256 is None:
        span_sha256 = span_hash("fixture span for %s" % candidate_id)
    return {
        "kind": "candidate", "candidate_id": candidate_id, "event": event,
        "state": state, "candidate_class": candidate_class, "origin": origin,
        "origin_max": origin_max, "proposed_disposition": proposed_disposition,
        "span_sha256": span_sha256, "gate_artifact": gate_artifact,
        "gate_commit": gate_commit, "note": note, "recorded_at": recorded_at,
    }


# ------------------------------------------------------------------ self-test
def self_test():
    global yaml
    import shutil
    import subprocess

    total = failed = skipped = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    def skip(name, note):
        nonlocal skipped
        skipped += 1
        print("  -- %s (SKIPPED: %s)" % (name, note))

    def _corrupt(text, old, new):
        if old not in text:
            raise AssertionError("self-test fixture bug: %r not found in text" % old)
        return text.replace(old, new, 1)

    has_floor = "session-derived" in _origin.ORIGIN_ORDER

    # ================================================================ constants
    case("CANDIDATE_CLASSES/DISPOSITIONS/STATES/TERMINAL_STATES match spec",
         CANDIDATE_CLASSES == ("decision-stated", "finding", "directive", "correction")
         and DISPOSITIONS == ("transcribe-as-operator-event", "plain-intake",
                              "discard-candidate")
         and STATES == ("staged", "registered", "promoted", "expired",
                        "discarded", "quarantined")
         and TERMINAL_STATES == frozenset({"promoted", "expired", "discarded",
                                           "quarantined"}))

    # ================================================================ span_hash /
    # make_candidate_id
    id_a = make_candidate_id("session-1234", "hello world")
    id_b = make_candidate_id("session-1234", "hello world")
    id_c = make_candidate_id("session-1234", "hello worlD")
    case("make_candidate_id is stable across calls", id_a == id_b)
    case("make_candidate_id differs when span differs by one character", id_a != id_c)
    case("make_candidate_id shape: session_id[:8]-span_hash[:16]",
         id_a == "%s-%s" % ("session-1234"[:8], span_hash("hello world")[:16]))
    case("span_hash matches hashlib.sha256(span.encode('utf-8')).hexdigest()",
         span_hash("abc") == hashlib.sha256("abc".encode("utf-8")).hexdigest())

    # ================================================================ render /
    # parse round-trip
    span0 = "the operator decided to ship v3 on Tuesday"
    session0 = "sess-abcdefgh"
    meta0 = _fixture_meta(session_id=session0, span=span0)
    good_text = render_candidate(meta0, span0)
    try:
        m, s = parse_candidate(good_text)
        case("well-formed candidate file parses cleanly", m == meta0 and s == span0)
    except CandidateViolation as e:
        case("well-formed candidate file parses cleanly (raised: %s)" % e, False)

    span_special = ("line one\r\nwith a fence marker:\r\n```\r\ninside a "
                    "triple-backtick fence\r\n```\r\nnon-ascii: café "
                    "☃ 中文\r\nend, no trailing newline")
    meta_special = _fixture_meta(session_id="sess-special", span=span_special)
    text_special = render_candidate(meta_special, span_special)
    try:
        m2, s2 = parse_candidate(text_special)
        case("round-trip: backticks + triple-backtick fence + CRLF + non-ascii "
             "span is byte-identical after render->parse",
             s2 == span_special and m2 == meta_special)
    except CandidateViolation as e:
        case("round-trip: backticks + triple-backtick fence + CRLF + non-ascii "
             "span is byte-identical after render->parse (raised: %s)" % e, False)

    # a span that itself ends in a newline (distinguishes "span had a trailing
    # newline" from "the separator we always insert")
    span_trailing_nl = "a span that ends with its own newline\n"
    meta_tnl = _fixture_meta(session_id="sess-tnl", span=span_trailing_nl)
    text_tnl = render_candidate(meta_tnl, span_trailing_nl)
    try:
        m3, s3 = parse_candidate(text_tnl)
        case("round-trip preserves a span's OWN trailing newline exactly",
             s3 == span_trailing_nl)
    except CandidateViolation as e:
        case("round-trip preserves a span's OWN trailing newline exactly "
             "(raised: %s)" % e, False)

    # ================================================================ parse_candidate
    # refusals -- each its own case, message names the problem
    cases_bad = [
        ("kind != session-candidate refused",
         _corrupt(good_text, 'kind: "session-candidate"', 'kind: "not-a-candidate"'),
         "kind"),
        ("origin != 'session-derived' refused (mint-cannot-lower-trust invariant)",
         _corrupt(good_text, 'origin: "session-derived"', 'origin: "human"'),
         "origin"),
        ("origin_max outside ORIGIN_ORDER refused",
         _corrupt(good_text, 'origin_max: "corpus"', 'origin_max: "not-a-real-origin"'),
         "origin_max"),
        ("candidate_class outside enum refused",
         _corrupt(good_text, 'candidate_class: "decision-stated"',
                  'candidate_class: "not-a-class"'),
         "candidate_class"),
        ("proposed_disposition outside enum refused",
         _corrupt(good_text, 'proposed_disposition: "transcribe-as-operator-event"',
                  'proposed_disposition: "not-a-disposition"'),
         "proposed_disposition"),
        ("span_sha256 wrong shape (not 64 hex) refused",
         _corrupt(good_text, 'span_sha256: "%s"' % meta0["span_sha256"],
                  'span_sha256: "not-hex-at-all"'),
         "span_sha256"),
        ("span_sha256 mismatch (right shape, wrong value) refused",
         _corrupt(good_text, 'span_sha256: "%s"' % meta0["span_sha256"],
                  'span_sha256: "%s"' % ("0" * 64 if meta0["span_sha256"] != "0" * 64
                                         else "1" * 64)),
         "span_sha256"),
        ("candidate_id mismatch (spoofed id) refused",
         _corrupt(good_text, 'candidate_id: "%s"' % meta0["candidate_id"],
                  'candidate_id: "%s"' % ("x" * 8 + "-" + "y" * 16)),
         "candidate_id"),
        ("unknown frontmatter key refused",
         good_text.replace('---\n', '---\nextra_field: "nope"\n', 1),
         "extra_field"),
        ("missing frontmatter key refused",
         good_text.replace('transcript_pointer: "%s"\n' % meta0["transcript_pointer"],
                           '', 1),
         "transcript_pointer"),
        ("frontmatter that is not a YAML mapping refused",
         "---\n- just\n- a\n- list\n---\n\n## Verbatim span\n\n```\nx\n```\n",
         "mapping"),
        ("body with ZERO fenced code blocks refused",
         good_text.split(_VERBATIM_HEADER)[0] + _VERBATIM_HEADER
         + "\n\nno fence here at all\n",
         "fenced code block"),
        ("body with a second fenced block / trailing content refused",
         good_text + "trailing junk after the block\n",
         "after the verbatim-span fenced block"),
        ("body missing the '## Verbatim span' header refused",
         good_text.replace(_VERBATIM_HEADER + "\n", "", 1),
         "header"),
        # ---- id charset: these ids become a FILESYSTEM PATH ----------------
        # The traversal case is the one the recompute cannot catch: a
        # session_id of "../../etc" recomputes to candidate_id "../../e-<hex>"
        # PERFECTLY, so the id-equality check passes it. Only the charset gate
        # (which runs BEFORE the recompute) stops it.
        ("session_id with path traversal refused (charset gate runs before the "
         "recompute -- a traversing id recomputes to itself)",
         _corrupt(good_text, 'session_id: "%s"' % meta0["session_id"],
                  'session_id: "../../etc"'),
         "session_id"),
        ("candidate_id with path traversal refused (charset gate)",
         _corrupt(good_text, 'candidate_id: "%s"' % meta0["candidate_id"],
                  'candidate_id: "../../evil"'),
         "candidate_id"),
        ("candidate_id containing a path separator refused (charset gate)",
         _corrupt(good_text, 'candidate_id: "%s"' % meta0["candidate_id"],
                  'candidate_id: "sub/dir-abc"'),
         "candidate_id"),
        ("candidate_id starting with '.' refused (dotfile / temp-prefix "
         "collision)",
         _corrupt(good_text, 'candidate_id: "%s"' % meta0["candidate_id"],
                  'candidate_id: ".candidate-tmp-x"'),
         "candidate_id"),
        ("harvested_at that is not an ISO-8601 date refused (its first 10 "
         "chars become the staged filename's date part)",
         _corrupt(good_text, 'harvested_at: "%s"' % meta0["harvested_at"],
                  'harvested_at: "whenever"'),
         "harvested_at"),
    ]
    for name, bad_text, keyword in cases_bad:
        try:
            parse_candidate(bad_text)
            case(name, False)
        except CandidateViolation as e:
            case(name + " (message names the problem)", keyword in str(e))

    # ---- id charset at the OTHER two entry points (a caller can reach these
    # without ever going through parse_candidate) -------------------------------
    try:
        make_candidate_id("../../etc", "some span")
        case("make_candidate_id refuses a path-traversing session_id", False)
    except CandidateViolation as e:
        case("make_candidate_id refuses a path-traversing session_id",
             "session_id" in str(e))
    try:
        make_candidate_id("ok-session", "some span")
        case("make_candidate_id accepts a well-formed session_id", True)
    except CandidateViolation:
        case("make_candidate_id accepts a well-formed session_id", False)

    base_trav = tempfile.mkdtemp(prefix="candidates-traversal-")
    try:
        # Hand-assemble a meta the way the harvester does (bypassing
        # parse_candidate entirely) and prove the write path refuses it -- and,
        # crucially, that NOTHING was created anywhere outside staging.
        meta_trav = dict(_fixture_meta(session_id="sess-trav", span="escape me"))
        meta_trav["candidate_id"] = "../../../evil"
        try:
            write_candidate_atomic(base_trav, meta_trav, "escape me")
            case("write_candidate_atomic refuses a traversing candidate_id", False)
        except CandidateViolation as e:
            case("write_candidate_atomic refuses a traversing candidate_id",
                 "candidate_id" in str(e) or "containment" in str(e))
        escaped = [p for p in os.listdir(base_trav) if p.endswith(".md")]
        case("write_candidate_atomic traversal refusal created NOTHING outside "
             "staging", escaped == [])

        meta_date = dict(_fixture_meta(session_id="sess-date", span="bad date"))
        meta_date["harvested_at"] = "whenever"
        try:
            write_candidate_atomic(base_trav, meta_date, "bad date")
            case("write_candidate_atomic refuses a non-ISO harvested_at", False)
        except CandidateViolation as e:
            case("write_candidate_atomic refuses a non-ISO harvested_at",
                 "harvested_at" in str(e))
    finally:
        shutil.rmtree(base_trav, ignore_errors=True)

    # unterminated fence: strip the closing fence, leaving the block never closed
    fence0 = _pick_fence(span0)
    suffix0 = "\n" + fence0 + "\n"
    if good_text.endswith(suffix0):
        unterminated = good_text[:-len(suffix0)] + "\n"
        try:
            parse_candidate(unterminated)
            case("UNTERMINATED fenced code block refused", False)
        except CandidateViolation as e:
            case("UNTERMINATED fenced code block refused (message names it)",
                 "UNTERMINATED" in str(e) or "unterminated" in str(e).lower())
    else:
        case("UNTERMINATED fenced code block refused (fixture construction)", False)

    # PyYAML absent -> clean refusal, never a crash
    orig_yaml = yaml
    yaml = None
    try:
        try:
            parse_candidate(good_text)
            case("PyYAML absent -> parse_candidate refuses cleanly (not a crash)", False)
        except CandidateViolation as e:
            case("PyYAML absent -> parse_candidate refuses cleanly (not a crash)",
                 "PyYAML" in str(e))
    finally:
        yaml = orig_yaml

    # ================================================================ write_candidate_atomic
    tmp_root = tempfile.mkdtemp(prefix="candidates-write-")
    try:
        meta_w = _fixture_meta(session_id="sess-write", span="write me",
                               harvested_at="2026-07-22T09:00:00")
        path_w = write_candidate_atomic(tmp_root, meta_w, "write me")
        expected_name = "2026-07-22-candidate-%s.md" % meta_w["candidate_id"]
        case("write_candidate_atomic writes to the expected final path",
             os.path.basename(path_w) == expected_name and os.path.isfile(path_w))
        staging_dir = os.path.join(tmp_root, CANDIDATES_STAGING_DIR)
        leftover_tmp = [f for f in os.listdir(staging_dir)
                        if f != os.path.basename(path_w)]
        case("write_candidate_atomic leaves no temp file behind on success",
             leftover_tmp == [])
        m_w, s_w = validate_candidate_file(path_w)
        case("the written candidate file parses back correctly (utf-8-sig read)",
             m_w == meta_w and s_w == "write me")

        # simulated write-stage failure (os.replace raises): no file under the
        # final name, no orphan temp file -- the exception propagates, never
        # silently swallowed.
        meta_w2 = _fixture_meta(session_id="sess-write2", span="write me too",
                                harvested_at="2026-07-22T10:00:00")
        orig_replace = os.replace

        def _boom(*_a, **_k):
            raise OSError("simulated disk failure")

        os.replace = _boom
        try:
            try:
                write_candidate_atomic(tmp_root, meta_w2, "write me too")
                case("simulated write-stage failure propagates (never swallowed)", False)
            except OSError:
                case("simulated write-stage failure propagates (never swallowed)", True)
        finally:
            os.replace = orig_replace
        expected_name2 = "2026-07-22-candidate-%s.md" % meta_w2["candidate_id"]
        final2 = os.path.join(staging_dir, expected_name2)
        case("failed write leaves NO file under the final name",
             not os.path.isfile(final2))
        leftover_tmp2 = [f for f in os.listdir(staging_dir)
                         if f != os.path.basename(path_w)]
        case("failed write leaves no orphan temp file behind", leftover_tmp2 == [])

        # render failure (missing meta field): nothing touches disk at all
        before_listing = sorted(os.listdir(staging_dir))
        try:
            write_candidate_atomic(tmp_root, {"kind": "session-candidate"}, "span")
            case("render failure (malformed meta) raises before any disk write", False)
        except CandidateViolation:
            after_listing = sorted(os.listdir(staging_dir))
            case("render failure (malformed meta) raises before any disk write "
                 "(directory listing unchanged)", after_listing == before_listing)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # ================================================================ ledger
    # PLUMBING: seq/chain/tamper/supersede -- adaptive origin, works either
    # side of the concurrent origin.py "session-derived" landing.
    base = tempfile.mkdtemp(prefix="cands-")
    try:
        subprocess.run(["git", "-C", base, "init", "-q"], capture_output=True)
        subprocess.run(["git", "-C", base, "config", "user.email", "t@t"],
                       capture_output=True)
        subprocess.run(["git", "-C", base, "config", "user.name", "t"],
                       capture_output=True)

        rec1 = _minimal_candidate_record(
            "cand-aaa11111",
            "intake/session-candidates/2026-07-22-candidate-cand-aaa11111.md",
            state="staged", note="harvested")
        rec2 = _minimal_candidate_record(
            "cand-bbb22222",
            "intake/session-candidates/2026-07-22-candidate-cand-bbb22222.md",
            state="staged", note="harvested")

        s1, _p1 = append_candidate_record(base, rec1)
        s2, _p2 = append_candidate_record(base, rec2)
        case("candidate chain lives at receipts/candidates/, seq 1,2",
             (s1, s2) == (1, 2)
             and os.path.isfile(os.path.join(base, "receipts", "candidates", "1.json"))
             and not os.path.isdir(os.path.join(base, "receipts", "journal")))
        case("candidate chain check green (2 records)", check_candidate_chain(base) == 2)

        eff = load_candidate_ledger(base)
        case("load_candidate_ledger returns both candidates",
             set(eff) == {"cand-aaa11111", "cand-bbb22222"})

        hist = ledger_history(base)
        case("ledger_history returns ALL records in seq order",
             [r["seq"] for r in hist] == [1, 2])

        # supersedes-by-later-seq: cand-aaa11111 staged -> registered
        rec1b = _minimal_candidate_record("cand-aaa11111", rec1["event"],
                                          state="registered", note="reconciler registered")
        s3, _p3 = append_candidate_record(base, rec1b)
        case("supersedes-by-later-seq: staged then registered -> effective "
             "state 'registered'",
             s3 == 3 and effective_state(base, "cand-aaa11111") == "registered"
             and effective_state(base, "cand-bbb22222") == "staged")
        case("effective_state on an unknown candidate_id returns None",
             effective_state(base, "cand-does-not-exist") is None)
        case("is_terminal: 'registered' is not terminal, 'promoted' is",
             is_terminal("registered") is False and is_terminal("promoted") is True)

        # tamper the chain: corrupt seq 2's prev_record_hash -> loud failure
        p2_path = os.path.join(base, "receipts", "candidates", "2.json")
        rec2_disk = json.load(open(p2_path, encoding="utf-8"))
        rec2_disk["prev_record_hash"] = "0" * 64
        open(p2_path, "w", encoding="utf-8").write(json.dumps(rec2_disk))
        try:
            check_candidate_chain(base)
            case("candidate chain tamper (hash mismatch) fails loud", False)
        except _cc.JournalViolation as e:
            case("candidate chain tamper (hash mismatch) fails loud",
                 "mismatch" in str(e))
        try:
            load_candidate_ledger(base)
            case("load_candidate_ledger refuses over a tampered chain", False)
        except _cc.JournalViolation:
            case("load_candidate_ledger refuses over a tampered chain", True)

        case("compile_core.JOURNAL_DIR restored to receipts/journal after "
             "candidate-chain calls",
             _cc.JOURNAL_DIR == os.path.join("receipts", "journal"))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    # ================================================================ validate_candidate_record
    # direct unit refusals
    good_rec = dict(_minimal_candidate_record("cand-good1234",
                    "intake/session-candidates/x.md"), seq=1, prev_record_hash=None)
    try:
        validate_candidate_record(good_rec)
        case("well-formed candidate record validates", True)
    except CandidateViolation as e:
        case("well-formed candidate record validates (raised: %s)" % e, False)

    bad_key = dict(good_rec, extra_field="nope")
    try:
        validate_candidate_record(bad_key)
        case("unknown key refused", False)
    except CandidateViolation as e:
        case("unknown key refused", "extra_field" in str(e))

    bad_state = dict(good_rec, state="not-a-state")
    try:
        validate_candidate_record(bad_state)
        case("bad state refused", False)
    except CandidateViolation as e:
        case("bad state refused", "state" in str(e))

    bad_kind = dict(good_rec, kind="not-a-candidate")
    try:
        validate_candidate_record(bad_kind)
        case("kind != 'candidate' refused", False)
    except CandidateViolation as e:
        case("kind != 'candidate' refused", "kind" in str(e))

    bad_shape = dict(good_rec, span_sha256="not-hex")
    try:
        validate_candidate_record(bad_shape)
        case("bad span_sha256 shape refused", False)
    except CandidateViolation as e:
        case("bad span_sha256 shape refused", "span_sha256" in str(e))

    if has_floor:
        bad_origin_human = dict(good_rec, origin="human")
        try:
            validate_candidate_record(bad_origin_human)
            case("origin: human refused (less restrictive than session-derived "
                 "floor)", False)
        except CandidateViolation as e:
            case("origin: human refused (less restrictive than session-derived "
                 "floor)", "human" in str(e) and "session-derived" in str(e))

        bad_origin_corpus = dict(good_rec, origin="corpus")
        try:
            validate_candidate_record(bad_origin_corpus)
            case("origin: corpus refused (less restrictive than session-derived "
                 "floor)", False)
        except CandidateViolation as e:
            case("origin: corpus refused (less restrictive than session-derived "
                 "floor)", "corpus" in str(e) and "session-derived" in str(e))
    else:
        skip("origin: human refused (session-derived floor)",
             "origin.ORIGIN_ORDER does not yet contain 'session-derived' -- a "
             "concurrent leg lands it; the floor cannot be exercised until then")
        skip("origin: corpus refused (session-derived floor)",
             "origin.ORIGIN_ORDER does not yet contain 'session-derived' -- a "
             "concurrent leg lands it; the floor cannot be exercised until then")

    # ================================================================ new_record
    meta_nr = _fixture_meta(session_id="sess-newrec",
                            span="a decision was stated for new_record")
    rec_nr = new_record(meta_nr, "staged", "harvested from session sess-newrec",
                        "intake\\session-candidates\\2026-07-22-candidate-%s.md"
                        % meta_nr["candidate_id"])
    case("new_record builds the correct dict shape from parsed meta "
         "(forward-slash normalised event)",
         rec_nr["kind"] == "candidate"
         and rec_nr["candidate_id"] == meta_nr["candidate_id"]
         and rec_nr["state"] == "staged"
         and rec_nr["candidate_class"] == meta_nr["candidate_class"]
         and rec_nr["origin"] == meta_nr["origin"]
         and rec_nr["origin_max"] == meta_nr["origin_max"]
         and rec_nr["proposed_disposition"] == meta_nr["proposed_disposition"]
         and rec_nr["span_sha256"] == meta_nr["span_sha256"]
         and rec_nr["gate_artifact"] is None and rec_nr["gate_commit"] is None
         and rec_nr["note"] == "harvested from session sess-newrec"
         and rec_nr["event"] == "intake/session-candidates/2026-07-22-candidate-%s.md"
             % meta_nr["candidate_id"]
         and "seq" not in rec_nr and "prev_record_hash" not in rec_nr)

    if has_floor:
        base_nr = tempfile.mkdtemp(prefix="cands-newrec-")
        try:
            subprocess.run(["git", "-C", base_nr, "init", "-q"], capture_output=True)
            subprocess.run(["git", "-C", base_nr, "config", "user.email", "t@t"],
                           capture_output=True)
            subprocess.run(["git", "-C", base_nr, "config", "user.name", "t"],
                           capture_output=True)
            s_nr, _p_nr = append_candidate_record(base_nr, rec_nr)
            case("new_record's real output (session-derived origin) appends "
                 "cleanly end-to-end", s_nr == 1 and check_candidate_chain(base_nr) == 1)
        finally:
            shutil.rmtree(base_nr, ignore_errors=True)
    else:
        skip("new_record's real output appends end-to-end (session-derived origin)",
             "origin.ORIGIN_ORDER does not yet contain 'session-derived' -- a "
             "concurrent leg lands it")

    # ================================================================ staged_files
    base_sf = tempfile.mkdtemp(prefix="cands-staged-")
    try:
        case("staged_files on a missing intake/session-candidates/ dir -> []",
             staged_files(base_sf) == [])
        sdir = os.path.join(base_sf, CANDIDATES_STAGING_DIR)
        os.makedirs(sdir)
        open(os.path.join(sdir, "2026-07-22-candidate-aaa.md"), "w",
            encoding="utf-8").write("x")
        open(os.path.join(sdir, "2026-07-22-candidate-bbb.md"), "w",
            encoding="utf-8").write("x")
        open(os.path.join(sdir, "not-a-candidate.txt"), "w", encoding="utf-8").write("x")
        sub = os.path.join(sdir, "nested")
        os.makedirs(sub)
        open(os.path.join(sub, "2026-07-22-candidate-should-not-appear.md"), "w",
            encoding="utf-8").write("x")
        files = staged_files(base_sf)
        case("staged_files lists only top-level .md files, sorted, forward-slash",
             files == ["intake/session-candidates/2026-07-22-candidate-aaa.md",
                       "intake/session-candidates/2026-07-22-candidate-bbb.md"])
        case("staged_files does NOT recurse into a subdirectory",
             all("nested" not in f for f in files))
    finally:
        shutil.rmtree(base_sf, ignore_errors=True)

    if failed:
        print("candidates self-test: FAIL (%d/%d, %d skipped)"
             % (total - failed, total, skipped))
        return 1
    note = ("" if skipped == 0 else
           " (%d SKIPPED -- origin.ORIGIN_ORDER lacks 'session-derived' pending "
           "a concurrent leg)" % skipped)
    print("candidates self-test: PASS (%d/%d)%s" % (total, total, note))
    return 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    print("usage: candidates.py --self-test  (library module, imported by "
          "harvest-candidates.py / register-candidates.py / promote-candidate.py)")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
