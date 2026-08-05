#!/usr/bin/env python3
"""check-loop-state.py -- loop-envelope meta.yaml validator + P5 typed-event
extension (memory-engine v3, component C3).

PROVENANCE (adjudication 5, memory-engine-v3-p5-typed-events-design-2026-07-06.md):
check-loop-state.py is a PHANTOM on this fork -- the test plan cites it as an
existing sensor (tp:11, tp:584) but it was never carried onto the dogfood fork.
It exists only in the live-instance import snapshot at
`_import/<live-wiki>/deploy/check-loop-state.py`. THIS FILE IS A PORT of that
snapshot (ported 2026-07-06, snapshot content read in full at port time),
adapted to the fork's actual layout, plus the three P5 extension checks
(design component C3). The test-plan wording defect ("extend an existing
sensor" the template never shipped) is logged to the harness backlog
separately, per adjudication 5 -- not silently absorbed here.

WHAT WAS PORTED VERBATIM IN SPIRIT
  - raw_informed_by(): the frontmatter informed_by extractor, INCLUDING its
    line-level degraded-parse fallback for YAML-hostile frontmatter blocks.
    This is the exact function the P5 extension reuses. ONE FORK DIVERGENCE
    (orchestrator adjudication 2026-07-06): the fallback's block-list dash
    bug is FIXED here -- the snapshot's fallback regex mis-extracted the
    block-list form ("informed_by:" followed by "  - value" lines) under
    whole-block parse failure, capturing "- value" with the leading dash
    and producing FALSE back-link violations (two real fork raws hit it).
    See the inline comment at the fallback regex. The bug exists upstream
    in the live instance and is being handed back separately.
  - normalize_handoff_ref(), folder_date(), load_yaml_file(): unchanged.
  - check_handoff(): the handoffs/<folder>/meta.yaml validator -- status
    vocabulary, INDEX.md Active/Archive cross-check, field-schema checks
    (current protocol only), round/file coherence, locked-status checks
    (raw back-link resolution via raw_informed_by, confidence-audit.md
    presence). Kept because the fork's handoffs/ tree (39 folders) is the
    live structural analogue of the snapshot's handoffs/.
  - The named-allowlist discipline: LEGACY_LENIENT_FOLDERS (pre-protocol
    immutable folders exempted from field-schema drift, never from status/
    INDEX checks) and BODYLINK_BACKLINK_EXEMPT (session-sourced lock raws
    whose back-link lives in prose body, not frontmatter -- append-only raw
    means this is structurally unfixable, so the exemption suppresses only
    the one specific violation it names). PORT HISTORY: both sets were
    first ported EMPTY (conservative), then the snapshot's two named
    entries (`2026-05-27-5b-products-inventory-schema`,
    `2026-06-21-gateway-sync-runtime-model`) were RESTORED per orchestrator
    adjudication 2026-07-06 -- the folders were imported onto this fork
    byte-identically from the live instance, so the live instance's
    recorded dispositions carry over; re-firing settled named one-offs is
    noise, not vigilance. A third, FORK-NEW named set (HARNESS_ERA_FOLDERS)
    was added by the same adjudication for two template-repo-era governance
    handoffs imported as immutable evidence artifacts. Never wildcarded.
  - Exit-code / self-test / CLI conventions: 0 clean / 1 violation(s) /
    2 inconclusive, --self-test over deploy/test-fixtures/loop-state/,
    --check for the live run (house convention, deploy/README.md).

WHAT WAS TRIMMED (every trim named, per the build charge)
  1. ALL dispatches/ handling (DISPATCH_STATUS, DISPATCH_STAGE_STATUS,
     DISPATCH_REQUIRED, DISPATCH_STAGE_REQUIRED, check_dispatch(), the
     dispatch-valid/-invalid self-test fixtures, and the ddir walk in
     run_live()). The fork has NO dispatches/ directory -- it is not a
     "sometimes absent" case to tolerate, it is a directory that does not
     exist in this harness's vocabulary at all (handoffs/ is the only loop
     envelope on this fork). Trimming this is a structural-inapplicability
     trim, not a leniency weakening: if a dispatches/ directory ever
     appears on this fork it will simply be invisible to this sensor (a gap
     to notice via `git status` unfamiliarity, not a silent false pass --
     no such directory is expected to exist per this fork's own
     architecture docs).
  2. PROTOCOL_EFFECTIVE / pre-protocol leniency machinery is KEPT (not
     trimmed) because handoffs/2026-05-27-5b-products-inventory-schema is a
     real folder-date boundary case on this fork (confirmed by an operator
     LEGACY_LENIENT_FOLDERS entry existed in the snapshot under the same
     name) -- see the live run's findings below for whether that folder
     needs the entry restored here too.
  3. Nothing else is trimmed structurally -- the handoffs checks apply
     as-is; dispatches/ is the only inapplicable surface.

FORK REALITY DISCOVERED DURING THE PORT (named, not silently patched)
  The live handoffs/ corpus does NOT use `locked_by_raw_file` uniformly.
  Surveyed across all 39 meta.yaml files: 19 use `locked_by_raw_file`
  (the schema the snapshot / design text assumes), 5 use `decision_raw_file`,
  2 use `decision_raw`, 1 uses `locked_decision_raw`, 1 uses
  `locked_decision_raw_file`, 1 uses `locked_decision`, and 14 locked
  folders carry NONE of these keys (either no raw-pointer field at all, or
  the pointer lives only in prose / a sibling handoff's meta via
  `integrated_into`). Two meta.yaml files additionally fail whole-file YAML
  parse outright (unquoted colon in prose value; a list/mapping indentation
  clash) -- both confirmed by direct PyYAML probe, not assumed.
  DISPOSITION: check_handoff()'s locked-status branch and extension check
  (b) both read EXACTLY `locked_by_raw_file` per the frozen design text
  (component C3: "resolves its locked_by_raw_file to an existing raw
  file..."). A locked folder lacking that exact key, or whose meta.yaml
  does not parse, is a NAMED VIOLATION -- never silently allowlisted and
  never patched into a synonym-matching guess. This is a genuine fork
  finding surfaced by the live --check run (see the build report), not a
  defect in this sensor: the naming drift is real content history, and
  papering over it with a synonym table would hide exactly the corruption/
  drift condition this sensor exists to tripwire.

Schema sources (keep in sync -- extension, never loosening):
  - handoff meta shape: handoffs/METHODOLOGY.md Sec meta.yaml shape (pruned)
  - pre-protocol handoffs (folder date < 2026-05-27) keep their as-authored
    field shape -> field-schema checks are SKIPPED for them, but status +
    INDEX cross-checks ALWAYS run. Leniency never suppresses the drift check.

P5 EXTENSION (component C3; imports deploy/registrations.py's
load_registrations, the sibling-import way registrations.py itself imports
compile-core.py -- see _load() below):

  (a) every raw/*.md whose frontmatter carries informed_by: (detected via
      the ported raw_informed_by(), degraded fallback included) must have a
      registration record that is REGISTERED LOCK-CLASS (see the F6
      predicate note below) -- missing registration, or a registration that
      is not lock-class, is a violation naming the raw file.
  (b) every handoffs/*/meta.yaml with status: locked must resolve
      locked_by_raw_file to an EXISTING raw file that is ITSELF registered
      lock-class (F6 predicate) -- named-allowlist exemptions only
      (LOOP_STATE_EXT_EXEMPT below; empty at port time).

  ADJUDICATION NOTE (2026-07-06, orchestrator, at the P5 atomic flip --
  applied after the first post-mint live --check surfaced 21 findings; all
  four finding classes resolved by UNIFORMLY APPLYING already-recorded
  rules, never new ad-hoc exemptions):
    * "Registered lock-class" in checks (a)/(b) is tested via the ENGINE'S
      CANONICAL F6 PREDICATE (_registered_is_lock_class below, mirroring
      compile-v2._registered_is_lock_class / check-run-diff's LOCK_CLASSES):
      lock-class iff event_class in {t1, correction, lock, informed_by} OR
      event_class_origin == 'judgment'. F6 is the established engine-wide
      meaning of lock-class treatment (judgment-assigned = maximally
      conservative = lock); the design's '{informed_by, lock}' wording is
      correctly read through that canonical predicate. NOT a loosening --
      judgment is the most conservative class. (Resolves the gateway-sync
      body-link lock raw, registered event_class 'judgment' by the mint.)
    * Extension check (b) honors the SAME pre-protocol leniency the ported
      base checks already apply (folder date < PROTOCOL_EFFECTIVE, or a
      LEGACY_LENIENT_FOLDERS entry): a PRE-protocol locked folder whose
      locked_by_raw_file is absent/empty, or whose meta.yaml does not
      parse, is a NOTE (named, counted), not a violation -- one leniency
      rule, uniformly applied; those folders are immutable artifacts
      predating the bidirectional-link convention. A POST-protocol folder
      with the same defect STILL fires (the going-forward discipline is
      untouched; fixture-proven both directions).
    * Extension check (b) consults HARNESS_ERA_FOLDERS with the same
      rationale + scope as the base checks (template-era governance
      artifacts outside this instance's protocol; named one-offs, never
      wildcarded): NOTE, not violation.
  The 16 pre-protocol absences + the 7-variant locked_by field-name drift +
  the upstream fallback dash bug remain REAL live-wiki content findings
  (intake doc authored separately, doctrine channel); these dispositions
  are leniency-scoping, not finding-suppression.
  (c) every top-level receipts/*.md (excluding receipts/journal/ and
      receipts/registrations/ and any other non-.md sidecar dir, e.g. this
      fork's receipts/verify/) must have a registration record with
      asserts_corpus_state == True -- a straight count cross-check; the 3
      known-hole receipts (deploy/known-holes.yaml) are registrable like any
      other receipt (adjudication 4/backfill-registrations.py already
      registers them with origin "unknown" -- a hole is never dropped from
      the ledger) and must be present in the registration map same as any
      other receipt.

  LIVE-TREE SEMANTICS PRE-MINT: the live registration store
  (receipts/registrations/) does not exist yet on this fork -- the mint
  (backfill-registrations.py, component C1) is a later atomic step per the
  design's Sec6 ("enlargement lands atomically"). Running --check against a
  tree with NO receipts/registrations/ directory therefore exits 2
  INCONCLUSIVE with a named reason (registration store absent) for the
  THREE EXTENSION CHECKS specifically -- it never silently exits 0 before
  the mint lands, and it never downgrades to exit 1 (a present-but-broken
  chain, e.g. tamper/gap/hash-mismatch, is load_registrations' own loud
  RegistrationViolation / JournalViolation, which this sensor lets propagate
  as an inconclusive reason too -- a broken chain is not the same condition
  as an absent one, but neither is silently clean). The PORTED base checks
  (handoffs/ meta.yaml, INDEX cross-check, raw back-link) are INDEPENDENT of
  the registration store and continue to run and report their own exit
  code/findings regardless of whether the store exists -- only the
  extension's own three checks are gated on the store's presence.

Exit codes (house convention, deploy/README.md Sec Sensors):
  0  PASS -- all checks (ported base + extension) pass
  1  FAIL -- one or more violations (each printed, two-space indented)
  2  INCONCLUSIVE -- could not run (PyYAML missing, INDEX unreadable,
     registration store absent pre-mint, or a broken registration chain)

Usage:
  py deploy/check-loop-state.py [--root DIR]    # validate the live repo
                                                 # (S9 fix 2026-08-05: the
                                                 # long-documented --check flag
                                                 # was never parsed -- the bare
                                                 # invocation IS the live run)
  py deploy/check-loop-state.py --self-test     # validate committed fixtures
                                                 # (deploy/test-fixtures/loop-state/)
"""

