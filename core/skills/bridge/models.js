#!/usr/bin/env node
'use strict';
/*
 * models.js -- run-time model resolution for every cross-vendor leg (backlog v3.0-204, v3.0.58).
 *
 * THE PROBLEM IT ENDS. Every leg used to hard-code a model id ('gpt-5.6-sol', 'grok-4.5',
 * 'sonnet'), so the verifiers fell behind each provider's frontier the day a new model shipped
 * and stayed there until someone edited the harness -- and nobody noticed, because the verdicts
 * still came back. A lagging refuter is exactly the rubber-stamp risk the bridge's model floor
 * exists to prevent.
 *
 * THE RULE. A leg never names a model itself. It asks resolveModel(provider) and gets the first
 * answer from this chain:
 *   1. explicit   -- the caller's --model flag (one-off override)
 *   2. env        -- the leg's own environment variable (VERIFY_MODEL, HANDOFF_LEG_MODEL, ...)
 *   3. registry   -- the OPTIONAL operator file ~/.rheoscope/frontier-models.json, outside every
 *                    repository: {"openai": "...", "xai": "...", "anthropic": "..."}. For a
 *                    deliberate override only; absent by default.
 *   4. live       -- the provider CLI's OWN current default: ~/.codex/config.toml `model`,
 *                    `grok models` "Default model", ~/.claude/settings.json `model`.
 *   5. fallback   -- a shipped constant, used only when nothing above answers. For Anthropic it is
 *                    the alias 'fable', which the claude CLI maps to its newest top-tier model, so
 *                    even the fallback tracks the CLI rather than the harness.
 * When a provider's app or CLI moves its default to a new model, every leg follows with no
 * harness change. `node models.js` prints what each provider resolves to and where it came from;
 * the doctor shows the same and WARNs when a registry override lags the CLI's own default.
 *
 * Every verdict already records the model that actually ran, so the chain is auditable after
 * the fact. Threat-model note: the registry is an operator file outside the repo, like
 * ~/.codex/config.toml itself; a session that edits it changes which model verifies, and the
 * doctor line and each verdict's recorded model are what make that visible.
 */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const MODEL_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:\-]{0,79}$/;   // an id, never an argv fragment

const FALLBACK = { openai: 'gpt-6-astra', xai: 'grok-4.7', anthropic: 'fable' };
// STRENGTH FLOOR (review round 1, 2026-09-29): a judge leg never runs a weak tier, whichever source
// names it -- an id matching its provider's pattern is SKIPPED and the chain continues. Names are
// not a ranking, so this is a floor on known-weak tiers only, not a proof of strength.
const WEAK = { openai: /(^|[-_.])(mini|nano|lite)([-_.]|$)/i, xai: /(^|[-_.])(fast|mini)([-_.]|$)/i,
               anthropic: /haiku/i };
// one row per LEG, each with the env name that leg actually reads (review round 2: merging
// VERIFY_MODEL and HANDOFF_LEG_MODEL into one row reported a handoff-only override as the
// verifier's model). VERIFY_MODEL is read by BOTH directions, each in its own process.
const LEGS = [
  { leg: 'openai/verify', provider: 'openai', env: ['VERIFY_MODEL'] },
  { leg: 'openai/handoff', provider: 'openai', env: ['HANDOFF_LEG_MODEL'] },
  { leg: 'xai/verify', provider: 'xai', env: ['GROK_VERIFY_MODEL'] },
  { leg: 'anthropic/verify', provider: 'anthropic', env: ['VERIFY_MODEL'] },
  // v3.0-234: the Claude-direction handoff leg (handoff-leg.js --vendor anthropic) reads the same
  // HANDOFF_LEG_MODEL its OpenAI sibling does, each in its own process for its own provider.
  { leg: 'anthropic/handoff', provider: 'anthropic', env: ['HANDOFF_LEG_MODEL'] },
];
const PROVIDERS = Object.keys(FALLBACK);

