#!/usr/bin/env python3
"""check-derivation.py -- contract-layer derivation-status sensor (knowledge-os, v3).

Reports every wiki view whose derivation block is an `audit-pending` T1 view
(memory-engine-v3-spec.md §7/§13, F12): a top-tier view that has NOT yet passed
its mandatory adversarial verify. The dispatch / build path consumes this report
to REFUSE building or dispatching against an unaudited T1 view -- the refusal lives
in the caller; this sensor only surfaces the list (and, under --gate, exits nonzero
so a flight-plan sweep can hard-stop).

Companion to check-frontmatter.py (which validates the block's structure +
schema_version). This one reads the block's *state*. Stdlib-only.

Census scope (v3.0-157, fleet inbox #9): `wiki/cold/**` -- retire.py's
content-addressed cold store -- is excluded from the ENTIRE census (audit-pending,
region-presence, stale-verified alike) and reported as a count on its own line.
A cold object is a quoted record whose integrity is the retirement journal's
sha256; it is not a compiled view, and minting a region into one (the FIX this
sensor prints for real region-less views) would corrupt its content address.

CONTENT-3 CANONICAL (stale-verified) check
-------------------------------------------
This module also carries the CANONICAL (frontmatter-side) half of CONTENT-3 ·
VERIFIED-RESET (memory-engine-v3-spec.md, derivation-block schema ~L120-145;
memory-engine-v3-test-plan.md ~L246-254: "Part of deploy/check-derivation.py
(ACC-3's sensor): diff body (frontmatter excluded) vs HEAD; compare verified.
Fixture: views/view-stale-verified.md pair.").

check-verified-reset.py already covers the JOURNAL side of this risk class --
quoting its own docstring so the split isn't duplicated here: "The engine's own
rebuilds already reset verified-status through the journal (compile-v2.py's
verify_run stamps noop_candidates with a view_sha256 pin at verify time; a later
absorb/verify record legitimately re-covers a view). That in-engine path is
governed. The uncovered risk this sensor closes: a view file edited OUTSIDE the
engine (manual edit, another tool) AFTER a verify stamped it -- the stale
verified status must not keep being served." check-verified-reset.py answers
that from the JOURNAL's point of view (last verify stamp's pinned sha256 vs
current body). This module answers the same risk class from the FRONTMATTER's
point of view, with no journal dependency at all: does the view's OWN
`verified:` field claim non-null while its body has moved since HEAD? A file
can pass the journal-side check (no journal record exists yet, or journal is
absent) and still be caught here, and vice versa -- they are deliberately
independent layers over the same invariant, not a single code path.

MECHANICS (frontmatter-side, mechanical, cheap -- no journal read):
  For a view file, extract the derivation block. If `verified:` parses to a
  non-null value (either a bare scalar sanity check, e.g. legacy `verified:
  passed`, or the schema's nested block whose first non-blank sub-key is
  present, e.g. `verified:\n  status: passed`), the view claims to be
  verified. Only then do we care about staleness.

  "Body" = the file's full text with the derivation region (DERIV_START..
  DERIV_END inclusive) removed. This intentionally also drops the surrounding
  YAML frontmatter dashes-block if the derivation region sits inside it (it
  does not in this schema -- derivation is a separate strip region below the
  frontmatter -- but body comparison is frontmatter-blind either way since we
  only diff what's outside the derivation markers; the spec's "frontmatter
  excluded" phrasing maps onto "derivation-region excluded" here, as the
  YAML title/sources frontmatter is not what verified: pins).

  STALE iff: body(working tree) != body(HEAD) AND verified is non-null in the
  WORKING TREE copy. (If verified is null in the working copy, it doesn't
  matter whether the body changed -- null is "never verified" or "already
  reset", both fine.) This is the spec's "vs HEAD" read implemented literally:
  the working tree is compared against the git blob at HEAD for the same
  path; a file with no HEAD blob (new, untracked) cannot be stale (nothing to
  have gone stale relative to) and is skipped, not flagged.

  FAIL-CLOSED: a file whose text contains a DERIV_START marker but whose
  region cannot be parsed into start+end (truncated/malformed) is
  INCONCLUSIVE, exit 2, distinct from a VIOLATION (stale-verified found),
  which is also exit 2 for CLI-uniformity reasons but reported under a
  different label and counted separately -- see --gate below for the exact
  precedence.

Fixture location note (spec deviation, intentional): the test plan says
"Fixture: views/view-stale-verified.md pair." This repo has no top-level
views/ directory -- wiki/ is the canonical content tree and deploy/fixtures/
is where this engine's other sensors (check-verified-reset.py, etc.) already
keep synthetic fixtures out of wiki/. The pair therefore lives at
deploy/fixtures/view-stale-verified/{stale.md,fresh.md} rather than at the
spec's literal views/ path. Self-test does not read these files from disk
(embedded strings are used for CL-3-style hermetic self-test, matching this
module's existing convention below) -- the on-disk pair exists so a human or
a future git-backed test can point check-derivation.py --stale-only directly
at them and see the same verdicts against a real git history.

Usage:
  check-derivation.py [PATH ...]   scan (default: wiki/ under the resolved root);
                                    print audit-pending T1 views
  check-derivation.py --root DIR   resolve wiki/ (and the git HEAD comparisons) under
                                    DIR. Default root when absent = the parent of the
                                    deploy/ dir holding this script (family root
                                    standard, silence-sweep 2026-08-04) -- NEVER the
                                    CWD, so a wrong working directory can no longer
                                    make the F12 gate pass on a tree it never located.
  check-derivation.py --gate       exit 2 if any audit-pending T1 view OR any
                                    stale-verified view is found (either trips the gate)
  check-derivation.py --stale-only [PATH ...]   scan ONLY for stale-verified views
                                    (skips the audit-pending T1 report); useful when a
                                    caller wants CONTENT-3 in isolation
  check-derivation.py --self-test  run embedded fixtures (CL-3)

Exit codes: 0 = clean / report-only
  | 2 = audit-pending T1 view(s) and/or stale-verified view(s) found under --gate
        or --stale-only, OR an unparseable derivation region on a file that HAS
        one (inconclusive) -- inconclusive is reported distinctly in text but
        shares this exit code family per this module's existing CLI convention
  | 3 = INCONCLUSIVE, TREE NOT LOCATED (fail-honest, silence-sweep S2): no PATH
        args and no wiki/ directory exists under the resolved root, in EVERY mode
        including --gate. Chosen as a NEW code precisely because existing callers
        treat 0 as pass and 2 as gate-failure (doctor.py's derivation-gate; the
        flight-plan Step 5.8 sweep) -- an unlocated tree must read as neither.
        An EXISTING-but-empty wiki/ is a located tree and keeps exit 0.
  | 1 = self-test failure.
"""

