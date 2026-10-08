// User intent: build the project's real multi-size favicon.ico from the approved Board artwork by machine, so the icon is never hand-exported.
// Usage: node viewer/tools/gen-favicon.mjs   (rewrites viewer/vendor/favicon.ico in place from vendor/icon.svg, icon-16.svg and icon-32.svg)
//        node viewer/tools/gen-favicon.mjs --png <dir>   also writes each size as <dir>/favicon-<size>.png for inspection
// Needs `npm --prefix viewer install` first: it renders the SVGs in Playwright's Chromium.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const here = dirname(fileURLToPath(import.meta.url));
const VENDOR = join(here, '../vendor');
const OUT = join(VENDOR, 'favicon.ico');
// 16 and 32 px have their own drawings on whole pixels; the main artwork's 3.6-unit columns and 1.6-unit gaps blur at those sizes.
const IMAGES = [
  { size: 16, src: 'icon-16.svg' },
  { size: 32, src: 'icon-32.svg' },
  { size: 48, src: 'icon.svg' },
  { size: 256, src: 'icon.svg' },
];

async function render(page, { size, src }) {
  const svg = readFileSync(join(VENDOR, src), 'utf8').replace(/<!--[\s\S]*?-->/g, '').trim();
  await page.setViewportSize({ width: size, height: size });
  await page.setContent(
    `<!doctype html><style>html,body{margin:0;background:transparent}svg{display:block;width:${size}px;height:${size}px}</style>${svg}`);
  return page.screenshot({ type: 'png', omitBackground: true, clip: { x: 0, y: 0, width: size, height: size } });
}

// ICONDIR (6 bytes) + one ICONDIRENTRY (16 bytes) per image + the PNG payloads back to back.
function packIco(images) {
  const dir = Buffer.alloc(6 + 16 * images.length);
  dir.writeUInt16LE(0, 0);               // reserved
  dir.writeUInt16LE(1, 2);               // type: icon
  dir.writeUInt16LE(images.length, 4);
  let offset = dir.length;
  images.forEach(({ size, png }, i) => {
    const at = 6 + 16 * i;
    dir.writeUInt8(size >= 256 ? 0 : size, at);       // width; 0 means 256
    dir.writeUInt8(size >= 256 ? 0 : size, at + 1);   // height
    dir.writeUInt8(0, at + 2);           // palette size: none
    dir.writeUInt8(0, at + 3);           // reserved
    dir.writeUInt16LE(1, at + 4);        // colour planes
    dir.writeUInt16LE(32, at + 6);       // bits per pixel
    dir.writeUInt32LE(png.length, at + 8);
    dir.writeUInt32LE(offset, at + 12);
    offset += png.length;
  });
  return Buffer.concat([dir, ...images.map((i) => i.png)]);
}

const pngDir = process.argv.includes('--png') ? process.argv[process.argv.indexOf('--png') + 1] : null;
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ deviceScaleFactor: 1 });
  const images = [];
  for (const image of IMAGES) images.push({ size: image.size, png: await render(page, image) });
  writeFileSync(OUT, packIco(images));
  if (pngDir) {
    mkdirSync(pngDir, { recursive: true });
    for (const { size, png } of images) writeFileSync(join(pngDir, `favicon-${size}.png`), png);
  }
  console.log(`favicon.ico: ${images.map((i) => `${i.size}px ${i.png.length}B`).join(', ')}`);
} finally {
  await browser.close();
}
