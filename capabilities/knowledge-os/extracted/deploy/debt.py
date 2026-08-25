#!/usr/bin/env python3
"""debt.py -- lineage-stable cap debt and the absorb brake (ADR #11 Release 3, v3.0.52;
backlog v3.0-129/-130; design brief v4 section 2.2 [R2-C2, R3-C4]).

DEBT IS COMPUTED, NEVER STORED -- the pending.py doctrine applied to cap debt: every
episode is re-derived from git objects and the working tree on every run, so a deleted
file, a rename, a split, or a rewritten ledger cannot make debt disappear; there is no
stored flag to forget. What this module answers:

  EPISODES.  A cap episode is (logical view identity, crossing) and stays OPEN until the
  debt is discharged by retirement -- never by reorganizing hot bytes:
    own        the view's LF-normalized bytes exceed its engine-caps.yaml cap. The
               crossing is the oldest commit of the newest contiguous over-cap run of the
               view's history (rename-following), so a rename/move never resets the
               clock.
    split      the view is named in the `parts:` of a SUPERSEDED stub's redirect-map.
               The parent's excess-over-cap at supersession is apportioned across the
               parts by their sizes at the split commit [R3-C4: a split never discharges
               -- descendants inherit aggregate byte debt even when individually under
               cap]. A merge is the same rule fired from several stubs; the sums add.
    recreate   the view's path (or its region view_id, wherever the file now sits) had a
               previous incarnation deleted while over cap. Path reuse and id reuse both
               inherit; nonstandard creation paths land here too, because the key is the
               path/id, not the creating tool.
  Discharge is retirement only: the sum of span bytes in PUBLISHED retire records for the
  view (first-parent chain, after the crossing/split/deletion) counts against the debt; a
  proposed, refused, unauthorized, or interrupted retirement is not on the chain and
  counts nothing (ADR #11 G4). An own episode also discharges when the view measures
  under cap (bytes left the hot tier).

  THE BRAKE.  During an open or escalated episode an ordinary absorb may not increase the
  view's LF-normalized bytes (condition 7). compile-v2.validate_absorb_output asks
  brake() and refuses growth. The outs, exactly the ADR's: a net-zero-or-shrinking
  rewrite; a correction paired with a retirement that EXECUTES in the same prepared
  commit (deploy/retire.py --splice -- that lane measures the final size itself); or an
  explicit expiring operator exception (deploy/rulings/cap-exception-*.md, an
  operator-edited-only class path, committed and HEAD-identical, `expires:` dated).

  THE OBLIGATION.  One per episode, never renewed: `open` until the grace deadline
  (engine-caps.yaml episode_grace_days / episode_grace_absorbs, whichever first), then
  `escalated` -- an enforcement state, not discharge [R2-C2]: growth stays refused and
  the episode is surfaced for the decision inbox (the sweep briefing carries it; the
  inbox projects from there). Terminal states are `discharged` (debt gone) and
  `exception-approved` (a valid, unexpired operator exception; expiry returns it to
  escalated). Honest liveness (condition 8): this module guarantees non-growth and
  visibility; returning under cap still requires operator-promoted retirement.

  IDENTITY.  The derivation region's `view_id:` (minted at birth from the birth path by
  backfill-derivation.render_region since v3.0.52). A legacy region without the key
  resolves to the SAME value its next mint would write (mint_view_id is the one home),
  so legacy views inherit by path exactly as minted views do. Documented residual: a
  recreation at a NEW path with a NEW id and no redirect-map link is not mechanically
  linked to its ancestor -- it is backstopped by mass conservation (whichever view holds
  the mass crosses its own cap and opens its own episode).

Usage:
  debt.py --root R [--branch B] --report [--json]      every episode, corpus-wide
  debt.py --root R --view wiki/topic/x.md [--json]     one view's episodes
  debt.py --root R --brake wiki/topic/x.md OLD NEW     the validator's question (exit 2 = refuse)
  debt.py --self-test
Exit: 0 no open episodes / brake allows | 1 self-test failure | 2 open episodes exist,
brake refuses, or inconclusive.
"""

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

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


_caps_mod = _trust_mod = _backfill_mod = None


def _caps():
    global _caps_mod
    if _caps_mod is None:
        _caps_mod = _load("_debt_caps", "check-caps.py")
    return _caps_mod


def _trust():
    global _trust_mod
    if _trust_mod is None:
        _trust_mod = _load("_debt_trust", "trust.py")
    return _trust_mod


def _backfill():
    global _backfill_mod
    if _backfill_mod is None:
        _backfill_mod = _load("_debt_backfill", "backfill-derivation.py")
    return _backfill_mod


JOURNAL_DIR = "receipts/journal"
RULINGS_GLOB_DIR = "deploy/rulings"
DEFAULT_GRACE_DAYS = 30
DEFAULT_GRACE_ABSORBS = 10
_PROJECTION_BASENAMES = {"INDEX.md", "HEALTH.md", "REVIEW.md"}
_CAPS_OVERRIDE = None   # SELF-TEST ONLY: replaces the engine-caps.yaml cap table
_GRACE_OVERRIDE = None  # SELF-TEST ONLY: (days, absorbs)


class Refuse(Exception):
    pass


def _git(repo, *args):
    p = subprocess.run(["git", "--no-replace-objects", "-C", repo] + list(args),
                       capture_output=True)
    return p.returncode, p.stdout, p.stderr


def _git_text(repo, *args):
    rc, out, err = _git(repo, *args)
    return rc, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _blob(repo, commit, rel):
    rc, out, _ = _git(repo, "cat-file", "blob", "%s:%s" % (commit, rel))
    return out if rc == 0 else None


def _lf_len(data):
    return len(data.replace(b"\r\n", b"\n"))


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_date(s):
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def cap_table():
    if _CAPS_OVERRIDE is not None:
        return dict(_CAPS_OVERRIDE)
    return _caps().load_caps()


