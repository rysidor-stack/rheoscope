#!/usr/bin/env python3
"""check-template-updates.py -- "check for updates to the harness", self-serve (v3.0-59).

Reads project.yaml's template_source (the public template URL, init-stamped) and
template_release (the tag this instance actually runs; advanced by adoption sessions),
lists the source's tags via `git ls-remote --tags`, and reports whether newer release
tags exist. REPORT-ONLY: it never fetches content, never adds a remote, and its output
always points at the file-level adoption path (core/onboarding/UPDATING.md) -- never
`git pull` (pulling the template into an instance smears template files over
instantiated ones).

Exit codes: 0 up-to-date or not-configured (both noted plainly) / 1 updates available
(a signal, not an error -- callers like /sweep surface it, nothing blocks) /
2 inconclusive (offline, git absent, unreadable config -- never silently green).

CLI:
  py core/governance/check-template-updates.py --check       # live (network: one ls-remote)
  py core/governance/check-template-updates.py --self-test   # offline, no network
"""

import os
import re
import subprocess
import sys

TAG_RE = re.compile(r"^v(\d+)\.(\d+)(?:\.(\d+))?$")


def read_config(root):
    """Minimal line-level read of project.yaml -- no YAML dependency."""
    path = os.path.join(root, "project.yaml")
    if not os.path.isfile(path):
        return None, None, "project.yaml not found under %s" % root
    src = rel = ""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r'^template_source:\s*"?([^"\n]*)"?\s*$', line)
            if m:
                src = m.group(1).strip()
            m = re.match(r'^template_release:\s*"?([^"\n]*)"?\s*$', line)
            if m:
                rel = m.group(1).strip()
    return src, rel, None


def parse_tag(tag):
    m = TAG_RE.match(tag.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def tags_from_lsremote(text):
    """Parse `git ls-remote --tags` output into sorted release tuples (deduped, ^{} peeled)."""
    tags = set()
    for line in text.splitlines():
        m = re.search(r"refs/tags/(v[\w.\-]+?)(\^\{\})?$", line.strip())
        if m and parse_tag(m.group(1)):
            tags.add(parse_tag(m.group(1)))
    return sorted(tags)


def fmt(t):
    return "v%d.%d.%d" % t


def run_check(root, lsremote_text=None):
    """Returns (exit_code, lines). lsremote_text injectable for self-test."""
    src, rel, err = read_config(root)
    if err:
        return 2, ["INCONCLUSIVE: " + err]
    if not src:
        return 0, ["template-updates: not configured (template_source empty -- pre-v3.0.12 "
                   "instance). Backfill per core/onboarding/UPDATING.md; not an error."]
    local = parse_tag(rel) if rel else None
    if lsremote_text is None:
        try:
            proc = subprocess.run(["git", "ls-remote", "--tags", src],
                                  capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            return 2, ["INCONCLUSIVE: cannot reach %s (%s) -- offline or git unavailable; "
                       "re-run when connected" % (src, e.__class__.__name__)]
        if proc.returncode != 0:
            return 2, ["INCONCLUSIVE: git ls-remote failed against %s: %s"
                       % (src, (proc.stderr or "").strip()[:200])]
        lsremote_text = proc.stdout
    remote = tags_from_lsremote(lsremote_text)
    if not remote:
        return 2, ["INCONCLUSIVE: %s reports no parseable release tags" % src]
    newest = remote[-1]
    if local is None:
        return 1, ["template-updates: template_release is empty/unparseable ('%s') but the "
                   "source's newest release is %s -- backfill your release record, then "
                   "re-check (core/onboarding/UPDATING.md)" % (rel, fmt(newest))]
    newer = [t for t in remote if t > local]
    local_disp = rel.strip() or fmt(local)  # show the operator's recorded string, not a
    # normalized triple ("v3.0" must not render as "v3.0.0" -- v3.0-61 cosmetic)
    if not newer:
        return 0, ["template-updates: up to date -- running %s, source newest is %s"
                   % (local_disp, fmt(newest))]
    return 1, ["template-updates: UPDATES AVAILABLE -- running %s, source has %s (newest %s)."
               % (local_disp, ", ".join(fmt(t) for t in newer), fmt(newest)),
               "Adopt FILE-LEVEL per core/onboarding/UPDATING.md (diff-driven worklist); "
               "never `git pull` the template into this repo."]


def self_test():
    import tempfile
    n = 0
    fake_remote = ("aaa\trefs/tags/v3.0.10\nbbb\trefs/tags/v3.0.10^{}\n"
                   "ccc\trefs/tags/v3.0.11\nddd\trefs/tags/v3.0.12\n"
                   "eee\trefs/tags/not-a-release\n")
    with tempfile.TemporaryDirectory() as td:
        def write_yaml(body):
            with open(os.path.join(td, "project.yaml"), "w", encoding="utf-8") as f:
                f.write(body)
        write_yaml('project_name: t\ntemplate_source: "https://example.invalid/x"\n'
                   'template_release: "v3.0.12"\n')
        code, lines = run_check(td, lsremote_text=fake_remote)
        assert code == 0 and "up to date" in lines[0], (code, lines)
        n += 1
        write_yaml('template_source: "https://example.invalid/x"\ntemplate_release: "v3.0.10"\n')
        code, lines = run_check(td, lsremote_text=fake_remote)
        assert code == 1 and "v3.0.11, v3.0.12" in lines[0], (code, lines)
        assert "never `git pull`" in lines[1], lines
        n += 2
        write_yaml('template_source: ""\ntemplate_release: ""\n')
        code, lines = run_check(td, lsremote_text=fake_remote)
        assert code == 0 and "not configured" in lines[0], (code, lines)
        n += 1
        write_yaml('template_source: "https://example.invalid/x"\ntemplate_release: ""\n')
        code, lines = run_check(td, lsremote_text=fake_remote)
        assert code == 1 and "backfill" in lines[0].lower(), (code, lines)
        n += 1
        write_yaml('template_source: "https://example.invalid/x"\ntemplate_release: "v3.0.12"\n')
        code, lines = run_check(td, lsremote_text="junk\nno tags here\n")
        assert code == 2, (code, lines)
        n += 1
    code, lines = run_check(os.path.join(tempfile.gettempdir(), "no-such-root-xyz"))
    assert code == 2, (code, lines)
    n += 1
    print("check-template-updates --self-test: %d/%d assertions PASS" % (n, n))
    return 0


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return self_test()
    root = os.getcwd()
    if "--root" in argv:
        root = argv[argv.index("--root") + 1]
    code, lines = run_check(root)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
