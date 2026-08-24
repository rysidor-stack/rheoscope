#!/usr/bin/env python3
"""retire.py -- the view-retirement verb ("forget-down"), ADR #11 Release 2 (v3.0.50,
backlog v3.0-139 / v3.0-129; design brief v4 section 2.1; condition 4 as amended
2026-08-22).

WHAT IT DOES. It PREPARES a retirement and stops. A retirement moves one or more spans of a
hot view to immutable storage and leaves a stub that keeps every heading and explicit anchor
at its original path. Nothing here writes the working tree or the production branch:

  --propose   builds the retirement as ONE immutable commit C on refs/retire/<seq> with git
              plumbing (temporary index, write-tree, commit-tree, create-only update-ref),
              whose tree carries: the rewritten view (span -> stub; redirect entry appended
              to the engine-owned block inside the derivation region), the content-addressed
              cold object (cold-relocate mode), the proposal artifact
              (deploy/rulings/retire-<seq>/proposal.md: the COMPLETE retiring preimage, the
              stub, the redirect entries, the whole-view diff, every hash -- the thing the
              operator promotes), and the journal record receipts/journal/<seq>.json
              (run_type retire, chained onto the branch tip's last record, carrying the
              proposal digest and every identity the oracle recomputes). Prints the seq, C
              and the proposal digest. A retirement is therefore COMPLETE-OR-ABSENT by
              construction: the ref is the last write, and until it exists nothing exists.
  --recover   deterministic crash recovery: every refs/retire/<seq> is re-derived from
              objects (record <-> proposal digest, view@C == view@parent with the span
              replaced by the exact stub, cold blob == span bytes) and COMPLETED (kept,
              reported as awaiting promotion) or DISCARDED (ref deleted, reason printed);
              a prepared C whose parent is no longer the branch head is STALE and discarded.
  --resolve   citation resolution through the redirect block and cold index: a legacy
              (untagged) citation via the frozen legacy registry, a generation-tagged
              citation (`view.md:80@<hash8>`) via the generation it names.
  --register-legacy  freezes every pre-Release-2 citation into
              receipts/citations/legacy-<date>.json (append-only; built from the read-only
              manifest's seed; refuses if the citation universe cannot be established).
  --list      prepared retirements (refs/retire/*) with their digests and promotion state.
  --show      prints a prepared proposal artifact by digest.

PUBLICATION is NOT here. Under trust_surface_signing: visible the operator runs
`py deploy/promote.py <proposal-digest>` from their own terminal (one action per batch;
it constructs the promotion record and fast-forwards the branch atomically); under
`required` the operator signs `git tag -s retire/<seq> C` with the presence-requiring key
and `trust.py --publish` fast-forwards. Under an unchosen mode nothing publishes.

GATES (brief section 2.1, every one pinned both directions in --self-test):
  1 anchor conservation   check-split's anchor multiset of the view is unchanged.
  2 citation resolution   every inbound line citation into a retiring span (from the
                          COMPLETE citation universe: registered artifacts + wiki tree)
                          is either generation-tagged and resolvable, or legacy AND present
                          in the frozen registry; else REFUSE naming it. Universe not
                          establishable -> REFUSE.
  3 content conservation  cold object bytes == the LF-normalized span bytes (SHA-256 AND a
                          byte compare on reuse); copied stub lines are verbatim span lines
                          (inline anchors extracted onto their own line are recorded);
                          generated lines match the template exactly; post view == pre
                          view with the span replaced by the exact stub.
  4 claim preservation    ledger-dedup only: every substantive line of the span maps to an
                          exact line of an immutable raw/ event at the branch tip; any
                          unmapped line DOWNGRADES the span to cold-relocate (never drop).
  5 informed authority    the proposal artifact DISPLAYS the full preimage; its digest is
                          what the promote action names; the engine re-derives every bound
                          value at publication (trust.py check_publishable) and a consumed
                          or mismatched digest refuses.

RELEASE SCOPE. One view per proposal, targeted retirements only (the views blocking
verified corrections) -- broad migration waits for Release 3's brake (ADR #11 phasing).
RELEASE_SCOPE below records the G2 state of THIS build: "disabled" until the clean-oracle
acceptance run passed, then "targeted".

Usage:
  retire.py --root R --propose wiki/<view>.md (--span "Title" ... | --preamble)
            [--mode cold|dedup] [--mapping m.json] [--branch main]
  retire.py --root R --recover [--branch main]
  retire.py --root R --list | --show DIGEST | --resolve "view.md:80[@hash8]" [--from ARTIFACT]
  retire.py --root R --register-legacy
  retire.py --self-test

Exit: 0 ok | 1 self-test failure | 2 refusal / inconclusive.
"""

