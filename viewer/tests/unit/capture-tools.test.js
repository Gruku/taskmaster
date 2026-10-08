// User intent: the screenshot tools must fail fast and leave nothing behind — a bad option or a busy port refuses
// before any directory is made, any server started or any browser launched.
import { after, test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import net from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const TOOLS = join(dirname(fileURLToPath(import.meta.url)), '..', 'tools');
const MODALS = join(TOOLS, 'capture-modals.mjs');
const CAPTURE = join(TOOLS, 'capture.mjs');
const tmp = mkdtempSync(join(tmpdir(), 'tm-capture-tools-'));
after(() => rmSync(tmp, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }));

const run = (tool, args) => spawnSync(process.execPath, [tool, ...args], { encoding: 'utf8', timeout: 20000 });

test('capture-modals: an unknown option value exits 2 before the output directory is made', () => {
  for (const [i, bad] of ['--only=nope', '--themes=dusk', '--widths=x'].entries()) {
    const out = join(tmp, `modals-bad-${i}`);
    const r = run(MODALS, [out, bad]);
    assert.equal(r.status, 2, `${bad}: ${r.stderr}`);
    assert.equal(existsSync(out), false, `${bad} made ${out}`);
  }
});

test('capture.mjs: an unknown --only exits 2 before the output directory is made', () => {
  const out = join(tmp, 'capture-bad');
  const r = run(CAPTURE, ['http://127.0.0.1:9', out, '--only=nope']);
  assert.equal(r.status, 2, r.stderr);
  assert.equal(existsSync(out), false);
});

test('capture-modals: a port in use exits 2 at once, says so, and launches no browser', async () => {
  const busy = net.createServer();
  await new Promise((ok) => busy.listen(0, '127.0.0.1', ok));
  const { port } = busy.address();
  const out = join(tmp, 'modals-busy');
  try {
    const t0 = Date.now();
    const r = run(MODALS, [out, `--port=${port}`, '--only=detail-rich']);
    assert.equal(r.status, 2, r.stderr);
    assert.ok(Date.now() - t0 < 10000, `took ${Date.now() - t0} ms`);
    assert.match(r.stderr, new RegExp(`port ${port} is in use; pass --port=<free port>`));
    assert.equal(existsSync(join(out, 'metrics.json')), false);
  } finally {
    await new Promise((ok) => busy.close(ok));
  }
});

test('capture-modals: relation-suggestions waits for the field to stop moving, not a fixed time', () => {
  const src = readFileSync(MODALS, 'utf8');
  const scene = src.slice(src.indexOf("['relation-suggestions'"), src.indexOf("['conflict-banner'"));
  assert.ok(scene.length > 0);
  assert.doesNotMatch(scene, /waitForTimeout/);
  assert.match(scene, /getBoundingClientRect\(\)\.top/);
  assert.match(scene, /requestAnimationFrame/);
});
