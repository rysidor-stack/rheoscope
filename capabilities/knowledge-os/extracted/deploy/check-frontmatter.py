#!/usr/bin/env python3
"""check-frontmatter.py -- contract-layer determinism sensor (knowledge-os, v3).

Validates YAML frontmatter against KNOWN_KEYS across the knowledge pipeline
(raw/ wiki/ roadmap/ receipts/) AND the engine-managed *derivation block*
(memory-engine-v3-spec.md §5 -- the v2/EXPECTED branch).

Doctrine (spec §12): sensors DEGRADE, never block -- they report and exit 0 so a
formatter or a missing key never halts a session -- with a single, narrow class of hard
refusal, both members structural ("this block cannot be deterministically machine-read"),
never a content judgment: (1) a derivation block whose `schema_version` does not match this
sensor's SCHEMA_VERSION (forward/backward skew would silently mis-read machine-critical
blocks); and (2) a derivation block that is NOT line-representable -- it uses YAML the
line-contract cannot faithfully parse (merge key, anchor/alias/tag on a guarded field,
top-level flow mapping, tab indent, quoted top-level key), which could smuggle a guarded
field past this reader to a full-YAML consumer (the LangSec parity refusal; see the guard
below and contract-layer-langsec-adjudication §5-§6). Spec §12 makes these the documented
exceptions to "degrade, never block."

This is the repo's one proven corpus-wide killer class made mechanical: the
2026-06-09 formatter incident corrupted frontmatter at scale, and P1 mints new
machine-critical derivation blocks into that blast radius. The sensor exists so a
malformed block is caught before any reader trusts it.

Formatter-corruption tripwire (back-ported from the live wiki's sibling sensor @ 3a66ddb, born of the
same incident): beyond the frontmatter schema above, this also catches the formatter's two other
signatures -- FLATTENed frontmatter (keys reflowed onto one prose line) and collapsed GFM table
BODIES (TABLE-FUSE / TABLE-JAM / TABLE-NOSEP). Those are WARN-level (degrade); run --strict to make
any finding fail (a pre-commit / CI gate). The detection engine is byte-faithful to the source
sensor; its fixtures ride in --self-test. Precision over recall -- a near-silent sensor everyone
trusts beats a noisy one everyone mutes; do not loosen the empirically-tuned thresholds.

No PyYAML runtime REQUIREMENT: the contract layer adds NO runtime dependency. The
parser is a line-based linter (top-level key membership + the strip-region), not a full
YAML loader -- sufficient for KNOWN_KEYS validation, and dependency-free everywhere. The
LangSec parity guard keeps that line-reader honest by REFUSING any block it cannot faithfully
read (see below). PyYAML appears inside self_test() (a TEST-ONLY import, skipped if
absent) to prove line-read/AST parity on the guarded fields, and -- opportunistically,
degrade-never-require (v3.0-82) -- in the FLATTEN detector, where a block that PARSES as a
legal YAML mapping suppresses the flatten heuristic: key-shaped PROSE inside a legal folded
scalar or quoted string is not formatter flattening, and two real files were false-accused
before parse-first landed. With PyYAML absent the heuristic still runs, its finding tagged
unverified so the briefing layer cannot state it as fact.

Usage:
  check-frontmatter.py [PATH ...]   scan files/dirs (default: raw wiki roadmap receipts
                                    under the resolved root)
  check-frontmatter.py --root DIR   resolve the default corpus dirs under DIR. Default
                                    root when absent = the parent of the deploy/ dir
                                    holding this script (family root standard, silence-
                                    sweep 2026-08-04) -- NEVER the CWD, so a wrong
                                    working directory can no longer make the sensor
                                    answer about a tree it never located.
  check-frontmatter.py --self-test  run embedded fixtures (CL-3); exit 0 if all pass
  check-frontmatter.py --strict     exit 1 on any finding (not just hard refusals)

Exit codes: 0 = clean (or findings, degrade mode) | 1 = a hard refusal (schema skew),
or any finding under --strict, or a self-test failure | 2 = INCONCLUSIVE -- no PATH
args and NONE of the subject corpus dirs (raw/ wiki/ roadmap/ receipts/) exist under
the resolved root: the sensor never located its tree, so it issues NO verdict (fail-
honest, silence-sweep S3). Holds under --strict too -- an unlocated tree is never a
pass AND never a finding-fail; it is its own condition.
"""

import os
import re
import sys

# Optional, degrade-never-require (v3.0-82): used ONLY by _check_flatten's parse-first
# suppression. Same guard pattern as doctor.py / check-caps.py.
try:
    import yaml as _yaml_opt
except ImportError:  # pragma: no cover
    _yaml_opt = None

# Family root standard (silence-sweep 2026-08-04; same pattern as check-loop-state.py):
# the default scan root is the parent of the deploy/ dir holding this script -- never
# the CWD. A caller may override with --root DIR.
_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.dirname(_HERE)

# The derivation-block schema version this sensor enforces. Matches the frozen
# memory-engine-v3-spec.md §5 (`schema_version: 3.2`). Skew is the one hard refusal.
SCHEMA_VERSION = "3.2"

