#!/usr/bin/env python3
"""drill-planted-defects.py -- P3 sensor LLM-1: planted-defect efficacy floor
(test-plan sec.LLM-1, phase table row "LLM-1 (d1/d3 floor)").

Weekly-batch drill proving the compile loop's defect-catching machinery
actually catches planted defects -- the efficacy FLOOR the loop must clear
before P3 (RECONCILE+VERIFY) is allowed to advance, and weekly thereafter.

Design: take a healthy ABSORB answer (fixture), derive four DEFECTIVE
variants -- one per plant class below -- and run each through the REAL loop
(compile_v2.run, with a small per-plant absorb backend that emits exactly
the defective answer). Each plant must be CAUGHT:
  - a mechanical catch: compile_v2.validate_absorb_output raises
    ValidationError, so run() refuses BEFORE anything is journaled (the
    plant never reaches the journal at all -- chain length is asserted
    unchanged); or
  - a judgment catch: the defective answer is journaled as a
    PENDING_NOOP_CANDIDATE and a VERIFY leg over it returns a GENUINE
    semantic non-"confirm" verdict -- i.e. a well-formed verifier block
    with gate_ok=true, not a bridge crash and not a substrate-gated wrapper
    verdict (self-test uses a fixture verify backend; --live wires the real
    BridgeVerifyBackend, flag-gated, never exercised in --self-test, via a
    stamped dispatch manifest so the judgment leg actually runs).

  Any outcome where the bridge could not be consulted on its semantic
  merits -- a crash (bridge-error), or a wrapper verdict downgraded to
  "substrate-gated" by the F17 gates -- is NOT a catch and is NOT a miss:
  it is a distinct batch outcome "inconclusive-infrastructure" that FAILS
  the batch. A floor that can PASS on infrastructure failure is not a
  floor.

Also asserts the CONTROL: the healthy (unmodified) answer passes validation
with no false positive.

PLANT CLASSES (deterministic, one function per class):
  dropped-claim        remove a load-bearing claim from new_text that a
                        delta event carries. F13 fixture rule: the removed
                        claim must have NO implication path -- it must not
                        be inferable from any text remaining in the view or
                        siblings. The self-test greps the remaining text for
                        the removed claim's key terms and asserts absence.
  fabricated-support    a corpus_support support_lines entry that is NOT a
                        verbatim line of the cited artifact.
  manifest-omission     new_text changes a section the manifest does not
                        claim ("diff touches unclaimed section").
  deletion-floor-nibble shrinks the view just past the 30% CONTENT-1 floor
                        (and/or drops a heading).

Usage:
  drill-planted-defects.py --self-test
  drill-planted-defects.py --run --root DIR [--live]
      [--receipt PATH]  (default: ROOT/receipts/drill-planted-defects-<ts>.json)
Exit: 0 PASS (all plants caught + control passed, or self-test all green) |
      1 FAIL (a plant escaped, or the control false-positived) |
      2 INCONCLUSIVE-INFRASTRUCTURE (a --live judgment leg crashed or was
        substrate-gated instead of returning a genuine semantic verdict --
        the floor was not actually exercised; this is a distinct outcome
        from both PASS and FAIL, and FAILS the batch) or a usage/setup
        error (fixture missing, etc.) |
      3 refusal (bad args / preconditions).
"""

