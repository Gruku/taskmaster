// User intent: the Bugs list — every bug reachable through a chip per status (fixed and adopted ones too), archived bugs
// behind their own toggle, a labelled Sort, rows that are real links, and its loading, failure and empty states in words.
import { bugRow } from '../components/bug-card.js';
import { stateBlock } from '../components/empty-state.js';
import { chipRow, filterChip } from '../components/chips.js';
import { filterRail, labelled } from '../components/list-toolbar.js';
import { icon } from '../components/icon.js';
import * as api from '../api.js';
import { claimTopbar, setTopbarCount, tmSearch } from '../lib/topbar.js';
import { keepFocus } from '../lib/keep-focus.js';
import { chipClickNext, CHIP_CLICK_HINT } from '../util/chip-toggle.js';
import { pluralize } from '../util/pluralize.js';
import { h } from '../util/h.js';
import { BUG_SORTS, bugPrefs, bugStatusChips, filterBugs, isArchivedBug, sortBugs } from '../util/bugs-filter.js';

export const meta = { title: 'Bugs', icon: '⊘', sidebarKey: 'bugs' };

export function mount(root, { store, prefs }) {
  // `prefs` is the patch helper; what was saved is read from the store.
  const state = bugPrefs(store.getPrefs()?.screens?.bugs);
  let search = '';
  let bugs = null;       // null until the list has loaded
  let failed = false;
  let alive = true;

  root.replaceChildren();
  const screen = h('section', { class: 'bugs' });

  // ── Row 2: search and the one view control ──
  const topbar = claimTopbar();
  const searchBuilt = tmSearch({ placeholder: 'Search bugs…', onInput: (v) => { search = v; paint(); } });
  const searchInput = searchBuilt.input;
  const sort = h('select', { class: 'ef-enum-select', id: 'bugs-sort' },
    BUG_SORTS.map((o) => h('option', { value: o.value }, o.label)));
  sort.value = state.sort;
  sort.addEventListener('change', () => { state.sort = sort.value; persist(); paint(); });
  topbar?.append(searchBuilt.el, labelled({ label: 'Sort', control: h('span', { class: 'ef-select' }, sort, icon('chevron', { size: 16 })) }));

  // ── The filter rail: status chips, Show archived, Clear ──
  const rail = filterRail({ onClear: clear });
  const statusRow = chipRow({
    label: 'Status', chips: bugStatusChips([], state.statuses), hint: CHIP_CLICK_HINT,
    onToggle: (value, ev) => { state.statuses = chipClickNext(ev, state.statuses, value); persist(); paint(); },
  });
  const archivedChip = filterChip({
    label: 'Show archived', value: 'archived', pressed: state.archived, count: 0,
    onToggle: () => { state.archived = !state.archived; persist(); paint(); },
  });
  rail.add(statusRow.el, archivedChip);

  const list = h('ul', { class: 'bugs__list', 'aria-label': 'Bugs' });
  const stateHost = h('div', { class: 'bugs__state' });
  screen.append(rail.el, list, stateHost);
  root.append(screen);

  function persist() {
    prefs?.patch?.({ screens: { bugs: { statuses: [...state.statuses], archived: state.archived, sort: state.sort } } });
  }

  function clear() {
    state.statuses = [];
    state.archived = false;
    search = '';
    searchInput.value = '';
    searchInput.dispatchEvent(new Event('input', { bubbles: true }));
    persist();
    paint();
  }

  // The chip's own paint is private to chips.js; these are the parts a new count or toggle changes.
  function paintArchivedChip(count) {
    archivedChip.setAttribute('aria-pressed', String(state.archived));
    archivedChip.disabled = count === 0 && !state.archived;
    archivedChip.querySelector('.chip__count').textContent = String(count);
  }

  function showState(block) {
    list.replaceChildren();
    list.hidden = true;
    stateHost.replaceChildren(block);
  }

  function paint() {
    // A debounced search (or Clear's own input event) can fire after the screen is gone; the topbar is the next screen's.
    if (!alive) return;
    // Focus in the list, the state block or the rail is put back, or handed on, never dropped to <body>.
    const spare = () => list.querySelector('a[href]') ?? stateHost.querySelector('button') ?? searchInput;
    const restore = [keepFocus(list, { fallback: spare }), keepFocus(stateHost, { fallback: spare }),
      keepFocus(rail.el, { fallback: searchInput })];
    draw();
    for (const back of restore) back();
  }

  function draw() {
    const narrowed = state.statuses.length > 0 || state.archived || search.trim() !== '';
    rail.setClearable(narrowed);
    if (!bugs) {
      setTopbarCount('');
      statusRow.update(bugStatusChips([], state.statuses));
      paintArchivedChip(0);
      showState(failed
        ? stateBlock({ state: 'error', label: 'Bugs', headline: 'Could not load bugs.', action: { label: 'Try again', onClick: load } })
        : stateBlock({ state: 'loading', busy: true, headline: 'Loading bugs…' }));
      return;
    }

    const admitted = bugs.filter((b) => state.archived || !isArchivedBug(b));
    const shown = sortBugs(filterBugs(bugs, { ...state, search }), state.sort);
    setTopbarCount(`${admitted.length} ${pluralize(admitted.length, 'bug', 'bugs')}${shown.length < admitted.length ? ` · ${shown.length} visible` : ''}`);
    statusRow.update(bugStatusChips(admitted, state.statuses));
    paintArchivedChip(bugs.filter(isArchivedBug).length);

    if (!bugs.length) return showState(stateBlock({ label: 'Bugs', headline: 'No bugs recorded yet.' }));
    if (!shown.length) {
      return showState(stateBlock({ label: 'No matches', headline: 'No bugs match these filters.', action: { label: 'Clear filters', onClick: clear } }));
    }
    const now = Date.now();
    stateHost.replaceChildren();
    list.hidden = false;
    list.replaceChildren(...shown.map((b) => bugRow(b, { now })));
  }

  // One request for every bug, archived ones flagged; a response that arrives after the screen is gone is dropped.
  function load() {
    failed = false;
    bugs = null;
    paint();
    api.listBugs({ include_archive: true }).then((data) => {
      if (!alive) return;
      bugs = Array.isArray(data) ? data : Array.isArray(data?.bugs) ? data.bugs : [];
      paint();
    }, (e) => {
      if (!alive) return;
      console.error('bugs load failed', e);
      failed = true;
      paint();
    });
  }

  load();

  return () => {
    alive = false;
    statusRow.destroy();
  };
}
