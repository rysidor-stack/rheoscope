#!/usr/bin/env python3
"""drill-crash-absorb.py -- OPS-1 CRASH-ORDERING DRILL (test-plan tp:270-278), throwaway repo.

Property under test (write-ordering contract): an event E counts consumed by view V
IFF a parseable RUN JOURNAL record (receipts/journal/<seq>.json) records (V,E).
Content writes happen BEFORE the journal record; the record is written exactly once,
at end of run, landing in the SAME commit as the views it describes.

The LLM agent can't be killed deterministically, so this drill replays the mechanical
write sequence with an injectable crash point:

  after-content  view file written, crash before append_record -> assert the local
                 re-scan lists (V,E) PENDING (no record exists) and the half-written
                 view content is still on disk (re-absorb-safe: the next run can
                 re-merge into it; no data is lost by the crash).
  after-journal  content + journal record written, crash before commit -> assert the
                 re-scan now counts (V,E) CONSUMED (a parseable record exists) but
                 `git status` shows both the view and the journal record uncommitted;
                 assert the recovery invariant: the NEXT run's commit step
                 (stage_only_commit) can commit both together, and the resulting
                 commit-set == {view, journal record} exactly.
  after-commit   full happy path -> assert the commit contains exactly view+journal,
                 the re-scan shows CONSUMED, and a second run of the re-scan is
                 idempotent (identical result).

Inverse-ordering check (structural exclusion): the drill's absorb helper takes the
content-write as a callable executed strictly BEFORE append_record -- there is no
code path that calls append_record first. As the detection half of that guarantee,
a separate check function `find_orphan_records` flags any journal record naming a
view V that is NOT present on disk (the append_record-before-content violation
signature) -- run here against a good tree (expect none) and against a deliberately
forged orphan record (expect it flagged).

The local re-scan is intentionally NOT staleness.py -- it is the minimal function the
gate text specifies: (V,E) consumed iff present in a parseable journal record.

Usage:
  drill-crash-absorb.py                      # all three crash points + inverse check
  drill-crash-absorb.py --crash-point after-content
  drill-crash-absorb.py --crash-point after-journal
  drill-crash-absorb.py --crash-point after-commit
Exit codes: 0 = PASS (all requested cases) | 1 = FAIL.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _load_module(basename, alias):
    path = os.path.join(_HERE, basename)
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cc = _load_module("compile-core.py", "compile_core")

CRASH_POINTS = ("after-content", "after-journal", "after-commit")


# ------------------------------------------------------------------ minimal re-scan
def rescan_status(repo_root, view, event):
    """(V,E) consumed iff a parseable journal record names it in `absorbed`. This is
    the gate-text-minimal local function, deliberately independent of staleness.py."""
    jd = cc.journal_dir(repo_root)
    if not os.path.isdir(jd):
        return "PENDING"
    for f in sorted(os.listdir(jd)):
        if not f.endswith(".json"):
            continue
        try:
            rec = json.loads(open(os.path.join(jd, f), encoding="utf-8").read())
        except (ValueError, OSError):
            continue   # unparseable receipt does not count
        for a in rec.get("absorbed", []):
            if a.get("view") == view and event in a.get("events", []):
                return "CONSUMED"
    return "PENDING"


def find_orphan_records(repo_root):
    """Detection half of the inverse-ordering guarantee: flag any journal record whose
    `absorbed[].view` names a file NOT present on disk (the append-before-content
    violation signature). Returns a list of (seq, view) violations."""
    jd = cc.journal_dir(repo_root)
    violations = []
    if not os.path.isdir(jd):
        return violations
    for f in sorted(os.listdir(jd)):
        if not f.endswith(".json"):
            continue
        try:
            rec = json.loads(open(os.path.join(jd, f), encoding="utf-8").read())
        except (ValueError, OSError):
            continue
        for a in rec.get("absorbed", []):
            v = a.get("view")
            if v and not os.path.isfile(os.path.join(repo_root, v)):
                violations.append((rec.get("seq"), v))
    return violations


# ------------------------------------------------------------------ fixture setup
def _sh(args, cwd):
    return subprocess.run(["git", "-C", cwd] + args, capture_output=True, text=True)


def make_repo():
    base = tempfile.mkdtemp(prefix="crash-absorb-")
    _sh(["init", "-q"], base)
    _sh(["config", "user.email", "t@t"], base)
    _sh(["config", "user.name", "t"], base)
    wiki = os.path.join(base, "wiki")
    os.makedirs(wiki)
    view_path = os.path.join(wiki, "view-a.md")
    with open(view_path, "w", encoding="utf-8") as fh:
        fh.write("# view A\n\noriginal content\n")
    _sh(["add", "-A"], base)
    _sh(["commit", "-qm", "seed"], base)
    return base, "wiki/view-a.md"


def write_content(repo_root, rel_view, event):
    """Content write: append absorbed-event marker to the view. Executed strictly
    BEFORE any journal write in every code path below."""
    p = os.path.join(repo_root, rel_view)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write("\nabsorbed: %s\n" % event)


def write_journal(repo_root, rel_view, event, parent_sha=""):
    rec = cc.minimal_record("compile", parent_sha)
    rec["absorbed"] = [{"view": rel_view, "events": [event], "pre_blob": "x",
                        "post_blob": "y", "manifest": [rel_view], "corpus_support": []}]
    seq, path = cc.append_record(repo_root, rec)
    return seq, path


# ------------------------------------------------------------------ cases
def case_after_content(report):
    ok = True
    base, rel_view = make_repo()
    try:
        event = "E1"
        write_content(base, rel_view, event)
        # CRASH -- no journal write.
        status = rescan_status(base, rel_view, event)
        ok &= report("after-content: rescan lists E PENDING", status == "PENDING")
        content_on_disk = open(os.path.join(base, rel_view), encoding="utf-8").read()
        ok &= report("after-content: half-written view content still on disk",
                     "absorbed: %s" % event in content_on_disk)
        dirty = _sh(["status", "--porcelain"], base).stdout
        ok &= report("after-content: view is dirty/uncommitted (re-absorb-safe)",
                     rel_view.replace("/", os.sep) in dirty or rel_view in dirty)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return ok


def case_after_journal(report):
    ok = True
    base, rel_view = make_repo()
    try:
        event = "E1"
        write_content(base, rel_view, event)
        seq, jpath = write_journal(base, rel_view, event)
        # CRASH -- no commit.
        status = rescan_status(base, rel_view, event)
        ok &= report("after-journal: rescan counts (V,E) CONSUMED", status == "CONSUMED")
        dirty = _sh(["status", "--porcelain"], base).stdout
        rel_j = os.path.relpath(jpath, base).replace(os.sep, "/")
        ok &= report("after-journal: view uncommitted in git status",
                     "view-a.md" in dirty)
        ok &= report("after-journal: journal record uncommitted in git status",
                     "receipts" in dirty)
        # recovery: the NEXT run's commit step commits both together.
        sha = cc.stage_only_commit(base, [rel_view, rel_j], "recovery commit")
        shown = _sh(["show", "--name-only", "--format=", sha], base).stdout.split()
        ok &= report("after-journal: recovery commit-set == {view, journal record}",
                     sorted(shown) == sorted([rel_view, rel_j]))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return ok


def case_after_commit(report):
    ok = True
    base, rel_view = make_repo()
    try:
        event = "E1"
        write_content(base, rel_view, event)
        seq, jpath = write_journal(base, rel_view, event)
        rel_j = os.path.relpath(jpath, base).replace(os.sep, "/")
        sha = cc.stage_only_commit(base, [rel_view, rel_j], "happy path commit")
        shown = _sh(["show", "--name-only", "--format=", sha], base).stdout.split()
        ok &= report("after-commit: commit contains exactly view+journal",
                     sorted(shown) == sorted([rel_view, rel_j]))
        status1 = rescan_status(base, rel_view, event)
        ok &= report("after-commit: rescan shows CONSUMED", status1 == "CONSUMED")
        status2 = rescan_status(base, rel_view, event)
        ok &= report("after-commit: rescan is idempotent (2nd run identical)",
                     status1 == status2)
        dirty = _sh(["status", "--porcelain"], base).stdout
        ok &= report("after-commit: tree clean post-commit", dirty.strip() == "")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return ok


def case_inverse_ordering(report):
    """Structural exclusion + detection: append_record-before-content signature."""
    ok = True
    base, rel_view = make_repo()
    try:
        event = "E1"
        write_content(base, rel_view, event)
        write_journal(base, rel_view, event)
        clean_violations = find_orphan_records(base)
        ok &= report("inverse: good tree (content before journal) has zero orphans",
                     clean_violations == [])

        # Forge the violation: a record naming a view that does NOT exist on disk.
        rec = cc.minimal_record("compile")
        rec["absorbed"] = [{"view": "wiki/does-not-exist.md", "events": ["E2"],
                            "pre_blob": "x", "post_blob": "y",
                            "manifest": ["wiki/does-not-exist.md"], "corpus_support": []}]
        seq2, _p = cc.append_record(base, rec)
        forged_violations = find_orphan_records(base)
        ok &= report("inverse: forged orphan record (append-before-content) is flagged",
                     any(v == "wiki/does-not-exist.md" for _s, v in forged_violations))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return ok


CASES = {
    "after-content": case_after_content,
    "after-journal": case_after_journal,
    "after-commit": case_after_commit,
}


def main(argv):
    requested = None
    if "--crash-point" in argv:
        i = argv.index("--crash-point")
        if i + 1 >= len(argv) or argv[i + 1] not in CRASH_POINTS:
            print("--crash-point requires one of: %s" % ", ".join(CRASH_POINTS))
            return 1
        requested = argv[i + 1]

    total = failed = 0

    def report(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1
        return ok

    if requested:
        CASES[requested](report)
    else:
        for cp in CRASH_POINTS:
            CASES[cp](report)
        case_inverse_ordering(report)

    if failed:
        print("drill-crash-absorb (OPS-1): FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("drill-crash-absorb (OPS-1): PASS (%d/%d)" % (total, total))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
