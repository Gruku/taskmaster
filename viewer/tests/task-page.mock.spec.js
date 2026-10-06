// User intent: the full task page must hold up as a frame — the Document/Graph switch shows what is on screen, Edit is
// the page's one primary in row 1, a load that fails says so in words with one way on, and another writer, a reload
// mid-edit or leaving the page (while it loads, or with Edit open) never paints over the user or loses their typing.
import { test, expect } from '@playwright/test';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD, DETAIL_TASK, RICH_RELATED, taskDetail } from './mock-fixtures.js';

const DETAIL = '/api/task/T-102/detail';
const TABLE = {
  '/api/board': BOARD, '/api/backlog': BOARD, '/api/bugs': [],
  [DETAIL]: taskDetail(DETAIL_TASK, 't1:fixture', RICH_RELATED),
  'PUT /api/viewer/prefs': {},
};

let errors;
let patches;
test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  errors = [];
  patches = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('request', (r) => { if (r.method() === 'PATCH') patches.push(new URL(r.url()).pathname); });
});
// A write the mock did not expect means the page talked to an endpoint this spec never set up.
test.afterEach(async ({ page }) => {
  expect(unmockedWrites(page)).toEqual([]);
  expect(errors).toEqual([]);
});

// The document classes the mount itself, so its own node is the one to look for.
const doc = (page) => page.locator('#screen-mount.td-doc');
const h1 = (page) => page.locator('#screen-mount h1.td-title');

async function open(page, hash = '#/task/T-102', table = {}) {
  await mockApi(page, { ...TABLE, ...table });
  await page.goto('/' + hash);
}

// Another writer changes the task: the next detail read has `task`, and a poll brings a new board revision.
async function changedElsewhere(page, task) {
  await page.route('**/api/task/T-102/detail', (route) => route.fulfill({ json: taskDetail(task, 't1:other', RICH_RELATED) }));
  await bumpRevision(page);
}
// Another writer renames the task: the next detail read has the new title, and a poll brings a new board revision.
async function renamedElsewhere(page, title) {
  await page.route('**/api/task/T-102/detail', (route) => route.fulfill({ json: taskDetail({ ...DETAIL_TASK, title }, 't1:other', RICH_RELATED) }));
  await bumpRevision(page);
}
function bumpRevision(page) {
  return page.evaluate(() => import('/js/store.js').then(({ store }) => {
    const next = structuredClone(store.getBacklog());
    next.revision = `r-${Date.now()}`;
    store.setBoard(next);
  }));
}

// Holds the next detail read until the test releases it with the detail to answer; later reads answer that detail too.
async function holdDetail(page) {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  await page.route('**/api/task/T-102/detail', async (route) => {
    const json = await gate;
    await route.fulfill({ json });
  });
  return release;
}

test('the view switch shows the view on screen and saves the choice', async ({ page }) => {
  const saves = [];
  page.on('request', (r) => { if (r.method() === 'PUT' && r.url().endsWith('/api/viewer/prefs')) saves.push(r.postDataJSON()); });
  await open(page, '#/task/T-102?view=B');
  const pressed = page.locator('#topbar-actions .tm-segmented button[aria-pressed="true"]');
  await expect(pressed).toHaveText('Graph');
  await expect(page.locator('.td-page-B')).toBeVisible();

  await page.locator('#topbar-actions .tm-segmented').getByRole('button', { name: 'Document' }).click();
  await expect(pressed).toHaveText('Document');
  await expect(page.locator('.td-page-A')).toBeVisible();
  expect(await page.evaluate(() => location.hash)).toBe('#/task/T-102');
  await expect.poll(() => saves.at(-1)?.screens?.task_detail?.view).toBe('A');

  await page.locator('#topbar-actions .tm-segmented').getByRole('button', { name: 'Graph' }).click();
  await expect(pressed).toHaveText('Graph');
});

test('Edit sits in row 1 as the page\'s primary and the switch alone in row 2', async ({ page }) => {
  await open(page);
  await expect(doc(page)).toBeVisible();
  const edit = page.locator('#topbar-primary [title="Edit task"]');
  await expect(edit).toHaveClass(/(^|\s)btn(\s|$)/);
  await expect(edit).toHaveClass(/(^|\s)btn--primary(\s|$)/);
  await expect(page.locator('#topbar-actions [title="Edit task"]')).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(edit).toBeVisible();
  expect((await edit.boundingBox()).height).toBeGreaterThanOrEqual(44);
});

test('another writer\'s change waits while a section is edited on the page, then shows', async ({ page }) => {
  await open(page);
  await expect(doc(page)).toBeVisible();
  await page.locator('[data-focus="edit:notes"]').click();
  const textarea = page.locator('[data-section="notes"] textarea');
  await expect(textarea).toBeFocused();
  await textarea.press('Control+End');
  await page.keyboard.type(' more');

  await renamedElsewhere(page, 'Renamed elsewhere');
  await page.waitForTimeout(300);
  await expect(h1(page)).toHaveText(DETAIL_TASK.title);
  await expect(textarea).toBeFocused();
  expect(await textarea.inputValue()).toMatch(/ more$/);

  await page.keyboard.press('Escape');
  await expect(h1(page)).toHaveText('Renamed elsewhere');
  expect(await page.evaluate(() => document.activeElement !== document.body && document.activeElement.isConnected)).toBe(true);
  expect(patches).toEqual([]);
});

