// User intent: dark is the default theme — an unknown or missing saved theme is dark; the OS is followed only when 'system' was chosen.
import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveTheme, normalizePref } from '../../js/lib/theme.js';

test('explicit choices win', () => {
  assert.equal(resolveTheme('dark', false), 'dark');
  assert.equal(resolveTheme('light', true), 'light');
});
test('known prefs are kept as they are', () => {
  for (const p of ['dark', 'light', 'system']) assert.equal(normalizePref(p), p);
});
test('system follows the OS', () => {
  assert.equal(resolveTheme('system', true), 'dark');
  assert.equal(resolveTheme('system', false), 'light');
});
for (const bad of [undefined, null, '', 'blue', 42, {}]) {
  test(`unknown pref ${JSON.stringify(bad)} is dark, whatever the OS says`, () => {
    assert.equal(normalizePref(bad), 'dark');
    assert.equal(resolveTheme(bad, true), 'dark');
    assert.equal(resolveTheme(bad, false), 'dark');
  });
}
