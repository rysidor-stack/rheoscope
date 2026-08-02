#!/usr/bin/env python3
"""project.py -- projection-skeleton generator (memory-engine v3, ECO-4 second half).

ECO-4 CATALOG/PROJECTION IDEMPOTENCE (test-plan lines ~450-456): "With no state change, a
second consecutive run of catalog.py and project.py produces byte-identical outputs
(CATALOG.md/json, INDEX/HEALTH/changelog skeletons, briefing skeleton). Implies: no embedded
wall-clock timestamps except sourced ones (generation time, if kept, comes from the receipt
being projected, not now()), stable sort orders, no accreting sections."

This module is the projection half: it derives four SKELETONS -- INDEX, HEALTH, changelog,
briefing -- from committed corpus state only (the wiki/ tree + receipts/ if present), and
writes them read-only-derive style under a caller-chosen --out directory (never the live
wiki -- this never overwrites wiki/INDEX.md etc.).

JUDGMENT CALL (documented per the task's ambiguity-handling instruction): the test-plan names
the four skeletons but does not specify their exact prose content -- the real wiki/INDEX.md,
wiki/HEALTH.md etc. are hand/LLM-compiled narrative documents (see /compile), not something a
sensor script should regenerate wholesale. Regenerating narrative would either (a) diverge from
the compiled richness and be useless, or (b) require re-embedding compile-time judgment in a
sensor -- both wrong. So these skeletons are kept DELIBERATELY MINIMAL and mechanical: a stable
header plus a sorted listing per skeleton, computed only from filesystem facts (paths, sizes,
sourced dates parsed out of receipt filenames/frontmatter) that are already deterministic given
committed state. Determinism is the gate ECO-4 tests; richness is /compile's job, not this
script's. If project.py is ever asked to emit fuller projections, extend the skeleton builders
below without touching the determinism contract (no now(), stable sort, no accretion).

Skeletons emitted (one file each, under --out/projection/):
  INDEX.skeleton.md      -- sorted list of wiki/<domain>/INDEX.md dirs + article counts.
  HEALTH.skeleton.md     -- sorted list of wiki articles (*.md, excluding INDEX.md) + byte size
                             (LF-normalized, per ECO-3's checkout-invariant convention) + a
                             sourced "dated" field parsed from the article's frontmatter if
                             present (never mtime -- mtime is checkout/OS variant).
  changelog.skeleton.md  -- sorted list of receipts/*.md by filename (receipt filenames already
                             embed a sortable UTC-like stamp -- sourced, not generated).
  briefing.skeleton.md   -- header + the single newest receipt filename (max by sorted name) as
                             the "as-of" pointer -- sourced from the receipt corpus, not now().

Usage:
  project.py --root REPO_ROOT --out OUT_DIR    write skeletons under OUT_DIR/projection/
  project.py --self-test                       fixture corpus, run-twice byte-identity + mutation test

Exit codes: 0 = wrote/verified projection | 1 = self-test failure | 2 = inconclusive (bad --root).
"""

import io
import os
import re
import sys
import tempfile
import shutil

try:  # cp1252 consoles + unicode corpus content: never let an encode error mask a verdict.
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Deterministic filesystem facts (no now(), no os.path.getsize -- ECO-3 convention).
# ---------------------------------------------------------------------------

def _lf_bytes(path):
    """LF-normalized byte length, per ECO-3: checkout-invariant, never os.path.getsize."""
    with open(path, "rb") as fh:
        data = fh.read()
    return len(data.replace(b"\r\n", b"\n"))


