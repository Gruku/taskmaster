// User intent: Epic detail reads like every detail page — its real status, progress from one function, its tasks grouped
// as links — by mouse, keyboard and phone, in both themes, and leaving it early leaves nothing behind.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD, ARCH_EPIC_FIXTURE, LONG_IDS_BOARD, epicDetailMocks, epicPayload } from './mock-fixtures.js';
import { epicSwatch } from '../js/lib/epics.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

// ARCH_EPIC_FIXTURE has 3 named components + 1 _unassigned bucket = 4 blocks total.
const EXPECTED_BLOCK_COUNT = 4;

test.beforeEach(async ({ page }) => { await page.emulateMedia({ reducedMotion: 'reduce' }); });
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

// A real backlog's volume on the board, with this spec's `viewer` epic listed last so it has a swatch too.
const LONG_WITH_VIEWER = { ...LONG_IDS_BOARD, epics: [...LONG_IDS_BOARD.epics, BOARD.epics.find((e) => e.id === 'viewer')] };

async function boot(page, { theme = 'dark', width = 1440, height = 900, route = '#/epic/viewer', ready = 'h1.ed-title', board, extra = {} } = {}) {
  await page.setViewportSize({ width, height });
  await mockApi(page, { ...epicDetailMocks({ theme }), ...(board ? { '/api/board': board, '/api/backlog': board } : {}), ...extra });
  await page.goto('/' + route);
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  if (ready) await expect(page.locator(ready).first()).toBeVisible();
}
// The colour `var(--cat-N)` paints in the page's theme.
const catColour = (page, n) => page.evaluate((n) => {
  const probe = document.createElement('span');
  probe.style.background = `var(--cat-${n})`;
  document.body.append(probe);
  const colour = getComputedStyle(probe).backgroundColor;
  probe.remove();
  return colour;
}, n);
const mountState = (page) => page.locator('#screen-mount').evaluate((el) => [el.className, el.getAttribute('style')]);
const taskRow = (scope, id) => scope.locator('.ed-task').filter({ has: scope.page().locator('.t-id', { hasText: id }) });
// A real click on the row's own content: the link's hit area covers the row, so the pointer lands on the link.
async function clickOn(page, locator) {
  const box = await locator.boundingBox();
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
}

test('the header reads id · Epics, the name, the lifecycle marker and the design tag — no back crumb, no Exploring beside active', async ({ page }) => {
  await boot(page);
  const meta = page.locator('.ed-meta');
  await expect(meta).toHaveText(/^viewer\s*·?\s*Epics/);
  await expect(meta.getByRole('link', { name: 'Epics' })).toHaveAttribute('href', '#/epics');
  await expect(page.locator('h1.ed-title')).toHaveText('Viewer re-skin');
  await expect(page.locator('.ed-markers .marker__word').first()).toHaveText('Active');
  await expect(page.locator('.ed-tag', { hasText: 'Design · Locked' })).toHaveCount(1);
  await expect(page.locator('.ed-done-when')).toContainText('All screens pass the audit.');
  await expect(page.locator('.ed-back')).toHaveCount(0);
  const text = await page.locator('#screen-mount').textContent();
  expect(text).not.toContain('‹');
  expect(text).not.toContain('🔒');
  await expect(page.getByText('Exploring')).toHaveCount(0);
  expect(await mountState(page)).toEqual(['screen-mount', null]);
});

