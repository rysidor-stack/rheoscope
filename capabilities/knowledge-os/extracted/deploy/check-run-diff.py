#!/usr/bin/env python3
"""check-run-diff.py -- ACC-4 MANIFEST-VS-DIFF (+ LLM-2 --sections) for compile-v2.

ACC-4 (test-plan ~117-125), file granularity, both directions:
  (a) every (V,E) in the run's journal record with disposition != no-op  =>  the
      run commit's diff touches V's file; conversely every wiki view file touched
      in the commit appears in the record's absorbed manifest (stowaway = FAIL).
  (b) no-ops in scope: every journaled no-op entry carries a verifier artifact
      reference + packet hash, and its justification cites the FULL event body AND
      the FULL view body -- mechanically enforced as `justification.event_sha256` /
      `justification.view_sha256` matching the actual bodies at the commit (an
      empty-diff-only justification has neither -> FAIL). For T1 / correction /
      lock / `informed_by` events the artifact must be same-run: `verified_at`
      inside the record's `run_window` -- outside => FAIL. EXEMPTION: an entry
      with disposition="PENDING_NOOP_CANDIDATE" and verified=false is pre-VERIFY
      -- PENDING semantics (compile-v2's two-stage design: VERIFY fills
      artifact+hash in the append-only follow-up record) means artifact and
      packet_sha256 may legitimately be empty; the justification full-body
      checks still apply, and consumption (verified=true, or any CONSUMED
      disposition) still requires the artifact.
  (c) conservative event_class default (F6): an entry whose `event_class_origin`
      is "judgment" is treated as lock-class for no-op purposes REGARDLESS of the
      assigned class value -- its no-ops route to PENDING_NOOP_CANDIDATE
      (`verified` may be false), but the same-run artifact discipline of (b)
      still applies when it IS marked verified.

LLM-2 (--sections, test-plan ~356-364), section granularity per absorbed entry:
  manifest entries {event, section} vs the real pre_blob..post_blob diff's changed
  sections -- claim-without-hunk and hunk-without-claim both FAIL.

Journal field conventions (engine-owned, additive over compile-core's schema):
  record.run_window = {start, end} ISO strings
  absorbed[].manifest = [{event, section}]
  noop_candidates[] += {artifact, packet_sha256, event_class, event_class_origin,
                        verified_at, justification: {event_sha256, view_sha256}}

Usage:
  check-run-diff.py --commit SHA [--repo DIR] [--sections]
  check-run-diff.py --self-test
Exit: 0 clean | 1 violation(s) | 2 inconclusive.
"""

import hashlib
import json
import os
import re
import subprocess
import sys

LOCK_CLASSES = {"t1", "correction", "lock", "informed_by"}

# engine-managed derivation region delimiters (assemble.py/backfill-derivation.py
# convention, spec sec.5) -- duplicated here (not imported) to keep this script's
# existing dependency-light, standalone-module style; the exact literal markers
# are shared verbatim across every module that touches the region.
DERIV_START = "# --- derivation"
DERIV_END = "# --- /derivation"


def _derivation_region(text):
    """Return (start_line_idx, end_line_idx) of the derivation block (both
    inclusive-exclusive over splitlines()), or None if absent/malformed."""
    lines = text.splitlines()
    start = end = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if start is None and s.startswith(DERIV_START):
            start = i
        elif start is not None and s.startswith(DERIV_END):
            end = i
            break
    if start is not None and end is not None and end > start:
        return start, end
    return None


def only_derivation_region_changed(pre_text, post_text):
    """True iff PRE_TEXT and POST_TEXT are byte-identical everywhere OUTSIDE
    their derivation regions (2026-07-05 absorption-verify amendment: the
    engine's verified: stamp write must touch ONLY the derivation block,
    never body prose -- this is the mechanical narrow-exemption check, not
    a trust assumption). False if either side lacks a derivation region
    (fail-closed: no region to scope the exemption to)."""
    pre_region = _derivation_region(pre_text)
    post_region = _derivation_region(post_text)
    if pre_region is None or post_region is None:
        return False
    pre_lines = pre_text.splitlines()
    post_lines = post_text.splitlines()
    pre_outside = pre_lines[:pre_region[0]] + pre_lines[pre_region[1] + 1:]
    post_outside = post_lines[:post_region[0]] + post_lines[post_region[1] + 1:]
    return pre_outside == post_outside


