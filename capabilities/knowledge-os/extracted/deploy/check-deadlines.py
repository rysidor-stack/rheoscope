#!/usr/bin/env python3
"""check-deadlines.py -- the deadline-and-trigger sensor (knowledge-os, v3.0-42, D4).

Time-bound promises and conditional re-open triggers otherwise live as prose only, buried
in raw/ and wiki/ narrative until someone happens to reread the right paragraph -- the
long-horizon seam census (2026-07-22, item 1 "LIFETIME") names this the failure mode of
"systems dying on schedule out of well-documented prose." deploy/deadline-register.yaml is
the register that makes those clocks machine-readable; this sensor is the thing that reads
it. Report-only, degrade-never-block (spec sec.12 doctrine, same as every deploy/check-*.py
sensor): a missing or malformed register is reported plainly and never stops a sweep.

REGISTER SHAPE (see deploy/deadline-register.yaml's own header for the authoritative
schema comment): a top-level `rows:` list of flat mappings, each with `what` / exactly one
of `when` (an ISO date YYYY-MM-DD or an approximate month YYYY-MM) or `if` (plain-English
condition text) / `source` / `owner` / `status` (watching | fired | discharged).

CLASSIFICATION (D4):
  - a `watching` row with a dated `when` -- overdue (when < today) is FIRED, due within 30
    days is ATTENTION, otherwise it is watching-not-yet-due. A YYYY-MM `when` is treated as
    the first of that month and flagged "approximate" everywhere it is shown.
  - a `watching` row with an `if` -- listed under Watching-conditions. Conditions do NOT
    self-evaluate; surfacing them for a human/session to judge is this sensor's whole job.
  - a `fired` or `discharged` row (status already recorded by a human/session) -- counted in
    the summary only, never re-detailed. The sensor computes overdue-ness for `watching`
    rows on its own; it never edits the register or auto-promotes a row's status itself.
  - a row that fails BLOCKING schema validation (missing required key, both when+if present,
    neither present, a status outside the enum, an unparseable date, or a non-string value
    for `what`/`source`/`owner`) is reported as a schema finding and excluded from the
    countdown -- never a crash, never silently dropped either.
  - a row with an unknown/extra key is reported as a schema finding NAMING the key(s), but
    is still classified normally if it is otherwise valid -- report, don't drop.
  - `owner` is deliberately FREE-FORM by design: any non-empty string ("operator", "session",
    a named person, a team name, ...) is accepted. It is validated only for presence and
    string-ness, never against a fixed enum -- see deadline-register.yaml's header for the
    same note.

PyYAML is OPTIONAL, guarded exactly like every other deploy/*.py sensor in this wave
(`try: import yaml / except ImportError: yaml = None` -- see check-workspace.py's
load_registry / origin.py's load_origin_config / staleness.py's loaders for the same
fail-safe-degrade contract). If PyYAML is absent, an existing register is reported as a
top-level problem ("PyYAML not installed") with zero rows read -- degrade, never a crash.

Usage:
  check-deadlines.py [--register PATH] [--json] [--today YYYY-MM-DD]
  check-deadlines.py --self-test
Exit: 0 on any completed report (this is a reporting primitive, never a gate) | nonzero
  only from --self-test on failure.
"""

import json
import os
import re
import sys
from datetime import date, datetime

try:
    import yaml
except ImportError:  # pragma: no cover -- PyYAML is optional everywhere in this repo
    yaml = None

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_REGISTER = os.path.join(_HERE, "deadline-register.yaml")

VALID_STATUSES = {"watching", "fired", "discharged"}
ATTENTION_WINDOW_DAYS = 30

# The full set of keys this sensor understands on a row. Anything outside this set is an
# unknown/extra key -- reported as a (non-blocking) schema finding naming the key(s), never
# silently ignored and never dropping the row from classification (see validate_and_classify_row).
ALLOWED_KEYS = {"what", "when", "if", "source", "owner", "status"}

