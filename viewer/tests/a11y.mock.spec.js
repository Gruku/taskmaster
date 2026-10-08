// User intent: the release gate for accessibility — every route, in both themes and at both widths, is read by axe and by
// the audit's probes, so a contrast, label, landmark, mouse-only or tiny-target regression cannot ship unnoticed.
import { test, expect } from '@playwright/test';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { mockApi, unmockedWrites, unmockedReads } from './mock-api.js';
import { ROUTES } from './route-fixtures.js';
import { pointerOnlyTargets, smallTouchTargets } from './a11y-probe.js';

const AXE = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');
// Spec §8's list. "Landmark failures" are axe's landmark-* rules; `region` (a best-practice rule that fires on the
// banner and modal hosts by design) is not part of the gate.
const GATE = ['color-contrast', 'label', 'select-name', 'nested-interactive', 'scrollable-region-focusable',
  'landmark-one-main', 'landmark-no-duplicate-main', 'landmark-unique', 'landmark-main-is-top-level',
  'landmark-complementary-is-top-level', 'landmark-banner-is-top-level', 'landmark-contentinfo-is-top-level',
  'landmark-no-duplicate-banner', 'landmark-no-duplicate-contentinfo'];
const WIDTHS = [['desktop', 1440, 900], ['phone', 390, 844]];

async function openRoute(page, r, theme) {
  await page.addInitScript((t) => { try { localStorage.setItem('tm.theme', t); } catch { /* storage unavailable */ } }, theme);
  await mockApi(page, r.build({ theme }));
  await page.goto(`/${r.route}`);
  await expect(page.locator(r.ready).first()).toBeVisible();
  await r.open?.(page);
  await expect(page.locator('#screen-mount [aria-busy="true"]')).toHaveCount(0);
  if (!r.state) await expect(page.locator('.tm-empty[data-state="error"]')).toHaveCount(0);
  await page.evaluate(() => document.fonts.ready);
}

test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

for (const r of ROUTES) for (const theme of ['dark', 'light']) for (const [size, width, height] of WIDTHS) {
  test(`${r.name} · ${theme} · ${size}`, async ({ page }) => {
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.setViewportSize({ width, height });
    await openRoute(page, r, theme);
    expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe(theme);

    await page.addScriptTag({ content: AXE });
    const { missing, violations } = await page.evaluate(async (gate) => {
      const known = new Set(axe.getRules().map((x) => x.ruleId));
      const res = await axe.run(document, { runOnly: { type: 'rule', values: gate.filter((id) => known.has(id)) }, resultTypes: ['violations'] });
      return { missing: gate.filter((id) => !known.has(id)),
        violations: res.violations.map((v) => `${v.id}: ${v.nodes.slice(0, 8).map((n) => n.target.join(' ')).join(' | ')}`) };
    }, GATE);
    expect(missing, 'every gate rule exists in this axe version').toEqual([]);
    expect(violations).toEqual([]);
    expect(await page.evaluate(pointerOnlyTargets)).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    if (width <= 768) expect(await page.evaluate(smallTouchTargets)).toEqual([]);
    expect(unmockedReads(page)).toEqual([]);
    expect(errors).toEqual([]);
  });
}

// Spec §4: the topbar is one of exactly two heights — one row, or one row plus row 2 — on every route; and a screen's
// count reads "n <noun>", followed by " · m visible" only while something narrows the list — never "m of n"
// (plan 3 ruling; with nothing narrowed, as here, a screen may show either form — Task 9 checks when the suffix shows).
for (const [size, width, height] of WIDTHS) {
  test(`topbar · two heights and one count wording · ${size}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    const seen = [];
    for (const r of ROUTES.filter((x) => !x.open)) {
      await page.goto('about:blank');
      await page.unrouteAll({ behavior: 'ignoreErrors' });
      await openRoute(page, r, 'dark');
      seen.push(await page.evaluate((name) => {
        const row2 = document.querySelector('.topbar-row2');
        const twoRows = !!row2 && row2.getBoundingClientRect().height > 0;
        return { name, twoRows, h: Math.round(document.getElementById('topbar').getBoundingClientRect().height),
          count: document.getElementById('topbar-count')?.textContent.trim() ?? '' };
      }, r.name));
    }
    for (const twoRows of [false, true]) {
      const heights = [...new Set(seen.filter((s) => s.twoRows === twoRows).map((s) => s.h))];
      expect(heights.length, `${twoRows ? 'two-row' : 'one-row'} topbars: ${JSON.stringify(seen)}`).toBeLessThanOrEqual(1);
    }
    for (const s of seen.filter((x) => x.count)) {
      expect(s.count, s.name).toMatch(/^\d+ \S.*?( · \d+ visible)?$/);
      expect(s.count, s.name).not.toMatch(/\bof\b/);
    }
  });
}