// ---- vendor identity helpers (v3.0-233 / v3.0-234) --------------------------------------------
// Every leg's prompt used to carry the literal "The requester is a different AI vendor (Anthropic
// Claude)". Under a Codex-led project that sentence is false, and a close leg that believes it
// locks a same-family T1. The requester vendor is now an ARGUMENT (--requester-vendor /
// VERIFY_REQUESTER_VENDOR), defaulting to the opposite of the leg's own vendor, and the prompt
// text is built from it here -- one home, both directions. A leg whose requester equals its own
// vendor REFUSES (verify: isError; handoff-leg: exit 64) -- a same-family leg is not a leg.
const VENDOR_NAMES = {
  anthropic: 'Anthropic Claude',
  openai: 'OpenAI Codex/GPT',
  xai: 'xAI Grok',
  google: 'Google Gemini',
};
const VENDOR_ALIASES = { claude: 'anthropic', codex: 'openai', gpt: 'openai', grok: 'xai', gemini: 'google' };
const OPPOSITE_VENDOR = { anthropic: 'openai', openai: 'anthropic', xai: 'anthropic', google: 'anthropic' };

function normalizeVendor(v) {
  const t = (typeof v === 'string' ? v.trim().toLowerCase() : '');
  if (!t) return null;
  if (VENDOR_NAMES[t]) return t;
  return VENDOR_ALIASES[t] || null;           // unknown token -> null (caller decides: refuse)
}
function vendorDisplay(v) { return VENDOR_NAMES[v] || String(v); }
function oppositeVendor(v) { return OPPOSITE_VENDOR[v] || 'anthropic'; }

/**
 * resolveRequesterVendor(legVendor, {explicit, env, envName})
 *   -> {vendor, source, error}  source in explicit|env:<NAME>|default
 * The leg vendor is the substrate the leg itself runs (the verifier/closer). The requester is
 * who authored the work under test. error is set (vendor null) when the value is unknown or
 * equals the leg vendor -- the caller MUST refuse, never run.
 */
// v3.0.63 review round 1 (packet A): the default requester is read from the SESSION's own agent
// markers -- the same ones the doctor's session_driver() reads -- never assumed. A Codex session that
// passes no flag is an OpenAI requester; defaulting it to "the opposite of the leg" labelled a
// GPT-reviews-GPT verdict cross-vendor.
const CLAUDE_MARKERS = ['CLAUDECODE', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_ENTRYPOINT'];
const CODEX_MARKERS = ['CODEX_SANDBOX', 'CODEX_THREAD_ID', 'CODEX_SESSION_ID', 'CODEX_HOME_SESSION'];
function sessionVendor(env) {
  const e = env || process.env;
  const on = k => e[k] != null && String(e[k]).trim() !== '';
  const c = CLAUDE_MARKERS.some(on), x = CODEX_MARKERS.some(on);
  return (c && x) ? 'both' : c ? 'anthropic' : x ? 'openai' : null;
}

function resolveRequesterVendor(legVendor, opts) {
  const o = opts || {};
  const env = o.env || process.env;
  let raw = null, source = 'default';
  if (o.explicit != null && String(o.explicit).trim() !== '') { raw = o.explicit; source = 'explicit'; }
  else if (o.envName && env[o.envName] != null && String(env[o.envName]).trim() !== '') { raw = env[o.envName]; source = 'env:' + o.envName; }
  if (raw == null) {
    const sv = sessionVendor(env);
    if (sv === 'both') return { vendor: null, source: 'session', error: 'this process carries BOTH Claude and ' +
      'Codex session markers, so who is asking is ambiguous -- pass --requester-vendor explicitly' };
    if (sv) { raw = sv; source = 'session'; }
    else return { vendor: oppositeVendor(legVendor), source: 'default-assumed', error: null };
  }
  const v = normalizeVendor(raw);
  if (!v) return { vendor: null, source, error: 'unknown requester vendor ' + JSON.stringify(String(raw)) +
    ' (known: ' + Object.keys(VENDOR_NAMES).join('|') + ')' };
  if (v === legVendor) return { vendor: null, source, error: 'requester vendor ' + v + ' equals the leg vendor ' +
    legVendor + ' -- a same-vendor leg is not cross-vendor; pick the other direction' };
  return { vendor: v, source, error: null };
}

/** The sentence every leg prompt opens with, built from the real requester vendor. */
function requesterSentence(requesterVendor, legVendor) {
  return 'The requester is a different AI vendor (' + vendorDisplay(requesterVendor) + '); you are ' +
    vendorDisplay(legVendor) + '.';
}

function registryPath(home) {
  return path.join(home || os.homedir() || '', '.rheoscope', 'frontier-models.json');
}

function cleanId(v) {
  return (typeof v === 'string' && MODEL_ID_RE.test(v.trim())) ? v.trim() : null;
}

function readRegistry(home) {
  try {
    const d = JSON.parse(fs.readFileSync(registryPath(home), 'utf8'));
    return (d && typeof d === 'object') ? d : {};
  } catch (e) { return {}; }
}

// ---- live sources: each provider CLI's own current default --------------------------------
function codexConfigModel(home) {
  // top-level `model = "..."` in ~/.codex/config.toml (before the first [section])
  try {
    const text = fs.readFileSync(path.join(home || os.homedir() || '', '.codex', 'config.toml'), 'utf8');
    for (const line of text.split(/\r?\n/)) {
      if (/^\s*\[/.test(line)) break;
      const m = /^\s*model\s*=\s*"([^"]+)"\s*(#.*)?$/.exec(line);
      if (m) return cleanId(m[1]);
    }
  } catch (e) { /* no config */ }
  return null;
}

