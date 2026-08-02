#!/usr/bin/env python3
"""check-parity.py -- fork/template mirror-drift sensor (session C, v3.0-43b, decision D6).

The fork keeps two things byte-mirrored from the template repo by hand: the engine sensors
under deploy/ (mirrored from <template>/capabilities/knowledge-os/extracted/deploy/) and the
skills under .claude/skills/ (instantiated from <template>/core/skills/). Both mirrors drift
silently whenever one side is edited and the other isn't. This sensor makes that drift
mechanical: SHA-256 comparison, report-only, degrade-never-block (spec doctrine, matching
check-frontmatter.py / check-caps.py in this directory).

Where the template lives: an optional `template_path:` key in ./project.yaml (the fork sets
it; the template repo does not, so parity is naturally unwatched there). Absence of
project.yaml, absence of the key, or absence of PyYAML (needed to read project.yaml -- the
knowledge-os frontmatter contract deliberately keeps check-frontmatter.py stdlib-only, but
project.yaml is a small config file with no comparable blast-radius concern, so this sensor
uses PyYAML the same tolerant way check-caps.py reads engine-caps.yaml) all degrade to the
same single NOTE line and exit 0 -- never a hard failure, per D6.

Two mirror pairs compared by SHA-256:
  1. deploy/                        vs  <template_path>/capabilities/knowledge-os/extracted/deploy/
  2. .claude/skills/<name>/         vs  <template_path>/core/skills/<name>/   (per shared skill dir)

Exclusions (deploy/ walk; see EXCLUSION NOTES below for the "why" of each):
  __pycache__/, evidence/, fixtures/, test-fixtures/  -- always skipped, never compared.
  descriptors/                                        -- skipped ONLY if it exceeds
                                                          DESCRIPTORS_SIZE_LIMIT (see below).

Three things are NOT the same as DRIFT, and are reported informationally instead:
  - instance-config basenames (INSTANCE_CONFIG_BASENAMES below): files that legitimately hold
    per-instance content (credentials, deadlines, origin/signing config, allowlists, a golden
    descriptor answer-key) and are expected to differ fork-to-fork and fork-to-template.
  - instance-local script basenames (INSTANCE_LOCAL_SCRIPT_BASENAMES below): one-shot,
    fork-hardwired campaign tooling under deploy/ that has no template-side counterpart at all,
    ever, by design (e.g. a clearance-claim generator hardcoded to this fork's own evidence
    paths and commit history) -- distinct from instance-config in that it's executable tooling,
    not config/data whose content differs.
  - the .example / .template convention: a fork's instantiated `foo.yaml` commonly pairs with
    the template's placeholder `foo.yaml.example` or `foo.yaml.template` (same for
    `SKILL.md` / `SKILL.md.template`). Different basenames by design -- never byte-compared,
    just named as a pair.

Usage:
  check-parity.py                                   report against project.yaml's template_path
  check-parity.py --json                             same report, machine-readable
  check-parity.py --fork-root DIR --template-root DIR
                                                       compare two explicit roots directly,
                                                       bypassing project.yaml (used by --self-test;
                                                       also useful for an ad-hoc manual comparison)
  check-parity.py --self-test                        hermetic fixture battery; PASS/FAIL

Exit codes: 0 = report completed (drift found or not -- report-only, never blocks) |
1 = self-test failure only.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile

try:  # cp1252 consoles + unicode paths/content: never let an encode error mask a verdict.
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import yaml
except ImportError:  # pragma: no cover -- degrade path, exercised by self-test's own note
    yaml = None

_HERE = os.path.dirname(os.path.abspath(__file__))

# --- exclusion policy (deploy/ walk) ---------------------------------------------------------
# These are directory BASENAMES, pruned wherever they occur under deploy/. The mirror contract
# (per D6) covers ENGINE CODE AND CONFIG -- these four hold things that are neither:
#   __pycache__   -- compiled bytecode, not source; never meaningfully "in parity".
#   evidence/     -- session-accumulated run evidence (664MB / 4715 files at build time on this
#                     fork) -- pure generated output, not engine surface, and comparing it
#                     against the template (which carries none) would be all noise, no signal.
#   fixtures/     -- small (8KB at build time) but content-only, not engine code/config.
#   test-fixtures/-- same character as fixtures/ (158KB / 64 files at build time): test inputs,
#                     not engine surface.
# __pycache__ is pruned everywhere (deploy/ AND every skill dir) -- compiled bytecode is never
# a meaningful comparison target regardless of which mirror pair is being walked.
ALWAYS_SKIP_DIRNAMES = frozenset({"__pycache__"})
DEPLOY_SKIP_DIRNAMES = frozenset({"evidence", "fixtures", "test-fixtures"})

# descriptors/ is judgment-call middle ground (task instruction: "include descriptors only if
# small"). It holds hand-authored golden-task answer keys (KB-scale by design -- see
# golden-descriptors.yaml's own docstring and the template's descriptors/README.md). Below the
# limit it's compared like any other config; above it, it has stopped looking like hand-authored
# fixture data and started looking like accumulated generated output (evidence/'s failure mode),
# so it is excluded and the exclusion is reported rather than silently applied.
DESCRIPTORS_SIZE_LIMIT = 1_048_576  # 1 MiB

# --- instance-config basenames ---------------------------------------------------------------
# Per-instance content: legitimately differs fork-to-fork and fork-to-template. Reported
# informationally, never as DRIFT, regardless of which side(s) hold it or whether their content
# matches. Matched on os.path.basename() with any .example/.template suffix stripped first, so
# credential-bindings.yaml (fork) and credential-bindings.yaml.example (template) both match.
INSTANCE_CONFIG_BASENAMES = frozenset({
    "deadline-register.yaml",
    "environment-manifest.yaml",
    "credential-bindings.yaml",
    "origin-config.yaml",
    "signing-config.yaml",
    "origin-attestations.yaml",
    "known-holes.yaml",
    "safe-allowlist.yaml",
    # Judgment-call addition beyond the operator's original 8-item list (documented per the task's
    # own instruction to use judgment and say why): golden-descriptors.yaml. The template's own
    # capabilities/knowledge-os/extracted/deploy/descriptors/README.md states a golden descriptor
    # "is only meaningful against a real, populated wiki" and "cannot be verified... against any
    # other instance's wiki" -- i.e. it is per-instance content by the template's own design, the
    # same character as the 8 basenames above, just not on the pre-supplied list.
    "golden-descriptors.yaml",
    # Orchestrator adjudication on the first real run (2026-07-23): two more per-instance files
    # that surfaced as false DRIFT. entities.yaml is the instance's own entity/alias graph
    # (instance entities on the fork; a generic seed in the template). engine-caps.yaml is
    # instance-adapted cap policy (its own comments say "Reconcile against this instance's
    # real cap policy"; the fork copy names the instance). Both are the same character as
    # the basenames above: content that MUST differ per instance by design.
    "entities.yaml",
    "engine-caps.yaml",
})

# --- instance-local scripts (deploy/; one-shot per-instance tooling, never mirrored) ---------
# Different character from INSTANCE_CONFIG_BASENAMES above: those are config/data files whose
# *content* legitimately differs per instance while the file itself is expected on both sides
# (or as a fork-only/.example placeholder pair). These are executable, one-shot campaign tooling
# hardwired to THIS fork's own evidence paths and commit history -- they have no template-side
# counterpart at all, ever, by design. Matched the same way (basename, with .example/.template
# convention suffix stripped first), reported informationally, never as DRIFT.
INSTANCE_LOCAL_SCRIPT_BASENAMES = frozenset({
    # adjudicated 2026-07-24, session E: instance campaign tooling, F13 clearance generator tied
    # to fork-local evidence dirs (deploy/evidence/f13-v2-full-2026-07-04/...; commit 055c4a3).
    "gen-clearance-claims.py",
})

CONVENTION_SUFFIXES = (".example", ".template")


def _strip_convention_suffix(name):
    for suf in CONVENTION_SUFFIXES:
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def _dir_size(path):
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for f in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _list_files(root, apply_deploy_exclusions=False):
    """Return ({relpath (posix, sorted-stable): abspath}, [exclusion-note, ...]).

    apply_deploy_exclusions=True applies SKIP_DIRNAMES + the descriptors/ size gate (the
    deploy/ mirror walk); skill-dir walks pass False (no such exclusions apply there -- a
    skill directory is small and entirely engine surface by construction)."""
    out = {}
    notes = []
    if not os.path.isdir(root):
        return out, notes
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for d in list(dirnames):
            if d in ALWAYS_SKIP_DIRNAMES:
                dirnames.remove(d)
        if apply_deploy_exclusions:
            for d in list(dirnames):
                if d in DEPLOY_SKIP_DIRNAMES:
                    dirnames.remove(d)
                elif d == "descriptors":
                    dsize = _dir_size(os.path.join(dirpath, d))
                    if dsize > DESCRIPTORS_SIZE_LIMIT:
                        dirnames.remove(d)
                        rel_d = os.path.relpath(os.path.join(dirpath, d), root).replace(os.sep, "/")
                        notes.append(
                            "excluded %s/%s (%d bytes > %d-byte descriptors/ limit; treated like "
                            "evidence/ -- no longer hand-authored-fixture-scale)"
                            % (os.path.relpath(root, root) or ".", rel_d, dsize, DESCRIPTORS_SIZE_LIMIT))
        for f in sorted(filenames):
            fp = os.path.join(dirpath, f)
            rel = os.path.relpath(fp, root).replace(os.sep, "/")
            out[rel] = fp
    return out, notes


def _find_convention_pair(rel, other_files):
    """If `rel` (missing from `other_files`) has a .example/.template twin (or is itself one)
    present in `other_files`, return that twin's relpath; else None."""
    for suf in CONVENTION_SUFFIXES:
        cand = rel + suf
        if cand in other_files:
            return cand
    stripped = _strip_convention_suffix(rel)
    if stripped != rel and stripped in other_files:
        return stripped
    return None


