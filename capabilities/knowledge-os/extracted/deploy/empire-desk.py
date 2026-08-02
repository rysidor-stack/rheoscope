#!/usr/bin/env python3
"""empire-desk.py -- the empire desk rollup (knowledge-os, v3.0-40, session D).

The operator runs several projects on this harness (the bottleneck-principle standard:
"Ryan runs 6-8 projects, only decisions reach him"). Each project already has its own desk
(DECISIONS-PENDING.md, SWEEP-BRIEFING.md, deploy/check-deadlines.py) -- this script is the
one place ABOVE all of them: it visits every project registered in the workspace's own
`projects.yaml` (session C's registry -- one registry, two consumers, the other being
deploy/check-workspace.py) and rolls their desks into a single `<root>/EMPIRE-DESK.md`.

GENERATED PROJECTION, never hand-edited -- exactly like decision-inbox.py's own output.
The ONLY file this script ever writes is `<root>/EMPIRE-DESK.md` (where <root> is the
resolved workspace root). It never touches any per-project file. --dry-run prints the
would-be content instead of writing it -- no file, not even EMPIRE-DESK.md, is touched
in that mode.

WORKSPACE-ROOT RESOLUTION -- copied verbatim (comment, not import) from
deploy/check-workspace.py's D3 resolution order (resolve_workspace_root /
_read_workspace_root_key). DO NOT DIVERGE: if that rule changes on check-workspace.py, the
copy below must change with it, same as the docstring there says.
  1. --root flag (explicit, wins outright)
  2. the `workspace_root:` key in ./project.yaml (this repo's own project.yaml; absence, an
     unreadable file, PyYAML being unavailable, or any other YAML weirdness all tolerate
     silently and fall through to step 3 -- never a crash)
  3. the parent directory of the repo (cwd's parent)

DEGRADATION -- same posture as check-workspace.py's reaper: if <root>/projects.yaml does
not exist, this prints exactly one NOTE line and exits 0. No EMPIRE-DESK.md is written in
that case (a workspace that never opted into governance gets a quiet note, not a rollup
file with nothing real in it).

When the registry exists: for each registered project whose path exists on disk, this
reads that project's DECISIONS-PENDING.md and SWEEP-BRIEFING.md (either or both absent ->
a per-project note, never a crash) and runs that project's own `deploy/check-deadlines.py
--json` as a subprocess with a timeout (the file being absent, the call failing, timing
out, or returning unparseable output all degrade to a per-project note, never a crash and
never abort the rest of the rollup). A registered project whose path does not exist at all
gets a single "path does not exist" note and is otherwise skipped.

EMPIRE-DESK.md layout, in order:
  1. "Waiting on you" -- every reachable project's DECISIONS-PENDING.md checkbox items,
     merged in registry order, each line tagged "[project-name]".
  2. one merged countdown -- every reachable project's check-deadlines.py rows in the
     `attention` or `fired` category, merged and sorted soonest-first (ascending
     days_remaining -- an overdue/fired row's days_remaining is negative, so the most
     overdue items surface first, then the closest upcoming ones), each line tagged
     "[project-name]".
  3. one section per registered project: its SWEEP-BRIEFING.md All-clear line + attention
     count, its DECISIONS-PENDING.md item count, its deadlines status, and any
     degrade/skip notes gathered along the way.
Plain English throughout -- no jargon, no unexplained abbreviations; a project name tag in
square brackets is the only bit of syntax on an otherwise ordinary sentence.

ACTIVATION: built in session D (backlog v3.0-40) and hermetically self-tested against
fixture projects here. Nothing in this repo schedules, imports, or invokes this script --
activation stays TRIGGER-GATED on >=2 additional live multi-project instances existing, per
the backlog entry recorded at landing. It is safe to leave sitting unused.

Usage:
  empire-desk.py [--root DIR] [--dry-run]
  empire-desk.py --self-test
Exit: 0 on any completed report (this is a reporting primitive, never a gate) | nonzero
  only from --self-test on failure.
"""

import json
import os
import re
import subprocess
import sys
from datetime import date

try:
    import yaml
except ImportError:  # pragma: no cover -- PyYAML is optional everywhere in this repo
    yaml = None

_HERE = os.path.dirname(os.path.abspath(__file__))

OUTPUT_NAME = "EMPIRE-DESK.md"
DECISIONS_NAME = "DECISIONS-PENDING.md"
SWEEP_NAME = "SWEEP-BRIEFING.md"

# Per-call cap for the check-deadlines.py subprocess -- a hung or pathologically slow
# per-project sensor degrades to a note instead of hanging the whole rollup, same doctrine
# as check-workspace.py's GIT_TIMEOUT_SECONDS for its git subprocess calls.
CHECK_DEADLINES_TIMEOUT_SECONDS = 15


# =============================================================================================
# Workspace-root resolution -- copied verbatim from check-workspace.py. DO NOT DIVERGE.
# =============================================================================================

def _read_workspace_root_key(project_yaml_path, repo_dir):
    """Best-effort read of `workspace_root:` from ./project.yaml. Tolerates absence, an
    unreadable file, PyYAML being unavailable, and any YAML weirdness -- always returns
    None (never raises) so the caller can fall through the resolution order. Copied from
    check-workspace.py's function of the same name -- see that file for the authoritative
    version; keep this one identical."""
    if yaml is None or not os.path.isfile(project_yaml_path):
        return None
    try:
        with open(project_yaml_path, "r", encoding="utf-8-sig") as fh:
            doc = yaml.safe_load(fh)
    except Exception:  # noqa: BLE001 -- deliberately broad: "any YAML weirdness" tolerates, never crashes
        return None
    if not isinstance(doc, dict):
        return None
    wr = doc.get("workspace_root")
    if not isinstance(wr, str) or not wr.strip():
        return None
    wr = wr.strip()
    return wr if os.path.isabs(wr) else os.path.normpath(os.path.join(repo_dir, wr))


