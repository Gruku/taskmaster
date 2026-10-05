// User intent: creating an idea must behave in a real browser exactly as creating a task does — the same labelled form,
// no native dialog, no question when nothing was typed, one POST carrying the defaults plus what was typed, and a
// refusal said in words rather than as a status code or a JSON body.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

const IDEAS = [
  { id: 'IDEA-1', title: 'Board swimlanes by epic', status: 'exploring', tags: ['ux', 'board'], created: '2026-10-01T09:00:00Z' },
  { id: 'IDEA-2', title: 'Faster store writes', status: 'candidate', tags: ['perf'], created: '2026-10-02T09:00:00Z' },
  { id: 'IDEA-3', title: 'Phone layout for the table', status: 'parking-lot', tags: ['ux', 'mobile'], created: '2026-10-03T09:00:00Z' },
];
const CREATED = { id: 'IDEA-9', title: 'Faster board', status: 'exploring', tags: ['ux'], created: '2026-10-05T09:00:00Z' };

let nativeDialogs;
test.beforeEach(async ({ page }) => {
  nativeDialogs = [];
  page.on('dialog', (d) => { nativeDialogs.push(d.message()); d.dismiss(); });
  await page.emulateMedia({ reducedMotion: 'reduce' });
});
test.afterEach(async ({ page }) => {
  expect(nativeDialogs, 'the form never uses a native browser dialog').toEqual([]);
  expect(unmockedWrites(page)).toEqual([]);
});

// Reads of the list are counted; once an idea has been created the list holds it.
async function openCreate(page, { theme = 'dark', post = { ok: true, id: 'IDEA-9' } } = {}) {
  await mockApi(page, { '/api/viewer/prefs': { theme, ui: {}, screens: {} }, 'POST /api/ideas': post });
  const reads = { count: 0, created: false };
  await page.route((url) => url.pathname === '/api/ideas', (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    reads.count++;
    return route.fulfill({ json: { ideas: reads.created ? [...IDEAS, CREATED] : IDEAS } });
  });
  await page.goto('/#/ideas');
  await expect(page.locator('.ideas__list')).toContainText('Board swimlanes by epic');
  // Once the fonts have settled the row, "New Idea" is in it or behind Filters (row 2 too narrow for it).
  await page.evaluate(() => document.fonts.ready.then(() => new Promise((ok) => requestAnimationFrame(() => ok()))));
  const filters = page.locator('#topbar-actions > .overflow-more');
  if (await filters.isVisible()) await filters.click();
  await newIdea(page).click();
  const dialog = page.getByRole('dialog', { name: 'Create idea' });
  await expect(dialog).toBeVisible();
  await expect(ctl(dialog, 'title')).toBeFocused();
  return { dialog, reads };
}

const newIdea = (page) => page.getByRole('button', { name: /new idea/i });
const field = (dialog, key) => dialog.locator(`[data-key="${key}"]`);
const ctl = (dialog, key) => field(dialog, key).locator('input, select, textarea').first();
const save = (dialog) => dialog.getByRole('button', { name: 'Save', exact: true });
const cancel = (dialog) => dialog.getByRole('button', { name: 'Cancel', exact: true });
const confirmBox = (page) => page.getByRole('alertdialog', { name: 'Discard changes?' });
const posts = (page) => {
  const seen = [];
  page.on('request', (r) => {
    if (r.method() === 'POST' && new URL(r.url()).pathname === '/api/ideas') seen.push(r.postDataJSON());
  });
  return seen;
};

