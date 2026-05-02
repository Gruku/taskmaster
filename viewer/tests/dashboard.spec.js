import { test, expect } from '@playwright/test';

const BASE = process.env.VIEWER_BASE || 'http://127.0.0.1:8765';

test.describe('dashboard', () => {
  test.beforeEach(async ({ request }) => {
    // Reset dashboard layout so each test starts from the seeded default.
    await request.put(`${BASE}/api/viewer/prefs`, {
      data: { dashboard: { layout: [] } },
    });
  });

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

  test('briefing strip renders phase pips with active variant', async ({ page }) => {
    await page.goto(`${BASE}/v3#/dashboard`);
    // Pips are populated asynchronously by briefing-strip refresh() after the
    // recent-events fetch resolves. Wait for the first pip before counting.
    await expect(page.locator('.dash-briefing__pip').first()).toBeAttached();
    const pips = page.locator('.dash-briefing__pip');
    expect(await pips.count()).toBeGreaterThan(0);
    // Exactly one pip is the active variant (per phase invariant).
    await expect(page.locator('.dash-briefing__pip--active')).toHaveCount(1);
    // Active pip has tooltip combining phase name + status.
    const activeTitle = await page.locator('.dash-briefing__pip--active').getAttribute('title');
    expect(activeTitle).toMatch(/ · active$/);
  });

  test('auto-mode session timer uses compact relative-time format', async ({ page }) => {
    await page.goto(`${BASE}/v3#/dashboard`);
    // The strip element is always created but the session-time child is only
    // appended once auto state loads. If the fixture has no auto state, skip.
    const sess = page.locator('.kanban-strip-session-time').first();
    try {
      await sess.waitFor({ state: 'attached', timeout: 2000 });
    } catch {
      test.skip();
      return;
    }
    // Compact format from formatTimeInStatus: "<n>m" / "<n>h" / "<n>d" — never HH:MM:SS.
    await expect(sess).toHaveText(/^running \d+[mhd]$/);
    // Tooltip exposes the full start timestamp for disambiguation.
    const title = await sess.getAttribute('title');
    expect(title).toBeTruthy();
  });
});