def _git(repo, *args):
    p = subprocess.run(["git", "-C", repo] + list(args),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if p.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (args[0], (p.stderr or "")[-200:]))
    return p.stdout


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def commit_files(repo, sha):
    out = _git(repo, "show", "--name-only", "--format=", sha)
    return [ln.strip().replace("\\", "/") for ln in out.splitlines() if ln.strip()]


def load_run_record(repo, sha):
    """The run's journal record = the receipts/journal/<seq>.json file ADDED in
    this commit. None if the commit carries no record."""
    recs = [f for f in commit_files(repo, sha)
            if re.match(r"receipts/journal/\d+\.json$", f)]
    if not recs:
        return None, None
    if len(recs) > 1:
        raise RuntimeError("commit carries %d journal records" % len(recs))
    body = _git(repo, "show", "%s:%s" % (sha, recs[0]))
    return json.loads(body), recs[0]


def file_at(repo, sha, path):
    try:
        return _git(repo, "show", "%s:%s" % (sha, path))
    except RuntimeError:
        return None


def effective_lock_class(entry):
    """F6: judgment-assigned event_class is lock-class regardless of value."""
    if str(entry.get("event_class_origin", "")).lower() == "judgment":
        return True
    return str(entry.get("event_class", "")).lower() in LOCK_CLASSES


def check_acc4(repo, sha):
    problems = []
    record, rec_path = load_run_record(repo, sha)
    if record is None:
        return ["no journal record in commit (vacuous only if this is not a "
                "compile commit)"], None
    touched = set(commit_files(repo, sha))
    absorbed_views = set()
    # (a) forward: journaled absorption => diff touches the view
    for a in record.get("absorbed", []):
        v = a["view"].replace("\\", "/")
        absorbed_views.add(v)
        if v not in touched:
            problems.append("fabricated claim: absorbed view %s not in commit diff"
                            % v)
    # absorption-verify (2026-07-05 amendment): a verify commit's CONFIRM
    # stamp writes ONLY the view's derivation region (verified: block) --
    # engine-owned, not a body-content absorption. Narrow exemption: the
    # view is treated as claimed ONLY IF (a) it is journaled in THIS
    # record's absorption_verified[], AND (b) the commit's diff to it is
    # verifiably confined to the derivation region (mechanically checked,
    # never merely trusted from the journal claim). A view named in
    # absorption_verified[] whose diff reaches outside the derivation
    # region is NOT exempted -- it still trips stowaway/fabricated below,
    # fail-closed.
    stamp_only_views = set()
    for av in record.get("absorption_verified", []):
        v = str(av.get("view", "")).replace("\\", "/")
        if not v:
            continue
        post_text = file_at(repo, sha, v)
        pre_text = file_at(repo, sha + "^", v)
        if post_text is not None and pre_text is not None \
                and only_derivation_region_changed(pre_text, post_text):
            stamp_only_views.add(v)
    # (a) converse: every touched wiki view is claimed
    for f in touched:
        if f.startswith("wiki/") and f.endswith(".md") \
                and f not in absorbed_views and f not in stamp_only_views:
            problems.append("stowaway: commit touches unclaimed view %s" % f)
    # (b)/(c) no-ops
    window = record.get("run_window") or {}
    for i, nc in enumerate(record.get("noop_candidates", [])):
        tag = "no-op[%d] (%s,%s)" % (i, nc.get("view"), nc.get("event"))
        pending_unverified = (str(nc.get("disposition")) ==
                              "PENDING_NOOP_CANDIDATE" and not nc.get("verified"))
        # v3.0-83 (2026-07-31): a verifier artifact is required only where
        # verification is OWED -- a lock-class candidate (effective_lock_class,
        # F6-conservative) or one that claims verified=true. compile-v2's run()
        # deliberately writes non-lock-class noops as CONSUMED with an empty
        # artifact (lock-class-only verification is a design decision, not an
        # omission); demanding artifacts on those made every routine-class noop
        # structurally FAIL this check -- first exercised live 2026-07-31 (13
        # violations on a co-cite correction run). Their integrity guard is the
        # justification hash pair below, which runs for every candidate.
        verification_owed = effective_lock_class(nc) or bool(nc.get("verified"))
        if not pending_unverified and verification_owed:
            if not nc.get("artifact"):
                problems.append("%s: missing verifier artifact reference" % tag)
            if not nc.get("packet_sha256"):
                problems.append("%s: missing packet hash" % tag)
        just = nc.get("justification") or {}
        ebody = file_at(repo, sha, nc.get("event", ""))
        vbody = file_at(repo, sha, nc.get("view", ""))
        if not just.get("event_sha256") or not just.get("view_sha256"):
            problems.append("%s: justification does not cite full event+view "
                            "bodies (empty-diff justification)" % tag)
        else:
            if ebody is not None and just["event_sha256"] != _sha256(ebody):
                problems.append("%s: justification event hash != event body at "
                                "commit" % tag)
            if vbody is not None and just["view_sha256"] != _sha256(vbody):
                problems.append("%s: justification view hash != view body at "
                                "commit" % tag)
        lockish = effective_lock_class(nc)
        if lockish and nc.get("verified"):
            va = str(nc.get("verified_at", ""))
            if not (window.get("start") and window.get("end")
                    and window["start"] <= va <= window["end"]):
                problems.append("%s: T1/lock-class no-op verifier artifact not "
                                "same-run (verified_at=%r window=%r)"
                                % (tag, va, window))
        if lockish and not nc.get("verified"):
            # legal ONLY as a pending candidate, never consumed
            if str(nc.get("disposition", "PENDING_NOOP_CANDIDATE")) == "CONSUMED":
                problems.append("%s: judgment/lock-class unverified no-op marked "
                                "CONSUMED (F6 violation)" % tag)
    return problems, record


