// User intent: in a real browser a Kanban card is a link — Enter opens the task and Escape hands focus back, a modified
// click is the browser's, the copy button only copies — a long title stays in three lines with the id unbroken at
// phone width, a recent card says "New" without a glow, and the cards pass axe in both themes.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD, DETAIL_TASK, RICH_RELATED, taskDetail, longBoard } from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
});
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

async function board(page, { theme = 'dark', board = BOARD, viewport } = {}) {
  if (viewport) await page.setViewportSize(viewport);
  await mockApi(page, {
    '/api/viewer/prefs': { theme, ui: {}, screens: {} },
    '/api/board': board, '/api/backlog': board, '/api/bugs': [],
    '/api/task/T-102/detail': taskDetail(DETAIL_TASK, 't1:fixture', RICH_RELATED),
  });
  await page.goto('/#/kanban');
  await expect(page.locator('.card-task').first()).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
}

const card = (page, id) => page.locator(`.card-task[data-task-id="${id}"]`);
const linkOf = (page, id) => card(page, id).locator(':scope > .link-row__link');

test('a card is a link: Enter opens the task, Escape hands focus back, Ctrl+click is the browser\'s', async ({ page }) => {
  await board(page);
  const link = linkOf(page, 'T-102');
  await link.focus();
  await page.keyboard.press('Enter');
  const dialog = page.locator('.modal--detail');
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('.modal-eyebrow')).toHaveText('T-102');
  await page.keyboard.press('Escape');
  await expect(page.locator('.modal')).toHaveCount(0);
  await expect(link).toBeFocused();

  const opened = page.context().waitForEvent('page');
  await link.click({ modifiers: ['Control'] });
  const tab = await opened;
  await expect(tab).toHaveURL(/#\/task\/T-102$/);
  await tab.close();
  await page.waitForTimeout(300);
  await expect(page.locator('.modal')).toHaveCount(0);
  await expect(page).toHaveURL(/#\/kanban$/);
});

test('the copy-id control copies and never opens the task', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (t) => { window.__copied = t; } } });
  });
  await board(page);
  await card(page, 'T-102').locator('.card-id').click();
  await expect.poll(() => page.evaluate(() => window.__copied)).toBe('T-102');
  // Give a wrongly opened dialog the time it takes a real one to load before saying there is none.
  await page.waitForTimeout(300);
  await expect(page.locator('.modal')).toHaveCount(0);
});

test('a 120-character title is clamped to three lines and the id never breaks', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 390, height: 844 } });
  const cards = await page.locator('.card-task').evaluateAll((els) => els.slice(0, 10).map((el) => {
    const title = el.querySelector('.card-title');
    const cs = getComputedStyle(title);
    return {
      id: el.dataset.taskId,
      titleLength: title.title.length,
      titleHeight: title.getBoundingClientRect().height,
      lineHeight: parseFloat(cs.lineHeight),
      idRects: el.querySelector('.card-id .truncate').getClientRects().length,
      scrollWidth: el.scrollWidth,
      clientWidth: el.clientWidth,
    };
  }));
  expect(cards).toHaveLength(10);
  for (const c of cards) {
    expect(c.titleLength, c.id).toBe(120);
    expect(c.titleHeight, `${c.id} title height`).toBeLessThanOrEqual(3 * c.lineHeight + 1);
    expect(c.idRects, `${c.id} id rects`).toBe(1);
    expect(c.scrollWidth, `${c.id} overflows`).toBeLessThanOrEqual(c.clientWidth);
  }
});

