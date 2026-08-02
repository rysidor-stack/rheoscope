#!/usr/bin/env python3
"""routing-census.py -- LLM-6's F15 primitive: a STANDALONE reproducible
routing census script, deliberately independent of compile-v2 / the absorb
matcher whose drops it is meant to audit (spec sec.7: "the census must not
be self-graded by the matcher whose drops it audits").

Ground truth for "which views does this event route to": each VIEW's own
frontmatter `sources:` list (view cites its raw events -- see e.g.
wiki/<domain>/api-endpoints.md's `sources:` block). This script inverts that
citation graph: event -> [views that cite it], over a caller-supplied
LEDGER SLICE (an explicit list of raw/ event paths, or a manifest file
listing them -- never "all of raw/", so a census run is always scoped to a
declared slice, matching the OPS-4 bench's plan-scoped model).

Inputs (both hashed, both part of the journaled/printed record):
  - ledger slice: sorted list of raw event paths, resolved either from
    --events (repeated) or a --manifest JSON file ({"events": [rel...]}).
  - entities.yaml blob SHA (git blob hash of deploy/entities.yaml at
    --root's current HEAD, via `git rev-parse HEAD:deploy/entities.yaml`;
    falls back to hashing the working-tree file's bytes with sha256 if the
    path isn't committed yet, clearly labeled so the two hash spaces are
    never confused).
  Both are combined into one "input_manifest" dict; its OWN canonical
  serialization is what gets sha256'd for the printed/returned
  input_manifest_sha256 -- so the hash is a hash of exactly the bytes any
  other invocation with the same logical inputs would also produce.

Output: {"event": [view, ...]} deterministic map, one entry per event in
the ledger slice (an event with zero citers still gets an empty list -- so
a matched-but-dropped event is visible in the census, not absent from it;
this is the "boundary" property spec sec.7 VERIFY checks against:
"no matched-but-omitted event in the census"). Canonical serialization:
json.dumps(..., sort_keys=True, separators=(",", ":")) -- no whitespace
variance, no timestamps anywhere in the hashed payload. output_sha256 is
sha256 of that exact byte string.

Determinism contract (asserted in --self-test by running the identical
inputs twice and diffing both hashes): same ledger slice + same
entities.yaml SHA => byte-identical output and byte-identical
output_sha256, on every invocation, forever (no wall-clock, no PYTHONHASHSEED
sensitivity -- dict iteration is irrelevant since we sort_keys at
serialization, and set/list construction below is always over
sorted() sequences, never raw dict/set iteration order).

This script does NOT read the compile-v2 journal, does NOT consult
absorbed[]/corpus_support, and does NOT judge whether the routing was
"correct" -- it mechanically recomputes citer-derived ground truth from
view frontmatter alone, so VERIFY/CI can diff its output hash against a
journaled census hash and catch tampering or a divergent code path (F15).
Wiring its hashes into verify_run's journal is a LATER integration --
explicitly out of scope here (see LLM-6 build note).

Usage:
  routing-census.py --root DIR --events raw/a.md raw/b.md ...
  routing-census.py --root DIR --manifest ledger-slice.json
  routing-census.py --self-test
Exit: 0 always on a normal census run (this is a reporting primitive, not a
  pass/fail gate); 2 on malformed input (bad manifest / no events given).
"""

import hashlib
import json
import os
import subprocess
import sys

WIKI_DIRS = ("wiki",)


# ------------------------------------------------------------------ git shell
def _git(repo, *args):
    p = subprocess.run(["git", "-C", repo] + list(args),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p.returncode, p.stdout, p.stderr


def _sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def _sha256_text(s):
    return _sha256_bytes(s.encode("utf-8"))


# ------------------------------------------------------------------ frontmatter
def _strip_bom(text):
    if text.startswith("﻿"):
        return text[1:]
    return text


def _extract_frontmatter(text):
    text = _strip_bom(text)
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i]
    return None


def _extract_sources(region_lines):
    """Parse a `sources:\n  - path\n  - path` block (indent-2 list items)
    out of a frontmatter region. Minimal, dependency-free -- no PyYAML on
    the runtime path (matches this repo's check-frontmatter.py convention).
    Returns a list of raw paths as written (forward slashes expected)."""
    if region_lines is None:
        return []
    out = []
    in_sources = False
    for ln in region_lines:
        stripped = ln.strip()
        if not stripped:
            continue
        indent = len(ln) - len(ln.lstrip(" "))
        if indent == 0:
            in_sources = stripped.rstrip() == "sources:"
            continue
        if in_sources and stripped.startswith("- "):
            out.append(stripped[2:].strip())
    return out


