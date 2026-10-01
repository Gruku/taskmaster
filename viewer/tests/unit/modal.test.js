// User intent: every modal in the viewer stands on one shell, so its accessibility contract — labelling, inert page,
// stacking, veto, focus return, an in-app confirm — is pinned here line by line.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const PAGE = '<div class="shell"><button id="opener">o</button><section id="screen-mount"><a href="#x" id="fallback">f</a></section></div><div id="modal-host"></div>';
const dom = new JSDOM(`<!doctype html><html><body>${PAGE}</body></html>`);
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.HTMLElement = dom.window.HTMLElement;

const { openModal, confirmDialog, openModalCount, focusableIn, resolveFocusTarget } = await import('../../js/components/modal.js');

const $ = (sel) => document.querySelector(sel);
const shell = () => $('.shell');
// Initial focus is applied after the caller has filled the body, one microtask later.
const tick = () => new Promise((ok) => setTimeout(ok, 0));
const fire = (el, type, init = {}) => el.dispatchEvent(Object.assign(new dom.window.Event(type, { bubbles: true, cancelable: true }), init));
const key = (k) => fire(document.activeElement ?? document.body, 'keydown', { key: k });
const overlayOf = (m) => m.dialog.parentElement;
const clickOverlay = (m) => { for (const t of ['pointerdown', 'pointerup', 'click']) fire(overlayOf(m), t); };

test.beforeEach(() => {
  assert.equal(openModalCount(), 0, 'the previous test left a modal open');
  document.body.innerHTML = PAGE;
  document.body.className = '';
});

test('1. the dialog is a labelled modal dialog; each modal has its own title id', () => {
  const a = openModal({ title: 'First' });
  const b = openModal({ title: 'Second' });
  for (const m of [a, b]) {
    assert.equal(m.dialog.getAttribute('role'), 'dialog');
    assert.equal(m.dialog.getAttribute('aria-modal'), 'true');
    const title = document.getElementById(m.dialog.getAttribute('aria-labelledby'));
    assert.ok(m.dialog.contains(title), 'aria-labelledby points inside the dialog');
  }
  assert.equal(document.getElementById(a.dialog.getAttribute('aria-labelledby')).textContent, 'First');
  assert.equal(document.getElementById(b.dialog.getAttribute('aria-labelledby')).textContent, 'Second');
  assert.notEqual(a.dialog.getAttribute('aria-labelledby'), b.dialog.getAttribute('aria-labelledby'));
  assert.ok($('#modal-host').contains(a.dialog), 'mounted in #modal-host');
  b.close(); a.close();
});

test('1. title accepts a Node and can be replaced; the eyebrow is optional and hidden while empty', () => {
  const strong = document.createElement('strong');
  strong.textContent = 'Node title';
  const m = openModal({ title: strong, eyebrow: 'T-101', size: 'lg', className: 'extra' });
  const title = document.getElementById(m.dialog.getAttribute('aria-labelledby'));
  assert.equal(title.firstChild, strong);
  assert.equal(m.dialog.querySelector('.modal-eyebrow').textContent, 'T-101');
  assert.ok(m.dialog.classList.contains('modal--lg') && m.dialog.classList.contains('extra'));
  m.setTitle('<img src=x onerror=alert(1)>');
  assert.equal(title.textContent, '<img src=x onerror=alert(1)>');
  assert.equal(title.querySelector('img'), null, 'a string title is text, never markup');
  m.setEyebrow('');
  assert.equal(m.dialog.querySelector('.modal-eyebrow').hidden, true);
  m.setEyebrow('T-102');
  assert.equal(m.dialog.querySelector('.modal-eyebrow').hidden, false);
  assert.ok(m.header.contains(m.actions) && m.dialog.contains(m.body) && m.dialog.contains(m.footer));
  m.close();
  const plain = openModal({ title: 'x' });
  assert.ok(plain.dialog.classList.contains('modal--md'), 'md is the default size');
  assert.equal(plain.dialog.querySelector('.modal-eyebrow').hidden, true);
  plain.close();
});

