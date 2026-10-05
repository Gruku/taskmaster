// User intent: a row of controls never wraps — what does not fit moves, in order, behind one "More" button and comes
// back when there is room again, as the same live nodes, so chip rows and the topbar share one way of overflowing.

import { h } from '../util/h.js';
import { icon } from './icon.js';
import { openPopover } from './popover.js';

const RELEASE_MS = 500;   // after a pointerup with no click yet: a touch tap's click comes later
const STUCK_MS = 3000;    // after the closing press, if its release never arrives

// How many leading items stay: all when they fit with the gaps between them; otherwise the most that fit beside
// More, each followed by a gap.
export function fitCount(widths, available, { gap = 0, moreWidth = 0 } = {}) {
  const n = widths.length;
  const all = widths.reduce((s, w) => s + w, 0) + gap * Math.max(0, n - 1);
  if (all <= available) return n;
  let used = moreWidth;
  let k = 0;
  while (k < n && used + widths[k] + gap <= available) used += widths[k++] + gap;
  return k;
}

/**
 * Lays out `row` on one line: children that do not fit are parked, in order, in the list of a popover opened by the
 * `more` button appended to the row, and return in their original order when room comes back. Parked children carry
 * `data-popover-item`. `keep(el)` marks children that never move; their room is taken first.
 * Layout follows the row's width and children added to or removed from it; while the popover is open it waits until
 * the popover closes. Without ResizeObserver (jsdom) nothing moves and `more` stays hidden.
 *
 * What the measurement needs from the caller:
 * - The row's width comes from its container (it fills its slot, with `min-width: 0`), never from its content: a row
 *   sized by its children shrinks as they park and never grows back, so they never return.
 * - Children that may move are measured at their natural width (`flex-shrink: 0` while measured). Give them
 *   `flex-shrink: 0` in CSS too, or a visible one can still shrink beside a growing neighbour. A child that should flex
 *   (a search field) must be `keep`; it is measured at whatever width the row leaves it.
 * - A child whose width changes in place (a new count or label) needs `relayout()`; only the row's width and its list
 *   of children are observed.
 * - Parked children are out of the document while the popover is closed: keep references to them, or use `onLayout`.
 * - Call `destroy()` when the row is unmounted; it disconnects the observers and the web-font listener.
 * The handle: `more`, `isOpen()` (the popover), `relayout()`, `reset()` (everything back in the row), `destroy()`.
 */
