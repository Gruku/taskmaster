// User intent: an issue card's evidence preview drops markdown's marks but never eats a word's own characters — file
// names, identifiers and dunder names in evidence stay exactly as written.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { plainMarkdown } from '../../js/util/issue-fields.js';

test('underscores inside a word are not emphasis', () => {
  assert.equal(plainMarkdown('see test_suite_speed.py'), 'see test_suite_speed.py');
  assert.equal(plainMarkdown('the slow part is _rebuild_related under the mutex'), 'the slow part is _rebuild_related under the mutex');
  assert.equal(plainMarkdown('imports in __init__ run twice'), 'imports in __init__ run twice');
});

test('code spans keep their text untouched', () => {
  assert.equal(plainMarkdown('call `_rebuild_related()` and `**kwargs`'), 'call _rebuild_related() and **kwargs');
  assert.equal(plainMarkdown('`__init__.py` loads'), '__init__.py loads');
});

test('real emphasis and strong lose their marks', () => {
  assert.equal(plainMarkdown('Seen on **three** laptops'), 'Seen on three laptops');
  assert.equal(plainMarkdown('it is _really_ slow'), 'it is really slow');
  assert.equal(plainMarkdown('__very bold__ and *em* and ~~gone~~'), 'very bold and em and gone');
  assert.equal(plainMarkdown('a_b and snake_case_name stay'), 'a_b and snake_case_name stay');
  assert.equal(plainMarkdown('a [link](https://x.test) and # not a heading'), 'a link and # not a heading');
});