def grace_cfg():
    if _GRACE_OVERRIDE is not None:
        return {"days": _GRACE_OVERRIDE[0], "absorbs": _GRACE_OVERRIDE[1]}
    cfg = {"days": DEFAULT_GRACE_DAYS, "absorbs": DEFAULT_GRACE_ABSORBS}
    try:
        text = open(os.path.join(_HERE, "engine-caps.yaml"), encoding="utf-8-sig").read()
    except OSError:
        return cfg
    m = re.search(r"(?m)^episode_grace_days:\s*(\d+)", text)
    if m:
        cfg["days"] = int(m.group(1))
    m = re.search(r"(?m)^episode_grace_absorbs:\s*(\d+)", text)
    if m:
        cfg["absorbs"] = int(m.group(1))
    return cfg


# ------------------------------------------------------------------ region reading
_REGION_START = "# --- derivation"
_REGION_END = "# --- /derivation"


def _region_keys(text):
    """Shallow top-level `key: value` pairs of the derivation region (the assemble.py
    parse rule); {} when no region."""
    lines = text.splitlines()
    s = e = None
    for i, ln in enumerate(lines):
        st = ln.strip()
        if s is None and st.startswith(_REGION_START):
            s = i
        elif s is not None and st.startswith(_REGION_END):
            e = i
            break
    if s is None or e is None:
        return {}
    keys = {}
    for ln in lines[s + 1:e]:
        if not ln.strip() or ln.lstrip().startswith("#") or ln[:1] in (" ", "\t"):
            continue
        if ":" not in ln:
            continue
        k, _, v = ln.partition(":")
        k = k.strip()
        if k and all(c.isalnum() or c == "_" for c in k):
            keys[k] = v.split("#", 1)[0].strip()
    return keys


def effective_view_id(rel, text):
    """The region's view_id, else the value its next mint would write (one home:
    backfill-derivation.mint_view_id) -- so legacy views key identically."""
    vid = _region_keys(text).get("view_id", "")
    return vid or _backfill().mint_view_id(rel)


def _cap_for(text, caps):
    kind = _region_keys(text).get("view", "") or None
    return caps.get(kind or "default", caps["default"])


def _is_superseded_stub(text):
    fm_status = re.search(r"(?m)^status:\s*superseded\s*$", text.split("# ---", 1)[0])
    return ("```redirect-map" in text) and (
        fm_status is not None or _region_keys(text).get("status") == "superseded")


def _redirect_map_parts(text):
    m = re.search(r"```redirect-map\n(.*?)```", text, re.S)
    if not m:
        return []
    parts, in_parts = [], False
    for ln in m.group(1).splitlines():
        st = ln.strip()
        if st.startswith("parts:"):
            in_parts = True
            continue
        if in_parts:
            if st.startswith("- "):
                parts.append(st[2:].strip().replace("\\", "/"))
            elif st and not ln.startswith(" "):
                in_parts = False
    return parts


# ------------------------------------------------------------------ history primitives
def _touch_history(repo, head, rel, follow=False):
    """[(commit, iso_date, path_at_commit)] of first-parent commits touching rel, newest
    first. follow=True survives renames (the crossing clock must not reset on a move) --
    --name-only under --follow reports the file's path AT each commit, so the blob is
    always read by the name the view had then."""
    args = ["log", "--first-parent", "--name-only", "--format=%x00%H%x09%cI"]
    if follow:
        args.append("--follow")
    args += [head, "--", rel]
    rc, out, _ = _git_text(repo, *args)
    rows = []
    for block in out.split("\x00"):
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines or "\t" not in lines[0]:
            continue
        sha, d = lines[0].split("\t", 1)
        path = lines[1].strip() if len(lines) > 1 else rel
        rows.append((sha.strip(), d.strip(), path.replace("\\", "/")))
    return rows


def find_crossing(repo, head, rel, cap):
    """The oldest commit of the NEWEST contiguous over-cap run: (commit, date) or None.
    Walks the rename-following touch history so a move never resets the clock."""
    crossing = None
    for sha, d, path in _touch_history(repo, head, rel, follow=True):
        b = _blob(repo, sha, path) or _blob(repo, sha, rel)
        if b is None:
            break
        if _lf_len(b) > cap:
            crossing = (sha, d)
        else:
            break  # the run is broken; the newest run ended above
    return crossing


def _absorbs_since(repo, head, rel, since_sha):
    """Commits touching the view after the crossing, the grace clock's absorb proxy."""
    rc, out, _ = _git_text(repo, "rev-list", "--first-parent", "--count",
                           "%s..%s" % (since_sha, head), "--", rel)
    try:
        return int(out.strip())
    except ValueError:
        return 0


def _published_retirements(repo, head, rel):
    """[(sha, bytes)] of PUBLISHED, un-rolled-back retirements for rel on the
    first-parent chain, oldest first -- the shared source for both discharge readers."""
    rolled_back = set()
    recs = _trust()._retire_records_history(repo, head)
    for _p, _sha, rec in recs:
        if rec.get("rollback_of") is not None and \
                str(rec.get("view", "")).replace("\\", "/") == rel:
            rolled_back.add(rec["rollback_of"])
    rows = []
    for _p, sha, rec in recs:
        if str(rec.get("view", "")).replace("\\", "/") != rel:
            continue
        if rec.get("rollback_of") is not None or rec.get("seq") in rolled_back:
            continue
        total = 0
        for s in rec.get("spans") or []:
            try:
                total += int(s.get("bytes", 0))
            except (TypeError, ValueError):
                pass
        if total:
            rows.append((sha, total))
    rows.reverse()  # history walk is tip-most first; discharge allocates oldest first
    return rows


def allocate_discharge(repo, head, rel, inherited):
    """Cross-vendor round-2 fold (c3): a retired byte discharges ONE inherited byte,
    never one per episode. Each published retirement's bytes are allocated across the
    view's inherited episodes oldest-crossing-first, and only to episodes whose crossing
    precedes the retirement (git ancestry). Mutates each episode's `retired_since` and
    `remaining`; returns the episodes."""
    inherited = sorted(inherited, key=lambda e: e["crossing"]["date"])
    for ep in inherited:
        ep["retired_since"] = 0
    for sha, avail in _published_retirements(repo, head, rel):
        for ep in inherited:
            if avail <= 0:
                break
            rc, _o, _e = _git(repo, "merge-base", "--is-ancestor",
                              ep["crossing"]["commit"], sha)
            if rc != 0 or sha == ep["crossing"]["commit"]:
                continue
            room = ep["inherited_bytes"] - ep["retired_since"]
            take = min(avail, max(0, room))
            ep["retired_since"] += take
            avail -= take
    for ep in inherited:
        ep["remaining"] = max(0, ep["inherited_bytes"] - ep["retired_since"])
    return inherited


