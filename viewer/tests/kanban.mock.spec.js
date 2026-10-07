// User intent: in a real browser a Kanban card is a link — Enter opens the task and Escape hands focus back, a modified
// click is the browser's, the copy button only copies — a long title stays in three lines with the id unbroken at
// phone width, a recent card says "New" without a glow, and the cards pass axe in both themes.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD, DETAIL_TASK, RICH_RELATED, taskDetail, longBoard, kanbanMocks } from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
});
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

async function board(page, { theme = 'dark', board = BOARD, viewport } = {}) {
  if (viewport) await page.setViewportSize(viewport);
  await mockApi(page, kanbanMocks({ theme, board }));
  await page.goto('/#/kanban');
  await expect(page.locator('.card-task[data-task-id] > .link-row__link').first()).toBeVisible();
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
  // At 390 one column shows at a time: pick T-102's from the Columns tabs.
  await page.locator(`#kanban-col-${recentBoard.tasks.find((t) => t.id === 'T-102').status}-tab`).click();
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

test('at 390 Epic options sits on the epic line, not on a row of its own', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 390, height: 844 } });
  await expect(page.locator('.kanban-filters__epic .overflow-more')).toBeVisible();
  expect(await visibleTops(rowChips(page, 'epic'))).toHaveLength(1);
  const m = await page.evaluate(() => {
    const box = (s) => document.querySelector(s).getBoundingClientRect();
    const group = box('.kanban-filters__epic');
    const btn = box('.kanban-filters .epic-options-btn');
    const more = box('.kanban-filters__epic .overflow-more');
    const bar = box('.kanban-filters');
    return { groupTop: Math.round(group.top), btnTop: Math.round(btn.top), btnRight: btn.right, moreRight: more.right,
      groupRight: group.right, barRight: bar.right, vw: innerWidth, sideways: document.documentElement.scrollWidth - innerWidth };
  });
  expect(m.btnTop, 'Epic options top = epic group top').toBe(m.groupTop);
  expect(m.moreRight, 'More inside its group').toBeLessThanOrEqual(m.groupRight + 0.5);
  expect(m.btnRight, 'Epic options inside the filter box').toBeLessThanOrEqual(m.barRight + 0.5);
  expect(m.btnRight).toBeLessThanOrEqual(m.vw);
  expect(m.sideways).toBe(0);
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

// Leave the board with something open, then come back: no page error, no popover or modal left, the board lays out anew.
async function leaveAndReturn(page, errors) {
  await page.evaluate(() => { location.hash = '#/table'; });
  await expect(page.locator('table.tbl')).toBeVisible();
  await expect(page.locator('.popover')).toHaveCount(0);
  await expect(page.locator('.modal')).toHaveCount(0);
  // The modal's scroll lock goes with it: html:has(> body.modal-open) would otherwise keep a phone page from scrolling.
  await expect(page.locator('body.modal-open')).toHaveCount(0);
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).overflowY)).not.toBe('hidden');
  expect(await page.evaluate(() => import('/js/components/popover.js').then((m) => m.openPopoverCount()))).toBe(0);
  await page.evaluate(() => { location.hash = '#/kanban'; });
  await expect(page.locator('.card-task[data-task-id] > .link-row__link').first()).toBeVisible();
  await expect(page.locator('.phase-strip')).toHaveCount(1);
  await expect(page.locator('.popover')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBe(0);
  expect(errors).toEqual([]);
}

