import {test, expect} from '@playwright/test';

test('a late initial detail response cannot paint over a newer board route', async ({page}) => {
  let release, arrived;
  const held = new Promise(resolve => { release = resolve; });
  const started = new Promise(resolve => { arrived = resolve; });
  await page.route('**/api/task/board-001/detail', async route => {
    const response = await route.fetch();
    arrived();
    await held;
    await route.fulfill({response});
  });
  await page.goto('/#/task/board-001');
  await started;
  await page.evaluate(() => { location.hash = '#/kanban'; });
  await expect(page.locator('.kanban-page')).toBeVisible();
  const returned = page.waitForResponse(r => r.url().endsWith('/api/task/board-001/detail'));
  release();
  await returned;
  // Force subsequent promise/render continuations and a frame, not a time guess.
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(page.locator('.td-page')).toHaveCount(0);
  await expect(page.locator('.kanban-page')).toBeVisible();
});

test('first edit is conditioned and refreshes the card with a delta', async ({page}) => {
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('/#/task/board-001');
  const field = page.locator('.if-wrap[data-key="title"]');
  await expect(field).toBeVisible();
  await field.click();
  const input = field.locator('input');
  const saved = page.waitForResponse(r => r.request().method() === 'PATCH' && r.url().endsWith('/api/tasks/board-001'));
  await input.fill('Saved in browser');
  await input.press('Enter');
  const response = await saved;
  expect(response.status()).toBe(200);
  expect(response.request().headers()['if-match']).toMatch(/^t1:/);
  await expect(field).toContainText('Saved in browser');
  await expect.poll(() => page.evaluate(async () => (await import('/static/v3/js/store.js')).store.getBacklog()?.tasks.find(t => t.id === 'board-001')?.title)).toBe('Saved in browser');
  expect(errors).toEqual([]);
});

test('unknown task renders a not-found state', async ({page}) => {
  await page.goto('/#/task/no-such-task');
  await expect(page.locator('#screen-mount')).toContainText('Task not found');
});
