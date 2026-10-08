// User intent: one modal shell for the whole viewer — every dialog gets the same frame and the same guarantees
// (labelled, focus kept inside the topmost one, page inert behind it, focus handed back on close), and
// confirmations are asked in-app instead of through the browser's native dialogs.
import { h } from '../util/h.js';
import { icon } from './icon.js';

const SIZES = ['sm', 'md', 'lg'];
const stack = [];          // open modals, bottom → top
const returns = new WeakMap(); // modal → { from, ancestors }: where focus goes when it closes
let seq = 0;
let shellWasInert = false; // .shell's own inert state before the first modal opened

const BANNER_HOST = 'conflict-banner-host';   // filled by edit/conflict-banner.js; painted above every modal

const CAN_FOCUS = 'a[href], area[href], button, input, select, textarea, iframe, audio[controls], video[controls], '
  + 'details > summary:first-of-type, [tabindex], [contenteditable]:not([contenteditable="false"])';

// A closed <details> shows only its first summary; the rest of it is not rendered.
function inClosedDetails(el) {
  for (let d = el.parentElement?.closest('details:not([open])'); d; d = d.parentElement?.closest('details:not([open])')) {
    if (!d.querySelector(':scope > summary')?.contains(el)) return true;
  }
  return false;
}

function canTakeFocus(el) {
  if (!el || el.nodeType !== 1 || !el.isConnected || !el.matches(CAN_FOCUS)) return false;
  if (el.disabled || el.type === 'hidden' || el.closest('[inert], [hidden]') || inClosedDetails(el)) return false;
  // jsdom has no layout; a browser also rules out display:none and visibility:hidden. A rendered element of no
  // size stays in: the browser tabs to it.
  return typeof el.checkVisibility === 'function' ? el.checkVisibility({ visibilityProperty: true }) : true;
}

// What Tab can reach inside `root`, in document order. Only a tabindex written on the element takes it out: the
// tabIndex an editable region or a media element reports without one is not what the browser's Tab does.
export function focusableIn(root) {
  if (!root) return [];
  return [...root.querySelectorAll(CAN_FOCUS)]
    .filter((el) => (!el.hasAttribute('tabindex') || el.tabIndex >= 0) && canTakeFocus(el))
    // jsdom's selector engine does not return this list in document order; a browser's already is.
    .sort((a, b) => (a.compareDocumentPosition(b) & 4 ? -1 : 1));
}

// Where focus goes when a modal closes, best first: the opener, the nearest ancestor of a removed
// opener that is still on the page, then the first focusable of `within` — the modal beneath, or
// the screen when none is left.
function focusTargets(opener, { ancestors = [], within } = {}) {
  const root = within ?? document.getElementById('screen-mount');
  return [opener, ...ancestors, ...focusableIn(root).slice(0, 1), within].filter(canTakeFocus);
}

export function resolveFocusTarget(opener, opts) {
  return focusTargets(opener, opts)[0] ?? null;
}

export function openModalCount() {
  return stack.length;
}

const top = () => stack.at(-1);

// The handle of the topmost open modal, or null — for a modal that must ask the ones above it to close.
export function topModal() {
  return top() ?? null;
}

// The page behind is inert while any modal is open; a covered modal is inert until it is on top again.
function syncLayers() {
  const shell = document.querySelector('.shell');
  if (stack.length) {
    shell?.setAttribute('inert', '');
  } else if (!shellWasInert) {
    shell?.removeAttribute('inert');
  }
  document.body.classList.toggle('modal-open', stack.length > 0);
  for (const m of stack) m.dialog.toggleAttribute('inert', m !== top());
}