for (const [what, viewport, open] of [
  ['the archived-phases menu', { width: 1440, height: 900 }, async (page) => {
    await page.locator('.phase-strip .phase-archived').click();
    await expect(page.locator('.phase-archived__menu')).toBeVisible();
  }],
  ['the topbar Filters popover (row 2 parked at 390)', { width: 390, height: 844 }, async (page) => {
    await page.evaluate(() => document.fonts.ready);
    await page.locator('#topbar-actions > .overflow-more').click();
    await expect(page.getByRole('dialog', { name: 'Filters' })).toBeVisible();
  }],
  ['the detail modal', { width: 1440, height: 900, board: BOARD }, async (page) => {
    await linkOf(page, 'T-102').click();
    await expect(page.locator('.modal--detail .td-doc--embedded')).toBeVisible();
  }],
]) {
  test(`leaving the board with ${what} open leaves nothing behind`, async ({ page }) => {
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    const { board: b = longBoard(), ...size } = viewport;
    await board(page, { board: b, viewport: size });
    await open(page);
    await leaveAndReturn(page, errors);
  });
}

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
const colLabels = (page) => page.locator('.kanban-col-title');

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

test('at 390 with 230 tasks one column shows, nothing scrolls sideways, and the page ends with that column', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 390, height: 844 } });
  const list = page.getByRole('tablist', { name: 'Columns' });
  await expect(list).toBeVisible();
  for (const name of ['Blocked 23', 'Todo 92', 'In progress 46', 'In review 23', 'Done 46']) {
    await expect(list.getByRole('tab', { name })).toHaveCount(1);
  }
  await expect(page.locator('.kanban-col:visible')).toHaveCount(1);
  const m = await page.evaluate(() => {
    const col = document.querySelector('.kanban-col:not([hidden])');
    return { sw: document.documentElement.scrollWidth, iw: innerWidth, sh: document.documentElement.scrollHeight,
      bottom: col.getBoundingClientRect().bottom + scrollY };
  });
  console.log(`390 long board page height: ${m.sh}`);
  expect(m.sw).toBeLessThanOrEqual(m.iw);
  expect(m.sh).toBeLessThanOrEqual(m.bottom + 64);
  const sel = list.locator('[aria-selected="true"]');
  const before = await sel.getAttribute('aria-controls');
  await sel.focus();
  await page.keyboard.press('ArrowRight');
  const after = await list.locator('[aria-selected="true"]').getAttribute('aria-controls');
  expect(after).not.toBe(before);
  await expect(page.locator(`#${after}`)).toBeVisible();
  await expect(page.locator('.kanban-col-toggle:visible')).toHaveCount(0);
  const shadows = await page.locator('.kanban-col-head').evaluateAll((els) => els.map((e) => getComputedStyle(e).boxShadow));
  expect(shadows.every((s) => s === 'none')).toBe(true);
  for (const h of await list.getByRole('tab').evaluateAll((els) => els.map((e) => e.getBoundingClientRect().height))) {
    expect(h).toBeGreaterThanOrEqual(44);
  }
});

test('at 1440 every column shows and the page does not scroll', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  await expect(page.getByRole('tablist', { name: 'Columns' })).toBeHidden();
  await expect(page.locator('.kanban-col:visible')).toHaveCount(5);
  expect(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight)).toBe(true);
  const todo = page.locator('#kanban-col-body-todo');
  expect(await todo.evaluate((b) => b.scrollHeight > b.clientHeight)).toBe(true);
});

// Task 8, Part B: the collapse head, the no-match state, a poll's repaint, the keyboard walk, axe.
const colOf = (page, key) => page.locator(`.kanban-col:has(#kanban-col-body-${key})`);
const toggleOf = (page, key) => page.locator(`.kanban-col-toggle[data-key="${key}"]`);
const renameTask = (page, id, title) => page.evaluate(([id, title]) => import('/js/store.js').then(({ store }) => {
  const next = structuredClone(store.getBacklog());
  next.revision = `r-${Date.now()}`;
  const walk = (o) => { if (o && typeof o === 'object') { if (o.id === id && 'title' in o) o.title = title; Object.values(o).forEach(walk); } };
  walk(next);
  store.setBoard(next);
}), [id, title]);

