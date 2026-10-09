#!/usr/bin/env python3
"""session-model.py -- the driving session's model identity, read from the
tool's OWN session record (backlog v3.0-241, v3.0.64).

Compile's dispatch stamp needs a model identity with a SOURCE (never the
session's self-belief -- 2026-08-05). Both supported tools write the real
model id on every turn of their own session record, so the identity is read
from that record and stamped as `identity_source="attestation:<path>#line <n>"`
(the `attestation:` class `stamp_dispatch` already accepts). The attestation
roster and its operator question remain only as the FALLBACK when this tool
returns no record.

Records read (read-only; nothing is ever written):
  Claude Code  env CLAUDE_CODE_SESSION_ID; transcript
               ~/.claude/projects/<slug>/<session_id>.jsonl, found by globbing
               ~/.claude/projects/*/<session_id>.jsonl (exactly ONE match
               required). The LATEST `"type": "assistant"` entry whose
               `message.model` is a real id (`<synthetic>` and empty skipped),
               so a mid-session /model switch stamps the model now driving.
  Codex        env CODEX_THREAD_ID or CODEX_SESSION_ID (if both are set they
               must agree); the rollout ~/.codex/sessions/**/rollout-*<id>*.jsonl
               (exactly ONE match required). The LATEST `"type": "turn_context"`
               record's `payload.model` -- the per-turn record of the model that
               ran the turn (other record types echo settings or, for
               `compacted`, the PREVIOUS turn's model, so they are not read).
               Never "the newest rollout file": a parallel session could own it.
  Both tools' markers, neither, an unusable id, or no unique file => no record.

Output (stdout, one JSON object):
  record     {"ok": true, "vendor": "anthropic"|"openai", "model": "<id>",
              "source": "attestation:<path>#line <n>", "line": <n>,
              "path": "<path>"}
  no record  {"ok": false, "reason": "<why>"}
Nothing from the record except the model id, its line number and the record's
path is ever printed.

Usage:
  session-model.py              resolve from this process's environment
  session-model.py --self-test  hermetic fixture battery (temp HOME)

Exit codes: 0 = record found | 1 = no record (the roster fallback applies) |
            1 = a self-test failure | 2 = usage error.
Zero third-party dependencies; Python 3.
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

CLAUDE_ENV = "CLAUDE_CODE_SESSION_ID"
CODEX_ENVS = ("CODEX_THREAD_ID", "CODEX_SESSION_ID")

# A session id is used inside a glob pattern: only plain id characters, so a
# value can never widen the match (no '*', '?', '[', path separators).
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{3,127}$")
# A printable model id: one token, no placeholder brackets, bounded length.
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+\[\]-]{0,199}$")


def _no(reason):
    return {"ok": False, "reason": reason}


def _real_model(value):
    return (isinstance(value, str) and value.strip() == value
            and not value.startswith("<") and bool(_MODEL_RE.match(value)))


def _latest(path, extract):
    """(model, line) of the LAST line whose parsed JSON `extract` maps to a
    real model id, or (None, None). Unparseable lines are skipped."""
    found = (None, None)
    with open(path, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            if '"model"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            model = extract(rec)
            if _real_model(model):
                found = (model, n)
    return found


def _claude_model(rec):
    if rec.get("type") != "assistant":
        return None
    msg = rec.get("message")
    return msg.get("model") if isinstance(msg, dict) else None


def _codex_model(rec):
    if rec.get("type") != "turn_context":
        return None
    payload = rec.get("payload")
    return payload.get("model") if isinstance(payload, dict) else None


def _unique(pattern, recursive=False):
    matches = sorted(set(os.path.normpath(p) for p in
                         glob.glob(pattern, recursive=recursive)
                         if os.path.isfile(p)))
    return matches


def _result(vendor, path, extract, what):
    try:
        model, line = _latest(path, extract)
    except OSError as exc:
        return _no("%s record %s unreadable (%s)" % (what, path, exc.__class__.__name__))
    if model is None:
        return _no("%s record %s has no entry carrying a real model id" % (what, path))
    shown = path.replace("\\", "/")
    return {"ok": True, "vendor": vendor, "model": model,
            "source": "attestation:%s#line %d" % (shown, line),
            "line": line, "path": shown}


def resolve(env, home):
    """Resolve the session record from an environment mapping and a home dir."""
    claude_id = (env.get(CLAUDE_ENV) or "").strip()
    codex_vals = [(k, (env.get(k) or "").strip()) for k in CODEX_ENVS]
    codex_vals = [(k, v) for k, v in codex_vals if v]
    if claude_id and codex_vals:
        return _no("both Claude Code (%s) and Codex (%s) session markers are set "
                   "-- the driving tool is ambiguous"
                   % (CLAUDE_ENV, ", ".join(k for k, _ in codex_vals)))
    if not claude_id and not codex_vals:
        return _no("no session marker in the environment (%s, %s)"
                   % (CLAUDE_ENV, " / ".join(CODEX_ENVS)))
    if claude_id:
        if not _ID_RE.match(claude_id):
            return _no("%s is not a plain session id" % CLAUDE_ENV)
        matches = _unique(os.path.join(home, ".claude", "projects", "*",
                                       claude_id + ".jsonl"))
        if len(matches) != 1:
            return _no("expected exactly one Claude Code transcript "
                       "~/.claude/projects/*/%s.jsonl, found %d"
                       % (claude_id, len(matches)))
        return _result("anthropic", matches[0], _claude_model, "Claude Code")
    ids = set(v for _, v in codex_vals)
    if len(ids) != 1:
        return _no("%s disagree -- the Codex session is ambiguous"
                   % " and ".join(k for k, _ in codex_vals))
    codex_id = ids.pop()
    if not _ID_RE.match(codex_id):
        return _no("the Codex session id is not a plain id")
    matches = _unique(os.path.join(home, ".codex", "sessions", "**",
                                   "rollout-*%s*.jsonl" % codex_id), recursive=True)
    if len(matches) != 1:
        return _no("expected exactly one Codex rollout "
                   "~/.codex/sessions/**/rollout-*%s*.jsonl, found %d"
                   % (codex_id, len(matches)))
    # v3.0.64 review round 1: the file NAME matching is not identity -- the rollout's own
    # session_meta record must carry this exact id, or the record is not this session's
    if not _codex_meta_id_is(matches[0], codex_id):
        return _no("the Codex rollout's session_meta does not carry this session's id -- "
                   "not this session's record")
    return _result("openai", matches[0], _codex_model, "Codex")


def _codex_meta_id_is(path, codex_id):
    """True when the rollout's FIRST session_meta record names exactly this session id
    (payload.id, or payload.session_id). Reads only that record's id fields."""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("type") == "session_meta":
                    p = rec.get("payload") or {}
                    return codex_id in (p.get("id"), p.get("session_id"))
    except OSError:
        return False
    return False


