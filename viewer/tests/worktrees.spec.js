import { test, expect } from '@playwright/test';

test.describe('Project Structure (#/worktrees)', () => {
  test('worktrees screen mounts', async ({ page }) => {
    await page.goto('/v3#/worktrees');
    await page.waitForSelector('.ws-page', { timeout: 5000 });
    // Either the grid renders cards or the empty state — both are valid.
    const cards = await page.locator('.ws-card').count();
    const empty = await page.locator('.ws-empty').count();
    expect(cards + empty).toBeGreaterThan(0);
  });
});
