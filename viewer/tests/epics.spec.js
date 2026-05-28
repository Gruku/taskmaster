// plugins/taskmaster/viewer/tests/epics.spec.js
import { test, expect } from '@playwright/test';

test.describe('Epics list', () => {
  test('route #/epics resolves and renders the list container', async ({ page }) => {
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    await page.goto('/v3');
    await page.evaluate(() => location.hash = '#/epics');
    await expect(page.locator('#page-title')).toHaveText('Epics');
    await expect(page.locator('.epics')).toBeVisible();
    await expect(page.locator('.sidebar-link[data-key="epics"]')).toHaveClass(/active/);
    expect(errors).toEqual([]);
  });
});
