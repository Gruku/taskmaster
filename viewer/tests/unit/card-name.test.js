// User intent: a board card is a keyboard stop that opens the task, so a screen reader must hear which task it is —
// its id and title — without the card pretending to be a button around its own controls.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
const { window } = new JSDOM('<!DOCTYPE html><body></body>');
globalThis.document = window.document;

import { renderCard } from '../../js/components/card.js';

const nameOf = (card) => card.getAttribute('aria-labelledby').split(' ')
  .map((id) => card.querySelector(`[id="${id}"]`)?.textContent).join(' ');

test('a card is focusable and named by its id and title, from its own elements', () => {
  const card = renderCard({ task: { id: 'T-102', title: 'Re-skin the board', status: 'todo' } });
  assert.equal(card.tabIndex, 0);
  assert.equal(card.getAttribute('role'), 'article');
  assert.equal(nameOf(card), 'T-102 Re-skin the board');
});

test('two cards never share the ids their names point at', () => {
  const a = renderCard({ task: { id: 'T-1', title: 'A', status: 'todo' } });
  const b = renderCard({ task: { id: 'T-1', title: 'A', status: 'todo' }, density: 'minimal' });
  assert.notEqual(a.getAttribute('aria-labelledby'), b.getAttribute('aria-labelledby'));
  assert.equal(nameOf(b), 'T-1 A');
});
