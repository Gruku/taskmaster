// viewer/tests/unit/merge-status.test.js
// Unit tests for merge-status component (Spec B — merge-rung ladder dots).
// Uses node:test — no Playwright, no JSDOM needed (pure string-returning functions).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderMergeLadder } from '../../js/components/merge-status.js';

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
