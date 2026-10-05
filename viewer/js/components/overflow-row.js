// User intent: a row of controls never wraps — what does not fit moves, in order, behind one "More" button and comes
// back when there is room again, as the same live nodes, so chip rows and the topbar share one way of overflowing.

import { h } from '../util/h.js';
import { icon } from './icon.js';
import { openPopover } from './popover.js';

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

  function layout() {
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
    const available = row.clientWidth - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0)
      - items.filter(keep).reduce((s, el) => s + size(el) + gap, 0);
    const hidden = moving.slice(fitCount(moving.map(size), available, { gap, moreWidth: size(more) }));
    for (const el of hidden) {
      el.setAttribute('data-popover-item', '');
      list.append(el);
    }
    more.hidden = !hidden.length;
    count.textContent = String(hidden.length);
    mo.takeRecords();
    onLayout?.({ hidden });
  }

  function closed(reason) {
    popover = null;
    if (!pending || destroyed) return;
    if (reason !== 'outside') { layout(); return; }
    // A press elsewhere closed it and is still on its way to what it hit; nothing may move under it before its click.
    waiting = new view.AbortController();
    const done = () => {
      waiting?.abort();
      waiting = null;
      view.setTimeout(layout, 0);
    };
    doc.addEventListener('pointerup', done, { capture: true, signal: waiting.signal });
    doc.addEventListener('pointercancel', done, { capture: true, signal: waiting.signal });
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
    relayout: layout,
    reset,
    destroy() {
      reset();
      destroyed = true;
      ro?.disconnect();
      mo?.disconnect();
    },
  };
}
