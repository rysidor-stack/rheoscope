#!/usr/bin/env python3
"""desk-metrics.py -- the desk's metrics-history recorder (session D, v3.0-39, desk enrichment).

session-d-design-brief-2026-07-23.md Part 2 item 1: trend granularity is the WRITE-SIDE
cadence, never "per sweep run" -- a manual interactive /sweep is read-only and appends
nothing. `--append` reads the CURRENT sweep-visible state (the cheap sensors' own --json
output where available, plus a couple of counts this script derives cheaply itself) and
appends exactly ONE JSON line to `receipts/desk/metrics-history.jsonl` (creating the
directory the first time). `--trends` reads that history back and reports plain-English
trend lines ("N rising for K days", "N falling", ...) so a briefing or DESK.html can quote
them without re-deriving anything.

NEVER INVOKED BY /sweep ITSELF. /sweep is read-only by iron rule (.claude/skills/sweep/
SKILL.md's "Not a fixer" -- "every check it runs is read-only"); appending a history line
is a WRITE, so it belongs exclusively to whichever session already writes SWEEP-BRIEFING.md
on a schedule -- the /standing-loop payload and the scheduled-recipe session (see that
SKILL.md's "Scheduling" section, amended alongside this script to name the append + the
DESK.html regeneration explicitly). A direct, manual /sweep run never shells out to this
script; nothing above imports it either. Same doctrine as the inbox-regeneration split
(decision-inbox.py's --check vs. its write path).

WHAT --append COLLECTS (each source degrades independently -- one absent source never
blocks the others, and a missing source is recorded as `"present": false` with a note,
never silently dropped):
  - deadlines : `deploy/check-deadlines.py --json` (the deadline-and-trigger register),
    if the script exists here. Its own `degraded` flag (absent register) maps to
    `"present": false` too.
  - parity    : `deploy/check-parity.py --json` (fork/template mirror drift), if the
    script exists. A `{"note": ...}` reply (no template_path set, or the template root
    doesn't exist) maps to `"present": false` -- exactly check-parity's own degrade shape.
  - briefing  : the "Needs your attention" numbered-item count parsed straight out of
    SWEEP-BRIEFING.md, if present -- the one cheap count the design brief names by name.
    A small self-contained regex, deliberately NOT importing deploy/decision-inbox.py
    (a different builder's file; this script only ever reads the briefing text itself,
    the same read decision-inbox.py already performs independently).
  - spend     : two independent, best-effort figures (design brief Part 2 item 5, "v1 =
    what is locally measurable and already recorded... no new telemetry"):
      * .claude/sweep-schedule.log run counts + durations in the trailing window, parsed
        from its "===== sweep run ... =====" / "===== sweep done ... (exit N) ====="
        marker pairs (tolerant of an absent file, and of a trailing unfinished run --
        the same "a run in progress is not a crash" doctrine /sweep's own SKILL.md
        documents for reading this same log).
      * summed `token_cost` frontmatter (RECEIPT_KEYS optional field, check-frontmatter.py)
        across `receipts/*-compile.md` files whose filename-embedded date falls in the
        trailing window. `token_cost` is commonly written as an approximate string
        (`~180000`, see any recent receipts/*-compile.md) -- non-digit characters are
        stripped before parsing; a receipt whose value still won't parse as a number is
        counted and skipped, never crashes the scan.

Hermetic --self-test: builds a temp history file, a fixture SWEEP-BRIEFING.md, a fixture
.claude/sweep-schedule.log, and fixture compile receipts under a temp root, and exercises
every collection path (present, absent, malformed) plus every --trends direction
(rising / falling / steady / not-enough-history). Report-only doctrine (spec sec.12,
matching every deploy/check-*.py sensor here): a completed --append or --trends always
exits 0; only --self-test can fail.

Usage:
  desk-metrics.py --append [--root PATH] [--history PATH] [--today YYYY-MM-DD]
                   [--deploy-dir PATH] [--briefing PATH] [--schedule-log PATH]
                   [--receipts-dir PATH] [--register PATH] [--fork-root PATH]
                   [--template-root PATH] [--window-days N] [--json]
  desk-metrics.py --trends [--history PATH] [--json]
  desk-metrics.py --self-test
Exit: 0 on any completed --append/--trends (report-only/append-only-its-own-file
  primitive, never a gate) | nonzero only from --self-test on failure.
"""

import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_WINDOW_DAYS = 7
ATTENTION_HEADING_RE = re.compile(
    r"^[#>\s]*\**\s*needs your attention\s*:?\s*\**\s*$", re.IGNORECASE)