import importlib.util
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_HERE)
FIXTURES = os.path.join(_HERE, "test-fixtures", "loop-state")

PROTOCOL_EFFECTIVE = "2026-05-27"  # METHODOLOGY Sec Schema version

# Transition-day folders closed under mixed practice whose artifacts are now
# immutable (locked folder + append-only raw) -- field/coherence checks would
# flag unfixable history, so they get pre-protocol leniency. Named explicitly,
# never wildcarded; status + INDEX cross-checks still run for them.
# PORT HISTORY: first ported EMPTY (conservative -- snapshot entries not
# assumed to transfer), then the snapshot's entry RESTORED per orchestrator
# adjudication 2026-07-06: the named folder was imported onto this fork
# byte-identically from the live instance, so the live instance's recorded
# disposition of it carries over; re-firing a settled named one-off is
# noise, not vigilance.
LEGACY_LENIENT_FOLDERS = {"2026-05-27-5b-products-inventory-schema"}

# Locked folders whose lock raw is session-sourced (a `*-lock.md` raw, not the
# `ryan-decision-<n>` form) and carries the handoff back-link in its BODY rather
# than `informed_by` frontmatter. The raw is append-only (CLAUDE.md invariant 2),
# so the frontmatter half is STRUCTURALLY UNFIXABLE, and the folder is
# locked-immutable (invariant 5). This suppresses ONLY the "carries no informed_by
# frontmatter" violation for the named folder -- every other check (status, INDEX,
# field schema, confidence-audit presence, a *wrong* informed_by) still runs.
# Named one-offs, never wildcarded; the going-forward convention is unchanged
# (METHODOLOGY Sec Bidirectional link -- lock raws use `ryan-decision-<n>` naming +
# `informed_by`). Disposition: wiki/REVIEW.md "[compile] Loop-state tripwire"
# (2026-06-23, live instance).
# PORT HISTORY: first ported EMPTY, then the snapshot's entry RESTORED per
# orchestrator adjudication 2026-07-06 (same rationale as
# LEGACY_LENIENT_FOLDERS above: byte-identical import, recorded disposition
# carries over).
BODYLINK_BACKLINK_EXEMPT = {"2026-06-21-gateway-sync-runtime-model"}

# Template-repo-era governance handoffs imported as immutable evidence
# artifacts. NEW ON THE FORK (not in the snapshot), added per orchestrator
# adjudication 2026-07-06: these two folders predate / stand outside this
# instance's handoff protocol entirely -- they were authored in the harness
# TEMPLATE repo's own governance loop and carry a different field shape
# (missing several HANDOFF_REQUIRED keys, extra fields like `hypothesis`)
# and `../../` raw paths pointing into directories (harness-v2.0/adr/,
# adr/) that are not part of this fork's raw/ ledger. Exempt from the
# field-schema / INDEX / raw-back-link checks ONLY (status-vocabulary
# validity still applies via check_handoff's early status gate). Named
# one-offs, never wildcarded: any NEW folder exhibiting the same defects
# still fires every check.
HARNESS_ERA_FOLDERS = {
    "2026-06-08-harness-v2.0-scope-review",
    "2026-06-14-v3-engine-licensing-and-firewall-merge",
}

# P5 extension-only named allowlist (component C3, adjudication 5's "named-
# allowlist exemptions only" clause for check (b)). Separate from the two
# ported sets above because it exempts a DIFFERENT failure mode (locked
# folder whose locked_by_raw_file resolves to a raw that is NOT registered
# lock-class, or whose key is altogether absent under a known synonym this
# fork's history used before the design's frozen field name was set). EMPTY
# at port time -- never populated preemptively; the live run below reports
# what it finds and any allowlisting is a follow-on operator decision, not
# this build's call to make.
LOOP_STATE_EXT_EXEMPT = set()

HANDOFF_STATUS = {"open", "answered", "locked", "halted", "superseded"}
# Pre-protocol folders may carry legacy halt variants (INDEX legend).
HANDOFF_STATUS_LEGACY_EXTRA = {"halted-provider-dependency"}
HYPOTHESIS_OUTCOME = {"confirmed", "revised", "rejected", "pending"}

# Canonical meta shape: this list and the meta.yaml block in
# core/handoffs/HANDOFF-AUTHORING.md are the SAME fact in two homes, and the
# self-test's doc-parity case asserts they agree key-for-key (reconciled
# 2026-08-05 -- the doc had drifted 4-missing/3-retired and a meta authored
# from it drew seven violations here). A shape change lands in both files in
# the same commit or --self-test fails.
HANDOFF_REQUIRED = [
    "handoff_id", "status", "tier", "authored", "authored_by",
    "answered", "answered_by", "locked", "locked_by_raw_file",
    "decision_under_investigation", "parent_phase", "hypothesis_carried",
    "hypothesis_outcome", "target_substrate", "rounds_completed",
    "round_modes", "round_overrides", "supersedes", "superseded_by",
]
HANDOFF_OPTIONAL = {
    "packet_modes",  # A2 extension: per-round 'inline'|'reference'
    "locked_by",     # locker substrate identifier
    # v3.0-78 (handoff collapse, 2026-07-31): the park marker for a dead
    # headless close leg. Legal ONLY as `close: pending` alongside
    # `status: answered` (auto-retried by the next /handoff invocation or the
    # nightly standing-loop tick; no manual fallback). Amended here together
    # with core/handoffs/HANDOFF-AUTHORING.md's meta block (the canonical
    # shape's shipped home), per the parity rule above HANDOFF_REQUIRED.
    "close",
}

# Extension check (a)/(b) registered-class gate. WIDENED 2026-07-06 (orchestrator
# adjudication at the P5 atomic flip; see the ADJUDICATION NOTE in the module
# docstring) from the design text's literal {"informed_by", "lock"} to the engine's
# canonical F6 set -- mirrors compile-v2.LOCK_CLASSES / check-run-diff.LOCK_CLASSES.
LOCK_CLASSES = {"t1", "correction", "lock", "informed_by"}


def _registered_is_lock_class(record):
    """The engine's canonical F6 lock-class predicate against a REGISTRATION
    record -- mirrors compile-v2._registered_is_lock_class exactly: registered
    lock-class treatment is event_class in LOCK_CLASSES, OR event_class_origin
    == 'judgment' (F6: judgment-assigned = maximally conservative = lock).
    Adjudicated 2026-07-06 (module-docstring ADJUDICATION NOTE)."""
    if str(record.get("event_class_origin", "")).lower() == "judgment":
        return True
    return str(record.get("event_class", "")).lower() in LOCK_CLASSES


