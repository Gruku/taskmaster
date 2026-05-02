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
  // Topbar segmented control (tm-segmented uses data-key, not data-view)
  await page.locator('.tm-segmented button[data-key="B"]').click();
  await expect(page.locator('.tm-segmented button[data-key="B"]')).toHaveClass(/on|is-active/);
  // Wait for the 400ms prefs debounce to flush before reload
  await page.waitForTimeout(600);
  await page.reload();
  await expect(page.locator('.tm-segmented button[data-key="B"]')).toHaveClass(/on|is-active/);
});

test('lesson card surfaces all §3.13 elements', async ({ page }) => {
  await page.goto('/v3/#/lessons');
  const card = page.locator('.lesson-card').first();
  // kind icon
  await expect(card.locator('.lesson-card__kind')).toBeVisible();
  await expect(card.locator('.lesson-card__kind')).toHaveText(/^(⚠|◇|⊘)$/);
  // anchor pills with When: label
  await expect(card.locator('.anchor-pills__label')).toHaveText('When:');
  // active signal: sparkline pill with count, in head row
  await expect(card.locator('.lesson-card__head .sparkline-pill .sparkline-count')).toBeVisible();
  // passive signal: dot meter, on anchors row
  await expect(card.locator('.lesson-card__anchors-row .dot-meter')).toBeVisible();
  // foot row: reinforce-count
  await expect(card.locator('.lesson-card__foot .lesson-card__fired')).toBeVisible();
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

test('lesson card click navigates to lesson detail', async ({ page }) => {
  await page.goto('/v3/#/lessons');
  const card = page.locator('.lesson-card').first();
  const id = await card.getAttribute('data-lesson-id');
  await card.click();
  await page.waitForURL(`**/#/lesson/${id}`);
  await expect(page.locator('.lesson-detail .ld-title')).toBeVisible();
  await expect(page.locator('.lesson-detail .ld-id')).toHaveText(id);
});

test('lesson search filters cards by title', async ({ page }) => {
  await page.goto('/v3/#/lessons');
  await expect(page.locator('.lesson-card').first()).toBeVisible();
  const initial = await page.locator('.lesson-card').count();
  expect(initial).toBeGreaterThan(0);
  const firstTitle = (await page.locator('.lesson-card__title').first().textContent()).trim();
  // Use a distinctive substring of the first lesson's title
  const probe = firstTitle.split(/\s+/).find(w => w.length > 4) || firstTitle.slice(0, 5);
  await page.locator('.tm-search input').fill(probe);
  // Debounce 180ms
  await page.waitForTimeout(300);
  const filtered = await page.locator('.lesson-card').count();
  expect(filtered).toBeLessThanOrEqual(initial);
  expect(filtered).toBeGreaterThan(0);
  // Subcount reflects filter
  await expect(page.locator('.tm-subcount')).toHaveText(/of \d+ lessons/);
});

test('lesson detail back link returns to lessons list', async ({ page }) => {
  await page.goto('/v3/#/lesson/L-001');
  await expect(page.locator('.lesson-detail')).toBeVisible();
  await page.locator('.lesson-detail .ld-back').click();
  await page.waitForURL('**/#/lessons');
  await expect(page.locator('.lessons')).toBeVisible();
});
