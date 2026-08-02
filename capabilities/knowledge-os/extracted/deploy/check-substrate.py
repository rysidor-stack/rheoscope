#!/usr/bin/env python3
"""check-substrate.py -- LLM-5 substrate-separation gate (test-plan tp:384) with F17/F18.

The `verified:` block records the absorb and verify substrates as SEPARATE `vendor` + `model_id`
fields (F18), derived from invocation metadata, NEVER orchestrator-supplied strings (F17). The
substrate-gate policy the tests check (spec §5):

  * ROUTINE T1 verify gates on MODEL_ID difference (absorb model_id != verify model_id) --
    hub-fired Claude-on-Claude is fine as long as the models differ.
  * MIGRATION-era content audits + DESIGN-gate verifications gate on VENDOR difference (the
    cross-substrate firewall) -- same vendor is not enough, even if model_ids differ.

Usage: check-substrate.py --self-test
Exit codes: 0 = policy holds on every fixture | 1 = a fixture slipped | 2 = INCONCLUSIVE.
"""

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


def substrate_gate_ok(absorb_vendor, absorb_model, verify_vendor, verify_model, gate_kind):
    """The F18 decision: does this verify leg satisfy the substrate separation its gate demands?"""
    if gate_kind == ROUTINE:
        return absorb_model != verify_model          # model_id difference suffices
    if gate_kind in (MIGRATION, DESIGN):
        return absorb_vendor != verify_vendor         # vendor difference REQUIRED (firewall)
    return False  # unknown gate kind -> fail closed


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
