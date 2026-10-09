#!/usr/bin/env python3
"""check-substrate.py -- LLM-5 substrate-separation gate (test-plan tp:384) with F17/F18.

The `verified:` block records the absorb and verify substrates as SEPARATE `vendor` + `model_id`
fields (F18), derived from invocation metadata, NEVER orchestrator-supplied strings (F17). The
substrate-gate policy the tests check (spec §5):

  * ROUTINE T1 verify gates on MODEL_ID difference (absorb model_id != verify model_id) --
    hub-fired Claude-on-Claude is fine as long as the models differ.
  * MIGRATION-era content audits + DESIGN-gate verifications gate on VENDOR difference (the
    cross-substrate firewall) -- same vendor is not enough, even if model_ids differ.

It is also the single home of the v3.0-233 identity normalization (same_model) and the
verifier DIRECTION rule (choose_verifier): see the comments above each.

Usage: check-substrate.py --self-test
Exit codes: 0 = policy holds on every fixture | 1 = a fixture slipped | 2 = INCONCLUSIVE.
"""

import json
import os
import re
import sys

ROUTINE = "routine"
MIGRATION = "migration"   # content audit
DESIGN = "design"         # design-gate verification


def verified_block_wellformed(block):
    """F18: absorb/verify substrates must be present as SEPARATE vendor + model_id fields, not
    a single combined string. Returns (ok, reason)."""
    if not isinstance(block, dict):
        return False, "verified block is not a mapping"
    required = ("verifier_vendor", "verifier_model_id", "absorb_vendor", "absorb_model_id")
    missing = [k for k in required if not block.get(k)]
    if missing:
        return False, "missing separate substrate field(s): %s" % ", ".join(missing)
    # a combined "vendor/model" smuggled into one field is a fail
    for k in ("verifier_vendor", "absorb_vendor"):
        if "/" in str(block[k]) or ":" in str(block[k]):
            return False, "%s looks like a combined vendor/model string (F18 requires separate)" % k
    return True, "ok"


def substrate_derived_from_invocation(block):
    """F17 (schema layer): the block must explicitly carry `substrate_source: invocation-metadata`.
    FAIL-CLOSED: an ABSENT substrate_source is rejected (no default) -- an orchestrator that omits
    the field must not be accepted as if it were invocation-derived; likewise any other value
    (e.g. 'orchestrator-declared') is rejected.

    LIMIT (the runtime residual, cross-vendor-flagged 2026-07-03): this is a SCHEMA check -- it
    proves the block claims invocation-metadata provenance and fails closed otherwise. It does NOT
    by itself prove the marker reflects REAL invocation metadata rather than a spoofed string; that
    guarantee lives at the WRITE PATH (assemble.py/VERIFY, P4), which must POPULATE the substrate
    fields + this marker from actual invocation metadata, never copy an orchestrator descriptor.
    The write-path enforcement is an operational (needs-operational-data) leg owed at P4."""
    return block.get("substrate_source") == "invocation-metadata"


def attestation_ok(block):
    """F17 write-path attestation gate (2026-07-05 design). ADDITIVE to
    substrate_derived_from_invocation -- does NOT change its signature or
    semantics. `block` here is the substrate block's `attestation` sub-object
    (or the whole substrate block if it carries `attestation` as a key --
    callers pass block.get("attestation") explicitly).

    BOTH sides must be attested (AND-semantics, per the F17 attestation
    design 2026-07-05): the verifier channel must be exactly
    "subprocess-runtime" AND the absorb channel must be exactly
    "dispatch-record" AND artifact + artifact_sha256 must both be present.
    A block that only attests one side (verifier-only or absorb-only) FAILS
    CLOSED -- this is deliberately NOT an OR: a good verifier channel with an
    absent/missing absorb_channel must not pass, and vice versa.
    """
    if not isinstance(block, dict):
        return False, "attestation block is not a mapping"
    channel = block.get("channel")
    absorb_channel = block.get("absorb_channel")
    if channel != "subprocess-runtime":
        return False, "verifier channel is not subprocess-runtime: %r" % (channel,)
    if absorb_channel != "dispatch-record":
        return False, "absorb channel is not dispatch-record: %r" % (absorb_channel,)
    artifact = block.get("artifact")
    artifact_sha = block.get("artifact_sha256")
    if not artifact or not artifact_sha:
        return False, "attestation missing artifact path+sha256"
    return True, "ok"


