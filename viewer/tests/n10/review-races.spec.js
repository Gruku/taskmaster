import {test, expect} from '@playwright/test';
import {update} from './fixture.js';

test('a delayed unseen refresh cannot advance an active edit precondition', async ({page}) => {
  await page.goto('/#/task/board-001');
  const field = page.locator('.if-wrap[data-key="title"]');
  await expect(field).toBeVisible();
  const shownEtag = await page.evaluate(async () => (await import('/static/v3/js/store.js')).store.getEtag('task:board-001'));
  let release, arrived;
  const held = new Promise(resolve => { release = resolve; });
  const started = new Promise(resolve => { arrived = resolve; });
  await page.route('**/api/task/board-001/detail', async route => {
    const response = await route.fetch();
    arrived();
    await held;
    await route.fulfill({response});
  });
  await update(page, 'title', 'Peer title while detail refresh is delayed');
  await page.evaluate(async () => (await import('/static/v3/js/store.js')).store.refreshBoard());
  await started;
  await field.click();
  const returned = page.waitForResponse(r => r.url().endsWith('/api/task/board-001/detail'));
  release();
  await returned;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const saved = page.waitForResponse(r => r.request().method() === 'PATCH' && r.url().endsWith('/api/tasks/board-001'));
  await field.locator('input').fill('Draft based on the old displayed revision');
  await field.locator('input').press('Enter');
  const response = await saved;
  expect(response.request().headers()['if-match']).toBe(shownEtag);
  expect(response.status()).toBe(409);
  const current = await (await page.request.get('/api/task/board-001/detail')).json();
  expect(current.task.title).toBe('Peer title while detail refresh is delayed');
});

test('modal dependency navigation disposes the abandoned edit and pending autosave', async ({page}) => {
  await page.route('**/api/viewer/prefs', async route => {
    if (route.request().method() === 'GET') await route.fulfill({json: {}});
    else await route.continue();
  });
  const original = await (await page.request.get('/api/task/board-002/detail')).json();
  const writes = [];
  page.on('request', request => {
    if (request.method() === 'PATCH' && request.url().endsWith('/api/tasks/board-002')) writes.push(request);
  });
  await page.goto('/#/kanban');
  await page.locator('.card-task[data-task-id="board-002"] .card-title').click();
  const field = page.locator('.dm-modal .if-wrap[data-key="title"]');
  await expect(field).toBeVisible();
  await page.clock.install();
  await page.clock.pauseAt(new Date());
  await field.click();
  await field.locator('input').fill('Abandoned modal draft must never autosave');
  // Dispatch navigation without first blurring the editor: blur intentionally
  // commits. This leaves the debounce pending so disposal must cancel it.
  await page.locator('.dm-modal .td-dep').filter({hasText: 'board-001'}).evaluate(link => link.click());
  await expect(page.locator('.dm-openfull')).toHaveAttribute('href', '#/task/board-001');
  const stillEditing = await page.evaluate(async () => (await import('/static/v3/js/store.js')).store.isEditing('board-002'));
  await page.clock.runFor(1000);
  const current = await (await page.request.get('/api/task/board-002/detail')).json();
  expect({stillEditing, writes: writes.length, title: current.task.title}).toEqual({stillEditing: false, writes: 0, title: original.task.title});
});
