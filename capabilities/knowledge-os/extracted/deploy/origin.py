#!/usr/bin/env python3
"""origin.py -- the F7 canonical-origin backfill rule (memory-engine v3, P1).

Assigns each legacy ledger event a canonical `origin`, the value that becomes a view's
`origin_max` (spec §5) and the input the boot-time capability wall reads (tcb §4/§8). This
assignment is **append-only after P1-live** and is reviewed at MIG-1 (mig1-composition
amendment §B2). This module is the *named mechanical rule* the spec demands (spec §242-245:
"a named mechanical rule OR per-event operator attestation"); events it cannot resolve
mechanically default to `unknown` (the conservative direction) and are surfaced for
per-event operator attestation in the MIG-1 B2 origin census.

The rule (first match wins), reasoned to the conservative/optimal path:
  0. PER-EVENT OPERATOR ATTESTATION (deploy/origin-attestations.yaml, the B2 review's
     output) -> the attested origin. This is the spec's second limb ("a named mechanical
     rule OR per-event operator attestation") and exists precisely for events the rule
     below routes to `unknown`.
  1. UNPARSEABLE (YAML-hostile frontmatter, the judgment class) -> `unknown`. We cannot
     verify the content mechanically; unknown is the most restrictive origin (>= external-
     scrape, tp:79(6)) -> the boot wall denies the most. B2 default.
  2. operator-decision file (filename matches a CONFIGURED human_prefix, e.g. `ryan-*` on
     this fork, after the date prefix -- see deploy/origin-config.yaml) -> `human`.
     Non-forgeable per amendment B6: the file is pre-existing committed git history, so it
     predates this build session's run window by construction (B6's "predates the session's
     run window" limb) -- a same-run session cannot mint it. Corroborated by a receipt trail
     when present (recorded in the census as extra evidence, not required for the mint).
  3. everything else parseable (hub `session-*` output, spike/findings notes) -> `corpus`.
     Project-internal, not external -- more restrictive than `human`, not quarantined like
     `external-scrape`/`unknown`. A session's origin >= origin_max of the human packet it
     consumed (F9); `corpus` is the conservative floor when that packet is unrecorded.

STAGING FLOOR (R-1, spec r1-build-decisions-2026-07-22.md Part 2): a new origin class,
`session-derived`, sits in ORIGIN_ORDER between `corpus` and `vendor-ref` -- project-
internal like `corpus` (it never leaves the repo), but model-mediated at a trust boundary
`corpus` never crossed: a session harvested and staged it, no human authored it as a
project file. It marks R-1 session-candidate files staged at `intake/session-candidates/`
(CANDIDATES_DIR) awaiting operator disposition -- NEVER operator-provenance on its own,
however parseable or well-formed the staged content looks. This is the whole of the
filename-mint fix: rule 2 above mints `human` from a filename alone, and a session writing
its own candidate file can choose that filename -- so for any event id under CANDIDATES_DIR
(is_candidate_path()), assign_origin() floors whatever the ordinary rules (0-3, including
rule 2, a declared_origin, and a B2 attestation) would have said via
origin_max([ordinary_result, "session-derived"]). The floor is monotone -- it can only push
a candidate's origin MORE restrictive (an unparseable candidate is still `unknown`), never
less; there is no rule ordering under which staging comes out less restrictive than
`session-derived`. See is_candidate_path() and the staging-floor block in assign_origin().

INSTANCE CONFIG, NOT ENGINE LOGIC: rule 2's filename prefix (`ryan` on this fork) is
per-instance trust-boundary config, not a hardcoded engine constant -- deploy/origin-
config.yaml's `human_prefixes:` list names the operator(s) this instance treats as
non-forgeable-by-filename. A fresh/template instance ships with NO name baked in; the
config file absent, unparseable, or PyYAML unavailable all degrade to an EMPTY prefix
list (fail-safe: nothing auto-mints human -- see load_origin_config()/is_human_named()).

SEMANTICS NOTE (operator-corrected 2026-07-03, B2 review): `human` means the event's
CONTENT came from the operator's direct input (decisions, dialogue, walkthroughs) --
these files are SESSION-TRANSCRIBED captures of that input, not hand-typed by the
operator. The taint property `human` asserts is content-provenance (trusted operator
input, nothing scraped/third-party smuggled in), never literal file authorship. The B6
non-forgeability (committed-history keying) is unaffected by this correction.

`origin_max` over a view's consumed events is the MOST RESTRICTIVE of their origins (origin
can only increase in restrictiveness through the loop, F9) -- one `unknown` consumed event
pins the view to `unknown`.

Usage:
  origin.py --self-test     run embedded fixtures
Exit codes: 0 = self-test PASS | 1 = self-test FAIL.
"""

