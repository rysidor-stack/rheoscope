"""
core/governance/check-reference-integrity.py — catch governing-doc references that
point at files missing from the committed tree.

A deterministic drift sensor for one recurring failure class: a control-plane artifact
(a governing doc) asserts that a file is canonical while that file was never committed —
so the committed doc points at nothing. The reference dangles silently until someone
notices. This script re-derives the spec-vs-reality gap every run so it cannot be
forgotten; it is wired into /flight-plan (Step 5.6), which re-checks every session, so a
dangling reference re-surfaces in every briefing until a human commits the one-line fix.

Scope (PHASE 1 — deliberately narrow):
  * Source: markdown link targets `](path)` in the COMMITTED governing doc(s)
    (`git show HEAD:<doc>`) — NOT the working-tree copy. An untracked file "exists" in the
    working tree and would wrongly pass; the committed tree is the reality the committed
    doc is judged against. Targets resolve relative to each doc's own location (standard
    markdown semantics), so a governing doc in a subdirectory (core/governance/CLAUDE.md)
    can reference its siblings and parents; a doc at the repo root resolves targets as
    plain repo-relative paths (identical to the original single-doc-at-root behavior).
  * Only link targets in PROSE are checked. Targets inside fenced code blocks (``` / ~~~)
    are illustrative template content, not real references. Including them drowns the
    signal in false positives, and a noisy sensor gets muted.
  * Skipped: external URLs (scheme://, mailto:), absolute paths, #anchors, globs (*), and
    template placeholders — date stamps (YYYY), mustache substitution markers, and
    angle-bracket slugs. The high-signal subset is repo-relative markdown link targets:
    canonical refs are links; examples are plain/inline-code text.

Governing docs checked:
  * Default (no args): core/governance/CLAUDE.md — the harness's governing doc.
  * Additional docs may be passed as arguments. /flight-plan passes core/governance/CLAUDE.md
    plus every project.yaml `governance_docs[].path`, so the whole declared governing set is
    covered, not just the root doc.
  * HARNESS-CHANGELOG.md is deliberately OUT of scope (v2.0 #10c decision, backlog v2.0-1):
    it is an append-only historical record, so references in old entries legitimately rot
    as the tree evolves — failing on them would pressure history rewrites. (The dangling
    reference that prompted the question was also inline-code, which this sensor never
    checks — only markdown link targets.) Revisit only if the class recurs.

Classification of each link target against HEAD (committed-doc vs committed-tree):
  OK                    present in HEAD (a file, or a directory with tracked files under it)
  UNCOMMITTED           not in HEAD but present untracked in the working tree
                        (fix: git add + commit)
  DELETION-UNCOMMITTED  in HEAD but locally deleted (unstaged) — committing the deletion
                        will dangle the link (fix: restore the file, or fix the link)
  DANGLING              nowhere — not in HEAD, not in the working tree (fix: correct the link)

Detect-only. The reconcile is a human `git commit` (one line) once surfaced — this script
never writes. Local-only: pure stdlib + git, no SSH / network. Best-effort: it exits 2
(inconclusive) rather than crashing when git or HEAD is unavailable, so the /flight-plan
step can degrade gracefully.

Exit codes:
  0  clean — every checked link target resolves in HEAD
  1  one or more reference violations (actionable)
  2  inconclusive — no governing doc could be read from HEAD (no git / no HEAD / not
     committed), or git is unavailable

Usage:
  py core/governance/check-reference-integrity.py [doc1 doc2 ...]
"""
import os
import posixpath
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# This script lives at <repo>/core/governance/ — two levels below the repo root.
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))

# Governing doc checked when no arguments are given.
DEFAULT_DOCS = ['core/governance/CLAUDE.md']

# Markdown inline-link target: the `dest` in `[text](dest)`. Restricted to a single line
# (no newline inside the parens) so an unclosed paren cannot gobble across lines.
LINK_RE = re.compile(r'\]\(([^)\n]+)\)')

# A line that opens or closes a fenced code block: ``` or ~~~ (3+), optional indent/info.
FENCE_RE = re.compile(r'^\s*(`{3,}|~{3,})')

