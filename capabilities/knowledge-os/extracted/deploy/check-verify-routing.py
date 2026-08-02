#!/usr/bin/env python3
"""check-verify-routing.py -- verify-leg tier-promotion sensor (backlog v3.0-77).

Reads deploy/verify-routing-register.yaml (declared tier anchors: one exact source
substring per verify-leg routing surface) and asserts each marker is still present in its
file. The register is the decision record; this sensor only compares. Report-only,
stateless, stdlib + optional PyYAML (absent => SKIP, degrade-never-block, same guard as
check-triggers.py).

Why marker presence and not parsing: the 2026-07-29 incident's promotion was source edits
to the routing surfaces (vendor-pinning the verifier around an unchanged policy). Full
cross-language routing-semantics parsing is brittleness masquerading as rigor (the
register's own design note); the anchors are the tier DECLARATIONS themselves, so the
promotion class cannot land without tripping a row -- and a legitimate refactor updates
the row in the same commit, which is exactly the visibility the sensor exists to force.

Findings:
  BLOCKING  marker absent from its file (tier declaration edited/moved), file missing,
            or register schema invalid (missing key, duplicate id, unparseable)
  OK        marker present

Usage: check-verify-routing.py [--root DIR] | --self-test
Exit: 0 all present (or register absent-by-design / PyYAML absent => SKIP) | 1 any
BLOCKING finding | 2 usage.
"""

import argparse
import os
import sys
import tempfile

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

REGISTER = os.path.join("deploy", "verify-routing-register.yaml")
REQUIRED_KEYS = ("id", "file", "marker", "means", "decision-ref")


def load_register(root):
    """(rows, problems). Schema violations are BLOCKING (fail-loud, like
    check-triggers.py): a rotting register row must never silently skip."""
    path = os.path.join(root, REGISTER)
    if not os.path.isfile(path):
        return None, []
    try:
        data = yaml.safe_load(open(path, encoding="utf-8"))
    except yaml.YAMLError as e:
        return [], ["register unparseable: %s" % str(e).splitlines()[0]]
    rows = (data or {}).get("rows")
    if not isinstance(rows, list):
        return [], ["register has no rows: list"]
    problems = []
    seen = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            problems.append("row %d is not a mapping" % i)
            continue
        for k in REQUIRED_KEYS:
            if not str(row.get(k, "") or "").strip():
                problems.append("row %d (%s): missing key %r"
                                % (i, row.get("id", "?"), k))
        rid = str(row.get("id", ""))
        if rid in seen:
            problems.append("duplicate id: %s" % rid)
        seen.add(rid)
    return rows, problems


def evaluate(root):
    """(findings, ok_count, skipped_reason). findings are BLOCKING strings."""
    if yaml is None:
        return [], 0, "PyYAML absent -- register unreadable; routing unwatched"
    rows, problems = load_register(root)
    if rows is None:
        return [], 0, "no %s (register not adopted)" % REGISTER.replace(os.sep, "/")
    findings = ["SCHEMA: %s" % p for p in problems]
    ok = 0
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("marker", "") or "").strip():
            continue    # already a schema finding above
        rel = str(row["file"]).replace("/", os.sep)
        fpath = os.path.join(root, rel)
        rid = row.get("id", "?")
        if not os.path.isfile(fpath):
            findings.append("%s: file missing: %s" % (rid, row["file"]))
            continue
        try:
            text = open(fpath, encoding="utf-8", errors="ignore").read()
        except OSError as e:
            findings.append("%s: file unreadable: %s (%s)" % (rid, row["file"], e))
            continue
        if str(row["marker"]) not in text:
            findings.append(
                "%s: TIER ANCHOR ABSENT -- %r not found in %s. Either the tier "
                "declaration was edited (promotion/de-promotion class: revert it) "
                "or a refactor moved it (update the register row in the same "
                "commit, citing the authorizing decision: %s). Raising a leg's "
                "tier is a decision, not a fix (v3.0-76/77)."
                % (rid, row["marker"], row["file"], row.get("decision-ref", "?")))
        else:
            ok += 1
    return findings, ok, None


def self_test():
    failed = total = 0

    def check(label, cond):
        nonlocal failed, total
        total += 1
        print("  %s %s" % ("ok " if cond else "XX ", label))
        if not cond:
            failed += 1

    if yaml is None:  # pragma: no cover
        print("check-verify-routing --self-test: SKIP (PyYAML absent)")
        return 0

    def write_register(root, body):
        d = os.path.join(root, "deploy")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "verify-routing-register.yaml"), "w",
                  encoding="utf-8") as fh:
            fh.write(body)

    ROW = ('rows:\n  - id: r1\n    file: deploy/target.py\n'
           '    marker: "gate_kind=\\"routine\\""\n    means: m\n'
           '    decision-ref: v0\n')

    with tempfile.TemporaryDirectory() as td:
        f, ok, skip = evaluate(td)
        check("register absent -> SKIP, no findings", skip is not None and not f)

        write_register(td, ROW)
        with open(os.path.join(td, "deploy", "target.py"), "w",
                  encoding="utf-8") as fh:
            fh.write('dispatch(gate_kind="routine")\n')
        f, ok, skip = evaluate(td)
        check("marker present -> OK, no findings", not f and ok == 1 and skip is None)

        with open(os.path.join(td, "deploy", "target.py"), "w",
                  encoding="utf-8") as fh:
            fh.write('dispatch(gate_kind="design")\n')   # the promotion edit
        f, ok, skip = evaluate(td)
        check("tier declaration edited -> BLOCKING naming the row + decision-ref",
              len(f) == 1 and "TIER ANCHOR ABSENT" in f[0] and "v0" in f[0])

        os.remove(os.path.join(td, "deploy", "target.py"))
        f, ok, skip = evaluate(td)
        check("anchored file missing -> BLOCKING", len(f) == 1 and "file missing" in f[0])

        write_register(td, "rows:\n  - id: r1\n    file: deploy/target.py\n")
        f, ok, skip = evaluate(td)
        check("schema violation (missing keys) -> BLOCKING",
              any("SCHEMA" in x for x in f))

        write_register(td, ROW + ROW.replace("rows:\n", ""))
        f, ok, skip = evaluate(td)
        check("duplicate id -> BLOCKING", any("duplicate id" in x for x in f))

    print("check-verify-routing self-test: %s (%d/%d)"
          % ("FAIL" if failed else "PASS", total - failed, total))
    return 1 if failed else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="check-verify-routing.py")
    ap.add_argument("--root", default=".")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    findings, ok, skip = evaluate(os.path.abspath(args.root))
    if skip is not None:
        print("check-verify-routing: SKIP -- %s" % skip)
        return 0
    for f in findings:
        print("  BLOCKING  %s" % f)
    print("check-verify-routing: %s (%d anchor(s) present, %d finding(s))"
          % ("FAIL" if findings else "PASS", ok, len(findings)))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