test('a column head is a heading with a marker, a count and a named collapse button', async ({ page }) => {
  const puts = [];
  page.on('request', (r) => { if (r.method() === 'PUT' && r.url().includes('/api/viewer/prefs')) puts.push(r.postDataJSON()); });
  await board(page, { viewport: { width: 1440, height: 900 } });
  const h2 = colOf(page, 'in-review').getByRole('heading', { level: 2 });
  await expect(h2).toContainText('In review');
  await expect(h2).toContainText('waiting on you');
  const t = toggleOf(page, 'in-review');
  await expect(t).toHaveAccessibleName('Collapse In review');
  await expect(t).toHaveAttribute('aria-expanded', 'true');
  await t.click();
  await expect(t).toHaveAttribute('aria-expanded', 'false');
  await expect(t).toHaveAccessibleName('Expand In review');
  await expect(colOf(page, 'in-review')).toHaveClass(/collapsed/);
  const cols = (o) => (o && typeof o === 'object' ? (o.kanban?.collapsed_columns ?? Object.values(o).map(cols).find(Boolean)) : undefined);
  await expect.poll(() => cols(puts.at(-1))).toEqual(['in-review']);
});

test('a collapsed column stays collapsed across a poll, and the toggle works from the keyboard', async ({ page }) => {
  await board(page, { viewport: { width: 1440, height: 900 } });
  const t = toggleOf(page, 'in-review');
  await t.focus();
  await page.keyboard.press('Enter');
  await expect(t).toHaveAttribute('aria-expanded', 'false');
  await markBodies(page);
  await bumpBoard(page);
  await expectRepainted(page);
  await expect(toggleOf(page, 'in-review')).toHaveAttribute('aria-expanded', 'false');
  await expect(colOf(page, 'in-review')).toHaveClass(/collapsed/);
  await expect(toggleOf(page, 'in-review')).toBeFocused();
  await page.keyboard.press('Space');
  await expect(toggleOf(page, 'in-review')).toHaveAttribute('aria-expanded', 'true');
  await expect(colOf(page, 'in-review')).not.toHaveClass(/collapsed/);
});

test('a search that matches nothing says so once and offers Clear filters', async ({ page }) => {
  await board(page, { viewport: { width: 1440, height: 900 } });
  await searchBox(page).fill('zzzz');
  const empty = page.locator('.kanban-board .tm-empty');
  await expect(empty).toHaveCount(1);
  await expect(empty).toContainText('0 of 7 tasks match');
  await expect(empty.getByRole('button')).toHaveCount(1);
  await expect(empty.getByRole('button', { name: 'Clear filters' })).toBeVisible();
  const others = page.locator('.kanban-col-body:not(:has(.tm-empty))');
  expect(await others.count()).toBeGreaterThan(0);
  for (const t of await others.allTextContents()) expect(t.trim()).toBe('No tasks');
  await empty.getByRole('button', { name: 'Clear filters' }).click();
  await expect(searchBox(page)).toHaveValue('');
  await expect(page.locator('.card-task')).toHaveCount(7);
});

test('a poll that redraws the board keeps focus on the same card', async ({ page }) => {
  await board(page, { viewport: { width: 1440, height: 900 } });
  await linkOf(page, 'T-102').focus();
  await markBodies(page);
  await renameTask(page, 'T-104', 'Renamed by the poll');
  await expectRepainted(page);
  await expect(card(page, 'T-104')).toContainText('Renamed by the poll');
  await expect(linkOf(page, 'T-102')).toBeFocused();
  await card(page, 'T-102').locator('.card-id').focus();
  await markBodies(page);
  await renameTask(page, 'T-104', 'Renamed again');
  await expectRepainted(page);
  await expect(card(page, 'T-104')).toContainText('Renamed again');
  await expect(card(page, 'T-102').locator('.card-id')).toBeFocused();
});

