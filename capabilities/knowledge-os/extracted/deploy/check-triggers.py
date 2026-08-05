#!/usr/bin/env python3
"""check-triggers.py -- the trigger-register sensor (knowledge-os, v3.0-64).

The harness's fourth trigger type: DECLARED CONDITIONS. Skills otherwise fire on operator
memory, session reflexes, or the clock; deploy/trigger-register.yaml is the file that
declares "it is time to run X" as a machine-evaluable condition, and this sensor is the
thing that evaluates it. (Design history: a dev-repo spec, 2026-07-28, not shipped --
this docstring and the register .example are the live contract.)
(five-pass /reason run, 2026-07-28).

Doctrine (the spec's pass-2 kills, binding):
  - Rows PROPOSE; they never run anything. `authorization: propose` is the only
    implemented class in v1 -- any other value is a LOUD schema error (the field exists
    so rows survive the v3.0-63 arming machinery unchanged).
  - STATELESS: this sensor writes nothing. A fired condition re-arms only when its
    `resets_on` receipt is minted (running the proposed action produces the receipt that
    silences the trigger). No last_fired bookkeeping, no write-back -- sources win.
  - CLOSED predicate vocabulary (six entries, below). Free-text conditions are REFUSED
    by design: un-enumerated conditions can only be human-judged and stay in
    deadline-register.yaml's Watching list. Deadlines = dates a human owns; triggers =
    conditions a script owns. Two files.

PREDICATE VOCABULARY v1 (closed):
  days_since_receipt(type, days)                 newest receipts/*-<type>.md older than
                                                 N days, or none exists -> MET
  unprocessed_raw(min_count)                     raw/*.md absent from every wiki
                                                 frontmatter `sources:` block (archival
                                                 `compile: false` files excluded) >= N
  articles_changed_since_receipt(type, min_count) wiki/** files git-committed since the
                                                 newest <type> receipt's timestamp >= N
                                                 (no receipt -> all wiki history counts)
  file_age(path, days)                           file mtime older than N days, or the
                                                 file is absent -> MET (absence of a
                                                 freshness-class projection is stale)
  count_lines_matching(glob, regex, min)         matching lines across glob'd files >= N
  marker_present(glob, regex)                    any glob'd file has a matching line

An unknown predicate, an unknown `authorization` value, a missing required key, or a
duplicate id is a BLOCKING schema violation: reported plainly, the row excluded from
evaluation, and the process exits 1 (the spec's fail-loud rule -- a rotting register row
must never be silently skipped). The report itself always completes. A `retired` row is
counted, never evaluated, never deleted.

Git is used only by articles_changed_since_receipt; if git is unavailable that row
evaluates INCONCLUSIVE (reported, never a crash). PyYAML is OPTIONAL, guarded exactly
like check-deadlines.py: absent PyYAML degrades to a top-level problem with zero rows.

Usage:
  check-triggers.py [--register PATH] [--root PATH] [--json] [--today YYYY-MM-DD]
  check-triggers.py --self-test
Exit: 0 on a completed report with no blocking schema violations | 1 on any blocking
  schema violation or --self-test failure | never crashes on malformed input.
"""

import fnmatch
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta

try:
    import yaml
except ImportError:  # pragma: no cover -- PyYAML is optional everywhere in this repo
    yaml = None

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_REGISTER = os.path.join(_HERE, "trigger-register.yaml")
_DEFAULT_ROOT = os.path.dirname(_HERE)

VALID_STATUSES = {"active", "retired"}
VALID_AUTHORIZATIONS = {"propose"}
REQUIRED_KEYS = ("id", "condition", "action", "rationale", "authorization", "source",
                 "status")
ALLOWED_KEYS = set(REQUIRED_KEYS) | {"resets_on"}

# predicate name -> (required arg names, int-valued arg names)
PREDICATES = {
    "days_since_receipt": (("type", "days"), ("days",)),
    "unprocessed_raw": (("min_count",), ("min_count",)),
    "articles_changed_since_receipt": (("type", "min_count"), ("min_count",)),
    "file_age": (("path", "days"), ("days",)),
    "count_lines_matching": (("glob", "regex", "min"), ("min",)),
    "marker_present": (("glob", "regex"), ()),
}

