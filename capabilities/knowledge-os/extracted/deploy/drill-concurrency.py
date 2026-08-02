#!/usr/bin/env python3
"""drill-concurrency.py -- OPS-2 CONCURRENCY DRILL (test-plan tp:280-288).

Three behaviors against the real compile-core.py lockfile primitives (acquire_lock /
release_lock / LockHeld / pid_alive), on a throwaway git repo (never the live tree):

  1. LIVE HOLD    run 1 = a real stub sleeper process (`python -c "import time;
                  time.sleep(60)"`) whose pid is written into the lock. run 2's
                  acquire_lock refuses: raises LockHeld, distinct CLI exit code 3
                  (vs crash=1), message names holder pid/host/start, and ZERO writes
                  happened (repo tree file-set + mtimes snapshotted before/after,
                  asserted unchanged). Sleeper is killed afterwards.
  2. STALE LOCK   lock pid is a real-but-dead pid (spawn a short-lived process, wait
                  for exit, reuse its pid -- never a hardcoded magic number). Plain
                  acquire_lock refuses with a STALE-marked message. acquire_lock
                  (break_stale=True) succeeds and returns the broken lock's content,
                  asserted equal to what was planted (the receipt-notes logging hook).
  3. LIVE NEVER   with the sleeper's live lock in place, acquire_lock(break_stale=True)
     AUTO-BROKEN  still refuses; only force=True overrides (asserted it does). Output
                  notes that orchestrator skills never pass --force.

Usage: drill-concurrency.py
Exit: 0 = PASS (3/3) | 1 = FAIL
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_PY = sys.executable or "python3"

_spec = importlib.util.spec_from_file_location(
    "compile_core", os.path.join(_HERE, "compile-core.py"))
compile_core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compile_core)


def _init_repo(base):
    subprocess.run(["git", "-C", base, "init", "-q"], capture_output=True)
    subprocess.run(["git", "-C", base, "config", "user.email", "t@t"],
                    capture_output=True)
    subprocess.run(["git", "-C", base, "config", "user.name", "t"],
                    capture_output=True)
    with open(os.path.join(base, "seed.txt"), "w", encoding="utf-8") as fh:
        fh.write("seed\n")
    subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", base, "commit", "-qm", "seed"],
                    capture_output=True)


def _tree_snapshot(base):
    """file-set + mtimes, excluding .git internals (those churn on read too)."""
    snap = {}
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            try:
                snap[os.path.relpath(fp, base)] = os.stat(fp).st_mtime_ns
            except OSError:
                pass
    return snap


def _spawn_sleeper(seconds=60):
    return subprocess.Popen([_PY, "-c", "import time; time.sleep(%d)" % seconds],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _write_lock(base, pid, run_type="live"):
    lp = compile_core.lock_path(base)
    os.makedirs(os.path.dirname(lp), exist_ok=True)
    content = {"pid": pid, "hostname": "drillhost",
               "started_iso": "2026-01-01T00:00:00", "run_type": run_type}
    with open(lp, "w", encoding="utf-8") as fh:
        json.dump(content, fh)
    return lp, content


def main():
    total = failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    base = tempfile.mkdtemp(prefix="drill-concurrency-")
    sleeper = None
    try:
        _init_repo(base)

        # ---------------------------------------------------------- case 1: live hold
        sleeper = _spawn_sleeper(60)
        _write_lock(base, sleeper.pid, run_type="compile-run1")

        before = _tree_snapshot(base)
        raised = None
        try:
            compile_core.acquire_lock(base, "compile-run2")
        except compile_core.LockHeld as e:
            raised = e
        after = _tree_snapshot(base)

        case("case1: run2 acquire raises LockHeld while holder alive",
             raised is not None)
        msg = str(raised) if raised else ""
        case("case1: message names holder pid/host/start",
             raised is not None and str(sleeper.pid) in msg
             and "drillhost" in msg and "2026-01-01T00:00:00" in msg)
        case("case1: zero writes performed by refused run2 (tree unchanged)",
             before == after)

        # CLI-level exit code check (distinct 3 vs crash=1)
        cli_proc = subprocess.run(
            [_PY, os.path.join(_HERE, "compile-core.py"), "--acquire-lock-cli",
             base, "compile-run2-cli"],
            capture_output=True, text=True)
        case("case1: CLI exit code 3 on lock-held refusal (distinct from crash=1)",
             cli_proc.returncode == compile_core.EXIT_LOCK_HELD
             and compile_core.EXIT_LOCK_HELD != 1)

        sleeper.kill()
        sleeper.wait(timeout=10)
        sleeper = None
        compile_core.release_lock(base)

        # ---------------------------------------------------------- case 2: stale lock
        dead = subprocess.Popen([_PY, "-c", "pass"])
        dead_pid = dead.pid
        dead.wait(timeout=10)
        # be sure the pid really reads as dead now
        deadline = time.time() + 5
        while compile_core.pid_alive(dead_pid) and time.time() < deadline:
            time.sleep(0.1)

        lp, planted = _write_lock(base, dead_pid, run_type="crashed-run")
        stale_msg = None
        try:
            compile_core.acquire_lock(base, "compile-run3")
            case("case2: plain acquire refuses on stale lock", False)
        except compile_core.LockHeld as e:
            stale_msg = str(e)
            case("case2: plain acquire refuses on stale lock", True)
        case("case2: refusal message is STALE-marked",
             stale_msg is not None and "STALE" in stale_msg)

        _lp2, broken = compile_core.acquire_lock(base, "compile-run3", break_stale=True)
        case("case2: break_stale=True succeeds",
             broken is not None)
        case("case2: broken lock content matches planted lock (receipt-notes hook)",
             broken is not None
             and broken.get("pid") == planted["pid"]
             and broken.get("hostname") == planted["hostname"]
             and broken.get("started_iso") == planted["started_iso"]
             and broken.get("run_type") == planted["run_type"])
        compile_core.release_lock(base)

        # ---------------------------------------------------------- case 3: live never auto-broken
        sleeper = _spawn_sleeper(60)
        _write_lock(base, sleeper.pid, run_type="live-run4")

        try:
            compile_core.acquire_lock(base, "compile-run5", break_stale=True)
            case("case3: break_stale=True does NOT auto-break a live-PID lock", False)
        except compile_core.LockHeld:
            case("case3: break_stale=True does NOT auto-break a live-PID lock", True)

        _lp3, broken3 = compile_core.acquire_lock(base, "compile-run5", force=True)
        case("case3: force=True overrides a live lock",
             broken3 is not None and broken3.get("pid") == sleeper.pid)
        print("  note: orchestrator skills never pass --force / force=True "
              "(spec contract, not asserted by drill mechanics)")
        compile_core.release_lock(base)

        sleeper.kill()
        sleeper.wait(timeout=10)
        sleeper = None

    finally:
        if sleeper is not None:
            try:
                sleeper.kill()
                sleeper.wait(timeout=10)
            except Exception:
                pass
        import shutil
        shutil.rmtree(base, ignore_errors=True)

    if failed:
        print("drill-concurrency (OPS-2): FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("drill-concurrency (OPS-2): PASS (%d/%d)" % (total, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
