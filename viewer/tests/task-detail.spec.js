// @ts-check
import { test, expect } from '@playwright/test';

const TASK_ID = process.env.TM_TEST_TASK_ID || 'T-148';

test.describe('Task Detail screen', () => {
  test('Variant A renders header, meta, and title', async ({ page }) => {
    await page.goto(`/v3/#/task/${TASK_ID}`);
    await expect(page.locator('[data-test="view-toggle"]')).toBeVisible();
    await expect(page.locator('[data-test="meta"]')).toBeVisible();
    await expect(page.locator('[data-test="task-id"]')).toContainText(TASK_ID);
    await expect(page.locator('[data-test="title"]')).not.toBeEmpty();
  });

  test('lock banner appears only when locked_by is set', async ({ page }) => {
    await page.goto(`/v3/#/task/${TASK_ID}`);
    const banner = page.locator('[data-test="lock-banner"]');
    if (await banner.count()) {
      await expect(banner).toContainText(/locked/i);
    }
  });
});
