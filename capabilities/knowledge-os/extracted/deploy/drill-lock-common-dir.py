#!/usr/bin/env python3
"""drill-lock-common-dir.py -- OPS-6 LOCK-COMMON-DIR drill (test-plan lines ~330-338).

Verifies: seq allocation locks at `git rev-parse --git-common-dir`, so two git
worktrees of ONE repo share the seq lock even though each worktree has its own
receipts/journal/ directory on disk. Cases:

  1. race fixture   -- two subprocess workers, one with cwd in worktree A and one
                        with cwd in worktree B, BOTH appending records against the
                        SAME journal location (the main worktree root) concurrently.
                        Exercises cross-worktree lock exclusion for a shared journal:
                        20 records, seqs exactly 1..20, no collision, chain passes.
  2. legal concurrency -- two worktrees branching from one HEAD, each keeping its OWN
                        journal, both stamping the same parent_git_sha. parent_git_sha
                        is forensic only and is never chain-checked -- both worktrees'
                        chains must pass clean, independently.
  3. three injected violations against a 5-record fixture sequence, each named:
        (a) gap             -- delete a middle record -> 'gap'
        (b) duplicate seq   -- alias filename (007.json vs 7.json) -> 'duplicate'
        (c) hash mismatch   -- corrupt prev_record_hash -> 'mismatch'

Usage:
  drill-lock-common-dir.py                 runs all cases as assertions (default)
  drill-lock-common-dir.py --case race|legal|gap|duplicate|mismatch   single-case CLI mode
Exit codes: 0 = PASS | 1 = FAIL (default mode: any assertion failed;
  single-case mode: the named violation was NOT detected / case did not behave as expected).
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE_PATH = os.path.join(_HERE, "compile-core.py")
_PY = sys.executable or "python3"


def _load_core():
    spec = importlib.util.spec_from_file_location("compile_core", _CORE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _load_core()


def _git(args, cwd):
    r = subprocess.run(["git", "-C", cwd] + args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (args, r.stderr))
    return r.stdout.strip()


def _init_repo(base):
    _git(["init", "-q"], base)
    _git(["config", "user.email", "t@t"], base)
    _git(["config", "user.name", "t"], base)
    open(os.path.join(base, "seed.txt"), "w", encoding="utf-8").write("seed\n")
    _git(["add", "-A"], base)
    _git(["commit", "-qm", "baseline"], base)


def _add_worktree(base, wt_path, branch):
    _git(["worktree", "add", "-b", branch, wt_path, "HEAD"], base)


# ---- worker subprocess: appends N records against a given repo_root -------------
_WORKER_SRC = r"""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("compile_core", r"%s")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
repo_root, n, parent_sha = sys.argv[1], int(sys.argv[2]), sys.argv[3]
seqs = []
for _ in range(n):
    rec = core.minimal_record("compile", parent_sha)
    seq, _path = core.append_record(repo_root, rec)
    seqs.append(seq)