export function overflowRow(row, {
  moreLabel = 'More', moreIcon = null, popoverLabel = moreLabel, keep = () => false, onLayout,
} = {}) {
  const doc = row.ownerDocument;
  const view = doc.defaultView;
  const count = h('span', { class: 'overflow-more__count' }, '0');
  const more = h('button', { type: 'button', class: 'btn btn--ghost btn--sm overflow-more' },
    moreIcon ? icon(moreIcon, { size: 16 }) : null, h('span', { class: 'overflow-more__label' }, moreLabel), count);
  more.hidden = true;
  const list = h('div', { class: 'overflow-list' });
  row.append(more);

  let popover = null;
  let pending = false;
  let destroyed = false;
  let width = null;
  let waiting = null;   // AbortController of a wait for the press that closed the popover to finish

  // The row's own children: not More, and not a popover one of them opened (it is inserted right after its anchor).
  const isChild = (n) => n.nodeType === 1 && n !== more && !n.classList.contains('popover');
  const children = () => [...row.children].filter(isChild);

  // Parked children go back before More; a child added after More joins them there, so More stays last.
  function restore() {
    for (const el of [...list.children]) {
      el.removeAttribute('data-popover-item');
      row.insertBefore(el, more);
    }
    for (const el of children()) if (more.compareDocumentPosition(el) & view.Node.DOCUMENT_POSITION_FOLLOWING) row.insertBefore(el, more);
  }

  function layout(pass = 0) {
    if (destroyed || !ro) return;
    if (popover) { pending = true; return; }
    pending = false;
    // Hidden (display: none) there is nothing to measure; the row's next resize lays it out.
    if (!row.getClientRects().length) return;
    mo.takeRecords();
    restore();
    const items = children();
    const moving = items.filter((el) => !keep(el));
    const style = view.getComputedStyle(row);
    const gap = parseFloat(style.columnGap) || 0;
    const size = (el) => el.getBoundingClientRect().width;
    more.hidden = false;
    count.textContent = String(moving.length);
    // Measured at their natural width: in an overflowing row a shrinkable child would read as already squeezed.
    const shrink = moving.map((el) => el.style.flexShrink);
    for (const el of moving) el.style.flexShrink = '0';
    const widths = moving.map(size);
    const moreWidth = size(more);
    const available = row.clientWidth - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0)
      - items.filter(keep).reduce((s, el) => s + size(el) + gap, 0);
    moving.forEach((el, i) => { el.style.flexShrink = shrink[i]; });
    const hidden = moving.slice(fitCount(widths, available, { gap, moreWidth }));
    const focused = hidden.find((el) => el.contains(doc.activeElement));
    for (const el of hidden) {
      el.setAttribute('data-popover-item', '');
      list.append(el);
    }
    more.hidden = !hidden.length;
    count.textContent = String(hidden.length);
    // Parked, the focused control left the page; the keyboard user goes on from More rather than from <body>.
    if (focused) more.focus({ preventScroll: true });
    mo.takeRecords();
    onLayout?.({ hidden });
    // onLayout may have widened More (a caller's "· 2 on"); one more pass makes room for it.
    if (!pass && !more.hidden && size(more) > moreWidth + 0.5) layout(1);
  }

  function closed(reason) {
    popover = null;
    if (!pending || destroyed) return;
    if (reason === 'outside') afterPress(() => layout());
    else layout();
  }

  // A press elsewhere closed the popover and is still on its way to what it hit; nothing may move under it before
  // its click. A touch tap's click comes after its pointerup, so the release waits for the click itself, or for a
  // while after a pointerup with no click (a drag), or a cancelled press; a release that never comes (swallowed by a
  // context menu) is let go after a longer while.
  function afterPress(fn) {
    waiting?.abort();
    const ac = new view.AbortController();
    waiting = ac;
    const timers = [];
    const done = () => {
      if (ac.signal.aborted) return;
      ac.abort();
      if (waiting === ac) waiting = null;
      view.setTimeout(fn, 0);
    };
    const later = (ms) => timers.push(view.setTimeout(done, ms));
    ac.signal.addEventListener('abort', () => timers.forEach((t) => view.clearTimeout(t)));
    const opts = { capture: true, signal: ac.signal };
    doc.addEventListener('click', done, opts);
    doc.addEventListener('pointercancel', done, opts);
    doc.addEventListener('pointerup', () => later(RELEASE_MS), { ...opts, once: true });
    later(STUCK_MS);
  }

  more.addEventListener('click', () => {
    if (popover) { popover.close('toggle', { returnFocus: true }); return; }
    popover = openPopover({ anchor: more, content: list, role: 'dialog', label: popoverLabel, focus: 'first', onClose: closed });
  });

  const ro = view.ResizeObserver ? new view.ResizeObserver((entries) => {
    const w = entries.at(-1).contentRect.width;
    if (w === width) return;
    width = w;
    layout();
  }) : null;
  const mo = ro ? new view.MutationObserver((records) => {
    if (records.some((r) => [...r.addedNodes, ...r.removedNodes].some(isChild))) layout();
  }) : null;
  ro?.observe(row);
  mo?.observe(row, { childList: true });
  // A web font arriving after the first layout changes every child's width and nothing else would notice.
  const onFonts = () => layout();
  if (ro) doc.fonts?.addEventListener('loadingdone', onFonts);

  function reset() {
    pending = false;
    waiting?.abort();
    waiting = null;
    popover?.close('reset');
    restore();
    more.hidden = true;
    count.textContent = '0';
    mo?.takeRecords();
  }

  return {
    more,
    isOpen: () => !!popover,
    relayout: () => layout(),
    reset,
    destroy() {
      reset();
      destroyed = true;
      ro?.disconnect();
      mo?.disconnect();
      doc.fonts?.removeEventListener('loadingdone', onFonts);
    },
  };
}
