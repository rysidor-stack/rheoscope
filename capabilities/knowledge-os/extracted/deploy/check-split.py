#!/usr/bin/env python3
"""check-split.py -- CONTENT-2 split-citation integrity sensor (memory-engine v3, P0).

Run as the acceptance check after a view F is split into parts P1..Pn. Validates
(test-plan CONTENT-2):

  1. STUB. A stub exists at F's original path with `status: superseded` in its
     frontmatter AND a machine-readable redirect map.
  2. ANCHORS. Every anchor in the anchor set A(F-before) is conserved across the parts.
     A(F) = slugified H2/H3 headings UNION explicit ID spans (`{#id}`, `<a id>`)
     [content2-amendment A1]. Conservation is by multiset: a UNIQUE anchor (all citeable
     clause-IDs, every explicit ID) must land in exactly one part (a second copy trips
     'fabricated' -> the frozen 'two = ambiguous citation'); dropping it trips 'dropped'
     -> 'zero = lost knowledge'. If A(F-before) is EMPTY the gate exits 2 INCONCLUSIVE,
     never PASS (A1 fail-closed; EX-3).
  3. CITATIONS. For every file in raw/ and the handoff envelope whose text cites F by
     basename with a line number, the stub's line->part map covers it OR a REVIEW entry
     names it. The envelope is resolved by CONTENT the way sibling check-loop-state.py
     does (fork layout handoffs/, or the template's documented core/handoffs/ -- the old
     hardwired handoffs/ scan covered zero handoff files on template-layout instances);
     records in BOTH envelopes is itself a violation, never a silent one-envelope scan. The
     citation grammar is conjunction/range-aware [content2-amendment A2]: 'lines 1622 and
     661' is TWO citations (both must resolve); 'lines 100-120' is one range (covered iff
     the whole range maps). (raw/ is immutable, so the stub is where a citation is healed.)

content2-amendment (2026-07-01, dated sibling; frozen tp:236-244 untouched): A1 anchor
grammar (EX-3), A2 citation grammar (EX-4), A3 fixtures. A4.2 VOIDS any pre-amendment
verdict on the two real P0 splits -> re-run under this grammar.

F-BEFORE is read from git (`git show <ref>:<path>`, default HEAD) -- the pre-split
committed version -- or from --before-file FILE for the self-test.

The stub's redirect map is a fenced ```redirect-map block of YAML:

    ```redirect-map
    parts:
      - wiki/systems/schema-foundations-keys.md
      - wiki/systems/schema-foundations-money.md
    anchors:
      decision-1-primary-key-strategy-locked-2026-05-02: wiki/systems/schema-foundations-keys.md
    lines:
      "661": wiki/systems/schema-foundations-softdelete.md
      "1622": wiki/systems/schema-foundations-money.md
    review: []          # line numbers intentionally healed via REVIEW instead of the map
    ```

Usage:
  check-split.py STUB_PATH [--before REF] [--root DIR]   validate a real split
  check-split.py --self-test                              embedded 3-file-split battery

When --root is absent the root is the parent of the deploy/ dir this script lives
in (the family standard, matching check-loop-state.py) -- never the caller's CWD,
which pointed the citation scan at whatever directory the operator happened to be
in.

Exit codes: 0 = split integral | 1 = a violation, or self-test failure
            | 2 = inconclusive (git/F-before unavailable).
"""

import os
import re
import subprocess
import sys
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# Family root standard (matches sibling check-loop-state.py): the default root
# is the parent of the deploy/ dir this script lives in, never os.getcwd() --
# a CWD-relative root silently pointed the citation scan at whatever directory
# the operator happened to be standing in.
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_HERE)

