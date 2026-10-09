#!/usr/bin/env node
'use strict';
/**
 * verify-cli — a thin, FAIL-LOUD command-line front-end for the cross-vendor-verify cross-vendor
 * `verify` tool. It drives one of the two bundled verifier servers over MCP (initialize ->
 * tools/list -> tools/call) exactly as a mounted session would, and prints the verdict JSON.
 * It adds no new mechanism — it just makes the proven servers callable from ANY session as a
 * one-shot subprocess, without that session having to pre-mount the MCP config.
 *
 * Why a CLI instead of mounting the MCP per session:
 *   - Works from a session that did NOT launch with --mcp-config (like a running orchestrator).
 *   - Keeps the verify tool OUT of the ambient tool surface of credentialed/push sessions
 *     (taint co-residency) — you invoke it deliberately, you don't carry it.
 *   - One chokepoint to FAIL LOUD: a missing verifier, a garbage verdict, or a timeout exits
 *     non-zero with "VERIFY FAILED: ..." on stderr. It never prints a fake "looks fine."
 *   - From inside a Claude Code session the OpenAI direction is the one that authenticates
 *     (codex.exe uses ~/.codex/auth.json; a nested `claude -p` 401s there). From a Codex session
 *     it is the reverse. The caller picks the direction; the CLI never assumes one.
 *
 * DIRECTION (v3.0-233): `--direction openai` (the DEFAULT when neither flag is given -- unchanged
 * behaviour) routes to codex-verify-server.js -> an OpenAI Codex/GPT verifier; `--direction
 * anthropic` routes to verify-server.js -> a contained, tool-less Anthropic Claude verifier;
 * `--server <path>` names any verify-protocol server explicitly. The banner and the verdict's
 * `verifier.vendor` reflect the server ACTUALLY used (each server stamps its own vendor). The
 * requester vendor (`--requester-vendor`, default the opposite of the verifier) is passed to the
 * server, which REFUSES a same-vendor pairing. The skill that drives this CLI still owns the
 * verifier-not-equal-author rule: it reads the artifact's author stamp and picks the direction.
 *
 * stdout = the verdict JSON ONLY (machine-consumable / pipeable).
 * stderr = all human diagnostics (banner, failures).
 *
 * Usage:
 *   node verify-cli.js --claim "<one falsifiable sentence>" \
 *                      --evidence-file <path to RAW primary artifacts> \
 *                      [--tier T2|T3|T4] [--model <id>] [--effort medium] [--timeout-ms 180000]
 *                      [--direction openai|anthropic | --server <path>] [--requester-vendor <vendor>]
 *   node verify-cli.js --claim "..." --evidence "<inline string>"   # small evidence only
 *
 * Exit codes: 0 = parseable verdict returned; 2 = verifier/tool error (isError);
 *             3 = unparseable verdict; 4 = timeout; 64 = usage error; 1 = internal error.
 */

const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

// The bundled servers, by the vendor that ANSWERS. The default direction stays openai (the
// Claude-side tool's historical behaviour); the skill picks per the artifact's author stamp.
const SERVERS = {
  openai: path.join(__dirname, 'codex-verify-server.js'),
  anthropic: path.join(__dirname, 'verify-server.js'),
};
const DEFAULT_DIRECTION = 'openai';
const DEFAULT_SERVER = SERVERS[DEFAULT_DIRECTION];

// Which vendor a server path answers as: a bundled server by its basename, else 'custom'
// (the verdict's verifier.vendor, stamped by the server itself, is the authority after the run).
function vendorOfServer(serverPath) {
  const b = path.basename(serverPath).toLowerCase();
  for (const v of Object.keys(SERVERS)) if (path.basename(SERVERS[v]).toLowerCase() === b) return v;
  return 'custom';
}

function die(code, msg) {
  process.stderr.write('VERIFY FAILED: ' + msg + '\n');
  process.exit(code);
}

function parseArgs(argv) {
  const a = { server: '', direction: '', requesterVendor: '', tier: '', model: '', effort: '', timeoutMs: 0, repoRoot: '', read: [] };
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
      case '--server': a.server = next(); break;                // any verify-protocol server
      case '--direction': a.direction = next(); break;          // openai|anthropic -> bundled server
      case '--requester-vendor': a.requesterVendor = next(); break;
      case '--repo-root': a.repoRoot = next(); break;            // opt-in repo-grounding
      case '--read': a.read.push(next()); break;                 // repeatable allowlist entry
      case '-h': case '--help': a.help = true; break;
      default: die(64, 'unknown argument: ' + k);
    }
  }
  return a;
}