test('2. the page is inert and body is marked while any modal is open, and only until the last closes', () => {
  const a = openModal({ title: 'A' });
  assert.equal(shell().hasAttribute('inert'), true);
  assert.equal(document.body.classList.contains('modal-open'), true);
  assert.equal(openModalCount(), 1);
  const b = openModal({ title: 'B' });
  assert.equal(openModalCount(), 2);
  b.close();
  assert.equal(shell().hasAttribute('inert'), true, 'one modal is still open');
  assert.equal(document.body.classList.contains('modal-open'), true);
  a.close();
  assert.equal(shell().hasAttribute('inert'), false);
  assert.equal(document.body.classList.contains('modal-open'), false);
  assert.equal(openModalCount(), 0);
});

test('2. closing restores the inert state it found, on .shell only', () => {
  shell().setAttribute('inert', '');
  const m = openModal({ title: 'A' });
  m.close();
  assert.equal(shell().hasAttribute('inert'), true, 'a shell that was already inert stays inert');
  shell().removeAttribute('inert');

  const main = document.createElement('main');
  main.className = 'main';
  main.setAttribute('inert', '');   // the mobile drawer's own state
  shell().appendChild(main);
  const n = openModal({ title: 'B' });
  n.close();
  assert.equal(main.hasAttribute('inert'), true, 'the drawer state on .main is not touched');
});

test('2. closing out of order still releases the page only when the last modal closes', () => {
  const a = openModal({ title: 'A' });
  const b = openModal({ title: 'B' });
  a.close();
  assert.equal(shell().hasAttribute('inert'), true);
  assert.equal(b.isTop(), true);
  b.close();
  assert.equal(shell().hasAttribute('inert'), false);
});

test('3. a second modal makes the first dialog inert until it closes; isTop follows', () => {
  const a = openModal({ title: 'A' });
  assert.equal(a.isTop(), true);
  const b = openModal({ title: 'B' });
  assert.equal(a.dialog.hasAttribute('inert'), true);
  assert.equal(b.dialog.hasAttribute('inert'), false);
  assert.equal(a.isTop(), false);
  assert.equal(b.isTop(), true);
  const c = openModal({ title: 'C' });
  assert.equal(b.dialog.hasAttribute('inert'), true);
  c.close();
  assert.equal(b.dialog.hasAttribute('inert'), false);
  assert.equal(a.dialog.hasAttribute('inert'), true, 'still covered by B');
  b.close();
  assert.equal(a.dialog.hasAttribute('inert'), false);
  assert.equal(a.isTop(), true);
  a.close();
  assert.equal(a.isTop(), false, 'a closed modal is not on top');
});

test('5. Escape asks only the topmost modal to close', async () => {
  const a = openModal({ title: 'A' });
  const b = openModal({ title: 'B' });
  key('Escape');
  await tick();
  assert.equal(b.dialog.isConnected, false);
  assert.equal(a.dialog.isConnected, true);
  key('Escape');
  await tick();
  assert.equal(a.dialog.isConnected, false);
  assert.equal(openModalCount(), 0);
});

test('5. an Escape that a control inside the modal already handled does not close it', async () => {
  const m = openModal({ title: 'A' });
  const input = document.createElement('input');
  input.addEventListener('keydown', (e) => e.preventDefault());
  m.body.appendChild(input);
  fire(input, 'keydown', { key: 'Escape' });
  await tick();
  assert.equal(m.dialog.isConnected, true);
  m.close();
});

test('5. an Escape the modal acted on never reaches a page-level listener behind it', async () => {
  let seen = 0;
  const onKey = (e) => { if (e.key === 'Escape') seen++; };
  document.addEventListener('keydown', onKey);
  const m = openModal({ title: 'A' });
  await tick();
  key('Escape');
  await tick();
  document.removeEventListener('keydown', onKey);
  assert.equal(m.dialog.isConnected, false);
  assert.equal(seen, 0);
});

