// User intent: everything that floats over the viewer opens through one popover, so its contract — dismissed by Escape,
// an outside press, focus loss or a redraw of its anchor, never eating the press, keyed like a menu — is pinned here.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const PAGE = '<div id="wrap"><button id="anchor">Open</button></div><button id="outside">Elsewhere</button><input id="field">';
const dom = new JSDOM(`<!doctype html><html><body>${PAGE}</body></html>`);
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const { openPopover, openPopoverCount, placePopover } = await import('../../js/components/popover.js');

const $ = (sel) => document.querySelector(sel);
const tick = () => Promise.resolve();
const fire = (el, type, init = {}) => {
  const e = new dom.window.Event(type, { bubbles: true, cancelable: true });
  Object.assign(e, init);
  el.dispatchEvent(e);
  return e;
};
const key = (el, k) => {
  const e = new dom.window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true });
  el.dispatchEvent(e);
  return e;
};
const button = (text, attrs = {}) => {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = text;
  for (const [k, v] of Object.entries(attrs)) b.setAttribute(k, v);
  return b;
};
const menuItems = (n, attrs = () => ({})) => Array.from({ length: n }, (_, i) => button(`item ${i}`, { role: 'menuitem', ...attrs(i) }));

const opened = [];
function open(opts = {}) {
  const reasons = [];
  const p = openPopover({ anchor: $('#anchor'), content: menuItems(3), role: 'menu', label: 'Things', onClose: (r) => reasons.push(r), ...opts });
  opened.push(p);
  return Object.assign(p, { reasons });
}

test.beforeEach(() => {
  assert.equal(openPopoverCount(), 0, 'the previous test left a popover open');
  document.body.innerHTML = PAGE;
});
test.afterEach(() => { for (const p of opened.splice(0)) p.close(); });

test('1. inserted right after its anchor, fixed, with an id the anchor controls while open', () => {
  const p = open();
  assert.equal($('#anchor').nextElementSibling, p.el);
  assert.equal(p.el.style.position, 'fixed');
  assert.match(p.el.id, /^popover-\d+$/);
  assert.equal(p.el.className, 'popover');
  assert.equal(p.el.getAttribute('role'), 'menu');
  assert.equal(p.el.getAttribute('aria-label'), 'Things');
  assert.equal($('#anchor').getAttribute('aria-expanded'), 'true');
  assert.equal($('#anchor').getAttribute('aria-controls'), p.el.id);
  assert.equal(p.isOpen(), true);
  assert.equal(openPopoverCount(), 1);
  p.close();
  assert.equal(p.isOpen(), false);
  assert.equal(p.el.isConnected, false);
  assert.equal($('#anchor').hasAttribute('aria-controls'), false);
  assert.deepEqual(p.reasons, ['api']);
});

test('1. extra classes, labelledBy, and two popovers get distinct ids', () => {
  const a = open({ className: 'ho-status-menu', label: undefined, labelledBy: 'some-heading' });
  assert.equal(a.el.className, 'popover ho-status-menu');
  assert.equal(a.el.getAttribute('aria-labelledby'), 'some-heading');
  assert.equal(a.el.hasAttribute('aria-label'), false);
  const id = a.el.id;
  a.close();
  const b = open();
  assert.notEqual(b.el.id, id);
});

