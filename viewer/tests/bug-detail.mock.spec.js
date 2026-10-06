// User intent: the bug page reads as the shared detail template — summary and location shown, status and severity as
// markers, relations in the rail — and says a missing or failed load in words, by keyboard, at 390px and in both themes
// (plan 3c Task 8).
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD, BUG, BUG_FIXED, LONG_BUG } from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');
let errors;
let dialogs;

test.beforeEach(async ({ page }) => {
  errors = [];
  dialogs = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('dialog', (d) => { dialogs.push(d.message()); void d.dismiss(); });
  await page.emulateMedia({ reducedMotion: 'reduce' });
});
test.afterEach(async ({ page }) => {
  expect(unmockedWrites(page)).toEqual([]);
  expect(errors).toEqual([]);
  expect(dialogs).toEqual([]);
});

async function open(page, hash, { theme = 'dark', before } = {}) {
  await mockApi(page, {
    '/api/viewer/prefs': { theme, ui: {}, screens: {} },
    '/api/board': BOARD, '/api/backlog': BOARD, '/api/bugs': [], '/api/issues': { issues: [] },
    '/api/bugs/B-031': BUG, '/api/bugs/B-030': BUG_FIXED, '/api/bugs/B-1234': LONG_BUG,
    '/api/bugs/B-999': { status: 404, json: { ok: false, error: 'unknown bug B-999' } },
  });
  if (before) await before();
  await page.goto(`/${hash}`);
}

const mount = (page) => page.locator('#screen-mount');

test('the bug reads as the detail template, with its summary and location', async ({ page }) => {
  await open(page, '#/bug/B-031');
  const m = mount(page);
  await expect(m.locator('h1')).toHaveText(BUG.title);
  await expect(m).toHaveClass(/dp-page--bug/);
  await expect(m.locator('[data-field="status"] .marker__word')).toHaveText('Open');
  await expect(m.locator('[data-field="severity"] .marker__word')).toHaveText('Medium');
  await expect(m.locator('[data-section="summary"] code')).toHaveCount(1);
  await expect(m.locator('[data-section="summary"] ol')).toHaveCount(1);
  await expect(m.locator('[data-section="location"] code')).toHaveText('viewer/css/screens/kanban.css:87');
  const meta = m.locator('[data-test="meta"]');
  await expect(meta.locator('a[href="#/bugs"]')).toHaveText('Bugs');
  await expect(meta.locator('a[href="#/task/T-102"]')).toHaveText('T-102');
  await expect(meta).toContainText('reported by user');
  const found = m.locator('[data-sub="found-in"] a.td-dep');
  await expect(found.locator('.td-dep__id')).toHaveText('T-102');
  await expect(found.locator('.td-dep__title')).toHaveText('Re-skin the Kanban cards and columns');
  await expect(m.locator('.id-crumb, [class*="bug-detail"]')).toHaveCount(0);
  await expect(m).not.toContainText('‹');
});

test('a fixed bug shows its commit, where it went, and no severity it never had', async ({ page }) => {
  await open(page, '#/bug/B-030');
  const m = mount(page);
  await expect(m.locator('[data-field="status"] .marker__word')).toHaveText('Fixed');
  await expect(m.locator('[data-field="severity"]')).toHaveCount(0);
  await expect(m.locator('[data-tag="fix_commit"]')).toContainText('abfb1b9c0ffee');
  await expect(m.locator('[data-tag="fix_commit"]')).toHaveAttribute('aria-label', 'Copy fix commit abfb1b9c0ffee');
  for (const g of ['found-in', 'adopted-into', 'promoted-to']) await expect(m.locator(`[data-sub="${g}"]`)).toHaveCount(1);
  await expect(m.locator('[data-sub="adopted-into"] a.td-dep[href="#/task/T-101"]')).toBeVisible();
  const pill = m.locator('[data-sub="promoted-to"] a.link-pill[href="#/issue/ISS-012"]');
  await expect(pill.locator('.link-pill__label')).toHaveText('Issue');
  await expect(pill.locator('.link-pill__id')).toHaveText('ISS-012');
});