test('5. a press and release on the overlay closes; a press that began inside the dialog does not', async () => {
  const m = openModal({ title: 'A' });
  fire(m.body, 'pointerdown');
  fire(overlayOf(m), 'pointerup');
  fire(overlayOf(m), 'click');   // the browser targets the click at the common ancestor
  await tick();
  assert.equal(m.dialog.isConnected, true, 'a drag out of the dialog is not a dismissal');
  fire(overlayOf(m), 'pointerdown');
  fire(m.body, 'pointerup');
  fire(overlayOf(m), 'click');
  await tick();
  assert.equal(m.dialog.isConnected, true, 'a drag into the dialog is not a dismissal');
  fire(m.body, 'pointerdown'); fire(m.body, 'pointerup'); fire(m.body, 'click');
  await tick();
  assert.equal(m.dialog.isConnected, true, 'a click inside is not a dismissal');
  clickOverlay(m);
  await tick();
  assert.equal(m.dialog.isConnected, false);
});

test('6. onRequestClose can veto, synchronously or through a promise', async () => {
  let answer = false;
  const sync = openModal({ title: 'A', onRequestClose: () => answer });
  assert.equal(await sync.requestClose(), false);
  assert.equal(sync.dialog.isConnected, true);
  key('Escape'); await tick();
  assert.equal(sync.dialog.isConnected, true);
  clickOverlay(sync); await tick();
  assert.equal(sync.dialog.isConnected, true);
  sync.dialog.querySelector('.modal-close').click(); await tick();
  assert.equal(sync.dialog.isConnected, true, 'the close button asks too');
  answer = true;
  assert.equal(await sync.requestClose(), true);
  assert.equal(sync.dialog.isConnected, false);

  const asyncVeto = openModal({ title: 'B', onRequestClose: () => Promise.resolve(false) });
  assert.equal(await asyncVeto.requestClose(), false);
  assert.equal(asyncVeto.dialog.isConnected, true);
  asyncVeto.close();

  const noAnswer = openModal({ title: 'C', onRequestClose: () => {} });
  assert.equal(await noAnswer.requestClose(), true, 'returning nothing means close');
  assert.equal(noAnswer.dialog.isConnected, false);

  const plain = openModal({ title: 'D' });
  assert.equal(await plain.requestClose(), true, 'no guard means close');
});

test('6. a guard that throws keeps the modal open; a second request while one is pending asks once', async () => {
  const errors = [];
  const original = console.error;
  console.error = (...a) => errors.push(a);
  try {
    const m = openModal({ title: 'A', onRequestClose: () => { throw new Error('boom'); } });
    assert.equal(await m.requestClose(), false);
    assert.equal(m.dialog.isConnected, true);
    assert.equal(errors.length, 1);
    m.close();
  } finally { console.error = original; }

  let asked = 0;
  let release;
  const slow = openModal({ title: 'B', onRequestClose: () => { asked++; return new Promise((ok) => { release = ok; }); } });
  const first = slow.requestClose();
  const second = slow.requestClose();
  release(true);
  assert.deepEqual(await Promise.all([first, second]), [true, true]);
  assert.equal(asked, 1);
  assert.equal(slow.dialog.isConnected, false);
});

test('7. focus returns to the opener when it is still there', async () => {
  $('#opener').focus();
  const m = openModal({ title: 'A' });   // opener defaults to the focused element
  await tick();
  assert.ok(m.dialog.contains(document.activeElement), 'focus moved into the dialog');
  m.close();
  assert.equal(document.activeElement, $('#opener'));

  const explicit = openModal({ title: 'B', opener: $('#fallback') });
  await tick();
  explicit.close();
  assert.equal(document.activeElement, $('#fallback'));
});

