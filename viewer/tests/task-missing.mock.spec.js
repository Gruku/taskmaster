// User intent: a task that doesn't exist must show a plain not-found state and no controls from the previously opened task.
import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api.js';

test('missing task shows not-found and clears the topbar', async ({ page }) => {
  await mockApi(page, {
    '/api/task/NOPE-999/detail': { status: 404, json: { ok: false, error: 'unknown task' } },
  });
  await page.goto('/#/task/NOPE-999');
  await expect(page.locator('#screen-mount .tm-empty__headline')).toHaveText('Task not found');
  await expect(page.locator('#screen-mount')).not.toContainText('GET /api');
  await expect(page.locator('#topbar-actions > *')).toHaveCount(0);
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
  await expect(page.locator('#topbar-actions > *')).toHaveCount(0);
  expect(errors).toEqual([]);
});
