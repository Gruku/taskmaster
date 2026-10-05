// User intent: one way to open anything that floats over the page (menus, suggestion lists, "More", "Filters") —
// placed right after its anchor, dismissed by Escape, an outside press, focus loss or a redraw of its anchor,
// and never eating the press that dismissed it.

import { h } from '../util/h.js';

const ITEMS = '[role="menuitem"], [role="menuitemradio"], [role="menuitemcheckbox"], [role="option"], [data-popover-item]';
const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';
const GAP = 4;

const open = new Set();   // handles of the popovers open now
let seq = 0;

export function openPopoverCount() {
  return open.size;
}

const usable = (el) => !el.disabled && el.getAttribute('aria-disabled') !== 'true';
const isOn = (el) => ['aria-checked', 'aria-selected', 'aria-pressed'].some((a) => el.getAttribute(a) === 'true');

export function openPopover({
  anchor, content, role = 'dialog', label, labelledBy, focus = 'first', className = '', minWidth = 'none', onClose,
}) {
  const doc = anchor.ownerDocument;
  const view = doc.defaultView;
  // One floats at a time, except one opened from inside another.
  for (const p of [...open]) if (!p.el.contains(anchor)) p.close('replaced');

  const el = h('div', {
    id: `popover-${++seq}`, class: `popover ${className}`.trim(), style: 'position: fixed',
    role, 'aria-label': label, 'aria-labelledby': labelledBy,
  }, content);
  // Items of this popover, not of one opened from inside it.
  const items = () => [...el.querySelectorAll(ITEMS)].filter((i) => usable(i) && i.closest('.popover') === el);
  const ac = new view.AbortController();
  const on = (target, type, fn, capture = false) => target.addEventListener(type, fn, { capture, signal: ac.signal });
  const outside = (node) => !(node && (el.contains(node) || anchor.contains(node)));
  let observer = null;

  const handle = {
    el,
    close(reason = 'api', { returnFocus = false } = {}) {
      if (!open.has(handle)) return;
      open.delete(handle);
      ac.abort();
      observer?.disconnect();
      const hadFocus = el.contains(doc.activeElement);
      el.remove();
      anchor.setAttribute('aria-expanded', 'false');
      anchor.removeAttribute('aria-controls');
      // Focus lost to a press elsewhere or to a redraw is the user's or the page's; it is not pulled back.
      // Nor is the page: after a scroll or an outside press the anchor may have moved away, and following it with
      // the viewport would move what the user is pressing out from under the pointer.
      const back = returnFocus || (hadFocus && reason !== 'focusout' && reason !== 'detached');
      if (back && anchor.isConnected) anchor.focus({ preventScroll: true });
      onClose?.(reason);
    },
    isOpen: () => open.has(handle),
    reposition: () => placePopover(el, anchor, { minWidth }),
  };

  function onEscape(e) {
    if (e.key !== 'Escape' || e.defaultPrevented) return;
    // The key is used up here: a modal around the popover must not also close.
    e.preventDefault();
    e.stopPropagation();
    handle.close('escape', { returnFocus: true });
  }
  on(el, 'keydown', onEscape);
  on(anchor, 'keydown', onEscape);
  on(el, 'keydown', (e) => {
    // A key already used, or a modified arrow (the browser's or the system's), is not the list's.
    if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
    const list = items();
    const at = list.indexOf(doc.activeElement);
    if (at < 0) return;   // a text input in a dialog popover keeps its arrows
    const step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
    const to = step ? list[(at + step + list.length) % list.length]
      : e.key === 'Home' ? list[0] : e.key === 'End' ? list.at(-1) : null;
    if (!to) return;
    e.preventDefault();
    to.focus();
  });
  // A press on the anchor is left to the anchor's own click; nothing here stops a press from reaching its target.
  on(doc, 'pointerdown', (e) => { if (outside(e.target)) handle.close('outside'); }, true);
  const onFocusOut = (e) => { if (e.relatedTarget && outside(e.relatedTarget)) handle.close('focusout'); };
  on(el, 'focusout', onFocusOut);
  on(anchor, 'focusout', onFocusOut);
  on(view, 'resize', () => handle.reposition());
  // A dialog still rising when this opened was its containing block, transformed and moving the anchor with it;
  // once its animation ends the popover is placed again, or it would stand off by the dialog's offset.
  const settled = (e) => { if (e.target !== el && e.target.contains?.(el)) handle.reposition(); };
  on(doc, 'animationend', settled, true);
  on(doc, 'animationcancel', settled, true);
  // A scroll already under way when it opened (the one that brought the anchor into view) is dispatched at the next
  // frame's scroll step, before animation-frame callbacks; listening from that frame on lets it pass.
  const armScroll = () => on(doc, 'scroll', (e) => { if (!el.contains(e.target)) handle.close('scroll'); }, true);
  if (view.requestAnimationFrame) view.requestAnimationFrame(armScroll);
  else armScroll();

  anchor.after(el);
  if (anchor.getAttribute('role') !== 'combobox') anchor.setAttribute('aria-haspopup', role);
  anchor.setAttribute('aria-expanded', 'true');
  anchor.setAttribute('aria-controls', el.id);
  open.add(handle);
  handle.reposition();
  // A redraw that takes the anchor away takes the popover with it.
  observer = new view.MutationObserver(() => {
    if (!anchor.isConnected || !el.isConnected) handle.close('detached');
  });
  observer.observe(doc.body, { childList: true, subtree: true });

  if (focus !== 'none') {
    const list = items();
    // The first of these that takes focus: an item that is no control itself (a parked count) passes to the next.
    // Placed inside the viewport already; scrolling to it would read as a scroll away and close it.
    const target = [focus === 'checked' && list.find(isOn), list[0], ...[...el.querySelectorAll(FOCUSABLE)].filter(usable)]
      .filter(Boolean).find((n) => { n.focus({ preventScroll: true }); return doc.activeElement === n; });
    // Within a list that scrolls inside itself, though, a checked item far down is brought into sight.
    target?.scrollIntoView?.({ block: 'nearest' });
  }
  return handle;
}

// Fixed, so no scrolling panel clips it; measured against wherever its containing block puts the origin
// (a frosted overlay or a transformed panel is one), and flipped above the anchor when there is no room below.
export function placePopover(el, anchor, { minWidth = 'none' } = {}) {
  const view = el.ownerDocument.defaultView;
  // Out of the flow before anything is measured: in it, the popover would stretch a centred row and move the anchor.
  el.style.position = 'fixed';
  el.style.left = '0px';
  el.style.top = '0px';
  const at = anchor.getBoundingClientRect();
  if (minWidth === 'anchor') el.style.minWidth = `${at.width}px`;
  const origin = el.getBoundingClientRect();
  const height = el.offsetHeight || 0;
  const width = el.offsetWidth || 0;
  const below = at.bottom + GAP;
  const fits = below + height <= view.innerHeight;
  const top = fits ? below : Math.max(GAP, at.top - GAP - height);
  const left = Math.max(GAP, Math.min(at.left, view.innerWidth - width - GAP));
  el.style.left = `${left - origin.left}px`;
  el.style.top = `${top - origin.top}px`;
}