# KNOWN_KEYS per frontmatter class (from docs/wiki-schema.md). required vs optional.
# Unknown top-level keys are reported as findings (degrade) -- the formatter-incident
# signature is keys mangled or dropped, so an unexpected key is a real signal.
#
# KEY-LIST EXTENSION under schema 3.2 (schema-drift adjudication, session B full-pass 2026-07-23, authorization
# deploy/evidence/operator-fullpass-2026-07-23.md): the live corpus had grown an
# established decision-lineage convention the schema never caught up to -- 88
# occurrences across 9 keys, flagged by /sweep since 07-21. SCHEMA_VERSION is NOT
# bumped: that constant versions the DERIVATION-BLOCK schema (skew-refused against
# live blocks), and this change touches only the per-class KNOWN_KEYS lists.
# Adjudicated RECOGNIZED
# (optional, never required):
#   decision_method (56) / supersedes (14) / amends / informs -- decision lineage;
#   type + related_dispatch -- the dispatch-result event species;
#   topics -- a tags sibling in 2 files;
#   author -- prose provenance annotation;
#   origin (8) -- SELF-DECLARED AND NEVER HONORED: registration computes origin
#     exclusively via origin.py's rule cascade (backfill-registrations._origin_for
#     passes NO declared_origin -- verified 2026-07-23: a raw event declaring
#     `origin: human` registered as `corpus`). Recognizing the KEY silences schema
#     noise; it does not, and must never, make the VALUE load-bearing.
#   canonical -- doc<->sensor key conflict (drift cluster #3, silence-sweep
#     2026-08-04; live-confirmed on the Ultrapak instance 2026-08-05): wiki-schema
#     REQUIRES the key on flagged raw files and the sibling check-knowledge-debt.py
#     ENFORCES it (presence + dangling-target checks live there), while this sensor
#     flagged it as unknown -- a schema-compliant raw drew a permanent WARN.
#     Recognized here as optional ONLY: requiredness stays check-knowledge-debt's
#     job; this entry exists solely to stop the false unknown-key WARN.
RAW_KEYS = {
    "required": {"source", "date", "tags", "summary"},
    "optional": {"domain", "compile", "informed_by", "decision_method",
                 "supersedes", "amends", "informs", "type", "related_dispatch",
                 "topics", "author", "origin", "canonical"},
}
WIKI_KEYS = {
    "required": {"title", "domain", "scope", "last_updated", "sources", "confidence"},
    "optional": {"cross_links"},
}
ROADMAP_KEYS = {
    "required": {"title", "phase", "status", "last_updated"},
    # sources + audited: live-side roadmap convention (2026-07-23 adjudication, see RAW_KEYS)
    "optional": {"sources", "audited"},
}
# Receipts share an envelope but vary by type; validate the envelope, allow the rest.
# Canon home: docs/wiki-schema.md section 7 (compile receipt schema). journal_seq +
# run_commit joined the canon 2026-07-28 with the engine wiring; this list trailed
# until the 2026-08-05 single-homing pass (drift cluster 4).
RECEIPT_KEYS = {
    "required": {"type", "timestamp"},
    "optional": {
        "duration_minutes", "token_cost", "raw_inputs", "articles_modified",
        "scope_tags", "cross_links_changed", "confidence_changes",
        "meaningful_change", "circuit_breaker_hit", "review_compacted",
        "pending_cascade", "notes", "journal_seq", "run_commit",
    },
    "lenient": True,  # type-specific keys vary; unknown keys are INFO, not WARN
}

# Derivation block (spec §5). All keys required; enums checked where the spec fixes them.
DERIVATION_KEYS = {
    "required": {
        "schema_version", "view", "summary", "entities", "status", "tier",
        "consumed_status", "origin_max", "subscribes", "bundle", "verified",
    },
    "optional": set(),
}
ENUMS = {
    "view": {"topic", "dashboard", "index", "briefing"},
    "status": {"active", "superseded"},
    "tier": {"T1", "T2", "T3", "T4"},
    "consumed_status": {"verified-consumed", "legacy-assumed", "audit-pending"},
}

DERIV_START = "# --- derivation"
DERIV_END = "# --- /derivation"

# --- formatter-corruption back-port (from the live wiki's check-frontmatter.py @ 3a66ddb) ------
# A sibling tripwire born of the same 2026-06-09 formatter incident this sensor's docstring cites.
# It catches on-save-formatter damage the contract checks above do NOT: collapsed GFM table BODIES
# and FLATTENed frontmatter. Domain-neutral GFM/YAML signatures; stdlib-only (no PyYAML). The
# detection engine below is byte-faithful to the source sensor; findings are WARN (degrade, spec
# §12) so the sweep never blocks — run --strict to make any finding fail (a pre-commit/CI gate).
FUSE_RE = re.compile(r"[a-z0-9]`[A-Z]")             # fused cell: lower/digit ` Capital (collapsed)
JAM_RE = re.compile(r"(?:[A-Z][a-z]+){2,}\d")       # jammed TitleCase header run + digit
ROW_BOUNDARY_RE = re.compile(r"\d+[A-Z][a-z]")      # <numericCode><Label> fused-row signature
ROW_BOUNDARY_MIN = 2
INLINE_CODE_RE = re.compile(r"`[^`]*`")             # strip before JAM (legit digit/case jams)
URL_RE = re.compile(r"\b(?:https?://|www\.)\S+")    # strip before JAM
PIPE_ROW_RE = re.compile(r"^\|.*\|$")               # a live GFM row keeps its pipes
SEP_ROW_RE = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")    # fenced-code grammar (char+len tracked)

# FLATTEN detector: a frontmatter physical line carrying >=2 known keys after its own leading key
# means the formatter reflowed the YAML block as prose. KNOWN_KEYS is derived from THIS sensor's
# own frontmatter schemas (single source) — not a separately-maintained list.
KNOWN_KEYS = tuple(sorted(
    RAW_KEYS["required"] | RAW_KEYS["optional"]
    | WIKI_KEYS["required"] | WIKI_KEYS["optional"]
    | ROADMAP_KEYS["required"] | ROADMAP_KEYS["optional"]
    | RECEIPT_KEYS["required"] | RECEIPT_KEYS["optional"]
))
KEY_TOKEN_RE = re.compile(r"\b(?:%s):" % "|".join(re.escape(k) for k in KNOWN_KEYS))
LEADING_KEY_RE = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*:\s*")
FLATTEN_MIN_EXTRA_KEYS = 2


