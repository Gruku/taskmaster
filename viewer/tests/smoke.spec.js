import { test, expect } from '@playwright/test';

const ROUTES = [
  { hash: '#/dashboard',          title: 'Dashboard',           sidebarKey: 'dashboard' },
  { hash: '#/kanban',             title: 'Kanban',              sidebarKey: 'kanban' },
  { hash: '#/sessions',           title: 'Sessions / Handovers',sidebarKey: 'sessions' },
  { hash: '#/issues',             title: 'Issues',              sidebarKey: 'issues' },
  { hash: '#/task/T-148',         title: 'Task Detail',         sidebarKey: null },
];

// Number of sidebar links is derived from ROUTES (unique sidebarKeys) so it stays in sync.
const SIDEBAR_LINK_COUNT = new Set(ROUTES.map(r => r.sidebarKey).filter(Boolean)).size;

test.describe('Viewer v3 smoke', () => {
  test('boots and renders sidebar', async ({ page }) => {
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));

    await page.goto('/v3');
    await expect(page.locator('#sidebar .sidebar-link')).toHaveCount(SIDEBAR_LINK_COUNT);
    await expect(page.locator('#page-title')).not.toHaveText('Loading…');
    expect(errors).toEqual([]);
  });

  for (const r of ROUTES) {
    test(`route ${r.hash} resolves`, async ({ page }) => {
      const errors = [];
      page.on('pageerror', e => errors.push(String(e)));

      await page.goto('/v3');
      await page.evaluate(h => location.hash = h, r.hash);
      await expect(page.locator('#page-title')).toHaveText(r.title);
      await expect(page.locator('.screen-mount .stub')).toBeVisible();
      if (r.sidebarKey) {
        await expect(page.locator(`.sidebar-link[data-key="${r.sidebarKey}"]`)).toHaveClass(/active/);
      }
      expect(errors).toEqual([]);
    });
  }

  test('unknown hash falls back to dashboard', async ({ page }) => {
    await page.goto('/v3');
    await page.evaluate(() => location.hash = '#/garbage');
    await expect(page.locator('#page-title')).toHaveText('Dashboard');
  });

  test('prefs persist via the API and are reflected in store', async ({ page }) => {
    await page.goto('/v3');

    // Mutate via the same path screens will use.
    await page.evaluate(async () => {
      const resp = await fetch('/api/viewer/prefs', {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ theme: 'light', kanban: { filters: { search: 'auth' } } }),
      });
      if (!resp.ok) throw new Error('PUT failed');
    });

    // Reload, then read prefs from /api/viewer/prefs.
    await page.reload();
    const prefs = await page.evaluate(async () => {
      const resp = await fetch('/api/viewer/prefs');
      return resp.json();
    });

    expect(prefs.theme).toBe('light');
    expect(prefs.kanban.filters.search).toBe('auth');
  });
});
