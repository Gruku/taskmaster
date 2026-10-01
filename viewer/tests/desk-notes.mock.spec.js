// User intent: Dashboard notes read as coloured paper stickers — warm paper for the user, cool paper for Claude,
// dark ink on both — and they look the same in the dark and the light theme.
import { test, expect } from '@playwright/test';
import { mockApi, unmockedWrites } from './mock-api.js';
import { NOTES } from './mock-fixtures.js';

test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

const PAPER_USER = 'rgb(255, 203, 138)';     // --pastel-orange
const PAPER_CLAUDE = 'rgb(181, 194, 238)';   // --pastel-signature
const INK = 'rgb(13, 13, 12)';               // ground-0 dark = ground-100 light
const INK_SOFT = 'rgb(77, 76, 72)';          // ground-30 dark = ground-50 light

for (const theme of ['dark', 'light']) {
  test(`${theme}: user and Claude notes are coloured paper with dark ink`, async ({ page }) => {
    await mockApi(page, { '/api/viewer/prefs': { theme, ui: {}, screens: {} }, '/api/notes': NOTES });
    await page.goto('/#/dashboard');
    const user = page.locator('.dk-note--user').first();
    const claude = page.locator('.dk-note--claude').first();
    await expect(user).toHaveCSS('background-color', PAPER_USER);
    await expect(claude).toHaveCSS('background-color', PAPER_CLAUDE);
    for (const note of [user, claude]) {
      await expect(note.locator('.dk-note__body')).toHaveCSS('color', INK);
      await expect(note.locator('.dk-note__body p').first()).toHaveCSS('color', INK);
      await expect(note.locator('.dk-note__who')).toHaveCSS('color', INK_SOFT);
      await expect(note).toHaveCSS('box-shadow', 'none');
    }
    // Hover never moves the note and never repaints the paper.
    const tilt = await user.evaluate((el) => getComputedStyle(el).transform);
    await user.hover();
    await expect(user).toHaveCSS('background-color', PAPER_USER);
    expect(await user.evaluate((el) => getComputedStyle(el).transform)).toBe(tilt);
    // The composer is a blank slot, not a sheet of paper.
    await expect(page.locator('.dk-composer')).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  });
}
