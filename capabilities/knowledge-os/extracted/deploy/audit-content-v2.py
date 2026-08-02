#!/usr/bin/env python3
"""audit-content-v2.py -- F13 protocol v2: per-claim ENUMERATE/LOCATE audit
(memory-engine-v3-f13-enumeration-amendment.md, 2026-07-03).

Replaces the v1 gestalt leg ("is any load-bearing claim absent from all views?") whose
recall floor on dense views the 2026-07-03 efficacy fixture exposed. Two stages/event:

  ENUMERATE  packet = the raw event ONLY + the blocking-class definition; the verifier
             returns numbered `C<n>: "<verbatim quote>" -- <summary>` lines in its
             reason field. Small packets; completeness checkable against the event.
  LOCATE     packet = the numbered claim list + ALL the event's target views; the
             verifier returns one `C<n>: FOUND <view> -- "<evidence>"` or
             `C<n>: MISSING` line per claim. THE PARSED TABLE IS THE AUTHORITY; the
             verdict enum is only a checksum (any MISSING row => the event fails even
             under a `confirmed` verdict; a confirmed verdict whose table cannot be
             parsed => indecisive).

Efficacy floor (upgraded): the planted fixture's removed claim must surface as a
MISSING row (matched via the spec's absence_probes) in the planted leg's locate table.
Substrate gates (LLM-5 MIGRATION, F17/F18) are inherited from v1 verbatim.

Usage:
  audit-content-v2.py --prepare --root DIR --out DIR --planted SPEC [--events e1,..]
  audit-content-v2.py --prepare-clearance --root DIR --out DIR --planted SPEC
                   --claims-file PATH [--closure]
                   # F13 clearance mode (2026-07-06): locate-from-supplied-claim-list.
                   # NO enumerate stage -- claims come verbatim from --claims-file
                   # (deploy/gen-clearance-claims.py's output). Planted floor still
                   # mandatory; refuses without --planted. Downstream (--build-refire,
                   # --fire-locate, --ingest, --events sharding) is unchanged.
  audit-content-v2.py --fire-enum   --batch DIR [--timeout-ms N]
  audit-content-v2.py --build-locate --batch DIR
  audit-content-v2.py --fire-locate --batch DIR [--timeout-ms N]
  audit-content-v2.py --build-refire --batch DIR   # re-chunk unanswered claim numbers
  audit-content-v2.py --build-enum-supplement --batch DIR  # under-floor enum -> ask for
                                                   # ADDITIONAL claims until exhausted
  audit-content-v2.py --ingest --batch DIR
  audit-content-v2.py --self-test
Exit codes: 0 = batch accepted, all events pass | 1 = planted floor failed OR >=1 event
  has MISSING claims | 2 = INCONCLUSIVE (setup/substrate/parse floor).
"""

