// User intent: pin that the issue card shows the evidence text the API sends, not the legacy symptom field.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
const { window } = new JSDOM('<!DOCTYPE html><body></body>');
globalThis.document = window.document;

import { issueCard } from '../../js/components/issue-card.js';

const evidenceOf = (el) => el.querySelector('.issue-card__evidence')?.textContent;

test('issue card shows evidence when only evidence is set', () => {
  const el = issueCard({ id: 'ISS-1', title: 'T', severity: 'P1', status: 'open', evidence: 'stack trace here' });
  assert.equal(evidenceOf(el), 'stack trace here');
});
test('issue card still shows legacy symptom', () => {
  const el = issueCard({ id: 'ISS-2', title: 'T', severity: 'P1', status: 'open', symptom: 'old symptom' });
  assert.equal(evidenceOf(el), 'old symptom');
});
test('issue card shows evidence exactly once', () => {
  const el = issueCard({ id: 'ISS-3', title: 'T', severity: 'P1', status: 'open', evidence: 'unique-evidence-text', symptom: 'legacy-symptom-text' });
  assert.equal(el.querySelectorAll('.issue-card__evidence').length, 1);
  assert.equal(evidenceOf(el), 'unique-evidence-text');
  assert.ok(!el.textContent.includes('legacy-symptom-text'), 'the symptom is not shown beside the evidence');
});
