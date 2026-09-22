// Real Chromium measurements. Refuses to mutate anything but a marked copy
// under this worktree's ignored test-results directory.
import {createRequire} from 'node:module';
import {readFile, writeFile, realpath} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const require = createRequire(import.meta.url);
const {chromium} = require('../viewer/node_modules/playwright');
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = Object.fromEntries(process.argv.slice(2).map(v => v.split(/=(.*)/s).slice(0, 2)));
const url = args['--url'];
const output = path.resolve(args['--output']);
const baseline = args['--baseline'] === 'true';
const runs = Number(args['--runs'] || 5);
const samples = Number(args['--samples'] || 50);
const observationMs = Number(args['--observation-ms'] || 60000);
const allowed = await realpath(path.join(repo, 'test-results'));
const identity = await (await fetch(`${url}/api/identity`)).json();
const root = await realpath(identity.root);
if (!root.startsWith(allowed + path.sep) || !output.startsWith(allowed + path.sep)) throw Error('copy/output outside test-results');
await readFile(path.join(root, '.benchmark-copy'), 'utf8');
const browser = await chromium.launch({headless: true});
const result = {baseline, samples, runs: [], browser: browser.version(), note: 'Historical frontend uses current safe server runtime; heap polls accelerated, responsiveness uses normal 3s polls.'};
try {
  for (let run = 0; run < runs; run++) {
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
    const page = await context.newPage();
    // Normalize presentation only in this browser; never persist copy prefs.
    await page.route('**/api/viewer/prefs', async route => {
      if (route.request().method() === 'GET') await route.fulfill({json: {ui: {detail_view_mode: 'modal'}}});
      else await route.continue();
    });
    await page.addInitScript(() => {
      window.__TM_MEASURE__ = true;
      window.__longTasks = [];
      window.__events = [];
      new PerformanceObserver(list => window.__longTasks.push(...list.getEntries().map(e => ({start: e.startTime, duration: e.duration})))).observe({type: 'longtask', buffered: true});
      new PerformanceObserver(list => window.__events.push(...list.getEntries().map(e => ({name: e.name, duration: e.duration, interactionId: e.interactionId})))).observe({type: 'event', durationThreshold: 16, buffered: true});
    });
    const cdp = await context.newCDPSession(page);
    await cdp.send('Network.enable');
    const requests = new Map();
    const network = [];
    cdp.on('Network.responseReceived', ({requestId, response}) => {
      if (/\/api\/(board|backlog|task\/[^/]+(?:\/detail)?)($|\?)/.test(response.url)) requests.set(requestId, {url: new URL(response.url).pathname, status: response.status, contentLength: response.headers['Content-Length']});
    });
    cdp.on('Network.loadingFinished', ({requestId, encodedDataLength}) => {
      if (requests.has(requestId)) { network.push({...requests.get(requestId), encodedDataLength}); requests.delete(requestId); }
    });
    const heap = async () => { await cdp.send('HeapProfiler.collectGarbage'); return await cdp.send('Runtime.getHeapUsage'); };
    await page.goto(`${url}/#/kanban`);
    await page.locator('.card-task').first().waitFor();
    const id = await page.locator('.card-task').first().getAttribute('data-task-id');
    const loaded = await heap();
    const refresh = async () => page.evaluate(async baseline => {
      const {store} = await import('/static/v3/js/store.js');
      if (!baseline) { await store.refreshBoard(); return; }
      // Same historical API and store, with measurement around their real work.
      const {api} = await import('/static/v3/js/api.js');
      const started = performance.now();
      const payload = await api.backlog();
      performance.measure('tm:baseline-fetch-and-parse', {start: started, end: performance.now()});
      const apply = performance.now();
      store.setBacklog(payload);
      performance.measure('tm:baseline-apply-and-paint', {start: apply, end: performance.now()});
    }, baseline);
    for (let i = 0; i < samples; i++) await refresh();
    const idle50 = await heap();
    const task = await (await page.request.get(`${url}/api/task/${encodeURIComponent(id)}`)).json();
    for (let i = 0; i < samples; i++) {
      const response = await page.request.patch(`${url}/api/tasks/${encodeURIComponent(id)}`, {data: {title: `${task.title.slice(0, 90)} [browser ${run}-${i}]`}});
      if (!response.ok()) throw Error(`copy edit failed ${response.status()}: ${await response.text()}`);
      await refresh();
    }
    const delta50 = await heap();
    await page.evaluate(() => { window.__longTasks = []; window.__events = []; });
    const observedStart = await page.evaluate(() => performance.now());
    // Product cadence, not an HTTP latency proxy. This wait runs detached.
    await page.waitForTimeout(observationMs);
    const longTasks = await page.evaluate(() => window.__longTasks);
    const click = await page.evaluate(() => performance.now());
    await page.locator(`.card-task[data-task-id="${id}"] .card-title`).click();
    let modalError = null;
    try {
      await page.locator('.dm-modal .td-title, .dm-modal .dm-error').first().waitFor();
      if (await page.locator('.dm-modal .dm-error').count()) {
        modalError = await page.locator('.dm-modal .dm-error').innerText();
        if (!baseline) throw Error(modalError);
      }
    }
    catch (error) {
      await page.screenshot({path: output + '.png'});
      const diagnostic = {url: page.url(), modal: await page.locator('.dm-modal').allTextContents()};
      await writeFile(output + '.error.json', JSON.stringify(diagnostic, null, 2));
      throw error;
    }
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const painted = await page.evaluate(() => performance.now());
    const measures = await page.evaluate(() => performance.getEntriesByType('measure').map(e => ({name: e.name, duration: e.duration})));
    const events = await page.evaluate(() => window.__events);
    result.runs.push({loaded, afterIdlePolls: idle50, afterDeltas: delta50, network, longTasks, observedStart, idleObservationMs: observationMs, clickToModalFramesMs: modalError ? null : painted - click, modalError, events, measures});
    const restored = await page.request.patch(`${url}/api/tasks/${encodeURIComponent(id)}`, {data: {title: task.title}});
    if (!restored.ok()) throw Error('copy title restore failed');
    await writeFile(output, JSON.stringify(result, null, 2));
    console.log(JSON.stringify({run, baseline, heap: [loaded.usedSize, idle50.usedSize, delta50.usedSize], idleLongTasks: longTasks.length, clickToModalFramesMs: modalError ? null : painted - click, modalError}));
    await context.close();
  }
} finally { await browser.close(); }
