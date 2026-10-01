// User intent: capture interactive states (modals, menus, focus, hover, mobile drawer) for the RR re-skin audit.
// Read-only: every non-GET API call is intercepted and answered locally; nothing is saved or submitted.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.argv[2] || 'http://127.0.0.1:43790';
const OUT = path.join(__dirname, 'shots');
const log = {};
const blocked = [];

async function mk(browser, w, h, { stripLastTask = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
  const page = await ctx.newPage();
  await page.route('**/api/**', async (route) => {
    const req = route.request();
    if (req.method() !== 'GET' && req.method() !== 'HEAD') {
      blocked.push(`${req.method()} ${req.url()}`);
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    }
    if (stripLastTask && req.url().includes('/api/viewer/prefs')) {
      const r = await route.fetch(); const j = await r.json();
      if (j.ui) delete j.ui.last_task_id;
      return route.fulfill({ response: r, json: j });
    }
    return route.continue();
  });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message.slice(0, 200)));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
  page.errors = errors;
  return { ctx, page };
}
const settle = async (p, ms = 1200) => { try { await p.waitForLoadState('networkidle', { timeout: 6000 }); } catch {} await p.waitForTimeout(ms); };
const go = async (p, r) => { await p.goto('about:blank'); await p.goto(`${BASE}/v3#${r}`); await settle(p); };
const shot = (p, n, opts = {}) => p.screenshot({ path: path.join(OUT, n + '.png'), ...opts });

// Walk Tab N times; record each focused element and whether any visible focus indicator exists.
async function tabWalk(page, n, label) {
  await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); });
  await page.mouse.click(5, 5).catch(() => {});
  const rows = [];
  for (let i = 0; i < n; i++) {
    await page.keyboard.press('Tab');
    const r = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return { s: 'body' };
      const cs = getComputedStyle(el);
      const s = el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '');
      const outline = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0 ? `${cs.outlineWidth} ${cs.outlineStyle} ${cs.outlineColor}` : '';
      const shadow = cs.boxShadow !== 'none' ? cs.boxShadow.slice(0, 60) : '';
      const name = (el.getAttribute('aria-label') || el.textContent || el.getAttribute('placeholder') || '').trim().slice(0, 40);
      const r = el.getBoundingClientRect();
      return { s, name, outline, shadow, fv: el.matches(':focus-visible'), inView: r.bottom > 0 && r.top < innerHeight, inModal: !!el.closest('.dm-overlay,.em-overlay,[role=dialog]') };
    });
    rows.push(r);
  }
  log['tab:' + label] = rows;
  return rows;
}