import os
import re
import subprocess
import sys

DERIV_START = "# --- derivation"
DERIV_END = "# --- /derivation"

# Regenerated projections are rebuilt wholesale by /compile, so they neither
# carry nor need a derivation region -- same exclusion backfill-derivation.py
# applies when minting (v3.0-69 region-presence check).
PROJECTION_BASENAMES = {"INDEX.md", "HEALTH.md", "REVIEW.md"}

# Hand-authored, direct-editable wiki subtrees: never absorbed through the
# engine, so they have nothing to verify and no region to carry. Flight plans
# are named as the single-writer rule's documented exception
# (core/governance/CLAUDE.md), which is exactly why /compile never writes
# them. Reported live 2026-08-06 (v3.0-local-9): the region-presence check
# listed both of a project's flight plans as unverifiable and pointed at
# backfill-derivation.py to fix them -- a tool that then refuses them. A
# check whose only remedy is a tool that declines the file is a loop, and a
# permanent one on every sweep.
NON_COMPILED_DIRS = ("flight-plans",)

# Retirement cold objects (wiki/cold/**, retire.py's content-addressed store) are
# NOT compiled views and are excluded from this sensor's ENTIRE census -- not just
# the region-presence check (v3.0-157, fleet inbox #9). A cold object is the raw
# span bytes of retired content: its integrity story is the retirement journal's
# sha256 (the filename embeds the content address), never a compile derivation,
# and its bytes are a QUOTED RECORD that may verbatim-contain anything -- a
# derivation region included -- without that meaning anything about the file
# itself. Flagging one trains operators to ignore red; and this sensor's FIX
# line (backfill-derivation.py) would mint a region into content-addressed
# bytes, breaking digest verification for every published retirement. Cold
# paths are filtered at _iter_files (explicit args included -- a cold object
# must NEVER fail this gate, however it was named) and counted on their own
# always-printed line so the class stays visible without gating RESULT (the
# v3.0-148 quoted-record pattern).
_COLD_RE = re.compile(r"(^|[/\\])wiki[/\\]cold([/\\]|$)")


