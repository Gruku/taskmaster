import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
const { window } = new JSDOM('<!DOCTYPE html><body></body>');
globalThis.document = window.document;

import { renderCard } from '../../js/components/card.js';

test('card renders a bundle badge when task.bundle is set', () => {
  const el = renderCard({ task: { id: 't-1', title: 'X', status: 'todo', bundle: 'asset-ux' } });
  assert.match(el.outerHTML, /⬢.*asset-ux/);
  assert.match(el.outerHTML, /tm-bundle/);
});

test('card renders no bundle badge when task.bundle is absent', () => {
  const el = renderCard({ task: { id: 't-2', title: 'Y', status: 'todo' } });
  assert.doesNotMatch(el.outerHTML, /tm-bundle/);
});
