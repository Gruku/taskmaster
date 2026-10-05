// User intent: the Ideas create form is the same labelled form as the task one, offering an idea's own statuses in its
// own words, and suggesting tags the user already uses rather than making them retype them.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;

const { ideaSchema } = await import('../../js/components/edit/forms/idea-form.js');
const { runValidation } = await import('../../js/components/edit/schema.js');
const { TextField } = await import('../../js/components/edit/fields/text-field.js');
const { EnumSelect } = await import('../../js/components/edit/fields/enum-select.js');
const { ChipInput } = await import('../../js/components/edit/fields/chip-input.js');
const { MdField } = await import('../../js/components/edit/fields/md-field.js');

const IDEAS = [
  { id: 'IDEA-1', tags: ['UX', 'board'] },
  { id: 'IDEA-2', tags: ['ux', 'perf', 'tags-a', 'tags-b'] },
  { id: 'IDEA-3', tags: ['tags-c', 'tags-d', 'tags-e', 'tags-f', 'tags-g', 'tags-h', 'tags-i'] },
  { id: 'IDEA-4' },
  { id: 'IDEA-5', tags: null },
];
const schema = (ideas = IDEAS) => ideaSchema({ getIdeas: () => ideas });

test('the fields, their renderers and groups: title, status and tags in Basics, the body in Content', () => {
  const s = schema();
  assert.equal(s.entity, 'idea');
  assert.equal(s.label, 'Idea');
  assert.deepEqual(s.fields.map((f) => [f.key, f.label, f.renderer, f.group]), [
    ['title', 'Title', TextField, 'basics'],
    ['status', 'Status', EnumSelect, 'basics'],
    ['tags', 'Tags', ChipInput, 'basics'],
    ['body', 'Body', MdField, 'content'],
  ]);
  const title = s.fields.find((f) => f.key === 'title');
  assert.equal(title.wide, true);
  assert.equal(title.required, true);
  assert.equal(title.maxLength, 140);
  assert.equal(s.fields.find((f) => f.key === 'status').required, true);
  const tags = s.fields.find((f) => f.key === 'tags');
  assert.equal(tags.allowFree, true);
  assert.equal(tags.placeholder, 'add a tag…');
  assert.equal(s.fields.find((f) => f.key === 'body').open, true, 'the body is where an idea is written: it starts open');
  assert.deepEqual(s.systemManaged, ['id', 'created', 'updated', 'archived', 'promoted_to']);
});

test('Basics does not end on a lone field: Title spans the row, Status and Tags pair up', () => {
  const cells = schema().fields.filter((f) => f.group === 'basics').reduce((n, f) => n + (f.wide ? 2 : 1), 0);
  assert.equal(cells % 2, 0);
});

test('status offers every idea status in the marker table\'s order and words', () => {
  const status = schema().fields.find((f) => f.key === 'status');
  assert.deepEqual(status.options, [
    { value: 'exploring', label: 'Exploring' },
    { value: 'candidate', label: 'Candidate' },
    { value: 'parking-lot', label: 'Parking lot' },
    { value: 'promoted', label: 'Promoted' },
    { value: 'dropped', label: 'Dropped' },
  ]);
});

test('a new idea with a title and the defaults is valid; without a title, or with an unknown status, it is not', () => {
  const s = schema();
  assert.equal(runValidation({ title: 'Faster board', status: 'exploring', tags: [] }, s).valid, true);
  assert.equal(runValidation({ title: '', status: 'exploring', tags: [] }, s).errors.title, 'required');
  assert.equal(runValidation({ title: 'x', status: 'someday', tags: [] }, s).errors.status, 'invalid value');
  assert.ok(runValidation({ title: 'x'.repeat(141), status: 'exploring' }, s).errors.title);
});

test('the tag suggestions are the distinct tags already used that contain the text, case-insensitive, at most 8', async () => {
  const source = schema().fields.find((f) => f.key === 'tags').source;
  // Matched without regard to case; a tag stored in two spellings is two tags, each offered once.
  assert.deepEqual(await source('ux'), ['UX', 'ux']);
  assert.deepEqual(await source('OAR'), ['board']);
  assert.deepEqual(await source('perf'), ['perf']);
  const many = await source('tags-');
  assert.equal(many.length, 8);
  assert.equal(new Set(many).size, 8);
  assert.ok(many.every((t) => t.startsWith('tags-')));
  assert.deepEqual(await source('nothing like it'), []);
});

test('the tag suggestions survive ideas with no tags, odd tags, and a list that is not there yet', async () => {
  const odd = [{ tags: ['ok', 7, null, ''] }, { tags: 'not-a-list' }, null];
  assert.deepEqual(await schema(odd).fields.find((f) => f.key === 'tags').source('o'), ['ok']);
  assert.deepEqual(await schema(null).fields.find((f) => f.key === 'tags').source('o'), []);
  assert.deepEqual(await schema({ ideas: 'x' }).fields.find((f) => f.key === 'tags').source('o'), []);
});

test('the Ideas screen builds no dialog of its own: no .em- class, no #entity-modal-host, no window.confirm', async () => {
  const { readFileSync } = await import('node:fs');
  const js = readFileSync(new URL('../../js/screens/ideas.js', import.meta.url), 'utf8');
  const css = readFileSync(new URL('../../css/screens/ideas.css', import.meta.url), 'utf8');
  for (const [name, src] of [['ideas.js', js], ['ideas.css', css]]) {
    assert.doesNotMatch(src, /(?<![\w-])em-/, `${name} still carries an .em- class`);
    assert.doesNotMatch(src, /window\.confirm|entity-modal-host/, name);
  }
});