test('1. placement: 4px below the anchor, flipped above when it does not fit, clamped 4px inside, containing block subtracted', () => {
  const anchor = $('#anchor');
  const el = document.createElement('div');
  anchor.after(el);
  const rect = (r) => () => ({ ...r, right: r.left + r.width, bottom: r.top + r.height, x: r.left, y: r.top });
  let origin = { left: 0, top: 0 };
  el.getBoundingClientRect = () => rect({ left: origin.left, top: origin.top, width: 200, height: 100 })();
  Object.defineProperty(el, 'offsetWidth', { get: () => 200 });
  Object.defineProperty(el, 'offsetHeight', { get: () => 100 });
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 600 });

  anchor.getBoundingClientRect = rect({ left: 20, top: 50, width: 80, height: 30 });
  placePopover(el, anchor);
  assert.deepEqual([el.style.position, el.style.left, el.style.top], ['fixed', '20px', '84px']);

  anchor.getBoundingClientRect = rect({ left: 300, top: 540, width: 80, height: 30 });
  placePopover(el, anchor);
  assert.equal(el.style.left, `${390 - 200 - 4}px`, 'clamped 4px inside the right edge');
  assert.equal(el.style.top, `${540 - 4 - 100}px`, 'flipped above');

  origin = { left: 10, top: 30 };   // a transformed ancestor moves the fixed origin
  anchor.getBoundingClientRect = rect({ left: 20, top: 50, width: 80, height: 30 });
  placePopover(el, anchor);
  assert.deepEqual([el.style.left, el.style.top], ['10px', '54px']);

  anchor.getBoundingClientRect = rect({ left: 20, top: 50, width: 260, height: 30 });
  placePopover(el, anchor, { minWidth: 'anchor' });
  assert.equal(el.style.minWidth, '260px');
});

test('2. Escape inside closes with "escape", is used up before a modal around it hears it, and focuses the anchor', () => {
  const heard = [];
  $('#wrap').addEventListener('keydown', (e) => heard.push(e.key));
  const p = open();
  assert.equal(document.activeElement, p.el.querySelector('[role="menuitem"]'));
  const e = key(document.activeElement, 'Escape');
  assert.deepEqual(p.reasons, ['escape']);
  assert.equal(e.defaultPrevented, true);
  assert.deepEqual(heard, [], 'the key never bubbled past the popover');
  assert.equal(document.activeElement, $('#anchor'));
  assert.equal(p.el.isConnected, false);
});

test('2. Escape on the anchor while open closes; one already used by someone else does not', () => {
  const heard = [];
  $('#wrap').addEventListener('keydown', (e) => heard.push(e.key));
  const p = open({ focus: 'none' });
  const anchor = $('#anchor');
  // The anchor's own handler (a combobox clearing its text) runs first and uses the key.
  anchor.addEventListener('keydown', (e) => e.preventDefault(), { capture: true, once: true });
  key(anchor, 'Escape');
  assert.equal(p.isOpen(), true, 'an Escape already used is not taken again');

  const e = key(anchor, 'Escape');
  assert.deepEqual(p.reasons, ['escape']);
  assert.equal(e.defaultPrevented, true);
  assert.deepEqual(heard, ['Escape'], 'only the used one bubbled');
  assert.equal(document.activeElement, anchor);
});

test('3. a press outside closes with "outside" and still reaches its target', () => {
  const p = open();
  const outside = $('#outside');
  const seen = [];
  document.body.addEventListener('pointerdown', (e) => seen.push(e.target.id));
  let clicked = 0;
  outside.addEventListener('click', () => { clicked += 1; });
  const e = fire(outside, 'pointerdown');
  outside.click();
  assert.deepEqual(p.reasons, ['outside']);
  assert.equal(e.defaultPrevented, false);
  assert.deepEqual(seen, ['outside'], 'propagation was not stopped');
  assert.equal(clicked, 1);
});

test('3. a press inside, or on the anchor, leaves it open', () => {
  const p = open();
  fire(p.el.querySelector('[role="menuitem"]'), 'pointerdown');
  fire($('#anchor'), 'pointerdown');
  assert.equal(p.isOpen(), true);
  assert.deepEqual(p.reasons, []);
});

test('4. focus leaving for an element outside closes with "focusout" and moves no focus', () => {
  const p = open();
  const field = $('#field');
  field.focus();
  assert.deepEqual(p.reasons, ['focusout']);
  assert.equal(document.activeElement, field);
});

test('4. focus moving to the anchor, inside the popover, or nowhere keeps it open', () => {
  const p = open();
  const items = [...p.el.querySelectorAll('[role="menuitem"]')];
  items[1].focus();
  $('#anchor').focus();
  items[0].focus();
  fire(items[0], 'focusout', { relatedTarget: null });
  assert.equal(p.isOpen(), true);
  // From the anchor, too: focus leaving it for elsewhere closes.
  $('#anchor').focus();
  $('#outside').focus();
  assert.deepEqual(p.reasons, ['focusout']);
});

