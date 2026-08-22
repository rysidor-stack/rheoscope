#!/usr/bin/env python3
"""doctor.py -- unified environment-readiness sensor (template self-truth, W2, backlog v3.0-26).

A fresh instantiation can look green in the transcript while codex is unauthenticated, jq is
missing, the security hooks were never wired, a superseded skill (grill) shadows its successor
(preflight), or a knowledge-os sensor silently fails its own self-test. None of that surfaces
until something breaks live. /doctor makes it visible on demand.

Assumes cwd is the ROOT of an INSTANTIATED project (post-init.sh/init.ps1): .claude/skills/,
.claude/settings.local.json, and (if knowledge-os is enabled) deploy/ live at the project root,
not under core/ -- core/skills/ is consumed and deleted by init. Pass --root to point elsewhere
(e.g. the template repo itself, where most checks correctly FAIL or SKIP: it isn't instantiated).

Doctrine: run ALL checks always, never stop at the first failure; every FAIL/WARN carries an
actionable "FIX:" line inline (errors teach, never a bare exit code); a check whose subject is
absent BY DESIGN (e.g. no deploy/ because knowledge-os isn't enabled) SKIPs with a reason, not a
FAIL; in normal readiness-check mode doctor writes nothing -- it does INVOKE each deploy
sensor's --self-test and check-derivation --gate, which are contract-bound to be
side-effect-free (embedded fixtures / report-only scans); doctor cannot independently
guarantee that of a locally modified sensor. --self-test mode writes only its own fixture
files inside an auto-cleaned temporary directory, never the project tree.

Checks (harness-v3.0/specs/template-self-truth-and-onboarding-brief-2026-07-10.md §W2, W5):
  1. bridge-wired     .claude/skills/bridge/verify-cli.js exists.
  2. node             node on PATH, `node --version` >= 18.
  3. bridge-cli       `node verify-cli.js --help` exits 0 (SKIP if #1 or #2 failed).
  4. codex-auth       codex on PATH AND authenticated (`codex login status`).
  5. jq               jq on PATH (runtime dep of the security hook scripts).
  6. python-sensors   every deploy/*.py advertising --self-test passes it.
  7. hooks-wired      .claude/settings.local.json exists, is valid JSON, references both
                      block-dangerous-bash.sh and block-env-writes.sh, AND wires
                      block-dangerous-bash.sh under both a "Bash" and a "PowerShell" matcher
                      (Claude Code's PowerShell tool is a separate code path from Bash --
                      a Bash-only matcher leaves it completely unmediated even though the
                      script's own deny patterns already cover PowerShell command forms;
                      see core/security/hooks/README.md) and block-env-writes.sh under an
                      Edit/Write matcher.
  8. skill-drift      superseded-skill probe (grill/preflight class).
  9. derivation-gate  deploy/check-derivation.py --gate, if present.
  10. docs-stamps     teaching docs (TOUR/GLOSSARY/SYSTEM-MAP/OPERATIONS/orient SKILL.md,
                      manifest doctrine/format, conformance SKILL.md, root ARCHITECTURE.md)
                      carry a `verified-against: <VERSION> (<date>)` stamp matching this
                      instance's project.yaml template_version (docs-truth discipline, §W5;
                      root ARCHITECTURE.md added per backlog v3.0-75 -- migration never
                      refreshed it, so instances carried instantiation-day copies forever).
  11. version-drift   deploy/environment-manifest.yaml, if present: run each row's probe,
                      compare its output to the row's recorded version_verified (tolerant
                      contains/normalized comparison). Absent manifest or PyYAML -> SKIP
                      ("version drift unwatched"), never FAIL; unreachable probe or drifted
                      version -> WARN naming both versions and the verified date (session-C
                      build decision D5 -- dev-repo design record, stated in full here).
  12. sensor-reachability  every deploy/*.py reachable from an executable surface
                      (backlog v3.0-80; the dormant register retired 2026-08-08 -- the
                      dev-only drills it excused no longer ship).
  13. skill-adapters  deploy/gen-skill-adapters.py --check, when wired (backlog v3.0-79).
  15. precommit-scanner  (v3.0-112, 2026-08-17) the commit scanner is INSTALLED at
                      .git/hooks/pre-commit and byte-current with the shipped copy (or a
                      deliberate chain naming it); WARN when absent/stale -- an instance
                      that ran init before `git init`, or skipped a MIGRATION reinstall,
                      otherwise runs unscanned forever with a green doctor.
  16. trust-surfaces  (v3.0.46, backlog v3.0-120) the trust-surface class (core/security/
                      hooks/trust-surfaces.txt + the embedded floor) is what it was committed
                      and signed to be: (a) every tracked member HEAD-identical, (b) every
                      member's newest commit operator-signed per deploy/trust.py (FAIL under
                      trust_surface_signing: required, WARN under warn), (c) the untracked
                      hook-lane members wire the perimeter (checks 7 + 15), (d) every key in
                      allowed_signers is presence-requiring (sk), (e) no retire record
                      without a verified operator tag. Stated in the check's own text: it
                      runs in-process and is in the class -- a tampered doctor can lie; the
                      root of trust is the operator's signature, never this check.
  14. corpus-reachability  (v3.0.18, backlog v3.0-88) every execution corpus declared in
                      project.yaml -- the corpus_sources list, or the legacy singular
                      corpus_source + corpus_config -- is present and readable at its
                      clone_path (`git rev-parse <branch>`, read-only). One result per
                      corpus: a declared-but-unreachable corpus FAILs (partial observation
                      is never silent); both binding forms declared at once FAILs as a
                      config error; no binding declared -> SKIP.

Usage: doctor.py [--root PATH] | doctor.py --self-test (embedded fixtures; no live
node/codex/jq required).

Exit codes: 0 = all PASS/SKIP (WARNs alone still exit 0) | 1 = self-test failure | 2 = a FAIL.
"""

import argparse
import datetime as _dt
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# TEST-ONLY / OPTIONAL: PyYAML is not a runtime dependency of the rest of doctor.py -- only
# check_version_drift() needs it, to parse deploy/environment-manifest.yaml. Same guard
# pattern the deploy sensors use (e.g. deploy/check-caps.py): import once at module level,
# degrade (never crash) if it's absent.
try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# The grill -> preflight class (2026-07-09 rename). Extend this map as future core skills
# supersede a capability-fork sibling; each entry is checked independently.
# 2026-07-31 (v3.0-78 handoff collapse): handoff-author + handoff-receive both
# collapsed into the single /handoff entry point (answer legs ride the bridge;
# handoff-close survives as the close leg's protocol document, so it is NOT in
# this map).
SUPERSEDED = {
    "grill": "preflight",
    "handoff-author": "handoff",
    "handoff-receive": "handoff",
}

HOOK_SCRIPTS = ("block-dangerous-bash.sh", "block-env-writes.sh")

# The docs-truth stamp discipline (§W5): teaching docs carry a `verified-against: <VERSION>
# (<date>)` line near the top. This is the fixed candidate list of INSTANCE paths (post-init
# runtime locations, not the template's capabilities/extracted/ source paths) -- not every
# instance ships every doc (e.g. no docs/engine/OPERATIONS.md if knowledge-os isn't enabled),
# so a candidate's absence is silent, never a per-file SKIP.
STAMPED_DOCS = (
    "core/onboarding/TOUR.md",
    "core/onboarding/GLOSSARY.md",
    "core/onboarding/SYSTEM-MAP.html",
    "docs/engine/OPERATIONS.md",
    ".claude/skills/orient/SKILL.md",
    "core/methodology/manifest-driven-builds.md",
    "core/methodology/manifest-format.md",
    ".claude/skills/conformance/SKILL.md",
    # Root ARCHITECTURE.md (backlog v3.0-75): instantiation copies it in and no migration
    # step ever refreshed it, so an instance several releases along still described the
    # harness it was born with. The stamp makes that drift visible here.
    "ARCHITECTURE.md",
    # 2026-08-05 (silence-sweep drift cluster 6): three docs carried stamps this list
    # never watched, so their stamps could rot unnoticed -- the exact state this
    # check exists to prevent.
    "core/governance/WORKSPACE.md",
    ".claude/skills/standing-loop/SKILL.md",
    ".claude/skills/sweep/SKILL.md",
)

_STAMP_RE = re.compile(r"verified-against:\s*([^\s(]+)\s*\(([^)]+)\)")
_TEMPLATE_VERSION_RE = re.compile(r'(?m)^\s*template_version:\s*"?([^"\s#]+)"?')

class Result:
    __slots__ = ("status", "name", "detail")

    def __init__(self, status, name, detail=""):
        self.status = status  # PASS | FAIL | WARN | SKIP
        self.name = name
        self.detail = detail

    def line(self):
        return "[%s] %s: %s" % (self.status, self.name, self.detail) if self.detail \
            else "[%s] %s" % (self.status, self.name)

def _tail(text, n_lines=6, max_chars=400):
    """Last few lines of subprocess output, trimmed, for quoting in a FIX line."""
    text = (text or "").strip()
    if not text:
        return "(no output)"
    lines = text.splitlines()
    out = "\n    ".join(lines[-n_lines:])
    return out[-max_chars:] if len(out) > max_chars else out

def _run(cmd, timeout, cwd=None):
    """subprocess.run wrapper. Returns (returncode, combined_output); returncode is the
    string "TIMEOUT" on expiry, or None if the executable could not be launched at all.
    Never uses shell=True; cmd is always an argv list."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return "TIMEOUT", "timed out after %ss" % timeout
    except OSError as e:
        return None, str(e)

# --------------------------------------------------------------------------------------
# Individual checks. Each takes a ctx dict {"root": Path, "python": str} and returns a
# Result, or a list of Results for checks with a dynamic sub-item count.
# --------------------------------------------------------------------------------------

def check_bridge_wired(ctx):
    path = ctx["root"] / ".claude" / "skills" / "bridge" / "verify-cli.js"
    if path.is_file():
        return Result("PASS", "bridge-wired", str(path))
    return Result("FAIL", "bridge-wired",
                  "missing .claude/skills/bridge/verify-cli.js. "
                  "FIX: re-run init (init.sh / init.ps1) and accept the bridge capability, "
                  "or copy core/skills/bridge/ to .claude/skills/bridge/ by hand.")

def _parse_node_major(version_text):
    """'v24.18.0\\n' -> 24. Pure function so it's testable without invoking node."""
    text = (version_text or "").strip()
    if text.startswith("v"):
        text = text[1:]
    head = text.split(".", 1)[0]
    return int(head) if head.isdigit() else None

def check_node(ctx):
    exe = shutil.which("node")
    if not exe:
        return Result("FAIL", "node",
                       "node not found on PATH. FIX: install node >= 18 "
                       "(https://nodejs.org, or via nvm/winget/brew).")
    rc, out = _run([exe, "--version"], timeout=10)
    if rc != 0:
        return Result("FAIL", "node",
                       "`node --version` failed (exit %s): %s FIX: reinstall node >= 18."
                       % (rc, _tail(out)))
    major = _parse_node_major(out)
    if major is None:
        return Result("WARN", "node",
                       "could not parse `node --version` output: %r. "
                       "FIX: verify manually with `node --version`." % out.strip())
    if major < 18:
        return Result("FAIL", "node",
                       "node %s found, but >= 18 is required. FIX: upgrade node to >= 18."
                       % out.strip())
    return Result("PASS", "node", "%s on PATH (%s)" % (out.strip(), exe))

def check_bridge_cli(ctx, bridge_result, node_result):
    if bridge_result.status != "PASS" or node_result.status != "PASS":
        return Result("SKIP", "bridge-cli",
                       "prerequisite check failed (bridge-wired and/or node); "
                       "skipping the --help invocation.")
    node_exe = shutil.which("node")
    bridge_path = ctx["root"] / ".claude" / "skills" / "bridge" / "verify-cli.js"
    rc, out = _run([node_exe, str(bridge_path), "--help"], timeout=15)
    if rc == 0:
        return Result("PASS", "bridge-cli", "verify-cli.js --help exited 0")
    return Result("FAIL", "bridge-cli",
                   "verify-cli.js --help exited %s: %s "
                   "FIX: inspect the stderr above; the bridge script or node install is broken."
                   % (rc, _tail(out)))

_CODEX_UNRECOGNIZED_MARKERS = (
    "unrecognized", "unknown subcommand", "unknown command",
    "no such subcommand", "invalid subcommand", "error: unrecognized",
)