test('7. opener removed while open: focus goes to the first focusable of the screen, without throwing', async () => {
  $('#opener').focus();
  const m = openModal({ title: 'A' });
  await tick();
  $('#opener').remove();
  assert.doesNotThrow(() => m.close());
  assert.equal(document.activeElement, $('#fallback'));
});

test('7. opener removed inside a surviving focusable ancestor: focus goes to that ancestor', async () => {
  const card = document.createElement('div');
  card.tabIndex = 0;
  const inner = document.createElement('button');
  card.appendChild(inner);
  $('#screen-mount').appendChild(card);
  inner.focus();
  const m = openModal({ title: 'A' });
  await tick();
  inner.remove();
  m.close();
  assert.equal(document.activeElement, card);
});

test('7. opener removed and nothing else focusable: focus rests on body, without throwing', async () => {
  $('#opener').focus();
  const m = openModal({ title: 'A' });
  await tick();
  $('#opener').remove();
  $('#fallback').remove();
  assert.doesNotThrow(() => m.close());
  assert.equal(document.activeElement, document.body);
});

test('7. an opener that can no longer take focus is skipped', async () => {
  $('#opener').focus();
  const m = openModal({ title: 'A' });
  await tick();
  $('#opener').disabled = true;
  m.close();
  assert.equal(document.activeElement, $('#fallback'));
});

test('7. a modal opened from a modal returns focus to the control that opened it', async () => {
  $('#opener').focus();
  const a = openModal({ title: 'A' });
  const edit = document.createElement('button');
  a.body.appendChild(edit);
  await tick();
  edit.focus();
  const b = openModal({ title: 'B' });
  await tick();
  assert.ok(b.dialog.contains(document.activeElement));
  b.close();
  assert.equal(document.activeElement, edit, 'not body, not the page behind');
  a.close();
  assert.equal(document.activeElement, $('#opener'));
});

test('7. a modal opened from a modal whose opener was re-rendered away keeps focus in the modal beneath', async () => {
  const a = openModal({ title: 'A' });
  const edit = document.createElement('button');
  a.body.appendChild(edit);
  await tick();
  edit.focus();
  const b = openModal({ title: 'B' });
  await tick();
  edit.remove();
  b.close();
  assert.ok(a.dialog.contains(document.activeElement), 'the page behind is still inert');
  a.close();
});

test('7. a covered modal that closes does not pull focus out of the one on top', async () => {
  $('#opener').focus();
  const a = openModal({ title: 'A' });
  const b = openModal({ title: 'B' });
  await tick();
  const held = document.activeElement;
  assert.ok(b.dialog.contains(held));
  a.close();
  assert.equal(document.activeElement, held);
  b.close();
});

test('initial focus: first focusable of the body, else the close button; initialFocus overrides', async () => {
  const empty = openModal({ title: 'A' });
  await tick();
  assert.equal(document.activeElement, empty.dialog.querySelector('.modal-close'));
  empty.close();

  const filled = openModal({ title: 'B' });
  const input = document.createElement('input');
  const save = document.createElement('button');
  filled.body.appendChild(input);
  filled.footer.appendChild(save);
  await tick();
  assert.equal(document.activeElement, input, 'the body is filled after openModal returns');
  filled.close();

  const chosen = openModal({ title: 'C', initialFocus: (dialog) => dialog.querySelector('.pick') });
  const pick = document.createElement('button');
  pick.className = 'pick';
  chosen.body.append(document.createElement('input'), pick);
  await tick();
  assert.equal(document.activeElement, pick);
  chosen.close();

  const none = openModal({ title: 'D', initialFocus: () => null });
  await tick();
  assert.equal(document.activeElement, none.dialog.querySelector('.modal-close'), 'null falls back to the default');
  none.close();
});

