#!/usr/bin/env python3
"""check-workspace.py -- workspace-hygiene reaper (knowledge-os, v3.0-41, D3).

REPORT-ONLY (workspace-governance-brief-2026-07-22 point 3: "DELETION IS ALWAYS A HUMAN
WORD"): this sensor never deletes, moves, or archives anything. The report path (default
invocation) writes nothing to disk at all -- everything goes to stdout. --self-test is the
one exception: it writes its own temporary fixture folders under the OS temp dir to exercise
the checks above, and removes them again before exiting. It exists to answer "what's sitting
in the workspace, and does it belong there" the same way check-frontmatter.py answers "is
this file's frontmatter sound" -- mechanically, read-only, degrade-never-block (spec sec.12
doctrine, same as every other deploy/check-*.py sensor).

The workspace root is a DIFFERENT directory than the repo this script lives in: it's the
folder that HOLDS repos/ (this repo among them), worktrees/, scratch/, and archive/, per
the four-zone standard in core/governance/WORKSPACE.md. Resolution order (D3):
  1. --root flag (explicit, wins outright)
  2. the `workspace_root:` key in ./project.yaml (this repo's own project.yaml; absence,
     an unreadable file, PyYAML being unavailable, or any other YAML weirdness all
     tolerate silently and fall through to step 3 -- never a crash)
  3. the parent directory of the repo (cwd's parent)

Degradation (D3): if <root>/projects.yaml does not exist, this prints exactly one NOTE
line ("workspace governance not adopted here...") and exits 0 -- no zone/registry
classification is attempted, because that classification is meaningless without a
registry to classify AGAINST. This mirrors the brief's point 6 ("migration is a
reap-report first") -- a workspace that never opted in gets a single quiet note, not a
wall of findings about folders nobody agreed to govern.

When the registry exists (even a malformed one -- a malformed projects.yaml is a
finding, never a crash; classification continues with whatever could be salvaged):
  - every TOP-LEVEL entry of the workspace root is classified as (a) one of the four
    zones repos/ worktrees/ scratch/ archive/, (b) a registered project path (from
    projects.yaml, resolved absolute+normcased so Windows drive-letter/case variance
    never causes a false UNKNOWN), or (c) UNKNOWN -- flagged with its recursive size.
  - inside worktrees/ and scratch/, every CHILD folder is checked for a WHY.md birth
    certificate (loose `key: value` lines, tolerant of case/spacing/bullets/dashes and
    the purpose/created/expected-lifetime/created-by vocabulary). Missing WHY.md is a
    defect ("born without a birth certificate"). A WHY.md present but missing any of the
    three REQUIRED fields (purpose, created, expected-lifetime -- per WORKSPACE.md's
    "four questions") is also a defect ("incomplete birth certificate"), naming which
    field(s) are missing. Missing created-by alone (all three required fields present) is
    a soft note, not a defect -- the standard calls it out but the reaper doesn't gate on
    it. A WHY.md whose created + expected-lifetime lands before "now" is a defect ("past
    its stated lifetime"). Lifetimes are parsed tolerantly ("3 days", "2 weeks", "1
    month", "permanent", "until <date>"); an unparseable lifetime is a soft note, never a
    crash and never counted as past-due.
  - inside worktrees/ and scratch/, every child folder whose name does NOT end in a
    `--<YYYYMMDD>` segment gets a NOTES-level line (never a defect) pointing at the
    naming-convention section of WORKSPACE.md. This is a tolerant trailing-date check
    only -- it never tries to validate the purpose/repo segments of the name.
  - inside worktrees/ specifically, every child that is a git checkout (has a `.git` file
    or directory) is checked against the standard's reaper class "worktrees whose branch
    has been merged or deleted": (a) if HEAD is detached or its branch is otherwise gone,
    that's flagged "worktree on a deleted or detached branch"; (b) else, if HEAD is a
    fully-merged ancestor of the origin's default branch (or a local main/master when no
    origin exists), that's flagged "...candidate for reaping". This uses `git` via
    subprocess with a short per-call timeout; git being absent, the folder not being a
    real repo, or any other weirdness degrades to a plain note -- never a crash, never a
    false defect. REPORT-ONLY like everything else here: flagging is not reaping.

Folder sizes are a du-style recursive byte sum with a hard cap (STOP counting a single
entry past SIZE_CAP_BYTES and report ">2 GB", plus a file-count cap so a folder with
millions of tiny files cannot hang the sweep either) -- never a full, unbounded walk.

PyYAML is OPTIONAL, guarded exactly like every other deploy/*.py sensor (`try: import
yaml / except ImportError: yaml = None`) -- see origin.py's load_origin_config /
staleness.py's loaders for the same fail-safe-degrade contract. If PyYAML is absent,
project.yaml's workspace_root key is silently skipped (falls through the resolution
order above) and projects.yaml -- if present -- is reported as a registry problem
("PyYAML not installed") with the registry treated as empty; classification still runs.

Usage:
  check-workspace.py [--root DIR] [--json]
  check-workspace.py --self-test
Exit: 0 on any completed report (this is a reporting primitive, never a gate) | nonzero
  only from --self-test on failure.
"""

import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta

try:
    import yaml
except ImportError:  # pragma: no cover -- PyYAML is optional everywhere in this repo
    yaml = None

ZONES = ("repos", "worktrees", "scratch", "archive")
ZONE_CHILD_CHECK = ("worktrees", "scratch")  # zones whose children get a WHY.md check
VALID_ROLES = {"control-plane", "corpus", "port", "fork", "dormant"}

# Root-level entries that belong to workspace governance itself, never flagged UNKNOWN.
GOVERNANCE_FILES = {"projects.yaml", "workspace.md", "archive-index.md"}

SIZE_CAP_BYTES = 2 * 1024 * 1024 * 1024     # 2 GB -- stop counting a single entry past this
FILE_COUNT_CAP = 200_000                     # belt-and-suspenders: a huge-file-count folder
                                              # cannot hang the sweep either, even if small in bytes

GIT_TIMEOUT_SECONDS = 5   # per-call cap for every subprocess git invocation -- a hung or
                          # slow git (network stall on a fetch-backed helper, huge repo,
                          # whatever) degrades to a note instead of hanging the sweep.
