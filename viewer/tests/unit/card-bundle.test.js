import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
const { window } = new JSDOM('<!DOCTYPE html><body></body>');
globalThis.document = window.document;

import { renderCard } from '../../js/components/card.js';

test('card renders a bundle tag when task.bundle is set', () => {
  const el = renderCard({ task: { id: 't-1', title: 'X', status: 'todo', bundle: 'asset-ux' } });
  assert.equal(el.querySelector('.card-bundle').textContent, 'Bundle asset-ux');
  assert.doesNotMatch(el.outerHTML, /⬢/);
});

test('card renders no bundle tag when task.bundle is absent', () => {
  const el = renderCard({ task: { id: 't-2', title: 'Y', status: 'todo' } });
  assert.equal(el.querySelector('.card-bundle'), null);
});

test('card hides bundle tag when hideBundleChip:true', () => {
  const el = renderCard({ task: { id: 't-3', title: 'Z', status: 'todo', bundle: 'asset-ux' }, hideBundleChip: true });
  assert.equal(el.querySelector('.card-bundle'), null);
  assert.doesNotMatch(el.outerHTML, /asset-ux/);
});
