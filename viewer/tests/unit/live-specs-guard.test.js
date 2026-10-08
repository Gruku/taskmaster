// User intent: the live-server specs write viewer prefs, so they must never run against a real viewer by accident —
// only on an explicit opt-in, and for run_smoke.sh only against a backlog the caller has named.
import test, { before } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync, copyFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { requireLiveOptIn } from '../live-guard.js';

const TESTS_DIR = join(dirname(fileURLToPath(import.meta.url)), '..');
const VIEWER_DIR = join(TESTS_DIR, '..');
const optInFree = () => Object.fromEntries(Object.entries(process.env).filter(([k]) => !/^TM_LIVE_SPECS_/i.test(k)));

// Temp trees this file makes; a run killed mid-test leaves its tree behind, so the next run sweeps the old ones.
const SMOKE_PREFIX = 'tm-smoke-guard-';
const RM = { recursive: true, force: true, maxRetries: 5, retryDelay: 100 };
const smokeDirs = () => readdirSync(tmpdir()).filter((n) => n.startsWith(SMOKE_PREFIX));
const made = new Set();
const stale = new Set();
before(() => {
  for (const name of smokeDirs()) {
    const dir = join(tmpdir(), name);
    try {
      if (Date.now() - statSync(dir).mtimeMs < 60 * 60 * 1000) continue;
      rmSync(dir, RM);
      stale.add(name);
    } catch { /* gone already, or still held: left for the next run */ }
  }
});

test('requireLiveOptIn refuses unless TM_LIVE_SPECS_OK is exactly 1', () => {
  for (const env of [{}, { TM_LIVE_SPECS_OK: '' }, { TM_LIVE_SPECS_OK: '0' }, { TM_LIVE_SPECS_OK: 'true' }, { TM_LIVE_SPECS_OK: 'yes' }]) {
    assert.throws(() => requireLiveOptIn(env, ['node', 'playwright', 'test']), /TM_LIVE_SPECS_OK=1/, JSON.stringify(env));
  }
  assert.doesNotThrow(() => requireLiveOptIn({ TM_LIVE_SPECS_OK: '1' }, ['node', 'playwright', 'test']));
});

test('requireLiveOptIn lets a --list through: listing runs nothing and writes nothing', () => {
  assert.doesNotThrow(() => requireLiveOptIn({}, ['node', 'playwright', 'test', '--list']));
});

test('the live Playwright config lists its tests without the opt-in, and still refuses to run them', () => {
  const cli = createRequire(join(VIEWER_DIR, 'package.json')).resolve('@playwright/test/cli');
  const pw = (...args) => spawnSync(process.execPath, [cli, 'test', '--config', 'tests/playwright.config.js', ...args], {
    cwd: VIEWER_DIR, env: optInFree(), encoding: 'utf8', timeout: 60_000,
  });
  const listed = pw('--list');
  assert.equal(listed.status, 0, listed.stdout + listed.stderr);
  const run = pw('no-spec-matches-this-filter');
  assert.notEqual(run.status, 0);
  assert.match(run.stdout + run.stderr, /TM_LIVE_SPECS_OK=1/);
});

test('the live Playwright config refuses to load without the opt-in', async () => {
  const url = pathToFileURL(join(TESTS_DIR, 'playwright.config.js')).href;
  const saved = process.env.TM_LIVE_SPECS_OK;
  try {
    delete process.env.TM_LIVE_SPECS_OK;
    await assert.rejects(() => import(`${url}?refused`), /TM_LIVE_SPECS_OK=1/);
    process.env.TM_LIVE_SPECS_OK = '1';
    const mod = await import(`${url}?allowed`);
    assert.ok(mod.default);
  } finally {
    if (saved === undefined) delete process.env.TM_LIVE_SPECS_OK; else process.env.TM_LIVE_SPECS_OK = saved;
  }
});

test('npm run test:e2e goes through the guarded config', () => {
  const pkg = JSON.parse(readFileSync(join(TESTS_DIR, '..', 'package.json'), 'utf8'));
  assert.match(pkg.scripts['test:e2e'], /--config tests\/playwright\.config\.js/);
});

test('every spec that names the live port itself checks the opt-in', () => {
  // A hardcoded URL does not need the config's baseURL, so the config's guard alone would not cover it.
  const unguarded = readdirSync(TESTS_DIR)
    .filter((n) => n.endsWith('.spec.js'))
    .filter((n) => {
      const src = readFileSync(join(TESTS_DIR, n), 'utf8');
      const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
      return /127\.0\.0\.1:8765|localhost:8765/.test(code) && !/^requireLiveOptIn\(\);\r?$/m.test(code);
    });
  assert.deepEqual(unguarded, []);
});

// ── run_smoke.sh, run inside a throwaway tree with python/curl/npx replaced by stubs ──
function findBash() {
  if (process.platform !== 'win32') return 'bash';
  // Not System32\bash.exe (WSL): it sees neither this PATH nor these paths.
  const roots = [process.env.ProgramFiles, process.env['ProgramFiles(x86)'], process.env.LOCALAPPDATA && join(process.env.LOCALAPPDATA, 'Programs')];
  for (const r of roots) {
    const p = r && join(r, 'Git', 'bin', 'bash.exe');
    if (p && existsSync(p)) return p;
  }
  return null;
}
const BASH = findBash();
const fwd = (p) => p.replaceAll('\\', '/');

