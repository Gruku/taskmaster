import { test, expect } from '@playwright/test';

const BASE = 'http://127.0.0.1:8765';

test.beforeEach(async ({ request }) => {
  // Reset lessons prefs slice so view toggle tests don't bleed into shelf tests.
  await request.put(`${BASE}/api/viewer/prefs`, {
    data: { screens: { lessons: { view: 'A' } } },
  });
});

test('lessons screen renders three shelves', async ({ page }) => {
  await page.goto('/v3/#/lessons');
  await expect(page.locator('.lessons')).toBeVisible();
  await expect(page.locator('.lessons-shelf--core')).toBeVisible();
  await expect(page.locator('.lessons-shelf--active')).toBeVisible();
  await expect(page.locator('.lessons-shelf--retired')).toBeVisible();
});

test('reinforce button bumps count via API', async ({ page }) => {
  await page.goto('/v3/#/lessons');
  const card = page.locator('.lesson-card').first();
  await card.hover();
  const initialCount = await card.locator('.sparkline-count').textContent();
  await card.locator('.lesson-card__reinforce').click();
  // is-fired is applied before the render() microtask replaces DOM
  await expect(card.locator('.lesson-card__reinforce.is-fired')).toBeVisible({ timeout: 3000 });
  await expect(card.locator('.lesson-card__reinforce')).toHaveText(/Reinforced now/);
  // Wait for re-render to complete, then check updated count on the new first card
  await page.waitForTimeout(500);
  const newCount = await page.locator('.lesson-card').first().locator('.sparkline-count').textContent();
  expect(parseInt(newCount, 10)).toBeGreaterThan(parseInt(initialCount, 10));
});

test('view toggle persists to prefs', async ({ page }) => {
  await page.goto('/v3/#/lessons');
  await page.locator('.lessons__view-toggle button[data-view="B"]').click();
  await expect(page.locator('.lessons__view-toggle button[data-view="B"]')).toHaveClass(/is-active/);
  // Wait for the 400ms prefs debounce to flush before reload
  await page.waitForTimeout(600);
  await page.reload();
  await expect(page.locator('.lessons__view-toggle button[data-view="B"]')).toHaveClass(/is-active/);
});

test('lesson card surfaces all §3.13 elements', async ({ page }) => {
  await page.goto('/v3/#/lessons');
  const card = page.locator('.lesson-card').first();
  // kind icon
  await expect(card.locator('.lesson-card__kind')).toBeVisible();
  await expect(card.locator('.lesson-card__kind')).toHaveText(/^(⚠|◇|⊘)$/);
  // anchor pills with When: label
  await expect(card.locator('.anchor-pills__label')).toHaveText('When:');
  // first_seen caption
  await expect(card.locator('.lesson-card__since')).toBeVisible();
  // active signal: sparkline pill with count
  await expect(card.locator('.sparkline-pill .sparkline-count')).toBeVisible();
  // passive signal: dot meter
  await expect(card.locator('.dot-meter')).toBeVisible();
});

test('core shelf shows gold styling on ID', async ({ page }) => {
  await page.goto('/v3/#/lessons');
  const coreCard = page.locator('.lesson-card--core').first();
  if (await coreCard.count() === 0) test.skip(); // no core lessons in fixture
  const idColor = await coreCard.locator('.lesson-card__id').evaluate(el => getComputedStyle(el).color);
  // Gold-ish: high red, medium green, low blue
  const m = idColor.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
  expect(Number(m[1])).toBeGreaterThan(150);
});
