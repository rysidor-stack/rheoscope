#!/usr/bin/env python3
"""check-origin-propagation.py -- ORIGIN-PROPAGATION-INT registration-side sensor
(memory-engine v3 P4; test-plan tp:490-498 fixtures (a)/(b)/(c) verbatim; extends the
P1 gate-registration amendment C3/F-13 sensor with the P4 record-level CLI per
harness-v3.0/specs/memory-engine-v3-assemble-machinery-design-2026-07-05.md Component 3).

THE TAINT-LAUNDERING PATH THIS CLOSES: tainted view -> session-authored raw event ->
(session registers itself as) trusted origin -> a build/fix packet later assembled from
that "clean" raw is unknowingly built on scraped/unknown content. The session loop is the
seam: a session that CONSUMED a tainted packet must not be able to MINT a more-trusted
origin for what it writes back into the ledger. Origin can only increase in restrictiveness
through the loop (F9) -- never decrease, and never through self-report.

THE INTEGRATION SEAM: assemble.py's packet envelope records `packet_origin_max` (the most
restrictive origin_max over the views actually included in the packet) at emission time.
That field is written into the session's intake record (the fixture JSON this sensor reads
as `--intake`). The registration writer -- whatever later stamps a raw event's `origin` in
the ledger -- reads that same intake record to compute the floor the new registration must
respect. THE ENVELOPE FIELD IS THE CONTRACT: this sensor does not reach into assemble.py at
all; it validates the (intake record, registration record) pair against the F7 lattice
(deploy/origin.py ORIGIN_ORDER), so it stays correct however assemble.py's internals evolve,
as long as the envelope keeps emitting `packet_origin_max`.

RECORD SCHEMA (JSON, this sensor's contract):
  intake record       {"packet_origin_max": <origin-or-null>}
  registration record  {"event": <path/id str>, "origin": <origin-or-null>,
                        "receipt": <bool, optional, default false>}

RULE: registered `origin` must be >= `packet_origin_max` in origin.py's ORIGIN_ORDER lattice
(least->most restrictive: human, corpus, vendor-ref, external-scrape, unknown). No trust
upgrade through a session loop -- a session that consumed a more-restrictive packet cannot
register a less-restrictive origin for what it writes.

EXCEPTION: an event whose filename matches a CONFIGURED human-prefix operator-decision-file
pattern (deploy/origin.py's is_human_named() / deploy/origin-config.yaml -- `ryan-*` on this
fork, applied after the date prefix) MAY mint `origin: human` regardless of packet_origin_max,
but ONLY when the registration record carries a receipt trail (`"receipt": true`) -- fixture
(c). A matching file claiming `human` with no receipt trail is a violation (unproven mint),
not an exception. The prefix itself is instance config (deploy/origin-config.yaml), not a
constant hardcoded here or in origin.py -- see origin.py's module docstring.

Fixtures (a)/(b)/(c) per test-plan tp:490-498, verbatim:
  (a) intake packet_origin_max: external-scrape; registration stamps origin: external-scrape
      (or unknown) -- PASS (no upgrade).
  (b) same intake; registration stamps origin: human with no operator-decision-file backing
      -- FAIL (the violation is named in the registration audit).
  (c) a raw event whose filename matches the configured human-prefix pattern (ryan-* on this
      fork) AND carries a receipt entry -- permitted to register origin: human -- PASS.

Usage:
  check-origin-propagation.py --self-test
  check-origin-propagation.py --check --intake PATH --registration PATH

Exit codes: 0 = clean (registration honors the propagation rule) |
            1 = VIOLATION (trust upgrade / unproven human-mint), record + both origins named |
            2 = INCONCLUSIVE (either record unparseable/missing required fields; distinct
                from a violation -- we could not adjudicate, not "we adjudicated clean").

Stdlib-only. Imports origin.py via the importlib _load pattern (no sys.path mutation).
"""

import json
import os
import sys