test('the epic swatch is its categorical colour', async ({ page }) => {
  const extra = { '/api/epic/epic-01': epicPayload(LONG_IDS_BOARD, 'epic-01'), '/api/epic/epic-07': epicPayload(LONG_IDS_BOARD, 'epic-07') };
  await boot(page, { route: '#/epic/epic-01', board: LONG_IDS_BOARD, extra });
  const markers = page.locator('.ed-markers');
  const swatches = (scope) => scope.locator('.epic-swatch').evaluateAll((els) => els.map((el) => getComputedStyle(el).backgroundColor));
  const first = await catColour(page, epicSwatch('epic-01', LONG_IDS_BOARD.epics));
  // The board may land after the epic: the swatch follows it.
  await expect(markers.locator('.epic-swatch')).toHaveCount(1);
  expect(await swatches(markers)).toEqual([first]);
  await expect(markers.locator(':scope > :first-child')).toHaveClass(/\bepic-swatch\b/);

  // Six swatches, then round again: the 7th epic is the 1st one's colour.
  await page.evaluate(() => { location.hash = '#/epic/epic-07'; });
  await expect(page.locator('h1.ed-title')).toHaveText(LONG_IDS_BOARD.epics[6].name);
  await expect(markers.locator('.epic-swatch')).toHaveCount(1);
  expect(epicSwatch('epic-07', LONG_IDS_BOARD.epics)).toBe(1);
  expect(await swatches(markers)).toEqual([first]);

  // The same swatch in the detail modal.
  await page.evaluate(() => import('/js/lib/open-detail.js').then((m) => m.openDetail('epic', 'epic-07')));
  const dialog = page.getByRole('dialog', { name: LONG_IDS_BOARD.epics[6].name });
  await expect(dialog.locator('.ed-markers .epic-swatch')).toHaveCount(1);
  expect(await swatches(dialog.locator('.ed-markers'))).toEqual([first]);
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);

  // An epic the board does not list: no swatch, and its id and name are still there.
  await page.evaluate(() => { location.hash = '#/epic/viewer'; });
  await expect(page.locator('h1.ed-title')).toHaveText('Viewer re-skin');
  await expect(page.locator('.ed-id')).toHaveText('viewer');
  await expect(markers.locator('.marker__word').first()).toHaveText('Active');
  await expect(page.locator('.epic-swatch')).toHaveCount(0);

  // The next poll brings a board that lists it: the open page takes its swatch without a reload.
  const next = { ...LONG_WITH_VIEWER, revision: 'r-long-2' };
  await mockApi(page, { ...epicDetailMocks(), ...extra, '/api/board': next, '/api/backlog': next });
  await expect(markers.locator('.epic-swatch')).toHaveCount(1, { timeout: 10_000 });
  expect(await swatches(markers)).toEqual([await catColour(page, epicSwatch('viewer', LONG_WITH_VIEWER.epics))]);
  await expect(markers.locator(':scope > :first-child')).toHaveClass(/\bepic-swatch\b/);
});

test('progress says closed/total and the breakdown and legend agree with the task list', async ({ page }) => {
  await boot(page);
  await expect(page.locator('.ed-progress__label')).toHaveText('1/4 closed · 1 done');
  await expect(page.locator('.ed-seg')).toHaveCount(3);
  const bar = await page.evaluate(() => ({
    segs: [...document.querySelectorAll('.ed-seg')].map((s) => [...s.classList].find((c) => c.startsWith('ed-seg--'))),
    sum: [...document.querySelectorAll('.ed-seg')].reduce((n, s) => n + s.getBoundingClientRect().width, 0),
    width: document.querySelector('.ed-breakdown').clientWidth,
  }));
  expect(bar.segs).toEqual(['ed-seg--in-progress', 'ed-seg--todo', 'ed-seg--done']);
  expect(Math.abs(bar.sum - bar.width)).toBeLessThanOrEqual(2);
  await expect(page.locator('.ed-legend li .marker__word')).toHaveText(['In progress', 'Todo', 'Done']);
  await expect(page.locator('.ed-legend li .ed-legend__n')).toHaveText(['2', '1', '1']);
  expect(await page.locator('.ed-group').evaluateAll((els) => els.map((el) => el.dataset.status))).toEqual(['in-progress', 'todo', 'done']);
  expect(await page.locator('.ed-group[data-status="done"]').evaluate((el) => el.open)).toBe(false);
  const inProgress = page.locator('.ed-group[data-status="in-progress"]');
  await expect(inProgress.locator('.ed-group__n')).toHaveText('2');
  const links = inProgress.locator('.ed-task a.link-row__link');
  await expect(links).toHaveCount(2);
  await expect(links.nth(0)).toHaveAccessibleName('T-102 Re-skin the Kanban cards and columns');
  await expect(links.nth(0)).toHaveAttribute('href', '#/task/T-102');
  await expect(links.nth(1)).toHaveAccessibleName('T-103 Dashboard notes read as paper stickers');
  await expect(links.nth(1)).toHaveAttribute('href', '#/task/T-103');
});

