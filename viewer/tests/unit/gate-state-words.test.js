// User intent: the server's gate_state mirror is said in the same words wherever it shows (the detail's gate strip and
// the board's card) and the machine string itself never reaches the page.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { gateStateWords } from '../../js/components/gate-pipeline.js';

test('<gate>:<state> reads as the gate\'s label and the state\'s word', () => {
  assert.equal(gateStateWords('review-gate:pending'), 'Review gate — pending');
  assert.equal(gateStateWords('plan-review:warn'), 'Plan review — passed with warnings');
  assert.equal(gateStateWords('spec-review:pass'), 'Spec review — passed');
});

test('blocked@<gate> says where the task is stuck', () => {
  assert.equal(gateStateWords('blocked@review-gate'), 'Blocked at Review gate');
  assert.equal(gateStateWords('blocked@security-audit'), 'Blocked at Security audit');
});

test('a gate the viewer has no label for is sentence-cased from its id', () => {
  assert.equal(gateStateWords('security_audit:fail'), 'Security audit — failed');
});

test('anything that is not one of the two shapes, or an unknown state, is null', () => {
  for (const raw of ['', 'review-gate', 'review-gate:maybe', 'a:b:c', 'blocked@', 'blocked@a:b', null, undefined, 42, {}]) {
    assert.equal(gateStateWords(raw), null, String(raw));
  }
});