# ------------------------------------------------------------------ identity normalization
# v3.0-233 (the same-model trap, audit 2026-10-06 root cause 1). The gate used to compare raw
# strings, so two differently-written forms of ONE model ("gpt-5.5" / "GPT-5.5",
# "claude-opus-4.8" / "claude-opus-4-8", "anthropic/claude-opus-4-8") passed a routine gate as
# "different models", while the claude CLI's alias-vs-full-id reporting ("fable" requested,
# "claude-fable-5-1" reported) could not be recognised as one model at all.
#
# THE NORMALIZATION (documented; same_model() is the only comparison the gate uses):
#   1. strip + lower-case;
#   2. drop a leading "<provider>/" or "models/" prefix ("anthropic/", "openai/", "xai/", ...);
#   3. drop a trailing bracketed context-window tag ("[1m]") -- the same weights;
#   4. treat ".", "_" and whitespace as "-" and collapse repeated "-" ("4.8" == "4-8");
#   5. split off a trailing snapshot date ("-20250929", "@20250929", "-2024-08-06") and a
#      trailing "-latest": an UNDATED id is the moving name of the newest snapshot, so it is
#      the same model as any dated form of the same base; two DIFFERENT dates are two
#      different snapshots and stay different;
#   6. an Anthropic ALIAS (a single alphabetic token other than "claude", e.g. "fable",
#      "opus", "sonnet") is the same model as a full "claude-..." id that carries that token
#      ("fable" == "claude-fable-5-1"); the claude CLI accepts the alias and reports the full id.
# What it never does: drop a version number, a size/tier token ("mini", "haiku", "sol"), or
# any other token -- so "gpt-5" != "gpt-5.5", "claude-opus-4-8" != "claude-opus-4-7",
# "gpt-5.5" != "gpt-5.5-mini". Unknown or empty ids are never "the same" as anything by
# normalization; the gate treats a missing id as a failure on its own (fail closed).
_PROVIDER_PREFIX_RE = re.compile(r"^(?:anthropic|openai|xai|google|models|bedrock|vertex)/")
_CONTEXT_TAG_RE = re.compile(r"\[[^\]]*\]$")
_SNAPSHOT_RE = re.compile(r"(?:[-@](\d{8})|-(\d{4}-\d{2}-\d{2}))$")
_ANTHROPIC_ALIAS_RE = re.compile(r"^[a-z]+$")


def _model_base_and_date(model):
    """(base, snapshot_date_or_None) after normalization steps 1-5; (None, None) for empty."""
    if model is None:
        return None, None
    m = str(model).strip().lower()
    if not m:
        return None, None
    m = _PROVIDER_PREFIX_RE.sub("", m)
    m = _CONTEXT_TAG_RE.sub("", m).strip()
    m = re.sub(r"[._\s]+", "-", m)
    m = re.sub(r"-{2,}", "-", m).strip("-")
    date = None
    snap = _SNAPSHOT_RE.search(m)
    if snap:
        date = (snap.group(1) or snap.group(2)).replace("-", "")
        m = m[:snap.start()].rstrip("-")
    if m.endswith("-latest"):
        m = m[:-len("-latest")]
    return (m or None), date


def canonical_model_id(model):
    """The normalized base id (steps 1-5 above, snapshot date dropped); None for empty."""
    return _model_base_and_date(model)[0]


def _is_anthropic_alias(base):
    return bool(base) and base != "claude" and _ANTHROPIC_ALIAS_RE.match(base) is not None


def is_alias_of(alias, full_id):
    """True when `alias` is an Anthropic alias token (step 6) and `full_id` is a claude-... id
    carrying that token -- the case the claude CLI reports as model_match 'alias'.

    What this does NOT establish (v3.0.64 review round 1): which VERSION an alias denoted. An
    alias such as 'opus' matches every claude-opus-* id. That is safe in both places it is
    used: in same_model() it can only make two ids compare EQUAL, which makes a routine gate
    REFUSE (more cautious, never less); in the F17 gate it accepts an alias request and then
    records the RUNTIME id the CLI reported as the verifier model -- the model that actually
    ran is what every later comparison uses."""
    a, _ad = _model_base_and_date(alias)
    f, _fd = _model_base_and_date(full_id)
    if not (a and f) or not _is_anthropic_alias(a) or not f.startswith("claude-"):
        return False
    return a in f.split("-")


def same_model(a, b):
    """True when two model ids name the same model under the documented normalization."""
    ba, da = _model_base_and_date(a)
    bb, db = _model_base_and_date(b)
    if not ba or not bb:
        return False
    if ba == bb:
        return not (da and db and da != db)       # two different snapshots stay different
    return is_alias_of(a, b) or is_alias_of(b, a)