import os
import posixpath
import re
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

_HERE = os.path.dirname(os.path.abspath(__file__))

# Restrictiveness ordering (spec §5 enum + tp:79(6)): least->most restrictive. origin_max is
# the max in THIS ordering (most restrictive) over a view's consumed events.
# `session-derived` (R-1, spec r1-build-decisions-2026-07-22.md Part 2.1) sits strictly
# between `corpus` and `vendor-ref` -- project-internal but model-mediated at a trust
# boundary `corpus` never crossed. Ranks are COMPUTED from this list position, never
# stored, so the insertion is safe for every existing record: nothing on disk encodes a
# rank, only an origin string, and this list is re-read fresh every process start.
ORIGIN_ORDER = ["human", "corpus", "session-derived", "vendor-ref", "external-scrape",
                "unknown"]
_RANK = {o: i for i, o in enumerate(ORIGIN_ORDER)}

# R-1 staging directory (spec Part 2.2): the harvester writes session-candidate files here
# (deploy/harvest-candidates.py, a later leg). is_candidate_path() is the SINGLE SOURCE OF
# TRUTH for "is this event id staged" that assign_origin()'s staging floor keys off of --
# every other module that needs the same test should import this rather than re-deriving
# its own prefix match (same discipline as is_human_named() being the one place rule 2's
# filename match lives).
CANDIDATES_DIR = "intake/session-candidates"


def is_candidate_path(event_id):
    """True iff `event_id` refers to a file under CANDIDATES_DIR. SEGMENT-BOUNDARY
    matching over the CANONICALISED path, not a naive test in either direction:

    - not a substring test: `intake/session-candidates-other/x.md` is a near-miss
      whose segment `session-candidates-other` != `session-candidates` -> False;
    - not a lexical prefix test either (cross-vendor round-1 finding, 2026-07-23):
      a bare `startswith(CANDIDATES_DIR + "/")` is dodged by an ABSOLUTE path
      (`C:/repo/intake/session-candidates/x.md`), a dot-component spelling
      (`intake/./session-candidates/x.md`, `intake/session-candidates/../
      session-candidates/x.md`), or a parent-relative one (`../intake/...`) --
      and a dodge here is not cosmetic: the caller falls through to the ordinary
      rules, where a `ryan-*` FILENAME mints `human`. The floor's whole point
      dies on any spelling miss.

    So: separators normalised, dot components collapsed (posixpath.normpath),
    then the SEGMENT PAIR ("intake", "session-candidates") is searched for
    ANYWHERE in the path with at least one further segment (the file) after it,
    case-insensitively (Windows filesystems are case-insensitive).

    DELIBERATE OVER-MATCH, fail-safe by construction: a path like
    `archive/intake/session-candidates/x.md` -- not the real staging directory --
    ALSO floors. The floor only ever RAISES restrictiveness (origin_max), so a
    false positive costs an over-restrictive origin for an oddly-named file,
    while a false negative mints unearned trust. Between those, over-match is
    the only defensible direction for a trust boundary."""
    norm = posixpath.normpath(str(event_id).replace("\\", "/"))
    segs = [s.lower() for s in norm.split("/") if s]
    want = [s.lower() for s in CANDIDATES_DIR.split("/")]
    n = len(want)
    return any(segs[i:i + n] == want
               for i in range(0, len(segs) - n))  # needs >=1 segment after the pair

# operator-decision-file date prefix (B6 fixture (c)): stripped before the CONFIGURED
# human_prefixes (deploy/origin-config.yaml) are matched -- see is_human_named() below.
_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{6})?-")

_ORIGIN_CONFIG_FILE = os.path.join(_HERE, "origin-config.yaml")

# Lazy module-level cache of the compiled human_prefixes regexes, loaded once from the LIVE
# config file on first use (None = not yet loaded). Only populated by the no-override (path=
# None) call path; a self-test that supplies an explicit `path` always loads fresh and never
# touches or populates this cache, so fixture configs never leak between cases.
_human_prefix_cache = None


