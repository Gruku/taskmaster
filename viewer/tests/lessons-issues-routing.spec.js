import { test, expect } from '@playwright/test';

test('navigating lessons → issues → lessons via hash works', async ({ page }) => {
  await page.goto('/v3/#/lessons');
  await expect(page.locator('.lessons')).toBeVisible();

  await page.evaluate(() => location.hash = '#/issues');
  await expect(page.locator('.issues')).toBeVisible();

  await page.evaluate(() => location.hash = '#/lessons');
  await expect(page.locator('.lessons')).toBeVisible();
});

test('clicking an issue task pill navigates to task detail', async ({ page }) => {
  await page.goto('/v3/#/issues');
  const pill = page.locator('.issue-card__task-pill').first();
  if (await pill.count() === 0) test.skip();
  const id = (await pill.textContent()).trim();
  await pill.click();
  await expect(page).toHaveURL(new RegExp(`#/task/${id}`));
});
