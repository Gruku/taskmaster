// User intent: the gate's two probes may only excuse an element for a real reason — a link measured by the box its ::after
// truly covers, a pointer target reached through a real control or a row link's cover — and these self-tests hold each
// exemption strict both ways, so it can never quietly hide a small or mouse-only target.
import { test, expect } from '@playwright/test';
import { mockApi, unmockedWrites } from './mock-api.js';
import { smallTouchTargets, pointerOnlyTargets } from './a11y-probe.js';

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

// ── pointerOnlyTargets ── `body` is the page; `.p` is a pointer span, and a covered row's link stretches over the row.
async function pointer(page, body, css = '') {
  await mockApi(page, {});
  await page.setContent(`<!doctype html><style>
    .p { cursor: pointer; display: inline-block; width: 40px; height: 20px; }
    .link-row { position: relative; display: block; min-height: 60px; }
    .link-row__link::after { content: ''; position: absolute; inset: 0; }
    .link-row__controls { position: relative; z-index: 1; }
    ${css}
  </style>${body}`);
  return page.evaluate(pointerOnlyTargets);
}

test('a pointer span inside a tabindex=-1 dialog is mouse-only: the dialog is focusable, not interactive', async ({ page }) => {
  expect(await pointer(page, '<div role="dialog" tabindex="-1"><span class="p">x</span></div>')).toEqual(['span.p "x"']);
});

test('a pointer span inside a tabindex=0 tab panel is mouse-only', async ({ page }) => {
  expect(await pointer(page, '<div role="tabpanel" tabindex="0"><span class="p">x</span></div>')).toEqual(['span.p "x"']);
});

test('a pointer span in a covered row\'s controls slot is mouse-only: the slot sits above the cover', async ({ page }) => {
  const out = await pointer(page, '<div class="link-row"><a class="link-row__link" href="#t">Task</a>'
    + '<div class="link-row__controls"><span class="p">x</span></div></div>');
  expect(out).toEqual(['span.p "x"']);
});

test('a row whose link\'s ::after is not at inset 0 does not cover its body text', async ({ page }) => {
  const out = await pointer(page, '<div class="link-row"><a class="link-row__link" href="#t">Task</a><span class="p">x</span></div>',
    '.link-row__link::after { inset: 0 0 auto 0; }');
  expect(out).toEqual(['span.p "x"']);
});

test('a pointer span inside a button is reached through the button', async ({ page }) => {
  expect(await pointer(page, '<button type="button"><span class="p">x</span></button>')).toEqual([]);
});

test('a role=button with tabindex=0 is reached; without a tabindex it is not', async ({ page }) => {
  const out = await pointer(page, '<span class="p" role="button" tabindex="0">a</span> <span class="p" role="button">b</span>');
  expect(out).toEqual(['span.p "b"']);
});

test('a covered row and its body text are the link\'s', async ({ page }) => {
  const out = await pointer(page, '<div class="link-row p"><a class="link-row__link" href="#t">Task</a></div>'
    + '<div class="link-row"><a class="link-row__link" href="#t">Task</a><span class="p">x</span></div>');
  expect(out).toEqual([]);
});

test('inert content is skipped, as the touch-target probe skips it', async ({ page }) => {
  expect(await pointer(page, '<div inert><span class="p">x</span></div>')).toEqual([]);
});