test('a poll keeps a column\'s scroll at 1440, and the chosen tab and page scroll at 390', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  const todo = page.locator('#kanban-col-body-todo');
  await todo.evaluate((b) => { b.scrollTop = 400; });
  await markBodies(page);
  await bumpBoard(page);
  await expectRepainted(page);
  await page.waitForTimeout(100);
  expect(await page.locator('#kanban-col-body-todo').evaluate((b) => b.scrollTop)).toBe(400);
  await page.setViewportSize({ width: 390, height: 844 });
  const list = page.getByRole('tablist', { name: 'Columns' });
  await list.getByRole('tab', { name: 'In progress 46' }).click();
  await page.evaluate(() => window.scrollTo(0, 600));
  const tab = list.getByRole('tab', { name: 'In progress 46' });
  await tab.focus();
  await page.evaluate(() => window.scrollTo(0, 600));
  await markBodies(page);
  await bumpBoard(page);
  await expectRepainted(page);
  await page.waitForTimeout(100);
  await expect(tab).toHaveAttribute('aria-selected', 'true');
  await expect(tab).toBeFocused();
  expect(await page.evaluate(() => scrollY)).toBe(600);
});

test('keyboard walk: row 2, phases, priority, epics, then the cards in order; Enter opens, Escape returns', async ({ page }) => {
  await board(page, { viewport: { width: 1440, height: 900 } });
  const order = ['[data-global-search]', '.tm-segmented button', '.kanban-field select', '.phase-chip--all',
    '.chip[data-value="critical"]', '.chip[data-value="__all__"]', '.epic-options-btn', '.kanban-col .link-row__link', '.kanban-col .card-id'];
  await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); });
  const first = new Map();
  for (let i = 0; i < 120 && first.size < order.length; i++) {
    await page.keyboard.press('Tab');
    const hit = await page.evaluate((sels) => sels.find((s) => document.activeElement?.matches(s)), order);
    if (hit && !first.has(hit)) first.set(hit, i);
  }
  expect([...first.keys()]).toEqual(order);
  const idx = order.map((s) => first.get(s));
  for (let k = 1; k < idx.length; k++) expect(idx[k]).toBeGreaterThan(idx[k - 1]);
  const link = page.locator('.kanban-col .link-row__link').first();
  await link.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.modal--detail')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.modal--detail')).toHaveCount(0);
  await expect(link).toBeFocused();
  // Escape with nothing open leaves the board alone, and Space on a chip presses it.
  await page.keyboard.press('Escape');
  await expect(page.locator('.card-task').first()).toBeVisible();
  const crit = page.locator('.chip[data-value="critical"]');
  await crit.focus();
  await page.keyboard.press('Space');
  await expect(crit).toHaveAttribute('aria-pressed', 'true');
});

test('Escape closes only the topmost thing: Epic options first, then nothing else', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  await page.locator('.epic-options-btn').click();
  await expect(page.locator('.epic-options')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.epic-options')).toBeHidden();
  await expect(page.locator('.epic-options-btn')).toBeFocused();
  await expect(page.locator('.card-task').first()).toBeVisible();
});

for (const theme of ['light', 'dark']) {
  for (const width of [1440, 390]) {
    test(`axe (${theme}, ${width}): the Kanban screen has no violation`, async ({ page }) => {
      await board(page, { theme, viewport: { width, height: width > 400 ? 900 : 844 } });
      if (width > 400) await toggleOf(page, 'done').click(); // a collapsed column is scanned too
      await page.evaluate(axeSource);
      const result = await page.evaluate(() => window.axe.run(document.querySelector('#screen-mount'), {
        runOnly: { type: 'rule', values: ['color-contrast', 'nested-interactive', 'aria-allowed-attr', 'aria-valid-attr', 'aria-valid-attr-value',
          'aria-required-attr', 'aria-required-children', 'aria-required-parent', 'aria-allowed-role', 'aria-prohibited-attr',
          'scrollable-region-focusable', 'heading-order', 'button-name', 'link-name'] },
        resultTypes: ['violations'],
      }));
      expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
    });
  }
}

