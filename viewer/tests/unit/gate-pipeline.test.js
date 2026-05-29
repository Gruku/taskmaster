// viewer/tests/unit/gate-pipeline.test.js
// Unit tests for gate-pipeline component (Spec A — surfacing gate pipeline in the viewer).
// Uses node:test — no Playwright, no JSDOM needed (pure string-returning functions).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderGatePipeline, laneBadge } from '../../js/components/gate-pipeline.js';

// ── renderGatePipeline ──────────────────────────────────────────────────────

test('renders a node per required gate with correct status classes', () => {
  const task = {
    id: 't-1',
    lane: 'express',
    gate_state: 'review-gate:pending',
    gates: { impl: { status: 'done' } },
  };
  const html = renderGatePipeline(task);
  assert.match(html, /impl/,          'impl gate label present');
  assert.match(html, /review-gate/,   'review-gate label present');
  assert.match(html, /gate--done/,    'impl gate has gate--done class');
  assert.match(html, /gate--pending/, 'review-gate has gate--pending class');
  // NO left-rail CSS — hard rule
  assert.doesNotMatch(html, /border-left:\s*\d+px/, 'no border-left inline style');
  assert.doesNotMatch(html, /margin-left:\s*\d+px/,  'no left-margin inline style');
});

test('laneless task renders empty string', () => {
  assert.equal(renderGatePipeline({ id: 't', gates: {} }), '');
  assert.equal(renderGatePipeline({ id: 't' }), '');
  assert.equal(renderGatePipeline({}), '');
  assert.equal(renderGatePipeline(null), '');
});

test('full-lane renders all 7 gates', () => {
  const html = renderGatePipeline({ id: 't-2', lane: 'full', gates: {} });
  const expected = ['spec', 'spec-review', 'plan', 'plan-review', 'tests', 'impl', 'review-gate'];
  for (const g of expected) {
    assert.match(html, new RegExp(g), `gate "${g}" present`);
  }
});

test('standard-lane renders 5 gates', () => {
  const html = renderGatePipeline({ id: 't-3', lane: 'standard', gates: {} });
  const expected = ['spec', 'design-review', 'tests', 'impl', 'review-gate'];
  for (const g of expected) {
    assert.match(html, new RegExp(g), `gate "${g}" present`);
  }
});

test('skipped gate gets gate--skipped class', () => {
  const task = {
    id: 't-4',
    lane: 'standard',
    gates: { spec: { skipped: true } },
  };
  const html = renderGatePipeline(task);
  // spec gate node should carry gate--skipped
  assert.match(html, /gate--skipped/);
});

test('verdict-bearing gate reflects verdict class', () => {
  const task = {
    id: 't-5',
    lane: 'express',
    gates: { impl: { status: 'done', verdict: 'fail' } },
  };
  const html = renderGatePipeline(task);
  // verdict overrides status
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
    gates: { impl: { verdict: 'warn' } },
  };
  const html = renderGatePipeline(task);
  assert.match(html, /gate--warn/);
});

test('gate_state one-liner renders in the output', () => {
  const task = {
    id: 't-8',
    lane: 'full',
    gate_state: 'impl:pending',
    gates: {},
  };
  const html = renderGatePipeline(task);
  assert.match(html, /impl:pending/, 'gate_state text is present');
});

test('no gate_state omits the one-liner element', () => {
  const task = { id: 't-9', lane: 'express', gates: { impl: { status: 'done' } } };
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
