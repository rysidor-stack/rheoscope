#!/usr/bin/env python3
"""compile-core.py -- compile-v2 engine primitives (memory-engine v3, P2).

Three primitives the P2 gates test, built library-first so the drills (OPS-1/2/3/6)
and check-run-diff.py (ACC-4/LLM-2) exercise the REAL code paths:

  RUN LOCKFILE   single JSON lockfile ({pid, hostname, started_iso, run_type});
                 second run refuses cleanly (exit code distinct from crash, message
                 names the holder, zero writes); a stale lock (holder PID dead) is
                 breakable only via the documented --break-stale-lock (which logs the
                 broken lock's content for the new run's receipt notes); a live-PID
                 lock is NEVER auto-broken (--force exists; orchestrator skills never
                 use it). [test-plan OPS-2]

  RUN JOURNAL    receipts/journal/<seq>.json, schema-validated at write time (refuses
                 malformed output; not corruptible receipt prose). seq allocated
                 under a lock at `git rev-parse --git-common-dir` (covers all
                 worktrees). Integrity on prev_record_hash (hash of record seq-1):
                 gap, duplicate, or mismatch fails loud. parent_git_sha is forensic
                 metadata ONLY -- two concurrent worktrees from one HEAD are legal
                 and trip nothing. [spec sec.6 / F4; test-plan OPS-6]

  STAGE-ONLY     the run's commit step stages exactly the declared manifest paths via
  COMMIT         per-path `git add <file>` (never directory adds); write ordering is
                 content -> journal record -> ONE commit containing both. [OPS-1/OPS-3]

Exit codes (CLI): 0 ok | 3 lock-held refusal (distinct from crash) | 1 violation/fail
| 2 inconclusive. Library callers get exceptions: LockHeld, JournalViolation.
"""

import ctypes
import hashlib
import json
import os
import socket
import subprocess
import sys
import time

JOURNAL_DIR = os.path.join("receipts", "journal")
LOCK_BASENAME = "compile-run.lock.json"
EXIT_LOCK_HELD = 3

RECORD_REQUIRED = {
    "seq": int,
    "run_type": str,
    "absorbed": list,          # [{view, events, pre_blob, post_blob, manifest,
    #                              corpus_support}]
    "noop_candidates": list,   # [{view, event, verified: bool, artifact}]
    "pins": list,
    "registrations": dict,     # {entities, cascades, origin, event_class}
    "corrections": list,
    "prev_record_hash": (str, type(None)),  # None only for seq 1
    "parent_git_sha": str,     # forensic metadata ONLY -- never chain-checked
}
ABSORBED_REQUIRED = {"view": str, "events": list, "pre_blob": str,
                     "post_blob": str, "manifest": list,
                     "corpus_support": list}   # spec sec.7/F16: required field
#                      (empty only when no shipped-state prose changed)


class LockHeld(Exception):
    def __init__(self, holder):
        self.holder = holder
        super().__init__("run lock held by pid=%s host=%s since %s (%s)" % (
            holder.get("pid"), holder.get("hostname"),
            holder.get("started_iso"), holder.get("run_type")))


class JournalViolation(Exception):
    pass