test('"New Idea" opens "Create idea" on the shared form: every field labelled, the body open, nothing flagged', async ({ page }) => {
  const { dialog } = await openCreate(page);
  await expect(page.locator('.modal--form')).toHaveCount(1);
  for (const [name, key] of [['Title', 'title'], ['Status', 'status'], ['Tags', 'tags'], ['Body', 'body']]) {
    // A required field's label goes on to say "required".
    const control = dialog.getByLabel(new RegExp(`^${name}`));
    await expect(control, name).toHaveCount(1);
    expect(await control.evaluate((el) => el.closest('[data-key]').dataset.key)).toBe(key);
  }
  await expect(ctl(dialog, 'status')).toHaveValue('exploring');
  expect(await ctl(dialog, 'status').locator('option').allTextContents()).toEqual(['Exploring', 'Candidate', 'Parking lot', 'Promoted', 'Dropped']);
  await expect(field(dialog, 'body').getByRole('button', { name: 'Body' })).toHaveAttribute('aria-expanded', 'true');
  await expect(ctl(dialog, 'body')).toBeVisible();
  await expect(save(dialog)).toBeDisabled();
  await expect(dialog.locator('.ef-error:not(:empty)')).toHaveCount(0);
  // Nothing of the screen's old hand-built dialog is left.
  await expect(page.locator('#entity-modal-host')).toHaveCount(0);
  await expect(page.locator('[class^="em-"], [class*=" em-"]')).toHaveCount(0);
  await expect(page.locator('body')).not.toHaveClass(/em-open/);
});

test('an untouched form closes on Escape with no dialog of any kind, and focus returns to "New Idea"', async ({ page }) => {
  const { dialog } = await openCreate(page);
  await ctl(dialog, 'tags').focus();
  await page.keyboard.press('Escape');
  await expect(page.locator('.modal')).toHaveCount(0);
  await expect(newIdea(page)).toBeFocused();
});

test('a typed title is guarded: Escape asks "Discard changes?" in-app; "Keep editing" keeps it, "Discard" closes', async ({ page }) => {
  const sent = posts(page);
  const { dialog } = await openCreate(page);
  await page.keyboard.type('Half a thought');
  await page.keyboard.press('Escape');
  await expect(confirmBox(page)).toBeVisible();
  await expect(confirmBox(page)).toContainText('Your edits to this idea will be lost.');
  await confirmBox(page).getByRole('button', { name: 'Keep editing' }).click();
  await expect(ctl(dialog, 'title')).toHaveValue('Half a thought');
  await page.keyboard.press('Escape');
  await confirmBox(page).getByRole('button', { name: 'Discard' }).click();
  await expect(page.locator('.modal')).toHaveCount(0);
  expect(sent).toEqual([]);
});

test('Save sends one POST with the defaults plus what was typed, closes, and the list is read again', async ({ page }) => {
  const sent = posts(page);
  const { dialog, reads } = await openCreate(page);
  await page.keyboard.type('Faster board');
  // The tag is offered from the ones already in use.
  await ctl(dialog, 'tags').fill('u');
  await expect(page.getByRole('listbox', { name: 'Tags suggestions' })).toContainText('ux');
  await ctl(dialog, 'tags').fill('ux');
  await page.keyboard.press('Enter');
  await expect(field(dialog, 'tags').locator('.ef-chip-label')).toHaveText(['ux']);
  const before = reads.count;
  reads.created = true;
  await save(dialog).click();
  await expect(page.locator('.modal')).toHaveCount(0);
  expect(sent).toEqual([{ status: 'exploring', tags: ['ux'], title: 'Faster board' }]);
  expect(reads.count).toBe(before + 1);
  await expect(page.locator('.ideas__list')).toContainText('Faster board');
});

test('a list that fails to refresh after the idea was created does not hold the form open', async ({ page }) => {
  const sent = posts(page);
  const { dialog } = await openCreate(page);
  await page.route((url) => url.pathname === '/api/ideas', (route) =>
    route.request().method() === 'GET' ? route.fulfill({ status: 500, json: { ok: false } }) : route.fallback());
  await page.keyboard.type('Faster board');
  await save(dialog).click();
  await expect(page.locator('.modal')).toHaveCount(0);
  expect(sent).toHaveLength(1);
});