_RECEIPT_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:T(\d{2})(\d{2})(\d{2}))?-")


# --------------------------------------------------------------------------- register load

def load_register(path):
    """Same contract as check-deadlines.load_register: (rows_raw, problems, present)."""
    if not os.path.isfile(path):
        return [], [], False
    problems = []
    if yaml is None:
        problems.append("PyYAML not installed -- cannot parse trigger-register.yaml; "
                        "treated as 0 rows (degrade)")
        return [], problems, True
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            doc = yaml.safe_load(fh)
    except (OSError, UnicodeDecodeError) as e:
        problems.append("trigger-register.yaml unreadable: %s" % e)
        return [], problems, True
    except Exception as e:  # yaml.YAMLError and anything else -- malformed, never a crash
        problems.append("trigger-register.yaml malformed YAML: %s" % e)
        return [], problems, True
    if doc is None:
        problems.append("trigger-register.yaml is empty (no top-level 'rows:' key)")
        return [], problems, True
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        problems.append("trigger-register.yaml malformed: expected a top-level 'rows:' list")
        return [], problems, True
    return doc["rows"], problems, True


# --------------------------------------------------------------------------- row validation

def validate_row(idx, raw, seen_ids):
    """Returns (row_dict, blocking_issues, warnings). Blocking issues exclude the row
    from evaluation AND make the whole run exit 1 (fail-loud); warnings (unknown extra
    keys) are reported but the row still evaluates -- report, don't drop."""
    if not isinstance(raw, dict):
        return None, ["row %d is not a mapping: %r" % (idx, raw)], []

    issues = []
    warnings = []

    unknown = sorted(str(k) for k in raw.keys() if k not in ALLOWED_KEYS)
    if unknown:
        warnings.append("row %d (%s): unknown key(s): %s"
                        % (idx, raw.get("id", "?"), ", ".join(unknown)))

    missing = [k for k in REQUIRED_KEYS if not raw.get(k)]
    if missing:
        issues.append("row %d (%s): missing required key(s): %s"
                      % (idx, raw.get("id", "?"), ", ".join(missing)))

    rid = raw.get("id")
    if rid is not None:
        if not isinstance(rid, str):
            issues.append("row %d: non-string id: %r" % (idx, rid))
        elif rid in seen_ids:
            issues.append("row %d: duplicate id %r" % (idx, rid))
        else:
            seen_ids.add(rid)

    auth = raw.get("authorization")
    if auth is not None and auth not in VALID_AUTHORIZATIONS:
        issues.append("row %d (%s): unknown authorization %r (v1 implements only: %s)"
                      % (idx, rid or "?", auth, ", ".join(sorted(VALID_AUTHORIZATIONS))))

    status = raw.get("status")
    if status is not None and status not in VALID_STATUSES:
        issues.append("row %d (%s): invalid status %r (expected one of %s)"
                      % (idx, rid or "?", status, ", ".join(sorted(VALID_STATUSES))))

    cond = raw.get("condition")
    pred = None
    if cond is not None:
        if not isinstance(cond, dict) or not cond.get("predicate"):
            issues.append("row %d (%s): condition must be a mapping with a 'predicate' key"
                          % (idx, rid or "?"))
        else:
            pred = cond.get("predicate")
            if pred not in PREDICATES:
                issues.append("row %d (%s): unknown predicate %r (closed vocabulary: %s)"
                              % (idx, rid or "?", pred, ", ".join(sorted(PREDICATES))))
            else:
                req, ints = PREDICATES[pred]
                miss = [a for a in req if cond.get(a) in (None, "")]
                if miss:
                    issues.append("row %d (%s): predicate %s missing arg(s): %s"
                                  % (idx, rid or "?", pred, ", ".join(miss)))
                bad = [a for a in ints if cond.get(a) is not None
                       and not isinstance(cond.get(a), int)]
                if bad:
                    issues.append("row %d (%s): predicate %s non-integer arg(s): %s"
                                  % (idx, rid or "?", pred, ", ".join(bad)))
                extra = sorted(str(k) for k in cond.keys()
                               if k != "predicate" and k not in req)
                if extra:
                    warnings.append("row %d (%s): predicate %s unknown arg(s): %s"
                                    % (idx, rid or "?", pred, ", ".join(extra)))

    row = {"index": idx, "id": rid, "condition": cond, "predicate": pred,
           "action": raw.get("action"), "rationale": raw.get("rationale"),
           "resets_on": raw.get("resets_on"), "source": raw.get("source"),
           "status": status}
    return row, issues, warnings