test('B-999 is not found in words; a 500 says so without the server\'s text; no action is offered without a record', async ({ page }) => {
  await open(page, '#/bug/B-999');
  const m = mount(page);
  const missing = m.locator('.tm-empty[data-state="missing"]');
  await expect(missing).toBeVisible();
  await expect(missing.locator('.tm-empty__label')).toHaveText('B-999');
  await expect(missing.locator('.tm-empty__headline')).toHaveText('Bug not found');
  await expect(missing.getByRole('link', { name: 'Open Bugs' })).toBeVisible();
  await expect(page.locator('#topbar-primary')).toBeEmpty();
  await expect(m.locator('button')).toHaveCount(0);

  let fails = 2;
  let hold = null; // set before the last Try again, so its Loading state can be seen
  await page.route('**/api/bugs/B-031', async (route) => {
    if (fails-- > 0) {
      await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'Traceback: KeyError found_in' }) });
      return;
    }
    if (hold) await hold;
    await route.fallback();
  });
  await page.evaluate(() => { location.hash = '#/bug/B-031'; });
  const failed = m.locator('.tm-empty[data-state="error"]');
  await expect(failed).toBeVisible();
  for (const word of ['Traceback', '500', '/api']) await expect(m).not.toContainText(word);
  // A second failure after Try again lands focus on the new Try again, never <body>.
  await failed.getByRole('button', { name: 'Try again' }).click();
  await expect(m.locator('.tm-empty[data-state="error"] button')).toBeFocused();
  let release;
  hold = new Promise((r) => { release = r; });
  await m.getByRole('button', { name: 'Try again' }).click();
  const busy = m.locator('.tm-empty[aria-busy="true"]');
  await expect(busy).toBeVisible();
  await expect(busy).toBeFocused();
  release();
  await expect(m.locator('h1')).toHaveText(BUG.title);
  await expect(m.locator('h1')).toBeFocused();
});

test('a bug opened after a missing one starts clean', async ({ page }) => {
  await open(page, '#/bug/B-999');
  await expect(mount(page).locator('.tm-empty')).toBeVisible();
  await page.evaluate(() => { location.hash = '#/bug/B-031'; });
  await expect(mount(page).locator('h1')).toHaveText(BUG.title);
  await expect(mount(page).locator('.td-doc')).toHaveCount(0); // the mount itself is the one .td-doc
  await expect(mount(page)).toHaveClass(/td-doc/);
  await expect(mount(page).locator('.tm-empty')).toHaveCount(0);
});

test('the first fetch shows a Loading state, not a blank page', async ({ page }) => {
  let release;
  const gate = new Promise((r) => { release = r; });
  // Registered after mockApi (via before), so it wins.
  await open(page, '#/bug/B-031', { before: () => page.route('**/api/bugs/B-031', async (route) => { await gate; await route.fallback(); }) });
  await expect(mount(page).locator('.tm-empty[aria-busy="true"]')).toBeVisible();
  release();
  await expect(mount(page).locator('h1')).toHaveText(BUG.title);
});

async function tabStops(page) {
  return page.evaluate(() => [...document.querySelector('#screen-mount').querySelectorAll('a[href], button, [tabindex]')]
    .filter((el) => el.tabIndex >= 0 && el.getClientRects().length && !el.disabled)
    .map((el) => el.getAttribute('data-test') || el.getAttribute('data-tag') || el.getAttribute('href') || el.getAttribute('data-action')));
}