function grokBin(home) {
  const h = home || os.homedir() || '';
  const own = path.join(h, '.grok', 'bin', process.platform === 'win32' ? 'grok.exe' : 'grok');
  try { if (fs.statSync(own).isFile()) return own; } catch (e) { /* fall through */ }
  return 'grok';
}

function grokDefaultModel(home, runner) {
  // `grok models` prints "Default model: <id>" (it does so even before auth)
  const run = runner || ((bin, args) => spawnSync(bin, args, { encoding: 'utf8', timeout: 20000, windowsHide: true }));
  try {
    const r = run(grokBin(home), ['models']);
    const m = /Default model:\s*(\S+)/.exec(((r && r.stdout) || '') + '\n' + ((r && r.stderr) || ''));
    return m ? cleanId(m[1]) : null;
  } catch (e) { return null; }
}

function claudeSettingsModel(home) {
  try {
    const d = JSON.parse(fs.readFileSync(path.join(home || os.homedir() || '', '.claude', 'settings.json'), 'utf8'));
    return cleanId(d && d.model);
  } catch (e) { return null; }
}

function liveDefault(provider, opts) {
  const o = opts || {};
  if (provider === 'openai') return codexConfigModel(o.home);
  if (provider === 'xai') return grokDefaultModel(o.home, o.grokRunner);
  if (provider === 'anthropic') return claudeSettingsModel(o.home);
  return null;
}

/**
 * resolveModel(provider, {explicit, envNames, env, home, grokRunner})
 *   -> {model, source, live}   source in explicit|env:<NAME>|registry|live|fallback
 * `live` is the CLI's own default when it was looked up (null otherwise) -- the doctor compares
 * it with a registry override to report lag.
 */
function resolveModel(provider, opts) {
  const o = opts || {};
  if (!Object.prototype.hasOwnProperty.call(FALLBACK, provider)) throw new Error('unknown provider ' + provider);
  const env = o.env || process.env;
  const skipped = [];                                   // weak ids named somewhere, and ignored
  const strong = (v, where) => {
    if (!v) return null;
    if (WEAK[provider].test(v)) { skipped.push(where + '=' + v); return null; }
    return v;
  };
  const ex = strong(cleanId(o.explicit), 'explicit');
  if (ex) return { model: ex, source: 'explicit', live: null, skipped };
  for (const name of (o.envNames || [])) {
    const v = strong(cleanId(env[name]), 'env:' + name);
    if (v) return { model: v, source: 'env:' + name, live: null, skipped };
  }
  const reg = strong(cleanId(readRegistry(o.home)[provider]), 'registry');
  const liveRaw = (o.skipLive && reg) ? null : liveDefault(provider, o);
  const live = strong(liveRaw, 'live');
  if (reg) return { model: reg, source: 'registry', live, skipped };
  if (live) return { model: live, source: 'live', live, skipped };
  return { model: FALLBACK[provider], source: 'fallback', live: null, skipped };
}