# --------------------------------------------------------------------------- evaluation

def _newest_receipt_dt(root, rtype):
    """Newest timestamp among receipts/*-<type>.md, parsed from the filename prefix
    (YYYY-MM-DD or YYYY-MM-DDTHHMMSS). Files whose names don't parse are ignored --
    the receipt-naming convention IS the contract. Returns datetime or None."""
    rdir = os.path.join(root, "receipts")
    if not os.path.isdir(rdir):
        return None
    newest = None
    suffix = "-%s.md" % rtype
    for name in os.listdir(rdir):
        if not name.endswith(suffix):
            continue
        m = _RECEIPT_TS_RE.match(name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            continue
        if m.group(2):
            d = d.replace(hour=int(m.group(2)), minute=int(m.group(3)),
                          second=int(m.group(4)))
        if newest is None or d > newest:
            newest = d
    return newest


def _iter_glob(root, pattern):
    """Yield paths (relative to root) matching a /-separated glob. `**` in the first
    segment position is supported via a full walk; otherwise fnmatch per path."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__",
                                                        ".batch-run")]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, pattern.replace("**/", "")):
                yield os.path.join(dirpath, fn)


def _count_matching_lines(root, pattern, regex, stop_at=None):
    try:
        rx = re.compile(regex)
    except re.error as e:
        return None, "bad regex %r: %s" % (regex, e)
    n = 0
    for path in _iter_glob(root, pattern):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if rx.search(line):
                        n += 1
                        if stop_at is not None and n >= stop_at:
                            return n, None
        except OSError:
            continue
    return n, None


def _frontmatter_block(path):
    """Return the frontmatter text between the first two '---' fences, or ''. Reads the
    whole block (never line-anchored) so formatter-flattened frontmatter still matches."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(65536)
    except OSError:
        return ""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[:end] if end != -1 else text


def evaluate(row, root, today):
    """Returns (met_bool_or_None, detail). None = INCONCLUSIVE (reported, never met)."""
    cond = row["condition"]
    pred = row["predicate"]
    now = datetime(today.year, today.month, today.day)

    if pred == "days_since_receipt":
        newest = _newest_receipt_dt(root, cond["type"])
        if newest is None:
            return True, "no receipts/*-%s.md exists (never run)" % cond["type"]
        age = (now - newest).days
        met = age > cond["days"]
        return met, "newest %s receipt is %d day(s) old (threshold %d)" % (
            cond["type"], age, cond["days"])

    if pred == "unprocessed_raw":
        rdir = os.path.join(root, "raw")
        if not os.path.isdir(rdir):
            return False, "no raw/ directory"
        raw_files = [f for f in os.listdir(rdir)
                     if f.endswith(".md") and os.path.isfile(os.path.join(rdir, f))]
        # collect every wiki frontmatter block once
        fronts = []
        wdir = os.path.join(root, "wiki")
        if os.path.isdir(wdir):
            for dirpath, _dirnames, filenames in os.walk(wdir):
                for fn in filenames:
                    if fn.endswith(".md"):
                        fronts.append(_frontmatter_block(os.path.join(dirpath, fn)))
        blob = "\n".join(fronts)
        unprocessed = 0
        for f in raw_files:
            fm = _frontmatter_block(os.path.join(rdir, f))
            if re.search(r"^\s*compile:\s*false\s*$", fm, re.MULTILINE) or \
               "compile: false" in fm:
                continue  # archival -- never "unprocessed" by design
            if ("raw/" + f) not in blob:
                unprocessed += 1
        met = unprocessed >= cond["min_count"]
        return met, "%d unprocessed raw file(s) (threshold %d)" % (
            unprocessed, cond["min_count"])

    if pred == "articles_changed_since_receipt":
        newest = _newest_receipt_dt(root, cond["type"])
        cmd = ["git", "-C", root, "log", "--name-only", "--pretty=format:", "--",
               "wiki/"]
        if newest is not None:
            cmd = cmd[:5] + ["--since=%s" % newest.isoformat()] + cmd[5:]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            return None, "git unavailable: %s" % e
        if out.returncode != 0:
            return None, "git log failed: %s" % out.stderr.strip()[:200]
        changed = {ln.strip() for ln in out.stdout.splitlines() if ln.strip()}
        met = len(changed) >= cond["min_count"]
        base = ("since the newest %s receipt (%s)" % (cond["type"], newest.isoformat())
                if newest is not None
                else "across all history (no %s receipt exists)" % cond["type"])
        return met, "%d wiki file(s) changed %s (threshold %d)" % (
            len(changed), base, cond["min_count"])

    if pred == "file_age":
        path = os.path.join(root, cond["path"])
        if not os.path.isfile(path):
            return True, "%s is absent (a missing freshness-class file is stale)" % cond["path"]
        age = (now - datetime.fromtimestamp(os.path.getmtime(path))).days
        met = age > cond["days"]
        return met, "%s is %d day(s) old (threshold %d)" % (cond["path"], age, cond["days"])

    if pred == "count_lines_matching":
        n, err = _count_matching_lines(root, cond["glob"], cond["regex"])
        if err:
            return None, err
        met = n >= cond["min"]
        return met, "%d line(s) matching %r across %s (threshold %d)" % (
            n, cond["regex"], cond["glob"], cond["min"])

    if pred == "marker_present":
        n, err = _count_matching_lines(root, cond["glob"], cond["regex"], stop_at=1)
        if err:
            return None, err
        return n > 0, ("marker %r %s in %s"
                       % (cond["regex"], "present" if n else "absent", cond["glob"]))

    return None, "unreachable: unknown predicate survived validation"  # pragma: no cover


# --------------------------------------------------------------------------- report

def build_report(register_path, root, today=None):
    today = today or date.today()
    report = {"register_path": register_path, "root": root, "today": today.isoformat()}

    rows_raw, problems, present = load_register(register_path)
    if not present:
        report["degraded"] = True
        report["note"] = ("no trigger register at %s -- nothing to report (degrade)"
                          % register_path)
        report["blocking"] = False
        return report

    report["degraded"] = False
    report["top_level_problems"] = problems

    seen_ids = set()
    rows, schema_issues, schema_warnings = [], [], []
    for i, raw in enumerate(rows_raw):
        row, issues, warns = validate_row(i, raw, seen_ids)
        schema_issues.extend(issues)
        schema_warnings.extend(warns)
        if row is not None and not issues:
            rows.append(row)

    met, unmet, inconclusive, retired = [], [], [], []
    for row in rows:
        if row["status"] == "retired":
            retired.append(row)
            continue
        ok, detail = evaluate(row, root, today)
        row["detail"] = detail
        if ok is None:
            inconclusive.append(row)
        elif ok:
            met.append(row)
        else:
            unmet.append(row)

    report["schema_issues"] = schema_issues
    report["schema_warnings"] = schema_warnings
    report["met"] = met
    report["unmet"] = unmet
    report["inconclusive"] = inconclusive
    report["counts"] = {"total": len(rows_raw), "met": len(met), "unmet": len(unmet),
                        "inconclusive": len(inconclusive), "retired": len(retired),
                        "schema_issues": len(schema_issues),
                        "schema_warnings": len(schema_warnings)}
    # fail-loud rule: blocking schema violations (or a malformed/unreadable register)
    # flip the exit code -- a rotting register must never be silently skipped.
    report["blocking"] = bool(schema_issues or problems)
    return report


def print_report(report, as_json=False):
    if as_json:
        print(json.dumps(report, indent=1, sort_keys=True, default=str))
        return
    if report.get("degraded"):
        print("check-triggers: NOTE -- %s" % report["note"])
        return

    c = report["counts"]
    print("check-triggers: %s (today=%s)" % (report["register_path"], report["today"]))
    print("  %d row(s) read: %d MET / %d unmet / %d inconclusive / %d retired / "
          "%d schema issue(s) / %d schema warning(s)"
          % (c["total"], c["met"], c["unmet"], c["inconclusive"], c["retired"],
             c["schema_issues"], c["schema_warnings"]))
    for p in report["top_level_problems"]:
        print("  top-level problem: %s" % p)

    print()
    print("  MET -- conditions proposing action (%d):" % c["met"])
    for r in report["met"]:
        print("    - [%s] propose: %s" % (r["id"], r["action"]))
        print("        why now: %s" % r["detail"])
        print("        rationale: %s (source: %s)" % (r["rationale"], r["source"]))
        if r.get("resets_on"):
            print("        re-arms when a '%s' receipt is minted" % r["resets_on"])
    if not report["met"]:
        print("    (none)")

    print()
    print("  Unmet -- evaluated, not due (%d):" % c["unmet"])
    for r in report["unmet"]:
        print("    - [%s] %s" % (r["id"], r["detail"]))
    if not report["unmet"]:
        print("    (none)")

    if report["inconclusive"]:
        print()
        print("  INCONCLUSIVE -- could not evaluate (%d):" % c["inconclusive"])
        for r in report["inconclusive"]:
            print("    - [%s] %s" % (r["id"], r["detail"]))

    if report["schema_issues"] or report["schema_warnings"]:
        print()
        print("  Schema findings (fail-loud; blocking issues flip the exit code):")
        for msg in report["schema_issues"]:
            print("    - BLOCKING: %s" % msg)
        for msg in report["schema_warnings"]:
            print("    - warning: %s" % msg)


# --------------------------------------------------------------------------- self-test

def self_test():
    import shutil
    import tempfile

    total = failed = 0

    def case(name, ok, detail=""):
        nonlocal total, failed
        total += 1
        print("  %s %s%s" % ("ok " if ok else "XX ", name,
                             ("  << " + repr(detail)) if (not ok and detail != "") else ""))
        if not ok:
            failed += 1

    NOW = date(2026, 7, 28)  # fixed reference date -- deterministic

    def write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def mkrow(rid, cond, extra=""):
        return ("  - id: %s\n    condition: %s\n    action: \"/x\"\n"
                "    rationale: \"r\"\n    authorization: propose\n"
                "    source: \"s\"\n    status: active\n%s" % (rid, cond, extra))

    # ---- Case A: absent register -> NOTE, degrade, exit 0 ------------------------------
    base = tempfile.mkdtemp(prefix="cktr-absent-")
    try:
        rep = build_report(os.path.join(base, "nope.yaml"), base, today=NOW)
        case("absent register: degraded, non-blocking",
             rep["degraded"] is True and rep["blocking"] is False)
        rc = main(["--register", os.path.join(base, "nope.yaml"), "--root", base,
                   "--today", "2026-07-28"])
        case("absent register: main() exits 0", rc == 0)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    if yaml is None:
        print("  -- remaining sections SKIPPED (PyYAML not installed; degrade contract "
              "confirmed above)")
        print("check-triggers self-test: %s (%d/%d)"
              % ("PASS" if failed == 0 else "FAIL", total - failed, total))
        return 0 if failed == 0 else 1

    # ---- Case B: schema refusals are BLOCKING and loud ---------------------------------
    base = tempfile.mkdtemp(prefix="cktr-schema-")
    try:
        reg = os.path.join(base, "reg.yaml")
        write(reg, "rows:\n"
              + mkrow("ok-row", "{ predicate: file_age, path: X.md, days: 2 }")
              + mkrow("bad-pred", "{ predicate: vibes, days: 2 }")
              + mkrow("bad-auth", "{ predicate: file_age, path: X.md, days: 2 }")
                .replace("authorization: propose", "authorization: auto_run")
              + mkrow("ok-row", "{ predicate: file_age, path: X.md, days: 2 }")
              + "  - id: missing-most\n    status: active\n"
              + mkrow("extra-key", "{ predicate: file_age, path: X.md, days: 2 }",
                      "    snooze: true\n"))
        rep = build_report(reg, base, today=NOW)
        case("unknown predicate: blocking issue naming the vocabulary",
             any("unknown predicate" in m and "vibes" in m for m in rep["schema_issues"]),
             rep["schema_issues"])
        case("unknown authorization: blocking issue",
             any("unknown authorization" in m for m in rep["schema_issues"]))
        case("duplicate id: blocking issue",
             any("duplicate id" in m for m in rep["schema_issues"]))
        case("missing keys: blocking issue",
             any("missing required key" in m for m in rep["schema_issues"]))
        case("unknown row key: WARNING only, row still evaluated",
             any("snooze" in m for m in rep["schema_warnings"])
             and any(r["id"] == "extra-key" for r in rep["met"] + rep["unmet"]))
        case("blocking flag set", rep["blocking"] is True)
        rc = main(["--register", reg, "--root", base, "--today", "2026-07-28"])
        case("schema violation: main() exits 1", rc == 1)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    # ---- Case C: each predicate, met and unmet + resets_on re-arm ----------------------
    base = tempfile.mkdtemp(prefix="cktr-preds-")
    try:
        # receipts: an old audit receipt (35d), a fresh compile receipt (0d)
        write(os.path.join(base, "receipts", "2026-06-23T141111-audit.md"), "x\n")
        write(os.path.join(base, "receipts", "2026-07-28T120000-compile.md"),
              "scan: q | files~3 | minutes~2\nscan: r | files~9 | minutes~4\n")
        # raw: one routed, one unrouted, one archival
        write(os.path.join(base, "raw", "a.md"), "---\nsource: s\n---\nbody\n")
        write(os.path.join(base, "raw", "b.md"), "---\nsource: s\n---\nbody\n")
        write(os.path.join(base, "raw", "c.md"), "---\ncompile: false\n---\nbody\n")
        write(os.path.join(base, "wiki", "d", "art.md"),
              "---\nsources:\n  - raw/a.md\n---\nbody\n")
        # stale file for file_age (mtime now => 0 days => unmet at days: 2)
        write(os.path.join(base, "DECISIONS-PENDING.md"), "x\n")

        reg = os.path.join(base, "reg.yaml")
        write(reg, "rows:\n"
              + mkrow("audit-old", "{ predicate: days_since_receipt, type: audit, days: 14 }",
                      "    resets_on: audit\n")
              + mkrow("compile-fresh", "{ predicate: days_since_receipt, type: compile, days: 14 }")
              + mkrow("never-run", "{ predicate: days_since_receipt, type: discover, days: 14 }")
              + mkrow("raw-pending", "{ predicate: unprocessed_raw, min_count: 1 }")
              + mkrow("raw-high-bar", "{ predicate: unprocessed_raw, min_count: 5 }")
              + mkrow("dp-fresh", "{ predicate: file_age, path: DECISIONS-PENDING.md, days: 2 }")
              + mkrow("gone-stale", "{ predicate: file_age, path: NOT-THERE.md, days: 2 }")
              + mkrow("scan-low", "{ predicate: count_lines_matching, glob: \"receipts/*.md\", regex: \"^scan:\", min: 10 }")
              + mkrow("scan-met", "{ predicate: count_lines_matching, glob: \"receipts/*.md\", regex: \"^scan:\", min: 2 }")
              + mkrow("marker-yes", "{ predicate: marker_present, glob: \"receipts/*.md\", regex: \"^scan:\" }")
              + mkrow("marker-no", "{ predicate: marker_present, glob: \"receipts/*.md\", regex: \"^nope:\" }")
              + mkrow("old-retired", "{ predicate: days_since_receipt, type: audit, days: 14 }")
                .replace("status: active", "status: retired"))
        rep = build_report(reg, base, today=NOW)
        ids_met = {r["id"] for r in rep["met"]}
        ids_unmet = {r["id"] for r in rep["unmet"]}
        case("days_since_receipt: 35d-old audit MET at 14d", "audit-old" in ids_met)
        case("days_since_receipt: fresh compile unmet", "compile-fresh" in ids_unmet)
        case("days_since_receipt: never-run type MET", "never-run" in ids_met)
        case("unprocessed_raw: 1 unrouted (archival excluded) MET at 1, unmet at 5",
             "raw-pending" in ids_met and "raw-high-bar" in ids_unmet,
             (sorted(ids_met), sorted(ids_unmet)))
        case("file_age: fresh file unmet; absent file MET",
             "dp-fresh" in ids_unmet and "gone-stale" in ids_met)
        case("count_lines_matching: 2 scan lines unmet at 10, MET at 2",
             "scan-low" in ids_unmet and "scan-met" in ids_met)
        case("marker_present: present MET, absent unmet",
             "marker-yes" in ids_met and "marker-no" in ids_unmet)
        case("retired row: counted, never evaluated",
             rep["counts"]["retired"] == 1
             and "old-retired" not in ids_met | ids_unmet)
        case("MET rows carry the propose fields",
             all(r["action"] and r["rationale"] and r["detail"] for r in rep["met"]))
        rc = main(["--register", reg, "--root", base, "--today", "2026-07-28"])
        case("clean register with MET rows: exit 0 (report-only, propose never runs)",
             rc == 0)

        # resets_on re-arm: mint a fresh audit receipt -> audit-old goes unmet
        write(os.path.join(base, "receipts", "2026-07-27T090000-audit.md"), "x\n")
        rep2 = build_report(reg, base, today=NOW)
        case("resets_on semantics: minting the receipt re-arms (audit-old now unmet)",
             "audit-old" in {r["id"] for r in rep2["unmet"]})

        try:
            print_report(rep, as_json=True)
            print_report(rep, as_json=False)
            case("both output paths print cleanly", True)
        except Exception as e:  # noqa: BLE001
            case("both output paths print cleanly", False, e)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    # ---- Case D: git-dependent predicate degrades INCONCLUSIVE off-repo ----------------
    base = tempfile.mkdtemp(prefix="cktr-git-")
    try:
        reg = os.path.join(base, "reg.yaml")
        write(reg, "rows:\n"
              + mkrow("wiki-churn", "{ predicate: articles_changed_since_receipt, type: discover, min_count: 8 }"))
        rep = build_report(reg, base, today=NOW)
        r = (rep["met"] + rep["unmet"] + rep["inconclusive"])[0]
        case("articles_changed_since_receipt: evaluates or degrades INCONCLUSIVE, "
             "never crashes", r["id"] == "wiki-churn" and bool(r["detail"]))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    # ---- Case E: malformed top-level is blocking (fail-loud, register exists) ----------
    base = tempfile.mkdtemp(prefix="cktr-badtop-")
    try:
        reg = os.path.join(base, "reg.yaml")
        write(reg, "rows: not-a-list\n")
        rep = build_report(reg, base, today=NOW)
        case("malformed top-level: reported AND blocking",
             rep["top_level_problems"] and rep["blocking"] is True)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    print("check-triggers self-test: %s (%d/%d)"
          % ("PASS" if failed == 0 else "FAIL", total - failed, total))
    return 0 if failed == 0 else 1


# --------------------------------------------------------------------------- CLI

def main(argv):
    args = argv[1:] if argv and argv[0].endswith(".py") else list(argv)
    if "--self-test" in args:
        return self_test()

    as_json = "--json" in args
    register_path = _DEFAULT_REGISTER
    root = _DEFAULT_ROOT
    if "--register" in args:
        i = args.index("--register")
        if i + 1 < len(args):
            register_path = args[i + 1]
    if "--root" in args:
        i = args.index("--root")
        if i + 1 < len(args):
            root = os.path.abspath(args[i + 1])

    today = date.today()
    if "--today" in args:
        i = args.index("--today")
        if i + 1 < len(args):
            try:
                today = datetime.strptime(args[i + 1], "%Y-%m-%d").date()
            except ValueError:
                print("check-triggers: NOTE -- unparseable --today value %r, using "
                      "wall-clock date instead" % args[i + 1])

    report = build_report(register_path, root, today=today)
    print_report(report, as_json=as_json)
    return 1 if report.get("blocking") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
