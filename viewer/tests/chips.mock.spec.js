// User intent: a chip row must hold in a real browser what jsdom cannot measure — one line that never wraps, the rest
// behind "More" in order and back when there is room, pressed chips still said from the row, a click that closes More
// still landing, touch-sized on a phone, and accessible with no shadow in both themes.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

test.beforeEach(async ({ page }) => { await page.emulateMedia({ reducedMotion: 'reduce' }); });
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

const LABELS = Array.from({ length: 20 }, (_, i) => `Epic ${String(i + 1).padStart(2, '0')}`);

// There is no harness page: the row is mounted through its module on a booted screen. Its onToggle presses the chip
// the way a screen would, by handing the row its new state; window.__toggles records every call.
async function mount(page, { width = 600, theme = 'dark', pressed = [] } = {}) {
  await mockApi(page, { '/api/viewer/prefs': { theme, ui: {}, screens: {} } });
  await page.goto('/#/settings');
  await expect(page.locator('#sidebar .sidebar-link').first()).toBeVisible();
  await page.evaluate(async ({ labels, width, pressed }) => {
    const { chipRow } = await import('/js/components/chips.js');
    const state = new Set(pressed);
    // window.__extra: chips a test adds ahead of the twenty, the way new data would.
    const chips = () => [...(window.__extra ?? []).map((c) => ({ ...c, pressed: state.has(c.value) })),
      ...labels.map((label, i) => ({ value: `e${i + 1}`, label, count: i + 1, swatch: (i % 6) + 1, pressed: state.has(`e${i + 1}`) }))];
    window.__chips = chips;
    window.__toggles = [];
    const row = chipRow({
      label: 'Epic',
      chips: chips(),
      onToggle: (value) => {
        window.__toggles.push(value);
        if (state.has(value)) state.delete(value); else state.add(value);
        row.update(chips());
      },
    });
    const box = document.createElement('div');
    box.id = 'chip-box';
    box.style.width = `${width}px`;
    box.append(row.el);
    document.getElementById('screen-mount').replaceChildren(box);
    window.__row = row;
  }, { labels: LABELS, width, pressed });
  await expect(page.locator('.chip-row')).toBeVisible();
}

const more = (page) => page.locator('.chip-row .overflow-more');
const pop = (page) => page.getByRole('dialog', { name: 'More Epic' });
const rowChips = (page) => page.locator('.chip-row__chips > .chip');
// New data while More is open: a chip ahead of the others, which must wait for More to close.
const addAhead = (page) => page.evaluate(() => {
  window.__extra = [{ value: 'new', label: 'Brand new' }];
  window.__row.update(window.__chips());
});
// Everything the row shows ends inside it: nothing, More least of all, is clipped by its overflow.
const fitsInRow = (page) => page.locator('.chip-row__chips').evaluate((row) => {
  const edge = row.getBoundingClientRect().right;
  return [...row.children].filter((c) => !c.hidden && !c.classList.contains('popover')).every((c) => c.getBoundingClientRect().right <= edge + 0.5);
});
const setWidth = (page, w) => page.evaluate((w) => { document.getElementById('chip-box').style.width = `${w}px`; }, w);

test('at 600px the chips stay on one line and the rest list behind More, in order', async ({ page }) => {
  await mount(page);
  await expect(more(page)).toBeVisible();
  const visible = await rowChips(page).evaluateAll((els) => els.map((el) => ({ top: el.offsetTop, text: el.querySelector('.chip__label').textContent })));
  expect(visible.length).toBeGreaterThan(0);
  expect(visible.length).toBeLessThan(20);
  expect(new Set(visible.map((v) => v.top)).size).toBe(1);
  expect(visible.map((v) => v.text)).toEqual(LABELS.slice(0, visible.length));
  await expect(more(page).locator('.overflow-more__count')).toHaveText(String(20 - visible.length));
  expect(await fitsInRow(page)).toBe(true);

  await more(page).click();
  await expect(pop(page)).toBeVisible();
  await expect(more(page)).toHaveAttribute('aria-expanded', 'true');
  await expect(pop(page).locator('.chip__label')).toHaveText(LABELS.slice(visible.length));
  await expect(pop(page).locator('.chip').first()).toBeFocused();
});

test('toggling a chip inside More keeps More open and flips the chip', async ({ page }) => {
  await mount(page);
  await more(page).click();
  const chip = pop(page).locator('.chip[data-value="e20"]');
  await expect(chip).toHaveAttribute('aria-pressed', 'false');
  await chip.click();
  await expect(chip).toHaveAttribute('aria-pressed', 'true');
  await expect(pop(page)).toBeVisible();
  await expect(chip).toBeFocused();
  expect(await page.evaluate(() => window.__toggles)).toEqual(['e20']);
  await expect(more(page).locator('.overflow-more__on')).toHaveText('· 1 on');
});