import importlib.util
import json
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _load_module(basename, alias):
    spec = importlib.util.spec_from_file_location(alias, os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_v1 = _load_module("audit-content.py", "audit_content_v1")
_substrate = _load_module("check-substrate.py", "check_substrate_v2ref")

BLOCKING_DEF = """\
A LOAD-BEARING (blocking-class) CLAIM is one downstream work would rely on FROM THE
WIKI: a decision, lock, or commitment; a schema or mechanism rule; a compliance
constraint; a price or quantitative commitment; a correction of prior recorded state; a
standing constraint or invariant. NOT load-bearing (do NOT list): implementation
directives whose effect is realized in the repository itself (executed tasks,
navigation/formatting instructions); ephemeral point-in-time observations with no
standing reliance; secondary rationale restating support for a decision you have
already listed; phrasing, ordering, incidental color; AND NONE OF THESE SIX
NON-ABSORBABLE CLASSES (precision amendment 2026-07-04): YAML frontmatter fields;
the document's self-descriptions ("this is a comprehensive sweep"); compile-process
directives ("update cross-references in the next /compile"); re-open-trigger
bookkeeping ("trigger #4 does NOT fire"); raw-authoring compliance self-assertions
("no card data transcribed", "read-only only", "redacted before leaving host");
shipped-state conclusions whose canonical encoding is the repository/corpus itself.\
"""

ENUM_CONTRACT = """CONTRACT (ENUMERATE stage, F13 protocol v2). The claim under test asserts the EVENT
below contains ZERO load-bearing claims. The definition:

%s

Adjudicate by exhaustive counterexample: if the event DOES assert load-bearing claims,
REJECT -- and your reason field is the evidence, so it MUST enumerate EVERY load-bearing
claim the event asserts (an incomplete enumeration under-states the refutation), one
line per claim, exactly this format, nothing between the lines:
C1: "<short verbatim quote from the event>" -- <one-line summary>
C2: ...
Number consecutively; quote verbatim (trim long quotes to their load-bearing core).
Only if the event genuinely asserts no load-bearing claim, confirm.""" % BLOCKING_DEF

LOCATE_CONTRACT = """\
CONTRACT (LOCATE stage, F13 protocol v2). The EVENT (identified below) asserts the
numbered claims listed. The VIEW(s) below are its compile targets. For EACH numbered
claim, determine whether it is represented (verbatim or as a faithful restatement --
including a documented, sourced correction of the claim) in AT LEAST ONE view.
JUDGE SEMANTIC REPRESENTATION, NOT STRING OVERLAP (precision amendment 2026-07-04):
the views compile MEANING, not prose -- a paraphrase, restructuring, distillation,
or tighter statement of the claim counts as FOUND; absence of the claim's wording
is NOT evidence of the claim's absence.

FORMAT (mandatory -- one line per claim, in order, nothing else between them):
C1: FOUND <view path> -- "<short quoted evidence from that view>"
C2: MISSING
Every claim number must appear exactly once. Judge only presence/absence. Confirm the
claim under test only if NO line is MISSING; reject if any is.\
"""

ENUM_CLAIM = ("The EVENT in packet %s asserts ZERO load-bearing claims "
              "under the packet's stated blocking-class definition.")
LOCATE_CLAIM = ("In packet %s, every numbered claim is represented in at least one "
                "VIEW section (verbatim, faithful restatement, or documented sourced "
                "correction); no claim is MISSING from all views.")

_CLAIM_RE = re.compile(r"^C(\d+)\s*:\s*(.+)$", re.MULTILINE)
_LOCATE_RE = re.compile(r"^C(\d+)\s*:\s*(FOUND\b[^\n]*|MISSING\b[^\n]*)$",
                        re.MULTILINE | re.IGNORECASE)


def parse_enum(reason):
    """-> ordered [(n, text)] or None if nothing parses.
    Primary: line-anchored `C<n>: ...`. Fallback: the verifier sometimes embeds the
    numbered claims INLINE in a prose paragraph ("... including: C1: "..." -- x. C2:
    ...") -- an inline scan splitting on the next `C<n>:` recovers them. The larger
    parse wins (a prose preamble plus a proper table should not be shadowed by one
    inline stray)."""
    anchored = [(int(n), t.strip()) for n, t in _CLAIM_RE.findall(reason or "")]
    inline = [(int(n), t.strip().rstrip("."))
              for n, t in re.findall(r"C(\d+)\s*:\s*(.+?)(?=\s+C\d+\s*:|$)",
                                     reason or "", re.DOTALL)]
    best = anchored if len(anchored) >= len(inline) else inline
    if not best:
        return None
    seen, out = set(), []
    for n, t in best:
        if n not in seen:
            seen.add(n)
            out.append((n, t))
    return out


def enum_floor(event_text):
    """A cheap mechanical floor on how many enumerable units the event plausibly has:
    count bullet/numbered/bold-lead lines. The verifier list must reach at least
    ceil(floor/4) -- a drift guard against a truncated/lazy enumeration, NOT a
    completeness proof."""
    n = 0
    for ln in event_text.splitlines():
        s = ln.strip()
        if s.startswith(("-", "*")) and len(s) > 8 or re.match(r"^\d+\.\s", s) \
                or s.startswith("**"):
            n += 1
    return max(1, (n + 3) // 4)


def parse_locate(reason):
    """-> {n: ('FOUND', view, evidence) | ('MISSING',)} or None if nothing parses.
    Primary: strict `C<n>: FOUND|MISSING` table lines. Fallback (the bridge verifier
    tends to prose-summarize): per-claim prose sentences -- "C<n> is represented/
    stated/covered/present ..." => FOUND; "C<n> is absent/missing/not represented/
    not stated/nowhere" => MISSING. Prose-derived rows are marked so ingest can
    require strict tables for the planted floor if desired."""
    hits = _LOCATE_RE.findall(reason or "")
    out = {}
    for n, rest in hits:
        n = int(n)
        r = rest.strip()
        if r.upper().startswith("MISSING"):
            out[n] = ("MISSING",)
        else:
            body = r[5:].strip(" :-")
            view = body.split("--")[0].split(" -- ")[0].strip().split(" ")[0].strip()
            out[n] = ("FOUND", view, body)
    # prose range fallback: "C1-C5 (are) represented/..." expands to each claim
    for m in re.finditer(r"C(\d+)\s*(?:-|–|through|to)\s*C?(\d+)"
                         r"((?:(?!C\d).){0,120})", reason or "", re.DOTALL):
        a, z, seg = int(m.group(1)), int(m.group(2)), m.group(3).lower()
        verdictish = None
        if re.search(r"(absent|missing|not represented|not stated|nowhere|"
                     r"not found|omitted)", seg):
            verdictish = ("MISSING",)
        elif re.search(r"(represented|stated|covered|present|found|appears|"
                       r"carried|captured|preserved)", seg):
            verdictish = ("FOUND", "prose-range", seg.strip()[:100])
        if verdictish and 0 < z - a <= 50:
            for n in range(a, z + 1):
                out.setdefault(n, verdictish)
    # prose fallback for claims the strict table missed
    for m in re.finditer(r"C(\d+)((?:(?!C\d+).){0,160})", reason or "",
                         re.DOTALL):
        n = int(m.group(1))
        if n in out:
            continue
        seg = m.group(2).lower()
        if re.search(r"(is not|are not|absent|missing|not represented|"
                     r"not stated|not established|never states?|never mentions?|"
                     r"does not appear|not present|not carried|nowhere|not found|"
                     r"lacks|omitted)", seg):
            out[n] = ("MISSING",)
        elif re.search(r"(is |are )?(represented|stated|covered|present|found|"
                       r"appears|carried|captured|preserved)", seg):
            out[n] = ("FOUND", "prose", seg.strip()[:120])
    # clause-level fallback: the verifier often writes the verdict verb BEFORE an
    # enumerated list ("positively represents C1, C2, and C4, but does not represent
    # C3") -- classify each clause's polarity and apply it to every claim number in
    # the clause that is still unassigned
    for clause in re.split(r"(?<=[.;])\s+|,\s+(?:but|while|whereas)\s+|;\s*",
                           reason or ""):
        nums = [int(x) for x in re.findall(r"\bC(\d+)\b", clause)]
        # a clause-level verdict outranks the weaker per-claim segment guesses
        # (marked "prose"), which misfire on verb-before-list enumerations
        fresh = [n for n in nums if n not in out
                 or (out[n][0] == "FOUND" and len(out[n]) > 2
                     and out[n][1] == "prose")]
        if not fresh:
            continue
        low = clause.lower()
        neg = re.search(r"(does not|do not|not clearly|never|fails? to|absent|"
                        r"missing|lacks|omit|nowhere|not represented|not stated|"
                        r"not established|not found|no view|not present|"
                        r"only partially|not carried)", low)
        pos = re.search(r"(represent|covered|states?\b|stated|present\b|found|"
                        r"appears|carried|captur|preserv|document|includ|"
                        r"verbatim|establish)", low)
        if neg:
            for n in fresh:
                out[n] = ("MISSING",)
        elif pos:
            for n in fresh:
                out[n] = ("FOUND", "prose-clause", clause.strip()[:120])
    return out or None


# --------------------------------------------------------------------------- prepare
def prepare(root, out, planted_specs, events=None):
    _v1._assert_out_safe(root, out)
    pop = _v1.enumerate_population(root)
    if events:
        missing = [e for e in events if e not in pop]
        if missing:
            print("RESULT: INCONCLUSIVE -- not in population: %s" % ", ".join(missing))
            return 2
        pop = {e: pop[e] for e in events}
    if not planted_specs:
        print("RESULT: INCONCLUSIVE -- a batch without a planted-defect leg is invalid")
        return 2
    p = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                       capture_output=True, text=True)
    salt = (p.stdout or "no-sha").strip()
    os.makedirs(os.path.join(out, "packets"), exist_ok=True)
    os.makedirs(os.path.join(out, "verdicts"), exist_ok=True)
    legs = []

    def emit(erel, views, planted, spec_path, mutated_view=None, mutated_text=None,
             probes=None):
        leg_id = _v1._leg_id(erel, salt, planted=planted)
        etext = _v1._read(root, erel)
        enum_packet = "\n".join([
            "# CONTENT AUDIT ENUM PACKET %s" % leg_id, "", ENUM_CONTRACT, "",
            "## EVENT: %s" % erel, "", "```markdown", etext.rstrip("\n"), "```", ""])
        ep = "packets/%s-enum.md" % leg_id
        with open(os.path.join(out, ep.replace("/", os.sep)), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(enum_packet)
        legs.append({"id": leg_id, "event": erel, "views": views, "planted": planted,
                     "spec": spec_path, "enum_packet": ep,
                     "enum_floor": enum_floor(etext),
                     "enum_claim": ENUM_CLAIM % (leg_id + "-enum"),
                     "mutated_view": mutated_view, "absence_probes": probes or [],
                     "locate_packet": None,
                     "locate_claim": LOCATE_CLAIM % (leg_id + "-locate")})
        if mutated_text is not None:
            with open(os.path.join(out, "packets", leg_id + "-mutated-view.md"), "w",
                      encoding="utf-8", newline="\n") as fh:
                fh.write(mutated_text)

    full_pop = _v1.enumerate_population(root)
    planted_events, mutated_views = set(), set()
    for spec_path in planted_specs:
        spec = _v1.load_planted_spec(spec_path)
        vrel, erel = spec["view"].replace("\\", "/"), spec["event"].replace("\\", "/")
        planted_events.add(erel)
        mutated_views.add(vrel)
        try:
            mutated = _v1.apply_planted(_v1._read(root, vrel), spec)
        except ValueError as e:
            print("RESULT: INCONCLUSIVE -- %s" % e)
            return 2
        probes = spec.get("absence_probes") or []
        for probe in probes:
            if probe.lower() in mutated.lower():
                print("RESULT: INCONCLUSIVE -- probe %r survives mutation" % probe)
                return 2
        emit(erel, full_pop.get(erel) or [vrel], True, spec_path.replace("\\", "/"),
             mutated_view=vrel, mutated_text=mutated, probes=probes)
    deferred = []
    for erel, views in pop.items():
        if erel in planted_events:
            continue
        if any(v in mutated_views for v in views):
            deferred.append(erel)
            continue
        emit(erel, views, False, None)
    legs.sort(key=lambda x: x["id"])
    manifest = {"protocol": "v2-enumerate-locate", "root_sha": salt,
                "root": os.path.abspath(root).replace("\\", "/"),
                "absorb_vendor": _v1.ABSORB_VENDOR,
                "absorb_model_id": _v1.ABSORB_MODEL_ID,
                "deferred_events": sorted(deferred), "legs": legs}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8",
              newline="\n") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
    print("prepared %d v2 leg(s) (%d planted; %d deferred) @ %s -> %s"
          % (len(legs), len(planted_events), len(deferred), salt[:12], out))
    return 0


# --------------------------------------------------------------------------- fire
def _fire_leg(manifest, batch, packet_rel, claim, vname, timeout_ms):
    bridge = os.environ.get("CROSS_VENDOR_BRIDGE_DIR") or os.path.join(
        manifest["root"], ".claude", "skills", "bridge")
    args = ["node", os.path.join(bridge, "verify-cli.js"), "--claim", claim,
            "--evidence-file", os.path.join(batch, packet_rel.replace("/", os.sep)),
            "--tier", "T2"]
    if timeout_ms:
        args += ["--timeout-ms", str(timeout_ms)]
    proc = subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return False, (proc.stderr or "").strip()[-200:]
    with open(os.path.join(batch, "verdicts", vname), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(proc.stdout.strip())
    return True, ""


def fire_phase(batch, phase, timeout_ms=0, full_run_authorized=False, events=None):
    with open(os.path.join(batch, "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    todo = []
    for leg in manifest["legs"]:
        if events and leg["event"] not in events:
            continue
        if phase == "enum":
            cands = [(leg["enum_packet"], "%s-enum.json" % leg["id"],
                      leg["enum_claim"])]
            for s in (leg.get("enum_supplements") or []) + \
                     (leg.get("enum_parts") or []):
                cands.append((s["packet"], s["verdict"], s["claim"]))
            for packet, vname, claim in cands:
                vp = os.path.join(batch, "verdicts", vname)
                if not (os.path.isfile(vp) and os.path.getsize(vp) > 0):
                    todo.append((dict(leg, enum_claim=claim), packet, vname))
            continue
        for k, ch in enumerate(leg.get("locate_chunks") or []):
            vname = "%s-locate-%d.json" % (leg["id"], k)
            vp = os.path.join(batch, "verdicts", vname)
            if not (os.path.isfile(vp) and os.path.getsize(vp) > 0):
                todo.append((leg, ch["packet"], vname))
    if len(todo) > _v1.SPEND_CAP_LEGS and not full_run_authorized:
        print("REFUSED -- %d legs exceeds the Appendix-K cap of %d"
              % (len(todo), _v1.SPEND_CAP_LEGS))
        return 2
    fired = failed = 0
    for leg, packet, vname in todo:
        claim = leg["enum_claim"] if phase == "enum" else leg["locate_claim"]
        print("firing %s %s (%s)..." % (phase, leg["id"], leg["event"]))
        ok, err = _fire_leg(manifest, batch, packet, claim, vname, timeout_ms)
        if ok:
            fired += 1
        else:
            failed += 1
            print("  FAILED: %s" % err)
    print("fire-%s: %d fired, %d failed" % (phase, fired, failed))
    return 0 if failed == 0 else 2


# --------------------------------------------------------------------------- enum merge
def _merged_enum(batch, leg):
    """Merge the main enum verdict with any supplement verdicts.
    -> (claims [(n, text)], exhausted bool). Supplements continue the numbering; a
    colliding number gets the next free one. `exhausted` = the latest supplement
    CONFIRMED (the event has no additional load-bearing claims) -- which satisfies
    the floor by exhaustion even when the merged count stays under it."""
    claims, seen_nums, exhausted = [], set(), False
    paths = [os.path.join(batch, "verdicts", "%s-enum.json" % leg["id"])]
    for s in leg.get("enum_supplements") or []:
        paths.append(os.path.join(batch, "verdicts", s["verdict"]))
    n_base = len(paths)
    parts = leg.get("enum_parts") or []
    parts_ok = bool(parts)
    for s in parts:
        p = os.path.join(batch, "verdicts", s["verdict"])
        if not os.path.isfile(p):
            parts_ok = False
        paths.append(p)
    for i, p in enumerate(paths):
        if not os.path.isfile(p):
            continue
        v = json.load(open(p, encoding="utf-8"))
        if 0 < i < n_base and str(v.get("verdict", "")).lower().startswith("confirm"):
            exhausted = True
        parsed = parse_enum(v.get("reason", "")) or []
        if i >= n_base and not parsed \
                and not str(v.get("verdict", "")).lower().startswith("confirm"):
            parts_ok = False   # a part that neither enumerates nor confirms-empty
        existing = {t.strip().lower() for _n, t in claims}
        for n, t in parsed:
            if t.strip().lower() in existing:
                continue
            if n in seen_nums:
                n = (max(seen_nums) + 1) if seen_nums else 1
            seen_nums.add(n)
            claims.append((n, t))
            existing.add(t.strip().lower())
    claims.sort(key=lambda x: x[0])
    # full part coverage = a complete enumeration by construction (the parts tile
    # the whole event), independent of the mechanical floor
    if parts and parts_ok:
        exhausted = True
    return claims, exhausted


def _split_event(text, target=5000):
    """Split event text into parts of roughly `target` chars, preferring markdown
    heading boundaries, then blank lines."""
    if len(text) <= target:
        return [text]
    lines = text.split("\n")
    parts, cur, size = [], [], 0
    for ln in lines:
        boundary = ln.startswith("#") or (not ln.strip() and size > target)
        if cur and size >= target and boundary:
            parts.append("\n".join(cur))
            cur, size = [], 0
        cur.append(ln)
        size += len(ln) + 1
    if cur:
        parts.append("\n".join(cur))
    return parts


def build_enum_chunked(batch):
    """Last-resort enumeration for events whose merged enum is still under floor
    (the bridge verifier's reason field is length-capped; giant events cannot be
    enumerated in one leg). Split the event into ~5k-char parts; each part gets its
    own enum leg. When every part verdict is in and parses, the union counts as a
    COMPLETE enumeration (parts cover the whole event) regardless of the floor."""
    with open(os.path.join(batch, "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    built = 0
    for leg in manifest["legs"]:
        ev = os.path.join(batch, "verdicts", "%s-enum.json" % leg["id"])
        if not os.path.isfile(ev) or leg.get("enum_parts"):
            continue
        claims, exhausted = _merged_enum(batch, leg)
        if exhausted or len(claims) >= leg.get("enum_floor", 1):
            continue
        packet_text = open(os.path.join(
            batch, leg["enum_packet"].replace("/", os.sep)),
            encoding="utf-8").read()
        body = packet_text.split("```markdown\n", 1)[1].rsplit("\n```", 1)[0]
        pieces = _split_event(body)
        if len(pieces) < 2:
            continue
        leg["enum_parts"] = []
        for k, piece in enumerate(pieces):
            parts = ["# CONTENT AUDIT ENUM PART %s-p%d (part %d of %d)"
                     % (leg["id"], k, k + 1, len(pieces)), "",
                     ENUM_CONTRACT, "",
                     "This packet carries PART %d of %d of the event %s. Enumerate "
                     "every load-bearing claim asserted IN THIS PART, numbering "
                     "from C1. One `C<n>:` line per claim; never summarize with "
                     "'examples include'." % (k + 1, len(pieces), leg["event"]),
                     "", "## EVENT PART %d/%d: %s" % (k + 1, len(pieces),
                                                      leg["event"]),
                     "", "```markdown", piece.rstrip("\n"), "```", ""]
            pp = "packets/%s-enum-p%d.md" % (leg["id"], k)
            with open(os.path.join(batch, pp.replace("/", os.sep)), "w",
                      encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(parts))
            leg["enum_parts"].append(
                {"packet": pp, "verdict": "%s-enum-p%d.json" % (leg["id"], k),
                 "claim": ("The EVENT PART in packet %s-p%d asserts ZERO "
                           "load-bearing claims under the packet's stated "
                           "blocking-class definition." % (leg["id"], k))})
        built += 1
    with open(os.path.join(batch, "manifest.json"), "w", encoding="utf-8",
              newline="\n") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
    print("build-enum-chunked: %d leg(s) chunked" % built)
    return 0


def build_enum_supplement(batch):
    """For legs whose merged enumeration is still under floor and not exhausted:
    emit a supplement enum packet listing what is already enumerated and demanding
    ONLY the additional claims (numbering continued). --fire-enum picks it up."""
    with open(os.path.join(batch, "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    built = 0
    for leg in manifest["legs"]:
        ev = os.path.join(batch, "verdicts", "%s-enum.json" % leg["id"])
        if not os.path.isfile(ev):
            continue
        claims, exhausted = _merged_enum(batch, leg)
        if exhausted or len(claims) >= leg.get("enum_floor", 1):
            continue
        k = len(leg.get("enum_supplements") or [])
        nxt = (max(n for n, _ in claims) + 1) if claims else 1
        packet_text = open(os.path.join(
            batch, leg["enum_packet"].replace("/", os.sep)),
            encoding="utf-8").read()
        event_block = packet_text.split("## EVENT:", 1)[1]
        parts = ["# CONTENT AUDIT ENUM SUPPLEMENT %s-s%d" % (leg["id"], k), "",
                 ENUM_CONTRACT, "",
                 "A PRIOR LEG ALREADY ENUMERATED THE CLAIMS BELOW. Do NOT repeat "
                 "them. If the event asserts load-bearing claims NOT in this list, "
                 "REJECT and enumerate EVERY additional one -- exhaustively, one "
                 "`C<n>:` line per claim, numbering from C%d. NEVER summarize with "
                 "'examples include'. Only if the list is already complete, confirm."
                 % nxt, "", "## ALREADY ENUMERATED", ""]
        parts += ["C%d: %s" % (n, t) for n, t in claims]
        parts += ["", "## EVENT:" + event_block.rstrip("\n"), ""]
        sp = "packets/%s-enum-s%d.md" % (leg["id"], k)
        with open(os.path.join(batch, sp.replace("/", os.sep)), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(parts))
        supp = {"packet": sp, "verdict": "%s-enum-s%d.json" % (leg["id"], k),
                "claim": ("The ALREADY ENUMERATED list in packet %s-s%d is a "
                          "COMPLETE enumeration of the EVENT's load-bearing claims; "
                          "the event asserts no additional one." % (leg["id"], k))}
        leg.setdefault("enum_supplements", []).append(supp)
        built += 1
    with open(os.path.join(batch, "manifest.json"), "w", encoding="utf-8",
              newline="\n") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
    print("build-enum-supplement: %d packet(s)" % built)
    return 0


# --------------------------------------------------------------------------- locate build
CLOSURE_CANDIDATES = ["SESSION-BRIEFING.md", "FLIGHTDECK.md", "CLAUDE.md",
                      "wiki/INDEX.md"]


def _closure_views(root):
    """Precision amendment #1 (partial mechanization): root projections +
    governance files that exist in the tree, appended to every locate packet under
    --closure. A claim found ONLY here is a sources:-gap finding, not a content gap."""
    out = [c for c in CLOSURE_CANDIDATES
           if os.path.isfile(os.path.join(root, c.replace("/", os.sep)))]
    rd = os.path.join(root, "roadmap")
    if os.path.isdir(rd):
        out += sorted("roadmap/" + f for f in os.listdir(rd) if f.endswith(".md"))
    return out


def _frontmatter_block(event_text):
    """The YAML frontmatter body, or '' if none."""
    if not event_text.startswith("---"):
        return ""
    end = event_text.find("\n---", 3)
    return event_text[3:end] if end > 0 else ""


def filter_frontmatter_claims(claims, event_text):
    """Precision amendment #6 (mechanical part): drop claims whose quoted text is
    drawn from the event's YAML frontmatter block. -> (kept, dropped)."""
    fm = _frontmatter_block(event_text).lower()
    if not fm:
        return claims, []
    kept, dropped = [], []
    for n, t in claims:
        m = re.search(r'"([^"]{6,})"', t)
        if m and m.group(1).lower() in fm:
            dropped.append((n, t))
        else:
            kept.append((n, t))
    return kept, dropped


CHUNK = 5   # locate chunk size: small tables force per-claim attention and fit
#             the verifier's compact-reason discipline (the 20-claim pilot leg got
#             glossed into prose ranges; 5-claim legs cannot compress much). Shared by
#             the enum-derived path (build_locate) and clearance mode (prepare_clearance)
#             -- same chunking arithmetic incl. remainder, same packet wording.


def _build_locate_chunks(batch, leg, claims, root, extra):
    """Build this leg's locate_chunks (packets written to disk, chunk metadata
    returned) from an already-resolved [(n, text), ...] claim list. Shared, byte-for-
    byte, by the enum-derived locate path (build_locate) and clearance mode
    (prepare_clearance) -- one packet-text builder, not forked wording."""
    chunks = [claims[i:i + CHUNK] for i in range(0, len(claims), CHUNK)]
    locate_chunks = []
    for k, chunk in enumerate(chunks):
        parts = ["# CONTENT AUDIT LOCATE PACKET %s-locate-%d" % (leg["id"], k), "",
                 LOCATE_CONTRACT, "", "## EVENT: %s" % leg["event"],
                 "", "## NUMBERED CLAIMS", ""]
        parts += ["C%d: %s" % (n, txt) for n, txt in chunk]
        parts.append("")
        allviews = leg["views"] + [x for x in extra if x not in leg["views"]]
        for i, vrel in enumerate(allviews, 1):
            if leg.get("planted") and vrel == leg.get("mutated_view"):
                vtext = open(os.path.join(batch, "packets",
                                          leg["id"] + "-mutated-view.md"),
                             encoding="utf-8").read()
            else:
                vtext = _v1._read(root, vrel)
            tag = "" if vrel in leg["views"] else \
                " [CLOSURE -- a claim found ONLY here is a sources:-gap]"
            parts += ["## VIEW %d of %d: %s%s" % (i, len(allviews), vrel, tag),
                      "", "```markdown", vtext.rstrip(chr(10)), "```", ""]
        lp = "packets/%s-locate-%d.md" % (leg["id"], k)
        with open(os.path.join(batch, lp.replace("/", os.sep)), "w",
                  encoding="utf-8", newline=chr(10)) as fh:
            fh.write(chr(10).join(parts))
        locate_chunks.append({"packet": lp, "claims": [n for n, _ in chunk]})
    return locate_chunks


def build_locate(batch, root=None, closure=False):
    """After enum verdicts arrive: build each leg's locate packet from the parsed
    claim list + the event's views (mutated copy for the planted leg)."""
    with open(os.path.join(batch, "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    root = root or manifest["root"]
    extra = _closure_views(root) if closure else []
    built = skipped = 0
    for leg in manifest["legs"]:
        ev = os.path.join(batch, "verdicts", "%s-enum.json" % leg["id"])
        if not os.path.isfile(ev):
            skipped += 1
            continue
        claims, exhausted = _merged_enum(batch, leg)
        if not claims or (len(claims) < leg.get("enum_floor", 1) and not exhausted):
            leg["enum_parse"] = "FLOOR-FAIL(%s)" % (len(claims) if claims else 0)
            skipped += 1
            continue
        etext = _v1._read(root, leg["event"])
        claims, fm_dropped = filter_frontmatter_claims(claims, etext)
        if fm_dropped:
            leg["frontmatter_dropped"] = [n for n, _ in fm_dropped]
        if not claims:
            leg["enum_parse"] = "ALL-FRONTMATTER(0)"
            skipped += 1
            continue
        leg["enum_parse"] = "ok(%d)%s%s" % (
            len(claims), "+exhausted" if exhausted else "",
            "-fm%d" % len(fm_dropped) if fm_dropped else "")
        leg["claims"] = [{"n": n, "text": t} for n, t in claims]
        leg["locate_chunks"] = _build_locate_chunks(batch, leg, claims, root, extra)
        leg["locate_packet"] = leg["locate_chunks"][0]["packet"] \
            if leg["locate_chunks"] else None
        built += 1
    with open(os.path.join(batch, "manifest.json"), "w", encoding="utf-8",
              newline="\n") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
    print("build-locate: %d built, %d skipped (missing/floor-failed enum)"
          % (built, skipped))
    return 0 if built else 2


# --------------------------------------------------------------------------- clearance
def load_clearance_claims(path):
    """The gen-clearance-claims.py output: {"generated_from": ..., "events": {erel:
    {"views": [...], "claims": [{"n", "text", "view"}, ...]}}}. Read-only; no
    self-checks re-run here (gen-clearance-claims.py already refuses to write a file
    that fails its own census checks)."""
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    events = doc.get("events") or {}
    if not events:
        raise ValueError("clearance claims file %s has no events" % path)
    return doc


def prepare_clearance(root, out, claims_file, planted_specs, out_root=None,
                      closure=False):
    """Clearance mode (F13 option (a), 2026-07-06): builds a batch whose ONLY legs
    are locate legs derived from a SUPPLIED claim list -- NO enumerate stage at all.
    Mirrors prepare()'s CLI shape and refusal patterns; reuses _build_locate_chunks
    (same packet wording, same CHUNK=5 chunking arithmetic incl. remainder) so the
    locate packet a clearance leg fires is byte-for-byte what the enum-derived path
    would build for the same claim list.

    PLANTED FLOOR UNCHANGED AND MANDATORY: refuses (same exit/message pattern as
    prepare()) without a --planted spec. The planted leg's claim list is the planted
    spec's OWN removed-claim text (the mutation spec's `edits[].find` clauses --
    the only place the removed clause's exact text already lives; clearance mode has
    no enum stage to derive it from), numbered from 1; its view mutation mechanics
    (drift guard, absence-probe pre-check, blinded mutated-view substitution) are
    exactly the existing planted path's.

    LEG-KEY PARITY WITH emit() (2026-07-06 fix): fire_phase()'s locate path reads
    leg["locate_claim"] unconditionally (regardless of chunk index -- one claim
    string per leg, same as the enum-derived path). emit() sets it at prepare time;
    this function must too, using the SAME construction (LOCATE_CLAIM % (leg_id +
    "-locate")) on every leg -- planted and regular alike."""
    _v1._assert_out_safe(root, out)
    if not planted_specs:
        print("RESULT: INCONCLUSIVE -- a batch without a planted-defect leg is invalid")
        return 2
    try:
        claims_doc = load_clearance_claims(claims_file)
    except (OSError, ValueError) as e:
        print("RESULT: INCONCLUSIVE -- %s" % e)
        return 2
    claims_events = claims_doc["events"]

    p = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                       capture_output=True, text=True)
    salt = (p.stdout or "no-sha").strip()
    os.makedirs(os.path.join(out, "packets"), exist_ok=True)
    os.makedirs(os.path.join(out, "verdicts"), exist_ok=True)
    legs = []
    extra = _closure_views(root) if closure else []

    planted_events, mutated_views = set(), set()
    for spec_path in planted_specs:
        spec = _v1.load_planted_spec(spec_path)
        vrel = spec["view"].replace("\\", "/")
        erel = spec["event"].replace("\\", "/")
        planted_events.add(erel)
        mutated_views.add(vrel)
        try:
            mutated = _v1.apply_planted(_v1._read(root, vrel), spec)
        except ValueError as e:
            print("RESULT: INCONCLUSIVE -- %s" % e)
            return 2
        probes = spec.get("absence_probes") or []
        for probe in probes:
            if probe.lower() in mutated.lower():
                print("RESULT: INCONCLUSIVE -- probe %r survives mutation" % probe)
                return 2
        # the planted claim's text: the spec's own removed-clause text (edits[].find),
        # since clearance mode has no enum stage to derive claim text from the raw
        # event. Non-empty finds only, joined in spec order, numbered from 1.
        finds = [ed.get("find", "").strip() for ed in spec.get("edits") or []]
        finds = [f for f in finds if f]
        planted_claims = [(i + 1, f) for i, f in enumerate(finds)] or \
            [(1, spec.get("description") or "(planted claim)")]
        leg_id = _v1._leg_id(erel, salt, planted=True)
        with open(os.path.join(out, "packets", leg_id + "-mutated-view.md"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(mutated)
        leg = {"id": leg_id, "event": erel, "views": [vrel], "planted": True,
               "spec": spec_path.replace("\\", "/"), "mutated_view": vrel,
               "absence_probes": probes, "claims": [{"n": n, "text": t}
                                                     for n, t in planted_claims],
               "tier": "T2-clean",
               "locate_claim": LOCATE_CLAIM % (leg_id + "-locate")}
        leg["locate_chunks"] = _build_locate_chunks(out, leg, planted_claims, root,
                                                     extra)
        leg["locate_packet"] = leg["locate_chunks"][0]["packet"] \
            if leg["locate_chunks"] else None
        legs.append(leg)

    deferred = []
    for erel, ev in claims_events.items():
        erel = erel.replace("\\", "/")
        if erel in planted_events:
            continue
        views = [v.replace("\\", "/") for v in ev["views"]]
        if any(v in mutated_views for v in views):
            deferred.append(erel)
            continue
        claims = [(c["n"], c["text"]) for c in
                  sorted(ev["claims"], key=lambda c: c["n"])]
        leg_id = _v1._leg_id(erel, salt, planted=False)
        leg = {"id": leg_id, "event": erel, "views": views, "planted": False,
               "spec": None, "claims": [{"n": n, "text": t} for n, t in claims],
               "locate_claim": LOCATE_CLAIM % (leg_id + "-locate")}
        leg["locate_chunks"] = _build_locate_chunks(out, leg, claims, root, extra)
        leg["locate_packet"] = leg["locate_chunks"][0]["packet"] \
            if leg["locate_chunks"] else None
        legs.append(leg)

    legs.sort(key=lambda x: x["id"])
    manifest = {"protocol": "v2-clearance", "root_sha": salt,
                "root": os.path.abspath(root).replace("\\", "/"),
                "absorb_vendor": _v1.ABSORB_VENDOR,
                "absorb_model_id": _v1.ABSORB_MODEL_ID,
                "claims_file": os.path.abspath(claims_file).replace("\\", "/"),
                "generated_from": claims_doc.get("generated_from"),
                "deferred_events": sorted(deferred), "legs": legs}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8",
              newline="\n") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
    print("prepared %d clearance leg(s) (%d planted; %d deferred) @ %s -> %s"
          % (len(legs), len(planted_events), len(deferred), salt[:12], out))
    return 0


# --------------------------------------------------------------------------- refire
def build_refire(batch, root=None):
    """Coverage hardening (pilot finding: prose tables answer most-but-not-all claim
    numbers -> pass verdicts land indecisive). For each leg whose locate chunks all
    have verdicts but whose merged table leaves claim numbers unanswered, append
    smaller (3-claim) chunks carrying ONLY the unanswered claims; --fire-locate then
    picks them up as unfired. One round per invocation; if numbers stay unanswered
    after a refire round, indecisive stands (no infinite loop)."""
    with open(os.path.join(batch, "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    root = root or manifest["root"]
    added_legs = 0
    added_chunks = 0
    for leg in manifest["legs"]:
        chunks = leg.get("locate_chunks") or []
        if not chunks or not leg.get("claims"):
            continue
        table = {}
        complete = True
        for k, ch in enumerate(chunks):
            lp = os.path.join(batch, "verdicts", "%s-locate-%d.json" % (leg["id"], k))
            if not os.path.isfile(lp):
                complete = False
                break
            v = json.load(open(lp, encoding="utf-8"))
            part = parse_locate(v.get("reason", "")) or {}
            table.update(part)
            # a CONFIRMED chunk verdict asserts the conjunction "no claim in this
            # chunk is MISSING" -- fill its unanswered claim numbers as FOUND
            # rather than re-asking (an explicit MISSING row still outranks it)
            if str(v.get("verdict", "")).lower().startswith("confirm"):
                for n in ch.get("claims", []):
                    table.setdefault(n, ("FOUND", "chunk-confirmed", ""))
        if not complete:
            continue
        claims = {c["n"]: c["text"] for c in leg["claims"]}
        unanswered = sorted(set(claims) - set(table))
        if not unanswered:
            continue
        RECHUNK = 3
        pend = [(n, claims[n]) for n in unanswered]
        for j in range(0, len(pend), RECHUNK):
            chunk = pend[j:j + RECHUNK]
            k = len(leg["locate_chunks"])
            parts = ["# CONTENT AUDIT LOCATE PACKET %s-locate-%d" % (leg["id"], k), "",
                     LOCATE_CONTRACT, "",
                     "NOTE: this is a re-fire of claims a prior leg left unanswered. "
                     "Answer EVERY numbered claim below with its own C<n> line.",
                     "", "## EVENT: %s" % leg["event"], "", "## NUMBERED CLAIMS", ""]
            parts += ["C%d: %s" % (n, txt) for n, txt in chunk]
            parts.append("")
            for i, vrel in enumerate(leg["views"], 1):
                if leg.get("planted") and vrel == leg.get("mutated_view"):
                    vtext = open(os.path.join(batch, "packets",
                                              leg["id"] + "-mutated-view.md"),
                                 encoding="utf-8").read()
                else:
                    vtext = _v1._read(root, vrel)
                parts += ["## VIEW %d of %d: %s" % (i, len(leg["views"]), vrel), "",
                          "```markdown", vtext.rstrip(chr(10)), "```", ""]
            lp = "packets/%s-locate-%d.md" % (leg["id"], k)
            with open(os.path.join(batch, lp.replace("/", os.sep)), "w",
                      encoding="utf-8", newline=chr(10)) as fh:
                fh.write(chr(10).join(parts))
            leg["locate_chunks"].append({"packet": lp,
                                         "claims": [n for n, _ in chunk],
                                         "refire": True})
            added_chunks += 1
        added_legs += 1
    with open(os.path.join(batch, "manifest.json"), "w", encoding="utf-8",
              newline="\n") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
    print("build-refire: %d chunk(s) added across %d leg(s)"
          % (added_chunks, added_legs))
    return 0


# --------------------------------------------------------------------------- ingest
def ingest(batch):
    with open(os.path.join(batch, "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    legs = manifest["legs"]
    planted = [x for x in legs if x.get("planted")]
    if not planted:
        print("RESULT: INCONCLUSIVE -- no planted leg")
        return 2
    os.makedirs(os.path.join(batch, "records"), exist_ok=True)
    substrate_fails, rows = [], []
    for leg in legs:
        chunks = leg.get("locate_chunks") or []
        table = {}
        verdict = None
        all_chunks_present = bool(chunks)
        leg_substrate_ok = True
        for k, ch in enumerate(chunks):
            lp = os.path.join(batch, "verdicts", "%s-locate-%d.json" % (leg["id"], k))
            if not os.path.isfile(lp):
                all_chunks_present = False
                continue
            verdict = json.load(open(lp, encoding="utf-8"))
            part = parse_locate(verdict.get("reason", ""))
            if part:
                table.update(part)
            # CONFIRMED chunk = conjunction "no claim here is MISSING": fill the
            # chunk's unanswered numbers as FOUND (explicit rows keep precedence)
            if str(verdict.get("verdict", "")).lower().startswith("confirm"):
                for n in ch.get("claims", []):
                    table.setdefault(n, ("FOUND", "chunk-confirmed", ""))
            crec = _v1._substrate_record(
                {"id": leg["id"], "packet_sha256": ""}, verdict, manifest)
            ok_form, _why = _substrate.verified_block_wellformed(crec)
            if not (ok_form and _substrate.substrate_derived_from_invocation(crec)
                    and _substrate.substrate_gate_ok(
                        crec["absorb_vendor"], crec["absorb_model_id"],
                        crec["verifier_vendor"], crec["verifier_model_id"],
                        _substrate.MIGRATION)):
                leg_substrate_ok = False
        rec = _v1._substrate_record(
            {"id": leg["id"], "packet_sha256": ""}, verdict, manifest)
        rec["artifact"] = "verdicts/%s-locate-*.json" % leg["id"]
        status = "indecisive"
        missing = []
        if verdict is not None and table and all_chunks_present:
            claims = {c["n"]: c["text"] for c in leg.get("claims", [])}
            covered = set(table) >= set(claims)
            missing = [{"n": n, "text": claims.get(n, "?")}
                       for n, r in sorted(table.items()) if r[0] == "MISSING"]
            if missing:
                status = "fail"            # a MISSING row is decisive evidence
                #                            (THE TABLE IS THE AUTHORITY)
            elif covered:
                status = "pass"            # pass requires the FULL table
            else:
                status = "indecisive"      # incomplete table, nothing missing
            if not leg_substrate_ok:
                substrate_fails.append(leg["id"])
        row = {"id": leg["id"], "event": leg["event"], "views": leg["views"],
               "planted": bool(leg.get("planted")), "status": status,
               "missing": missing, "n_claims": len(leg.get("claims", [])),
               # precision amendment: confidence tier -- T1-regrounded is assigned
               # only by a consumer-HEAD re-validation pass, never here. Clearance-mode
               # legs never carry enum_parts (no enumerate stage at all), so they fall
               # through to T2-clean here unchanged -- no clearance-specific branch
               # needed.
               "tier": "T3-raw" if leg.get("enum_parts") else "T2-clean",
               "substrate": rec}
        rows.append(row)
        with open(os.path.join(batch, "records", leg["id"] + ".json"), "w",
                  encoding="utf-8", newline="\n") as fh:
            json.dump(row, fh, indent=2, sort_keys=True)
    if substrate_fails:
        print("RESULT: INCONCLUSIVE -- substrate fails: %s" % substrate_fails)
        return 2
    # planted floor: the planted claim must be a MISSING row (matched via probes)
    floor_ok = True
    for r in rows:
        if not r["planted"]:
            continue
        probes = [p.lower() for x in legs if x["id"] == r["id"]
                  for p in x.get("absence_probes", [])]
        hit = any(any(p in (m["text"] or "").lower() for p in probes)
                  for m in r["missing"])
        if not hit:
            floor_ok = False
    regular = [r for r in rows if not r["planted"]]
    decisive = [r for r in regular if r["status"] in ("pass", "fail")]
    result = {"protocol": manifest.get("protocol", "v2-enumerate-locate"),  # mirror the manifest's protocol (clearance batches stamp v2-clearance)
              "legs": len(legs),
              "planted_floor_met": floor_ok,
              "events_pass": sorted(r["event"] for r in regular
                                    if r["status"] == "pass"),
              "events_fail": sorted(r["event"] for r in regular
                                    if r["status"] == "fail"),
              "indecisive": sorted(r["event"] for r in regular
                                   if r["status"] == "indecisive"),
              "missing_total": sum(len(r["missing"]) for r in regular),
              "batch_accepted": floor_ok and (not regular or
                                              len(decisive) / len(regular) >= 0.8),
              "root_sha": manifest.get("root_sha")}
    with open(os.path.join(batch, "batch-result.json"), "w", encoding="utf-8",
              newline="\n") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
    print("=" * 72)
    print("F13 v2 ENUM/LOCATE -- batch @ %s" % str(result["root_sha"])[:12])
    for r in rows:
        print("  %-14s %-52s %s%s %s" % (
            r["id"], r["event"], r["status"],
            " [PLANTED]" if r["planted"] else "",
            ("missing=%d" % len(r["missing"])) if r["missing"] else ""))
    print("planted floor met: %s · pass %d · fail %d · indecisive %d · missing rows %d"
          % (floor_ok, len(result["events_pass"]), len(result["events_fail"]),
             len(result["indecisive"]), result["missing_total"]))
    if not floor_ok:
        print("RESULT: FAIL -- planted claim NOT a MISSING row; batch NOT accepted.")
        return 1
    if result["events_fail"]:
        print("RESULT: FAIL -- batch accepted; %d event(s) have MISSING claims "
              "(complete per-event lists in records/)." % len(result["events_fail"]))
        return 1
    if not result["batch_accepted"]:
        print("RESULT: INCONCLUSIVE -- decisive floor not met.")
        return 2
    print("RESULT: PASS -- batch accepted; all events fully located.")
    return 0


# --------------------------------------------------------------------------- self-test
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

    # parsers
    en = parse_enum('preamble\nC1: "the cap is 25" -- spend cap\nC2: "PG wins" -- '
                    'conflict rule\ntrailing')
    case("enum parse: 2 claims in order", en == [(1, '"the cap is 25" -- spend cap'),
                                                 (2, '"PG wins" -- conflict rule')])
    case("enum parse: garbage -> None", parse_enum("no claims here") is None)
    en2 = parse_enum('The event plainly asserts claims: C1: "cap is 25" -- spend. '
                     'C2: "PG wins" -- conflict.')
    case("enum parse: inline-prose fallback recovers embedded claims",
         en2 is not None and len(en2) == 2 and "PG wins" in en2[1][1])
    loc = parse_locate('C1: FOUND wiki/a.md -- "the cap is 25"\nC2: MISSING\n'
                       'C3: found wiki/b.md -- "x"')
    case("locate parse: FOUND/MISSING/case-insensitive",
         loc[1][0] == "FOUND" and loc[2] == ("MISSING",) and loc[3][0] == "FOUND")
    case("locate parse: garbage -> None", parse_locate("nothing") is None)
    lc = parse_locate("The VIEW positively represents C1, C2, C4, and C5, but it "
                      "does not clearly represent C3's process rule. C10 is only "
                      "partially adjacent, not clearly the stated constraint.")
    case("locate parse: clause-level verb-before-list enumeration",
         lc is not None and all(lc.get(n, ("",))[0] == "FOUND" for n in (1, 2, 4, 5))
         and lc.get(3) == ("MISSING",) and lc.get(10) == ("MISSING",))
    case("enum floor: bullets/бold counted, floor = ceil(n/4)",
         enum_floor("- a claim line here\n- another claim\n**bold** x\n1. numbered\n")
         == 1)
    ev = "---\nsource: session\ndate: 2026-01-01\n---\n\nThe cap is 25 legs.\n"
    kept, dropped = filter_frontmatter_claims(
        [(1, '"source: session" -- frontmatter field'),
         (2, '"The cap is 25 legs" -- spend cap')], ev)
    case("precision #6: frontmatter-quoted claim dropped, body claim kept",
         [n for n, _ in kept] == [2] and [n for n, _ in dropped] == [1])
    kept2, dropped2 = filter_frontmatter_claims(
        [(1, '"no quote here at all')], "no frontmatter")
    case("precision #6: no frontmatter -> nothing dropped",
         len(kept2) == 1 and not dropped2)

    # ----------------------------------------------------------------- clearance mode
    # Self-contained fixture tree (NOT _v1._mk_fixture_tree): prepare_clearance() never
    # calls _v1.enumerate_population / staleness.load_ledger (no enum stage, no ledger
    # population walk), so these cases do not need a registrations store and are
    # independent of the (pre-existing, unrelated) enlarged-ledger EnlargementViolation
    # that currently crashes the enum-derived prepare() fixture below (staleness.py's
    # load_ledger flipped enlarged=True by default the same day, 2026-07-06, adjudication
    # 6; _v1._mk_fixture_tree's 3 fixture events carry no registrations -- see the run
    # report's flagged contradiction). Kept as its own isolated try/finally so it runs
    # and is counted regardless.
    cbase = tempfile.mkdtemp(prefix="f13clear-")
    try:
        craw = os.path.join(cbase, "raw")
        cwiki = os.path.join(cbase, "wiki", "topic")
        os.makedirs(craw)
        os.makedirs(cwiki)

        def cw(path, text):
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)

        # event A: 7 claims -> chunks of 5 + 2 (remainder chunk)
        cw(os.path.join(craw, "2026-01-10-eA.md"),
           "---\ndate: 2026-01-10\n---\nEvent A raw body (7 absorb claims).\n")
        cw(os.path.join(cwiki, "va.md"),
           "---\ntitle: VA\nsources:\n  - raw/2026-01-10-eA.md\n---\n# VA\ncurrent body\n")
        # event B: 3 claims -> one chunk of 3
        cw(os.path.join(craw, "2026-01-11-eB.md"),
           "---\ndate: 2026-01-11\n---\nEvent B raw body (3 absorb claims).\n")
        cw(os.path.join(cwiki, "vb.md"),
           "---\ntitle: VB\nsources:\n  - raw/2026-01-11-eB.md\n---\n# VB\ncurrent body\n")
        # planted event/view: 1 claim, removed clause "the cap is fixed at 25"
        cw(os.path.join(craw, "2026-01-12-ep.md"),
           "---\ndate: 2026-01-12\n---\nThe cap is fixed at 25 legs per invocation.\n")
        cw(os.path.join(cwiki, "vp.md"),
           "---\ntitle: VP\nsources:\n  - raw/2026-01-12-ep.md\n---\n# VP\n"
           "The cap is fixed at 25 legs per invocation.\n")
        cspec_path = os.path.join(cbase, "planted-clearance.yaml")
        cw(cspec_path,
           "view: wiki/topic/vp.md\nevent: raw/2026-01-12-ep.md\n"
           "class: d1-dropped-clause\ndescription: drop the leg-cap claim\n"
           "absence_probes:\n  - \"cap is fixed at 25\"\nedits:\n"
           "  - find: \"The cap is fixed at 25 legs per invocation.\"\n"
           "    replace: \"\"\n")

        claims_doc = {
            "generated_from": "wave2 proposals @ fixture-sha",
            "events": {
                "raw/2026-01-10-eA.md": {
                    "views": ["wiki/topic/va.md"],
                    "claims": [{"n": i, "text": "claim text A%d" % i, "view":
                               "wiki/topic/va.md"} for i in range(1, 8)]},
                "raw/2026-01-11-eB.md": {
                    "views": ["wiki/topic/vb.md"],
                    "claims": [{"n": i, "text": "claim text B%d" % i, "view":
                               "wiki/topic/vb.md"} for i in range(1, 4)]},
            }}
        cclaims_path = os.path.join(cbase, "claims.json")
        with open(cclaims_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(claims_doc, fh, indent=2, sort_keys=True)

        # (b) refuse-without-planted
        cout_np = os.path.join(cbase, "batch-noplant")
        case("clearance: --prepare-clearance without --planted -> INCONCLUSIVE (2), "
             "same refusal message pattern as prepare()",
             prepare_clearance(cbase, cout_np, cclaims_path, []) == 2)

        # (a) claims-file parse + leg build: exactly 2+1 locate chunks, correct
        # chunk membership incl. the remainder chunk
        cout = os.path.join(cbase, "batch")
        rc = prepare_clearance(cbase, cout, cclaims_path, [cspec_path])
        case("clearance: prepare-clearance rc=0", rc == 0)
        cman = json.load(open(os.path.join(cout, "manifest.json"), encoding="utf-8"))
        case("clearance: manifest protocol tag", cman["protocol"] == "v2-clearance")
        caleg = next(x for x in cman["legs"] if x["event"] == "raw/2026-01-10-eA.md")
        cbleg = next(x for x in cman["legs"] if x["event"] == "raw/2026-01-11-eB.md")
        cpleg = next(x for x in cman["legs"] if x["planted"])
        case("clearance: event A (7 claims) chunks into 2 legs (5 + 2 remainder)",
             len(caleg["locate_chunks"]) == 2
             and caleg["locate_chunks"][0]["claims"] == [1, 2, 3, 4, 5]
             and caleg["locate_chunks"][1]["claims"] == [6, 7])
        case("clearance: event B (3 claims) chunks into 1 leg of 3",
             len(cbleg["locate_chunks"]) == 1
             and cbleg["locate_chunks"][0]["claims"] == [1, 2, 3])
        case("clearance: NO enum stage -- no enum_packet/enum_floor on any leg",
             all("enum_packet" not in x and "enum_floor" not in x
                 for x in cman["legs"]))

        # (a2) key-parity assertion (2026-07-06 fix for the locate_claim KeyError):
        # every key fire_phase() reads on the phase="locate" path -- and every key
        # build_refire() reads -- must exist on every clearance leg. Hard-coded here
        # (with this comment as the mechanical cross-reference) rather than
        # introspected from source, since fire_phase's read list is a handful of
        # literal leg[...] subscripts, not a data structure to walk at runtime:
        #   fire_phase(phase="locate"):  leg["id"], leg["event"], leg.get(
        #       "locate_chunks"), leg["locate_claim"]        (audit-content-v2.py
        #       lines ~359-371)
        #   build_refire():  leg.get("locate_chunks"), leg.get("claims"), leg["id"],
        #       leg["claims"], leg["event"], leg["views"], leg.get("planted"),
        #       leg.get("mutated_view")                      (lines ~805-856)
        FIRE_LOCATE_READ_KEYS = ["id", "event", "locate_claim"]  # + .get("locate_chunks")
        BUILD_REFIRE_READ_KEYS = ["id", "claims", "event", "views"]  # + .get(...) optionals
        case("clearance: key-parity -- every fire_phase(locate)/build_refire "
             "required key exists on every clearance leg (incl. locate_claim, "
             "the KeyError this fix closes)",
             all(k in leg for leg in cman["legs"]
                 for k in FIRE_LOCATE_READ_KEYS + BUILD_REFIRE_READ_KEYS))

        # (a3) fire-path case: fire_phase driven over the 2-chunk clearance leg (event
        # A, 7 claims -> 5+2 chunks) with the bridge subprocess call stubbed (no real
        # bridge invocation -- mirrors how the self-test fakes verdicts for ingest,
        # applied here to fire_phase's subprocess seam since it has no dedicated test
        # seam of its own). Asserts both chunks produce verdict files and the
        # todo/cap accounting is right (2 fired, 0 failed, rc=0).
        _self_mod = sys.modules[__name__]  # this module, however it was loaded/aliased
        _orig_fire_leg = _self_mod._fire_leg
        _stub_calls = []

        def _stub_fire_leg(manifest_, batch_, packet_rel, claim, vname, timeout_ms):
            _stub_calls.append((packet_rel, claim, vname))
            with open(os.path.join(batch_, "verdicts", vname), "w",
                      encoding="utf-8", newline="\n") as fh:
                json.dump({"verifier": {"vendor": "openai", "model": "gpt-5.5"},
                           "uncertainty": "confident", "verdict": "confirmed",
                           "reason": "C1: FOUND wiki/topic/va.md -- \"x\""}, fh)
            return True, ""

        _self_mod._fire_leg = _stub_fire_leg
        try:
            rc_fire = fire_phase(cout, "locate", events={"raw/2026-01-10-eA.md"})
        finally:
            _self_mod._fire_leg = _orig_fire_leg
        va0 = os.path.join(cout, "verdicts", caleg["id"] + "-locate-0.json")
        va1 = os.path.join(cout, "verdicts", caleg["id"] + "-locate-1.json")
        case("clearance: fire_phase(locate) over a 2-chunk clearance leg with the "
             "bridge call stubbed -- reaches the bridge-call point instead of "
             "KeyError('locate_claim'), both chunks produce verdict files, "
             "todo/cap accounting correct (2 fired, 0 failed, rc=0)",
             rc_fire == 0 and len(_stub_calls) == 2
             and os.path.isfile(va0) and os.path.getsize(va0) > 0
             and os.path.isfile(va1) and os.path.getsize(va1) > 0
             and all(claim == caleg["locate_claim"] for _p, claim, _v in _stub_calls))
        # clean the stub-fired verdicts so the (b)-onward planted/regular-fixture
        # verdict writes below start from the same clean verdicts/ dir they expect
        os.remove(va0)
        os.remove(va1)

        capacket = open(os.path.join(cout, caleg["locate_chunks"][0]["packet"]
                                     .replace("/", os.sep)), encoding="utf-8").read()
        # NOTE (flagged judgment call -- see run report): the spec text asks the
        # clearance locate packet to embed "the full raw event body" in addition to
        # the views, but the EXISTING locate packet format (build_locate/build_refire)
        # has NEVER embedded the raw event body -- only an "## EVENT: <path>" label
        # line (the raw body is embedded in ENUM packets only, never LOCATE packets).
        # Reusing _build_locate_chunks byte-for-byte (per "reuse the existing
        # packet-text builders, do not fork the wording") means clearance locate
        # packets do NOT carry the raw event body either -- this asserts that
        # (conservative) choice; it does not assert the spec's literal raw-body ask.
        case("clearance: locate packet embeds full current view body + the "
             "semantic-representation bar, IDENTICAL format to build_locate's "
             "locate packets (event path label only, no raw event body -- see "
             "flagged discrepancy in the run report)",
             "current body" in capacket
             and "JUDGE SEMANTIC REPRESENTATION, NOT STRING OVERLAP" in capacket
             and LOCATE_CONTRACT in capacket
             and "## EVENT: raw/2026-01-10-eA.md" in capacket)
        case("clearance: planted leg's claim text is the spec's removed-clause text "
             "(edits[].find), numbered from 1",
             cpleg["claims"] == [{"n": 1, "text": "The cap is fixed at 25 legs "
                                  "per invocation."}])
        cpptext = open(os.path.join(cout, cpleg["locate_packet"].replace("/", os.sep)),
                       encoding="utf-8").read()
        case("clearance: planted leg packet embeds the MUTATED view (claim absent)",
             "The cap is fixed at 25" in cpptext.split("## VIEW")[0]
             and "The cap is fixed at 25 legs per invocation."
             not in cpptext.split("## VIEW")[1])

        # (c) planted floor end-to-end on fixtures: floor_ok True when the MISSING
        # row matches absence_probes, False when absent (batch_accepted flips)
        def cwv(name, obj):
            with open(os.path.join(cout, "verdicts", name), "w", encoding="utf-8",
                      newline="\n") as fh:
                json.dump(obj, fh)

        vmeta = {"verifier": {"vendor": "openai", "model": "gpt-5.5"},
                 "uncertainty": "confident"}
        cwv(cpleg["id"] + "-locate-0.json", dict(vmeta, verdict="rejected",
            reason='C1: MISSING'))
        cwv(caleg["id"] + "-locate-0.json", dict(vmeta, verdict="confirmed",
            reason="\n".join('C%d: FOUND wiki/topic/va.md -- "x"' % i
                             for i in range(1, 6))))
        cwv(caleg["id"] + "-locate-1.json", dict(vmeta, verdict="confirmed",
            reason='C6: FOUND wiki/topic/va.md -- "x"\n'
                   'C7: FOUND wiki/topic/va.md -- "x"'))
        cwv(cbleg["id"] + "-locate-0.json", dict(vmeta, verdict="confirmed",
            reason="\n".join('C%d: FOUND wiki/topic/vb.md -- "x"' % i
                             for i in range(1, 4))))
        case("clearance: ingest floor_ok True (MISSING row matches absence_probes) "
             "-> batch PASS (0)", ingest(cout) == 0)
        cres = json.load(open(os.path.join(cout, "batch-result.json"),
                              encoding="utf-8"))
        case("clearance: batch-result planted_floor_met True, batch_accepted True",
             cres["planted_floor_met"] is True and cres["batch_accepted"] is True)
        case("clearance: batch-result protocol mirrors manifest (v2-clearance, "
             "not the enum-locate literal)", cres["protocol"] == "v2-clearance")

        # floor flips False when the planted claim is NOT a MISSING row (probe absent)
        cwv(cpleg["id"] + "-locate-0.json", dict(vmeta, verdict="confirmed",
            reason='C1: FOUND wiki/topic/vp.md -- "x"'))
        case("clearance: floor_ok flips False (planted claim not MISSING) -> "
             "FAIL (1), batch_accepted False", ingest(cout) == 1)
        cres = json.load(open(os.path.join(cout, "batch-result.json"),
                              encoding="utf-8"))
        case("clearance: batch-result planted_floor_met False, batch_accepted False",
             cres["planted_floor_met"] is False and cres["batch_accepted"] is False)
        cwv(cpleg["id"] + "-locate-0.json", dict(vmeta, verdict="rejected",
            reason='C1: MISSING'))

        # (e) ingest tier: clearance rows tagged T2-clean
        crec = json.load(open(os.path.join(cout, "records", cbleg["id"] + ".json"),
                              encoding="utf-8"))
        case("clearance: ingest tier T2-clean on a clearance row (no enum_parts)",
             crec["tier"] == "T2-clean")

        # (f) chunk-confirmed fill semantics hold in clearance mode: CONFIRMED chunk
        # back-fills its numbers as FOUND; explicit MISSING outranks
        cwv(caleg["id"] + "-locate-1.json", dict(vmeta, verdict="confirmed", reason=""))
        case("clearance: CONFIRMED chunk with no rows back-fills its claims FOUND "
             "via chunk-confirmed", ingest(cout) == 0)
        cwv(caleg["id"] + "-locate-1.json", dict(vmeta, verdict="confirmed",
            reason='C6: MISSING'))
        case("clearance: explicit MISSING row outranks chunk-confirmed fill -> "
             "event fails", ingest(cout) == 1)
        cres = json.load(open(os.path.join(cout, "batch-result.json"),
                              encoding="utf-8"))
        case("clearance: the failing event is named with its missing claim",
             "raw/2026-01-10-eA.md" in cres["events_fail"])
        cwv(caleg["id"] + "-locate-1.json", dict(vmeta, verdict="confirmed",
            reason='C6: FOUND wiki/topic/va.md -- "x"\n'
                   'C7: FOUND wiki/topic/va.md -- "x"'))
        case("clearance: restored to PASS (0) before refire cases", ingest(cout) == 0)

        # (d) refire: a fixture verdict leaving claim numbers unanswered ->
        # build_refire produces RECHUNK=3 chunks flagged refire:True for exactly
        # those numbers
        cwv(cbleg["id"] + "-locate-0.json", dict(vmeta, verdict="rejected",
            reason='C1: FOUND wiki/topic/vb.md -- "x"'))
        case("clearance: build-refire rc=0", build_refire(cout, root=cbase) == 0)
        cman2 = json.load(open(os.path.join(cout, "manifest.json"), encoding="utf-8"))
        cbleg2 = next(x for x in cman2["legs"] if x["id"] == cbleg["id"])
        case("clearance: refire re-chunks unanswered claims [2, 3], flagged "
             "refire:True",
             len(cbleg2["locate_chunks"]) == 2
             and cbleg2["locate_chunks"][1].get("refire") is True
             and cbleg2["locate_chunks"][1]["claims"] == [2, 3])
        cwv(cbleg["id"] + "-locate-1.json", dict(vmeta, verdict="confirmed",
            reason='C2: FOUND wiki/topic/vb.md -- "x"\nC3: FOUND wiki/topic/vb.md '
                   '-- "x"'))
        case("clearance: refire verdict completes the table -> batch PASS (0)",
             ingest(cout) == 0)

        # (g) sharding: --events filter fires only the named event's legs. Same
        # hermetic technique the v1 self-test uses for its shard/cap case (fire_phase
        # refuses BEFORE any bridge invocation once the (forced-down) cap is exceeded,
        # so the REFUSED leg count IS the count that would have fired -- no real firing,
        # no bridge dependency, deterministic).
        for f in os.listdir(os.path.join(cout, "verdicts")):
            os.remove(os.path.join(cout, "verdicts", f))
        import contextlib
        import io
        cap_saved = _v1.SPEND_CAP_LEGS
        try:
            _v1.SPEND_CAP_LEGS = 0
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc_shard = fire_phase(cout, "locate",
                                      events={"raw/2026-01-11-eB.md"})
            n_b_chunks = len(cbleg2["locate_chunks"])
            case("clearance: --events sharding counts ONLY the named event's locate "
                 "chunks against the cap (fire_phase refuses before any bridge call)",
                 rc_shard == 2
                 and ("%d legs exceeds" % n_b_chunks) in buf.getvalue())
        finally:
            _v1.SPEND_CAP_LEGS = cap_saved
    finally:
        shutil.rmtree(cbase, ignore_errors=True)

    base = tempfile.mkdtemp(prefix="f13v2-")
    try:
        spec_path = _v1._mk_fixture_tree(base)
        out = os.path.join(base, "b")
        rc = prepare(base, out, [spec_path])
        case("prepare v2 rc=0", rc == 0)
        man = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
        case("v2 manifest: protocol tag + enum packets exist",
             man["protocol"] == "v2-enumerate-locate" and all(
                 os.path.isfile(os.path.join(out, x["enum_packet"]))
                 for x in man["legs"]))
        pleg = next(x for x in man["legs"] if x["planted"])
        rleg = next(x for x in man["legs"] if not x["planted"]
                    and x["event"] == "raw/2026-01-02-e2.md")

        def wv(name, obj):
            with open(os.path.join(out, "verdicts", name), "w", encoding="utf-8",
                      newline="\n") as fh:
                json.dump(obj, fh)

        vmeta = {"verifier": {"vendor": "openai", "model": "gpt-5.5"},
                 "uncertainty": "confident"}
        # enum verdicts (planted event e1 has 2 claims: price + refund window)
        wv(pleg["id"] + "-enum.json", dict(vmeta, verdict="confirmed",
           reason='C1: "launch price is $49/mo" -- price\n'
                  'C2: "refund window is 30 days" -- refund'))
        wv(rleg["id"] + "-enum.json", dict(vmeta, verdict="confirmed",
           reason='C1: "beta cap is 200 seats" -- cap'))
        for leg in man["legs"]:
            if leg["id"] not in (pleg["id"], rleg["id"]):
                wv(leg["id"] + "-enum.json", dict(vmeta, verdict="confirmed",
                   reason='C1: "beta opens 2026-02-01" -- open\n'
                          'C2: "gamma budget is $10k" -- budget'))
        case("build-locate rc=0", build_locate(out, root=base) == 0)
        man = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
        pleg = next(x for x in man["legs"] if x["planted"])
        ptext = open(os.path.join(out, pleg["locate_packet"].replace("/", os.sep)),
                     encoding="utf-8").read()
        case("planted locate packet embeds the MUTATED view (claim absent)",
             "refund window" in ptext.split("## VIEW")[0]
             and "The refund window is 30 days." not in ptext.split("## VIEW")[1])

        # locate verdicts: planted -> C2 MISSING (probe: fixture spec has no
        # absence_probes -> add matcher expectation via text)
        wv(pleg["id"] + "-locate-0.json", dict(vmeta, verdict="rejected",
           reason='C1: FOUND wiki/topic/v1.md -- "launch price is $49/mo"\n'
                  'C2: MISSING'))
        for leg in man["legs"]:
            if leg["planted"]:
                continue
            n = len(leg.get("claims", []))
            reason = "\n".join('C%d: FOUND %s -- "x"' % (i + 1, leg["views"][0])
                               for i in range(n))
            wv(leg["id"] + "-locate-0.json", dict(vmeta, verdict="confirmed",
                                                reason=reason))
        # give the planted spec a probe so the floor matcher works
        man["legs"] = [dict(x, absence_probes=["refund window"]) if x["planted"]
                       else x for x in man["legs"]]
        json.dump(man, open(os.path.join(out, "manifest.json"), "w",
                            encoding="utf-8", newline="\n"), indent=2, sort_keys=True)
        case("ingest: planted MISSING row + all located -> PASS (0)",
             ingest(out) == 0)

        # table-authority: confirmed verdict but a MISSING row -> event FAILS
        wv(rleg["id"] + "-locate-0.json", dict(vmeta, verdict="confirmed",
           reason='C1: MISSING'))
        case("ingest: table beats verdict bit (confirmed + MISSING row -> fail, 1)",
             ingest(out) == 1)
        res = json.load(open(os.path.join(out, "batch-result.json"), encoding="utf-8"))
        case("result: the failing event carries its complete missing list",
             res["events_fail"] == [rleg["event"]] and res["missing_total"] == 1)
        case("result: batch-result protocol mirrors manifest (v2-enumerate-locate "
             "in standard mode)", res["protocol"] == "v2-enumerate-locate")
        wv(rleg["id"] + "-locate-0.json", dict(vmeta, verdict="confirmed",
           reason='C1: FOUND wiki/topic/v2.md -- "beta cap"'))

        # planted floor: planted locate says all FOUND -> floor fails (1)
        wv(pleg["id"] + "-locate-0.json", dict(vmeta, verdict="confirmed",
           reason='C1: FOUND wiki/topic/v1.md -- "x"\n'
                  'C2: FOUND wiki/topic/v1.md -- "y"'))
        case("ingest: planted claim NOT missing -> floor FAIL (1)", ingest(out) == 1)
        wv(pleg["id"] + "-locate-0.json", dict(vmeta, verdict="rejected",
           reason='C1: FOUND wiki/topic/v1.md -- "launch price"\nC2: MISSING'))

        # rejected chunk with no parseable row -> indecisive (ambiguous rejection);
        # a CONFIRMED chunk with no rows would instead fill as chunk-confirmed
        wv(rleg["id"] + "-locate-0.json", dict(vmeta, verdict="rejected", reason=""))
        case("ingest: ambiguous rejected chunk -> indecisive -> floor check (2)",
             ingest(out) == 2)
        wv(rleg["id"] + "-locate-0.json", dict(vmeta, verdict="confirmed", reason=""))
        case("ingest: confirmed chunk fills unanswered as FOUND -> PASS (0)",
             ingest(out) == 0)
        wv(rleg["id"] + "-locate-0.json", dict(vmeta, verdict="rejected", reason=""))

        # enum supplement: under-floor enum -> supplement packet -> exhaustion or
        # merged claims satisfy the floor
        man3 = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
        for x in man3["legs"]:
            if x["id"] == rleg["id"]:
                x["enum_floor"] = 3   # force under-floor (1 claim parsed)
        json.dump(man3, open(os.path.join(out, "manifest.json"), "w",
                             encoding="utf-8", newline="\n"), indent=2,
                  sort_keys=True)
        case("build-enum-supplement rc=0", build_enum_supplement(out) == 0)
        man3 = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
        rleg3 = next(x for x in man3["legs"] if x["id"] == rleg["id"])
        supp = (rleg3.get("enum_supplements") or [None])[0]
        case("supplement packet built, numbering continues at C2",
             supp is not None and os.path.isfile(os.path.join(
                 out, supp["packet"].replace("/", os.sep)))
             and "numbering from C2" in open(os.path.join(
                 out, supp["packet"].replace("/", os.sep)),
                 encoding="utf-8").read())
        wv(supp["verdict"], dict(vmeta, verdict="rejected",
           reason='C2: "beta ships dark" -- rollout\nC3: "beta is invite-only" -- gate'))
        mc, mex = _merged_enum(out, rleg3)
        case("merged enum: 3 claims, floor met, not exhausted",
             len(mc) == 3 and not mex)
        case("supplement: floor met -> no further packet",
             build_enum_supplement(out) == 0 and len(json.load(open(
                 os.path.join(out, "manifest.json"), encoding="utf-8"))["legs"][
                 [x["id"] for x in man3["legs"]].index(rleg["id"])].get(
                 "enum_supplements", [])) == 1)
        # exhaustion: reset floor high, confirm-verdict supplement satisfies it
        man4 = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
        for x in man4["legs"]:
            if x["id"] == rleg["id"]:
                x["enum_floor"] = 50
        json.dump(man4, open(os.path.join(out, "manifest.json"), "w",
                             encoding="utf-8", newline="\n"), indent=2,
                  sort_keys=True)
        wv(supp["verdict"], dict(vmeta, verdict="confirmed",
                                 reason="the list is complete"))
        rleg4 = next(x for x in man4["legs"] if x["id"] == rleg["id"])
        mc, mex = _merged_enum(out, rleg4)
        case("exhaustion: confirmed supplement satisfies an unmet floor",
             mex and len(mc) == 1)
        # restore: floor back to sane, drop supplements so later cases see 1 chunk
        for x in man4["legs"]:
            if x["id"] == rleg["id"]:
                x["enum_floor"] = 1
                x.pop("enum_supplements", None)
        json.dump(man4, open(os.path.join(out, "manifest.json"), "w",
                             encoding="utf-8", newline="\n"), indent=2,
                  sort_keys=True)
        case("build-locate after supplement play rc=0",
             build_locate(out, root=base) == 0)

        # enum-chunked: a giant event whose enum cannot fit one reason field gets
        # per-part enum legs; full part coverage = exhaustion by construction
        man5 = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
        for x in man5["legs"]:
            if x["id"] == rleg["id"]:
                x["enum_floor"] = 40
        json.dump(man5, open(os.path.join(out, "manifest.json"), "w",
                             encoding="utf-8", newline="\n"), indent=2,
                  sort_keys=True)
        # widen the event packet so _split_event yields >=2 parts
        rpk = os.path.join(out, rleg["enum_packet"].replace("/", os.sep))
        orig_pkt = open(rpk, encoding="utf-8").read()
        pad = "\n".join("# H%d\npadding line %d" % (i, i) for i in range(400))
        open(rpk, "w", encoding="utf-8", newline="\n").write(
            orig_pkt.replace("```markdown\n", "```markdown\n" + pad + "\n", 1))
        case("build-enum-chunked rc=0", build_enum_chunked(out) == 0)
        man5 = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
        rleg5 = next(x for x in man5["legs"] if x["id"] == rleg["id"])
        nparts = len(rleg5.get("enum_parts") or [])
        case("enum-chunked: >=2 part packets built", nparts >= 2)
        for s in rleg5["enum_parts"]:
            wv(s["verdict"], dict(vmeta, verdict="confirmed", reason="no claims"))
        wv(rleg5["enum_parts"][0]["verdict"], dict(vmeta, verdict="rejected",
           reason='C1: "beta cap is 200 seats" -- cap\nC2: "gamma is $10k" -- b'))
        mc, mex = _merged_enum(out, rleg5)
        case("enum-chunked: full part coverage -> exhausted despite unmet floor",
             mex and len(mc) >= 2)
        os.remove(os.path.join(out, "verdicts",
                               rleg5["enum_parts"][-1]["verdict"]))
        mc, mex = _merged_enum(out, rleg5)
        case("enum-chunked: a missing part verdict blocks exhaustion", not mex)
        # restore state for the refire cases below
        open(rpk, "w", encoding="utf-8", newline="\n").write(orig_pkt)
        for x in man5["legs"]:
            if x["id"] == rleg["id"]:
                x["enum_floor"] = 1
                x.pop("enum_parts", None)
        json.dump(man5, open(os.path.join(out, "manifest.json"), "w",
                             encoding="utf-8", newline="\n"), indent=2,
                  sort_keys=True)
        case("build-locate after enum-chunked play rc=0",
             build_locate(out, root=base) == 0)

        # refire: unanswered claim numbers get new 3-claim chunks; a verdict on the
        # refire chunk completes the table and flips the event decisive
        case("build-refire rc=0", build_refire(out, root=base) == 0)
        man2 = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
        rleg2 = next(x for x in man2["legs"] if x["id"] == rleg["id"])
        case("refire: unanswered claim re-chunked and flagged",
             len(rleg2["locate_chunks"]) == 2
             and rleg2["locate_chunks"][1].get("refire") is True
             and rleg2["locate_chunks"][1]["claims"] == [1]
             and os.path.isfile(os.path.join(
                 out, rleg2["locate_chunks"][1]["packet"].replace("/", os.sep))))
        case("refire: idempotent on answered legs (planted leg untouched)",
             len(next(x for x in man2["legs"]
                      if x["planted"])["locate_chunks"]) == 1)
        wv(rleg["id"] + "-locate-1.json", dict(vmeta, verdict="confirmed",
           reason='C1: FOUND wiki/topic/v2.md -- "beta cap"'))
        case("refire verdict completes the table -> batch PASS (0)", ingest(out) == 0)
        case("build-refire: nothing left unanswered -> adds nothing",
             build_refire(out, root=base) == 0 and len(json.load(open(
                 os.path.join(out, "manifest.json"), encoding="utf-8"))["legs"][
                 [x["id"] for x in man2["legs"]].index(rleg["id"])]
                 ["locate_chunks"]) == 2)
        wv(rleg["id"] + "-locate-0.json", dict(vmeta, verdict="confirmed",
           reason='C1: FOUND wiki/topic/v2.md -- "beta cap"'))
        # substrate: same-vendor locate leg -> INCONCLUSIVE
        wv(rleg["id"] + "-locate-0.json",
           {"verifier": {"vendor": "anthropic", "model": "claude-sonnet-5"},
            "uncertainty": "confident", "verdict": "confirmed",
            "reason": 'C1: FOUND wiki/topic/v2.md -- "beta cap"'})
        case("ingest: same-vendor locate leg -> INCONCLUSIVE (2)", ingest(out) == 2)

        # operator_full_run_authorization: v2 has no independent implementation --
        # main() resolves BOTH --fire-enum and --fire-locate through _v1's helper
        # ONCE before dispatch (2026-07-07 live-ops nit; full structural-class
        # coverage lives in audit-content.py's self-test). These two cases exercise
        # the resolution SEAM exactly as v2's main() calls it.
        case("full-run-auth: flag absent resolves clean through the v2 seam "
             "(same helper audit-content.py validates)",
             _v1.operator_full_run_authorization(["--fire-locate", "--batch", "x"])
             == (None, None))
        bogus_batch2 = os.path.join(base, "batch-bogus-authcheck-v2")
        os.makedirs(bogus_batch2, exist_ok=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_main2 = main(["audit-content-v2.py", "--fire-locate",
                             "--batch", bogus_batch2,
                             "--operator-authorized-full-run", "bogus.md"])
        case("main(): --fire-locate with a bogus authorization value REFUSES (2) "
             "before any dispatch, naming the artifact class",
             rc_main2 == 2 and "REFUSED" in buf.getvalue()
             and _v1.FULL_RUN_ARTIFACT_CLASS in buf.getvalue())
    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failed:
        print("audit-content-v2 (F13): FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("audit-content-v2 (F13): PASS (%d/%d)" % (total, total))
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
    batch = opt("--batch")
    if "--prepare" in args:
        planted = [args[i + 1] for i, a in enumerate(args)
                   if a == "--planted" and i + 1 < len(args)]
        events = opt("--events")
        events = [e.strip() for e in events.split(",")] if events else None
        return prepare(opt("--root"), opt("--out"), planted, events)
    if "--prepare-clearance" in args:
        planted = [args[i + 1] for i, a in enumerate(args)
                   if a == "--planted" and i + 1 < len(args)]
        return prepare_clearance(opt("--root"), opt("--out"), opt("--claims-file"),
                                 planted, closure="--closure" in args)
    fire_events = opt("--events")
    fire_events = set(e.strip() for e in fire_events.split(",")) if fire_events \
        else None
    # resolved ONCE here (not per fire_phase call site) -- both --fire-enum and
    # --fire-locate consume the same validated-artifact result (2026-07-07 live-ops
    # nit: the bare presence check is now a validated operator-artifact path; see
    # _v1.operator_full_run_authorization)
    full_run_path, refuse_reason = _v1.operator_full_run_authorization(args)
    if refuse_reason is not None:
        print("REFUSED -- --operator-authorized-full-run %s (requires a committed "
              "operator authorization artifact of class %s)"
              % (refuse_reason, _v1.FULL_RUN_ARTIFACT_CLASS))
        return 2
    if "--fire-enum" in args:
        return fire_phase(batch, "enum", int(opt("--timeout-ms", "0") or 0),
                          full_run_path is not None, fire_events)
    if "--build-locate" in args:
        return build_locate(batch, closure="--closure" in args)
    if "--build-refire" in args:
        return build_refire(batch)
    if "--build-enum-supplement" in args:
        return build_enum_supplement(batch)
    if "--build-enum-chunked" in args:
        return build_enum_chunked(batch)
    if "--fire-locate" in args:
        return fire_phase(batch, "locate", int(opt("--timeout-ms", "0") or 0),
                          full_run_path is not None, fire_events)
    if "--ingest" in args:
        return ingest(batch)
    print(__doc__.strip().split("\n\n")[-1])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