test('5. a scroll outside closes with "scroll"; a scroll inside the popover does not', () => {
  const p = open();
  document.dispatchEvent(new dom.window.Event('scroll'));
  assert.deepEqual(p.reasons, ['scroll']);
  const q = open();
  q.el.dispatchEvent(new dom.window.Event('scroll'));
  assert.equal(q.isOpen(), true);
  $('#wrap').dispatchEvent(new dom.window.Event('scroll'));
  assert.deepEqual(q.reasons, ['scroll']);
});

test('5. a scroll that was under way before it opened does not close it; one from the next frame on does', async () => {
  const visual = new JSDOM(`<!doctype html><html><body>${PAGE}</body></html>`, { pretendToBeVisual: true });
  const vdoc = visual.window.document;
  const reasons = [];
  const p = openPopover({ anchor: vdoc.getElementById('anchor'), content: menuItems(2), role: 'menu', label: 'V', onClose: (r) => reasons.push(r) });
  opened.push(p);
  vdoc.dispatchEvent(new visual.window.Event('scroll'));
  assert.equal(p.isOpen(), true, 'dispatched before the next frame');
  await new Promise((ok) => visual.window.requestAnimationFrame(() => setTimeout(ok, 0)));
  vdoc.dispatchEvent(new visual.window.Event('scroll'));
  assert.deepEqual(reasons, ['scroll']);
  visual.window.close();
});

test('5. a window resize repositions', () => {
  const p = open();
  const anchor = $('#anchor');
  anchor.getBoundingClientRect = () => ({ left: 33, top: 10, width: 10, height: 10, right: 43, bottom: 20 });
  window.dispatchEvent(new dom.window.Event('resize'));
  assert.equal(p.el.style.left, '33px');
  assert.equal(p.el.style.top, '24px');
  delete anchor.getBoundingClientRect;
});

test('6. an anchor that leaves the document closes it within one observer callback and leaves nothing listening', async () => {
  const signals = [];
  const spy = (target) => {
    const add = target.addEventListener;
    target.addEventListener = function (type, fn, opts) { signals.push([type, opts?.signal]); return add.call(this, type, fn, opts); };
    return () => { target.addEventListener = add; };
  };
  const restore = [spy(document), spy(window)];
  const p = open();
  restore.forEach((r) => r());
  assert.deepEqual(signals.map(([t]) => t).sort(), ['pointerdown', 'resize', 'scroll']);
  $('#wrap').remove();
  await tick();
  assert.deepEqual(p.reasons, ['detached']);
  assert.equal(p.el.isConnected, false);
  assert.equal(openPopoverCount(), 0);
  assert.ok(signals.every(([, s]) => s?.aborted), 'every document and window listener is released');
  fire(document.body, 'pointerdown');
  key(document.body, 'Escape');
  document.dispatchEvent(new dom.window.Event('scroll'));
  assert.deepEqual(p.reasons, ['detached']);
});

test('6. an anchor removed on its own takes the popover with it', async () => {
  const p = open();
  $('#anchor').remove();
  await tick();
  assert.deepEqual(p.reasons, ['detached']);
  assert.equal(p.el.isConnected, false);
});

test('7. opening another popover replaces an open one; one opened from inside an open popover keeps its parent', () => {
  const a = open();
  const b = openPopover({ anchor: $('#outside'), content: menuItems(2), role: 'menu', label: 'Other', onClose: () => {} });
  opened.push(b);
  assert.deepEqual(a.reasons, ['replaced']);
  assert.equal(openPopoverCount(), 1);

  const inner = button('More', { 'data-popover-item': '' });
  const last = button('Last', { 'data-popover-item': '' });
  const parent = open({ content: [inner, last], role: 'dialog' });
  assert.equal(b.isOpen(), false);
  const child = openPopover({ anchor: inner, content: menuItems(2), role: 'menu', label: 'Child' });
  opened.push(child);
  assert.equal(parent.isOpen(), true);
  assert.equal(openPopoverCount(), 2);
  assert.ok(parent.el.contains(child.el));
  // The parent's arrows walk its own items, not the child's.
  inner.focus();
  key(inner, 'ArrowDown');
  assert.equal(document.activeElement, last);
});

