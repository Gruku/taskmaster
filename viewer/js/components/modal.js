// User intent: one modal shell for the whole viewer — every dialog gets the same frame and the same guarantees
// (labelled, focus kept inside the topmost one, page inert behind it, focus handed back on close), and
// confirmations are asked in-app instead of through the browser's native dialogs.
import { h } from '../util/h.js';
import { icon } from './icon.js';

const SIZES = ['sm', 'md', 'lg'];
const stack = [];          // open modals, bottom → top
let seq = 0;
let shellWasInert = false; // .shell's own inert state before the first modal opened

const CAN_FOCUS = 'a[href], button, input, select, textarea, summary, [tabindex], [contenteditable=""], [contenteditable="true"]';

function canTakeFocus(el) {
  if (!el || el.nodeType !== 1 || !el.isConnected || !el.matches(CAN_FOCUS)) return false;
  if (el.disabled || el.type === 'hidden' || el.closest('[inert], [hidden]')) return false;
  // jsdom has no layout; a browser also rules out display:none and visibility:hidden.
  return typeof el.checkVisibility === 'function' ? el.checkVisibility({ visibilityProperty: true }) : true;
}

// What Tab can reach inside `root`, in document order.
export function focusableIn(root) {
  if (!root) return [];
  return [...root.querySelectorAll(CAN_FOCUS)].filter((el) => el.tabIndex >= 0 && canTakeFocus(el));
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
function onTab(e) {
  const m = top();
  if (!m || e.key !== 'Tab') return;
  const items = focusableIn(m.dialog);
  const active = document.activeElement;
  const first = items[0] ?? m.dialog;
  const last = items.at(-1) ?? m.dialog;
  let to = null;
  if (!m.dialog.contains(active) || !items.length) to = e.shiftKey ? last : first;
  else if (e.shiftKey && (active === first || active === m.dialog)) to = last;
  else if (!e.shiftKey && active === last) to = first;
  if (to) { e.preventDefault(); to.focus(); }
}

// Escape reaches the modal after the control it was typed in: one that used the key for itself
// (and said so with preventDefault) keeps the modal open.
function isEscape(e) {
  return e.key === 'Escape' && !e.defaultPrevented && !e.isComposing;
}

// Escape typed while focus is outside every modal (it was lost with a removed element) still closes the top one.
function onStrayEscape(e) {
  if (!isEscape(e) || e.target?.closest?.('.modal-overlay')) return;
  e.preventDefault();
  top()?.requestClose();
}

export function openModal({ title, eyebrow, size = 'md', className, onRequestClose, opener, initialFocus } = {}) {
  const id = `modal-title-${++seq}`;
  const eyebrowEl = h('div', { class: 'modal-eyebrow' });
  const titleEl = h('h2', { class: 'modal-title', id });
  const actions = h('div', { class: 'modal-actions' });
  const closeBtn = h('button', { type: 'button', class: 'modal-close', 'aria-label': 'Close' }, icon('dismiss'));
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
  // Kept from open time: once the opener is removed its parent chain is gone.
  const ancestors = [];
  for (let p = from?.parentElement; p && p !== document.body; p = p.parentElement) ancestors.push(p);

  let closed = false;
  let pending = null;       // the close request being decided, if any
  const closedFns = [];

  function setTitle(value) {
    titleEl.replaceChildren(value == null ? '' : value);
  }
  function setEyebrow(text) {
    eyebrowEl.textContent = text ?? '';
    eyebrowEl.hidden = !eyebrowEl.textContent;
  }
  function run(fn) {
    try { fn(); } catch (err) { console.error('modal onClosed callback failed', err); }
  }

  function close() {
    if (closed) return;
    closed = true;
    const wasTop = top() === handle;
    stack.splice(stack.indexOf(handle), 1);
    overlay.remove();
    if (!stack.length) {
      document.removeEventListener('keydown', onTab, true);
      document.removeEventListener('keydown', onStrayEscape);
    }
    syncLayers();
    // Focus moves only after the layers are released, or the target would still be inert.
    // A covered modal that closes leaves focus where it is, in the one on top.
    for (const target of wasTop ? focusTargets(from, { ancestors, within: top()?.dialog }) : []) {
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
  };

  setTitle(title);
  setEyebrow(eyebrow);
  closeBtn.addEventListener('click', () => { requestClose(); });

  // A dismissal is a press and a release that both land on the overlay itself; a drag that starts
  // in the dialog (selecting text, a scrollbar) and ends outside is not one.
  let downOnOverlay = false;
  let upOnOverlay = false;
  overlay.addEventListener('pointerdown', (e) => { downOnOverlay = e.target === overlay && !e.button; upOnOverlay = false; });
  overlay.addEventListener('pointerup', (e) => { upOnOverlay = e.target === overlay; });
  overlay.addEventListener('click', (e) => {
    const dismiss = downOnOverlay && upOnOverlay && e.target === overlay;
    downOnOverlay = upOnOverlay = false;
    if (dismiss) requestClose();
  });

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

export function confirmDialog({ title, message, confirmLabel = 'Confirm', cancelLabel = 'Cancel', tone = 'default' } = {}) {
  return new Promise((resolve) => {
    const critical = tone === 'critical';
    let confirmed = false;
    const cancel = h('button', { type: 'button', class: 'btn btn--secondary', 'data-cancel': '' }, cancelLabel);
    const confirm = h('button', { type: 'button', class: `btn ${critical ? 'btn--critical' : 'btn--primary'}`, 'data-confirm': '' }, confirmLabel);
    // A destructive answer is never one stray Enter away: critical confirms start on Cancel.
    const modal = openModal({ title, size: 'sm', className: 'modal--confirm', initialFocus: () => (critical ? cancel : confirm) });
    modal.body.appendChild(h('p', { class: 'modal-message' }, message ?? ''));
    modal.footer.append(cancel, confirm);
    cancel.addEventListener('click', () => modal.close());
    confirm.addEventListener('click', () => { confirmed = true; modal.close(); });
    modal.onClosed(() => resolve(confirmed));
  });
}
