// User intent: the Ideas screen filters by tag through one searchable "Tags" popover rather than a wall of chips;
// "UX" and "ux" are one tag, a chosen tag stays releasable even when the search or new data hides it, and the button
// says how many are chosen.
import { h } from '../util/h.js';
import { openPopover } from './popover.js';

export function tagKey(tag) {
  return String(tag ?? '').trim().toLowerCase();
}

/** Every item's `tags`, one entry per tag whatever its case: [{ key, label, count }], most used first. */
export function collectTags(items) {
  const byKey = new Map();
  for (const item of items ?? []) {
    const seen = new Map();   // key → the first spelling this item used
    for (const raw of Array.isArray(item?.tags) ? item.tags : []) {
      const key = tagKey(raw);
      if (key && !seen.has(key)) seen.set(key, String(raw).trim());
    }
    for (const [key, spelling] of seen) {
      let tag = byKey.get(key);
      if (!tag) byKey.set(key, tag = { key, count: 0, spellings: new Map() });
      tag.count += 1;
      tag.spellings.set(spelling, (tag.spellings.get(spelling) ?? 0) + 1);
    }
  }
  return [...byKey.values()].map(({ key, count, spellings }) => {
    let label = '';
    let most = 0;
    // A Map iterates in insertion order, so on a tie the first spelling seen stays.
    for (const [spelling, n] of spellings) if (n > most) { label = spelling; most = n; }
    return { key, label, count };
  }).sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, undefined, { sensitivity: 'base' }));
}

export function tagFilter({ label = 'Tags', getTags, selected = [], onChange }) {
  let chosen = [...new Set((selected ?? []).map(tagKey).filter(Boolean))];
  const labels = new Map();   // key → its last label shown, for a chosen key the data no longer carries
  let pop = null;
  let parts = null;           // the open popover's search, list, status and Clear, plus each key's option

  const el = h('button', { type: 'button', class: 'btn btn--secondary btn--sm tag-filter' }, h('span', {}, label));

  function paintButton() {
    const n = chosen.length;
    el.setAttribute('aria-label', n ? `${label}, ${n} selected` : label);
    let on = el.querySelector('.tag-filter__on');
    if (!n) { on?.remove(); return; }
    if (!on) el.append(on = h('span', { class: 'tag-filter__on' }));
    on.textContent = `· ${n}`;
  }

  // The data's tags, then any chosen key it no longer carries (count 0) so it can still be released.
  function choices() {
    const tags = getTags?.() ?? [];
    const keys = new Set(tags.map((t) => t.key));
    return [...tags, ...chosen.filter((k) => !keys.has(k)).map((key) => ({ key, label: labels.get(key) ?? key, count: 0 }))];
  }

  function optionFor(tag) {
    let opt = parts.options.get(tag.key);
    if (!opt) {
      const box = h('input', { type: 'checkbox', value: tag.key, 'data-popover-item': '' });
      box.addEventListener('change', () => toggle(tag.key, box.checked));
      opt = { el: h('label', { class: 'tag-filter__option' }, box, h('span', { class: 'tag-filter__name' }), h('span', { class: 'tag-filter__count', 'aria-hidden': 'true' })), box };
      parts.options.set(tag.key, opt);
    }
    const name = opt.el.querySelector('.tag-filter__name');
    name.textContent = tag.label;
    name.title = tag.label;
    opt.el.querySelector('.tag-filter__count').textContent = String(tag.count ?? 0);
    opt.box.checked = chosen.includes(tag.key);
    labels.set(tag.key, tag.label);
    return opt;
  }

  function applySearch() {
    const q = parts.search.value.trim();
    const needle = q.toLowerCase();
    let shown = 0;
    for (const opt of parts.options.values()) {
      const match = opt.el.querySelector('.tag-filter__name').textContent.toLowerCase().includes(needle);
      // Disabled as well as hidden, so the popover's arrow keys pass over it.
      opt.el.hidden = !match;
      opt.box.disabled = !match;
      if (match) shown += 1;
    }
    parts.none.textContent = shown ? '' : `No tag matches “${q}”.`;
  }

  function paintList() {
    const active = document.activeElement;
    const focusedKey = [...parts.options].find(([, o]) => o.box === active)?.[0];
    const tags = choices();
    const keep = new Set(tags.map((t) => t.key));
    for (const [key, opt] of parts.options) if (!keep.has(key)) { opt.el.remove(); parts.options.delete(key); }
    // Move only what is out of place: moving a focused node drops its focus.
    let next = null;
    for (const tag of [...tags].reverse()) {
      const opt = optionFor(tag);
      if (opt.el.nextElementSibling !== next || opt.el.parentNode !== parts.list) parts.list.insertBefore(opt.el, next);
      next = opt.el;
    }
    applySearch();
    parts.clear.hidden = chosen.length === 0;
    if (focusedKey === undefined || document.activeElement === active) return;
    const back = parts.options.get(focusedKey)?.box;
    (back && !back.disabled ? back : parts.search).focus({ preventScroll: true });
  }

  function changed() {
    paintButton();
    if (parts) {
      for (const [key, opt] of parts.options) opt.box.checked = chosen.includes(key);
      parts.clear.hidden = chosen.length === 0;
    }
    onChange?.([...chosen]);
  }

  function toggle(key, on) {
    chosen = chosen.filter((k) => k !== key);
    if (on) chosen.push(key);
    changed();
  }

  function clear() {
    const hadFocus = !!parts && document.activeElement === parts.clear;
    chosen = [];
    changed();
    // Clear hides itself; focus it held goes to the search box, not to <body>.
    if (hadFocus) parts.search.focus({ preventScroll: true });
  }

  function open() {
    const search = h('input', { type: 'search', class: 'tag-filter__search', 'aria-label': 'Find a tag', placeholder: 'Find a tag…' });
    const list = h('div', { class: 'tag-filter__list', role: 'group', 'aria-label': 'Tags' });
    const none = h('p', { class: 'tag-filter__none', role: 'status' });
    const clearBtn = h('button', { type: 'button', class: 'btn btn--ghost btn--sm tag-filter__clear' }, 'Clear tags');
    parts = { search, list, none, clear: clearBtn, options: new Map() };
    search.addEventListener('input', applySearch);
    search.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowDown' || e.altKey || e.ctrlKey || e.metaKey) return;
      const first = parts.list.querySelector('input:not(:disabled)');
      if (!first) return;
      e.preventDefault();
      first.focus();
    });
    clearBtn.addEventListener('click', clear);
    paintList();
    pop = openPopover({
      anchor: el, content: [search, list, none, clearBtn], role: 'dialog', label: 'Filter by tag', focus: 'none',
      className: 'tag-filter__popover', onClose: () => { pop = null; parts = null; },
    });
    // The popover places no focus; this one starts in the search box.
    search.focus({ preventScroll: true });
  }

  el.addEventListener('click', () => {
    if (pop) pop.close('api');
    else open();
  });
  paintButton();

  return {
    el,
    update() {
      paintButton();
      if (parts) {
        paintList();
        pop?.reposition();
      }
    },
    selected: () => [...chosen],
    clear,
  };
}
