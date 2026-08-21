#!/usr/bin/env python3
"""retire-manifest.py -- READ-ONLY corpus-wide view-retirement dry-run manifest
(memory-engine v3, ADR #11 Release 1; backlog v3.0-129).

The measuring instrument the ratified design names as its Release-1 deliverable: before
the retirement verb writes a byte, this tool answers, per over-cap view, what retirement
WOULD do -- spans (preamble included), candidate mode per span, descendant headings and
explicit anchors the stub must retain, inbound line citations from the COMPLETE
registered citation universe (every registered artifact + the wiki tree), the
content-addressed cold identity each span would take, predicted post-retirement size,
and whether the view can reach its cap at all under largest-first retirement.

It writes NOTHING into the repo -- by construction: the only writes are the JSON manifest
(+ optional markdown summary) to caller-named --out/--md paths, and a path inside the repo
root is REFUSED (exit 2). Default output is stdout. Judgment questions -- whether a span's
substantive assertions all have an immutable ledger home -- are NOT decided here: a
span that cites existing raw/ events is labeled `dedup-candidate` (the verify leg
decides at retirement time; an unmapped assertion downgrades to cold-relocate), every
other span is `cold-relocate`. The manifest never claims more than it measured.

Reuses, never re-implements: the cap table and LF-normalized byte rule
(check-caps.py), the heading/anchor/citation grammar (check-split.py), the registration
chain (registrations.py) as the enumeration of the citation universe.

Usage:
  retire-manifest.py [--root DIR] [--all | VIEW ...] [--out manifest.json] [--md summary.md]
  retire-manifest.py --self-test

Exit codes: 0 = manifest produced | 1 = self-test failure | 2 = inconclusive (cap
config or registration chain unreadable -- the design says retirement REFUSES when the
citation universe cannot be established; the manifest says so rather than guessing).
"""

import hashlib
import json
import os
import re
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

try:
    import check_caps as _cc  # noqa: F401  (module-name form, if present)
except Exception:
    _cc = None

# check-caps.py / check-split.py have hyphenated names; load them by path.
import importlib.util


