import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const { window } = new JSDOM('<!DOCTYPE html>');
globalThis.document = window.document;

import { createResumeRail } from '../../js/components/continuity/resume-rail.js';

const ITEMS = [
  { id: 'h1', type: 'handover', title: 'open todo',  status: 'todo',        action_class: 'resume', timestamp: '2026-05-17T10:00:00Z' },
  { id: 'h2', type: 'handover', title: 'open inprog',status: 'in-progress', action_class: 'resume', timestamp: '2026-05-16T10:00:00Z' },
  { id: 'h3', type: 'handover', title: 'old done',   status: 'done',        action_class: 'resume', timestamp: '2026-05-15T10:00:00Z' },
  { id: 'h4', type: 'handover', title: 'older done', status: 'done',        action_class: 'resume', timestamp: '2026-05-14T10:00:00Z' },
  { id: 't1', type: 'task',     title: 'task work',                         action_class: 'resume', timestamp: '2026-05-17T08:00:00Z' },
];

function textOf(el) { return el?.textContent ?? ''; }

test('resume-rail splits handovers into Open and Recent sub-sections', () => {
  const rail = createResumeRail({ items: ITEMS, onItemClick: () => {} });
  document.body.appendChild(rail.root);
  const subs = rail.root.querySelectorAll('.co-resume__sub');
  assert.equal(subs.length, 2);

  const openLabel   = subs[0].querySelector('.co-resume__sub-label');
  const recentLabel = subs[1].querySelector('.co-resume__sub-label');
  assert.equal(textOf(openLabel),   'Open');
  assert.equal(textOf(recentLabel), 'Recent');

  const openRows   = subs[0].querySelectorAll('.co-row');
  const recentRows = subs[1].querySelectorAll('.co-row');
  // Open: 2 handovers (todo, in-progress) + 1 task.
  assert.equal(openRows.length, 3);
  // Recent: 2 done handovers.
  assert.equal(recentRows.length, 2);

  // Recent rows use the compact variant.
  for (const r of recentRows) {
    assert.ok(r.classList.contains('co-row--compact'),
      'recent row should be compact');
  }
  // Open rows do not.
  for (const r of openRows) {
    assert.ok(!r.classList.contains('co-row--compact'),
      'open row should not be compact');
  }
});

test('resume-rail renders count in head', () => {
  const rail = createResumeRail({ items: ITEMS, onItemClick: () => {} });
  const count = rail.root.querySelector('.co-spine__count');
  assert.equal(textOf(count), String(ITEMS.length));
});

test('resume-rail returns null root when empty + empty:true', () => {
  const rail = createResumeRail({ items: [], empty: true });
  assert.equal(rail.root, null);
});

test('resume-rail still renders head when not in empty mode', () => {
  const rail = createResumeRail({ items: [], empty: false });
  assert.ok(rail.root);
  const empty = rail.root.querySelector('.co-spine__empty');
  assert.ok(empty);
});

test('resume-rail omits Recent sub-section when no done handovers', () => {
  const openOnly = ITEMS.filter(i => i.status !== 'done' || i.type !== 'handover');
  const rail = createResumeRail({ items: openOnly, onItemClick: () => {} });
  const subs = rail.root.querySelectorAll('.co-resume__sub');
  assert.equal(subs.length, 1);
  const label = subs[0].querySelector('.co-resume__sub-label');
  assert.equal(textOf(label), 'Open');
});