test('a pressed chip parked behind More is announced on the More button', async ({ page }) => {
  await mount(page, { pressed: ['e20'] });
  // Parked: out of the row (the closed popover's list is not in the page).
  await expect(more(page)).toBeVisible();
  await expect(page.locator('.chip[data-value="e20"]')).toHaveCount(0);
  await expect(more(page).locator('.overflow-more__on')).toHaveText('· 1 on');
  const hidden = await more(page).locator('.overflow-more__count').textContent();
  await expect(more(page)).toHaveAccessibleName(`More Epic, ${hidden} hidden, 1 selected`);
  // "· 1 on" widened More after it was measured; the row made room for it.
  expect(await fitsInRow(page)).toBe(true);
  // Released, the announcement goes.
  await more(page).click();
  await pop(page).locator('.chip[data-value="e20"]').click();
  await expect(more(page).locator('.overflow-more__on')).toHaveCount(0);
  await expect(more(page)).toHaveAccessibleName(`More Epic, ${hidden} hidden`);
});

// The brief said 1400px, but twenty chips with swatch and count measure about 2230px with their gaps.
test('More never ends up clipped by the "· n on" it gains after it was measured, whatever the slack', async ({ page }) => {
  await mount(page);
  // A fresh row at each width across one chip's width, so the room left beside More takes every value, including too
  // little for "· 1 on". Then a parked chip is pressed from outside (say a filter restored from the address): More
  // is measured before "· 1 on" is added to it.
  const clipped = await page.evaluate(async (labels) => {
    const { chipRow } = await import('/js/components/chips.js');
    const chips = (on) => labels.map((label, i) => ({ value: `e${i + 1}`, label, count: i + 1, pressed: on && i === 19 }));
    const out = [];
    for (let w = 540; w <= 660; w += 2) {
      const r = chipRow({ label: 'Epic', chips: chips(false) });
      const box = document.createElement('div');
      box.style.width = `${w}px`;
      box.append(r.el);
      document.getElementById('screen-mount').append(box);
      const row = r.el.querySelector('.chip-row__chips');
      // A new observer's first report comes in the same delivery as the row's own, which was created first and so
      // has laid the row out by then (frames alone are not enough: a delivery can lag them).
      await new Promise((done) => { const ro = new ResizeObserver(() => { ro.disconnect(); done(); }); ro.observe(row); });
      r.update(chips(true));
      const more = row.querySelector('.overflow-more');
      if (!more.querySelector('.overflow-more__on')) out.push(`${w}: nothing parked is announced`);
      if (more.getBoundingClientRect().right > row.getBoundingClientRect().right + 0.5) out.push(`${w}: More clipped`);
      r.destroy();
      box.remove();
    }
    return out;
  }, LABELS);
  expect(clipped).toEqual([]);
});

test('a count that widens a visible chip in place makes room for itself', async ({ page }) => {
  await mount(page);
  // Every count grows by five digits, so the chips that were shown no longer all fit beside More.
  const before = await rowChips(page).count();
  await page.evaluate(() => {
    const chips = window.__chips().map((c) => ({ ...c, count: c.count * 100000 }));
    window.__row.update(chips);
  });
  expect(await fitsInRow(page)).toBe(true);
  expect(await rowChips(page).count()).toBeLessThan(before);
  const shown = await rowChips(page).count();
  await expect(more(page).locator('.overflow-more__count')).toHaveText(String(20 - shown));
});

test('a focused chip that a narrower row parks hands focus to More, not to the page', async ({ page }) => {
  await mount(page, { width: 900 });
  const last = rowChips(page).last();
  await last.focus();
  const value = await last.getAttribute('data-value');
  await setWidth(page, 400);
  await expect(page.locator(`.chip[data-value="${value}"]`)).toHaveCount(0);
  await expect(more(page)).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(pop(page).locator(`.chip[data-value="${value}"]`)).toBeVisible();
});

test('widening to 2400px brings every chip back in order and hides More', async ({ page }) => {
  await mount(page);
  await expect(more(page)).toBeVisible();
  await setWidth(page, 2400);
  await expect(more(page)).toBeHidden();
  await expect(rowChips(page).locator('.chip__label')).toHaveText(LABELS);
  expect(await page.locator('.chip-row [data-popover-item]').count()).toBe(0);
  // And narrowing again parks the tail once more.
  await setWidth(page, 600);
  await expect(more(page)).toBeVisible();
});