_VENDOR_ALIASES = {"claude": "anthropic", "codex": "openai", "gpt": "openai", "grok": "xai",
                   "gemini": "google"}


def normalize_vendor(vendor):
    """Lower-cased vendor with the bridge's aliases folded (claude->anthropic, codex->openai);
    None for empty. Unknown vendors pass through lower-cased (compared as themselves)."""
    if vendor is None:
        return None
    v = str(vendor).strip().lower()
    if not v:
        return None
    return _VENDOR_ALIASES.get(v, v)


def model_vendor_hint(model):
    """Best-effort vendor of a model id (used only to keep a VERIFY_MODEL meant for one vendor
    from being handed to the other vendor's CLI); None when the id does not say."""
    base = canonical_model_id(model)
    if not base:
        return None
    if base.startswith("claude") or _is_anthropic_alias(base) and base in (
            "opus", "sonnet", "haiku", "fable", "opusplan"):
        return "anthropic"
    if base.startswith("gpt") or base.startswith("codex") or re.match(r"^o\d", base):
        return "openai"
    if base.startswith("grok"):
        return "xai"
    if base.startswith("gemini"):
        return "google"
    return None


def substrate_gate_ok(absorb_vendor, absorb_model, verify_vendor, verify_model, gate_kind):
    """The F18 decision: does this verify leg satisfy the substrate separation its gate demands?

    v3.0-233: both comparisons run on NORMALIZED identities (same_model / normalize_vendor
    above) and a missing identity fails closed. The tier semantics are unchanged: ROUTINE is
    model difference (same vendor compliant -- the anti-promotion pin below), MIGRATION/DESIGN
    is vendor difference."""
    if gate_kind == ROUTINE:
        if not absorb_model or not verify_model or same_model(absorb_model, verify_model):
            return False                              # one model, however it is written
        return absorb_model != verify_model          # model_id difference suffices
    if gate_kind in (MIGRATION, DESIGN):
        absorb_vendor, verify_vendor = normalize_vendor(absorb_vendor), normalize_vendor(verify_vendor)
        if not absorb_vendor or not verify_vendor:
            return False
        return absorb_vendor != verify_vendor         # vendor difference REQUIRED (firewall)
    return False  # unknown gate kind -> fail closed


# ------------------------------------------------------------------ verifier routing (v3.0-233)
# THE DIRECTION RULE (engine half of v3.0-233 / v3.0-183). The verifier for a leg is chosen
# from the ARTIFACT's stamped author (the dispatch stamp's vendor and model; for a content
# audit, the corpus records), never from the session that happens to run the engine, and the
# choice is TIERED exactly like substrate_gate_ok:
#   ROUTINE (compile verify, routing): the OPPOSITE vendor when its CLI is available (that
#     always differs in model); otherwise the SAME vendor with a DIFFERENT model -- an explicit
#     VERIFY_MODEL, or the operator registry's second model (`<vendor>_second` in
#     ~/.rheoscope/frontier-models.json); otherwise REFUSE with a plain message. A same-model
#     leg is never run.
#   MIGRATION / DESIGN (content audits, design gates): the opposite vendor of the author,
#     required; refuse when its CLI is unavailable.
# The bridge has two answering directions (verify-cli.js --direction openai|anthropic);
# `--requester-vendor` is always the AUTHOR's vendor.
BRIDGE_DIRECTIONS = ("openai", "anthropic")
# The other vendor(s) in preference order, by author vendor. A third-family author (xai,
# google) may be verified by either bridge direction.
_OPPOSITE_ORDER = {"openai": ("anthropic",), "anthropic": ("openai",)}
_DEFAULT_ORDER = ("anthropic", "openai")
# The v3.0.63 bridge REFUSES every requester equal to the answering vendor (models.js
# resolveRequesterVendor; verify-cli exits 64), and the engine never declares a false
# requester. So the routine same-vendor/different-model route is SELECTED by the policy but
# cannot be DISPATCHED through the shipped bridge: choose_verifier refuses it with a message
# that says so. Flip this only together with a bridge that accepts a routine same-vendor call.
BRIDGE_SAME_VENDOR_LEGS = False
CLI_NAMES = {"openai": "Codex CLI (`codex` >= 0.144)",
             "anthropic": "Claude Code CLI (`claude` >= 2.1.220)"}


