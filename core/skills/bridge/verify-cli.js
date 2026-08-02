#!/usr/bin/env node
'use strict';
/**
 * verify-cli — a thin, FAIL-LOUD command-line front-end for the cross-vendor-verify cross-vendor
 * `verify` tool. It drives the EXISTING codex-verify-server.js over MCP (initialize ->
 * tools/list -> tools/call) exactly as a mounted Claude Code session would, and prints the
 * verdict JSON. It adds no new mechanism — it just makes the proven server callable from ANY
 * session as a one-shot subprocess, without that session having to pre-mount the MCP config.
 *
 * Why a CLI instead of mounting the MCP per session:
 *   - Works from a session that did NOT launch with --mcp-config (like a running orchestrator).
 *   - Keeps the verify tool OUT of the ambient tool surface of credentialed/push sessions
 *     (taint co-residency) — you invoke it deliberately, you don't carry it.
 *   - One chokepoint to FAIL LOUD: a missing verifier, a garbage verdict, or a timeout exits
 *     non-zero with "VERIFY FAILED: ..." on stderr. It never prints a fake "looks fine."
 *   - It spawns codex.exe (which authenticates inside a Claude Code session); it never spawns
 *     `claude -p` (which 401s there). So the Claude->GPT direction works from inside CC.
 *
 * DIRECTION: this CLI always routes to the codex-verify server -> an OpenAI Codex/GPT verifier.
 * It is the CLAUDE-SIDE tool: the asker must be a NON-OpenAI substrate (e.g. Claude) for the
 * result to count as cross-vendor. A Codex/GPT asker must instead use claude-verify (-> Claude).
 * The skill that drives this CLI owns that verifier-not-equal-author rule.
 *
 * stdout = the verdict JSON ONLY (machine-consumable / pipeable).
 * stderr = all human diagnostics (banner, failures).
 *
 * Usage:
 *   node verify-cli.js --claim "<one falsifiable sentence>" \
 *                      --evidence-file <path to RAW primary artifacts> \
 *                      [--tier T2|T3|T4] [--model gpt-5.6-sol] [--effort medium] [--timeout-ms 180000]
 *   node verify-cli.js --claim "..." --evidence "<inline string>"   # small evidence only
 *
 * Exit codes: 0 = parseable verdict returned; 2 = verifier/tool error (isError);
 *             3 = unparseable verdict; 4 = timeout; 64 = usage error; 1 = internal error.
 */

const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const DEFAULT_SERVER = path.join(__dirname, 'codex-verify-server.js');

function die(code, msg) {
  process.stderr.write('VERIFY FAILED: ' + msg + '\n');
  process.exit(code);
}

function parseArgs(argv) {
  const a = { server: DEFAULT_SERVER, tier: '', model: '', effort: '', timeoutMs: 0, repoRoot: '', read: [] };
  for (let i = 2; i < argv.length; i++) {
    const k = argv[i];
    const next = () => { const v = argv[++i]; if (v === undefined) die(64, 'missing value for ' + k); return v; };
    switch (k) {
      case '--claim': a.claim = next(); break;
      case '--evidence': a.evidence = next(); break;
      case '--evidence-file': a.evidenceFile = next(); break;
      case '--tier': a.tier = next(); break;
      case '--model': a.model = next(); break;
      case '--effort': a.effort = next(); break;
      case '--timeout-ms': a.timeoutMs = parseInt(next(), 10); break;
      case '--server': a.server = next(); break;
      case '--repo-root': a.repoRoot = next(); break;            // opt-in repo-grounding
      case '--read': a.read.push(next()); break;                 // repeatable allowlist entry
      case '-h': case '--help': a.help = true; break;
      default: die(64, 'unknown argument: ' + k);
    }
  }
  return a;
}

const HELP = [
  'verify-cli — cross-vendor second opinion (Claude-side: routes to an OpenAI Codex/GPT verifier)',
  '',
  '  --claim         <text>   REQUIRED. One falsifiable sentence to adjudicate.',
  '  --evidence-file <path>   RAW primary artifacts (diff/test-output/source/data). PREFERRED.',
  '  --evidence      <text>   Inline evidence (small only; prefer --evidence-file for artifacts).',
  '  --tier          T2|T3|T4 Optional decision tier (informational).',
  '  --model         <id>     Verifier model (default gpt-5.6-sol).',
  '  --effort        <level>  model_reasoning_effort (default medium).',
  '  --timeout-ms    <n>      Verifier timeout (default server default, 180000).',
  '',
  '  REPO-GROUNDING (opt-in; default-deny; the bridge reads, the model is tool-less):',
  '  --repo-root     <path>   Absolute repo root the bridge may read from. Needs >=1 --read.',
  '  --read          <relpath> Repo-relative file or dir the bridge may read (repeatable).',
  '                           Secret paths (.env, *.pem/key, auth.json, .ssh/.aws/.codex) and any',
  '                           file with secret-shaped content are denied and fail the call closed.',
  '',
  'stdout = verdict JSON only. stderr = diagnostics. Non-zero exit on ANY failure.',
].join('\n');

