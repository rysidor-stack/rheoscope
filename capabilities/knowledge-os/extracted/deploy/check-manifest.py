#!/usr/bin/env python3
"""check-manifest.py -- behavioral-manifest sensor (knowledge-os capability, harness).

Checks the `manifests/` tree (core/methodology/manifest-format.md, the parsing spec)
against its own frontmatter schema (Section 3), flags vocabulary (Section 4), the
MANIFEST-INDEX schema (Section 6), the status enum (Section 7), and the Section 12
check-manifest bullet -- the authoritative check list this sensor implements:

  1. frontmatter-complete -- required fields present; status/confidence/row_shape
     enums; surface matches directory.
  2. layer-valid -- `manifest:` key registered in deploy/manifest-layers.yaml
     (the doctrine Section 4 layer registry, as machine config), any status.
  3. row-count -- declared_rows vs the computed row count for the file's row_shape.
  4. id-unique -- no duplicate row IDs within a manifest.
  5. flags-vocab -- every flag token in the controlled vocabulary union the file's
     own schema_extensions.
  6. sha256-pins -- source_artifacts working-tree paths carry a verified sha256 pin;
     git: refs and absent files SKIP (absence and commit-pinning can be by design).
  7. index-coherence -- MANIFEST-INDEX.md entries agree with their manifest files
     (status, row count, layer-key validity, certified_by presence).
  8. amendments-log -- manifest carries the required append-only `## Amendments`
     section (manifest-format.md Section 8; an empty stub is fine from birth).
  9. amendment-linkage -- bidirectional: every row-level amendment marker (a bracketed
     amendment-ID pointer on a flag token, Section 4, e.g. `BUILD-MUST-DIVERGE [A3]`)
     resolves to a real `**A<n>**`-shaped entry in that manifest's `## Amendments` log
     (Section 8's canonical record shape); every such entry's named row(s) exist, and --
     for the single-row `row: <id>` value-change form only, never the bulk `rows: ...`
     graduation form (Section 5: a graduation amendment is "read ... as resolved via
     that amendment, not via the flag cell alone") -- the named row carries the
     amendment reference back. Narrative-form (non-canonical-shape) amendment entries
     are grandfathered per Section 8 and never parsed as findings.
  10. id-unique-global -- row IDs are unique across every manifest in the project's
      `manifests/` tree, not merely within the one file check 4 covers (Section 4: "a
      row ID is a permanent handle" -- a handle that collides across surfaces is not a
      permanent handle to any one thing).

Checks 9 and 10 extend beyond Section 12's doctrine-authoritative eight-item list (same
harness-extension posture as open-markers below). Both were adjudicated from the
2026-07-24 MDD hand-over bundle's check-manifests.mjs sensor (that project's amendment-
linkage and global row-ID-uniqueness checks), re-derived here against this harness's own
Section 8 record shape rather than ported wholesale. Both FAIL unconditionally on any
broken linkage or collision, any manifest status -- the row-count check's precedent
(checks 1-6, 8), not the status-gated open-markers treatment below.

Beyond the ten checks above, two reporting behaviors ride along in check_manifest_file
(not numbered checks -- Section 12's list is the doctrine-authoritative eight; both are
harness reporting extensions per the v2.2 countability clause): open-markers -- counts
literal OPEN markers (manifest-format.md Section 4's `OPEN -- missing: <fact>` marker,
matching either the em-dash or the ASCII '--' spelling of the dash) found anywhere in the
manifest body (ratified decision #5, 2026-07-20). Zero is a silent PASS ("none"). A nonzero
count is informational (PASS) on a DRAFT/EXTRACTED/SUPERSEDED manifest, but a FAIL on a
CERTIFIED/LIVE manifest -- certification must disposition every OPEN before it certifies.

conflict-markers -- the CONFLICT marker's counting/gating twin (manifest-format.md Section
4's `CONFLICT -- <source A> says X, <source B> says Y` marker, same em-dash/ASCII-dash
tolerance; v2.2 Amendment Addendum item 15, 2026-07-24 hand-over incorporation): "countable
exactly like OPEN markers... CERTIFIED with a nonzero count requires each CONFLICT
individually resolved." Same status-gated severity as open-markers (informational on
DRAFT/EXTRACTED/SUPERSEDED, FAIL on CERTIFIED/LIVE) and reported as its own line, never
folded into the OPEN count -- Section 1's orthogonal-classes principle: OPEN is a missing
fact, CONFLICT is a live disagreement between two sources, and a new concern gets its own
line rather than being folded into one that already means something else.

HERMETICITY: a project with no `manifests/` tree is the normal, absent-by-design
state on a fresh instance (manifest-format.md Section 2) -- this sensor prints a
NOTE and exits 0, never a finding, never a failure.

Stdlib-only (no PyYAML): manifest frontmatter, the MANIFEST-INDEX YAML block, and
manifest-layers.yaml all use a small, regular YAML subset (block mappings, inline
`[a, b]` lists, and block lists of scalars/mappings) -- the same shallow-parse
convention deploy/assemble.py's _parse_shallow_yaml already established for this
engine's own descriptor/derivation reading. No new runtime dependency is introduced.

Usage:
  check-manifest.py [--root PATH]   check PATH's manifests/ tree (default: cwd)
  check-manifest.py --self-test     run embedded hermetic fixtures; exit 0 if all pass

Exit codes: 0 = clean, or degrade (WARN/SKIP/NOTE only) | 1 = at least one FAIL,
or a self-test failure.
"""

import hashlib
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Controlled vocabularies (manifest-format.md Sections 3, 4, 7).
# ---------------------------------------------------------------------------

REQUIRED_FM_FIELDS = [
    "manifest", "surface", "version", "status", "source_artifacts",
    "extracted", "declared_rows", "confidence", "row_shape",
]
# List-shaped fields: presence of the key is what's required: an empty declared
# list ([] / no rows) is a legitimate state, not a missing field.
LIST_FM_FIELDS = {"source_artifacts", "schema_extensions", "viewports", "variant_axes"}

STATUS_ENUM = {"DRAFT", "EXTRACTED", "CERTIFIED", "LIVE", "SUPERSEDED"}
CONFIDENCE_ENUM = {"source-crosschecked", "interaction-only", "probe-derived"}
ROW_SHAPE_ENUM = {"table", "sections", "hybrid"}

# The doctrine's 8 controlled flags plus SUPERSEDED-BY (used as a flag per Section 8)
# and the accepted "no flags" placeholder tokens ("-", em dash, "none"). An empty
# flags cell is handled structurally (zero tokens -> nothing to validate), not as a
# literal vocabulary member.
CONTROLLED_FLAGS = frozenset({
    "UNREACHABLE", "UNREACHABLE-BY-DESIGN", "SURPRISE", "NOOP",
    "BUILD-MUST-DIVERGE", "TIME-SENSITIVE", "REAL-MODE-ONLY", "DRAFT",
    "SUPERSEDED-BY", "-", "\u2014", "none",
})


# ---------------------------------------------------------------------------
# Minimal stdlib-only YAML-subset reader (block mappings, inline [a, b] lists,
# block lists of scalars or single-level mappings, one level of nesting per
# indent step). Sufficient for manifest frontmatter, the MANIFEST-INDEX YAML
# block, and manifest-layers.yaml's top-level keys -- the shapes this engine's
# manifests use. Adapted from deploy/assemble.py's _parse_shallow_yaml pattern.
# ---------------------------------------------------------------------------

def _strip_comment(s):
    out = []
    in_q = None
    for ch in s:
        if in_q:
            out.append(ch)
            if ch == in_q:
                in_q = None
            continue
        if ch in ("'", '"'):
            in_q = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out)


def _indent_of(s):
    return len(s) - len(s.lstrip(" "))


def _split_top_commas(s):
    out = []
    cur = []
    in_q = None
    for ch in s:
        if in_q:
            cur.append(ch)
            if ch == in_q:
                in_q = None
            continue
        if ch in ("'", '"'):
            in_q = ch
            cur.append(ch)
            continue
        if ch == "," and not in_q:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def _split_inline_list(s):
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [x.strip().strip("'\"") for x in _split_top_commas(inner) if x.strip()]
    return None


def _scalar_of(val):
    """A bare scalar value: an inline list if val is `[...]`, else the value with
    one matching pair of surrounding quotes stripped, else None for an empty val."""
    if not val:
        return None
    inline = _split_inline_list(val)
    if inline is not None:
        return inline
    return val.strip("'\"")