def opposite_vendors(author_vendor):
    a = normalize_vendor(author_vendor)
    order = _OPPOSITE_ORDER.get(a, _DEFAULT_ORDER)
    return tuple(v for v in order if v != a)


def choose_verifier(author_vendor, author_model, gate_kind, available, second_model=None,
                    env_model=None, same_vendor_ok=None):
    """Pick the verifier route for one leg. Pure: every input is passed in.

    available     -- iterable of bridge directions whose CLI is present ("openai"/"anthropic")
    second_model  -- the operator registry's second model for the author's vendor, if any
    env_model     -- VERIFY_MODEL, if set (an explicit same-vendor model for a routine leg)
    Returns a dict: ok, direction, verifier_vendor, model (an explicit --model, or None to let
    the bridge resolve), requester_vendor, author_vendor, author_model, gate_kind, basis
    ("opposite-vendor" | "same-vendor-different-model"), drop_env_model (VERIFY_MODEL names the
    author's vendor and must not reach the other vendor's CLI), refusal (None when ok)."""
    if same_vendor_ok is None:
        same_vendor_ok = BRIDGE_SAME_VENDOR_LEGS
    a = normalize_vendor(author_vendor)
    avail = {normalize_vendor(v) for v in (available or ()) if normalize_vendor(v)}
    route = {"ok": False, "direction": None, "verifier_vendor": None, "model": None,
             "requester_vendor": a, "author_vendor": a, "author_model": author_model,
             "gate_kind": gate_kind, "basis": None, "model_source": None, "drop_env_model": False,
             "refusal": None}

    def refuse(msg):
        route["refusal"] = msg
        return route

    if gate_kind not in (ROUTINE, MIGRATION, DESIGN):
        return refuse("unknown gate kind %r -- no verifier is chosen for it" % (gate_kind,))
    if not a:
        return refuse("the artifact carries no author vendor (no dispatch stamp / corpus "
                      "record names who wrote it), so no verifier can be chosen -- stamp the "
                      "dispatch (compile-backends.stamp_dispatch) before verifying")
    # v3.0.64: a placeholder is a MISSING identity, not a vendor -- "unknown" routed as if it
    # were a third family would pick a side arbitrarily (found by drill-planted-defects)
    if a in ("unknown", "none", "null", "n/a", "na", "?", "tbd"):
        return refuse("the author vendor is recorded as %r -- a missing identity, not a vendor; "
                      "no verifier can be chosen until the dispatch is stamped with a real one" % a)
    if "/" in a or ":" in a:
        return refuse("author vendor %r looks like a combined vendor/model string" % a)
    # v3.0.64 review round 1: a ROUTINE leg's separation is by model, so the author's model is
    # required too; vendor-tier legs separate by vendor and may carry a labelled unrecorded model
    am = (str(author_model).strip().lower() if author_model is not None else "")
    if gate_kind == ROUTINE and am in ("", "unknown", "none", "null", "n/a", "na", "?", "tbd"):
        return refuse("routine leg: the author's model is not recorded (%r) -- a routine leg "
                      "separates by model, so it cannot be routed without one; stamp the dispatch "
                      "with the real model" % (author_model,))
    others = opposite_vendors(a)
    for v in others:
        if v in avail:
            route.update(ok=True, direction=v, verifier_vendor=v, basis="opposite-vendor",
                         drop_env_model=bool(env_model) and model_vendor_hint(env_model) not in (None, v))
            return route
    want = " or ".join(CLI_NAMES.get(v, v) for v in others)
    if gate_kind in (MIGRATION, DESIGN):
        return refuse("this is a vendor-tier leg (%s): the verifier must be a different VENDOR "
                      "from the author (%s), and no %s was found. Install it and log in, then "
                      "re-run." % (gate_kind, a, want))
    # ROUTINE: the same vendor with a different model
    if a not in BRIDGE_DIRECTIONS:
        return refuse("routine leg: no %s was found, and the bridge has no %s verifier for a "
                      "same-vendor check. Install one of them and log in." % (want, a))
    if a not in avail:
        return refuse("routine leg: no verifier CLI was found at all (neither %s nor %s). "
                      "Install one and log in." % (want, CLI_NAMES.get(a, a)))
    candidate, source = None, None
    if env_model and model_vendor_hint(env_model) in (None, a):
        candidate, source = env_model, "VERIFY_MODEL"
    elif second_model:
        candidate, source = second_model, "registry %s_second" % a
    if not candidate:
        return refuse("routine leg: the other vendor's %s is not installed, and no second %s "
                      "model is set for a same-vendor check (the author is %s/%s). Install %s, "
                      "or set VERIFY_MODEL (or \"%s_second\" in ~/.rheoscope/frontier-models.json) "
                      "to a %s model other than %s."
                      % (want, a, a, author_model, want, a, a, author_model))
    if not author_model or same_model(candidate, author_model):
        return refuse("routine leg: the same-vendor verifier %s (from %s) is the SAME model as "
                      "the author (%s/%s) -- a model never verifies its own work. Install %s, or "
                      "set a different %s model." % (candidate, source, a, author_model, want, a))
    route.update(direction=a, verifier_vendor=a, model=candidate,
                 basis="same-vendor-different-model", model_source=source)
    if not same_vendor_ok:
        return refuse("routine leg: the policy allows a same-vendor check here (%s/%s verifying "
                      "%s/%s), but the shipped bridge refuses a requester of its own vendor "
                      "(verify-cli exits 64) and the engine never declares a false requester. "
                      "Install %s to run this leg cross-vendor."
                      % (a, candidate, a, author_model, want))
    route["ok"] = True
    return route