BOLD_HEADING_RE = re.compile(r"^\s*\*\*[^*]+\*\*\s*:?\s*$")
MD_HEADING_RE = re.compile(r"^\s*#")
NUMBERED_RE = re.compile(r"^\s*\d+\.\s+(.*)$")

RUN_LINE_RE = re.compile(
    r"^=====\s*sweep run\s+\S+\s+(\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})\.\d+\s*=====")
DONE_LINE_RE = re.compile(
    r"^=====\s*sweep done\s+\S+\s+(\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})\.\d+\s*"
    r"\(exit (\d+)\)\s*=====")

COMPILE_RECEIPT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T\d{6}-compile\.md$")
TOKEN_COST_LINE_RE = re.compile(r"^token_cost:\s*(.+?)\s*$")
NON_DIGIT_RE = re.compile(r"[^0-9]")


# --------------------------------------------------------------------------- subprocess sensors

def _run_json_script(script_path, extra_args):
    """Invoke `script_path --json <extra_args>` with this same interpreter (sys.executable
    -- the process already running this script is a working Python, so re-using it avoids
    any PATH-based py/python/python3 search). Returns (parsed_json_or_None, note_str).
    Never raises: a missing script, a timeout, a nonzero exit, or unparseable stdout all
    collapse to (None, <a plain-English note>) -- degrade, never crash (spec sec.12)."""
    if not os.path.isfile(script_path):
        return None, "%s not found" % os.path.basename(script_path)
    cmd = [sys.executable, script_path, "--json"] + list(extra_args)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:  # noqa: BLE001 -- any subprocess failure degrades, never crashes
        return None, "%s failed to run: %s" % (os.path.basename(script_path), e)
    out = (proc.stdout or "").strip()
    if not out:
        return None, "%s produced no output (exit %s)" % (os.path.basename(script_path), proc.returncode)
    try:
        return json.loads(out), None
    except ValueError:
        return None, "%s output was not valid JSON" % os.path.basename(script_path)


def collect_deadlines(deploy_dir, register_path=None, today=None):
    script = os.path.join(deploy_dir, "check-deadlines.py")
    extra = []
    if register_path:
        extra += ["--register", register_path]
    if today:
        extra += ["--today", today]
    data, note = _run_json_script(script, extra)
    if data is None:
        return {"present": False, "note": note}
    if data.get("degraded"):
        return {"present": False, "note": data.get("note", "deadline register absent")}
    counts = data.get("counts", {}) or {}
    return {
        "present": True,
        "total": counts.get("total"),
        "attention": counts.get("attention"),
        "fired": counts.get("fired"),
        "watching": counts.get("watching"),
        "if_row": counts.get("if_row"),
        "recorded_fired": counts.get("recorded_fired"),
        "recorded_discharged": counts.get("recorded_discharged"),
        "schema_findings": counts.get("schema_findings"),
    }


def collect_parity(deploy_dir, fork_root=None, template_root=None):
    script = os.path.join(deploy_dir, "check-parity.py")
    extra = []
    if fork_root:
        extra += ["--fork-root", fork_root]
    if template_root:
        extra += ["--template-root", template_root]
    data, note = _run_json_script(script, extra)
    if data is None:
        return {"present": False, "note": note}
    if "note" in data and "deploy" not in data:
        return {"present": False, "note": data["note"]}
    deploy_r = data.get("deploy", {}) or {}
    skills_r = data.get("skills", {}) or {}
    return {
        "present": True,
        "differs": len(deploy_r.get("differs", [])) + len(skills_r.get("differs", [])),
        "missing_in_template": (len(deploy_r.get("missing_in_template", []))
                                 + len(skills_r.get("missing_in_template", []))),
        "missing_in_fork": (len(deploy_r.get("missing_in_fork", []))
                             + len(skills_r.get("missing_in_fork", []))),
    }


# --------------------------------------------------------------------------- cheap derived counts

def count_attention_items(text):
    """Count numbered items under a '**Needs your attention:**' heading in briefing text.
    Deliberately independent of deploy/decision-inbox.py's own (near-identical) parser --
    that file belongs to a different builder in this session; this is a read-only count
    over the same public artifact, not a dependency on that script's internals."""
    in_section = False
    count = 0
    for raw in text.splitlines():
        stripped = raw.strip()
        if not in_section:
            if ATTENTION_HEADING_RE.match(stripped):
                in_section = True
            continue
        if BOLD_HEADING_RE.match(raw) or MD_HEADING_RE.match(raw):
            break
        if NUMBERED_RE.match(raw):
            count += 1
    return count