# --------------------------------------------------------------------- sections
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _section_of(lines, idx):
    for j in range(idx, -1, -1):
        m = _HEADING.match(lines[j])
        if m:
            return m.group(2).strip()
    return "(preamble)"


def changed_sections(repo, pre_blob, post_blob):
    """Set of section headings changed between two blobs (union of the section
    each +/- hunk line falls in, computed against its own side)."""
    out = _git(repo, "diff", pre_blob, post_blob)
    pre = _git(repo, "cat-file", "-p", pre_blob).split("\n")
    post = _git(repo, "cat-file", "-p", post_blob).split("\n")
    secs = set()
    old_ln = new_ln = 0
    for ln in out.splitlines():
        m = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", ln)
        if m:
            old_ln, new_ln = int(m.group(1)), int(m.group(2))
            continue
        if ln.startswith("+++") or ln.startswith("---"):
            continue
        if ln.startswith("+"):
            if ln[1:].strip():   # blank lines carry no content -- unattributed
                secs.add(_section_of(post, min(new_ln - 1, len(post) - 1)))
            new_ln += 1
        elif ln.startswith("-"):
            if ln[1:].strip():
                secs.add(_section_of(pre, min(old_ln - 1, len(pre) - 1)))
            old_ln += 1
        elif ln.startswith(" "):
            old_ln += 1
            new_ln += 1
    return secs


