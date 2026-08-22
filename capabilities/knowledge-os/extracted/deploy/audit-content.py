#!/usr/bin/env python3
"""audit-content.py -- F13 migration-era content-audit harness (test-plan tp:350-352, spec §13).

The content audit: event-claims -> view-hunks over every `legacy-assumed` /
`absorbed-without-source` T1/correction/lock view, BEFORE such a view is cleared for
build/fix dispatch. At P1 every seeded view is tier-T1-conservative (backfill-derivation
mints `tier: T1` for all), so the population = every ledger event with >=1 sourcing wiki
view (the seeded legacy-assumed pairs) + any GOLD-1 injection pairs
(absorbed-without-source; zero on this corpus per the 2026-07-02 dry-run).

AUDIT UNIT = THE EVENT (not the view). Legacy receipts batch one event into many sibling
views, distributing its claims by topic -- auditing "every event claim appears in THIS
view" is structurally false on any multi-target event (the same batched-compile shape
that made GOLD-1's naive cross-product an artifact; proven live by this harness's own
pilot round 1, where all 3 regular per-view legs rejected on sibling-absorbed claims).
The conservation property F13 owns (per the GOLD-1 metric amendment's binding caveat) is
"no load-bearing claim of the event is absent from ALL of its target views" -- so each
leg packets one event + the union of views that source it. A failing event conservatively
blocks EVERY view that sources it; a view is cleared only when all its events pass.

It runs UNDER THE VERIFY HARNESS, never as a standalone same-substrate LLM batch (tp:350):
  (a) SUBSTRATE  : verifier vendor != view-lineage vendor. The lineage author is
                   anthropic/Claude, so every audit leg must return from a non-anthropic
                   verifier -- the MIGRATION policy of check-substrate.py (LLM-5), with
                   F17/F18 enforced fail-closed on every leg's substrate record.
  (b) RECORD     : every leg's verdict is recorded in the same structured form as a
                   VERIFY pass (separate vendor/model_id fields, substrate_source,
                   packet hash, artifact = the raw verdict JSON on disk).
  (c) PLANTED    : every batch must contain >=1 planted-defect leg -- a fixture copy of a
                   real view with a load-bearing event claim deterministically removed
                   (committed mutation spec, exact-once drift guard). No planted leg in
                   the batch -> INCONCLUSIVE (exit 2). A planted leg the verifier fails
                   to catch -> the batch is NOT accepted (exit 1, efficacy failure).
                   Wrong substrate on any leg -> INCONCLUSIVE (exit 2). Decisive-verdict
                   floor >= 4/5 over the regular legs (mirrors LLM-1's catch floor;
                   `revised`/missing verdicts are indecisive), else INCONCLUSIVE.

Precise gate semantics (cross-vendor-revised, 2026-07-03 gpt-5.5 check): the substrate
gate (a) applies to every ANSWERED leg -- a leg with a missing/unparseable verdict file
carries no substrate to judge and is instead handled fail-safe by two other gates: it
counts INDECISIVE against the >=4/5 decisive floor (too many -> INCONCLUSIVE), and its
views are conservatively BLOCKED (a non-confirmed event never clears anything, and a
missing-verdict planted leg counts as MISSED -> exit 1). There is no clearance path
through an unanswered leg. The F17 check here is the schema layer (fail-closed); the
runtime guarantee that the marker reflects REAL invocation metadata is the write-path
leg owed at P4 (same residual as the LLM-5 c4 finding).

Verdict mapping (bridge enum confirmed/revised/rejected; the leg claim asserts NO
load-bearing claim is absent from the view):
  confirmed -> view audit PASS (candidate for verified-consumed at P1-live seeding)
  rejected  -> a claim is absent (planted leg: CAUGHT, floor satisfied; regular leg:
               view AUDIT-FAIL -- the view stays blocked, finding surfaced to operator)
  revised   -> indecisive (surfaced; counts against the 4/5 decisive floor)

BLINDING: leg ids are salted hashes; the planted flag lives only in the batch manifest
(never in a packet or its filename), so the verifier cannot distinguish planted legs.

READ-ONLY over the tree: packets/records are written to --out / --batch only; --prepare
refuses an out dir inside the tree's wiki/ or raw/. This harness never stamps
consumed_status -- clearing views to verified-consumed happens at P1-live seeding
(operator-gated), consuming this batch's accepted record.

Usage:
  audit-content.py --prepare --root DIR --out DIR --planted SPEC [--planted SPEC2 ...]
                   [--events e1,e2,...]     (default: the full event population)
  audit-content.py --fire --batch DIR [--timeout-ms N] [--effort LEVEL]
                   (fires the bridge verifier per unanswered leg; needs node + codex CLI)
  audit-content.py --ingest --batch DIR
  audit-content.py --population --root DIR  (report the audit population, read-only)
  audit-content.py --self-test
Exit codes: 0 = batch accepted, every audited view passes | 1 = planted-defect missed
  (efficacy failure) or >=1 real view failed the audit | 2 = INCONCLUSIVE
  (setup / substrate / decisive-floor / missing planted).
"""

import fnmatch
import hashlib
import importlib.util
import json
import os
import posixpath
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    import staleness  # noqa: E402
except Exception:  # pragma: no cover
    staleness = None

try:
    import yaml  # noqa: E402
except ImportError:  # pragma: no cover
    yaml = None

try:
    import registrations  # noqa: E402  (P5: fixture tree mints real chains)
except Exception:  # pragma: no cover
    registrations = None


