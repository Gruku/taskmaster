// User intent: a task that doesn't exist must show a plain not-found state and no controls from the previously opened task.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD, bugDetailMocks, taskPageMocks } from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

// A write the mock did not expect means the page talked to an endpoint this spec never set up.
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

test('missing task shows not-found and clears the topbar', async ({ page }) => {
  await mockApi(page, taskPageMocks());
  await page.goto('/#/task/NOPE-999');
  await expect(page.locator('#screen-mount .tm-empty__headline')).toHaveText('Task not found');
  await expect(page.locator('#screen-mount .tm-empty__label')).toHaveText('NOPE-999');
  await expect(page.locator('#screen-mount')).not.toContainText('GET /api');
  // Row 2 keeps only its hidden Filters button.
  await expect(page.locator('#topbar-actions > :not(.overflow-more)')).toHaveCount(0);
  await expect(page.locator('#topbar-actions')).toBeHidden();
});

test('the not-found state offers one way on, a link styled as a button', async ({ page }) => {
  const link = page.locator('#screen-mount .tm-empty a.btn');

  for (const theme of ['dark', 'light']) {
    await mockApi(page, taskPageMocks({ theme }));
    await page.goto('/#/task/NOPE-999');
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(link).toHaveText('Open the Kanban');
    await expect(link).toHaveAttribute('href', '#/kanban');
    await page.evaluate(axeSource);
    const result = await page.evaluate(() => window.axe.run(document.getElementById('screen-mount'), { runOnly: ['color-contrast'] }));
    expect(result.violations.map((v) => `${theme} ${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  }
});

test('no task id shows the empty state without inline styles', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#/task');
  await expect(page.locator('#screen-mount .tm-empty__headline')).toHaveText('No task open');
  const link = page.locator('#screen-mount .tm-empty a.btn');
  await expect(link).toHaveText('Open the Kanban');
  await expect(link).toHaveAttribute('href', '#/kanban');
  expect(await page.locator('#screen-mount [style]').count()).toBe(0);
});

test('missing task opened after a real one drops that task\'s topbar controls', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await mockApi(page, {
    '/api/task/REAL-1/detail': {
      task: { id: 'REAL-1', title: 'A real task', status: 'todo', epic: 'demo' },
      related: {},
      claim: null,
      etag: 't1:test',
    },
    '/api/task/NOPE-999/detail': { status: 404, json: { ok: false, error: 'unknown task' } },
  });
  await page.goto('/#/task/REAL-1');
  await expect(page.locator('#topbar-primary [aria-label="Edit task"]')).toBeVisible();
  await expect(page.locator('#screen-mount')).toContainText('A real task');

  await page.evaluate(() => { location.hash = '#/task/NOPE-999'; });
  await expect(page.locator('#screen-mount .tm-empty__headline')).toHaveText('Task not found');
  await expect(page.locator('#topbar-primary > *')).toHaveCount(0);
  await expect(page.locator('#topbar-actions > :not(.overflow-more)')).toHaveCount(0);
  await expect(page.locator('#topbar-actions')).toBeHidden();
  expect(errors).toEqual([]);
});

test('a missing task is not remembered as the last one opened; a real one is', async ({ page }) => {
  await mockApi(page, {
    '/api/task/REAL-1/detail': {
      task: { id: 'REAL-1', title: 'A real task', status: 'todo', epic: 'demo' },
      related: {},
      claim: null,
      etag: 't1:test',
    },
    '/api/task/NOPE-999/detail': { status: 404, json: { ok: false, error: 'unknown task' } },
  });
  const puts = [];
  page.on('request', (r) => { if (r.method() === 'PUT' && r.url().endsWith('/api/viewer/prefs')) puts.push(r.postData()); });
  const headline = page.locator('#screen-mount .tm-empty__headline');

  await page.goto('/#/task/NOPE-999');
  await expect(headline).toHaveText('Task not found');
  // Bare #/task re-opens the last task; a missing id must not have become that.
  await page.evaluate(() => { location.hash = '#/task'; });
  await expect(headline).toHaveText('No task open');
  expect(await page.evaluate(() => location.hash)).toBe('#/task');
  await page.waitForTimeout(700);   // longer than the prefs debounce: a queued save would have gone out
  expect(puts.join('\n')).not.toContain('last_task_id');

  await page.evaluate(() => { location.hash = '#/task/REAL-1'; });
  await expect(page.locator('#screen-mount')).toContainText('A real task');
  await expect.poll(() => puts.join('\n')).toContain('"last_task_id":"REAL-1"');
  expect(puts.join('\n')).not.toContain('NOPE-999');
  await page.evaluate(() => { location.hash = '#/task'; });
  await expect(page.locator('#screen-mount')).toContainText('A real task');
  expect(await page.evaluate(() => location.hash)).toBe('#/task/REAL-1');
});

test('a remembered task that no longer exists is forgotten', async ({ page }) => {
  await mockApi(page, {
    '/api/viewer/prefs': { theme: 'dark', ui: { last_task_id: 'T-999' }, screens: {} },
    '/api/task/T-999/detail': { status: 404, json: { error: 'not found' } },
  });
  const puts = [];
  page.on('request', (r) => { if (r.method() === 'PUT' && r.url().endsWith('/api/viewer/prefs')) puts.push(JSON.parse(r.postData())); });
  await page.goto('/#/task');
  await expect(page.locator('#screen-mount .tm-empty[data-state="missing"]')).toBeVisible();
  await expect(page.locator('#screen-mount .tm-empty__label')).toHaveText('T-999');
  await expect.poll(() => puts.some((p) => p.ui && 'last_task_id' in p.ui && p.ui.last_task_id === null)).toBe(true);
  // The next bare #/task has nothing to follow.
  await page.evaluate(() => { location.hash = '#/task'; });
  await expect(page.locator('#screen-mount .tm-empty__headline')).toHaveText('No task open');
  expect(await page.evaluate(() => location.hash)).toBe('#/task');
});

// "Not found" is the 404 status, never a guess from the message: a server failure on an id that contains 404 is still
// a failure to load, and a real 404 is still the missing block.
const BOARD_404 = {
  ...BOARD,
  tasks: [...BOARD.tasks, { id: 'T-404', title: 'A task whose id says 404', status: 'todo', priority: 'medium', epic: 'viewer', phase: 'P1', depends_on: [] }],
};
const FAILS_500 = { status: 500, json: { error: 'x' } };

test('a task page whose id contains 404 and fails with a 500 says it could not load, not that the task is missing', async ({ page }) => {
  await mockApi(page, { ...taskPageMocks(), '/api/task/T-404/detail': FAILS_500 });
  await page.goto('/#/task/T-404');
  const block = page.locator('#screen-mount .tm-empty');
  await expect(block).toHaveAttribute('data-state', 'error');
  await expect(block.locator('.tm-empty__headline')).toHaveText('Could not load this task');
  await expect(block).not.toContainText('not found');
});

// Opens the detail modal from the T-404 card on a board whose T-404 detail answers `detail`.
async function openT404(page, detail) {
  await mockApi(page, {
    '/api/viewer/prefs': { theme: 'dark', ui: {}, screens: {} },
    '/api/board': BOARD_404, '/api/backlog': BOARD_404, '/api/bugs': [],
    '/api/task/T-404/detail': detail,
  });
  await page.goto('/#/kanban');
  const sel = '.card-task[data-task-id="T-404"]';
  // At phone width the Kanban shows one column at a time: pick the column holding the card.
  if (await page.evaluate(() => innerWidth <= 768)) {
    await page.locator(sel).waitFor({ state: 'attached' });
    const panel = await page.locator(sel).evaluate((c) => c.closest('.kanban-col').id);
    await page.locator(`[id="${panel}-tab"]`).click();
  }
  await page.locator(`${sel} > .link-row__link`).click();
  return page.locator('.modal--detail');
}

test('the detail modal says a 500 on T-404 could not load, not that the task is missing', async ({ page }) => {
  const dialog = await openT404(page, FAILS_500);
  await expect(dialog.locator('.tm-empty')).toHaveAttribute('data-state', 'error');
  await expect(dialog.locator('.tm-empty__headline')).toHaveText('Could not load this task');
  await expect(dialog).not.toContainText('not found');
});

test('the detail modal shows a real 404 as the missing block', async ({ page }) => {
  const dialog = await openT404(page, { status: 404, json: { ok: false, error: 'unknown task' } });
  await expect(dialog.locator('.tm-empty')).toHaveAttribute('data-state', 'missing');
  await expect(dialog.locator('.tm-empty__headline')).toHaveText('This task was not found');
});

test('a bug page whose id contains 404 and fails with a 500 says it could not load, not that the bug is missing', async ({ page }) => {
  await mockApi(page, { ...bugDetailMocks(), '/api/bugs': [], '/api/issues': { issues: [] }, '/api/bugs/B-404': FAILS_500 });
  await page.goto('/#/bug/B-404');
  const block = page.locator('#screen-mount .tm-empty');
  await expect(block).toHaveAttribute('data-state', 'error');
  await expect(block.locator('.tm-empty__headline')).toHaveText('Could not load this bug');
  await expect(block).not.toContainText('not found');
});
