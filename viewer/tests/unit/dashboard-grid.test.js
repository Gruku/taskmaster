import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  computePlacements,
  addWidget,
  removeWidget,
  moveWidget,
} from '../../js/components/dashboard-grid.js';

test('computePlacements groups widgets by rail and orders by index', () => {
  const layout = [
    { id: 'a', type: 'suggested-next',    size: 'medium', rail: 'left',   index: 0 },
    { id: 'b', type: 'phase-deliverables',size: 'medium', rail: 'left',   index: 1 },
    { id: 'c', type: 'open-issues',       size: 'medium', rail: 'right',  index: 0 },
    { id: 'd', type: 'recent-commits',    size: 'small',  rail: 'bottom', index: 0 },
  ];
  const out = computePlacements(layout);
  assert.equal(out.length, 4);
  const left = out.filter(p => p.instance.rail === 'left').map(p => p.instance.id);
  assert.deepEqual(left, ['a', 'b']);
  const right = out.filter(p => p.instance.rail === 'right').map(p => p.instance.id);
  assert.deepEqual(right, ['c']);
  const bottom = out.filter(p => p.instance.rail === 'bottom').map(p => p.instance.id);
  assert.deepEqual(bottom, ['d']);
});

test('computePlacements assigns deterministic order for missing index', () => {
  const layout = [
    { id: 'a', type: 'x', size: 'medium', rail: 'left' },
    { id: 'b', type: 'y', size: 'medium', rail: 'left' },
  ];
  const out = computePlacements(layout);
  assert.deepEqual(out.map(p => p.instance.id), ['a', 'b']);
});

test('addWidget appends to end of target rail and assigns new index', () => {
  const layout = [
    { id: 'a', type: 'suggested-next', size: 'medium', rail: 'left', index: 0 },
  ];
  const out = addWidget(layout, 'open-issues', { rail: 'left', id: 'b' });
  assert.equal(out.length, 2);
  assert.equal(out[1].id, 'b');
  assert.equal(out[1].rail, 'left');
  assert.equal(out[1].index, 1);
});

test('removeWidget drops the instance and re-indexes survivors', () => {
  const layout = [
    { id: 'a', type: 'x', size: 'medium', rail: 'left', index: 0 },
    { id: 'b', type: 'y', size: 'medium', rail: 'left', index: 1 },
    { id: 'c', type: 'z', size: 'medium', rail: 'left', index: 2 },
  ];
  const out = removeWidget(layout, 'b');
  assert.deepEqual(out.map(i => i.id), ['a', 'c']);
  assert.deepEqual(out.map(i => i.index), [0, 1]);
});

test('moveWidget across rails reorders both rails consistently', () => {
  const layout = [
    { id: 'a', type: 'x', size: 'medium', rail: 'left',  index: 0 },
    { id: 'b', type: 'y', size: 'medium', rail: 'left',  index: 1 },
    { id: 'c', type: 'z', size: 'medium', rail: 'right', index: 0 },
  ];
  const out = moveWidget(layout, 'a', { rail: 'right', index: 0 });
  const right = out.filter(i => i.rail === 'right').sort((p, q) => p.index - q.index).map(i => i.id);
  assert.deepEqual(right, ['a', 'c']);
  const left = out.filter(i => i.rail === 'left').sort((p, q) => p.index - q.index).map(i => i.id);
  assert.deepEqual(left, ['b']);
});

test('defaultLayout: every entry has rail, type, and unique id', async () => {
  const { defaultLayout } = await import('../../js/components/widget-catalog.js');
  const seed = defaultLayout();
  assert.ok(seed.length >= 10);
  const ids = new Set(seed.map(i => i.id));
  assert.equal(ids.size, seed.length);
  for (const inst of seed) {
    assert.ok(inst.type, `instance missing type: ${JSON.stringify(inst)}`);
    assert.ok(['left', 'right', 'bottom'].includes(inst.rail), `bad rail: ${inst.rail}`);
    assert.ok(['small', 'medium', 'wide'].includes(inst.size), `bad size: ${inst.size}`);
  }
});
