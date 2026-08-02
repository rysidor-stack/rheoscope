#!/usr/bin/env python3
"""check-caps.py -- ECO-3 byte-cap sensor (memory-engine v3, P0).

Caps are the first thing the engine builds: an over-cap view is the split
trigger (CONTENT-2). The one property that makes a byte cap trustworthy on this
repo is **checkout-invariance** -- the verdict must not change between the CRLF
working tree and the LF index/VPS checkout. So caps are measured on the
LF-NORMALIZED UTF-8 byte length (read binary, replace b"\\r\\n" with b"\\n",
len), NEVER os.path.getsize. On a 500-line T1 view the CRLF/LF delta is ~+3,400
bytes -- enough to flip a boundary verdict (test-plan ECO-3).

Cap values per view type are read from deploy/engine-caps.yaml (or entities.yaml
once caps migrate there) -- the sensor NEVER hardcodes them, so the self-test is
parametric over whatever cap table is configured.

Usage:
  check-caps.py [PATH ...]      report over-cap views (default: wiki/); degrade (exit 0)
  check-caps.py --strict ...    exit 1 if any view is over cap
  check-caps.py --self-test     CRLF-invariance battery (the ECO-3 gate)

Exit codes: 0 = clean / over-cap reported (degrade) | 1 = over-cap under --strict,
or a self-test failure | 2 = inconclusive (cap config unreadable).

view-type detection reads the derivation block's `view:` key (added at P1);
pre-derivation views resolve to the `default` cap.
"""

import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_CONFIG = os.path.join(_HERE, "engine-caps.yaml")

_VIEW_RE = re.compile(r"^\s*view:\s*([A-Za-z0-9_-]+)", re.MULTILINE)
DERIV_START = "# --- derivation"
DERIV_END = "# --- /derivation"


def lf_byte_len(path):
    """LF-normalized UTF-8 byte length of a file -- the checkout-invariant size.
    Reads binary and normalizes CRLF->LF before measuring (NEVER os.path.getsize)."""
    with open(path, "rb") as fh:
        data = fh.read()
    return len(data.replace(b"\r\n", b"\n"))


def lf_byte_len_bytes(data):
    """LF-normalized byte length of an in-memory bytes object (self-test helper)."""
    return len(data.replace(b"\r\n", b"\n"))


def load_caps(config_path=_DEFAULT_CONFIG):
    """Return the {view_type: cap_bytes} mapping. Raises if unreadable."""
    if yaml is None:
        raise RuntimeError("PyYAML not available; cannot read cap config")
    with open(config_path, "r", encoding="utf-8-sig") as fh:
        doc = yaml.safe_load(fh) or {}
    caps = doc.get("caps") or {}
    if "default" not in caps:
        raise RuntimeError("cap config has no 'default' cap")
    return {k: int(v) for k, v in caps.items()}


def view_type_of(text):
    """The derivation block's `view:` value, or None (pre-derivation view)."""
    lines = text.splitlines()
    start = end = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if start is None and s.startswith(DERIV_START):
            start = i
        elif start is not None and s.startswith(DERIV_END):
            end = i
            break
    if start is None or end is None:
        return None
    block = "\n".join(lines[start + 1:end])
    m = _VIEW_RE.search(block)
    return m.group(1) if m else None


def cap_for(caps, view_type):
    return caps.get(view_type or "default", caps["default"])


def verdict(path, caps):
    """(over_cap, lf_bytes, cap, view_type) for one view file."""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            text = fh.read()
        vt = view_type_of(text)
    except (OSError, UnicodeDecodeError):
        vt = None
    n = lf_byte_len(path)
    cap = cap_for(caps, vt)
    return (n > cap, n, cap, vt)


def _iter_md(paths):
    for p in paths:
        if os.path.isfile(p) and p.endswith(".md"):
            yield p
        elif os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                if os.sep + ".git" in root:
                    continue
                for f in files:
                    if f.endswith(".md"):
                        yield os.path.join(root, f)


