import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const cssPath = resolve(__dirname, '../../css/screens/auto-mode.css');
const css = readFileSync(cssPath, 'utf8');

function findRule(selector) {
  // Naive: capture text between `<selector> {` and the next closing `}`.
  const escSel = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`${escSel}\\s*{([^}]*)}`, 'm');
  const m = re.exec(css);
  if (!m) throw new Error(`selector not found in CSS: ${selector}`);
  return m[1];
}

test('stepper connector starts at right edge of circle (left: calc(50% + 10px))', () => {
  const body = findRule('.stepper-step::before');
  assert.match(body, /left:\s*calc\(\s*50%\s*\+\s*10px\s*\)/);
});

test('stepper connector ends at left edge of next circle (right: calc(-50% + 10px))', () => {
  const body = findRule('.stepper-step::before');
  assert.match(body, /right:\s*calc\(\s*-50%\s*\+\s*10px\s*\)/);
});

test('done connector is gray (not green) — base var(--border)', () => {
  const body = findRule('.stepper-step--done::before');
  assert.match(body, /background:\s*var\(--border\)/);
  assert.doesNotMatch(body, /var\(--green\)/);
});

test('active connector uses blue gradient lead-in', () => {
  const body = findRule('.stepper-step--active::before');
  assert.match(body, /linear-gradient\(\s*90deg\s*,\s*var\(--accent\)\s*,\s*var\(--border\)\s*\)/);
});
