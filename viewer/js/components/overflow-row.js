// User intent: a row of controls never wraps — what does not fit moves, in order, behind one "More" button and comes
// back when there is room again, as the same live nodes, so chip rows and the topbar share one way of overflowing.

import { h } from '../util/h.js';
import { icon } from './icon.js';
import { openPopover } from './popover.js';

const RELEASE_MS = 500;   // after a pointerup with no click yet: a touch tap's click comes later
const MORE_PASSES = 3;    // layouts in a row while onLayout keeps widening More

// How many leading items stay: all when they fit with the gaps between them; otherwise the most that fit beside
// More, each followed by a gap. With `mins` (each item's least width), one more stays when it fits at its least width:
// it is the one that shrinks, so the row fills instead of ending in a gap narrower than the next item.
export function fitCount(widths, available, { gap = 0, moreWidth = 0, mins } = {}) {
  const n = widths.length;
  const all = widths.reduce((s, w) => s + w, 0) + gap * Math.max(0, n - 1);
  if (all <= available) return n;
  // The last item squeezed needs no More beside it.
  if (mins?.[n - 1] < widths[n - 1] && all - widths[n - 1] + mins[n - 1] <= available) return n;
  let used = moreWidth;
  let k = 0;
  while (k < n && used + widths[k] + gap <= available) used += widths[k++] + gap;
  return k < n && mins?.[k] < widths[k] && used + mins[k] + gap <= available ? k + 1 : k;
}

/**
 * A press elsewhere closed a popover and is still on its way to what it hit; nothing may move under it before its
 * click, however long it is held. `fn` runs on the task after the press lands: its click, `RELEASE_MS` after a
 * pointerup with no click (a drag; a touch tap's click comes later), a cancelled press, `RELEASE_MS` after a context
 * menu (which swallows the release), or the window losing focus. Aborting the returned controller drops the wait.
 */
export function waitForRelease(doc, view, fn) {
  const ac = new view.AbortController();
  const timers = [];
  const done = () => {
    if (ac.signal.aborted) return;
    ac.abort();
    view.setTimeout(fn, 0);
  };
  const later = () => timers.push(view.setTimeout(done, RELEASE_MS));
  ac.signal.addEventListener('abort', () => timers.forEach((t) => view.clearTimeout(t)));
  const opts = { capture: true, signal: ac.signal };
  doc.addEventListener('click', done, opts);
  doc.addEventListener('pointercancel', done, opts);
  doc.addEventListener('pointerup', later, { ...opts, once: true });
  doc.addEventListener('contextmenu', later, { ...opts, once: true });
  // The window's own blur only: an element's (focus moving to what was pressed) passes the window's capture phase.
  view.addEventListener('blur', (e) => { if (e.target === e.currentTarget) done(); }, { signal: ac.signal });
  return ac;
}

/**
 * Lays out `row` on one line: children that do not fit are parked, in order, in the list of a popover opened by the
 * `more` button appended to the row, and return to their places when room comes back. Parked children carry
 * `data-popover-item`; More's count is how many there are. `keep(el)` marks children that never move; their room is
 * taken first. A child with no width (empty, or `hidden`) needs no room: it stays in the row and is never counted.
 * Layout follows the row's width and children added to or removed from it; while the popover is open it waits until
 * the popover closes. Without ResizeObserver (jsdom) nothing moves and `more` stays hidden.
 *
 * What the measurement needs from the caller:
 * - The row's width comes from its container (it fills its slot, with `min-width: 0`), never from its content: a row
 *   sized by its children shrinks as they park and never grows back, so they never return.
 * - Children that may move are measured at their natural width (`flex-shrink: 0` while measured). Give them
 *   `flex-shrink: 0` in CSS too, or a visible one can still shrink beside a growing neighbour. A child that should flex
 *   (a search field) must be `keep`; it is measured unshrunk like the rest, so it keeps the room of its flex basis
 *   (or more when the row has room to spare), never just its min-width.
 * - A child whose width changes in place (a new count or label) needs `relayout()`; only the row's width and its list
 *   of children are observed.
 * - Parked children are out of the document while the popover is closed: keep references to them, or use `onLayout`.
 * - A row may carry `data-overflow-squeeze` (a CSS length): the first child that does not fit at its natural width stays when
 *   it fits at that width, marked `data-overflow-squeezed`; the row's CSS must let that one (and only it) shrink.
 * - Call `destroy()` when the row is unmounted; it disconnects the observers and the web-font listener.
 * - `counted: false` hides More's count, where a number would misread (beside "Filters" it reads as that many filters on).
 * The handle: `more`, `isOpen()` (the popover), `relayout()`, `reset()` (everything back in the row), `destroy()`.
 */
