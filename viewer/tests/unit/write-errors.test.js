// User intent: a write the server refused is explained in words a person reads — the server's own reason when it gives
// one, a plain sentence when it does not — and never as a method, a URL, a status code or a JSON body.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { api } from '../../js/api.js';

const { describeWriteError, lostRace } = await import('../../js/components/edit/write-errors.js');

async function withFetch(respond, run) {
  const original = globalThis.fetch;
  globalThis.fetch = async (path, init) => respond(path, init);
  try { return await run(); } finally { globalThis.fetch = original; }
}
const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
// The error the shared client throws for a PATCH answered with `response`.
const refused = (response) => withFetch(() => response, () => api.patchTask('T-1', { status: 'done' }).catch((e) => e));

// Nothing a person should never see: the verb, the path, a status code, or a JSON body.
function assertPlain(text) {
  assert.equal(typeof text, 'string');
  assert.ok(text.length > 0);
  assert.doesNotMatch(text, /PATCH|\/api\/|→|\{|\}|\b[45]\d\d\b|"ok"/, text);
}

test('the server\'s reason is shown as it was given: a refusing 409 and a 400', async () => {
  const gate = 'T-1: blocking gate "review" is not cleared';
  assert.equal(describeWriteError(await refused(json(409, { ok: false, error: gate }))), gate);
  assert.equal(describeWriteError(await refused(json(400, { ok: false, error: 'title is required' }))), 'title is required');
});

test('a 422 names each field the server refused and why, in words', async () => {
  const e = await refused(json(422, { ok: false, errors: { status: 'Completion blocked: review-gate is still open', review_instructions: 'too long' } }));
  const text = describeWriteError(e);
  assert.equal(text, 'Status: Completion blocked: review-gate is still open · Review instructions: too long');
  assertPlain(text);
  assert.notEqual(text, 'validation failed');
  // A 422 that names nothing still says the change was refused.
  assertPlain(describeWriteError(await refused(json(422, { ok: false, errors: {} }))));
});

test('a 404 says the task no longer exists', async () => {
  const text = describeWriteError(await refused(json(404, { ok: false, error: 'task T-1 not found' })));
  assertPlain(text);
  assert.match(text, /no longer exists/);
});

test('a 5xx is a plain sentence, never the server\'s traceback', async () => {
  for (const status of [500, 502, 503]) {
    const text = describeWriteError(await refused(json(status, { ok: false, error: "KeyError: 'depends_on'" })));
    assertPlain(text);
    assert.doesNotMatch(text, /KeyError/);
    assert.match(text, /could not save/i);
  }
  const html = await refused(new Response('<html>Bad gateway</html>', { status: 502 }));
  assert.doesNotMatch(describeWriteError(html), /html/i);
});

test('a network failure says the server could not be reached', async () => {
  const e = await withFetch(() => { throw new TypeError('Failed to fetch'); }, () => api.patchTask('T-1', {}).catch((err) => err));
  const text = describeWriteError(e);
  assertPlain(text);
  assert.match(text, /could not reach the server/i);
  assert.doesNotMatch(text, /Failed to fetch/);
});

test('a 409 with no body, or only "stale", says the task changed rather than printing "stale"', async () => {
  for (const response of [new Response('', { status: 409 }), json(409, { ok: false, error: 'stale' }), json(409, {})]) {
    const text = describeWriteError(await refused(response));
    assertPlain(text);
    assert.notEqual(text, 'stale');
    assert.match(text, /changed/);
  }
});

test('a 4xx with a body that is not a reason is a plain sentence, not the body', async () => {
  const text = describeWriteError(await refused(new Response('{"weird": true}', { status: 400 })));
  assertPlain(text);
});

test('anything else (no error at all, a bare string) still gives a sentence', () => {
  for (const e of [undefined, null, 'boom', new Error('GET /api/x → JSON parse failed: Unexpected token')]) assertPlain(describeWriteError(e));
});

test('lostRace: only a 409 that names the revision it lost to', () => {
  assert.equal(lostRace({ code: 409, current_etag: 't1:fresh' }), true);
  assert.equal(lostRace({ code: 409 }), false);
  assert.equal(lostRace({ code: 412, current_etag: 'x' }), false);
  assert.equal(lostRace(null), false);
});

// The inline field is on every task document; the form and its modal load only when a form opens (M-6).
test('the inline field and the task document import write errors without the form chain', () => {
  const imports = (file) => [...readFileSync(new URL(`../../js/components/${file}`, import.meta.url), 'utf8')
    .matchAll(/^import\s[^;]*?from\s+'([^']+)'/gm)].map((m) => m[1]);
  for (const file of ['edit/inline-field.js', 'task-detail-document.js']) {
    const list = imports(file);
    assert.ok(list.some((p) => p.endsWith('write-errors.js')), `${file} uses write-errors.js`);
    for (const heavy of ['task-actions.js', 'entity-modal.js', 'modal.js']) {
      assert.ok(!list.some((p) => p.endsWith(`/${heavy}`)), `${file} does not statically import ${heavy}`);
    }
  }
  const own = imports('edit/write-errors.js');
  assert.deepEqual(own, [], 'write-errors.js imports nothing');
});