test('a recent card has a strong border and a New tag, and no glow', async ({ page }) => {
  const recentBoard = structuredClone(BOARD);
  recentBoard.tasks.find((t) => t.id === 'T-102').started = new Date(Date.now() - 3_600_000).toISOString();
  // A card wide enough for all of line 1; in a narrow column "New" wraps below the id (next test).
  await board(page, { board: recentBoard, viewport: { width: 390, height: 844 } });
  await page.mouse.move(0, 0);
  const recent = card(page, 'T-102');
  await expect(recent).toHaveClass(/\brecent\b/);
  const tag = recent.locator('.card-new');
  await expect(tag).toHaveText('New');
  await expect(tag).toBeVisible();
  const tagBox = await tag.boundingBox();
  const idBox = await recent.locator('.card-id').boundingBox();
  // Line 1: the tag's box and the id's box share a vertical band.
  expect(tagBox.y).toBeLessThan(idBox.y + idBox.height);
  expect(idBox.y).toBeLessThan(tagBox.y + tagBox.height);

  const strong = await page.evaluate(() => {
    const probe = document.createElement('div');
    probe.style.border = '1px solid var(--border-strong)';
    document.body.append(probe);
    const color = getComputedStyle(probe).borderTopColor;
    probe.remove();
    return color;
  });
  const style = await recent.evaluate((el) => ({ border: getComputedStyle(el).borderTopColor, shadow: getComputedStyle(el).boxShadow }));
  expect(style).toEqual({ border: strong, shadow: 'none' });
  await expect(card(page, 'T-101').locator('.card-new')).toHaveCount(0);
});

// The fixture board with every card part in use: a recent card, a bundle of two, a note, a blocker, a branch and a doc.
function richBoard() {
  const full = structuredClone(BOARD);
  Object.assign(full.tasks.find((t) => t.id === 'T-102'), {
    started: new Date(Date.now() - 3_600_000).toISOString(), branch: 'feat/kanban-cards', docs: { spec: 'docs/spec.md' },
    estimate: 'M', spec_review: 'warn', bundle: 'cards', lane: 'full', gate_state: 'review-gate:pending',
  });
  Object.assign(full.tasks.find((t) => t.id === 'T-103'), { bundle: 'cards', created: new Date(Date.now() - 11 * 86_400_000).toISOString() });
  Object.assign(full.tasks.find((t) => t.id === 'T-107'), { human_action: 'Check the light theme by eye', started: new Date(Date.now() - 8 * 86_400_000).toISOString() });
  Object.assign(full.tasks.find((t) => t.id === 'T-106'), { blockers_count: 1 });
  return full;
}

// Per card: is the id cut, and does any element of the card reach past the card's own box?
const measureCards = (page) => page.locator('.card-task').evaluateAll((cards) => cards.map((card) => {
  const box = card.getBoundingClientRect();
  const id = card.querySelector('.card-id');
  const idText = id.querySelector('.truncate');
  const outside = [...card.querySelectorAll('*')].filter((el) => {
    if (el.closest('.card-sr') || !el.getClientRects().length) return false;
    const r = el.getBoundingClientRect();
    return r.left < box.left - 0.5 || r.right > box.right + 0.5 || r.top < box.top - 0.5 || r.bottom > box.bottom + 0.5;
  }).map((el) => `${el.localName}.${String(el.getAttribute('class') || '').trim().split(/\s+/).join('.')}`);
  return {
    id: card.dataset.taskId,
    inBundle: !!card.closest('.bundle-frame'),
    idCut: idText.scrollWidth > idText.clientWidth || id.scrollWidth > id.clientWidth,
    priOffLine: (() => {
      const pri = card.querySelector('.card-pri');
      if (!pri) return false;
      const a = id.getBoundingClientRect();
      const b = pri.getBoundingClientRect();
      return Math.abs((a.top + a.bottom) / 2 - (b.top + b.bottom) / 2) >= 4;
    })(),
    overflow: card.scrollWidth > card.clientWidth,
    outside,
  };
}));