def parse_yaml_block(text):
    """Parse a restricted YAML subset (block mappings, inline lists, block lists of
    scalars or one-level mappings) into a nested dict. Returns {} for empty text."""
    lines = [ln.rstrip("\n") for ln in text.splitlines()]
    n = len(lines)

    def parse_block(idx, base_indent):
        result = {}
        i = idx
        while i < n:
            s_full = _strip_comment(lines[i])
            if not s_full.strip():
                i += 1
                continue
            ind = _indent_of(s_full)
            if ind < base_indent:
                break
            if ind > base_indent:
                i += 1
                continue
            content = s_full.strip()
            if content.startswith("- ") or content == "-":
                break  # caller (a list parser) owns this line
            if ":" not in content:
                i += 1
                continue
            key, _, val = content.partition(":")
            key = key.strip().strip("'\"")
            val = val.strip()
            i += 1
            if val == "":
                peek = i
                while peek < n:
                    cand = _strip_comment(lines[peek])
                    if cand.strip():
                        break
                    peek += 1
                if peek < n:
                    nxt = _strip_comment(lines[peek])
                    if nxt.strip().startswith("- ") and _indent_of(nxt) >= base_indent:
                        items, i = parse_list(peek, _indent_of(nxt))
                        result[key] = items
                        continue
                    if nxt.strip() and _indent_of(nxt) > base_indent:
                        sub, i = parse_block(peek, _indent_of(nxt))
                        result[key] = sub
                        continue
                result[key] = None
            else:
                result[key] = _scalar_of(val)
        return result, i

    def parse_list(idx, item_indent):
        items = []
        i = idx
        while i < n:
            raw = _strip_comment(lines[i])
            if not raw.strip():
                i += 1
                continue
            ind = _indent_of(raw)
            if ind < item_indent or not raw.strip().startswith("- "):
                break
            after = raw.strip()[2:]
            if ":" in after:
                key, _, val = after.partition(":")
                entry = {key.strip(): _scalar_of(val.strip())}
                j = i + 1
                sub_indent = ind + 2
                while j < n:
                    raw2 = _strip_comment(lines[j])
                    if not raw2.strip():
                        j += 1
                        continue
                    ind2 = _indent_of(raw2)
                    if ind2 < sub_indent or raw2.strip().startswith("- "):
                        break
                    c2 = raw2.strip()
                    if ":" in c2:
                        k2, _, v2 = c2.partition(":")
                        entry[k2.strip()] = _scalar_of(v2.strip())
                    j += 1
                items.append(entry)
                i = j
            else:
                items.append(after.strip().strip("'\""))
                i += 1
        return items, i

    top, _ = parse_block(0, 0)
    return top


def _is_yaml_null(v):
    return v is None or (isinstance(v, str) and v.strip().lower() in ("null", "~", ""))


# ---------------------------------------------------------------------------
# File-shape extraction: frontmatter, the MANIFEST-INDEX fenced YAML block, and
# the manifest-layers.yaml top-level key set.
# ---------------------------------------------------------------------------

