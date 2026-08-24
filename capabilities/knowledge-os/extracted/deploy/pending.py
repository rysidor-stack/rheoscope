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
DURABLE at the commit the sweep itself makes (step 17 commits the receipt rows with the
briefing): a row appended and deleted before that commit was never durable, exactly as an
uncommitted file anywhere is not -- the append-only check binds every committed version
and the working tree's extension of the newest one.

Usage:
  pending.py --root R --render [--json] [--branch main]     the table (sweep step 17)
  pending.py --root R --heartbeat open|ok|failed --run-id ID
  pending.py --root R --ack --run-id ID --briefing PATH     attended sweeps only
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


def reconstruct(repo, branch="main"):
    """Every item on the branch's first-parent chain, tip-most first. Items:
    retire:<commit>  a commit that introduced/modified a retire record (trust.py's walk)
    trust:<commit>   a commit touching any trust-surface class path (hook-lane untracked
                     members excepted -- they are never in history)"""
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


def status(repo, branch="main", now=None):
    now = now or _now()
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
    acked = {}
    for a in acks:
        if "_malformed" in a:
            continue
        it = ids.get(a.get("item"))
        if it is None:
            findings.append("ack for an item that is not in history: %s (run %s at %s) -- a forged "
                            "ack, or history was rewritten" % (a.get("item"), a.get("run_id"), a.get("ts")))
            continue
        ad, idt = _parse(a.get("ts")), _parse(it.get("date") or "")
        if ad and idt and ad < idt:
            findings.append("ack for %s is dated %s, BEFORE the item's commit (%s)" % (
                a.get("item"), a.get("ts"), it.get("date")))
            continue
        if not a.get("run_id") or not a.get("briefing"):
            findings.append("ack for %s names no run_id/briefing" % a.get("item"))
            continue
        acked.setdefault(a["item"], a)
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
    # every STORED ack is re-validated the way ack() validates at write time (cross-vendor
    # round-3 fold): the run's `open` heartbeat must exist AND be attended, and the briefing
    # named must STILL render the item -- a directly-appended ledger row that skipped ack()
    # is caught here, its item reopens, and the row is a finding.
    attended_opens = {r.get("run_id") for r in sweeps if r.get("kind") == "open" and r.get("attended")}
    any_opens = {r.get("run_id") for r in sweeps if r.get("kind") == "open"}
    for item_id, a in list(acked.items()):
        rid = a.get("run_id")
        if rid not in any_opens:
            findings.append("ack for %s names run %s which has no sweep heartbeat -- not written "
                            "by a sweep" % (item_id, rid))
            del acked[item_id]
            continue
        if rid not in attended_opens:
            findings.append("ack for %s names run %s whose sweep was UNATTENDED -- an unattended "
                            "sweep renders but never acknowledges" % (item_id, rid))
            del acked[item_id]
            continue
        it = ids.get(item_id)
        btext = _briefing_text(repo, a.get("briefing"), a.get("briefing_sha256"))
        # the briefing the ack names MUST be recoverable by its stored sha256 (cross-vendor
        # round-7 fold): a legitimate sweep's briefing is committed and therefore recoverable,
        # so an ack naming bytes that cannot be found at that sha -- a fabricated or wrong-hash
        # briefing -- is not trusted; the item reopens (the safe direction) and it is a finding.
        if btext is None:
            findings.append("ack for %s names briefing %s whose bytes cannot be recovered at the "
                            "recorded sha256 -- an acknowledgement must show the item in a briefing "
                            "that exists" % (item_id, a.get("briefing")))
            del acked[item_id]
            continue
        if it is not None and not rendered_in(btext, it):
            findings.append("ack for %s names briefing %s, but that briefing does not render the "
                            "item -- the acknowledgement did not show it" % (item_id, a.get("briefing")))
            del acked[item_id]
    pending = [it for it in items if it["id"] not in acked]
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
            "findings": findings, "observation": obs, "now": _iso(now)}


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
    _append(repo, SWEEPS, row)
    return row