def _stem_after_date(event_id):
    """Strip a leading YYYY-MM-DD[THHMMSS]- prefix from an event id / basename."""
    base = os.path.basename(event_id)
    if base.endswith(".md"):
        base = base[:-3]
    return _DATE_PREFIX_RE.sub("", base)


def _read(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return fh.read()


def load_origin_config(path=None):
    """Load deploy/origin-config.yaml -> {"human_prefixes": [str, ...]}.

    FAIL-SAFE (doctrine: never auto-mint human on an unresolvable config): the file
    ABSENT, PyYAML unavailable, unreadable, or structurally unparseable/malformed all
    degrade to an EMPTY human_prefixes list -- never a crash, never a silent grant. Same
    defensive-loader contract deploy/staleness.py's load_known_holes/_load_alias_map use
    (yaml unavailable or missing/unreadable/malformed file -> empty, degrade don't crash).
    `path` overrides the default (the repo's live deploy/origin-config.yaml) so a
    self-test can point this at a fixture file without depending on the live one."""
    path = path or _ORIGIN_CONFIG_FILE
    if yaml is None or not os.path.isfile(path):
        return {"human_prefixes": []}
    try:
        data = yaml.safe_load(_read(path)) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return {"human_prefixes": []}
    if not isinstance(data, dict):
        return {"human_prefixes": []}
    raw = data.get("human_prefixes")
    if not isinstance(raw, list):
        return {"human_prefixes": []}
    prefixes = []
    for p in raw:
        # STRICT member validation (cross-vendor review finding): a non-string
        # member (dict/number/None) or an empty/whitespace-only string is NOT
        # stringified-and-kept -- it marks the ENTIRE config malformed, and the
        # universal fail-safe degrade applies (empty prefixes, nothing auto-mints
        # human), same direction as absent/unparseable. Rationale: a silently
        # stringified dict would become a bogus prefix, and an empty-string
        # member would compile to '^-' and match ANY '-'-containing stem.
        if not isinstance(p, str) or not p.strip():
            return {"human_prefixes": []}
        prefixes.append(p.strip())
    return {"human_prefixes": prefixes}


def _human_prefix_patterns(path=None):
    """Compiled case-insensitive `^<prefix>-` regexes for the configured human_prefixes.
    path=None uses (and lazily populates) the module-level cache over the LIVE config
    file; an explicit path (self-test seam) always loads fresh and bypasses the cache."""
    global _human_prefix_cache
    if path is not None:
        cfg = load_origin_config(path)
        return [re.compile(r"^%s-" % re.escape(p), re.IGNORECASE)
                for p in cfg["human_prefixes"]]
    if _human_prefix_cache is None:
        cfg = load_origin_config(None)
        _human_prefix_cache = [re.compile(r"^%s-" % re.escape(p), re.IGNORECASE)
                               for p in cfg["human_prefixes"]]
    return _human_prefix_cache


def is_human_named(event_id, path=None):
    """SINGLE SOURCE OF TRUTH for the F7/B6 operator-decision-file pattern: True iff
    `event_id`'s stem-after-date matches one of deploy/origin-config.yaml's configured
    human_prefixes (case-insensitive `^<prefix>-`). Every live-logic replication of this
    rule elsewhere in deploy/ routes through this function (or, where a caller must stay
    import-free of this module, replicates this same tiny config read) rather than
    hardcoding a prefix of its own. `path` overrides the config file (self-test seam)."""
    stem = _stem_after_date(event_id)
    return any(p.match(stem) for p in _human_prefix_patterns(path))


def load_attestations(path=None):
    """Load the B2 per-event operator attestations -> {event_id: entry-dict}. Default
    path: deploy/origin-attestations.yaml next to this module. Missing file -> {}."""
    try:
        import yaml
    except ImportError:  # pragma: no cover
        return {}
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "origin-attestations.yaml")
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = yaml.safe_load(fh.read()) or {}
    out = {}
    for a in (data.get("attestations") or []):
        if isinstance(a, dict) and a.get("id") and a.get("origin") in _RANK:
            out[str(a["id"]).replace("\\", "/")] = a
    return out


