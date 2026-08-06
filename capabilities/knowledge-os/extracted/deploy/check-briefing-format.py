#!/usr/bin/env python3
"""check-briefing-format.py -- the sweep-briefing FORMAT-layer validator (knowledge-os, v3).

Replays the VALIDATOR rows of `manifests/sweep-briefing/format-MANIFEST.md` against one
briefing artifact (a rendered `/sweep` briefing -- typically SWEEP-BRIEFING.md, or any text
matching its shape). The contract this validator implements is `.claude/skills/sweep/SKILL.md`
section "The briefing" (and the unclassifiable-output rule immediately under it): three
sections, in order, with exact headings; a one-sentence All-clear; numbered
Needs-your-attention items of two to four sentences (amended 2026-08-05: the old
exactly-three rule forced padding on naturally-two-sentence items; the three ROLES -- what's
wrong / consequence / fix -- are judged by the manifest's RUBRIC row, the count here is a
range) with no inline file paths and a well-formed trailing "(details: ...)" tail; a dashed
Watching list; no raw tool-output leakage, no placeholder tokens, no script filenames in
prose; any unclassifiable-output marker must surface inside Needs-your-attention, never
silently elsewhere; and nothing but whitespace precedes the "**All clear:**" heading -- no
title line, no preamble.

A second mode, --prose-scan PATH (added v3.0.25), applies only the any-layout rows -- raw
tool-output leakage, script filenames in prose, placeholder tokens -- to ANY operator-read
file (SESSION-BRIEFING.md, DECISIONS-PENDING.md), independent of the three-section briefing
shape. /sweep wires it so raw sensor codes in operator files are a mechanical finding, not a
style hope.

Checks map 1:1 to `format-MANIFEST.md` VALIDATOR row IDs (the ROW_CHECKS table below is the
authoritative list); each row's mechanical assertion is implemented as one function. RUBRIC
rows ("plain English / business terms", "All-clear names what was checked", "attention
sentence roles", "Watching tone") are judgment calls graded by an independent-grader session
against the criteria stated in the manifest row itself -- THIS VALIDATOR DOES NOT, AND CANNOT,
CHECK RUBRIC ROWS. It only replays the VALIDATOR rows.

Sensor conventions (matches deploy/check-deadlines.py / deploy/check-frontmatter.py):
degrade-never-block by default -- a completed report always exits 0; findings are data, not
an exit code. `--strict` (what /conformance uses) exits nonzero on any row FAIL. `--file` with
a path that does not exist, or no `--file` at all, is reported as a NOTE and exits 0 -- never
a crash. Stdlib-only; no runtime dependency.

Usage:
  check-briefing-format.py --file PATH [--json] [--strict]
  check-briefing-format.py --prose-scan PATH [--json] [--strict]
  check-briefing-format.py --self-test
Exit codes: 0 = completed report (degrade; findings are data) | 1 = --strict with >=1 row
  FAIL, or a --self-test failure.
"""

import json
import os
import re
import sys
from collections import OrderedDict

_HERE = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------- shared parsing

_HEADING_PATTERNS = OrderedDict([
    ("all_clear", re.compile(r"(?m)^\*\*All clear:\*\*\s*")),
    ("attention", re.compile(r"(?m)^\*\*Needs your attention:\*\*\s*")),
    ("watching", re.compile(r"(?m)^\*\*Watching:\*\*\s*")),
])
_HEADING_LABEL = {
    "all_clear": "All clear",
    "attention": "Needs your attention",
    "watching": "Watching",
}
_CANON_ORDER = ("all_clear", "attention", "watching")

# A sentence boundary: one or more terminal punctuation marks followed by (whitespace then an
# uppercase letter, a digit, a backtick, or an opening paren) or by end-of-string. This survives
# a mid-sentence abbreviation-free period inside a path/filename token (e.g. "SWEEP-BRIEFING.md
# overwriting") because the character right after that period is lowercase, not a boundary.
_SENT_BOUNDARY_RE = re.compile(r"[.!?]+(?=\s+[A-Z0-9`(]|\s*$)")

