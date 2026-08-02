#!/usr/bin/env python3
"""check-eco2.py -- ECO-2 schema_version skew guard, both directions (test-plan tp:430).

The one hard refusal in the derivation contract: a block whose `schema_version` != the sensor's
SCHEMA_VERSION is REFUSED (fail-closed), whether the block is OLDER (a stale view the engine
moved past) or NEWER (a view written by a future engine this one must not silently accept). This
gate proves both directions refuse and the matching version passes, by driving the shipped
`check-frontmatter.py` sensor as a library (single source of truth -- ECO-2 never re-implements
the rule).

Usage:
  check-eco2.py --self-test
Exit codes: 0 = both skew directions REFUSE + matching passes | 1 = a direction slipped through
  | 2 = INCONCLUSIVE (sensor unloadable).
"""

import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sensor():
    path = os.path.join(_HERE, "check-frontmatter.py")
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location("check_frontmatter", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _refuses(sensor, text):
    return any(getattr(f, "level", None) == "REFUSE" for f in sensor.check_text(text, "wiki/x.md"))


def self_test():
    sensor = _load_sensor()
    if sensor is None:
        print("check-eco2 (ECO-2): INCONCLUSIVE -- check-frontmatter.py unloadable")
        return 2
    cur = sensor.SCHEMA_VERSION
    valid = sensor._VALID_DERIV
    older = valid.replace("schema_version: %s" % cur, "schema_version: 3.1")
    newer = valid.replace("schema_version: %s" % cur, "schema_version: 3.3")
    failed = 0
    cases = [
        ("matching version passes (no REFUSE)", not _refuses(sensor, valid)),
        ("OLDER skew (3.1) is REFUSED", _refuses(sensor, older)),
        ("NEWER skew (3.3) is REFUSED", _refuses(sensor, newer)),
    ]
    for name, ok in cases:
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1
    if failed:
        print("check-eco2 (ECO-2): FAIL (%d/%d)" % (len(cases) - failed, len(cases)))
        return 1
    print("check-eco2 (ECO-2): PASS (%d/%d; skew refused both directions vs %s)"
          % (len(cases), len(cases), cur))
    return 0


def main(argv):
    if "--self-test" in argv[1:] or len(argv) == 1:
        return self_test()
    print("usage: check-eco2.py --self-test")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