FIX_HINTS = {
    'UNCOMMITTED': 'untracked in the working tree — `git add` it, then commit',
    'DELETION-UNCOMMITTED': 'in HEAD but locally deleted (unstaged) — restore the file or fix the link',
    'DANGLING': 'not in HEAD or the working tree — correct the link in the governing doc',
}


def run_git(*args):
    """Run `git -C REPO_ROOT <args>`; return (exit_code, stdout). UTF-8, quotepath off."""
    try:
        proc = subprocess.run(
            ['git', '-C', REPO_ROOT, '-c', 'core.quotepath=false', *args],
            capture_output=True, encoding='utf-8', errors='replace',
        )
    except FileNotFoundError:
        return 127, ''  # git binary not on PATH
    return proc.returncode, proc.stdout


def strip_fenced_blocks(text):
    """Drop lines inside ```/~~~ fenced code blocks; return the prose lines joined.

    Phase-1 simplification: toggle on any line starting with 3+ backticks/tildes. It does
    not track fence char/length matching (CommonMark) — unnecessary here and not worth the
    complexity.
    """
    out, in_fence = [], False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue  # drop the fence delimiter line itself
        if not in_fence:
            out.append(line)
    return '\n'.join(out)


def normalize_target(target):
    """Reduce a raw link dest to a repo-relative path: drop `"title"`, #fragment, ./, /."""
    t = target.strip()
    parts = t.split()
    if parts:
        t = parts[0]          # drop a `path "Title"` title
    t = t.split('#', 1)[0]    # drop a #fragment
    if t.startswith('./'):
        t = t[2:]
    return t.rstrip('/')


def is_skippable(t):
    """True for targets that are not repo-relative file paths (do not classify these)."""
    if not t:
        return True
    if t.startswith('#'):                            # pure in-page anchor
        return True
    if t.startswith('/'):                            # absolute / site-root path
        return True
    if re.match(r'^[A-Za-z][A-Za-z0-9+.\-]*:', t):   # scheme: http: https: mailto: tel: C:
        return True
    if '*' in t:                                     # glob
        return True
    if 'YYYY' in t:                                  # date template placeholder
        return True
    if '<' in t or '>' in t:                         # angle-bracket slug placeholders
        return True
    if '{' in t or '}' in t:                         # mustache substitution placeholders
        return True
    return False


def dir_member(paths, t):
    """True if any path in `paths` lives under directory `t` (i.e. `t/` is a real dir)."""
    prefix = t + '/'
    return any(p.startswith(prefix) for p in paths)


def classify(t, head_files, untracked, deleted):
    """Classify a repo-relative target against committed tree + working-tree state."""
    if t in head_files:
        return 'DELETION-UNCOMMITTED' if t in deleted else 'OK'
    if dir_member(head_files, t):
        return 'OK'                                  # directory present in HEAD
    if t in untracked or dir_member(untracked, t):
        return 'UNCOMMITTED'
    if t in deleted:
        return 'DELETION-UNCOMMITTED'
    return 'DANGLING'


def resolve_target(docpath, t):
    """Resolve a link target relative to the doc's directory → a repo-relative path.

    Markdown links are relative to the file that contains them, so a governing doc in a
    subdirectory (e.g. core/governance/CLAUDE.md) references siblings/parents accordingly:
    `HARDCONSTRAINTS.md` → core/governance/HARDCONSTRAINTS.md, `../methodology/x.md` →
    core/methodology/x.md. A doc at the repo root resolves targets as plain repo-relative
    paths (identical to the original source project behavior, where the doc was always at root).
    Returns None if the target escapes the repo root (so it cannot be classified).
    """
    docdir = posixpath.dirname(docpath)
    joined = posixpath.normpath(posixpath.join(docdir, t) if docdir else t)
    if joined == '.' or joined == '..' or joined.startswith('../'):
        return None
    return joined


def extract_targets(doc_text):
    """Prose markdown link targets in doc_text: de-duped, skippables removed.

    Returns (targets, skipped_count).
    """
    prose = strip_fenced_blocks(doc_text)
    seen, targets, skipped = set(), [], 0
    for m in LINK_RE.finditer(prose):
        t = normalize_target(m.group(1))
        if is_skippable(t):
            skipped += 1
            continue
        if t in seen:
            continue
        seen.add(t)
        targets.append(t)
    return targets, skipped