def registry_second_model(vendor, home=None):
    """The operator registry's second model for `vendor` (`<vendor>_second` in
    ~/.rheoscope/frontier-models.json -- the same optional, outside-every-repo file models.js
    reads; models.js ignores this key). Read-only; None when absent."""
    v = normalize_vendor(vendor)
    if not v:
        return None
    path = os.path.join(home or os.path.expanduser("~"), ".rheoscope", "frontier-models.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    val = data.get("%s_second" % v) if isinstance(data, dict) else None
    return val.strip() if isinstance(val, str) and re.match(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,79}$",
                                                           val.strip()) else None


def detect_cli_vendors(env=None, which=None, isfile=None, listdir=None):
    """PRESENCE-only detection of the two verifier CLIs (no version check, no spawn): a pinned
    CODEX_BIN / CLAUDE_BIN that exists, the CLI on PATH, or a desktop app's bundled copy. The
    compile driver's pre-write probe is the version-checked authority and pins what it found,
    so on the driver path this sees exactly the pinned binary."""
    env = os.environ if env is None else env
    isfile = isfile or os.path.isfile
    listdir = listdir or os.listdir
    if which is None:
        import shutil
        which = shutil.which
    found = set()

    def bundled(root, *tail_dir, exe):
        if not root:
            return False
        base = os.path.join(root, *tail_dir)
        try:
            return any(isfile(os.path.join(base, d, exe)) for d in listdir(base))
        except OSError:
            return False

    exe = ".exe" if os.name == "nt" else ""
    standalone = os.path.join(env.get("LOCALAPPDATA") or "", "Programs", "OpenAI", "Codex",
                              "bin", "codex" + exe)
    if (env.get("CODEX_BIN") and isfile(env["CODEX_BIN"])) or which("codex") \
            or bundled(env.get("LOCALAPPDATA"), "OpenAI", "Codex", "bin", exe="codex" + exe) \
            or (env.get("LOCALAPPDATA") and isfile(standalone)):
        found.add("openai")
    if (env.get("CLAUDE_BIN") and isfile(env["CLAUDE_BIN"])) or which("claude") \
            or bundled(env.get("APPDATA"), "Claude", "claude-code", exe="claude" + exe):
        found.add("anthropic")
    return found


