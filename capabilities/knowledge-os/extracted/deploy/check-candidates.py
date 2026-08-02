#!/usr/bin/env python3
"""check-candidates.py -- R-1 session-candidate conservation sensor (leg E).

Spec: harness-v3.0/specs/r1-build-decisions-2026-07-22.md Part 7, verbatim. Parts 3-5
(the candidate file shape, the append-only ledger at receipts/candidates/, harvest +
register) are the data this sensor reads; deploy/candidates.py is the sole library this
module imports. Sibling legs (harvest-candidates.py, register-candidates.py,
promote-candidate.py) are NOT imported here and this module never waits on them.

WHAT THIS IS: a census sensor, the same family as deploy/staleness.py (the knowledge-base
conservation census) and the structural checkers in .claude/skills/sweep/SKILL.md. It is
read-only -- it never writes a candidate file, never appends a ledger record, never
touches git. Wired into /sweep immediately after staleness.py's conservation census (same
category of check by the skill's own stated taxonomy: structural sensors before
behavioral ones).

WHY THE COUNT ALONE PROVES NOTHING (the whole reason this sensor exists, not a stylistic
choice -- build-decisions Part 8 criterion 4 and its "Sol R1" framing): if N candidates
are staged on disk and M chain entries claim to be non-terminal, a bare "N != M" tells an
operator that SOMETHING is wrong without telling them WHICH candidate is missing, whether
it was ever promoted, or whether it is safe to re-harvest. The ledger's `event` field
already names the exact staged path every open candidate is supposed to have -- so a
vanished candidate is reported BY ITS CHAIN ENTRY (candidate_id, seq, state, expected
path), never folded into a count drift. Same discipline for the reverse direction: an
orphan staged file (on disk, unclaimed by any chain entry) is named individually too.

THE FOUR CHECKS, IN ORDER (spec Part 7):
  1. Chain integrity, via candidates.load_candidate_ledger (which runs
     candidates.check_candidate_chain first, and refuses to build the effective map over
     a broken chain) -- a gap, a duplicate seq, a prev_record_hash mismatch, or a record
     that fails the candidate schema all surface LOUD here, named, and stop the sensor:
     nothing downstream can be trusted about a chain that does not verify.
  2. Every staged file has a chain entry -- an orphan is named individually.
  3. Every chain entry whose EFFECTIVE (highest-seq) state is non-terminal has its staged
     file present on disk -- a vanished candidate is named by chain entry, never as a bare
     count.
  4. A census by state (counts per state, plus totals) -- printed always, on the clean
     path, so an operator always sees the shape of what is being tracked, not just silence
     on success.

FAIL-SAFE SKIP (spec Part 7 + the module-level SKIP-with-note discipline every other
sensor in this family uses on an absent capability): if NEITHER receipts/candidates/ NOR
intake/session-candidates/ exists, this instance has never harvested a session candidate.
That is the normal state of a fresh template instance -- and of this fork until the first
harvest -- and must read as healthy, not broken: print a clear note and exit 0. The
moment EITHER directory exists, though, every check runs in full: a candidate ledger with
no staging directory behind it (or a staging directory with no ledger at all) is itself a
finding, not a second flavour of "nothing to see."

PYYAML: this sensor's four checks, as specified, never parse a candidate file's YAML
frontmatter -- conservation here is decided entirely from the ledger's own JSON records
(receipts/candidates/<seq>.json, no PyYAML involved) plus plain filesystem presence
checks. The exit-2-on-PyYAML-unavailable contract is still honoured (spec Part 7: "Exit 0
green / 1 violation / 2 inconclusive (PyYAML unavailable)"), both for parity with every
sibling sensor's CLI vocabulary and because a future extension of this sensor that
validates a staged file's own frontmatter (not just its presence) would need it. That gate
sits AFTER the fail-safe SKIP decision, never before it -- an instance that has simply
never harvested must SKIP cleanly with exit 0 regardless of whether PyYAML happens to be
installed; a missing capability is not the same condition as a missing dependency.

Output is plain, self-contained English -- an operator who has never read this file's
code must be able to understand every line: what was checked, what was found, and what a
finding means for them. No .md pointers, no jargon, no bare file paths standing in for an
explanation.

Usage:
  check-candidates.py --root DIR     run the census sensor over DIR (default: cwd)
  check-candidates.py --self-test    run embedded hermetic fixtures; exit 0 if all pass

Exit codes: 0 = green (or a clean SKIP) | 1 = a conservation violation, a broken chain, or
  a self-test failure | 2 = inconclusive (PyYAML unavailable).
"""

