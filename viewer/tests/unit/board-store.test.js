import test from 'node:test';
import assert from 'node:assert/strict';
import { store } from '../../js/store.js';
import { api } from '../../js/api.js';

test('optional GET retains its empty fallback for conflict and validation errors', async () => {
  const original = globalThis.fetch;
  try {
    for (const status of [404, 409, 422, 500]) {
      globalThis.fetch = async () => new Response('{}', {status, headers: {'Content-Type': 'application/json'}});
      assert.equal(await api.getLastSession(), null);
    }
  } finally { globalThis.fetch = original; }
});

test('304 is never parsed and a repeated revision never emits', async () => {
  let emitted = 0;
  const unsubscribe = store.subscribe('backlog', () => emitted++);
  store.setBoard({revision: 'one', cursor: 'one', tasks: [], epics: [], phases: []});
  store.setBoard({revision: 'one', cursor: 'one', tasks: [], epics: [], phases: []});
  const before = emitted;
  const original = globalThis.fetch;
  globalThis.fetch = async (path, init) => {
    assert.match(path, /since=one/);
    assert.equal(init.headers['If-None-Match'], '"one"');
    return {status: 304, headers: new Headers(), json() { throw Error('304 parsed'); }};
  };
  try { await store.refreshBoard(api); assert.equal(emitted, before); }
  finally { globalThis.fetch = original; unsubscribe(); }
});
