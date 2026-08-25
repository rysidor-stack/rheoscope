#!/usr/bin/env python3
"""trust.py -- trust-surface integrity primitives (v3.0-120, ships v3.0.46).

ADR #11 condition 4 / G2 as AMENDED 2026-08-22 (v3.0.48, backlog v3.0-135, handoff
2026-08-22-adr11-condition4-reversible-visible): authority is "exact, informed,
explicitly selected, reversible, and observable". The operator chooses the instance's
authority mode ONCE -- `project.yaml: trust_surface_signing: visible | required` -- and
the absence of a recorded choice never selects one: a fresh instance is asked at init;
an existing instance resolves to migration-only `warn` with RETIREMENT DISABLED until
the choice is recorded. Under `visible` the agent prepares a retirement (deploy/retire.py,
Release 2 / v3.0.50) and ONE lightweight operator action outside the session --
`deploy/promote.py <proposal-digest>` in the operator's own terminal -- names the exact
proposal digest and constructs the PROMOTION RECORD (an annotated, unsigned tag
retire/<seq> on the prepared commit whose message carries `proposal_digest:` and
`mode: visible`) and fast-forwards the branch atomically; check_publishable accepts that
record in place of the signed tag under `visible` ONLY. Under `required` the
presence-requiring signature below is the root and the sk tag path is unchanged; a
verified operator tag publishes under either CHOSEN mode. The promotion record is a
procedural human boundary for the stated non-adaptive threat (a misled, wrong or
unattended session), not cryptographic proof of human origin: every retirement and
trust-surface change is ALSO reconstructed into a durable pending item from git objects
(deploy/pending.py) that stays outstanding until an attended sweep shows it to the
operator. Design of the signing machinery (kept, optional hardening):
harness-v3.0/specs/trust-surface-integrity-mini-pass-2026-08-21.md (brief v4).

WHAT THIS FILE IS, AND IS NOT. This module runs in the agent's own process and is
itself a member of the trust-surface class (core/security/hooks/trust-surfaces.txt).
A same-user agent can patch it. The design therefore does NOT make this code the
root of trust: the root is the OPERATOR'S PHYSICAL SECURITY KEY (a presence-
requiring FIDO "sk" SSH key) on the commit or tag that PUBLISHES a trust-surface
change or a retirement. What this module does is make every HONEST reader refuse
unsigned or tampered trust-surface state, and make the publisher-side checks
mechanical. A tampered copy of this file can lie; it cannot produce an operator
signature. Every consumer that imports this file says the same thing.

Primitives (brief sections 4-5):
  committed_identical(repo, path)      working-tree bytes == HEAD's blob, and tracked.
  operator_signed(repo, path)          the newest commit touching `path` carries an SSH
                                       signature that verifies against the pinned
                                       allowed_signers -- sk-typed keys ONLY (a non-sk
                                       key is filtered OUT of the pin before ssh-keygen
                                       sees it; its signature is `untrusted`, never
                                       "warned"); the pin's own history is chain-verified
                                       (verify_pin_chain) so no later pin content can vouch
                                       for itself.
  verify_pin_chain(repo, commit)       every commit in `commit`'s history that touched
                                       core/security/hooks/allowed_signers is signed by a
                                       key listed in the file AT ITS PARENT; the first
                                       (bootstrap) commit is signed by a key it lists
                                       itself; a merge touching the pin, a deletion, or a
                                       delete-and-recreate is refused.
  operator_tag(repo, tag, commit)      refs/tags/<tag> is an annotated tag OBJECT whose
                                       embedded name is <tag>, whose `object` is exactly
                                       <commit>, signed by a pinned sk key.
  promotion_tag(repo, tag, commit)     (v3.0.50) refs/tags/<tag> is an annotated tag OBJECT
                                       naming exactly <commit> whose message carries
                                       `proposal_digest: sha256:<hex>` and `mode: visible`
                                       -- the visible-mode PROMOTION RECORD deploy/
                                       promote.py writes from the operator's terminal.
  publication_authority(repo, tag, c)  the one seam every publisher/reader consults: a
                                       verified operator tag (any chosen mode), or, under
                                       `visible` only, a promotion tag; the caller then
                                       binds the promotion's digest to the record's.
  check_publishable(repo, tag, branch) the Release-2 publisher's gate (brief section 5):
                                       tag verifies + names C; C has exactly one parent
                                       and it IS the production head (no merge ancestry,
                                       no extra commits, no stale proposal); C's tree
                                       carries a retire journal record whose
                                       proposal_digest equals the digest of the proposal
                                       artifact in the SAME tree, whose seq/tag match the
                                       tag name; the digest and seq are not already
                                       consumed on the production branch; under `visible`
                                       the promotion record's digest must equal the
                                       record's (exact-proposal binding).
  publish_retirement(repo, tag, branch) fast-forward of the production branch to C,
                                       atomic on the expected old head, ONLY when
                                       check_publishable passes.
  retire_records_status(repo, rev)     every `run_type: retire` journal record in <rev>'s
                                       tree, with whether its introducing commit carries
                                       a verified operator tag -- an unverified one is an
                                       UNPUBLISHED PROPOSAL (the sweep and doctor name it).
  gate_artifact(repo, path)            the HUMAN-GATE consumer entry point:
                                       committed_identical is ALWAYS required; operator_
                                       signed is refused under project.yaml
                                       trust_surface_signing: required, warned (accepted
                                       + surfaced) under warn, accepted silently under
                                       visible (sensors still run).
  mode_chosen(repo)                    project.yaml RECORDS visible or required. Absent
                                       OR warn -> migration-only compatibility; retirement
                                       is disabled (check_publishable refuses first).

Every git read goes through `git --no-replace-objects` so a `git replace` ref cannot
substitute an object under a verified hash (G2: alternate write paths).

Usage:
  trust.py --self-test
  trust.py --root R --report [--json]                 class-wide table (sweep step 17)
  trust.py --root R --check PATH [PATH...]            gate_artifact per path, exit 2 on refuse
  trust.py --root R --verify-tag TAG --commit SHA
  trust.py --root R --check-publish TAG [--branch main]
  trust.py --root R --publish TAG [--branch main]     fast-forward on a passing check
  trust.py --root R --retire-records                  unpublished-proposal census

Exit: 0 ok | 1 self-test failure | 2 refusal / inconclusive.
"""

import argparse
import base64
import contextlib
import hashlib
import json
import os
import posixpath
import re
import shutil
import struct
import subprocess
import sys
import tempfile

try:  # cp1252 consoles + unicode paths: never let an encode error mask a verdict.
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PIN_PATH = "core/security/hooks/allowed_signers"
CLASS_PATH = "core/security/hooks/trust-surfaces.txt"
JOURNAL_DIR = "receipts/journal"
SSH_NAMESPACE = "git"

# Presence-requiring key types (FIDO/U2F-backed). A signature by any other type is
# UNTRUSTED. This set is the whole reason the operator's daily id_ed25519 cannot vouch
# for anything: an agent with an unlocked agent socket holding that key gains nothing.
SK_TYPES = frozenset({"sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com"})

# The hard-coded FLOOR of the class. core/security/hooks/trust-surfaces.txt can only
# WIDEN it (union) -- never narrow it -- so an emptied or missing file fails closed.
# The same floor is embedded in both hooks and the doctor; the hook battery pins the
# shipped file equal to the floor so the four homes cannot drift apart unnoticed.
TRUST_SURFACE_FLOOR = (
    "core/security/hooks/**",
    "deploy/safe-allowlist.yaml",
    "deploy/evidence/operator-*.md",
    "deploy/rulings/**",
    "deploy/trust.py",
    "deploy/compile-driver.py",
    "deploy/compile-backends.py",
    "deploy/audit-content.py",
    "deploy/retire.py",
    "deploy/promote.py",
    "deploy/pending.py",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".git/hooks/**",
    ".gitattributes",
)
# Members that are never tracked (per-machine / per-clone): hook-lane members only.
# HEAD-identity and signing do not apply; the doctor's (c) wiring check covers them.
UNTRACKED_MEMBERS = (".claude/settings.local.json", ".git/hooks/**")

# The type set the verifier accepts. PUBLIC API never changes this; self_test()
# rebinds it inside a context manager (and restores it) to exercise the signature
# MECHANICS with software keys on a platform where no FIDO token can be touched. No
# CLI flag, no consumer, and no environment variable reaches this -- a reviewer grepping
# for `_ACCEPT_TYPES` will find exactly two writers: the module line and the self-test.
_ACCEPT_TYPES = SK_TYPES


class TrustError(Exception):
    """A refusal with a reason the caller must surface verbatim."""


# ------------------------------------------------------------------ git plumbing
def _git(repo, *args, input_bytes=None, check=False):
    """git with --no-replace-objects, bytes in/out. Returns (rc, stdout, stderr)."""
    cmd = ["git", "--no-replace-objects", "-C", repo] + list(args)
    p = subprocess.run(cmd, input=input_bytes, capture_output=True)
    if check and p.returncode != 0:
        raise TrustError("git %s failed: %s" % (" ".join(args[:2]),
                                                 p.stderr.decode("utf-8", "replace").strip()))
    return p.returncode, p.stdout, p.stderr


def _git_text(repo, *args, check=False):
    rc, out, err = _git(repo, *args, check=check)
    return rc, out.decode("utf-8", "replace")


def is_git_repo(repo):
    rc, out = _git_text(repo, "rev-parse", "--is-inside-work-tree")
    return rc == 0 and out.strip() == "true"