// { withBacklog } puts .taskmaster/backlog.yaml in the parent the script resolves as TASKMASTER_ROOT.
// { portBusy } makes the stub curl answer before the script has started its own server.
function runSmoke({ env = {}, withBacklog = true, portBusy = false, thenWith = null } = {}) {
  const top = mkdtempSync(join(tmpdir(), SMOKE_PREFIX));
  made.add(top.slice(tmpdir().length + 1));
  const plugin = join(top, 'repo', 'plugin');
  const tests = join(plugin, 'viewer', 'tests');
  const bin = join(top, 'bin');
  mkdirSync(tests, { recursive: true });
  mkdirSync(bin);
  copyFileSync(join(TESTS_DIR, 'run_smoke.sh'), join(tests, 'run_smoke.sh'));
  if (withBacklog) {
    mkdirSync(join(top, 'repo', '.taskmaster'));
    writeFileSync(join(top, 'repo', '.taskmaster', 'backlog.yaml'), 'tasks: []\n');
  }
  const log = fwd(join(top, 'calls.log'));
  const started = fwd(join(top, 'server-started'));
  const stub = (name, body) => writeFileSync(join(bin, name), `#!/usr/bin/env bash\n${body}\n`, { mode: 0o755 });
  stub('python', `echo "python root=$TASKMASTER_ROOT" >> "${log}"; touch "${started}"; exit 0`);
  stub('curl', portBusy ? 'exit 0' : `[ -e "${started}" ]`);
  stub('npx', `echo "npx $*" >> "${log}"; exit 0`);
  const base = Object.fromEntries(Object.entries(process.env).filter(([k]) => !/^(TM_LIVE_SPECS_.*|TASKMASTER_ROOT)$/i.test(k)));
  // The stubs go on PATH inside the shell: Git Bash puts its own bin directories (which hold a real curl)
  // ahead of any PATH it inherits.
  const run = (extra) => {
    const r = spawnSync(BASH, ['-c', 'PATH="$(cd "$1" && pwd):$PATH" exec bash "$2"', 'bash', fwd(bin), fwd(join(tests, 'run_smoke.sh'))], {
      env: { ...base, ...extra }, encoding: 'utf8', timeout: 30_000,
    });
    return { status: r.status, stderr: r.stderr, stdout: r.stdout, calls: existsSync(log) ? readFileSync(log, 'utf8') : '' };
  };
  try {
    const first = run(env);
    // thenWith(first) → env for a second run in the same tree (the root to name is only known from the refusal).
    return thenWith ? { first, second: run(thenWith(first)) } : first;
  } finally {
    // On Windows a Git Bash child can still hold a handle in the tree for a moment.
    rmSync(top, RM);
  }
}
const smoke = (name, fn) => test(name, { skip: BASH ? false : 'Git Bash not found' }, fn);

smoke('run_smoke.sh refuses without TM_LIVE_SPECS_OK=1 and starts nothing', () => {
  const r = runSmoke();
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /TM_LIVE_SPECS_OK=1/);
  assert.match(r.stderr, /write viewer prefs/i);
  assert.equal(r.calls, '');
});

smoke('run_smoke.sh refuses a root holding a backlog until that exact root is named', () => {
  const refused = runSmoke({ env: { TM_LIVE_SPECS_OK: '1' } });
  assert.notEqual(refused.status, 0);
  assert.equal(refused.calls, '');
  const named = refused.stderr.match(/TM_LIVE_SPECS_ROOT_OK=(\S+)/)?.[1];
  assert.ok(named, refused.stderr);
  assert.match(named, /\/repo$/);

  const wrong = runSmoke({ env: { TM_LIVE_SPECS_OK: '1', TM_LIVE_SPECS_ROOT_OK: '/some/other/repo' } });
  assert.notEqual(wrong.status, 0);
  assert.match(wrong.stderr, /TM_LIVE_SPECS_ROOT_OK=/);
  assert.equal(wrong.calls, '');

  const anyValue = runSmoke({ env: { TM_LIVE_SPECS_OK: '1', TM_LIVE_SPECS_ROOT_OK: '1' } });
  assert.notEqual(anyValue.status, 0);
  assert.equal(anyValue.calls, '');
});

smoke('run_smoke.sh runs against a backlog root once that root is named', () => {
  const { first, second } = runSmoke({
    env: { TM_LIVE_SPECS_OK: '1' },
    thenWith: (refused) => ({ TM_LIVE_SPECS_OK: '1', TM_LIVE_SPECS_ROOT_OK: refused.stderr.match(/TM_LIVE_SPECS_ROOT_OK=(\S+)/)[1] }),
  });
  assert.notEqual(first.status, 0);
  assert.equal(first.calls, '');
  assert.equal(second.status, 0, second.stderr);
  assert.match(second.calls, /python root=.*\/repo\n/);
  assert.match(second.calls, /npx playwright test\n/);
});

smoke('run_smoke.sh runs once the opt-in is given and no backlog is at the root', () => {
  const r = runSmoke({ env: { TM_LIVE_SPECS_OK: '1' }, withBacklog: false });
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.calls, /python root=.*\/plugin\n/);
  assert.match(r.calls, /npx playwright test\n/);
});

smoke('run_smoke.sh refuses when a viewer already answers on its port', () => {
  // Its own server could not bind, and the specs would write prefs to whoever holds the port.
  const r = runSmoke({ env: { TM_LIVE_SPECS_OK: '1' }, withBacklog: false, portBusy: true });
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /8765/);
  assert.equal(r.calls, '');
});

// Last in the file: what the tests above made is gone, and so are the stale trees swept before them. Only those names
// are compared — another suite running at the same time may have trees of its own in flight.
test('the smoke-guard tests leave no temp dirs behind', () => {
  const left = new Set(smokeDirs());
  assert.deepEqual([...made, ...stale].filter((n) => left.has(n)), []);
});