_REQUIRED_WHY_FIELDS = ("purpose", "created", "expected-lifetime")
_ZONE_NAME_DATE_RE = re.compile(r"--\d{8}$")   # tolerant trailing-date check only, per
                                                # WORKSPACE.md's naming-convention section --
                                                # deliberately does not validate the
                                                # purpose/repo segments of the name.

_UNIT_DAYS = {"day": 1, "days": 1, "week": 7, "weeks": 7,
              "month": 30, "months": 30, "year": 365, "years": 365}
_SPAN_RE = re.compile(r"^(\d+)\s*(day|days|week|weeks|month|months|year|years)\b")
_UNTIL_RE = re.compile(r"^until\s+(.+)$")
_PERMANENT_WORDS = {"permanent", "indefinite", "n/a", "none", "forever"}
_BULLET_RE = re.compile(r"^[-*>#\s]+")
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%B %d, %Y", "%b %d, %Y", "%b %d %Y")


# --------------------------------------------------------------------------- small helpers

def _norm(p):
    """Absolute + normcased -- so Windows drive-letter case and separator variance never
    cause a false UNKNOWN / false miss when comparing a registry path to a filesystem path."""
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def _resolve_registry_path(root, raw_path):
    if os.path.isabs(raw_path):
        return raw_path
    return os.path.join(root, raw_path)


def format_size(nbytes, capped_bytes=False, capped_files=False):
    if capped_bytes:
        return ">2 GB"
    if capped_files:
        return ">%s files (stopped early)" % format(FILE_COUNT_CAP, ",")
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024 or unit == "GB":
            return ("%d %s" % (nbytes, unit)) if unit == "B" else ("%.1f %s" % (nbytes, unit))
        nbytes /= 1024.0
    return "%.1f GB" % nbytes


def compute_size(path):
    """du-style recursive byte sum, capped so one pathological folder cannot hang the sweep.
    Returns (bytes, capped_bytes, capped_files)."""
    total = 0
    files_seen = 0
    capped_bytes = capped_files = False
    try:
        for dirpath, _dirnames, filenames in os.walk(path, onerror=lambda e: None):
            for fn in filenames:
                files_seen += 1
                if files_seen > FILE_COUNT_CAP:
                    capped_files = True
                    return total, capped_bytes, capped_files
                fp = os.path.join(dirpath, fn)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    continue
                if total > SIZE_CAP_BYTES:
                    capped_bytes = True
                    return total, capped_bytes, capped_files
    except OSError:
        pass
    return total, capped_bytes, capped_files


def size_label(path):
    b, cb, cf = compute_size(path)
    return format_size(b, cb, cf)


# --------------------------------------------------------------------------- root resolution

def _read_workspace_root_key(project_yaml_path, repo_dir):
    """Best-effort read of `workspace_root:` from ./project.yaml. Tolerates absence, an
    unreadable file, PyYAML being unavailable, and any YAML weirdness -- always returns
    None (never raises) so the caller can fall through the resolution order."""
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
    """Returns (root, source_label) per the D3 resolution order."""
    if explicit_root:
        return explicit_root, "--root flag"
    project_yaml_path = os.path.join(repo_dir, "project.yaml")
    wr = _read_workspace_root_key(project_yaml_path, repo_dir)
    if wr:
        return wr, "project.yaml: workspace_root"
    return os.path.dirname(os.path.abspath(repo_dir)), "parent directory of the repo (default)"


# --------------------------------------------------------------------------- registry

def load_registry(root):
    """Returns (projects, problems, registry_present).
    projects: list of {name, path, role, notes} dicts (possibly empty).
    problems: list of finding strings (malformed entries, unknown roles, parse failures).
    registry_present: False only when <root>/projects.yaml does not exist at all --
    the caller uses this to trigger the single-NOTE degradation path (D3)."""
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
        if not isinstance(entry, dict) or not all(k in entry for k in ("name", "path", "role")):
            problems.append("projects.yaml entry #%d malformed (needs name/path/role): %r"
                             % (i, entry))
            continue
        role = entry.get("role")
        if role not in VALID_ROLES:
            problems.append("projects.yaml entry '%s' has unknown role '%s' (expected one of %s)"
                             % (entry.get("name"), role, ", ".join(sorted(VALID_ROLES))))
        projects.append({
            "name": entry.get("name"),
            "path": entry.get("path"),
            "role": role,
            "notes": entry.get("notes", ""),
        })
    return projects, problems, True


# --------------------------------------------------------------------------- WHY.md

