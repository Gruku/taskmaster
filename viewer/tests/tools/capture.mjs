// User intent: one repeatable, read-only screenshot + metrics sweep of every viewer route in both themes and both widths,
// so each stage of the Reality Reprojection re-skin can be judged against real data without ever writing to that backlog.
// Every non-GET/HEAD request is answered locally and listed at the end; the theme is set in the browser only, never in server prefs.
//
// Usage: node viewer/tests/tools/capture.mjs <base-url> <out-dir> [--only=name,name] [--themes=dark,light] [--widths=d,m]
//   writes <out-dir>/<route>.<theme>.<d|m>.png and <out-dir>/metrics.json (keys "<route>.<theme>.<d|m>").
import { chromium } from '@playwright/test';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const require = createRequire(import.meta.url);
const AXE = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');

const [BASE, OUT_ARG, ...FLAGS] = process.argv.slice(2);
if (!BASE || !OUT_ARG) {
  console.error('usage: node capture.mjs <base-url> <out-dir> [--only=a,b] [--themes=dark,light] [--widths=d,m]');
  process.exit(2);
}
const OUT = path.resolve(OUT_ARG);
fs.mkdirSync(OUT, { recursive: true });
const flag = (name) => FLAGS.find(f => f.startsWith(`--${name}=`))?.split('=')[1].split(',').filter(Boolean);
const ONLY = flag('only');

const ROUTES = [
  ['dashboard', '/dashboard'],
  ['kanban', '/kanban'],
  ['table', '/table'],
  ['task-empty', '/task'],
  ['task-inprogress', '/task/v3-polish-045'],
  ['task-inprogress-B', '/task/v3-polish-045?view=B'],
  ['task-rich-done', '/task/mobile-dash-003'],
  ['task-inreview', '/task/v3-polish-054'],
  ['task-missing', '/task/NOPE-999'],
  ['epics', '/epics'],
  ['epic-polish', '/epic/v3-polish'],
  ['epic-mobile', '/epic/mobile-dash'],
  ['epic-missing', '/epic/nope'],
  ['sessions', '/sessions'],
  ['issues', '/issues'],
  ['issue-004', '/issue/ISS-004'],
  ['issue-missing', '/issue/ISS-999'],
  ['bugs', '/bugs'],
  ['bug-086', '/bug/B-086'],
  ['bug-001-fixed', '/bug/B-001'],
  ['bug-missing', '/bug/B-999'],
  ['ideas', '/ideas'],
  ['archived', '/archived'],
  ['settings', '/settings'],
].filter(([name]) => !ONLY || ONLY.includes(name));

const THEMES = (flag('themes') || ['dark', 'light']);
const VIEWPORTS = [['d', 1440, 900], ['m', 390, 844]].filter(([vk]) => (flag('widths') || ['d', 'm']).includes(vk));

// blocked: writes answered here. seenWrites: every write the browser attempted. The two must match,
// or a write went out some way the route handler did not see.
const blocked = [];
const seenWrites = [];
const isWrite = (req) => req.method() !== 'GET' && req.method() !== 'HEAD';