for (const [name, make] of [['the fixture board', richBoard], ['the long board', longBoard]]) {
  test(`1440, five columns, ${name}: no card's id is cut and nothing leaves its card, in a bundle frame too`, async ({ page }) => {
    await board(page, { board: make(), viewport: { width: 1440, height: 900 } });
    await expect(page.locator('.kanban-col')).toHaveCount(5);
    const cards = await measureCards(page);
    expect(cards.length).toBeGreaterThan(name === 'the long board' ? 200 : 6);
    expect(cards.some((c) => c.inBundle), 'a bundle frame is measured').toBe(true);
    for (const c of cards) {
      expect(c.idCut, `${c.id}: id cut`).toBe(false);
      expect(c.priOffLine, `${c.id}: priority not beside the id`).toBe(false);
      expect(c.overflow, `${c.id}: card scrolls sideways`).toBe(false);
      expect(c.outside, `${c.id}: past the card`).toEqual([]);
    }
  });
}

test('in a narrow card "New" and the age give way: they wrap below the id, which keeps its priority beside it', async ({ page }) => {
  await board(page, { board: richBoard(), viewport: { width: 1440, height: 900 } });
  const recent = card(page, 'T-102');
  const [id, pri, tag, title] = await Promise.all(['.card-id', '.card-pri', '.card-new', '.card-title']
    .map((s) => recent.locator(s).boundingBox()));
  expect(Math.abs(pri.y + pri.height / 2 - (id.y + id.height / 2)), 'priority on the id\'s line').toBeLessThan(4);
  expect(tag.y, '"New" on a line below the id').toBeGreaterThanOrEqual(id.y + id.height - 1);
  expect(tag.y + tag.height, '"New" above the title').toBeLessThanOrEqual(title.y + 1);
});

test('1440, the long board: the estimate shares the epic\'s line and the bundle slug stays on one', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  const lines = await page.locator('.card-task').evaluateAll((cards) => cards
    .filter((c) => c.querySelector('.card-epic') && c.querySelector('.card-estimate'))
    .map((c) => {
      const a = c.querySelector('.card-epic').getBoundingClientRect();
      const b = c.querySelector('.card-estimate').getBoundingClientRect();
      return { id: c.dataset.taskId, apart: Math.abs((a.top + a.bottom) / 2 - (b.top + b.bottom) / 2) };
    }));
  expect(lines.length).toBeGreaterThan(100);
  for (const l of lines) expect(l.apart, `${l.id}: estimate off the epic's line`).toBeLessThan(2);
  const slug = page.locator('.bundle-frame-head .slug').first();
  expect(await slug.evaluate((el) => el.getClientRects().length)).toBe(1);
  await expect(slug).toHaveAttribute('title', 'long-bundle-slug-alpha');
});

test('the doc button opens the primary doc and never the task', async ({ page }) => {
  await page.addInitScript(() => { window.open = (...args) => { (window.__opened ??= []).push(args); return null; }; });
  await board(page, { board: richBoard() });
  await card(page, 'T-102').locator('.card-docs').click();
  expect(await page.evaluate(() => window.__opened)).toEqual([['docs/spec.md', '_blank', 'noopener']]);
  await page.waitForTimeout(300);
  await expect(page.locator('.modal')).toHaveCount(0);
});