def _is_instance_config(rel):
    base = os.path.basename(rel)
    return base in INSTANCE_CONFIG_BASENAMES or _strip_convention_suffix(base) in INSTANCE_CONFIG_BASENAMES


def _is_instance_local_script(rel):
    base = os.path.basename(rel)
    return (base in INSTANCE_LOCAL_SCRIPT_BASENAMES
            or _strip_convention_suffix(base) in INSTANCE_LOCAL_SCRIPT_BASENAMES)


def compare_trees(fork_root, template_root, apply_deploy_exclusions):
    """Core per-pair comparison, shared by the deploy/ walk and each skill-dir walk."""
    fork_files, fork_notes = _list_files(fork_root, apply_deploy_exclusions)
    tmpl_files, tmpl_notes = _list_files(template_root, apply_deploy_exclusions)

    identical, differs = [], []
    missing_in_template, missing_in_fork = [], []
    instance_config, convention_pairs = [], []
    instance_local_script = []
    paired = set()  # relpaths already consumed by a convention-pair match (either side) --
                     # a pair is a single 2-element relationship, not two independent findings.

    for rel in sorted(set(fork_files) | set(tmpl_files)):
        if rel in paired:
            continue
        in_fork, in_tmpl = rel in fork_files, rel in tmpl_files
        if in_fork and in_tmpl:
            if _is_instance_config(rel):
                instance_config.append({"path": rel, "note": "present both sides (per-instance content; not content-compared)"})
                continue
            h1, h2 = _sha256(fork_files[rel]), _sha256(tmpl_files[rel])
            (identical if h1 == h2 else differs).append(rel)
        elif in_fork:
            if _is_instance_local_script(rel):
                instance_local_script.append({"path": rel, "note": "fork-only (instance-local script, never mirrored, informational)"})
                continue
            if _is_instance_config(rel):
                instance_config.append({"path": rel, "note": "fork-only (per-instance content, informational)"})
                continue
            pair = _find_convention_pair(rel, tmpl_files)
            if pair is not None:
                convention_pairs.append({"fork": rel, "template": pair})
                paired.add(pair)
                continue
            missing_in_template.append(rel)
        else:
            if _is_instance_config(rel):
                instance_config.append({"path": rel, "note": "template-only placeholder (per-instance content, informational)"})
                continue
            pair = _find_convention_pair(rel, fork_files)
            if pair is not None:
                convention_pairs.append({"fork": pair, "template": rel})
                paired.add(pair)
                continue
            missing_in_fork.append(rel)

    return {
        "identical": identical, "differs": differs,
        "missing_in_template": missing_in_template, "missing_in_fork": missing_in_fork,
        "instance_config": instance_config, "convention_pairs": convention_pairs,
        "instance_local_script": instance_local_script,
        "excluded_notes": fork_notes + tmpl_notes,
    }