def resolve_workspace_root(explicit_root, repo_dir):
    """Returns (root, source_label) per the D3 resolution order. Copied from
    check-workspace.py's function of the same name -- see that file for the authoritative
    version; keep this one identical."""
    if explicit_root:
        return explicit_root, "--root flag"
    project_yaml_path = os.path.join(repo_dir, "project.yaml")
    wr = _read_workspace_root_key(project_yaml_path, repo_dir)
    if wr:
        return wr, "project.yaml: workspace_root"
    return os.path.dirname(os.path.abspath(repo_dir)), "parent directory of the repo (default)"


def _resolve_registry_path(root, raw_path):
    """Mirrors check-workspace.py's helper of the same name: a registry entry's `path` is
    used as-is if absolute, else joined onto the workspace root."""
    if os.path.isabs(raw_path):
        return raw_path
    return os.path.join(root, raw_path)


# =============================================================================================
# Registry (projects.yaml) load -- same shape/degradation contract as check-workspace.py's
# load_registry, so the two consumers of one registry never disagree about what "malformed"
# means. Not imported (empire-desk.py has no other dependency on check-workspace.py), but
# deliberately mirrors that function's tolerances.
# =============================================================================================

def load_registry(root):
    """Returns (projects, problems, registry_present).
    projects: list of {name, path, role, notes} dicts (possibly empty).
    problems: list of finding strings (malformed entries, parse failures).
    registry_present: False only when <root>/projects.yaml does not exist at all -- the
    caller uses this to trigger the single-NOTE degradation path, same as the reaper."""
    path = os.path.join(root, "projects.yaml")
    if not os.path.isfile(path):
        return [], [], False

    problems = []
    if yaml is None:
        problems.append("PyYAML not installed -- cannot parse projects.yaml; "
                         "registry treated as empty (degrade)")
        return [], problems, True

    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            doc = yaml.safe_load(fh)
    except (OSError, UnicodeDecodeError) as e:
        problems.append("projects.yaml unreadable: %s" % e)
        return [], problems, True
    except Exception as e:  # yaml.YAMLError and anything else -- malformed file, not a crash
        problems.append("projects.yaml malformed YAML: %s" % e)
        return [], problems, True

    if not isinstance(doc, dict) or not isinstance(doc.get("projects"), list):
        problems.append("projects.yaml malformed: expected a top-level 'projects:' list")
        return [], problems, True

    projects = []
    for i, entry in enumerate(doc["projects"]):
        # Same malformed-entry criterion as check-workspace.py's load_registry (needs
        # name/path/role) -- the two consumers of one registry must never disagree about
        # what counts as a usable entry.
        if not isinstance(entry, dict) or not all(k in entry for k in ("name", "path", "role")):
            problems.append("projects.yaml entry #%d malformed (needs name/path/role): %r"
                             % (i, entry))
            continue
        name, entry_path, role = entry.get("name"), entry.get("path"), entry.get("role")
        # Type-check name/path/role as non-empty strings here, at load time -- a null or
        # numeric `path` (or name/role) must never reach downstream path handling (e.g.
        # os.path.isabs in _resolve_registry_path), where it would raise instead of
        # degrading. A violation becomes a per-entry registry note and the entry is
        # skipped, same posture as the presence check just above.
        if not all(isinstance(v, str) and v.strip() for v in (name, entry_path, role)):
            problems.append("projects.yaml entry #%d malformed (name/path/role must be "
                             "non-empty strings): %r" % (i, entry))
            continue
        projects.append({
            "name": name,
            "path": entry_path,
            "role": role,
            "notes": entry.get("notes", ""),
        })
    return projects, problems, True


# =============================================================================================
# Per-project reads: DECISIONS-PENDING.md / SWEEP-BRIEFING.md
# =============================================================================================

# A DECISIONS-PENDING.md checkbox item, e.g. "- [ ] Backlog v3.0-21: ... *(details: ...)*"
# -- see deploy/decision-inbox.py's own output shape. The trailing "(details: ...)" tail is
# kept intact so a merged item stays traceable back to its source.
_CHECKBOX_RE = re.compile(r"^-\s\[ \]\s+(.*)$")


def parse_decisions(path):
    """Every checkbox item in a project's DECISIONS-PENDING.md, in file order, regardless
    of which section it sits under (the merge only needs "what's pending", not which of
    decision-inbox.py's five source classes it came from). Returns None on any read
    failure (OSError/UnicodeDecodeError) -- never raises -- so the caller can tell "file
    exists but couldn't be read" apart from "file exists and genuinely has zero items"."""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return None
    items = []
    for ln in text.splitlines():
        m = _CHECKBOX_RE.match(ln)
        if m:
            items.append(m.group(1).strip())
    return items


