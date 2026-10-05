// viewer/tests/unit/conflict-banner.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body><div id="conflict-banner-host"></div></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;

const { showFieldConflict } = await import('../../js/components/edit/conflict-banner.js');

test('field conflict shows local + server values', () => {
  const close = showFieldConflict({
    entityKind: 'task', entityId: 'e1-001',
    fieldKey: 'title', fieldLabel: 'Title',
    localValue: 'My version', currentValue: 'Server version',
    currentEtag: 'abc', onKeepMine: async () => {}, onUseServer: () => {},
  });
  const banner = document.querySelector('.cb-banner');
  assert.ok(banner);
  assert.match(banner.textContent, /My version/);
  assert.match(banner.textContent, /Server version/);
  close();
  assert.equal(document.querySelector('.cb-banner'), null);
});

test('Keep mine button calls onKeepMine and dismisses', async () => {
  let called = false;
  showFieldConflict({
    entityKind: 'task', entityId: 'e1-001',
    fieldKey: 'title', fieldLabel: 'Title',
    localValue: 'a', currentValue: 'b', currentEtag: 'x',
    onKeepMine: async () => { called = true; },
    onUseServer: () => {},
  });
  document.querySelector('.cb-keep-mine').click();
  await new Promise(r => setTimeout(r, 5));
  assert.equal(called, true);
  assert.equal(document.querySelector('.cb-banner'), null);
});

test('Use server button calls onUseServer and dismisses', () => {
  let called = false;
  showFieldConflict({
    entityKind: 'task', entityId: 'e1-001',
    fieldKey: 'title', fieldLabel: 'Title',
    localValue: 'a', currentValue: 'b', currentEtag: 'x',
    onKeepMine: async () => {},
    onUseServer: () => { called = true; },
  });
  document.querySelector('.cb-use-server').click();
  assert.equal(called, true);
  assert.equal(document.querySelector('.cb-banner'), null);
});

// The same emptiness the form uses: a field one side left blank and the other never set is not a difference (M-4).
test('the full banner lists only real differences: null, "", [] and {} are one emptiness, maps compare by key', async () => {
  const { showFullConflict } = await import('../../js/components/edit/conflict-banner.js');
  const close = showFullConflict({
    entityKind: 'task', entityId: 'e1-001',
    localDraft: { title: 'mine', notes: '', anchors: [], docs: { a: '1', b: '2' }, branch: null },
    currentValue: { title: 'theirs', notes: null, anchors: null, docs: { b: '2', a: '1' }, branch: {} },
    currentEtag: 'x', onResolve: async () => {}, onDismiss: () => {},
  });
  assert.deepEqual([...document.querySelectorAll('.cb-multi-row .cb-key')].map((e) => e.textContent), ['title']);
  close();
});
