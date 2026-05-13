import { test, expect } from '@playwright/test';

// Verifies the layout fixes from v3-polish-045:
//   - .main fits the viewport exactly (no clipped padding)
//   - topbar is sticky at the top of .main
//   - .screen-mount never scrolls itself
//   - Kanban: .kanban-page fills .screen-mount, only column bodies scroll
//   - Other screens: their root is the scroller, topbar stays put

const VIEWPORT_H = 720;

test.use({ viewport: { width: 1280, height: VIEWPORT_H } });

test('shell fits viewport — .main height == viewport height', async ({ page }) => {
  await page.goto('/v3#/dashboard');
  await page.waitForLoadState('networkidle');
  const dims = await page.evaluate(() => {
    const main = document.querySelector('.main');
    const shell = document.querySelector('.shell');
    return {
      mainBoxHeight: main.getBoundingClientRect().height,
      shellBoxHeight: shell.getBoundingClientRect().height,
      mainBoxStyle: getComputedStyle(main).boxSizing,
    };
  });
  expect(dims.mainBoxStyle).toBe('border-box');
  expect(dims.mainBoxHeight).toBe(VIEWPORT_H);
  expect(dims.shellBoxHeight).toBe(VIEWPORT_H);
});

test('topbar is sticky and stays at top of .main on any screen', async ({ page }) => {
  await page.goto('/v3#/issues');
  await page.waitForLoadState('networkidle');
  const probe = await page.evaluate(() => {
    const tb = document.querySelector('.topbar');
    const cs = getComputedStyle(tb);
    return {
      position: cs.position,
      top: cs.top,
      zIndex: cs.zIndex,
      hasBackground: cs.backgroundColor !== 'rgba(0, 0, 0, 0)',
    };
  });
  expect(probe.position).toBe('sticky');
  expect(probe.top).toBe('0px');
  expect(probe.hasBackground).toBe(true);
});

test('screen-mount itself does not scroll', async ({ page }) => {
  await page.goto('/v3#/issues');
  await page.waitForLoadState('networkidle');
  const probe = await page.evaluate(() => {
    const sm = document.querySelector('.screen-mount');
    const cs = getComputedStyle(sm);
    return { overflow: cs.overflow, overflowY: cs.overflowY };
  });
  // overflow shorthand may resolve to "hidden" or "hidden hidden" depending on browser
  expect(probe.overflow.startsWith('hidden')).toBe(true);
});

test('kanban: page fills screen-mount, board never scrolls, column bodies do', async ({ page }) => {
  await page.goto('/v3#/kanban');
  await page.waitForLoadState('networkidle');
  // give it a tick for column layout
  await page.waitForTimeout(200);
  const layout = await page.evaluate(() => {
    const sm = document.querySelector('.screen-mount');
    const page = document.querySelector('.kanban-page');
    const board = document.querySelector('.kanban-board');
    if (!page || !board) return { error: 'kanban not rendered' };
    const bodies = Array.from(document.querySelectorAll('.kanban-col-body'));
    return {
      smHeight: sm.getBoundingClientRect().height,
      pageHeight: page.getBoundingClientRect().height,
      pageScrollH: page.scrollHeight,
      pageClientH: page.clientHeight,
      boardOverflow: getComputedStyle(board).overflow,
      bodyCount: bodies.length,
      bodyOverflowYs: bodies.map(b => getComputedStyle(b).overflowY),
    };
  });
  expect(layout.error).toBeUndefined();
  // kanban-page fills screen-mount (within 1px tolerance for borders)
  expect(Math.abs(layout.pageHeight - layout.smHeight)).toBeLessThanOrEqual(1);
  // kanban-page does NOT scroll itself
  expect(layout.pageScrollH).toBeLessThanOrEqual(layout.pageClientH + 1);
  // column bodies are the scrollers
  expect(layout.bodyCount).toBeGreaterThan(0);
  for (const ov of layout.bodyOverflowYs) {
    expect(ov).toBe('auto');
  }
});

test('issues/lessons/dashboard scroll on their root, not via window', async ({ page }) => {
  const cases = [
    { hash: '#/issues',    rootSelector: '.screen-mount > .issues' },
    { hash: '#/lessons',   rootSelector: '.screen-mount > .lessons' },
    { hash: '#/dashboard', rootSelector: '.screen-mount.dash' },
  ];
  for (const c of cases) {
    await page.goto(`/v3${c.hash}`);
    await page.waitForLoadState('networkidle');
    await page.waitForSelector(c.rootSelector, { timeout: 5000 });
    const probe = await page.evaluate((sel) => {
      const root = document.querySelector(sel);
      const cs = getComputedStyle(root);
      return {
        rootClass: root.className,
        overflowY: cs.overflowY,
        windowScrollY: window.scrollY,
      };
    }, c.rootSelector);
    const ok = ['auto', 'scroll'].includes(probe.overflowY);
    if (!ok) throw new Error(`overflowY="${probe.overflowY}" on root=${probe.rootClass} for ${c.hash}`);
    expect(probe.windowScrollY).toBe(0);
  }
});
