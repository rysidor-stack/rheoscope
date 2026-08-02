#!/usr/bin/env node
'use strict';
/**
 * converge — the mechanical half of the /cross-check-loop skill.
 *
 * It runs ONE round of cross-vendor verification over a ledger of load-bearing claims, enforces
 * the honesty gates, records each verdict + the SHA-256 of the evidence that produced it, and
 * enforces the tier round cap. It builds ON the proven single-shot primitive
 * (cross-vendor-verify/verify-cli.js -> an OpenAI Codex/GPT verifier): one round == N verify-cli calls,
 * one per active claim, run with bounded concurrency. It adds NO new verifier and NO new vendor
 * path — the load-bearing check every round is the same cross-vendor call.
 *
 * The split is deliberate. Everything JUDGMENT — decompose into claims, gather/re-ground primary
 * artifacts, decide which claims a correction cascades into, decide converged-vs-escalate — stays
 * in the driving session (see SKILL.md). This script only does what MUST be mechanical so the
 * session cannot accidentally skip it or fool itself:
 *
 *   1. ANTI-ECHO GATES (the make-or-break). An evidence file may never (a) be a verdict-shaped
 *      JSON, (b) live in the verdicts/ output dir, or (c) resolve outside the run dir. AND — the
 *      one a multi-round loop must nail — a `recheck` claim's evidence must DIFFER (by SHA) from
 *      the evidence it was last verified against: a re-check on identical bytes is not a re-ground,
 *      it is the prior conclusion wearing a fresh-round costume. These are compared here, not left
 *      to the session to remember. (The script still cannot detect narrative prose that mimics an
 *      artifact and carries a plausible provenance line — that residual is a session honor
 *      obligation, and the GPT verifier's skeptic mandate is the behavioral backstop, not a gate.)
 *   2. TIER ROUND CAP. T2=2 rounds, T3=1. Hitting the cap with unsettled claims is the ESCALATE
 *      signal (the handoff protocol), not a thing to override your way past. T1 -> author a full handoff;
 *      T4 -> don't loop, just decide.
 *   3. NEEDS-ACTION / ESCALATE FORCING FUNCTION. After a round, a revised/rejected/disagreed claim
 *      is parked in `needs-action`. The next round refuses to run until each is processed (cascade
 *      named -> recheck+reason, or settled, or escalate). An `escalate` claim hard-blocks the next
 *      round entirely — that is the handoff signal. You cannot sprint past a correction.
 *   4. EVIDENCE-SHA AUDIT TRAIL. Every claim's evidence hash is recorded per round, so "converged"
 *      is provably "fresh artifacts withstood fresh scrutiny."
 *
 * Usage:
 *   node converge.js <ledger.json>            run one round (verify active claims, record, cap-check)
 *   node converge.js <ledger.json> --status   lint + print state, NO verify calls (exits 3 if an
 *                                              active claim fails a gate)
 *   node converge.js <ledger.json> --concurrency N   (default 2) cap parallel verify calls
 *
 * Exit: 0 ok; 2 a verify-cli call failed (HALT); 3 ledger/gate violation (HALT); 64 usage/setup.
 *
 * The ledger JSON is authored and edited by the driving session (it is an LLM; JSON is fine).
 * Shape: see SKILL.md "The ledger". Paths inside it resolve relative to the ledger file's dir, and
 * evidence files MUST live under that run dir (taint containment).
 */

const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const VERIFY_CLI = process.env.CONVERGE_VERIFY_CLI ||
  (process.env.CROSS_VENDOR_BRIDGE_DIR
    ? path.join(process.env.CROSS_VENDOR_BRIDGE_DIR, 'verify-cli.js')
    : path.join(__dirname, '..', '..', 'bridge', 'verify-cli.js'));

const ROUND_CAP = { T2: 2, T3: 1 };           // the handoff protocol tier round caps
const ACTIVE = new Set(['pending', 'recheck']);
const DONE = new Set(['settled', 'blocked-on-test']);
const LEGAL_STATES = new Set(['pending', 'recheck', 'needs-action', 'settled', 'blocked-on-test', 'escalate']);
const VALID_VERDICTS = new Set(['confirmed', 'revised', 'rejected']);
const ID_RE = /^[A-Za-z0-9_-]+$/;

const LIVE = new Set();                        // in-flight child processes, killed if a round HALTs

function die(code, msg) {
  for (const c of LIVE) { try { c.kill(); } catch (e) {} }
  process.stderr.write('CONVERGE HALT: ' + msg + '\n');
  process.exit(code);
}
function note(msg) { process.stderr.write(msg + '\n'); }

