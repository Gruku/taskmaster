// viewer/tests/unit/merge-status.test.js
// Unit tests for merge-status component (Spec B — merge-rung ladder dots).
// Uses node:test — no Playwright, no JSDOM needed (pure string-returning functions).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderMergeLadder, renderMergeLadderCompact } from '../../js/components/merge-status.js';

const LADDER = [{ label: 'develop' }, { label: 'stage' }, { label: 'master' }];

test('renders a dot per ladder rung, filled up to reached', () => {
  const task = { merge_status: { develop: { merge_commit: 'a' }, stage: { merge_commit: 'b' } } };
  const html = renderMergeLadder(task, LADDER);
  assert.match(html, /develop/);
  assert.match(html, /stage/);
  assert.match(html, /master/);
  assert.match(html, /rung--filled/);
  assert.match(html, /rung--empty/);
  assert.doesNotMatch(html, /border-left:\s*\d+px|margin-left/);    // no left rails
  assert.doesNotMatch(html, /box-shadow|transform|translate|scale/); // no shadow/motion
});

test('no merges renders an all-empty ladder, not nothing', () => {
  const html = renderMergeLadder({ merge_status: {} }, LADDER);
  assert.match(html, /rung--empty/);
});

test('rung label outside the ladder is ignored in the ordered rendering', () => {
  const task = { merge_status: { 'branch:hotfix': {} } };
  const html = renderMergeLadder(task, LADDER);
  assert.doesNotMatch(html, /rung--filled/);
});

// ── renderMergeLadderCompact (called directly by card.js) ───────────────────
// Compact derives from the SLIM mirror task.merge_gate_state (highest reached
// ladder rung), NOT the heavy merge_status which is stripped from cards.

test('compact: dots filled up to the reached rung (merge_gate_state)', () => {
  const task = { merge_gate_state: 'stage' };
  const html = renderMergeLadderCompact(task, LADDER);
  const dotCount = (html.match(/ml-dot/g) || []).length;
  assert.equal(dotCount, LADDER.length);              // one dot per rung
  const filledCount = (html.match(/rung--filled/g) || []).length;
  assert.equal(filledCount, 2);                       // develop + stage filled
  assert.match(html, /rung--empty/);                  // master not reached
  assert.doesNotMatch(html, /border-left:\s*\d+px|margin-left/);    // no left rails
  assert.doesNotMatch(html, /box-shadow|transform|translate|scale/); // no shadow/motion
});

test('compact: terminal rung fills every dot', () => {
  const html = renderMergeLadderCompact({ merge_gate_state: 'master' }, LADDER);
  const filledCount = (html.match(/rung--filled/g) || []).length;
  assert.equal(filledCount, LADDER.length);           // all filled
  assert.doesNotMatch(html, /rung--empty/);
});

test('compact: no reached rung renders nothing (glance surface)', () => {
  assert.equal(renderMergeLadderCompact({ merge_gate_state: '' }, LADDER), '');
  assert.equal(renderMergeLadderCompact({}, LADDER), '');
  assert.equal(renderMergeLadderCompact(null, LADDER), '');
});

test('compact: merge_gate_state outside the ladder renders nothing', () => {
  const html = renderMergeLadderCompact({ merge_gate_state: 'branch:hotfix' }, LADDER);
  assert.equal(html, '');
});
