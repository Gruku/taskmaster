import {test, expect} from '@playwright/test';

test('only the live unexpired claim displays a lock banner', async ({page}) => {
  await page.goto('/#/task/board-004');
  await expect(page.locator('[data-test="lock-banner"]')).toBeVisible();
  await expect(page.locator('[data-test="lock-banner"]')).toContainText(' until ');
  for (const id of ['board-005', 'board-006', 'board-007']) {
    const detail = await (await page.request.get(`/api/task/${id}/detail`)).json();
    expect(detail.claim.expired).toBe(true);
    expect(detail.task).not.toHaveProperty('locked_by');
    await page.goto(`/#/task/${id}`);
    await expect(page.locator('.if-wrap[data-key="title"]')).toBeVisible();
    await expect(page.locator('[data-test="lock-banner"]')).toHaveCount(0);
  }
});
