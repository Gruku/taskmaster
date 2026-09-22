// Read-only follow-up: CDP extra-info observes 304 headers even when no normal
// responseReceived/loadingFinished pair is delivered to the page collector.
import {createRequire} from 'node:module';
import {readFile, writeFile, realpath} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const require = createRequire(import.meta.url);
const {chromium} = require('../viewer/node_modules/playwright');
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = Object.fromEntries(process.argv.slice(2).map(v => v.split(/=(.*)/s).slice(0, 2)));
const allowed = await realpath(path.join(repo, 'test-results'));
const output = path.resolve(args['--output']);
const identity = await (await fetch(`${args['--url']}/api/identity`)).json();
const root = await realpath(identity.root);
if (!root.startsWith(allowed + path.sep) || !output.startsWith(allowed + path.sep)) throw Error('not a test copy');
await readFile(path.join(root, '.benchmark-copy'), 'utf8');
const browser = await chromium.launch({headless: true});
try {
  const page = await browser.newPage();
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Network.enable');
  const records = new Map();
  const get = id => { if (!records.has(id)) records.set(id, {}); return records.get(id); };
  cdp.on('Network.requestWillBeSent', e => { get(e.requestId).path = new URL(e.request.url).pathname; });
  cdp.on('Network.responseReceived', e => { get(e.requestId).responseStatus = e.response.status; });
  cdp.on('Network.responseReceivedExtraInfo', e => {
    const record = get(e.requestId);
    record.status = e.statusCode;
    record.contentLength = e.headers['Content-Length'] ?? e.headers['content-length'] ?? null;
    record.headerBytes = e.headersText == null ? null : Buffer.byteLength(e.headersText);
  });
  cdp.on('Network.loadingFinished', e => { get(e.requestId).encodedDataLength = e.encodedDataLength; });
  cdp.on('Network.loadingFailed', e => { get(e.requestId).loadingFailure = e.errorText; });
  await page.goto(`${args['--url']}/api/identity`);
  const responses = await page.evaluate(async () => {
    const first = await fetch('/api/board', {cache: 'no-store'});
    const etag = first.headers.get('ETag');
    await first.text();
    if (first.status !== 200 || !etag) throw Error('missing initial board/ETag');
    const results = [];
    for (let i = 0; i < 10; i++) {
      const response = await fetch('/api/board', {cache: 'no-store', headers: {'If-None-Match': etag}});
      results.push({status: response.status, bodyBytes: (await response.arrayBuffer()).byteLength});
    }
    return results;
  });
  // Roundtrip ensures pending CDP events have reached this client.
  await cdp.send('Runtime.evaluate', {expression: '0'});
  const network = [...records.values()].filter(r => r.path === '/api/board');
  if (responses.some(r => r.status !== 304 || r.bodyBytes !== 0)) throw Error('unchanged board did not return empty 304');
  const conditional = network.filter(r => r.status === 304);
  if (conditional.length !== 10 || conditional.some(r => r.headerBytes == null)) throw Error('missing raw 304 headers');
  await writeFile(output, JSON.stringify({browser: browser.version(), responses, network,
    note: 'headerBytes is the byte length of CDP headersText, not an invented encodedDataLength when Chromium omits loadingFinished.'}, null, 2), {flag: 'wx'});
  console.log(JSON.stringify({samples: conditional.length, headerBytes: conditional.map(r => r.headerBytes),
    encodedDataLength: conditional.map(r => r.encodedDataLength ?? null)}));
} finally { await browser.close(); }
