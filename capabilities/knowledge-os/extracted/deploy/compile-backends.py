#!/usr/bin/env python3
"""compile-backends.py -- live ABSORB/VERIFY backends for compile-v2.py's
injectable-backend seams (P2-entry wiring). compile-v2.py itself is
cross-vendor certified and is NOT modified by this file; it is imported
read-only (via the same hyphenated-filename importlib pattern compile-v2.py
uses for compile-core.py) so this module's ValidationError raises are the
orchestrator's own class.

AUTONOMY-DISPOSITION DISPATCH GUARD (the "calling daemon" seam; gate C4 --
check-autonomy-disposition.py ships the pure classify()/disposition()
sensor but explicitly disclaims the daemon that CALLS it: "the daemon/
orchestrator that CALLS it is compile-v2 work"). That daemon lives HERE,
not in compile-v2.py/compile-core.py/check-run-diff.py/check-autonomy-
disposition.py -- none of those certified engine files are modified by
this module.

Wire-in contract -- ENFORCED: a compile-v2 driver MUST call run_guarded()/
verify_run_guarded() below instead of calling compile_v2.run()/verify_run()
directly. Both helpers call dispatch_guard(kind, repo, ...) themselves and
raise DispatchRefused (carrying the guard dict) when the permit is False,
so a driver cannot forget the check:

    try:
        result = run_guarded(repo, plan, absorb_backend)
    except DispatchRefused as e:
        ...  # e.guard holds the refused permit record

    try:
        vresult = verify_run_guarded(repo, seq, verify_backend,
                                     authorization=authorization)
    except DispatchRefused as e:
        ...

Both helpers attach the guard's permit record to the result dict under
"dispatch_guard" on success, so the permit decision is journal-adjacent
evidence alongside the run's own sha/seq. verify_run_guarded() derives the
events_views argument to dispatch_guard() mechanically from the compile
record on disk (core.journal_dir) rather than requiring the caller to
reconstruct it -- see verify_run_guarded()'s docstring.

Calling compile_v2.run()/verify_run() directly bypasses the guard entirely
and is reserved for fixtures/self-tests (this module's own self_test(),
and compile-v2.py's own self-test suite) -- never for a live driver.
dispatch_guard() itself remains available for callers that need the permit
decision without dispatching (e.g. --dispatch-check).

derive_dispatch_action() builds the classification action MECHANICALLY
from runtime evidence -- kind, and (for verify) the actual events/views
being transmitted -- never from a caller-supplied label. The action is
then run through check-autonomy-disposition.disposition() against
ENGINE_ACTION_TABLE (an audited two-row table scoped to these two engine
actions, not the illustrative AUDITED_TABLE the sensor ships with).

Two seams, two backends:

  VERIFY  BridgeVerifyBackend.verify(packet) fires the cross-vendor bridge
          CLI (node <bridge>/verify-cli.js --claim ... --evidence-file ...
          --tier T2) exactly the way audit-content-v2.py's _fire_leg does
          (cp1252-safe subprocess: capture_output=True, text=True,
          encoding="utf-8", errors="replace"). The bridge verdict is then
          run through THREE check-substrate.py gates (imported, not
          reimplemented): verified_block_wellformed, substrate_derived_
          from_invocation, substrate_gate_ok. Any gate failure downgrades
          the returned verdict to "substrate-gated" -- which never starts
          with "confirm", so compile-v2.verify_run's candidate stays
          PENDING (the safe state) -- while preserving the original bridge
          verdict under "bridge_verdict" for the record. verify() never
          raises on a bridge/gate failure; a raise is reserved for
          programmer error (e.g. an unwritable staging dir).

          ACCOUNTING (v3.0-23 fix, backlog): a "substrate-gated" downgrade
          with a nested "bridge_verdict" also carries a top-level
          "gated_inner_verdict" (= bridge_verdict["verdict"]) -- e.g. a
          confident "rejected" that got gated for observability reasons
          must not vanish from every downstream tally that reads only the
          top-level "verdict". Pure observability: never read by any
          permit/refusal logic, never the effective verdict -- fail-closed
          is unchanged.

          BANNER CROSS-CHECK: verify-cli.js also writes two human-diagnostic
          lines to STDERR -- an opening "[cross-check] verifier=<vendor>/
          <model> ..." banner (config-derived, printed BEFORE the verifier is
          invoked) and a closing "[cross-check] verdict=... verifier=<vendor>/
          <model>" line (printed after the verdict is parsed). This is a
          SECOND CONVEYANCE CHANNEL of the same invocation wrapper -- both
          lines and the stdout JSON's "verifier" field originate from the
          same verify-cli.js process, so agreement between them is a
          consistency check on that one wrapper, NOT an independent harness
          attestation of who actually ran. The F17 write-path guarantee (a
          harness-level attestation of the invoking substrate) remains the
          declared P4 residual this check does not close. verify() parses
          stderr for the FIRST "verifier=<vendor>/<model>" occurrence and
          compares it against the stdout verdict's verifier block: a
          mismatch downgrades to "substrate-gated" (bridge verdict preserved
          under "bridge_verdict"); an absent banner does not fail anything
          (older bridge builds may not emit it) but is still recorded. Every
          returned dict's "substrate" block carries "banner_crosscheck" in
          {"match", "mismatch", "absent"}.

  ABSORB  emit_packets() is a plan -> work-packet compiler: for each plan
          item it writes a self-contained ABSORB work packet (the spec
          sec.7 "slim contract": view + delta events + routing rules +
          invariant 4 verbatim + the sec.9 untrusted-data rule verbatim +
          the answer-file contract) plus a dispatch-manifest.json that an
          orchestrator fills in with which model/vendor answered which
          packet. FileHandoffAbsorbBackend.absorb() is the other half: it
          resolves an already-produced answer JSON (matched to the view via
          the dispatch manifest) and hands it back to compile-v2.run()
          verbatim -- ALL structural/content validation happens inside
          compile-v2.validate_absorb_output(), never here.

Both backends are fixture-first: every self-test path runs with NO network
and NO real node subprocess -- BridgeVerifyBackend takes an injectable
`runner` callable(args_list) -> (returncode, stdout, stderr); the default
runner is the only place that shells out for real.

Usage: compile-backends.py --self-test
Exit: 0 all self-test cases pass | 1 a case failed.
"""

import fnmatch
import hashlib
import importlib.util
import json
import os
import posixpath
import re
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(basename, alias):
    spec = importlib.util.spec_from_file_location(alias,
                                                  os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


compile_v2 = _load("compile-v2.py", "compile_v2_ref")
substrate = _load("check-substrate.py", "check_substrate_ref")
autonomy = _load("check-autonomy-disposition.py", "check_autonomy_disposition_ref")
path_containment = _load("check-path-containment.py", "check_path_containment_ref")
try:
    origin = _load("origin.py", "origin_ref")
except Exception:  # pragma: no cover -- module load failure must not crash the guard
    origin = None
try:
    staleness = _load("staleness.py", "staleness_ref")
except Exception:  # pragma: no cover
    staleness = None

ValidationError = compile_v2.ValidationError

# ------------------------------------------------------- AUTONOMY-DISPOSITION dispatch

# The audited action table for THIS engine's two dispatch points (gate C4 shape:
# {tool: (reversibility, verifiability, blast, egress, sec-crit)}). Scoped to exactly
# the two actions compile-v2 drivers take; deliberately NOT the sensor's illustrative
# AUDITED_TABLE (that one is fixture material for check-autonomy-disposition.py itself).
ENGINE_ACTION_TABLE = {
    # stage-only commit: git revert exists mechanically (reversible), and
    # check-run-diff.py is the deterministic verifier of the produced commit.
    "compile-absorb-commit": ("reversible", "deterministic", "low", False, False),
    # cross-vendor bridge transmission: cannot be unsent (irreversible egress), but
    # the verdict ingest is deterministically gated (check-substrate.py three gates).
    "bridge-verify-egress": ("irreversible", "deterministic", "low", True, False),
}


def _fallback_human_prefixes(origin_config_path=None):
    """Read deploy/origin-config.yaml's `human_prefixes:` DIRECTLY, without importing
    origin.py -- this helper exists precisely for the branch where origin.py itself failed
    to load, so it structurally CANNOT delegate to origin.load_origin_config()/
    is_human_named() (the whole point of this fallback is to survive that module's
    absence). Same fail-safe contract as origin.py's own loader: file absent, PyYAML
    unavailable, unreadable, or structurally unparseable/malformed all degrade to an EMPTY
    list -- never a crash, never an assumed prefix, never a hardcoded name baked into this
    guard. `origin_config_path` overrides the default (self-test seam; production callers
    never pass it, so they always read the live per-instance config)."""
    path = origin_config_path or os.path.join(_HERE, "origin-config.yaml")
    try:
        import yaml
    except ImportError:
        return []
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = yaml.safe_load(fh.read()) or {}
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("human_prefixes")
    if not isinstance(raw, list):
        return []
    prefixes = []
    for p in raw:
        # STRICT member validation (cross-vendor review finding; IDENTICAL to
        # origin.load_origin_config's rule -- the two parsers must never
        # diverge): a non-string member (dict/number/None) or an empty/
        # whitespace-only string marks the ENTIRE config malformed -> empty
        # prefixes (fail-safe), never stringified-and-kept. An empty-string
        # member would otherwise compile to '^-' and match ANY '-'-containing
        # stem.
        if not isinstance(p, str) or not p.strip():
            return []
        prefixes.append(p.strip())
    return prefixes


def _origin_of_event_rel_fallback(event_rel, origin_config_path=None):
    """DOCUMENTED FALLBACK ONLY -- the basename-prefix rule, used when the
    canonical origin.py classification is unavailable (module load failure,
    missing/unreadable file, etc). Mirrors origin.py's rule 2/3: basename
    after the date prefix matching one of deploy/origin-config.yaml's
    CONFIGURED human_prefixes (`ryan` on this fork; read directly via
    _fallback_human_prefixes above, since origin.py itself is unavailable in
    this branch by construction) -> "human"; "session-..." -> "corpus-first-
    party"; anything else -> "unknown" (fail-dangerous unchanged). This is
    deliberately cruder than origin.py's real classification (which also
    resolves e.g. "*-findings.md"/"*-lock.md" corpus/human events via
    frontmatter parseability + the B2 attestation file) -- it exists only so
    the guard degrades safely rather than crashing when the canonical source
    cannot be consulted. `origin_config_path` overrides the config file
    (self-test seam; production callers never pass it)."""
    base = os.path.basename(event_rel)
    if base.endswith(".md"):
        base = base[:-3]
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}(?:T\d{6})?-", "", base)
    for prefix in _fallback_human_prefixes(origin_config_path):
        if re.match(r"^%s-" % re.escape(prefix), stem, re.IGNORECASE):
            return "human"
    if re.match(r"^session-", stem, re.IGNORECASE):
        return "corpus-first-party"
    return "unknown"


def _origin_of_event_rel(repo, event_rel, origin_config_path=None):
    """Origin derivation for the dispatch guard. Consults the CANONICAL
    origin.py classification (the same F7 rule + origin-attestations.yaml
    the B2 census uses -- see backfill-derivation.py's event_parseable() /
    compute_origin_max() for the reference call pattern this mirrors) and
    maps its vocabulary onto the guard's: "human" -> "human"; "corpus" ->
    "corpus-first-party"; anything else (unknown/session-derived/vendor-ref/
    external-scrape/a raised error) -> "unknown" (fail-dangerous unchanged).

    `session-derived` (R-1, 2026-07-22) is named in that list deliberately
    rather than left to the catch-all: an R-1 session candidate is staged,
    un-dispositioned, model-mediated content, and the guard must treat it as
    untrusted -- the catch-all already does, and naming it here keeps that a
    stated decision rather than an accident of enumeration.

    Returns (guard_origin, source) where source is "canonical" when
    origin.py actually produced the classification, or "fallback-prefix"
    when we fell back to the basename-prefix rule (origin.py unavailable,
    or the event file could not be read) -- recorded so the derived action
    params never silently claim a canonical answer that wasn't obtained.

    A module load failure or any other exception from origin.py must NOT
    crash the guard: on failure we fall back to the basename-prefix rule.

    The canonical path REQUIRES both origin.py AND staleness.py to have
    loaded successfully -- staleness.py is what actually determines
    frontmatter parseability, so without it there is no honest way to
    compute `parseable` and the whole canonical classification would be
    a guess. If either module is unavailable, we take the same
    basename-prefix fallback used for an origin.py load failure.

    Within the canonical path, a per-event read/parse failure (the file
    exists but raises while being read or frontmatter-split) does NOT
    fall back to the prefix rule and does NOT assume parseable=True --
    it classifies that single event "unknown" directly (fail-dangerous),
    since a raised error is itself evidence we could not determine
    parseability, not evidence that the content was fine.

    `origin_config_path` is a self-test seam threaded to BOTH classification
    paths: the fallback's own config read (_fallback_human_prefixes) AND the
    canonical origin.assign_origin() call (its origin_config_path seam) --
    production callers never pass it, so live classification always reads the
    live deploy/origin-config.yaml on either path."""
    if origin is None or staleness is None:
        return _origin_of_event_rel_fallback(event_rel, origin_config_path), "fallback-prefix"
    try:
        fp = os.path.join(repo, event_rel.replace("/", os.sep))
        if os.path.isfile(fp):
            try:
                with open(fp, "r", encoding="utf-8-sig") as fh:
                    text = fh.read()
                status, _payload = staleness.split_frontmatter(text)
                parseable = status == "ok"
            except Exception:  # pragma: no cover -- per-event failure must not
                # be treated as parseable; fail-dangerous straight to
                # "unknown" rather than assuming parseable=True.
                return "unknown", "canonical"
        else:
            # absent event file -> treat as parseable-neutral, same
            # convention backfill-derivation.py's event_parseable() uses;
            # the rule then keys on the filename.
            parseable = True
        attestations = origin.load_attestations(
            os.path.join(_HERE, "origin-attestations.yaml"))
        attestation = attestations.get(event_rel.replace("\\", "/"))
        canonical_origin, _basis, _attested = origin.assign_origin(
            event_rel, parseable, attestation=attestation,
            origin_config_path=origin_config_path)
    except Exception:  # pragma: no cover -- canonical source must not crash the guard
        return _origin_of_event_rel_fallback(event_rel, origin_config_path), "fallback-prefix"

    if canonical_origin == "human":
        return "human", "canonical"
    if canonical_origin == "corpus":
        return "corpus-first-party", "canonical"
    return "unknown", "canonical"


def _origin_max(origins):
    """max = worst origin present, named honestly (not origin.py's restrictiveness
    order, which has 5 rungs -- this guard only ever sees {human, corpus-first-party,
    unknown} because those are the only three _origin_of_event_rel can return)."""
    origins = set(origins)
    if not origins or origins <= {"human"}:
        return "human"
    if origins <= {"human", "corpus-first-party"}:
        return "corpus-first-party"
    return "unknown"


def derive_dispatch_action(kind, repo, plan=None, packet_paths=None,
                           events_views=None):
    """Mechanically derive the {tool, params} dispatch action from runtime
    evidence -- NEVER from a caller-supplied classification. `plan`/
    `packet_paths` are accepted for signature-compatibility with a future
    plan-shaped caller but are unused for kind="verify" here: this module
    instead takes the simpler `events_views=[(event_rel, view_rel), ...]`
    list directly (the pending noop candidates' event+view rels), which is
    cleaner than re-deriving the same list by re-parsing `plan` when the
    caller already has it in hand from compile_v2.run()'s noop_candidates.

    The returned params carry ONLY the documented mechanical keys -- no
    "agent_claims"/"self_report" key is ever produced or read here."""
    if kind == "compile":
        return {"tool": "compile-absorb-commit",
                "params": {"undo_registered": True}}
    if kind == "verify":
        events_views = events_views or []
        origins = set()
        origin_sources = set()
        for event_rel, view_rel in events_views:
            o, source = _origin_of_event_rel(repo, event_rel)
            origins.add(o)
            origin_sources.add(source)
            if view_rel and view_rel.replace("\\", "/").startswith("wiki/"):
                origins.add("corpus-first-party")
        origin_max = _origin_max(origins)
        # honest per the whole batch: "canonical" only if EVERY event's
        # origin was actually resolved via origin.py; any fallback in the
        # batch makes the recorded source "fallback-prefix".
        origin_source = ("canonical" if origin_sources
                         and origin_sources == {"canonical"}
                         else "fallback-prefix")
        return {"tool": "bridge-verify-egress",
                "params": {
                    "egress": True,
                    "payload_shape": "closed-grammar-structural",
                    "payload_value_origins": sorted(origins),
                    "payload_origin_max": origin_max,
                    "mechanical_canceller": False,
                    "canceller_pre_transmission": False,
                    "origin_source": origin_source,
                }}
    raise ValueError("derive_dispatch_action: unknown kind %r "
                     "(expected 'compile' or 'verify')" % (kind,))