def extract_frontmatter(text):
    """(frontmatter_lines, body_start_index) or (None, 0) if absent/unterminated."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, 0
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], i + 1
    return None, 0


_FENCE_RE = re.compile(r"^\s*```")


def extract_index_yaml_block(text):
    """The lines inside the first fenced code block in a MANIFEST-INDEX.md file
    (Section 6: "one MANIFEST-INDEX.md per surface, holding a single YAML block").
    Returns None if no fenced block is found."""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if _FENCE_RE.match(ln):
            if start is None:
                start = i
            else:
                return lines[start + 1:i]
    return None


_TOP_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):")


def load_layer_registry(path):
    """The set of registered layer keys from manifest-layers.yaml (any status).
    `dimensions` is excluded -- it is a list, not a layer key (Section 4.4)."""
    keys = set()
    with open(path, "r", encoding="utf-8-sig") as fh:
        for line in fh:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if line[:1] in (" ", "\t"):
                continue
            m = _TOP_KEY_RE.match(line)
            if m and m.group(1) != "dimensions":
                keys.add(m.group(1))
    return keys


# ---------------------------------------------------------------------------
# Row scanning: table shape (canonical `| id | ... |` rows) and sections/hybrid
# shape (`### <id>` headings) -- manifest-format.md Section 4 / this file's task
# spec Section 3 counting rules.
# ---------------------------------------------------------------------------

_PIPE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_SEP_ROW_RE = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_HEADING_RE = re.compile(r"^###\s+(.*)$")
_SECTION_SLUG_RE = re.compile(
    r"^`([a-z0-9]+(?:-[a-z0-9]+)*)`|^([a-z0-9]+(?:-[a-z0-9]+)*)(?:\s|$)")
_BOLD_FLAGS_RE = re.compile(r"\*\*flags:?\*\*\s*(.*)$", re.IGNORECASE)
_BRACKET_TOKEN_RE = re.compile(r"^\[.*\]$")
_TOKEN_SPLIT_RE = re.compile(r"[;\s]+")
_AMENDMENTS_HEADING_RE = re.compile(r"^##\s+Amendments(\s|$)")
# manifest-format.md Section 4 OPEN marker: literal text OPEN, one space, an em dash
# (\u2014) or ASCII '--', one space, then 'missing:' (ASCII-authored manifests may
# spell the dash as '--'). v2.2 decision 5 (2026-07-20).
_OPEN_MARKER_RE = re.compile(r"OPEN\s+(?:\u2014|--)\s+missing:")
# manifest-format.md Section 4 CONFLICT marker: literal text CONFLICT, one space, an em
# dash (\u2014) or ASCII '--', then the two-sources-disagree shape '<source A> says X,
# <source B> says Y' -- the word 'says' is the stable anchor of that shape (there is no
# single fixed keyword the way OPEN has 'missing:', since the contested value itself
# varies). Same em-dash/ASCII-dash tolerance as OPEN. v2.2 Amendment Addendum item 15
# (2026-07-24 hand-over incorporation). Matched per-line (no re.DOTALL) same as OPEN --
# a marker lives inside one table cell, never spanning a newline join across rows.
_CONFLICT_MARKER_RE = re.compile(r"CONFLICT\s+(?:\u2014|--)\s+.*\bsays\b", re.IGNORECASE)

# --- CHECK 9 (amendment-linkage) support -----------------------------------------------
# manifest-format.md Section 8's canonical record shape is a bold-prefixed, pipe-delimited
# bullet -- '**A<n>** | date <YYYY-MM-DD> | row(s): ... | prior: ... | new: ... |
# provenance: ...' -- not a markdown table row (contrast the MDD bundle's check-
# manifests.mjs, which reads a `| amendment | date | row ids | ... |` table; this harness's
# own \u00a78 shape is prose bullets under '## Amendments', frequently soft-wrapped across
# several source lines). A bullet is only a linkage-checkable entry if it opens with
# '**A<n>**' immediately after its '- ' marker; anything else is a narrative-form entry
# (\u00a78: "grandfathered", never rewritten, never a finding here).
_BULLET_START_RE = re.compile(r"^-\s+(.*)$")
_TOP_HEADING_RE = re.compile(r"^#{1,6}\s")
_AMEND_CANON_RE = re.compile(r"^\*\*(A\d+)\*\*\s*\|(.*)$", re.DOTALL)
_KEBAB_BACKTICK_RE = re.compile(r"`([a-z0-9]+(?:-[a-z0-9]+)*)`")
# A row-level amendment marker (\u00a74: "any flag may carry a bracketed pointer to the
# amendment ... e.g. BUILD-MUST-DIVERGE [A3]"). Amendment IDs are 'A<digits>' (\u00a78); a
# row-pointer bracket like SUPERSEDED-BY [some-row-id] doesn't match this shape and is
# correctly left alone -- it points at a row, not an amendment.
_BRACKET_AMEND_ID_RE = re.compile(r"\[(A\d+)\]")


def scan_amendment_bullets(body_lines):
    """Top-level bullet items under '## Amendments', each joined into one whitespace-
    normalized string (\u00a78 entries are commonly soft-wrapped prose, not single lines).
    Returns [(joined_text, first_line_no)], first_line_no 1-based into body_lines."""
    start = None
    for i, ln in enumerate(body_lines):
        if _AMENDMENTS_HEADING_RE.match(ln.rstrip()):
            start = i + 1
            break
    if start is None:
        return []
    bullets = []
    cur = None
    cur_line = None
    for i in range(start, len(body_lines)):
        stripped = body_lines[i].rstrip()
        if _TOP_HEADING_RE.match(stripped.strip()):
            break  # next top-level section ends the Amendments list
        m = _BULLET_START_RE.match(stripped)
        if m:
            if cur is not None:
                bullets.append((" ".join(cur), cur_line))
            cur = [m.group(1).strip()]
            cur_line = i + 1
        elif not stripped.strip():
            continue  # blank line: a loose-list gap, not a bullet boundary
        elif stripped[:1] in (" ", "\t") and cur is not None:
            cur.append(stripped.strip())
        # else: unindented non-bullet prose -- ignore rather than misattribute
    if cur is not None:
        bullets.append((" ".join(cur), cur_line))
    return bullets


def parse_amendment_entries(bullets):
    """Canonical-shape (manifest-format.md section 8) bullets only -- '**A<n>** | date
    ... | row(s): ... | ...'. Narrative-form bullets never match and are silently
    skipped (section 8: grandfathered, not rewritten). Returns [{"id", "row_ids", "form",
    "line"}]; "form" is "single" (row: <id>, a value-change amendment -- the back-
    reference is checked) or "bulk" (rows: ..., a graduation/batch amendment -- section 5
    reads it as "resolved ... not via the flag cell alone", so only row existence, not the
    back-reference, is checked)."""
    entries = []
    for text, line_no in bullets:
        m = _AMEND_CANON_RE.match(text)
        if not m:
            continue
        amend_id = m.group(1)
        fields = [f.strip() for f in m.group(2).split("|")]
        row_field, form = None, None
        for f in fields:
            fl = f.lower()
            if fl.startswith("row:"):
                row_field, form = f[len("row:"):].strip(), "single"
                break
            if fl.startswith("rows:"):
                row_field, form = f[len("rows:"):].strip(), "bulk"
                break
        row_ids = []
        if form == "single" and row_field:
            bare = row_field.strip("`").strip()
            if _SLUG_RE.match(bare):
                row_ids = [bare]
        elif form == "bulk" and row_field:
            row_ids = _KEBAB_BACKTICK_RE.findall(row_field)
        entries.append({"id": amend_id, "row_ids": row_ids, "form": form, "line": line_no})
    return entries


def extract_amendment_markers(flags_text):
    """Bracket-pointer amendment IDs found in a row's flags cell (see
    _BRACKET_AMEND_ID_RE above)."""
    if not flags_text:
        return []
    return _BRACKET_AMEND_ID_RE.findall(flags_text)


def _cells_of(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_slug_cell(cell):
    c = cell.strip()
    if c.startswith("`") and c.endswith("`") and len(c) > 2:
        c = c[1:-1]
    return c if _SLUG_RE.match(c) else None


def scan_table_rows(body_lines):
    """Rows from every table whose header's first column is `id`: [(row_id,
    flags_text_or_None), ...]. flags_text is None if the table has no `flags`
    header column at all."""
    rows = []
    no_flags_column = False
    i, n = 0, len(body_lines)
    while i < n:
        line = body_lines[i]
        if _PIPE_ROW_RE.match(line.strip()):
            cells = _cells_of(line)
            if cells and cells[0].strip().lower() == "id":
                j = i + 1
                if j < n and _SEP_ROW_RE.match(body_lines[j].strip()):
                    flags_idx = None
                    for idx, c in enumerate(cells):
                        if c.strip().lower() == "flags":
                            flags_idx = idx
                            break
                    if flags_idx is None:
                        no_flags_column = True
                    k = j + 1
                    while (k < n and _PIPE_ROW_RE.match(body_lines[k].strip())
                           and not _SEP_ROW_RE.match(body_lines[k].strip())):
                        rcells = _cells_of(body_lines[k])
                        slug = _is_slug_cell(rcells[0]) if rcells else None
                        if slug:
                            flags_text = (rcells[flags_idx]
                                          if flags_idx is not None and flags_idx < len(rcells)
                                          else None)
                            rows.append((slug, flags_text))
                        k += 1
                    i = k
                    continue
        i += 1
    return rows, no_flags_column


def _find_flags_in_section(lines):
    for line in lines:
        m = _BOLD_FLAGS_RE.search(line)
        if m:
            return m.group(1).strip()
        if _PIPE_ROW_RE.match(line.strip()):
            cells = _cells_of(line)
            if len(cells) >= 2 and cells[0].strip().lower() == "flags":
                return cells[1].strip()
    return None


def scan_section_rows(body_lines):
    """Rows from `### <id>` headings (sections and hybrid row_shape read alike,
    per this sensor's task spec): [(row_id, flags_text_or_None), ...]."""
    heading_idxs = []
    for i, line in enumerate(body_lines):
        m = _HEADING_RE.match(line.rstrip())
        if m:
            heading_idxs.append((i, m.group(1).strip()))
    rows = []
    for pos, (i, text) in enumerate(heading_idxs):
        sm = _SECTION_SLUG_RE.match(text)
        slug = (sm.group(1) or sm.group(2)) if sm else None
        if not slug:
            continue
        end = heading_idxs[pos + 1][0] if pos + 1 < len(heading_idxs) else len(body_lines)
        flags_text = _find_flags_in_section(body_lines[i + 1:end])
        rows.append((slug, flags_text))
    return rows


def tokenize_flags(text):
    """Flag tokens from a flags cell/marker, bracketed pointers stripped. Returns
    None if text is None (marker not found at all -- distinct from an empty cell,
    which yields [])."""
    if text is None:
        return None
    toks = []
    for raw in _TOKEN_SPLIT_RE.split(text.strip()):
        t = raw.strip()
        if not t or _BRACKET_TOKEN_RE.match(t):
            continue
        toks.append(t)
    return toks


# ---------------------------------------------------------------------------
# Finding / output.
# ---------------------------------------------------------------------------

class Finding:
    __slots__ = ("level", "check", "path", "msg")

    def __init__(self, level, check, path, msg):
        self.level = level  # PASS | WARN | FAIL | SKIP | NOTE
        self.check = check
        self.path = path
        self.msg = msg

    def __str__(self):
        return "  [%s] %-16s %s: %s" % (self.level, self.check, self.path, self.msg)


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _to_os_path(root, rel):
    """A repo-relative path (always '/'-separated per convention) resolved onto
    root using os.path.join -- Windows-safe, never a hardcoded separator."""
    rel = rel.replace("\\", "/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    return os.path.join(root, *parts) if parts else root


# ---------------------------------------------------------------------------
# Per-manifest-file checks (1 frontmatter-complete, 2 layer-valid, 3 row-count,
# 4 id-unique, 5 flags-vocab, 6 sha256-pins).
# ---------------------------------------------------------------------------

def check_manifest_file(fpath, surface, layer_keys, root, out, global_row_index=None):
    """Runs checks 1-6, 8, 9 against one <layer>-MANIFEST.md file, and (if
    global_row_index is given) contributes this file's row IDs toward check 10's
    cross-surface uniqueness pass. Returns a summary dict {status, declared_rows} for
    the index-coherence check, or None if the file could not be parsed at all (no
    frontmatter)."""
    with open(fpath, "r", encoding="utf-8-sig") as fh:
        text = fh.read()

    fm_lines, body_start = extract_frontmatter(text)
    if fm_lines is None:
        out.append(Finding("FAIL", "frontmatter-complete", fpath,
                            "no frontmatter block found (missing or unterminated ---)"))
        return None
    fm = parse_yaml_block("\n".join(fm_lines))

    # --- CHECK 1: frontmatter-complete ---
    missing = []
    for f in REQUIRED_FM_FIELDS:
        if f not in fm:
            missing.append(f)
            continue
        v = fm[f]
        if f in LIST_FM_FIELDS:
            continue  # presence is enough; an empty declared list is legitimate
        if v is None or (isinstance(v, str) and not v.strip()):
            missing.append(f)
    for f in missing:
        out.append(Finding("FAIL", "frontmatter-complete", fpath,
                            "missing required field: %s" % f))

    status = (fm.get("status") or "").strip()
    if status and status not in STATUS_ENUM:
        out.append(Finding("FAIL", "frontmatter-complete", fpath,
                            "status '%s' not in %s" % (status, sorted(STATUS_ENUM))))
    confidence = (fm.get("confidence") or "").strip()
    if confidence and confidence not in CONFIDENCE_ENUM:
        out.append(Finding("FAIL", "frontmatter-complete", fpath,
                            "confidence '%s' not in %s" % (confidence, sorted(CONFIDENCE_ENUM))))
    row_shape = (fm.get("row_shape") or "").strip()
    if row_shape and row_shape not in ROW_SHAPE_ENUM:
        out.append(Finding("FAIL", "frontmatter-complete", fpath,
                            "row_shape '%s' not in %s" % (row_shape, sorted(ROW_SHAPE_ENUM))))
    surface_fm = (fm.get("surface") or "").strip()
    if surface_fm and surface_fm != surface:
        out.append(Finding("FAIL", "frontmatter-complete", fpath,
                            "surface '%s' does not match directory '%s'" % (surface_fm, surface)))
    if (not missing and status in STATUS_ENUM and confidence in CONFIDENCE_ENUM
            and row_shape in ROW_SHAPE_ENUM and surface_fm == surface):
        out.append(Finding("PASS", "frontmatter-complete", fpath, "ok"))

    # --- CHECK 2: layer-valid ---
    layer = (fm.get("manifest") or "").strip()
    if layer:
        if layer_keys is None:
            pass  # registry itself unreadable -- already warned once, globally
        elif layer not in layer_keys:
            out.append(Finding("FAIL", "layer-valid", fpath,
                                "unknown layer '%s' (not in manifest-layers.yaml)" % layer))
        else:
            out.append(Finding("PASS", "layer-valid", fpath, "layer '%s' registered" % layer))

    # --- CHECK 3 + 4: row-count, id-unique ---
    body_lines = text.splitlines()[body_start:]
    no_flags_column = False
    if row_shape == "table":
        rows, no_flags_column = scan_table_rows(body_lines)
    elif row_shape in ("sections", "hybrid"):
        rows = scan_section_rows(body_lines)
    else:
        rows = []
    computed = len(rows)

    declared_raw = (fm.get("declared_rows") or "").strip() if fm.get("declared_rows") is not None else ""
    declared = None
    if declared_raw:
        try:
            declared = int(declared_raw)
        except ValueError:
            out.append(Finding("FAIL", "row-count", fpath,
                                "declared_rows '%s' is not an integer" % declared_raw))
    if declared is not None:
        if declared != computed:
            out.append(Finding("FAIL", "row-count", fpath,
                                "declared_rows=%d != computed=%d" % (declared, computed)))
        else:
            out.append(Finding("PASS", "row-count", fpath,
                                "declared_rows matches computed (%d)" % computed))

    ids = [r[0] for r in rows]
    seen = {}
    for rid in ids:
        seen[rid] = seen.get(rid, 0) + 1
    dupes = sorted(k for k, v in seen.items() if v > 1)
    if dupes:
        out.append(Finding("FAIL", "id-unique", fpath,
                            "duplicate row id(s): %s" % ", ".join(dupes)))
    elif ids:
        out.append(Finding("PASS", "id-unique", fpath, "%d row id(s), all unique" % len(ids)))

    if global_row_index is not None:
        for rid in set(ids):  # once per file even if this file itself has a within-file dupe
            global_row_index.setdefault(rid, []).append(fpath)

    # --- CHECK 5: flags-vocab ---
    schema_ext = fm.get("schema_extensions")
    if not isinstance(schema_ext, list):
        schema_ext = [schema_ext] if schema_ext else []
    allowed = CONTROLLED_FLAGS | set(schema_ext)
    bad_tokens = {}
    missing_marker_rows = []
    for rid, flags_text in rows:
        toks = tokenize_flags(flags_text)
        if toks is None:
            if row_shape in ("sections", "hybrid"):
                missing_marker_rows.append(rid)
            continue
        for t in toks:
            if t not in allowed:
                bad_tokens.setdefault(rid, set()).add(t)
    if bad_tokens:
        for rid, toks in sorted(bad_tokens.items()):
            out.append(Finding("FAIL", "flags-vocab", fpath,
                                "row '%s' has unknown flag token(s): %s"
                                % (rid, ", ".join(sorted(toks)))))
    elif rows:
        out.append(Finding("PASS", "flags-vocab", fpath, "all flag tokens valid"))
    if missing_marker_rows:
        out.append(Finding("WARN", "flags-vocab", fpath,
                            "no flags marker found (best-effort scan) for row(s): %s"
                            % ", ".join(missing_marker_rows)))
    if row_shape == "table" and no_flags_column and rows:
        out.append(Finding("WARN", "flags-vocab", fpath,
                            "no 'flags' header column found in a canonical table"))

    # --- CHECK 6: sha256-pins ---
    source_artifacts = fm.get("source_artifacts")
    if isinstance(source_artifacts, dict):
        source_artifacts = [source_artifacts]
    for entry in (source_artifacts or []):
        if not isinstance(entry, dict):
            continue
        ref = entry.get("ref")
        ref = ref.strip() if isinstance(ref, str) else ""
        sha = entry.get("sha256")
        sha = sha.strip() if isinstance(sha, str) else ""
        spath = entry.get("path")
        spath = spath.strip() if isinstance(spath, str) else ""
        if ref.startswith("git:"):
            out.append(Finding("SKIP", "sha256-pins", fpath,
                                "ref '%s' is a git commit pin; sha256 not required" % ref))
            continue
        if not spath:
            out.append(Finding("WARN", "sha256-pins", fpath,
                                "source_artifacts entry has neither a usable path nor a git: ref"))
            continue
        if not sha:
            out.append(Finding("FAIL", "sha256-pins", fpath,
                                "source_artifacts '%s' has no sha256 pin" % spath))
            continue
        full = _to_os_path(root, spath)
        if not os.path.isfile(full):
            out.append(Finding("SKIP", "sha256-pins", fpath,
                                "pinned file absent (may be by design): %s" % spath))
            continue
        actual = _sha256_file(full)
        if actual.lower() != sha.lower():
            out.append(Finding("FAIL", "sha256-pins", fpath,
                                "sha256 mismatch for %s: declared=%s actual=%s"
                                % (spath, sha, actual)))
        else:
            out.append(Finding("PASS", "sha256-pins", fpath, "sha256 verified for %s" % spath))

    # --- CHECK 8: amendments-log ---
    has_amendments = any(_AMENDMENTS_HEADING_RE.match(ln.rstrip()) for ln in body_lines)
    if has_amendments:
        out.append(Finding("PASS", "amendments-log", fpath, "'## Amendments' section present"))
    else:
        out.append(Finding("FAIL", "amendments-log", fpath,
                            "manifest lacks the required append-only '## Amendments' section "
                            "(manifest-format.md section 8; an empty stub is fine)"))

    # --- CHECK 9: amendment-linkage (bidirectional) ---
    amend_entries = parse_amendment_entries(scan_amendment_bullets(body_lines))
    amend_ids_seen = {e["id"] for e in amend_entries}
    row_ids_set = set(ids)

    row_markers = []  # [(row_id, amend_id), ...] -- every bracket-pointer marker found
    for rid, flags_text in rows:
        for amend_id in extract_amendment_markers(flags_text):
            row_markers.append((rid, amend_id))
    marker_pairs = set(row_markers)

    linkage_problems = 0
    for rid, amend_id in row_markers:
        if amend_id not in amend_ids_seen:
            linkage_problems += 1
            out.append(Finding("FAIL", "amendment-linkage", fpath,
                                "row '%s' amendment marker [%s] does not resolve to any "
                                "entry in the '## Amendments' log" % (rid, amend_id)))

    for entry in amend_entries:
        amend_id = entry["id"]
        for rid in entry["row_ids"]:
            if rid not in row_ids_set:
                linkage_problems += 1
                out.append(Finding("FAIL", "amendment-linkage", fpath,
                                    "amendment %s names row '%s', but no such row exists "
                                    "in this manifest" % (amend_id, rid)))
                continue
            if entry["form"] == "single" and (rid, amend_id) not in marker_pairs:
                linkage_problems += 1
                out.append(Finding("FAIL", "amendment-linkage", fpath,
                                    "amendment %s names row '%s', but the row's flags cell "
                                    "does not carry the amendment reference back ([%s])"
                                    % (amend_id, rid, amend_id)))

    if linkage_problems == 0 and (row_markers or amend_entries):
        out.append(Finding("PASS", "amendment-linkage", fpath,
                            "%d row marker(s), %d amendment entr%s, all linked"
                            % (len(row_markers), len(amend_entries),
                               "y" if len(amend_entries) == 1 else "ies")))

    # --- open-markers (reporting behavior, not a numbered check -- v2.2 decision 5) ---
    # manifest-format.md Section 4's OPEN marker is countable: certification must
    # disposition every OPEN before a manifest goes CERTIFIED/LIVE. DRAFT/EXTRACTED/
    # SUPERSEDED manifests may legitimately still carry OPENs -- informational there.
    open_count = len(_OPEN_MARKER_RE.findall("\n".join(body_lines)))
    if open_count == 0:
        out.append(Finding("PASS", "open-markers", fpath, "none"))
    elif status in ("CERTIFIED", "LIVE"):
        out.append(Finding("FAIL", "open-markers", fpath,
                            "%d OPEN marker(s) on a %s manifest -- certification must "
                            "disposition every OPEN (manifest-format.md section 4, "
                            "v2.2 decision 5)" % (open_count, status)))
    else:
        out.append(Finding("PASS", "open-markers", fpath, "%d OPEN marker(s)" % open_count))

    # --- conflict-markers (reporting behavior, not a numbered check -- v2.2 Amendment
    # Addendum item 15, 2026-07-24 hand-over incorporation) ---
    # manifest-format.md Section 4's CONFLICT marker is countable exactly like OPEN:
    # certification must resolve every CONFLICT via an operator ruling recorded in the
    # amendment log before a manifest goes CERTIFIED/LIVE. DRAFT/EXTRACTED/SUPERSEDED
    # manifests may legitimately still carry CONFLICTs -- informational there. Reported
    # as its own line, never folded into open_count above (Section 1's orthogonal-classes
    # principle -- OPEN is a missing fact, CONFLICT is a live disagreement).
    conflict_count = len(_CONFLICT_MARKER_RE.findall("\n".join(body_lines)))
    if conflict_count == 0:
        out.append(Finding("PASS", "conflict-markers", fpath, "none"))
    elif status in ("CERTIFIED", "LIVE"):
        out.append(Finding("FAIL", "conflict-markers", fpath,
                            "%d CONFLICT marker(s) on a %s manifest -- certification must "
                            "resolve every CONFLICT via an operator ruling recorded in the "
                            "amendment log (manifest-format.md section 4, v2.2 Amendment "
                            "Addendum item 15)" % (conflict_count, status)))
    else:
        out.append(Finding("PASS", "conflict-markers", fpath,
                            "%d CONFLICT marker(s)" % conflict_count))

    return {"status": status, "declared_rows": declared, "layer": layer}


# ---------------------------------------------------------------------------
# CHECK 7: index-coherence.
# ---------------------------------------------------------------------------

def iter_manifest_files(surface_dir):
    for name in sorted(os.listdir(surface_dir)):
        full = os.path.join(surface_dir, name)
        if name.endswith("-MANIFEST.md") and os.path.isfile(full):
            yield name, full


def check_index_coherence(surface, surface_dir, root, manifest_summaries, layer_keys, out):
    index_path = os.path.join(surface_dir, "MANIFEST-INDEX.md")
    if not os.path.isfile(index_path):
        out.append(Finding("WARN", "index-coherence", surface_dir,
                            "no MANIFEST-INDEX.md found for surface"))
        return
    with open(index_path, "r", encoding="utf-8-sig") as fh:
        text = fh.read()
    block_lines = extract_index_yaml_block(text)
    if block_lines is None:
        out.append(Finding("FAIL", "index-coherence", index_path,
                            "no fenced YAML block found in MANIFEST-INDEX.md"))
        return
    idx = parse_yaml_block("\n".join(block_lines))
    layers = idx.get("layers")
    if not isinstance(layers, dict):
        layers = {}

    check_start = len(out)  # marks where this INDEX's findings begin, for the PASS gate below
    checked_count = 0

    listed_files = set()
    for lkey, entry in layers.items():
        if not isinstance(entry, dict):
            continue
        if layer_keys is not None and lkey not in layer_keys:
            out.append(Finding("FAIL", "index-coherence", index_path,
                                "INDEX layer key '%s' not in manifest-layers.yaml" % lkey))
        fpath = entry.get("file")
        if _is_yaml_null(fpath):
            continue  # e.g. status: MISSING, no file to reconcile against
        full_fpath = _to_os_path(root, fpath)
        listed_files.add(os.path.normpath(full_fpath))
        if not os.path.isfile(full_fpath):
            out.append(Finding("FAIL", "index-coherence", index_path,
                                "layer '%s' file does not exist: %s" % (lkey, fpath)))
            continue
        summary = manifest_summaries.get(os.path.normpath(full_fpath))
        if summary is None:
            out.append(Finding("WARN", "index-coherence", index_path,
                                "layer '%s' file %s could not be parsed for coherence"
                                % (lkey, fpath)))
            continue
        checked_count += 1

        # MANIFEST-INDEX layer-key vs manifest-file layer cross-check
        # (cross-vendor review finding): the INDEX may file a manifest under
        # layer key 'interaction' while the referenced file's own
        # frontmatter `manifest:` field says 'logic' -- neither this
        # sensor's status/rows comparisons below nor assemble.py's gate
        # (which trusts the INDEX statuses) previously caught that on their
        # own. An empty file_layer is a missing-field case already reported
        # by CHECK 1 (frontmatter-complete); skip re-reporting it here.
        file_layer = summary.get("layer") or ""
        if file_layer and file_layer != lkey:
            out.append(Finding("FAIL", "index-coherence", index_path,
                                "layer key '%s' references %s whose manifest: is '%s'"
                                % (lkey, fpath, file_layer)))

        idx_status = entry.get("status")
        idx_status = idx_status.strip() if isinstance(idx_status, str) else ""
        if summary["status"] != idx_status:
            out.append(Finding("FAIL", "index-coherence", index_path,
                                "layer '%s' status mismatch: file=%s index=%s"
                                % (lkey, summary["status"], idx_status)))
        idx_rows_raw = entry.get("rows")
        idx_rows = None
        if not _is_yaml_null(idx_rows_raw):
            try:
                idx_rows = int(str(idx_rows_raw).strip())
            except ValueError:
                out.append(Finding("FAIL", "index-coherence", index_path,
                                    "layer '%s' INDEX rows '%s' is not an integer"
                                    % (lkey, idx_rows_raw)))
        if idx_rows is not None and summary["declared_rows"] != idx_rows:
            out.append(Finding("FAIL", "index-coherence", index_path,
                                "layer '%s' rows mismatch: file declared_rows=%s index rows=%s"
                                % (lkey, summary["declared_rows"], idx_rows)))
        certified_by = entry.get("certified_by")
        if idx_status in ("CERTIFIED", "LIVE") and _is_yaml_null(certified_by):
            out.append(Finding("FAIL", "index-coherence", index_path,
                                "layer '%s' status=%s but certified_by is missing"
                                % (lkey, idx_status)))

    for _name, fpath in iter_manifest_files(surface_dir):
        if os.path.normpath(fpath) not in listed_files:
            out.append(Finding("WARN", "index-coherence", fpath,
                                "manifest file present on disk but not listed in MANIFEST-INDEX.md "
                                "(conservative: the gate reads the INDEX, so an unlisted layer reads "
                                "as MISSING and refuses -- never bypasses)"))

    # Success-path confirmation: a clean run otherwise leaves this check silent (WARN/FAIL
    # only), giving no visible sign it ran at all. One PASS per checked INDEX when this
    # check raised zero WARN/FAIL findings against it.
    if not any(f.level in ("WARN", "FAIL") for f in out[check_start:]):
        noun = "entry" if checked_count == 1 else "entries"
        out.append(Finding("PASS", "index-coherence", index_path,
                            "%d layer %s coherent with their manifest files"
                            % (checked_count, noun)))


# ---------------------------------------------------------------------------
# CHECK 10: id-unique-global (cross-surface row-ID uniqueness).
# ---------------------------------------------------------------------------

def check_cross_surface_uniqueness(global_row_index, manifests_root, out):
    """A row ID is "a permanent handle" (manifest-format.md section 4) -- checked here
    across every manifest in the manifests/ tree, not merely within the one file check 4
    (id-unique) covers. Reports every colliding location, not just the first."""
    collisions = {rid: sorted(set(paths))
                  for rid, paths in global_row_index.items() if len(set(paths)) > 1}
    if collisions:
        for rid in sorted(collisions):
            out.append(Finding("FAIL", "id-unique-global", manifests_root,
                                "row id '%s' is not unique across the manifests/ tree -- "
                                "found in: %s" % (rid, "; ".join(collisions[rid]))))
    elif global_row_index:
        out.append(Finding("PASS", "id-unique-global", manifests_root,
                            "%d distinct row id(s) across the manifests/ tree, all unique"
                            % len(global_row_index)))


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------

def iter_surfaces(manifests_root):
    for name in sorted(os.listdir(manifests_root)):
        full = os.path.join(manifests_root, name)
        if os.path.isdir(full):
            yield name, full


def run_checks(root, layers_path=None):
    manifests_root = os.path.join(root, "manifests")
    if not os.path.isdir(manifests_root):
        print("NOTE: manifests/ absent -- nothing to check (absent-by-design on a fresh instance)")
        return 0

    if layers_path is None:
        layers_path = os.path.join(root, "deploy", "manifest-layers.yaml")

    findings = []
    layer_keys = None
    if os.path.isfile(layers_path):
        layer_keys = load_layer_registry(layers_path)
    else:
        findings.append(Finding("WARN", "layer-valid", layers_path,
                                 "manifest-layers.yaml not found; layer-valid check skipped"))

    global_row_index = {}
    for surface, surface_dir in iter_surfaces(manifests_root):
        manifest_summaries = {}
        for _name, fpath in iter_manifest_files(surface_dir):
            summary = check_manifest_file(fpath, surface, layer_keys, root, findings,
                                           global_row_index)
            if summary is not None:
                manifest_summaries[os.path.normpath(fpath)] = summary
        check_index_coherence(surface, surface_dir, root, manifest_summaries, layer_keys, findings)

    check_cross_surface_uniqueness(global_row_index, manifests_root, findings)

    for f in findings:
        print(str(f))
    n_pass = sum(1 for f in findings if f.level == "PASS")
    n_warn = sum(1 for f in findings if f.level == "WARN")
    n_skip = sum(1 for f in findings if f.level == "SKIP")
    n_fail = sum(1 for f in findings if f.level == "FAIL")
    print("check-manifest: %d PASS, %d WARN, %d SKIP, %d FAIL (%d finding(s) total)"
          % (n_pass, n_warn, n_skip, n_fail, len(findings)))
    return 1 if n_fail else 0


# ---------------------------------------------------------------------------
# Self-test (hermetic): every fixture -- manifests/ tree AND manifest-layers.yaml --
# is built under tempfile.mkdtemp(). Never reads the live deploy/manifest-layers.yaml
# or any live manifests/ tree.
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
        d = tempfile.mkdtemp(prefix="check-manifest-selftest-")
        tmp_dirs.append(d)
        return d

    def write(path, content):
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)

    def mk_layers(root, keys):
        path = os.path.join(root, "deploy", "manifest-layers.yaml")
        lines = ["# fixture manifest-layers.yaml (self-test only -- never the live file)"]
        for k in keys:
            lines.append("%s: {status: ACTIVE, replay: test replay, named_risk: test risk}" % k)
        lines.append("dimensions: [accessibility, viewport, copy]")
        write(path, "\n".join(lines) + "\n")
        return path

    def run(root, layers_path):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run_checks(root, layers_path=layers_path)
        return code, buf.getvalue()

    def table_manifest(declared_rows, row1_flags="-", row2_flags="none", extra_frontmatter="",
                        manifest_key="interaction", row1_id="row-one", row2_id="row-two",
                        source_block="", include_amendments=True):
        text = """---
manifest: %s
surface: acme
version: "1.0"
status: EXTRACTED
source_artifacts:%s
extracted: 2026-07-01
confidence: source-crosschecked
row_shape: table
declared_rows: %d
%s---

# Interaction manifest

| id | name | replay path | expected observable | variant | flags | evidence | kind |
|---|---|---|---|---|---|---|---|
| `%s` | Row one | Navigate to /a | Text is A | role=guest | %s | a11y-tree | EXACT |
| `%s` | Row two | Navigate to /b | Text is B | role=guest | %s | a11y-tree | EXACT |
""" % (manifest_key, source_block, declared_rows, extra_frontmatter,
       row1_id, row1_flags, row2_id, row2_flags)
        if include_amendments:
            text += "\n## Amendments\n"
        return text

    def open_marker_manifest(status, row1_observable="Text is A", row2_observable="Text is B",
                              declared_rows=2):
        """Table-shaped fixture like table_manifest, but with a settable `status` and a
        settable 'expected observable' cell per row -- the field a row plants an OPEN
        marker in (manifest-format.md section 4), either dash spelling."""
        return """---
manifest: interaction
surface: acme
version: "1.0"
status: %s
source_artifacts: []
extracted: 2026-07-01
confidence: source-crosschecked
row_shape: table
declared_rows: %d
---

# Interaction manifest

| id | name | replay path | expected observable | variant | flags | evidence | kind |
|---|---|---|---|---|---|---|---|
| `row-one` | Row one | Navigate to /a | %s | role=guest | - | a11y-tree | EXACT |
| `row-two` | Row two | Navigate to /b | %s | role=guest | - | a11y-tree | EXACT |

## Amendments
""" % (status, declared_rows, row1_observable, row2_observable)

    def index_text(status="EXTRACTED", rows=2, certified_by="null",
                    file_="manifests/acme/interaction-MANIFEST.md"):
        return """# MANIFEST-INDEX

```yaml
surface: acme
updated: 2026-07-01
layers:
  interaction:
    file: %s
    status: %s
    rows: %d
    certified_by: %s
gate:
  next_increment: "test increment"
  tier: T3
  touched_layers: [interaction]
  state: OPEN
  date: 2026-07-01
```
""" % (file_, status, rows, certified_by)

    try:
        # --- (1) GREEN surface: everything passes ---------------------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        src_path = os.path.join(root, "manifests", "acme", "source", "acme.html")
        write(src_path, "<html>fixture content for the green case</html>")
        sha = _sha256_file(src_path)
        source_block = ("\n  - path: manifests/acme/source/acme.html\n    sha256: %s" % sha)
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              table_manifest(2, source_block=source_block))
        write(os.path.join(root, "manifests", "acme", "MANIFEST-INDEX.md"), index_text())
        code, out = run(root, layers)
        case("(1) green surface: exit 0", code == 0, out)
        case("(1) green surface: no FAIL findings", "[FAIL]" not in out, out)
        case("(1) green surface: matching INDEX layer key/file manifest: field stays green",
             "references" not in out, out)
        case("(1) green surface: index-coherence PASS line present",
             "[PASS] index-coherence" in out
             and "1 layer entry coherent with their manifest files" in out, out)

        # --- (2) declared_rows mismatch FAILs --------------------------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              table_manifest(3))  # actual table has 2 rows
        code, out = run(root, layers)
        case("(2) declared_rows mismatch: exit 1", code == 1)
        case("(2) declared_rows mismatch: FAIL names both numbers",
             "declared_rows=3" in out and "computed=2" in out, out)

        # --- (3) duplicate ID FAILs -------------------------------------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              table_manifest(2, row2_id="row-one"))  # same id twice, count still 2
        code, out = run(root, layers)
        case("(3) duplicate id: exit 1", code == 1)
        case("(3) duplicate id: FAIL names the id", "duplicate row id(s): row-one" in out, out)

        # --- (4) unknown flag token FAILs -------------------------------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              table_manifest(2, row1_flags="BOGUS-FLAG"))
        code, out = run(root, layers)
        case("(4) unknown flag: exit 1", code == 1)
        case("(4) unknown flag: FAIL names the token", "BOGUS-FLAG" in out, out)

        # --- (5) schema_extensions-declared flag passes ------------------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              table_manifest(2, row1_flags="CUSTOM-FLAG",
                              extra_frontmatter="schema_extensions: [CUSTOM-FLAG]\n"))
        code, out = run(root, layers)
        case("(5) schema_extensions-declared flag: exit 0", code == 0, out)
        case("(5) schema_extensions-declared flag: no flags-vocab FAIL",
             "flags-vocab" not in out or "FAIL" not in out.split("flags-vocab")[1].split("\n")[0]
             if "flags-vocab" in out else True, out)

        # --- (6) unknown layer FAILs --------------------------------------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])  # registry does NOT have 'bogus-layer'
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              table_manifest(2, manifest_key="bogus-layer"))
        code, out = run(root, layers)
        case("(6) unknown layer: exit 1", code == 1)
        case("(6) unknown layer: FAIL names it", "unknown layer 'bogus-layer'" in out, out)

        # --- (7) sections row_shape counts ### rows correctly -------------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        sections_text = """---
