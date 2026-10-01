// User intent: a saved preference (the theme above all) must never be dropped because another preference changed a moment later.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createPrefsWriter, deepMerge } from '../../js/lib/prefs-writer.js';

// One pending timer at a time, fired by hand.
function fakeTimers() {
  let next = 1;
  const timers = new Map();
  return {
    set: (fn) => { const id = next++; timers.set(id, fn); return id; },
    clear: (id) => { timers.delete(id); },
    pending: () => timers.size,
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
  const writer = createPrefsWriter({ save, delayMs: 400, setTimer: t.set, clearTimer: t.clear, onError: (e) => errors.push(e) });
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

test('a failed save is reported and does not take a later patch with it', async () => {
  const saved = [];
  let fail = true;
  const { t, writer, errors } = harness(async (p) => {
    saved.push(p);
    if (fail) { fail = false; throw new Error('PUT /api/viewer/prefs → 500'); }
  });
  writer.queue({ theme: 'light' });
  t.fire();
  writer.queue({ ui: { sidebar_collapsed: true } });
  await settle();
  assert.equal(errors.length, 1);
  t.fire();
  await settle();
  assert.deepEqual(saved, [{ theme: 'light' }, { ui: { sidebar_collapsed: true } }]);
  assert.equal(errors.length, 1);
});

test('a save that throws synchronously is reported the same way', async () => {
  let calls = 0;
  const { t, writer, errors } = harness(() => { calls++; if (calls === 1) throw new Error('boom'); return Promise.resolve(); });
  writer.queue({ theme: 'light' });
  t.fire();
  await settle();
  assert.equal(errors.length, 1);
  writer.queue({ theme: 'dark' });
  t.fire();
  await settle();
  assert.equal(calls, 2);
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