def _walk_view_files(root):
    for wdir in WIKI_DIRS:
        base = os.path.join(root, wdir)
        if not os.path.isdir(base):
            continue
        for dirpath, _dn, filenames in os.walk(base):
            for fn in filenames:
                if fn.endswith(".md"):
                    ap = os.path.join(dirpath, fn)
                    rel = os.path.relpath(ap, root).replace(os.sep, "/")
                    yield rel, ap


def build_citer_index(root):
    """view_rel -> [event_rel, ...] (view's OWN sources: list), scanned once
    over every wiki/**/*.md. Read-only."""
    index = {}
    for view_rel, ap in _walk_view_files(root):
        try:
            text = open(ap, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        fm = _extract_frontmatter(text)
        srcs = _extract_sources(fm)
        if srcs:
            index[view_rel] = srcs
    return index


# ------------------------------------------------------------------ census
def _entities_blob_sha(root):
    """Prefer the committed git blob sha1 of deploy/entities.yaml at HEAD
    (a real git object id); fall back to a content sha256 of the working
    file if it's uncommitted/untracked, clearly tagged so the two hash
    spaces (git blob sha1 vs content sha256) are never conflated downstream."""
    rc, out, _err = _git(root, "rev-parse", "HEAD:deploy/entities.yaml")
    if rc == 0 and out.strip():
        return {"kind": "git-blob-sha1", "value": out.strip()}
    path = os.path.join(root, "deploy", "entities.yaml")
    if os.path.isfile(path):
        return {"kind": "content-sha256",
                "value": _sha256_bytes(open(path, "rb").read())}
    return {"kind": "absent", "value": ""}


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def compute_census(root, events):
    """events: iterable of raw event rel-paths (the ledger slice). Returns
    (input_manifest, input_manifest_sha256, output, output_sha256)."""
    events_sorted = sorted(set(events))
    citer_index = build_citer_index(root)
    # invert: event -> [views citing it], sorted, deduped
    inverted = {ev: [] for ev in events_sorted}
    for view_rel, srcs in citer_index.items():
        for ev in srcs:
            if ev in inverted:
                inverted[ev].append(view_rel)
    output = {ev: sorted(set(views)) for ev, views in inverted.items()}
    output_canon = _canon(output)
    output_sha256 = _sha256_text(output_canon)

    ent_sha = _entities_blob_sha(root)
    input_manifest = {"ledger_slice": events_sorted,
                      "entities_yaml_blob_sha": ent_sha}
    input_canon = _canon(input_manifest)
    input_manifest_sha256 = _sha256_text(input_canon)
    return input_manifest, input_manifest_sha256, output, output_sha256


# ------------------------------------------------------------------ self-test
def self_test():
    import shutil
    import tempfile
    total = failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    base = tempfile.mkdtemp(prefix="rcensus-")
    try:
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", base] + args, capture_output=True)
        os.makedirs(os.path.join(base, "raw"))
        os.makedirs(os.path.join(base, "wiki", "systems"))
        os.makedirs(os.path.join(base, "deploy"))

        def write(path, text):
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)

        write(os.path.join(base, "raw", "e1.md"), "event one body\n")
        write(os.path.join(base, "raw", "e2.md"), "event two body\n")
        write(os.path.join(base, "raw", "e3.md"), "event three, uncited\n")
        write(os.path.join(base, "wiki", "systems", "va.md"),
             "---\ntitle: A\nsources:\n  - raw/e1.md\n  - raw/e2.md\n"
             "---\nbody\n")
        write(os.path.join(base, "wiki", "systems", "vb.md"),
             "---\ntitle: B\nsources:\n  - raw/e2.md\n---\nbody\n")
        write(os.path.join(base, "deploy", "entities.yaml"),
             "entities:\n  foo:\n    views: []\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "seed"],
                       capture_output=True)

        events = ["raw/e1.md", "raw/e2.md", "raw/e3.md"]
        im1, ims1, out1, os1 = compute_census(base, events)
        case("e1 -> [va.md]", out1["raw/e1.md"] == ["wiki/systems/va.md"])
        case("e2 -> [va.md, vb.md] sorted",
            out1["raw/e2.md"] == ["wiki/systems/va.md", "wiki/systems/vb.md"])
        case("e3 (uncited) present with empty list, not omitted",
            "raw/e3.md" in out1 and out1["raw/e3.md"] == [])
        case("entities.yaml resolved as git-blob-sha1",
            im1["entities_yaml_blob_sha"]["kind"] == "git-blob-sha1")

        # determinism: same inputs run twice -> byte-identical hashes
        im2, ims2, out2, os2 = compute_census(base, events)
        case("output byte-identical across two runs", out1 == out2)
        case("output_sha256 identical across two runs", os1 == os2)
        case("input_manifest_sha256 identical across two runs", ims1 == ims2)
        case("no timestamp field leaked into hashed input_manifest",
            "created" not in im1 and "time" not in _canon(im1).lower())

        # order-independence of the events argument (a list, set, or
        # reversed order must hash identically since ledger_slice is
        # sorted before hashing)
        im3, ims3, out3, os3 = compute_census(
            base, list(reversed(events)) + ["raw/e2.md"])  # dup + reverse
        case("dup + reversed event order -> identical output_sha256",
            os3 == os1)
        case("dup + reversed event order -> identical manifest sha",
            ims3 == ims1)

        # a moved/renamed event still gets censused correctly: rename
        # raw/e1.md -> raw/e1-renamed.md, update the citing view's
        # sources: list, and re-run over the NEW event path
        subprocess.run(["git", "-C", base, "mv", "raw/e1.md",
                       "raw/e1-renamed.md"], capture_output=True)
        write(os.path.join(base, "wiki", "systems", "va.md"),
             "---\ntitle: A\nsources:\n  - raw/e1-renamed.md\n  - raw/e2.md\n"
             "---\nbody\n")
        subprocess.run(["git", "-C", base, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", base, "commit", "-qm", "rename e1"],
                       capture_output=True)
        im4, ims4, out4, os4 = compute_census(
            base, ["raw/e1-renamed.md", "raw/e2.md"])
        case("renamed event still censused to its citing view",
            out4["raw/e1-renamed.md"] == ["wiki/systems/va.md"])

        # malformed manifest handling exercised via CLI path
        bad_manifest = os.path.join(base, "bad-manifest.json")
        write(bad_manifest, "not json")
        rc = main(["--root", base, "--manifest", bad_manifest])
        case("malformed manifest -> exit 2", rc == 2)

        no_events_rc = main(["--root", base])
        case("no events given -> exit 2", no_events_rc == 2)

        good_manifest = os.path.join(base, "good-manifest.json")
        write(good_manifest, json.dumps({"events": events}))
        rc_good = main(["--root", base, "--manifest", good_manifest])
        case("well-formed manifest run -> exit 0", rc_good == 0)

    finally:
        shutil.rmtree(base, ignore_errors=True)

    print("routing-census self-test: %s (%d/%d)"
         % ("PASS" if failed == 0 else "FAIL", total - failed, total))
    return 0 if failed == 0 else 1


# ------------------------------------------------------------------ CLI
def main(argv):
    if "--self-test" in argv:
        return self_test()
    if "--root" not in argv:
        print("usage: routing-census.py --root DIR (--events E [E...] | "
             "--manifest FILE) | --self-test")
        return 2
    root = argv[argv.index("--root") + 1]
    events = []
    if "--manifest" in argv:
        mpath = argv[argv.index("--manifest") + 1]
        try:
            manifest = json.load(open(mpath, encoding="utf-8"))
        except (ValueError, OSError) as e:
            print("routing-census: malformed manifest %s: %s" % (mpath, e))
            return 2
        events = manifest.get("events") or []
    elif "--events" in argv:
        i = argv.index("--events") + 1
        while i < len(argv) and not argv[i].startswith("--"):
            events.append(argv[i])
            i += 1
    if not events:
        print("routing-census: no events in ledger slice (need --events or "
             "a --manifest with a non-empty \"events\" list)")
        return 2
    input_manifest, ims, output, osha = compute_census(root, events)
    print(json.dumps({"input_manifest": input_manifest,
                      "input_manifest_sha256": ims,
                      "output": output,
                      "output_sha256": osha}, indent=1, sort_keys=True))
    print("routing-census: input_manifest_sha256=%s output_sha256=%s"
         % (ims, osha))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