def _assign_ordinary(event_id, parseable, has_receipt, declared_origin, attestation,
                     origin_config_path):
    """The pre-staging-floor rule cascade -- exactly rules 0-3 from the module docstring,
    unaware of CANDIDATES_DIR. Split out of assign_origin() so the staging floor (below)
    can be applied EXACTLY ONCE, uniformly, over whichever of these four rules fired,
    rather than re-implementing the floor inside each branch (four chances to miss one)."""
    if attestation and attestation.get("origin") in _RANK:
        return (attestation["origin"],
                "operator attestation (B2): %s [%s, %s]"
                % (attestation.get("basis", "").strip() or "attested",
                   attestation.get("attested_by", "operator"),
                   attestation.get("date", "?")),
                True)
    if declared_origin:
        o = declared_origin.strip()
        if o in _RANK:
            return o, "declared in event frontmatter", True
    if not parseable:
        return "unknown", "unparseable (judgment-class); conservative default (B2)", False
    if is_human_named(event_id, path=origin_config_path):
        basis = ("operator-decision file (configured human_prefixes, deploy/origin-"
                 "config.yaml); pre-existing committed history predates session run "
                 "window (B6)")
        if has_receipt:
            basis += "; receipt trail present"
        return "human", basis, True
    return "corpus", "project-internal session/spike output; conservative floor (F9)", True


def assign_origin(event_id, parseable, has_receipt=False, declared_origin=None,
                  attestation=None, origin_config_path=None):
    """Return (origin, basis, attested). `attested` marks a mechanically-derived trusted
    origin (not a judgment default). `declared_origin` (rare for legacy) short-circuits.
    `attestation` (a load_attestations entry) is the B2 operator override -- it wins
    over everything, including the unparseable default (that is its purpose).
    `origin_config_path` overrides deploy/origin-config.yaml (self-test seam; see
    is_human_named()) -- production callers never pass it, so they always read the live
    per-instance config.

    STAGING FLOOR (R-1, spec Part 2.2): if `event_id` is under CANDIDATES_DIR
    (is_candidate_path()), the origin the rules above would have assigned is floored via
    origin_max([ordinary_result, "session-derived"]) before it is returned -- so nothing
    that can be staged into intake/session-candidates/ (a same-run-mintable filename, the
    candidate's own declared_origin, or a B2 attestation naming a staging path) can lower a
    candidate's origin below session-derived. The floor can only raise restrictiveness
    (origin_max never lowers), and it never touches non-candidate event ids."""
    origin, basis, attested = _assign_ordinary(
        event_id, parseable, has_receipt, declared_origin, attestation, origin_config_path)
    if is_candidate_path(event_id):
        # `attested`: deliberately left UNCHANGED by the floor. Applying the floor is
        # itself a fixed, no-judgment mechanical rule (there is no operator discretion in
        # "is this path under CANDIDATES_DIR"), so a result the ordinary rule already
        # attested stays attested. The one case that reads as an exception is unparseable
        # staging (_assign_ordinary already set attested=False there) -- but that is not
        # the floor changing anything: it was ALREADY a judgment default before the floor
        # ever ran, and origin_max(["unknown", "session-derived"]) == "unknown" means the
        # floor doesn't even change the VALUE in that case, let alone its attested-ness.
        # So: pass `attested` straight through: the floor never upgrades a judgment default
        # into an attested fact, and never downgrades an attested fact into a judgment call.
        floored = origin_max([origin, "session-derived"])
        basis = (basis + " ; staging floor: intake/session-candidates/ can never assign an "
                 "origin less restrictive than session-derived (R-1 spec Part 2.2)")
        return floored, basis, attested
    return origin, basis, attested


def origin_max(origins):
    """Most-restrictive origin over an iterable (empty -> 'human', the identity: a view with
    no consumed events carries no taint)."""
    best = None
    for o in origins:
        r = _RANK.get(o, _RANK["unknown"])
        if best is None or r > best[0]:
            best = (r, o)
    return best[1] if best else "human"


