#!/usr/bin/env python3
"""check-knowledge-debt.py -- read-only knowledge-debt sensor (backlog v3.0-51 + v3.0-52).

Two debt classes, one sensor, zero writes:

  ARCHIVE (v3.0-51): every raw/*.md carrying `compile: false` claims its knowledge
  lives somewhere canonical. This sensor is the first thing that ever checks that
  claim. A flagged file names its home in a `canonical:` frontmatter field (a
  repo-relative path -- deliverables/, docs/adr/, docs/governance/ are all
  legitimate homes; a wiki article is too). Enforcement is DATE-GATED per the
  check-loop-state precedent (raw files are append-only; a retroactive requirement
  would break every existing instance forever):
    - file dated >= CUT_DATE and no `canonical:` field  -> FAIL
    - `canonical:` target does not exist (any file age)  -> FAIL (a dangling
      promise is a fresh defect regardless of the file's age)
    - file dated <  CUT_DATE and no `canonical:` field  -> WARN (legacy-unannotated,
      counted, grandfathered -- operator batch-annotates whenever)
    - file date unparseable                              -> WARN (never a silent pass)
  HONESTY NOTE: an existing target proves pointer-integrity, NOT content-coverage.
  The sensor cannot verify the knowledge is actually inside the named home; it
  verifies the promise points at something real. Content adjudication stays human.

  REVIEW (v3.0-52 + v3.0-53c): wiki/REVIEW.md entries carry stale-after dates and
  nothing ever escalated one past its date -- the tripwire fires, then rusts. This
  sensor reports (never FAILs on) AGING debt: entries past stale-after, plus
  entries whose stale line carries no parseable date (counted as `undated`, never
  silently ignored -- real corpora carry non-standard stale lines; legacy entries
  are grandfathered). One structural exception (v3.0-53c, date-gated like the
  archive class): an entry whose Created date is on/after CUT_DATE and whose
  stale-after is unparseable is a WRITER defect, not aging -- it would never age
  out on its own -- and FAILs.

  The sensor REPORTS, never flips flags and never edits REVIEW.md. Corpus
  adjudication is operator-gated by design (v3.0-51's guardrail).

Exit codes (the deploy sensor family's shared convention): 0 clean-or-warn / 1 FAIL(s) /
2 inconclusive (unreadable tree). Absent raw/ AND wiki/REVIEW.md -> "not
applicable" note, exit 0 (build-only projects and pre-kickoff trees are not
debtors).

CLI:
  py deploy/check-knowledge-debt.py --check            # live run from repo root
  py deploy/check-knowledge-debt.py --check --root P   # live run against tree P
  py deploy/check-knowledge-debt.py --self-test        # committed fixtures
"""

import os
import re
import sys
import datetime

CUT_DATE = datetime.date(2026, 7, 27)  # v3.0.8 ship date; files born after this must name a home
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
FIXTURES = os.path.join("deploy", "test-fixtures", "knowledge-debt")