def _load(basename, alias):
    """Sibling-import a deploy/ module by file path -- the same pattern
    registrations.py itself uses to import compile-core.py and origin.py
    (avoids package-relative import machinery for a flat deploy/ dir)."""
    spec = importlib.util.spec_from_file_location(alias, os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def folder_date(name):
    m = re.match(r"^(\d{4}-\d{2}-\d{2})-", name)
    return m.group(1) if m else None


def _envelope_has_records(path):
    """A handoff envelope 'has records' when at least one immediate
    subdirectory carries a meta.yaml. Protocol DOCS living in the same
    directory (core/handoffs/HANDOFF-*.md, README.md) are plain files and
    never count."""
    if not os.path.isdir(path):
        return False
    for entry in os.listdir(path):
        if os.path.isfile(os.path.join(path, entry, "meta.yaml")):
            return True
    return False


def handoffs_dir(root):
    """Resolve the handoff envelope. Returns (path, ambiguous).

    The fork this sensor grew up on keeps records at `handoffs/`; the shipped
    template's protocol README instructs instances to create them at
    `core/handoffs/<YYYY-MM-DD>-<slug>/`, and the /handoff skill names both
    layouts ("the project's `handoffs/` directory -- `core/handoffs/` on
    projects that keep it there"). This sensor hardwired the fork path, so on
    every docs-following instance it scanned an empty location and went
    INCONCLUSIVE (reported live 2026-08-04, a LAMPS T1 lock session).

    Resolution is by CONTENT, not preference: the candidate holding at least
    one record folder wins. Records in BOTH is a real defect state --
    (first, True) so callers refuse loudly instead of silently scanning one
    envelope of two. Records in NEITHER falls back to the first candidate
    that exists on disk (empty-envelope reporting keeps its old shape), then
    to the fork path."""
    cands = [os.path.join(root, "handoffs"),
             os.path.join(root, "core", "handoffs")]
    with_records = [c for c in cands if _envelope_has_records(c)]
    if len(with_records) == 2:
        return with_records[0], True
    if with_records:
        return with_records[0], False
    for c in cands:
        if os.path.isdir(c):
            return c, False
    return cands[0], False


def load_yaml_file(path, yaml):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f.read())


