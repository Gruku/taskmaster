// User intent: pin the Kanban phase strip's structure, pressed state, in-place updates and archived menu, so the
// one-line phase filter cannot drift back to the carousel stepper's transforms, glyphs or lost focus.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.Event = dom.window.Event;
globalThis.Node = dom.window.Node;
globalThis.HTMLElement = dom.window.HTMLElement;

const { phaseStrip } = await import('../../js/components/phase-strip.js');
const { openPopoverCount } = await import('../../js/components/popover.js');
const { longBoard } = await import('../mock-fixtures.js');

// The phase rows as kanban.js builds them.
function rows(board = longBoard()) {
  return board.phases.slice().sort((a, b) => (a.order ?? 999) - (b.order ?? 999)).map((ph) => {
    const total = board.tasks.filter((t) => t.phase === ph.id).length;
    const done = board.tasks.filter((t) => t.phase === ph.id && t.status === 'done').length;
    return { id: ph.id, name: ph.name || ph.id, status: ph.status, done, total, archived_reason: ph.archived_reason };
  });
}

function mount(active = '__all__', phases = rows()) {
  const picks = [];
  const s = phaseStrip({ onSelect: (v) => picks.push(v) });
  document.body.replaceChildren(s.el);
  s.update({ phases, active });
  return { s, picks, el: s.el };
}

test('phase strip: a labelled group with the Phase label and the items row', () => {
  const { el, s } = mount();
  assert.ok(el.matches('div.phase-strip[role="group"][aria-labelledby]'));
  const label = el.querySelector(':scope > span.phase-strip__label');
  assert.equal(label.textContent, 'Phase');
  assert.equal(label.id, el.getAttribute('aria-labelledby'));
  assert.ok(el.querySelector(':scope > div.phase-strip__items'));
  s.destroy();
});

test('phase strip: All, Archived, every phase in bucket order, then No phase', () => {
  const { el, s } = mount();
  const items = [...el.querySelectorAll('.phase-strip__items > button:not(.overflow-more)')];
  assert.ok(items[0].matches('button.chip.phase-chip.phase-chip--all[data-value="__all__"]'));
  assert.equal(items[0].textContent, 'All');
  assert.ok(items[1].matches('button.btn.btn--ghost.btn--sm.phase-archived'));
  assert.equal(items[1].querySelector('.phase-archived__count').textContent, '1');
  const last = items.at(-1);
  assert.ok(last.matches('button.chip.phase-chip.phase-chip--orphans[data-value="__orphans__"]'));
  assert.equal(last.textContent, 'No phase');
  const phaseBtns = items.slice(2, -1);
  const expected = rows().filter((p) => p.status !== 'archived');
  assert.deepEqual(phaseBtns.map((b) => b.dataset.value), expected.map((p) => p.id));
  for (const [i, b] of phaseBtns.entries()) {
    const p = expected[i];
    assert.ok(b.matches('button.chip.phase-chip'));
    assert.equal(b.querySelector('.phase-chip__num').textContent, p.id.slice(1));
    assert.equal(b.querySelector('.phase-chip__name').textContent, p.name);
    assert.equal(b.querySelector('.phase-chip__count').textContent, `${p.done}/${p.total}`);
    assert.equal(b.title, `${p.name} · ${p.done}/${p.total} done`);
    const kind = p.status === 'done' ? 'done' : p.status === 'active' ? 'current' : 'future';
    assert.ok(b.classList.contains(`phase-chip--${kind}`), `${p.id} is ${kind}`);
    const check = b.querySelector('svg');
    if (kind === 'done') assert.ok(check && check.nextElementSibling.matches('.phase-chip__name'), 'check before the name');
    else assert.equal(check, null);
    const bar = b.querySelector('.phase-chip__bar > span');
    if (kind === 'current') assert.equal(bar.style.width, `${Math.round((p.done / p.total) * 100)}%`);
    else assert.equal(bar, null);
  }
  s.destroy();
});

test('phase strip: aria-pressed follows active, and a click reports the value', () => {
  const { el, s, picks } = mount();
  const pressed = () => [...el.querySelectorAll('[aria-pressed="true"]')].map((b) => b.dataset.value);
  assert.deepEqual(pressed(), ['__all__']);
  for (const b of el.querySelectorAll('.phase-chip')) assert.ok(['true', 'false'].includes(b.getAttribute('aria-pressed')));
  el.querySelector('[data-value="P5"]').click();
  el.querySelector('[data-value="__orphans__"]').click();
  assert.deepEqual(picks, ['P5', '__orphans__']);
  s.update({ phases: rows(), active: '__orphans__' });
  assert.deepEqual(pressed(), ['__orphans__']);
  s.destroy();
});

test('phase strip: update reuses each button by value, keeping focus, and applies removals and reorders', () => {
  const { el, s } = mount();
  const p2 = el.querySelector('[data-value="P2"]');
  p2.focus();
  s.update({ phases: rows(), active: 'P2' });
  assert.equal(el.querySelector('[data-value="P2"]'), p2);
  assert.equal(p2.getAttribute('aria-pressed'), 'true');
  assert.equal(el.querySelector('[data-value="__all__"]').getAttribute('aria-pressed'), 'false');
  assert.equal(document.activeElement, p2);
  const fewer = rows().filter((p) => p.id !== 'P3').reverse();
  s.update({ phases: fewer, active: 'P2' });
  assert.equal(el.querySelector('[data-value="P3"]'), null);
  assert.equal(el.querySelector('[data-value="P2"]'), p2);
  assert.equal(document.activeElement, p2);
  s.destroy();
});

test('phase strip: the archived menu checks the active archived phase and picking it closes and reports', () => {
  const { el, s, picks } = mount('P0');
  const btn = el.querySelector('.phase-archived');
  assert.equal(btn.getAttribute('aria-pressed'), 'true', 'reads pressed while an archived phase is active');
  btn.click();
  const menu = document.querySelector('.popover[role="menu"][aria-label="Archived phases"]');
  assert.ok(menu);
  const items = menu.querySelectorAll('button.popover-item[role="menuitemradio"]');
  assert.equal(items.length, 1);
  assert.equal(items[0].getAttribute('aria-checked'), 'true');
  assert.match(items[0].textContent, /Phase 0: The prototype.*0\/0.*superseded/);
  items[0].click();
  assert.deepEqual(picks, ['P0']);
  assert.equal(openPopoverCount(), 0);
  s.destroy();
});

test('phase strip: no archived phase means no Archived button', () => {
  const { el, s } = mount('__all__', rows().filter((p) => p.status !== 'archived'));
  assert.equal(el.querySelector('.phase-archived'), null);
  s.destroy();
});

test('phase strip: no .disabled, no inline transform, none of the stepper glyphs', () => {
  const { el, s } = mount('P4');
  assert.equal(el.querySelector('.disabled'), null);
  for (const n of el.querySelectorAll('[style]')) assert.doesNotMatch(n.getAttribute('style'), /transform/);
  assert.doesNotMatch(el.textContent, /[‹›«»◀▶◂▸←→✓✔●○…]/);
  s.destroy();
});