def _is_cold_path(fp):
    return bool(_COLD_RE.search(os.path.normpath(fp)))

# Family root standard (silence-sweep 2026-08-04; same pattern as check-loop-state.py):
# the default scan root is the parent of the deploy/ dir holding this script -- never
# the CWD. A caller may override with --root DIR.
_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.dirname(_HERE)


def _extract_derivation(text):
    lines = text.splitlines()
    start = end = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if start is None and s.startswith(DERIV_START):
            start = i
        elif start is not None and s.startswith(DERIV_END):
            end = i
            break
    if start is not None and end is not None and end > start:
        return lines[start + 1:end]
    return None


def _derivation_region_status(text):
    """Returns ("absent", None) if no DERIV_START marker at all; ("ok", lines)
    if a well-formed start..end region is found; ("malformed", None) if a
    DERIV_START marker is present but no valid matching end was found (fail-
    closed case -- the file HAS a derivation region but it can't be parsed)."""
    lines = text.splitlines()
    start = end = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if start is None and s.startswith(DERIV_START):
            start = i
        elif start is not None and s.startswith(DERIV_END):
            end = i
            break
    if start is None:
        return "absent", None
    if end is not None and end > start:
        return "ok", lines[start + 1:end]
    return "malformed", None


def _strip_derivation_region(text):
    """Body with the derivation region (markers inclusive) removed. If the
    region is absent or malformed, returns text unchanged (caller decides
    whether that's meaningful for its purposes)."""
    lines = text.splitlines(keepends=True)
    start = end = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if start is None and s.startswith(DERIV_START):
            start = i
        elif start is not None and s.startswith(DERIV_END):
            end = i
            break
    if start is not None and end is not None and end > start:
        return "".join(lines[:start] + lines[end + 1:])
    return text


def _verified_is_non_null(region_lines):
    """True if the derivation region's `verified:` key is present and does
    not resolve to a null/empty value. Handles both the legacy bare-scalar
    form (`verified: passed`) and the schema's nested-block form
    (`verified:` on its own line followed by indented `status:`/`at:`/...
    sub-keys)."""
    keys = _top_level_keys(region_lines)
    if "verified" not in keys:
        return False
    val = keys["verified"]
    if val and val.lower() not in ("null", "~", ""):
        return True
    if val:
        return False
    # bare `verified:` with nothing on the line -- check for an indented
    # nested block following it (schema's canonical non-null form).
    found = False
    in_block = False
    for ln in region_lines:
        if re.match(r"^verified:\s*(#.*)?$", ln):
            in_block = True
            continue
        if in_block:
            if ln[:1] in (" ", "\t") and ln.strip():
                found = True
                break
            if ln.strip() and ln[:1] not in (" ", "\t"):
                break
    return found


def _top_level_keys(region_lines):
    keys = {}
    for ln in region_lines:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if ln[:1] in (" ", "\t"):
            continue
        m = re.match(r"^([A-Za-z0-9_]+):(.*)$", ln)
        if m:
            keys[m.group(1)] = m.group(2).split("#", 1)[0].strip()
    return keys


