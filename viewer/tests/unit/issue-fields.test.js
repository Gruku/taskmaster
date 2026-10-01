// User intent: pin the issue field names the API really sends, so the detail page can't silently show blanks again.
import test from 'node:test';
import assert from 'node:assert/strict';
import { issueDiscovered, issueEvidence } from '../../js/util/issue-fields.js';

test('reads API names', () => {
  const i = { discovered: '2026-05-01', evidence: 'stack trace' };
  assert.equal(issueDiscovered(i), '2026-05-01');
  assert.equal(issueEvidence(i), 'stack trace');
});
test('falls back to legacy names', () => {
  const i = { created: '2026-04-01', symptom: 'old text' };
  assert.equal(issueDiscovered(i), '2026-04-01');
  assert.equal(issueEvidence(i), 'old text');
});
test('API names win over legacy', () => {
  assert.equal(issueDiscovered({ discovered: 'a', created: 'b' }), 'a');
  assert.equal(issueEvidence({ evidence: 'a', symptom: 'b' }), 'a');
});
test('missing or null issue', () => {
  assert.equal(issueDiscovered(undefined), null);
  assert.equal(issueEvidence(null), '');
  assert.equal(issueEvidence({ evidence: null }), '');
});
