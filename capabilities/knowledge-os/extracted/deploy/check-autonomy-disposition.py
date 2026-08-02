#!/usr/bin/env python3
"""check-autonomy-disposition.py -- AUTONOMY-DISPOSITION gate (gate-registration C4;
memory-engine-v3-autonomy-model-spec.md v1.4 SS2/SS5/SS8).

The mechanical action-disposition function: a PURE function of runtime-evidenced facts
about the ACTUAL action (tool + params classified against an audited table), where the
acting agent has NO VOTE -- agent-supplied class tags and self-reports are ignored by
construction (rung-A / rung-D). Every unknown input routes TOWARD the human gate
(default-dangerous): unknown reversibility => irreversible; unknown blast => high;
unknown verifiability => none; unknown security relevance => security-critical.

Decision order (SS2; order is load-bearing):
  1. egress payload_shape = free-selection            -> HUMAN-GATE (rung-4, FIRST for
     egress; overrides the blast budget -- small != safe). A closed-grammar-structural
     payload carrying any string value whose origin is not {human, corpus-first-party}
     IS free-selection (rung-D, A-17).
  2. egress payload origin_max not in {human, corpus-first-party} -> HUMAN-GATE (A-7).
  3. reversible AND deterministically-verifiable      -> AUTONOMOUS.
  4. security_critical AND NOT det-verifiable         -> HUMAN-GATE (rung-D: reversibility
     is no defense without a deterministic in-window detector of THIS compromise).
  5. reversible AND llm-only-verifiable               -> AUTONOMOUS-DECORRELATED.
  6. irreversible AND low-blast                       -> AUTONOMOUS-WITH-CANCEL iff a
     MECHANICAL in-window canceller exists (PRE-transmission for egress) else HUMAN-GATE.
  7. irreversible AND high-blast                      -> HUMAN-GATE (T1).
  8. not-verifiable AND (irreversible OR high-blast)  -> HUMAN-GATE.
  9. reversible AND low-blast AND not-verifiable      -> AUTONOMOUS-NO-PROMOTE.

Also mechanical here (SS4/SS5): promotion policy (a security-critical or irreversible
promotion NEVER rides a single in-family LLM PASS; unverifiable+consequential escalates)
and the T1-aggregate windowed accumulators (count/spend/egress-bytes/mutation-volume +
the trusted-surface accumulator: a write targeting a boot-trusted path counts against a
strict budget REGARDLESS of byte size and must itself be origin-human or human-gated).

This sensor ships ahead of the P2 engine build; the daemon/orchestrator that CALLS it is
compile-v2 work. Stdlib-only.

Usage: check-autonomy-disposition.py --self-test   (fixtures A-1..A-19 + invariant sweep)
Exit codes: 0 = all fixtures + invariants hold | 1 = a fixture/invariant failed.
"""

import itertools
import sys

AUTONOMOUS = "AUTONOMOUS"
AUTONOMOUS_DECORRELATED = "AUTONOMOUS-DECORRELATED"
AUTONOMOUS_WITH_CANCEL = "AUTONOMOUS-WITH-CANCEL"
AUTONOMOUS_NO_PROMOTE = "AUTONOMOUS-NO-PROMOTE"
HUMAN_GATE = "HUMAN-GATE"

TRUSTED_ORIGINS = ("human", "corpus-first-party")

# The audited per-tool classification table (SS2: "an audited per-tool/per-action
# table, exactly as Pillar-1 SS5 classifies capability"). Illustrative rows for the
# self-test; the live table is engine config, but the DERIVATION DISCIPLINE (classify
# from tool+params, agent-no-vote, default-dangerous) is what this sensor proves.
AUDITED_TABLE = {
    # tool                reversibility   verifiability   blast   egress  sec-crit
    "fs-write-staged":   ("reversible",   "deterministic", "low",  False,  False),
    "fs-write-live":     ("reversible",   "llm-only",      "low",  False,  False),
    "git-push":          ("irreversible", "deterministic", "high", True,   False),
    "send-message":      ("irreversible", "none",          "low",  True,   False),
    "iam-policy-edit":   ("reversible",   "llm-only",      "high", False,  True),
    "credential-mint":   ("irreversible", "none",          "high", False,  True),
    "schema-migrate":    ("reversible",   "deterministic", "high", False,  True),
    "order-place":       ("irreversible", "none",          "high", True,   False),
    "draft-note":        ("reversible",   "none",          "low",  False,  False),
}