test('a task row opens its task in the modal on the page, and peeks it inside the epic modal', async ({ page }) => {
  await boot(page);
  const row = taskRow(page.locator('.ed-tasks'), 'T-102');
  await clickOn(page, row.locator('.link-row__content .marker'));
  await expect(page.getByRole('dialog', { name: 'Re-skin the Kanban cards and columns' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(row.locator('a.link-row__link')).toBeFocused();

  await page.evaluate(() => { location.hash = '#/epics'; });
  await expect(page.locator('.epics-screen')).toBeVisible();
  await page.evaluate(() => import('/js/lib/open-detail.js').then((m) => m.openDetail('epic', 'viewer')));
  const epicDialog = page.getByRole('dialog', { name: 'Viewer re-skin' });
  await expect(epicDialog.locator('.ed-task').first()).toBeVisible();
  await clickOn(page, taskRow(epicDialog, 'T-102'));
  await expect(page.getByRole('dialog', { name: 'Re-skin the Kanban cards and columns' })).toBeVisible();
  await expect(page.locator('.modal')).toHaveCount(1);
});

test('every task row and group toggle is reached by Tab', async ({ page }) => {
  await boot(page);
  await page.locator('.ed-meta').getByRole('link', { name: 'Epics' }).focus();
  const where = () => page.evaluate(() => {
    const a = document.activeElement;
    if (!a || a === document.body) return null;
    if (a.matches('summary.ed-group__head')) return `group:${a.closest('.ed-group').dataset.status}`;
    if (a.closest('.ed-task')) return `task:${a.closest('.ed-task').querySelector('.t-id').textContent}`;
    return `${a.localName}:${(a.getAttribute('href') || a.textContent || '').trim().slice(0, 40)}`;
  });
  const seen = [];
  for (let i = 0; i < 40; i += 1) {
    await page.keyboard.press('Tab');
    const d = await where();
    if (!d) break;
    seen.push(d);
    if (d === 'a:/file/docs/specs/viewer.md') break;
  }
  const at = seen.indexOf('group:in-progress');
  expect(at, seen.join(' | ')).toBeGreaterThanOrEqual(0);
  expect(seen.slice(at, at + 6)).toEqual(['group:in-progress', 'task:T-102', 'task:T-103', 'group:todo', 'task:T-104', 'group:done']);
  expect(seen).not.toContain('task:T-101');   // Done is closed: its rows wait behind its toggle

  const done = page.locator('.ed-group[data-status="done"] > summary');
  await done.focus();
  await page.keyboard.press('Enter');
  expect(await page.locator('.ed-group[data-status="done"]').evaluate((el) => el.open)).toBe(true);
  await page.keyboard.press('Tab');
  expect(await where()).toBe('task:T-101');
});

test('an epic with no tasks says so instead of an empty bar', async ({ page }) => {
  await boot(page, { route: '#/epic/empty', ready: '.ed-progress .tm-empty__headline' });
  await expect(page.locator('.ed-progress .tm-empty__headline')).toHaveText('No tasks in this epic yet.');
  await expect(page.locator('.ed-breakdown')).toHaveCount(0);
  await expect(page.locator('.ed-group')).toHaveCount(0);
});

test('not found, a failure and Try again are state blocks with one action', async ({ page }) => {
  await boot(page, { route: '#/epic/nope', ready: '.tm-empty[data-state="missing"]' });
  const missing = page.locator('.tm-empty[data-state="missing"]');
  await expect(missing.locator('.tm-empty__label')).toHaveText('Not found');
  await expect(missing.locator('.tm-empty__headline')).toHaveText('There is no epic called nope.');
  await expect(missing.getByRole('link', { name: 'All epics' })).toHaveAttribute('href', '#/epics');
  await expect(missing.locator('a, button')).toHaveCount(1);
  await expect(page.locator('#page-title')).toHaveText('Epic');

  await page.evaluate(() => { location.hash = '#/epic/broken'; });
  const failed = page.locator('.tm-empty[data-state="error"]');
  await expect(failed.locator('.tm-empty__headline')).toHaveText('This epic could not be loaded.');
  await expect(failed.locator('a, button')).toHaveCount(1);
  const text = await page.locator('body').innerText();
  expect(text).not.toContain('sqlite3');
  expect(text).not.toContain('500');

  await page.route('**/api/epic/broken', (route) => route.fulfill({ json: epicPayload(BOARD, 'viewer', { id: 'broken', name: 'Recovered' }) }));
  await failed.getByRole('button', { name: 'Try again' }).click();
  await expect(page.locator('h1.ed-title')).toHaveText('Recovered');
  await expect(page.locator('.tm-empty[data-state="error"]')).toHaveCount(0);

  await page.evaluate(() => { location.hash = '#/epic'; });
  await expect(page.locator('#screen-mount .tm-empty__headline')).toHaveText('No epic selected.');
  await expect(page.locator('#screen-mount .tm-empty').getByRole('link', { name: 'All epics' })).toHaveAttribute('href', '#/epics');
});

test('Try again keeps the keyboard: on the loading block while it retries, then on the new Try again or the title', async ({ page }) => {
  await boot(page, { route: '#/epic/broken', ready: '.tm-empty[data-state="error"]' });
  // Each retry's answer is held open until the test releases it.
  let release;
  await page.route('**/api/epic/broken', async (route) => {
    const answer = await new Promise((resolve) => { release = resolve; });
    await route.fulfill(answer);
  });
  const focused = () => page.evaluate(() => {
    const a = document.activeElement;
    if (!a || a === document.body) return 'body';
    return a.matches('.tm-empty') ? `block:${a.dataset.state}` : `${a.localName}:${a.textContent.trim()}`;
  });
  const retry = page.locator('.tm-empty[data-state="error"]').getByRole('button', { name: 'Try again' });

  await retry.focus();
  await page.keyboard.press('Enter');
  const loading = page.locator('#screen-mount .tm-empty[data-state="loading"]');
  await expect(loading.locator('.tm-empty__headline')).toHaveText('Loading…');
  await expect(loading).toHaveAttribute('aria-busy', 'true');
  expect(await focused()).toBe('block:loading');
  await expect.poll(() => typeof release).toBe('function');
  release({ status: 500, json: { ok: false, error: 'sqlite3.OperationalError: database is locked' } });
  await expect(retry).toBeVisible();
  expect(await focused()).toBe('button:Try again');

  release = undefined;
  await page.keyboard.press('Enter');
  await expect(loading).toBeVisible();
  expect(await focused()).toBe('block:loading');
  await expect.poll(() => typeof release).toBe('function');
  release({ json: epicPayload(BOARD, 'viewer', { id: 'broken', name: 'Recovered' }) });
  await expect(page.locator('h1.ed-title')).toHaveText('Recovered');
  expect(await focused()).toBe('h1:Recovered');
});

test('leaving while the epic is still loading leaves nothing behind', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.addInitScript(() => {
    const live = new Set();
    window.__live = live;
    for (const name of ['ResizeObserver', 'MutationObserver']) {
      const Base = window[name];
      window[name] = class extends Base {
        observe(target, ...rest) { this.__targets = [...(this.__targets ?? []), target]; live.add(this); return super.observe(target, ...rest); }
        disconnect() { live.delete(this); return super.disconnect(); }
      };
    }
  });
  await boot(page, { route: '#/epics', ready: '.epic-row' });
  await page.route('**/api/epic/arch-test', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 600));
    await route.fulfill({ json: ARCH_EPIC_FIXTURE }).catch(() => {});
  });
  await page.evaluate(() => { location.hash = '#/epic/arch-test'; });
  await expect(page.locator('#screen-mount .tm-empty[data-state="loading"]')).toBeVisible();
  await page.evaluate(() => { location.hash = '#/table'; });
  await expect(page.locator('table.tbl')).toBeVisible();
  await page.waitForTimeout(1000);
  await expect(page.locator('.ed-root')).toHaveCount(0);
  await expect(page.locator('.ed-page')).toHaveCount(0);
  await expect(page.locator('.cd-map')).toHaveCount(0);
  await expect(page.locator('table.tbl')).toBeVisible();
  expect(await mountState(page)).toEqual(['screen-mount', null]);
  const watching = await page.evaluate(() => [...window.__live]
    .filter((o) => o.__targets.some((t) => t.matches?.('.ed-diagram__canvas, .cd-map') || t.closest?.('.cd-map'))).length);
  expect(watching).toBe(0);
  expect(errors).toEqual([]);
});

