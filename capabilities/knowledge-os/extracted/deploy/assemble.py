#!/usr/bin/env python3
"""assemble.py -- P4 read-path assembler + serving REFUSAL gate (knowledge-os, v3).

Implements the frozen spec's memory-engine-v3-spec.md §7 tail:
  "assemble.py refuses an unverified-T1 view to a build task."
plus F12 consumed_status discipline (§5, §7, §13, §11):
  a build/fix task packet must never include a T1 view whose derivation
  `consumed_status` is anything other than `verified-consumed`.

SCOPE (deliberately minimal -- this is the serving REFUSAL gate, not the
full P4 packet/bundle/taint machinery):
  - Assembles a packet: concatenated view bodies with a provenance header
    per view (path, tier, consumed_status, verified-at if present, and a
    cheap staleness banner).
  - Enforces the T1 gate below. THIS is the point of this build.
  - Emits NO packet content at all on refusal (fail-closed, all-or-nothing:
    a partial packet is the failure mode to avoid -- §11 "Fail loud, never
    truncate" generalizes to "never partially serve").

THE GATE (judgment calls, each a conservative reading of the spec):
  - consumed_status == "audit-pending" on a T1 view -> REFUSE.
    (§7: "assemble.py refuses an unverified-T1 view to a build task";
    §11: "audit-pending ... T1 view = hard stop for build/fix.")
  - consumed_status missing/unparseable on a T1 view -> REFUSE, fail-closed.
    The spec never describes a state where a T1 view legitimately has no
    consumed_status (F12 stamps it at P1 for every view), so an absent or
    malformed value is treated as unproven, not as passing.
  - tier missing/unparseable on a view -> treated as T1 (conservative
    default), matching backfill-derivation.py's documented T1-default
    doctrine (deploy/backfill-derivation.py: "tier: T1 -- conservative
    default (mandatory adversarial verify)"). Refused unless the (assumed)
    T1 view is separately verified-consumed.
  - consumed_status == "legacy-assumed" on a T1 view -> REFUSE for build
    tasks, but with a DISTINCT reason code ("legacy-assumed: B3 audit
    owed") from audit-pending. SPEC AMBIGUITY, flagged rather than
    silently resolved: §13 says legacy-assumed pairs are audit-gated
    (F14: "absorbed-without-source pairs are ALSO legacy-assumed and
    audit-gated") and §7 says a legacy-assumed/absorbed-without-source T1
    view needs a one-time content audit "before" being served to a
    build/fix task -- i.e. legacy-assumed is NOT yet cleared for build
    tasks either. This build takes the conservative reading (refuse, not
    pass), matching audit-pending's disposition, but keeps a DISTINCT
    reason code (rather than collapsing it into "audit-pending") so an
    operator can down-scope this specific refusal class later (e.g. once
    a B3-style audit sweep clears a cohort) without re-deriving the whole
    gate. This is the one place this script's behavior could plausibly
    be loosened by a future operator decision; it is not silently
    decided here.
  - consumed_status == "verified-consumed" on a T1 view -> PASS.
  - T3 views PASS regardless of consumed_status (§5: "T3 = sonnet and
    sampled"; spec never gates T3 serving on consumed_status) but still
    get a provenance header (transparency, not exemption from disclosure).
  - Any REFUSED view in a requested set aborts the WHOLE packet atomically
    -- no partial packet, matching "the failure mode to avoid" above.

OUT OF SCOPE for this build (later gates, noted per instructions, not
implemented here):
  - F11 origin_max / taint-quarantine refusal (§9, §11): "assemble.py
    refuses to emit a packet whose origin_max exceeds human to a
    credentialed session profile" and "a build/fix packet excludes
    external-scrape/unknown content regardless of tier." This script
    does not parse `origin_max` or enforce quarantine-by-task-type. A
    later gate must add this before assemble.py is trusted for the full
    P4 security boundary.
  - Full bundle depth-limiting / retrieval-closure assembly (§11).
  - Stale-view detection against the run journal (§6) -- this script only
    detects a CHEAP, LOCAL staleness signal: whether the file's mtime is
    newer than the recorded `verified.at` timestamp. It does not consult
    staleness.py's ledger-driven staleness computation. This is a banner
    (informational), never a refusal reason, per scope.
  - Corpus-artifact readability checks (§11 "Corpus safety for builds").

CLI:
  assemble.py --task "<descriptor>" [--views PATH ...] [--root DIR] [--json PATH]
  assemble.py --self-test

Exit codes: 0 = packet assembled clean | 2 = refusal (gate tripped) |
3 = usage error | 1 = self-test failure.

Stdlib-only. Reuses check-derivation.py's derivation-block parsing
approach (delimited-region extraction + top-level-key scan) rather than
importing it, so this file has no import-time dependency on another
agent's in-flight edits to check-derivation.py.
"""

import argparse
import io
import json
import os
import sys
import time

DERIV_START = "# --- derivation"
DERIV_END = "# --- /derivation"

REASON_AUDIT_PENDING = "audit-pending"
REASON_LEGACY_ASSUMED = "legacy-assumed: B3 audit owed"
REASON_MISSING_STATUS = "missing/unparseable consumed_status on T1 view (fail-closed)"
REASON_MISSING_DERIV = "no derivation block found (fail-closed, treated as T1)"


def _extract_derivation(text):
    """Return the lines strictly between the delimiter markers, or None."""
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


def _top_level_keys(region_lines):
    """Parse only top-level (non-indented) `key: value` pairs -- same
    shallow approach as check-derivation.py. Nested blocks (e.g. `verified:`
    sub-keys) are handled separately by _verified_block below.
    """
    keys = {}
    for ln in region_lines:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if ln[:1] in (" ", "\t"):
            continue
        if ":" not in ln:
            continue
        k, _, v = ln.partition(":")
        k = k.strip()
        if not k or not all(c.isalnum() or c == "_" for c in k):
            continue
        keys[k] = v.split("#", 1)[0].strip()
    return keys


def _verified_block(region_lines):
    """Extract the indented sub-keys of the `verified:` block, if present.
    Returns a dict (possibly empty) with keys like 'status', 'at', etc.
    """
    out = {}
    in_block = False
    for ln in region_lines:
        stripped = ln.strip()
        if not in_block:
            if stripped.startswith("verified:"):
                in_block = True
            continue
        if not ln[:1] in (" ", "\t"):
            break  # dedent -- block ended
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        k, _, v = stripped.partition(":")
        out[k.strip()] = v.split("#", 1)[0].strip()
    return out


def parse_derivation(text):
    """Return a dict describing a view's gate-relevant derivation state:
    {tier, tier_assumed, consumed_status, verified_at, has_block}
    """
    region = _extract_derivation(text)
    if region is None:
        return {
            "has_block": False,
            "tier": "T1",
            "tier_assumed": True,
            "consumed_status": None,
            "verified_at": None,
        }
    keys = _top_level_keys(region)
    raw_tier = keys.get("tier")
    if raw_tier in ("T1", "T3"):
        tier = raw_tier
        tier_assumed = False
    else:
        tier = "T1"
        tier_assumed = True
    consumed_status = keys.get("consumed_status") or None
    verified = _verified_block(region)
    verified_at = verified.get("at") or None
    return {
        "has_block": True,
        "tier": tier,
        "tier_assumed": tier_assumed,
        "consumed_status": consumed_status,
        "verified_at": verified_at,
    }


def gate_view(deriv):
    """Decide pass/refuse for one view's parsed derivation state.

    Returns (ok: bool, reason: str or None).
    """
    if not deriv["has_block"]:
        return False, REASON_MISSING_DERIV

    if deriv["tier"] == "T3" and not deriv["tier_assumed"]:
        return True, None  # T3 passes regardless of consumed_status

    # T1 (real or conservatively assumed)
    status = deriv["consumed_status"]
    if status == "verified-consumed":
        return True, None
    if status == "audit-pending":
        return False, REASON_AUDIT_PENDING
    if status == "legacy-assumed":
        return False, REASON_LEGACY_ASSUMED
    # missing / unparseable / any other unrecognized value
    return False, REASON_MISSING_STATUS


def _staleness_banner(path, verified_at):
    """Cheap, local staleness signal: mtime newer than verified.at.
    Best-effort only; never a refusal reason (out of scope per docstring).
    """
    if not verified_at:
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    # verified_at is an ISO8601 UTC timestamp, e.g. 2026-06-10T18:22:00Z
    try:
        ts = verified_at.rstrip("Z")
        struct = time.strptime(ts, "%Y-%m-%dT%H:%M:%S")
        verified_epoch = time.mktime(struct) - time.timezone
    except (ValueError, OverflowError):
        return None
    if mtime > verified_epoch:
        return "STALE-BANNER: file content-changed after verified.at (%s)" % verified_at
    return None


def resolve_within(root, path):
    """Realpath containment check -- both arguments resolved, NTFS ADS
    paths rejected. Minimal replication of the §9 F10 rule for view paths
    passed on this CLI. Returns the resolved absolute path or raises
    ValueError.
    """
    root_r = os.path.realpath(root)
    cand_r = os.path.realpath(path)
    # reject any ':' beyond the drive letter (NTFS alternate data streams)
    drive, rest = os.path.splitdrive(cand_r)
    if ":" in rest:
        raise ValueError("path contains ':' beyond drive letter (ADS-style): %s" % path)
    common = os.path.commonpath([root_r, cand_r]) if root_r else cand_r
    if root_r and common != root_r:
        raise ValueError("path escapes root: %s" % path)
    return cand_r


def build_header(path, deriv, reason):
    lines = ["--- view: %s ---" % path]
    lines.append("tier: %s%s" % (deriv["tier"], " (assumed, no explicit tier)" if deriv["tier_assumed"] else ""))
    lines.append("consumed_status: %s" % (deriv["consumed_status"] or "(missing)"))
    if deriv["verified_at"]:
        lines.append("verified-at: %s" % deriv["verified_at"])
    if reason:
        lines.append("REFUSAL: %s" % reason)
    return "\n".join(lines)


def assemble(task, view_paths, root=None):
    """Core assembly. Returns (exit_code, result_dict).

    result_dict on success (exit_code 0):
      {"task": task, "packet": "<concatenated text>", "views": [...headers...]}
    result_dict on refusal (exit_code 2):
      {"task": task, "refused": [{"path":..., "reason":...}, ...]}
    """
    root = root or os.getcwd()
    entries = []  # (path, text, deriv)
    load_errors = []

    for p in view_paths:
        try:
            resolved = resolve_within(root, p)
        except ValueError as e:
            load_errors.append({"path": p, "reason": "path-containment: %s" % e})
            continue
        try:
            with open(resolved, "r", encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError) as e:
            load_errors.append({"path": p, "reason": "unreadable: %s" % e})
            continue
        deriv = parse_derivation(text)
        entries.append((p, resolved, text, deriv))

    if load_errors:
        return 2, {"task": task, "refused": load_errors}

    refused = []
    for p, resolved, text, deriv in entries:
        ok, reason = gate_view(deriv)
        if not ok:
            refused.append({"path": p, "reason": reason})

    if refused:
        # Atomic refusal: NO packet content emitted, even for the passing views.
        return 2, {"task": task, "refused": refused}

    headers = []
    bodies = []
    for p, resolved, text, deriv in entries:
        banner = _staleness_banner(resolved, deriv["verified_at"])
        header = build_header(p, deriv, None)
        if banner:
            header += "\n" + banner
        headers.append(header)
        bodies.append(header + "\n\n" + text)

    packet = "\n\n====\n\n".join(bodies)
    return 0, {"task": task, "packet": packet, "views": headers}


# ------------------------------------------------------------------------
# Self-test fixtures (fixture-first: written before implementation logic
# was finalized).
# ------------------------------------------------------------------------

_FIX_VERIFIED_T1 = """---
title: x
---
# --- derivation (engine-managed; strip region) ---
schema_version: 3.2
tier: T1
consumed_status: verified-consumed
verified:
  status: passed
  at: 2020-01-01T00:00:00Z
# --- /derivation ---

Body verified T1.
"""

_FIX_AUDIT_PENDING_T1 = """---
title: x
---
# --- derivation (engine-managed; strip region) ---
schema_version: 3.2
tier: T1
consumed_status: audit-pending
# --- /derivation ---

Body audit-pending T1.
"""

_FIX_LEGACY_ASSUMED_T1 = """---
title: x
---
# --- derivation (engine-managed; strip region) ---
schema_version: 3.2
tier: T1
consumed_status: legacy-assumed
# --- /derivation ---

Body legacy-assumed T1.
"""

_FIX_NO_DERIV = "---\ntitle: x\n---\nNo derivation block at all.\n"

_FIX_MISSING_TIER = """---
title: x
---
# --- derivation (engine-managed; strip region) ---
schema_version: 3.2
consumed_status: verified-consumed
# --- /derivation ---

Body missing tier, but verified -- still refused? No: tier defaults T1,
consumed_status is verified-consumed, so this should PASS.
"""

_FIX_MISSING_TIER_UNVERIFIED = """---
title: x
---
# --- derivation (engine-managed; strip region) ---
schema_version: 3.2
consumed_status: audit-pending
# --- /derivation ---

Missing tier defaults T1; audit-pending -> refused.
"""

_FIX_T3_AUDIT_PENDING = """---
title: x
---
# --- derivation (engine-managed; strip region) ---
schema_version: 3.2
tier: T3
consumed_status: audit-pending
# --- /derivation ---

T3 passes regardless.
"""

_FIX_MISSING_STATUS_T1 = """---
title: x
---
# --- derivation (engine-managed; strip region) ---
schema_version: 3.2
tier: T1
# --- /derivation ---

T1 with no consumed_status key at all -- fail closed.
"""