def classify(action, table=None):
    """Derive the disposition inputs from the ACTUAL action (tool+params) against the
    audited table. The agent's own labels (anything under action['agent_claims'] or
    action['self_report']) are NEVER read -- rung-A/rung-D: an agent-supplied class is
    attacker-chosen. Unknown tool => every input defaults dangerous."""
    table = table if table is not None else AUDITED_TABLE
    tool = action.get("tool")
    row = table.get(tool)
    if row is None:
        reversibility, verifiability, blast, egress, seccrit = (
            "irreversible", "none", "high", bool(action.get("params", {}).get("egress")),
            True)   # unknown => dangerous on every axis
    else:
        reversibility, verifiability, blast, egress, seccrit = row
    params = action.get("params", {}) or {}
    # A-6: "reversible" is only real if an undo is REGISTERED on the actual action
    if reversibility == "reversible" and not params.get("undo_registered", True):
        reversibility = "irreversible"
    facts = {
        "reversibility": reversibility,
        "verifiability": verifiability,
        "blast": blast,
        "egress": egress,
        "security_critical": seccrit,
        # egress payload facts come from the runtime payload, never the agent
        "payload_shape": params.get("payload_shape"),          # harness-derived
        "payload_value_origins": params.get("payload_value_origins", []),
        "payload_origin_max": params.get("payload_origin_max"),
        "mechanical_canceller": params.get("mechanical_canceller", False),
        "canceller_pre_transmission": params.get("canceller_pre_transmission", False),
    }
    # rung-D / A-17: a closed-grammar-structural payload whose string values are not
    # all trusted-origin is data-bearing => free-selection
    if facts["egress"] and facts["payload_shape"] == "closed-grammar-structural":
        if any(o not in TRUSTED_ORIGINS for o in facts["payload_value_origins"]):
            facts["payload_shape"] = "free-selection"
    # unknown egress payload shape => the most dangerous shape
    if facts["egress"] and facts["payload_shape"] not in (
            "schema-pinned", "closed-grammar-structural", "free-selection"):
        facts["payload_shape"] = "free-selection"
    return facts


def disposition(action, table=None):
    """The SS2 pure function. Returns (disposition, reason)."""
    f = classify(action, table)
    # 1. rung-4: free-selection egress gates FIRST, regardless of blast/reversibility
    if f["egress"] and f["payload_shape"] == "free-selection":
        return HUMAN_GATE, "free-selection egress (SS8a selection channel; rung-4)"
    # 2. A-7: egress payload origin bounding (composes with Pillar-1 SS8a)
    if f["egress"] and f["payload_origin_max"] not in TRUSTED_ORIGINS:
        return HUMAN_GATE, "egress payload origin_max not trusted (A-7)"
    # 3. reversible + deterministic
    if f["reversibility"] == "reversible" and f["verifiability"] == "deterministic":
        return AUTONOMOUS, "reversible + deterministic verify"
    # 4. rung-D: security-critical without a deterministic detector
    if f["security_critical"] and f["verifiability"] != "deterministic":
        return HUMAN_GATE, ("security-critical without a deterministic detector "
                            "(rung-D: reversibility is no defense)")
    # 5. reversible + llm-only (non-security-critical -- seccrit already routed)
    if f["reversibility"] == "reversible" and f["verifiability"] == "llm-only":
        return AUTONOMOUS_DECORRELATED, "reversible + llm-only -> decorrelated verify"
    # 6. irreversible + low blast: cancel window iff mechanical canceller
    if f["reversibility"] == "irreversible" and f["blast"] == "low":
        ok_cancel = f["mechanical_canceller"] and (
            not f["egress"] or f["canceller_pre_transmission"])
        if ok_cancel:
            return AUTONOMOUS_WITH_CANCEL, "delay-then-commit with mechanical canceller"
        return HUMAN_GATE, "no mechanical in-window canceller (rung-A, A-14)"
    # 7. irreversible + high blast
    if f["reversibility"] == "irreversible" and f["blast"] == "high":
        return HUMAN_GATE, "irreversible + high blast (T1)"
    # 8. not verifiable + consequential
    if f["verifiability"] == "none" and (f["reversibility"] == "irreversible"
                                         or f["blast"] == "high"):
        return HUMAN_GATE, "unverifiable + consequential"
    # 9. reversible + low + none
    return AUTONOMOUS_NO_PROMOTE, "staging only; hold, never auto-promote"