# STRING-VALUED required keys that must actually be strings, not just present. A YAML list
# or number here (e.g. `owner: [alice, bob]`) is a BLOCKING schema finding -- the row is
# skipped from classification, same treatment as a missing key.  NOTE: `owner` itself stays
# FREE-FORM by design -- any non-empty string is accepted (a person's name, "operator",
# "session", a team name, ...); this check only enforces that it IS a string, never what
# string it is.
STRING_VALUED_KEYS = ("what", "source", "owner")

_FULL_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


# --------------------------------------------------------------------------- register load

def load_register(path):
    """Returns (rows_raw, problems, present).
    rows_raw: the top-level 'rows:' list as read from YAML (elements may be anything --
    per-row validation happens later; a non-dict element is itself a schema finding).
    problems: top-level structural findings (register unreadable/malformed/absent PyYAML).
    present: False only when the file does not exist at all -- the caller uses this to
    trigger the single-NOTE absent-file degradation path (D4)."""
    if not os.path.isfile(path):
        return [], [], False

    problems = []
    if yaml is None:
        problems.append("PyYAML not installed -- cannot parse deadline-register.yaml; "
                         "treated as 0 rows (degrade)")
        return [], problems, True

    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            doc = yaml.safe_load(fh)
    except (OSError, UnicodeDecodeError) as e:
        problems.append("deadline-register.yaml unreadable: %s" % e)
        return [], problems, True
    except Exception as e:  # yaml.YAMLError and anything else -- malformed, never a crash
        problems.append("deadline-register.yaml malformed YAML: %s" % e)
        return [], problems, True

    if doc is None:
        problems.append("deadline-register.yaml is empty (no top-level 'rows:' key)")
        return [], problems, True
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        problems.append("deadline-register.yaml malformed: expected a top-level 'rows:' list")
        return [], problems, True
    return doc["rows"], problems, True


# --------------------------------------------------------------------------- date parsing

def parse_when_value(when):
    """Returns (date_obj, approximate_bool) or None if unparseable. PyYAML auto-resolves a
    bare YYYY-MM-DD scalar to a datetime.date already (YAML core-schema timestamp typing);
    a bare YYYY-MM does NOT match that resolver and arrives as a plain string, which is
    exactly the approximate-month case this treats as the first of the month."""
    if isinstance(when, datetime):
        return when.date(), False
    if isinstance(when, date):
        return when, False
    if isinstance(when, str):
        s = when.strip()
        m = _FULL_DATE_RE.match(s)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), False
            except ValueError:
                return None
        m = _MONTH_RE.match(s)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), 1), True
            except ValueError:
                return None
        return None
    return None


# --------------------------------------------------------------------------- row validation

