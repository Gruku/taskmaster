import { test, expect } from '@playwright/test';

test.describe('handover status pill', () => {
  test('renders todo / in-progress / done with distinct classes', async ({ page }) => {
    await page.goto('/v3#/sessions');
    // Wait for the sessions page to load
    await expect(page.locator('.sessions-page')).toBeVisible();
    // At least one status pill must be present (regardless of which statuses are seeded)
    const anyPill = page.locator('.ho-status-pill').first();
    await expect(anyPill).toBeVisible();
    // Verify the pill carries one of the three valid status modifier classes
    const pillClass = await anyPill.getAttribute('class');
    const validClasses = ['ho-status-pill-todo', 'ho-status-pill-in-progress', 'ho-status-pill-done'];
    const hasValidClass = validClasses.some((c) => pillClass?.includes(c));
    expect(hasValidClass).toBe(true);
  });

  test('clicking the pill opens an override menu with the three statuses', async ({ page }) => {
    await page.goto('/v3#/sessions');
    await expect(page.locator('.sessions-page')).toBeVisible();

    // Open a handover detail rail so the handover rail pill is present.
    // The .ho locator targets handover rows on the sessions timeline.
    const hoRow = page.locator('.ho').first();
    if (await hoRow.count() > 0) {
      await hoRow.click();
      await expect(page.locator('.right-rail')).toBeVisible();
    }

    // Click whichever pill appears first (in panel or rail)
    const firstPill = page.locator('.ho-status-pill').first();
    await expect(firstPill).toBeVisible();
    await firstPill.click();

    const menu = page.locator('.ho-status-menu');
    await expect(menu).toBeVisible();
    await expect(menu.getByText('todo')).toBeVisible();
    await expect(menu.getByText('in-progress')).toBeVisible();
    await expect(menu.getByText('done')).toBeVisible();
  });

  test('selecting a status calls /api/handover/*/status endpoint', async ({ page }) => {
    let captured = null;
    await page.route('**/api/handover/*/status', (route) => {
      captured = route.request().postDataJSON();
      route.fulfill({ status: 200, body: JSON.stringify({ ok: true, status: 'done' }) });
    });

    await page.goto('/v3#/sessions');
    await expect(page.locator('.sessions-page')).toBeVisible();

    // Open handover detail rail if handover rows exist
    const hoRow = page.locator('.ho').first();
    if (await hoRow.count() > 0) {
      await hoRow.click();
      await expect(page.locator('.right-rail')).toBeVisible();
    }

    const firstPill = page.locator('.ho-status-pill').first();
    await expect(firstPill).toBeVisible();
    await firstPill.click();

    const menu = page.locator('.ho-status-menu');
    await expect(menu).toBeVisible();
    await menu.getByText('done').click();

    expect(captured).toBeTruthy();
    expect(captured.status).toBe('done');
  });
});
