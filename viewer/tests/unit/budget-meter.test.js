// User intent: the budget bar shows a label that is text (never markup) and sets its fill width through CSSOM, so it renders under a strict CSP and cannot inject through the label.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;

const { buildBudgetMeter } = await import('../../js/components/budget-meter.js');

test('a hostile label renders as text', () => {
  const el = buildBudgetMeter({ label: '<img src=x onerror=window.__pwned=1>', used: 5, limit: 10, pct: 0.5, tier: 'ok' });
  assert.equal(el.querySelector('img'), null);
  assert.equal(el.querySelector('.bmeter-label').textContent, '<img src=x onerror=window.__pwned=1>');
  assert.equal(dom.window.__pwned, undefined);
});

test('the fill width is set through style.width, and the source has no style attribute', () => {
  const el = buildBudgetMeter({ label: 'Tokens', used: 1500, limit: 3000, pct: 0.5, tier: 'warn' });
  assert.equal(el.querySelector('.bmeter-fill').style.width, '50%');
  assert.ok(el.matches('div.bmeter.bmeter--warn'));
  assert.equal(el.querySelector('.bmeter-vals').textContent, '1.5k / 3.0k');
  const source = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../../js/components/budget-meter.js'), 'utf8');
  assert.doesNotMatch(source, /style\s*=|innerHTML/);
});