# AUTHORIZATION-ARTIFACT CLASS (2026-07-06 class constraint, see
# dispatch_guard's docstring): the ONLY repo-relative path shape a HUMAN-GATE
# authorization may name. AUTHORIZATION_ARTIFACT_CLASS_GLOB stays the single
# named/documented class string -- refusal messages keep naming it -- but
# the check itself is STRUCTURAL (see _is_authorization_artifact_class),
# not a raw fnmatch of the whole path: fnmatch's `*` matches `/` too, so
# fnmatchcase("deploy/evidence/operator-sub/nested.md",
# "deploy/evidence/operator-*.md") is TRUE -- a nested path under
# deploy/evidence/ can smuggle its way past the class check merely by
# having a first path segment that happens to start with "operator-".
# Case-sensitive throughout (the real artifacts on disk are lowercase
# "operator-...", and this deliberately does not accept a case-insensitive
# match of some other casing).
AUTHORIZATION_ARTIFACT_CLASS_GLOB = "deploy/evidence/operator-*.md"


def _is_authorization_artifact_class(repo_relative_posix_path):
    """True iff `repo_relative_posix_path` (already POSIX-normalized,
    repo-relative -- e.g. as produced by resolve_within + os.path.relpath)
    is DIRECTLY under `deploy/evidence` (no deeper nesting) with a basename
    matching `operator-*.md`.

    Structural check, not a whole-path fnmatch: the path is split into
    (directory, basename) and the directory must equal exactly
    "deploy/evidence" -- string equality, so no directory-component
    shenanigans -- with only the basename fnmatch'd against `operator-*.md`.
    This is deliberate: fnmatch's `*` matches `/` as readily as any other
    character, so a single fnmatchcase call against the full glob would
    let "deploy/evidence/operator-sub/nested.md" (a nested path one level
    deeper than the class allows) pass, since `*` in the pattern happily
    spans the extra `/`. Splitting first and comparing the directory
    exactly closes that gap structurally rather than relying on the glob
    engine to enforce a no-nesting rule it does not actually enforce."""
    directory, basename = posixpath.split(repo_relative_posix_path)
    return (directory == "deploy/evidence"
            and fnmatch.fnmatchcase(basename, "operator-*.md"))


def dispatch_guard(kind, repo, events_views=None, authorization=None):
    """The AUTONOMY-DISPOSITION dispatch guard. Derives the action mechanically
    (derive_dispatch_action), classifies it (check-autonomy-disposition.
    disposition against ENGINE_ACTION_TABLE), and decides permit per the
    orchestrator's ruling:

      - any AUTONOMOUS* disposition -> permit True, no authorization needed.
      - HUMAN-GATE citing free-selection egress (rung-4) or an untrusted
        origin_max (A-7) -> permit False ALWAYS, even with authorization:
        these demand a per-action operator ack this guard cannot supply.
      - any OTHER HUMAN-GATE -> permit True only if `authorization` is
        {"path": <file under repo>, "quote": <string>}, the path is in the
        AUTHORIZATION-ARTIFACT CLASS (below), and the quote appears verbatim
        in that file's text; the returned record then carries the file's
        path and sha256. Missing/mismatched authorization, or a path outside
        the class, -> permit False, reason names what failed.

    AUTHORIZATION-ARTIFACT CLASS CONSTRAINT (2026-07-06, r3 confirm-slice
    lesson): P4 coda observed an agent satisfy this gate with a quote pulled
    from a DESIGN-STATUS document -- prior to this constraint, ANY repo file
    could satisfy the gate merely by containing the quoted string, which
    means a HUMAN-GATE could be "satisfied" by a document nobody wrote as an
    authorization at all. The class is now checked explicitly: the
    authorization path's repo-relative, POSIX-normalized form must live
    directly under `deploy/evidence/` with a basename matching
    `operator-*.md` (case-sensitive). This mirrors the real operator
    authorization artifacts already on disk (e.g.
    deploy/evidence/operator-authorization-2026-07-04-autonomous-
    verification.md, deploy/evidence/operator-decision-2026-07-05-ops4-
    accepted.md). A path outside this class is refused BEFORE the
    quote/existence checks run -- wrong-document-class is now unsatisfiable,
    not merely unlikely.

    `events_views`/`authorization` are read literally; any "agent_claims" or
    "self_report" key smuggled into a caller's structures is never read by
    this function or by derive_dispatch_action -- both take only the
    documented positional/keyword shape, so there is no code path that could
    read such a key even if a caller tried to attach one.
    """
    action = derive_dispatch_action(kind, repo, events_views=events_views)
    disp, reason = autonomy.disposition(action, ENGINE_ACTION_TABLE)

    if disp.startswith(autonomy.AUTONOMOUS):
        return {"disposition": disp, "reason": reason, "permit": True,
                "authorization": None}

    if disp == autonomy.HUMAN_GATE:
        hard_refuse = ("free-selection" in reason) or ("A-7" in reason)
        if hard_refuse:
            return {"disposition": disp, "reason": reason, "permit": False,
                    "authorization": None}
        if not authorization:
            return {"disposition": disp,
                    "reason": reason + " -- no authorization supplied",
                    "permit": False, "authorization": None}
        auth_path = authorization.get("path")
        auth_quote = authorization.get("quote")
        if not auth_path or not auth_quote:
            return {"disposition": disp,
                    "reason": reason + " -- authorization missing path/quote",
                    "permit": False, "authorization": None}
        try:
            abs_path = path_containment.resolve_within(repo, auth_path)
        except path_containment.PathContainmentError:
            return {"disposition": disp,
                    "reason": reason + " -- authorization path escapes repo",
                    "permit": False, "authorization": None}
        repo_rel_posix = os.path.relpath(abs_path, repo).replace(os.sep, "/")
        if not _is_authorization_artifact_class(repo_rel_posix):
            return {"disposition": disp,
                    "reason": reason + " -- authorization file not in the "
                              "operator-artifact class %s: %s"
                              % (AUTHORIZATION_ARTIFACT_CLASS_GLOB, auth_path),
                    "permit": False, "authorization": None}
        if not os.path.isfile(abs_path):
            return {"disposition": disp,
                    "reason": reason + " -- authorization file not found: %s"
                              % auth_path,
                    "permit": False, "authorization": None}
        text = open(abs_path, encoding="utf-8").read()
        if auth_quote not in text:
            return {"disposition": disp,
                    "reason": reason + " -- authorization quote not found "
                              "verbatim in file",
                    "permit": False, "authorization": None}
        record = {"path": auth_path, "sha256": _sha256(text)}
        return {"disposition": disp, "reason": reason, "permit": True,
                "authorization": record}

    # any disposition string not explicitly handled above is treated as a
    # gate (default-dangerous, mirrors the sensor's own posture)
    return {"disposition": disp, "reason": reason, "permit": False,
            "authorization": None}


class DispatchRefused(Exception):
    """Raised by run_guarded()/verify_run_guarded() when dispatch_guard()
    returns permit=False. Carries the full guard dict (disposition, reason,
    permit, authorization) as .guard so a caller can inspect why."""

    def __init__(self, guard):
        self.guard = guard
        super().__init__("dispatch refused: %s" % guard.get("reason"))


def _pending_events_views(repo, compile_seq):
    """Mechanically derive the [(event_rel, view_rel), ...] pairs that a
    verify_run() call over `compile_seq` would actually transmit: read the
    compile record at receipts/journal/<compile_seq>.json (via compile_v2's
    own core.journal_dir helper -- the same path compile_v2.verify_run()
    itself reads) and collect (event, view) from its noop_candidates that
    are unverified and not already CONSUMED. This is exactly the set
    verify_run() iterates over (it skips nc.get("verified") or
    disposition == "CONSUMED"), so the guard sees the same events/views the
    engine is about to transmit. Returns [] if the record has none pending."""
    jd = compile_v2.core.journal_dir(repo)
    rec_path = os.path.join(jd, "%d.json" % compile_seq)
    rec = json.load(open(rec_path, encoding="utf-8"))
    pairs = []
    for nc in rec.get("noop_candidates", []):
        if nc.get("verified") or nc.get("disposition") == "CONSUMED":
            continue
        pairs.append((nc["event"], nc["view"]))
    return pairs


def run_guarded(repo, plan, absorb_backend, authorization=None, **kwargs):
    """ENFORCED entry point for the compile dispatch. Calls dispatch_guard
    ("compile", repo) and raises DispatchRefused when not permitted;
    otherwise dispatches to compile_v2.run() and attaches the guard's
    permit record to the result under "dispatch_guard" -- journal-adjacent
    evidence of the permit decision that authorized this run."""
    guard = dispatch_guard("compile", repo, authorization=authorization)
    if not guard["permit"]:
        raise DispatchRefused(guard)
    result = compile_v2.run(repo, plan, absorb_backend, **kwargs)
    result["dispatch_guard"] = guard
    return result


def verify_run_guarded(repo, compile_seq, verify_backend, authorization=None,
                       **kwargs):
    """ENFORCED entry point for the verify dispatch. Derives events_views
    mechanically from the compile record itself (_pending_events_views --
    the exact (event, view) pairs verify_run() would assemble packets for),
    calls dispatch_guard("verify", repo, events_views=..., authorization=
    authorization), and raises DispatchRefused when not permitted -- BEFORE
    compile_v2.verify_run() is ever called, so a refusal journals nothing.
    On permit, dispatches to compile_v2.verify_run() and attaches the
    guard's permit record to the result under "dispatch_guard"."""
    events_views = _pending_events_views(repo, compile_seq)
    guard = dispatch_guard("verify", repo, events_views=events_views,
                          authorization=authorization)
    if not guard["permit"]:
        raise DispatchRefused(guard)
    result = compile_v2.verify_run(repo, compile_seq, verify_backend,
                                   **kwargs)
    result["dispatch_guard"] = guard
    return result

# --------------------------------------------------------------- spec verbatim
INVARIANT_4 = (
    "4. **Never delete knowledge** — supersede + link; cap breach never "
    "trims; retention is a verify question;\n"
    "   journal corruption is quarantined, never deleted (§6)."
)

# the FULL §9 bullet (spec lines 247-250), verbatim including the leading
# "- " list marker, line breaks, and the two-space continuation indents
S9_UNTRUSTED_RULE = (
    "- **Quarantine by task type, not view tier (F8).** Any **build/fix** "
    "packet excludes\n"
    "  `external-scrape`/`unknown`-origin content **regardless of the "
    "view's tier** — untrusted text never\n"
    "  reaches a code-writing worker. Banners (not exclusion) remain only "
    "for recon/verify tasks. (Full\n"
    "  per-span taint provenance, needed only to safely *include* untrusted "
    "spans, is deferred to the template.)"
)

ABSORB_SLIM_CONTRACT = (
    "a **slim contract** (view + delta events + routing rules + invariant 4 "
    "verbatim + the\n§9 untrusted-data rule)"
)


def _canonical_manifest_bytes(manifest_no_dispatch):
    return json.dumps(manifest_no_dispatch, indent=1, sort_keys=True).encode("utf-8")


# The three legal provenance classes for the identity a dispatch stamp
# carries (G3, silence-sweep remediation 2026-08-05). The stamped identity
# becomes the permanent sha-committed absorb-side attestation, so WHERE it
# came from is recorded alongside WHAT it says -- and "the session typed what
# it believes it is" is not a legal source (the exact anti-pattern /handoff
# names: typing identity instead of copying an attestation):
#   attestation:<record>            a mechanical record (an F17 attestation
#                                   sidecar, a runtime-written identity file,
#                                   or a named test fixture)
#   operator-attested:<date/note>   an interactive session: the operator
#                                   confirmed the identity from their own UI,
#                                   in their own words, this run
#   scheduled-invocation:<task>     an unattended run: identity copied from
#                                   the scheduled command line that pinned
#                                   the model (invocation metadata)
IDENTITY_SOURCE_CLASSES = ("attestation:", "operator-attested:",
                           "scheduled-invocation:")


def _identity_source_wellformed(identity_source):
    """(ok, reason): a non-empty string in one of the legal classes above,
    with a non-empty payload after the class prefix."""
    if not isinstance(identity_source, str) or not identity_source.strip():
        return False, (
            "identity_source is required: how was this model/vendor identity "
            "obtained? Legal forms: %s -- each with a non-empty payload. "
            "Typing an identity from memory is not a source."
            % ", ".join("'%s<...>'" % c for c in IDENTITY_SOURCE_CLASSES))
    for cls in IDENTITY_SOURCE_CLASSES:
        if identity_source.startswith(cls):
            if identity_source[len(cls):].strip():
                return True, "ok"
            return False, ("identity_source '%s' has an empty payload after "
                           "its class prefix" % identity_source)
    return False, (
        "identity_source '%s' is not in a legal class (%s)"
        % (identity_source, ", ".join(IDENTITY_SOURCE_CLASSES)))