import argparse
import datetime
import difflib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_by_path(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_trust = _load_by_path("_retire_trust", "trust.py")
_manifest = _load_by_path("_retire_manifest", "retire-manifest.py")
_split = _load_by_path("_retire_split", "check-split.py")
_core = _load_by_path("_retire_core", "compile-core.py")
_caps = _manifest._caps

# The G2 state of this build (ADR #11 condition 10 / the amended acceptance item 5):
# "disabled" = the verb refuses --propose; "targeted" = Release-2 production use (one view
# per proposal, operator-promoted). Flipped ONLY by the build that ran the clean-oracle
# acceptance (harness-v3.0/stranger-tests/v3.0.50-g2-e2e.py) to PASS.
RELEASE_SCOPE = "targeted"

JOURNAL_DIR = "receipts/journal"
RULINGS_DIR = "deploy/rulings"
COLD_DIR = "wiki/cold"
LEGACY_DIR = "receipts/citations"
RET_START = "# --- retirements"
RET_END = "# --- /retirements"
DERIV_START = _caps.DERIV_START
DERIV_END = _caps.DERIV_END
POINTER_TMPL = "> Retired to %s -- journal seq %d."
TAGGED_CITE_RE = _split.TAGGED_CITE_RE  # single home: check-split.py (v3.0-141 parity)
SLUG_MAX = 32  # cold-object section slug cap; the 64-hex content address follows it
DEFAULT_BLOCK_MAX_ENTRIES = 20
DEFAULT_BLOCK_MAX_BYTES = 8192


class Refuse(Exception):
    """A refusal with a reason the caller surfaces verbatim."""


# ------------------------------------------------------------------ git plumbing
def _git(repo, *args, input_bytes=None, env=None):
    cmd = ["git", "--no-replace-objects", "-C", repo] + list(args)
    p = subprocess.run(cmd, input=input_bytes, capture_output=True, env=env)
    return p.returncode, p.stdout, p.stderr


def _git_text(repo, *args, env=None):
    rc, out, err = _git(repo, *args, env=env)
    return rc, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _rev(repo, ref):
    rc, out, _ = _git_text(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
    return out.strip() if rc == 0 else None


def _blob(repo, commit, path):
    rc, out, _ = _git(repo, "cat-file", "blob", "%s:%s" % (commit, path))
    return out if rc == 0 else None


def _hash_object(repo, data):
    rc, out, err = _git(repo, "hash-object", "-w", "--stdin", input_bytes=data)
    if rc != 0:
        raise Refuse("git hash-object failed: %s" % err.decode("utf-8", "replace").strip())
    return out.decode().strip()


def _lf(b):
    return b.replace(b"\r\n", b"\n")


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_CAPS_OVERRIDE = None  # SELF-TEST ONLY: a dict that replaces the engine-caps.yaml reading


def _caps_cfg():
    if _CAPS_OVERRIDE is not None:
        return dict(_CAPS_OVERRIDE)
    cfg = {"entries": DEFAULT_BLOCK_MAX_ENTRIES, "bytes": DEFAULT_BLOCK_MAX_BYTES}
    try:
        text = open(os.path.join(_HERE, "engine-caps.yaml"), encoding="utf-8").read()
    except OSError:
        return cfg
    m = re.search(r"(?m)^retirements_block_max_entries:\s*(\d+)", text)
    if m:
        cfg["entries"] = int(m.group(1))
    m = re.search(r"(?m)^retirements_block_max_bytes:\s*(\d+)", text)
    if m:
        cfg["bytes"] = int(m.group(1))
    return cfg


# ------------------------------------------------------------------ the redirect block
def parse_retirements_block(text):
    """(entries, pointer, block_lines_idx) from the engine-owned block inside the derivation
    region. Rows are comment-prefixed JSON (`# {...}`) so every YAML/line reader of the
    region sees comments only. `pointer` is the compaction pointer row (index path) or None.
    Returns ([], None, None) when absent."""
    lines = text.replace("\r\n", "\n").split("\n")
    s = e = None
    for i, ln in enumerate(lines):
        st = ln.strip()
        if s is None and st == RET_START:
            s = i
        elif s is not None and st == RET_END:
            e = i
            break
    if s is None or e is None:
        return [], None, None
    entries, pointer = [], None
    for ln in lines[s + 1:e]:
        st = ln.strip()
        if not st.startswith("# "):
            raise Refuse("retirements block carries a non-comment row: %r" % st[:60])
        row = json.loads(st[2:])
        if "index" in row:
            pointer = row
        else:
            entries.append(row)
    return entries, pointer, (s, e)


def _region_bounds(lines):
    ds = de = None
    for i, ln in enumerate(lines):
        st = ln.strip()
        if ds is None and st.startswith(DERIV_START):
            ds = i
        elif ds is not None and st.startswith(DERIV_END):
            de = i
            break
    return ds, de


def render_block(entries, pointer):
    rows = [RET_START]
    if pointer:
        rows.append("# " + json.dumps(pointer, sort_keys=True, separators=(",", ":")))
    for en in entries:
        rows.append("# " + json.dumps(en, sort_keys=True, separators=(",", ":")))
    rows.append(RET_END)
    return rows


def gen_hash(text):
    """The GENERATION identity of a view: sha256 of the LF text with the engine-owned
    retirements block removed (markers inclusive). The block describes generations, so it
    cannot be part of the identity it describes; every redirect entry's pre_hash/post_hash
    and every `@hash8` citation tag use THIS. The whole-file sha256 is recorded beside it
    for the oracle (pre_view_sha256 / post_view_sha256)."""
    return _sha(strip_block(text).encode("utf-8"))


def strip_block(text):
    """The view text without its retirements block (markers inclusive)."""
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    _e, _p, idx = parse_retirements_block(text)
    if idx is not None:
        lines = lines[:idx[0]] + lines[idx[1] + 1:]
    return "\n".join(lines)


def block_bytes(text):
    """Bytes of the retirements block (markers inclusive) or b''. The absorb validator
    refuses any absorb that changes them (engine-owned, immutable to absorb)."""
    lines = text.replace("\r\n", "\n").split("\n")
    _e, _p, idx = parse_retirements_block(text)
    if idx is None:
        return b""
    return "\n".join(lines[idx[0]:idx[1] + 1]).encode("utf-8")


def all_redirects(repo, commit, view_rel, text=None):
    """Every redirect entry for the view in generation order: cold index (if any) first,
    then the block. Reads the index blob from the commit's tree."""
    if text is None:
        b = _blob(repo, commit, view_rel)
        if b is None:
            return []
        text = b.decode("utf-8", "replace")
    entries, pointer, _ = parse_retirements_block(text)
    out = []
    if pointer:
        ib = _blob(repo, commit, pointer["index"])
        if ib is None:
            raise Refuse("cold index %s named by the redirect block is absent from %s" % (
                pointer["index"], commit[:12]))
        idx = json.loads(ib.decode("utf-8-sig"))
        if _sha(_lf(ib)) != pointer["index"].rsplit("--", 1)[1][:-5]:
            raise Refuse("cold index %s does not hash to its own name" % pointer["index"])
        out.extend(idx["entries"])
    out.extend(entries)
    return out


# ------------------------------------------------------------------ spans + stubs
def _view_slug(view_rel):
    return _manifest.slug(os.path.splitext(os.path.basename(view_rel))[0])


def _bare_title(title):
    """A heading's text without a trailing `{#anchor}` attribute (titles may be named
    either way on the command line)."""
    return re.sub(r"\s*\{#[A-Za-z0-9_-]+\}\s*$", "", title).strip()


def select_spans(text, titles, preamble):
    spans = _manifest.parse_spans(text)
    chosen = []
    if preamble:
        pre = [s for s in spans if s["level"] == 0]
        if not pre:
            raise Refuse("the view has no (preamble) span to retire")
        chosen.extend(pre)
    for t in titles:
        hits = [s for s in spans if s["level"] and (s["title"] == t or _bare_title(s["title"]) == t)]
        if not hits:
            raise Refuse("no span titled %r (spans: %s)" % (
                t, ", ".join(repr(s["title"]) for s in spans if s["level"])[:400]))
        if len(hits) > 1:
            raise Refuse("span title %r is ambiguous (%d headings) -- rename one first" % (
                t, len(hits)))
        chosen.extend(hits)
    # no nesting between chosen spans, and no overlap
    chosen.sort(key=lambda s: s["start_line"])
    for a, b in zip(chosen, chosen[1:]):
        if b["start_line"] <= a["end_line"]:
            raise Refuse("spans %r and %r overlap (one nests the other); retire the outer one "
                         "alone or the inner one alone" % (a["title"], b["title"]))
    return chosen


def build_stub(lines, span, target, seq):
    """The stub contract (brief gate 3, R3-C2): copied lines (heading, descendant
    headings, explicit anchors -- verbatim span lines, or an inline anchor token extracted
    onto its own line and recorded) then generated lines (blank, pointer, blank).
    Returns (stub_lines, copied, generated, extracted)."""
    s0, s1 = span["start_line"] - 1, span["end_line"] - 1
    span_lines = lines[s0:s1 + 1]
    copied, extracted = [], []
    if span["level"]:
        copied.append(lines[s0])
    heading_idx = {d["line"] - 1 for d in span["descendant_headings"]}
    for i in sorted(heading_idx):
        copied.append(lines[i])
    for a in span["explicit_anchors"]:
        li = a["line"] - 1
        ln = lines[li]
        if li == s0 and span["level"] or li in heading_idx:
            continue  # the anchor rides on a copied heading line
        if ln.strip() in ("{#%s}" % a["id"],) or re.fullmatch(
                r"""\s*<a\s+[^>]*\bid\s*=\s*["']%s["'][^>]*>\s*(</a>)?\s*""" % re.escape(a["id"]), ln):
            if ln not in copied:
                copied.append(ln)
            continue
        tok = "{#%s}" % a["id"] if ("{#%s}" % a["id"]) in ln else None
        if tok is None:
            m = re.search(r"""<a\s+[^>]*\bid\s*=\s*["']%s["'][^>]*>(?:</a>)?""" % re.escape(a["id"]), ln)
            tok = m.group(0) if m else None
        if tok is None:
            raise Refuse("explicit anchor %s on span line %d could not be extracted" % (
                a["id"], a["line"]))
        copied.append(tok)
        extracted.append({"id": a["id"], "extracted_from": a["line"], "token": tok})
    generated = ["", POINTER_TMPL % (target, seq), ""]
    if not span["level"]:
        generated = [POINTER_TMPL % (target, seq), ""]
    # gate 3: copied lines are verbatim span lines (extracted tokens excepted, recorded)
    span_set = set(span_lines)
    ext_tokens = {x["token"] for x in extracted}
    for c in copied:
        if c not in span_set and c not in ext_tokens:
            raise Refuse("stub copied line is not a verbatim span line: %r" % c[:80])
    return copied + generated, copied, generated, extracted


def _substantive_lines(lines, span):
    """Ledger-dedup coverage universe: every non-blank, non-heading, non-anchor-only line
    of the span outside code fences. Conservative on purpose (R2-C5): every line is an
    assertion until mapped."""
    s0, s1 = span["start_line"] - 1, span["end_line"] - 1
    fence = _manifest._FenceTracker()
    out = []
    for i in range(s0, s1 + 1):
        ln = lines[i]
        if fence.feed(ln):
            continue
        st = ln.strip()
        if not st or _manifest.H_ANY_RE.match(ln) or st.startswith("{#") or st.startswith("<a "):
            continue
        out.append((i + 1, ln))
    return out


def check_mapping(repo, head, lines, span, mapping):
    """Gate 4. mapping: [{"line": n, "artifact": "raw/<event>.md", "artifact_line": k}].
    Every substantive span line must map to an EXACT line of an immutable raw/ artifact at
    the branch tip. Returns (ok, unmapped, artifacts, used) -- `used` is the accepted
    per-line mapping, recorded in the retire record so verify_prepared and the clean
    oracle can RE-VERIFY it against the raw blobs (cross-vendor round-9 catch: a dedup
    retirement whose mapping is never re-checked could cite nonexistent raw lines)."""
    by_line = {}
    for m in mapping or []:
        by_line.setdefault(int(m["line"]), []).append(m)
    unmapped, arts, used = [], set(), []
    cache = {}
    for n, ln in _substantive_lines(lines, span):
        ok = False
        for m in by_line.get(n, []):
            art = str(m.get("artifact", "")).replace("\\", "/")
            if not art.startswith("raw/"):
                continue  # only immutable ledger material is a home (R1-C1)
            if art not in cache:
                b = _blob(repo, head, art)
                cache[art] = b.decode("utf-8", "replace").replace("\r\n", "\n").split("\n") if b else None
            al = cache[art]
            k = int(m.get("artifact_line", 0))
            # EXACT line, whitespace included (cross-vendor round-2 catch; the brief says "an
            # exact line of an immutable raw/ artifact", the same rule compile-v2's
            # corpus_support gate uses). A mismatch does not drop the assertion -- it leaves
            # it unmapped, which downgrades the whole span to cold-relocate (the safe mode
            # that stores the exact bytes).
            if al and 1 <= k <= len(al) and al[k - 1] == ln:
                ok = True
                arts.add(art)
                used.append({"line": n, "artifact": art, "artifact_line": k})
                break
        if not ok:
            unmapped.append({"line": n, "text": ln[:120]})
    return not unmapped, unmapped, sorted(arts), used


# ------------------------------------------------------------------ citations
def _legacy_registry(repo, head):
    """Every receipts/citations/legacy-*.json at the branch tip, merged."""
    rc, out, _ = _git_text(repo, "ls-tree", "-r", "--name-only", head, "--", LEGACY_DIR)
    rows = []
    if rc != 0:
        return rows
    for p in out.split():
        if re.match(r"^%s/legacy-\d{4}-\d{2}-\d{2}(-\d+)?\.json$" % LEGACY_DIR, p):
            b = _blob(repo, head, p)
            try:
                rows.extend(json.loads(b.decode("utf-8-sig")).get("citations", []))
            except Exception:
                raise Refuse("legacy registry %s unreadable" % p)
    return rows


def _registered(rows, view_rel, cite):
    base = os.path.basename(view_rel)
    for r in rows:
        if os.path.basename(r.get("view", "")) != base or r.get("artifact") != cite["artifact"]:
            continue
        if int(r.get("artifact_line", -1)) != cite["artifact_line"]:
            continue
        if set(r.get("discretes", [])) == set(cite["discretes"]) and \
                sorted(map(tuple, r.get("ranges", []))) == sorted(map(tuple, cite["ranges"])):
            return r
    return None


def _universe_at(repo, head):
    """The citation universe read from the BRANCH TIP's git objects (cross-vendor round-2
    catch): every registered event at `head` + every wiki .md at `head` (cold objects
    excluded). Returns {rel: text}. Refuses when the registration chain or a registered
    artifact cannot be read from `head` (design condition 3: an unestablishable universe
    refuses -- and 'establishable' means from committed state, not a dirty working tree)."""
    # registered events = the registration records' `event` fields at head
    rc, out, _ = _git_text(repo, "ls-tree", "-r", "--name-only", head, "--", "receipts/registrations")
    events, chain_ok = set(), True
    for q in out.split():
        if re.match(r"^receipts/registrations/\d+\.json$", q):
            b = _blob(repo, head, q)
            try:
                rec = json.loads(b.decode("utf-8-sig"))
                ev = str(rec.get("event", "")).replace("\\", "/")
                if ev:
                    events.add(ev)
            except Exception:
                chain_ok = False
    if not chain_ok:
        raise Refuse("registration chain not readable at %s -- the citation universe cannot be "
                     "established (design condition 3)" % head[:12])
    rc, out, _ = _git_text(repo, "ls-tree", "-r", "--name-only", head, "--", "wiki")
    wiki = [q for q in out.split() if q.endswith(".md") and not q.startswith(COLD_DIR + "/")]
    uni = {}
    for rel in sorted(set(events) | set(wiki)):
        b = _blob(repo, head, rel)
        if b is None:
            raise Refuse("registered artifact %s is not in %s's tree -- the citation universe is "
                         "not established (design condition 3)" % (rel, head[:12]))
        uni[rel] = _lf(b).decode("utf-8", "replace")
    return uni


def tagged_citations_at(universe, view_rel):
    """Generation-tagged citations `<basename>:<line>@<hash8>` against the view, from the
    branch-tip universe (a {rel: text} map)."""
    base = os.path.basename(view_rel)
    out = []
    for rel, text in universe.items():
        if rel == view_rel or base not in text:
            continue
        for m in TAGGED_CITE_RE.finditer(text):
            if m.group(1) == base:
                out.append({"artifact": rel, "artifact_line": text.count("\n", 0, m.start()) + 1,
                            "line": int(m.group(2)), "gen": m.group(3)})
    return out


def inbound_citations_at(universe, view_rel):
    """Legacy line citations against the view, from the branch-tip universe, using
    check-split's exact grammar (the same decomposition the manifest uses, but over
    committed bytes)."""
    base = os.path.basename(view_rel)
    out = []
    for rel, text in universe.items():
        if rel == view_rel or base not in text:
            continue
        for m in re.finditer(re.escape(base), text):
            window = text[m.end():m.end() + 200]
            line_no = text.count("\n", 0, m.start()) + 1
            for cm in _split.CITE_FIND_RE.finditer(window):
                d, r = _split._parse_citation(cm.group(1))
                if d or r:
                    out.append({"artifact": rel, "artifact_line": line_no,
                                "discretes": sorted(d), "ranges": sorted([list(x) for x in r])})
    return out


def walk_redirects(entries, gen_hash, line):
    """Resolve (generation, line) forward through the redirect chain. Returns
    {'resolved', 'target', 'line', 'via'} -- target is the view (current generation) or a
    cold object when the line fell inside a retired span."""
    via = []
    cur = gen_hash
    started = False
    # entries of ONE generation (a batch: same pre_hash) are judged together in PRE
    # coordinates, then the shifts of every span above the line are summed
    groups = []
    for en in entries:
        if groups and groups[-1][0]["pre_hash"] == en["pre_hash"]:
            groups[-1].append(en)
        else:
            groups.append([en])
    for grp in groups:
        if not started:
            if grp[0]["pre_hash"].startswith(cur):
                started = True
            else:
                continue
        for en in grp:
            a, b = en["span"]
            if a <= line <= b:
                via.append(en["seq"])
                return {"resolved": True, "target": en["target"], "line": line - a + 1,
                        "kind": "cold" if en["target"].startswith(COLD_DIR) else "ledger", "via": via}
        base = line
        line += sum(en["shift"] for en in grp if en["span"][1] < base)
        if base > grp[0].get("bafter", base):
            line += grp[0].get("bshift", 0)
        via.append(grp[0]["seq"])
        cur = grp[0]["post_hash"]
    if not started and entries:
        return {"resolved": False, "reason": "generation %s.. is not in the redirect chain" % cur[:8],
                "via": via}
    return {"resolved": True, "target": "view", "line": line, "kind": "view", "via": via}


def citation_gate(root, repo, head, view_rel, pre_text, pre_hash, spans, existing):
    """Gate 2. Returns the list of inbound citations into the retiring spans with how each
    resolves; raises Refuse when one cannot."""
    universe = _universe_at(repo, head)  # from the branch tip's objects, never the worktree
    legacy = inbound_citations_at(universe, view_rel)
    tagged = tagged_citations_at(universe, view_rel)
    registry = _legacy_registry(repo, head)
    hits = []
    for c in legacy:
        for s in spans:
            if _manifest._cite_hits_span(c, s):
                reg = _registered(registry, view_rel, c)
                if reg is None:
                    raise Refuse("legacy citation into %r from %s:%d (%s) is NOT in the frozen "
                                 "legacy registry (receipts/citations/legacy-*.json) -- run "
                                 "--register-legacy first, or tag the citation with its "
                                 "generation" % (s["title"], c["artifact"], c["artifact_line"],
                                                 c["discretes"] or c["ranges"]))
                gen = reg.get("view_sha256", pre_hash)
                if gen != pre_hash and not any(e["pre_hash"] == gen for e in existing):
                    raise Refuse("legacy citation from %s:%d was registered against view "
                                 "generation %s.. which the redirect chain does not reach" % (
                                     c["artifact"], c["artifact_line"], gen[:8]))
                hits.append({"artifact": c["artifact"], "artifact_line": c["artifact_line"],
                             "span": s["title"], "generation": "legacy:" + gen[:8],
                             "heals": "redirect entry seq maps lines %d-%d" % (s["start_line"], s["end_line"])})
    for t in tagged:
        r = walk_redirects(existing, t["gen"], t["line"]) if existing else (
            {"resolved": t["gen"] == pre_hash[:8], "target": "view", "line": t["line"]})
        if not r.get("resolved") and t["gen"] != pre_hash[:8]:
            raise Refuse("tagged citation %s:%d@%s from %s:%d names a generation the redirect "
                         "chain does not reach" % (os.path.basename(view_rel), t["line"], t["gen"],
                                                   t["artifact"], t["artifact_line"]))
        ln = r.get("line", t["line"]) if r.get("target") == "view" else None
        for s in spans:
            if ln is not None and s["start_line"] <= ln <= s["end_line"]:
                hits.append({"artifact": t["artifact"], "artifact_line": t["artifact_line"],
                             "span": s["title"], "generation": "tagged:" + t["gen"],
                             "heals": "redirect entry"})
    return hits


# ------------------------------------------------------------------ propose
def _next_seq(repo, head):
    rc, out, _ = _git_text(repo, "ls-tree", "--name-only", head, "--", JOURNAL_DIR + "/")
    seqs = []
    for p in out.split():
        m = re.match(r"^%s/(\d+)\.json$" % JOURNAL_DIR, p)
        if m:
            seqs.append(int(m.group(1)))
    n = max(seqs) if seqs else 0
    if seqs and sorted(seqs) != list(range(1, n + 1)):
        raise Refuse("journal at %s is not contiguous (seqs %s) -- never append onto a broken "
                     "chain" % (head[:12], sorted(seqs)[:10]))
    prev_hash = None
    if n:
        prev_hash = _core._record_hash(_blob(repo, head, "%s/%d.json" % (JOURNAL_DIR, n)))
    return n + 1, prev_hash


def _worktree_matches(repo, head, rel):
    b = _blob(repo, head, rel)
    try:
        raw = open(os.path.join(repo, rel.replace("/", os.sep)), "rb").read()
    except OSError:
        return b is None
    return b is not None and _lf(raw) == _lf(b)


def propose(root, view_rel, titles=(), preamble=False, mode="cold", mapping=None,
            branch="main", now=None):
    """Prepare a retirement. Returns {'seq','commit','digest','record','proposal','spans'}."""
    if RELEASE_SCOPE == "disabled":
        raise Refuse("production retirement is DISABLED in this build (ADR #11 Release 2 G2 "
                     "not yet passed)")
    repo = root
    if not _trust.is_git_repo(repo):
        raise Refuse("%s is not a git repository" % repo)
    if not _trust.mode_chosen(repo):
        raise Refuse("retirement disabled: " + _trust.ABSENT_MODE_NOTE)
    head = _rev(repo, "refs/heads/%s" % branch)
    if head is None:
        raise Refuse("production branch %s does not resolve" % branch)
    view_rel = view_rel.replace("\\", "/")
    if view_rel.startswith(COLD_DIR + "/"):
        raise Refuse("cold objects are immutable; they are never retired")
    pre_b = _blob(repo, head, view_rel)
    if pre_b is None:
        raise Refuse("%s is not in %s's tree" % (view_rel, branch))
    if not _worktree_matches(repo, head, view_rel):
        raise Refuse("working-tree %s differs from %s -- commit or discard the edit first "
                     "(a retirement builds on the committed view, never on a half-staged one)"
                     % (view_rel, branch))
    pre_text = _lf(pre_b).decode("utf-8")
    pre_hash = gen_hash(pre_text)
    pre_file_sha = _sha(_lf(pre_b))
    lines = pre_text.split("\n")
    ds, de = _region_bounds(lines)
    if ds is None or de is None:
        raise Refuse("%s has no engine-managed derivation region -- the redirect block lives "
                     "there; this is not a compiled view" % view_rel)
    existing = all_redirects(repo, head, view_rel, text=pre_text)
    _entries, pointer, _idx = parse_retirements_block(pre_text)
    spans = select_spans(pre_text, list(titles), preamble)
    if not spans:
        raise Refuse("nothing selected: name --span titles and/or --preamble")
    # v3.0.51 (v3.0-146, fleet inbox #6): the derivation region sits WHEREVER the engine
    # put it -- assemble.py's canonical layout is frontmatter -> region -> body; legacy
    # fixtures put the region at the file end. The BODY is everything outside the region
    # interval [ds, de], and spans are clipped against that interval, never against an
    # assumed region-at-end (the v3.0.50 model refused every engine-compiled view). A
    # span wholly AFTER the region (canonical layout) needs no clip. A span starting
    # BEFORE the region whose manifest extent runs to/past it (region-at-end: the
    # manifest extends the last span over the file's trailing blank line) is clipped to
    # the line before the region, trailing blanks dropped to ONE --
    # retire-manifest._clip_span_idx applies the SAME rule (the v3.0-132 two-tool
    # discipline, pinned on _SHARED_CLIP_FIXTURE in both batteries). A span whose extent
    # crosses NON-BLANK text beyond the region REFUSES: a retirement is one contiguous
    # slice, and a straddling span is not a compiled-view shape (refuse rather than
    # silently retire half a section).
    for sp in spans:
        s0 = sp["start_line"] - 1
        if s0 > de:
            continue  # canonical layout: the span sits wholly after the region
        if s0 >= ds:
            raise Refuse("span %r starts inside the derivation region (internal)" % sp["title"])
        if sp["end_line"] > ds:  # end_line is 1-based; ds is the 0-based region start
            tail = [ln for ln in lines[de + 1:sp["end_line"]] if ln.strip()]
            if tail:
                raise Refuse("span %r crosses the derivation region onto %d non-blank line(s) "
                             "beyond it -- a retirement is one contiguous slice and the region "
                             "is never retired; not a compiled view layout; nothing retired"
                             % (sp["title"], len(tail)))
            sp["end_line"] = ds  # the line before the region (1-based == ds)
            while sp["end_line"] > sp["start_line"] and not lines[sp["end_line"] - 1].strip():
                sp["end_line"] -= 1
            sp["end_line"] += 1 if sp["end_line"] < ds else 0  # keep ONE trailing blank line
            st = "\n".join(lines[sp["start_line"] - 1:sp["end_line"]]) + "\n"
            sp["sha256"] = _sha(st.encode("utf-8"))
            sp["bytes"] = len(st.encode("utf-8"))
    seq, prev_hash = _next_seq(repo, head)
    tag = "retire/%d" % seq
    # refuse a seq already prepared or consumed on the branch
    if _rev(repo, "refs/retire/%d" % seq):
        raise Refuse("refs/retire/%d already exists -- promote it, or `--recover` to discard a "
                     "stale one, before preparing another" % seq)
    for _p, _sha_c, r in _trust._retire_records_history(repo, head):
        if r.get("seq") == seq:
            raise Refuse("seq %d already consumed on %s" % (seq, branch))
    hits = citation_gate(root, repo, head, view_rel, pre_text, pre_hash, spans, existing)
    vslug = _view_slug(view_rel)
    # ---- per span, bottom-up so earlier line numbers never shift under us
    new_lines = list(lines)
    results = []
    blobs = {}  # path -> (bytes, blob sha) to put in C's tree
    for span in sorted(spans, key=lambda s: -s["start_line"]):
        s0, s1 = span["start_line"] - 1, span["end_line"] - 1
        span_text = "\n".join(lines[s0:s1 + 1]) + "\n"
        span_bytes = span_text.encode("utf-8")
        span_sha = _sha(span_bytes)
        if span_sha != span["sha256"]:
            raise Refuse("span hash disagreement between parser and bytes (internal)")
        eff_mode, unmapped, arts, used_map = mode, [], [], []
        if mode == "dedup":
            ok, unmapped, arts, used_map = check_mapping(repo, head, lines, span, mapping)
            if not ok:
                eff_mode = "cold"
                used_map = []
        cold = None
        if eff_mode == "cold":
            # the slug is capped (the full sha256 IS the identity; a long heading under a deep
            # Windows checkout crossed MAX_PATH in the G2 run -- the v3.0-133 defect class)
            cpath = "%s/%s/%s--%s.md" % (COLD_DIR, vslug, _manifest.slug(_bare_title(span["title"]))[:SLUG_MAX], span_sha)
            existing_cold = _blob(repo, head, cpath)
            if existing_cold is not None:
                if _lf(existing_cold) != span_bytes:  # identity is never trusted in place of bytes
                    raise Refuse("cold object %s exists with DIFFERENT bytes than the span "
                                 "(content-address collision or tampering) -- refuse" % cpath)
                reused = True
                blob_sha = _hash_object(repo, span_bytes)
            else:
                reused = False
                blob_sha = _hash_object(repo, span_bytes)
                blobs[cpath] = (span_bytes, blob_sha)
            cold = {"path": cpath, "sha256": span_sha, "bytes": len(span_bytes), "reused": reused,
                    "blob": blob_sha}
            target = cpath
        else:
            target = ", ".join(arts)
        stub, copied, generated, extracted = build_stub(lines, span, target, seq)
        new_lines[s0:s1 + 1] = stub
        results.append({"title": span["title"], "level": span["level"], "span_text": span_text,
                        "start_line": span["start_line"], "end_line": span["end_line"],
                        "bytes": len(span_bytes), "sha256": span_sha, "mode": eff_mode,
                        "downgraded_from": "dedup" if (mode == "dedup" and eff_mode == "cold") else None,
                        "unmapped": unmapped, "mapping_artifacts": arts, "mapping": used_map,
                        "cold_object": cold,
                        "stub_lines": stub, "copied_lines": copied, "generated_lines": generated,
                        "extracted_anchors": extracted,
                        "shift": len(stub) - (s1 - s0 + 1), "target": target})
    results.sort(key=lambda r: r["start_line"])
    # ---- redirect entries (one per span; keyed by seq + pre-hash; post-hash filled below)
    # the redirect entries name generations by gen_hash (view minus its block), so the post
    # generation is computed once from the assembled body; the block is then written.
    body_lines = new_lines
    ds2, de2 = _region_bounds(body_lines)
    entries_old, pointer_old, idx_old = parse_retirements_block("\n".join(body_lines))
    new_entries = []
    for i, r in enumerate(results):
        new_entries.append({"seq": seq, "i": i, "pre_hash": pre_hash, "post_hash": None,
                            "span": [r["start_line"], r["end_line"]],
                            "stub": [r["start_line"], r["start_line"] + len(r["stub_lines"]) - 1],
                            "shift": r["shift"], "target": r["target"], "mode": r["mode"],
                            # bare title: a `{#id}` inside a block row would read as an
                            # explicit anchor to check-split's grammar (gate 1 parity)
                            "title": _bare_title(r["title"])})
    cfg = _caps_cfg()
    compaction = None
    merged = list(entries_old) + new_entries
    pointer = pointer_old
    rendered = render_block(merged, pointer)
    if len(merged) > cfg["entries"] or len("\n".join(rendered).encode("utf-8")) > cfg["bytes"]:
        # compaction: the whole chain (old index entries + block entries + new) into ONE
        # content-addressed cold index; the block keeps one pointer row
        full = list(existing) + new_entries
        compaction = {"entries": len(full), "previous_index": pointer_old["index"] if pointer_old else None}
        merged, pointer = [], {"index": None, "entries": len(full)}
    # v3.0.51 (v3.0-146): the block's LINE growth this retirement (structural: markers +
    # one row per entry + one pointer row; content never changes the row count), and the
    # PRE-view line below which it applies (the region end marker -- the block lives
    # inside the region). A citation resolving across this generation transition shifts
    # by bshift when it points below the region (canonical layout: the whole body);
    # region-at-end views have no body below the region, so bshift is inert there --
    # exactly v3.0.50's behavior. Recorded per entry; walk_redirects applies it;
    # verify_prepared re-derives it (never trusted).
    _old_blk = (idx_old[1] - idx_old[0] + 1) if idx_old is not None else 0
    _new_blk = 2 + len(merged) + (1 if pointer else 0)
    for en in new_entries:
        en["bshift"] = _new_blk - _old_blk
        en["bafter"] = de + 1  # 1-based line of the region end marker at the PRE view

    def _assemble(entries, pointer_row):
        out = list(body_lines)
        if idx_old is not None:
            out[idx_old[0]:idx_old[1] + 1] = render_block(entries, pointer_row)
        else:
            out[de2:de2] = render_block(entries, pointer_row)
        return "\n".join(out)

    # the post GENERATION hash excludes the block, so it is computable before the block is
    # written (no self-reference -- a hash cannot contain itself): hash the assembled view
    # with the block stripped.
    post_hash = gen_hash(_assemble([], None))
    for en in new_entries:
        en["post_hash"] = post_hash
    if compaction is None:
        post_text = _assemble(merged, pointer)
    else:
        full = list(existing) + new_entries
        ib = (json.dumps({"view": view_rel, "entries": full}, sort_keys=True, indent=1) + "\n").encode("utf-8")
        pointer = {"index": "%s/%s/redirects--%s.json" % (COLD_DIR, vslug, _sha(ib)), "entries": len(full)}
        post_text = _assemble([], pointer)
        compaction["index"] = pointer["index"]
        blobs[pointer["index"]] = (ib, _hash_object(repo, ib))
    if gen_hash(post_text) != post_hash:
        raise Refuse("generation hash disagreement after block write (internal)")
    post_bytes = post_text.encode("utf-8")
    # ---- gate 1: anchor conservation (check-split's primitive, reused)
    if _split.anchor_multiset(pre_text) != _split.anchor_multiset(post_text):
        raise Refuse("anchor conservation failed: the stub does not preserve the view's anchor "
                     "multiset (internal -- the stub template must copy every heading/anchor)")
    # ---- gate 3: whole-view reconstruction: pre == post with each stub replaced by its span
    if reconstruct(post_text, results, repo, head, blobs, pre_text=pre_text) != strip_block(pre_text):
        raise Refuse("whole-view reconstruction failed: post view with stubs replaced by the "
                     "cold objects is not byte-identical to the pre view (internal)")
    # ...and the block itself changed by exactly this retirement's entries
    if compaction is None:
        if parse_retirements_block(post_text)[0] != list(entries_old) + new_entries:
            raise Refuse("redirect block delta is not exactly this retirement's entries (internal)")
    # ---- proposal artifact (gate 5: displayed, never collapsed)
    rec_path = "%s/%d.json" % (JOURNAL_DIR, seq)
    prop_path = "%s/retire-%d/proposal.md" % (RULINGS_DIR, seq)
    prop_text = render_proposal(view_rel, branch, head, seq, pre_hash, post_hash, pre_text,
                                post_text, results, new_entries, compaction, hits, now or _now())
    prop_bytes = prop_text.encode("utf-8")
    digest = _sha(prop_bytes)
    blobs[prop_path] = (prop_bytes, _hash_object(repo, prop_bytes))
    blobs[view_rel] = (post_bytes, _hash_object(repo, post_bytes))
    rec = _core.minimal_record("retire", parent_git_sha=head)
    rec.update({"seq": seq, "prev_record_hash": prev_hash, "tag": tag, "proposal": prop_path,
                "proposal_digest": "sha256:" + digest, "view": view_rel, "branch": branch,
                "parent": head, "pre_view_sha256": pre_file_sha, "post_view_sha256": _sha(post_bytes),
                "pre_generation": pre_hash, "post_generation": post_hash,
                "pre_view_blob": _hash_object(repo, _lf(pre_b)), "post_view_blob": blobs[view_rel][1],
                "spans": [{k: v for k, v in r.items() if k != "span_text"} for r in results],
                "redirects": new_entries, "compaction": compaction,
                "inbound_citations": hits, "prepared_at": now or _now(),
                "cold_objects": [r["cold_object"] for r in results if r["cold_object"]]})
    _core.validate_record(rec)
    rec_bytes = json.dumps(rec, indent=1, sort_keys=True).encode("utf-8")
    blobs[rec_path] = (rec_bytes, _hash_object(repo, rec_bytes))
    commit = _build_commit(repo, head, blobs, "retire %d: %s -- %s" % (
        seq, view_rel, "; ".join(r["title"] for r in results)))
    rc, out, err = _git_text(repo, "update-ref", "refs/retire/%d" % seq, commit, "0" * 40)
    if rc != 0:
        raise Refuse("refs/retire/%d could not be created (exists?): %s" % (seq, err.strip()))
    return {"seq": seq, "commit": commit, "digest": digest, "record": rec_path,
            "proposal": prop_path, "spans": results, "view": view_rel, "tag": tag,
            "compaction": compaction}


def _build_commit(repo, head, blobs, message):
    """C from head's tree + blobs, via a TEMPORARY index: the real index and working tree
    are never touched."""
    td = tempfile.mkdtemp(prefix="retire-idx-")
    try:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(td, "index"))
        rc, _o, err = _git_text(repo, "read-tree", head, env=env)
        if rc != 0:
            raise Refuse("read-tree failed: %s" % err.strip())
        for path, (data, sha) in sorted(blobs.items()):
            rc, _o, err = _git_text(repo, "update-index", "--add", "--cacheinfo",
                                    "100644,%s,%s" % (sha, path), env=env)
            if rc != 0:
                raise Refuse("update-index failed for %s: %s" % (path, err.strip()))
        rc, tree, err = _git_text(repo, "write-tree", env=env)
        if rc != 0:
            raise Refuse("write-tree failed: %s" % err.strip())
        rc, commit, err = _git_text(repo, "commit-tree", tree.strip(), "-p", head, "-m", message,
                                    env=dict(env, GIT_AUTHOR_NAME=os.environ.get("GIT_AUTHOR_NAME", "retire.py"),
                                             GIT_AUTHOR_EMAIL=os.environ.get("GIT_AUTHOR_EMAIL", "retire@engine"),
                                             GIT_COMMITTER_NAME=os.environ.get("GIT_COMMITTER_NAME", "retire.py"),
                                             GIT_COMMITTER_EMAIL=os.environ.get("GIT_COMMITTER_EMAIL", "retire@engine")))
        if rc != 0:
            raise Refuse("commit-tree failed: %s" % err.strip())
        return commit.strip()
    finally:
        shutil.rmtree(td, ignore_errors=True)


def reconstruct(post_text, spans, repo, head, blobs=None, pre_text=None):
    """Gate 3 / condition 5: the pre view's GENERATION text (block-stripped) from the post
    view + the cold objects (or, for ledger-dedup spans, the span bytes the record carries
    are NOT stored -- the pre view is recovered from view@parent; here we substitute the
    spans' own bytes). Returns the reconstructed generation text; compare against
    strip_block(pre). v3.0.51 (v3.0-146): recovery works in GENERATION coordinates because
    in the canonical frontmatter -> region -> body layout the engine-owned block sits
    ABOVE the body, so full-file stub positions shift as the block grows. The recorded
    start_line is a full-PRE-view coordinate; `pre_text` (when given) supplies the PRE
    block's location so spans below it are offset by its length -- a pre view without a
    block (every first retirement, and every v3.0.50 region-at-end view) needs no offset.
    """
    lines = strip_block(post_text).split("\n")
    _pe, _pp, pidx = parse_retirements_block(pre_text) if pre_text is not None else (None, None, None)
    pboff = (pidx[1] - pidx[0] + 1) if pidx is not None else 0
    # TOP-DOWN: once the uppermost stub is replaced by its span, everything below it is back
    # at its pre-retirement line numbers, so each span's recorded start_line is exact.
    for r in sorted(spans, key=lambda s: s["start_line"]):
        a = r["start_line"] - 1
        if pboff and a > pidx[1]:
            a -= pboff
        b = a + len(r["stub_lines"]) - 1
        if r["cold_object"]:
            data = None
            if blobs and r["cold_object"]["path"] in blobs:
                data = blobs[r["cold_object"]["path"]][0]
            if data is None:
                data = _blob(repo, head, r["cold_object"]["path"])
            if data is None:
                raise Refuse("cold object %s missing" % r["cold_object"]["path"])
            span_text = _lf(data).decode("utf-8")
        else:
            span_text = r.get("span_text")  # dedup: caller supplies (recover reads view@parent)
            if span_text is None:
                raise Refuse("ledger-dedup span has no stored bytes; recover from view@parent")
        lines[a:b + 1] = span_text[:-1].split("\n") if span_text.endswith("\n") else span_text.split("\n")
    return "\n".join(lines)


_H_RE = re.compile(r"^\s{0,3}(#{2,6})\s")  # H2-H6: the parser's structural headings (H1 in a preamble is body)
_ANCHOR_ATTR = re.compile(r"\{#([A-Za-z0-9_-]+)\}")
_ANCHOR_HTML = re.compile(r"""<a\s+[^>]*\bid\s*=\s*["']([A-Za-z0-9_-]+)["']""", re.I)


def _mapping_defect(repo, commit, span_text, span_rec):
    """None when span_rec's recorded `mapping` covers every substantive line of the span and
    each row points at an EXACT line of a raw/ blob in `commit`'s tree; else a reason.
    Shared by verify_prepared; the clean oracle mirrors it (round-9 fold)."""
    span_lines = span_text.split("\n")
    mapping = span_rec.get("mapping") or []
    by_line = {}
    for m in mapping:
        art = str(m.get("artifact", "")).replace("\\", "/")
        if not art.startswith("raw/"):
            return "maps line %s to %r, which is not immutable raw/ material" % (m.get("line"), art)
        b = _blob(repo, commit, art)
        if b is None:
            return "maps line %s to %s, which is not in C's tree" % (m.get("line"), art)
        al = _lf(b).decode("utf-8", "replace").split("\n")
        k = int(m.get("artifact_line", 0))
        n = int(m.get("line", 0))
        off = n - span_rec["start_line"]
        if not (0 <= off < len(span_lines)):
            return "maps line %s which is outside the span" % n
        if not (1 <= k <= len(al)) or al[k - 1] != span_lines[off]:
            return "maps span line %s to %s:%s but the bytes differ" % (n, art, k)
        by_line[n] = m
    # coverage: every substantive line (same rule as check_mapping) must be mapped
    fence = _manifest._FenceTracker()
    for i, ln in enumerate(span_lines):
        if fence.feed(ln):
            continue
        st = ln.strip()
        if not st or _manifest.H_ANY_RE.match(ln) or st.startswith("{#") or st.startswith("<a "):
            continue
        n = span_rec["start_line"] + i
        if n not in by_line:
            return "leaves substantive line %s (%r) unmapped" % (n, ln[:60])
    return None


def stub_anchors_and_headings(span_text):
    """The heading LINES and explicit anchor IDS a stub of this span must preserve (outside
    code fences). Stdlib-shaped so the clean oracle can mirror it. Returns (headings, ids)."""
    heads, ids, in_fence, fch, flen = [], [], False, "", 0
    for ln in span_text.split("\n"):
        m = re.match(r"^\s*(`{3,}|~{3,})(.*)$", ln)
        if m:
            run = m.group(1)
            if not in_fence:
                in_fence, fch, flen = True, run[0], len(run)
            elif run[0] == fch and len(run) >= flen and m.group(2).strip() == "":
                in_fence = False
            continue
        if in_fence:
            continue
        if _H_RE.match(ln):
            heads.append(ln)
        ids.extend(_ANCHOR_ATTR.findall(ln))
        ids.extend(_ANCHOR_HTML.findall(ln))
    return heads, ids


def _stub_defect(span_text, stub_lines, seq):
    """None when the stub preserves every heading LINE and explicit anchor ID of the span and
    carries the pointer line; else a short reason. Shared by verify_prepared and mirrored by
    the clean oracle (round-6 catch)."""
    heads, ids = stub_anchors_and_headings(span_text)
    stub = "\n".join(stub_lines)
    stub_set = set(stub_lines)
    for h in heads:
        if h not in stub_set:
            return "drops heading line %r" % h[:60]
    _sh, sids = stub_anchors_and_headings(stub)
    for i in ids:
        if i not in sids:
            return "drops explicit anchor %r" % i
    if not re.search(r"(?m)^> Retired to .+ -- journal seq %d\.\s*$" % seq, stub):
        return "carries no `> Retired to ... -- journal seq %d.` pointer line" % seq
    return None


def render_proposal(view_rel, branch, head, seq, pre_hash, post_hash, pre_text, post_text,
                    results, entries, compaction, hits, when):
    def fence_for(text):
        longest = max([len(m.group(0)) for m in re.finditer(r"`{3,}", text)] + [3])
        return "`" * (longest + 1)
    out = ["# Retirement proposal -- seq %d" % seq, "",
           "- view: `%s` (branch `%s`, parent commit `%s`)" % (view_rel, branch, head),
           "- prepared: %s" % when,
           "- pre-retirement generation (view minus its redirect block) sha256: `%s`" % pre_hash,
           "- post-retirement generation sha256: `%s`" % post_hash,
           "- bytes: %d -> %d" % (len(pre_text.encode("utf-8")), len(post_text.encode("utf-8"))),
           "- spans retired: %d" % len(results), ""]
    for i, r in enumerate(results):
        pre_lines = pre_text.split("\n")
        span_text = "\n".join(pre_lines[r["start_line"] - 1:r["end_line"]]) + "\n"
        f = fence_for(span_text)
        out += ["## Span %d -- %s (lines %d-%d, %d bytes, sha256 %s)" % (
            i, r["title"], r["start_line"], r["end_line"], r["bytes"], r["sha256"]), "",
            "- mode: %s%s" % (r["mode"], (" (downgraded from dedup: %d unmapped line(s))"
                                          % len(r["unmapped"])) if r["downgraded_from"] else ""),
            "- destination: `%s`%s" % (r["target"], " (existing cold object REUSED, byte-compared)"
                                        if r["cold_object"] and r["cold_object"]["reused"] else ""),
            "", "### Retiring preimage (complete, verbatim)", "", f, span_text.rstrip("\n"), f, "",
            "### Stub (copied lines, then generated lines)", "", f,
            "\n".join(r["stub_lines"]).rstrip("\n"), f, ""]
        if r["extracted_anchors"]:
            out += ["- extracted inline anchors: " + ", ".join(
                "%s (from line %d)" % (x["id"], x["extracted_from"]) for x in r["extracted_anchors"]), ""]
    out += ["## Redirect entries", ""]
    for en in entries:
        out.append("- `%s`" % json.dumps(en, sort_keys=True))
    if compaction:
        out += ["", "Redirect block compacted: %d entries moved to cold index `%s`" % (
            compaction["entries"], compaction.get("index"))]
    out += ["", "## Inbound citations into the retired spans (%d)" % len(hits), ""]
    for h in hits:
        out.append("- %s:%d -> %r [%s] -- %s" % (h["artifact"], h["artifact_line"], h["span"],
                                                  h["generation"], h["heals"]))
    if not hits:
        out.append("- none")
    diff = difflib.unified_diff(pre_text.split("\n"), post_text.split("\n"),
                                "a/" + view_rel, "b/" + view_rel, lineterm="")
    out += ["", "## Whole-view diff", "", "```diff"] + list(diff) + ["```", ""]
    return "\n".join(out)


# ------------------------------------------------------------------ recover / list / show
def _prepared(repo):
    rc, out, _ = _git_text(repo, "for-each-ref", "--format=%(refname)", "refs/retire/")
    refs = []
    for r in out.split():
        m = re.match(r"^refs/retire/(\d+)$", r)
        if m:
            refs.append((int(m.group(1)), r))
    return sorted(refs)


def verify_prepared(repo, commit):
    """Re-derive a prepared C from objects alone (the same checks the clean oracle runs).
    Returns (ok, reason, record)."""
    parents = _trust._parents(repo, commit) or []
    if len(parents) != 1:
        return False, "C has %d parents" % len(parents), None
    parent = parents[0]
    rc, ns, _ = _git_text(repo, "diff-tree", "--no-commit-id", "--name-status", "-r", parent, commit)
    delta = [l.split("\t", 1) for l in ns.splitlines() if "\t" in l]
    recs = [q for st, q in delta if q.startswith(JOURNAL_DIR + "/")]
    if len(recs) != 1 or [st for st, q in delta if q == recs[0]] != ["A"]:
        return False, "journal delta is not exactly one ADDED record", None
    b = _blob(repo, commit, recs[0])
    try:
        rec = json.loads(b.decode("utf-8-sig"))
    except Exception:
        return False, "record unreadable", None
    if rec.get("run_type") != "retire" or rec.get("parent") != parent:
        return False, "record is not a retire record naming its parent", rec
    prop = _blob(repo, commit, rec.get("proposal", ""))
    if prop is None or "sha256:" + _sha(prop) != rec.get("proposal_digest"):
        return False, "proposal digest does not match the proposal blob", rec
    view = rec.get("view")
    pre = _blob(repo, parent, view)
    post = _blob(repo, commit, view)
    if pre is None or post is None:
        return False, "view absent at parent or C", rec
    if _sha(_lf(pre)) != rec.get("pre_view_sha256") or _sha(_lf(post)) != rec.get("post_view_sha256"):
        return False, "view hashes do not match the record", rec
    pre_text, post_text = _lf(pre).decode("utf-8"), _lf(post).decode("utf-8")
    pre_lines = pre_text.split("\n")
    spans = []
    for s in rec.get("spans", []):
        s = dict(s)
        s["span_text"] = "\n".join(pre_lines[s["start_line"] - 1:s["end_line"]]) + "\n"
        if _sha(s["span_text"].encode("utf-8")) != s["sha256"]:
            return False, "span %r bytes at parent do not hash to the record" % s["title"], rec
        if s["cold_object"]:
            cb = _blob(repo, commit, s["cold_object"]["path"])
            if cb is None or _lf(cb) != s["span_text"].encode("utf-8"):
                return False, "cold object %s is not byte-identical to the span" % s["cold_object"]["path"], rec
        if s.get("mode") == "dedup" and not s.get("cold_object"):
            # round-9 fold: the recorded mapping is RE-VERIFIED against the raw blobs in C's
            # tree -- every mapped line must be an exact line of an immutable raw/ artifact,
            # and every substantive span line must be covered. A dedup record citing
            # nonexistent or mismatched raw lines is rejected, never trusted.
            bad = _mapping_defect(repo, commit, s["span_text"], s)
            if bad:
                return False, "dedup mapping for span %r %s" % (s["title"], bad), rec
        # the STUB the record carries must PRESERVE the span's headings and explicit anchors
        # and carry the pointer line (cross-vendor round-6 catch: recovery replaces the stub
        # with the preimage, so a destructive stub -- one that drops a heading/anchor -- would
        # otherwise reconstruct fine and pass). Validated from the span bytes, not trusted.
        bad = _stub_defect(s["span_text"], s.get("stub_lines") or [], rec.get("seq"))
        if bad:
            return False, "stub for span %r %s" % (s["title"], bad), rec
        spans.append(s)
    try:
        if reconstruct(post_text, spans, repo, commit, pre_text=pre_text) != strip_block(pre_text):
            return False, "post view with stubs replaced is not the pre view", rec
    except Refuse as e:
        return False, str(e), rec
    # v3.0.51: the entries' block-shift fields are REQUIRED on this retirement's own
    # entries and RE-DERIVED from the two views, never trusted (cross-vendor round-1
    # catches: a forged bshift mis-resolves citations while conserving content, and an
    # entry that simply OMITS the fields would otherwise default to no block shift --
    # this verb always writes them, so absence is forgery, not legacy; chain-historical
    # v3.0.50 entries are not re-verified here and keep their inert defaults).
    _dlen = len(post_text.split("\n")) - len(pre_text.split("\n")) - sum(
        int(s2.get("shift", 0)) for s2 in rec.get("spans", []))
    _dsp, _dep = _region_bounds(pre_text.split("\n"))
    for en in rec.get("redirects") or []:
        if "bshift" not in en or "bafter" not in en:
            return False, ("redirect entry omits its block-shift fields (bshift/bafter) -- this "
                           "verb always writes them; an entry without them would silently "
                           "mis-resolve citations below a top-of-file block"), rec
        if en["bshift"] != _dlen or (_dep is not None and en.get("bafter") != _dep + 1):
            return False, "redirect entry block-shift fields do not re-derive from the views", rec
    try:
        pre_chain = all_redirects(repo, parent, view, text=pre_text)
        post_chain = all_redirects(repo, commit, view, text=post_text)
    except Refuse as e:
        return False, "redirect chain unreadable: %s" % e, rec
    if post_chain != pre_chain + list(rec.get("redirects", [])):
        return False, "redirect chain at C is not the parent's chain + this retirement's entries", rec
    # one redirect entry per span (cross-vendor round-5 catch: a nonempty retirement with
    # redirects:[] and an unchanged block would otherwise reconstruct as consistent)
    if len(rec.get("redirects") or []) != len(spans):
        return False, ("this retirement moves %d span(s) but records %d redirect entr(y/ies) -- "
                       "every retired span leaves a redirect" % (len(spans), len(rec.get("redirects") or []))), rec
    if rec.get("pre_generation") != gen_hash(pre_text) or rec.get("post_generation") != gen_hash(post_text):
        return False, "generation hashes do not match the record", rec
    # the proposal the operator promotes must DISPLAY exactly what C moves (cross-vendor
    # round-5 catch: the digest binds the proposal's identity, not its truthfulness -- so the
    # displayed preimage must literally contain each span's verbatim bytes and the whole-view
    # diff, else a deceptive proposal could be promoted by digest).
    prop_text = prop.decode("utf-8", "replace")
    for s2 in spans:
        if s2["span_text"].rstrip("\n") not in prop_text:
            return False, ("the proposal does not display span %r's verbatim preimage -- a "
                           "deceptive proposal" % s2.get("title")), rec
    if "## Whole-view diff" not in prop_text:
        return False, "the proposal carries no whole-view diff (condition 5: informed display)", rec
    # every other path in the delta must be a cold object / index / proposal / the view
    allowed = {view, rec.get("proposal"), recs[0]} | {s["cold_object"]["path"] for s in spans if s["cold_object"]}
    if rec.get("compaction") and rec["compaction"].get("index"):
        allowed.add(rec["compaction"]["index"])
    extra = [q for st, q in delta if q not in allowed]
    if extra:
        return False, "C touches paths outside the retirement: %s" % ", ".join(extra[:5]), rec
    return True, "consistent: record %s, proposal digest %s.., %d span(s)" % (
        recs[0], _sha(prop)[:12], len(spans)), rec


def recover(repo, branch="main"):
    """Deterministic: each refs/retire/<seq> is COMPLETE (kept) or DISCARDED (deleted)."""
    head = _rev(repo, "refs/heads/%s" % branch)
    rc, fp, _ = _git_text(repo, "rev-list", "--first-parent", head or "HEAD")
    on_branch = set(fp.split())
    out = []
    for seq, ref in _prepared(repo):
        c = _rev(repo, ref)
        if c in on_branch:
            _git_text(repo, "update-ref", "-d", ref, c)
            out.append({"seq": seq, "commit": c, "action": "pruned",
                        "reason": "already published on %s (ref no longer needed)" % branch})
            continue
        ok, reason, rec = verify_prepared(repo, c)
        parent = (_trust._parents(repo, c) or [None])[0]
        if ok and parent == head:
            out.append({"seq": seq, "commit": c, "action": "kept", "reason": reason + " -- awaiting promotion"})
        elif ok:
            _git_text(repo, "update-ref", "-d", ref, c)
            out.append({"seq": seq, "commit": c, "action": "discarded",
                        "reason": "STALE: prepared on %s but %s is now %s -- re-prepare" % (
                            (parent or "?")[:12], branch, (head or "?")[:12])})
        else:
            _git_text(repo, "update-ref", "-d", ref, c)
            out.append({"seq": seq, "commit": c, "action": "discarded", "reason": "INCONSISTENT: " + reason})
    return out


def list_prepared(repo, branch="main"):
    head = _rev(repo, "refs/heads/%s" % branch)
    rows = []
    for seq, ref in _prepared(repo):
        c = _rev(repo, ref)
        ok, reason, rec = verify_prepared(repo, c)
        parent = (_trust._parents(repo, c) or [None])[0]
        rows.append({"seq": seq, "commit": c, "consistent": ok, "reason": reason,
                     "stale": parent != head, "digest": (rec or {}).get("proposal_digest"),
                     "view": (rec or {}).get("view"),
                     "tag": (rec or {}).get("tag"),
                     "publishable": _trust.check_publishable(repo, "retire/%d" % seq, branch)["ok"]})
    return rows


def find_by_digest(repo, digest):
    d = digest.strip().lower()
    d = d[7:] if d.startswith("sha256:") else d
    if len(d) < 12 or not re.fullmatch(r"[0-9a-f]+", d):
        raise Refuse("give at least 12 hex characters of the proposal digest")
    hits = []
    for seq, ref in _prepared(repo):
        c = _rev(repo, ref)
        ok, reason, rec = verify_prepared(repo, c)
        if rec and str(rec.get("proposal_digest", "")).replace("sha256:", "").startswith(d):
            hits.append((seq, c, ok, reason, rec))
    if not hits:
        raise Refuse("no prepared retirement carries proposal digest %s.." % d[:12])
    if len(hits) > 1:
        raise Refuse("digest prefix %s is ambiguous (%d prepared retirements) -- give more "
                     "characters" % (d[:12], len(hits)))
    return hits[0]


def register_legacy(root, branch="main", now=None):
    repo = root
    head = _rev(repo, "refs/heads/%s" % branch)
    m, status = _manifest.build_manifest(root, views=None, all_views=False)
    if m is None:
        raise Refuse(status)
    seed = m["legacy_citation_registry_seed"]
    rows = []
    for c in seed:
        vb = _blob(repo, head, c["view"])
        rows.append(dict(c, view_sha256=gen_hash(_lf(vb).decode("utf-8", "replace")) if vb else None))
    when = (now or _now())[:10]
    os.makedirs(os.path.join(root, LEGACY_DIR.replace("/", os.sep)), exist_ok=True)
    n = 0
    while True:
        name = "legacy-%s%s.json" % (when, "" if n == 0 else "-%d" % n)
        p = os.path.join(root, LEGACY_DIR.replace("/", os.sep), name)
        if not os.path.exists(p):
            break
        n += 1
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"built_at": now or _now(), "source": "retire-manifest.py seed at %s" % (head or "?")[:12],
                   "frozen": True, "citations": rows}, fh, indent=1, sort_keys=True)
    return os.path.relpath(p, root).replace("\\", "/"), len(rows)


