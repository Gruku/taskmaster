import { test, expect } from '@playwright/test';

const BASE = process.env.VIEWER_BASE || 'http://127.0.0.1:8765';

test.describe('dashboard', () => {
  test('mounts the briefing strip and bento', async ({ page }) => {
    await page.goto(`${BASE}/v3#/dashboard`);
    await expect(page.locator('.dash-briefing')).toBeVisible();
    await expect(page.locator('.dash-bento')).toBeVisible();
    await expect(page.locator('.dash-board')).toBeVisible();
  });

  test('seeds default layout with at least 10 widgets', async ({ page }) => {
    await page.goto(`${BASE}/v3#/dashboard`);
    const widgets = page.locator('.widget');
    await expect(widgets).toHaveCount(10, { timeout: 5000 });
  });

  test('edit toggle reveals add tiles and remove buttons', async ({ page }) => {
    await page.goto(`${BASE}/v3#/dashboard`);
    await page.locator('.dash-edit-toggle').click();
    await expect(page.locator('.dash-add-tile').first()).toBeVisible();
    await expect(page.locator('.widget__remove').first()).toBeVisible();
  });

  test('removing a widget persists across reload', async ({ page }) => {
    await page.goto(`${BASE}/v3#/dashboard`);
    await page.locator('.dash-edit-toggle').click();
    const before = await page.locator('.widget').count();
    await page.locator('.widget__remove').first().click();
    await page.waitForTimeout(300);
    const after = await page.locator('.widget').count();
    expect(after).toBe(before - 1);
    await page.reload();
    await expect(page.locator('.widget')).toHaveCount(after);
  });

  test('add tile picker adds a stale-tasks widget', async ({ page }) => {
    await page.goto(`${BASE}/v3#/dashboard`);
    await page.locator('.dash-edit-toggle').click();
    await page.locator('.dash-add-tile').first().click();
    await page.locator('.dash-picker__item:has-text("Stale tasks")').click();
    await expect(page.locator('[data-widget-type="stale-tasks"]').last()).toBeVisible();
  });
});