# A trailing parenthetical at the very end of a string, no nested parens -- the shape of a
# "(details: ...)" tail (or, in a defective artifact, some other wrongly-keyworded tail).
_TAIL_RE = re.compile(r"\s*\(([^()]*)\)\s*$")

# Path-shaped token: a backtick-quoted span, a slash-containing token, or a bare filename with
# one of these extensions. Deliberately excludes ".py" -- script filenames are their own row
# (no-script-names-in-prose) so the two checks stay independent for single-defect isolation.
_PATH_RE = re.compile(
    r"(`[^`]+`)"
    r"|(\b[\w][\w.-]*/[\w./-]+\b)"
    r"|(\b[\w-]+\.(?:md|ya?ml|json|jsonl|log|txt)\b)",
    re.IGNORECASE,
)

_SCRIPT_RE = re.compile(r"\b[\w-]+\.py\b")

_RAW_OUTPUT_RE = re.compile(
    r"\[(?:PASS|FAIL|WARN|SKIP|NOTE|REFUSE)\]"
    r"|Traceback \(most recent call last\)"
    r"|^\$\s",
    re.MULTILINE,
)

_PLACEHOLDER_RE = re.compile(
    r"\bTODO\b|\bTBD\b|\bXXX\b|<\.\.\.>|lorem ipsum", re.IGNORECASE)

_UNCLASSIFIABLE_RE = re.compile(
    r"unclassifiable|could not classify|cannot classify|did not recognize|unrecognized output",
    re.IGNORECASE,
)

# Any trailing-parenthetical "aside" of the shape "(word: ...)" -- generalized past the
# "details:" keyword so a malformed tail (wrong keyword) is still excluded from the
# raw-output/script-name prose scan; those two checks care about PROSE, not about what a tail
# (even a broken one) legitimately carries.
_ANY_TAIL_ASIDE_RE = re.compile(r"\([a-zA-Z][\w-]*:\s*[^()]*\)")

_ATTENTION_ITEM_START_RE = re.compile(r"^\s*(\d+)\.\s+(.*)$")
_WATCHING_DASH_RE = re.compile(r"^\s*-\s+\S")


def count_sentences(text):
    t = text.strip()
    if not t:
        return 0
    return len(_SENT_BOUNDARY_RE.findall(t))


def strip_tail(text):
    """(prose, tail_content_or_None) -- strips one trailing "(...)" span, no nested parens."""
    m = _TAIL_RE.search(text)
    if m:
        return text[:m.start()].rstrip(), m.group(1).strip()
    return text.strip(), None


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def prose_only(text):
    """The document with every trailing-aside parenthetical ("(word: ...)") removed -- the
    view raw-output-leakage / script-name checks scan, so a legitimate tail's technical
    content (a path, a tool token) never counts as prose leakage. HTML comments are also
    stripped (v3.0.28, Ultrapak adoption finding v3.0-local-4 / upstream v3.0-66): a
    machine header like DECISIONS-PENDING.md's "regenerated by deploy/decision-inbox.py"
    comment is plumbing that never renders -- the operator cannot read it, so it is not
    prose. Visible sentences keep the full ban."""
    return _ANY_TAIL_ASIDE_RE.sub("", _HTML_COMMENT_RE.sub("", text))


def find_headings(text):
    return {k: list(p.finditer(text)) for k, p in _HEADING_PATTERNS.items()}


def section_contents(text):
    """{key: content_or_None} for each of the three canonical section keys, using the FIRST
    occurrence of each present heading and the next present heading (by position) as the
    span boundary -- independent of physical document order, so a reordered-sections defect
    still yields correct per-section content."""
    found = find_headings(text)
    first = {k: v[0] for k, v in found.items() if v}
    ordered = sorted(first.items(), key=lambda kv: kv[1].start())
    contents = {}
    for i, (k, m) in enumerate(ordered):
        start = m.end()
        end = ordered[i + 1][1].start() if i + 1 < len(ordered) else len(text)
        contents[k] = text[start:end].strip("\n").strip()
    for k in _CANON_ORDER:
        contents.setdefault(k, None)
    return contents