(async () => {
  const browser = await chromium.launch();
  try {
    // ---------- Desktop ----------
    let { ctx, page } = await mk(browser, 1440, 900);

    // Kanban: hover card, hover button, tab walk, epic "More" dropdown, detail modal
    await go(page, '/kanban');
    const card = page.locator('.card-task').first();
    const cb = await card.boundingBox();
    await shot(page, 'kanban-card-rest.d', { clip: { x: cb.x - 10, y: cb.y - 10, width: cb.width + 20, height: Math.min(cb.height + 20, 400) } });
    await card.hover(); await page.waitForTimeout(400);
    await shot(page, 'kanban-card-hover.d', { clip: { x: cb.x - 10, y: cb.y - 10, width: cb.width + 20, height: Math.min(cb.height + 20, 400) } });
    log.cardHover = await card.evaluate(el => { const cs = getComputedStyle(el); return { transform: cs.transform, shadow: cs.boxShadow, bg: cs.backgroundColor, border: cs.borderColor, tabindex: el.getAttribute('tabindex'), role: el.getAttribute('role') }; });
    const addBtn = page.locator('#topbar-actions .tm-action--primary').first();
    if (await addBtn.count()) { await addBtn.hover(); await page.waitForTimeout(300); await shot(page, 'topbar-primary-hover.d', { clip: { x: 900, y: 0, width: 540, height: 110 } }); }
    await tabWalk(page, 30, 'kanban');
    await shot(page, 'kanban-tab30.d');
    const more = page.locator('.kanban-epic-more').first();
    if (await more.count()) { await more.click(); await page.waitForTimeout(500); await shot(page, 'kanban-epic-more-open.d'); await page.keyboard.press('Escape'); await page.waitForTimeout(300);
      log.epicMoreEscClosed = await page.locator('.kanban-epic-dropdown').evaluateAll(els => els.every(e => !e.offsetParent)); }
    // detail modal via card click
    await go(page, '/kanban');
    await page.locator('.card-task .card-title').first().click();
    await page.waitForTimeout(1500);
    await shot(page, 'detail-modal-task.d');
    log.modal = await page.evaluate(() => {
      const m = document.querySelector('.dm-modal');
      return m ? { labelledby: m.getAttribute('aria-labelledby'), label: m.getAttribute('aria-label'), activeInModal: !!document.activeElement.closest('.dm-overlay'), active: document.activeElement.className, hash: location.hash } : null;
    });
    await tabWalk(page, 25, 'detail-modal');
    await page.keyboard.press('Escape'); await page.waitForTimeout(500);
    log.modalAfterEsc = await page.evaluate(() => ({ open: !!document.querySelector('.dm-overlay'), active: document.activeElement.tagName + '.' + document.activeElement.className }));

    // Task create modal (+ Task) — opened, not submitted
    await go(page, '/kanban');
    await page.locator('#topbar-actions .tm-action--primary').first().click();
    await page.waitForTimeout(800);
    await shot(page, 'task-create-modal.d');
    await tabWalk(page, 12, 'task-create-modal');
    await page.keyboard.press('Escape'); await page.waitForTimeout(300);
    log.createModalAfterEsc = await page.evaluate(() => !!document.querySelector('#entity-modal-host *'));

    // Kanban group variants (prefs PUT blocked)
    for (const g of ['phase', 'epic', 'area']) {
      await go(page, '/kanban');
      await page.locator('select.kanban-select').first().selectOption(g);
      await page.waitForTimeout(800);
      await shot(page, `kanban-group-${g}.d`);
    }
    // Kanban compact density
    await go(page, '/kanban');
    const dens = page.locator('.kanban-density button, [class*=density] button');
    if (await dens.count()) { await dens.first().click(); await page.waitForTimeout(600); await shot(page, 'kanban-density-compact.d'); }

    // Task detail: Edit modal open (not saved), tab walk, tall shot
    await go(page, '/task/v3-polish-054');
    await tabWalk(page, 20, 'task-detail');
    await page.locator('#topbar-actions button', { hasText: 'Edit' }).first().click();
    await page.waitForTimeout(1000);
    await shot(page, 'task-edit-modal.d');
    await page.keyboard.press('Escape'); await page.waitForTimeout(400);

    // Stale topbar on missing task after a good task
    await go(page, '/task/v3-polish-054');
    await go(page, '/task/NOPE-999');
    log.staleTopbarOnMissing = await page.evaluate(() => [...document.querySelectorAll('#topbar-actions button')].map(b => b.textContent.trim() + (b.disabled ? '(disabled)' : '')));

    // Issues view variants
    for (const v of ['Hybrid', 'Severity', 'List']) {
      await go(page, '/issues');
      await page.locator('#topbar-actions .tm-segmented button', { hasText: v }).first().click().catch(() => {});
      await page.waitForTimeout(800);
      await shot(page, `issues-view-${v.toLowerCase()}.d`);
    }
    // Ideas: new idea modal (not submitted), row click
    await go(page, '/ideas');
    await page.locator('button', { hasText: 'New Idea' }).first().click().catch(() => {});
    await page.waitForTimeout(800);
    await shot(page, 'ideas-new-modal.d');
    await page.keyboard.press('Escape');
    await go(page, '/ideas');
    await page.locator('.ideas-row, [class*=idea-row], [data-idea-id]').first().click().catch(() => {});
    await page.waitForTimeout(900);
    await shot(page, 'ideas-row-open.d');
    // Bugs row click
    await go(page, '/bugs');
    await page.locator('[class*=bug-card], [class*=bug-row]').first().click().catch(() => {});
    await page.waitForTimeout(900);
    await shot(page, 'bugs-row-click.d');
    log.bugsRowClickHash = await page.evaluate(() => location.hash);
    // Sessions: open a session in right rail
    await go(page, '/sessions');
    await page.locator('[class*=session-card], [class*=thread-card], .tl-card, [data-session-id]').first().click().catch(() => {});
    await page.waitForTimeout(1200);
    await shot(page, 'sessions-rail-open.d');
    // Table: row hover + tab walk + sort click
    await go(page, '/table');
    await page.locator('tbody tr').nth(2).hover().catch(() => {}); await page.waitForTimeout(300);
    await shot(page, 'table-row-hover.d', { clip: { x: 236, y: 180, width: 1204, height: 260 } });
    await tabWalk(page, 30, 'table');
    // Sidebar collapsed (prefs PUT blocked)
    await go(page, '/dashboard');
    await page.locator('.sidebar-collapse-btn').click(); await page.waitForTimeout(500);
    await shot(page, 'sidebar-collapsed.d');
    await tabWalk(page, 16, 'dashboard-collapsed');
    await ctx.close();

    // Tall desktop captures: grow viewport to the scroll container height
    ({ ctx, page } = await mk(browser, 1440, 900));
    for (const [n, r] of [['dashboard', '/dashboard'], ['task-inreview', '/task/v3-polish-054'], ['epics', '/epics'], ['bugs', '/bugs'], ['ideas', '/ideas'], ['sessions', '/sessions'], ['epic-mobile', '/epic/mobile-dash'], ['archived', '/archived']]) {
      await page.setViewportSize({ width: 1440, height: 900 });
      await go(page, r);
      const h = await page.evaluate(() => { const els = [document.querySelector('#screen-mount'), ...document.querySelectorAll('#screen-mount *')]; let m = 0; for (const e of els) { if (e && e.scrollHeight > e.clientHeight + 4 && /(auto|scroll)/.test(getComputedStyle(e).overflowY)) m = Math.max(m, e.scrollHeight); } return m; });
      const H = Math.min(Math.max(900, h + 120), 7000);
      await page.setViewportSize({ width: 1440, height: H }); await page.waitForTimeout(700);
      await shot(page, `${n}.d.tall`);
      log['tallH:' + n] = H;
    }
    await ctx.close();

    // Empty task state (prefs stripped of last_task_id)
    ({ ctx, page } = await mk(browser, 1440, 900, { stripLastTask: true }));
    await go(page, '/task'); await shot(page, 'task-empty-state.d');
    await ctx.close();

    // ---------- Mobile ----------
    ({ ctx, page } = await mk(browser, 390, 844));
    await go(page, '/kanban');
    const hb = page.locator('.hamburger, [class*=hamburger], button[aria-label*=menu i], button[aria-label*=navigation i]').first();
    log.hamburger = await hb.evaluate(el => ({ cls: el.className, label: el.getAttribute('aria-label'), expanded: el.getAttribute('aria-expanded') })).catch(() => null);
    await hb.click().catch(() => {}); await page.waitForTimeout(600);
    await shot(page, 'mobile-drawer-open.m');
    log.drawerFocus = await page.evaluate(() => document.activeElement.tagName + '.' + document.activeElement.className);
    await page.keyboard.press('Escape'); await page.waitForTimeout(400);
    log.drawerOpenAfterEsc = await page.evaluate(() => document.querySelector('.shell')?.className);
    await shot(page, 'mobile-drawer-after-esc.m');
    await go(page, '/kanban');
    await page.locator('.card-task .card-title').first().click(); await page.waitForTimeout(1500);
    await shot(page, 'detail-modal-task.m');
    await page.keyboard.press('Escape');
    await go(page, '/task/v3-polish-054');
    await page.locator('#topbar-actions button', { hasText: 'Edit' }).first().click().catch(() => {}); await page.waitForTimeout(1000);
    await shot(page, 'task-edit-modal.m');
    await page.keyboard.press('Escape');
    await go(page, '/kanban');
    await page.locator('#topbar-actions .tm-action--primary, button', { hasText: 'Task' }).first().click().catch(() => {}); await page.waitForTimeout(800);
    await shot(page, 'task-create-modal.m');
    await ctx.close();
  } catch (e) {
    console.error('FAILED', e);
  } finally {
    log.blocked = blocked;
    fs.writeFileSync(path.join(__dirname, 'interact.json'), JSON.stringify(log, null, 1));
    await browser.close();
  }
})();