def _load_module(basename, alias):
    spec = importlib.util.spec_from_file_location(alias, os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_substrate = _load_module("check-substrate.py", "check_substrate")

# The view-lineage author (absorb side). The legacy corpus was authored across many Claude
# sessions/models; the MIGRATION gate is VENDOR-level (tp:384 policy), so the model_id is an
# honest aggregate label, recorded separately per F18.
ABSORB_VENDOR = "anthropic"
ABSORB_MODEL_ID = "claude-legacy-lineage"

CLAIM_TEMPLATE = (
    "In content-audit packet %s, every load-bearing claim asserted by the EVENT section "
    "is represented in AT LEAST ONE of the VIEW sections (verbatim or as a faithful "
    "restatement); no load-bearing event claim is absent from every view."
)

PACKET_CONTRACT = """\
CONTRACT (event-claims -> view-hunks; load-bearing definition per the 2026-07-03
f13-loadbearing amendment). The EVENT below was compiled into the VIEW(s) below (its
declared compile targets); together they are claimed to have absorbed it. A LOAD-BEARING
CLAIM is one downstream work would rely on FROM THE WIKI: a decision, lock, or
commitment; a schema or mechanism rule; a compliance constraint; a price or quantitative
commitment; a correction of prior recorded state; a standing constraint or invariant.
The following are NOT load-bearing (distillation-accepted -- their absence is not a
defect): implementation directives whose effect is realized in the repository itself
(executed tasks, navigation/formatting instructions); ephemeral point-in-time
observations with no standing reliance (e.g. current episode/post counts); secondary
rationale restating support for a decision the views do carry; phrasing, ordering, or
incidental color. Claims from one event are legitimately DISTRIBUTED across its target
views by topic (batched compiles); a claim represented in ANY one view is accounted for.
Audit direction: for each load-bearing claim in the EVENT, check it is represented in at
least one VIEW (verbatim or faithfully restated). CONFIRM the claim under test only if
no load-bearing event claim is missing from all views. REJECT if any load-bearing claim
appears in NO view, quote the missing claim verbatim from the event, and state briefly
why it is load-bearing under the definition above. Judge only presence/absence -- not
style, not whether the views add material beyond the event.
"""


# --------------------------------------------------------------------------- population
def enumerate_population(root, injection_pairs=None):
    """The F13 audit population, read-only: {event_rel: sorted [view_rel, ...]}.

    EVENT-CENTRIC (the load-bearing design decision, learned from the pilot): legacy
    receipts batch one event into MANY sibling views, distributing its claims by topic --
    the same structure that made GOLD-1's naive cross-product an artifact. A per-view
    audit unit ("every event claim appears in THIS view") is structurally false on any
    multi-target event; the conservation property F13 actually owns (per the GOLD-1
    metric amendment's binding caveat) is "no load-bearing claim of the event is absent
    from ALL of its target views". So the audit unit is the event + the union of views
    that source it. sources:-seeded legacy-assumed pairs + optional absorbed-without-
    source injection pairs (GOLD-1 F14 list; audited identically per the F14 fix)."""
    if staleness is None:
        raise RuntimeError("staleness.py unavailable")
    ledger = set(staleness.load_ledger(root))
    pop = {}
    wiki = os.path.join(root, "wiki")
    for dr, _ds, fs in os.walk(wiki):
        if os.sep + ".git" in dr:
            continue
        for f in sorted(fs):
            if not f.endswith(".md"):
                continue
            rel = os.path.relpath(os.path.join(dr, f), root).replace("\\", "/")
            if not staleness._is_view(rel):
                continue
            try:
                with open(os.path.join(dr, f), "r", encoding="utf-8-sig") as fh:
                    _st, data = staleness.split_frontmatter(fh.read())
            except OSError:
                continue
            if not isinstance(data, dict):
                continue
            for e in (data.get("sources") or []):
                if isinstance(e, str) and e.replace("\\", "/") in ledger:
                    pop.setdefault(e.replace("\\", "/"), set()).add(rel)
    for pair in (injection_pairs or []):
        v, e = pair["view"].replace("\\", "/"), pair["event"].replace("\\", "/")
        pop.setdefault(e, set()).add(v)
    return {e: sorted(vs) for e, vs in pop.items()}


# --------------------------------------------------------------------------- planted
def load_planted_spec(path):
    """A committed mutation spec: {view, event, class, description, edits:[{find,replace}]}."""
    if yaml is None:
        raise RuntimeError("PyYAML unavailable")
    with open(path, "r", encoding="utf-8-sig") as fh:
        spec = yaml.safe_load(fh.read()) or {}
    for k in ("view", "event", "edits"):
        if not spec.get(k):
            raise ValueError("planted spec %s missing key: %s" % (path, k))
    return spec


def apply_planted(view_text, spec):
    """Apply the spec's ordered edits. Each `find` must occur EXACTLY once in the current
    text (drift guard: the fixture must break loudly if the view moved under the spec).
    Returns mutated text; raises ValueError on drift."""
    text = view_text
    for i, ed in enumerate(spec["edits"]):
        find = ed.get("find", "")
        n = text.count(find) if find else 0
        if n != 1:
            raise ValueError("planted-spec drift: edit %d `find` occurs %d times (need "
                             "exactly 1) -- re-derive the spec against the current view"
                             % (i, n))
        text = text.replace(find, ed.get("replace", "") or "", 1)
    return text


# --------------------------------------------------------------------------- packets
def _leg_id(event_rel, salt, planted=False):
    key = "%s|%s|%s" % (event_rel, "planted" if planted else "regular", salt)
    return "leg-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


def _read(root, rel):
    with open(os.path.join(root, rel.replace("/", os.sep)), "r",
              encoding="utf-8-sig") as fh:
        return fh.read()


def build_packet(leg_id, event_rel, event_text, views):
    """views = [(view_rel, view_text), ...]. Deterministic, no timestamps."""
    parts = ["# CONTENT AUDIT PACKET %s" % leg_id, "", PACKET_CONTRACT, "",
             "## EVENT: %s" % event_rel, "", "```markdown",
             event_text.rstrip("\n"), "```", ""]
    for i, (vrel, vtext) in enumerate(views, 1):
        parts += ["## VIEW %d of %d: %s" % (i, len(views), vrel), "",
                  "```markdown", vtext.rstrip("\n"), "```", ""]
    return "\n".join(parts)


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _assert_out_safe(root, out):
    """--prepare writes ONLY under --out; refuse an out dir inside the tree's content
    surfaces (wiki/ or raw/) so the harness can never mutate what it audits."""
    out_abs = os.path.abspath(out).replace("\\", "/") + "/"
    for surface in ("wiki", "raw"):
        s = os.path.abspath(os.path.join(root, surface)).replace("\\", "/") + "/"
        if out_abs.startswith(s):
            raise SystemExit("REFUSED -- --out %s is inside the tree's %s/ content "
                             "surface (the harness is read-only over the tree)"
                             % (out, surface))


def prepare(root, out, planted_specs, events=None, injection_json=None):
    if yaml is None:
        print("RESULT: INCONCLUSIVE -- PyYAML unavailable")
        return 2
    _assert_out_safe(root, out)
    injection = []
    if injection_json and os.path.isfile(injection_json):
        with open(injection_json, "r", encoding="utf-8") as fh:
            injection = (json.load(fh) or {}).get("injection_list", [])
    pop = enumerate_population(root, injection)
    if events:
        missing = [e for e in events if e not in pop]
        if missing:
            print("RESULT: INCONCLUSIVE -- requested event(s) not in the audit "
                  "population: %s" % ", ".join(missing))
            return 2
        pop = {e: pop[e] for e in events}
    if not planted_specs:
        print("RESULT: INCONCLUSIVE -- a batch without a planted-defect leg is invalid "
              "(tp:350(c)); pass --planted SPEC")
        return 2

    p = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                       capture_output=True, text=True)
    salt = (p.stdout or "no-sha").strip()

    os.makedirs(os.path.join(out, "packets"), exist_ok=True)
    os.makedirs(os.path.join(out, "verdicts"), exist_ok=True)
    legs = []

    def emit(leg_id, event_rel, views_texts, views, planted, spec_path):
        packet = build_packet(leg_id, event_rel, _read(root, event_rel), views_texts)
        rel_packet = "packets/%s.md" % leg_id
        fp = os.path.join(out, rel_packet.replace("/", os.sep))
        with open(fp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(packet)
        legs.append({"id": leg_id, "event": event_rel, "views": views,
                     "planted": planted, "spec": spec_path,
                     "packet": rel_packet, "packet_sha256": _sha256_file(fp),
                     "claim": CLAIM_TEMPLATE % leg_id})

    full_pop = enumerate_population(root, injection)
    planted_events = set()
    mutated_views = set()
    for spec_path in planted_specs:
        spec = load_planted_spec(spec_path)
        vrel = spec["view"].replace("\\", "/")
        erel = spec["event"].replace("\\", "/")
        planted_events.add(erel)
        mutated_views.add(vrel)
        try:
            mutated = apply_planted(_read(root, vrel), spec)
        except ValueError as e:
            print("RESULT: INCONCLUSIVE -- %s" % e)
            return 2
        views = full_pop.get(erel) or [vrel]
        vt = [(v, mutated if v == vrel else _read(root, v)) for v in views]
        # absence probes (added after the 2026-07-03 invalid-fixture miss): the planted
        # claim must be absent from EVERY view in the packet post-mutation -- a claim
        # restated in a sibling view is not a defect under the disjunctive property, and
        # a fixture built on one silently invalidates the batch's efficacy floor
        for probe in (spec.get("absence_probes") or []):
            hits = [v for v, t in vt if probe.lower() in t.lower()]
            if hits:
                print("RESULT: INCONCLUSIVE -- planted spec %s: absence probe %r still "
                      "present post-mutation in: %s (the planted claim must be absent "
                      "from ALL packet views)" % (spec_path, probe, ", ".join(hits)))
                return 2
        emit(_leg_id(erel, salt, planted=True), erel, vt, views, True,
             spec_path.replace("\\", "/"))

    deferred = []
    for erel, views in pop.items():
        if erel in planted_events:
            continue
        if any(v in mutated_views for v in views):
            # this event's packet would embed the UNMUTATED text of a view another leg
            # ships mutated -- the twin would unblind the planted leg; audit it in a
            # later batch instead
            deferred.append(erel)
            continue
        emit(_leg_id(erel, salt), erel, [(v, _read(root, v)) for v in views],
             views, False, None)

    legs.sort(key=lambda x: x["id"])   # hash order == blinded order
    # the FULL population's view->events map, so ingest can roll up view clearance
    # honestly: a view clears only when EVERY event sourcing it (population-wide, not
    # batch-wide) has a passing audit
    view_totals = {}
    for erel, views in full_pop.items():
        for v in views:
            view_totals.setdefault(v, []).append(erel)
    manifest = {"root_sha": salt, "root": os.path.abspath(root).replace("\\", "/"),
                "absorb_vendor": ABSORB_VENDOR, "absorb_model_id": ABSORB_MODEL_ID,
                "deferred_events": sorted(deferred),
                "view_event_totals": {v: sorted(es) for v, es in view_totals.items()},
                "legs": legs}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8",
              newline="\n") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
    n_p = sum(1 for x in legs if x["planted"])
    print("prepared %d leg(s) (%d planted, %d regular; %d deferred to keep the planted "
          "view blinded) @ %s -> %s"
          % (len(legs), n_p, len(legs) - n_p, len(deferred), salt[:12], out))
    print("blinding: planted flags live in manifest.json ONLY -- do not ship it to the "
          "verifier")
    return 0