def attention_items(text):
    """None if the section is absent; [] if present but empty/"(none)"; else a list of
    (declared_number, item_text) in document order. item_text joins wrapped continuation
    lines (e.g. a details tail on its own line) with a single space."""
    contents = section_contents(text)
    att = contents.get("attention")
    if att is None:
        return None
    if not att or att.strip().lower() in ("(none)", "none"):
        return []
    items = []
    current_num = None
    current_lines = []

    def flush():
        if current_num is not None:
            items.append((current_num, " ".join(current_lines).strip()))

    for ln in att.splitlines():
        m = _ATTENTION_ITEM_START_RE.match(ln)
        if m:
            flush()
            current_num = int(m.group(1))
            current_lines = [m.group(2)]
        elif current_num is not None and ln.strip():
            current_lines.append(ln.strip())
    flush()
    return items


def watching_lines(text):
    """None if the section is absent; else the non-blank lines of the section, in order."""
    contents = section_contents(text)
    w = contents.get("watching")
    if w is None:
        return None
    return [ln for ln in w.splitlines() if ln.strip()]


# --------------------------------------------------------------------------- row checks
# Each returns (passed: bool, reason: str). One function per VALIDATOR row in
# manifests/sweep-briefing/format-MANIFEST.md -- the ROW_CHECKS table below is the 1:1 map.

def check_sections_present(text):
    found = find_headings(text)
    missing = [k for k, v in found.items() if not v]
    if missing:
        return False, "missing section heading(s): %s" % ", ".join(
            _HEADING_LABEL[k] for k in _CANON_ORDER if k in missing)
    dupes = [k for k, v in found.items() if len(v) > 1]
    if dupes:
        return False, "section heading(s) appear more than once: %s" % ", ".join(
            _HEADING_LABEL[k] for k in _CANON_ORDER if k in dupes)
    return True, "all three section headings present exactly once"


def check_sections_order(text):
    found = find_headings(text)
    first = {k: v[0].start() for k, v in found.items() if v}
    present_canon = [k for k in _CANON_ORDER if k in first]
    by_position = sorted(first, key=lambda k: first[k])
    if by_position != present_canon:
        return False, ("sections (of those present) are out of order: found %s, "
                        "expected %s" % (
                            [_HEADING_LABEL[k] for k in by_position],
                            [_HEADING_LABEL[k] for k in present_canon]))
    return True, "section headings (of those present) appear in the canonical order"


def check_all_clear_one_sentence(text):
    ac = section_contents(text).get("all_clear")
    if ac is None:
        return True, "All clear section absent (see sections-present)"
    n = count_sentences(ac)
    if n == 1 and ac.rstrip().endswith((".", "!", "?")):
        return True, "All clear section is exactly one sentence"
    return False, "All clear section has %d sentence(s) (expected exactly 1): %r" % (
        n, ac[:100])


def check_attention_numbered(text):
    items = attention_items(text)
    if items is None:
        return True, "Needs your attention section absent (see sections-present)"
    if not items:
        return True, "no attention items to number"
    nums = [n for n, _ in items]
    expected = list(range(1, len(items) + 1))
    if nums == expected:
        return True, "%d attention item(s) numbered sequentially from 1" % len(items)
    return False, "attention item numbering is %s, expected sequential %s" % (nums, expected)


def check_attention_three_sentences(text):
    """Amended 2026-08-05 (manifest amendment A2): the count is a RANGE, 2..4 -- the old
    exactly-3 rule forced padding on naturally-two-sentence items. Whether the sentences
    cover their three required ROLES (what's wrong / consequence of ignoring / fix +
    self-applicability) stays a RUBRIC judgment (row attention-sentence-roles), not a count.
    Row id kept for fixture and smoke-set continuity."""
    items = attention_items(text)
    if items is None:
        return True, "section absent (see sections-present)"
    if not items:
        return True, "no attention items"
    bad = []
    for n, itext in items:
        prose, _tail = strip_tail(itext)
        c = count_sentences(prose)
        if not (2 <= c <= 4):
            bad.append("#%s has %d" % (n, c))
    if bad:
        return False, "item(s) outside the 2-4 sentence range: %s" % "; ".join(bad)
    return True, "%d attention item(s), each 2-4 sentences" % len(items)


