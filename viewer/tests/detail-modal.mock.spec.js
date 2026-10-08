// User intent: the task detail modal must never paint over the user — an inline editor opened while a re-read is in
// flight stays put (B-095), a refused title keeps its reason in view however far the body is scrolled — and it draws
// the task the same way every time: one marker size on first open, after a redraw and on the full page, no meta line.
import { test, expect } from '@playwright/test';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD, DETAIL_TASK, RICH_RELATED, LONG_TASK, LONG_RELATED, taskDetail, kanbanMocks } from './mock-fixtures.js';

const DETAILS = {
  '/api/task/T-102/detail': taskDetail(DETAIL_TASK, 't1:fixture', RICH_RELATED),
  '/api/task/T-105/detail': taskDetail(LONG_TASK, 't1:fixture', LONG_RELATED),
};

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
});
// A write the mock did not expect means the page talked to an endpoint this spec never set up.
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

async function board(page, { theme = 'dark', table = {} } = {}) {
  await mockApi(page, { ...kanbanMocks({ theme }), ...DETAILS, ...table });
  await page.goto('/#/kanban');
  await expect(page.locator('.card-task[data-task-id] > .link-row__link').first()).toBeVisible();
  await expect(card(page, 'T-102')).toBeVisible();
}

const card = (page, id) => page.locator(`.card-task[data-task-id="${id}"] > .link-row__link`);
const detail = (page) => page.locator('.modal--detail');
const titleOf = (dialog) => dialog.locator('.modal-title');

// Clicks the card and waits for the task's document (not the loading state) to be in the dialog.
async function openCard(page, id) {
  await card(page, id).click();
  const dialog = detail(page);
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('.td-doc--embedded')).toBeVisible();
  return dialog;
}

// An open inline picker holds the task's edit lease.
const editing = (page, id) => page.evaluate((i) => import('/js/store.js').then(({ store }) => store.isEditing(i)), id);
// A poll brings a new board revision: the dialog re-reads the task on screen.
const bumpRevision = (page) => page.evaluate(() => import('/js/store.js').then(({ store }) => {
  const next = structuredClone(store.getBacklog());
  next.revision = `r-${Date.now()}`;
  store.setBoard(next);
}));
// Another writer renames the task: the next detail read has the new title, and a poll brings a new board revision.
async function renamedElsewhere(page, title) {
  await page.route('**/api/task/T-102/detail', (route) => route.fulfill({ json: taskDetail({ ...DETAIL_TASK, title }, 't1:other', RICH_RELATED) }));
  await bumpRevision(page);
}

// The marker row's type size and the first marker's height, wherever the document is drawn.
const markerSize = (scope) => scope.locator('.td-markers').first().evaluate((row) => ({
  font: getComputedStyle(row).fontSize,
  height: row.querySelector('.td-marker-host .marker')?.getBoundingClientRect().height,
}));

test('a re-read that lands after an inline editor opened leaves the editor alone (B-095)', async ({ page }) => {
  await board(page);
  const dialog = await openCard(page, 'T-102');

  let release;
  const held = new Promise((r) => { release = r; });
  await page.route('**/api/task/T-102/detail', async (route) => {
    await held;
    await route.fulfill({ json: taskDetail({ ...DETAIL_TASK, title: 'Renamed while editing' }, 't1:other', RICH_RELATED) });
  });
  const asked = page.waitForRequest('**/api/task/T-102/detail');
  await bumpRevision(page);
  await asked;

  // The re-read is in flight; the user opens the status picker before it lands.
  const status = dialog.locator('[data-field="status"]');
  await status.locator('.ef-editable').click();
  const select = status.locator('select');
  await expect(select).toBeFocused();
  expect(await editing(page, 'T-102')).toBe(true);

  const landed = page.waitForResponse('**/api/task/T-102/detail');
  release();
  await landed;
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => r())));
  await expect(select, 'the picker the user opened is still there').toBeFocused();
  expect(await editing(page, 'T-102')).toBe(true);
  await expect(titleOf(dialog)).toHaveText(DETAIL_TASK.title);

  // Leaving the picker ends the edit lease, and the dialog then reads the task again.
  await page.keyboard.press('Tab');
  await expect(titleOf(dialog)).toHaveText('Renamed while editing');
});

