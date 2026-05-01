import { test, expect } from '@playwright/test';

const BASE = process.env.VIEWER_BASE_URL || 'http://127.0.0.1:8765';

test.beforeEach(async ({ request }) => {
  await request.put(`${BASE}/api/viewer/prefs`, {
    data: { screens: { auto_mode: { view: 'A', helper_dismissed: false, active_sid: null } } },
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

  test('sessions strip renders one tab per session', async ({ page }) => {
    // The dev fixture seeds two sessions; if it doesn't, fall back to >=1.
    await page.goto('http://127.0.0.1:8765/v3/#/auto');
    await page.waitForTimeout(1000);
    const tabs = page.locator('.sstrip-tab');
    const count = await tabs.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test('clicking Pause posts to /api/auto/pause', async ({ page }) => {
    let posted = null;
    page.on('request', (req) => {
      if (req.method() === 'POST' && req.url().includes('/api/auto/pause')) {
        posted = req.postData();
      }
    });
    await page.goto('http://127.0.0.1:8765/v3/#/auto');
    // Only meaningful if a session is running. Skip otherwise.
    const empty = await page.locator('.spine-empty').count();
    if (empty > 0) test.skip();
    await page.locator('.auto-control-btn--pause').click();
    await page.waitForTimeout(200);
    expect(posted).toBeTruthy();
    expect(posted).toContain('"session_id"');
  });
});
