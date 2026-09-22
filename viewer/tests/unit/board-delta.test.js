import test from 'node:test';
import assert from 'node:assert/strict';
import { applyBoardDelta } from '../../js/lib/board-delta.js';
import { activeEpic } from '../../js/lib/epics.js';

test('deltas replace rows, remove idempotently and preserve full-board order', () => {
  let full = {revision: '0', cursor: '0', epics: [{id: 'b'}, {id: 'a'}], phases: [], tasks: []};
  const rows = new Map();
  for (let n = 1; n <= 100; n++) {
    const task = {id: `t${n % 11}`, epic: n % 2 ? 'a' : 'b', order: n % 3, title: `v${n}`};
    const removed = `t${(n + 4) % 11}`;
    rows.set(task.id, task); rows.delete(removed);
    full = applyBoardDelta(full, {since: String(n - 1), revision: String(n), cursor: String(n), tasks_upsert: [task], tasks_remove: [removed]});
    const expected = [...rows.values()].sort((a, b) => (a.epic === 'b' ? 0 : 1) - (b.epic === 'b' ? 0 : 1) || a.order - b.order || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
    assert.deepEqual(full.tasks, expected);
  }
  assert.throws(() => applyBoardDelta(full, {since: 'old', revision: 'new', tasks_upsert: [], tasks_remove: []}));
});

test('active epic matches task-count, alphabetical and declaration fallbacks', () => {
  const epics = [{id: 'z', status: 'active'}, {id: 'a', status: 'active'}];
  assert.equal(activeEpic({epics, tasks: []}), 'a');
  assert.equal(activeEpic({epics, tasks: [{epic: 'z', status: 'in-review'}]}), 'z');
  assert.equal(activeEpic({epics: [{id: 'z'}, {id: 'a'}], tasks: []}), 'z');
});
