// User intent: on a phone a board shows one column at a time behind tabs with counts that key like a real tablist;
// each tab's id and button stay stable across repaints, so screens can label panels by id and keep focus on a tab.
import { h } from '../util/h.js';

const MOVES = {
  ArrowRight: (i, n) => (i + 1) % n,
  ArrowLeft: (i, n) => (i - 1 + n) % n,
  Home: () => 0,
  End: (i, n) => n - 1,
};

/**
 * The phone column switcher: a tablist with one tab per column, shown at 768px and below with two or more columns.
 * @param {{ label: string, columns: Array<{ key: string, label: string, count: number, panelId: string }>,
 *   selected: string, onSelect: (key: string) => void }} opts
 * @returns {{ el: HTMLElement, update: (next: { columns?: object[], selected?: string }) => void, destroy: () => void }}
 *
 * While the strip scrolls sideways it carries `column-tabs--more-start` / `--more-end` on the side(s) with tabs
 * past the edge (kept current on scroll and resize); destroy() drops those listeners and classes.
 *
 * Tab ids are always `${panelId}-tab`, and update() repaints each key's existing button in place (focus stays on it).
 *
 * Panel contract — the component never touches panels; every consumer does this itself:
 * - each column's panel has `id = panelId`;
 * - while `matchMedia('(max-width: 768px)')` matches and `el` is not hidden (two or more columns), every panel has
 *   `role="tabpanel"` and `aria-labelledby="${panelId}-tab"`, and every panel but the selected one is `hidden`;
 * - otherwise panels carry neither (no tab role, their own label) and none is hidden;
 * - the consumer owns the `matchMedia` change listener (a change repaints) and removes it on cleanup.
 */
export function columnTabs({ label, columns = [], selected, onSelect }) {
  const el = h('div', { class: 'column-tabs', role: 'tablist', 'aria-label': label });
  const byKey = new Map();
  let current = selected;

  function paintSelection() {
    for (const [key, btn] of byKey) {
      const on = key === current;
      btn.setAttribute('aria-selected', String(on));
      btn.setAttribute('tabindex', on ? '0' : '-1');
    }
    // A selected key with no tab must not drop the list out of the Tab order: the first tab takes the stop.
    if (!byKey.has(current)) el.firstElementChild?.setAttribute('tabindex', '0');
  }

  function choose(key, focus) {
    const btn = byKey.get(key);
    if (!btn) return;
    current = key;
    paintSelection();
    if (focus) btn.focus();
    onSelect?.(key);
  }

  function tabFor(col) {
    let btn = byKey.get(col.key);
    if (!btn) {
      btn = h('button', { type: 'button', class: 'column-tabs__tab', role: 'tab' },
        h('span', { class: 'column-tabs__label' }), h('span', { class: 'column-tabs__count' }));
      btn.addEventListener('click', () => choose(btn.dataset.key, false));
      byKey.set(col.key, btn);
    }
    btn.id = `${col.panelId}-tab`;
    btn.setAttribute('aria-controls', col.panelId);
    btn.dataset.key = col.key;
    btn.querySelector('.column-tabs__label').textContent = col.label;
    btn.querySelector('.column-tabs__count').textContent = String(col.count ?? 0);
    return btn;
  }

  function paint(cols) {
    const focused = el.contains(document.activeElement) ? document.activeElement : null;
    const keep = new Set(cols.map((c) => c.key));
    for (const [key, btn] of byKey) {
      if (!keep.has(key)) { btn.remove(); byKey.delete(key); }
    }
    // Move only what is out of place: moving a focused node in the DOM drops its focus.
    let next = null;
    for (const col of [...cols].reverse()) {
      const btn = tabFor(col);
      if (btn.nextElementSibling !== next || btn.parentNode !== el) el.insertBefore(btn, next);
      next = btn;
    }
    el.hidden = cols.length < 2;
    paintSelection();
    // Focus that was on a tab stays in the list: on the same button if it was moved, on the selected one (or the
    // first) if its tab is gone — never dropped to <body>.
    if (!focused || document.activeElement === focused) return;
    (el.contains(focused) ? focused : byKey.get(current) ?? el.firstElementChild)?.focus();
  }

  // Slide the list itself so the selected tab shows. Not scrollIntoView: that also scrolls every scrolling ancestor,
  // and a poll repaint would pull a page the user has scrolled down back up to the tab strip.
  function revealSelected() {
    const btn = byKey.get(current);
    if (!btn || el.hidden) return;
    const box = el.getBoundingClientRect();
    const tab = btn.getBoundingClientRect();
    const left = box.left + el.clientLeft;
    const right = left + el.clientWidth;
    if (tab.left < left) el.scrollLeft += tab.left - left;
    else if (tab.right > right) el.scrollLeft += tab.right - right;
  }

  el.addEventListener('keydown', (ev) => {
    const move = MOVES[ev.key];
    const btn = ev.target.closest?.('.column-tabs__tab');
    if (!move || !btn) return;
    ev.preventDefault();
    const all = [...el.children];
    choose(all[move(all.indexOf(btn), all.length)].dataset.key, true);
  });

  // A strip that scrolls sideways says so: a fade on each side that still has tabs past the edge.
  function paintCue() {
    const max = el.scrollWidth - el.clientWidth;
    const at = Math.abs(el.scrollLeft);
    el.classList.toggle('column-tabs--more-start', !el.hidden && max > 1 && at > 1);
    el.classList.toggle('column-tabs--more-end', !el.hidden && max > 1 && at < max - 1);
  }
  el.addEventListener('scroll', paintCue, { passive: true });
  const resize = typeof ResizeObserver === 'function' ? new ResizeObserver(paintCue) : null;
  resize?.observe(el);

  paint(columns);
  paintCue();

  return {
    el,
    update({ columns: cols = columns, selected: sel = current } = {}) {
      columns = cols;
      current = sel;
      paint(cols);
      revealSelected();
      paintCue();
    },
    destroy() {
      el.removeEventListener('scroll', paintCue);
      resize?.disconnect();
      el.classList.remove('column-tabs--more-start', 'column-tabs--more-end');
    },
  };
}