import argparse
import importlib.util
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _load(basename, alias):
    spec = importlib.util.spec_from_file_location(
        alias, os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


compile_v2 = _load("compile-v2.py", "compile_v2_pd")
core = compile_v2.core   # compile-core.py, already loaded by compile-v2

ValidationError = compile_v2.ValidationError

# Bridge-dir resolution for --live runs. BridgeVerifyBackend._bridge_dir()
# resolves relative to `self.repo` by default, which in this drill is the
# THROWAWAY --root fixture -- never the repo checkout, so it never has
# .claude/skills/bridge/ and a live run silently MODULE_NOT_FOUNDs (~2s,
# recorded as bridge-error). Fix: resolve relative to THIS FILE's own
# location (the repo checkout this drill lives in) by default, still
# honoring an explicit CROSS_VENDOR_BRIDGE_DIR override.
def _default_bridge_dir():
    # this file lives at <repo>/deploy/drill-planted-defects.py; the bridge
    # lives at <repo>/.claude/skills/bridge -- one level up from _HERE.
    return os.path.join(os.path.dirname(_HERE), ".claude", "skills", "bridge")


def _ensure_live_bridge_dir_env():
    """If CROSS_VENDOR_BRIDGE_DIR is not already set by the operator, point
    it at this repo checkout's bridge (not the disposable fixture root)."""
    if not os.environ.get("CROSS_VENDOR_BRIDGE_DIR"):
        os.environ["CROSS_VENDOR_BRIDGE_DIR"] = _default_bridge_dir()


def _live_verify_timeout_ms():
    """Bridge-leg timeout (ms) for the --live judgment leg. Sources the
    operator's standing VERIFY_TIMEOUT_MS knob and defaults to 540000 -- the
    live-ops bridge-leg headroom (a weekly gpt-5.5 leg can be rate-limited or
    slow). BridgeVerifyBackend otherwise hardcodes 180000 and passes it
    explicitly to verify-cli, which then overwrites the inherited env back to
    180000 -- so WITHOUT sourcing it here the documented VERIFY_TIMEOUT_MS
    export is inert on this path and the guard caps at ~220s. A non-integer
    value falls back to the 540000 default (never raises)."""
    raw = os.environ.get("VERIFY_TIMEOUT_MS")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return 540000


PLANT_CLASSES = ("dropped-claim", "fabricated-support", "manifest-omission",
                  "deletion-floor-nibble")


# ------------------------------------------------------------- fixture answer
def _healthy_answer(event_rel, event_body):
    """A healthy absorb answer: appends one new section carrying the event's
    load-bearing claim, with a verbatim corpus_support line pinned to the
    event artifact itself (the same pattern compile-v2's own fixtures use)."""
    claim_line = event_body.strip().splitlines()[-1]
    return {
        "new_text": None,  # filled in per-view by the caller (view_text-dependent)
        "manifest": [{"event": event_rel, "section": "Absorbed"}],
        "corpus_support": [{"artifact": event_rel,
                            "support_lines": [claim_line]}],
        "noops": [],
    }


def build_healthy_answer(view_text, event_rel, event_body):
    claim_line = event_body.strip().splitlines()[-1]
    new_text = (view_text if view_text.endswith("\n") or not view_text
                else view_text + "\n")
    new_text += "\n## Absorbed\n%s\n" % claim_line
    return {
        "new_text": new_text,
        "manifest": [{"event": event_rel, "section": "Absorbed"}],
        "corpus_support": [{"artifact": event_rel,
                            "artifact_sha256": _sha256_of_path_text(event_body),
                            "support_lines": [claim_line]}],
        "noops": [],
    }


def _sha256_of_path_text(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- plants
def plant_dropped_claim(healthy):
    """Remove the load-bearing claim line from new_text entirely (both the
    heading's content line and any trace of it), leaving the manifest
    pointed at a section that now carries nothing distinctive -- this is
    the deletion the VERIFY packet's diff must be able to see. F13: the
    fixture must ensure the claim has no implication path (asserted by the
    self-test via a grep over the remaining text for the claim's key
    terms)."""
    out = dict(healthy)
    lines = out["new_text"].splitlines()
    claim_line = healthy["corpus_support"][0]["support_lines"][0]
    kept = [ln for ln in lines if ln.strip() != claim_line.strip()]
    # keep the heading so this isn't ALSO a manifest-omission / deletion-floor
    # plant -- only the claim's content line is dropped.
    out["new_text"] = "\n".join(kept) + ("\n" if healthy["new_text"].endswith(
        "\n") else "")
    return out


def plant_fabricated_support(healthy):
    """support_lines entry that is NOT a verbatim line of the cited
    artifact -- mechanical catch (validate_absorb_output's F16 check)."""
    out = json.loads(json.dumps(healthy))  # deep copy
    out["corpus_support"][0]["support_lines"] = [
        "this line was never written in any artifact anywhere"]
    return out


def plant_manifest_omission(healthy):
    """new_text changes a section the manifest does not claim: append a
    SECOND, unclaimed section beyond the one the manifest names --
    mechanical catch ("diff touches unclaimed section")."""
    out = json.loads(json.dumps(healthy))
    out["new_text"] = out["new_text"] + "\n## Unclaimed Extra\nsurprise text\n"
    return out


def plant_deletion_floor_nibble(healthy, view_text):
    """Shrink the view just past the 30% CONTENT-1 floor by dropping most of
    the ORIGINAL body (keeping only enough to still look like a nonempty
    edit) -- mechanical catch (CONTENT-1 deletion floor: >30% shrink)."""
    out = json.loads(json.dumps(healthy))
    # new_text must be < 0.7 * len(old_text) to trip the floor. Build a
    # short replacement that still differs from old_text and carries the
    # section the manifest claims (so ONLY the deletion-floor check fires,
    # not manifest-vs-diff too) but drops the original heading(s).
    claim_line = healthy["corpus_support"][0]["support_lines"][0]
    out["new_text"] = "x\n"  # drops every original heading + shrinks hugely
    return out


PLANT_FUNCS = {
    "dropped-claim": lambda healthy, view_text: plant_dropped_claim(healthy),
    "fabricated-support": lambda healthy, view_text:
        plant_fabricated_support(healthy),
    "manifest-omission": lambda healthy, view_text:
        plant_manifest_omission(healthy),
    "deletion-floor-nibble": lambda healthy, view_text:
        plant_deletion_floor_nibble(healthy, view_text),
}

EXPECTED_MECHANICAL_CATCH = {
    "dropped-claim": False,   # judgment catch (VERIFY), not mechanical
    "fabricated-support": True,
    "manifest-omission": True,
    "deletion-floor-nibble": True,
}


# ---------------------------------------------------------- per-plant backend
class _FixedAnswerAbsorbBackend:
    """Hands back exactly ONE precomputed answer dict for the one view/event
    pair this drill's plan targets, regardless of what run() passes in --
    the drill controls the defective content directly rather than routing
    through a live agent."""

    def __init__(self, answer):
        self.answer = answer

    def absorb(self, view_rel, view_text, events):
        return self.answer


class _FixtureNonConfirmVerifyBackend:
    """Judgment-catch simulation for --self-test: NEVER confirms (every
    packet comes back 'rejected'). Real judgment legs in --live mode use
    compile_backends.BridgeVerifyBackend instead."""

    def verify(self, packet):
        return {"verdict": "rejected",
                "reason": "drill-planted-defects fixture: dropped-claim "
                          "plant must never be confirmed",
                "uncertainty": "confident", "verifier": {}}


class _FixtureConfirmVerifyBackend:
    """Control-path / non-plant verify backend: confirms everything -- used
    only to drain any PENDING_NOOP_CANDIDATE left by a run that is NOT the
    dropped-claim plant (there shouldn't be any, since the other three
    plants are mechanically refused pre-journal, and the control's answer
    carries no no-ops at all)."""

    def verify(self, packet):
        return {"verdict": "confirmed", "reason": "fixture",
                "uncertainty": "confident", "verifier": {}}


# --------------------------------------------------------------- journal len
def _journal_len(root):
    jd = core.journal_dir(root)
    if not os.path.isdir(jd):
        return 0
    return len([f for f in os.listdir(jd) if f.endswith(".json")])


def _genuine_semantic_verdict(verdict):
    """Is `verdict` (a raw dict returned by a verify backend's .verify(),
    or read back from a receipts/verify/noop-*.json artifact) a GENUINE
    semantic judgment from the bridge, as opposed to a crash or a
    substrate-gated wrapper verdict? A live dropped-claim catch REQUIRES
    this to be true for verdict in {"rejected", "revised"} -- anything
    else (bridge-error, substrate-gated, or a well-formed confirm) is not
    a genuine non-confirm catch. Returns (is_genuine, reason)."""
    if not isinstance(verdict, dict):
        return False, "verdict is not a mapping"
    v = str(verdict.get("verdict", "")).lower()
    if v not in ("rejected", "revised"):
        return False, "verdict is %r (not in the strict allowlist " \
            "{rejected, revised} -- treated as infrastructure/gate " \
            "outcome, not a semantic judgment)" % v
    substrate_block = verdict.get("substrate")
    if not isinstance(substrate_block, dict):
        return False, "no well-formed substrate/verifier block present"
    if substrate_block.get("gate_ok") is not True:
        return False, "substrate gate_ok is not true (%r)" % (
            substrate_block.get("gate_ok"),)
    verifier_block = verdict.get("verifier")
    if not isinstance(verifier_block, dict) or not verifier_block.get(
            "vendor") or not verifier_block.get("model"):
        return False, "verifier block is not well-formed (missing vendor/model)"
    return True, "genuine semantic verdict, gate_ok=true"


# ---------------------------------------------------------------- one plant
def _run_one_plant(root, plant_class, healthy, view_rel, view_text,
                    event_rel, live=False, verify_runner=None):
    """Runs ONE plant through the real loop. Returns
    {"class": plant_class, "caught": bool, "caught_by": str}.
    caught_by in {"mechanical-refusal", "verify-non-confirm",
    "verify-confirmed-ESCAPED", "no-refusal-ESCAPED",
    "inconclusive-infrastructure"}.

    A live judgment-catch ("verify-non-confirm") REQUIRES a GENUINE
    semantic verdict from the bridge (rejected/revised, well-formed
    verifier block, gate_ok=true) -- see _genuine_semantic_verdict(). Any
    crash (bridge-error) or substrate-gated wrapper verdict is classified
    "inconclusive-infrastructure" instead: this is NOT a catch and NOT an
    escape -- it is a distinct outcome that FAILS the batch (a floor that
    can pass on infrastructure failure is not a floor)."""
    defective = PLANT_FUNCS[plant_class](healthy, view_text)
    plan = {"items": [{"view": view_rel, "events": [event_rel],
                       "event_class": {event_rel: {"class": "t1",
                                                    "origin": "explicit"}}}]}
    before = _journal_len(root)
    backend = _FixedAnswerAbsorbBackend(defective)

    try:
        res = compile_v2.run(root, plan, backend, run_type="verify-drill")
    except ValidationError as e:
        after = _journal_len(root)
        if after != before:
            raise ValidationError(
                "plant %r was refused (%s) but journal length changed "
                "(%d -> %d) -- refusals must journal NOTHING"
                % (plant_class, e, before, after))
        return {"class": plant_class, "caught": True,
                "caught_by": "mechanical-refusal", "detail": str(e)}

    # not mechanically refused: it journaled. If this plant class was
    # expected to be a mechanical catch, that's an escape.
    if EXPECTED_MECHANICAL_CATCH[plant_class]:
        return {"class": plant_class, "caught": False,
                "caught_by": "no-refusal-ESCAPED",
                "detail": "expected a mechanical ValidationError; none "
                          "raised, plant was journaled at seq %d"
                          % res["seq"]}

    # judgment catch path (dropped-claim): must have produced a
    # PENDING_NOOP_CANDIDATE-carrying run with something to verify, OR (more
    # commonly for a dropped claim) simply landed as absorbed content that
    # is now MISSING the claim -- either way, the drill fires a VERIFY leg
    # whose packet embeds the diff against the healthy prior view and
    # asserts a non-confirm verdict.
    if live:
        cb = _load("compile-backends.py", "compile_backends_pd")
        manifest_path = os.path.join(root, "receipts", "dispatch-manifest.json")
        if not os.path.isfile(manifest_path):
            # belt-and-suspenders: _seed_fixture_repo already stamps one,
            # but a caller-supplied --root might not have (--run mode).
            _stamp_fixture_dispatch_manifest(root)
        _timeout_ms = _live_verify_timeout_ms()
        verify_backend = cb.BridgeVerifyBackend(
            root, dispatch_manifest_path=manifest_path,
            timeout_ms=_timeout_ms,
            runner=verify_runner) if verify_runner else \
            cb.BridgeVerifyBackend(root, dispatch_manifest_path=manifest_path,
                                   timeout_ms=_timeout_ms)
    else:
        verify_backend = _FixtureNonConfirmVerifyBackend()

    if res["noop_candidates"]:
        vres = compile_v2.verify_run(root, res["seq"], verify_backend,
                                     run_type="verify-drill-verify")
        # read back the raw verdict artifact this verify pass wrote (one
        # event -> one packet -> one artifact, e0) to classify genuinely
        # rather than trusting vres["confirmed"] alone -- a crash/gated
        # outcome must not silently count as a catch.
        raw_verdict = None
        art_path = os.path.join(
            root, "receipts", "verify",
            "noop-seq%d-e0.json" % res["seq"])
        if os.path.isfile(art_path):
            raw_verdict = json.load(open(art_path, encoding="utf-8"))
        if vres["confirmed"] > 0:
            return {"class": plant_class, "caught": False,
                    "caught_by": "verify-confirmed-ESCAPED",
                    "detail": "VERIFY confirmed a dropped-claim plant "
                              "(seq %d)" % res["seq"]}
        if live:
            genuine, reason = _genuine_semantic_verdict(raw_verdict)
            if not genuine:
                return {"class": plant_class, "caught": False,
                        "caught_by": "inconclusive-infrastructure",
                        "detail": "VERIFY did not confirm, but the verdict "
                                  "is not a genuine semantic judgment: %s "
                                  "(seq %d, verify seq %d, raw_verdict=%r)"
                                  % (reason, res["seq"], vres["seq"],
                                     raw_verdict)}
        return {"class": plant_class, "caught": True,
                "caught_by": "verify-non-confirm",
                "detail": "VERIFY refused to confirm (seq %d, verify seq %d)"
                          % (res["seq"], vres["seq"])}

    # dropped-claim landed as REAL absorbed content (no no-op candidate) --
    # judge it directly with the same verify backend over a packet embedding
    # the prior-view diff, per the property's packet-content requirement
    # (test-plan LLM-1: "VERIFY without the prior-view diff is structurally
    # blind to d1"). Build that packet here since verify_run() only ever
    # assembles packets for noop_candidates. The packet ALSO carries the
    # FULL RAW EVENT BODY (the source whose claim was dropped) alongside the
    # view diff -- without it, a verifier cannot semantically judge "zero
    # claims dropped" purely from a diff that (by construction, since the
    # claim line's own heading survives) shows nothing deleted from the
    # view. Exactly one 'CLAIM: ' line.
    prior_text = view_text
    post_path = os.path.join(root, view_rel.replace("/", os.sep))
    post_text = open(post_path, encoding="utf-8").read()
    event_path = os.path.join(root, event_rel.replace("/", os.sep))
    event_body = open(event_path, encoding="utf-8").read()
    packet = ("# DROPPED-CLAIM AUDIT PACKET (LLM-1 plant)\n\n"
              "CLAIM: event %s carries zero load-bearing claims dropped by "
              "this absorb.\n\n## FULL RAW EVENT BODY: %s\n%s\n\n"
              "## PRIOR VIEW BODY\n%s\n\n## POST VIEW BODY\n"
              "%s\n\n## DIFF (prior -> post)\n%s\n"
              % (event_rel, event_rel, event_body, prior_text, post_text,
                 _unified_diff(prior_text, post_text)))
    verdict = verify_backend.verify(packet)

    # Persist the FULL wrapped verdict JSON alongside the attest record for
    # the live direct-audit-packet path -- previously only the packet
    # (implicitly, via the verify call) and the attest record (written
    # inside BridgeVerifyBackend.verify() itself) were durable; the wrapped
    # verdict this drill actually classified against was never written to
    # disk, so a post-hoc audit of a live run couldn't see what the bridge
    # said. Written unconditionally on the live path (confirm, catch, or
    # inconclusive-infrastructure all count) so the receipt's plant detail
    # can always point at it.
    wrapped_verdict_rel = None
    if live:
        packet_stem = "seq%s-e0" % res["seq"]
        wrapped_verdict_rel = "receipts/verify/wrapped-%s.json" % packet_stem
        wrapped_verdict_abs = os.path.join(
            root, wrapped_verdict_rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(wrapped_verdict_abs), exist_ok=True)
        with open(wrapped_verdict_abs, "w", encoding="utf-8",
                  newline="\n") as fh:
            json.dump(verdict, fh, indent=1, sort_keys=True)

    confirm = str(verdict.get("verdict", "")).lower().startswith("confirm")
    if confirm:
        return {"class": plant_class, "caught": False,
                "caught_by": "verify-confirmed-ESCAPED",
                "detail": "VERIFY confirmed a dropped-claim plant (direct "
                          "audit packet, seq %d)" % res["seq"],
                "wrapped_verdict": wrapped_verdict_rel}
    if live:
        genuine, reason = _genuine_semantic_verdict(verdict)
        if not genuine:
            return {"class": plant_class, "caught": False,
                    "caught_by": "inconclusive-infrastructure",
                    "detail": "VERIFY did not confirm, but the verdict is "
                              "not a genuine semantic judgment: %s (direct "
                              "audit packet, seq %d, raw_verdict=%r)"
                              % (reason, res["seq"], verdict),
                    "wrapped_verdict": wrapped_verdict_rel}
    return {"class": plant_class, "caught": True,
            "caught_by": "verify-non-confirm",
            "detail": "VERIFY refused to confirm (direct audit packet, "
                      "seq %d)" % res["seq"],
            "wrapped_verdict": wrapped_verdict_rel}


def _unified_diff(a, b):
    import difflib
    return "\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(),
                                          lineterm=""))


