#!/usr/bin/env python3
"""pending.py -- the durable pending list and the missed-observation alarm (ADR #11
condition 4 as amended 2026-08-22, binding item 4; v3.0.50, backlog v3.0-139 (b)/(c)).

THE LIST IS COMPUTED, NEVER STORED. Every retirement and every trust-surface change is
RECONSTRUCTED from git objects on the production branch's first-parent chain -- the commit,
its parent, author, the retire record's seq and proposal digest, the diff identity (sha256
of `git diff-tree -r parent commit` over the paths that make it an item) and, for a
retirement, the cold-object identity (path, sha256, blob) -- and an item stays PENDING until
an ATTENDED sweep has shown it to the operator. What IS stored is append-only and small:

  receipts/pending/sweeps.jsonl   heartbeats: {ts, kind: open|close, run_id, attended,
                                  outcome: ok|failed}  -- every sweep, attended or not
  receipts/pending/acks.jsonl     {ts, run_id, item, briefing, briefing_sha256}
                                  -- written ONLY by an attended sweep, for the items it
                                  rendered; an unattended (scheduled) sweep renders but
                                  never acknowledges, so an item nobody has read persists
  receipts/pending/alarms.jsonl   {ts, kind: missed-cycle|failed-cycle, observer, ...}
                                  -- written by the independent observer (the nightly
                                  standing-loop tick, `--observe`) when the configured
                                  observation window (project.yaml observation_window_days,
                                  default 7) passed without an attended sweep that closed
                                  ok, or a cycle failed; an alarm is itself a pending item

Why this shape is honest under the stated threat (a misled, wrong or unattended session --
not an adaptive adversary): deleting a journal record, rewriting an ack, or suppressing a
sweep cannot make an item disappear, because the item is re-derived from history every
run (a deleted record still appears: trust.py's history walk), a removed ack REOPENS the
item (the safe direction), and a missed cycle becomes an alarm row written by a process
that is not the session. An ack for an item that does not exist in history, or dated
before the item's commit, is reported as a finding. A same-user agent CAN append a forged
ack from an attended session -- that is inside the threat model's boundary and visible:
the ack row names its run_id and briefing, both of which the oracle can check.

Acknowledgement costs the operator nothing beyond reading the sweep ("acknowledge in the
sweep, nothing else"): the attended sweep records that it SHOWED the items. It is never a
second synchronous publication gate. What an ack row mechanically asserts (cross-vendor
round-1 fold, v3.0.50): the run_id it names has an `open` heartbeat (a sweep ran), the
briefing it names CONTAINS each acknowledged item's identity (the item was rendered), and
the run was attended. A bare `--ack` with an arbitrary run id or a briefing that does not
show the item REFUSES. A `close ok` without its `open` is ignored for the window and
reported as a finding. And "missed" is judged on the ITEMS, not only on heartbeats: any
item outstanding longer than the window is a missed observation no matter what the
heartbeat ledger says, so a fabricated cycle cannot hide an old unread change. What no
row can assert is that a human read the briefing -- that is the boundary the operator
chose ("acknowledge in the sweep, nothing else"), stated, not hidden. Ledger rows become
DURABLE at the commit the sweep itself makes (step 17 commits the receipt rows after the
ack): a row appended and deleted before that commit was never durable, exactly as an
uncommitted file anywhere is not -- the append-only check binds every committed version
and the working tree's extension of the newest one.

v3.0.54 (backlog v3.0-163, fleet inbox #14): an ack is ANCHORED to a committed briefing.
The first production instance's first attended close wrote 82 ack rows naming briefing
bytes that were edited three minutes later and committed nowhere -- every row void, the
pending count unable to reach zero, every later honest close forced to `failed`, and the
append-only rule forbidding the only remedy the doctor printed (delete the rows). Three
rules each right alone and jointly unsatisfiable. Two changes close the class:
  - ack() REFUSES when the briefing's working-tree bytes are not HEAD-identical (the
    briefing is committed first, then acknowledged) -- so an ack row can only ever name
    bytes git already holds, and rotating the briefing later can never void it;
  - status() prefers the LATEST VALID ack per item (rows newest-first, first that
    validates wins), so a re-ack from a committed briefing clears items an earlier void
    block left pending. Rows older than the winning ack are history and are not
    re-examined; void rows are findings ONLY while no valid ack exists for their item
    (a permanently-red sensor over a resolved state is the learn-to-ignore class this
    release exists to end). The chain stays append-only throughout: nothing is deleted,
    the remedy is one more row.
The recorded sha256 is always over the briefing's exact working-tree bytes; recovery
accepts the same bytes under either line-ending convention (`core.autocrlf` instances
check out LF blobs as CRLF files), so a legitimately committed briefing is recoverable
from git after rotation no matter which convention wrote it.

Usage:
  pending.py --root R --render [--json] [--branch main]     the table (sweep step 17)
  pending.py --root R --appendix                            the briefing's dashed machine
                                  appendix: one `- <item-id> (<date>)` line per pending
                                  item -- the exact lines that satisfy BOTH --ack's
                                  rendered-check and the briefing format contract
                                  (v3.0-159, fleet inbox #11: the render TABLE fails the
                                  format battery -- script names in the author column,
                                  non-dashed rows after Watching -- so the table lives in
                                  a committed receipt file the briefing cites, and THESE
                                  lines are what the briefing itself carries)
  pending.py --root R --heartbeat open|ok|failed --run-id ID
  pending.py --root R --ack --run-id ID --briefing PATH     attended sweeps only; PATH
                                  must be committed at HEAD (commit the briefing
                                  first, then ack -- v3.0-163)
  pending.py --root R --observe [--observer NAME]           the independent observer tick
  pending.py --self-test
Exit: 0 | 1 self-test failure | 2 refusal (e.g. --ack from an unattended run) or findings.
"""

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import re
import shutil
import socket
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


_trust = _load("_pending_trust", "trust.py")

PENDING_DIR = "receipts/pending"
SWEEPS = PENDING_DIR + "/sweeps.jsonl"
ACKS = PENDING_DIR + "/acks.jsonl"
ALARMS = PENDING_DIR + "/alarms.jsonl"
DEFAULT_WINDOW_DAYS = 7
OPEN_STALE_HOURS = 6  # an `open` heartbeat with no `close` after this long = a failed cycle
UNATTENDED_ENV = "RHEOSCOPE_UNATTENDED"


class Refuse(Exception):
    pass


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts):
    try:
        return datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except (ValueError, TypeError):
        try:
            return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return None


def attended():
    return os.environ.get(UNATTENDED_ENV, "").strip() not in ("1", "true", "yes")


def _git(repo, *args):
    p = subprocess.run(["git", "--no-replace-objects", "-C", repo] + list(args), capture_output=True)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def _blob_at(repo, commit, path):
    p = subprocess.run(["git", "--no-replace-objects", "-C", repo, "cat-file", "blob",
                        "%s:%s" % (commit, path)], capture_output=True)
    return p.stdout if p.returncode == 0 else None


def window_days(repo):
    p = os.path.join(repo, "project.yaml")
    try:
        text = open(p, encoding="utf-8-sig").read()
    except OSError:
        return DEFAULT_WINDOW_DAYS, "project.yaml absent -- default %d days" % DEFAULT_WINDOW_DAYS
    m = re.search(r"(?m)^\s*observation_window_days:\s*(\d+)\s*(#.*)?$", text)
    if not m:
        return DEFAULT_WINDOW_DAYS, ("observation_window_days not set in project.yaml -- default %d "
                                     "days" % DEFAULT_WINDOW_DAYS)
    return int(m.group(1)), "project.yaml observation_window_days: %s" % m.group(1)


def _append_only_defect(repo, rel):
    """A finding string when `rel`'s working-tree bytes are NOT an append-only extension of
    every committed version of the file on the current branch's first-parent chain; else
    None. Deleting or rewriting a receipt row (rather than appending) trips this."""
    try:
        cur = open(os.path.join(repo, rel.replace("/", os.sep)), "rb").read().replace(b"\r\n", b"\n")
    except OSError:
        cur = None
    rc, out, _ = _git(repo, "log", "--first-parent", "--format=%H", "--", rel)
    shas = out.split()
    if not shas:
        return None
    newest = _blob_at(repo, shas[0], rel)
    if newest is not None:
        newest = newest.replace(b"\r\n", b"\n")
        if cur is None:
            return ("%s was committed but is MISSING from the working tree -- receipt ledgers are "
                    "append-only (restore it: `git checkout -- %s`)" % (rel, rel))
        if not cur.startswith(newest):
            return ("%s in the working tree is not an append-only extension of its committed "
                    "version -- a receipt row was deleted or edited; restore with `git checkout -- "
                    "%s` (the reconstructed items are unaffected)" % (rel, rel))
    prev = None
    for sha in reversed(shas):  # oldest -> newest committed versions must chain by prefix
        b = _blob_at(repo, sha, rel)
        if b is None:
            continue
        b = b.replace(b"\r\n", b"\n")
        if prev is not None and not b.startswith(prev):
            return ("%s was rewritten in commit %s -- a committed receipt row was deleted or "
                    "edited; the ledger is append-only (the reconstructed items are unaffected)"
                    % (rel, sha[:12]))
        prev = b
    return None