def compare_deploy(fork_root, template_root):
    fork_deploy = os.path.join(fork_root, "deploy")
    tmpl_deploy = os.path.join(template_root, "capabilities", "knowledge-os", "extracted", "deploy")
    return compare_trees(fork_deploy, tmpl_deploy, apply_deploy_exclusions=True)


def compare_skills(fork_root, template_root):
    fork_skills = os.path.join(fork_root, ".claude", "skills")
    tmpl_skills = os.path.join(template_root, "core", "skills")

    fork_dirs = {d for d in os.listdir(fork_skills) if os.path.isdir(os.path.join(fork_skills, d))} \
        if os.path.isdir(fork_skills) else set()
    tmpl_dirs = {d for d in os.listdir(tmpl_skills) if os.path.isdir(os.path.join(tmpl_skills, d))} \
        if os.path.isdir(tmpl_skills) else set()

    instance_local_skill = sorted(fork_dirs - tmpl_dirs)   # fork-only skill: legitimate, informational
    missing_instance_skill = sorted(tmpl_dirs - fork_dirs)  # template skill never instantiated: a real gap

    identical, differs = [], []
    missing_in_template, missing_in_fork, convention_pairs = [], [], []
    for skill in sorted(fork_dirs & tmpl_dirs):
        per_skill = compare_trees(os.path.join(fork_skills, skill), os.path.join(tmpl_skills, skill),
                                   apply_deploy_exclusions=False)
        identical.extend("%s/%s" % (skill, r) for r in per_skill["identical"])
        differs.extend("%s/%s" % (skill, r) for r in per_skill["differs"])
        missing_in_template.extend("%s/%s" % (skill, r) for r in per_skill["missing_in_template"])
        missing_in_fork.extend("%s/%s" % (skill, r) for r in per_skill["missing_in_fork"])
        for p in per_skill["convention_pairs"]:
            convention_pairs.append({"fork": "%s/%s" % (skill, p["fork"]), "template": "%s/%s" % (skill, p["template"])})
        # instance_config basenames are not expected inside skill dirs; per_skill["instance_config"]
        # is folded into convention_pairs/differs-neutral territory only if it ever fires (it won't
        # for the current skill inventory, but compare_trees stays generic on purpose).

    return {
        "instance_local_skill": instance_local_skill,
        "missing_instance_skill": missing_instance_skill,
        "identical": identical, "differs": differs,
        "missing_in_template": missing_in_template, "missing_in_fork": missing_in_fork,
        "convention_pairs": convention_pairs,
    }


