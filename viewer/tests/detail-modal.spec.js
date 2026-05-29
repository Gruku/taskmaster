// plugins/taskmaster/viewer/tests/detail-modal.spec.js
import { test, expect } from '@playwright/test';

async function setMode(request, mode) {
  await request.put('/api/viewer/prefs', {
    data: { ui: { detail_view_mode: mode }, kanban: { filters: { search: '' } } },
  });
}

test.describe('Detail modal (task)', () => {
  test('modal mode: clicking a kanban card opens the overlay, Esc closes it', async ({ page, request }) => {
    await setMode(request, 'modal');
    await page.goto('/v3');
    await page.evaluate(() => location.hash = '#/kanban');
    const card = page.locator('.card-task').first();
    await card.waitFor();
    await card.click();
    await expect(page.locator('.dm-overlay')).toBeVisible();
    await expect(page).toHaveURL(/#\/kanban$/);          // overlay, not a route change
    await page.keyboard.press('Escape');
    await expect(page.locator('.dm-overlay')).toHaveCount(0);
  });

  test('modal mode: Open full navigates to the route and closes the overlay', async ({ page, request }) => {
    await setMode(request, 'modal');

    await page.goto('/v3');
    await page.evaluate(() => location.hash = '#/kanban');
    await page.locator('.card-task').first().click();
    await page.locator('.dm-openfull').click();
    await expect(page).toHaveURL(/#\/task\//);
    await expect(page.locator('.dm-overlay')).toHaveCount(0);
  });

  test('full mode: clicking a card navigates to the route, no overlay', async ({ page, request }) => {
    await setMode(request, 'full');
    await page.goto('/v3');
    await page.evaluate(() => location.hash = '#/kanban');
    await page.locator('.card-task').first().click();
    await expect(page).toHaveURL(/#\/task\//);
    await expect(page.locator('.dm-overlay')).toHaveCount(0);
  });
});
