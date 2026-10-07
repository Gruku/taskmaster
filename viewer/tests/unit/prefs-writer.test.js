// User intent: a saved preference (the theme above all) must never be dropped because another preference changed a moment later.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createPrefsWriter, deepMerge } from '../../js/lib/prefs-writer.js';

// Timers fired by hand, all at once; `delays()` names how long each pending one asked for.
function fakeTimers() {
  let next = 1;
  const timers = new Map();
  const delays = new Map();
  return {
    set: (fn, ms) => { const id = next++; timers.set(id, fn); delays.set(id, ms); return id; },
    clear: (id) => { timers.delete(id); },
    pending: () => timers.size,
    delays: () => [...timers.keys()].map((id) => delays.get(id)),
    fire() {
      const all = [...timers.values()];
      timers.clear();
      for (const fn of all) fn();
    },
  };
}
const settle = () => new Promise((r) => setImmediate(r));

function harness(save) {
  const t = fakeTimers();
  const errors = [];
  const writer = createPrefsWriter({ save, delayMs: 400, setTimer: t.set, clearTimer: t.clear, onError: (e, info) => errors.push({ e, ...info }) });
  return { t, writer, errors };
}

test('two patches within the window produce one save containing both', async () => {
  const saved = [];
  const { t, writer } = harness(async (p) => { saved.push(p); });
  writer.queue({ theme: 'light' });
  writer.queue({ ui: { last_task_id: 'T-1' } });
  assert.equal(t.pending(), 1);
  assert.deepEqual(saved, []);
  t.fire();
  await settle();
  assert.deepEqual(saved, [{ theme: 'light', ui: { last_task_id: 'T-1' } }]);
  assert.equal(t.pending(), 0);
});

test('nested keys merge rather than replace; a later value for the same key wins', async () => {
  const saved = [];
  const { t, writer } = harness(async (p) => { saved.push(p); });
  writer.queue({ ui: { sidebar_collapsed: true }, screens: { issues: { view: 'list' } } });
  writer.queue({ ui: { last_task_id: 'T-2' }, screens: { issues: { view: 'board' }, bugs: { filters: ['open'] } } });
  t.fire();
  await settle();
  assert.deepEqual(saved, [{
    ui: { sidebar_collapsed: true, last_task_id: 'T-2' },
    screens: { issues: { view: 'board' }, bugs: { filters: ['open'] } },
  }]);
});

test('a patch arriving while a save is in flight is sent afterwards, not lost and not overlapped', async () => {
  const saved = [];
  let release;
  const { t, writer } = harness((p) => {
    saved.push(p);
    return new Promise((ok) => { release = ok; });
  });
  writer.queue({ theme: 'light' });
  t.fire();
  assert.deepEqual(saved, [{ theme: 'light' }]);
  writer.queue({ ui: { sidebar_collapsed: true } });
  t.fire();                       // debounce elapses while the first save is still open
  await settle();
  assert.deepEqual(saved, [{ theme: 'light' }]);
  release();
  await settle();
  assert.deepEqual(saved, [{ theme: 'light' }, { ui: { sidebar_collapsed: true } }]);
});

test('a patch queued during a save still waits out its own debounce', async () => {
  const saved = [];
  let release;
  const { t, writer } = harness((p) => {
    saved.push(p);
    return new Promise((ok) => { release = ok; });
  });
  writer.queue({ theme: 'dark' });
  t.fire();
  writer.queue({ card_density: 'compact' });
  release();
  await settle();
  assert.equal(saved.length, 1);  // its timer has not fired yet
  t.fire();
  await settle();
  assert.deepEqual(saved, [{ theme: 'dark' }, { card_density: 'compact' }]);
});

test('a failed save is retried with newer patches merged over it, newest wins, and given up after three tries', async () => {
  const saved = [];
  let failing = true;
  const { t, writer, errors } = harness(async (p, opts) => {
    saved.push({ p: structuredClone(p), opts });
    if (failing) throw new Error('PUT /api/viewer/prefs → 500');
  });
  writer.queue({ theme: 'light', ui: { a: 1 } });
  t.fire();
  await settle();
  assert.equal(saved.length, 1);
  assert.deepEqual(t.delays(), [800]);             // the first retry waits retryDelayMs(1)
  assert.equal(errors.length, 0);                  // not given up yet: nothing reported
  writer.queue({ theme: 'dark' });                 // while the retry waits
  assert.deepEqual(t.delays(), [800]);             // it rides the retry rather than jumping ahead of it
  t.fire();
  await settle();
  assert.equal(saved.length, 2);
  assert.deepEqual(saved[1].p, { theme: 'dark', ui: { a: 1 } });
  t.fire(); await settle();
  t.fire(); await settle();
  assert.equal(saved.length, 4);                   // the first save and three retries
  assert.equal(errors.length, 1);
  assert.deepEqual(errors[0].dropped, { theme: 'dark', ui: { a: 1 } });
  assert.match(String(errors[0].e), /500/);
  assert.equal(t.pending(), 0);
  t.fire(); await settle();
  assert.equal(saved.length, 4);                   // no further save
  // A later patch starts a fresh count: it fails, and is retried rather than given up at once.
  writer.queue({ card_density: 'compact' });
  t.fire(); await settle();
  assert.equal(saved.length, 5);
  assert.deepEqual(saved[4].p, { card_density: 'compact' });
  assert.equal(errors.length, 1);
  assert.equal(t.pending(), 1);
  failing = false;
  t.fire(); await settle();
  assert.deepEqual(saved[5].p, { card_density: 'compact' });
  assert.equal(t.pending(), 0);
  assert.equal(errors.length, 1);
});