test('8. close is idempotent and onClosed callbacks run once', () => {
  const m = openModal({ title: 'A' });
  let calls = 0;
  m.onClosed(() => { calls++; });
  m.onClosed(() => { calls++; });
  m.close();
  assert.doesNotThrow(() => m.close());
  assert.equal(calls, 2);
  assert.equal(openModalCount(), 0);
  assert.equal(shell().hasAttribute('inert'), false);
  let late = 0;
  m.onClosed(() => { late++; });
  assert.equal(late, 1, 'registering after close still runs, once');
});

test('8. a throwing onClosed callback does not stop the others or the close', () => {
  const original = console.error;
  console.error = () => {};
  try {
    const m = openModal({ title: 'A' });
    let ran = false;
    m.onClosed(() => { throw new Error('boom'); });
    m.onClosed(() => { ran = true; });
    assert.doesNotThrow(() => m.close());
    assert.equal(ran, true);
    assert.equal(m.dialog.isConnected, false);
  } finally { console.error = original; }
});

test('9. confirmDialog resolves true on confirm and false on cancel, Escape and overlay click', async () => {
  const confirmBtn = () => $('.modal [data-confirm]');
  const cancelBtn = () => $('.modal [data-cancel]');

  let p = confirmDialog({ title: 'Discard changes?', message: 'Your edits will be lost.' });
  assert.equal($('.modal .modal-title').textContent, 'Discard changes?');
  assert.match($('.modal .modal-body').textContent, /Your edits will be lost\./);
  assert.equal(confirmBtn().textContent, 'Confirm');
  assert.equal(cancelBtn().textContent, 'Cancel');
  confirmBtn().click();
  assert.equal(await p, true);
  assert.equal(openModalCount(), 0);

  p = confirmDialog({ title: 'T', message: 'm', confirmLabel: 'Discard', cancelLabel: 'Keep editing' });
  assert.equal(confirmBtn().textContent, 'Discard');
  assert.equal(cancelBtn().textContent, 'Keep editing');
  cancelBtn().click();
  assert.equal(await p, false);

  p = confirmDialog({ title: 'T', message: 'm' });
  key('Escape');
  assert.equal(await p, false);

  p = confirmDialog({ title: 'T', message: 'm' });
  for (const t of ['pointerdown', 'pointerup', 'click']) fire($('.modal-overlay'), t);
  assert.equal(await p, false);

  p = confirmDialog({ title: 'T', message: 'm' });
  $('.modal .modal-close').click();
  assert.equal(await p, false);
  assert.equal(openModalCount(), 0);
});

test('9. confirmDialog focuses confirm by default and cancel when critical; critical marks the confirm button', async () => {
  let p = confirmDialog({ title: 'T', message: 'm' });
  await tick();
  assert.equal(document.activeElement, $('.modal [data-confirm]'));
  assert.equal($('.modal [data-confirm]').classList.contains('btn--critical'), false);
  assert.equal($('.modal [data-confirm]').type, 'button');
  $('.modal [data-cancel]').click();
  await p;

  p = confirmDialog({ title: 'T', message: 'm', tone: 'critical' });
  await tick();
  assert.equal(document.activeElement, $('.modal [data-cancel]'));
  assert.equal($('.modal [data-confirm]').classList.contains('btn--critical'), true);
  $('.modal [data-cancel]').click();
  await p;
});

test('9. a confirm stacked on a modal closes alone and hands focus back', async () => {
  const form = openModal({ title: 'Edit task' });
  const discard = document.createElement('button');
  form.body.appendChild(discard);
  await tick();
  discard.focus();
  const p = confirmDialog({ title: 'Discard?', message: 'm' });
  await tick();
  assert.equal(form.dialog.hasAttribute('inert'), true);
  key('Escape');
  assert.equal(await p, false);
  assert.equal(form.dialog.isConnected, true);
  assert.equal(document.activeElement, discard);
  form.close();
});

