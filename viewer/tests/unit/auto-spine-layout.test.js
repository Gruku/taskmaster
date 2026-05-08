import test from 'node:test';
import assert from 'node:assert/strict';

import {
  AUTO_STAGES,
  computeSpineLayout,
  spineStageFor,
  doneStagesForCursor,
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

test('connectors start at the bottom edge of node N and end at the top edge of N+1', () => {
  const layout = computeSpineLayout({
    cursorStage: 'IMPLEMENT', completed: ['PICK'], subagents: [],
    width: 240, height: 480, padding: 40,
  });
  for (let i = 0; i < layout.connectors.length; i += 1) {
    const c = layout.connectors[i];
    const a = layout.nodes[i];
    const b = layout.nodes[i + 1];
    assert.equal(c.x1, a.x);
    assert.equal(c.y1, a.y + a.r);
    assert.equal(c.x2, b.x);
    assert.equal(c.y2, b.y - b.r);
  }
});

test('connector.fromState mirrors the upper node state (drives line color)', () => {
  const layout = computeSpineLayout({
    cursorStage: 'IMPLEMENT', completed: ['PICK'], subagents: [],
    width: 240, height: 480, padding: 40,
  });
  // Done above active → fromState 'done'; below active → 'active' for the line below cursor.
  assert.equal(layout.connectors[0].fromState, 'done');         // PICK → IMPLEMENT
  assert.equal(layout.connectors[1].fromState, 'active');       // IMPLEMENT → REVIEW
  assert.equal(layout.connectors[2].fromState, 'pending');      // REVIEW → HANDOVER_STUB
});

test('satellite bezier has horizontal in/out tangents (control points share y with anchors)', () => {
  const layout = computeSpineLayout({
    cursorStage: 'IMPLEMENT', completed: ['PICK'],
    subagents: [{ type: 'G', status: 'running' }, { type: 'E', status: 'done' }],
    width: 240, height: 480, padding: 40,
  });
  assert.equal(layout.satellites.length, 2);
  for (const sat of layout.satellites) {
    assert.equal(sat.bezier.c1y, sat.bezier.startY,
      'first control point must share y with start (horizontal tangent at active node)');
    assert.equal(sat.bezier.c2y, sat.bezier.endY,
      'second control point must share y with end (horizontal tangent at satellite)');
  }
});

test('satellites alternate sides of the spine', () => {
  const layout = computeSpineLayout({
    cursorStage: 'IMPLEMENT', completed: ['PICK'],
    subagents: [
      { type: 'G', status: 'running' },
      { type: 'E', status: 'running' },
    ],
    width: 240, height: 480, padding: 40,
  });
  const cx = 240 / 2;
  assert.ok(layout.satellites[0].node.x > cx, 'first satellite right of spine');
  assert.ok(layout.satellites[1].node.x < cx, 'second satellite left of spine');
});

test('null cursorStage produces all-pending or all-done nodes (no active)', () => {
  const layout = computeSpineLayout({
    cursorStage: null, completed: ['PICK', 'IMPLEMENT'], subagents: [],
    width: 240, height: 480, padding: 40,
  });
  const active = layout.nodes.find((n) => n.state === 'active');
  assert.equal(active, undefined);
  // Done where listed, pending elsewhere
  assert.equal(layout.nodes[0].state, 'done');
  assert.equal(layout.nodes[1].state, 'done');
  assert.equal(layout.nodes[2].state, 'pending');
});

test('spineStageFor maps state-machine stages onto the 5 spine buckets', () => {
  assert.equal(spineStageFor('PICK'),          'PICK');
  assert.equal(spineStageFor('SPEC_REVIEW'),   'PICK');
  assert.equal(spineStageFor('WRITE_TESTS'),   'PICK');
  assert.equal(spineStageFor('IMPLEMENT'),     'IMPLEMENT');
  assert.equal(spineStageFor('TEST'),          'IMPLEMENT');
  assert.equal(spineStageFor('REVIEW_GATE'),   'REVIEW');
  assert.equal(spineStageFor('HANDOVER_STUB'), 'HANDOVER_STUB');
  assert.equal(spineStageFor('END_SESSION'),   'HANDOVER_STUB');
  assert.equal(spineStageFor('COMPLETE'),      'COMPLETE');
  assert.equal(spineStageFor(null),            null);
});

test('doneStagesForCursor returns spine stages strictly before the cursor bucket', () => {
  assert.deepEqual(doneStagesForCursor('PICK'),          []);
  assert.deepEqual(doneStagesForCursor('SPEC_REVIEW'),   []); // still in PICK bucket
  assert.deepEqual(doneStagesForCursor('IMPLEMENT'),     ['PICK']);
  assert.deepEqual(doneStagesForCursor('TEST'),          ['PICK']); // collapses to IMPLEMENT
  assert.deepEqual(doneStagesForCursor('REVIEW_GATE'),   ['PICK', 'IMPLEMENT']);
  assert.deepEqual(doneStagesForCursor('HANDOVER_STUB'), ['PICK', 'IMPLEMENT', 'REVIEW']);
  assert.deepEqual(doneStagesForCursor('COMPLETE'),      ['PICK', 'IMPLEMENT', 'REVIEW', 'HANDOVER_STUB']);
  assert.deepEqual(doneStagesForCursor(null),            []);
});

test('computeSpineLayout: state-machine cursor stages light up the right spine bucket', () => {
  // cursor at WRITE_TESTS — collapses to PICK bucket; that should be the active node.
  const layout = computeSpineLayout({
    cursorStage: 'WRITE_TESTS', completed: [], subagents: [],
    width: 240, height: 480, padding: 40,
  });
  const active = layout.nodes.find((n) => n.state === 'active');
  assert.equal(active.stage, 'PICK');
  // cursor at TEST — collapses to IMPLEMENT bucket.
  const layout2 = computeSpineLayout({
    cursorStage: 'TEST', completed: ['PICK'], subagents: [],
    width: 240, height: 480, padding: 40,
  });
  const active2 = layout2.nodes.find((n) => n.state === 'active');
  assert.equal(active2.stage, 'IMPLEMENT');
});
