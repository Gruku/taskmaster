// User intent: pin that the issue card shows the evidence text the API sends, not the legacy symptom field.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
const { window } = new JSDOM('<!DOCTYPE html><body></body>');
globalThis.document = window.document;

import { issueCard } from '../../js/components/issue-card.js';

test('issue card shows evidence when only evidence is set', () => {
  const el = issueCard({ id: 'ISS-1', title: 'T', severity: 'P1', status: 'open', evidence: 'stack trace here' });
  assert.match(el.outerHTML, /stack trace here/);
});
test('issue card still shows legacy symptom', () => {
  const el = issueCard({ id: 'ISS-2', title: 'T', severity: 'P1', status: 'open', symptom: 'old symptom' });
  assert.match(el.outerHTML, /old symptom/);
});
test('issue card shows evidence exactly once', () => {
  const el = issueCard({ id: 'ISS-3', title: 'T', severity: 'P1', status: 'open', evidence: 'unique-evidence-text' });
  assert.equal(el.outerHTML.split('unique-evidence-text').length - 1, 1);
});
