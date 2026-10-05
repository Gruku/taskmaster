// User intent: one repeatable screenshot sweep of the two task modals (detail and Create/Edit) and the full task page in
// every state the user meets, in both themes and both widths — from the static viewer with every API call mocked, never a
// live backlog. Unmocked writes, page errors and native dialogs are listed at the end and fail the run.
//
// Usage: node viewer/tests/tools/capture-modals.mjs <out-dir> [--only=name,name] [--themes=dark,light] [--widths=d,m] [--port=8799]
//   writes <out-dir>/<scene>.<theme>.<d|m>.png and <out-dir>/metrics.json (keys "<scene>.<theme>.<d|m>").
import { chromium } from '@playwright/test';
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { mockApi, unmockedWrites } from '../mock-api.js';
import * as F from '../mock-fixtures.js';

const require = createRequire(import.meta.url);
const AXE = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
const here = path.dirname(fileURLToPath(import.meta.url));

const [OUT_ARG, ...FLAGS] = process.argv.slice(2);
const usage = (why) => {
  if (why) console.error(why);
  console.error('usage: node capture-modals.mjs <out-dir> [--only=a,b] [--themes=dark,light] [--widths=d,m] [--port=8799]');
  process.exit(2);
};
if (!OUT_ARG || OUT_ARG.startsWith('--')) usage();
const flag = (name) => FLAGS.find(f => f.startsWith(`--${name}=`))?.split('=')[1].split(',').filter(Boolean);
// A misspelt value would otherwise capture nothing and still exit 0.
const choice = (name, allowed) => {
  const picked = flag(name);
  if (!picked) return allowed;
  const unknown = picked.filter(v => !allowed.includes(v));
  if (unknown.length || !picked.length) usage(`--${name}: expected any of ${allowed.join(', ')}; got "${picked.join(',')}"`);
  return picked;
};
const THEMES = choice('themes', ['dark', 'light']);
const WIDTHS = choice('widths', ['d', 'm']);
const PORT = Number(flag('port')?.[0] ?? 8799);
if (!Number.isInteger(PORT) || PORT <= 0) usage('--port: expected a port number');
const BASE = `http://127.0.0.1:${PORT}`;
const OUT = path.resolve(OUT_ARG);
fs.mkdirSync(OUT, { recursive: true });

// The fixture board plus enough cards that every column has company behind the modal.
const EXTRA = [
  ['T-108', 'Archive view: restore the filter chips', 'todo', 'low', 'viewer'],
  ['T-109', 'Settings screen reads the new tokens', 'in-progress', 'medium', 'viewer'],
  ['T-110', 'Writer mutex: bound the wait and report it', 'blocked', 'high', 'store'],
  ['T-111', 'Drop the legacy JSON mirror', 'done', 'medium', 'store'],
  ['T-112', 'Handover quotes render as markdown', 'in-review', 'low', 'viewer'],
].map(([id, title, status, priority, epic]) => ({ id, title, status, priority, epic, phase: 'P1', depends_on: [] }));
const BOARD = { ...F.BOARD, tasks: [...F.BOARD.tasks, ...EXTRA], context: { active_epic: 'viewer' } };

const TABLE = {
  '/api/board': BOARD, '/api/backlog': BOARD,
  '/api/task/T-102/detail': F.taskDetail(F.DETAIL_TASK, 't1', F.RICH_RELATED),
  '/api/task/T-104/detail': F.taskDetail(F.EMPTY_TASK),
  '/api/task/T-105/detail': F.taskDetail(F.LONG_TASK, 't1', F.LONG_RELATED),
  // The error state: the store answers with a raw message the modal must not print.
  '/api/task/T-103/detail': { status: 500, json: { ok: false, error: 'sqlite3.OperationalError: database is locked' } },
};

// /api/bugs?found_in=<id> answers only that task's bugs; mockApi keys on the path alone and would hand every task the bug.
const BUGS = [{ id: 'B-031', title: 'Card edge vanishes on the light ground', status: 'open', found_in: 'T-102' }];
const bugsByTask = (page) => page.route('**/api/bugs*', (route) => {
  const found = new URL(route.request().url()).searchParams.get('found_in');
  return route.fulfill({ json: BUGS.filter((b) => !found || b.found_in === found) });
});