function report(opts) {
  const o = opts || {};
  const env = o.env || process.env;
  const liveCache = {};
  return LEGS.map(L => {
    const p = L.provider;
    if (!(p in liveCache)) liveCache[p] = liveDefault(p, o);
    const r = resolveModel(p, Object.assign({}, o, { envNames: L.env }));
    // EVERY source is inspected for weak tiers and for a registry/default difference, whichever
    // source wins (review round 2: a strong env override used to mask a weak registry line)
    const reg = cleanId(readRegistry(o.home)[p]);
    const live = cleanId(liveCache[p]);
    const named = [];
    for (const n of L.env) { const v = cleanId(env[n]); if (v) named.push(['env:' + n, v]); }
    if (reg) named.push(['registry', reg]);
    if (live) named.push(['live', live]);
    const weak = Array.from(new Set(named.filter(([, v]) => WEAK[p].test(v)).map(([w, v]) => w + '=' + v)
      .concat(r.skipped || [])));             // + anything resolveModel skipped (an explicit value)
    // 'differs', not 'lags': model names are not a ranking (review round 1)
    return { leg: L.leg, provider: p, model: r.model, source: r.source, live_default: live, registry: reg,
             overrides_live: !!reg && !!live && reg !== live && !WEAK[p].test(reg) && !WEAK[p].test(live),
             skipped_weak: weak };
  });
}

module.exports = { resolveModel, report, registryPath, FALLBACK, PROVIDERS, WEAK, LEGS,
                   codexConfigModel, grokDefaultModel, claudeSettingsModel,
                   VENDOR_NAMES, normalizeVendor, vendorDisplay, oppositeVendor, sessionVendor,
                   resolveRequesterVendor, requesterSentence };