def _parse_date(text):
    m = DATE_RE.search(text or "")
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _frontmatter(text):
    """Return the frontmatter block's lines, or None. Line-level tolerant:
    the block is whatever sits between a leading '---' line and the next
    '---' line; YAML-hostile content degrades to regex scans (house
    precedent: check-loop-state's degraded-parse fallback)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, min(len(lines), 200)):
        if lines[i].strip() == "---":
            return lines[1:i]
    return None


def _canonical_targets(fm_lines):
    """Extract canonical: value(s). Supports scalar, inline list, and block list.
    Returns a list of target strings (possibly empty = field present but valueless),
    or None if the field is absent."""
    targets = None
    for i, ln in enumerate(fm_lines):
        m = re.match(r"^canonical:\s*(.*)$", ln.strip())
        if not m:
            continue
        rest = m.group(1).strip()
        targets = []
        if rest.startswith("[") and rest.endswith("]"):
            targets = [t.strip().strip("'\"") for t in rest[1:-1].split(",") if t.strip()]
        elif rest:
            targets = [rest.strip("'\"")]
        else:
            for follow in fm_lines[i + 1:]:
                fm = re.match(r"^\s+-\s+(.+)$", follow)
                if fm:
                    targets.append(fm.group(1).strip().strip("'\""))
                elif follow.strip() == "":
                    continue
                else:
                    break
        break
    return targets


def _target_ok(root, target):
    """A canonical target must be repo-relative and resolve INSIDE the repo.
    Absolute paths (POSIX or drive-lettered) and ..-traversal escaping root are
    rejected even if the outside file exists -- the schema says repo-relative,
    and a home outside the repository is not a home the repository governs.
    (Cross-vendor catch, v3.0.8 leg round 1: os.path.join(root, abs) silently
    discards root.)"""
    if os.path.isabs(target) or re.match(r"^[A-Za-z]:", target):
        return False
    resolved = os.path.realpath(os.path.join(root, target))
    root_r = os.path.realpath(root)
    if not (resolved == root_r or resolved.startswith(root_r + os.sep)):
        return False
    return os.path.exists(resolved)


def scan_archive(root):
    """ARCHIVE class: audit every compile:false raw file."""
    stats = {"flagged": 0, "verified": 0, "legacy": 0, "dateless": 0}
    fails, warns = [], []
    raw_dir = os.path.join(root, "raw")
    if not os.path.isdir(raw_dir):
        return None, fails, warns
    for name in sorted(os.listdir(raw_dir)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(raw_dir, name)
        if not os.path.isfile(path):
            continue
        fm = _frontmatter(_read(path))
        if fm is None:
            continue  # malformed raw is wiki-schema's beat, not this sensor's
        if not any(re.match(r"^compile:\s*false\s*$", ln.strip()) for ln in fm):
            continue
        stats["flagged"] += 1
        rel = "raw/" + name
        targets = _canonical_targets(fm)
        if targets:
            dangling = [t for t in targets if not _target_ok(root, t)]
            if dangling:
                fails.append("%s: canonical target(s) missing or outside the repo: %s -- a "
                             "dangling or escaping home is a fresh defect regardless of file "
                             "age" % (rel, ", ".join(dangling)))
            else:
                stats["verified"] += 1
            continue
        # no canonical field (or field present but empty -> treated as absent)
        fdate = None
        for ln in fm:
            m = re.match(r"^date:\s*(.+)$", ln.strip())
            if m:
                fdate = _parse_date(m.group(1))
                break
        if fdate is None:
            fdate = _parse_date(name)
        if fdate is None:
            stats["dateless"] += 1
            warns.append("%s: compile:false, no canonical: field, and no parseable date "
                         "(frontmatter or filename) -- cannot date-gate; annotate it" % rel)
        elif fdate >= CUT_DATE:
            # v3.0.27 (plain-language sweep V2): say what it means and what it costs --
            # the schema-speak form sat unactioned for a week on a live instance because
            # nothing in it said what ignoring it would do. Mechanics in the bracket tail.
            fails.append("%s was archived without saying where its content now lives -- "
                         "until the one-line pointer is added, whatever that note contains "
                         "is unfindable from the wiki; a session can draft it on a yes "
                         "[compile:false dated %s >= cut %s, canonical: absent]"
                         % (rel, fdate, CUT_DATE))
        else:
            stats["legacy"] += 1
            warns.append("%s: legacy compile:false (%s, pre-cut) with no canonical: field -- "
                         "grandfathered, counted; batch-annotate when adjudicated" % (rel, fdate))
    return stats, fails, warns


def scan_review(root, today):
    """REVIEW class: report-only on AGING (v3.0-52 guardrail); FAILs only the
    structural writer defect of a NEW entry with no parseable stale-after
    (v3.0-53c, date-gated -- legacy undated entries stay WARN-counted)."""
    stats = {"entries": 0, "stale": 0, "undated": 0, "oldest_days": 0, "malformed_new": 0}
    warns, fails = [], []
    path = os.path.join(root, "wiki", "REVIEW.md")
    if not os.path.isfile(path):
        return None, warns, fails
    blocks = re.split(r"(?m)^### ", _read(path))[1:]
    stats["entries"] = len(blocks)
    for block in blocks:
        title = block.splitlines()[0].strip() if block.splitlines() else "(untitled)"
        m = re.search(r"Stale after:\**\s*([^\n]*)", block)
        sdate = _parse_date(m.group(1)) if m else None
        if sdate is None:
            stats["undated"] += 1
            cm = re.search(r"Created:\**\s*([^\n]*)", block)
            cdate = _parse_date(cm.group(1)) if cm else None
            if cdate is not None and cdate >= CUT_DATE:
                stats["malformed_new"] += 1
                fails.append("REVIEW entry created %s (>= cut %s) with no parseable stale-after "
                             "date -- it can never age out; the writer must emit one: %s"
                             % (cdate, CUT_DATE, title[:90]))
            continue
        if sdate < today:
            stats["stale"] += 1
            age = (today - sdate).days
            stats["oldest_days"] = max(stats["oldest_days"], age)
            warns.append("REVIEW stale %dd past its own date (%s): %s" % (age, sdate, title[:90]))
    legacy_undated = stats["undated"] - stats["malformed_new"]
    if legacy_undated:
        warns.append("REVIEW: %d legacy entr%s with no parseable stale-after date -- counted, not "
                     "silently skipped; they never age out on their own"
                     % (legacy_undated, "y" if legacy_undated == 1 else "ies"))
    return stats, warns, fails


def run(root, today=None, quiet=False):
    today = today or datetime.date.today()
    if not os.path.isdir(root):
        # A root we cannot see is INCONCLUSIVE, never "not applicable" --
        # permission failures must not masquerade as clean build-only trees.
        if not quiet:
            print("INCONCLUSIVE: root is not a readable directory: %s" % root)
        return 2, None
    try:
        a_stats, fails, a_warns = scan_archive(root)
        r_stats, r_warns, r_fails = scan_review(root, today)
        fails = fails + r_fails
    except OSError as e:
        if not quiet:
            print("INCONCLUSIVE: cannot read tree under %s: %s" % (root, e))
        return 2, None
    out = []
    if a_stats is None and r_stats is None:
        out.append("check-knowledge-debt: not applicable (no raw/ and no wiki/REVIEW.md) -- OK")
        if not quiet:
            print("\n".join(out))
        return 0, {"archive": a_stats, "review": r_stats}
    for f in fails:
        out.append("[FAIL] " + f)
    for w in a_warns + r_warns:
        out.append("[WARN] " + w)
    summary = []
    if a_stats is not None:
        summary.append("archive: %d compile:false -- %d canonical-verified, %d legacy-unannotated, "
                       "%d undatable" % (a_stats["flagged"], a_stats["verified"],
                                         a_stats["legacy"], a_stats["dateless"]))
    if r_stats is not None:
        summary.append("review: %d entries -- %d stale (oldest %dd), %d undated (%d malformed-new)"
                       % (r_stats["entries"], r_stats["stale"], r_stats["oldest_days"],
                          r_stats["undated"], r_stats["malformed_new"]))
    out.append("check-knowledge-debt: " + "; ".join(summary))
    out.append("RESULT: %s (%d FAIL, %d WARN) -- pointer-integrity only; content "
               "adjudication stays human" % ("FAIL" if fails else "PASS",
                                             len(fails), len(a_warns) + len(r_warns)))
    if not quiet:
        print("\n".join(out))
    return (1 if fails else 0), {"archive": a_stats, "review": r_stats}


def self_test():
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test-fixtures", "knowledge-debt")
    today = datetime.date(2026, 7, 27)  # pinned: fixture expectations are date-stable
    n = 0

    code, stats = run(os.path.join(base, "clean"), today=today, quiet=True)
    assert code == 0, "clean tree must exit 0, got %d" % code
    assert stats["archive"] == {"flagged": 1, "verified": 1, "legacy": 0, "dateless": 0}, stats
    assert stats["review"]["stale"] == 0 and stats["review"]["entries"] == 1, stats
    assert stats["review"]["malformed_new"] == 0, stats
    n += 2

    code, stats = run(os.path.join(base, "dirty"), today=today, quiet=True)
    assert code == 1, "dirty tree must exit 1, got %d" % code
    a, r = stats["archive"], stats["review"]
    assert a == {"flagged": 5, "verified": 0, "legacy": 1, "dateless": 1}, a
    assert r == {"entries": 4, "stale": 1, "undated": 2, "oldest_days": 207,
                 "malformed_new": 1}, r
    n += 2

    code, stats = run(os.path.join(base, "empty"), today=today, quiet=True)
    assert code == 0 and stats["archive"] is None and stats["review"] is None, (code, stats)
    n += 1

    code, stats = run(os.path.join(base, "no-such-tree-xyz"), today=today, quiet=True)
    assert code == 2 and stats is None, "unreadable root must be INCONCLUSIVE, got %s" % code
    n += 1

    # Existing-but-permission-denied directory: simulate the ACL case
    # deterministically (cross-vendor catch, v3.0.8 leg round 2) -- a raised
    # PermissionError mid-scan must be INCONCLUSIVE exit 2, never a crash and
    # never a clean pass.
    real_listdir = os.listdir
    def _denied(path):
        raise PermissionError(13, "access denied (simulated)", str(path))
    os.listdir = _denied
    try:
        code, stats = run(os.path.join(base, "dirty"), today=today, quiet=True)
    finally:
        os.listdir = real_listdir
    assert code == 2 and stats is None, "permission-denied scan must be INCONCLUSIVE, got %s" % code
    n += 1

    print("check-knowledge-debt --self-test: %d/%d assertions PASS" % (n, n))
    return 0


def main():
    # Windows consoles default to cp1252; real REVIEW titles carry non-Latin-1
    # characters. Degrade unprintable characters, never crash a read-only sensor.
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return self_test()
    root = os.getcwd()
    if "--root" in argv:
        root = argv[argv.index("--root") + 1]
    if "--check" in argv or "--root" in argv:
        code, _ = run(root)
        return code
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