def _rev(repo, ref):
    rc, out = _git_text(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
    return out.strip() if rc == 0 else None


def _blob_at(repo, commit, path):
    """Bytes of <commit>:<path>, or None when absent."""
    rc, out, _ = _git(repo, "cat-file", "blob", "%s:%s" % (commit, path))
    return out if rc == 0 else None


def _parents(repo, commit):
    rc, out = _git_text(repo, "rev-list", "--parents", "-n", "1", commit)
    if rc != 0:
        return None
    parts = out.split()
    return parts[1:]


# ------------------------------------------------------------------ class + mode
def _glob_to_re(glob):
    out = "(^|/)"
    i = 0
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**", i):
            out += ".*"
            i += 2
            continue
        if c == "*":
            out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        else:
            out += re.escape(c)
        i += 1
    return re.compile(out + "$")


def load_class(repo):
    """The trust-surface class: FLOOR union the committed trust-surfaces.txt.

    Reads the file from the WORKING TREE (an uncommitted widening still widens --
    widening is always safe; narrowing is impossible by construction)."""
    globs = list(TRUST_SURFACE_FLOOR)
    p = os.path.join(repo, CLASS_PATH.replace("/", os.sep))
    try:
        with open(p, encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip().replace("\\", "/")
                if line and line not in globs:
                    globs.append(line)
    except OSError:
        pass
    return globs


def in_class(repo_rel_posix, globs=None):
    globs = globs if globs is not None else list(TRUST_SURFACE_FLOOR)
    rel = repo_rel_posix.replace("\\", "/")
    while rel.startswith("./"):  # NOT lstrip("./"): that strips leading DOTS too
        rel = rel[2:]            # (.gitattributes -> gitattributes; round-7 catch)
    return any(_glob_to_re(g).search(rel) for g in globs)


def class_members(repo, globs=None):
    """(tracked_paths, untracked_globs): tracked class members from git ls-files --
    the working tree is NOT walked (an untracked file claiming a class path is not a
    member of anything; its existence is the doctor's (a) finding)."""
    globs = globs or load_class(repo)
    rc, out = _git_text(repo, "ls-files", "-z")
    tracked = []
    if rc == 0:
        for p in out.split("\0"):
            if p and in_class(p, globs):
                tracked.append(p)
    return sorted(tracked), [g for g in globs if g in UNTRACKED_MEMBERS]


ABSENT_MODE_NOTE = ("no settled authority mode recorded -- migration-only warn: HUMAN-GATE "
                    "consumers accept + surface as before, RETIREMENT IS DISABLED until "
                    "project.yaml records trust_surface_signing: visible or required (ADR #11 "
                    "condition 4 as amended 2026-08-22; MIGRATION v3.0.48 -> v3.0.49)")


def mode_chosen(repo):
    """True when project.yaml RECORDS a SETTLED authority mode: `visible` or `required`.
    `warn` -- absent OR written -- is migration-only compatibility, never a choice (the
    amended condition 4, binding item 1; cross-vendor round-1 catch: an explicitly written
    `warn` must not enable retirement either)."""
    p = os.path.join(repo, "project.yaml")
    try:
        text = open(p, encoding="utf-8-sig").read()
    except OSError:
        return False
    return re.search(r'(?m)^\s*trust_surface_signing:\s*"?(required|visible)"?\s*(#.*)?$',
                     text, re.I) is not None


def signing_mode(repo):
    """project.yaml trust_surface_signing: visible | required | warn. ABSENT -> warn, but
    only as existing-instance compatibility (ABSENT_MODE_NOTE): consumers keep the
    v3.0.46 warn behavior, retirement is disabled, and the doctor/sweep name the missing
    choice. An unrecognized value fails CLOSED to required."""
    p = os.path.join(repo, "project.yaml")
    try:
        text = open(p, encoding="utf-8-sig").read()
    except OSError:
        return "warn", "project.yaml absent -- " + ABSENT_MODE_NOTE
    m = re.search(r'(?m)^\s*trust_surface_signing:\s*"?([A-Za-z_-]+)"?', text)
    if not m:
        return "warn", ABSENT_MODE_NOTE
    v = m.group(1).lower()
    if v in ("warn", "required", "visible"):
        # `visible` (v3.0.47, backlog v3.0-135 mechanics; default flip stays T1): no
        # signature required, nothing warned -- committed-identity, the append-only
        # journal, the first-parent reader and the rewind detector still run as SENSORS.
        return v, "project.yaml trust_surface_signing: %s" % v
    return "required", ("project.yaml trust_surface_signing has unrecognized value %r "
                        "-- failing closed to required" % m.group(1))


def production_branch(repo):
    """v3.0.52 (v3.0-151, fleet inbox #8): THE production branch, resolved once -- one
    home for every Release-2/3 consumer (retire.py, promote.py, pending.py, doctor 16(f),
    every echoed command). Resolution order: the project.yaml `production_branch:` key;
    else the checked-out branch (`symbolic-ref HEAD`, the same rule branch_rewind uses);
    else None. Callers REFUSE on None rather than assuming `main`: silently defaulting a
    branch that does not resolve is how the first production fork froze a legacy citation
    registry with null view hashes (inbox #8) and how doctor 16(f) false-FAILed on every
    non-main instance. Returns (branch_or_None, note)."""
    p = os.path.join(repo, "project.yaml")
    try:
        text = open(p, encoding="utf-8-sig").read()
    except OSError:
        text = ""
    m = re.search(r'(?m)^\s*production_branch:\s*"?([A-Za-z0-9._/-]+)"?\s*(#.*)?$', text)
    if m:
        return m.group(1), "project.yaml production_branch: %s" % m.group(1)
    rc, out = _git_text(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    if rc == 0 and out.strip():
        return out.strip(), "checked-out branch (symbolic-ref HEAD)"
    return None, ("no production_branch key in project.yaml and HEAD names no branch "
                  "(detached or not a checkout) -- pass --branch, or set "
                  "`production_branch:` in project.yaml")


def resolve_branch(repo, branch=None):
    """The one entry point consumers call: an explicit branch wins; else
    production_branch(); an unresolvable branch raises TrustError with the note --
    never a silent `main` (v3.0-151)."""
    if branch:
        return branch
    b, note = production_branch(repo)
    if not b:
        raise TrustError("production branch unresolvable: %s" % note)
    return b


# ------------------------------------------------------------------ allowed_signers
_KEYTYPE_RE = re.compile(r"^(sk-)?(ssh-|ecdsa-)[A-Za-z0-9@.\-]+$")


def parse_allowed_signers(text):
    """Rows of {principals, options, keytype, key, comment, sk, line}. Tolerates the
    optional options field (e.g. namespaces="git") the way ssh-keygen does."""
    rows = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        toks = line.split()
        if len(toks) < 3:
            continue
        idx = next((i for i, t in enumerate(toks[1:], 1) if _KEYTYPE_RE.match(t)), None)
        if idx is None or idx + 1 >= len(toks):
            continue
        rows.append({"principals": toks[0].split(","),
                     "options": " ".join(toks[1:idx]),
                     "keytype": toks[idx], "key": toks[idx + 1],
                     "comment": " ".join(toks[idx + 2:]),
                     "sk": toks[idx] in SK_TYPES, "line": line})
    return rows


def _filtered_signers(text, accept_types):
    """allowed_signers content reduced to keys whose type is accepted. Non-sk keys are
    not 'warned about' -- they are simply not there when ssh-keygen verifies."""
    return "\n".join(r["line"] for r in parse_allowed_signers(text)
                     if r["keytype"] in accept_types) + "\n"


# ------------------------------------------------------------------ SSHSIG parsing
def _ssh_string(buf, off):
    (n,) = struct.unpack(">I", buf[off:off + 4])
    return buf[off + 4:off + 4 + n], off + 4 + n


def parse_sshsig(sig_text):
    """{'keytype', 'pubkey_b64', 'namespace', 'hash'} from an armored SSHSIG, or raises."""
    m = re.search(r"-----BEGIN SSH SIGNATURE-----(.*?)-----END SSH SIGNATURE-----",
                  sig_text, re.S)
    if not m:
        raise TrustError("not an SSH signature")
    blob = base64.b64decode("".join(m.group(1).split()))
    if blob[:6] != b"SSHSIG":
        raise TrustError("bad SSHSIG magic")
    off = 6 + 4  # magic + version
    pub, off = _ssh_string(blob, off)
    ns, off = _ssh_string(blob, off)
    _res, off = _ssh_string(blob, off)
    halg, off = _ssh_string(blob, off)
    keytype, _ = _ssh_string(pub, 0)
    return {"keytype": keytype.decode("ascii", "replace"),
            "pubkey_b64": base64.b64encode(pub).decode("ascii"),
            "namespace": ns.decode("utf-8", "replace"),
            "hash": halg.decode("ascii", "replace")}


def _verify_sig(payload, sig_text, signers_text, namespace=SSH_NAMESPACE):
    """ssh-keygen -Y verify of `payload` against the sk-filtered pin.
    Returns {'ok', 'principal', 'keytype', 'reason'}; never raises on a bad signature."""
    accept = _ACCEPT_TYPES
    try:
        info = parse_sshsig(sig_text)
    except (TrustError, struct.error, ValueError) as e:
        return {"ok": False, "principal": None, "keytype": None,
                "reason": "unparseable signature: %s" % e}
    if info["keytype"] not in accept:
        return {"ok": False, "principal": None, "keytype": info["keytype"],
                "reason": "signing key type %s is not presence-requiring (accepted: %s) "
                          "-- signature UNTRUSTED" % (info["keytype"], ", ".join(sorted(accept)))}
    if info["namespace"] != namespace:
        return {"ok": False, "principal": None, "keytype": info["keytype"],
                "reason": "signature namespace %r is not %r" % (info["namespace"], namespace)}
    filtered = _filtered_signers(signers_text, accept)
    if not filtered.strip():
        return {"ok": False, "principal": None, "keytype": info["keytype"],
                "reason": "pin lists no presence-requiring key"}
    if shutil.which("ssh-keygen") is None:
        return {"ok": False, "principal": None, "keytype": info["keytype"],
                "reason": "ssh-keygen not on PATH -- cannot verify (fail closed)"}
    td = tempfile.mkdtemp(prefix="trust-verify-")
    try:
        sig_p = os.path.join(td, "sig")
        signers_p = os.path.join(td, "signers")
        with open(sig_p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(sig_text if sig_text.endswith("\n") else sig_text + "\n")
        with open(signers_p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(filtered)
        p = subprocess.run(["ssh-keygen", "-Y", "find-principals", "-s", sig_p,
                            "-f", signers_p], capture_output=True)
        principals = [l.strip() for l in p.stdout.decode("utf-8", "replace").splitlines()
                      if l.strip()]
        if p.returncode != 0 or not principals:
            return {"ok": False, "principal": None, "keytype": info["keytype"],
                    "reason": "signing key is not in the pinned presence-requiring set"}
        for pr in principals:
            v = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", signers_p, "-I", pr,
                                "-n", namespace, "-s", sig_p], input=payload,
                               capture_output=True)
            if v.returncode == 0:
                return {"ok": True, "principal": pr, "keytype": info["keytype"],
                        "reason": "verified (%s, %s)" % (pr, info["keytype"])}
        return {"ok": False, "principal": principals[0], "keytype": info["keytype"],
                "reason": "signature does not verify for pinned principal %s" % principals[0]}
    finally:
        shutil.rmtree(td, ignore_errors=True)


# ------------------------------------------------------------------ objects + signatures
def commit_signature(repo, commit):
    """(payload_bytes, sig_text|None) for a commit object: the buffer git signed is the
    commit with its gpgsig header removed."""
    rc, raw, _ = _git(repo, "cat-file", "commit", commit)
    if rc != 0:
        raise TrustError("not a commit: %s" % commit)
    head, sep, body = raw.partition(b"\n\n")
    lines = head.split(b"\n")
    kept, sig_lines, in_sig = [], [], False
    for l in lines:
        if l.startswith(b"gpgsig "):
            in_sig = True
            sig_lines.append(l[len(b"gpgsig "):])
            continue
        if in_sig and l.startswith(b" "):
            sig_lines.append(l[1:])
            continue
        in_sig = False
        kept.append(l)
    payload = b"\n".join(kept) + sep + body
    sig = b"\n".join(sig_lines).decode("utf-8", "replace") if sig_lines else None
    return payload, sig


def tag_object(repo, tag):
    """The annotated tag object refs/tags/<tag> points at: {'sha','object','type','tag',
    payload, sig} or raises (lightweight tag / missing ref / not a tag object)."""
    rc, out = _git_text(repo, "rev-parse", "--verify", "--quiet", "refs/tags/%s" % tag)
    if rc != 0:
        raise TrustError("tag ref refs/tags/%s does not exist" % tag)
    sha = out.strip()
    rc, t = _git_text(repo, "cat-file", "-t", sha)
    if rc != 0 or t.strip() != "tag":
        raise TrustError("refs/tags/%s is not an annotated tag object (lightweight tags "
                         "carry no signature)" % tag)
    rc, raw, _ = _git(repo, "cat-file", "tag", sha)
    marker = b"-----BEGIN SSH SIGNATURE-----"
    i = raw.find(marker)
    payload, sig = (raw[:i], raw[i:].decode("utf-8", "replace")) if i >= 0 else (raw, None)
    hdr = {}
    for l in payload.split(b"\n\n", 1)[0].split(b"\n"):
        k, _, v = l.partition(b" ")
        hdr.setdefault(k.decode(), v.decode("utf-8", "replace"))
    return {"sha": sha, "object": hdr.get("object"), "type": hdr.get("type"),
            "tag": hdr.get("tag"), "payload": payload, "sig": sig}


# ------------------------------------------------------------------ the pin chain
def pin_at(repo, commit):
    """allowed_signers content at <commit>, or None when the file is absent there."""
    b = _blob_at(repo, commit, PIN_PATH)
    return b.decode("utf-8", "replace") if b is not None else None


_CHAIN_CACHE = {}


def verify_pin_chain(repo, commit):
    """Every commit in <commit>'s history touching the pin is signed by a key its PARENT's
    pin lists (bootstrap: the first such commit, signed by a key it lists itself).
    Returns {'ok', 'reason', 'bootstrap', 'links'}."""
    key = (os.path.abspath(repo), commit, _ACCEPT_TYPES)
    if key in _CHAIN_CACHE:
        return _CHAIN_CACHE[key]
    result = _verify_pin_chain(repo, commit)
    _CHAIN_CACHE[key] = result
    return result


def _verify_pin_chain(repo, commit):
    # FULL history (cross-vendor round-1 catch, 2026-08-21): the default path-simplified
    # walk follows the TREESAME parent of a merge, so a merge of an UNRELATED history
    # carrying its own self-vouching pin could pivot the chain onto an attacker's key.
    # Rule: every pin-touching commit in the full ancestry is verified, and there must
    # be EXACTLY ONE bootstrap (a commit introducing the pin over a pin-less parent).
    rc, out = _git_text(repo, "rev-list", "--reverse", "--full-history", commit, "--", PIN_PATH)
    if rc != 0:
        return {"ok": False, "reason": "cannot list pin history", "bootstrap": None, "links": []}
    touching = [l.strip() for l in out.splitlines() if l.strip()]
    if not touching:
        return {"ok": False, "reason": "no allowed_signers in history (no bootstrap commit)",
                "bootstrap": None, "links": []}
    if pin_at(repo, commit) is None:
        return {"ok": False, "reason": "allowed_signers absent at %s (pin deleted)" % commit[:12],
                "bootstrap": touching[0], "links": []}
    links, bootstraps = [], []
    for c in touching:
        parents = _parents(repo, c) or []
        here = pin_at(repo, c)
        if here is None:
            return {"ok": False, "reason": "pin DELETED in commit %s" % c[:12],
                    "bootstrap": bootstraps[0] if bootstraps else None, "links": links}
        parent_pins = [pin_at(repo, p) for p in parents]
        if len(parents) > 1:
            # a merge may only CARRY a pin that one of its parents already had
            # (that parent's own link is verified on its own turn); a pin that differs
            # from every parent is a change made BY the merge -- refused.
            if here not in parent_pins:
                return {"ok": False, "reason": "pin changed in MERGE commit %s -- a signature never "
                        "vouches for history it does not name" % c[:12],
                        "bootstrap": bootstraps[0] if bootstraps else None, "links": links}
            links.append({"commit": c, "ok": True, "principal": None, "keytype": None,
                          "against": "merge carries a parent's pin"})
            continue
        parent_pin = parent_pins[0] if parents else None
        if parent_pin is None:
            bootstraps.append(c)
            if len(bootstraps) > 1:
                return {"ok": False, "reason": "pin introduced a SECOND time in commit %s (first at "
                        "%s): a re-creation or an unrelated history merged in -- not the bootstrap"
                        % (c[:12], bootstraps[0][:12]), "bootstrap": bootstraps[0], "links": links}
            signers = here  # bootstrap: self-vouching, one operator touch
        elif parent_pin == here:
            continue  # listed by --full-history but unchanged here: nothing to verify
        else:
            signers = parent_pin
        payload, sig = commit_signature(repo, c)
        if not sig:
            return {"ok": False, "reason": "pin commit %s is UNSIGNED" % c[:12],
                    "bootstrap": bootstraps[0] if bootstraps else None, "links": links}
        v = _verify_sig(payload, sig, signers)
        links.append({"commit": c, "ok": v["ok"], "principal": v["principal"],
                      "keytype": v["keytype"], "against": "self (bootstrap)" if parent_pin is None
                      else "parent pin"})
        if not v["ok"]:
            return {"ok": False, "reason": "pin commit %s: %s (verified against the %s)"
                    % (c[:12], v["reason"], "bootstrap commit's own pin" if parent_pin is None
                       else "PARENT commit's pin"),
                    "bootstrap": bootstraps[0] if bootstraps else None, "links": links}
    return {"ok": True, "reason": "chain of %d pin commit(s) verified back to the single bootstrap %s"
            % (len(touching), bootstraps[0][:12]), "bootstrap": bootstraps[0], "links": links}


def pin_status(repo, rev="HEAD"):
    """Doctor (d): keys listed in the pin at <rev>, typed; non-sk keys are a FAIL."""
    text = pin_at(repo, rev)
    if text is None:
        return {"present": False, "keys": [], "non_sk": [], "chain": None}
    rows = parse_allowed_signers(text)
    chain = verify_pin_chain(repo, _rev(repo, rev) or rev)
    return {"present": True, "keys": rows,
            "non_sk": [r for r in rows if not r["sk"]], "chain": chain}


# ------------------------------------------------------------------ primitives
def _lf(b):
    return b.replace(b"\r\n", b"\n")


def committed_identical(repo, path, rev="HEAD", content=None):
    """Tracked AND the RAW working-tree bytes == HEAD's blob (line endings excepted).
    Raw bytes on purpose (cross-vendor round-6 catch): `git diff` honours clean filters
    and attribute conversions, so an agent could add a .gitattributes filter that makes
    a forged file LOOK clean; the consumers read the raw file, so the raw file is what
    must equal HEAD. CRLF/LF is the only tolerated difference -- it cannot alter content.
    `content`, when given, is compared INSTEAD of re-reading the file (round-12 catch: a
    consumer that parsed bytes and a gate that re-read the file could be swapped between;
    now the gate hands the verified blob back and the consumer parses THAT).
    Returns (ok, reason, blob)."""
    rel = path.replace("\\", "/")
    rc, _ = _git_text(repo, "ls-files", "--error-unmatch", "--", rel)
    if rc != 0:
        return False, "%s is not tracked by git (exists-but-uncommitted is not committed)" % rel, None
    blob = _blob_at(repo, rev, rel)
    if blob is None:
        return False, "%s is staged but absent from %s" % (rel, rev), None
    if content is None:
        try:
            with open(os.path.join(repo, rel.replace("/", os.sep)), "rb") as fh:
                raw = fh.read()
        except OSError as e:
            return False, "%s unreadable: %s" % (rel, e), None
    else:
        raw = content
    if raw != blob and _lf(raw) != _lf(blob):
        rc2, stat = _git_text(repo, "diff", "--stat", rev, "--", rel)
        summary = " ".join(stat.split()) or ("raw bytes differ (git diff reports clean -- a "
                                              "clean filter or attribute conversion is in play)")
        return False, "%s differs from HEAD (uncommitted perimeter change): %s" % (rel, summary), None
    return True, "%s is HEAD-identical (%s)" % (rel, rev[:12] if rev != "HEAD" else "HEAD"), blob


def newest_commit(repo, path, rev="HEAD"):
    rc, out = _git_text(repo, "log", "-1", "--format=%H%x09%an%x09%cI", rev, "--",
                        path.replace("\\", "/"))
    if rc != 0 or not out.strip():
        return None
    sha, an, date = (out.strip().split("\t") + ["", ""])[:3]
    return {"commit": sha, "author": an, "date": date}


def operator_signed(repo, path, rev="HEAD"):
    """The newest commit touching `path` (in rev's history) is operator-signed.
    Verified against the pin AT THAT COMMIT (its PARENT's pin when the path is the pin
    itself) after the pin's own chain verifies. Returns a dict with 'ok' and 'reason'."""
    rel = path.replace("\\", "/")
    nc = newest_commit(repo, rel, rev)
    if not nc:
        return {"ok": False, "reason": "%s has no commit in history" % rel, "commit": None,
                "author": None, "date": None, "principal": None, "keytype": None}
    c = nc["commit"]
    res = dict(nc, ok=False, principal=None, keytype=None)
    chain = verify_pin_chain(repo, c)
    if not chain["ok"]:
        res["reason"] = "pin chain not verified at %s: %s" % (c[:12], chain["reason"])
        return res
    if rel == PIN_PATH:
        # the chain already verified this commit against its parent's pin
        link = next((l for l in chain["links"] if l["commit"] == c), None)
        if link and link["ok"]:
            res.update(ok=True, principal=link["principal"], keytype=link["keytype"],
                       reason="pin commit verified against the parent pin (%s)" % link["principal"])
        else:
            res["reason"] = "pin commit %s not in the verified chain" % c[:12]
        return res
    signers = pin_at(repo, c)
    try:
        payload, sig = commit_signature(repo, c)
    except TrustError as e:
        res["reason"] = str(e)
        return res
    if not sig:
        res["reason"] = "commit %s touching %s is UNSIGNED" % (c[:12], rel)
        return res
    v = _verify_sig(payload, sig, signers or "")
    res.update(ok=v["ok"], principal=v["principal"], keytype=v["keytype"],
               reason=("commit %s: %s" % (c[:12], v["reason"])))
    return res


def operator_tag(repo, tag, commit):
    """refs/tags/<tag> is a signed annotated tag object, embedded name == tag, object ==
    commit exactly, signature by a pinned sk key (pin at the commit, chain-verified)."""
    try:
        t = tag_object(repo, tag)
    except TrustError as e:
        return {"ok": False, "reason": str(e), "tag_sha": None, "principal": None}
    full = _rev(repo, commit)
    if full is None:
        return {"ok": False, "reason": "commit %s does not resolve" % commit, "tag_sha": t["sha"],
                "principal": None}
    if t["tag"] != tag:
        return {"ok": False, "reason": "tag object's embedded name is %r, ref name is %r -- "
                "a re-pointed/replayed tag ref" % (t["tag"], tag), "tag_sha": t["sha"],
                "principal": None}
    if t["type"] != "commit" or t["object"] != full:
        return {"ok": False, "reason": "tag %s names %s %s, not commit %s" % (
            tag, t["type"], (t["object"] or "?")[:12], full[:12]), "tag_sha": t["sha"],
            "principal": None}
    if not t["sig"]:
        return {"ok": False, "reason": "tag %s is UNSIGNED" % tag, "tag_sha": t["sha"],
                "principal": None}
    chain = verify_pin_chain(repo, full)
    if not chain["ok"]:
        return {"ok": False, "reason": "pin chain not verified at %s: %s" % (full[:12],
                chain["reason"]), "tag_sha": t["sha"], "principal": None}
    v = _verify_sig(t["payload"], t["sig"], pin_at(repo, full) or "")
    return {"ok": v["ok"], "reason": "tag %s -> %s: %s" % (tag, full[:12], v["reason"]),
            "tag_sha": t["sha"], "principal": v["principal"], "keytype": v["keytype"],
            "commit": full}


def promotion_tag(repo, tag, commit):
    """v3.0.50 (ADR #11 condition 4 as amended, binding item 3): the visible-mode
    PROMOTION RECORD. refs/tags/<tag> is an annotated tag object, embedded name == tag,
    object == commit exactly, whose message carries `proposal_digest: sha256:<hex>` and
    `mode: visible`. NOT a signature check: the record is the trace of the operator's
    out-of-session action (deploy/promote.py); its authority is procedural and it is
    bound to the exact proposal by the digest the CALLER compares against the record in
    C's tree. Returns {'ok','reason','digest','tagger','tag_sha'}."""
    try:
        t = tag_object(repo, tag)
    except TrustError as e:
        return {"ok": False, "reason": str(e), "digest": None, "tagger": None, "tag_sha": None}
    full = _rev(repo, commit)
    if full is None:
        return {"ok": False, "reason": "commit %s does not resolve" % commit, "digest": None,
                "tagger": None, "tag_sha": t["sha"]}
    if t["tag"] != tag:
        return {"ok": False, "reason": "tag object's embedded name is %r, ref name is %r -- a "
                "re-pointed/replayed tag ref" % (t["tag"], tag), "digest": None, "tagger": None,
                "tag_sha": t["sha"]}
    if t["type"] != "commit" or t["object"] != full:
        return {"ok": False, "reason": "tag %s names %s %s, not commit %s" % (
            tag, t["type"], (t["object"] or "?")[:12], full[:12]), "digest": None,
            "tagger": None, "tag_sha": t["sha"]}
    hdr, _, msg = t["payload"].decode("utf-8", "replace").partition("\n\n")
    tagger = next((l[len("tagger "):] for l in hdr.split("\n") if l.startswith("tagger ")), None)
    dm = re.search(r"(?m)^proposal_digest:\s*(sha256:)?([0-9a-fA-F]{64})\s*$", msg)
    mm = re.search(r"(?m)^mode:\s*visible\s*$", msg)
    if not dm or not mm:
        return {"ok": False, "reason": "tag %s is not a promotion record (message must carry "
                "`proposal_digest: sha256:<64 hex>` and `mode: visible`)" % tag,
                "digest": None, "tagger": tagger, "tag_sha": t["sha"]}
    return {"ok": True, "reason": "promotion record %s -> %s (digest %s.., tagger %s)" % (
        tag, full[:12], dm.group(2)[:12], (tagger or "?").split(">")[0] + ">"),
        "digest": dm.group(2).lower(), "tagger": tagger, "tag_sha": t["sha"], "commit": full}


def publication_authority(repo, tag, commit, mode=None):
    """The ONE seam (v3.0.50). A verified operator tag publishes under either CHOSEN mode;
    under `visible` a promotion record does too. `warn`/absent never reaches here
    (check_publishable refuses on mode_chosen first) but is refused again defensively.
    Returns the operator_tag / promotion_tag result plus 'kind': 'signed' | 'promoted'."""
    if mode is None:
        mode = signing_mode(repo)[0]
    sig = operator_tag(repo, tag, commit)
    if sig["ok"]:
        return dict(sig, kind="signed")
    if mode == "visible":
        pr = promotion_tag(repo, tag, commit)
        if pr["ok"]:
            return dict(pr, kind="promoted")
        return {"ok": False, "kind": None, "commit": commit,
                "reason": "%s; and as a visible-mode promotion record: %s" % (sig["reason"], pr["reason"])}
    reason = sig["reason"]
    if mode == "required":
        reason += " (trust_surface_signing: required -- only a verified sk tag publishes)"
    return {"ok": False, "kind": None, "commit": commit, "reason": reason}


# ------------------------------------------------------------------ retirement publication
_RETIRE_TAG_RE = re.compile(r"^retire/(\d+)$")


def _digest_hex(s):
    s = (s or "").strip()
    return s[7:].lower() if s.lower().startswith("sha256:") else s.lower()


def _retire_records_in_tree(repo, rev):
    """[(path, record_dict)] for every receipts/journal/*.json with run_type retire at rev."""
    rc, out = _git_text(repo, "ls-tree", "-r", "--name-only", rev, "--", JOURNAL_DIR)
    found = []
    if rc != 0:
        return found
    for p in out.splitlines():
        p = p.strip()
        if not p.endswith(".json"):
            continue
        b = _blob_at(repo, rev, p)
        try:
            rec = json.loads(b.decode("utf-8-sig"))
        except Exception:
            continue
        if isinstance(rec, dict) and rec.get("run_type") == "retire":
            found.append((p, rec))
    return found


def _retire_records_history(repo, rev):
    """Every retire record ever ADDED on rev's FIRST-PARENT chain: [(path, commit, rec)],
    tip-most first. History, not the tip tree (cross-vendor round-8 catch: a deleted
    published record must still consume its digest and seq). Dedup by (path, sha) only --
    the same path can be a retire record at several commits (round 9)."""
    # --diff-filter=AM (cross-vendor round-9 catch): a pre-existing non-retire journal file
    # MODIFIED into a retire record must be seen at the commit that made it one.
    # --no-renames (round-10 catch): rename detection would classify a renamed-and-modified
    # file as R and hide it from the AM filter; without it a rename is a delete + an add.
    rc, out = _git_text(repo, "log", "--first-parent", "--no-renames", "--diff-filter=AM",
                        "--name-only", "--format=%x00%H", rev, "--", JOURNAL_DIR)
    found = []
    if rc != 0:
        return found
    for block in out.split("\x00"):
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        sha, paths = lines[0], lines[1:]
        for q in paths:
            if not q.endswith(".json"):
                continue
            b = _blob_at(repo, sha, q)
            try:
                rec = json.loads(b.decode("utf-8-sig")) if b is not None else None
            except Exception:
                rec = None
            if isinstance(rec, dict) and rec.get("run_type") == "retire":
                found.append((q, sha, rec))
    return found


def _journal_path_used(repo, rev, path):
    """True when `path` was ever added/modified/deleted on rev's FIRST-PARENT chain
    (append-only journal, round-11 catch: a pathname once used may never be reused)."""
    rc, out = _git_text(repo, "log", "--first-parent", "--no-renames", "--diff-filter=AMD",
                        "--format=%H", rev, "--", path)
    return rc == 0 and bool(out.strip())


def check_publishable(repo, tag, branch="main"):
    """Brief section 5's publication conditions + G2 replay/stale cases. Returns
    {'ok','reason','commit','head','record'}; never writes."""
    m = _RETIRE_TAG_RE.match(tag)
    if not m:
        return {"ok": False, "reason": "tag %r is not of the form retire/<seq>" % tag}
    seq = int(m.group(1))
    # Amended condition 4 (v3.0.48): absence of a recorded authority mode never
    # authorizes retirement -- refuse BEFORE any signature is consulted.
    if not mode_chosen(repo):
        return {"ok": False, "reason": "retirement disabled: " + ABSENT_MODE_NOTE}
    try:
        t = tag_object(repo, tag)
    except TrustError as e:
        reason = str(e)
        if signing_mode(repo)[0] == "visible":
            reason += (" (trust_surface_signing: visible -- nothing is promoted yet: run "
                       "`py deploy/promote.py <proposal-digest> --branch %s` from your "
                       "own terminal)" % branch)
        return {"ok": False, "reason": reason}
    c = t["object"]
    head = _rev(repo, "refs/heads/%s" % branch)
    if head is None:
        return {"ok": False, "reason": "production branch %s does not resolve" % branch}
    mode = signing_mode(repo)[0]
    sig = publication_authority(repo, tag, c, mode)
    if not sig["ok"]:
        reason = sig["reason"]
        if mode == "visible":
            reason += (" (trust_surface_signing: visible -- publication is the exact-digest "
                       "operator promotion: `py deploy/promote.py <proposal-digest> "
                       "--branch %s` from your own terminal, or a verified operator tag)"
                       % branch)
        return {"ok": False, "reason": reason, "commit": c, "head": head}
    parents = _parents(repo, c) or []
    if len(parents) != 1:
        return {"ok": False, "reason": "C %s has %d parents -- must be a single commit (no "
                "merge ancestry)" % (c[:12], len(parents)), "commit": c, "head": head}
    if parents[0] != head:
        return {"ok": False, "reason": "C %s is not a single commit atop the production head "
                "%s (parent is %s): extra commits, or a STALE proposal (the branch moved "
                "after the proposal was minted) -- re-prepare against the current head"
                % (c[:12], head[:12], parents[0][:12]), "commit": c, "head": head}
    # C must introduce EXACTLY ONE retire record over the production head (cross-vendor
    # round-2 catch: validating only the record whose seq matches the tag let a second
    # record ride inside the signed C). The tag vouches for one retirement, not a batch
    # of records -- a batch is one record naming many spans.
    # Append-only journal, enforced (round-9 catch: a pre-existing non-retire journal file
    # modified INTO a retire record is not an introduction): C's delta under
    # receipts/journal must be exactly ONE ADDED file, and that file must be the record.
    rc, ns = _git_text(repo, "diff-tree", "--no-commit-id", "--name-status", "-r", head, c,
                       "--", JOURNAL_DIR)
    delta = [l.split("\t", 1) for l in ns.splitlines() if "\t" in l]
    if len(delta) != 1 or delta[0][0] != "A":
        return {"ok": False, "reason": "C's journal delta over the production head must be exactly "
                "one ADDED record (append-only journal); got: %s" % (
                    ", ".join("%s %s" % (st, q) for st, q in delta) or "nothing"),
                "commit": c, "head": head}
    rec_path = delta[0][1]
    rec_blob = _blob_at(repo, c, rec_path)
    try:
        rec = json.loads(rec_blob.decode("utf-8-sig")) if rec_blob else None
    except Exception:
        rec = None
    if not isinstance(rec, dict) or rec.get("run_type") != "retire":
        return {"ok": False, "reason": "the file C adds under the journal (%s) is not a retire "
                "record" % rec_path, "commit": c, "head": head}
    if _journal_path_used(repo, head, rec_path):
        return {"ok": False, "reason": "journal path %s was already used on %s -- the journal is "
                "append-only; a pathname is never reused" % (rec_path, branch), "commit": c,
                "head": head}
    if rec.get("seq") != seq:
        return {"ok": False, "reason": "the retire record C introduces (%s) carries seq %r, not the "
                "tag's %d" % (rec_path, rec.get("seq"), seq), "commit": c, "head": head}
    if rec.get("tag") != tag:
        return {"ok": False, "reason": "retire record %s names tag %r, not %r" % (
            rec_path, rec.get("tag"), tag), "commit": c, "head": head}
    prop = rec.get("proposal")
    blob = _blob_at(repo, c, prop) if prop else None
    if blob is None:
        return {"ok": False, "reason": "retire record's proposal artifact %r is not in C's tree"
                % prop, "commit": c, "head": head}
    digest = hashlib.sha256(blob).hexdigest()
    if _digest_hex(rec.get("proposal_digest")) != digest:
        return {"ok": False, "reason": "retire record's proposal_digest does not equal the "
                "digest of %s in C's tree (record %s.., tree %s..)" % (
                    prop, _digest_hex(rec.get("proposal_digest"))[:12], digest[:12]),
                "commit": c, "head": head}
    if sig["kind"] == "promoted" and sig["digest"] != digest:
        return {"ok": False, "reason": "promotion record %s names proposal digest %s.. but C's "
                "record/proposal digest is %s.. -- the promotion is bound to a DIFFERENT proposal "
                "(exact-proposal binding)" % (tag, sig["digest"][:12], digest[:12]),
                "commit": c, "head": head}
    for p, sha, r in _retire_records_history(repo, head):  # history, not the tip tree
        if _digest_hex(r.get("proposal_digest")) == digest:
            return {"ok": False, "reason": "proposal digest already CONSUMED on %s by %s (introduced "
                    "at %s) -- a replayed proposal" % (branch, p, sha[:12]), "commit": c,
                    "head": head}
        if r.get("seq") == seq:
            return {"ok": False, "reason": "retire seq %d already introduced on %s (%s at %s)" % (
                seq, branch, p, sha[:12]), "commit": c, "head": head}
    who = ("verified by %s" % sig["principal"]) if sig["kind"] == "signed" else (
        "promoted (visible-mode record by %s)" % ((sig.get("tagger") or "?").split(">")[0] + ">"))
    return {"ok": True, "reason": "publishable: tag %s %s, C %s single commit atop "
            "%s, record %s digest-matched" % (tag, who, c[:12], head[:12], rec_path),
            "commit": c, "head": head, "record": rec_path, "kind": sig["kind"]}


def publish_retirement(repo, tag, branch="main"):
    """Fast-forward refs/heads/<branch> to C, atomic on the checked old head."""
    chk = check_publishable(repo, tag, branch)
    if not chk["ok"]:
        return chk
    rc, out = _git_text(repo, "update-ref", "refs/heads/%s" % branch, chk["commit"], chk["head"])
    if rc != 0:
        return {"ok": False, "reason": "update-ref refused (head moved?): %s" % out.strip(),
                "commit": chk["commit"], "head": chk["head"]}
    return dict(chk, published=True, reason="PUBLISHED: " + chk["reason"])


_BATCH_TAG_RE = re.compile(r"^retire/batch/(\d+-\d+)$")


def check_publishable_batch(repo, tag, branch="main"):
    """v3.0.52 (ADR #11 Release 3; the amended condition 4's 'one promote per batch'):
    the batch twin of check_publishable. `tag` = retire/batch/<first>-<last> names the
    BINDER commit M, whose tree adds exactly the batch manifest; ONE operator act on M
    (a visible-mode promotion record carrying the manifest digest, or a verified sk tag
    under required -- the v3.0.46 brief's own 'one tag can name a batch commit') vouches
    for PRECISELY the member chain the manifest names. Every single-retirement condition
    re-runs per member against its own parent; substitution, reordering, insertion,
    truncation, a consumed digest/seq, or a moved branch each refuse. Never writes."""
    m = _BATCH_TAG_RE.match(tag)
    if not m:
        return {"ok": False, "reason": "tag %r is not of the form retire/batch/<first>-<last>" % tag}
    bid = m.group(1)
    if not mode_chosen(repo):
        return {"ok": False, "reason": "retirement disabled: " + ABSENT_MODE_NOTE}
    head = _rev(repo, "refs/heads/%s" % branch)
    if head is None:
        return {"ok": False, "reason": "production branch %s does not resolve" % branch}
    try:
        t = tag_object(repo, tag)
    except TrustError as e:
        reason = str(e)
        if signing_mode(repo)[0] == "visible":
            reason += (" (trust_surface_signing: visible -- nothing is promoted yet: run "
                       "`py deploy/promote.py <batch-digest> --branch %s` from your own "
                       "terminal)" % branch)
        return {"ok": False, "reason": reason}
    M = t["object"]
    mpath = "deploy/rulings/retire-batch-%s/manifest.json" % bid
    mb = _blob_at(repo, M, mpath)
    if mb is None:
        return {"ok": False, "reason": "tagged commit %s carries no batch manifest %s"
                % ((M or "?")[:12], mpath), "commit": M, "head": head}
    digest = hashlib.sha256(mb).hexdigest()
    mode = signing_mode(repo)[0]
    sig = publication_authority(repo, tag, M, mode)
    if not sig["ok"]:
        reason = sig["reason"]
        if mode == "visible":
            reason += (" (visible -- the exact-digest batch promotion: `py deploy/"
                       "promote.py <batch-digest> --branch %s` from your own terminal)"
                       % branch)
        return {"ok": False, "reason": reason, "commit": M, "head": head}
    if sig["kind"] == "promoted" and sig["digest"] != digest:
        return {"ok": False, "reason": "promotion record %s names digest %s.. but the batch "
                "manifest in M hashes to %s.. -- bound to a DIFFERENT batch"
                % (tag, sig["digest"][:12], digest[:12]), "commit": M, "head": head}
    try:
        manifest = json.loads(mb.decode("utf-8-sig"))
    except Exception:
        return {"ok": False, "reason": "batch manifest unreadable", "commit": M, "head": head}
    rows = manifest.get("members") or []
    if not rows:
        return {"ok": False, "reason": "batch manifest names no members", "commit": M, "head": head}
    parents = _parents(repo, M) or []
    if len(parents) != 1:
        return {"ok": False, "reason": "M has %d parents" % len(parents), "commit": M, "head": head}
    rc, ns = _git_text(repo, "diff-tree", "--no-commit-id", "--name-status", "-r",
                       parents[0], M)
    delta = [l.split("\t", 1) for l in ns.splitlines() if "\t" in l]
    if delta != [["A", mpath]]:
        return {"ok": False, "reason": "M's delta over its parent is not exactly the batch "
                "manifest (got: %s)" % (", ".join("%s %s" % (s, q) for s, q in delta)
                                        or "nothing"), "commit": M, "head": head}
    seen_views = set()
    for _row in rows:
        v2 = str(_row.get("view", "")).replace("\\", "/")
        if v2 in seen_views:
            return {"ok": False, "reason": "batch manifest names view %s in TWO members -- "
                    "a batch names each view at most once (atomic-per-view)" % v2,
                    "commit": M, "head": head}
        seen_views.add(v2)
    chain, c = [], parents[0]
    for _row in rows:
        chain.append(c)
        ps = _parents(repo, c) or []
        if len(ps) != 1:
            return {"ok": False, "reason": "member commit %s is not single-parent" % c[:12],
                    "commit": M, "head": head}
        c = ps[0]
    chain.reverse()
    if c != head or manifest.get("parent_head") != head:
        return {"ok": False, "reason": "batch chain bottoms out at %s but %s is %s -- a "
                "STALE or PARTIALLY PUBLISHED batch; the remainder is refused (run "
                "`py deploy/retire.py --recover`, then re-propose)" % (
                    c[:12], branch, head[:12]), "commit": M, "head": head}
    hist = _retire_records_history(repo, head)
    members = []
    for row, ci in zip(rows, chain):
        if row.get("commit") != ci:
            return {"ok": False, "reason": "member seq %s: manifest names commit %s but the "
                    "chain carries %s -- member SUBSTITUTION" % (
                        row.get("seq"), str(row.get("commit"))[:12], ci[:12]),
                    "commit": M, "head": head}
        pi = (_parents(repo, ci) or [None])[0]
        rc, ns = _git_text(repo, "diff-tree", "--no-commit-id", "--name-status", "-r",
                           pi, ci, "--", JOURNAL_DIR)
        d2 = [l.split("\t", 1) for l in ns.splitlines() if "\t" in l]
        if len(d2) != 1 or d2[0][0] != "A":
            return {"ok": False, "reason": "member %s's journal delta is not exactly one "
                    "ADDED record" % ci[:12], "commit": M, "head": head}
        rec_path = d2[0][1]
        rb = _blob_at(repo, ci, rec_path)
        try:
            rec = json.loads(rb.decode("utf-8-sig")) if rb else None
        except Exception:
            rec = None
        if not isinstance(rec, dict) or rec.get("run_type") != "retire":
            return {"ok": False, "reason": "member %s adds %s which is not a retire record"
                    % (ci[:12], rec_path), "commit": M, "head": head}
        if (rec.get("batch") or {}).get("id") != bid or rec.get("seq") != row.get("seq"):
            return {"ok": False, "reason": "member record %s does not name batch %s / seq %s"
                    % (rec_path, bid, row.get("seq")), "commit": M, "head": head}
        # cross-vendor round-2 fold (c1): the record's view is BOUND to the manifest
        # row's -- a forged record claiming another member's view would otherwise make
        # the allowed-path pin below vouch for the wrong view
        if str(rec.get("view", "")).replace("\\", "/") != \
                str(row.get("view", "")).replace("\\", "/"):
            return {"ok": False, "reason": "member seq %s: record names view %r but the "
                    "manifest row names %r -- view substitution" % (
                        rec.get("seq"), rec.get("view"), row.get("view")),
                    "commit": M, "head": head}
        prop = rec.get("proposal")
        pb = _blob_at(repo, ci, prop) if prop else None
        if pb is None or "sha256:" + hashlib.sha256(pb).hexdigest() != row.get("proposal_digest")                 or _digest_hex(rec.get("proposal_digest")) != hashlib.sha256(pb).hexdigest():
            return {"ok": False, "reason": "member seq %s: proposal digest does not bind the "
                    "record, the blob and the manifest row together" % row.get("seq"),
                    "commit": M, "head": head}
        if _journal_path_used(repo, head, rec_path):
            return {"ok": False, "reason": "journal path %s was already used on %s -- never "
                    "reused" % (rec_path, branch), "commit": M, "head": head}
        for p2, sha2, r2 in hist:
            if _digest_hex(r2.get("proposal_digest")) == _digest_hex(rec.get("proposal_digest"))                     or r2.get("seq") == rec.get("seq"):
                return {"ok": False, "reason": "member seq %s: digest or seq already CONSUMED "
                        "on %s (%s at %s) -- a replayed member" % (
                            rec.get("seq"), branch, p2, sha2[:12]), "commit": M, "head": head}
        # v3.0.52 cross-vendor round-1 fold: THIS check, not its caller, pins per-view
        # atomicity -- a member commit may touch ONLY its own retirement's paths (its
        # view, record, proposal, cold objects, compaction index). Without it a member
        # could smuggle a change to a LATER member's view and a halted batch would leave
        # that view half-applied. verify_batch pins the same thing through
        # verify_prepared; the publisher must not lean on its caller having run it.
        rc, ns_all = _git_text(repo, "diff-tree", "--no-commit-id", "--name-only", "-r",
                               pi, ci)
        allowed = {rec.get("view"), rec.get("proposal"), rec_path}
        for co in rec.get("cold_objects") or []:
            allowed.add(co.get("path"))
        if rec.get("compaction") and rec["compaction"].get("index"):
            allowed.add(rec["compaction"]["index"])
        extra = [q.strip() for q in ns_all.splitlines()
                 if q.strip() and q.strip() not in allowed]
        if extra:
            return {"ok": False, "reason": "member seq %s touches paths outside its own "
                    "retirement (%s) -- atomic-per-view means one member, one view"
                    % (rec.get("seq"), ", ".join(extra[:5])), "commit": M, "head": head}
        members.append({"seq": rec.get("seq"), "commit": ci, "record": rec_path,
                        "view": rec.get("view"),
                        "digest": _digest_hex(rec.get("proposal_digest"))})
    who = ("verified by %s" % sig.get("principal")) if sig["kind"] == "signed" else (
        "promoted (visible-mode batch record by %s)" % ((sig.get("tagger") or "?").split(">")[0] + ">"))
    return {"ok": True, "reason": "publishable: batch %s %s, %d member(s) atop %s, manifest "
            "digest-matched" % (bid, who, len(members), head[:12]),
            "commit": M, "head": head, "members": members, "digest": digest, "bid": bid,
            "kind": sig["kind"]}


def publish_batch(repo, tag, branch="main", halt_after=None):
    """Member-by-member fast-forward -- ATOMIC PER VIEW: each update-ref lands one whole
    member commit (one view's complete retirement) or nothing; the binder M publishes
    last, only on a full batch. halt_after=K is the BRAKE's slow-down: the first K
    members publish, the remainder never does (the caller then discards the remainder's
    refs -- refused, never half-applied). Returns {'ok','published',[...],'halted'}."""
    chk = check_publishable_batch(repo, tag, branch)
    if not chk["ok"]:
        return chk
    todo = chk["members"] if halt_after is None else chk["members"][:max(0, int(halt_after))]
    published, cur = [], chk["head"]
    for mrow in todo:
        rc, out = _git_text(repo, "update-ref", "refs/heads/%s" % branch, mrow["commit"], cur)
        if rc != 0:
            return {"ok": False, "published": published, "halted": True,
                    "reason": "update-ref refused mid-batch at member seq %s (%s) -- %d/%d "
                              "published, each a complete view; the remainder is refused "
                              "(run `py deploy/retire.py --recover`)" % (
                                  mrow["seq"], (out or "").strip(), len(published),
                                  len(chk["members"])), "commit": cur, "head": chk["head"]}
        cur = mrow["commit"]
        published.append(mrow)
    halted = len(published) < len(chk["members"])
    binder_note = ""
    binder_published = False
    if not halted:
        rc, out = _git_text(repo, "update-ref", "refs/heads/%s" % branch, chk["commit"], cur)
        if rc == 0:
            cur = chk["commit"]
            binder_published = True
        else:
            # round-2 fold (c1): every member landed but the binder could not fast-
            # forward (a concurrent head move) -- REPORTED, never silently ok
            binder_note = (" -- NOTE: the batch BINDER did not publish (%s); every member "
                           "view is complete, but the manifest commit is not on the "
                           "branch: inspect and fast-forward it yourself, or leave it "
                           "(the pending list still reconstructs the members)"
                           % (out or "update-ref refused").strip())
    return dict(chk, ok=True, published=published, halted=halted, tip=cur,
                binder_published=binder_published,
                reason=("PUBLISHED %d/%d member(s)%s: %s%s" % (
                    len(published), len(chk["members"]),
                    " -- HALTED by the brake; the remainder is refused" if halted else
                    (" + the batch binder" if binder_published else ""),
                    chk["reason"], binder_note)))


def retire_records_status(repo, rev="HEAD"):
    """Doctor (e) / sweep: each retire record at rev with its publication status.

    No `git log` attribution (cross-vendor round-5 catch: path-simplified history follows
    the TREESAME parent of a merge, so a signed-but-STALE C merged into the advanced
    branch by an unsigned merge read as published). Instead: C comes from the tag object
    the record names; honest publication is a fast-forward, so C must lie on the branch's
    FIRST-PARENT chain; the record and its proposal at the branch tip must be byte-equal
    to C's; C must be a single-parent commit that introduced exactly this one record;
    and no ancestry-earlier record may have consumed the digest or seq."""
    out = []
    mode = signing_mode(repo)[0]
    if not mode_chosen(repo):
        mode = "warn"  # never `visible` by accident: a promotion record needs a CHOSEN mode
    all_recs = _retire_records_in_tree(repo, rev)
    rc, fp = _git_text(repo, "rev-list", "--first-parent", rev)
    first_parent = fp.split()
    fp_index = {c: i for i, c in enumerate(first_parent)}  # 0 = tip
    rows = {}
    for p, rec in all_recs:
        tag = rec.get("tag") or ("retire/%s" % rec.get("seq"))
        row = {"path": p, "seq": rec.get("seq"), "tag": tag, "commit": None, "published": False}
        rows[p] = row
        # v3.0.52 batch members: no per-member tag exists -- authority is the ONE
        # operator act on the binder M (tag retire/batch/<id>), which vouches for the
        # member through the manifest in M's tree. Everything else re-runs per member.
        b = rec.get("batch") or {}
        if b.get("id"):
            btag = b.get("tag") or ("retire/batch/%s" % b["id"])
            row["tag"] = btag
            try:
                t = tag_object(repo, btag)
            except TrustError as e:
                row["reason"] = "batch member awaiting the batch promotion: %s" % e
                continue
            M = t["object"]
            mb = _blob_at(repo, M, "deploy/rulings/retire-batch-%s/manifest.json" % b["id"])
            if mb is None:
                row["reason"] = "batch tag %s names %s which carries no manifest" % (btag, (M or "?")[:12])
                continue
            v = publication_authority(repo, btag, M, mode)
            if not v["ok"]:
                row["reason"] = v["reason"]
                continue
            if v["kind"] == "promoted" and v["digest"] != hashlib.sha256(mb).hexdigest():
                row["reason"] = "batch promotion bound to a different manifest"
                continue
            try:
                manifest = json.loads(mb.decode("utf-8-sig"))
            except Exception:
                row["reason"] = "batch manifest unreadable"
                continue
            mrow = next((r2 for r2 in manifest.get("members") or []
                         if r2.get("seq") == rec.get("seq")), None)
            if mrow is None or _digest_hex(mrow.get("proposal_digest")) !=                     _digest_hex(rec.get("proposal_digest")) or mrow.get("view") != rec.get("view"):
                row["reason"] = "record does not match its batch manifest row"
                continue
            c = mrow.get("commit")
            row["commit"] = c
            row["kind"] = "batch-" + v["kind"]
            if c not in fp_index:
                row["reason"] = ("batch member not on the first-parent chain -- prepared "
                                 "but never published (a halted batch's refused remainder "
                                 "looks exactly like this)")
                continue
            # the BINDER is deliberately NOT required on the chain (v3.0.52 stranger-run
            # catch): a brake-halted batch publishes members without it, and those
            # members are REAL publications -- reading them UNPUBLISHED would false-alarm
            # doctor 16(e) and the sweep on every halted batch. Authority is the verified
            # batch tag + the manifest row binding this member's exact commit, both
            # checked above; the fp-chain membership of the MEMBER is what publication
            # means.
            prop = rec.get("proposal") or ""
            if (_blob_at(repo, rev, p) != _blob_at(repo, c, p)
                    or _blob_at(repo, rev, prop) is None
                    or _blob_at(repo, rev, prop) != _blob_at(repo, c, prop)):
                row["reason"] = "record or proposal bytes at %s differ from the member commit" % rev
                continue
            rc, ns = _git_text(repo, "diff-tree", "--no-commit-id", "--name-status", "-r",
                               c + "^", c, "--", JOURNAL_DIR)
            if [l.split("\t", 1) for l in ns.splitlines() if "\t" in l] != [["A", p]]:
                row["reason"] = "member's journal delta is not exactly this one record"
                continue
            if _journal_path_used(repo, c + "^", p):
                row["reason"] = "journal path already used below the member commit"
                continue
            row["reason"] = "batch %s: %s" % (b["id"], v["reason"])
            row["_ok"] = True
            continue
        m = _RETIRE_TAG_RE.match(tag or "")
        if not m or int(m.group(1)) != rec.get("seq"):
            row["reason"] = "record's tag %r does not name its own seq %r" % (tag, rec.get("seq"))
            continue
        try:
            t = tag_object(repo, tag)
        except TrustError as e:
            row["reason"] = str(e)
            continue
        c = t["object"]
        row["commit"] = c
        if c not in fp_index:
            row["reason"] = ("tagged commit %s is not on the first-parent chain of %s -- it was "
                             "merged in, not published by fast-forward (a stale C brought in by "
                             "a merge looks exactly like this)" % ((c or "?")[:12], rev))
            continue
        v = publication_authority(repo, tag, c, mode)
        if not v["ok"]:
            row["reason"] = v["reason"]
            continue
        row["kind"] = v["kind"]
        if v["kind"] == "promoted" and v["digest"] != _digest_hex(rec.get("proposal_digest")):
            row["reason"] = ("promotion record names digest %s.. but the record carries %s.. -- "
                             "bound to a different proposal" % (v["digest"][:12],
                                                                _digest_hex(rec.get("proposal_digest"))[:12]))
            continue
        if len(_parents(repo, c) or []) != 1:
            row["reason"] = "tagged commit %s is not a single-parent commit" % c[:12]
            continue
        prop = rec.get("proposal") or ""
        if (_blob_at(repo, rev, p) != _blob_at(repo, c, p)
                or _blob_at(repo, rev, prop) is None
                or _blob_at(repo, rev, prop) != _blob_at(repo, c, prop)):
            row["reason"] = ("record or proposal bytes at %s differ from the tagged commit %s -- "
                             "the tag vouches only for %s's bytes" % (rev, c[:12], c[:12]))
            continue
        if hashlib.sha256(_blob_at(repo, c, prop)).hexdigest() != _digest_hex(
                rec.get("proposal_digest")):
            row["reason"] = "proposal_digest does not match the proposal blob in %s" % c[:12]
            continue
        # C's journal delta over its parent must be exactly this one ADDED file (round-9
        # catch: a modified-into-a-record file is not an introduction; append-only journal)
        rc, ns = _git_text(repo, "diff-tree", "--no-commit-id", "--name-status", "-r",
                           c + "^", c, "--", JOURNAL_DIR)
        delta = [l.split("\t", 1) for l in ns.splitlines() if "\t" in l]
        if delta != [["A", p]]:
            row["reason"] = ("tagged commit %s's journal delta is not exactly the addition of %s "
                             "(got: %s) -- the tag vouches for one appended record"
                             % (c[:12], p, ", ".join("%s %s" % (st, q) for st, q in delta) or "nothing"))
            continue
        if _journal_path_used(repo, c + "^", p):
            row["reason"] = ("journal path %s was already used below %s -- a pathname is never "
                             "reused (append-only journal)" % (p, c[:12]))
            continue
        row["reason"] = v["reason"]
        row["_ok"] = True
    # consumed digest / seq (round-4 catch, widened to HISTORY by round 8): among records
    # whose tag verified, only the one introduced DEEPEST on the first-parent chain
    # publishes -- and a record introduced on that chain but since DELETED still consumes.
    history = _retire_records_history(repo, rev)
    tip_paths = {p for p, _ in all_recs}
    for p, rec in all_recs:
        row = rows[p]
        if not row.pop("_ok", False):
            continue
        mine = fp_index[row["commit"]]
        dup = ["%s@%s" % (q, sha[:12]) for q, sha, r2 in history
               if not (q == p and sha == row["commit"]) and sha in fp_index
               and fp_index[sha] > mine and (
                   _digest_hex(r2.get("proposal_digest")) == _digest_hex(rec.get("proposal_digest"))
                   or r2.get("seq") == rec.get("seq"))]
        if dup:
            row["reason"] = ("proposal digest or seq already CONSUMED by the earlier record %s -- "
                             "a replayed proposal" % ", ".join(dup))
        else:
            row["published"] = True
    out = [rows[p] for p, _ in all_recs]
    seen = set()
    tip_ident = {(p, r.get("seq"), _digest_hex(r.get("proposal_digest"))) for p, r in all_recs}
    for p, sha, rec in history:
        ident = (p, rec.get("seq"), _digest_hex(rec.get("proposal_digest")))
        if ident in tip_ident or ident in seen:  # keyed on identity, not path (round 11)
            continue
        seen.add(ident)
        out.append({"path": p, "seq": rec.get("seq"), "tag": rec.get("tag"), "commit": sha,
                    "published": False,
                    "reason": "retire record DELETED from the branch after its introduction at %s "
                              "-- the journal is append-only; its digest/seq stay consumed" % sha[:12]})
    return out


# ------------------------------------------------------------------ consumer entry point
def branch_rewind(repo, branch=None):
    """Ref-movement detector (cross-vendor round-4 residual made visible): a same-user
    agent can force a signed-but-STALE retirement C onto the production branch, which
    discards the intervening history -- undetectable from the branch alone. Against the
    branch's remote-tracking ref it is a non-fast-forward. Returns a list of findings."""
    if branch is None:
        rc, out = _git_text(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
        branch = out.strip() if rc == 0 else None
    if not branch:
        return []
    rc, out = _git_text(repo, "for-each-ref", "--format=%(refname:short)", "refs/remotes")
    findings = []
    for r in out.split():
        if r.endswith("/" + branch):
            rc, _ = _git_text(repo, "merge-base", "--is-ancestor", r, branch)
            if rc != 0:
                findings.append("local %s is NOT a fast-forward of %s -- history rewound or "
                                "rewritten (a forced stale retirement looks exactly like this)"
                                % (branch, r))
    return findings


def gate_artifact(repo, path, mode=None, content=None):
    """HUMAN-GATE consumers call this FIRST and then parse the returned `blob` -- never the
    file on disk (round-12 catch: parse-then-gate left a swap window between the two
    reads). `content`: the bytes the caller already holds, compared instead of re-reading.
    committed_identical: always a refusal. operator_signed: refusal under `required`,
    a surfaced warning under `warn`.
    Returns {'ok', 'refuse', 'warnings', 'mode', 'signed', 'head', 'blob'}."""
    rel = path.replace("\\", "/")
    if mode is None:
        mode, _why = signing_mode(repo)
    # ONE HEAD snapshot for both checks (cross-vendor round-7 catch): resolved separately,
    # an agent could move HEAD between them so that no single commit satisfied both.
    head = _rev(repo, "HEAD")
    if head is None:
        return {"ok": False, "refuse": "trust-surface not committed-identical: HEAD does not "
                                       "resolve to a commit (nothing is committed yet)",
                "warnings": [], "mode": mode, "signed": None, "head": None}
    ok, reason, blob = committed_identical(repo, rel, rev=head, content=content)
    if not ok:
        return {"ok": False, "refuse": "trust-surface not committed-identical: " + reason,
                "warnings": [], "mode": mode, "signed": None, "head": head, "blob": None}
    s = operator_signed(repo, rel, rev=head)
    if s["ok"]:
        return {"ok": True, "refuse": None, "warnings": [], "mode": mode, "signed": s,
                "head": head, "blob": blob}
    if mode == "visible":
        return {"ok": True, "refuse": None, "warnings": [], "mode": mode, "signed": s,
                "head": head, "blob": blob}
    msg = "trust-surface %s is not operator-signed: %s" % (rel, s["reason"])
    if mode == "required":
        return {"ok": False, "refuse": msg + " (trust_surface_signing: required)",
                "warnings": [], "mode": mode, "signed": s, "head": head, "blob": None}
    return {"ok": True, "refuse": None, "mode": mode, "signed": s, "head": head, "blob": blob,
            "warnings": [msg + " -- ACCEPTED under trust_surface_signing: warn (cutover); "
                         "set required once the pin's bootstrap commit exists"]}


def report(repo, rev="HEAD"):
    """The class-wide table (sweep step 17, doctor 16 a/b/d/e)."""
    mode, why = signing_mode(repo)
    globs = load_class(repo)
    tracked, untracked = class_members(repo, globs)
    rows = []
    for p in tracked:
        ci_ok, ci_reason, _blob = committed_identical(repo, p)
        s = operator_signed(repo, p, rev)
        rows.append({"path": p, "head_identical": ci_ok, "head_reason": ci_reason,
                     "commit": (s.get("commit") or "")[:12], "author": s.get("author"),
                     "date": s.get("date"), "signed": s["ok"], "principal": s.get("principal"),
                     "keytype": s.get("keytype"), "reason": s["reason"]})
    return {"mode": mode, "mode_reason": why, "mode_chosen": mode_chosen(repo),
            "class": globs, "untracked_members": untracked,
            "pin": pin_status(repo, rev), "surfaces": rows,
            "retire_records": retire_records_status(repo, rev),
            "branch_rewind": branch_rewind(repo)}


def _print_report(rep):
    print("trust-surface report  (signing mode: %s -- %s)" % (rep["mode"], rep["mode_reason"]))
    pin = rep["pin"]
    if not pin["present"]:
        print("pin: core/security/hooks/allowed_signers ABSENT -- no operator can vouch yet "
              "(bootstrap ceremony pending)")
    else:
        ch = pin["chain"] or {}
        print("pin: %d key(s), %d non-presence (%s); chain: %s" % (
            len(pin["keys"]), len(pin["non_sk"]), "FAIL" if pin["non_sk"] else "ok",
            ch.get("reason")))
    print("%-44s %-5s %-12s %-18s %-6s %s" % ("surface", "HEAD=", "commit", "author", "signed",
                                              "detail"))
    for r in rep["surfaces"]:
        print("%-44s %-5s %-12s %-18s %-6s %s" % (
            r["path"][-44:], "yes" if r["head_identical"] else "NO", r["commit"],
            (r["author"] or "")[:18], "yes" if r["signed"] else "NO",
            r["reason"] if not (r["head_identical"] and r["signed"]) else r.get("principal") or ""))
    for u in rep["untracked_members"]:
        print("%-44s (untracked member: hook-lane only; doctor 16(c) checks wiring)" % u)
    for rr in rep["retire_records"]:
        print("retire record %s seq %s: %s -- %s" % (rr["path"], rr["seq"],
              "PUBLISHED" if rr["published"] else "UNPUBLISHED PROPOSAL", rr["reason"]))
    for f in rep.get("branch_rewind", []):
        print("REWIND: " + f)


# ------------------------------------------------------------------ self-test
@contextlib.contextmanager
def _accept_types(types):
    """SELF-TEST ONLY. See _ACCEPT_TYPES."""
    global _ACCEPT_TYPES
    saved = _ACCEPT_TYPES
    _ACCEPT_TYPES = frozenset(types)
    _CHAIN_CACHE.clear()
    try:
        yield
    finally:
        _ACCEPT_TYPES = saved
        _CHAIN_CACHE.clear()


def _ssh_str(b):
    return struct.pack(">I", len(b)) + b


def _synthetic_sk_pub(ed25519_pub_line):
    """An sk-ssh-ed25519@openssh.com PUBLIC key line built from a software ed25519 public
    key: same 32 raw bytes, application 'ssh:'. No private half exists for it in sk form,
    which is exactly the point of the cases that use it."""
    b64 = ed25519_pub_line.split()[1]
    blob = base64.b64decode(b64)
    _kt, off = _ssh_string(blob, 0)
    pk, _ = _ssh_string(blob, off)
    sk = _ssh_str(b"sk-ssh-ed25519@openssh.com") + _ssh_str(pk) + _ssh_str(b"ssh:")
    return "sk-ssh-ed25519@openssh.com " + base64.b64encode(sk).decode("ascii")


def _forge_sk_sshsig(real_sig_text, sk_pub_line):
    """The REAL signature blob with its public key swapped for an sk-typed one: type
    check passes, cryptographic verification cannot."""
    m = re.search(r"-----BEGIN SSH SIGNATURE-----(.*?)-----END SSH SIGNATURE-----",
                  real_sig_text, re.S)
    blob = base64.b64decode("".join(m.group(1).split()))
    off = 10
    _pub, off2 = _ssh_string(blob, off)
    new_pub = base64.b64decode(sk_pub_line.split()[1])
    forged = blob[:off] + _ssh_str(new_pub) + blob[off2:]
    b64 = base64.b64encode(forged).decode("ascii")
    lines = [b64[i:i + 70] for i in range(0, len(b64), 70)]
    return "-----BEGIN SSH SIGNATURE-----\n" + "\n".join(lines) + "\n-----END SSH SIGNATURE-----\n"


def self_test():
    failed = total = 0

    def case(name, cond, detail=""):
        nonlocal failed, total
        total += 1
        if not cond:
            failed += 1
        print("  %s %s%s" % ("ok " if cond else "XX ", name,
                             ("  [%s]" % detail if detail and not cond else "")))

    if shutil.which("ssh-keygen") is None or shutil.which("git") is None:
        print("trust.py self-test: INCONCLUSIVE -- ssh-keygen and git are required on PATH")
        return 2

    base = tempfile.mkdtemp(prefix="trust-selftest-")
    try:
        keys = {}
        for name in ("opA", "opB", "mallory"):
            kp = os.path.join(base, name)
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", kp],
                           check=True, capture_output=True)
            keys[name] = {"priv": kp, "pub": open(kp + ".pub").read().strip()}
        sk_line_A = _synthetic_sk_pub(keys["opA"]["pub"])

        def git(repo, *a, key=None, **kw):
            cfg = []
            if key:
                cfg = ["-c", "gpg.format=ssh", "-c", "user.signingkey=" + keys[key]["priv"]]
            p = subprocess.run(["git", "-C", repo] + cfg + list(a), capture_output=True,
                               text=True, encoding="utf-8", errors="replace", **kw)
            return p

        def write(repo, rel, text):
            p = os.path.join(repo, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)

        def commit(repo, msg, key=None, paths=("-A",)):
            git(repo, "add", *paths)
            a = ["commit", "-q", "-m", msg] + (["-S"] if key else [])
            p = git(repo, *a, key=key)
            assert p.returncode == 0, p.stderr
            return git(repo, "rev-parse", "HEAD").stdout.strip()

        def new_repo(name):
            r = os.path.join(base, name)
            os.makedirs(r)
            git(r, "init", "-q", "-b", "main")
            git(r, "config", "user.email", "t@t")
            git(r, "config", "user.name", "tester")
            git(r, "config", "commit.gpgsign", "false")
            return r

        # ---------------------------------------------------------- 1. pure helpers
        rows = parse_allowed_signers(
            "# comment\noperator %s\noperator-soft %s\nx namespaces=\"git\" %s\n" % (
                sk_line_A, keys["opA"]["pub"], keys["opB"]["pub"]))
        case("parse_allowed_signers: 3 rows, options field tolerated",
             len(rows) == 3 and rows[2]["options"] == 'namespaces="git"', str(rows))
        case("sk typing: only the sk line is presence-requiring",
             [r["sk"] for r in rows] == [True, False, False])
        filt = _filtered_signers("\n".join(r["line"] for r in rows), SK_TYPES)
        case("non-sk keys are ABSENT from the filtered pin (not warned -- gone)",
             "ssh-ed25519 AAAA" not in filt and "sk-ssh-ed25519" in filt)
        case("in_class: dot-files are members (.gitattributes, .claude/settings.json; "
             "round-7 catch) and './' prefixes are stripped without eating dots",
             in_class(".gitattributes") and in_class(".claude/settings.json")
             and in_class("./.claude/settings.local.json") and not in_class("gitattributes"))
        case("glob->regex: '**' spans dirs, '*' does not; '.example' sibling is NOT in class",
             in_class("core/security/hooks/test-inputs/x.json")
             and in_class("deploy/evidence/operator-x.md")
             and not in_class("deploy/evidence/operator-sub/x.md")
             and not in_class("deploy/safe-allowlist.yaml.example")
             and not in_class("deploy/evidence/README.md")
             and in_class("capabilities/knowledge-os/extracted/deploy/trust.py"))
        # a real signature's type + a forged sk-typed blob's type
        mp = os.path.join(base, "m")
        open(mp, "wb").write(b"payload")
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", keys["opA"]["priv"], "-n", "git", mp],
                       check=True, capture_output=True)
        real_sig = open(mp + ".sig").read()
        case("parse_sshsig: software key reads as ssh-ed25519 in namespace git",
             parse_sshsig(real_sig)["keytype"] == "ssh-ed25519"
             and parse_sshsig(real_sig)["namespace"] == "git")
        forged = _forge_sk_sshsig(real_sig, sk_line_A)
        case("parse_sshsig: forged blob reads as sk type (the type check alone buys nothing)",
             parse_sshsig(forged)["keytype"] == "sk-ssh-ed25519@openssh.com")
        v = _verify_sig(b"payload", forged, "operator %s\n" % sk_line_A)
        case("forged sk-typed signature FAILS cryptographic verification against the sk pin",
             not v["ok"] and v["keytype"] == "sk-ssh-ed25519@openssh.com", v["reason"])
        v = _verify_sig(b"payload", real_sig, "operator-soft %s\n" % keys["opA"]["pub"])
        case("REAL software-key signature listed in the pin is REFUSED: type not sk",
             not v["ok"] and "not presence-requiring" in v["reason"], v["reason"])
        with _accept_types({"ssh-ed25519"}):
            v = _verify_sig(b"payload", real_sig, "operator-soft %s\n" % keys["opA"]["pub"])
            case("[mechanics, test-only type override] the same signature verifies",
                 v["ok"] and v["principal"] == "operator-soft", v["reason"])
            v = _verify_sig(b"payloaX", real_sig, "operator-soft %s\n" % keys["opA"]["pub"])
            case("[mechanics] a one-byte payload change fails verification", not v["ok"])
            v = _verify_sig(b"payload", real_sig, "other %s\n" % keys["opB"]["pub"])
            case("[mechanics] a key not in the pin is refused 'not in the pinned set'",
                 not v["ok"] and "not in the pinned" in v["reason"], v["reason"])
        case("_ACCEPT_TYPES restored to SK_TYPES after the override", _ACCEPT_TYPES == SK_TYPES)

        # ---------------------------------------------------------- 2. real sk policy
        r1 = new_repo("policy")
        write(r1, PIN_PATH, "operator %s\n" % sk_line_A)
        write(r1, CLASS_PATH, "\n".join(TRUST_SURFACE_FLOOR) + "\n")
        write(r1, "README.md", "x\n")
        c_boot = commit(r1, "bootstrap (signed by the SOFTWARE key, listed only as sk)", key="opA")
        s = operator_signed(r1, PIN_PATH)
        case("bootstrap signed by a non-sk key is NOT operator-signed (chain refuses)",
             not s["ok"] and "not presence-requiring" in s["reason"], s["reason"])
        write(r1, "deploy/evidence/operator-grant.md", "Verbatim grant: \"yes\"\n")
        commit(r1, "unsigned artifact")
        s = operator_signed(r1, "deploy/evidence/operator-grant.md")
        case("artifact whose pin chain is unverified is refused naming the chain",
             not s["ok"] and "pin chain" in s["reason"], s["reason"])
        ok, why, _b = committed_identical(r1, "deploy/evidence/operator-grant.md")
        case("committed_identical: committed file passes", ok, why)
        write(r1, "deploy/evidence/operator-grant.md", "Verbatim grant: \"yes to everything\"\n")
        ok, why, _b = committed_identical(r1, "deploy/evidence/operator-grant.md")
        case("committed_identical: worktree differing from HEAD is refused with the stat",
             not ok and "differs from HEAD" in why, why)
        git(r1, "add", "-A")
        ok, why, _b = committed_identical(r1, "deploy/evidence/operator-grant.md")
        case("committed_identical: STAGED but uncommitted still refused", not ok, why)
        git(r1, "reset", "-q", "--hard")
        # clean-filter spoof (cross-vendor round-6 catch): .gitattributes routes the grant
        # through a filter that emits HEAD's bytes, so `git diff` says clean while the RAW
        # file carries a forgery.
        git(r1, "config", "filter.spoof.clean",
            "git show HEAD:deploy/evidence/operator-grant.md")
        write(r1, ".gitattributes", "deploy/evidence/operator-*.md filter=spoof\n")
        write(r1, "deploy/evidence/operator-grant.md", "Verbatim grant: \"FORGED\"\n")
        p = git(r1, "diff", "--quiet", "HEAD", "--", "deploy/evidence/operator-grant.md")
        ok, why, _b = committed_identical(r1, "deploy/evidence/operator-grant.md")
        case("committed_identical: a clean-filter spoof (git diff reports CLEAN) is still "
             "refused -- raw bytes are compared to the HEAD blob",
             p.returncode == 0 and not ok and "differs from HEAD" in why, (p.returncode, why))
        git(r1, "config", "--unset", "filter.spoof.clean")
        os.remove(os.path.join(r1, ".gitattributes"))
        git(r1, "reset", "-q", "--hard")
        write(r1, "deploy/evidence/operator-grant.md",
              open(os.path.join(r1, "deploy/evidence/operator-grant.md"), "rb").read()
              .replace(b"\r\n", b"\n").replace(b"\n", b"\r\n").decode())
        ok, why, _b = committed_identical(r1, "deploy/evidence/operator-grant.md")
        case("committed_identical: CRLF checkout of an LF blob is tolerated", ok, why)
        git(r1, "reset", "-q", "--hard")
        write(r1, "deploy/evidence/operator-new.md", "Verbatim grant: \"new\"\n")
        ok, why, _b = committed_identical(r1, "deploy/evidence/operator-new.md")
        case("committed_identical: untracked file refused ('not tracked')",
             not ok and "not tracked" in why, why)
        os.remove(os.path.join(r1, "deploy", "evidence", "operator-new.md"))
        g = gate_artifact(r1, "deploy/evidence/operator-grant.md", mode="warn")
        case("gate_artifact under warn: unsigned committed artifact ACCEPTED with a warning",
             g["ok"] and g["warnings"] and "ACCEPTED under" in g["warnings"][0], str(g))
        case("gate_artifact returns the verified HEAD blob for the consumer to parse (never "
             "the file on disk)",
             g.get("blob") == _blob_at(r1, "HEAD", "deploy/evidence/operator-grant.md")
             and b"yes" in g["blob"])
        g2 = gate_artifact(r1, "deploy/evidence/operator-grant.md", mode="warn",
                           content=b"Verbatim grant: swapped")
        case("gate_artifact with caller-held bytes that differ from the blob is refused "
             "(swap-window closed)", not g2["ok"] and "differs from HEAD" in g2["refuse"])
        case("gate_artifact binds both checks to ONE resolved HEAD sha (carried in the result)",
             g.get("head") == git(r1, "rev-parse", "HEAD").stdout.strip()
             and g["signed"]["commit"] is not None)
        g = gate_artifact(r1, "deploy/evidence/operator-grant.md", mode="required")
        case("gate_artifact under required: unsigned artifact REFUSED",
             not g["ok"] and "required" in g["refuse"], str(g))
        write(r1, "deploy/evidence/operator-grant.md", "Verbatim grant: \"tampered\"\n")
        g = gate_artifact(r1, "deploy/evidence/operator-grant.md", mode="warn")
        case("gate_artifact: uncommitted change refused EVEN under warn",
             not g["ok"] and "not committed-identical" in g["refuse"], str(g))
        git(r1, "reset", "-q", "--hard")
        case("signing_mode: no project.yaml -> warn (migration-only) and NOT chosen",
             signing_mode(r1)[0] == "warn" and not mode_chosen(r1)
             and "RETIREMENT IS DISABLED" in signing_mode(r1)[1])
        write(r1, "project.yaml", "project_slug: x\n")
        case("signing_mode: project.yaml without the key -> warn, not chosen, note names the "
             "migration", signing_mode(r1)[0] == "warn" and not mode_chosen(r1)
             and "v3.0.49" in signing_mode(r1)[1])
        write(r1, "project.yaml", "trust_surface_signing: warn\n")
        case("an EXPLICITLY written warn is still NOT a chosen mode (migration-only; "
             "cross-vendor round-1 catch)", signing_mode(r1)[0] == "warn"
             and not mode_chosen(r1))
        write(r1, "project.yaml", "trust_surface_signing: required\n")
        case("signing_mode: required read", signing_mode(r1)[0] == "required")
        write(r1, "project.yaml", "trust_surface_signing: maybe\n")
        case("signing_mode: unrecognized value fails CLOSED to required",
             signing_mode(r1)[0] == "required")
        write(r1, "project.yaml", "trust_surface_signing: visible\n")
        gv = gate_artifact(r1, "deploy/evidence/operator-grant.md")
        case("visible mode: unsigned committed artifact accepted with NO warning (sensors only)",
             signing_mode(r1)[0] == "visible" and gv["ok"] and gv["warnings"] == []
             and gv["blob"] is not None, str(gv))
        case("visible mode: recorded -> mode_chosen True; report carries it",
             mode_chosen(r1) and report(r1)["mode_chosen"] is True)
        write(r1, "project.yaml", "trust_surface_signing: required   # chosen\n")
        case("required mode with a trailing comment -> chosen", mode_chosen(r1))
        write(r1, "project.yaml", "trust_surface_signing: visible\n")
        write(r1, "deploy/evidence/operator-grant.md", "Verbatim grant: \"tampered\"\n")
        gv = gate_artifact(r1, "deploy/evidence/operator-grant.md")
        case("visible mode: an UNCOMMITTED change is still refused", not gv["ok"], str(gv))
        git(r1, "reset", "-q", "--hard")
        os.remove(os.path.join(r1, "project.yaml"))
        ps = pin_status(r1)
        case("pin_status: sk-only pin has zero non-sk keys", ps["present"] and not ps["non_sk"])
        write(r1, PIN_PATH, "operator %s\nsoft %s\n" % (sk_line_A, keys["opA"]["pub"]))
        commit(r1, "add a soft key to the pin (unsigned)")
        ps = pin_status(r1)
        case("pin_status: a non-presence key listed is reported (doctor 16(d) FAIL input)",
             len(ps["non_sk"]) == 1 and not ps["chain"]["ok"])
        rep = report(r1)
        case("report: every tracked class member present, none signed",
             {r["path"] for r in rep["surfaces"]} == {PIN_PATH, CLASS_PATH,
                                                      "deploy/evidence/operator-grant.md"}
             and not any(r["signed"] for r in rep["surfaces"]), str(rep["surfaces"]))

        # ---------------------------------------------------------- 3. mechanics (override)
        with _accept_types({"ssh-ed25519"}):
            r2 = new_repo("mech")
            write(r2, PIN_PATH, "opA %s\n" % keys["opA"]["pub"])
            write(r2, "wiki/view.md", "# View\n\nbody\n")
            c_boot = commit(r2, "bootstrap pin", key="opA")
            ch = verify_pin_chain(r2, c_boot)
            case("[mech] bootstrap commit signed by a key it lists -> chain ok",
                 ch["ok"] and ch["bootstrap"] == c_boot, ch["reason"])
            s = operator_signed(r2, PIN_PATH)
            case("[mech] operator_signed(pin) -> principal opA via parent-pin rule",
                 s["ok"] and s["principal"] == "opA", s["reason"])
            write(r2, "deploy/evidence/operator-grant.md", "Verbatim grant: \"go\"\n")
            c_art = commit(r2, "signed grant", key="opA")
            s = operator_signed(r2, "deploy/evidence/operator-grant.md")
            case("[mech] signed artifact -> ok, keytype recorded",
                 s["ok"] and s["keytype"] == "ssh-ed25519" and s["commit"] == c_art, s["reason"])
            g = gate_artifact(r2, "deploy/evidence/operator-grant.md", mode="required")
            case("[mech] gate_artifact required + signed -> ok, no warnings",
                 g["ok"] and not g["warnings"])
            write(r2, "deploy/evidence/operator-grant.md", "Verbatim grant: \"go further\"\n")
            commit(r2, "agent re-edit, unsigned")
            s = operator_signed(r2, "deploy/evidence/operator-grant.md")
            case("[mech] newest commit unsigned -> refused 'UNSIGNED' (an older signature "
                 "does not carry forward)", not s["ok"] and "UNSIGNED" in s["reason"], s["reason"])
            # rotation
            write(r2, PIN_PATH, "opB %s\n" % keys["opB"]["pub"])
            git(r2, "add", "-A")
            p = git(r2, "commit", "-q", "-S", "-m", "rotate to B signed by B", key="opB")
            s = operator_signed(r2, PIN_PATH)
            case("[mech] rotation signed by the NEW key only is refused (parent pin lacks it)",
                 not s["ok"] and "PARENT" in s["reason"], s["reason"])
            git(r2, "reset", "-q", "--hard", "HEAD~1")
            write(r2, PIN_PATH, "opA %s\nopB %s\n" % (keys["opA"]["pub"], keys["opB"]["pub"]))
            commit(r2, "add B, signed by A", key="opA")
            s = operator_signed(r2, PIN_PATH)
            case("[mech] rotation signed by a PARENT-pinned key accepted", s["ok"], s["reason"])
            write(r2, PIN_PATH, "opB %s\n" % keys["opB"]["pub"])
            c_rot = commit(r2, "drop A, signed by B", key="opB")
            s = operator_signed(r2, PIN_PATH)
            case("[mech] second rotation by B (now in parent pin) accepted; A retired",
                 s["ok"] and s["principal"] == "opB", s["reason"])
            write(r2, "deploy/evidence/operator-grant.md", "Verbatim grant: \"by B\"\n")
            commit(r2, "grant by B", key="opB")
            s = operator_signed(r2, "deploy/evidence/operator-grant.md")
            case("[mech] artifact signed by B after rotation ok", s["ok"], s["reason"])
            write(r2, "deploy/evidence/operator-old.md", "Verbatim grant: \"by A, old\"\n")
            commit(r2, "grant by A after A was retired", key="opA")
            s = operator_signed(r2, "deploy/evidence/operator-old.md")
            case("[mech] signature by a RETIRED key (not in pin at that commit) refused",
                 not s["ok"], s["reason"])
            # delete + recreate on a side branch
            main_head = git(r2, "rev-parse", "HEAD").stdout.strip()
            git(r2, "checkout", "-q", "-b", "evil")
            git(r2, "rm", "-q", PIN_PATH)
            p = git(r2, "commit", "-q", "-S", "-m", "delete pin", key="opB")
            ch = verify_pin_chain(r2, git(r2, "rev-parse", "HEAD").stdout.strip())
            case("[mech] pin DELETED -> chain refused", not ch["ok"] and "absent" in ch["reason"],
                 ch["reason"])
            write(r2, PIN_PATH, "mallory %s\n" % keys["mallory"]["pub"])
            c_evil = commit(r2, "re-bootstrap with mallory", key="mallory")
            ch = verify_pin_chain(r2, c_evil)
            case("[mech] delete-and-recreate self-vouching pin refused (the walk refuses at "
                 "the deletion link; the re-creation never becomes a bootstrap)",
                 not ch["ok"] and ("DELETED" in ch["reason"] or "re-created" in ch["reason"]),
                 ch["reason"])
            git(r2, "checkout", "-q", "main")
            # merges. (i) a merge whose pin is TREESAME to one parent changes nothing the
            # parent's chain did not already verify -- git's path simplification attributes
            # the pin to that parent, and the chain follows it. (ii) a merge whose pin
            # differs from BOTH parents (conflict-resolved) is a pin change made BY the
            # merge commit -- refused: a signature never vouches for history it does not name.
            git(r2, "checkout", "-q", "-b", "rot2", c_rot)
            write(r2, PIN_PATH, "opB %s\nmallory %s\n" % (keys["opB"]["pub"],
                                                           keys["mallory"]["pub"]))
            commit(r2, "side add mallory signed by B", key="opB")
            git(r2, "checkout", "-q", "main")
            p = git(r2, "merge", "-q", "--no-ff", "--no-edit", "rot2", key="opB")
            merged = git(r2, "rev-parse", "HEAD").stdout.strip()
            ch = verify_pin_chain(r2, merged)
            case("[mech] merge TREESAME to a verified side branch: pin chain ok via that parent",
                 p.returncode == 0 and ch["ok"], ch["reason"])
            git(r2, "reset", "-q", "--hard", main_head)
            git(r2, "checkout", "-q", "-b", "rot3", c_rot)
            write(r2, PIN_PATH, "opB %s\nopA %s\n" % (keys["opB"]["pub"], keys["opA"]["pub"]))
            commit(r2, "side re-add A signed by B", key="opB")
            git(r2, "checkout", "-q", "main")
            write(r2, PIN_PATH, "opB %s\nmallory %s\n" % (keys["opB"]["pub"],
                                                           keys["mallory"]["pub"]))
            commit(r2, "main add mallory signed by B", key="opB")
            p = git(r2, "merge", "-q", "--no-edit", "rot3")  # conflicts on the pin
            write(r2, PIN_PATH, "opB %s\nopA %s\nmallory %s\n" % (
                keys["opB"]["pub"], keys["opA"]["pub"], keys["mallory"]["pub"]))
            git(r2, "add", PIN_PATH)
            p2 = git(r2, "commit", "-q", "-S", "--no-edit", key="opB")
            merged = git(r2, "rev-parse", "HEAD").stdout.strip()
            ch = verify_pin_chain(r2, merged)
            case("[mech] a MERGE commit whose pin differs from BOTH parents is refused",
                 p.returncode != 0 and p2.returncode == 0 and len(_parents(r2, merged)) == 2
                 and not ch["ok"] and "MERGE" in ch["reason"], ch["reason"])
            git(r2, "reset", "-q", "--hard", main_head)
            _CHAIN_CACHE.clear()

            # unrelated-history pivot (cross-vendor round-1 catch): an orphan branch with
            # its own self-vouching pin, merged in with the pin resolved to the orphan's.
            git(r2, "checkout", "-q", "--orphan", "pivot")
            git(r2, "rm", "-rfq", ".")
            write(r2, PIN_PATH, "mallory %s\n" % keys["mallory"]["pub"])
            commit(r2, "orphan bootstrap by mallory", key="mallory")
            orphan = git(r2, "rev-parse", "HEAD").stdout.strip()
            ch = verify_pin_chain(r2, orphan)
            case("[mech] an orphan history's self-vouching pin verifies IN ISOLATION (it is a "
                 "bootstrap there)", ch["ok"], ch["reason"])
            git(r2, "checkout", "-q", "main")
            p = git(r2, "merge", "-q", "--allow-unrelated-histories", "--no-edit", "pivot")
            write(r2, PIN_PATH, "mallory %s\n" % keys["mallory"]["pub"])  # resolve to the orphan's pin
            git(r2, "add", PIN_PATH)
            git(r2, "commit", "-q", "--no-edit")
            pivoted = git(r2, "rev-parse", "HEAD").stdout.strip()
            ch = verify_pin_chain(r2, pivoted)
            case("[mech] merging that orphan in and resolving the pin to ITS key is refused "
                 "(second bootstrap in the full history)",
                 not ch["ok"] and ("SECOND" in ch["reason"] or "MERGE" in ch["reason"]), ch["reason"])
            git(r2, "reset", "-q", "--hard", main_head)
            _CHAIN_CACHE.clear()

            # tags + publication -------------------------------------------------
            prod = git(r2, "rev-parse", "HEAD").stdout.strip()
            git(r2, "checkout", "-q", "-b", "work", prod)
            write(r2, "wiki/view.md", "# View\n\nbody (retired section moved cold)\n")
            write(r2, "deploy/rulings/retire-1/proposal.md", "proposal digest me\n")
            dig = hashlib.sha256(b"proposal digest me\n").hexdigest()
            write(r2, "receipts/journal/7.json", json.dumps({
                "run_type": "retire", "seq": 1, "tag": "retire/1",
                "proposal": "deploy/rulings/retire-1/proposal.md",
                "proposal_digest": "sha256:" + dig}, indent=1))
            c1 = commit(r2, "retire 1 prepared (unsigned work-ref commit)")
            git(r2, "update-ref", "refs/retire/1", c1)
            git(r2, "checkout", "-q", "main")
            v = operator_tag(r2, "retire/1", c1)
            case("[mech] no tag yet -> refused 'does not exist'", not v["ok"] and "does not exist"
                 in v["reason"], v["reason"])
            rr = retire_records_status(r2, c1)
            case("[mech] retire record without a verified tag reads UNPUBLISHED",
                 len(rr) == 1 and not rr[0]["published"], str(rr))
            git(r2, "tag", "retire/1", c1)  # lightweight
            v = operator_tag(r2, "retire/1", c1)
            case("[mech] lightweight tag refused (no object, no signature)",
                 not v["ok"] and "not an annotated" in v["reason"], v["reason"])
            git(r2, "tag", "-d", "retire/1")
            git(r2, "tag", "-a", "-m", "unsigned", "retire/1", c1)
            v = operator_tag(r2, "retire/1", c1)
            case("[mech] unsigned annotated tag refused", not v["ok"] and "UNSIGNED" in v["reason"],
                 v["reason"])
            git(r2, "tag", "-d", "retire/1")
            p = git(r2, "tag", "-s", "-m", "retire 1 by mallory", "retire/1", c1, key="mallory")
            v = operator_tag(r2, "retire/1", c1)
            case("[mech] tag signed by an UNPINNED key refused",
                 p.returncode == 0 and not v["ok"] and "not in the pinned" in v["reason"],
                 v["reason"])
            git(r2, "tag", "-d", "retire/1")
            p = git(r2, "tag", "-s", "-m", "retire 1", "retire/1", c1, key="opB")
            v = operator_tag(r2, "retire/1", c1)
            case("[mech] tag signed by the pinned key names C -> ok",
                 p.returncode == 0 and v["ok"] and v["principal"] == "opB", v["reason"])
            v = operator_tag(r2, "retire/1", prod)
            case("[mech] the same tag checked against a DIFFERENT commit refused",
                 not v["ok"] and "not commit" in v["reason"], v["reason"])
            chk = check_publishable(r2, "retire/1", "main")
            case("[mech] amended condition 4: NO recorded authority mode -> check_publishable "
                 "refuses BEFORE consulting the (valid) tag", not chk["ok"]
                 and "retirement disabled" in chk["reason"], chk["reason"])
            # project.yaml is per-instance config, never part of the fixture's commits:
            # exclude it so the `-A` commits below do not carry it across branches.
            write(r2, ".git/info/exclude", "project.yaml\n")
            write(r2, "project.yaml", "trust_surface_signing: warn\n")
            chk = check_publishable(r2, "retire/1", "main")
            case("[mech] an EXPLICIT migration-only warn also refuses publication (the valid "
                 "tag is never reached)", not chk["ok"] and "retirement disabled" in chk["reason"],
                 chk["reason"])
            write(r2, "project.yaml", "trust_surface_signing: visible\n")
            git(r2, "tag", "-d", "retire/1")
            git(r2, "tag", "-a", "-m", "unsigned", "retire/1", c1)
            chk = check_publishable(r2, "retire/1", "main")
            case("[mech] under visible an UNSIGNED tag still does not publish; the refusal "
                 "names the promote action", not chk["ok"]
                 and "exact-digest" in chk["reason"] and "promote.py" in chk["reason"], chk["reason"])
            # ---- v3.0.50: the visible-mode PROMOTION RECORD (ADR #11 cond. 4 as amended, item 3)
            promo_msg = "promotion\nproposal_digest: sha256:%s\nmode: visible\n" % dig
            git(r2, "tag", "-d", "retire/1")
            git(r2, "tag", "-a", "-m", promo_msg, "retire/1", c1)
            pr = promotion_tag(r2, "retire/1", c1)
            case("[mech] promotion_tag: annotated unsigned tag carrying the digest + mode reads as "
                 "a promotion record (digest parsed)", pr["ok"] and pr["digest"] == dig, pr["reason"])
            chk = check_publishable(r2, "retire/1", "main")
            case("[mech] under visible the promotion record PUBLISHES (no signature; exact digest)",
                 chk["ok"] and chk.get("kind") == "promoted" and "promoted" in chk["reason"],
                 chk["reason"])
            rr = retire_records_status(r2, c1)
            case("[mech] the reader agrees: the promoted record reads PUBLISHED (kind promoted)",
                 len(rr) == 1 and rr[0]["published"] and rr[0].get("kind") == "promoted", str(rr))
            write(r2, "project.yaml", "trust_surface_signing: required\n")
            chk = check_publishable(r2, "retire/1", "main")
            case("[mech] under required the SAME promotion record is refused (sk tag path "
                 "unchanged)", not chk["ok"] and "required" in chk["reason"], chk["reason"])
            rr = retire_records_status(r2, c1)
            case("[mech] ...and the reader under required reads it UNPUBLISHED",
                 len(rr) == 1 and not rr[0]["published"], str(rr))
            write(r2, "project.yaml", "trust_surface_signing: warn\n")
            chk = check_publishable(r2, "retire/1", "main")
            rr = retire_records_status(r2, c1)
            case("[mech] under migration-only warn a promotion record publishes nothing "
                 "(publisher refuses on the unchosen mode; reader reads UNPUBLISHED)",
                 not chk["ok"] and "retirement disabled" in chk["reason"]
                 and not rr[0]["published"], chk["reason"])
            os.remove(os.path.join(r2, "project.yaml"))
            rr = retire_records_status(r2, c1)
            case("[mech] with NO project.yaml the promotion record is not authority either",
                 not rr[0]["published"], str(rr))
            write(r2, "project.yaml", "trust_surface_signing: visible\n")
            git(r2, "tag", "-d", "retire/1")
            other = hashlib.sha256(b"some other proposal\n").hexdigest()
            git(r2, "tag", "-a", "-m", "promotion\nproposal_digest: sha256:%s\nmode: visible\n"
                % other, "retire/1", c1)
            chk = check_publishable(r2, "retire/1", "main")
            case("[mech] a promotion record naming a DIFFERENT digest is refused (exact-proposal "
                 "binding -- a chat 'yes' or a wrong/stale digest binds nothing)",
                 not chk["ok"] and "DIFFERENT proposal" in chk["reason"], chk["reason"])
            rr = retire_records_status(r2, c1)
            case("[mech] ...and the reader reads it UNPUBLISHED naming the mismatch",
                 not rr[0]["published"] and "different proposal" in rr[0]["reason"], str(rr))
            git(r2, "tag", "-d", "retire/1")
            git(r2, "tag", "-a", "-m", "promotion\nproposal_digest: sha256:%s\n" % dig,
                "retire/1", c1)
            chk = check_publishable(r2, "retire/1", "main")
            case("[mech] a tag with the digest but WITHOUT `mode: visible` is not a promotion "
                 "record", not chk["ok"] and "not a promotion record" in chk["reason"], chk["reason"])
            git(r2, "tag", "-d", "retire/1")
            git(r2, "tag", "-a", "-m", "promotion\nproposal_digest: sha256:%s\nmode: visible\n"
                % dig, "retire/1", prod)
            pr = promotion_tag(r2, "retire/1", c1)
            case("[mech] a promotion record on a different commit than C is refused",
                 not pr["ok"] and "not commit" in pr["reason"], pr["reason"])
            git(r2, "tag", "-d", "retire/1")
            git(r2, "tag", "retire/1", c1)  # lightweight with no message at all
            pr = promotion_tag(r2, "retire/1", c1)
            case("[mech] a lightweight tag is never a promotion record", not pr["ok"], pr["reason"])
            git(r2, "tag", "-d", "retire/1")
            p = git(r2, "tag", "-s", "-m", "retire 1", "retire/1", c1, key="opB")
            chk = check_publishable(r2, "retire/1", "main")
            case("[mech] under visible a VERIFIED operator tag publishes (stronger than the "
                 "mode requires; kind signed)", p.returncode == 0 and chk["ok"]
                 and chk.get("kind") == "signed", chk["reason"])
            write(r2, "project.yaml", "trust_surface_signing: required\n")
            chk = check_publishable(r2, "retire/1", "main")
            case("[mech] check_publishable: the honest fixture passes", chk["ok"], chk["reason"])
            # replayed tag ref: retire/2 pointed at retire/1's tag object
            tagobj = git(r2, "rev-parse", "refs/tags/retire/1").stdout.strip()
            git(r2, "update-ref", "refs/tags/retire/2", tagobj)
            v = operator_tag(r2, "retire/2", c1)
            case("[mech] tag ref re-pointed at another tag's object refused (embedded name)",
                 not v["ok"] and "embedded name" in v["reason"], v["reason"])
            git(r2, "update-ref", "-d", "refs/tags/retire/2")
            # amended commit: the tag no longer names it
            git(r2, "checkout", "-q", "work")
            write(r2, "wiki/view.md", "# View\n\nbody (amended after inspection)\n")
            git(r2, "add", "-A")
            git(r2, "commit", "-q", "--amend", "--no-edit")
            c1b = git(r2, "rev-parse", "HEAD").stdout.strip()
            git(r2, "checkout", "-q", "main")
            v = operator_tag(r2, "retire/1", c1b)
            case("[mech] AMENDED commit is a different hash the tag does not name",
                 c1b != c1 and not v["ok"], v["reason"])
            chk = check_publishable(r2, "retire/1", "main")
            case("[mech] check_publishable still names the ORIGINAL C (tag is the authority, "
                 "not the work ref)", chk["ok"] and chk["commit"] == c1, chk["reason"])
            # replace ref: try to substitute C's content under its hash
            git(r2, "replace", c1, c1b)
            shown = git(r2, "cat-file", "-p", c1 + ":wiki/view.md").stdout
            real = _blob_at(r2, c1, "wiki/view.md").decode()
            case("[mech] `git replace` is IGNORED by trust reads (plain git shows the "
                 "substitute; trust reads the real object)",
                 "amended" in shown and "amended" not in real, shown + "|" + real)
            chk = check_publishable(r2, "retire/1", "main")
            case("[mech] publishable verdict unchanged under a replace ref", chk["ok"],
                 chk["reason"])
            git(r2, "replace", "-d", c1)
            # extra commit on top of C, tag moved to it
            git(r2, "checkout", "-q", "-b", "extra", c1)
            write(r2, "wiki/other.md", "sneaked in\n")
            c_extra = commit(r2, "extra commit riding the batch")
            git(r2, "checkout", "-q", "main")
            git(r2, "tag", "-s", "-m", "retire 1 extra", "retire/1x", c_extra, key="opB")
            # a tag named retire/1x is not retire/<seq>; use seq 2 for the shape
            git(r2, "tag", "-d", "retire/1x")
            write(r2, "x", "")  # no-op
            os.remove(os.path.join(r2, "x"))
            git(r2, "tag", "-s", "-m", "retire 2 on a 2-commit stack", "retire/2", c_extra, key="opB")
            chk = check_publishable(r2, "retire/2", "main")
            case("[mech] C not a single commit atop head (extra commit) refused",
                 not chk["ok"] and "not a single commit" in chk["reason"], chk["reason"])
            git(r2, "tag", "-d", "retire/2")
            # merge commit as C
            git(r2, "checkout", "-q", "-b", "mergeC", prod)
            p = git(r2, "merge", "-q", "--no-ff", "--no-edit", "work")
            c_merge = git(r2, "rev-parse", "HEAD").stdout.strip()
            git(r2, "checkout", "-q", "main")
            git(r2, "tag", "-s", "-m", "retire 2 merge", "retire/2", c_merge, key="opB")
            chk = check_publishable(r2, "retire/2", "main")
            case("[mech] C with merge ancestry refused",
                 p.returncode == 0 and not chk["ok"] and "parents" in chk["reason"], chk["reason"])
            git(r2, "tag", "-d", "retire/2")
            # digest mismatch
            git(r2, "checkout", "-q", "-b", "bad-digest", prod)
            write(r2, "deploy/rulings/retire-3/proposal.md", "the shown proposal\n")
            write(r2, "receipts/journal/8.json", json.dumps({
                "run_type": "retire", "seq": 3, "tag": "retire/3",
                "proposal": "deploy/rulings/retire-3/proposal.md",
                "proposal_digest": "sha256:" + hashlib.sha256(b"a different proposal\n").hexdigest()}))
            c_bad = commit(r2, "retire 3 with a lying digest")
            git(r2, "checkout", "-q", "main")
            git(r2, "tag", "-s", "-m", "retire 3", "retire/3", c_bad, key="opB")
            chk = check_publishable(r2, "retire/3", "main")
            case("[mech] proposal_digest != digest of the artifact in C's tree refused",
                 not chk["ok"] and "proposal_digest" in chk["reason"], chk["reason"])
            git(r2, "tag", "-d", "retire/3")
            # stale proposal: production moved after C was minted
            stale_head = prod
            write(r2, "wiki/view.md", "# View\n\nbody plus an intervening absorb\n")
            c_abs = commit(r2, "intervening absorb on main")
            chk = check_publishable(r2, "retire/1", "main")
            case("[mech] STALE proposal (branch moved after minting) refused",
                 not chk["ok"] and "STALE" in chk["reason"], chk["reason"])
            git(r2, "reset", "-q", "--hard", stale_head)
            # publish
            pub = publish_retirement(r2, "retire/1", "main")
            git(r2, "reset", "-q", "--hard")  # update-ref moved the ref; sync the worktree
            now = git(r2, "rev-parse", "refs/heads/main").stdout.strip()
            case("[mech] publish_retirement fast-forwards main to C exactly",
                 pub["ok"] and pub.get("published") and now == c1, pub["reason"])
            rr = retire_records_status(r2, "main")
            case("[mech] after publication the record reads PUBLISHED",
                 len(rr) == 1 and rr[0]["published"], str(rr))
            pub2 = publish_retirement(r2, "retire/1", "main")
            case("[mech] re-publishing the same tag refused (C is now the head; not atop it)",
                 not pub2["ok"], pub2["reason"])
            # consumed digest replay under a new seq + fresh tag
            git(r2, "checkout", "-q", "-b", "replay", c1)
            write(r2, "deploy/rulings/retire-4/proposal.md", "proposal digest me\n")
            write(r2, "receipts/journal/9.json", json.dumps({
                "run_type": "retire", "seq": 4, "tag": "retire/4",
                "proposal": "deploy/rulings/retire-4/proposal.md",
                "proposal_digest": "sha256:" + dig}))
            c_rep = commit(r2, "retire 4 re-using the consumed digest")
            git(r2, "checkout", "-q", "main")
            git(r2, "tag", "-s", "-m", "retire 4", "retire/4", c_rep, key="opB")
            chk = check_publishable(r2, "retire/4", "main")
            case("[mech] a CONSUMED proposal digest replayed under a new seq refused",
                 not chk["ok"] and "CONSUMED" in chk["reason"], chk["reason"])
            git(r2, "tag", "-d", "retire/4")
            # record tag mismatch
            git(r2, "checkout", "-q", "-b", "mismatch", c1)
            write(r2, "deploy/rulings/retire-5/proposal.md", "five\n")
            write(r2, "receipts/journal/10.json", json.dumps({
                "run_type": "retire", "seq": 5, "tag": "retire/1",
                "proposal": "deploy/rulings/retire-5/proposal.md",
                "proposal_digest": hashlib.sha256(b"five\n").hexdigest()}))
            c_mm = commit(r2, "retire 5 whose record names tag retire/1")
            git(r2, "checkout", "-q", "main")
            git(r2, "tag", "-s", "-m", "retire 5", "retire/5", c_mm, key="opB")
            chk = check_publishable(r2, "retire/5", "main")
            case("[mech] record naming a different tag than the one presented refused",
                 not chk["ok"] and "names tag" in chk["reason"], chk["reason"])
            # smuggled second record inside a signed C (cross-vendor round-2 catch)
            git(r2, "checkout", "-q", "-b", "smuggle", "main")
            write(r2, "deploy/rulings/retire-7/proposal.md", "seven\n")
            write(r2, "receipts/journal/11.json", json.dumps({
                "run_type": "retire", "seq": 7, "tag": "retire/7",
                "proposal": "deploy/rulings/retire-7/proposal.md",
                "proposal_digest": hashlib.sha256(b"seven\n").hexdigest()}))
            write(r2, "receipts/journal/12.json", json.dumps({
                "run_type": "retire", "seq": 8, "tag": "retire/7",
                "proposal": "deploy/rulings/retire-7/proposal.md",
                "proposal_digest": hashlib.sha256(b"seven\n").hexdigest()}))
            c_sm = commit(r2, "retire 7 plus a smuggled second record")
            git(r2, "checkout", "-q", "main")
            git(r2, "tag", "-s", "-m", "retire 7", "retire/7", c_sm, key="opB")
            chk = check_publishable(r2, "retire/7", "main")
            case("[mech] a signed C introducing TWO retire records is refused",
                 not chk["ok"] and "exactly one ADDED" in chk["reason"], chk["reason"])
            git(r2, "update-ref", "refs/heads/main", c_sm)  # an agent forces it onto main anyway
            rr = {r["path"]: r for r in retire_records_status(r2, "main")}
            case("[mech] ...and if forced onto main, the honest reader marks BOTH records "
                 "UNPUBLISHED (sole-record rule)",
                 not rr["receipts/journal/11.json"]["published"]
                 and not rr["receipts/journal/12.json"]["published"], str(rr))
            git(r2, "update-ref", "refs/heads/main", c1)
            git(r2, "tag", "-d", "retire/7")
            # a published record edited AFTER the tagged commit (round-3 catch)
            git(r2, "checkout", "-q", "main")
            rec7 = json.loads(open(os.path.join(r2, "receipts", "journal", "7.json")).read())
            rec7["note"] = "quietly widened after publication"
            write(r2, "receipts/journal/7.json", json.dumps(rec7, indent=1))
            c_edit = commit(r2, "agent edits the published record (unsigned)")
            rr = retire_records_status(r2, "main")
            case("[mech] a published record modified by a later unsigned commit reads "
                 "UNPUBLISHED (newest commit != tagged commit)",
                 len(rr) == 1 and not rr[0]["published"] and "differ from the tagged" in rr[0]["reason"],
                 str(rr))
            git(r2, "reset", "-q", "--hard", c1)
            _CHAIN_CACHE.clear()
            # forced replay (round-4 catch): the signed-but-refused replay C (c_rep, consumed
            # digest) is force-moved onto main; the reader must not publish it.
            git(r2, "tag", "-s", "-m", "retire 4", "retire/4", c_rep, key="opB")
            git(r2, "update-ref", "refs/heads/main", c_rep)
            rr = {r["seq"]: r for r in retire_records_status(r2, "main")}
            case("[mech] a signed REPLAY C forced onto main: the original record stays published, "
                 "the replay reads UNPUBLISHED (consumed digest)",
                 rr[1]["published"] and not rr[4]["published"] and "CONSUMED" in rr[4]["reason"],
                 str(rr))
            git(r2, "update-ref", "refs/heads/main", c1)
            git(r2, "tag", "-d", "retire/4")
            # forced STALE C: undetectable from the branch alone; the remote-tracking ref
            # shows the non-fast-forward (branch_rewind)
            bare = os.path.join(base, "origin.git")
            git(r2, "init", "-q", "--bare", bare)
            git(r2, "remote", "add", "origin", bare)
            write(r2, "wiki/view.md", "# View\n\nabsorbed after C\n")
            c_abs2 = commit(r2, "absorb after publication")
            git(r2, "push", "-q", "origin", "main")
            case("[mech] branch_rewind: clean fast-forward state reports nothing",
                 branch_rewind(r2, "main") == [])
            git(r2, "checkout", "-q", "-b", "stale6", c1)  # minted against the PRE-absorb head
            write(r2, "deploy/rulings/retire-6/proposal.md", "six\n")
            write(r2, "receipts/journal/13.json", json.dumps({
                "run_type": "retire", "seq": 6, "tag": "retire/6",
                "proposal": "deploy/rulings/retire-6/proposal.md",
                "proposal_digest": hashlib.sha256(b"six\n").hexdigest()}))
            C6_stale = commit(r2, "retire 6 prepared against a superseded head")
            git(r2, "checkout", "-q", "main")
            git(r2, "tag", "-s", "-m", "retire 6", "retire/6", C6_stale, key="opB")
            chk = check_publishable(r2, "retire/6", "main")
            case("[mech] the publisher refuses the stale C", not chk["ok"] and "STALE" in chk["reason"])
            git(r2, "update-ref", "refs/heads/main", C6_stale)
            rw = branch_rewind(r2, "main")
            rr = {r["seq"]: r for r in retire_records_status(r2, "main")}
            case("[mech] a signed STALE C forced onto main reads published to the branch-only "
                 "reader (stated residual) AND branch_rewind flags the non-fast-forward",
                 rr[6]["published"] and rw and "NOT a fast-forward" in rw[0], str((rr, rw)))
            git(r2, "update-ref", "refs/heads/main", c1)
            # stale via MERGE (round-5 catch): main advanced to c_abs2, then an unsigned merge
            # brings the signed stale C6 in -- C6 is not on main's first-parent chain.
            git(r2, "update-ref", "refs/heads/main", c_abs2)
            git(r2, "reset", "-q", "--hard")
            p = git(r2, "merge", "-q", "--no-edit", "stale6")
            merged_in = git(r2, "rev-parse", "HEAD").stdout.strip()
            rr = {r["seq"]: r for r in retire_records_status(r2, "main")}
            case("[mech] a signed STALE C merged into the advanced branch by an unsigned merge "
                 "reads UNPUBLISHED (not on the first-parent chain)",
                 p.returncode == 0 and len(_parents(r2, merged_in)) == 2
                 and not rr[6]["published"] and "first-parent" in rr[6]["reason"]
                 and rr[1]["published"], str(rr))
            case("[mech] branch_rewind stays quiet for that merge (it IS a fast-forward of "
                 "origin) -- the first-parent rule, not the rewind detector, catches it",
                 branch_rewind(r2, "main") == [])
            git(r2, "update-ref", "refs/heads/main", c1)
            git(r2, "reset", "-q", "--hard")
            git(r2, "tag", "-d", "retire/6")
            git(r2, "remote", "remove", "origin")
            # delete-then-replay (cross-vendor round-8 catch): an unsigned commit deletes the
            # published record, then the consumed digest is replayed under a new seq with a
            # genuine operator tag.
            git(r2, "rm", "-q", "receipts/journal/7.json")
            c_del = commit(r2, "agent deletes the published retire record (unsigned)")
            git(r2, "checkout", "-q", "-b", "replay9", c_del)
            write(r2, "deploy/rulings/retire-9/proposal.md", "proposal digest me\n")
            write(r2, "receipts/journal/14.json", json.dumps({
                "run_type": "retire", "seq": 9, "tag": "retire/9",
                "proposal": "deploy/rulings/retire-9/proposal.md",
                "proposal_digest": "sha256:" + dig}))
            c_r9 = commit(r2, "retire 9 replaying the deleted record digest")
            git(r2, "checkout", "-q", "main")
            git(r2, "tag", "-s", "-m", "retire 9", "retire/9", c_r9, key="opB")
            chk = check_publishable(r2, "retire/9", "main")
            case("[mech] deleted-then-replayed digest refused by the publisher (consumption is "
                 "judged over first-parent HISTORY, not the tip tree)",
                 not chk["ok"] and "CONSUMED" in chk["reason"], chk["reason"])
            git(r2, "update-ref", "refs/heads/main", c_r9)
            rr = retire_records_status(r2, "main")
            byseq = {r["seq"]: r for r in rr}
            case("[mech] forced onto main anyway: the replay reads UNPUBLISHED and the deleted "
                 "record is reported as DELETED (append-only journal)",
                 not byseq[9]["published"] and "CONSUMED" in byseq[9]["reason"]
                 and 1 in byseq and not byseq[1]["published"] and "DELETED" in byseq[1]["reason"],
                 str(rr))
            git(r2, "update-ref", "refs/heads/main", c1)
            git(r2, "reset", "-q", "--hard")
            git(r2, "tag", "-d", "retire/9")
            # non-retire -> retire transition (cross-vendor round-9 catch): a pre-existing
            # compile record is MODIFIED into a retire record by C.
            write(r2, "receipts/journal/15.json", json.dumps({"run_type": "compile", "seq": 15}))
            c_pre = commit(r2, "an ordinary compile record (unsigned, fine)")
            git(r2, "checkout", "-q", "-b", "morph", c_pre)
            write(r2, "deploy/rulings/retire-10/proposal.md", "ten\n")
            write(r2, "receipts/journal/15.json", json.dumps({
                "run_type": "retire", "seq": 10, "tag": "retire/10",
                "proposal": "deploy/rulings/retire-10/proposal.md",
                "proposal_digest": hashlib.sha256(b"ten\n").hexdigest()}))
            c_morph = commit(r2, "retire 10 by morphing an existing journal file")
            git(r2, "checkout", "-q", "main")
            git(r2, "tag", "-s", "-m", "retire 10", "retire/10", c_morph, key="opB")
            chk = check_publishable(r2, "retire/10", "main")
            case("[mech] a record MODIFIED into existence (not appended) is refused by the "
                 "publisher (journal delta must be exactly one ADDED file)",
                 not chk["ok"] and "ADDED" in chk["reason"], chk["reason"])
            git(r2, "update-ref", "refs/heads/main", c_morph)
            rr = {r["seq"]: r for r in retire_records_status(r2, "main")}
            case("[mech] forced onto main: the morphed record reads UNPUBLISHED",
                 not rr[10]["published"] and "addition" in rr[10]["reason"], str(rr))
            hist = _retire_records_history(r2, "main")
            case("[mech] history walk sees the morphed record at the commit that made it one "
                 "(AM, not A) so its digest is consumed thereafter",
                 any(sha == c_morph and r.get("seq") == 10 for _, sha, r in hist), str(hist))
            git(r2, "update-ref", "refs/heads/main", c1)
            git(r2, "reset", "-q", "--hard")
            git(r2, "tag", "-d", "retire/10")
            # rename-and-modify (cross-vendor round-10 catch): git's rename detection would
            # classify the step as R and hide it from an A/M history walk.
            git(r2, "checkout", "-q", "-b", "renamer", c_pre)
            git(r2, "mv", "receipts/journal/15.json", "receipts/journal/16.json")
            write(r2, "receipts/journal/16.json", json.dumps({
                "run_type": "retire", "seq": 16, "tag": "retire/16",
                "proposal": "deploy/rulings/retire-10/proposal.md",
                "proposal_digest": hashlib.sha256(b"sixteen\\n").hexdigest(),
                "pad": "x" * 2000}))
            c_ren = commit(r2, "renamed + modified into a retire record")
            git(r2, "rm", "-q", "receipts/journal/16.json")
            c_ren_del = commit(r2, "and deleted again")
            hist = _retire_records_history(r2, "renamer")
            case("[mech] a journal file renamed-and-modified into a retire record, then deleted, "
                 "is still seen by the history walk (--no-renames): its digest/seq stay consumed",
                 any(sha == c_ren and r.get("seq") == 16 for _, sha, r in hist), str(hist))
            git(r2, "checkout", "-q", "main")
            git(r2, "reset", "-q", "--hard", c1)
            # delete, then re-add a DIFFERENT record at the same path (round-11 catch)
            git(r2, "rm", "-q", "receipts/journal/7.json")
            c_del2 = commit(r2, "delete the published record (unsigned)")
            git(r2, "checkout", "-q", "-b", "reuse", c_del2)
            write(r2, "deploy/rulings/retire-17/proposal.md", "seventeen\n")
            write(r2, "receipts/journal/7.json", json.dumps({
                "run_type": "retire", "seq": 17, "tag": "retire/17",
                "proposal": "deploy/rulings/retire-17/proposal.md",
                "proposal_digest": hashlib.sha256(b"seventeen\n").hexdigest()}))
            c_reuse = commit(r2, "retire 17 re-using the deleted record's PATH")
            git(r2, "checkout", "-q", "main")
            git(r2, "tag", "-s", "-m", "retire 17", "retire/17", c_reuse, key="opB")
            chk = check_publishable(r2, "retire/17", "main")
            case("[mech] a record appended at a previously USED journal path is refused by the "
                 "publisher (pathnames are never reused)",
                 not chk["ok"] and "never reused" in chk["reason"], chk["reason"])
            git(r2, "update-ref", "refs/heads/main", c_reuse)
            git(r2, "reset", "-q", "--hard")
            rr = {r["seq"]: r for r in retire_records_status(r2, "main")}
            case("[mech] forced onto main: the path-reusing record reads UNPUBLISHED and the "
                 "deleted seq-1 record is STILL reported as DELETED",
                 not rr[17]["published"] and 1 in rr and not rr[1]["published"]
                 and "DELETED" in rr[1]["reason"], str(rr))
            git(r2, "update-ref", "refs/heads/main", c1)
            git(r2, "reset", "-q", "--hard")
            git(r2, "tag", "-d", "retire/17")
            rep = report(r2)
            case("[mech] report lists the published record and the signed pin",
                 any(rr["published"] for rr in rep["retire_records"])
                 and any(r["path"] == PIN_PATH and r["signed"] for r in rep["surfaces"]))
        case("_ACCEPT_TYPES restored after the mechanics block", _ACCEPT_TYPES == SK_TYPES)
        # the public API with the real policy refuses the whole mechanics repo (non-sk)
        s = operator_signed(r2, PIN_PATH)
        case("real policy: the software-key-signed mechanics repo is REFUSED end to end",
             not s["ok"] and "not presence-requiring" in s["reason"], s["reason"])
        v = operator_tag(r2, "retire/1", c1)
        case("real policy: its software-key tag is REFUSED too", not v["ok"], v["reason"])
        # ---- v3.0.52 (v3.0-151): production_branch -- one home, pinned both directions
        r3 = os.path.join(base, "r3")
        os.makedirs(r3)
        subprocess.run(["git", "-C", r3, "init", "-q", "-b", "dogfood/fork-v3"],
                       capture_output=True)
        b3, note3 = production_branch(r3)
        case("v3.0-151: no project.yaml key -> the checked-out branch (symbolic-ref), even "
             "unborn", b3 == "dogfood/fork-v3" and "symbolic-ref" in note3, (b3, note3))
        with open(os.path.join(r3, "project.yaml"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("project_slug: t3\nproduction_branch: release/prod\n")
        b3, note3 = production_branch(r3)
        case("v3.0-151: the project.yaml production_branch key WINS over the checkout",
             b3 == "release/prod" and "project.yaml" in note3, (b3, note3))
        case("v3.0-151: an explicit branch wins over both",
             resolve_branch(r3, "explicit") == "explicit")
        os.remove(os.path.join(r3, "project.yaml"))
        subprocess.run(["git", "-C", r3, "config", "user.email", "t@t"], capture_output=True)
        subprocess.run(["git", "-C", r3, "config", "user.name", "t"], capture_output=True)
        subprocess.run(["git", "-C", r3, "config", "commit.gpgsign", "false"], capture_output=True)
        with open(os.path.join(r3, "x"), "w") as fh:
            fh.write("x")
        subprocess.run(["git", "-C", r3, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", r3, "commit", "-q", "-m", "seed"], capture_output=True)
        subprocess.run(["git", "-C", r3, "checkout", "-q", "--detach"], capture_output=True)
        b3, _n3 = production_branch(r3)
        try:
            resolve_branch(r3)
            det_refused = False
        except TrustError as e3:
            det_refused = "unresolvable" in str(e3)
        case("v3.0-151: a DETACHED head with no key resolves to nothing and resolve_branch "
             "REFUSES -- never a silent `main`", b3 is None and det_refused, b3)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    print("trust.py self-test: %s (%d/%d)" % ("FAIL" if failed else "PASS", total - failed, total))
    print("NOTE: no FIDO token can be touched in a battery. The sk-positive path (a real "
          "presence-requiring signature verifying) is exercised only by the operator's "
          "ceremony commit; here the TYPE gate is pinned in both directions (sk-typed forged "
          "blob fails crypto; real soft-key signature fails type) and the signature "
          "MECHANICS are proven with software keys under the self-test-only type override.")
    return 1 if failed else 0


# ------------------------------------------------------------------ CLI
def main(argv=None):
    ap = argparse.ArgumentParser(prog="trust.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root", default=".")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", nargs="+", metavar="PATH")
    ap.add_argument("--verify-tag", metavar="TAG")
    ap.add_argument("--commit", metavar="SHA")
    ap.add_argument("--check-publish", metavar="TAG")
    ap.add_argument("--publish", metavar="TAG")
    ap.add_argument("--branch", default=None,
                    help="production branch (default: project.yaml production_branch, "
                         "else the checked-out branch -- v3.0-151)")
    ap.add_argument("--retire-records", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    repo = os.path.abspath(a.root)
    if not is_git_repo(repo):
        print("REFUSED: %s is not a git repository (trust state is git state)" % repo)
        return 2
    if a.report:
        rep = report(repo)
        if a.json:
            print(json.dumps(rep, indent=1, default=str))
        else:
            _print_report(rep)
        bad = [r for r in rep["surfaces"] if not r["head_identical"]]
        unsigned = [r for r in rep["surfaces"] if not r["signed"]]
        unpub = [r for r in rep["retire_records"] if not r["published"]]
        if (bad or unpub or (unsigned and rep["mode"] == "required") or rep["pin"]["non_sk"]
                or rep.get("branch_rewind")):
            return 2
        return 0
    if a.check:
        rc = 0
        for p in a.check:
            g = gate_artifact(repo, p)
            for w in g["warnings"]:
                print("WARN: " + w)
            if g["ok"]:
                print("OK: %s (mode %s)%s" % (p, g["mode"],
                      "" if not g["signed"] or not g["signed"]["ok"] else
                      " signed by " + str(g["signed"]["principal"])))
            else:
                print("REFUSED: " + g["refuse"])
                rc = 2
        return rc
    if a.verify_tag:
        if not a.commit:
            print("--verify-tag needs --commit")
            return 2
        v = operator_tag(repo, a.verify_tag, a.commit)
        print(("OK: " if v["ok"] else "REFUSED: ") + v["reason"])
        return 0 if v["ok"] else 2
    try:
        a.branch = resolve_branch(repo, a.branch)
    except TrustError as e:
        print("REFUSED: %s" % e)
        return 2
    if a.check_publish:
        v = check_publishable(repo, a.check_publish, a.branch)
        print(("OK: " if v["ok"] else "REFUSED: ") + v["reason"])
        return 0 if v["ok"] else 2
    if a.publish:
        v = publish_retirement(repo, a.publish, a.branch)
        print(("OK: " if v["ok"] else "REFUSED: ") + v["reason"])
        return 0 if v["ok"] else 2
    if a.retire_records:
        rr = retire_records_status(repo)
        for r in rr:
            print("%s seq %s: %s -- %s" % (r["path"], r["seq"],
                  "PUBLISHED" if r["published"] else "UNPUBLISHED PROPOSAL", r["reason"]))
        if not rr:
            print("no retire records at HEAD")
        return 2 if any(not r["published"] for r in rr) else 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
