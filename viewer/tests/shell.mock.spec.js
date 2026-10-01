// User intent: the shell must be keyboard-usable, free of the banned visual patterns, and its theme toggle must work.
import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

test.beforeEach(async ({ page }) => { await mockApi(page); });

test('theme toggle flips and persists the choice', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/#/settings');
  const puts = [];
  page.on('request', (r) => { if (r.method() === 'PUT') puts.push(r.postData()); });
  const toggle = page.locator('#theme-toggle');
  await expect(toggle).toHaveAttribute('aria-label', 'Switch to light theme');
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await toggle.click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(toggle).toHaveAttribute('aria-label', 'Switch to dark theme');
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  expect(await page.evaluate(() => localStorage.getItem('tm.theme'))).toBe('light');
  await expect.poll(() => puts.join('')).toContain('"theme":"light"');
});

test('theme toggle works from the keyboard', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/#/settings');
  const toggle = page.locator('#theme-toggle');
  await expect(toggle).toHaveAttribute('aria-label', 'Switch to light theme');
  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.keyboard.press('Space');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
});

test('active nav item has no shadow and no left rail; Task item is gone', async ({ page }) => {
  await page.goto('/#/kanban');
  const active = page.locator('.sidebar-link.active');
  await expect(active).toHaveAttribute('data-key', 'kanban');
  await expect(active).toHaveAttribute('aria-current', 'page');
  const cs = await active.evaluate((el) => {
    const s = getComputedStyle(el);
    return { sh: s.boxShadow, l: s.borderLeftWidth, r: s.borderRightWidth, lc: s.borderLeftColor, rc: s.borderRightColor };
  });
  expect(cs.sh).toBe('none');
  expect(cs.l).toBe(cs.r);
  expect(cs.lc).toBe(cs.rc);
  await expect(page.locator('.sidebar-link')).toHaveCount(10);
  await expect(page.locator('.sidebar-link[data-key="task"]')).toHaveCount(0);
  await expect(page.locator('.sidebar-footer')).toHaveCount(0);
  await expect(page.locator('.sidebar-link .badge')).toHaveCount(0);
});

test('task detail highlights no nav item', async ({ page }) => {
  await page.goto('/#/kanban');
  await expect(page.locator('.sidebar-link.active')).toHaveCount(1);
  await page.goto('/#/task');
  await expect(page.locator('#page-title')).toHaveText('Task Detail');
  await expect(page.locator('.sidebar-link.active')).toHaveCount(0);
});

test('nav icons are painted inline SVG, not text glyphs', async ({ page }) => {
  await page.goto('/#/kanban');
  const links = page.locator('.sidebar-link');
  await expect(links).toHaveCount(10);
  const probe = await links.evaluateAll((els) => els.map((a) => {
    const svg = a.querySelector('.ic > svg.icon');
    const shape = svg?.firstElementChild;
    const box = svg?.getBoundingClientRect();
    const shapeBox = shape?.getBoundingClientRect();
    return {
      key: a.dataset.key,
      svgNs: svg?.namespaceURI, shapeNs: shape?.namespaceURI,
      w: box?.width, h: box?.height, shapeW: shapeBox?.width, shapeH: shapeBox?.height,
      icText: a.querySelector('.ic')?.textContent,
    };
  }));
  for (const p of probe) {
    expect(p.svgNs, p.key).toBe(SVG_NS);
    expect(p.shapeNs, p.key).toBe(SVG_NS);
    expect(p.w, p.key).toBe(20);
    expect(p.h, p.key).toBe(20);
    expect(p.shapeW, p.key).toBeGreaterThan(0);
    expect(p.shapeH, p.key).toBeGreaterThan(0);
    expect(p.icText, p.key).toBe('');
  }
  // Brand is the wordmark alone: no gradient mark.
  await expect(page.locator('.sidebar-logo .name')).toHaveText('TASKMASTER');
  await expect(page.locator('.sidebar-logo .mark')).toHaveCount(0);
  await expect(page.locator('#theme-toggle svg.icon')).toHaveCount(1);
});