def census(events, attestations=None, origin_config_path=None):
    """events: iterable of dicts {id, parseable, has_receipt, declared_origin?}. Returns the
    MIG-1 B2 origin-assignment census: per-origin counts, rule-derived vs judgment split, and
    the full attested list (each entry's origin + basis text). `attestations` (see
    load_attestations) resolves judgment-class events the operator has attested.
    `origin_config_path` overrides deploy/origin-config.yaml (self-test seam), threaded
    through to every assign_origin() call below."""
    attestations = attestations or {}
    counts = {o: 0 for o in ORIGIN_ORDER}
    rule_derived = 0
    judgment = 0
    attested_list = []
    unattested = []
    for e in events:
        o, basis, attested = assign_origin(
            e["id"], e.get("parseable", True), e.get("has_receipt", False),
            e.get("declared_origin"),
            attestation=attestations.get(str(e["id"]).replace("\\", "/")),
            origin_config_path=origin_config_path)
        counts[o] += 1
        entry = {"id": e["id"], "origin": o, "basis": basis, "attested": attested}
        if attested:
            rule_derived += 1
            attested_list.append(entry)
        else:
            judgment += 1
            unattested.append(entry)
    return {
        "counts": counts,
        "rule_derived": rule_derived,
        "judgment": judgment,
        "attested_list": attested_list,
        "unattested": unattested,
        "total": rule_derived + judgment,
    }


