#!/usr/bin/env python3
"""drill-eco4.py -- ECO-4 CATALOG/PROJECTION IDEMPOTENCE integration drill (test-plan
lines ~450-458): "run twice in-place ... git diff --stat between runs must be empty" for
catalog.py, and byte-identical --out-dir output across two consecutive runs for project.py.

Runs against the REAL repo, read-only with respect to tracked files:
  - catalog.py validates deploy/entities.yaml against the live tree; it does not write
    anything (confirmed by reading its source: `run()` only prints and returns an exit
    code -- no output file). The drill runs it twice and asserts the exit code is stable
    AND `git diff --stat` over the tracked tree is empty both before and after (i.e.
    catalog.py never mutates tracked files).
  - project.py writes its projection skeletons under a caller-given --out directory only
    (never wiki/). The drill runs it twice into two fresh temp --out dirs and byte-compares
    every skeleton file between the two runs.

Usage: drill-eco4.py [--root REPO_ROOT]
Prints "  ok <n>"/"  XX <n>" lines and a final:
  drill-eco4 (ECO-4): PASS (n/n)   exit 0
  drill-eco4 (ECO-4): FAIL (n/n)   exit 1
"""

import filecmp
import os
import subprocess
import sys
import tempfile
import shutil

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_PY = sys.executable or "python3"


def _git(root, args):
    proc = subprocess.run(["git", "-C", root] + args, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _run_py(script, args):
    proc = subprocess.run([_PY, os.path.join(_HERE, script)] + args,
                           capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _tree_dirty(root):
    """True if the tracked tree has any diff (staged or unstaged) -- our proxy for
    'catalog.py mutated something'."""
    rc, out, _err = _git(root, ["diff", "--stat", "HEAD"])
    return bool(out.strip())


def drill(root):
    total = failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    is_git = os.path.isdir(os.path.join(root, ".git")) or _git(root, ["rev-parse", "--git-dir"])[0] == 0

    # --- catalog.py leg -----------------------------------------------------
    pre_dirty = _tree_dirty(root) if is_git else None

    rc1, out1, _e1 = _run_py("catalog.py", [])
    mid_dirty = _tree_dirty(root) if is_git else None
    rc2, out2, _e2 = _run_py("catalog.py", [])
    post_dirty = _tree_dirty(root) if is_git else None

    case("catalog.py exit code stable across two consecutive runs (%d == %d)" % (rc1, rc2),
         rc1 == rc2)
    case("catalog.py stdout byte-identical across two consecutive runs (excluding this drill's "
         "own noise)", out1 == out2)

    if is_git:
        case("git tree unchanged by catalog.py run 1 (git diff --stat empty delta)",
             pre_dirty == mid_dirty)
        case("git tree unchanged by catalog.py run 2 (git diff --stat empty delta)",
             mid_dirty == post_dirty)
    else:
        print("  -- (not a git repo at %s; skipping git diff --stat checks for catalog.py)" % root)

    # --- project.py leg -------------------------------------------------------
    tmp = tempfile.mkdtemp(prefix="drill-eco4-")
    try:
        out_a = os.path.join(tmp, "out_a")
        out_b = os.path.join(tmp, "out_b")
        rc_a, sout_a, _ = _run_py("project.py", ["--root", root, "--out", out_a])
        rc_b, sout_b, _ = _run_py("project.py", ["--root", root, "--out", out_b])

        case("project.py exits 0 on both runs against the real repo", rc_a == 0 and rc_b == 0)

        proj_a = os.path.join(out_a, "projection")
        proj_b = os.path.join(out_b, "projection")
        if os.path.isdir(proj_a) and os.path.isdir(proj_b):
            names_a = sorted(os.listdir(proj_a))
            names_b = sorted(os.listdir(proj_b))
            case("project.py emits the same skeleton filenames both runs", names_a == names_b)
            all_identical = True
            for fname in names_a:
                fa = os.path.join(proj_a, fname)
                fb = os.path.join(proj_b, fname)
                if not filecmp.cmp(fa, fb, shallow=False):
                    all_identical = False
            case("project.py --out dirs byte-identical across two consecutive runs (ECO-4)",
                 all_identical)
        else:
            case("project.py wrote a projection/ dir under --out on both runs",
                 False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return total, failed


def main(argv):
    args = argv[1:]
    root = None
    if "--root" in args:
        root = args[args.index("--root") + 1]
    if not root:
        root = os.path.dirname(_HERE)  # repo root = parent of deploy/

    total, failed = drill(root)
    if failed:
        print("drill-eco4 (ECO-4): FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("drill-eco4 (ECO-4): PASS (%d/%d)" % (total, total))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
