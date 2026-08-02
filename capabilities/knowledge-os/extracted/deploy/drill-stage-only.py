#!/usr/bin/env python3
"""drill-stage-only.py -- OPS-3 STAGE-ONLY DISCIPLINE drill (test-plan tp:~290-298).

Salts a throwaway repo's tree with the four real contamination classes from the
gate text, then executes the REAL engine commit path (compile-core.append_record +
stage_only_commit) over a fixture manifest, and asserts the commit touches exactly
the manifest paths while every salt file survives untouched on disk.

Salt classes (exactly these four):
  1. untracked script at root       (scripts/zz-drill-salt.ts)
  2. untracked .md at root          (DRILL-SALT.md)
  3. modified tracked file OUTSIDE wiki/   (roadmap.md)
  4. modified tracked file INSIDE wiki/, NOT claimed by the run (wiki/unclaimed.md)
     -- the hard case: `git add wiki/` would swallow it; discipline must be
     per-path adds only.

The "run": edits one claimed wiki view (wiki/claimed.md), writes a journal record
via append_record, then commits via stage_only_commit with manifest =
[claimed view, journal path].

Assertions (quantified, per the gate):
  - commit-file-set == manifest-path-set (via `git show --name-only`)
  - salt-set intersect commit-set = empty
  - every salt file still on disk, unstaged/uncommitted, bytes unchanged
  - the in-wiki unclaimed file (salt 4) explicitly untouched by the commit
  - mechanics rule: a manifest entry that is a DIRECTORY is refused (JournalViolation)

Usage: drill-stage-only.py
Exit: 0 PASS | 1 FAIL
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "compile_core", os.path.join(_HERE, "compile-core.py"))
compile_core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compile_core)


def _git(root, *args):
    r = subprocess.run(["git", "-C", root] + list(args),
                       capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main():
    total = failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("%s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    base = tempfile.mkdtemp(prefix="stageonly-")
    try:
        # --- seeded baseline: wiki/ tree with 2+ tracked files, one tracked file at root
        _git(base, "init", "-q")
        _git(base, "config", "user.email", "t@t")
        _git(base, "config", "user.name", "t")
        _write(os.path.join(base, "roadmap.md"), "roadmap v1\n")
        _write(os.path.join(base, "wiki", "claimed.md"), "claimed view v1\n")
        _write(os.path.join(base, "wiki", "unclaimed.md"), "unclaimed view v1\n")
        _git(base, "add", "-A")
        _git(base, "commit", "-q", "-m", "baseline")

        # --- salt the tree with EXACTLY the four salt classes
        salt_untracked_script = os.path.join(base, "scripts", "zz-drill-salt.ts")
        _write(salt_untracked_script, "// untracked salt script\n")

        salt_untracked_md = os.path.join(base, "DRILL-SALT.md")
        _write(salt_untracked_md, "# untracked salt md\n")

        salt_roadmap = os.path.join(base, "roadmap.md")
        _write(salt_roadmap, "roadmap v1\nSALT: unrelated edit outside wiki/\n")

        salt_unclaimed = os.path.join(base, "wiki", "unclaimed.md")
        _write(salt_unclaimed, "unclaimed view v1\nSALT: unrelated edit inside wiki/\n")

        salt_files = {
            "scripts/zz-drill-salt.ts": salt_untracked_script,
            "DRILL-SALT.md": salt_untracked_md,
            "roadmap.md": salt_roadmap,
            "wiki/unclaimed.md": salt_unclaimed,
        }
        # record every salt file's bytes BEFORE the run
        salt_bytes_before = {k: _sha(v) for k, v in salt_files.items()}

        # --- the run: edit one claimed wiki view + write a journal record
        claimed_path = os.path.join(base, "wiki", "claimed.md")
        _write(claimed_path, "claimed view v1\nabsorbed by the run\n")

        parent_sha = _git(base, "rev-parse", "HEAD")[1].strip()
        rec = compile_core.minimal_record("compile", parent_sha)
        seq, journal_path = compile_core.append_record(base, rec)
        journal_rel = os.path.relpath(journal_path, base).replace(os.sep, "/")

        manifest = ["wiki/claimed.md", journal_rel]
        commit_sha = compile_core.stage_only_commit(
            base, manifest, "drill: absorb claimed.md (seq %d)" % seq)

        # --- assertions
        _, show_out, _ = _git(base, "show", "--name-only", "--pretty=format:", commit_sha)
        commit_set = {p.strip().replace(os.sep, "/") for p in show_out.splitlines() if p.strip()}
        manifest_set = {p.replace(os.sep, "/") for p in manifest}

        case("commit-file-set == manifest-path-set", commit_set == manifest_set)
        case("salt-set intersect commit-set = empty",
             not (set(salt_files.keys()) & commit_set))

        _, porcelain, _ = _git(base, "status", "--porcelain")

        all_survive = True
        for rel, path in salt_files.items():
            exists = os.path.isfile(path)
            bytes_unchanged = exists and _sha(path) == salt_bytes_before[rel]
            # git collapses an untracked dir's sole content to "dir/" in porcelain
            top_dir = rel.split("/")[0] + "/"
            still_dirty = (rel in porcelain or rel.replace("/", os.sep) in porcelain
                          or top_dir in porcelain)
            ok = exists and bytes_unchanged and still_dirty
            case("salt survives unstaged/uncommitted/unchanged: %s" % rel, ok)
            all_survive = all_survive and ok

        case("all salt files survive (aggregate)", all_survive)

        # explicit hard-case assertion: in-wiki unclaimed file untouched by the commit
        case("in-wiki unclaimed file explicitly untouched by commit",
             "wiki/unclaimed.md" not in commit_set
             and _sha(salt_unclaimed) == salt_bytes_before["wiki/unclaimed.md"])

        # mechanics rule: a manifest entry that is a directory is refused
        try:
            compile_core.stage_only_commit(base, ["wiki"], "dir add refused")
            case("directory manifest entry refused (JournalViolation)", False)
        except compile_core.JournalViolation:
            case("directory manifest entry refused (JournalViolation)", True)

    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failed:
        print("drill-stage-only (OPS-3): FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("drill-stage-only (OPS-3): PASS (%d/%d)" % (total, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