test('a refused title stays in view with the body scrolled to the end', async ({ page }) => {
  const reason = 'Titles are frozen during review';
  await board(page, { table: { 'PATCH /api/tasks/T-105': { status: 409, json: { ok: false, error: reason } } } });
  const dialog = await openCard(page, 'T-105');
  const body = dialog.locator('.modal-body');
  await body.evaluate((el) => { el.scrollTop = el.scrollHeight; });
  expect(await body.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);

  const heading = titleOf(dialog);
  await heading.locator('.ef-editable').click();
  const input = heading.locator('input');
  await expect(input).toBeFocused();
  await input.fill('Renamed while frozen');
  await input.press('Enter');

  const message = page.locator('.modal--detail > .td-title-message');
  async function inView() {
    await expect(message.locator('.if-error')).toHaveText(reason);
    await expect(message).toBeVisible();
    const [m, head, scroller] = await Promise.all([
      message.boundingBox(), dialog.locator('.modal-header').boundingBox(), body.boundingBox(),
    ]);
    expect(m.y, 'under the header').toBeGreaterThanOrEqual(head.y + head.height - 1);
    expect(m.y + m.height, 'above the scrolling body').toBeLessThanOrEqual(scroller.y + 1);
    expect(await body.evaluate((el) => el.scrollTop), 'the body is still scrolled').toBeGreaterThan(0);
  }
  await inView();
  // While the editor is open the heading's name is what is typed; the reason is never part of it.
  await expect(page.getByRole('dialog', { name: reason })).toHaveCount(0);
  await expect(heading.locator('.if-error')).toHaveCount(0);

  // Closing the editor ends the edit lease; the task is read and drawn anew, and the reason moves with it.
  const reread = page.waitForResponse('**/api/task/T-105/detail');
  await input.press('Escape');
  await reread;
  await expect(page.getByRole('dialog', { name: LONG_TASK.title, exact: true })).toBeVisible();
  await inView();
  await expect(dialog.locator('.td-title-message')).toHaveCount(1);
});

test('a refused title leaves the body where the reader scrolled it and the editor open', async ({ page }) => {
  await board(page, { table: { 'PATCH /api/tasks/T-105': { status: 409, json: { ok: false, error: 'Titles are frozen during review' } } } });
  const dialog = await openCard(page, 'T-105');
  const body = dialog.locator('.modal-body');
  // Midway, not at the end: at the end the browser clamps scrollTop by however much the header grows while editing.
  const before = await body.evaluate((el) => { el.scrollTop = Math.round((el.scrollHeight - el.clientHeight) / 2); return el.scrollTop; });
  expect(before).toBeGreaterThan(0);
  const heading = titleOf(dialog);
  await heading.locator('.ef-editable').click();
  await heading.locator('input').fill('Renamed while frozen');
  await heading.locator('input').press('Enter');
  await expect(page.locator('.modal--detail > .td-title-message .if-error')).toBeVisible();
  await page.waitForTimeout(300);
  expect(await body.evaluate((el) => el.scrollTop), 'scrollTop after the refusal').toBe(before);
  await expect(heading.locator('input')).toHaveValue('Renamed while frozen');
});

test('the marker row keeps one size through a redraw and matches the full page', async ({ page }) => {
  await board(page);
  const dialog = await openCard(page, 'T-102');
  await expect.poll(async () => (await markerSize(dialog)).height ?? 0).toBeGreaterThan(0);
  const first = await markerSize(dialog);

  // A redraw replaces the row: a measure taken as it goes reads a detached node (no font size, no marker), so each
  // later size is read until the row in the document has it.
  await renamedElsewhere(page, 'Renamed elsewhere');
  await expect(titleOf(dialog)).toHaveText('Renamed elsewhere');
  await expect.poll(() => markerSize(dialog), { message: 'after another writer\'s change' }).toEqual(first);

  await page.goto('/#/task/T-102');
  const doc = page.locator('#screen-mount.td-doc');
  await expect(doc.locator('h1.td-title')).toBeVisible();
  await expect.poll(() => markerSize(doc), { message: 'on the full page' }).toEqual(first);
});

test('the modal has no meta line; the page keeps it', async ({ page }) => {
  await board(page);
  const dialog = await openCard(page, 'T-102');
  await expect(dialog.locator('[data-test="task-id"]')).toHaveCount(0);

  await page.goto('/#/task/T-102');
  await expect(page.locator('#screen-mount h1.td-title')).toBeVisible();
  await expect(page.locator('#screen-mount [data-test="task-id"]')).toHaveCount(1);
});

// A button the code hides with the `hidden` attribute is gone, whatever display its .btn class gives it.
test('Edit is not shown while the task loads, and appears with the task; any hidden .btn is not drawn', async ({ page }) => {
  await board(page);
  let release;
  const held = new Promise((ok) => { release = ok; });
  await page.route('**/api/task/T-102/detail', async (route) => { await held; await route.fallback(); });
  await card(page, 'T-102').click();
  const dialog = detail(page);
  await expect(dialog.locator('[aria-busy="true"], .state-block').first()).toBeVisible();
  const edit = dialog.locator('[data-action="edit"]');
  await expect(edit).toHaveAttribute('hidden', '');
  await expect(edit).toBeHidden();
  release();
  await expect(dialog.locator('.td-doc--embedded')).toBeVisible();
  await expect(edit).toBeVisible();
  const display = await page.evaluate(() => {
    const b = document.createElement('button');
    b.className = 'btn btn--primary btn--icon btn--sm';
    b.hidden = true;
    document.body.append(b);
    const d = getComputedStyle(b).display;
    b.remove();
    return d;
  });
  expect(display).toBe('none');
});