function parseArgs(argv) {
  const a = { status: false, concurrency: 2 };
  const rest = [];
  for (let i = 2; i < argv.length; i++) {
    const k = argv[i];
    if (k === '--status') a.status = true;
    else if (k === '--concurrency') a.concurrency = parseInt(argv[++i], 10) || 2;
    else if (k === '-h' || k === '--help') a.help = true;
    else if (k.startsWith('--')) die(64, 'unknown argument: ' + k);
    else rest.push(k);
  }
  a.ledgerPath = rest[0];
  return a;
}

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

// A verify-cli verdict is `{verdict, reason, uncertainty, citations, verifier}`. Treat an evidence
// file as a recycled verdict (the echo this loop must prevent) if it parses to an object that EITHER
// carries a real verdict value, OR clusters two+ of the verdict-schema keys — this also catches a
// verdict with `verifier`/`uncertainty` stripped off (the cheap bypass of the naive two-field test).
function looksLikeVerdict(text) {
  let o;
  try { o = JSON.parse(text); } catch (e) { return false; }
  if (!o || typeof o !== 'object' || Array.isArray(o)) return false;
  if ('verdict' in o && VALID_VERDICTS.has(o.verdict)) return true;
  const KEYS = ['verdict', 'reason', 'uncertainty', 'citations', 'verifier'];
  return KEYS.filter(k => k in o).length >= 2;
}

function loadLedger(p) {
  let raw;
  try { raw = fs.readFileSync(p, 'utf8'); }
  catch (e) { die(64, 'cannot read ledger ' + p + ': ' + e.message); }
  let l;
  try { l = JSON.parse(raw); } catch (e) { die(3, 'ledger is not valid JSON: ' + e.message); }
  if (!l.statement) die(3, 'ledger missing "statement"');
  if (!ROUND_CAP[l.tier]) {
    if (l.tier === 'T1') die(3, 'tier T1 is out of scope for the loop — a T1 keystone decision goes to a full handoff (the handoff protocol), not this loop.');
    if (l.tier === 'T4') die(3, 'tier T4 is out of scope — T4 means don\'t loop; just decide (the handoff protocol).');
    die(3, 'tier must be T2 or T3 (got ' + JSON.stringify(l.tier) + ')');
  }
  if (!Array.isArray(l.claims) || l.claims.length === 0) die(3, 'ledger has no claims');
  if (typeof l.round !== 'number') l.round = 0;
  if (!Array.isArray(l.rounds)) l.rounds = [];
  const ids = new Set();
  for (const c of l.claims) {
    if (!c.id) die(3, 'a claim is missing "id"');
    if (!ID_RE.test(c.id)) die(3, 'claim id ' + JSON.stringify(c.id) + ' must match ' + ID_RE + ' (it is used in file paths)');
    if (ids.has(c.id)) die(3, 'duplicate claim id: ' + c.id);
    ids.add(c.id);
    if (!c.claim) die(3, 'claim ' + c.id + ' missing "claim" text');
    if (!c.state) c.state = 'pending';
    if (!LEGAL_STATES.has(c.state)) die(3, 'claim ' + c.id + ' has unrecognized state ' + JSON.stringify(c.state) +
      '. Legal: ' + [...LEGAL_STATES].join(' | '));
    if (!Array.isArray(c.history)) c.history = [];
    if (c.depends_on && !Array.isArray(c.depends_on)) die(3, 'claim ' + c.id + ' depends_on must be an array');
  }
  for (const c of l.claims) for (const d of (c.depends_on || []))
    if (!ids.has(d)) die(3, 'claim ' + c.id + ' depends_on unknown claim id ' + JSON.stringify(d));
  return l;
}