def check_attention_no_inline_path(text):
    items = attention_items(text)
    if items is None:
        return True, "section absent (see sections-present)"
    if not items:
        return True, "no attention items"
    bad = []
    for n, itext in items:
        prose, _tail = strip_tail(itext)
        if _PATH_RE.search(prose):
            bad.append("#%s" % n)
    if bad:
        return False, "item(s) contain a file-path-shaped token in their sentence text: %s" % (
            ", ".join(bad))
    return True, "no inline file-path tokens found in attention item sentences"


def check_attention_details_tail_form(text):
    items = attention_items(text)
    if items is None:
        return True, "section absent (see sections-present)"
    if not items:
        return True, "no attention items"
    bad = []
    for n, itext in items:
        _prose, tail = strip_tail(itext)
        if tail is None:
            continue
        if not re.match(r"^details:\s*\S", tail):
            bad.append("#%s (%r)" % (n, tail[:40]))
    if bad:
        return False, ("item(s) have a trailing parenthetical not in the '(details: ...)' "
                        "form: %s" % "; ".join(bad))
    return True, "all trailing parentheticals (where present) use the '(details: ...)' form"


def check_watching_dashed_list(text):
    lines = watching_lines(text)
    if lines is None:
        return True, "Watching section absent (see sections-present)"
    if not lines:
        return True, "no watching items"
    bad = [ln for ln in lines if not _WATCHING_DASH_RE.match(ln)]
    if bad:
        return False, "watching line(s) not in dashed-list form: %r" % bad[0][:60]
    return True, "%d watching item(s), all dash-prefixed" % len(lines)


def check_no_raw_output_leakage(text):
    m = _RAW_OUTPUT_RE.search(prose_only(text))
    if m:
        return False, "raw sensor/tool output signature found: %r" % m.group(0)
    return True, "no raw sensor-output signatures found"


def check_unclassifiable_in_attention(text):
    contents = section_contents(text)
    outside = (contents.get("all_clear") or "") + "\n" + (contents.get("watching") or "")
    m = _UNCLASSIFIABLE_RE.search(outside)
    if m:
        return False, ("an unclassifiable/unrecognized-output marker (%r) appears outside "
                        "the Needs-your-attention section" % m.group(0))
    return True, "no unclassifiable-output marker found outside Needs-your-attention"


def check_no_placeholder_tokens(text):
    m = _PLACEHOLDER_RE.search(text)
    if m:
        return False, "placeholder token found: %r" % m.group(0)
    return True, "no placeholder tokens found"


def check_no_script_names_in_prose(text):
    m = _SCRIPT_RE.search(prose_only(text))
    if m:
        return False, "a script filename appears in prose text: %r" % m.group(0)
    return True, "no script filenames found in prose text"


def check_no_preamble_before_all_clear(text):
    found = find_headings(text)
    ac = found.get("all_clear")
    if not ac:
        return True, "All clear section absent (see sections-present)"
    prefix = text[:ac[0].start()]
    if prefix.strip():
        return False, ("content precedes the '**All clear:**' heading -- the briefing must "
                        "start there, no title line or preamble: %r" % prefix.strip()[:60])
    return True, "nothing but whitespace precedes the '**All clear:**' heading"


# Row-id order matches the manifest's row table order (VALIDATOR rows only).
ROW_CHECKS = OrderedDict([
    ("sections-present", check_sections_present),
    ("sections-order", check_sections_order),
    ("all-clear-one-sentence", check_all_clear_one_sentence),
    ("attention-numbered", check_attention_numbered),
    ("attention-three-sentences", check_attention_three_sentences),
    ("attention-no-inline-path", check_attention_no_inline_path),
    ("attention-details-tail-form", check_attention_details_tail_form),
    ("watching-dashed-list", check_watching_dashed_list),
    ("no-raw-output-leakage", check_no_raw_output_leakage),
    ("unclassifiable-in-attention", check_unclassifiable_in_attention),
    ("no-placeholder-tokens", check_no_placeholder_tokens),
    ("no-script-names-in-prose", check_no_script_names_in_prose),
    ("no-preamble-before-all-clear", check_no_preamble_before_all_clear),
])


