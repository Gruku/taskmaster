// User intent: the vendored fonts are OFL-licensed, and the OFL lets them ship only with their licence — every font
// file in vendor/fonts must have its family's licence text beside it.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const FONTS = join(dirname(fileURLToPath(import.meta.url)), '../../vendor/fonts');

test('every vendored font has its OFL licence beside it', () => {
  const woff2 = readdirSync(FONTS).filter((n) => n.endsWith('.woff2'));
  assert.ok(woff2.length > 0, 'no fonts found');
  for (const font of woff2) {
    const family = font.replace(/-[^-]*\.woff2$/, '');
    const licence = join(FONTS, `OFL-${family}.txt`);
    assert.ok(existsSync(licence), `${font}: OFL-${family}.txt is missing`);
    const text = readFileSync(licence, 'utf8');
    assert.match(text, /SIL OPEN FONT LICENSE Version 1\.1/, `${font}: not the OFL 1.1`);
    assert.match(text, /^Copyright/m, `${font}: no Copyright line`);
  }
});
