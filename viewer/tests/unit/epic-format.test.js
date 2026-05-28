// plugins/taskmaster/viewer/tests/unit/epic-format.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  designBadge, componentGlyph, progressPercent, tasksForComponent,
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