class Finding:
    __slots__ = ("level", "path", "msg")

    def __init__(self, level, path, msg):
        self.level = level  # REFUSE | WARN | INFO
        self.path = path
        self.msg = msg

    def __str__(self):
        return "  [%s] %s: %s" % (self.level, self.path, self.msg)


def _extract_frontmatter(text):
    """Return the lines between a leading '---' fence and the next '---', or None."""
    if text.startswith("﻿"):  # strip a leading UTF-8 BOM so a BOM'd file's frontmatter
        text = text[1:]            # (KEYS / derivation / FLATTEN) is still seen, matching the body engine
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i]
    return "UNTERMINATED"


def _extract_derivation(text):
    """Return the lines inside the engine-managed strip region, or None."""
    lines = text.splitlines()
    start = end = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if start is None and s.startswith(DERIV_START):
            start = i
        elif start is not None and s.startswith(DERIV_END):
            end = i
            break
    if start is not None and end is not None and end > start:
        return lines[start + 1:end]
    if start is not None and end is None:
        return "UNTERMINATED"
    return None


def _top_level_keys(region_lines):
    """Map top-level `key: value` (indent 0, non-comment) within a region."""
    keys = {}
    for ln in region_lines:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if ln[:1] in (" ", "\t"):  # nested under a parent key
            continue
        m = re.match(r"^([A-Za-z0-9_]+):(.*)$", ln)
        if m:
            keys[m.group(1)] = m.group(2).strip()
    return keys


# --- LangSec parity guard (contract-layer-langsec-adjudication §5-§6) -----------------------------
# The reader above (_top_level_keys) is a line-based linter, NOT a YAML grammar. The 2026-07-01
# LangSec adjudication refuted Gemini Finding 7's parser-differential KILL for `schema_version` (on
# the block-scalar smuggle + duplicate-key forms the reader and a real YAML parser agree, or the
# reader refuses) but scoped that refutation to schema_version + those two shapes. This guard closes
# the GENERAL principle for BOTH security-load-bearing scalar fields (schema_version AND origin_max):
# a derivation block that uses YAML the deterministic line-contract cannot faithfully represent --
# a merge key, an anchor/alias/tag on a guarded field, a top-level flow mapping, tab indentation, or
# a quoted top-level key -- could smuggle a guarded field past this reader to a downstream full-YAML
# consumer (a parser differential). Rather than silently mis-read one of the machine-critical blocks,
# the sensor REFUSES (fail-closed). Legit engine-authored blocks use none of these (plain block
# mappings, scalar values, flow SEQUENCES like `entities: [x]`, block scalars `>`/`|`, nested
# mappings), so the false-refuse rate on real corpus is zero. --self-test proves the invariant
# mechanically against a PyYAML AST (a TEST-ONLY import; the sensor stays stdlib-only at runtime).
GUARDED_FIELDS = ("schema_version", "origin_max")
_INDIRECT_VALUE_RE = re.compile(r"^[&*!]")                    # anchor / alias / tag token as a value
_QUOTED_TOPKEY_RE = re.compile(r"""^\s*["'][^"']+["']\s*:""")  # a quoted top-level key
_MERGE_KEY_RE = re.compile(r"^<<\s*:")                       # YAML merge key (injects keys invisibly)
_TAB_INDENT_RE = re.compile(r"^[ ]*\t")                      # a tab in leading indentation


def _unquote_scalar(v):
    """Strip ONE matching pair of surrounding quotes from a scalar (so a legally quoted
    `schema_version: '3.2'` reads as 3.2, matching a YAML parser instead of false-refusing)."""
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


def _deriv_unrepresentable(region_lines):
    """Return a reason string if the derivation region uses YAML the line-contract cannot
    faithfully represent (=> a possible parser differential on a guarded field), else None.
    Scoped to constructs that can shadow / inject / indirect schema_version or origin_max."""
    for ln in region_lines:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if _TAB_INDENT_RE.match(ln):
            return "tab indentation (a full YAML parser rejects it; the line-reader silently mis-nests)"
        if ln[:1] not in (" ", "\t"):  # a top-level (indent-0) construct
            stripped = ln.lstrip()
            if _MERGE_KEY_RE.match(stripped):
                return "YAML merge key '<<:' (injects top-level keys the line-reader cannot see)"
            if stripped[:1] == "{":
                return "top-level flow mapping '{...}' (injects keys the line-reader cannot see)"
            if _QUOTED_TOPKEY_RE.match(ln):
                return "quoted top-level key (the line-reader only matches bare keys)"
    keys = _top_level_keys(region_lines)
    for f in GUARDED_FIELDS:
        v = keys.get(f)
        if v is not None and _INDIRECT_VALUE_RE.match(v.split("#", 1)[0].strip()):
            return ("anchor/alias/tag on '%s' value (line-reader captures the token, "
                    "not the resolved value)" % f)
    return None


def _classify(path):
    p = path.replace("\\", "/").lower()
    if "/raw/" in p or p.startswith("raw/"):
        return "raw"
    if "/roadmap/" in p or p.startswith("roadmap/"):
        return "roadmap"
    if "/receipts/" in p or p.startswith("receipts/"):
        return "receipt"
    if "/wiki/" in p or p.startswith("wiki/"):
        return "wiki"
    return None


# --- formatter-corruption engine (back-port; byte-faithful to that sensor @ 3a66ddb) ----

