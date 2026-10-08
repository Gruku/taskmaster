// User intent: the Ideas "Tags" filter must hold in a real browser what jsdom cannot show — forty tags searchable and
// scrolling inside the popover, the keyboard going from the search box to the choices and Escape back to Tags, one entry
// for "UX"/"ux", touch-sized on a phone, inside the viewport, and accessible with no shadow in both themes.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

test.beforeEach(async ({ page }) => { await page.emulateMedia({ reducedMotion: 'reduce' }); });
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

// There is no harness page: the filter is mounted through its module on a booted screen. window.__tags records every
// onChange call's keys (the last one wins).
async function mount(page, { theme = 'dark' } = {}) {
  await mockApi(page, { '/api/viewer/prefs': { theme, ui: {}, screens: {} } });
  await page.goto('/#/settings');
  await expect(page.locator('#sidebar .sidebar-link').first()).toBeVisible();
  await page.evaluate(async () => {
    const { tagFilter, collectTags } = await import('/js/components/tag-filter.js');
    const items = [
      ...Array.from({ length: 40 }, (_, i) => ({ tags: [`tag-${String(i + 1).padStart(2, '0')}`] })),
      { tags: ['UX'] },
      { tags: ['ux'] },
    ];
    window.__tags = null;
    const tf = tagFilter({ getTags: () => collectTags(items), onChange: (keys) => { window.__tags = keys; } });
    const box = document.createElement('div');
    box.id = 'tag-box';
    box.append(tf.el);
    document.getElementById('screen-mount').replaceChildren(box);
  });
  await expect(tags(page)).toBeVisible();
}

const tags = (page) => page.locator('#tag-box > .tag-filter');
const dialog = (page) => page.getByRole('dialog', { name: 'Filter by tag' });
const search = (page) => dialog(page).getByRole('searchbox', { name: 'Find a tag' });
const choice = (page, key) => dialog(page).locator(`.tag-filter__list input[type="checkbox"][value="${key}"]`);
const visibleNames = (page) => dialog(page).locator('.tag-filter__option:not([hidden]) .tag-filter__name').allTextContents();
const inViewport = (page) => dialog(page).evaluate((el) => {
  const r = el.getBoundingClientRect();
  return r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight;
});

test('forty tags: searchable, scrolls inside, keyboard from search to choices, Escape back to Tags', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mount(page);
  await tags(page).click();
  await expect(dialog(page)).toBeVisible();
  await expect(search(page)).toBeFocused();
  await expect(dialog(page).getByRole('checkbox')).toHaveCount(41);
  await expect(dialog(page).locator('.tag-filter__name', { hasText: /^ux$/i })).toHaveCount(1);
  expect(await dialog(page).locator('.tag-filter__list').evaluate((l) => l.scrollHeight > l.clientHeight)).toBe(true);
  expect(await inViewport(page)).toBe(true);

  await page.keyboard.type('3');
  const thirteen = ['tag-03', 'tag-13', 'tag-23', ...Array.from({ length: 10 }, (_, i) => `tag-3${i}`)];
  expect(await visibleNames(page)).toEqual(thirteen);

  await page.keyboard.press('ArrowDown');
  await expect(choice(page, 'tag-03')).toBeFocused();
  await page.keyboard.press('Space');
  await expect(choice(page, 'tag-03')).toBeChecked();
  expect(await page.evaluate(() => window.__tags)).toEqual(['tag-03']);
  await expect(tags(page).locator('.tag-filter__on')).toHaveText('· 1');
  await expect(tags(page)).toHaveAccessibleName('Tags, 1 selected');

  // tag-04 … tag-12 are hidden by the search, so the next stop is tag-13.
  await page.keyboard.press('ArrowDown');
  await expect(choice(page, 'tag-13')).toBeFocused();

  await page.keyboard.press('Escape');
  await expect(dialog(page)).toHaveCount(0);
  await expect(tags(page)).toBeFocused();

  await tags(page).click();
  await expect(search(page)).toBeFocused();
  await page.keyboard.type('zzz');
  await expect(dialog(page).locator('.tag-filter__none')).toHaveText('No tag matches “zzz”.');
  // The chosen tag is hidden by the search but still said on the button and released by Clear tags.
  await expect(tags(page).locator('.tag-filter__on')).toHaveText('· 1');
  await dialog(page).getByRole('button', { name: 'Clear tags' }).click();
  expect(await page.evaluate(() => window.__tags)).toEqual([]);
  await expect(tags(page)).toHaveAccessibleName('Tags');
  await expect(search(page)).toBeFocused();
});

test('the popover stays inside the viewport at 390×844', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mount(page);
  await tags(page).click();
  await expect(dialog(page)).toBeVisible();
  expect(await inViewport(page)).toBe(true);
  expect(await dialog(page).locator('.tag-filter__list').evaluate((l) => l.scrollHeight > l.clientHeight)).toBe(true);
});

test('at 390×844 every visible option, the search box and the Tags button are at least 44px tall', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mount(page);
  expect((await tags(page).boundingBox()).height).toBeGreaterThanOrEqual(44);
  await tags(page).click();
  await expect(dialog(page)).toBeVisible();
  expect((await search(page).boundingBox()).height).toBeGreaterThanOrEqual(44);
  const heights = await dialog(page).locator('.tag-filter__option:not([hidden])').evaluateAll((els) => els.map((el) => el.getBoundingClientRect().height));
  expect(heights).toHaveLength(41);
  for (const h of heights) expect(h).toBeGreaterThanOrEqual(44);
  await choice(page, 'tag-01').check();
  expect((await dialog(page).getByRole('button', { name: 'Clear tags' }).boundingBox()).height).toBeGreaterThanOrEqual(44);
});

for (const theme of ['dark', 'light']) {
  test(`${theme}: the open Tags popover has no shadow and no axe violation`, async ({ page }) => {
    await mount(page, { theme });
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await tags(page).click();
    await expect(dialog(page)).toBeVisible();
    await choice(page, 'ux').check();
    await expect(page.locator('.popover')).toHaveCSS('box-shadow', 'none');
    await page.evaluate(axeSource);
    const result = await page.evaluate(() => window.axe.run(document.body, { resultTypes: ['violations'] }));
    expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}
