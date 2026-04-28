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

test('deep upstream chain: L-2 connects through L-1, not directly to center', () => {
  const out = computeGraphLayout({
    center: { id: 'C', title: 'Center', status: 'in-progress' },
    upstream: [
      { id: 'U1', title: 'Up 1', status: 'done', depth: 1 },
      { id: 'U2', title: 'Up 2', status: 'done', depth: 2 },
    ],
    downstream: [],
    width: 800, height: 320,
  });
  const u2 = out.nodes.find(n => n.id === 'U2');
  const u1 = out.nodes.find(n => n.id === 'U1');
  assert.equal(u2.column, -2);
  assert.equal(u1.column, -1);
  assert.ok(u2.faded, 'L-2 nodes should be faded');
  assert.equal(u1.faded, false, 'L-1 nodes should not be faded');

  const u2Edge = out.edges.find(e => e.from === 'U2');
  assert.equal(u2Edge.to, 'U1', 'U2 should chain through U1, not directly to center');
});

test('deep downstream chain: L+2 connects through L+1', () => {
  const out = computeGraphLayout({
    center: { id: 'C', title: 'Center', status: 'in-progress' },
    upstream: [],
    downstream: [
      { id: 'D1', title: 'Dn 1', status: 'backlog', depth: 1 },
      { id: 'D2', title: 'Dn 2', status: 'backlog', depth: 2 },
    ],
    width: 800, height: 320,
  });
  const d2 = out.nodes.find(n => n.id === 'D2');
  const d1 = out.nodes.find(n => n.id === 'D1');
  assert.equal(d2.column, 2);
  assert.equal(d1.column, 1);
  assert.ok(d2.faded);

  const d2Edge = out.edges.find(e => e.from === 'D2');
  assert.equal(d2Edge.to, 'D1', 'D2 should chain through D1');
});