test('walks by keyboard', async ({ page }) => {
  await open(page, '#/bug/B-031');
  await expect(mount(page).locator('h1')).toBeVisible();
  expect(await tabStops(page)).toEqual(['bug-id', '#/bugs', '#/task/T-102', 'shelve', 'adopt', 'promote', '#/task/T-102']);
  await page.evaluate(() => { location.hash = '#/bug/B-030'; });
  await expect(mount(page).locator('[data-tag="fix_commit"]')).toBeVisible();
  expect(await tabStops(page)).toEqual(['bug-id', '#/bugs', '#/task/T-102', 'fix_commit', '#/task/T-102', '#/task/T-101', '#/issue/ISS-012']);
  await page.locator('[data-test="bug-id"]').focus();
  for (const sel of ['[data-test="meta"] a[href="#/bugs"]', '[data-test="meta"] a[href="#/task/T-102"]', '[data-tag="fix_commit"]',
    '[data-sub="found-in"] a', '[data-sub="adopted-into"] a', '[data-sub="promoted-to"] a']) {
    await page.keyboard.press('Tab');
    await expect(mount(page).locator(sel)).toBeFocused();
  }
});

test('a long title and a long path stay inside 390px', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page, '#/bug/B-1234');
  await expect(mount(page).locator('h1')).toBeVisible();
  const box = await page.evaluate(() => {
    const id = document.querySelector('#screen-mount .td-id-text');
    return { sw: document.documentElement.scrollWidth, iw: innerWidth, rects: id.getClientRects().length, text: id.textContent };
  });
  expect(box.sw).toBeLessThanOrEqual(box.iw);
  expect(box.text).toBe('B-1234');
  expect(box.rects).toBe(1);
});

// Answers GET and records every write to /api/bugs/B-031; after an accepted write the GET answers the merged bug.
function bugWrites(page, answer = () => ({ json: { ok: true, id: 'B-031' } })) {
  const posts = [];
  let current = BUG;
  const install = () => page.route('**/api/bugs/B-031', async (route) => {
    const req = route.request();
    if (req.method() === 'GET') { await route.fulfill({ json: current }); return; }
    const body = req.postDataJSON();
    posts.push(body);
    const r = answer(body, posts.length);
    if ((r.status ?? 200) < 300) current = { ...BUG, ...body };
    await route.fulfill({ status: r.status ?? 200, json: r.json });
  });
  return { posts, install };
}

const focusIsSensible = (page) => page.evaluate(() => document.activeElement && document.activeElement !== document.body);
const statusWord = (page) => mount(page).locator('[data-field="status"] .marker__word');

test('Mark fixed asks for the commit in a form and the page shows the bug fixed', async ({ page }) => {
  const w = bugWrites(page, (body) => ({ json: { ok: true, id: 'B-031', status: body.status } }));
  await open(page, '#/bug/B-031', { before: w.install });
  const primary = page.locator('#topbar-primary').getByRole('button', { name: 'Mark this bug fixed' });
  await primary.click();
  const dlg = page.getByRole('dialog', { name: 'Mark fixed' });
  await expect(dlg).toBeVisible();
  const save = dlg.getByRole('button', { name: 'Mark fixed' });
  await expect(save).toBeDisabled();
  await dlg.getByRole('textbox', { name: /Fix commit/ }).fill('abc1234');
  await save.click();
  await expect(dlg).toHaveCount(0);
  expect(w.posts).toEqual([{ status: 'fixed', fix_commit: 'abc1234' }]);
  await expect(statusWord(page)).toHaveText('Fixed');
  await expect(mount(page).locator('.dp-actions')).toHaveCount(0);
  await expect(page.locator('#topbar-primary')).toBeEmpty();
  expect(await focusIsSensible(page)).toBe(true);
});

test('a refused fix is said in words in the form', async ({ page }) => {
  const w = bugWrites(page, (_b, n) => (n === 1
    ? { status: 400, json: { ok: false, error: 'Error: status=fixed requires fix_commit to be set' } }
    : { status: 500, json: { ok: false, error: 'Traceback: boom' } }));
  await open(page, '#/bug/B-031', { before: w.install });
  await page.locator('#topbar-primary button').click();
  const dlg = page.getByRole('dialog', { name: 'Mark fixed' });
  await dlg.getByRole('textbox', { name: /Fix commit/ }).fill('abc1234');
  await dlg.getByRole('button', { name: 'Mark fixed' }).click();
  const alert = dlg.locator('[role="alert"]').filter({ hasText: /\S/ });
  await expect(alert).toContainText('requires fix_commit');
  for (const word of ['400', '/api', '{']) await expect(alert).not.toContainText(word);
  await dlg.getByRole('button', { name: 'Mark fixed' }).click();
  await expect(alert).toHaveText('The server could not save this change. Try again in a moment.');
  await expect(dlg).toBeVisible();
  await expect(statusWord(page)).toHaveText('Open');
  expect(w.posts).toHaveLength(2);
});