async function metrics(page) {
  return page.evaluate(() => {
    const vw = innerWidth;
    const sel = (el) => {
      if (!el || el === document) return '';
      let s = el.tagName.toLowerCase();
      if (el.id) s += '#' + el.id;
      const cls = (typeof el.className === 'string' ? el.className : '').trim().split(/\s+/).filter(Boolean).slice(0, 3);
      if (cls.length) s += '.' + cls.join('.');
      return s;
    };
    const visible = (el) => {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none';
    };
    const hasText = (el) => [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    const all = [...document.querySelectorAll('body *')].filter(visible);
    // horizontal overflow past viewport
    const overflowX = document.documentElement.scrollWidth - vw;
    const offRight = all.filter(el => el.getBoundingClientRect().right > vw + 1)
      .map(el => ({ s: sel(el), right: Math.round(el.getBoundingClientRect().right), w: Math.round(el.getBoundingClientRect().width) }));
    // truncated text (ellipsis or hidden overflow with clipped content)
    const truncated = all.filter(el => {
      const cs = getComputedStyle(el);
      if (!(cs.textOverflow === 'ellipsis' || cs.webkitLineClamp !== 'none')) return false;
      return el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 2;
    }).map(el => ({ s: sel(el), text: el.textContent.trim().slice(0, 80), title: el.getAttribute('title') || el.closest('[title]')?.getAttribute('title') || '' }));
    // pointer-cursor elements that are not keyboard focusable
    const focusableSel = 'a[href],button,input,select,textarea,summary,[tabindex]:not([tabindex="-1"]),[contenteditable="true"]';
    const pointerNoKb = all.filter(el => getComputedStyle(el).cursor === 'pointer' && !el.matches(focusableSel) && !el.closest(focusableSel) && !(el.parentElement && getComputedStyle(el.parentElement).cursor === 'pointer'))
      .map(el => ({ s: sel(el), text: el.textContent.trim().slice(0, 50), role: el.getAttribute('role') || '' }));
    // small targets
    const smallTargets = all.filter(el => el.matches('a[href],button,input,select,[role=button],[tabindex]:not([tabindex="-1"])'))
      .map(el => ({ el, r: el.getBoundingClientRect() }))
      .filter(({ r }) => r.width < 24 || r.height < 24)
      .map(({ el, r }) => ({ s: sel(el), w: Math.round(r.width), h: Math.round(r.height), text: (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 30) }));
    // font-size / radius / shadow histograms across visible elements
    const hist = (fn) => { const m = {}; for (const el of all) { const v = fn(el); if (v) m[v] = (m[v] || 0) + 1; } return m; };
    const fontSizes = hist(el => hasText(el) ? getComputedStyle(el).fontSize : null);
    const radii = hist(el => { const r = getComputedStyle(el).borderTopLeftRadius; return r !== '0px' ? r : null; });
    const shadowed = all.filter(el => getComputedStyle(el).boxShadow !== 'none');
    const shadows = shadowed.map(el => ({ s: sel(el), v: getComputedStyle(el).boxShadow.slice(0, 90) }));
    // shell chrome = the frame itself plus everything in the sidebar and topbar; screen content is judged separately
    const chromeShadows = shadowed.filter(el => el.matches('.shell, .main, .sidebar, .sidebar *, .topbar, .topbar *'))
      .map(el => ({ s: sel(el), v: getComputedStyle(el).boxShadow.slice(0, 90) }));
    const leftRails = all.filter(el => {
      const cs = getComputedStyle(el);
      const lw = parseFloat(cs.borderLeftWidth);
      if (lw < 2) return false;
      return parseFloat(cs.borderRightWidth) < lw && cs.borderLeftStyle !== 'none';
    }).map(el => ({ s: sel(el), c: getComputedStyle(el).borderLeftColor, w: getComputedStyle(el).borderLeftWidth }));
    const family = (el) => getComputedStyle(el).fontFamily.split(',')[0].trim().replace(/^["']|["']$/g, '');
    const fontFamilies = hist(el => hasText(el) ? family(el) : null);
    // which selectors render text in a family outside the three RR voices
    const RR_FAMILIES = new Set(['DM Sans', 'JetBrains Mono', 'League Spartan']);
    const offFamilies = {};
    for (const el of all) {
      if (!hasText(el)) continue;
      const f = family(el);
      if (RR_FAMILIES.has(f)) continue;
      const k = `${f} <- ${sel(el)}`;
      offFamilies[k] = (offFamilies[k] || 0) + 1;
    }
    const smallText = hist(el => hasText(el) && parseFloat(getComputedStyle(el).fontSize) < 11 ? `${getComputedStyle(el).fontSize} ${sel(el)}` : null);
    const italic = hist(el => hasText(el) && getComputedStyle(el).fontStyle === 'italic' ? sel(el) : null);
    const topbar = document.getElementById('topbar');
    return {
      theme: document.documentElement.dataset.theme,
      title: document.getElementById('page-title')?.textContent,
      topbarH: topbar ? Math.round(topbar.getBoundingClientRect().height) : null,
      topbarText: topbar?.innerText.replace(/\s+/g, ' ').trim().slice(0, 300),
      topbarButtons: topbar ? [...topbar.querySelectorAll('button, a[href], input, select')].filter(visible)
        .map(el => el.getAttribute('aria-label') || el.textContent.trim() || el.getAttribute('placeholder') || sel(el)) : [],
      docW: document.documentElement.scrollWidth, docH: document.documentElement.scrollHeight, overflowX,
      offRight: offRight.slice(0, 25), offRightCount: offRight.length,
      truncated: truncated.slice(0, 30), truncatedCount: truncated.length,
      pointerNoKb: pointerNoKb.slice(0, 30), pointerNoKbCount: pointerNoKb.length,
      smallTargets: smallTargets.slice(0, 30), smallTargetsCount: smallTargets.length,
      fontSizes, radii, fontFamilies, offFamilies, smallText, italic,
      shadows: shadows.slice(0, 20), shadowCount: shadows.length,
      chromeShadows: chromeShadows.slice(0, 20), chromeShadowCount: chromeShadows.length,
      leftRails: leftRails.slice(0, 20), leftRailCount: leftRails.length,
      bodyText: document.getElementById('screen-mount')?.innerText.slice(0, 1500),
      screenButtons: [...document.querySelectorAll('#screen-mount button')].filter(visible).slice(0, 40)
        .map(el => `${(el.getAttribute('aria-label') || el.textContent.trim()).slice(0, 40)}${el.disabled ? ' [disabled]' : ''}`),
    };
  });
}

async function axe(page) {
  await page.addScriptTag({ content: AXE });
  return page.evaluate(async () => {
    const r = await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'best-practice'] }, resultTypes: ['violations'] });
    return r.violations.map(v => ({ id: v.id, impact: v.impact, help: v.help, n: v.nodes.length,
      nodes: v.nodes.slice(0, 12).map(n => ({ t: n.target.join(' '), s: (n.any[0]?.message || n.failureSummary || '').slice(0, 200) })) }));
  });
}