// Validate the claims we are ABOUT to verify this round. Returns the list of active claims.
function lintForRound(l, ledgerDir, mode) {
  const runRoot = path.resolve(ledgerDir);
  const verdictsDir = path.resolve(ledgerDir, 'verdicts');

  if (mode === 'round') {
    const esc = l.claims.filter(c => c.state === 'escalate');
    if (esc.length) die(3, 'these claims are marked escalate: [' + esc.map(c => c.id).join(', ') + ']. ' +
      'Write the escalation packet (SKILL.md "Escalate") and author a full handoff — do not run another round.');
    const stuck = l.claims.filter(c => c.state === 'needs-action');
    if (stuck.length) die(3, 'these claims are still needs-action from the last round: [' + stuck.map(c => c.id).join(', ') +
      ']. Process each one before the next round: name the cascade (set state="recheck" + a recheck_reason ' +
      'and re-ground evidence_file with FRESH artifacts), or set state="settled" (correction absorbed, claim now holds), ' +
      'or state="escalate". A correction you have not processed cannot be re-verified away.');
  }

  const active = l.claims.filter(c => ACTIVE.has(c.state));
  for (const c of active) {
    if (c.state === 'recheck' && !c.recheck_reason)
      die(3, 'claim ' + c.id + ' is state=recheck but has no recheck_reason — name WHY it is being re-checked ' +
        '(the cascade reason: which correction disturbed it). Re-checking without naming the cause is blind looping.');
    if (!c.evidence_file)
      die(3, 'claim ' + c.id + ' has no evidence_file');
    if (!c.provenance || !String(c.provenance).trim())
      die(3, 'claim ' + c.id + ' has no provenance — name the PRIMARY source of its evidence ' +
        '(e.g. "git diff A..B -- path", "test output: <cmd> lines N-M", "source path:lines @ SHA"). ' +
        'If you cannot, the evidence is your own narrative and there is no cross-vendor check to run.');
    const ev = path.resolve(ledgerDir, c.evidence_file);
    // --- taint containment: evidence must be a file you wrote under the run dir ---
    if (ev !== runRoot && !ev.startsWith(runRoot + path.sep))
      die(3, 'TAINT: claim ' + c.id + ' evidence_file resolves outside the run dir (' + ev + '). Evidence must be ' +
        'files you explicitly wrote under ' + runRoot + ' — never a system path, a parent dir, or "../".');
    if (!fs.existsSync(ev)) die(3, 'claim ' + c.id + ' evidence_file not found: ' + ev);
    const stat = fs.statSync(ev);
    if (!stat.isFile() || stat.size === 0) die(3, 'claim ' + c.id + ' evidence_file is empty or not a file: ' + ev);
    // --- anti-echo: never a verdict, never from verdicts/ ---
    if (ev === verdictsDir || ev.startsWith(verdictsDir + path.sep))
      die(3, 'ANTI-ECHO: claim ' + c.id + ' evidence_file lives in verdicts/ (' + ev + '). A verdict can never be ' +
        'evidence for the next round — that is the rubber-stamp this loop exists to prevent. Re-ground in fresh primary artifacts.');
    const text = fs.readFileSync(ev, 'utf8');
    if (looksLikeVerdict(text))
      die(3, 'ANTI-ECHO: claim ' + c.id + ' evidence_file (' + ev + ') parses as a verify-cli verdict. You are feeding a ' +
        'conclusion back in as evidence. Round N+1 must re-ground in fresh PRIMARY artifacts (diff/test-output/source/data), not in round N\'s verdict.');
    c._evidenceAbs = ev;
    c._evidenceSha = sha256(ev);
    // --- anti-echo: a recheck must re-ground (evidence SHA must differ from when it was last verified) ---
    if (c.state === 'recheck' && c.last_evidence_sha && c._evidenceSha === c.last_evidence_sha)
      die(3, 'ANTI-ECHO: claim ' + c.id + ' is state=recheck but its evidence_file is byte-identical to the artifact it ' +
        'was last verified against (same SHA ' + c._evidenceSha.slice(0, 12) + '). A re-check on identical bytes is not a ' +
        're-ground — it would re-verify the prior conclusion, not the correction. Write FRESH evidence reflecting the correction.');
  }
  return active;
}

// One verify-cli subprocess for one claim. Resolves {verdict} or rejects (HALT-worthy).
function runVerify(claim, evidenceAbs, tier) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const done = (fn, arg) => { if (settled) return; settled = true; fn(arg); };
    const args = ['--claim', claim.claim, '--evidence-file', evidenceAbs];
    if (tier) args.push('--tier', tier);
    const p = spawn(process.execPath, [VERIFY_CLI, ...args],
      { stdio: ['ignore', 'pipe', 'inherit'], env: process.env });   // stderr -> our stderr (per-claim banner shows)
    LIVE.add(p);
    let out = '';
    p.stdout.on('data', d => { out += d; });
    p.on('error', e => { LIVE.delete(p); done(reject, new Error('could not spawn verify-cli for ' + claim.id + ': ' + e.message)); });
    p.on('close', code => {
      LIVE.delete(p);
      if (code !== 0) return done(reject, new Error('verify-cli exited ' + code + ' for claim ' + claim.id +
        ' (see the VERIFY FAILED line above). Round HALTED — fix the verifier, do not fake a verdict.'));
      let v;
      try { v = JSON.parse(out); } catch (e) { return done(reject, new Error('verify-cli stdout for ' + claim.id + ' was not JSON: ' + out.slice(0, 300))); }
      done(resolve, v);
    });
  });
}