test('Shelve asks in the app and Keep open sends nothing', async ({ page }) => {
  const w = bugWrites(page);
  await open(page, '#/bug/B-031', { before: w.install });
  const row = mount(page).getByRole('group', { name: 'Bug actions' });
  await row.getByRole('button', { name: 'Shelve' }).click();
  const ask = page.getByRole('alertdialog', { name: 'Shelve B-031?' });
  await ask.getByRole('button', { name: 'Keep open' }).click();
  await expect(ask).toHaveCount(0);
  expect(w.posts).toEqual([]);
  expect(await focusIsSensible(page)).toBe(true);
  await row.getByRole('button', { name: 'Shelve' }).click();
  await page.getByRole('alertdialog', { name: 'Shelve B-031?' }).getByRole('button', { name: 'Shelve' }).click();
  await expect(statusWord(page)).toHaveText('Shelved');
  expect(w.posts).toEqual([{ status: 'shelved' }]);
  await expect(mount(page).getByRole('button', { name: 'Shelve' })).toHaveCount(0);
  expect(await focusIsSensible(page)).toBe(true);
});

test('Adopt refuses a task that is not on the board, then adopts', async ({ page }) => {
  const w = bugWrites(page);
  await open(page, '#/bug/B-031', { before: w.install });
  await mount(page).getByRole('button', { name: 'Adopt into task' }).click();
  const dlg = page.getByRole('dialog', { name: 'Adopt into a task' });
  const field = dlg.getByRole('textbox', { name: /Task/ });
  await field.fill('T-999');
  await dlg.getByRole('button', { name: 'Adopt' }).click();
  await expect(dlg).toContainText('No task T-999 on the board');
  expect(w.posts).toEqual([]);
  await field.fill('');
  await expect(dlg).toContainText('Task is required');
  await field.fill('T-101');
  await dlg.getByRole('button', { name: 'Adopt' }).click();
  await expect(dlg).toHaveCount(0);
  expect(w.posts).toEqual([{ status: 'adopted', adopted_into: 'T-101' }]);
  await expect(statusWord(page)).toHaveText('Adopted');
  expect(await focusIsSensible(page)).toBe(true);
});

test('a refused Shelve is said in words under the row, the bug stays open, focus is back on Shelve', async ({ page }) => {
  const w = bugWrites(page, (_b, n) => (n === 1
    ? { status: 400, json: { ok: false, error: 'Error: bug B-031 is locked by another session' } }
    : { status: 500, json: { ok: false, error: 'Traceback: boom at /api/bugs {"x":1}' } }));
  await open(page, '#/bug/B-031', { before: w.install });
  const row = mount(page).getByRole('group', { name: 'Bug actions' });
  const shelve = row.getByRole('button', { name: 'Shelve' });
  const msg = row.locator('.dp-actions__message');
  await shelve.click();
  await page.getByRole('alertdialog', { name: 'Shelve B-031?' }).getByRole('button', { name: 'Shelve' }).click();
  await expect(msg).toContainText('locked by another session');
  for (const word of ['400', '/api', '{']) await expect(msg).not.toContainText(word);
  await expect(shelve).toBeFocused();
  await expect(shelve).toBeEnabled();
  await expect(row).not.toHaveAttribute('aria-busy', /.*/);
  await expect(statusWord(page)).toHaveText('Open');
  await shelve.click();
  await page.getByRole('alertdialog', { name: 'Shelve B-031?' }).getByRole('button', { name: 'Shelve' }).click();
  await expect(msg).toHaveText('The server could not save this change. Try again in a moment.');
  for (const word of ['500', '/api', '{', 'Traceback']) await expect(msg).not.toContainText(word);
  await expect(shelve).toBeFocused();
  await expect(statusWord(page)).toHaveText('Open');
  expect(w.posts).toEqual([{ status: 'shelved' }, { status: 'shelved' }]);
});