// ---------------- CLI + hermetic self-test ----------------
if (require.main === module) {
  const argv = process.argv.slice(2);
  if (argv.includes('--self-test')) {
    let pass = 0, fail = 0;
    const ok = (name, cond) => { if (cond) pass++; else { fail++; process.stdout.write('  FAIL ' + name + '\n'); } };
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'models-st-'));
    const noGrok = () => ({ stdout: '', stderr: '' });
    try {
      const E = {};
      // empty home -> fallbacks
      let r = resolveModel('openai', { home: tmp, env: E, grokRunner: noGrok });
      ok('empty home: openai falls back', r.model === FALLBACK.openai && r.source === 'fallback');
      r = resolveModel('anthropic', { home: tmp, env: E });
      ok('empty home: anthropic falls back to the tracking alias', r.model === 'fable' && r.source === 'fallback');
      // live sources
      fs.mkdirSync(path.join(tmp, '.codex'));
      fs.writeFileSync(path.join(tmp, '.codex', 'config.toml'),
        'model = "gpt-7-nova"\npersonality = "x"\n[profiles.fast]\nmodel = "gpt-mini"\n');
      r = resolveModel('openai', { home: tmp, env: E });
      ok('live: codex config top-level model', r.model === 'gpt-7-nova' && r.source === 'live');
      fs.writeFileSync(path.join(tmp, '.codex', 'config.toml'), '[profiles.fast]\nmodel = "gpt-mini"\n');
      r = resolveModel('openai', { home: tmp, env: E });
      ok('live: a model under a [section] is NOT the default', r.source === 'fallback');
      r = resolveModel('xai', { home: tmp, env: E, grokRunner: () => ({ stdout: 'Default model: grok-5\n\nAvailable models:\n  * grok-5 (default)\n' }) });
      ok('live: grok models "Default model"', r.model === 'grok-5' && r.source === 'live');
      r = resolveModel('xai', { home: tmp, env: E, grokRunner: () => { throw new Error('no grok'); } });
      ok('live: grok missing -> fallback', r.model === FALLBACK.xai && r.source === 'fallback');
      fs.mkdirSync(path.join(tmp, '.claude'));
      fs.writeFileSync(path.join(tmp, '.claude', 'settings.json'), JSON.stringify({ model: 'opus' }));
      r = resolveModel('anthropic', { home: tmp, env: E });
      ok('live: claude settings model', r.model === 'opus' && r.source === 'live');
      // registry beats live; lag is reported
      fs.mkdirSync(path.join(tmp, '.rheoscope'));
      fs.writeFileSync(registryPath(tmp), JSON.stringify({ openai: 'gpt-6-astra' }));
      fs.writeFileSync(path.join(tmp, '.codex', 'config.toml'), 'model = "gpt-7-nova"\n');
      r = resolveModel('openai', { home: tmp, env: E });
      ok('registry beats live', r.model === 'gpt-6-astra' && r.source === 'registry' && r.live === 'gpt-7-nova');
      const rows = report({ home: tmp, env: E, grokRunner: noGrok });
      ok('report flags a registry override that differs from the CLI default',
         rows.find(x => x.provider === 'openai').overrides_live === true);
      // env beats registry; explicit beats env
      r = resolveModel('openai', { home: tmp, env: { VERIFY_MODEL: 'gpt-x-env' }, envNames: ['VERIFY_MODEL'] });
      ok('env beats registry', r.model === 'gpt-x-env' && r.source === 'env:VERIFY_MODEL');
      r = resolveModel('openai', { home: tmp, explicit: 'gpt-y', env: { VERIFY_MODEL: 'gpt-x-env' }, envNames: ['VERIFY_MODEL'] });
      ok('explicit beats env', r.model === 'gpt-y' && r.source === 'explicit');
      // hostile values are ignored, never passed on
      fs.writeFileSync(registryPath(tmp), JSON.stringify({ openai: 'gpt-6 --dangerously-bypass' }));
      r = resolveModel('openai', { home: tmp, env: E });
      ok('a malformed registry value is ignored (falls to live)', r.model === 'gpt-7-nova' && r.source === 'live');
      r = resolveModel('openai', { home: tmp, explicit: '-c evil=1', env: E });
      ok('a malformed explicit value is ignored', r.source !== 'explicit');
      // strength floor: a weak tier is skipped from ANY source, and recorded
      fs.writeFileSync(registryPath(tmp), JSON.stringify({ anthropic: 'haiku', openai: 'gpt-6-mini' }));
      r = resolveModel('anthropic', { home: tmp, env: E });
      ok('a weak registry tier (haiku) is skipped -> the live default wins', r.model === 'opus' && r.skipped.length === 1);
      r = resolveModel('openai', { home: tmp, env: E });
      ok('a weak registry tier (gpt-6-mini) is skipped', r.model === 'gpt-7-nova' && r.source === 'live');
      r = resolveModel('openai', { home: tmp, explicit: 'gpt-5-nano', env: E });
      ok('a weak EXPLICIT model is skipped too', r.source !== 'explicit' && r.skipped[0] === 'explicit=gpt-5-nano');
      r = resolveModel('xai', { home: tmp, env: { GROK_VERIFY_MODEL: 'grok-4.7-build-fast' }, envNames: ['GROK_VERIFY_MODEL'], grokRunner: noGrok });
      ok('a weak xai tier (-fast) is skipped', r.model === FALLBACK.xai);
      r = resolveModel('openai', { home: tmp, explicit: 'gpt-6-astra', env: E });
      ok('a strong id containing no weak token is kept (astra)', r.model === 'gpt-6-astra');
      const rows2 = report({ home: tmp, env: { VERIFY_MODEL: 'gpt-x-env' }, grokRunner: noGrok });
      ok('report shows an env override the verify leg would read', rows2.find(x => x.leg === 'openai/verify').source === 'env:VERIFY_MODEL');
      ok('...and does NOT attribute it to the handoff leg (review round 2)', rows2.find(x => x.leg === 'openai/handoff').source !== 'env:VERIFY_MODEL');
      const rows3 = report({ home: tmp, env: { VERIFY_MODEL: 'gpt-6-astra' }, grokRunner: noGrok });
      ok('a strong env override does NOT mask a weak registry line (review round 2)',
         rows3.find(x => x.leg === 'openai/verify').skipped_weak.includes('registry=gpt-6-mini'));
      const rows4 = report({ home: tmp, explicit: 'haiku', env: E, grokRunner: noGrok });
      ok('report lists a weak EXPLICIT value too (review round 3)',
         rows4.find(x => x.leg === 'anthropic/verify').skipped_weak.includes('explicit=haiku'));
      fs.writeFileSync(registryPath(tmp), '{not json');
      r = resolveModel('openai', { home: tmp, env: E });
      ok('an unreadable registry is ignored', r.source === 'live');
      // v3.0-234: the anthropic handoff row reads HANDOFF_LEG_MODEL, not VERIFY_MODEL
      const rows5 = report({ home: tmp, env: { HANDOFF_LEG_MODEL: 'opus' }, grokRunner: noGrok });
      ok('anthropic/handoff row exists and reads HANDOFF_LEG_MODEL',
         rows5.find(x => x.leg === 'anthropic/handoff').source === 'env:HANDOFF_LEG_MODEL');
      ok('...and anthropic/verify does NOT read it', rows5.find(x => x.leg === 'anthropic/verify').source !== 'env:HANDOFF_LEG_MODEL');
      // v3.0-233/234: requester vendor is an argument; same-vendor refuses; unknown refuses
      let q = resolveRequesterVendor('openai', { env: E });
      ok('with no session marker, requester defaults to the opposite of the leg vendor, labelled assumed',
         q.vendor === 'anthropic' && q.source === 'default-assumed');
      // v3.0.63 review round 1: the default comes from the session's own markers
      q = resolveRequesterVendor('openai', { env: { CODEX_SANDBOX: 'seatbelt' } });
      ok('a CODEX session asking the OpenAI verifier with no flag is REFUSED (GPT reviewing GPT)',
         q.vendor === null && q.source === 'session' && /equals the leg vendor/.test(q.error));
      q = resolveRequesterVendor('anthropic', { env: { CODEX_SANDBOX: 'seatbelt' } });
      ok('...and the same session asking the Claude verifier is an OpenAI requester',
         q.vendor === 'openai' && q.source === 'session');
      q = resolveRequesterVendor('openai', { env: { CLAUDECODE: '1' } });
      ok('a Claude session asking the OpenAI verifier is an Anthropic requester',
         q.vendor === 'anthropic' && q.source === 'session');
      q = resolveRequesterVendor('openai', { env: { CLAUDECODE: '1', CODEX_SANDBOX: 'x' } });
      ok('both markers with no flag is ambiguous and REFUSED', q.vendor === null && /ambiguous/.test(q.error));
      q = resolveRequesterVendor('openai', { explicit: 'anthropic', env: { CODEX_SANDBOX: 'x' } });
      ok('an explicit flag still wins over the markers', q.vendor === 'anthropic' && q.source === 'explicit');
      q = resolveRequesterVendor('anthropic', { env: E });
      ok('...in both directions', q.vendor === 'openai');
      q = resolveRequesterVendor('openai', { explicit: 'Claude', env: E });
      ok('an alias token (Claude) normalizes', q.vendor === 'anthropic' && q.source === 'explicit');
      q = resolveRequesterVendor('openai', { env: { X: 'openai' }, envName: 'X' });
      ok('requester == leg vendor is REFUSED', q.vendor === null && /equals the leg vendor/.test(q.error));
      q = resolveRequesterVendor('openai', { explicit: 'martian', env: E });
      ok('an unknown vendor token is REFUSED', q.vendor === null && /unknown requester vendor/.test(q.error));
      ok('the prompt sentence names the real requester', requesterSentence('openai', 'anthropic') ===
         'The requester is a different AI vendor (OpenAI Codex/GPT); you are Anthropic Claude.');
    } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
    process.stdout.write('models self-test: ' + (fail ? 'FAIL' : 'PASS') + ' (' + pass + '/' + (pass + fail) + ')\n');
    process.exit(fail ? 1 : 0);
  }
  const rows = report({});
  if (argv.includes('--json')) { process.stdout.write(JSON.stringify(rows) + '\n'); process.exit(0); }
  for (const x of rows) {
    process.stdout.write(x.leg.padEnd(17) + ' ' + x.model.padEnd(20) + ' (' + x.source +
      (x.overrides_live ? '; differs from the CLI default ' + x.live_default : '') +
      (x.skipped_weak.length ? '; weak tier ignored: ' + x.skipped_weak.join(', ') : '') + ')\n');
  }
}