for (const [name, post, words] of [
  ['a 400 shows the server\'s reason', { status: 400, json: { ok: false, error: 'title is required' } }, 'title is required'],
  ['a 500 is said in words', { status: 500, json: { ok: false, error: 'sqlite3.OperationalError: database is locked' } },
    'The server could not save this change. Try again in a moment.'],
]) {
  test(`a refusal is words: ${name}, the form stays open with the edit, and no method, URL or JSON reaches the page`, async ({ page }) => {
    const { dialog } = await openCreate(page, { post });
    await page.keyboard.type('Faster board');
    await save(dialog).click();
    const footer = dialog.locator('.modal-footer');
    await expect(footer.locator('[role="alert"]')).toHaveText(words);
    const text = await footer.textContent();
    for (const raw of ['→', '/api', '{', '400', '500']) expect(text, raw).not.toContain(raw);
    await expect(ctl(dialog, 'title')).toHaveValue('Faster board');
    await expect(save(dialog)).toBeEnabled();
    await cancel(dialog).click();
    await confirmBox(page).getByRole('button', { name: 'Discard' }).click();
  });
}

test('Save with no title says so on the title and in the footer, and sends nothing', async ({ page }) => {
  const sent = posts(page);
  const { dialog } = await openCreate(page);
  await ctl(dialog, 'body').fill('Only a body so far');
  await save(dialog).click();
  await expect(ctl(dialog, 'title')).toBeFocused();
  await expect(ctl(dialog, 'title')).toHaveAttribute('aria-invalid', 'true');
  await expect(field(dialog, 'title').locator('.ef-error')).toHaveText('Title is required');
  await expect(dialog.locator('[role="status"]')).toHaveText('1 field needs attention');
  expect(sent).toEqual([]);
  await cancel(dialog).click();
  await confirmBox(page).getByRole('button', { name: 'Discard' }).click();
});

// ── Phone ──
test('390×844: one column, nothing wider than the screen, Save and Cancel in view without scrolling', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const { dialog } = await openCreate(page);
  const boxes = await page.evaluate(() => Object.fromEntries(['title', 'status', 'tags', 'body'].map((k) => {
    const r = document.querySelector(`.modal--form [data-key="${k}"]`).getBoundingClientRect();
    return [k, { left: r.left, top: r.top }];
  })));
  expect(boxes.tags.left).toBe(boxes.status.left);
  expect(boxes.tags.top).toBeGreaterThan(boxes.status.top);
  const overflow = await page.evaluate(() => {
    const body = document.querySelector('.modal--form .modal-body');
    const wide = [...document.querySelectorAll('.modal--form *')].filter((el) => el.getBoundingClientRect().right > window.innerWidth + 0.5 && !el.closest('.eform-sr'));
    return { page: document.documentElement.scrollWidth, body: body.scrollWidth - body.clientWidth, wide: wide.map((el) => el.className), scrollY: window.scrollY };
  });
  expect(overflow.page).toBeLessThanOrEqual(390);
  expect(overflow.body).toBeLessThanOrEqual(0);
  expect(overflow.wide).toEqual([]);
  expect(overflow.scrollY).toBe(0);
  await expect(save(dialog)).toBeInViewport({ ratio: 1 });
  await expect(cancel(dialog)).toBeInViewport({ ratio: 1 });
});

// ── Accessibility ──
const AXE_RULES = /^(color-contrast|label|select-name|button-name|aria-.+)$/;
async function axe(page) {
  await page.evaluate(axeSource);
  const result = await page.evaluate(() => window.axe.run(document.querySelector('.modal--form'), { resultTypes: ['violations'] }));
  return result.violations.filter((v) => AXE_RULES.test(v.id)).map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`);
}

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): the Create idea form, clean, with its messages shown, and with the tag list open`, async ({ page }) => {
    const { dialog } = await openCreate(page, { theme });
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    expect(await axe(page)).toEqual([]);
    await ctl(dialog, 'body').fill('x');
    await save(dialog).click();
    await expect(dialog.locator('[role="status"]')).toHaveText('1 field needs attention');
    expect(await axe(page)).toEqual([]);
    await ctl(dialog, 'tags').fill('u');
    await expect(page.getByRole('listbox', { name: 'Tags suggestions' })).toBeVisible();
    await page.keyboard.press('ArrowDown');
    expect(await axe(page)).toEqual([]);
    await page.keyboard.press('Escape');
    await cancel(dialog).click();
    await confirmBox(page).getByRole('button', { name: 'Discard' }).click();
  });
}
