#!/usr/bin/env python3
"""drill-workload-bench.py -- OPS-4 P2-WORKLOAD-ACCEPTANCE drill
(test-plan tp:300-308).

P2 acceptance is not a single-event smoke test -- it is a replayed 30-day
workload cost benchmark. This drill drives the REAL engine seams
(compile-backends.py's emit_packets / FileHandoffAbsorbBackend /
BridgeVerifyBackend / run_guarded / verify_run_guarded) exactly the way the
session-dispatched agent pattern does; compile-v2.py / compile-core.py are
never modified or reimplemented here.

Three phases, matching the dispatch pattern (prepare work -> a human/agent
loop fills in answers -> run+verify+measure -> recompute a verdict later
from the receipt alone):

  --prepare  groups bench-plan.json's sliced events into one compile-plan
             item per view (all events for that view in one item), emits
             work packets via compile_backends.emit_packets, and writes
             bench-manifest.json recording which candidates are t1
             (mandatory verify) vs t3 (1-in-5 sampled verify, deterministic
             by sorted index).

  --run      requires every answer JSON to already exist (refuses by name
             otherwise); times the absorb run and the verify pass; verify
             only actually fires the bridge for t1 candidates and the t3
             sample -- unsampled t3 candidates get a synthetic
             "skipped-sampling" verdict so they never falsely confirm and
             never fire the bridge. OPS-4 gate clause (c) ("every T1 event
             gets a full VERIFY pass") is read PER EVENT, not per
             (event,view) candidate: a T1 event that appears as a
             no-op candidate in multiple plan views fires the bridge only
             ONCE, for the first such candidate encountered in
             verify_run's iteration order; every later candidate for that
             same already-fired T1 event gets a synthetic
             "skipped-duplicate-t1-event" verdict (also never starting
             with "confirm") and stays PENDING. T3 sampling is unaffected
             by this -- the deterministic 1-in-5 sample is already defined
             per event. Sums token cost from an optional --absorb-costs
             file (never invents a number: missing file => token axis is
             INCONCLUSIVE). Emits a receipt JSON, including a per-event
             verify map recording, for every T1 event, whether the bridge
             fired for it and how many candidates it had.

  --verdict  recomputes PASS/FAIL/INCONCLUSIVE from a receipt + baseline
             file alone -- idempotent, no state, no re-run.

bench-plan.json shape:
  {"events": [{"event": "<rel>", "class": "t1"|"t3", "views": ["<rel>", ...]}],
   "window": {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"},
   "deviations": ["<free-text note>", ...]}

costs.json shape (written by the orchestrator, summed not invented here):
  {"packets": {"<answer rel>": <tokens>}, "baseline": {"<label>": <tokens>}}

baseline.json shape: {"p95_wall": <seconds>, "token_cost": <int>}

Gate (verbatim from the test plan): PASS iff p95_wall <= 2 * baseline
p95_wall AND token_cost <= 1.5 * baseline token_cost. Missing token_cost
data makes the token axis INCONCLUSIVE (never a silent PASS) but does not
by itself fail the wall-clock axis.

Usage:
  drill-workload-bench.py --self-test
  drill-workload-bench.py --prepare --root DIR --plan bench-plan.json
      --staging DIR
  drill-workload-bench.py --run --root DIR --plan bench-plan.json
      --staging DIR [--absorb-costs costs.json]
      [--absorb-vendor V] [--absorb-model M] [--baseline baseline.json]
      [--authorization-path FILE --authorization-quote TEXT]
      [--chunk-size N]  (default 12; splits plan items into sequential
       compile+verify chunks of at most N items each, so one bench run
       reproduces the real many-session 30-day replay shape instead of
       tripping compile-v2's certified 15-rebuilds/run circuit breaker,
       which is a per-run cap by design and is never changed here. T1
       per-event dedup and the T3 sample persist across chunks on one
       shared verify backend instance -- one benchmark = one leg budget)
      (authorization is the dispatch_guard HUMAN-GATE ack required whenever
       the 30-day slice's events carry a trusted origin per
       compile-backends.dispatch_guard -- see that module's docstring;
       events whose payload origin is untrusted/unknown are hard-refused by
       the guard regardless of authorization, by design. FILE must be a real
       operator artifact satisfying the authorization-artifact CLASS
       CONSTRAINT added 2026-07-06 -- repo-relative path
       deploy/evidence/operator-*.md, POSIX-normalized and case-sensitive;
       see compile-backends.py's AUTHORIZATION_ARTIFACT_CLASS_GLOB and
       dispatch_guard's docstring)
      OPS-1 recovery discipline: --run refuses (exit 3) at the very start
      if `git -C ROOT status --porcelain` is non-empty, naming the dirty
      paths -- a dirty tree means a prior crashed/refused run left
      forward-crash residue that must be reset before a new chunked run
      can safely start.
  drill-workload-bench.py --verdict --receipt RECEIPT.json
      --baseline baseline.json
Exit: 0 PASS (or self-test all green) | 1 FAIL / a case failed |
      2 INCONCLUSIVE (token axis unknown; reported, not a hard failure) |
      3 usage/refusal (missing answers, bad args).
"""

import importlib.util
import json
import os
import statistics
import subprocess
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


compile_v2 = _load("compile-v2.py", "compile_v2_wb")
cb = _load("compile-backends.py", "compile_backends_wb")

ValidationError = compile_v2.ValidationError

WALL_MULTIPLE = 2.0
TOKEN_MULTIPLE = 1.5
SAMPLE_STRIDE = 5   # 1-in-5 deterministic T3 verify sample

DEFAULT_CHUNK_SIZE = 12   # OPS-4/OPS-1: compile-v2's certified circuit
                          # breaker (CIRCUIT_BREAKER_REBUILDS = 15
                          # rebuilds/run) is a per-run cap by design and
                          # must NOT be changed here. The real 30-day
                          # workload was many compile sessions, not one; a
                          # bench that replays the whole slice as a single
                          # compile run can legitimately trip the breaker
                          # on item count alone. Splitting plan["items"]
                          # into sequential chunks of at most
                          # DEFAULT_CHUNK_SIZE items (configurable via
                          # --chunk-size) reproduces that multi-session
                          # shape: each chunk is its own run_guarded +
                          # verify_run_guarded pair, while the T1
                          # per-event dedup (_SamplingVerifyBackend.t1_fired
                          # / t1_verify_map) and the T3 sample are held on
                          # ONE sampling_backend instance reused across all
                          # chunks, so "one benchmark = one leg budget"
                          # still holds end to end.

ROUTING_RULES_TEXT = (
    "Route T1/correction/lock/informed_by events to top-tier verify; "
    "T3 events sampled 1-in-5 for VERIFY per the weekly drill cadence.")


# ------------------------------------------------------------- plan grouping
def _group_plan_items(bench_plan):
    """bench-plan.json's events (one event, N views each) -> one compile-plan
    item PER VIEW, with all events that name that view folded together.
    event_class per event: {"class": t1|t3, "origin": "judgment"} (F6
    conservative default -- the bench never claims "explicit" origin for
    itself, so no absorbed no-op silently escapes the PENDING_NOOP_CANDIDATE
    discipline)."""
    by_view = {}
    order = []
    for ev in bench_plan["events"]:
        erel = ev["event"]
        ecls = ev["class"]
        if ecls not in ("t1", "t3"):
            raise ValidationError(
                "bench-plan event %r has unknown class %r (want t1/t3)"
                % (erel, ecls))
        for vrel in ev["views"]:
            if vrel not in by_view:
                by_view[vrel] = {"view": vrel, "events": [],
                                  "event_class": {}}
                order.append(vrel)
            item = by_view[vrel]
            if erel not in item["events"]:
                item["events"].append(erel)
            item["event_class"][erel] = {"class": ecls, "origin": "judgment"}
    return {"items": [by_view[v] for v in order]}