async function newPage(browser, theme, w, h) {
  // Service workers are blocked so no request can bypass the route handler below.
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1, colorScheme: theme, serviceWorkers: 'block' });
  await ctx.addInitScript((t) => { try { localStorage.setItem('tm.theme', t); } catch { /* storage unavailable */ } }, theme);
  ctx.on('request', (req) => { if (isWrite(req)) seenWrites.push(`${req.method()} ${req.url()}`); });
  // All URLs, not only /api/**: a write to any path is answered here and never reaches the server.
  await ctx.route('**/*', async (route) => {
    const req = route.request();
    if (isWrite(req)) {
      blocked.push(`${req.method()} ${req.url()}`);
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    }
    // The app takes its theme from server prefs on boot; rewrite the answer instead of changing the prefs.
    if (new URL(req.url()).pathname === '/api/viewer/prefs') {
      const res = await route.fetch();
      let json = {};
      try { json = await res.json(); } catch { /* not JSON: serve a theme-only object */ }
      return route.fulfill({ response: res, json: { ...json, theme } });
    }
    return route.continue();
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`[${m.type()}] ${m.text().slice(0, 200)}`); });
  page.on('pageerror', e => errors.push(`[pageerror] ${e.message.slice(0, 200)}`));
  return { ctx, page, errors };
}

async function settle(page) {
  try { await page.waitForLoadState('networkidle', { timeout: 8000 }); } catch { /* long-poll or slow route: capture what is there */ }
  await page.waitForTimeout(1200);
}

const browser = await chromium.launch();
const results = {};
let exit = 0;
try {
  for (const theme of THEMES) {
    for (const [vk, w, h] of VIEWPORTS) {
      const { ctx, page, errors } = await newPage(browser, theme, w, h);
      for (const [name, route] of ROUTES) {
        const t0 = Date.now();
        // about:blank first: consecutive hash-only URLs would otherwise be an in-app navigation, not a fresh load.
        await page.goto('about:blank');
        // Cleared after leaving the previous route, so its aborted in-flight poll is not blamed on this one.
        errors.length = 0;
        await page.goto(`${BASE}/v3#${route}`, { waitUntil: 'domcontentloaded' });
        await settle(page);
        const key = `${name}.${theme}.${vk}`;
        const file = `${key}.png`;
        await page.screenshot({ path: path.join(OUT, file), fullPage: true });
        const m = await metrics(page);
        let ax = [];
        try { ax = await axe(page); } catch (e) { ax = [{ id: 'axe-error', help: e.message }]; }
        const contrast = ax.find(v => v.id === 'color-contrast')?.n || 0;
        results[key] = { route, theme, viewport: vk, file, ms: Date.now() - t0, url: page.url(), errors: [...errors], ...m, contrast, axe: ax };
        if (m.theme !== theme) { console.error(`THEME MISMATCH ${key}: page is ${m.theme}`); exit = 1; }
        console.log(key, 'overflowX', m.overflowX, 'contrast', contrast, 'errors', errors.length);
      }
      await ctx.close();
    }
  }
} finally {
  const leaked = seenWrites.filter(w => !blocked.includes(w));
  fs.writeFileSync(path.join(OUT, 'metrics.json'), JSON.stringify({ base: BASE, results, blocked, leaked }, null, 1));
  await browser.close();
  console.log(`\nblocked write requests (answered locally, never sent): ${blocked.length}`);
  for (const b of blocked) console.log('  ' + b);
  if (leaked.length) {
    console.error(`WRITES NOT INTERCEPTED: ${leaked.length}`);
    for (const l of leaked) console.error('  ' + l);
    exit = 1;
  }
}
process.exit(exit);