for (const theme of ['light', 'dark']) {
  test(`axe (${theme}): cards have no contrast, nested-interactive or aria violation`, async ({ page }) => {
    await board(page, { theme, board: richBoard() });
    await expect(page.locator('.bundle-frame')).toHaveCount(1);
    await page.evaluate(axeSource);
    const result = await page.evaluate(() => window.axe.run(document.querySelector('.kanban-board'), {
      runOnly: { type: 'rule', values: ['color-contrast', 'nested-interactive', 'aria-allowed-attr', 'aria-valid-attr-value', 'link-name', 'button-name'] },
      resultTypes: ['violations'],
    }));
    expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}

// Task 5: the phase strip.
test('every phase is named in full or in its title, and the current one is wider', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  const r = await page.locator('.phase-strip').evaluate((strip) => {
    const chips = [...strip.querySelectorAll('.phase-chip[data-value^="P"]')].filter((c) => c.isConnected && c.offsetParent);
    const named = chips.every((c) => {
      const n = c.querySelector('.phase-chip__name');
      return n.scrollWidth <= n.clientWidth || c.title.startsWith(n.textContent);
    });
    const w = (c) => c.getBoundingClientRect().width;
    const cur = strip.querySelector('.phase-chip--current');
    const fut = chips.filter((c) => c.classList.contains('phase-chip--future')).map(w);
    return { n: chips.length, named, wider: !!cur && fut.every((x) => w(cur) > x) };
  });
  expect(r.n).toBeGreaterThan(0);
  expect(r.named).toBe(true);
  expect(r.wider).toBe(true);
});

test('at 390 the phase row is one line; More lists the rest and picking one filters the board', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 390, height: 844 } });
  const tops = await page.locator('.phase-strip__items > *').evaluateAll((els) => [...new Set(els.filter((e) => e.offsetParent).map((e) => e.offsetTop))]);
  // At most two lines: the current phase keeps a readable name, so Archived and More may take a second line (fix round 1).
  expect(tops.length).toBeLessThanOrEqual(2);
  const more = page.locator('.phase-strip .overflow-more');
  await expect(more).toBeVisible();
  await more.click();
  await page.locator('.popover [data-value="P5"]').click();
  await expect(page.locator('.card-task')).toHaveCount(longBoard().tasks.filter((t) => t.phase === 'P5').length);
});

test('the archived menu picks an archived phase from the keyboard', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  const btn = page.locator('.phase-archived');
  await btn.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.popover[role="menu"] [role="menuitemradio"]').first()).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(btn).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.card-task')).toHaveCount(0);
});