def retired_bytes_since(repo, head, rel, since_sha=None):
    """Sum of span bytes in PUBLISHED retire records for rel on the first-parent chain,
    introduced strictly after since_sha (all when None). Prepared refs, refused or
    interrupted retirements are not on the chain and count nothing (ADR #11 G4). A
    ROLLED-BACK retirement counts nothing either (v3.0.52: a rollback record on the
    chain names `rollback_of`; the hot bytes came back, so the discharge is void)."""
    total = 0
    rolled_back = set()
    recs = _trust()._retire_records_history(repo, head)
    for _p, _sha, rec in recs:
        if rec.get("rollback_of") is not None and \
                str(rec.get("view", "")).replace("\\", "/") == rel:
            rolled_back.add(rec["rollback_of"])
    for p, sha, rec in recs:
        if str(rec.get("view", "")).replace("\\", "/") != rel:
            continue
        if rec.get("rollback_of") is not None or rec.get("seq") in rolled_back:
            continue
        if since_sha:
            rc, _o, _e = _git(repo, "merge-base", "--is-ancestor", since_sha, sha)
            if rc != 0 or sha == since_sha:
                continue
        for s in rec.get("spans") or []:
            try:
                total += int(s.get("bytes", 0))
            except (TypeError, ValueError):
                pass
    return total


# ------------------------------------------------------------------ inheritance
def _supersession_commit(repo, head, stub_rel):
    """(split_commit, split_date, pre_blob): the oldest commit whose blob carries the
    redirect-map, and the parent CONTENT from the touching commit before it. None when
    the file was born a stub (nothing to inherit)."""
    hist = _touch_history(repo, head, stub_rel)
    split = None
    pre = None
    for i, (sha, d, _pth) in enumerate(hist):  # newest -> oldest
        b = _blob(repo, sha, stub_rel)
        if b is not None and b"```redirect-map" in b:
            split = (sha, d)
            pre = None
            for psha, _pd, _pp in hist[i + 1:]:
                pb = _blob(repo, psha, stub_rel)
                if pb is not None and b"```redirect-map" not in pb:
                    pre = pb
                    break
        else:
            break
    if split is None or pre is None:
        return None
    return split[0], split[1], pre


def _first_blob(repo, head, rel):
    hist = _touch_history(repo, head, rel)
    for sha, _d, _p in reversed(hist):
        b = _blob(repo, sha, rel)
        if b is not None:
            return b
    return None


def split_inherited(repo, head, view_rel, caps, _seen=None):
    """Episodes inherited from every superseded stub naming view_rel in its parts.
    Inheritance is TRANSITIVE (v3.0.52 G4 catch): a superseded intermediate passes on
    its OWN excess plus the REMAINING inherited debt it was still carrying at
    supersession -- otherwise a split-then-merge chain would clear aggregate debt
    without retiring a byte, exactly ADR #11 reopen trigger (d)."""
    out = []
    seen = set(_seen or ())
    if view_rel in seen:
        return out
    seen.add(view_rel)
    # stubs are read from the BRANCH TIP's tree, never the working tree (cross-vendor
    # round-1 catch): an uncommitted worktree deletion of a stub must not hide the
    # lineage -- inheritance derives from committed state, like every other input here
    # except the view's CURRENT size (worktree by design, check-caps parity).
    rc, tree_out, _ = _git_text(repo, "ls-tree", "-r", "--name-only", head, "--", "wiki")
    for srel in tree_out.splitlines():
        srel = srel.strip()
        if True:
            if not srel.endswith(".md") or srel.startswith("wiki/cold/"):
                continue
            if srel in seen:
                continue
            sb = _blob(repo, head, srel)
            if sb is None:
                continue
            stext = sb.decode("utf-8", "replace")
            if not _is_superseded_stub(stext):
                continue
            parts = _redirect_map_parts(stext)
            if view_rel not in parts:
                continue
            sup = _supersession_commit(repo, head, srel)
            if sup is None:
                continue
            split_sha, split_date, pre_blob = sup
            pre_text = pre_blob.decode("utf-8", "replace")
            own_excess = max(0, _lf_len(pre_blob) - _cap_for(pre_text, caps))
            # round-2 fold (c3): the intermediate's carried debt is discharge-ALLOCATED
            # (one retired byte discharges one inherited byte, never one per ancestor)
            carried_eps = split_inherited(repo, head, srel, caps, seen) + \
                recreate_inherited(repo, head, srel,
                                   effective_view_id(srel, pre_text), caps)
            carried = sum(e["remaining"] for e in
                          allocate_discharge(repo, head, srel, carried_eps))
            excess = own_excess + carried
            if excess <= 0:
                continue
            sizes = {}
            for p in parts:
                pb = _blob(repo, split_sha, p) or _first_blob(repo, head, p)
                sizes[p] = _lf_len(pb) if pb else 0
            total = sum(sizes.values())
            if total <= 0:
                continue
            share = int(round(excess * sizes[view_rel] / float(total)))
            if share <= 0:
                continue
            retired = retired_bytes_since(repo, head, view_rel, split_sha)
            out.append({"kind": "split", "from": srel,
                        "from_view_id": effective_view_id(srel, pre_text),
                        "crossing": {"commit": split_sha, "date": split_date},
                        "inherited_bytes": share, "carried_transitively": carried,
                        "retired_since": retired,
                        "remaining": max(0, share - retired)})
    return out


