#!/usr/bin/env python3
"""registrations.py -- P5 typed-event registration record store (memory-engine v3, C1).

Spec sec.4: typing lives in an APPEND-ONLY REGISTRATION RECORD, one per ledger event,
schema-gated, seq-ordered, minted through the sec.6 substrate (compile-core.py's
append_record/check_chain) -- never in the event file itself (~17-19% of legacy events
have unrepairable frontmatter; raw/ is append-only, so the event file can never be
"fixed" to carry its own class).

THIS IS A SEPARATE CHAIN/NAMESPACE FROM THE RUN JOURNAL (receipts/journal/<seq>.json).
A registration is not a run: ACC-2/ACC-3's receipt-hole/no-op semantics stay scoped to
runs, never to this store. Records live at receipts/registrations/<seq>.json and are
chained independently (their own prev_record_hash sequence starting at 1), reusing
compile-core's append_record/check_chain machinery by IMPORT via the JournalConfig
parameterization below (compile-core.py itself is untouched -- its JOURNAL_DIR constant
is module-global, so a thin subclass-free wrapper swaps the directory per call instead).

Record schema (kind: "registration"), one per event (adjudication 4):
  seq                   int, engine-owned (allocated by append_record)
  event                 str, the event's repo-relative path (receipts/... or raw/...)
  origin                str, one of origin.py's ORIGIN_ORDER -- the COMPUTED value;
                        origin.py remains the computation, this field is its durable record
  origin_evidence       str, which origin.py rule/basis fired (assign_origin's `basis`)
  event_class           str, the registered class (e.g. "compile", "flight-plan",
                        "informed_by", "lock", or a judgment placeholder)
  event_class_origin    "explicit" | "judgment" -- explicit iff a recognized class key
                        was read from the event's own frontmatter; judgment otherwise
                        (F6: judgment -> lock-class treatment downstream, in the CONSUMER,
                        not encoded again here)
  asserts_corpus_state  bool -- true for every legacy receipt (adjudication 1a: a
                        receipt's content is a report about what a run did to the
                        corpus, pointer-class per spec sec.10); false for every raw/
                        event (a raw event, lock or not, is content-bearing -- its
                        claims ARE absorbed into views, adjudication 1b)
  registered_at         str, ISO timestamp of the mint
  prev_record_hash      str | None -- chained by append_record, None only for seq 1

Unknown top-level keys are refused at validate_registration (schema-gated, write-time
refusal discipline -- matches compile-core.validate_record's posture).

Append-only correction mechanic: load_registrations returns the EFFECTIVE map, keyed by
event id, where a LATER seq for the same event id supersedes an earlier one. Corrections
are never made by editing/removing a prior record -- a corrected registration is minted
as a new record (higher seq) for the same `event`; the reader's supersedes-by-later-seq
rule is how the correction becomes visible without ever mutating chain history.

Registration NEVER requires the event file to parse -- that is its entire sec.4 purpose:
the 3 known-hole receipts and any unparseable raw/ event still get a registration record
(origin "unknown" unless an operator attestation or B6 ryan-* rule resolves it), so a
registration's presence/absence is never gated on whether staleness.py could read the
event's own frontmatter.
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


_cc = _load("compile-core.py", "compile_core_registrations")
_origin = _load("origin.py", "origin_registrations")

REGISTRATIONS_DIR = os.path.join("receipts", "registrations")

# ------------------------------------------------------------------ receipts population
# B-2 (steady-state-ops brief, 2026-07-08/09): engine-written machine-sidecar
# directories directly under receipts/ -- these hold ENGINE output (the run-journal
# JSON at receipts/journal/, the registration-chain JSON at receipts/registrations/,
# per-leg verify packets at receipts/verify/, ...), never legacy-prose corpus
# receipts. Named/explicit -- same discipline as deploy/known-holes.yaml -- NEVER a
# wildcard/pattern match; extend by adding a name when a new engine sidecar
# directory is introduced.
#
# SINGLE SOURCE OF TRUTH: staleness.py's enlarged-ledger receipts enumeration
# (load_ledger's P5 enlarged path, via _iter_receipts_only) and check-loop-state.py's
# P5 extension check (c) (the receipts-population count) both need the IDENTICAL
# receipts population. Before this fix the two sensors defined it INDEPENDENTLY
# (staleness.py: a recursive os.walk with its own 2-item exclusion tuple;
# check-loop-state.py: a non-recursive os.listdir) and had silently drifted apart --
# staleness.py's walk swept receipts/verify/packets/*.md (written by every live
# compile verify leg) into the enlarged ledger with no registration ->
# EnlargementViolation crashing the census on the engine's OWN output.
#
# registrations.py is the module BOTH sensors already import (lazily, sibling-import
# by path -- staleness.py only on its enlarged-mode path, check-loop-state.py only
# inside its P5 extension checks) for the registration-store lookups, so it is the
# natural LOWER-LEVEL home for this shared constant + the shared enumeration helper
# below: neither sensor has to import the OTHER (a peer-to-peer import staleness.py
# and check-loop-state.py never otherwise have), and there is no circular-import
# risk (registrations.py itself imports only compile-core.py and origin.py, neither
# of which imports staleness.py or check-loop-state.py).
#
# "candidates" (R-1, spec r1-build-decisions-2026-07-22.md Part 2.3): the candidate
# ledger writes receipts/candidates/<seq>.json, the same JSON-chain-under-receipts/
# shape as journal/ and registrations/ -- list_receipts_population's .md-only filter
# would never have swept those files into the population anyway, so naming the
# directory here changes no behavior today. It is named regardless, for the same
# explicit-over-incidental reason this constant is a fixed tuple rather than a
# pattern match in the first place: a future sidecar file type under receipts/
# candidates/ must not have to wait for someone to notice the gap.
ENGINE_SIDECAR_DIRS = ("journal", "registrations", "verify", "candidates")


def list_receipts_population(root):
    """The receipts/ conservation population: every receipts/*.md file, recursively,
    EXCLUDING any file that lives under a top-level ENGINE_SIDECAR_DIRS subdirectory
    (receipts/journal/, receipts/registrations/, receipts/verify/, ...). Returns a
    sorted list of repo-relative paths (forward slashes). Missing receipts/ ->
    empty list (never raises).

    THIS is the single function both staleness.py's enlarged-ledger receipts
    enumeration and check-loop-state.py's extension check (c) call, so their
    receipts populations are PROVABLY identical -- not just coincidentally
    identical because today's corpus happens to be flat -- and can no longer
    silently drift apart the way they did before this fix (B-2)."""
    rroot = os.path.join(root, "receipts")
    if not os.path.isdir(rroot):
        return []
    out = []
    for r, _dirs, files in os.walk(rroot):
        if os.sep + ".git" in r:
            continue
        rel_dir = os.path.relpath(r, root).replace("\\", "/")
        parts = rel_dir.split("/")
        # parts[0] == "receipts"; parts[1], when present, is the first subdirectory
        # under receipts/ -- an ENGINE_SIDECAR_DIRS member there (and everything
        # nested beneath it) is excluded from the population.
        if len(parts) >= 2 and parts[1] in ENGINE_SIDECAR_DIRS:
            continue
        for f in files:
            if f.endswith(".md"):
                out.append(os.path.relpath(os.path.join(r, f), root).replace("\\", "/"))
    return sorted(out)


REGISTRATION_REQUIRED = {
    "kind": str,
    "seq": int,
    "event": str,
    "origin": str,
    "origin_evidence": str,
    "event_class": str,
    "event_class_origin": str,
    "asserts_corpus_state": bool,
    "registered_at": str,
    "prev_record_hash": (str, type(None)),
}
_ALLOWED_KEYS = set(REGISTRATION_REQUIRED)
_ALLOWED_EVENT_CLASS_ORIGIN = {"explicit", "judgment"}


class RegistrationViolation(Exception):
    pass


def _origin_order():
    return _origin.ORIGIN_ORDER


def validate_registration(record):
    """Schema gate at WRITE time (mirrors compile_core.validate_record's posture).
    Refuses: missing/mistyped required fields, unknown keys, an origin outside
    origin.py's ORIGIN_ORDER, an event_class_origin outside {explicit, judgment},
    kind != "registration"."""
    if not isinstance(record, dict):
        raise RegistrationViolation("record is not an object")
    extra = set(record) - _ALLOWED_KEYS
    if extra:
        raise RegistrationViolation("unknown key(s): %s" % sorted(extra))
    for key, typ in REGISTRATION_REQUIRED.items():
        if key not in record:
            raise RegistrationViolation("missing field: %s" % key)
        if not isinstance(record[key], typ):
            raise RegistrationViolation(
                "field %s has wrong type %s" % (key, type(record[key]).__name__))
    if record["kind"] != "registration":
        raise RegistrationViolation("kind must be 'registration', got %r" % record["kind"])
    if record["origin"] not in _origin_order():
        raise RegistrationViolation("origin %r not in ORIGIN_ORDER" % record["origin"])
    if record["event_class_origin"] not in _ALLOWED_EVENT_CLASS_ORIGIN:
        raise RegistrationViolation(
            "event_class_origin must be explicit|judgment, got %r"
            % record["event_class_origin"])


# ------------------------------------------------------------------ journal-dir
# parameterization (compile-core is untouched; JOURNAL_DIR there is a module
# constant, so the registration chain is minted by pointing compile_core's
# directory helpers at receipts/registrations/ for the duration of the call).
class _RegistrationJournal(object):
    """Wraps compile_core's append_record/check_chain against REGISTRATIONS_DIR
    instead of compile_core.JOURNAL_DIR, without editing compile_core.py. Not
    thread-safe across concurrent DIFFERENT directories in one process (fine:
    compile-core's own _SeqLock already serializes at the git-common-dir level,
    and this repo's build never runs registration + run-journal mints in the
    same process concurrently)."""

    def __init__(self, repo_root):
        self.repo_root = repo_root

    def __enter__(self):
        self._orig = _cc.JOURNAL_DIR
        _cc.JOURNAL_DIR = REGISTRATIONS_DIR
        return self

    def __exit__(self, *exc):
        _cc.JOURNAL_DIR = self._orig
        return False


def append_registration(repo_root, record):
    """Mint one registration record through compile_core.append_record, chained
    under receipts/registrations/. `record` supplies every field except seq and
    prev_record_hash (engine-owned, exactly compile_core's append_record contract).
    Validates the FULL schema before handing to compile_core (compile_core's own
    validate_record only checks the run-journal schema, which this record does
    NOT match -- so this module's schema gate runs first)."""
    with _RegistrationJournal(repo_root):
        # compile_core.append_record calls its own validate_record(rec) internally,
        # which expects RUN-JOURNAL fields (absorbed/noop_candidates/...) -- a
        # registration record does not have them. So the record is smuggled through
        # by MONKEY-PATCHING compile_core.validate_record to this module's gate for
        # the duration of the call, restored immediately after (compile-core.py
        # itself is untouched on disk; only the imported module object's attribute
        # is swapped in-process).
        _orig_validate = _cc.validate_record
        _cc.validate_record = validate_registration
        try:
            return _cc.append_record(repo_root, record)
        finally:
            _cc.validate_record = _orig_validate


def check_registration_chain(repo_root):
    """check_chain over receipts/registrations/ -- same gap/dup/hash-mismatch
    integrity compile_core.check_chain gives the run journal. Swaps validate_record
    the same way append_registration does, so a mismatched-schema record among the
    registrations fails the SAME loud way a run-journal mismatch would."""
    with _RegistrationJournal(repo_root):
        _orig_validate = _cc.validate_record
        _cc.validate_record = validate_registration
        try:
            return _cc.check_chain(repo_root)
        finally:
            _cc.validate_record = _orig_validate


def registrations_dir(repo_root):
    return os.path.join(repo_root, REGISTRATIONS_DIR)


def load_registrations(root):
    """Read receipts/registrations/<seq>.json in seq order, running the FULL chain
    check first (gap/dup/prev_record_hash mismatch -> loud RegistrationViolation,
    never silent). Returns {event_rel: record} -- the EFFECTIVE map: a later seq for
    the same `event` supersedes an earlier one (the append-only correction
    mechanic; see module docstring). event_rel uses forward slashes."""
    n = check_registration_chain(root)
    effective = {}
    jd = registrations_dir(root)
    for seq in range(1, n + 1):
        path = os.path.join(jd, "%d.json" % seq)
        with open(path, "r", encoding="utf-8") as fh:
            rec = json.load(fh)
        event = str(rec["event"]).replace("\\", "/")
        effective[event] = rec  # later seq overwrites -- supersedes-by-later-seq
    return effective


def _minimal(event, origin_val="corpus", event_class="compile",
             event_class_origin="explicit", asserts_corpus_state=True,
             registered_at="2026-07-06T00:00:00"):
    return {
        "kind": "registration", "event": event, "origin": origin_val,
        "origin_evidence": "test fixture", "event_class": event_class,
        "event_class_origin": event_class_origin,
        "asserts_corpus_state": asserts_corpus_state,
        "registered_at": registered_at,
    }


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

    # --- validate_registration: schema gate ---
    try:
        validate_registration({"kind": "registration"})
        case("missing required fields refused", False)
    except RegistrationViolation:
        case("missing required fields refused", True)

    ok_rec = dict(_minimal("raw/x.md"), seq=1, prev_record_hash=None)
    try:
        validate_registration(ok_rec)
        case("well-formed record validates", True)
    except RegistrationViolation:
        case("well-formed record validates", False)

    bad_key = dict(ok_rec, extra_field="nope")
    try:
        validate_registration(bad_key)
        case("unknown key refused", False)
    except RegistrationViolation as e:
        case("unknown key refused", "extra_field" in str(e))

    bad_origin = dict(ok_rec, origin="bogus")
    try:
        validate_registration(bad_origin)
        case("bad origin (outside ORIGIN_ORDER) refused", False)
    except RegistrationViolation as e:
        case("bad origin (outside ORIGIN_ORDER) refused", "ORIGIN_ORDER" in str(e))

    bad_eco = dict(ok_rec, event_class_origin="guess")
    try:
        validate_registration(bad_eco)
        case("bad event_class_origin refused", False)
    except RegistrationViolation:
        case("bad event_class_origin refused", True)

    bad_kind = dict(ok_rec, kind="not-a-registration")
    try:
        validate_registration(bad_kind)
        case("kind != registration refused", False)
    except RegistrationViolation:
        case("kind != registration refused", True)

    # --- append/chain over a tempdir git repo ---
    base = tempfile.mkdtemp(prefix="regs-")
    try:
        subprocess.run(["git", "-C", base, "init", "-q"], capture_output=True)
        subprocess.run(["git", "-C", base, "config", "user.email", "t@t"],
                       capture_output=True)
        subprocess.run(["git", "-C", base, "config", "user.name", "t"],
                       capture_output=True)

        s1, p1 = append_registration(base, _minimal("receipts/a.md"))
        s2, p2 = append_registration(base, _minimal("raw/b.md", origin_val="human",
                                                     event_class="informed_by",
                                                     asserts_corpus_state=False))
        case("registration chain lives at receipts/registrations/, seq 1,2",
             (s1, s2) == (1, 2)
             and os.path.isfile(os.path.join(base, "receipts", "registrations", "1.json"))
             and not os.path.isdir(os.path.join(base, "receipts", "journal")))
        case("registration chain check green (2 records)",
             check_registration_chain(base) == 2)

        regs = load_registrations(base)
        case("load_registrations returns both events",
             set(regs) == {"receipts/a.md", "raw/b.md"})
        case("raw/b.md registered informed_by/human/asserts_corpus_state=False",
             regs["raw/b.md"]["event_class"] == "informed_by"
             and regs["raw/b.md"]["origin"] == "human"
             and regs["raw/b.md"]["asserts_corpus_state"] is False)

        # supersedes-by-later-seq: a correction for receipts/a.md at seq 3
        s3, _p3 = append_registration(base, _minimal("receipts/a.md", event_class="audit"))
        regs2 = load_registrations(base)
        case("later seq for the same event supersedes the earlier one",
             s3 == 3 and regs2["receipts/a.md"]["event_class"] == "audit"
             and regs2["receipts/a.md"]["seq"] == 3)

        # tamper the chain: corrupt seq 2's prev_record_hash -> loud failure
        p2_path = os.path.join(base, "receipts", "registrations", "2.json")
        rec2 = json.load(open(p2_path, encoding="utf-8"))
        rec2["prev_record_hash"] = "0" * 64
        open(p2_path, "w", encoding="utf-8").write(json.dumps(rec2))
        try:
            check_registration_chain(base)
            case("chain tamper (hash mismatch) fails loud", False)
        except _cc.JournalViolation as e:
            case("chain tamper (hash mismatch) fails loud", "mismatch" in str(e))
        try:
            load_registrations(base)
            case("load_registrations refuses over a tampered chain", False)
        except _cc.JournalViolation:
            case("load_registrations refuses over a tampered chain", True)

        # run-journal machinery (compile_core.JOURNAL_DIR) is restored after use
        case("compile_core.JOURNAL_DIR restored to receipts/journal after registration calls",
             _cc.JOURNAL_DIR == os.path.join("receipts", "journal"))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    # --- list_receipts_population / ENGINE_SIDECAR_DIRS (B-2, 2026-07-09): the
    # single shared receipts-population source of truth for staleness.py's
    # enlarged-ledger enumeration and check-loop-state.py's extension check (c). ---
    base4 = tempfile.mkdtemp(prefix="regs-receipts-pop-")
    try:
        def _w(rel, text):
            p = os.path.join(base4, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(text)

        case("list_receipts_population over a missing receipts/ dir returns []",
             list_receipts_population(base4) == [])

        _w("receipts/a.md", "a real receipt\n")
        _w("receipts/journal/1.json", "{}")
        _w("receipts/registrations/1.json", "{}")
        _w("receipts/verify/packets/plant.md", "an engine verify-leg sidecar\n")
        pop = list_receipts_population(base4)
        case("list_receipts_population includes a real top-level receipt",
             "receipts/a.md" in pop)
        case("list_receipts_population EXCLUDES receipts/verify/... "
             "(engine sidecar, B-2)", "receipts/verify/packets/plant.md" not in pop)
        case("list_receipts_population is exactly the real receipt (journal/"
             "registrations sidecars carry no .md files anyway)", pop == ["receipts/a.md"])
        case("ENGINE_SIDECAR_DIRS names journal, registrations, verify, candidates "
             "(named, never wildcarded)",
             set(ENGINE_SIDECAR_DIRS)
             == {"journal", "registrations", "verify", "candidates"})
    finally:
        shutil.rmtree(base4, ignore_errors=True)

    if failed:
        print("registrations self-test: FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("registrations self-test: PASS (%d/%d)" % (total, total))
    return 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    print("usage: registrations.py --self-test  (library module, imported by "
          "backfill-registrations.py)")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