def _classify_codex(rc, out):
    """Pure function: (returncode, combined output) of `codex login status` -> (status, detail).
    Split out from check_codex_auth so self-test can exercise it without a real codex binary."""
    if rc == 0:
        return "PASS", "authenticated (%s)" % _tail(out, n_lines=1)
    lower = (out or "").lower()
    if any(marker in lower for marker in _CODEX_UNRECOGNIZED_MARKERS):
        return "WARN", ("codex present, auth state unverifiable "
                         "(login status subcommand not recognized) -- run: codex login")
    return "FAIL", ("codex present but not authenticated (login status exit %s): %s "
                     "FIX: run: codex login" % (rc, _tail(out)))

def check_codex_auth(ctx):
    exe = shutil.which("codex")
    if not exe:
        return Result("FAIL", "codex-auth",
                       "codex not found on PATH. "
                       "FIX: install the codex CLI (requires a ChatGPT subscription), "
                       "then run: codex login")
    rc, out = _run([exe, "login", "status"], timeout=30)
    if rc == "TIMEOUT":
        return Result("WARN", "codex-auth",
                       "`codex login status` timed out after 30s -- auth state unverifiable. "
                       "FIX: run manually: codex login")
    if rc is None:
        return Result("FAIL", "codex-auth",
                       "failed to execute codex (%s). FIX: reinstall the codex CLI." % out)
    status, detail = _classify_codex(rc, out)
    return Result(status, "codex-auth", detail)

def _jq_install_hint():
    if sys.platform == "win32":
        return "winget install jqlang.jq  (or: choco install jq)"
    if sys.platform == "darwin":
        return "brew install jq"
    return "apt install jq  (or dnf install jq / pacman -S jq, per your distro)"

def check_jq(ctx):
    exe = shutil.which("jq")
    if exe:
        return Result("PASS", "jq", "on PATH (%s)" % exe)
    return Result("FAIL", "jq",
                   "jq not found on PATH (runtime dependency of the security hook scripts). "
                   "FIX: %s" % _jq_install_hint())

def check_python_sensors(ctx):
    deploy_dir = ctx["root"] / "deploy"
    if not deploy_dir.is_dir():
        return [Result("SKIP", "python-sensors",
                        "no deploy/ directory (knowledge-os capability not enabled "
                        "for this project).")]
    py_files = sorted(deploy_dir.glob("*.py"))
    out_results = []
    # v3.0.27 (plain-language sweep S5): --fast-selftests runs a deterministic,
    # date-keyed ROTATION instead of the full battery -- the every-session-open /sweep
    # invocation was re-proving all 63 embedded fixtures against files that had not
    # changed, at up to 60s each. The rotation is stated loudly (below), never silent:
    # every sensor still runs at least once per 7 calendar days, a full battery is one
    # `--full` flag away, and init-end + manual doctor runs stay FULL by default --
    # what a PASS attests is printed, never quietly narrowed.
    rotation_note = None

    def _advertises_selftest(path):
        try:
            return "--self-test" in path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return True   # unreadable: keep it in scope so the main loop FAILs it loudly

    selftest_files = [f for f in py_files if _advertises_selftest(f)]
    run_set = set(selftest_files)
    if ctx.get("fast_selftests") and len(selftest_files) > 7:
        day = _dt.date.today().toordinal()
        cycle = 7
        run_set = {f for i, f in enumerate(selftest_files) if i % cycle == day % cycle}
        rotation_note = (
            "fast mode: ran %d of %d self-test(s) today (date-keyed rotation -- every "
            "sensor runs at least once a week; run without --fast-selftests for the "
            "full battery, which init and manual checkups always use)"
            % (len(run_set), len(selftest_files)))
    for f in py_files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            out_results.append(Result("FAIL", "python-sensors:%s" % f.name,
                                       "could not read file: %s "
                                       "FIX: check the file's permissions and encoding; if it "
                                       "is corrupted, re-copy deploy/ from the template's "
                                       "knowledge-os capability "
                                       "(capabilities/knowledge-os/extracted/deploy/)." % e))
            continue
        if "--self-test" not in text:
            continue  # doesn't advertise a self-test; not this check's business
        if f not in run_set:
            continue  # fast-mode rotation: not this file's day (stated in the note below)
        # Most sensors' self-tests finish in well under 60s. A few engine drills are
        # legitimately heavier (e.g. drill-replay-bench.py's generator+replay bench can
        # run 30-90s) -- give those a longer budget instead of raising the default for
        # every sensor.
        sensor_timeout = 180 if f.name == "drill-replay-bench.py" else 60
        rc, out = _run([ctx["python"], str(f), "--self-test"], timeout=sensor_timeout,
                        cwd=str(ctx["root"]))
        name = "python-sensors:%s" % f.name
        if rc == 0:
            out_results.append(Result("PASS", name, "self-test passed"))
        elif rc == "TIMEOUT":
            out_results.append(Result("FAIL", name,
                                       "self-test timed out. FIX: investigate %s for a hang."
                                       % f.name))
        else:
            out_results.append(Result("FAIL", name,
                                       "self-test exited %s: %s "
                                       "FIX: run `python %s --self-test` directly and fix "
                                       "the failing case(s)." % (rc, _tail(out), f)))
    if not out_results:
        return [Result("SKIP", "python-sensors",
                        "deploy/ present but no *.py advertises --self-test "
                        "(%d file(s) scanned)." % len(py_files))]
    if rotation_note:
        out_results.insert(0, Result("PASS", "python-sensors", rotation_note))
    return out_results

def _matcher_tokens_for_script(hooks_cfg, script_name):
    """Every individual matcher TOKEN referencing script_name, token-exact rather than
    substring. A single entry's matcher can be a regex alternation like "Bash|PowerShell"
    covering both tools at once -- this splits on "|" and strips whitespace around each
    token, so "Bash|PowerShell" yields the token set {"Bash", "PowerShell"}. Callers test
    exact set membership (e.g. "Bash" in tokens), never substring containment: a matcher
    string like "NotBash" yields the single token "NotBash", which does NOT equal "Bash"
    and so does NOT satisfy a Bash requirement (round-1 cross-check finding: the prior
    substring form, "Bash" in m, would have let "NotBash" false-positive as Bash coverage).
    """
    out = set()
    entries = hooks_cfg.get("PreToolUse", []) if isinstance(hooks_cfg, dict) else []
    if not isinstance(entries, list):
        return out
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for h in entry.get("hooks", []) or []:
            if isinstance(h, dict) and script_name in str(h.get("command", "")):
                matcher = str(entry.get("matcher", ""))
                for tok in matcher.split("|"):
                    tok = tok.strip()
                    if tok:
                        out.add(tok)
                break
    return out

def check_hooks_wired(ctx):
    path = ctx["root"] / ".claude" / "settings.local.json"
    if not path.is_file():
        return Result("FAIL", "hooks-wired",
                       "missing .claude/settings.local.json. "
                       "FIX: copy core/security/settings.local.json.example -> "
                       ".claude/settings.local.json (or re-run init and accept the hooks "
                       "prompt).")
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, json.JSONDecodeError) as e:
        return Result("FAIL", "hooks-wired",
                       ".claude/settings.local.json exists but is not valid JSON: %s "
                       "FIX: repair it, or replace it from "
                       "core/security/settings.local.json.example." % e)
    missing = [h for h in HOOK_SCRIPTS if h not in text]
    if missing:
        return Result("WARN", "hooks-wired",
                       ".claude/settings.local.json present but missing hook reference(s): "
                       "%s. FIX: add the PreToolUse hook entries from "
                       "core/security/settings.local.json.example." % ", ".join(missing))

    hooks_cfg = data.get("hooks", {}) if isinstance(data, dict) else {}
    bash_tokens = _matcher_tokens_for_script(hooks_cfg, "block-dangerous-bash.sh")
    env_tokens = _matcher_tokens_for_script(hooks_cfg, "block-env-writes.sh")

    problems = []
    if "Bash" not in bash_tokens:
        problems.append('block-dangerous-bash.sh not wired under a "Bash" matcher')
    if "PowerShell" not in bash_tokens:
        problems.append('block-dangerous-bash.sh not wired under a "PowerShell" matcher '
                         '(PowerShell-tool calls are unmediated -- see '
                         'core/security/hooks/README.md)')
    if not ({"Edit", "Write"} & env_tokens):
        problems.append('block-env-writes.sh not wired under an Edit/Write matcher')

    if problems:
        return Result("WARN", "hooks-wired",
                       ".claude/settings.local.json references both hook scripts but matcher "
                       "coverage is incomplete: %s. FIX: mirror the PreToolUse entries from "
                       "core/security/settings.local.json.example." % "; ".join(problems))

    # v3.0.47 (v3.0-134, cross-vendor round-2 catch): a scheduled wrapper that does not set
    # the unattended marker runs with ATTENDED egress permissions (allow + log). Every
    # .cmd/.ps1/.sh wrapper under .claude/ must carry RHEOSCOPE_UNATTENDED.
    unmarked = []
    for w in sorted((ctx["root"] / ".claude").glob("*")):
        if w.suffix.lower() in (".cmd", ".bat", ".ps1", ".sh") and w.is_file():
            try:
                if "RHEOSCOPE_UNATTENDED" not in w.read_text(encoding="utf-8", errors="replace"):
                    unmarked.append(w.name)
            except OSError:
                unmarked.append(w.name + " (unreadable)")
    if unmarked:
        return Result("WARN", "hooks-wired",
                       "hooks wired, but scheduled wrapper(s) under .claude/ do not set the "
                       "unattended marker: %s -- a run they launch would allow-and-log egress as "
                       "if you were present instead of asking/failing closed. FIX: add "
                       "`set RHEOSCOPE_UNATTENDED=1` (cmd) / `$env:RHEOSCOPE_UNATTENDED=1` (ps1) / "
                       "`export RHEOSCOPE_UNATTENDED=1` (sh) as the wrapper's first line."
                       % ", ".join(unmarked))

    return Result("PASS", "hooks-wired",
                   "settings.local.json present, valid JSON, both hooks wired with correct "
                   "matcher coverage (Bash + PowerShell for block-dangerous-bash.sh, "
                   "Edit/Write for block-env-writes.sh)")


def check_precommit_scanner(ctx):
    """Check 15 (v3.0-112, 2026-08-17 defect-class hunt): the commit scanner must
    be INSTALLED and CURRENT, not just shipped. Before this check, an instance
    whose init ran before `git init`, or that skipped a MIGRATION reinstall step,
    ran no scanner (or a stale one) forever with 71 green checks. Compares the
    installed hook's bytes to the shipped source; a differing hook that mentions
    the scanner by name is treated as a deliberate chain, surfaced as a note.
    WARN, never FAIL -- the scanner is defense-in-depth, not project correctness."""
    src = ctx["root"] / "core" / "security" / "hooks" / "scan-staged-secrets.sh"
    if not src.is_file():
        return Result("SKIP", "precommit-scanner",
                       "core/security/hooks/scan-staged-secrets.sh not shipped in this "
                       "instance -- nothing to install (pre-v3.0.36 tree)")
    gitdir = ctx["root"] / ".git"
    if not gitdir.is_dir():
        return Result("WARN", "precommit-scanner",
                       "this folder is not a git repository, so the commit scanner cannot "
                       "be installed and NO commit is scanned. FIX: git init, then "
                       "cp core/security/hooks/scan-staged-secrets.sh .git/hooks/pre-commit")
    hook = gitdir / "hooks" / "pre-commit"
    if not hook.is_file():
        return Result("WARN", "precommit-scanner",
                       "no .git/hooks/pre-commit -- commits are NOT scanned for secrets "
                       "(the agent --no-verify bar is guarding a scanner that isn't there). "
                       "FIX: cp core/security/hooks/scan-staged-secrets.sh .git/hooks/pre-commit")
    try:
        same = hook.read_bytes() == src.read_bytes()
    except OSError as e:
        return Result("WARN", "precommit-scanner",
                       "could not read the installed hook: %s" % e)
    if same:
        return Result("PASS", "precommit-scanner",
                       "commit scanner installed at .git/hooks/pre-commit and byte-identical "
                       "to the shipped core/security/hooks copy")
    try:
        chained = "scan-staged-secrets" in hook.read_text(encoding="utf-8", errors="replace")
    except OSError:
        chained = False
    if chained:
        return Result("PASS", "precommit-scanner",
                       "pre-commit hook differs from the shipped scanner but references it "
                       "by name -- reading it as a deliberate chain. Keep the chained copy "
                       "current when adopting scanner updates.")
    return Result("WARN", "precommit-scanner",
                   ".git/hooks/pre-commit exists but is neither the shipped scanner nor a "
                   "chain referencing it -- commits are NOT scanned for secrets. FIX: chain "
                   "core/security/hooks/scan-staged-secrets.sh from your hook, or replace "
                   "the hook if it isn't yours on purpose (it may also simply be STALE: "
                   "re-copy after adopting a newer template).")

