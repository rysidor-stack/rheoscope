#!/usr/bin/env python3
"""promote.py -- the operator's ONE lightweight action that publishes a prepared
retirement (ADR #11 condition 4 as amended 2026-08-22, binding item 3; v3.0.50, backlog
v3.0-139 (a)). Run it from YOUR OWN TERMINAL, never from an agent session:

    py deploy/promote.py <proposal-digest>        # >= 12 hex chars of the digest printed
                                                  # by `retire.py --propose` / `--list`

What it does, in order, and stops at the first refusal:
  1. refuses to run inside an agent session (the CLAUDECODE / AI_AGENT environment markers
     and RHEOSCOPE_UNATTENDED): this is the procedural human boundary -- the action has
     to come from outside the session that prepared the proposal;
  2. finds the prepared commit C (refs/retire/<seq>) whose journal record carries EXACTLY
     that proposal digest (a prefix must be unique);
  3. re-derives C from git objects (deploy/retire.py verify_prepared: record <-> proposal
     digest, view@C == view@parent with the span replaced by the exact stub, cold bytes ==
     span bytes) and refuses a stale C (parent != branch head) or an inconsistent one;
  4. shows you what will publish: view, spans, bytes before/after, destination(s), digest;
     the full preimage is in the proposal artifact (`retire.py --show <digest>`);
  5. under trust_surface_signing: visible -- writes the PROMOTION RECORD: an annotated
     (unsigned) tag retire/<seq> on C whose message carries `proposal_digest: sha256:<d>`
     and `mode: visible`, then runs trust.py's check_publishable (every condition: single
     commit atop the head, exactly one appended record, digest bound, not consumed) and
     fast-forwards the branch to C atomically on the expected old head; the working tree
     is updated for the paths C touched (refused beforehand if they are dirty);
     under trust_surface_signing: required -- prints the exact `git tag -s retire/<seq> C`
     to run with your presence-requiring key and publishes only if a verified operator
     tag already exists (the sk path is unchanged);
     under an unchosen mode (absent or `warn`) -- refuses: retirement is disabled.

What it is NOT: cryptographic proof that a human ran it. Under the stated threat (one
operator, one machine, a misled/wrong/unattended session -- not an adaptive adversary) the
boundary is that nothing in-session can run it (the Bash DENY tier refuses `promote.py`
and `git tag retire/*`; this file refuses under the session markers) and that whatever
publishes is reconstructed into the pending list the next sweep shows you. Commit-author
metadata is never consulted. A chat "yes" publishes nothing.

Exit: 0 published | 2 refused.
"""

import argparse
import importlib.util
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_trust = _load("_promote_trust", "trust.py")
_retire = _load("_promote_retire", "retire.py")

SESSION_MARKERS = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "AI_AGENT", "CODEX_SANDBOX",
                   "RHEOSCOPE_UNATTENDED")


class Refuse(Exception):
    pass


def in_agent_session(env=None):
    env = os.environ if env is None else env
    return [k for k in SESSION_MARKERS if env.get(k, "").strip()]


def _git(repo, *args):
    p = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr


def promote(repo, digest, branch="main", env=None, say=print):
    # No bypass parameter exists on purpose (cross-vendor round-10 catch): the session-marker
    # refusal is unconditional in this code path. (A same-user caller can of course copy the
    # function -- the mediated-lane DENY and the pending list are the layers behind this one.)
    markers = in_agent_session(env)
    if markers:
        raise Refuse("refusing inside an agent session (%s set). The promote action is the "
                     "operator's, from their own terminal -- that is the whole boundary. Nothing "
                     "was published." % ", ".join(markers))
    if not _trust.is_git_repo(repo):
        raise Refuse("%s is not a git repository" % repo)
    mode, why = _trust.signing_mode(repo)
    if not _trust.mode_chosen(repo):
        raise Refuse("retirement disabled: " + _trust.ABSENT_MODE_NOTE)
    try:
        seq, c, ok, reason, rec = _retire.find_by_digest(repo, digest)
    except _retire.Refuse as e:
        raise Refuse(str(e))
    if not ok:
        raise Refuse("prepared retirement seq %d (%s) does not re-derive from git objects: %s -- "
                     "run `py deploy/retire.py --recover`" % (seq, c[:12], reason))
    head = _trust._rev(repo, "refs/heads/%s" % branch)
    parent = (_trust._parents(repo, c) or [None])[0]
    if parent != head:
        raise Refuse("prepared C %s is STALE: it was built on %s but %s is now %s. Run "
                     "`py deploy/retire.py --recover` (discards it) and re-prepare." % (
                         c[:12], (parent or "?")[:12], branch, (head or "?")[:12]))
    full_digest = str(rec.get("proposal_digest", "")).replace("sha256:", "")
    tag = "retire/%d" % seq
    say("retirement seq %d -- commit %s on %s (parent %s)" % (seq, c[:12], branch, head[:12]))
    say("  view: %s" % rec.get("view"))
    for s in rec.get("spans", []):
        say("  span %r lines %d-%d: %d bytes -> %s (%s)" % (
            s["title"], s["start_line"], s["end_line"], s["bytes"], s["mode"], s["target"]))
    say("  proposal: %s  digest sha256:%s" % (rec.get("proposal"), full_digest))
    say("  full preimage: py deploy/retire.py --show %s" % full_digest[:16])
    # the working tree must be clean on the paths C touches (the fast-forward updates them)
    rc, ns, _ = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", head, c)
    touched = [l.strip() for l in ns.splitlines() if l.strip()]
    rc, st, _ = _git(repo, "status", "--porcelain", "--", *touched) if touched else (0, "", "")
    if st.strip():
        raise Refuse("working tree has uncommitted changes on paths this retirement touches:\n%s\n"
                     "commit or discard them first; nothing was published" % st.strip())
    # Windows MAX_PATH (the G2 run's catch, v3.0-133's defect class): a cold object carries a
    # 64-hex content address, so under a deep checkout the working-tree write can fail AFTER
    # the ref moved. Refuse BEFORE moving anything unless git's long-path support is on.
    if os.name == "nt":
        longest = max((len(os.path.join(repo, t.replace("/", os.sep))) for t in touched), default=0)
        rc, lp, _ = _git(repo, "config", "--get", "core.longpaths")
        if longest > 240 and lp.strip().lower() != "true":
            raise Refuse("a path this retirement writes is %d characters long and git's long-path "
                         "support is off on this Windows checkout -- the branch would move but the "
                         "working tree could not be written. FIX (once, your terminal): "
                         "`git config core.longpaths true`, then run this command again. Nothing "
                         "was published." % longest)
    if mode == "required":
        v = _trust.operator_tag(repo, tag, c)
        if not v["ok"]:
            raise Refuse("trust_surface_signing: required -- a verified operator tag is the only "
                         "authority. Inspect C with your own git, then:\n"
                         "    git tag -s retire/%d %s -m \"retire %d\"\n"
                         "and run this command again (or `py deploy/trust.py --publish retire/%d`)."
                         % (seq, c, seq, seq))
        say("  operator tag verified: %s" % v["reason"])
    else:
        # visible: write the promotion record (refuse to clobber an existing tag)
        if _trust._rev(repo, "refs/tags/%s" % tag) is not None or \
                subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "--quiet", "refs/tags/" + tag],
                               capture_output=True).returncode == 0:
            pr = _trust.promotion_tag(repo, tag, c)
            if not (pr["ok"] and pr["digest"] == full_digest):
                raise Refuse("a tag %s already exists and is not this proposal's promotion record "
                             "(%s) -- inspect and delete it by hand before promoting" % (tag, pr["reason"]))
            say("  promotion record already present: %s" % pr["reason"])
        else:
            msg = "promotion\nproposal_digest: sha256:%s\nmode: visible\n" % full_digest
            rc, out, err = _git(repo, "-c", "tag.gpgSign=false", "tag", "-a", "-m", msg, tag, c)
            if rc != 0:
                raise Refuse("could not write the promotion record: %s" % (err or out).strip())
            say("  promotion record written: tag %s -> %s (proposal_digest bound)" % (tag, c[:12]))
    chk = _trust.check_publishable(repo, tag, branch)
    if not chk["ok"]:
        raise Refuse("publisher refused: %s" % chk["reason"])
    rc, out, err = _git(repo, "update-ref", "refs/heads/%s" % branch, c, head)
    if rc != 0:
        raise Refuse("update-ref refused (the head moved?): %s" % (err or out).strip())
    # update the checked-out tree for the touched paths (HEAD is the branch, normally)
    rc, sym, _ = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    if rc == 0 and sym.strip() == branch:
        rc, out, err = _git(repo, "read-tree", "-m", "-u", head, c)
        if rc != 0:
            say("  NOTE: branch published but the working tree could not be updated: %s -- run "
                "`git checkout -- .` by hand" % (err or out).strip())
    _git(repo, "update-ref", "-d", "refs/retire/%d" % seq, c)  # the work ref is spent
    say("PUBLISHED: %s" % chk["reason"])
    say("It will appear in the next sweep's pending list until you have read it there.")
    return {"ok": True, "seq": seq, "commit": c, "tag": tag, "digest": full_digest, "mode": mode}


