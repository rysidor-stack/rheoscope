#!/usr/bin/env python3
"""drill-trajectory-replay.py -- v3.0-91 trajectory-replay battery (DEV-ONLY).

Replays full verify-verdict TRAJECTORIES -- create -> reject -> revert /
set-aside -> update -> confirm, across many runs -- through a journal-only
lifecycle classifier, and asserts that EVERY leg lands in exactly ONE of the
eight states of engine/OPERATIONS.md section 7's verdict-lifecycle table
(v3.0-92), with its verify baseline derivable. The per-function batteries test
lifecycle transitions case-by-case; this drill is the guard for the class those
batteries keep missing (the v3.0-67/69/73/74 record): state-machine bugs that
only appear across a HISTORY.

The eight states, from the section-7 table (the lifecycle authority):
  PENDING            absorbed, no verify record covers the run yet
  VERIFIED           absorption_verified[] entry -- terminal
  BLOCKED            completed non-confirm, journaled disposition blocking
                     (incl. unclassified / legacy no-disposition / stamp-refused)
  RECORDED           completed non-confirm, journaled disposition recorded
                     (verifier demotion 2026-08-09)
  ADJUDICATED        absorption_adjudicated[] -- operator ruling, terminal
  RUN-REVERTED       driver_revert{reverted} -- rejection stays on the record
  RUN-AUTO-REVERTED  transport-shaped leg (judged by journaled verdict VALUE)
                     + driver_revert
  FAILED-REVERT      driver_revert{revert-failed}, NON-terminal, human-owned
                     (superseded if a later reverted record lands for the run)

Everything here reads the JOURNAL ALONE -- the verdict artifact is forensics,
never state (v3.0-74's lesson as a rule). Case (I) additionally pins
`compile-driver.verify_record_legs`'s own journal-first reading of the
discarded-approval shape (the v3.0-74 fix, landed by the lifecycle-completion
build): the formerly-invisible stampless artifact-confirmed leg reads
stamp-refused(confirmed) / stamped False / completed True, and the KNOWN-DEFECT
pin that used to hold this slot flipped to that expectation the day the fix
landed, exactly as its comment instructed.

PLACEMENT NOTE (deviation from the backlog entry, recorded 2026-08-09): the
v3.0-91 entry says "extend drill-golden-replay.py" AND "DEV-ONLY (rides the
make-release DEV_ONLY_DEPLOY exclusion)". The tree contradicts the premise:
drill-golden-replay.py SHIPS (make-release.py names it instance-reachable --
the MIG-1 adoption drill). Extending it would ship this battery, including the
KNOWN-DEFECT pin, which must never reach an instance. So the battery lives in
this dev-only SIBLING of the golden-replay drill instead, and joins
DEV_ONLY_DEPLOY in the same commit.

Usage:
  drill-trajectory-replay.py --root DIR [--json]   replay a real journal history
  drill-trajectory-replay.py --self-test           synthetic multi-run fixtures
Exit codes: 0 = every leg in exactly one state, baselines derivable (or no
  journal: NOTE, nothing to replay) | 1 = a classification violation, or a
  self-test failure.
"""