H_RE = re.compile(r"^(#{2,3})\s+(.*?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
_NUM_RE = re.compile(r"\d+")

# content2-amendment A2 -- conjunction/range-aware citation grammar. The pre-amendment
# regex `lines?\s+(\d+(?:\s+and\s+\d+)*)` read "lines 1622 and 661" as the SINGLE number
# 1622 (EX-4, CONFIRMED against the gate's own canonical fixture). This one finds the whole
# numeric run joined by list separators (, and &) or range separators (- –); _parse_citation
# decomposes it into discrete citations + range citations.
CITE_FIND_RE = re.compile(
    r"lines?\s+(\d+(?:\s*(?:,|and|&|–|-)\s*\d+)*)", re.IGNORECASE)

# v3.0.51 (v3.0-141, brief v4 [R3-C1]): the generation-tagged citation grammar --
# `view.md:80@<hash8>` -- minted by the absorb pipeline from Release 2 on. ONE home for
# the pattern (the v3.0-132 parity discipline): deploy/retire.py (resolution through the
# redirect chain) and deploy/compile-v2.py (minting + bare-citation refusal) both import
# THIS constant. Byte-identical to the verb's original v3.0.50 pattern.
TAGGED_CITE_RE = re.compile(r"([A-Za-z0-9._-]+\.md):(\d+)@([0-9a-f]{8})")

# content2-amendment A1.2 -- explicit ID spans: `{#some-id}` attrs and `<a id="some-id">`
# HTML anchors. These join the heading-slug set to form the full anchor set A(F).
_ID_ATTR_RE = re.compile(r"\{#([A-Za-z0-9_-]+)\}")
_ID_HTML_RE = re.compile(r"""<a\s+[^>]*\bid\s*=\s*["']([A-Za-z0-9_-]+)["']""", re.IGNORECASE)


def slugify(heading):
    """GitHub-style heading slug: lowercased, non-alnum -> hyphen, collapse, trim."""
    s = heading.strip().lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def heading_slugs(text):
    """Slugs of all H2/H3 headings outside fenced code, in order (with duplicates)."""
    out = []
    in_fence = False
    fch = ""
    flen = 0
    for line in text.splitlines():
        m = FENCE_RE.match(line)
        if m:
            run = m.group(1)
            if not in_fence:
                in_fence, fch, flen = True, run[0], len(run)
            elif run[0] == fch and len(run) >= flen and m.group(2).strip() == "":
                in_fence = False
            continue
        if in_fence:
            continue
        hm = H_RE.match(line)
        if hm:
            out.append(slugify(hm.group(2)))
    return out


def explicit_ids(text):
    """content2-amendment A1.2: every `{#id}` attribute + `<a id="id">` anchor, outside
    fenced code, in order (with duplicates so conservation can count them)."""
    out = []
    in_fence = False
    fch = ""
    flen = 0
    for line in text.splitlines():
        m = FENCE_RE.match(line)
        if m:
            run = m.group(1)
            if not in_fence:
                in_fence, fch, flen = True, run[0], len(run)
            elif run[0] == fch and len(run) >= flen and m.group(2).strip() == "":
                in_fence = False
            continue
        if in_fence:
            continue
        out.extend(_ID_ATTR_RE.findall(line))
        out.extend(_ID_HTML_RE.findall(line))
    return out


def anchor_multiset(text):
    """content2-amendment A1: the anchor set A(F) = heading slugs UNION explicit ID spans.
    Returned as a Counter so the conservation check (nothing dropped / nothing fabricated)
    can compare multiplicities. For UNIQUE anchors (all citeable clause-IDs and every
    explicit ID) count==1, so multiset-conservation enforces the frozen 'exactly one Pi'
    (a unique anchor landing in two parts trips 'fabricated'); a generic subheading that
    legitimately repeats once per decision is conserved by count rather than false-flagged
    as an ambiguous citation. This is the review-aware refinement of the frozen gloss, not
    a weakening of it."""
    c = Counter(s for s in heading_slugs(text) if s)
    c.update(i for i in explicit_ids(text) if i)
    return c


def _parse_citation(expr):
    """Decompose a matched citation expression into (discretes:set[int], ranges:set[(a,b)]).
    List separators (, and &) delimit separate citations; a `-`/`–` between two numbers is
    one range citation (A2)."""
    discretes = set()
    ranges = set()
    for seg in re.split(r"\s*(?:,|and|&)\s*", expr, flags=re.IGNORECASE):
        seg = seg.strip()
        if not seg:
            continue
        rm = re.match(r"^(\d+)\s*[–-]\s*(\d+)$", seg)
        if rm:
            a, b = int(rm.group(1)), int(rm.group(2))
            ranges.add((min(a, b), max(a, b)))
        else:
            for n in _NUM_RE.findall(seg):
                discretes.add(int(n))
    return discretes, ranges


def cited_citations(text, basename):
    """All citations a text makes against `basename`: (discretes, ranges). Replaces the
    pre-amendment cited_line_numbers, which silently dropped every number after the first.
    v3.0.51 (v3.0-141): a generation-tagged citation `basename:NN@<hash8>` is a discrete
    citation of line NN too -- the tagged grammar joins the legacy prose grammar here so
    the split gate's coverage check sees post-Release-2 citations."""
    discretes = set()
    ranges = set()
    if basename not in text:
        return discretes, ranges
    for m in re.finditer(re.escape(basename), text):
        window = text[m.end():m.end() + 200]
        for cm in CITE_FIND_RE.finditer(window):
            d, r = _parse_citation(cm.group(1))
            discretes.update(d)
            ranges.update(r)
    for tm in TAGGED_CITE_RE.finditer(text):
        if tm.group(1) == basename:
            discretes.add(int(tm.group(2)))
    return discretes, ranges


def _line_covered(n, lines_map, review_lines):
    key = str(n)
    return key in lines_map or key in review_lines


def _range_covered(a, b, lines_map, review_lines):
    """A2: a range citation is covered iff the whole range maps -- an explicit range key,
    or both endpoints resolved and (when both are in the line map) landing in the SAME part
    (the range did not straddle a split boundary uncovered)."""
    for key in ("%d-%d" % (a, b), "%d–%d" % (a, b)):
        if key in lines_map or key in review_lines:
            return True
    if _line_covered(a, lines_map, review_lines) and _line_covered(b, lines_map, review_lines):
        ka, kb = str(a), str(b)
        if ka in lines_map and kb in lines_map and lines_map[ka] != lines_map[kb]:
            return False
        return True
    return False


def _frontmatter(text):
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i])
    return None