def collect_briefing(briefing_path):
    if not os.path.isfile(briefing_path):
        return {"present": False, "note": "SWEEP-BRIEFING.md not found"}
    try:
        with open(briefing_path, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        return {"present": False, "note": "SWEEP-BRIEFING.md unreadable: %s" % e}
    return {"present": True, "attention_items": count_attention_items(text)}


# --------------------------------------------------------------------------- spend: schedule log

def parse_schedule_log(text):
    """Return a list of {'start': datetime, 'end': datetime|None, 'exit_code': int|None}
    entries, sequential-pairing (single scheduled run at a time; a 'done' line closes the
    most recently opened 'run'). A trailing unfinished 'run' with no matching 'done' is
    still returned (end=None) -- the same in-flight-is-not-a-crash reading /sweep's own
    SKILL.md documents for this log, never dropped."""
    entries = []
    pending = None
    for raw in text.splitlines():
        line = raw.strip()
        m = RUN_LINE_RE.match(line)
        if m:
            if pending is not None:
                entries.append(pending)  # a run with no 'done' before the next 'run' opened
            mm, dd, yyyy, hh, mi, ss = m.groups()
            try:
                start = datetime(int(yyyy), int(mm), int(dd), int(hh), int(mi), int(ss))
            except ValueError:
                pending = None
                continue
            pending = {"start": start, "end": None, "exit_code": None}
            continue
        m = DONE_LINE_RE.match(line)
        if m and pending is not None:
            mm, dd, yyyy, hh, mi, ss, code = m.groups()
            try:
                end = datetime(int(yyyy), int(mm), int(dd), int(hh), int(mi), int(ss))
            except ValueError:
                continue
            pending["end"] = end
            pending["exit_code"] = int(code)
            entries.append(pending)
            pending = None
    if pending is not None:
        entries.append(pending)
    return entries


def collect_schedule_log(path, today, window_days=DEFAULT_WINDOW_DAYS):
    if not os.path.isfile(path):
        return {"present": False, "note": ".claude/sweep-schedule.log not found"}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        return {"present": False, "note": "sweep-schedule.log unreadable: %s" % e}
    entries = parse_schedule_log(text)
    window_start = today - timedelta(days=window_days - 1)
    in_window = [e for e in entries if window_start <= e["start"].date() <= today]
    completed = [e for e in in_window if e["end"] is not None]
    unfinished = len(in_window) - len(completed)
    durations = [(e["end"] - e["start"]).total_seconds() / 60.0 for e in completed]
    avg = round(sum(durations) / len(durations), 1) if durations else None
    return {
        "present": True,
        "window_days": window_days,
        "runs_in_window": len(in_window),
        "completed_in_window": len(completed),
        "unfinished_in_window": unfinished,
        "avg_duration_minutes": avg,
    }


# --------------------------------------------------------------------------- spend: compile receipts

def collect_compile_spend(receipts_dir, today, window_days=DEFAULT_WINDOW_DAYS):
    if not os.path.isdir(receipts_dir):
        return {"present": False, "note": "receipts/ not found"}
    window_start = today - timedelta(days=window_days - 1)
    files_in_window = 0
    parsed = 0
    malformed = 0
    total = 0
    for name in sorted(os.listdir(receipts_dir)):
        m = COMPILE_RECEIPT_RE.match(name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (window_start <= d <= today):
            continue
        files_in_window += 1
        path = os.path.join(receipts_dir, name)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            malformed += 1
            continue
        value = None
        if lines and lines[0].strip() == "---":
            for ln in lines[1:]:
                if ln.strip() == "---":
                    break  # end of frontmatter reached, no token_cost seen
                tm = TOKEN_COST_LINE_RE.match(ln)
                if tm:
                    value = tm.group(1)
                    break
        if value is None:
            malformed += 1
            continue
        digits = NON_DIGIT_RE.sub("", value)
        if not digits:
            malformed += 1
            continue
        try:
            total += int(digits)
            parsed += 1
        except ValueError:
            malformed += 1
    return {
        "present": True,
        "window_days": window_days,
        "files_in_window": files_in_window,
        "token_cost_files_parsed": parsed,
        "token_cost_files_malformed_or_missing": malformed,
        "token_cost_sum": total,
    }


# --------------------------------------------------------------------------- one metrics entry

def build_metrics(root, today=None, deploy_dir=None, briefing_path=None,
                   schedule_log_path=None, receipts_dir=None, register_path=None,
                   fork_root=None, template_root=None, window_days=DEFAULT_WINDOW_DAYS):
    today = today or date.today()
    deploy_dir = deploy_dir or os.path.join(root, "deploy")
    briefing_path = briefing_path or os.path.join(root, "SWEEP-BRIEFING.md")
    schedule_log_path = schedule_log_path or os.path.join(root, ".claude", "sweep-schedule.log")
    receipts_dir = receipts_dir or os.path.join(root, "receipts")

    deadlines = collect_deadlines(deploy_dir, register_path=register_path,
                                   today=today.isoformat())
    parity = collect_parity(deploy_dir, fork_root=fork_root or root, template_root=template_root)
    briefing = collect_briefing(briefing_path)
    schedule = collect_schedule_log(schedule_log_path, today, window_days=window_days)
    compile_spend = collect_compile_spend(receipts_dir, today, window_days=window_days)

    return {
        "date": today.isoformat(),
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "deadlines": deadlines,
        "parity": parity,
        "briefing": briefing,
        "spend": {"schedule_log": schedule, "compile_receipts": compile_spend},
    }


def append_entry(history_path, entry):
    d = os.path.dirname(history_path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(history_path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, sort_keys=True))
        fh.write("\n")


# --------------------------------------------------------------------------- --trends

TREND_FIELDS = (
    (("deadlines", "attention"), "deadline items needing attention"),
    (("deadlines", "fired"), "overdue deadlines"),
    (("parity", "differs"), "fork/template mirror-drift files"),
    (("briefing", "attention_items"), "open attention items in the briefing"),
)


def load_history(history_path):
    """Returns (entries, notes). Malformed lines are skipped and noted, never crash a read."""
    entries, notes = [], []
    if not os.path.isfile(history_path):
        notes.append("no metrics history at %s yet" % history_path)
        return entries, notes
    try:
        with open(history_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError as e:
        notes.append("metrics-history.jsonl unreadable: %s" % e)
        return entries, notes
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        try:
            entries.append(json.loads(ln))
        except ValueError:
            notes.append("line %d is not valid JSON, skipped" % (i + 1))
    return entries, notes


def dedupe_by_date(entries):
    """One entry per calendar date -- the LATEST recorded entry for that date wins (a
    manual extra --append on an already-recorded day supersedes the earlier one), and
    dates keep their first-seen chronological order (append-only file => already sorted)."""
    order = []
    by_date = {}
    for e in entries:
        d = e.get("date")
        if d is None:
            continue
        if d not in by_date:
            order.append(d)
        by_date[d] = e
    return [by_date[d] for d in order]


def _get_path(entry, path):
    node = entry
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if isinstance(node, dict):  # a {"present": false, ...} block -- no numeric value
        return None
    return node


def _sign(n):
    return (n > 0) - (n < 0)


def compute_trend_line(values, label):
    """values: chronological (oldest-first) list of (date, number) pairs with numbers
    already present (nulls filtered by the caller). Returns a plain-English line, or None
    if there isn't enough history to say anything."""
    if len(values) < 2:
        return None
    nums = [v for _, v in values]
    diffs = [nums[i] - nums[i - 1] for i in range(1, len(nums))]
    last_sign = _sign(diffs[-1])
    if last_sign == 0:
        return "%s: steady at %s across %d recorded day(s)" % (label, nums[-1], len(nums))
    run = 0
    i = len(diffs) - 1
    while i >= 0 and _sign(diffs[i]) == last_sign:
        run += 1
        i -= 1
    days = run + 1  # data points spanned by the run
    direction = "rising" if last_sign > 0 else "falling"
    return ("%s: %s for %d day(s) (now %s, was %s)"
            % (label, direction, days, nums[-1], nums[-1 - run]))


def compute_trends(entries):
    """entries: raw history entries (any order/dupes). Returns (lines, note)."""
    deduped = dedupe_by_date(entries)
    if len(deduped) < 2:
        return [], ("not enough history yet to show a trend (%d recorded day(s); need at "
                     "least 2 write-side --append runs)" % len(deduped))
    lines = []
    for path, label in TREND_FIELDS:
        series = []
        for e in deduped:
            v = _get_path(e, path)
            if isinstance(v, (int, float)):
                series.append((e.get("date"), v))
        line = compute_trend_line(series, label)
        if line:
            lines.append(line)
    if not lines:
        return [], "history is present but no field has two or more numeric readings yet"
    return lines, None


# --------------------------------------------------------------------------- CLI plumbing

def _arg_value(args, flag):
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return None


def _default_history_path(root):
    return os.path.join(root, "receipts", "desk", "metrics-history.jsonl")


def cmd_append(args):
    root = _arg_value(args, "--root") or "."
    history_path = _arg_value(args, "--history") or _default_history_path(root)
    today_s = _arg_value(args, "--today")
    today = None
    if today_s:
        try:
            today = datetime.strptime(today_s, "%Y-%m-%d").date()
        except ValueError:
            print("desk-metrics: NOTE -- unparseable --today value %r, using wall-clock date"
                  % today_s)
    window_days = DEFAULT_WINDOW_DAYS
    wd = _arg_value(args, "--window-days")
    if wd:
        try:
            window_days = int(wd)
        except ValueError:
            pass

    entry = build_metrics(
        root, today=today,
        deploy_dir=_arg_value(args, "--deploy-dir"),
        briefing_path=_arg_value(args, "--briefing"),
        schedule_log_path=_arg_value(args, "--schedule-log"),
        receipts_dir=_arg_value(args, "--receipts-dir"),
        register_path=_arg_value(args, "--register"),
        fork_root=_arg_value(args, "--fork-root"),
        template_root=_arg_value(args, "--template-root"),
        window_days=window_days,
    )
    append_entry(history_path, entry)

    if "--json" in args:
        print(json.dumps(entry, indent=2, sort_keys=True))
    else:
        print("desk-metrics: appended %s to %s" % (entry["date"], history_path))
        print("  deadlines: %s" % ("present" if entry["deadlines"]["present"]
                                    else "absent (%s)" % entry["deadlines"].get("note")))
        print("  parity:    %s" % ("present" if entry["parity"]["present"]
                                    else "absent (%s)" % entry["parity"].get("note")))
        print("  briefing:  %s" % ("present" if entry["briefing"]["present"]
                                    else "absent (%s)" % entry["briefing"].get("note")))
        sl = entry["spend"]["schedule_log"]
        cr = entry["spend"]["compile_receipts"]
        print("  spend:     schedule-log %s / compile-receipts %s"
              % ("present" if sl["present"] else "absent (%s)" % sl.get("note"),
                 "present" if cr["present"] else "absent (%s)" % cr.get("note")))
    return 0


def cmd_trends(args):
    root = _arg_value(args, "--root") or "."
    history_path = _arg_value(args, "--history") or _default_history_path(root)
    entries, notes = load_history(history_path)
    lines, note = compute_trends(entries)

    if "--json" in args:
        print(json.dumps({"history_path": history_path, "trends": lines,
                           "note": note, "load_notes": notes}, indent=2))
        return 0

    print("desk-metrics trends: %s" % history_path)
    for n in notes:
        print("  NOTE: %s" % n)
    if lines:
        for l in lines:
            print("  - %s" % l)
    else:
        print("  %s" % (note or "nothing to report"))
    return 0


# --------------------------------------------------------------------------- self-test

def self_test():
    import shutil
    import tempfile

    total = failed = 0

    def case(name, ok, detail=""):
        nonlocal total, failed
        total += 1
        status = "ok " if ok else "XX "
        print("  %s %s%s" % (status, name, ("  << " + repr(detail)) if (not ok and detail != "") else ""))
        if not ok:
            failed += 1

    NOW = date(2026, 7, 23)

    def write(path, text):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    # ---- count_attention_items ---------------------------------------------------------
    briefing_text = (
        "**All clear:** 3 checks ran clean.\n\n"
        "**Needs your attention:**\n\n"
        "1. First item, three sentences, whatever.\n"
        "2. Second item, more detail here.\n"
        "3. Third item.\n\n"
        "**Watching:**\n"
        "- a declared red, not a finding\n"
    )
    case("count_attention_items: 3 numbered items counted", count_attention_items(briefing_text) == 3)
    case("count_attention_items: no heading -> 0",
         count_attention_items("**All clear:** nothing else here.\n") == 0)
    all_clear_text = "**All clear:** 1 check ran clean.\n\n**Watching:**\n- nothing\n"
    case("count_attention_items: heading absent entirely -> 0",
         count_attention_items(all_clear_text) == 0)

    # ---- parse_schedule_log / collect_schedule_log --------------------------------------
    log_text = "\n".join([
        "===== sweep run Wed 07/22/2026  5:00:02.89 ===== ",
        "some noise line",
        "===== sweep done Wed 07/22/2026  5:04:14.01 (exit 0) ===== ",
        "===== sweep run Thu 07/23/2026  5:00:03.71 ===== ",
        "Sweep complete.",
        "===== sweep done Thu 07/23/2026  5:04:33.02 (exit 0) ===== ",
        "===== sweep run Thu 07/23/2026 20:00:00.00 ===== ",  # unfinished tail, no 'done'
        "",
    ])
    entries = parse_schedule_log(log_text)
    case("parse_schedule_log: 3 entries found (2 complete + 1 unfinished)", len(entries) == 3, entries)
    case("parse_schedule_log: last entry unfinished (end=None)", entries[-1]["end"] is None, entries)
    case("parse_schedule_log: first entry duration ~4.2 minutes",
         entries[0]["end"] is not None
         and abs((entries[0]["end"] - entries[0]["start"]).total_seconds() / 60.0 - 4.2) < 0.5,
         entries[0])

    base = tempfile.mkdtemp(prefix="desk-metrics-selftest-")
    try:
        log_path = os.path.join(base, "sweep-schedule.log")
        write(log_path, log_text)
        sched = collect_schedule_log(log_path, NOW, window_days=7)
        case("collect_schedule_log: present=True", sched["present"] is True, sched)
        case("collect_schedule_log: 3 runs in a 7-day window ending 2026-07-23",
             sched["runs_in_window"] == 3, sched)
        case("collect_schedule_log: 2 completed, 1 unfinished",
             sched["completed_in_window"] == 2 and sched["unfinished_in_window"] == 1, sched)
        case("collect_schedule_log: avg duration is a positive number",
             isinstance(sched["avg_duration_minutes"], float) and sched["avg_duration_minutes"] > 0, sched)

        missing_log = collect_schedule_log(os.path.join(base, "no-such.log"), NOW)
        case("collect_schedule_log: absent file -> present=False with a note",
             missing_log["present"] is False and "note" in missing_log, missing_log)

        # ---- collect_compile_spend --------------------------------------------------
        receipts_dir = os.path.join(base, "receipts")
        write(os.path.join(receipts_dir, "2026-07-20T090000-compile.md"),
              "---\ntype: compile\ntimestamp: 2026-07-20T09:00:00\ntoken_cost: ~50000\n---\nbody\n")
        write(os.path.join(receipts_dir, "2026-07-23T163000-compile.md"),
              "---\ntype: compile\ntimestamp: 2026-07-23T16:30:00\ntoken_cost: ~180000\n---\nbody\n")
        write(os.path.join(receipts_dir, "2026-06-01T000000-compile.md"),  # outside the window
              "---\ntype: compile\ntimestamp: 2026-06-01T00:00:00\ntoken_cost: ~99999\n---\nbody\n")
        write(os.path.join(receipts_dir, "2026-07-21T000000-compile.md"),  # missing token_cost
              "---\ntype: compile\ntimestamp: 2026-07-21T00:00:00\nduration_minutes: 10\n---\nbody\n")
        write(os.path.join(receipts_dir, "not-a-compile-receipt.md"), "irrelevant\n")

        spend = collect_compile_spend(receipts_dir, NOW, window_days=7)
        case("collect_compile_spend: present=True", spend["present"] is True, spend)
        case("collect_compile_spend: 3 in-window compile receipts (excludes the June one)",
             spend["files_in_window"] == 3, spend)
        case("collect_compile_spend: 2 parsed token_cost values, 1 missing",
             spend["token_cost_files_parsed"] == 2
             and spend["token_cost_files_malformed_or_missing"] == 1, spend)
        case("collect_compile_spend: sum is 50000+180000=230000 (tilde-prefixed values parsed)",
             spend["token_cost_sum"] == 230000, spend)

        missing_receipts = collect_compile_spend(os.path.join(base, "no-such-dir"), NOW)
        case("collect_compile_spend: absent receipts/ -> present=False with a note",
             missing_receipts["present"] is False and "note" in missing_receipts, missing_receipts)

        # ---- collect_briefing ---------------------------------------------------------
        briefing_path = os.path.join(base, "SWEEP-BRIEFING.md")
        write(briefing_path, briefing_text)
        b = collect_briefing(briefing_path)
        case("collect_briefing: present=True, attention_items=3", b == {"present": True, "attention_items": 3}, b)
        b_missing = collect_briefing(os.path.join(base, "no-such-briefing.md"))
        case("collect_briefing: absent -> present=False with a note",
             b_missing["present"] is False and "note" in b_missing, b_missing)

        # ---- collect_deadlines / collect_parity (real sibling scripts, fixture inputs) -
        deploy_dir = _HERE  # the real deploy/ this file lives in -- check-deadlines.py /
                             # check-parity.py are right there; only their INPUTS are fixtures.
        reg_path = os.path.join(base, "deadline-register.yaml")
        write(reg_path, "\n".join([
            "rows:",
            "  - what: attention row",
            "    when: 2026-08-01",
            "    source: fixture",
            "    owner: operator",
            "    status: watching",
        ]))
        if os.path.isfile(os.path.join(deploy_dir, "check-deadlines.py")):
            dl = collect_deadlines(deploy_dir, register_path=reg_path, today="2026-07-23")
            case("collect_deadlines: present=True against a fixture register",
                 dl.get("present") is True, dl)
            case("collect_deadlines: attention count is 1 (row due in 9 days)",
                 dl.get("attention") == 1, dl)
        else:
            case("collect_deadlines: check-deadlines.py not found next to this script (skip)", True)

        dl_missing = collect_deadlines(deploy_dir,
                                        register_path=os.path.join(base, "no-such-register.yaml"),
                                        today="2026-07-23")
        case("collect_deadlines: fixture-absent register -> present=False",
             dl_missing.get("present") is False, dl_missing)

        dl_no_script = collect_deadlines(os.path.join(base, "no-deploy-here"))
        case("collect_deadlines: script itself absent -> present=False with a note",
             dl_no_script.get("present") is False and "note" in dl_no_script, dl_no_script)

        if os.path.isfile(os.path.join(deploy_dir, "check-parity.py")):
            fork_fx = os.path.join(base, "parity-fork")
            tmpl_fx = os.path.join(base, "parity-template")
            write(os.path.join(fork_fx, "deploy", "a.py"), "print(1)\n")
            write(os.path.join(tmpl_fx, "capabilities", "knowledge-os", "extracted", "deploy", "a.py"), "print(2)\n")
            pr = collect_parity(deploy_dir, fork_root=fork_fx, template_root=tmpl_fx)
            case("collect_parity: present=True against fixture roots", pr.get("present") is True, pr)
            case("collect_parity: 1 file DIFFERS", pr.get("differs") == 1, pr)

            empty_root = os.path.join(base, "no-project-yaml-here")
            os.makedirs(empty_root, exist_ok=True)
            pr_absent = collect_parity(deploy_dir, fork_root=empty_root, template_root=None)
            case("collect_parity: no template_path resolvable -> present=False",
                 pr_absent.get("present") is False, pr_absent)
        else:
            case("collect_parity: check-parity.py not found next to this script (skip)", True)

        # ---- build_metrics end-to-end, then append_entry + load_history ----------------
        entry = build_metrics(
            base, today=NOW, deploy_dir=deploy_dir, briefing_path=briefing_path,
            schedule_log_path=log_path, receipts_dir=receipts_dir, register_path=reg_path,
            fork_root=os.path.join(base, "parity-fork"),
            template_root=os.path.join(base, "parity-template"),
        )
        case("build_metrics: date matches --today", entry["date"] == "2026-07-23", entry)
        case("build_metrics: every top-level section present",
             all(k in entry for k in ("deadlines", "parity", "briefing", "spend")), entry)

        hist_path = os.path.join(base, "metrics-history.jsonl")
        append_entry(hist_path, entry)
        append_entry(hist_path, entry)  # a second append must not clobber, just add a line
        with open(hist_path, "r", encoding="utf-8") as fh:
            raw_lines = [l for l in fh.read().splitlines() if l.strip()]
        case("append_entry: two appends -> two lines, each valid JSON",
             len(raw_lines) == 2 and all(json.loads(l) for l in raw_lines), raw_lines)

        loaded, load_notes = load_history(hist_path)
        case("load_history: reads both lines back", len(loaded) == 2, loaded)

        missing_hist_entries, missing_hist_notes = load_history(os.path.join(base, "no-hist.jsonl"))
        case("load_history: absent file -> empty list + a note",
             missing_hist_entries == [] and len(missing_hist_notes) == 1, missing_hist_notes)

        malformed_hist = os.path.join(base, "malformed-history.jsonl")
        write(malformed_hist, '{"date": "2026-07-20"}\nnot json at all\n{"date": "2026-07-21"}\n')
        m_entries, m_notes = load_history(malformed_hist)
        case("load_history: malformed line skipped, valid ones kept, a note recorded",
             len(m_entries) == 2 and len(m_notes) == 1, (m_entries, m_notes))

        # ---- dedupe_by_date / compute_trend_line / compute_trends -----------------------
        rising_hist = [
            {"date": "2026-07-20", "deadlines": {"present": True, "attention": 1}},
            {"date": "2026-07-21", "deadlines": {"present": True, "attention": 2}},
            {"date": "2026-07-22", "deadlines": {"present": True, "attention": 3}},
            {"date": "2026-07-23", "deadlines": {"present": True, "attention": 4}},
        ]
        lines, note = compute_trends(rising_hist)
        case("compute_trends: rising trend line produced for deadlines.attention",
             any("attention" in l and "rising" in l for l in lines), lines)

        falling_hist = [
            {"date": "2026-07-20", "parity": {"present": True, "differs": 9}},
            {"date": "2026-07-21", "parity": {"present": True, "differs": 6}},
            {"date": "2026-07-22", "parity": {"present": True, "differs": 3}},
        ]
        lines2, _ = compute_trends(falling_hist)
        case("compute_trends: falling trend line produced for parity.differs",
             any("mirror-drift" in l and "falling" in l for l in lines2), lines2)

        steady_hist = [
            {"date": "2026-07-20", "briefing": {"present": True, "attention_items": 2}},
            {"date": "2026-07-21", "briefing": {"present": True, "attention_items": 2}},
        ]
        lines3, _ = compute_trends(steady_hist)
        case("compute_trends: steady trend line produced for briefing.attention_items",
             any("steady" in l for l in lines3), lines3)

        one_entry_hist = [{"date": "2026-07-23", "deadlines": {"present": True, "attention": 1}}]
        lines4, note4 = compute_trends(one_entry_hist)
        case("compute_trends: single day -> no lines, a 'not enough history' note",
             lines4 == [] and "not enough history" in (note4 or ""), (lines4, note4))

        dup_dates_hist = [
            {"date": "2026-07-22", "deadlines": {"present": True, "attention": 1}},
            {"date": "2026-07-22", "deadlines": {"present": True, "attention": 5}},  # same day, re-run
            {"date": "2026-07-23", "deadlines": {"present": True, "attention": 5}},
        ]
        deduped = dedupe_by_date(dup_dates_hist)
        case("dedupe_by_date: same-day re-append keeps the LATER value, one entry per date",
             len(deduped) == 2 and deduped[0]["deadlines"]["attention"] == 5, deduped)

        # ---- CLI wiring smoke: --append then --trends against real fixtures -------------
        cli_hist = os.path.join(base, "cli-history.jsonl")
        rc1 = cmd_append(["--root", base, "--history", cli_hist, "--today", "2026-07-22",
                           "--deploy-dir", deploy_dir, "--briefing", briefing_path,
                           "--schedule-log", log_path, "--receipts-dir", receipts_dir,
                           "--register", reg_path,
                           "--fork-root", os.path.join(base, "parity-fork"),
                           "--template-root", os.path.join(base, "parity-template")])
        rc2 = cmd_append(["--root", base, "--history", cli_hist, "--today", "2026-07-23",
                           "--deploy-dir", deploy_dir, "--briefing", briefing_path,
                           "--schedule-log", log_path, "--receipts-dir", receipts_dir,
                           "--register", reg_path,
                           "--fork-root", os.path.join(base, "parity-fork"),
                           "--template-root", os.path.join(base, "parity-template"), "--json"])
        case("CLI: two --append calls both return 0", rc1 == 0 and rc2 == 0)
        with open(cli_hist, "r", encoding="utf-8") as fh:
            cli_lines = [l for l in fh.read().splitlines() if l.strip()]
        case("CLI: --append wrote exactly 2 lines for 2 distinct --today dates", len(cli_lines) == 2, cli_lines)

        rc3 = cmd_trends(["--history", cli_hist])
        rc4 = cmd_trends(["--history", cli_hist, "--json"])
        case("CLI: --trends (text and --json) both return 0", rc3 == 0 and rc4 == 0)

        rc5 = cmd_append(["--root", base, "--history", os.path.join(base, "bad-today-history.jsonl"),
                           "--today", "not-a-date", "--deploy-dir", deploy_dir])
        case("CLI: an unparseable --today degrades to wall-clock date, still returns 0", rc5 == 0)

    finally:
        shutil.rmtree(base, ignore_errors=True)

    print("desk-metrics self-test: %s (%d/%d)"
          % ("PASS" if failed == 0 else "FAIL", total - failed, total))
    return 0 if failed == 0 else 1


# --------------------------------------------------------------------------- main

def main(argv):
    args = argv[1:] if argv and argv[0].endswith(".py") else list(argv)
    if "--self-test" in args:
        return self_test()
    if "--append" in args:
        return cmd_append(args)
    if "--trends" in args:
        return cmd_trends(args)
    print("desk-metrics: nothing to do -- pass --append, --trends, or --self-test")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