const HELP = [
  'verify-cli — cross-vendor second opinion (routes to a contained OpenAI Codex/GPT OR Anthropic Claude verifier)',
  '',
  '  --claim         <text>   REQUIRED. One falsifiable sentence to adjudicate.',
  '  --evidence-file <path>   RAW primary artifacts (diff/test-output/source/data). PREFERRED.',
  '  --evidence      <text>   Inline evidence (small only; prefer --evidence-file for artifacts).',
  '  --tier          T2|T3|T4 Optional decision tier (informational).',
  '  --model         <id>     Verifier model (default: resolved -- VERIFY_MODEL, the operator registry,\n' +
  '                           the Codex CLI\'s own default, then a fallback; `node models.js`).',
  '  --effort        <level>  model_reasoning_effort (default medium).',
  '  --timeout-ms    <n>      Verifier timeout (default server default, 180000).',
  '',
  '  DIRECTION (who ANSWERS; default openai -- unchanged when neither flag is given):',
  '  --direction     openai|anthropic  openai -> codex-verify-server.js (contained `codex exec`);',
  '                           anthropic -> verify-server.js (contained, tool-less `claude -p`).',
  '  --server        <path>   Any verify-protocol MCP server script (overrides --direction; the',
  '                           banner then says custom/<basename> until the verdict names its vendor).',
  '  --requester-vendor <v>   Who AUTHORED the thing under test (default: the opposite of the',
  '                           verifier). Passed to the server as VERIFY_REQUESTER_VENDOR; a server',
  '                           REFUSES a requester equal to its own vendor (same-vendor is not cross-vendor).',
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
  // ---- direction / server selection (v3.0-233) ----
  if (args.direction && args.server) die(64, '--direction and --server are mutually exclusive');
  if (args.direction && !SERVERS[args.direction]) die(64, '--direction must be openai or anthropic (got ' + JSON.stringify(args.direction) + ')');
  const server = args.server || SERVERS[args.direction || DEFAULT_DIRECTION];
  if (!fs.existsSync(server)) die(64, 'server not found: ' + server);
  const vendor = args.direction || vendorOfServer(server);

  const env = Object.assign({}, process.env);
  if (args.model) env.VERIFY_MODEL = args.model;
  if (args.effort) env.VERIFY_EFFORT = args.effort;
  if (args.timeoutMs) env.VERIFY_TIMEOUT_MS = String(args.timeoutMs);
  if (args.requesterVendor) env.VERIFY_REQUESTER_VENDOR = args.requesterVendor;

  // Guard a little longer than the verifier's own timeout so the inner timeout surfaces first.
  const serverTimeout = args.timeoutMs || parseInt(env.VERIFY_TIMEOUT_MS || '180000', 10);
  const guardMs = serverTimeout + 40000;

  // The banner names the vendor of the server ACTUALLY used (never a hard-coded openai/) and the
  // model that vendor's row resolves to; a custom server shows its basename.
  const MODELS = require('./models.js');
  const bannerModel = (vendor === 'openai' || vendor === 'anthropic')
    ? MODELS.resolveModel(vendor, { explicit: args.model, envNames: ['VERIFY_MODEL'], env, skipLive: true }).model
    : path.basename(server);
  const rq = (vendor === 'openai' || vendor === 'anthropic')
    ? MODELS.resolveRequesterVendor(vendor, { explicit: args.requesterVendor, env, envName: 'VERIFY_REQUESTER_VENDOR' })
    : { vendor: args.requesterVendor || '?', error: null };
  if (rq.error) die(64, rq.error);
  process.stderr.write('[cross-check] verifier=' + vendor + '/' + bannerModel + ' requester=' + rq.vendor +
    (vendor === 'custom'
      ? ' (custom server; the verdict\'s verifier.vendor is the authority)\n'
      : ' (the asker must be a non-' + vendor + ' substrate for this to count as cross-vendor)\n'));

  const srv = spawn(process.execPath, [server], { stdio: ['pipe', 'pipe', 'inherit'], env });

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