def _read_text(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return fh.read()


_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


def _sourced_date(text):
    """Pull the first sourced date out of frontmatter/prose (never mtime, never now()).
    Deterministic: first match in file-order, not "latest" (avoids an extra sort dependency
    on content semantics -- pure textual scan)."""
    m = _DATE_RE.search(text or "")
    return m.group(1) if m else ""


def _walk_wiki_dirs(root):
    """Sorted list of wiki/<domain> directories that carry an INDEX.md, each with its
    article count (*.md files in that dir, excluding INDEX.md itself, non-recursive)."""
    wiki = os.path.join(root, "wiki")
    out = []
    if not os.path.isdir(wiki):
        return out
    for name in sorted(os.listdir(wiki)):
        d = os.path.join(wiki, name)
        if not os.path.isdir(d):
            continue
        idx = os.path.join(d, "INDEX.md")
        if not os.path.isfile(idx):
            continue
        count = sum(
            1 for f in os.listdir(d)
            if f.endswith(".md") and f != "INDEX.md" and os.path.isfile(os.path.join(d, f))
        )
        out.append((name, count))
    return sorted(out, key=lambda t: t[0])


def _walk_wiki_articles(root):
    """Sorted list of all wiki article paths (relative, forward-slash, excluding INDEX.md),
    each with LF-normalized byte size + a sourced date scraped from its own text."""
    wiki = os.path.join(root, "wiki")
    out = []
    if not os.path.isdir(wiki):
        return out
    for dirpath, dirnames, filenames in os.walk(wiki):
        dirnames.sort()
        for f in sorted(filenames):
            if not f.endswith(".md") or f == "INDEX.md":
                continue
            fp = os.path.join(dirpath, f)
            rel = os.path.relpath(fp, root).replace(os.sep, "/")
            size = _lf_bytes(fp)
            try:
                date = _sourced_date(_read_text(fp))
            except (OSError, UnicodeDecodeError):
                date = ""
            out.append((rel, size, date))
    return sorted(out, key=lambda t: t[0])


def _list_receipts(root):
    """Sorted list of receipts/*.md filenames (the filename itself is the sourced stamp --
    receipts are named <UTC-stamp>-<slug>.md, already a sortable string)."""
    receipts = os.path.join(root, "receipts")
    if not os.path.isdir(receipts):
        return []
    return sorted(f for f in os.listdir(receipts) if f.endswith(".md"))


# ---------------------------------------------------------------------------
# Skeleton builders -- pure functions over the facts above, stable sort, no accretion.
# ---------------------------------------------------------------------------

def build_index_skeleton(root):
    dirs = _walk_wiki_dirs(root)
    lines = ["# INDEX (projection skeleton)", "",
             "Derived from committed wiki/ tree only. Minimal by design (see module docstring);",
             "the narrative INDEX.md is compiled by /compile, not projected here.", "",
             "| Directory | Articles |", "|-----------|----------|"]
    for name, count in dirs:
        lines.append("| %s | %d |" % (name, count))
    lines.append("")
    lines.append("Total directories: %d" % len(dirs))
    return "\n".join(lines) + "\n"


def build_health_skeleton(root):
    arts = _walk_wiki_articles(root)
    lines = ["# HEALTH (projection skeleton)", "",
             "Derived from committed wiki/ tree only (LF-normalized byte sizes per ECO-3;",
             "dated field is a sourced in-text date, never mtime/now()).", "",
             "| Article | Bytes (LF) | Dated |", "|---------|-----------:|-------|"]
    for rel, size, date in arts:
        lines.append("| %s | %d | %s |" % (rel, size, date or "-"))
    lines.append("")
    lines.append("Total articles: %d" % len(arts))
    return "\n".join(lines) + "\n"


def build_changelog_skeleton(root):
    receipts = _list_receipts(root)
    lines = ["# CHANGELOG (projection skeleton)", "",
             "Sorted receipts/*.md filenames (the filename is the sourced stamp).", "",
             "| Receipt |", "|---------|"]
    for r in receipts:
        lines.append("| %s |" % r)
    lines.append("")
    lines.append("Total receipts: %d" % len(receipts))
    return "\n".join(lines) + "\n"


def build_briefing_skeleton(root):
    receipts = _list_receipts(root)
    newest = receipts[-1] if receipts else ""
    lines = ["# SESSION-BRIEFING (projection skeleton)", "",
             "As-of pointer is the lexicographically-newest receipts/*.md filename",
             "(sourced from the receipt corpus, never now()).", "",
             "As-of receipt: %s" % (newest or "(none)"),
             "Receipt count: %d" % len(receipts)]
    return "\n".join(lines) + "\n"


_SKELETONS = {
    "INDEX.skeleton.md": build_index_skeleton,
    "HEALTH.skeleton.md": build_health_skeleton,
    "changelog.skeleton.md": build_changelog_skeleton,
    "briefing.skeleton.md": build_briefing_skeleton,
}


def project(root, out_dir):
    """Write all four skeletons under out_dir/projection/. Returns the dict of
    {filename: content} written (for byte-comparison by drill-eco4.py)."""
    proj_dir = os.path.join(out_dir, "projection")
    os.makedirs(proj_dir, exist_ok=True)
    written = {}
    for fname, builder in sorted(_SKELETONS.items()):
        content = builder(root)
        path = os.path.join(proj_dir, fname)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        written[fname] = content
    return written


# ---------------------------------------------------------------------------
# Self-test: tempdir fixture corpus, run twice, assert byte-identical; mutation changes output.
# ---------------------------------------------------------------------------

def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _build_fixture_corpus(base):
    """A tiny fake wiki + receipts tree, deterministic content."""
    _write(os.path.join(base, "wiki", "INDEX.md"), "# Wiki\n")
    _write(os.path.join(base, "wiki", "alpha", "INDEX.md"), "# Alpha\n")
    _write(os.path.join(base, "wiki", "alpha", "one.md"),
           "# One\n\nDated 2026-01-05 per receipt.\n")
    _write(os.path.join(base, "wiki", "alpha", "two.md"),
           "# Two\n\nDated 2026-02-10.\n")
    _write(os.path.join(base, "wiki", "beta", "INDEX.md"), "# Beta\n")
    _write(os.path.join(base, "wiki", "beta", "three.md"), "# Three\n\nno date here.\n")
    _write(os.path.join(base, "receipts", "2026-01-05T000000-compile.md"), "receipt 1\n")
    _write(os.path.join(base, "receipts", "2026-02-10T000000-flight-plan.md"), "receipt 2\n")


def self_test():
    total = failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    tmp = tempfile.mkdtemp(prefix="project-selftest-")
    try:
        corpus = os.path.join(tmp, "corpus")
        out1 = os.path.join(tmp, "out1")
        out2 = os.path.join(tmp, "out2")
        _build_fixture_corpus(corpus)

        w1 = project(corpus, out1)
        w2 = project(corpus, out2)

        case("same skeleton filenames both runs", sorted(w1) == sorted(w2))
        case("byte-identical content across two consecutive runs (ECO-4 core property)",
             all(w1[k] == w2[k] for k in w1))

        # on-disk byte-compare too (not just in-memory dict)
        disk_identical = True
        for fname in _SKELETONS:
            p1 = os.path.join(out1, "projection", fname)
            p2 = os.path.join(out2, "projection", fname)
            with open(p1, "rb") as a, open(p2, "rb") as b:
                if a.read() != b.read():
                    disk_identical = False
        case("on-disk files byte-identical across two consecutive runs", disk_identical)

        case("INDEX skeleton lists both fixture domains sorted",
             "alpha" in w1["INDEX.skeleton.md"] and "beta" in w1["INDEX.skeleton.md"]
             and w1["INDEX.skeleton.md"].index("alpha") < w1["INDEX.skeleton.md"].index("beta"))
        case("HEALTH skeleton picks up a sourced date, not mtime",
             "2026-01-05" in w1["HEALTH.skeleton.md"])
        case("changelog skeleton lists receipts sorted",
             w1["changelog.skeleton.md"].index("2026-01-05")
             < w1["changelog.skeleton.md"].index("2026-02-10"))
        case("briefing skeleton points at the lexicographically-newest receipt",
             "2026-02-10T000000-flight-plan.md" in w1["briefing.skeleton.md"])

        # mutation must change output (no false idempotence / no stale caching)
        _write(os.path.join(corpus, "wiki", "beta", "four.md"), "# Four\n\nDated 2026-03-01.\n")
        out3 = os.path.join(tmp, "out3")
        w3 = project(corpus, out3)
        case("a real corpus mutation changes projected output",
             any(w3[k] != w1[k] for k in w1))

        # CRLF-vs-LF source content must not change LF-normalized size accounting
        crlf_corpus = os.path.join(tmp, "corpus_crlf")
        shutil.copytree(corpus, crlf_corpus)
        alpha_one = os.path.join(crlf_corpus, "wiki", "alpha", "one.md")
        with open(alpha_one, "rb") as fh:
            data = fh.read()
        with open(alpha_one, "wb") as fh:
            fh.write(data.replace(b"\n", b"\r\n"))
        w_crlf = project(crlf_corpus, os.path.join(tmp, "out_crlf"))
        case("CRLF/LF checkout variance does not change HEALTH byte accounting",
             w_crlf["HEALTH.skeleton.md"] == w3["HEALTH.skeleton.md"])

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failed:
        print("project self-test: FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("project self-test: PASS (%d/%d)" % (total, total))
    return 0


def main(argv):
    args = argv[1:]
    if "--self-test" in args:
        return self_test()
    root = out = None
    if "--root" in args:
        root = args[args.index("--root") + 1]
    if "--out" in args:
        out = args[args.index("--out") + 1]
    if not root or not out:
        print("usage: project.py --root REPO_ROOT --out OUT_DIR")
        return 2
    if not os.path.isdir(root):
        print("RESULT: INCONCLUSIVE -- root not found: %s" % root)
        return 2
    written = project(root, out)
    for fname in sorted(written):
        print("  wrote %s" % os.path.join(out, "projection", fname))
    print("RESULT: PASS -- %d skeleton(s) projected to %s" % (len(written), os.path.join(out, "projection")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