# SWEEP-BRIEFING.md's contract (per /sweep SKILL.md "The briefing"): three sections in
# fixed order, an "**All clear:** ..." summary line, then a "**Needs your attention:**"
# heading with a numbered list. These regex shapes mirror deploy/decision-inbox.py's
# scan_sweep parsing (same heading/numbered-item shapes) -- kept as this script's own copy
# rather than an import, since this script has no other dependency on decision-inbox.py.
_ALL_CLEAR_RE = re.compile(r"^\*\*All clear:\*\*\s*(.*)$")
_ATTENTION_HEADING_RE = re.compile(
    r"^[#>\s]*\**\s*needs your attention\s*:?\s*\**\s*$", re.IGNORECASE)
_BOLD_HEADING_RE = re.compile(r"^\s*\*\*[^*]+\*\*\s*:?\s*$")
_MD_HEADING_RE = re.compile(r"^\s*#")
_NUMBERED_RE = re.compile(r"^\s*\d+\.\s+(.*)$")


def parse_sweep(path):
    """Returns {"all_clear": str-or-None, "attention_count": int}, or None on any read
    failure -- never raises. `all_clear` is the text of the first "**All clear:** ..."
    line found; `attention_count` counts numbered items under the "Needs your attention"
    heading, stopping at the next bold/markdown heading (same section-boundary rule as
    decision-inbox.py's scan_sweep)."""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return None
    all_clear = None
    attention_count = 0
    in_section = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if all_clear is None:
            m = _ALL_CLEAR_RE.match(stripped)
            if m:
                all_clear = m.group(1).strip()
        if not in_section:
            if _ATTENTION_HEADING_RE.match(stripped):
                in_section = True
            continue
        if _BOLD_HEADING_RE.match(raw) or _MD_HEADING_RE.match(raw):
            in_section = False
            continue
        if _NUMBERED_RE.match(raw):
            attention_count += 1
    return {"all_clear": all_clear, "attention_count": attention_count}


# =============================================================================================
# Per-project deadlines: run deploy/check-deadlines.py --json as a subprocess
# =============================================================================================

def run_check_deadlines(project_path):
    """Runs <project_path>/deploy/check-deadlines.py --json and returns
    {"rows": [...], "note": str-or-None}. `rows` holds only the 'attention'/'fired'
    category rows (the ones this rollup's countdown cares about). Every failure mode --
    the script being absent, the call erroring, timing out, returning non-zero, or
    printing unparseable JSON -- degrades to an explanatory note with empty rows. Never
    raises, never blocks the rest of the rollup."""
    script = os.path.join(project_path, "deploy", "check-deadlines.py")
    if not os.path.isfile(script):
        return {"rows": [],
                 "note": "deploy/check-deadlines.py not found -- deadlines skipped for this project"}
    try:
        proc = subprocess.run(
            [sys.executable, script, "--json"],
            cwd=project_path, capture_output=True, text=True,
            timeout=CHECK_DEADLINES_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"rows": [],
                 "note": "deploy/check-deadlines.py timed out after %ds -- deadlines skipped"
                          % CHECK_DEADLINES_TIMEOUT_SECONDS}
    except (OSError, subprocess.SubprocessError) as e:
        return {"rows": [],
                 "note": "deploy/check-deadlines.py could not be run (%s: %s) -- deadlines skipped"
                          % (type(e).__name__, e)}
    if proc.returncode != 0:
        return {"rows": [],
                 "note": "deploy/check-deadlines.py exited %d -- deadlines skipped for this project"
                          % proc.returncode}
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError) as e:
        return {"rows": [],
                 "note": "deploy/check-deadlines.py --json produced unparseable output (%s) -- "
                          "deadlines skipped" % e}
    if not isinstance(data, dict):
        return {"rows": [], "note": "deploy/check-deadlines.py --json output was not a report object "
                                      "-- deadlines skipped"}
    if data.get("degraded"):
        return {"rows": [], "note": data.get("note") or "deploy/check-deadlines.py reported no register"}
    rows = [r for r in data.get("rows", []) if isinstance(r, dict) and r.get("category") in ("attention", "fired")]
    return {"rows": rows, "note": None}


# =============================================================================================
# Per-project visit + report assembly
# =============================================================================================

def visit_project(root, proj):
    """Gathers everything for one registered project. Never raises -- every sub-step
    degrades to a note on failure (absent path, absent file, subprocess failure)."""
    name = proj["name"]
    path = _resolve_registry_path(root, proj["path"])
    result = {"name": name, "path": path, "notes": []}

    if not os.path.isdir(path):
        result["missing"] = True
        result["notes"].append("registered project path does not exist: %s -- skipped" % path)
        return result
    result["missing"] = False

    # os.path.exists (not os.path.isfile) on purpose: an existing-but-unreadable path --
    # including a directory sitting where a file is expected, which raises OSError on
    # open() rather than being silently absent -- must reach the read attempt below so its
    # failure gets a note, instead of being folded into the "not found" case and going
    # unmentioned.
    decisions_path = os.path.join(path, DECISIONS_NAME)
    if os.path.exists(decisions_path):
        parsed_decisions = parse_decisions(decisions_path)
        if parsed_decisions is None:
            result["decisions_items"] = []
            result["notes"].append("%s could not be read -- decisions section skipped for "
                                    "this project" % DECISIONS_NAME)
        else:
            result["decisions_items"] = parsed_decisions
    else:
        result["decisions_items"] = []
        result["notes"].append("%s not found -- decisions section skipped for this project"
                                % DECISIONS_NAME)

    sweep_path = os.path.join(path, SWEEP_NAME)
    if os.path.exists(sweep_path):
        result["sweep_summary"] = parse_sweep(sweep_path)
        if result["sweep_summary"] is None:
            result["notes"].append("%s could not be read -- briefing summary skipped"
                                    % SWEEP_NAME)
    else:
        result["sweep_summary"] = None
        result["notes"].append("%s not found -- briefing summary skipped for this project"
                                % SWEEP_NAME)

    result["deadlines"] = run_check_deadlines(path)
    return result


