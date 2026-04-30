import { test, expect } from '@playwright/test';

const BASE = process.env.VIEWER_BASE_URL || 'http://127.0.0.1:8765';

test.beforeEach(async ({ request }) => {
  await request.put(`${BASE}/api/viewer/prefs`, { data: { screens: { sessions: { view: 'A' } } } });
});

test.describe('Sessions screen', () => {
  test('route resolves and key DOM nodes mount', async ({ page }) => {
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));

    await page.goto('/v3#/sessions');
    await expect(page.locator('.sessions-page')).toBeVisible();
    await expect(page.locator('.sessions-topbar h2')).toHaveText('Sessions / Handovers');
    await expect(page.locator('[data-role=view-toggle] .seg')).toHaveCount(3);
    await expect(page.locator('[data-role=kinds] .sessions-kind-chip')).toHaveCount(3);
    await expect(page.locator('[data-role=new-note]')).toBeVisible();

    // No JS errors during initial mount.
    expect(errors).toEqual([]);
  });

  test('view toggle switches active segment', async ({ page }) => {
    await page.goto('/v3#/sessions');
    const segs = page.locator('[data-role=view-toggle] .seg');
    await segs.nth(1).click();
    await expect(segs.nth(1)).toHaveClass(/\bon\b/);
    await expect(segs.nth(0)).not.toHaveClass(/\bon\b/);
  });

  test('kind chips toggle visibility', async ({ page }) => {
    await page.goto('/v3#/sessions');
    const chip = page.locator('[data-role=kinds] [data-kind=recap]');
    await expect(chip).toHaveClass(/\bon\b/);
    await chip.click();
    await expect(chip).not.toHaveClass(/\bon\b/);
  });
});
