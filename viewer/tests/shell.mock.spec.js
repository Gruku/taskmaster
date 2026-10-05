// User intent: the shell must be keyboard-usable, free of the banned visual patterns, and its theme toggle must work.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD, DETAIL_TASK, taskDetail } from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

const SVG_NS = 'http://www.w3.org/2000/svg';

// Row 2 lays out again when the web fonts arrive; after that, and a frame, what is parked behind Filters has settled.
async function rowSettled(page) {
  await expect(page.locator('#topbar-actions > *:not([hidden])').first()).toBeVisible();
  await page.evaluate(() => document.fonts.ready.then(() => new Promise((ok) => requestAnimationFrame(() => ok()))));
}

// A row-2 control: in the row, or behind Filters when the row is too narrow for it (Filters is opened to reach it).
async function topbarControl(page, selector) {
  await rowSettled(page);
  if (!(await page.locator(`#topbar-actions > ${selector}, #topbar-actions > :not(.popover) ${selector}`).count())) {
    // From the keyboard, so a control focused in the popover afterwards still shows its focus ring.
    await page.locator('#topbar-actions > .overflow-more').focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('dialog', { name: 'Filters' })).toBeVisible();
  }
  return page.locator(`#topbar-actions ${selector}`);
}

test.beforeEach(async ({ page }) => { await mockApi(page); });
// A write the mock did not expect means the page talked to an endpoint this spec never set up.
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

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

test('a theme choice followed at once by another preference change is still saved', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/#/kanban');
  const toggle = page.locator('#theme-toggle');
  await expect(toggle).toBeEnabled();
  await expect(page.locator('.sidebar-link.active')).toHaveCount(1);   // boot's own writes are behind us
  const puts = [];
  page.on('request', (r) => { if (r.method() === 'PUT' && r.url().endsWith('/api/viewer/prefs')) puts.push(r.postData()); });
  // Both inside one debounce window: the second used to replace the first.
  await page.evaluate(() => {
    document.getElementById('theme-toggle').click();
    document.querySelector('.sidebar-collapse-btn').click();
  });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect.poll(() => puts.join('\n')).toContain('"sidebar_collapsed":true');
  expect(puts.join('\n')).toContain('"theme":"light"');
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
      signature: resolve('--text-accent'),
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
  await (await topbarControl(page, '[aria-label="Add task"]')).click();
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

// Every screen that builds its topbar from the shared controls, with content so its counts and chips are drawn.
const TOPBAR_ROUTES = ['#/kanban', '#/table', '#/issues', '#/sessions', '#/ideas', '#/bugs', '#/archived', '#/task/T-102'];
const withContent = (theme = 'dark') => ({
  '/api/viewer/prefs': { theme, ui: {}, screens: {} },
  '/api/board': BOARD, '/api/backlog': BOARD, '/api/bugs': [], '/api/sessions': [], '/api/threads': [],
  '/api/task/T-102/detail': taskDetail(DETAIL_TASK),
});

for (const width of [1440, 390]) {
  test(`at ${width}px the Add task button is the shared primary button`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page, withContent());
    await page.goto('/#/kanban');
    const add = await topbarControl(page, '[aria-label="Add task"]');
    await expect(add).toBeVisible();
    await expect(add).toHaveClass(/(^|\s)btn(\s|$)/);
    await expect(add).toHaveClass(/(^|\s)btn--primary(\s|$)/);
    await expect(add.locator('svg.icon')).toHaveCount(1);
  });
}