test('10. the close button is labelled Close and draws the dismiss icon', () => {
  const m = openModal({ title: 'A' });
  const close = m.dialog.querySelector('.modal-close');
  assert.equal(close.getAttribute('aria-label'), 'Close');
  assert.equal(close.type, 'button');
  const svg = close.querySelector('svg.icon');
  assert.ok(svg, 'an inline icon');
  assert.equal(svg.getAttribute('aria-hidden'), 'true');
  assert.match(svg.innerHTML, /M5\.5 5\.5 L18\.5 18\.5/);
  m.close();
});

test('helpers: focusableIn lists what Tab can reach, in order', () => {
  const root = document.createElement('div');
  root.innerHTML = '<button id="a">a</button><button disabled>x</button><input type="hidden"><a id="b" href="#">b</a><a>no href</a>'
    + '<span id="c" tabindex="0">c</span><span tabindex="-1">skip</span><div hidden><button>h</button></div>'
    + '<div inert><button>i</button></div><textarea id="d"></textarea><select id="e"></select>';
  document.body.appendChild(root);
  assert.deepEqual(focusableIn(root).map((el) => el.id), ['a', 'b', 'c', 'd', 'e']);
  assert.deepEqual(focusableIn(null), []);
});

test('helpers: resolveFocusTarget prefers the opener, then a surviving ancestor, then the screen', () => {
  assert.equal(resolveFocusTarget($('#opener')), $('#opener'));
  assert.equal(resolveFocusTarget(null), $('#fallback'));
  assert.equal(resolveFocusTarget(document.body), $('#fallback'), 'body is not a target');
  const gone = document.createElement('button');
  assert.equal(resolveFocusTarget(gone), $('#fallback'));
  shell().setAttribute('inert', '');
  assert.equal(resolveFocusTarget($('#opener')), null, 'nothing inside an inert page is a target');
  shell().removeAttribute('inert');
  $('#fallback').remove();
  assert.equal(resolveFocusTarget(gone), null);
});

// ── Tab cycle: the dialog alone, or the conflict banner and then the dialog ──
// jsdom does not move focus on Tab, so a press the shell leaves to the browser shows as "not prevented".
const tab = (shiftKey = false) => !fire(document.activeElement ?? document.body, 'keydown', { key: 'Tab', shiftKey });
function formModal() {
  const m = openModal({ title: 'Edit task' });
  const input = document.createElement('input');
  const save = document.createElement('button');
  m.body.appendChild(input);
  m.footer.appendChild(save);
  return { m, input, save, closeBtn: m.dialog.querySelector('.modal-close') };
}
function addBanner(controls = 2) {
  const host = document.createElement('div');
  host.id = 'conflict-banner-host';
  const buttons = Array.from({ length: controls }, () => document.createElement('button'));
  host.append(...buttons);
  document.body.appendChild(host);
  return { host, buttons };
}

test('4. with no conflict banner, Tab wraps inside the dialog at both ends and is left alone in between', async () => {
  const { m, input, save, closeBtn } = formModal();
  await tick();
  assert.equal(document.activeElement, input);
  assert.equal(tab(), false, 'mid-dialog Tab is the browser\'s');
  assert.equal(tab(true), false);
  save.focus();
  assert.equal(tab(), true);
  assert.equal(document.activeElement, closeBtn, 'forward from the last control wraps to the first');
  assert.equal(tab(true), true);
  assert.equal(document.activeElement, save, 'backward from the first control wraps to the last');
  document.activeElement.blur();
  assert.equal(tab(), true);
  assert.equal(document.activeElement, closeBtn, 'lost focus is pulled back in');
  m.close();
});

test('4. an empty conflict banner host changes nothing', async () => {
  const { host } = addBanner(0);
  host.appendChild(document.createElement('div')).textContent = 'no controls here';
  const { m, save, closeBtn } = formModal();
  await tick();
  save.focus();
  assert.equal(tab(), true);
  assert.equal(document.activeElement, closeBtn);
  assert.equal(tab(true), true);
  assert.equal(document.activeElement, save);
  m.close();
});

