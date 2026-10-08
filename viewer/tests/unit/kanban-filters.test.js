// User intent: pin the Kanban filter specs — one open-task count shared by chips and Epic options, full priority words,
// pinned-first epic order with archived epics hidden by default, and an Epic options popover with real links and pins.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.Event = dom.window.Event;
globalThis.Node = dom.window.Node;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);

const { countOpen, OPEN_COUNT_HINT } = await import('../../js/lib/filters.js');
const { priorityChips } = await import('../../js/components/priority-chips.js');
const { epicChips } = await import('../../js/components/epic-chips.js');
const { openEpicOptions } = await import('../../js/components/epic-dropdown.js');

test('countOpen counts open and status-less tasks, skips done and archived and missing fields', () => {
  const tasks = ['todo', 'in-progress', 'in-review', 'blocked', undefined, 'done', 'archived']
    .map((status) => ({ status, epic: 'e1' }));
  tasks.push({ status: 'todo' });
  assert.equal(countOpen(tasks, 'epic').get('e1'), 5);
  assert.equal(countOpen(tasks, 'epic').size, 1);
});

test('OPEN_COUNT_HINT says what it counts and how to multi-select', () => {
  assert.match(OPEN_COUNT_HINT, /open tasks/);
  assert.match(OPEN_COUNT_HINT, /shift-click/);
});

test('priorityChips are full words with open counts; only active ones pressed', () => {
  const chips = priorityChips(['high'], new Map([['high', 3]]));
  assert.deepEqual(chips.map((c) => c.label), ['Critical', 'High', 'Medium', 'Low']);
  assert.deepEqual(chips.filter((c) => c.pressed).map((c) => c.value), ['high']);
  assert.equal(chips[1].count, 3);
  assert.equal(chips[0].count, 0);
});

const EPICS = [
  { id: 'a', name: 'Zeta', status: 'active', swatch: 1 },
  { id: 'b', name: 'Alpha', status: 'active', swatch: 2 },
  { id: 'c', name: 'Mid', status: 'active', swatch: 3 },
  { id: 'x', name: 'Old', status: 'archived', swatch: 4 },
];

test('epicChips: All first, pinned next, archived hidden unless shown or selected, labels are names', () => {
  const ids = (o) => epicChips({ epics: EPICS, counts: new Map([['a', 2]]), ...o }).map((c) => c.value);
  assert.deepEqual(ids({ pinnedIds: ['c'] }).slice(0, 2), ['__all__', 'c']);
  assert.ok(!ids({}).includes('x'));
  assert.ok(ids({ showArchived: true }).includes('x'));
  assert.ok(ids({ selectedIds: ['x'] }).includes('x'));
  const chips = epicChips({ epics: EPICS });
  assert.equal(chips[0].pressed, true);
  assert.deepEqual(chips.slice(1).map((c) => c.label).sort(), ['Alpha', 'Mid', 'Zeta']);
  assert.deepEqual(epicChips({ epics: EPICS, sort: 'alpha' }).slice(1).map((c) => c.label), ['Alpha', 'Mid', 'Zeta']);
});

test('openEpicOptions: named Order select, one link per epic, Pin presses in place, filter narrows rows', () => {
  const anchor = document.body.appendChild(document.createElement('button'));
  const epics = Array.from({ length: 9 }, (_, i) => ({ id: `epic-0${i + 1}`, name: `Epic 0${i + 1}`, status: 'active', swatch: 1 }));
  const pins = [];
  const handle = openEpicOptions({ anchor, epics, counts: new Map(), pinnedIds: [], onPinToggle: (id, on) => pins.push([id, on]) });
  const pop = document.querySelector('.epic-options');
  const select = pop.querySelector('select');
  assert.equal(pop.querySelector(`label[for="${select.id}"]`).textContent, 'Order');
  const links = [...pop.querySelectorAll('a.epic-option__name')];
  assert.equal(links.length, 9);
  assert.equal(links.find((a) => a.title === 'Epic 03').getAttribute('href'), '#/epic/epic-03');
  const pin = pop.querySelector('[data-epic="epic-02"] .epic-option__pin');
  pin.click();
  assert.deepEqual(pins, [['epic-02', true]]);
  assert.equal(pin.getAttribute('aria-pressed'), 'true');
  const filter = pop.querySelector('input[type="search"]');
  filter.value = '07';
  filter.dispatchEvent(new window.Event('input'));
  assert.deepEqual([...pop.querySelectorAll('li.epic-option')].map((li) => li.dataset.epic), ['epic-07']);
  handle.close?.();
});