test('while a Shelve is being saved the row is busy and a second Shelve sends nothing', async ({ page }) => {
  const posts = [];
  let release;
  const gate = new Promise((r) => { release = r; });
  let current = BUG;
  await open(page, '#/bug/B-031', { before: () => page.route('**/api/bugs/B-031', async (route) => {
    const req = route.request();
    if (req.method() === 'GET') { await route.fulfill({ json: current }); return; }
    posts.push(req.postDataJSON());
    await gate;
    current = { ...BUG, status: 'shelved' };
    await route.fulfill({ json: { ok: true, id: 'B-031' } });
  }) });
  const row = mount(page).getByRole('group', { name: 'Bug actions' });
  const shelve = row.getByRole('button', { name: 'Shelve' });
  await shelve.click();
  await page.getByRole('alertdialog', { name: 'Shelve B-031?' }).getByRole('button', { name: 'Shelve' }).click();
  await expect.poll(() => posts.length).toBe(1);
  await expect(row).toHaveAttribute('aria-busy', 'true');
  for (const b of await row.getByRole('button').all()) await expect(b).toBeDisabled();
  await shelve.click({ force: true });
  await shelve.evaluate((b) => b.click());
  await expect(page.getByRole('alertdialog')).toHaveCount(0);
  release();
  await expect(statusWord(page)).toHaveText('Shelved');
  expect(posts).toEqual([{ status: 'shelved' }]);
  expect(await focusIsSensible(page)).toBe(true);
});

for (const status of [500, 404]) {
  test(`after a Shelve, a re-read that answers ${status} leaves no Mark fixed in row 1`, async ({ page }) => {
    const posts = [];
    await open(page, '#/bug/B-031', { before: () => page.route('**/api/bugs/B-031', async (route) => {
      const req = route.request();
      if (req.method() !== 'GET') { posts.push(req.postDataJSON()); await route.fulfill({ json: { ok: true, id: 'B-031' } }); return; }
      if (posts.length) await route.fulfill({ status, json: { ok: false, error: 'gone' } });
      else await route.fulfill({ json: BUG });
    }) });
    await expect(page.locator('#topbar-primary').getByRole('button', { name: 'Mark this bug fixed' })).toHaveCount(1);
    await mount(page).getByRole('group', { name: 'Bug actions' }).getByRole('button', { name: 'Shelve' }).click();
    await page.getByRole('alertdialog', { name: 'Shelve B-031?' }).getByRole('button', { name: 'Shelve' }).click();
    await expect(mount(page).locator('.tm-empty')).toContainText(status === 404 ? 'Bug not found' : 'Could not load this bug');
    await expect(page.locator('#topbar-primary')).toBeEmpty();
    expect(posts).toEqual([{ status: 'shelved' }]);
  });
}

