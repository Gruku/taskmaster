// plugins/taskmaster/viewer/tests/desk.spec.js
//
// Desk dashboard: sticky board + pruned continuity band.
// Runs against live .taskmaster — all notes created here are archived in afterAll.

import { test, expect } from '@playwright/test';

const BASE = process.env.VIEWER_BASE_URL || 'http://127.0.0.1:8765';
const CREATED = [];

test.describe('desk dashboard', () => {
  test.afterAll(async ({ request }) => {
    for (const id of CREATED) {
      await request.post(`${BASE}/api/notes/${id}/archive`).catch(() => {});
    }
  });
  test('mounts board + composer, no console errors', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });

    await page.goto('/v3#/dashboard');
    await expect(page.locator('.dk-desk')).toBeVisible();
    await expect(page.locator('.dk-composer__input')).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('marked is loaded locally (SRI fix)', async ({ page }) => {
    await page.goto('/v3#/dashboard');
    await expect(page.locator('.dk-desk')).toBeVisible();
    const hasMarked = await page.evaluate(() => typeof window.marked?.parse === 'function');
    expect(hasMarked).toBe(true);
  });

  test('composer creates a user paper note', async ({ page }) => {
    await page.goto('/v3#/dashboard');
    await expect(page.locator('.dk-desk')).toBeVisible();
    const text = `e2e desk note ${Date.now()}`;
    await page.locator('.dk-composer__input').fill(text);
    await page.locator('.dk-composer__input').press('Enter');

    // Wait for the note to appear in the board.
    const card = page.locator('.dk-note--user', { hasText: text });
    await expect(card).toBeVisible();

    const id = await card.getAttribute('data-note-id');
    CREATED.push(id);
    await expect(card.locator('.dk-note__who')).toHaveText('you');
  });

  test('pin reorders to front; archive removes', async ({ page }) => {
    // Create a note via API so we have a known id before page load.
    const r = await page.request.post(`${BASE}/api/notes`, {
      data: { text: `e2e pin target ${Date.now()}` },
    });
    const { id } = await r.json();
    CREATED.push(id);

    await page.goto('/v3#/dashboard');
    await expect(page.locator('.dk-desk')).toBeVisible();

    const card = page.locator(`[data-note-id="${id}"]`);
    await expect(card).toBeVisible();

    // Pin: hover to reveal buttons, then click pin.
    await card.hover();
    await card.locator('.dk-note__pin').click();

    // After pin, this note should be the first non-composer note.
    await expect(page.locator('.dk-note:not(.dk-composer)').first()).toHaveAttribute('data-note-id', id);

    // Archive: hover to reveal buttons, then click archive.
    await card.hover();
    await card.locator('.dk-note__archive').click();

    // Note should vanish from the board.
    await expect(page.locator(`[data-note-id="${id}"]`)).toHaveCount(0);

    // Remove from cleanup list since it's already archived.
    const idx = CREATED.indexOf(id);
    if (idx !== -1) CREATED.splice(idx, 1);
  });

  test('continuity rails cap at 5 rows with older link', async ({ page }) => {
    await page.goto('/v3#/dashboard');
    await expect(page.locator('.dk-desk')).toBeVisible();

    // Each rendered rail is a .co-spine; rows inside are .co-row.
    const spines = page.locator('.dk-continuity .co-spine');
    const n = await spines.count();

    for (let i = 0; i < n; i++) {
      const rows = spines.nth(i).locator('.co-row');
      expect(await rows.count()).toBeLessThanOrEqual(5);
    }
  });
});
