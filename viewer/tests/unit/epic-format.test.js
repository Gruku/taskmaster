// plugins/taskmaster/viewer/tests/unit/epic-format.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  designBadge, componentGlyph, progressPercent, epicProgress, tasksForComponent,
} from '../../js/lib/epic-format.js';

test('designBadge — locked carries a lock flag and label', () => {
  const b = designBadge('locked');
  assert.equal(b.locked, true);
  assert.equal(b.label, 'Locked');
  assert.equal(b.cls, 'locked');
});

test('designBadge — unknown/empty falls back to exploring', () => {
  assert.equal(designBadge('bogus').cls, 'exploring');
  assert.equal(designBadge(undefined).cls, 'exploring');
});

test('componentGlyph — per-status glyph, default for unknown', () => {
  assert.equal(componentGlyph('done'), '●');
  assert.equal(componentGlyph('in-progress'), '◐');
  assert.equal(componentGlyph('blocked'), '✗');
  assert.equal(componentGlyph('todo'), '○');
  assert.equal(componentGlyph(undefined), '○');
});

test('progressPercent — (done+archived)/total, 0 when empty', () => {
  assert.equal(progressPercent({ total: 4, done: 1, archived: 1 }), 50);
  assert.equal(progressPercent({ total: 0 }), 0);
  assert.equal(progressPercent(undefined), 0);
});

test('tasksForComponent — key match, and _unassigned = no component', () => {
  const tasks = [
    { id: 'a', component: 'core' },
    { id: 'b', component: 'ui' },
    { id: 'c' },
  ];
  assert.deepEqual(tasksForComponent(tasks, 'core').map(t => t.id), ['a']);
  assert.deepEqual(tasksForComponent(tasks, '_unassigned').map(t => t.id), ['c']);
});

test('epicProgress — closed = done + archived, label and pct agree', () => {
  const p = epicProgress({ total: 55, done: 25, archived: 10 });
  assert.deepEqual(
    { closed: p.closed, pct: p.pct, label: p.label },
    { closed: 35, pct: 64, label: '35/55 closed · 25 done · 10 archived' },
  );
});
test('epicProgress — no archived part when zero', () => {
  assert.equal(epicProgress({ total: 4, done: 1 }).label, '1/4 closed · 1 done');
});
test('epicProgress — empty and missing stats', () => {
  assert.deepEqual(epicProgress(undefined), { total: 0, done: 0, archived: 0, closed: 0, pct: 0, label: '0/0 closed' });
  assert.equal(epicProgress({ total: 0, done: 3 }).pct, 0);
});
test('epicProgress — clamps stale counters', () => {
  const p = epicProgress({ total: 3, done: 3, archived: 2 });
  assert.equal(p.closed, 3);
  assert.equal(p.pct, 100);
});
test('progressPercent delegates', () => {
  assert.equal(progressPercent({ total: 4, done: 1, archived: 1 }), 50);
});
