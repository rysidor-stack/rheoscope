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

PHASE 2 — the full-tree citation sweep (`--sweep`, silence-sweep remediation item 3,
2026-08-05). The 2026-08-04 audit's Mechanism 1 ("phantom authority") showed the phase-1
scope missed the dominant citation channel: INLINE-CODE and bare-token path citations in
prose, across ALL shipped docs and in Python module docstrings + FIX/guidance strings —
`/orient` routing release-history questions to a changelog that never shipped, doctor FIX
lines prescribing a maintenance doc that never shipped, three protocol docs declaring a
never-written METHODOLOGY.md their superseding canon. `--sweep` extracts every path-shaped
citation from every tracked .md (markdown links + inline code + bare path tokens) and every
tracked .py (module docstring + lines carrying `FIX:`), then classifies each against HEAD:

  * A citation resolves if it exists in EITHER layout — template form or instance form —
    because shipped docs legitimately describe the post-init world: `deploy/x.py` ↔
    `capabilities/knowledge-os/extracted/deploy/x.py`, `docs/engine/X` ↔ `.../extracted/
    engine/X`, `.claude/skills/<n>/` ↔ `core/skills/<n>/`, and `.template`/`.example`
    suffixes satisfy their rendered names (`project.yaml` ↔ `project.yaml.example`).
  * Citations to RUNTIME-MINTED artifacts (wiki/, raw/, receipts/, dated handoff records,
    operator evidence, briefing projections...) are SKIPPED as a NAMED, COUNTED class —
    they cannot exist in a template tree and flagging them would drown the signal (a noisy
    sensor gets muted). Skips are always counted, never silent.
  * Append-only history (HARNESS-CHANGELOG.md, MIGRATION.md, changelog.md) is excluded as a
    SOURCE (same v2.0 #10c rationale as phase 1): old entries legitimately rot. Dev-only
    history dirs (audits/, harness-v*/, verifier-reviews/, design-history/) likewise.
  * Everything else that dangles in BOTH layouts is a violation. What this sweep can NOT
    catch, stated honestly: citations that resolve on the template but die at init (the
    post-init dead-path class — core/skills/ cites in TOUR/GLOSSARY survive init's
    deletion), and non-path phantom authorities ("the hub", "hard rule 4", ADR-#1-style
    commitments). Those remain human-triage classes from the audit.

Exit codes:
  0  clean — every checked link target resolves in HEAD
  1  one or more reference violations (actionable)
  2  inconclusive — no governing doc could be read from HEAD (no git / no HEAD / not
     committed), or git is unavailable

Usage:
  py core/governance/check-reference-integrity.py [doc1 doc2 ...]   # phase-1 governing docs
  py core/governance/check-reference-integrity.py --sweep [--root DIR]
  py core/governance/check-reference-integrity.py --self-test
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


# --------------------------------------------------------------------------- phase 2: sweep

# Source files excluded from the sweep: append-only history whose old entries
# legitimately rot (phase-1 precedent, v2.0 #10c), and dev-only history dirs.
SWEEP_EXCLUDE_BASENAMES = {
    'HARNESS-CHANGELOG.md', 'MIGRATION.md', 'changelog.md',
    # teaching/format docs whose citations are deliberately fictional
    # project content (worked examples of what an INSTANCE might contain)
    'EXAMPLES.md', 'CONTEXT-FORMAT.md', 'REPO-GROUNDING.md',
    # a shipped historical audit record -- same append-only class as audits/
    'init-script-audit.md',
    # ratified/certified-scrubbed rendering (v3.0.18): its case-study
    # citations are historical fork content and the text is frozen by rule
    'manifest-driven-builds.md',
}

# usage-example placeholder names in sensor docstrings and format docs --
# arguments in illustrative command lines ("--manifest ledger-slice.json"),
# fictional case-study files in the manifest contract. Skipped as a NAMED,
# COUNTED class; each entry exists because flagging it would prescribe an
# edit to an example, not a citation fix.
SWEEP_ILLUSTRATIVE_TARGETS = {
    'foo.yaml', 'ledger-slice.json', 'roadmap.md', 'llm-wiki.md',
    'views/view-stale-verified.md', 'disposition.py',
    'exports/checkout-2026-06-01.json', 'orders-web-SEED.json',
    'pricing-spec.md', 'legacy-pricing-engine.js',
}
SWEEP_EXCLUDE_SRC_PREFIXES = (
    'audits/', 'verifier-reviews/', 'harness-v2.0/', 'harness-v3.0/', 'adr/',
    'handoffs/', 'capabilities/knowledge-os/design-history/',
    # certified byte-frozen engine design docs (v3.0.18 provenance banners):
    # their internal citations are historical fork-layout BY RULE -- certified
    # text is never edited as doctrine, so flagging them prescribes an edit
    # the harness forbids. The LIVE engine doctrine (OPERATIONS.md,
    # wiki-schema) stays fully swept.
    'capabilities/knowledge-os/extracted/engine/memory-engine-v3-',
    'capabilities/knowledge-os/extracted/engine/engine-verification-ledger',
    # runtime/content trees on instances -- their content is knowledge, not doctrine
    'wiki/', 'raw/', 'receipts/', 'intake/', 'deliverables/', 'assets/',
    # committed test fixtures cite fixture-relative paths by design
    'test-fixtures/',
    # worked-example instance content shipped for illustration
    'capabilities/code-conventions/examples/',
)
# deferred capability designs cite files their build would create
SWEEP_EXCLUDE_SRC_FRAGMENTS = ('/deferred/', '/examples/', '/test-fixtures/')

# Citation targets that are minted at runtime on an instance and can never
# exist in a template tree -- skipped as a NAMED class, counted, never silent.
RUNTIME_TARGET_PREFIXES = (
    'wiki/', 'raw/', 'receipts/', 'intake/', 'manifests/', '.batch-run/',
    'deliverables/', 'assets/', 'deploy/evidence/', 'docs/adr/',
    'docs/flight-plans/', 'docs/governance/PROJECT-COMPASS',
    'docs/governance/project-thesis', '.claude/settings',
    '.claude/nightly-sweep',
    # instance-authored homes a recipe/kickoff tells the project to create
    'references/', 'methodology/',
)
RUNTIME_TARGET_NAMES = {
    'SESSION-BRIEFING.md', 'SWEEP-BRIEFING.md', 'DECISIONS-PENDING.md',
    'STANDING-LOOP-LOG.md', 'DESK.html', 'EMPIRE-DESK.md',
    'sweep-schedule.log', 'settings.local.json',
    # wiki/handoff projections minted at runtime, cited by shorthand basename
    'REVIEW.md', 'HEALTH.md', 'HALT.md', 'PENDING.md',
    # per-handoff record files (protocol-internal, minted per decision)
    'close-packet.md', 'close-deliverable.json', 'close-deliverable.attest.json',
    'confidence-audit.md', 'brief.md', 'context.md',
    # per-run staging/config artifacts minted by the pipeline or the instance
    'dispatch-manifest.json', 'golden-descriptors.yaml',
    # illustrative throwaway scripts the docs say to write once and discard
    'run_event.py', 'verify_event.py', 'stage.py',
}
RUNTIME_ROUND_RE = re.compile(r'^(packet|output)-round-')
# dated handoff record folders (either envelope) are runtime records
RUNTIME_HANDOFF_RE = re.compile(r'^(core/)?handoffs/\d{4}-\d{2}-\d{2}-')
# the handoff envelope's own runtime INDEX
RUNTIME_INDEX_RE = re.compile(r'^(core/)?handoffs/INDEX\.md$')

# template-form <-> instance-form layout mappings (both directions tried)
LAYOUT_MAPS = [
    ('deploy/', 'capabilities/knowledge-os/extracted/deploy/'),
    ('docs/engine/', 'capabilities/knowledge-os/extracted/engine/'),
    ('docs/', 'capabilities/knowledge-os/extracted/'),
    ('.claude/skills/', 'core/skills/'),
    ('.agents/skills/', 'core/skills/'),
]
# instance root docs whose template home is elsewhere
NAME_MAPS = {'CLAUDE.md': 'core/governance/CLAUDE.md.template'}

# path-shaped tokens: slashed path with a harness-relevant extension
# (optionally ../-prefixed, resolved doc-relative), or a bare filename
# (MAINTENANCE.md, doctor.py, ...). Extensions outside the harness's own
# file classes (.ts, .tsx, ...) are worked-example project content.
_EXTS = r'(?:md|py|yaml|yml|json|sh|ps1|js|cmd)'
SLASHED_PATH_RE = re.compile(
    r'(?<![\w./-])((?:\.\./)*(?:[A-Za-z0-9_-][A-Za-z0-9_.\-]*/)+'
    r'[A-Za-z0-9_\-][A-Za-z0-9_.\-]*\.' + _EXTS + r')(?![\w/])')
BARE_FILE_RE = re.compile(
    r'(?<![\w./-])([A-Za-z0-9][A-Za-z0-9_\-]*[A-Za-z0-9]\.' + _EXTS + r')(?![\w/])')
# prose like "init.sh/init.ps1" scans as a slashed path whose non-final
# segment carries a file extension -- that is a sentence, not a path
MIDSEG_EXT_RE = re.compile(r'\.' + _EXTS + r'/')
INLINE_CODE_RE = re.compile(r'`([^`\n]+)`')
FIXLINE_RE = re.compile(r'FIX:', re.I)


def _suffix_forms(path):
    forms = {path, path + '.template', path + '.example'}
    if path.endswith('.template'):
        forms.add(path[:-len('.template')])
    if path.endswith('.example'):
        forms.add(path[:-len('.example')])
    return forms


def sweep_candidates(t):
    """Every path a citation `t` may legitimately live at across the two
    layouts (template form vs instance form) and rendered-name suffixes."""
    cands = _suffix_forms(t)
    if t in NAME_MAPS:
        cands.add(NAME_MAPS[t])
    mapped = set()
    for c in cands:
        for a, b in LAYOUT_MAPS:
            if c.startswith(a):
                mapped.add(b + c[len(a):])
            if c.startswith(b):
                mapped.add(a + c[len(b):])
    for m in list(mapped):
        mapped |= _suffix_forms(m)
    return cands | mapped


def is_runtime_target(t):
    if RUNTIME_HANDOFF_RE.match(t) or RUNTIME_INDEX_RE.match(t):
        return True
    base = t.rsplit('/', 1)[-1]
    if base in RUNTIME_TARGET_NAMES or RUNTIME_ROUND_RE.match(base):
        return True
    return any(t.startswith(p) for p in RUNTIME_TARGET_PREFIXES)


def _extract_tokens(scope):
    found = [pm.group(1) for pm in SLASHED_PATH_RE.finditer(scope)]
    found.extend(pm.group(1) for pm in BARE_FILE_RE.finditer(scope))
    return found


def sweep_extract_md(text):
    """Path-shaped citations in a markdown doc: link targets + inline-code /
    fenced-block tokens + bare path tokens in prose. Fenced blocks stay
    INCLUDED here -- unlike phase 1, shipped docs put their real citations in
    fences and inline code (usage blocks, FIX examples), which is exactly the
    channel Mechanism 1 rode in on. The placeholder skips keep the noise down."""
    found = [normalize_target(m.group(1)) for m in LINK_RE.finditer(text)]
    found.extend(_extract_tokens(text))
    return found


def sweep_extract_py(text):
    """Citations in a Python file: the module docstring + every line carrying
    FIX: (doctor's remediation strings and their kin)."""
    scopes = []
    m = re.match(r'^(?:#[^\n]*\n|\s)*("""|\'\'\')(.*?)\1', text, re.S)
    if m:
        scopes.append(m.group(2))
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if FIXLINE_RE.search(ln):
            # FIX guidance strings are usually concatenated across source
            # lines; take the line plus the next two so a cited path split
            # onto the continuation line is still seen
            scopes.append('\n'.join(lines[i:i + 3]))
    found = []
    for scope in scopes:
        found.extend(_extract_tokens(scope))
    return found


def run_sweep(root=None):
    """Full-tree citation sweep. Returns an exit code."""
    global REPO_ROOT
    if root:
        REPO_ROOT = os.path.abspath(root)
    rc_root, toplevel = run_git('rev-parse', '--show-toplevel')
    if rc_root != 0:
        print('RESULT: INCONCLUSIVE — %s is not a git repo / git unavailable.' % REPO_ROOT)
        return 2
    rc_tree, tree = run_git('ls-tree', '-r', '--name-only', 'HEAD')
    if rc_tree != 0:
        print('RESULT: INCONCLUSIVE — could not list the HEAD tree.')
        return 2
    head_files = set(filter(None, tree.splitlines()))

    SRC_EXT = ('.md', '.py', '.yaml', '.yml', '.yaml.example', '.yml.example')
    sources = [p for p in sorted(head_files)
               if p.endswith(SRC_EXT)
               and os.path.basename(p) not in SWEEP_EXCLUDE_BASENAMES
               and not any(p.startswith(x) for x in SWEEP_EXCLUDE_SRC_PREFIXES)
               and not any(f in p for f in SWEEP_EXCLUDE_SRC_FRAGMENTS)]

    print('Reference integrity — full-tree citation sweep (phase 2)')
    print('  Repo: %s   sources: %d files' % (REPO_ROOT, len(sources)))

    head_basenames = {p.rsplit('/', 1)[-1] for p in head_files}

    violations = []   # (source, target)
    n_checked = 0
    n_runtime = 0
    n_placeholder = 0
    n_basename = 0
    n_illustrative = 0
    seen_pairs = set()
    for src in sources:
        rc, text = run_git('show', 'HEAD:%s' % src)
        if rc != 0:
            continue
        if src.endswith('.md'):
            tokens = sweep_extract_md(text)
        elif src.endswith('.py'):
            tokens = sweep_extract_py(text)
        else:
            tokens = _extract_tokens(text)   # yaml example headers/comments
        for tok in tokens:
            t = normalize_target(tok)
            if is_skippable(t) or '...' in t or MIDSEG_EXT_RE.search(t):
                n_placeholder += 1
                continue
            if t in SWEEP_ILLUSTRATIVE_TARGETS:
                n_illustrative += 1
                continue
            if is_runtime_target(t):
                n_runtime += 1
                continue
            key = (src, t)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            if '/' not in t:
                # bare filename: a shorthand citation resolves if ANY tracked
                # file carries that basename (or its rendered/.template form)
                n_checked += 1
                if any(b in head_basenames for b in
                       (t, t + '.template', t + '.example')) \
                        or (t.endswith('.template') and t[:-9] in head_basenames) \
                        or (t.endswith('.example') and t[:-8] in head_basenames):
                    n_basename += 1
                else:
                    violations.append((src, t))
                continue
            n_checked += 1
            cands = set(sweep_candidates(t))
            rel = resolve_target(src, t)
            if rel is not None and rel != t:
                cands |= sweep_candidates(rel)
            # "uncle" resolution: a doc citing a sibling DIRECTORY's file by
            # shorthand (capabilities/x/RECIPE.md citing stress-testing/RECIPE.md)
            docdir = posixpath.dirname(src)
            if docdir:
                uncle = posixpath.normpath(posixpath.join(posixpath.dirname(docdir), t))
                if not uncle.startswith('..'):
                    cands |= sweep_candidates(uncle)
            if not any(c in head_files or dir_member(head_files, c)
                       for c in cands) \
                    and not any(p.endswith('/' + t) for p in head_files):
                violations.append((src, t))

    print('  %d unique citation(s) checked (%d resolved by basename); skipped '
          'as runtime-minted: %d; illustrative: %d; placeholder/non-path: %d'
          % (n_checked, n_basename, n_runtime, n_illustrative, n_placeholder))
    print()
    if violations:
        by_target = {}
        for src, t in violations:
            by_target.setdefault(t, []).append(src)
        for t in sorted(by_target):
            srcs = by_target[t]
            print('  DANGLING  %s  — cited by %d file(s): %s%s'
                  % (t, len(srcs), ', '.join(srcs[:4]),
                     ' ...' if len(srcs) > 4 else ''))
        print()
        print('RESULT: FAIL — %d dangling citation(s) across %d phantom target(s). '
              'Each cited path exists in neither the template layout nor the '
              'instance layout of this tree.' % (len(violations), len(by_target)))
        return 1
    print('RESULT: PASS — all %d checked citation(s) resolve in HEAD (either layout).'
          % n_checked)
    return 0


def run_sweep_self_test():
    """Inline git-tempdir fixtures for the sweep (check-loop-state (1a3) house
    style): dangling vs resolving vs runtime-skip vs layout/suffix-mapped."""
    import shutil
    import tempfile
    global REPO_ROOT
    saved_root = REPO_ROOT
    failures = []

    def build_repo(files):
        d = tempfile.mkdtemp(prefix='cri-')
        subprocess.run(['git', '-C', d, 'init', '-q'], capture_output=True)
        subprocess.run(['git', '-C', d, 'config', 'user.email', 't@t'], capture_output=True)
        subprocess.run(['git', '-C', d, 'config', 'user.name', 't'], capture_output=True)
        for rel, content in files.items():
            p = os.path.join(d, *rel.split('/'))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'w', encoding='utf-8') as fh:
                fh.write(content)
        subprocess.run(['git', '-C', d, 'add', '-A'], capture_output=True)
        subprocess.run(['git', '-C', d, 'commit', '-qm', 'fixture'], capture_output=True)
        return d

    cases = [
        ('dangling inline-code + bare-token + FIX-line citations FAIL', {
            'doc.md': 'See `deploy/README.md` and consult MAINTENANCE.md for the ritual.\n',
            'deploy/sensor.py': '"""Cites harness-v3.0/specs/spec-x.md as its authority."""\n'
                                'X = 1\n# FIX: see HARNESS-CHANGELOG.md for history\n',
        }, 1, {'deploy/README.md', 'MAINTENANCE.md', 'harness-v3.0/specs/spec-x.md',
               'HARNESS-CHANGELOG.md'}),
        ('resolving citations PASS (real file, suffix form, layout map, runtime skip)', {
            'doc.md': 'Run `deploy/real.py` against `project.yaml`; output lands in '
                      '`wiki/HEALTH.md` and `receipts/verify/x.json`. Docs: '
                      '[ops](docs/engine/OPERATIONS.md).\n',
            'capabilities/knowledge-os/extracted/deploy/real.py': 'X = 1\n',
            'capabilities/knowledge-os/extracted/engine/OPERATIONS.md': '# ops\n',
            'project.yaml.example': 'a: 1\n',
        }, 0, set()),
        ('dated handoff records + evidence artifacts are runtime skips, '
         'envelope protocol doc is not', {
            'doc.md': 'Records at `core/handoffs/2026-01-01-x/meta.yaml`; grant at '
                      '`deploy/evidence/operator-x.md`; canon is '
                      '`core/handoffs/METHODOLOGY.md`.\n',
        }, 1, {'core/handoffs/METHODOLOGY.md'}),
        ('bare-name shorthand resolves by basename; illustrative names skip', {
            'doc.md': 'See GUIDE.md for the walkthrough; try `foo.yaml` as input; '
                      'MISSING-DOC.md is the one that dangles.\n',
            'core/onboarding/GUIDE.md': '# guide\n',
        }, 1, {'MISSING-DOC.md'}),
    ]
    import io
    from contextlib import redirect_stdout
    for name, files, want_rc, want_targets in cases:
        d = build_repo(files)
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_sweep(root=d)
            out = buf.getvalue()
            got_targets = set(re.findall(r'DANGLING\s+(\S+)', out))
            if rc != want_rc or got_targets != want_targets:
                failures.append('%s: rc=%s (want %s), targets=%s (want %s)'
                                % (name, rc, want_rc, sorted(got_targets),
                                   sorted(want_targets)))
        finally:
            REPO_ROOT = saved_root
            shutil.rmtree(d, ignore_errors=True)

    if failures:
        for f in failures:
            print('  FAIL  %s' % f)
        print('check-reference-integrity self-test: FAIL (%d/%d)'
              % (len(cases) - len(failures), len(cases)))
        return 1
    print('check-reference-integrity self-test: PASS (%d/%d)' % (len(cases), len(cases)))
    return 0


def main():
    global REPO_ROOT
    sys.stdout.reconfigure(encoding='utf-8')

    argv = sys.argv[1:]
    if '--self-test' in argv:
        return run_sweep_self_test()
    if '--sweep' in argv:
        root = None
        if '--root' in argv:
            i = argv.index('--root')
            if i + 1 >= len(argv):
                print('usage: --root DIR')
                return 2
            root = argv[i + 1]
        return run_sweep(root=root)

    docs = [d.replace('\\', '/') for d in argv] or list(DEFAULT_DOCS)

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
