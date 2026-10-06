// User intent: once the Archived screen is left, a late search callback must not repaint it or rewrite the topbar count
// that the next screen now owns.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const { window } = new JSDOM('<!DOCTYPE html><body><div id="topbar-actions"></div><span id="topbar-count"></span></body>');
for (const k of ['window', 'document', 'Event', 'HTMLElement', 'Node', 'navigator', 'CustomEvent', 'KeyboardEvent']) {
  Object.defineProperty(globalThis, k, { value: k === 'window' ? window : window[k], configurable: true, writable: true });
}

const { mount } = await import('../../js/screens/archived.js');

const store = {
  getBacklog: () => ({ epics: [], tasks: [{ id: 'T-1', title: 'Alpha', status: 'archived' }, { id: 'T-2', title: 'Beta', status: 'archived' }] }),
  subscribe: () => () => {},
};

test('a search callback that lands after cleanup neither repaints nor touches the topbar count', async () => {
  const root = document.createElement('div');
  document.body.appendChild(root);
  const cleanup = await mount(root, { store });
  const count = document.getElementById('topbar-count');
  assert.equal(count.textContent, '2 archived tasks');
  const input = document.querySelector('#topbar-actions input');
  const page = root.querySelector('.archived-page');
  const before = page.innerHTML;

  cleanup();
  count.textContent = 'next screen';
  input.value = 'alpha';
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
  await new Promise((r) => setTimeout(r, 400)); // past the search's debounce

  assert.equal(count.textContent, 'next screen');
  assert.equal(page.innerHTML, before);
  root.remove();
});

// "Clear search" repaints directly, not through the search callback, so this pins paint()'s own guard.
test('a Clear search press that lands after cleanup does not repaint or touch the topbar count', async () => {
  const root = document.createElement('div');
  document.body.appendChild(root);
  const cleanup = await mount(root, { store });
  const input = document.querySelector('#topbar-actions input');
  input.value = 'nothing like it';
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
  await new Promise((r) => setTimeout(r, 400));
  const page = root.querySelector('.archived-page');
  const clear = [...page.querySelectorAll('button')].find((b) => b.textContent.includes('Clear search'));
  assert.ok(clear, 'the no-match state offers Clear search');
  const before = page.innerHTML;
  const count = document.getElementById('topbar-count');

  cleanup();
  count.textContent = 'next screen';
  clear.click();
  await new Promise((r) => setTimeout(r, 400));

  assert.equal(count.textContent, 'next screen');
  assert.equal(page.innerHTML, before);
  root.remove();
});