test('Ctrl+K focuses the search field, which has a name and a focus ring', async ({ page }) => {
  await page.goto('/#/kanban');
  const input = page.locator('[data-global-search]');
  await input.waitFor();
  await expect(input).toHaveAttribute('aria-label', 'Find tasks');
  await page.keyboard.press('Control+k');
  await expect(input).toBeFocused();
  // The ring is drawn on the field's wrapper, in the focus color.
  const ring = await input.evaluate((el) => {
    const wrap = getComputedStyle(el.parentElement);
    const probe = document.createElement('i');
    probe.style.color = 'var(--border-focus)';
    document.body.appendChild(probe);
    const focusColor = getComputedStyle(probe).color;
    probe.remove();
    return { style: wrap.outlineStyle, width: wrap.outlineWidth, color: wrap.outlineColor, focusColor };
  });
  expect(ring.style).toBe('solid');
  expect(ring.width).toBe('2px');
  expect(ring.color).toBe(ring.focusColor);
});

test('search shortcut hint names the platform shortcut', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(navigator, 'platform', { get: () => 'Win32' }));
  await page.goto('/#/issues');
  await expect(page.locator('.tm-search .cmp-kbd')).toHaveText('Ctrl K');
  await expect(page.locator('[data-global-search]')).toHaveAttribute('aria-label', /\S/);
});

test('topbar row 1 keeps the same height and the title the same position on every route', async ({ page }) => {
  const heights = new Set();
  const titleTops = new Set();
  const barHeights = new Set();
  for (const h of ['#/dashboard', '#/kanban', '#/issues', '#/ideas', '#/settings']) {
    await page.goto('/' + h);
    await expect(page.locator(`.sidebar-link.active[data-key="${h.slice(2)}"]`)).toHaveCount(1);
    heights.add(await page.locator('.topbar-row1').evaluate((el) => el.offsetHeight));
    titleTops.add(await page.locator('#page-title').evaluate((el) => Math.round(el.getBoundingClientRect().top)));
    barHeights.add(await page.locator('#topbar').evaluate((el) => el.offsetHeight));
  }
  expect([...heights]).toEqual([56]);
  expect(titleTops.size).toBe(1);
  // One of exactly two heights: row 1 alone, or row 1 + row 2 (each plus the 1px rule).
  expect([...barHeights].sort((a, b) => a - b)).toEqual([57, 105]);
});

test('sidebar collapse keeps its label in step with its state', async ({ page }) => {
  await page.goto('/#/kanban');
  const btn = page.locator('.sidebar-collapse-btn');
  await expect(btn).toHaveAttribute('aria-label', 'Collapse sidebar');
  await btn.click();
  await expect(page.locator('.shell')).toHaveClass(/sidebar-collapsed/);
  await expect(btn).toHaveAttribute('aria-label', 'Expand sidebar');
  await expect(btn).toHaveClass(/is-collapsed/);
  await expect.poll(() => page.locator('#sidebar').evaluate((el) => el.offsetWidth)).toBe(56);
});

test.describe('mobile drawer', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('opens with focus inside, closes on Escape, returns focus', async ({ page }) => {
    await page.goto('/#/kanban');
    await expect(page.locator('.sidebar-link.active')).toHaveCount(1);   // route settled: a route event closes the drawer
    const burger = page.locator('.topbar-hamburger');
    await expect(burger).toHaveAttribute('aria-expanded', 'false');
    await expect(burger).toHaveAttribute('aria-controls', 'sidebar');
    expect(await burger.evaluate((el) => el.parentElement.className)).toBe('topbar-row1');
    // Closed drawer is off-screen and out of the tab order.
    expect(await page.locator('#sidebar').evaluate((el) => el.inert)).toBe(true);
    await burger.click();
    await expect(burger).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('.sidebar-link').first()).toBeFocused();
    await expect(page.locator('.sidebar-collapse-btn')).toBeHidden();
    await page.keyboard.press('Escape');
    await expect(burger).toHaveAttribute('aria-expanded', 'false');
    await expect(burger).toBeFocused();
    await expect(page.locator('.shell')).not.toHaveClass(/sidebar-drawer-open/);
  });

  test('choosing a destination closes the drawer', async ({ page }) => {
    await page.goto('/#/kanban');
    await expect(page.locator('.sidebar-link.active')).toHaveCount(1);
    await page.locator('.topbar-hamburger').click();
    await page.locator('.sidebar-link[data-key="settings"]').click();
    await expect(page.locator('#page-title')).toHaveText('Settings');
    await expect(page.locator('.shell')).not.toHaveClass(/sidebar-drawer-open/);
    await expect(page.locator('.topbar-hamburger')).toHaveAttribute('aria-expanded', 'false');
  });

  test('topbar row 1 stays 56px and the page does not scroll sideways', async ({ page }) => {
    await page.goto('/#/kanban');
    await page.locator('.topbar-hamburger').waitFor();
    expect(await page.locator('.topbar-row1').evaluate((el) => el.offsetHeight)).toBe(56);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
});