import importlib.util
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(basename, alias):
    spec = importlib.util.spec_from_file_location(alias, os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


driver = _load("compile-driver.py", "compile_driver_trajectory")

STATES = ("PENDING", "VERIFIED", "BLOCKED", "RECORDED", "ADJUDICATED",
          "RUN-REVERTED", "RUN-AUTO-REVERTED", "FAILED-REVERT")
_COMPLETED_LABELS = ("confirmed", "revised", "rejected")


def _label_completed(label):
    """Did the journaled verdict_label record a COMPLETED verdict? Mirrors
    classify_verdict's allowlist over the label the engine journaled at record
    time (incl. the substrate-gated(inner) form). None = legacy record, which
    is NOT transport-shaped -- it predates the label and blocked as written."""
    if label is None:
        return True
    if label in _COMPLETED_LABELS:
        return True
    if label.startswith("substrate-gated(") and label.endswith(")"):
        return label[len("substrate-gated("):-1] in _COMPLETED_LABELS
    return False


# --------------------------------------------------------------- context + legs
class Ctx(object):
    __slots__ = ("reverted", "revert_failed", "adjudicated", "covered_runs",
                 "absorbs_by_view", "confirmed_cover", "adjudicated_pins",
                 "baseline_resets")


def trajectory_context(recs):
    ctx = Ctx()
    ctx.reverted, ctx.revert_failed = set(), set()
    ctx.adjudicated = set()                    # (run seq, view)
    ctx.covered_runs = set()                   # run seqs a verify record covers
    ctx.absorbs_by_view = {}                   # view -> [run seq, ...]
    ctx.confirmed_cover = {}                   # (run seq, view) -> newest
    #                                            verify seq holding a stamp
    ctx.adjudicated_pins = {}                  # view -> (journal seq, run seq)
    #                                            newest PIN-BEARING (non-union)
    #                                            adjudication (v3.0-105: union
    #                                            entries carry union_event and
    #                                            pin no content -- the
    #                                            REQUIRED baseline skip)
    ctx.baseline_resets = {}                   # view -> newest reset journal
    #                                            seq (v3.0-106)
    for seq in sorted(recs):
        rec = recs[seq]
        dr = rec.get("driver_revert")
        if isinstance(dr, dict) and isinstance(dr.get("reverts_seq"), int):
            if dr.get("status") == "reverted":
                ctx.reverted.add(dr["reverts_seq"])
            elif dr.get("status") == "revert-failed":
                ctx.revert_failed.add(dr["reverts_seq"])
        for adj in rec.get("absorption_adjudicated") or []:
            if isinstance(adj.get("adjudicates_seq"), int):
                ctx.adjudicated.add((adj["adjudicates_seq"], adj.get("view")))
                if not adj.get("union_event"):
                    ctx.adjudicated_pins[adj.get("view")] = (
                        seq, adj["adjudicates_seq"])
        for br in rec.get("baseline_reset") or []:
            if br.get("view"):
                ctx.baseline_resets[br["view"]] = seq
        if isinstance(rec.get("verifies_seq"), int):
            ctx.covered_runs.add(rec["verifies_seq"])
            for av in rec.get("absorption_verified") or []:
                key = (rec["verifies_seq"], av.get("view"))
                ctx.confirmed_cover[key] = max(
                    ctx.confirmed_cover.get(key, -1), seq)
        for ab in rec.get("absorbed") or []:
            ctx.absorbs_by_view.setdefault(ab.get("view"), []).append(seq)
    return ctx


def collect_legs(recs, ctx):
    """Every verify leg the journal records, plus synthesized PENDING legs for
    absorbed views of runs no verify record covers (section 7's PENDING row).
    Leg: {run_seq, verify_seq, view, kind, label, disposition}."""
    legs = []
    for seq in sorted(recs):
        vrec = recs[seq]
        run_seq = vrec.get("verifies_seq")
        if isinstance(run_seq, int):
            for av in vrec.get("absorption_verified") or []:
                legs.append({"run_seq": run_seq, "verify_seq": seq,
                             "view": av.get("view", "?"), "kind": "verified",
                             "label": "confirmed", "disposition": None})
            for at in vrec.get("absorption_verify_attempts") or []:
                # Supersession (v3.0-74 design sec.2.3): a LATER covering
                # verify record holding an absorption_verified entry for the
                # same view supersedes this record's open reading for the
                # (run, view) pair -- the classifier takes the newest
                # covering record's leg per view, so the superseded attempt
                # is dropped (the pair is represented by the stamped leg).
                if ctx.confirmed_cover.get(
                        (run_seq, at.get("view", "?")), -1) > seq:
                    continue
                legs.append({"run_seq": run_seq, "verify_seq": seq,
                             "view": at.get("view", "?"), "kind": "attempt",
                             "label": at.get("verdict_label"),
                             "disposition": at.get("disposition")})
            seen_ev = set()
            for nc in vrec.get("noop_candidates") or []:
                if nc.get("verified") or not nc.get("artifact"):
                    continue
                ev = nc.get("event", "?")
                if ev in seen_ev:
                    continue
                seen_ev.add(ev)
                legs.append({"run_seq": run_seq, "verify_seq": seq,
                             "view": "union:%s" % ev, "kind": "union",
                             "label": nc.get("verdict_label"),
                             "disposition": nc.get("verify_disposition")})
        if str(recs[seq].get("run_type", "")).lower() == "compile" \
                and seq not in ctx.covered_runs:
            for ab in recs[seq].get("absorbed") or []:
                legs.append({"run_seq": seq, "verify_seq": None,
                             "view": ab.get("view", "?"), "kind": "pending",
                             "label": None, "disposition": None})
    return legs


# --------------------------------------------------------------- classification
def matching_states(leg, ctx):
    """Every section-7 state whose row-condition this leg satisfies, evaluated
    INDEPENDENTLY -- the exactly-one assertion is the drill's core claim, so
    the predicates must not be an if/elif chain that makes it a tautology."""
    n, v = leg["run_seq"], leg["view"]
    kind = leg["kind"]
    adj = (n, v) in ctx.adjudicated
    rev = n in ctx.reverted
    failed = n in ctx.revert_failed and n not in ctx.reverted
    transport = not _label_completed(leg["label"])
    nonconfirm = kind in ("attempt", "union")
    # Fail-closed exactly like _partition_nonconfirm_legs: ONLY the literal
    # "recorded" is recorded; absent, legacy, or any other value blocks.
    # (This drill's own case (J) caught the first draft treating an unknown
    # disposition as neither -- a leg matching zero states.)
    recorded = leg["disposition"] == "recorded"

    states = []
    if kind == "pending":
        states.append("PENDING")
    if kind == "verified" and not adj and not rev and not failed:
        states.append("VERIFIED")
    if nonconfirm and not recorded and not adj and not rev and not failed:
        states.append("BLOCKED")
    if nonconfirm and recorded and not adj and not rev and not failed:
        states.append("RECORDED")
    if kind != "pending" and adj:
        states.append("ADJUDICATED")
    if kind != "pending" and rev and not adj and not transport:
        states.append("RUN-REVERTED")
    if kind != "pending" and rev and not adj and transport:
        states.append("RUN-AUTO-REVERTED")
    if kind != "pending" and failed and not adj:
        states.append("FAILED-REVERT")
    return states


def derive_baseline(view, ctx, classified):
    """Section 7 / v3.0-67 ladder: the newest (by journal seq) of last
    machine-verified state, last operator-adjudicated state, and last
    operator baseline-reset (v3.0-106); else the view's real pre-absorb
    content -- reverted runs' ghosts excluded by construction (a VERIFIED
    classification already requires the run not be reverted). Union
    adjudication entries are EXCLUDED by construction (v3.0-105, the
    REQUIRED skip): ctx.adjudicated_pins carries only pin-bearing entries
    (no `union_event`), so a union set-aside can never advance any view's
    baseline here."""
    candidates = []
    ver = [leg["run_seq"] for leg, st in classified
           if leg["view"] == view and st == "VERIFIED"]
    if ver:
        ver_j = max([j for (n, vv), j in ctx.confirmed_cover.items()
                     if vv == view] or [-1])
        candidates.append((ver_j, "machine-verified", max(ver)))
    if view in ctx.adjudicated_pins:
        jseq, run_seq = ctx.adjudicated_pins[view]
        candidates.append((jseq, "adjudicated", run_seq))
    if view in ctx.baseline_resets:
        jseq = ctx.baseline_resets[view]
        candidates.append((jseq, "baseline-reset", jseq))
    if candidates:
        _j, kind, val = max(candidates)
        return (kind, val)
    return ("pre-absorb", None)


def replay(recs):
    """Classify every leg of a journal history. Returns (rows, violations,
    baselines): rows = [{leg fields + state}], violations = [str], baselines =
    {view: (basis, seq)}. The three standing assertions:
      1. exactly ONE section-7 state per leg (totality AND mutual exclusion);
      2. a baseline is derivable for every view the history touches;
      3. a view with only non-confirm legs (no VERIFIED, no ADJUDICATED) sits
         at the pre-absorb baseline -- a bare rejection advances nothing.
         (An operator baseline-reset record is the one sanctioned advance
         without a terminal leg -- v3.0-106; it is an operator ruling with
         journaled provenance, not a bare rejection.)"""
    ctx = trajectory_context(recs)
    legs = collect_legs(recs, ctx)
    rows, violations, classified = [], [], []
    for leg in legs:
        states = matching_states(leg, ctx)
        if len(states) != 1:
            violations.append(
                "leg run=%s view=%s kind=%s matches %d state(s) %s -- "
                "exactly one required"
                % (leg["run_seq"], leg["view"], leg["kind"], len(states),
                   states or "(none)"))
            state = states[0] if states else "(unclassifiable)"
        else:
            state = states[0]
        if state not in STATES and state != "(unclassifiable)":
            violations.append("leg run=%s view=%s landed outside the eight "
                              "states: %s" % (leg["run_seq"], leg["view"], state))
        classified.append((leg, state))
        rows.append(dict(leg, state=state))

    views = sorted({leg["view"] for leg in legs if leg["kind"] != "union"})
    baselines = {}
    for v in views:
        try:
            baselines[v] = derive_baseline(v, ctx, classified)
        except Exception as e:                                # pragma: no cover
            violations.append("baseline underivable for %s: %s" % (v, e))
            continue
        basis, _seq = baselines[v]
        has_terminal = any(st in ("VERIFIED", "ADJUDICATED")
                           for leg, st in classified if leg["view"] == v)
        if not has_terminal and basis not in ("pre-absorb", "baseline-reset"):
            violations.append(
                "view %s has no VERIFIED/ADJUDICATED leg but baseline reads "
                "%s -- a bare rejection advanced a baseline" % (v, basis))
        if basis == "baseline-reset" and v not in ctx.baseline_resets:
            violations.append(
                "view %s reads a baseline-reset basis with no baseline_reset "
                "record on the journal" % v)
    return rows, violations, baselines


# --------------------------------------------------------------- real-history mode
def run_trajectories(root, as_json=False):
    repo = os.path.abspath(root)
    jd = os.path.join(repo, "receipts", "journal")
    if not os.path.isdir(jd):
        print("NOTE: no journal at %s -- nothing to replay (this drill reads "
              "a live instance's receipts/journal/)" % jd)
        return 0
    recs = driver.load_journal(repo)
    rows, violations, baselines = replay(recs)
    if as_json:
        print(json.dumps({"legs": rows, "violations": violations,
                          "baselines": {k: list(v) for k, v in baselines.items()},
                          "pass": not violations}, indent=2, sort_keys=True))
        return 1 if violations else 0
    print("== v3.0-91 trajectory replay (journal-only) ==")
    census = {}
    for r in rows:
        census[r["state"]] = census.get(r["state"], 0) + 1
    print("  legs: %d   states: %s" % (len(rows), json.dumps(census, sort_keys=True)))
    for r in rows:
        print("  run %-4s %-40s %-18s label=%s" % (r["run_seq"], r["view"],
                                                   r["state"], r["label"]))
    print("  baselines: %d view(s), all derivable" % len(baselines))
    if violations:
        for v in violations:
            print("  VIOLATION  %s" % v)
        print("RESULT: FAIL -- %d violation(s)" % len(violations))
        return 1
    print("RESULT: PASS -- every leg in exactly one of the eight states; "
          "every baseline derivable")
    return 0


# --------------------------------------------------------------- self-test
def self_test():                                              # noqa: C901
    import shutil
    import tempfile

    total = failed = 0

    def case(name, ok, detail=""):
        nonlocal total, failed
        total += 1
        print("  %s %s%s" % ("ok " if ok else "XX ", name,
                             ("  << %s" % detail) if (not ok and detail) else ""))
        if not ok:
            failed += 1

    def vrec(verify_seq, run_seq, verified=(), attempts=(), noops=(),
             start="2026-08-09"):
        return verify_seq, {"verifies_seq": run_seq,
                            "run_window": {"start": start},
                            "absorption_verified": list(verified),
                            "absorption_verify_attempts": list(attempts),
                            "noop_candidates": list(noops)}

    def runrec(seq, views):
        return seq, {"run_type": "compile",
                     "absorbed": [{"view": v} for v in views]}

    def attempt(view, label="rejected", classes=("contradiction",),
                disposition="blocking", legacy=False):
        a = {"view": view, "artifact": "receipts/verify/x.json",
             "reason": "fixture reason sentence"}
        if not legacy:
            a["verdict_label"] = label
            a["reason_classes"] = list(classes)
            a["disposition"] = disposition
        return a

    def states_of(recs):
        rows, violations, baselines = replay(recs)
        by_leg = {}
        for r in rows:
            by_leg[(r["run_seq"], r["view"])] = r["state"]
        return by_leg, violations, baselines

    # --- (A) golden trajectory: create -> reject -> revert -> re-ride -> confirm
    recs = dict([
        runrec(10, ["wiki/a.md"]),
        vrec(11, 10, attempts=[attempt("wiki/a.md")]),
    ])
    st, viol, base = states_of(recs)
    case("(A) rejected leg is BLOCKED", st[(10, "wiki/a.md")] == "BLOCKED", str(st))
    case("(A) blocked-only view sits at the pre-absorb baseline "
         "(a bare rejection advances nothing)",
         base["wiki/a.md"] == ("pre-absorb", None), str(base))
    case("(A) no violations at the blocked stage", not viol, "; ".join(viol))

    recs[12] = {"driver_revert": {"status": "reverted", "reverts_seq": 10}}
    st, viol, base = states_of(recs)
    case("(A) after --revert the leg is RUN-REVERTED (rejection stays on the "
         "record)", st[(10, "wiki/a.md")] == "RUN-REVERTED", str(st))

    recs.update([runrec(13, ["wiki/a.md"]),
                 vrec(14, 13, verified=[{"view": "wiki/a.md"}])])
    st, viol, base = states_of(recs)
    case("(A) the re-ridden leg is VERIFIED", st[(13, "wiki/a.md")] == "VERIFIED",
         str(st))
    case("(A) full trajectory: zero violations, every leg exactly one state",
         not viol, "; ".join(viol))
    case("(A) baseline advances to machine-verified at the re-ride",
         base["wiki/a.md"] == ("machine-verified", 13), str(base))

    # --- (B) set-aside: reject -> operator ruling -> ADJUDICATED
    recs = dict([
        runrec(20, ["wiki/b.md"]),
        vrec(21, 20, attempts=[attempt("wiki/b.md")]),
        (22, {"absorption_adjudicated": [
            {"adjudicates_seq": 20, "view": "wiki/b.md",
             "ruling": "operator ruling, verbatim"}]}),
    ])
    st, viol, base = states_of(recs)
    case("(B) set-aside leg is ADJUDICATED", st[(20, "wiki/b.md")] == "ADJUDICATED",
         str(st))
    case("(B) baseline advances as adjudicated, never machine-verified",
         base["wiki/b.md"] == ("adjudicated", 20), str(base))
    case("(B) zero violations", not viol, "; ".join(viol))

    # --- (C) demotion: recorded leg beside a confirmed sibling; then accepted
    recs = dict([
        runrec(30, ["wiki/c.md", "wiki/d.md"]),
        vrec(31, 30,
             verified=[{"view": "wiki/d.md"}],
             attempts=[attempt("wiki/c.md", classes=("scope-omission",),
                               disposition="recorded")]),
    ])
    st, viol, base = states_of(recs)
    case("(C) recorded-class leg is RECORDED", st[(30, "wiki/c.md")] == "RECORDED",
         str(st))
    case("(C) confirmed sibling on the same run is VERIFIED",
         st[(30, "wiki/d.md")] == "VERIFIED", str(st))
    case("(C) recorded leg advances no baseline",
         base["wiki/c.md"] == ("pre-absorb", None), str(base))
    recs[32] = {"absorption_adjudicated": [
        {"adjudicates_seq": 30, "view": "wiki/c.md", "ruling": "accept"}]}
    st, viol, base = states_of(recs)
    case("(C) accepted recorded leg becomes ADJUDICATED",
         st[(30, "wiki/c.md")] == "ADJUDICATED", str(st))
    case("(C) zero violations across the demotion trajectory", not viol,
         "; ".join(viol))

    # --- (D) mixed run: one blocking leg + one recorded sibling, both stated
    recs = dict([
        runrec(40, ["wiki/e.md", "wiki/f.md"]),
        vrec(41, 40, attempts=[
            attempt("wiki/e.md", classes=("fabrication",)),
            attempt("wiki/f.md", classes=("scope-omission",),
                    disposition="recorded")]),
    ])
    st, viol, _ = states_of(recs)
    case("(D) mixed run: blocking leg is BLOCKED",
         st[(40, "wiki/e.md")] == "BLOCKED", str(st))
    case("(D) mixed run: recorded sibling is RECORDED, not swallowed",
         st[(40, "wiki/f.md")] == "RECORDED", str(st))
    case("(D) zero violations on the mixed run", not viol, "; ".join(viol))

    # --- (E) transport: bridge-error leg + auto-revert -> RUN-AUTO-REVERTED
    recs = dict([
        runrec(50, ["wiki/g.md"]),
        vrec(51, 50, attempts=[attempt("wiki/g.md", label="bridge-error",
                                       classes=("unclassified",))]),
        (52, {"driver_revert": {"status": "reverted", "reverts_seq": 50}}),
    ])
    st, viol, _ = states_of(recs)
    case("(E) transport-shaped reverted leg is RUN-AUTO-REVERTED (judged by "
         "verdict VALUE)", st[(50, "wiki/g.md")] == "RUN-AUTO-REVERTED", str(st))
    case("(E) zero violations", not viol, "; ".join(viol))

    # --- (F) failed revert -> FAILED-REVERT; a later successful revert supersedes
    recs = dict([
        runrec(60, ["wiki/h.md"]),
        vrec(61, 60, attempts=[attempt("wiki/h.md")]),
        (62, {"driver_revert": {"status": "revert-failed", "reverts_seq": 60}}),
    ])
    st, viol, _ = states_of(recs)
    case("(F) revert-failed run's leg is FAILED-REVERT (non-terminal, "
         "human-owned)", st[(60, "wiki/h.md")] == "FAILED-REVERT", str(st))
    recs[63] = {"driver_revert": {"status": "reverted", "reverts_seq": 60}}
    st, viol, _ = states_of(recs)
    case("(F) a later successful revert supersedes: leg is RUN-REVERTED",
         st[(60, "wiki/h.md")] == "RUN-REVERTED", str(st))
    case("(F) zero violations either side of the recovery", not viol,
         "; ".join(viol))

    # --- (G) PENDING: absorbed run, no verify record yet
    recs = dict([runrec(70, ["wiki/i.md"])])
    st, viol, base = states_of(recs)
    case("(G) uncovered absorbed view is PENDING", st[(70, "wiki/i.md")] == "PENDING",
         str(st))
    case("(G) pending view's baseline is pre-absorb",
         base["wiki/i.md"] == ("pre-absorb", None), str(base))

    # --- (H) v3.0-73 collision case: rejected run, later ordinary work on the
    # same view, revert REFUSED (writes nothing) -> correct FORWARD instead.
    # The journal shows: rejection at 80 (stays on the record, no revert
    # record), the forward correction absorbing the same view at 82, confirmed.
    recs = dict([
        runrec(80, ["wiki/j.md"]),
        vrec(81, 80, attempts=[attempt("wiki/j.md")]),
        runrec(82, ["wiki/j.md"]),
        vrec(83, 82, verified=[{"view": "wiki/j.md"}]),
    ])
    st, viol, base = states_of(recs)
    case("(H) v3.0-73 collision: the refused-revert rejection stays BLOCKED "
         "on the record", st[(80, "wiki/j.md")] == "BLOCKED", str(st))
    case("(H) v3.0-73 collision: the forward correction is VERIFIED "
         "independently", st[(82, "wiki/j.md")] == "VERIFIED", str(st))
    case("(H) v3.0-73 collision: baseline is the forward correction's",
         base["wiki/j.md"] == ("machine-verified", 82), str(base))
    case("(H) v3.0-73 collision: exactly one state each, zero violations",
         not viol, "; ".join(viol))

    # --- (I) v3.0-74 discarded-approval shape: legacy attempts-leg whose
    # ARTIFACT says confirmed but the journal recorded no stamp.
    base_i = tempfile.mkdtemp(prefix="traj-v3074-")
    try:
        art_rel = "receipts/verify/absorb-seq90-v1.json"
        art_path = os.path.join(base_i, *art_rel.split("/"))
        os.makedirs(os.path.dirname(art_path))
        with open(art_path, "w", encoding="utf-8") as fh:
            json.dump({"verdict": "confirmed",
                       "reason": "faithful (stamp refused engine-side)"}, fh)
        legacy_leg = {"view": "wiki/k.md", "artifact": art_rel,
                      "reason": "confirmed but stamp refused"}
        recs = dict([
            runrec(90, ["wiki/k.md"]),
            vrec(91, 90, attempts=[legacy_leg]),
        ])
        st, viol, base = states_of(recs)
        case("(I) v3.0-74 journal-only reading: the discarded-approval leg is "
             "BLOCKED (attempts entry = no stamp; the artifact is forensics)",
             st[(90, "wiki/k.md")] == "BLOCKED", str(st))
        case("(I) v3.0-74 journal-only reading: no baseline advanced",
             base["wiki/k.md"] == ("pre-absorb", None), str(base))
        # v3.0-74 FIXED (the lifecycle-completion build): verify_record_legs
        # judges by the RECORD -- an attempts entry means no stamp, so the
        # discarded-approval leg reads stamp-refused(confirmed), stamped
        # False, completed True (a real verdict exists; the journal holds no
        # stamp). The artifact supplied only the completion axis and the
        # forensic label, never confirmation.
        vr = driver.verify_record_legs(base_i, recs[91])
        legs_i = vr["legs"]
        case("(I) v3.0-74 fixed: verify_record_legs reads the JOURNAL -- the "
             "stampless artifact-confirmed leg is stamp-refused(confirmed), "
             "not stamped, completed",
             len(legs_i) == 1
             and legs_i[0]["label"] == "stamp-refused(confirmed)"
             and legs_i[0]["stamped"] is False
             and legs_i[0]["completed"] is True, str(legs_i))
        case("(I) v3.0-74 fixed: the leg is NOT incomplete (terminality "
             "invariance -- completion is the transport axis, untouched)",
             vr["incomplete"] == [], str(vr["incomplete"]))
    finally:
        shutil.rmtree(base_i, ignore_errors=True)

    # --- (I2) supersession (v3.0-74 design sec.2.3): an older covering
    # record's open attempt is superseded by a later covering record's stamp
    # for the same (run, view) -- the narrow --reverify recovery's shape. The
    # classifier takes the newest covering record's leg; the pair reads
    # VERIFIED, the stale open reading is gone, nothing else moves.
    recs = dict([
        runrec(85, ["wiki/n.md", "wiki/o.md"]),
        vrec(86, 85, attempts=[attempt("wiki/n.md", legacy=True),
                               attempt("wiki/o.md", legacy=True)]),
        vrec(87, 85, verified=[{"view": "wiki/n.md"}],
             attempts=[attempt("wiki/o.md", legacy=True)]),
    ])
    st, viol, base = states_of(recs)
    case("(I2) supersession: the re-earned stamp wins the (run, view) pair",
         st[(85, "wiki/n.md")] == "VERIFIED", str(st))
    case("(I2) supersession: the sibling with no later stamp stays BLOCKED "
         "(nothing else moves)", st[(85, "wiki/o.md")] == "BLOCKED", str(st))
    case("(I2) supersession: baseline advances to machine-verified",
         base["wiki/n.md"] == ("machine-verified", 85), str(base))
    case("(I2) supersession: zero violations", not viol, "; ".join(viol))

    # --- (I3) union-leg discarded shape: an artifact-confirmed no-op union
    # leg with `verified` unset is the same discarded-approval shape --
    # verify_record_legs applies the identical journal-first relabel.
    base_i3 = tempfile.mkdtemp(prefix="traj-v3074u-")
    try:
        art_rel = "receipts/verify/noop-seq92-e0.json"
        art_path = os.path.join(base_i3, *art_rel.split("/"))
        os.makedirs(os.path.dirname(art_path))
        with open(art_path, "w", encoding="utf-8") as fh:
            json.dump({"verdict": "confirmed",
                       "reason": "union holds (flip never landed)"}, fh)
        urec = {"verifies_seq": 92,
                "noop_candidates": [{"event": "raw/x.md", "view": "wiki/p.md",
                                     "artifact": art_rel}]}
        vr = driver.verify_record_legs(base_i3, urec)
        legs_u = vr["legs"]
        case("(I3) union discarded shape: stamp-refused(confirmed), not "
             "stamped, completed",
             len(legs_u) == 1
             and legs_u[0]["label"] == "stamp-refused(confirmed)"
             and legs_u[0]["stamped"] is False
             and legs_u[0]["completed"] is True, str(legs_u))
        case("(I3) union discarded shape: not incomplete (completion axis "
             "untouched)", vr["incomplete"] == [], str(vr["incomplete"]))
    finally:
        shutil.rmtree(base_i3, ignore_errors=True)

    # --- (L) v3.0-105: union set-aside classifies ADJUDICATED with ZERO
    # classifier edits -- the design's load-bearing claim, pinned. The
    # adjudication record's view is the pseudo-view string `union:<event>`,
    # exactly the key collect_legs already builds, so matching_states needs
    # no change; and the pin-less record advances NO view baseline (the
    # REQUIRED derive_baseline skip, keyed on union_event).
    noop = {"event": "raw/u.md", "view": "wiki/q.md",
            "artifact": "receipts/verify/noop-seq100-e0.json",
            "verdict_label": "rejected",
            "reason_classes": ["contradiction"]}
    recs = dict([
        runrec(100, ["wiki/q.md"]),
        vrec(101, 100, verified=[{"view": "wiki/q.md"}], noops=[noop]),
    ])
    st, viol, base = states_of(recs)
    case("(L) an open union leg is BLOCKED beside its confirmed sibling",
         st[(100, "union:raw/u.md")] == "BLOCKED"
         and st[(100, "wiki/q.md")] == "VERIFIED", str(st))
    recs[102] = {"absorption_adjudicated": [
        {"adjudicates_seq": 100, "view": "union:raw/u.md",
         "union_event": "raw/u.md", "union_views": ["wiki/q.md"],
         "ruling": "operator ruling, verbatim",
         "adjudicated_by": "operator"}]}
    st, viol, base = states_of(recs)
    case("(L) union set-aside classifies ADJUDICATED with zero classifier "
         "edits (the pseudo-view record matches the existing "
         "(adjudicates_seq, view) key)",
         st[(100, "union:raw/u.md")] == "ADJUDICATED", str(st))
    case("(L) exactly-one-state invariance holds under the union "
         "adjudication record", not viol, "; ".join(viol))
    case("(L) the union adjudication advances NO view baseline (pin-less "
         "by design; the REQUIRED skip) and no union pseudo-view enters "
         "the baseline census",
         base["wiki/q.md"] == ("machine-verified", 100)
         and not any(v.startswith("union:") for v in base), str(base))
    # the same verb covers a RECORDED union leg (verify_disposition:
    # recorded), operator-paced -- the sec.7 row's own action.
    noop_rec = dict(noop, event="raw/u2.md",
                    artifact="receipts/verify/noop-seq103-e0.json",
                    verify_disposition="recorded")
    recs2 = dict([
        runrec(103, ["wiki/q2.md"]),
        vrec(104, 103, verified=[{"view": "wiki/q2.md"}],
             noops=[noop_rec]),
    ])
    st, viol, _ = states_of(recs2)
    case("(L) a union leg journaled verify_disposition: recorded is "
         "RECORDED", st[(103, "union:raw/u2.md")] == "RECORDED", str(st))
    recs2[105] = {"absorption_adjudicated": [
        {"adjudicates_seq": 103, "view": "union:raw/u2.md",
         "union_event": "raw/u2.md", "ruling": "accept",
         "adjudicated_by": "operator"}]}
    st, viol, _ = states_of(recs2)
    case("(L) RECORDED union leg -> ADJUDICATED through the same verb, "
         "zero violations",
         st[(103, "union:raw/u2.md")] == "ADJUDICATED" and not viol,
         "%s %s" % (st, viol))

    # --- (M) v3.0-106: a baseline-reset record contributes ZERO legs, moves
    # ZERO leg states, and lands in the baseline census as
    # ("baseline-reset", journal seq).
    recs = dict([
        runrec(120, ["wiki/r.md"]),
        vrec(121, 120, attempts=[attempt("wiki/r.md")]),
    ])
    rows_before, _v, _b = replay(recs)
    recs[122] = {"run_type": "baseline-reset",
                 "baseline_reset": [
                     {"view": "wiki/r.md", "at": "2026-08-16T13:00:00",
                      "refresh_commit": "f" * 40, "view_sha256": "a" * 64,
                      "provenance": "fixture photograph",
                      "ruling": "operator ruling, verbatim",
                      "reset_by": "operator"}],
                 "refused": []}
    rows_after, viol, base = replay(recs)
    case("(M) a baseline-reset record contributes ZERO legs",
         len(rows_after) == len(rows_before),
         "%d -> %d" % (len(rows_before), len(rows_after)))
    st = {(r["run_seq"], r["view"]): r["state"] for r in rows_after}
    case("(M) the reset moves NO leg state -- the open rejection stays "
         "BLOCKED (a reset adjudicates nothing)",
         st[(120, "wiki/r.md")] == "BLOCKED", str(st))
    case("(M) the baseline census names the reset rung "
         "(('baseline-reset', journal seq))",
         base["wiki/r.md"] == ("baseline-reset", 122), str(base))
    case("(M) exactly-one-state invariance holds under the baseline-reset "
         "record (and the reset basis is not flagged as a bare-rejection "
         "advance)", not viol, "; ".join(viol))
    # ladder ordering: a post-reset confirm supersedes the reset rung
    recs.update([runrec(123, ["wiki/r.md"]),
                 vrec(124, 123, verified=[{"view": "wiki/r.md"}])])
    _rows, viol, base = replay(recs)
    case("(M) a post-reset confirm supersedes the reset rung "
         "(newest by journal seq)",
         base["wiki/r.md"] == ("machine-verified", 123) and not viol,
         str(base))
    # and the reset rung itself wins over an OLDER adjudication (the fork's
    # memory-engine composition shape, decision 0.4: doors compose freely)
    recs3 = dict([
        runrec(130, ["wiki/s.md"]),
        vrec(131, 130, attempts=[attempt("wiki/s.md")]),
        (132, {"absorption_adjudicated": [
            {"adjudicates_seq": 130, "view": "wiki/s.md",
             "ruling": "wave set-aside", "adjudicated_by": "operator"}]}),
        (133, {"run_type": "baseline-reset",
               "baseline_reset": [
                   {"view": "wiki/s.md", "at": "t",
                    "refresh_commit": "f" * 40, "view_sha256": "a" * 64,
                    "provenance": "photograph", "ruling": "r",
                    "reset_by": "operator"}],
               "refused": []}),
    ])
    st, viol, base = states_of(recs3)
    case("(M) composition: the leg stays ADJUDICATED while the newer reset "
         "rung carries the baseline (disjoint records, disjoint readers)",
         st[(130, "wiki/s.md")] == "ADJUDICATED"
         and base["wiki/s.md"] == ("baseline-reset", 133)
         and not viol, "%s %s %s" % (st, base, viol))

    # --- (J) exactly-one assertion has teeth: an inconsistent history (a run
    # both reverted and carrying a live recorded disposition is fine -- revert
    # wins; but a leg matching ZERO states must be reported, never dropped).
    # A pending-kind leg can never be manufactured to match two states by
    # construction, so probe totality instead: an attempts leg with a bogus
    # disposition value still lands in exactly one state (blocking fail-closed).
    recs = dict([
        runrec(95, ["wiki/l.md"]),
        vrec(96, 95, attempts=[attempt("wiki/l.md", disposition="banana")]),
    ])
    st, viol, _ = states_of(recs)
    case("(J) unknown disposition value fails closed to BLOCKED, exactly one "
         "state", st[(95, "wiki/l.md")] == "BLOCKED" and not viol,
         "%s %s" % (st, viol))

    # --- (K) real-mode entry point degrades honestly with no journal
    empty = tempfile.mkdtemp(prefix="traj-empty-")
    try:
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = run_trajectories(empty)
        case("(K) no journal: NOTE + exit 0, never a crash or a false PASS",
             rc == 0 and "nothing to replay" in buf.getvalue(), buf.getvalue())
    finally:
        shutil.rmtree(empty, ignore_errors=True)

    if failed:
        print("drill-trajectory-replay self-test: FAIL (%d/%d)"
              % (total - failed, total))
        return 1
    print("drill-trajectory-replay self-test: PASS (%d/%d)" % (total, total))
    return 0


# --------------------------------------------------------------- CLI
def main(argv):
    args = argv[1:]
    if "--self-test" in args:
        return self_test()
    root = "."
    if "--root" in args:
        i = args.index("--root")
        if i + 1 < len(args):
            root = args[i + 1]
    return run_trajectories(root, as_json="--json" in args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