test('8. focus "first" takes the first enabled item, else the first focusable', () => {
  const items = menuItems(3, (i) => (i === 0 ? { disabled: '' } : {}));
  const p = open({ content: items });
  assert.equal(document.activeElement, items[1]);
  p.close();
  const input = document.createElement('input');
  open({ content: [document.createTextNode('Find'), input], role: 'dialog', label: 'Find' });
  assert.equal(document.activeElement, input);
});

test('8. focus "checked" takes the checked, selected or pressed item, else the first; "none" leaves focus alone', () => {
  for (const attr of ['aria-checked', 'aria-selected', 'aria-pressed']) {
    const items = menuItems(3, (i) => ({ [attr]: String(i === 2) }));
    const p = open({ content: items, focus: 'checked' });
    assert.equal(document.activeElement, items[2], attr);
    p.close();
  }
  const items = menuItems(3, (i) => ({ 'aria-disabled': String(i === 0) }));
  const p = open({ content: items, focus: 'checked' });
  assert.equal(document.activeElement, items[1]);
  p.close();
  $('#field').focus();
  open({ focus: 'none' });
  assert.equal(document.activeElement, $('#field'));
});

test('9. arrows step over a disabled item and wrap; Home and End jump; each is used up', () => {
  const items = menuItems(3, (i) => (i === 1 ? { disabled: '' } : {}));
  open({ content: items });
  assert.equal(document.activeElement, items[0]);
  let e = key(items[0], 'ArrowDown');
  assert.equal(document.activeElement, items[2]);
  assert.equal(e.defaultPrevented, true);
  key(items[2], 'ArrowDown');
  assert.equal(document.activeElement, items[0], 'wraps');
  key(items[0], 'ArrowUp');
  assert.equal(document.activeElement, items[2], 'wraps back');
  e = key(items[2], 'Home');
  assert.equal(document.activeElement, items[0]);
  assert.equal(e.defaultPrevented, true);
  key(items[0], 'End');
  assert.equal(document.activeElement, items[2]);
});

test('9. with focus on a text input in a dialog popover, arrows are left alone', () => {
  const input = document.createElement('input');
  const items = menuItems(2, () => ({ role: 'option' }));
  open({ content: [input, ...items], role: 'dialog', label: 'Pick' });
  assert.equal(document.activeElement, items[0], 'items come before other focusables');
  input.focus();
  const e = key(input, 'ArrowDown');
  assert.equal(e.defaultPrevented, false);
  assert.equal(document.activeElement, input);
});

test('10. the anchor names the popup kind unless it is a combobox, and is collapsed again after close', () => {
  for (const role of ['menu', 'listbox', 'dialog']) {
    const p = open({ role });
    assert.equal($('#anchor').getAttribute('aria-haspopup'), role);
    p.close();
    assert.equal($('#anchor').getAttribute('aria-expanded'), 'false');
  }
  const combo = $('#field');
  combo.setAttribute('role', 'combobox');
  const p = openPopover({ anchor: combo, content: menuItems(1, () => ({ role: 'option' })), role: 'listbox', label: 'Choices', focus: 'none' });
  opened.push(p);
  assert.equal(combo.hasAttribute('aria-haspopup'), false);
  assert.equal(combo.getAttribute('aria-expanded'), 'true');
});

test('11. close with returnFocus, or any close while focus is inside, focuses the anchor', () => {
  $('#outside').focus();
  let p = open({ focus: 'none' });
  p.close('api', { returnFocus: true });
  assert.equal(document.activeElement, $('#anchor'));

  $('#outside').focus();
  p = open({ focus: 'none' });
  p.close();
  assert.equal(document.activeElement, $('#outside'), 'focus outside stays put');

  p = open();
  p.close();
  assert.equal(document.activeElement, $('#anchor'), 'focus was inside');
});

test('11. close is idempotent: onClose runs once', () => {
  const p = open();
  p.close();
  p.close('escape');
  assert.deepEqual(p.reasons, ['api']);
});