def stub_is_superseded(text):
    fm = _frontmatter(text) or ""
    for ln in fm.splitlines():
        m = re.match(r"^status:\s*(\S+)", ln)
        if m and m.group(1).strip().strip('"\'') == "superseded":
            return True
    return False


def parse_redirect_map(text):
    """Extract and YAML-load the fenced ```redirect-map block; None if absent."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = FENCE_RE.match(lines[i])
        if m and "redirect-map" in m.group(2):
            body = []
            j = i + 1
            while j < len(lines):
                m2 = FENCE_RE.match(lines[j])
                if m2 and m2.group(1)[0] == m.group(1)[0] and m2.group(2).strip() == "":
                    break
                body.append(lines[j])
                j += 1
            if yaml is None:
                return {}
            return yaml.safe_load("\n".join(body)) or {}
        i += 1
    return None


def cited_line_numbers(text, basename):
    """All line numbers a text cites against `basename` (e.g. 'foo.md lines 12 and 34')."""
    nums = set()
    if basename not in text:
        return nums
    # Scan a window after each basename mention for a `lines? N [and N]` citation.
    for m in re.finditer(re.escape(basename), text):
        window = text[m.end():m.end() + 200]
        for cm in CITE_LINE_RE.finditer(window):
            nums.update(int(n) for n in _NUM_RE.findall(cm.group(1)))
    return nums


# The next two helpers are copied verbatim from sibling check-loop-state.py
# (its 2026-08-04 fork-layout fix). The deploy/ dir has no package imports, so
# the module stays self-contained by carrying its own copy.
def _envelope_has_records(path):
    """A handoff envelope 'has records' when at least one immediate
    subdirectory carries a meta.yaml. Protocol DOCS living in the same
    directory (core/handoffs/HANDOFF-*.md, README.md) are plain files and
    never count."""
    if not os.path.isdir(path):
        return False
    for entry in os.listdir(path):
        if os.path.isfile(os.path.join(path, entry, "meta.yaml")):
            return True
    return False


def handoffs_dir(root):
    """Resolve the handoff envelope. Returns (path, ambiguous).

    The fork this sensor grew up on keeps records at `handoffs/`; the shipped
    template's protocol README instructs instances to create them at
    `core/handoffs/<YYYY-MM-DD>-<slug>/`, and the /handoff skill names both
    layouts ("the project's `handoffs/` directory -- `core/handoffs/` on
    projects that keep it there"). This sensor hardwired the fork path, so on
    every docs-following instance it scanned an empty location and went
    INCONCLUSIVE (reported live 2026-08-04, a LAMPS T1 lock session).

    Resolution is by CONTENT, not preference: the candidate holding at least
    one record folder wins. Records in BOTH is a real defect state --
    (first, True) so callers refuse loudly instead of silently scanning one
    envelope of two. Records in NEITHER falls back to the first candidate
    that exists on disk (empty-envelope reporting keeps its old shape), then
    to the fork path."""
    cands = [os.path.join(root, "handoffs"),
             os.path.join(root, "core", "handoffs")]
    with_records = [c for c in cands if _envelope_has_records(c)]
    if len(with_records) == 2:
        return with_records[0], True
    if with_records:
        return with_records[0], False
    for c in cands:
        if os.path.isdir(c):
            return c, False
    return cands[0], False


def validate_split(before_text, stub_text, root, parts_texts=None, cite_files=None):
    """Return a list of violation strings ([] = integral). parts_texts/cite_files
    let the self-test inject content without disk; runtime mode reads from `root`."""
    violations = []

    # 1. stub: superseded + redirect map
    if not stub_is_superseded(stub_text):
        violations.append("stub is not status: superseded")
    rmap = parse_redirect_map(stub_text)
    if rmap is None:
        violations.append("stub has no ```redirect-map block")
        return violations  # nothing else is checkable without the map
    parts = rmap.get("parts") or []
    anchors_map = rmap.get("anchors") or {}
    lines_map = {str(k): v for k, v in (rmap.get("lines") or {}).items()}
    review_lines = {str(x) for x in (rmap.get("review") or [])}

    # resolve part texts
    if parts_texts is None:
        parts_texts = {}
        for p in parts:
            fp = p if os.path.isabs(p) else os.path.join(root, p)
            try:
                with open(fp, "r", encoding="utf-8-sig") as fh:
                    parts_texts[p] = fh.read()
            except OSError as e:
                violations.append("redirect-map part missing on disk: %s (%s)" % (p, e))

    # 2. anchors: CONSERVATION. The multiset of H2/H3 heading slugs in F-before
    #    must be preserved across the parts -- nothing dropped (zero = lost
    #    knowledge), nothing fabricated (parts carry a slug F-before never had).
    #    Uniqueness is NOT required: a generic subheading ("rationale", "steps")
    #    legitimately repeats once per decision/phase, so it appears in as many
    #    parts as F-before had it. Citeable clause-IDs (## Decision #N) are unique
    #    in F-before and so land in exactly one part as a consequence.
    before_ctr = anchor_multiset(before_text)
    parts_ctr = Counter()
    for ptext in parts_texts.values():
        parts_ctr.update(anchor_multiset(ptext))
    for s, n in before_ctr.items():
        if parts_ctr[s] < n:
            violations.append("dropped anchor #%s (%d in F-before, %d across parts)"
                              % (s, n, parts_ctr[s]))
    for s, n in parts_ctr.items():
        if n > before_ctr.get(s, 0):
            violations.append("fabricated anchor #%s (%d across parts, %d in F-before)"
                              % (s, n, before_ctr.get(s, 0)))
    # redirect-map.anchors accuracy (non-exhaustive): each listed clause-anchor must
    # be a real anchor (heading slug or explicit ID span) present in the part it maps to.
    for anchor, part in anchors_map.items():
        ptext = parts_texts.get(part)
        if ptext is None:
            violations.append("redirect-map.anchors -> unknown part: #%s -> %s" % (anchor, part))
        elif anchor not in anchor_multiset(ptext):
            violations.append("redirect-map.anchors #%s not an anchor in %s" % (anchor, part))

    # 3. citations: raw/ + the RESOLVED handoff envelope's line-numbered
    #    citations covered by the lines map. The envelope is resolved by
    #    content via handoffs_dir() -- the old hardwired ("raw", "handoffs")
    #    tuple scanned zero handoff files on template-layout instances
    #    (records at core/handoffs/), so this leg passed vacuously there.
    #    Records in BOTH envelopes is a defect state, named loudly as a
    #    violation rather than silently scanning one envelope of two.
    basename = os.path.basename(stub_path_global or "F.md")
    if cite_files is None:
        hdir, env_ambiguous = handoffs_dir(root)
        if env_ambiguous:
            violations.append(
                "handoff records found in BOTH handoffs/ and core/handoffs/ -- "
                "two live envelopes is a defect state (records must live in "
                "exactly one); consolidate before the citation scan can be "
                "trusted")
        cite_files = {}
        for d in (os.path.join(root, "raw"), hdir):
            if not os.path.isdir(d):
                continue
            for dr, _ds, fs in os.walk(d):
                for f in fs:
                    if f.endswith((".md", ".yaml")):
                        fp = os.path.join(dr, f)
                        try:
                            with open(fp, "r", encoding="utf-8-sig") as fh:
                                cite_files[fp] = fh.read()
                        except OSError:
                            pass
    for fp, ctext in cite_files.items():
        discretes, ranges = cited_citations(ctext, basename)
        for n in sorted(discretes):
            if not _line_covered(n, lines_map, review_lines):
                violations.append("uncovered line citation %s -> %s (cites %s)"
                                  % (n, fp, basename))
        for a, b in sorted(ranges):
            if not _range_covered(a, b, lines_map, review_lines):
                violations.append("uncovered range citation %d-%d -> %s (cites %s)"
                                  % (a, b, fp, basename))
    return violations


stub_path_global = None  # set by main() so basename is available to validate_split


def anchor_set_empty(before_text):
    """content2-amendment A1 fail-closed guard: True iff A(F-before) == empty. An empty
    anchor set on a file worth splitting means the extractor is broken (EX-3: a checker
    that matches nothing would otherwise PASS over an empty set) -> INCONCLUSIVE, never PASS."""
    return not anchor_multiset(before_text)


def run(stub_path, before_ref="HEAD", root=None):
    global stub_path_global
    stub_path_global = stub_path
    # Family root standard: default to the repo containing this deploy/ dir,
    # never the caller's CWD (see REPO_ROOT above).
    root = os.path.abspath(root) if root else REPO_ROOT
    try:
        with open(stub_path, "r", encoding="utf-8-sig") as fh:
            stub_text = fh.read()
    except OSError as e:
        print("RESULT: INCONCLUSIVE -- stub unreadable: %s" % e)
        return 2
    rel = os.path.relpath(stub_path, root).replace("\\", "/")
    try:
        before_text = subprocess.run(
            ["git", "show", "%s:%s" % (before_ref, rel)],
            cwd=root, capture_output=True, encoding="utf-8", check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print("RESULT: INCONCLUSIVE -- cannot read F-before (%s:%s): %s" % (before_ref, rel, e))
        return 2
    if anchor_set_empty(before_text):
        print("RESULT: INCONCLUSIVE -- F-before %s has an empty anchor set "
              "(content2-amendment A1 fail-closed: extractor broken, not zero knowledge)" % rel)
        return 2
    violations = validate_split(before_text, stub_text, root)
    for v in violations:
        print("  VIOLATION %s" % v)
    if violations:
        print("RESULT: FAIL -- %d split-integrity violation(s)" % len(violations))
        return 1
    print("RESULT: PASS -- split of %s is integral" % rel)
    return 0


# ---------------------------------------------------------------------------
# Self-test: an embedded 3-part split + the invalid variants.
# ---------------------------------------------------------------------------

_BEFORE = """---
title: F
status: active
---
## Decision A
aaa
## Decision B
bbb (see line 6 here)
## Decision C
ccc
"""

_PART1 = "---\ntitle: F-A\nstatus: active\n---\n## Decision A\naaa\n"
_PART2 = "---\ntitle: F-B\nstatus: active\n---\n## Decision B\nbbb\n"
_PART3 = "---\ntitle: F-C\nstatus: active\n---\n## Decision C\nccc\n"

_STUB_OK = """---
title: F (split)
status: superseded
---
# F was split

```redirect-map
parts:
  - F-a.md
  - F-b.md
  - F-c.md
anchors:
  decision-a: F-a.md
  decision-b: F-b.md
  decision-c: F-c.md
lines:
  "6": F-b.md
```
"""

_CITER = "See F.md lines 6 for the B rule.\n"


def _parts_ok():
    return {"F-a.md": _PART1, "F-b.md": _PART2, "F-c.md": _PART3}


def self_test():
    global stub_path_global
    stub_path_global = "F.md"
    failed = 0
    total = 0

    def case(name, ok):
        nonlocal failed, total
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    # 1. valid split -> no violations
    v = validate_split(_BEFORE, _STUB_OK, ".", parts_texts=_parts_ok(), cite_files={"raw/x.md": _CITER})
    case("valid split has zero violations", v == [])

    # 2. stub not superseded
    bad_stub = _STUB_OK.replace("status: superseded", "status: active")
    v = validate_split(_BEFORE, bad_stub, ".", parts_texts=_parts_ok(), cite_files={})
    case("non-superseded stub trips", any("superseded" in x for x in v))

    # 3. dropped anchor (part3 omitted -> Decision C in no part)
    parts_drop = {"F-a.md": _PART1, "F-b.md": _PART2}
    v = validate_split(_BEFORE, _STUB_OK, ".", parts_texts=parts_drop, cite_files={})
    case("dropped anchor trips", any("dropped anchor" in x for x in v))

    # 4. fabricated/duplicated anchor (Decision B in two parts; F-before had it once)
    parts_dup = {"F-a.md": _PART1, "F-b.md": _PART2, "F-c.md": _PART3, "F-b2.md": _PART2}
    v = validate_split(_BEFORE, _STUB_OK, ".", parts_texts=parts_dup, cite_files={})
    case("duplicated anchor trips", any("fabricated anchor" in x for x in v))

    # 4b. a generic heading repeated once-per-decision is CONSERVED (not flagged) --
    #     the real-corpus property the strict-uniqueness model got wrong.
    before_rep = ("---\nstatus: active\n---\n## Decision A\n### Reasoning\na\n"
                  "## Decision B\n### Reasoning\nb\n")
    pA = "---\nstatus: active\n---\n## Decision A\n### Reasoning\na\n"
    pB = "---\nstatus: active\n---\n## Decision B\n### Reasoning\nb\n"
    stub_rep = ("---\nstatus: superseded\n---\n# x\n\n```redirect-map\n"
                "parts: [A.md, B.md]\nanchors:\n  decision-a: A.md\n  decision-b: B.md\n"
                "lines: {}\n```\n")
    v = validate_split(before_rep, stub_rep, ".", parts_texts={"A.md": pA, "B.md": pB}, cite_files={})
    case("repeated heading conserved (not flagged)", v == [])

    # 5. uncovered line citation (lines map empty)
    stub_noline = _STUB_OK.replace('lines:\n  "6": F-b.md\n', "lines: {}\n")
    v = validate_split(_BEFORE, stub_noline, ".", parts_texts=_parts_ok(),
                       cite_files={"raw/x.md": _CITER})
    case("uncovered line citation trips", any("uncovered line citation" in x for x in v))

    # 6. citation healed via REVIEW instead of the map
    stub_review = _STUB_OK.replace('lines:\n  "6": F-b.md\n', 'lines: {}\nreview: [6]\n')
    v = validate_split(_BEFORE, stub_review, ".", parts_texts=_parts_ok(),
                       cite_files={"raw/x.md": _CITER})
    case("citation healed via REVIEW passes", v == [])

    # 6b. v3.0-141: a generation-TAGGED citation (`F.md:6@<hash8>`) is a citation of
    #     line 6 to this gate too -- uncovered it trips, covered it passes (the tagged
    #     grammar joined the legacy grammar; single home for retire.py / compile-v2.py).
    tagged_citer = "see F.md:6@0a1b2c3d for the rationale\n"
    d6, r6 = cited_citations(tagged_citer, "F.md")
    case("tagged citation parsed as discrete line 6", d6 == {6} and r6 == set())
    v = validate_split(_BEFORE, stub_noline, ".", parts_texts=_parts_ok(),
                       cite_files={"raw/t.md": tagged_citer})
    case("uncovered TAGGED citation trips", any("uncovered line citation" in x for x in v))
    v = validate_split(_BEFORE, _STUB_OK, ".", parts_texts=_parts_ok(),
                       cite_files={"raw/t.md": tagged_citer})
    case("covered TAGGED citation passes", v == [])

    # 7. missing redirect map
    v = validate_split(_BEFORE, "---\nstatus: superseded\n---\nno map\n", ".",
                       parts_texts=_parts_ok(), cite_files={})
    case("missing redirect-map trips", any("redirect-map" in x for x in v))

    # -- content2-amendment A3 fixtures ------------------------------------------
    prev_stub = stub_path_global
    stub_path_global = "schema-foundations.md"
    _before_c = "---\nstatus: active\n---\n## Money Rule\nx\n"
    _parts_c = {"m.md": "---\nstatus: active\n---\n## Money Rule\nx\n"}
    _citer_conj = {"raw/2026-06-10-ryan-bookings-entity-schema.md":
                   "cites schema-foundations.md lines 1622 and 661 for the money rule.\n"}

    # split-citation-conjunction (EX-4): BOTH numbers extracted; PASS iff both resolve.
    d, r = cited_citations(_citer_conj["raw/2026-06-10-ryan-bookings-entity-schema.md"],
                           "schema-foundations.md")
    case("A3 conjunction extracts BOTH 1622 and 661 (EX-4)", d == {1622, 661} and r == set())
    _stub_both = ("---\nstatus: superseded\n---\n# split\n\n```redirect-map\n"
                  "parts: [m.md]\nanchors:\n  money-rule: m.md\n"
                  'lines:\n  "1622": m.md\n  "661": m.md\n```\n')
    v = validate_split(_before_c, _stub_both, ".", parts_texts=_parts_c, cite_files=_citer_conj)
    case("A3 split-citation-conjunction PASS when both resolve", v == [])
    _stub_one = _stub_both.replace('  "661": m.md\n', "")
    v = validate_split(_before_c, _stub_one, ".", parts_texts=_parts_c, cite_files=_citer_conj)
    case("A3 split-citation-conjunction FAIL when only 1622 resolves (661 unhealed)",
         any("uncovered line citation 661" in x for x in v))

    # split-citation-range: 'lines 100–120' is ONE range; covered iff the whole range maps.
    _citer_range = {"raw/x.md": "see schema-foundations.md lines 100–120 here.\n"}
    d, r = cited_citations(_citer_range["raw/x.md"], "schema-foundations.md")
    case("A3 range citation parses as one range (100,120)", r == {(100, 120)} and d == set())
    _stub_range_ok = ("---\nstatus: superseded\n---\n# s\n\n```redirect-map\n"
                      "parts: [m.md]\nanchors:\n  money-rule: m.md\n"
                      'lines:\n  "100": m.md\n  "120": m.md\n```\n')
    v = validate_split(_before_c, _stub_range_ok, ".", parts_texts=_parts_c, cite_files=_citer_range)
    case("A3 split-citation-range PASS when whole range lands in one part", v == [])
    _before_c2 = "---\nstatus: active\n---\n## Money Rule\nx\n## Tax Rule\ny\n"
    _parts_c2 = {"m.md": "---\nstatus: active\n---\n## Money Rule\nx\n",
                 "n.md": "---\nstatus: active\n---\n## Tax Rule\ny\n"}
    _stub_range_split = ("---\nstatus: superseded\n---\n# s\n\n```redirect-map\n"
                         "parts: [m.md, n.md]\nanchors:\n  money-rule: m.md\n  tax-rule: n.md\n"
                         'lines:\n  "100": m.md\n  "120": n.md\n```\n')
    v = validate_split(_before_c2, _stub_range_split, ".", parts_texts=_parts_c2,
                       cite_files=_citer_range)
    case("A3 split-citation-range FAIL when range straddles two parts",
         any("uncovered range citation 100-120" in x for x in v))

    # split-empty-anchor-set (A1 fail-closed): F-before with no headings/IDs -> INCONCLUSIVE.
    _before_empty = "---\nstatus: active\n---\njust prose, no headings and no id spans.\n"
    case("A3 split-empty-anchor-set: empty A(F-before) -> anchor_set_empty True (exit 2)",
         anchor_set_empty(_before_empty) is True)
    case("A3 heading-rich F-before is NOT empty (guard is non-vacuous)",
         anchor_set_empty(_BEFORE) is False)

    # A1.2 explicit ID spans join the anchor set.
    _before_ids = "---\nstatus: active\n---\n## Heading {#money}\nprose <a id=\"tax\"></a> more\n"
    case("A1.2 explicit {#id} and <a id> join the anchor set",
         set(anchor_multiset(_before_ids)) >= {"money", "tax"})
    stub_path_global = prev_stub

    # -- envelope resolution for the citation leg (fixture style follows
    #    check-loop-state.py run_self_test "(1a3) envelope resolution"):
    #    on-disk tempdir roots, cite_files=None so the real scan runs.
    import shutil
    import tempfile

    def _cite_root(record_dirs, citer_rel=None):
        d = tempfile.mkdtemp(prefix="csplit-env-")
        for rel in record_dirs:
            p = os.path.join(d, *rel.split("/"))
            os.makedirs(p, exist_ok=True)
            with open(os.path.join(p, "meta.yaml"), "w",
                      encoding="utf-8") as fh:
                fh.write("status: open\n")
        if citer_rel:
            cp = os.path.join(d, *citer_rel.split("/"))
            with open(cp, "w", encoding="utf-8") as fh:
                fh.write(_CITER)
        return d

    # template layout: a citing record at core/handoffs/ must be SEEN by the
    # citation leg -- the old ("raw", "handoffs") tuple covered zero handoff
    # files here, so an uncovered citation passed vacuously.
    d = _cite_root(["core/handoffs/2026-08-01-h"],
                   citer_rel="core/handoffs/2026-08-01-h/notes.md")
    try:
        stub_noline_env = _STUB_OK.replace('lines:\n  "6": F-b.md\n',
                                           "lines: {}\n")
        v = validate_split(_BEFORE, stub_noline_env, d,
                           parts_texts=_parts_ok(), cite_files=None)
        case("template-layout core/handoffs citer is scanned (uncovered trips)",
             any("uncovered line citation" in x for x in v))
        v = validate_split(_BEFORE, _STUB_OK, d,
                           parts_texts=_parts_ok(), cite_files=None)
        case("template-layout citer covered by the lines map -> clean", v == [])
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # records in BOTH envelopes: loud defect naming both, never a silent
    # single-envelope scan.
    d = _cite_root(["handoffs/2026-08-01-a", "core/handoffs/2026-08-01-b"])
    try:
        v = validate_split(_BEFORE, _STUB_OK, d,
                           parts_texts=_parts_ok(), cite_files=None)
        case("records in BOTH envelopes -> loud violation naming both",
             any("BOTH handoffs/ and core/handoffs/" in x for x in v))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    if failed:
        print("check-split self-test: FAIL (%d/%d)" % (failed, total))
        return 1
    print("check-split self-test: PASS (%d/%d)" % (total, total))
    return 0


def main(argv):
    args = argv[1:]
    if "--self-test" in args:
        return self_test()
    before_ref = "HEAD"
    root = None
    positional = []
    i = 0
    while i < len(args):
        if args[i] == "--before":
            before_ref = args[i + 1]; i += 2; continue
        if args[i] == "--root":
            root = args[i + 1]; i += 2; continue
        positional.append(args[i]); i += 1
    if not positional:
        print("usage: check-split.py STUB_PATH [--before REF] [--root DIR] | --self-test")
        return 1
    return run(positional[0], before_ref=before_ref, root=root)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