test('a click on a visible chip while More is open closes More and toggles the chip', async ({ page }) => {
  await mount(page);
  await more(page).click();
  await expect(pop(page)).toBeVisible();
  const first = rowChips(page).first();
  await expect(first).toHaveAttribute('aria-pressed', 'false');
  await first.click();
  await expect(pop(page)).toHaveCount(0);
  await expect(first).toHaveAttribute('aria-pressed', 'true');
  expect(await page.evaluate(() => window.__toggles)).toEqual(['e1']);
});

test('a row change waiting on More does not move the visible chip that a press closing More is aimed at', async ({ page }) => {
  await mount(page);
  await more(page).click();
  await addAhead(page);
  const first = page.locator('.chip[data-value="e1"]');
  const box = await first.boundingBox();
  // Pressed and held: More closes on the press; the new chip, which would push e1 right, waits for the release.
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await expect(pop(page)).toHaveCount(0);
  expect(await first.boundingBox()).toEqual(box);
  await page.mouse.up();
  await expect(first).toHaveAttribute('aria-pressed', 'true');
  expect(await page.evaluate(() => window.__toggles)).toEqual(['e1']);
  await expect(rowChips(page).first()).toHaveAttribute('data-value', 'new');
});

test('a press that closes More and is dragged off without a click still lets the waiting change in', async ({ page }) => {
  await mount(page);
  await more(page).click();
  await addAhead(page);
  const box = await page.locator('.chip[data-value="e1"]').boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2, box.y + 200, { steps: 4 });
  await page.mouse.up();
  await expect(rowChips(page).first()).toHaveAttribute('data-value', 'new');
  expect(await page.evaluate(() => window.__toggles)).toEqual([]);
});

test('a chip added while More is open waits for it to close, then the row lays out afresh', async ({ page }) => {
  await mount(page);
  await more(page).click();
  const parked = await pop(page).locator('.chip').evaluateAll((els) => els.map((el) => el.dataset.value));
  await addAhead(page);
  await expect(page.locator('.chip[data-value="new"]')).toHaveCount(0);
  await expect(pop(page)).toBeVisible();
  expect(await pop(page).locator('.chip').evaluateAll((els) => els.map((el) => el.dataset.value))).toEqual(parked);
  await page.keyboard.press('Escape');
  await expect(pop(page)).toHaveCount(0);
  await expect(rowChips(page).first()).toHaveAttribute('data-value', 'new');
  const all = ['Brand new', ...LABELS];
  const shown = await rowChips(page).count();
  await expect(rowChips(page).locator('.chip__label')).toHaveText(all.slice(0, shown));
  await expect(more(page).locator('.overflow-more__count')).toHaveText(String(21 - shown));
  await more(page).click();
  await expect(pop(page).locator('.chip__label')).toHaveText(all.slice(shown));
});

test('Escape in More closes it and focuses More', async ({ page }) => {
  await mount(page);
  await more(page).click();
  await expect(pop(page).locator('.chip').first()).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(pop(page)).toHaveCount(0);
  await expect(more(page)).toBeFocused();
});

test('at 390px every chip and More is at least 44px tall', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mount(page, { width: 340 });
  await expect(more(page)).toBeVisible();
  const heights = await page.locator('.chip-row .chip:not([data-popover-item]), .chip-row .overflow-more').evaluateAll((els) => els.map((el) => el.getBoundingClientRect().height));
  expect(heights.length).toBeGreaterThan(1);
  for (const h of heights) expect(h).toBeGreaterThanOrEqual(44);
  await more(page).click();
  const parked = await pop(page).locator('.chip').evaluateAll((els) => els.map((el) => el.getBoundingClientRect().height));
  for (const h of parked) expect(h).toBeGreaterThanOrEqual(44);
});

for (const theme of ['dark', 'light']) {
  test(`${theme}: More open has no shadow and no axe violation`, async ({ page }) => {
    await mount(page, { theme, pressed: ['e2', 'e20'] });
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await more(page).click();
    await expect(pop(page)).toBeVisible();
    const shadows = await page.locator('.chip-row .chip, .chip-row .overflow-more, .popover').evaluateAll((els) => [...new Set(els.map((el) => getComputedStyle(el).boxShadow))]);
    expect(shadows).toEqual(['none']);
    await page.evaluate(axeSource);
    const result = await page.evaluate(() => window.axe.run(document.querySelector('.chip-row'), { resultTypes: ['violations'] }));
    expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}