manifest: interaction
surface: acme
version: "1.0"
status: EXTRACTED
source_artifacts: []
extracted: 2026-07-01
confidence: interaction-only
row_shape: sections
declared_rows: 2
---

# Interaction manifest (sections)

### `row-one` Empty state
**Flags:** -

Body text.

### `row-two` Error state
**Flags:** none

Body text.

## Amendments
"""
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"), sections_text)
        code, out = run(root, layers)
        case("(7) sections row_shape: exit 0", code == 0, out)
        case("(7) sections row_shape: row-count PASS present",
             "row-count" in out and "declared_rows matches computed (2)" in out, out)

        # --- (8) sha256 mismatch FAILs -------------------------------------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        src_path = os.path.join(root, "manifests", "acme", "source", "acme.html")
        write(src_path, "<html>some real content</html>")
        wrong_sha = "0" * 64
        source_block = ("\n  - path: manifests/acme/source/acme.html\n    sha256: %s" % wrong_sha)
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              table_manifest(2, source_block=source_block))
        code, out = run(root, layers)
        case("(8) sha256 mismatch: exit 1", code == 1)
        case("(8) sha256 mismatch: FAIL names the paths", "sha256 mismatch" in out, out)

        # --- (9) sha256 absent-file SKIPs, does not FAIL --------------------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        source_block = ("\n  - path: manifests/acme/source/does-not-exist.html\n    sha256: %s"
                         % ("a" * 64))
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              table_manifest(2, source_block=source_block))
        code, out = run(root, layers)
        case("(9) sha256 absent file: exit 0 (SKIP, not FAIL)", code == 0, out)
        case("(9) sha256 absent file: SKIP with note", "pinned file absent" in out, out)

        # --- (10) INDEX status mismatch FAILs -------------------------------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              table_manifest(2))  # file status: EXTRACTED
        write(os.path.join(root, "manifests", "acme", "MANIFEST-INDEX.md"),
              index_text(status="CERTIFIED", certified_by="receipts/r1.md"))  # INDEX disagrees
        code, out = run(root, layers)
        case("(10) INDEX status mismatch: exit 1", code == 1)
        case("(10) INDEX status mismatch: FAIL names file vs index",
             "status mismatch: file=EXTRACTED index=CERTIFIED" in out, out)

        # --- (11) CERTIFIED without certified_by FAILs -----------------------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              table_manifest(2, extra_frontmatter="status: CERTIFIED\n").replace(
                  "status: EXTRACTED\n", "", 1))
        write(os.path.join(root, "manifests", "acme", "MANIFEST-INDEX.md"),
              index_text(status="CERTIFIED", certified_by="null"))
        code, out = run(root, layers)
        case("(11) CERTIFIED without certified_by: exit 1", code == 1)
        case("(11) CERTIFIED without certified_by: FAIL names it",
             "certified_by is missing" in out, out)

        # --- (12) absent manifests/ tree -> NOTE + exit 0 --------------------------------
        root = mkroot()
        code, out = run(root, os.path.join(root, "deploy", "manifest-layers.yaml"))
        case("(12) absent manifests/: exit 0", code == 0)
        case("(12) absent manifests/: NOTE printed", "NOTE: manifests/ absent" in out, out)

        # --- (13) git: ref without sha256 passes ------------------------------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        source_block = ('\n  - path: exports/legacy.json\n    repo: legacy-export\n'
                         '    ref: "git:4f8a9c2d:exports/legacy.json"')
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              table_manifest(2, source_block=source_block))
        code, out = run(root, layers)
        case("(13) git: ref without sha256: exit 0", code == 0, out)
        case("(13) git: ref without sha256: SKIP (commit pin substitutes)",
             "git commit pin" in out, out)

        # --- (14) working-tree path without sha256 FAILs -----------------------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        source_block = "\n  - path: manifests/acme/source/acme.html"  # no sha256, no ref
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              table_manifest(2, source_block=source_block))
        code, out = run(root, layers)
        case("(14) working-tree path without sha256: exit 1", code == 1)
        case("(14) working-tree path without sha256: FAIL names it",
             "has no sha256 pin" in out, out)

        # --- (15) SUPERSEDED-BY with bracketed pointer tokenizes clean -----------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              table_manifest(2, row1_flags="SUPERSEDED-BY [row-two-v2]"))
        code, out = run(root, layers)
        case("(15) SUPERSEDED-BY with bracketed pointer: exit 0", code == 0, out)
        case("(15) SUPERSEDED-BY with bracketed pointer: no flags-vocab FAIL",
             "unknown flag token" not in out, out)

        # --- (16) INDEX layer key vs file's manifest: field mismatch FAILs, naming both ------
        root = mkroot()
        layers = mk_layers(root, ["interaction", "logic"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              table_manifest(2, manifest_key="logic"))  # file's own manifest: field is 'logic'
        write(os.path.join(root, "manifests", "acme", "MANIFEST-INDEX.md"),
              index_text())  # INDEX still files it under layer key 'interaction'
        code, out = run(root, layers)
        case("(16) INDEX layer key vs file manifest mismatch: exit 1", code == 1)
        case("(16) FAIL names both the INDEX layer key and the file's manifest: value",
             "layer key 'interaction'" in out and "manifest: is 'logic'" in out, out)

        # --- (17) manifest without ## Amendments FAILs -----------------------------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              table_manifest(2, include_amendments=False))
        code, out = run(root, layers)
        case("(17) manifest without ## Amendments: exit 1", code == 1)
        case("(17) manifest without ## Amendments: FAIL names the section",
             "amendments-log" in out
             and "required append-only '## Amendments' section" in out, out)

        # --- (18) manifest with a stub ## Amendments PASSes ------------------------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              table_manifest(2))  # include_amendments=True by default: heading, no entries
        code, out = run(root, layers)
        case("(18) manifest with stub ## Amendments: exit 0", code == 0, out)
        case("(18) manifest with stub ## Amendments: amendments-log PASS present",
             "[PASS] amendments-log" in out and "section present" in out, out)

        # --- (19) EXTRACTED manifest with 2 OPEN markers: informational, exit 0 -----------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              open_marker_manifest("EXTRACTED",
                                    row1_observable="OPEN \u2014 missing: fact one",
                                    row2_observable="OPEN -- missing: fact two"))
        code, out = run(root, layers)
        case("(19) EXTRACTED with 2 OPEN markers (em dash + ASCII '--'): exit 0", code == 0, out)
        case("(19) EXTRACTED with 2 OPEN markers: informational PASS line names 2",
             any("[PASS] open-markers" in ln and "2 OPEN marker(s)" in ln
                 for ln in out.splitlines()), out)

        # --- (20) CERTIFIED manifest with 1 OPEN marker: FAIL, exit 1 ---------------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              open_marker_manifest("CERTIFIED",
                                    row1_observable="OPEN \u2014 missing: fact one",
                                    row2_observable="Text is B"))
        code, out = run(root, layers)
        case("(20) CERTIFIED with 1 OPEN marker: exit 1", code == 1)
        case("(20) CERTIFIED with 1 OPEN marker: FAIL names the count and status",
             any("[FAIL] open-markers" in ln and "1 OPEN marker(s) on a CERTIFIED manifest" in ln
                 for ln in out.splitlines()), out)

        # --- (21) CERTIFIED manifest with zero OPEN markers stays green -------------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              open_marker_manifest("CERTIFIED"))  # default observables carry no OPEN marker
        code, out = run(root, layers)
        case("(21) CERTIFIED with zero OPEN markers: exit 0", code == 0, out)
        case("(21) CERTIFIED with zero OPEN markers: open-markers PASS 'none'",
             any("[PASS] open-markers" in ln and ln.strip().endswith(": none")
                 for ln in out.splitlines()), out)

        # --- amendment-linkage (CHECK 9) fixture helper ----------------------------------------
        def amendment_manifest(amendments_block, row1_flags="-", row2_flags="-",
                                declared_rows=2, row1_id="row-one", row2_id="row-two"):
            """A CERTIFIED (deliberately -- exercises check 9's unconditional-FAIL severity,
            same posture as row-count) table manifest with a caller-supplied '## Amendments'
            body."""
            return """---