# Rows that hold for ANY operator-read file, independent of the three-section briefing
# layout (v3.0.25): raw sensor/tool output, script filenames in prose, and placeholder
# tokens do not belong in any surface a human reads, whatever its shape. /sweep applies
# these to SESSION-BRIEFING.md and DECISIONS-PENDING.md via --prose-scan.
PROSE_SCAN_ROWS = ("no-raw-output-leakage", "no-script-names-in-prose",
                   "no-placeholder-tokens")


def run_rows_on_text(text, row_ids):
    rows = []
    for rid in row_ids:
        fn = ROW_CHECKS[rid]
        try:
            ok, reason = fn(text)
        except Exception as e:  # noqa: BLE001 -- a validator crash is a FAIL, never an exit
            ok, reason = False, "validator crashed on this row: %s" % e
        rows.append({"id": rid, "pass": ok, "reason": reason})
    n_pass = sum(1 for r in rows if r["pass"])
    return rows, n_pass


# --------------------------------------------------------------------------- report assembly

def run_file(path):
    if not os.path.isfile(path):
        return {"file": path, "present": False,
                "note": "no such file -- nothing to check (degrade)"}
    with open(path, "r", encoding="utf-8-sig") as fh:
        text = fh.read()
    rows, n_pass = run_rows_on_text(text, list(ROW_CHECKS))
    return {
        "file": path, "present": True, "rows": rows,
        "counts": {"total": len(rows), "pass": n_pass, "fail": len(rows) - n_pass},
    }


def run_prose_scan(path):
    if not os.path.isfile(path):
        return {"file": path, "present": False, "mode": "prose-scan",
                "note": "no such file -- nothing to check (degrade)"}
    with open(path, "r", encoding="utf-8-sig") as fh:
        text = fh.read()
    rows, n_pass = run_rows_on_text(text, list(PROSE_SCAN_ROWS))
    return {
        "file": path, "present": True, "mode": "prose-scan", "rows": rows,
        "counts": {"total": len(rows), "pass": n_pass, "fail": len(rows) - n_pass},
    }


def print_report(report, as_json=False):
    if as_json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return
    if not report.get("present"):
        print("check-briefing-format: NOTE -- %s: %s" % (
            report.get("file"), report.get("note")))
        return
    print("check-briefing-format: %s" % report["file"])
    for r in report["rows"]:
        print("  [%s] %-30s %s" % ("PASS" if r["pass"] else "FAIL", r["id"], r["reason"]))
    c = report["counts"]
    print("check-briefing-format: %d PASS, %d FAIL (%d row(s) total)"
          % (c["pass"], c["fail"], c["total"]))


# --------------------------------------------------------------------------- self-test

