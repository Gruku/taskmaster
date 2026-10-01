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
  await toggle.click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(toggle).toHaveAttribute('aria-label', 'Switch to dark theme');
  // An action button: the label names the result, so it carries no pressed state.
  expect(await toggle.getAttribute('aria-pressed')).toBeNull();
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

test('theme toggle is disabled until the saved preference has loaded', async ({ page }) => {
  // A click before prefs arrive would be applied, then silently reverted by the loaded value.
  let release;
  const gate = new Promise((ok) => { release = ok; });
  await page.route('**/api/viewer/prefs', async (route) => {
    await gate;
    await route.fulfill({ json: { theme: 'dark', ui: {}, screens: {} } });
  });
  await page.goto('/#/settings', { waitUntil: 'commit' });
  const toggle = page.locator('#theme-toggle');
  await expect(toggle.locator('svg.icon')).toHaveCount(1);   // boot has started and is waiting on prefs
  await expect(toggle).toBeDisabled();
  release();
  await expect(toggle).toBeEnabled();
  await expect(toggle).toHaveAttribute('aria-label', 'Switch to light theme');
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

test('links with their own colour rule keep it; only unclaimed links take the signature colour', async ({ page }) => {
  // The fallback for unclaimed links must not outrank a single-class rule such as .sidebar-link.
  const missing = { '/api/task/NOPE-999/detail': { status: 404, json: { ok: false, error: 'unknown task' } } };
  const probe = () => page.evaluate(() => {
    const resolve = (v) => {
      const i = document.createElement('i');
      i.style.color = `var(${v})`;
      document.body.appendChild(i);
      const c = getComputedStyle(i).color;
      i.remove();
      return c;
    };
    const color = (sel) => {
      const el = document.querySelector(sel);
      // Transitions on colour would report a mid-fade value.
      el.style.transition = 'none';
      return getComputedStyle(el).color;
    };
    return {
      signature: resolve('--signature-text'),
      body: resolve('--foreground-default'),
      nav: color('.sidebar-link:not(.active)'),
      classless: color('#screen-mount .tm-empty__hint a'),
    };
  });
  for (const theme of ['dark', 'light']) {
    await mockApi(page, { ...missing, '/api/viewer/prefs': { theme, ui: {}, screens: {} } });
    await page.goto('/#/task/NOPE-999');
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(page.locator('#screen-mount .tm-empty__hint a')).toBeVisible();
    await expect(page.locator('.sidebar-link').first()).toBeVisible();
    const c = await probe();
    expect(c.signature, theme).not.toBe(c.body);
    expect(c.nav, theme).not.toBe(c.signature);
    expect(c.nav, theme).toBe(c.body);
    expect(c.classless, theme).toBe(c.signature);
  }
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

test('the search ring belongs to the input; the clear button shows its own', async ({ page }) => {
  await page.goto('/#/kanban');
  const input = page.locator('[data-global-search]');
  await input.fill('abc');
  await page.keyboard.press('Tab');
  const clear = page.locator('.tm-search__clear');
  await expect(clear).toBeFocused();
  const styles = await clear.evaluate((el) => ({
    wrap: getComputedStyle(el.parentElement).outlineStyle,
    self: getComputedStyle(el).outlineStyle,
  }));
  expect(styles.wrap).toBe('none');
  expect(styles.self).toBe('solid');
});

test('Ctrl+K leaves focus alone while a modal is open', async ({ page }) => {
  await page.goto('/#/kanban');
  await page.locator('#topbar-actions [aria-label="Add task"]').click();
  const modal = page.locator('[aria-modal="true"]');
  const field = modal.locator('input, textarea').first();
  await field.focus();
  await expect(field).toBeFocused();
  await page.keyboard.press('Control+k');
  await expect(field).toBeFocused();
  await expect(page.locator('[data-global-search]')).not.toBeFocused();
});

test('Ctrl+K ignores Shift and Alt chords and key events without a key', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('/#/kanban');
  const input = page.locator('[data-global-search]');
  await input.waitFor();
  await page.keyboard.press('Control+Shift+K');
  await expect(input).not.toBeFocused();
  await page.keyboard.press('Control+Alt+k');
  await expect(input).not.toBeFocused();
  // Autofill and some IMEs dispatch keydown without `key`.
  await page.evaluate(() => window.dispatchEvent(new Event('keydown')));
  await page.evaluate(() => window.dispatchEvent(Object.assign(new Event('keydown'), { ctrlKey: true })));
  expect(errors).toEqual([]);
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

  test('keeps keyboard focus inside while open; its close button closes it', async ({ page }) => {
    await page.goto('/#/kanban');
    const burger = page.locator('.topbar-hamburger');
    await burger.click();
    await expect(page.locator('.sidebar-link').first()).toBeFocused();
    expect(await page.locator('.main').evaluate((el) => el.inert)).toBe(true);
    // More Tab presses than the drawer has stops: focus never lands on the page behind it.
    for (let i = 0; i < 14; i++) {
      await page.keyboard.press('Tab');
      const where = await page.evaluate(() => {
        const a = document.activeElement;
        return a === document.body ? 'body' : document.getElementById('sidebar').contains(a) ? 'drawer' : 'page';
      });
      expect(where, `Tab ${i + 1}`).not.toBe('page');
    }
    const close = page.locator('.sidebar-close-btn');
    await expect(close).toBeVisible();
    await expect(close).toHaveAttribute('aria-label', 'Close navigation');
    await close.focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('.shell')).not.toHaveClass(/sidebar-drawer-open/);
    await expect(burger).toBeFocused();
    expect(await page.locator('.main').evaluate((el) => el.inert)).toBe(false);
  });

  test('choosing a destination closes the drawer', async ({ page }) => {
    await page.goto('/#/kanban');
    await page.locator('.topbar-hamburger').click();
    await page.locator('.sidebar-link[data-key="settings"]').click();
    await expect(page.locator('#page-title')).toHaveText('Settings');
    await expect(page.locator('.shell')).not.toHaveClass(/sidebar-drawer-open/);
    await expect(page.locator('.topbar-hamburger')).toHaveAttribute('aria-expanded', 'false');
  });

  test('tapping the current page in the drawer closes it', async ({ page }) => {
    await page.goto('/#/kanban');
    await expect(page.locator('.sidebar-link.active')).toHaveAttribute('data-key', 'kanban');
    await page.locator('.topbar-hamburger').click();
    await page.locator('.sidebar-link[data-key="kanban"]').click();
    await expect(page.locator('.shell')).not.toHaveClass(/sidebar-drawer-open/);
    await expect(page.locator('.topbar-hamburger')).toBeFocused();
  });

  test('a drawer opened while the screen is still mounting stays open', async ({ page }) => {
    let release;
    const gate = new Promise((ok) => { release = ok; });
    await page.route('**/api/ideas**', async (route) => { await gate; await route.fulfill({ json: { ideas: [] } }); });
    await page.goto('/#/ideas');
    const burger = page.locator('.topbar-hamburger');
    await burger.click();
    await expect(burger).toHaveAttribute('aria-expanded', 'true');
    release();
    await expect(page.locator('.sidebar-link.active')).toHaveAttribute('data-key', 'ideas');   // route:changed has fired
    await expect(burger).toHaveAttribute('aria-expanded', 'true');
  });

  test('the desktop collapse button has no close-drawer twin showing', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto('/#/kanban');
    await expect(page.locator('.sidebar-collapse-btn')).toBeVisible();
    await expect(page.locator('.sidebar-close-btn')).toBeHidden();
  });

  test('topbar row 1 stays 56px and the page does not scroll sideways', async ({ page }) => {
    await page.goto('/#/kanban');
    await page.locator('.topbar-hamburger').waitFor();
    expect(await page.locator('.topbar-row1').evaluate((el) => el.offsetHeight)).toBe(56);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
});
