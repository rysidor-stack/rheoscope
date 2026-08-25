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
import json
import os
import re
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


def _find_batch(repo, digest):
    """The prepared batch whose manifest digest starts with `digest`, or None."""
    d = (digest or "").strip().lower()
    d = d[7:] if d.startswith("sha256:") else d
    hits = []
    for bid, _ref in _retire._prepared_batches(repo):
        bd = _retire.batch_digest(repo, bid)
        if bd and bd.startswith(d):
            hits.append(bid)
    if len(hits) > 1:
        raise Refuse("batch digest prefix %s is ambiguous (%d prepared batches)" % (d[:12], len(hits)))
    return hits[0] if hits else None


def _cleanup_batch_refs(repo, bid, manifest, say):
    refs = ["refs/retire/%d" % r.get("seq") for r in (manifest or {}).get("members") or []]
    refs.append("refs/retire/batch/%s" % bid)
    for ref in refs:
        c = _trust._rev(repo, ref)
        if c:
            _git(repo, "update-ref", "-d", ref, c)


def promote_batch(repo, bid, branch, env, say, halt_after=None):
    """v3.0.52: ONE operator action publishes a whole prepared batch -- the amended
    condition 4's locked wording made mechanical. halt_after=K is the BRAKE's slow-down:
    only the first K views publish (each atomically); the remainder's refs are DELETED
    and its half of the batch digest can never publish (the manifest binds the old head)
    -- refused, never half-applied. Under `required` the operator signs ONE tag on the
    binder M; under `visible` this writes the batch promotion record."""
    ok, reason, manifest, chain = _retire.verify_batch(repo, bid)
    if not ok:
        raise Refuse("prepared batch %s does not re-derive from git objects: %s -- run "
                     "`py deploy/retire.py --recover`" % (bid, reason))
    head = _trust._rev(repo, "refs/heads/%s" % branch)
    if manifest.get("parent_head") != head:
        raise Refuse("batch %s is STALE or PARTIALLY PUBLISHED (prepared on %s, %s is now "
                     "%s) -- the remainder is refused; run `py deploy/retire.py --recover` "
                     "and re-propose" % (bid, str(manifest.get("parent_head"))[:12], branch,
                                         (head or "?")[:12]))
    if halt_after is not None and int(halt_after) < 1:
        raise Refuse("--halt-after must be >= 1 (to publish nothing, simply run "
                     "`py deploy/retire.py --recover` instead)")
    M = _trust._rev(repo, "refs/retire/batch/%s" % bid)
    mb = _trust._blob_at(repo, M, "deploy/rulings/retire-batch-%s/manifest.json" % bid)
    import hashlib as _hl
    full_digest = _hl.sha256(mb).hexdigest()
    tag = "retire/batch/%s" % bid
    say("batch %s -- binder %s on %s (parent %s), %d member view(s):" % (
        bid, M[:12], branch, head[:12], len(chain)))
    for row in manifest.get("members") or []:
        say("  seq %s %s view %s: %s (%d bytes retiring)" % (
            row.get("seq"), str(row.get("commit"))[:12], row.get("view"),
            ", ".join(repr(s) for s in row.get("spans") or []), row.get("bytes") or 0))
    say("  BATCH digest sha256:%s" % full_digest)
    say("  full preimages: py deploy/retire.py --show <member digest> (each proposal)")
    touched = set()
    for ci in chain + [M]:
        rc, ns, _ = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r",
                         ci + "^", ci)
        touched.update(l.strip() for l in ns.splitlines() if l.strip())
    rc, st, _ = _git(repo, "status", "--porcelain", "--", *sorted(touched)) if touched         else (0, "", "")
    if st.strip():
        raise Refuse("working tree has uncommitted changes on paths this batch touches:\n"
                     "%s\ncommit or discard them first; nothing was published" % st.strip())
    if os.name == "nt":
        longest = max((len(os.path.join(repo, t.replace("/", os.sep))) for t in touched),
                      default=0)
        rc, lp, _ = _git(repo, "config", "--get", "core.longpaths")
        if longest > 240 and lp.strip().lower() != "true":
            raise Refuse("a path this batch writes is %d characters long and git's "
                         "long-path support is off -- FIX (once): `git config "
                         "core.longpaths true`, then re-run. Nothing was published."
                         % longest)
    mode, _why = _trust.signing_mode(repo)
    if mode == "required":
        v = _trust.operator_tag(repo, tag, M)
        if not v["ok"]:
            raise Refuse("trust_surface_signing: required -- ONE verified operator tag on "
                         "the binder publishes the whole batch. Inspect the chain with "
                         "your own git, then:\n    git tag -s %s %s -m \"batch %s\"\n"
                         "and run this command again." % (tag, M, bid))
        say("  operator tag verified: %s" % v["reason"])
    else:
        if _trust._rev(repo, "refs/tags/%s" % tag) is not None:
            pr = _trust.promotion_tag(repo, tag, M)
            if not (pr["ok"] and pr["digest"] == full_digest):
                raise Refuse("a tag %s already exists and is not this batch's promotion "
                             "record (%s) -- inspect and delete it by hand" % (tag, pr["reason"]))
            say("  promotion record already present")
        else:
            msg = "promotion\nproposal_digest: sha256:%s\nmode: visible\n" % full_digest
            rc, out, err = _git(repo, "-c", "tag.gpgSign=false", "tag", "-a", "-m", msg,
                                tag, M)
            if rc != 0:
                raise Refuse("could not write the batch promotion record: %s"
                             % (err or out).strip())
            say("  promotion record written: tag %s -> %s (batch digest bound)" % (tag, M[:12]))
    pub = _trust.publish_batch(repo, tag, branch, halt_after=halt_after)
    if not pub.get("ok") and not pub.get("published"):
        raise Refuse("publisher refused: %s" % pub["reason"])
    rc, sym, _ = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    if rc == 0 and sym.strip() == branch:
        rc, out, err = _git(repo, "read-tree", "-m", "-u", head, pub.get("tip") or
                            (pub["published"][-1]["commit"] if pub.get("published") else head))
        if rc != 0:
            say("  NOTE: branch published but the working tree could not be updated: %s -- "
                "run `git checkout -- .` by hand" % (err or out).strip())
    _cleanup_batch_refs(repo, bid, manifest, say)
    say(pub["reason"] if pub.get("ok") else "PARTIAL: " + pub["reason"])
    for row in pub.get("published") or []:
        say("  PUBLISHED seq %s -> %s (%s)" % (row["seq"], str(row["commit"])[:12],
                                               row["view"]))
    if pub.get("halted"):
        say("  BRAKE: remaining member(s) refused -- their refs are deleted; re-propose "
            "what should still retire and promote it freshly.")
    say("Every published item appears in the next sweep's pending list until you read it there.")
    return {"ok": True, "batch": bid, "digest": full_digest, "published": pub.get("published"),
            "halted": bool(pub.get("halted")), "mode": mode}


