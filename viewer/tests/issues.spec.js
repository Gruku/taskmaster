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

test('issue card surfaces all §3.14 elements', async ({ page }) => {
  await page.goto('/v3/#/issues');
  const card = page.locator('.issue-card').first();
  // severity hexagon glyph
  await expect(card.locator('.sev-glyph svg')).toBeVisible();
  // console-style location
  await expect(card.locator('.issue-card__location')).toBeVisible();
  await expect(card.locator('.issue-card__location-num')).toBeVisible();
  // italic-serif symptom
  const symptomFont = await card.locator('.issue-card__symptom').evaluate(el => getComputedStyle(el).fontStyle);
  expect(symptomFont).toBe('italic');
  // aging bar
  await expect(card.locator('.aging-bar')).toBeVisible();
  await expect(card.locator('.aging-bar__chip')).toHaveText(/^(Fresh|Aging|Stale)$/);
});

test('blocks chip appears when issue blocks non-done tasks', async ({ page }) => {
  await page.goto('/v3/#/issues');
  const chip = page.locator('.issue-card__blocks').first();
  if (await chip.count() === 0) test.skip();
  await expect(chip).toHaveText(/⊘ blocks \d+/);
});