test.describe('Architecture Map (C2)', () => {
  let pageErrors;

  test.beforeEach(async ({ page }) => {
    pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(String(e)));
    // Gate on the diagram's own output (.cd-map), not just the .ed-diagram section shell — the shell is appended
    // before mountComponentDiagram runs, so waiting on it alone would not catch a mount that bails early.
    await boot(page, { route: '#/epic/arch-test', ready: '.ed-diagram .cd-map' });
  });

  test.afterEach(() => {
    // No runtime JS errors during any architecture-map interaction (drawEdges runs via requestAnimationFrame).
    expect(pageErrors).toEqual([]);
  });

  test('svg.cd-connectors is present inside .ed-diagram', async ({ page }) => {
    await expect(page.locator('.ed-diagram svg.cd-connectors')).toBeVisible();
  });

  test('cd-block count matches fixture component count + unassigned bucket', async ({ page }) => {
    expect(await page.locator('.ed-diagram .cd-block').count()).toBe(EXPECTED_BLOCK_COUNT);
  });

  test('cd-edge paths exist for each after-edge in the fixture (2 edges)', async ({ page }) => {
    // Edges: ingest→thumb and thumb→cdn.
    expect(await page.locator('.ed-diagram path.cd-edge').count()).toBe(2);
  });

  test('unassigned bucket renders a cd-block--unassigned block', async ({ page }) => {
    await expect(page.locator('.ed-diagram .cd-block--unassigned')).toBeVisible();
  });

  test('.ed-diagram appears in DOM before the side column within the epic detail', async ({ page }) => {
    const order = await page.evaluate(() => {
      const diagram = document.querySelector('.ed-diagram');
      const side = document.querySelector('.ed-side');
      if (!diagram || !side) return 'missing';
      return (diagram.compareDocumentPosition(side) & Node.DOCUMENT_POSITION_FOLLOWING) ? 'diagram-before-side' : 'diagram-after-side';
    });
    expect(order).toBe('diagram-before-side');
  });
});