# The trust-surface class FLOOR (v3.0-120; the same list lives in both hooks and in
# deploy/trust.py). core/security/hooks/trust-surfaces.txt can only WIDEN it.
TRUST_SURFACE_FLOOR = (
    "core/security/hooks/**",
    "deploy/safe-allowlist.yaml",
    "deploy/evidence/operator-*.md",
    "deploy/rulings/**",
    "deploy/trust.py",
    "deploy/compile-driver.py",
    "deploy/compile-backends.py",
    "deploy/audit-content.py",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".git/hooks/**",
    ".gitattributes",
)
TRUST_SK_TYPES = ("sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com")
_TRUST_HONEST_NOTE = ("(This sensor runs in-process and is itself in the class: a tampered "
                      "doctor can lie. The root of trust is the operator's presence-requiring "
                      "signature on the publishing commit, not this check.)")


def _trust_glob_re(glob):
    out = "(^|/)"
    i = 0
    while i < len(glob):
        if glob.startswith("**", i):
            out += ".*"
            i += 2
            continue
        c = glob[i]
        out += "[^/]*" if c == "*" else ("[^/]" if c == "?" else re.escape(c))
        i += 1
    return re.compile(out + "$")


def _trust_class(root):
    globs = list(TRUST_SURFACE_FLOOR)
    p = Path(root) / "core" / "security" / "hooks" / "trust-surfaces.txt"
    try:
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.split("#", 1)[0].strip().replace("\\", "/")
            if line and line not in globs:
                globs.append(line)
    except OSError:
        pass
    return globs


def _git_out(root, *args):
    try:
        p = subprocess.run(["git", "--no-replace-objects", "-C", str(root)] + list(args),
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)
    return p.returncode, p.stdout


def check_trust_surfaces(ctx):
    """Check 16 (v3.0-120, brief section 6): the trust-surface class is what it was
    committed and signed to be. Five sub-checks, one Result each:
      (a) head-identity  every TRACKED class member is byte-identical to HEAD
                         (FAIL "uncommitted perimeter change" with the diff stat);
      (b) signatures     every tracked member's newest commit is operator-signed
                         (deploy/trust.py --report): FAIL under trust_surface_signing:
                         required, WARN under warn; WARN "unavailable" without trust.py;
      (c) wiring         the UNTRACKED members (.claude/settings.local.json,
                         .git/hooks/**) are hook-lane members: all three hook entries
                         wired (check 7) and the scanner byte-current (check 15), else
                         FAIL "perimeter unwired";
      (d) pin            every key in core/security/hooks/allowed_signers is sk-typed,
                         else FAIL "non-presence key listed"; absent pin -> WARN
                         (bootstrap ceremony pending);
      (e) retirements    any `retire` journal record whose commit carries no verified
                         operator tag -> FAIL "unpublished retirement present"."""
    root = Path(ctx["root"])
    fam = "trust-surfaces"
    if not (root / ".git").is_dir():
        return [Result("WARN", fam + ":head-identity",
                       "not a git repository -- no trust surface can be committed-identical "
                       "or signed, so every HUMAN-GATE consumer will refuse. FIX: git init "
                       "and commit the tree.")]
    out = []
    globs = _trust_class(root)
    regs = [_trust_glob_re(g) for g in globs]
    rc, ls = _git_out(root, "ls-files", "-z")
    tracked = [p for p in ls.split("\0") if p and any(r.search(p) for r in regs)] if rc == 0 else []

    # (a) HEAD-identity
    dirty = []
    for p in tracked:
        # RAW bytes vs the HEAD blob, CRLF tolerated (never `git diff`: clean filters and
        # attribute conversions could make a forged file look clean -- round-6 catch)
        try:
            proc = subprocess.run(["git", "--no-replace-objects", "-C", str(root), "cat-file",
                                   "blob", "HEAD:%s" % p], capture_output=True, timeout=60)
            blob = proc.stdout if proc.returncode == 0 else None
            raw = (root / p).read_bytes()
        except (OSError, subprocess.SubprocessError):
            blob, raw = None, None
        if blob is None or raw is None:
            dirty.append("%s (unreadable or absent from HEAD)" % p)
        elif raw != blob and raw.replace(b"\r\n", b"\n") != blob.replace(b"\r\n", b"\n"):
            _, stat = _git_out(root, "diff", "--stat", "HEAD", "--", p)
            summary = (stat.strip().splitlines() or [""])[-1].strip()
            dirty.append("%s (%s)" % (p, summary.split(", ", 1)[-1] if summary else
                                      "raw bytes differ; git diff reports clean -- a clean "
                                      "filter/attribute conversion is in play"))
    if dirty:
        out.append(Result("FAIL", fam + ":head-identity",
                          "uncommitted perimeter change in %d trust surface(s): %s. An "
                          "unsigned working-tree edit to a trust surface is non-authoritative "
                          "-- every honest consumer refuses it. FIX: if the operator made it, "
                          "commit it with `git commit -S` (sk key); if they did not, `git "
                          "checkout -- <path>` restores the committed bytes and the session "
                          "that wrote it is the finding. %s"
                          % (len(dirty), "; ".join(dirty), _TRUST_HONEST_NOTE)))
    else:
        out.append(Result("PASS", fam + ":head-identity",
                          "%d tracked trust surface(s) byte-identical to HEAD. %s"
                          % (len(tracked), _TRUST_HONEST_NOTE)))

    # (d) the pin
    rc, pin = _git_out(root, "show", "HEAD:core/security/hooks/allowed_signers")
    if rc != 0:
        out.append(Result("WARN", fam + ":pin",
                          "core/security/hooks/allowed_signers is not committed -- no operator "
                          "signature can verify yet, so consumers run in cutover (warn) at "
                          "best. FIX: the one-time ceremony in MIGRATION v3.0.45->v3.0.46: "
                          "list your sk public key(s), commit with `git commit -S`."))
    else:
        non_sk, keys = [], 0
        for line in pin.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            toks = line.split()
            kt = next((t for t in toks[1:] if re.match(r"^(sk-)?(ssh-|ecdsa-)", t)), None)
            if kt is None:
                continue
            keys += 1
            if kt not in TRUST_SK_TYPES:
                non_sk.append("%s (%s)" % (toks[0], kt))
        if non_sk:
            out.append(Result("FAIL", fam + ":pin",
                              "non-presence key listed in allowed_signers: %s. A software key "
                              "can be used by anything holding the agent socket, so it is "
                              "ignored by every verifier and must not be listed. FIX: remove "
                              "the line; list only sk-ssh-ed25519@openssh.com / "
                              "sk-ecdsa-sha2-nistp256@openssh.com keys; commit with -S."
                              % ", ".join(non_sk)))
        elif keys == 0:
            out.append(Result("FAIL", fam + ":pin",
                              "allowed_signers is committed but lists no key. FIX: add the "
                              "operator's sk public key line and commit with -S."))
        else:
            out.append(Result("PASS", fam + ":pin",
                              "%d presence-requiring key(s) pinned, no software keys" % keys))

    # (b) + (e) via deploy/trust.py
    trust_py = root / "deploy" / "trust.py"
    verifier_dirty = any(d.startswith("deploy/trust.py ") for d in dirty)
    if trust_py.is_file() and verifier_dirty:
        # (b)/(e) read THROUGH deploy/trust.py. A working-tree patch to it is caught by (a)
        # above; running the patched copy would let it lie about signatures and
        # retirements, so refuse to consult it. (A COMMITTED patch is HEAD-identical and
        # cannot be told apart from inside the repo -- that commit is unsigned, and the
        # root remains the operator's signature, never this sensor.)
        out.append(Result("FAIL", fam + ":signatures",
                          "verifier tampered: deploy/trust.py differs from HEAD, so signature "
                          "and retirement status are UNKNOWABLE from inside this repo. FIX: "
                          "restore it (`git checkout -- deploy/trust.py`) and re-run; treat the "
                          "session that patched it as the finding."))
        out.append(Result("FAIL", fam + ":retirements",
                          "verifier tampered: see trust-surfaces:signatures. FIX: restore "
                          "deploy/trust.py, then re-run."))
    elif not trust_py.is_file():
        out.append(Result("WARN", fam + ":signatures",
                          "deploy/trust.py absent -- signature verification unavailable "
                          "(knowledge-os not enabled, or pre-v3.0.46 deploy/). Head-identity "
                          "and wiring still checked. FIX: adopt v3.0.46's deploy/trust.py."))
    else:
        rc, rep_text = _run([ctx["python"], str(trust_py), "--root", str(root), "--report",
                             "--json"], timeout=180, cwd=str(root))
        try:  # _run combines stdout+stderr; the report is the outermost JSON object
            rep = json.loads(rep_text[rep_text.index("{"):rep_text.rindex("}") + 1])
        except (ValueError, TypeError):
            rep = None
        if not isinstance(rep, dict):
            out.append(Result("FAIL", fam + ":signatures",
                              "deploy/trust.py --report --json produced no report (rc %s): %s "
                              "FIX: run it by hand; a crashing verifier is itself a finding."
                              % (rc, _tail(rep_text))))
        else:
            mode = rep.get("mode", "warn")
            unsigned = [r for r in rep.get("surfaces", []) if not r.get("signed")]
            if mode == "visible":
                out.append(Result("PASS", fam + ":signatures",
                                  "trust_surface_signing: visible -- no signature expected; %d "
                                  "surface(s) tracked, %d carry an operator signature anyway. "
                                  "Authority here is reversible-and-visible (sweep step 17 shows "
                                  "every change)." % (len(rep.get("surfaces", [])),
                                                      len(rep.get("surfaces", [])) - len(unsigned))))
            elif unsigned:
                detail = "; ".join("%s: %s" % (r["path"], r.get("reason")) for r in unsigned[:6])
                more = "" if len(unsigned) <= 6 else " (+%d more)" % (len(unsigned) - 6)
                status = "FAIL" if mode == "required" else "WARN"
                out.append(Result(status, fam + ":signatures",
                                  "%d trust surface(s) not operator-signed under "
                                  "trust_surface_signing: %s -- %s%s. FIX: the operator "
                                  "re-commits each with `git commit -S` (sk key); under warn "
                                  "this is surfaced, under required every consumer refuses."
                                  % (len(unsigned), mode, detail, more)))
            else:
                out.append(Result("PASS", fam + ":signatures",
                                  "%d trust surface(s) operator-signed (mode %s)"
                                  % (len(rep.get("surfaces", [])), mode)))
            unpub = [r for r in rep.get("retire_records", []) if not r.get("published")]
            rewind = rep.get("branch_rewind") or []
            if rewind:
                out.append(Result("FAIL", fam + ":retirements",
                                  "history rewound: %s. A forced STALE retirement (a signed C whose "
                                  "base was superseded, moved onto the branch) looks exactly like "
                                  "this and is invisible to a branch-only reader. FIX: compare "
                                  "with the remote (`git log --oneline origin/<branch>..<branch>` "
                                  "and the reverse); restore the branch; treat the session as the "
                                  "finding." % "; ".join(rewind)))
            elif unpub:
                out.append(Result("FAIL", fam + ":retirements",
                                  "unpublished retirement present: %s. A retire record whose "
                                  "commit carries no verified operator tag is a proposal, not "
                                  "a retirement -- if you did not sign it, an agent journaled it "
                                  "by an alternate write path. FIX: inspect with `git show`; "
                                  "either sign the tag (`git tag -s retire/<seq> <C>`) or revert "
                                  "the record." % "; ".join("%s seq %s (%s)" % (
                                      r["path"], r.get("seq"), r.get("reason")) for r in unpub)))
            else:
                out.append(Result("PASS", fam + ":retirements",
                                  "%d retire record(s), all carry a verified operator tag"
                                  % len(rep.get("retire_records", []))))

    # (c) wiring of the untracked members
    hw = check_hooks_wired(ctx)
    sc = check_precommit_scanner(ctx)
    if hw.status == "PASS" and sc.status == "PASS":
        out.append(Result("PASS", fam + ":wiring",
                          "untracked members wired: settings wire all three hook entries, "
                          "scanner byte-current"))
    else:
        out.append(Result("FAIL", fam + ":wiring",
                          "perimeter unwired: hooks-wired=%s (%s); precommit-scanner=%s (%s). "
                          "The untracked trust surfaces (.claude/settings.local.json, "
                          ".git/hooks/**) cannot be signed, so their only check is that they "
                          "wire the perimeter. FIX: see the two checks' own FIX lines."
                          % (hw.status, hw.detail.split(". FIX")[0][:140], sc.status,
                             sc.detail.split(". FIX")[0][:140])))
    return out