// Wraps Tab by hand so containment does not depend on the browser honouring `inert`.
// A conflict banner asks the user to act while the form is open, so its controls join the topmost
// modal's cycle: banner first, then the dialog, wrapping. Only the crossings are taken over; Tab
// between two controls of the same group stays the browser's.
function onTab(e) {
  const m = top();
  if (!m || e.key !== 'Tab') return;
  const bannerHost = document.getElementById(BANNER_HOST);
  const banner = focusableIn(bannerHost);
  const own = focusableIn(m.dialog);
  const groups = [banner, own.length ? own : [m.dialog]].filter((g) => g.length);
  const active = document.activeElement;
  const at = banner.length && bannerHost.contains(active) ? 0 : m.dialog.contains(active) ? groups.length - 1 : -1;
  let to = null;
  if (at < 0) {
    // Focus lost to the page comes back to the dialog, not the banner.
    to = e.shiftKey ? groups.at(-1).at(-1) : groups.at(-1)[0];
  } else {
    const group = groups[at];
    const atEdge = active === (e.shiftKey ? group[0] : group.at(-1)) || (e.shiftKey && active === m.dialog);
    const next = groups[(at + (e.shiftKey ? groups.length - 1 : 1)) % groups.length];
    if (atEdge) to = e.shiftKey ? next.at(-1) : next[0];
  }
  if (to) { e.preventDefault(); to.focus(); }
}

// Escape reaches the modal after the control it was typed in: one that used the key for itself
// (and said so with preventDefault) keeps the modal open.
function isEscape(e) {
  return e.key === 'Escape' && !e.defaultPrevented && !e.isComposing;
}

// Escape typed while focus is outside every modal (it was lost with a removed element) still closes the top one.
// Not from inside the conflict banner: it owns its keys, and closing the form being resolved would lose the edit.
function onStrayEscape(e) {
  if (!isEscape(e) || e.target?.closest?.(`.modal-overlay, #${BANNER_HOST}`)) return;
  e.preventDefault();
  top()?.requestClose();
}

