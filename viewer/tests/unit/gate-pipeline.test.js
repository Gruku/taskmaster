// viewer/tests/unit/gate-pipeline.test.js
// Unit tests for gate-pipeline component (Spec A — surfacing gate pipeline in the viewer).
// Uses node:test; JSDOM parses the returned HTML string where the test reads what a person sees.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { renderGatePipeline, laneBadge } from '../../js/components/gate-pipeline.js';

const { document } = new JSDOM('<!doctype html><body></body>').window;
const parse = (html) => { const div = document.createElement('div'); div.innerHTML = html; return div; };
const texts = (root, sel) => [...root.querySelectorAll(sel)].map((el) => el.textContent);

// ── renderGatePipeline ──────────────────────────────────────────────────────

test('express-lane renders review-gate node only (no impl node)', () => {
  const task = {
    id: 't-1',
    lane: 'express',
    gate_state: 'review-gate:pending',
    gates: { 'review-gate': { verdict: 'pass' } },
  };
  const html = renderGatePipeline(task);
  assert.match(html, /Review gate/,   'review-gate label present');
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
  assert.match(html, /Review gate/,   'review-gate label present');
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
  const expected = ['Spec review', 'Plan review', 'Review gate'];
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
  const expected = ['Design review', 'Review gate'];
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

test('each gate says its name and its state in words; gate_state on the track adds no line', () => {
  const root = parse(renderGatePipeline({
    lane: 'full',
    gates: { 'spec-review': { verdict: 'pass' }, 'plan-review': { verdict: 'warn' } },
    gate_state: 'review-gate:pending',
  }));
  assert.equal(root.querySelectorAll('.gp-gate').length, 3);
  assert.deepEqual(texts(root, '.gp-gate .marker__word'), ['Spec review', 'Plan review', 'Review gate']);
  assert.deepEqual(texts(root, '.gp-gate .gp-gate__state'), ['passed', 'passed with warnings', 'pending']);
  assert.deepEqual([...root.querySelectorAll('.gp-gate')].map((el) => el.getAttribute('title')),
    ['Spec review: passed', 'Plan review: passed with warnings', 'Review gate: pending']);
  assert.equal(root.querySelector('.gp-state'), null, 'the node already says where the task stands');
  assert.equal(root.querySelector('.gp-word'), null);
  assert.ok(!root.textContent.includes('review-gate:pending'), 'the raw gate_state never reaches the page');
  assert.ok(!root.textContent.includes('review-gate'), 'no gate is named by its machine id');
});

test('every state has its word, and an unknown gate name is sentence-cased', () => {
  const words = {
    done: { status: 'done' }, passed: { verdict: 'pass' }, 'passed with warnings': { verdict: 'warn' },
    failed: { verdict: 'fail' }, skipped: { skipped: true }, pending: undefined,
  };
  for (const [word, record] of Object.entries(words)) {
    const root = parse(renderGatePipeline({ lane: 'express', gates: { 'review-gate': record } }));
    assert.equal(root.querySelector('.gp-gate__state').textContent, word);
  }
  const root = parse(renderGatePipeline({ lane: 'express', gates: {}, gate_state: 'security-audit:fail' }));
  assert.equal(root.querySelector('.gp-state').textContent, 'Current step: Security audit — failed');
  const proto = parse(renderGatePipeline({ lane: 'express', gates: {}, gate_state: 'constructor:fail' }));
  assert.equal(proto.querySelector('.gp-state').textContent, 'Current step: Constructor — failed');
});

test('gate_state for a gate off the lane\'s track reads as the current step in words', () => {
  const root = parse(renderGatePipeline({ lane: 'express', gates: {}, gate_state: 'spec-review:fail' }));
  assert.equal(root.querySelector('.gp-state').textContent, 'Current step: Spec review — failed');
  assert.ok(!root.textContent.includes('spec-review:fail'));
});

test('a gate_state not of the gate:state shape is not printed', () => {
  for (const gate_state of ['weird', 'blocked@review-gate', 'spec-review:', ':pass', 'spec-review:sideways', 'spec-review:constructor', 'a:b:c']) {
    const root = parse(renderGatePipeline({ lane: 'express', gates: {}, gate_state }));
    assert.equal(root.querySelector('.gp-state'), null, gate_state);
    assert.ok(!root.textContent.includes(gate_state), gate_state);
  }
});

test('a gate name in gate_state is text, never markup', () => {
  const root = parse(renderGatePipeline({ lane: 'express', gates: {}, gate_state: '<b>x</b>:fail' }));
  assert.equal(root.querySelector('b'), null);
  assert.equal(root.querySelector('.gp-state').textContent, 'Current step: <b>x</b> — failed');
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