def rollback(repo, digest, branch=None, env=None, say=print, last=None):
    """v3.0.52: the BRAKE's undo, from the operator's terminal only. Names the EXACT
    digest of what published (a batch's manifest digest, or a single retirement's
    proposal digest); each named view is restored ATOMICALLY (one inverse commit per
    view, re-derived from git objects; cold objects remain; the redirect chain
    advances). A batch rolls back its published members in reverse order; --last N
    limits it to the most recently published N."""
    markers = in_agent_session(env)
    if markers:
        raise Refuse("refusing inside an agent session (%s set). Rollback is the "
                     "operator's brake, from their own terminal. Nothing was rolled back."
                     % ", ".join(markers))
    if not _trust.is_git_repo(repo):
        raise Refuse("%s is not a git repository" % repo)
    try:
        branch = _trust.resolve_branch(repo, branch)
    except _trust.TrustError as e:
        raise Refuse(str(e))
    if not _trust.mode_chosen(repo):
        raise Refuse("retirement disabled: " + _trust.ABSENT_MODE_NOTE)
    head = _trust._rev(repo, "refs/heads/%s" % branch)
    d = (digest or "").strip().lower()
    d = d[7:] if d.startswith("sha256:") else d
    if len(d) < 12:
        raise Refuse("give at least 12 hex characters of the digest that published")
    import hashlib as _hl
    seqs = []
    # a batch: find its promotion/signed tag, match the manifest digest
    rc, out, _ = _git(repo, "for-each-ref", "--format=%(refname:short)",
                      "refs/tags/retire/batch/")
    for tname in out.split():
        m = re.match(r"^retire/batch/(\d+-\d+)$", tname.strip())
        if not m:
            continue
        try:
            t = _trust.tag_object(repo, tname.strip())
        except _trust.TrustError:
            continue
        mb = _trust._blob_at(repo, t["object"],
                             "deploy/rulings/retire-batch-%s/manifest.json" % m.group(1))
        if mb is None or not _hl.sha256(mb).hexdigest().startswith(d):
            continue
        manifest = json.loads(mb.decode("utf-8-sig"))
        rc2, fp, _ = _git(repo, "rev-list", "--first-parent", head)
        on_branch = set(fp.split())
        seqs = [row.get("seq") for row in manifest.get("members") or []
                if row.get("commit") in on_branch]
        seqs.reverse()
        say("batch %s: %d published member(s) to roll back (reverse order)"
            % (m.group(1), len(seqs)))
        break
    if not seqs:
        # a single published retirement by proposal digest
        for p, sha, r in _trust._retire_records_history(repo, head):
            if _trust._digest_hex(r.get("proposal_digest", "")).startswith(d)                     and r.get("rollback_of") is None:
                seqs = [r.get("seq")]
                break
    if not seqs:
        raise Refuse("digest %s.. names nothing published on %s" % (d[:12], branch))
    if last is not None:
        seqs = seqs[:max(0, int(last))]
    done = []
    for seq in seqs:
        try:
            res = _retire.prepare_rollback(repo, seq, branch)
        except _retire.Refuse as e:
            say("  seq %s NOT rolled back: %s" % (seq, e))
            break  # atomic per view: stop at the first refusal, done stays done
        okv, reasonv, _recv = _retire.verify_prepared(repo, res["commit"])
        if not okv:
            _git(repo, "update-ref", "-d", "refs/retire/%d" % res["seq"], res["commit"])
            say("  seq %s NOT rolled back (inverse did not re-derive: %s)" % (seq, reasonv))
            break
        mode, _why = _trust.signing_mode(repo)
        if mode == "required":
            say("  under required, sign each inverse commit yourself:")
            say("    git tag -s %s %s -m \"rollback of %s\"" % (res["tag"], res["commit"], seq))
            say("  then: py deploy/trust.py --publish %s --branch %s" % (res["tag"], branch))
            break
        msg = "promotion\nproposal_digest: sha256:%s\nmode: visible\n" % res["digest"]
        rc, out, err = _git(repo, "-c", "tag.gpgSign=false", "tag", "-a", "-m", msg,
                            res["tag"], res["commit"])
        if rc != 0:
            raise Refuse("could not write the rollback promotion record: %s" % (err or out).strip())
        pub = _trust.publish_retirement(repo, res["tag"], branch)
        if not pub.get("ok"):
            raise Refuse("publisher refused the rollback: %s" % pub["reason"])
        rc, sym, _ = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
        old_head = head
        head = _trust._rev(repo, "refs/heads/%s" % branch)
        if rc == 0 and sym.strip() == branch:
            _git(repo, "read-tree", "-m", "-u", old_head, head)
        _git(repo, "update-ref", "-d", "refs/retire/%d" % res["seq"], res["commit"])
        say("  ROLLED BACK seq %s -> restore commit %s (%s)" % (seq, res["commit"][:12],
                                                                res["view"]))
        done.append({"of": seq, "commit": res["commit"], "view": res["view"]})
    say("%d view(s) restored; cold objects remain; the pending list shows each restore "
        "until the next sweep you read." % len(done))
    return {"ok": bool(done), "rolled_back": done}