def stamp_dispatch(manifest_path, model, vendor, identity_source=None):
    """F17 absorb-side attestation (2026-07-05 design), channel `dispatch-record`.

    The ONLY way a dispatch-manifest.json's packet entries get a real
    model/vendor after emit_packets() leaves them null. Stamps EVERY packet
    entry with `model`/`vendor`, and adds a top-level `dispatch` block
    {model, vendor, identity_source, stamped_at, manifest_sha256_at_stamp}
    where the sha256 covers the manifest JSON canonically serialized WITH the
    dispatch block EXCLUDED (so the stamp commits to the packet contents it
    applies to, without a self-referential hash-of-itself problem).

    REFUSALS (G3, 2026-08-05 -- this function used to validate nothing, and
    the skill's example text invited literally typing "<your model id>"):
    empty/placeholder model or vendor (angle brackets are the placeholder
    tell), a combined vendor string (F18 requires separate fields; '/' or ':'
    in vendor is the combined-string tell, mirroring check-substrate), and a
    missing or out-of-class identity_source (see IDENTITY_SOURCE_CLASSES).

    Returns the stamped manifest dict (also rewritten to manifest_path).
    """
    for label, value in (("model", model), ("vendor", vendor)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("stamp_dispatch: %s is empty -- the dispatch "
                             "stamp is the permanent absorb-side attestation "
                             "and cannot be blank" % label)
        if "<" in value or ">" in value:
            raise ValueError(
                "stamp_dispatch: %s %r looks like an unfilled placeholder -- "
                "supply the real identity, never the template text" % (label, value))
    if "/" in vendor or ":" in vendor:
        raise ValueError(
            "stamp_dispatch: vendor %r looks like a combined vendor/model "
            "string -- F18 requires separate fields" % vendor)
    src_ok, src_reason = _identity_source_wellformed(identity_source)
    if not src_ok:
        raise ValueError("stamp_dispatch: %s" % src_reason)
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    for entry in manifest.get("packets") or []:
        entry["model"] = model
        entry["vendor"] = vendor
    manifest.pop("dispatch", None)  # exclude any prior stamp before hashing
    manifest_sha = _sha256_bytes(_canonical_manifest_bytes(manifest))
    manifest["dispatch"] = {
        "model": model,
        "vendor": vendor,
        "identity_source": identity_source,
        "stamped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "manifest_sha256_at_stamp": manifest_sha,
    }
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
    return manifest


def _verify_dispatch_stamp(manifest):
    """Shared refusal logic: is `manifest`'s top-level `dispatch` block
    well-formed AND does its manifest_sha256_at_stamp match a recompute over
    the manifest with the dispatch block excluded? Returns (ok, reason)."""
    dispatch = manifest.get("dispatch")
    if not isinstance(dispatch, dict):
        return False, "manifest has no well-formed top-level 'dispatch' stamp"
    required = ("model", "vendor", "identity_source", "stamped_at",
                "manifest_sha256_at_stamp")
    missing = [k for k in required if not dispatch.get(k)]
    if missing:
        return False, "dispatch stamp missing field(s): %s" % ", ".join(missing)
    src_ok, src_reason = _identity_source_wellformed(dispatch.get("identity_source"))
    if not src_ok:
        return False, "dispatch stamp identity_source rejected: %s" % src_reason
    check = dict(manifest)
    check.pop("dispatch", None)
    recomputed = _sha256_bytes(_canonical_manifest_bytes(check))
    if recomputed != dispatch["manifest_sha256_at_stamp"]:
        return False, ("dispatch stamp sha256 mismatch: recomputed %s != "
                       "stamped %s" % (recomputed, dispatch["manifest_sha256_at_stamp"]))
    return True, "ok"


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------- verify backend
# First "verifier=<vendor>/<model>" occurrence in the bridge CLI's stderr (the
# opening config-derived banner or, absent that, the closing verdict line --
# either way we take the FIRST match per the fix spec). Vendor is constrained
# to [a-z0-9_-]; model is greedy-safe (\S+) since model ids can carry dots.
_BANNER_VERIFIER_RE = re.compile(r"verifier=([a-z0-9_-]+)/(\S+)", re.IGNORECASE)

# How much of the bridge's stderr survives into a fail-closed verdict's `reason`
# (and therefore into the receipts/verify/*.json artifact a later session reads).
# Raised 200 -> 2000 on 2026-07-28 (backlog v3.0-68): the server's opening banner
# names the RESOLVED codex binary, and at 200 chars that line was always clipped
# away -- so the seq-103 live failure's journaled evidence said "bridge exited 1"
# and nothing about WHICH binary had been resolved, which is the one fact that
# would have named the root cause (a scrubbed-env resolution to a version-gated
# install) on the spot.
BRIDGE_STDERR_EXCERPT_CHARS = 2000


def _default_runner(args):
    p = subprocess.run(args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr


class BridgeVerifyBackend:
    """VERIFY leg: fires the cross-vendor bridge CLI, then gates the result
    through check-substrate.py before it is allowed to flip a candidate.

    F17 absorb identity (2026-07-05 design): `absorb_vendor`/`absorb_model_id`
    bare constructor literals are kept ONLY as a back-compat path for the
    self-test fixtures below (clearly marked at each call site) -- a REAL run
    must instead pass `dispatch_manifest_path` (the dispatch-manifest.json a
    dispatching code path has already run stamp_dispatch() over), and the
    absorb vendor/model are DERIVED from that stamp, never from a literal.
    There is no raw-dict ("dispatch_block") shortcut into the attested
    dispatch-record channel: the ONLY way to obtain `_absorb_channel ==
    "dispatch-record"` is a manifest path that passes _verify_dispatch_stamp
    (well-formedness + a recomputed manifest_sha256_at_stamp match), the same
    check FileHandoffAbsorbBackend runs. Any mismatch or malformed stamp
    fails closed with ValidationError at construction time -- it can never
    surface as a verify verdict. When both a stamp source and bare literals
    are given, the stamp wins and the literals are ignored (documented, not
    silently merged)."""

    def __init__(self, repo, absorb_vendor=None, absorb_model_id=None,
                 gate_kind="routine", tier="T2", timeout_ms=None,
                 staging=None, runner=None, dispatch_manifest_path=None):
        self.repo = repo
        self.gate_kind = gate_kind
        self.tier = tier
        # v3.0-20: unpinned callers honor the operator's VERIFY_TIMEOUT_MS
        # export -- verify-cli overwrites the inherited env with the
        # explicit --timeout-ms arg below, so the resolution must happen
        # HERE. Default unchanged (180000) when the env is unset. An
        # explicit caller pin always wins and skips THIS constructor's env
        # resolution; pin 0 means "emit no --timeout-ms flag" (unchanged
        # legacy semantic -- verify-cli then falls back to its OWN
        # env/180000 default server-side, so 0 defers, it does not disable).
        if timeout_ms is None:
            raw = os.environ.get("VERIFY_TIMEOUT_MS")
            try:
                timeout_ms = int(raw) if raw else 180000
            except ValueError:
                timeout_ms = 180000
        self.timeout_ms = timeout_ms
        self.staging = staging or os.path.join(repo, "receipts", "verify",
                                               "packets")
        self.runner = runner or _default_runner

        stamp_source = None
        if dispatch_manifest_path is not None:
            with open(dispatch_manifest_path, encoding="utf-8") as fh:
                manifest = json.load(fh)
            stamp_ok, stamp_reason = _verify_dispatch_stamp(manifest)
            if not stamp_ok:
                raise ValidationError(
                    "dispatch stamp refused for BridgeVerifyBackend absorb "
                    "identity: %s" % stamp_reason)
            stamp_source = manifest["dispatch"]

        if stamp_source is not None:
            self.absorb_vendor = stamp_source["vendor"]
            self.absorb_model_id = stamp_source["model"]
            self._absorb_channel = "dispatch-record"
        else:
            # BACK-COMPAT FIXTURE PATH ONLY (documented above) -- real runs
            # must supply dispatch_manifest_path/dispatch_block instead.
            self.absorb_vendor = absorb_vendor
            self.absorb_model_id = absorb_model_id
            self._absorb_channel = None

    def _bridge_dir(self):
        return os.environ.get("CROSS_VENDOR_BRIDGE_DIR") or os.path.join(
            self.repo, ".claude", "skills", "bridge")

    @staticmethod
    def _extract_claim(packet):
        for line in packet.splitlines():
            if line.startswith("CLAIM: "):
                return line[len("CLAIM: "):].strip()
        return None

    @staticmethod
    def _extract_packet_id(packet):
        # matches both header forms: per-candidate seqN-M (pre-amendment) and
        # per-event union seqN-eM (verify-unit amendment 2026-07-05)
        m = re.search(r"^# NOOP VERIFY PACKET (seq\d+-e?\d+)", packet, re.M)
        if m:
            return m.group(1)
        # absorption-verify packet (2026-07-05 amendment): the -v
        # discriminator keeps this unambiguous vs the no-op -e form ONLY --
        # nothing else about this function changes.
        m = re.search(r"^# ABSORPTION VERIFY PACKET (seq\d+-v\d+)", packet,
                      re.M)
        if m:
            return m.group(1)
        return _sha256(packet)[:12]

    def _write_attest_record(self, pid, attestation, wrapper_verifier_claim,
                             evidence_path):
        """F17 verifier-side attestation (2026-07-05 design): writes the
        attestation record to receipts/verify/attest/<packet-stem>.attest.json,
        adding packet_sha256 (of the packet/evidence file this leg verified)
        and the raw wrapper verifier claim for forensics. Returns the
        repo-relative path (POSIX separators), or the absolute path if the
        record falls outside the repo tree (mirrors the evidence_rel pattern)."""
        attest_dir = os.path.join(self.repo, "receipts", "verify", "attest")
        os.makedirs(attest_dir, exist_ok=True)
        attest_path = os.path.join(attest_dir, "%s.attest.json" % pid)
        packet_sha256 = _sha256(open(evidence_path, encoding="utf-8").read())
        record = dict(attestation)
        record["packet_sha256"] = packet_sha256
        record["wrapper_verifier_claim"] = wrapper_verifier_claim
        with open(attest_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(record, fh, indent=1, sort_keys=True)
        try:
            return os.path.relpath(attest_path, self.repo).replace(os.sep, "/")
        except ValueError:
            return attest_path.replace(os.sep, "/")

    @staticmethod
    def _fail_closed(verdict, reason, stderr_text=""):
        # These are pre-verdict failures (no CLAIM line, nonzero exit, empty
        # or unparseable stdout) -- there is no stdout verifier field to
        # cross-check against yet, but "banner_crosscheck" is still recorded
        # per-path: "absent" if the banner never appeared in stderr at all,
        # else "n/a" (banner present but nothing to compare it to).
        banner_crosscheck = ("n/a" if _BANNER_VERIFIER_RE.search(
            stderr_text or "") else "absent")
        return {"verdict": verdict, "reason": reason, "uncertainty": "unknown",
                "verifier": {},
                "substrate": {"banner_crosscheck": banner_crosscheck},
                "evidence_file": ""}

    def verify(self, packet):
        claim = self._extract_claim(packet)
        if not claim:
            return self._fail_closed("bridge-error",
                                     "packet carries no CLAIM: line")

        os.makedirs(self.staging, exist_ok=True)
        pid = self._extract_packet_id(packet)
        evidence_name = "packet-%s.md" % pid
        evidence_path = os.path.join(self.staging, evidence_name)
        with open(evidence_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(packet)
        try:
            evidence_rel = os.path.relpath(evidence_path, self.repo).replace(
                os.sep, "/")
        except ValueError:
            evidence_rel = evidence_path.replace(os.sep, "/")

        bridge = self._bridge_dir()
        args = ["node", os.path.join(bridge, "verify-cli.js"),
                "--claim", claim, "--evidence-file", evidence_path,
                "--tier", self.tier]
        if self.timeout_ms:
            args += ["--timeout-ms", str(self.timeout_ms)]

        rc, stdout, stderr = self.runner(args)
        if rc != 0:
            out = self._fail_closed(
                "bridge-error",
                "bridge exited %s: %s"
                % (rc, (stderr or "")[-BRIDGE_STDERR_EXCERPT_CHARS:]),
                stderr_text=stderr)
            out["evidence_file"] = evidence_rel
            return out
        if not (stdout or "").strip():
            out = self._fail_closed(
                "bridge-error",
                "bridge produced no stdout: %s"
                % (stderr or "")[-BRIDGE_STDERR_EXCERPT_CHARS:],
                stderr_text=stderr)
            out["evidence_file"] = evidence_rel
            return out
        try:
            bridge_verdict = json.loads(stdout.strip())
        except (ValueError, TypeError):
            out = self._fail_closed(
                "bridge-error",
                "bridge stdout not parseable JSON: %s"
                % (stderr or "")[-BRIDGE_STDERR_EXCERPT_CHARS:],
                stderr_text=stderr)
            out["evidence_file"] = evidence_rel
            return out

        verifier = bridge_verdict.get("verifier") or {}
        attestation = bridge_verdict.get("attestation")

        # F17 write-path attestation gate (2026-07-05 design). Fail-closed
        # (verdict -> substrate-gated, substrate_source NOT set to
        # invocation-metadata) when: no attestation object came back; a
        # present runtime_model disagrees with argv_model; or the model
        # DERIVED from the attestation disagrees with the wrapper's own
        # stdout verifier.model claim (the old declared channel becomes a
        # cross-check, never the source of truth).
        attest_reason = None
        if not isinstance(attestation, dict):
            attest_reason = "no attestation object returned by the bridge"
        else:
            argv_model = attestation.get("argv_model")
            runtime_model = attestation.get("runtime_model")
            if runtime_model is not None and runtime_model != argv_model:
                attest_reason = (
                    "attestation argv/runtime model disagreement: "
                    "argv_model=%r runtime_model=%r" % (argv_model, runtime_model))
            else:
                derived_verifier_model = runtime_model if runtime_model is not None else argv_model
                wrapper_claim = verifier.get("model")
                if derived_verifier_model != wrapper_claim:
                    attest_reason = (
                        "attestation/wrapper-claim disagreement: derived "
                        "verifier model=%r != wrapper verifier.model=%r" % (
                            derived_verifier_model, wrapper_claim))

        attestation_ok_flag = attest_reason is None
        derived_verifier_model_id = None
        attest_record_rel = None
        if attestation_ok_flag:
            derived_verifier_model_id = (
                attestation.get("runtime_model")
                if attestation.get("runtime_model") is not None
                else attestation.get("argv_model"))

        block = {
            "verifier_vendor": verifier.get("vendor"),
            "verifier_model_id": (derived_verifier_model_id
                                  if attestation_ok_flag else verifier.get("model")),
            "absorb_vendor": self.absorb_vendor,
            "absorb_model_id": self.absorb_model_id,
            "substrate_source": ("invocation-metadata" if attestation_ok_flag
                                 else "attestation-gated"),
        }

        if attestation_ok_flag:
            attest_record_rel = self._write_attest_record(
                pid, attestation, verifier, evidence_path)

        if not attestation_ok_flag:
            substrate_info = dict(block, wellformed=None,
                                  derived_from_invocation=False,
                                  gate_ok=False, gate_kind=self.gate_kind,
                                  attestation={"channel": None, "reason": attest_reason})
            return {"verdict": "substrate-gated",
                    "reason": "F17 attestation gate: %s" % attest_reason,
                    "uncertainty": bridge_verdict.get("uncertainty", "unknown"),
                    "verifier": verifier, "substrate": substrate_info,
                    "evidence_file": evidence_rel,
                    "bridge_verdict": bridge_verdict,
                    "gated_inner_verdict": bridge_verdict.get("verdict")}

        attestation_entry = {
            "channel": attestation.get("channel"),
            "artifact": attest_record_rel,
            "artifact_sha256": (_sha256(open(os.path.join(
                self.repo, attest_record_rel.replace("/", os.sep)),
                encoding="utf-8").read()) if attest_record_rel else None),
            "absorb_channel": self._absorb_channel,
        }

        # F17 write-path substrate-channel gate (2026-07-05 cross-vendor
        # conformance finding): the argv/runtime/wrapper cross-checks above
        # only prove the MODEL strings agree with each other -- they never
        # validate the attestation's CHANNEL. check_substrate.attestation_ok
        # is the deterministic gate for that (channel must be
        # "subprocess-runtime", or absorb_channel must be "dispatch-record",
        # with artifact+sha present). Fail closed to the same
        # substrate-gated path used above: NO substrate_source
        # "invocation-metadata" may be emitted without this passing.
        channel_ok, channel_reason = substrate.attestation_ok(attestation_entry)
        if not channel_ok:
            block["substrate_source"] = "attestation-gated"
            substrate_info = dict(block, wellformed=None,
                                  derived_from_invocation=False,
                                  gate_ok=False, gate_kind=self.gate_kind,
                                  attestation=attestation_entry)
            return {"verdict": "substrate-gated",
                    "reason": "F17 attestation channel gate: %s" % channel_reason,
                    "uncertainty": bridge_verdict.get("uncertainty", "unknown"),
                    "verifier": verifier, "substrate": substrate_info,
                    "evidence_file": evidence_rel,
                    "bridge_verdict": bridge_verdict,
                    "gated_inner_verdict": bridge_verdict.get("verdict")}

        wellformed, wf_reason = substrate.verified_block_wellformed(block)
        derived = substrate.substrate_derived_from_invocation(block)
        gate_ok = False
        if wellformed and derived:
            gate_ok = substrate.substrate_gate_ok(
                block["absorb_vendor"], block["absorb_model_id"],
                block["verifier_vendor"], block["verifier_model_id"],
                self.gate_kind)
        substrate_info = dict(block, wellformed=wellformed,
                              derived_from_invocation=derived,
                              gate_ok=gate_ok, gate_kind=self.gate_kind)
        substrate_info["attestation"] = attestation_entry

        # BANNER CROSS-CHECK (second conveyance channel, not a harness
        # attestation -- see class docstring). Parse the FIRST
        # "verifier=vendor/model" occurrence out of stderr and compare it
        # against the stdout verdict's own verifier block.
        banner_match = _BANNER_VERIFIER_RE.search(stderr or "")
        if banner_match is None:
            substrate_info["banner_crosscheck"] = "absent"
        else:
            banner_vendor, banner_model = banner_match.group(1), banner_match.group(2)
            if (banner_vendor == block["verifier_vendor"]
                    and banner_model == block["verifier_model_id"]):
                substrate_info["banner_crosscheck"] = "match"
            else:
                substrate_info["banner_crosscheck"] = "mismatch"
                gate_reason = (
                    "stderr banner cross-check mismatch: banner verifier=%s/%s "
                    "!= stdout verdict verifier=%s/%s" % (
                        banner_vendor, banner_model,
                        block["verifier_vendor"], block["verifier_model_id"]))
                return {"verdict": "substrate-gated", "reason": gate_reason,
                        "uncertainty": bridge_verdict.get("uncertainty",
                                                          "unknown"),
                        "verifier": verifier, "substrate": substrate_info,
                        "evidence_file": evidence_rel,
                        "bridge_verdict": bridge_verdict,
                        "gated_inner_verdict": bridge_verdict.get("verdict")}

        if not (wellformed and derived and gate_ok):
            if not wellformed:
                gate_reason = "substrate not well-formed: %s" % wf_reason
            elif not derived:
                gate_reason = ("substrate not derived from invocation "
                              "metadata")
            else:
                gate_reason = ("substrate gate %r failed (absorb=%s/%s "
                               "verify=%s/%s)" % (
                                   self.gate_kind, block["absorb_vendor"],
                                   block["absorb_model_id"],
                                   block["verifier_vendor"],
                                   block["verifier_model_id"]))
            return {"verdict": "substrate-gated", "reason": gate_reason,
                    "uncertainty": bridge_verdict.get("uncertainty", "unknown"),
                    "verifier": verifier, "substrate": substrate_info,
                    "evidence_file": evidence_rel,
                    "bridge_verdict": bridge_verdict,
                    "gated_inner_verdict": bridge_verdict.get("verdict")}

        return {"verdict": bridge_verdict.get("verdict"),
                "reason": bridge_verdict.get("reason"),
                "uncertainty": bridge_verdict.get("uncertainty"),
                "verifier": verifier, "substrate": substrate_info,
                "evidence_file": evidence_rel}


# --------------------------------------------------------------- packet emission
def _slugify(rel):
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", rel).strip("-").lower()
    return slug or "item"


ANSWER_CONTRACT = """\
Write your answer as JSON to the ANSWER FILE path given above, matching this
schema exactly:

{
  "new_text": "<full replacement view text>" or null,
  "manifest": [{"event": "<event rel path>", "section": "<section name>"}],
  "corpus_support": [
    {"artifact": "<rel path>", "artifact_sha256": "<hex sha256, REQUIRED>",
     "support_lines": ["<EXACT line of the artifact, whitespace included>"]}
  ],
  "noops": [{"event": "<event rel path>", "justification_note": "<why>"}]
}

Rules (mechanically enforced, not advisory):
  - `manifest` must match the REAL diff between the current view and your
    `new_text`, section-for-section, in BOTH directions: every section you
    claim changed must actually differ, and every section that differs must
    be claimed. No heading may be deleted; the view may not shrink more
    than 30% by length.
  - Every `corpus_support` entry MUST carry `artifact_sha256` (the sha256
    hex digest of the artifact's current full text) -- entries without it
    are refused. Every `support_lines` entry must be an EXACT line of the
    cited artifact (whitespace included, not a paraphrase or substring).
  - If ALL events are no-ops for this view, set "new_text" to null and
    "manifest" to [] -- do not emit a manifest for a no-op.
  - If ONLY SOME events are no-ops, list those in "noops" with a
    "justification_note" and still emit "new_text"/"manifest" for the rest.
  - Any shipped-state/live/as-built prose you add or change REQUIRES at
    least one corpus_support entry.
  - If your `new_text` carries a `last_updated:` frontmatter value, it must
    be max(existing view's last_updated, event date) -- never earlier than
    the existing view's value.
"""


def emit_packets(repo, plan, staging, routing_rules_text, event_class=None):
    """Compile a plan into ABSORB work packets + a dispatch manifest. Returns
    the manifest dict (also written to <staging>/dispatch-manifest.json)."""
    packets_dir = os.path.join(staging, "packets")
    answers_dir = os.path.join(staging, "answers")
    os.makedirs(packets_dir, exist_ok=True)
    os.makedirs(answers_dir, exist_ok=True)

    manifest_entries = []
    for idx, item in enumerate(plan["items"], start=1):
        view_rel = item["view"]
        slug = _slugify(view_rel)
        nn = "%02d" % idx
        packet_name = "%s-%s.md" % (nn, slug)
        answer_name = "%s-%s.json" % (nn, slug)
        packet_rel = "packets/%s" % packet_name
        answer_rel = "answers/%s" % answer_name

        vp = os.path.join(repo, view_rel.replace("/", os.sep))
        view_text = (open(vp, encoding="utf-8").read()
                     if os.path.isfile(vp) else None)

        classes = item.get("event_class") or (event_class or {}).get(
            view_rel, {})
        if not classes and event_class:
            classes = event_class

        lock_class_any = False
        event_class_lines = []
        event_bodies = []
        for erel in item["events"]:
            entry = classes.get(erel) if isinstance(classes, dict) else None
            entry = entry or {}
            cls = entry.get("class", "unknown")
            origin = entry.get("origin", "judgment")
            lock = compile_v2.is_lock_class(entry)
            if lock:
                lock_class_any = True
            line = "- %s: class=%s origin=%s" % (erel, cls, origin)
            if lock:
                line += " -- LOCK-CLASS: orchestrator judgment review required"
            event_class_lines.append(line)

            ep = os.path.join(repo, erel.replace("/", os.sep))
            ebody = (open(ep, encoding="utf-8").read()
                     if os.path.isfile(ep) else "(event file missing)")
            event_bodies.append((erel, ebody))

        lines = []
        lines.append("# ABSORB WORK PACKET %s — %s" % (nn, view_rel))
        lines.append("")
        lines.append("Write your answer to: %s" % answer_rel)
        lines.append("")
        lines.append("This is " + ABSORB_SLIM_CONTRACT + ".")
        lines.append("")
        lines.append("## ANSWER CONTRACT")
        lines.append(ANSWER_CONTRACT)
        lines.append("## INVARIANT 4 (verbatim)")
        lines.append(INVARIANT_4)
        lines.append("")
        lines.append("## UNTRUSTED-DATA RULE (spec sec.9, verbatim)")
        lines.append(S9_UNTRUSTED_RULE)
        lines.append("")
        lines.append("## ROUTING RULES")
        lines.append(routing_rules_text)
        lines.append("")
        lines.append("## EVENT CLASSES")
        lines.extend(event_class_lines)
        lines.append("")
        lines.append("## CURRENT VIEW: %s" % view_rel)
        lines.append(view_text if view_text is not None
                     else "(view does not exist yet)")
        for erel, ebody in event_bodies:
            lines.append("")
            lines.append("## DELTA EVENT: %s" % erel)
            lines.append(ebody)

        packet_text = "\n".join(lines)
        if not packet_text.endswith("\n"):
            packet_text += "\n"
        with open(os.path.join(packets_dir, packet_name), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(packet_text)

        manifest_entries.append({
            "packet": packet_rel, "answer": answer_rel, "view": view_rel,
            "events": list(item["events"]), "lock_class": lock_class_any,
            "model": None, "vendor": None,
        })

    manifest = {"packets": manifest_entries,
               "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with open(os.path.join(staging, "dispatch-manifest.json"), "w",
              encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
    return manifest


class FileHandoffAbsorbBackend:
    """ABSORB leg: resolves an already-produced answer JSON via the
    dispatch manifest emit_packets() wrote, and hands it back verbatim.
    compile-v2.validate_absorb_output() does all real validation."""

    def __init__(self, staging):
        self.staging = staging

    def absorb(self, view_rel, view_text, events):
        manifest_path = os.path.join(self.staging, "dispatch-manifest.json")
        if not os.path.isfile(manifest_path):
            raise ValidationError(
                "no dispatch-manifest.json in staging dir %s" % self.staging)
        try:
            manifest = json.load(open(manifest_path, encoding="utf-8"))
        except (ValueError, TypeError) as e:
            raise ValidationError("dispatch-manifest.json unparseable: %s"
                                  % e)

        # F17 absorb-side attestation (2026-07-05 design): REFUSE any
        # manifest lacking a well-formed dispatch stamp, or whose recomputed
        # sha256 mismatches -- fail-closed, a new refusal (not a changed
        # acceptance of the prior unstamped-manifest path).
        stamp_ok, stamp_reason = _verify_dispatch_stamp(manifest)
        if not stamp_ok:
            raise ValidationError(
                "dispatch-manifest.json refused (F17 dispatch-record "
                "attestation): %s" % stamp_reason)

        entry = None
        for p in manifest.get("packets") or []:
            if p.get("view") == view_rel:
                entry = p
                break
        if entry is None:
            raise ValidationError(
                "no dispatch-manifest entry for view %s" % view_rel)

        answer_path = os.path.join(self.staging,
                                   entry["answer"].replace("/", os.sep))
        if not os.path.isfile(answer_path):
            raise ValidationError("answer file missing: %s" % answer_path)
        try:
            answer = json.load(open(answer_path, encoding="utf-8"))
        except (ValueError, TypeError) as e:
            raise ValidationError("answer file %s unparseable: %s"
                                  % (answer_path, e))

        required = ("new_text", "manifest", "corpus_support", "noops")
        missing = [k for k in required if k not in answer]
        if missing:
            raise ValidationError(
                "answer file %s missing required key(s): %s"
                % (answer_path, missing))
        return answer


# --------------------------------------------------------------- self-test
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

    # v3.0-27 coupling fix: try the SHIPPED engine copy first -- docs/engine/
    # (the instantiated-project sibling of deploy/, per init.sh/init.ps1's
    # wire_copy of extracted/engine -> docs/engine) or, in this dev checkout
    # before instantiation, capabilities/knowledge-os/extracted/engine/ (the
    # sibling of extracted/deploy/ that docs/engine/ is wired FROM) -- and
    # fall back to the harness-v3.0/ dev path only when neither shipped copy
    # is present, so the self-test works unmodified in a trimmed release
    # (no harness-v3.0/) as well as in this dev tree.
    _shipped_spec_candidates = (
        os.path.join(_HERE, os.pardir, "docs", "engine",
                     "memory-engine-v3-spec.md"),
        os.path.join(_HERE, os.pardir, "engine", "memory-engine-v3-spec.md"),
    )
    spec_path = next((p for p in _shipped_spec_candidates
                      if os.path.isfile(p)), None)
    if spec_path is None:
        repo_root = _HERE
        while repo_root and not os.path.isdir(
                os.path.join(repo_root, "harness-v3.0")):
            parent = os.path.dirname(repo_root)
            if parent == repo_root:
                repo_root = None
                break
            repo_root = parent

        spec_path = os.path.join(repo_root or _HERE, "harness-v3.0", "specs",
                                 "memory-engine-v3-spec.md")
    spec_text = open(spec_path, encoding="utf-8").read()
    case("INVARIANT_4 appears verbatim in the spec file",
         INVARIANT_4 in spec_text)
    case("S9_UNTRUSTED_RULE (full bullet) appears verbatim in the spec file",
         S9_UNTRUSTED_RULE in spec_text)

    # claim extraction
    good_packet = ("# NOOP VERIFY PACKET seq1-0\n\nCLAIM: event raw/e1.md "
                  "carries zero load-bearing claims absent from view "
                  "wiki/a.md.\n\n## FULL EVENT BODY\nx\n## FULL VIEW BODY\ny\n")
    case("claim extraction finds the CLAIM line",
         BridgeVerifyBackend._extract_claim(good_packet)
         == "event raw/e1.md carries zero load-bearing claims absent from "
            "view wiki/a.md.")
    no_claim_packet = "# NOOP VERIFY PACKET seq1-0\n\nno claim line here\n"
    case("claim extraction returns None with no CLAIM line",
         BridgeVerifyBackend._extract_claim(no_claim_packet) is None)
    case("packet id extraction: pre-amendment per-candidate form",
         BridgeVerifyBackend._extract_packet_id(good_packet) == "seq1-0")
    case("packet id extraction: union per-event form (verify-unit "
         "amendment 2026-07-05)",
         BridgeVerifyBackend._extract_packet_id(
             "# NOOP VERIFY PACKET seq3-e0\n\nCLAIM: x\n") == "seq3-e0")
    # absorption-verify amendment (2026-07-05): -v discriminator, regression
    # both directions -- -e ids unchanged, -v ids now resolve.
    case("packet id extraction: -e (no-op union) form UNCHANGED after the "
         "absorption-verify amendment",
         BridgeVerifyBackend._extract_packet_id(
             "# NOOP VERIFY PACKET seq5-e2\n\nCLAIM: x\n") == "seq5-e2")
    case("packet id extraction: -v (absorption-verify) form resolves to "
         "the absorb- artifact stem",
         BridgeVerifyBackend._extract_packet_id(
             "# ABSORPTION VERIFY PACKET seq5-v1\n\nCLAIM: view wiki/a.md "
             "at post-absorb state faithfully absorbs events raw/e1.md: "
             "every manifest claim is supported.\n") == "seq5-v1")
    case("packet id extraction: unrecognized header form falls back to a "
         "content hash (fail-safe, not fail-closed, for either form)",
         BridgeVerifyBackend._extract_packet_id("no header here\n")
         == _sha256("no header here\n")[:12])

    tmp = tempfile.mkdtemp(prefix="cb-")
    try:
        staging = os.path.join(tmp, "verify-staging")
        _stamp_counter = [0]

        def make_backend(runner, gate_kind="routine", vendor="anthropic",
                         model="claude-sonnet-5"):
            """Default self-test backend constructor: derives absorb identity
            from a REAL stamped dispatch manifest (stamp_dispatch), never from
            bare literals -- the literal-constructor path no longer yields the
            attested "invocation-metadata" happy path (F17 attestation-channel
            gate, 2026-07-05), so every fixture that exercises a CONFIRMED/
            attested outcome must go through a real stamp. A fresh manifest
            file is written per call (counter-suffixed) so parallel backends
            in the same tmp dir don't clobber each other's stamp."""
            _stamp_counter[0] += 1
            manifest_path = os.path.join(
                tmp, "self-test-manifest-%d.json" % _stamp_counter[0])
            with open(manifest_path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"packets": []}, fh)
            stamp_dispatch(manifest_path, model=model, vendor=vendor,
                           identity_source="attestation:self-test-fixture")
            return BridgeVerifyBackend(tmp, gate_kind=gate_kind,
                                       staging=staging, runner=runner,
                                       dispatch_manifest_path=manifest_path)

        def make_backend_literal(runner, gate_kind="routine",
                                 vendor="anthropic", model="claude-sonnet-5"):
            """BACK-COMPAT FIXTURE-ONLY constructor: bare absorb_vendor/
            absorb_model_id literals, no dispatch stamp -- used ONLY by
            fixtures that prove this path is now RETIRED from the attested
            happy path (it must yield substrate-gated, never
            invocation-metadata, since absorb_channel stays unset)."""
            return BridgeVerifyBackend(tmp, vendor, model, gate_kind=gate_kind,
                                       staging=staging, runner=runner)

        # missing CLAIM line -> bridge-error, never confirm, before any runner call
        b_noclaim = make_backend(lambda args: (0, "{}", ""))
        v = b_noclaim.verify(no_claim_packet)
        case("missing CLAIM -> bridge-error verdict",
             v["verdict"] == "bridge-error")
        case("missing CLAIM verdict never starts with confirm",
             not str(v["verdict"]).lower().startswith("confirm"))

        def _good_attestation(model):
            # F17 (2026-07-05 design): a GOOD attestation is internally
            # self-consistent -- argv_model == runtime_model == the
            # wrapper's own stdout verifier.model claim.
            return {"channel": "subprocess-runtime", "argv_model": model,
                    "runtime_model": model, "runtime_model_line": "model: " + model,
                    "exit_code": 0, "token_usage": None, "ts": "2026-07-05T00:00:00Z"}

        def confirmed_runner(vendor="openai", model="gpt-5.5",
                             attestation="auto"):
            def runner(args):
                payload = {
                    "verdict": "confirmed", "reason": "ok",
                    "uncertainty": "confident",
                    "verifier": {"vendor": vendor, "model": model}}
                att = _good_attestation(model) if attestation == "auto" else attestation
                if att is not None:
                    payload["attestation"] = att
                return 0, json.dumps(payload), ""
            return runner

        b1 = make_backend(confirmed_runner())
        v1 = b1.verify(good_packet)
        case("routine gate, different model -> confirmed",
             v1["verdict"] == "confirmed")
        case("routine gate, different model -> gate_ok true",
             v1["substrate"]["gate_ok"] is True)
        case("evidence file written to staging",
             os.path.isfile(os.path.join(staging, v1["evidence_file"].split(
                 "/")[-1])) or os.path.isfile(os.path.join(
                     tmp, v1["evidence_file"])))
        # v3.0-23: a normal (non-gated) leg must NOT carry the gated-inner-
        # verdict accounting field at all -- it is only ever added on the
        # substrate-gated downgrade path.
        case("gated_inner_verdict absent on a normal (non-gated) leg",
             "gated_inner_verdict" not in v1)

        # SAME model as absorb -> substrate-gated (routine gate fails)
        b2 = make_backend(confirmed_runner(vendor="anthropic",
                                           model="claude-sonnet-5"))
        v2 = b2.verify(good_packet)
        case("routine gate, SAME model -> substrate-gated",
             v2["verdict"] == "substrate-gated")
        case("substrate-gated preserves original bridge_verdict",
             v2.get("bridge_verdict", {}).get("verdict") == "confirmed")
        # v3.0-23 fix: the nested bridge verdict's CONTENT must re-surface as
        # a distinct top-level accounting field, never as the effective
        # verdict (which stays "substrate-gated" above, unchanged).
        case("v3.0-23: gated_inner_verdict mirrors bridge_verdict.verdict",
             v2.get("gated_inner_verdict") == "confirmed")

        # v3.0-23 live scenario (receipts/verify/absorb-seq19-v0.json): a
        # SAME-model gate failure wrapping a CONFIDENT "rejected" nested
        # verdict -- the outer downgrade must still surface "rejected" via
        # gated_inner_verdict rather than letting it vanish into
        # "substrate-gated" for every tally that reads only "verdict".
        def rejected_runner(vendor="anthropic", model="claude-sonnet-5"):
            def runner(args):
                payload = {
                    "verdict": "rejected",
                    "reason": "contradiction found against corpus support",
                    "uncertainty": "confident",
                    "verifier": {"vendor": vendor, "model": model},
                    "attestation": _good_attestation(model)}
                return 0, json.dumps(payload), ""
            return runner
        b_rej = make_backend(rejected_runner())
        v_rej = b_rej.verify(good_packet)
        case("v3.0-23: SAME-model gate + nested rejected -> outer stays "
             "substrate-gated (fail-closed direction unchanged)",
             v_rej["verdict"] == "substrate-gated")
        case("v3.0-23: gated_inner_verdict surfaces the nested confident "
             "rejected that would otherwise vanish from accounting",
             v_rej.get("gated_inner_verdict") == "rejected")

        # migration gate: same vendor -> gated; different vendor -> passes
        b3 = make_backend(confirmed_runner(vendor="anthropic",
                                           model="claude-haiku-4-5"),
                          gate_kind="migration")
        v3 = b3.verify(good_packet)
        case("migration gate, SAME vendor (diff model) -> substrate-gated",
             v3["verdict"] == "substrate-gated")

        b4 = make_backend(confirmed_runner(vendor="openai", model="gpt-5.5"),
                          gate_kind="migration")
        v4 = b4.verify(good_packet)
        case("migration gate, different vendor -> passes (confirmed)",
             v4["verdict"] == "confirmed")

        # unknown gate_kind -> fail-closed
        b5 = make_backend(confirmed_runner(vendor="openai", model="gpt-5.5"),
                          gate_kind="mystery")
        v5 = b5.verify(good_packet)
        case("unknown gate_kind -> substrate-gated (fail-closed)",
             v5["verdict"] == "substrate-gated")

        # runner failure modes
        b6 = make_backend(lambda args: (1, "", "boom"))
        v6 = b6.verify(good_packet)
        case("runner nonzero exit -> bridge-error", v6["verdict"] == "bridge-error")

        b7 = make_backend(lambda args: (0, "", ""))
        v7 = b7.verify(good_packet)
        case("runner empty stdout -> bridge-error", v7["verdict"] == "bridge-error")

        b8 = make_backend(lambda args: (0, "not json {{{", ""))
        v8 = b8.verify(good_packet)
        case("runner garbage stdout -> bridge-error",
             v8["verdict"] == "bridge-error")

        # v3.0-68: the journaled stderr excerpt must be long enough to carry the
        # server's opening banner (which names the RESOLVED codex binary). At
        # the old 200-char clip the banner was always cut away, so seq 103's
        # evidence never recorded which binary had been resolved.
        banner = ("[codex-verify] codex-verify MCP server ready -- verifier="
                  "C:\\Users\\x\\AppData\\Roaming\\npm\\node_modules\\@openai\\"
                  "codex\\node_modules\\@openai\\codex-win32-x64\\vendor\\"
                  "x86_64-pc-windows-msvc\\bin\\codex.exe exec\n")
        long_stderr = banner + ("noise line\n" * 120)
        b8b = make_backend(lambda args: (1, "", long_stderr))
        v8b = b8b.verify(good_packet)
        case("bridge-error reason keeps a %d-char stderr excerpt, so the "
             "resolved-binary banner survives into the artifact"
             % BRIDGE_STDERR_EXCERPT_CHARS,
             v8b["verdict"] == "bridge-error"
             and "codex.exe exec" in v8b["reason"]
             and len(v8b["reason"]) > 1000)

        # verdict missing "verifier" -> wellformed fails -> substrate-gated
        def no_verifier_runner(args):
            return 0, json.dumps({"verdict": "confirmed", "reason": "ok",
                                  "uncertainty": "confident"}), ""
        b9 = make_backend(no_verifier_runner)
        v9 = b9.verify(good_packet)
        case("verdict missing verifier -> substrate-gated",
             v9["verdict"] == "substrate-gated")

        # combined vendor/model string smuggle -> substrate-gated
        def combined_runner(args):
            return 0, json.dumps({
                "verdict": "confirmed", "reason": "ok",
                "uncertainty": "confident",
                "verifier": {"vendor": "openai/gpt-5.5",
                            "model": "gpt-5.5"}}), ""
        b10 = make_backend(combined_runner)
        v10 = b10.verify(good_packet)
        case("combined vendor/model string -> substrate-gated",
             v10["verdict"] == "substrate-gated")

        # ------------------------------------------------- F17 attestation gate
        # (a) missing attestation entirely -> substrate-gated, never confirms
        b_noattest = make_backend(confirmed_runner(attestation=None))
        v_noattest = b_noattest.verify(good_packet)
        case("F17: no attestation object -> substrate-gated",
             v_noattest["verdict"] == "substrate-gated")
        case("F17: no attestation -> substrate_source NOT invocation-metadata",
             v_noattest["substrate"]["substrate_source"] != "invocation-metadata")

        # (b) argv/runtime model disagreement -> substrate-gated
        b_disagree_rt = make_backend(confirmed_runner(
            attestation={"channel": "subprocess-runtime", "argv_model": "gpt-5.5",
                        "runtime_model": "gpt-4.1", "runtime_model_line": "model: gpt-4.1",
                        "exit_code": 0, "token_usage": None, "ts": "2026-07-05T00:00:00Z"}))
        v_disagree_rt = b_disagree_rt.verify(good_packet)
        case("F17: argv_model != runtime_model -> substrate-gated",
             v_disagree_rt["verdict"] == "substrate-gated")
        case("F17: argv/runtime disagreement reason names both models",
             "gpt-5.5" in v_disagree_rt["reason"] and "gpt-4.1" in v_disagree_rt["reason"])

        # (c) derived model disagrees with the wrapper's own stdout verifier.model
        #     claim (old declared channel becomes a cross-check) -> substrate-gated
        def wrapper_disagree_runner(args):
            payload = {
                "verdict": "confirmed", "reason": "ok", "uncertainty": "confident",
                "verifier": {"vendor": "openai", "model": "gpt-5.5"},
                "attestation": {"channel": "subprocess-runtime",
                               "argv_model": "gpt-4.1", "runtime_model": "gpt-4.1",
                               "runtime_model_line": "model: gpt-4.1", "exit_code": 0,
                               "token_usage": None, "ts": "2026-07-05T00:00:00Z"}}
            return 0, json.dumps(payload), ""
        b_wrapdis = make_backend(wrapper_disagree_runner)
        v_wrapdis = b_wrapdis.verify(good_packet)
        case("F17: derived model != wrapper verifier.model claim -> "
            "substrate-gated",
             v_wrapdis["verdict"] == "substrate-gated")

        # (d) good attestation happy path: substrate_source set, attest.json
        #     written and pinned (artifact + sha present)
        b_goodattest = make_backend(confirmed_runner(vendor="openai", model="gpt-5.5"))
        v_goodattest = b_goodattest.verify(good_packet)
        case("F17: good attestation -> confirmed (not gated)",
             v_goodattest["verdict"] == "confirmed")
        case("F17: good attestation -> substrate_source == invocation-metadata",
             v_goodattest["substrate"]["substrate_source"] == "invocation-metadata")
        case("F17: good attestation -> substrate.attestation.channel == "
            "subprocess-runtime",
             v_goodattest["substrate"]["attestation"]["channel"] == "subprocess-runtime")
        attest_artifact_rel = v_goodattest["substrate"]["attestation"]["artifact"]
        case("F17: attest record artifact path recorded",
             bool(attest_artifact_rel))
        attest_artifact_abs = os.path.join(tmp, attest_artifact_rel.replace("/", os.sep))
        case("F17: attest.json actually written to disk",
             os.path.isfile(attest_artifact_abs))
        case("F17: substrate.attestation.artifact_sha256 matches the written file",
             v_goodattest["substrate"]["attestation"]["artifact_sha256"]
             == _sha256(open(attest_artifact_abs, encoding="utf-8").read()))
        written_attest = json.load(open(attest_artifact_abs, encoding="utf-8"))
        case("F17: attest.json carries packet_sha256",
             bool(written_attest.get("packet_sha256")))
        case("F17: attest.json carries the raw wrapper verifier claim",
             written_attest.get("wrapper_verifier_claim", {}).get("model") == "gpt-5.5")

        # (d.1) literal-constructor RETIREMENT proof: the back-compat bare-
        # literal absorb identity path (no dispatch stamp) must NEVER emit
        # substrate_source "invocation-metadata" even on an otherwise-perfect
        # attested verifier leg -- absorb_channel stays unset (None), so
        # attestation_ok() (AND-semantics) fails, and the outcome is
        # substrate-gated, proving the OR-shaped literal happy path is
        # retired, not merely untested.
        b_literal_happy = make_backend_literal(
            confirmed_runner(vendor="openai", model="gpt-5.5"))
        v_literal_happy = b_literal_happy.verify(good_packet)
        case("literal-constructor happy-attempt -> substrate-gated "
            "(attested happy path retired for bare literals)",
             v_literal_happy["verdict"] == "substrate-gated")
        case("literal-constructor happy-attempt -> substrate_source NOT "
            "invocation-metadata",
             v_literal_happy["substrate"]["substrate_source"]
             != "invocation-metadata")
        case("literal-constructor happy-attempt -> absorb_channel absent",
             v_literal_happy["substrate"]["attestation"]["absorb_channel"]
             is None)

        # ------------------------------------------------- F17 channel gate
        # (cross-vendor conformance finding, 2026-07-05): argv/runtime/wrapper
        # model strings can all agree while the attestation CHANNEL itself is
        # spoofed or absent -- these fixtures prove attestation_ok() is
        # actually consulted on the write path and fails closed.

        # (e) unknown/spoofed verifier channel, models all agree -> gated
        def spoofed_channel_runner(args):
            model = "gpt-5.5"
            payload = {
                "verdict": "confirmed", "reason": "ok", "uncertainty": "confident",
                "verifier": {"vendor": "openai", "model": model},
                "attestation": {"channel": "spoofed-whatever", "argv_model": model,
                               "runtime_model": model, "runtime_model_line": "model: " + model,
                               "exit_code": 0, "token_usage": None,
                               "ts": "2026-07-05T00:00:00Z"}}
            return 0, json.dumps(payload), ""
        b_spoofchan = make_backend(spoofed_channel_runner)
        v_spoofchan = b_spoofchan.verify(good_packet)
        case("F17: unknown verifier channel (models agree) -> substrate-gated",
             v_spoofchan["verdict"] == "substrate-gated")
        case("F17: unknown verifier channel -> substrate_source NOT "
            "invocation-metadata",
             v_spoofchan["substrate"]["substrate_source"] != "invocation-metadata")

        # (f) unknown absorb_channel -- back-compat fixture path (literal
        #     absorb_vendor/model, no dispatch stamp) leaves _absorb_channel
        #     None, which is itself an unknown absorb_channel; confirm the
        #     gate catches it even with a good subprocess-runtime attestation
        #     if the channel key itself is missing/blank.
        def blank_channel_runner(args):
            model = "gpt-5.5"
            payload = {
                "verdict": "confirmed", "reason": "ok", "uncertainty": "confident",
                "verifier": {"vendor": "openai", "model": model},
                "attestation": {"channel": "", "argv_model": model,
                               "runtime_model": model, "runtime_model_line": "model: " + model,
                               "exit_code": 0, "token_usage": None,
                               "ts": "2026-07-05T00:00:00Z"}}
            return 0, json.dumps(payload), ""
        b_blankchan = make_backend(blank_channel_runner)
        v_blankchan = b_blankchan.verify(good_packet)
        case("F17: unknown absorb_channel / blank verifier channel -> "
            "substrate-gated",
             v_blankchan["verdict"] == "substrate-gated")

        # (g) missing artifact_sha256 -- forced by monkeypatching
        #     _write_attest_record to omit the sha input, simulated instead
        #     by writing an attest record path that does not resolve (so
        #     attest_record_rel comes back falsy). Simplest deterministic
        #     way to force this without patching private methods: make the
        #     evidence_path unreadable is fragile; instead directly exercise
        #     attestation_ok() -- the unit under test -- with an entry that
        #     has a good channel but a missing sha, proving the gate rejects
        #     it, then confirm compile-backends' own good-path entry always
        #     supplies one (already proven above).
        case("F17: attestation_ok rejects good channel + missing sha "
            "(unit-level)",
             substrate.attestation_ok({"channel": "subprocess-runtime",
                                       "artifact": "receipts/verify/attest/x.json",
                                       "artifact_sha256": None})[0] is False)
        case("F17: attestation_ok rejects good channel + missing artifact "
            "(unit-level)",
             substrate.attestation_ok({"channel": "subprocess-runtime",
                                       "artifact": None,
                                       "artifact_sha256": "deadbeef"})[0] is False)

        # ------------------------------------------------- banner cross-check
        # (a) stderr banner MATCHES the stdout verdict's verifier -> confirmed
        #     passes, substrate.banner_crosscheck == "match"
        def banner_matching_runner(args):
            stderr_text = ("[cross-check] verifier=openai/gpt-5.5 (asker MUST "
                          "be a non-OpenAI substrate for this to count as "
                          "cross-vendor)\n[cross-check] verdict=confirmed "
                          "uncertainty=confident verifier=openai/gpt-5.5\n")
            return 0, json.dumps({
                "verdict": "confirmed", "reason": "ok",
                "uncertainty": "confident",
                "verifier": {"vendor": "openai", "model": "gpt-5.5"},
                "attestation": _good_attestation("gpt-5.5")}), stderr_text
        b11 = make_backend(banner_matching_runner)
        v11 = b11.verify(good_packet)
        case("banner matches verifier -> confirmed passes",
             v11["verdict"] == "confirmed")
        case("banner matches verifier -> banner_crosscheck == match",
             v11["substrate"]["banner_crosscheck"] == "match")

        # (b) stderr banner MISMATCHES the stdout verdict's verifier ->
        #     substrate-gated, even though the gate itself would have passed
        def banner_mismatching_runner(args):
            stderr_text = ("[cross-check] verifier=openai/gpt-5.5 (asker MUST "
                          "be a non-OpenAI substrate for this to count as "
                          "cross-vendor)\n")
            return 0, json.dumps({
                "verdict": "confirmed", "reason": "ok",
                "uncertainty": "confident",
                "verifier": {"vendor": "anthropic",
                            "model": "claude-haiku-4-5"},
                "attestation": _good_attestation("claude-haiku-4-5")}), stderr_text
        b12 = make_backend(banner_mismatching_runner)
        v12 = b12.verify(good_packet)
        case("banner mismatches verifier -> substrate-gated",
             v12["verdict"] == "substrate-gated")
        case("banner mismatch -> banner_crosscheck == mismatch",
             v12["substrate"]["banner_crosscheck"] == "mismatch")
        case("banner mismatch reason names both values",
             "openai/gpt-5.5" in v12["reason"]
             and "anthropic/claude-haiku-4-5" in v12["reason"])
        case("banner mismatch preserves original bridge_verdict",
             v12.get("bridge_verdict", {}).get("verdict") == "confirmed")

        # (c) no banner at all -> verdict unaffected, banner_crosscheck == absent
        b13 = make_backend(confirmed_runner(vendor="openai", model="gpt-5.5"))
        v13 = b13.verify(good_packet)
        case("no banner -> verdict unaffected (still confirmed)",
             v13["verdict"] == "confirmed")
        case("no banner -> banner_crosscheck == absent",
             v13["substrate"]["banner_crosscheck"] == "absent")

        # ------------------------------------------------------- emit_packets
        repo = os.path.join(tmp, "repo")
        os.makedirs(os.path.join(repo, "wiki"))
        os.makedirs(os.path.join(repo, "raw"))
        with open(os.path.join(repo, "wiki", "a.md"), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write("# A\n\n## Intro\nhello\n")
        with open(os.path.join(repo, "raw", "e1.md"), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write("fact one\n")
        with open(os.path.join(repo, "raw", "e2.md"), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write("fact two, judgment origin\n")

        plan = {"items": [
            {"view": "wiki/a.md", "events": ["raw/e1.md", "raw/e2.md"],
             "event_class": {
                 "raw/e1.md": {"class": "t3", "origin": "explicit"},
                 "raw/e2.md": {"class": "t3", "origin": "judgment"}}},
        ]}
        pkg_staging = os.path.join(tmp, "pkg-staging")
        os.makedirs(pkg_staging)
        routing_text = "Route T1/correction/lock events to top-tier models."
        manifest = emit_packets(repo, plan, pkg_staging, routing_text)
        case("emit_packets returns 1 packet entry",
             len(manifest["packets"]) == 1)
        case("dispatch-manifest.json written",
             os.path.isfile(os.path.join(pkg_staging,
                                         "dispatch-manifest.json")))
        packet_path = os.path.join(pkg_staging,
                                   manifest["packets"][0]["packet"].replace(
                                       "/", os.sep))
        packet_text = open(packet_path, encoding="utf-8").read()
        case("packet contains verbatim INVARIANT_4", INVARIANT_4 in packet_text)
        case("packet contains verbatim S9 rule", S9_UNTRUSTED_RULE in packet_text)
        case("packet contains routing rules", routing_text in packet_text)
        case("packet contains view text", "hello" in packet_text)
        case("packet contains both event bodies",
             "fact one" in packet_text and "fact two" in packet_text)
        case("packet answer contract mentions artifact_sha256",
             "artifact_sha256" in packet_text)
        case("lock-class marking correct for judgment-origin event",
             any("raw/e2.md" in ln and "LOCK-CLASS" in ln
                 for ln in packet_text.splitlines()))
        case("no lock-class marking for explicit-origin event",
             not any("raw/e1.md" in ln and "LOCK-CLASS" in ln
                     for ln in packet_text.splitlines()))
        case("manifest entry lock_class true (one lock-class event present)",
             manifest["packets"][0]["lock_class"] is True)

        # --------------------------------------------------- stamp_dispatch (F17)
        manifest_path = os.path.join(pkg_staging, "dispatch-manifest.json")

        # (a) unstamped manifest -> FileHandoffAbsorbBackend REFUSES
        backend_unstamped = FileHandoffAbsorbBackend(pkg_staging)
        try:
            backend_unstamped.absorb("wiki/a.md", "# A\n\n## Intro\nhello\n",
                                     {"raw/e1.md": "fact one\n"})
            case("unstamped manifest refused by FileHandoffAbsorbBackend", False)
        except ValidationError as e:
            case("unstamped manifest refused by FileHandoffAbsorbBackend",
                 "dispatch-record" in str(e) or "dispatch" in str(e))

        # (b) stamp it -> happy path: dispatch block present, sha verifies
        stamped = stamp_dispatch(manifest_path, "gpt-5.5", "openai",
                                 identity_source="attestation:self-test-fixture")
        case("stamp_dispatch adds top-level dispatch block",
             stamped.get("dispatch", {}).get("model") == "gpt-5.5"
             and stamped["dispatch"]["vendor"] == "openai")
        case("stamp_dispatch stamps every packet entry",
             all(p.get("model") == "gpt-5.5" and p.get("vendor") == "openai"
                 for p in stamped["packets"]))
        ok_stamp, _ = _verify_dispatch_stamp(stamped)
        case("stamp_dispatch happy path: recomputed sha matches", ok_stamp)
        case("stamp records its identity_source verbatim",
             stamped["dispatch"]["identity_source"]
             == "attestation:self-test-fixture")

        # (b2) G3 refusals (silence-sweep 2026-08-05): the stamp used to
        # validate nothing, so the skill text's literal placeholder
        # "<your model id>" -- a session's typed self-belief -- became the
        # permanent absorb-side attestation. Each illegal form must refuse.
        def _stamp_refused(kwargs):
            try:
                stamp_dispatch(manifest_path, **kwargs)
                return False
            except ValueError:
                return True
        case("placeholder model '<your model id>' refused",
             _stamp_refused(dict(model="<your model id>", vendor="openai",
                                 identity_source="attestation:x")))
        case("empty vendor refused",
             _stamp_refused(dict(model="gpt-5.5", vendor="  ",
                                 identity_source="attestation:x")))
        case("combined vendor/model string refused (F18)",
             _stamp_refused(dict(model="gpt-5.5", vendor="openai/gpt-5.5",
                                 identity_source="attestation:x")))
        case("missing identity_source refused",
             _stamp_refused(dict(model="gpt-5.5", vendor="openai")))
        case("out-of-class identity_source refused",
             _stamp_refused(dict(model="gpt-5.5", vendor="openai",
                                 identity_source="typed-from-memory")))
        case("empty-payload identity_source refused",
             _stamp_refused(dict(model="gpt-5.5", vendor="openai",
                                 identity_source="operator-attested: ")))
        # a stamp whose identity_source was stripped post-hoc fails
        # verification (fail-closed at the driver's refusal path too)
        stripped = dict(stamped)
        stripped["dispatch"] = dict(stamped["dispatch"])
        stripped["dispatch"].pop("identity_source")
        ok_stripped, stripped_reason = _verify_dispatch_stamp(stripped)
        case("stamp with identity_source stripped fails verification",
             not ok_stripped and "identity_source" in stripped_reason)

        # (c) tamper with a packet entry post-stamp -> sha mismatch -> refused
        tampered = json.load(open(manifest_path, encoding="utf-8"))
        tampered["packets"][0]["view"] = "wiki/tampered.md"
        with open(manifest_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(tampered, fh, indent=1, sort_keys=True)
        ok_tampered, tampered_reason = _verify_dispatch_stamp(tampered)
        case("sha-mismatch stamp detected by _verify_dispatch_stamp",
             not ok_tampered and "mismatch" in tampered_reason)
        # restore the clean stamped manifest for the rest of the fixture
        with open(manifest_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(stamped, fh, indent=1, sort_keys=True)

        # (d) BridgeVerifyBackend derives absorb identity from the stamp,
        # not from bare constructor literals, when dispatch_manifest_path
        # is given
        bvb_from_stamp = BridgeVerifyBackend(
            tmp, gate_kind="migration", staging=staging,
            runner=confirmed_runner(vendor="anthropic", model="claude-sonnet-5"),
            dispatch_manifest_path=manifest_path)
        case("BridgeVerifyBackend derives absorb_vendor from the dispatch "
            "stamp", bvb_from_stamp.absorb_vendor == "openai")
        case("BridgeVerifyBackend derives absorb_model_id from the dispatch "
            "stamp", bvb_from_stamp.absorb_model_id == "gpt-5.5")
        v_from_stamp = bvb_from_stamp.verify(good_packet)
        case("BridgeVerifyBackend (stamp-derived absorb=openai/gpt-5.5) vs "
            "verifier anthropic/claude-sonnet-5, migration gate -> passes "
            "(vendor differs)",
             v_from_stamp["verdict"] == "confirmed")
        case("stamp-derived absorb identity recorded with dispatch-record "
            "absorb_channel",
             v_from_stamp["substrate"]["attestation"]["absorb_channel"]
             == "dispatch-record")

        # (e) an unstamped manifest path passed to BridgeVerifyBackend's
        # constructor is refused just like FileHandoffAbsorbBackend's
        unstamped_manifest_path = os.path.join(tmp, "unstamped-manifest.json")
        with open(unstamped_manifest_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"packets": []}, fh)
        try:
            BridgeVerifyBackend(tmp, dispatch_manifest_path=unstamped_manifest_path)
            case("BridgeVerifyBackend refuses an unstamped manifest path", False)
        except ValidationError:
            case("BridgeVerifyBackend refuses an unstamped manifest path", True)

        # (f) a manifest whose stamp sha256 no longer matches its packets
        # (tampered post-stamp) is refused at BridgeVerifyBackend construction
        # -- it must NEVER surface as a verify verdict (closes the raw
        # dispatch_block bypass: the only door into the attested
        # "dispatch-record" absorb_channel is a manifest that passes
        # _verify_dispatch_stamp)
        tampered_manifest_path = os.path.join(tmp, "tampered-manifest.json")
        tampered_stamped = json.load(open(manifest_path, encoding="utf-8"))
        with open(tampered_manifest_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(tampered_stamped, fh, indent=1, sort_keys=True)
        stamp_dispatch(tampered_manifest_path, model="gpt-5.5", vendor="openai",
                       identity_source="attestation:self-test-fixture")
        tm = json.load(open(tampered_manifest_path, encoding="utf-8"))
        tm.setdefault("packets", []).append(
            {"event": "raw/injected.md", "view": "wiki/injected.md",
             "answer": "injected.json"})
        with open(tampered_manifest_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(tm, fh, indent=1, sort_keys=True)
        try:
            BridgeVerifyBackend(tmp, gate_kind="migration", staging=staging,
                                runner=confirmed_runner(vendor="anthropic",
                                                        model="claude-sonnet-5"),
                                dispatch_manifest_path=tampered_manifest_path)
            case("BridgeVerifyBackend refuses a sha-tampered stamped "
                "manifest at construction (never a verify verdict)", False)
        except ValidationError:
            case("BridgeVerifyBackend refuses a sha-tampered stamped "
                "manifest at construction (never a verify verdict)", True)

        # ------------------------------- v3.0-20: VERIFY_TIMEOUT_MS resolution
        # unpinned (timeout_ms=None) construction resolves from the env, with
        # the legacy 180000 default preserved when the env is unset/garbage;
        # an explicit caller pin (incl. 0) always wins and skips the env.
        _saved_vtm = os.environ.pop("VERIFY_TIMEOUT_MS", None)
        try:
            case("timeout_ms default resolves to 180000 when "
                "VERIFY_TIMEOUT_MS is unset",
                 BridgeVerifyBackend(tmp).timeout_ms == 180000)
            os.environ["VERIFY_TIMEOUT_MS"] = "540000"
            case("timeout_ms: unpinned construction resolves "
                "VERIFY_TIMEOUT_MS=540000 from the env",
                 BridgeVerifyBackend(tmp).timeout_ms == 540000)
            os.environ["VERIFY_TIMEOUT_MS"] = "not-an-int"
            case("timeout_ms: non-integer VERIFY_TIMEOUT_MS falls back to "
                "180000",
                 BridgeVerifyBackend(tmp).timeout_ms == 180000)
            os.environ["VERIFY_TIMEOUT_MS"] = "540000"
            case("timeout_ms: explicit pin (123) wins over an env value, "
                "and explicit pin 0 stays 0 (env NOT consulted)",
                 BridgeVerifyBackend(tmp, timeout_ms=123).timeout_ms == 123
                 and BridgeVerifyBackend(tmp, timeout_ms=0).timeout_ms == 0)
        finally:
            os.environ.pop("VERIFY_TIMEOUT_MS", None)
            if _saved_vtm is not None:
                os.environ["VERIFY_TIMEOUT_MS"] = _saved_vtm

        # ---------------------------------------------- FileHandoffAbsorbBackend
        answer_ok = {"new_text": "# A\n\n## Intro\nhello\n\n## Absorbed\nfact one\n",
                    "manifest": [{"event": "raw/e1.md", "section": "Absorbed"}],
                    "corpus_support": [{"artifact": "raw/e1.md",
                                        "artifact_sha256": _sha256(
                                            "fact one\n"),
                                        "support_lines": ["fact one"]}],
                    "noops": [{"event": "raw/e2.md",
                              "justification_note": "already represented"}]}
        answer_path = os.path.join(pkg_staging,
                                   manifest["packets"][0]["answer"].replace(
                                       "/", os.sep))
        with open(answer_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(answer_ok, fh)
        backend = FileHandoffAbsorbBackend(pkg_staging)
        got = backend.absorb("wiki/a.md", "# A\n\n## Intro\nhello\n",
                             {"raw/e1.md": "fact one\n",
                              "raw/e2.md": "fact two, judgment origin\n"})
        case("FileHandoffAbsorbBackend returns the answer dict verbatim",
             got == answer_ok)

        os.remove(answer_path)
        try:
            backend.absorb("wiki/a.md", "", {})
            case("missing answer raises ValidationError", False)
        except ValidationError:
            case("missing answer raises ValidationError", True)

        with open(answer_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("{not json")
        try:
            backend.absorb("wiki/a.md", "", {})
            case("malformed answer JSON raises ValidationError", False)
        except ValidationError:
            case("malformed answer JSON raises ValidationError", True)
        with open(answer_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(answer_ok, fh)

        try:
            backend.absorb("wiki/does-not-exist.md", "", {})
            case("answer request for wrong/unknown view raises "
                "ValidationError", False)
        except ValidationError:
            case("answer request for wrong/unknown view raises "
                "ValidationError", True)

        # ------------------------------------------------------- integration
        subprocess.run(["git", "-C", repo, "init", "-q"], capture_output=True)
        subprocess.run(["git", "-C", repo, "config", "user.email", "t@t"],
                       capture_output=True)
        subprocess.run(["git", "-C", repo, "config", "user.name", "t"],
                       capture_output=True)
        subprocess.run(["git", "-C", repo, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "seed"],
                       capture_output=True)

        int_staging = os.path.join(tmp, "int-staging")
        os.makedirs(int_staging)
        int_manifest = emit_packets(repo, plan, int_staging, routing_text)
        int_manifest = stamp_dispatch(
            os.path.join(int_staging, "dispatch-manifest.json"),
            "gpt-5.5", "openai",
            identity_source="attestation:self-test-fixture")
        int_answer_path = os.path.join(
            int_staging, int_manifest["packets"][0]["answer"].replace(
                "/", os.sep))
        with open(int_answer_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(answer_ok, fh)

        int_backend = FileHandoffAbsorbBackend(int_staging)
        res = compile_v2.run(repo, plan, int_backend)
        case("integration: compile_v2.run produces a commit at seq 1",
             res["seq"] == 1 and bool(res["sha"]))
        case("integration: judgment-origin no-op is a PENDING_NOOP_CANDIDATE",
             any(nc["event"] == "raw/e2.md"
                 and nc["disposition"] == "PENDING_NOOP_CANDIDATE"
                 for nc in res["noop_candidates"]))

        verify_backend = make_backend(confirmed_runner(vendor="openai",
                                                        model="gpt-5.5"))
        vres = compile_v2.verify_run(repo, res["seq"], verify_backend)
        case("integration: verify_run confirms the pending candidate",
             vres["confirmed"] == 1 and vres["checked"] == 1)

        # ------------------------------------------- AUTONOMY-DISPOSITION guard
        g_compile = dispatch_guard("compile", repo)
        case("dispatch_guard compile -> AUTONOMOUS, permit True",
             g_compile["disposition"] == autonomy.AUTONOMOUS
             and g_compile["permit"] is True
             and g_compile["authorization"] is None)

        ryan_session_ev = [("raw/2026-05-01-ryan-decision.md", "wiki/a.md"),
                          ("raw/2026-05-02-session-7-notes.md", "wiki/a.md")]
        g_verify_trusted = dispatch_guard("verify", repo,
                                          events_views=ryan_session_ev)
        case("dispatch_guard verify, ryan-*/session-* events + wiki view -> "
            "HUMAN-GATE (no mechanical canceller)",
             g_verify_trusted["disposition"] == autonomy.HUMAN_GATE)
        case("dispatch_guard verify trusted-origin, no authorization -> "
            "permit False",
             g_verify_trusted["permit"] is False)

        # AUTHORIZATION-ARTIFACT CLASS (2026-07-06): the fixture authorization
        # file now lives at deploy/evidence/operator-test-authorization.md --
        # the ONLY path shape dispatch_guard's class constraint accepts (see
        # its docstring's r3 confirm-slice lesson).
        os.makedirs(os.path.join(repo, "deploy", "evidence"), exist_ok=True)
        auth_file = os.path.join(repo, "deploy", "evidence",
                                 "operator-test-authorization.md")
        with open(auth_file, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("Operator ack: proceed with verify dispatch for seq 1.\n")
        auth_ok = {"path": auth_file,
                  "quote": "Operator ack: proceed with verify dispatch for "
                           "seq 1."}
        g_verify_authed = dispatch_guard("verify", repo,
                                         events_views=ryan_session_ev,
                                         authorization=auth_ok)
        case("dispatch_guard verify trusted-origin + matching authorization "
            "-> permit True",
             g_verify_authed["permit"] is True)
        case("authorization record carries path + sha256",
             g_verify_authed["authorization"] is not None
             and g_verify_authed["authorization"]["path"] == auth_file
             and g_verify_authed["authorization"]["sha256"]
             == _sha256(open(auth_file, encoding="utf-8").read()))

        auth_bad = {"path": auth_file, "quote": "this quote is not in the file"}
        g_verify_badauth = dispatch_guard("verify", repo,
                                          events_views=ryan_session_ev,
                                          authorization=auth_bad)
        case("dispatch_guard verify: authorization quote NOT in file -> "
            "permit False",
             g_verify_badauth["permit"] is False)

        # -------------------------------------- authorization path containment
        # (d) absolute path OUTSIDE the repo -> permit False
        outside_auth_file = os.path.join(tmp, "outside-authorization.md")
        with open(outside_auth_file, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("Operator ack: proceed with verify dispatch for seq 1.\n")
        auth_outside = {"path": outside_auth_file,
                        "quote": "Operator ack: proceed with verify dispatch "
                                 "for seq 1."}
        g_verify_outside = dispatch_guard("verify", repo,
                                          events_views=ryan_session_ev,
                                          authorization=auth_outside)
        case("dispatch_guard verify: authorization path absolute + OUTSIDE "
            "repo -> permit False",
             g_verify_outside["permit"] is False)
        case("outside-repo authorization reason names the escape",
             "escapes repo" in g_verify_outside["reason"])

        # (e) ".."-traversal path -> permit False
        auth_traversal = {"path": "..\\..\\evil.md",
                          "quote": "Operator ack: proceed with verify "
                                   "dispatch for seq 1."}
        g_verify_traversal = dispatch_guard("verify", repo,
                                            events_views=ryan_session_ev,
                                            authorization=auth_traversal)
        case("dispatch_guard verify: authorization path '..\\\\..\\\\evil.md' "
            "-> permit False",
             g_verify_traversal["permit"] is False)
        case("traversal authorization reason names the escape",
             "escapes repo" in g_verify_traversal["reason"])

        # (f) valid RELATIVE path under repo (in-class) still permits
        rel_auth_file = "deploy/evidence/operator-relative-test.md"
        with open(os.path.join(repo, rel_auth_file.replace("/", os.sep)), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("Operator ack: proceed with verify dispatch for seq 1.\n")
        auth_relative = {"path": rel_auth_file,
                         "quote": "Operator ack: proceed with verify "
                                  "dispatch for seq 1."}
        g_verify_relative = dispatch_guard("verify", repo,
                                           events_views=ryan_session_ev,
                                           authorization=auth_relative)
        case("dispatch_guard verify: valid relative path under repo -> "
            "permit True",
             g_verify_relative["permit"] is True)

        # ---------------------------- (b) AUTHORIZATION-ARTIFACT CLASS CONSTRAINT
        # (2026-07-06, r3 confirm-slice lesson): a document with a MATCHING
        # quote but the WRONG path class must be refused, naming the class --
        # this is the live defect this batch closes (P4 coda: a design-status
        # document satisfied the gate).
        #
        # (g) matching quote at the OLD/non-class location (repo root) ->
        #     refused with the class-constraint reason
        root_level_auth = os.path.join(repo, "authorization.md")
        with open(root_level_auth, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("Operator ack: proceed with verify dispatch for seq 1.\n")
        auth_root_level = {"path": root_level_auth,
                           "quote": "Operator ack: proceed with verify "
                                    "dispatch for seq 1."}
        g_verify_rootlevel = dispatch_guard("verify", repo,
                                            events_views=ryan_session_ev,
                                            authorization=auth_root_level)
        case("class constraint: matching quote at repo-root (non-class "
            "path) -> permit False",
             g_verify_rootlevel["permit"] is False)
        case("class constraint: repo-root refusal names the class",
             "operator-artifact class" in g_verify_rootlevel["reason"]
             and AUTHORIZATION_ARTIFACT_CLASS_GLOB in g_verify_rootlevel["reason"])

        # (h) matching quote at a design-status-shaped non-class path (the
        #     EXACT live defect: a design-status document satisfying the gate)
        design_status_dir = os.path.join(repo, "specs")
        os.makedirs(design_status_dir, exist_ok=True)
        design_status_file = os.path.join(design_status_dir,
                                          "design-status.md")
        with open(design_status_file, "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write("Operator ack: proceed with verify dispatch for seq 1.\n")
        auth_design_status = {"path": design_status_file,
                              "quote": "Operator ack: proceed with verify "
                                       "dispatch for seq 1."}
        g_verify_designstatus = dispatch_guard("verify", repo,
                                               events_views=ryan_session_ev,
                                               authorization=auth_design_status)
        case("class constraint: matching quote in a design-status document "
            "(the live r3 defect) -> permit False",
             g_verify_designstatus["permit"] is False)
        case("class constraint: design-status refusal names the class",
             "operator-artifact class" in g_verify_designstatus["reason"])

        # (i) a file named operator-x.md but NOT under deploy/evidence ->
        #     refused (basename alone does not satisfy the class)
        wrong_dir_auth = os.path.join(repo, "operator-x.md")
        with open(wrong_dir_auth, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("Operator ack: proceed with verify dispatch for seq 1.\n")
        auth_wrong_dir = {"path": wrong_dir_auth,
                          "quote": "Operator ack: proceed with verify "
                                   "dispatch for seq 1."}
        g_verify_wrongdir = dispatch_guard("verify", repo,
                                           events_views=ryan_session_ev,
                                           authorization=auth_wrong_dir)
        case("class constraint: operator-x.md NOT under deploy/evidence -> "
            "permit False",
             g_verify_wrongdir["permit"] is False)
        case("class constraint: wrong-directory refusal names the class",
             "operator-artifact class" in g_verify_wrongdir["reason"])

        # (j) unit-level: _is_authorization_artifact_class direct assertions
        case("_is_authorization_artifact_class: in-class path matches",
             _is_authorization_artifact_class(
                 "deploy/evidence/operator-foo.md"))
        case("_is_authorization_artifact_class: repo-root path refused",
             not _is_authorization_artifact_class("authorization.md"))
        case("_is_authorization_artifact_class: nested-too-deep path refused",
             not _is_authorization_artifact_class(
                 "deploy/evidence/sub/operator-foo.md"))
        case("_is_authorization_artifact_class: wrong basename prefix refused",
             not _is_authorization_artifact_class(
                 "deploy/evidence/not-operator-foo.md"))
        case("_is_authorization_artifact_class: wrong extension refused",
             not _is_authorization_artifact_class(
                 "deploy/evidence/operator-foo.txt"))
        case("_is_authorization_artifact_class: case-sensitive (capitalized "
            "Operator- refused)",
             not _is_authorization_artifact_class(
                 "deploy/evidence/Operator-foo.md"))

        # cross-vendor finding (2026-07-06): fnmatch's `*` matches `/`, so a
        # naive fnmatchcase of the WHOLE path against the class glob lets a
        # nested path one level deeper than the class allows sneak through
        # whenever that extra directory segment itself happens to start with
        # "operator-". Both shapes below are the exact reviewer-named cases.
        case("_is_authorization_artifact_class: nested dir under "
            "deploy/evidence (the exact reviewer-named shape) refused, "
            "not smuggled past via fnmatch's / crossing",
             not _is_authorization_artifact_class(
                 "deploy/evidence/operator-x/y.md"))
        case("_is_authorization_artifact_class: nested dir whose OWN "
            "basename also matches operator-*.md refused (fnmatch's `*` "
            "must not cross the intervening /)",
             not _is_authorization_artifact_class(
                 "deploy/evidence/operator-sub/operator-nested.md"))

        # -------------------------- (c) EMPTY-events_views DISPOSITION (pin)
        # Live observation, 2026-07-06 P4 coda: dispatch_guard("verify", ...)
        # with EMPTY events_views (a zero-no-op verify pass) derives
        # origin_max over an empty set (_origin_max's `not origins` branch
        # returns "human", the LEAST-restrictive rung) and the resulting
        # action still routes to HUMAN-GATE -- fail-safe direction (gates
        # MORE, never less, autonomy spec rung-A A-14). This is CORRECT
        # behavior; pinned here so it cannot regress silently into either
        # AUTONOMOUS (would be a silent-permit hole) or a hard-refuse rung
        # (would over-gate a genuinely empty pass). Same pin for
        # events_views=None, since dispatch_guard treats None as empty
        # (`events_views = events_views or []` in derive_dispatch_action).
        g_verify_empty = dispatch_guard("verify", repo, events_views=[])
        case("empty events_views -> disposition contains HUMAN-GATE",
             "HUMAN-GATE" in g_verify_empty["disposition"])
        case("empty events_views -> permit False (never silently permitted, "
            "never AUTONOMOUS)",
             g_verify_empty["permit"] is False)
        case("empty events_views is not treated as the hard-refuse rung-4/"
            "A-7 class (a valid authorization CAN satisfy it below)",
             "free-selection" not in g_verify_empty["reason"]
             and "A-7" not in g_verify_empty["reason"])

        g_verify_empty_authed = dispatch_guard(
            "verify", repo, events_views=[], authorization=auth_ok)
        case("empty events_views + valid operator-class authorization -> "
            "permit True",
             g_verify_empty_authed["permit"] is True)

        g_verify_none = dispatch_guard("verify", repo, events_views=None)
        case("events_views=None -> disposition contains HUMAN-GATE (treated "
            "as empty)",
             "HUMAN-GATE" in g_verify_none["disposition"])
        case("events_views=None -> permit False",
             g_verify_none["permit"] is False)
        case("events_views=[] and events_views=None: FULL guard dicts are "
            "EQUAL at the whole-record level (not just disposition/permit "
            "checked separately -- pins None==empty structurally, so a "
            "future change that makes only reason or authorization diverge "
            "between the two would still be caught)",
             g_verify_empty == g_verify_none)
        g_verify_none_authed = dispatch_guard(
            "verify", repo, events_views=None, authorization=auth_ok)
        case("events_views=None + valid operator-class authorization -> "
            "permit True",
             g_verify_none_authed["permit"] is True)

        # genuinely-unclassifiable event: a real on-disk file with YAML-
        # hostile frontmatter (origin.py's judgment-class archetype 2/3,
        # unparseable -> "unknown" per its rule 1) -- not just an absent
        # filename, so this exercises the CANONICAL classification path,
        # not the absent-file "parseable-neutral" convention.
        with open(os.path.join(repo, "raw", "2026-01-01-scrape-thing.md"),
                  "w", encoding="utf-8", newline="\n") as fh:
            fh.write("---\nunclosed frontmatter fence, no closing ---\n"
                     "body text\n")
        unknown_ev = ryan_session_ev + [
            ("raw/2026-01-01-scrape-thing.md", "wiki/a.md")]
        g_verify_unknown = dispatch_guard("verify", repo,
                                          events_views=unknown_ev,
                                          authorization=auth_ok)
        case("dispatch_guard verify with an unknown-origin event -> "
            "HUMAN-GATE (A-7 class) even with valid authorization",
             g_verify_unknown["disposition"] == autonomy.HUMAN_GATE
             and g_verify_unknown["permit"] is False)
        case("unknown-origin gate reason cites free-selection or A-7",
             ("free-selection" in g_verify_unknown["reason"])
             or ("A-7" in g_verify_unknown["reason"]))

        # Origin-config self-sufficiency for the ryan-* -> human assertions
        # below ((a), (d), (e)): they must hold regardless of what the LIVE
        # deploy/origin-config.yaml says (a fresh instance configured for a
        # different operator, or none at all, must not turn this suite red),
        # so each passes this suite's OWN [ryan] fixture config via the
        # origin_config_path seam. Written under `tmp` -- auto-cleaned.
        _ryan_cfg = os.path.join(tmp, "origin-config-ryan-fixture.yaml")
        with open(_ryan_cfg, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("human_prefixes: [ryan]\n")

        # (a) a ryan-* event still classifies as human via the canonical path
        o_human, src_human = _origin_of_event_rel(
            repo, "raw/2026-05-01-ryan-decision.md",
            origin_config_path=_ryan_cfg)
        case("canonical _origin_of_event_rel: ryan-* (absent file, "
            "parseable-neutral) -> human",
             o_human == "human")

        # (b) a *-findings.md event with parseable session-sourced
        # frontmatter (no ryan-/session- prefix) still classifies via the
        # canonical rule -- origin.py's rule 3 ("everything else parseable
        # -> corpus"), which the old basename-prefix-only fallback could
        # not have reached (it would have said "unknown").
        with open(os.path.join(repo, "raw",
                               "2026-06-05-products-sync-spike-findings.md"),
                  "w", encoding="utf-8", newline="\n") as fh:
            fh.write("---\nsource: session\ndate: 2026-06-05\n---\n\n"
                     "# Findings\nbody\n")
        o_findings, src_findings = _origin_of_event_rel(
            repo, "raw/2026-06-05-products-sync-spike-findings.md")
        case("canonical _origin_of_event_rel: parseable *-findings.md "
            "(no ryan-/session- prefix) -> corpus-first-party, NOT unknown",
             o_findings == "corpus-first-party")
        case("canonical classification recorded as source='canonical'",
             src_findings == "canonical")

        # (c) genuinely-unclassifiable (unparseable frontmatter) name still
        # -> "unknown", and verify dispatch still hard-refuses (covered by
        # g_verify_unknown above, which now uses exactly this fixture).
        o_unknown, src_unknown = _origin_of_event_rel(
            repo, "raw/2026-01-01-scrape-thing.md")
        case("canonical _origin_of_event_rel: unparseable frontmatter -> "
            "unknown", o_unknown == "unknown")

        # (d) module-load-failure fallback: with origin.py forced "missing"
        # (via a NoneType stand-in swapped in and restored), the guard falls
        # back to the basename-prefix rule instead of crashing, and records
        # "origin_source": "fallback-prefix" honestly.
        real_origin_mod = sys.modules[__name__].__dict__.get("origin")
        globals()["origin"] = None
        try:
            o_fb, src_fb = _origin_of_event_rel(
                repo, "raw/2026-05-01-ryan-decision.md",
                origin_config_path=_ryan_cfg)
            case("origin.py unavailable -> fallback still classifies "
                "ryan-* as human", o_fb == "human")
            case("origin.py unavailable -> origin_source is "
                "'fallback-prefix'", src_fb == "fallback-prefix")
            fb_action = derive_dispatch_action(
                "verify", repo,
                events_views=[("raw/2026-05-01-ryan-decision.md",
                              "wiki/a.md")])
            case("derive_dispatch_action records origin_source="
                "'fallback-prefix' when origin.py is unavailable",
                 fb_action["params"]["origin_source"] == "fallback-prefix")
        finally:
            globals()["origin"] = real_origin_mod

        # (d2) fallback generalization: the basename-prefix fallback reads
        # deploy/origin-config.yaml's human_prefixes DIRECTLY (never a
        # hardcoded "ryan-", even in this degraded branch) -- proven here via
        # the origin_config_path self-test seam, independent of origin.py's
        # own availability. ABSENT config -> no auto-mint (template-port
        # case); a DIFFERENT configured prefix (alice) mints human for that
        # prefix's files and NOT for ryan-*.
        _missing_cfg = os.path.join(tempfile.gettempdir(),
                                    "compile-backends-origin-config-missing-%d.yaml"
                                    % os.getpid())
        case("fallback (d2): ABSENT origin-config -> ryan-* does NOT fallback-classify"
            " human (no hardcoded prefix)",
             _origin_of_event_rel_fallback("raw/2026-05-01-ryan-decision.md",
                                           origin_config_path=_missing_cfg) != "human")
        _fd_alice, _alice_cfg = tempfile.mkstemp(prefix="compile-backends-origin-config-",
                                                 suffix=".yaml")
        with os.fdopen(_fd_alice, "w", encoding="utf-8") as _fh:
            _fh.write("human_prefixes: [alice]\n")
        try:
            case("fallback (d2): ALT-PREFIX config (alice) -> alice-* fallback-classifies"
                " human",
                 _origin_of_event_rel_fallback("raw/2026-05-01-alice-decision.md",
                                               origin_config_path=_alice_cfg) == "human")
            case("fallback (d2): ALT-PREFIX config (alice) -> ryan-* no longer"
                " fallback-classifies human ('ryan' is not this config's prefix)",
                 _origin_of_event_rel_fallback("raw/2026-05-01-ryan-decision.md",
                                               origin_config_path=_alice_cfg) != "human")
        finally:
            try:
                os.remove(_alice_cfg)
            except OSError:
                pass

        # (d3) STRICT member validation + literal matching (cross-vendor
        # review; identical rule to origin.load_origin_config): a non-string
        # or empty-string MEMBER degrades the ENTIRE config to empty prefixes
        # (even otherwise-valid members mint nothing), and prefixes match
        # LITERALLY (re.escape -- a '.' in a prefix never wildcards).
        def _mk_cfg(content):
            fd, p = tempfile.mkstemp(prefix="compile-backends-origin-config-",
                                     suffix=".yaml")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            return p

        _bad_member_cfg = _mk_cfg("human_prefixes: [ryan, 42]\n")
        _empty_member_cfg = _mk_cfg("human_prefixes: ['', ryan]\n")
        _meta_cfg = _mk_cfg("human_prefixes: [a.b]\n")
        try:
            case("fallback (d3): non-string member -> ENTIRE config degrades;"
                " ryan-* does NOT fallback-classify human",
                 _fallback_human_prefixes(_bad_member_cfg) == []
                 and _origin_of_event_rel_fallback(
                     "raw/2026-05-01-ryan-decision.md",
                     origin_config_path=_bad_member_cfg) != "human")
            case("fallback (d3): empty-string member -> ENTIRE config degrades;"
                " no '^-' catch-all, '-'-containing stem NOT human",
                 _fallback_human_prefixes(_empty_member_cfg) == []
                 and _origin_of_event_rel_fallback(
                     "raw/2026-05-01-anything-thing.md",
                     origin_config_path=_empty_member_cfg) != "human")
            case("fallback (d3): LITERAL matching -- prefix 'a.b' matches 'a.b-*'",
                 _origin_of_event_rel_fallback(
                     "raw/2026-05-01-a.b-thing.md",
                     origin_config_path=_meta_cfg) == "human")
            case("fallback (d3): LITERAL matching -- prefix 'a.b' does NOT"
                " wildcard-match 'axb-*' (re.escape, no regex injection)",
                 _origin_of_event_rel_fallback(
                     "raw/2026-05-01-axb-thing.md",
                     origin_config_path=_meta_cfg) != "human")
        finally:
            for _p in (_bad_member_cfg, _empty_member_cfg, _meta_cfg):
                try:
                    os.remove(_p)
                except OSError:
                    pass

        # (e) staleness-module-unavailable fallback: with staleness.py forced
        # "missing" (origin.py still present), the canonical path must NOT
        # be taken -- fail-open would let an unparseable ryan-* event slip
        # through as "canonical" via the parseable=True assumption. The
        # guard must fall back to the basename-prefix rule instead, exactly
        # as it does when origin.py itself is unavailable.
        real_staleness_mod = sys.modules[__name__].__dict__.get("staleness")
        globals()["staleness"] = None
        try:
            o_stale_fb, src_stale_fb = _origin_of_event_rel(
                repo, "raw/2026-05-01-ryan-decision.md",
                origin_config_path=_ryan_cfg)
            case("staleness.py unavailable -> fallback still classifies "
                "ryan-* as human", o_stale_fb == "human")
            case("staleness.py unavailable -> origin_source is "
                "'fallback-prefix', NOT 'canonical'",
                 src_stale_fb == "fallback-prefix")
            # the unparseable-frontmatter fixture from (c): with staleness.py
            # unavailable the guard cannot determine parseability at all, so
            # it must take the fallback path (basename has no ryan-/session-
            # prefix -> "unknown" via the fallback rule) rather than ever
            # reporting "canonical".
            o_stale_fb2, src_stale_fb2 = _origin_of_event_rel(
                repo, "raw/2026-01-01-scrape-thing.md")
            case("staleness.py unavailable -> unparseable-name event still "
                "resolves via fallback-prefix, never 'canonical'",
                 src_stale_fb2 == "fallback-prefix")
        finally:
            globals()["staleness"] = real_staleness_mod

        # (f) per-event read/parse failure inside the canonical path (both
        # origin.py and staleness.py available, file exists) must classify
        # that event "unknown" directly -- fail-dangerous, never
        # parseable=True by assumption. Simulated by monkeypatching
        # staleness.split_frontmatter to raise for this one call.
        raising_event_rel = "raw/2026-05-02-ryan-raises.md"
        with open(os.path.join(repo, "raw", "2026-05-02-ryan-raises.md"),
                  "w", encoding="utf-8", newline="\n") as fh:
            fh.write("---\nsource: human\n---\n\nbody\n")
        real_split_frontmatter = staleness.split_frontmatter

        def _raising_split_frontmatter(*a, **kw):
            raise RuntimeError("simulated parse failure")

        staleness.split_frontmatter = _raising_split_frontmatter
        try:
            o_raise, src_raise = _origin_of_event_rel(repo, raising_event_rel)
            case("read/parse failure inside canonical path -> 'unknown' "
                "(fail-dangerous, not parseable=True by assumption)",
                 o_raise == "unknown")
            case("read/parse failure inside canonical path is still "
                "recorded as source='canonical' (module loaded fine, "
                "only this event's parse failed)",
                 src_raise == "canonical")
        finally:
            staleness.split_frontmatter = real_split_frontmatter

        # origin_source == 'canonical' on the normal (origin.py available)
        # path, for a batch where every event resolved canonically.
        canonical_action = derive_dispatch_action(
            "verify", repo,
            events_views=[("raw/2026-05-01-ryan-decision.md", "wiki/a.md")])
        case("derive_dispatch_action records origin_source='canonical' "
            "when origin.py resolved every event",
             canonical_action["params"]["origin_source"] == "canonical")

        # -------------------------- live-repo test (documented, see task):
        # the 4 real OPS-4-slice events named without a ryan-/session-
        # prefix (raw/*-findings.md, raw/*-lock.md) must classify as
        # human/corpus-first-party via the REAL repo's real attestations +
        # census, never "unknown" -- this is the concrete case the guard
        # was reworked for. Runs against the actual project repo root
        # (two levels up from this file: deploy/ -> repo root), not the
        # tmp fixture repo above, because the real attestations.yaml +
        # real raw/ files are the evidence that makes this true.
        real_repo = os.path.dirname(_HERE)
        real_flagged = [
            "raw/2026-06-05-products-sync-spike-findings.md",
            "raw/2026-06-11-invoices-payments-census-findings.md",
            "raw/2026-06-21-gateway-sync-runtime-model-lock.md",
            "raw/2026-06-25-pan-guard-iin-gate-stage1-built.md",
        ]
        if os.path.isdir(os.path.join(real_repo, "raw")):
            for rel in real_flagged:
                if not os.path.isfile(os.path.join(real_repo,
                                                    rel.replace("/", os.sep))):
                    continue
                o_real, src_real = _origin_of_event_rel(real_repo, rel)
                case("live-repo: %s classifies as human/corpus-first-party "
                    "(canonical census), not unknown" % rel,
                     o_real in ("human", "corpus-first-party"))
                case("live-repo: %s classification is 'canonical'" % rel,
                     src_real == "canonical")

        try:
            derive_dispatch_action("mystery-kind", repo)
            case("derive_dispatch_action: unknown kind raises ValueError",
                False)
        except ValueError:
            case("derive_dispatch_action: unknown kind raises ValueError",
                True)
        try:
            dispatch_guard("mystery-kind", repo)
            case("dispatch_guard: unknown kind raises ValueError", False)
        except ValueError:
            case("dispatch_guard: unknown kind raises ValueError", True)

        # agent_claims/self_report can never be read: the public API for
        # deriving an action takes only (kind, repo, plan, packet_paths,
        # events_views) -- there is no parameter through which a caller could
        # smuggle a self-report into the derivation, and the returned params
        # dict contains only the documented mechanical keys.
        verify_action = derive_dispatch_action("verify", repo,
                                               events_views=ryan_session_ev)
        compile_action = derive_dispatch_action("compile", repo)
        case("derive_dispatch_action(verify) params contain only documented "
            "keys (no agent_claims/self_report)",
             set(verify_action["params"]) == {
                 "egress", "payload_shape", "payload_value_origins",
                 "payload_origin_max", "mechanical_canceller",
                 "canceller_pre_transmission", "origin_source"})
        case("derive_dispatch_action(compile) params contain only documented "
            "keys (no agent_claims/self_report)",
             set(compile_action["params"]) == {"undo_registered"})

        # determinism: same inputs -> same output dict
        g_a = dispatch_guard("verify", repo, events_views=ryan_session_ev,
                             authorization=auth_ok)
        g_b = dispatch_guard("verify", repo, events_views=ryan_session_ev,
                             authorization=auth_ok)
        case("dispatch_guard is deterministic: same inputs -> same output",
             g_a == g_b)
        g_c = dispatch_guard("compile", repo)
        g_d = dispatch_guard("compile", repo)
        case("dispatch_guard is deterministic for compile kind too",
             g_c == g_d)

        # --------------------------------------- ENFORCED entry points
        # A fresh view/event pair so this compile run produces its OWN new
        # pending PENDING_NOOP_CANDIDATE (seq 1's e2 candidate was already
        # consumed by the verify_run above) -- events are named with a
        # ryan-* prefix AND carry parseable frontmatter, so the canonical
        # origin.py classification (rule 1 unparseable-check now runs
        # first) still resolves to "human" -> HUMAN-GATE, matching the
        # guard tests already exercised above.
        os.makedirs(os.path.join(repo, "raw"), exist_ok=True)
        with open(os.path.join(repo, "raw",
                               "2026-05-03-ryan-guarded-fact.md"),
                  "w", encoding="utf-8", newline="\n") as fh:
            fh.write("---\nsource: ryan\ndate: 2026-05-03\n---\n\n"
                     "already represented\n")
        guarded_plan = {"items": [
            {"view": "wiki/a.md",
             "events": ["raw/2026-05-03-ryan-guarded-fact.md"],
             "event_class": {
                 "raw/2026-05-03-ryan-guarded-fact.md":
                     {"class": "t1", "origin": "explicit"}}},
        ]}
        guarded_staging = os.path.join(tmp, "guarded-staging")
        os.makedirs(guarded_staging)
        guarded_manifest = emit_packets(repo, guarded_plan, guarded_staging,
                                        routing_text)
        guarded_manifest = stamp_dispatch(
            os.path.join(guarded_staging, "dispatch-manifest.json"),
            "gpt-5.5", "openai",
            identity_source="attestation:self-test-fixture")
        guarded_answer_path = os.path.join(
            guarded_staging,
            guarded_manifest["packets"][0]["answer"].replace("/", os.sep))
        with open(guarded_answer_path, "w", encoding="utf-8",
                  newline="\n") as fh:
            json.dump({"new_text": None, "manifest": [],
                      "corpus_support": [],
                      "noops": [{
                          "event": "raw/2026-05-03-ryan-guarded-fact.md",
                          "justification_note": "already represented"}]},
                     fh)
        guarded_backend = FileHandoffAbsorbBackend(guarded_staging)

        # (c) run_guarded on the compile fixture -> proceeds (AUTONOMOUS)
        g_res = run_guarded(repo, guarded_plan, guarded_backend)
        case("run_guarded proceeds and returns a real compile result",
             bool(g_res.get("sha")) and g_res.get("seq") is not None)
        case("run_guarded result carries dispatch_guard AUTONOMOUS record",
             g_res["dispatch_guard"]["disposition"] == autonomy.AUTONOMOUS)
        guarded_seq = g_res["seq"]

        # this compile run's event is human-origin -> its pending candidate
        # (if any) would trigger HUMAN-GATE on verify. Confirm the pending
        # noop candidate is present and unverified before testing the guard.
        pending_pairs = _pending_events_views(repo, guarded_seq)
        case("_pending_events_views derives exactly the pending candidate's "
            "(event, view) pair from the journal record",
             pending_pairs == [("raw/2026-05-03-ryan-guarded-fact.md",
                                "wiki/a.md")])

        pre_chain_len = compile_v2.core.check_chain(repo)

        def _boom_verify(*a, **kw):
            raise AssertionError(
                "compile_v2.verify_run must not be called on refusal")
        boom_backend = type("Boom", (), {"verify": staticmethod(
            lambda packet: _boom_verify())})()

        # (a) verify_run_guarded WITHOUT authorization -> DispatchRefused,
        #     and journals NOTHING (chain length unchanged, no new commit)
        try:
            verify_run_guarded(repo, guarded_seq, boom_backend)
            case("verify_run_guarded without authorization raises "
                "DispatchRefused", False)
        except DispatchRefused as e:
            case("verify_run_guarded without authorization raises "
                "DispatchRefused", e.guard["permit"] is False)
        case("refused verify_run_guarded journaled nothing (chain length "
            "unchanged)",
             compile_v2.core.check_chain(repo) == pre_chain_len)

        # (b) verify_run_guarded WITH a valid under-repo authorization
        #     file+quote + stub-runner backend -> proceeds, result carries
        #     dispatch_guard record with sha256
        guard_auth_file = os.path.join(repo, "deploy", "evidence",
                                       "operator-guard-authorization.md")
        os.makedirs(os.path.dirname(guard_auth_file), exist_ok=True)
        with open(guard_auth_file, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("Operator ack: proceed with verify dispatch for seq %d."
                     "\n" % guarded_seq)
        guard_auth = {"path": guard_auth_file,
                     "quote": "Operator ack: proceed with verify dispatch "
                              "for seq %d." % guarded_seq}
        stub_verify_backend = make_backend(confirmed_runner(
            vendor="openai", model="gpt-5.5"))
        vg_res = verify_run_guarded(repo, guarded_seq, stub_verify_backend,
                                    authorization=guard_auth)
        case("verify_run_guarded with valid authorization proceeds",
             vg_res.get("checked") == 1)
        case("verify_run_guarded result carries dispatch_guard record with "
            "sha256",
             vg_res["dispatch_guard"]["permit"] is True
             and vg_res["dispatch_guard"]["authorization"] is not None
             and vg_res["dispatch_guard"]["authorization"]["sha256"]
             == _sha256(open(guard_auth_file, encoding="utf-8").read()))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    label = "compile-backends self-test"
    if failed:
        print("%s: FAIL (%d/%d)" % (label, total - failed, total))
        return 1
    print("%s: PASS (%d/%d)" % (label, total, total))
    return 0


def _cli_dispatch_check(argv):
    if not argv or argv[0] not in ("compile", "verify"):
        print("usage: compile-backends.py --dispatch-check compile|verify "
             "[--events-views <json file>] [--auth-path P --auth-quote Q]")
        return 3
    kind = argv[0]
    rest = argv[1:]
    events_views = None
    authorization = None
    i = 0
    while i < len(rest):
        if rest[i] == "--events-views" and i + 1 < len(rest):
            with open(rest[i + 1], encoding="utf-8") as fh:
                data = json.load(fh)
            events_views = [tuple(pair) for pair in data]
            i += 2
        elif rest[i] == "--auth-path" and i + 1 < len(rest):
            authorization = authorization or {}
            authorization["path"] = rest[i + 1]
            i += 2
        elif rest[i] == "--auth-quote" and i + 1 < len(rest):
            authorization = authorization or {}
            authorization["quote"] = rest[i + 1]
            i += 2
        else:
            i += 1
    repo = os.getcwd()
    guard = dispatch_guard(kind, repo, events_views=events_views,
                           authorization=authorization)
    print(json.dumps(guard, indent=1, sort_keys=True))
    return 0 if guard["permit"] else 3


def main(argv):
    if "--self-test" in argv:
        return self_test()
    if "--dispatch-check" in argv:
        idx = argv.index("--dispatch-check")
        return _cli_dispatch_check(argv[idx + 1:])
    print(__doc__.strip().split("\n\n")[-1])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
