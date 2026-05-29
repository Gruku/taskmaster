// plugins/taskmaster/viewer/tests/unit/view-mode.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import { detailViewMode, parseDetailHref, shouldInterceptDetailLink } from '../../js/lib/view-mode.js';

test('detailViewMode — default modal, explicit full, malformed→modal', () => {
  assert.equal(detailViewMode(undefined), 'modal');
  assert.equal(detailViewMode({}), 'modal');
  assert.equal(detailViewMode({ ui: { detail_view_mode: 'full' } }), 'full');
  assert.equal(detailViewMode({ ui: { detail_view_mode: 'nonsense' } }), 'modal');
});

test('parseDetailHref — extracts {kind,id} for task/epic, null otherwise', () => {
  assert.deepEqual(parseDetailHref('#/task/T-1'), { kind: 'task', id: 'T-1' });
  assert.deepEqual(parseDetailHref('#/epic/asset-engine'), { kind: 'epic', id: 'asset-engine' });
  assert.deepEqual(parseDetailHref('#/task/T%2D1'), { kind: 'task', id: 'T-1' }); // decoded
  assert.equal(parseDetailHref('#/kanban'), null);
  assert.equal(parseDetailHref('#/task/'), null);  // no id
  assert.equal(parseDetailHref('#/task/T-1/related'), null); // sub-path, leave alone
});

test('shouldInterceptDetailLink — modal + plain left-click on a detail href only', () => {
  const base = { href: '#/task/T-1', mode: 'modal', button: 0,
                 metaKey: false, ctrlKey: false, shiftKey: false, altKey: false };
  assert.equal(shouldInterceptDetailLink(base), true);
  assert.equal(shouldInterceptDetailLink({ ...base, mode: 'full' }), false);      // full mode
  assert.equal(shouldInterceptDetailLink({ ...base, button: 1 }), false);          // middle-click
  assert.equal(shouldInterceptDetailLink({ ...base, metaKey: true }), false);      // cmd-click
  assert.equal(shouldInterceptDetailLink({ ...base, ctrlKey: true }), false);      // ctrl-click
  assert.equal(shouldInterceptDetailLink({ ...base, href: '#/lessons' }), false);  // not a detail href
});