def promote(repo, digest, branch=None, env=None, say=print, halt_after=None):
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
    # v3.0.52 (v3.0-151): the branch through the one home -- so the command the propose
    # output echoes (with --branch) and a bare invocation on the instance agree.
    try:
        branch = _trust.resolve_branch(repo, branch)
    except _trust.TrustError as e:
        raise Refuse(str(e))
    mode, why = _trust.signing_mode(repo)
    if not _trust.mode_chosen(repo):
        raise Refuse("retirement disabled: " + _trust.ABSENT_MODE_NOTE)
    bid = _find_batch(repo, digest)
    if bid is not None:
        return promote_batch(repo, bid, branch, env, say, halt_after=halt_after)
    if halt_after is not None:
        raise Refuse("--halt-after applies to a BATCH digest; %s.. names no prepared "
                     "batch" % str(digest)[:12])
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

        # v3.0.52 (v3.0-151): the battery repo lives on a NON-main branch and no call
        # below passes --branch -- the whole flow runs on resolved-branch defaults, which
        # is the fleet-inbox-#8 direction (a `dogfood/*` instance, no local main).
        git("init", "-q", "-b", "dogfood/fork-v3")
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
                 "nothing", "agent session" in str(e)
                 and _trust._rev(r, "refs/heads/dogfood/fork-v3") != res["commit"], e)
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
        case("visible (v3.0-151: on the resolved non-main branch, no flag passed): a 16-char "
             "digest prefix promotes -- promotion record written, branch fast-forwarded to C, "
             "worktree updated",
             pub["ok"] and _trust._rev(r, "refs/heads/dogfood/fork-v3") == res["commit"]
             and _trust.promotion_tag(r, "retire/1", res["commit"])["ok"]
             and "> Retired to" in open(os.path.join(r, "wiki/topic/view.md"), encoding="utf-8").read()
             and git("status", "--porcelain").stdout.strip() == "", (pub, out))
        case("the display named the view, the span, the bytes and the digest before publishing",
             any("span 'S1'" in l for l in out) and any("digest sha256:" in l for l in out), out)
        rr = _trust.retire_records_status(r, "dogfood/fork-v3")
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

        # -------- v3.0.52: the BATCH ceremony -- one action, N views; halt; rollback
        saved_scope = _retire.RELEASE_SCOPE
        _retire.RELEASE_SCOPE = "broad"
        try:
            write("wiki/topic/va.md", "# A\n\n## SA\n\nalpha body.\n\n"
                  "# --- derivation (engine-managed; strip region) ---\nschema_version: 3.2\n"
                  "view: topic\n# --- /derivation ---\n")
            write("wiki/topic/vb.md", "# B\n\n## SB\n\nbeta body.\n\n"
                  "# --- derivation (engine-managed; strip region) ---\nschema_version: 3.2\n"
                  "view: topic\n# --- /derivation ---\n")
            commit("two more views")
            spec = {"members": [{"view": "wiki/topic/va.md", "spans": ["SA"]},
                                {"view": "wiki/topic/vb.md", "spans": ["SB"]}]}
            bres = _retire.propose_batch(r, spec)
            case("batch: chain + binder prepared, ONE digest covers both views",
                 len(bres["members"]) == 2 and len(bres["digest"]) == 64
                 and _trust._rev(r, "refs/retire/batch/%s" % bres["batch"]) == bres["commit"],
                 bres)
            okb, reasonb, _mfb, _chb = _retire.verify_batch(r, bres["batch"])
            case("batch: re-derives from objects", okb, reasonb)
            out2 = []
            try:
                promote(r, bres["digest"], env={"CLAUDECODE": "1"}, say=out2.append)
                case("in-session batch promote refused", False)
            except Refuse as e:
                case("a batch promote inside an agent session REFUSES (the one action is "
                     "the operator's)", "agent session" in str(e), e)
            pubb = promote(r, bres["digest"][:16], env=clean, say=out2.append)
            head_now = _trust._rev(r, "refs/heads/dogfood/fork-v3")
            case("batch: ONE promote publishes both members + the binder, atomically per "
                 "view; refs cleaned; worktree updated",
                 pubb["ok"] and not pubb["halted"] and len(pubb["published"]) == 2
                 and head_now == bres["commit"] and _retire._prepared(r) == []
                 and _retire._prepared_batches(r) == []
                 and "> Retired to" in open(os.path.join(r, "wiki/topic/va.md"),
                                            encoding="utf-8").read()
                 and "> Retired to" in open(os.path.join(r, "wiki/topic/vb.md"),
                                            encoding="utf-8").read(), (pubb, out2))
            rrb = {x["seq"]: x for x in _trust.retire_records_status(r, "dogfood/fork-v3")}
            case("batch: the honest reader reads BOTH members PUBLISHED through the batch "
                 "authority (kind batch-promoted)",
                 all(rrb[m["seq"]]["published"] and rrb[m["seq"]]["kind"] == "batch-promoted"
                     for m in pubb["published"]), rrb)
            try:
                promote(r, bres["digest"], env=clean, say=out2.append)
                case("consumed batch digest re-promote refused", False)
            except Refuse as e:
                case("re-promoting a consumed batch digest refuses (nothing prepared "
                     "carries it)", "no prepared" in str(e) or "names no" in str(e), e)
            # ---- halt: a fresh 2-view batch, publish only the first view
            write("wiki/topic/vc.md", "# C\n\n## SC\n\ngamma body.\n\n"
                  "# --- derivation (engine-managed; strip region) ---\nschema_version: 3.2\n"
                  "view: topic\n# --- /derivation ---\n")
            write("wiki/topic/vd.md", "# D\n\n## SD\n\ndelta body.\n\n"
                  "# --- derivation (engine-managed; strip region) ---\nschema_version: 3.2\n"
                  "view: topic\n# --- /derivation ---\n")
            commit("two more views for the halt case")
            bres2 = _retire.propose_batch(r, {"members": [
                {"view": "wiki/topic/vc.md", "spans": ["SC"]},
                {"view": "wiki/topic/vd.md", "spans": ["SD"]}]})
            out3 = []
            pubh = promote(r, bres2["digest"], env=clean, say=out3.append, halt_after=1)
            vd_now = open(os.path.join(r, "wiki/topic/vd.md"), encoding="utf-8").read()
            case("BRAKE halt: --halt-after 1 publishes the first view completely, the "
                 "second not at all (never half-applied), remainder refs deleted",
                 pubh["halted"] and len(pubh["published"]) == 1
                 and "> Retired to" in open(os.path.join(r, "wiki/topic/vc.md"),
                                            encoding="utf-8").read()
                 and "gamma body." not in open(os.path.join(r, "wiki/topic/vc.md"),
                                               encoding="utf-8").read()
                 and "delta body." in vd_now and "> Retired to" not in vd_now
                 and _retire._prepared(r) == [] and _retire._prepared_batches(r) == [],
                 (pubh, vd_now))
            rrh = {x["seq"]: x for x in _trust.retire_records_status(r, "dogfood/fork-v3")}
            case("stranger-run fold: a HALTED batch's published member reads PUBLISHED "
                 "(the batch tag + manifest row bind it; the unpublished binder is not "
                 "an alarm)", rrh[pubh["published"][0]["seq"]]["published"]
                 and rrh[pubh["published"][0]["seq"]]["kind"] == "batch-promoted", rrh)
            try:
                promote(r, bres2["digest"], env=clean, say=out3.append)
                case("halted remainder re-promote refused", False)
            except Refuse as e:
                case("BRAKE halt: the remainder is REFUSED -- the consumed batch digest "
                     "publishes nothing further", "no prepared" in str(e) or "names no" in str(e), e)
            # ---- rollback: undo the halted batch's published member (view restored)
            out4 = []
            try:
                rollback(r, bres2["digest"], env={"CLAUDECODE": "1"}, say=out4.append)
                case("in-session rollback refused", False)
            except Refuse as e:
                case("rollback inside an agent session REFUSES", "agent session" in str(e), e)
            rb = rollback(r, bres2["digest"], env=clean, say=out4.append)
            vc_now = open(os.path.join(r, "wiki/topic/vc.md"), encoding="utf-8").read()
            case("BRAKE rollback: the published member's view is RESTORED atomically "
                 "(hot bytes back, stub gone), cold object remains, redirect chain "
                 "advanced",
                 rb["ok"] and len(rb["rolled_back"]) == 1
                 and "gamma body." in vc_now and "> Retired to" not in vc_now.split(
                     "# --- retirements")[0]
                 and any(x.endswith(".md") for x in os.listdir(os.path.join(
                     r, "wiki", "cold", "vc"))), (rb, vc_now[:200]))
            rrb2 = _trust.retire_records_status(r, "dogfood/fork-v3")
            case("rollback: the restore is itself a PUBLISHED retire record (promoted), "
                 "and the honest reader still reads the rolled-back member as published "
                 "history", any(x.get("published") and "ROLLBACK" not in str(x.get("reason"))
                                for x in rrb2), rrb2)
            resolved = _retire.resolve(r, "vc.md:5@%s" % _retire.gen_hash(
                "# C\n\n## SC\n\ngamma body.\n\n"
                "# --- derivation (engine-managed; strip region) ---\nschema_version: 3.2\n"
                "view: topic\n# --- /derivation ---\n")[:8])
            case("rollback: a citation tagged with the ORIGINAL generation still resolves "
                 "through stub-then-rollback to the restored view", resolved["resolved"]
                 and resolved["kind"] == "view", resolved)
        finally:
            _retire.RELEASE_SCOPE = saved_scope
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("promote.py self-test: %s (%d/%d)" % ("FAIL" if failed else "PASS", total - failed, total))
    return 1 if failed else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="promote.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("digest", nargs="?", help="proposal digest OR batch digest (>= 12 hex)")
    ap.add_argument("--halt-after", type=int, default=None, metavar="N",
                    help="the brake: publish only the first N views of a batch; the "
                         "remainder is refused (v3.0.52)")
    ap.add_argument("--rollback", metavar="DIGEST",
                    help="the brake's undo: restore the view(s) a published batch or "
                         "single retirement moved, atomically per view (v3.0.52)")
    ap.add_argument("--last", type=int, default=None, metavar="N",
                    help="with --rollback on a batch: only the N most recently published "
                         "members")
    ap.add_argument("--root", default=".")
    ap.add_argument("--branch", default=None,
                    help="production branch (default: project.yaml production_branch, "
                         "else the checked-out branch -- v3.0-151)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    try:
        if a.rollback:
            rb = rollback(os.path.abspath(a.root), a.rollback, a.branch, last=a.last)
            # round-2 fold (c2): a rollback that restored nothing (refused member, or
            # required-mode manual instructions printed) exits refusal-shaped
            return 0 if rb.get("ok") else 2
        if not a.digest:
            ap.print_help()
            return 2
        promote(os.path.abspath(a.root), a.digest, a.branch, halt_after=a.halt_after)
        return 0
    except Refuse as e:
        print("REFUSED: %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
