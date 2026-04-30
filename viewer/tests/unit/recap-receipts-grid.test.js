import test from 'node:test';
import assert from 'node:assert/strict';
import { renderReceiptsGrid } from '../../js/components/recap-receipts-grid.js';

test('renders four cards: Tasks, Files, Lessons, Issues', () => {
  const html = renderReceiptsGrid({
    tasks_added: [{ id: 'T-3', title: 'New' }],
    tasks_changed: [{ id: 'T-2', from: { status: 'in-progress' }, to: { status: 'done' } }],
    tasks_removed: [],
    files_touched: [{ path: 'a.css', plus: 12, minus: 3 }],
    lessons_fired: [{ id: 'LSN-08', name: 'Worktree rule', fires: 3, first_time: false }],
    issues_opened: [{ id: 'ISS-12', severity: 'High', title: 'crash' }],
    issues_transitioned: [],
  });
  assert.match(html, /receipts-grid/);
  assert.match(html, /Tasks/);
  assert.match(html, /Files touched/);
  assert.match(html, /Lessons fired/);
  assert.match(html, /Issues/);
  assert.match(html, /T-3/);
  assert.match(html, /a\.css/);
  assert.match(html, /LSN-08/);
  assert.match(html, /ISS-12/);
});

test('empty diff still renders four cards with empty hint', () => {
  const html = renderReceiptsGrid({
    tasks_added: [], tasks_changed: [], tasks_removed: [],
    files_touched: [], lessons_fired: [],
    issues_opened: [], issues_transitioned: [],
  });
  assert.match(html, /Tasks/);
  assert.match(html, /No changes/);
});