def check_skill_drift(ctx):
    skills_dir = ctx["root"] / ".claude" / "skills"
    results = []
    for old, new in SUPERSEDED.items():
        old_exists = (skills_dir / old).exists()
        new_exists = (skills_dir / new).exists()
        name = "skill-drift:%s" % old
        if old_exists:
            results.append(Result("FAIL", name,
                                   "stale superseded skill .claude/skills/%s installed "
                                   "alongside its successor. FIX: delete .claude/skills/%s "
                                   "(/%s replaced it 2026-07-09)." % (old, old, new)))
        elif not new_exists:
            results.append(Result("WARN", name,
                                   "successor .claude/skills/%s (of %s) is not installed. "
                                   "FIX: verify this project's capability wiring installed "
                                   "/%s, or re-run init." % (new, old, new)))
        else:
            results.append(Result("PASS", name,
                                   "no stale %s; successor %s present." % (old, new)))
    return results

def check_derivation_gate(ctx):
    path = ctx["root"] / "deploy" / "check-derivation.py"
    if not path.is_file():
        return Result("SKIP", "derivation-gate",
                       "no deploy/check-derivation.py (knowledge-os not enabled, "
                       "or the sensor is not wired for this project).")
    rc, out = _run([ctx["python"], str(path), "--gate"], timeout=60, cwd=str(ctx["root"]))
    if rc == 0:
        return Result("PASS", "derivation-gate", "no audit-pending T1 views")
    if rc == 2:
        return Result("FAIL", "derivation-gate",
                       "audit-pending T1 view(s) present. "
                       "FIX: run the adversarial verify loop before building on those views. "
                       "%s" % _tail(out))
    if rc == 3:
        # The sensor's fail-honest tree-not-located code (silence-sweep S2): no wiki/
        # under its resolved root. Neither a pass (nothing was verified) nor the
        # audit-pending FAIL (nothing was found) -- state is simply unverified.
        return Result("WARN", "derivation-gate",
                       "derivation gate could not locate the wiki tree -- state unverified. "
                       "FIX: if this project keeps a knowledge corpus, check that wiki/ exists "
                       "next to deploy/ (or run `python deploy/check-derivation.py --root "
                       "<tree>` by hand); if knowledge-os content was never populated, this "
                       "warning is the honest report of that. %s" % _tail(out))
    if rc == "TIMEOUT":
        return Result("WARN", "derivation-gate",
                       "check-derivation.py --gate timed out; state unverified. "
                       "FIX: run it manually.")
    return Result("WARN", "derivation-gate",
                   "check-derivation.py --gate exited unexpected code %s: %s "
                   "FIX: run `python deploy/check-derivation.py` directly to see the full "
                   "error; the sensor may be from a different schema version -- re-sync "
                   "deploy/ from the template (capabilities/knowledge-os/extracted/deploy/)."
                   % (rc, _tail(out)))

def _extract_stamp(root, rel_path):
    """Read an instance file and pull its `verified-against: <VERSION> (<date>)` stamp, if
    any. Returns (version, date) strs, or None if the file is unreadable or carries no stamp.
    Plain regex over the whole file works uniformly across .md and .html candidates -- the
    stamp text is literal in both (a <span> in the HTML case). Not windowed to a fixed number
    of leading lines: SYSTEM-MAP.html's stamp renders in the page hero, which sits well past
    its embedded <style> block (observed at line 205-293 depending on build) -- a "first N
    lines" cutoff would permanently miss it. All 5 candidates are modest-sized docs (<1000
    lines), so a full-file scan costs nothing meaningful."""
    path = root / rel_path
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _STAMP_RE.search(text)
    return (m.group(1).strip(), m.group(2).strip()) if m else None

def _read_instance_template_version(root):
    """Leniently parse `template_version:` out of the instance's project.yaml -- regex only,
    no YAML dependency (project.yaml.example documents the same field this reads). Returns the
    version string, or None if project.yaml is absent, unreadable, or has no matching line."""
    path = root / "project.yaml"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _TEMPLATE_VERSION_RE.search(text)
    return m.group(1).strip() if m else None

def check_docs_stamps(ctx):
    root = ctx["root"]
    present = [p for p in STAMPED_DOCS if (root / p).is_file()]
    if not present:
        return [Result("SKIP", "docs-stamps",
                        "none of the %d candidate teaching docs are present in this instance "
                        "(not every project ships every doc -- e.g. no docs/engine/OPERATIONS.md "
                        "without knowledge-os)." % len(STAMPED_DOCS))]
    current_version = _read_instance_template_version(root)
    results = []
    for rel in present:
        name = "docs-stamps:%s" % rel
        stamp = _extract_stamp(root, rel)
        if stamp is None:
            results.append(Result("WARN", name,
                "%s has no `verified-against:` stamp anywhere in the file. "
                "FIX: add a `verified-against: <VERSION> (<date>)` line near the top, matching "
                "the convention in core/onboarding/TOUR.md -- see the docs-truth discipline "
                "(HARNESS-CHANGELOG.md v3.0, W5)." % rel))
            continue
        ver, date = stamp
        if current_version is None:
            results.append(Result("PASS", name,
                "verified-against: %s (%s); version comparison skipped -- this instance's "
                "project.yaml is absent or unreadable, so template_version could not be read."
                % (ver, date)))
        elif ver != current_version:
            results.append(Result("WARN", name,
                "doc verified against %s, this instance is %s -- re-verify and re-stamp. "
                "FIX: re-read %s against the current artifacts and update its "
                "`verified-against:` line; see MAINTENANCE.md (template repo root) for the "
                "re-verification procedure." % (ver, current_version, rel)))
        else:
            results.append(Result("PASS", name,
                "verified-against: %s (%s); matches this instance's template_version %s."
                % (ver, date, current_version)))
    return results

# deploy/environment-manifest.yaml schema (session-C build decision D5):
#   tools:
#     - tool: <name>                  # e.g. python, node, git, codex
#       probe: <shell command str>    # prints a version to stdout, e.g. "python --version"
#       version_verified: <string>    # the probe's captured first-line output, last verified
#       date: <string>                # when version_verified was captured (e.g. 2026-07-23)
_VERSION_PROBE_TIMEOUT = 10

