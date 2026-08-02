#!/usr/bin/env bash
# Deterministic self-test for converge.js — exercises every honesty gate WITHOUT calling GPT.
# Every gate fires during lint, before any verify-cli subprocess spawns, so these are fast + offline.
# Usage: bash selftest.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
CONVERGE="$HERE/converge.js"
T="$(mktemp -d)"
PASS=0; FAIL=0

# assert <expected-exit> <substring-in-stderr> <ledger-json-string> [extra args]
assert() {
  local name="$1" want_code="$2" want_sub="$3" json="$4"; shift 4
  local dir; dir="$(mktemp -d "$T/case.XXXX")"
  printf '%s' "$json" > "$dir/ledger.json"
  # let test bodies pre-create evidence/verdict files via a callback dir
  if [ -n "${SETUP:-}" ]; then ( cd "$dir" && eval "$SETUP" ); fi
  local out; out="$(node "$CONVERGE" "$dir/ledger.json" "$@" 2>&1)"; local code=$?
  if [ "$code" -eq "$want_code" ] && printf '%s' "$out" | grep -qF "$want_sub"; then
    echo "PASS  $name  (exit $code)"; PASS=$((PASS+1))
  else
    echo "FAIL  $name  (got exit $code, wanted $want_code; wanted substring: '$want_sub')"
    echo "------ output ------"; printf '%s\n' "$out" | sed 's/^/    /'; echo "-------------------"
    FAIL=$((FAIL+1))
  fi
  unset SETUP
}

echo "== converge.js self-test =="

# 1. T1 -> handoff redirect
assert "tier T1 rejected" 3 "T1 keystone decision goes to a full handoff" \
'{"statement":"x","tier":"T1","claims":[{"id":"c1","claim":"a"}]}'

# 2. T4 -> don't loop
assert "tier T4 rejected" 3 "T4 means don" \
'{"statement":"x","tier":"T4","claims":[{"id":"c1","claim":"a"}]}'

# 3. bad tier
assert "bad tier rejected" 3 "tier must be T2 or T3" \
'{"statement":"x","tier":"T9","claims":[{"id":"c1","claim":"a"}]}'

# 4. no claims
assert "no claims rejected" 3 "no claims" \
'{"statement":"x","tier":"T2","claims":[]}'

# 5. duplicate ids
assert "dup id rejected" 3 "duplicate claim id" \
'{"statement":"x","tier":"T2","claims":[{"id":"c1","claim":"a"},{"id":"c1","claim":"b"}]}'

# 6. recheck without reason
SETUP='echo "diff --git a b" > ev.txt'
assert "recheck needs reason" 3 "no recheck_reason" \
'{"statement":"x","tier":"T2","claims":[{"id":"c1","claim":"a","state":"recheck","evidence_file":"ev.txt","provenance":"git diff"}]}'

# 7. missing provenance
SETUP='echo "diff --git a b" > ev.txt'
assert "provenance required" 3 "has no provenance" \
'{"statement":"x","tier":"T2","claims":[{"id":"c1","claim":"a","state":"pending","evidence_file":"ev.txt"}]}'

# 8. missing evidence file
assert "evidence file required" 3 "evidence_file not found" \
'{"statement":"x","tier":"T2","claims":[{"id":"c1","claim":"a","state":"pending","evidence_file":"nope.txt","provenance":"git diff"}]}'

# 9. empty evidence file
SETUP='> ev.txt'
assert "empty evidence rejected" 3 "empty or not a file" \
'{"statement":"x","tier":"T2","claims":[{"id":"c1","claim":"a","state":"pending","evidence_file":"ev.txt","provenance":"git diff"}]}'

# 10. ANTI-ECHO: evidence file is a verdict JSON
SETUP='echo "{\"verdict\":\"confirmed\",\"uncertainty\":\"confident\",\"verifier\":{\"vendor\":\"openai\"}}" > verdictish.json'
assert "anti-echo verdict-shaped" 3 "parses as a verify-cli verdict" \
'{"statement":"x","tier":"T2","claims":[{"id":"c1","claim":"a","state":"pending","evidence_file":"verdictish.json","provenance":"recycled verdict"}]}'