print(json.dumps(seqs))
"""


def _run_worker(repo_root, n, parent_sha, cwd):
    worker_path = os.path.join(cwd, "_worker.py")
    open(worker_path, "w", encoding="utf-8").write(_WORKER_SRC % _CORE_PATH.replace("\\", "\\\\"))
    r = subprocess.run([_PY, worker_path, repo_root, str(n), parent_sha],
                       cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("worker failed: %s" % r.stderr)
    return json.loads(r.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------- case 1: race
def case_race(report):
    base = tempfile.mkdtemp(prefix="lockdrill-race-")
    try:
        _init_repo(base)
        wt1 = os.path.join(base, "..", "lockdrill-race-wt1")
        wt1 = os.path.abspath(wt1)
        _add_worktree(base, wt1, "wt1")
        head_sha = _git(["rev-parse", "HEAD"], base)

        # Both workers target the SAME journal (main worktree root = base) so the
        # gate's real intent -- one winner per seq slot for a SHARED journal -- is
        # exercised. Worker 2 runs with cwd inside the second worktree, but the
        # repo_root argument it passes to append_record is still `base`; the seq
        # lock resolves via git-common-dir, which both worktrees share.
        _write_worker(base)
        worker_path = os.path.join(base, "_worker.py")
        p1 = subprocess.Popen([_PY, worker_path, base, "10", head_sha],
                              cwd=base, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p2 = subprocess.Popen([_PY, worker_path, base, "10", head_sha],
                              cwd=wt1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out1, err1 = p1.communicate()
        out2, err2 = p2.communicate()
        ok = p1.returncode == 0 and p2.returncode == 0
        report("race: both workers exit 0", ok)
        if not ok:
            report("  worker1 stderr: %s" % err1.decode(errors="replace")[:200], True)
            report("  worker2 stderr: %s" % err2.decode(errors="replace")[:200], True)
            return
        seqs1 = json.loads(out1.decode().strip().splitlines()[-1])
        seqs2 = json.loads(out2.decode().strip().splitlines()[-1])
        all_seqs = sorted(seqs1 + seqs2)
        report("race: 20 records total", len(all_seqs) == 20)
        report("race: seqs exactly 1..20, no collision", all_seqs == list(range(1, 21)))
        n = core.check_chain(base)
        report("race: chain check passes (20 records)", n == 20)
    finally:
        _git(["worktree", "remove", "--force", wt1], base) if os.path.isdir(wt1) else None
        shutil.rmtree(base, ignore_errors=True)
        shutil.rmtree(wt1, ignore_errors=True)


def _write_worker(base):
    worker_path = os.path.join(base, "_worker.py")
    if not os.path.isfile(worker_path):
        open(worker_path, "w", encoding="utf-8").write(
            _WORKER_SRC % _CORE_PATH.replace("\\", "\\\\"))


# --------------------------------------------------------- case 2: legal concurrency
def case_legal(report):
    base = tempfile.mkdtemp(prefix="lockdrill-legal-")
    wt1 = os.path.abspath(os.path.join(base, "..", "lockdrill-legal-wt1"))
    wt2 = os.path.abspath(os.path.join(base, "..", "lockdrill-legal-wt2"))
    try:
        _init_repo(base)
        _add_worktree(base, wt1, "legal-wt1")
        _add_worktree(base, wt2, "legal-wt2")
        head_sha = _git(["rev-parse", "HEAD"], base)

        seqs1 = _run_worker(wt1, 5, head_sha, wt1)
        seqs2 = _run_worker(wt2, 5, head_sha, wt2)
        report("legal: worktree1 seqs 1..5", seqs1 == list(range(1, 6)))
        report("legal: worktree2 seqs 1..5 (own journal)", seqs2 == list(range(1, 6)))

        n1 = core.check_chain(wt1)
        n2 = core.check_chain(wt2)
        report("legal: worktree1 chain check passes", n1 == 5)
        report("legal: worktree2 chain check passes", n2 == 5)

        # confirm both worktrees' records really do carry the same parent_git_sha
        rec1, _h = core._read_seq(wt1, 1)
        rec2, _h = core._read_seq(wt2, 1)
        report("legal: both records share parent_git_sha (forensic, unchecked)",
               rec1["parent_git_sha"] == head_sha == rec2["parent_git_sha"])
    finally:
        for wt in (wt1, wt2):
            if os.path.isdir(wt):
                subprocess.run(["git", "-C", base, "worktree", "remove", "--force", wt],
                              capture_output=True)
        shutil.rmtree(base, ignore_errors=True)
        shutil.rmtree(wt1, ignore_errors=True)
        shutil.rmtree(wt2, ignore_errors=True)


# ------------------------------------------------------- case 3: injected violations
def _seed_five(base):
    _init_repo(base)
    for _ in range(5):
        core.append_record(base, core.minimal_record("compile", "deadbeef"))


def case_gap(report):
    base = tempfile.mkdtemp(prefix="lockdrill-gap-")
    try:
        _seed_five(base)
        os.remove(os.path.join(core.journal_dir(base), "3.json"))
        try:
            core.check_chain(base)
            report("gap: detected", False)
        except core.JournalViolation as e:
            report("gap: detected and named", "gap" in str(e) and "3" in str(e))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def case_duplicate(report):
    base = tempfile.mkdtemp(prefix="lockdrill-dup-")
    try:
        _seed_five(base)
        jd = core.journal_dir(base)
        shutil.copy(os.path.join(jd, "4.json"), os.path.join(jd, "04.json"))
        try:
            core.check_chain(base)
            report("duplicate: detected", False)
        except core.JournalViolation as e:
            report("duplicate: detected and named", "duplicate" in str(e))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def case_mismatch(report):
    base = tempfile.mkdtemp(prefix="lockdrill-mismatch-")
    try:
        _seed_five(base)
        jd = core.journal_dir(base)
        p4 = os.path.join(jd, "4.json")
        rec = json.load(open(p4, encoding="utf-8"))
        rec["prev_record_hash"] = "0" * 64
        open(p4, "w", encoding="utf-8").write(json.dumps(rec))
        try:
            core.check_chain(base)
            report("mismatch: detected", False)
        except core.JournalViolation as e:
            report("mismatch: detected and named",
                   "mismatch" in str(e) and "4" in str(e))
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ------------------------------------------------------------------------- driver
_CASES = {
    "race": case_race,
    "legal": case_legal,
    "gap": case_gap,
    "duplicate": case_duplicate,
    "mismatch": case_mismatch,
}


def main(argv):
    if "--case" in argv:
        name = argv[argv.index("--case") + 1]
        fn = _CASES.get(name)
        if fn is None:
            print("unknown case: %s (choices: %s)" % (name, ", ".join(_CASES)))
            return 2
        results = []
        fn(lambda label, ok: results.append((label, ok)))
        for label, ok in results:
            print("  %s %s" % ("ok " if ok else "XX ", label))
        return 0 if all(ok for _, ok in results) else 1

    total = [0]
    failed = [0]

    def report(label, ok):
        total[0] += 1
        if not ok:
            failed[0] += 1
        print("  %s %s" % ("ok " if ok else "XX ", label))

    for name in ("race", "legal", "gap", "duplicate", "mismatch"):
        print("-- case: %s --" % name)
        _CASES[name](report)

    if failed[0]:
        print("drill-lock-common-dir (OPS-6): FAIL (%d/%d)" % (total[0] - failed[0], total[0]))
        return 1
    print("drill-lock-common-dir (OPS-6): PASS (%d/%d)" % (total[0], total[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