def recreate_inherited(repo, head, view_rel, view_id, caps):
    """Episodes inherited from a deleted over-cap incarnation: by PATH (the newest
    deletion of this very path) and by VIEW_ID (a deleted wiki view elsewhere whose
    region carried the same id)."""
    out = []
    seen_del = set()
    rc, out_t, _ = _git_text(repo, "log", "--first-parent", "--diff-filter=D",
                             "--format=%H%x09%cI", head, "--", view_rel)
    rows = [ln.split("\t", 1) for ln in out_t.splitlines() if "\t" in ln]
    if rows:
        dsha, ddate = rows[0][0].strip(), rows[0][1].strip()
        pre = _blob(repo, dsha + "^", view_rel)
        if pre is not None:
            excess = _lf_len(pre) - _cap_for(pre.decode("utf-8", "replace"), caps)
            if excess > 0:
                retired = retired_bytes_since(repo, head, view_rel, dsha)
                seen_del.add(dsha)
                out.append({"kind": "recreate", "from": view_rel,
                            "from_view_id": effective_view_id(
                                view_rel, pre.decode("utf-8", "replace")),
                            "crossing": {"commit": dsha, "date": ddate},
                            "inherited_bytes": excess, "retired_since": retired,
                            "remaining": max(0, excess - retired)})
    # by view_id, anywhere under wiki/ (equivalent-query creation: the region was copied)
    rc, out_t, _ = _git_text(repo, "log", "--first-parent", "--diff-filter=D",
                             "--name-only", "--format=%x00%H%x09%cI", head, "--", "wiki")
    for block in out_t.split("\x00"):
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines or "\t" not in lines[0]:
            continue
        dsha, ddate = lines[0].split("\t", 1)
        if dsha in seen_del:
            continue
        for q in lines[1:]:
            q = q.strip()
            if not q.endswith(".md") or q == view_rel or q.startswith("wiki/cold/"):
                continue
            pre = _blob(repo, dsha + "^", q)
            if pre is None:
                continue
            ptext = pre.decode("utf-8", "replace")
            if _region_keys(ptext).get("view_id", "") != view_id or not view_id:
                continue
            excess = _lf_len(pre) - _cap_for(ptext, caps)
            if excess <= 0:
                continue
            retired = retired_bytes_since(repo, head, view_rel, dsha)
            out.append({"kind": "recreate", "from": q, "from_view_id": view_id,
                        "crossing": {"commit": dsha, "date": ddate},
                        "inherited_bytes": excess, "retired_since": retired,
                        "remaining": max(0, excess - retired)})
    return out


# ------------------------------------------------------------------ exceptions + state
def exceptions(root, view_rel, view_id, now=None, findings=None):
    """Valid operator cap exceptions for the view: deploy/rulings/cap-exception-*.md,
    committed AND HEAD-identical (an uncommitted ruling is a session artifact, not an
    operator act -- surfaced as a finding, never honored), naming the view by path or id,
    with an unexpired `expires:`. Returns (valid, expired) lists."""
    now = now or _now()
    valid, expired = [], []
    d = os.path.join(root, RULINGS_GLOB_DIR.replace("/", os.sep))
    if not os.path.isdir(d):
        return valid, expired
    for fn in sorted(os.listdir(d)):
        if not (fn.startswith("cap-exception-") and fn.endswith(".md")):
            continue
        rel = RULINGS_GLOB_DIR + "/" + fn
        try:
            text = open(os.path.join(d, fn), encoding="utf-8-sig").read()
        except OSError:
            continue
        mv = re.search(r"(?m)^view:\s*(\S+)", text)
        mi = re.search(r"(?m)^view_id:\s*(\S+)", text)
        me = re.search(r"(?m)^expires:\s*(\S+)", text)
        names = (mv and mv.group(1).replace("\\", "/") == view_rel) or \
                (mi and mi.group(1) == view_id)
        if not names or not me:
            continue
        try:
            ident = _trust().committed_identical(root, rel)[0]
        except Exception:
            ident = False
        if not ident:
            if findings is not None:
                findings.append("cap exception %s is not committed-identical to HEAD -- "
                                "ignored (an operator ruling is a committed file)" % rel)
            continue
        exp = _parse_date(me.group(1))
        row = {"path": rel, "expires": me.group(1)}
        if exp is not None and exp.replace(tzinfo=exp.tzinfo or datetime.timezone.utc) \
                >= now.replace(microsecond=0):
            valid.append(row)
        else:
            expired.append(row)
    return valid, expired


def _finish(ep, repo, head, rel, valid_exc, now):
    g = grace_cfg()
    cd = _parse_date(ep["crossing"]["date"])
    deadline = cd + datetime.timedelta(days=g["days"]) if cd else None
    absorbs = _absorbs_since(repo, head, rel, ep["crossing"]["commit"])
    past = (deadline is not None and now > deadline) or absorbs > g["absorbs"]
    ep["deadline"] = deadline.isoformat() if deadline else None
    ep["absorbs_since"] = absorbs
    ep["grace"] = g
    if valid_exc:
        ep["state"] = "exception-approved"
        ep["exception"] = valid_exc[0]
    elif past:
        ep["state"] = "escalated"
    else:
        ep["state"] = "open"
    ep["obligation_id"] = "%s@%s" % (ep.get("view_id", "?"), ep["crossing"]["commit"][:12])
    return ep