# --------------------------------------------------------------- self-test

SID = "11111111-2222-3333-4444-555555555555"
SID2 = "99999999-8888-7777-6666-555555555555"
XID = "01a0fe60-a322-7392-9de5-0e81b5c9d36e"


def _write(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in records:
            fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")


def _asst(model, text="SECRET-CONVERSATION-TEXT"):
    return {"type": "assistant", "message": {"model": model, "role": "assistant",
            "content": [{"type": "text", "text": text}]}}


def _claude_transcript(home, slug, sid, records):
    _write(os.path.join(home, ".claude", "projects", slug, sid + ".jsonl"), records)


def _codex_rollout(home, day, xid, records):
    _write(os.path.join(home, ".codex", "sessions", "2026", "10", day,
                        "rollout-2026-10-%sT13-49-01-%s.jsonl" % (day, xid)), records)


def _tc(model):
    return {"type": "turn_context", "payload": {"model": model, "cwd": "SECRET-CWD"}}


def self_test():
    results = []

    def case(name, cond):
        results.append((name, bool(cond)))
        print("  [%s] %s" % ("PASS" if cond else "FAIL", name))

    root = tempfile.mkdtemp(prefix="session-model-selftest-")
    try:
        # 1. Claude: mid-file model switch -> latest wins, synthetic skipped.
        h = os.path.join(root, "h1")
        _claude_transcript(h, "C--proj", SID, [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            _asst("claude-fable-5-1"), _asst("claude-fable-5-1"),
            {"type": "summary", "summary": "x"},
            _asst("claude-opus-5-5"),
            _asst("<synthetic>"),
            {"type": "user", "message": {"role": "user", "content": "model ok"}},
        ])
        r = resolve({CLAUDE_ENV: SID}, h)
        case("claude: mid-file /model switch stamps the latest model",
             r.get("ok") and r["model"] == "claude-opus-5-5" and r["vendor"] == "anthropic")
        case("claude: <synthetic> trailing entry skipped; line is the real turn's (5)",
             r.get("ok") and r["line"] == 5)
        case("claude: source is an attestation: path#line form",
             r.get("ok") and r["source"].startswith("attestation:")
             and r["source"].endswith("/C--proj/%s.jsonl#line 5" % SID))
        case("claude: output carries only ok/vendor/model/source/line/path",
             r.get("ok") and set(r) == {"ok", "vendor", "model", "source", "line", "path"}
             and "SECRET" not in json.dumps(r))

        # 2. Claude: empty / missing model and non-assistant model fields skipped.
        h = os.path.join(root, "h2")
        _claude_transcript(h, "C--proj", SID, [
            _asst("claude-opus-5-5"),
            _asst(""),
            {"type": "assistant", "message": {"role": "assistant"}},
            {"type": "user", "message": {"model": "claude-impostor-9"}},
            "not json {\"model\"",
        ])
        r = resolve({CLAUDE_ENV: SID}, h)
        case("claude: empty/missing model and user-entry model ignored",
             r.get("ok") and r["model"] == "claude-opus-5-5" and r["line"] == 1)

        # 3. Claude: only synthetic entries -> no record.
        h = os.path.join(root, "h3")
        _claude_transcript(h, "C--proj", SID, [_asst("<synthetic>")])
        r = resolve({CLAUDE_ENV: SID}, h)
        case("claude: only <synthetic> entries -> no record", r.get("ok") is False)

        # 4. Claude: no transcript file -> no record.
        h = os.path.join(root, "h4")
        os.makedirs(os.path.join(h, ".claude", "projects", "C--proj"))
        r = resolve({CLAUDE_ENV: SID}, h)
        case("claude: no transcript file -> no record",
             r.get("ok") is False and "found 0" in r["reason"])

        # 5. Claude: two matching files (two project slugs) -> no record.
        h = os.path.join(root, "h5")
        _claude_transcript(h, "C--proj-a", SID, [_asst("claude-opus-5-5")])
        _claude_transcript(h, "C--proj-b", SID, [_asst("claude-fable-5-1")])
        r = resolve({CLAUDE_ENV: SID}, h)
        case("claude: two matching transcripts -> no record",
             r.get("ok") is False and "found 2" in r["reason"])

        # 6. Claude: another session's transcript is never read.
        h = os.path.join(root, "h6")
        _claude_transcript(h, "C--proj", SID2, [_asst("claude-opus-5-5")])
        r = resolve({CLAUDE_ENV: SID}, h)
        case("claude: a different session's transcript is not used",
             r.get("ok") is False)

        # 7. Claude: an id that would widen the glob is refused.
        r = resolve({CLAUDE_ENV: "*"}, h)
        case("claude: glob-widening session id refused", r.get("ok") is False)

        # 8. Codex: rollout matched by id; latest turn_context wins;
        #    compacted / settings records not read.
        h = os.path.join(root, "h8")
        _codex_rollout(h, "02", XID, [
            {"type": "session_meta", "payload": {"id": XID, "base_instructions":
             {"provenance": {"model": "gpt-old"}}}},
            _tc("gpt-6-astra"),
            {"type": "response_item", "payload": {"type": "message",
             "content": "SECRET-CODEX-TEXT"}},
            _tc("gpt-6.1-sol"),
            {"type": "compacted", "payload": {"resume_metadata":
             {"previous_turn_settings": {"model": "gpt-6-astra"}}}},
            {"type": "event_msg", "payload": {"type": "thread_settings_applied",
             "thread_settings": {"model": "gpt-6-astra"}}},
        ])
        # a parallel session's NEWER rollout must not be picked
        _codex_rollout(h, "09", "01a12179-fa40-7000-8000-000000000000",
                       [_tc("gpt-parallel")])
        r = resolve({"CODEX_THREAD_ID": XID}, h)
        case("codex: rollout matched by id, latest turn_context model (line 4)",
             r.get("ok") and r["vendor"] == "openai" and r["model"] == "gpt-6.1-sol"
             and r["line"] == 4 and "SECRET" not in json.dumps(r))
        # v3.0.64 review round 1: a file whose NAME matches but whose session_meta names another
        # session is not this session's record
        h8b = os.path.join(root, "h8b")
        _codex_rollout(h8b, "02", XID, [
            {"type": "session_meta", "payload": {"id": "ffffffff-0000-7000-8000-000000000000"}},
            {"type": "turn_context", "payload": {"model": "gpt-6.1-sol"}}])
        r = resolve({"CODEX_THREAD_ID": XID}, h8b)
        case("codex: a rollout named for this id whose session_meta names ANOTHER session -> no record",
             r.get("ok") is False and "session_meta" in r.get("reason", ""))
        r = resolve({"CODEX_SESSION_ID": XID}, h)
        case("codex: CODEX_SESSION_ID also accepted",
             r.get("ok") and r["model"] == "gpt-6.1-sol")
        r = resolve({"CODEX_THREAD_ID": XID, "CODEX_SESSION_ID": SID2}, h)
        case("codex: disagreeing thread/session ids -> no record", r.get("ok") is False)

        # 9. Codex session with no id in env -> no record (never newest file).
        r = resolve({}, h)
        case("codex: no session id -> no record (no newest-file fallback)",
             r.get("ok") is False)

        # 10. Codex: two rollouts matching one id -> no record.
        _codex_rollout(h, "03", XID, [_tc("gpt-6-astra")])
        r = resolve({"CODEX_THREAD_ID": XID}, h)
        case("codex: two rollouts for one id -> no record",
             r.get("ok") is False and "found 2" in r["reason"])

        # 11. Both markers -> no record.
        h = os.path.join(root, "h11")
        _claude_transcript(h, "C--proj", SID, [_asst("claude-opus-5-5")])
        _codex_rollout(h, "02", XID, [_tc("gpt-6.1-sol")])
        r = resolve({CLAUDE_ENV: SID, "CODEX_THREAD_ID": XID}, h)
        case("both Claude Code and Codex markers -> no record",
             r.get("ok") is False and "ambiguous" in r["reason"])

        # 12. CLI end to end with a temp HOME (HOME and USERPROFILE).
        env = dict((k, v) for k, v in os.environ.items()
                   if k != CLAUDE_ENV and k not in CODEX_ENVS)
        env.update({"HOME": h, "USERPROFILE": h, CLAUDE_ENV: SID})
        p = subprocess.run([sys.executable, os.path.abspath(__file__)], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out = json.loads(p.stdout.decode("utf-8"))
        except ValueError:
            out = {}
        case("cli: temp HOME run prints the record and exits 0",
             p.returncode == 0 and out.get("model") == "claude-opus-5-5")
        env.pop(CLAUDE_ENV)
        p = subprocess.run([sys.executable, os.path.abspath(__file__)], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out = json.loads(p.stdout.decode("utf-8"))
        except ValueError:
            out = {}
        case("cli: no marker prints ok:false and exits 1",
             p.returncode == 1 and out.get("ok") is False)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    passed = sum(1 for _, ok in results if ok)
    print("session-model self-test: %d/%d PASS" % (passed, len(results)))
    return 0 if passed == len(results) else 1


def main(argv):
    if argv == ["--self-test"]:
        return self_test()
    if argv:
        sys.stderr.write("usage: session-model.py [--self-test]\n")
        return 2
    result = resolve(os.environ, os.path.expanduser("~"))
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