export function overflowRow(row, {
  moreLabel = 'More', moreIcon = null, popoverLabel = moreLabel, keep = () => false, onLayout, counted = true,
} = {}) {
  const doc = row.ownerDocument;
  const view = doc.defaultView;
  const count = h('span', { class: 'overflow-more__count' }, '0');
  count.hidden = !counted;
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
  const places = new Map();   // parked child → the comment holding its place in the row

  // The row's own children: not More, and not a popover one of them opened (it is inserted right after its anchor).
  const isChild = (n) => n.nodeType === 1 && n !== more && !n.classList.contains('popover');
  const children = () => [...row.children].filter(isChild);

  // Parked children go back to their places, and the place of one its caller removed meanwhile goes too; a child
  // added after More moves before it, so More stays last.
  function restore() {
    for (const [el, place] of places) {
      el.removeAttribute('data-popover-item');
      if (el.parentNode !== list) place.remove();
      else if (place.parentNode === row) place.replaceWith(el);
      else { place.remove(); row.insertBefore(el, more); }
    }
    places.clear();
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
    for (const el of items) el.removeAttribute('data-overflow-squeezed');
    const moving = items.filter((el) => !keep(el));
    const style = view.getComputedStyle(row);
    const gap = parseFloat(style.columnGap) || 0;
    const size = (el) => el.getBoundingClientRect().width;
    // Every child is measured at its natural width: in an overflowing row a shrinkable child, kept or moving, would read as already squeezed.
    const shrink = items.map((el) => el.style.flexShrink);
    for (const el of items) el.style.flexShrink = '0';
    const widths = moving.map(size);
    // A row with data-overflow-squeeze (a CSS length) keeps one more child when it fits at that width (see fitCount).
    const squeeze = row.dataset.overflowSqueeze?.trim();
    const maxes = squeeze ? moving.map((el) => el.style.maxWidth) : null;
    if (squeeze) moving.forEach((el) => { el.style.maxWidth = squeeze; });
    const mins = squeeze ? moving.map(size) : null;
    if (squeeze) moving.forEach((el, i) => { el.style.maxWidth = maxes[i]; });
    // Only a child with a width is parked: an empty one would be counted on More and leave a gap in its list.
    const sized = moving.filter((el, i) => widths[i] > 0);
    // An empty child still laid out (not display: none) takes a gap in the row.
    const empty = moving.filter((el, i) => !(widths[i] > 0) && el.getClientRects().length).length;
    more.hidden = false;
    count.textContent = String(sized.length);
    const moreWidth = size(more);
    const available = row.clientWidth - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0)
      - items.filter(keep).reduce((s, el) => s + size(el) + gap, 0) - gap * empty;
    items.forEach((el, i) => { el.style.flexShrink = shrink[i]; });
    const natural = widths.filter((w) => w > 0);
    const kept = fitCount(natural, available, { gap, moreWidth, mins: mins?.filter((w, i) => widths[i] > 0) });
    const hidden = sized.slice(kept);
    // The one kept below its natural width carries data-overflow-squeezed: the row's CSS lets it alone shrink.
    const need = natural.slice(0, kept).reduce((s, w) => s + w + gap, 0) - gap + (hidden.length ? gap + moreWidth : 0);
    if (kept && need > available) sized[kept - 1].setAttribute('data-overflow-squeezed', '');
    const focused = hidden.find((el) => el.contains(doc.activeElement));
    for (const el of hidden) {
      const place = doc.createComment('');
      el.replaceWith(place);
      places.set(el, place);
      el.setAttribute('data-popover-item', '');
      list.append(el);
    }
    more.hidden = !hidden.length;
    count.textContent = String(hidden.length);
    // Parked, the focused control left the page; the keyboard user goes on from More rather than from <body>.
    if (focused) more.focus({ preventScroll: true });
    mo.takeRecords();
    onLayout?.({ hidden });
    // onLayout may have widened More (a caller's "· 2 on"); another pass makes room for it, until More stops growing.
    if (pass + 1 < MORE_PASSES && !more.hidden && size(more) > moreWidth + 0.5) layout(pass + 1);
  }

  function closed(reason) {
    popover = null;
    if (!pending || destroyed) return;
    if (reason !== 'outside') { layout(); return; }
    waiting?.abort();
    const ac = waitForRelease(doc, view, () => { if (waiting === ac) waiting = null; layout(); });
    waiting = ac;
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
    for (const el of children()) el.removeAttribute('data-overflow-squeezed');
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