test('a success resets the failure count', async () => {
  let calls = 0;
  const fails = new Set([1, 2, 3, 5, 6, 7]);      // attempt 4 succeeds, so attempts 5–7 are a new run of three
  const { t, writer, errors } = harness(async () => { calls++; if (fails.has(calls)) throw new Error('down'); });
  writer.queue({ theme: 'light' });
  for (let i = 0; i < 4; i++) { t.fire(); await settle(); }
  assert.equal(calls, 4);
  assert.equal(errors.length, 0);
  writer.queue({ theme: 'dark' });
  for (let i = 0; i < 4; i++) { t.fire(); await settle(); }
  assert.equal(calls, 8);                          // 5, 6, 7 fail; 8 is the third retry and succeeds
  assert.equal(errors.length, 0);
});

test('a save that throws synchronously is retried the same way', async () => {
  const saved = [];
  const { t, writer, errors } = harness((p) => { saved.push(p); if (saved.length === 1) throw new Error('boom'); return Promise.resolve(); });
  writer.queue({ theme: 'light' });
  t.fire();
  await settle();
  t.fire();
  await settle();
  assert.deepEqual(saved, [{ theme: 'light' }, { theme: 'light' }]);
  assert.equal(errors.length, 0);
});

test('flush sends what is pending at once with keepalive, even during a save', async () => {
  const calls = [];
  const { t, writer } = harness((p, opts) => { calls.push([structuredClone(p), opts]); return new Promise(() => {}); });
  writer.queue({ theme: 'light' });
  t.fire();                                        // the first save is held open
  writer.queue({ x: 1 });
  writer.flush();
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1], [{ x: 1 }, { keepalive: true }]);
  assert.equal(t.pending(), 0);                    // its debounce is cleared: it is not sent twice
  writer.flush();                                  // nothing pending: nothing sent
  assert.equal(calls.length, 2);
});

test('flush with nothing pending calls nothing', async () => {
  const calls = [];
  const { writer } = harness(async (p, opts) => { calls.push([p, opts]); });
  writer.flush();
  assert.deepEqual(calls, []);
});

test('flush sends a batch waiting for its retry, and does not retry it again', async () => {
  const calls = [];
  const { t, writer, errors } = harness(async (p, opts) => { calls.push([structuredClone(p), opts]); throw new Error('down'); });
  writer.queue({ theme: 'light' });
  t.fire();
  await settle();
  writer.queue({ ui: { a: 1 } });
  writer.flush();
  assert.deepEqual(calls[1], [{ theme: 'light', ui: { a: 1 } }, { keepalive: true }]);
  assert.equal(t.pending(), 0);
  await settle();
  t.fire(); await settle();
  assert.equal(calls.length, 2);
  assert.equal(errors.length, 1);                  // the page is going away: what flush could not send is given up
  assert.deepEqual(errors[0].dropped, { theme: 'light', ui: { a: 1 } });
});

test('a regular save uses no keepalive', async () => {
  const calls = [];
  const { t, writer } = harness(async (p, opts) => { calls.push(opts); });
  writer.queue({ theme: 'light' });
  t.fire();
  await settle();
  assert.deepEqual(calls, [{ keepalive: false }]);
});

test('the caller\'s patch objects are not mutated by later merges', async () => {
  const { t, writer } = harness(async () => {});
  const first = { ui: { sidebar_collapsed: true } };
  writer.queue(first);
  writer.queue({ ui: { last_task_id: 'T-3' } });
  t.fire();
  await settle();
  assert.deepEqual(first, { ui: { sidebar_collapsed: true } });
});

test('deepMerge: objects merge key by key, arrays and scalars replace', () => {
  assert.deepEqual(deepMerge({ a: { x: 1, y: 2 }, list: [1, 2] }, { a: { y: 3, z: 4 }, list: [9] }), { a: { x: 1, y: 3, z: 4 }, list: [9] });
  assert.deepEqual(deepMerge({ a: 1 }, { a: { b: 2 } }), { a: { b: 2 } });
  assert.deepEqual(deepMerge({ a: { b: 2 } }, { a: null }), { a: null });
});