def main():
    global REPO_ROOT
    sys.stdout.reconfigure(encoding='utf-8')

    docs = [d.replace('\\', '/') for d in sys.argv[1:]] or list(DEFAULT_DOCS)

    print('Reference integrity — markdown link targets in committed governing doc(s) vs HEAD')

    # --- sanity-check the repo root (handles subdir / symlink invocation, and confirms
    #     REPO_ROOT really is a git repo before any per-doc reads) ---
    rc_root, toplevel = run_git('rev-parse', '--show-toplevel')
    if rc_root != 0:
        print(f'  INCONCLUSIVE: {REPO_ROOT} is not a git repo / git unavailable '
              f'(git rev-parse exit {rc_root}).')
        print()
        print('RESULT: INCONCLUSIVE — reference check could not run (no git repo / no HEAD).')
        return 2
    toplevel = toplevel.strip()
    if toplevel:
        REPO_ROOT = toplevel  # authoritative root from git itself

    print(f'  Repo: {REPO_ROOT}')
    print(f'  Docs: {", ".join(docs)}')

    # --- committed-tree + working-tree state (one git call each, reused across docs) ---
    rc_tree, tree = run_git('ls-tree', '-r', '--name-only', 'HEAD')
    if rc_tree != 0:
        print('  INCONCLUSIVE: could not list the HEAD tree (unborn HEAD?).')
        print()
        print('RESULT: INCONCLUSIVE — reference check could not run.')
        return 2
    head_files = set(filter(None, tree.splitlines()))
    _, others = run_git('ls-files', '--others', '--exclude-standard')
    untracked = set(filter(None, others.splitlines()))
    _, dele = run_git('ls-files', '--deleted')
    deleted = set(filter(None, dele.splitlines()))

    violations = []          # list of (doc, status, target)
    total_targets = 0
    total_skipped = 0
    unreadable = []
    checked_any = False

    for doc in docs:
        rc, text = run_git('show', f'HEAD:{doc}')
        if rc != 0:
            unreadable.append(doc)
            print(f'  - {doc}: INCONCLUSIVE — not readable from HEAD (not committed?)')
            continue
        checked_any = True
        raw_targets, skipped = extract_targets(text)
        doc_v = []
        n_checked = 0
        for rt in raw_targets:
            rp = resolve_target(doc, rt)
            if rp is None:
                skipped += 1                 # target escapes the repo root — out of scope
                continue
            n_checked += 1
            status = classify(rp, head_files, untracked, deleted)
            if status != 'OK':
                shown = rp if rp == rt else f'{rt} -> {rp}'
                doc_v.append((doc, status, shown))
        total_targets += n_checked
        total_skipped += skipped
        violations.extend(doc_v)
        print(f'  - {doc}: {"FAIL" if doc_v else "PASS"} '
              f'({n_checked} prose target(s), {skipped} skipped, {len(doc_v)} violation(s))')

    print()
    if violations:
        width = max(len(s) for _, s, _ in violations)
        for doc, status, t in violations:
            print(f'  {status.ljust(width)}  {t}  [{doc}]  — {FIX_HINTS.get(status, "")}')
        print()
        print(f'RESULT: FAIL — {len(violations)} reference violation(s). '
              f'A committed governing doc references path(s) missing from the committed tree.')
        return 1

    if not checked_any:
        print('RESULT: INCONCLUSIVE — no governing doc could be read from HEAD '
              f'({", ".join(unreadable)}).')
        return 2

    msg = (f'RESULT: PASS — all {total_targets} prose link target(s) across '
           f'{len(docs) - len(unreadable)} doc(s) resolve in HEAD '
           f'({total_skipped} non-path target(s) skipped).')
    if unreadable:
        msg += f' NOTE: {len(unreadable)} declared doc(s) not committed, skipped: {", ".join(unreadable)}.'
    print(msg)
    return 0


if __name__ == '__main__':
    sys.exit(main())