async function pool(items, limit, fn) {
  const out = new Array(items.length);
  let i = 0;
  const n = Math.max(1, Math.min(limit, items.length));
  await Promise.all(Array.from({ length: n }, async () => {
    while (true) {
      const idx = i++;
      if (idx >= items.length) return;
      out[idx] = await fn(items[idx], idx);
    }
  }));
  return out;
}

// verdict + uncertainty -> next state. The session owns transitions out of needs-action.
function nextState(verdict, uncertainty) {
  if (!VALID_VERDICTS.has(verdict)) die(3, 'verifier returned unrecognized verdict ' + JSON.stringify(verdict) +
    ' (expected confirmed | revised | rejected). Refusing to map it to a state.');
  if (uncertainty === 'reasonable-disagreement') return 'needs-action';   // judgment divergence -> session weighs / escalates
  if (verdict === 'confirmed' && uncertainty === 'needs-operational-data') return 'blocked-on-test';
  if (verdict === 'confirmed') return 'settled';
  return 'needs-action';   // revised | rejected
}

function stateGlyph(s) {
  return ({ settled: 'OK ', 'blocked-on-test': 'TEST', 'needs-action': 'ACT ', recheck: 'RE  ', pending: 'NEW ', escalate: 'ESC ' })[s] || s;
}

function dependents(l, id) {
  return l.claims.filter(c => (c.depends_on || []).includes(id)).map(c => c.id);
}

function printStatus(l) {
  const cap = ROUND_CAP[l.tier];
  note('');
  note('  ' + l.tier + '  round ' + l.round + '/' + cap + '   ' + l.statement);
  note('  ' + '-'.repeat(74));
  for (const c of l.claims) {
    const v = c.last_verdict ? (c.last_verdict + (c.last_uncertainty ? '/' + c.last_uncertainty : '')) : '(unverified)';
    note('  [' + stateGlyph(c.state) + '] ' + c.id.padEnd(5) + ' ' + v.padEnd(34) + ' ' + c.claim.slice(0, 60));
    if (c.state === 'needs-action') {
      note('         ^ process this: name cascade -> recheck+reason, or settled, or escalate');
      const deps = dependents(l, c.id);
      if (deps.length) note('         cascade hint: claims declaring depends_on ' + c.id + ': [' + deps.join(', ') +
        '] — decide whether the correction disturbs them (undeclared ripples count too).');
    }
    if (c.state === 'recheck' && c.recheck_reason) note('         re-checking because: ' + c.recheck_reason);
  }
  note('');
}

// The proposal: script proposes, session disposes. No score, no percentage — a qualitative call.
// Driven by current state only (never a stale prior-round record): a fresh revision lives in
// needs-action; a cascaded re-check lives in recheck (active); convergence is "nothing left to do".
function proposal(l) {
  const cap = ROUND_CAP[l.tier];
  const capReached = l.round >= cap;
  const anyEscalate = l.claims.some(c => c.state === 'escalate');
  const anyNeedsAction = l.claims.some(c => c.state === 'needs-action');
  const anyActive = l.claims.some(c => ACTIVE.has(c.state));
  const allDone = l.claims.every(c => DONE.has(c.state));
  const blocked = l.claims.filter(c => c.state === 'blocked-on-test');

  if (anyEscalate || (capReached && (anyNeedsAction || anyActive))) {
    note('>> ESCALATE. ' + (anyEscalate ? 'A claim is marked escalate. ' : 'Round cap (' + cap + ') reached with unsettled claims. ') +
      'The inability to converge within the cap IS the signal (the handoff protocol). Write the escalation packet ' +
      '(SKILL.md "Escalate") and author a full handoff — do not loop further.');
    return;
  }
  if (anyNeedsAction) {
    note('>> CONTINUE. Process each needs-action claim (name the cascade -> recheck+reason + re-ground with FRESH ' +
      'artifacts, or settle, or escalate), then run the next round.');
    return;
  }
  if (anyActive) {
    note('>> CONTINUE. Active claim(s) remain — re-ground any recheck with FRESH evidence, then run a round.');
    return;
  }
  if (allDone) {
    note('>> CONVERGENCE CANDIDATE. Nothing left to verify — every claim is settled' +
      (blocked.length ? ' (except ' + blocked.map(c => c.id).join(', ') + ' = blocked-on-test: surface "go run the test")' : '') +
      '. The convergence call is YOURS: confirm in writing that the last round surfaced no NEW load-bearing finding ' +
      '(if you settled a correction without re-checking it cross-vendor, say so and justify it). Then conclude. ' +
      'Verification is NOT approval — locking / human sign-off stay separate.');
  }
}