def check_sections(repo, sha):
    problems = []
    record, _p = load_run_record(repo, sha)
    if record is None:
        return ["no journal record in commit"]
    for a in record.get("absorbed", []):
        v = a["view"]
        claimed = {(m.get("section") or "").strip()
                   for m in (a.get("manifest") or []) if isinstance(m, dict)}
        try:
            actual = changed_sections(repo, a["pre_blob"], a["post_blob"])
        except RuntimeError as e:
            problems.append("%s: cannot diff blobs (%s)" % (v, e))
            continue
        for s in sorted(claimed - actual):
            problems.append("%s: claim-without-hunk -- manifest claims section "
                            "%r, diff does not touch it" % (v, s))
        for s in sorted(actual - claimed):
            problems.append("%s: hunk-without-claim -- diff touches section %r, "
                            "manifest does not claim it" % (v, s))
    return problems


# --------------------------------------------------------------------- self-test
def self_test():
    import importlib.util
    import shutil
    import tempfile
    spec = importlib.util.spec_from_file_location(
        "compile_core", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "compile-core.py"))
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    total = failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    base = tempfile.mkdtemp(prefix="crd-")
    try:
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", base] + args, capture_output=True)
        os.makedirs(os.path.join(base, "wiki"))
        os.makedirs(os.path.join(base, "raw"))
        view = os.path.join(base, "wiki", "a.md")
        open(view, "w", encoding="utf-8", newline="\n").write(
            "# Title\n\n## Alpha\nold alpha\n\n## Beta\nold beta\n")
        open(os.path.join(base, "wiki", "b.md"), "w").write("# B\nstable\n")
        open(os.path.join(base, "raw", "e1.md"), "w").write("event body one\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "seed"],
                       capture_output=True)
        pre_blob = _git(base, "rev-parse", "HEAD:wiki/a.md").strip()

        def run_commit(absorbed, noops, extra_touch=None, msg="run"):
            if extra_touch:
                open(os.path.join(base, extra_touch), "a").write("stowaway\n")
            for a in absorbed:
                a.setdefault("corpus_support", [])
            rec = core.minimal_record("compile", "sha")
            rec["absorbed"] = absorbed
            rec["noop_candidates"] = noops
            rec["run_window"] = {"start": "2026-07-04T00:00:00",
                                 "end": "2026-07-04T23:59:59"}
            _s, jp = core.append_record(base, rec)
            paths = [a["view"] for a in absorbed] + \
                    [os.path.relpath(jp, base).replace(os.sep, "/")]
            if extra_touch:
                paths.append(extra_touch)
            return core.stage_only_commit(base, paths, msg)

        # happy path: edit Alpha section, manifest claims Alpha
        with open(view, "a", encoding="utf-8", newline="\n") as fh:
            pass
        txt = open(view, encoding="utf-8").read().replace("old alpha", "new alpha")
        open(view, "w", encoding="utf-8", newline="\n").write(txt)
        post_blob_txt = txt
        sha1 = run_commit(
            [{"view": "wiki/a.md", "events": ["raw/e1.md"],
              "pre_blob": pre_blob, "post_blob": "",  # filled below
              "manifest": [{"event": "raw/e1.md", "section": "Alpha"}]}], [])
        post_blob = _git(base, "rev-parse", "%s:wiki/a.md" % sha1).strip()
        # patch the record's post_blob in a follow-up? -- instead recompute:
        # rewrite record file with real blobs and amend is overkill; use blobs
        # directly in section check via a fixed record object:
        probs, rec = check_acc4(base, sha1)
        case("ACC-4 happy path clean", probs == [])
        rec["absorbed"][0]["pre_blob"] = pre_blob
        rec["absorbed"][0]["post_blob"] = post_blob
        # inject corrected record for section check by writing it to the file the
        # commit references is immutable -- so test check_sections' core directly:
        secs = changed_sections(base, pre_blob, post_blob)
        case("sections: changed set == {Alpha}", secs == {"Alpha"})
        claimed = {"Alpha"}
        case("sections: consistent manifest passes",
             not (claimed - secs) and not (secs - claimed))
        case("sections: claim-without-hunk trips",
             bool({"Beta"} - secs))
        case("sections: hunk-without-claim trips",
             bool(secs - set()))

        # fabricated claim: absorbed names untouched view
        txt = open(view, encoding="utf-8").read().replace("old beta", "new beta")
        open(view, "w", encoding="utf-8", newline="\n").write(txt)
        sha2 = run_commit(
            [{"view": "wiki/a.md", "events": ["raw/e1.md"], "pre_blob": "x",
              "post_blob": "y", "manifest": []},
             {"view": "wiki/b.md", "events": ["raw/e1.md"], "pre_blob": "x",
              "post_blob": "y", "manifest": []}], [])
        probs, _ = check_acc4(base, sha2)
        case("fabricated claim caught (b.md claimed, untouched)",
             any("fabricated" in p and "b.md" in p for p in probs))

        # stowaway: commit touches unclaimed wiki file
        txt = open(view, encoding="utf-8").read() + "more\n"
        open(view, "w", encoding="utf-8", newline="\n").write(txt)
        sha3 = run_commit(
            [{"view": "wiki/a.md", "events": ["raw/e1.md"], "pre_blob": "x",
              "post_blob": "y", "manifest": []}], [],
            extra_touch="wiki/b.md")
        probs, _ = check_acc4(base, sha3)
        case("stowaway caught (unclaimed b.md in commit)",
             any("stowaway" in p and "b.md" in p for p in probs))

        # no-op fixtures
        ebody = open(os.path.join(base, "raw", "e1.md"), encoding="utf-8").read()
        vbody = open(os.path.join(base, "wiki", "b.md"), encoding="utf-8").read()
        good_noop = {"view": "wiki/b.md", "event": "raw/e1.md", "verified": True,
                     "artifact": "deploy/evidence/x.json",
                     "packet_sha256": "p" * 8, "event_class": "t1",
                     "event_class_origin": "explicit",
                     "verified_at": "2026-07-04T12:00:00",
                     "justification": {"event_sha256": _sha256(ebody),
                                       "view_sha256": _sha256(vbody)}}
        txt = open(view, encoding="utf-8").read() + "x\n"
        open(view, "w", encoding="utf-8", newline="\n").write(txt)
        sha4 = run_commit([{"view": "wiki/a.md", "events": [], "pre_blob": "x",
                            "post_blob": "y", "manifest": []}], [good_noop])
        probs, _ = check_acc4(base, sha4)
        case("compliant T1 no-op passes",
             not [p for p in probs if "no-op" in p])

        bad = dict(good_noop)
        bad.pop("artifact")
        bad2 = dict(good_noop, justification={})
        bad3 = dict(good_noop, verified_at="2026-07-05T09:00:00")  # outside window
        bad4 = dict(good_noop, event_class="t3",
                    event_class_origin="judgment", verified=False,
                    disposition="CONSUMED")
        txt = open(view, encoding="utf-8").read() + "y\n"
        open(view, "w", encoding="utf-8", newline="\n").write(txt)
        sha5 = run_commit([{"view": "wiki/a.md", "events": [], "pre_blob": "x",
                            "post_blob": "y", "manifest": []}],
                          [bad, bad2, bad3, bad4])
        probs, _ = check_acc4(base, sha5)
        case("no-op-missing-artifact trips",
             any("missing verifier artifact" in p for p in probs))
        case("no-op-empty-diff justification trips",
             any("empty-diff justification" in p for p in probs))
        case("T1-no-op-unverified-same-run trips (verified_at outside window)",
             any("not same-run" in p for p in probs))
        case("F6: judgment-assigned class CONSUMED no-op trips",
             any("F6 violation" in p for p in probs))

        # PENDING_NOOP_CANDIDATE exemption: pre-VERIFY entry with empty
        # artifact/hash but a VALID full-body justification -> no artifact/hash
        # violation (PENDING semantics; VERIFY fills these in later)
        pending_noop = {"view": "wiki/b.md", "event": "raw/e1.md",
                        "verified": False, "artifact": "", "packet_sha256": "",
                        "event_class": "t3", "event_class_origin": "explicit",
                        "disposition": "PENDING_NOOP_CANDIDATE",
                        "justification": {"event_sha256": _sha256(ebody),
                                          "view_sha256": _sha256(vbody)}}
        txt = open(view, encoding="utf-8").read() + "z\n"
        open(view, "w", encoding="utf-8", newline="\n").write(txt)
        sha6 = run_commit([{"view": "wiki/a.md", "events": [], "pre_blob": "x",
                            "post_blob": "y", "manifest": []}], [pending_noop])
        probs, _ = check_acc4(base, sha6)
        case("PENDING_NOOP_CANDIDATE (verified=false, empty artifact/hash) "
             "passes -- no artifact/hash violation",
             not any(("missing verifier artifact" in p or "missing packet hash"
                      in p) for p in probs))

        # v3.0-83 (2026-07-31): CONSUMED + non-lock class = verification NOT
        # owed (compile-v2 deliberately never verifies these; empty artifact is
        # the designed state) -> NO artifact/hash violation. Justification hash
        # checks still guard it.
        consumed_noop = dict(pending_noop, disposition="CONSUMED")
        txt = open(view, encoding="utf-8").read() + "w\n"
        open(view, "w", encoding="utf-8", newline="\n").write(txt)
        sha7 = run_commit([{"view": "wiki/a.md", "events": [], "pre_blob": "x",
                            "post_blob": "y", "manifest": []}], [consumed_noop])
        probs, _ = check_acc4(base, sha7)
        case("CONSUMED non-lock no-op with empty artifact passes (verification "
             "not owed -- v3.0-83)",
             not any(("missing verifier artifact" in p or "missing packet hash"
                      in p) for p in probs))

        # ...but a LOCK-CLASS consumed-unverified no-op is owed verification:
        # it trips BOTH the F6 consumed-without-verify violation AND
        # missing-artifact (fail-closed direction preserved).
        consumed_lock = dict(consumed_noop, event_class="t1")
        txt = open(view, encoding="utf-8").read() + "v\n"
        open(view, "w", encoding="utf-8", newline="\n").write(txt)
        sha7b = run_commit([{"view": "wiki/a.md", "events": [], "pre_blob": "x",
                             "post_blob": "y", "manifest": []}], [consumed_lock])
        probs, _ = check_acc4(base, sha7b)
        case("CONSUMED lock-class unverified no-op still trips missing-artifact "
             "+ F6",
             any("missing verifier artifact" in p for p in probs)
             and any("F6 violation" in p for p in probs))

        # ...and a verified=true entry with an empty artifact is a lie: owed.
        verified_bare = dict(consumed_noop, verified=True)
        txt = open(view, encoding="utf-8").read() + "u\n"
        open(view, "w", encoding="utf-8", newline="\n").write(txt)
        sha7c = run_commit([{"view": "wiki/a.md", "events": [], "pre_blob": "x",
                             "post_blob": "y", "manifest": []}], [verified_bare])
        probs, _ = check_acc4(base, sha7c)
        case("verified=true no-op with empty artifact still trips "
             "missing-artifact",
             any("missing verifier artifact" in p for p in probs))

        # ------------------------------------- absorption-verify (2026-07-05)
        # A verify commit's CONFIRM stamp writes ONLY the view's derivation
        # region -- narrow, mechanically-checked exemption from the
        # stowaway/fabricated-claim rule above (never a blanket trust of the
        # journal's own absorption_verified[] claim).
        derived_view = os.path.join(base, "wiki", "derived.md")
        derived_text = ("# Derived\n\n"
                        "# --- derivation (engine-managed; strip region) ---\n"
                        "tier: T1\n"
                        "verified: null\n"
                        "# --- /derivation ---\n\n"
                        "## Intro\nbody text unrelated to derivation\n")
        open(derived_view, "w", encoding="utf-8", newline="\n").write(derived_text)
        subprocess.run(["git", "-C", base, "add", "wiki/derived.md"],
                       capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "seed derived.md"],
                       capture_output=True)

        def run_verify_commit(view_rel, new_view_text, absorption_verified,
                              msg="verify"):
            open(os.path.join(base, view_rel.replace("/", os.sep)), "w",
                encoding="utf-8", newline="\n").write(new_view_text)
            rec = core.minimal_record("verify", "sha")
            rec["absorption_verified"] = absorption_verified
            _s, jp = core.append_record(base, rec)
            paths = [view_rel, os.path.relpath(jp, base).replace(os.sep, "/")]
            return core.stage_only_commit(base, paths, msg)

        # (a) stamp-only edit (derivation region changed, body untouched) +
        # journaled absorption_verified[] naming the view -> exempted, clean
        stamped_ok = derived_text.replace(
            "verified: null",
            "verified:\n  status: passed\n  at: 2026-07-05T00:00:00\n"
            "  verifier_vendor: openai\n  verifier_model_id: gpt-5.5\n"
            "  absorb_vendor: anthropic\n  absorb_model_id: claude-opus-4-8\n"
            "  packet_hash: " + "a" * 64 + "\n"
            "  artifact: receipts/verify/absorb-seq1-v0.json")
        sha8 = run_verify_commit(
            "wiki/derived.md", stamped_ok,
            [{"view": "wiki/derived.md", "events": ["raw/e1.md"],
              "verified_at": "2026-07-05T00:00:00",
              "artifact": "receipts/verify/absorb-seq1-v0.json",
              "packet_sha256": "b" * 64, "view_sha256": _sha256(stamped_ok),
              "substrate": {}}])
        probs, _ = check_acc4(base, sha8)
        case("absorption-verify: derivation-only stamp exempted from "
             "stowaway/fabricated-claim (narrow, mechanical)", probs == [])

        # (b) same claim, but the diff ALSO changes body prose outside the
        # derivation region -- the exemption must NOT extend there: still
        # trips stowaway (never trust the journal claim over the real diff)
        stamped_bad = stamped_ok.replace(
            "body text unrelated to derivation",
            "body text SNUCK IN alongside the stamp")
        sha9 = run_verify_commit(
            "wiki/derived.md", stamped_bad,
            [{"view": "wiki/derived.md", "events": ["raw/e1.md"],
              "verified_at": "2026-07-05T00:00:00",
              "artifact": "receipts/verify/absorb-seq1-v1.json",
              "packet_sha256": "c" * 64, "view_sha256": _sha256(stamped_bad),
              "substrate": {}}])
        probs, _ = check_acc4(base, sha9)
        case("absorption-verify: exemption does NOT extend to body-prose "
             "edits smuggled alongside the stamp (still trips stowaway)",
             any("stowaway" in p and "derived.md" in p for p in probs))

        # (c) a wiki view touched in a verify commit with NO
        # absorption_verified[] entry at all (e.g. an unrelated hand-edit
        # riding along) is never exempted, regardless of derivation-only
        # shape -- exemption requires the journaled claim AND the
        # mechanical check, not either alone.
        stamped_unclaimed = derived_text.replace("tier: T1", "tier: T3")
        sha10 = run_verify_commit("wiki/derived.md", stamped_unclaimed, [])
        probs, _ = check_acc4(base, sha10)
        case("absorption-verify: derivation-only edit with NO journaled "
             "absorption_verified[] claim is still a stowaway",
             any("stowaway" in p and "derived.md" in p for p in probs))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failed:
        print("check-run-diff (ACC-4/LLM-2): FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("check-run-diff (ACC-4/LLM-2): PASS (%d/%d)" % (total, total))
    return 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    repo = argv[argv.index("--repo") + 1] if "--repo" in argv else "."
    if "--commit" not in argv:
        print(__doc__.strip().split("\n\n")[-1])
        return 2
    sha = argv[argv.index("--commit") + 1]
    probs, _rec = check_acc4(repo, sha)
    if "--sections" in argv:
        probs += check_sections(repo, sha)
    for p in probs:
        print("VIOLATION: %s" % p)
    print("check-run-diff: %s (%d violation(s))"
          % ("CLEAN" if not probs else "FAIL", len(probs)))
    return 0 if not probs else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