# ------------------------------------------------------------------ control
def _run_control(root, healthy, view_rel, event_rel):
    """The healthy answer must pass validation with NO false positive."""
    plan = {"items": [{"view": view_rel, "events": [event_rel],
                       "event_class": {event_rel: {"class": "t1",
                                                    "origin": "explicit"}}}]}
    backend = _FixedAnswerAbsorbBackend(healthy)
    res = compile_v2.run(root, plan, backend, run_type="verify-drill")
    return res["seq"] is not None and res["rebuilds"] == 1


# --------------------------------------------------------------------- batch
def run_batch(root, live=False, verify_runner=None):
    """Runs the control + all 4 plants against a FRESH copy of the view for
    each case (each plant/control gets its own event + view pair so plants
    never contaminate each other's journal/content state)."""
    plants = []
    control_passed = False

    view_rel = "wiki/target.md"
    view_path = os.path.join(root, view_rel.replace("/", os.sep))
    base_view_text = open(view_path, encoding="utf-8").read()

    events_dir = os.path.join(root, "raw")

    # control uses its own event so its absorbed content doesn't collide
    # with any plant's expectations about the view's prior state
    control_event_rel = "raw/ctl.md"
    control_event_body = open(os.path.join(root, "raw", "ctl.md"),
                              encoding="utf-8").read()
    healthy_ctl = build_healthy_answer(base_view_text, control_event_rel,
                                       control_event_body)
    control_passed = _run_control(root, healthy_ctl, view_rel,
                                  control_event_rel)

    for plant_class in PLANT_CLASSES:
        erel = "raw/%s.md" % plant_class.replace("-", "_")
        ebody = open(os.path.join(root, erel.replace("/", os.sep)),
                     encoding="utf-8").read()
        # each plant absorbs against the ORIGINAL view text (not the
        # control's post-absorb text) -- re-seed the view file fresh so
        # every plant sees the same starting state and plants can't stack.
        with open(view_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(base_view_text)
        healthy = build_healthy_answer(base_view_text, erel, ebody)
        result = _run_one_plant(root, plant_class, healthy, view_rel,
                                base_view_text, erel, live=live,
                                verify_runner=verify_runner)
        plants.append(result)

    any_infra = any(p["caught_by"] == "inconclusive-infrastructure"
                    for p in plants)
    if any_infra:
        # a distinct outcome from both PASS and FAIL: the floor was never
        # actually exercised for at least one plant (bridge crash or
        # substrate-gated wrapper verdict, not a semantic judgment) -- this
        # must not be reported as a passing batch.
        verdict = "INCONCLUSIVE-INFRASTRUCTURE"
    elif control_passed and all(p["caught"] for p in plants):
        verdict = "PASS"
    else:
        verdict = "FAIL"
    receipt = {
        "type": "drill-planted-defects",
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "live": bool(live),
        "control_passed": control_passed,
        "plants": plants,
        "verdict": verdict,
    }
    return receipt


def write_receipt(root, receipt, receipt_path=None):
    if receipt_path is None:
        os.makedirs(os.path.join(root, "receipts"), exist_ok=True)
        receipt_path = os.path.join(
            root, "receipts", "drill-planted-defects-%s.json"
            % time.strftime("%Y-%m-%d"))
    with open(receipt_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(receipt, fh, indent=1, sort_keys=True)
    return receipt_path


# ------------------------------------------------------------------ fixture repo
def _seed_fixture_repo(root):
    import subprocess
    os.makedirs(os.path.join(root, "wiki"), exist_ok=True)
    os.makedirs(os.path.join(root, "raw"), exist_ok=True)
    with open(os.path.join(root, "wiki", "target.md"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write("# Target\n\n## Intro\nbaseline content that must survive "
                 "every plant's absorb unless the plant itself nibbles it.\n"
                 "This sentence about widgets is unrelated filler kept long "
                 "enough that a >30%% shrink is easy to detect.\nMore filler "
                 "so the deletion floor has real room to trip: alpha bravo "
                 "charlie delta echo foxtrot golf hotel india juliet kilo "
                 "lima mike november oscar papa quebec romeo sierra tango "
                 "uniform victor whiskey xray yankee zulu.\n")
    events = {
        "raw/ctl.md": "control fact: nothing planted here\n",
        "raw/dropped_claim.md":
            "load-bearing claim: the flux capacitor requires 1.21 "
            "gigawatts\n",
        "raw/fabricated_support.md": "fabricated support source fact\n",
        "raw/manifest_omission.md": "manifest omission source fact\n",
        "raw/deletion_floor_nibble.md": "deletion floor source fact\n",
    }
    for rel, body in events.items():
        p = os.path.join(root, rel.replace("/", os.sep))
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
    for args in (["init", "-q"], ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", root] + args, capture_output=True)
    subprocess.run(["git", "-C", root, "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "seed"],
                   capture_output=True)
    _stamp_fixture_dispatch_manifest(root)


def _stamp_fixture_dispatch_manifest(root, model="unknown", vendor="unknown"):
    """Create + stamp a minimal dispatch-manifest.json in the fixture repo
    (compile_backends.stamp_dispatch, the same pattern drill-workload-
    bench.py uses) so a --live judgment leg can construct BridgeVerifyBackend
    via dispatch_manifest_path and actually pass the F17 channel gate instead
    of being unconditionally substrate-gated (bare "unknown"/"unknown"
    literals -> _absorb_channel None -> gate always fails before the raw
    bridge verdict is ever consulted)."""
    cb = _load("compile-backends.py", "compile_backends_pd")
    receipts_dir = os.path.join(root, "receipts")
    os.makedirs(receipts_dir, exist_ok=True)
    manifest_path = os.path.join(receipts_dir, "dispatch-manifest.json")
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"packets": []}, fh, indent=1, sort_keys=True)
    cb.stamp_dispatch(manifest_path, model, vendor,
                      identity_source="attestation:drill-fixture")
    return manifest_path


# ---------------------------------------------------------------------- CLI
def main(argv):
    ap = argparse.ArgumentParser(prog="drill-planted-defects.py")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--root")
    ap.add_argument("--live", action="store_true",
                     help="wire compile_backends.BridgeVerifyBackend (via a "
                          "stamped dispatch manifest) for the judgment-catch "
                          "leg instead of the fixture non-confirm backend. "
                          "Bridge dir defaults to this repo checkout's "
                          ".claude/skills/bridge unless CROSS_VENDOR_BRIDGE_"
                          "DIR is set. Never used in --self-test.")
    ap.add_argument("--receipt")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    if args.run:
        if not args.root:
            print("refuse: --run requires --root", file=sys.stderr)
            return 3
        if args.live:
            _ensure_live_bridge_dir_env()
        receipt = run_batch(args.root, live=args.live)
        path = write_receipt(args.root, receipt, args.receipt)
        print("drill-planted-defects: verdict = %s" % receipt["verdict"])
        for p in receipt["plants"]:
            print("  %-24s caught=%-5s via %s"
                  % (p["class"], p["caught"], p["caught_by"]))
        print("drill-planted-defects: control_passed = %s"
              % receipt["control_passed"])
        print("drill-planted-defects: receipt -> %s" % path)
        if receipt["verdict"] == "PASS":
            return 0
        if receipt["verdict"] == "INCONCLUSIVE-INFRASTRUCTURE":
            return 2
        return 1

    print("usage: drill-planted-defects.py --self-test | --run --root DIR "
          "[--live] [--receipt PATH]", file=sys.stderr)
    return 3


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

    tmp = tempfile.mkdtemp(prefix="pd-selftest-")
    try:
        root = os.path.join(tmp, "repo")
        os.makedirs(root)
        _seed_fixture_repo(root)

        # -------------------------------------------------- F13 no-implication
        # the dropped-claim plant's removed text must not be inferable from
        # anything left in the view: assert the claim's key terms are gone
        # from the ORIGINAL view body too (never present as a paraphrase/
        # sibling fact), and that after the plant runs, the post-view body
        # (if any got written at all) still lacks them.
        base_view_text = open(os.path.join(root, "wiki", "target.md"),
                              encoding="utf-8").read()
        claim_body = open(os.path.join(root, "raw", "dropped_claim.md"),
                          encoding="utf-8").read()
        claim_line = claim_body.strip().splitlines()[-1]
        key_terms = ["flux capacitor", "1.21 gigawatts"]
        case("F13: dropped-claim's key terms are ABSENT from the original "
            "view (no implication path via a pre-existing sibling fact)",
             all(t not in base_view_text for t in key_terms))

        healthy = build_healthy_answer(base_view_text, "raw/dropped_claim.md",
                                       claim_body)
        defective = plant_dropped_claim(healthy)
        case("F13: dropped-claim plant actually removes the claim line "
            "from new_text",
             claim_line.strip() not in defective["new_text"])
        case("F13: dropped-claim plant's new_text still lacks the key terms "
            "(no implication path introduced by the plant itself)",
             all(t not in defective["new_text"] for t in key_terms))

        # ---------------------------------------------------- individual plants
        fab = plant_fabricated_support(healthy)
        case("fabricated-support plant: support_lines no longer verbatim "
            "in the cited artifact",
             fab["corpus_support"][0]["support_lines"][0] not in
             set(claim_body.splitlines()))

        manifest_bad = plant_manifest_omission(healthy)
        case("manifest-omission plant: new_text grew an unclaimed section",
             "Unclaimed Extra" in manifest_bad["new_text"]
             and not any("Unclaimed Extra" in
                        str(m.get("section")) for m in
                        manifest_bad["manifest"]))

        floor_bad = plant_deletion_floor_nibble(healthy, base_view_text)
        case("deletion-floor-nibble plant: shrinks past the 30%% floor",
             len(floor_bad["new_text"]) < 0.7 * len(base_view_text))

        # -------------------------------------------------------- full batch
        receipt = run_batch(root, live=False)
        case("batch: receipt type is drill-planted-defects",
             receipt["type"] == "drill-planted-defects")
        case("batch: control passed (healthy answer, no false positive)",
             receipt["control_passed"] is True)
        case("batch: exactly 4 plant results recorded",
             len(receipt["plants"]) == 4)
        case("batch: all 4 plant classes represented",
             sorted(p["class"] for p in receipt["plants"]) ==
             sorted(PLANT_CLASSES))
        case("batch: all 4 plants caught", all(p["caught"] for p in
                                               receipt["plants"]))
        case("batch: overall verdict PASS", receipt["verdict"] == "PASS")

        by_class = {p["class"]: p for p in receipt["plants"]}
        case("dropped-claim caught via verify-non-confirm (judgment catch)",
             by_class["dropped-claim"]["caught_by"] == "verify-non-confirm")
        case("fabricated-support caught via mechanical-refusal",
             by_class["fabricated-support"]["caught_by"]
             == "mechanical-refusal")
        case("manifest-omission caught via mechanical-refusal",
             by_class["manifest-omission"]["caught_by"]
             == "mechanical-refusal")
        case("deletion-floor-nibble caught via mechanical-refusal",
             by_class["deletion-floor-nibble"]["caught_by"]
             == "mechanical-refusal")

        # -------------------------------------------------------- receipt schema
        receipt_path = write_receipt(root, receipt)
        case("receipt written to disk", os.path.isfile(receipt_path))
        reloaded = json.load(open(receipt_path, encoding="utf-8"))
        case("receipt schema: top-level keys present",
             {"type", "created", "live", "control_passed", "plants",
              "verdict"} <= set(reloaded.keys()))
        case("receipt schema: each plant entry has class/caught/caught_by",
             all({"class", "caught", "caught_by"} <= set(p.keys())
                for p in reloaded["plants"]))

        # ----------------------------------- refused plants journal NOTHING
        # re-derive: for the 3 mechanically-caught plants, the journal grew
        # by exactly ONE record only (the control's) -- i.e. none of the
        # three refused plants added a record. Assert directly by counting
        # journal files vs. how many runs actually completed (control = 1,
        # dropped-claim = 1 [it DOES journal, since it's a judgment catch],
        # the other three = 0 each).
        jd = core.journal_dir(root)
        n_journaled = len([f for f in os.listdir(jd) if f.endswith(".json")])
        case("journal chain length: exactly 2 records "
            "(1 control + 1 dropped-claim judgment-catch; the 3 mechanical "
            "refusals journaled nothing)",
             n_journaled == 2)

        # -------------------------------------------------- CLI exit codes
        rc_bad_args = main(["--run"])
        case("CLI: --run without --root refuses with exit 3",
             rc_bad_args == 3)

        root2 = os.path.join(tmp, "repo2")
        os.makedirs(root2)
        _seed_fixture_repo(root2)
        receipt_path2 = os.path.join(tmp, "receipt2.json")
        rc_run = main(["--run", "--root", root2, "--receipt", receipt_path2])
        case("CLI: --run --root DIR on a fresh fixture exits 0 (PASS)",
             rc_run == 0)
        case("CLI: --run wrote the receipt to the given --receipt path",
             os.path.isfile(receipt_path2))

        # -------------------------------------------- stamped manifest present
        case("fixture repo: stamped dispatch-manifest.json present after seeding",
             os.path.isfile(os.path.join(root2, "receipts",
                                        "dispatch-manifest.json")))
        seeded_manifest = json.load(open(
            os.path.join(root2, "receipts", "dispatch-manifest.json"),
            encoding="utf-8"))
        _cb_stampcheck = _load("compile-backends.py", "compile_backends_pd")
        _stamp_ok, _stamp_reason = _cb_stampcheck._verify_dispatch_stamp(
            seeded_manifest)
        case("fixture repo: seeded manifest carries a well-formed dispatch "
            "stamp (%s)" % _stamp_reason, _stamp_ok is True)

        # --------------------------------- live bridge-leg timeout sourcing
        # regression: the --live judgment leg must get 540000ms headroom by
        # default (not the hardcoded 180000 that made VERIFY_TIMEOUT_MS inert
        # and capped the guard at ~220s), and must honor an explicit export.
        _saved_vt = os.environ.pop("VERIFY_TIMEOUT_MS", None)
        try:
            case("live timeout: defaults to 540000ms when VERIFY_TIMEOUT_MS unset",
                 _live_verify_timeout_ms() == 540000)
            os.environ["VERIFY_TIMEOUT_MS"] = "300000"
            case("live timeout: honors an explicit VERIFY_TIMEOUT_MS export",
                 _live_verify_timeout_ms() == 300000)
            os.environ["VERIFY_TIMEOUT_MS"] = "not-an-int"
            case("live timeout: non-integer VERIFY_TIMEOUT_MS falls back to 540000",
                 _live_verify_timeout_ms() == 540000)
        finally:
            os.environ.pop("VERIFY_TIMEOUT_MS", None)
            if _saved_vt is not None:
                os.environ["VERIFY_TIMEOUT_MS"] = _saved_vt

        # ------------------------------------- packet content: raw event body
        # rebuild the exact packet the direct-audit-packet path constructs
        # for dropped-claim and assert it carries the full raw event body
        # AND exactly one 'CLAIM: ' line.
        root3 = os.path.join(tmp, "repo3")
        os.makedirs(root3)
        _seed_fixture_repo(root3)
        view_rel3 = "wiki/target.md"
        view_path3 = os.path.join(root3, view_rel3.replace("/", os.sep))
        base_view_text3 = open(view_path3, encoding="utf-8").read()
        erel3 = "raw/dropped_claim.md"
        ebody3 = open(os.path.join(root3, erel3.replace("/", os.sep)),
                     encoding="utf-8").read()
        healthy3 = build_healthy_answer(base_view_text3, erel3, ebody3)

        result3 = _run_one_plant(root3, "dropped-claim", healthy3, view_rel3,
                                 base_view_text3, erel3, live=False,
                                 verify_runner=None)
        # the non-live path uses _FixtureNonConfirmVerifyBackend, which
        # doesn't capture -- re-derive the packet the same way _run_one_plant
        # does, via the direct-audit-packet branch, to assert its content
        # (dropped-claim always lands as real absorbed content, no no-op
        # candidate, given this fixture's plan).
        defective3 = plant_dropped_claim(healthy3)
        post_text3 = defective3["new_text"]
        packet3 = ("# DROPPED-CLAIM AUDIT PACKET (LLM-1 plant)\n\n"
                  "CLAIM: event %s carries zero load-bearing claims dropped "
                  "by this absorb.\n\n## FULL RAW EVENT BODY: %s\n%s\n\n"
                  "## PRIOR VIEW BODY\n%s\n\n## POST VIEW BODY\n%s\n\n"
                  "## DIFF (prior -> post)\n%s\n"
                  % (erel3, erel3, ebody3, base_view_text3, post_text3,
                     _unified_diff(base_view_text3, post_text3)))
        case("dropped-claim audit packet contains the FULL RAW EVENT BODY",
             ebody3.strip() in packet3)
        case("dropped-claim audit packet contains exactly one 'CLAIM: ' line",
             sum(1 for ln in packet3.splitlines()
                if ln.startswith("CLAIM: ")) == 1)
        case("dropped-claim result (non-live) still caught via "
            "verify-non-confirm", result3["caught"] is True
             and result3["caught_by"] == "verify-non-confirm")

        # ------------------------------------------- classification: crash
        root4 = os.path.join(tmp, "repo4")
        os.makedirs(root4)
        _seed_fixture_repo(root4)
        view_rel4 = "wiki/target.md"
        view_path4 = os.path.join(root4, view_rel4.replace("/", os.sep))
        base_view_text4 = open(view_path4, encoding="utf-8").read()
        erel4 = "raw/dropped_claim.md"
        ebody4 = open(os.path.join(root4, erel4.replace("/", os.sep)),
                     encoding="utf-8").read()
        healthy4 = build_healthy_answer(base_view_text4, erel4, ebody4)

        def _crash_verify_runner(args):
            return 1, "", "node: MODULE_NOT_FOUND"

        result_crash = _run_one_plant(
            root4, "dropped-claim", healthy4, view_rel4, base_view_text4,
            erel4, live=True, verify_runner=_crash_verify_runner)
        case("crash (bridge-error) -> inconclusive-infrastructure, NOT a catch",
             result_crash["caught"] is False
             and result_crash["caught_by"] == "inconclusive-infrastructure")

        # ----------------------------------- classification: substrate-gated
        # bare unknown/unknown literals with NO dispatch manifest at all ->
        # F17 channel gate always fails -> substrate-gated wrapper verdict,
        # even though the underlying (fixture) runner reports a clean exit.
        root5 = os.path.join(tmp, "repo5")
        os.makedirs(root5)
        _seed_fixture_repo(root5)
        # remove the seeded stamped manifest to force the ungated bare-
        # literal fallback path inside BridgeVerifyBackend (constructed here
        # directly, bypassing _run_one_plant's auto-stamp, to prove the
        # channel gate fires on its own merits).
        os.remove(os.path.join(root5, "receipts", "dispatch-manifest.json"))
        cb = _load("compile-backends.py", "compile_backends_pd")

        def _clean_confirmed_runner(args):
            return 0, json.dumps({
                "verdict": "confirmed", "reason": "looks fine",
                "uncertainty": "confident",
                "verifier": {"vendor": "openai", "model": "gpt-5.5"},
                "attestation": {"channel": "subprocess-runtime",
                               "argv_model": "gpt-5.5",
                               "runtime_model": "gpt-5.5"},
            }), "[cross-check] verifier=openai/gpt-5.5"

        gated_backend = cb.BridgeVerifyBackend(
            root5, "unknown", "unknown", runner=_clean_confirmed_runner)
        gated_verdict = gated_backend.verify(
            "CLAIM: test claim for substrate-gated fixture\n")
        case("bare unknown/unknown absorb identity (no dispatch manifest) "
            "-> substrate-gated wrapper verdict",
             gated_verdict["verdict"] == "substrate-gated")
        genuine_gated, _ = _genuine_semantic_verdict(gated_verdict)
        case("substrate-gated verdict is NOT classified as genuine",
             genuine_gated is False)

        # ------------------------------- classification: genuine reject/catch
        stamped_manifest_path = os.path.join(root5, "receipts",
                                             "dispatch-manifest.json")
        _stamp_fixture_dispatch_manifest(root5, model="claude-opus-4-8",
                                        vendor="anthropic")

        def _clean_rejected_runner(args):
            return 0, json.dumps({
                "verdict": "rejected", "reason": "claim not supported",
                "uncertainty": "confident",
                "verifier": {"vendor": "openai", "model": "gpt-5.5"},
                "attestation": {"channel": "subprocess-runtime",
                               "argv_model": "gpt-5.5",
                               "runtime_model": "gpt-5.5"},
            }), "[cross-check] verifier=openai/gpt-5.5"

        genuine_backend = cb.BridgeVerifyBackend(
            root5, dispatch_manifest_path=stamped_manifest_path,
            runner=_clean_rejected_runner)
        genuine_verdict = genuine_backend.verify(
            "CLAIM: test claim for genuine-reject fixture\n")
        case("stamped manifest + distinct absorb/verify models -> gate_ok true",
             genuine_verdict.get("substrate", {}).get("gate_ok") is True)
        genuine_ok, _ = _genuine_semantic_verdict(genuine_verdict)
        case("genuine reject with gate_ok=true IS classified as a genuine "
            "semantic verdict (a real catch)", genuine_ok is True)

        # ------------------------ STRICT ALLOWLIST: unknown verdict string
        # a cross-vendor round correctly flagged that accepting "anything
        # other than confirm/bridge-error/substrate-gated" as genuine would
        # wrongly count an unrecognized verdict string (a new/typo'd verdict
        # the bridge might emit, e.g. "maybe" or "error-ish") as a real
        # catch. The allowlist must be POSITIVE: verdict must be IN
        # {"rejected", "revised"} (case-insensitive) even with a
        # well-formed verifier block and gate_ok=true -- anything else is
        # inconclusive-infrastructure, not a catch.
        unknown_verdict = {
            "verdict": "maybe",
            "reason": "ambiguous",
            "uncertainty": "confident",
            "verifier": {"vendor": "openai", "model": "gpt-5.5"},
            "substrate": {"gate_ok": True},
        }
        genuine_unknown, reason_unknown = _genuine_semantic_verdict(
            unknown_verdict)
        case("unknown verdict string ('maybe') + gate_ok=true + well-formed "
            "verifier block is NOT classified as genuine (strict allowlist "
            "{rejected, revised} only)",
             genuine_unknown is False)
        case("unknown-verdict rejection reason cites the strict allowlist",
             "allowlist" in reason_unknown)

        view_text5 = open(os.path.join(root5, "wiki", "target.md"),
                         encoding="utf-8").read()
        event_body5 = open(os.path.join(root5, "raw", "dropped_claim.md"),
                          encoding="utf-8").read()
        healthy5 = build_healthy_answer(view_text5, "raw/dropped_claim.md",
                                        event_body5)
        result_genuine_catch = _run_one_plant(
            root5, "dropped-claim", healthy5, "wiki/target.md", view_text5,
            "raw/dropped_claim.md", live=True,
            verify_runner=_clean_rejected_runner)
        case("end-to-end: genuine reject via a real live leg -> caught via "
            "verify-non-confirm (real catch, not a gate artifact)",
             result_genuine_catch["caught"] is True
             and result_genuine_catch["caught_by"] == "verify-non-confirm")

        # ------------------------- WRAPPED-VERDICT PERSISTENCE (live path)
        # after a fixture live-path run, the full wrapped verdict JSON must
        # exist on disk (not just the packet/attest record) so a post-hoc
        # audit can see exactly what the bridge said.
        wv_rel = result_genuine_catch.get("wrapped_verdict")
        case("live dropped-claim run records a wrapped_verdict path",
             bool(wv_rel))
        wv_abs = os.path.join(root5, wv_rel.replace("/", os.sep)) \
            if wv_rel else None
        case("wrapped verdict file exists on disk",
             bool(wv_abs) and os.path.isfile(wv_abs))
        if wv_abs and os.path.isfile(wv_abs):
            wv_loaded = json.load(open(wv_abs, encoding="utf-8"))
            case("wrapped verdict file parses and carries a 'verdict' key",
                 "verdict" in wv_loaded)

        # -------------------------------- classification: genuine confirm (MISS)
        root6 = os.path.join(tmp, "repo6")
        os.makedirs(root6)
        _seed_fixture_repo(root6)
        healthy6 = build_healthy_answer(
            open(os.path.join(root6, "wiki", "target.md"),
                encoding="utf-8").read(), "raw/dropped_claim.md",
            open(os.path.join(root6, "raw", "dropped_claim.md"),
                encoding="utf-8").read())
        result_escaped = _run_one_plant(
            root6, "dropped-claim", healthy6, "wiki/target.md",
            open(os.path.join(root6, "wiki", "target.md"),
                encoding="utf-8").read(),
            "raw/dropped_claim.md", live=True,
            verify_runner=_clean_confirmed_runner)
        case("genuine confirm (gate_ok true, verdict=confirmed) -> plant "
            "ESCAPED (MISS), not caught",
             result_escaped["caught"] is False
             and result_escaped["caught_by"] == "verify-confirmed-ESCAPED")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d/%d cases passed" % (total - failed, total))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