def scan(paths, strict=False):
    try:
        caps = load_caps()
    except (OSError, RuntimeError) as e:
        print("RESULT: INCONCLUSIVE -- cap config unreadable: %s" % e)
        return 2
    over = []
    n_scanned = 0
    for fp in _iter_md(paths):
        n_scanned += 1
        is_over, n, cap, vt = verdict(fp, caps)
        if is_over:
            over.append((fp, n, cap, vt))
    for fp, n, cap, vt in sorted(over, key=lambda r: -r[1]):
        print("  OVER-CAP %s  (%d LF-bytes > %d cap, view=%s)" % (fp, n, cap, vt or "default"))
    if over:
        print("RESULT: %s -- %d/%d view(s) over cap (split candidates)"
              % ("FAIL" if strict else "PASS(degrade)", len(over), n_scanned))
        return 1 if strict else 0
    print("RESULT: PASS -- %d view(s) scanned, 0 over cap" % n_scanned)
    return 0


# ---------------------------------------------------------------------------
# Self-test: CRLF-invariance at the boundary, parametric over the cap table.
# ---------------------------------------------------------------------------

def _blob_of_lf_size(n):
    """A bytes object whose LF-normalized length is exactly n, containing '\\n's
    (so its CRLF twin has strictly more raw bytes but the same LF length)."""
    # one '\n' every 40 chars, padded with 'x'
    out = bytearray()
    while len(out) < n:
        out.append(0x0A if (len(out) % 40 == 39) else ord("x"))
    return bytes(out[:n])


def self_test():
    try:
        caps = load_caps()
    except (OSError, RuntimeError) as e:
        print("RESULT: INCONCLUSIVE -- cap config unreadable: %s" % e)
        return 2

    failed = 0
    total = 0

    def case(name, ok):
        nonlocal failed, total
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    for vt, cap in sorted(caps.items()):
        # at exactly cap: not over. cap+1: over. Each tested LF and as its CRLF twin.
        at_lf = _blob_of_lf_size(cap)
        at_crlf = at_lf.replace(b"\n", b"\r\n")
        over_lf = _blob_of_lf_size(cap + 1)
        over_crlf = over_lf.replace(b"\n", b"\r\n")

        # CRLF twin must measure identically (the core invariance property)
        case("%s: LF/CRLF length equal @cap" % vt,
             lf_byte_len_bytes(at_lf) == lf_byte_len_bytes(at_crlf) == cap)
        case("%s: LF/CRLF length equal @cap+1" % vt,
             lf_byte_len_bytes(over_lf) == lf_byte_len_bytes(over_crlf) == cap + 1)
        # verdict equality across CRLF/LF at both sides of the boundary
        case("%s: verdict equal @cap (both not-over)" % vt,
             (lf_byte_len_bytes(at_lf) > cap) == (lf_byte_len_bytes(at_crlf) > cap) == False)
        case("%s: verdict equal @cap+1 (both over)" % vt,
             (lf_byte_len_bytes(over_lf) > cap) == (lf_byte_len_bytes(over_crlf) > cap) == True)
        # the trap this guards: raw byte length DOES differ (proving normalization matters)
        case("%s: raw CRLF length differs (normalization is load-bearing)" % vt,
             len(at_crlf) > len(at_lf))

    if failed:
        print("check-caps self-test: FAIL (%d/%d)" % (failed, total))
        return 1
    print("check-caps self-test: PASS (%d/%d)" % (total, total))
    return 0


def main(argv):
    args = argv[1:]
    if "--self-test" in args:
        return self_test()
    strict = "--strict" in args
    paths = [a for a in args if not a.startswith("--")]
    if not paths:
        paths = ["wiki"] if os.path.isdir("wiki") else []
        if not paths:
            print("check-caps: no wiki/ present; nothing to scan.")
            return 0
    return scan(paths, strict=strict)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