def self_test():
    import tempfile

    total = 0
    failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    def _mk_config(content):
        fd, path = tempfile.mkstemp(prefix="origin-config-", suffix=".yaml")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path

    # Every case below that exercises the human-prefix rule points at its OWN fixture
    # config, built in a temp file -- NEVER the repo's live deploy/origin-config.yaml.
    # This fork's live config happens to also say human_prefixes: [ryan], but the self-
    # test must not silently depend on that coincidence (a fresh/template instance ships
    # with no origin-config.yaml at all, and this self-test must still pass there).
    cfg_ryan = _mk_config("human_prefixes: [ryan]\n")
    cfg_alice = _mk_config("human_prefixes: [alice]\n")
    cfg_missing = os.path.join(tempfile.gettempdir(),
                               "origin-config-does-not-exist-%d.yaml" % os.getpid())
    cfg_bad1 = cfg_bad2 = cfg_bad3 = cfg_bad4 = cfg_bad5 = cfg_meta = None

    try:
        # rule cases
        o, _b, a = assign_origin("2026-05-02-ryan-decision-1-pk-strategy.md", True,
                                 has_receipt=True, origin_config_path=cfg_ryan)
        case("ryan-* decision file -> human (attested)", o == "human" and a is True)
        o, _b, a = assign_origin("2026-06-19-session-7.5-completion-pgbouncer-mtls.md", True,
                                 origin_config_path=cfg_ryan)
        case("session-* -> corpus (attested rule-derived)", o == "corpus" and a is True)
        o, _b, a = assign_origin("2026-06-05-products-sync-spike-findings.md", True,
                                 origin_config_path=cfg_ryan)
        case("misc project file -> corpus", o == "corpus")
        o, _b, a = assign_origin("2026-06-09-ryan-services-entity-schema.md", False,
                                 origin_config_path=cfg_ryan)
        case("UNPARSEABLE ryan-* -> unknown (unparseable wins, conservative)",
             o == "unknown" and a is False)
        o, _b, a = assign_origin("x.md", True, declared_origin="external-scrape",
                                 origin_config_path=cfg_ryan)
        case("declared origin short-circuits", o == "external-scrape")

        # B2 operator attestation: wins over the unparseable default (its purpose)
        att = {"origin": "human", "attested_by": "operator", "date": "2026-07-03",
               "basis": "content from operator's direct input, session-transcribed"}
        o, b, a = assign_origin("2026-06-09-ryan-services-entity-schema.md", False,
                                attestation=att, origin_config_path=cfg_ryan)
        case("B2 attestation resolves an unparseable event (attested=True)",
             o == "human" and a is True and "operator attestation (B2)" in b)
        o, _b, a = assign_origin("x.md", False, attestation={"origin": "bogus-value"},
                                 origin_config_path=cfg_ryan)
        case("attestation with an invalid origin is IGNORED (falls back to unknown)",
             o == "unknown" and a is False)
        c2 = census([{"id": "raw/2026-06-09-ryan-services-entity-schema.md",
                      "parseable": False}],
                    attestations={"raw/2026-06-09-ryan-services-entity-schema.md": att},
                    origin_config_path=cfg_ryan)
        case("census consumes attestations (0 judgment, 0 unattested)",
             c2["judgment"] == 0 and not c2["unattested"]
             and c2["counts"]["human"] == 1)

        # origin_max
        case("origin_max picks most restrictive", origin_max(["human", "corpus", "human"]) == "corpus")
        case("origin_max: one unknown pins the view", origin_max(["human", "unknown"]) == "unknown")
        case("origin_max of empty is human (no taint)", origin_max([]) == "human")
        case("origin_max human-only stays human", origin_max(["human", "human"]) == "human")

        # ---- R-1 `session-derived` origin class + the staging floor (spec
        # r1-build-decisions-2026-07-22.md Part 2) --------------------------------------
        case("session-derived sits between corpus and vendor-ref in ORIGIN_ORDER",
             ORIGIN_ORDER.index("corpus") < ORIGIN_ORDER.index("session-derived")
             < ORIGIN_ORDER.index("vendor-ref"))
        case("origin_max: corpus + session-derived -> session-derived (more restrictive)",
             origin_max(["corpus", "session-derived"]) == "session-derived")
        case("origin_max: session-derived + unknown -> unknown (unknown still pins)",
             origin_max(["session-derived", "unknown"]) == "unknown")
        case("origin_max: human + session-derived -> session-derived",
             origin_max(["human", "session-derived"]) == "session-derived")

        # is_candidate_path: path-PREFIX test, not substring; both separators; near-miss
        case("is_candidate_path: a staged file under the dir -> True",
             is_candidate_path("intake/session-candidates/2026-07-22-candidate-abc123.md")
             is True)
        case("is_candidate_path: an unrelated path -> False",
             is_candidate_path("raw/2026-07-22-ryan-decision.md") is False)
        case("is_candidate_path: NEAR-MISS directory name (substring, not prefix) -> False",
             is_candidate_path(
                 "intake/session-candidates-other/x.md") is False)
        case("is_candidate_path: Windows-separator input normalises and matches",
             is_candidate_path(
                 "intake\\session-candidates\\2026-07-22-candidate-abc123.md") is True)
        case("is_candidate_path: leading ./ is stripped",
             is_candidate_path(
                 "./intake/session-candidates/2026-07-22-candidate-abc123.md") is True)

        # canonicalisation (cross-vendor round-1 finding, 2026-07-23): every
        # alternative SPELLING of a staged path must still floor -- a lexical
        # prefix test was dodged by all four of these, and a dodge falls through
        # to the filename rule where a ryan-* name mints human.
        case("is_candidate_path: ABSOLUTE path to a staged file still matches "
             "(segment scan, not prefix)",
             is_candidate_path(
                 "C:/repo/intake/session-candidates/2026-07-22-ryan-x.md") is True)
        case("is_candidate_path: dot-component spelling (intake/./...) collapses "
             "and matches",
             is_candidate_path(
                 "intake/./session-candidates/2026-07-22-ryan-x.md") is True)
        case("is_candidate_path: dot-dot spelling (session-candidates/../"
             "session-candidates/) collapses and matches",
             is_candidate_path("intake/session-candidates/../session-candidates/"
                               "2026-07-22-ryan-x.md") is True)
        case("is_candidate_path: parent-relative spelling (../intake/...) matches",
             is_candidate_path(
                 "../intake/session-candidates/2026-07-22-ryan-x.md") is True)
        case("is_candidate_path: case-insensitive (Windows filesystems are)",
             is_candidate_path(
                 "INTAKE/Session-Candidates/2026-07-22-ryan-x.md") is True)
        case("is_candidate_path: mid-path over-match IS matched (deliberate, "
             "fail-safe: the floor may only over-apply, never under-apply)",
             is_candidate_path(
                 "archive/intake/session-candidates/x.md") is True)
        case("is_candidate_path: the directory itself (no file segment after the "
             "pair) -> False",
             is_candidate_path("intake/session-candidates") is False)
        case("is_candidate_path: near-miss still False AFTER canonicalisation",
             is_candidate_path(
                 "intake/./session-candidates-other/x.md") is False)
        # the floor holds through an alternative spelling END-TO-END: an absolute
        # path to a ryan-named staged file assigns session-derived, never human.
        o, b, _a = assign_origin(
            "C:/repo/intake/session-candidates/2026-07-22-ryan-decision.md", True,
            origin_config_path=cfg_ryan)
        case("staging floor: ABSOLUTE-path spelling of a ryan-named staged file "
             "still floors to session-derived (the round-1 bypass, closed)",
             o == "session-derived" and "staging floor" in b)

        # the staging floor itself, against a [ryan] fixture config (NEVER the live
        # deploy/origin-config.yaml -- same discipline as every other case in this file).
        staged_ryan_named = "intake/session-candidates/2026-07-22-ryan-anything.md"
        o, b, a = assign_origin(staged_ryan_named, True, origin_config_path=cfg_ryan)
        case("staging floor: a ryan-* FILENAME staged in candidates/ -> session-derived, "
             "NOT human (the filename-mint fix)",
             o == "session-derived" and "staging floor" in b)
        case("staging floor: attested stays True (mechanically derived, not a judgment "
             "default)", a is True)

        # the SAME filename OUTSIDE the candidates directory still mints human -- proves
        # the floor is scoped to CANDIDATES_DIR, not a global regression of rule 2.
        o, _b, a = assign_origin("intake/2026-07-22-ryan-anything.md", True,
                                 origin_config_path=cfg_ryan)
        case("staging floor is SCOPED: the same filename outside candidates/ still -> "
             "human", o == "human" and a is True)

        # declared_origin: human inside staging cannot lower the floor either
        o, b, a = assign_origin(
            "intake/session-candidates/2026-07-22-candidate-decl.md", True,
            declared_origin="human", origin_config_path=cfg_ryan)
        case("staging floor: declared_origin=human inside staging -> session-derived",
             o == "session-derived" and "staging floor" in b)

        # B2 attestation claiming human inside staging cannot lower the floor either
        att_human = {"origin": "human", "attested_by": "operator", "date": "2026-07-22"}
        o, b, a = assign_origin(
            "intake/session-candidates/2026-07-22-candidate-att.md", True,
            attestation=att_human, origin_config_path=cfg_ryan)
        case("staging floor: attestation claiming human inside staging -> session-derived",
             o == "session-derived" and "staging floor" in b)

        # B2 attestation claiming something ALREADY more restrictive than session-derived
        # is NOT lowered by the floor -- the floor only ever raises restrictiveness.
        att_scrape = {"origin": "external-scrape", "attested_by": "operator",
                      "date": "2026-07-22"}
        o, b, a = assign_origin(
            "intake/session-candidates/2026-07-22-candidate-scrape.md", True,
            attestation=att_scrape, origin_config_path=cfg_ryan)
        case("staging floor never LOWERS restrictiveness: attestation claiming "
             "external-scrape inside staging stays external-scrape",
             o == "external-scrape" and "staging floor" in b)

        # unparseable staged candidate -> unknown, and attested stays False (a judgment
        # default the floor does not resolve, per the docstring's chosen semantics).
        o, b, a = assign_origin(
            "intake/session-candidates/2026-07-22-candidate-bad.md", False,
            origin_config_path=cfg_ryan)
        case("staging floor: an unparseable staged candidate -> unknown",
             o == "unknown" and "staging floor" in b)
        case("staging floor: unparseable staged candidate stays attested=False "
             "(judgment default, not resolved by the floor)", a is False)

        # census
        c = census([
            {"id": "2026-05-02-ryan-decision-1-pk-strategy.md", "parseable": True, "has_receipt": True},
            {"id": "2026-06-19-session-7.5-completion.md", "parseable": True},
            {"id": "2026-06-09-ryan-services-entity-schema.md", "parseable": False},
        ], origin_config_path=cfg_ryan)
        case("census counts human/corpus/unknown", c["counts"]["human"] == 1
             and c["counts"]["corpus"] == 1 and c["counts"]["unknown"] == 1)
        case("census: unparseable is in the judgment (unattested) list",
             c["judgment"] == 1 and c["unattested"][0]["origin"] == "unknown")
        case("census total == inputs", c["total"] == 3)

        # ---- origin-config.yaml: trust-boundary config, NOT a hardcoded engine constant --
        cfg = load_origin_config(cfg_ryan)
        case("load_origin_config parses human_prefixes: [ryan]", cfg["human_prefixes"] == ["ryan"])
        case("is_human_named('ryan-x.md') under ryan-config -> True",
             is_human_named("2026-01-01-ryan-x.md", path=cfg_ryan) is True)
        case("is_human_named('alice-x.md') under ryan-config -> False",
             is_human_named("2026-01-01-alice-x.md", path=cfg_ryan) is False)

        # ABSENT config -> fail-safe EMPTY prefix list -> nothing auto-mints human. This is
        # the template-port case: a fresh instance with no origin-config.yaml at all must
        # never silently mint human for a `ryan-*`-named file -- it falls through to the
        # ordinary parseable/unmatched rule (-> corpus), never a magic auto-trust default.
        cfg_absent = load_origin_config(cfg_missing)
        case("ABSENT config -> empty human_prefixes (fail-safe)", cfg_absent["human_prefixes"] == [])
        case("is_human_named('ryan-x.md') with ABSENT config -> False (no auto-mint)",
             is_human_named("2026-01-01-ryan-x.md", path=cfg_missing) is False)
        o, _b, a = assign_origin("2026-01-01-ryan-x.md", True, origin_config_path=cfg_missing)
        case("ABSENT config: ryan-* raw event assigns corpus, NOT human (no magic trigger"
             " when unconfigured)", o == "corpus")

        # MALFORMED/UNPARSEABLE config -> same fail-safe empty degrade, never a crash.
        cfg_bad1 = _mk_config("- just\n- a\n- list\n")
        case("MALFORMED config (top-level list, not a mapping) -> empty (fail-safe)",
             load_origin_config(cfg_bad1)["human_prefixes"] == [])
        cfg_bad2 = _mk_config("human_prefixes: not-a-list\n")
        case("MALFORMED config (human_prefixes not a list) -> empty (fail-safe)",
             load_origin_config(cfg_bad2)["human_prefixes"] == [])
        cfg_bad3 = _mk_config("human_prefixes: [ryan, alice\n")
        case("UNPARSEABLE config (YAML-hostile) -> empty (fail-safe), never a crash",
             load_origin_config(cfg_bad3)["human_prefixes"] == [])

        # STRICT member validation (cross-vendor review): any non-string or
        # empty-string MEMBER marks the ENTIRE config malformed -> empty
        # prefixes -- even members that would individually be valid mint
        # nothing (a partially-honored trust config is worse than an honestly
        # empty one).
        cfg_bad4 = _mk_config("human_prefixes: [ryan, 42]\n")
        case("MALFORMED config (non-string member) -> ENTIRE config degrades to empty",
             load_origin_config(cfg_bad4)["human_prefixes"] == [])
        o, _b, a = assign_origin("2026-01-01-ryan-x.md", True, origin_config_path=cfg_bad4)
        case("MALFORMED config (non-string member): even the valid 'ryan' member mints"
             " nothing -> corpus", o == "corpus")
        cfg_bad5 = _mk_config("human_prefixes: ['', ryan]\n")
        case("MALFORMED config (empty-string member) -> empty (never compiles a '^-'"
             " catch-all)", load_origin_config(cfg_bad5)["human_prefixes"] == [])
        case("MALFORMED config (empty-string member): '-'-containing stem does NOT mint"
             " human", is_human_named("2026-01-01-anything-x.md", path=cfg_bad5) is False)

        # LITERAL prefix matching (no regex injection via prefix content):
        # prefixes are re.escape()d before compiling, so a metachar like '.'
        # matches itself only.
        cfg_meta = _mk_config("human_prefixes: [a.b]\n")
        case("LITERAL matching: prefix 'a.b' matches 'a.b-*'",
             is_human_named("2026-01-01-a.b-decision.md", path=cfg_meta) is True)
        case("LITERAL matching: prefix 'a.b' does NOT wildcard-match 'axb-*'"
             " (re.escape, no regex injection)",
             is_human_named("2026-01-01-axb-decision.md", path=cfg_meta) is False)

        # DIFFERENT prefix (alice) -- proves this is per-instance DATA, not an engine
        # constant: an instance configured for a different operator name mints human for
        # THAT name's files, and no longer for "ryan-*" (which is meaningless to it).
        o, _b, a = assign_origin("2026-01-01-alice-decision.md", True,
                                 origin_config_path=cfg_alice)
        case("ALT-PREFIX config (alice): alice-* raw event -> human",
             o == "human" and a is True)
        o, _b, a = assign_origin("2026-01-01-ryan-decision.md", True,
                                 origin_config_path=cfg_alice)
        case("ALT-PREFIX config (alice): ryan-* raw event -> corpus, NOT human"
             " ('ryan' is not this instance's configured prefix)", o == "corpus")
    finally:
        for p in (cfg_ryan, cfg_alice, cfg_bad1, cfg_bad2, cfg_bad3, cfg_bad4,
                  cfg_bad5, cfg_meta):
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass

    if failed:
        print("origin self-test: FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("origin self-test: PASS (%d/%d)" % (total, total))
    return 0


def main(argv):
    if "--self-test" in argv[1:]:
        return self_test()
    print("usage: origin.py --self-test  (this module is imported by the backfill + drill)")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
