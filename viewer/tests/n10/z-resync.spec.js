import {test, expect} from '@playwright/test';
import {control} from './fixture.js';

test.beforeEach(async ({page}) => {
  await page.route('**/api/viewer/prefs', async route => {
    if (route.request().method() === 'GET') await route.fulfill({json: {}});
    else await route.continue();
  });
});

async function equalFresh(page) {
  const fresh = await (await page.request.get('/api/board')).json();
  const applied = await page.evaluate(async () => {
    const {store} = await import('/static/v3/js/store.js');
    const {tasks, epics, phases} = store.getBacklog();
    return {tasks, epics, phases};
  });
  expect(applied).toEqual({tasks: fresh.tasks, epics: fresh.epics, phases: fresh.phases});
}

test('dependency navigation, back, and removal while detail is open', async ({page}) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/#/task/board-002');
  await expect(page.locator('.if-wrap[data-key="title"]')).toBeVisible();
  await page.locator('.td-dep').filter({hasText: 'board-001'}).click();
  await expect(page).toHaveURL(/#\/task\/board-001$/);
  await page.goBack();
  await expect(page).toHaveURL(/#\/task\/board-002$/);
  await page.goto('/#/kanban');
  await page.locator('.card-task[data-task-id="board-002"] .card-title').click();
  await page.locator('.dm-modal .td-dep').filter({hasText: 'board-001'}).click();
  await expect(page.locator('.dm-modal .td-title')).toBeVisible();
  await page.getByRole('link', {name: 'Open full'}).click();
  await expect(page).toHaveURL(/#\/task\/board-001$/);
  await page.goBack();
  await expect(page).toHaveURL(/#\/kanban$/);
  await page.goto('/#/task/board-002');
  await expect(page.locator('.if-wrap[data-key="title"]')).toBeVisible();
  await control(page, ['--action', 'remove', '--task', 'board-002']);
  await expect(page.locator('body')).toContainText(/not found/i, {timeout: 10000});
  expect(errors).toEqual([]);
});

test('201 changed rows and a new store at the same port resync to a fresh board', async ({page}) => {
  test.setTimeout(60000);
  await page.goto('/#/kanban');
  await expect(page.locator('.card-task').first()).toBeVisible();
  let release;
  const changed = new Promise(resolve => { release = resolve; });
  await page.route('**/api/board?since=*', async route => { await changed; await route.continue(); });
  const capped = page.waitForResponse(async r => r.url().includes('/api/board?since=') && r.status() === 200 && (await r.json()).resync === 'too_many');
  await control(page, ['--action', 'burst']);
  release();
  await capped;
  await expect.poll(async () => page.evaluate(async () => (await import('/static/v3/js/store.js')).store.getBacklog().tasks.find(t => t.id === 'bulk-000').title)).toBe('Changed 0');
  await equalFresh(page);
  const swapped = page.waitForResponse(async r => r.url().includes('/api/board?since=') && r.status() === 200 && (await r.json()).resync === 'store_changed');
  await control(page, ['--action', 'swap']);
  await swapped;
  await expect.poll(async () => page.evaluate(async () => (await import('/static/v3/js/store.js')).store.getBacklog().tasks.find(t => t.id === 'bulk-000').title)).toBe('Bulk 0');
  await equalFresh(page);
});
