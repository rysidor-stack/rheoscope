#!/usr/bin/env python3
"""check-verified-reset.py -- CONTENT-3 VERIFIED-RESET sensor (P3).

The engine's own rebuilds already reset verified-status through the journal
(compile-v2.verify_run stamps noop_candidates with a view_sha256 pin at
verify time; a later absorb/verify record legitimately re-covers a view).
That in-engine path is governed. The uncovered risk this sensor closes: a
view file edited OUTSIDE the engine (manual edit, another tool) AFTER a
verify stamped it -- the stale verified status must not keep being served.

MECHANICS
  Walk receipts/journal/<seq>.json in seq order. For every noop_candidate
  with verified=true, its justification carries a view_sha256 pin (the body
  hash of nc["view"] at verify time) and, since the 2026-07-05 verify-unit
  amendment, a union_view_sha256 map (all routed views' hashes for that
  event's verify packet, keyed by view path). For each view V, the LAST
  verified stamp in seq order (either a direct view_sha256 pin naming V, or
  a union_view_sha256[V] entry from a hub-event packet that routed through
  V) is that view's CURRENT authoritative pin -- ANY later stamp for the
  same view (even from the same or a following record) supersedes an
  earlier one, so only the last-by-seq pin per view is checked.

  STALENESS RULE (exact): view V's last stamp (seq=S, pinned sha=P) is
  STALE iff sha256(current body of V) != P. "Current body" is read from the
  worktree if the file exists on disk, else falls back to the blob at HEAD
  (so the sensor also works against a bare/checked-out ref without an
  edited worktree). There is no forward-looking exemption beyond "last
  stamp wins" -- if a later verify record re-covers V at a NEW sha, that
  later stamp is what gets compared (the rule is inherently precedence-
  correct because only the last stamp survives into the comparison set;
  earlier stamps for the same view are simply superseded and never flagged
  on their own).

  FAIL-CLOSED: any verified=true noop_candidate whose justification is
  missing/unparseable pins (no view_sha256 for its own view AND no
  union_view_sha256 map, or a present map with no entry for its own view)
  makes the WHOLE run inconclusive (exit 2) -- corrupt pins are never
  silently treated as clean.

  ABSORPTION-VERIFY STAMPS (2026-07-05 amendment): a CONFIRMed absorption
  is a per-view unit -- it never creates a noop_candidates entry at all, so
  its pin lives on the record's OWN `absorption_verified[]` list instead:
  {view, events, view_sha256, ...}. This is a SEPARATE code path from the
  noop_candidates walk above (not an extension of it -- the two mechanisms
  produce differently-shaped journal entries), but folds into the exact
  same `stamps` map under the same view key, so the "last stamp by seq
  wins" precedence rule applies uniformly regardless of which mechanism
  (no-op union verify or absorption verify) produced the winning pin. A
  malformed absorption_verified[] entry (missing view or view_sha256) is
  fail-closed inconclusive, same discipline as a malformed noop_candidate.

Usage:
  check-verified-reset.py --self-test
  check-verified-reset.py --check --root DIR [--json PATH]
Exit: 0 clean | 1 violation(s) found | 2 inconclusive (corrupt/absent pins,
      unreadable journal, or repo/journal missing).
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(basename, alias):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        alias, os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _load("compile-core.py", "compile_core_v3reset")


def _sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git(repo, *args):
    p = subprocess.run(["git", "-C", repo] + list(args), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        return None
    return p.stdout


def _current_body(repo, view_rel):
    """Worktree body if present on disk, else HEAD blob. None if neither
    exists (view was deleted -- treated as inconclusive by the caller, not
    silently clean)."""
    p = os.path.join(repo, view_rel.replace("/", os.sep))
    if os.path.isfile(p):
        with open(p, encoding="utf-8", newline="") as fh:
            return fh.read()
    out = _git(repo, "show", "HEAD:%s" % view_rel)
    return out


def _iter_journal_records(repo):
    """Yields (seq, record) in seq order. Raises ValueError on unreadable/
    malformed journal (caller maps to exit 2)."""
    jd = core.journal_dir(repo)
    if not os.path.isdir(jd):
        return
    names = [f for f in os.listdir(jd) if f.endswith(".json")]
    seqs = []
    for f in names:
        stem = f[:-5]
        if not stem.isdigit():
            raise ValueError("non-numeric journal filename: %s" % f)
        seqs.append(int(stem))
    for seq in sorted(seqs):
        p = os.path.join(jd, "%d.json" % seq)
        try:
            with open(p, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError) as e:
            raise ValueError("unreadable journal record %d: %s" % (seq, e))
        yield seq, rec


def collect_last_stamps(repo):
    """Returns (stamps, inconclusive_reasons).

    stamps: {view_rel: {"seq": int, "event": str, "sha256": str}} -- the
    LAST (highest-seq) verified stamp per view, walking records in seq
    order so later stamps naturally overwrite earlier ones for the same
    view (precedence: last stamp by seq wins).

    inconclusive_reasons: list[str] -- non-empty means the caller must
    exit 2 regardless of what stamps/violations were found (fail-closed:
    a corrupt pin anywhere makes the whole picture untrustworthy).
    """
    stamps = {}
    reasons = []
    try:
        records = list(_iter_journal_records(repo))
    except ValueError as e:
        return {}, [str(e)]

    for seq, rec in records:
        ncs = rec.get("noop_candidates")
        if not isinstance(ncs, list):
            continue
        for idx, nc in enumerate(ncs):
            if not isinstance(nc, dict) or not nc.get("verified"):
                continue
            view = nc.get("view")
            event = nc.get("event")
            just = nc.get("justification")
            tag = "seq %d noop_candidates[%d] (view=%r, event=%r)" % (
                seq, idx, view, event)
            if not view or not isinstance(just, dict):
                reasons.append("%s: verified=true but missing view or "
                               "justification block" % tag)
                continue
            pin = None
            vsha = just.get("view_sha256")
            if just.get("view") == view and isinstance(vsha, str) and vsha:
                # direct pin covers this candidate's own view
                pin = vsha
            elif isinstance(vsha, str) and vsha and "union_view_sha256" not in just:
                # legacy pre-union records: view_sha256 pins nc's own view
                pin = vsha
            uvs = just.get("union_view_sha256")
            if pin is None and isinstance(uvs, dict):
                pin = uvs.get(view)
            if pin is None and isinstance(vsha, str) and vsha and view not in (uvs or {}):
                # union map present but doesn't name this view: fall back to
                # the direct view_sha256 pin (still covers nc's own view)
                pin = vsha
            if not pin:
                reasons.append("%s: verified=true but no usable sha256 pin "
                               "(view_sha256 absent, union_view_sha256 "
                               "absent/missing this view)" % tag)
                continue
            stamps[view] = {"seq": seq, "event": event, "sha256": pin}
        # absorption-verify stamps (2026-07-05 amendment): a CONFIRMed
        # absorption pins the view's post-absorb body sha256 directly on
        # absorption_verified[] (no noop_candidates entry is ever created
        # for an absorption -- it is a per-view unit, not a per-(event,view)
        # candidate). Same "last stamp by seq wins" precedence: walking
        # records in seq order means a later absorption OR no-op stamp for
        # the same view naturally overwrites an earlier one, regardless of
        # which of the two mechanisms produced it.
        avs = rec.get("absorption_verified")
        if isinstance(avs, list):
            for idx, av in enumerate(avs):
                if not isinstance(av, dict):
                    continue
                view = av.get("view")
                vsha = av.get("view_sha256")
                events = av.get("events")
                tag = "seq %d absorption_verified[%d] (view=%r)" % (
                    seq, idx, view)
                if not view or not isinstance(vsha, str) or not vsha:
                    reasons.append("%s: absorption stamp missing view or "
                                   "view_sha256 pin" % tag)
                    continue
                event_field = (sorted(events)[0] if isinstance(events, list)
                              and events else None)
                stamps[view] = {"seq": seq, "event": event_field,
                                "sha256": vsha}
    return stamps, reasons


def check(repo):
    """Returns (violations, inconclusive_reasons)."""
    stamps, reasons = collect_last_stamps(repo)
    if reasons:
        return [], reasons
    violations = []
    for view, stamp in sorted(stamps.items()):
        body = _current_body(repo, view)
        if body is None:
            reasons.append("view %r pinned at seq %d no longer exists in "
                           "worktree or HEAD" % (view, stamp["seq"]))
            continue
        current_sha = _sha256_text(body)
        if current_sha != stamp["sha256"]:
            violations.append({
                "view": view,
                "event": stamp["event"],
                "stamped_seq": stamp["seq"],
                "stamped_sha256": stamp["sha256"],
                "current_sha256": current_sha,
            })
    return violations, reasons


# --------------------------------------------------------------------- self-test
def _mkrepo(base):
    for args in (["init", "-q"], ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", base] + args, capture_output=True)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _commit_all(base, msg):
    subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", base, "commit", "-qm", msg], capture_output=True)


def _append_verify_record(base, view, event, sha256_val, seq_hint=None,
                          use_union=True, corrupt=None):
    rec = core.minimal_record("verify", "sha")
    just = {"event_sha256": "e" * 64}
    if corrupt == "no_pins":
        pass  # neither view_sha256 nor union_view_sha256
    elif corrupt == "union_missing_view":
        just["union_view_sha256"] = {"wiki/other.md": "z" * 64}
    else:
        if use_union:
            just["union_view_sha256"] = {view: sha256_val}
        else:
            just["view_sha256"] = sha256_val
            just["view"] = view
    rec["noop_candidates"] = [{
        "view": view, "event": event, "verified": True,
        "artifact": "receipts/verify/x.json", "packet_sha256": "p" * 8,
        "verified_at": "2026-07-05T00:00:00",
        "justification": just,
    }]
    seq, _path = core.append_record(base, rec)
    _commit_all(base, "verify seq %d" % seq)
    return seq


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

    base = tempfile.mkdtemp(prefix="cvr-")
    try:
        _mkrepo(base)
        view_path = os.path.join(base, "wiki", "a.md")
        _write(view_path, "# A\n\nbody one\n")
        _commit_all(base, "seed")

        # (a) verify-stamped view untouched -> clean
        body = open(view_path, encoding="utf-8").read()
        sha = _sha256_text(body)
        _append_verify_record(base, "wiki/a.md", "raw/e1.md", sha)
        violations, reasons = check(base)
        case("(a) untouched verified view: clean, no violations",
             violations == [] and reasons == [])

        # (b) manual edit after stamp -> flagged with correct seq/sha pair
        with open(view_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("manual edit outside engine\n")
        violations, reasons = check(base)
        case("(b) manual edit after stamp: flagged, no inconclusive",
             len(violations) == 1 and reasons == [])
        if violations:
            v = violations[0]
            case("(b) violation names correct view/event/seq/stamped sha",
                 v["view"] == "wiki/a.md" and v["event"] == "raw/e1.md"
                 and v["stamped_seq"] == 1 and v["stamped_sha256"] == sha)
            new_body = open(view_path, encoding="utf-8").read()
            case("(b) violation's current sha matches worktree body now",
                 v["current_sha256"] == _sha256_text(new_body)
                 and v["current_sha256"] != sha)

        # (c) manual edit then a LATER verify record re-covering the view at
        # the new sha -> clean
        new_body = open(view_path, encoding="utf-8").read()
        new_sha = _sha256_text(new_body)
        _append_verify_record(base, "wiki/a.md", "raw/e1.md", new_sha)
        violations, reasons = check(base)
        case("(c) later re-verify at new sha: clean", violations == []
             and reasons == [])

        # union_view_sha256 path: hub event routes through two views, one
        # of which is later manually edited
        view2_path = os.path.join(base, "wiki", "b.md")
        _write(view2_path, "# B\n\nbody two\n")
        _commit_all(base, "add b")
        b_body = open(view2_path, encoding="utf-8").read()
        b_sha = _sha256_text(b_body)
        rec = core.minimal_record("verify", "sha")
        rec["noop_candidates"] = [
            {"view": "wiki/a.md", "event": "raw/hub.md", "verified": True,
             "artifact": "x", "packet_sha256": "p",
             "justification": {"union_view_sha256":
                                {"wiki/a.md": new_sha, "wiki/b.md": b_sha}}},
            {"view": "wiki/b.md", "event": "raw/hub.md", "verified": True,
             "artifact": "x", "packet_sha256": "p",
             "justification": {"union_view_sha256":
                                {"wiki/a.md": new_sha, "wiki/b.md": b_sha}}},
        ]
        seq, _p = core.append_record(base, rec)
        _commit_all(base, "hub verify seq %d" % seq)
        with open(view2_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("manual edit on b\n")
        violations, reasons = check(base)
        case("union_view_sha256 hub packet: b.md edit flagged, a.md clean",
             reasons == [] and len(violations) == 1
             and violations[0]["view"] == "wiki/b.md")

        # absorption-verify stamp (2026-07-05 amendment): a LATER absorption
        # pin for wiki/a.md (view_sha256 on absorption_verified[], not
        # noop_candidates) must SUPERSEDE the earlier no-op union pin at a
        # lower seq -- the same "last stamp by seq wins" rule, across the
        # two different journal shapes.
        a_body_post_absorb = "# A\n\nbody one edited by absorption\n"
        _write(os.path.join(base, "wiki", "a.md"), a_body_post_absorb)
        _commit_all(base, "absorb a.md")
        absorb_sha = _sha256_text(a_body_post_absorb)
        rec_abs = core.minimal_record("verify", "sha")
        rec_abs["absorption_verified"] = [
            {"view": "wiki/a.md", "events": ["raw/e2.md"],
             "verified_at": "2026-07-05T00:00:00",
             "artifact": "receipts/verify/absorb-seq%d-v0.json" % (seq + 1),
             "packet_sha256": "d" * 64, "view_sha256": absorb_sha,
             "substrate": {}}]
        abs_seq, _p2 = core.append_record(base, rec_abs)
        _commit_all(base, "absorption verify seq %d" % abs_seq)
        violations, reasons = check(base)
        case("absorption-verify stamp: current body matches the new pin -> "
             "clean (a.md no longer flagged even though an earlier "
             "no-op union pin named it at a different sha)",
             reasons == [] and not any(v["view"] == "wiki/a.md"
                                       for v in violations))

        # manual edit AFTER the absorption stamp -> flagged against the
        # absorption pin (the LATEST one), not the earlier no-op pin
        with open(os.path.join(base, "wiki", "a.md"), "a", encoding="utf-8",
                  newline="\n") as fh:
            fh.write("manual edit after absorption stamp\n")
        violations, reasons = check(base)
        a_violations = [v for v in violations if v["view"] == "wiki/a.md"]
        case("absorption-verify stamp supersedes the earlier no-op pin: "
             "manual edit after the absorption stamp is flagged against "
             "the absorption seq/sha, not the stale no-op one",
             reasons == [] and len(a_violations) == 1
             and a_violations[0]["stamped_seq"] == abs_seq
             and a_violations[0]["stamped_sha256"] == absorb_sha)

        # (d) corrupt/absent justification pins -> inconclusive exit 2
        base2 = tempfile.mkdtemp(prefix="cvr2-")
        try:
            _mkrepo(base2)
            _write(os.path.join(base2, "wiki", "c.md"), "# C\nbody\n")
            _commit_all(base2, "seed")
            _append_verify_record(base2, "wiki/c.md", "raw/e1.md", "irrelevant",
                                  corrupt="no_pins")
            violations2, reasons2 = check(base2)
            case("(d) missing pins: inconclusive (reasons non-empty, no "
                 "silent-clean)", violations2 == [] and len(reasons2) == 1)
        finally:
            shutil.rmtree(base2, ignore_errors=True)

        base3 = tempfile.mkdtemp(prefix="cvr3-")
        try:
            _mkrepo(base3)
            _write(os.path.join(base3, "wiki", "d.md"), "# D\nbody\n")
            _commit_all(base3, "seed")
            _append_verify_record(base3, "wiki/d.md", "raw/e1.md", "irrelevant",
                                  corrupt="union_missing_view")
            violations3, reasons3 = check(base3)
            case("(d) union map present but missing this view: inconclusive",
                 violations3 == [] and len(reasons3) == 1)
        finally:
            shutil.rmtree(base3, ignore_errors=True)

        # CLI wiring smoke test: --check --root, exit codes
        rc = main(["--check", "--root", base])
        case("CLI --check callable, returns int exit code (STALE=1 here, "
             "since base has the pending wiki/b.md violation above)",
             rc == 1)

    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failed:
        print("check-verified-reset (CONTENT-3): FAIL (%d/%d)"
              % (total - failed, total))
        return 1
    print("check-verified-reset (CONTENT-3): PASS (%d/%d)" % (total, total))
    return 0


# --------------------------------------------------------------------- CLI
def main(argv):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    if not args.check:
        print(__doc__.strip().split("\n\n")[-1])
        return 2

    root = args.root
    if not os.path.isdir(root):
        print("INCONCLUSIVE: root does not exist: %s" % root)
        return 2
    jd = core.journal_dir(root)
    if not os.path.isdir(jd):
        print("INCONCLUSIVE: no journal directory at %s" % jd)
        return 2

    violations, reasons = check(root)
    receipt = {"root": os.path.abspath(root), "violations": violations,
              "inconclusive_reasons": reasons}
    if args.json:
        with open(args.json, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(receipt, fh, indent=1, sort_keys=True)

    if reasons:
        for r in reasons:
            print("INCONCLUSIVE: %s" % r)
        print("check-verified-reset: INCONCLUSIVE (%d reason(s))"
              % len(reasons))
        return 2
    for v in violations:
        print("VIOLATION: view=%s event=%s stamped_seq=%d "
              "stamped_sha256=%s current_sha256=%s"
              % (v["view"], v["event"], v["stamped_seq"],
                 v["stamped_sha256"], v["current_sha256"]))
    print("check-verified-reset: %s (%d violation(s))"
          % ("CLEAN" if not violations else "STALE", len(violations)))
    return 0 if not violations else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