// `onRequestClose` answers every close the user asks for (Escape, the overlay, the close button, requestClose()); a
// guard whose answer never settles keeps the modal open, every request meanwhile shares that one pending answer, and
// close() still closes it.
export function openModal({ title, eyebrow, size = 'md', className, onRequestClose, opener, initialFocus } = {}) {
  const id = `modal-title-${++seq}`;
  const eyebrowEl = h('div', { class: 'modal-eyebrow' });
  const titleEl = h('h2', { class: 'modal-title', id });
  const actions = h('div', { class: 'modal-actions' });
  const closeBtn = h('button', { type: 'button', class: 'modal-close btn btn--ghost btn--icon', 'aria-label': 'Close' }, icon('dismiss'));
  const header = h('header', { class: 'modal-header' },
    h('div', { class: 'modal-heading' }, eyebrowEl, titleEl), actions, closeBtn);
  const body = h('div', { class: 'modal-body' });
  const footer = h('footer', { class: 'modal-footer' });
  const dialog = h('div', {
    class: ['modal', `modal--${SIZES.includes(size) ? size : 'md'}`, className].filter(Boolean).join(' '),
    role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': id, tabindex: '-1',
  }, header, body, footer);
  const overlay = h('div', { class: 'modal-overlay' }, dialog);

  const active = document.activeElement;
  const from = opener ?? (active && active !== document.body ? active : null);
  // Kept from open time: once the opener is removed its parent chain is gone. An opener inside a popover goes when
  // the popover closes (a control parked behind Filters is out of the page again); the button that opened the
  // popover stands in for it first.
  const popover = from?.closest('.popover');
  const ancestors = popover?.id ? [...document.querySelectorAll(`[aria-controls="${popover.id}"]`)] : [];
  for (let p = from?.parentElement; p && p !== document.body; p = p.parentElement) ancestors.push(p);
  // A modal beneath that closes first may hand this one its own target (see close()).
  const back = { from, ancestors };

  let closed = false;
  let pending = null;       // the close request being decided, if any
  const closedFns = [];
  const keyFns = [];        // onKey handlers, in the order they were added
  const view = document.defaultView;
  let pressed = false;      // a press that began inside the overlay is still held
  const afterFns = new Set();

  function setTitle(value) {
    titleEl.replaceChildren(value == null ? '' : value);
  }
  function setEyebrow(text) {
    eyebrowEl.textContent = text ?? '';
    eyebrowEl.hidden = !eyebrowEl.textContent;
  }
  function run(fn, what = 'onClosed callback') {
    try { return fn(); } catch (err) { console.error(`modal ${what} failed`, err); return undefined; }
  }

  // A press is held from a pointerdown inside the overlay until it is released anywhere, or the window loses it.
  const RELEASE = ['pointerup', 'pointercancel'];
  function press() {
    if (pressed) return;
    pressed = true;
    for (const type of RELEASE) document.addEventListener(type, release, true);
    view?.addEventListener('blur', release);
  }
  function release() {
    if (!pressed) return;
    pressed = false;
    for (const type of RELEASE) document.removeEventListener(type, release, true);
    view?.removeEventListener('blur', release);
    // On the next task, so the click this release belongs to lands first.
    if (afterFns.size) setTimeout(flush, 0);
  }
  function flush() {
    if (closed || pressed) return;   // a new press keeps the queue for its own release
    const fns = [...afterFns];
    afterFns.clear();
    for (const fn of fns) run(fn, 'afterPress callback');
  }
  function afterPress(fn) {
    if (closed) return;
    if (pressed) afterFns.add(fn);
    else fn();
  }

  function onKey(fn) {
    if (closed) return () => {};
    const entry = { fn };
    keyFns.push(entry);
    return () => { const at = keyFns.indexOf(entry); if (at >= 0) keyFns.splice(at, 1); };
  }

  function close() {
    if (closed) return;
    closed = true;
    const wasTop = top() === handle;
    const at = stack.indexOf(handle);
    // A modal above that was opened from inside this one would hand focus back into a dialog that is gone: it
    // returns where this one would have.
    for (const above of stack.slice(at + 1)) {
      const r = returns.get(above);
      if (r?.from && (dialog.contains(r.from) || r.ancestors.includes(dialog))) Object.assign(r, back);
    }
    stack.splice(at, 1);
    keyFns.length = 0;
    afterFns.clear();
    release();
    overlay.remove();
    if (!stack.length) {
      document.removeEventListener('keydown', onTab, true);
      document.removeEventListener('keydown', onStrayEscape);
    }
    syncLayers();
    // Focus moves only after the layers are released, or the target would still be inert.
    // A covered modal that closes leaves focus where it is, in the one on top.
    for (const target of wasTop ? focusTargets(back.from, { ancestors: back.ancestors, within: top()?.dialog }) : []) {
      try { target.focus(); } catch { /* a target that refuses focus is skipped */ }
      if (document.activeElement === target) break;
    }
    for (const fn of closedFns.splice(0)) run(fn);
  }

  // Resolves true when the modal closed. Decided synchronously when the guard answers synchronously.
  function requestClose() {
    if (closed) return Promise.resolve(true);
    if (pending) return pending;
    const settle = (answer) => {
      if (answer === false) return false;
      close();
      return true;
    };
    const refuse = (err) => {
      // A failing guard must not lose what it was guarding: the modal stays open.
      console.error('modal onRequestClose failed', err);
      return false;
    };
    let answer;
    try { answer = onRequestClose?.(); } catch (err) { return Promise.resolve(refuse(err)); }
    if (typeof answer?.then !== 'function') return Promise.resolve(settle(answer));
    pending = Promise.resolve(answer).then(settle, refuse).finally(() => { pending = null; });
    return pending;
  }

  const handle = {
    dialog, header, actions, body, footer,
    setTitle, setEyebrow, requestClose, close,
    isTop: () => top() === handle,
    onClosed(fn) { if (closed) run(fn); else closedFns.push(fn); },
    onKey, afterPress,
    pressing: () => pressed,
  };
  returns.set(handle, back);

  setTitle(title);
  setEyebrow(eyebrow);
  closeBtn.addEventListener('click', () => { requestClose(); });

  // A dismissal is a press and a release that both land on the overlay itself; a drag that starts
  // in the dialog (selecting text, a scrollbar) and ends outside is not one.
  let downOnOverlay = false;
  let upOnOverlay = false;
  overlay.addEventListener('pointerdown', (e) => { downOnOverlay = e.target === overlay && !e.button; upOnOverlay = false; });
  overlay.addEventListener('pointerdown', press, true);
  overlay.addEventListener('pointerup', (e) => { upOnOverlay = e.target === overlay; });
  overlay.addEventListener('click', (e) => {
    const dismiss = downOnOverlay && upOnOverlay && e.target === overlay;
    downOnOverlay = upOnOverlay = false;
    if (dismiss) requestClose();
  });

  // A form's own keys (Ctrl+Enter) are taken on the way down, before the focused control acts on them.
  dialog.addEventListener('keydown', (e) => {
    if (top() !== handle) return;
    for (const { fn } of [...keyFns]) {
      if (run(() => fn(e), 'onKey handler') !== true) continue;
      e.preventDefault();
      e.stopPropagation();
      return;
    }
  }, true);

  // Handled here and stopped, so a page-level Escape listener behind the modal never acts on the same key.
  overlay.addEventListener('keydown', (e) => {
    if (!isEscape(e)) return;
    e.preventDefault();
    e.stopPropagation();
    if (top() === handle) requestClose();
  });

  if (!stack.length) {
    shellWasInert = document.querySelector('.shell')?.hasAttribute('inert') ?? false;
    // Capture phase: Tab containment must hold even when a field stops the event on its way up.
    document.addEventListener('keydown', onTab, true);
    document.addEventListener('keydown', onStrayEscape);
  }
  stack.push(handle);
  (document.getElementById('modal-host') ?? document.body).appendChild(overlay);
  syncLayers();

  // Callers fill the body after this returns, so the first focus waits for them.
  queueMicrotask(() => {
    if (closed || top() !== handle) return;
    const target = [initialFocus?.(dialog), focusableIn(body)[0], closeBtn].find(canTakeFocus) ?? dialog;
    target.focus();
  });

  return handle;
}