def _load_by_path(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_caps = _load_by_path("_retire_caps", "check-caps.py")
_split = _load_by_path("_retire_split", "check-split.py")
_reg = _load_by_path("_retire_reg", "registrations.py")

DERIV_START = _caps.DERIV_START
DERIV_END = _caps.DERIV_END
H_ANY_RE = re.compile(r"^(#{2,6})\s+(.*?)\s*#*\s*$")
RAW_REF_RE = re.compile(r"raw/(20\d\d-\d\d-\d\d-[A-Za-z0-9._-]+\.md)")
DATED_DECISION_RE = re.compile(r"\(locked\s+20\d\d-\d\d-\d\d|decision\s+[A-Z]{1,4}\d", re.IGNORECASE)
STUB_OVERHEAD_BYTES = 96  # pointer line + blank lines; the real stub is engine-templated


def lf_bytes(text):
    return len(text.replace("\r\n", "\n").encode("utf-8"))


def sha256_lf(text):
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def slug(s):
    s = re.sub(r"[^A-Za-z0-9]+", "-", s.strip().lower()).strip("-")
    return s or "section"


# ------------------------------------------------------------------ parsing
def body_region(text):
    """(pre, body_lines, post, body_start_line) -- body excludes YAML frontmatter and the
    derivation region (the same exclusions the validator and check-caps use)."""
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    fm_end = -1
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                fm_end = j
                break
    start = fm_end + 1
    d_start = d_end = None
    for k in range(start, len(lines)):
        s = lines[k].strip()
        if d_start is None and s.startswith(DERIV_START):
            d_start = k
        elif d_start is not None and s.startswith(DERIV_END):
            d_end = k
            break
    body_idx = [k for k in range(start, len(lines))
                if not (d_start is not None and d_end is not None and d_start <= k <= d_end)]
    return lines, body_idx


class _FenceTracker(object):
    """CommonMark-shaped fence state: a fence opens with a run of >=3 backticks or tildes
    and closes ONLY on a line whose run uses the SAME character with length >= the opener
    (cross-vendor round-1 catch: a bare toggle let a ~~~ line close a ``` fence and expose
    pseudo-headings). Mixed/nested markers of the other character are content."""

    def __init__(self):
        self.open_char = None
        self.open_len = 0

    def feed(self, line):
        """Returns True if the line is INSIDE a fence (or is a fence delimiter)."""
        m = _split.FENCE_RE.match(line)
        # DELIBERATELY matches check-split's fence recognition, leading whitespace and
        # all (cross-vendor round-3): the Release-2 acceptance gate reuses check-split's
        # anchor primitive, so this instrument must see exactly the anchor set that gate
        # will see. check-split's FENCE_RE is looser than CommonMark (it accepts a 4+-space
        # indented marker as a fence); that leniency is filed upstream as a shared defect
        # to fix in BOTH tools together (v3.0-132), never in one alone.
        if m:
            run = m.group(1)
            ch, n = run[0], len(run)
            if self.open_char is None:
                self.open_char, self.open_len = ch, n
                return True
            if ch == self.open_char and n >= self.open_len and not m.group(2).strip():
                self.open_char, self.open_len = None, 0
                return True
            return True
        return self.open_char is not None


def parse_spans(text):
    """Parser-defined spans over the body: `(preamble)` + one span per heading (H2-H6), each
    running to the line before the next heading of the same-or-higher level. Returns a list
    of dicts with 1-based inclusive line numbers into the ORIGINAL file."""
    lines, body_idx = body_region(text)
    # code fences are not headings
    fence = _FenceTracker()
    heads = []  # (idx_in_file, level, title)
    for k in body_idx:
        ln = lines[k]
        if fence.feed(ln):
            continue
        m = H_ANY_RE.match(ln)
        if m:
            heads.append((k, len(m.group(1)), m.group(2).strip()))
    spans = []
    if not body_idx:
        return spans
    first_head = heads[0][0] if heads else None
    pre_idx = [k for k in body_idx if first_head is None or k < first_head]
    if pre_idx and any(lines[k].strip() for k in pre_idx):
        spans.append(_mk_span(lines, pre_idx, 0, "(preamble)", body_idx))
    for n, (k, lvl, title) in enumerate(heads):
        end_excl = len(lines)
        for k2, lvl2, _ in heads[n + 1:]:
            if lvl2 <= lvl:
                end_excl = k2
                break
        idx = [i for i in body_idx if k <= i < end_excl]
        spans.append(_mk_span(lines, idx, lvl, title, body_idx))
    return spans


def _mk_span(lines, idx, level, title, body_idx):
    span_text = "\n".join(lines[i] for i in idx) + "\n"
    desc = []
    explicit = []
    fence = _FenceTracker()
    for i in idx[1:] if level else idx:
        ln = lines[i]
        if fence.feed(ln):
            continue
        m = H_ANY_RE.match(ln)
        if m:
            desc.append({"line": i + 1, "level": len(m.group(1)), "title": m.group(2).strip()})
    # explicit anchors: fence-filtered, matching check-split's anchor grammar (an `{#id}`
    # inside a code block is not an anchor -- cross-vendor round-2 catch)
    fence2 = _FenceTracker()
    for i in idx:
        if fence2.feed(lines[i]):
            continue
        for m in _split._ID_ATTR_RE.finditer(lines[i]):
            explicit.append({"line": i + 1, "id": m.group(1)})
        for m in _split._ID_HTML_RE.finditer(lines[i]):
            explicit.append({"line": i + 1, "id": m.group(1)})
    raws = sorted(set(RAW_REF_RE.findall(span_text)))
    return {
        "title": title, "level": level,
        "start_line": idx[0] + 1, "end_line": idx[-1] + 1,
        "bytes": lf_bytes(span_text),
        "sha256": sha256_lf(span_text),
        "descendant_headings": desc,
        "explicit_anchors": explicit,
        "raw_refs": raws,
        "dated_decision_shape": bool(DATED_DECISION_RE.search(title)),
        "cold_object": "wiki/cold/%s/%s--%s.md" % ("{view-slug}", slug(title), sha256_lf(span_text)),
    }


# ------------------------------------------------------------------ citations
def citation_universe(root, walk=os.walk):
    """Every registered artifact (the registration chain is the enumeration) + the wiki
    tree. Returns (paths, note). Raises RuntimeError when the chain cannot be read OR any
    part of the wiki tree cannot be enumerated -- the design's refusal condition (a
    silently-skipped subtree would be a partial universe; cross-vendor round-3 catch)."""
    paths = set()
    try:
        regs = _reg.load_registrations(root)
    except Exception as e:  # chain unreadable -> inconclusive
        raise RuntimeError("registration chain unreadable: %s" % e)
    # load_registrations returns the EFFECTIVE map {event_rel: record}; the keys are the
    # enumeration (later seq supersedes earlier for the same event -- already applied).
    for ev in (regs or {}):
        ev = str(ev).replace("\\", "/")
        if ev:
            paths.add(ev)
    wiki = os.path.join(root, "wiki")

    def _refuse(err):
        raise RuntimeError("wiki tree not fully enumerable: %s (%s)"
                           % (getattr(err, "filename", "?"), err.__class__.__name__))

    for dp, _, fns in walk(wiki, onerror=_refuse):
        for fn in fns:
            if fn.endswith(".md"):
                rel = os.path.relpath(os.path.join(dp, fn), root).replace("\\", "/")
                paths.add(rel)
    return sorted(paths), "registered artifacts + wiki tree (%d files)" % len(paths)


class UniverseUnreadable(RuntimeError):
    """A registered artifact could not be read: the citation universe is NOT established,
    so (design condition 3) the manifest is inconclusive rather than silently partial."""


def inbound_citations(root, view_rel, universe):
    """Line citations against this view anywhere in the universe, using check-split's
    EXACT semantics: the basename (`<name>.md`) followed within a 200-char window by its
    conjunction/range-aware citation grammar, decomposed by check-split's own
    `_parse_citation` (reversed ranges normalized). Returns a list of
    {artifact, artifact_line, discretes:[n], ranges:[[a,b]]}. Raises UniverseUnreadable
    on ANY registered artifact that cannot be read -- never skips."""
    base = os.path.basename(view_rel)
    out = []
    unreadable = []
    for rel in universe:
        if rel == view_rel:
            continue
        p = os.path.join(root, rel.replace("/", os.sep))
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as e:
            unreadable.append("%s (%s)" % (rel, e.__class__.__name__))
            continue
        if base not in text:
            continue
        for m in re.finditer(re.escape(base), text):
            window = text[m.end():m.end() + 200]
            line_no = text.count("\n", 0, m.start()) + 1
            for cm in _split.CITE_FIND_RE.finditer(window):
                d, r = _split._parse_citation(cm.group(1))
                if d or r:
                    out.append({"artifact": rel, "artifact_line": line_no,
                                "discretes": sorted(d), "ranges": sorted([list(x) for x in r])})
    if unreadable:
        raise UniverseUnreadable("%d registered artifact(s) unreadable: %s"
                                 % (len(unreadable), "; ".join(unreadable[:5])))
    return out


def _cite_hits_span(c, s):
    lo, hi = s["start_line"], s["end_line"]
    if any(lo <= n <= hi for n in c["discretes"]):
        return True
    return any(not (b < lo or a > hi) for a, b in c["ranges"])


# ------------------------------------------------------------------ per-view manifest
def view_manifest(root, view_rel, caps, universe):
    p = os.path.join(root, view_rel.replace("/", os.sep))
    with open(p, "r", encoding="utf-8-sig") as fh:
        text = fh.read()
    over, nbytes, cap, vt = _caps.verdict(p, caps)
    spans = parse_spans(text)
    vslug = slug(os.path.splitext(os.path.basename(view_rel))[0])
    for s in spans:
        s["cold_object"] = s["cold_object"].replace("{view-slug}", vslug)
        s["mode_candidate"] = ("dedup-candidate (verify leg maps every substantive assertion; "
                               "unmapped -> cold-relocate)" if s["raw_refs"] else "cold-relocate")
        s["raw_refs_exist"] = [os.path.isfile(os.path.join(root, "raw", r)) for r in s["raw_refs"]]
        stub_est = sum(len(lines_of(text)[d["line"] - 1].encode("utf-8")) + 1
                       for d in s["descendant_headings"]) + STUB_OVERHEAD_BYTES
        if s["level"]:
            stub_est += len(lines_of(text)[s["start_line"] - 1].encode("utf-8")) + 1
        s["stub_estimate_bytes"] = stub_est
        s["predicted_view_bytes_if_retired_alone"] = max(0, nbytes - s["bytes"] + stub_est)
    cites = inbound_citations(root, view_rel, universe)
    for s in spans:
        s["inbound_citations"] = [c for c in cites if _cite_hits_span(c, s)]
    in_any = {id(c) for s in spans for c in s["inbound_citations"]}
    orphan = [c for c in cites if id(c) not in in_any]
    # OWN bytes: a parent span's bytes include its children (its span runs to the next
    # same-or-higher heading). Planning over raw span bytes double-counts (cross-vendor
    # round-1 catch) and nominated a flight plan's whole live-work layer before any
    # finished sub-section (production catch). So each span's OWN bytes = its bytes minus
    # the bytes of the spans nested directly inside it; the plan retires OWN text,
    # largest-own first, and every byte is counted exactly once. A span with
    # descendants retires only its own text; the descendants are separate proposals.
    by_start = sorted(spans, key=lambda s: s["start_line"])
    for s in spans:
        inner = [t for t in by_start
                 if t is not s and t["start_line"] > s["start_line"] and t["end_line"] <= s["end_line"]]
        direct = [t for t in inner
                  if not any(u is not t and u["start_line"] < t["start_line"] and u["end_line"] >= t["end_line"]
                             for u in inner)]
        s["own_bytes"] = s["bytes"] - sum(t["bytes"] for t in direct)
        s["leaf"] = not s["descendant_headings"]
    running = nbytes
    plan = []
    for s in sorted(spans, key=lambda s: -s["own_bytes"]):
        if running <= cap:
            break
        gain = s["own_bytes"] - s["stub_estimate_bytes"]
        if gain <= 0:
            continue
        running -= gain
        plan.append({"title": s["title"], "leaf": s["leaf"], "own_bytes": s["own_bytes"],
                     "after": running})
    return {
        "view": view_rel, "view_type": vt, "bytes": nbytes, "cap": cap, "over_cap": over,
        "spans": spans,
        "citations_outside_any_span": orphan,
        "largest_first_plan": plan,
        "cap_reachable": running <= cap,
        "predicted_bytes_after_plan": running,
        "retirements_needed": len(plan),
    }


def lines_of(text):
    return text.replace("\r\n", "\n").split("\n")


def build_manifest(root, views=None, all_views=False):
    try:
        caps = _caps.load_caps(os.path.join(_HERE, "engine-caps.yaml"))
    except Exception as e:
        return None, "INCONCLUSIVE: cap config unreadable (%s)" % e
    try:
        universe, unote = citation_universe(root)
    except RuntimeError as e:
        return None, "INCONCLUSIVE: citation universe cannot be established -- %s; the design's retirement refusal condition" % e
    # every registered artifact must be READABLE, or the universe is not established
    probe_unreadable = []
    for rel in universe:
        p = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(p) or not os.access(p, os.R_OK):
            probe_unreadable.append(rel)
    if probe_unreadable:
        return None, ("INCONCLUSIVE: %d registered artifact(s) missing or unreadable (e.g. %s) -- the "
                      "citation universe is not established; the design's retirement refusal condition"
                      % (len(probe_unreadable), ", ".join(probe_unreadable[:3])))
    if not views:
        views = []
        for dp, _, fns in os.walk(os.path.join(root, "wiki")):
            if os.sep + "cold" in dp:
                continue
            for fn in fns:
                if fn.endswith(".md"):
                    views.append(os.path.relpath(os.path.join(dp, fn), root).replace("\\", "/"))
        views.sort()
    entries = []
    for v in views:
        p = os.path.join(root, v.replace("/", os.sep))
        over, nbytes, cap, vt = _caps.verdict(p, caps)
        if over or not all_views:
            entries.append(view_manifest(root, v, caps, universe))
    over_cap = [e for e in entries if e["over_cap"]]
    return {
        "tool": "retire-manifest.py", "read_only": True, "root": os.path.abspath(root),
        "citation_universe": unote,
        "views_scanned": len(views), "views_over_cap": len(over_cap),
        "views_cap_reachable": sum(1 for e in over_cap if e["cap_reachable"]),
        "views_cap_unreachable": [e["view"] for e in over_cap if not e["cap_reachable"]],
        "total_retirements_needed": sum(e["retirements_needed"] for e in over_cap),
        "legacy_citation_registry_seed": [
            {"view": e["view"], **c} for e in entries for s in e["spans"] for c in s["inbound_citations"]
        ] + [{"view": e["view"], **c} for e in entries for c in e["citations_outside_any_span"]],
        "entries": entries,
    }, "ok"


def markdown_summary(m):
    out = ["# Retirement dry-run manifest (read-only)", "",
           "Root: `%s` -- citation universe: %s" % (m["root"], m["citation_universe"]),
           "Views scanned: %d | over cap: %d | cap reachable by largest-first retirement: %d | unreachable: %d"
           % (m["views_scanned"], m["views_over_cap"], m["views_cap_reachable"], len(m["views_cap_unreachable"])),
           "Total retirements needed (largest-first): %d | legacy citations to register: %d"
           % (m["total_retirements_needed"], len(m["legacy_citation_registry_seed"])), "",
           "| view | bytes | cap | spans | largest span | dedup-cand. | cold | inbound cites | retirements | reachable |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for e in sorted(m["entries"], key=lambda e: -e["bytes"]):
        if not e["over_cap"]:
            continue
        sp = e["spans"]
        big = max(sp, key=lambda s: s["bytes"]) if sp else None
        out.append("| %s | %d | %d | %d | %s (%d) | %d | %d | %d | %d | %s |" % (
            e["view"], e["bytes"], e["cap"], len(sp),
            (big["title"][:40] if big else "-"), (big["bytes"] if big else 0),
            sum(1 for s in sp if s["mode_candidate"].startswith("dedup")),
            sum(1 for s in sp if s["mode_candidate"] == "cold-relocate"),
            sum(len(s["inbound_citations"]) for s in sp) + len(e["citations_outside_any_span"]),
            e["retirements_needed"], "yes" if e["cap_reachable"] else "NO"))
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ self-test
_FIXTURE_VIEW = """---
title: Fixture
---
# --- derivation
view: topic
# --- /derivation
Preamble paragraph before any heading. {#pre-anchor}

## Decision RT1 (locked 2026-05-23)

Body citing raw/2026-05-23-ryan-decision-rt1.md for the rationale.

### Nested detail

Some nested text with <a id="nested-id"></a> an explicit anchor.

## Current state

```
## not a heading inside a fence
~~~
## still inside the backtick fence (a tilde run must not close it)
```
Current prose.
"""


def self_test():
    fails = []
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "wiki", "systems"))
        os.makedirs(os.path.join(td, "raw"))
        os.makedirs(os.path.join(td, "receipts", "registrations"))
        vp = os.path.join(td, "wiki", "systems", "fixture.md")
        big = _FIXTURE_VIEW.replace("Current prose.", "Current prose.\n" + ("x" * 1000 + "\n") * 60)
        with open(vp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(big)
        with open(os.path.join(td, "raw", "2026-05-23-ryan-decision-rt1.md"), "w", encoding="utf-8") as fh:
            fh.write("event\n")
        citer = os.path.join(td, "raw", "2026-06-01-session-cites.md")
        with open(citer, "w", encoding="utf-8") as fh:
            # reversed range on purpose: check-split normalizes 15-14 -> (14,15)
            fh.write("See fixture.md lines 10 and 15-14 for RT1.\n")
        # registration chain: minimal records for both raw files (the chain writer
        # requires a git repo -- the fixture is one, quietly)
        import subprocess
        subprocess.run(["git", "init", "-q", td], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for i, ev in enumerate(["raw/2026-05-23-ryan-decision-rt1.md", "raw/2026-06-01-session-cites.md"], 1):
            try:
                _reg.append_registration(td, _reg._minimal(ev))
            except Exception as e:
                fails.append("fixture registration failed: %s" % e)
                break
        m, note = build_manifest(td)
        if m is None:
            fails.append("manifest inconclusive: %s" % note)
        else:
            e = m["entries"][0]
            titles = [s["title"] for s in e["spans"]]
            if titles[:1] != ["(preamble)"]:
                fails.append("preamble span missing: %r" % titles)
            if "Decision RT1 (locked 2026-05-23)" not in titles:
                fails.append("H2 span missing")
            if "Nested detail" not in titles:
                fails.append("nested H3 span missing")
            if "not a heading inside a fence" in titles:
                fails.append("fenced pseudo-heading parsed as span")
            if "still inside the backtick fence (a tilde run must not close it)" in titles:
                fails.append("a ~~~ line closed a ``` fence (fence tracker must match delimiter)")
            rt1 = next(s for s in e["spans"] if s["title"].startswith("Decision RT1"))
            if not rt1["mode_candidate"].startswith("dedup"):
                fails.append("RT1 span should be dedup-candidate (cites an existing raw event)")
            if not any(d["title"] == "Nested detail" for d in rt1["descendant_headings"]):
                fails.append("descendant heading not captured in the H2 span")
            if not any(a["id"] == "nested-id" for a in rt1["explicit_anchors"]):
                fails.append("explicit HTML anchor not captured")
            pre = e["spans"][0]
            if not any(a["id"] == "pre-anchor" for a in pre["explicit_anchors"]):
                fails.append("preamble explicit anchor not captured")
            if not rt1["inbound_citations"]:
                fails.append("inbound citation into RT1 span not found via registered universe")
            else:
                c0 = rt1["inbound_citations"][0]
                if [14, 15] not in c0["ranges"]:
                    fails.append("reversed range 15-14 not normalized to [14,15] via check-split's parser: %r" % c0)
            # own-bytes accounting: parent own + child bytes == parent bytes; no double count
            nested = next(s for s in e["spans"] if s["title"] == "Nested detail")
            if rt1["own_bytes"] + nested["bytes"] != rt1["bytes"]:
                fails.append("own_bytes accounting wrong: parent own %d + child %d != parent %d"
                             % (rt1["own_bytes"], nested["bytes"], rt1["bytes"]))
            titles_in_plan = [p["title"] for p in e["largest_first_plan"]]
            if len(titles_in_plan) != len(set(titles_in_plan)):
                fails.append("plan retires a span twice")
            cur = next(s for s in e["spans"] if s["title"] == "Current state")
            if cur["mode_candidate"] != "cold-relocate":
                fails.append("span without raw refs should be cold-relocate")
            if not e["over_cap"] or not e["cap_reachable"]:
                fails.append("fixture should be over cap and reachable (got over=%s reach=%s)" % (e["over_cap"], e["cap_reachable"]))
            if not rt1["cold_object"].endswith(rt1["sha256"] + ".md"):
                fails.append("cold identity must be the full span sha256")
            # CRLF invariance: same manifest bytes/sha on a CRLF copy
            with open(vp, "rb") as fh:
                data = fh.read()
            with open(vp, "wb") as fh:
                fh.write(data.replace(b"\n", b"\r\n"))
            m2, _ = build_manifest(td)
            r2 = next(s for s in m2["entries"][0]["spans"] if s["title"].startswith("Decision RT1"))
            if (r2["sha256"], r2["bytes"]) != (rt1["sha256"], rt1["bytes"]):
                fails.append("CRLF checkout changed a span's sha/bytes (checkout-invariance broken)")
            # write-nothing: no new files under the root except our fixtures
            made = sorted(os.path.relpath(os.path.join(dp, f), td) for dp, _, fs in os.walk(td) for f in fs)
            if any(x.startswith("wiki" + os.sep + "cold") for x in made):
                fails.append("dry-run wrote into wiki/cold")
        # INCONCLUSIVE path 1: a registered artifact that is missing from disk -> no manifest
        os.remove(citer)
        m3, note3 = build_manifest(td)
        if m3 is not None or "INCONCLUSIVE" not in note3:
            fails.append("missing registered artifact did not make the manifest inconclusive: %r" % note3)
        # INCONCLUSIVE path 2: corrupt registration chain -> no manifest
        with open(os.path.join(td, "receipts", "registrations", "2.json"), "w") as fh:
            fh.write("{not json")
        m4, note4 = build_manifest(td)
        if m4 is not None or "INCONCLUSIVE" not in note4:
            fails.append("corrupt chain did not make the manifest inconclusive: %r" % note4)
        # write-nothing by construction: --out inside the repo root is refused
        rc = main(["--root", td, "--out", os.path.join(td, "manifest.json")])
        if rc != 2 or os.path.exists(os.path.join(td, "manifest.json")):
            fails.append("--out inside the repo root was not refused (rc=%s)" % rc)
        # canonical-path bypass attempts: case alias and a dot-segment alias must also refuse
        alias_case = os.path.join(td.swapcase() if os.name == "nt" else td, "m2.json")
        alias_dots = os.path.join(td, "wiki", "..", "m3.json")
        for alias in (alias_case, alias_dots):
            rc = main(["--root", td, "--out", alias])
            if rc != 2:
                fails.append("aliased in-repo --out was not refused: %s (rc=%s)" % (alias, rc))
        # fence recognition must equal check-split's (incl. its leniency on indented
        # markers) so both tools see one anchor set; pinned as PARITY, not as CommonMark
        with open(vp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("---\nt: x\n---\n## H\n    ```\n## parity-heading\n    ```\n")
        text = open(vp, encoding="utf-8").read()
        ours = {s["title"] for s in parse_spans(text)}
        theirs = set(_split.heading_slugs(text)) if hasattr(_split, "heading_slugs") else None
        if theirs is not None and (("parity-heading" in theirs) != ("parity-heading" in ours)):
            fails.append("fence parity with check-split broken: ours=%r theirs=%r" % (ours, theirs))
        # wiki enumeration error -> refusal, never a partial universe
        def _bad_walk(top, onerror=None):
            onerror(OSError("simulated inaccessible subtree"))
            return iter(())
        try:
            citation_universe(td, walk=_bad_walk)
            fails.append("inaccessible wiki subtree did not refuse")
        except RuntimeError:
            pass
        # an explicit anchor inside a fence is not an anchor
        with open(vp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("---\nt: x\n---\n## H\n```\n{#not-an-anchor}\n```\n{#real-anchor}\n")
        sp = parse_spans(open(vp, encoding="utf-8").read())
        ids = [a["id"] for s in sp for a in s["explicit_anchors"]]
        if "not-an-anchor" in ids or "real-anchor" not in ids:
            fails.append("explicit-anchor fence filtering wrong: %r" % ids)
    n_checks = 25
    if fails:
        for f in fails:
            print("FAIL: " + f)
        print("retire-manifest self-test: %d/%d FAILED" % (len(fails), n_checks))
        return 1
    print("retire-manifest self-test: PASS (%d/%d)" % (n_checks, n_checks))
    return 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    root = "."
    out = md = None
    views = []
    all_views = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--root":
            root = argv[i + 1]; i += 2; continue
        if a == "--out":
            out = argv[i + 1]; i += 2; continue
        if a == "--md":
            md = argv[i + 1]; i += 2; continue
        if a == "--all":
            all_views = True; i += 1; continue
        views.append(a); i += 1
    # write-nothing, by construction: output paths INSIDE the repo root are refused
    # canonical containment: realpath (junctions/symlinks resolved) + normcase (Windows
    # case/separator aliases) -- a lexical startswith on abspath is bypassable
    # (cross-vendor round-2 catch)
    root_canon = os.path.normcase(os.path.realpath(root))
    for label, op in (("--out", out), ("--md", md)):
        if not op:
            continue
        op_canon = os.path.normcase(os.path.realpath(op))
        if op_canon == root_canon or op_canon.startswith(root_canon + os.sep):
            print("retire-manifest: REFUSED -- %s path %s is inside the repo root; this tool writes "
                  "nothing into the repo (name a path outside it)" % (label, op))
            return 2
    try:
        m, note = build_manifest(root, views or None, all_views)
    except UniverseUnreadable as e:
        m, note = None, "INCONCLUSIVE: %s" % e
    if m is None:
        print("retire-manifest: " + note)
        return 2
    js = json.dumps(m, indent=1, ensure_ascii=False)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(js)
    else:
        print(js)
    if md:
        with open(md, "w", encoding="utf-8") as fh:
            fh.write(markdown_summary(m))
    print("retire-manifest: %d view(s) scanned, %d over cap, %d cap-reachable, %d retirements needed, %d legacy citations (READ-ONLY; wrote nothing into the repo)"
          % (m["views_scanned"], m["views_over_cap"], m["views_cap_reachable"], m["total_retirements_needed"], len(m["legacy_citation_registry_seed"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
