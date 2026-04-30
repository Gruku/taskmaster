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
