// User intent: the shared API client is the one door every screen uses — bugs are listed through it like everything else,
// and a 409 tells a lost race (which names the revision it lost to) from the server refusing a write with a reason.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { api } from '../../js/api.js';

async function withFetch(respond, run) {
  const original = globalThis.fetch;
  const seen = [];
  globalThis.fetch = async (path, init) => { seen.push({ path, init }); return respond(path, init); };
  try { return await run(seen); } finally { globalThis.fetch = original; }
}
const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

test('api.listBugs is on the shared client and passes its filters as the query', async () => {
  assert.equal(typeof api.listBugs, 'function');
  await withFetch(() => json(200, { bugs: [{ id: 'B-1' }] }), async (seen) => {
    assert.deepEqual(await api.listBugs({ found_in: 'T-102' }), { bugs: [{ id: 'B-1' }] });
    assert.equal(seen[0].path, '/api/bugs?found_in=T-102');
    await api.listBugs();
    assert.equal(seen[1].path, '/api/bugs');
  });
});

test('a 409 that names a revision is a lost race; any other 409 carries the server\'s reason and no revision', async () => {
  await withFetch(
    () => json(409, { ok: false, error: 'stale', current: { id: 'T-1' }, current_etag: 't1:fresh' }),
    async () => {
      const e = await api.patchTask('T-1', { title: 'x' }).catch((err) => err);
      assert.equal(e.code, 409);
      assert.equal(e.current_etag, 't1:fresh');
      assert.deepEqual(e.current, { id: 'T-1' });
    });
  await withFetch(
    () => json(409, { ok: false, error: 'T-1: blocking gate "review" is not cleared' }),
    async () => {
      const e = await api.patchTask('T-1', { status: 'done' }).catch((err) => err);
      assert.equal(e.code, 409);
      assert.equal(e.message, 'T-1: blocking gate "review" is not cleared');
      assert.equal(e.current_etag, undefined);
    });
});

test('patchTask sends the If-Match it is given', async () => {
  await withFetch(() => json(200, { ok: true }), async (seen) => {
    await api.patchTask('T-1', { title: 'x' }, { ifMatch: 't1:fresh' });
    assert.equal(seen[0].init.headers['If-Match'], 't1:fresh');
  });
});

test('a 409 whose error is not a non-empty string carries "stale", never an object turned into text', async () => {
  for (const error of [{ nested: 1 }, '', '   ', 7, null, undefined]) {
    await withFetch(() => json(409, { ok: false, error }), async () => {
      const e = await api.patchTask('T-1', { title: 'x' }).catch((err) => err);
      assert.equal(e.code, 409);
      assert.equal(e.message, 'stale', JSON.stringify(error));
    });
  }
  // A body of JSON null is not an object to read a reason from.
  await withFetch(() => json(409, null), async () => {
    const e = await api.patchTask('T-1', { title: 'x' }).catch((err) => err);
    assert.equal(e.code, 409);
    assert.equal(e.message, 'stale');
  });
});