test('Attention and Docs are real links in words, not hue', async ({ page }) => {
  await boot(page);
  const side = page.locator('.ed-side');
  await expect(side.locator('a[href="#/task/T-102"]')).toHaveCount(1);
  await expect(side.locator('.ed-attn .marker__word')).toHaveText('Has blockers');
  const doc = side.locator('.ed-docs').getByRole('link', { name: 'spec' });
  await expect(doc).toHaveAttribute('href', '/file/docs/specs/viewer.md');
  await expect(doc).toHaveAttribute('target', '_blank');
  await expect(doc).toHaveAttribute('rel', 'noopener');
  const colors = await page.evaluate(() => {
    const probe = document.createElement('span');
    probe.style.color = 'var(--foreground-default)';
    document.querySelector('.ed-side').append(probe);
    const want = getComputedStyle(probe).color;
    probe.remove();
    return {
      want,
      attention: getComputedStyle(document.querySelector('.ed-side a[href="#/task/T-102"]')).color,
      docs: getComputedStyle(document.querySelector('.ed-docs a')).color,
    };
  });
  expect(colors.attention).toBe(colors.want);
  expect(colors.docs).toBe(colors.want);
});

test('at 390 the page is one column and nothing scrolls sideways', async ({ page }) => {
  await boot(page, { width: 390, height: 844, route: '#/epic/big' });
  const look = await page.evaluate(() => {
    const main = document.querySelector('.ed-main').getBoundingClientRect();
    const side = document.querySelector('.ed-side').getBoundingClientRect();
    return {
      pageX: document.scrollingElement.scrollWidth - innerWidth,
      sideBelow: side.top >= main.bottom - 1,
      lostWords: [...document.querySelectorAll('.ed-task .truncate')].filter((el) => el.title !== el.textContent).length,
      rows: document.querySelectorAll('.ed-task').length,
    };
  });
  expect(look).toEqual({ pageX: 0, sideBelow: true, lostWords: 0, rows: 60 });
});

for (const theme of ['dark', 'light']) for (const [w, h] of [[1440, 900], [390, 844]]) {
  test(`axe (${theme}, ${w}): Epic detail passes contrast, nesting and aria on the page and in the modal`, async ({ page }) => {
    const run = (selector, exclude = []) => page.evaluate(async ([sel, skip]) => {
      const aria = window.axe.getRules().map((r) => r.ruleId).filter((id) => id.startsWith('aria-'));
      return (await window.axe.run({ include: [[sel]], exclude: skip.map((x) => [x]) },
        { runOnly: { type: 'rule', values: ['color-contrast', 'nested-interactive', 'list', 'listitem', ...aria] }, resultTypes: ['violations'] }))
        .violations.map((x) => `${x.id}: ${x.nodes.map((n) => n.target.join(' ')).join(' | ')}`);
    }, [selector, exclude]);

    await boot(page, { theme, width: w, height: h, board: LONG_WITH_VIEWER });
    await expect(page.locator('.ed-markers .epic-swatch')).toHaveCount(1);
    await page.evaluate(axeSource);
    // Done is closed by default; open it so its rows are checked too.
    await page.locator('.ed-group[data-status="done"]').evaluate((el) => { el.open = true; });
    expect(await run('#screen-mount'), 'page').toEqual([]);

    await page.evaluate(() => { location.hash = '#/epic/arch-test'; });
    await expect(page.locator('.ed-diagram .cd-block').first()).toBeVisible();
    // The map's blocks and 3a's re-skinned Kanban cards inside them.
    expect(await run('#screen-mount'), 'architecture map').toEqual([]);
    expect(await page.locator('.cd-block').evaluateAll((els) => [...new Set(els.map((el) => getComputedStyle(el).boxShadow))])).toEqual(['none']);

    await page.evaluate(() => import('/js/lib/open-detail.js').then((m) => m.openDetail('epic', 'viewer')));
    const dialog = page.getByRole('dialog', { name: 'Viewer re-skin' });
    await expect(dialog.locator('.ed-task').first()).toBeVisible();
    expect(await run('[role="dialog"]'), 'modal').toEqual([]);
  });
}
