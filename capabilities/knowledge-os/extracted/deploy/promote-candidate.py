#!/usr/bin/env python3
"""promote-candidate.py -- R-1 engine extension (d): THE PROMOTION GATE
(memory-engine v3, R-1 leg D).

Spec: harness-v3.0/specs/r1-build-decisions-2026-07-22.md Part 6 (verbatim,
including the 2026-07-22 amendment on why a pinned entry carries a PUBLIC KEY and
not merely a fingerprint), with Parts 3/4 for the candidate file + ledger shapes
and Part 8 for the done-bar. The design brief (r1-session-intake-writer-design-
brief-2026-07-21.md, extension (d) and the four-round cross-vendor review log) is
the *why*: every check below exists because an earlier design was broken.

WHAT THIS MODULE IS. It is the ONLY path by which model-mediated session output
can acquire OPERATOR PROVENANCE in this system's permanent memory. A session can
write a candidate; a session can write prose; a session can even commit. What a
session cannot do is hold the operator's signing key. This module is the machine
that turns "a key the operator physically holds" into "an origin mint", and
nothing else in the engine may do that.

THE ESCALATION ARC (the review log's own summary of why the gate looks like this
-- presence -> binding -> temporal -> authenticated creation -> content-addressed
consumption). Each arrow is a design that was REJECTED as insufficient:
  R1  a merely-present gate artifact                -> forgeable by the same run
  R2  + candidate-id/disposition binding + predates -> an EARLIER attacker-
                                                       directed run can commit it
                                                       (git authorship is
                                                       spoofable; sessions commit)
  R3  + signature-verified introducing commit       -> but the promoter read the
                                                       artifact from the WORKING
                                                       TREE: an unsigned later
                                                       edit rebinds it (TOCTOU)
  R4  + verified-blob binding (git show <sha>:<p>)  -> terminal, converged
So: the signature and the consumed bytes must be THE SAME OBJECT. That is check 7,
and it is the reason checks 5/6/7/8 are ordered exactly as they are.

NO DOWNGRADE-AND-PROCEED PATH EXISTS IN THIS FILE. There is no "warn and
continue", no "best effort", no partial grant. Every failure is a NAMED refusal
(PromotionRefused -> `REFUSED: <check> -- <why>`, exit 1). Missing tooling is
INCONCLUSIVE (exit 2), never a refusal-shaped guess and never a promotion. The
only exit-0 path runs the full ten checks to completion.

WHY THE PROMOTER IGNORES REPO GIT CONFIG ENTIRELY (the Part 6 amendment, verified
against git 2.54 rather than assumed -- and re-probed on this instance before this
module was written). A good SSH-signed commit reports `%G?` = `G` and `%GF` = the
same `SHA256:...` string `ssh-keygen -lf` prints; an unsigned commit reports `N`
and an EMPTY `%GF`. But `%G?` is only `G` when the signer is found in the repo's
configured `gpg.ssh.allowedSignersFile` -- REPO-LOCAL CONFIG A SESSION CAN EDIT.
Pinning a fingerprint alone would rest the verdict partly on a mutable file. So
this module writes a TEMPORARY allowed-signers file from the pinned `public_key`
and verifies with `git -c gpg.ssh.allowedSignersFile=<temp> ...`. Both tampering
directions land safely: a session that adds its OWN key to the repo's
allowed-signers still produces a `%GF` outside the pinned set (refused, and
observably `%G?` = `U` against our file), and a session that DELETES the repo's
config changes nothing, because the promoter never read it.

Probed facts this module depends on (git 2.54.0.windows.1, OpenSSH ssh-keygen;
re-verified on this instance, not re-derived at runtime):
  * good signature, key in OUR allowed-signers  -> %G? = "G", %GF = SHA256:...
  * good signature, key NOT in our file         -> %G? = "U", %GF = SHA256:...
  * unsigned commit                             -> %G? = "N", %GF = ""
  * signature over different content (spliced)  -> %G? = "B", %GF = ""
  * `git verify-commit` exits 0 on an unsigned commit in some configurations, so
    its EXIT CODE is never consulted -- only `%G?` is.
  * `git show <sha>:<path>` returns the blob WITHOUT eol/smudge conversion (bytes
    as committed) even under core.autocrlf=true.
  * `git log --diff-filter=A --format=%H -- <p>` prints newest-first; see
    _introducing_commit() for why the LAST line (oldest) is the introducing one.

Usage:
  promote-candidate.py --root DIR --candidate-id ID --artifact PATH
  promote-candidate.py --self-test
Exit: 0 promoted | 1 REFUSED (any check) | 2 INCONCLUSIVE (git/ssh-keygen/PyYAML
      unavailable -- never a promotion, and deliberately NOT dressed up as a
      refusal, because "I could not check" is not "I checked and it failed").
"""

import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

