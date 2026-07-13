import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
const { window } = new JSDOM('<!DOCTYPE html><body></body>');
globalThis.document = window.document;

import { renderCard } from '../../js/components/card.js';
import { STATUS_LABELS } from '../../js/lib/filters.js';

test('in-review card renders its human_action', () => {
  const el = renderCard({ task: { id: 't-1', title: 'X', status: 'in-review', human_action: 'add API key to .env' } });
  assert.match(el.outerHTML, /card-human-action/);
  assert.match(el.outerHTML, /add API key to \.env/);
});

test('in-review card without human_action renders no line', () => {
  const el = renderCard({ task: { id: 't-2', title: 'Y', status: 'in-review' } });
  assert.doesNotMatch(el.outerHTML, /card-human-action/);
});

test('non-in-review card ignores a stale human_action', () => {
  const el = renderCard({ task: { id: 't-3', title: 'Z', status: 'done', human_action: 'leftover' } });
  assert.doesNotMatch(el.outerHTML, /card-human-action/);
});

test('in-review status label reads Waiting on human', () => {
  assert.equal(STATUS_LABELS['in-review'], 'Waiting on human');
});
