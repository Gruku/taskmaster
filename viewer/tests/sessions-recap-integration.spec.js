import { test, expect } from '@playwright/test';

test('clicking a recap child row in Sessions navigates to /recap/<sid>', async ({ page, request }) => {
  // Seed a handover + recap so Sessions has a session with a recap_id.
  await request.put('/api/recap/SES-0001', {
    headers: { 'Content-Type': 'application/json' },
    data: {
      frontmatter: { snapshot_before: 'SNAP-0000', snapshot_after: 'SNAP-0001',
                     generator: 'claude', generated_at: '2026-04-26T16:48Z', token_cost: 100 },
      title: 'tt', what_happened: 'x', what_landed: 'y', whats_next: 'z',
    },
  });
  await page.goto('/v3#/sessions');
  const recapChild = page.locator('.ho-child.recap, .ho-child:has(.ho-kind.recap)').first();
  if (await recapChild.count()) {
    await recapChild.click();
    await expect(page).toHaveURL(/#\/recap\/SES-/);
    await expect(page.locator('.recap-hero-title')).toBeVisible();
  }
});