def resolve(repo, cite, branch="main", from_artifact=None):
    """`view.md:80` (legacy: needs the registry) or `view.md:80@hash8`."""
    head = _rev(repo, "refs/heads/%s" % branch)
    m = re.match(r"^([A-Za-z0-9._/-]+\.md):(\d+)(?:@([0-9a-f]{8}))?$", cite.strip())
    if not m:
        raise Refuse("citation must look like view.md:80 or view.md:80@<hash8>")
    base, line, gen = os.path.basename(m.group(1)), int(m.group(2)), m.group(3)
    rc, out, _ = _git_text(repo, "ls-tree", "-r", "--name-only", head, "--", "wiki")
    cands = [p for p in out.split() if os.path.basename(p) == base and not p.startswith(COLD_DIR + "/")]
    if len(cands) != 1:
        raise Refuse("view basename %s resolves to %d wiki files" % (base, len(cands)))
    view_rel = cands[0]
    entries = all_redirects(repo, head, view_rel)
    cur_hash = gen_hash(_lf(_blob(repo, head, view_rel)).decode("utf-8"))
    if gen is None:
        if from_artifact is None:
            raise Refuse("a legacy (untagged) citation resolves through the frozen registry: "
                         "give --from <citing artifact>")
        reg = [r for r in _legacy_registry(repo, head)
               if os.path.basename(r.get("view", "")) == base and r.get("artifact") == from_artifact
               and (line in r.get("discretes", []) or any(a <= line <= b for a, b in r.get("ranges", [])))]
        if not reg:
            raise Refuse("legacy citation %s:%d from %s is not in the registry -- unresolvable "
                         "(it was written after Release 2 without a generation tag, or never "
                         "registered)" % (base, line, from_artifact))
        gen = (reg[0].get("view_sha256") or cur_hash)[:8]
    if gen == cur_hash[:8]:
        return {"resolved": True, "target": "view", "line": line, "kind": "view", "via": [],
                "view": view_rel}
    r = walk_redirects(entries, gen, line)
    r["view"] = view_rel
    return r