def audit_pending_t1(text):
    """True if this file carries a derivation block that is a T1 audit-pending view."""
    deriv = _extract_derivation(text)
    if not deriv:
        return False
    keys = _top_level_keys(deriv)
    return keys.get("tier") == "T1" and keys.get("consumed_status") == "audit-pending"


# ---------------------------------------------------------------------------
# CONTENT-3 CANONICAL: stale-verified (frontmatter side)
# ---------------------------------------------------------------------------

# Verdicts returned by stale_verified_check(). Distinct from a plain bool so
# callers can tell "clean because null" apart from "clean because unchanged"
# apart from "inconclusive."
SV_CLEAN = "clean"           # verified null, OR verified non-null but body unchanged
SV_STALE = "stale"           # verified non-null AND body changed vs HEAD -> VIOLATION
SV_INCONCLUSIVE = "inconclusive"  # derivation region present but unparseable
SV_SKIP = "skip"             # no derivation region at all -- not in scope


def stale_verified_check(working_text, head_text):
    """CONTENT-3 canonical check for one file.

    working_text: current working-tree contents.
    head_text: contents at HEAD for the same path, or None if the file has
      no HEAD blob (new/untracked -- nothing to have gone stale against).

    Returns one of SV_CLEAN / SV_STALE / SV_INCONCLUSIVE / SV_SKIP.
    """
    status, region = _derivation_region_status(working_text)
    if status == "absent":
        return SV_SKIP
    if status == "malformed":
        return SV_INCONCLUSIVE

    if not _verified_is_non_null(region):
        return SV_CLEAN  # null = never verified (or already reset) -- fine regardless of body

    if head_text is None:
        return SV_CLEAN  # no HEAD blob to have gone stale against

    head_status, _head_region = _derivation_region_status(head_text)
    if head_status == "malformed":
        return SV_INCONCLUSIVE

    working_body = _strip_derivation_region(working_text)
    head_body = _strip_derivation_region(head_text)

    if working_body != head_body:
        return SV_STALE
    return SV_CLEAN