# --------------------------------------------------------------------------- fire
SPEND_CAP_LEGS = 25   # Appendix K (operator-confirmed 2026-07-03): default legs/batch;
#                       a bigger run needs --operator-authorized-full-run (explicit
#                       operator authorization, cited in the kickoff receipt)

FULL_RUN_ARTIFACT_CLASS = "deploy/evidence/operator-*.md"


def operator_full_run_authorization(args):
    """Resolve --operator-authorized-full-run to a VALIDATED operator-
    artifact path, or refuse. The flag now takes a value: the repo-relative
    path of a committed operator authorization artifact of the HUMAN-GATE
    class deploy/evidence/operator-*.md. Structural class check (the
    compile-backends.py _is_authorization_artifact_class precedent): the
    path is normalized to POSIX, split into (directory, basename); the
    directory must equal exactly "deploy/evidence" (string equality -- no
    nesting, no traversal) and the basename must fnmatch operator-*.md;
    the file must exist at that relative path. Returns (path, None) on
    success, (None, None) when the flag is absent, (None, reason) when
    the flag is present but invalid -- callers must REFUSE on a reason,
    never downgrade to an unauthorized run silently."""
    flag = "--operator-authorized-full-run"
    if flag not in args:
        return None, None
    i = args.index(flag)
    if i + 1 >= len(args) or args[i + 1].startswith("--"):
        return None, "no value given (missing or looks like another flag)"
    raw = args[i + 1]
    if os.path.isabs(raw):
        return None, "path must be repo-relative, not absolute: %s" % raw
    rel = raw.replace("\\", "/")
    directory, basename = posixpath.split(rel)
    if directory != "deploy/evidence" or not fnmatch.fnmatchcase(basename,
                                                                  "operator-*.md"):
        return None, "path is not in the operator-artifact class: %s" % raw
    if not os.path.isfile(rel):
        return None, "artifact not found: %s" % raw
    # v3.0-120 (brief sections 4-5): "committed" is CHECKED, never claimed. The
    # artifact must be committed-identical (always) and operator-signed (refused
    # under project.yaml trust_surface_signing: required; surfaced under warn).
    gate = _trust_gate(rel)
    if not gate["ok"]:
        return None, gate["refuse"]
    for w in gate["warnings"]:
        print("WARNING (trust-surface): %s" % w)
    return rel, None


