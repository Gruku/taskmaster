// User intent: the gate's touch-target probe may only measure a link by a bigger box when the link's ::after truly
// covers that box — these self-tests hold it strict both ways, so the exemption can never quietly hide a small target.
import { test, expect } from '@playwright/test';
import { mockApi, unmockedWrites } from './mock-api.js';
import { smallTouchTargets } from './a11y-probe.js';

test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

// A 60px row holding a 20px link; `after` is the link's ::after rule, `row` and `link` extra declarations.
async function probe(page, { after, row = '', link = '' }) {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, {});
  await page.setContent(`<!doctype html><style>
    body { margin: 0; }
    .row { height: 60px; ${row} }
    .cell { display: block; }
    a { display: block; height: 20px; line-height: 20px; ${link} }
    a::after { ${after} }
  </style><div class="row"><span class="cell"><a href="#x">Task</a></span></div>`);
  return page.evaluate(smallTouchTargets);
}

const COVER = "content: ''; position: absolute; inset: 0;";

test('a link whose ::after covers its positioned row is measured by the row', async ({ page }) => {
  expect(await probe(page, { after: COVER, row: 'position: relative;' })).toEqual([]);
});

test('a transformed row contains the ::after just as a positioned one does', async ({ page }) => {
  expect(await probe(page, { after: COVER, row: 'transform: translateZ(0);' })).toEqual([]);
});

test('a ::after that is not at inset 0 on every side leaves the link measured by its own box', async ({ page }) => {
  const out = await probe(page, { after: "content: ''; position: absolute; inset: 0 0 auto 0;", row: 'position: relative;' });
  expect(out).toEqual(['a "Task" 20px']);
});

test('a ::after that is not absolute leaves the link measured by its own box', async ({ page }) => {
  const out = await probe(page, { after: "content: ''; position: relative; inset: 0;", row: 'position: relative;' });
  expect(out).toEqual(['a "Task" 20px']);
});

test('a ::after with no content is not rendered and covers nothing', async ({ page }) => {
  const out = await probe(page, { after: 'position: absolute; inset: 0;', row: 'position: relative;' });
  expect(out).toEqual(['a "Task" 20px']);
});

test('a positioned link contains its own ::after, so the link\'s box is measured', async ({ page }) => {
  const out = await probe(page, { after: COVER, row: 'position: relative;', link: 'position: relative;' });
  expect(out).toEqual(['a "Task" 20px']);
});
