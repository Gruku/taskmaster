import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const { window } = new JSDOM('<!DOCTYPE html><div id="root"></div>');
globalThis.document = window.document;

import { createViewSwitcher } from '../../js/components/continuity/view-switcher.js';

test('view-switcher renders three buttons and emits select events', () => {
  const events = [];
  const sw = createViewSwitcher({
    active: 'action',
    onSelect: (v) => events.push(v),
  });
  document.body.appendChild(sw.root);
  const btns = sw.root.querySelectorAll('button');
  assert.equal(btns.length, 3);
  btns[1].click();   // time
  btns[2].click();   // entity
  assert.deepEqual(events, ['time', 'entity']);
});

test('view-switcher reflects active prop', () => {
  const sw = createViewSwitcher({ active: 'entity', onSelect: () => {} });
  const active = sw.root.querySelector('button.is-active');
  assert.equal(active?.textContent.toLowerCase(), 'entity');
});