# 11. ANTI-ECHO: evidence lives in verdicts/
SETUP='mkdir -p verdicts && echo "diff --git a b" > verdicts/c1-r1.json'
assert "anti-echo verdicts-dir" 3 "lives in verdicts/" \
'{"statement":"x","tier":"T2","claims":[{"id":"c1","claim":"a","state":"pending","evidence_file":"verdicts/c1-r1.json","provenance":"x"}]}'

# 12. needs-action blocks the next round
SETUP='echo "diff --git a b" > ev.txt'
assert "needs-action blocks round" 3 "still needs-action from the last round" \
'{"statement":"x","tier":"T2","round":1,"claims":[{"id":"c1","claim":"a","state":"needs-action","evidence_file":"ev.txt","provenance":"git diff"}]}'

# 13. cap reached (T3 allows 1; round already 1)
SETUP='echo "diff --git a b" > ev.txt'
assert "T3 cap reached" 3 "round cap reached" \
'{"statement":"x","tier":"T3","round":1,"claims":[{"id":"c1","claim":"a","state":"pending","evidence_file":"ev.txt","provenance":"git diff"}]}'

# 14. needs-action is ALLOWED in --status mode (inspection)
SETUP='echo "diff --git a b" > ev.txt'
assert "status tolerates needs-action" 0 "process this" \
'{"statement":"x","tier":"T2","round":1,"claims":[{"id":"c1","claim":"a","state":"needs-action","last_verdict":"revised","evidence_file":"ev.txt","provenance":"git diff"}]}' --status

# 15. clean ledger passes lint in --status (would proceed to verify in round mode)
SETUP='echo "diff --git a b" > ev.txt'
assert "clean ledger lints OK" 0 "CONVERGENCE CANDIDATE" \
'{"statement":"x","tier":"T2","round":1,"rounds":[{"round":1,"new_finding":false}],"claims":[{"id":"c1","claim":"a","state":"settled","last_verdict":"confirmed","evidence_file":"ev.txt","provenance":"git diff"}]}' --status

# 16. status shows ESCALATE when cap reached with unsettled
SETUP='echo "diff --git a b" > ev.txt'
assert "status shows escalate at cap" 0 "ESCALATE" \
'{"statement":"x","tier":"T3","round":1,"rounds":[{"round":1,"new_finding":true}],"claims":[{"id":"c1","claim":"a","state":"needs-action","last_verdict":"rejected","evidence_file":"ev.txt","provenance":"git diff"}]}' --status

# 17. all settled, none active -> CONVERGENCE CANDIDATE (absorb-without-recheck is the session's call)
SETUP='echo "diff --git a b" > ev.txt'
assert "all settled -> converge candidate" 0 "CONVERGENCE CANDIDATE" \
'{"statement":"x","tier":"T2","round":1,"rounds":[{"round":1,"new_finding":true}],"claims":[{"id":"c1","claim":"a","state":"settled","last_verdict":"confirmed","evidence_file":"ev.txt","provenance":"git diff"}]}' --status

# 18. ANTI-ECHO: stripped verdict (verifier/uncertainty removed) is still blocked
SETUP='echo "{\"verdict\":\"confirmed\"}" > stripped.json'
assert "anti-echo stripped verdict" 3 "parses as a verify-cli verdict" \
'{"statement":"x","tier":"T2","claims":[{"id":"c1","claim":"a","state":"pending","evidence_file":"stripped.json","provenance":"recycled"}]}'

# 19. unrecognized claim state -> HALT
assert "unknown state rejected" 3 "unrecognized state" \
'{"statement":"x","tier":"T2","claims":[{"id":"c1","claim":"a","state":"done"}]}'

# 20. TAINT: evidence path escapes the run dir -> HALT
assert "evidence path escape rejected" 3 "outside the run dir" \
'{"statement":"x","tier":"T2","claims":[{"id":"c1","claim":"a","state":"pending","evidence_file":"../escape.txt","provenance":"x"}]}'