for (const theme of ['dark', 'light']) {
  test(`${theme}: the topbar's controls pass axe colour contrast on every screen`, async ({ page }) => {
    await mockApi(page, withContent(theme));
    for (const route of TOPBAR_ROUTES) {
      await page.goto('/' + route);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(page.locator('#topbar-actions > *').first(), route).toBeVisible();
      await page.evaluate(axeSource);
      const contrast = async (where) => {
        const result = await page.evaluate(() => window.axe.run(document.getElementById('topbar'), { runOnly: ['color-contrast'] }));
        expect(result.violations.map((v) => `${where} ${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
      };
      await contrast(route);
      // What did not fit is checked where it is shown: in the Filters popover.
      const more = page.locator('#topbar-actions > .overflow-more');
      await rowSettled(page);
      if (await more.isVisible()) {
        await more.click();
        await expect(page.getByRole('dialog', { name: 'Filters' })).toBeVisible();
        // Measured once the popover has faded in.
        await page.evaluate(() => Promise.all(document.getAnimations().map((a) => a.finished)));
        await contrast(`${route} (Filters)`);
      }
    }
  });
}

test('a focused segment shows its whole ring and the pressed one is the solid signature fill', async ({ page }) => {
  await mockApi(page, withContent());
  await page.goto('/#/issues');
  const seg = await topbarControl(page, '.tm-segmented');
  const pressed = seg.locator('button[aria-pressed="true"]');
  await expect(pressed).toHaveCount(1);
  await pressed.focus();
  const look = await pressed.evaluate((el) => {
    const probe = (v) => { const i = document.createElement('i'); i.style.color = v; document.body.appendChild(i); const c = getComputedStyle(i).color; i.remove(); return c; };
    const cs = getComputedStyle(el);
    return {
      outline: `${cs.outlineStyle} ${cs.outlineWidth}`, clip: getComputedStyle(el.parentElement).overflow,
      bg: cs.backgroundColor, fill: probe('var(--signature-fill)'), ink: cs.color, onFill: probe('var(--on-accent-fill)'),
    };
  });
  expect(look.outline).toBe('solid 2px');
  // A clipping group would cut the 2px-offset ring off at its edge.
  expect(look.clip).toBe('visible');
  expect(look.bg).toBe(look.fill);
  expect(look.ink).toBe(look.onFill);
});

test('no topbar control is a disabled placeholder', async ({ page }) => {
  await mockApi(page, withContent());
  for (const route of ['#/issues', '#/sessions']) {
    await page.goto('/' + route);
    await expect(page.locator('#topbar-actions .tm-search')).toBeVisible();
    await expect(page.locator('#topbar :is(button, a)[disabled], #topbar [aria-disabled="true"]')).toHaveCount(0);
    await expect(page.locator('#topbar [title*="coming soon" i], #topbar [aria-label*="coming soon" i]')).toHaveCount(0);
  }
});

test.describe('topbar controls at phone width', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('search, segments, actions and the clear button are 44px touch targets', async ({ page }) => {
    await mockApi(page, withContent());
    for (const route of ['#/kanban', '#/issues', '#/task/T-102']) {
      await page.goto('/' + route);
      await expect(page.locator('#topbar-actions > *').first()).toBeVisible();
      const input = page.locator('#topbar-actions .tm-search input');
      if (await input.count()) await input.fill('abc');
      const short = () => page.locator('#topbar-actions').evaluate((root) => [...root.querySelectorAll('.tm-search, .tm-search__clear, .tm-segmented > button, .btn')]
        .filter((el) => el.getClientRects().length)
        .map((el) => ({ el: el.className || el.tagName, h: el.getBoundingClientRect().height }))
        .filter(({ h }) => h < 44));
      expect(await short(), route).toEqual([]);
      // The controls parked behind Filters are touch targets there too.
      const more = page.locator('#topbar-actions > .overflow-more');
      await rowSettled(page);
      if (await more.isVisible()) {
        await more.click();
        await expect(page.getByRole('dialog', { name: 'Filters' })).toBeVisible();
        expect(await short(), `${route} (Filters)`).toEqual([]);
      }
    }
  });
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

test('the tab icon is the pixel-fitted ICO, a real file the server can deliver', async ({ page }) => {
  await page.goto('/#/kanban');
  const hrefs = await page.locator('link[rel="icon"]').evaluateAll((links) => links.map((l) => l.href));
  expect(hrefs.map((h) => new URL(h).pathname)).toEqual(['/vendor/favicon.ico']);
  for (const href of hrefs) {
    const res = await page.request.get(href);
    expect(res.status(), href).toBe(200);
  }
});

// Topbar row 2 stays on one line: what does not fit waits behind "Filters".
const filters = (page) => page.locator('#topbar-actions > .overflow-more');
const filtersPopover = (page) => page.getByRole('dialog', { name: 'Filters' });
// Every control left in the row ends inside it; none is cut off at its edge.
const rowFits = (page) => page.locator('#topbar-actions').evaluate((row) => {
  const edge = row.getBoundingClientRect().right;
  const cut = [...row.children].filter((c) => !c.hidden && !c.classList.contains('popover'))
    .filter((c) => c.getBoundingClientRect().right > edge + 0.5).map((c) => c.className);
  return { overflow: row.scrollWidth - row.clientWidth, cut };
});

test('row 2 never scrolls sideways or wraps, and a row holding only the hidden Filters button is hidden', async ({ page }) => {
  await mockApi(page, withContent());
  await page.goto('/#/table');
  await expect(page.locator('#topbar-actions .tm-search')).toBeVisible();
  const look = await page.locator('#topbar-actions').evaluate((row) => {
    const cs = getComputedStyle(row);
    return { x: cs.overflowX, y: cs.overflowY, wrap: cs.flexWrap };
  });
  expect(look).toEqual({ x: 'hidden', y: 'hidden', wrap: 'nowrap' });
  await page.goto('/#/settings');
  await expect(page.locator('#page-title')).toHaveText('Settings');
  await expect(filters(page)).toHaveCount(1);
  await expect(filters(page)).toBeHidden();
  await expect(page.locator('#topbar-actions')).toBeHidden();
});

test.describe('topbar row 2 at phone width', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('on the Table the search and Filters stay; the rest is in the Filters popover and works there', async ({ page }) => {
    await mockApi(page, withContent());
    await page.goto('/#/table');
    const search = page.locator('#topbar-actions > .tm-search');
    await expect(search).toBeVisible();
    await expect(filters(page)).toBeVisible();
    await expect.poll(() => rowFits(page)).toEqual({ overflow: 0, cut: [] });
    // What did not fit is out of the row until Filters opens.
    await expect(page.locator('#topbar-actions > [aria-label="Add task"]')).toHaveCount(0);
    await filters(page).click();
    const pop = filtersPopover(page);
    await expect(pop).toBeVisible();
    await expect(pop.locator('.tm-subcount')).toBeVisible();
    const add = pop.locator('[aria-label="Add task"]');
    await expect(add).toBeVisible();
    // The count parked first is no control: focus goes on to the first one that is.
    await expect(add).toBeFocused();
    await add.click();
    await expect(page.getByRole('dialog', { name: 'Create task' })).toBeVisible();
  });

  test('parked controls are a column in the Filters popover and a parked chip row wraps', async ({ page }) => {
    await mockApi(page, withContent());
    await page.goto('/#/kanban');
    await expect(filters(page)).toBeVisible();
    await filters(page).click();
    const pop = filtersPopover(page);
    await expect(pop).toBeVisible();
    const look = await pop.evaluate((el) => {
      const list = el.querySelector('.overflow-list');
      const items = [...list.children];
      const boxes = items.map((c) => c.getBoundingClientRect());
      return {
        direction: getComputedStyle(list).flexDirection,
        stacked: boxes.every((b, i) => i === 0 || b.top >= boxes[i - 1].bottom - 0.5),
        chipWrap: [...list.querySelectorAll('.tm-chip-row')].map((r) => getComputedStyle(r).flexWrap),
        sideways: el.scrollWidth - el.clientWidth,
        // Over the board's sticky column headers, not under them: each parked control takes a press at its centre.
        covered: [...list.querySelectorAll('button, select')].filter((c) => {
          const b = c.getBoundingClientRect();
          return !c.contains(document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2));
        }).map((c) => c.getAttribute('aria-label') || c.className),
      };
    });
    expect(look.direction).toBe('column');
    expect(look.stacked).toBe(true);
    expect(look.chipWrap.length).toBeGreaterThan(0);
    expect(look.chipWrap.every((w) => w === 'wrap')).toBe(true);
    expect(look.sideways).toBe(0);
    expect(look.covered).toEqual([]);
  });

  test('leaving a screen with its controls parked behind Filters leaves nothing behind', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await mockApi(page, withContent());
    await page.goto('/#/table');
    await filters(page).click();
    await expect(filtersPopover(page)).toBeVisible();
    await page.evaluate(() => { location.hash = '#/kanban'; });
    await expect(page.locator('#page-title')).toHaveText('Kanban');
    await expect(page.locator('[placeholder="Filter… (prefix ! to exclude)"]')).toHaveCount(0);
    await expect(page.locator('.popover')).toHaveCount(0);
    await expect(page.locator('#topbar-actions > .tm-search input')).toBeVisible();
    await expect.poll(() => rowFits(page)).toEqual({ overflow: 0, cut: [] });
    // Kanban's own controls were laid out afresh: what is parked is Kanban's, and Filters lists them.
    await filters(page).click();
    await expect(filtersPopover(page).locator('[aria-label="Add task"]')).toHaveCount(1);
    await expect(filtersPopover(page).locator('.tm-subcount')).toHaveCount(1);
    expect(errors).toEqual([]);
  });

  test('Filters counts the controls it holds; an empty chip group is neither parked nor counted', async ({ page }) => {
    await mockApi(page, withContent());
    await page.goto('/#/ideas');
    await expect(filters(page)).toBeVisible();
    // With no ideas the status and tag groups have no chips yet: they stay in the row, taking no room.
    await expect(page.locator('#topbar-actions > .ideas__status-chips:empty')).toHaveCount(1);
    await expect(page.locator('#topbar-actions > .ideas__tag-chips:empty')).toHaveCount(1);
    await filters(page).click();
    const items = filtersPopover(page).locator('.overflow-list > *');
    await expect(items.first()).toBeVisible();
    const sizes = await items.evaluateAll((els) => els.map((el) => el.getBoundingClientRect().width));
    expect(sizes.every((w) => w > 0)).toBe(true);
    await expect(filters(page).locator('.overflow-more__count')).toHaveText(String(sizes.length));
  });

  test('a parked segmented control stays one piece', async ({ page }) => {
    await mockApi(page, withContent());
    await page.goto('/#/issues');
    await expect(filters(page)).toBeVisible();
    await filters(page).click();
    const seg = filtersPopover(page).locator('.tm-segmented');
    await expect(seg).toBeVisible();
    const look = await seg.evaluate((el) => ({
      wrap: getComputedStyle(el).flexWrap,
      rows: new Set([...el.children].map((b) => Math.round(b.getBoundingClientRect().top))).size,
    }));
    expect(look).toEqual({ wrap: 'nowrap', rows: 1 });
  });
});

test('an empty child keeps its place in a row while the others park and come back', async ({ page }) => {
  await mockApi(page, withContent());
  await page.goto('/#/settings');
  await expect(page.locator('#page-title')).toHaveText('Settings');
  await page.evaluate(async () => {
    const { overflowRow } = await import('/js/components/overflow-row.js');
    const row = document.createElement('div');
    row.id = 'probe-row';
    row.style.cssText = 'display: flex; gap: 8px; width: 260px; min-width: 0; overflow: hidden';
    const child = (k, text) => {
      const el = document.createElement(text ? 'button' : 'div');
      el.dataset.k = k;
      el.style.cssText = 'flex-shrink: 0';
      if (text) Object.assign(el, { type: 'button', textContent: text, style: 'flex-shrink: 0; width: 100px' });
      return el;
    };
    row.append(child('a', 'Alpha'), child('e', ''), child('b', 'Bravo'), child('c', 'Charlie'));
    document.getElementById('screen-mount').replaceChildren(row);
    window.__ov = overflowRow(row);
  });
  const look = () => page.evaluate(() => ({
    row: [...document.getElementById('probe-row').children].filter((c) => !c.hidden && c.dataset.k).map((c) => c.dataset.k),
    count: window.__ov.more.hidden ? null : window.__ov.more.querySelector('.overflow-more__count').textContent,
  }));
  await expect.poll(look).toEqual({ row: ['a', 'e'], count: '2' });
  // The empty child fills while Bravo and Charlie are parked; when room comes back they return after it, not before.
  await page.evaluate(() => {
    document.querySelector('[data-k="e"]').textContent = 'Echo';
    document.getElementById('probe-row').style.width = '1200px';
  });
  await expect.poll(look).toEqual({ row: ['a', 'e', 'b', 'c'], count: null });
  await page.evaluate(() => window.__ov.destroy());
});

test('at desktop width the Table parks nothing and Filters is hidden', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockApi(page, withContent());
  await page.goto('/#/table');
  await expect(page.locator('#topbar-actions [aria-label="Add task"]')).toBeVisible();
  await expect(filters(page)).toHaveCount(1);
  await expect(filters(page)).toBeHidden();
  // Parked controls leave the document, so the row is checked from what stays: everything, and a count of none.
  await expect(page.locator('#topbar-actions > .tm-subcount')).toBeVisible();
  await expect(filters(page).locator('.overflow-more__count')).toHaveText('0');
  expect(await rowFits(page)).toEqual({ overflow: 0, cut: [] });
});

test('a topbar control that grows in place is laid out again', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockApi(page, withContent());
  await page.goto('/#/table');
  await expect(filters(page)).toBeHidden();
  // A count's new text changes its width without adding or removing anything from the row.
  await page.locator('#topbar-actions > .tm-subcount').evaluate((el) => { el.textContent = 'a very long count '.repeat(12); });
  await expect(filters(page)).toBeVisible();
  await expect.poll(() => rowFits(page)).toEqual({ overflow: 0, cut: [] });
});

for (const route of ['#/kanban', '#/archived']) {
  test(`leaving ${route} raises no error`, async ({ page }) => {
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    await mockApi(page, withContent());
    await page.goto('/' + route);
    await expect(page.locator('#topbar-actions .tm-search')).toBeVisible();
    await page.evaluate(() => { location.hash = '#/settings'; });
    await expect(page.locator('#page-title')).toHaveText('Settings');
    expect(errors).toEqual([]);
  });
}