def episodes_for_view(root, view_rel, branch=None, now=None, findings=None):
    """Every OPEN episode for one view (own + inherited), with states. Discharged
    episodes are not emitted (debt gone is debt gone; the journal holds the history)."""
    now = now or _now()
    repo = root
    try:
        branch = _trust().resolve_branch(repo, branch)
    except _trust().TrustError as e:
        raise Refuse(str(e))
    rc, head_t, _ = _git_text(repo, "rev-parse", "--verify", "--quiet",
                              "refs/heads/%s^{commit}" % branch)
    if rc != 0:
        raise Refuse("production branch %s does not resolve" % branch)
    head = head_t.strip()
    view_rel = view_rel.replace("\\", "/")
    p = os.path.join(root, view_rel.replace("/", os.sep))
    try:
        raw = open(p, "rb").read()
    except OSError:
        raw = _blob(repo, head, view_rel)
        if raw is None:
            return []
    text = raw.decode("utf-8", "replace")
    if _is_superseded_stub(text) or os.path.basename(view_rel) in _PROJECTION_BASENAMES:
        return []
    caps = cap_table()
    cap = _cap_for(text, caps)
    vid = effective_view_id(view_rel, text)
    valid_exc, expired_exc = exceptions(root, view_rel, vid, now, findings)
    out = []
    cur = _lf_len(raw)
    if cur > cap:
        crossing = find_crossing(repo, head, view_rel, cap)
        if crossing is None:
            # over cap only in the working tree (not yet committed over): the episode
            # opens at the tip -- the brake still applies, the clock starts now
            rc2, tip_d, _ = _git_text(repo, "log", "-1", "--format=%cI", head)
            crossing = (head, tip_d.strip())
        ep = {"view": view_rel, "view_id": vid, "kind": "own", "bytes": cur, "cap": cap,
              "excess": cur - cap, "remaining": cur - cap,
              "crossing": {"commit": crossing[0], "date": crossing[1]},
              "retired_since": retired_bytes_since(repo, head, view_rel, crossing[0])}
        out.append(_finish(ep, repo, head, view_rel, valid_exc, now))
    # round-2 fold (c3): inherited episodes share ONE discharge pool -- a retired byte
    # discharges one inherited byte total, allocated oldest-crossing-first, never once
    # per ancestor (the multi-parent double-discharge the round-2 verifier constructed)
    inherited = split_inherited(repo, head, view_rel, caps) + \
        recreate_inherited(repo, head, view_rel, vid, caps)
    for ep in allocate_discharge(repo, head, view_rel, inherited):
        if ep["remaining"] <= 0:
            continue
        ep.update({"view": view_rel, "view_id": vid, "bytes": cur, "cap": cap})
        out.append(_finish(ep, repo, head, view_rel, valid_exc, now))
    if expired_exc and out:
        for ep in out:
            ep["expired_exceptions"] = expired_exc
    return out


def episodes(root, branch=None, now=None, findings=None):
    """Corpus-wide: every open episode across wiki/ (cold tier, stubs, projections
    excluded)."""
    out = []
    wiki = os.path.join(root, "wiki")
    if not os.path.isdir(wiki):
        return out
    for dirpath, _dns, fns in os.walk(wiki):
        rel_dp = os.path.relpath(dirpath, root).replace("\\", "/")
        if rel_dp.startswith("wiki/cold"):
            continue
        for fn in sorted(fns):
            if fn.endswith(".md") and fn not in _PROJECTION_BASENAMES:
                out.extend(episodes_for_view(root, rel_dp + "/" + fn, branch, now,
                                             findings))
    return out


def brake(root, view_rel, old_len, new_len, branch=None, now=None):
    """The validator's question (condition 7): may an ordinary absorb take this view
    from old_len to new_len LF bytes? Growth during any open/escalated episode refuses;
    a valid exception, or non-growth, allows. Returns {'allowed','reason','episodes'}."""
    if new_len <= old_len:
        return {"allowed": True, "reason": "non-increasing (%d -> %d LF bytes)"
                % (old_len, new_len), "episodes": []}
    eps = episodes_for_view(root, view_rel, branch, now)
    blocking = [e for e in eps if e["state"] in ("open", "escalated")]
    if not blocking:
        exc = [e for e in eps if e["state"] == "exception-approved"]
        return {"allowed": True, "reason": ("operator exception %s (expires %s)"
                % (exc[0]["exception"]["path"], exc[0]["exception"]["expires"]))
                if exc else "no open cap episode", "episodes": eps}
    e0 = blocking[0]
    return {"allowed": False, "episodes": eps, "reason":
            "cap episode %s is %s (%s debt: %d byte(s) remaining, crossed at %s): an "
            "ordinary absorb may not grow the view (%d -> %d LF bytes). The outs are the "
            "ADR's: rewrite net-zero; pair the correction with a retirement that executes "
            "in the same prepared commit (deploy/retire.py --splice); or an operator "
            "exception (deploy/rulings/cap-exception-*.md, committed, `expires:` dated)"
            % (e0["obligation_id"], e0["state"], e0["kind"], e0["remaining"],
               e0["crossing"]["commit"][:12], old_len, new_len)}


def render(eps, findings=()):
    out = []
    if not eps:
        out.append("no open cap episodes")
    for e in eps:
        out.append("%-9s %-11s %s  view %s (%d/%d bytes)  remaining %d  crossed %s  "
                   "absorbs %d  deadline %s%s" % (
                       e["state"], e["kind"], e["obligation_id"], e["view"], e["bytes"],
                       e["cap"], e["remaining"], e["crossing"]["commit"][:12],
                       e["absorbs_since"], e.get("deadline") or "?",
                       "  [exception %s]" % e["exception"]["path"]
                       if e.get("exception") else ""))
    for f in findings:
        out.append("FINDING: " + f)
    return "\n".join(out)