def split_frontmatter(text):
    """(block_str, had_close, body_start_line) -- BOM-aware. The body engine's frontmatter/body
    boundary. Kept distinct from _extract_frontmatter (used by the contract checks above) so the
    proven engine stays byte-faithful to the source sensor."""
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, False, 1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), True, i + 2
    return "\n".join(lines[1:]), False, len(lines) + 1


def check_body(text):
    """Yield (line, sev, reason) body-corruption violations. Skips the frontmatter block and any
    ```/~~~ fenced code. Verbatim detection engine: TABLE-FUSE / TABLE-JAM / TABLE-NOSEP."""
    _, _, body_start = split_frontmatter(text)
    raw = text[1:] if text.startswith("﻿") else text
    lines = raw.splitlines()
    in_fence = False
    fence_char = ""
    fence_len = 0
    prev_was_pipe_row = False
    for idx in range(body_start - 1, len(lines)):
        line = lines[idx]
        lineno = idx + 1
        m = FENCE_RE.match(line)
        if m:
            run, rest = m.group(1), m.group(2).strip()
            ch, ln = run[0], len(run)
            if not in_fence:
                in_fence, fence_char, fence_len = True, ch, ln   # opening fence
                prev_was_pipe_row = False
                continue
            if ch == fence_char and ln >= fence_len and rest == "":
                in_fence, fence_char, fence_len = False, "", 0    # valid CommonMark close
                prev_was_pipe_row = False
                continue
            # A fence-looking line INSIDE the block (nested/shorter fence or one with an info
            # string) is literal content, NOT a toggle -- stay in_fence. (Naive toggling desynced
            # on nested fences and exposed embedded code as a false TABLE-JAM; see self_test.)
            continue
        if in_fence:
            continue
        stripped = line.strip()
        is_pipe_row = bool(PIPE_ROW_RE.match(stripped))
        if "|" not in line:
            # Pipe-less line: a collapsed table (its pipes were eaten) lives here, never a live one.
            if FUSE_RE.search(line):
                yield (lineno, "TABLE-FUSE", _snippet(line, FUSE_RE))
            elif _is_jammed(line):
                yield (lineno, "TABLE-JAM", _snippet(line, JAM_RE))
        elif is_pipe_row and not prev_was_pipe_row and not SEP_ROW_RE.match(stripped):
            nxt = _next_nonblank(lines, idx + 1)
            if nxt is not None and not SEP_ROW_RE.match(nxt.strip()):
                yield (lineno, "TABLE-NOSEP", "table header row has no '|---|' separator below")
        prev_was_pipe_row = is_pipe_row


def _is_jammed(line):
    """True if a pipe-less line carries the backtick-less collapsed-table signature: a jammed
    TitleCase header run AND >= ROW_BOUNDARY_MIN fused row boundaries (after stripping inline-code
    and URLs, which legitimately carry digit/case jams). The dual gate is the FP suppressor."""
    residue = URL_RE.sub(" ", INLINE_CODE_RE.sub(" ", line))
    if not JAM_RE.search(residue):
        return False
    return len(ROW_BOUNDARY_RE.findall(residue)) >= ROW_BOUNDARY_MIN


def _next_nonblank(lines, start):
    for j in range(start, len(lines)):
        if lines[j].strip():
            return lines[j]
    return None


def _snippet(line, rx, width=60):
    """A short context window around the first regex match, for the report."""
    m = rx.search(line)
    s = max(0, m.start() - 12)
    frag = line[s:s + width].strip()
    return ("...%s..." % frag) if len(line) > width else frag


def _check_flatten(fm_lines):
    """Return a (sev, reason) FLATTEN tuple if any frontmatter line joins >= FLATTEN_MIN_EXTRA_KEYS
    known keys after its own leading key (the formatter's flatten signature), else None. fm_lines
    are the lines BETWEEN the --- fences, as _extract_frontmatter returns them.

    Parse-first (v3.0-82): a block PyYAML loads as a legal mapping is NOT flattened --
    key-shaped tokens inside a legal folded scalar (`summary: >-` prose carrying
    `source: ryan`-shaped labels) or a quoted string are prose, and the heuristic
    false-accused two real, valid files of being 'collapsed by an editor' (sweep
    2026-07-29 item 4). Genuinely flattened lines do NOT parse (a plain scalar cannot
    contain ': '), so they still fall through to the heuristic. With PyYAML absent the
    heuristic runs alone and its finding says so."""
    if _yaml_opt is not None:
        try:
            if isinstance(_yaml_opt.safe_load("\n".join(fm_lines)), dict):
                return None
        except _yaml_opt.YAMLError:
            pass    # unparseable: the heuristic below judges
    for line in fm_lines:
        if line.strip() == "---":
            break
        rest = LEADING_KEY_RE.sub("", line, count=1)
        if len(KEY_TOKEN_RE.findall(rest)) >= FLATTEN_MIN_EXTRA_KEYS:
            suffix = ("" if _yaml_opt is not None
                      else " [unverified heuristic -- PyYAML absent, parse-check "
                           "unavailable; treat as a lead, not a fact]")
            return ("FLATTEN", "frontmatter keys joined onto one line (formatter flattening): "
                    + line.strip()[:70] + suffix)
    return None


def _check_keys(found, spec, path, label, out):
    missing = spec["required"] - set(found)
    for k in sorted(missing):
        out.append(Finding("WARN", path, "%s missing required key: %s" % (label, k)))
    known = spec["required"] | spec["optional"]
    if not spec.get("lenient"):
        for k in sorted(set(found) - known):
            out.append(Finding("WARN", path, "%s unknown key: %s" % (label, k)))