def _normalize_version(text):
    """Lowercase + collapse whitespace, for a tolerant version comparison. Pure function so
    it's testable without a live probe. Intentionally loose: doctor is flagging *drift* for a
    human to glance at, not asserting semver equality -- a probe and a manifest row disagreeing
    on incidental formatting (extra whitespace, case) shouldn't manufacture a false WARN."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())

def _versions_match(probe_output, verified):
    """Tolerant contains-either-direction comparison of two normalized version strings."""
    a, b = _normalize_version(probe_output), _normalize_version(verified)
    if not a or not b:
        return False
    return a in b or b in a

def check_version_drift(ctx):
    manifest_path = ctx["root"] / "deploy" / "environment-manifest.yaml"
    if not manifest_path.is_file():
        return Result("SKIP", "version-drift",
                       "no environment manifest -- version drift unwatched. "
                       "FIX: none required; add deploy/environment-manifest.yaml (see "
                       "deploy/environment-manifest.yaml.example) to start tracking toolchain "
                       "drift.")
    if yaml is None:
        return Result("SKIP", "version-drift",
                       "deploy/environment-manifest.yaml present but PyYAML is not installed "
                       "-- version drift unwatched. FIX: pip install pyyaml.")
    try:
        doc = yaml.safe_load(manifest_path.read_text(encoding="utf-8-sig")) or {}
    except (OSError, yaml.YAMLError) as e:
        return Result("WARN", "version-drift",
                       "could not read/parse deploy/environment-manifest.yaml: %s "
                       "FIX: check the file's YAML syntax against "
                       "deploy/environment-manifest.yaml.example." % e)
    tools = doc.get("tools") or []
    if not tools:
        return Result("SKIP", "version-drift",
                       "deploy/environment-manifest.yaml present but lists no tools -- "
                       "version drift unwatched.")
    results = []
    for i, row in enumerate(tools):
        if not isinstance(row, dict) or not row.get("tool"):
            results.append(Result("WARN", "version-drift:row%d" % i,
                                   "manifest row %d is malformed (not a mapping, or missing "
                                   "'tool'). FIX: fix the row's shape in "
                                   "deploy/environment-manifest.yaml -- see "
                                   "deploy/environment-manifest.yaml.example." % i))
            continue
        tool = str(row["tool"])
        name = "version-drift:%s" % tool
        probe = row.get("probe")
        verified = row.get("version_verified")
        date = row.get("date") or "unknown date"
        if not probe:
            results.append(Result("WARN", name,
                                   "manifest row for %s has no probe command. "
                                   "FIX: add a probe (e.g. `%s --version`) to "
                                   "deploy/environment-manifest.yaml." % (tool, tool)))
            continue
        try:
            argv = shlex.split(probe)
        except ValueError as e:
            results.append(Result("WARN", name,
                                   "manifest row for %s has an unparseable probe %r: %s "
                                   "FIX: fix the probe command's quoting." % (tool, probe, e)))
            continue
        rc, out = _run(argv, timeout=_VERSION_PROBE_TIMEOUT, cwd=str(ctx["root"]))
        if rc is None or rc == "TIMEOUT" or rc != 0:
            results.append(Result("WARN", name,
                                   "tool unreachable -- probe `%s` did not run cleanly "
                                   "(%s) (recorded: %s, verified %s). FIX: verify %s is "
                                   "installed and on PATH, or fix the probe in "
                                   "deploy/environment-manifest.yaml."
                                   % (probe, _tail(out), verified or "not recorded", date,
                                      tool)))
            continue
        first_line = next(iter((out or "").strip().splitlines()), "")
        if not verified:
            results.append(Result("WARN", name,
                                   "manifest row for %s has no version_verified to compare "
                                   "against; probe reports %r. FIX: record the current probe "
                                   "output as version_verified in "
                                   "deploy/environment-manifest.yaml." % (tool, first_line)))
            continue
        if _versions_match(first_line, verified):
            results.append(Result("PASS", name,
                                   "%s matches verified %r (verified %s)"
                                   % (first_line, verified, date)))
        else:
            results.append(Result("WARN", name,
                                   "version drift: probe now reports %r, manifest "
                                   "version_verified is %r (verified %s). "
                                   "FIX: confirm the new version works, then update "
                                   "version_verified/date for %s in "
                                   "deploy/environment-manifest.yaml." % (first_line, verified,
                                                                            date, tool)))
    return results

# --------------------------------------------------------------------------------------
# Check 12: sensor-reachability (backlog v3.0-80). The first UPWARD check: every other
# check asks "does this unit still work"; this one asks "does anything still REACH it".
# A deploy sensor disconnected from every call site keeps passing its --self-test forever
# under check 6's blanket sweep -- a green gate that gates nothing -- which is how the
# built-but-unwired defect class (v3.0-25 hooks, check-derivation pre-v3.0-26, the
# v3.0-65 engine cluster, v3.0-79 skills) stayed invisible to /doctor by construction.
#
# Two-state since 2026-08-08: every deploy/*.py is either reachable from an executable
# surface or UNACCOUNTED -> WARN demanding a disposition. The dormant register
# (deploy/dormant-register.yaml, v3.0-80/v3.0.26) is RETIRED: its only rows were the
# template author's own engine drills and test batteries, which no longer ship in the
# release at all (make-release.py export exclusion, 2026-08-08 structural-audit
# remediation) -- a register whose every row excused a file that no longer exists is
# pure weight. The WARN never prescribes wiring: orphaned code has two causes
# (needed-but-unwired vs built-but-undemanded) and connecting a capability is a
# decision, not a fix. WARN tier only -- a new sensor must spend zero operator
# attention beyond the report line.
# --------------------------------------------------------------------------------------

def _reachability_surfaces(root):
    """Executable surfaces whose text counts as an invocation: skill protocols and their
    helper scripts, init scripts, scheduler .cmd wrappers, and deploy registers. Prose
    (evidence/, specs, receipts, wiki) is deliberately NOT a surface -- being described
    is not being invoked; the loose definition measured 0 orphans where the strict one
    measured 17."""
    surfaces = []
    skills_dir = root / ".claude" / "skills"
    if skills_dir.is_dir():
        for p in skills_dir.rglob("*"):
            if p.is_file() and p.suffix in (".md", ".py", ".js", ".cmd"):
                surfaces.append(p)
    for name in ("init.sh", "init.ps1", "init-validate.sh", "init-validate.ps1"):
        p = root / name
        if p.is_file():
            surfaces.append(p)
    claude_dir = root / ".claude"
    if claude_dir.is_dir():
        surfaces.extend(claude_dir.glob("*.cmd"))
    deploy = root / "deploy"
    if deploy.is_dir():
        for pat in ("*.yaml", "*.yml"):
            surfaces.extend(deploy.glob(pat))
    return surfaces

def check_sensor_reachability(ctx):
    root = ctx["root"]
    deploy = root / "deploy"
    if not deploy.is_dir():
        return Result("SKIP", "sensor-reachability",
                       "no deploy/ (knowledge-os capability not enabled)")
    scripts = sorted(p.name for p in deploy.glob("*.py") if p.is_file())
    if not scripts:
        return Result("SKIP", "sensor-reachability", "deploy/ holds no python scripts")

    surface_chunks = []
    for p in _reachability_surfaces(root):
        try:
            surface_chunks.append(p.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    surface = "\n".join(surface_chunks)

    bodies = {}
    for s in scripts:
        try:
            bodies[s] = (deploy / s).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            bodies[s] = ""

    # Entry points: named on a surface. Then transitive closure over deploy->deploy
    # references -- literal filename OR the underscore stem, which catches the dynamic
    # `_load_module("check-substrate.py", ...)` / import-by-filename pattern the deploy
    # tree uses. Known, accepted limitation (WARN tier): a filename BUILT from string
    # concatenation would evade the closure; none exist in this tree today.
    reach = {s for s in scripts if s in surface}
    frontier = list(reach)
    while frontier:
        body = bodies.get(frontier.pop(), "")
        for t in scripts:
            if t not in reach and (t in body or t[:-3].replace("-", "_") in body):
                reach.add(t)
                frontier.append(t)

    unaccounted = sorted(s for s in scripts if s not in reach)

    def _cap(names, n=10):
        return ", ".join(names[:n]) + (" (+%d more)" % (len(names) - n)
                                        if len(names) > n else "")

    if unaccounted:
        return Result("WARN", "sensor-reachability",
                       "%d installed script(s) are used by nothing. Nothing is broken -- "
                       "they are dead weight until someone either wires them in or "
                       "removes them [UNACCOUNTED]: %s. FIX: for each unaccounted "
                       "script, wire an invoker or delete it -- connecting a capability "
                       "is a decision, not a fix (v3.0-80)."
                       % (len(unaccounted), _cap(unaccounted)))
    return Result("PASS", "sensor-reachability",
                   "%d deploy script(s): %d reachable, 0 unaccounted"
                   % (len(scripts), len(reach & set(scripts))))

# --------------------------------------------------------------------------------------
# Check 13: skill-adapters (backlog v3.0-79). Non-Claude agents (Codex et al.) discover
# repository skills from .agents/skills/, not .claude/skills/. The June-2026 hand-built
# mirror drifted invisibly ("outside every sensor's scope" -- wiki/REVIEW.md); the
# replacement is a GENERATED, tracked adapter set (deploy/gen-skill-adapters.py), and
# this check runs its --check mode so adapter drift fails loud in the readiness report.
# --------------------------------------------------------------------------------------

def check_skill_adapters(ctx):
    path = ctx["root"] / "deploy" / "gen-skill-adapters.py"
    if not path.is_file():
        return Result("SKIP", "skill-adapters",
                       "no deploy/gen-skill-adapters.py (adapter generation not wired "
                       "for this project -- non-Claude agents rely on AGENTS.md prose "
                       "for skill discovery).")
    rc, out = _run([ctx["python"], str(path), "--check"], timeout=60,
                   cwd=str(ctx["root"]))
    if rc == 0:
        return Result("PASS", "skill-adapters", _tail(out, n_lines=1))
    if rc == 1:
        return Result("WARN", "skill-adapters",
                       "adapters out of sync with .claude/skills: %s "
                       "FIX: run `python deploy/gen-skill-adapters.py` and commit the "
                       "regenerated .agents/skills/ tree." % _tail(out))
    if rc == "TIMEOUT":
        return Result("WARN", "skill-adapters",
                       "gen-skill-adapters.py --check timed out; state unverified. "
                       "FIX: run it manually.")
    return Result("WARN", "skill-adapters",
                   "gen-skill-adapters.py --check exited unexpected code %s: %s "
                   "FIX: run `python deploy/gen-skill-adapters.py --check` directly to "
                   "see the full error." % (rc, _tail(out)))

def _effective_corpus_list(root):
    """Parse project.yaml's corpus binding (v3.0.18, backlog v3.0-88) into the same
    effective corpus list every consumer builds: the corpus_sources list, else the
    legacy singular corpus_source + corpus_config as a one-entry list (id = the repo
    name segment). Returns (entries, skip_reason, error): skip_reason is set when the
    binding is undeclared or unwatchable BY DESIGN (SKIP, not FAIL); error is set on a
    config defect that must surface visibly (the both-forms conflict, a malformed
    entry, an unparseable project.yaml)."""
    py = root / "project.yaml"
    if not py.is_file():
        return [], "no project.yaml at root (not an instantiated project)", None
    if yaml is None:
        return [], ("PyYAML not installed; corpus binding unwatched. "
                    "FIX: pip install pyyaml"), None
    try:
        data = yaml.safe_load(py.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001 -- any parse failure is the same finding
        return [], None, ("project.yaml did not parse: %s. FIX: repair the YAML -- every "
                          "corpus consumer reads this binding at runtime." % e)
    singular = data.get("corpus_source") or "none"
    sources = data.get("corpus_sources")
    if sources and singular != "none":
        return [], None, ("corpus binding declares BOTH the singular corpus_source (%r) and "
                          "the corpus_sources list -- a config error; consumers refuse to "
                          "pick one. FIX: keep corpus_sources and set corpus_source: none."
                          % singular)
    entries = []
    if sources:
        if not isinstance(sources, list):
            return [], None, ("corpus_sources is not a list. "
                              "FIX: see project.yaml.example for the list form.")
        for i, e in enumerate(sources):
            if not isinstance(e, dict) or not all(
                    e.get(k) for k in ("source", "clone_path", "branch")):
                return [], None, ("corpus_sources[%d] is missing source/clone_path/branch. "
                                  "FIX: each entry needs all three; see "
                                  "project.yaml.example." % i)
            entries.append({"id": str(e.get("id") or str(e["source"]).split("/")[-1]),
                            "source": str(e["source"]),
                            "clone_path": str(e["clone_path"]),
                            "branch": str(e["branch"])})
    elif singular != "none":
        cfg = data.get("corpus_config") or {}
        if not cfg.get("clone_path") or not cfg.get("branch"):
            return [], None, ("corpus_source %r is declared but corpus_config lacks "
                              "clone_path/branch. FIX: see project.yaml.example."
                              % singular)
        entries.append({"id": str(singular).split("/")[-1], "source": str(singular),
                        "clone_path": str(cfg["clone_path"]),
                        "branch": str(cfg["branch"])})
    if not entries:
        return [], ("no corpus declared (corpus_source: none, no corpus_sources) -- "
                    "corpus observation inert for this project"), None
    return entries, None, None

def check_corpus_reachability(ctx):
    """Check 12 (v3.0.18, backlog v3.0-88): every declared execution corpus is present
    and readable at its clone_path -- read-only (`git rev-parse` only, no fetch, no
    credentials). One Result per corpus so partial observation fails VISIBLY: a
    declared-but-unreachable corpus is a FAIL naming that corpus, never a silent
    subset-of-corpora green."""
    entries, skip_reason, err = _effective_corpus_list(ctx["root"])
    if err:
        return Result("FAIL", "corpus-reachability", err)
    if skip_reason:
        return Result("SKIP", "corpus-reachability", skip_reason)
    results = []
    for e in entries:
        name = "corpus-reachability:%s" % e["id"]
        clone = Path(e["clone_path"])
        if not clone.is_dir():
            results.append(Result("FAIL", name,
                "corpus %s (%s) declared but clone_path %s does not exist -- this corpus "
                "is UNOBSERVABLE and every consumer will surface it. FIX: clone %s there "
                "(read-only observation copy) or correct clone_path in project.yaml."
                % (e["id"], e["source"], e["clone_path"], e["source"])))
            continue
        rc, out = _run(["git", "-C", str(clone), "rev-parse", e["branch"]], timeout=30)
        if rc == 0:
            results.append(Result("PASS", name, "%s @ %s (%s)"
                                  % (e["source"], (out or "").strip()[:12], e["branch"])))
        else:
            results.append(Result("FAIL", name,
                "corpus %s (%s): `git rev-parse %s` failed at %s: %s FIX: ensure the "
                "clone is a git repo with branch %s present (read-only observation "
                "needs no credentials)."
                % (e["id"], e["source"], e["branch"], e["clone_path"],
                   _tail(out, n_lines=2), e["branch"])))
    return results

# --------------------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------------------

def _safe(fn, name, *args):
    """Run a check function; a crash in the check itself becomes a FAIL, not a hard stop,
    so one broken check never hides the rest of the report."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 -- deliberately broad; this is the last line of defense
        return Result("FAIL", name,
                       "check crashed: %s: %s. FIX: this is a doctor.py bug -- report it; "
                       "the environment state for this check is unknown." % (type(e).__name__, e))

def run_all(root, fast_selftests=False):
    ctx = {"root": root, "python": sys.executable, "fast_selftests": fast_selftests}
    results = []

    def add(r):
        results.extend(r) if isinstance(r, list) else results.append(r)

    r_bridge = _safe(check_bridge_wired, "bridge-wired", ctx)
    r_node = _safe(check_node, "node", ctx)
    add(r_bridge)
    add(r_node)
    add(_safe(check_bridge_cli, "bridge-cli", ctx, r_bridge, r_node))
    add(_safe(check_codex_auth, "codex-auth", ctx))
    add(_safe(check_jq, "jq", ctx))
    add(_safe(check_python_sensors, "python-sensors", ctx))
    add(_safe(check_hooks_wired, "hooks-wired", ctx))
    add(_safe(check_precommit_scanner, "precommit-scanner", ctx))
    add(_safe(check_trust_surfaces, "trust-surfaces", ctx))
    add(_safe(check_skill_drift, "skill-drift", ctx))
    add(_safe(check_derivation_gate, "derivation-gate", ctx))
    add(_safe(check_docs_stamps, "docs-stamps", ctx))
    add(_safe(check_version_drift, "version-drift", ctx))
    add(_safe(check_sensor_reachability, "sensor-reachability", ctx))
    add(_safe(check_skill_adapters, "skill-adapters", ctx))
    add(_safe(check_corpus_reachability, "corpus-reachability", ctx))
    return results

def _exit_code(results):
    return 2 if any(r.status == "FAIL" for r in results) else 0

def _summary_line(results):
    counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "SKIP": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return ("doctor: %d check(s) -- %d pass, %d warn, %d fail, %d skip"
            % (len(results), counts["PASS"], counts["WARN"], counts["FAIL"], counts["SKIP"]))

# --------------------------------------------------------------------------------------
# Self-test (embedded fixtures; no live node/codex/jq required). Pass-case and fail-case
# fixtures for checks 1 (bridge-wired), 7 (hooks-wired), 8 (skill-drift); error-branch
# fixtures for 6 (unreadable sensor file) and 9 (unexpected gate exit code); the five states
# of check 10 (docs-stamps: none-present, stamped+matching, stamped+mismatched,
# present-but-unstamped, stale root ARCHITECTURE.md); the four states of check 11
# (version-drift: no manifest, matching
# row, drifted row, unreachable probe -- the live python interpreter stands in as a harmless,
# always-present probe target, same live-tool-as-fixture pattern checks 2/4's pure helpers
# avoid needing altogether); the runner's exit-code logic; the pure parsing/classification
# helpers behind the subprocess-dependent checks (2, 4, 11); and a global assertion that every
# fixture-produced FAIL/WARN carries FIX:.
# --------------------------------------------------------------------------------------