def build_report(root, now=None):
    now = now or date.today()
    report = {"root": root, "generated": now.isoformat()}

    projects, registry_problems, registry_present = load_registry(root)

    if not registry_present:
        report["degraded"] = True
        report["note"] = ("workspace governance not adopted here (no projects.yaml at %s) "
                           "-- skipping" % root)
        return report

    report["degraded"] = False
    report["registry_problems"] = registry_problems
    report["projects"] = [visit_project(root, p) for p in projects]
    return report


# =============================================================================================
# Rendering
# =============================================================================================

def render_markdown(report):
    lines = [
        "<!-- DERIVED PROJECTION -- regenerated by deploy/empire-desk.py; never hand-edit; "
        "each project's own DECISIONS-PENDING.md / SWEEP-BRIEFING.md / deadline register "
        "wins. Regenerated: %s. -->" % report["generated"],
        "",
    ]

    if report.get("degraded"):
        lines.append("# Empire desk")
        lines.append("")
        lines.append("NOTE -- %s" % report["note"])
        return "\n".join(lines) + "\n"

    projects = report["projects"]
    reachable = [p for p in projects if not p.get("missing")]
    missing = [p for p in projects if p.get("missing")]

    lines.append("# Empire desk -- %s" % report["generated"])
    lines.append("")
    lines.append("%d project(s) registered, %d reachable, %d registered but missing on disk."
                  % (len(projects), len(reachable), len(missing)))

    # ---- Section 1: Waiting on you (merged, registry order, tagged) --------------------
    lines.append("")
    lines.append("## Waiting on you")
    lines.append("")
    merged_items = []
    for p in reachable:
        for item in p.get("decisions_items", []):
            merged_items.append((p["name"], item))
    if merged_items:
        for name, item in merged_items:
            lines.append("- [%s] %s" % (name, item))
    else:
        lines.append("Nothing is waiting on you across the empire. All clear.")

    # ---- Section 2: merged countdown (attention + fired rows, soonest first) -----------
    lines.append("")
    lines.append("## Countdown -- deadlines needing attention or already overdue")
    lines.append("")
    merged_rows = []
    for p in reachable:
        for r in p.get("deadlines", {}).get("rows", []):
            merged_rows.append((p["name"], r))
    if merged_rows:
        # Soonest-first: ascending days_remaining -- an overdue/fired row's days_remaining
        # is negative, so the most overdue items lead, followed by the closest upcoming
        # ones. Same sort key check-deadlines.py itself uses within each of its own
        # sections; here it runs once across every project's rows together.
        merged_rows.sort(key=lambda t: t[1].get("days_remaining")
                          if t[1].get("days_remaining") is not None else 0)
        for name, r in merged_rows:
            days = r.get("days_remaining")
            what = r.get("what") or "(unnamed item)"
            when = r.get("when") or "(no date)"
            if r.get("category") == "fired":
                status = ("overdue by %d day(s)" % (-days)) if days is not None else "overdue"
            else:
                status = ("%d day(s) remaining" % days) if days is not None else "due soon"
            lines.append("- [%s] %s -- %s, due %s" % (name, what, status, when))
    else:
        lines.append("Nothing due or overdue across the empire right now.")

    # ---- Section 3: per-project detail ---------------------------------------------------
    lines.append("")
    lines.append("## Per-project detail")
    for p in projects:
        lines.append("")
        lines.append("### %s" % p["name"])
        if p.get("missing"):
            for n in p["notes"]:
                lines.append("- %s" % n)
            continue
        sweep = p.get("sweep_summary")
        if sweep:
            all_clear = sweep.get("all_clear") or "(no All-clear line found in the briefing)"
            lines.append("- Briefing: %s" % all_clear)
            lines.append("- Attention items in the last briefing: %d" % sweep.get("attention_count", 0))
        lines.append("- Waiting-on-you items: %d" % len(p.get("decisions_items", [])))
        dl = p.get("deadlines", {})
        if dl.get("note"):
            lines.append("- Deadlines: %s" % dl["note"])
        else:
            lines.append("- Deadlines: %d row(s) need attention or are overdue"
                          % len(dl.get("rows", [])))
        for n in p.get("notes", []):
            lines.append("- %s" % n)

    if report.get("registry_problems"):
        lines.append("")
        lines.append("## Registry problems")
        for msg in report["registry_problems"]:
            lines.append("- %s" % msg)

    return "\n".join(lines) + "\n"


# =============================================================================================
# CLI plumbing
# =============================================================================================

def do_report(root, dry_run, now=None):
    """now is passed straight through to build_report (see its docstring): None means
    date.today(), so a plain CLI invocation (main() never passes now=) is byte-for-byte
    unchanged. It exists only so a caller -- the self-test's Case B, specifically -- can
    pin the SAME date used for its own separate comparison build_report(...) call, instead
    of that comparison's pinned date silently drifting apart from this function's
    today()-based one across a real midnight rollover."""
    report = build_report(root, now=now)
    content = render_markdown(report)

    if report.get("degraded"):
        # Same posture as check-workspace.py's reaper degrade path: one NOTE, exit 0,
        # nothing written -- a workspace that never adopted governance gets a quiet note,
        # never a rollup file with nothing real to roll up.
        print("empire-desk: NOTE -- %s" % report["note"])
        return 0

    if dry_run:
        print(content)
        print("empire-desk: --dry-run -- would write %s" % os.path.join(root, OUTPUT_NAME))
        return 0

    out_path = os.path.join(root, OUTPUT_NAME)
    with open(out_path, "wb") as fh:
        fh.write(content.encode("utf-8"))
    print("empire-desk: wrote %s" % out_path)
    return 0