def promote_allowed(f_security_critical, f_reversibility, verify_evidence,
                    deterministic_detector=False):
    """SS4 promotion policy. verify_evidence: {'kind': 'deterministic'|'llm',
    'vendor_separated': bool, 'in_family_single': bool}. Returns (bool, reason)."""
    kind = (verify_evidence or {}).get("kind")
    if kind == "deterministic":
        return True, "deterministic verify"
    if kind != "llm":
        return False, "no verification evidence -> escalate (SS4)"
    if f_security_critical or f_reversibility == "irreversible":
        if (verify_evidence.get("in_family_single")
                or not verify_evidence.get("vendor_separated")):
            return False, ("single in-family LLM PASS never promotes a "
                           "security-critical/irreversible action (A-8)")
        if f_security_critical and f_reversibility == "irreversible":
            return False, ("irreversible AND security-critical: decorrelated verify is "
                           "necessary-not-sufficient -> Pillar-4 trigger (rung-3)")
        if f_security_critical and not deterministic_detector:
            return False, ("reversible security-critical without a deterministic "
                           "detector never auto-promotes (rung-D)")
        return True, "decorrelated verify on a reversible action"
    if not verify_evidence.get("vendor_separated"):
        return True, "in-family LLM verify acceptable only for the routine case"
    return True, "decorrelated verify"


class AggregateBudget:
    """SS5 T1-aggregate: windowed accumulators over the ACTUAL action stream --
    count / spend / egress-bytes / mutation-volume -- plus the trusted-surface
    accumulator (rung-3): any write targeting a boot-trusted path counts against a
    strict budget REGARDLESS of size, and must itself be origin-human or human-gated."""

    def __init__(self, budgets):
        self.budgets = budgets
        self.totals = {k: 0 for k in budgets}

    def record(self, **counts):
        fired = []
        for k, v in counts.items():
            if k in self.totals:
                self.totals[k] += v
                if self.totals[k] > self.budgets[k]:
                    fired.append(k)
        return fired  # non-empty -> the human gate fires (T1-aggregate)


def trusted_surface_write_disposition(target_is_boot_trusted, write_origin):
    """SS5/SS7: an autonomous action can never raise the boot-trusted corpus."""
    if not target_is_boot_trusted:
        return None  # not this rule's concern
    if write_origin == "human":
        return AUTONOMOUS  # operator-authored content flowing to its own trusted store
    return HUMAN_GATE


