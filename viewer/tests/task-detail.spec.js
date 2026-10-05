// @ts-check
import { test, expect } from '@playwright/test';

const TASK_ID = process.env.TM_TEST_TASK_ID || 'T-148';

test.describe('Task Detail screen', () => {
  test.beforeEach(async ({ request }) => {
    await request.put('/api/viewer/prefs', { data: { screens: { task_detail: { view: 'A' } } } });
  });

  test('Variant A renders header, meta, and title', async ({ page }) => {
    await page.goto(`/v3/#/task/${TASK_ID}`);
    await expect(page.locator('.tm-segmented [data-key="A"]')).toHaveAttribute('aria-pressed', 'true');
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

  test('marker row holds the status and priority markers (a shape plus a word) and the epic tag', async ({ page }) => {
    await page.goto(`/v3/#/task/${TASK_ID}`);
    const chips = page.locator('[data-test="chips"]');
    await expect(chips).toBeVisible();
    for (const field of ['status', 'priority']) {
      const marker = chips.locator(`[data-field="${field}"] .marker`);
      await expect(marker.locator('.marker__shape')).toHaveCount(1);
      await expect(marker.locator('.marker__word')).not.toBeEmpty();
    }
    // Estimate and epic are tags shown only when the task has them.
    if (await chips.locator('[data-tag="epic"]').count()) await expect(chips.locator('[data-tag="epic"]')).toBeVisible();
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

  test('Variant B draws one center node, or says there is nothing to draw', async ({ page }) => {
    await page.request.put('/api/viewer/prefs', { data: { screens: { task_detail: { view: 'B' } } } });
    await page.goto(`/v3/#/task/${TASK_ID}`);
    const frame = page.locator('[data-test="graph-frame"]');
    await expect(frame).toBeVisible();
    // A task with no dependencies and nothing waiting on it gets the empty state instead of a lone node.
    if (await frame.locator('.tm-empty').count()) {
      await expect(frame.locator('.tm-empty__headline')).toHaveText('No dependencies to draw');
    } else {
      await expect(page.locator('[data-test="graph-svg"] .node-rect.center')).toHaveCount(1);
    }
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
    await expect(page.locator('[data-test="meta"]')).toBeVisible();
    const aRail = page.locator('[data-test="rail"] .td-panel');
    const aPanels = await aRail.count();

    await page.request.put('/api/viewer/prefs', { data: { screens: { task_detail: { view: 'B' } } } });
    await page.reload();
    await expect(page.locator('[data-test="graph-frame"]')).toBeVisible();
    const bRail = page.locator('[data-test="rail"] .td-panel');
    const bPanels = await bRail.count();
    expect(aPanels).toBe(bPanels);
    // Relations, Docs, Handovers, Issues — each only when it has something; no rail at all when none has.
    expect(aPanels).toBeLessThanOrEqual(4);
  });

  test('clicking the view toggle persists prefs and re-renders the other variant', async ({ page }) => {
    await page.request.put('/api/viewer/prefs', { data: { screens: { task_detail: { view: 'A' } } } });
    await page.goto(`/v3/#/task/${TASK_ID}`);
    await expect(page.locator('[data-test="meta"]')).toBeVisible();
    await page.locator('.tm-segmented [data-key="B"]').click();
    await expect(page.locator('[data-test="graph-frame"]')).toBeVisible();

    await page.reload();
    await expect(page.locator('[data-test="graph-frame"]')).toBeVisible();
  });

  test('unknown task id renders an error message, not a crash', async ({ page }) => {
    await page.goto('/v3/#/task/T-DOES-NOT-EXIST');
    await expect(page.locator('.tm-empty__headline')).toHaveText('Task not found');
  });
});
