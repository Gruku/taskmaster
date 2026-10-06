// User intent: on a phone a board shows one column at a time behind tabs with counts that key like a real tablist;
// each tab's id and button stay stable across repaints, so screens can label panels by id and keep focus on a tab.
import { h } from '../util/h.js';

const MOVES = {
  ArrowRight: (i, n) => (i + 1) % n,
  ArrowLeft: (i, n) => (i - 1 + n) % n,
  Home: () => 0,
  End: (i, n) => n - 1,
};

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
    const keep = new Set(cols.map((c) => c.key));
    for (const [key, btn] of byKey) {
      if (!keep.has(key)) { btn.remove(); byKey.delete(key); }
    }
    // Move only what is out of place, and refocus a moved button: moving a focused node in the DOM drops its focus.
    const focused = el.contains(document.activeElement) ? document.activeElement : null;
    let next = null;
    for (const col of [...cols].reverse()) {
      const btn = tabFor(col);
      if (btn.nextElementSibling !== next || btn.parentNode !== el) el.insertBefore(btn, next);
      next = btn;
    }
    if (focused?.isConnected && document.activeElement !== focused) focused.focus();
    el.hidden = cols.length < 2;
    paintSelection();
  }

  el.addEventListener('keydown', (ev) => {
    const move = MOVES[ev.key];
    const btn = ev.target.closest?.('.column-tabs__tab');
    if (!move || !btn) return;
    ev.preventDefault();
    const all = [...el.children];
    choose(all[move(all.indexOf(btn), all.length)].dataset.key, true);
  });

  paint(columns);

  return {
    el,
    update({ columns: cols = columns, selected: sel = current } = {}) {
      columns = cols;
      current = sel;
      paint(cols);
      byKey.get(current)?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
    },
  };
}
