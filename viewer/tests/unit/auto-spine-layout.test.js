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