test('a reload already under way when the title editor opens does not paint over it', async ({ page }) => {
  await open(page);
  await expect(doc(page)).toBeVisible();
  const release = await holdDetail(page);
  const started = page.waitForRequest('**/api/task/T-102/detail');
  await bumpRevision(page);
  await started;

  await h1(page).locator('.ef-editable').click();
  const input = h1(page).locator('input');
  await input.fill('Draft');
  release(taskDetail({ ...DETAIL_TASK, title: 'Renamed elsewhere' }, 't1:other', RICH_RELATED));
  await page.waitForTimeout(300);
  await expect(input).toHaveCount(1);
  await expect(input).toBeFocused();
  await expect(input).toHaveValue('Draft');

  await page.keyboard.press('Escape');
  await expect(h1(page)).toHaveText('Renamed elsewhere');
  expect(patches).toEqual([]);
});

test('leaving the page while it loads leaves nothing behind', async ({ page }) => {
  await mockApi(page, TABLE);
  const release = await holdDetail(page);
  const started = page.waitForRequest('**/api/task/T-102/detail');
  await page.goto('/#/task/T-102');
  await started;
  await page.evaluate(() => { location.hash = '#/kanban'; });
  await expect(page.locator('.card-task').first()).toBeVisible();
  release(taskDetail(DETAIL_TASK, 't1:fixture', RICH_RELATED));
  await page.waitForTimeout(300);

  await expect(page.locator('#screen-mount .td-doc, #screen-mount.td-doc')).toHaveCount(0);
  await expect(h1(page)).toHaveCount(0);
  await expect(page.locator('#topbar-primary [title="Edit task"]')).toHaveCount(0);
  await expect(page.locator('.tm-segmented button', { hasText: 'Graph' })).toHaveCount(0);
});

test('leaving with Edit open: clean closes, typed asks', async ({ page }) => {
  await open(page);
  await expect(doc(page)).toBeVisible();
  const form = page.getByRole('dialog', { name: 'Edit task' });
  await page.locator('#topbar-primary [title="Edit task"]').click();
  await expect(form).toBeVisible();
  await page.evaluate(() => { location.hash = '#/kanban'; });
  await expect(page.locator('.modal')).toHaveCount(0);
  await expect(page.getByRole('alertdialog')).toHaveCount(0);

  await page.evaluate(() => { location.hash = '#/task/T-102'; });
  await expect(doc(page)).toBeVisible();
  await page.locator('#topbar-primary [title="Edit task"]').click();
  await expect(form).toBeVisible();
  const title = form.locator('[data-key="title"]').locator('input, select, textarea').first();
  await expect(title).toBeFocused();
  await page.keyboard.press('End');
  await page.keyboard.type(' (draft)');
  await page.evaluate(() => { location.hash = '#/kanban'; });
  const confirm = page.getByRole('alertdialog', { name: 'Discard changes?' });
  await expect(confirm).toBeVisible();

  await confirm.getByRole('button', { name: 'Keep editing' }).click();
  await expect(confirm).toHaveCount(0);
  await expect(form).toBeVisible();
  await expect(title).toHaveValue(`${DETAIL_TASK.title} (draft)`);

  await page.keyboard.press('Escape');
  await expect(confirm).toBeVisible();
  await confirm.getByRole('button', { name: 'Discard' }).click();
  await expect(page.locator('.modal')).toHaveCount(0);
  expect(patches).toEqual([]);
});

test('a load that fails says so in words and offers Try again', async ({ page }) => {
  let answer = { status: 500, json: { error: 'Traceback: KeyError depends_on' } };
  await mockApi(page, TABLE);
  await page.route('**/api/task/T-102/detail', (route) => route.fulfill(answer));
  await page.goto('/#/task/T-102');
  const block = page.locator('#screen-mount .tm-empty[data-state="error"]');
  await expect(block).toBeVisible();
  await expect(block.locator('.tm-empty__label')).toHaveText('T-102');
  await expect(block.locator('.tm-empty__headline')).toHaveText('Could not load this task');
  const words = await page.locator('#screen-mount').innerText();
  for (const raw of ['Traceback', '500', '/api']) expect(words).not.toContain(raw);
  await expect(page.locator('#topbar-primary > *')).toHaveCount(0);
  await expect(page.locator('#topbar-actions .tm-segmented')).toHaveCount(0);

  answer = { json: taskDetail(DETAIL_TASK, 't1:fixture', RICH_RELATED) };
  await block.getByRole('button', { name: 'Try again' }).click();
  await expect(h1(page)).toHaveText(DETAIL_TASK.title);
});

test('a refused reason goes when another writer changes that field, and stays when they change another', async ({ page }) => {
  const reason = 'Completion blocked: review-gate is still open';
  await open(page, '#/task/T-102', { 'PATCH /api/tasks/T-102': { status: 409, json: { ok: false, error: reason } } });
  await expect(doc(page)).toBeVisible();
  const status = page.locator('#screen-mount [data-field="status"]');
  await status.locator('.ef-editable').click();
  const select = status.locator('select');
  await expect(select).toBeFocused();
  await select.selectOption('done');
  await expect(status.locator('.if-error')).toHaveText(reason);
  await page.keyboard.press('Tab');
  await expect(select).toHaveCount(0);
  await expect(status.locator('.if-error')).toHaveText(reason);

  await changedElsewhere(page, { ...DETAIL_TASK, title: 'Renamed elsewhere' });
  await expect(h1(page)).toHaveText('Renamed elsewhere');
  await expect(status.locator('.if-error'), 'another field changed: the reason still stands').toHaveText(reason);

  await changedElsewhere(page, { ...DETAIL_TASK, title: 'Renamed elsewhere', status: 'in-review' });
  await expect(status.locator('.marker__word')).toHaveText('In review');
  await expect(status.locator('.if-error'), 'the field itself changed: the reason is gone').toHaveText('');
});