try:  # cp1252 consoles + unicode spans: never let an encode error mask a verdict.
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import yaml
except ImportError:  # pragma: no cover -- optional-yaml idiom (origin.py,
    yaml = None       # candidates.py): absent PyYAML degrades, never crashes.

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(basename, alias):
    spec = importlib.util.spec_from_file_location(alias, os.path.join(_HERE, basename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Sibling-import by path (deploy/ is a flat directory, never a package) -- the
# same idiom register-intake.py and candidates.py use. Distinct aliases so this
# module's imported module objects are its own.
_cands = _load("candidates.py", "candidates_promote")
_origin = _load("origin.py", "origin_promote")


# ------------------------------------------------------------------ constants
SIGNING_CONFIG_FILE = os.path.join(_HERE, "signing-config.yaml")

REQUIRED_DISPOSITION = "transcribe-as-operator-event"

# Part 6 check 3: the ONLY origin_max values a promotion may carry. Written as an
# explicit allow-list, never a deny-list of "tainted" classes: a future origin
# class added to origin.ORIGIN_ORDER must default to REFUSED, not to permitted.
UNTAINTED_ORIGIN_MAX = ("human", "corpus")

RAW_DIR = "raw"

# The operator-provenance filename prefix is PER-INSTANCE CONFIG, never an engine
# constant -- read live from deploy/origin-config.yaml's human_prefixes, the same
# single source of truth origin.is_human_named() matches against. Spec Part 6
# writes the target as `raw/<YYYY-MM-DD>-ryan-<slug>.md`, but "ryan" there is THIS
# FORK's configured operator, not an engine literal: the template ships with no
# name baked in at all, and an instance configured for a different operator would
# have had a `ryan-`-prefixed file mint nothing.
#
# WHY AN UNCONFIGURED INSTANCE REFUSES rather than writing the file anyway (check
# 4b): with no configured prefix, the written raw event does NOT mint `human` --
# it falls through to `corpus`. Superficially that is the fail-safe direction, and
# it is why an earlier draft let it proceed. But it is not fail-safe HERE: the
# ledger would record the candidate as `promoted` -- the state whose whole meaning
# is "this reached operator provenance" -- while the file it points at carries
# none. That is a durable falsehood in an append-only ledger, and corrections are
# new records, never edits, so nothing can retract it. Refusing to promote is the
# honest failure; a `promoted` record that is not true is not.
def operator_filename_prefix(origin_config_path=None):
    """This instance's operator-provenance filename prefix, or None when the
    instance has none configured. The FIRST configured human_prefix wins when
    several are listed -- the file needs exactly one name, and origin.py's rule
    mints `human` for any of them, so any choice is correct; first is the stable,
    predictable one. `origin_config_path` is the self-test seam (production
    callers pass nothing and read the live per-instance config), exactly as
    origin.assign_origin's own seam works."""
    prefixes = _origin.load_origin_config(origin_config_path)["human_prefixes"]
    return prefixes[0] if prefixes else None

_SLUG_CAP = 64

# The exact binding keys the gate artifact must carry (check 8). Deliberately not
# configurable: a configurable binding key is a configurable bypass.
_BIND_CANDIDATE_KEY = "candidate_id"
_BIND_DISPOSITION_KEY = "approved_disposition"

_FINGERPRINT_RE = re.compile(r"^SHA256:[A-Za-z0-9+/=]{10,}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")


class PromotionRefused(Exception):
    """A NAMED refusal at one of the ten checks. `check` is the check's name (it
    appears verbatim in the printed `REFUSED:` line and is what the self-test
    asserts on -- a test that passes because the WRONG check fired is worse than
    no test), `why` is the human-readable reason."""

    def __init__(self, check, why):
        self.check = check
        self.why = why
        Exception.__init__(self, "%s -- %s" % (check, why))


class Inconclusive(Exception):
    """Tooling absent (git / ssh-keygen). NOT a refusal: "I could not check" is a
    different fact from "I checked and it failed", and collapsing the two would
    let a future reader mistake a broken toolchain for an adversarial artifact."""
    pass


def refusal_line(exc):
    """The single canonical rendering of a refusal, so the CLI and the self-test
    assert on the SAME string."""
    return "REFUSED: %s" % exc


def _refuse(check, why):
    raise PromotionRefused(check, why)


# ------------------------------------------------------------------ subprocess
def _run(args, cwd=None):
    """(rc, stdout_bytes, stderr_bytes). ARGUMENT LIST ONLY -- never shell=True:
    every value that reaches here (an artifact path, a candidate id, a temp file
    name) is either operator- or ledger-supplied, and a shell would turn any of
    them into an injection surface on the single most security-critical code path
    in the system."""
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True)
    except (OSError, ValueError) as e:
        return 127, b"", str(e).encode("utf-8", "replace")
    return p.returncode, p.stdout, p.stderr


def _gitpath(p):
    """Forward-slash a filesystem path for use as a git CONFIG VALUE. Windows
    backslashes in a config value are escape characters to git's config parser,
    so `-c gpg.ssh.allowedSignersFile=C:\\Temp\\x` can silently become a path that
    does not exist -- and a missing allowed-signers file verifies NOTHING."""
    return str(p).replace("\\", "/")


def _git(root, args, config=None, decode=True):
    """`git -C <root> [-c k=v ...] <args...>`. `-C <root>` is unconditional (the
    build constraint) so this module can never accidentally read the CWD's repo.
    `config` entries are passed with -c, which OVERRIDES repo config -- that is
    the whole point for gpg.ssh.allowedSignersFile."""
    cmd = ["git", "-C", str(root)]
    for k, v in (config or {}).items():
        cmd += ["-c", "%s=%s" % (k, v)]
    cmd += list(args)
    rc, out, err = _run(cmd)
    if decode:
        return rc, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    return rc, out, err


def tools_available():
    """(ok, missing_name). git and ssh-keygen are both hard requirements: without
    git there is no committed history to verify against, and without ssh-keygen
    the pinned fingerprint/public-key consistency gate cannot run at all."""
    for tool in ("git", "ssh-keygen"):
        if shutil.which(tool) is None:
            return False, tool
    return True, None


# ------------------------------------------------------------------ fingerprint
def ssh_keygen_fingerprint(public_key_line):
    """The real SHA256 fingerprint of an ssh PUBLIC KEY LINE, or None.

    Computed by writing the key to a temp file and running `ssh-keygen -lf`,
    whose output is `<bits> SHA256:<base64> <comment> (<TYPE>)` -- the SHA256
    field is taken by prefix, never by column index (comments contain spaces).

    Returns None (never raises) for: ssh-keygen absent, a non-zero exit (the key
    line is not a public key), or output with no SHA256 field. Every None here is
    treated by load_signing_config as a MALFORMED MEMBER, which voids the ENTIRE
    pinned set -- the fail-safe direction."""
    if shutil.which("ssh-keygen") is None:
        return None
    if not isinstance(public_key_line, str) or "\n" in public_key_line:
        return None
    fd, path = tempfile.mkstemp(prefix="r1-pinned-key-", suffix=".pub")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(public_key_line.strip() + "\n")
        rc, out, _err = _run(["ssh-keygen", "-lf", path])
        if rc != 0:
            return None
        for field in out.decode("utf-8", "replace").split():
            if field.startswith("SHA256:"):
                return field
        return None
    except OSError:
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ------------------------------------------------------------------ trust config
def _empty_signing_config(mode=""):
    return {"signature_mode": mode, "operator_signing_keys": []}


def load_signing_config(path=None, fingerprint_fn=None):
    """Load deploy/signing-config.yaml -> {"signature_mode": str,
    "operator_signing_keys": [ {fingerprint, public_key, principal}, ... ]}.

    THIS IS THE REFERENCE FAIL-SAFE LOADER CONTRACT, mirrored ONE-FOR-ONE from
    origin.load_origin_config() (read that function next to this one; the posture
    is deliberately identical, down to the strict-member rule):

      file ABSENT | PyYAML unavailable | unreadable | unparseable | not a mapping
      | operator_signing_keys not a list | ANY member invalid
                        =>  EMPTY key list.  Never a crash. Never a partial grant.

    A member is invalid if it is not a dict, is missing any of the three keys, has
    a non-string or empty/whitespace-only value, has a public_key or principal
    spanning multiple lines or containing whitespace where a single token is
    required, has a fingerprint outside the `SHA256:<base64>` shape, OR -- the
    consistency gate the spec names explicitly -- has a `fingerprint` that is NOT
    the real fingerprint of its own `public_key`.

    WHY ONE BAD MEMBER VOIDS ALL OF THEM (the cross-vendor finding origin.py
    already carries): a partially-honored TRUST config is worse than an honestly
    empty one. If an operator's config has drifted enough that one entry is
    inconsistent, the honest response is "this instance is unarmed", not "this
    instance is armed with whichever subset happened to parse".

    signature_mode: this build implements SSH verification only. A config whose
    mode is anything other than "ssh" cannot be honored by this code, so it too
    degrades to an empty key list (the mode STRING is still returned, so the
    refusal at check 4 can name it). Honoring a `gpg` config with ssh machinery
    would be exactly the partial grant the doctrine forbids.

    `path` overrides the live file (the self-test seam origin.py uses); the
    self-test NEVER reads deploy/signing-config.yaml. `fingerprint_fn` is a
    second seam for unit-testing the consistency gate without ssh-keygen."""
    path = path or SIGNING_CONFIG_FILE
    fingerprint_fn = fingerprint_fn or ssh_keygen_fingerprint

    if yaml is None or not os.path.isfile(path):
        return _empty_signing_config()
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = yaml.safe_load(fh.read()) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return _empty_signing_config()
    if not isinstance(data, dict):
        return _empty_signing_config()

    mode = data.get("signature_mode")
    mode = mode.strip() if isinstance(mode, str) else ""
    if mode != "ssh":
        return _empty_signing_config(mode)

    raw = data.get("operator_signing_keys")
    if raw is None:
        return _empty_signing_config(mode)
    if not isinstance(raw, list):
        return _empty_signing_config(mode)

    entries = []
    for member in raw:
        if not isinstance(member, dict):
            return _empty_signing_config(mode)
        if set(member) - {"fingerprint", "public_key", "principal"}:
            # Unknown keys in a trust-config member are refused the same way
            # candidates.py refuses unknown frontmatter keys: an unrecognised key
            # may be a typo'd one that was MEANT to constrain something.
            return _empty_signing_config(mode)
        vals = {}
        for key in ("fingerprint", "public_key", "principal"):
            v = member.get(key)
            if not isinstance(v, str) or not v.strip():
                return _empty_signing_config(mode)
            vals[key] = v.strip()
        # principal becomes the FIRST FIELD of an allowed-signers line, and
        # public_key the rest of that line -- so embedded whitespace/newlines
        # would silently restructure the file we hand to git.
        if len(vals["principal"].split()) != 1:
            return _empty_signing_config(mode)
        if "\n" in vals["public_key"] or "\r" in vals["public_key"]:
            return _empty_signing_config(mode)
        if not _FINGERPRINT_RE.match(vals["fingerprint"]):
            return _empty_signing_config(mode)
        # THE CONSISTENCY GATE (spec Part 6): the pinned fingerprint must really
        # be the fingerprint of the pinned public key. A config where the two
        # disagree is malformed -- and it is the dangerous kind of malformed,
        # because the two fields would then authorise different things.
        real = fingerprint_fn(vals["public_key"])
        if real is None or real != vals["fingerprint"]:
            return _empty_signing_config(mode)
        entries.append(vals)
    return {"signature_mode": mode, "operator_signing_keys": entries}


def _write_temp_allowed_signers(entries):
    """An OpenSSH allowed-signers file built ONLY from the pinned entries -- the
    heart of "the promoter does not trust repo git config at all". Returns the
    temp path; the caller removes it in a finally."""
    fd, path = tempfile.mkstemp(prefix="r1-allowed-signers-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        for e in entries:
            fh.write("%s %s\n" % (e["principal"], e["public_key"]))
    return path


# ------------------------------------------------------------------ path safety
def safe_repo_relative(p):
    """A repo-relative, forward-slash, non-escaping path, or None.

    `--artifact` is CLI-supplied and the ledger's `event` is read back off disk;
    both become arguments to git AND (for the candidate) a filesystem read. An
    absolute path, a drive-qualified path, a `..` segment, or an empty segment is
    refused outright rather than normalised -- normalising an attacker-shaped
    path just moves the bug (candidates.py makes the same call for candidate ids
    that become filenames)."""
    if not isinstance(p, str):
        return None
    n = p.replace("\\", "/").strip()
    if not n:
        return None
    if n.startswith("./"):
        n = n[2:]
    if n.startswith("/") or re.match(r"^[A-Za-z]:", n):
        return None
    parts = n.split("/")
    if any(seg in ("", ".", "..") for seg in parts):
        return None
    return n


def _safe_slug(*parts):
    """`[a-z0-9-]` only, collapsed, capped, never empty. CALLERS MUST NEVER PASS
    SPAN TEXT: the slug becomes a filename in raw/, and span text is exactly the
    model-mediated, possibly attacker-influenced string this whole module exists
    to keep away from trust-bearing surfaces. It is built from the candidate
    CLASS (a fixed enum) and the candidate ID (already charset-constrained by
    candidates._CANDIDATE_ID_RE) -- both mechanical, neither authored."""
    s = "-".join(str(p) for p in parts if p).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if len(s) > _SLUG_CAP:
        s = s[:_SLUG_CAP].rstrip("-")
    return s or "session-candidate"


# ------------------------------------------------------------------ git facts
def _introducing_commit(root, artifact):
    """The commit that ADDED `artifact`, or None.

    `git log --diff-filter=A --format=%H -- :(literal)<path>` walks HEAD's history
    NEWEST-FIRST (git log's universal reverse-chronological default). If a path
    was added, deleted and re-added, several commits are reported; the OLDEST --
    i.e. the LAST line -- is the INTRODUCING one, and that is what this returns.

    Reasoned, not assumed, because the choice has a security direction: taking the
    NEWEST add would let an adversary delete an operator-signed artifact and
    re-add it in a commit of their own, moving the verification target onto a
    commit they control. Taking the OLDEST can only ever move the target onto an
    EARLIER commit, and the earliest add is the one the operator signed if anyone
    did. When the oldest add is unsigned and a later re-add is signed, this
    refuses -- conservative, and the correct direction for a trust boundary.

    `:(literal)` disables pathspec glob magic, so an artifact path containing `*`
    or `[` is matched as itself and can never widen the search.
    """
    rc, out, _err = _git(root, ["log", "--diff-filter=A", "--format=%H", "--",
                                ":(literal)" + artifact])
    if rc != 0:
        return None
    shas = [l.strip() for l in out.splitlines() if l.strip()]
    if not shas:
        return None
    return shas[-1]


def _verify_signature(root, sha, config):
    """(status, fingerprint) from
    `git -c gpg.ssh.allowedSignersFile=<temp-from-pinned-keys> log -1
        --format=%G?%n%GF%n%GK <sha>`.

    `git verify-commit`'s EXIT CODE is deliberately never used: it exits 0 on an
    unsigned commit in some configurations, which would turn "no signature at
    all" into "verified". Only `%G?` is read.

    `%GF` is the signing key's fingerprint; `%GK` is used as the documented
    fallback when `%GF` is empty (spec Part 6 check 6). On ssh signatures probed
    here the two are identical."""
    allowed = _write_temp_allowed_signers(config["operator_signing_keys"])
    try:
        rc, out, err = _git(
            root, ["log", "-1", "--format=%G?%n%GF%n%GK", sha],
            config={
                # Every -c value OVERRIDES anything the repo's own config says --
                # that is the entire point (see the module docstring).
                #
                # THE SET MUST BE COMPLETE (cross-vendor round-2 finding,
                # 2026-07-23): `git -C` still loads .git/config, which a session
                # can write, and command-line -c only shields the keys it names.
                # The first shipped set covered allowedSignersFile + format and
                # left `gpg.ssh.program` reachable -- a repo-local value there
                # points verification at ANY program a session chooses, and the
                # OPERATOR's own later clean-environment invocation would use it.
                # So: every gpg.* key that can influence ssh signature
                # verification is pinned here. A NEW git version introducing a
                # new verification-relevant key re-opens this seam -- that risk
                # is recorded in the spec's Part 10 residuals.
                "gpg.ssh.allowedSignersFile": _gitpath(allowed),
                "gpg.format": "ssh",
                # force the real ssh-keygen from PATH; a repo-local value here
                # is an arbitrary-verifier substitution
                "gpg.ssh.program": "ssh-keygen",
                # a repo-planted revocation file could only force a FALSE
                # REFUSAL (fail-safe direction) -- overridden to empty anyway,
                # so the verdict is deterministic under any repo config
                "gpg.ssh.revocationFile": "",
                # never let a repo-local default-key command run at all
                "gpg.ssh.defaultKeyCommand": "",
            })
        if rc != 0:
            return None, None
        lines = out.split("\n")
        status = lines[0].strip() if len(lines) > 0 else ""
        gf = lines[1].strip() if len(lines) > 1 else ""
        gk = lines[2].strip() if len(lines) > 2 else ""
        return status, (gf or gk)
    finally:
        try:
            os.remove(allowed)
        except OSError:
            pass


def _committer_epoch(root, sha):
    rc, out, _err = _git(root, ["log", "-1", "--format=%ct", sha])
    if rc != 0:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def _show_blob(root, sha, path):
    """The artifact's bytes AS COMMITTED IN `sha` -- never the working tree, never
    a later commit (spec Part 6 check 7, the round-4 TOCTOU fold). Returns
    (ok, text_or_error)."""
    rc, out, err = _git(root, ["show", "%s:%s" % (sha, path)], decode=False)
    if rc != 0:
        return False, err.decode("utf-8", "replace").strip()
    try:
        return True, out.decode("utf-8")
    except UnicodeDecodeError:
        return False, "blob is not valid utf-8"


# ------------------------------------------------------------------ blob binding
def parse_binding(blob_text, key):
    """(status, value) for a `<key>: <value>` line in the verified blob.

    status is one of "ok" | "missing" | "ambiguous". Defensive by construction:
      * surrounding whitespace on the line and around the value is tolerated;
      * ONE optional matching pair of surrounding quotes (' or ") is stripped;
      * a value that OPENS with a quote but does not close with the same one is
        AMBIGUOUS (refused), never silently kept;
      * MORE THAN ONE line for the same key is AMBIGUOUS (refused) -- even if the
        values agree, because "which line armed this?" must have exactly one
        answer. This is also the mechanical form of "no blanket authorizations":
        an artifact cannot list several candidates and hope one matches.
    No comment stripping, no wildcard expansion, no prefix matching: the value is
    compared with `==` by the caller, so `candidate_id: abc # note` simply is not
    `abc` and is refused."""
    hits = []
    for line in blob_text.split("\n"):
        s = line.replace("\r", "").strip()
        if not s.startswith(key + ":"):
            continue
        v = s[len(key) + 1:].strip()
        if len(v) >= 1 and v[0] in ("'", '"'):
            if len(v) >= 2 and v[-1] == v[0]:
                v = v[1:-1]
            else:
                return "ambiguous", None
        hits.append(v)
    if not hits:
        return "missing", None
    if len(hits) > 1:
        return "ambiguous", None
    return "ok", hits[0]


# ------------------------------------------------------------------ raw event
def render_raw_event(meta, span, artifact, commit_sha, op_prefix):
    """The promoted operator-provenance raw event's full text.

    FULLY DETERMINISTIC -- no wall-clock value appears anywhere in it. That is
    load-bearing, not cosmetic: see promote()'s write-ordering comment. Re-running
    a promotion whose ledger append failed must be able to recognise the raw file
    it already wrote as byte-identical and converge, rather than tripping over its
    own output. The promotion TIME lives in the ledger record's `recorded_at`,
    where a re-run does not have to reproduce it.

    The span is emitted inside a collision-proof fence chosen by
    candidates._pick_fence (reused rather than reimplemented -- a second copy of
    fence-selection logic is a second chance to get it subtly wrong), so no line
    of the span can terminate the block or be read as document structure. The
    frontmatter `summary` names only the candidate id (charset-constrained); span
    text NEVER reaches frontmatter -- the DA-1 lesson, applied to this module's
    own output: nothing here may read as an authorization."""
    fence = _cands._pick_fence(span)
    lines = [
        "---",
        "source: %s" % op_prefix,
        "date: %s" % str(meta["harvested_at"])[:10],
        "tags: [operator-decision, r1-promotion, %s]" % meta["candidate_class"],
        "summary: Operator-provenance transcription promoted from session candidate "
        "%s under the R-1 signature-verified promotion gate." % meta["candidate_id"],
        "---",
        "",
        "# Operator transcription (R-1 promotion gate)",
        "",
        "## Provenance",
        "",
        "- candidate_id: %s" % meta["candidate_id"],
        "- candidate_class: %s" % meta["candidate_class"],
        "- session_id: %s" % meta["session_id"],
        "- harvested_at: %s" % meta["harvested_at"],
        "- span_sha256: %s" % meta["span_sha256"],
        "- gate_artifact: %s" % artifact,
        "- gate_commit: %s" % commit_sha,
        "- gate: deploy/promote-candidate.py -- ten checks; the artifact's",
        "  introducing commit was signature-verified against a pinned operator",
        "  signing key, and the artifact's content was read from that commit's",
        "  blob (never the working tree, never a later commit).",
        "",
        "## Verbatim span",
        "",
        fence,
    ]
    return "\n".join(lines) + "\n" + span + "\n" + fence + "\n"


def _write_raw_atomic(root, rel, text):
    """temp in the SAME directory -> flush -> fsync -> os.replace. Same discipline
    candidates.write_candidate_atomic uses; `newline=""` is Windows-load-bearing
    (without it Python rewrites every "\\n" to "\\r\\n" and the verbatim span
    stops being verbatim)."""
    final = os.path.join(root, rel.replace("/", os.sep))
    d = os.path.dirname(final)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".r1-promote-tmp-", suffix=".md", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, final)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return final


# ================================================================== THE GATE
def _single_use_scan(root, artifact_rel, sha):
    """Check 10's scan, factored so it runs TWICE: once in the ordinary check
    sequence (fast feedback, unlocked) and once more UNDER THE PROMOTION LOCK
    (authoritative). Raises PromotionRefused on prior consumption."""
    try:
        history = _cands.ledger_history(root)
    except Exception as e:
        _refuse("(10) single-use",
                "the candidate ledger history could not be read: %s: %s"
                % (type(e).__name__, e))
    for prior in history:
        pa = prior.get("gate_artifact")
        pc = prior.get("gate_commit")
        if pa == artifact_rel or (pc and pc == sha):
            _refuse("(10) single-use",
                    "gate artifact %s (commit %s) was ALREADY CONSUMED by ledger "
                    "record seq %s (candidate %s, state %s, gate_artifact=%r, "
                    "gate_commit=%r) -- one signed artifact arms exactly one "
                    "promotion"
                    % (artifact_rel, sha, prior.get("seq"), prior.get("candidate_id"),
                       prior.get("state"), pa, pc))


class _PromotionLock:
    """Cross-process mutual exclusion for the CHECK-AND-CONSUME transaction
    (repo-grounded verification round finding, 2026-07-23, severity HIGH): the
    single-use scan and the `promoted` append were separated by the raw-file
    write, so two concurrent promotions could BOTH observe "unconsumed" and both
    append -- duplicate consumption records in an append-only ledger no re-run
    can retract. compile-core's _SeqLock only serializes individual chain
    appends, never the read-check-append span, so promotion needs its own lock
    held across re-check + raw write + append. Same O_CREAT|O_EXCL spin-lock
    pattern as _SeqLock (portable, crash-safe via staleness timeout), its own
    file name at the git common dir so it composes with -- never replaces --
    the append lock nested inside. One inherited semantic, stated honestly: the
    staleness threshold IS the wait timeout, so a holder still alive past
    `timeout` has its lock broken by the next waiter -- acceptable because the
    locked span (re-check + one file write + one append) is sub-second, and 30 s
    of patience before break-in is generous the same way _SeqLock's is."""

    def __init__(self, repo_root, timeout=30.0):
        rc, out, _err = _git(repo_root, ["rev-parse", "--git-common-dir"])
        common = out.strip() if rc == 0 else os.path.join(repo_root, ".git")
        if not os.path.isabs(common):
            common = os.path.join(repo_root, common)
        self.path = os.path.join(common, "promote-candidate.lock")
        self.timeout = timeout
        self.fd = None

    def __enter__(self):
        deadline = time.time() + self.timeout
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > self.timeout:
                        os.remove(self.path)   # stale lock (crashed holder)
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    _refuse("(12) promotion lock",
                            "another promotion holds the lock at %s and did not "
                            "release it within %.0fs -- refusing rather than "
                            "risking a concurrent double-consumption"
                            % (self.path, self.timeout))
                time.sleep(0.02)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
        try:
            os.remove(self.path)
        except OSError:
            pass



def promote(root, candidate_id, artifact, signing_config_path=None,
            run_started_at=None, out=None, origin_config_path=None):
    """Run all ten Part 6 checks in order and, only if every one passes, write the
    raw event and append the `promoted` ledger record.

    Raises PromotionRefused (named check) on any failure, Inconclusive on missing
    tooling. Returns a dict of the promotion's facts on success. There is NO
    third outcome and no partial one."""
    say = out if out is not None else (lambda *_a: None)

    ok, missing = tools_available()
    if not ok:
        raise Inconclusive("%s is unavailable -- the gate cannot be evaluated" % missing)

    run_started_at = run_started_at if run_started_at is not None else time.time()
    root = os.path.abspath(root)

    # ---------------------------------------------------------------- check 1
    # Candidate is real and eligible.
    try:
        ledger = _cands.load_candidate_ledger(root)
    except Exception as e:
        _refuse("(1) candidate eligibility",
                "the candidate ledger could not be read (a broken or tampered "
                "chain is never promoted over): %s: %s" % (type(e).__name__, e))
    record = ledger.get(str(candidate_id))
    if record is None:
        _refuse("(1) candidate eligibility",
                "candidate %r has no entry in the candidate ledger at "
                "receipts/candidates/ -- only a REGISTERED candidate can be "
                "promoted, and an unknown id is not one" % candidate_id)
    state = record["state"]
    if state != "registered":
        _refuse("(1) candidate eligibility",
                "candidate %r is in effective state %r, not 'registered' "
                "(a staged/expired/discarded/quarantined candidate, and an "
                "already-promoted one, are all ineligible)" % (candidate_id, state))

    event_rel = safe_repo_relative(record.get("event"))
    if event_rel is None or not _origin.is_candidate_path(event_rel):
        _refuse("(1) candidate eligibility",
                "candidate %r's ledger `event` path %r is not a safe repo-relative "
                "path under %s -- refusing to read a staged candidate from outside "
                "the staging directory"
                % (candidate_id, record.get("event"), _origin.CANDIDATES_DIR))
    staged_abs = os.path.join(root, event_rel.replace("/", os.sep))
    if not os.path.isfile(staged_abs):
        _refuse("(1) candidate eligibility",
                "candidate %r's staged file %s is MISSING from disk (a conservation "
                "violation check-candidates.py names; never promoted over)"
                % (candidate_id, event_rel))
    try:
        meta, span = _cands.validate_candidate_file(staged_abs)
    except _cands.CandidateViolation as e:
        _refuse("(1) candidate eligibility",
                "candidate %r's staged file %s does not validate: %s"
                % (candidate_id, event_rel, e))

    # Recompute BOTH derived identities against the file's own bytes. parse_
    # candidate already does this internally; it is repeated here because this is
    # the module where "a rewritten span is a different candidate" must be
    # provably enforced rather than inherited by assumption.
    if _cands.span_hash(span) != meta["span_sha256"]:
        _refuse("(1) candidate eligibility",
                "candidate %r: recomputed span_sha256 does not match the file's "
                "frontmatter (a rewritten span is a different candidate)" % candidate_id)
    if _cands.make_candidate_id(meta["session_id"], span) != meta["candidate_id"]:
        _refuse("(1) candidate eligibility",
                "candidate %r: recomputed candidate_id does not match the file's "
                "frontmatter" % candidate_id)
    if meta["candidate_id"] != str(candidate_id):
        _refuse("(1) candidate eligibility",
                "the staged file at %s declares candidate_id %r, not the requested "
                "%r" % (event_rel, meta["candidate_id"], candidate_id))
    # Cross-check against what was REGISTERED. Beyond the spec's literal wording,
    # and conservative: if the staged file's span hash differs from the hash the
    # ledger registered, the file changed after registration -- the registrar
    # approved a different set of bytes than the ones about to be transcribed.
    if record.get("span_sha256") != meta["span_sha256"]:
        _refuse("(1) candidate eligibility",
                "candidate %r: the staged file's span_sha256 (%s) differs from the "
                "REGISTERED record's (%s) -- the file changed after registration"
                % (candidate_id, meta["span_sha256"], record.get("span_sha256")))
    say("  (1) candidate eligibility  ok  -- registered, file validates, ids recompute")

    # ---------------------------------------------------------------- check 2
    if meta["proposed_disposition"] != REQUIRED_DISPOSITION:
        _refuse("(2) proposed disposition",
                "candidate %r proposes %r; only %r may be promoted to operator "
                "provenance (a plain-intake or discard candidate has no business "
                "on this path)"
                % (candidate_id, meta["proposed_disposition"], REQUIRED_DISPOSITION))
    say("  (2) proposed disposition   ok  -- %s" % REQUIRED_DISPOSITION)

    # ---------------------------------------------------------------- check 3
    # Taint. The refusal names BOTH the candidate and the class (acceptance 2).
    origin_max = meta["origin_max"]
    if origin_max not in UNTAINTED_ORIGIN_MAX:
        _refuse("(3) taint (origin_max)",
                "candidate %r carries origin_max %r, which is outside the untainted "
                "set %s -- a candidate harvested by a session that saw tainted "
                "content can NEVER be promoted to operator provenance, however "
                "valid its armed artifact is (co-residency propagation)"
                % (candidate_id, origin_max, list(UNTAINTED_ORIGIN_MAX)))
    say("  (3) taint (origin_max)     ok  -- %s" % origin_max)

    # ---------------------------------------------------------------- check 4
    config = load_signing_config(signing_config_path)
    pinned = config["operator_signing_keys"]
    if not pinned:
        _refuse("(4) pinned signing keys",
                "operator_signing_keys is EMPTY -- this instance is unarmed and "
                "nothing can be promoted. (Absent, PyYAML-less, unparseable, "
                "non-ssh-mode, or ANY invalid member all degrade to empty by "
                "design; signature_mode read as %r.)" % config["signature_mode"])
    pinned_fps = {e["fingerprint"] for e in pinned}
    say("  (4) pinned signing keys    ok  -- %d pinned key(s)" % len(pinned))

    # --------------------------------------------------------------- check 4b
    # The instance must actually HAVE an operator-provenance prefix configured.
    # See operator_filename_prefix() for why this refuses rather than writing a
    # file that would mint `corpus` under a `promoted` ledger record.
    op_prefix = operator_filename_prefix(origin_config_path)
    if not op_prefix:
        _refuse("(4b) operator provenance prefix",
                "deploy/origin-config.yaml configures NO human_prefixes on this "
                "instance, so a promoted raw event would not mint operator "
                "provenance -- it would assign `corpus` while the ledger recorded "
                "the candidate as `promoted`. Refusing rather than writing a "
                "record that is not true. (Absent, PyYAML-less, unparseable, or "
                "malformed config all degrade to empty by origin.py's own "
                "fail-safe, so this refusal covers every unresolvable-config case.)")
    say("  (4b) operator prefix       ok  -- %r" % op_prefix)

    # ---------------------------------------------------------------- check 5
    artifact_rel = safe_repo_relative(artifact)
    if artifact_rel is None:
        _refuse("(5) introducing commit",
                "--artifact %r is not a safe repo-relative path (absolute paths, "
                "drive letters, '.' / '..' segments and empty segments are refused "
                "outright, never normalised)" % (artifact,))
    rc_repo, _o, err_repo = _git(root, ["rev-parse", "--git-dir"])
    if rc_repo != 0:
        _refuse("(5) introducing commit",
                "%s is not a git repository, so the artifact cannot have committed "
                "history to verify: %s" % (root, err_repo.strip()))
    sha = _introducing_commit(root, artifact_rel)
    if sha is None:
        _refuse("(5) introducing commit",
                "no commit in HEAD's history ADDED %s -- an artifact that exists "
                "only in the working tree (or only on an unmerged branch) is "
                "exactly the 'fabricated this run' case this check kills"
                % artifact_rel)
    if not _SHA_RE.match(sha):
        _refuse("(5) introducing commit",
                "git reported a malformed introducing-commit id %r for %s"
                % (sha, artifact_rel))
    say("  (5) introducing commit     ok  -- %s added by %s" % (artifact_rel, sha))

    # ---------------------------------------------------------------- check 6
    # Signature, verified ONLY against a temp allowed-signers file built from the
    # pinned public keys. Three DISTINCT refusals, never collapsed into one:
    # "there was no signature", "there was a signature and it is broken", and
    # "there was a good signature by a key this instance does not trust" are three
    # different facts about the world and a reader of the refusal must be able to
    # tell them apart (they imply three different responses).
    status, fp = _verify_signature(root, sha, config)
    if status is None:
        _refuse("(6a) signature: unverifiable",
                "git could not report a signature verdict for commit %s" % sha)
    if status in ("N", "") and not fp:
        _refuse("(6a) signature: unsigned commit",
                "commit %s (which added %s) carries NO signature (%%G? = %r). "
                "Temporal separation is not approval: an attacker-directed EARLIER "
                "run can commit an artifact. The boundary is a key sessions never "
                "hold." % (sha, artifact_rel, status))
    if status == "B":
        _refuse("(6b) signature: bad signature",
                "commit %s carries a signature that does NOT verify over its own "
                "content (%%G? = 'B') -- a spliced or tampered commit object"
                % sha)
    if status == "G" and fp in pinned_fps:
        pass
    elif fp:
        _refuse("(6c) signature: unpinned key",
                "commit %s carries a signature by key %s (%%G? = %r), which is NOT "
                "in this instance's pinned operator_signing_keys %s -- a good "
                "signature by the wrong hand is still not the operator's hand"
                % (sha, fp, status, sorted(pinned_fps)))
    else:
        _refuse("(6a) signature: unverifiable",
                "commit %s produced signature status %r with no reportable key "
                "fingerprint -- refusing rather than guessing" % (sha, status))
    say("  (6) signature verified     ok  -- %%G?=G, key %s (pinned)" % fp)

    # ---------------------------------------------------------------- check 7
    # Verified-blob binding. The bytes checked in check 8 are THE SAME OBJECT the
    # signature in check 6 covers. The working tree is never read; a later commit
    # is never read; therefore a post-introduction modification has no path by
    # which to take effect at all -- it is not detected and rejected, it is simply
    # never looked at.
    ok_blob, blob = _show_blob(root, sha, artifact_rel)
    if not ok_blob:
        _refuse("(7) verified-blob read",
                "could not read %s from the VERIFIED commit %s: %s (the working "
                "tree is never a fallback)" % (artifact_rel, sha, blob))
    say("  (7) verified-blob read     ok  -- %d bytes from %s:%s"
        % (len(blob.encode("utf-8")), sha, artifact_rel))

    # ---------------------------------------------------------------- check 8
    st_c, val_c = parse_binding(blob, _BIND_CANDIDATE_KEY)
    if st_c == "missing":
        _refuse("(8) artifact binding",
                "the verified blob of %s contains no `%s:` line -- a gate artifact "
                "must name the ONE candidate it arms, explicitly"
                % (artifact_rel, _BIND_CANDIDATE_KEY))
    if st_c == "ambiguous":
        _refuse("(8) artifact binding",
                "the verified blob of %s contains an AMBIGUOUS `%s:` binding "
                "(multiple lines, or an unbalanced quote) -- no blanket or "
                "multi-candidate authorizations" % (artifact_rel, _BIND_CANDIDATE_KEY))
    if val_c != str(candidate_id):
        _refuse("(8) artifact binding",
                "the verified blob of %s arms candidate_id %r, not %r -- exact "
                "match only: no wildcards, no prefixes, no 'all candidates from "
                "session X'" % (artifact_rel, val_c, candidate_id))
    st_d, val_d = parse_binding(blob, _BIND_DISPOSITION_KEY)
    if st_d == "missing":
        _refuse("(8) artifact binding",
                "the verified blob of %s contains no `%s:` line"
                % (artifact_rel, _BIND_DISPOSITION_KEY))
    if st_d == "ambiguous":
        _refuse("(8) artifact binding",
                "the verified blob of %s contains an AMBIGUOUS `%s:` binding"
                % (artifact_rel, _BIND_DISPOSITION_KEY))
    if val_d != REQUIRED_DISPOSITION:
        _refuse("(8) artifact binding",
                "the verified blob of %s approves disposition %r, not %r"
                % (artifact_rel, val_d, REQUIRED_DISPOSITION))
    say("  (8) artifact binding       ok  -- names %s + %s"
        % (candidate_id, REQUIRED_DISPOSITION))

    # ---------------------------------------------------------------- check 9
    # Temporal, retained as DEFENCE IN DEPTH (the signature is the real control).
    # STRICTLY predate: an artifact committed in the same instant this run began
    # is not established as pre-existing, and the conservative reading of a tie is
    # refusal.
    ct = _committer_epoch(root, sha)
    if ct is None:
        _refuse("(9) temporal ordering",
                "could not read the committer timestamp of %s" % sha)
    if not ct < run_started_at:
        _refuse("(9) temporal ordering",
                "commit %s has committer timestamp %d, which does not strictly "
                "predate the start of this run (%d) -- an artifact created during "
                "or after this run is a same-run fabrication candidate"
                % (sha, ct, int(run_started_at)))
    say("  (9) temporal ordering      ok  -- committed %d < run start %d"
        % (ct, int(run_started_at)))

    # --------------------------------------------------------------- check 10
    # Single-use, over the FULL history (ledger_history, not the effective map):
    # a consumption record that has since been superseded still consumed the
    # artifact. Matching on EITHER the path OR the verified sha -- each on its own
    # is sufficient evidence of prior consumption, and requiring both to match
    # would let a rename or a re-add slip a second use through.
    _single_use_scan(root, artifact_rel, sha)
    say("  (10) single-use            ok  -- artifact not previously consumed "
        "(re-checked under the promotion lock before consumption)")

    # ============================================================ PROMOTION
    # Everything from here to the ledger append runs UNDER THE PROMOTION LOCK
    # (repo-grounded round finding, 2026-07-23): the pre-lock checks 1-10 gave
    # fast unlocked feedback, but the AUTHORITATIVE eligibility + single-use
    # verdicts are the re-checks below, taken while no other promotion can be
    # between its own check and its own append. Without this, two concurrent
    # promotions could both observe "state registered, artifact unconsumed" and
    # both append -- duplicate consumption records no correction can retract.
    #
    # WRITE ORDER (unchanged inside the lock): raw file FIRST, ledger record
    # SECOND -- deliberately, and for
    # the same reason the harvester writes its candidate file before its `staged`
    # record (build-decisions Part 5). A crash between the two leaves a raw file
    # with no `promoted` record: a VISIBLE conservation violation that the census
    # names and a re-run converges (the ledger still says `registered`, the
    # artifact is still unconsumed, and the re-run recognises its own byte-
    # identical raw file and completes the append). The opposite order would leave
    # a `promoted` record pointing at a file that was never written -- a LIE in the
    # permanent, append-only ledger, which no re-run can retract because
    # corrections are new records, never edits. A visible gap beats an
    # unretractable falsehood.
    slug = _safe_slug(meta["candidate_class"], meta["candidate_id"])
    raw_rel = "%s/%s-%s-%s.md" % (RAW_DIR, str(meta["harvested_at"])[:10],
                                   op_prefix, slug)
    raw_text = render_raw_event(meta, span, artifact_rel, sha, op_prefix)
    raw_abs = os.path.join(root, raw_rel.replace("/", os.sep))
    with _PromotionLock(root):
        # authoritative re-checks, now race-free: eligibility (check 1's state
        # test -- a concurrent promotion of the SAME candidate has flipped it to
        # `promoted`) and single-use (check 10 -- a concurrent promotion via the
        # same artifact/commit has consumed it).
        state_now = _cands.effective_state(root, str(candidate_id))
        if state_now != "registered":
            _refuse("(1) candidate eligibility",
                    "candidate %r changed state to %r between the pre-checks and "
                    "the locked consumption step (a concurrent promotion won the "
                    "race) -- refusing; nothing was written by THIS run"
                    % (candidate_id, state_now))
        _single_use_scan(root, artifact_rel, sha)
        return _promote_locked(root, candidate_id, meta, span, artifact_rel, sha,
                               fp, op_prefix, raw_rel, raw_text, raw_abs,
                               event_rel, say)


def _promote_locked(root, candidate_id, meta, span, artifact_rel, sha, fp,
                    op_prefix, raw_rel, raw_text, raw_abs, event_rel, say):
    """The consumption step, called ONLY under _PromotionLock (see promote())."""
    if os.path.exists(raw_abs):
        # Convergence, not overwrite. Byte-identical => this is THIS promotion's
        # own crashed first attempt (render_raw_event is deterministic precisely so
        # that this comparison is meaningful); anything else => refuse, because
        # silently overwriting an existing raw event would be a write authority
        # over truth this module must never have.
        try:
            with open(raw_abs, "r", encoding="utf-8", newline="") as fh:
                existing = fh.read()
        except OSError as e:
            _refuse("(11) raw-event write",
                    "%s already exists and could not be read: %s" % (raw_rel, e))
        if existing != raw_text:
            _refuse("(11) raw-event write",
                    "%s already exists with DIFFERENT content -- refusing to "
                    "overwrite an existing raw event" % raw_rel)
        say("  (--) raw event             ok  -- %s already present, byte-identical "
            "(converging a crashed prior run)" % raw_rel)
    else:
        _write_raw_atomic(root, raw_rel, raw_text)
        say("  (--) raw event written     ok  -- %s" % raw_rel)

    rec = _cands.new_record(
        meta, "promoted",
        "promoted to %s under gate artifact %s, signature-verified introducing "
        "commit %s (pinned key %s)" % (raw_rel, artifact_rel, sha, fp),
        event_rel, gate_artifact=artifact_rel, gate_commit=sha)
    seq, _path = _cands.append_candidate_record(root, rec)
    say("  (--) ledger record appended ok -- seq %d, state promoted" % seq)

    return {"candidate_id": str(candidate_id), "raw_event": raw_rel,
            "gate_artifact": artifact_rel, "gate_commit": sha,
            "fingerprint": fp, "seq": seq}


# ------------------------------------------------------------------ self-test
def self_test():
    import shutil as _shutil

    total = failed = skipped = 0

    def case(name, ok):
        nonlocal total, failed
        total += 1
        print("  %s %s" % ("ok " if ok else "XX ", name))
        if not ok:
            failed += 1

    def skip(name, note):
        nonlocal skipped
        skipped += 1
        print("  -- %s (SKIPPED: %s)" % (name, note))

    tools_ok, missing_tool = tools_available()
    if not tools_ok:
        print("promote-candidate self-test: INCONCLUSIVE -- %s unavailable"
              % missing_tool)
        return 2
    if yaml is None:
        print("promote-candidate self-test: INCONCLUSIVE -- PyYAML unavailable")
        return 2

    roots = []
    keydir = tempfile.mkdtemp(prefix="r1-promote-keys-")

    # The suite's [ryan] origin-config FIXTURE. Held in a one-element list so the
    # nested attempt() closure can read it as a default without a `nonlocal`
    # dance. NOTHING in this suite ever reads the live deploy/origin-config.yaml
    # -- hermeticity (acceptance criterion 5) covers the origin config exactly as
    # it covers the signing config.
    _ORIGIN_CFG_FIXTURE = [_mk_origin_cfg(keydir)]

    # ---------------------------------------------------------------- fixtures
    def mkroot(tag):
        """A fresh temp git repo per case (never the live repo, never a shared
        one). commit.gpgsign is forced OFF and gpg.format pinned so that a GLOBAL
        user config cannot silently sign the commits this suite needs to be
        UNSIGNED -- a self-test that accidentally signs its 'unsigned' fixture
        would prove the opposite of what it claims."""
        base = tempfile.mkdtemp(prefix="r1-promote-%s-" % tag)
        roots.append(base)
        for args in (["init", "-q"],
                     ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"],
                     ["config", "commit.gpgsign", "false"],
                     ["config", "tag.gpgsign", "false"],
                     ["config", "gpg.format", "ssh"]):
            _git(base, args)
        return base

    def keypair(name):
        """A REAL ed25519 keypair via ssh-keygen (never a hardcoded fixture key,
        and never the operator's). Returns (pub_path, pub_line, fingerprint)."""
        path = os.path.join(keydir, name)
        rc, _o, err = _run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", path, "-q"])
        if rc != 0:
            raise RuntimeError("ssh-keygen failed: %s" % err)
        with open(path + ".pub", "r", encoding="utf-8") as fh:
            line = fh.read().strip()
        return path + ".pub", line, ssh_keygen_fingerprint(line)

    def write_cfg(root, entries, mode="ssh", raw_text=None):
        """A signing-config FIXTURE inside the case's own temp root. The live
        deploy/signing-config.yaml is never read by this suite."""
        p = os.path.join(root, "signing-config-fixture.yaml")
        if raw_text is not None:
            body = raw_text
        else:
            body = "signature_mode: %s\noperator_signing_keys:\n" % mode
            if not entries:
                body = "signature_mode: %s\noperator_signing_keys: []\n" % mode
            for e in entries:
                body += ('  - fingerprint: "%s"\n    public_key: "%s"\n'
                         '    principal: "%s"\n'
                         % (e["fingerprint"], e["public_key"], e["principal"]))
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        return p

    def stage_candidate(root, span, session_id="sess-r1d", origin_max="corpus",
                        disposition=REQUIRED_DISPOSITION,
                        candidate_class="decision-stated", state="registered",
                        harvested_at="2026-07-20T09:00:00"):
        """Stage a candidate file and drive it to `state` through the REAL ledger
        (staged -> registered), so eligibility is proven against the same code
        path register-candidates.py will use."""
        meta = {
            "kind": "session-candidate",
            "candidate_id": _cands.make_candidate_id(session_id, span),
            "session_id": session_id,
            "candidate_class": candidate_class,
            "origin": "session-derived",
            "origin_max": origin_max,
            "proposed_disposition": disposition,
            "transcript_pointer": "self-test fixture",
            "harvested_at": harvested_at,
            "span_sha256": _cands.span_hash(span),
        }
        abs_path = _cands.write_candidate_atomic(root, meta, span)
        rel = os.path.relpath(abs_path, root).replace("\\", "/")
        _cands.append_candidate_record(
            root, _cands.new_record(meta, "staged", "self-test harvest", rel))
        if state != "staged":
            _cands.append_candidate_record(
                root, _cands.new_record(meta, state, "self-test register", rel))
        return meta, rel

    PAST = "@%d +0000" % (int(time.time()) - 86400)
    FUTURE = "@%d +0000" % (int(time.time()) + 86400)

    def commit_file(root, rel, text, signing_key=None, when=PAST, msg="fixture"):
        """Write `rel` and commit it. `signing_key` None => genuinely UNSIGNED."""
        p = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        _git(root, ["add", "--", ":(literal)" + rel])
        cmd = ["git", "-C", root]
        if signing_key:
            cmd += ["-c", "gpg.format=ssh",
                    "-c", "user.signingkey=%s" % _gitpath(signing_key)]
        cmd += ["commit", "-q", "-m", msg]
        if signing_key:
            cmd += ["-S"]
        else:
            cmd += ["--no-gpg-sign"]
        env = dict(os.environ)
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
        try:
            r = subprocess.run(cmd, capture_output=True, env=env)
        except OSError as e:
            raise RuntimeError("git commit failed: %s" % e)
        if r.returncode != 0:
            raise RuntimeError("git commit failed: %s"
                               % r.stderr.decode("utf-8", "replace"))
        rc, out, _e = _git(root, ["log", "-1", "--format=%H"])
        return out.strip()

    def armed_text(cid, disposition=REQUIRED_DISPOSITION, extra=""):
        return ("# Operator gate artifact\n\n"
                "This artifact arms exactly one candidate.\n\n"
                "candidate_id: %s\n"
                "approved_disposition: %s\n%s" % (cid, disposition, extra))

    def attempt(root, cid, artifact, cfg, run_started_at=None,
                origin_cfg=None):
        """('OK', info) | ('REFUSED', exc) | ('INCONCLUSIVE', exc).

        origin_cfg defaults to the suite's [ryan] FIXTURE config, never the live
        deploy/origin-config.yaml -- hermeticity (acceptance criterion 5) covers
        the origin config exactly as it covers the signing config."""
        try:
            info = promote(root, cid, artifact, signing_config_path=cfg,
                           run_started_at=run_started_at,
                           origin_config_path=(origin_cfg if origin_cfg is not None
                                               else _ORIGIN_CFG_FIXTURE[0]))
            return "OK", info
        except PromotionRefused as e:
            return "REFUSED", e
        except Inconclusive as e:
            return "INCONCLUSIVE", e

    def refused(label, res, check_frag, why_frags=()):
        """Assert the refusal fired AT THE NAMED CHECK and that its message says
        the expected thing. Asserting only on 'it refused' would pass when the
        WRONG check fired -- which on this module is worse than no test."""
        kind, e = res
        if kind != "REFUSED":
            case(label + " [expected REFUSED, got %s]" % kind, False)
            return None
        line = refusal_line(e)
        ok = check_frag in e.check and all(f in line for f in why_frags)
        case("%s  [%s]" % (label, e.check), ok)
        if not ok:
            print("       message was: %s" % line)
        return e

    try:
        pubA, lineA, fpA = keypair("operator")
        pubB, lineB, fpB = keypair("impostor")
        case("(fixture) two REAL ed25519 keypairs generated with distinct "
             "fingerprints",
             bool(fpA) and bool(fpB) and fpA != fpB)
        ENTRY_A = {"fingerprint": fpA, "public_key": lineA,
                   "principal": "operator@example.com"}
        ENTRY_B = {"fingerprint": fpB, "public_key": lineB,
                   "principal": "impostor@example.com"}

        # ============================================================ loader
        cfg_root = mkroot("cfgunit")
        good_cfg = write_cfg(cfg_root, [ENTRY_A])
        case("load_signing_config: a well-formed ssh config yields exactly the "
             "pinned entry",
             [e["fingerprint"] for e in
              load_signing_config(good_cfg)["operator_signing_keys"]] == [fpA])

        missing_path = os.path.join(cfg_root, "no-such-signing-config.yaml")
        case("load_signing_config: ABSENT file -> empty (fail-safe)",
             load_signing_config(missing_path)["operator_signing_keys"] == [])

        cfg_list = write_cfg(cfg_root, [], raw_text="- just\n- a\n- list\n")
        case("load_signing_config: top-level not a mapping -> empty",
             load_signing_config(cfg_list)["operator_signing_keys"] == [])

        cfg_yamlbad = write_cfg(cfg_root, [], raw_text="signature_mode: [ssh\n")
        case("load_signing_config: YAML-hostile file -> empty, never a crash",
             load_signing_config(cfg_yamlbad)["operator_signing_keys"] == [])

        cfg_empty = write_cfg(cfg_root, [])
        case("load_signing_config: operator_signing_keys: [] -> empty",
             load_signing_config(cfg_empty)["operator_signing_keys"] == [])

        cfg_notlist = write_cfg(cfg_root, [], raw_text=(
            "signature_mode: ssh\noperator_signing_keys: not-a-list\n"))
        case("load_signing_config: operator_signing_keys not a list -> empty",
             load_signing_config(cfg_notlist)["operator_signing_keys"] == [])

        cfg_notdict = write_cfg(cfg_root, [], raw_text=(
            "signature_mode: ssh\noperator_signing_keys:\n  - just-a-string\n"))
        case("load_signing_config: a non-dict member -> ENTIRE config empty",
             load_signing_config(cfg_notdict)["operator_signing_keys"] == [])

        cfg_missingkey = write_cfg(cfg_root, [], raw_text=(
            'signature_mode: ssh\noperator_signing_keys:\n'
            '  - fingerprint: "%s"\n    public_key: "%s"\n' % (fpA, lineA)))
        case("load_signing_config: a member missing `principal` -> ENTIRE config "
             "empty", load_signing_config(cfg_missingkey)["operator_signing_keys"] == [])

        cfg_emptyval = write_cfg(cfg_root, [
            {"fingerprint": fpA, "public_key": lineA, "principal": "   "}])
        case("load_signing_config: a member with an empty/whitespace value -> "
             "ENTIRE config empty",
             load_signing_config(cfg_emptyval)["operator_signing_keys"] == [])

        cfg_twoone_bad = write_cfg(cfg_root, [
            ENTRY_A, {"fingerprint": fpB, "public_key": lineB, "principal": ""}])
        case("load_signing_config: one bad member voids the GOOD one too (a "
             "partially-honored trust config is worse than an empty one)",
             load_signing_config(cfg_twoone_bad)["operator_signing_keys"] == [])

        cfg_fpmismatch = write_cfg(cfg_root, [
            {"fingerprint": fpB, "public_key": lineA,
             "principal": "operator@example.com"}])
        case("load_signing_config: fingerprint that is NOT the fingerprint of its "
             "own public_key -> ENTIRE config empty (consistency gate)",
             load_signing_config(cfg_fpmismatch)["operator_signing_keys"] == [])

        cfg_gpg = write_cfg(cfg_root, [ENTRY_A], mode="gpg")
        case("load_signing_config: signature_mode 'gpg' (unimplemented here) -> "
             "empty keys, mode still reported",
             load_signing_config(cfg_gpg)["operator_signing_keys"] == []
             and load_signing_config(cfg_gpg)["signature_mode"] == "gpg")

        cfg_badfpshape = write_cfg(cfg_root, [
            {"fingerprint": "MD5:aa:bb", "public_key": lineA, "principal": "x@y"}])
        case("load_signing_config: fingerprint outside the SHA256:<b64> shape -> "
             "empty", load_signing_config(cfg_badfpshape)["operator_signing_keys"] == [])

        cfg_wsprincipal = write_cfg(cfg_root, [
            {"fingerprint": fpA, "public_key": lineA,
             "principal": "two tokens"}])
        case("load_signing_config: a principal with embedded whitespace (would "
             "restructure the allowed-signers line) -> empty",
             load_signing_config(cfg_wsprincipal)["operator_signing_keys"] == [])

        _saved_yaml = globals()["yaml"]
        globals()["yaml"] = None
        try:
            case("load_signing_config: PyYAML unavailable -> empty (fail-safe, "
                 "never a crash)",
                 load_signing_config(good_cfg)["operator_signing_keys"] == [])
        finally:
            globals()["yaml"] = _saved_yaml

        # ============================================================ parse_binding
        case("parse_binding: plain value",
             parse_binding("candidate_id: abc-123\n", "candidate_id") == ("ok", "abc-123"))
        case("parse_binding: surrounding whitespace + quotes tolerated",
             parse_binding('   candidate_id:  "abc-123"  \n', "candidate_id")
             == ("ok", "abc-123"))
        case("parse_binding: unbalanced quote is AMBIGUOUS (refused), never kept",
             parse_binding('candidate_id: "abc-123\n', "candidate_id")[0] == "ambiguous")
        case("parse_binding: two lines for one key is AMBIGUOUS (no blanket "
             "authorizations)",
             parse_binding("candidate_id: a\ncandidate_id: b\n", "candidate_id")[0]
             == "ambiguous")
        case("parse_binding: absent key is `missing`",
             parse_binding("nothing here\n", "candidate_id")[0] == "missing")
        case("parse_binding: a trailing comment is NOT stripped (value differs, so "
             "the caller's == refuses it)",
             parse_binding("candidate_id: abc # note\n", "candidate_id")
             == ("ok", "abc # note"))
        case("parse_binding: CRLF line endings tolerated",
             parse_binding("candidate_id: abc-123\r\n", "candidate_id")
             == ("ok", "abc-123"))

        # ============================================================ safe paths
        case("safe_repo_relative refuses absolute / drive / .. / empty segments",
             safe_repo_relative("/etc/passwd") is None
             and safe_repo_relative("C:/Windows/x") is None
             and safe_repo_relative("deploy/../../etc/x") is None
             and safe_repo_relative("deploy//x") is None
             and safe_repo_relative("") is None)
        case("safe_repo_relative normalises backslashes and a leading ./",
             safe_repo_relative(".\\deploy\\evidence\\a.md")
             == "deploy/evidence/a.md")
        case("_safe_slug is [a-z0-9-] only, capped, never built from span text",
             re.match(r"^[a-z0-9-]+$",
                      _safe_slug("decision-stated", "sess1234-abcdef0123456789"))
             is not None
             and len(_safe_slug("x" * 200, "y" * 200)) <= _SLUG_CAP)

        # ============================================================ HAPPY PATH
        SPAN = ("We are going with the reversible-cutover posture: everything on a "
                "branch, nothing merges itself.")
        rootH = mkroot("happy")
        cfgH = write_cfg(rootH, [ENTRY_A])
        metaH, _relH = stage_candidate(rootH, SPAN)
        cidH = metaH["candidate_id"]
        artH = "deploy/evidence/operator-armed-1.md"
        shaH = commit_file(rootH, artH, armed_text(cidH), signing_key=pubA)
        kind, info = attempt(rootH, cidH, artH, cfgH)
        case("HAPPY PATH: a correctly armed artifact PROMOTES (a gate that refuses "
             "everything is not a gate)", kind == "OK")
        if kind == "OK":
            raw_abs = os.path.join(rootH, info["raw_event"].replace("/", os.sep))
            case("HAPPY PATH: the raw event appears at raw/<date>-ryan-<slug>.md",
                 os.path.isfile(raw_abs)
                 and info["raw_event"].startswith("raw/2026-07-20-ryan-"))
            body = open(raw_abs, encoding="utf-8", newline="").read()
            case("HAPPY PATH: the raw event carries the span VERBATIM", SPAN in body)
            case("HAPPY PATH: the raw event's provenance block names the candidate "
                 "id, the gate artifact and the verified commit sha",
                 ("- candidate_id: %s" % cidH) in body
                 and ("- gate_artifact: %s" % artH) in body
                 and ("- gate_commit: %s" % shaH) in body)
            case("HAPPY PATH: the ledger effective state becomes `promoted`",
                 _cands.effective_state(rootH, cidH) == "promoted")
            promoted_rec = [r for r in _cands.ledger_history(rootH)
                            if r["state"] == "promoted"]
            case("HAPPY PATH: the promoted record carries gate_artifact + "
                 "gate_commit",
                 len(promoted_rec) == 1
                 and promoted_rec[0]["gate_artifact"] == artH
                 and promoted_rec[0]["gate_commit"] == shaH)
            case("HAPPY PATH: the promoted record keeps origin `session-derived` "
                 "(the LEDGER never mints human; the raw filename is what the "
                 "origin rule reads)",
                 promoted_rec and promoted_rec[0]["origin"] == "session-derived")
            case("HAPPY PATH: origin.assign_origin on the written raw path yields "
                 "`human` (the mint this gate exists to authorise), using a "
                 "fixture origin-config, never the live one",
                 _origin.assign_origin(
                     info["raw_event"], True,
                     origin_config_path=_mk_origin_cfg(cfg_root))[0] == "human")
        else:
            case("HAPPY PATH follow-ups (skipped: promotion did not succeed)", False)

        # ---- naive same-candidate double-use stops at check 1 -----------------
        # (cross-vendor round-1 finding, 2026-07-23: the spec's check-10 analysis
        # asserts this variant never reaches single-use, but no test proved it.)
        # Re-promoting the COMPLETED happy-path promotion refuses at check 1 --
        # effective state is `promoted`, not `registered`. Distinct from the
        # WRITE ORDER convergence re-run below, which legitimately proceeds
        # because a crash before the append leaves state `registered`.
        refused("RE-PROMOTING an already-promoted candidate: refused at check 1 "
                "(state `promoted`) -- naive same-candidate double-use never "
                "reaches check 10",
                attempt(rootH, cidH, artH, cfgH),
                "(1) candidate eligibility", ("promoted",))

        # ---- check 4b: the operator prefix is PER-INSTANCE CONFIG -------------
        # An engine constant here would be a bug the fork could never see (this
        # fork's live config happens to say [ryan]). Both directions are proven:
        # an UNCONFIGURED instance refuses, and a DIFFERENTLY-configured one
        # writes ITS operator's prefix.
        rootU = mkroot("unconfigured-prefix")
        cfgU = write_cfg(rootU, [ENTRY_A])
        metaU, _relU = stage_candidate(rootU, "an unconfigured-instance decision")
        cidU = metaU["candidate_id"]
        artU = "deploy/evidence/operator-armed-unconfigured.md"
        commit_file(rootU, artU, armed_text(cidU), signing_key=pubA)
        empty_origin_cfg = os.path.join(keydir, "origin-config-empty.yaml")
        with open(empty_origin_cfg, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("human_prefixes: []\n")
        refused("UNCONFIGURED operator prefix (no human_prefixes) -> refused, "
                "rather than writing a raw event that would mint `corpus` under "
                "a `promoted` ledger record",
                attempt(rootU, cidU, artU, cfgU, origin_cfg=empty_origin_cfg),
                "(4b) operator provenance prefix")
        case("  ...nothing was written to raw/ on the unconfigured instance",
             not os.path.isdir(os.path.join(rootU, "raw"))
             or os.listdir(os.path.join(rootU, "raw")) == [])
        case("  ...and the candidate is still `registered`, never `promoted`",
             _cands.effective_state(rootU, cidU) == "registered")

        rootZ = mkroot("alt-prefix")
        cfgZ = write_cfg(rootZ, [ENTRY_A])
        metaZ, _relZ = stage_candidate(rootZ, "an alt-operator instance decision")
        cidZ = metaZ["candidate_id"]
        artZ = "deploy/evidence/operator-armed-alt.md"
        commit_file(rootZ, artZ, armed_text(cidZ), signing_key=pubA)
        alt_origin_cfg = _mk_origin_cfg(keydir, prefix="alice")
        kindZ, infoZ = attempt(rootZ, cidZ, artZ, cfgZ, origin_cfg=alt_origin_cfg)
        case("ALT-PREFIX instance ([alice]) promotes and writes raw/<date>-ALICE-"
             "<slug>.md -- the prefix is instance config, not an engine constant",
             kindZ == "OK" and "-alice-" in (infoZ or {}).get("raw_event", ""))
        if kindZ == "OK":
            altbody = open(os.path.join(rootZ,
                                        infoZ["raw_event"].replace("/", os.sep)),
                           encoding="utf-8", newline="").read()
            case("ALT-PREFIX: the raw event's `source:` field is alice too (not a "
                 "hardcoded name)", "source: alice" in altbody)
            case("ALT-PREFIX: origin.assign_origin on THAT path yields `human` "
                 "under the [alice] fixture config",
                 _origin.assign_origin(infoZ["raw_event"], True,
                                       origin_config_path=alt_origin_cfg)[0]
                 == "human")

        # ---- single-use, in the SAME root, against the SAME artifact ----------
        # (a) THE SPEC-WORDED CASE: a DIFFERENT candidate against the same
        # artifact. It refuses -- but at check (8), not (10), because the checks
        # run in the spec's order and the binding check is BOTH earlier AND
        # strictly stronger for this variant: an artifact whose signed blob names
        # candidate X cannot arm candidate Y at all, consumed or not. Asserting
        # the exact check that fired (rather than merely "it refused") is the
        # point of this whole harness -- so this case asserts (8), honestly.
        meta2, _rel2 = stage_candidate(rootH, SPAN + " (a second, different span)",
                                       session_id="sess-r1d2")
        cid2 = meta2["candidate_id"]
        refused("SINGLE-USE (a): a DIFFERENT candidate against the SAME artifact is "
                "refused -- at the BINDING check, which is earlier and stronger "
                "here (the signed blob names the first candidate)",
                attempt(rootH, cid2, artH, cfgH),
                "(8) artifact binding", ("exact match only",))
        case("SINGLE-USE (a): the second candidate is still `registered` (nothing "
             "promoted)", _cands.effective_state(rootH, cid2) == "registered")

        # (b) THE CASE THAT ACTUALLY REACHES CHECK 10. Single-use bites when
        # checks 1-9 ALL pass a second time, and the only way that happens is a
        # superseding `registered` record for the ALREADY-PROMOTED candidate --
        # which the ledger explicitly permits (corrections are new records, never
        # edits), so this is a real reachable state, not a contrived one. Without
        # check 10 this would re-promote: same candidate, same artifact, same
        # signature, all still valid.
        _cands.append_candidate_record(
            rootH, _cands.new_record(metaH, "registered",
                                     "self-test: superseding correction that "
                                     "re-opens an already-promoted candidate",
                                     _relH))
        case("SINGLE-USE (b) setup: a superseding `registered` record re-opens the "
             "already-promoted candidate (checks 1-9 will all pass again)",
             _cands.effective_state(rootH, cidH) == "registered")
        e = refused("SINGLE-USE (b): the SECOND promotion of the SAME candidate "
                    "against the SAME already-consumed artifact is refused",
                    attempt(rootH, cidH, artH, cfgH),
                    "(10) single-use", ("ALREADY CONSUMED",))
        if e is not None:
            case("SINGLE-USE (b): the refusal CITES the consuming record's seq",
                 re.search(r"record seq \d+", str(e)) is not None)

        # ============================================================ no artifact
        rootN = mkroot("noartifact")
        cfgN = write_cfg(rootN, [ENTRY_A])
        metaN, _ = stage_candidate(rootN, SPAN)
        commit_file(rootN, "README.md", "seed\n")   # a repo with history, but no artifact
        refused("NO ARTIFACT AT ALL: refused",
                attempt(rootN, metaN["candidate_id"],
                        "deploy/evidence/operator-does-not-exist.md", cfgN),
                "(5) introducing commit", ("no commit in HEAD's history ADDED",))
        case("NO ARTIFACT: nothing was written to raw/",
             not os.path.isdir(os.path.join(rootN, "raw")))

        # ==================================================== working tree only
        rootW = mkroot("worktree")
        cfgW = write_cfg(rootW, [ENTRY_A])
        metaW, _ = stage_candidate(rootW, SPAN)
        commit_file(rootW, "README.md", "seed\n")
        artW = "deploy/evidence/operator-fabricated-this-run.md"
        pW = os.path.join(rootW, artW.replace("/", os.sep))
        os.makedirs(os.path.dirname(pW), exist_ok=True)
        with open(pW, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(armed_text(metaW["candidate_id"]))
        refused("WORKING-TREE ONLY (never committed): refused",
                attempt(rootW, metaW["candidate_id"], artW, cfgW),
                "(5) introducing commit", ("only in the working tree",))
        case("WORKING-TREE ONLY: nothing written to raw/",
             not os.path.isdir(os.path.join(rootW, "raw")))

        # ==================================================== unsigned commit
        # THE ROUND-3 ATTACK: an artifact committed by an EARLIER run. Temporal
        # separation alone would pass it; only the signature stops it.
        rootU = mkroot("unsigned")
        cfgU = write_cfg(rootU, [ENTRY_A])
        metaU, _ = stage_candidate(rootU, SPAN)
        artU = "deploy/evidence/operator-earlier-run-unsigned.md"
        commit_file(rootU, artU, armed_text(metaU["candidate_id"]), signing_key=None)
        e_unsigned = refused(
            "UNSIGNED COMMIT (the round-3 attack: an earlier-run session-committed "
            "artifact): refused",
            attempt(rootU, metaU["candidate_id"], artU, cfgU),
            "signature: unsigned", ("NO signature",))
        case("UNSIGNED: nothing written to raw/",
             not os.path.isdir(os.path.join(rootU, "raw")))
        case("UNSIGNED: candidate still `registered`",
             _cands.effective_state(rootU, metaU["candidate_id"]) == "registered")

        # ==================================================== non-pinned key
        rootP = mkroot("unpinned")
        cfgP = write_cfg(rootP, [ENTRY_A])          # pins A; artifact signed by B
        metaP, _ = stage_candidate(rootP, SPAN)
        artP = "deploy/evidence/operator-signed-by-impostor.md"
        commit_file(rootP, artP, armed_text(metaP["candidate_id"]), signing_key=pubB)
        e_unpinned = refused(
            "NON-PINNED KEY (a good signature by the wrong hand): refused",
            attempt(rootP, metaP["candidate_id"], artP, cfgP),
            "signature: unpinned key", ("NOT in this instance's pinned",))
        if e_unsigned is not None and e_unpinned is not None:
            case("NON-PINNED vs UNSIGNED are DISTINCT, separately-named refusals",
                 e_unsigned.check != e_unpinned.check
                 and "unsigned" in e_unsigned.check
                 and "unpinned" in e_unpinned.check)
            case("NON-PINNED refusal names the offending key fingerprint",
                 fpB in str(e_unpinned))
        case("NON-PINNED: nothing written to raw/",
             not os.path.isdir(os.path.join(rootP, "raw")))

        # ============================ hostile REPO git config (the Part 6 amendment)
        # The amendment's whole claim is that the promoter does not trust repo git
        # config AT ALL. Both tampering directions are proven here, against a repo
        # whose own gpg.ssh.allowedSignersFile is adversarial.
        rootG1 = mkroot("repocfg-adds-own-key")
        cfgG1 = write_cfg(rootG1, [ENTRY_A])          # instance pins A
        hostile = os.path.join(rootG1, "hostile-allowed-signers.txt")
        with open(hostile, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("impostor@example.com %s\n" % lineB)   # session trusts ITSELF
        _git(rootG1, ["config", "gpg.ssh.allowedSignersFile", _gitpath(hostile)])
        metaG1, _ = stage_candidate(rootG1, SPAN)
        artG1 = "deploy/evidence/operator-repo-config-attack.md"
        commit_file(rootG1, artG1, armed_text(metaG1["candidate_id"]),
                    signing_key=pubB)
        refused("REPO CONFIG TAMPER (a session adds its OWN key to the repo's "
                "gpg.ssh.allowedSignersFile and signs with it): still refused, "
                "because %GF is outside the PINNED set",
                attempt(rootG1, metaG1["candidate_id"], artG1, cfgG1),
                "signature: unpinned key", (fpB,))

        rootG2 = mkroot("repocfg-hostile-but-operator-signed")
        cfgG2 = write_cfg(rootG2, [ENTRY_A])
        junk = os.path.join(rootG2, "junk-allowed-signers.txt")
        with open(junk, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("impostor@example.com %s\n" % lineB)   # operator's key ABSENT
        _git(rootG2, ["config", "gpg.ssh.allowedSignersFile", _gitpath(junk)])
        metaG2, _ = stage_candidate(rootG2, SPAN)
        artG2 = "deploy/evidence/operator-armed-despite-repo-config.md"
        shaG2 = commit_file(rootG2, artG2, armed_text(metaG2["candidate_id"]),
                            signing_key=pubA)
        kindG2, infoG2 = attempt(rootG2, metaG2["candidate_id"], artG2, cfgG2)
        case("REPO CONFIG TAMPER (a session points the repo's allowed-signers at a "
             "file WITHOUT the operator's key): the promotion still SUCCEEDS -- the "
             "promoter never read repo config, it built its own allowed-signers "
             "file from the pinned public_key",
             kindG2 == "OK" and infoG2.get("gate_commit") == shaG2)

        # ---- repo-local gpg.ssh.program tamper (cross-vendor round-2 finding) --
        # -c only shields the keys it names, and the first shipped override set
        # left gpg.ssh.program reachable: a session-written .git/config could
        # point verification at ANY program, poisoning the operator's own later
        # clean-environment run. Proven closed by precedence: the repo config
        # names a NONEXISTENT verifier program; if repo config were consulted at
        # all, verifying the properly-signed commit would error and the promotion
        # would refuse -- instead it succeeds, because the promoter's own
        # -c gpg.ssh.program=ssh-keygen wins.
        rootG3 = mkroot("repocfg-ssh-program")
        cfgG3 = write_cfg(rootG3, [ENTRY_A])
        metaG3, _ = stage_candidate(rootG3, SPAN)
        artG3 = "deploy/evidence/operator-armed-despite-ssh-program.md"
        shaG3 = commit_file(rootG3, artG3, armed_text(metaG3["candidate_id"]),
                            signing_key=pubA)
        # planted AFTER the operator's signed commit -- the real attack timeline
        # (a session cannot plant config into the past), and the fixture's own
        # signing step must not consult the planted program either
        _git(rootG3, ["config", "gpg.ssh.program",
                      "nonexistent-verifier-planted-by-session"])
        kindG3, infoG3 = attempt(rootG3, metaG3["candidate_id"], artG3, cfgG3)
        case("REPO CONFIG TAMPER (a session plants gpg.ssh.program in .git/config): "
             "the operator-signed promotion still SUCCEEDS -- the promoter's own "
             "-c gpg.ssh.program=ssh-keygen overrides the planted value, which is "
             "provably never consulted (it names a nonexistent program)",
             kindG3 == "OK" and infoG3.get("gate_commit") == shaG3)
        # ...and the same planted config does not make an UNSIGNED artifact pass.
        rootG4 = mkroot("repocfg-ssh-program-unsigned")
        cfgG4 = write_cfg(rootG4, [ENTRY_A])
        metaG4, _ = stage_candidate(rootG4, SPAN)
        artG4 = "deploy/evidence/operator-unsigned-despite-ssh-program.md"
        commit_file(rootG4, artG4, armed_text(metaG4["candidate_id"]),
                    signing_key=None)
        _git(rootG4, ["config", "gpg.ssh.program",
                      "nonexistent-verifier-planted-by-session"])
        refused("REPO CONFIG TAMPER (planted gpg.ssh.program) + UNSIGNED artifact: "
                "still refused as unsigned",
                attempt(rootG4, metaG4["candidate_id"], artG4, cfgG4),
                "signature: unsigned commit")

        # ==================================================== bad signature
        # A signature that does not verify over its own content: splice the signed
        # commit object's message and re-point HEAD at the spliced object.
        rootB = mkroot("badsig")
        cfgB = write_cfg(rootB, [ENTRY_A])
        metaB, _ = stage_candidate(rootB, SPAN)
        artB = "deploy/evidence/operator-spliced.md"
        shaB = commit_file(rootB, artB, armed_text(metaB["candidate_id"]),
                           signing_key=pubA, msg="original signed message")
        rc_cat, raw_obj, _e = _git(rootB, ["cat-file", "commit", shaB], decode=False)
        spliced = raw_obj.replace(b"original signed message", b"TAMPERED-message-xx")
        p_obj = os.path.join(keydir, "spliced-commit.txt")
        with open(p_obj, "wb") as fh:
            fh.write(spliced)
        rc_h, bad_sha, _e2 = _run(["git", "-C", rootB, "hash-object", "-t", "commit",
                                   "-w", p_obj])
        bad_sha = bad_sha.decode("utf-8", "replace").strip()
        if rc_cat == 0 and rc_h == 0 and bad_sha:
            _git(rootB, ["update-ref", "HEAD", bad_sha])
            res_b = attempt(rootB, metaB["candidate_id"], artB, cfgB)
            e_bad = refused("BAD SIGNATURE (spliced commit object): refused",
                            res_b, "signature: bad signature",
                            ("does NOT verify",))
            if e_bad is not None and e_unsigned is not None and e_unpinned is not None:
                case("BAD SIGNATURE is a THIRD distinct refusal (unsigned / bad / "
                     "unpinned are three named outcomes)",
                     len({e_bad.check, e_unsigned.check, e_unpinned.check}) == 3)
        else:
            skip("BAD SIGNATURE (spliced commit object)",
                 "could not splice a commit object in this environment")

        # ==================================================== wrong candidate id
        rootC = mkroot("wrongcid")
        cfgC = write_cfg(rootC, [ENTRY_A])
        metaC, _ = stage_candidate(rootC, SPAN)
        artC = "deploy/evidence/operator-arms-someone-else.md"
        commit_file(rootC, artC, armed_text("sess-xxxx-0123456789abcdef"),
                    signing_key=pubA)
        refused("ARTIFACT NAMES A DIFFERENT candidate_id: refused",
                attempt(rootC, metaC["candidate_id"], artC, cfgC),
                "(8) artifact binding", ("exact match only",))
        case("WRONG CID: nothing written to raw/",
             not os.path.isdir(os.path.join(rootC, "raw")))

        # ==================================================== wrong disposition
        rootD = mkroot("wrongdisp")
        cfgD = write_cfg(rootD, [ENTRY_A])
        metaD, _ = stage_candidate(rootD, SPAN)
        artD = "deploy/evidence/operator-wrong-disposition.md"
        commit_file(rootD, artD,
                    armed_text(metaD["candidate_id"], disposition="plain-intake"),
                    signing_key=pubA)
        refused("ARTIFACT APPROVES A DIFFERENT disposition: refused",
                attempt(rootD, metaD["candidate_id"], artD, cfgD),
                "(8) artifact binding", ("approves disposition",))

        # ==================================================== blanket authorization
        rootM = mkroot("multibind")
        cfgM = write_cfg(rootM, [ENTRY_A])
        metaM, _ = stage_candidate(rootM, SPAN)
        artM = "deploy/evidence/operator-blanket.md"
        commit_file(rootM, artM,
                    armed_text(metaM["candidate_id"],
                               extra="candidate_id: sess-yyyy-fedcba9876543210\n"),
                    signing_key=pubA)
        refused("BLANKET/MULTI-CANDIDATE artifact (two candidate_id lines): refused",
                attempt(rootM, metaM["candidate_id"], artM, cfgM),
                "(8) artifact binding", ("AMBIGUOUS",))

        # ==================================================== TOCTOU (4 variants)
        def toctou(tag, mode, rebind):
            """A validly-signed artifact, then modified. `mode` is 'worktree' or
            'commit' (a LATER UNSIGNED commit); `rebind` is 'cid' or 'disp'. The
            promotion outcome must be IDENTICAL to the unmodified signed blob --
            i.e. the modification has no effect whatsoever, because the modified
            bytes are never read."""
            r = mkroot("toctou-%s-%s" % (mode, rebind))
            cfg = write_cfg(r, [ENTRY_A])
            m, _rel = stage_candidate(r, SPAN)
            cid = m["candidate_id"]
            art = "deploy/evidence/operator-toctou.md"
            good_sha = commit_file(r, art, armed_text(cid), signing_key=pubA)
            if rebind == "cid":
                tampered = armed_text("sess-zzzz-0f0f0f0f0f0f0f0f")
            else:
                tampered = armed_text(cid, disposition="plain-intake")
            if mode == "worktree":
                p = os.path.join(r, art.replace("/", os.sep))
                with open(p, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(tampered)
            else:
                commit_file(r, art, tampered, signing_key=None,
                            msg="later UNSIGNED rebinding commit")
            kind_t, info_t = attempt(r, cid, art, cfg)
            label = ("TOCTOU %s / rebound %s: the modification has NO effect -- "
                     "promotion proceeds from the SIGNED blob"
                     % ("working tree only" if mode == "worktree"
                        else "later UNSIGNED commit", rebind))
            case(label, kind_t == "OK" and info_t.get("gate_commit") == good_sha)
            if kind_t == "OK":
                raw_p = os.path.join(r, info_t["raw_event"].replace("/", os.sep))
                txt = open(raw_p, encoding="utf-8", newline="").read()
                case("  ...and the written raw event binds the ORIGINAL candidate "
                     "id + the SIGNED commit (never the tampered values)",
                     ("- candidate_id: %s" % cid) in txt
                     and ("- gate_commit: %s" % good_sha) in txt
                     and "sess-zzzz-0f0f0f0f0f0f0f0f" not in txt)

        toctou("t1", "worktree", "cid")
        toctou("t2", "worktree", "disp")
        toctou("t3", "commit", "cid")
        toctou("t4", "commit", "disp")

        # ==================================================== taint
        for tainted in ("unknown", "external-scrape"):
            rt = mkroot("taint-%s" % tainted.replace("-", ""))
            cfgt = write_cfg(rt, [ENTRY_A])
            mt, _ = stage_candidate(rt, SPAN, origin_max=tainted)
            at = "deploy/evidence/operator-armed-tainted.md"
            commit_file(rt, at, armed_text(mt["candidate_id"]), signing_key=pubA)
            et = refused("TAINTED origin_max=%s refused EVEN WITH a fully valid "
                         "armed artifact" % tainted,
                         attempt(rt, mt["candidate_id"], at, cfgt),
                         "(3) taint", (tainted,))
            if et is not None:
                case("  ...refusal names BOTH the candidate id and the tainted "
                     "class", mt["candidate_id"] in str(et) and tainted in str(et))
            case("  ...nothing written to raw/ for the %s candidate" % tainted,
                 not os.path.isdir(os.path.join(rt, "raw")))

        # ==================================================== signing config
        for tag, cfg_maker, frag in (
            ("EMPTY", lambda r: write_cfg(r, []), "EMPTY"),
            ("ABSENT", lambda r: os.path.join(r, "nope-signing-config.yaml"), "EMPTY"),
            ("MALFORMED (top-level list)",
             lambda r: write_cfg(r, [], raw_text="- a\n- b\n"), "EMPTY"),
            ("FINGERPRINT/PUBLIC-KEY MISMATCH",
             lambda r: write_cfg(r, [{"fingerprint": fpB, "public_key": lineA,
                                      "principal": "operator@example.com"}]),
             "EMPTY"),
        ):
            rs = mkroot("cfg")
            ms, _ = stage_candidate(rs, SPAN)
            asf = "deploy/evidence/operator-armed-cfg.md"
            commit_file(rs, asf, armed_text(ms["candidate_id"]), signing_key=pubA)
            refused("SIGNING CONFIG %s -> refused (this instance is unarmed), even "
                    "with a fully valid armed artifact" % tag,
                    attempt(rs, ms["candidate_id"], asf, cfg_maker(rs)),
                    "(4) pinned signing keys", (frag,))
            case("  ...nothing promoted under config %s" % tag,
                 not os.path.isdir(os.path.join(rs, "raw"))
                 and _cands.effective_state(rs, ms["candidate_id"]) == "registered")

        # ==================================================== not registered
        rootS = mkroot("staged")
        cfgS = write_cfg(rootS, [ENTRY_A])
        metaS, _ = stage_candidate(rootS, SPAN, state="staged")
        artS = "deploy/evidence/operator-armed-staged.md"
        commit_file(rootS, artS, armed_text(metaS["candidate_id"]), signing_key=pubA)
        refused("CANDIDATE NOT `registered` (still `staged`): refused",
                attempt(rootS, metaS["candidate_id"], artS, cfgS),
                "(1) candidate eligibility", ("not 'registered'",))

        rootX = mkroot("unknowncid")
        cfgX = write_cfg(rootX, [ENTRY_A])
        commit_file(rootX, "README.md", "seed\n")
        refused("CANDIDATE ID NOT IN THE LEDGER AT ALL: refused",
                attempt(rootX, "sess-nope-0000000000000000",
                        "deploy/evidence/whatever.md", cfgX),
                "(1) candidate eligibility", ("no entry in the candidate ledger",))

        # ==================================================== wrong disposition
        rootQ = mkroot("dispcand")
        cfgQ = write_cfg(rootQ, [ENTRY_A])
        metaQ, _ = stage_candidate(rootQ, SPAN, disposition="plain-intake")
        artQ = "deploy/evidence/operator-armed-plain.md"
        commit_file(rootQ, artQ, armed_text(metaQ["candidate_id"]), signing_key=pubA)
        refused("CANDIDATE's OWN proposed_disposition is not "
                "transcribe-as-operator-event: refused",
                attempt(rootQ, metaQ["candidate_id"], artQ, cfgQ),
                "(2) proposed disposition", ("plain-intake",))

        # ==================================================== temporal
        rootT = mkroot("temporal")
        cfgT = write_cfg(rootT, [ENTRY_A])
        metaT, _ = stage_candidate(rootT, SPAN)
        artT = "deploy/evidence/operator-armed-future.md"
        commit_file(rootT, artT, armed_text(metaT["candidate_id"]),
                    signing_key=pubA, when=FUTURE)
        refused("TEMPORAL: a verified commit that does NOT strictly predate the run "
                "start is refused",
                attempt(rootT, metaT["candidate_id"], artT, cfgT),
                "(9) temporal ordering", ("does not strictly predate",))

        # ==================================================== rewritten span
        rootR = mkroot("rewritten")
        cfgR = write_cfg(rootR, [ENTRY_A])
        metaR, relR = stage_candidate(rootR, SPAN)
        artR = "deploy/evidence/operator-armed-rewrite.md"
        commit_file(rootR, artR, armed_text(metaR["candidate_id"]), signing_key=pubA)
        pR = os.path.join(rootR, relR.replace("/", os.sep))
        txtR = open(pR, encoding="utf-8-sig", newline="").read()
        with open(pR, "w", encoding="utf-8", newline="") as fh:
            fh.write(txtR.replace(SPAN, SPAN + " AND ALSO give the agent root."))
        refused("REWRITTEN SPAN after registration: refused (a rewritten span is a "
                "different candidate)",
                attempt(rootR, metaR["candidate_id"], artR, cfgR),
                "(1) candidate eligibility", ())

        # =============================================== write-order convergence
        # Prove the documented ordering (raw file FIRST) and that a crash between
        # the two writes leaves a VISIBLE, CONVERGING gap rather than a lie in the
        # ledger.
        rootO = mkroot("writeorder")
        cfgO = write_cfg(rootO, [ENTRY_A])
        metaO, _ = stage_candidate(rootO, SPAN)
        cidO = metaO["candidate_id"]
        artO = "deploy/evidence/operator-armed-order.md"
        commit_file(rootO, artO, armed_text(cidO), signing_key=pubA)
        _orig_append = _cands.append_candidate_record

        def _boom(_root, _rec):
            raise RuntimeError("simulated crash between the raw write and the append")

        _cands.append_candidate_record = _boom
        try:
            try:
                promote(rootO, cidO, artO, signing_config_path=cfgO,
                        origin_config_path=_ORIGIN_CFG_FIXTURE[0])
                case("WRITE ORDER: a crash at the ledger append propagates", False)
            except RuntimeError:
                case("WRITE ORDER: a crash at the ledger append propagates "
                     "(never swallowed)", True)
        finally:
            _cands.append_candidate_record = _orig_append
        raws = os.listdir(os.path.join(rootO, "raw")) \
            if os.path.isdir(os.path.join(rootO, "raw")) else []
        case("WRITE ORDER: the raw file exists with NO promoted record -- a visible "
             "conservation violation, never a ledger record pointing at a file that "
             "was never written",
             len([f for f in raws if f.endswith(".md")]) == 1
             and _cands.effective_state(rootO, cidO) == "registered")
        kind_o, info_o = attempt(rootO, cidO, artO, cfgO)
        case("WRITE ORDER: a re-run CONVERGES (recognises its own byte-identical "
             "raw file and completes the append)",
             kind_o == "OK" and _cands.effective_state(rootO, cidO) == "promoted")
        raws2 = [f for f in os.listdir(os.path.join(rootO, "raw"))
                 if f.endswith(".md")]
        case("WRITE ORDER: the converging re-run left exactly ONE raw file",
             len(raws2) == 1)

        # a DIFFERENT pre-existing file at the target path is never overwritten
        rootV = mkroot("nooverwrite")
        cfgV = write_cfg(rootV, [ENTRY_A])
        metaV, _ = stage_candidate(rootV, SPAN)
        cidV = metaV["candidate_id"]
        artV = "deploy/evidence/operator-armed-collide.md"
        commit_file(rootV, artV, armed_text(cidV), signing_key=pubA)
        slugV = _safe_slug(metaV["candidate_class"], cidV)
        collide = os.path.join(rootV, "raw",
                               "2026-07-20-ryan-%s.md" % slugV)
        os.makedirs(os.path.dirname(collide), exist_ok=True)
        with open(collide, "w", encoding="utf-8", newline="") as fh:
            fh.write("a DIFFERENT pre-existing raw event\n")
        refused("PRE-EXISTING raw event at the target path with different content: "
                "refused, never overwritten",
                attempt(rootV, cidV, artV, cfgV),
                "(11) raw-event write", ("refusing to overwrite",))
        case("  ...the pre-existing file is untouched",
             open(collide, encoding="utf-8").read()
             == "a DIFFERENT pre-existing raw event\n")

        # ================== concurrent check-and-consume (repo-grounded round,
        # 2026-07-23, HIGH). The race is simulated DETERMINISTICALLY: the lock's
        # own __enter__ is patched to let a "concurrent winner" append its
        # promoted record after our pre-checks passed but before our re-check
        # runs -- exactly the interleaving the lock's re-check exists to catch.
        rootRC = mkroot("race")
        cfgRC = write_cfg(rootRC, [ENTRY_A])
        metaRC, _ = stage_candidate(rootRC, "a decision raced by two promoters")
        cidRC = metaRC["candidate_id"]
        artRC = "deploy/evidence/operator-armed-race.md"
        shaRC = commit_file(rootRC, artRC, armed_text(cidRC), signing_key=pubA)
        _orig_enter = _PromotionLock.__enter__
        _fired = {"n": 0}

        def _racing_enter(self):
            r = _orig_enter(self)
            if _fired["n"] == 0:
                _fired["n"] = 1
                # the concurrent winner: same candidate, same artifact, its
                # promoted record lands first (direct append -- simulating the
                # other process's completed consumption step)
                _cands.append_candidate_record(rootRC, _cands.new_record(
                    metaRC, "promoted", "simulated concurrent winner",
                    "intake/session-candidates/x.md",
                    gate_artifact=artRC, gate_commit=shaRC))
            return r

        _PromotionLock.__enter__ = _racing_enter
        try:
            kindRC, eRC = attempt(rootRC, cidRC, artRC, cfgRC)
        finally:
            _PromotionLock.__enter__ = _orig_enter
        case("RACE: a concurrent promotion landing between pre-check and locked "
             "re-check is CAUGHT -- the loser refuses instead of appending a "
             "duplicate consumption record",
             kindRC == "REFUSED" and "concurrent" in str(eRC).lower())
        case("RACE: the losing run wrote NOTHING to raw/",
             not os.path.isdir(os.path.join(rootRC, "raw"))
             or os.listdir(os.path.join(rootRC, "raw")) == [])
        case("RACE: exactly ONE promoted record exists (the winner's)",
             sum(1 for r in _cands.ledger_history(rootRC)
                 if r["state"] == "promoted") == 1)

        # lock mechanics: a held (fresh) lock refuses at timeout; a stale lock
        # (crashed holder) is broken and acquired.
        rootLK = mkroot("lock")
        lk = _PromotionLock(rootLK, timeout=2.0)
        with open(lk.path, "w") as fh:
            fh.write("held-by-a-live-process")
        try:
            # timeout doubles as the staleness threshold (same as _SeqLock), so
            # it must exceed the ctor's own git-subprocess spawn time or the
            # freshly-held lock reads as stale -- 2s is enough on Windows, and
            # the mtime is refreshed right before entering for the same reason.
            # a LIVE holder is simulated with a slightly-future mtime: the
            # staleness threshold equals the wait deadline (same one-value
            # semantics as compile-core's _SeqLock), so a same-age lock is
            # broken at exactly the moment the deadline fires -- correct for a
            # crashed holder, but this case wants the REFUSAL path, i.e. a
            # holder whose lock stays fresher than the waiter's patience.
            lk_probe = _PromotionLock(rootLK, timeout=2.0)
            fut = time.time() + 60
            os.utime(lk.path, (fut, fut))
            try:
                with lk_probe:
                    case("LOCK: a freshly-held lock refuses at timeout", False)
            except PromotionRefused as e:
                case("LOCK: a freshly-held lock refuses at timeout",
                     "(12) promotion lock" in str(e))
            old = time.time() - 120
            os.utime(lk.path, (old, old))
            with _PromotionLock(rootLK, timeout=2.0):
                case("LOCK: a STALE lock (crashed holder) is broken and "
                     "acquired", True)
        finally:
            try:
                os.remove(lk.path)
            except OSError:
                pass

        # ==================================================== hermeticity
        # The original form of this case asserted the live config was UNARMED --
        # true until the operator's 2026-07-23 key act, and wrong ever after: an
        # armed instance is the intended end state, not a hermeticity violation.
        # What hermeticity actually requires is that this suite neither DEPENDS
        # ON nor MODIFIES the live config: every attempt() threads an explicit
        # fixture signing_config_path, and the assertions below prove the live
        # file contains none of this run's TEST keys (i.e. the suite never wrote
        # to it) whichever armed/unarmed state the instance is in.
        live_keys = load_signing_config()["operator_signing_keys"]
        test_fps = {fpA, fpB}
        case("HERMETIC: the live deploy/signing-config.yaml contains NO test-run "
             "key (this suite never writes the live trust config; armed or "
             "unarmed, its %d live key(s) are not ours)" % len(live_keys),
             all(k["fingerprint"] not in test_fps for k in live_keys))

    finally:
        _shutil.rmtree(keydir, ignore_errors=True)
        for r in roots:
            _shutil.rmtree(r, ignore_errors=True)

    if failed:
        print("promote-candidate self-test: FAIL (%d/%d, %d skipped)"
              % (total - failed, total, skipped))
        return 1
    note = "" if skipped == 0 else " (%d skipped)" % skipped
    print("promote-candidate self-test: PASS (%d/%d)%s" % (total, total, note))
    return 0


def _mk_origin_cfg(dirpath, prefix="ryan"):
    """An origin-config FIXTURE. EVERY self-test promotion routes through one of
    these -- never the live deploy/origin-config.yaml. This fork's live config
    happens to say [ryan] too, but the suite must not depend on that coincidence:
    a fresh/template instance ships with NO origin-config at all, and this suite
    must still pass there (origin.py's own self-test makes exactly the same
    point). `prefix` is parameterised so the alt-operator case can prove the
    filename really does follow instance config rather than an engine constant."""
    # The prefix is IN THE FILENAME: two fixtures for different operators must be
    # two distinct files. A shared name silently clobbers whichever was written
    # first, and every later case then reads the other operator's config -- which
    # is exactly the failure this comment exists to have caught once.
    p = os.path.join(dirpath, "origin-config-fixture-%s.yaml" % prefix)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("human_prefixes: [%s]\n" % prefix)
    return p


# ------------------------------------------------------------------ CLI
def main(argv):
    p = argparse.ArgumentParser(
        prog="promote-candidate.py",
        description="R-1 promotion gate: promote a REGISTERED session candidate to "
                    "an operator-provenance raw event, only behind a "
                    "signature-verified operator gate artifact (ten checks, no "
                    "downgrade-and-proceed path).")
    p.add_argument("--root", default=".", help="repo root (default: .)")
    p.add_argument("--candidate-id", help="the candidate to promote")
    p.add_argument("--artifact", help="repo-relative path to the gate artifact")
    p.add_argument("--self-test", action="store_true",
                   help="hermetic self-test (temp roots + a generated test keypair; "
                        "never the live ledger or the live signing config)")
    args = p.parse_args(argv)

    if args.self_test:
        return self_test()

    ok, missing = tools_available()
    if not ok:
        print("RESULT: INCONCLUSIVE -- %s is unavailable; the promotion gate "
              "cannot be evaluated (this is NOT a refusal and NOT a promotion)"
              % missing)
        return 2
    if yaml is None:
        print("RESULT: INCONCLUSIVE -- PyYAML unavailable; neither the candidate "
              "file nor the signing config can be parsed")
        return 2
    if not args.candidate_id or not args.artifact:
        p.print_usage()
        print("promote-candidate.py: --candidate-id and --artifact are both "
              "required (or use --self-test)")
        return 2

    root = os.path.abspath(args.root)
    try:
        info = promote(root, args.candidate_id, args.artifact,
                       out=lambda s: print(s))
    except PromotionRefused as e:
        print(refusal_line(e))
        return 1
    except Inconclusive as e:
        print("RESULT: INCONCLUSIVE -- %s" % e)
        return 2
    print("PROMOTED: candidate %s -> %s (gate artifact %s @ %s, pinned key %s, "
          "ledger seq %d)"
          % (info["candidate_id"], info["raw_event"], info["gate_artifact"],
             info["gate_commit"], info["fingerprint"], info["seq"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