def _check_enums(found, path, out):
    for key, allowed in ENUMS.items():
        if key in found and found[key]:
            val = found[key].split("#", 1)[0].strip()  # strip trailing comments
            if val and val not in allowed:
                out.append(Finding(
                    "WARN", path,
                    "derivation %s='%s' not in {%s}" % (key, val, ", ".join(sorted(allowed)))))


def check_text(text, path):
    """Validate one file's text. Returns a list of Finding."""
    out = []
    cls = _classify(path)

    fm = _extract_frontmatter(text)
    if fm == "UNTERMINATED":
        out.append(Finding("WARN", path, "frontmatter opened with '---' but never closed"))
    elif fm is not None:
        flat = _check_flatten(fm)
        if flat is not None:
            # FLATTEN explains any missing/unknown keys -- report it alone (precision over recall).
            out.append(Finding("WARN", path, "%s: %s" % flat))
        elif cls is not None:
            found = _top_level_keys(fm)
            spec = {"raw": RAW_KEYS, "wiki": WIKI_KEYS,
                    "roadmap": ROADMAP_KEYS, "receipt": RECEIPT_KEYS}[cls]
            _check_keys(found, spec, path, cls + " frontmatter", out)

    deriv = _extract_derivation(text)
    if deriv == "UNTERMINATED":
        out.append(Finding("WARN", path, "derivation block opened but never closed (%s)" % DERIV_END))
    elif deriv is not None:
        # LangSec parity refusal (adjudication §5-§6): a block using YAML the line-contract cannot
        # faithfully represent could smuggle a guarded field (schema_version/origin_max) past this
        # reader to a full-YAML consumer. Refuse rather than risk a parser differential on a
        # machine-critical block -- and skip the per-key checks (they would read mis-parsed garbage).
        unrep = _deriv_unrepresentable(deriv)
        if unrep is not None:
            out.append(Finding("REFUSE", path, "derivation block not line-representable: %s" % unrep))
        else:
            dfound = _top_level_keys(deriv)
            # The one content refusal: schema_version skew (spec §12). Quote-normalized so a legally
            # quoted `'3.2'` is not false-refused (it agrees with the YAML value; adjudication §6).
            sv = _unquote_scalar(dfound.get("schema_version", "").split("#", 1)[0].strip())
            if not sv:
                out.append(Finding("REFUSE", path, "derivation block has no schema_version"))
            elif sv != SCHEMA_VERSION:
                out.append(Finding(
                    "REFUSE", path,
                    "derivation schema_version='%s' != sensor SCHEMA_VERSION='%s' (skew; refusing)"
                    % (sv, SCHEMA_VERSION)))
            _check_keys(dfound, DERIVATION_KEYS, path, "derivation block", out)
            _check_enums(dfound, path, out)

    # Body-corruption sweep (back-port): collapsed GFM tables outside fenced code. WARN/degrade.
    for lineno, sev, reason in check_body(text):
        out.append(Finding("WARN", path, "%s (L%d): %s" % (sev, lineno, reason)))
    return out


def _iter_files(paths):
    for p in paths:
        if os.path.isfile(p):
            yield p
        elif os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                if os.sep + ".git" in root:
                    continue
                for f in files:
                    if f.endswith(".md"):
                        yield os.path.join(root, f)


def scan(paths, strict=False):
    findings = []
    for fp in _iter_files(paths):
        try:
            with open(fp, "r", encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError) as e:
            findings.append(Finding("WARN", fp, "unreadable: %s" % e))
            continue
        findings.extend(check_text(text, fp))

    refusals = [f for f in findings if f.level == "REFUSE"]
    if findings:
        for f in findings:
            print(str(f))
    else:
        print("check-frontmatter: clean (%d file(s) scanned, 0 findings)" % sum(1 for _ in _iter_files(paths)))
    if refusals:
        print("check-frontmatter: REFUSE -- %d hard refusal(s) (schema-skew or non-line-representable)"
              % len(refusals))
        return 1
    if strict and findings:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Embedded self-test (CL-3). Each case: text, classifying path, expected counts.
# ---------------------------------------------------------------------------

_VALID_RAW = """---
source: alice
date: 2026-06-20
tags: [systems, schema]
summary: One line.
---
body
"""

_RAW_MISSING = """---
source: alice
date: 2026-06-20
tags: [systems]
---
body
"""  # missing summary

_RAW_UNKNOWN = """---
source: alice
date: 2026-06-20
tags: [systems]
summary: One line.
bogus_key: x
---
body
"""

_RAW_CANONICAL = """---
source: alice
date: 2026-06-20
tags: [systems, schema]
summary: One line.
compile: false
canonical: wiki/systems/x.md
---
body
"""  # wiki-schema-mandated canonical: key -- must draw NO unknown-key finding

_VALID_DERIV = """---
title: Schema foundations
domain: systems
scope: build
last_updated: 2026-06-20
sources:
  - raw/2026-06-10-alice-x.md
confidence: high
---

# --- derivation (engine-managed; strip region) ---
schema_version: 3.2
view: topic
summary: One line for the catalog.
entities: [work-orders]
status: active
tier: T1
consumed_status: verified-consumed
origin_max: human
subscribes:
  entities: [work-orders]
  corpus: [<corpus-logical-name>:<artifact-path>]
bundle: [wiki/systems/schema-foundations.md]
verified:
  status: passed
# --- /derivation ---

# Title
"""

_DERIV_SKEW = _VALID_DERIV.replace("schema_version: 3.2", "schema_version: 3.1")
_DERIV_MISSING = _VALID_DERIV.replace("origin_max: human\n", "")
_DERIV_BAD_ENUM = _VALID_DERIV.replace("view: topic", "view: bogus")
_UNTERMINATED_FM = "---\nsource: alice\nbody with no close\n"