def _git_show_head(repo, rel_path):
    """Returns HEAD blob text for rel_path, or None if it doesn't exist at
    HEAD (new/untracked file) or git is unavailable."""
    try:
        p = subprocess.run(
            ["git", "-C", repo, "show", "HEAD:%s" % rel_path.replace(os.sep, "/")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except (OSError, FileNotFoundError):
        return None
    if p.returncode != 0:
        return None
    return p.stdout


def _iter_files(paths):
    for p in paths:
        if os.path.isfile(p):
            if not _is_cold_path(p):
                yield p
        elif os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                if os.sep + ".git" in root:
                    continue
                for f in files:
                    fp = os.path.join(root, f)
                    if f.endswith(".md") and not _is_cold_path(fp):
                        yield fp


def _count_cold(paths):
    """How many cold objects the census excluded -- reported, never gated."""
    n = 0
    for p in paths:
        if os.path.isfile(p):
            n += 1 if _is_cold_path(p) else 0
        elif os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                if os.sep + ".git" in root:
                    continue
                n += sum(1 for f in files
                         if f.endswith(".md") and _is_cold_path(os.path.join(root, f)))
    return n


def scan_stale(paths, repo=None):
    """Runs the CONTENT-3 canonical stale-verified check across paths.
    Returns (stale_list, inconclusive_list). repo defaults to DEFAULT_ROOT
    (family root standard) -- never the CWD."""
    repo = repo or DEFAULT_ROOT
    stale = []
    inconclusive = []
    for fp in _iter_files(paths):
        try:
            with open(fp, "r", encoding="utf-8") as fh:
                working_text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        rel = os.path.relpath(fp, repo)
        head_text = _git_show_head(repo, rel)
        verdict = stale_verified_check(working_text, head_text)
        if verdict == SV_STALE:
            stale.append(fp)
        elif verdict == SV_INCONCLUSIVE:
            inconclusive.append(fp)
    return stale, inconclusive


def scan(paths, gate=False, stale_only=False, repo=None):
    pending = []
    regionless = []
    if not stale_only:
        for fp in _iter_files(paths):
            try:
                with open(fp, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            if audit_pending_t1(text):
                pending.append(fp)
            # v3.0-69: region PRESENCE, the blind spot this sensor carried.
            # Every check above keys on a region's CONTENTS, so a view with
            # no region at all was invisible: stale_verified_check returns
            # SV_SKIP ("not in scope") and audit_pending_t1 returns False.
            # A region-less view cannot record a verification at all --
            # _stamp_verified_block writes strictly inside the region, so a
            # confirmed cross-vendor verdict is produced and then discarded
            # -- and this sensor reported the tree clean while that was true.
            parts = os.path.normpath(fp).replace("\\", "/").split("/")
            if (os.path.basename(fp) not in PROJECTION_BASENAMES
                    and not any(d in parts for d in NON_COMPILED_DIRS)
                    and DERIV_START not in text):
                regionless.append(fp)

    stale, inconclusive = scan_stale(paths, repo=repo)

    rc = 0

    # v3.0-157: the excluded class stays visible without gating RESULT. This
    # line is the ONLY place a cold object may appear in this sensor's output;
    # in particular the backfill FIX advice below can never name a cold path.
    n_cold = _count_cold(paths)
    if n_cold:
        print("check-derivation: %d cold object(s) under wiki/cold/ excluded "
              "from the view census (content-addressed retirement records; "
              "integrity = the retirement journal's sha256, not a derivation "
              "region -- never backfill these)." % n_cold)

    if not stale_only:
        if pending:
            print("check-derivation: %d audit-pending T1 view(s) -- dispatch/build must REFUSE these:" % len(pending))
            for fp in sorted(pending):
                print("  - %s" % fp)
            if gate:
                rc = 2
        else:
            print("check-derivation: no audit-pending T1 views.")

        if regionless:
            print("check-derivation: %d wiki view(s) carry NO derivation "
                  "region -- these can never record a verification. A "
                  "cross-vendor checker can approve them, but the approval "
                  "has nowhere to be stamped, so it is produced and then "
                  "discarded:" % len(regionless))
            for fp in sorted(regionless):
                print("  - %s" % fp)
            print("  FIX: run `python deploy/backfill-derivation.py --root .` "
                  "once (on a worktree or branch, per that script's own "
                  "safety note) to mint a region for each. Views created by "
                  "the engine from v3.0.29 on get one automatically.")
            if gate:
                rc = 2
        else:
            print("check-derivation: every wiki view carries a derivation "
                  "region (verifications can be recorded).")

    if inconclusive:
        print("check-derivation: %d view(s) with an UNPARSEABLE derivation region -- INCONCLUSIVE (fail-closed):" % len(inconclusive))
        for fp in sorted(inconclusive):
            print("  - %s" % fp)
        rc = 2
    elif stale:
        print("check-derivation: %d STALE-VERIFIED view(s) (CONTENT-3, body changed vs HEAD but verified: not reset):" % len(stale))
        for fp in sorted(stale):
            print("  - %s" % fp)
        if gate or stale_only:
            rc = 2
    else:
        print("check-derivation: no stale-verified views (CONTENT-3 canonical clean).")

    return rc


_AUDIT_PENDING = """# --- derivation (engine-managed; strip region) ---
schema_version: 3.2
view: topic
tier: T1
consumed_status: audit-pending
# --- /derivation ---
"""

_VERIFIED_T1 = _AUDIT_PENDING.replace("audit-pending", "verified-consumed")
_PENDING_T3 = _AUDIT_PENDING.replace("tier: T1", "tier: T3")
_NO_DERIV = "---\ntitle: x\n---\nbody\n"


def self_test():
    cases = [
        ("audit-pending T1", _AUDIT_PENDING, True),
        ("verified T1",      _VERIFIED_T1,   False),
        ("audit-pending T3", _PENDING_T3,    False),
        ("no derivation",    _NO_DERIV,      False),
    ]
    failed = 0
    for name, text, expected in cases:
        got = audit_pending_t1(text)
        ok = (got == expected)
        if not ok:
            failed += 1
        print("  %s %-18s got=%s exp=%s" % ("ok " if ok else "XX ", name, got, expected))

    # ---- CONTENT-3 canonical (stale-verified) cases -----------------------
    _HEAD_BODY = "---\ntitle: x\n---\n# --- derivation (engine-managed; strip region) ---\nschema_version: 3.2\nview: topic\nverified:\n  status: passed\n  at: 2026-06-10T18:22:00Z\n# --- /derivation ---\nOriginal body text, unchanged.\n"
    _STALE_WORKING = _HEAD_BODY.replace(
        "Original body text, unchanged.",
        "Body text was edited after the verify stamp.",
    )
    _FRESH_WORKING = _HEAD_BODY  # body identical to HEAD, verified still non-null
    _NULL_VERIFIED_HEAD = _HEAD_BODY.replace(
        "verified:\n  status: passed\n  at: 2026-06-10T18:22:00Z\n", "verified: null\n"
    )
    _NULL_VERIFIED_WORKING = _NULL_VERIFIED_HEAD.replace(
        "Original body text, unchanged.", "Body edited but verified was already null.",
    )
    _MALFORMED = "# --- derivation (engine-managed; strip region) ---\nschema_version: 3.2\nverified:\n  status: passed\nbody with no closing marker\n"

    sv_cases = [
        ("stale flagged",        _STALE_WORKING, _HEAD_BODY,           SV_STALE),
        ("fresh clean",          _FRESH_WORKING, _HEAD_BODY,           SV_CLEAN),
        ("verified-null ignored", _NULL_VERIFIED_WORKING, _NULL_VERIFIED_HEAD, SV_CLEAN),
        ("no-derivation ignored", _NO_DERIV,     _NO_DERIV,            SV_SKIP),
        ("unparseable inconclusive", _MALFORMED, _MALFORMED,           SV_INCONCLUSIVE),
    ]
    for name, working, head, expected in sv_cases:
        got = stale_verified_check(working, head)
        ok = (got == expected)
        if not ok:
            failed += 1
        print("  %s %-24s got=%-13s exp=%s" % ("ok " if ok else "XX ", name, got, expected))

    # ---- FAIL-HONEST tree-absent cases (silence-sweep S2; tempdir fixture -------
    # pattern per check-loop-state.py's (1a3) envelope-resolution cases): a root
    # with NO wiki/ must be INCONCLUSIVE exit 3 in EVERY mode (--gate included --
    # the old exit-0 rendered as a PASSing F12 gate on an unlocated tree), while a
    # root with an existing-but-EMPTY wiki/ is a located tree and keeps exit 0.
    import shutil
    import tempfile
    d = tempfile.mkdtemp(prefix="cdv-root-")
    try:
        root_cases = [
            ("tree-absent plain",  ["check-derivation.py", "--root", d], 3),
            ("tree-absent --gate", ["check-derivation.py", "--gate", "--root", d], 3),
            ("tree-absent --stale-only",
             ["check-derivation.py", "--stale-only", "--root", d], 3),
        ]
        for name, argv, exp in root_cases:
            got = main(argv)
            ok = (got == exp)
            if not ok:
                failed += 1
            print("  %s %-24s exit=%-10s exp=%s" % ("ok " if ok else "XX ", name, got, exp))
        os.makedirs(os.path.join(d, "wiki"))
        empty_cases = [
            ("empty-wiki plain",  ["check-derivation.py", "--root", d], 0),
            ("empty-wiki --gate", ["check-derivation.py", "--gate", "--root", d], 0),
        ]
        for name, argv, exp in empty_cases:
            got = main(argv)
            ok = (got == exp)
            if not ok:
                failed += 1
            print("  %s %-24s exit=%-10s exp=%s" % ("ok " if ok else "XX ", name, got, exp))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- v3.0-69 region-PRESENCE cases -------------------------------------
    # The blind spot: every other check here keys on a region's CONTENTS, so a
    # view carrying no region at all was invisible and the tree reported clean
    # while verification was impossible on it.
    region_cases = []
    d2 = tempfile.mkdtemp(prefix="cdv-region-")
    try:
        wiki2 = os.path.join(d2, "wiki")
        os.makedirs(wiki2)
        # a view with NO derivation region -- the engine-born shape
        with open(os.path.join(wiki2, "regionless.md"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(_NO_DERIV)
        region_cases = [
            ("regionless plain (reported, not a gate failure)",
             ["check-derivation.py", "--root", d2], 0),
            ("regionless --gate REFUSES",
             ["check-derivation.py", "--gate", "--root", d2], 2),
        ]
        for name, argv, exp in region_cases:
            got = main(argv)
            ok = (got == exp)
            if not ok:
                failed += 1
            print("  %s %-46s exit=%-4s exp=%s"
                  % ("ok " if ok else "XX ", name, got, exp))
        # a regenerated projection is NEVER expected to carry a region
        os.remove(os.path.join(wiki2, "regionless.md"))
        for proj in sorted(PROJECTION_BASENAMES):
            with open(os.path.join(wiki2, proj), "w", encoding="utf-8",
                      newline="\n") as fh:
                fh.write(_NO_DERIV)
        proj_case = ("projections exempt from the region check",
                     ["check-derivation.py", "--gate", "--root", d2], 0)
        got = main(proj_case[1])
        ok = (got == proj_case[2])
        if not ok:
            failed += 1
        print("  %s %-46s exit=%-4s exp=%s"
              % ("ok " if ok else "XX ", proj_case[0], got, proj_case[2]))
        region_cases.append(proj_case)
        # hand-authored flight plans are never compiled -> never flagged
        # (v3.0-local-9: they were listed as unverifiable, with a remedy
        # tool that refuses them -- a permanent loop on every sweep)
        for proj in PROJECTION_BASENAMES:
            os.remove(os.path.join(wiki2, proj))
        fp_dir = os.path.join(wiki2, "flight-plans")
        os.makedirs(fp_dir)
        with open(os.path.join(fp_dir, "plan-a.md"), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(_NO_DERIV)
        fp_case = ("hand-authored flight plans exempt from the region check",
                   ["check-derivation.py", "--gate", "--root", d2], 0)
        got = main(fp_case[1])
        ok = (got == fp_case[2])
        if not ok:
            failed += 1
        print("  %s %-46s exit=%-4s exp=%s"
              % ("ok " if ok else "XX ", fp_case[0], got, fp_case[2]))
        region_cases.append(fp_case)
        shutil.rmtree(fp_dir, ignore_errors=True)
        # a view WITH a region passes the presence check
        with open(os.path.join(wiki2, "regioned.md"), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write("---\ntitle: x\n---\n" + _VERIFIED_T1 + "body\n")
        ok_case = ("view WITH a region passes the presence check",
                   ["check-derivation.py", "--gate", "--root", d2], 0)
        got = main(ok_case[1])
        ok = (got == ok_case[2])
        if not ok:
            failed += 1
        print("  %s %-46s exit=%-4s exp=%s"
              % ("ok " if ok else "XX ", ok_case[0], got, ok_case[2]))
        region_cases.append(ok_case)
    finally:
        shutil.rmtree(d2, ignore_errors=True)

    # ---- v3.0-157 cold-object cases (fleet inbox #9) ------------------------
    # A cold object under wiki/cold/ is a content-addressed retirement record:
    # it must NEVER fail this gate (not for a missing region, not for quoted
    # audit-pending bytes it verbatim-contains), while a region-less REAL view
    # beside it still does -- and the FIX line never names the cold path.
    cold_cases = []
    d3 = tempfile.mkdtemp(prefix="cdv-cold-")
    try:
        wiki3 = os.path.join(d3, "wiki")
        cold3 = os.path.join(wiki3, "cold", "some-view")
        os.makedirs(cold3)
        # bare span bytes, no region -- the shape retire.py writes
        with open(os.path.join(cold3, "section-a--" + "a" * 64 + ".md"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("Retired span bytes, verbatim.\n")
        # quoted-record hazard: cold bytes that verbatim-contain an
        # audit-pending T1 derivation region (retired content can quote
        # anything) -- must not be read as a view's own state
        with open(os.path.join(cold3, "section-b--" + "b" * 64 + ".md"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("Quoted record follows:\n" + _AUDIT_PENDING)
        cold_cases = [
            ("cold objects never FAIL the gate (regionless + quoted region)",
             ["check-derivation.py", "--gate", "--root", d3], 0),
        ]
        for name, argv, exp in cold_cases:
            got = main(argv)
            ok = (got == exp)
            if not ok:
                failed += 1
            print("  %s %-52s exit=%-4s exp=%s"
                  % ("ok " if ok else "XX ", name, got, exp))
        # a region-less REAL view beside the cold store still FAILs, and the
        # sensor's listing/FIX must name only the real view, never a cold path
        with open(os.path.join(wiki3, "real-regionless.md"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(_NO_DERIV)
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = main(["check-derivation.py", "--gate", "--root", d3])
        out_text = buf.getvalue()
        mixed_ok = (got == 2 and "real-regionless.md" in out_text
                    and ("cold" + os.sep + "some-view") not in
                    out_text.replace("wiki/cold/ excluded", "")
                    and "excluded from the view census" in out_text)
        if not mixed_ok:
            failed += 1
        print("  %s %-52s exit=%-4s exp=2 (real view named, cold path not)"
              % ("ok " if mixed_ok else "XX ",
                 "region-less REAL view beside cold still FAILs", got))
        cold_cases.append(("mixed", None, 2))
    finally:
        shutil.rmtree(d3, ignore_errors=True)

    total = (len(cases) + len(sv_cases) + len(root_cases) + len(empty_cases)
             + len(region_cases) + len(cold_cases))
    if failed:
        print("check-derivation self-test: FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("check-derivation self-test: PASS (%d/%d)" % (total, total))
    return 0


def main(argv):
    args = argv[1:]
    if "--self-test" in args:
        return self_test()
    gate = "--gate" in args
    stale_only = "--stale-only" in args
    # --root DIR: family root standard (see DEFAULT_ROOT above). The flag's value is
    # consumed here so the positional-path collection below never mistakes it for a
    # scan target.
    root = DEFAULT_ROOT
    if "--root" in args:
        i = args.index("--root")
        if i + 1 >= len(args) or args[i + 1].startswith("--"):
            print("check-derivation: --root requires a directory argument")
            return 3
        root = os.path.abspath(args[i + 1])
        args = args[:i] + args[i + 2:]
    paths = [a for a in args if not a.startswith("--")]
    if not paths:
        wiki = os.path.join(root, "wiki")
        if not os.path.isdir(wiki):
            # FAIL-HONEST (silence-sweep S2): the sensor did not locate its subject
            # tree, so it must not answer -- in ANY mode. The old behavior exited 0
            # here, which doctor.py rendered as "[PASS] derivation-gate: no
            # audit-pending T1 views" on a tree with no wiki at all. Exit 3 is
            # deliberately neither 0 (pass) nor 2 (gate failure) -- see the
            # exit-code contract in the module docstring.
            print("check-derivation: INCONCLUSIVE -- no wiki/ directory under "
                  "resolved root %s; the sensor never located its tree, so no "
                  "verdict is issued in any mode (--gate included; exit 3)." % root)
            return 3
        paths = [wiki]
    return scan(paths, gate=gate, stale_only=stale_only, repo=root)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