def self_test():
    total = failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    good = {"verifier_vendor": "openai", "verifier_model_id": "gpt-5.5",
            "absorb_vendor": "anthropic", "absorb_model_id": "claude-opus-4-8"}
    # F18 well-formedness
    ok, _ = verified_block_wellformed(good)
    case("F18 separate vendor+model_id fields pass", ok)
    combined = dict(good, verifier_vendor="openai/gpt-5.5")
    ok, _ = verified_block_wellformed(combined)
    case("F18 combined vendor/model string is rejected", not ok)
    missing = {k: v for k, v in good.items() if k != "verifier_model_id"}
    ok, _ = verified_block_wellformed(missing)
    case("F18 missing model_id field is rejected", not ok)

    # F17 provenance (schema layer -- fail-closed)
    case("F17 explicit invocation-metadata substrate accepted",
         substrate_derived_from_invocation(dict(good, substrate_source="invocation-metadata")))
    case("F17 orchestrator-declared substrate rejected (spoof)",
         not substrate_derived_from_invocation(dict(good, substrate_source="orchestrator-declared")))
    case("F17 ABSENT substrate_source rejected (fail-closed, no default)",
         not substrate_derived_from_invocation(good))

    # F17 write-path attestation gate (2026-07-05 design). AND-semantics:
    # BOTH the verifier channel (subprocess-runtime) AND the absorb channel
    # (dispatch-record) must be present -- a block attesting only one side
    # fails closed.
    verifier_only = {"channel": "subprocess-runtime",
                     "artifact": "receipts/verify/attest/x.attest.json",
                     "artifact_sha256": "deadbeef"}
    ok, _ = attestation_ok(verifier_only)
    case("attestation_ok: verifier channel ONLY (absorb_channel absent) -> FAIL "
         "(OR-shaped case retired, AND now required)", not ok)
    absorb_only = {"channel": "dispatch-record", "absorb_channel": "dispatch-record",
                  "artifact": "receipts/dispatch/manifest.json",
                  "artifact_sha256": "cafebabe"}
    ok, _ = attestation_ok(absorb_only)
    case("attestation_ok: absorb channel ONLY (verifier channel != "
         "subprocess-runtime) -> FAIL (OR-shaped case retired, AND now required)",
         not ok)
    good_attest = dict(verifier_only, absorb_channel="dispatch-record")
    ok, _ = attestation_ok(good_attest)
    case("attestation_ok: BOTH subprocess-runtime AND dispatch-record + "
         "artifact/sha -> PASS", ok)
    ok, _ = attestation_ok(dict(good_attest, channel="orchestrator-declared"))
    case("attestation_ok: unknown verifier channel -> FAIL (fail-closed)", not ok)
    ok, _ = attestation_ok(dict(good_attest, absorb_channel="orchestrator-declared"))
    case("attestation_ok: unknown absorb channel -> FAIL (fail-closed)", not ok)
    ok, _ = attestation_ok({})
    case("attestation_ok: empty block -> FAIL", not ok)
    ok, _ = attestation_ok(dict(good_attest, artifact=None))
    case("attestation_ok: missing artifact path -> FAIL", not ok)
    ok, _ = attestation_ok(dict(good_attest, artifact_sha256=None))
    case("attestation_ok: missing artifact sha -> FAIL", not ok)
    ok, _ = attestation_ok("not-a-dict")
    case("attestation_ok: non-mapping input -> FAIL", not ok)

    # F18 routine gate: model_id difference
    case("routine: different model_id -> PASS",
         substrate_gate_ok("anthropic", "claude-opus-4-8", "anthropic", "claude-haiku-4-5", ROUTINE))
    case("routine: SAME model_id -> FAIL",
         not substrate_gate_ok("anthropic", "claude-opus-4-8", "anthropic", "claude-opus-4-8", ROUTINE))

    # ANTI-PROMOTION PIN (incident 2026-07-29). A ROUTINE leg with the SAME vendor and a
    # DIFFERENT model_id is compliant -- for EVERY vendor, OpenAI-on-OpenAI included. A
    # session once "hardened" this into a vendor requirement to satisfy an operator's
    # surprise, editing the routing (verify-cli.js / verify-server.js / compile-backends /
    # compile-driver) rather than reading this policy. The damage was not a failed gate: it
    # silently converted an in-repo verification into a mandatory OUTBOUND evidence packet,
    # turning one compile into a queue of egress approvals (~90 min, 4 re-approvals, payload
    # growing 8 KB -> 264 KB). These two cases exist so that promoting the routine tier to a
    # vendor gate fails HERE first, loudly, in a self-test /doctor already runs -- instead of
    # being discovered as unexplained slowness. Raising a tier is a decision, not a fix.
    case("routine: same-vendor OpenAI, differing model_id -> PASS (anti-promotion pin)",
         substrate_gate_ok("openai", "codex-builder-1", "openai", "gpt-5", ROUTINE))
    case("routine: same-vendor OpenAI, SAME model_id -> FAIL (pin does not weaken the gate)",
         not substrate_gate_ok("openai", "gpt-5", "openai", "gpt-5", ROUTINE))

    # F18 migration / design gate: vendor difference REQUIRED
    case("migration audit: different vendor -> PASS",
         substrate_gate_ok("anthropic", "claude-opus-4-8", "openai", "gpt-5.5", MIGRATION))
    case("migration audit: SAME vendor (diff model) -> FAIL (firewall)",
         not substrate_gate_ok("anthropic", "claude-opus-4-8", "anthropic", "claude-haiku-4-5", MIGRATION))
    case("design gate: same vendor -> FAIL",
         not substrate_gate_ok("anthropic", "claude-opus-4-8", "anthropic", "claude-sonnet-5", DESIGN))
    case("design gate: different vendor -> PASS",
         substrate_gate_ok("anthropic", "claude-opus-4-8", "openai", "gpt-5.5", DESIGN))

    # v3.0-233: normalization -- one model however written; two different models never equal
    case("normalize: case/whitespace forms of one model are the SAME model",
         same_model("gpt-5.5", " GPT-5.5 ") and same_model("gpt-5.5", "gpt_5_5"))
    case("normalize: '4.8' and '4-8' and a provider prefix are one model",
         same_model("claude-opus-4.8", "anthropic/claude-opus-4-8"))
    case("normalize: a context tag and an undated name match a dated snapshot",
         same_model("claude-sonnet-4-5[1m]", "claude-sonnet-4-5-20250929")
         and same_model("claude-sonnet-4-5-latest", "claude-sonnet-4-5"))
    case("normalize: TWO DIFFERENT snapshot dates stay different models",
         not same_model("gpt-4o-2024-05-13", "gpt-4o-2024-08-06"))
    case("normalize: an alias and its full id are ONE model (fable == claude-fable-5-1)",
         same_model("fable", "claude-fable-5-1") and same_model("claude-opus-5-5", "opus")
         and is_alias_of("fable", "claude-fable-5-1"))
    case("normalize: different models never compare equal (versions, tiers, families)",
         not same_model("gpt-5", "gpt-5.5") and not same_model("claude-opus-4-8", "claude-opus-4-7")
         and not same_model("gpt-5.5", "gpt-5.5-mini") and not same_model("fable", "claude-opus-5-5")
         and not same_model("claude", "claude-opus-5-5") and not same_model("gpt", "gpt-5")
         and not same_model("", "") and not same_model(None, "gpt-5"))
    case("routine gate: two spellings of ONE model -> FAIL (the same-model trap)",
         not substrate_gate_ok("openai", "gpt-6.1-sol", "openai", "GPT-6.1-sol", ROUTINE)
         and not substrate_gate_ok("anthropic", "fable", "anthropic", "claude-fable-5-1", ROUTINE))
    case("routine gate: a missing model id -> FAIL (fail closed)",
         not substrate_gate_ok("openai", "", "anthropic", "claude-fable-5-1", ROUTINE)
         and not substrate_gate_ok("openai", "gpt-5", "anthropic", None, ROUTINE))
    case("migration gate: vendor aliases normalize (claude == anthropic) -> FAIL",
         not substrate_gate_ok("Anthropic", "claude-opus-4-8", "claude", "claude-fable-5", MIGRATION)
         and not substrate_gate_ok("", "x", "openai", "gpt-5", MIGRATION))

    # v3.0-233: the direction rule (choose_verifier) -- every case pure, nothing spawned
    both = {"openai", "anthropic"}
    r = choose_verifier("openai", "", ROUTINE, both)
    case("route: a routine leg whose author MODEL is missing is REFUSED (separation is by model)",
         not r["ok"] and "author's model is not recorded" in (r.get("refusal") or ""))
    r = choose_verifier("openai", "", MIGRATION, both)
    case("route: a vendor-tier leg needs only the author VENDOR (an unrecorded model is labelled, not guessed)",
         r["ok"] and r["direction"] == "anthropic")
    r = choose_verifier("unknown", "unknown", ROUTINE, both)
    case("route: an author recorded as the placeholder 'unknown' is REFUSED, never routed",
         not r["ok"] and "missing identity" in (r.get("reason") or r.get("error") or str(r)))
    r = choose_verifier("openai", "gpt-6.1-sol", ROUTINE, both)
    case("route: a Codex-authored artifact is routed to Claude (requester = openai)",
         r["ok"] and r["direction"] == "anthropic" and r["requester_vendor"] == "openai"
         and r["basis"] == "opposite-vendor" and r["model"] is None)
    r = choose_verifier("anthropic", "claude-opus-5-5", ROUTINE, both)
    case("route: a Claude-authored artifact is routed to OpenAI (requester = anthropic)",
         r["ok"] and r["direction"] == "openai" and r["requester_vendor"] == "anthropic")
    r = choose_verifier("anthropic", "claude-opus-5-5", ROUTINE, both, env_model="claude-sonnet-5")
    case("route: a VERIFY_MODEL naming the author's vendor is DROPPED on the opposite route",
         r["ok"] and r["direction"] == "openai" and r["drop_env_model"] is True)
    r = choose_verifier("openai", "gpt-6.1-sol", ROUTINE, {"openai"}, env_model="gpt-6-astra",
                        same_vendor_ok=True)
    case("route: routine, no Claude CLI, a different VERIFY_MODEL -> same vendor, that model",
         r["ok"] and r["direction"] == "openai" and r["model"] == "gpt-6-astra"
         and r["basis"] == "same-vendor-different-model" and r["model_source"] == "VERIFY_MODEL")
    r = choose_verifier("openai", "gpt-6.1-sol", ROUTINE, {"openai"}, second_model="gpt-6-astra",
                        same_vendor_ok=True)
    case("route: routine, no Claude CLI, the registry's second model -> same vendor",
         r["ok"] and r["model"] == "gpt-6-astra" and r["model_source"] == "registry openai_second")
    r = choose_verifier("openai", "gpt-6.1-sol", ROUTINE, {"openai"}, env_model="GPT-6.1-sol",
                        same_vendor_ok=True)
    case("route: routine same-vendor with the SAME model (differently written) -> REFUSED",
         not r["ok"] and "SAME model" in r["refusal"])
    r = choose_verifier("openai", "gpt-6.1-sol", ROUTINE, {"openai"})
    case("route: routine, no Claude CLI and no second model -> REFUSED naming what to install/set",
         not r["ok"] and "Claude Code CLI" in r["refusal"] and "VERIFY_MODEL" in r["refusal"])
    r = choose_verifier("openai", "gpt-6.1-sol", ROUTINE, {"openai"}, second_model="gpt-6-astra")
    case("route: the shipped bridge refuses same-vendor requesters, so that route REFUSES "
         "plainly (never a false requester)",
         not r["ok"] and r["basis"] == "same-vendor-different-model" and "bridge" in r["refusal"]
         and BRIDGE_SAME_VENDOR_LEGS is False)
    r = choose_verifier("openai", "gpt-6.1-sol", ROUTINE, set())
    case("route: no far-side CLI and no CLI at all -> REFUSED", not r["ok"] and r["refusal"])
    r = choose_verifier("openai", "gpt-6.1-sol", MIGRATION, {"openai"}, second_model="gpt-6-astra",
                        same_vendor_ok=True)
    case("route: vendor-tier leg never falls back to the same vendor -> REFUSED",
         not r["ok"] and "vendor-tier" in r["refusal"] and "Claude Code CLI" in r["refusal"])
    r = choose_verifier("anthropic", "claude-opus-5-5", DESIGN, both)
    case("route: vendor-tier leg -> the author's opposite vendor",
         r["ok"] and r["direction"] == "openai")
    case("route: no author vendor -> REFUSED (never defaulted from the session)",
         not choose_verifier(None, "x", ROUTINE, both)["ok"]
         and not choose_verifier("openai/gpt-5", "x", ROUTINE, both)["ok"])
    r = choose_verifier("xai", "grok-4.7", MIGRATION, {"openai"})
    case("route: a third-family author is verified by whichever bridge direction exists",
         r["ok"] and r["direction"] == "openai" and r["requester_vendor"] == "xai")
    import tempfile
    tmp_home = tempfile.mkdtemp(prefix="cs-selftest-home-")
    try:
        os.makedirs(os.path.join(tmp_home, ".rheoscope"), exist_ok=True)
        with open(os.path.join(tmp_home, ".rheoscope", "frontier-models.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"openai": "gpt-6.1-sol", "openai_second": "gpt-6-astra",
                       "anthropic_second": "bad id; rm"}, fh)
        case("registry: <vendor>_second is read; a malformed id is ignored",
             registry_second_model("openai", home=tmp_home) == "gpt-6-astra"
             and registry_second_model("anthropic", home=tmp_home) is None
             and registry_second_model("xai", home=tmp_home) is None)
    finally:
        import shutil
        shutil.rmtree(tmp_home, ignore_errors=True)
    found = detect_cli_vendors(env={"CLAUDE_BIN": "C:/x/claude.exe"}, which=lambda n: None,
                               isfile=lambda p: p == "C:/x/claude.exe", listdir=lambda p: [])
    case("detect: a pinned CLAUDE_BIN that exists -> anthropic only (presence, no spawn)",
         found == {"anthropic"})

    if failed:
        print("check-substrate (LLM-5): FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("check-substrate (LLM-5): PASS (%d/%d)" % (total, total))
    return 0


def main(argv):
    if "--self-test" in argv[1:] or len(argv) == 1:
        return self_test()
    print("usage: check-substrate.py --self-test")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