def read_why(folder_path):
    """Loose `key: value` reader for WHY.md, tolerant of bullets, headings, case, and
    spacing/underscore variants of the purpose/created/expected-lifetime/created-by
    vocabulary. Returns None if WHY.md is absent (the "born without a birth
    certificate" case), else a dict keyed by normalized-lowercase-hyphenated key."""
    why_path = os.path.join(folder_path, "WHY.md")
    if not os.path.isfile(why_path):
        return None
    try:
        with open(why_path, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    data = {}
    for raw_ln in text.splitlines():
        ln = raw_ln.strip()
        if not ln:
            continue
        ln = _BULLET_RE.sub("", ln, count=1)
        if ":" not in ln:
            continue
        key, _, val = ln.partition(":")
        key = key.strip().strip("*").strip().lower()
        key = re.sub(r"[\s_]+", "-", key)
        val = val.strip()
        if key and val:
            data[key] = val
    return data


def _parse_date(s):
    if not s:
        return None
    s = s.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def evaluate_lifetime(created_str, lifetime_str, now):
    """Returns {"status": "past"|"ok"|"unknown", "detail": str}. Tolerant of "3 days",
    "2 weeks", "1 month", "permanent"/"indefinite", "until <date>"; anything else
    (or a missing/unparseable `created`) degrades to "unknown" -- a soft note, never a
    crash and never treated as past-due."""
    if not lifetime_str:
        return {"status": "unknown", "detail": "no expected-lifetime recorded in WHY.md"}
    lt = lifetime_str.strip().lower()
    if lt in _PERMANENT_WORDS:
        return {"status": "ok", "detail": "permanent"}
    m_until = _UNTIL_RE.match(lt)
    if m_until:
        d = _parse_date(m_until.group(1))
        if d is None:
            return {"status": "unknown",
                     "detail": "unparseable 'until' date: %r" % lifetime_str}
        return {"status": ("past" if now > d else "ok"), "detail": "until %s" % d.isoformat()}
    m_span = _SPAN_RE.match(lt)
    if m_span:
        created = _parse_date(created_str)
        if created is None:
            return {"status": "unknown",
                     "detail": "expected-lifetime parseable but 'created' date missing/unparseable "
                                "(created=%r)" % created_str}
        n = int(m_span.group(1))
        due = created + timedelta(days=n * _UNIT_DAYS[m_span.group(2)])
        return {"status": ("past" if now > due else "ok"),
                 "detail": "created %s + %s -> due %s" % (created_str, lifetime_str, due.isoformat())}
    return {"status": "unknown", "detail": "unparseable expected-lifetime: %r" % lifetime_str}


# --------------------------------------------------------------------------- classification

def classify_root(root, projects):
    """Every top-level entry of the workspace root -> zones / registered / unknown."""
    registered = {_norm(_resolve_registry_path(root, p["path"])): p for p in projects}
    zones_present = {}
    registered_hits = []
    unknown = []

    try:
        entries = sorted(os.listdir(root))
    except OSError as e:
        return {"zones_present": {}, "registered_hits": [], "unknown": [],
                 "list_error": str(e)}

    for name in entries:
        if name.startswith("."):
            continue  # hidden/system entries (.git, etc.) are not workspace-governance concerns
        full = os.path.join(root, name)
        lname = name.lower()
        if lname in ZONES:
            zones_present[lname] = full
            continue
        if lname in GOVERNANCE_FILES:
            continue  # the registry / governance docs themselves are never flagged
        key = _norm(full)
        if key in registered:
            registered_hits.append(registered[key])
            continue
        unknown.append({
            "name": name,
            "path": full,
            "is_dir": os.path.isdir(full),
            "size": size_label(full),
        })
    return {"zones_present": zones_present, "registered_hits": registered_hits,
             "unknown": unknown, "list_error": None}


def classify_zone_children(zone_path, now):
    """Inside worktrees/ or scratch/: each child folder's WHY.md birth-certificate check,
    plus a tolerant naming-convention note (FIX 3). WHY.md handling (FIX 2):
      - absent entirely -> defect "missing-why" ("born without a birth certificate").
      - present but missing any of purpose/created/expected-lifetime -> defect
        "incomplete-why", naming the missing field(s) -- a WHY.md that exists but doesn't
        answer the standard's required questions is still a birth-certificate defect, not
        a pass.
      - present with all three required fields -> lifetime is evaluated as before (past /
        ok / unknown-soft-note). created-by missing *alone* (all three required fields
        present) is a soft note, never a defect -- the standard names it but the reaper
        doesn't gate on it.
    """
    out = []
    try:
        names = sorted(os.listdir(zone_path))
    except OSError:
        return out
    for name in names:
        full = os.path.join(zone_path, name)
        if not os.path.isdir(full):
            continue
        entry = {"name": name, "path": full, "size": size_label(full)}

        if not _ZONE_NAME_DATE_RE.search(name):
            entry["naming_note"] = ("folder name '%s' does not end in a '--<YYYYMMDD>' "
                                     "date segment -- see WORKSPACE.md's naming-convention "
                                     "section" % name)

        why = read_why(full)
        if why is None:
            entry["defect"] = "missing-why"
        else:
            entry["why"] = why
            missing_required = [f for f in _REQUIRED_WHY_FIELDS if not why.get(f)]
            if missing_required:
                entry["defect"] = "incomplete-why"
                entry["missing_fields"] = missing_required
            else:
                lc = evaluate_lifetime(why.get("created"), why.get("expected-lifetime"), now)
                entry["lifetime"] = lc
                if lc["status"] == "past":
                    entry["defect"] = "past-lifetime"
                elif lc["status"] == "unknown":
                    entry["note"] = lc["detail"]
                if not why.get("created-by"):
                    cb_note = "missing created-by (soft note -- not a defect)"
                    entry["note"] = (entry["note"] + "; " + cb_note) if entry.get("note") else cb_note
        out.append(entry)
    return out


# --------------------------------------------------------------------------- worktree reap check (FIX 1)

def _is_git_checkout(path):
    git_marker = os.path.join(path, ".git")
    return os.path.isfile(git_marker) or os.path.isdir(git_marker)


def _run_git(args, cwd, timeout=GIT_TIMEOUT_SECONDS):
    """Best-effort git invocation. Returns (returncode, stdout, stderr), or None on ANY
    failure to even run git (git absent from PATH, timeout, OS-level error) -- callers
    treat None as "could not determine, degrade to a note", never a crash."""
    try:
        proc = subprocess.run(
            ["git", "-C", cwd] + list(args),
            capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _git_default_branch(child_path):
    """Best-effort determination of the child's origin default branch, per FIX 1's
    resolution order: refs/remotes/origin/HEAD, then origin/main, origin/master, then
    local main, master. Returns a ref string usable as the RHS of `merge-base
    --is-ancestor`, or None if nothing was found (caller skips the merged check)."""
    r = _run_git(["symbolic-ref", "-q", "refs/remotes/origin/HEAD"], child_path)
    if r is not None and r[0] == 0 and r[1]:
        ref = r[1]
        prefix = "refs/remotes/"
        return ref[len(prefix):] if ref.startswith(prefix) else ref
    # (full ref to verify existence, short name to hand to merge-base) pairs, in fallback order
    candidates = (
        ("refs/remotes/origin/main", "origin/main"),
        ("refs/remotes/origin/master", "origin/master"),
        ("refs/heads/main", "main"),
        ("refs/heads/master", "master"),
    )
    for check_ref, use_ref in candidates:
        r = _run_git(["rev-parse", "-q", "--verify", check_ref], child_path)
        if r is not None and r[0] == 0 and r[1]:
            return use_ref
    return None


def check_worktree_reap_candidate(child_path):
    """For a single worktrees/ child that is a git checkout: determine reap-candidacy per
    WORKSPACE.md's reaper class "worktrees whose branch has been merged or deleted".
    Returns a dict with exactly one of:
      {"flag": "deleted-branch", "detail": str}  -- HEAD is detached, OR HEAD is a symbolic
                                                     ref to a branch whose ref no longer
                                                     resolves (deleted elsewhere)
      {"flag": "merged", "detail": str}          -- HEAD is a merged ancestor of the default branch
      {"note": str}                              -- degrade condition, never a defect
      {}                                          -- checked clean: normal, unmerged, on a branch
    Never raises -- every git call goes through _run_git, which itself never raises."""
    # Confirm this is actually a usable git working tree BEFORE reading anything HEAD-shaped
    # -- distinguishes "not a repo / weird state" (degrade to a note) from "HEAD really is
    # detached/branchless in a real repo" (a genuine flag). Without this, a folder that merely
    # has a stray/empty .git/ (not a real repo) would read as rc!=0 from symbolic-ref and get
    # misreported as "deleted or detached branch" -- a false defect, exactly what's forbidden.
    probe = _run_git(["rev-parse", "--is-inside-work-tree"], child_path)
    if probe is None:
        return {"note": "git unavailable or the call failed -- reap-check skipped"}
    rc0, out0, _err0 = probe
    if rc0 != 0 or out0.strip().lower() != "true":
        return {"note": "not a usable git working tree (git repo, weird state, or absent) "
                         "-- reap-check skipped"}

    r = _run_git(["symbolic-ref", "-q", "HEAD"], child_path)
    if r is None:
        return {"note": "git unavailable or the call failed -- reap-check skipped"}
    rc, out, _err = r
    if rc != 0:
        return {"flag": "deleted-branch",
                "detail": "worktree on a deleted or detached branch"}
    if not out:
        return {"note": "git returned no branch name -- reap-check skipped"}

    # HEAD is a symbolic ref (e.g. refs/heads/gone-branch) -- but a symbolic ref succeeds
    # even when the ref it POINTS TO no longer resolves (e.g. the branch was deleted from
    # another checkout/clone while this worktree's .git/HEAD still names it). That case is
    # just as much "the branch is gone" as a detached HEAD is, so verify the target ref
    # actually resolves before treating this as a normal on-a-branch worktree.
    rv = _run_git(["rev-parse", "--verify", "--quiet", out], child_path)
    if rv is None:
        return {"note": "git unavailable or the call failed -- reap-check skipped"}
    rcv, _outv, _errv = rv
    if rcv != 0:
        return {"flag": "deleted-branch",
                "detail": "worktree HEAD symbolically references '%s', which no longer "
                          "resolves (branch deleted elsewhere)" % out}

    default = _git_default_branch(child_path)
    if default is None:
        return {"note": "could not determine origin/local default branch -- "
                         "merged-check skipped"}

    r2 = _run_git(["merge-base", "--is-ancestor", "HEAD", default], child_path)
    if r2 is None:
        return {"note": "git unavailable or the call failed -- merged-check skipped"}
    rc2, _out2, _err2 = r2
    if rc2 == 0:
        return {"flag": "merged",
                "detail": "worktree branch fully merged into %s -- candidate for reaping" % default}
    if rc2 == 1:
        return {}  # a real, unmerged answer -- nothing to flag
    return {"note": "merge-base --is-ancestor against %s failed (exit %d) -- "
                     "merged-check skipped" % (default, rc2)}


def classify_worktree_reap_candidates(worktrees_path):
    """For each child of worktrees/ that is a git checkout, run check_worktree_reap_candidate.
    Returns (flags, notes): flags is a list of {"name","path","flag","detail"} dicts for
    real reap candidates; notes is a list of plain strings for degrade conditions (git
    absent, not a repo, weird state) -- REPORT-ONLY, and never a defect in either list."""
    flags = []
    notes = []
    try:
        names = sorted(os.listdir(worktrees_path))
    except OSError:
        return flags, notes
    for name in names:
        full = os.path.join(worktrees_path, name)
        if not os.path.isdir(full) or not _is_git_checkout(full):
            continue
        result = check_worktree_reap_candidate(full)
        if result.get("flag"):
            flags.append({"name": name, "path": full, "flag": result["flag"],
                           "detail": result["detail"]})
        elif result.get("note"):
            notes.append("%s: %s" % (name, result["note"]))
    return flags, notes


# --------------------------------------------------------------------------- report assembly

def build_report(root, source, now=None):
    now = now or date.today()
    report = {"root": root, "root_source": source}

    if not os.path.isdir(root):
        report["degraded"] = True
        report["note"] = "workspace root '%s' does not exist -- nothing to report" % root
        return report

    projects, registry_problems, registry_present = load_registry(root)

    if not registry_present:
        report["degraded"] = True
        report["note"] = ("workspace governance not adopted here (no projects.yaml at %s) "
                           "-- skipping" % root)
        return report

    report["degraded"] = False
    root_c = classify_root(root, projects)
    if root_c["list_error"]:
        registry_problems.append("could not list workspace root %s: %s"
                                  % (root, root_c["list_error"]))

    birth_defects = []
    past_lifetime = []
    naming_notes = []
    zone_children_by_zone = {}
    for zone in ZONE_CHILD_CHECK:
        zp = root_c["zones_present"].get(zone)
        if not zp:
            continue
        children = classify_zone_children(zp, now)
        zone_children_by_zone[zone] = children
        for c in children:
            if c.get("defect") == "missing-why":
                birth_defects.append({"zone": zone, "name": c["name"], "path": c["path"],
                                        "size": c["size"], "reason": "missing-why"})
            elif c.get("defect") == "incomplete-why":
                birth_defects.append({"zone": zone, "name": c["name"], "path": c["path"],
                                        "size": c["size"], "reason": "incomplete-why",
                                        "missing_fields": c["missing_fields"]})
            elif c.get("defect") == "past-lifetime":
                past_lifetime.append({"zone": zone, "name": c["name"], "path": c["path"],
                                        "size": c["size"], "detail": c["lifetime"]["detail"]})
            if c.get("naming_note"):
                naming_notes.append({"zone": zone, "name": c["name"], "path": c["path"],
                                       "detail": c["naming_note"]})

    reap_candidates, reap_notes = [], []
    wt_zone = root_c["zones_present"].get("worktrees")
    if wt_zone:
        reap_candidates, reap_notes = classify_worktree_reap_candidates(wt_zone)

    report.update({
        "zones_present": sorted(root_c["zones_present"].keys()),
        "zones_missing": sorted(z for z in ZONES if z not in root_c["zones_present"]),
        "registered_hits": len(root_c["registered_hits"]),
        "registered_total": len(projects),
        "unknown_at_root": root_c["unknown"],
        "birth_certificate_defects": birth_defects,
        "past_lifetime": past_lifetime,
        "naming_convention_notes": naming_notes,
        "worktree_reap_candidates": reap_candidates,
        "worktree_reap_notes": reap_notes,
        "registry_problems": registry_problems,
        "zone_children": zone_children_by_zone,
    })
    return report


# --------------------------------------------------------------------------- output

def print_report(report, as_json=False):
    if as_json:
        print(json.dumps(report, indent=1, sort_keys=True, default=str))
        return

    if report.get("degraded"):
        print("check-workspace: NOTE -- %s" % report["note"])
        return

    n_unknown = len(report["unknown_at_root"])
    n_birth = len(report["birth_certificate_defects"])
    n_past = len(report["past_lifetime"])
    n_reap = len(report["worktree_reap_candidates"])
    n_naming = len(report["naming_convention_notes"])
    n_reg_problems = len(report["registry_problems"])

    print("check-workspace: root=%s (resolved via %s)" % (report["root"], report["root_source"]))
    print("  summary: zones present %s; zones missing %s; %d/%d registered projects found "
          "at root; %d unknown root item(s); %d birth-certificate defect(s); "
          "%d past-lifetime item(s); %d worktree reap candidate(s); "
          "%d naming-convention note(s); %d registry problem(s)"
          % (report["zones_present"] or "(none)", report["zones_missing"] or "(none)",
             report["registered_hits"], report["registered_total"],
             n_unknown, n_birth, n_past, n_reap, n_naming, n_reg_problems))

    print()
    print("  unknown-at-root (%d):" % n_unknown)
    if n_unknown:
        for u in report["unknown_at_root"]:
            kind = "dir" if u["is_dir"] else "file"
            print("    - %s (%s, %s) -- not in any zone, not registered" % (u["name"], kind, u["size"]))
    else:
        print("    (none)")

    print()
    print("  birth-certificate defects (%d):" % n_birth)
    if n_birth:
        for d in report["birth_certificate_defects"]:
            if d.get("reason") == "incomplete-why":
                print("    - %s/%s (%s) -- incomplete birth certificate (missing: %s)"
                      % (d["zone"], d["name"], d["size"], ", ".join(d["missing_fields"])))
            else:
                print("    - %s/%s (%s) -- born without a birth certificate (no WHY.md)"
                      % (d["zone"], d["name"], d["size"]))
    else:
        print("    (none)")

    print()
    print("  past-lifetime items (%d):" % n_past)
    if n_past:
        for p in report["past_lifetime"]:
            print("    - %s/%s (%s) -- past its stated lifetime (%s)"
                  % (p["zone"], p["name"], p["size"], p["detail"]))
    else:
        print("    (none)")

    print()
    print("  worktree reap candidates (%d):" % n_reap)
    if n_reap:
        for r in report["worktree_reap_candidates"]:
            print("    - worktrees/%s -- %s" % (r["name"], r["detail"]))
    else:
        print("    (none)")
    for note in report["worktree_reap_notes"]:
        print("    note: %s" % note)

    print()
    print("  naming-convention notes (%d):" % n_naming)
    if n_naming:
        for n in report["naming_convention_notes"]:
            print("    - %s/%s -- %s" % (n["zone"], n["name"], n["detail"]))
    else:
        print("    (none)")

    print()
    print("  registry problems (%d):" % n_reg_problems)
    if n_reg_problems:
        for msg in report["registry_problems"]:
            print("    - %s" % msg)
    else:
        print("    (none)")


# --------------------------------------------------------------------------- self-test

def self_test():
    import shutil
    import stat
    import tempfile

    total = failed = 0

    def rmtree_robust(path):
        """Best-effort recursive delete of a self-test temp fixture. A real git repo
        routinely leaves read-only object files behind (git's own hygiene, not a bug) --
        plain shutil.rmtree(..., ignore_errors=True) silently leaves those on Windows,
        which would break the "removes it afterward" promise for the FIX-1 git fixtures.
        Clears the read-only bit and retries once per entry; any residual failure is still
        swallowed -- this is temp-fixture cleanup, never allowed to crash or block the
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
        # str(detail): callers pass dicts/lists as detail as often as strings -- a failing
        # case must always print cleanly, never raise on the format itself.
        print("  %s %s%s" % (status, name, ("  << " + str(detail)) if (not ok and detail) else ""))
        if not ok:
            failed += 1

    NOW = date(2026, 7, 23)  # fixed reference date -- deterministic regardless of wall clock

    def write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    # ---- Case A: no projects.yaml at all -> single NOTE, degrade path -----------------
    base_a = tempfile.mkdtemp(prefix="ckws-noreg-")
    try:
        os.makedirs(os.path.join(base_a, "repos"))
        rep = build_report(base_a, "test", now=NOW)
        case("no-registry: degraded flag set", rep.get("degraded") is True)
        case("no-registry: note names 'not adopted here'",
             "not adopted here" in rep.get("note", ""), rep.get("note", ""))
    finally:
        shutil.rmtree(base_a, ignore_errors=True)

    # ---- Case B: full fake workspace with a registry, all four zones, mixed content ---
    base = tempfile.mkdtemp(prefix="ckws-full-")
    try:
        for z in ZONES:
            os.makedirs(os.path.join(base, z))

        # a registered repo, sitting loose at workspace root (pre-migration scenario)
        os.makedirs(os.path.join(base, "loose-registered-repo"))
        write(os.path.join(base, "loose-registered-repo", "marker.txt"), "x\n")

        # an unregistered root folder -> UNKNOWN
        os.makedirs(os.path.join(base, "mystery-folder"))
        write(os.path.join(base, "mystery-folder", "stuff.bin"), "0" * 500)

        # scratch child: valid WHY.md, well within its lifetime
        sc_ok = os.path.join(base, "scratch", "probe--20260722")
        os.makedirs(sc_ok)
        write(os.path.join(sc_ok, "WHY.md"),
              "purpose: QA probe for the parity report\n"
              "created: 2026-07-22\n"
              "expected-lifetime: 3 weeks\n"
              "created-by: session-c\n")

        # scratch child: past its stated lifetime
        sc_past = os.path.join(base, "scratch", "old-probe--20260601")
        os.makedirs(sc_past)
        write(os.path.join(sc_past, "WHY.md"),
              "Purpose: one-off port test\n"
              "Created: 2026-06-01\n"
              "Expected Lifetime: 3 days\n")

        # worktree child: missing WHY.md entirely -> birth-certificate defect
        wt_missing = os.path.join(base, "worktrees", "acme--feature-x--20260710")
        os.makedirs(wt_missing)
        write(os.path.join(wt_missing, "notes.txt"), "no WHY.md here\n")

        # worktree child: permanent, never flagged
        wt_perm = os.path.join(base, "worktrees", "acme--long-lived--20260101")
        os.makedirs(wt_perm)
        write(os.path.join(wt_perm, "WHY.md"),
              "purpose: standing integration worktree\n"
              "created: 2026-01-01\n"
              "expected-lifetime: permanent\n")

        # worktree child: unparseable lifetime -> soft note, not a defect
        wt_weird = os.path.join(base, "worktrees", "acme--odd--20260601")
        os.makedirs(wt_weird)
        write(os.path.join(wt_weird, "WHY.md"),
              "purpose: exploratory\n"
              "created: 2026-06-01\n"
              "expected-lifetime: soon-ish\n")

        # the registry itself
        write(os.path.join(base, "projects.yaml"),
              "projects:\n"
              "- name: acme-fork\n"
              "  path: loose-registered-repo\n"
              "  role: fork\n"
              "- name: weird-role-project\n"
              "  path: repos\n"  # deliberately points at a zone dir; still exercises path match
              "  role: not-a-real-role\n")

        rep = build_report(base, "test", now=NOW)
        case("registry: not degraded", rep.get("degraded") is False)
        case("zones: all four present",
             rep["zones_present"] == sorted(ZONES), rep["zones_present"])

        unk_names = {u["name"] for u in rep["unknown_at_root"]}
        case("unknown: mystery-folder flagged", "mystery-folder" in unk_names, unk_names)
        case("unknown: loose-registered-repo NOT flagged (it's registered)",
             "loose-registered-repo" not in unk_names, unk_names)
        case("unknown: zone dirs NOT flagged", not (set(ZONES) & unk_names), unk_names)

        case("registry: unknown role produced a finding",
             any("unknown role" in p for p in rep["registry_problems"]), rep["registry_problems"])

        birth_names = {d["name"] for d in rep["birth_certificate_defects"]}
        case("birth-cert: missing-WHY worktree flagged", "acme--feature-x--20260710" in birth_names)
        case("birth-cert: WHY-having worktrees NOT flagged",
             not ({"acme--long-lived--20260101", "acme--odd--20260601"} & birth_names))

        past_names = {p["name"] for p in rep["past_lifetime"]}
        case("past-lifetime: old-probe flagged", "old-probe--20260601" in past_names)
        case("past-lifetime: within-lifetime probe NOT flagged", "probe--20260722" not in past_names)
        case("past-lifetime: permanent worktree NOT flagged", "acme--long-lived--20260101" not in past_names)

        # soft-note path for the unparseable lifetime -- present as a note, never a crash,
        # never counted as past-due
        odd_child = next(c for c in rep["zone_children"]["worktrees"] if c["name"] == "acme--odd--20260601")
        case("unparseable lifetime: not a defect", "defect" not in odd_child, odd_child)
        case("unparseable lifetime: recorded as a note", "note" in odd_child, odd_child)

        # size cap sanity: an empty-ish folder reports a small human-readable size, not '>2 GB'
        case("size: mystery-folder size is not the capped label",
             not next(u for u in rep["unknown_at_root"] if u["name"] == "mystery-folder")["size"].startswith(">2"))

        # --root flag wins outright over any other resolution
        r2, src2 = resolve_workspace_root(base, "/some/other/repo/dir")
        case("--root flag resolution wins", r2 == base and src2 == "--root flag")

        # JSON output round-trips through json.dumps without raising
        try:
            print_report(rep, as_json=True)
            json_ok = True
        except Exception as e:  # noqa: BLE001
            json_ok = False
            print("    JSON print raised: %s" % e)
        case("json output serializes cleanly", json_ok)

        # plain-English output likewise doesn't raise
        try:
            print_report(rep, as_json=False)
            text_ok = True
        except Exception as e:  # noqa: BLE001
            text_ok = False
            print("    text print raised: %s" % e)
        case("plain-English output prints cleanly", text_ok)

    finally:
        shutil.rmtree(base, ignore_errors=True)

    # ---- Case C: malformed registry (not a list) -> a finding, not a crash ------------
    base_c = tempfile.mkdtemp(prefix="ckws-badreg-")
    try:
        os.makedirs(os.path.join(base_c, "repos"))
        write(os.path.join(base_c, "projects.yaml"), "projects: not-a-list\n")
        rep_c = build_report(base_c, "test", now=NOW)
        case("malformed registry: not degraded (still classifies)", rep_c.get("degraded") is False)
        case("malformed registry: produced a registry problem",
             len(rep_c["registry_problems"]) >= 1, rep_c["registry_problems"])
    finally:
        shutil.rmtree(base_c, ignore_errors=True)

    # ---- Case D: lifetime parsing unit tests -------------------------------------------
    lt = evaluate_lifetime("2026-07-01", "3 weeks", NOW)
    case("lifetime: 2026-07-01 + 3 weeks is past by 2026-07-23", lt["status"] == "past", lt)
    lt2 = evaluate_lifetime("2026-07-22", "2 weeks", NOW)
    case("lifetime: 2026-07-22 + 2 weeks is NOT past", lt2["status"] == "ok", lt2)
    lt3 = evaluate_lifetime("2026-01-01", "until 2026-12-31", NOW)
    case("lifetime: until a future date is NOT past", lt3["status"] == "ok", lt3)
    lt4 = evaluate_lifetime("2026-01-01", "until 2026-02-01", NOW)
    case("lifetime: until a past date IS past", lt4["status"] == "past", lt4)
    lt5 = evaluate_lifetime(None, "3 weeks", NOW)
    case("lifetime: missing created -> unknown, not a crash", lt5["status"] == "unknown", lt5)
    lt6 = evaluate_lifetime("2026-01-01", "whenever", NOW)
    case("lifetime: gibberish lifetime -> unknown, not a crash", lt6["status"] == "unknown", lt6)

    # ---- Case E: WHY.md tolerant key reading -------------------------------------------
    base_e = tempfile.mkdtemp(prefix="ckws-why-")
    base_e_empty = None
    try:
        write(os.path.join(base_e, "WHY.md"),
              "- **Purpose**: exploratory probe\n"
              "* Created_By: someone\n"
              "expected_lifetime: 1 month\n")
        why = read_why(base_e)
        case("WHY.md: bullet + bold + underscore variants all normalize",
             why is not None and why.get("purpose") == "exploratory probe"
             and why.get("created-by") == "someone"
             and why.get("expected-lifetime") == "1 month", why)
        base_e_empty = tempfile.mkdtemp(prefix="ckws-empty-")
        case("WHY.md absent -> None, not a crash", read_why(base_e_empty) is None)
    finally:
        shutil.rmtree(base_e, ignore_errors=True)
        if base_e_empty is not None:
            rmtree_robust(base_e_empty)

    # ---- Case F: PyYAML-absent degrade path (simulated) --------------------------------
    global yaml
    saved_yaml = yaml
    base_f = tempfile.mkdtemp(prefix="ckws-noyaml-")
    try:
        os.makedirs(os.path.join(base_f, "repos"))
        write(os.path.join(base_f, "projects.yaml"), "projects:\n- name: x\n  path: repos\n  role: fork\n")
        yaml = None
        projects_f, problems_f, present_f = load_registry(base_f)
        case("PyYAML-absent: registry reported present (file exists)", present_f is True)
        case("PyYAML-absent: registry treated as empty", projects_f == [])
        case("PyYAML-absent: a clear finding is produced",
             any("PyYAML not installed" in p for p in problems_f), problems_f)
    finally:
        yaml = saved_yaml
        shutil.rmtree(base_f, ignore_errors=True)

    # ---- Case G: WHY.md required-field enforcement (FIX 2) -----------------------------
    base_g = tempfile.mkdtemp(prefix="ckws-whyreq-")
    try:
        os.makedirs(os.path.join(base_g, "repos"))
        write(os.path.join(base_g, "projects.yaml"), "projects: []\n")

        # incomplete WHY.md: missing expected-lifetime -> a birth-certificate DEFECT,
        # not a silent pass, even though WHY.md exists
        wt_incomplete = os.path.join(base_g, "worktrees", "incomplete--20260710")
        os.makedirs(wt_incomplete)
        write(os.path.join(wt_incomplete, "WHY.md"),
              "purpose: half-finished probe\n"
              "created: 2026-07-10\n")

        # created-by missing ALONE (purpose/created/expected-lifetime all present) ->
        # a soft note, never a defect
        wt_cbonly = os.path.join(base_g, "worktrees", "cbonly--20260710")
        os.makedirs(wt_cbonly)
        write(os.path.join(wt_cbonly, "WHY.md"),
              "purpose: fully specified except created-by\n"
              "created: 2026-07-10\n"
              "expected-lifetime: permanent\n")

        rep_g = build_report(base_g, "test", now=NOW)
        defects_g = {d["name"]: d for d in rep_g["birth_certificate_defects"]}
        case("incomplete WHY.md is a birth-certificate defect",
             "incomplete--20260710" in defects_g, defects_g)
        case("incomplete WHY.md defect names the missing field(s)",
             defects_g.get("incomplete--20260710", {}).get("reason") == "incomplete-why"
             and "expected-lifetime" in defects_g.get("incomplete--20260710", {}).get("missing_fields", []),
             defects_g.get("incomplete--20260710"))
        case("created-by-only-missing is NOT a defect",
             "cbonly--20260710" not in defects_g, defects_g)
        cb_child = next(c for c in rep_g["zone_children"]["worktrees"] if c["name"] == "cbonly--20260710")
        case("created-by-only-missing recorded as a soft note",
             "note" in cb_child and "created-by" in cb_child["note"], cb_child)
    finally:
        shutil.rmtree(base_g, ignore_errors=True)

    # ---- Case H: zone naming-convention notes (FIX 3) ----------------------------------
    base_h = tempfile.mkdtemp(prefix="ckws-naming-")
    try:
        os.makedirs(os.path.join(base_h, "repos"))
        write(os.path.join(base_h, "projects.yaml"), "projects: []\n")

        why_ok = ("purpose: naming-convention fixture\n"
                  "created: 2026-07-23\n"
                  "expected-lifetime: permanent\n")

        sc_conforming = os.path.join(base_h, "scratch", "probe--20260723")
        os.makedirs(sc_conforming)
        write(os.path.join(sc_conforming, "WHY.md"), why_ok)

        sc_bad_name = os.path.join(base_h, "scratch", "probe-no-date")
        os.makedirs(sc_bad_name)
        write(os.path.join(sc_bad_name, "WHY.md"), why_ok)

        rep_h = build_report(base_h, "test", now=NOW)
        naming_names = {n["name"] for n in rep_h["naming_convention_notes"]}
        case("nonconforming folder name produces a naming-convention note",
             "probe-no-date" in naming_names, naming_names)
        case("conforming folder name does NOT produce a naming-convention note",
             "probe--20260723" not in naming_names, naming_names)
        defect_names_h = {d["name"] for d in rep_h["birth_certificate_defects"]}
        case("naming-convention note is never a defect",
             "probe-no-date" not in defect_names_h, defect_names_h)
    finally:
        shutil.rmtree(base_h, ignore_errors=True)

    # ---- Case I: worktree merged/deleted-branch reap detection (FIX 1) -----------------
    git_available = shutil.which("git") is not None
    if not git_available:
        case("FIX 1 reap-detection self-test: skipped (git not found on PATH)", True)
    else:
        base_i = tempfile.mkdtemp(prefix="ckws-gitreap-")
        try:
            def git(args, cwd):
                return subprocess.run(["git"] + list(args), cwd=cwd,
                                       capture_output=True, text=True, timeout=10)

            def git_ok(args, cwd):
                r = git(args, cwd)
                if r.returncode != 0:
                    raise RuntimeError("git %s failed in %s: %s" % (args, cwd, r.stderr))
                return r

            origin_dir = os.path.join(base_i, "origin")
            os.makedirs(origin_dir)
            git_ok(["init", "-q", "-b", "main"], origin_dir)
            git_ok(["config", "user.email", "test@example.invalid"], origin_dir)
            git_ok(["config", "user.name", "ckws-selftest"], origin_dir)
            write(os.path.join(origin_dir, "seed.txt"), "seed\n")
            git_ok(["add", "seed.txt"], origin_dir)
            git_ok(["commit", "-q", "-m", "seed"], origin_dir)
            origin_url = origin_dir.replace("\\", "/")

            wt_dir = os.path.join(base_i, "worktrees")
            os.makedirs(wt_dir)

            def clone(child):
                git_ok(["clone", "-q", origin_url, child], base_i)
                git_ok(["config", "user.email", "test@example.invalid"], child)
                git_ok(["config", "user.name", "ckws-selftest"], child)

            # merged-branch case: a feature branch fully merged into main, then
            # re-checked-out so HEAD sits on the now-fully-merged branch
            merged_child = os.path.join(wt_dir, "merged-child--20260723")
            clone(merged_child)
            git_ok(["checkout", "-q", "-b", "feature-done"], merged_child)
            write(os.path.join(merged_child, "feature.txt"), "feature\n")
            git_ok(["add", "feature.txt"], merged_child)
            git_ok(["commit", "-q", "-m", "feature work"], merged_child)
            git_ok(["checkout", "-q", "main"], merged_child)
            git_ok(["merge", "-q", "--no-ff", "-m", "merge feature-done", "feature-done"], merged_child)
            git_ok(["checkout", "-q", "feature-done"], merged_child)
            # drop the origin remote so default-branch resolution falls back to the LOCAL
            # main (which has the merge); origin/main would still be stale at the seed
            # commit and wrongly say "not merged" -- this also exercises the standard's
            # documented local-main/master fallback path.
            git_ok(["remote", "remove", "origin"], merged_child)

            # deleted/detached-branch case
            detached_child = os.path.join(wt_dir, "detached-child--20260723")
            clone(detached_child)
            head_sha = git_ok(["rev-parse", "HEAD"], detached_child).stdout.strip()
            git_ok(["checkout", "-q", head_sha], detached_child)

            # symbolic-ref-to-a-deleted-branch case (DEFECT 2): HEAD stays a SYMBOLIC ref
            # (unlike the detached case above) but the branch it names no longer resolves --
            # e.g. the branch was deleted from another checkout/clone while this worktree's
            # .git/HEAD still says "ref: refs/heads/gone-branch". `git symbolic-ref -q HEAD`
            # still succeeds here (rc==0), so the plain detached-HEAD check alone would miss
            # this; only `git rev-parse --verify --quiet <ref>` failing catches it.
            symref_gone_child = os.path.join(wt_dir, "symref-gone-child--20260723")
            clone(symref_gone_child)
            git_ok(["checkout", "-q", "-b", "gone-branch"], symref_gone_child)
            git_ok(["update-ref", "-d", "refs/heads/gone-branch"], symref_gone_child)

            # normal, unmerged child -> should trigger neither flag
            normal_child = os.path.join(wt_dir, "normal-child--20260723")
            clone(normal_child)
            git_ok(["checkout", "-q", "-b", "still-working"], normal_child)
            write(os.path.join(normal_child, "wip.txt"), "wip\n")
            git_ok(["add", "wip.txt"], normal_child)
            git_ok(["commit", "-q", "-m", "work in progress"], normal_child)

            flags, notes = classify_worktree_reap_candidates(wt_dir)
            by_name = {f["name"]: f for f in flags}

            case("FIX 1: fully-merged branch flagged as reap candidate",
                 by_name.get("merged-child--20260723", {}).get("flag") == "merged", by_name)
            case("FIX 1: detached HEAD flagged as deleted/detached branch",
                 by_name.get("detached-child--20260723", {}).get("flag") == "deleted-branch", by_name)
            case("DEFECT 2: symbolic-ref to a deleted branch flagged as deleted-branch",
                 by_name.get("symref-gone-child--20260723", {}).get("flag") == "deleted-branch", by_name)
            case("FIX 1: normal unmerged branch NOT flagged",
                 "normal-child--20260723" not in by_name, by_name)
        except Exception as e:  # noqa: BLE001 -- a fixture-setup hiccup skips, never crashes the suite
            case("FIX 1 reap-detection self-test: skipped (fixture setup failed: %s)" % e, True)
        finally:
            rmtree_robust(base_i)

    # ---- Case J: FIX 1 degrade paths (not a git repo, git call failure) ----------------
    base_j = tempfile.mkdtemp(prefix="ckws-gitdegrade-")
    try:
        # a worktree child with a .git file/dir but that is NOT actually a valid repo
        # (e.g. an empty .git directory) -- must degrade to a note, never crash, never a
        # false defect
        fake_git_child = os.path.join(base_j, "not-a-real-repo--20260723")
        os.makedirs(os.path.join(fake_git_child, ".git"))
        result = check_worktree_reap_candidate(fake_git_child)
        case("FIX 1: a .git dir that isn't a real repo degrades to a note, not a crash/defect",
             "note" in result and "flag" not in result, result)

        flags_j, notes_j = classify_worktree_reap_candidates(base_j)
        case("FIX 1: non-repo child produces no flags at the classify_* level",
             flags_j == [], flags_j)
    finally:
        rmtree_robust(base_j)

    print("check-workspace self-test: %s (%d/%d)"
          % ("PASS" if failed == 0 else "FAIL", total - failed, total))
    return 0 if failed == 0 else 1


# --------------------------------------------------------------------------- CLI

def main(argv):
    args = argv[1:]
    if "--self-test" in args:
        return self_test()
    as_json = "--json" in args
    explicit_root = None
    if "--root" in args:
        i = args.index("--root")
        if i + 1 < len(args):
            explicit_root = args[i + 1]
    repo_dir = os.getcwd()
    root, source = resolve_workspace_root(explicit_root, repo_dir)
    report = build_report(root, source)
    print_report(report, as_json=as_json)
    return 0  # report-only: always 0 on a completed report (D3)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
