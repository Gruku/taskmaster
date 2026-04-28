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

  test('chip row contains status, priority, size, epic', async ({ page }) => {
    await page.goto(`/v3/#/task/${TASK_ID}`);
    const chips = page.locator('[data-test="chips"]');
    await expect(chips).toBeVisible();
    await expect(chips.locator('.td-status-pill')).toBeVisible();
    await expect(chips.locator('.td-pri-pill')).toBeVisible();
    await expect(chips.locator('.td-size-chip')).toBeVisible();
    await expect(chips.locator('.td-epic-chip')).toBeVisible();
  });

  test('document sections render description and notes', async ({ page }) => {
    await page.goto(`/v3/#/task/${TASK_ID}`);
    await expect(page.locator('[data-test="sec-spec"]')).toBeVisible();
    await expect(page.locator('[data-test="sec-notes"]')).toBeVisible();
  });

  test('Variant B renders compact head, graph frame, and tabs', async ({ page }) => {
    await page.goto(`/v3/#/task/${TASK_ID}?view=B`);
    await page.request.put('/api/viewer/prefs', { data: { screens: { task_detail: { view: 'B' } } } });
    await page.reload();
    await expect(page.locator('[data-test="compact-head"]')).toBeVisible();
    await expect(page.locator('[data-test="graph-frame"]')).toBeVisible();
    await expect(page.locator('[data-test="tabs"]')).toBeVisible();
  });

  test('Variant B graph SVG renders at least one center node', async ({ page }) => {
    await page.request.put('/api/viewer/prefs', { data: { screens: { task_detail: { view: 'B' } } } });
    await page.goto(`/v3/#/task/${TASK_ID}`);
    await expect(page.locator('[data-test="graph-svg"]')).toBeVisible();
    const centerNodes = page.locator('[data-test="graph-svg"] .node-rect.center');
    await expect(centerNodes).toHaveCount(1);
  });

  test('Variant B tabs switch and render Anchors panel', async ({ page }) => {
    await page.request.put('/api/viewer/prefs', { data: { screens: { task_detail: { view: 'B' } } } });
    await page.goto(`/v3/#/task/${TASK_ID}`);
    await page.locator('[data-test="tabs"] [data-tab="anchors"]').click();
    await expect(page.locator('[data-tab-panel="anchors"]')).toHaveClass(/on/);
    await expect(page.locator('[data-tab-panel="anchors"] .td-anchor-pill').first()).toBeVisible();
  });

  test('right rail panels match between Variant A and Variant B', async ({ page }) => {
    await page.request.put('/api/viewer/prefs', { data: { screens: { task_detail: { view: 'A' } } } });
    await page.goto(`/v3/#/task/${TASK_ID}`);
    const aPanels = await page.locator('[data-test="rail"] .td-panel').count();

    await page.request.put('/api/viewer/prefs', { data: { screens: { task_detail: { view: 'B' } } } });
    await page.reload();
    const bPanels = await page.locator('[data-test="rail"] .td-panel').count();
    expect(aPanels).toBe(bPanels);
    expect(aPanels).toBeGreaterThanOrEqual(6);
  });
});