def self_test():
    global _yaml_opt    # rebound (and restored) hermetically for the PyYAML-absent case
    cases = [
        ("valid raw",        _VALID_RAW,        "raw/x.md",          0, 0),
        ("raw missing key",  _RAW_MISSING,      "raw/x.md",          1, 0),
        ("raw unknown key",  _RAW_UNKNOWN,      "raw/x.md",          1, 0),
        # drift cluster #3 fix: a schema-compliant canonical: raw is exactly clean
        ("raw canonical key",_RAW_CANONICAL,    "raw/x.md",          0, 0),
        ("valid derivation", _VALID_DERIV,      "wiki/systems/x.md", 0, 0),
        ("deriv skew",       _DERIV_SKEW,       "wiki/systems/x.md", 1, 1),
        ("deriv missing key",_DERIV_MISSING,    "wiki/systems/x.md", 1, 0),
        ("deriv bad enum",   _DERIV_BAD_ENUM,   "wiki/systems/x.md", 1, 0),
        ("unterminated fm",  _UNTERMINATED_FM,  "raw/x.md",          1, 0),
    ]
    failed = 0
    for name, text, path, exp_findings, exp_refusals in cases:
        out = check_text(text, path)
        refusals = sum(1 for f in out if f.level == "REFUSE")
        ok = (len(out) >= exp_findings) and (refusals == exp_refusals)
        # exact-zero cases must be exactly zero
        if exp_findings == 0:
            ok = (len(out) == 0)
        status = "ok " if ok else "XX "
        if not ok:
            failed += 1
        print("  %s %-18s findings=%d (exp>=%d) refusals=%d (exp=%d)"
              % (status, name, len(out), exp_findings, refusals, exp_refusals))
        if not ok:
            for f in out:
                print("        %s" % f)

    # Body-corruption + FLATTEN fixtures (back-port regression guard). Tested against the engine
    # DIRECTLY (check_body / _check_flatten), not check_text, so the contract's stricter frontmatter
    # schema does not interfere -- this isolates the detection logic, mirroring the source sensor.
    total = len(cases)
    PUBKEY = ('ssh_public_keys: "ssh-ed25519 '
              'AAAAC3NzaC1lZDI1NTE5AAAAIMZv1TceM2LX9GEymIws3aLooYnfDnM0+Zt8wYcO7mJj user@host"')
    FM = "---\ntitle: x\ndomain: systems\nscope: build\nlast_updated: 2026-06-16\n---\n"
    body_cases = [
        ("nested-fence-pubkey",  FM + "\n````markdown\n```yaml\n" + PUBKEY + "\n```\n````\n", 0),
        ("simple-fence-base64",  "# t\n\n```\nAAAAC3NzaC1lZDI1NTE5AAAAIMZv1TceM2LX9GEymIws3aLooYnfDnM0\n```\n", 0),
        ("collapsed-table-jam",  "# t\n\nRoleNameNotes1Draft2Active3Inactive\n", 1),
        ("collapsed-table-fuse", "# t\n\nsome cell value`Save more`Initialize here\n", 1),
    ]
    for name, text, exp_body in body_cases:
        total += 1
        nbody = len(list(check_body(text)))
        ok = (nbody == exp_body)
        if not ok:
            failed += 1
        print("  %s %-18s body_hits=%d (exp=%d)" % ("ok " if ok else "XX ", name, nbody, exp_body))
    total += 1
    flat_fm = _extract_frontmatter("---\ntitle: x domain: y scope: z last_updated: 2026\n---\n# t\n")
    flat_hit = (flat_fm not in (None, "UNTERMINATED")) and (_check_flatten(flat_fm) is not None)
    if not flat_hit:
        failed += 1
    print("  %s %-18s flatten=%s (exp=True)" % ("ok " if flat_hit else "XX ", "frontmatter-flatten", flat_hit))
    total += 1  # BOM regression: a leading UTF-8 BOM must not hide a flattened frontmatter
    bom_fm = _extract_frontmatter("﻿---\ntitle: x domain: y scope: z last_updated: 2026\n---\n# t\n")
    bom_hit = (bom_fm not in (None, "UNTERMINATED")) and (_check_flatten(bom_fm) is not None)
    if not bom_hit:
        failed += 1
    print("  %s %-18s flatten=%s (exp=True)" % ("ok " if bom_hit else "XX ", "flatten-with-bom", bom_hit))

    # FAIL-HONEST tree-absent cases (silence-sweep S3; tempdir fixture pattern per
    # check-loop-state.py's (1a3) envelope-resolution cases): a root holding NONE of the
    # corpus dirs must be INCONCLUSIVE exit 2 -- plain AND --strict alike (the old
    # behavior was exit 0 in both, a CI gate that gated nothing from a wrong CWD) --
    # while a root holding an existing-but-empty corpus dir stays a LOCATED tree
    # (exit 0, unchanged behavior).
    import shutil
    import tempfile
    d = tempfile.mkdtemp(prefix="cfm-root-")
    try:
        for name, argv, exp in [
            ("tree-absent",        ["check-frontmatter.py", "--root", d], 2),
            ("tree-absent-strict", ["check-frontmatter.py", "--strict", "--root", d], 2),
        ]:
            total += 1
            rc = main(argv)
            ok = (rc == exp)
            if not ok:
                failed += 1
            print("  %s %-18s exit=%s (exp=%d)" % ("ok " if ok else "XX ", name, rc, exp))
        os.makedirs(os.path.join(d, "raw"))
        total += 1
        rc = main(["check-frontmatter.py", "--root", d])
        ok = (rc == 0)
        if not ok:
            failed += 1
        print("  %s %-18s exit=%s (exp=0) -- empty-but-present corpus dir stays located"
              % ("ok " if ok else "XX ", "tree-located-empty", rc))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # v3.0-82 parse-first regression fixtures: the two real false-positive shapes from the
    # 2026-07-29 sweep. Both are LEGAL YAML whose prose carries >= FLATTEN_MIN_EXTRA_KEYS
    # key-shaped tokens -- without parse-first each false-fires; with it, both suppress.
    # (Skipped honestly if PyYAML is absent -- the suppression needs the parser.)
    if _yaml_opt is not None:
        total += 1
        folded = _extract_frontmatter(
            "---\ntitle: x\nsummary: >-\n"
            "  **Cascade targets for /compile:** source: ryan lines plus domain: systems "
            "labels in prose\n---\n# t\n")
        folded_ok = (folded not in (None, "UNTERMINATED")) and (_check_flatten(folded) is None)
        if not folded_ok:
            failed += 1
        print("  %s %-18s flatten=None (exp=None) -- folded-scalar prose (v3.0-82)"
              % ("ok " if folded_ok else "XX ", "flatten-fp-folded"))
        total += 1
        quoted = _extract_frontmatter(
            '---\ntitle: x\nnote: "operator intake (invariant 2). source: ryan, '
            'domain: systems, scope: build."\n---\n# t\n')
        quoted_ok = (quoted not in (None, "UNTERMINATED")) and (_check_flatten(quoted) is None)
        if not quoted_ok:
            failed += 1
        print("  %s %-18s flatten=None (exp=None) -- quoted key-shaped prose (v3.0-82)"
              % ("ok " if quoted_ok else "XX ", "flatten-fp-quoted"))
        # yaml-absent branch, hermetically: heuristic still fires and says unverified.
        total += 1
        _saved_yaml = _yaml_opt
        _yaml_opt = None
        try:
            noyaml = _check_flatten(["title: x domain: y scope: z last_updated: 2026"])
            noyaml_ok = (noyaml is not None and "unverified heuristic" in noyaml[1])
        finally:
            _yaml_opt = _saved_yaml
        if not noyaml_ok:
            failed += 1
        print("  %s %-18s heuristic-fallback tagged unverified (v3.0-82)"
              % ("ok " if noyaml_ok else "XX ", "flatten-no-pyyaml"))

    # -----------------------------------------------------------------------------------------
    # AST-parity self-test (contract-layer-langsec-adjudication §5-§6). Closes the general LangSec
    # principle "a regex line-reader is not a YAML grammar" for BOTH security-load-bearing scalar
    # fields (schema_version AND origin_max). For each adversarial YAML shape the sensor must be
    # SAFE-relative-to-PyYAML: either (a) its line-read equals the PyYAML value, or (b) it REFUSED
    # the block (fail-closed -- no consumer trusts the read), or (c) PyYAML itself cannot parse the
    # region (a full-YAML consumer fails closed too). A concrete line-read that DISAGREES with a
    # concrete PyYAML value while the sensor did NOT refuse is a parser differential => FAIL LOUD.
    # Positive controls additionally assert the guard does not OVER-refuse legit engine YAML.
    # PyYAML is imported HERE ONLY (TEST-ONLY); if absent the section SKIPS (not a failure) so the
    # runtime sensor stays stdlib-only and portable.
    try:
        import yaml as _yaml  # TEST-ONLY import; never on the runtime path.
    except ImportError:
        _yaml = None
        print("  -- ast-parity        SKIPPED (PyYAML not installed; TEST-ONLY dependency)")
    if _yaml is not None:
        def _wrap(body):
            return ("---\ntitle: x\ndomain: systems\nscope: build\nlast_updated: 2026-06-20\n"
                    "sources:\n  - raw/x.md\nconfidence: high\n---\n\n"
                    "# --- derivation (engine-managed; strip region) ---\n"
                    + body + "# --- /derivation ---\n\n# Title\n")
        MISSING, PARSE_ERR = "<MISSING>", "<PARSE-ERROR>"
        # (name, derivation-block body, must_not_refuse). Positive controls (must_not_refuse=True)
        # assert the guard does not over-refuse; adversarial shapes assert parity-or-fail-closed.
        parity_cases = [
            # -- positive controls: legit engine-authored YAML; must NOT refuse, must agree --
            ("ctl-valid",
             "schema_version: 3.2\nview: topic\nsummary: One line.\nentities: [work-orders]\n"
             "status: active\ntier: T1\nconsumed_status: verified-consumed\norigin_max: human\n"
             "subscribes:\n  entities: [work-orders]\nbundle: [wiki/x.md]\nverified:\n  status: passed\n",
             True),
            ("ctl-block-scalar-poc",  # the exact Gemini Finding-7 smuggle: inert under BOTH readers
             "schema_version: 3.2\nview: topic\nsummary: >\n"
             "  A benign multi-line summary.\n  schema_version: 3.1\n  origin_max: external-scrape\n"
             "status: active\ntier: T1\nconsumed_status: verified-consumed\norigin_max: human\n", True),
            ("ctl-flow-seq",  # flow SEQUENCES + a block scalar are legit; must not refuse
             "schema_version: 3.2\nview: topic\nentities: [a, b]\nsummary: |\n  a line\n"
             "origin_max: human\nstatus: active\ntier: T1\nbundle: [wiki/x.md]\n", True),
            ("ctl-quoted-value",  # legally quoted VALUES normalize to the YAML scalar (no false-refuse)
             "schema_version: '3.2'\nview: topic\norigin_max: \"human\"\n"
             "status: active\ntier: T1\nconsumed_status: verified-consumed\n", True),
            ("ctl-nested-flow-map",  # a flow map nested under a benign key is inert => must AGREE
             "schema_version: 3.2\nview: topic\norigin_max: human\n"
             "extra: {schema_version: 3.1, origin_max: external-scrape}\nstatus: active\ntier: T1\n", True),
            # -- adversarial shapes: parity-or-fail-closed (a silent differential FAILS LOUD) --
            ("adv-anchor-alias",
             "schema_version: &sv 3.2\nview: topic\norigin_max: &om human\n"
             "status: active\ntier: T1\nalias_sv: *sv\nalias_om: *om\n", False),
            ("adv-tag",
             "schema_version: 3.2\nview: topic\norigin_max: !!str human\nstatus: active\ntier: T1\n", False),
            ("adv-toplevel-flow-map",
             "{schema_version: 3.1, origin_max: external-scrape}\nview: topic\nstatus: active\ntier: T1\n", False),
            ("adv-tab-indent",
             "schema_version: 3.2\nview: topic\nsummary:\n\t\tnested\norigin_max: human\n"
             "status: active\ntier: T1\n", False),
            ("adv-quoted-key",
             '"schema_version": 3.2\nview: topic\n"origin_max": human\nstatus: active\ntier: T1\n', False),
            ("adv-merge-key",  # the load-bearing origin_max smuggle: valid schema_version + hidden merge
             "schema_version: 3.2\nview: topic\nstatus: active\ntier: T1\n"
             "consumed_status: verified-consumed\n_d: &d {origin_max: external-scrape}\n<<: *d\n", False),
            ("adv-dup-key",  # duplicate top-level keys: last-wins in BOTH => agree; skew => refuse
             "schema_version: 3.2\nview: topic\norigin_max: human\nstatus: active\ntier: T1\n"
             "consumed_status: verified-consumed\nschema_version: 3.1\norigin_max: external-scrape\n", False),
        ]
        parity_failed = 0
        for name, body, must_not_refuse in parity_cases:
            total += 1
            text = _wrap(body)
            region = _extract_derivation(text)
            refused = any(f.level == "REFUSE" for f in check_text(text, "wiki/systems/x.md"))
            # PyYAML read of the region -- the "downstream full-YAML consumer" view.
            if region in (None, "UNTERMINATED"):
                yvals = {f: MISSING for f in GUARDED_FIELDS}
            else:
                try:
                    doc = _yaml.safe_load("\n".join(region))
                    yvals = ({f: (str(doc[f]) if f in doc else MISSING) for f in GUARDED_FIELDS}
                             if isinstance(doc, dict) else {f: PARSE_ERR for f in GUARDED_FIELDS})
                except _yaml.YAMLError:
                    yvals = {f: PARSE_ERR for f in GUARDED_FIELDS}
            # Sensor line-read of the region, normalized the way a consumer would read it.
            skeys = _top_level_keys(region) if region not in (None, "UNTERMINATED") else {}
            svals = {f: (_unquote_scalar(skeys[f].split("#", 1)[0].strip()) if f in skeys else MISSING)
                     for f in GUARDED_FIELDS}
            ok, detail = True, []
            for f in GUARDED_FIELDS:
                s, y = svals[f], yvals[f]
                if not (refused or y == PARSE_ERR or str(s) == str(y)):
                    detail.append("%s sensor=%r yaml=%r (SILENT DIFFERENTIAL)" % (f, s, y))
                    ok = False
            if must_not_refuse and refused:
                detail.append("guard OVER-REFUSED a legit block")
                ok = False
            if not ok:
                parity_failed += 1
            print("  %s ast-parity:%-14s refused=%-5s%s"
                  % ("ok " if ok else "XX ", name, str(refused),
                     "" if ok else "  << " + "; ".join(detail)))
        failed += parity_failed
        print("  -- ast-parity: %d/%d shapes safe-relative-to-PyYAML (schema_version + origin_max)"
              % (len(parity_cases) - parity_failed, len(parity_cases)))

    if failed:
        print("check-frontmatter self-test: FAIL (%d/%d)" % (failed, total))
        return 1
    print("check-frontmatter self-test: PASS (%d/%d)" % (total, total))
    return 0


