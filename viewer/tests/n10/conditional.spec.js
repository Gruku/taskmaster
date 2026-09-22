import {test, expect} from '@playwright/test';

test('idle poll is an empty 304, without parsing, store emission, or repaint', async ({page}) => {
  await page.goto('/#/kanban');
  await expect(page.locator('.card-task').first()).toBeVisible();
  const first = await page.request.get('/api/board');
  const board = await first.json();
  expect(new Set(board.tasks.map(t => t.id)).size).toBe(board.tasks.length);
  expect(board).not.toHaveProperty('_rows');
  expect(board).not.toHaveProperty('context');
  expect(board.epics.every(e => !('tasks' in e))).toBeTruthy();
  await page.evaluate(async () => {
    const {store} = await import('/static/v3/js/store.js');
    window.boardEmits = 0;
    store.subscribe('backlog', () => window.boardEmits++);
    window.firstCard = document.querySelector('.card-task');
  });
  const response = await page.waitForResponse(r => r.url().includes('/api/board?since=') && r.status() === 304);
  expect(response.headers()['content-length']).toBe('0');
  await page.waitForResponse(r => r.url().includes('/api/board?since=') && r.status() === 304);
  expect(await page.evaluate(() => window.boardEmits)).toBe(0);
  expect(await page.evaluate(() => window.firstCard === document.querySelector('.card-task'))).toBeTruthy();
});
