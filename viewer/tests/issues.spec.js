import { test, expect } from '@playwright/test';

const BASE = 'http://127.0.0.1:8765';

test.beforeEach(async ({ request }) => {
  // Reset issues prefs slice so view toggle tests don't bleed.
  await request.put(`${BASE}/api/viewer/prefs`, {
    data: { screens: { issues: { view: 'A' } } },
  });
});

test('issues screen renders Investigating + Open columns', async ({ page }) => {
  await page.goto('/v3/#/issues');
  await expect(page.locator('.issues')).toBeVisible();
  await expect(page.locator('.issues__column-header').filter({ hasText: 'Investigating' })).toBeVisible();
  await expect(page.locator('.issues__column-header').filter({ hasText: 'Open' })).toBeVisible();
});

test('issue card shows severity word, not P0/P1', async ({ page }) => {
  await page.goto('/v3/#/issues');
  const sevChips = page.locator('.issue-card__sev-chip');
  const count = await sevChips.count();
  for (let i = 0; i < count; i++) {
    const text = await sevChips.nth(i).textContent();
    expect(text.trim()).toMatch(/^(Critical|High|Medium|Low)$/);
  }
});

test('repro block expands on click', async ({ page }) => {
  await page.goto('/v3/#/issues');
  const repro = page.locator('.issue-card__repro').first();
  await expect(repro).toBeVisible();
  await expect(repro).not.toHaveAttribute('open', '');
  await repro.locator('summary').click();
  await expect(repro).toHaveAttribute('open', '');
});

test('resolved shelf collapsed by default and expandable', async ({ page }) => {
  await page.goto('/v3/#/issues');
  const list = page.locator('.issues__resolved-list');
  await expect(list).toBeHidden();
  await page.locator('.issues__resolved-header').click();
  await expect(list).toBeVisible();
});