def raw_informed_by(path, yaml):
    """Extract informed_by from a raw file's frontmatter. Ported from the
    live-instance snapshot, including the degraded line-level fallback --
    this is the exact extractor the P5 extension reuses per the design
    charge and the test-plan's own citation ("reuse its raw_informed_by
    extractor"). ONE fork divergence: the fallback's block-list dash bug is
    fixed here (see the inline comment at the fallback regex below) per
    orchestrator adjudication 2026-07-06; everything else is verbatim.

    Returns (found: bool, values: list[str]). Tolerates YAML-hostile prose in
    other frontmatter fields (long summaries with colons are common) by
    falling back to a line-level extraction of just the informed_by key.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False, []
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return False, []
    block = m.group(1)
    try:
        fm = yaml.safe_load(block)
        if isinstance(fm, dict) and "informed_by" in fm:
            val = fm["informed_by"]
            vals = val if isinstance(val, list) else [val]
            return True, [str(v) for v in vals if v is not None]
    except Exception:
        pass
    # Fallback: the whole block failed to parse -- pull the one key by line.
    # FORK DIVERGENCE FROM THE SNAPSHOT (fixed 2026-07-06, orchestrator
    # adjudication): the snapshot's regex here was r"^informed_by:\s*(.*)$",
    # whose \s* also matches NEWLINES -- for the block-list form
    # ("informed_by:" on its own line followed by "  - value" lines) it
    # crossed onto the next line and captured "- value" WITH the leading
    # dash, producing false "raw back-link broken" violations downstream
    # (normalize_handoff_ref never strips a leading dash). Two real raws on
    # this fork hit that path (two 2026-05 block-list-form decision raws;
    # see fork history). Fixed by
    # constraining the inline capture to the SAME line ([ \t]* instead of
    # \s*): a bare "informed_by:" line now yields an empty inline value and
    # falls through to the block-list loop below, which strips dashes
    # correctly. The bug exists upstream in the live-instance snapshot and
    # is being handed back separately; proven fixture-first here
    # (--self-test: block-list-under-hostile-frontmatter extraction + the
    # inline-form regression case).
    km = re.search(r"^informed_by:[ \t]*(.*)$", block, re.MULTILINE)
    if not km:
        return False, []
    inline = km.group(1).strip()
    if inline and inline not in ("|", ">"):
        return True, [inline.strip("[]").strip()]
    vals = []
    for line in block[km.end():].splitlines():
        lm = re.match(r"^\s+-\s+(.+)$", line)
        if lm:
            vals.append(lm.group(1).strip())
        elif line.strip():
            break
    return True, vals


def normalize_handoff_ref(value):
    """'handoffs/<id>/' or '<id>' (quoted or not) -> '<id>'."""
    v = str(value).strip().strip("'\"")
    if v.startswith("core/handoffs/"):
        v = v[len("core/handoffs/"):]
    if v.startswith("handoffs/"):
        v = v[len("handoffs/"):]
    return v.rstrip("/")


def check_handoff(folder, meta, index_active, index_archive, yaml, lenient, root=None):
    """Returns a list of violation strings for one handoff folder. PORTED
    verbatim from the snapshot (handoffs/ is this fork's only loop envelope;
    dispatches/ trimmed, see module header). `root` is the repo root to
    resolve locked_by_raw_file against -- defaults to this fork's own
    REPO_ROOT for the live run and the schema-only self-test call, but MUST
    be the fixture tempdir root when called from a full-tree self-test case
    (a live-run-time deviation from the snapshot, which had no multi-tree
    concept: the snapshot always validated the one repo it lived in)."""
    root = root if root is not None else REPO_ROOT
    v = []
    name = os.path.basename(folder)
    status = str(meta.get("status", "")).strip()

    legal = HANDOFF_STATUS | (HANDOFF_STATUS_LEGACY_EXTRA if lenient else set())
    if status not in legal:
        v.append(f"{name}: status '{status}' not in {sorted(legal)}")
        return v  # can't classify further without a legal status

    # Template-repo-era governance artifacts: field-schema / INDEX /
    # raw-back-link checks exempted ONLY (the status-vocabulary gate above
    # has already run for them). Named one-offs, never wildcarded -- see
    # HARNESS_ERA_FOLDERS' rationale comment.
    if name in HARNESS_ERA_FOLDERS:
        return v

    # INDEX cross-check -- ALWAYS runs, lenient or not (the drift tripwire).
    if index_active is not None:
        in_active = name in index_active
        in_archive = name in index_archive
        if status in ("open", "answered") or status.startswith("halted"):
            if not in_active:
                v.append(f"{name}: status '{status}' but no row in INDEX.md Active section")
        elif status in ("locked", "superseded"):
            if not in_archive:
                v.append(f"{name}: status '{status}' but no row in INDEX.md Archive section")

    if lenient:
        return v  # pre-protocol: field schema is intentional historical record

    # Field schema (current protocol only)
    for key in HANDOFF_REQUIRED:
        if key not in meta:
            v.append(f"{name}: missing required field '{key}'")
    unknown = set(meta) - set(HANDOFF_REQUIRED) - HANDOFF_OPTIONAL
    if unknown:
        v.append(f"{name}: fields outside the pruned shape: {sorted(unknown)}")

    ho = str(meta.get("hypothesis_outcome", "")).strip()
    if ho and ho not in HYPOTHESIS_OUTCOME:
        v.append(f"{name}: hypothesis_outcome '{ho}' not in {sorted(HYPOTHESIS_OUTCOME)}")

    pm = meta.get("packet_modes")
    if pm is not None:
        if not isinstance(pm, list) or any(m not in ("inline", "reference") for m in pm):
            v.append(f"{name}: packet_modes must be a list of 'inline'|'reference'")

    # v3.0-78: `close` is the dead-close-leg park marker. Only 'pending' is a
    # legal value, and only on a folder that is answered-awaiting-close --
    # anywhere else it would misrepresent the lock state.
    cl = meta.get("close")
    if cl is not None:
        if str(cl).strip() != "pending":
            v.append(f"{name}: close '{cl}' is not a legal value (only 'pending')")
        elif status != "answered":
            v.append(f"{name}: close: pending is only legal with status 'answered' "
                     f"(got status '{status}')")

    # Round/file coherence
    outputs = sorted(
        f for f in os.listdir(folder)
        if re.match(r"^output-round-\d+\.md$", f)
    ) if os.path.isdir(folder) else []
    rc = meta.get("rounds_completed")
    if isinstance(rc, int) and rc != len(outputs):
        v.append(f"{name}: rounds_completed={rc} but {len(outputs)} output-round-*.md file(s)")

    if status == "answered":
        if not meta.get("answered"):
            v.append(f"{name}: status answered but 'answered' date is empty")
        if isinstance(rc, int) and rc < 1:
            v.append(f"{name}: status answered but rounds_completed={rc}")

    if status == "locked":
        if not meta.get("locked"):
            v.append(f"{name}: status locked but 'locked' date is empty")
        lrf = meta.get("locked_by_raw_file")
        if not lrf:
            v.append(f"{name}: status locked but locked_by_raw_file is empty")
        else:
            raw_path = os.path.join(root, str(lrf).replace("raw/", "raw" + os.sep, 1))
            if not os.path.isfile(raw_path):
                v.append(f"{name}: locked_by_raw_file '{lrf}' does not exist on disk")
            else:
                found, vals = raw_informed_by(raw_path, yaml)
                hid = normalize_handoff_ref(meta.get("handoff_id", name))
                if not found:
                    if name not in BODYLINK_BACKLINK_EXEMPT:
                        v.append(f"{name}: raw back-link broken -- {lrf} carries no informed_by frontmatter")
                elif hid not in [normalize_handoff_ref(x) for x in vals]:
                    v.append(
                        f"{name}: raw back-link broken -- {lrf} informed_by={vals} "
                        f"does not name handoff_id '{hid}'"
                    )
        if not os.path.isfile(os.path.join(folder, "confidence-audit.md")):
            v.append(f"{name}: status locked but confidence-audit.md is missing")

    return v


# --------------------------------------------------------------- P5 extension
def _registrations_module():
    return _load("registrations.py", "registrations_loopstate")


def check_extension(root, yaml):
    """Runs the three P5 extension checks (component C3). Returns
    (violations: list[str], inconclusive: list[str], notes: list[str]).
    inconclusive is non-empty (exit 2) whenever the registration store is
    absent pre-mint or the chain itself is broken -- the extension never
    silently passes before the mint, per the design's live-tree-semantics
    clause. notes carries the named leniency dispositions (pre-protocol /
    HARNESS_ERA folders under check (b) -- 2026-07-06 adjudication, see the
    module-docstring ADJUDICATION NOTE): counted and printed, never silent,
    never exit-affecting."""
    violations = []
    inconclusive = []
    notes = []

    regs_dir = os.path.join(root, "receipts", "registrations")
    if not os.path.isdir(regs_dir):
        inconclusive.append(
            "registration store absent (no receipts/registrations/ directory) -- "
            "the P5 registration mint (backfill-registrations.py, component C1) "
            "has not run yet on this tree; extension checks (a)/(b)/(c) cannot "
            "run pre-mint and must not silently pass"
        )
        return violations, inconclusive, notes

    regs = _registrations_module()
    try:
        registrations = regs.load_registrations(root)
    except Exception as e:
        inconclusive.append(
            f"registration chain unreadable/broken at {regs_dir}: {e}"
        )
        return violations, inconclusive, notes

    # ---- check (a): every informed_by raw has a registration, event_class
    # in LOCK_CLASSES.
    raw_root = os.path.join(root, "raw")
    if os.path.isdir(raw_root):
        for entry in sorted(os.listdir(raw_root)):
            if not entry.endswith(".md"):
                continue
            path = os.path.join(raw_root, entry)
            if not os.path.isfile(path):
                continue
            found, _vals = raw_informed_by(path, yaml)
            if not found:
                continue
            rel = "raw/" + entry
            rec = registrations.get(rel)
            if rec is None:
                violations.append(
                    f"{rel}: carries informed_by frontmatter but has no registration record"
                )
            elif not _registered_is_lock_class(rec):
                violations.append(
                    f"{rel}: informed_by raw registered with event_class "
                    f"'{rec.get('event_class')}' (origin '{rec.get('event_class_origin')}'), "
                    f"not lock-class under the F6 predicate "
                    f"(event_class in {sorted(LOCK_CLASSES)} or origin 'judgment')"
                )

    # ---- check (b): every locked handoff resolves locked_by_raw_file to an
    # existing, registered lock-class raw (F6 predicate). Leniency scoping
    # per the 2026-07-06 adjudication (module-docstring ADJUDICATION NOTE):
    # pre-protocol folders (same PROTOCOL_EFFECTIVE / LEGACY_LENIENT_FOLDERS
    # rule the base checks apply) with absent-key or unparseable meta, and
    # HARNESS_ERA_FOLDERS, are NOTEs -- named + counted, never violations;
    # post-protocol folders with the same defects STILL fire.
    hdir, env_ambiguous = handoffs_dir(root)
    if env_ambiguous:
        violations.append(
            "handoff records found in BOTH handoffs/ and core/handoffs/ -- "
            "two live envelopes is a defect state (records must live in "
            "exactly one); consolidate before this check can be trusted"
        )
    if os.path.isdir(hdir):
        for entry in sorted(os.listdir(hdir)):
            folder = os.path.join(hdir, entry)
            mpath = os.path.join(folder, "meta.yaml")
            if not (os.path.isdir(folder) and os.path.isfile(mpath)):
                continue
            if entry in LOOP_STATE_EXT_EXEMPT:
                continue
            if entry in HARNESS_ERA_FOLDERS:
                notes.append(
                    f"{entry}: HARNESS_ERA folder -- template-era governance artifact "
                    f"outside this instance's protocol; extension check (b) skipped "
                    f"(named one-off, same scope as the base checks; 2026-07-06 adjudication)"
                )
                continue
            date = folder_date(entry)
            lenient = (date is not None and date < PROTOCOL_EFFECTIVE) \
                or entry in LEGACY_LENIENT_FOLDERS
            try:
                meta = load_yaml_file(mpath, yaml)
            except Exception as e:
                first = str(e).splitlines()[0]
                if lenient:
                    notes.append(
                        f"{entry}: legacy meta.yaml unparseable ({first}) -- pre-protocol "
                        f"immutable artifact, extension check (b) skipped (same leniency "
                        f"rule as the base checks; 2026-07-06 adjudication)"
                    )
                else:
                    violations.append(
                        f"{entry}: meta.yaml does not parse as YAML for extension check (b) "
                        f"({first})"
                    )
                continue
            if not isinstance(meta, dict):
                if lenient:
                    notes.append(
                        f"{entry}: legacy meta.yaml not a YAML mapping -- pre-protocol "
                        f"immutable artifact, extension check (b) skipped "
                        f"(2026-07-06 adjudication)"
                    )
                else:
                    violations.append(
                        f"{entry}: meta.yaml is not a YAML mapping (extension check (b))")
                continue
            if str(meta.get("status", "")).strip() != "locked":
                continue
            lrf = meta.get("locked_by_raw_file")
            if not lrf:
                if lenient:
                    notes.append(
                        f"{entry}: pre-protocol locked folder without locked_by_raw_file -- "
                        f"immutable artifact predating the bidirectional-link convention; "
                        f"extension check (b) skipped (named + counted, 2026-07-06 "
                        f"adjudication; remains a live-wiki content finding)"
                    )
                else:
                    violations.append(
                        f"{entry}: status locked but locked_by_raw_file is absent/empty -- "
                        f"cannot resolve to a registered lock raw (extension check (b))"
                    )
                continue
            rel = str(lrf).replace("\\", "/")
            raw_path = os.path.join(root, rel.replace("raw/", "raw" + os.sep, 1))
            if not os.path.isfile(raw_path):
                violations.append(
                    f"{entry}: locked_by_raw_file '{rel}' does not exist on disk "
                    f"(extension check (b))"
                )
                continue
            rec = registrations.get(rel)
            if rec is None:
                violations.append(
                    f"{entry}: locked_by_raw_file '{rel}' has no registration record "
                    f"(extension check (b))"
                )
            elif not _registered_is_lock_class(rec):
                violations.append(
                    f"{entry}: locked_by_raw_file '{rel}' registered with event_class "
                    f"'{rec.get('event_class')}' (origin '{rec.get('event_class_origin')}'), "
                    f"not lock-class under the F6 predicate (event_class in "
                    f"{sorted(LOCK_CLASSES)} or origin 'judgment') (extension check (b))"
                )

    # ---- check (c): every receipts/*.md in the shared conservation population has
    # a pointer-class registration (asserts_corpus_state True). Count cross-check;
    # holes included. Population sourced from regs.list_receipts_population(root)
    # -- the SAME function staleness.py's enlarged-ledger receipts enumeration
    # calls -- so the two sensors' receipts populations are PROVABLY identical, not
    # just coincidentally identical because today's corpus happens to be flat.
    # (B-2, 2026-07-09: was previously an independent os.listdir(receipts_root)
    # definition here -- non-recursive, so it happened to already skip the engine
    # sidecar dirs by never walking into subdirectories at all, a DIFFERENT reason
    # than staleness.py's explicit ENGINE_SIDECAR_DIRS exclusion; the two
    # definitions could have silently diverged the moment either changed
    # independently. Sourcing both from one shared function closes that for good.)
    for rel in regs.list_receipts_population(root):
        rec = registrations.get(rel)
        if rec is None:
            violations.append(
                f"{rel}: receipt has no registration record (extension check (c))"
            )
        elif rec.get("asserts_corpus_state") is not True:
            violations.append(
                f"{rel}: receipt registered with asserts_corpus_state="
                f"{rec.get('asserts_corpus_state')!r}, expected True "
                f"(extension check (c))"
            )

    return violations, inconclusive, notes


# ------------------------------------------------------- P6 extension (fork-new)
# Substrate-separation MECHANICAL ENFORCEMENT (2026-07-25). Prior to this, the
# sensor validated authored_by/answered_by/locked_by_raw_file/target_substrate
# for PRESENCE only -- it never checked that the answering substrate actually
# satisfies the substrate-separation doctrine the handoff skills state
# (the /handoff skill's --round machinery, formerly handoff-author/SKILL.md Step 2 Round N+1: "target must
# differ from all prior round substrates (listed in meta.yaml.round_modes and
# meta.yaml.answered_by history)"; the former handoff-receive/SKILL.md's "Substrate-
# separation check" section states the same rule for the verifier side).
#
# A NAIVE STRING COMPARE (answered_by == authored_by) is worse than no check:
# this fork's real handoffs/ corpus stores substrate identity as free text
# authored by humans/models across many months ("opus-4-7-1m-no-web-search",
# "GPT-5.5 Pro (deep research mode)", "planning-session", "claude-code",
# round-by-round narrative strings, multi-round YAML lists, self-corrections
# like "the output self-signs 'GPT-4o' ... operator confirms it is Gemini").
# A bare == would false-PASS almost everything (no two free-text strings are
# byte-identical) and would be unable to tell "same substrate, different
# wording" from "different substrate" at all -- exactly the false-PASS
# failure mode this check exists to avoid.
#
# NORMALIZER: parse_substrate_identity() below extracts a best-effort
# (vendor, family, version) triple from free text via known model-family
# tokens (opus/sonnet/haiku/fable -> anthropic; gpt/codex -> openai;
# gemini -> google; grok -> xai). CONSERVATIVE BY DESIGN: it returns
# "unparseable" (None) whenever confidence is not high -- no known family
# token found (e.g. "planning-session", "claude-code" -- the latter names
# Anthropic's CLI tool, not a specific model family, so family is genuinely
# unknown), OR more than one DISTINCT (vendor, family) pair is named in the
# same text (e.g. the GPT-4o/Gemini self-correction above -- guessing which
# mention is authoritative would be exactly the kind of silent
# misattribution this check must not produce).
#
# COMPARISON: for each handoff round (author = round 0), the answering
# substrate must differ from ALL prior round substrates, not just the
# immediately preceding one (same rule the skills state). The required
# separation LEVEL is read from the round's own target_substrate text:
# vendor-level (e.g. "cross-vendor", "non-Claude", "NOT same-vendor") when
# the text says so explicitly; family-level otherwise (the doctrine's own
# baseline example in the former handoff-author/SKILL.md Step 2: "a different frontier
# model family"). A round whose text is explicitly self-labeled
# "NOT firewall-valid" (this fork's own vocabulary for an acknowledged
# same-family advisory/pressure pass that was never meant to satisfy the
# separation requirement -- see e.g. 2026-06-14-v3-engine-licensing-and-
# firewall-merge's output-round-3/4) is skipped: the source data itself
# already names these as non-firewall, so flagging them would misrepresent
# doctrine the corpus has already correctly self-assessed.
#
# SEVERITY: a confirmed same-substrate violation on a meta dated 2026-07-25
# (this enforcement's ship date) or later is a hard FAIL. On an older meta it
# is a WARN labeled "historical -- recorded before mechanical enforcement
# (2026-07-25)" (the corpus predates the check; it is a finding to surface,
# not silently absorbed retroactively as a violation). ANY unparseable
# identifier is always a WARN naming the value -- never a silent PASS
# (a naive check would drop it), never a FAIL (we are not confident it is
# wrong). Field PRESENCE checks are unaffected -- see check_handoff() above.

SUBSTRATE_ENFORCEMENT_CUTOFF = "2026-07-25"

# (regex, vendor, family) -- order doesn't matter, all patterns are tried.
_SUBSTRATE_FAMILY_TOKENS = [
    (r"\bopus\b", "anthropic", "opus"),
    (r"\bsonnet\b", "anthropic", "sonnet"),
    (r"\bhaiku\b", "anthropic", "haiku"),
    (r"\bfable\b", "anthropic", "fable"),
    (r"\bgpt\b", "openai", "gpt"),
    (r"\bcodex\b", "openai", "gpt"),  # OpenAI Codex runs on the GPT family
    (r"\bgemini\b", "google", "gemini"),
    (r"\bgrok\b", "xai", "grok"),
]

# Immediate-next-token suffix words that are deployment/role context, not part
# of the model's own version identity -- excluded from the reported version
# string (which is informational only; comparisons below never use it).
_SUBSTRATE_SUFFIX_STOPWORDS = {
    "session", "chat", "orchestrator", "hub", "consumer", "panel", "fresh",
    "mode", "class", "with", "for", "and", "the", "a", "an",
}

_SUBSTRATE_VENDOR_LEVEL_TRIGGERS = (
    "cross-vendor", "cross vendor", "non-claude", "not claude",
    "different vendor", "vendor separat", "vendor-separat",
    "not same-vendor", "not same vendor", "must not be claude",
    "must not be answered by any claude",
)

_SUBSTRATE_FIREWALL_EXEMPT_MARKERS = (
    "not firewall-valid", "non-firewall-valid", "not firewall valid",
)


class SubstrateID:
    """Best-effort parsed (vendor, family, version) identity. `version` is
    informational only -- substrate_id_equal() below never reads it; the
    doctrine's separation unit is family (or vendor, when required)."""
    __slots__ = ("vendor", "family", "version", "raw")

    def __init__(self, vendor, family, version, raw):
        self.vendor = vendor
        self.family = family
        self.version = version
        self.raw = raw

    def raw_summary(self):
        s = f"{self.vendor}/{self.family}"
        if self.version:
            s += f"/{self.version}"
        return s


