// User intent: a doc value always becomes a safe, same-meaning link: http(s) as itself, a path through /file/ with each
// segment encoded, so `#`, `?` and dot segments can't change which URL is fetched (security review m-10).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { docLink } from '../../js/lib/doc-link.js';

test('a project path goes through /file/, each segment encoded', () => {
  assert.equal(docLink('docs/specs/a.md').href, '/file/docs/specs/a.md');
  assert.equal(docLink('/docs/a b#c?.md').href, '/file/docs/a%20b%23c%3F.md');
  assert.equal(docLink('docs/../../api/viewer/prefs').href, '/file/docs/api/viewer/prefs');
  assert.equal(docLink('javascript:alert(1)').href, '/file/javascript%3Aalert(1)');
  assert.equal(docLink('docs/a.md').rel, 'noopener');
});

test('an http(s) URL opens as itself, without a referrer', () => {
  const link = docLink('https://example.com/r/1');
  assert.deepEqual(link, { href: 'https://example.com/r/1', target: '_blank', rel: 'noopener noreferrer' });
});