function main() {
  const args = parseArgs(process.argv);
  if (args.help) { process.stderr.write(HELP + '\n'); process.exit(0); }
  if (!args.claim) die(64, 'no --claim given. ' + HELP);

  let evidence = args.evidence || '';
  if (args.evidenceFile) {
    try { evidence = fs.readFileSync(args.evidenceFile, 'utf8'); }
    catch (e) { die(64, 'could not read --evidence-file ' + args.evidenceFile + ': ' + e.message); }
    if (!evidence.trim()) die(64, '--evidence-file ' + args.evidenceFile + ' is empty');
  }
  if (!fs.existsSync(args.server)) die(64, 'server not found: ' + args.server);

  const env = Object.assign({}, process.env);
  if (args.model) env.VERIFY_MODEL = args.model;
  if (args.effort) env.VERIFY_EFFORT = args.effort;
  if (args.timeoutMs) env.VERIFY_TIMEOUT_MS = String(args.timeoutMs);

  // Guard a little longer than the verifier's own timeout so the inner timeout surfaces first.
  const serverTimeout = args.timeoutMs || parseInt(env.VERIFY_TIMEOUT_MS || '180000', 10);
  const guardMs = serverTimeout + 40000;

  process.stderr.write('[cross-check] verifier=openai/' + (args.model || env.VERIFY_MODEL || 'gpt-5.6-sol') +
    ' (asker MUST be a non-OpenAI substrate for this to count as cross-vendor)\n');

  const srv = spawn(process.execPath, [args.server], { stdio: ['pipe', 'pipe', 'inherit'], env });

  const guard = setTimeout(() => {
    try { srv.kill(); } catch (e) {}
    die(4, 'no verdict within ' + guardMs + 'ms (verifier may be rate-limited or hung)');
  }, guardMs);

  srv.on('error', e => { clearTimeout(guard); die(1, 'could not spawn server: ' + e.message); });
  srv.on('close', (code, signal) => {
    // If the server exits before we resolve a verdict, that's a loud failure.
    clearTimeout(guard);
    die(1, 'server exited early (code ' + code + (signal ? ', signal ' + signal : '') + ') before returning a verdict');
  });

  let buf = '';
  srv.stdout.on('data', d => {
    buf += d;
    let nl;
    while ((nl = buf.indexOf('\n')) !== -1) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      let msg;
      try { msg = JSON.parse(line); } catch (e) { continue; } // non-JSON server log noise
      handle(msg);
    }
  });

  function send(o) { srv.stdin.write(JSON.stringify(o) + '\n'); }

  function handle(msg) {
    if (msg.id === 1) {                       // initialize ok -> list tools
      send({ jsonrpc: '2.0', id: 2, method: 'tools/list' });
    } else if (msg.id === 2) {                // tools/list -> verify must be present
      const names = ((msg.result && msg.result.tools) || []).map(t => t.name);
      if (!names.includes('verify')) { clearTimeout(guard); srv.kill(); die(1, 'server exposes no `verify` tool (got: ' + names.join(',') + ')'); }
      const arguments_ = { claim: args.claim };
      if (evidence) arguments_.evidence = evidence;
      if (args.tier) arguments_.tier = args.tier;
      if (args.repoRoot) arguments_.repoRoot = args.repoRoot;
      if (args.read && args.read.length) arguments_.readAllowlist = args.read;
      send({ jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name: 'verify', arguments: arguments_ } });
    } else if (msg.id === 3) {                // verdict
      clearTimeout(guard);
      srv.removeAllListeners('close');        // we're done; the kill below is expected
      const result = msg.result;
      let text;
      try { text = result.content[0].text; } catch (e) { srv.kill(); die(1, 'malformed tool result: ' + JSON.stringify(msg)); }
      if (result.isError) { srv.kill(); die(2, text); }   // server already prefixes "VERIFY FAILED: ..."
      let verdict;
      try { verdict = JSON.parse(text); } catch (e) { srv.kill(); die(3, 'verifier returned non-JSON: ' + text.slice(0, 400)); }
      if (verdict.verdict === 'unparseable' || !verdict.verdict) { srv.kill(); die(3, 'verifier produced no usable verdict: ' + text.slice(0, 400)); }
      // Allowlist the verdict value: a non-enum string (schema drift, or the server's swallowed
      // output-schema write failure leaving codex unconstrained) must FAIL LOUD, not pass as exit 0.
      const VALID_VERDICTS = new Set(['confirmed', 'revised', 'rejected']);
      if (!VALID_VERDICTS.has(verdict.verdict)) { srv.kill(); die(3, 'verifier returned unrecognized verdict value: ' + JSON.stringify(verdict.verdict)); }
      const v = verdict.verifier || {};
      process.stderr.write('[cross-check] verdict=' + verdict.verdict + ' uncertainty=' + (verdict.uncertainty || '?') +
        ' verifier=' + (v.vendor || '?') + '/' + (v.model || '?') + '\n');
      // Flush stdout (a pipe/file write is async; exiting immediately can truncate the JSON).
      const finish = () => { try { srv.kill(); } catch (e) {} process.exit(0); };
      if (process.stdout.write(JSON.stringify(verdict, null, 2) + '\n')) finish();
      else process.stdout.once('drain', finish);
    } else if (msg.error) {
      clearTimeout(guard); srv.kill(); die(2, 'server JSON-RPC error: ' + JSON.stringify(msg.error));
    }
  }

  // kick off the handshake
  send({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2024-11-05', capabilities: {} } });
}

main();