def validate_and_classify_row(idx, raw, today):
    """Returns a processed-row dict. `valid` False means `issues` (BLOCKING findings) is
    non-empty and the row is excluded from countdown/if-row classification (category is
    None) -- it still counts toward the schema-findings total, never silently dropped.
    `warnings` holds NON-BLOCKING findings (currently: unknown/extra keys) -- these are
    reported as schema findings too, but do not affect `valid` or `category`: a row with an
    extra key is still classified normally if it is otherwise schema-valid (report, don't
    drop, per the cross-vendor review)."""
    if not isinstance(raw, dict):
        return {"index": idx, "what": None, "source": None, "owner": None, "status": None,
                "when": None, "approx": None, "if": None, "days_remaining": None,
                "issues": ["row is not a mapping: %r" % (raw,)], "warnings": [],
                "valid": False, "category": None}

    what = raw.get("what")
    source = raw.get("source")
    owner = raw.get("owner")
    status = raw.get("status")
    when_raw = raw.get("when")
    cond = raw.get("if")

    issues = []
    warnings = []

    unknown_keys = sorted(str(k) for k in raw.keys() if k not in ALLOWED_KEYS)
    if unknown_keys:
        warnings.append("unknown key(s): %s" % ", ".join(unknown_keys))

    missing = [k for k in ("what", "source", "owner", "status") if not raw.get(k)]
    if missing:
        issues.append("missing required key(s): %s" % ", ".join(missing))

    # non-string what/source/owner (present but the wrong type, e.g. a YAML list or number)
    # is BLOCKING -- schema finding, row skipped from classification. `owner` remains
    # free-form in content; this only checks that it IS a string.
    bad_types = [k for k in STRING_VALUED_KEYS
                 if raw.get(k) not in (None, "") and not isinstance(raw.get(k), str)]
    if bad_types:
        issues.append("non-string value for key(s): %s" % ", ".join(bad_types))

    has_when = raw.get("when") not in (None, "")
    has_if = raw.get("if") not in (None, "")
    if has_when and has_if:
        issues.append("both 'when' and 'if' present (exactly one required)")
    if not has_when and not has_if:
        issues.append("neither 'when' nor 'if' present (exactly one required)")

    if status is not None and status not in VALID_STATUSES:
        issues.append("invalid status %r (expected one of %s)"
                       % (status, ", ".join(sorted(VALID_STATUSES))))

    date_obj = approx = days = None
    if has_when and not has_if:
        parsed = parse_when_value(when_raw)
        if parsed is None:
            issues.append("unparseable 'when' date: %r (expected YYYY-MM-DD or YYYY-MM)"
                           % (when_raw,))
        else:
            date_obj, approx = parsed
            days = (date_obj - today).days

    row = {
        "index": idx, "what": what, "source": source, "owner": owner, "status": status,
        "when": (date_obj.isoformat() if date_obj else when_raw), "approx": approx,
        "if": cond, "days_remaining": days, "issues": issues, "warnings": warnings,
        "valid": not issues,
    }
    if issues:
        row["category"] = None
        return row

    if status == "watching":
        if has_if:
            row["category"] = "if-row"
        elif days < 0:
            row["category"] = "fired"
        elif days <= ATTENTION_WINDOW_DAYS:
            row["category"] = "attention"
        else:
            row["category"] = "watching"
    elif status == "fired":
        row["category"] = "recorded-fired"
    else:  # discharged
        row["category"] = "recorded-discharged"
    return row


# --------------------------------------------------------------------------- report assembly

def build_report(register_path, today=None):
    today = today or date.today()
    report = {"register_path": register_path, "today": today.isoformat()}

    rows_raw, problems, present = load_register(register_path)
    if not present:
        report["degraded"] = True
        report["note"] = ("no deadline register at %s -- nothing to report (degrade)"
                           % register_path)
        return report

    report["degraded"] = False
    report["top_level_problems"] = problems
    rows = [validate_and_classify_row(i, r, today) for i, r in enumerate(rows_raw)]
    report["rows"] = rows

    counts = {"total": len(rows), "schema_findings": 0, "attention": 0, "fired": 0,
              "watching": 0, "if_row": 0, "recorded_fired": 0, "recorded_discharged": 0}
    cat_key = {"attention": "attention", "fired": "fired", "watching": "watching",
               "if-row": "if_row", "recorded-fired": "recorded_fired",
               "recorded-discharged": "recorded_discharged"}
    for r in rows:
        # a row is a "schema finding" if it has any BLOCKING issue OR any non-blocking
        # warning (e.g. an unknown/extra key) -- both are reported, but only a blocking
        # issue excludes the row from classification below.
        if r["issues"] or r["warnings"]:
            counts["schema_findings"] += 1
        if not r["valid"]:
            continue
        counts[cat_key[r["category"]]] += 1
    report["counts"] = counts
    return report


# --------------------------------------------------------------------------- output

def _fmt_when(row):
    tag = " (approximate month)" if row.get("approx") else ""
    return "%s%s" % (row["when"], tag)


def _fmt_row(row):
    return "source: %s; owner: %s" % (row["source"], row["owner"])


