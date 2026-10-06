// User intent: the Dashboard's backlog subscription must never outlive the screen — not when cleanup runs, and not when
// the router abandons a mount whose first fetches are still in flight (that mount never gets its cleanup called).
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const { window } = new JSDOM('<!DOCTYPE html><body><div id="topbar-actions"></div><span id="topbar-count"></span></body>');
for (const k of ['window', 'document', 'Event', 'HTMLElement', 'Node', 'navigator', 'CustomEvent', 'KeyboardEvent']) {
  Object.defineProperty(globalThis, k, { value: k === 'window' ? window : window[k], configurable: true, writable: true });
}
globalThis.fetch = async () => { throw new Error('no network in unit tests'); };

const { mount } = await import('../../js/screens/desk.js');

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

// A store that records every subscribe and every unsubscribe, so a leak is a count, not a guess.
function recordingStore() {
  const live = new Set();
  return {
    live,
    getIssues: () => [],
    getBacklog: () => ({ tasks: [] }),
    subscribe(topic, fn) {
      const entry = { topic, fn };
      live.add(entry);
      return () => live.delete(entry);
    },
    emit() { for (const e of [...live]) e.fn(); },
  };
}

function pendingApi() {
  const notes = deferred();
  const continuity = deferred();
  return {
    notes, continuity,
    api: { notes: () => notes.promise, get: () => continuity.promise, post: async () => ({}) },
  };
}

test('cleanup removes the backlog subscription', async () => {
  const store = recordingStore();
  const root = document.createElement('div');
  document.body.appendChild(root);
  const p = pendingApi();
  const mounting = mount(root, { store, api: p.api });
  p.notes.resolve({ notes: [] });
  p.continuity.resolve({ items: [] });
  const cleanup = await mounting;
  assert.equal(store.live.size, 1);
  await cleanup();
  assert.equal(store.live.size, 0);
  root.remove();
});

test('a mount abandoned while its first fetch is pending leaves no subscription once the fetch lands', async () => {
  const store = recordingStore();
  const root = document.createElement('div');
  document.body.appendChild(root);
  const p = pendingApi();
  const mounting = mount(root, { store, api: p.api });
  // The router leaves before the first fetch answers: the screen is gone and this mount's cleanup is never called.
  root.remove();
  p.notes.resolve({ notes: [] });
  p.continuity.resolve({ items: [] });
  await mounting;
  assert.equal(store.live.size, 0, 'the subscription outlived the abandoned mount');
});

test('an emit reaching an abandoned mount mid-fetch drops its subscription', async () => {
  const store = recordingStore();
  const root = document.createElement('div');
  document.body.appendChild(root);
  const p = pendingApi();
  const mounting = mount(root, { store, api: p.api });
  root.remove();
  store.emit();
  assert.equal(store.live.size, 0);
  p.notes.resolve({ notes: [] });
  p.continuity.resolve({ items: [] });
  await mounting;
  assert.equal(store.live.size, 0);
});
