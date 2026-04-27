import { test, expect } from '@playwright/test';

const ROUTES = [
  { hash: '#/dashboard',          title: 'Dashboard',           sidebarKey: 'dashboard' },
  { hash: '#/kanban',             title: 'Kanban',              sidebarKey: 'kanban' },
  { hash: '#/sessions',           title: 'Sessions / Handovers',sidebarKey: 'sessions' },
  { hash: '#/lessons',            title: 'Lessons',             sidebarKey: 'lessons' },
  { hash: '#/issues',             title: 'Issues',              sidebarKey: 'issues' },
  { hash: '#/auto',               title: 'Auto Mode',           sidebarKey: 'auto_mode' },
  { hash: '#/recap',              title: 'Recap',               sidebarKey: 'recap' },
  { hash: '#/recap/SES-0184',     title: 'Recap',               sidebarKey: 'recap' },
  { hash: '#/task/T-148',         title: 'Task Detail',         sidebarKey: null },
];

test.describe('Viewer v3 smoke', () => {
  test('boots and renders sidebar', async ({ page }) => {
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));

    await page.goto('/v3');
    await expect(page.locator('#sidebar .sidebar-link')).toHaveCount(7);
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
});