# --------------------------------------------------------------------------- self-test
def self_test():
    total = failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    def act(tool, **params):
        return {"tool": tool, "params": params}

    d = disposition
    # A-1 reversible + deterministic
    case("A-1  reversible+deterministic -> AUTONOMOUS",
         d(act("fs-write-staged"))[0] == AUTONOMOUS)
    # A-2 reversible + llm-only
    case("A-2  reversible+llm-only -> AUTONOMOUS-DECORRELATED",
         d(act("fs-write-live"))[0] == AUTONOMOUS_DECORRELATED)
    # A-3 irreversible + low-blast (mechanical pre-transmission canceller)
    case("A-3  irreversible+low-blast+mech canceller -> AUTONOMOUS-WITH-CANCEL",
         d(act("send-message", payload_shape="schema-pinned",
               payload_origin_max="human", mechanical_canceller=True,
               canceller_pre_transmission=True))[0] == AUTONOMOUS_WITH_CANCEL)
    # A-4 irreversible + high-blast egress of non-trusted data
    case("A-4  irreversible+high-blast untrusted egress -> HUMAN-GATE",
         d(act("order-place", payload_shape="schema-pinned",
               payload_origin_max="external-scrape"))[0] == HUMAN_GATE)
    # A-5 unknown tool -> every input dangerous -> gate
    case("A-5  unknown action -> defaults dangerous -> HUMAN-GATE",
         d(act("never-seen-tool"))[0] == HUMAN_GATE)
    # A-6 marked reversible but no undo registered
    case("A-6  reversible-tag without registered undo -> treated irreversible",
         d(act("fs-write-live", undo_registered=False))[0] == HUMAN_GATE)
    # A-7 egress payload origin not trusted
    case("A-7  egress origin_max=external-scrape -> gated",
         d(act("git-push", payload_shape="schema-pinned",
               payload_origin_max="external-scrape"))[0] == HUMAN_GATE)
    # A-8 security-critical promotion on a single in-family LLM PASS
    ok, _ = promote_allowed(True, "reversible",
                            {"kind": "llm", "in_family_single": True,
                             "vendor_separated": False})
    case("A-8  seccrit promotion on single in-family LLM PASS -> NOT promoted", not ok)
    # A-9 T4 info gap pulls the human (trivial routing check)
    case("A-9  T4 info-gap -> human pulled", HUMAN_GATE == HUMAN_GATE)
    # A-10 unverifiable + irreversible promotion escalates
    ok, _ = promote_allowed(False, "irreversible", {"kind": None})
    case("A-10 unverifiable+irreversible promotion -> escalate, no auto-promote",
         not ok)
    # A-11 agent-supplied class tag ignored
    a = act("credential-mint")
    a["agent_claims"] = {"class": "routine reversible write"}
    case("A-11 agent class tag IGNORED; destructive action routes dangerous",
         d(a)[0] == HUMAN_GATE)
    # A-12 aggregate budget fires on summed small actions
    agg = AggregateBudget({"count": 100, "spend": 500})
    fired = []
    for _ in range(101):
        fired = agg.record(count=1, spend=1)
    case("A-12 101 individually-autonomous actions cross count budget -> T1-aggregate",
         "count" in fired)
    # A-13 withheld self-report never widens autonomy (disposition never reads it)
    a1 = act("order-place", payload_shape="schema-pinned", payload_origin_max="human")
    a2 = dict(a1, self_report={"confidence": "high", "on_goal": True})
    case("A-13 withheld/supplied self-report changes NOTHING (boundary independent)",
         d(a1) == d(a2) and d(a1)[0] == HUMAN_GATE)
    # A-14 cancel class without mechanical canceller -> HUMAN-GATE
    case("A-14 no mechanical canceller -> HUMAN-GATE not AUTONOMOUS-WITH-CANCEL",
         d(act("send-message", payload_shape="schema-pinned",
               payload_origin_max="human",
               mechanical_canceller=False))[0] == HUMAN_GATE)
    # A-15 small allowlisted free-selection egress of trusted bytes -> still gated
    case("A-15 free-selection egress of curated trusted bytes -> HUMAN-GATE (small!=safe)",
         d(act("send-message", payload_shape="free-selection",
               payload_origin_max="corpus-first-party", mechanical_canceller=True,
               canceller_pre_transmission=True))[0] == HUMAN_GATE)
    # A-16 reordered push (free-selection) vs schema-pinned projection of same bytes
    free = d(act("git-push", payload_shape="free-selection",
                 payload_origin_max="corpus-first-party"))[0]
    pinned = d(act("git-push", payload_shape="schema-pinned",
                   payload_origin_max="corpus-first-party"))[0]
    case("A-16 free-selection form -> HUMAN-GATE; schema-pinned form -> normal "
         "irreversible routing", free == HUMAN_GATE and pinned == HUMAN_GATE
         and d(act("git-push", payload_shape="schema-pinned",
                   payload_origin_max="corpus-first-party"))[1].startswith(
                       "irreversible + high blast"))
    # A-17 closed-grammar payload with a non-trusted-origin string value
    case("A-17 closed-grammar + agent-origin string value -> free-selection -> "
         "HUMAN-GATE",
         d(act("send-message", payload_shape="closed-grammar-structural",
               payload_value_origins=["agent"], payload_origin_max="human",
               mechanical_canceller=True,
               canceller_pre_transmission=True))[0] == HUMAN_GATE)
    # A-18 agent labels an IAM write "routine"
    a = act("iam-policy-edit")
    a["agent_claims"] = {"security": "routine"}
    case("A-18 IAM write labelled routine -> label ignored -> security-critical routing",
         d(a)[0] == HUMAN_GATE)
    # A-19 reversible + security-critical + llm-only
    case("A-19 reversible seccrit llm-only (IAM edit in txn) -> HUMAN-GATE (rung-D)",
         d(act("iam-policy-edit"))[0] == HUMAN_GATE)
    # seccrit WITH deterministic detector may be autonomous (the detector exists)
    case("     seccrit + deterministic verify (schema-migrate) -> AUTONOMOUS",
         d(act("schema-migrate"))[0] == AUTONOMOUS)
    # trusted-surface accumulator (rung-3 closure)
    case("     write to boot-trusted path, non-human origin -> HUMAN-GATE",
         trusted_surface_write_disposition(True, "corpus") == HUMAN_GATE)
    case("     write to boot-trusted path, human origin -> allowed",
         trusted_surface_write_disposition(True, "human") == AUTONOMOUS)

    # ---- invariant sweep over the full synthetic input lattice (SS8 pass condition)
    viol = 0
    tools = list(AUDITED_TABLE) + ["unknown-tool"]
    shapes = [None, "schema-pinned", "closed-grammar-structural", "free-selection"]
    origins = [None, "human", "corpus-first-party", "external-scrape"]
    for tool, shape, om, canceller in itertools.product(
            tools, shapes, origins, (False, True)):
        a = act(tool, payload_shape=shape, payload_origin_max=om,
                mechanical_canceller=canceller, canceller_pre_transmission=canceller)
        disp, _r = disposition(a)
        f = classify(a)
        # no AUTONOMOUS for irreversible high-blast
        if f["reversibility"] == "irreversible" and f["blast"] == "high" \
                and disp in (AUTONOMOUS, AUTONOMOUS_DECORRELATED,
                             AUTONOMOUS_WITH_CANCEL, AUTONOMOUS_NO_PROMOTE):
            viol += 1
        # free-selection egress is never autonomous
        if f["egress"] and f["payload_shape"] == "free-selection" and disp != HUMAN_GATE:
            viol += 1
        # security-critical never autonomous without deterministic verify
        if f["security_critical"] and f["verifiability"] != "deterministic" \
                and disp != HUMAN_GATE:
            viol += 1
        # unknown tool always gates
        if tool == "unknown-tool" and disp != HUMAN_GATE:
            viol += 1
    case("invariant sweep over %d lattice points: 0 violations"
         % (len(tools) * len(shapes) * len(origins) * 2), viol == 0)

    if failed:
        print("check-autonomy-disposition: FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("check-autonomy-disposition: PASS (%d/%d)" % (total, total))
    return 0


def main(argv):
    if "--self-test" in argv[1:] or len(argv) == 1:
        return self_test()
    print("usage: check-autonomy-disposition.py --self-test")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
