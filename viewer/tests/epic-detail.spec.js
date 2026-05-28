// plugins/taskmaster/viewer/tests/epic-detail.spec.js
import { test, expect } from '@playwright/test';

test.describe('Epic detail', () => {
  test('route #/epic resolves with page-title Epic', async ({ page }) => {
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    await page.goto('/v3');
    await page.evaluate(() => location.hash = '#/epic/__none__');
    await expect(page.locator('#page-title')).toHaveText('Epic');
    expect(errors).toEqual([]);
  });

  test('unknown epic id shows a not-found state with a back link', async ({ page }) => {
    await page.goto('/v3');
    await page.evaluate(() => location.hash = '#/epic/__definitely_missing__');
    await expect(page.locator('.ed-empty')).toContainText('not found');
    await expect(page.locator('.ed-empty a[href="#/epics"]')).toBeVisible();
  });
});
