import {test, expect} from '@playwright/test';
import {update} from './fixture.js';

for (const [field, component, selected, outside, reset] of [
  ['priority', 'priority-chips', 'high', 'low', 'medium'],
  ['epic', 'epic-chips', 'board', 'other', 'board'],
  ['phase', 'phase-stepper', 'dev', 'next', 'dev'],
]) {
  test(`${field} membership follows peer deltas without navigation`, async ({page}) => {
    await page.route('**/api/viewer/prefs', async route => {
      if (route.request().method() === 'GET') await route.fulfill({json: {}});
      else await route.continue();
    });
    await update(page, field, selected);
    await page.goto('/#/kanban');
    const card = page.locator('.card-task[data-task-id="board-001"]');
    await expect(card).toBeVisible();
    await page.locator(`[data-cmp="${component}"] [data-key="${selected}"]`).click();
    await page.evaluate(() => { window.navigationSentinel = true; });
    for (const [value, present] of [[outside, false], [selected, true]]) {
      const delta = page.waitForResponse(async r => r.url().includes('/api/board?since=') && r.status() === 200 && (await r.json()).tasks_upsert?.some(t => t.id === 'board-001'));
      await update(page, field, value);
      await delta;
      if (present) await expect(card).toBeVisible();
      else await expect(card).toHaveCount(0);
      expect(await page.evaluate(() => window.navigationSentinel)).toBe(true);
    }
    await update(page, field, reset);
  });
}