def self_test():
    import hashlib, json, shutil, tempfile
    failed = total = 0

    def case(name, cond, detail=""):
        nonlocal failed, total
        total += 1
        if not cond:
            failed += 1
        print("  %s %s%s" % ("ok " if cond else "XX ", name, ("  [%s]" % str(detail)[:300]) if detail and not cond else ""))

    if shutil.which("git") is None:
        print("promote.py self-test: INCONCLUSIVE -- git required")
        return 2
    base = tempfile.mkdtemp(prefix="promote-selftest-")
    try:
        r = os.path.join(base, "repo")
        os.makedirs(r)

        def git(*a):
            return subprocess.run(["git", "-C", r] + list(a), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")

        def write(rel, text):
            p = os.path.join(r, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)

        def commit(msg):
            git("add", "-A")
            p = git("commit", "-q", "-m", msg)
            assert p.returncode == 0, p.stderr
            return git("rev-parse", "HEAD").stdout.strip()

        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@t")
        git("config", "user.name", "tester")
        git("config", "commit.gpgsign", "false")
        DERIV = "# --- derivation (engine-managed; strip region) ---\nschema_version: 3.2\nview: topic\n# --- /derivation ---\n"
        write("wiki/topic/view.md", "# V\n\n## S1\n\nbody one.\n\n## S2\n\nbody two.\n\n" + DERIV)
        write("raw/2026-08-01-e.md", "# E\n")
        write("receipts/registrations/1.json", json.dumps({
            "kind": "registration", "seq": 1, "event": "raw/2026-08-01-e.md", "origin": "corpus",
            "origin_evidence": "fixture", "event_class": "compile", "event_class_origin": "explicit",
            "asserts_corpus_state": True, "registered_at": "2026-08-01T00:00:00", "prev_record_hash": None}))
        write("project.yaml", "trust_surface_signing: visible\n")
        commit("seed")
        res = _retire.propose(r, "wiki/topic/view.md", titles=["S1"])
        clean = {k: v for k, v in os.environ.items() if k not in SESSION_MARKERS}
        out = []
        try:
            promote(r, res["digest"], env={"CLAUDECODE": "1"}, say=out.append)
            case("in-session refused", False)
        except Refuse as e:
            case("inside an agent session (CLAUDECODE set) the promote action REFUSES and publishes "
                 "nothing", "agent session" in str(e) and _trust._rev(r, "refs/heads/main") != res["commit"], e)
        try:
            promote(r, res["digest"], env={"RHEOSCOPE_UNATTENDED": "1"}, say=out.append)
            case("unattended refused", False)
        except Refuse as e:
            case("an unattended run cannot promote either", "agent session" in str(e), e)
        try:
            promote(r, "deadbeefdeadbeef", env=clean, say=out.append)
            case("unknown digest refused", False)
        except Refuse as e:
            case("a digest that names no prepared retirement refuses (a chat 'yes' or a wrong "
                 "digest binds nothing)", "no prepared retirement" in str(e), e)
        try:
            promote(r, res["digest"][:6], env=clean, say=out.append)
            case("short prefix refused", False)
        except Refuse as e:
            case("a digest prefix under 12 hex chars refuses", "12 hex" in str(e), e)
        write("project.yaml", "trust_surface_signing: warn\n")
        try:
            promote(r, res["digest"], env=clean, say=out.append)
            case("warn refused", False)
        except Refuse as e:
            case("under migration-only warn the promote action refuses (retirement disabled)",
                 "retirement disabled" in str(e), e)
        write("project.yaml", "trust_surface_signing: required\n")
        try:
            promote(r, res["digest"], env=clean, say=out.append)
            case("required without tag refused", False)
        except Refuse as e:
            case("under required it prints the exact `git tag -s` to run and refuses to publish "
                 "unsigned (sk path unchanged)", "git tag -s retire/1" in str(e), e)
        write("project.yaml", "trust_surface_signing: visible\n")
        write("wiki/topic/view.md", "dirty\n")
        try:
            promote(r, res["digest"], env=clean, say=out.append)
            case("dirty worktree refused", False)
        except Refuse as e:
            case("a dirty working tree on a touched path refuses before anything is written",
                 "uncommitted changes" in str(e) and _trust._rev(r, "refs/tags/retire/1") is None, e)
        git("checkout", "--", "wiki/topic/view.md")
        out = []
        pub = promote(r, res["digest"][:16], env=clean, say=out.append)
        case("visible: a 16-char digest prefix promotes -- promotion record written, branch "
             "fast-forwarded to C, worktree updated",
             pub["ok"] and _trust._rev(r, "refs/heads/main") == res["commit"]
             and _trust.promotion_tag(r, "retire/1", res["commit"])["ok"]
             and "> Retired to" in open(os.path.join(r, "wiki/topic/view.md"), encoding="utf-8").read()
             and git("status", "--porcelain").stdout.strip() == "", (pub, out))
        case("the display named the view, the span, the bytes and the digest before publishing",
             any("span 'S1'" in l for l in out) and any("digest sha256:" in l for l in out), out)
        rr = _trust.retire_records_status(r, "main")
        case("the honest reader reads it PUBLISHED (promoted)", rr and rr[0]["published"] and rr[0]["kind"] == "promoted", rr)
        try:
            promote(r, res["digest"], env=clean, say=out.append)
            case("re-promote refused", False)
        except Refuse as e:
            case("promoting the same digest again refuses (no prepared retirement / consumed)",
                 "no prepared" in str(e) or "CONSUMED" in str(e), e)
        # replay: re-prepare a proposal with the same digest is impossible (parent differs);
        # a forged prepared C carrying a consumed digest is refused by the publisher
        res2 = _retire.propose(r, "wiki/topic/view.md", titles=["S2"])
        write("wiki/topic/view.md", open(os.path.join(r, "wiki/topic/view.md"), encoding="utf-8").read().replace("body two.", "body two, absorbed."))
        commit("intervening absorb")
        try:
            promote(r, res2["digest"], env=clean, say=out.append)
            case("stale refused", False)
        except Refuse as e:
            case("a STALE prepared C (branch moved) refuses and points at --recover",
                 "STALE" in str(e) and "--recover" in str(e), e)
        _retire.recover(r)
        case("nothing left prepared after recovery", _retire._prepared(r) == [])
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("promote.py self-test: %s (%d/%d)" % ("FAIL" if failed else "PASS", total - failed, total))
    return 1 if failed else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="promote.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("digest", nargs="?", help="proposal digest (>= 12 hex chars)")
    ap.add_argument("--root", default=".")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.digest:
        ap.print_help()
        return 2
    try:
        promote(os.path.abspath(a.root), a.digest, a.branch)
        return 0
    except Refuse as e:
        print("REFUSED: %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