for (const theme of ['dark', 'light']) {
  test(`${theme}: the phase strip, its More and the archived menu pass axe`, async ({ page }) => {
    await board(page, { theme, board: longBoard(), viewport: { width: 390, height: 844 } });
    await page.evaluate(axeSource);
    const run = (sel) => page.evaluate((s) => window.axe.run(document.querySelector(s), { resultTypes: ['violations'] })
      .then((r) => r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(' ')}`)), sel);
    expect(await run('.phase-strip')).toEqual([]);
    await page.locator('.phase-strip .overflow-more').click();
    expect(await run('.popover')).toEqual([]);
    await page.keyboard.press('Escape');
    await page.locator('.phase-archived').click();
    expect(await run('.popover[role="menu"]')).toEqual([]);
  });
}

// Task 6: the filter bar.
const rowChips = (page, row) => page.locator(`.kanban-filters__${row} .chip-row__chips > .chip`);
const priChip = (page, word) => rowChips(page, 'priority').filter({ has: page.locator('.chip__label', { hasText: new RegExp(`^${word}$`) }) });
const epicOption = (page, id) => page.locator('.epic-option').filter({ has: page.locator(`a[href="#/epic/${id}"]`) });
const searchBox = (page) => page.locator('.tm-search input');
const bumpBoard = (page) => page.evaluate(() => import('/js/store.js').then(({ store }) => {
  const next = structuredClone(store.getBacklog());
  next.revision = `r-${Date.now()}`;
  store.setBoard(next);
}));
const visibleTops = (loc) => loc.evaluateAll((els) => [...new Set(els.filter((e) => e.offsetParent).map((e) => e.offsetTop))]);

test('the epic row is one line with More at 1440 and at 390', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.locator('.kanban-filters__epic .overflow-more')).toBeVisible();
    expect(await visibleTops(rowChips(page, 'epic'))).toHaveLength(1);
    expect(await visibleTops(rowChips(page, 'priority'))).toHaveLength(1);
  }
});

test('an epic\'s count is the same on its chip and in Epic options, and the label says what it counts', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  // A chip that does not fit is parked in the Epic row's More list.
  const chip = page.locator('.chip[data-value="epic-03"] .chip__count');
  const parked = !(await chip.count());
  if (parked) await page.locator('.kanban-filters__epic .overflow-more').click();
  const onChip = await chip.textContent();
  if (parked) await page.keyboard.press('Escape');
  await page.locator('.epic-options-btn').click();
  await expect(epicOption(page, 'epic-03').locator('.epic-option__count')).toHaveText(onChip);
  expect(await page.locator('.kanban-filters__epic .chip-row__label').getAttribute('title')).toContain('open tasks');
});

test('priority chips are words and filter the board', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  expect(await rowChips(page, 'priority').locator('.chip__label').allTextContents()).toEqual(['Critical', 'High', 'Medium', 'Low']);
  const words = () => page.locator('.card-task .card-pri .marker__word').allTextContents();
  await priChip(page, 'High').click();
  await expect.poll(async () => [...new Set(await words())]).toEqual(['High']);
  await priChip(page, 'Critical').click({ modifiers: ['Shift'] });
  await expect(priChip(page, 'Critical')).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(async () => [...new Set(await words())].sort()).toEqual(['Critical', 'High']);
});

test('no control sits inside a chip', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  await expect(page.locator('.chip a, .chip button')).toHaveCount(0);
  await page.evaluate(axeSource);
  const v = await page.evaluate(() => window.axe.run(document.querySelector('.kanban-filterbar'),
    { runOnly: { type: 'rule', values: ['nested-interactive'] }, resultTypes: ['violations'] }).then((r) => r.violations.length));
  expect(v).toBe(0);
});

test('Clear filters appears with a filter and clears everything', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  const clear = page.locator('.kanban-clear');
  await expect(clear).toBeHidden();
  await priChip(page, 'High').click();
  await searchBox(page).fill('T-10');
  await expect(clear).toBeVisible();
  await clear.click();
  expect(await page.locator('.kanban-filters .chip[aria-pressed="true"]').evaluateAll((els) => els.map((e) => e.dataset.value))).toEqual(['__all__']);
  await expect(searchBox(page)).toHaveValue('');
  await expect(clear).toBeHidden();
  await expect(searchBox(page)).toBeFocused();
});

test('pinning an epic in Epic options puts it first and saves it', async ({ page }) => {
  const puts = [];
  page.on('request', (r) => { if (r.method() === 'PUT' && r.url().includes('/api/viewer/prefs')) puts.push(r.postDataJSON()); });
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  await page.locator('.epic-options-btn').click();
  const pin = epicOption(page, 'epic-20').locator('.epic-option__pin');
  await pin.click();
  await expect(pin).toHaveAttribute('aria-pressed', 'true');
  await expect(rowChips(page, 'epic').nth(1)).toHaveAttribute('data-value', 'epic-20');
  await expect(rowChips(page, 'epic').nth(1).locator('.chip__label')).toHaveText(/^Epic 20/);
  const pinned = (o) => (o && typeof o === 'object' ? (o.kanban?.pinnedEpics ?? Object.values(o).map(pinned).find(Boolean)) : undefined);
  await expect.poll(() => pinned(puts.at(-1))).toEqual(['epic-20']);
  await expect(page.locator('.epic-options')).toBeVisible();
});

test('a poll keeps focus on a pressed chip and keeps Epic options open', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  const high = priChip(page, 'High');
  await high.click();
  await high.focus();
  await bumpBoard(page);
  await expect(high).toBeFocused();
  await expect(high).toHaveAttribute('aria-pressed', 'true');
  await page.locator('.epic-options-btn').click();
  await expect(page.locator('.epic-options')).toBeVisible();
  await bumpBoard(page);
  await page.waitForTimeout(200);
  await expect(page.locator('.epic-options')).toBeVisible();
});

test('leaving the board with Epic options open leaves nothing behind', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  await page.locator('.epic-options-btn').click();
  await expect(page.locator('.epic-options')).toBeVisible();
  await page.evaluate(() => { location.hash = '#/table'; });
  await expect(page.locator('table.tbl')).toBeVisible();
  await expect(page.locator('.popover')).toHaveCount(0);
  expect(await page.evaluate(() => import('/js/components/popover.js').then((m) => m.openPopoverCount()))).toBe(0);
  expect(errors).toEqual([]);
  await page.evaluate(() => { location.hash = '#/kanban'; });
  await expect(page.locator('.card-task').first()).toBeVisible();
  const more = page.locator('.kanban-filters__epic .overflow-more');
  await expect(more).toBeVisible();
  const shown = await more.locator('.overflow-more__count').textContent();
  await more.click();
  await expect(page.locator('.popover .chip')).toHaveCount(Number(shown));
});

for (const theme of ['light', 'dark']) {
  test(`axe (${theme}): the filter bar and its popovers`, async ({ page }) => {
    await board(page, { theme, board: longBoard(), viewport: { width: 1440, height: 900 } });
    await page.evaluate(axeSource);
    const rules = ['color-contrast', 'nested-interactive', 'aria-allowed-attr', 'aria-allowed-role', 'aria-command-name', 'aria-hidden-focus',
      'aria-input-field-name', 'aria-prohibited-attr', 'aria-required-attr', 'aria-required-children', 'aria-required-parent', 'aria-roles',
      'aria-toggle-field-name', 'aria-valid-attr', 'aria-valid-attr-value', 'label', 'select-name'];
    const run = (sel) => page.evaluate(({ s, values }) => window.axe.run(document.querySelector(s), { runOnly: { type: 'rule', values }, resultTypes: ['violations'] })
      .then((r) => r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(' ')}`)), { s: sel, values: rules });
    expect(await run('.kanban-filterbar')).toEqual([]);
    await page.locator('.epic-options-btn').click();
    await expect(page.locator('.epic-options')).toBeVisible();
    expect(await run('body')).toEqual([]);
    await page.keyboard.press('Escape');
    await expect(page.locator('.epic-options')).toHaveCount(0);
    await page.locator('.kanban-filters__epic .overflow-more').click();
    await expect(page.locator('.popover')).toBeVisible();
    expect(await run('body')).toEqual([]);
  });
}

