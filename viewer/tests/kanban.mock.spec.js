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
