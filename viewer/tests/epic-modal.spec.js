// plugins/taskmaster/viewer/tests/epic-modal.spec.js
import { test, expect } from '@playwright/test';

test('modal mode: the ↗ on an epic filter chip opens the epic modal', async ({ page }) => {
  await page.evaluate(() => {}); // no-op to keep structure parallel
  await page.goto('/v3');
  // ensure modal mode
  await page.evaluate(async () => {
    await fetch('/api/viewer/prefs', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ui: { detail_view_mode: 'modal' } }) });
  });
  await page.evaluate(() => location.hash = '#/kanban');
  const open = page.locator('.kanban-epic-chip__open').first();
  await open.waitFor();
  await open.click();
  await expect(page.locator('.dm-overlay')).toBeVisible();
  await expect(page.locator('.dm-overlay .ed-root')).toBeVisible(); // epic detail component
  // Guard double-history bug: one goBack should fully close the modal and
  // return to the kanban board. If openDetail fires twice, the first goBack
  // only pops one entry and the overlay stays visible.
  await page.goBack();
  await expect(page.locator('.dm-overlay')).toHaveCount(0);
  await expect(page).toHaveURL(/#\/kanban$/);
});