def _extract_substrate_version(tail):
    """`tail` is the text immediately following a matched family token (e.g.
    "-4-8-1m-no-web-search" after "opus"). Walks up to 3 pure-digit,
    hyphen/dot-separated segments as the version core, then optionally
    appends ONE trailing alpha suffix segment.

    A digit segment glued directly to letters with no separator (e.g. "1m",
    "200k" -- context-window tags) is NEVER consumed, even partially: the
    lookahead in the per-segment regex requires the digit run be followed by
    a separator or end-of-string, so "1m" cannot contribute a dangling "1" to
    the version the way a naive `\\d+` would. This is what makes
    "claude-opus-4-8-1m" parse to version "4.8" (not "4.8.1") and
    "gpt-5.6-sol" parse to version "5.6-sol" (the alpha suffix IS kept -- it
    is not glued to a digit, so it is not a context tag)."""
    m = re.match(r"[\s\-]+", tail)
    if not m:
        return None
    pos = m.end()
    core_parts = []
    while len(core_parts) < 3:
        seg_m = re.match(r"(\d+)(?=[\s\-.]|$)", tail[pos:])
        if not seg_m:
            break
        core_parts.append(seg_m.group(1))
        pos += seg_m.end()
        sep_m = re.match(r"[.\-]", tail[pos:])
        if not sep_m:
            break
        pos += 1
    if not core_parts:
        return None
    core = ".".join(core_parts)
    rest = tail[pos:]
    suf_m = re.match(r"[\s.\-]?([a-z][a-z0-9]*)\b", rest)
    if suf_m and suf_m.group(1) not in _SUBSTRATE_SUFFIX_STOPWORDS:
        return f"{core}-{suf_m.group(1)}"
    return core


def parse_substrate_identity(text):
    """Best-effort free-text -> SubstrateID, or None if unparseable
    (conservative -- see the P6 module-header note above for the two
    unparseable conditions: no known family token, or more than one
    DISTINCT family named in the same text)."""
    if not text:
        return None
    t = str(text).lower()
    hits = {}
    for pattern, vendor, family in _SUBSTRATE_FAMILY_TOKENS:
        m = re.search(pattern, t)
        if m:
            hits.setdefault((vendor, family), m)
    if len(hits) != 1:
        return None
    (vendor, family), m = next(iter(hits.items()))
    version = _extract_substrate_version(t[m.end():])
    return SubstrateID(vendor, family, version, str(text))


def substrate_id_equal(a, b, level):
    """level: 'vendor' (vendor alone must differ) or 'family' (vendor+family
    pair must differ -- the doctrine's baseline minimum)."""
    if a is None or b is None:
        return False
    if level == "vendor":
        return a.vendor == b.vendor
    return a.vendor == b.vendor and a.family == b.family


def _vendor_level_required(target_substrate_text):
    if not target_substrate_text:
        return False
    t = str(target_substrate_text).lower()
    return any(trig in t for trig in _SUBSTRATE_VENDOR_LEVEL_TRIGGERS)


def _round_is_firewall_exempt(round_text):
    t = str(round_text).lower()
    return any(marker in t for marker in _SUBSTRATE_FIREWALL_EXEMPT_MARKERS)


def _substrate_short(text, limit=140):
    t = re.sub(r"\s+", " ", str(text)).strip()
    return t if len(t) <= limit else t[: limit - 3] + "..."