// The whisper is shown whole or not at all, and its words stay in the heading's name either way.
const whisperState = (page) => colOf(page, 'in-review').evaluate((col) => {
  const t = col.querySelector('.kanban-col-title');
  const w = t.querySelector('.kanban-col-whisper');
  const shown = !!w && getComputedStyle(w).display !== 'none' && w.getClientRects().length > 0;
  return { shown, whisperCut: shown && w.scrollWidth > w.clientWidth, headingCut: shown && t.scrollWidth > t.clientWidth, title: t.title };
});
test('the In review whisper is never cut: hidden when its head is too narrow, whole when there is room, always in the name', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  const h2 = colOf(page, 'in-review').getByRole('heading', { level: 2 });
  const at1440 = await whisperState(page);
  console.log(`1440 whisper: ${JSON.stringify(at1440)}`);
  expect([at1440.whisperCut, at1440.headingCut]).toEqual([false, false]);
  expect(at1440.title).toBe('In review, waiting on you');
  await expect(h2).toHaveAccessibleName(/waiting on you/);
  for (const key of ['blocked', 'todo', 'done']) await toggleOf(page, key).click();
  await expect.poll(async () => (await whisperState(page)).shown).toBe(true);
  const roomy = await whisperState(page);
  expect([roomy.whisperCut, roomy.headingCut]).toEqual([false, false]);
  await expect(h2).toHaveAccessibleName(/waiting on you/);
});

test('a cut column title keeps its words: every heading\'s title is its full label (Group: Epic, the long board)', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  const group = page.getByRole('combobox', { name: 'Group' });
  await group.selectOption({ label: 'Epic' });
  await expect(group).toHaveValue('epic');
  await expect.poll(() => colLabels(page).count()).toBeGreaterThan(1);
  const heads = await colLabels(page).evaluateAll((ts) => ts.map((t) => ({ text: t.textContent, title: t.title, cut: t.scrollWidth > t.clientWidth })));
  console.log(`epic headings cut: ${heads.filter((h) => h.cut).length} of ${heads.length}`);
  for (const h of heads) expect(h.title).toBe(h.text);
});

test('390, the long board: the sticky column head stays below the sticky topbar when the page scrolls', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 390, height: 844 } });
  await page.evaluate(() => window.scrollTo(0, 1500));
  await expect.poll(() => page.evaluate(() => scrollY)).toBe(1500);
  const m = await page.evaluate(() => {
    const head = document.querySelector('.kanban-col:not([hidden]) .kanban-col-head');
    const r = head.getBoundingClientRect();
    const bar = document.querySelector('.topbar').getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return { headTop: Math.round(r.top), barBottom: Math.round(bar.bottom), covered: !head.contains(hit) };
  });
  console.log(`390 sticky head: ${JSON.stringify(m)}`);
  expect(m.headTop).toBeGreaterThanOrEqual(m.barBottom);
  expect(m.covered).toBe(false);
});

// Task 9 carries: each poll test proves a repaint happened; phone focus, scroll lock, narrow strip, live epic counts.
const markBodies = (page) => page.evaluate(() => document.querySelectorAll('.kanban-col-body').forEach((b) => { b.dataset.stale = '1'; }));
const expectRepainted = (page) => expect(page.locator('.kanban-col-body[data-stale]')).toHaveCount(0);
const setStatus = (page, pick, status) => page.evaluate(([pick, status]) => import('/js/store.js').then(({ store }) => {
  const next = structuredClone(store.getBacklog());
  next.revision = `r-${Date.now()}`;
  let hit = null;
  const walk = (o) => {
    if (!o || typeof o !== 'object' || hit) return;
    if ('status' in o && typeof o.id === 'string' && /^T-/.test(o.id)
      && (pick.id ? o.id === pick.id : o.epic === pick.epic && o.status !== 'done')) { o.status = status; hit = o.id; return; }
    Object.values(o).forEach(walk);
  };
  walk(next);
  store.setBoard(next);
  return hit;
}), [pick, status]);