try:  # cp1252 consoles + unicode paths: never let an encode error mask a verdict.
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(basename, alias):
    import importlib.util
    spec = importlib.util.spec_from_file_location(alias, os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


origin = _load("origin.py", "origin_v3_cop")  # ORIGIN_ORDER / _RANK / origin_max / is_human_named


class Inconclusive(Exception):
    """Raised for unparseable/missing-field input -- distinct from a rule violation."""


# --------------------------------------------------------------------- record-level check

def _rank(o):
    if o not in origin._RANK:
        raise Inconclusive("unrecognized origin value: %r" % (o,))
    return origin._RANK[o]


def _is_ryan_operator_file(event_id, origin_config_path=None):
    """The configured human-prefix operator-decision-file pattern (deploy/origin-config.yaml
    `human_prefixes:` -- `ryan` on this fork), via origin.py's own is_human_named() -- the
    SINGLE source of truth for this pattern, so this sensor and the F7 backfill rule always
    agree on what counts as an operator-decision file. (Function name kept as `_is_ryan_...`
    for this fork's fixtures/case-names below, which name the fork's own configured prefix;
    the check itself is prefix-agnostic and reads whatever deploy/origin-config.yaml says.)
    `origin_config_path` overrides the config file (self-test seam, threaded straight to
    is_human_named's own `path` seam) -- production callers never pass it."""
    return origin.is_human_named(event_id, path=origin_config_path)


def check_propagation(intake, registration, origin_config_path=None):
    """Validate one (intake record, registration record) pair against the F7 lattice.

    intake: {"packet_origin_max": <origin|None>}
    registration: {"event": str, "origin": <origin|None>, "receipt": bool (optional)}

    Returns None on PASS. Raises Inconclusive on unparseable input. Returns a violation
    string (never raises for a rule violation -- that is exit 1, a named finding, not an
    error) describing the record + both origins.

    `origin_config_path` is a self-test seam (which origin-config file names the
    human_prefixes for the fixture-c exception) -- production callers never pass it,
    so a live check always reads the live deploy/origin-config.yaml.
    """
    if not isinstance(intake, dict) or not isinstance(registration, dict):
        raise Inconclusive("intake/registration record is not a JSON object")

    if "packet_origin_max" not in intake:
        raise Inconclusive("intake record missing required field: packet_origin_max")
    packet_origin_max = intake["packet_origin_max"]
    if packet_origin_max is None:
        # Unknown intake forces the most conservative floor (F-3 default-deny direction):
        # an unrecorded/unknown packet_origin_max is treated as `unknown`, the most
        # restrictive value in the lattice -- never treated as "no constraint".
        packet_origin_max = "unknown"

    if "event" not in registration or not registration.get("event"):
        raise Inconclusive("registration record missing required field: event")
    event = registration["event"]

    if "origin" not in registration:
        raise Inconclusive("registration record missing required field: origin")
    registered_origin = registration["origin"]
    if registered_origin is None:
        raise Inconclusive("registration record has a null origin (unstamped)")

    has_receipt = bool(registration.get("receipt", False))

    floor_rank = _rank(packet_origin_max)
    reg_rank = _rank(registered_origin)

    # EXCEPTION: ryan-* operator-decision file WITH a receipt trail may mint human,
    # regardless of packet_origin_max (fixture c).
    if registered_origin == "human" and _is_ryan_operator_file(
            event, origin_config_path=origin_config_path):
        if has_receipt:
            return None  # PASS -- fixture (c)
        return (
            "VIOLATION: record %r registers origin=human via the ryan-* operator-decision "
            "pattern but carries NO receipt trail -- unproven human-mint (not the fixture-c "
            "exception); packet_origin_max=%r, registered=human"
            % (event, packet_origin_max)
        )

    # General rule: registered origin must be >= packet_origin_max (no trust upgrade).
    if reg_rank < floor_rank:
        return (
            "VIOLATION: record %r registers origin=%r which is LESS restrictive than the "
            "consumed packet_origin_max=%r (trust upgrade through the session loop; F9) -- "
            "ORIGIN_ORDER=%s"
            % (event, registered_origin, packet_origin_max, origin.ORIGIN_ORDER)
        )

    return None  # PASS


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise Inconclusive("could not read/parse %s: %s" % (path, exc))


def run_check(intake_path, registration_path, origin_config_path=None):
    """Returns (exit_code, message). `origin_config_path` is the self-test seam
    threaded to check_propagation -- the CLI entry (main) never passes it."""
    try:
        intake = _read_json(intake_path)
        registration = _read_json(registration_path)
        violation = check_propagation(intake, registration,
                                      origin_config_path=origin_config_path)
    except Inconclusive as exc:
        return 2, "INCONCLUSIVE: %s" % exc
    if violation:
        return 1, violation
    return 0, "clean: registration honors origin propagation (no trust upgrade)"


# --------------------------------------------------------------------- self-test fixtures

def self_test():
    import tempfile
    total = failed = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    # Origin-config self-sufficiency: every case that reaches the fixture-c
    # human-mint exception (registered origin == human) passes this suite's OWN
    # [ryan] fixture config via the origin_config_path seam -- the live deploy/
    # origin-config.yaml (which on a fresh instance may name a different
    # operator, or not exist) is never load-bearing for this suite's verdicts.
    _cfg_fd, _cfg = tempfile.mkstemp(prefix="cop-origin-config-", suffix=".yaml")
    with os.fdopen(_cfg_fd, "w", encoding="utf-8") as _fh:
        _fh.write("human_prefixes: [ryan]\n")

    # ---- fixtures (a)/(b)/(c) verbatim (test-plan tp:490-498) ----

    # (a) tainted intake -> registration stamps external-scrape (no upgrade) -- PASS
    intake_a = {"packet_origin_max": "external-scrape"}
    reg_a = {"event": "raw/2026-07-05-session-7-followup.md", "origin": "external-scrape"}
    case("(a) external-scrape intake -> external-scrape registration = PASS",
         check_propagation(intake_a, reg_a) is None)

    # (a-unknown) same, registering unknown (>= external-scrape) also PASS
    reg_a_unknown = {"event": "raw/2026-07-05-session-7-followup.md", "origin": "unknown"}
    case("(a) external-scrape intake -> unknown registration = PASS (more restrictive ok)",
         check_propagation(intake_a, reg_a_unknown) is None)

    # (b) same tainted intake, registration stamps human with NO operator-decision-file
    # backing -- FAIL, violation named
    reg_b = {"event": "raw/2026-07-05-session-7-followup.md", "origin": "human"}
    v_b = check_propagation(intake_a, reg_b, origin_config_path=_cfg)
    case("(b) external-scrape intake -> human registration (no backing) = VIOLATION",
         v_b is not None and "raw/2026-07-05-session-7-followup.md" in v_b
         and "external-scrape" in v_b)

    # (c) ryan-* operator-decision file WITH a receipt trail -- permitted to register human
    intake_c = {"packet_origin_max": "external-scrape"}
    reg_c = {"event": "raw/2026-07-05-ryan-decision-9-followup.md", "origin": "human",
             "receipt": True}
    case("(c) ryan-* file + receipt trail -> human registration = PASS",
         check_propagation(intake_c, reg_c, origin_config_path=_cfg) is None)

    # ---- edge cases ----

    # equal origins pass (boundary: reg_rank == floor_rank)
    intake_eq = {"packet_origin_max": "corpus"}
    reg_eq = {"event": "raw/2026-07-05-session-equal.md", "origin": "corpus"}
    case("equal origins (corpus == corpus) = PASS",
         check_propagation(intake_eq, reg_eq) is None)

    # unknown intake (None / missing-from-context) forces unknown/external-scrape floor;
    # registering corpus or human under it is a violation
    intake_unknown = {"packet_origin_max": None}
    reg_under_unknown_corpus = {"event": "raw/2026-07-05-session-x.md", "origin": "corpus"}
    v_unk = check_propagation(intake_unknown, reg_under_unknown_corpus)
    case("unknown intake (null) forces unknown floor -> corpus registration = VIOLATION",
         v_unk is not None and "unknown" in v_unk)

    reg_under_unknown_ext = {"event": "raw/2026-07-05-session-y.md", "origin": "external-scrape"}
    case("unknown intake (null) -> external-scrape registration = VIOLATION "
         "(external-scrape ranks less restrictive than unknown)",
         check_propagation(intake_unknown, reg_under_unknown_ext) is not None)

    reg_under_unknown_unknown = {"event": "raw/2026-07-05-session-z.md", "origin": "unknown"}
    case("unknown intake (null) -> unknown registration = PASS",
         check_propagation(intake_unknown, reg_under_unknown_unknown) is None)

    # missing receipt on a ryan-* file claiming human => violation (distinct from fixture c)
    reg_ryan_no_receipt = {"event": "raw/2026-07-05-ryan-decision-9-followup.md",
                            "origin": "human"}
    v_ryan = check_propagation(intake_c, reg_ryan_no_receipt, origin_config_path=_cfg)
    case("ryan-* file claiming human with NO receipt = VIOLATION (distinct from fixture c)",
         v_ryan is not None and "receipt" in v_ryan)

    # ryan-* file with receipt=False explicit
    reg_ryan_receipt_false = {"event": "raw/2026-07-05-ryan-decision-10.md",
                               "origin": "human", "receipt": False}
    case("ryan-* file with receipt explicitly False = VIOLATION",
         check_propagation(intake_c, reg_ryan_receipt_false,
                           origin_config_path=_cfg) is not None)

    # a non-ryan file registering human under a tainted floor is a plain violation
    # (not eligible for the exception at all, regardless of receipt)
    reg_nonryan_human_receipt = {"event": "raw/2026-07-05-session-summary.md",
                                  "origin": "human", "receipt": True}
    v_nonryan = check_propagation(intake_a, reg_nonryan_human_receipt,
                                  origin_config_path=_cfg)
    case("non-ryan file + receipt=true still VIOLATION (exception is ryan-*-scoped only)",
         v_nonryan is not None)

    # clean human intake -> human registration, PASS (no taint at all)
    intake_clean = {"packet_origin_max": "human"}
    reg_clean = {"event": "raw/2026-07-05-session-clean.md", "origin": "human"}
    case("human intake -> human registration = PASS (untainted loop)",
         check_propagation(intake_clean, reg_clean, origin_config_path=_cfg) is None)

    # human intake -> corpus registration also PASS (more restrictive than required floor)
    reg_over = {"event": "raw/2026-07-05-session-over.md", "origin": "corpus"}
    case("human intake -> corpus registration = PASS (more restrictive than floor is fine)",
         check_propagation(intake_clean, reg_over) is None)

    # ---- unparseable / malformed input -> INCONCLUSIVE (2), distinct from violation (1) ----

    case("unrecognized origin value raises Inconclusive",
         _raises_inconclusive(lambda: check_propagation(
             {"packet_origin_max": "external-scrape"},
             {"event": "raw/x.md", "origin": "bogus-origin-value"})))

    case("missing packet_origin_max key raises Inconclusive",
         _raises_inconclusive(lambda: check_propagation(
             {}, {"event": "raw/x.md", "origin": "human"})))

    case("missing event key raises Inconclusive",
         _raises_inconclusive(lambda: check_propagation(
             {"packet_origin_max": "human"}, {"origin": "human"})))

    case("missing origin key raises Inconclusive",
         _raises_inconclusive(lambda: check_propagation(
             {"packet_origin_max": "human"}, {"event": "raw/x.md"})))

    case("null origin (unstamped) raises Inconclusive",
         _raises_inconclusive(lambda: check_propagation(
             {"packet_origin_max": "human"}, {"event": "raw/x.md", "origin": None})))

    case("non-dict registration raises Inconclusive",
         _raises_inconclusive(lambda: check_propagation(
             {"packet_origin_max": "human"}, ["not", "a", "dict"])))

    # ---- end-to-end CLI-path fixtures via temp files (exercises run_check + _read_json) ----
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        p_intake_a = os.path.join(tmp, "intake_a.json")
        p_reg_a = os.path.join(tmp, "registration_a.json")
        with open(p_intake_a, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(intake_a, fh)
        with open(p_reg_a, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(reg_a, fh)
        code, msg = run_check(p_intake_a, p_reg_a)
        case("CLI-path (a) fixture files -> exit 0 clean", code == 0)

        p_reg_b = os.path.join(tmp, "registration_b.json")
        with open(p_reg_b, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(reg_b, fh)
        code, msg = run_check(p_intake_a, p_reg_b, origin_config_path=_cfg)
        case("CLI-path (b) fixture files -> exit 1 violation named", code == 1 and "human" in msg)

        p_intake_c = os.path.join(tmp, "intake_c.json")
        p_reg_c = os.path.join(tmp, "registration_c.json")
        with open(p_intake_c, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(intake_c, fh)
        with open(p_reg_c, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(reg_c, fh)
        code, msg = run_check(p_intake_c, p_reg_c, origin_config_path=_cfg)
        case("CLI-path (c) fixture files -> exit 0 clean", code == 0)

        # unparseable JSON file -> exit 2
        p_bad = os.path.join(tmp, "bad.json")
        with open(p_bad, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("{not valid json")
        code, msg = run_check(p_bad, p_reg_c)
        case("CLI-path unparseable intake JSON -> exit 2 inconclusive", code == 2)

        # missing file -> exit 2
        code, msg = run_check(os.path.join(tmp, "does-not-exist.json"), p_reg_c)
        case("CLI-path missing intake file -> exit 2 inconclusive", code == 2)

    try:
        os.remove(_cfg)
    except OSError:
        pass

    if failed:
        print("check-origin-propagation: FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("check-origin-propagation: PASS (%d/%d)" % (total, total))
    return 0


def _raises_inconclusive(fn):
    try:
        fn()
    except Inconclusive:
        return True
    except Exception:
        return False
    return False


# --------------------------------------------------------------------- CLI

def main(argv):
    args = argv[1:]

    if "--self-test" in args or not args:
        return self_test()

    if "--check" in args:
        try:
            intake_path = args[args.index("--intake") + 1]
            registration_path = args[args.index("--registration") + 1]
        except (ValueError, IndexError):
            sys.stderr.write(
                "usage: check-origin-propagation.py --check --intake PATH "
                "--registration PATH\n")
            return 2
        code, msg = run_check(intake_path, registration_path)
        print(msg)
        return code

    sys.stderr.write(
        "usage: check-origin-propagation.py --self-test\n"
        "       check-origin-propagation.py --check --intake PATH --registration PATH\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