def ack(repo, run_id, briefing, branch="main", now=None):
    """Attended sweeps ONLY. Acknowledges every currently pending item (the sweep rendered
    them into `briefing`)."""
    if not attended():
        raise Refuse("this is an UNATTENDED run (%s set): it renders the pending list but never "
                     "acknowledges it -- only a sweep the operator reads does" % UNATTENDED_ENV)
    bp = os.path.join(repo, briefing.replace("/", os.sep))
    try:
        btext = open(bp, "rb").read()
    except OSError:
        raise Refuse("briefing %s is not readable -- nothing was shown, nothing is acknowledged" % briefing)
    bsha = hashlib.sha256(btext).hexdigest()
    btext = btext.decode("utf-8", "replace")
    opens = [r for r in _rows(repo, SWEEPS) if r.get("kind") == "open" and r.get("run_id") == run_id]
    if not opens:
        raise Refuse("run %s has no `open` heartbeat -- an acknowledgement rides a sweep that "
                     "ran (`--heartbeat open --run-id %s` first); nothing is acknowledged" % (run_id, run_id))
    if not all(r.get("attended") for r in opens):
        raise Refuse("run %s was opened UNATTENDED -- it cannot acknowledge" % run_id)
    st = status(repo, branch, now)
    ts = _iso(now or _now())
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
                     "acknowledgement covers only what the briefing shows%s" % (
                         len(not_shown), briefing, ", ".join(not_shown)[:300],
                         " (%d item(s) that were shown are acknowledged)" % len(rows) if rows else ""))
    return rows


def _briefing_text(repo, rel, want_sha):
    """The bytes of the briefing an ack names, IF they still hash to the recorded sha256 --
    from the working tree or from any commit that carried that path. None when the exact
    bytes cannot be recovered (the ack is then gated on attended+heartbeat only; the content
    check is best-effort, never a false accusation on a legitimately rotated briefing)."""
    if not rel:
        return None
    try:
        raw = open(os.path.join(repo, rel.replace("/", os.sep)), "rb").read()
        if hashlib.sha256(raw).hexdigest() == want_sha:
            return raw.decode("utf-8", "replace")
    except OSError:
        pass
    rc, out, _ = _git(repo, "log", "--all", "--format=%H", "--", rel)
    for sha in out.split():
        b = _blob_at(repo, sha, rel)
        if b is not None and hashlib.sha256(b).hexdigest() == want_sha:
            return b.decode("utf-8", "replace")
    return None


def rendered_in(briefing_text, item):
    """The briefing SHOWS the item: its commit (first 12 hex) for retirements and
    trust-surface changes, its timestamp for alarms."""
    if item.get("kind") == "alarm":
        return bool(item.get("date")) and item["date"] in briefing_text
    c = item.get("commit") or ""
    return len(c) >= 12 and c[:12] in briefing_text


def observe(repo, observer="standing-loop", branch="main", now=None):
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
            detail = "seq %s view %s digest %s -- %s%s" % (
                it.get("seq"), it.get("view"), (it.get("proposal_digest") or "?")[7:19],
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
        try:
            ack(r, "run-1", "SWEEP-BRIEFING.md", now=t0)
            case("ack with a briefing that renders nothing refused", False)
        except Refuse as e:
            case("round-1 fold: a briefing that does not SHOW the items acknowledges nothing (all 4 stay "
                 "pending)", "NOT rendered" in str(e) and len(status(r, now=t0)["pending"]) == 4, e)
        st0 = status(r, now=t0)
        write("SWEEP-BRIEFING.md", "briefing with the items\n" + "\n".join(
            "- %s %s" % (it["kind"], it["commit"][:12]) for it in st0["pending"]) + "\n")
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
        heartbeat(r, "ok", "fake-1", now=t30)  # a paired attended ok with NO acks for the old item
        st = status(r, now=t30)
        case("round-1 fold: an item outstanding longer than the window is MISSED even when a paired "
             "attended `ok` cycle was just recorded (missed is judged on the items)",
             st["observation"]["missed"] and st["observation"]["overdue_items"] == ["trust:%s" % c4]
             and [x["id"] for x in st["pending"]] == ["trust:%s" % c4],
             st["observation"])
        # tamper direction 2: a forged ack for a commit not in history -> finding
        _append(r, ACKS, {"ts": _iso(t6), "run_id": "run-x", "item": "trust:%s" % ("f" * 40),
                          "briefing": "SWEEP-BRIEFING.md", "briefing_sha256": "0" * 64})
        st = status(r, now=t6)
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
        rall = render(status(r, now=t6))
        case("render: names the window line and the table header",
             "observation window" in rall and "kind" in rall and "commit" in rall)
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
    ap.add_argument("--branch", default="main")
    ap.add_argument("--render", action="store_true")
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