manifest: interaction
surface: acme
version: "1.0"
status: CERTIFIED
source_artifacts: []
extracted: 2026-07-01
confidence: source-crosschecked
row_shape: table
declared_rows: %d
---

# Interaction manifest

| id | name | replay path | expected observable | variant | flags | evidence | kind |
|---|---|---|---|---|---|---|---|
| `%s` | Row one | Navigate to /a | Text is A | role=guest | %s | a11y-tree | EXACT |
| `%s` | Row two | Navigate to /b | Text is B | role=guest | %s | a11y-tree | EXACT |

## Amendments

%s
""" % (declared_rows, row1_id, row1_flags, row2_id, row2_flags, amendments_block)

        # --- (22) amendment-linkage: single-row form, clean bidirectional link -> exit 0 --------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              amendment_manifest(
                  "- **A1** | date 2026-07-01 | row: row-one | prior: old value | "
                  "new: new value | provenance: decisions/x.md",
                  row1_flags="BUILD-MUST-DIVERGE [A1]"))
        code, out = run(root, layers)
        case("(22) amendment-linkage clean single-row link: exit 0", code == 0, out)
        case("(22) amendment-linkage clean single-row link: PASS present",
             "[PASS] amendment-linkage" in out, out)

        # --- (23) amendment-linkage: dangling row marker -> FAIL, exit 1 ------------------------
        # row-one points at amendment A9, which does not exist; row-two's own marker to the
        # real A1 entry is correctly linked, isolating the dangling-marker defect alone.
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              amendment_manifest(
                  "- **A1** | date 2026-07-01 | row: row-two | prior: x | new: y | "
                  "provenance: z",
                  row1_flags="[A9]", row2_flags="[A1]"))
        code, out = run(root, layers)
        case("(23) amendment-linkage dangling row marker: exit 1", code == 1)
        case("(23) amendment-linkage dangling row marker: FAIL names it",
             "row 'row-one' amendment marker [A9] does not resolve" in out, out)

        # --- (24) amendment-linkage: amendment names a nonexistent row -> FAIL, exit 1 ----------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              amendment_manifest(
                  "- **A1** | date 2026-07-01 | row: ghost-row | prior: x | new: y | "
                  "provenance: z"))
        code, out = run(root, layers)
        case("(24) amendment-linkage names nonexistent row: exit 1", code == 1)
        case("(24) amendment-linkage names nonexistent row: FAIL names it",
             "amendment A1 names row 'ghost-row', but no such row exists" in out, out)

        # --- (25) amendment-linkage: named row lacks the back-reference -> FAIL, exit 1 ---------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              amendment_manifest(
                  "- **A1** | date 2026-07-01 | row: row-two | prior: x | new: y | "
                  "provenance: z"))  # row-two's flags stay '-' -- no [A1] back-reference
        code, out = run(root, layers)
        case("(25) amendment-linkage missing back-reference: exit 1", code == 1)
        case("(25) amendment-linkage missing back-reference: FAIL names it",
             "amendment A1 names row 'row-two', but the row's flags cell does not carry "
             "the amendment reference back ([A1])" in out, out)

        # --- (26) amendment-linkage: bulk 'rows:' graduation form needs no back-reference -------
        # (section 5: graduation is resolved via the amendment log, "not via the flag cell
        # alone" -- regression guard against over-strict porting of the .mjs reference shape)
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              amendment_manifest(
                  "- **A1** | date 2026-07-01 | rows: 2 new (`row-one`, VALIDATOR; "
                  "`row-two`, RUBRIC) | prior: n/a | new: n/a | provenance: z"))
        code, out = run(root, layers)
        case("(26) amendment-linkage bulk form needs no back-reference: exit 0", code == 0, out)
        case("(26) amendment-linkage bulk form: no amendment-linkage FAIL",
             "[FAIL] amendment-linkage" not in out, out)
        case("(26) amendment-linkage bulk form: PASS present",
             "[PASS] amendment-linkage" in out, out)

        # --- (27) amendment-linkage: narrative-form bullet is not a finding ---------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              amendment_manifest(
                  "- 2026-07-01: a narrative note about a change, no canonical shape here."))
        code, out = run(root, layers)
        case("(27) amendment-linkage narrative-form bullet: exit 0", code == 0, out)
        case("(27) amendment-linkage narrative-form bullet: no amendment-linkage finding",
             "amendment-linkage" not in out, out)

        # --- (28) id-unique-global: distinct row ids across two surfaces -> exit 0, PASS --------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              table_manifest(2))
        write(os.path.join(root, "manifests", "other", "interaction-MANIFEST.md"),
              table_manifest(2, row1_id="other-row-one", row2_id="other-row-two")
              .replace("surface: acme", "surface: other"))
        code, out = run(root, layers)
        case("(28) id-unique-global distinct across surfaces: exit 0", code == 0, out)
        case("(28) id-unique-global distinct across surfaces: PASS present",
             "[PASS] id-unique-global" in out, out)

        # --- (29) id-unique-global: colliding row id across two surfaces -> FAIL, exit 1 --------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              table_manifest(2))
        write(os.path.join(root, "manifests", "other", "interaction-MANIFEST.md"),
              table_manifest(2, row1_id="row-one", row2_id="other-row-two")
              .replace("surface: acme", "surface: other"))
        code, out = run(root, layers)
        case("(29) id-unique-global collision: exit 1", code == 1)
        case("(29) id-unique-global collision: FAIL names the id and both files",
             "row id 'row-one' is not unique across the manifests/ tree" in out
             and os.path.join("manifests", "acme") in out
             and os.path.join("manifests", "other") in out, out)

        # --- (30) EXTRACTED manifest with 2 CONFLICT markers: informational, exit 0 --------------
        # mirrors case (19) for OPEN -- same em-dash/ASCII-dash tolerance, both counted.
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              open_marker_manifest(
                  "EXTRACTED",
                  row1_observable="CONFLICT — pricing-spec.md says highest-value promo "
                                   "applies, legacy-pricing-engine.js says promos stack",
                  row2_observable="CONFLICT -- doc-a.md says X, doc-b.md says Y"))
        code, out = run(root, layers)
        case("(30) EXTRACTED with 2 CONFLICT markers (em dash + ASCII '--'): exit 0",
             code == 0, out)
        case("(30) EXTRACTED with 2 CONFLICT markers: informational PASS line names 2",
             any("[PASS] conflict-markers" in ln and "2 CONFLICT marker(s)" in ln
                 for ln in out.splitlines()), out)

        # --- (31) CERTIFIED manifest with 1 CONFLICT marker: FAIL, exit 1 ------------------------
        # mirrors case (20) for OPEN -- CERTIFIED with a nonzero CONFLICT count must FAIL,
        # same certification-blocking severity manifest-format.md section 4 promises.
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              open_marker_manifest(
                  "CERTIFIED",
                  row1_observable="CONFLICT — pricing-spec.md says X, legacy-engine.js "
                                   "says Y",
                  row2_observable="Text is B"))
        code, out = run(root, layers)
        case("(31) CERTIFIED with 1 CONFLICT marker: exit 1", code == 1)
        case("(31) CERTIFIED with 1 CONFLICT marker: FAIL names the count and status",
             any("[FAIL] conflict-markers" in ln
                 and "1 CONFLICT marker(s) on a CERTIFIED manifest" in ln
                 for ln in out.splitlines()), out)

        # --- (32) CERTIFIED manifest with zero CONFLICT markers stays green (clean manifest
        # passes) -- mirrors case (21) for OPEN, and confirms open-markers/conflict-markers
        # are independent lines (a manifest can be clean on one, dirty on neither, without
        # cross-contamination) ---------------------------------------------------------------
        root = mkroot()
        layers = mk_layers(root, ["interaction"])
        write(os.path.join(root, "manifests", "acme", "interaction-MANIFEST.md"),
              open_marker_manifest("CERTIFIED"))  # default observables carry no marker at all
        code, out = run(root, layers)
        case("(32) CERTIFIED with zero CONFLICT markers: exit 0", code == 0, out)
        case("(32) CERTIFIED with zero CONFLICT markers: conflict-markers PASS 'none'",
             any("[PASS] conflict-markers" in ln and ln.strip().endswith(": none")
                 for ln in out.splitlines()), out)
        case("(32) CERTIFIED with zero CONFLICT markers: open-markers PASS 'none' too "
             "(no cross-contamination between the two marker counts)",
             any("[PASS] open-markers" in ln and ln.strip().endswith(": none")
                 for ln in out.splitlines()), out)

        # --- Parser unit checks (in-memory, no disk) -----------------------------------------
        case("tokenize_flags strips bracketed pointer",
             tokenize_flags("BUILD-MUST-DIVERGE [A2]") == ["BUILD-MUST-DIVERGE"])
        case("tokenize_flags handles semicolon stacking",
             tokenize_flags("UNREACHABLE; TIME-SENSITIVE") == ["UNREACHABLE", "TIME-SENSITIVE"])
        case("tokenize_flags of empty cell is []", tokenize_flags("") == [])
        case("tokenize_flags of missing marker is None", tokenize_flags(None) is None)
        parsed = parse_yaml_block(
            "source_artifacts:\n  - path: a.html\n    sha256: abc\n  - path: b.html\n"
            "    repo: ext\n    ref: \"git:deadbeef:b.html\"\nschema_extensions: [FOO, BAR]\n")
        case("parse_yaml_block: block list of mappings",
             parsed["source_artifacts"] == [
                 {"path": "a.html", "sha256": "abc"},
                 {"path": "b.html", "repo": "ext", "ref": "git:deadbeef:b.html"}])
        case("parse_yaml_block: inline list", parsed["schema_extensions"] == ["FOO", "BAR"])
        nested = parse_yaml_block(
            "layers:\n  interaction:\n    status: CERTIFIED\n    rows: 3\n  data:\n"
            "    status: MISSING\n")
        case("parse_yaml_block: nested mapping-of-mappings",
             nested["layers"]["interaction"]["status"] == "CERTIFIED"
             and nested["layers"]["interaction"]["rows"] == "3"
             and nested["layers"]["data"]["status"] == "MISSING")
        registry_probe = mkroot()
        lp = mk_layers(registry_probe, ["interaction", "logic", "data-protection"])
        keys = load_layer_registry(lp)
        case("load_layer_registry: reads keys, excludes dimensions",
             keys == {"interaction", "logic", "data-protection"})

    finally:
        for d in tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)

    if failed:
        print("check-manifest self-test: FAIL (%d/%d)" % (total - failed, total))
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
    return run_checks(root)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