def self_test():
    global yaml  # rebound (and restored) hermetically below to force the PyYAML-absent branch
    failed = 0
    total = 0
    produced = []  # every fixture-produced Result, for the global FIX-discipline assertion

    def check(name, cond):
        nonlocal failed, total
        total += 1
        if not cond:
            failed += 1
        print("  %s %s" % ("ok " if cond else "XX ", name))

    def note(r):
        produced.extend(r if isinstance(r, list) else [r])
        return r

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ctx = {"root": root, "python": sys.executable}
        check("bridge-wired: fail when missing", note(check_bridge_wired(ctx)).status == "FAIL")
        bridge_dir = root / ".claude" / "skills" / "bridge"
        bridge_dir.mkdir(parents=True)
        (bridge_dir / "verify-cli.js").write_text("// stub\n", encoding="utf-8")
        check("bridge-wired: pass when present", note(check_bridge_wired(ctx)).status == "PASS")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ctx = {"root": root, "python": sys.executable}
        check("hooks-wired: fail when file missing",
              note(check_hooks_wired(ctx)).status == "FAIL")

        claude_dir = root / ".claude"
        claude_dir.mkdir(parents=True)
        (claude_dir / "settings.local.json").write_text("{not valid json", encoding="utf-8")
        check("hooks-wired: fail on invalid json", note(check_hooks_wired(ctx)).status == "FAIL")

        (claude_dir / "settings.local.json").write_text(
            json.dumps({"hooks": {"PreToolUse": [
                {"hooks": [{"command": "$X/core/security/hooks/block-dangerous-bash.sh"}]}]}}),
            encoding="utf-8")
        r = note(check_hooks_wired(ctx))
        check("hooks-wired: warn when one hook missing",
              r.status == "WARN" and "block-env-writes.sh" in r.detail)

        # v3.0-112: precommit-scanner check, all four dispositions on real trees.
        scan_src_dir = Path(ctx["root"]) / "core" / "security" / "hooks"
        scan_src_dir.mkdir(parents=True, exist_ok=True)
        scan_src = scan_src_dir / "scan-staged-secrets.sh"
        r = note(check_precommit_scanner(ctx))
        check("precommit-scanner: SKIP when the scanner isn't shipped",
              r.status == "SKIP")
        scan_src.write_text("#!/bin/sh\n# the shipped scanner bytes\n", encoding="utf-8")
        r = note(check_precommit_scanner(ctx))
        check("precommit-scanner: warn when not a git repo (nothing scans)",
              r.status == "WARN" and "git init" in r.detail)
        hooks_dir = Path(ctx["root"]) / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        r = note(check_precommit_scanner(ctx))
        check("precommit-scanner: warn when repo has no pre-commit hook installed",
              r.status == "WARN" and "pre-commit" in r.detail)
        (hooks_dir / "pre-commit").write_text(
            "#!/bin/sh\n# the shipped scanner bytes\n", encoding="utf-8")
        r = note(check_precommit_scanner(ctx))
        check("precommit-scanner: PASS when installed hook is byte-identical",
              r.status == "PASS")
        (hooks_dir / "pre-commit").write_text(
            "#!/bin/sh\nmy own hook\nbash core/security/hooks/scan-staged-secrets.sh\n",
            encoding="utf-8")
        r = note(check_precommit_scanner(ctx))
        check("precommit-scanner: a differing hook that NAMES the scanner reads as a chain",
              r.status == "PASS" and "chain" in r.detail)
        (hooks_dir / "pre-commit").write_text("#!/bin/sh\nunrelated hook\n", encoding="utf-8")
        r = note(check_precommit_scanner(ctx))
        check("precommit-scanner: warn on a foreign/stale hook (commits unscanned)",
              r.status == "WARN" and "STALE" in r.detail)

        (claude_dir / "settings.local.json").write_text(
            json.dumps({"hooks": {"PreToolUse": [
                {"hooks": [{"command": "$X/core/security/hooks/block-dangerous-bash.sh"}]},
                {"hooks": [{"command": "$X/core/security/hooks/block-env-writes.sh"}]}]}}),
            encoding="utf-8")
        r = note(check_hooks_wired(ctx))
        check("hooks-wired: warn when both scripts present but neither entry has a matcher",
              r.status == "WARN" and "Bash" in r.detail and "PowerShell" in r.detail)

        (claude_dir / "settings.local.json").write_text(
            json.dumps({"hooks": {"PreToolUse": [
                {"matcher": "Bash",
                 "hooks": [{"command": "$X/core/security/hooks/block-dangerous-bash.sh"}]},
                {"matcher": "Edit|Write",
                 "hooks": [{"command": "$X/core/security/hooks/block-env-writes.sh"}]}]}}),
            encoding="utf-8")
        r = note(check_hooks_wired(ctx))
        check("hooks-wired: warn when Bash wired but PowerShell matcher missing "
              "(the exact shadowed-tool defect this check was extended to catch)",
              r.status == "WARN" and "PowerShell" in r.detail
              and "block-dangerous-bash.sh not wired under a \"Bash\"" not in r.detail)

        (claude_dir / "settings.local.json").write_text(
            json.dumps({"hooks": {"PreToolUse": [
                {"matcher": "Bash",
                 "hooks": [{"command": "$X/core/security/hooks/block-dangerous-bash.sh"}]},
                {"matcher": "PowerShell",
                 "hooks": [{"command": "$X/core/security/hooks/block-dangerous-bash.sh"}]},
                {"matcher": "Bash",
                 "hooks": [{"command": "$X/core/security/hooks/block-env-writes.sh"}]}]}}),
            encoding="utf-8")
        r = note(check_hooks_wired(ctx))
        check("hooks-wired: warn when block-env-writes.sh not under an Edit/Write matcher",
              r.status == "WARN" and "block-env-writes.sh" in r.detail and "Edit" in r.detail)

        # v3.0.47: a scheduled wrapper without the unattended marker -> WARN; with it -> PASS
        (claude_dir / "settings.local.json").write_text(
            json.dumps({"hooks": {"PreToolUse": [
                {"matcher": "Bash",
                 "hooks": [{"command": "$X/core/security/hooks/block-dangerous-bash.sh"}]},
                {"matcher": "PowerShell",
                 "hooks": [{"command": "$X/core/security/hooks/block-dangerous-bash.sh"}]},
                {"matcher": "Edit|Write",
                 "hooks": [{"command": "$X/core/security/hooks/block-env-writes.sh"}]}]}}),
            encoding="utf-8")
        (claude_dir / "nightly-sweep.cmd").write_text("@echo off\r\nclaude -p /sweep\r\n",
                                                      encoding="utf-8")
        r = note(check_hooks_wired(ctx))
        check("hooks-wired: a scheduled wrapper WITHOUT the unattended marker -> WARN naming it",
              r.status == "WARN" and "nightly-sweep.cmd" in r.detail
              and "RHEOSCOPE_UNATTENDED" in r.detail)
        (claude_dir / "nightly-sweep.cmd").write_text(
            "@echo off\r\nset RHEOSCOPE_UNATTENDED=1\r\nclaude -p /sweep\r\n", encoding="utf-8")
        r = note(check_hooks_wired(ctx))
        check("hooks-wired: the same wrapper WITH the marker -> PASS", r.status == "PASS")
        (claude_dir / "nightly-sweep.cmd").unlink()

        (claude_dir / "settings.local.json").write_text(
            json.dumps({"hooks": {"PreToolUse": [
                {"matcher": "Bash",
                 "hooks": [{"command": "$X/core/security/hooks/block-dangerous-bash.sh"}]},
                {"matcher": "PowerShell",
                 "hooks": [{"command": "$X/core/security/hooks/block-dangerous-bash.sh"}]},
                {"matcher": "Edit|Write",
                 "hooks": [{"command": "$X/core/security/hooks/block-env-writes.sh"}]}]}}),
            encoding="utf-8")
        check("hooks-wired: pass when both present with full matcher coverage",
              note(check_hooks_wired(ctx)).status == "PASS")

        # Combined-entry matcher form ("Bash|PowerShell" in one PreToolUse entry) also counts --
        # token-exact set membership (split on "|", trim), not substring containment, per
        # _matcher_tokens_for_script's contract.
        (claude_dir / "settings.local.json").write_text(
            json.dumps({"hooks": {"PreToolUse": [
                {"matcher": "Bash|PowerShell",
                 "hooks": [{"command": "$X/core/security/hooks/block-dangerous-bash.sh"}]},
                {"matcher": "Edit|Write",
                 "hooks": [{"command": "$X/core/security/hooks/block-env-writes.sh"}]}]}}),
            encoding="utf-8")
        check("hooks-wired: pass with a single combined Bash|PowerShell matcher entry",
              note(check_hooks_wired(ctx)).status == "PASS")

        # Round-2 cross-check false-positive shape: a matcher string that CONTAINS "Bash" as
        # a substring ("NotBash") must NOT satisfy the Bash requirement -- token-exact
        # comparison, not "Bash" in matcher_string. Proves the substring-containment defect
        # the prior _matcher_strings_for_script form was vulnerable to is actually closed.
        (claude_dir / "settings.local.json").write_text(
            json.dumps({"hooks": {"PreToolUse": [
                {"matcher": "NotBash",
                 "hooks": [{"command": "$X/core/security/hooks/block-dangerous-bash.sh"}]},
                {"matcher": "PowerShell",
                 "hooks": [{"command": "$X/core/security/hooks/block-dangerous-bash.sh"}]},
                {"matcher": "Edit|Write",
                 "hooks": [{"command": "$X/core/security/hooks/block-env-writes.sh"}]}]}}),
            encoding="utf-8")
        r = note(check_hooks_wired(ctx))
        check("hooks-wired: WARN on \"NotBash\" matcher -- token-exact, substring "
              "\"Bash\" in \"NotBash\" must NOT false-positive as Bash coverage",
              r.status == "WARN"
              and "block-dangerous-bash.sh not wired under a \"Bash\" matcher" in r.detail)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ctx = {"root": root, "python": sys.executable}
        results = note(check_skill_drift(ctx))
        check("skill-drift: warn when successor missing entirely",
              any(r.status == "WARN" for r in results))

        skills_dir = root / ".claude" / "skills"
        (skills_dir / "preflight").mkdir(parents=True)
        (skills_dir / "handoff").mkdir(parents=True)  # successor of author+receive (v3.0-78)
        results = note(check_skill_drift(ctx))
        check("skill-drift: pass when successors present, no stale",
              all(r.status == "PASS" for r in results))

        (skills_dir / "grill").mkdir(parents=True)
        results = note(check_skill_drift(ctx))
        check("skill-drift: fail when stale skill present alongside successor",
              any(r.status == "FAIL" for r in results))

        # v3.0-78: a lingering handoff-author (or -receive) install alongside
        # /handoff is the same stale-superseded class -- FAIL on its own row.
        (skills_dir / "handoff-author").mkdir(parents=True)
        results = note(check_skill_drift(ctx))
        check("skill-drift: fail on stale handoff-author alongside /handoff",
              any(r.status == "FAIL" and "handoff-author" in r.name for r in results))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ctx = {"root": root, "python": sys.executable}
        results = note(check_docs_stamps(ctx))
        check("docs-stamps: single SKIP when no candidate docs present",
              len(results) == 1 and results[0].status == "SKIP")

        (root / "project.yaml").write_text('template_version: "3.0"\n', encoding="utf-8")
        onboarding_dir = root / "core" / "onboarding"
        onboarding_dir.mkdir(parents=True)

        (onboarding_dir / "TOUR.md").write_text(
            "# Tour\n\n*verified-against: 3.0 (2026-07-13)*\n", encoding="utf-8")
        r = next(x for x in note(check_docs_stamps(ctx)) if x.name.endswith("TOUR.md"))
        check("docs-stamps: pass when stamped and matching template_version", r.status == "PASS")

        (onboarding_dir / "GLOSSARY.md").write_text(
            "# Glossary\n\n*verified-against: 2.1 (2026-06-01)*\n", encoding="utf-8")
        r = next(x for x in note(check_docs_stamps(ctx)) if x.name.endswith("GLOSSARY.md"))
        check("docs-stamps: warn on version mismatch, carries FIX",
              r.status == "WARN" and "FIX:" in r.detail)

        (onboarding_dir / "SYSTEM-MAP.html").write_text(
            "<html><body><h1>Map</h1></body></html>\n", encoding="utf-8")
        r = next(x for x in note(check_docs_stamps(ctx)) if x.name.endswith("SYSTEM-MAP.html"))
        check("docs-stamps: warn when present but unstamped, carries FIX",
              r.status == "WARN" and "FIX:" in r.detail)

        (root / "ARCHITECTURE.md").write_text(
            "# Architecture\n\n*Architecture document version: 2.1*\n"
            "*verified-against: 2.1 (2026-06-12)*\n", encoding="utf-8")
        r = next(x for x in note(check_docs_stamps(ctx))
                 if x.name == "docs-stamps:ARCHITECTURE.md")
        check("docs-stamps: stale root ARCHITECTURE.md warns (v3.0-75 class)",
              r.status == "WARN" and "FIX:" in r.detail)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ctx = {"root": root, "python": sys.executable}
        r = note(check_version_drift(ctx))
        check("version-drift: skip when no manifest", r.status == "SKIP")

        deploy_dir = root / "deploy"
        deploy_dir.mkdir(parents=True)
        if yaml is None:
            r = note(check_version_drift(ctx))
            check("version-drift: skip when PyYAML absent (manifest present)",
                  r.status == "SKIP")
        else:
            # A harmless, always-present probe: the live python interpreter running this very
            # self-test (guaranteed on PATH-independent -- quoted absolute path via
            # sys.executable, same interpreter check_python_sensors/check_derivation_gate
            # already trust elsewhere in this file). Capture its real `--version` output so
            # the "matching row" fixture doesn't depend on a hardcoded version string going
            # stale.
            probe_cmd = "\"%s\" --version" % sys.executable
            probe_rc, probe_out = _run([sys.executable, "--version"], timeout=10)
            live_python_available = probe_rc == 0
            if live_python_available:
                live_version = next(iter((probe_out or "").strip().splitlines()), "")
                (deploy_dir / "environment-manifest.yaml").write_text(
                    "tools:\n  - tool: python\n    probe: %s\n"
                    "    version_verified: %s\n    date: \"2026-07-23\"\n"
                    % (json.dumps(probe_cmd), json.dumps(live_version)),
                    encoding="utf-8")
                results = note(check_version_drift(ctx))
                r = next(x for x in results if x.name == "version-drift:python")
                check("version-drift: pass when probe matches version_verified",
                      r.status == "PASS")

            (deploy_dir / "environment-manifest.yaml").write_text(
                "tools:\n  - tool: python\n    probe: %s\n"
                "    version_verified: \"Python 0.0.1\"\n    date: \"2026-01-01\"\n"
                % json.dumps(probe_cmd),
                encoding="utf-8")
            results = note(check_version_drift(ctx))
            r = next(x for x in results if x.name == "version-drift:python")
            check("version-drift: warn on drifted version, names both + date, carries FIX",
                  r.status == "WARN" and "Python 0.0.1" in r.detail
                  and "2026-01-01" in r.detail and "FIX:" in r.detail)

            (deploy_dir / "environment-manifest.yaml").write_text(
                "tools:\n  - tool: nonexistent-tool-xyz\n"
                "    probe: \"totally-nonexistent-binary-xyz --version\"\n"
                "    version_verified: \"1.0\"\n    date: \"2026-01-01\"\n",
                encoding="utf-8")
            results = note(check_version_drift(ctx))
            r = next(x for x in results if x.name == "version-drift:nonexistent-tool-xyz")
            check("version-drift: warn tool unreachable when probe/tool absent, carries FIX",
                  r.status == "WARN" and "unreachable" in r.detail and "FIX:" in r.detail)
            check("version-drift: unreachable warn names recorded version + verified date",
                  "1.0" in r.detail and "2026-01-01" in r.detail)

            # FIX 2: the yaml-is-None branch (line ~451) is otherwise unreachable in this
            # self-test whenever PyYAML happens to be installed (the common case, since this
            # whole "else" arm only runs when `yaml is not None`). Force it hermetically by
            # temporarily rebinding the module-level `yaml` name to None around a single call,
            # restoring it in a finally so no other check in this run is affected -- exercises
            # the exact code path check_version_drift() takes when PyYAML is absent, rather
            # than relying on the test environment happening to lack it.
            saved_yaml = yaml
            yaml = None
            try:
                r = note(check_version_drift(ctx))
            finally:
                yaml = saved_yaml
            check("version-drift: skip when PyYAML absent (forced module-level yaml=None)",
                  r.status == "SKIP" and "PyYAML" in r.detail)

    # check 12 (corpus-reachability, v3.0-88): the fixture states -- no project.yaml,
    # binding undeclared, both-forms config error, malformed entry, declared-but-missing
    # clone (per-corpus FAIL), and (git permitting) a live PASS on a scratch repo via the
    # list form + the legacy singular form resolving to the same effective list
    # (back-compat). Same live-tool-as-fixture stance as check 11's python probe: git
    # absent on PATH -> the pass-case fixtures are skipped, the pure-config cases still run.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ctx = {"root": root, "python": sys.executable}
        r = note(check_corpus_reachability(ctx))
        check("corpus: skip when no project.yaml", r.status == "SKIP")
        if yaml is not None:
            (root / "project.yaml").write_text("corpus_source: none\n", encoding="utf-8")
            r = note(check_corpus_reachability(ctx))
            check("corpus: skip when binding undeclared", r.status == "SKIP")
            (root / "project.yaml").write_text(
                "corpus_source: org/legacy\ncorpus_config:\n  clone_path: X\n  branch: main\n"
                "corpus_sources:\n- source: org/a\n  clone_path: Y\n  branch: main\n",
                encoding="utf-8")
            r = note(check_corpus_reachability(ctx))
            check("corpus: both forms declared -> FAIL config error, carries FIX",
                  r.status == "FAIL" and "BOTH" in r.detail and "FIX:" in r.detail)
            (root / "project.yaml").write_text(
                "corpus_source: none\ncorpus_sources:\n- source: org/a\n  branch: main\n",
                encoding="utf-8")
            r = note(check_corpus_reachability(ctx))
            check("corpus: malformed entry -> FAIL, carries FIX",
                  r.status == "FAIL" and "FIX:" in r.detail)
            missing = root / "no-such-clone"
            (root / "project.yaml").write_text(
                "corpus_source: none\ncorpus_sources:\n- id: ghost\n  source: org/ghost\n"
                "  clone_path: %s\n  branch: main\n" % json.dumps(str(missing)),
                encoding="utf-8")
            rs = note(check_corpus_reachability(ctx))
            r = next(x for x in rs if x.name == "corpus-reachability:ghost")
            check("corpus: declared-but-missing clone FAILs per corpus, carries FIX",
                  r.status == "FAIL" and "UNOBSERVABLE" in r.detail and "FIX:" in r.detail)
            git_rc, _out = _run(["git", "--version"], timeout=10)
            if git_rc == 0:
                clone = root / "scratch-corpus"
                clone.mkdir()
                ok = _run(["git", "init", "-q", str(clone)], timeout=30)[0] == 0
                ok = ok and _run(["git", "-C", str(clone), "-c", "user.email=t@t",
                                  "-c", "user.name=t", "commit", "--allow-empty",
                                  "-q", "-m", "x"], timeout=30)[0] == 0
                if ok:
                    _rc, branch = _run(["git", "-C", str(clone), "rev-parse",
                                        "--abbrev-ref", "HEAD"], timeout=30)
                    branch = (branch or "").strip() or "master"
                    (root / "project.yaml").write_text(
                        "corpus_source: none\ncorpus_sources:\n- source: org/scratch\n"
                        "  clone_path: %s\n  branch: %s\n"
                        % (json.dumps(str(clone)), branch), encoding="utf-8")
                    rs = note(check_corpus_reachability(ctx))
                    check("corpus: reachable clone PASSes, id defaults to repo name",
                          rs[0].status == "PASS"
                          and rs[0].name == "corpus-reachability:scratch")
                    (root / "project.yaml").write_text(
                        "corpus_source: org/scratch\ncorpus_config:\n  clone_path: %s\n"
                        "  branch: %s\n" % (json.dumps(str(clone)), branch),
                        encoding="utf-8")
                    rs = note(check_corpus_reachability(ctx))
                    check("corpus: legacy singular form still PASSes (back-compat)",
                          rs[0].status == "PASS"
                          and rs[0].name == "corpus-reachability:scratch")

    for name, statuses, expected in (
        ("exit-logic: all pass/skip -> 0", ("PASS", "SKIP"), 0),
        ("exit-logic: warn-only stays 0", ("PASS", "WARN"), 0),
        ("exit-logic: any fail -> 2", ("PASS", "FAIL"), 2),
    ):
        got = _exit_code([Result(s, "x") for s in statuses])
        check(name, got == expected)

    for name, text, expected in (
        ("node-version: v18.0.0 -> major 18", "v18.0.0\n", 18),
        ("node-version: v24.18.0 -> major 24", "v24.18.0", 24),
        ("node-version: garbage -> None", "not a version", None),
    ):
        check(name, _parse_node_major(text) == expected)

    for name, a, b, expected in (
        ("versions-match: identical strings", "Python 3.12.10", "Python 3.12.10", True),
        ("versions-match: whitespace/case tolerant", "  PYTHON 3.12.10\n",
         "python 3.12.10", True),
        ("versions-match: substring either direction", "git version 2.54.0.windows.1",
         "2.54.0", True),
        ("versions-match: genuinely different versions", "Python 3.12.10", "Python 0.0.1",
         False),
        ("versions-match: empty strings never match", "", "", False),
    ):
        check(name, _versions_match(a, b) == expected)

    for name, rc, out, expected in (
        ("codex-classify: exit 0 -> PASS", 0, "Logged in using ChatGPT\n", "PASS"),
        ("codex-classify: unrecognized subcommand -> WARN", 1,
         "error: unrecognized subcommand 'status'\n", "WARN"),
        ("codex-classify: not authenticated -> FAIL", 1, "Not logged in.\n", "FAIL"),
    ):
        status, _detail = _classify_codex(rc, out)
        check(name, status == expected)

    # bridge-cli: SKIP branch is exercised without touching node or a real bridge script.
    stub_ctx = {"root": Path("."), "python": sys.executable}
    r = check_bridge_cli(stub_ctx, Result("FAIL", "bridge-wired"), Result("PASS", "node"))
    check("bridge-cli: skip when bridge-wired failed", r.status == "SKIP")
    r = check_bridge_cli(stub_ctx, Result("PASS", "bridge-wired"), Result("FAIL", "node"))
    check("bridge-cli: skip when node failed", r.status == "SKIP")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ctx = {"root": root, "python": sys.executable}
        results = check_python_sensors(ctx)
        check("python-sensors: skip when deploy/ absent",
              len(results) == 1 and results[0].status == "SKIP")

        r = check_derivation_gate(ctx)
        check("derivation-gate: skip when check-derivation.py absent", r.status == "SKIP")

        deploy_dir = root / "deploy"
        # An unreadable "sensor": a DIRECTORY named *.py matches the glob and raises OSError
        # on read_text -- cross-platform (IsADirectoryError on Unix, PermissionError on
        # Windows), no chmod games needed.
        (deploy_dir / "unreadable.py").mkdir(parents=True)
        results = note(check_python_sensors(ctx))
        r = next((x for x in results if "unreadable.py" in x.name), None)
        check("python-sensors: unreadable file -> FAIL with FIX",
              r is not None and r.status == "FAIL" and "FIX:" in r.detail)

        # A check-derivation stub exiting an unexpected code (neither 0, 2, nor 3) -> WARN + FIX.
        (deploy_dir / "check-derivation.py").write_text(
            "import sys\nsys.exit(7)\n", encoding="utf-8")
        r = note(check_derivation_gate(ctx))
        check("derivation-gate: unexpected exit code -> WARN with FIX",
              r.status == "WARN" and "FIX:" in r.detail)

        # A stub exiting 3 (the sensor's fail-honest tree-not-located code, silence-sweep
        # S2) -> the dedicated WARN ("could not locate the wiki tree"), never PASS, never
        # the audit-pending FAIL.
        (deploy_dir / "check-derivation.py").write_text(
            "import sys\nsys.exit(3)\n", encoding="utf-8")
        r = note(check_derivation_gate(ctx))
        check("derivation-gate: tree-not-located exit 3 -> WARN 'could not locate' with FIX",
              r.status == "WARN" and "could not locate the wiki tree" in r.detail
              and "FIX:" in r.detail)

    # Check 12: sensor-reachability -- the three states (skip / orphan-WARN with the
    # transitive chain honored / all-reachable PASS). Register cases retired 2026-08-08
    # with the dormant register itself.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ctx = {"root": root, "python": sys.executable}
        r = check_sensor_reachability(ctx)
        check("sensor-reachability: skip when deploy/ absent", r.status == "SKIP")

        deploy_dir = root / "deploy"
        deploy_dir.mkdir(parents=True)
        r = check_sensor_reachability(ctx)
        check("sensor-reachability: skip when deploy/ holds no python", r.status == "SKIP")

        # wired.py is named by a skill; it dynamically loads chained.py (underscore-stem
        # form); orphan.py is reached by nothing.
        (deploy_dir / "wired.py").write_text(
            '_load_module("chained.py", "chained_ref")\n', encoding="utf-8")
        (deploy_dir / "chained.py").write_text("x = 1\n", encoding="utf-8")
        (deploy_dir / "orphan.py").write_text("x = 2\n", encoding="utf-8")
        skill_dir = root / ".claude" / "skills" / "sweep"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "Step 1: run `python deploy/wired.py`.\n", encoding="utf-8")
        r = note(check_sensor_reachability(ctx))
        check("sensor-reachability: orphan WARNs with FIX; transitive chain stays clean",
              r.status == "WARN" and "orphan.py" in r.detail
              and "chained.py" not in r.detail and "FIX:" in r.detail)

        (deploy_dir / "orphan.py").unlink()
        r = note(check_sensor_reachability(ctx))
        check("sensor-reachability: all reachable -> PASS with 0 unaccounted",
              r.status == "PASS" and "0 unaccounted" in r.detail)

    # Check 13: skill-adapters -- absent-generator SKIP; stub-driven WARN (drift) with
    # FIX; stub-driven PASS. Same stub-script pattern as the derivation-gate cases.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ctx = {"root": root, "python": sys.executable}
        r = check_skill_adapters(ctx)
        check("skill-adapters: skip when generator absent", r.status == "SKIP")

        deploy_dir = root / "deploy"
        deploy_dir.mkdir(parents=True)
        (deploy_dir / "gen-skill-adapters.py").write_text(
            "import sys\nprint('skill-adapters DRIFT: stale=[x]')\nsys.exit(1)\n",
            encoding="utf-8")
        r = note(check_skill_adapters(ctx))
        check("skill-adapters: drift (exit 1) -> WARN with FIX",
              r.status == "WARN" and "FIX:" in r.detail)

        (deploy_dir / "gen-skill-adapters.py").write_text(
            "print('skill-adapters current (15 adapters)')\n", encoding="utf-8")
        r = note(check_skill_adapters(ctx))
        check("skill-adapters: current (exit 0) -> PASS", r.status == "PASS")

    # Check 16: trust-surfaces (v3.0-120) -- (a) head-identity, (b) signatures via a stub
    # trust.py report, (c) wiring, (d) pin typing, (e) unpublished retirements. Real git
    # repos in a temp dir; skipped with a note when git is not on PATH.
    if shutil.which("git") is None:
        print("  -- trust-surfaces cases skipped: git not on PATH")
    else:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ctx = {"root": root, "python": sys.executable}
            r = note(check_trust_surfaces(ctx))
            check("trust-surfaces: WARN when not a git repo",
                  len(r) == 1 and r[0].status == "WARN" and "git init" in r[0].detail)

            def git(*a):
                return subprocess.run(["git", "-C", str(root)] + list(a), capture_output=True,
                                      text=True, encoding="utf-8", errors="replace")
            git("init", "-q", "-b", "main")
            git("config", "user.email", "t@t")
            git("config", "user.name", "t")
            hooks = root / "core" / "security" / "hooks"
            hooks.mkdir(parents=True)
            (hooks / "egress-allowlist.txt").write_text("# none\n", encoding="utf-8")
            (hooks / "scan-staged-secrets.sh").write_text("#!/bin/sh\nscanner\n",
                                                          encoding="utf-8")
            (root / "README.md").write_text("x\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "seed")
            by = {x.name: x for x in note(check_trust_surfaces(ctx))}
            check("trust-surfaces(a): tracked members HEAD-identical -> PASS carrying the "
                  "honest in-process note",
                  by["trust-surfaces:head-identity"].status == "PASS"
                  and "tampered doctor can lie" in by["trust-surfaces:head-identity"].detail)
            check("trust-surfaces(d): pin absent -> WARN naming the ceremony",
                  by["trust-surfaces:pin"].status == "WARN"
                  and "ceremony" in by["trust-surfaces:pin"].detail)
            check("trust-surfaces(b): no deploy/trust.py -> WARN unavailable",
                  by["trust-surfaces:signatures"].status == "WARN"
                  and "unavailable" in by["trust-surfaces:signatures"].detail)
            check("trust-surfaces(c): nothing wired -> FAIL perimeter unwired",
                  by["trust-surfaces:wiring"].status == "FAIL"
                  and "perimeter unwired" in by["trust-surfaces:wiring"].detail)
            (hooks / "egress-allowlist.txt").write_text("# none\ncurl .*\n", encoding="utf-8")
            by = {x.name: x for x in note(check_trust_surfaces(ctx))}
            check("trust-surfaces(a): an uncommitted edit to a class member -> FAIL "
                  "'uncommitted perimeter change' naming the file",
                  by["trust-surfaces:head-identity"].status == "FAIL"
                  and "egress-allowlist.txt" in by["trust-surfaces:head-identity"].detail
                  and "uncommitted perimeter change" in by["trust-surfaces:head-identity"].detail)
            git("checkout", "--", "core/security/hooks/egress-allowlist.txt")
            (root / "deploy").mkdir()
            (root / "deploy" / "safe-allowlist.yaml").write_text("safe: []\n", encoding="utf-8")
            by = {x.name: x for x in note(check_trust_surfaces(ctx))}
            check("trust-surfaces(a): an UNTRACKED file at a class path is not a member "
                  "(it cannot be committed-identical; its consumers refuse it)",
                  by["trust-surfaces:head-identity"].status == "PASS")
            (hooks / "allowed_signers").write_text(
                "operator ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBZNGsMoi7jjpMb soft\n",
                encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "pin with a soft key")
            by = {x.name: x for x in note(check_trust_surfaces(ctx))}
            check("trust-surfaces(d): a non-sk key in the pin -> FAIL 'non-presence key listed'",
                  by["trust-surfaces:pin"].status == "FAIL"
                  and "non-presence key listed" in by["trust-surfaces:pin"].detail)
            (hooks / "allowed_signers").write_text(
                "operator sk-ssh-ed25519@openssh.com AAAAGnNrLXNzaC1lZDI1NTE5QG9wZW5zc2guY29t "
                "daily\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "pin sk only")
            by = {x.name: x for x in note(check_trust_surfaces(ctx))}
            check("trust-surfaces(d): sk-only pin -> PASS",
                  by["trust-surfaces:pin"].status == "PASS")
            # (b)/(e) through a stub trust.py that emits a fixed report
            stub = ("import json,os\nm=open(os.path.join(os.path.dirname(__file__),"
                    "'mode.txt')).read().strip()\nprint(json.dumps({'mode': m, 'surfaces': "
                    "[{'path': 'core/security/hooks/egress-allowlist.txt', 'signed': False, "
                    "'reason': 'commit abc: UNSIGNED', 'head_identical': True}], "
                    "'retire_records': [{'path': 'receipts/journal/9.json', 'seq': 9, "
                    "'published': False, 'reason': 'tag ref refs/tags/retire/9 does not "
                    "exist'}], 'pin': {}}))\n")
            (root / "deploy" / "trust.py").write_text(stub, encoding="utf-8")
            (root / "deploy" / "mode.txt").write_text("required\n", encoding="utf-8")
            by = {x.name: x for x in note(check_trust_surfaces(ctx))}
            check("trust-surfaces(b): unsigned member under required -> FAIL",
                  by["trust-surfaces:signatures"].status == "FAIL"
                  and "egress-allowlist.txt" in by["trust-surfaces:signatures"].detail)
            check("trust-surfaces(e): a retire record with no verified tag -> FAIL "
                  "'unpublished retirement present'",
                  by["trust-surfaces:retirements"].status == "FAIL"
                  and "unpublished retirement present" in by["trust-surfaces:retirements"].detail
                  and "seq 9" in by["trust-surfaces:retirements"].detail)
            (root / "deploy" / "mode.txt").write_text("warn\n", encoding="utf-8")
            by = {x.name: x for x in note(check_trust_surfaces(ctx))}
            check("trust-surfaces(b): the same unsigned member under warn -> WARN, not FAIL",
                  by["trust-surfaces:signatures"].status == "WARN")
            (root / "deploy" / "trust.py").write_text(
                stub.replace("'pin': {}", "'pin': {}, 'branch_rewind': ['local main is NOT a "
                             "fast-forward of origin/main -- history rewound or rewritten']"),
                encoding="utf-8")
            by = {x.name: x for x in note(check_trust_surfaces(ctx))}
            check("trust-surfaces(e): a branch-rewind finding -> FAIL 'history rewound'",
                  by["trust-surfaces:retirements"].status == "FAIL"
                  and "history rewound" in by["trust-surfaces:retirements"].detail)
            (root / "deploy" / "trust.py").write_text(stub, encoding="utf-8")
            (root / "deploy" / "trust.py").write_text("import sys\nsys.exit(3)\n",
                                                      encoding="utf-8")
            by = {x.name: x for x in note(check_trust_surfaces(ctx))}
            check("trust-surfaces(b): a crashing verifier -> FAIL (never a silent pass)",
                  by["trust-surfaces:signatures"].status == "FAIL")
            # a PATCHED verifier in the working tree: (a) names it, (b)/(e) refuse to run it
            (root / "deploy" / "trust.py").write_text(stub, encoding="utf-8")
            (root / "deploy" / "mode.txt").write_text("warn\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "ship a verifier")
            (root / "deploy" / "trust.py").write_text(stub + "# patched\n", encoding="utf-8")
            by = {x.name: x for x in note(check_trust_surfaces(ctx))}
            check("trust-surfaces: a working-tree patch to deploy/trust.py -> (a) names it AND "
                  "(b)/(e) FAIL 'verifier tampered' instead of consulting it",
                  by["trust-surfaces:head-identity"].status == "FAIL"
                  and by["trust-surfaces:signatures"].status == "FAIL"
                  and "verifier tampered" in by["trust-surfaces:signatures"].detail
                  and by["trust-surfaces:retirements"].status == "FAIL")
            git("checkout", "--", "deploy/trust.py")
            # (c) PASS when both underlying checks pass
            claude = root / ".claude"
            claude.mkdir()
            (claude / "settings.local.json").write_text(json.dumps({"hooks": {"PreToolUse": [
                {"matcher": "Bash", "hooks": [{"command": "$X/core/security/hooks/block-dangerous-bash.sh"}]},
                {"matcher": "PowerShell", "hooks": [{"command": "$X/core/security/hooks/block-dangerous-bash.sh"}]},
                {"matcher": "Edit|Write", "hooks": [{"command": "$X/core/security/hooks/block-env-writes.sh"}]}]}}),
                encoding="utf-8")
            (root / ".git" / "hooks").mkdir(exist_ok=True)
            (root / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\nscanner\n",
                                                                 encoding="utf-8")
            by = {x.name: x for x in note(check_trust_surfaces(ctx))}
            check("trust-surfaces(c): hooks wired (3 entries) + scanner byte-current -> PASS",
                  by["trust-surfaces:wiring"].status == "PASS")

    # Global fix-discipline assertion: every FAIL/WARN a fixture produced must teach.
    bad = [r for r in produced if r.status in ("FAIL", "WARN") and "FIX:" not in r.detail]
    check("fix-discipline: every fixture FAIL/WARN carries a FIX (%d checked, %d bare)"
          % (len(produced), len(bad)), not bad)

    if failed:
        print("doctor self-test: FAIL (%d/%d)" % (failed, total))
        return 1
    print("doctor self-test: PASS (%d/%d)" % (total, total))
    return 0

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="doctor.py",
        description="Unified environment-readiness sensor for an instantiated "
                     "project-os-harness project.")
    parser.add_argument("--root", default=None,
                         help="Project root to check (default: current working directory).")
    parser.add_argument("--self-test", action="store_true",
                         help="Run embedded self-test fixtures and exit.")
    parser.add_argument("--fast-selftests", action="store_true",
                         help="Sensor self-tests run as a date-keyed rotation instead of "
                              "the full battery (for the every-session /sweep call; init "
                              "and manual checkups should stay full).")
    parser.add_argument("--verbose", action="store_true",
                         help="Print every check line, including all-PASS family members "
                              "(default collapses homogeneous PASS runs to one line).")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    print("doctor: checking %s" % root)
    results = run_all(root, fast_selftests=args.fast_selftests)

    # v3.0.27 (plain-language sweep V3): the report used to be a flat scroll -- on an
    # instance, one FAIL sat mid-stream among 63 identical "[PASS] python-sensors:X"
    # lines, and the report ended in counts nobody acts on. Default view: homogeneous
    # all-PASS families collapse to one line (--verbose expands); every non-PASS line
    # always prints in full; and the report ends with what needs a human. Nothing is
    # dropped -- detail moves behind --verbose, findings never do.
    if args.verbose:
        for r in results:
            print(r.line())
    else:
        fams = {}
        for r in results:
            if ":" in r.name and r.status == "PASS":
                fams.setdefault(r.name.split(":", 1)[0], []).append(r)
        collapsible = {fam for fam, members in fams.items() if len(members) > 1}
        done = set()
        for r in results:
            fam = r.name.split(":", 1)[0] if ":" in r.name else None
            if r.status == "PASS" and fam in collapsible:
                if fam not in done:
                    print("[PASS] %s: %d member check(s) all passed (--verbose lists "
                          "them)" % (fam, len(fams[fam])))
                    done.add(fam)
                continue
            print(r.line())
    print(_summary_line(results))

    fails = [r for r in results if r.status == "FAIL"]
    warns = [r for r in results if r.status == "WARN"]
    def _first_clause(detail):
        return detail.split(". ", 1)[0][:160]
    if fails:
        print("NEEDS YOU (%d):" % len(fails))
        for r in fails:
            print("  - %s -- %s (full line above)" % (r.name, _first_clause(r.detail)))
    if warns:
        print("WORTH KNOWING (%d):" % len(warns))
        for r in warns:
            print("  - %s -- %s (full line above)" % (r.name, _first_clause(r.detail)))
    if fails or warns:
        print("If you do nothing: the project keeps working; the items above stay "
              "broken or switched off until someone acts on their FIX lines.")
    else:
        print("Nothing needs you.")
    return _exit_code(results)

if __name__ == "__main__":
    sys.exit(main())
