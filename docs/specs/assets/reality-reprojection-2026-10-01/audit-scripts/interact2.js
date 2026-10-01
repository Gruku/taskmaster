// User intent: targeted re-checks (modal focus trap, stale topbar, input focus styling, axe inside modals) for the RR audit.
// Read-only: non-GET API calls are answered locally.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const BASE = process.argv[2] || 'http://127.0.0.1:43790';
const OUT = path.join(__dirname, 'shots');
const AXE = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
const log = {};

const settle = async (p, ms = 1200) => { try { await p.waitForLoadState('networkidle', { timeout: 6000 }); } catch {} await p.waitForTimeout(ms); };
const focusInfo = (p) => p.evaluate(() => {
  const el = document.activeElement;
  return { s: el.tagName.toLowerCase() + '.' + String(el.className).split(' ')[0], inDialog: !!el.closest('.dm-overlay,.em-overlay,[role=dialog]'), name: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 30) };
});
async function axeRun(p, ctxSel) {
  await p.addScriptTag({ content: AXE });
  return p.evaluate(async (sel) => {
    const r = await axe.run(sel ? document.querySelector(sel) : document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] } });
    return r.violations.map(v => ({ id: v.id, n: v.nodes.length, ex: v.nodes.slice(0, 4).map(n => n.target.join(' ') + ' :: ' + (n.any[0]?.message || '').slice(0, 120)) }));
  }, ctxSel);
}

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.route('**/api/**', r => (r.request().method() === 'GET' ? r.continue() : r.fulfill({ status: 200, contentType: 'application/json', body: '{}' })));
  try {
    // 1. Detail modal focus trap
    await page.goto(`${BASE}/v3#/kanban`); await settle(page);
    await page.locator('.card-task .card-title').first().click(); await page.waitForTimeout(1500);
    const steps = [];
    for (let i = 0; i < 30; i++) { await page.keyboard.press('Tab'); steps.push(await focusInfo(page)); }
    log.modalTab = steps;
    log.modalAxe = await axeRun(page, '.dm-overlay');
    await page.keyboard.press('Escape'); await page.waitForTimeout(500);
    log.modalClosedByEsc = !(await page.locator('.dm-overlay').count());
    log.focusAfterClose = await focusInfo(page);

    // 2. Stale topbar via in-app hash navigation
    await page.evaluate(() => { location.hash = '#/task/v3-polish-054'; }); await settle(page);
    await page.evaluate(() => { location.hash = '#/task/NOPE-999'; }); await settle(page);
    log.staleTopbar = await page.evaluate(() => [...document.querySelectorAll('#topbar-actions button')].map(b => b.textContent.trim() + (b.disabled ? '(disabled)' : '')));
    await page.screenshot({ path: path.join(OUT, 'task-missing-after-nav.d.png') });
    await page.evaluate(() => { location.hash = '#/bug/B-999'; }); await settle(page);
    log.bugMissingText = await page.evaluate(() => document.getElementById('screen-mount').innerText.slice(0, 200));

    // 3. Create modal: escape + axe + input focus styling
    await page.goto('about:blank'); await page.goto(`${BASE}/v3#/kanban`); await settle(page);
    await page.locator('#topbar-actions .tm-action--primary').first().click(); await page.waitForTimeout(800);
    log.createAxe = await axeRun(page, '#entity-modal-host');
    const inp = page.locator('#entity-modal-host input.ef-text-input').first();
    const before = await inp.evaluate(el => { const c = getComputedStyle(el); return [c.borderColor, c.outlineStyle, c.boxShadow, c.backgroundColor].join(' | '); });
    await inp.focus(); await page.keyboard.press('Shift+Tab'); await page.keyboard.press('Tab');
    const after = await inp.evaluate(el => { const c = getComputedStyle(el); return [c.borderColor, c.outlineStyle, c.boxShadow, c.backgroundColor].join(' | '); });
    log.createInputFocus = { before, after };
    await page.screenshot({ path: path.join(OUT, 'task-create-modal-focus.d.png') });
    await page.keyboard.press('Escape'); await page.waitForTimeout(400);
    log.createOpenAfterEsc = await page.locator('#entity-modal-host .em-overlay, #entity-modal-host [role=dialog]').count();
    await page.screenshot({ path: path.join(OUT, 'task-create-after-esc.d.png') });

    // 4. Topbar search input focus styling
    await page.goto('about:blank'); await page.goto(`${BASE}/v3#/table`); await settle(page);
    const si = page.locator('#topbar-actions .tm-search input');
    const sb = await si.evaluate(el => { const c = getComputedStyle(el.parentElement); return c.borderColor + ' | ' + c.boxShadow + ' | ' + getComputedStyle(el).outlineStyle; });
    await si.focus();
    const sa = await si.evaluate(el => { const c = getComputedStyle(el.parentElement); return c.borderColor + ' | ' + c.boxShadow + ' | ' + getComputedStyle(el).outlineStyle; });
    log.searchFocus = { sb, sa };
    await page.screenshot({ path: path.join(OUT, 'search-focus.d.png'), clip: { x: 236, y: 0, width: 1204, height: 90 } });
    // ⌘K hint: does Ctrl/Meta+K focus search?
    await page.locator('h1').click();
    await page.keyboard.press('Control+k'); await page.waitForTimeout(200);
    const k1 = await focusInfo(page);
    await page.keyboard.press('Meta+k'); await page.waitForTimeout(200);
    log.cmdK = { ctrlK: k1, metaK: await focusInfo(page) };
    // Table: sortable header keyboard access + truncated cell titles
    log.tableHeaders = await page.evaluate(() => [...document.querySelectorAll('thead th')].map(th => ({ t: th.textContent.trim(), tab: th.tabIndex, role: th.getAttribute('role'), aria: th.getAttribute('aria-sort'), btn: !!th.querySelector('button'), cursor: getComputedStyle(th).cursor })));
    log.tableTruncTitles = await page.evaluate(() => { const td = [...document.querySelectorAll('tbody tr:first-child td')]; return td.map(c => ({ txt: c.textContent.trim().slice(0, 40), title: c.getAttribute('title') || c.firstElementChild?.getAttribute?.('title') || '', clipped: c.scrollWidth > c.clientWidth || [...c.querySelectorAll('*')].some(x => x.scrollWidth > x.clientWidth + 1) })); });
    log.tableRowFocusable = await page.evaluate(() => { const tr = document.querySelector('tbody tr'); return { tab: tr.tabIndex, role: tr.getAttribute('role'), link: !!tr.querySelector('a[href]'), cursor: getComputedStyle(tr).cursor }; });

    // 5. Kanban epic "More" dropdown: escape + outside click
    await page.goto('about:blank'); await page.goto(`${BASE}/v3#/kanban`); await settle(page);
    await page.locator('.kanban-epic-more').first().click(); await page.waitForTimeout(400);
    await page.mouse.click(700, 60); await page.waitForTimeout(400);
    log.epicMoreOpenAfterOutsideClick = await page.locator('.kanban-epic-dropdown').evaluateAll(els => els.filter(e => e.offsetParent).length);
    await page.screenshot({ path: path.join(OUT, 'kanban-epic-more-after-outside-click.d.png') });

    // 6. Contrast samples on key tokens (computed by axe already) — and sticky-note text
    // 7. Settings radio accent color
    await page.goto('about:blank'); await page.goto(`${BASE}/v3#/settings`); await settle(page);
    log.radio = await page.evaluate(() => { const r = document.querySelector('input[type=radio]'); const c = getComputedStyle(r); return { accent: c.accentColor, appearance: c.appearance }; });
  } catch (e) { console.error('FAILED', e.message.slice(0, 400)); }
  finally {
    fs.writeFileSync(path.join(__dirname, 'interact2.json'), JSON.stringify(log, null, 1));
    await browser.close();
  }
})();
