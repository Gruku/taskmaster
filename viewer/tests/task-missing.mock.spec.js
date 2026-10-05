// User intent: a task that doesn't exist must show a plain not-found state and no controls from the previously opened task.
import { test, expect } from '@playwright/test';
import { mockApi, unmockedWrites } from './mock-api.js';

// A write the mock did not expect means the page talked to an endpoint this spec never set up.
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

test('missing task shows not-found and clears the topbar', async ({ page }) => {
  await mockApi(page, {
    '/api/task/NOPE-999/detail': { status: 404, json: { ok: false, error: 'unknown task' } },
  });
  await page.goto('/#/task/NOPE-999');
  await expect(page.locator('#screen-mount .tm-empty__headline')).toHaveText('Task not found');
  await expect(page.locator('#screen-mount')).not.toContainText('GET /api');
  // Row 2 keeps only its hidden Filters button.
  await expect(page.locator('#topbar-actions > :not(.overflow-more)')).toHaveCount(0);
  await expect(page.locator('#topbar-actions')).toBeHidden();
});

test('the not-found link takes the signature colour in both themes, not the browser default blue', async ({ page }) => {
  const missing = { '/api/task/NOPE-999/detail': { status: 404, json: { ok: false, error: 'unknown task' } } };
  const link = page.locator('#screen-mount .tm-empty__hint a');

  await mockApi(page, { ...missing, '/api/viewer/prefs': { theme: 'dark', ui: {}, screens: {} } });
  await page.goto('/#/task/NOPE-999');
  await expect(link).toHaveCSS('color', 'rgb(138, 158, 235)');   // text-accent dark = signature-vivid #8a9eeb

  await mockApi(page, { ...missing, '/api/viewer/prefs': { theme: 'light', ui: {}, screens: {} } });
  await page.reload();
  await expect(link).toHaveCSS('color', 'rgb(63, 88, 192)');     // text-accent light = signature #3f58c0
});

test('no task id shows the empty state without inline styles', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#/task');
  await expect(page.locator('#screen-mount .tm-empty__headline')).toHaveText('No task open');
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
  await expect(page.locator('#topbar-actions [aria-label="Edit task"]')).toBeVisible();
  await expect(page.locator('#screen-mount')).toContainText('A real task');

  await page.evaluate(() => { location.hash = '#/task/NOPE-999'; });
  await expect(page.locator('#screen-mount .tm-empty__headline')).toHaveText('Task not found');
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
