#!/usr/bin/env python3
"""check-corpus-support.py -- LLM-6 read-side/verify-side sensor: re-verify
every journaled corpus_support entry's support_lines are EXACT lines of the
resolved artifact body AT THE PINNED artifact_sha256 (F16 re-verification).

Emit-side F16 is already enforced at ABSORB time inside
compile-v2.validate_absorb_output: it checks corpus_support artifact exists,
artifact_sha256 pins the CURRENT body, and support_lines are exact lines of
that current body -- all evaluated against the worktree AS IT WAS AT EMIT.
This script is the independent READ-SIDE re-check, run later (VERIFY / CI /
audit time), against journal history: the artifact may have been edited
since that compile ran, so the pinned artifact_sha256 may no longer match
the CURRENT file body. This script does not assume "current file" is right;
it resolves the artifact AT THE PINNED SHA, wherever that pin lives in the
artifact's git history, then re-checks support_lines against THAT body.

Artifact resolution (a git-blob-sha1-vs-content-sha256 problem):
  artifact_sha256 in the journal is sha256(text) (compile_v2._sha256 -- see
  compile-v2.py ~line 77: hashlib.sha256(text.encode("utf-8")).hexdigest()),
  NOT a git blob sha1 (git hashes "blob <len>\0<content>", a different value
  and a different algorithm identity than a bare content sha256). So we
  cannot `git cat-file` the pin directly -- there is no git object keyed by
  a content-sha256. Resolution walks CANDIDATE blobs and hashes each one:
    1. current worktree file -- if sha256(current text) == pin: resolution
       = "current".
    2. else, git history of that path (`git log --follow --format=%H --
       <artifact>`, oldest-safe: every commit that ever touched the path,
       including across renames) -- for each commit, retrieve the blob:
       try `git show <commit>:<artifact>` first (the path's current name,
       cheap and correct for every commit at/after the rename); if that
       fails, the commit predates a rename `--follow` bridged, so fall
       back to every path that commit's own diff touched
       (`git diff-tree --name-only -r <commit>`, both sides of the tree)
       and try `git show <commit>:<candidate>` for each. This deliberately
       does NOT try to recover "the one true path at each commit" from
       git's rename-DISPLAY heuristics (`--name-status -M`/`--raw`):
       --follow's own internal path-bridging is more lenient than the
       R-line similarity threshold those flags use, especially on small
       files, so parsing name-status output to track a per-commit path
       can disagree with which commits --follow actually walked and
       silently miss the historical blob again, for a different reason.
       Trying every path each commit touched sidesteps that mismatch
       entirely. First candidate whose sha256 == pin: resolution =
       "historical". (Using ONLY the current artifact path for every
       historical commit -- never falling back to the commit's other
       touched paths -- would make `git show` fail for every commit
       before a rename, and a genuinely-matching historical pin would be
       missed as UNRESOLVED.)
    3. no path history at all, or history exhausted with no match:
       resolution = "UNRESOLVED".

Per-entry report: {seq, view, artifact, artifact_sha256, resolution
  ("current"|"historical"|"UNRESOLVED"), lines_ok (bool), bad_lines: [str]}.

Exit codes:
  0  every entry resolved (current or historical) AND every support_line
     verified exact in the resolved body.
  1  VIOLATION -- at least one resolved artifact had a support_line that is
     NOT an exact line of the resolved body (fabricated / stale-post-edit
     claim that the historical pin does not actually cover).
  2  INCONCLUSIVE -- at least one entry's artifact_sha256 could not be
     resolved to ANY candidate blob (no current match, no historical match,
     or the path has no git history at all) -- structurally impossible to
     decide, NEVER reported as silently clean.

Usage:
  check-corpus-support.py --root DIR [--seq N]   # walk receipts/journal
  check-corpus-support.py --self-test
"""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(basename, alias):
    spec = importlib.util.spec_from_file_location(
        alias, os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _load("compile-core.py", "compile_core_v2_ccs")

JOURNAL_DIR = os.path.join("receipts", "journal")


# ------------------------------------------------------------------ git shell
def _git(repo, *args):
    p = subprocess.run(["git", "-C", repo] + list(args),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p.returncode, p.stdout, p.stderr


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ journal walk
def iter_journal_records(repo):
    """Yield (seq, record dict) for every receipts/journal/<seq>.json in
    ascending seq order. Read-only -- never writes, never validates chain
    integrity (that is compile-core.check_chain's job elsewhere); a gap or
    corrupt record here is skipped with a synthetic INCONCLUSIVE entry
    rather than raising, so one bad record doesn't blind the sensor to the
    rest of the journal."""
    jd = os.path.join(repo, JOURNAL_DIR)
    if not os.path.isdir(jd):
        return
    names = [f for f in os.listdir(jd) if f.endswith(".json")]
    seqs = sorted((int(f[:-5]), f) for f in names if f[:-5].isdigit())
    for seq, fname in seqs:
        path = os.path.join(jd, fname)
        try:
            rec = json.load(open(path, encoding="utf-8"))
        except (ValueError, OSError):
            yield seq, None
            continue
        yield seq, rec


# ------------------------------------------------------------------ resolution
def _artifact_history_shas(repo, artifact_rel):
    """Every commit sha that ever touched artifact_rel, oldest-safe across
    renames (--follow). Empty list if the path has no history at all (never
    committed, or git itself errors).

    NOTE: --follow's own internal path-tracking (used just to decide which
    commits belong in this log at all) is a separate, MORE LENIENT
    break/rename detector than the one behind `--name-status`/`-M`/`--raw`
    rename display. On small files especially, --follow will happily jump
    a rename that `--name-status -M` refuses to pair as R (falls back to
    a bare D+A). That was tried here first and rejected: parsing
    name-status R-lines to recover "the path at each commit" silently
    breaks exactly on the files this sensor cares about most (small
    provenance/journal artifacts), re-introducing the same missed-pin bug
    for a different reason. So we do NOT try to recover a single
    "path at commit" from git's rename display at all -- see
    _blob_at_commit below, which sidesteps the question entirely."""
    rc, out, _err = _git(repo, "log", "--follow", "--format=%H", "--",
                         artifact_rel)
    if rc != 0:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _blob_at_commit(repo, commit_sha, artifact_rel):
    """Best-effort retrieval of the blob this logical artifact had at
    commit_sha, tolerant of the artifact having lived at a DIFFERENT path
    in that commit's tree (pre-rename, or renamed-and-back).

    1. Try the direct, cheap lookup: `git show <commit>:<artifact_rel>`.
       Covers every commit at or after the path's current name took
       effect -- the overwhelmingly common case.
    2. If that fails (path didn't exist in that commit's tree), the
       commit is on the OTHER side of some rename `--follow` bridged.
       Rather than re-deriving which exact path string that was (which
       depends on git's similarity-threshold rename detection and can
       disagree with what --follow itself bridged -- see
       _artifact_history_shas), fall back to every path this commit's
       diff actually touched (`git diff-tree --name-only -r <commit>`,
       both the old and new side of the tree) and try each. This is
       robust to weak/undetected rename pairing because it does not
       depend on git recognizing a D+A pair as a rename at all -- it just
       tries every path this commit changed, at this commit's own tree.

    Returns text or None."""
    rc, blob, _err = _git(repo, "show", "%s:%s" % (commit_sha, artifact_rel))
    if rc == 0:
        return blob
    rc, out, _err = _git(repo, "diff-tree", "--no-commit-id", "--name-only",
                         "-r", commit_sha)
    if rc != 0:
        return None
    for candidate in (ln.strip() for ln in out.splitlines() if ln.strip()):
        if candidate == artifact_rel:
            continue  # already tried above
        rc, blob, _err = _git(repo, "show", "%s:%s" % (commit_sha, candidate))
        if rc == 0:
            return blob
    return None


def resolve_artifact(repo, artifact_rel, pinned_sha256):
    """Returns (resolution, body_or_None). resolution in
    {"current","historical","UNRESOLVED"}."""
    ap = os.path.join(repo, artifact_rel.replace("/", os.sep))
    if os.path.isfile(ap):
        try:
            cur_text = open(ap, encoding="utf-8", errors="replace").read()
        except OSError:
            cur_text = None
        if cur_text is not None and _sha256(cur_text) == pinned_sha256:
            return "current", cur_text
    for commit_sha in _artifact_history_shas(repo, artifact_rel):
        blob = _blob_at_commit(repo, commit_sha, artifact_rel)
        if blob is None:
            continue
        if _sha256(blob) == pinned_sha256:
            return "historical", blob
    return "UNRESOLVED", None


# ------------------------------------------------------------------ main check
def check_entry(repo, seq, view, cs):
    """cs = one corpus_support entry {"artifact","artifact_sha256",
    "support_lines"}. Returns a report dict."""
    artifact = cs.get("artifact", "")
    pinned = cs.get("artifact_sha256", "")
    support_lines = cs.get("support_lines") or []
    entry = {"seq": seq, "view": view, "artifact": artifact,
             "artifact_sha256": pinned, "resolution": None,
             "lines_ok": None, "bad_lines": []}
    if not artifact or not pinned:
        entry["resolution"] = "UNRESOLVED"
        entry["lines_ok"] = False
        entry["bad_lines"] = list(support_lines)
        return entry
    resolution, body = resolve_artifact(repo, artifact, pinned)
    entry["resolution"] = resolution
    if resolution == "UNRESOLVED":
        entry["lines_ok"] = False
        entry["bad_lines"] = list(support_lines)
        return entry
    body_lines = set(body.splitlines())
    bad = [ln for ln in support_lines if ln not in body_lines]
    entry["lines_ok"] = (len(bad) == 0) and len(support_lines) > 0
    entry["bad_lines"] = bad
    return entry


def check_repo(repo, only_seq=None):
    """Walk every journal record's absorbed[].corpus_support entries.
    Returns (reports, exit_code). exit_code: 0 clean, 1 violation (any
    lines_ok==False on a RESOLVED artifact), 2 inconclusive (any
    UNRESOLVED or unreadable record present, and no violation already
    forces exit 1 -- violation outranks inconclusive: a caught fabrication
    is worse than an unresolved pin)."""
    reports = []
    saw_violation = False
    saw_inconclusive = False
    for seq, rec in iter_journal_records(repo):
        if only_seq is not None and seq != only_seq:
            continue
        if rec is None:
            reports.append({"seq": seq, "view": None, "artifact": None,
                            "artifact_sha256": None,
                            "resolution": "UNRESOLVED",
                            "lines_ok": False,
                            "bad_lines": ["record unreadable/corrupt"]})
            saw_inconclusive = True
            continue
        for a in rec.get("absorbed", []) or []:
            view = a.get("view", "")
            for cs in a.get("corpus_support", []) or []:
                entry = check_entry(repo, seq, view, cs)
                reports.append(entry)
                if entry["resolution"] == "UNRESOLVED":
                    saw_inconclusive = True
                elif not entry["lines_ok"]:
                    saw_violation = True
    if saw_violation:
        return reports, 1
    if saw_inconclusive:
        return reports, 2
    return reports, 0


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

    base = tempfile.mkdtemp(prefix="ccs-")
    try:
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", base] + args, capture_output=True)
        os.makedirs(os.path.join(base, "raw"))
        os.makedirs(os.path.join(base, "wiki"))

        art_path = os.path.join(base, "raw", "e1.md")

        def write(path, text):
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)

        def commit(msg):
            subprocess.run(["git", "-C", base, "add", "-A"],
                           capture_output=True)
            subprocess.run(["git", "-C", base, "commit", "-qm", msg],
                           capture_output=True)

        # --- v1 of artifact: two lines
        write(art_path, "alpha line one\nbeta line two\n")
        write(os.path.join(base, "wiki", "a.md"), "# A\nstable\n")
        commit("seed v1")
        v1_text = open(art_path, encoding="utf-8").read()
        v1_sha = _sha256(v1_text)

        # --- healthy support entry (current, valid line)
        rec1 = core.minimal_record("compile", "sha1")
        rec1["absorbed"] = [{
            "view": "wiki/a.md", "events": ["raw/e1.md"],
            "pre_blob": "x", "post_blob": "y", "manifest": [],
            "corpus_support": [{"artifact": "raw/e1.md",
                                "artifact_sha256": v1_sha,
                                "support_lines": ["alpha line one"]}]}]
        rec1["noop_candidates"] = []
        rec1["run_window"] = {"start": "2026-07-04T00:00:00",
                              "end": "2026-07-04T23:59:59"}
        seq1, jp1 = core.append_record(base, rec1)
        core.stage_only_commit(
            base, ["wiki/a.md",
                  os.path.relpath(jp1, base).replace(os.sep, "/")],
            "compile seq %d" % seq1)

        reports, code = check_repo(base, only_seq=seq1)
        e = reports[0]
        case("healthy support entry: resolution=current",
            e["resolution"] == "current")
        case("healthy support entry: lines_ok", e["lines_ok"] is True)
        case("healthy support entry: exit 0", code == 0)

        # --- edit the artifact (support line for seq1's pin no longer
        # matches CURRENT body) but seq1's pin still resolves HISTORICALLY
        # against the v1 blob and should still pass.
        write(art_path, "alpha line one EDITED\nbeta line two\ngamma new\n")
        commit("edit artifact")
        reports, code = check_repo(base, only_seq=seq1)
        e = reports[0]
        case("post-edit: old pin resolves historically",
            e["resolution"] == "historical")
        case("post-edit: old pin's support_lines still verified",
            e["lines_ok"] is True and code == 0)

        # --- rename-before-pin: artifact committed at an OLD path, pinned
        # there, then `git mv` to a NEW path plus a content change. The
        # journal entry pins the OLD content sha under the OLD path
        # string (that is what compile-v2 recorded artifact_rel as, at
        # ABSORB time, before the rename ever happened). `resolve_artifact`
        # is called with that OLD path -- current-worktree lookup at the
        # old path fails (git mv removed it there), so this exercises the
        # git-history branch: history must walk back PAST the rename and
        # retrieve the blob at the path it actually had pre-rename, not
        # at the (post-rename) artifact_rel string used to invoke --follow.
        # commit 1 (seed): content lives at old_rel.
        # commit 2 (rename+edit): `git mv` old_rel -> new_rel, content
        #   changed in the SAME commit -- this is the historical version
        #   we pin below, and it is ONLY ever reachable at new_rel (it did
        #   not exist at old_rel in that commit's tree).
        # commit 3 (edit again): current worktree content, unrelated to
        #   the pin, so the fast "current" path can't shortcut this case.
        # The journal entry's `artifact` field is old_rel (the name this
        # logical artifact was known by at the time this particular pin
        # was recorded) -- exercising exactly the failure mode where
        # `--follow`'s commit walk over old_rel DOES reach the pinned
        # commit, but a naive `git show <commit>:old_rel` on it fails
        # because that commit's tree only has the content at new_rel.
        old_rel = "raw/e2-old.md"
        new_rel = "raw/e2-new.md"
        old_path = os.path.join(base, old_rel.replace("/", os.sep))
        new_path = os.path.join(base, new_rel.replace("/", os.sep))
        write(old_path, "delta line one\nepsilon line two\n")
        commit("seed e2 at old path")

        subprocess.run(["git", "-C", base, "mv", old_rel, new_rel],
                       capture_output=True)
        write(new_path, "delta line one CHANGED\nepsilon two\n")
        commit("rename e2 + edit")
        pinned_text = open(new_path, encoding="utf-8").read()
        pinned_sha = _sha256(pinned_text)

        write(new_path, "delta line one CHANGED AGAIN\nepsilon three\n")
        commit("edit e2 again (current)")

        rec5 = core.minimal_record("compile", "sha5")
        rec5["absorbed"] = [{
            "view": "wiki/a.md", "events": ["raw/e2-old.md"],
            "pre_blob": "x", "post_blob": "y", "manifest": [],
            "corpus_support": [{"artifact": old_rel,
                                "artifact_sha256": pinned_sha,
                                "support_lines": ["delta line one CHANGED"]}]}]
        rec5["noop_candidates"] = []
        rec5["run_window"] = {"start": "2026-07-05T03:00:00",
                              "end": "2026-07-05T03:00:00"}
        seq5, jp5 = core.append_record(base, rec5)
        core.stage_only_commit(
            base, [os.path.relpath(jp5, base).replace(os.sep, "/")],
            "compile seq %d (rename-before-pin)" % seq5)
        reports, code = check_repo(base, only_seq=seq5)
        e = reports[0]
        case("rename-before-pin: resolves historical (not UNRESOLVED)",
            e["resolution"] == "historical")
        case("rename-before-pin: old-path pin's support_lines verified",
            e["lines_ok"] is True and code == 0)

        # --- fabricated support line (never existed in artifact at any
        # version) -> resolves current (matches CURRENT sha), fails lines
        v2_text = open(art_path, encoding="utf-8").read()
        v2_sha = _sha256(v2_text)
        rec2 = core.minimal_record("compile", "sha2")
        rec2["absorbed"] = [{
            "view": "wiki/a.md", "events": ["raw/e1.md"],
            "pre_blob": "x", "post_blob": "y", "manifest": [],
            "corpus_support": [{"artifact": "raw/e1.md",
                                "artifact_sha256": v2_sha,
                                "support_lines": [
                                    "this line exists nowhere ever"]}]}]
        rec2["noop_candidates"] = []
        rec2["run_window"] = {"start": "2026-07-05T00:00:00",
                              "end": "2026-07-05T23:59:59"}
        seq2, jp2 = core.append_record(base, rec2)
        core.stage_only_commit(
            base, [os.path.relpath(jp2, base).replace(os.sep, "/")],
            "compile seq %d (fabricated)" % seq2)
        reports, code = check_repo(base, only_seq=seq2)
        e = reports[0]
        case("fabricated line: resolution=current",
            e["resolution"] == "current")
        case("fabricated line: lines_ok False", e["lines_ok"] is False)
        case("fabricated line: exit 1 (violation)", code == 1)

        # --- unresolvable pin (sha matches nothing, current or historical)
        rec3 = core.minimal_record("compile", "sha3")
        rec3["absorbed"] = [{
            "view": "wiki/a.md", "events": ["raw/e1.md"],
            "pre_blob": "x", "post_blob": "y", "manifest": [],
            "corpus_support": [{"artifact": "raw/e1.md",
                                "artifact_sha256": "0" * 64,
                                "support_lines": ["alpha line one"]}]}]
        rec3["noop_candidates"] = []
        rec3["run_window"] = {"start": "2026-07-05T01:00:00",
                              "end": "2026-07-05T01:00:00"}
        seq3, jp3 = core.append_record(base, rec3)
        core.stage_only_commit(
            base, [os.path.relpath(jp3, base).replace(os.sep, "/")],
            "compile seq %d (unresolvable)" % seq3)
        reports, code = check_repo(base, only_seq=seq3)
        e = reports[0]
        case("unresolvable pin: resolution=UNRESOLVED",
            e["resolution"] == "UNRESOLVED")
        case("unresolvable pin: exit 2 (inconclusive)", code == 2)

        # --- artifact referenced by an entry that never existed on disk
        # or in history at all (no history path at all)
        rec4 = core.minimal_record("compile", "sha4")
        rec4["absorbed"] = [{
            "view": "wiki/a.md", "events": [], "pre_blob": "x",
            "post_blob": "y", "manifest": [],
            "corpus_support": [{"artifact": "raw/never-existed.md",
                                "artifact_sha256": _sha256("phantom"),
                                "support_lines": ["phantom line"]}]}]
        rec4["noop_candidates"] = []
        rec4["run_window"] = {"start": "2026-07-05T02:00:00",
                              "end": "2026-07-05T02:00:00"}
        seq4, jp4 = core.append_record(base, rec4)
        core.stage_only_commit(
            base, [os.path.relpath(jp4, base).replace(os.sep, "/")],
            "compile seq %d (no-history artifact)" % seq4)
        reports, code = check_repo(base, only_seq=seq4)
        e = reports[0]
        case("no-history artifact: UNRESOLVED",
            e["resolution"] == "UNRESOLVED")
        case("no-history artifact: exit 2", code == 2)

        # --- full-repo walk sees every seq, and violation outranks
        # inconclusive when both are present
        reports_all, code_all = check_repo(base)
        case("full walk covers all 5 seqs (>=5 entries)",
            len(reports_all) >= 5)
        case("violation outranks inconclusive in aggregate exit",
            code_all == 1)

    finally:
        shutil.rmtree(base, ignore_errors=True)

    print("check-corpus-support self-test: %s (%d/%d)"
         % ("PASS" if failed == 0 else "FAIL", total - failed, total))
    return 0 if failed == 0 else 1


# ------------------------------------------------------------------ CLI
def main(argv):
    if "--self-test" in argv:
        return self_test()
    if "--root" not in argv:
        print("usage: check-corpus-support.py --root DIR [--seq N] | "
             "--self-test")
        return 2
    repo = argv[argv.index("--root") + 1]
    only_seq = None
    if "--seq" in argv:
        only_seq = int(argv[argv.index("--seq") + 1])
    reports, code = check_repo(repo, only_seq=only_seq)
    print(json.dumps(reports, indent=1, sort_keys=True))
    if code == 0:
        print("check-corpus-support: PASS (%d entries)" % len(reports))
    elif code == 1:
        print("check-corpus-support: VIOLATION")
    else:
        print("check-corpus-support: INCONCLUSIVE (unresolved pin(s))")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