def build_report(fork_root, template_root):
    return {
        "fork_root": os.path.abspath(fork_root),
        "template_root": os.path.abspath(template_root),
        "deploy": compare_deploy(fork_root, template_root),
        "skills": compare_skills(fork_root, template_root),
    }


# ---------------------------------------------------------------------------
# Plain-English rendering
# ---------------------------------------------------------------------------

def render_text(report):
    d, s = report["deploy"], report["skills"]
    lines = []
    lines.append("check-parity: fork/template mirror-drift report")
    lines.append("  fork:     %s" % report["fork_root"])
    lines.append("  template: %s" % report["template_root"])
    lines.append("")
    lines.append("Convention note: an instantiated fork file (e.g. origin-config.yaml, SKILL.md)")
    lines.append("commonly pairs with a template PLACEHOLDER of a different basename")
    lines.append("(origin-config.yaml.template, SKILL.md.template, foo.yaml.example). Those pairs")
    lines.append("are never byte-compared -- they're named under 'convention pairs' below, not DRIFT.")
    lines.append("")

    drift_deploy = len(d["differs"]) + len(d["missing_in_template"]) + len(d["missing_in_fork"])
    drift_skills = (len(s["differs"]) + len(s["missing_in_template"]) + len(s["missing_in_fork"])
                     + len(s["missing_instance_skill"]))
    n_drift = drift_deploy + drift_skills
    n_identical = len(d["identical"]) + len(s["identical"])
    n_info = (len(d["instance_config"]) + len(d["convention_pairs"])
              + len(s["convention_pairs"]) + len(s["instance_local_skill"])
              + len(d["instance_local_script"]))

    lines.append("SUMMARY: %d identical | %d DRIFT finding(s) | %d informational item(s)"
                 % (n_identical, n_drift, n_info))
    lines.append("")

    if n_drift == 0:
        lines.append("DRIFT: none.")
    else:
        lines.append("DRIFT (%d):" % n_drift)
        for rel in d["differs"]:
            lines.append("  DIFFERS            deploy/%s  (fork and template both have it; content differs)" % rel)
        for rel in d["missing_in_template"]:
            lines.append("  MISSING-IN-TEMPLATE deploy/%s  (in fork, not mirrored to template)" % rel)
        for rel in d["missing_in_fork"]:
            lines.append("  MISSING-IN-FORK     deploy/%s  (in template, not pulled into fork)" % rel)
        for rel in s["differs"]:
            lines.append("  DIFFERS            .claude/skills/%s  (fork and template both have it; content differs)" % rel)
        for rel in s["missing_in_template"]:
            lines.append("  MISSING-IN-TEMPLATE .claude/skills/%s  (in fork skill, not mirrored to template)" % rel)
        for rel in s["missing_in_fork"]:
            lines.append("  MISSING-IN-FORK     .claude/skills/%s  (in template skill, not pulled into fork)" % rel)
        for name in s["missing_instance_skill"]:
            lines.append("  MISSING-INSTANCE-SKILL  %s  (exists in template core/skills/, never instantiated onto"
                         " this fork's .claude/skills/ -- operator's call whether to adopt it)" % name)
    lines.append("")

    lines.append("INFORMATIONAL -- instance-config (per-instance content; expected to differ, not drift):")
    if d["instance_config"] or s.get("instance_config"):
        for item in d["instance_config"]:
            lines.append("  deploy/%-46s %s" % (item["path"], item["note"]))
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("INFORMATIONAL -- instance-local scripts (deploy/; one-shot per-instance tooling, never mirrored):")
    if d["instance_local_script"]:
        for item in d["instance_local_script"]:
            lines.append("  deploy/%-46s %s" % (item["path"], item["note"]))
    else:
        lines.append("  (none)")
    lines.append("")

    all_pairs = ([("deploy/" + p["fork"], "deploy/" + p["template"]) for p in d["convention_pairs"]]
                 + [(".claude/skills/" + p["fork"], "core/skills/" + p["template"]) for p in s["convention_pairs"]])
    lines.append("INFORMATIONAL -- .example/.template convention pairs (not byte-compared):")
    if all_pairs:
        for fork_p, tmpl_p in all_pairs:
            lines.append("  %-46s <-> %s" % (fork_p, tmpl_p))
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("INFORMATIONAL -- instance-local skills (fork-only; legitimate, e.g. project-specific skills):")
    if s["instance_local_skill"]:
        for name in s["instance_local_skill"]:
            lines.append("  .claude/skills/%s" % name)
    else:
        lines.append("  (none)")
    lines.append("")

    notes = d["excluded_notes"]
    lines.append("EXCLUDED FROM COMPARISON (deploy/ walk):")
    lines.append("  __pycache__/, evidence/, fixtures/, test-fixtures/  -- always (generated/test data,")
    lines.append("  not engine surface). descriptors/ -- only if it exceeds the %d-byte size gate."
                 % DESCRIPTORS_SIZE_LIMIT)
    if notes:
        for n in notes:
            lines.append("  %s" % n)
    lines.append("")
    lines.append("%d identical file(s) not listed individually (deploy: %d, skills: %d)."
                 % (n_identical, len(d["identical"]), len(s["identical"])))
    return "\n".join(lines) + "\n"