def self_test():
    """Hermetic against the repo's pinned seed at manifests/sweep-briefing/source/: both
    exemplars must pass every row; each defect-<rowid>.md must fail EXACTLY its own row and
    pass every other row (single-defect isolation, proving validator sensitivity row by row).
    If the seed directory is absent (e.g. this file's mirrored copy in a template checkout
    that does not carry manifests/ -- manifests/ is template content carried by the
    orchestrator, not mirrored into deploy/), the self-test degrades to a NOTE and exits 0:
    there is nothing to assert, never a failure."""
    seed_dir = os.path.normpath(
        os.path.join(_HERE, "..", "manifests", "sweep-briefing", "source"))
    if not os.path.isdir(seed_dir):
        print("check-briefing-format --self-test: NOTE -- no pinned seed at %s "
              "(this context carries no manifests/ tree); nothing to assert, "
              "degrading to a no-op pass." % seed_dir)
        return 0

    total = failed = 0

    def case(name, ok, detail=""):
        nonlocal total, failed
        total += 1
        status = "ok " if ok else "XX "
        print("  %s %s%s" % (status, name, ("  << " + detail) if (not ok and detail) else ""))
        if not ok:
            failed += 1

    for fname in ("exemplar-1.md", "exemplar-2.md"):
        path = os.path.join(seed_dir, fname)
        if not os.path.isfile(path):
            case("%s present" % fname, False, "seed file missing")
            continue
        report = run_file(path)
        bad = [r["id"] for r in report["rows"] if not r["pass"]]
        case("%s: all %d row(s) pass" % (fname, report["counts"]["total"]),
             not bad, "failing rows: %s" % bad)

    for rid in ROW_CHECKS:
        fname = "defect-%s.md" % rid
        path = os.path.join(seed_dir, fname)
        if not os.path.isfile(path):
            case("%s present" % fname, False, "seed file missing")
            continue
        report = run_file(path)
        by_id = {r["id"]: r["pass"] for r in report["rows"]}
        own_failed = by_id.get(rid) is False
        others = [k for k, v in by_id.items() if k != rid and not v]
        ok = own_failed and not others
        detail = "" if ok else ("row-under-test pass=%s; other row(s) also failing: %s"
                                 % (by_id.get(rid), others))
        case("%s: fails exactly row '%s', passes the rest" % (fname, rid), ok, detail)

    # Hermetic prose-scan cases (v3.0.25) -- no seed needed; these prove the any-layout
    # rows fire on a non-briefing-shaped operator file and stay quiet on clean prose.
    clean_prose = (
        "## Waiting on you\n\n"
        "- A roadmap decision is open, and if nothing happens by Friday the phase stays "
        "blocked. (details: wiki/REVIEW.md)\n")
    leaky_prose = (
        "## Attention\n\n"
        "- [WARN] check-frontmatter.py reported TABLE-NOSEP at L106 -- rejoin the row.\n")
    rows, n_pass = run_rows_on_text(clean_prose, list(PROSE_SCAN_ROWS))
    case("prose-scan: clean operator prose passes all %d row(s)" % len(rows),
         n_pass == len(rows),
         "failing rows: %s" % [r["id"] for r in rows if not r["pass"]])
    rows, _ = run_rows_on_text(leaky_prose, list(PROSE_SCAN_ROWS))
    by_id = {r["id"]: r["pass"] for r in rows}
    case("prose-scan: raw [WARN] + script filename both caught, placeholders quiet",
         (by_id.get("no-raw-output-leakage") is False
          and by_id.get("no-script-names-in-prose") is False
          and by_id.get("no-placeholder-tokens") is True),
         "row results: %s" % by_id)
    # v3.0.28 (Ultrapak adoption finding): a machine HTML comment naming a script --
    # DECISIONS-PENDING.md's own derived-projection header -- is plumbing, not prose;
    # it never renders, so it must not trip the scan. A script name in VISIBLE prose
    # right next to it must still be caught.
    header_only = ("<!-- DERIVED PROJECTION -- regenerated by deploy/decision-inbox.py; "
                   "never hand-edit. -->\n\nNothing is waiting on you. All clear.\n")
    rows, n_pass = run_rows_on_text(header_only, list(PROSE_SCAN_ROWS))
    case("prose-scan: script name inside an HTML comment does not trip the scan",
         n_pass == len(rows),
         "failing rows: %s" % [r["id"] for r in rows if not r["pass"]])
    mixed = header_only + "\nAlso check-frontmatter.py said something.\n"
    rows, _ = run_rows_on_text(mixed, list(PROSE_SCAN_ROWS))
    by_id = {r["id"]: r["pass"] for r in rows}
    case("prose-scan: script name in VISIBLE prose still caught beside a comment",
         by_id.get("no-script-names-in-prose") is False, "row results: %s" % by_id)

    print("check-briefing-format self-test: %s (%d/%d)"
          % ("PASS" if not failed else "FAIL", total - failed, total))
    return 0 if not failed else 1


# --------------------------------------------------------------------------- CLI

def main(argv):
    args = argv[1:]
    if "--self-test" in args:
        return self_test()

    as_json = "--json" in args
    strict = "--strict" in args
    file_path = None
    prose_path = None
    if "--file" in args:
        i = args.index("--file")
        if i + 1 < len(args):
            file_path = args[i + 1]
    if "--prose-scan" in args:
        i = args.index("--prose-scan")
        if i + 1 < len(args):
            prose_path = args[i + 1]

    if prose_path is not None:
        report = run_prose_scan(prose_path)
    elif file_path is None:
        report = {"file": None, "present": False,
                  "note": "no --file given -- nothing to check (degrade)"}
    else:
        report = run_file(file_path)

    print_report(report, as_json=as_json)
    if strict and report.get("present") and report["counts"]["fail"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
