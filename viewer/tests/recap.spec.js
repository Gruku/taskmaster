import { test, expect } from '@playwright/test';

test.describe('Recap screen', () => {
  test('route resolves and renders empty state without console errors', async ({ page }) => {
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));

    await page.goto('/v3#/recap');
    await expect(page.locator('.recap-page')).toBeVisible({ timeout: 5000 });
    expect(errors).toEqual([]);
  });

  test('with seeded recap the hero renders title and 5 stat cells', async ({ page, request }) => {
    // Seed: write one handover and one recap via PUT /api/recap
    await request.put('/api/recap/SES-0001', {
      headers: { 'Content-Type': 'application/json' },
      data: {
        frontmatter: {
          snapshot_before: 'SNAP-0000', snapshot_after: 'SNAP-0001',
          generator: 'claude', generated_at: '2026-04-26T16:48:00Z', token_cost: 1840,
        },
        title: 'Stitched the worktree review gate',
        what_happened: 'Started in worktree-shadow.',
        what_landed: 'Three closed.',
        whats_next: 'Rebase tomorrow.',
      },
    });
    await page.goto('/v3#/recap/SES-0001');
    await expect(page.locator('.recap-hero-title')).toContainText('Stitched');
    await expect(page.locator('.recap-stat')).toHaveCount(5);
    await expect(page.locator('.receipts-grid .rcard')).toHaveCount(4);
  });

  test('clicking edit reveals three textareas + save/cancel/regenerate', async ({ page }) => {
    await page.goto('/v3#/recap/SES-0001');
    await page.locator('[data-role=edit]').click();
    await expect(page.locator('[data-role=ed-what-happened]')).toBeVisible();
    await expect(page.locator('[data-role=ed-what-landed]')).toBeVisible();
    await expect(page.locator('[data-role=ed-whats-next]')).toBeVisible();
    await expect(page.locator('[data-role=save]')).toBeVisible();
    await expect(page.locator('[data-role=cancel]')).toBeVisible();
    await expect(page.locator('[data-role=regenerate]')).toBeVisible();
  });
});