import importlib.util
import json
import os
import sys

try:
    import yaml
except ImportError:  # pragma: no cover -- optional-yaml idiom, matches every sibling
    yaml = None       # sensor in this family (staleness.py, candidates.py, origin.py).

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(basename, alias):
    """Sibling-import by path -- the same mechanism candidates.py/registrations.py use
    for their own sibling imports (deploy/ is a flat directory, never a package)."""
    spec = importlib.util.spec_from_file_location(alias, os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rel_to_local(rel_path):
    """A ledger record's `event` field is always forward-slash repo-relative (candidates.
    new_record normalises it). Converting it back to the local OS separator here is the
    ONE place this module does that conversion, so a filesystem existence check is
    correct on both Windows and POSIX without scattering os.sep logic through the checks
    below."""
    return os.path.join(*rel_path.split("/")) if rel_path else rel_path


def run(root):
    """Runs the four checks over `root`. Returns (lines, exit_code) -- `lines` is the
    complete plain-English report (the caller just prints them in order); this function
    never prints directly, so --self-test can capture and assert on the report text."""
    lines = []
    cand = _load("candidates.py", "candidates_check_candidates")

    staging_dir_abs = os.path.join(root, cand.CANDIDATES_STAGING_DIR)
    ledger_dir_abs = cand.candidates_ledger_dir(root)
    staging_present = os.path.isdir(staging_dir_abs)
    ledger_present = os.path.isdir(ledger_dir_abs)

    # ---- fail-safe SKIP: neither surface exists -- never harvested, never a problem.
    if not staging_present and not ledger_present:
        lines.append(
            "SKIP: this instance has never harvested a session candidate -- neither the "
            "candidate staging folder (intake/session-candidates/) nor the candidate "
            "ledger (receipts/candidates/) exists yet. That is the normal, healthy state "
            "for an instance that has not run the session-candidate harvester, not a "
            "problem. Nothing was checked."
        )
        return lines, 0

    # ---- PyYAML gate (see module docstring: after the SKIP decision, never before it).
    if yaml is None:
        lines.append(
            "INCONCLUSIVE: PyYAML is not installed, so this check could not run. Install "
            "PyYAML (pip install pyyaml) and try again. This instance DOES have session-"
            "candidate activity on disk (a staging folder or a candidate ledger, or "
            "both), so this is not the normal empty-instance case -- it genuinely could "
            "not be checked this time."
        )
        return lines, 2

    # ---- check 1: chain integrity + the effective (highest-seq-wins) state per candidate.
    try:
        effective = cand.load_candidate_ledger(root)
    except Exception as e:  # noqa: BLE001 -- deliberately broad: both compile-core's
        # JournalViolation (gap/duplicate/hash-mismatch) and candidates.CandidateViolation
        # (a record that no longer matches the candidate schema) must be caught here and
        # reported by name, never let through as an uncaught traceback and never silently
        # swallowed. Nothing past a broken chain can be trusted, so the sensor stops here.
        lines.append(
            "VIOLATION: the session-candidate ledger at receipts/candidates/ failed its "
            "integrity check (%s: %s). This means the chain of candidate records has a "
            "gap, a duplicate entry, a broken link between one record and the next, or a "
            "record whose content no longer matches the schema it was written under. "
            "Nothing else about this instance's candidates can be trusted until the chain "
            "is repaired, so no further checks ran." % (type(e).__name__, e)
        )
        return lines, 1

    staged = cand.staged_files(root)  # sorted repo-relative, forward-slash, missing dir -> []
    claimed_paths = {rec["event"] for rec in effective.values()}

    violations = []

    # ---- check 2: every staged file has a chain entry (an orphan is named individually).
    for path in staged:
        if path not in claimed_paths:
            violations.append(
                "ORPHAN STAGED FILE: %s sits in the candidate staging folder, but no "
                "entry in the candidate ledger ever claimed it. This usually means a "
                "harvest wrote the file but crashed before appending its ledger record, "
                "or that a file was placed there by hand. Running the reconciler "
                "(register-candidates.py) will pick up a genuine crashed harvest; if the "
                "file does not belong there, remove it." % path
            )

    # ---- check 3: every non-terminal chain entry has its staged file present, named by
    # the chain entry itself (candidate_id, seq, state, expected path) -- never a count.
    for candidate_id in sorted(effective):
        rec = effective[candidate_id]
        if cand.is_terminal(rec["state"]):
            continue  # terminal means accounted for (e.g. a promoted candidate's staged
            # file may legitimately be gone) -- absence here is expected, not a violation.
        expected_path = rec["event"]
        full_path = os.path.join(root, _rel_to_local(expected_path))
        if not os.path.isfile(full_path):
            violations.append(
                "VANISHED CANDIDATE: candidate %s (ledger entry #%s, currently in state "
                "'%s') should have its file at %s, but that file is not there. This "
                "candidate is still open -- it has not been promoted, expired, "
                "discarded, or quarantined -- so its staged file is required to exist. "
                "Something removed it outside the normal candidate lifecycle."
                % (candidate_id, rec.get("seq", "?"), rec["state"], expected_path)
            )

    # ---- check 4: census by state, always printed (the shape of what is being tracked,
    # not just silence on success).
    counts = {}
    for rec in effective.values():
        counts[rec["state"]] = counts.get(rec["state"], 0) + 1
    if counts:
        breakdown = ", ".join(
            "%d %s" % (counts[s], s) for s in cand.STATES if s in counts
        )
    else:
        breakdown = "none"
    lines.append(
        "Census: %d candidate(s) tracked in the ledger (%s). %d file(s) currently staged "
        "on disk." % (len(effective), breakdown, len(staged))
    )

    if violations:
        lines.append(
            "%d conservation violation(s) found -- each names the specific candidate or "
            "file involved:" % len(violations)
        )
        for v in violations:
            lines.append("  - " + v)
        return lines, 1

    lines.append(
        "PASS: every staged candidate file has a matching ledger entry, every open "
        "candidate's file is present on disk, and the ledger's chain is intact."
    )
    return lines, 0


# ------------------------------------------------------------------ self-test
def self_test():
    import shutil
    import subprocess
    import tempfile

    cand = _load("candidates.py", "candidates_check_candidates_selftest")

    total = failed = 0

    def case(name, ok, detail=""):
        nonlocal total, failed
        total += 1
        print("  %s %s%s" % ("ok " if ok else "XX ", name,
                             ("  << " + detail) if (not ok and detail) else ""))
        if not ok:
            failed += 1

    def git_init(root):
        subprocess.run(["git", "-C", root, "init", "-q"], capture_output=True)
        subprocess.run(["git", "-C", root, "config", "user.email", "t@t"],
                       capture_output=True)
        subprocess.run(["git", "-C", root, "config", "user.name", "t"],
                       capture_output=True)

    def stage(root, session_id, span, **kw):
        meta = cand._fixture_meta(session_id=session_id, span=span, **kw)
        path = cand.write_candidate_atomic(root, meta, span)
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        return meta, rel

    def register(root, meta, rel, state, note="self-test fixture"):
        rec = cand.new_record(meta, state, note, rel)
        seq, _p = cand.append_candidate_record(root, rec)
        return seq

    tmp_dirs = []

    def mkroot(prefix):
        d = tempfile.mkdtemp(prefix=prefix)
        tmp_dirs.append(d)
        return d

    try:
        # ---- (1) clean, fully-reconciled ledger is green -----------------------------
        root = mkroot("checkcand-clean-")
        git_init(root)
        m1, r1 = stage(root, "sessA1234", "first span for the clean case")
        register(root, m1, r1, "staged")
        m2, r2 = stage(root, "sessB5678", "second span for the clean case")
        register(root, m2, r2, "staged")
        register(root, m2, r2, "registered")
        lines, code = run(root)
        joined = "\n".join(lines)
        case("(1) clean fully-reconciled ledger: exit 0", code == 0)
        case("(1) clean fully-reconciled ledger: PASS reported",
             "PASS" in joined, joined)
        case("(1) clean fully-reconciled ledger: census line present",
             "Census:" in joined, joined)

        # ---- (2) acceptance criterion 4: staged file deleted out-of-band -------------
        # -- reported, and named BY THE CHAIN ENTRY (candidate_id appears), never as a
        # bare count.
        root = mkroot("checkcand-vanished-")
        git_init(root)
        m3, r3 = stage(root, "sessVanis", "a span whose staged file will vanish")
        register(root, m3, r3, "staged")
        register(root, m3, r3, "registered")
        os.remove(os.path.join(root, *r3.split("/")))
        lines, code = run(root)
        joined = "\n".join(lines)
        case("(2) vanished candidate: exit 1", code == 1)
        case("(2) vanished candidate: names the candidate_id, not just a count",
             m3["candidate_id"] in joined, joined)
        case("(2) vanished candidate: says VANISHED CANDIDATE",
             "VANISHED CANDIDATE" in joined, joined)
        case("(2) vanished candidate: names the expected path",
             r3 in joined, joined)

        # ---- (3) orphan staged file with no chain entry -------------------------------
        root = mkroot("checkcand-orphan-")
        git_init(root)
        m4, r4 = stage(root, "sessOrphn", "an orphan span never registered at all")
        lines, code = run(root)
        joined = "\n".join(lines)
        case("(3) orphan staged file: exit 1", code == 1)
        case("(3) orphan staged file: names the file's path",
             r4 in joined, joined)
        case("(3) orphan staged file: says ORPHAN STAGED FILE",
             "ORPHAN STAGED FILE" in joined, joined)

        # ---- (4) a TERMINAL-state candidate whose file is absent is NOT a violation ---
        # (terminal means accounted for -- e.g. after promotion the staged file may
        # legitimately be gone).
        root = mkroot("checkcand-terminal-")
        git_init(root)
        m5, r5 = stage(root, "sessTermA", "a span that gets promoted then cleaned up")
        register(root, m5, r5, "staged")
        register(root, m5, r5, "registered")
        register(root, m5, r5, "promoted")
        os.remove(os.path.join(root, *r5.split("/")))
        lines, code = run(root)
        joined = "\n".join(lines)
        case("(4) terminal-state candidate, file absent: exit 0 (not a violation)",
             code == 0, joined)
        case("(4) terminal-state candidate, file absent: not reported as vanished",
             m5["candidate_id"] not in joined or "VANISHED" not in joined, joined)
        case("(4) terminal-state candidate, file absent: PASS reported",
             "PASS" in joined, joined)

        # ---- (5) a tampered chain fails loud ------------------------------------------
        root = mkroot("checkcand-tamper-")
        git_init(root)
        m6, r6 = stage(root, "sessTampr", "first span, chain will be tampered")
        register(root, m6, r6, "staged")
        m7, r7 = stage(root, "sessTampr2", "second span, chains onto the tampered one")
        register(root, m7, r7, "staged")
        seq1_path = os.path.join(cand.candidates_ledger_dir(root), "1.json")
        with open(seq1_path, "r", encoding="utf-8") as fh:
            rec1 = json.load(fh)
        rec1["note"] = "tampered after the fact -- breaks seq 2's prev_record_hash link"
        with open(seq1_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rec1))
        lines, code = run(root)
        joined = "\n".join(lines)
        case("(5) tampered chain: exit 1", code == 1)
        case("(5) tampered chain: reported as a VIOLATION, not silent",
             "VIOLATION" in joined, joined)

        # ---- (6) both directories absent: SKIP cleanly with exit 0 --------------------
        root = mkroot("checkcand-skip-")
        lines, code = run(root)
        joined = "\n".join(lines)
        case("(6) both directories absent: exit 0", code == 0)
        case("(6) both directories absent: reported as SKIP", "SKIP" in joined, joined)

        # ---- (7) chain present but the staging directory absent: a finding, not a skip
        root = mkroot("checkcand-nostagedir-")
        git_init(root)
        m8, r8 = stage(root, "sessNoDir1", "a span whose whole staging dir disappears")
        register(root, m8, r8, "staged")
        shutil.rmtree(os.path.join(root, cand.CANDIDATES_STAGING_DIR))
        lines, code = run(root)
        joined = "\n".join(lines)
        case("(7) chain present, staging dir entirely absent: not treated as a SKIP",
             "SKIP" not in joined, joined)
        case("(7) chain present, staging dir entirely absent: reported as a finding",
             code == 1, joined)
        case("(7) chain present, staging dir entirely absent: names the candidate",
             m8["candidate_id"] in joined, joined)

    finally:
        for d in tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)

    if failed:
        print("check-candidates self-test: FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("check-candidates self-test: PASS (%d/%d)" % (total, total))
    return 0


def main(argv):
    args = argv[1:]
    if "--self-test" in args:
        return self_test()
    root = os.getcwd()
    if "--root" in args:
        i = args.index("--root")
        if i + 1 < len(args):
            root = args[i + 1]
    lines, code = run(root)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