function writeLedger(ledgerPath, l) {
  const tmp = ledgerPath + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(l, null, 2) + '\n');
  fs.renameSync(tmp, ledgerPath);   // atomic replace (Node uses MOVEFILE_REPLACE_EXISTING on Windows)
}

async function main() {
  const args = parseArgs(process.argv);
  if (args.help || !args.ledgerPath) {
    note('usage: node converge.js <ledger.json> [--status] [--concurrency N]');
    process.exit(args.help ? 0 : 64);
  }
  const ledgerPath = path.resolve(args.ledgerPath);
  const ledgerDir = path.dirname(ledgerPath);
  const l = loadLedger(ledgerPath);

  if (args.status) {
    lintForRound(l, ledgerDir, 'status');   // surfaces gate problems without verifying
    printStatus(l);
    proposal(l);
    return;
  }

  // --- run a round ---
  const cap = ROUND_CAP[l.tier];
  const next = l.round + 1;
  if (next > cap)
    die(3, 'round cap reached (' + l.tier + ' allows ' + cap + ', last completed round is ' + l.round + '). ' +
      'Can\'t converge within the cap => ESCALATE to a full handoff (SKILL.md "Escalate"). Looping past the cap is the failure mode this guard prevents.');

  const active = lintForRound(l, ledgerDir, 'round');
  if (active.length === 0) {
    note('No active (pending/recheck) claims — nothing to verify. Run with --status and make the convergence call.');
    printStatus(l);
    proposal(l);
    return;
  }

  if (!fs.existsSync(VERIFY_CLI))
    die(64, 'verify-cli not found at ' + VERIFY_CLI + ' — set CONVERGE_VERIFY_CLI to its path.');

  note('Running round ' + next + ' over ' + active.length + ' claim(s): [' + active.map(c => c.id).join(', ') + '] ' +
    '(concurrency ' + args.concurrency + ', verifier=openai via verify-cli)');

  const verdictsDir = path.join(ledgerDir, 'verdicts');
  fs.mkdirSync(verdictsDir, { recursive: true });

  let verdicts;
  try {
    verdicts = await pool(active, args.concurrency, c => runVerify(c, c._evidenceAbs, c.tier || l.tier));
  } catch (e) {
    die(2, e.message);   // die() kills any sibling verify children still in LIVE
  }

  // Record. The script auto-settles only the clean cases; everything else parks in needs-action
  // for the session's judgment.
  const roundRec = { round: next, verified: [], results: {}, new_finding: false };
  active.forEach((c, i) => {
    const v = verdicts[i];
    const vf = path.join('verdicts', c.id + '-r' + next + '.json');
    fs.writeFileSync(path.join(ledgerDir, vf), JSON.stringify(v, null, 2) + '\n');
    const st = nextState(v.verdict, v.uncertainty);
    c.last_verdict = v.verdict;
    c.last_uncertainty = v.uncertainty || null;
    c.last_evidence_sha = c._evidenceSha;
    c.last_verdict_file = vf;
    c.history.push({ round: next, verdict: v.verdict, uncertainty: v.uncertainty || null,
      evidence_sha: c._evidenceSha, verdict_file: vf, recheck_reason: c.recheck_reason || null });
    if (c.state === 'recheck') c.recheck_reason = null;   // consumed: the reason is now in history
    c.state = st;
    roundRec.verified.push(c.id);
    roundRec.results[c.id] = { verdict: v.verdict, uncertainty: v.uncertainty || null, evidence_sha: c._evidenceSha, verdict_file: vf };
    if (st === 'needs-action') roundRec.new_finding = true;
  });

  l.round = next;
  l.rounds.push(roundRec);
  for (const c of l.claims) { delete c._evidenceAbs; delete c._evidenceSha; }
  writeLedger(ledgerPath, l);

  note('Round ' + next + ' complete. new_finding=' + roundRec.new_finding + '. Verdicts written to verdicts/.');
  printStatus(l);
  proposal(l);
}

main().catch(e => die(1, e && e.stack || String(e)));