test('390: a focused card that a poll moves into a hidden column hands focus to its old column\'s tab', async ({ page }) => {
  await board(page, { viewport: { width: 390, height: 844 } });
  const first = page.locator('.card-task:visible').first();
  const id = await first.getAttribute('data-task-id');
  const key = await first.evaluate((c) => c.closest('.kanban-col').dataset.key);
  await linkOf(page, id).focus();
  await markBodies(page);
  expect(await setStatus(page, { id }, key === 'done' ? 'todo' : 'done')).toBe(id);
  await expectRepainted(page);
  await expect(page.locator(`#kanban-col-${key}-tab`)).toBeFocused();
});

test('390, the long board: with a task open in the modal the page behind it does not scroll', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 390, height: 844 } });
  await page.evaluate(() => window.scrollTo(0, 300));
  const before = await page.evaluate(() => scrollY);
  await page.locator('.card-task:visible > .link-row__link').first().click();
  await expect(page.locator('.modal--detail')).toBeVisible();
  for (const [x, y] of [[195, 422], [8, 836]]) {
    await page.mouse.move(x, y);
    await page.mouse.wheel(0, 1200);
  }
  await page.waitForTimeout(200);
  expect(await page.evaluate(() => scrollY)).toBe(before);
  expect(await page.evaluate(() => getComputedStyle(document.body).overflowY)).toBe('hidden');
});

test('at 360, the long board: More and Archived stay whole on screen and nothing scrolls sideways', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 360, height: 800 } });
  await expect(page.locator('.phase-strip .overflow-more')).toBeVisible();
  const m = await page.evaluate(() => {
    const box = (sel) => { const el = document.querySelector(sel); const r = el.getBoundingClientRect(); return { left: r.left, right: r.right, cut: el.scrollWidth - el.clientWidth }; };
    return { more: box('.phase-strip .overflow-more'), archived: box('.phase-strip .phase-archived'), vw: innerWidth,
      sideways: document.documentElement.scrollWidth - innerWidth };
  });
  for (const b of [m.more, m.archived]) {
    expect(b.left).toBeGreaterThanOrEqual(0);
    expect(b.right).toBeLessThanOrEqual(m.vw);
    expect(b.cut).toBeLessThanOrEqual(0);
  }
  expect(m.sideways).toBeLessThanOrEqual(0);
});

test('Epic options open across a poll: counts follow the board, search text and focus stay', async ({ page }) => {
  await board(page, { viewport: { width: 1440, height: 900 } });
  await page.locator('.epic-options-btn').click();
  const count = epicOption(page, 'viewer').locator('.epic-option__count');
  const n = Number(await count.textContent());
  expect(n).toBeGreaterThan(0);
  await page.locator('.epic-options__filter').fill('view');
  const pin = epicOption(page, 'viewer').locator('.epic-option__pin');
  await pin.focus();
  expect(await setStatus(page, { epic: 'viewer' }, 'done')).toMatch(/^T-/);
  await expect(count).toHaveText(String(n - 1));
  await expect(page.locator('.epic-options__filter')).toHaveValue('view');
  await expect(pin).toBeFocused();
});

test('a collapsed In review rail hides its whisper (nothing cut) and its title keeps its words', async ({ page }) => {
  await board(page, { board: longBoard(), viewport: { width: 1440, height: 900 } });
  await toggleOf(page, 'in-review').click();
  await expect(colOf(page, 'in-review')).toHaveClass(/collapsed/);
  await expect.poll(async () => (await whisperState(page)).shown).toBe(false);
  const s = await whisperState(page);
  expect(s.whisperCut).toBe(false);
  expect(s.title).toBe('In review, waiting on you');
  await expect(colOf(page, 'in-review').getByRole('heading', { level: 2 })).toHaveAccessibleName(/waiting on you/);
});