// ── How each scene is reached ──
const openCard = (id) => async (page) => {
  await page.goto(`${BASE}/#/kanban`);
  await page.locator(`.card-task[data-task-id="${id}"]`).click();
  await page.locator('.modal--detail .td-doc--embedded, .modal--detail .tm-empty[data-state="error"], .modal--detail .tm-empty').first().waitFor();
};
const openCreate = async (page) => {
  await page.goto(`${BASE}/#/kanban`);
  await page.locator('#topbar-actions [aria-label="Add task"]').click();
  await page.locator('.modal--form').waitFor();
};
const openPage = (id) => async (page) => {
  await page.goto(`${BASE}/#/task/${id}`);
  await page.locator('.td-doc--page').waitFor();
};
const ctl = (dialog, key) => dialog.locator(`[data-key="${key}"]`).locator('input, select, textarea').first();
// A create POST left unanswered: the form stays in its saving state for the shot.
const holdCreate = (page) => page.route('**/api/tasks', (route) => (route.request().method() === 'POST' ? undefined : route.fallback()));

// [name, { open, drive?, routes?, fullPage?, scope? }] — scope is where axe looks (the topmost dialog by default).
const ALL_SCENES = [
  ['detail-rich', { open: openCard('T-102') }],
  // The rail's last panel in view: beside the body on a wide dialog, below it on a narrow one.
  ['detail-rich-rail', { open: openCard('T-102'), drive: async (p) => {
    await p.locator('.modal--detail [data-test="rail"]').evaluate((r) => r.scrollIntoView({ block: 'end' }));
  } }],
  ['detail-empty', { open: openCard('T-104') }],
  ['detail-long', { open: openCard('T-105') }],
  ['detail-error', { open: openCard('T-103') }],
  ['detail-edit-stacked', { open: openCard('T-102'), drive: async (p) => {
    await p.locator('.modal--detail [data-action="edit"]').click();
    await p.locator('.modal--form').waitFor();
  } }],
  ['create-untouched', { open: openCreate }],
  ['create-validation', { open: openCreate, drive: async (p) => {
    const dialog = p.locator('.modal--form');
    await ctl(dialog, 'description').fill('Collect the merged tasks.');
    await ctl(dialog, 'stage').fill('-1');
    await ctl(dialog, 'estimate').fill('0');
    await dialog.locator('[data-save]').click();
    await dialog.locator('[aria-invalid="true"]').first().waitFor();
  } }],
  ['create-saving', { open: openCreate, routes: holdCreate, drive: async (p) => {
    await p.keyboard.type('Write the release notes for 7.1');
    await p.locator('.modal--form [data-save]').click();
  } }],
  ['discard-confirm', { open: openCreate, drive: async (p) => {
    await p.keyboard.type('Write the release notes for 7.1');
    await p.keyboard.press('Escape');
    await p.locator('.modal--confirm').waitFor();
  } }],
  ['page-rich', { open: openPage('T-102'), fullPage: true, scope: '#screen-mount' }],
  ['page-empty', { open: openPage('T-104'), fullPage: true, scope: '#screen-mount' }],
];
const ONLY = flag('only');
const unknownScenes = (ONLY || []).filter(n => !ALL_SCENES.some(([name]) => name === n));
if (unknownScenes.length || (ONLY && !ONLY.length)) usage(`--only: unknown scene(s) "${unknownScenes.join(',')}"; known: ${ALL_SCENES.map(([n]) => n).join(', ')}`);
const SCENES = ALL_SCENES.filter(([name]) => !ONLY || ONLY.includes(name));
const VIEWPORTS = [['d', 1440, 900], ['m', 390, 844]].filter(([vk]) => WIDTHS.includes(vk));

// The full page scrolls inside the app frame, not the document, so a full-page screenshot alone stops at the fold:
// the viewport is grown by the longest inner scroll first.
async function growToContent(page, w, h) {
  const extra = await page.evaluate(() => Math.max(0, ...[...document.querySelectorAll('body *')].map((el) => {
    const oy = getComputedStyle(el).overflowY;
    return (oy === 'auto' || oy === 'scroll') ? el.scrollHeight - el.clientHeight : 0;
  })));
  if (extra > 0) await page.setViewportSize({ width: w, height: Math.min(h + extra, 12000) });
}

