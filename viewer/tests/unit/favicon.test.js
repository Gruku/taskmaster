// User intent: the project ships a real multi-size .ico — pin its structure so a broken regeneration cannot ship unnoticed.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const ICO = join(here, '../../vendor/favicon.ico');
const PNG_SIG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const SIZES = [16, 32, 48, 256];

function entries(buf) {
  const out = [];
  for (let i = 0; i < buf.readUInt16LE(4); i++) {
    const at = 6 + i * 16;
    out.push({
      width: buf.readUInt8(at), height: buf.readUInt8(at + 1),
      planes: buf.readUInt16LE(at + 4), bpp: buf.readUInt16LE(at + 6),
      length: buf.readUInt32LE(at + 8), offset: buf.readUInt32LE(at + 12),
    });
  }
  return out;
}

test('favicon.ico is an icon file with four images', () => {
  const buf = readFileSync(ICO);
  assert.equal(buf.readUInt16LE(0), 0, 'reserved');
  assert.equal(buf.readUInt16LE(2), 1, 'type 1 = icon');
  assert.equal(buf.readUInt16LE(4), SIZES.length);
});

test('entries are 16, 32, 48 and 256 px, with 256 stored as 0', () => {
  const list = entries(readFileSync(ICO));
  assert.deepEqual(list.map((e) => e.width), [16, 32, 48, 0]);
  assert.deepEqual(list.map((e) => e.height), [16, 32, 48, 0]);
  for (const e of list) assert.equal(e.bpp, 32);
});

test('each entry points at a PNG inside the file whose IHDR matches the entry', () => {
  const buf = readFileSync(ICO);
  const list = entries(buf);
  let expected = 6 + 16 * list.length;   // payloads follow the directory back to back
  list.forEach((e, i) => {
    assert.equal(e.offset, expected, `entry ${i} offset`);
    assert.ok(e.length > PNG_SIG.length + 25, `entry ${i} length`);
    assert.ok(e.offset + e.length <= buf.length, `entry ${i} ends inside the file`);
    expected = e.offset + e.length;
    const png = buf.subarray(e.offset, e.offset + e.length);
    assert.deepEqual(png.subarray(0, 8), PNG_SIG, `entry ${i} PNG signature`);
    assert.equal(png.toString('latin1', 12, 16), 'IHDR');
    assert.equal(png.readUInt32BE(16), SIZES[i], `entry ${i} IHDR width`);
    assert.equal(png.readUInt32BE(20), SIZES[i], `entry ${i} IHDR height`);
    assert.equal(png.readUInt8(25), 6, `entry ${i} is RGBA`);
  });
  assert.equal(expected, buf.length, 'no trailing bytes');
});