test('a second press on Epic options closes it and keeps focus on the button', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  const btn = page.locator('.epic-options-btn');
  await btn.click();
  await expect(page.locator('.epic-options')).toBeVisible();
  await btn.click();
  await expect(page.locator('.epic-options')).toHaveCount(0);
  await expect(btn).toBeFocused();
  await btn.click();
  await expect(page.locator('.epic-options')).toBeVisible();
});

test('at 390x844 every chip, the options button and Clear filters are at least 44px tall', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 390, height: 844 } });
  await searchBox(page).fill('T-10');
  await expect(page.locator('.kanban-clear')).toBeVisible();
  const heights = await page.locator('.kanban-filters .chip, .epic-options-btn, .kanban-clear, .kanban-filters .overflow-more, .phase-strip .overflow-more')
    .evaluateAll((els) => els.filter((e) => e.offsetParent).map((e) => [e.dataset.value || e.className, e.getBoundingClientRect().height]));
  expect(heights.length).toBeGreaterThan(3);
  // The rows' More buttons are touch targets too: the phase strip's, Priority's and Epic's.
  const mores = heights.filter(([name]) => String(name).includes('overflow-more'));
  console.log(`390 More heights: ${mores.map(([, hgt]) => hgt).join(', ')}`);
  expect(mores.length).toBeGreaterThanOrEqual(3);
  expect(heights.filter(([, hgt]) => hgt < 44)).toEqual([]);
});