test('Promote opens the new issue', async ({ page }) => {
  const sent = [];
  await open(page, '#/bug/B-031', { before: async () => {
    await page.route('**/api/bugs/promote', async (route) => {
      sent.push(route.request().postDataJSON());
      await route.fulfill({ status: 201, json: { ok: true, issue_id: 'ISS-030' } });
    });
    await page.route('**/api/issues', (route) => route.fulfill({ json: { issues: [{ id: 'ISS-030', title: 'Promoted', severity: 'P1', status: 'open' }] } }));
  } });
  await mount(page).getByRole('button', { name: 'Promote to issue' }).click();
  const dlg = page.getByRole('dialog', { name: 'Promote to an issue' });
  await dlg.getByRole('textbox', { name: /Evidence/ }).fill('Recurring: 3 bugs');
  await dlg.getByRole('button', { name: 'Promote' }).click();
  await expect(page).toHaveURL(/#\/issue\/ISS-030$/);
  await expect(dlg).toHaveCount(0);
  expect(sent).toHaveLength(1);
  expect(sent[0]).toMatchObject({ bug_ids: ['B-031'], title: BUG.title, severity: 'P2', evidence_text: 'Recurring: 3 bugs' });
  await expect(page.locator("#screen-mount h1")).toHaveText("Promoted");
  await expect(page.locator("#screen-mount h1")).toBeFocused();
  expect(await focusIsSensible(page)).toBe(true);
});

test('leaving the bug page with an action form open: clean closes, typed asks', async ({ page }) => {
  const w = bugWrites(page);
  await open(page, '#/bug/B-031', { before: w.install });
  await page.locator('#topbar-primary button').click();
  await expect(page.getByRole('dialog', { name: 'Mark fixed' })).toBeVisible();
  await page.evaluate(() => { location.hash = '#/bugs'; });
  await expect(page.locator('.modal')).toHaveCount(0);
  await page.evaluate(() => { location.hash = '#/bug/B-031'; });
  await page.locator('#topbar-primary button').click();
  await page.getByRole('dialog', { name: 'Mark fixed' }).getByRole('textbox', { name: /Fix commit/ }).fill('abc');
  await page.evaluate(() => { location.hash = '#/bugs'; });
  const ask = page.getByRole('alertdialog', { name: 'Discard changes?' });
  await ask.getByRole('button', { name: 'Discard' }).click();
  await expect(page.locator('.modal')).toHaveCount(0);
  expect(w.posts).toEqual([]);
});

const FORMS = [
  ['#topbar-primary button', 'dialog', 'Mark fixed'],
  ['[data-action="adopt"]', 'dialog', 'Adopt into a task'],
  ['[data-action="promote"]', 'dialog', 'Promote to an issue'],
  ['[data-action="shelve"]', 'alertdialog', 'Shelve B-031?'],
];
const AXE_RULES = (id) => ['color-contrast', 'label', 'select-name'].includes(id) || id.startsWith('aria-');

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): the action row and each form are clean; every control is 44px tall at 390px`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await open(page, '#/bug/B-031', { theme });
    await expect(mount(page).locator('h1')).toBeVisible();
    await page.addScriptTag({ content: axeSource });
    const run = (sel) => page.evaluate((s) => window.axe.run(s, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] } })
      .then((r) => r.violations.map((v) => ({ id: v.id, nodes: v.nodes.map((n) => n.target.join(' ')).join(' | ') }))), sel);
    const bad = (list, where) => list.filter((v) => AXE_RULES(v.id)).map((v) => `${where} ${v.id}: ${v.nodes}`);
    expect(bad(await run('#screen-mount'), 'page')).toEqual([]);
    const short = (sel) => page.evaluate((s) => [...document.querySelectorAll(s)]
      .filter((el) => el.getClientRects().length && el.getBoundingClientRect().height < 44)
      .map((el) => `${el.tagName.toLowerCase()}.${el.className} "${(el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 30)}" ${Math.round(el.getBoundingClientRect().height)}`), sel);
    expect(await short('.dp-actions .btn')).toEqual([]);
    for (const [opener, role, name] of FORMS) {
      await page.locator(opener).click();
      const dlg = page.getByRole(role, { name });
      await expect(dlg).toBeVisible();
      expect(bad(await run('.modal'), name)).toEqual([]);
      expect(await short('.modal button, .modal input, .modal select, .modal textarea, .modal [contenteditable="true"]')).toEqual([]);
      await page.keyboard.press('Escape');
      await expect(dlg).toHaveCount(0);
    }
  });
}

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): the bug page has no violations`, async ({ page }) => {
    for (const id of ['B-031', 'B-030']) {
      await open(page, `#/bug/${id}`, { theme });
      await expect(mount(page).locator('h1')).toBeVisible();
      await page.addScriptTag({ content: axeSource });
      const result = await page.evaluate(() => window.axe.run('#screen-mount', { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] } }));
      expect(result.violations.map((v) => `${id} ${v.id}: ${v.nodes.map((n) => n.target).join(' | ')}`)).toEqual([]);
    }
  });
}
