// viewer/tests/unit/gate-pipeline.test.js
// Unit tests for gate-pipeline component (Spec A — surfacing gate pipeline in the viewer).
// Uses node:test — no Playwright, no JSDOM needed (pure string-returning functions).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderGatePipeline, laneBadge } from '../../js/components/gate-pipeline.js';

// ── renderGatePipeline ──────────────────────────────────────────────────────

test('express-lane renders review-gate node only (no impl node)', () => {
  const task = {
    id: 't-1',
    lane: 'express',
    gate_state: 'review-gate:pending',
    gates: { 'review-gate': { verdict: 'pass' } },
  };
  const html = renderGatePipeline(task);
  assert.match(html, /review-gate/,   'review-gate label present');
  assert.match(html, /gate--pass/,    'review-gate has gate--pass class');
  assert.doesNotMatch(html, /class="gp-gate[^"]*"[^>]*>impl</, 'impl node NOT rendered');
  // NO left-rail CSS — hard rule
  assert.doesNotMatch(html, /border-left:\s*\d+px/, 'no border-left inline style');
  assert.doesNotMatch(html, /margin-left:\s*\d+px/,  'no left-margin inline style');
});

test('express-lane with no gates shows review-gate as pending', () => {
  const task = {
    id: 't-1b',
    lane: 'express',
    gates: {},
  };
  const html = renderGatePipeline(task);
  assert.match(html, /review-gate/,   'review-gate label present');
  assert.match(html, /gate--pending/, 'review-gate has gate--pending class');
  assert.doesNotMatch(html, /\bimpl\b/, 'impl NOT rendered');
});

test('laneless task renders empty string', () => {
  assert.equal(renderGatePipeline({ id: 't', gates: {} }), '');
  assert.equal(renderGatePipeline({ id: 't' }), '');
  assert.equal(renderGatePipeline({}), '');
  assert.equal(renderGatePipeline(null), '');
});

test('full-lane renders 3 blocking gates only', () => {
  const html = renderGatePipeline({ id: 't-2', lane: 'full', gates: {} });
  const expected = ['spec-review', 'plan-review', 'review-gate'];
  for (const g of expected) {
    assert.match(html, new RegExp(g), `gate "${g}" present`);
  }
  // Status gates must NOT appear as pipeline nodes
  for (const g of ['spec', 'plan', 'tests', 'impl']) {
    // Each status gate label must not appear as a gp-gate node title/label.
    // We check it does not appear as a standalone word inside a gate span.
    assert.doesNotMatch(html, new RegExp(`gate[^>]*>${g}<`), `status gate "${g}" must not be a pipeline node`);
  }
});

test('standard-lane renders 2 blocking gates only', () => {
  const html = renderGatePipeline({ id: 't-3', lane: 'standard', gates: {} });
  const expected = ['design-review', 'review-gate'];
  for (const g of expected) {
    assert.match(html, new RegExp(g), `gate "${g}" present`);
  }
  // Status gates must NOT appear as pipeline nodes
  for (const g of ['spec', 'tests', 'impl']) {
    assert.doesNotMatch(html, new RegExp(`gate[^>]*>${g}<`), `status gate "${g}" must not be a pipeline node`);
  }
});

test('skipped blocking gate gets gate--skipped class', () => {
  const task = {
    id: 't-4',
    lane: 'standard',
    gates: { 'design-review': { skipped: true } },
  };
  const html = renderGatePipeline(task);
  assert.match(html, /gate--skipped/);
});

test('verdict-bearing review-gate reflects verdict class', () => {
  const task = {
    id: 't-5',
    lane: 'express',
    gates: { 'review-gate': { verdict: 'fail' } },
  };
  const html = renderGatePipeline(task);
  assert.match(html, /gate--fail/);
});

test('gate--pass for pass verdict', () => {
  const task = {
    id: 't-6',
    lane: 'express',
    gates: { 'review-gate': { verdict: 'pass' } },
  };
  const html = renderGatePipeline(task);
  assert.match(html, /gate--pass/);
});

test('gate--warn for warn verdict', () => {
  const task = {
    id: 't-7',
    lane: 'express',
    gates: { 'review-gate': { verdict: 'warn' } },
  };
  const html = renderGatePipeline(task);
  assert.match(html, /gate--warn/);
});

test('gate_state one-liner renders in the output', () => {
  const task = {
    id: 't-8',
    lane: 'full',
    gate_state: 'review-gate:pending',
    gates: {},
  };
  const html = renderGatePipeline(task);
  assert.match(html, /review-gate:pending/, 'gate_state text is present');
});

test('no gate_state omits the one-liner element', () => {
  const task = { id: 't-9', lane: 'express', gates: { 'review-gate': { verdict: 'pass' } } };
  const html = renderGatePipeline(task);
  assert.doesNotMatch(html, /gp-state/, 'no gate_state => no .gp-state element');
});

// ── laneBadge ───────────────────────────────────────────────────────────────

test('laneBadge reflects lane name', () => {
  assert.match(laneBadge({ lane: 'full' }),     /full/);
  assert.match(laneBadge({ lane: 'standard' }), /standard/);
  assert.match(laneBadge({ lane: 'express' }),  /express/);
});

test('laneBadge returns empty string when no lane', () => {
  assert.equal(laneBadge({}),    '');
  assert.equal(laneBadge(null),  '');
  assert.equal(laneBadge(),      '');
});

test('laneBadge carries lane-badge class', () => {
  const html = laneBadge({ lane: 'express' });
  assert.match(html, /lane-badge/);
});
