const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await (await b.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  await p.route('**/api/**', r => r.request().method() === 'GET' ? r.continue() : r.fulfill({ status: 200, body: '{}' }));
  await p.goto('http://127.0.0.1:43790/v3#/settings'); await p.waitForTimeout(2500);
  console.log(await p.evaluate(() => {
    const out = [];
    for (const y of [845, 850, 870, 895]) { const el = document.elementFromPoint(100, y); out.push(y + ' ' + el.tagName + '.' + el.className + ' ' + JSON.stringify(el.getBoundingClientRect())); }
    const sb = document.querySelector('.sidebar'); const cs = getComputedStyle(sb);
    out.push('sidebar h ' + sb.getBoundingClientRect().height + ' padding ' + cs.padding);
    const sh = document.querySelector('.shell'); out.push('shell ' + JSON.stringify(sh.getBoundingClientRect()) + ' ' + getComputedStyle(sh).gridTemplateRows + ' / ' + getComputedStyle(sh).gridTemplateColumns);
    const left = [...document.querySelectorAll('#screen-mount > *')].map(e => e.className + '@' + Math.round(e.getBoundingClientRect().left));
    out.push('mount children ' + left.join(', '));
    return out.join('\n');
  }));
  // content left edge per screen
  for (const r of ['/dashboard','/kanban','/table','/task/v3-polish-054','/epics','/epic/v3-polish','/sessions','/issues','/issue/ISS-004','/bugs','/bug/B-086','/ideas','/archived','/settings']) {
    await p.goto('about:blank'); await p.goto('http://127.0.0.1:43790/v3#' + r); await p.waitForTimeout(1800);
    const v = await p.evaluate(() => {
      const m = document.getElementById('screen-mount');
      const els = [...m.querySelectorAll('*')].filter(e => { const r = e.getBoundingClientRect(); return r.width > 40 && r.height > 8 && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()); });
      const minL = Math.min(...els.map(e => e.getBoundingClientRect().left));
      const maxR = Math.max(...els.map(e => e.getBoundingClientRect().right));
      const t = document.getElementById('page-title').getBoundingClientRect();
      return { textLeft: Math.round(minL), textRight: Math.round(maxR), titleTop: Math.round(t.top), titleLeft: Math.round(t.left), mountPad: getComputedStyle(m).padding, topbarH: Math.round(document.getElementById('topbar').getBoundingClientRect().height) };
    });
    console.log(r.padEnd(22), JSON.stringify(v));
  }
  await b.close();
})();