test('at 390 the priority row shows at least two chips (all four if they fit), none cut, and nothing scrolls sideways', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 390, height: 844 } });
  const m = await page.evaluate(() => {
    const row = document.querySelector('.kanban-filters__priority');
    const box = row.querySelector('.chip-row__chips') || row;
    const boxRight = box.getBoundingClientRect().right;
    const all = [...row.querySelectorAll('.chip-row__chips .chip')];
    const shown = all.filter((c) => c.offsetParent && getComputedStyle(c).visibility !== 'hidden');
    return {
      total: all.length,
      chips: shown.map((c) => ({ name: c.textContent.trim(), right: c.getBoundingClientRect().right, cut: c.scrollWidth - c.clientWidth })),
      boxRight, vw: innerWidth, sideways: document.documentElement.scrollWidth - innerWidth,
      rowWidth: row.getBoundingClientRect().width, boxWidth: box.getBoundingClientRect().width,
    };
  });
  console.log(`390 priority chips shown: ${m.chips.length} (${m.chips.map((c) => c.name).join(' | ')}); row ${m.rowWidth}px, chips box ${m.boxWidth}px`);
  expect(m.chips.length).toBeGreaterThanOrEqual(2);
  for (const c of m.chips) {
    expect(c.right).toBeLessThanOrEqual(m.vw);
    expect(c.right).toBeLessThanOrEqual(m.boxRight + 0.5);
    expect(c.cut).toBeLessThanOrEqual(0);
  }
  expect(m.sideways).toBeLessThanOrEqual(0);
});

test('no phase chip shows a stray "null" between its number and its name', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  const texts = await page.locator('.phase-strip .phase-chip').evaluateAll((els) => els.map((e) => e.textContent));
  expect(texts.length).toBeGreaterThan(0);
  expect(texts.filter((t) => t.includes('null'))).toEqual([]);
});

test('at 390 the phase strip\'s More is inside the screen and its text is not cut', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 390, height: 844 } });
  const more = page.locator('.phase-strip .overflow-more');
  await expect(more).toBeVisible();
  const m = await more.evaluate((el) => ({ right: el.getBoundingClientRect().right, vw: innerWidth, sw: el.scrollWidth, cw: el.clientWidth }));
  expect(m.right).toBeLessThanOrEqual(m.vw);
  expect(m.sw).toBeLessThanOrEqual(m.cw);
});

test('at 390 the current phase keeps a readable name, and More and Archived stay whole on screen', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 390, height: 844 } });
  await expect(page.locator('.phase-strip .overflow-more')).toBeVisible();
  const m = await page.evaluate(() => {
    const name = document.querySelector('.phase-strip .phase-chip--current .phase-chip__name');
    const box = (sel) => {
      const el = document.querySelector(sel);
      return { right: el.getBoundingClientRect().right, cut: el.scrollWidth - el.clientWidth };
    };
    return {
      nameWidth: name.getBoundingClientRect().width, nameText: name.textContent.trim(),
      more: box('.phase-strip .overflow-more'), archived: box('.phase-strip .phase-archived'),
      vw: innerWidth, sideways: document.documentElement.scrollWidth - innerWidth,
    };
  });
  console.log(`390 current-name width: ${m.nameWidth}px`);
  expect(m.nameText.length).toBeGreaterThan(0);
  expect(m.nameWidth).toBeGreaterThanOrEqual(80);
  expect(m.more.right).toBeLessThanOrEqual(m.vw);
  expect(m.archived.right).toBeLessThanOrEqual(m.vw);
  expect(m.more.cut).toBeLessThanOrEqual(0);
  expect(m.archived.cut).toBeLessThanOrEqual(0);
  expect(m.sideways).toBeLessThanOrEqual(0);
});

test('at 1440 every control in the filter bar is the chip height', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  await priChip(page, 'High').click();
  await expect(page.locator('.kanban-clear')).toBeVisible();
  const hs = await page.locator('.kanban-filters .chip, .kanban-filters .overflow-more, .epic-options-btn, .kanban-clear')
    .evaluateAll((els) => els.filter((e) => e.offsetParent).map((e) => Math.round(e.getBoundingClientRect().height)));
  expect([...new Set(hs)]).toEqual([28]);
});

// Row 1 carries the count and Add task; row 2 keeps search, density and the labelled Group and Sort selects.
const rowFilters = (page) => page.locator('#topbar-actions > .overflow-more');
const colLabels = (page) => page.locator('.kanban-col-head .lbl');