async function inspect(page, scope) {
  return page.evaluate((scope) => {
    const root = scope ? document.querySelector(scope) : [...document.querySelectorAll('[role="dialog"], [role="alertdialog"]')].pop();
    const box = root?.getBoundingClientRect();
    return {
      theme: document.documentElement.dataset.theme,
      overflowX: document.documentElement.scrollWidth - innerWidth,
      root: root ? { w: Math.round(box.width), h: Math.round(box.height), right: Math.round(box.right) } : null,
      label: root?.getAttribute('aria-labelledby') ? document.getElementById(root.getAttribute('aria-labelledby'))?.textContent.trim().slice(0, 80) : null,
      focused: document.activeElement ? `${document.activeElement.tagName.toLowerCase()}${document.activeElement.className ? '.' + String(document.activeElement.className).trim().split(/\s+/).join('.') : ''}` : null,
      mains: document.querySelectorAll('main').length,
    };
  }, scope);
}

async function axe(page, scope) {
  await page.addScriptTag({ content: AXE });
  return page.evaluate(async (scope) => {
    const root = scope ? document.querySelector(scope) : [...document.querySelectorAll('[role="dialog"], [role="alertdialog"]')].pop();
    const r = await axe.run(root ?? document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] }, resultTypes: ['violations'] });
    return r.violations.map(v => ({ id: v.id, n: v.nodes.length, nodes: v.nodes.slice(0, 6).map(n => n.target.join(' ')) }));
  }, scope);
}

const server = spawn(process.execPath, [path.join(here, 'static-server.mjs'), String(PORT)], { stdio: 'ignore' });
const stop = () => { try { server.kill(); } catch { /* already gone */ } };
process.on('exit', stop);
let up = false;
for (let i = 0; i < 50 && !up; i++) {
  try { up = (await fetch(`${BASE}/index.html`)).ok; } catch { /* not yet */ }
  if (!up) await new Promise((ok) => setTimeout(ok, 200));
}
if (!up) { console.error(`static server did not start on ${PORT}`); process.exit(1); }

const browser = await chromium.launch();
const results = {};
const problems = [];
try {
  for (const theme of THEMES) {
    for (const [vk, w, h] of VIEWPORTS) {
      for (const [name, scene] of SCENES) {
        const key = `${name}.${theme}.${vk}`;
        const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1, colorScheme: theme,
          reducedMotion: 'reduce', serviceWorkers: 'block' });
        await ctx.addInitScript((t) => { try { localStorage.setItem('tm.theme', t); } catch { /* storage unavailable */ } }, theme);
        const page = await ctx.newPage();
        const errors = [];
        page.on('pageerror', (e) => errors.push(`[pageerror] ${e.message.slice(0, 200)}`));
        page.on('dialog', (d) => { errors.push(`[native dialog] ${d.message()}`); d.dismiss(); });
        try {
          await mockApi(page, { '/api/viewer/prefs': { theme, ui: {}, screens: {} }, ...TABLE });
          await bugsByTask(page);
          await scene.routes?.(page);
          await scene.open(page);
          await page.evaluate(() => document.fonts.ready);
          await scene.drive?.(page);
          // The pointer is left where the card was clicked; parked on the overlay corner it hovers nothing in the shot.
          await page.mouse.move(1, h - 1);
          if (scene.fullPage) await growToContent(page, w, h);
          await page.waitForTimeout(400);
          await page.screenshot({ path: path.join(OUT, `${key}.png`), fullPage: !!scene.fullPage });
          const m = await inspect(page, scene.scope);
          let ax = [];
          try { ax = await axe(page, scene.scope); } catch (e) { ax = [{ id: 'axe-error', n: 1, nodes: [e.message] }]; }
          const writes = unmockedWrites(page);
          results[key] = { file: `${key}.png`, ...m, errors, unmockedWrites: writes, axe: ax };
          if (m.theme !== theme) problems.push(`${key}: page is ${m.theme}`);
          if (errors.length) problems.push(`${key}: ${errors.join('; ')}`);
          if (writes.length) problems.push(`${key}: unmocked writes ${writes.join(', ')}`);
          console.log(key, 'overflowX', m.overflowX, 'axe', ax.map(v => `${v.id}×${v.n}`).join(' ') || 'clean');
        } catch (e) {
          problems.push(`${key}: ${e.message.split('\n')[0]}`);
          console.error(`FAILED ${key}: ${e.message.split('\n')[0]}`);
        } finally {
          await ctx.close();
        }
      }
    }
  }
} finally {
  fs.writeFileSync(path.join(OUT, 'metrics.json'), JSON.stringify({ results, problems }, null, 1));
  await browser.close();
  stop();
}
for (const p of problems) console.error('PROBLEM ' + p);
process.exit(problems.length ? 1 : 0);