def _t1_t3_events(bench_plan):
    t1, t3 = set(), set()
    for ev in bench_plan["events"]:
        (t1 if ev["class"] == "t1" else t3).add(ev["event"])
    return sorted(t1), sorted(t3)


def _t3_sample(t3_events_sorted):
    """Deterministic 1-in-5 sample: indices 0, 5, 10, ... of the SORTED T3
    event list (sorted once here, and the caller must reuse this exact
    sorted order -- the sample is defined over it, not over insertion
    order)."""
    return [t3_events_sorted[i]
            for i in range(0, len(t3_events_sorted), SAMPLE_STRIDE)]


# ------------------------------------------------------------------ prepare
def prepare(root, plan_path, staging):
    bench_plan = json.load(open(plan_path, encoding="utf-8"))
    plan = _group_plan_items(bench_plan)
    os.makedirs(staging, exist_ok=True)
    manifest = cb.emit_packets(root, plan, staging, ROUTING_RULES_TEXT)

    t1_events, t3_events = _t1_t3_events(bench_plan)
    t3_sample = _t3_sample(t3_events)

    bench_manifest = {
        "items": plan["items"],
        "t1_events": t1_events,
        "t3_events": t3_events,
        "t3_verify_sample": t3_sample,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with open(os.path.join(staging, "bench-manifest.json"), "w",
              encoding="utf-8", newline="\n") as fh:
        json.dump(bench_manifest, fh, indent=1, sort_keys=True)

    print("prepare: %d compile-plan item(s) from %d event(s) (%d t1 / %d t3)"
          % (len(plan["items"]), len(bench_plan["events"]),
             len(t1_events), len(t3_events)))
    print("prepare: t3 verify sample = %d/%d event(s): %s"
          % (len(t3_sample), len(t3_events), t3_sample))
    print("prepare: dispatch manifest -> %s"
          % os.path.join(staging, "dispatch-manifest.json"))
    print("prepare: bench manifest -> %s"
          % os.path.join(staging, "bench-manifest.json"))
    return bench_manifest, manifest


# ------------------------------------------------------------------ costs
def _load_costs(costs_path):
    if not costs_path:
        return None
    return json.load(open(costs_path, encoding="utf-8"))


def _sum_token_cost(costs):
    """SUM only -- never invented. costs is the {"packets": {...},
    "baseline": {...}} shape the orchestrator writes; this sums the
    "packets" leg (the replay's own cost), not "baseline" (that belongs to
    the old-path comparison the caller supplies separately)."""
    if costs is None:
        return None
    return sum(int(v) for v in (costs.get("packets") or {}).values())


# -------------------------------------------------------- sampling verify wrap
class _SamplingVerifyBackend:
    """Wraps a real verify backend (BridgeVerifyBackend in production, a
    fixture in self-test): consults bench-manifest to decide, PER PACKET,
    whether this candidate is t1 (always verified) or in the t3 sample
    (verified) vs an unsampled t3 (never fires the inner backend -- returns
    a "skipped-sampling" verdict, which does not start with "confirm" so
    compile-v2.verify_run leaves the candidate PENDING, recorded as
    sampled-out rather than silently confirmed).

    OPS-4 gate clause (c), "every T1 event gets a full VERIFY pass," is read
    PER EVENT. compile_v2.verify_run (memory-engine v3 verify-unit amendment,
    2026-07-05) now fires the backend exactly ONCE per event by construction
    -- one UNION packet covering every routed view's body, never one packet
    per (event,view) candidate. A T1 event that hubs across N plan views
    therefore reaches this wrapper as a single call carrying all N routed
    views in its packet (parsed from the "## FULL VIEW BODY: <view>" section
    headers), not as N separate calls. The historical
    "skipped-duplicate-t1-event" synthetic path (firing per-candidate and
    deduping the 2nd..Nth call for the same T1 event) is UNREACHABLE under
    the union-packet contract -- verify_run itself is the per-event dedup
    now -- so t1_fired/that verdict are kept only as defensive dead code in
    case a future backend is ever driven with the old per-candidate calling
    convention. candidates_total is derived from the union packet's own
    routed-view count (mechanically, not by counting wrapper calls), so it
    still reports "how many (event,view) candidates this T1 event's union
    covers," matching the pre-amendment receipt field's meaning even though
    the wrapper is invoked once instead of N times. T3 sampling is unchanged
    -- the deterministic 1-in-5 sample is already defined per event.

    Packets carry "CLAIM: event <event> carries zero load-bearing claims
    absent from <...>." (compile_v2.verify_run's own packet format) -- the
    event rel is parsed back out of that line to look up sampling status,
    since the wrapper only sees the packet text, matching the same contract
    BridgeVerifyBackend._extract_claim uses."""

    def __init__(self, inner, t1_events, t3_sample_events):
        self.inner = inner
        self.t1 = set(t1_events)
        self.sampled_t3 = set(t3_sample_events)
        self.fired = []          # packets that reached the inner backend
        self.skipped = []        # (event,) tuples skipped for sampling
        self.t1_fired = set()    # t1 events that have already fired once
        # per-event verify map: {event: {"fired": bool, "candidates_total": N}}
        self.t1_verify_map = {e: {"fired": False, "candidates_total": 0}
                              for e in self.t1}

    @staticmethod
    def _event_of_packet(packet):
        for line in packet.splitlines():
            if line.startswith("CLAIM: event "):
                rest = line[len("CLAIM: event "):]
                return rest.split(" carries zero load-bearing claims", 1)[0].strip()
        return None

    @staticmethod
    def _routed_view_count(packet):
        """Union packets (memory-engine v3 verify-unit amendment) carry one
        '## FULL VIEW BODY: <view>' section per routed view -- that count IS
        the candidate count the pre-amendment wrapper used to get by tallying
        one wrapper call per (event,view) candidate."""
        return sum(1 for ln in packet.splitlines()
                  if ln.startswith("## FULL VIEW BODY: "))

    def verify(self, packet):
        erel = self._event_of_packet(packet)
        mandatory = erel in self.t1
        sampled = erel in self.sampled_t3

        if mandatory:
            entry = self.t1_verify_map[erel]
            entry["candidates_total"] += max(1, self._routed_view_count(packet))
            if erel in self.t1_fired:
                # defensive: unreachable under the one-call-per-event union
                # contract, kept only for a hypothetical per-candidate caller.
                return {"verdict": "skipped-duplicate-t1-event",
                        "reason": "T1 event already bridge-verified once "
                                  "this run (OPS-4 per-event mandatory "
                                  "verify); candidate stays pending",
                        "uncertainty": "n/a", "verifier": {},
                        "substrate": {"banner_crosscheck": "n/a"},
                        "evidence_file": ""}
            self.t1_fired.add(erel)
            entry["fired"] = True
            self.fired.append(erel)
            return self.inner.verify(packet)

        if erel is not None and not sampled:
            self.skipped.append(erel)
            return {"verdict": "skipped-sampling",
                    "reason": "t3 event not in the 1-in-5 verify sample",
                    "uncertainty": "n/a", "verifier": {},
                    "substrate": {"banner_crosscheck": "n/a"},
                    "evidence_file": ""}
        self.fired.append(erel)
        return self.inner.verify(packet)


# ------------------------------------------------------------------ run
def _require_answers_present(staging):
    manifest_path = os.path.join(staging, "dispatch-manifest.json")
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    missing = []
    for p in manifest.get("packets") or []:
        ap = os.path.join(staging, p["answer"].replace("/", os.sep))
        if not os.path.isfile(ap):
            missing.append(p["answer"])
    if missing:
        raise ValidationError(
            "run refuses: %d answer JSON(s) missing: %s"
            % (len(missing), ", ".join(sorted(missing))))
    return manifest


def _dry_prepass_timings(root, plan, absorb_backend):
    """Per-item wall-clock WITHOUT mutating the real tree: for each plan item,
    time absorb_backend.absorb(...) + compile_v2.validate_absorb_output(...)
    against the CURRENT on-disk view/events. Never writes anything -- the
    real (single) mutation happens once in the real run below. This is the
    documented source of the p50/p95 numbers; the real run's own total wall
    time is reported alongside it, separately, not blended into the
    per-item distribution."""
    timings = []
    for item in plan["items"]:
        view = item["view"]
        vp = os.path.join(root, view.replace("/", os.sep))
        old = open(vp, encoding="utf-8").read() if os.path.isfile(vp) else ""
        events = {}
        for erel in item["events"]:
            ep = os.path.join(root, erel.replace("/", os.sep))
            events[erel] = (open(ep, encoding="utf-8").read()
                            if os.path.isfile(ep) else "")
        t0 = time.perf_counter()
        out = absorb_backend.absorb(view, old, events)
        try:
            compile_v2.validate_absorb_output(root, view, old, out, events)
        except ValidationError:
            pass   # timing-only pre-pass; real validation happens in run_guarded
        timings.append(time.perf_counter() - t0)
    return timings


def _chunk_items(items, chunk_size):
    """Split plan["items"] into sequential chunks of at most chunk_size
    items each, preserving order. chunk_size must be >= 1."""
    if chunk_size < 1:
        raise ValidationError(
            "--chunk-size must be >= 1 (got %r)" % (chunk_size,))
    return [items[i:i + chunk_size]
            for i in range(0, len(items), chunk_size)]


def _refuse_if_dirty(root):
    """OPS-1 recovery discipline: a non-empty `git status --porcelain` on
    root means a prior crashed/refused run left forward-crash residue (a
    partial chunk's absorbed-but-unverified mutation, or similar) that must
    be reset by the operator before this drill can safely start a new
    chunked run -- resuming on top of unknown local changes would corrupt
    the chunk sequencing and the T1/T3 sampling state. Hard-refuse (exit 3
    at the CLI layer) naming the dirty paths rather than silently
    proceeding or auto-resetting."""
    result = subprocess.run(
        ["git", "-C", root, "status", "--porcelain"],
        capture_output=True, text=True)
    dirty = [line for line in result.stdout.splitlines() if line.strip()]
    if dirty:
        paths = [line[3:] if len(line) > 3 else line for line in dirty]
        raise ValidationError(
            "run refuses (OPS-1 recovery discipline): %d dirty path(s) in "
            "%s -- a prior crashed/refused run may have left forward-crash "
            "residue; reset the tree before starting a new chunked run: %s"
            % (len(dirty), root, ", ".join(sorted(paths))))


def _p50_p95(samples):
    if not samples:
        return 0.0, 0.0
    s = sorted(samples)
    if len(s) == 1:
        return s[0], s[0]
    return (statistics.median(s), s[min(len(s) - 1,
            int(round(0.95 * (len(s) - 1))))])


def run(root, plan_path, staging, absorb_vendor=None, absorb_model=None,
        costs_path=None, baseline_path=None, verify_runner=None,
        authorization=None, chunk_size=DEFAULT_CHUNK_SIZE):
    _refuse_if_dirty(root)

    bench_plan = json.load(open(plan_path, encoding="utf-8"))
    plan = _group_plan_items(bench_plan)
    bench_manifest_path = os.path.join(staging, "bench-manifest.json")
    bench_manifest = json.load(open(bench_manifest_path, encoding="utf-8"))

    _require_answers_present(staging)

    # F17 dispatch-record stamp (2026-07-05 design): FileHandoffAbsorbBackend
    # .absorb() now refuses any dispatch-manifest.json lacking a well-formed
    # dispatch stamp. This drill declares its absorb identity via
    # --absorb-vendor/--absorb-model (the same values it also passes to
    # BridgeVerifyBackend below for the verify-side identity) -- stamp the
    # manifest emit_packets() already wrote in --prepare with those same
    # values before any absorb() call reads it.
    cb.stamp_dispatch(
        os.path.join(staging, "dispatch-manifest.json"),
        absorb_model or "unknown", absorb_vendor or "unknown",
        identity_source="attestation:drill-invocation-argv")

    absorb_backend = cb.FileHandoffAbsorbBackend(staging)

    # dry per-item pre-pass timing (does not touch the real tree; chunk-
    # independent -- it runs once over the full item list, matching the
    # documented p50/p95 source)
    pre_pass_wall = _dry_prepass_timings(root, plan, absorb_backend)
    p50_wall, p95_wall = _p50_p95(pre_pass_wall)

    # ONE sampling_backend, reused across every chunk, so T1 per-event
    # dedup (t1_fired / t1_verify_map) and the T3 sample persist across
    # chunk boundaries -- one benchmark = one leg budget, not one leg
    # budget per chunk.
    inner_verify = cb.BridgeVerifyBackend(
        root, absorb_vendor or "unknown", absorb_model or "unknown",
        runner=verify_runner) if verify_runner is not None else \
        cb.BridgeVerifyBackend(root, absorb_vendor or "unknown",
                               absorb_model or "unknown")
    sampling_backend = _SamplingVerifyBackend(
        inner_verify, bench_manifest["t1_events"],
        bench_manifest["t3_verify_sample"])

    item_chunks = _chunk_items(plan["items"], chunk_size)

    chunks = []
    absorb_wall_total = 0.0
    verify_wall_total = 0.0
    total_rebuilds = 0
    all_seqs = []
    confirmed_total = 0
    checked_total = 0

    for chunk_items in item_chunks:
        chunk_plan = dict(plan)
        chunk_plan["items"] = chunk_items

        t0 = time.perf_counter()
        run_result = cb.run_guarded(root, chunk_plan, absorb_backend)
        chunk_absorb_wall = time.perf_counter() - t0
        absorb_wall_total += chunk_absorb_wall

        t1 = time.perf_counter()
        verify_result = cb.verify_run_guarded(
            root, run_result["seq"], sampling_backend,
            authorization=authorization)
        chunk_verify_wall = time.perf_counter() - t1
        verify_wall_total += chunk_verify_wall

        total_rebuilds += run_result["rebuilds"]
        all_seqs.append(run_result["seq"])
        confirmed_total += verify_result["confirmed"]
        checked_total += verify_result["checked"]

        chunks.append({
            "seq": run_result["seq"],
            "sha": run_result["sha"],
            "items": len(chunk_items),
            "rebuilds": run_result["rebuilds"],
            "verify_seq": verify_result["seq"],
            "verify_sha": verify_result["sha"],
            "confirmed": verify_result["confirmed"],
            "checked": verify_result["checked"],
        })

    costs = _load_costs(costs_path)
    token_cost = _sum_token_cost(costs)

    receipt = {
        "type": "workload-bench",
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "window": bench_plan.get("window"),
        "deviations": bench_plan.get("deviations") or [],
        "chunk_size": chunk_size,
        "chunks": chunks,
        "compile_seqs": all_seqs,
        "compile_seq": all_seqs[-1] if all_seqs else None,
        "verify_seq": chunks[-1]["verify_seq"] if chunks else None,
        "rebuilds": total_rebuilds,
        "items": len(plan["items"]),
        "t1_events": bench_manifest["t1_events"],
        "t3_events": bench_manifest["t3_events"],
        "t3_verify_sample": bench_manifest["t3_verify_sample"],
        "t3_skipped_sampling": sorted(sampling_backend.skipped),
        "verify_fired": sorted(e for e in sampling_backend.fired if e),
        "t1_verify_map": sampling_backend.t1_verify_map,
        "p50_wall": p50_wall,
        "p95_wall": p95_wall,
        "per_item_wall_prepass": pre_pass_wall,
        "absorb_run_wall_total": absorb_wall_total,
        "verify_run_wall_total": verify_wall_total,
        "token_cost": token_cost if token_cost is not None else "not-recorded",
        "verified_confirmed": confirmed_total,
        "verified_checked": checked_total,
    }

    baseline = json.load(open(baseline_path, encoding="utf-8")) \
        if baseline_path else None
    if baseline is not None:
        receipt["baseline"] = baseline
        receipt["verdict"] = _compute_verdict(receipt, baseline)
    else:
        receipt["verdict"] = "INCONCLUSIVE (no baseline supplied)"

    receipts_dir = os.path.join(root, "receipts")
    os.makedirs(receipts_dir, exist_ok=True)
    out_path = os.path.join(
        receipts_dir, "drill-workload-bench-%s.json"
        % time.strftime("%Y-%m-%d"))
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(receipt, fh, indent=1, sort_keys=True)

    print("run: %d chunk(s) (chunk-size=%d), total rebuilds=%d, "
          "compile seqs=%s, verify seqs=%s"
          % (len(chunks), chunk_size, total_rebuilds, all_seqs,
             [c["verify_seq"] for c in chunks]))
    print("run: p50=%.4fs p95=%.4fs (dry per-item pre-pass, n=%d)"
          % (p50_wall, p95_wall, len(pre_pass_wall)))
    print("run: absorb-run wall total=%.4fs, verify-run wall total=%.4fs"
          % (absorb_wall_total, verify_wall_total))
    print("run: token_cost = %s" % receipt["token_cost"])
    print("run: t3 sampled-out (skipped-sampling, never confirmed): %s"
          % receipt["t3_skipped_sampling"])
    print("run: verdict = %s" % receipt["verdict"])
    print("run: receipt -> %s" % out_path)
    return receipt, out_path


# --------------------------------------------------------------- verdict math
def _compute_verdict(receipt, baseline):
    """PASS iff p95_wall <= 2*baseline.p95_wall AND token_cost <=
    1.5*baseline.token_cost. Exactly-at-threshold PASSES (<=, not <). Token
    axis INCONCLUSIVE (never silently PASS) when token_cost is
    "not-recorded" -- but that alone does not fail the wall-clock axis,
    matching the brief's "token_cost reported as not-recorded => the pass
    verdict on the token axis = INCONCLUSIVE, never silently PASS"."""
    base_p95 = baseline["p95_wall"]
    base_tok = baseline["token_cost"]
    wall_ok = receipt["p95_wall"] <= WALL_MULTIPLE * base_p95
    tok = receipt.get("token_cost")
    if tok is None or tok == "not-recorded":
        token_axis = "INCONCLUSIVE"
    else:
        token_axis = "PASS" if tok <= TOKEN_MULTIPLE * base_tok else "FAIL"

    if not wall_ok:
        return "FAIL (p95_wall %.4fs > %.1fx baseline %.4fs)" % (
            receipt["p95_wall"], WALL_MULTIPLE, base_p95)
    if token_axis == "FAIL":
        return "FAIL (token_cost %s > %.1fx baseline %s)" % (
            tok, TOKEN_MULTIPLE, base_tok)
    if token_axis == "INCONCLUSIVE":
        return "INCONCLUSIVE (wall-clock PASS; token_cost not-recorded)"
    return "PASS"


def verdict_from_files(receipt_path, baseline_path):
    receipt = json.load(open(receipt_path, encoding="utf-8"))
    baseline = json.load(open(baseline_path, encoding="utf-8"))
    return _compute_verdict(receipt, baseline)


# ------------------------------------------------------------------ self-test
def self_test():
    import shutil
    import subprocess
    import tempfile

    total = failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    def _commit_all(repo_root, message):
        """OPS-1 discipline, applied inside the fixture itself: a real
        --run writes a receipt (and verify packets/evidence) into the
        working tree, so the operator/session flow is expected to commit
        that output before the next --run on the same root. The self-test
        simulates that discipline explicitly rather than special-casing
        the OPS-1 dirty-tree refusal to ignore run()'s own output."""
        subprocess.run(["git", "-C", repo_root, "add", "-A"],
                       capture_output=True)
        subprocess.run(["git", "-C", repo_root, "commit", "-qm", message],
                       capture_output=True)

    # ---------------------------------------------------------- pure-fn cases
    plan_in = {"events": [
        {"event": "raw/e1.md", "class": "t1", "views": ["wiki/a.md"]},
        {"event": "raw/e2.md", "class": "t3", "views": ["wiki/a.md",
                                                         "wiki/b.md"]},
        {"event": "raw/e3.md", "class": "t3", "views": ["wiki/b.md"]},
    ], "window": {"from": "2026-06-01", "to": "2026-07-01"}, "deviations": []}
    grouped = _group_plan_items(plan_in)
    case("grouping: 2 views produce 2 plan items",
         len(grouped["items"]) == 2)
    a_item = next(i for i in grouped["items"] if i["view"] == "wiki/a.md")
    case("grouping: view absorbing 2 sliced events gets both events in one item",
         sorted(a_item["events"]) == ["raw/e1.md", "raw/e2.md"])
    case("grouping: event_class carries class+origin=judgment per event",
         a_item["event_class"]["raw/e1.md"] == {"class": "t1",
                                                "origin": "judgment"}
         and a_item["event_class"]["raw/e2.md"] == {"class": "t3",
                                                    "origin": "judgment"})

    try:
        _group_plan_items({"events": [{"event": "raw/bad.md", "class": "t2",
                                       "views": ["wiki/a.md"]}]})
        case("unknown event class refused", False)
    except ValidationError:
        case("unknown event class refused", True)

    t1e, t3e = _t1_t3_events(plan_in)
    case("t1/t3 partition correct", t1e == ["raw/e1.md"]
         and t3e == ["raw/e2.md", "raw/e3.md"])

    big_t3 = ["raw/t3-%02d.md" % i for i in range(12)]
    sample = _t3_sample(sorted(big_t3))
    case("t3 sample deterministic 1-in-5 by sorted index (0,5,10)",
         sample == [sorted(big_t3)[0], sorted(big_t3)[5], sorted(big_t3)[10]])
    case("t3 sample count matches ceil(n/5)", len(sample) == 3)

    # ---------------------------------------------------------------- p50/p95
    p50, p95 = _p50_p95([1.0, 2.0, 3.0, 4.0, 5.0])
    case("p50/p95 median on odd sample", p50 == 3.0)
    case("p50/p95 uses 95th-percentile index on 5 samples", p95 == 5.0)
    p50b, p95b = _p50_p95([])
    case("p50/p95 empty -> (0,0)", (p50b, p95b) == (0.0, 0.0))

    # -------------------------------------------------------------- verdict math
    base = {"p95_wall": 10.0, "token_cost": 1000}
    exact_wall = {"p95_wall": 20.0, "token_cost": 1500}
    case("verdict: exactly-at-threshold (2x wall, 1.5x tokens) PASSES",
         _compute_verdict(exact_wall, base) == "PASS")
    over_wall = {"p95_wall": 20.01, "token_cost": 1000}
    case("verdict: p95 fractionally over 2x -> FAIL",
         _compute_verdict(over_wall, base).startswith("FAIL"))
    over_tok = {"p95_wall": 10.0, "token_cost": 1500.01}
    case("verdict: token_cost fractionally over 1.5x -> FAIL",
         _compute_verdict(over_tok, base).startswith("FAIL"))
    no_tok = {"p95_wall": 10.0, "token_cost": "not-recorded"}
    case("verdict: missing token_cost -> INCONCLUSIVE (never silent PASS)",
         _compute_verdict(no_tok, base).startswith("INCONCLUSIVE"))
    case("verdict: wall FAIL dominates even if token would be INCONCLUSIVE",
         _compute_verdict({"p95_wall": 999, "token_cost": "not-recorded"},
                          base).startswith("FAIL"))

    # ------------------------------------------------------ costs summation
    case("_sum_token_cost sums 'packets' leg only",
         _sum_token_cost({"packets": {"a": 100, "b": 50},
                          "baseline": {"x": 99999}}) == 150)
    case("_sum_token_cost(None) -> None (no costs file = not-recorded)",
         _sum_token_cost(None) is None)

    # --------------------------------------------------- sampling verify wrap
    class _StubInner:
        def __init__(self):
            self.calls = []

        def verify(self, packet):
            self.calls.append(packet)
            return {"verdict": "confirmed", "reason": "stub",
                    "uncertainty": "confident", "verifier": {}}

    def _mk_packet(event, view):
        return ("# NOOP VERIFY PACKET seq1-0\n\nCLAIM: event %s carries "
                "zero load-bearing claims absent from view %s.\n"
                % (event, view))

    inner = _StubInner()
    wrap = _SamplingVerifyBackend(inner, t1_events=["raw/e1.md"],
                                  t3_sample_events=["raw/e2.md"])
    v_t1 = wrap.verify(_mk_packet("raw/e1.md", "wiki/a.md"))
    case("sampling wrap: t1 event always fires inner backend",
         v_t1["verdict"] == "confirmed" and len(inner.calls) == 1)
    v_t3_sampled = wrap.verify(_mk_packet("raw/e2.md", "wiki/a.md"))
    case("sampling wrap: sampled t3 event fires inner backend",
         v_t3_sampled["verdict"] == "confirmed" and len(inner.calls) == 2)
    v_t3_unsampled = wrap.verify(_mk_packet("raw/e3.md", "wiki/b.md"))
    case("sampling wrap: unsampled t3 event never fires inner backend",
         len(inner.calls) == 2)
    case("sampling wrap: unsampled t3 -> skipped-sampling verdict",
         v_t3_unsampled["verdict"] == "skipped-sampling")
    case("sampling wrap: skipped-sampling never starts with confirm",
         not v_t3_unsampled["verdict"].lower().startswith("confirm"))
    case("sampling wrap: unsampled event recorded in .skipped",
         "raw/e3.md" in wrap.skipped)

    # --------------------------------------- per-event t1 dedup (unit-level)
    counting_inner = _StubInner()
    wrap_dedup = _SamplingVerifyBackend(counting_inner,
                                        t1_events=["raw/e1.md"],
                                        t3_sample_events=[])
    v_first = wrap_dedup.verify(_mk_packet("raw/e1.md", "wiki/a.md"))
    case("t1 dedup: first candidate for a t1 event fires the inner backend",
         v_first["verdict"] == "confirmed" and len(counting_inner.calls) == 1)
    v_second = wrap_dedup.verify(_mk_packet("raw/e1.md", "wiki/b.md"))
    case("t1 dedup: second candidate for the SAME t1 event (different view) "
        "does not fire the inner backend again",
         len(counting_inner.calls) == 1)
    case("t1 dedup: second candidate gets skipped-duplicate-t1-event verdict",
         v_second["verdict"] == "skipped-duplicate-t1-event")
    case("t1 dedup: skipped-duplicate-t1-event never starts with confirm",
         not v_second["verdict"].lower().startswith("confirm"))
    case("t1 dedup: verify map records fired=True, candidates_total=2",
         wrap_dedup.t1_verify_map["raw/e1.md"] ==
         {"fired": True, "candidates_total": 2})

    # ----------------------------------------------------- prepare + manifest
    tmp = tempfile.mkdtemp(prefix="wb-selftest-")
    try:
        root = os.path.join(tmp, "repo")
        os.makedirs(os.path.join(root, "wiki"))
        os.makedirs(os.path.join(root, "raw"))
        with open(os.path.join(root, "wiki", "a.md"), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write("# A\n\n## Intro\nhello\n")
        with open(os.path.join(root, "wiki", "b.md"), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write("# B\n\n## Intro\nworld\n")
        # NOTE: event filenames carry the "session-" prefix deliberately --
        # compile-backends.dispatch_guard's derive_dispatch_action() treats
        # that prefix as trusted origin (corpus-first-party). Untrusted/
        # unknown-origin events hit the guard's rung-4 hard refusal
        # (free-selection egress) regardless of authorization, by design --
        # a bench fixture that wants to exercise the guarded verify path at
        # all must use trusted-origin event names, same as compile-backends'
        # own self-test does.
        # each event carries parseable frontmatter so compile-backends'
        # canonical (origin.py-backed) classification -- which now checks
        # parseability BEFORE the filename prefix (origin.py rule 1 is
        # unparseable-frontmatter -> unknown, ahead of rule 3's session-*
        # -> corpus) -- still resolves these session-* fixtures to
        # corpus-first-party rather than falling through to unknown.
        for erel, body in [("raw/session-e1.md", "t1 fact\n"),
                           ("raw/session-e2.md", "t3 fact shared\n"),
                           ("raw/session-e3.md", "t3 fact solo\n")]:
            with open(os.path.join(root, erel.replace("/", os.sep)), "w",
                      encoding="utf-8", newline="\n") as fh:
                fh.write("---\nsource: session\n---\n\n" + body)
        subprocess.run(["git", "-C", root, "init", "-q"], capture_output=True)
        subprocess.run(["git", "-C", root, "config", "user.email", "t@t"],
                       capture_output=True)
        subprocess.run(["git", "-C", root, "config", "user.name", "t"],
                       capture_output=True)
        subprocess.run(["git", "-C", root, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", root, "commit", "-qm", "seed"],
                       capture_output=True)

        # 2026-07-06 authorization-artifact CLASS CONSTRAINT (see
        # compile-backends.py's AUTHORIZATION_ARTIFACT_CLASS_GLOB /
        # dispatch_guard docstring): the ack file must live at
        # deploy/evidence/operator-*.md, not the fixture-repo root.
        auth_dir = os.path.join(root, "deploy", "evidence")
        os.makedirs(auth_dir, exist_ok=True)
        auth_file = os.path.join(auth_dir, "operator-test-authorization.md")
        with open(auth_file, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("Operator ack: proceed with workload-bench verify "
                     "dispatch.\n")
        authorization = {"path": auth_file,
                         "quote": "Operator ack: proceed with workload-bench "
                                  "verify dispatch."}
        # OPS-1: run() now refuses on a dirty tree, so fixture setup must
        # leave the tree clean before any run() call.
        subprocess.run(["git", "-C", root, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", root, "commit", "-qm", "add authorization"],
                       capture_output=True)

        plan_path = os.path.join(tmp, "bench-plan.json")
        with open(plan_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({
                "events": [
                    {"event": "raw/session-e1.md", "class": "t1",
                     "views": ["wiki/a.md"]},
                    {"event": "raw/session-e2.md", "class": "t3",
                     "views": ["wiki/a.md", "wiki/b.md"]},
                    {"event": "raw/session-e3.md", "class": "t3",
                     "views": ["wiki/b.md"]},
                ],
                "window": {"from": "2026-06-01", "to": "2026-07-01"},
                "deviations": ["fixture-only self-test, not a live 30-day slice"],
            }, fh)

        staging = os.path.join(tmp, "staging")
        bench_manifest, dispatch_manifest = prepare(root, plan_path, staging)
        case("prepare: 2 compile-plan items written (one per view)",
             len(bench_manifest["items"]) == 2)
        case("prepare: t1_events recorded", bench_manifest["t1_events"]
             == ["raw/session-e1.md"])
        case("prepare: t3_events recorded",
             bench_manifest["t3_events"] == ["raw/session-e2.md",
                                             "raw/session-e3.md"])
        case("prepare: t3 sample = index 0 of sorted t3 list (n=2, stride 5)",
             bench_manifest["t3_verify_sample"] == ["raw/session-e2.md"])
        case("prepare: bench-manifest.json written to staging",
             os.path.isfile(os.path.join(staging, "bench-manifest.json")))
        case("prepare: dispatch-manifest.json written to staging (emit_packets)",
             os.path.isfile(os.path.join(staging, "dispatch-manifest.json")))
        case("prepare: emit_packets produced 2 packets",
             len(dispatch_manifest["packets"]) == 2)

        # run refuses on missing answers, naming them
        try:
            run(root, plan_path, staging)
            case("run refuses with missing answer JSONs", False)
        except ValidationError as e:
            case("run refuses with missing answer JSONs", True)
            case("refusal names the missing answer file(s)",
                 ".json" in str(e))

        # write fixture answers for both packets. View a: raw/session-e1.md
        # (t1) is claimed a NO-OP -- this is the case the gate cares about,
        # since verify_run() only ever assembles packets for noop_candidates.
        # Because it's t1, F6/the LOCK_CLASSES set force
        # PENDING_NOOP_CANDIDATE regardless of origin, so it is mandatorily
        # verified. session-e2.md (t3) is absorbed as real content in view
        # a and REPEATED as a real-content absorb in view b, so it never
        # becomes a candidate (matching "real content changes never create a
        # noop_candidate" -- only session-e3.md, a genuine t3 no-op in view
        # b, exercises the sampling axis).
        def _sha256_hex(text):
            import hashlib
            return hashlib.sha256(text.encode("utf-8")).hexdigest()

        for p in dispatch_manifest["packets"]:
            view = p["view"]
            ans_path = os.path.join(staging, p["answer"].replace("/", os.sep))
            if view == "wiki/a.md":
                answer = {
                    "new_text": "# A\n\n## Intro\nhello\n\n## Absorbed\n"
                                "t3 fact shared\n",
                    "manifest": [{"event": "raw/session-e2.md",
                                 "section": "Absorbed"}],
                    "corpus_support": [],
                    "noops": [{"event": "raw/session-e1.md",
                              "justification_note": "already represented"}]}
            else:
                answer = {
                    "new_text": "# B\n\n## Intro\nworld\n\n## Absorbed\n"
                                "t3 fact shared\n",
                    "manifest": [{"event": "raw/session-e2.md",
                                 "section": "Absorbed"}],
                    "corpus_support": [],
                    "noops": [{"event": "raw/session-e3.md",
                              "justification_note": "already represented"}]}
            with open(ans_path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(answer, fh)

        # fixture verify runner: confirms everything the inner bridge backend
        # actually receives (only fired for t1 + sampled t3, per the wrap)
        def fixture_runner(args):
            return 0, json.dumps({
                "verdict": "confirmed", "reason": "fixture",
                "uncertainty": "confident",
                "verifier": {"vendor": "openai", "model": "gpt-5.5"}}), ""

        receipt, out_path = run(root, plan_path, staging,
                                absorb_vendor="anthropic",
                                absorb_model="claude-sonnet-5",
                                verify_runner=fixture_runner,
                                authorization=authorization)
        case("run: receipt written to receipts/ under the worktree",
             os.path.isfile(out_path)
             and out_path.startswith(os.path.join(root, "receipts")))
        case("run: receipt type is workload-bench", receipt["type"] ==
             "workload-bench")
        case("run: p50/p95 present as numbers",
             isinstance(receipt["p50_wall"], float)
             and isinstance(receipt["p95_wall"], float))
        case("run: per-item pre-pass timing recorded for both items",
             len(receipt["per_item_wall_prepass"]) == 2)
        case("run: t3 unsampled event (session-e3) recorded as "
            "skipped-sampling",
             "raw/session-e3.md" in receipt["t3_skipped_sampling"])
        case("run: t1 event was fired (not skipped)",
             "raw/session-e1.md" in receipt["verify_fired"])
        case("run: token_cost is 'not-recorded' with no --absorb-costs",
             receipt["token_cost"] == "not-recorded")
        case("run: verdict INCONCLUSIVE with no baseline supplied",
             receipt["verdict"].startswith("INCONCLUSIVE"))
        # OPS-1: commit run()'s own output (receipt + verify evidence)
        # before the next run() on this same root, per the discipline
        # documented on _refuse_if_dirty.
        _commit_all(root, "commit receipt 1")

        # re-run verdict from the receipt + a baseline file (Phase C)
        baseline_path = os.path.join(tmp, "baseline.json")
        with open(baseline_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"p95_wall": 999.0, "token_cost": 999999}, fh)
        v = verdict_from_files(out_path, baseline_path)
        case("verdict phase: cheap receipt vs generous baseline -> PASS-ish "
            "(wall ok, token INCONCLUSIVE since not-recorded)",
             v.startswith("INCONCLUSIVE") or v == "PASS")

        # idempotence: recomputing twice gives the identical string
        v2 = verdict_from_files(out_path, baseline_path)
        case("verdict phase is idempotent (same receipt/baseline -> same "
            "string twice)", v == v2)

        # now with an --absorb-costs file, token axis becomes decidable
        costs_path = os.path.join(tmp, "costs.json")
        with open(costs_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"packets": {"answers/01-wiki-a-md.json": 500,
                                   "answers/02-wiki-b-md.json": 300},
                      "baseline": {"old_path": 100000}}, fh)
        staging2 = os.path.join(tmp, "staging2")
        prepare(root, plan_path, staging2)
        dm2 = json.load(open(os.path.join(staging2,
                                          "dispatch-manifest.json"),
                             encoding="utf-8"))
        for p in dm2["packets"]:
            view = p["view"]
            ans_path = os.path.join(staging2, p["answer"].replace("/", os.sep))
            if view == "wiki/a.md":
                old_a = open(os.path.join(root, "wiki", "a.md"),
                            encoding="utf-8").read()
                answer = {"new_text": old_a + "\n## Absorbed2\nmore\n",
                          "manifest": [{"event": "raw/session-e1.md",
                                       "section": "Absorbed2"},
                                      {"event": "raw/session-e2.md",
                                       "section": "Absorbed2"}],
                          "corpus_support": [], "noops": []}
            else:
                answer = {"new_text": None, "manifest": [],
                          "corpus_support": [],
                          "noops": [{"event": "raw/session-e2.md",
                                    "justification_note": "already represented"},
                                   {"event": "raw/session-e3.md",
                                    "justification_note": "already represented"}]}
            with open(ans_path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(answer, fh)
        receipt2, out_path2 = run(root, plan_path, staging2,
                                  absorb_vendor="anthropic",
                                  absorb_model="claude-sonnet-5",
                                  costs_path=costs_path,
                                  verify_runner=fixture_runner,
                                  authorization=authorization)
        case("run with --absorb-costs: token_cost summed (500+300=800)",
             receipt2["token_cost"] == 800)
        _commit_all(root, "commit receipt 2")

        baseline2 = os.path.join(tmp, "baseline2.json")
        with open(baseline2, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"p95_wall": 999.0, "token_cost": 1000}, fh)
        v3 = verdict_from_files(out_path2, baseline2)
        case("with costs recorded, token axis decides (800 <= 1.5*1000) "
            "-> PASS", v3 == "PASS")

        baseline3 = os.path.join(tmp, "baseline3.json")
        with open(baseline3, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"p95_wall": 999.0, "token_cost": 100}, fh)
        v4 = verdict_from_files(out_path2, baseline3)
        case("with costs recorded, token axis over 1.5x -> FAIL",
             v4.startswith("FAIL"))

        # T1 refusal-to-skip is structural: build a manifest where the ONLY
        # t1 event is deliberately absent from any sample list and confirm
        # the wrap still fires it (already covered above by v_t1, but assert
        # again against the real bench-manifest contents for this fixture)
        case("T1 refusal-to-skip: t1 event never appears in "
            "t3_skipped_sampling", "raw/session-e1.md" not in
             receipt["t3_skipped_sampling"])

        # ---------------------------------------------- T1 hub-event dedup (e2e)
        # session-e1.md (t1) is hubbed into BOTH wiki/a.md and wiki/b.md, and
        # answered as a no-op in both views -> two noop_candidates for the
        # SAME t1 event. OPS-4 clause (c) is per-event: exactly one bridge
        # firing for session-e1.md this run; the second candidate must come
        # back skipped-duplicate-t1-event and stay unverified.
        plan_path_hub = os.path.join(tmp, "bench-plan-hub.json")
        with open(plan_path_hub, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({
                "events": [
                    {"event": "raw/session-e1.md", "class": "t1",
                     "views": ["wiki/a.md", "wiki/b.md"]},
                ],
                "window": {"from": "2026-06-01", "to": "2026-07-01"},
                "deviations": ["fixture-only self-test: t1 hub-event dedup"],
            }, fh)

        staging_hub = os.path.join(tmp, "staging-hub")
        bench_manifest_hub, dispatch_manifest_hub = prepare(
            root, plan_path_hub, staging_hub)
        case("hub fixture: 2 plan items (one per view) from 1 hub event",
             len(bench_manifest_hub["items"]) == 2)

        for p in dispatch_manifest_hub["packets"]:
            ans_path = os.path.join(staging_hub,
                                    p["answer"].replace("/", os.sep))
            answer = {"new_text": None, "manifest": [],
                      "corpus_support": [],
                      "noops": [{"event": "raw/session-e1.md",
                                "justification_note":
                                "already represented (hub fixture)"}]}
            with open(ans_path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(answer, fh)

        counting_calls = []

        def counting_fixture_runner(args):
            counting_calls.append(args)
            return 0, json.dumps({
                "verdict": "confirmed", "reason": "fixture",
                "uncertainty": "confident",
                "verifier": {"vendor": "openai", "model": "gpt-5.5"}}), ""

        receipt_hub, out_path_hub = run(
            root, plan_path_hub, staging_hub,
            absorb_vendor="anthropic", absorb_model="claude-sonnet-5",
            verify_runner=counting_fixture_runner,
            authorization=authorization)

        case("hub e2e: bridge fired exactly ONCE for the hub t1 event "
            "across 2 views", len(counting_calls) == 1)
        case("hub e2e: verify_fired lists the hub event once",
             receipt_hub["verify_fired"].count("raw/session-e1.md") == 1)
        case("hub e2e: t1_verify_map records fired=True, "
            "candidates_total=2 for the hub event",
             receipt_hub["t1_verify_map"]["raw/session-e1.md"] ==
             {"fired": True, "candidates_total": 2})
        case("hub e2e: receipt carries the per-event verify map",
             "t1_verify_map" in receipt_hub)
        _commit_all(root, "commit hub receipt")

        # ------------------------------------------------- _chunk_items (unit)
        case("_chunk_items: 5 items, chunk-size 2 -> 3 chunks of 2/2/1",
             [len(c) for c in _chunk_items(list(range(5)), 2)] == [2, 2, 1])
        case("_chunk_items: preserves order",
             _chunk_items(list(range(5)), 2) ==
             [[0, 1], [2, 3], [4]])
        case("_chunk_items: chunk-size >= item count -> 1 chunk",
             len(_chunk_items(list(range(3)), 12)) == 1)
        try:
            _chunk_items([1, 2], 0)
            case("_chunk_items: chunk-size < 1 refused", False)
        except ValidationError:
            case("_chunk_items: chunk-size < 1 refused", True)

        # ------------------------------------------- OPS-1 dirty-tree refusal
        dirty_path = os.path.join(root, "wiki", "dirty.md")
        with open(dirty_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("uncommitted residue\n")
        try:
            _refuse_if_dirty(root)
            case("OPS-1: dirty tree refused", False)
        except ValidationError as e:
            case("OPS-1: dirty tree refused", True)
            case("OPS-1: refusal names the dirty path",
                 "dirty.md" in str(e))
        try:
            run(root, plan_path, staging, authorization=authorization)
            case("OPS-1: run() itself refuses on a dirty tree", False)
        except ValidationError as e:
            case("OPS-1: run() itself refuses on a dirty tree", True)
            case("OPS-1: run()'s dirty-tree refusal names the dirty path",
                 "dirty.md" in str(e))
        os.remove(dirty_path)
        case("OPS-1: clean tree after reset does not refuse",
             _refuse_if_dirty(root) is None)

        # --------------------------------------------- chunked run (e2e)
        # 5 views (5 plan items), chunk-size 2 -> 3 chunks (2,2,1). One T1
        # event ("raw/session-t1c.md") is hubbed into the FIRST view of
        # chunk 1 (wiki/c1.md) and the FIRST view of chunk 3 (wiki/c5.md),
        # both answered as no-ops -- exercising T1 per-event dedup ACROSS
        # chunk boundaries: the bridge must fire exactly once total, not
        # once per chunk.
        root2 = os.path.join(tmp, "repo2")
        os.makedirs(os.path.join(root2, "wiki"))
        os.makedirs(os.path.join(root2, "raw"))
        view_names = ["c1", "c2", "c3", "c4", "c5"]
        for vn in view_names:
            with open(os.path.join(root2, "wiki", vn + ".md"), "w",
                      encoding="utf-8", newline="\n") as fh:
                fh.write("# %s\n\n## Intro\nhello %s\n" % (vn, vn))
        with open(os.path.join(root2, "raw", "session-t1c.md"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("---\nsource: session\n---\n\nt1 chunked fact\n")
        subprocess.run(["git", "-C", root2, "init", "-q"],
                       capture_output=True)
        subprocess.run(["git", "-C", root2, "config", "user.email", "t@t"],
                       capture_output=True)
        subprocess.run(["git", "-C", root2, "config", "user.name", "t"],
                       capture_output=True)
        subprocess.run(["git", "-C", root2, "add", "-A"],
                       capture_output=True)
        subprocess.run(["git", "-C", root2, "commit", "-qm", "seed"],
                       capture_output=True)

        # 2026-07-06 authorization-artifact CLASS CONSTRAINT (see
        # compile-backends.py's AUTHORIZATION_ARTIFACT_CLASS_GLOB /
        # dispatch_guard docstring): the ack file must live at
        # deploy/evidence/operator-*.md, not the fixture-repo root.
        auth_dir2 = os.path.join(root2, "deploy", "evidence")
        os.makedirs(auth_dir2, exist_ok=True)
        auth_file2 = os.path.join(auth_dir2, "operator-test-authorization-2.md")
        with open(auth_file2, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("Operator ack: proceed with workload-bench verify "
                     "dispatch.\n")
        authorization2 = {"path": auth_file2,
                          "quote": "Operator ack: proceed with "
                                   "workload-bench verify dispatch."}

        plan_path_chunk = os.path.join(tmp, "bench-plan-chunk.json")
        with open(plan_path_chunk, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({
                "events": [
                    {"event": "raw/session-t1c.md", "class": "t1",
                     "views": ["wiki/c1.md", "wiki/c5.md"]},
                ],
                "window": {"from": "2026-06-01", "to": "2026-07-01"},
                "deviations": ["fixture-only self-test: chunked run"],
            }, fh)
        # note: _group_plan_items only ever creates plan items for views
        # actually named by an event, so also give each of c2/c3/c4 a
        # trivial t3 event to reach 5 plan items total (one per view).
        plan_data_chunk = json.load(open(plan_path_chunk, encoding="utf-8"))
        for i, vn in enumerate(["c2", "c3", "c4"]):
            erel = "raw/session-t3c%d.md" % i
            with open(os.path.join(root2, erel.replace("/", os.sep)), "w",
                      encoding="utf-8", newline="\n") as fh:
                fh.write("---\nsource: session\n---\n\nt3 filler %d\n" % i)
            plan_data_chunk["events"].append(
                {"event": erel, "class": "t3", "views": ["wiki/%s.md" % vn]})
        with open(plan_path_chunk, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(plan_data_chunk, fh)
        subprocess.run(["git", "-C", root2, "add", "-A"],
                       capture_output=True)
        subprocess.run(["git", "-C", root2, "commit", "-qm", "add fillers"],
                       capture_output=True)

        staging_chunk = os.path.join(tmp, "staging-chunk")
        bench_manifest_chunk, dispatch_manifest_chunk = prepare(
            root2, plan_path_chunk, staging_chunk)
        case("chunk fixture: 5 plan items (one per view)",
             len(bench_manifest_chunk["items"]) == 5)

        for p in dispatch_manifest_chunk["packets"]:
            view = p["view"]
            ans_path = os.path.join(staging_chunk,
                                    p["answer"].replace("/", os.sep))
            if view in ("wiki/c1.md", "wiki/c5.md"):
                answer = {"new_text": None, "manifest": [],
                          "corpus_support": [],
                          "noops": [{"event": "raw/session-t1c.md",
                                    "justification_note":
                                    "already represented (chunk fixture)"}]}
            else:
                idx = {"wiki/c2.md": 0, "wiki/c3.md": 1,
                      "wiki/c4.md": 2}[view]
                answer = {"new_text": None, "manifest": [],
                          "corpus_support": [],
                          "noops": [{"event": "raw/session-t3c%d.md" % idx,
                                    "justification_note":
                                    "already represented (chunk fixture)"}]}
            with open(ans_path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(answer, fh)

        chunk_calls = []

        def chunk_fixture_runner(args):
            chunk_calls.append(args)
            return 0, json.dumps({
                "verdict": "confirmed", "reason": "fixture",
                "uncertainty": "confident",
                "verifier": {"vendor": "openai", "model": "gpt-5.5"}}), ""

        receipt_chunk, out_path_chunk = run(
            root2, plan_path_chunk, staging_chunk,
            absorb_vendor="anthropic", absorb_model="claude-sonnet-5",
            verify_runner=chunk_fixture_runner,
            authorization=authorization2, chunk_size=2)

        case("chunked run: 3 chunks produced (5 items, chunk-size 2)",
             len(receipt_chunk["chunks"]) == 3)
        case("chunked run: 3 distinct compile seqs",
             len(set(receipt_chunk["compile_seqs"])) == 3)
        case("chunked run: 3 distinct verify seqs",
             len(set(c["verify_seq"] for c in receipt_chunk["chunks"])) == 3)
        case("chunked run: chunk item counts are 2/2/1",
             [c["items"] for c in receipt_chunk["chunks"]] == [2, 2, 1])
        case("chunked run: aggregate rebuilds = sum of per-chunk rebuilds",
             receipt_chunk["rebuilds"] ==
             sum(c["rebuilds"] for c in receipt_chunk["chunks"]))
        # chunk_calls counts every inner-backend fire this run: the T1 hub
        # event (deduped to exactly 1 fire across its 2 chunk-1/chunk-3
        # candidates) PLUS the one sampled T3 event (session-t3c0, index 0
        # of the sorted t3 list) = 2 total fires. The dedup claim itself is
        # verified precisely via t1_verify_map/verify_fired below.
        case("chunked run: T1 hub event spanning chunks 1 and 3 fires the "
            "bridge exactly ONCE total (dedup persists across chunks); "
            "plus 1 sampled T3 fire = 2 total inner-backend calls",
             len(chunk_calls) == 2)
        case("chunked run: t1_verify_map records fired=True, "
            "candidates_total=2 for the cross-chunk hub event",
             receipt_chunk["t1_verify_map"]["raw/session-t1c.md"] ==
             {"fired": True, "candidates_total": 2})
        case("chunked run: verify_fired lists the hub event once",
             receipt_chunk["verify_fired"].count("raw/session-t1c.md") == 1)
        case("chunked run: absorb_run_wall_total is the sum across chunks "
            "(positive, one wall reading per chunk)",
             receipt_chunk["absorb_run_wall_total"] > 0)
        case("chunked run: per-item dry pre-pass timing still covers all "
            "5 items (chunk-independent)",
             len(receipt_chunk["per_item_wall_prepass"]) == 5)
        case("chunked run: receipt records chunk_size used",
             receipt_chunk["chunk_size"] == 2)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failed:
        print("drill-workload-bench (OPS-4) self-test: FAIL (%d/%d)"
              % (total - failed, total))
        return 1
    print("drill-workload-bench (OPS-4) self-test: PASS (%d/%d)"
          % (total, total))
    return 0


# ------------------------------------------------------------------ CLI
def _arg(argv, flag, default=None):
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def main(argv):
    if "--self-test" in argv:
        return self_test()

    if "--prepare" in argv:
        root = _arg(argv, "--root")
        plan_path = _arg(argv, "--plan")
        staging = _arg(argv, "--staging")
        if not (root and plan_path and staging):
            print("--prepare requires --root --plan --staging")
            return 3
        try:
            prepare(root, plan_path, staging)
        except ValidationError as e:
            print("PREPARE REFUSED: %s" % e)
            return 3
        return 0

    if "--run" in argv:
        root = _arg(argv, "--root")
        plan_path = _arg(argv, "--plan")
        staging = _arg(argv, "--staging")
        if not (root and plan_path and staging):
            print("--run requires --root --plan --staging")
            return 3
        costs_path = _arg(argv, "--absorb-costs")
        baseline_path = _arg(argv, "--baseline")
        absorb_vendor = _arg(argv, "--absorb-vendor")
        absorb_model = _arg(argv, "--absorb-model")
        auth_path = _arg(argv, "--authorization-path")
        auth_quote = _arg(argv, "--authorization-quote")
        authorization = ({"path": auth_path, "quote": auth_quote}
                         if auth_path and auth_quote else None)
        chunk_size = int(_arg(argv, "--chunk-size", DEFAULT_CHUNK_SIZE))
        try:
            receipt, _out = run(root, plan_path, staging,
                                absorb_vendor=absorb_vendor,
                                absorb_model=absorb_model,
                                costs_path=costs_path,
                                baseline_path=baseline_path,
                                authorization=authorization,
                                chunk_size=chunk_size)
        except ValidationError as e:
            print("RUN REFUSED: %s" % e)
            return 3
        except cb.DispatchRefused as e:
            print("DISPATCH REFUSED: %s" % e.guard)
            return 3
        if str(receipt["verdict"]).startswith("FAIL"):
            return 1
        if str(receipt["verdict"]).startswith("INCONCLUSIVE"):
            return 2
        return 0

    if "--verdict" in argv:
        receipt_path = _arg(argv, "--receipt")
        baseline_path = _arg(argv, "--baseline")
        if not (receipt_path and baseline_path):
            print("--verdict requires --receipt --baseline")
            return 3
        v = verdict_from_files(receipt_path, baseline_path)
        print("verdict: %s" % v)
        if v.startswith("FAIL"):
            return 1
        if v.startswith("INCONCLUSIVE"):
            return 2
        return 0

    print(__doc__.strip().split("\n\n")[-1])
    return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