def _trust_gate(rel, root=None):
    """deploy/trust.py gate_artifact against the enclosing git repo (cwd-relative
    paths are how this CLI addresses artifacts). Not a git repo -> refused: trust
    state IS git state."""
    if root is None:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        if p.returncode != 0:
            return {"ok": False, "refuse": "not inside a git repository -- an "
                    "authorization artifact's committed state cannot be checked",
                    "warnings": [], "mode": None}
        root = p.stdout.strip()
    spec = importlib.util.spec_from_file_location(
        "trust_audit_ref", os.path.join(_HERE, "trust.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rel_posix = os.path.relpath(os.path.abspath(rel), root).replace(os.sep, "/")
    return mod.gate_artifact(root, rel_posix)


def fire(batch, timeout_ms=0, effort="", shard=None, full_run_authorized=False):
    """Fire the bridge verifier for every leg without a verdict file (resumable). The
    verifier substrate comes back in the verdict JSON's `verifier` field -- the
    invocation-metadata channel (F17); this runner never writes substrate strings.

    SPEND CAP (Appendix K, the mechanical kill): refuses to fire more than
    SPEND_CAP_LEGS unanswered legs in one invocation unless the operator's explicit
    authorization is passed (--operator-authorized-full-run). The cap counts what THIS
    invocation would fire (shards count their own slice).

    shard=(k, n): fire only legs whose manifest index % n == k -- lets N workers run
    the same batch concurrently without double-firing (resumability still applies)."""
    with open(os.path.join(batch, "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    bridge = os.environ.get("CROSS_VENDOR_BRIDGE_DIR") or os.path.join(
        manifest["root"], ".claude", "skills", "bridge")
    cli = os.path.join(bridge, "verify-cli.js")
    if not os.path.isfile(cli):
        print("RESULT: INCONCLUSIVE -- bridge not found at %s" % cli)
        return 2
    legs = manifest["legs"]
    if shard:
        k, n = shard
        legs = [x for i, x in enumerate(legs) if i % n == k]

    def _unanswered(leg):
        vp = os.path.join(batch, "verdicts", leg["id"] + ".json")
        return not (os.path.isfile(vp) and os.path.getsize(vp) > 0)

    to_fire = [x for x in legs if _unanswered(x)]
    if len(to_fire) > SPEND_CAP_LEGS and not full_run_authorized:
        print("REFUSED -- %d unanswered leg(s) exceeds the Appendix-K spend cap of %d "
              "legs per invocation. Re-run with --operator-authorized-full-run only on "
              "explicit operator authorization (recorded in the kickoff receipt)."
              % (len(to_fire), SPEND_CAP_LEGS))
        return 2
    fired = skipped = failed = 0
    for leg in legs:
        vpath = os.path.join(batch, "verdicts", leg["id"] + ".json")
        if os.path.isfile(vpath) and os.path.getsize(vpath) > 0:
            skipped += 1
            continue
        args = ["node", cli, "--claim", leg["claim"],
                "--evidence-file", os.path.join(batch, leg["packet"].replace("/", os.sep)),
                "--tier", "T2"]
        if timeout_ms:
            args += ["--timeout-ms", str(timeout_ms)]
        if effort:
            args += ["--effort", effort]
        print("firing %s (%s)..." % (leg["id"], leg["event"]))
        # the bridge emits UTF-8; never let Windows' cp1252 default kill the reader thread
        proc = subprocess.run(args, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0 or not (proc.stdout or "").strip():
            print("  FAILED rc=%d: %s" % (proc.returncode,
                                          (proc.stderr or "").strip()[-300:]))
            failed += 1
            continue
        with open(vpath, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(proc.stdout.strip())
        fired += 1
        print("  verdict saved -> %s" % vpath)
    print("fire: %d fired, %d already-answered, %d failed" % (fired, skipped, failed))
    return 0 if failed == 0 else 2


# --------------------------------------------------------------------------- ingest
def _substrate_record(leg, verdict, manifest):
    """The structured VERIFY-form record for one leg (tp:350(b)). substrate_source is set
    to invocation-metadata ONLY when the verdict JSON itself carries the verifier's
    vendor+model (the bridge populates them from the actual invocation, F17); anything
    else fails closed downstream."""
    v = (verdict or {}).get("verifier") or {}
    rec = {"verifier_vendor": v.get("vendor") or "",
           "verifier_model_id": v.get("model") or "",
           "absorb_vendor": manifest.get("absorb_vendor", ""),
           "absorb_model_id": manifest.get("absorb_model_id", ""),
           "packet_hash": leg.get("packet_sha256", ""),
           "artifact": "verdicts/%s.json" % leg["id"]}
    if rec["verifier_vendor"] and rec["verifier_model_id"]:
        rec["substrate_source"] = "invocation-metadata"
    return rec


def ingest(batch):
    mpath = os.path.join(batch, "manifest.json")
    if not os.path.isfile(mpath):
        print("RESULT: INCONCLUSIVE -- no manifest.json in %s" % batch)
        return 2
    with open(mpath, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    legs = manifest.get("legs", [])
    planted = [x for x in legs if x.get("planted")]
    regular = [x for x in legs if not x.get("planted")]
    if not planted:
        print("RESULT: INCONCLUSIVE -- batch contains no planted-defect leg (tp:350(c): "
              "a content-audit batch that omits the planted-defect case is INCONCLUSIVE)")
        return 2

    os.makedirs(os.path.join(batch, "records"), exist_ok=True)
    substrate_fails = []
    rows = []
    for leg in legs:
        vpath = os.path.join(batch, "verdicts", leg["id"] + ".json")
        verdict = None
        if os.path.isfile(vpath):
            try:
                with open(vpath, "r", encoding="utf-8") as fh:
                    verdict = json.load(fh)
            except ValueError:
                verdict = None
        if verdict is None:
            # CASCADE record (oversized packets): a leg whose packet exceeds the
            # verifier's input cap is audited in documented stages -- a view-SUBSET leg
            # first (confirmation on a subset is SOUND for the disjunctive property: a
            # claim in >=1 view of a subset is in >=1 view of the full set), plus a
            # residual leg (stage-1's missing claims x the held-out views) only on
            # rejection. cascade/<id>.json = {verdict, uncertainty, verifier, stages:
            # [verdict-file paths]}; every referenced stage verdict must exist (else
            # the cascade record is ignored) and each stage substrate-gates like any
            # leg. The combined verdict is the cascade runner's recorded conclusion --
            # transparent, with all raw stage verdicts kept.
            cpath = os.path.join(batch, "cascade", leg["id"] + ".json")
            if os.path.isfile(cpath):
                try:
                    with open(cpath, "r", encoding="utf-8") as fh:
                        casc = json.load(fh)
                    stage_files = [os.path.join(batch, s) for s in
                                   (casc.get("stages") or [])]
                    if stage_files and all(os.path.isfile(s) for s in stage_files):
                        verdict = casc
                except ValueError:
                    verdict = None
        rec = _substrate_record(leg, verdict, manifest)
        vv = (verdict or {}).get("verdict")
        row = {"id": leg["id"], "event": leg["event"], "views": leg.get("views", []),
               "planted": bool(leg.get("planted")),
               "verdict": vv, "uncertainty": (verdict or {}).get("uncertainty"),
               "reason": (verdict or {}).get("reason"), "substrate": rec}
        if verdict is not None:
            ok_form, why = _substrate.verified_block_wellformed(rec)
            ok_prov = _substrate.substrate_derived_from_invocation(rec)
            ok_gate = _substrate.substrate_gate_ok(
                rec["absorb_vendor"], rec["absorb_model_id"],
                rec["verifier_vendor"], rec["verifier_model_id"], _substrate.MIGRATION)
            if not (ok_form and ok_prov and ok_gate):
                substrate_fails.append(
                    "%s: %s" % (leg["id"],
                                "; ".join(w for w, ok in
                                          [(why, ok_form),
                                           ("substrate not invocation-derived (F17)", ok_prov),
                                           ("vendor gate: verifier %r vs absorb %r must "
                                            "differ (MIGRATION firewall)"
                                            % (rec["verifier_vendor"], rec["absorb_vendor"]),
                                            ok_gate)] if not ok)))
        rows.append(row)
        with open(os.path.join(batch, "records", leg["id"] + ".json"), "w",
                  encoding="utf-8", newline="\n") as fh:
            json.dump(row, fh, indent=2, sort_keys=True)

    # gate 1: substrate (wrong substrate -> INCONCLUSIVE, tp:352)
    if substrate_fails:
        for s in substrate_fails:
            print("SUBSTRATE FAIL %s" % s)
        print("RESULT: INCONCLUSIVE -- %d leg(s) fail the LLM-5 MIGRATION substrate gate"
              % len(substrate_fails))
        return 2

    # gate 2: decisive floor >= 4/5 over the regular legs (revised/missing = indecisive)
    decisive = [r for r in rows if not r["planted"] and r["verdict"] in
                ("confirmed", "rejected")]
    if regular and (len(decisive) / float(len(regular))) < 0.8:
        print("RESULT: INCONCLUSIVE -- decisive verdicts on %d/%d regular legs (< 4/5 "
              "floor); re-fire the indecisive legs" % (len(decisive), len(regular)))
        return 2

    # gate 3: the planted-defect efficacy floor -- every planted leg must be CAUGHT
    missed = [r for r in rows if r["planted"] and r["verdict"] != "rejected"]
    caught = [r for r in rows if r["planted"] and r["verdict"] == "rejected"]

    audit_fails = [r for r in rows if not r["planted"] and r["verdict"] == "rejected"]
    passes = [r for r in rows if not r["planted"] and r["verdict"] == "confirmed"]

    # view-level roll-up. BLOCKED: any failing/indecisive event blocks every view that
    # sources it (conservative -- the missing claim's proper home is a judgment call the
    # operator makes at re-absorb time). CLEARED: a view clears only when EVERY event
    # sourcing it in the FULL population passed -- a batch that audits one of a view's
    # five events must NOT report the view cleared (planted legs never clear anything).
    # Views neither cleared nor blocked are PENDING (events not yet audited).
    blocked_views = sorted({v for r in rows
                            if not r["planted"] and r["verdict"] != "confirmed"
                            for v in r["views"]})
    passed_events = {r["event"] for r in passes}
    totals = manifest.get("view_event_totals") or {}
    cleared_views = sorted(
        v for v in {vv for r in passes for vv in r["views"]}
        if v not in blocked_views
        and totals.get(v) and set(totals[v]) <= passed_events)

    result = {"legs": len(legs), "planted_caught": len(caught),
              "planted_missed": len(missed),
              "events_pass": sorted(r["event"] for r in passes),
              "events_audit_fail": sorted(r["event"] for r in audit_fails),
              "indecisive": sorted(r["event"] for r in rows
                                   if not r["planted"] and r["verdict"] not in
                                   ("confirmed", "rejected")),
              "views_cleared": cleared_views, "views_blocked": blocked_views,
              "deferred_events": manifest.get("deferred_events", []),
              "batch_accepted": not missed,
              "absorb": {"vendor": manifest.get("absorb_vendor"),
                         "model_id": manifest.get("absorb_model_id")},
              "root_sha": manifest.get("root_sha")}
    with open(os.path.join(batch, "batch-result.json"), "w", encoding="utf-8",
              newline="\n") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)

    print("=" * 72)
    print("F13 CONTENT AUDIT -- batch @ %s" % str(result["root_sha"])[:12])
    print("=" * 72)
    for r in rows:
        print("  %-14s %-52s %s%s" % (r["id"], r["event"], r["verdict"],
                                      "  [PLANTED]" if r["planted"] else ""))
    print("-" * 72)
    print("planted: %d caught / %d missed · regular events: %d pass / %d audit-fail / "
          "%d indecisive · views: %d cleared / %d blocked"
          % (len(caught), len(missed), len(passes), len(audit_fails),
             len(result["indecisive"]), len(cleared_views), len(blocked_views)))
    if missed:
        print("RESULT: FAIL -- planted-defect leg NOT caught (efficacy floor, tp:350(c)):"
              " the batch is NOT accepted; nothing is cleared by it.")
        return 1
    if audit_fails:
        print("RESULT: FAIL -- batch accepted (floor ok) but %d event(s) FAILED the "
              "content audit; every view sourcing them stays blocked:"
              % len(audit_fails))
        for r in audit_fails:
            print("  - %s (views: %s; see records/%s.json)"
                  % (r["event"], ", ".join(r["views"]), r["id"]))
        return 1
    print("RESULT: PASS -- batch accepted; %d event(s) pass; %d view(s) cleared "
          "(candidates for verified-consumed at P1-live seeding)."
          % (len(passes), len(cleared_views)))
    return 0


# --------------------------------------------------------------------------- self-test
def _mk_fixture_tree(base):
    """e1 -> v1 only (planted target); e2 -> v2; e3 -> v2+v3 with its claims
    DISTRIBUTED across the two siblings (the batched-compile shape that broke the
    per-view audit unit -- the event-centric packet must embed both views).

    P5 NOTE (2026-07-06, adjudication 6 / drill-replay-bench precedent at 33204bd):
    staleness.load_ledger defaults to enlarged=True, which requires a registration
    record for every ledger member (registrations.load_registrations, full coverage,
    no exemptions). This fixture tree therefore mints a REAL chained registration for
    each of its 3 raw/ events via registrations.append_registration (the canonical
    sec.6 chain, never hand-rolled JSON) and git-inits the tree (the seq lock lives
    at the git common dir, same as drill-replay-bench's synthetic corpora). Do NOT
    pass enlarged=False to sidestep this -- that would fixture-test a retired code
    path instead of the one every real audit run actually exercises."""
    raw = os.path.join(base, "raw")
    wiki = os.path.join(base, "wiki", "topic")
    os.makedirs(raw)
    os.makedirs(wiki)

    def w(path, text):
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    w(os.path.join(raw, "2026-01-01-e1.md"),
      "---\ndate: 2026-01-01\ntags: [alpha]\n---\nThe launch price is $49/mo.\n"
      "The refund window is 30 days.\n")
    w(os.path.join(raw, "2026-01-02-e2.md"),
      "---\ndate: 2026-01-02\ntags: [beta]\n---\nThe beta cap is 200 seats.\n")
    w(os.path.join(raw, "2026-01-03-e3.md"),
      "---\ndate: 2026-01-03\ntags: [beta, gamma]\n---\nThe beta opens 2026-02-01.\n"
      "The gamma budget is $10k.\n")
    w(os.path.join(wiki, "v1.md"),
      "---\ntitle: V1\nsources:\n  - raw/2026-01-01-e1.md\n---\n# V1\n"
      "Pricing decision: the launch price is $49/mo.\n"
      "Refund policy: the refund window is 30 days.\n")
    w(os.path.join(wiki, "v2.md"),
      "---\ntitle: V2\nsources:\n  - raw/2026-01-02-e2.md\n  - raw/2026-01-03-e3.md\n"
      "---\n# V2\nThe beta cap is 200 seats.\nThe beta opens 2026-02-01.\n")
    w(os.path.join(wiki, "v3.md"),
      "---\ntitle: V3\nsources:\n  - raw/2026-01-03-e3.md\n---\n# V3\n"
      "The gamma budget is $10k.\n")
    w(os.path.join(wiki, "INDEX.md"), "---\ntitle: idx\n---\nprojection\n")
    spec_path = os.path.join(base, "planted-v1.yaml")
    w(spec_path,
      "view: wiki/topic/v1.md\nevent: raw/2026-01-01-e1.md\nclass: d1-dropped-clause\n"
      "description: drop the refund-window claim\nedits:\n  - find: |\n"
      "      Refund policy: the refund window is 30 days.\n    replace: \"\"\n")
    subprocess.run(["git", "init", "-q", base], capture_output=True)
    subprocess.run(["git", "-C", base, "config", "user.email", "t@t"],
                   capture_output=True)
    subprocess.run(["git", "-C", base, "config", "user.name", "t"],
                   capture_output=True)

    # P5: mint one real chained registration per fixture raw/ event (adjudication 6 /
    # drill-replay-bench precedent) -- required because staleness.load_ledger's
    # enlarged=True default demands full ledger-member registration coverage.
    if registrations is not None:
        now = "2026-01-01T00:00:00"
        for rel in ("raw/2026-01-01-e1.md", "raw/2026-01-02-e2.md",
                    "raw/2026-01-03-e3.md"):
            registrations.append_registration(base, {
                "kind": "registration", "event": rel, "origin": "corpus",
                "origin_evidence": "audit-content fixture-tree event",
                "event_class": "session", "event_class_origin": "judgment",
                "asserts_corpus_state": False, "registered_at": now,
            })
    return spec_path


def _write_verdict(batch, leg_id, verdict, vendor="openai", model="gpt-5.5",
                   drop_verifier=False):
    v = {"verdict": verdict, "uncertainty": "confident"}
    if not drop_verifier:
        v["verifier"] = {"vendor": vendor, "model": model}
    with open(os.path.join(batch, "verdicts", leg_id + ".json"), "w",
              encoding="utf-8", newline="\n") as fh:
        json.dump(v, fh)


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

    if yaml is None or staleness is None:
        print("RESULT: INCONCLUSIVE -- PyYAML/staleness unavailable")
        return 2

    base = tempfile.mkdtemp(prefix="f13-selftest-")
    try:
        spec_path = _mk_fixture_tree(base)

        # population (event-centric)
        pop = enumerate_population(base)
        case("population: 3 events, keyed by event, projection excluded",
             set(pop) == {"raw/2026-01-01-e1.md", "raw/2026-01-02-e2.md",
                          "raw/2026-01-03-e3.md"})
        case("population: multi-target event lists ALL sibling views",
             pop["raw/2026-01-03-e3.md"] == ["wiki/topic/v2.md", "wiki/topic/v3.md"])
        case("population: injection pair merges in",
             "wiki/topic/v9.md" in enumerate_population(
                 base, [{"view": "wiki/topic/v9.md",
                         "event": "raw/2026-01-02-e2.md"}])["raw/2026-01-02-e2.md"])

        # planted spec + drift guard
        spec = load_planted_spec(spec_path)
        mut = apply_planted(_read(base, "wiki/topic/v1.md"), spec)
        case("planted: load-bearing clause removed", "refund window" not in mut
             and "$49/mo" in mut)
        try:
            apply_planted(mut, spec)
            case("planted: drift guard trips when find-text absent", False)
        except ValueError:
            case("planted: drift guard trips when find-text absent", True)

        # out-dir containment
        try:
            _assert_out_safe(base, os.path.join(base, "wiki", "out"))
            case("prepare refuses --out inside wiki/", False)
        except SystemExit:
            case("prepare refuses --out inside wiki/", True)

        # prepare (planted + regulars, blinded)
        out = os.path.join(base, "batch")
        rc = prepare(base, out, [spec_path])
        case("prepare rc=0", rc == 0)
        with open(os.path.join(out, "manifest.json"), "r", encoding="utf-8") as fh:
            man = json.load(fh)
        legs = man["legs"]
        case("prepare: 3 legs (planted e1 + regular e2, e3), keyed by event",
             len(legs) == 3 and sum(1 for x in legs if x["planted"]) == 1)
        pleg = next(x for x in legs if x["planted"])
        rleg = next(x for x in legs if not x["planted"]
                    and x["event"] == "raw/2026-01-02-e2.md")
        mleg = next(x for x in legs if x["event"] == "raw/2026-01-03-e3.md")
        case("multi-target leg packet embeds BOTH sibling views",
             all(s in open(os.path.join(out, mleg["packet"].replace("/", os.sep)),
                           encoding="utf-8").read()
                 for s in ("VIEW 1 of 2", "VIEW 2 of 2", "The gamma budget is $10k.",
                           "The beta opens 2026-02-01.")))
        ptext = open(os.path.join(out, pleg["packet"].replace("/", os.sep)),
                     encoding="utf-8").read()
        case("blinding: packet body/filename never say 'planted'",
             "planted" not in ptext.lower() and "planted" not in pleg["packet"].lower()
             and "planted" not in pleg["id"].lower())
        case("planted packet: claim present in EVENT section, absent from VIEW section",
             "The refund window is 30 days." in ptext.split("## VIEW")[0]
             and "refund window" not in ptext.split("## VIEW")[1])

        # absence-probe guard: a spec whose "removed" claim survives in a sibling view
        # is an invalid fixture -> INCONCLUSIVE at prepare, never a silent floor break
        spec_ap = os.path.join(base, "planted-e3-bad.yaml")
        with open(spec_ap, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("view: wiki/topic/v3.md\nevent: raw/2026-01-03-e3.md\n"
                     "class: d1-dropped-clause\ndescription: drop a claim e3 restates "
                     "in v2\nabsence_probes:\n  - \"beta opens 2026-02-01\"\nedits:\n"
                     "  - find: |\n      The gamma budget is $10k.\n    replace: \"\"\n")
        case("prepare: absence probe finds the claim in a sibling view -> "
             "INCONCLUSIVE (2)",
             prepare(base, os.path.join(base, "batch-ap"), [spec_ap]) == 2)

        # blinding twin-exclusion: an event sharing the mutated view is deferred
        out_tw = os.path.join(base, "batch-twin")
        spec_tw = os.path.join(base, "planted-v2.yaml")
        with open(spec_tw, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("view: wiki/topic/v2.md\nevent: raw/2026-01-02-e2.md\n"
                     "class: d1-dropped-clause\ndescription: drop the beta cap\n"
                     "edits:\n  - find: |\n      The beta cap is 200 seats.\n"
                     "    replace: \"\"\n")
        rc = prepare(base, out_tw, [spec_tw])
        with open(os.path.join(out_tw, "manifest.json"), encoding="utf-8") as fh:
            man_tw = json.load(fh)
        case("twin-exclusion: e3 (also sourcing the mutated v2) is deferred, not "
             "shipped unmutated",
             man_tw["deferred_events"] == ["raw/2026-01-03-e3.md"]
             and all(x["event"] != "raw/2026-01-03-e3.md" for x in man_tw["legs"]))

        # ingest gates -- all-good
        _write_verdict(out, pleg["id"], "rejected")
        _write_verdict(out, rleg["id"], "confirmed")
        _write_verdict(out, mleg["id"], "confirmed")
        case("ingest: planted caught + regulars confirmed -> PASS (0)",
             ingest(out) == 0)
        with open(os.path.join(out, "batch-result.json"), encoding="utf-8") as fh:
            res = json.load(fh)
        case("result: events pass + views cleared roll-up, batch accepted",
             res["events_pass"] == ["raw/2026-01-02-e2.md", "raw/2026-01-03-e3.md"]
             and res["views_cleared"] == ["wiki/topic/v2.md", "wiki/topic/v3.md"]
             and res["batch_accepted"])
        case("record: structured VERIFY form (F18 fields + F17 marker + packet hash)",
             json.load(open(os.path.join(out, "records", rleg["id"] + ".json"),
                            encoding="utf-8"))["substrate"]["substrate_source"]
             == "invocation-metadata")

        # planted missed -> efficacy failure (1)
        _write_verdict(out, pleg["id"], "confirmed")
        case("ingest: planted MISSED -> efficacy FAIL (1)", ingest(out) == 1)
        _write_verdict(out, pleg["id"], "rejected")

        # regular event genuinely failing -> 1, and BOTH its views blocked
        _write_verdict(out, mleg["id"], "rejected")
        case("ingest: regular event rejected -> audit-fail (1)", ingest(out) == 1)
        with open(os.path.join(out, "batch-result.json"), encoding="utf-8") as fh:
            res = json.load(fh)
        case("failing event blocks EVERY view sourcing it (conservative roll-up)",
             res["views_blocked"] == ["wiki/topic/v2.md", "wiki/topic/v3.md"]
             and res["views_cleared"] == [])
        _write_verdict(out, mleg["id"], "confirmed")

        # decisive floor: 1 of 2 regulars revised -> 1/2 < 4/5 -> INCONCLUSIVE
        _write_verdict(out, rleg["id"], "revised")
        case("ingest: indecisive regulars below 4/5 floor -> INCONCLUSIVE (2)",
             ingest(out) == 2)
        _write_verdict(out, rleg["id"], "confirmed")

        # substrate gates
        _write_verdict(out, rleg["id"], "confirmed", vendor="anthropic",
                       model="claude-sonnet-5")
        case("ingest: same-vendor verifier -> INCONCLUSIVE (MIGRATION firewall)",
             ingest(out) == 2)
        _write_verdict(out, rleg["id"], "confirmed", drop_verifier=True)
        case("ingest: verdict without invocation verifier metadata -> INCONCLUSIVE "
             "(F17 fail-closed)", ingest(out) == 2)
        _write_verdict(out, rleg["id"], "confirmed")

        # no-planted batch -> INCONCLUSIVE
        man2 = dict(man, legs=[x for x in legs if not x["planted"]])
        out2 = os.path.join(base, "batch2")
        os.makedirs(os.path.join(out2, "verdicts"))
        with open(os.path.join(out2, "manifest.json"), "w", encoding="utf-8",
                  newline="\n") as fh:
            json.dump(man2, fh)
        case("ingest: batch without a planted leg -> INCONCLUSIVE (2)",
             ingest(out2) == 2)

        # missing verdict file counts as indecisive (not a crash)
        out3 = os.path.join(base, "batch3")
        rc = prepare(base, out3, [spec_path])
        _write_verdict(out3, pleg["id"], "rejected")
        case("ingest: missing regular verdicts -> indecisive -> INCONCLUSIVE (2)",
             ingest(out3) == 2)

        # Appendix-K spend cap + shard slicing (hermetic: refusal happens before any
        # bridge invocation; a dummy bridge file satisfies the existence check)
        bridge_d = os.path.join(base, ".claude", "skills", "bridge")
        os.makedirs(bridge_d, exist_ok=True)
        with open(os.path.join(bridge_d, "verify-cli.js"), "w", encoding="utf-8") as fh:
            fh.write("// dummy\n")
        for f in os.listdir(os.path.join(out, "verdicts")):
            os.remove(os.path.join(out, "verdicts", f))
        import contextlib
        import io
        global SPEND_CAP_LEGS
        cap_saved = SPEND_CAP_LEGS
        try:
            SPEND_CAP_LEGS = 0   # everything over the cap -> refusal is pre-invocation
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc_full = fire(out)
            case("fire: unanswered legs over the cap -> REFUSED (2), nothing fired",
                 rc_full == 2 and "3 unanswered" in buf.getvalue()
                 and "REFUSED" in buf.getvalue())
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc_shard = fire(out, shard=(0, 3))
            case("fire: shard 0/3 counts only its own slice against the cap",
                 rc_shard == 2 and "1 unanswered" in buf.getvalue())
        finally:
            SPEND_CAP_LEGS = cap_saved
        _write_verdict(out, pleg["id"], "rejected")
        _write_verdict(out, rleg["id"], "confirmed")
        _write_verdict(out, mleg["id"], "confirmed")

        # cascade record: an oversized leg with a documented stage record + existing
        # stage verdicts counts as answered; a cascade citing a MISSING stage file is
        # ignored (leg stays indecisive)
        os.remove(os.path.join(out, "verdicts", mleg["id"] + ".json"))
        os.makedirs(os.path.join(out, "cascade"), exist_ok=True)
        _write_verdict(out, "stage1-" + mleg["id"], "confirmed")
        with open(os.path.join(out, "cascade", mleg["id"] + ".json"), "w",
                  encoding="utf-8", newline="\n") as fh:
            json.dump({"verdict": "confirmed", "uncertainty": "confident",
                       "verifier": {"vendor": "openai", "model": "gpt-5.5"},
                       "stages": ["verdicts/stage1-%s.json" % mleg["id"]]}, fh)
        case("cascade: documented stage record answers an oversized leg -> PASS (0)",
             ingest(out) == 0)
        with open(os.path.join(out, "cascade", mleg["id"] + ".json"), "w",
                  encoding="utf-8", newline="\n") as fh:
            json.dump({"verdict": "confirmed", "uncertainty": "confident",
                       "verifier": {"vendor": "openai", "model": "gpt-5.5"},
                       "stages": ["verdicts/DOES-NOT-EXIST.json"]}, fh)
        case("cascade: record citing a missing stage verdict is IGNORED -> "
             "INCONCLUSIVE (2)", ingest(out) == 2)
        os.remove(os.path.join(out, "cascade", mleg["id"] + ".json"))
        _write_verdict(out, mleg["id"], "confirmed")

        # partial batch: a passing event must NOT clear a view whose OTHER events are
        # unaudited (population-wide roll-up honesty)
        out4 = os.path.join(base, "batch4")
        rc = prepare(base, out4, [spec_path], events=["raw/2026-01-02-e2.md"])
        with open(os.path.join(out4, "manifest.json"), encoding="utf-8") as fh:
            man4 = json.load(fh)
        p4 = next(x for x in man4["legs"] if x["planted"])
        r4 = next(x for x in man4["legs"] if not x["planted"])
        _write_verdict(out4, p4["id"], "rejected")
        _write_verdict(out4, r4["id"], "confirmed")
        case("partial batch: PASS (0) with e2 confirmed", ingest(out4) == 0)
        with open(os.path.join(out4, "batch-result.json"), encoding="utf-8") as fh:
            res4 = json.load(fh)
        case("partial batch: v2 NOT cleared (its e3 is unaudited) and not blocked "
             "(pending)", res4["views_cleared"] == [] and res4["views_blocked"] == [])

        # operator_full_run_authorization: flag arity + structural artifact-class gate
        # (2026-07-07 live-ops nit -- the bare presence check is now a validated path;
        # SAFETY: only the helper is exercised here, never fire() past the cap)
        case("full-run-auth: flag absent -> (None, None)",
             operator_full_run_authorization(["--fire", "--batch", "x"])
             == (None, None))
        case("full-run-auth: flag present with no value -> refused",
             operator_full_run_authorization(
                 ["--operator-authorized-full-run"])[1] is not None)
        case("full-run-auth: value names a file that does not exist -> refused",
             "not found" in (operator_full_run_authorization(
                 ["--operator-authorized-full-run",
                  "deploy/evidence/operator-x.md"])[1] or ""))
        case("full-run-auth: nested path under deploy/evidence/ is refused "
             "(structural, no glob-across-/ smuggling)",
             operator_full_run_authorization(
                 ["--operator-authorized-full-run",
                  "deploy/evidence/sub/operator-x.md"])[1] is not None)
        case("full-run-auth: wrong directory is refused",
             operator_full_run_authorization(
                 ["--operator-authorized-full-run",
                  "deploy/operator-x.md"])[1] is not None)
        case("full-run-auth: wrong basename prefix is refused",
             operator_full_run_authorization(
                 ["--operator-authorized-full-run",
                  "deploy/evidence/note-x.md"])[1] is not None)
        case("full-run-auth: absolute path is refused",
             operator_full_run_authorization(
                 ["--operator-authorized-full-run",
                  os.path.join(base, "deploy", "evidence", "operator-x.md")])[1]
             is not None)

        cwd_saved = os.getcwd()
        try:
            os.chdir(base)
            os.makedirs(os.path.join("deploy", "evidence"), exist_ok=True)
            with open(os.path.join("deploy", "evidence", "operator-selftest.md"),
                      "w", encoding="utf-8", newline="\n") as fh:
                fh.write("selftest operator authorization artifact\n")
            # v3.0-120: uncommitted -> refused; committed -> clean (warn mode surfaces)
            got_path, got_reason = operator_full_run_authorization(
                ["--operator-authorized-full-run",
                 "deploy/evidence/operator-selftest.md"])
            case("full-run-auth v3.0-120: an UNCOMMITTED artifact is refused naming "
                 "committed-identical",
                 got_path is None and "not committed-identical" in (got_reason or ""))
            subprocess.run(["git", "add", "--", "deploy/evidence/operator-selftest.md"],
                           capture_output=True)
            subprocess.run(["git", "commit", "-q", "-m", "selftest artifact", "--",
                            "deploy/evidence/operator-selftest.md"], capture_output=True)
            got_path, got_reason = operator_full_run_authorization(
                ["--operator-authorized-full-run",
                 "deploy/evidence/operator-selftest.md"])
            case("full-run-auth POSITIVE: in-class, existing, COMMITTED artifact resolves "
                 "clean (warn mode: unsigned surfaced, not refused)",
                 got_path == "deploy/evidence/operator-selftest.md"
                 and got_reason is None)
            with open("project.yaml", "w", encoding="utf-8") as fh:
                fh.write("trust_surface_signing: required\n")
            got_path, got_reason = operator_full_run_authorization(
                ["--operator-authorized-full-run",
                 "deploy/evidence/operator-selftest.md"])
            case("full-run-auth v3.0-120: under required an unsigned artifact is refused",
                 got_path is None and "required" in (got_reason or ""))
            os.remove("project.yaml")
        finally:
            os.chdir(cwd_saved)

        # main()-level: the CLI refuses BEFORE any fire() dispatch when the flag's
        # value is bogus (batch dir need not even hold a manifest -- the auth check
        # runs first)
        import contextlib as _contextlib
        import io as _io
        bogus_batch = os.path.join(base, "batch-bogus-authcheck")
        os.makedirs(bogus_batch, exist_ok=True)
        buf = _io.StringIO()
        with _contextlib.redirect_stdout(buf):
            rc_main = main(["audit-content.py", "--fire", "--batch", bogus_batch,
                            "--operator-authorized-full-run", "bogus.md"])
        case("main(): --fire with a bogus authorization value REFUSES (2) before "
             "any dispatch, naming the artifact class",
             rc_main == 2 and "REFUSED" in buf.getvalue()
             and FULL_RUN_ARTIFACT_CLASS in buf.getvalue())
    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failed:
        print("audit-content (F13): FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("audit-content (F13): PASS (%d/%d)" % (total, total))
    return 0


# --------------------------------------------------------------------------- main
def main(argv):
    args = argv[1:]

    def opt(name, default=None):
        if name in args:
            i = args.index(name)
            if i + 1 < len(args):
                return args[i + 1]
        return default

    if "--self-test" in args:
        return self_test()
    if "--population" in args:
        root = opt("--root", ".")
        pop = enumerate_population(root)
        for e in sorted(pop):
            print("%3d view(s)  %s" % (len(pop[e]), e))
        print("population: %d event(s), %d (event,view) pair(s)"
              % (len(pop), sum(len(x) for x in pop.values())))
        return 0
    if "--prepare" in args:
        root, out = opt("--root"), opt("--out")
        planted = [args[i + 1] for i, a in enumerate(args)
                   if a == "--planted" and i + 1 < len(args)]
        events = opt("--events")
        events = [e.strip() for e in events.split(",")] if events else None
        if not root or not out:
            print("usage: audit-content.py --prepare --root DIR --out DIR --planted "
                  "SPEC [--events e1,e2,...]")
            return 2
        return prepare(root, out, planted, events, opt("--injection-json"))
    if "--fire" in args:
        batch = opt("--batch")
        if not batch:
            print("usage: audit-content.py --fire --batch DIR [--timeout-ms N] "
                  "[--effort LEVEL] [--shard K/N] "
                  "[--operator-authorized-full-run ARTIFACT.md]")
            return 2
        full_run_path, refuse_reason = operator_full_run_authorization(args)
        if refuse_reason is not None:
            print("REFUSED -- --operator-authorized-full-run %s (requires a committed "
                  "operator authorization artifact of class %s)"
                  % (refuse_reason, FULL_RUN_ARTIFACT_CLASS))
            return 2
        shard = None
        s = opt("--shard")
        if s and "/" in s:
            k, n = s.split("/", 1)
            shard = (int(k), int(n))
        return fire(batch, int(opt("--timeout-ms", "0") or 0), opt("--effort", ""),
                    shard=shard,
                    full_run_authorized=full_run_path is not None)
    if "--ingest" in args:
        batch = opt("--batch")
        if not batch:
            print("usage: audit-content.py --ingest --batch DIR")
            return 2
        return ingest(batch)
    print(__doc__.strip().split("\n\n")[-1])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