# ------------------------------------------------------------------ self-test
def self_test():
    failed = total = 0

    def case(name, cond, detail=""):
        nonlocal failed, total
        total += 1
        if not cond:
            failed += 1
        print("  %s %s%s" % ("ok " if cond else "XX ", name, ("  [%s]" % str(detail)[:300]) if detail and not cond else ""))

    if shutil.which("git") is None:
        print("retire.py self-test: INCONCLUSIVE -- git required")
        return 2
    base = tempfile.mkdtemp(prefix="retire-selftest-")
    try:
        r = os.path.join(base, "repo")
        os.makedirs(r)

        def git(*a):
            return subprocess.run(["git", "-C", r] + list(a), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")

        def write(rel, text):
            p = os.path.join(r, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)

        def commit(msg):
            git("add", "-A")
            p = git("commit", "-q", "-m", msg)
            assert p.returncode == 0, p.stderr
            return git("rev-parse", "HEAD").stdout.strip()

        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@t")
        git("config", "user.name", "tester")
        git("config", "commit.gpgsign", "false")
        DERIV = ("# --- derivation (engine-managed; strip region) ---\nschema_version: 3.2\n"
                 "view: topic\nstatus: active\n# --- /derivation ---\n")
        VIEW = ("---\ntitle: View\nlast_updated: 2026-08-01\n---\n\n# View\n\nPreamble line one.\n\n"
                "## Section A {#sec-a}\n\nAlpha body line 1.\nAlpha body line 2 with anchor <a id=\"inline-a\"></a> inline.\n\n"
                "### A child\n\nChild text.\n\n```\n## not a heading\n```\n\n"
                "## Section B\n\nBeta body.\n\n## Section C\n\nGamma body.\n\n" + DERIV)
        write("wiki/topic/view.md", VIEW)
        write("raw/2026-08-01-event.md", "# Event\n\nBeta body.\n\nGamma body.\n\nsee view.md lines 12 and 14-16 for A\n")
        write("raw/README.md", "see view.md lines 12 for A -- NOT registered, so not in the universe\n")
        write("receipts/registrations/1.json", json.dumps({
            "kind": "registration", "seq": 1, "event": "raw/2026-08-01-event.md", "origin": "corpus",
            "origin_evidence": "fixture", "event_class": "compile", "event_class_origin": "explicit",
            "asserts_corpus_state": True, "registered_at": "2026-08-01T00:00:00", "prev_record_hash": None}))
        j1 = _core.minimal_record("compile", "0" * 40)
        j1.update({"seq": 1, "prev_record_hash": None})
        write("receipts/journal/1.json", json.dumps(j1, indent=1, sort_keys=True))
        write("project.yaml", "project_slug: t\ntrust_surface_signing: visible\n")
        h0 = commit("seed")
        # the registration chain reader: make sure the manifest's universe works here
        try:
            uni, _n = _manifest.citation_universe(r)
            universe_ok = True
        except Exception as e:
            uni, universe_ok = [], False
        case("fixture: citation universe establishable (registration chain + wiki)", universe_ok, uni)

        # -------- refusals before any write
        try:
            propose(r, "wiki/topic/view.md", titles=["Nope"])
            case("unknown span title refused", False)
        except Refuse as e:
            case("unknown span title refused naming the spans", "no span titled" in str(e), e)
        try:
            propose(r, "wiki/topic/view.md", titles=["Section A"])
            case("legacy citation into the span without a registry refused", False)
        except Refuse as e:
            case("legacy citation into the span without a frozen registry REFUSES naming it",
                 "NOT in the frozen legacy registry" in str(e) and "raw/2026-08-01-event.md" in str(e), e)
        case("no ref was created by a refused proposal", _prepared(r) == [])
        # cross-vendor round-2 fold: the citation universe is read from the BRANCH TIP, never
        # the working tree. A dirty working tree that HIDES the citing raw event (delete it on
        # disk) must NOT let the retirement slip past -- the committed event still names the
        # citation, so it still refuses.
        os.remove(os.path.join(r, "raw", "2026-08-01-event.md"))
        try:
            propose(r, "wiki/topic/view.md", titles=["Section A"])
            case("dirty-worktree citation hide not refused", False)
        except Refuse as e:
            case("2c: deleting the citing raw event from the WORKING TREE does not hide the "
                 "citation -- the universe is read from HEAD's objects, so it still refuses",
                 "NOT in the frozen legacy registry" in str(e), e)
        # ...and a citation added ONLY to the dirty working tree (never committed) does NOT
        # establish anything: it is not in HEAD, so it is invisible to the gate.
        subprocess.run(["git", "-C", r, "checkout", "--", "raw/2026-08-01-event.md"], capture_output=True)
        write("wiki/topic/other.md", "cites view.md lines 12 for A\n")  # uncommitted
        try:
            propose(r, "wiki/topic/view.md", titles=["Section A"])
            case("uncommitted citer seen", False)
        except Refuse as e:
            case("2c: an uncommitted citing artifact is invisible to the gate (universe = HEAD "
                 "objects); the refusal still names only the committed citation",
                 "raw/2026-08-01-event.md" in str(e) and "topic/other.md" not in str(e), e)
        os.remove(os.path.join(r, "wiki", "topic", "other.md"))
        write("project.yaml", "project_slug: t\n")
        try:
            propose(r, "wiki/topic/view.md", titles=["Section B"])
            case("unchosen mode refused", False)
        except Refuse as e:
            case("no recorded authority mode -> propose refuses (retirement disabled)",
                 "retirement disabled" in str(e), e)
        write("project.yaml", "project_slug: t\ntrust_surface_signing: visible\n")
        write("wiki/topic/view.md", VIEW + "\nuncommitted edit\n")
        try:
            propose(r, "wiki/topic/view.md", titles=["Section B"])
            case("dirty view refused", False)
        except Refuse as e:
            case("working-tree view differing from the branch refuses (never half-staged)",
                 "differs from main" in str(e), e)
        write("wiki/topic/view.md", VIEW)
        write("wiki/topic/noderiv.md", "# X\n\n## S\n\nbody\n")
        h0 = commit("add a view without a derivation region")
        try:
            propose(r, "wiki/topic/noderiv.md", titles=["S"])
            case("no derivation region refused", False)
        except Refuse as e:
            case("a view without a derivation region refuses (no home for the redirect block)",
                 "derivation region" in str(e), e)

        # -------- register legacy citations, then cold-relocate Section A (anchors, child, fence)
        reg_path, n = register_legacy(r)
        case("legacy registry built from the manifest seed (append-only file)", n >= 1 and
             os.path.isfile(os.path.join(r, reg_path)), (reg_path, n))
        h1 = commit("freeze legacy citations")
        res = propose(r, "wiki/topic/view.md", titles=["Section A"], now="2026-08-23T00:00:00Z")
        C = res["commit"]
        case("propose: C prepared on refs/retire/2 with seq 2 (chained onto journal 1)",
             res["seq"] == 2 and _rev(r, "refs/retire/2") == C, res)
        case("propose: working tree and index untouched (git status clean)",
             git("status", "--porcelain").stdout.strip() == "", git("status", "--porcelain").stdout)
        case("propose: production branch unchanged", _rev(r, "refs/heads/main") == h1)
        rec = json.loads(_blob(r, C, res["record"]).decode())
        sp = rec["spans"][0]
        case("record: run_type retire, parent = branch head, proposal digest = sha256(proposal blob)",
             rec["run_type"] == "retire" and rec["parent"] == h1
             and rec["proposal_digest"] == "sha256:" + _sha(_blob(r, C, rec["proposal"])), rec.get("parent"))
        case("record: prev_record_hash chains onto journal 1's bytes at the branch tip",
             rec["prev_record_hash"] == _core._record_hash(_blob(r, h1, "receipts/journal/1.json")))
        cold = _blob(r, C, sp["cold_object"]["path"])
        pre_lines = VIEW.split("\n")
        span_text = "\n".join(pre_lines[sp["start_line"] - 1:sp["end_line"]]) + "\n"
        case("gate 3: cold object bytes == the complete LF span bytes (headings, child, fence "
             "included); content-addressed by the span sha256",
             cold == span_text.encode("utf-8") and sp["cold_object"]["path"].endswith("--%s.md" % _sha(cold))
             and "### A child" in cold.decode() and "## not a heading" in cold.decode(), sp["cold_object"])
        post = _blob(r, C, "wiki/topic/view.md").decode()
        case("stub: heading line, descendant heading, extracted inline anchor token, pointer line",
             "## Section A {#sec-a}" in post and "### A child" in post
             and '<a id="inline-a"></a>' in post and "> Retired to wiki/cold/view/section-a--" in post
             and "journal seq 2." in post and "Alpha body line 1." not in post, post)
        case("stub: extracted inline anchor recorded as extracted-from its span line",
             sp["extracted_anchors"] and sp["extracted_anchors"][0]["id"] == "inline-a"
             and sp["extracted_anchors"][0]["extracted_from"] == 13, sp["extracted_anchors"])
        case("gate 1: anchor multiset conserved (check-split's primitive)",
             _split.anchor_multiset(VIEW) == _split.anchor_multiset(post))
        entries, pointer, idx = parse_retirements_block(post)
        case("redirect block: one entry inside the derivation region, comment-prefixed, keyed by "
             "seq + pre-hash, carrying span/stub/shift/target/post-hash",
             len(entries) == 1 and entries[0]["seq"] == 2 and entries[0]["pre_hash"] == gen_hash(VIEW)
             and entries[0]["post_hash"] == gen_hash(post) and entries[0]["target"] == sp["cold_object"]["path"]
             and idx is not None and all(post.split("\n")[i].startswith("#") for i in range(idx[0], idx[1] + 1)), entries)
        ok, reason, _rec = verify_prepared(r, C)
        case("verify_prepared re-derives C from objects: consistent", ok, reason)
        # round-5 fold: a nonempty retirement whose record claims redirects:[] is rejected
        rec_np = json.loads(_blob(r, C, res["record"]).decode())
        rec_np["redirects"] = []
        bad = json.dumps(rec_np, indent=1, sort_keys=True).encode()
        blobs_np = {res["record"]: (bad, _hash_object(r, bad))}
        for q in (rec_np["view"], rec_np["proposal"]):
            b = _blob(r, C, q); blobs_np[q] = (b, _hash_object(r, b))
        for co in rec_np["cold_objects"]:
            b = _blob(r, C, co["path"]); blobs_np[co["path"]] = (b, _hash_object(r, b))
        Cnp = _build_commit(r, h1, blobs_np, "nonempty but redirects:[]")
        okn, reasonn, _ = verify_prepared(r, Cnp)
        case("round-5 fold: a nonempty retirement recording redirects:[] is rejected "
             "(every retired span leaves a redirect)", not okn and "redirect" in reasonn, reasonn)
        # round-5 fold: a DECEPTIVE proposal (does not display the span's verbatim preimage) is rejected
        rec_dp = json.loads(_blob(r, C, res["record"]).decode())
        deceptive = b"# Retirement proposal -- seq 2\n\nthis proposal LIES about what C moves\n\n## Whole-view diff\n\n```diff\n```\n"
        rec_dp["proposal_digest"] = "sha256:" + _sha(deceptive)
        bad2 = json.dumps(rec_dp, indent=1, sort_keys=True).encode()
        blobs_dp = {res["record"]: (bad2, _hash_object(r, bad2)),
                    rec_dp["proposal"]: (deceptive, _hash_object(r, deceptive))}
        for q in (rec_dp["view"],):
            b = _blob(r, C, q); blobs_dp[q] = (b, _hash_object(r, b))
        for co in rec_dp["cold_objects"]:
            b = _blob(r, C, co["path"]); blobs_dp[co["path"]] = (b, _hash_object(r, b))
        Cdp = _build_commit(r, h1, blobs_dp, "deceptive proposal, digest-consistent")
        okd, reasond, _ = verify_prepared(r, Cdp)
        case("round-5 fold: a digest-consistent but DECEPTIVE proposal (does not display the "
             "span's verbatim preimage) is rejected", not okd and "deceptive" in reasond.lower(), reasond)
        # round-6 fold: a DESTRUCTIVE stub (drops the span's own heading / anchor) is rejected
        # even though recovery would replace it with the preimage and reconstruct fine.
        rec_ds = json.loads(_blob(r, C, res["record"]).decode())
        sp0 = rec_ds["spans"][0]
        # replace the stub with one that keeps only the pointer line (drops "## Section A {#sec-a}")
        ptr = next(l for l in sp0["stub_lines"] if l.startswith("> Retired to"))
        new_stub = [ptr, ""]
        pre_l = VIEW.split("\n")
        # rebuild view@C with the shortened stub
        a, b = sp0["start_line"] - 1, sp0["end_line"] - 1
        post_l = _blob(r, C, "wiki/topic/view.md").decode().split("\n")
        # locate the existing stub region in post (it starts at a, length len(old stub))
        old_len = len(sp0["stub_lines"])
        forged_view = "\n".join(post_l[:a] + new_stub + post_l[a + old_len:])
        sp0["stub_lines"] = new_stub
        rec_ds["post_view_sha256"] = _sha(forged_view.encode())
        rec_ds["post_generation"] = gen_hash(forged_view)
        bad3 = json.dumps(rec_ds, indent=1, sort_keys=True).encode()
        blobs_ds = {res["record"]: (bad3, _hash_object(r, bad3)),
                    "wiki/topic/view.md": (forged_view.encode(), _hash_object(r, forged_view.encode()))}
        for q in (rec_ds["proposal"],):
            bb = _blob(r, C, q); blobs_ds[q] = (bb, _hash_object(r, bb))
        for co in rec_ds["cold_objects"]:
            bb = _blob(r, C, co["path"]); blobs_ds[co["path"]] = (bb, _hash_object(r, bb))
        Cds = _build_commit(r, h1, blobs_ds, "destructive stub (drops the heading/anchor)")
        oks, reasons, _ = verify_prepared(r, Cds)
        case("round-6 fold: a DESTRUCTIVE stub that drops the span's heading/anchor is rejected "
             "(the stub is validated from the span bytes, not trusted for recovery)",
             not oks and ("drops heading" in reasons or "drops explicit anchor" in reasons), reasons)
        case("gate 3: whole-view reconstruction from post + cold object == pre (byte-exact, "
             "modulo the engine-owned block this retirement appended)",
             strip_block(reconstruct(post, [dict(sp)], r, C)) == strip_block(VIEW)
             and strip_block(VIEW) == VIEW.rstrip("\n") + "" if False else
             strip_block(reconstruct(post, [dict(sp)], r, C)) == strip_block(VIEW))
        prop = _blob(r, C, rec["proposal"]).decode()
        case("gate 5: the proposal DISPLAYS the complete preimage verbatim, the stub, the "
             "redirect entry and the whole-view diff", "Alpha body line 1." in prop
             and "Retiring preimage (complete, verbatim)" in prop and "Whole-view diff" in prop
             and "Redirect entries" in prop and '"seq": 2' in prop, prop[:200])
        case("gate 2: the inbound legacy citation (registered) is listed as healed by the redirect",
             rec["inbound_citations"] and rec["inbound_citations"][0]["artifact"] == "raw/2026-08-01-event.md"
             and rec["inbound_citations"][0]["generation"].startswith("legacy:"), rec["inbound_citations"])
        lst = list_prepared(r)
        case("--list: prepared, consistent, not stale, NOT publishable (no promotion record yet)",
             len(lst) == 1 and lst[0]["consistent"] and not lst[0]["stale"] and not lst[0]["publishable"], lst)
        chk = _trust.check_publishable(r, "retire/2", "main")
        case("trust.py refuses to publish C without a promotion record (names promote.py)",
             not chk["ok"] and "promote.py" in chk["reason"], chk["reason"])
        try:
            propose(r, "wiki/topic/view.md", titles=["Section B"])
            case("second proposal while seq 2 is prepared refused", False)
        except Refuse as e:
            case("a second proposal while refs/retire/2 exists refuses (one prepared seq at a time)",
                 "already exists" in str(e), e)

        # -------- promotion (as promote.py does it) + the reader + the worktree update
        git("tag", "-a", "-m", "promotion\nproposal_digest: sha256:%s\nmode: visible\n" % res["digest"],
            "retire/2", C)
        pub = _trust.publish_retirement(r, "retire/2", "main")
        case("promotion record + publish_retirement fast-forwards main to C", pub["ok"]
             and _rev(r, "refs/heads/main") == C, pub.get("reason"))
        git("reset", "-q", "--hard")
        case("after checkout the view on disk is the stub view and the cold object exists",
             open(os.path.join(r, "wiki/topic/view.md"), encoding="utf-8").read().replace("\r\n", "\n") == post
             and os.path.isfile(os.path.join(r, sp["cold_object"]["path"].replace("/", os.sep))))
        rr = _trust.retire_records_status(r, "main")
        case("the honest reader reads seq 2 PUBLISHED (kind promoted)",
             len(rr) == 1 and rr[0]["published"] and rr[0]["kind"] == "promoted", rr)
        rec_out = recover(r)
        case("--recover prunes the ref of a published retirement",
             rec_out and rec_out[0]["action"] == "pruned" and _prepared(r) == [], rec_out)
        case("compile-core check_chain accepts the journal with the retire record",
             _core.check_chain(r) == 2)

        # -------- citation resolution
        rs = resolve(r, "view.md:12", from_artifact="raw/2026-08-01-event.md")
        case("resolve: a registered legacy citation into the retired span lands in the cold "
             "object at the right offset", rs["resolved"] and rs["kind"] == "cold"
             and rs["target"] == sp["cold_object"]["path"] and rs["line"] == 12 - sp["start_line"] + 1, rs)
        gen8 = gen_hash(VIEW)[:8]
        rs = resolve(r, "view.md:25@%s" % gen8)
        case("resolve: a generation-tagged citation BELOW the span shifts by the stub delta",
             rs["resolved"] and rs["kind"] == "view" and rs["line"] == 25 + sp["shift"], rs)
        rs = resolve(r, "view.md:5@%s" % gen8)
        case("resolve: a tagged citation ABOVE the span is unchanged", rs["resolved"] and rs["line"] == 5, rs)
        try:
            resolve(r, "view.md:12", from_artifact="raw/unknown.md")
            case("unregistered legacy citation refused", False)
        except Refuse as e:
            case("resolve: an unregistered legacy citation is refused, never guessed",
                 "not in the registry" in str(e), e)
        rs = resolve(r, "view.md:3@%s" % gen_hash(post)[:8])
        case("resolve: a citation tagged with the CURRENT generation resolves directly",
             rs["resolved"] and rs["line"] == 3 and rs["via"] == [], rs)
        rs = resolve(r, "view.md:3@deadbeef")
        case("resolve: an unknown generation is reported unresolved (exit 2), never guessed",
             not rs["resolved"] and "not in the redirect chain" in rs["reason"], rs)
        rs = walk_redirects(entries, "deadbeef", 3)
        case("walk_redirects: an unknown generation is unresolved, never guessed", not rs["resolved"])

        # -------- ledger-dedup: fully mapped -> no cold object; partial -> downgrade
        pre2 = open(os.path.join(r, "wiki/topic/view.md"), encoding="utf-8").read().replace("\r\n", "\n")
        pl = pre2.split("\n")
        bline = next(i + 1 for i, l in enumerate(pl) if l == "Beta body.")
        mapping = [{"line": bline, "artifact": "raw/2026-08-01-event.md", "artifact_line": 3}]
        res2 = propose(r, "wiki/topic/view.md", titles=["Section B"], mode="dedup", mapping=mapping)
        rec2 = json.loads(_blob(r, res2["commit"], res2["record"]).decode())
        case("dedup: every substantive line mapped to an exact immutable raw/ line -> mode dedup, "
             "no cold object, pointer names the raw event",
             rec2["spans"][0]["mode"] == "dedup" and rec2["spans"][0]["cold_object"] is None
             and "> Retired to raw/2026-08-01-event.md -- journal seq 3." in
             _blob(r, res2["commit"], "wiki/topic/view.md").decode(), rec2["spans"][0])
        ok, reason, _ = verify_prepared(r, res2["commit"])
        case("dedup: verify_prepared recovers the pre view from view@parent (consistent)", ok, reason)
        # round-9 fold: a dedup record whose recorded mapping cites a nonexistent raw line is
        # rejected on re-verification (the mapping is re-checked against C's raw blobs)
        rec_fm = json.loads(_blob(r, res2["commit"], res2["record"]).decode())
        rec_fm["spans"][0]["mapping"] = [{"line": rec_fm["spans"][0]["mapping"][0]["line"],
                                          "artifact": "raw/2026-08-01-event.md", "artifact_line": 99}]
        bad_fm = json.dumps(rec_fm, indent=1, sort_keys=True).encode()
        blobs_fm = {res2["record"]: (bad_fm, _hash_object(r, bad_fm))}
        for q in (rec_fm["view"], rec_fm["proposal"]):
            bq = _blob(r, res2["commit"], q); blobs_fm[q] = (bq, _hash_object(r, bq))
        C_fm = _build_commit(r, _trust._parents(r, res2["commit"])[0], blobs_fm, "forged dedup mapping")
        okf, reasonf, _ = verify_prepared(r, C_fm)
        case("round-9 fold: a dedup record citing a NONEXISTENT raw line is rejected on "
             "re-verification (immutable conservation is checked, never trusted)",
             not okf and "dedup mapping" in reasonf, reasonf)
        git("update-ref", "-d", "refs/retire/3")
        bad = [{"line": bline, "artifact": "raw/2026-08-01-event.md", "artifact_line": 5}]
        res3 = propose(r, "wiki/topic/view.md", titles=["Section B"], mode="dedup", mapping=bad)
        rec3 = json.loads(_blob(r, res3["commit"], res3["record"]).decode())
        case("dedup: a mapping to a NON-matching line leaves the assertion unmapped -> automatic "
             "downgrade to cold-relocate (never drop, never refuse-only)",
             rec3["spans"][0]["mode"] == "cold" and rec3["spans"][0]["downgraded_from"] == "dedup"
             and rec3["spans"][0]["unmapped"] and rec3["spans"][0]["cold_object"], rec3["spans"][0])
        git("update-ref", "-d", "refs/retire/3")
        # cross-vendor round-2 fold: mapping is EXACT-line, not .strip(). A raw event line that
        # equals the span line only after stripping leading whitespace does NOT map -> the
        # assertion is unmapped -> the span downgrades to cold (which stores the exact bytes).
        subprocess.run(["git", "-C", r, "checkout", "--", "raw/2026-08-01-event.md"], capture_output=True)
        write("raw/2026-08-02-indented.md", "# Indented\n\n    Beta body.\n")  # 4-space indent
        write("receipts/registrations/2.json", json.dumps({
            "kind": "registration", "seq": 2, "event": "raw/2026-08-02-indented.md", "origin": "corpus",
            "origin_evidence": "fixture", "event_class": "compile", "event_class_origin": "explicit",
            "asserts_corpus_state": True, "registered_at": "2026-08-02T00:00:00",
            "prev_record_hash": hashlib.sha256(open(os.path.join(r, "receipts/registrations/1.json"), "rb").read()).hexdigest()}))
        commit("an indented raw event")
        strip_map = [{"line": bline, "artifact": "raw/2026-08-02-indented.md", "artifact_line": 3}]
        res3b = propose(r, "wiki/topic/view.md", titles=["Section B"], mode="dedup", mapping=strip_map)
        rec3b = json.loads(_blob(r, res3b["commit"], res3b["record"]).decode())
        case("2b: a mapping to a raw line that matches only after .strip() (indented) does NOT map "
             "-> the span downgrades to cold-relocate (exact-line, not stripped)",
             rec3b["spans"][0]["mode"] == "cold" and rec3b["spans"][0]["downgraded_from"] == "dedup", rec3b["spans"][0])
        git("update-ref", "-d", "refs/retire/3")
        # and the EXACT line DOES map
        write("raw/2026-08-03-exact.md", "# Exact\n\nBeta body.\n")
        write("receipts/registrations/3.json", json.dumps({
            "kind": "registration", "seq": 3, "event": "raw/2026-08-03-exact.md", "origin": "corpus",
            "origin_evidence": "fixture", "event_class": "compile", "event_class_origin": "explicit",
            "asserts_corpus_state": True, "registered_at": "2026-08-03T00:00:00",
            "prev_record_hash": hashlib.sha256(open(os.path.join(r, "receipts/registrations/2.json"), "rb").read()).hexdigest()}))
        commit("an exact raw event")
        exact_map = [{"line": bline, "artifact": "raw/2026-08-03-exact.md", "artifact_line": 3}]
        res3c = propose(r, "wiki/topic/view.md", titles=["Section B"], mode="dedup", mapping=exact_map)
        rec3c = json.loads(_blob(r, res3c["commit"], res3c["record"]).decode())
        case("2b: the EXACT raw line maps -> mode dedup, no cold object",
             rec3c["spans"][0]["mode"] == "dedup" and rec3c["spans"][0]["cold_object"] is None, rec3c["spans"][0])
        git("update-ref", "-d", "refs/retire/3")
        mut = [{"line": bline, "artifact": "wiki/topic/view.md", "artifact_line": bline}]
        res4 = propose(r, "wiki/topic/view.md", titles=["Section B"], mode="dedup", mapping=mut)
        rec4 = json.loads(_blob(r, res4["commit"], res4["record"]).decode())
        case("dedup: a mapping to MUTABLE prose (a wiki view) is not a home -> downgrade (R1-C1)",
             rec4["spans"][0]["mode"] == "cold", rec4["spans"][0]["mode"])
        git("update-ref", "-d", "refs/retire/3")

        # -------- cold reuse (same bytes) + collision refusal (same name, different bytes)
        write("wiki/topic/view.md", pre2)
        # a second view with the SAME section bytes -> its cold object path differs by view slug;
        # reuse is exercised by retiring the same span from a re-added copy under the same view
        res5 = propose(r, "wiki/topic/view.md", titles=["Section C"])
        rec5 = json.loads(_blob(r, res5["commit"], res5["record"]).decode())
        cpath5 = rec5["spans"][0]["cold_object"]["path"]
        git("tag", "-a", "-m", "promotion\nproposal_digest: sha256:%s\nmode: visible\n" % res5["digest"], "retire/3", res5["commit"])
        pub = _trust.publish_retirement(r, "retire/3", "main")
        git("reset", "-q", "--hard")
        recover(r)
        case("second retirement (seq 3) promoted and published", pub["ok"])
        # two "## Section C" headings exist if a section is re-added beside its own stub:
        # the verb refuses ambiguity rather than guessing
        write("wiki/topic/view.md", open(os.path.join(r, "wiki/topic/view.md"), encoding="utf-8").read()
              .replace("\r\n", "\n").replace("# --- derivation", "## Section C\n\nGamma body.\n\n# --- derivation", 1))
        commit("a section re-added beside its own stub")
        try:
            propose(r, "wiki/topic/view.md", titles=["Section C"])
            case("ambiguous span title refused", False)
        except Refuse as e2:
            case("a duplicated heading makes the span title ambiguous -> refuse, never guess",
                 "ambiguous" in str(e2), e2)
        git("revert", "--no-edit", "HEAD")
        # cold REUSE: a cold object already at the content address the new span computes --
        # as if an earlier generation had retired the same bytes (the only way the same
        # path recurs in one view) -- is byte-compared and reused, never rewritten.
        cur = open(os.path.join(r, "wiki/topic/view.md"), encoding="utf-8").read().replace("\r\n", "\n")
        sec_d = "## Section D\n\nDelta body.\n\n"
        write("wiki/topic/view.md", cur.replace("# --- derivation", sec_d + "# --- derivation", 1))
        cpath_d = "wiki/cold/view/section-d--%s.md" % _sha(sec_d.encode())
        write(cpath_d, sec_d)
        commit("Section D added; its cold object pre-exists at the content address")
        res6 = propose(r, "wiki/topic/view.md", titles=["Section D"])
        rec6 = json.loads(_blob(r, res6["commit"], res6["record"]).decode())
        case("cold reuse: a span whose bytes equal an existing cold object REUSES it (byte-compared), "
             "no second object written", rec6["spans"][0]["cold_object"]["reused"] is True
             and rec6["spans"][0]["cold_object"]["path"] == cpath_d
             and cpath_d not in [q for q in git("diff-tree", "--no-commit-id", "--name-only", "-r",
                                                res6["commit"] + "^", res6["commit"]).stdout.split()],
             rec6["spans"][0]["cold_object"])
        ok, reason, _ = verify_prepared(r, res6["commit"])
        case("cold reuse: C re-derives consistent (the reused object is in the tree)", ok, reason)
        git("update-ref", "-d", "refs/retire/%d" % res6["seq"])
        write(cpath_d, "tampered cold bytes\n")
        commit("tamper the pre-existing cold object (unsigned, on main)")
        try:
            propose(r, "wiki/topic/view.md", titles=["Section D"])
            case("tampered existing cold object not refused", False)
        except Refuse as e:
            case("an existing cold object at the content address with DIFFERENT bytes REFUSES "
                 "(identity is never trusted in place of a byte compare)", "DIFFERENT bytes" in str(e), e)
        git("revert", "--no-edit", "HEAD")
        git("revert", "--no-edit", "HEAD~2")  # Section D + its cold object gone again

        # -------- stale proposal + crash recovery
        res7 = propose(r, "wiki/topic/view.md", titles=["Section C"])
        write("wiki/topic/view.md", open(os.path.join(r, "wiki/topic/view.md"), encoding="utf-8").read().replace("\r\n", "\n").replace("Preamble line one.", "Preamble line one, absorbed."))
        commit("an intervening absorb moves main")
        lst = list_prepared(r)
        case("an intervening absorb makes the prepared C STALE (listed, not publishable)",
             lst and lst[0]["stale"] and not lst[0]["publishable"], lst)
        git("tag", "-a", "-m", "promotion\nproposal_digest: sha256:%s\nmode: visible\n" % res7["digest"], "retire/%d" % res7["seq"], res7["commit"])
        chk = _trust.check_publishable(r, "retire/%d" % res7["seq"], "main")
        case("...and even with a promotion record the publisher refuses the STALE C",
             not chk["ok"] and "STALE" in chk["reason"], chk["reason"])
        git("tag", "-d", "retire/%d" % res7["seq"])
        out = recover(r)
        case("--recover DISCARDS the stale C deterministically (ref deleted, reason STALE)",
             out and out[0]["action"] == "discarded" and "STALE" in out[0]["reason"] and _prepared(r) == [], out)
        # a crash between writes: simulate by building C then corrupting -- a ref that names an
        # inconsistent commit (record digest != proposal)
        res8 = propose(r, "wiki/topic/view.md", titles=["Section C"])
        head = _rev(r, "refs/heads/main")
        rec8 = json.loads(_blob(r, res8["commit"], res8["record"]).decode())
        rec8["proposal_digest"] = "sha256:" + "0" * 64
        bad_rec = json.dumps(rec8, indent=1, sort_keys=True).encode()
        blobs = {res8["record"]: (bad_rec, _hash_object(r, bad_rec)),
                 rec8["view"]: (_blob(r, res8["commit"], rec8["view"]), _hash_object(r, _blob(r, res8["commit"], rec8["view"]))),
                 rec8["proposal"]: (_blob(r, res8["commit"], rec8["proposal"]), _hash_object(r, _blob(r, res8["commit"], rec8["proposal"])))}
        for co in rec8["cold_objects"]:
            b = _blob(r, res8["commit"], co["path"])
            blobs[co["path"]] = (b, _hash_object(r, b))
        bad_c = _build_commit(r, head, blobs, "inconsistent")
        git("update-ref", "refs/retire/%d" % res8["seq"], bad_c)
        out = recover(r)
        case("--recover DISCARDS an inconsistent C (record digest != proposal) -- complete or "
             "discard, nothing in between", out and out[0]["action"] == "discarded"
             and "INCONSISTENT" in out[0]["reason"] and _prepared(r) == [], out)
        res9 = propose(r, "wiki/topic/view.md", titles=["Section C"])
        out = recover(r)
        case("--recover KEEPS a consistent, current C (awaiting promotion)",
             out and out[0]["action"] == "kept" and _prepared(r) == [(res9["seq"], "refs/retire/%d" % res9["seq"])], out)
        case("a prepared C is idempotent under recovery (second run: still kept, same commit)",
             recover(r)[0]["commit"] == res9["commit"])
        git("update-ref", "-d", "refs/retire/%d" % res9["seq"])

        # -------- preamble + multi-span batch + compaction trigger
        res10 = propose(r, "wiki/topic/view.md", preamble=True, titles=["Section C"])
        rec10 = json.loads(_blob(r, res10["commit"], res10["record"]).decode())
        post10 = _blob(r, res10["commit"], "wiki/topic/view.md").decode()
        case("batch: preamble + a section retire in ONE proposal (two spans, two entries, one "
             "record, one digest)", len(rec10["spans"]) == 2 and rec10["spans"][0]["title"] == "(preamble)"
             and len(rec10["redirects"]) == 2 and rec10["spans"][0]["cold_object"], rec10["spans"])
        case("preamble stub is a pointer line only (cold-relocate)",
             post10.split("\n")[4:6] == [POINTER_TMPL % (rec10["spans"][0]["target"], res10["seq"]), ""], post10.split("\n")[:8])
        ok, reason, _ = verify_prepared(r, res10["commit"])
        case("batch: consistent under re-derivation", ok, reason)
        git("update-ref", "-d", "refs/retire/%d" % res10["seq"])
        # compaction: a block over the entry cap moves to a content-addressed cold index
        global _CAPS_OVERRIDE
        _CAPS_OVERRIDE = {"entries": 2, "bytes": DEFAULT_BLOCK_MAX_BYTES}
        try:
            res11 = propose(r, "wiki/topic/view.md", titles=["Section C"])
            rec11 = json.loads(_blob(r, res11["commit"], res11["record"]).decode())
            post11 = _blob(r, res11["commit"], "wiki/topic/view.md").decode()
            ent, ptr, _ = parse_retirements_block(post11)
            case("compaction: over the entry cap the block becomes ONE pointer row and the full "
                 "chain lands in a content-addressed cold index in C's tree",
                 rec11["compaction"] and ent == [] and ptr and ptr["index"].startswith("wiki/cold/view/redirects--")
                 and _blob(r, res11["commit"], ptr["index"]) is not None and ptr["entries"] == 3, (rec11.get("compaction"), ptr))
            chain = all_redirects(r, res11["commit"], "wiki/topic/view.md")
            case("compaction: the resolver sees index + block = the whole chain (3 entries, in order)",
                 [e["seq"] for e in chain] == [2, 3, res11["seq"]], [e["seq"] for e in chain])
            ok, reason, _ = verify_prepared(r, res11["commit"])
            case("compaction: C re-derives consistent (index is an allowed path)", ok, reason)
            git("tag", "-a", "-m", "promotion\nproposal_digest: sha256:%s\nmode: visible\n" % res11["digest"], "retire/%d" % res11["seq"], res11["commit"])
            pub = _trust.publish_retirement(r, "retire/%d" % res11["seq"], "main")
            git("reset", "-q", "--hard")
            recover(r)
            rs = resolve(r, "view.md:12", from_artifact="raw/2026-08-01-event.md")
            case("after compaction a legacy citation still resolves through the index",
                 pub["ok"] and rs["resolved"] and rs["kind"] == "cold", rs)
        finally:
            _CAPS_OVERRIDE = None

        # -------- block immutability helper for the absorb validator
        cur = open(os.path.join(r, "wiki/topic/view.md"), encoding="utf-8").read().replace("\r\n", "\n")
        case("block_bytes: the retirements block is extractable (markers inclusive) for the "
             "absorb validator's immutability rule", block_bytes(cur).startswith(RET_START.encode())
             and block_bytes(cur).endswith(RET_END.encode()) and block_bytes("# x\n\nbody\n") == b"")

        # -------- v3.0.51 (v3.0-146): the CANONICAL layout -- region at TOP, the fixture
        # GENERATED by assemble.py's own shape (frontmatter -> region -> body). The verb
        # must propose/verify on it (the v3.0.50 region-at-end model refused every
        # engine-compiled view, fleet inbox #6); the GUARD case fails if assemble.py's
        # canonical shape ever diverges, so the two tools' layout models cannot drift
        # apart silently again (the canonical-fixture rule; same guard in the manifest
        # battery).
        _asm = _load_by_path("_retire_asm", "assemble.py")
        canon = _asm._mk_view(entities=[], summary="canonical layout fixture") + \
            "\n## Canon A\n\nAlpha canonical body.\n\n## Canon B\n\nBeta canonical body.\n"
        c_lines = canon.split("\n")
        c_ds, c_de = _region_bounds(c_lines)
        c_fm_end = c_lines.index("---", 1)
        case("guard: assemble.py's generated fixture is frontmatter -> region -> body "
             "(divergence here fails BOTH batteries -- the canonical-fixture rule)",
             c_ds == c_fm_end + 1 and c_de is not None
             and all(not ln.strip() for ln in c_lines[c_fm_end + 1:c_ds]), (c_ds, c_de, c_fm_end))
        r2 = os.path.join(base, "repo2")
        os.makedirs(r2)

        def git2(*a):
            return subprocess.run(["git", "-C", r2] + list(a), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")

        def write2(rel, text):
            p = os.path.join(r2, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)

        git2("init", "-q", "-b", "main")
        git2("config", "user.email", "t@t")
        git2("config", "user.name", "tester")
        git2("config", "commit.gpgsign", "false")
        write2("wiki/topic/canon.md", canon)
        write2("project.yaml", "project_slug: t2\ntrust_surface_signing: visible\n")
        j1c = _core.minimal_record("compile", "0" * 40)
        j1c.update({"seq": 1, "prev_record_hash": None})
        write2("receipts/journal/1.json", json.dumps(j1c, indent=1, sort_keys=True))
        git2("add", "-A")
        git2("commit", "-q", "-m", "canonical seed")
        resc = propose(r2, "wiki/topic/canon.md", titles=["Canon A"], now="2026-08-24T00:00:00Z")
        Cc = resc["commit"]
        case("canonical layout: a span AFTER a top-of-file region PROPOSES (the v3.0.50 "
             "region-at-end assumption refused every engine-compiled view)",
             resc["seq"] == 2 and _rev(r2, "refs/retire/2") == Cc, resc)
        recc = json.loads(_blob(r2, Cc, resc["record"]).decode())
        spc = recc["spans"][0]
        postc = _blob(r2, Cc, "wiki/topic/canon.md").decode()
        case("canonical layout: stub in place, body moved to the cold object, redirect "
             "entry in the top-of-file region, anchors conserved",
             "## Canon A" in postc and "Alpha canonical body." not in postc
             and spc["cold_object"] and _blob(r2, Cc, spc["cold_object"]["path"]) is not None
             and _split.anchor_multiset(canon) == _split.anchor_multiset(postc), spc)
        case("canonical layout: whole-view reconstruction from post + cold object == pre",
             strip_block(reconstruct(postc, [dict(spc)], r2, Cc)) == strip_block(canon))
        okc, reasonc, _ = verify_prepared(r2, Cc)
        case("canonical layout: C re-derives consistent from objects", okc, reasonc)
        git2("update-ref", "-d", "refs/retire/2")
        # -------- v3.0-143: the SHARED clip fixture -- the verb and the manifest clip the
        # last span (region at file END) to the SAME bytes (the v3.0-132 two-tool rule);
        # the manifest battery pins the same constants.
        write2("wiki/topic/clip.md", _manifest._SHARED_CLIP_FIXTURE)
        git2("add", "-A")
        git2("commit", "-q", "-m", "clip fixture")
        resx = propose(r2, "wiki/topic/clip.md", titles=["Only Section"])
        recx = json.loads(_blob(r2, resx["commit"], resx["record"]).decode())
        spx = recx["spans"][0]
        case("shared clip fixture: the verb's clipped span sha/bytes/cold bytes equal the "
             "manifest's (_SHARED_CLIP_EXPECT) -- the two clips cannot diverge silently",
             spx["sha256"] == _sha(_manifest._SHARED_CLIP_EXPECT.encode("utf-8"))
             and spx["bytes"] == len(_manifest._SHARED_CLIP_EXPECT.encode("utf-8"))
             and _lf(_blob(r2, resx["commit"], spx["cold_object"]["path"])) ==
             _manifest._SHARED_CLIP_EXPECT.encode("utf-8"), spx)
        msx = [s for s in _manifest.parse_spans(_manifest._SHARED_CLIP_FIXTURE)
               if s["title"] == "Only Section"][0]
        case("shared clip fixture: manifest parse_spans reports the SAME sha/bytes",
             msx["sha256"] == spx["sha256"] and msx["bytes"] == spx["bytes"], msx)
        git2("update-ref", "-d", "refs/retire/%d" % resx["seq"])
        # a span crossing NON-BLANK text beyond the region refuses (one contiguous slice)
        strad = ("---\ntitle: S\n---\n\n## Top\n\nAbove region.\n\n"
                 "# --- derivation (engine-managed; strip region) ---\nschema_version: 3.2\n"
                 "# --- /derivation ---\n\nBelow region, same section.\n")
        write2("wiki/topic/strad.md", strad)
        git2("add", "-A")
        git2("commit", "-q", "-m", "straddle fixture")
        try:
            propose(r2, "wiki/topic/strad.md", titles=["Top"])
            case("straddling span refused", False)
        except Refuse as e:
            case("a span crossing the region onto non-blank text REFUSES (never silently "
                 "retires half a section)", "crosses the derivation region" in str(e), e)
        # publish the canonical retirement (as promote.py would), then retire a SECOND
        # span: the block ABOVE the body grows, and resolution must track the stub delta
        # AND the block growth (bshift) -- pinned against the real file positions, both
        # sides computed from the actual texts, never hand-numbered.
        resc2 = propose(r2, "wiki/topic/canon.md", titles=["Canon A"])
        git2("tag", "-a", "-m", "promotion\nproposal_digest: sha256:%s\nmode: visible\n" % resc2["digest"],
             "retire/2", resc2["commit"])
        pub_c = _trust.publish_retirement(r2, "retire/2", "main")
        git2("reset", "-q", "--hard")
        recover(r2)
        t2 = open(os.path.join(r2, "wiki", "topic", "canon.md"), encoding="utf-8").read().replace("\r\n", "\n")
        g2h = gen_hash(t2)
        beta_at_g1 = canon.split("\n").index("Beta canonical body.") + 1
        beta_at_g2 = t2.split("\n").index("Beta canonical body.") + 1
        rs_c = resolve(r2, "canon.md:%d@%s" % (beta_at_g1, gen_hash(canon)[:8]))
        case("canonical bshift: a g1 citation below the top-of-file region resolves through "
             "stub delta + block growth to the line's REAL current position",
             pub_c["ok"] and rs_c["resolved"] and rs_c["kind"] == "view"
             and rs_c["line"] == beta_at_g2, (rs_c, beta_at_g2))
        resd = propose(r2, "wiki/topic/canon.md", titles=["Canon B"])
        recd = json.loads(_blob(r2, resd["commit"], resd["record"]).decode())
        okd2, reasond2, _ = verify_prepared(r2, resd["commit"])
        case("canonical second retirement (the pre view already carries a block above the "
             "body): C re-derives consistent (generation-coordinate recovery)", okd2, reasond2)
        # a FORGED bshift (view block row and record changed together) is rejected: the
        # field is re-derived from the two views, never trusted
        post_d = _blob(r2, resd["commit"], "wiki/topic/canon.md").decode()
        ents_d, ptr_d, idx_d = parse_retirements_block(post_d)
        ents_f = [dict(x) for x in ents_d]
        ents_f[-1]["bshift"] = ents_f[-1]["bshift"] + 1
        pl_d = post_d.split("\n")
        pl_d[idx_d[0]:idx_d[1] + 1] = render_block(ents_f, ptr_d)
        forged_v = "\n".join(pl_d)
        rec_fb = json.loads(_blob(r2, resd["commit"], resd["record"]).decode())
        rec_fb["redirects"] = [e for e in ents_f if e["seq"] == resd["seq"]]
        rec_fb["post_view_sha256"] = _sha(forged_v.encode("utf-8"))
        rec_fb["post_generation"] = gen_hash(forged_v)
        bad_fb = json.dumps(rec_fb, indent=1, sort_keys=True).encode("utf-8")
        blobs_fb = {resd["record"]: (bad_fb, _hash_object(r2, bad_fb)),
                    "wiki/topic/canon.md": (forged_v.encode("utf-8"), _hash_object(r2, forged_v.encode("utf-8")))}
        for q_fb in (rec_fb["proposal"],):
            b_fb = _blob(r2, resd["commit"], q_fb)
            blobs_fb[q_fb] = (b_fb, _hash_object(r2, b_fb))
        for co_fb in rec_fb["cold_objects"]:
            b_fb = _blob(r2, resd["commit"], co_fb["path"])
            blobs_fb[co_fb["path"]] = (b_fb, _hash_object(r2, b_fb))
        C_fb = _build_commit(r2, _trust._parents(r2, resd["commit"])[0], blobs_fb, "forged bshift")
        okfb, reasonfb, _ = verify_prepared(r2, C_fb)
        case("a FORGED block-shift field is rejected on re-verification (re-derived from "
             "the views, never trusted)", not okfb and "block-shift" in reasonfb, reasonfb)
        # ...and OMITTING the fields (view block row and record together) is rejected too
        # (cross-vendor round-1 catch: absence must not default to no-shift)
        ents_o = [dict(x) for x in ents_d]
        ents_o[-1].pop("bshift", None)
        ents_o[-1].pop("bafter", None)
        pl_o = post_d.split("\n")
        pl_o[idx_d[0]:idx_d[1] + 1] = render_block(ents_o, ptr_d)
        omit_v = "\n".join(pl_o)
        rec_ob = json.loads(_blob(r2, resd["commit"], resd["record"]).decode())
        rec_ob["redirects"] = [e for e in ents_o if e["seq"] == resd["seq"]]
        rec_ob["post_view_sha256"] = _sha(omit_v.encode("utf-8"))
        rec_ob["post_generation"] = gen_hash(omit_v)
        bad_ob = json.dumps(rec_ob, indent=1, sort_keys=True).encode("utf-8")
        blobs_ob = {resd["record"]: (bad_ob, _hash_object(r2, bad_ob)),
                    "wiki/topic/canon.md": (omit_v.encode("utf-8"), _hash_object(r2, omit_v.encode("utf-8")))}
        for q_ob in (rec_ob["proposal"],):
            b_ob = _blob(r2, resd["commit"], q_ob)
            blobs_ob[q_ob] = (b_ob, _hash_object(r2, b_ob))
        for co_ob in rec_ob["cold_objects"]:
            b_ob = _blob(r2, resd["commit"], co_ob["path"])
            blobs_ob[co_ob["path"]] = (b_ob, _hash_object(r2, b_ob))
        C_ob = _build_commit(r2, _trust._parents(r2, resd["commit"])[0], blobs_ob, "omitted bshift")
        okob, reasonob, _ = verify_prepared(r2, C_ob)
        case("an entry OMITTING bshift/bafter is rejected (absence never defaults to "
             "no-shift; this verb always writes the fields)",
             not okob and "omits its block-shift" in reasonob, reasonob)
        git2("tag", "-a", "-m", "promotion\nproposal_digest: sha256:%s\nmode: visible\n" % resd["digest"],
             "retire/%d" % resd["seq"], resd["commit"])
        pub_d = _trust.publish_retirement(r2, "retire/%d" % resd["seq"], "main")
        git2("reset", "-q", "--hard")
        recover(r2)
        rs_d = resolve(r2, "canon.md:%d@%s" % (beta_at_g2, g2h[:8]))
        case("canonical bshift: a g2 citation INTO the second retired span resolves to its "
             "cold object at the right offset",
             pub_d["ok"] and rs_d["resolved"] and rs_d["kind"] == "cold"
             and rs_d["target"] == recd["spans"][0]["cold_object"]["path"]
             and rs_d["line"] == beta_at_g2 - recd["spans"][0]["start_line"] + 1, rs_d)
        case("canonical-layout battery leaves nothing prepared", _prepared(r2) == [])
        # -------- release scope switch
        global RELEASE_SCOPE
        RELEASE_SCOPE = "disabled"
        try:
            propose(r, "wiki/topic/view.md", titles=["Section C"])
            case("disabled scope refused", False)
        except Refuse as e:
            case("RELEASE_SCOPE disabled -> every --propose refuses naming G2", "DISABLED" in str(e), e)
        finally:
            RELEASE_SCOPE = "targeted"
        case("a cold object is never retired", _refuses(lambda: propose(r, cpath5, titles=["x"]), "immutable"))
        case("nothing prepared is left behind by the battery", _prepared(r) == [])
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("retire.py self-test: %s (%d/%d)" % ("FAIL" if failed else "PASS", total - failed, total))
    return 1 if failed else 0


def _refuses(fn, needle):
    try:
        fn()
        return False
    except Refuse as e:
        return needle in str(e)


# ------------------------------------------------------------------ CLI
def main(argv=None):
    ap = argparse.ArgumentParser(prog="retire.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root", default=".")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--propose", metavar="VIEW")
    ap.add_argument("--span", action="append", default=[], metavar="TITLE")
    ap.add_argument("--preamble", action="store_true")
    ap.add_argument("--mode", choices=["cold", "dedup"], default="cold")
    ap.add_argument("--mapping", metavar="JSON")
    ap.add_argument("--recover", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--show", metavar="DIGEST")
    ap.add_argument("--resolve", metavar="CITE")
    ap.add_argument("--from", dest="from_artifact", metavar="ARTIFACT")
    ap.add_argument("--register-legacy", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    root = os.path.abspath(a.root)
    try:
        if a.propose:
            mapping = json.load(open(a.mapping, encoding="utf-8")) if a.mapping else None
            res = propose(root, a.propose, a.span, a.preamble, a.mode, mapping, a.branch)
            if a.json:
                print(json.dumps(res, indent=1, default=str))
            else:
                print("PREPARED retirement seq %d on refs/retire/%d (commit %s)" % (res["seq"], res["seq"], res["commit"][:12]))
                for s in res["spans"]:
                    print("  span %r lines %d-%d: %d bytes -> %s (%s)" % (
                        s["title"], s["start_line"], s["end_line"], s["bytes"], s["mode"], s["target"]))
                print("  proposal: %s" % res["proposal"])
                print("  proposal digest: sha256:%s" % res["digest"])
                print("Nothing is published. To publish (trust_surface_signing: visible), from YOUR terminal:")
                print("  py deploy/promote.py %s" % res["digest"][:16])
                print("(under required: inspect C, then `git tag -s retire/%d %s` and `py deploy/trust.py --publish retire/%d`)" % (res["seq"], res["commit"][:12], res["seq"]))
            return 0
        if a.recover:
            out = recover(root, a.branch)
            for o in out:
                print("refs/retire/%d %s: %s -- %s" % (o["seq"], o["commit"][:12], o["action"].upper(), o["reason"]))
            if not out:
                print("nothing prepared; nothing to recover")
            return 0
        if a.list:
            rows = list_prepared(root, a.branch)
            for x in rows:
                print("seq %d %s view %s digest %s: %s%s%s" % (
                    x["seq"], x["commit"][:12], x["view"], (x["digest"] or "?")[7:23],
                    "consistent" if x["consistent"] else "INCONSISTENT (" + x["reason"] + ")",
                    " STALE" if x["stale"] else "", " publishable" if x["publishable"] else " awaiting promotion"))
            if not rows:
                print("no prepared retirements")
            return 0
        if a.show:
            seq, c, ok, reason, rec = find_by_digest(root, a.show)
            print(_blob(root, c, rec["proposal"]).decode("utf-8", "replace"))
            return 0
        if a.resolve:
            r = resolve(root, a.resolve, a.branch, a.from_artifact)
            print(json.dumps(r, indent=1))
            return 0 if r.get("resolved") else 2
        if a.register_legacy:
            p, n = register_legacy(root, a.branch)
            print("frozen %d legacy citation(s) into %s -- commit it" % (n, p))
            return 0
    except Refuse as e:
        print("REFUSED: %s" % e)
        return 2
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