# 21. bad claim id charset -> HALT (id is used in file paths)
assert "bad claim id rejected" 3 "must match" \
'{"statement":"x","tier":"T2","claims":[{"id":"c 1/..","claim":"a"}]}'

# 22. escalate state hard-blocks the next round
SETUP='echo "diff --git a b" > ev.txt'
assert "escalate blocks round" 3 "marked escalate" \
'{"statement":"x","tier":"T2","claims":[{"id":"c1","claim":"a","state":"escalate"},{"id":"c2","claim":"b","state":"pending","evidence_file":"ev.txt","provenance":"x"}]}'

# 23. depends_on referencing an unknown id -> HALT
assert "depends_on unknown id rejected" 3 "depends_on unknown claim id" \
'{"statement":"x","tier":"T2","claims":[{"id":"c1","claim":"a","depends_on":["zzz"]}]}'

# --- A1 special: the make-or-break — a recheck on identical evidence SHA is blocked ---
d="$(mktemp -d "$T/caseA1.XXXX")"
echo "diff --git a/x b/x  (round-1 artifact)" > "$d/ev.txt"
SH="$(sha256sum "$d/ev.txt" | cut -d' ' -f1)"
cat > "$d/ledger.json" <<JSON
{"statement":"x","tier":"T2","round":1,"claims":[{"id":"c1","claim":"a","state":"recheck","recheck_reason":"cascade from c2","evidence_file":"ev.txt","provenance":"git diff","last_evidence_sha":"$SH","history":[{"round":1,"verdict":"revised","evidence_sha":"$SH"}]}]}
JSON
out="$(node "$CONVERGE" "$d/ledger.json" --status 2>&1)"; code=$?
if [ "$code" -eq 3 ] && printf '%s' "$out" | grep -qF "byte-identical"; then
  echo "PASS  A1 stale-recheck SHA gate  (exit 3)"; PASS=$((PASS+1))
else echo "FAIL  A1 stale-recheck (got $code)"; printf '%s\n' "$out"|sed 's/^/    /'; FAIL=$((FAIL+1)); fi
# companion: a recheck with FRESH (different) evidence passes lint
echo "diff --git a/x b/x  (FRESH round-2 artifact, different bytes)" > "$d/ev.txt"
out="$(node "$CONVERGE" "$d/ledger.json" --status 2>&1)"; code=$?
if [ "$code" -eq 0 ] && printf '%s' "$out" | grep -qF "re-checking because"; then
  echo "PASS  A1 fresh-recheck passes  (exit 0)"; PASS=$((PASS+1))
else echo "FAIL  A1 fresh-recheck (got $code)"; printf '%s\n' "$out"|sed 's/^/    /'; FAIL=$((FAIL+1)); fi

# --- B4 special: verifier returns a non-enum verdict -> HALT (nextState refuses to map it) ---
d="$(mktemp -d "$T/caseB4.XXXX")"; mkdir -p "$d/evidence"
echo 'process.stdout.write(JSON.stringify({verdict:"partial",uncertainty:"confident",verifier:{vendor:"openai"}})+"\n")' > "$d/mock.js"
echo "diff --git a b" > "$d/evidence/c1.txt"
cat > "$d/ledger.json" <<'JSON'
{"statement":"x","tier":"T2","claims":[{"id":"c1","claim":"a","state":"pending","evidence_file":"evidence/c1.txt","provenance":"git diff"}]}
JSON
out="$(CONVERGE_VERIFY_CLI="$d/mock.js" node "$CONVERGE" "$d/ledger.json" 2>&1)"; code=$?
if [ "$code" -eq 3 ] && printf '%s' "$out" | grep -qF "unrecognized verdict"; then
  echo "PASS  B4 unknown verdict HALT  (exit 3)"; PASS=$((PASS+1))
else echo "FAIL  B4 unknown verdict (got $code)"; printf '%s\n' "$out"|sed 's/^/    /'; FAIL=$((FAIL+1)); fi

echo
echo "== $PASS passed, $FAIL failed =="
rm -rf "$T"
[ "$FAIL" -eq 0 ]