// `alert`: a question that interrupts to ask before a change (not a destructive one) is still an alert dialog.
export function confirmDialog({ title, message, confirmLabel = 'Confirm', cancelLabel = 'Cancel', tone = 'default', alert = false } = {}) {
  return new Promise((resolve) => {
    const critical = tone === 'critical';
    let confirmed = false;
    const cancel = h('button', { type: 'button', class: 'btn btn--secondary', 'data-cancel': '' }, cancelLabel);
    const confirm = h('button', { type: 'button', class: `btn ${critical ? 'btn--critical' : 'btn--primary'}`, 'data-confirm': '' }, confirmLabel);
    // A destructive answer is never one stray Enter away: critical confirms start on Cancel.
    const modal = openModal({ title, size: 'sm', className: 'modal--confirm', initialFocus: () => (critical ? cancel : confirm) });
    // The question is read with the title: "Discard changes?" alone does not say what would be lost. A destructive
    // one interrupts, as an alert dialog.
    const messageId = `modal-message-${++seq}`;
    modal.body.appendChild(h('p', { class: 'modal-message', id: messageId }, message ?? ''));
    modal.dialog.setAttribute('aria-describedby', messageId);
    if (critical || alert) modal.dialog.setAttribute('role', 'alertdialog');
    modal.footer.append(cancel, confirm);
    cancel.addEventListener('click', () => modal.close());
    confirm.addEventListener('click', () => { confirmed = true; modal.close(); });
    modal.onClosed(() => resolve(confirmed));
  });
}