# ------------------------------------------------------------------ pid liveness
def pid_alive(pid):
    """Windows-first liveness probe (OpenProcess), POSIX fallback (kill 0)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        # a handle can open on a zombie; check exit code STILL_ACTIVE (259)
        code = ctypes.c_ulong(0)
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return bool(ok) and code.value == 259
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ run lockfile
def lock_path(repo_root):
    return os.path.join(repo_root, ".claude", LOCK_BASENAME)


def acquire_lock(repo_root, run_type, break_stale=False, force=False,
                 _pid=None, _alive=pid_alive, _isfile=None):
    """Returns (lock_path, broken_lock_content_or_None). Raises LockHeld on a live
    lock (unless force). ZERO writes happen before the refusal decision.

    Race-window semantics (declared): if a rival creates the lock BETWEEN the
    isfile pre-check and the O_EXCL open, the collision raises LockHeld
    UNCONDITIONALLY -- force/break_stale are not reconsidered inside the race
    window. Deliberate: a race-time rival lock is freshly created by a live
    process, so refusal is the fail-safe reading; a force caller may simply
    retry and take the normal pre-check path."""
    lp = lock_path(repo_root)
    broken = None
    if (_isfile or os.path.isfile)(lp):
        try:
            holder = json.load(open(lp, encoding="utf-8"))
        except (ValueError, OSError):
            holder = {"pid": None, "hostname": "?", "started_iso": "?",
                      "run_type": "unparseable-lock"}
        alive = _alive(holder.get("pid"))
        if alive and not force:
            raise LockHeld(holder)
        if not alive and not (break_stale or force):
            raise LockHeld(dict(holder, run_type=str(holder.get("run_type"))
                                + " [STALE -- use --break-stale-lock]"))
        broken = holder    # logged into the new run's receipt notes
        os.remove(lp)
    os.makedirs(os.path.dirname(lp), exist_ok=True)
    content = {"pid": _pid if _pid is not None else os.getpid(),
               "hostname": socket.gethostname(),
               "started_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "run_type": run_type}
    try:
        fd = os.open(lp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        # TOCTOU window: a rival created the lock between the isfile check
        # and this O_EXCL open (2026-07-05 concurrency campaign, 1/20 trials).
        # The O_EXCL exclusion is the real guarantee; surface it as the
        # documented refusal, never a raw FileExistsError.
        try:
            holder = json.load(open(lp, encoding="utf-8"))
        except (ValueError, OSError):
            holder = {"pid": None, "hostname": "?", "started_iso": "?",
                      "run_type": "lock-race"}
        raise LockHeld(holder)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(content, fh)
    return lp, broken


def release_lock(repo_root):
    lp = lock_path(repo_root)
    if os.path.isfile(lp):
        os.remove(lp)


# ------------------------------------------------------------------ journal
def _record_hash(record_bytes):
    return hashlib.sha256(record_bytes).hexdigest()


def _common_dir(repo_root):
    p = subprocess.run(["git", "-C", repo_root, "rev-parse", "--git-common-dir"],
                       capture_output=True, text=True)
    d = (p.stdout or "").strip()
    if not d:
        raise JournalViolation("not a git repository: %s" % repo_root)
    return d if os.path.isabs(d) else os.path.join(repo_root, d)


class _SeqLock:
    """Cross-process mutual exclusion at the git common dir (covers all worktrees
    of one repo). O_CREAT|O_EXCL spin lock -- portable, crash-safe via staleness
    timeout (the seq lock guards milliseconds, so 30 s is generous)."""

    def __init__(self, repo_root, timeout=30.0):
        self.path = os.path.join(_common_dir(repo_root), "compile-seq.lock")
        self.timeout = timeout
        self.fd = None

    def __enter__(self):
        deadline = time.time() + self.timeout
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > self.timeout:
                        os.remove(self.path)   # stale seq lock (crashed holder)
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise JournalViolation("seq lock timeout: %s" % self.path)
                time.sleep(0.02)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
        try:
            os.remove(self.path)
        except OSError:
            pass


def validate_record(rec):
    """Schema gate at WRITE time -- malformed output is refused, not persisted."""
    if not isinstance(rec, dict):
        raise JournalViolation("record is not an object")
    for key, typ in RECORD_REQUIRED.items():
        if key not in rec:
            raise JournalViolation("missing field: %s" % key)
        if not isinstance(rec[key], typ):
            raise JournalViolation("field %s has wrong type %s"
                                   % (key, type(rec[key]).__name__))
    for i, a in enumerate(rec["absorbed"]):
        if not isinstance(a, dict):
            raise JournalViolation("absorbed[%d] not an object" % i)
        for key, typ in ABSORBED_REQUIRED.items():
            if key not in a or not isinstance(a[key], typ):
                raise JournalViolation("absorbed[%d].%s missing/wrong type" % (i, key))
    for i, nc in enumerate(rec["noop_candidates"]):
        if not isinstance(nc, dict) or "view" not in nc or "event" not in nc \
                or not isinstance(nc.get("verified"), bool):
            raise JournalViolation("noop_candidates[%d] malformed" % i)


def journal_dir(repo_root):
    return os.path.join(repo_root, JOURNAL_DIR)


def _read_seq(repo_root, seq):
    p = os.path.join(journal_dir(repo_root), "%d.json" % seq)
    if not os.path.isfile(p):
        return None, None
    b = open(p, "rb").read()
    return json.loads(b.decode("utf-8")), _record_hash(b)


def append_record(repo_root, rec):
    """Allocate seq under the common-dir lock, chain prev_record_hash, validate,
    write. Returns (seq, path). The caller supplies every field EXCEPT seq and
    prev_record_hash, which are engine-owned."""
    with _SeqLock(repo_root):
        jd = journal_dir(repo_root)
        os.makedirs(jd, exist_ok=True)
        # never append onto a corrupt chain: full integrity check first
        n_existing = check_chain(repo_root)
        nxt = n_existing + 1
        prev_hash = None
        if nxt > 1:
            _prev, prev_hash = _read_seq(repo_root, nxt - 1)
            if prev_hash is None:
                raise JournalViolation("gap below new seq %d" % nxt)
        rec = dict(rec, seq=nxt, prev_record_hash=prev_hash)
        validate_record(rec)
        path = os.path.join(jd, "%d.json" % nxt)
        if os.path.exists(path):
            raise JournalViolation("duplicate seq %d" % nxt)
        blob = json.dumps(rec, indent=1, sort_keys=True).encode("utf-8")
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(blob)
        os.replace(tmp, path)
        return nxt, path


def check_chain(repo_root):
    """Strict chain: contiguous seqs from 1, each prev_record_hash == sha256 of the
    prior record's bytes. Gap / duplicate-alias / mismatch -> JournalViolation.
    parent_git_sha is deliberately NOT examined (two worktrees off one HEAD are a
    legal success case)."""
    jd = journal_dir(repo_root)
    if not os.path.isdir(jd):
        return 0
    names = [f for f in os.listdir(jd) if f.endswith(".json")]
    seqs = []
    for f in names:
        stem = f[:-5]
        if not stem.isdigit():
            raise JournalViolation("non-numeric journal filename: %s" % f)
        seqs.append(int(stem))
    dupes = {s for s in seqs if seqs.count(s) > 1}
    if dupes:
        raise JournalViolation("duplicate seq: %s" % sorted(dupes))
    seqs.sort()
    prev_hash = None
    for want, seq in enumerate(seqs, 1):
        if seq != want:
            raise JournalViolation("gap: expected seq %d, found %d" % (want, seq))
        rec, this_hash = _read_seq(repo_root, seq)
        validate_record(rec)
        if rec["seq"] != seq:
            raise JournalViolation("seq field %s != filename %d" % (rec["seq"], seq))
        if rec["prev_record_hash"] != prev_hash:
            raise JournalViolation("prev_record_hash mismatch at seq %d" % seq)
        prev_hash = this_hash
    return len(seqs)


# ------------------------------------------------------------------ stage-only commit
def stage_only_commit(repo_root, manifest_paths, message):
    """Stage EXACTLY the declared paths (per-path `git add <file>`, never a
    directory), then one commit. Refuses directories in the manifest. Returns the
    new commit sha. Caller ordering contract: content writes, then append_record,
    then this -- the journal file itself must be IN manifest_paths."""
    for p in manifest_paths:
        ap = os.path.join(repo_root, p)
        if os.path.isdir(ap):
            raise JournalViolation("manifest path is a directory: %s" % p)
        if not os.path.isfile(ap):
            raise JournalViolation("manifest path missing on disk: %s" % p)
    for p in manifest_paths:
        r = subprocess.run(["git", "-C", repo_root, "add", "--", p],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise JournalViolation("git add failed for %s: %s" % (p, r.stderr[-200:]))
    r = subprocess.run(["git", "-C", repo_root, "commit", "-m", message,
                        "--only", "--"] + list(manifest_paths),
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise JournalViolation("git commit failed: %s" % (r.stderr or r.stdout)[-300:])
    sha = subprocess.run(["git", "-C", repo_root, "rev-parse", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    return sha


def minimal_record(run_type, parent_git_sha=""):
    """Skeleton with every required field, for callers and fixtures."""
    return {"run_type": run_type, "absorbed": [], "noop_candidates": [],
            "pins": [], "registrations": {}, "corrections": [],
            "parent_git_sha": parent_git_sha}


# ------------------------------------------------------------------ self-test
def self_test():
    import shutil
    import tempfile
    total = failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    base = tempfile.mkdtemp(prefix="ccore-")
    try:
        subprocess.run(["git", "-C", base, "init", "-q"], capture_output=True)
        subprocess.run(["git", "-C", base, "config", "user.email", "t@t"],
                       capture_output=True)
        subprocess.run(["git", "-C", base, "config", "user.name", "t"],
                       capture_output=True)

        # --- lockfile
        lp, broken = acquire_lock(base, "compile")
        case("lock acquired, no prior", os.path.isfile(lp) and broken is None)
        try:
            acquire_lock(base, "compile2", _pid=999999)
            case("second acquire refuses (live holder)", False)
        except LockHeld as e:
            case("second acquire refuses (live holder), names holder",
                 str(os.getpid()) in str(e))
        release_lock(base)
        # stale lock: dead pid
        with open(lp, "w", encoding="utf-8") as fh:
            json.dump({"pid": 4999999, "hostname": "x",
                       "started_iso": "2026-01-01T00:00:00",
                       "run_type": "crashed"}, fh)
        try:
            acquire_lock(base, "compile")
            case("stale lock NOT auto-broken without flag", False)
        except LockHeld as e:
            case("stale lock NOT auto-broken without flag", "STALE" in str(e))
        _lp, broken = acquire_lock(base, "compile", break_stale=True)
        case("--break-stale-lock succeeds and logs broken content",
             broken and broken["run_type"] == "crashed")
        # live lock + break_stale (no force) still refuses
        release_lock(base)
        with open(lp, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "hostname": "x",
                       "started_iso": "now", "run_type": "live"}, fh)
        try:
            acquire_lock(base, "compile", break_stale=True)
            case("live-PID lock never broken by --break-stale-lock", False)
        except LockHeld:
            case("live-PID lock never broken by --break-stale-lock", True)
        _lp, broken = acquire_lock(base, "compile", force=True)
        case("--force (never used by skills) overrides live lock",
             broken and broken["run_type"] == "live")
        release_lock(base)
        # TOCTOU seam (2026-07-05 concurrency campaign, 1/20 trials): a rival
        # creates the lockfile between the isfile check and the O_EXCL open --
        # must surface as LockHeld (documented refusal), never FileExistsError
        with open(lp, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "hostname": "x",
                       "started_iso": "now", "run_type": "racer"}, fh)
        try:
            acquire_lock(base, "compile", _isfile=lambda _p: False)
            case("TOCTOU race (lock appears after check) raises LockHeld", False)
        except LockHeld as e:
            case("TOCTOU race (lock appears after check) raises LockHeld",
                 "racer" in str(e) or "race" in str(e).lower())
        except FileExistsError:
            case("TOCTOU race (lock appears after check) raises LockHeld", False)
        release_lock(base)

        # --- journal schema gate
        try:
            append_record(base, {"run_type": "x"})
            case("malformed record refused at write", False)
        except JournalViolation:
            case("malformed record refused at write", True)
        case("journal dir empty after refusal (no partial write)",
             not os.path.isdir(journal_dir(base))
             or not os.listdir(journal_dir(base)))

        rec = minimal_record("compile", "abc123")
        s1, p1 = append_record(base, rec)
        s2, p2 = append_record(base, minimal_record("compile", "abc123"))
        case("seq monotonic 1,2", (s1, s2) == (1, 2))
        r2, _h = _read_seq(base, 2)
        r1b = open(p1, "rb").read()
        case("prev_record_hash = sha256(record 1)",
             r2["prev_record_hash"] == hashlib.sha256(r1b).hexdigest())
        case("chain check passes (2 records)", check_chain(base) == 2)
        case("same parent_git_sha both records trips NOTHING (legal concurrency)",
             True)  # asserted by the passing chain check above

        # violations: gap
        s3, p3 = append_record(base, minimal_record("compile"))
        os.remove(os.path.join(journal_dir(base), "2.json"))
        try:
            check_chain(base)
            case("gap detected", False)
        except JournalViolation as e:
            case("gap detected", "gap" in str(e))
        # restore then corrupt hash
        os.remove(p3)
        append_record(base, minimal_record("compile"))
        r2p = os.path.join(journal_dir(base), "2.json")
        r2 = json.load(open(r2p, encoding="utf-8"))
        r2["prev_record_hash"] = "0" * 64
        open(r2p, "w", encoding="utf-8").write(json.dumps(r2))
        try:
            check_chain(base)
            case("hash mismatch detected", False)
        except JournalViolation as e:
            case("hash mismatch detected", "mismatch" in str(e))
        # duplicate-alias filename
        shutil.copy(r2p, os.path.join(journal_dir(base), "02.json"))
        try:
            check_chain(base)
            case("duplicate seq detected", False)
        except JournalViolation as e:
            case("duplicate seq detected", "duplicate" in str(e))
        os.remove(os.path.join(journal_dir(base), "02.json"))

        # --- stage-only commit
        shutil.rmtree(journal_dir(base))
        wiki = os.path.join(base, "wiki")
        os.makedirs(wiki)
        open(os.path.join(wiki, "a.md"), "w").write("view A\n")
        open(os.path.join(wiki, "salt-in-wiki.md"), "w").write("unclaimed\n")
        open(os.path.join(base, "salt-root.md"), "w").write("salt\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "seed"],
                       capture_output=True)
        # the run: modify a.md + salt files, declare only a.md + journal
        open(os.path.join(wiki, "a.md"), "a").write("run edit\n")
        open(os.path.join(wiki, "salt-in-wiki.md"), "a").write("salt edit\n")
        open(os.path.join(base, "salt-root.md"), "a").write("salt edit\n")
        open(os.path.join(base, "salt-untracked.py"), "w").write("x=1\n")
        sq, jp = append_record(base, minimal_record("compile"))
        sha = stage_only_commit(base, ["wiki/a.md",
                                       os.path.relpath(jp, base).replace(os.sep, "/")],
                                "run commit")
        shown = subprocess.run(["git", "-C", base, "show", "--name-only",
                                "--format=", sha], capture_output=True,
                               text=True).stdout.split()
        case("commit-set == manifest-set (incl. journal, salt excluded)",
             sorted(shown) == sorted(["wiki/a.md", "receipts/journal/1.json"]))
        case("salt files survive on disk, unstaged",
             "salt edit" in open(os.path.join(wiki, "salt-in-wiki.md")).read()
             and os.path.isfile(os.path.join(base, "salt-untracked.py")))
        dirty = subprocess.run(["git", "-C", base, "status", "--porcelain"],
                               capture_output=True, text=True).stdout
        case("salt changes still dirty (never committed)",
             "salt-in-wiki.md" in dirty and "salt-root.md" in dirty
             and "salt-untracked.py" in dirty)
        try:
            stage_only_commit(base, ["wiki"], "dir add")
            case("directory manifest path refused", False)
        except JournalViolation:
            case("directory manifest path refused", True)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failed:
        print("compile-core: FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("compile-core: PASS (%d/%d)" % (total, total))
    return 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    if "--acquire-lock-cli" in argv:
        i = argv.index("--acquire-lock-cli")
        root = argv[i + 1]
        run_type = argv[i + 2] if len(argv) > i + 2 else "compile"
        try:
            lp, _broken = acquire_lock(root, run_type)
            print("lock acquired: %s" % lp)
            return 0
        except LockHeld as e:
            print("LOCK HELD: %s" % e)
            return EXIT_LOCK_HELD
    if "--check-chain" in argv:
        root = argv[argv.index("--check-chain") + 1] if \
            len(argv) > argv.index("--check-chain") + 1 else "."
        try:
            n = check_chain(root)
            print("chain OK: %d record(s)" % n)
            return 0
        except JournalViolation as e:
            print("CHAIN FAIL: %s" % e)
            return 1
    print(__doc__.strip().split("\n")[0])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