def _write_fixture(dirpath, name, content):
    fp = os.path.join(dirpath, name)
    with open(fp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    return fp


def self_test():
    import tempfile

    failed = 0
    total = 0

    def check(name, cond):
        nonlocal failed, total
        total += 1
        print("  %s %s" % ("ok " if cond else "XX ", name))
        if not cond:
            failed += 1

    # --- Unit-level parse/gate cases (no filesystem needed) ---
    d = parse_derivation(_FIX_VERIFIED_T1)
    ok, reason = gate_view(d)
    check("verified-consumed T1 passes", ok and reason is None)

    d = parse_derivation(_FIX_AUDIT_PENDING_T1)
    ok, reason = gate_view(d)
    check("audit-pending T1 refused", (not ok) and reason == REASON_AUDIT_PENDING)

    d = parse_derivation(_FIX_LEGACY_ASSUMED_T1)
    ok, reason = gate_view(d)
    check("legacy-assumed T1 refused w/ distinct code", (not ok) and reason == REASON_LEGACY_ASSUMED)
    check("legacy-assumed reason distinct from audit-pending", REASON_LEGACY_ASSUMED != REASON_AUDIT_PENDING)

    d = parse_derivation(_FIX_NO_DERIV)
    ok, reason = gate_view(d)
    check("missing derivation block refused", (not ok) and reason == REASON_MISSING_DERIV)
    check("missing derivation block treated as T1", d["tier"] == "T1" and d["tier_assumed"])

    d = parse_derivation(_FIX_MISSING_TIER)
    ok, reason = gate_view(d)
    check("missing tier defaults T1 + verified -> passes", ok and d["tier_assumed"])

    d = parse_derivation(_FIX_MISSING_TIER_UNVERIFIED)
    ok, reason = gate_view(d)
    check("missing tier defaults T1 + refused when not verified", (not ok) and d["tier_assumed"])

    d = parse_derivation(_FIX_T3_AUDIT_PENDING)
    ok, reason = gate_view(d)
    check("T3 audit-pending passes", ok and reason is None)

    d = parse_derivation(_FIX_MISSING_STATUS_T1)
    ok, reason = gate_view(d)
    check("missing consumed_status on T1 refused (fail-closed)", (not ok) and reason == REASON_MISSING_STATUS)

    # --- Filesystem-level: real assemble() calls, mixed requests, atomicity ---
    with tempfile.TemporaryDirectory() as tmp:
        p_ok = _write_fixture(tmp, "ok.md", _FIX_VERIFIED_T1)
        p_bad = _write_fixture(tmp, "bad.md", _FIX_AUDIT_PENDING_T1)
        p_t3 = _write_fixture(tmp, "t3.md", _FIX_T3_AUDIT_PENDING)

        code, result = assemble("happy path", [p_ok], root=tmp)
        check("happy-path exit 0", code == 0)
        check("happy-path packet has provenance header", "consumed_status: verified-consumed" in result.get("packet", ""))

        code, result = assemble("mixed", [p_ok, p_bad], root=tmp)
        check("mixed request refuses atomically (exit 2)", code == 2)
        check("mixed request emits no packet", "packet" not in result)
        check("mixed request lists offending view", any(r["path"] == p_bad for r in result.get("refused", [])))

        code, result = assemble("t3 only", [p_t3], root=tmp)
        check("T3-only request passes despite audit-pending", code == 0)

        # CLI smoke: exit codes via subprocess-free direct main() call
        out_json = os.path.join(tmp, "out.json")
        rc = main(["assemble.py", "--task", "smoke", "--views", p_ok, "--root", tmp, "--json", out_json])
        check("CLI smoke exit 0 on clean task", rc == 0)
        rc2 = main(["assemble.py", "--task", "smoke2", "--views", p_bad, "--root", tmp])
        check("CLI smoke exit 2 on refusal", rc2 == 2)

    print("assemble.py self-test (T1-refusal gate, unchanged): %s (%d/%d)" %
          ("PASS" if failed == 0 else "FAIL", total - failed, total))

    # ---- P4 packet machinery (Component 2) self-tests, additive ----
    p4_failed, p4_total = _self_test_packet_machinery()
    failed += p4_failed
    total += p4_total

    # ---- Behavioral-manifest gate self-tests, additive (manifest-format.md
    # Section 12's assemble.py bullet) ----
    mg_failed, mg_total = _self_test_manifest_gate()
    failed += mg_failed
    total += mg_total

    # ---- ECO-1 golden run (Component 4): every golden descriptor asserted
    # against the LIVE catalog, plus the stale-forcing negative half ----
    eco1_failed, eco1_total = _self_test_eco1_golden()
    failed += eco1_failed
    total += eco1_total

    # ---- F-CATALOG EXPOSURE (gate-reg C1): run tool_grant's full F-1..F-16
    # catalog here too, so the registered TOOL-GRANT-ISOLATION row -- which
    # points at `assemble.py --self-test` per Adjudication 4/gate-reg C1 --
    # genuinely executes the catalog, not just imports the module. Folds
    # tool_grant's own per-case pass/fail count into this total; the case
    # lines are also reprinted here (not just tool_grant's own --self-test
    # transcript) so a reader of assemble.py's output sees the catalog run.
    tg_failed, tg_total = _run_tool_grant_catalog()
    failed += tg_failed
    total += tg_total

    print("assemble.py self-test TOTAL: %s (%d/%d)" % ("PASS" if failed == 0 else "FAIL", total - failed, total))
    return 1 if failed else 0


def _run_tool_grant_catalog():
    """Runs deploy/tool_grant.py's full F-1..F-16 self-test catalog (via the
    same importlib sibling-load pattern used elsewhere in this module) and
    folds its per-case pass/fail count into assemble.py's own --self-test
    total (gate-reg C1: the registered TOOL-GRANT-ISOLATION row points at
    `assemble.py --self-test`, so this call is what makes that row genuinely
    execute the catalog rather than merely importing the module).

    tool_grant.self_test() prints one line per case in the fixed format
    "  ok  <name>" / "  XX  <name>" (see its own `case()` helper) and returns
    an aggregate 0/1 (PASS/FAIL), not per-case counts -- so stdout is
    captured here and parsed for the ok/XX line count, which is the same
    count tool_grant's own --self-test invocation would report standalone
    (same fixture list, per the module's own C1 note: "this module's own
    --self-test runs the IDENTICAL catalog standalone, so the gate's
    evidence and this module's own proof are the same fixture list").
    Returns (failed: int, total: int)."""
    import contextlib

    tg = _tool_grant()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = tg.self_test()
    output = buf.getvalue()
    print("--- deploy/tool_grant.py --self-test (F-1..F-16 catalog, folded in per gate-reg C1) ---")
    sys.stdout.write(output)

    ok_count = 0
    xx_count = 0
    for line in output.splitlines():
        if line.startswith("  ok "):
            ok_count += 1
        elif line.startswith("  XX "):
            xx_count += 1

    tg_total = ok_count + xx_count
    tg_failed = xx_count
    # Cross-check tool_grant's own aggregate return code against the parsed
    # per-case count -- if these ever disagree (e.g. tool_grant's output
    # format drifts), fail loud rather than silently under/over-counting.
    if (rc != 0) != (tg_failed > 0):
        tg_failed += 1
        tg_total += 1
        print("  XX  tool_grant catalog aggregate rc (%d) disagrees with parsed"
              " per-case failure count (%d) -- output format drift, fail loud" % (rc, tg_failed - 1))

    print("--- end tool_grant catalog (%d/%d) ---" % (tg_total - tg_failed, tg_total))
    return tg_failed, tg_total



###############################################################################
# P4 PACKET MACHINERY (Component 2, memory-engine-v3-assemble-machinery-design
# -2026-07-05.md). Everything above this banner is the pre-existing T1-refusal
# serving gate (SERVE-T1-REFUSAL, 18/18) and stays semantically UNCHANGED --
# the packet machinery below is purely additive and is reached only through
# the NEW --descriptor CLI path (or the assemble_packet()/build_descriptor()
# functions called directly). The legacy --task/--views path below in main()
# still calls the original assemble()/gate_view() above, byte-for-byte.
#
# Design doc: harness-v3.0/specs/memory-engine-v3-assemble-machinery-design-
# 2026-07-05.md, Component 2. Adjudications 1-3 bind this section:
#   A1 (F-11 rebind): descriptor attestation strings are IGNORED for egress
#       decisions -- recorded, never trusted. The old test-plan fixture (b)
#       "allowed with attestation" path is REBOUND: tainted+credentialed is
#       ALWAYS refused now, attestation or not.
#   A2 (F-8 structural exclusion): no strip verb anywhere. Exclusion happens
#       at grant/packet-emission time, in the envelope, never mid-session.
#   A3 (golden descriptors run against the LIVE catalog): this module carries
#       no overlay-based "pretend the catalog is different" logic for normal
#       runs; overlays are used ONLY by the staleness self-test fixtures
#       (temp copies), never to fake selection/closure/taint results.
###############################################################################

import re as _re


# ---------------------------------------------------------------------------
# F-INTEGRATION SEAM (was TODO(F-integration)): deploy/tool_grant.py (Component
# 1) is now imported via the same importlib `_load_sibling` pattern used below
# for check-derivation.py, so this module has no import-time coupling to
# tool_grant.py beyond the sibling-load call itself. The FULL TCB catalog
# (F-1..F-16, F-10 reference-only) now governs the egress co-residency
# decision -- tool_grant.load_allowlist()/classify_tool() classify the
# profile's requested tools, and tool_grant.grant() is the single source of
# truth for {granted, excluded, session_flags, ignored_descriptor_claims}
# recorded in the packet envelope. No local two-pattern stub remains.
# ---------------------------------------------------------------------------

_tool_grant_mod = None


def _tool_grant():
    global _tool_grant_mod
    if _tool_grant_mod is None:
        _tool_grant_mod = _load_sibling("tool_grant.py", "assemble_tool_grant")
    return _tool_grant_mod


def _classify_profile_tools(tools, allowlist_path=None):
    """Classify profile tools against the real SAFE allowlist (tool_grant's
    load_allowlist()/classify_tool()). tools: iterable of tool-name strings.
    Returns {"credential": [...], "egress": [...], "safe": [...],
    "untrusted_ingestion": [...], "interpreter": [...], "dangerous": [...]}
    (each a sorted, deduped list) -- never a bool, so callers can always name
    the offending tools. This is classification only (no grant/session
    context); the actual grant decision is computed separately by
    _profile_grant() below, which calls tool_grant.grant(). `allowlist_path`
    is a self-test seam (tool_grant.load_allowlist's own injectable path,
    threaded through) -- production callers never pass it, so they always
    classify against the live deploy/safe-allowlist.yaml."""
    tg = _tool_grant()
    allowlist = tg.load_allowlist(allowlist_path)
    buckets = {"credential": set(), "egress": set(), "safe": set(),
               "untrusted_ingestion": set(), "interpreter": set(), "dangerous": set()}
    key_by_class = {
        "credential": "credential", "egress": "egress", "safe": "safe",
        "untrusted-ingestion": "untrusted_ingestion", "interpreter": "interpreter",
        "dangerous": "dangerous",
    }
    for t in tools or []:
        if t is None:
            continue
        s = str(t)
        cls = tg.classify_tool(s, allowlist)
        buckets[key_by_class.get(cls, "dangerous")].add(s)
    return {k: sorted(v) for k, v in buckets.items()}


def _profile_grant(profile_tools, context_items, descriptor_claims, allowlist_path=None):
    """Calls the real tool_grant.grant() -- the single source of truth for
    the packet envelope's grant block. Returns the FULL grant result dict:
    {granted, excluded, session_flags, ignored_descriptor_claims}.
    session_flags.credentialed is the new source of truth for whether the
    egress co-residency refusal applies (semantics unchanged from the old
    placeholder's "any credential/egress tool requested" reading -- tool_grant
    computes the same thing, now over the full F-1..F-16 catalog rather than
    a two-pattern stub). `allowlist_path` is a self-test seam (tool_grant.
    grant's own injectable path, threaded through) -- production callers
    never pass it, so they always grant against the live deploy/safe-
    allowlist.yaml."""
    tg = _tool_grant()
    return tg.grant(list(profile_tools or []), list(context_items or []),
                     descriptor_claims=dict(descriptor_claims or {}),
                     allowlist_path=allowlist_path)


# ---------------------------------------------------------------------------
# Reuse check-derivation.py's stale_verified rule via the importlib pattern
# already established by check-verified-reset.py (`_load(basename, alias)`).
# Do NOT duplicate the diff logic -- import and call it.
# ---------------------------------------------------------------------------

def _load_sibling(basename, alias):
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(alias, os.path.join(here, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_check_derivation_mod = None


def _check_derivation():
    global _check_derivation_mod
    if _check_derivation_mod is None:
        _check_derivation_mod = _load_sibling("check-derivation.py", "assemble_check_derivation")
    return _check_derivation_mod


# ---------------------------------------------------------------------------
# ORIGIN_ORDER (F7 lattice) -- kept as a local, dependency-free constant
# mirroring deploy/origin.py's ORIGIN_ORDER (least -> most restrictive), so
# this module has no import-time coupling to origin.py's own in-flight state.
# The values and ordering are the same certified lattice.
#
# THE "FROZEN, NOT EXPECTED TO DRIFT" PREMISE IS RETIRED (R-1, 2026-07-22): the
# lattice was extended once (`session-derived` inserted between `corpus` and
# `vendor-ref`, spec r1-build-decisions-2026-07-22.md Part 2.1), so it CAN
# change and this mirror is a real drift surface -- the same shape of bug as
# the B-2 receipts-population divergence (see registrations.py's
# ENGINE_SIDECAR_DIRS note). The drift here is fail-safe but NOT harmless:
# _ORIGIN_RANK.get(o, unknown) and the derivation reader's
# `origin_max_val not in _ORIGIN_RANK -> "unknown"` would silently read a
# legitimate `session-derived` as `unknown`, over-restricting rather than
# reporting honestly. ANY future addition to origin.py's ORIGIN_ORDER must be
# made here in the same commit.
# ---------------------------------------------------------------------------

ORIGIN_ORDER = ["human", "corpus", "session-derived", "vendor-ref",
                "external-scrape", "unknown"]
_ORIGIN_RANK = {o: i for i, o in enumerate(ORIGIN_ORDER)}
TRUSTED_ORIGINS = ("human",)  # the fork's operative trusted set (Adjudication/D3)


def _origin_max(values):
    """Most-restrictive origin over an iterable; empty -> 'human' (no taint)."""
    best = None
    for o in values:
        r = _ORIGIN_RANK.get(o, _ORIGIN_RANK["unknown"])
        if best is None or r > best[0]:
            best = (r, o)
    return best[1] if best else "human"


# ---------------------------------------------------------------------------
# Minimal stdlib-only YAML-subset reader. This engine's derivation blocks and
# descriptor files use a small, regular YAML subset (top-level `key: value`,
# inline `[a, b, c]` lists, block `- item` lists, and one level of nested
# indented mappings) -- the same shallow-parse convention check-derivation.py
# and this file's own _top_level_keys/_verified_block already use. No PyYAML
# dependency is introduced (stdlib-only per the build instructions).
# ---------------------------------------------------------------------------

def _split_inline_list(s):
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
    return None


def _parse_shallow_yaml(text):
    """Parses a restricted YAML subset into nested dict/list structures.
    Handles: top-level and indented `key: value` mappings, inline `[a, b]`
    lists, block lists (`- item` / `- key: value` on following indented
    lines), and comments (`#` to end of line, outside quotes -- good enough
    for this engine's own generated + hand-authored fixture files, which
    never quote a literal `#`). Returns a dict."""
    lines = [ln.rstrip("\n") for ln in text.splitlines()]

    def strip_comment(s):
        out = []
        in_q = None
        for ch in s:
            if in_q:
                out.append(ch)
                if ch == in_q:
                    in_q = None
                continue
            if ch in ("'", '"'):
                in_q = ch
                out.append(ch)
                continue
            if ch == "#":
                break
            out.append(ch)
        return "".join(out)

    def indent_of(s):
        return len(s) - len(s.lstrip(" "))

    def parse_block(idx, base_indent):
        """Parse a mapping starting at lines[idx] whose keys sit at
        base_indent. Returns (dict, next_idx)."""
        result = {}
        i = idx
        n = len(lines)
        while i < n:
            raw = lines[i]
            stripped_full = strip_comment(raw)
            if not stripped_full.strip():
                i += 1
                continue
            ind = indent_of(stripped_full)
            if ind < base_indent:
                break
            if ind > base_indent:
                # shouldn't happen if callers are disciplined; skip stray line
                i += 1
                continue
            content = stripped_full.strip()
            if content.startswith("- "):
                break  # caller (list parser) owns this line
            if ":" not in content:
                i += 1
                continue
            key, _, val = content.partition(":")
            key = key.strip()
            val = val.strip()
            i += 1
            if val in (">", ">-", ">+", "|", "|-", "|+"):
                # Block scalar (folded `>` or literal `|`), clip/strip/keep
                # chomping indicators ignored beyond clip-vs-strip (this
                # engine's own fixtures/descriptors only use plain `>`).
                # Consume all following lines indented MORE than base_indent
                # (blank lines included) as the scalar body; folded (`>`)
                # joins non-blank consecutive lines with a single space and
                # a blank line becomes a paragraph break (`\n`); literal
                # (`|`) preserves line breaks. Stops at the first line back
                # at or below base_indent (or EOF).
                folded = val.startswith(">")
                strip_trailing = val.endswith("-")
                body_lines = []
                while i < n:
                    raw2 = lines[i]
                    if not raw2.strip():
                        body_lines.append("")
                        i += 1
                        continue
                    ind2 = indent_of(raw2)
                    if ind2 <= base_indent:
                        break
                    body_lines.append(raw2.strip() if folded else raw2[base_indent + 2:] if len(raw2) >= base_indent + 2 else raw2.strip())
                    i += 1
                # trim trailing blank lines (both styles clip/strip trailing
                # newlines the same way for this engine's purposes)
                while body_lines and body_lines[-1] == "":
                    body_lines.pop()
                if folded:
                    # YAML fold: join paragraphs (consecutive non-blank line
                    # runs) with "\n\n"; within a paragraph, lines join with " ".
                    paragraphs = []
                    cur = []
                    for bl in body_lines:
                        if bl == "":
                            if cur:
                                paragraphs.append(" ".join(cur))
                                cur = []
                        else:
                            cur.append(bl)
                    if cur:
                        paragraphs.append(" ".join(cur))
                    text_val = "\n\n".join(paragraphs)
                else:
                    text_val = "\n".join(body_lines)
                if not strip_trailing and text_val:
                    text_val += "\n"
                result[key] = text_val
                continue
            if val == "":
                # Either a nested mapping or a block list on following lines.
                # Skip blank / comment-only lines when peeking ahead (a
                # comment between `key:` and its indented block is legal
                # YAML and appears in this repo's own entities.yaml).
                peek = i
                while peek < n:
                    candidate = strip_comment(lines[peek])
                    if candidate.strip():
                        break
                    peek += 1
                if peek < n:
                    nxt = strip_comment(lines[peek])
                    if nxt.strip().startswith("- ") and indent_of(nxt) >= base_indent:
                        items, i = parse_list(peek, indent_of(nxt))
                        result[key] = items
                        continue
                    if nxt.strip() and indent_of(nxt) > base_indent:
                        sub, i = parse_block(peek, indent_of(nxt))
                        result[key] = sub
                        continue
                result[key] = None
            else:
                # Flow-style EMPTY mapping: `key: {}` -> {} (PyYAML parity).
                # The template-skeleton entities.yaml ships exactly
                # `entities: {}`; before this branch existed the value fell
                # through to the scalar path as the literal STRING "{}" and
                # crashed load_entities_catalog's `.items()` downstream.
                # Non-empty flow mappings (`{a: 1}`) remain OUTSIDE the
                # supported subset (nothing in this engine's generated or
                # hand-authored files uses them); `[]` already parses to an
                # empty list via _split_inline_list below.
                if val.startswith("{") and val.endswith("}") and not val[1:-1].strip():
                    result[key] = {}
                    continue
                inline = _split_inline_list(val)
                result[key] = inline if inline is not None else val.strip("'\"")
        return result, i

    def parse_list(idx, item_indent):
        items = []
        i = idx
        n = len(lines)
        while i < n:
            raw = strip_comment(lines[i])
            if not raw.strip():
                i += 1
                continue
            ind = indent_of(raw)
            if ind < item_indent or not raw.strip().startswith("- "):
                break
            after_dash = raw.strip()[2:]
            if ":" in after_dash and not after_dash.strip().startswith("["):
                # a mapping item: "- key: value" then possibly more indented keys
                fake_line = " " * (ind + 2) + after_dash
                sub, i2 = parse_block_from_lines([fake_line] + lines[i + 1:], ind + 2)
                items.append(sub)
                i = i + i2
            else:
                items.append(after_dash.strip().strip("'\""))
                i += 1
        return items, i

    def parse_block_from_lines(sublines, base_indent):
        saved = lines[:]
        lines[:] = sublines
        try:
            d, consumed = parse_block(0, base_indent)
        finally:
            lines[:] = saved
        return d, max(consumed - 1, 0)

    top, _ = parse_block(0, 0)
    return top


def load_yaml_file(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        text = fh.read()
    return _parse_shallow_yaml(text)


# ---------------------------------------------------------------------------
# Extended derivation parsing: entities / bundle / summary / origin_max, on
# top of the existing tier/consumed_status/verified parsing above. Reuses
# _extract_derivation / _top_level_keys / _verified_block -- no duplication.
# ---------------------------------------------------------------------------

def parse_full_derivation(text):
    """Extended derivation dict for the packet machinery:
    {has_block, tier, tier_assumed, consumed_status, verified_at,
     origin_max, entities, bundle, summary}
    origin_max missing/unparseable -> 'unknown' (F-3 default-deny, matching
    origin.py's own conservative default). entities/bundle missing -> [].
    """
    base = parse_derivation(text)
    region = _extract_derivation(text)
    if region is None:
        base.update({"origin_max": "unknown", "entities": [], "bundle": [], "summary": ""})
        return base
    keys = _top_level_keys(region)
    origin_max_val = keys.get("origin_max")
    if origin_max_val not in _ORIGIN_RANK:
        origin_max_val = "unknown"
    entities = _list_field(region, "entities")
    bundle = _list_field(region, "bundle")
    summary = keys.get("summary", "") or ""
    if summary.startswith('"') and summary.endswith('"') and len(summary) >= 2:
        summary = summary[1:-1]
    base.update({
        "origin_max": origin_max_val,
        "entities": entities,
        "bundle": bundle,
        "summary": summary,
    })
    return base


def _list_field(region_lines, field):
    """Extract a top-level `field: [...]` inline list, or a block-list form
    (`field:` followed by indented `- item` lines), from a derivation region.
    Returns [] if absent/empty/unparseable (fail-safe: never crashes selection)."""
    n = len(region_lines)
    for i, ln in enumerate(region_lines):
        s = ln.strip()
        if ln[:1] in (" ", "\t"):
            continue
        if not s.startswith(field + ":"):
            continue
        rest = s[len(field) + 1:].strip()
        if rest.startswith("[") :
            # inline list, possibly wrapping isn't supported (fixtures/real
            # views keep it on one line per repo convention observed).
            inline = _split_inline_list(rest)
            return inline if inline is not None else []
        if rest:
            return []  # scalar value under this key name -- not a list
        # block-list form on following indented lines
        items = []
        j = i + 1
        while j < n:
            ln2 = region_lines[j]
            if not ln2.strip():
                j += 1
                continue
            if ln2[:1] not in (" ", "\t"):
                break
            item = ln2.strip()
            if item.startswith("- "):
                items.append(item[2:].strip().strip("'\""))
                j += 1
            else:
                break
        return items
    return []


# ---------------------------------------------------------------------------
# entities.yaml alias-based selection (mechanical, case-insensitive, no LLM).
# ---------------------------------------------------------------------------

def load_entities_catalog(root):
    """Parses deploy/entities.yaml's `entities:` map into
    {entity_name: {"aliases": [...], "views": [...], "cascades_to": [...]}},
    deterministic order preserved as encountered in the file (dict insertion
    order, Python 3.7+ guarantee)."""
    path = os.path.join(root, "deploy", "entities.yaml")
    if not os.path.isfile(path):
        return {}
    data = load_yaml_file(path)
    # Empty-vocabulary shapes, PyYAML-parity by construction: `entities: {}`
    # (the template-skeleton form) parses to {} via _parse_shallow_yaml's
    # flow-empty-mapping branch; a BARE `entities:` key with no value parses
    # to None (exactly what yaml.safe_load returns for it) and the `or {}`
    # below coerces it to an empty catalog -- the same None->{} coercion every
    # PyYAML-based sibling reader uses (catalog.py's _entities(),
    # backfill-derivation.py's load_entity_views(), staleness.py's
    # _load_alias_map()). Both shapes mean "no entities defined yet", never a
    # crash.
    ents = data.get("entities") or {}
    if not isinstance(ents, dict):
        # scalar/list garbage under `entities:` -- degrade to an empty catalog
        # (same isinstance guard catalog.py's _entities() and staleness.py's
        # _load_alias_map() apply), never a crash.
        return {}
    out = {}
    for name, body in ents.items():
        body = body or {}
        out[name] = {
            "aliases": list(body.get("aliases") or []),
            "views": list(body.get("views") or []),
            "cascades_to": list(body.get("cascades_to") or []),
        }
    return out


def _tokenize(text):
    return [t for t in _re.split(r"[^a-z0-9\-]+", (text or "").lower()) if t]


def select_seed_views(descriptor_text, entities_catalog):
    """descriptor text -> entity terms via alias/name token match
    (case-insensitive, mechanical) -> the entity's views. Deterministic
    order: entities in catalog order, views in each entity's declared order,
    de-duplicated on first occurrence. Returns (seed_views_in_order, matched_entities)."""
    tokens = set(_tokenize(descriptor_text))
    matched = []
    seed = []
    seen = set()
    for name, body in entities_catalog.items():
        names_to_check = [name] + body["aliases"]
        hit = any(t in tokens for alias in names_to_check for t in _tokenize(alias))
        if hit:
            matched.append(name)
            for v in body["views"]:
                if v not in seen:
                    seen.add(v)
                    seed.append(v)
    return seed, matched


# ---------------------------------------------------------------------------
# Bundle closure (ECO-5): BFS over derivation `bundle:` edges, visited-set
# dedup, guaranteed termination, cycles reported by name (never a hang/
# silent-truncate).
# ---------------------------------------------------------------------------

def bundle_closure(root, seed_views):
    """BFS closure over the `bundle:` edges starting at seed_views.
    Returns {"order": [...visited views in BFS order, seeds first...],
             "cycles": [[v1, v2, ..., v1], ...]} -- cycles is a list of
    named cycle paths (each starting and ending on the same view) detected
    during the walk; the walk still terminates and completes regardless."""
    visited = []
    visited_set = set()
    cycles = []
    queue = list(dict.fromkeys(seed_views))  # dedup seeds, stable order
    # parent-chain tracking for cycle-path naming (best-effort, first path found)
    discovered_via = {}
    for s in queue:
        discovered_via[s] = [s]

    idx = 0
    # Use an explicit queue with a cap of len(all possible views) traversals
    # per edge to guarantee termination even under pathological input --
    # visited-set dedup below is what actually guarantees termination; this
    # is a defense-in-depth ceiling only.
    frontier = list(queue)
    while frontier:
        v = frontier.pop(0)
        if v in visited_set:
            continue
        visited_set.add(v)
        visited.append(v)
        path, ok = _read_view_text(root, v)
        if not ok:
            continue  # unreadable/missing view: nothing to expand, not fatal here
        deriv = parse_full_derivation(path)
        for edge in deriv["bundle"]:
            chain = discovered_via.get(v, [v]) + [edge]
            if edge in visited_set or edge in frontier:
                if edge in chain[:-1]:
                    cycles.append(chain)
                continue
            if edge == v:
                cycles.append([v, v])
                continue
            discovered_via[edge] = chain
            frontier.append(edge)

    return {"order": visited, "cycles": cycles}


def _read_view_text(root, rel_path):
    try:
        resolved = resolve_within(root, os.path.join(root, rel_path))
    except ValueError:
        return "", False
    try:
        with open(resolved, "r", encoding="utf-8") as fh:
            return fh.read(), True
    except (OSError, UnicodeDecodeError):
        return "", False


# ---------------------------------------------------------------------------
# Budget (byte-fit reporting + T1 build/fix overflow refusal).
# ---------------------------------------------------------------------------

DEFAULT_BUDGET_BYTES = 262144


def _lf_bytes_text(text):
    """LF-normalized byte length (ECO-3 convention: checkout-invariant)."""
    return len(text.encode("utf-8").replace(b"\r\n", b"\n"))


TRUNCATION_BANNER_FMT = "TRUNCATION-BANNER: summary truncated to fit byte budget (%d -> %d bytes)"


def truncate_summary(summary, max_bytes):
    """Deterministic truncation: cut on a UTF-8-safe boundary, append an
    explicit banner recording the truncation (never silent)."""
    raw = summary.encode("utf-8")
    if len(raw) <= max_bytes:
        return summary, False
    cut = raw[:max_bytes]
    # back off until valid utf-8 (never split a multi-byte codepoint)
    while cut:
        try:
            text = cut.decode("utf-8")
            break
        except UnicodeDecodeError:
            cut = cut[:-1]
    else:
        text = ""
    return text, True


# ---------------------------------------------------------------------------
# Taint-quarantine (ECO-7).
# ---------------------------------------------------------------------------

TAINTED_ORIGINS = ("external-scrape", "unknown")


def taint_quarantine(task_type, views_with_deriv):
    """views_with_deriv: [(path, deriv_dict), ...]. Returns
    {"included": [...], "excluded": [(path, origin), ...], "banner_paths": [...]}
    per ECO-7: build/fix EXCLUDES tainted views regardless of tier; verify/
    recon INCLUDES them with a banner."""
    included = []
    excluded = []
    banner_paths = []
    for path, deriv in views_with_deriv:
        tainted = deriv["origin_max"] in TAINTED_ORIGINS
        if tainted and task_type in ("build", "fix"):
            excluded.append((path, deriv["origin_max"]))
        else:
            included.append((path, deriv))
            if tainted:
                banner_paths.append(path)
    return {"included": included, "excluded": excluded, "banner_paths": banner_paths}


def taint_banner(path, origin_val):
    return "TAINT-BANNER: %s carries origin_max=%s (included; not a build/fix packet)" % (path, origin_val)


# ---------------------------------------------------------------------------
# Descriptor schema.
# ---------------------------------------------------------------------------

VALID_TASK_TYPES = ("build", "fix", "verify", "recon")


def build_descriptor(text=None, task_type=None, required_views=None, profile_tools=None,
                     descriptor_claims=None, views_override=None,
                     surfaces=None, touched_layers=None, manifest_exempt=None, tier=None):
    """Normalizes CLI/YAML inputs into the descriptor schema:
    {text, task_type, required_views, profile: {tools: []}, descriptor_claims,
     surfaces, touched_layers, manifest_exempt, tier}
    task_type defaults to 'recon' (fail-safe: most banner-heavy, least-
    privileged type for SELECTION) when unspecified, per the design doc.

    surfaces/touched_layers/manifest_exempt/tier are the OPTIONAL descriptor
    keys the behavioral-manifest gate reads (manifest-format.md Section 12's
    assemble.py bullet) -- additive, all default to empty/None, so any
    pre-existing caller that never supplies them builds the exact same
    descriptor shape it always did. The GATE (assemble_packet's own
    behavioral-manifest check, not this constructor) is what makes their
    absence consequential for build/fix task types."""
    tt = task_type or "recon"
    if tt not in VALID_TASK_TYPES:
        tt = "recon"
    req = list(required_views or [])
    if views_override:
        for v in views_override:
            if v not in req:
                req.append(v)
    return {
        "text": text or "",
        "task_type": tt,
        "required_views": req,
        "profile": {"tools": list((profile_tools or []))},
        "descriptor_claims": dict(descriptor_claims or {}),
        "surfaces": list(surfaces or []),
        "touched_layers": list(touched_layers or []),
        "manifest_exempt": manifest_exempt,
        "tier": tier,
    }


def load_descriptor_yaml(path, views_override=None):
    data = load_yaml_file(path)
    profile = data.get("profile") or {}
    tools = profile.get("tools") if isinstance(profile, dict) else []
    return build_descriptor(
        text=data.get("text", ""),
        task_type=data.get("task_type"),
        required_views=data.get("required_views") or [],
        profile_tools=tools,
        descriptor_claims=data.get("descriptor_claims") or {},
        views_override=views_override,
        surfaces=data.get("surfaces") or [],
        touched_layers=data.get("touched_layers") or [],
        manifest_exempt=data.get("manifest_exempt"),
        tier=data.get("tier"),
    )


# ---------------------------------------------------------------------------
# Origin-less inclusion guard (structural): any file without a parseable
# derivation block is refused inclusion in a credentialed-profile packet,
# and counts `unknown` in every packet's origin_max computation.
# ---------------------------------------------------------------------------

def _effective_origin_for_packet(deriv):
    """origin_max contribution of a view: 'unknown' if it has no parseable
    derivation block (origin-less inclusion guard), else its own origin_max."""
    if not deriv["has_block"]:
        return "unknown"
    return deriv["origin_max"]


# ---------------------------------------------------------------------------
# Staleness (ECO-1 negative half) -- reuse check-derivation.stale_verified_check,
# never duplicate the diff logic.
# ---------------------------------------------------------------------------

def check_staleness(root, rel_path, working_text):
    """Returns (verdict, ) where verdict in
    {SV_CLEAN, SV_STALE, SV_INCONCLUSIVE, SV_SKIP} -- see check-derivation.py.
    HEAD text is fetched the same way check-derivation.py does (git show)."""
    cd = _check_derivation()
    head_text = cd._git_show_head(root, rel_path)
    return cd.stale_verified_check(working_text, head_text)


# ---------------------------------------------------------------------------
# Behavioral-manifest gate (manifest-format.md Section 12's assemble.py
# bullet: "assemble.py refuses to assemble a build or fix packet that lacks
# gate-satisfying manifest coverage for its declared surfaces ... declare-
# or-exempt, tier-restricted, fail-closed"). NAMING: this is the
# *behavioral* manifest -- never the dispatch-manifest.json transport
# artifact or the execution engine's parallel-build result manifest (the
# harness's three-way naming firewall, manifest-format.md Section 1).
#
# Evaluated inside assemble_packet BEFORE closure assembly (this section's
# entry point is _behavioral_manifest_gate, called first thing in
# assemble_packet). ONLY task_type in ("build", "fix") ever refuses on this
# gate -- "verify"/"recon" are read-only and advisory-only here, matching
# how the pre-existing F12/staleness/taint-quarantine gates already scope
# their own refusal branches to build/fix (see assemble_packet below).
#
# DESIGN DECISION: manifest BODIES are never inlined into the packet --
# packets are byte-budgeted (ECO-3) and the execution engine's Builder-
# prompt Manifests block already mandates reading manifests from disk
# before building; this gate only guarantees the touched layers EXIST at a
# tier-satisfying status and POINTS at their file paths (path-pointers
# only). Path-pointers also means no new origin/taint content ever enters
# the packet via this gate -- if a future change inlines manifest text, it
# must route through the origin/taint machinery like any other view, never
# bypass it by virtue of being "just a manifest".
# ---------------------------------------------------------------------------

REASON_MG_NEITHER = ("behavioral-manifest gate: build/fix descriptor declares neither surfaces "
                     "nor manifest_exempt (declare-or-exempt; omission is not a bypass)")
REASON_MG_AMBIGUOUS = "behavioral-manifest gate: ambiguous: declares both surfaces and manifest_exempt"
REASON_MG_EXEMPT_NOT_T4 = "behavioral-manifest gate: manifest_exempt is T4-only (doctrine gate table); tier=%s"
REASON_MG_NO_TIER = "behavioral-manifest gate: surfaces declared without a valid tier (T1-T4); tier=%s"
REASON_MG_NO_TOUCHED_LAYERS = ("behavioral-manifest gate: surfaces declared without touched_layers "
                               "-- the gate is touch-based")
REASON_MG_INDEX_UNREADABLE = ("behavioral-manifest gate: MANIFEST-INDEX.md missing or unparseable "
                              "for surface %s")
REASON_MG_LAYER_STATUS = ("behavioral-manifest gate: surface %s layer %s status %s -- required %s "
                          "(tier %s)")
REASON_MG_FRONTMATTER_MISMATCH = ("behavioral-manifest gate: INDEX layer '%s' references %s whose "
                                  "manifest: is '%s' -- INDEX/file mismatch, fail-closed")
REASON_MG_FRONTMATTER_UNREADABLE = ("behavioral-manifest gate: INDEX layer '%s' references %s but "
                                    "the file is missing or unreadable -- fail-closed")

MANIFEST_VALID_TIERS = ("T1", "T2", "T3", "T4")
_MANIFEST_CERTIFIED_LIKE = ("CERTIFIED", "LIVE")
_MANIFEST_EXTRACTED_LIKE = ("EXTRACTED", "CERTIFIED", "LIVE")
_MANIFEST_NEVER_SATISFIES = ("MISSING", "SUPERSEDED")  # never satisfy any tier, incl. T4


def _manifest_required_display(tier):
    """Human-readable statement of what satisfies `tier`'s touched-layer
    requirement (manifest-format.md Section 7's gate table), used in
    behavioral-manifest gate refusal reasons."""
    if tier in ("T1", "T2"):
        return "CERTIFIED|LIVE"
    if tier == "T3":
        return "EXTRACTED|CERTIFIED|LIVE"
    if tier == "T4":
        return "any status except MISSING/SUPERSEDED (T4 gate is trivially open)"
    return "a recognized tier (T1-T4)"


def _manifest_layer_ok(tier, status):
    """Tier-dependent pass/fail for one touched layer's INDEX status
    (manifest-format.md Section 7 gate table). SUPERSEDED never satisfies
    at ANY tier; MISSING (no manifest yet) never satisfies at any tier
    either -- T4's "any status passes" is trivially open EXCEPT for these
    two, since neither names a manifest a T4 build could even point at.
    Returns (ok: bool, required_display: str)."""
    required_display = _manifest_required_display(tier)
    if status in _MANIFEST_NEVER_SATISFIES:
        return False, required_display
    if tier == "T4":
        return True, required_display
    if tier == "T3":
        return status in _MANIFEST_EXTRACTED_LIKE, required_display
    if tier in ("T1", "T2"):
        return status in _MANIFEST_CERTIFIED_LIKE, required_display
    return False, required_display


def _extract_fenced_yaml_block(text):
    """Lines inside the first fenced code block (```...```). A small port
    of check-manifest.py's extract_index_yaml_block (manifest-format.md
    Section 6: MANIFEST-INDEX.md holds "a single YAML block" in a fenced
    code block) -- COPIED rather than imported, matching this file's own
    documented no-import-time-coupling-to-sibling-scripts convention (see
    the module docstring's rationale for check-derivation.py, and
    _load_sibling below). The block's CONTENT is parsed by THIS file's own
    _parse_shallow_yaml -- reusing check-manifest.py's INDEX-parsing
    APPROACH per the build instructions, not inventing (or importing) a
    third parser."""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            if start is None:
                start = i
            else:
                return lines[start + 1:i]
    return None


def _load_manifest_index_layers(root, index_rel):
    """Reads manifests/<surface>/MANIFEST-INDEX.md's fenced YAML block and
    returns its `layers` mapping (manifest-format.md Section 6), or None if
    the file is missing, unreadable, or the block/`layers` shape is
    unparseable. Path-containment via resolve_within (F10), same discipline
    as every other on-disk read in this module."""
    try:
        resolved = resolve_within(root, os.path.join(root, index_rel))
    except ValueError:
        return None
    try:
        with open(resolved, "r", encoding="utf-8-sig") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return None
    block_lines = _extract_fenced_yaml_block(text)
    if block_lines is None:
        return None
    data = _parse_shallow_yaml("\n".join(block_lines))
    layers = data.get("layers")
    if not isinstance(layers, dict):
        return None
    return layers


def _read_manifest_frontmatter_layer(root, file_rel):
    """Reads a <layer>-MANIFEST.md file's frontmatter `manifest:` field (the
    per-file layer marker, manifest-format.md Section 3) -- frontmatter is
    `---`-delimited YAML at byte 0, parsed here with this file's own
    _parse_shallow_yaml (the same reuse discipline as
    _load_manifest_index_layers above; the frontmatter-extraction SHAPE is
    COPIED from check-manifest.py's extract_frontmatter, not imported, per
    this file's documented no-import-time-coupling-to-sibling-scripts
    convention). Path-containment via resolve_within (F10), same discipline
    as every other on-disk read in this module.

    Returns (value_or_None, ok: bool). ok is False if the file is missing,
    unreadable, or has no frontmatter block at all -- callers treat ok=False
    as "cannot verify" (fail-closed in gated mode, skipped in advisory)."""
    try:
        resolved = resolve_within(root, os.path.join(root, file_rel))
    except ValueError:
        return None, False
    try:
        with open(resolved, "r", encoding="utf-8-sig") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return None, False
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, False
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None, False
    fm = _parse_shallow_yaml("\n".join(lines[1:end]))
    return fm.get("manifest"), True


def _scan_manifest_layers(root, surfaces, touched_layers):
    """Pure data-gathering: reads each surface's MANIFEST-INDEX.md and looks
    up each touched layer's status. No pass/fail decision here -- the gated
    (build/fix) and advisory (verify/recon) callers each apply their own
    threshold (tier-dependent vs fixed-CERTIFIED) over this same data.
    Returns (layers_record, index_paths, manifest_paths, unreadable_surfaces,
    file_paths):
      layers_record: {surface: {layer: status}}
      index_paths: [manifests/<surface>/MANIFEST-INDEX.md, ...]
      manifest_paths: [file: path of every touched layer found, ...]
      unreadable_surfaces: [surface, ...] whose INDEX was missing/unparseable
      file_paths: {surface: {layer: file_path_or_None}} -- the INDEX entry's
        `file:` value per touched layer, for the frontmatter cross-check
        below (INDEX-layer-key vs manifest-file-layer mismatch, cross-vendor
        review finding) -- None where the entry has no file (e.g. MISSING).
    """
    layers_record = {}
    index_paths = []
    manifest_paths = []
    unreadable_surfaces = []
    file_paths = {}
    for surface in surfaces:
        index_rel = "manifests/%s/MANIFEST-INDEX.md" % surface
        index_paths.append(index_rel)
        idx_layers = _load_manifest_index_layers(root, index_rel)
        layers_record[surface] = {}
        file_paths[surface] = {}
        if idx_layers is None:
            unreadable_surfaces.append(surface)
            continue
        for layer in touched_layers:
            entry = idx_layers.get(layer)
            status = entry.get("status") if isinstance(entry, dict) else None
            status = status or "MISSING"
            layers_record[surface][layer] = status
            file_path = entry.get("file") if isinstance(entry, dict) else None
            file_paths[surface][layer] = file_path
            if file_path:
                manifest_paths.append(file_path)
    return layers_record, index_paths, manifest_paths, unreadable_surfaces, file_paths


def _behavioral_manifest_gate(descriptor, root, task_type):
    """The behavioral-manifest gate. Evaluated BEFORE closure assembly
    (called first thing in assemble_packet). ONLY task_type in
    ("build", "fix") ever refuses here; "verify"/"recon" are advisory-only
    and NEVER refuse on this gate.

    Returns {"refusal": {"path": .., "reason": ..} or None,
             "manifest_gate": dict or None,
             "packet_header": str or None,   # gated-OPEN pass only (build/fix)
             "banners": [str, ...]}          # advisory only (verify/recon)
    """
    surfaces = list(descriptor.get("surfaces") or [])
    touched_layers = list(descriptor.get("touched_layers") or [])
    manifest_exempt = descriptor.get("manifest_exempt")
    tier = descriptor.get("tier")

    out = {"refusal": None, "manifest_gate": None, "packet_header": None, "banners": []}

    if task_type not in ("build", "fix"):
        # verify/recon: NO refusals, ever, on this gate -- the doctrine gate
        # table is a build-readiness gate; verify/recon are read-only.
        if not surfaces:
            return out  # unchanged: no manifest_gate key at all

        # Descriptor-shape rules (declare-or-exempt, tier) deliberately do
        # NOT apply to verify/recon -- no refusals, no new requirements are
        # added here. But declared-and-then-ignored gate intent must still
        # banner: silence is never correct just because this branch never
        # refuses.
        if manifest_exempt:
            out["banners"].append(
                "descriptor declares both surfaces and manifest_exempt -- "
                "contradictory declaration ignored (advisory)")
            # Fall through: the surfaces scan below proceeds exactly as if
            # manifest_exempt were absent -- it already carries no weight
            # in this branch, this banner only makes the contradiction
            # visible instead of silently dropping it.

        if not touched_layers:
            out["banners"].append(
                "surfaces declared without touched_layers -- nothing "
                "scanned (advisory, verify packets never refuse on the "
                "behavioral-manifest gate)")
            return out  # nothing to scan -- skip the scan gracefully

        layers_record, index_paths, manifest_paths, unreadable_surfaces, file_paths = \
            _scan_manifest_layers(root, surfaces, touched_layers)
        out["manifest_gate"] = {
            "mode": "advisory", "tier": tier, "surfaces": surfaces,
            "touched_layers": touched_layers, "verdict": "ADVISORY",
            "layers": layers_record, "index_paths": index_paths,
            "manifest_paths": manifest_paths,
        }
        # Surface-level unreadable INDEX (cross-vendor review finding,
        # third of its class): the gated branch refuses outright on this
        # (REASON_MG_INDEX_UNREADABLE below); advisory packets never refuse,
        # but silently dropping it here left a verify/recon packet against a
        # surface whose MANIFEST-INDEX.md is missing or unparseable with NO
        # banner at all -- every anomaly the gated path refuses on must
        # banner here, silence is never correct.
        for surface in unreadable_surfaces:
            out["banners"].append(
                "surface %s MANIFEST-INDEX.md missing or unparseable "
                "(advisory, verify packets never refuse on the "
                "behavioral-manifest gate)" % surface)
        for surface in surfaces:
            for layer in touched_layers:
                status = layers_record.get(surface, {}).get(layer)
                if status and status not in _MANIFEST_CERTIFIED_LIKE:
                    out["banners"].append(
                        "layer %s at %s -- advisory, verify packets never "
                        "refuse on the behavioral-manifest gate" % (layer, status))
                # Same INDEX-layer-key vs manifest-file-layer read as the
                # gated branch below, best-effort: advisory packets never
                # refuse on this gate, so a mismatch becomes a banner, not
                # a refusal; a missing/unreadable file also becomes a banner
                # (cross-vendor review finding: silently skipping it left
                # the gate quiet exactly when the INDEX pointed at a dead
                # file, so nothing to compare against still gets flagged).
                file_rel = file_paths.get(surface, {}).get(layer)
                if file_rel:
                    fm_layer, readable = _read_manifest_frontmatter_layer(root, file_rel)
                    if not readable:
                        out["banners"].append(
                            "layer %s file %s missing or unreadable -- INDEX "
                            "references a file that cannot be read (advisory, "
                            "verify packets never refuse on the behavioral-"
                            "manifest gate)" % (layer, file_rel))
                    elif fm_layer != layer:
                        out["banners"].append(
                            "layer %s file %s manifest: is '%s' -- INDEX/file mismatch "
                            "(advisory, verify packets never refuse on the "
                            "behavioral-manifest gate)"
                            % (layer, file_rel, fm_layer if fm_layer else "(absent)"))
        return out

    # --- build/fix: declare-or-exempt, tier-restricted, fail-closed ---
    if not surfaces and not manifest_exempt:
        out["refusal"] = {"path": "<descriptor>", "reason": REASON_MG_NEITHER}
        return out
    if surfaces and manifest_exempt:
        out["refusal"] = {"path": "<descriptor>", "reason": REASON_MG_AMBIGUOUS}
        return out

    if manifest_exempt:
        if tier != "T4":
            out["refusal"] = {"path": "<descriptor>",
                              "reason": REASON_MG_EXEMPT_NOT_T4 % (tier or "absent")}
            return out
        out["manifest_gate"] = {"mode": "exempt", "tier": "T4", "reason": manifest_exempt}
        return out

    # surfaces present (manifest_exempt absent, by the XOR checks above)
    if tier not in MANIFEST_VALID_TIERS:
        out["refusal"] = {"path": "<descriptor>",
                          "reason": REASON_MG_NO_TIER % (tier or "absent")}
        return out
    if not touched_layers:
        out["refusal"] = {"path": "<descriptor>", "reason": REASON_MG_NO_TOUCHED_LAYERS}
        return out

    layers_record, index_paths, manifest_paths, unreadable_surfaces, file_paths = \
        _scan_manifest_layers(root, surfaces, touched_layers)
    if unreadable_surfaces:
        surface = unreadable_surfaces[0]
        out["refusal"] = {"path": "manifests/%s/MANIFEST-INDEX.md" % surface,
                          "reason": REASON_MG_INDEX_UNREADABLE % surface}
        return out

    for surface in surfaces:
        for layer in touched_layers:
            status = layers_record[surface][layer]
            ok, required_display = _manifest_layer_ok(tier, status)
            if not ok:
                out["refusal"] = {
                    "path": "manifests/%s/MANIFEST-INDEX.md" % surface,
                    "reason": REASON_MG_LAYER_STATUS % (surface, layer, status, required_display, tier),
                }
                return out

            # INDEX-layer-key vs manifest-file-layer cross-check (cross-
            # vendor review finding): an INDEX entry `interaction: {file:
            # .../logic-MANIFEST.md, status: CERTIFIED, ...}` -- a layer key
            # whose referenced file's own frontmatter `manifest:` field
            # disagrees -- previously satisfied this gate on status alone.
            # This makes the gate fail-closed standalone, even if
            # check-manifest.py's own index-coherence check never ran.
            file_rel = file_paths[surface][layer]
            if not file_rel:
                out["refusal"] = {
                    "path": "manifests/%s/MANIFEST-INDEX.md" % surface,
                    "reason": REASON_MG_FRONTMATTER_UNREADABLE
                              % (layer, "(no file in INDEX entry)"),
                }
                return out
            fm_layer, readable = _read_manifest_frontmatter_layer(root, file_rel)
            if not readable:
                out["refusal"] = {
                    "path": file_rel,
                    "reason": REASON_MG_FRONTMATTER_UNREADABLE % (layer, file_rel),
                }
                return out
            if fm_layer != layer:
                out["refusal"] = {
                    "path": file_rel,
                    "reason": REASON_MG_FRONTMATTER_MISMATCH
                              % (layer, file_rel, fm_layer if fm_layer else "(absent)"),
                }
                return out

    out["manifest_gate"] = {
        "mode": "gated", "tier": tier, "surfaces": surfaces,
        "touched_layers": touched_layers, "verdict": "OPEN",
        "layers": layers_record, "index_paths": index_paths,
        "manifest_paths": manifest_paths,
    }
    header_lines = ["BEHAVIORAL-MANIFEST GATE -- %s @ %s -- OPEN" % (", ".join(surfaces), tier)]
    for surface in surfaces:
        for layer in touched_layers:
            header_lines.append("  %s / %s: %s" % (surface, layer, layers_record[surface][layer]))
    header_lines.append(
        "REQUIRED READING (not inlined; read from disk before building): " +
        ", ".join(manifest_paths + index_paths))
    out["packet_header"] = "\n".join(header_lines)
    return out


# ---------------------------------------------------------------------------
# Packet assembly (the full P4 machinery).
# ---------------------------------------------------------------------------

REASON_STALE_HARD_STOP = "stale-verified (CONTENT-3): body changed after verify stamp -- hard stop for build/fix"
REASON_TAINT_EXCLUDED_REQUIRED = "excluded by taint-quarantine but required/only-match"
REASON_ORIGIN_LESS_CREDENTIALED = "no parseable derivation block -- refused in a credentialed-profile packet"


def assemble_packet(descriptor, root, budget_bytes=DEFAULT_BUDGET_BYTES, full_paths=None,
                    allowlist_path=None):
    """The full P4 packet machinery. Returns (exit_code, result_dict).

    exit_code: 0 = packet assembled | 1 = named hard-refusal (behavioral-
    manifest gate, overflow, taint-excluded-required, staleness hard-stop) |
    2 = egress co-residency refusal | 3 = usage error (raised by caller, not
    here). The behavioral-manifest gate is evaluated first, before closure
    assembly, for build/fix task types (manifest-format.md Section 12).

    result_dict always carries (on any path that isn't a usage error):
      packet_origin_max, profile classification (granted/excluded-by-class),
      ignored_descriptor_claims, banners (list of str), and on success the
      packet text + per-view headers; on refusal, the named subject(s).

    `allowlist_path` is a self-test seam threaded straight through to
    _classify_profile_tools()/_profile_grant() (tool_grant's own injectable
    allowlist path) -- production callers never pass it, so a live assemble
    always classifies/grants against deploy/safe-allowlist.yaml.
    """
    full_paths = set(full_paths or [])
    task_type = descriptor["task_type"]
    ignored_claims = dict(descriptor.get("descriptor_claims") or {})  # F-11: recorded, never used for decisions

    # --- behavioral-manifest gate (evaluated BEFORE closure assembly) ---
    mg_result = _behavioral_manifest_gate(descriptor, root, task_type)
    if mg_result["refusal"] is not None:
        r = mg_result["refusal"]
        mg_refused = [{"path": r["path"], "reason": r["reason"]}]
        # Top-level reason mirrors the other refusal classes (taint/budget/
        # staleness): a single-finding refusal surfaces that finding's own
        # reason at top level too, not just on the itemized line below;
        # multi-finding would surface a count summary instead (the gate
        # itself only ever returns one finding today, so this is a single-
        # item list in practice, but the shape stays correct if that changes).
        mg_top_reason = (mg_refused[0]["reason"] if len(mg_refused) == 1
                         else "behavioral-manifest gate refused (%d finding(s))" % len(mg_refused))
        return 1, {
            "task_type": task_type,
            "reason": mg_top_reason,
            "refused": mg_refused,
            "ignored_descriptor_claims": ignored_claims,
        }
    manifest_gate_data = mg_result["manifest_gate"]
    manifest_packet_header = mg_result["packet_header"]
    manifest_banners = mg_result["banners"]

    entities_catalog = load_entities_catalog(root)
    seed, matched_entities = select_seed_views(descriptor["text"], entities_catalog)
    for v in descriptor["required_views"]:
        if v not in seed:
            seed.append(v)

    closure = bundle_closure(root, seed)
    all_members = closure["order"]
    seed_set = set(seed)

    # --- required-view recall check (selection-level, informational always available) ---
    missing_required = [v for v in descriptor["required_views"] if v not in all_members]

    # --- load derivations for every closure member ---
    per_view = {}
    origin_less = []
    for v in all_members:
        text, ok = _read_view_text(root, v)
        if not ok:
            per_view[v] = {"deriv": {"has_block": False, "tier": "T1", "tier_assumed": True,
                                      "consumed_status": None, "verified_at": None,
                                      "origin_max": "unknown", "entities": [], "bundle": [], "summary": ""},
                           "text": None, "unreadable": True}
            origin_less.append(v)
            continue
        deriv = parse_full_derivation(text)
        per_view[v] = {"deriv": deriv, "text": text, "unreadable": False}
        if not deriv["has_block"]:
            origin_less.append(v)

    # --- staleness (ECO-1 negative half) ---
    stale_hard_stops = []
    stale_banners = []
    for v in all_members:
        info = per_view[v]
        if info["unreadable"] or info["text"] is None:
            continue
        if not info["deriv"]["verified_at"] and info["deriv"]["consumed_status"] != "verified-consumed":
            # cheap prefilter: only bother with the git-diff staleness check
            # when the view actually claims a non-null verified state (same
            # gate check-derivation.py itself applies) -- avoids needless
            # subprocess calls for the common legacy-assumed/audit-pending case.
            pass
        verdict = check_staleness(root, v, info["text"])
        if verdict == "stale":
            if task_type in ("build", "fix"):
                stale_hard_stops.append(v)
            else:
                stale_banners.append(v)

    if task_type in ("build", "fix") and stale_hard_stops:
        return 1, {
            "task_type": task_type,
            "refused": [{"path": v, "reason": REASON_STALE_HARD_STOP} for v in sorted(stale_hard_stops)],
            "ignored_descriptor_claims": ignored_claims,
        }

    # --- F12 consumed_status gate (SERVE-T1-REFUSAL foundation, composed in) ---
    # The design doc frames the pre-existing T1-refusal gate (gate_view() /
    # SERVE-T1-REFUSAL, 18/18, semantics UNCHANGED) as "the foundation" this
    # packet machinery builds on -- but the machinery's own closure/taint/
    # egress logic never independently called it, so a build/fix descriptor
    # whose only offense is an un-cleared consumed_status (audit-pending /
    # legacy-assumed / missing) but whose origin_max is CLEAN (human) would
    # incorrectly emit a packet (ECO-7 taint-quarantine alone does not catch
    # it -- origin and consumed_status are independent axes; the golden
    # descriptor build-gateway-adapters-mcp-cutover exercises exactly this:
    # origin_max human, consumed_status legacy-assumed). Composed here, same
    # gate_view()/parse_derivation() functions, atomic refusal (whole packet
    # aborts on ANY offending closure member), never a partial packet --
    # identical discipline to the original assemble()'s "mixed request
    # refuses atomically" rule.
    if task_type in ("build", "fix"):
        f12_refused = []
        for v in all_members:
            info = per_view[v]
            if info["unreadable"]:
                continue  # origin-less inclusion guard covers unreadable/no-block members
            ok, reason = gate_view(info["deriv"])
            if not ok:
                f12_refused.append((v, reason))
        if f12_refused:
            return 1, {
                "task_type": task_type,
                "refused": [{"path": v, "reason": reason} for v, reason in sorted(f12_refused)],
                "ignored_descriptor_claims": ignored_claims,
            }

    # --- taint-quarantine (ECO-7) ---
    views_with_deriv = [(v, per_view[v]["deriv"]) for v in all_members]
    tq = taint_quarantine(task_type, views_with_deriv)
    excluded_taint = tq["excluded"]  # [(path, origin)]
    excluded_taint_paths = {p for p, _o in excluded_taint}

    # only-match/required check for excluded-but-needed views: a view is
    # "required/only-match" if (a) it's an explicit --views/required_views
    # entry, or (b) removing all excluded-taint views from the SEED selection
    # would leave zero seed views left (i.e. it was the ONLY view satisfying
    # the descriptor's alias-derived selection -- test-plan case (3)/(2)).
    # Case (1) (mixed-origin, >1 seed match) is NOT a conflict: the tainted
    # seed is simply excluded and the human seed still satisfies selection.
    remaining_seed = [v for v in seed if v not in excluded_taint_paths]
    seed_would_be_empty = bool(seed) and not remaining_seed
    conflict = []
    for p, o in excluded_taint:
        is_explicit_required = p in descriptor["required_views"]
        is_only_seed_match = (p in seed_set) and seed_would_be_empty
        if is_explicit_required or is_only_seed_match:
            conflict.append((p, o))
    if conflict:
        return 1, {
            "task_type": task_type,
            "refused": [{"path": p, "origin": o, "reason": REASON_TAINT_EXCLUDED_REQUIRED}
                        for p, o in sorted(conflict)],
            "ignored_descriptor_claims": ignored_claims,
        }

    included_views = [v for v in all_members if v not in excluded_taint_paths]

    # --- origin-less inclusion guard + profile classification/grant ---
    # context_items: the included views' own origin_max (post taint-quarantine
    # exclusion) -- this is what the TCB grant decision reasons over, per the
    # design doc's seam-swap instruction. Origin-less members already count as
    # "unknown" via _effective_origin_for_packet, matching the origin-less
    # inclusion guard's own conservative default.
    profile_tools = descriptor["profile"]["tools"]
    tool_class = _classify_profile_tools(profile_tools, allowlist_path=allowlist_path)
    context_items = [{"id": v, "origin": _effective_origin_for_packet(per_view[v]["deriv"])}
                     for v in included_views]
    grant_result = _profile_grant(profile_tools, context_items, descriptor["descriptor_claims"],
                                  allowlist_path=allowlist_path)
    credentialed = grant_result["session_flags"]["credentialed"]
    # F-11: descriptor_claims is recorded via tool_grant's own
    # ignored_descriptor_claims (the real source of truth now); the older
    # ignored_claims local (the raw descriptor_claims dict, unfiltered) is
    # kept for the envelope's `ignored_descriptor_claims` key for backward
    # shape-compat with callers that expect the raw dict there -- the grant
    # block below carries tool_grant's own filtered/annotated version.

    if credentialed:
        origin_less_included = [v for v in included_views if v in origin_less]
        if origin_less_included:
            return 1, {
                "task_type": task_type,
                "refused": [{"path": v, "reason": REASON_ORIGIN_LESS_CREDENTIALED}
                            for v in sorted(origin_less_included)],
                "ignored_descriptor_claims": ignored_claims,
                "profile": tool_class,
                "grant": grant_result,
            }

    # --- packet origin_max (origin-less members count as unknown) ---
    packet_origin_values = [_effective_origin_for_packet(per_view[v]["deriv"]) for v in included_views]
    packet_origin_max = _origin_max(packet_origin_values)

    # --- egress co-residency ---
    # credentialed now derives from tool_grant's grant() session_flags (the
    # real TCB grant decision over the full F-1..F-16 catalog), not the old
    # two-pattern placeholder -- semantics unchanged (tainted packet +
    # credentialed profile -> refuse), source of truth upgraded.
    if credentialed and packet_origin_max not in TRUSTED_ORIGINS:
        offending = sorted(set(tool_class["credential"]) | set(tool_class["egress"]))
        return 2, {
            "task_type": task_type,
            "refused_egress": True,
            "packet_origin_max": packet_origin_max,
            "offending_tools": offending,
            "reason": "egress co-residency: tainted packet (origin_max=%s) + credentialed profile" % packet_origin_max,
            "ignored_descriptor_claims": ignored_claims,
            "profile": tool_class,
            "grant": grant_result,
        }

    # --- role shape: seeds full-text, closure members one-hop summary
    #     (or full-text if named via --full) ---
    banners = list(manifest_banners)  # behavioral-manifest gate ADVISORY banners (verify/recon only)
    for v in tq["banner_paths"]:
        if v in included_views:
            banners.append(taint_banner(v, per_view[v]["deriv"]["origin_max"]))
    for v in sorted(stale_banners):
        banners.append("STALE-BANNER (verify/recon): %s body changed after verify stamp" % v)

    headers = []
    bodies = []
    total_bytes = 0
    overflow_member = None
    for v in included_views:
        info = per_view[v]
        deriv = info["deriv"]
        is_seed_or_full = (v in seed_set) or (v in full_paths)
        if is_seed_or_full and info["text"] is not None:
            content = info["text"]
            role = "full-text"
        else:
            content = deriv["summary"] or "(no summary available)"
            role = "summary"

        header_lines = [
            "--- view: %s ---" % v,
            "role: %s" % role,
            "tier: %s%s" % (deriv["tier"], " (assumed)" if deriv["tier_assumed"] else ""),
            "origin_max: %s" % deriv["origin_max"],
            "consumed_status: %s" % (deriv["consumed_status"] or "(missing)"),
        ]
        this_banners = []
        if v in excluded_taint_paths:
            continue  # already excluded above; defensive, unreachable
        if v in tq["banner_paths"]:
            this_banners.append(taint_banner(v, deriv["origin_max"]))
        if v in stale_banners:
            this_banners.append("STALE-BANNER (verify/recon): body changed after verify stamp")

        body_bytes = _lf_bytes_text(content)
        prospective_total = total_bytes + body_bytes

        if prospective_total > budget_bytes:
            if task_type in ("build", "fix"):
                overflow_member = v
                break
            # verify/recon/T3: truncate the summary deterministically, never silent
            room = max(budget_bytes - total_bytes, 0)
            truncated, did_truncate = truncate_summary(content, room)
            if did_truncate:
                this_banners.append(TRUNCATION_BANNER_FMT % (body_bytes, len(truncated.encode("utf-8"))))
                content = truncated
                body_bytes = _lf_bytes_text(content)

        header = "\n".join(header_lines + (["REFUSAL: (none)"] if False else []))
        for b in this_banners:
            header += "\n" + b
        headers.append(header)
        bodies.append(header + "\n\n" + content)
        total_bytes += body_bytes

    if overflow_member is not None:
        return 1, {
            "task_type": task_type,
            "refused": [{"path": overflow_member,
                         "reason": "byte-budget overflow (%d bytes budget) -- full bundle or refuse, never partial" % budget_bytes}],
            "ignored_descriptor_claims": ignored_claims,
        }

    # behavioral-manifest gate: the "BEHAVIORAL-MANIFEST GATE -- ... -- OPEN"
    # block is prepended to the packet TEXT itself (path-pointers only, see
    # the gate's DESIGN DECISION comment above _behavioral_manifest_gate) --
    # a packet-level preamble, distinct from the per-view TAINT-BANNER/
    # STALE-BANNER lines embedded in each view's own header above.
    packet_sections = ([manifest_packet_header] if manifest_packet_header else []) + bodies
    packet = "\n\n====\n\n".join(packet_sections)
    result = {
        "task_type": task_type,
        "matched_entities": matched_entities,
        "seed_views": seed,
        "closure_members": all_members,
        "cycles": closure["cycles"],
        "missing_required": missing_required,
        "excluded_taint": [{"path": p, "origin": o} for p, o in sorted(excluded_taint)],
        "packet_origin_max": packet_origin_max,
        "profile": tool_class,
        "granted_profile_ok": True,
        "grant": grant_result,
        "budget_bytes": budget_bytes,
        "budget_bytes_used": total_bytes,
        "budget_fit": total_bytes <= budget_bytes,
        "banners": banners,
        "ignored_descriptor_claims": ignored_claims,
        "packet": packet,
        "views": headers,
    }
    if manifest_gate_data is not None:
        result["manifest_gate"] = manifest_gate_data
    return 0, result


# ---------------------------------------------------------------------------
# P4 packet-machinery self-test fixtures (fixture-first, additive to the
# 18 pre-existing T1-refusal cases above -- those stay unchanged).
# ---------------------------------------------------------------------------

def _mk_view(entities=None, bundle=None, tier="T1", consumed_status="verified-consumed",
             origin_max="human", summary="a summary line", verified_block=None, no_deriv=False):
    if no_deriv:
        return "---\ntitle: x\n---\nNo derivation block.\n"
    lines = ["---", "title: x", "---", "# --- derivation (engine-managed; strip region) ---",
              "schema_version: 3.2", "view: topic"]
    lines.append('summary: "%s"' % summary)
    lines.append("entities: [%s]" % ", ".join(entities or []))
    lines.append("tier: %s" % tier)
    if consumed_status is not None:
        lines.append("consumed_status: %s" % consumed_status)
    lines.append("origin_max: %s" % origin_max)
    lines.append("bundle: [%s]" % ", ".join(bundle or []))
    if verified_block:
        lines.append("verified:")
        lines.append("  status: %s" % verified_block.get("status", "passed"))
        lines.append("  at: %s" % verified_block.get("at", "2020-01-01T00:00:00Z"))
    else:
        lines.append("verified: null")
    lines.append("# --- /derivation ---")
    lines.append("")
    lines.append("Body text for %s." % (summary or "view"))
    return "\n".join(lines) + "\n"


def _write_tree(tmp, files):
    """files: {relpath: content}. Writes under tmp, returns tmp."""
    for rel, content in files.items():
        fp = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
    return tmp


def _self_test_packet_machinery():
    import tempfile
    import subprocess

    failed = 0
    total = 0

    def check(name, cond):
        nonlocal failed, total
        total += 1
        print("  %s [P4] %s" % ("ok " if cond else "XX ", name))
        if not cond:
            failed += 1

    # ---- descriptor schema ----
    d = build_descriptor(text="fix the thing", task_type=None)
    check("descriptor defaults task_type=recon when unspecified", d["task_type"] == "recon")
    d2 = build_descriptor(text="x", task_type="bogus")
    check("descriptor falls back to recon on invalid task_type", d2["task_type"] == "recon")
    d3 = build_descriptor(text="x", task_type="build", descriptor_claims={"trusted": True, "sandbox_attestation": "sig123"})
    check("descriptor_claims recorded verbatim, not interpreted", d3["descriptor_claims"] == {"trusted": True, "sandbox_attestation": "sig123"})

    # ---- entities.yaml alias selection (mechanical, deterministic) ----
    with tempfile.TemporaryDirectory() as tmp:
        _write_tree(tmp, {
            "deploy/entities.yaml": (
                "entities:\n"
                "  work-orders:\n"
                "    aliases: [wo, work-order]\n"
                "    views: [wiki/systems/schema-work-orders.md]\n"
                "  customers:\n"
                "    aliases: [customer]\n"
                "    views: [wiki/systems/schema-customers.md]\n"
            ),
        })
        cat = load_entities_catalog(tmp)
        check("entities.yaml parsed: two entities", set(cat.keys()) == {"work-orders", "customers"})
        check("entities.yaml aliases parsed", cat["work-orders"]["aliases"] == ["wo", "work-order"])
        seed, matched = select_seed_views("brief me on work-order status", cat)
        check("alias token match selects entity views", "wiki/systems/schema-work-orders.md" in seed)
        check("alias match is case-insensitive/mechanical", "work-orders" in matched)
        seed2, matched2 = select_seed_views("nothing relevant here", cat)
        check("no alias match -> empty seed", seed2 == [] and matched2 == [])

    # ---- empty-vocabulary shapes (template-skeleton portability) ----
    # The shipped template skeleton's entities.yaml is exactly `entities: {}`
    # + `known_holes: []` -- a fresh instance must load it as an EMPTY catalog
    # on day 1, never crash (_parse_shallow_yaml's flow-empty-mapping branch).
    with tempfile.TemporaryDirectory() as tmp:
        _write_tree(tmp, {
            "deploy/entities.yaml": (
                "# entities.yaml -- governed entity vocabulary (skeleton).\n"
                "entities: {}\n"
                "\n"
                "known_holes: []\n"
            ),
        })
        parsed = load_yaml_file(os.path.join(tmp, "deploy", "entities.yaml"))
        check("skeleton shape: `entities: {}` parses to an empty DICT (not the string '{}')",
              parsed.get("entities") == {} and isinstance(parsed.get("entities"), dict))
        check("skeleton shape: `known_holes: []` parses to an empty LIST",
              parsed.get("known_holes") == [] and isinstance(parsed.get("known_holes"), list))
        check("skeleton shape: load_entities_catalog -> empty catalog, no crash",
              load_entities_catalog(tmp) == {})
        seed_sk, matched_sk = select_seed_views("anything at all", load_entities_catalog(tmp))
        check("skeleton shape: selection over the empty catalog -> empty seed, no crash",
              seed_sk == [] and matched_sk == [])
    # Bare `entities:` (no value at all): parses to None -- the exact value
    # yaml.safe_load returns for a valueless key -- and load_entities_catalog's
    # `or {}` coerces it to an empty catalog (the same None->{} coercion every
    # PyYAML-based sibling reader applies; see the comment in
    # load_entities_catalog itself).
    with tempfile.TemporaryDirectory() as tmp:
        _write_tree(tmp, {"deploy/entities.yaml": "entities:\n"})
        parsed_bare = load_yaml_file(os.path.join(tmp, "deploy", "entities.yaml"))
        check("bare `entities:` (no value) parses to None (PyYAML parity)",
              parsed_bare.get("entities") is None)
        check("bare `entities:` -> load_entities_catalog coerces to empty catalog, no crash",
              load_entities_catalog(tmp) == {})
    # Scalar garbage under `entities:` degrades to an empty catalog too (the
    # isinstance guard, matching catalog.py/_entities and staleness.py/
    # _load_alias_map conventions).
    with tempfile.TemporaryDirectory() as tmp:
        _write_tree(tmp, {"deploy/entities.yaml": "entities: not-a-mapping\n"})
        check("scalar value under `entities:` degrades to empty catalog (isinstance guard)",
              load_entities_catalog(tmp) == {})

    # ---- ECO-5 bundle closure: chain, diamond, 3-cycle, self-loop ----
    import time as _time

    with tempfile.TemporaryDirectory() as tmp:
        _write_tree(tmp, {
            "wiki/a.md": _mk_view(bundle=["wiki/b.md"], summary="a"),
            "wiki/b.md": _mk_view(bundle=["wiki/c.md"], summary="b"),
            "wiki/c.md": _mk_view(bundle=[], summary="c"),
        })
        t0 = _time.time()
        result = bundle_closure(tmp, ["wiki/a.md"])
        check("chain closure visits all three, terminates <1s",
              result["order"] == ["wiki/a.md", "wiki/b.md", "wiki/c.md"] and (_time.time() - t0) < 1.0)
        check("chain closure: no cycles reported", result["cycles"] == [])

    with tempfile.TemporaryDirectory() as tmp:
        _write_tree(tmp, {
            "wiki/a.md": _mk_view(bundle=["wiki/b.md", "wiki/c.md"], summary="a"),
            "wiki/b.md": _mk_view(bundle=["wiki/d.md"], summary="b"),
            "wiki/c.md": _mk_view(bundle=["wiki/d.md"], summary="c"),
            "wiki/d.md": _mk_view(bundle=[], summary="d"),
        })
        t0 = _time.time()
        result = bundle_closure(tmp, ["wiki/a.md"])
        check("diamond closure visits all four exactly once (dedup)",
              sorted(result["order"]) == ["wiki/a.md", "wiki/b.md", "wiki/c.md", "wiki/d.md"]
              and len(result["order"]) == 4)
        check("diamond closure terminates <1s", (_time.time() - t0) < 1.0)
        check("diamond closure: no cycles reported", result["cycles"] == [])

    with tempfile.TemporaryDirectory() as tmp:
        _write_tree(tmp, {
            "wiki/a.md": _mk_view(bundle=["wiki/b.md"], summary="a"),
            "wiki/b.md": _mk_view(bundle=["wiki/c.md"], summary="b"),
            "wiki/c.md": _mk_view(bundle=["wiki/a.md"], summary="c"),
        })
        t0 = _time.time()
        result = bundle_closure(tmp, ["wiki/a.md"])
        check("3-cycle terminates <1s (no hang)", (_time.time() - t0) < 1.0)
        check("3-cycle visits all three members despite the cycle",
              sorted(result["order"]) == ["wiki/a.md", "wiki/b.md", "wiki/c.md"])
        check("3-cycle reports the cycle by name", len(result["cycles"]) >= 1)

    with tempfile.TemporaryDirectory() as tmp:
        _write_tree(tmp, {
            "wiki/a.md": _mk_view(bundle=["wiki/a.md"], summary="a"),
        })
        t0 = _time.time()
        result = bundle_closure(tmp, ["wiki/a.md"])
        check("self-loop terminates <1s (no hang)", (_time.time() - t0) < 1.0)
        check("self-loop visits the single member once", result["order"] == ["wiki/a.md"])
        check("self-loop reports the cycle by name", any(c == ["wiki/a.md", "wiki/a.md"] for c in result["cycles"]))

    # ---- ECO-7 taint-quarantine: four verbatim fixture cases ----
    with tempfile.TemporaryDirectory() as tmp:
        _write_tree(tmp, {
            "deploy/entities.yaml": "entities:\n  work-orders:\n    aliases: [wo]\n    views: [wiki/human.md, wiki/scraped.md]\n",
            "wiki/human.md": _mk_view(entities=["work-orders"], origin_max="human", summary="human view"),
            "wiki/scraped.md": _mk_view(entities=["work-orders"], origin_max="external-scrape", summary="scraped view"),
        })
        # (1) T1 build/fix mixed-origin: packet must contain only human-origin view
        # manifest_exempt+T4: this fixture tests taint-quarantine (ECO-7), not the
        # behavioral-manifest gate -- exempt it so the gate under test is reached.
        desc = build_descriptor(text="wo status", task_type="build",
                                manifest_exempt="P4 self-test fixture, no product surface", tier="T4")
        code, res = assemble_packet(desc, tmp)
        check("ECO-7(1) T1 build mixed-origin: exit 0",
              code == 0 and bool(res.get("closure_members")))
        check("ECO-7(1) scraped view listed as excluded-taint",
              any(o["path"] == "wiki/scraped.md" and o["origin"] == "external-scrape"
                  for o in res.get("excluded_taint", [])))
        check("ECO-7(1) scraped view excluded from packet body", "scraped view" not in res.get("packet", ""))
        check("ECO-7(1) human view included in packet body", "human view" in res.get("packet", ""))

    with tempfile.TemporaryDirectory() as tmp:
        # (2) T3 build/fix descriptor matching ONLY an external-scrape view -> exit 1, named, not bannered
        _write_tree(tmp, {
            "deploy/entities.yaml": "entities:\n  scraped-ent:\n    aliases: [scr]\n    views: [wiki/scraped.md]\n",
            "wiki/scraped.md": _mk_view(entities=["scraped-ent"], tier="T3", origin_max="external-scrape", summary="scraped only"),
        })
        desc = build_descriptor(text="scr topic", task_type="fix",
                                manifest_exempt="P4 self-test fixture, no product surface", tier="T4")
        code, res = assemble_packet(desc, tmp)
        check("ECO-7(2) T3 build/fix only-tainted-match: exit 1 (quarantine keys on task type, not tier)", code == 1)
        check("ECO-7(2) names the view and origin", any(r.get("path") == "wiki/scraped.md" for r in res.get("refused", [])))

    with tempfile.TemporaryDirectory() as tmp:
        # (3) T1 descriptor whose only matching view has origin_max: unknown -> exit 1, named
        _write_tree(tmp, {
            "deploy/entities.yaml": "entities:\n  unk-ent:\n    aliases: [unk]\n    views: [wiki/unknown.md]\n",
            "wiki/unknown.md": _mk_view(entities=["unk-ent"], origin_max="unknown", summary="unknown-origin view"),
        })
        desc = build_descriptor(text="unk topic", task_type="build",
                                manifest_exempt="P4 self-test fixture, no product surface", tier="T4")
        code, res = assemble_packet(desc, tmp)
        check("ECO-7(3) T1 only-match unknown-origin: exit 1", code == 1)
        check("ECO-7(3) names view+origin, no packet emitted", "packet" not in res)

    with tempfile.TemporaryDirectory() as tmp:
        # (4) recon/verify descriptor matching external-scrape view -> included WITH taint banner
        _write_tree(tmp, {
            "deploy/entities.yaml": "entities:\n  scraped-ent:\n    aliases: [scr]\n    views: [wiki/scraped.md]\n",
            "wiki/scraped.md": _mk_view(entities=["scraped-ent"], origin_max="external-scrape", summary="scraped recon view"),
        })
        desc = build_descriptor(text="scr topic", task_type="recon")
        code, res = assemble_packet(desc, tmp)
        check("ECO-7(4) recon includes tainted view (exit 0)", code == 0)
        check("ECO-7(4) taint banner present", any("TAINT-BANNER" in b for b in res.get("banners", [])))
        check("ECO-7(4) tainted content actually present in packet", "scraped recon view" in res.get("packet", ""))

    # ---- Egress co-residency: a, a2 (F-11 rebind), c ----
    # SELF-SUFFICIENCY (not the live deploy/safe-allowlist.yaml): this section and the
    # origin-less-inclusion section below both request a `mcp__bizzflo__*` tool and assert
    # its classification (F-1/credential), which otherwise depends on the live allowlist
    # actually carrying this fork's connector entry -- an instance that edited its real
    # allowlist (e.g. a neutralized template port with no such connector) would break
    # these assertions even though the packet-assembly LOGIC being tested is unaffected.
    # Build one temp fixture allowlist (via tool_grant's own shared fixture helper, so this
    # suite and tool_grant's own --self-test never diverge on what the fixture contains)
    # and thread it through every assemble_packet() call below via the allowlist_path
    # seam -- LIVE (non-self-test) callers never pass this, so production assemble still
    # reads the real deploy/safe-allowlist.yaml.
    _fixture_allowlist = _tool_grant()._mk_allowlist_fixture()

    # UPDATED TO THE NEW CONTRACT (F-integration seam swap): now that the egress
    # co-residency decision keys off tool_grant.grant()'s real session_flags
    # (context_items = the packet's own included views), a tainted packet's
    # per-view origin taints classify_context's trust computation too (any
    # non-{human,corpus-first-party-verified} item -> untrusted). tool_grant's
    # own F-1 rule ("untrusted boot context -> credential tool excluded, never
    # granted") therefore fires FIRST and structurally -- the credential tool
    # is simply never in the toolbox, so session_flags.credentialed is False
    # and assemble.py's own exit-2 egress-co-residency branch (still present,
    # keyed off session_flags.credentialed, as a belt-and-suspenders backstop)
    # is not reached for this fixture. This is per TCB spec tool-grant-tcb-
    # spec.md line ~181: "the grant decision never includes a credential/
    # egress tool ... for a credentialed session" -- the "unless attested"
    # exit-2 exception path is GONE (Adjudication 1); F-1 exclusion at grant
    # time is strictly the earlier, structural enforcement point the old
    # placeholder's exit-2 stub only approximated. The old EGRESS(a)/(a2)
    # expectation ("exit 2 refuse") is REPLACED by: exit 0 (a packet still
    # emits, with a taint banner, since recon is not quarantine-gated), the
    # tool absent from grant.granted, named in grant.excluded with fixture
    # F-1, and session_flags.credentialed == False.
    with tempfile.TemporaryDirectory() as tmp:
        _write_tree(tmp, {
            "deploy/entities.yaml": "entities:\n  scr:\n    aliases: [scrap]\n    views: [wiki/scraped.md]\n",
            "wiki/scraped.md": _mk_view(entities=["scr"], origin_max="external-scrape", summary="tainted"),
        })
        # (a) tainted context -> tool_grant F-1 excludes the credential tool at
        # grant time (never reaches the toolbox); packet still emits (recon).
        desc_a = build_descriptor(text="scrap topic", task_type="recon",
                                  profile_tools=["mcp__bizzflo__search_customers"])
        code_a, res_a = assemble_packet(desc_a, tmp, allowlist_path=_fixture_allowlist)
        check("EGRESS(a) [NEW CONTRACT] tainted context -> exit 0, packet still emits (recon, bannered)", code_a == 0)
        check("EGRESS(a) [NEW CONTRACT] credential tool excluded at grant (F-1), never granted",
              res_a.get("packet_origin_max") == "external-scrape"
              and "mcp__bizzflo__search_customers" not in res_a["grant"]["granted"]
              and any(e["tool"] == "mcp__bizzflo__search_customers" and e["fixture"] == "F-1"
                      for e in res_a["grant"]["excluded"])
              and res_a["grant"]["session_flags"]["credentialed"] is False)

        # (a2) same but WITH a descriptor attestation string -> STILL excluded
        # (F-11: attestation strings are ignored by tool_grant.grant() itself,
        # recorded in grant.ignored_descriptor_claims, decision unaffected).
        desc_a2 = build_descriptor(text="scrap topic", task_type="recon",
                                   profile_tools=["mcp__bizzflo__search_customers"],
                                   descriptor_claims={"sandbox_attestation": "present-but-ignored"})
        code_a2, res_a2 = assemble_packet(desc_a2, tmp, allowlist_path=_fixture_allowlist)
        check("EGRESS(a2) [NEW CONTRACT] F-11 rebind: attestation string present -> STILL excluded, exit 0", code_a2 == 0)
        check("EGRESS(a2) [NEW CONTRACT] attestation recorded in grant.ignored_descriptor_claims, not honored",
              "sandbox_attestation" in res_a2["grant"]["ignored_descriptor_claims"]
              and res_a2["grant"]["ignored_descriptor_claims"]["sandbox_attestation"]["disposition"].startswith("ignored")
              and "mcp__bizzflo__search_customers" not in res_a2["grant"]["granted"])

    with tempfile.TemporaryDirectory() as tmp:
        # (c) clean human packet + credentialed profile -> allowed
        _write_tree(tmp, {
            "deploy/entities.yaml": "entities:\n  clean-ent:\n    aliases: [cleanent]\n    views: [wiki/clean.md]\n",
            "wiki/clean.md": _mk_view(entities=["clean-ent"], origin_max="human", summary="clean content"),
        })
        desc_c = build_descriptor(text="cleanent topic", task_type="recon",
                                  profile_tools=["mcp__bizzflo__search_customers"])
        code_c, res_c = assemble_packet(desc_c, tmp, allowlist_path=_fixture_allowlist)
        check("EGRESS(c) clean human packet + credentialed -> allowed (exit 0)", code_c == 0)
        check("EGRESS(c) packet_origin_max recorded as human", res_c.get("packet_origin_max") == "human")

    # ---- EGRESS exit-2 backstop (structural proof it is unreachable via the
    # normal per-view-origin wiring today, PLUS a direct unit test of the
    # branch's own logic via a monkeypatched _profile_grant). Since
    # context_items are always the SAME included views packet_origin_max is
    # computed over, a tainted packet_origin_max (anything outside
    # TRUSTED_ORIGINS=("human",)) always makes classify_context's own
    # per-item origin non-human too, which always makes tool_grant's
    # trusted=False, which always makes session_flags.credentialed False --
    # so the exit-2 branch's condition (credentialed AND tainted) can never
    # both hold via this call path on the live fork (F-1 exclusion always
    # fires first and instead). This is proved structurally above (EGRESS(a)/
    # (a2), origin-less mixed case); this block additionally unit-tests the
    # exit-2 branch's OWN code directly by monkeypatching _profile_grant to
    # return a (session_flags.credentialed=True) result paired with a tainted
    # packet, so the backstop's logic itself -- never exercised by any
    # reachable live input -- is still verified correct in isolation, kept as
    # defense-in-depth per the design doc's literal text.
    with tempfile.TemporaryDirectory() as tmp:
        _write_tree(tmp, {
            "deploy/entities.yaml": "entities:\n  scr2:\n    aliases: [scrap2]\n    views: [wiki/scraped2.md]\n",
            "wiki/scraped2.md": _mk_view(entities=["scr2"], origin_max="external-scrape", summary="tainted2"),
        })
        desc_backstop = build_descriptor(text="scrap2 topic", task_type="recon",
                                         profile_tools=["mcp__bizzflo__search_customers"])
        _real_profile_grant = globals()["_profile_grant"]

        def _fake_credentialed_grant(profile_tools, context_items, descriptor_claims,
                                     allowlist_path=None):
            real = _real_profile_grant(profile_tools, context_items, descriptor_claims,
                                       allowlist_path=allowlist_path)
            real["granted"] = list(profile_tools)
            real["session_flags"] = dict(real["session_flags"])
            real["session_flags"]["credentialed"] = True
            real["session_flags"]["trusted"] = True
            return real

        globals()["_profile_grant"] = _fake_credentialed_grant
        try:
            code_bs, res_bs = assemble_packet(desc_backstop, tmp, allowlist_path=_fixture_allowlist)
        finally:
            globals()["_profile_grant"] = _real_profile_grant
        check("EGRESS exit-2 backstop [unit, monkeypatched credentialed=True]:"
              " tainted packet + credentialed=True still -> exit 2 refuse", code_bs == 2)
        check("EGRESS exit-2 backstop names origin and offending tools",
              res_bs.get("packet_origin_max") == "external-scrape"
              and "mcp__bizzflo__search_customers" in res_bs.get("offending_tools", []))

    # ---- Origin-less inclusion guard ----
    # UPDATED TO THE NEW CONTRACT: an origin-less view's effective origin is
    # "unknown" (F-3 default-deny), which now ALSO feeds tool_grant's
    # classify_context() as this packet's context_items -- so it taints the
    # grant's trust computation exactly the way an external-scrape view does
    # above. tool_grant's F-1 exclusion therefore fires first and structurally:
    # the credential tool is never granted, so the origin-less-inclusion
    # guard's OWN "credentialed" branch (REASON_ORIGIN_LESS_CREDENTIALED,
    # exit 1) is never reached through this call path any more -- it is now
    # dead-but-harmless defense-in-depth (the design doc's own text keeps
    # picturing "a credentialed-profile packet" as a live case; on the current
    # fork, F-1 subsumes it before this guard's condition can ever be true,
    # since ANY origin-less item collapses classify_context's origin_max to
    # unknown, which always makes trusted=False, which always makes
    # session_flags.credentialed False -- proved for the mixed-view case too,
    # not just the origin-less-only case). Verified below: a mix of one
    # human-origin view and one origin-less view still yields
    # credentialed=False (F-1 excludes), not the old exit-1 refusal.
    with tempfile.TemporaryDirectory() as tmp:
        _write_tree(tmp, {
            "deploy/entities.yaml": "entities:\n  noderiv:\n    aliases: [noderiv]\n    views: [wiki/noderiv.md]\n",
            "wiki/noderiv.md": _mk_view(no_deriv=True),
        })
        desc = build_descriptor(text="noderiv topic", task_type="recon",
                                profile_tools=["mcp__bizzflo__search_customers"])
        code, res = assemble_packet(desc, tmp, allowlist_path=_fixture_allowlist)
        check("origin-less [NEW CONTRACT] view + credential-requesting profile -> exit 0"
              " (F-1 excludes the tool at grant before the origin-less guard's own"
              " credentialed-branch condition can become true)", code == 0)
        check("origin-less [NEW CONTRACT] credential tool excluded via F-1, not granted",
              "mcp__bizzflo__search_customers" not in res["grant"]["granted"]
              and res["grant"]["session_flags"]["credentialed"] is False)
        desc_nc = build_descriptor(text="noderiv topic", task_type="recon", profile_tools=[])
        code_nc, res_nc = assemble_packet(desc_nc, tmp, allowlist_path=_fixture_allowlist)
        check("origin-less view counts unknown in non-credentialed packet's origin_max",
              code_nc == 0 and res_nc.get("packet_origin_max") == "unknown")

        # Mixed case: one human-origin view + one origin-less view, both
        # included, credentialed-requesting profile -- proves the origin-less
        # item's "unknown" contribution taints classify_context's trust
        # computation even when other included views are clean, so F-1 still
        # fires (the origin-less guard's credentialed branch stays unreachable
        # even in a mixed bundle, not just the origin-less-only case above).
        _write_tree(tmp, {
            "deploy/entities.yaml": "entities:\n  mix:\n    aliases: [mixent]\n    views: [wiki/human.md, wiki/noderiv2.md]\n",
            "wiki/human.md": _mk_view(entities=["mix"], origin_max="human", summary="human view"),
            "wiki/noderiv2.md": _mk_view(no_deriv=True),
        })
        desc_mix = build_descriptor(text="mixent topic", task_type="recon",
                                    profile_tools=["mcp__bizzflo__search_customers"])
        code_mix, res_mix = assemble_packet(desc_mix, tmp, allowlist_path=_fixture_allowlist)
        check("origin-less [NEW CONTRACT] mixed human+origin-less bundle -> still F-1-excluded,"
              " not the old exit-1 credentialed-guard refusal",
              code_mix == 0
              and "mcp__bizzflo__search_customers" not in res_mix["grant"]["granted"]
              and res_mix["grant"]["session_flags"]["credentialed"] is False)

    _tool_grant()._rm_allowlist_fixture(_fixture_allowlist)

    # ---- Budget ----
    with tempfile.TemporaryDirectory() as tmp:
        big_summary = "X" * 1000
        _write_tree(tmp, {
            "deploy/entities.yaml": "entities:\n  bigent:\n    aliases: [bigent]\n    views: [wiki/big.md]\n",
            "wiki/big.md": _mk_view(entities=["bigent"], summary=big_summary),
        })
        desc = build_descriptor(text="bigent topic", task_type="build",
                                manifest_exempt="P4 self-test fixture, no product surface", tier="T4")
        code, res = assemble_packet(desc, tmp, budget_bytes=10)
        check("T1 build/fix budget overflow -> exit 1 naming the member", code == 1)
        check("overflow refusal names the overflowing view",
              any(r.get("path") == "wiki/big.md" for r in res.get("refused", [])))

        desc_r = build_descriptor(text="bigent topic", task_type="recon")
        code_r, res_r = assemble_packet(desc_r, tmp, budget_bytes=10)
        check("recon/T3 budget overflow truncates with explicit banner instead of refusing", code_r == 0)
        check("truncation banner present", any("TRUNCATION-BANNER" in b for b in res_r.get("banners", [])) or
              any("TRUNCATION-BANNER" in v for v in res_r.get("views", [])))

    # ---- Staleness (ECO-1 negative half): reuse check-derivation, fixture repo ----
    # HERMETIC (W6 stranger-test fix): this block previously cloned the LIVE
    # repo (deploy/ + wiki/ + .git) and overlaid the fork-corpus view
    # wiki/systems/schema-work-orders.md -- so a fresh instance was green
    # exactly until `git init` (the harness's own next-step) created a .git
    # dir, after which the block ran and crashed on the missing fork view.
    # It now builds its OWN fixture repo, exercising the same CONTENT-3 shape
    # (stale_verified_check: verified stamp non-null + working body != HEAD
    # body -> SV_STALE) with zero dependency on the host tree's corpus or
    # .git: one view committed at HEAD carrying a verified stamp, then the
    # WORKING copy's body edited past the stamp. Only a missing/broken git
    # binary skips (git IS the staleness oracle -- HEAD-blob diffing; with no
    # git there is nothing real to exercise), matching the file's
    # skip-with-note precedent.
    with tempfile.TemporaryDirectory() as tmp:
        stale_rel = "wiki/stale.md"
        _write_tree(tmp, {
            "deploy/entities.yaml":
                "entities:\n  staleent:\n    aliases: [staleent]\n    views: [wiki/stale.md]\n",
            stale_rel: _mk_view(entities=["staleent"], summary="stale fixture view",
                                verified_block={"status": "passed",
                                                "at": "2020-01-01T00:00:00Z"}),
        })
        git_ok = True
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"], ["add", "."],
                     ["commit", "-q", "-m", "fixture"]):
            try:
                p = subprocess.run(["git", "-C", tmp] + args, capture_output=True)
                git_ok = p.returncode == 0
            except OSError:
                git_ok = False
            if not git_ok:
                break
        if git_ok:
            # edit the WORKING body past the verify stamp (HEAD keeps the
            # original) -> stale_verified_check sees SV_STALE.
            with open(os.path.join(tmp, "wiki", "stale.md"), "a",
                      encoding="utf-8", newline="\n") as fh:
                fh.write("\nEdit after the verify stamp -- triggers CONTENT-3 staleness.\n")

            desc = build_descriptor(text="staleent topic", task_type="build",
                                    required_views=[stale_rel],
                                    manifest_exempt="P4 self-test fixture, no product surface", tier="T4")
            code, res = assemble_packet(desc, tmp)
            check("staleness overlay: T1 build/fix hard-stops on stale-verified", code == 1)
            check("staleness overlay: names the stale view",
                  any(stale_rel in r.get("path", "") for r in res.get("refused", [])))

            desc_v = build_descriptor(text="staleent topic", task_type="recon",
                                      required_views=[stale_rel])
            code_v, res_v = assemble_packet(desc_v, tmp)
            check("staleness overlay: verify/recon gets a banner, not a hard stop", code_v == 0)
            check("staleness overlay: banner text present",
                  any("STALE-BANNER" in b for b in res_v.get("banners", [])))
        else:
            check("staleness fixture skipped (git binary unavailable; non-fatal)", True)

    # ---- v3.0-133 pin: the ECO-1 overlay repo NEVER copies the host .git ----
    # A host repo carrying a deliberately long ref (the Codex refs/codex/turn-diffs/...
    # class that crossed MAX_PATH when re-rooted under a deeper temp dir) still overlays
    # fine, and the planted ref is ABSENT from the overlay's fresh history -- proof the
    # host .git did not travel.
    with tempfile.TemporaryDirectory() as tmp:
        host = os.path.join(tmp, "host")
        _write_tree(host, {
            "deploy/entities.yaml": "entities: {}\n",
            "wiki/overlay-pin.md": _mk_view(entities=[], summary="overlay pin view"),
        })
        host_git_ok = True
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"], ["add", "."],
                     ["commit", "-q", "-m", "host fixture"]):
            try:
                host_git_ok = subprocess.run(["git", "-C", host] + args,
                                             capture_output=True).returncode == 0
            except OSError:
                host_git_ok = False
            if not host_git_ok:
                break
        if host_git_ok:
            long_ref = "refs/codex/turn-diffs/" + "x" * 180
            planted = subprocess.run(["git", "-C", host, "update-ref", long_ref, "HEAD"],
                                     capture_output=True).returncode == 0
            if not planted:
                # loose-ref plant refused (host path limits) -> plant via packed-refs (a
                # TEXT file, no per-ref path on disk) so the absence check ALWAYS runs
                # (cross-vendor round-1 catch: a pin conditional on the plant succeeding
                # is not a pin)
                head_sha = subprocess.run(["git", "-C", host, "rev-parse", "HEAD"],
                                          capture_output=True, text=True).stdout.strip()
                if head_sha:
                    with open(os.path.join(host, ".git", "packed-refs"), "a",
                              encoding="ascii", newline="\n") as prf:
                        prf.write("%s %s\n" % (head_sha, long_ref))
                    planted = subprocess.run(
                        ["git", "-C", host, "show-ref", "--verify", long_ref],
                        capture_output=True).returncode == 0
            overlay = os.path.join(tmp, "overlay")
            os.makedirs(overlay)
            ok_ov = _overlay_repo(overlay, host)
            check("v3.0-133: overlay fixture repo builds its OWN git history, never the "
                  "host .git (long host ref planted=%s)" % planted, ok_ov)
            if ok_ov and planted:
                ref_travelled = subprocess.run(
                    ["git", "-C", overlay, "show-ref", "--verify", long_ref],
                    capture_output=True).returncode == 0
                check("v3.0-133: the planted over-long host ref is ABSENT from the overlay "
                      "repo (the host .git did not travel)", not ref_travelled)
            elif ok_ov:
                # cross-vendor round-2 catch: a pin conditional on the plant is not a
                # pin. With the packed-refs fallback the plant is deterministic on any
                # working git; failing to establish it FAILS the case loudly rather
                # than recording a positive-half-only pass.
                check("v3.0-133: the long-ref plant could not be established even via "
                      "packed-refs -- the absence pin did NOT run; investigate", False)
        else:
            check("v3.0-133 overlay pin skipped (git binary unavailable; non-fatal)", True)

    # ---- Sanity: seed views get full-text, closure-only members get summaries ----
    with tempfile.TemporaryDirectory() as tmp:
        _write_tree(tmp, {
            "deploy/entities.yaml": "entities:\n  seedent:\n    aliases: [seedent]\n    views: [wiki/seed.md]\n",
            "wiki/seed.md": _mk_view(entities=["seedent"], bundle=["wiki/closure-only.md"], summary="seed summary"),
            "wiki/closure-only.md": _mk_view(entities=[], summary="closure-only summary"),
        })
        desc = build_descriptor(text="seedent topic", task_type="recon")
        code, res = assemble_packet(desc, tmp)
        check("seed view served full-text", "Body text for seed summary." in res.get("packet", ""))
        check("closure-only member served one-hop summary, not full body",
              "closure-only summary" in res.get("packet", "")
              and "Body text for closure-only summary." not in res.get("packet", ""))

        desc_full = build_descriptor(text="seedent topic", task_type="recon")
        code_full, res_full = assemble_packet(desc_full, tmp, full_paths=["wiki/closure-only.md"])
        check("--full promotes a named closure member to full-text",
              "Body text for closure-only summary." in res_full.get("packet", ""))

    # ---- CLI smoke for --descriptor / --budget-bytes / --full ----
    with tempfile.TemporaryDirectory() as tmp:
        _write_tree(tmp, {
            "deploy/entities.yaml": "entities:\n  client:\n    aliases: [client]\n    views: [wiki/cli.md]\n",
            "wiki/cli.md": _mk_view(entities=["client"], origin_max="human", summary="cli summary"),
        })
        desc_path = os.path.join(tmp, "descriptor.yaml")
        with open(desc_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("text: client topic\ntask_type: recon\n")
        out_json = os.path.join(tmp, "out.json")
        rc = main(["assemble.py", "--descriptor", desc_path, "--root", tmp,
                   "--budget-bytes", "262144", "--json", out_json])
        check("CLI --descriptor smoke: exit 0", rc == 0)
        with open(out_json, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        check("CLI --descriptor JSON carries packet_origin_max", payload.get("packet_origin_max") == "human")

    return failed, total


###############################################################################
# Behavioral-manifest gate self-test (manifest-format.md Section 12's
# assemble.py bullet). HERMETIC: every fixture (manifests/<surface>/
# MANIFEST-INDEX.md files + descriptor dicts) is built under
# tempfile.TemporaryDirectory() -- never reads live deploy/ config or a live
# manifests/ tree (this fork carries none, per manifest-format.md Section 2:
# absent-by-design until first extraction).
###############################################################################

def _self_test_manifest_gate():
    import tempfile

    failed = 0
    total = 0

    def check(name, cond):
        nonlocal failed, total
        total += 1
        print("  %s [MANIFEST-GATE] %s" % ("ok " if cond else "XX ", name))
        if not cond:
            failed += 1

    def mk_index(tmp, surface, layers, manifest_fields=None, missing_file_layers=None):
        """layers: {layer_key: status}. Minimal MANIFEST-INDEX.md fixture
        (manifest-format.md Section 6 shape) -- a fenced YAML block with one
        `layers.<key>` entry per given layer; MISSING carries no other
        fields, everything else gets a `file` path (and `certified_by` once
        CERTIFIED/LIVE), matching the doctrine's own schema.

        For every non-MISSING layer, ALSO writes the referenced
        <layer>-MANIFEST.md fixture file to disk with its own minimal
        frontmatter, so the behavioral-manifest gate's INDEX-layer-key vs
        manifest-file-layer cross-check has a real file to read (the green
        default: manifest_fields matches the layer key, i.e. coherent).

        manifest_fields: optional {layer_key: manifest_field_value} override
        for the written file's own frontmatter `manifest:` field -- pass a
        different value than the layer key to fixture the INDEX/file
        mismatch case.
        missing_file_layers: optional set of layer keys whose INDEX entry
        points at a file that is deliberately never written to disk (the
        'file absent on disk' refusal fixture)."""
        manifest_fields = manifest_fields or {}
        missing_file_layers = missing_file_layers or set()
        lines = ["surface: %s" % surface, "updated: 2026-07-18", "layers:"]
        for key, status in layers.items():
            lines.append("  %s:" % key)
            if status == "MISSING":
                lines.append("    status: MISSING")
                continue
            lines.append("    file: manifests/%s/%s-MANIFEST.md" % (surface, key))
            lines.append("    status: %s" % status)
            lines.append("    rows: 1")
            lines.append("    certified_by: %s" %
                          ("receipts/x-certify-r1.md" if status in ("CERTIFIED", "LIVE") else "null"))
            if key not in missing_file_layers:
                field = manifest_fields.get(key, key)
                manifest_text = "---\nmanifest: %s\nsurface: %s\nstatus: %s\n---\n\nBody.\n" % (
                    field, surface, status)
                _write_tree(tmp, {"manifests/%s/%s-MANIFEST.md" % (surface, key): manifest_text})
        body = "\n".join(lines)
        _write_tree(tmp, {"manifests/%s/MANIFEST-INDEX.md" % surface: "```yaml\n%s\n```\n" % body})

    # (1) build + surfaces + CERTIFIED INDEX at T2 -> pass, verdict OPEN,
    #     banner in packet text, manifest paths listed.
    with tempfile.TemporaryDirectory() as tmp:
        mk_index(tmp, "orders-web", {"interaction": "CERTIFIED"})
        desc = build_descriptor(text="x", task_type="build", surfaces=["orders-web"],
                                touched_layers=["interaction"], tier="T2")
        code, res = assemble_packet(desc, tmp)
        check("(1) build+CERTIFIED@T2: exit 0", code == 0)
        if code == 0:
            mg = res.get("manifest_gate") or {}
            check("(1) mode gated, verdict OPEN", mg.get("mode") == "gated" and mg.get("verdict") == "OPEN")
            check("(1) banner present in packet text",
                  "BEHAVIORAL-MANIFEST GATE" in res.get("packet", ""))
            check("(1) manifest path listed in manifest_gate and packet text",
                  any("interaction-MANIFEST.md" in p for p in mg.get("manifest_paths", []))
                  and "interaction-MANIFEST.md" in res.get("packet", ""))

    # (2) build with neither surfaces nor manifest_exempt -> refuse
    with tempfile.TemporaryDirectory() as tmp:
        desc = build_descriptor(text="x", task_type="build")
        code, res = assemble_packet(desc, tmp)
        check("(2) build w/ neither surfaces nor manifest_exempt: exit 1", code == 1)
        check("(2) refusal names declare-or-exempt",
              any("neither surfaces nor manifest_exempt" in r.get("reason", "")
                  for r in res.get("refused", [])))

    # (3) build with both surfaces and manifest_exempt -> refuse (ambiguous)
    with tempfile.TemporaryDirectory() as tmp:
        desc = build_descriptor(text="x", task_type="build", surfaces=["orders-web"],
                                touched_layers=["interaction"], tier="T2",
                                manifest_exempt="conflicting reason")
        code, res = assemble_packet(desc, tmp)
        check("(3) build w/ both declared: exit 1", code == 1)
        check("(3) refusal names ambiguous",
              any("ambiguous" in r.get("reason", "") for r in res.get("refused", [])))

    # (4) exempt at T4 -> pass, reason recorded
    with tempfile.TemporaryDirectory() as tmp:
        desc = build_descriptor(text="x", task_type="build", tier="T4",
                                manifest_exempt="golden-recall descriptor, no product surface")
        code, res = assemble_packet(desc, tmp)
        check("(4) exempt@T4: exit 0", code == 0)
        if code == 0:
            mg = res.get("manifest_gate") or {}
            check("(4) mode exempt, tier T4, reason recorded",
                  mg.get("mode") == "exempt" and mg.get("tier") == "T4"
                  and mg.get("reason") == "golden-recall descriptor, no product surface")

    # (5) exempt at T2 -> refuse (T4-only)
    with tempfile.TemporaryDirectory() as tmp:
        desc = build_descriptor(text="x", task_type="fix", tier="T2", manifest_exempt="reason")
        code, res = assemble_packet(desc, tmp)
        check("(5) exempt@T2: exit 1", code == 1)
        check("(5) refusal names T4-only",
              any("T4-only" in r.get("reason", "") for r in res.get("refused", [])))

    # (6) surfaces without tier -> refuse
    with tempfile.TemporaryDirectory() as tmp:
        desc = build_descriptor(text="x", task_type="fix", surfaces=["orders-web"],
                                touched_layers=["interaction"])
        code, res = assemble_packet(desc, tmp)
        check("(6) surfaces w/o tier: exit 1", code == 1)
        check("(6) refusal names missing/invalid tier",
              any("tier" in r.get("reason", "") for r in res.get("refused", [])))

    # (7) surfaces without touched_layers -> refuse (gate is touch-based)
    with tempfile.TemporaryDirectory() as tmp:
        desc = build_descriptor(text="x", task_type="fix", surfaces=["orders-web"], tier="T2")
        code, res = assemble_packet(desc, tmp)
        check("(7) surfaces w/o touched_layers: exit 1", code == 1)
        check("(7) refusal names touch-based",
              any("touch-based" in r.get("reason", "") for r in res.get("refused", [])))

    # (8) DRAFT layer at T2 -> refuse, naming layer + status
    with tempfile.TemporaryDirectory() as tmp:
        mk_index(tmp, "orders-web", {"design": "DRAFT"})
        desc = build_descriptor(text="x", task_type="build", surfaces=["orders-web"],
                                touched_layers=["design"], tier="T2")
        code, res = assemble_packet(desc, tmp)
        check("(8) DRAFT@T2: exit 1", code == 1)
        check("(8) refusal names layer + status",
              any("design" in r.get("reason", "") and "DRAFT" in r.get("reason", "")
                  for r in res.get("refused", [])))

    # (9) SUPERSEDED at T4-with-surfaces -> refuse (SUPERSEDED never satisfies)
    with tempfile.TemporaryDirectory() as tmp:
        mk_index(tmp, "orders-web", {"interaction": "SUPERSEDED"})
        desc = build_descriptor(text="x", task_type="build", surfaces=["orders-web"],
                                touched_layers=["interaction"], tier="T4")
        code, res = assemble_packet(desc, tmp)
        check("(9) SUPERSEDED@T4-with-surfaces: exit 1 (never satisfies)", code == 1)
        check("(9) refusal names SUPERSEDED", any("SUPERSEDED" in r.get("reason", "") for r in res.get("refused", [])))

    # (10) missing INDEX -> refuse, naming the surface
    with tempfile.TemporaryDirectory() as tmp:
        desc = build_descriptor(text="x", task_type="build", surfaces=["ghost-surface"],
                                touched_layers=["interaction"], tier="T2")
        code, res = assemble_packet(desc, tmp)
        check("(10) missing INDEX: exit 1", code == 1)
        check("(10) refusal names surface",
              any("ghost-surface" in r.get("reason", "") for r in res.get("refused", [])))

    # (11) EXTRACTED at T3 -> pass
    with tempfile.TemporaryDirectory() as tmp:
        mk_index(tmp, "orders-web", {"logic": "EXTRACTED"})
        desc = build_descriptor(text="x", task_type="fix", surfaces=["orders-web"],
                                touched_layers=["logic"], tier="T3")
        code, res = assemble_packet(desc, tmp)
        check("(11) EXTRACTED@T3: exit 0", code == 0)

    # (12) verify task + below-tier (DRAFT) layer -> NO refusal, ADVISORY + banner
    with tempfile.TemporaryDirectory() as tmp:
        mk_index(tmp, "orders-web", {"design": "DRAFT"})
        desc = build_descriptor(text="x", task_type="verify", surfaces=["orders-web"],
                                touched_layers=["design"], tier="T2")
        code, res = assemble_packet(desc, tmp)
        check("(12) verify+below-tier layer: exit 0 (never refuses)", code == 0)
        if code == 0:
            mg = res.get("manifest_gate") or {}
            check("(12) verdict ADVISORY", mg.get("verdict") == "ADVISORY")
            check("(12) advisory banner present",
                  any("design" in b and "DRAFT" in b and "advisory" in b for b in res.get("banners", [])))

    # (13) verify/recon without surfaces -> unchanged (no manifest_gate key, or mode:"none")
    with tempfile.TemporaryDirectory() as tmp:
        desc = build_descriptor(text="x", task_type="recon")
        code, res = assemble_packet(desc, tmp)
        check("(13) recon w/o surfaces: exit 0", code == 0)
        check("(13) unchanged: no manifest_gate key (or mode:none)",
              ("manifest_gate" not in res) or (res.get("manifest_gate", {}).get("mode") == "none"))

    # (14) LIVE at T1 -> pass
    with tempfile.TemporaryDirectory() as tmp:
        mk_index(tmp, "orders-web", {"authorization": "LIVE"})
        desc = build_descriptor(text="x", task_type="build", surfaces=["orders-web"],
                                touched_layers=["authorization"], tier="T1")
        code, res = assemble_packet(desc, tmp)
        check("(14) LIVE@T1: exit 0", code == 0)

    # (15) gated build, INDEX layer key vs file's manifest: field mismatch -> refuse, naming both
    with tempfile.TemporaryDirectory() as tmp:
        mk_index(tmp, "orders-web", {"interaction": "CERTIFIED"},
                 manifest_fields={"interaction": "logic"})
        desc = build_descriptor(text="x", task_type="build", surfaces=["orders-web"],
                                touched_layers=["interaction"], tier="T2")
        code, res = assemble_packet(desc, tmp)
        check("(15) INDEX/file layer mismatch: exit 1", code == 1)
        check("(15) refusal names both the INDEX layer key and the file's manifest: value",
              any("'interaction'" in r.get("reason", "") and "'logic'" in r.get("reason", "")
                  for r in res.get("refused", [])))

    # (16) gated build, INDEX entry's file absent on disk -> refuse (fail-closed)
    with tempfile.TemporaryDirectory() as tmp:
        mk_index(tmp, "orders-web", {"interaction": "CERTIFIED"},
                 missing_file_layers={"interaction"})
        desc = build_descriptor(text="x", task_type="build", surfaces=["orders-web"],
                                touched_layers=["interaction"], tier="T2")
        code, res = assemble_packet(desc, tmp)
        check("(16) INDEX entry's file absent on disk: exit 1", code == 1)
        check("(16) refusal names missing/unreadable",
              any("missing or unreadable" in r.get("reason", "") for r in res.get("refused", [])))

    # (17) verify task, same INDEX/file layer mismatch -> no refusal, banner present
    with tempfile.TemporaryDirectory() as tmp:
        mk_index(tmp, "orders-web", {"interaction": "CERTIFIED"},
                 manifest_fields={"interaction": "logic"})
        desc = build_descriptor(text="x", task_type="verify", surfaces=["orders-web"],
                                touched_layers=["interaction"], tier="T2")
        code, res = assemble_packet(desc, tmp)
        check("(17) verify + INDEX/file layer mismatch: exit 0 (never refuses)", code == 0)
        check("(17) mismatch banner present",
              any("logic" in b and "mismatch" in b for b in res.get("banners", [])))

    # (18) verify task, INDEX entry's file absent on disk -> no refusal, missing-file
    #      advisory banner present (cross-vendor review finding: the advisory branch
    #      used to silently skip a missing/unreadable referenced file with nothing
    #      to show for it -- now it banners like every other advisory case).
    with tempfile.TemporaryDirectory() as tmp:
        mk_index(tmp, "orders-web", {"interaction": "CERTIFIED"},
                 missing_file_layers={"interaction"})
        desc = build_descriptor(text="x", task_type="verify", surfaces=["orders-web"],
                                touched_layers=["interaction"], tier="T2")
        code, res = assemble_packet(desc, tmp)
        check("(18) verify + INDEX entry's file absent on disk: exit 0 (never refuses)", code == 0)
        check("(18) missing-file advisory banner present",
              any("missing or unreadable" in b for b in res.get("banners", [])))

    # (19) verify task, surface's MANIFEST-INDEX.md missing entirely -> no
    #      refusal, surface-level advisory banner present (cross-vendor
    #      review finding, third of its class: the advisory branch used to
    #      discard _scan_manifest_layers's unreadable_surfaces as `_unreadable`
    #      and never banner it -- a verify/recon packet against a surface
    #      whose INDEX is missing or unparseable got NO banner at all;
    #      the gated branch already refuses on this same condition, see (10)).
    with tempfile.TemporaryDirectory() as tmp:
        desc = build_descriptor(text="x", task_type="verify", surfaces=["ghost-surface"],
                                touched_layers=["interaction"], tier="T2")
        code, res = assemble_packet(desc, tmp)
        check("(19) verify + missing INDEX: exit 0 (never refuses)", code == 0)
        check("(19) surface-level missing/unparseable INDEX banner present",
              any("ghost-surface" in b and "missing or unparseable" in b
                  for b in res.get("banners", [])))

    # (20) verify task, touched layer entirely absent from the INDEX's
    #      `layers:` mapping (not merely present with status: MISSING) ->
    #      no refusal, banner present. _scan_manifest_layers normalizes an
    #      absent layer key to status "MISSING" the same as an explicit
    #      "status: MISSING" entry, so this already bannered via the
    #      generic below-CERTIFIED status check; pinned here per the
    #      class-sweep instruction so the equivalence can't silently regress.
    with tempfile.TemporaryDirectory() as tmp:
        mk_index(tmp, "orders-web", {"interaction": "CERTIFIED"})
        desc = build_descriptor(text="x", task_type="verify", surfaces=["orders-web"],
                                touched_layers=["logic"], tier="T2")
        code, res = assemble_packet(desc, tmp)
        check("(20) verify + touched layer absent from INDEX entirely: exit 0 (never refuses)",
              code == 0)
        check("(20) below-CERTIFIED banner present for the absent (MISSING) layer",
              any("logic" in b and "MISSING" in b and "advisory" in b
                  for b in res.get("banners", [])))

    # (21) verify task, surfaces declared but touched_layers absent/empty ->
    #      no refusal (descriptor-shape rules never apply to verify/recon),
    #      but declared-and-ignored gate intent must banner instead of the
    #      branch silently having nothing to scan and saying nothing.
    with tempfile.TemporaryDirectory() as tmp:
        desc = build_descriptor(text="x", task_type="verify", surfaces=["orders-web"], tier="T2")
        code, res = assemble_packet(desc, tmp)
        check("(21) verify + surfaces w/o touched_layers: exit 0 (never refuses)", code == 0)
        check("(21) 'nothing scanned' banner present",
              any("surfaces declared without touched_layers -- nothing scanned" in b
                  for b in res.get("banners", [])))

    # (22) verify task, descriptor declares BOTH surfaces and manifest_exempt
    #      (contradictory) -> no refusal, contradiction banner present, AND
    #      the normal surfaces scan still runs (manifest_exempt is ignored,
    #      not treated as if surfaces were absent).
    with tempfile.TemporaryDirectory() as tmp:
        mk_index(tmp, "orders-web", {"interaction": "CERTIFIED"})
        desc = build_descriptor(text="x", task_type="verify", surfaces=["orders-web"],
                                touched_layers=["interaction"], tier="T2",
                                manifest_exempt="conflicting reason")
        code, res = assemble_packet(desc, tmp)
        check("(22) verify + both surfaces and manifest_exempt declared: exit 0 (never refuses)",
              code == 0)
        check("(22) contradiction banner present",
              any("descriptor declares both surfaces and manifest_exempt -- "
                  "contradictory declaration ignored" in b
                  for b in res.get("banners", [])))
        mg = res.get("manifest_gate") or {}
        check("(22) normal scan still ran (manifest_gate ADVISORY data present)",
              mg.get("mode") == "advisory" and mg.get("verdict") == "ADVISORY"
              and mg.get("layers", {}).get("orders-web", {}).get("interaction") == "CERTIFIED")

    print("assemble.py behavioral-manifest gate self-test: %s (%d/%d)" %
          ("PASS" if failed == 0 else "FAIL", total - failed, total))
    return failed, total


###############################################################################
# ECO-1 GOLDEN RUN (task 3): loads deploy/descriptors/golden-descriptors.yaml
# and, for EVERY descriptor, asserts its expected_outcome against the LIVE
# catalog (Adjudication 3 -- no overlays for the positive half; overlays are
# used ONLY for the stale-forcing negative half, per the design doc and the
# golden file's own header). This is a SEPARATE self-test section (its own
# printed sub-total) from the fixture-tree-based _self_test_packet_machinery
# above, because it runs against the real repo root, not a synthetic
# tempfile tree -- a genuinely different kind of evidence (live-catalog
# proof, not fixture proof).
###############################################################################

def _overlay_repo(tmp, repo_root):
    """Build the ECO-1 negative-half overlay repo: the host's deploy/ + wiki/ trees copied
    under tmp and committed at HEAD by tmp's OWN fresh git history. NEVER copies the host
    .git (v3.0.51, backlog v3.0-133: a long ref path -- e.g. Codex's
    refs/codex/turn-diffs/... -- re-rooted under a deeper temp dir crossed the Windows
    path limit, so the doctor FAILed on production hosts for a reason unrelated to the
    instance; a self-test that depends on the host .git's contents is an environment
    probe, not a self-test). The staleness semantics are unchanged: HEAD holds the
    committed copies, the caller then edits the WORKING copy past the verify stamp.
    Returns True when the fixture repo committed cleanly; False = caller skips."""
    import shutil
    import subprocess
    try:
        shutil.copytree(os.path.join(repo_root, "deploy"), os.path.join(tmp, "deploy"))
        shutil.copytree(os.path.join(repo_root, "wiki"), os.path.join(tmp, "wiki"))
    except OSError:
        return False
    for args in (["init", "-q"], ["config", "user.email", "overlay@fixture"],
                 ["config", "user.name", "overlay"], ["config", "commit.gpgsign", "false"],
                 ["add", "."], ["commit", "-q", "-m", "overlay fixture"]):
        try:
            if subprocess.run(["git", "-C", tmp] + args, capture_output=True).returncode != 0:
                return False
        except OSError:
            return False
    return True


def _self_test_eco1_golden():
    failed = 0
    total = 0

    def check(name, cond):
        nonlocal failed, total
        total += 1
        print("  %s [ECO-1 golden] %s" % ("ok " if cond else "XX ", name))
        if not cond:
            failed += 1

    repo_root = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(repo_root)  # deploy/ -> repo root
    golden_path = os.path.join(repo_root, "descriptors", "golden-descriptors.yaml")
    if not os.path.isfile(golden_path):
        golden_path = os.path.join(repo_root, "deploy", "descriptors", "golden-descriptors.yaml")

    if not os.path.isfile(golden_path):
        # ABSENT golden file = the documented-legitimate FRESH-INSTANCE state
        # (see deploy/descriptors/README.md: a new instance authors its own
        # golden descriptors once it has real entities/views to pin) -- so this
        # section SKIPS with a note rather than failing a day-1 instance. This
        # skip covers ONLY the absent-file case: a PRESENT golden file is still
        # parsed and validated below, and a malformed/underpopulated one still
        # fails (the >=6-descriptors check and every per-descriptor assertion
        # run unchanged).
        check("ECO-1 golden section: SKIPPED -- no golden-descriptors.yaml on this"
              " instance (documented-legitimate fresh-instance state; author one"
              " per deploy/descriptors/README.md to activate this section)", True)
        return failed, total

    data = load_yaml_file(golden_path)
    golden_descriptors = data.get("descriptors") or []
    check("golden-descriptors.yaml parsed, >=6 descriptors present (Component 4 minimum)",
          len(golden_descriptors) >= 6)

    # SELF-SUFFICIENCY (not the live deploy/safe-allowlist.yaml): two of the golden
    # descriptors request a `mcp__bizzflo__*` profile tool and assert its F-1/credential
    # classification -- same coupling as _self_test_packet_machinery's EGRESS section, so
    # this loop uses the SAME shared fixture-allowlist seam (tool_grant's own fixture
    # helper). Harmless for the other descriptors (empty tools lists, allowlist unused).
    _fixture_allowlist = _tool_grant()._mk_allowlist_fixture()

    for gd in golden_descriptors:
        gid = gd.get("id", "<unnamed>")
        required_views = list(gd.get("required_views") or [])
        profile_tools = ((gd.get("profile") or {}).get("tools")) or []
        expected = gd.get("expected_outcome")
        desc = build_descriptor(text=gd.get("text", ""), task_type=gd.get("task_type"),
                                required_views=required_views, profile_tools=profile_tools,
                                descriptor_claims=gd.get("descriptor_claims") or {},
                                surfaces=gd.get("surfaces") or [],
                                touched_layers=gd.get("touched_layers") or [],
                                manifest_exempt=gd.get("manifest_exempt"),
                                tier=gd.get("tier"))
        code, res = assemble_packet(desc, repo_root, allowlist_path=_fixture_allowlist)

        # SELECTION recall (asserted for every descriptor, per adjudication 3):
        # the computed closure contains 100% of the hand-authored required_views
        # answer key, regardless of what assemble_packet ultimately decides to
        # do with them (packet vs refuse) -- this is the "did selection/closure
        # even REACH the right views" proof, independent of the gate outcome.
        closure_members = res.get("closure_members")
        if closure_members is None:
            # a refusal path that returns before computing closure_members
            # (e.g. staleness hard-stop) still names the offending view(s)
            # directly in `refused` -- selection recall there is proved by
            # the view being NAMED, since it could only be named if selection
            # reached it in the first place.
            named_paths = {r.get("path") for r in res.get("refused", [])}
            selection_recall_ok = all(v in named_paths for v in required_views)
        else:
            selection_recall_ok = all(v in closure_members for v in required_views)
        check("%s: SELECTION recall 100%% (all required_views reached by seed+closure)" % gid,
              selection_recall_ok)

        if expected == "packet":
            check("%s: expected_outcome=packet -> assemble_packet exit 0" % gid, code == 0)
            if code == 0:
                # PACKET recall: the emitted packet's body carries every
                # required view's own content (checked via the view's own
                # provenance header line, which is unique per path and always
                # present for an included view -- full-text or summary role).
                packet_text = res.get("packet", "")
                packet_recall_ok = all(("--- view: %s ---" % v) in packet_text for v in required_views)
                check("%s: PACKET recall 100%% (every required_view's header present in the emitted packet)" % gid,
                      packet_recall_ok)
                check("%s: byte-fit reported (budget_fit key present)" % gid,
                      "budget_fit" in res and "budget_bytes_used" in res)
            else:
                check("%s: expected packet but got refusal -- reason %r" % (gid, res.get("reason") or res.get("refused")),
                      False)

        elif expected == "refuse-F12":
            check("%s: expected_outcome=refuse-F12 -> assemble_packet exit 1" % gid, code == 1)
            if code == 1:
                refused_paths = {r.get("path") for r in res.get("refused", [])}
                names_required = any(v in refused_paths for v in required_views)
                check("%s: refusal names (at least one of) the required_views" % gid, names_required)
            else:
                check("%s: expected refuse-F12 but got exit %d" % (gid, code), False)

        elif expected == "refuse-egress":
            check("%s: expected_outcome=refuse-egress -> assemble_packet exit 2" % gid, code == 2)
            if code == 2:
                check("%s: egress refusal names origin and offending tools" % gid,
                      bool(res.get("packet_origin_max")) and bool(res.get("offending_tools")))
            else:
                check("%s: expected refuse-egress but got exit %d" % (gid, code), False)

        elif expected == "packet-tool-excluded":
            # The 7th descriptor's outcome (F-integration seam-swap, task 4): a
            # credentialed profile paired with an unknown/external-scrape
            # required view. tool_grant's F-1 rule excludes the credential/
            # egress tool at grant time (the packet's own tainted included
            # views taint classify_context's trust computation) BEFORE
            # assemble.py's own exit-2 egress-co-residency backstop could ever
            # see credentialed=True + tainted -- so a packet still emits
            # (exit 0, recon/verify are not quarantine-gated), but the
            # profile's tool never reaches grant.granted. See the golden
            # file's header (F-INTEGRATION SEAM-SWAP NOTE) and this
            # descriptor's own notes for the full reasoning.
            check("%s: expected_outcome=packet-tool-excluded -> assemble_packet exit 0" % gid, code == 0)
            if code == 0:
                grant = res.get("grant") or {}
                excluded_tools = {e.get("tool") for e in grant.get("excluded", [])}
                offending = set(profile_tools) & excluded_tools
                check("%s: PACKET recall 100%% (every required_view's header present in the emitted packet)" % gid,
                      all(("--- view: %s ---" % v) in res.get("packet", "") for v in required_views))
                check("%s: profile tool(s) excluded from grant via F-1, session_flags.credentialed False" % gid,
                      bool(offending)
                      and all(any(e.get("tool") == t and e.get("fixture") == "F-1"
                                  for e in grant.get("excluded", []))
                              for t in offending)
                      and grant.get("session_flags", {}).get("credentialed") is False
                      and not (set(profile_tools) & set(grant.get("granted", []))))
            else:
                check("%s: expected packet-tool-excluded but got exit %d" % (gid, code), False)

        else:
            check("%s: unrecognized expected_outcome %r in golden file" % (gid, expected), False)

    # ---- Stale-forcing negative half (ECO-1 negative half), golden-scoped ----
    # Per the design doc (Adjudication 3): overlays are used ONLY for this
    # negative half, applied on a TEMP JOURNAL COPY, never by mutating a
    # descriptor's required_views or the live wiki/ state. Reuses one of the
    # golden set's own required_views (a recon/verify descriptor's, since a T1
    # build/fix hard-stop AND a recon/verify banner can both be exercised off
    # the SAME overlaid view) so this section is concretely tied to Component
    # 4's own answer key, not a freestanding fixture.
    # HERMETIC (W6 stranger-test fix): the overlay target is DERIVED from the
    # instance's own golden file -- the first recon/verify descriptor whose
    # required view exists on disk under wiki/ -- where it previously
    # hardcoded one instance's wiki/<domain>/api-endpoints.md (which would crash
    # any other instance that authored its own goldens). Missing
    # preconditions SKIP with a note; nothing is masked by the skips: a
    # PRESENT golden file whose required views are wrong/missing already
    # fails the positive-half loop above (selection/packet recall asserted
    # per descriptor). The hermetic staleness LOGIC itself is always
    # exercised fixture-based in _self_test_packet_machinery regardless.
    is_git_repo = os.path.isdir(os.path.join(repo_root, ".git"))
    target_rel = None
    target_text = ""
    if is_git_repo:
        for gd in golden_descriptors:
            if gd.get("task_type") not in ("recon", "verify"):
                continue
            for rv in (gd.get("required_views") or []):
                rv = str(rv or "")
                if rv.startswith("wiki/") and os.path.isfile(
                        os.path.join(repo_root, rv.replace("/", os.sep))):
                    target_rel = rv
                    target_text = gd.get("text", "") or rv
                    break
            if target_rel:
                break
    if not is_git_repo:
        check("ECO-1 golden negative half: skipped (no .git found; non-fatal)", True)
    elif target_rel is None:
        check("ECO-1 golden negative half: skipped (no recon/verify golden descriptor"
              " with an on-disk wiki/ required view to overlay; non-fatal -- the"
              " positive-half loop above still validates every descriptor, and the"
              " staleness logic is covered fixture-based in the P4 self-tests)", True)
    else:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            if not _overlay_repo(tmp, repo_root):
                check("ECO-1 golden negative half: skipped (overlay fixture repo could not "
                      "be built -- git or copy unavailable; non-fatal)", True)
                _tool_grant()._rm_allowlist_fixture(_fixture_allowlist)
                print("assemble.py ECO-1 golden self-test: %s (%d/%d)" %
                      ("PASS" if failed == 0 else "FAIL", total - failed, total))
                return failed, total
            target_abs = os.path.join(tmp, target_rel.replace("/", os.sep))
            with open(target_abs, "r", encoding="utf-8") as fh:
                original = fh.read()
            if "verified: null" in original:
                forced_stale = original.replace(
                    "verified: null",
                    "verified:\n  status: passed\n  at: 2020-01-01T00:00:00Z\n", 1
                ) + "\nECO-1 golden-section forced overlay edit to trigger CONTENT-3 staleness.\n"
                with open(target_abs, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(forced_stale)

                desc_stale_build = build_descriptor(text=target_text,
                                                    task_type="build", required_views=[target_rel],
                                                    manifest_exempt="golden-recall descriptor, no product surface",
                                                    tier="T4")
                code_sb, res_sb = assemble_packet(desc_stale_build, tmp)
                check("ECO-1 golden negative half: T1 build/fix hard-stops on forced-stale overlay"
                      " (exit 1)", code_sb == 1)
                check("ECO-1 golden negative half: names the stale view",
                      any(target_rel in r.get("path", "") for r in res_sb.get("refused", [])))

                desc_stale_recon = build_descriptor(text=target_text,
                                                    task_type="recon", required_views=[target_rel])
                code_sr, res_sr = assemble_packet(desc_stale_recon, tmp)
                check("ECO-1 golden negative half: verify/recon gets a banner, not a hard stop"
                      " (exit 0)", code_sr == 0)
                check("ECO-1 golden negative half: stale banner text present",
                      any("STALE-BANNER" in b for b in res_sr.get("banners", [])))
            else:
                check("ECO-1 golden negative half: skipped (target view's verified block"
                      " shape changed -- overlay technique needs re-pointing, non-fatal)", True)

    _tool_grant()._rm_allowlist_fixture(_fixture_allowlist)

    print("assemble.py ECO-1 golden self-test: %s (%d/%d)" %
          ("PASS" if failed == 0 else "FAIL", total - failed, total))
    return failed, total


# ---------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------

def main(argv):
    if len(argv) > 1 and argv[1] == "--self-test":
        return self_test()

    parser = argparse.ArgumentParser(prog="assemble.py", add_help=True)
    parser.add_argument("--task", required=False, help="task descriptor")
    parser.add_argument("--views", nargs="*", default=[], help="view paths to assemble")
    parser.add_argument("--root", default=os.getcwd(), help="containment root for view paths")
    parser.add_argument("--json", dest="json_out", default=None, help="write result JSON to this path instead of stdout")
    parser.add_argument("--self-test", action="store_true", help="run embedded fixtures")
    parser.add_argument("--descriptor", default=None, help="YAML descriptor path (P4 packet machinery)")
    parser.add_argument("--budget-bytes", type=int, default=DEFAULT_BUDGET_BYTES, help="packet byte budget (default 262144)")
    parser.add_argument("--full", action="append", default=[], help="promote a closure member to full-text on named demand (repeatable)")

    try:
        ns = parser.parse_args(argv[1:])
    except SystemExit:
        return 3

    if ns.self_test:
        return self_test()

    if ns.descriptor:
        if not os.path.isfile(ns.descriptor):
            sys.stderr.write("assemble.py: --descriptor path not found: %s\n" % ns.descriptor)
            return 3
        descriptor = load_descriptor_yaml(ns.descriptor, views_override=ns.views)
        code, result = assemble_packet(descriptor, ns.root, budget_bytes=ns.budget_bytes, full_paths=ns.full)
        if ns.json_out:
            with open(ns.json_out, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(result, fh, indent=2)
        else:
            if code == 0:
                sys.stdout.write(result["packet"] + "\n")
            else:
                print("assemble.py: REFUSED (exit %d) -- %s" % (code, result.get("reason", "")))
                for r in result.get("refused", []):
                    print("  - %s :: %s" % (r.get("path"), r.get("reason")))
                if result.get("refused_egress"):
                    print("  packet_origin_max=%s offending_tools=%s" % (result["packet_origin_max"], result["offending_tools"]))
        return code

    if not ns.task:
        sys.stderr.write("assemble.py: --task is required\n")
        return 3
    if not ns.views:
        sys.stderr.write("assemble.py: --views requires at least one path\n")
        return 3

    code, result = assemble(ns.task, ns.views, root=ns.root)

    if ns.json_out:
        with open(ns.json_out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(result, fh, indent=2)
    else:
        if code == 0:
            sys.stdout.write(result["packet"] + "\n")
        else:
            print("assemble.py: REFUSED -- %d offending view(s):" % len(result["refused"]))
            for r in result["refused"]:
                print("  - %s :: %s" % (r["path"], r["reason"]))

    return code


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    sys.exit(main(sys.argv))