def main(argv):
    args = argv[1:]
    if "--self-test" in args:
        return self_test()

    dry_run = "--dry-run" in args
    explicit_root = None
    if "--root" in args:
        i = args.index("--root")
        if i + 1 < len(args):
            explicit_root = args[i + 1]

    repo_dir = os.getcwd()
    root, source = resolve_workspace_root(explicit_root, repo_dir)
    print("empire-desk: root=%s (resolved via %s)" % (root, source))
    return do_report(root, dry_run)


# =============================================================================================
# Self-test (hermetic)
# =============================================================================================

def self_test():
    import shutil
    import stat
    import tempfile

    total = failed = 0

    def rmtree_robust(path):
        """Best-effort recursive delete, tolerant of read-only leftovers (same convention
        as check-workspace.py's self-test cleanup) -- never allowed to crash or block the
        self-test itself."""
        def _onerror(func, p, _exc_info):
            try:
                os.chmod(p, stat.S_IWRITE)
                func(p)
            except Exception:  # noqa: BLE001 -- best-effort cleanup, never fatal
                pass
        shutil.rmtree(path, onerror=_onerror)

    def case(name, ok, detail=""):
        nonlocal total, failed
        total += 1
        status = "ok " if ok else "XX "
        print("  %s %s%s" % (status, name, ("  << " + str(detail)) if (not ok and detail) else ""))
        if not ok:
            failed += 1

    def write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    NOW = date(2026, 7, 23)

    # A fixture "check-deadlines.py" stand-in: prints fixed, deterministic JSON regardless
    # of args, so the self-test never depends on wall-clock date logic inside a real
    # check-deadlines.py -- it only needs to prove empire-desk.py subprocesses correctly,
    # parses the JSON shape, filters to attention/fired, and tolerates failure.
    _FIXTURE_DEADLINES_OK = (
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({\n"
        "    \"degraded\": False,\n"
        "    \"rows\": [\n"
        "        {\"what\": \"Fixture cert rotation\", \"category\": \"attention\", "
        "\"days_remaining\": 5, \"when\": \"2026-07-28\"},\n"
        "        {\"what\": \"Fixture overdue renewal\", \"category\": \"fired\", "
        "\"days_remaining\": -3, \"when\": \"2026-07-20\"},\n"
        "        {\"what\": \"Fixture far-off watching row\", \"category\": \"watching\", "
        "\"days_remaining\": 200, \"when\": \"2027-02-01\"}\n"
        "    ]\n"
        "}))\n"
    )
    _FIXTURE_DEADLINES_FAIL = (
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.exit(3)\n"
    )

    # ---- Case A: no projects.yaml at all -> single NOTE, degrade path, nothing written -
    base_a = tempfile.mkdtemp(prefix="empdsk-noreg-")
    try:
        rep = build_report(base_a, now=NOW)
        case("no-registry: degraded flag set", rep.get("degraded") is True)
        case("no-registry: note names 'not adopted here'",
             "not adopted here" in rep.get("note", ""), rep.get("note", ""))

        rc = do_report(base_a, dry_run=False)
        case("no-registry: do_report exits 0", rc == 0)
        case("no-registry: EMPIRE-DESK.md NOT written",
             not os.path.isfile(os.path.join(base_a, OUTPUT_NAME)))
    finally:
        shutil.rmtree(base_a, ignore_errors=True)

    # ---- Case B: full fixture workspace -- 3 registered projects -----------------------
    #   1. healthy-project   -- all-clear briefing, no decisions, no check-deadlines.py at
    #                           all (exercises the "not found" deadlines degrade note).
    #   2. busy-project      -- 2 waiting-on-you items, a briefing with 2 attention items,
    #                           and a fixture check-deadlines.py returning 1 attention + 1
    #                           fired row (+ 1 watching row that must NOT be merged in).
    #   3. ghost-project     -- registered, but its path does not exist on disk at all.
    base = tempfile.mkdtemp(prefix="empdsk-full-")
    try:
        # healthy-project
        healthy_dir = os.path.join(base, "healthy-project")
        os.makedirs(healthy_dir)
        write(os.path.join(healthy_dir, DECISIONS_NAME),
              "<!-- DERIVED PROJECTION -->\n\nNothing is waiting on you. All clear.\n")
        write(os.path.join(healthy_dir, SWEEP_NAME),
              "<!-- Generated by /sweep -->\n\n# Sweep briefing\n\n"
              "**All clear:** 5 checks ran clean, nothing to report.\n")

        # busy-project
        busy_dir = os.path.join(base, "busy-project")
        os.makedirs(busy_dir)
        write(os.path.join(busy_dir, DECISIONS_NAME), (
            "<!-- DERIVED PROJECTION -->\n\n"
            "# Waiting on you\n\n"
            "## Backlog items awaiting a decision\n"
            "- [ ] Backlog v9.9-1: Something needs a yes *(details: harness-backlog.md)*\n\n"
            "## Flight-plan blockers\n"
            "- [ ] Flight-plan blocker (widget): Waiting on a vendor contract "
            "*(details: wiki/flight-plans/widget.md)*\n"
        ))
        write(os.path.join(busy_dir, SWEEP_NAME), (
            "<!-- Generated by /sweep -->\n\n# Sweep briefing\n\n"
            "**All clear:** Most checks ran clean.\n\n"
            "**Needs your attention:**\n\n"
            "1. First attention item, a sentence about it.\n"
            "2. Second attention item, another sentence.\n\n"
            "**Watching:**\n"
            "- Nothing else to note.\n"
        ))
        deadlines_script = os.path.join(busy_dir, "deploy", "check-deadlines.py")
        write(deadlines_script, _FIXTURE_DEADLINES_OK)

        # ghost-project: registered, never actually checked out
        ghost_target = os.path.join(base, "ghost-project")  # deliberately never created

        write(os.path.join(base, "projects.yaml"), (
            "projects:\n"
            "- name: healthy-project\n"
            "  path: healthy-project\n"
            "  role: fork\n"
            "- name: busy-project\n"
            "  path: busy-project\n"
            "  role: fork\n"
            "- name: ghost-project\n"
            "  path: ghost-project\n"
            "  role: fork\n"
        ))

        rep = build_report(base, now=NOW)
        case("full workspace: not degraded", rep.get("degraded") is False)
        case("full workspace: 3 projects visited", len(rep["projects"]) == 3, rep["projects"])

        by_name = {p["name"]: p for p in rep["projects"]}
        case("healthy-project: reachable", by_name["healthy-project"]["missing"] is False)
        case("healthy-project: no decisions items", by_name["healthy-project"]["decisions_items"] == [])
        case("healthy-project: sweep all-clear text captured",
             by_name["healthy-project"]["sweep_summary"]["all_clear"]
             == "5 checks ran clean, nothing to report.",
             by_name["healthy-project"]["sweep_summary"])
        case("healthy-project: deadlines degrade note (check-deadlines.py absent)",
             "not found" in (by_name["healthy-project"]["deadlines"].get("note") or ""),
             by_name["healthy-project"]["deadlines"])
        case("healthy-project: deadlines rows empty on degrade",
             by_name["healthy-project"]["deadlines"]["rows"] == [])

        case("busy-project: reachable", by_name["busy-project"]["missing"] is False)
        case("busy-project: 2 decisions items in file order",
             by_name["busy-project"]["decisions_items"] ==
             ["Backlog v9.9-1: Something needs a yes *(details: harness-backlog.md)*",
              "Flight-plan blocker (widget): Waiting on a vendor contract "
              "*(details: wiki/flight-plans/widget.md)*"],
             by_name["busy-project"]["decisions_items"])
        case("busy-project: sweep attention_count == 2",
             by_name["busy-project"]["sweep_summary"]["attention_count"] == 2,
             by_name["busy-project"]["sweep_summary"])
        busy_rows = by_name["busy-project"]["deadlines"]["rows"]
        case("busy-project: deadlines rows == 2 (attention+fired only, watching excluded)",
             len(busy_rows) == 2, busy_rows)
        case("busy-project: no 'watching' category row leaked into merge set",
             all(r["category"] != "watching" for r in busy_rows), busy_rows)

        case("ghost-project: flagged missing", by_name["ghost-project"]["missing"] is True)
        case("ghost-project: note names the missing path",
             any("does not exist" in n for n in by_name["ghost-project"]["notes"]),
             by_name["ghost-project"]["notes"])
        case("ghost-project: no decisions_items key populated (never visited)",
             "decisions_items" not in by_name["ghost-project"])

        # ---- rendering: merge order, tagging, and section presence ---------------------
        content = render_markdown(rep)

        i_waiting = content.find("## Waiting on you")
        i_countdown = content.find("## Countdown")
        i_detail = content.find("## Per-project detail")
        case("sections appear in fixed order",
             i_waiting != -1 and i_countdown != -1 and i_detail != -1
             and i_waiting < i_countdown < i_detail, content)

        i_backlog_item = content.find("[busy-project] Backlog v9.9-1:")
        i_flightplan_item = content.find("[busy-project] Flight-plan blocker (widget):")
        case("merged waiting-on-you: both busy-project items tagged and present",
             i_backlog_item != -1 and i_flightplan_item != -1, content)
        case("merged waiting-on-you: backlog item appears before flight-plan item "
             "(file order preserved)",
             i_waiting < i_backlog_item < i_flightplan_item, content)
        case("merged waiting-on-you: healthy-project contributes nothing (it has no items)",
             "[healthy-project]" not in content[i_waiting:i_countdown], content)
        case("merged waiting-on-you: ghost-project (missing) contributes nothing",
             "[ghost-project]" not in content[i_waiting:i_countdown], content)

        countdown_block = content[i_countdown:i_detail]
        i_fired = countdown_block.find("[busy-project] Fixture overdue renewal")
        i_attn = countdown_block.find("[busy-project] Fixture cert rotation")
        case("countdown: both busy-project rows present, tagged",
             i_fired != -1 and i_attn != -1, countdown_block)
        case("countdown: soonest-first ordering (overdue/-3 days before attention/+5 days)",
             i_fired < i_attn, countdown_block)
        case("countdown: the watching-category row never appears",
             "Fixture far-off watching row" not in countdown_block, countdown_block)
        case("countdown: overdue phrasing names the day count",
             "overdue by 3 day(s)" in countdown_block, countdown_block)
        case("countdown: attention phrasing names the day count",
             "5 day(s) remaining" in countdown_block, countdown_block)

        detail_block = content[i_detail:]
        case("per-project detail: all three project names appear as headings",
             all(("### %s" % n) in detail_block
                 for n in ("healthy-project", "busy-project", "ghost-project")),
             detail_block)
        case("per-project detail: ghost-project section only carries its missing-path note",
             "does not exist" in detail_block[detail_block.find("### ghost-project"):], detail_block)
        case("per-project detail: healthy-project names its deadlines degrade note",
             "check-deadlines.py not found" in detail_block, detail_block)
        case("per-project detail: registry order preserved (healthy, busy, ghost)",
             detail_block.find("### healthy-project") < detail_block.find("### busy-project")
             < detail_block.find("### ghost-project"), detail_block)

        # ---- --dry-run writes nothing; a real run writes exactly one file --------------
        # now=NOW is pinned on BOTH of these calls to match the `rep`/`content` built
        # above (also pinned to NOW) -- without it, do_report's internal build_report
        # would fall back to date.today() and the "written content matches
        # render_markdown output" check below would flake on any day other than NOW
        # (a real hermeticity gap: it only ever passed on its authoring day).
        before = set(os.listdir(base))
        rc_dry = do_report(base, dry_run=True, now=NOW)
        after_dry = set(os.listdir(base))
        case("--dry-run: exits 0", rc_dry == 0)
        case("--dry-run: writes nothing at all", after_dry == before, after_dry - before)

        rc_real = do_report(base, dry_run=False, now=NOW)
        after_real = set(os.listdir(base))
        case("real run: exits 0", rc_real == 0)
        case("real run: EMPIRE-DESK.md written", os.path.isfile(os.path.join(base, OUTPUT_NAME)))
        case("real run: the ONLY new file at the workspace root is EMPIRE-DESK.md",
             after_real - before == {OUTPUT_NAME}, after_real - before)

        with open(os.path.join(base, OUTPUT_NAME), "r", encoding="utf-8") as fh:
            written = fh.read()
        case("real run: written content matches render_markdown output", written == content, None)

    finally:
        rmtree_robust(base)

    # ---- Case C: check-deadlines.py present but exits non-zero -> a note, not a crash --
    base_c = tempfile.mkdtemp(prefix="empdsk-baddl-")
    try:
        proj_dir = os.path.join(base_c, "flaky-project")
        os.makedirs(proj_dir)
        write(os.path.join(proj_dir, "deploy", "check-deadlines.py"), _FIXTURE_DEADLINES_FAIL)
        write(os.path.join(base_c, "projects.yaml"),
              "projects:\n- name: flaky-project\n  path: flaky-project\n  role: fork\n")
        rep_c = build_report(base_c, now=NOW)
        dl = rep_c["projects"][0]["deadlines"]
        case("check-deadlines.py non-zero exit: rows empty, not a crash", dl["rows"] == [])
        case("check-deadlines.py non-zero exit: note names the exit code",
             "exited 3" in (dl.get("note") or ""), dl)
    finally:
        rmtree_robust(base_c)

    # ---- Case D: malformed registry entry (missing role) -> a finding, not a crash -----
    base_d = tempfile.mkdtemp(prefix="empdsk-badreg-")
    try:
        write(os.path.join(base_d, "projects.yaml"),
              "projects:\n- name: incomplete-entry\n  path: somewhere\n")
        rep_d = build_report(base_d, now=NOW)
        case("malformed registry entry: not degraded (still produces a report)",
             rep_d.get("degraded") is False)
        case("malformed registry entry: produced a registry problem",
             len(rep_d.get("registry_problems", [])) >= 1, rep_d.get("registry_problems"))
        case("malformed registry entry: zero projects visited, not a crash",
             rep_d["projects"] == [])
    finally:
        rmtree_robust(base_d)

    # ---- Case E: --root flag resolution wins outright -----------------------------------
    r2, src2 = resolve_workspace_root("/some/explicit/root", "/some/other/repo/dir")
    case("--root flag resolution wins",
         r2 == "/some/explicit/root" and src2 == "--root flag")

    # ---- Case F: JSON round-trip / no crash on the render path for a degraded report ---
    try:
        render_markdown({"degraded": True, "note": "test note", "generated": "2026-07-23"})
        render_ok = True
    except Exception as e:  # noqa: BLE001
        render_ok = False
        print("    degraded render raised: %s" % e)
    case("degraded report renders cleanly (no crash)", render_ok)

    # ---- Case G: registry entries with non-string `path` (null, numeric) -> per-entry ---
    # registry notes, entries skipped, run completes cleanly -- never a crash inside
    # downstream path handling (e.g. os.path.isabs, which raises TypeError on None or an
    # int). A well-formed sibling entry proves this is a per-entry skip, not an all-or-
    # nothing failure.
    base_g = tempfile.mkdtemp(prefix="empdsk-badpath-")
    try:
        good_dir = os.path.join(base_g, "good-project")
        os.makedirs(good_dir)
        write(os.path.join(base_g, "projects.yaml"), (
            "projects:\n"
            "- name: nullpath-project\n"
            "  path: null\n"
            "  role: fork\n"
            "- name: numericpath-project\n"
            "  path: 42\n"
            "  role: fork\n"
            "- name: good-project\n"
            "  path: good-project\n"
            "  role: fork\n"
        ))
        rep_g = build_report(base_g, now=NOW)
        case("non-string path: build_report completes without raising (not degraded)",
             rep_g.get("degraded") is False)
        case("non-string path: null-path entry produced a registry problem",
             any("nullpath-project" in msg for msg in rep_g.get("registry_problems", [])),
             rep_g.get("registry_problems"))
        case("non-string path: numeric-path entry produced a registry problem",
             any("numericpath-project" in msg for msg in rep_g.get("registry_problems", [])),
             rep_g.get("registry_problems"))
        g_names = [p["name"] for p in rep_g["projects"]]
        case("non-string path: only the well-formed entry was visited",
             g_names == ["good-project"], g_names)
    finally:
        rmtree_robust(base_g)

    # ---- Case H: existing-but-unreadable DECISIONS-PENDING.md -> a note, not a silent ----
    # zero. Simulated portably on Windows with a DIRECTORY named DECISIONS-PENDING.md,
    # which raises OSError (IsADirectoryError/PermissionError) on open() -- the same
    # failure shape a permission-denied or locked file would produce.
    base_h = tempfile.mkdtemp(prefix="empdsk-unreadable-")
    try:
        proj_dir = os.path.join(base_h, "locked-project")
        os.makedirs(os.path.join(proj_dir, DECISIONS_NAME))  # a directory, not a file
        write(os.path.join(base_h, "projects.yaml"),
              "projects:\n- name: locked-project\n  path: locked-project\n  role: fork\n")
        rep_h = build_report(base_h, now=NOW)
        locked = rep_h["projects"][0]
        case("unreadable DECISIONS-PENDING.md: no crash, report still produced",
             rep_h.get("degraded") is False)
        case("unreadable DECISIONS-PENDING.md: items degrade to empty, not a stale guess",
             locked["decisions_items"] == [])
        case("unreadable DECISIONS-PENDING.md: a note names the unreadable file",
             any("DECISIONS-PENDING.md could not be read" in n for n in locked["notes"]),
             locked["notes"])
        content_h = render_markdown(rep_h)
        case("unreadable DECISIONS-PENDING.md: the note surfaces in the rendered report",
             "DECISIONS-PENDING.md could not be read" in content_h, content_h)
    finally:
        rmtree_robust(base_h)

    # ---- Case I: registry entries with non-string `name`/`role` (numeric name, list --
    # role) -> per-entry registry notes, entries skipped, run completes cleanly -- same
    # type-check posture as Case G's non-string `path`, but exercising the other two
    # fields the isinstance(v, str) check covers together. A well-formed sibling entry
    # proves this is a per-entry skip, not an all-or-nothing failure.
    base_i = tempfile.mkdtemp(prefix="empdsk-badnamerole-")
    try:
        good_dir = os.path.join(base_i, "good-project")
        os.makedirs(good_dir)
        write(os.path.join(base_i, "projects.yaml"), (
            "projects:\n"
            "- name: 42\n"
            "  path: numeric-name-project\n"
            "  role: fork\n"
            "- name: list-role-project\n"
            "  path: list-role-project\n"
            "  role: [fork, extra]\n"
            "- name: good-project\n"
            "  path: good-project\n"
            "  role: fork\n"
        ))
        rep_i = build_report(base_i, now=NOW)
        case("non-string name/role: build_report completes without raising (not degraded)",
             rep_i.get("degraded") is False)
        case("non-string name/role: numeric-name entry produced a registry problem",
             any("entry #0 malformed" in msg for msg in rep_i.get("registry_problems", [])),
             rep_i.get("registry_problems"))
        case("non-string name/role: list-role entry produced a registry problem",
             any("entry #1 malformed" in msg for msg in rep_i.get("registry_problems", [])),
             rep_i.get("registry_problems"))
        i_names = [p["name"] for p in rep_i["projects"]]
        case("non-string name/role: only the well-formed entry was visited",
             i_names == ["good-project"], i_names)
    finally:
        rmtree_robust(base_i)

    # ---- Case J: existing-but-unreadable SWEEP-BRIEFING.md -> a note, not a silent -------
    # zero, same simulation as Case H (a DIRECTORY named SWEEP-BRIEFING.md raises OSError
    # on open()) but exercising parse_sweep's degrade path instead of parse_decisions'.
    base_j = tempfile.mkdtemp(prefix="empdsk-unreadable-sweep-")
    try:
        proj_dir = os.path.join(base_j, "locked-sweep-project")
        os.makedirs(os.path.join(proj_dir, SWEEP_NAME))  # a directory, not a file
        write(os.path.join(base_j, "projects.yaml"),
              "projects:\n- name: locked-sweep-project\n  path: locked-sweep-project\n"
              "  role: fork\n")
        rep_j = build_report(base_j, now=NOW)
        locked_j = rep_j["projects"][0]
        case("unreadable SWEEP-BRIEFING.md: no crash, report still produced",
             rep_j.get("degraded") is False)
        case("unreadable SWEEP-BRIEFING.md: sweep_summary degrades to None, not a stale guess",
             locked_j["sweep_summary"] is None)
        case("unreadable SWEEP-BRIEFING.md: a note names the unreadable file",
             any("SWEEP-BRIEFING.md could not be read" in n for n in locked_j["notes"]),
             locked_j["notes"])
        content_j = render_markdown(rep_j)
        case("unreadable SWEEP-BRIEFING.md: the note surfaces in the rendered report",
             "SWEEP-BRIEFING.md could not be read" in content_j, content_j)
    finally:
        rmtree_robust(base_j)

    print("empire-desk self-test: %s (%d/%d)"
          % ("PASS" if failed == 0 else "FAIL", total - failed, total))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
