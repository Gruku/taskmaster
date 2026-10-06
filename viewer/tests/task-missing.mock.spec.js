// User intent: a task that doesn't exist must show a plain not-found state and no controls from the previously opened task.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

// A write the mock did not expect means the page talked to an endpoint this spec never set up.
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

test('missing task shows not-found and clears the topbar', async ({ page }) => {
  await mockApi(page, {
    '/api/task/NOPE-999/detail': { status: 404, json: { ok: false, error: 'unknown task' } },
  });
  await page.goto('/#/task/NOPE-999');
  await expect(page.locator('#screen-mount .tm-empty__headline')).toHaveText('Task not found');
  await expect(page.locator('#screen-mount .tm-empty__label')).toHaveText('NOPE-999');
  await expect(page.locator('#screen-mount')).not.toContainText('GET /api');
  // Row 2 keeps only its hidden Filters button.
  await expect(page.locator('#topbar-actions > :not(.overflow-more)')).toHaveCount(0);
  await expect(page.locator('#topbar-actions')).toBeHidden();
});

test('the not-found state offers one way on, a link styled as a button', async ({ page }) => {
  const missing = { '/api/task/NOPE-999/detail': { status: 404, json: { ok: false, error: 'unknown task' } } };
  const link = page.locator('#screen-mount .tm-empty a.btn');

  for (const theme of ['dark', 'light']) {
    await mockApi(page, { ...missing, '/api/viewer/prefs': { theme, ui: {}, screens: {} } });
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