def split_substrate_rounds(value):
    """answered_by may be: absent, a single string (one round, or a SINGLE
    string with multiple rounds narrated inline -- e.g. "round-1: X; round-2:
    Y"), or a YAML list (one entry per round, this fork's more common
    multi-round shape -- see 2026-06-08-harness-v2.0-scope-review). Returns
    an ordered list of per-round description strings."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None and str(v).strip()]
    text = str(value)
    markers = list(re.finditer(r"round[\s-]*\d+\s*[:\(]", text, re.IGNORECASE))
    if len(markers) >= 2:
        segments = []
        for i, mm in enumerate(markers):
            start = mm.start()
            end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
            segments.append(text[start:end].strip())
        return segments
    return [text]


def _substrate_meta_is_new(meta, entry):
    """Fail-closed: if no date evidence is found at all, treat as 'new'
    (strict) rather than silently downgrading to historical leniency."""
    dates = []
    for key in ("authored", "answered", "locked"):
        v = meta.get(key)
        if isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", v.strip()):
            dates.append(v.strip())
    fd = folder_date(entry)
    if fd:
        dates.append(fd)
    if not dates:
        return True
    return max(dates) >= SUBSTRATE_ENFORCEMENT_CUTOFF


def check_substrate_separation(entry, meta):
    """Returns (violations, warnings) for one handoff's substrate-separation
    doctrine check. violations are FAIL-level (folded into the caller's
    violations list, same as any other check); warnings never affect exit
    code (printed WARN, same treatment as `notes` elsewhere in this sensor
    but distinctly labeled so historical/unparseable findings are never
    mistaken for the routine leniency notes)."""
    violations = []
    warnings = []

    rounds_raw = split_substrate_rounds(meta.get("answered_by"))
    if not rounds_raw:
        return violations, warnings  # nothing answered yet -- nothing to compare

    authored_raw = meta.get("authored_by")
    prior_ids = []
    if authored_raw:
        author_id = parse_substrate_identity(authored_raw)
        if author_id is not None:
            prior_ids.append(("authored_by", author_id))
        else:
            warnings.append(
                f"{entry}: authored_by value {_substrate_short(authored_raw)!r} "
                f"is not a parseable substrate identifier -- substrate "
                f"separation cannot be mechanically verified against it"
            )

    vendor_required = _vendor_level_required(meta.get("target_substrate"))
    level = "vendor" if vendor_required else "family"
    is_new = _substrate_meta_is_new(meta, entry)

    for i, rtext in enumerate(rounds_raw, start=1):
        if _round_is_firewall_exempt(rtext):
            continue  # self-declared non-firewall round (see module header)
        rid = parse_substrate_identity(rtext)
        if rid is None:
            warnings.append(
                f"{entry}: round {i} answered_by value is not a parseable "
                f"substrate identifier ({_substrate_short(rtext)!r}) -- "
                f"substrate separation cannot be mechanically verified for "
                f"this round"
            )
            prior_ids.append((f"round {i} answered_by", None))
            continue
        same_as = next(
            (
                (label, pid)
                for label, pid in prior_ids
                if pid is not None and substrate_id_equal(rid, pid, level)
            ),
            None,
        )
        if same_as is not None:
            label, pid = same_as
            msg = (
                f"{entry}: round {i} answered_by ({rid.raw_summary()}) fails "
                f"substrate separation ({level}-level required) -- same as "
                f"{label} ({pid.raw_summary()})"
            )
            if is_new:
                violations.append(msg)
            else:
                warnings.append(
                    msg + " -- historical, recorded before mechanical "
                    "enforcement (2026-07-25)"
                )
        prior_ids.append((f"round {i} answered_by", rid))

    return violations, warnings


def run_live(yaml, root=None):
    root = root or REPO_ROOT
    violations = []
    inconclusive = []  # genuine can't-run conditions -> exit 2
    notes = []         # informational (expected legacy state) -> no exit impact
    substrate_warnings = []  # P6: historical violations / unparseable identifiers

    # envelope INDEX.md sections (envelope resolved by content -- handoffs/
    # or core/handoffs/, see handoffs_dir())
    hdir, env_ambiguous = handoffs_dir(root)
    env_rel = os.path.relpath(hdir, root).replace(os.sep, "/")
    if env_ambiguous:
        inconclusive.append(
            "handoff records found in BOTH handoffs/ and core/handoffs/ -- "
            "two live envelopes; consolidate to one before this sensor's "
            "results can be trusted (scanning %s only for this run)" % env_rel
        )
    index_active = index_archive = None
    index_path = os.path.join(hdir, "INDEX.md")
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            text = f.read()
        parts = re.split(r"\n## Archive\b", text, maxsplit=1)
        index_active = parts[0]
        index_archive = parts[1] if len(parts) > 1 else ""
    except OSError as e:
        inconclusive.append(f"{env_rel}/INDEX.md unreadable ({e}) -- INDEX cross-checks skipped")

    n_handoffs = 0

    if os.path.isdir(hdir):
        for entry in sorted(os.listdir(hdir)):
            folder = os.path.join(hdir, entry)
            mpath = os.path.join(folder, "meta.yaml")
            if not (os.path.isdir(folder) and os.path.isfile(mpath)):
                continue
            date = folder_date(entry)
            if date is None:
                continue
            n_handoffs += 1
            lenient = date < PROTOCOL_EFFECTIVE or entry in LEGACY_LENIENT_FOLDERS
            try:
                meta = load_yaml_file(mpath, yaml)
            except Exception as e:
                first = str(e).splitlines()[0]
                if lenient:
                    notes.append(
                        f"{entry}: legacy meta.yaml unparseable ({first}) -- "
                        f"pre-protocol immutable artifact, skipped"
                    )
                else:
                    violations.append(f"{entry}: meta.yaml does not parse as YAML ({first})")
                continue
            if not isinstance(meta, dict):
                violations.append(f"{entry}: meta.yaml is not a YAML mapping")
                continue
            violations.extend(
                check_handoff(folder, meta, index_active, index_archive, yaml, lenient, root=root)
            )
            sub_violations, sub_warnings = check_substrate_separation(entry, meta)
            violations.extend(sub_violations)
            substrate_warnings.extend(sub_warnings)
    else:
        notes.append(
            f"{env_rel}/ directory absent -- handoff checks vacuously clean")

    # dispatches/ was TRIMMED from this port (see module header, trim #1) --
    # the fork has no such directory and no such vocabulary. Nothing to walk.

    print(f"Scanned {n_handoffs} handoff folder(s) (no dispatches/ on this fork -- trimmed at port).")
    for note in notes:
        print(f"  NOTE: {note}")

    # ---- P5 extension (component C3) ----
    ext_violations, ext_inconclusive, ext_notes = check_extension(root, yaml)
    if ext_inconclusive:
        for r in ext_inconclusive:
            print(f"  INCONCLUSIVE (extension): {r}")
    else:
        print(f"Extension checks ran: {len(ext_violations)} violation(s), "
              f"{len(ext_notes)} note(s).")
    for note in ext_notes:
        print(f"  NOTE: {note}")
    violations.extend(ext_violations)
    inconclusive.extend(ext_inconclusive)

    # ---- P6 extension (substrate-separation mechanical enforcement) ----
    print(f"Substrate-separation checks: {len(substrate_warnings)} warning(s) "
          f"(historical violations + unparseable identifiers; new-dated "
          f"confirmed violations are folded into the FAIL list below).")
    for w in substrate_warnings:
        print(f"  WARN: {w}")

    for note in inconclusive:
        if not note.startswith("registration store absent") and not note.startswith(
            "registration chain unreadable"
        ):
            print(f"  INCONCLUSIVE: {note}")
    for vi in violations:
        print(f"  {vi}")

    if violations:
        print(f"RESULT: FAIL -- {len(violations)} loop-state violation(s)")
        return 1
    if inconclusive:
        print("RESULT: INCONCLUSIVE -- checks partially skipped")
        return 2
    print(f"RESULT: PASS -- loop state coherent ({n_handoffs} handoffs)")
    return 0


_TREE_SRC = os.path.join(FIXTURES, "trees")


def _build_tree_copy(case_name, dest):
    """Copies the committed fixture SOURCE tree (deploy/test-fixtures/
    loop-state/trees/<case_name>/ -- plain files, no .git, no minted
    registration JSON) into a disposable tempdir, git-inits it there, and --
    if a _registrations.json manifest is present at the tree's root --
    mints each declared registration via registrations.append_registration
    (the real C1 substrate, imported the sibling way). A tree with NO
    manifest file mints nothing at all, leaving receipts/registrations/
    absent (the pre-mint / registration-store-absent case). This mirrors
    backfill-registrations.py's own self-test convention: fixture SOURCE is
    committed and reviewable; the disposable copy + real chain-mint happens
    at test-run time, never touching the live tree."""
    import shutil
    import subprocess
    src = os.path.join(_TREE_SRC, case_name)
    for sub in ("raw", "handoffs", "core", "receipts"):
        s = os.path.join(src, sub)
        if os.path.isdir(s):
            shutil.copytree(s, os.path.join(dest, sub))
    subprocess.run(["git", "-C", dest, "init", "-q"], capture_output=True)
    subprocess.run(["git", "-C", dest, "config", "user.email", "t@t"], capture_output=True)
    subprocess.run(["git", "-C", dest, "config", "user.name", "t"], capture_output=True)

    manifest_path = os.path.join(src, "_registrations.json")
    if not os.path.isfile(manifest_path):
        return  # deliberately no manifest -> registration store stays absent
    import json
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    regs = _registrations_module()
    for entry in manifest:
        rec = dict(entry, kind="registration", registered_at="2026-07-06T00:00:00")
        regs.append_registration(dest, rec)
    # Ensure the store directory exists even for an EMPTY manifest ([]) --
    # otherwise an intentionally-empty-but-present store (the "raw/receipt
    # exists but is simply not covered by any registration" violation
    # cases) would be indistinguishable from a wholly-absent store (the
    # pre-mint inconclusive case), which must stay a DIFFERENT condition.
    os.makedirs(regs.registrations_dir(dest), exist_ok=True)


def run_self_test(yaml):
    """Fixture-based validation of deploy/test-fixtures/loop-state/. Runs
    BOTH the ported base checks (schema-only, over synthetic meta.yaml
    fixtures) AND a full run_live()-style pass over a valid fixture TREE
    plus one invalid fixture tree per extension violation class. Tree
    fixtures are copied into disposable tempdirs at run time (never the
    live tree) -- see _build_tree_copy()."""
    import shutil
    import tempfile
    failures = []

    # ---- (1) ported base-check fixtures (schema-only, handoff kind only --
    # dispatch fixtures trimmed along with dispatches/ support).
    base_cases = [
        ("handoff-valid.yaml", True),
        ("handoff-invalid.yaml", False),
    ]
    for fname, expect_pass in base_cases:
        path = os.path.join(FIXTURES, fname)
        if not os.path.isfile(path):
            failures.append(f"fixture missing: {fname}")
            continue
        try:
            meta = load_yaml_file(path, yaml)
        except Exception as e:
            failures.append(f"{fname}: does not parse ({e})")
            continue
        v = check_handoff(os.path.join(FIXTURES, "no-such-folder"), meta,
                           None, None, yaml, lenient=False)
        v = [x for x in v if "does not exist on disk" not in x
             and "confidence-audit.md" not in x
             and "output-round" not in x]
        ok = not v
        if ok != expect_pass:
            failures.append(
                f"{fname}: expected {'PASS' if expect_pass else 'FAIL'}, got "
                f"{'PASS' if ok else 'FAIL'} ({v[:3]})"
            )

    # ---- (1a2) v3.0-78 close-park marker cases (schema-only, inline metas
    # derived from the valid fixture): `close: pending` on an answered folder
    # is legal; any other value, or pending on a non-answered folder, fires.
    cp_base_path = os.path.join(FIXTURES, "handoff-valid.yaml")
    try:
        cp_base = load_yaml_file(cp_base_path, yaml)
    except Exception as e:
        cp_base = None
        failures.append(f"handoff-valid.yaml (close-park base): does not parse ({e})")
    if isinstance(cp_base, dict):
        def _cp_meta(**over):
            m = dict(cp_base)
            m.update(over)
            return m
        cp_cases = [
            ("close-pending-on-answered-passes",
             _cp_meta(status="answered", locked=None, locked_by_raw_file=None,
                      close="pending", rounds_completed=1), True),
            ("close-bogus-value-fires",
             _cp_meta(status="answered", locked=None, locked_by_raw_file=None,
                      close="retrying", rounds_completed=1), False),
            ("close-pending-on-locked-fires",
             _cp_meta(close="pending"), False),
        ]
        for cname, cmeta, expect_pass in cp_cases:
            cv = check_handoff(os.path.join(FIXTURES, "no-such-folder"), cmeta,
                                None, None, yaml, lenient=False)
            cv = [x for x in cv if "does not exist on disk" not in x
                  and "confidence-audit.md" not in x
                  and "output-round" not in x
                  and "'answered' date is empty" not in x]
            ok = not cv
            if ok != expect_pass:
                failures.append(
                    f"close-park case {cname}: expected "
                    f"{'PASS' if expect_pass else 'FAIL'}, got "
                    f"{'PASS' if ok else 'FAIL'} ({cv[:3]})")

    # ---- (1a3) envelope resolution (fork handoffs/ vs the template's
    # documented core/handoffs/; the hardwired fork path went INCONCLUSIVE
    # live on a docs-following instance, 2026-08-04).
    def _env_case(layout):
        d = tempfile.mkdtemp(prefix="cls-env-")
        for rel, record in layout:
            p = os.path.join(d, *rel.split("/"))
            os.makedirs(p, exist_ok=True)
            if record:
                sub = os.path.join(p, "2026-08-01-x")
                os.makedirs(sub, exist_ok=True)
                with open(os.path.join(sub, "meta.yaml"), "w",
                          encoding="utf-8") as fh:
                    fh.write("status: open\n")
            else:
                with open(os.path.join(p, "HANDOFF-AUTHORING.md"), "w",
                          encoding="utf-8") as fh:
                    fh.write("protocol doc, not a record\n")
        return d

    env_cases = [
        ("fork layout resolves handoffs/",
         [("handoffs", True), ("core/handoffs", False)], "handoffs", False),
        ("template layout resolves core/handoffs (doc files never count)",
         [("core/handoffs", True)], "core/handoffs", False),
        ("records in both envelopes -> ambiguous",
         [("handoffs", True), ("core/handoffs", True)], "handoffs", True),
        ("no records anywhere -> first existing dir",
         [("core/handoffs", False)], "core/handoffs", False),
    ]
    for cname, layout, want_rel, want_amb in env_cases:
        d = _env_case(layout)
        try:
            got, amb = handoffs_dir(d)
            got_rel = os.path.relpath(got, d).replace(os.sep, "/")
            if got_rel != want_rel or amb is not want_amb:
                failures.append(
                    f"envelope case '{cname}': got ({got_rel}, {amb}), "
                    f"expected ({want_rel}, {want_amb})")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # ---- (1a4) doc-parity (D1, 2026-08-05): the canonical meta shape has
    # two homes -- HANDOFF_REQUIRED above and the meta.yaml block in
    # core/handoffs/HANDOFF-AUTHORING.md -- and they must agree key-for-key
    # (the doc had drifted 4-missing/3-retired; a meta authored from it drew
    # seven violations here). Walk up from deploy/ to find the doc (instance
    # layout: <root>/core/handoffs/; template layout: the repo root a few
    # levels above extracted/deploy/). The doc being unfindable is a FAILURE,
    # not a skip -- a tree shipping this sensor without the canonical meta
    # doc is exactly the silent-drift state this case exists to catch.
    def _doc_meta_parity():
        d, cand = _HERE, None
        for _ in range(8):
            p = os.path.join(d, "core", "handoffs", "HANDOFF-AUTHORING.md")
            if os.path.isfile(p):
                cand = p
                break
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        if cand is None:
            return ["doc-parity: core/handoffs/HANDOFF-AUTHORING.md not "
                    "found walking up from %s" % _HERE]
        with open(cand, encoding="utf-8") as fh:
            doc_text = fh.read()
        m = re.search(r"## meta\.yaml.*?```yaml\n(.*?)```", doc_text, re.S)
        if not m:
            return ["doc-parity: %s has no yaml fence under '## meta.yaml'"
                    % cand]
        got = set(re.findall(r"(?m)^([a-z_]+):", m.group(1)))
        want = set(HANDOFF_REQUIRED)
        probs = []
        if want != got:
            probs.append(
                "doc-parity: %s meta block disagrees with HANDOFF_REQUIRED "
                "(missing from doc: %s; extra in doc: %s)"
                % (cand, sorted(want - got), sorted(got - want)))
        for k in sorted(HANDOFF_OPTIONAL):
            if not re.search(r"`%s\b" % re.escape(k), doc_text):
                probs.append("doc-parity: optional key '%s' not mentioned "
                             "in %s" % (k, cand))
        return probs
    failures.extend(_doc_meta_parity())

    # ---- (1b) degraded-fallback fix cases (fork divergence, orchestrator
    # adjudication 2026-07-06): block-list form under YAML-hostile
    # frontmatter must extract CLEAN values (no leading dash -- the fixed
    # snapshot bug); inline form must still work (regression).
    fb_block = os.path.join(FIXTURES, "fallback-blocklist-hostile.md")
    found, vals = raw_informed_by(fb_block, yaml)
    if not (found and vals == ["handoffs/2026-07-06-fixture-blocklist-case/",
                               "2026-07-06-second-value"]):
        failures.append(
            f"fallback-blocklist-hostile.md: expected clean block-list "
            f"extraction, got found={found} vals={vals}")
    fb_inline = os.path.join(FIXTURES, "fallback-inline-hostile.md")
    found, vals = raw_informed_by(fb_inline, yaml)
    if not (found and vals == ["2026-07-06-fixture-inline-case"]):
        failures.append(
            f"fallback-inline-hostile.md: expected inline regression "
            f"extraction, got found={found} vals={vals}")

    # ---- (1c) HARNESS_ERA_FOLDERS exemption: the SAME defect-shaped meta
    # is fed to check_handoff under an exempt name (zero violations) and a
    # non-exempt current-protocol name (violations fire) -- proving the
    # exemption is a named one-off, never a wildcard.
    he_path = os.path.join(FIXTURES, "harness-era-shape.yaml")
    try:
        he_meta = load_yaml_file(he_path, yaml)
    except Exception as e:
        he_meta = None
        failures.append(f"harness-era-shape.yaml: does not parse ({e})")
    if isinstance(he_meta, dict):
        v_exempt = check_handoff(
            os.path.join(FIXTURES, "2026-06-08-harness-v2.0-scope-review"),
            he_meta, None, None, yaml, lenient=False)
        if v_exempt:
            failures.append(
                f"harness-era-shape.yaml under exempt folder name: expected "
                f"0 violations, got {v_exempt[:3]}")
        v_plain = check_handoff(
            os.path.join(FIXTURES, "2026-07-06-not-exempt-same-defects"),
            he_meta, None, None, yaml, lenient=False)
        if not any("missing required field" in x for x in v_plain) \
                or not any("outside the pruned shape" in x for x in v_plain):
            failures.append(
                f"harness-era-shape.yaml under NON-exempt folder name: "
                f"expected field-schema violations to fire, got {v_plain[:3]}")

    # ---- (2) full-tree fixtures (valid + one invalid per extension
    # violation class + registration-store-absent + YAML-hostile
    # informed_by degraded-fallback proof). Each is copied into its own
    # disposable tempdir + git-inited + registrations minted there.
    # NAMES SHORTENED 2026-07-25 (Windows MAX_PATH fix at source -- see init.ps1's
    # pre-flight guard comment and TEMPLATE-README.md's long-path note): the tree_cases
    # first elements are now short slugs, not the old prose-length directory names, so
    # every tracked fixture path stays clear of Windows' 260-char ceiling regardless of
    # clone location. Meaning is unchanged -- see each tree's own _registrations.json /
    # meta.yaml comments for the full case description.
    tree_cases = [
        ("valid-tree", 0),
        ("inv-unreg", 1),                     # invalid-unregistered-informed-by
        ("inv-noraw", 1),                     # invalid-locked-handoff-missing-raw
        # explicit non-lock class ('compile'/explicit) STILL fires under the F6
        # predicate (2026-07-06 adjudication regression half)
        ("inv-badcls", 1),                    # invalid-locked-handoff-raw-not-lock-class
        ("inv-noreg", 1),                     # invalid-receipt-no-registration
        ("inv-notptr", 1),                    # invalid-receipt-not-pointer-class
        ("inv-degp", 0),                      # invalid-degraded-parse-informed-by
        ("no-reg-store", 2),                  # inconclusive-no-store
        # ---- 2026-07-06 adjudication cases (module-docstring ADJUDICATION
        # NOTE): leniency scoping + the canonical F6 predicate, both directions.
        ("val-prepp", 0),           # valid-preprotocol-absent-key: pre-protocol absent key => NOTE
        ("inv-pp-nokey", 1),        # invalid-postprotocol-absent-key: post-protocol same defect => fires
        ("val-hera-b", 0),          # valid-harness-era-checkb: HARNESS_ERA folder => NOTE
        ("val-judg", 0),            # valid-judgment-lock-raw: judgment-origin reg => lock-class
        # ---- B-2 (steady-state-ops brief, 2026-07-08/09): a receipts/verify/
        # sidecar file (unregistered, no corresponding _registrations.json entry)
        # must be EXCLUDED from extension check (c)'s receipts population -- PASS,
        # not "receipt has no registration record".
        ("val-vfy", 0),             # valid-verify-sidecar-excluded
    ]
    # The val-hera-b tree's handoff folder is named "2026-06-08-f-hera" -- a
    # dedicated test-only slug, deliberately NOT one of the two real production
    # HARNESS_ERA_FOLDERS entries (renaming this fixture to reuse a real entry's
    # name would make the path too long again; see the MAX_PATH fix note above).
    # It is added to HARNESS_ERA_FOLDERS for the duration of this one case only,
    # so the exemption mechanism is proven generically without ever widening the
    # production named-allowlist itself (which stays exactly the two real folders).
    _HERA_TEST_FOLDER = "2026-06-08-f-hera"
    for case_name, expect_exit in tree_cases:
        src = os.path.join(_TREE_SRC, case_name)
        if not os.path.isdir(src):
            failures.append(f"fixture tree missing: {case_name}")
            continue
        tmp = tempfile.mkdtemp(prefix="loopstate-" + case_name + "-")
        injected = case_name == "val-hera-b"
        if injected:
            HARNESS_ERA_FOLDERS.add(_HERA_TEST_FOLDER)
        try:
            _build_tree_copy(case_name, tmp)
            rc = run_live(yaml, root=tmp)
            if rc != expect_exit:
                failures.append(
                    f"{case_name}: expected exit {expect_exit}, got exit {rc}"
                )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            if injected:
                HARNESS_ERA_FOLDERS.discard(_HERA_TEST_FOLDER)

    # ---- (3) B-2 (2026-07-09): staleness.py's and check-loop-state.py's receipts
    # populations are PROVABLY identical -- both now call
    # registrations.list_receipts_population(root). Prove it directly, over the
    # same tree fixture used above, which plants an engine sidecar file
    # (receipts/verify/packets/plant.md) that must be excluded from BOTH.
    staleness_mod = _load("staleness.py", "staleness_loopstate_selftest_receipts")
    regs_mod_cmp = _registrations_module()
    tmp_cmp = tempfile.mkdtemp(prefix="loopstate-receipts-population-parity-")
    try:
        _build_tree_copy("val-vfy", tmp_cmp)  # valid-verify-sidecar-excluded
        staleness_population = {rel for _fp, rel in staleness_mod._iter_receipts_only(tmp_cmp)}
        checkloopstate_population = set(regs_mod_cmp.list_receipts_population(tmp_cmp))
        if staleness_population != checkloopstate_population:
            failures.append(
                "receipts population MISMATCH between staleness.py and "
                f"check-loop-state.py: staleness={sorted(staleness_population)!r} "
                f"check-loop-state={sorted(checkloopstate_population)!r}"
            )
        if "receipts/verify/packets/plant.md" in staleness_population \
                or "receipts/verify/packets/plant.md" in checkloopstate_population:
            failures.append(
                "receipts/verify/packets/plant.md leaked into a receipts population "
                "(must be excluded from BOTH sensors)"
            )
    finally:
        shutil.rmtree(tmp_cmp, ignore_errors=True)

    # ---- (4) P6 extension: substrate-separation mechanical enforcement
    # (2026-07-25). Unit-level -- check_substrate_separation() takes only an
    # entry name + a meta dict, no tree/registration machinery needed.
    substrate_cases = []

    def sub_case(name, entry, meta, expect_violations, expect_warning_substrings):
        v, w = check_substrate_separation(entry, meta)
        ok = (len(v) == expect_violations) and all(
            any(sub in one_w for one_w in w) for sub in expect_warning_substrings
        ) and (len(w) >= len(expect_warning_substrings))
        substrate_cases.append(name)
        if not ok:
            failures.append(
                f"substrate-separation {name}: expected {expect_violations} "
                f"violation(s) + warnings containing {expect_warning_substrings!r}, "
                f"got violations={v!r} warnings={w!r}"
            )

    # C1: clean separation (different vendor AND family both hops) -> passes.
    sub_case(
        "C1-clean-separation-passes",
        "2026-07-25-clean",
        {
            "authored_by": "claude-opus-4-8-orchestrator-session",
            "answered_by": "gpt-5.5-pro-deep-research-with-web-search",
            "target_substrate": "cross-vendor required",
        },
        0, [],
    )

    # C2: same-family violation (opus vs opus), meta dated >= cutoff -> FAIL.
    sub_case(
        "C2-same-family-new-date-fails",
        "2026-07-25-same-family-new",
        {
            "authored": "2026-07-25",
            "authored_by": "claude-opus-4-8",
            "answered_by": "claude-opus-4-9-fresh-session",
            "target_substrate": "",
        },
        1, [],
    )

    # C3: same violation shape, meta dated before the cutoff -> WARN historical.
    sub_case(
        "C3-same-family-old-date-warns-historical",
        "2026-06-01-same-family-old",
        {
            "authored": "2026-06-01",
            "authored_by": "claude-opus-4-8",
            "answered_by": "claude-opus-4-9-fresh-session",
            "target_substrate": "",
        },
        0, ["historical"],
    )

    # C4: unparseable identifiers -> WARN naming the value, on BOTH sides,
    # even under a new date (never silently PASS, never FAIL on unparseable).
    sub_case(
        "C4-unparseable-warns-never-fails",
        "2026-07-25-unparseable",
        {
            "authored": "2026-07-25",
            "authored_by": "planning-session",
            "answered_by": "receiving-session",
            "target_substrate": "",
        },
        0, ["planning-session", "receiving-session"],
    )

    # C5: family differs (opus vs sonnet, same vendor) but target_substrate
    # demands cross-vendor -> vendor-level requirement is enforced -> FAIL.
    sub_case(
        "C5-vendor-level-required-enforced",
        "2026-07-25-vendor-required",
        {
            "authored": "2026-07-25",
            "authored_by": "claude-opus-4-8",
            "answered_by": "claude-sonnet-4-8",
            "target_substrate": "Cross-vendor separation REQUIRED: must NOT "
                                 "be answered by any Claude model",
        },
        1, [],
    )

    # C6: same family-differs-same-vendor shape as C5, but WITHOUT a
    # vendor-level target_substrate cue -> family-level minimum is satisfied
    # -> passes. Proves the vendor escalation in C5 is target-driven, not
    # unconditional.
    sub_case(
        "C6-family-level-sufficient-when-vendor-not-required",
        "2026-07-25-family-sufficient",
        {
            "authored": "2026-07-25",
            "authored_by": "claude-opus-4-8",
            "answered_by": "claude-sonnet-4-8",
            "target_substrate": "a different frontier model family",
        },
        0, [],
    )

    # C7: round 2 must differ from ALL prior rounds, not just round 1 --
    # round 1 (gpt) is a clean hop from the author (opus), but round 2
    # reverts to the SAME family as the AUTHOR (opus), which round 1 alone
    # would not catch.
    sub_case(
        "C7-checked-against-all-prior-rounds-not-just-immediate",
        "2026-07-25-reverts-to-author",
        {
            "authored": "2026-07-25",
            "authored_by": "claude-opus-4-8",
            "answered_by": [
                "round 1: gpt-5.5-pro-deep-research",
                "round 2: claude-opus-4-9-fresh-session",
            ],
            "target_substrate": "",
        },
        1, [],
    )

    # C8: a round explicitly self-labeled "NOT firewall-valid" (this corpus's
    # own vocabulary for an acknowledged same-family advisory pass -- see
    # 2026-06-14-v3-engine-licensing-and-firewall-merge) is skipped, even
    # though it would otherwise be a same-family violation against the author.
    sub_case(
        "C8-firewall-exempt-round-skipped",
        "2026-07-25-advisory-skipped",
        {
            "authored": "2026-07-25",
            "authored_by": "claude-opus-4-8",
            "answered_by": [
                "ADVISORY (2026-07-25, NOT firewall-valid): Claude Opus 4.8 "
                "same-family panel",
            ],
            "target_substrate": "",
        },
        0, [],
    )

    for f in failures:
        print(f"  {f}")
    if failures:
        print(f"RESULT: FAIL -- self-test, {len(failures)} unexpected outcome(s)")
        return 1
    n_cases = (
        len(base_cases) + len(tree_cases) + 9 + len(env_cases)
        + len(substrate_cases)
    )  # +2 fallback-fix, +2 harness-era, +1 receipts-population-parity (B-2),
       # +3 close-park marker cases (v3.0-78),
       # +1 doc-parity (meta shape vs HANDOFF-AUTHORING.md, D1 2026-08-05),
       # +len(env_cases) envelope-resolution cases (handoffs/ vs core/handoffs/),
       # +len(substrate_cases) P6 substrate-separation unit cases
    print(f"RESULT: PASS -- self-test, {n_cases} fixture case(s) behaved as expected")
    return 0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        import yaml
    except ImportError:
        print("RESULT: INCONCLUSIVE -- PyYAML not installed (pip install pyyaml)")
        return 2
    if "--self-test" in sys.argv:
        return run_self_test(yaml)
    root = None
    if "--root" in sys.argv:
        i = sys.argv.index("--root")
        if i + 1 >= len(sys.argv):
            print("RESULT: INCONCLUSIVE -- --root requires a directory")
            return 2
        root = os.path.abspath(sys.argv[i + 1])
        if not os.path.isdir(root):
            print(f"RESULT: INCONCLUSIVE -- --root is not a directory: {root}")
            return 2
    return run_live(yaml, root=root)


if __name__ == "__main__":
    sys.exit(main())
