// plugins/taskmaster/viewer/tests/settings.spec.js
import { test, expect } from '@playwright/test';

test.describe('Settings', () => {
  test('toggling detail view to Full persists across reload', async ({ page }) => {
    await page.goto('/v3');
    await page.evaluate(() => location.hash = '#/settings');
    await expect(page.locator('#page-title')).toHaveText('Settings');
    await page.locator('.set-detail-view input[value="full"]').check();
    // Persisted to the prefs API.
    await page.waitForTimeout(600); // debounced savePrefs
    await page.reload();
    const prefs = await page.evaluate(async () => (await fetch('/api/viewer/prefs')).json());
    expect(prefs.ui.detail_view_mode).toBe('full');
    await page.evaluate(() => location.hash = '#/settings');
    await expect(page.locator('.set-detail-view input[value="full"]')).toBeChecked();
    // Reset to modal so the suite stays clean.
    await page.evaluate(async () => {
      await fetch('/api/viewer/prefs', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ui: { detail_view_mode: 'modal' } }) });
    });
  });
});