test('1440: row 1 has the count, row 2 parks nothing, Group and Sort are named selects', async ({ page }) => {
  await board(page, { viewport: { width: 1440, height: 900 } });
  await expect(page.locator('#topbar-count')).toHaveText('7 tasks');
  await expect(rowFilters(page)).toBeHidden();
  await expect(page.locator('#topbar-actions .tm-subcount, .kanban-head-right')).toHaveCount(0);
  await expect(page.getByRole('combobox', { name: 'Group' })).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Sort' })).toBeVisible();
  for (const sel of ['.tm-search', '.tm-segmented', 'label.kanban-field']) {
    expect(await page.locator(`#topbar-actions > ${sel}`).count()).toBeGreaterThan(0);
  }
  const dens = page.getByRole('group', { name: 'Card density' });
  await expect(dens).toHaveClass(/tm-segmented/);
  await expect(page.locator('.card-tags').first()).toBeVisible();
  const minimal = dens.getByRole('button', { name: 'Minimal cards' });
  await minimal.click();
  await expect(minimal).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.card-tags')).toHaveCount(0);
  await page.getByRole('combobox', { name: 'Group' }).selectOption({ label: 'Epic' });
  await expect(colLabels(page)).toContainText(['Viewer re-skin', 'Native store']);
});

test('row-1 count adds "· k visible" while a search narrows the board and drops it when cleared', async ({ page }) => {
  await board(page, { viewport: { width: 1440, height: 900 } });
  const count = page.locator('#topbar-count');
  await expect(count).toHaveText('7 tasks');
  await searchBox(page).fill('cutover checklist');
  await expect(count).toHaveText('7 tasks · 1 visible');
  await searchBox(page).fill('T-10');
  await expect(count).toHaveText('7 tasks · 7 visible');
  await page.locator('.kanban-clear').click();
  await expect(count).toHaveText('7 tasks');
});

test('Clear filters keeps the chosen Group: columns stay epics and the select still says Epic', async ({ page }) => {
  await board(page, { viewport: { width: 1440, height: 900 } });
  const group = page.getByRole('combobox', { name: 'Group' });
  await group.selectOption({ label: 'Epic' });
  await searchBox(page).fill('cutover checklist');
  await expect(colLabels(page)).toHaveText(['Native store']);
  await page.locator('.kanban-clear').click();
  await expect(colLabels(page)).toHaveText(['Viewer re-skin', 'Native store']);
  await expect(group).toHaveValue('epic');
});

test('390: Add task is a 44px row-1 button that opens Create; Group works from the Filters popover', async ({ page }) => {
  await board(page, { viewport: { width: 390, height: 844 } });
  const add = page.locator('#topbar-primary [aria-label="Add task"]');
  await expect(add).toBeVisible();
  const box = await add.boundingBox();
  expect([Math.round(box.width), Math.round(box.height)]).toEqual([44, 44]);
  await expect(page.locator('#topbar-actions > .tm-search')).toBeVisible();
  await expect(rowFilters(page)).toBeVisible();
  await rowFilters(page).click();
  const pop = page.getByRole('dialog', { name: 'Filters' });
  await pop.getByRole('combobox', { name: 'Group' }).selectOption({ label: 'Epic' });
  await expect(colLabels(page)).toContainText(['Viewer re-skin', 'Native store']);
  await page.keyboard.press('Escape');
  await add.click();
  await expect(page.locator('.modal--form')).toBeVisible();
});

test('choosing Sort "Created: oldest first" saves { by: created, dir: asc }', async ({ page }) => {
  const puts = [];
  page.on('request', (r) => { if (r.method() === 'PUT' && r.url().includes('/api/viewer/prefs')) puts.push(r.postDataJSON()); });
  await board(page, { viewport: { width: 1440, height: 900 } });
  await page.getByRole('combobox', { name: 'Sort' }).selectOption({ label: 'Created: oldest first' });
  await expect.poll(() => puts.filter((p) => p?.kanban?.filters?.sort).at(-1)?.kanban.filters.sort)
    .toEqual({ by: 'created', dir: 'asc' });
});
