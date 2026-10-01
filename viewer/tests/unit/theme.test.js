// User intent: an unknown or missing saved theme must fall back to the system theme, never to an unstyled page.
import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveTheme, normalizePref } from '../../js/lib/theme.js';

test('explicit choices win', () => {
  assert.equal(resolveTheme('dark', false), 'dark');
  assert.equal(resolveTheme('light', true), 'light');
});
test('system follows the OS', () => {
  assert.equal(resolveTheme('system', true), 'dark');
  assert.equal(resolveTheme('system', false), 'light');
});
for (const bad of [undefined, null, '', 'blue', 42, {}]) {
  test(`unknown pref ${JSON.stringify(bad)} behaves as system`, () => {
    assert.equal(normalizePref(bad), 'system');
    assert.equal(resolveTheme(bad, true), 'dark');
    assert.equal(resolveTheme(bad, false), 'light');
  });
}
