// User intent: read-only screenshot + metrics sweep of every viewer route for the RR re-skin audit.
// Non-GET API requests are aborted so nothing is written to the real backlog or prefs.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.argv[2] || 'http://127.0.0.1:43790';
const OUT = path.join(__dirname, 'shots');
fs.mkdirSync(OUT, { recursive: true });
const AXE = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');

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
];

const VIEWPORTS = [['d', 1440, 900], ['m', 390, 844]];
const blocked = [];

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
    const all = [...document.querySelectorAll('body *')].filter(visible);
    // horizontal overflow past viewport
    const overflowX = document.documentElement.scrollWidth - vw;
    const offRight = all.filter(el => el.getBoundingClientRect().right > vw + 1)
      .filter(el => !all.some(p => p !== el && p.contains(el) && p.getBoundingClientRect().right > vw + 1 && getComputedStyle(p).overflowX === 'visible' && false))
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
    const fontSizes = hist(el => [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) ? getComputedStyle(el).fontSize : null);
    const radii = hist(el => { const r = getComputedStyle(el).borderTopLeftRadius; return r !== '0px' ? r : null; });
    const shadows = all.filter(el => getComputedStyle(el).boxShadow !== 'none').map(el => ({ s: sel(el), v: getComputedStyle(el).boxShadow.slice(0, 90) }));
    const leftRails = all.filter(el => {
      const cs = getComputedStyle(el);
      const lw = parseFloat(cs.borderLeftWidth);
      if (lw < 2) return false;
      return parseFloat(cs.borderRightWidth) < lw && cs.borderLeftStyle !== 'none';
    }).map(el => ({ s: sel(el), c: getComputedStyle(el).borderLeftColor, w: getComputedStyle(el).borderLeftWidth }));
    const fontFamilies = hist(el => [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) ? getComputedStyle(el).fontFamily.split(',')[0] : null);
    return {
      title: document.getElementById('page-title')?.textContent,
      docW: document.documentElement.scrollWidth, docH: document.documentElement.scrollHeight, overflowX,
      offRight: offRight.slice(0, 25), offRightCount: offRight.length,
      truncated: truncated.slice(0, 30), truncatedCount: truncated.length,
      pointerNoKb: pointerNoKb.slice(0, 30), pointerNoKbCount: pointerNoKb.length,
      smallTargets: smallTargets.slice(0, 30), smallTargetsCount: smallTargets.length,
      fontSizes, radii, fontFamilies, shadows: shadows.slice(0, 20), shadowCount: shadows.length,
      leftRails: leftRails.slice(0, 20), leftRailCount: leftRails.length,
      bodyText: document.getElementById('screen-mount')?.innerText.slice(0, 400),
    };
  });
}

async function axe(page) {
  await page.addScriptTag({ content: AXE });
  return page.evaluate(async () => {
    const r = await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'best-practice'] }, resultTypes: ['violations'] });
    return r.violations.map(v => ({ id: v.id, impact: v.impact, help: v.help, n: v.nodes.length,
      nodes: v.nodes.slice(0, 8).map(n => ({ t: n.target.join(' '), s: (n.any[0]?.message || n.failureSummary || '').slice(0, 160) })) }));
  });
}

async function newPage(browser, w, h) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  await page.route('**/api/**', (route) => {
    const req = route.request();
    if (req.method() !== 'GET' && req.method() !== 'HEAD') {
      blocked.push(`${req.method()} ${req.url()}`);
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    }
    return route.continue();
  });
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`[${m.type()}] ${m.text().slice(0, 200)}`); });
  page.on('pageerror', e => errors.push(`[pageerror] ${e.message.slice(0, 200)}`));
  return { ctx, page, errors };
}

async function settle(page) {
  try { await page.waitForLoadState('networkidle', { timeout: 8000 }); } catch {}
  await page.waitForTimeout(1200);
}

(async () => {
  const browser = await chromium.launch();
  const results = {};
  try {
    for (const [vk, w, h] of VIEWPORTS) {
      const { ctx, page, errors } = await newPage(browser, w, h);
      for (const [name, route] of ROUTES) {
        errors.length = 0;
        const t0 = Date.now();
        await page.goto(`${BASE}/v3#${route}`, { waitUntil: 'domcontentloaded' });
        await settle(page);
        const file = `${name}.${vk}.png`;
        await page.screenshot({ path: path.join(OUT, file), fullPage: true });
        const m = await metrics(page);
        let ax = [];
        try { ax = await axe(page); } catch (e) { ax = [{ id: 'axe-error', help: e.message }]; }
        results[`${name}.${vk}`] = { route, file, ms: Date.now() - t0, url: page.url(), errors: [...errors], ...m, axe: ax };
        console.log(vk, name, 'ok', m.overflowX, ax.length);
      }
      await ctx.close();
    }
  } finally {
    fs.writeFileSync(path.join(__dirname, 'metrics.json'), JSON.stringify({ results, blocked }, null, 1));
    await browser.close();
  }
})();