def print_report(report, as_json=False):
    if as_json:
        print(json.dumps(report, indent=1, sort_keys=True, default=str))
        return

    if report.get("degraded"):
        print("check-deadlines: NOTE -- %s" % report["note"])
        return

    rows = report["rows"]
    counts = report["counts"]

    print("check-deadlines: %s (today=%s)" % (report["register_path"], report["today"]))
    print("  %d row(s) read: %d attention / %d fired / %d watching (not yet due) / "
          "%d watching-condition / %d recorded-fired / %d recorded-discharged / "
          "%d schema finding(s)"
          % (counts["total"], counts["attention"], counts["fired"], counts["watching"],
             counts["if_row"], counts["recorded_fired"], counts["recorded_discharged"],
             counts["schema_findings"]))
    if report["top_level_problems"]:
        print("  top-level problems:")
        for p in report["top_level_problems"]:
            print("    - %s" % p)

    attention = [r for r in rows if r["category"] == "attention"]
    fired = [r for r in rows if r["category"] == "fired"]
    watching = [r for r in rows if r["category"] == "watching"]
    if_rows = [r for r in rows if r["category"] == "if-row"]
    # schema findings = blocking-invalid rows (excluded above) PLUS otherwise-valid rows
    # that still carry a non-blocking warning (e.g. an unknown/extra key) -- those also
    # appear in one of the category lists above (report, don't drop).
    findings = [r for r in rows if not r["valid"] or r["warnings"]]

    print()
    print("  ATTENTION -- due within %d days (%d):" % (ATTENTION_WINDOW_DAYS, len(attention)))
    if attention:
        for r in sorted(attention, key=lambda r: r["days_remaining"]):
            print("    - %s -- %d day(s) remaining, due %s (%s)"
                  % (r["what"], r["days_remaining"], _fmt_when(r), _fmt_row(r)))
    else:
        print("    (none)")

    print()
    print("  FIRED -- overdue (%d):" % len(fired))
    if fired:
        for r in sorted(fired, key=lambda r: r["days_remaining"]):
            print("    - %s -- overdue by %d day(s), was due %s (%s)"
                  % (r["what"], -r["days_remaining"], _fmt_when(r), _fmt_row(r)))
    else:
        print("    (none)")

    print()
    print("  Watching (dated, not yet due) (%d):" % len(watching))
    if watching:
        for r in sorted(watching, key=lambda r: r["days_remaining"]):
            print("    - %s -- %d day(s) remaining, due %s (%s)"
                  % (r["what"], r["days_remaining"], _fmt_when(r), _fmt_row(r)))
    else:
        print("    (none)")

    print()
    print("  Watching-conditions -- if-rows; conditions do not self-evaluate (%d):"
          % len(if_rows))
    if if_rows:
        for r in if_rows:
            print("    - %s -- if: %s (%s)" % (r["what"], r["if"], _fmt_row(r)))
    else:
        print("    (none)")

    print()
    print("  Schema findings (%d):" % len(findings))
    if findings:
        for r in findings:
            all_msgs = list(r["issues"]) + list(r["warnings"])
            print("    - row %d (%s): %s"
                  % (r["index"], r["what"] or "(no 'what')", "; ".join(all_msgs)))
    else:
        print("    (none)")


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

    NOW = date(2026, 7, 23)  # fixed reference date -- deterministic regardless of wall clock

    def write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    # ---- Case A: absent register file -> single NOTE, degrade path, exit 0 -------------
    base_a = tempfile.mkdtemp(prefix="ckdl-absent-")
    try:
        rep = build_report(os.path.join(base_a, "no-such-file.yaml"), today=NOW)
        case("absent file: degraded flag set", rep.get("degraded") is True)
        case("absent file: note is present and plain",
             "nothing to report" in rep.get("note", ""), rep.get("note", ""))
        # exit-code smoke: main() with a nonexistent register must still return 0
        rc = main(["--register", os.path.join(base_a, "no-such-file.yaml"), "--today", "2026-07-23"])
        case("absent file: main() exits 0", rc == 0)
    finally:
        shutil.rmtree(base_a, ignore_errors=True)

    # ---- Case B: PyYAML-absent degrade path (simulated) --------------------------------
    global yaml
    saved_yaml = yaml
    base_b = tempfile.mkdtemp(prefix="ckdl-noyaml-")
    try:
        reg_b = os.path.join(base_b, "deadline-register.yaml")
        write(reg_b, "rows:\n- what: x\n  when: 2026-08-01\n  source: y\n  owner: operator\n  status: watching\n")
        yaml = None
        rows_b, problems_b, present_b = load_register(reg_b)
        case("PyYAML-absent: register reported present (file exists)", present_b is True)
        case("PyYAML-absent: rows treated as empty", rows_b == [])
        case("PyYAML-absent: a clear finding is produced",
             any("PyYAML not installed" in p for p in problems_b), problems_b)
    finally:
        yaml = saved_yaml
        shutil.rmtree(base_b, ignore_errors=True)

    if yaml is None:
        print("  -- remaining self-test sections SKIPPED (PyYAML not installed in this "
              "environment; the sensor itself still degrades cleanly per Case B above)")
        print("check-deadlines self-test: %s (%d/%d)"
              % ("PASS" if failed == 0 else "FAIL", total - failed, total))
        return 0 if failed == 0 else 1

    # ---- Case C: the full classification battery ---------------------------------------
    base_c = tempfile.mkdtemp(prefix="ckdl-full-")
    try:
        reg_c = os.path.join(base_c, "deadline-register.yaml")
        write(reg_c, "\n".join([
            "rows:",
            "  # future-far: 90 days out, well past the attention window",
            "  - what: far-future watching row",
            "    when: 2026-10-21",
            "    source: test/fixture.md",
            "    owner: operator",
            "    status: watching",
            "",
            "  # future-near: 10 days out, inside the 30-day attention window",
            "  - what: near-future watching row",
            "    when: 2026-08-02",
            "    source: test/fixture.md",
            "    owner: operator",
            "    status: watching",
            "",
            "  # past: overdue by 5 days",
            "  - what: overdue watching row",
            "    when: 2026-07-18",
            "    source: test/fixture.md",
            "    owner: operator",
            "    status: watching",
            "",
            "  # approximate month",
            "  - what: approximate-month watching row",
            "    when: 2027-01",
            "    source: test/fixture.md",
            "    owner: operator",
            "    status: watching",
            "",
            "  # if-row: a condition, not a date",
            "  - what: conditional re-open trigger",
            "    if: some condition text that does not self-evaluate",
            "    source: test/fixture.md",
            "    owner: session",
            "    status: watching",
            "",
            "  # discharged: dated but already recorded closed -- summary only",
            "  - what: discharged row",
            "    when: 2020-01-01",
            "    source: test/fixture.md",
            "    owner: operator",
            "    status: discharged",
            "",
            "  # fired: already recorded fired -- summary only, not re-detailed",
            "  - what: recorded-fired row",
            "    when: 2020-01-01",
            "    source: test/fixture.md",
            "    owner: operator",
            "    status: fired",
            "",
            "  # malformed: missing required keys",
            "  - what: missing keys row",
            "    when: 2026-09-01",
            "",
            "  # malformed: both when and if present",
            "  - what: both-when-and-if row",
            "    when: 2026-09-01",
            "    if: some condition",
            "    source: test/fixture.md",
            "    owner: operator",
            "    status: watching",
            "",
            "  # malformed: neither when nor if present",
            "  - what: neither-when-nor-if row",
            "    source: test/fixture.md",
            "    owner: operator",
            "    status: watching",
            "",
            "  # malformed: bad status",
            "  - what: bad-status row",
            "    when: 2026-09-01",
            "    source: test/fixture.md",
            "    owner: operator",
            "    status: not-a-real-status",
            "",
            "  # malformed: unparseable date",
            "  - what: unparseable-date row",
            "    when: not-a-date",
            "    source: test/fixture.md",
            "    owner: operator",
            "    status: watching",
            "",
            "  # schema: unknown/extra key present -- non-blocking, still classified normally",
            "  - what: extra-key row",
            "    when: 2026-11-01",
            "    source: test/fixture.md",
            "    owner: operator",
            "    status: watching",
            "    priority: urgent",
            "",
            "  # schema: non-string owner (a YAML list, not a string) -- blocking",
            "  - what: non-string-owner row",
            "    when: 2026-09-01",
            "    source: test/fixture.md",
            "    owner: [operator, session]",
            "    status: watching",
            "",
            "  # schema: non-string what (a bare number, not a string) -- blocking",
            "  - what: 424242",
            "    when: 2026-09-01",
            "    source: test/fixture.md",
            "    owner: operator",
            "    status: watching",
            "",
        ]))

        rep = build_report(reg_c, today=NOW)
        case("full battery: not degraded", rep.get("degraded") is False)
        rows = rep["rows"]
        by_what = {r["what"]: r for r in rows if r.get("what")}

        r = by_what["far-future watching row"]
        case("future-far: category=watching, days>30",
             r["category"] == "watching" and r["days_remaining"] > 30, r)

        r = by_what["near-future watching row"]
        case("future-near: category=attention, 0<=days<=30",
             r["category"] == "attention" and 0 <= r["days_remaining"] <= 30, r)

        r = by_what["overdue watching row"]
        case("past: category=fired (computed), days<0",
             r["category"] == "fired" and r["days_remaining"] < 0, r)
        case("past: overdue-by matches (5 days)", -r["days_remaining"] == 5, r)

        r = by_what["approximate-month watching row"]
        case("approximate month: parsed as first-of-month + flagged approximate",
             r["approx"] is True and r["when"] == "2027-01-01", r)

        r = by_what["conditional re-open trigger"]
        case("if-row: category=if-row, condition text preserved",
             r["category"] == "if-row" and r["if"] == "some condition text that does not self-evaluate", r)

        r = by_what["discharged row"]
        case("discharged: counted-only category, not re-classified as fired",
             r["category"] == "recorded-discharged", r)

        r = by_what["recorded-fired row"]
        case("recorded-fired: counted-only category, not re-detailed as computed-fired",
             r["category"] == "recorded-fired", r)

        r = by_what["missing keys row"]
        case("malformed (missing keys): invalid, issue names the missing keys",
             not r["valid"] and any("missing required key" in i for i in r["issues"]), r)

        r = by_what["both-when-and-if row"]
        case("malformed (both when+if): invalid, issue flags both-present",
             not r["valid"] and any("both" in i for i in r["issues"]), r)

        r = by_what["neither-when-nor-if row"]
        case("malformed (neither when nor if): invalid, issue flags neither-present",
             not r["valid"] and any("neither" in i for i in r["issues"]), r)

        r = by_what["bad-status row"]
        case("malformed (bad status): invalid, issue names invalid status",
             not r["valid"] and any("invalid status" in i for i in r["issues"]), r)

        r = by_what["unparseable-date row"]
        case("malformed (unparseable date): invalid, issue flags unparseable date",
             not r["valid"] and any("unparseable" in i for i in r["issues"]), r)

        r = by_what["extra-key row"]
        case("extra-key: still valid and classified (watching) despite the extra key",
             r["valid"] is True and r["category"] == "watching", r)
        case("extra-key: reported as a (non-blocking) schema finding naming the key",
             bool(r["warnings"]) and any("priority" in w for w in r["warnings"]), r)

        r = by_what["non-string-owner row"]
        case("non-string owner (a YAML list): invalid, issue names 'owner', skipped from classification",
             not r["valid"] and r["category"] is None
             and any("owner" in i for i in r["issues"]), r)

        r = by_what[424242]
        case("non-string what (a bare number): invalid, issue names 'what', skipped from classification",
             not r["valid"] and r["category"] is None
             and any("what" in i for i in r["issues"]), r)

        counts = rep["counts"]
        case("counts: total rows matches file (15)", counts["total"] == 15, counts)
        case("counts: schema_findings matches 5 malformed + 1 extra-key-warning + "
             "2 non-string-type rows (8)",
             counts["schema_findings"] == 8, counts)
        case("counts: attention=1, fired=1, watching=3 (far-future + approximate-month + "
             "extra-key), if_row=1, recorded_fired=1, recorded_discharged=1",
             (counts["attention"], counts["fired"], counts["watching"], counts["if_row"],
              counts["recorded_fired"], counts["recorded_discharged"]) == (1, 1, 3, 1, 1, 1),
             counts)

        # no crash on either output path
        try:
            print_report(rep, as_json=True)
            json_ok = True
        except Exception as e:  # noqa: BLE001
            json_ok = False
            print("    JSON print raised: %s" % e)
        case("json output serializes cleanly", json_ok)

        try:
            print_report(rep, as_json=False)
            text_ok = True
        except Exception as e:  # noqa: BLE001
            text_ok = False
            print("    text print raised: %s" % e)
        case("plain-English output prints cleanly", text_ok)

        # CLI wiring smoke test: --register/--today/--json, exit code always 0
        rc = main(["--register", reg_c, "--today", "2026-07-23", "--json"])
        case("CLI: --register/--today/--json wiring returns exit 0", rc == 0)
        rc2 = main(["--register", reg_c, "--today", "2026-07-23"])
        case("CLI: plain-text invocation returns exit 0", rc2 == 0)

    finally:
        shutil.rmtree(base_c, ignore_errors=True)

    # ---- Case D: a row that is not a mapping at all (e.g. a stray scalar in the list) --
    base_d = tempfile.mkdtemp(prefix="ckdl-notmap-")
    try:
        reg_d = os.path.join(base_d, "deadline-register.yaml")
        write(reg_d, "rows:\n- just a string, not a mapping\n")
        rep_d = build_report(reg_d, today=NOW)
        case("non-mapping row: reported as a schema finding, not a crash",
             rep_d["counts"]["schema_findings"] == 1 and rep_d["counts"]["total"] == 1, rep_d)
    finally:
        shutil.rmtree(base_d, ignore_errors=True)

    # ---- Case E: malformed top-level structure (rows: not a list) ----------------------
    base_e = tempfile.mkdtemp(prefix="ckdl-badtop-")
    try:
        reg_e = os.path.join(base_e, "deadline-register.yaml")
        write(reg_e, "rows: not-a-list\n")
        rep_e = build_report(reg_e, today=NOW)
        case("malformed top-level: not degraded (still produces a report)",
             rep_e.get("degraded") is False)
        case("malformed top-level: produced a top-level problem",
             len(rep_e["top_level_problems"]) >= 1, rep_e["top_level_problems"])
        case("malformed top-level: zero rows, not a crash", rep_e["counts"]["total"] == 0)
    finally:
        shutil.rmtree(base_e, ignore_errors=True)

    print("check-deadlines self-test: %s (%d/%d)"
          % ("PASS" if failed == 0 else "FAIL", total - failed, total))
    return 0 if failed == 0 else 1


# --------------------------------------------------------------------------- CLI

def main(argv):
    args = argv[1:] if argv and argv[0].endswith(".py") else list(argv)
    if "--self-test" in args:
        return self_test()

    as_json = "--json" in args
    register_path = _DEFAULT_REGISTER
    if "--register" in args:
        i = args.index("--register")
        if i + 1 < len(args):
            register_path = args[i + 1]

    today = date.today()
    if "--today" in args:
        i = args.index("--today")
        if i + 1 < len(args):
            try:
                today = datetime.strptime(args[i + 1], "%Y-%m-%d").date()
            except ValueError:
                print("check-deadlines: NOTE -- unparseable --today value %r, using wall-clock "
                      "date instead" % args[i + 1])

    report = build_report(register_path, today=today)
    print_report(report, as_json=as_json)
    return 0  # report-only: always 0 on a completed report (D4)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
