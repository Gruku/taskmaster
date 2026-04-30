import test from 'node:test';
import assert from 'node:assert/strict';

import {
  AUTO_STAGES,
  computeSpineLayout,
} from '../../js/components/auto-spine-layout.js';

test('AUTO_STAGES is the canonical 5-stage list', () => {
  assert.deepEqual(AUTO_STAGES, [
    'PICK', 'IMPLEMENT', 'REVIEW', 'HANDOVER_STUB', 'COMPLETE',
  ]);
});

test('computeSpineLayout returns one node per stage', () => {
  const layout = computeSpineLayout({
    cursorStage: 'IMPLEMENT',
    completed: ['PICK'],
    subagents: [],
    width: 240,
    height: 480,
    padding: 40,
  });
  assert.equal(layout.nodes.length, AUTO_STAGES.length);
  for (const node of layout.nodes) {
    assert.ok(['done', 'active', 'pending'].includes(node.state));
  }
});

test('active node radius is 18, others 10 (spec §3.15)', () => {
  const layout = computeSpineLayout({
    cursorStage: 'IMPLEMENT', completed: ['PICK'], subagents: [],
    width: 240, height: 480, padding: 40,
  });
  const active = layout.nodes.find((n) => n.state === 'active');
  assert.equal(active.r, 18);
  for (const n of layout.nodes) {
    if (n !== active) assert.equal(n.r, 10);
  }
});

test('y-positions are evenly spaced and inside padding', () => {
  const layout = computeSpineLayout({
    cursorStage: 'PICK', completed: [], subagents: [],
    width: 240, height: 480, padding: 40,
  });
  assert.equal(layout.nodes[0].y, 40);                       // first
  assert.equal(layout.nodes.at(-1).y, 480 - 40);             // last
  // monotonically increasing
  for (let i = 1; i < layout.nodes.length; i += 1) {
    assert.ok(layout.nodes[i].y > layout.nodes[i - 1].y);
  }
});
