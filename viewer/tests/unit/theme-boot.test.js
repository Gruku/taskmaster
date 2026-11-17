// User intent: the pre-paint theme script now lives in a classic file (the page carries no inline script under a strict CSP) and must pick the same theme as resolveTheme() for every saved value.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { resolveTheme } from '../../js/lib/theme.js';

const source = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../../js/theme-boot.js'), 'utf8');

function boot({ saved, systemDark, throwingStorage = false, noMatchMedia = false }) {
  const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', { runScripts: 'outside-only', url: 'http://localhost/' });
  const { window } = dom;
  if (throwingStorage) {
    Object.defineProperty(window, 'localStorage', { get() { throw new Error('SecurityError'); } });
  } else if (saved !== undefined) {
    window.localStorage.setItem('tm.theme', saved);
  }
  if (!noMatchMedia) window.matchMedia = () => ({ matches: systemDark });
  window.eval(source);
  return window.document.documentElement.dataset.theme;
}

for (const saved of ['dark', 'light', 'system', 'blue', '', undefined]) {
  for (const systemDark of [true, false]) {
    test(`saved ${JSON.stringify(saved)}, system ${systemDark ? 'dark' : 'light'}: same as resolveTheme`, () => {
      assert.equal(boot({ saved, systemDark }), resolveTheme(saved || 'dark', systemDark));
    });
  }
}

test('throwing storage falls back to dark', () => {
  assert.equal(boot({ throwingStorage: true, systemDark: false }), 'dark');
});

test('no matchMedia: system resolves to dark', () => {
  assert.equal(boot({ saved: 'system', noMatchMedia: true }), 'dark');
});

test('the file is a classic script: no import or export', () => {
  assert.doesNotMatch(source, /^\s*(import|export)\s/m);
});
