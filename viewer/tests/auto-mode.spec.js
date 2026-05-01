import { test, expect } from '@playwright/test';

const BASE = process.env.VIEWER_BASE_URL || 'http://127.0.0.1:8765';

test.beforeEach(async ({ request }) => {
  await request.put(`${BASE}/api/viewer/prefs`, {
    data: { screens: { auto_mode: { view: 'A', helper_dismissed: false } } },
  });
});

test.describe('Auto Mode page', () => {
  test('renders Quest Spine by default', async ({ page }) => {
    await page.goto('http://127.0.0.1:8765/v3/#/auto');
    await expect(page.locator('.auto-title')).toHaveText('Auto Mode');
    // 5 spine nodes for AUTO_STAGES (or empty state when no session)
    const empty = page.locator('.spine-empty');
    const nodes = page.locator('.spine-node');
    const ok = (await empty.count()) > 0 || (await nodes.count()) === 5;
    expect(ok).toBeTruthy();
  });

  test('Spine|Log toggle switches center view', async ({ page }) => {
    await page.goto('http://127.0.0.1:8765/v3/#/auto');
    await page.locator('.auto-toggle-seg[data-view="B"]').click();
    await expect(page.locator('.auto-toggle-seg[data-view="B"]')).toHaveClass(/on/);
    // Either flight log or empty placeholder
    const present = await page.locator('.flog-wrap, .flog-empty').count();
    expect(present).toBeGreaterThan(0);
  });

  test('Pause and Stop buttons are present', async ({ page }) => {
    await page.goto('http://127.0.0.1:8765/v3/#/auto');
    await expect(page.locator('.auto-control-btn--pause')).toBeVisible();
    await expect(page.locator('.auto-control-btn--stop')).toBeVisible();
  });
});