def _note(msg, json_out):
    if json_out:
        print(json.dumps({"note": msg}, indent=2))
    else:
        print("NOTE: %s" % msg)


def load_template_path(fork_root):
    """Return (template_path_or_None, reason_if_None). Tolerates every failure mode per D6:
    missing project.yaml, missing key, missing PyYAML -- each collapses to the same NOTE."""
    path = os.path.join(fork_root, "project.yaml")
    if not os.path.isfile(path):
        return None, "project.yaml not found at %s" % path
    if yaml is None:
        return None, "PyYAML not installed (cannot read project.yaml)"
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            doc = yaml.safe_load(fh) or {}
    except Exception as e:  # a malformed project.yaml degrades too, never blocks
        return None, "project.yaml unreadable: %s" % e
    tp = doc.get("template_path") if isinstance(doc, dict) else None
    if not tp:
        return None, "template_path key not set in project.yaml"
    return tp, None


# ---------------------------------------------------------------------------
# Self-test: hermetic fixture trees, every classification exercised.
# ---------------------------------------------------------------------------

def _write(path, content=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


def _build_fixture_pair(base):
    """Build fork_root/ and template_root/ under `base`, covering every class."""
    fork_root = os.path.join(base, "fork")
    tmpl_root = os.path.join(base, "template")

    # --- deploy/ mirror pair ---
    _write(os.path.join(fork_root, "deploy", "identical.py"), "print('A')\n")
    _write(os.path.join(tmpl_root, "capabilities", "knowledge-os", "extracted", "deploy", "identical.py"), "print('A')\n")

    _write(os.path.join(fork_root, "deploy", "differs.py"), "print('fork')\n")
    _write(os.path.join(tmpl_root, "capabilities", "knowledge-os", "extracted", "deploy", "differs.py"), "print('template')\n")

    _write(os.path.join(fork_root, "deploy", "only_fork.py"), "x = 1\n")
    _write(os.path.join(tmpl_root, "capabilities", "knowledge-os", "extracted", "deploy", "only_template.py"), "y = 2\n")

    # instance-local script basename, fork-only, no template-side counterpart ever -- must be
    # informational (instance_local_script), never missing_in_template, unlike only_fork.py above.
    _write(os.path.join(fork_root, "deploy", "gen-clearance-claims.py"), "# one-shot campaign tool\n")

    # instance-config basename, present both sides, deliberately DIFFERENT content -- must be
    # informational, not DRIFT (this is the property that most distinguishes it from `differs`).
    _write(os.path.join(fork_root, "deploy", "known-holes.yaml"), "holes: [fork-only-hole]\n")
    _write(os.path.join(tmpl_root, "capabilities", "knowledge-os", "extracted", "deploy", "known-holes.yaml"), "holes: []\n")

    # generic .example/.template convention pair, UNRELATED to the instance-config basename list
    # (proves the convention-pair mechanism works independent of instance-config matching).
    _write(os.path.join(fork_root, "deploy", "sample-config.yaml"), "mode: fork\n")
    _write(os.path.join(tmpl_root, "capabilities", "knowledge-os", "extracted", "deploy", "sample-config.yaml.template"), "mode: <fill-in>\n")

    # skip-dir contents: must never appear in any report bucket.
    _write(os.path.join(fork_root, "deploy", "__pycache__", "junk.pyc"), "not real bytecode\n")
    _write(os.path.join(fork_root, "deploy", "evidence", "run1", "big.txt"), "generated evidence\n")
    _write(os.path.join(fork_root, "deploy", "fixtures", "f.txt"), "fixture\n")
    _write(os.path.join(fork_root, "deploy", "test-fixtures", "tf.txt"), "test fixture\n")

    # descriptors/ under the size gate: compared normally, differs -> real DRIFT.
    _write(os.path.join(fork_root, "deploy", "descriptors", "small.yaml"), "a: 1\n")
    _write(os.path.join(tmpl_root, "capabilities", "knowledge-os", "extracted", "deploy", "descriptors", "small.yaml"), "a: 2\n")

    # descriptors/ over the size gate: excluded, must not appear as DRIFT.
    big_dir = os.path.join(fork_root, "deploy", "descriptors_huge_probe")  # placeholder, unused
    del big_dir

    # --- .claude/skills/ vs core/skills/ pair ---
    _write(os.path.join(fork_root, ".claude", "skills", "skillA", "SKILL.md"), "# A\nidentical\n")
    _write(os.path.join(tmpl_root, "core", "skills", "skillA", "SKILL.md"), "# A\nidentical\n")

    _write(os.path.join(fork_root, ".claude", "skills", "skillB", "SKILL.md"), "# B fork content\n")
    _write(os.path.join(tmpl_root, "core", "skills", "skillB", "SKILL.md.template"), "# B placeholder\n")

    _write(os.path.join(fork_root, ".claude", "skills", "skillC", "SKILL.md"), "# C fork-only skill\n")

    _write(os.path.join(tmpl_root, "core", "skills", "skillD", "SKILL.md"), "# D template-only skill\n")

    return fork_root, tmpl_root


def _build_oversize_descriptors_case(base):
    """A second, isolated fixture pair just for the descriptors/ size-gate exclusion (kept
    separate from the main pair so its ~1.1MB payload doesn't bloat every other case)."""
    fork_root = os.path.join(base, "fork_big")
    tmpl_root = os.path.join(base, "template_big")
    big = "x" * (DESCRIPTORS_SIZE_LIMIT + 1024)
    _write(os.path.join(fork_root, "deploy", "descriptors", "huge.yaml"), big)
    _write(os.path.join(tmpl_root, "capabilities", "knowledge-os", "extracted", "deploy", "descriptors", "huge.yaml"), "small\n")
    return fork_root, tmpl_root


def self_test():
    total = failed = 0

    def case(name, ok, detail=""):
        nonlocal total, failed
        total += 1
        status = "ok " if ok else "XX "
        print("  %s %s%s" % (status, name, ("  << " + detail) if (not ok and detail) else ""))
        if not ok:
            failed += 1

    base = tempfile.mkdtemp(prefix="check-parity-selftest-")
    try:
        fork_root, tmpl_root = _build_fixture_pair(base)
        report = build_report(fork_root, tmpl_root)
        d, s = report["deploy"], report["skills"]

        case("identical.py classified identical", "identical.py" in d["identical"])
        case("differs.py classified DIFFERS", "differs.py" in d["differs"])
        case("only_fork.py classified missing-in-template", "only_fork.py" in d["missing_in_template"])
        case("only_template.py classified missing-in-fork", "only_template.py" in d["missing_in_fork"])

        script_info = [i for i in d["instance_local_script"] if i["path"] == "gen-clearance-claims.py"]
        case("gen-clearance-claims.py (instance-local script, fork-only) -> informational, not DRIFT",
             len(script_info) == 1
             and "gen-clearance-claims.py" not in d["missing_in_template"]
             and "gen-clearance-claims.py" not in d["differs"])

        holes_info = [i for i in d["instance_config"] if i["path"] == "known-holes.yaml"]
        case("known-holes.yaml (instance-config, differing content) -> informational, not DRIFT",
             len(holes_info) == 1
             and "known-holes.yaml" not in d["differs"]
             and "known-holes.yaml" not in d["missing_in_template"]
             and "known-holes.yaml" not in d["missing_in_fork"])

        pair_hit = [p for p in d["convention_pairs"]
                    if p["fork"] == "sample-config.yaml" and p["template"] == "sample-config.yaml.template"]
        case("sample-config.yaml <-> sample-config.yaml.template -> convention pair, not DRIFT",
             len(pair_hit) == 1
             and "sample-config.yaml" not in d["missing_in_template"]
             and "sample-config.yaml.template" not in d["missing_in_fork"])

        all_deploy_paths = (d["identical"] + d["differs"] + d["missing_in_template"] + d["missing_in_fork"]
                            + [i["path"] for i in d["instance_config"]]
                            + [p["fork"] for p in d["convention_pairs"]] + [p["template"] for p in d["convention_pairs"]])
        case("__pycache__/evidence/fixtures/test-fixtures contents never appear in any bucket",
             not any(("__pycache__" in p or "evidence" in p or p.startswith("fixtures/") or p.startswith("test-fixtures/"))
                     for p in all_deploy_paths))

        case("descriptors/small.yaml (under size gate) compared normally -> DIFFERS",
             "descriptors/small.yaml" in d["differs"])

        case("skillA/SKILL.md identical", "skillA/SKILL.md" in s["identical"])
        skillb_pair = [p for p in s["convention_pairs"]
                       if p["fork"] == "skillB/SKILL.md" and p["template"] == "skillB/SKILL.md.template"]
        case("skillB SKILL.md <-> SKILL.md.template -> convention pair, not DRIFT",
             len(skillb_pair) == 1
             and "skillB/SKILL.md" not in s["missing_in_template"])
        case("skillC (fork-only skill dir) -> instance_local_skill", "skillC" in s["instance_local_skill"])
        case("skillD (template-only skill dir) -> missing_instance_skill", "skillD" in s["missing_instance_skill"])

        # render both output modes and make sure they don't raise / produce something non-trivial
        text = render_text(report)
        case("render_text produces non-empty plain-English report", isinstance(text, str) and "SUMMARY" in text)
        blob = json.dumps(report, indent=2)
        case("report is JSON-serializable", isinstance(blob, str) and len(blob) > 0)

        # descriptors/ size-gate exclusion, isolated fixture
        big_fork, big_tmpl = _build_oversize_descriptors_case(base)
        big_report = compare_deploy(big_fork, big_tmpl)
        excluded_note_hit = any("huge.yaml" not in "" and "descriptors" in n for n in big_report["excluded_notes"])
        case("oversize descriptors/ excluded (not compared, exclusion noted)",
             "descriptors/huge.yaml" not in big_report["differs"]
             and "descriptors/huge.yaml" not in big_report["missing_in_template"]
             and excluded_note_hit)

        # load_template_path degrade paths
        empty_dir = os.path.join(base, "no_project_yaml")
        os.makedirs(empty_dir, exist_ok=True)
        tp, reason = load_template_path(empty_dir)
        case("load_template_path degrades cleanly when project.yaml absent", tp is None and reason)

        with_yaml_no_key = os.path.join(base, "project_no_key")
        os.makedirs(with_yaml_no_key, exist_ok=True)
        _write(os.path.join(with_yaml_no_key, "project.yaml"), "project_name: x\n")
        tp2, reason2 = load_template_path(with_yaml_no_key)
        case("load_template_path degrades cleanly when template_path key absent", tp2 is None and reason2)

        with_key = os.path.join(base, "project_with_key")
        os.makedirs(with_key, exist_ok=True)
        _write(os.path.join(with_key, "project.yaml"), "template_path: \"%s\"\n" % tmpl_root.replace("\\", "/"))
        if yaml is not None:
            tp3, reason3 = load_template_path(with_key)
            case("load_template_path reads a present template_path key", tp3 == tmpl_root.replace("\\", "/") and reason3 is None)
        else:
            case("load_template_path PyYAML-absent path (PyYAML not installed here; skipped as N/A)", True)

    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failed:
        print("check-parity self-test: FAIL (%d/%d)" % (total - failed, total))
        return 1
    print("check-parity self-test: PASS (%d/%d)" % (total, total))
    return 0


# ---------------------------------------------------------------------------

def _arg_value(args, flag):
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return None


def main(argv):
    args = argv[1:]
    if "--self-test" in args:
        return self_test()

    json_out = "--json" in args
    fork_root = _arg_value(args, "--fork-root") or "."
    template_root = _arg_value(args, "--template-root")

    if template_root is None:
        template_root, reason = load_template_path(fork_root)
        if template_root is None:
            _note("template_path not set -- parity unwatched (%s)" % reason, json_out)
            return 0
    if not os.path.isdir(template_root):
        _note("template_path not set -- parity unwatched (template root not found: %s)" % template_root, json_out)
        return 0

    report = build_report(fork_root, template_root)
    if json_out:
        print(json.dumps(report, indent=2))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