test('4. a conflict banner joins the top modal\'s Tab cycle: banner, then dialog, wrapping', async () => {
  const { buttons: [useServer, keepMine] } = addBanner();
  const { m, input, save, closeBtn } = formModal();
  await tick();
  assert.equal(document.activeElement, input, 'opening still focuses the dialog, not the banner');

  save.focus();
  assert.equal(tab(), true);
  assert.equal(document.activeElement, useServer, 'Tab from the last dialog control reaches the banner\'s first');
  assert.equal(tab(), false, 'inside the banner Tab is the browser\'s');
  keepMine.focus();
  assert.equal(tab(), true);
  assert.equal(document.activeElement, closeBtn, 'Tab from the banner\'s last control enters the dialog');

  assert.equal(tab(true), true);
  assert.equal(document.activeElement, keepMine, 'Shift+Tab from the dialog\'s first control reaches the banner\'s last');
  assert.equal(tab(true), false);
  useServer.focus();
  assert.equal(tab(true), true);
  assert.equal(document.activeElement, save, 'Shift+Tab from the banner\'s first control wraps to the dialog\'s last');

  document.activeElement.blur();
  assert.equal(tab(), true);
  assert.equal(document.activeElement, closeBtn, 'lost focus goes to the dialog, not the banner');
  m.close();
});

test('4. the banner is reachable from the topmost modal only, and from a dialog with no controls of its own', async () => {
  const { buttons: [only] } = addBanner(1);
  const { m } = formModal();
  const confirm = openModal({ title: 'Discard?' });
  const confirmClose = confirm.dialog.querySelector('.modal-close');
  await tick();
  assert.equal(document.activeElement, confirmClose);
  assert.equal(tab(), true);
  assert.equal(document.activeElement, only);
  assert.equal(tab(), true);
  assert.equal(document.activeElement, confirmClose, 'never the covered dialog');
  assert.equal(tab(true), true);
  assert.equal(document.activeElement, only);
  confirm.close();
  m.close();

  const bare = openModal({ title: 'Bare' });
  bare.dialog.querySelector('.modal-close').remove();
  bare.dialog.focus();
  assert.equal(tab(), true);
  assert.equal(document.activeElement, only);
  assert.equal(tab(), true);
  assert.equal(document.activeElement, bare.dialog);
  assert.equal(tab(true), true);
  assert.equal(document.activeElement, only);
  bare.close();
});

test('4. a banner that goes away while the modal is open drops out of the cycle', async () => {
  const { host } = addBanner();
  const { m, save, closeBtn } = formModal();
  await tick();
  host.replaceChildren();
  save.focus();
  assert.equal(tab(), true);
  assert.equal(document.activeElement, closeBtn);
  m.close();
});

test('10. the close button is a ghost icon button from the shared button family', () => {
  const m = openModal({ title: 'A' });
  const close = m.dialog.querySelector('.modal-close');
  for (const c of ['btn', 'btn--ghost', 'btn--icon']) assert.ok(close.classList.contains(c), c);
  m.close();
});

test('4. Escape inside the conflict banner belongs to the banner: the modal beneath stays open; Escape in the dialog still asks to close', async () => {
  const { buttons: [useServer] } = addBanner();
  let asked = 0;
  const m = openModal({ title: 'Edit task', onRequestClose: () => { asked++; return false; } });
  const input = document.createElement('input');
  m.body.appendChild(input);
  await tick();

  useServer.focus();
  assert.equal(key('Escape'), true, 'the shell does not consume an Escape typed in the banner');
  await tick();
  assert.equal(asked, 0, 'the modal was not asked to close');
  assert.equal(openModalCount(), 1);

  input.focus();
  key('Escape');
  await tick();
  assert.equal(asked, 1, 'Escape from inside the dialog still requests close');

  // Focus lost to the page (not the banner) keeps the stray-Escape rescue.
  document.activeElement.blur();
  key('Escape');
  await tick();
  assert.equal(asked, 2);
  m.close();
});
