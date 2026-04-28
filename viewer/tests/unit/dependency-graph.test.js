import { test } from 'node:test';
import assert from 'node:assert/strict';
import { computeGraphLayout } from '../../js/components/dependency-graph.js';

test('empty graph: only the center node, columns shape correct', () => {
  const out = computeGraphLayout({
    center: { id: 'T-148', title: 'Center', status: 'in-progress' },
    upstream: [],
    downstream: [],
    width: 800,
    height: 320,
  });
  assert.equal(out.nodes.length, 1);
  assert.equal(out.nodes[0].id, 'T-148');
  assert.equal(out.nodes[0].column, 0);
  assert.equal(out.edges.length, 0);
  assert.deepEqual(out.columns.map(c => c.depth), [-2, -1, 0, 1, 2]);
  // Center column x is in the middle of the canvas.
  const col0 = out.columns.find(c => c.depth === 0);
  assert.ok(Math.abs(col0.x - 400) < 1, `center column x ~ 400, got ${col0.x}`);
});