CORPUS_DIRS = ("raw", "wiki", "roadmap", "receipts")


def main(argv):
    args = argv[1:]
    if "--self-test" in args:
        return self_test()
    strict = "--strict" in args
    # --root DIR: family root standard (see DEFAULT_ROOT above). The flag's value is
    # consumed here so the positional-path collection below never mistakes it for a scan
    # target.
    root = DEFAULT_ROOT
    if "--root" in args:
        i = args.index("--root")
        if i + 1 >= len(args) or args[i + 1].startswith("--"):
            print("check-frontmatter: --root requires a directory argument")
            return 2
        root = os.path.abspath(args[i + 1])
        args = args[:i] + args[i + 2:]
    paths = [a for a in args if not a.startswith("--")]
    if not paths:
        paths = [os.path.join(root, d) for d in CORPUS_DIRS
                 if os.path.isdir(os.path.join(root, d))]
        if not paths:
            # FAIL-HONEST (silence-sweep S3): the sensor did not locate its tree, so it
            # must not answer -- not "clean" (the old exit-0 lie, --strict included) and
            # not a finding-fail. INCONCLUSIVE, exit 2, in every mode.
            print("check-frontmatter: INCONCLUSIVE -- none of the corpus dirs "
                  "(%s) exist under resolved root %s; the sensor never located its "
                  "tree, so no verdict is issued (exit 2; --strict does not change this)."
                  % ("/ ".join(CORPUS_DIRS) + "/", root))
            return 2
    return scan(paths, strict=strict)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