def _rows(repo, rel):
    p = os.path.join(repo, rel.replace("/", os.sep))
    out = []
    try:
        with open(p, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    out.append({"_malformed": i, "_raw": line[:80]})
    except OSError:
        pass
    return out


def _append(repo, rel, row):
    p = os.path.join(repo, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


# ------------------------------------------------------------------ reconstruction
def _diff_identity(repo, parent, commit, paths=None):
    args = ["diff-tree", "-r", "--no-renames", parent, commit]
    if paths:
        args += ["--"] + list(paths)
    rc, out, _ = _git(repo, *args)
    return hashlib.sha256(out.encode("utf-8")).hexdigest() if rc == 0 else None


def _resolve_branch(repo, branch=None):
    """v3.0.52 (v3.0-151): the production branch through the one home
    (trust.production_branch: project.yaml key, else the checked-out branch); explicit
    wins; unresolvable refuses -- never a silent `main`. Doctor 16(f) inherits this
    default, so a non-main instance passes with no flag."""
    try:
        return _trust.resolve_branch(repo, branch)
    except _trust.TrustError as e:
        raise Refuse(str(e))


def reconstruct(repo, branch=None):
    """Every item on the branch's first-parent chain, tip-most first. Items:
    retire:<commit>  a commit that introduced/modified a retire record (trust.py's walk)
    trust:<commit>   a commit touching any trust-surface class path (hook-lane untracked
                     members excepted -- they are never in history)"""
    branch = _resolve_branch(repo, branch)
    head_rc, head, _ = _git(repo, "rev-parse", "--verify", "--quiet", "refs/heads/%s^{commit}" % branch)
    head = head.strip()
    if head_rc != 0:
        raise Refuse("production branch %s does not resolve" % branch)
    globs = _trust.load_class(repo)
    rc, out, _ = _git(repo, "log", "--first-parent", "--no-renames", "--name-only",
                      "--format=%x00%H%x09%P%x09%an <%ae>%x09%cI%x09%s", head)
    items = []
    seen_retire = set()
    records = {}
    for p, sha, rec in _trust._retire_records_history(repo, head):
        records.setdefault(sha, []).append((p, rec))
    for block in out.split("\x00"):
        lines = [l for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        hdr = lines[0].split("\t")
        sha, parents, author, date, subject = (hdr + ["", "", "", "", ""])[:5]
        parent = parents.split()[0] if parents else None
        paths = [l.strip() for l in lines[1:]]
        if sha in records:
            for p, rec in records[sha]:
                cold = rec.get("cold_objects") or []
                cold_ident = []
                for co in cold:
                    rc2, bl, _ = _git(repo, "rev-parse", "--verify", "--quiet", "%s:%s" % (sha, co.get("path", "")))
                    cold_ident.append({"path": co.get("path"), "sha256": co.get("sha256"),
                                       "blob": bl.strip() if rc2 == 0 else None})
                items.append({"id": "retire:%s" % sha, "kind": "retirement", "commit": sha,
                              "parent": parent, "author": author, "date": date, "subject": subject,
                              "seq": rec.get("seq"), "proposal_digest": rec.get("proposal_digest"),
                              "view": rec.get("view"), "record": p,
                              "batch": rec.get("batch"),
                              "rollback_of": rec.get("rollback_of"),
                              "diff_identity": _diff_identity(repo, parent, sha) if parent else None,
                              "cold_objects": cold_ident})
            seen_retire.add(sha)
        tpaths = [q for q in paths if _trust.in_class(q, globs)]
        if tpaths:
            items.append({"id": "trust:%s" % sha, "kind": "trust-surface", "commit": sha,
                          "parent": parent, "author": author, "date": date, "subject": subject,
                          "paths": tpaths,
                          "diff_identity": _diff_identity(repo, parent, sha, tpaths) if parent else None})
    # retire records introduced on the chain but whose commit the log did not list (cannot
    # happen on a first-parent walk; defensive) -- and DELETED records still count
    for sha, recs in records.items():
        if sha not in seen_retire:
            for p, rec in recs:
                items.append({"id": "retire:%s" % sha, "kind": "retirement", "commit": sha,
                              "seq": rec.get("seq"), "proposal_digest": rec.get("proposal_digest"),
                              "view": rec.get("view"), "record": p, "author": None, "parent": None,
                              "date": None, "diff_identity": None, "cold_objects": []})
    # publication status of every retire record (published / unpublished proposal / deleted)
    pub = {}
    for r in _trust.retire_records_status(repo, head):
        if r.get("commit"):
            pub.setdefault("retire:%s" % r["commit"], r)
    for it in items:
        if it["kind"] == "retirement":
            st = pub.get(it["id"])
            it["published"] = bool(st and st.get("published"))
            it["status"] = (st or {}).get("reason", "record present on the branch")
            it["authority"] = (st or {}).get("kind")
    return items, head


def status(repo, branch=None, now=None):
    now = now or _now()
    branch = _resolve_branch(repo, branch)
    items, head = reconstruct(repo, branch)
    acks = _rows(repo, ACKS)
    sweeps = _rows(repo, SWEEPS)
    alarms = _rows(repo, ALARMS)
    findings = []
    for rel, rows in ((ACKS, acks), (SWEEPS, sweeps), (ALARMS, alarms)):
        for r in rows:
            if "_malformed" in r:
                findings.append("%s line %d is not JSON (%s)" % (rel, r["_malformed"], r["_raw"]))
    # the receipt ledgers are APPEND-ONLY relative to their own git history (cross-vendor
    # round-9 fold): each historical version on the branch's first-parent chain must be a
    # prefix of the working-tree file -- a deleted or edited row (e.g. removing a
    # failed-cycle heartbeat and its alarm) is a finding, never silent.
    for rel in (ACKS, SWEEPS, ALARMS):
        f = _append_only_defect(repo, rel)
        if f:
            findings.append(f)
    ids = {it["id"]: it for it in items}
    for a in alarms:
        if "_malformed" in a:
            continue
        aid = "alarm:%s:%s" % (a.get("ts"), a.get("kind"))
        ids[aid] = {"id": aid, "kind": "alarm", "date": a.get("ts"), "author": a.get("observer"),
                    "detail": a.get("detail"), "commit": None, "parent": None}
        items.append(ids[aid])
    # v3.0-163 (fleet inbox #14): rows are grouped per item and examined NEWEST-FIRST;
    # the first row that validates acknowledges the item. Before v3.0.54 the OLDEST row
    # per item was kept and deleted on any defect without ever considering a newer row,
    # so one void block (acks naming never-committed briefing bytes) held every item
    # pending forever and the append-only rule forbade the only printed remedy.
    # the unknown-item rule (cross-vendor round-5/6 folds): a row naming an item that is not
    # in history is a finding UNTIL a later attended sweep has SHOWN it -- mechanically: a
    # later attended run that closed ok acknowledged a committed briefing whose text carries
    # this row's item id (the sweep skill carries every ledger finding into the briefing with
    # its item id in the details tail). Not "any later ok close": a wrong session must not be
    # able to retire the anomaly without a committed briefing that displays it. From then on
    # the row is history (superseded), never a permanent red, never deleted.
    any_opens = {r.get("run_id") for r in sweeps if r.get("kind") == "open"}
    unattended_opens = {r.get("run_id") for r in sweeps
                        if r.get("kind") == "open" and not r.get("attended")}
    # a run is attended for ACK purposes only when EVERY open row is attended (ack()'s own
    # rule, cross-vendor round-3 fold); a mixed run is named as such in the finding
    attended_opens = {r.get("run_id") for r in sweeps if r.get("kind") == "open" and r.get("attended")}
    mixed_opens = attended_opens & unattended_opens
    attended_opens = attended_opens - unattended_opens
    # a run's FIRST close row: an ack row dated after its run closed was not written by that
    # sweep (round-7 fold) -- every honest ack precedes its run's close
    close_ts = {}
    for r in sorted([r for r in sweeps if r.get("kind") == "close"], key=lambda r: r.get("ts") or ""):
        close_ts.setdefault(r.get("run_id"), r.get("ts") or "")
    _ok_runs = {r.get("run_id") for r in sweeps
                if r.get("kind") == "close" and r.get("outcome") == "ok" and r.get("attended")
                and r.get("run_id") in attended_opens}
    btext_cache = {}

    def _shown_later(row):
        """A later attended ok-closed run acknowledged a committed briefing that shows row's
        item -- through a PROOF ROW that is itself a VALID acknowledgement (round-7 fold: the
        proof row passes every check a real ack passes, including being dated before its run
        closed), never a bare later row."""
        rts, item = row.get("ts") or "", row.get("item") or ""
        needle = item.split(":", 1)[1][:12] if ":" in item and len(item.split(":", 1)[1]) >= 12 else item
        for b in acks:
            if "_malformed" in b or b is row or (b.get("ts") or "") <= rts or b.get("run_id") not in _ok_runs:
                continue
            bit = ids.get(b.get("item"))
            if bit is None or _ack_defect(repo, b, bit, any_opens, attended_opens, btext_cache,
                                          mixed_opens, close_ts) is not None:
                continue
            text = btext_cache.get((b.get("briefing"), b.get("briefing_sha256")), (None, None))[0]
            if text and needle and needle in text:
                return True
        return False

    superseded = 0
    rows_by_item = {}
    for idx, a in enumerate(acks):
        if "_malformed" in a:
            continue
        it = ids.get(a.get("item"))
        if it is None:
            if _shown_later(a):
                superseded += 1  # shown in a committed briefing a later attended sweep acked
            else:
                findings.append("ack for an item that is not in history: %s (run %s at %s) -- a "
                                "forged ack, or history was rewritten; it stays a finding until a "
                                "later attended sweep shows it (its committed briefing carries this "
                                "item id in a details tail), never delete the row"
                                % (a.get("item"), a.get("run_id"), a.get("ts")))
            continue
        # (ts, ledger position): two rows written in the same second order by position, so
        # "newest" is the last one appended (cross-vendor round-1 fold: a stable sort on
        # ts alone kept equal-second rows in ledger order, oldest first)
        rows_by_item.setdefault(a["item"], []).append((a.get("ts") or "", idx, a))
    # every STORED ack is re-validated the way ack() validates at write time (cross-vendor
    # round-3 fold): the run's `open` heartbeat must exist AND be attended, and the briefing
    # named must STILL render the item -- a directly-appended ledger row that skipped ack()
    # is caught here, its item stays open, and the row is a finding while no valid row
    # exists for the item.
    acked = {}
    for item_id, rows in rows_by_item.items():
        it = ids[item_id]
        reasons = []
        for _ts, _idx, a in sorted(rows, key=lambda r: (r[0], r[1]), reverse=True):
            why = _ack_defect(repo, a, it, any_opens, attended_opens, btext_cache, mixed_opens,
                              close_ts)
            if why is None:
                acked[item_id] = a
                break
            reasons.append(why)
        if item_id in acked:
            # void rows NEWER than the winning ack are superseded history, counted, never
            # findings; rows OLDER than the winner are not examined (the winner stands)
            superseded += len(reasons)
        else:
            findings.extend(reasons)
    pending = [it for it in items if it["id"] not in acked]
    # a rewound branch takes its committed ledgers with it: the remote-tracking detector is
    # the durable notice, so its finding rides here too (trust.py branch_rewind)
    for f in _trust.branch_rewind(repo, branch):
        findings.append("BRANCH REWOUND: " + f)
    # ---- observation window
    wdays, wnote = window_days(repo)
    opens = {}
    last_attended_ok = last_any_close = None
    failed = []
    paired_attended_ok = set()
    for r in sorted([r for r in sweeps if "_malformed" not in r], key=lambda r: r.get("ts") or ""):
        if r.get("kind") == "open":
            opens[r.get("run_id")] = r
        elif r.get("kind") == "close":
            o = opens.pop(r.get("run_id"), None)
            last_any_close = r.get("ts")
            if o is None:
                findings.append("heartbeat close for run %s at %s has no matching open -- not a "
                                "sweep cycle (ignored for the window)" % (r.get("run_id"), r.get("ts")))
                continue
            if r.get("outcome") == "ok" and r.get("attended") and o.get("attended"):
                last_attended_ok = r.get("ts")
                paired_attended_ok.add(r.get("run_id"))
            elif r.get("outcome") != "ok":
                failed.append(r)
    for rid, o in opens.items():
        od = _parse(o.get("ts"))
        if od and (now - od).total_seconds() > OPEN_STALE_HOURS * 3600:
            failed.append(dict(o, outcome="never closed"))
    # every failed cycle counts (round-3 fold): a failure followed by a later ok is NOT
    # excused -- the observer alarms on it once, and the alarm-row dedup keeps it from repeating.
    since = (now - _parse(last_attended_ok)).total_seconds() / 86400.0 if last_attended_ok else None
    # missed is judged on the ITEMS first: anything outstanding longer than the window is a
    # missed observation whatever the heartbeats say (a fabricated `ok` cannot hide it)
    overdue = []
    for it in pending:
        d = _parse(it.get("date") or "")
        if d and (now - d).total_seconds() > wdays * 86400:
            overdue.append(it["id"])
    missed = bool(overdue) or (since is None and bool(pending)) or (since is not None and since > wdays)
    obs = {"window_days": wdays, "window_note": wnote, "last_attended_ok": last_attended_ok,
           "overdue_items": overdue,
           "last_any_close": last_any_close, "days_since_attended": round(since, 2) if since is not None else None,
           "missed": missed, "failed_cycles": failed,
           "alarms_outstanding": [it for it in pending if it["kind"] == "alarm"]}
    return {"head": head, "items": items, "pending": pending, "acked": list(acked.values()),
            "superseded_ack_rows": superseded,
            "findings": findings, "observation": obs, "now": _iso(now)}


def _ack_defect(repo, a, it, any_opens, attended_opens, btext_cache=None, mixed_opens=(),
                close_ts=None):
    """Why this stored ack row does NOT acknowledge its item, or None when it does. The
    same checks ack() applies at write time, applied at read time (v3.0-163: one home,
    used newest-row-first per item)."""
    item_id = a.get("item")
    ad, idt = _parse(a.get("ts")), _parse(it.get("date") or "")
    if ad is None:
        # round-9 fold: an unparsable timestamp used to skip every chronology check; a row
        # with no readable time cannot be placed before or after anything, so it is void
        return "ack for %s carries no parseable timestamp (%r) -- not written by a sweep" % (
            item_id, a.get("ts"))
    if ad and idt and ad < idt:
        return "ack for %s is dated %s, BEFORE the item's commit (%s)" % (
            item_id, a.get("ts"), it.get("date"))
    if not a.get("run_id") or not a.get("briefing"):
        return "ack for %s names no run_id/briefing" % item_id
    rid = a.get("run_id")
    if rid not in any_opens:
        return ("ack for %s names run %s which has no sweep heartbeat -- not written by a sweep"
                % (item_id, rid))
    # the run's open rows must ALL be attended -- the same rule ack() applies at write time
    # (cross-vendor round-3 fold, v3.0.54: read-time used "any attended open", so a run
    # carrying both an attended and an unattended open row -- which ack() refuses outright
    # -- still validated a directly appended row)
    if rid not in attended_opens or rid in mixed_opens:
        return ("ack for %s names run %s whose sweep was UNATTENDED (or opened both attended "
                "and unattended) -- an unattended sweep renders but never acknowledges"
                % (item_id, rid))
    # an honest ack precedes its run's close (round-7 fold): a row dated after the run
    # closed was appended around the tool, not written by that sweep
    if close_ts and rid in close_ts and (a.get("ts") or "") > close_ts[rid]:
        return ("ack for %s is dated %s, AFTER its run %s closed (%s) -- not written by that "
                "sweep" % (item_id, a.get("ts"), rid, close_ts[rid]))
    key = (a.get("briefing"), a.get("briefing_sha256"))
    if btext_cache is not None and key in btext_cache:
        btext, committed_at = btext_cache[key]
    else:
        btext, committed_at = _briefing_from_git(repo, a.get("briefing"), a.get("briefing_sha256"),
                                                 btext_cache)
        if btext_cache is not None:
            btext_cache[key] = (btext, committed_at)
    # the briefing the ack names MUST be recoverable by its stored sha256 from a COMMIT
    # (cross-vendor round-7 fold, v3.0.53; git-only since the v3.0.54 round-4 fold): a
    # legitimate sweep's briefing is committed before it is acknowledged, so an ack naming
    # bytes no commit holds -- a fabricated or wrong-hash briefing, or (v3.0-163) bytes
    # edited before they were ever committed -- is not trusted.
    if btext is None:
        return ("ack for %s names briefing %s whose bytes cannot be recovered from any commit "
                "at the recorded sha256 -- an acknowledgement must show the item in a briefing "
                "that was committed; re-ack from a committed briefing (the next attended sweep "
                "does this), never delete the row" % (item_id, a.get("briefing")))
    # ...and that commit must PREDATE the row: bytes committed after the ack was written
    # were not what the ack was anchored to (round-4 fold -- otherwise a row appended
    # around the tool becomes valid the moment someone commits the bytes it named).
    # Stated boundary (round-5): both clocks have one-second resolution and an honest close
    # commits then acks within the same second, so EQUAL seconds are accepted; a row forged
    # around the tool and committed within that same second is inside the honest-observer
    # threat model (the row is in the ledger, its run id checkable), not a refusal.
    if ad and committed_at and ad.timestamp() < committed_at:
        return ("ack for %s is dated %s, BEFORE its briefing %s was first committed (%s) -- "
                "the row named bytes no commit held when it was written; re-ack from a "
                "committed briefing (the next attended sweep does this), never delete the row"
                % (item_id, a.get("ts"), a.get("briefing"),
                   datetime.datetime.fromtimestamp(committed_at, datetime.timezone.utc)
                   .strftime("%Y-%m-%dT%H:%M:%SZ")))
    if not rendered_in(btext, it):
        return ("ack for %s names briefing %s, but that briefing does not render the item -- "
                "the acknowledgement did not show it" % (item_id, a.get("briefing")))
    return None


# ------------------------------------------------------------------ writers
def heartbeat(repo, kind, run_id, outcome=None, now=None):
    if kind not in ("open", "ok", "failed"):
        raise Refuse("heartbeat kind must be open|ok|failed")
    row = {"ts": _iso(now or _now()), "run_id": run_id, "attended": attended(),
           "host": socket.gethostname(), "kind": "open" if kind == "open" else "close"}
    if kind != "open":
        row["outcome"] = kind
        if not any(r.get("kind") == "open" and r.get("run_id") == run_id for r in _rows(repo, SWEEPS)):
            raise Refuse("run %s has no `open` heartbeat -- a close without its open is not a sweep "
                         "cycle (nothing written)" % run_id)
    # v3.0-159, MECHANICAL half (cross-vendor round-1 fold: the ordering lived
    # only in the sweep skill's prose, so `--heartbeat ok` before a successful
    # ack was still writable): a run OPENED attended may close `ok` only when
    # nothing is left pending -- i.e. its ack succeeded (a successful ack
    # empties the pending list) or there was nothing to acknowledge. A run
    # opened unattended closes ok with items persisting BY DESIGN (it renders,
    # never acks -- and CANNOT ack, so gating its ok would deadlock it), and
    # `failed` is always writable -- that is the close a refused ack takes.
    # Attendance is derived from the run's OPEN heartbeat rows, the same
    # source ack() and the stored-ack re-validation read -- NEVER from the
    # close-time environment (cross-vendor round-3 fold: `attended()` at close
    # time let an attended-open run be closed `ok` from an unattended context,
    # skipping the guard).
    if kind == "ok":
        opens_for_run = [r for r in _rows(repo, SWEEPS)
                         if r.get("kind") == "open" and r.get("run_id") == run_id]
        # ANY attended open row arms the guard for this run id, permanently
        # (cross-vendor round-4 fold: with all(), appending one unattended open
        # row to an attended run id disarmed the guard; any() matches status()'s
        # own attended_opens classification, and a mixed-row run -- which ack()
        # refuses outright -- honestly closes `failed`, never `ok`).
        if any(r.get("attended") for r in opens_for_run):
            # Round-5 boundary, stated: the pending read and the row append are
            # not one transaction. An item that lands BETWEEN them is the
            # ordinary "arrived after the sweep" state, not a bypass -- this
            # ledger's doctrine computes everything at READ time (an ok row
            # never hides an item; the observer judges items, not rows), and
            # the module holds no locks anywhere (honest-observer threat
            # model, module docstring).
            st_pending = status(repo, None, now)["pending"]
            if st_pending:
                raise Refuse("run %s was opened ATTENDED and cannot close `ok` with %d item(s) "
                             "still pending un-acknowledged -- ack first (`--ack --run-id %s "
                             "--briefing <file>`; append the `--appendix` lines to the briefing "
                             "if items are not rendered), or close `failed` (v3.0-159)"
                             % (run_id, len(st_pending), run_id))
    _append(repo, SWEEPS, row)
    return row


def ack(repo, run_id, briefing, branch=None, now=None):
    """Attended sweeps ONLY. Acknowledges every currently pending item (the sweep rendered
    them into `briefing`)."""
    if not attended():
        raise Refuse("this is an UNATTENDED run (%s set): it renders the pending list but never "
                     "acknowledges it -- only a sweep the operator reads does" % UNATTENDED_ENV)
    bp = os.path.join(repo, briefing.replace("/", os.sep))
    try:
        btext_raw = open(bp, "rb").read()
    except OSError:
        raise Refuse("briefing %s is not readable -- nothing was shown, nothing is acknowledged" % briefing)
    # the recorded hash is over the LF-normalized CONTENT (cross-vendor round-1 fold,
    # v3.0.54): a working file with mixed line endings is HEAD-identical by content, and
    # only a content hash is recoverable from its blob after the file rotates. Rows
    # written before v3.0.54 recorded the raw working-tree bytes; recovery accepts those
    # through _sha_variants, so no existing row is orphaned by this change.
    bsha = hashlib.sha256(btext_raw.replace(b"\r\n", b"\n")).hexdigest()
    btext = btext_raw.decode("utf-8", "replace")
    opens = [r for r in _rows(repo, SWEEPS) if r.get("kind") == "open" and r.get("run_id") == run_id]
    if not opens:
        raise Refuse("run %s has no `open` heartbeat -- an acknowledgement rides a sweep that "
                     "ran (`--heartbeat open --run-id %s` first); nothing is acknowledged" % (run_id, run_id))
    if not all(r.get("attended") for r in opens):
        raise Refuse("run %s was opened UNATTENDED -- it cannot acknowledge" % run_id)
    # v3.0-163 (fleet inbox #14): the briefing must be COMMITTED before it is acknowledged.
    # An ack names the briefing by sha256; when those bytes are never committed, the row
    # is void the moment the file changes and nothing can ever recover it -- the first
    # production instance lost 82 rows exactly this way. HEAD-identical (line endings
    # aside: autocrlf checkouts differ from their blobs by convention only) is the one
    # mechanical guarantee that the bytes an ack names are bytes git holds.
    head_why = _head_identical_defect(repo, briefing, btext_raw)
    if head_why:
        raise Refuse("%s -- commit the briefing first, then ack (v3.0-163: an acknowledgement "
                     "anchored to uncommitted bytes is void as soon as the file changes, and the "
                     "append-only ledger cannot take the row back; nothing is acknowledged)"
                     % head_why)
    ts_dt = now or _now()
    # the anchoring commit must not be dated AFTER this clock (cross-vendor round-8 fold): a
    # skewed committer date would let the ack succeed and then read as void ("dated before
    # its briefing was first committed"); refuse here, naming the cause, rather than write a
    # row the ledger can never honor
    rc_c, ct_out, _ = _git(repo, "log", "-1", "--format=%ct", "HEAD", "--", briefing.replace("\\", "/"))
    try:
        head_ct = int(ct_out.strip())
    except ValueError:
        head_ct = None
    if head_ct is not None and head_ct > int(ts_dt.timestamp()):
        raise Refuse("briefing %s's newest commit is dated %s, AFTER this clock (%s) -- a "
                     "committer date in the future (clock skew) would leave the acknowledgement "
                     "void; fix the clock or re-commit the briefing, then ack (nothing is "
                     "acknowledged)" % (briefing, datetime.datetime.fromtimestamp(
                         head_ct, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), _iso(ts_dt)))
    st = status(repo, branch, now)
    ts = _iso(ts_dt)
    rows, not_shown = [], []
    for it in st["pending"]:
        if not rendered_in(btext, it):
            not_shown.append(it["id"])
            continue
        row = {"ts": ts, "run_id": run_id, "item": it["id"], "briefing": briefing.replace("\\", "/"),
               "briefing_sha256": bsha}
        _append(repo, ACKS, row)
        rows.append(row)
    if not_shown:
        raise Refuse("%d pending item(s) are NOT rendered in %s and stay pending: %s -- an "
                     "acknowledgement covers only what the briefing shows%s. Rendered = the "
                     "item's commit-first-12 (or an alarm's timestamp) appears as a substring; "
                     "append the `--appendix` output (one dashed bare-id line per item) to the "
                     "briefing's Watching section and re-ack -- that shape passes the briefing "
                     "format battery too (v3.0-159)" % (
                         len(not_shown), briefing, ", ".join(not_shown)[:300],
                         " (%d item(s) that were shown are acknowledged)" % len(rows) if rows else ""))
    return rows


def _sha_variants(raw):
    """sha256 of the bytes as given, LF-normalized, and CRLF-normalized: the same briefing
    under either line-ending convention (v3.0-163: `core.autocrlf` instances hold LF blobs
    and CRLF working files, so an ack's working-tree sha must still find its blob)."""
    lf = raw.replace(b"\r\n", b"\n")
    return {hashlib.sha256(raw).hexdigest(), hashlib.sha256(lf).hexdigest(),
            hashlib.sha256(lf.replace(b"\n", b"\r\n")).hexdigest()}


def _head_identical_defect(repo, rel, working_bytes):
    """A sentence when `rel`'s working-tree bytes are not what HEAD holds (untracked at
    HEAD, or edited since the last commit -- line endings aside); None when identical."""
    rel = rel.replace("\\", "/")
    blob = _blob_at(repo, "HEAD", rel)
    if blob is None:
        return "briefing %s is not committed at HEAD (untracked or never committed)" % rel
    if blob.replace(b"\r\n", b"\n") != working_bytes.replace(b"\r\n", b"\n"):
        return "briefing %s differs from its committed version at HEAD (edited since the last commit)" % rel
    return None


def _briefing_from_git(repo, rel, want_sha, cache=None):
    """(text, earliest commit time) of a COMMITTED version of `rel` whose bytes hash to the
    recorded sha256 under either line-ending convention; (None, None) when no commit holds
    them. The working tree never counts (cross-vendor round-4 fold, v3.0.54): an ack is
    anchored to a commit that existed when the row was written -- a row naming bytes that
    were committed only LATER (or never) is void, so a directly appended row cannot be
    made valid by committing its bytes afterwards, and a void block cannot be resurrected
    that way either. The caller compares the ack's timestamp with the commit time."""
    if not rel:
        return None, None
    cache = cache if cache is not None else {}
    key = ("log", rel)
    if key not in cache:
        rc, out, _ = _git(repo, "log", "--all", "--format=%H %ct", "--", rel)
        cache[key] = [l.split() for l in out.splitlines() if len(l.split()) == 2]
    best = None
    for sha, ct in cache[key]:
        bkey = ("blob", sha, rel)
        if bkey not in cache:
            b = _blob_at(repo, sha, rel)
            cache[bkey] = (b, _sha_variants(b) if b is not None else set())
        b, variants = cache[bkey]
        if b is not None and want_sha in variants:
            ct = int(ct)
            if best is None or ct < best[1]:
                best = (b, ct)
    return (best[0].decode("utf-8", "replace"), best[1]) if best else (None, None)


def rendered_in(briefing_text, item):
    """The briefing SHOWS the item. What "rendered" means, MECHANICALLY
    (v3.0-159 -- this was undocumented and the first attended close on the
    first production instance had to reverse-engineer it): a plain substring
    match against the briefing's decoded bytes -- the item's commit sha's
    first 12 hex characters for commit-backed items (retirements and
    trust-surface changes; the item id `kind:<full-sha>` contains them, so a
    bare-id line suffices), the item's full `date` timestamp for alarms.
    No table, no layout, no particular section is required -- the dashed
    machine appendix `--appendix` emits satisfies this for every item kind,
    and is the standardized shape (sweep step 17(b))."""
    if item.get("kind") == "alarm":
        return bool(item.get("date")) and item["date"] in briefing_text
    c = item.get("commit") or ""
    return len(c) >= 12 and c[:12] in briefing_text


def observe(repo, observer="standing-loop", branch=None, now=None):
    """The independent observer: writes an alarm row when the window was missed or a cycle
    failed, unless an identical alarm is already outstanding. Returns (status, new_rows)."""
    now = now or _now()
    st = status(repo, branch, now)
    obs = st["observation"]
    existing = [a for a in _rows(repo, ALARMS) if "_malformed" not in a]
    new = []
    if obs["missed"]:
        key = obs["last_attended_ok"] or "never"
        if not any(a.get("kind") == "missed-cycle" and a.get("last_attended_ok") == key
                   and "alarm:%s:missed-cycle" % a.get("ts") in {p["id"] for p in st["pending"]}
                   for a in existing):
            row = {"ts": _iso(now), "kind": "missed-cycle", "observer": observer,
                   "window_days": obs["window_days"], "last_attended_ok": key,
                   "detail": "no attended sweep closed ok in the last %s day(s) (window %d); %d item(s) pending"
                             % (obs["days_since_attended"] if obs["days_since_attended"] is not None else "?",
                                obs["window_days"], len([p for p in st["pending"] if p["kind"] != "alarm"]))}
            _append(repo, ALARMS, row)
            new.append(row)
    for f in obs["failed_cycles"]:
        key = "%s:%s" % (f.get("run_id"), f.get("ts"))
        if not any(a.get("kind") == "failed-cycle" and a.get("cycle") == key for a in existing):
            row = {"ts": _iso(now), "kind": "failed-cycle", "observer": observer, "cycle": key,
                   "detail": "sweep run %s (%s) %s" % (f.get("run_id"), f.get("ts"),
                                                      "failed" if f.get("outcome") == "failed" else "never closed")}
            _append(repo, ALARMS, row)
            new.append(row)
    return st, new


# ------------------------------------------------------------------ render
def render_appendix(st):
    """The briefing's dashed MACHINE APPENDIX (v3.0-159, fleet inbox #11): one
    `- <item-id> (<date>)` line per pending item, nothing else. Bare item ids
    carry the full commit sha (so rendered_in's first-12 substring match is
    satisfied), alarm lines carry the full timestamp both in the id and the
    parenthetical; no script names, no table rows, no fences -- every line is
    dash-prefixed, so the block passes the briefing format contract's
    watching-dashed-list and prose rows verbatim. The human-readable table
    (render() below) belongs in a committed receipt file the briefing cites
    from a details tail, never in the briefing itself."""
    lines = []
    for it in st["pending"]:
        lines.append("- %s (%s)" % (it["id"], it.get("date") or "undated"))
    if not st["pending"]:
        lines.append("- nothing pending")
    return "\n".join(lines)


def render(st):
    out = []
    obs = st["observation"]
    out.append("pending list (reconstructed from git objects on the production branch; head %s)" % st["head"][:12])
    out.append("observation window: %d day(s) (%s); last attended sweep ok: %s; %s" % (
        obs["window_days"], obs["window_note"], obs["last_attended_ok"] or "never",
        "MISSED" if obs["missed"] else "within window"))
    if obs["failed_cycles"]:
        out.append("failed cycles since the last attended ok: %d" % len(obs["failed_cycles"]))
    out.append("%-9s %-12s %-24s %-20s %s" % ("kind", "commit", "author", "date", "detail"))
    for it in st["pending"]:
        if it["kind"] == "retirement":
            btag = ""
            if it.get("batch"):
                btag = "batch %s member %s/%s -- " % (it["batch"].get("id"),
                                                     (it["batch"].get("index") or 0) + 1,
                                                     it["batch"].get("n"))
            if it.get("rollback_of") is not None:
                btag += "ROLLBACK of seq %s -- " % it["rollback_of"]
            detail = "%sseq %s view %s digest %s -- %s%s" % (
                btag, it.get("seq"), it.get("view"), (it.get("proposal_digest") or "?")[7:19],
                "PUBLISHED (%s)" % it.get("authority") if it.get("published") else "UNPUBLISHED PROPOSAL",
                "" if it.get("published") else ": " + str(it.get("status"))[:120])
        elif it["kind"] == "trust-surface":
            detail = ", ".join(it.get("paths", []))[:160]
        else:
            detail = "ALARM %s" % it.get("detail")
        out.append("%-9s %-12s %-24s %-20s %s" % (it["kind"][:9], (it.get("commit") or "")[:12],
                                                   (it.get("author") or "")[:24], (it.get("date") or "")[:20], detail))
    if not st["pending"]:
        out.append("(nothing pending)")
    out.append("acknowledged: %d item(s); pending: %d" % (len(st["acked"]), len(st["pending"])))
    if st.get("superseded_ack_rows"):
        out.append("superseded ack rows: %d (void rows newer than a valid ack for the same item -- "
                   "history, not findings)" % st["superseded_ack_rows"])
    for f in st["findings"]:
        out.append("FINDING: " + f)
    return "\n".join(out)


# ------------------------------------------------------------------ self-test
def self_test():
    failed = total = 0

    def case(name, cond, detail=""):
        nonlocal failed, total
        total += 1
        if not cond:
            failed += 1
        print("  %s %s%s" % ("ok " if cond else "XX ", name, ("  [%s]" % str(detail)[:300]) if detail and not cond else ""))

    if shutil.which("git") is None:
        print("pending.py self-test: INCONCLUSIVE -- git required")
        return 2
    base = tempfile.mkdtemp(prefix="pending-selftest-")
    saved_env = os.environ.get(UNATTENDED_ENV)
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

        def commit(msg, author="tester <t@t>"):
            git("add", "-A")
            n, e = author.split(" <")
            p = git("-c", "user.name=%s" % n, "-c", "user.email=%s" % e[:-1], "commit", "-q", "-m", msg)
            assert p.returncode == 0, p.stderr
            return git("rev-parse", "HEAD").stdout.strip()

        def commit_path(rel, msg):
            # v3.0-163: a briefing is COMMITTED before it is acknowledged -- only that
            # path, so the receipt ledgers' own commit history stays what each case built
            git("add", "--", rel)
            p = git("commit", "-q", "-m", msg)
            assert p.returncode == 0, p.stderr

        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@t")
        git("config", "user.name", "tester")
        git("config", "commit.gpgsign", "false")
        os.environ.pop(UNATTENDED_ENV, None)
        write("README.md", "x\n")
        write("project.yaml", "trust_surface_signing: visible\nobservation_window_days: 3\n")
        c0 = commit("seed")
        st = status(r)
        case("empty history: nothing pending, window read from project.yaml (3 days)",
             st["pending"] == [] and st["observation"]["window_days"] == 3, st["observation"])
        write("core/security/hooks/egress-allowlist.txt", "# none\n")
        c1 = commit("operator adds the allowlist", author="Ryan <r@r>")
        write("deploy/evidence/operator-grant.md", "grant\n")
        write("notes.md", "not a trust surface\n")
        c2 = commit("a session commits a grant + a note")
        st = status(r)
        case("trust-surface commits reconstructed as items (2), non-class commit ignored",
             [it["id"] for it in st["pending"]] == ["trust:%s" % c2, "trust:%s" % c1]
             and st["pending"][0]["paths"] == ["deploy/evidence/operator-grant.md"], st["pending"])
        case("item carries parent, author, date, diff identity",
             st["pending"][1]["parent"] == c0 and st["pending"][1]["author"].startswith("Ryan")
             and st["pending"][1]["date"] and len(st["pending"][1]["diff_identity"]) == 64)
        case("no attended sweep ever + pending items -> observation MISSED",
             st["observation"]["missed"] is True)
        # a retire record on the branch (unpromoted -> unpublished proposal)
        write("deploy/rulings/retire-1/proposal.md", "p\n")
        write("wiki/cold/v/s--abc.md", "cold\n")
        write("receipts/journal/1.json", json.dumps({
            "run_type": "retire", "seq": 1, "tag": "retire/1", "view": "wiki/v.md",
            "proposal": "deploy/rulings/retire-1/proposal.md",
            "proposal_digest": "sha256:" + hashlib.sha256(b"p\n").hexdigest(),
            "cold_objects": [{"path": "wiki/cold/v/s--abc.md", "sha256": hashlib.sha256(b"cold\n").hexdigest()}]}))
        c3 = commit("agent script journals a retirement directly on main")
        st = status(r)
        rit = [it for it in st["pending"] if it["kind"] == "retirement"]
        case("a retire record journaled by ANY path is a pending item with seq, digest, cold identity "
             "(path/sha256/blob) and reads UNPUBLISHED", len(rit) == 1 and rit[0]["seq"] == 1
             and rit[0]["cold_objects"][0]["blob"] and not rit[0]["published"]
             and "UNPUBLISHED" in render(st), rit)
        case("the same commit is ALSO a trust-surface item (deploy/rulings/** is in the class)",
             any(it["id"] == "trust:%s" % c3 for it in st["pending"]))
        # heartbeats + ack by an attended sweep
        t0 = (_now() + datetime.timedelta(minutes=1)).replace(microsecond=0)  # after the commits above
        try:
            ack(r, "run-0", "README.md", now=t0)
            case("ack without a sweep heartbeat refused", False)
        except Refuse as e:
            case("round-1 fold: --ack naming a run with no `open` heartbeat REFUSES (an ack rides a sweep "
                 "that ran)", "no `open` heartbeat" in str(e), e)
        try:
            heartbeat(r, "ok", "run-0", now=t0)
            case("close without open refused", False)
        except Refuse as e:
            case("round-1 fold: a `close` heartbeat with no `open` REFUSES (not a cycle)", "no `open`" in str(e), e)
        heartbeat(r, "open", "run-1", now=t0)
        write("SWEEP-BRIEFING.md", "briefing that shows nothing\n")
        commit_path("SWEEP-BRIEFING.md", "briefing 1")
        try:
            ack(r, "run-1", "SWEEP-BRIEFING.md", now=t0)
            case("ack with a briefing that renders nothing refused", False)
        except Refuse as e:
            case("round-1 fold: a briefing that does not SHOW the items acknowledges nothing (all 4 stay "
                 "pending)", "NOT rendered" in str(e) and len(status(r, now=t0)["pending"]) == 4, e)
        st0 = status(r, now=t0)
        write("SWEEP-BRIEFING.md", "briefing with the items\n" + "\n".join(
            "- %s %s" % (it["kind"], it["commit"][:12]) for it in st0["pending"]) + "\n")
        commit_path("SWEEP-BRIEFING.md", "briefing 2")
        rows = ack(r, "run-1", "SWEEP-BRIEFING.md", now=t0)
        heartbeat(r, "ok", "run-1", now=t0)
        st = status(r, now=t0)
        case("attended sweep acknowledges every pending item (4 rows) -> nothing pending",
             len(rows) == 4 and st["pending"] == [] and len(st["acked"]) == 4, (len(rows), st["pending"]))
        case("ack rows name run_id + briefing + briefing sha256",
             rows[0]["run_id"] == "run-1" and rows[0]["briefing"] == "SWEEP-BRIEFING.md"
             and len(rows[0]["briefing_sha256"]) == 64)
        case("observation: last attended ok recorded; within window", st["observation"]["last_attended_ok"]
             == _iso(t0) and not st["observation"]["missed"])
        # a new trust-surface change after the ack -> pending again
        write("core/security/hooks/egress-allowlist.txt", "# none\ncurl api.example.com\n")
        c4 = commit("widen the allowlist (unsigned)")
        st = status(r, now=t0)
        case("a later change is pending on its own; earlier acks stay", len(st["pending"]) == 1
             and st["pending"][0]["commit"] == c4)
        # unattended sweep renders but cannot ack
        os.environ[UNATTENDED_ENV] = "1"
        try:
            ack(r, "run-2", "SWEEP-BRIEFING.md", now=t0)
            case("unattended ack refused", False)
        except Refuse as e:
            case("an UNATTENDED run cannot acknowledge (renders only): the unread item persists",
                 "UNATTENDED" in str(e) and len(status(r, now=t0)["pending"]) == 1, e)
        hb = heartbeat(r, "open", "run-2", now=t0 + datetime.timedelta(days=1))
        heartbeat(r, "ok", "run-2", now=t0 + datetime.timedelta(days=1))
        case("unattended heartbeat recorded as attended=False", hb["attended"] is False)
        os.environ.pop(UNATTENDED_ENV, None)
        st = status(r, now=t0 + datetime.timedelta(days=2))
        case("an unattended ok close does NOT advance last_attended_ok",
             st["observation"]["last_attended_ok"] == _iso(t0))
        # window: 3 days -> at day 4 the observer raises a missed-cycle alarm (durable row)
        t4 = t0 + datetime.timedelta(days=4)
        st, new = observe(r, observer="standing-loop", now=t4)
        case("independent observer at day 4 (window 3): MISSED -> one missed-cycle alarm row written",
             st["observation"]["missed"] and len(new) == 1 and new[0]["kind"] == "missed-cycle"
             and os.path.isfile(os.path.join(r, ALARMS.replace("/", os.sep))), (st["observation"], new))
        st = status(r, now=t4)
        case("the alarm is itself a pending item (outstanding until an attended sweep)",
             any(it["kind"] == "alarm" for it in st["pending"]) and "ALARM" in render(st))
        st2, new2 = observe(r, observer="standing-loop", now=t4 + datetime.timedelta(days=1))
        case("a second observer tick does not duplicate an outstanding alarm", new2 == [], new2)
        # failed cycle: an open heartbeat never closed
        heartbeat(r, "open", "run-3", now=t4)
        st, new = observe(r, now=t4 + datetime.timedelta(hours=OPEN_STALE_HOURS + 1))
        case("an `open` heartbeat with no close after %dh is a FAILED cycle -> failed-cycle alarm"
             % OPEN_STALE_HOURS, any(n["kind"] == "failed-cycle" for n in new), new)
        heartbeat(r, "open", "run-4", now=t4 + datetime.timedelta(days=1))
        heartbeat(r, "failed", "run-4", now=t4 + datetime.timedelta(days=1))
        st, new = observe(r, now=t4 + datetime.timedelta(days=1, hours=1))
        case("an explicit failed close is a failed-cycle alarm too", any(n.get("cycle", "").startswith("run-4") for n in new), new)
        # attended sweep clears everything incl. alarms
        t6 = t4 + datetime.timedelta(days=2)
        heartbeat(r, "open", "run-5", now=t6)
        st5 = status(r, now=t6)
        write("SWEEP-BRIEFING.md", "the operator reads this one\n" + "\n".join(
            "- %s %s" % (it["kind"], (it.get("commit") or it.get("date"))) for it in st5["pending"]) + "\n")
        commit_path("SWEEP-BRIEFING.md", "briefing 3")
        rows = ack(r, "run-5", "SWEEP-BRIEFING.md", now=t6)
        heartbeat(r, "ok", "run-5", now=t6)
        st = status(r, now=t6)
        case("the next attended sweep acknowledges the change AND the alarms; nothing pending",
             st["pending"] == [] and not st["observation"]["missed"], (rows, st["pending"]))
        # tamper direction 1: delete an ack row -> the item REOPENS (safe direction)
        ap = os.path.join(r, ACKS.replace("/", os.sep))
        lines = open(ap, encoding="utf-8").read().splitlines()
        keep = [l for l in lines if '"trust:%s"' % c4 not in l]
        open(ap, "w", encoding="utf-8", newline="\n").write("\n".join(keep) + "\n")
        st = status(r, now=t6)
        case("deleting an ack row REOPENS the item (tampering only ever makes more visible)",
             len(st["pending"]) == 1 and st["pending"][0]["commit"] == c4)
        # round-1 fold: an ack naming a run with no heartbeat, and a fabricated paired cycle
        _append(r, ACKS, {"ts": _iso(t6), "run_id": "ghost", "item": "trust:%s" % c4,
                          "briefing": "SWEEP-BRIEFING.md", "briefing_sha256": "0" * 64})
        st = status(r, now=t6)
        case("round-1 fold: an ack naming a run with NO heartbeat is a finding and does not acknowledge",
             any("no sweep heartbeat" in f for f in st["findings"]) and len(st["pending"]) == 1, st["findings"])
        ap2 = os.path.join(r, ACKS.replace("/", os.sep))
        _kept = [l for l in open(ap2, encoding="utf-8").read().splitlines() if '"ghost"' not in l]
        open(ap2, "w", encoding="utf-8", newline="\n").write("\n".join(_kept) + "\n")
        t30 = t6 + datetime.timedelta(days=30)
        heartbeat(r, "open", "fake-1", now=t30)
        # v3.0-159 round-1 fold, both directions: the TOOL refuses to write an
        # attended `ok` over a pending un-acked item...
        try:
            heartbeat(r, "ok", "fake-1", now=t30)
            case("v3.0-159: attended `ok` with items pending un-acked REFUSES "
                 "naming the ack recipe", False)
        except Refuse as e:
            case("v3.0-159: attended `ok` with items pending un-acked REFUSES "
                 "naming the ack recipe",
                 "ack first" in str(e) and "--appendix" in str(e), e)
        case("v3.0-159: the refused `ok` wrote no close row",
             not any(r2.get("run_id") == "fake-1" and r2.get("kind") == "close"
                     for r2 in _rows(r, SWEEPS)))
        # round-3 fold: attendance is judged from the OPEN row, so flipping the
        # env at close time cannot skip the guard
        os.environ[UNATTENDED_ENV] = "1"
        try:
            heartbeat(r, "ok", "fake-1", now=t30)
            case("v3.0-159 round-3 fold: an attended-OPEN run cannot be closed "
                 "`ok` from an unattended context while items are pending", False)
        except Refuse as e:
            case("v3.0-159 round-3 fold: an attended-OPEN run cannot be closed "
                 "`ok` from an unattended context while items are pending",
                 "opened ATTENDED" in str(e), e)
        # round-4 fold: appending a second, UNATTENDED open row to the same run
        # id does not disarm the guard (any(), not all())
        try:
            heartbeat(r, "open", "fake-1", now=t30)  # unattended env still set
            heartbeat(r, "ok", "fake-1", now=t30)
            case("v3.0-159 round-4 fold: a mixed-open-rows run id still cannot "
                 "close `ok` over pending items", False)
        except Refuse as e:
            case("v3.0-159 round-4 fold: a mixed-open-rows run id still cannot "
                 "close `ok` over pending items", "opened ATTENDED" in str(e), e)
        finally:
            os.environ.pop(UNATTENDED_ENV, None)
        # ...`failed` stays writable (the close a refused ack takes)...
        heartbeat(r, "failed", "fake-1", now=t30)
        # ...and a forged row APPENDED DIRECTLY (the adversary the ledger
        # doctrine contemplates -- it bypasses the tool) still cannot hide the
        # missed item, which is what the original round-1 case pinned:
        _append(r, SWEEPS, {"ts": _iso(t30), "run_id": "fake-1b", "attended": True,
                            "host": "forge", "kind": "open"})
        _append(r, SWEEPS, {"ts": _iso(t30), "run_id": "fake-1b", "attended": True,
                            "host": "forge", "kind": "close", "outcome": "ok"})
        st = status(r, now=t30)
        case("round-1 fold: an item outstanding longer than the window is MISSED even when a paired "
             "attended `ok` cycle was just recorded (missed is judged on the items)",
             st["observation"]["missed"] and st["observation"]["overdue_items"] == ["trust:%s" % c4]
             and [x["id"] for x in st["pending"]] == ["trust:%s" % c4],
             st["observation"])
        # tamper direction 2: a forged ack for a commit not in history -> finding (dated after
        # every attended ok close in the ledger so far -- v3.0.54: such a row becomes history
        # once an attended sweep has closed ok after it, the sweep that showed it)
        t31 = t30 + datetime.timedelta(days=1)
        _append(r, ACKS, {"ts": _iso(t31), "run_id": "run-x", "item": "trust:%s" % ("f" * 40),
                          "briefing": "SWEEP-BRIEFING.md", "briefing_sha256": "0" * 64})
        st = status(r, now=t31)
        case("an ack naming an item that is not in history is a FINDING (forged ack / rewritten history)",
             any("not in history" in f for f in st["findings"]), st["findings"])
        # tamper direction 3: an ack dated before the item's commit -> finding, item still pending
        _append(r, ACKS, {"ts": "2020-01-01T00:00:00Z", "run_id": "run-y", "item": "trust:%s" % c4,
                          "briefing": "SWEEP-BRIEFING.md", "briefing_sha256": "0" * 64})
        st = status(r, now=t6)
        case("an ack dated BEFORE the item's commit is a finding and does not acknowledge",
             any("BEFORE" in f for f in st["findings"]) and len(st["pending"]) == 1, st["findings"])
        # round-3 fold: a directly-appended ack (skipping ack()) naming a run whose `open` was
        # UNATTENDED is caught by status() re-validation -- the item reopens and it is a finding
        os.environ[UNATTENDED_ENV] = "1"
        heartbeat(r, "open", "sneaky", now=t6)  # an unattended open
        os.environ.pop(UNATTENDED_ENV, None)
        _append(r, ACKS, {"ts": _iso(t6), "run_id": "sneaky", "item": "trust:%s" % c4,
                          "briefing": "SWEEP-BRIEFING.md",
                          "briefing_sha256": hashlib.sha256(open(os.path.join(r, "SWEEP-BRIEFING.md"), "rb").read()).hexdigest()})
        st = status(r, now=t6)
        case("round-3 fold: a stored ack whose run's `open` was UNATTENDED is re-validated OUT by "
             "status() -- the item reopens and it is a finding (write-time and read-time agree)",
             any("UNATTENDED" in f for f in st["findings"]) and "trust:%s" % c4 in [x["id"] for x in st["pending"]],
             st["findings"])
        _kill = os.path.join(r, ACKS.replace("/", os.sep))
        open(_kill, "w", encoding="utf-8", newline="\n").write("\n".join(
            l for l in [ln for ln in open(_kill, encoding="utf-8").read().splitlines()] if '"sneaky"' not in l) + "\n")
        # round-3 fold: a stored ack naming a briefing that no longer renders the item is caught
        heartbeat(r, "open", "content", now=t6)
        write("SWEEP-BRIEFING.md", "a briefing that shows nothing about the item\n")
        commit_path("SWEEP-BRIEFING.md", "a committed briefing that shows nothing")
        _append(r, ACKS, {"ts": _iso(t6), "run_id": "content", "item": "trust:%s" % c4,
                          "briefing": "SWEEP-BRIEFING.md",
                          "briefing_sha256": hashlib.sha256(open(os.path.join(r, "SWEEP-BRIEFING.md"), "rb").read()).hexdigest()})
        st = status(r, now=t6)
        case("round-3 fold: a stored ack whose (recoverable) briefing does not RENDER the item is "
             "re-validated OUT -- content is rechecked, not just the run id",
             any("does not render the item" in f for f in st["findings"])
             and "trust:%s" % c4 in [x["id"] for x in st["pending"]], st["findings"])
        open(_kill, "w", encoding="utf-8", newline="\n").write("\n".join(
            l for l in open(_kill, encoding="utf-8").read().splitlines() if '"content"' not in l) + "\n")
        # round-7 fold: an ack naming a briefing whose bytes cannot be recovered at the recorded
        # sha256 (a fabricated / wrong-hash briefing) is NOT trusted -- the item reopens, finding
        heartbeat(r, "open", "badhash", now=t6)
        _append(r, ACKS, {"ts": _iso(t6), "run_id": "badhash", "item": "trust:%s" % c4,
                          "briefing": "NO-SUCH-BRIEFING.md", "briefing_sha256": "0" * 64})
        st = status(r, now=t6)
        case("round-7 fold: an ack naming an UNRECOVERABLE briefing (wrong/missing bytes at the "
             "recorded sha) is a finding and does not acknowledge (the item reopens)",
             any("cannot be recovered" in f for f in st["findings"])
             and "trust:%s" % c4 in [x["id"] for x in st["pending"]], st["findings"])
        open(_kill, "w", encoding="utf-8", newline="\n").write("\n".join(
            l for l in open(_kill, encoding="utf-8").read().splitlines() if '"badhash"' not in l) + "\n")
        # round-9 fold: receipt ledgers are append-only vs their own git history -- deleting a
        # committed heartbeat row (e.g. to hide a failed cycle) is a finding
        git("add", "-A")
        git("commit", "-q", "-m", "commit the receipt ledgers")
        swp = os.path.join(r, SWEEPS.replace("/", os.sep))
        _all = open(swp, encoding="utf-8").read().splitlines()
        open(swp, "w", encoding="utf-8", newline="\n").write("\n".join(_all[:-1]) + "\n")
        st = status(r, now=t6)
        case("round-9 fold: deleting a committed receipt row (working tree) is a finding -- the "
             "ledger is append-only vs its git history",
             any("append-only extension" in f for f in st["findings"]), st["findings"])
        git("checkout", "--", SWEEPS)
        # ...and a COMMITTED deletion is caught by the history-prefix walk
        _all = open(swp, encoding="utf-8").read().splitlines()
        open(swp, "w", encoding="utf-8", newline="\n").write("\n".join(_all[:-1]) + "\n")
        git("add", "-A")
        git("commit", "-q", "-m", "suppress a heartbeat row (committed)")
        st = status(r, now=t6)
        case("round-9 fold: a COMMITTED receipt-row deletion is a finding (history-prefix walk)",
             any("rewritten in commit" in f for f in st["findings"]), st["findings"])
        # restore by appending the row back (append-only repair) and committing
        open(swp, "a", encoding="utf-8", newline="\n").write(_all[-1] + "\n")
        git("add", "-A")
        git("commit", "-q", "-m", "append the row back")
        # round-3 fold: a FAILED cycle followed by a later attended ok still alarms
        tF = t6 + datetime.timedelta(days=10)
        heartbeat(r, "open", "nf", now=tF); heartbeat(r, "failed", "nf", now=tF)
        write("SWEEP-BRIEFING.md", "operator reads\n" + "\n".join(
            "- %s %s" % (it["kind"], (it.get("commit") or it.get("date"))) for it in status(r, now=tF)["pending"]) + "\n")
        commit_path("SWEEP-BRIEFING.md", "briefing 4")
        heartbeat(r, "open", "okok", now=tF + datetime.timedelta(hours=1))
        ack(r, "okok", "SWEEP-BRIEFING.md", now=tF + datetime.timedelta(hours=1))
        heartbeat(r, "ok", "okok", now=tF + datetime.timedelta(hours=1))
        st, new = observe(r, now=tF + datetime.timedelta(hours=2))
        case("round-3 fold: a FAILED cycle followed by a later attended `ok` still raises a "
             "failed-cycle alarm (a failure is never excused by a later success)",
             any(n["kind"] == "failed-cycle" and n.get("cycle", "").startswith("nf") for n in new)
             and st["observation"]["failed_cycles"], new)
        # tamper direction 4: delete the retire record from the branch -> item persists (history)
        git("rm", "-q", "receipts/journal/1.json")
        c5 = commit("suppress the journal record")
        st = status(r, now=t6)
        case("deleting the retire record from the branch does not remove its item (reconstructed "
             "from history)",
             any(it["id"] == "retire:%s" % c3 for it in st["items"]))
        # malformed ledger line -> finding, never a crash
        _append(r, SWEEPS, {"ts": "x"})
        with open(os.path.join(r, SWEEPS.replace("/", os.sep)), "a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        st = status(r, now=t6)
        case("a malformed ledger line is a finding, never a silent skip or a crash",
             any("not JSON" in f for f in st["findings"]))
        # default window when unset
        write("project.yaml", "trust_surface_signing: visible\n")
        wd, note = window_days(r)
        case("observation_window_days absent -> default %d, note says so" % DEFAULT_WINDOW_DAYS,
             wd == DEFAULT_WINDOW_DAYS and "default" in note)
        # ---- v3.0-159: the machine appendix satisfies BOTH contracts ----
        st_apx = status(r, now=t6)
        apx = render_appendix(st_apx)
        case("appendix: one dashed line per pending item, every line dash-prefixed",
             apx and all(l.startswith("- ") for l in apx.splitlines())
             and len([l for l in apx.splitlines() if l != "- nothing pending"])
             == len(st_apx["pending"]), apx)
        case("appendix: every pending item is rendered_in the appendix text "
             "(commit-backed AND alarm kinds)",
             all(rendered_in(apx, it) for it in st_apx["pending"]), apx)
        case("appendix: no script filename ever appears (the render table's "
             "author column is exactly what it must not carry)",
             ".py" not in apx, apx)
        rall = render(status(r, now=t6))
        case("render: names the window line and the table header",
             "observation window" in rall and "kind" in rall and "commit" in rall)
        # ---- v3.0.52 (v3.0-151): a non-main instance resolves with NO branch argument
        r5 = os.path.join(base, "repo5")
        os.makedirs(r5)
        subprocess.run(["git", "-C", r5, "init", "-q", "-b", "dogfood/fork-v3"], capture_output=True)
        for cfg in (["user.email", "t@t"], ["user.name", "t"], ["commit.gpgsign", "false"]):
            subprocess.run(["git", "-C", r5, "config"] + cfg, capture_output=True)
        p5 = os.path.join(r5, "core", "security", "hooks")
        os.makedirs(p5, exist_ok=True)
        with open(os.path.join(p5, "egress-allowlist.txt"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# none\n")
        with open(os.path.join(r5, "project.yaml"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("trust_surface_signing: visible\n")
        subprocess.run(["git", "-C", r5, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", r5, "commit", "-q", "-m", "seed"], capture_output=True)
        st5 = status(r5)
        case("v3.0-151: status() with no branch on a `dogfood/fork-v3` checkout resolves and "
             "reconstructs (doctor 16(f)'s exact invocation shape passes on a non-main "
             "instance)", len(st5["pending"]) == 1
             and st5["pending"][0]["kind"] == "trust-surface", st5["pending"])
        subprocess.run(["git", "-C", r5, "checkout", "-q", "--detach"], capture_output=True)
        try:
            status(r5)
            case("v3.0-151: detached refused", False)
        except Refuse as e5:
            case("v3.0-151: a DETACHED head with no production_branch key REFUSES, never a "
                 "silent `main`", "unresolvable" in str(e5), e5)
        # ---- v3.0.54 (v3.0-163, fleet inbox #14): the first production instance's void
        # block, reproduced at its real size -- 82 items, 82 ack rows naming briefing bytes
        # edited before they were ever committed, the ok heartbeat refusing over them,
        # then the re-ack from a COMMITTED briefing that clears every item while the
        # append-only chain keeps every row.
        r6 = os.path.join(base, "repo6")
        os.makedirs(r6)

        def git6(*a):
            return subprocess.run(["git", "-C", r6] + list(a), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")

        def write6(rel, text):
            p6 = os.path.join(r6, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(p6), exist_ok=True)
            with open(p6, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)

        def commit6(paths, msg, when=None):
            git6("add", "--", *paths)
            env_when = {}
            if when is not None:  # a commit dated in the fixture's synthetic future
                env_when = {"GIT_AUTHOR_DATE": _iso(when), "GIT_COMMITTER_DATE": _iso(when)}
            p6 = subprocess.run(["git", "-C", r6, "commit", "-q", "-m", msg], capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                env=dict(os.environ, **env_when))
            assert p6.returncode == 0, p6.stderr

        git6("init", "-q", "-b", "main")
        for cfg in (["user.email", "t@t"], ["user.name", "t"], ["commit.gpgsign", "false"],
                    ["core.autocrlf", "false"]):
            git6("config", *cfg)
        write6("project.yaml", "trust_surface_signing: visible\nobservation_window_days: 7\n")
        commit6(["project.yaml"], "seed")
        for i in range(82):
            rel6 = "deploy/evidence/operator-grant-%02d.md" % i
            write6(rel6, "grant %d\n" % i)
            commit6([rel6], "grant %d" % i)
        tv = (_now() + datetime.timedelta(minutes=1)).replace(microsecond=0)
        st6 = status(r6, now=tv)
        case("void-block fixture: 82 trust-surface items pending", len(st6["pending"]) == 82,
             len(st6["pending"]))
        heartbeat(r6, "open", "run-A", now=tv)
        # the pre-v3.0.54 close shape: appendix into the briefing, rows written against the
        # WORKING-TREE bytes, then the briefing edited and committed (25f605e's shape)
        write6("SWEEP-BRIEFING.md", "**Watching:**\n" + render_appendix(st6) + "\n")
        void_sha = hashlib.sha256(open(os.path.join(r6, "SWEEP-BRIEFING.md"), "rb").read()).hexdigest()
        for it in st6["pending"]:
            _append(r6, ACKS, {"ts": _iso(tv), "run_id": "run-A", "item": it["id"],
                               "briefing": "SWEEP-BRIEFING.md", "briefing_sha256": void_sha})
        write6("SWEEP-BRIEFING.md", "**Watching:**\n- edited three minutes later\n"
               + render_appendix(st6) + "\n")
        commit6(["SWEEP-BRIEFING.md"], "sweep: first attended close (briefing edited after the ack)")
        st6 = status(r6, now=tv)
        case("void block: 82 ack rows name bytes no commit holds -> 82 items pending, 82 findings",
             len(st6["pending"]) == 82
             and sum("cannot be recovered" in f for f in st6["findings"]) == 82,
             (len(st6["pending"]), len(st6["findings"])))
        case("void block: the finding advises re-ack from a committed briefing, never row deletion",
             all("re-ack from a committed briefing" in f and "never delete the row" in f
                 for f in st6["findings"] if "cannot be recovered" in f))
        try:
            heartbeat(r6, "ok", "run-A", now=tv)
            case("void block: the attended ok heartbeat REFUSES over the void block (the "
                 "v3.0.53 'refuses forever' shape)", False)
        except Refuse as e6:
            case("void block: the attended ok heartbeat REFUSES over the void block (the "
                 "v3.0.53 'refuses forever' shape)", "82 item(s)" in str(e6), e6)
        heartbeat(r6, "failed", "run-A", now=tv)  # the honest close the instance took
        # direction 1: a fabricated ack against UNCOMMITTED bytes refuses
        tw = tv + datetime.timedelta(days=1)
        heartbeat(r6, "open", "run-B", now=tw)
        write6("SWEEP-BRIEFING.md", "**Watching:**\n- the re-ack\n" + render_appendix(st6) + "\n")
        try:
            ack(r6, "run-B", "SWEEP-BRIEFING.md", now=tw)
            case("v3.0-163: ack against a briefing edited since HEAD REFUSES 'commit the "
                 "briefing first, then ack'", False)
        except Refuse as e6:
            case("v3.0-163: ack against a briefing edited since HEAD REFUSES 'commit the "
                 "briefing first, then ack'",
                 "commit the briefing first, then ack" in str(e6)
                 and "differs from its committed version" in str(e6), e6)
        case("v3.0-163: the refused ack wrote no rows", len(_rows(r6, ACKS)) == 82)
        write6("UNTRACKED-BRIEFING.md", "**Watching:**\n" + render_appendix(st6) + "\n")
        try:
            ack(r6, "run-B", "UNTRACKED-BRIEFING.md", now=tw)
            case("v3.0-163: ack against an untracked briefing REFUSES", False)
        except Refuse as e6:
            case("v3.0-163: ack against an untracked briefing REFUSES",
                 "not committed at HEAD" in str(e6) and "commit the briefing first" in str(e6), e6)
        # direction 2: the valid re-ack from a COMMITTED briefing clears the block
        commit6(["SWEEP-BRIEFING.md"], "sweep: the re-ack briefing, committed FIRST")
        rows6 = ack(r6, "run-B", "SWEEP-BRIEFING.md", now=tw)
        st6 = status(r6, now=tw)
        case("v3.0-163: re-ack from a committed briefing acknowledges all 82 -> nothing pending, "
             "the void rows are no longer findings",
             len(rows6) == 82 and st6["pending"] == []
             and not any("cannot be recovered" in f for f in st6["findings"]),
             (len(rows6), len(st6["pending"]), st6["findings"][:2]))
        hb6 = heartbeat(r6, "ok", "run-B", now=tw)
        case("v3.0-163: the ok heartbeat is ACCEPTED after the re-ack", hb6["outcome"] == "ok")
        case("v3.0-163: append-only throughout -- 164 ack rows, the 82 void rows still present",
             len(_rows(r6, ACKS)) == 164
             and sum(r6r.get("briefing_sha256") == void_sha for r6r in _rows(r6, ACKS)) == 82)
        commit6([ACKS, SWEEPS], "commit the ledgers after the close")
        case("v3.0-163: the committed ledgers are a prefix chain (no append-only finding)",
             all(_append_only_defect(r6, rel6) is None for rel6 in (ACKS, SWEEPS, ALARMS)))
        st6 = status(r6, now=tw)
        case("v3.0-163: after committing the ledgers: nothing pending, no findings, within window "
             "(the doctor-green shape)",
             st6["pending"] == [] and st6["findings"] == [] and not st6["observation"]["missed"],
             st6["findings"][:3])
        crlf = open(os.path.join(r6, "SWEEP-BRIEFING.md"), "rb").read().replace(b"\n", b"\r\n")
        open(os.path.join(r6, "SWEEP-BRIEFING.md"), "wb").write(crlf)
        st6 = status(r6, now=tw)
        case("v3.0-163: a CRLF checkout of the committed briefing still validates every ack "
             "(line endings are convention, not content)",
             st6["pending"] == [] and st6["findings"] == [], st6["findings"][:2])
        heartbeat(r6, "open", "run-C", now=tw + datetime.timedelta(hours=1))
        _append(r6, ACKS, {"ts": _iso(tw + datetime.timedelta(hours=1)), "run_id": "run-C",
                           "item": st6["items"][0]["id"], "briefing": "SWEEP-BRIEFING.md",
                           "briefing_sha256": "0" * 64})
        st6 = status(r6, now=tw + datetime.timedelta(hours=1))
        case("v3.0-163: a void row NEWER than a valid ack is superseded history (counted in "
             "the render), the item stays acknowledged",
             st6["pending"] == [] and st6["superseded_ack_rows"] == 1
             and "superseded ack rows: 1" in render(st6),
             (st6["superseded_ack_rows"], st6["findings"][:2]))
        # cross-vendor round-1 folds (v3.0.54): (1) a MIXED-ending briefing (some CRLF lines,
        # some LF) is HEAD-identical by content, acks, and its ack still recovers from git
        # after the briefing rotates -- the recorded hash is over LF-normalized content;
        # (2) two rows in the same second order by ledger position, newest last
        git6("checkout", "-q", "--", "SWEEP-BRIEFING.md")
        write6("core/security/hooks/egress-allowlist.txt", "# widened\n")
        commit6(["core/security/hooks/egress-allowlist.txt"], "one more trust item")
        tx = tw + datetime.timedelta(days=1)
        heartbeat(r6, "open", "run-M", now=tx)
        st6 = status(r6, now=tx)
        mixed = ("**Watching:**\r\n- mixed endings\n" + render_appendix(st6) + "\n").encode("utf-8")
        with open(os.path.join(r6, "SWEEP-BRIEFING.md"), "wb") as fh:
            fh.write(mixed)
        commit6(["SWEEP-BRIEFING.md"], "a briefing with mixed line endings, committed")
        rows_m = ack(r6, "run-M", "SWEEP-BRIEFING.md", now=tx)
        heartbeat(r6, "ok", "run-M", now=tx)
        write6("SWEEP-BRIEFING.md", "**Watching:**\n- rotated: a later sweep's briefing\n")
        commit6(["SWEEP-BRIEFING.md"], "briefing rotated by a later sweep")
        st6 = status(r6, now=tx)
        case("round-1 fold: a mixed LF/CRLF briefing acks and its ack RECOVERS from git after "
             "the briefing rotates (content hash, not byte hash)",
             len(rows_m) == 1 and st6["pending"] == [] and st6["findings"] == [], st6["findings"][:2])
        # same-second rows: a void row then a valid row at ONE timestamp -- the later-appended
        # (valid) row must win, so nothing reopens and the void row reads as superseded
        write6("core/security/hooks/egress-allowlist.txt", "# widened twice\n")
        commit6(["core/security/hooks/egress-allowlist.txt"], "another trust item")
        ty = tx + datetime.timedelta(days=1)
        heartbeat(r6, "open", "run-S", now=ty)
        st6 = status(r6, now=ty)
        new_item = st6["pending"][0]["id"]
        _append(r6, ACKS, {"ts": _iso(ty), "run_id": "run-S", "item": new_item,
                           "briefing": "SWEEP-BRIEFING.md", "briefing_sha256": "0" * 64})
        write6("SWEEP-BRIEFING.md", "**Watching:**\n" + render_appendix(st6) + "\n")
        commit6(["SWEEP-BRIEFING.md"], "same-second briefing")
        ack(r6, "run-S", "SWEEP-BRIEFING.md", now=ty)
        st6 = status(r6, now=ty)
        case("round-1 fold: at one timestamp the LATER-appended valid row wins over an earlier "
             "void row (ledger position breaks the tie) -- item acknowledged, void row superseded",
             st6["pending"] == [] and st6["superseded_ack_rows"] >= 1
             and not any(new_item in f for f in st6["findings"]), (st6["findings"][:2], st6["superseded_ack_rows"]))
        # cross-vendor round-3 fold: a run opened BOTH attended and unattended (which ack()
        # refuses outright) must not validate a directly appended row at read time either --
        # read-time and write-time apply the same all-open-rows-attended rule
        write6("core/security/hooks/egress-allowlist.txt", "# widened thrice\n")
        commit6(["core/security/hooks/egress-allowlist.txt"], "a third trust item")
        tz = ty + datetime.timedelta(days=1)
        heartbeat(r6, "open", "run-X", now=tz)              # attended open
        os.environ[UNATTENDED_ENV] = "1"
        heartbeat(r6, "open", "run-X", now=tz)              # + an unattended open, same run id
        os.environ.pop(UNATTENDED_ENV, None)
        st6 = status(r6, now=tz)
        mixed_item = st6["pending"][0]["id"]
        write6("SWEEP-BRIEFING.md", "**Watching:**\n" + render_appendix(st6) + "\n")
        commit6(["SWEEP-BRIEFING.md"], "mixed-run briefing, committed")
        try:
            ack(r6, "run-X", "SWEEP-BRIEFING.md", now=tz)
            case("round-3 fold: ack() refuses a mixed attended/unattended run", False)
        except Refuse as e6:
            case("round-3 fold: ack() refuses a mixed attended/unattended run",
                 "UNATTENDED" in str(e6), e6)
        _append(r6, ACKS, {"ts": _iso(tz), "run_id": "run-X", "item": mixed_item,
                           "briefing": "SWEEP-BRIEFING.md",
                           "briefing_sha256": hashlib.sha256(
                               open(os.path.join(r6, "SWEEP-BRIEFING.md"), "rb").read()).hexdigest()})
        st6 = status(r6, now=tz)
        case("round-3 fold: a row appended DIRECTLY for a mixed run is re-validated OUT by "
             "status() -- the item stays pending, the row is a finding (read-time == write-time)",
             mixed_item in [x["id"] for x in st6["pending"]]
             and any("both attended and unattended" in f for f in st6["findings"]),
             st6["findings"][:2])
        # cross-vendor round-4 fold: a row appended DIRECTLY naming UNCOMMITTED bytes, with
        # exactly those bytes committed LATER, stays void -- the anchoring commit must
        # predate the row (and the working tree never validates a row on its own)
        write6("core/security/hooks/egress-allowlist.txt", "# widened four times\n")
        commit6(["core/security/hooks/egress-allowlist.txt"], "a fourth trust item")
        tl = tz + datetime.timedelta(days=1)
        heartbeat(r6, "open", "run-L", now=tl)
        st6 = status(r6, now=tl)
        late_item = st6["pending"][0]["id"]
        write6("SWEEP-BRIEFING.md", "**Watching:**\n" + render_appendix(st6) + "\n")
        late_sha = hashlib.sha256(open(os.path.join(r6, "SWEEP-BRIEFING.md"), "rb").read()).hexdigest()
        _append(r6, ACKS, {"ts": _iso(tl), "run_id": "run-L", "item": late_item,
                           "briefing": "SWEEP-BRIEFING.md", "briefing_sha256": late_sha})
        st6 = status(r6, now=tl)
        case("round-4 fold: a direct row naming UNCOMMITTED working-tree bytes does not "
             "acknowledge (the working tree never validates a row)",
             late_item in [x["id"] for x in st6["pending"]]
             and any("cannot be recovered from any commit" in f for f in st6["findings"]),
             st6["findings"][:2])
        commit6(["SWEEP-BRIEFING.md"], "the SAME bytes committed AFTER the row was written",
                when=tl + datetime.timedelta(hours=1))
        st6 = status(r6, now=tl + datetime.timedelta(hours=2))
        case("round-4 fold: committing those exact bytes LATER does not validate the row -- "
             "the ack predates its briefing's first commit (finding), item still pending",
             late_item in [x["id"] for x in st6["pending"]]
             and any("BEFORE its briefing" in f for f in st6["findings"]), st6["findings"][:2])
        # ...while the honest order (commit, THEN ack) on the same bytes acknowledges
        rows_l = ack(r6, "run-L", "SWEEP-BRIEFING.md", now=tl + datetime.timedelta(hours=2))
        st6 = status(r6, now=tl + datetime.timedelta(hours=2))
        case("round-4 fold: the honest order on the same committed bytes acknowledges; the "
             "earlier premature row is older than the winner (unexamined history), not a finding",
             len(rows_l) >= 1 and st6["pending"] == [] and st6["findings"] == [],
             (len(rows_l), st6["pending"], st6["findings"][:2]))
        # cross-vendor round-5 fold: a row naming an item NOT in history is a finding until an
        # attended sweep closes ok after it (the sweep that showed it); then it is history --
        # never a permanent red on an append-only ledger
        tu = tl + datetime.timedelta(days=1)
        _append(r6, ACKS, {"ts": _iso(tu), "run_id": "run-L", "item": "trust:%s" % ("e" * 40),
                           "briefing": "SWEEP-BRIEFING.md", "briefing_sha256": late_sha})
        st6 = status(r6, now=tu)
        case("round-5 fold: an unknown-item row is a finding while no later attended sweep has "
             "shown it", any("not in history" in f for f in st6["findings"]), st6["findings"][:2])
        # a later attended ok close that does NOT show it (round-6 fold) leaves the finding
        heartbeat(r6, "open", "run-U", now=tu + datetime.timedelta(hours=1))
        heartbeat(r6, "ok", "run-U", now=tu + datetime.timedelta(hours=1))  # nothing pending: ok lands
        st6 = status(r6, now=tu + datetime.timedelta(hours=2))
        case("round-6 fold: a later attended ok close whose briefing never showed the row does NOT "
             "retire the unknown-item finding", any("not in history" in f for f in st6["findings"]),
             st6["findings"][:2])
        # ...and a later attended close whose COMMITTED, acknowledged briefing carries the
        # item id (a details tail, as the sweep skill prescribes) retires it
        write6("core/security/hooks/egress-allowlist.txt", "# widened again\n")
        commit6(["core/security/hooks/egress-allowlist.txt"], "another trust item after the bogus row")
        tv2 = tu + datetime.timedelta(days=1)
        heartbeat(r6, "open", "run-V", now=tv2)
        st6 = status(r6, now=tv2)
        write6("SWEEP-BRIEFING.md", "**Needs your attention:**\n\n1. An acknowledgement exists that names "
               "no change in history. Nothing is acknowledged by it; the row stays as history "
               "once you have read this. (details: trust:%s)\n\n**Watching:**\n\n%s\n"
               % ("e" * 40, render_appendix(st6)))
        commit6(["SWEEP-BRIEFING.md"], "briefing that SHOWS the unknown-item finding")
        ack(r6, "run-V", "SWEEP-BRIEFING.md", now=tv2)
        heartbeat(r6, "ok", "run-V", now=tv2)
        st6 = status(r6, now=tv2 + datetime.timedelta(hours=1))
        case("round-6 fold: after an attended close whose committed briefing SHOWS the row (item id "
             "in a details tail) it is history (superseded), no finding, nothing pending -- the "
             "doctor goes green without deleting anything",
             st6["findings"] == [] and st6["pending"] == [] and st6["superseded_ack_rows"] >= 1
             and len(_rows(r6, ACKS)) >= 168, (st6["findings"][:2], st6["superseded_ack_rows"]))
        # cross-vendor round-7 fold: the PROOF row must itself be a valid ack written before
        # its run closed -- a forged proof row appended AFTER an ok close cannot retire a
        # second unknown-item row, and a plain ack row dated after its run's close is void
        tw2 = tv2 + datetime.timedelta(days=1)
        _append(r6, ACKS, {"ts": _iso(tw2), "run_id": "run-L", "item": "trust:%s" % ("d" * 40),
                           "briefing": "SWEEP-BRIEFING.md", "briefing_sha256": late_sha})
        write6("SWEEP-BRIEFING.md", "**Needs your attention:**\n\n1. Forged. (details: trust:%s)\n\n"
               "**Watching:**\n\n- nothing pending\n" % ("d" * 40))
        commit6(["SWEEP-BRIEFING.md"], "a briefing mentioning the second bogus id, committed",
                when=tw2 + datetime.timedelta(hours=1))
        forged_sha = hashlib.sha256(
            open(os.path.join(r6, "SWEEP-BRIEFING.md"), "rb").read().replace(b"\r\n", b"\n")).hexdigest()
        # run-V closed ok at tv2; this "proof" row names run-V and is dated after that close
        _append(r6, ACKS, {"ts": _iso(tw2 + datetime.timedelta(hours=2)), "run_id": "run-V",
                           "item": st6["items"][0]["id"], "briefing": "SWEEP-BRIEFING.md",
                           "briefing_sha256": forged_sha})
        st6 = status(r6, now=tw2 + datetime.timedelta(hours=3))
        case("round-7 fold: a forged proof row appended AFTER its run closed ok cannot retire an "
             "unknown-item finding (the proof must be a valid ack, dated before its run's close)",
             any("not in history" in f and "d" * 40 in f for f in st6["findings"]), st6["findings"][:3])
        case("round-7 fold: the forged proof row (a void row newer than the item's valid ack) reads "
             "as superseded history, not as an acknowledgement", st6["superseded_ack_rows"] >= 1)
        write6("core/security/hooks/egress-allowlist.txt", "# widened six times\n")
        commit6(["core/security/hooks/egress-allowlist.txt"], "a sixth trust item")
        st6 = status(r6, now=tw2 + datetime.timedelta(hours=3))
        sixth = st6["pending"][0]["id"]
        _append(r6, ACKS, {"ts": _iso(tw2 + datetime.timedelta(hours=4)), "run_id": "run-V",
                           "item": sixth, "briefing": "SWEEP-BRIEFING.md", "briefing_sha256": forged_sha})
        st6 = status(r6, now=tw2 + datetime.timedelta(hours=5))
        case("round-7 fold: an ack row dated AFTER its run closed is a finding and the item stays "
             "pending (an honest ack precedes its run's close)",
             sixth in [x["id"] for x in st6["pending"]]
             and any("AFTER its run" in f and sixth in f for f in st6["findings"]), st6["findings"][:3])
        # cross-vendor round-8 fold: a briefing whose HEAD commit is dated AFTER the ack clock
        # (skew) REFUSES at ack time -- never a row the ledger would later void
        tw3 = tw2 + datetime.timedelta(days=1)
        heartbeat(r6, "open", "run-W", now=tw3)
        st6 = status(r6, now=tw3)
        write6("SWEEP-BRIEFING.md", "**Watching:**\n" + render_appendix(st6) + "\n")
        commit6(["SWEEP-BRIEFING.md"], "briefing committed with a FUTURE committer date",
                when=tw3 + datetime.timedelta(days=2))
        try:
            ack(r6, "run-W", "SWEEP-BRIEFING.md", now=tw3)
            case("round-8 fold: a HEAD-identical briefing whose commit is dated after the ack clock "
                 "REFUSES at ack time, naming the skew", False)
        except Refuse as e6:
            case("round-8 fold: a HEAD-identical briefing whose commit is dated after the ack clock "
                 "REFUSES at ack time, naming the skew",
                 "AFTER this clock" in str(e6) and "clock skew" in str(e6), e6)
        case("round-8 fold: the refused ack wrote no rows",
             not any(r6r.get("run_id") == "run-W" for r6r in _rows(r6, ACKS)))
        # re-commit with a sane date: the same bytes now ack
        write6("SWEEP-BRIEFING.md", "**Watching:**\n" + render_appendix(st6) + "\n- re-committed\n")
        commit6(["SWEEP-BRIEFING.md"], "honest briefing for the sixth item", when=tw3)
        # cross-vendor round-9 fold: a row with an UNPARSABLE timestamp cannot dodge the
        # chronology checks -- it is void outright, even naming committed bytes
        _append(r6, ACKS, {"ts": "not-a-time", "run_id": "run-W", "item": sixth,
                           "briefing": "SWEEP-BRIEFING.md", "briefing_sha256": hashlib.sha256(
                               open(os.path.join(r6, "SWEEP-BRIEFING.md"), "rb").read()).hexdigest()})
        st6 = status(r6, now=tw3)
        case("round-9 fold: an ack row with an unparsable timestamp is void (finding), the item "
             "stays pending -- no chronology check can be skipped",
             sixth in [x["id"] for x in st6["pending"]]
             and any("no parseable timestamp" in f for f in st6["findings"]), st6["findings"][:3])
        ack(r6, "run-W", "SWEEP-BRIEFING.md", now=tw3)
        heartbeat(r6, "ok", "run-W", now=tw3)
        # stated boundary (round 5): the anchoring commit and the ack may share ONE second
        write6("core/security/hooks/egress-allowlist.txt", "# widened five times\n")
        commit6(["core/security/hooks/egress-allowlist.txt"], "a fifth trust item")
        tq = tu + datetime.timedelta(days=1)
        heartbeat(r6, "open", "run-Q", now=tq)
        st6 = status(r6, now=tq)
        write6("SWEEP-BRIEFING.md", "**Watching:**\n" + render_appendix(st6) + "\n")
        commit6(["SWEEP-BRIEFING.md"], "briefing committed in the same second as the ack", when=tq)
        rows_q = ack(r6, "run-Q", "SWEEP-BRIEFING.md", now=tq)
        st6 = status(r6, now=tq)
        case("stated boundary: a briefing committed in the SAME second as the ack acknowledges "
             "(one-second clocks; an honest fast close must not refuse)",
             len(rows_q) >= 1 and st6["pending"] == []
             and [f for f in st6["findings"] if "d" * 40 not in f] == [],  # the unretired bogus row stays a finding by design
             st6["findings"][:2])
    finally:
        if saved_env is None:
            os.environ.pop(UNATTENDED_ENV, None)
        else:
            os.environ[UNATTENDED_ENV] = saved_env
        shutil.rmtree(base, ignore_errors=True)
    print("pending.py self-test: %s (%d/%d)" % ("FAIL" if failed else "PASS", total - failed, total))
    return 1 if failed else 0


# ------------------------------------------------------------------ CLI
def main(argv=None):
    ap = argparse.ArgumentParser(prog="pending.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root", default=".")
    ap.add_argument("--branch", default=None,
                    help="production branch (default: project.yaml production_branch, "
                         "else the checked-out branch -- v3.0-151)")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--appendix", action="store_true",
                    help="print the briefing's dashed machine appendix (one bare-id "
                         "line per pending item; v3.0-159)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--heartbeat", choices=["open", "ok", "failed"])
    ap.add_argument("--run-id")
    ap.add_argument("--ack", action="store_true")
    ap.add_argument("--briefing")
    ap.add_argument("--observe", action="store_true")
    ap.add_argument("--observer", default="standing-loop")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    repo = os.path.abspath(a.root)
    if not _trust.is_git_repo(repo):
        print("REFUSED: %s is not a git repository (the pending list is git state)" % repo)
        return 2
    try:
        if a.heartbeat:
            if not a.run_id:
                print("REFUSED: --heartbeat needs --run-id")
                return 2
            row = heartbeat(repo, a.heartbeat, a.run_id)
            print(json.dumps(row))
            return 0
        if a.ack:
            if not a.run_id or not a.briefing:
                print("REFUSED: --ack needs --run-id and --briefing")
                return 2
            rows = ack(repo, a.run_id, a.briefing, a.branch)
            print("acknowledged %d item(s) as shown in %s (run %s)" % (len(rows), a.briefing, a.run_id))
            return 0
        if a.observe:
            st, new = observe(repo, a.observer, a.branch)
            for n in new:
                print("ALARM written: %s -- %s" % (n["kind"], n["detail"]))
            print(render(st))
            return 2 if (new or st["findings"]) else 0
        st = status(repo, a.branch)
        if a.appendix:
            print(render_appendix(st))
            return 2 if st["findings"] else 0
        if a.json:
            print(json.dumps(st, indent=1, default=str))
        else:
            print(render(st))
        return 2 if st["findings"] else 0
    except Refuse as e:
        print("REFUSED: %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
