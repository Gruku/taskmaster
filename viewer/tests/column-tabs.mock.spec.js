// User intent: a poll repaint of the phone column tabs must never yank a scrolled page back up to the tab strip — it
// may only slide the strip itself sideways so the selected tab shows. jsdom cannot measure this; a real browser can.
import { test, expect } from '@playwright/test';
import { mockApi, unmockedWrites } from './mock-api.js';

test.beforeEach(async ({ page }) => { await page.emulateMedia({ reducedMotion: 'reduce' }); });
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

// No harness page: the tabs mount through their module on a booted screen, above a column tall enough to scroll.
async function mount(page) {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, { '/api/viewer/prefs': { theme: 'dark', ui: {}, screens: {} } });
  await page.goto('/#/settings');
  await expect(page.locator('#sidebar .sidebar-link').first()).toBeAttached();
  await page.evaluate(async () => {
    const { columnTabs } = await import('/js/components/column-tabs.js');
    const columns = Array.from({ length: 8 }, (_, i) => ({ key: `k${i}`, label: `Column number ${i + 1}`, count: i * 3, panelId: `ct-panel-${i}` }));
    const tabs = columnTabs({ label: 'Test columns', columns, selected: 'k7', onSelect: () => {} });
    const tall = document.createElement('div');
    tall.style.height = '4000px';
    document.getElementById('screen-mount').replaceChildren(tabs.el, tall);
    window.__tabs = tabs;
    window.__columns = columns;
  });
  await expect(page.getByRole('tablist', { name: 'Test columns' })).toBeVisible();
}

// Every scrollTop from the list up to the document: whichever ancestor scrolls at this width, none may move.
const ancestorTops = (page) => page.locator('.column-tabs').evaluate((el) => {
  const out = [];
  for (let n = el.parentElement; n; n = n.parentElement) out.push(n.scrollTop);
  return [...out, document.scrollingElement.scrollTop];
});

test('at 390, a repaint scrolls only the tab list: the page stays put and the selected tab comes into the list', async ({ page }) => {
  await mount(page);
  const list = page.locator('.column-tabs');
  expect(await list.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true);
  await list.evaluate((el) => { el.scrollLeft = 0; });
  // Scroll whatever scrolls the page down, the way a user reading a long column would.
  await page.evaluate(() => {
    window.scrollTo(0, 1500);
    for (let n = document.querySelector('.column-tabs').parentElement; n; n = n.parentElement) n.scrollTop = 1500;
  });
  const before = await ancestorTops(page);
  expect(Math.max(...before)).toBeGreaterThan(0);
  const selected = page.locator('#ct-panel-7-tab');
  const outside = () => selected.evaluate((b) => {
    const l = b.parentElement.getBoundingClientRect();
    const r = b.getBoundingClientRect();
    return r.right > l.right + 0.5 || r.left < l.left - 0.5;
  });
  expect(await outside()).toBe(true);

  await page.evaluate(() => window.__tabs.update({ columns: window.__columns, selected: 'k7' }));

  expect(await ancestorTops(page)).toEqual(before);
  expect(await outside()).toBe(false);
});