# ------------------------------------------------------------------ self-test
def self_test():
    global _CAPS_OVERRIDE, _GRACE_OVERRIDE
    failed = total = 0

    def case(name, cond, detail=""):
        nonlocal failed, total
        total += 1
        if not cond:
            failed += 1
        print("  %s %s%s" % ("ok " if cond else "XX ", name,
                             ("  [%s]" % str(detail)[:300]) if detail and not cond else ""))

    if shutil.which("git") is None:
        print("debt.py self-test: INCONCLUSIVE -- git required")
        return 2
    base = tempfile.mkdtemp(prefix="debt-selftest-")
    _CAPS_OVERRIDE = {"topic": 200, "default": 200}
    _GRACE_OVERRIDE = (30, 10)
    try:
        r = os.path.join(base, "repo")
        os.makedirs(r)

        def git(*a):
            return subprocess.run(["git", "-C", r] + list(a), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace")

        def write(rel, t):
            p = os.path.join(r, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(t)

        def commit(msg, date="2026-08-01T00:00:00"):
            git("add", "-A")
            env = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
            p = subprocess.run(["git", "-C", r, "commit", "-q", "-m", msg],
                               capture_output=True, env=env)
            assert p.returncode == 0, p.stderr
            return subprocess.run(["git", "-C", r, "rev-parse", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()

        REGION = ("# --- derivation (engine-managed; strip region) ---\n"
                  "schema_version: 3.2\nview: topic\nview_id: %s\nstatus: active\n"
                  "# --- /derivation ---\n")

        def view_text(vid, body_bytes):
            head_txt = "---\ntitle: v\n---\n" + REGION % vid + "\n## S\n\n"
            pad = "x" * max(0, body_bytes - len(head_txt.encode()) - 1)
            return head_txt + pad + "\n"

        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        git("config", "commit.gpgsign", "false")
        write("project.yaml", "trust_surface_signing: visible\n")
        write("wiki/topic/small.md", view_text("v-small", 150))
        commit("seed", "2026-07-01T00:00:00")
        now = datetime.datetime(2026, 8, 24, tzinfo=datetime.timezone.utc)
        case("under-cap view: no episodes",
             episodes_for_view(r, "wiki/topic/small.md", now=now) == [])
        bk = brake(r, "wiki/topic/small.md", 150, 180, now=now)
        case("brake: growth of a debt-free view is allowed", bk["allowed"], bk)

        # own episode: crossing = oldest commit of the newest contiguous over-cap run
        write("wiki/topic/big.md", view_text("v-big", 150))
        commit("big born small", "2026-07-02T00:00:00")
        write("wiki/topic/big.md", view_text("v-big", 260))
        c_cross = commit("big crosses the cap", "2026-08-20T00:00:00")
        write("wiki/topic/big.md", view_text("v-big", 300))
        commit("big grows again", "2026-08-21T00:00:00")
        eps = episodes_for_view(r, "wiki/topic/big.md", now=now)
        case("own episode: open, excess = bytes - cap, crossing is the FIRST over-cap "
             "commit of the run (not the newest)",
             len(eps) == 1 and eps[0]["kind"] == "own" and eps[0]["state"] == "open"
             and eps[0]["excess"] == 100 and eps[0]["crossing"]["commit"] == c_cross, eps)
        bk = brake(r, "wiki/topic/big.md", 300, 301, now=now)
        case("brake: growth during an open episode REFUSES naming the three outs",
             not bk["allowed"] and "--splice" in bk["reason"]
             and "cap-exception" in bk["reason"], bk["reason"])
        bk = brake(r, "wiki/topic/big.md", 300, 300, now=now)
        case("brake: net-zero is allowed during the episode", bk["allowed"], bk)
        bk = brake(r, "wiki/topic/big.md", 300, 250, now=now)
        case("brake: shrinkage is always allowed", bk["allowed"])

        # escalation by deadline; exception file honored only when committed
        late = datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc)
        eps = episodes_for_view(r, "wiki/topic/big.md", now=late)
        case("obligation: past the grace deadline the episode is ESCALATED (an "
             "enforcement state -- growth still refused)",
             eps[0]["state"] == "escalated"
             and not brake(r, "wiki/topic/big.md", 300, 301, now=late)["allowed"], eps)
        write("deploy/rulings/cap-exception-v-big.md",
              "view: wiki/topic/big.md\nexpires: 2026-12-31\n\nOperator ruling: growth "
              "allowed while the Q4 migration lands.\n")
        f2 = []
        eps = episodes_for_view(r, "wiki/topic/big.md", now=late, findings=f2)
        case("an UNCOMMITTED cap exception is ignored with a finding (a ruling is a "
             "committed operator file)", eps[0]["state"] == "escalated"
             and any("not committed-identical" in x for x in f2), (eps[0]["state"], f2))
        commit("operator commits the exception", "2026-08-22T00:00:00")
        eps = episodes_for_view(r, "wiki/topic/big.md", now=late)
        bk = brake(r, "wiki/topic/big.md", 300, 301, now=late)
        case("a COMMITTED, unexpired exception -> exception-approved; the brake allows "
             "and names the ruling", eps[0]["state"] == "exception-approved"
             and bk["allowed"] and "cap-exception-v-big.md" in bk["reason"], (eps, bk))
        very_late = datetime.datetime(2027, 2, 1, tzinfo=datetime.timezone.utc)
        eps = episodes_for_view(r, "wiki/topic/big.md", now=very_late)
        case("an EXPIRED exception returns the episode to escalated (expiry is real)",
             eps[0]["state"] == "escalated"
             and eps[0].get("expired_exceptions"), eps)
        git("rm", "-q", "deploy/rulings/cap-exception-v-big.md")
        commit("exception withdrawn", "2026-08-23T00:00:00")

        # rename: the crossing clock does not reset (lineage-stable, --follow)
        git("mv", "wiki/topic/big.md", "wiki/topic/renamed.md")
        commit("rename the indebted view", "2026-08-23T01:00:00")
        eps = episodes_for_view(r, "wiki/topic/renamed.md", now=now)
        case("rename/move: the episode follows -- crossing commit UNCHANGED (a move "
             "never resets the grace clock)",
             len(eps) == 1 and eps[0]["crossing"]["commit"] == c_cross, eps)

        # published retirement discharges; a prepared-only one does not
        rec = {"run_type": "retire", "seq": 1, "tag": "retire/1",
               "view": "wiki/topic/renamed.md", "proposal": "deploy/rulings/retire-1/p.md",
               "proposal_digest": "sha256:" + "0" * 64,
               "spans": [{"title": "S", "bytes": 120}]}
        write("receipts/journal/1.json", json.dumps(rec))
        write("deploy/rulings/retire-1/p.md", "p\n")
        commit("a retirement record published on the chain", "2026-08-23T02:00:00")
        eps = episodes_for_view(r, "wiki/topic/renamed.md", now=now)
        case("published retirement bytes count against the debt (own episode still open "
             "on remaining bytes; retired_since = 120)",
             eps and eps[0]["retired_since"] == 120, eps)
        # shrink the view under cap -> own episode discharged (not emitted)
        write("wiki/topic/renamed.md", view_text("v-big", 150))
        commit("the retirement's stub view lands under cap", "2026-08-23T03:00:00")
        case("a view measuring under cap discharges its own episode",
             episodes_for_view(r, "wiki/topic/renamed.md", now=now) == [])

        # split: parent over cap -> superseded stub + parts; children born under cap
        write("wiki/topic/parent.md", view_text("v-parent", 500))
        commit("parent far over cap", "2026-08-10T00:00:00")
        stub = ("---\ntitle: parent\nstatus: superseded\n---\n" + REGION % "v-parent" +
                "\n```redirect-map\nparts:\n  - wiki/topic/childA.md\n"
                "  - wiki/topic/childB.md\nlines: {}\n```\n")
        write("wiki/topic/parent.md", stub)
        write("wiki/topic/childA.md", view_text("v-childa", 180))
        write("wiki/topic/childB.md", view_text("v-childb", 120))
        c_split = commit("split into two under-cap children", "2026-08-15T00:00:00")
        epsA = episodes_for_view(r, "wiki/topic/childA.md", now=now)
        epsB = episodes_for_view(r, "wiki/topic/childB.md", now=now)
        case("split NEVER discharges [R3-C4]: under-cap children carry apportioned "
             "inherited episodes (parent excess 300, apportioned by size)",
             len(epsA) == 1 and len(epsB) == 1 and epsA[0]["kind"] == "split"
             and epsB[0]["kind"] == "split"
             and epsA[0]["inherited_bytes"] + epsB[0]["inherited_bytes"] in (299, 300, 301)
             and epsA[0]["inherited_bytes"] > epsB[0]["inherited_bytes"], (epsA, epsB))
        bkA = brake(r, "wiki/topic/childA.md", 180, 200, now=now)
        case("brake: an under-cap child with inherited debt still refuses growth",
             not bkA["allowed"], bkA["reason"])
        case("the stub itself carries no episodes (superseded views are out of scope)",
             episodes_for_view(r, "wiki/topic/parent.md", now=now) == [])
        # a retirement on childA large enough discharges its inherited share
        recA = {"run_type": "retire", "seq": 2, "tag": "retire/2",
                "view": "wiki/topic/childA.md", "proposal": "deploy/rulings/retire-2/p.md",
                "proposal_digest": "sha256:" + "1" * 64,
                "spans": [{"title": "S", "bytes": epsA[0]["inherited_bytes"]}]}
        write("receipts/journal/2.json", json.dumps(recA))
        write("deploy/rulings/retire-2/p.md", "p\n")
        commit("childA retires its inherited share", "2026-08-16T00:00:00")
        case("retired hot bytes discharge the inherited episode (childA clear, childB "
             "still owes)", episodes_for_view(r, "wiki/topic/childA.md", now=now) == []
             and episodes_for_view(r, "wiki/topic/childB.md", now=now), None)

        # recreation at the same path inherits the deleted incarnation's excess
        write("wiki/topic/phoenix.md", view_text("v-phx", 400))
        commit("phoenix over cap", "2026-08-17T00:00:00")
        git("rm", "-q", "wiki/topic/phoenix.md")
        commit("phoenix deleted while owing", "2026-08-18T00:00:00")
        write("wiki/topic/phoenix.md", view_text("v-phx", 100))
        commit("phoenix recreated small (nonstandard path: direct write)",
               "2026-08-19T00:00:00")
        epsP = episodes_for_view(r, "wiki/topic/phoenix.md", now=now)
        case("recreation at the same path inherits the deleted incarnation's excess "
             "(200 bytes) -- covers nonstandard creation too (the key is the path)",
             len(epsP) == 1 and epsP[0]["kind"] == "recreate"
             and epsP[0]["inherited_bytes"] == 200, epsP)
        # equivalent-query creation: same view_id at a DIFFERENT path
        write("wiki/other/phoenix2.md", view_text("v-phx", 100))
        commit("same logical view recreated elsewhere (region copied)",
               "2026-08-19T01:00:00")
        epsQ = episodes_for_view(r, "wiki/other/phoenix2.md", now=now)
        case("equivalent-query creation: a view carrying the deleted view's view_id at a "
             "NEW path inherits too", len(epsQ) == 1 and epsQ[0]["kind"] == "recreate"
             and epsQ[0]["from"] == "wiki/topic/phoenix.md", epsQ)

        # G4 negative: a PREPARED (unpublished) retirement discharges nothing
        # simulate: a retire record NOT on the branch (a blob on a work ref only)
        pre_eps = episodes_for_view(r, "wiki/topic/childB.md", now=now)
        write("receipts/journal/99.json", json.dumps({
            "run_type": "retire", "seq": 99, "tag": "retire/99",
            "view": "wiki/topic/childB.md", "proposal": "x",
            "proposal_digest": "sha256:" + "2" * 64,
            "spans": [{"title": "S", "bytes": 10000}]}))
        # NOT committed: stays working-tree only
        eps2 = episodes_for_view(r, "wiki/topic/childB.md", now=now)
        case("G4: an uncommitted/unpublished retirement record counts NOTHING against "
             "debt (only the published first-parent chain discharges)",
             eps2 and eps2[0]["remaining"] == pre_eps[0]["remaining"], (pre_eps, eps2))
        os.remove(os.path.join(r, "receipts", "journal", "99.json"))

        # transitive inheritance (G4 catch): supersede the still-owing childB into a
        # merged view -- childB is under cap (own excess 0), so WITHOUT transitivity the
        # merge would clear its inherited debt without retiring a byte (reopen trigger d)
        owing = episodes_for_view(r, "wiki/topic/childB.md", now=now)[0]["remaining"]
        stubB = ("---\ntitle: childB\nstatus: superseded\n---\n" + REGION % "v-childb" +
                 "\n```redirect-map\nparts:\n  - wiki/topic/merged.md\nlines: {}\n```\n")
        write("wiki/topic/childB.md", stubB)
        write("wiki/topic/merged.md", view_text("v-merged", 100))
        commit("merge the owing child into a fresh under-cap view", "2026-08-20T00:00:00")
        epsM = episodes_for_view(r, "wiki/topic/merged.md", now=now)
        case("TRANSITIVE inheritance: a superseded intermediate passes its REMAINING "
             "inherited debt through (merge cannot clear aggregate debt -- reopen "
             "trigger d)", len(epsM) == 1 and epsM[0]["kind"] == "split"
             and epsM[0]["inherited_bytes"] == owing
             and epsM[0]["carried_transitively"] == owing, (owing, epsM))
        # round-1 fold: deleting a stub from the WORKING TREE does not hide the lineage
        # (stubs read from the branch tip's objects)
        os.remove(os.path.join(r, "wiki", "topic", "childB.md"))
        epsM2 = episodes_for_view(r, "wiki/topic/merged.md", now=now)
        case("an uncommitted worktree deletion of the stub does NOT hide inherited debt "
             "(lineage derives from committed state)",
             len(epsM2) == 1 and epsM2[0]["inherited_bytes"] == owing, epsM2)
        subprocess.run(["git", "-C", r, "checkout", "--", "wiki/topic/childB.md"],
                       capture_output=True)
        # corpus report shape
        alleps = episodes(r, now=now)
        case("corpus report: exactly the open episodes (merged + phoenix + phoenix2), "
             "render names states and obligations",
             {e["view"] for e in alleps} == {"wiki/topic/merged.md",
                                            "wiki/topic/phoenix.md",
                                            "wiki/other/phoenix2.md"}
             and "remaining" in render(alleps), alleps)
        # round-2 fold (c3): ONE retirement discharges ONE inherited byte TOTAL --
        # allocated oldest-crossing-first across multiple ancestors, never once per
        # episode (the round-2 verifier's constructed double-discharge)
        write("wiki/topic/childC.md", view_text("v-childc", 350))
        commit("childC born over cap", "2026-08-20T01:00:00")
        stubC = ("---\ntitle: childC\nstatus: superseded\n---\n" + REGION % "v-childc" +
                 "\n```redirect-map\nparts:\n  - wiki/topic/merged.md\nlines: {}\n```\n")
        write("wiki/topic/childC.md", stubC)
        commit("childC superseded into merged too", "2026-08-20T02:00:00")
        epsM3 = episodes_for_view(r, "wiki/topic/merged.md", now=now)
        total_before = sum(e["remaining"] for e in epsM3)
        case("multi-parent: merged carries BOTH ancestors' episodes",
             len(epsM3) == 2 and total_before == owing + 150, epsM3)
        write("receipts/journal/3.json", json.dumps({
            "run_type": "retire", "seq": 3, "tag": "retire/3",
            "view": "wiki/topic/merged.md", "proposal": "deploy/rulings/retire-3/p.md",
            "proposal_digest": "sha256:" + "3" * 64,
            "spans": [{"title": "S", "bytes": owing}]}))
        write("deploy/rulings/retire-3/p.md", "p\n")
        commit("one retirement on the merged view", "2026-08-20T03:00:00")
        epsM4 = episodes_for_view(r, "wiki/topic/merged.md", now=now)
        total_after = sum(e["remaining"] for e in epsM4)
        case("round-2 fold: one retirement discharges its bytes ONCE across multiple "
             "inherited episodes (oldest crossing first) -- never once per ancestor",
             total_after == total_before - owing
             and len(epsM4) == 1 and epsM4[0]["from"] == "wiki/topic/childC.md",
             (total_before, total_after, epsM4))
        # the canonical-fixture rule (v3.0-146): this tool parses compiled-view layout
        # (region keys, bodies), so at least one fixture is GENERATED by assemble.py's
        # own canonical shape -- with the shape guard, so the two layout models cannot
        # drift apart silently
        _asm = _load("_debt_asm", "assemble.py")
        canon = _asm._mk_view(entities=[], summary="canonical debt fixture") + \
            "\n## Canon\n\n" + ("c" * 400) + "\n"
        c_lines = canon.split("\n")
        c_fm_end = c_lines.index("---", 1)
        c_reg = _region_keys(canon)
        c_ds = next(i for i, ln in enumerate(c_lines) if ln.strip().startswith(_REGION_START))
        case("canonical-fixture guard: assemble's generated shape is frontmatter -> "
             "region -> body (divergence here fails this battery -- the v3.0-146 rule)",
             c_ds > c_fm_end
             and all(not ln.strip() for ln in c_lines[c_fm_end + 1:c_ds]), (c_fm_end, c_ds))
        write("wiki/topic/canonical.md", canon)
        commit("canonical assemble-generated over-cap view", "2026-08-21T00:00:00")
        epsC = episodes_for_view(r, "wiki/topic/canonical.md", now=now)
        case("canonical layout: an over-cap assemble-generated view (region at TOP) opens "
             "its own episode and the brake refuses growth -- the region parser and cap "
             "resolver read the canonical shape",
             len(epsC) == 1 and epsC[0]["kind"] == "own"
             and c_reg.get("view") == "topic" and "view_id" in c_reg
             and not brake(r, "wiki/topic/canonical.md", 500, 600, now=now)["allowed"],
             (epsC, c_reg))
    finally:
        _CAPS_OVERRIDE = None
        _GRACE_OVERRIDE = None
        shutil.rmtree(base, ignore_errors=True)
    print("debt.py self-test: %s (%d/%d)" % ("FAIL" if failed else "PASS",
                                             total - failed, total))
    return 1 if failed else 0


# ------------------------------------------------------------------ CLI
def main(argv=None):
    ap = argparse.ArgumentParser(prog="debt.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root", default=".")
    ap.add_argument("--branch", default=None)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--view", metavar="REL")
    ap.add_argument("--brake", nargs=3, metavar=("VIEW", "OLD", "NEW"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    root = os.path.abspath(a.root)
    try:
        if a.brake:
            v = brake(root, a.brake[0], int(a.brake[1]), int(a.brake[2]), a.branch)
            print(json.dumps(v, indent=1, default=str) if a.json else
                  ("ALLOWED: " if v["allowed"] else "REFUSED: ") + v["reason"])
            return 0 if v["allowed"] else 2
        findings = []
        if a.view:
            eps = episodes_for_view(root, a.view, a.branch, findings=findings)
        else:
            eps = episodes(root, a.branch, findings=findings)
        if a.json:
            print(json.dumps({"episodes": eps, "findings": findings}, indent=1,
                             default=str))
        else:
            print(render(eps, findings))
        return 2 if [e for e in eps if e["state"] in ("open", "escalated")] else 0
    except Refuse as e:
        print("REFUSED: %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
