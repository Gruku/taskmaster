// Table view — Obsidian-Bases-style alternative to Kanban.
// Reads /api/backlog (via store), renders a sortable+filterable table.
// Persisted state lives under prefs.table.

import { claimTopbar, claimTopbarPrimary, setTopbarCount, tmSearch, tmAction } from '../lib/topbar.js';
import { pluralize } from '../util/pluralize.js';
import { formatAbsolute } from '../lib/time.js';
import { stateBlock } from '../components/empty-state.js';
import { openTaskCreateModal } from '../components/edit/task-actions.js';
import { chipClickNext, CHIP_CLICK_HINT } from '../util/chip-toggle.js';
import { chipRow } from '../components/chips.js';
import { sortHeader } from '../components/sort-header.js';
import { icon } from '../components/icon.js';
import { TASK_STATUS, PRIORITY, statusMarker, priorityMarker } from '../components/status.js';
import { epicIndex } from '../lib/epics.js';
import { truncate } from '../lib/text.js';
import { h } from '../util/h.js';

export const meta = { title: 'Table', icon: '▭', sidebarKey: 'table' };

const none = () => h('span', { class: 't-none' }, '—');
const tech = (v) => (v ? truncate(v, { className: 't-tech' }) : none());
const swatchEl = (n) => (n ? h('span', { class: `epic-swatch epic-swatch--cat-${n}`, 'aria-hidden': 'true' }) : null);
// An epic the board does not list keeps its id and has no swatch.
const epicCell = (id, epics) => {
  const ep = epics.get(id);
  return h('span', { class: 't-epic-cell' }, [swatchEl(ep?.swatch), truncate(ep?.name ?? id)]);
};

// Widths in rem are fixed, each just wider than its header or its usual content; the ID column is measured to its
// longest ID (up to ID_MAX_CH, beyond which an ID wraps, never cut) and the title takes the rest.
const COLUMNS = [
  { key: 'id',        label: 'ID',       sortable: true,
    get: t => t.id, cell: t => h('span', { class: 't-id' }, String(t.id ?? '')) },
  { key: 'title',     label: 'Title',    sortable: true,
    get: t => (t.title || '').toLowerCase(),
    cell: t => h('a', { class: 'tbl-link', href: '#/task/' + encodeURIComponent(t.id) }, truncate(t.title || t.id)) },
  { key: 'status',    label: 'Status',   width: 8,    sortable: true,
    get: t => statusOrder(t.status), cell: t => statusMarker('task', t.status) },
  { key: 'priority',  label: 'Priority', width: 6.5,  sortable: true,
    get: t => priorityOrder(t.priority), cell: t => (t.priority ? priorityMarker(String(t.priority).toLowerCase()) : none()) },
  { key: 'phase',     label: 'Phase',    width: 5,    sortable: true,
    get: t => t.phase || '', cell: t => tech(t.phase) },
  { key: 'epic',      label: 'Epic',     width: 11,   sortable: true,
    get: t => t.epic || '', cell: (t, ctx) => (t.epic ? epicCell(t.epic, ctx.epics) : none()) },
  { key: 'area',      label: 'Area',     width: 7,    sortable: true,
    get: t => t.area || '', cell: t => tech(t.area) },
  { key: 'estimate',  label: 'Size',     width: 4.5,  sortable: true,
    get: t => sizeOrder(t.estimate), cell: t => (t.estimate ? h('span', { class: 't-tech' }, String(t.estimate)) : none()) },
  { key: 'branch',    label: 'Branch',   width: 12,   sortable: false,
    get: t => t.branch || '', cell: t => (t.branch ? truncate(t.branch, { tag: 'code', className: 't-tech' }) : none()) },
  { key: 'started',   label: 'Started',  width: 7.5,  sortable: true,
    get: t => t.started || '',
    cell: t => (t.started ? h('span', { class: 't-tech' }, formatAbsolute(t.started, { time: false, year: true }) || String(t.started)) : none()) },
];
const TITLE_MIN_REM = 20;
const ID_MAX_CH = 14;
const FIXED_REM = COLUMNS.reduce((sum, c) => sum + (c.width || 0), 0);

const STATUS_ORDER = { 'in-progress': 0, 'in-review': 1, blocked: 2, todo: 3, done: 4, archived: 5 };
const PRIORITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };
const SIZE_ORDER = { XS: 0, S: 1, M: 2, L: 3, XL: 4 };

function statusOrder(s)  { return STATUS_ORDER[s] ?? 99; }
function priorityOrder(p){ return PRIORITY_ORDER[(p||'').toLowerCase()] ?? 99; }
function sizeOrder(s)    { return SIZE_ORDER[s] ?? 99; }
function prettyStatus(s) { return TASK_STATUS[s]?.label || s || ''; }
function prettyPriority(p) { return PRIORITY[p]?.label || p || ''; }

const DEFAULT_STATE = {
  sort: { by: 'priority', dir: 'asc' },   // priority asc → critical first
  search: '',
  filters: { status: [], priority: [], epic: [], area: [] },
};

// A saved sort from an older build (or a hand-edited prefs file) may name a column that is gone or no longer sortable;
// keeping it would leave the phone Sort select blank and the rows unsorted, so it falls back to the default.
function validSort(saved) {
  const ok = saved && COLUMNS.some(c => c.sortable && c.key === saved.by) && (saved.dir === 'asc' || saved.dir === 'desc');
  return ok ? { by: saved.by, dir: saved.dir } : { ...DEFAULT_STATE.sort };
}

export async function mount(root, { store, api, prefs, params }) {
  root.replaceChildren();
  const screen = document.createElement('section');
  screen.className = 'tbl-screen';

  // ── Topbar: Add task and the count in row 1, the search in row 2 ─
  const topbar = claimTopbar();
  const searchBuilt = tmSearch({
    placeholder: 'Filter… (prefix ! to exclude)',
    onInput: (v) => { state.search = v; paint(); persist(); },
  });
  const search = searchBuilt.input;
  const newTaskBtn = tmAction({
    icon: 'plus', label: 'Task', variant: 'primary', title: 'Add task',
    onClick: () => openTaskCreateModal({ store, api }),
  });
  topbar?.appendChild(searchBuilt.el);
  claimTopbarPrimary()?.append(newTaskBtn);

  // ── Filter chip rail ──────────────────────────────────────────
  // One chipRow per group, made the first time the group has options and updated in place on every paint, so focus
  // and an open "More" survive a redraw.
  const chipRail = document.createElement('div');
  chipRail.className = 'tbl-chips';
  screen.appendChild(chipRail);
  const chipRows = new Map();   // kind → { row, chips }
  const clearBtn = document.createElement('button');
  clearBtn.type = 'button';
  clearBtn.className = 'btn btn--ghost btn--sm tbl-clear';
  clearBtn.append(icon('dismiss', { size: 14 }), 'Clear filters');
  clearBtn.addEventListener('click', () => {
    // The button goes with the filters; the keyboard carries on from the search rather than from <body>.
    const had = document.activeElement === clearBtn;
    clearFilters();
    if (had) search.focus();
  });
  // A row sized by its chips (Status, Priority) does not grow back by itself once chips are parked; a new rail width
  // lays every row out again from its full set. (overflowRow's JSDoc: a row's width must come from its container.)
  let railWidth = null;
  const railObserver = window.ResizeObserver ? new ResizeObserver((entries) => {
    const w = entries.at(-1).contentRect.width;
    if (w === railWidth) return;
    railWidth = w;
    for (const { row, chips } of chipRows.values()) row.update(chips);
  }) : null;
  railObserver?.observe(chipRail);

  // ── Sort bar ──────────────────────────────────────────────────
  // At phone width the header row is gone, so the sort is this one select (CSS shows it only there).
  const sortSelect = h('select', { id: 'tbl-sort', class: 'ef-enum-select' },
    COLUMNS.filter(c => c.sortable).flatMap(c => ['asc', 'desc'].map(dir =>
      h('option', { value: `${c.key}:${dir}` }, `${c.label} — ${dir === 'asc' ? 'ascending' : 'descending'}`))));
  sortSelect.addEventListener('change', () => {
    const [by, dir] = sortSelect.value.split(':');
    state.sort = { by, dir };
    paint(); persist();
  });
  screen.appendChild(h('div', { class: 'tbl-sortbar' },
    h('label', { class: 'tbl-sortbar__label', for: 'tbl-sort' }, 'Sort'),
    h('span', { class: 'ef-select' }, sortSelect, icon('chevron', { size: 16 }))));

  // ── Table frame ───────────────────────────────────────────────
  // The host scrolls both ways inside the frame; the frame says whether there is more to the right (the fade) and
  // whether the user has scrolled sideways (a stronger edge on the title column).
  const frame = h('div', { class: 'tbl-frame' });
  const tableHost = h('div', { class: 'tbl-host' });
  const fade = h('div', { class: 'tbl-fade tbl-fade--end', 'aria-hidden': 'true' });
  // The start fade sits just after the sticky edge, over the columns scrolled under it.
  const fadeStart = h('div', { class: 'tbl-fade tbl-fade--start', 'aria-hidden': 'true' });
  frame.append(tableHost, fade, fadeStart);
  screen.appendChild(frame);
  // The measured ID column: { longest id it was measured for, its width in px }.
  let measured = { longest: null, px: 0 };
  const cue = () => {
    frame.toggleAttribute('data-more-end', tableHost.scrollLeft + tableHost.clientWidth < tableHost.scrollWidth - 1);
    frame.toggleAttribute('data-scrolled', tableHost.scrollLeft > 0);
    // A header that has gone under the sticky edge, even in part, hides its label: a cut one would leave a fragment of
    // its word under the fade ("IC ⇕"). The sticky headers never go under: the ID always, the title unless the frame
    // lets it scroll (data-title-loose). A sticky one's offsetLeft follows the scroll, so it is never measured.
    const edge = parseFloat(fadeStart.style.left) || 0;
    const loose = frame.hasAttribute('data-title-loose');
    for (const th of tableHost.querySelectorAll('th.tbl-th')) {
      const sticky = th.dataset.key === 'id' || (th.dataset.key === 'title' && !loose);
      th.toggleAttribute('data-under', !sticky && th.offsetLeft >= edge - 0.5 && th.offsetLeft - tableHost.scrollLeft < edge - 0.5);
    }
  };
  // What depends on the host's size rather than its scroll: run on resize and after every paint, reads before writes.
  const fit = () => {
    const { clientWidth, offsetWidth, clientHeight, offsetHeight } = tableHost;
    const headHeight = tableHost.querySelector('thead')?.offsetHeight ?? 0;
    const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
    // The fade stops at the scrollbars, never over them.
    fade.style.right = `${offsetWidth - clientWidth}px`;
    fade.style.bottom = fadeStart.style.bottom = `${offsetHeight - clientHeight}px`;
    // A link reached by Shift+Tab is scrolled into view below the sticky header, not under it.
    tableHost.style.scrollPaddingTop = `${headHeight}px`;
    // When ID and title together would hold most of the frame, the title scrolls with the rest and only the ID stays,
    // so the other columns can still come into view.
    const loose = measured.px + TITLE_MIN_REM * rem > 0.6 * clientWidth;
    frame.toggleAttribute('data-title-loose', loose);
    const tbl = tableHost.querySelector('table.tbl');
    if (tbl) placeTitle(tbl);
    // The empty/no-match block spans the part of the table the frame shows, so its action is always in reach.
    const block = tableHost.querySelector('.tbl-empty > .tm-empty');
    if (block) block.style.width = `${clientWidth}px`;
    cue();
  };
  tableHost.addEventListener('scroll', cue, { passive: true });
  const hostObserver = window.ResizeObserver ? new ResizeObserver(fit) : null;
  hostObserver?.observe(tableHost);

  root.appendChild(screen);

  // Hydrate state from prefs
  const persisted = (store.getPrefs() || {}).table || {};
  const state = {
    sort:    validSort(persisted.sort),
    search:  persisted.search || '',
    filters: {
      status:   [...(persisted.filters?.status   || [])],
      priority: [...(persisted.filters?.priority || [])],
      epic:     [...(persisted.filters?.epic     || [])],
      area:     [...(persisted.filters?.area     || [])],
    },
  };
  // A link may name the statuses to show (the Dashboard's "In progress"): they replace the saved Status chips for this
  // visit only — nothing is saved until the user changes something.
  const seed = [...new Set(String(params?.status ?? '').split(',').map((v) => v.trim()))]
    .filter((v) => v !== 'archived' && Object.hasOwn(TASK_STATUS, v));
  if (seed.length) state.filters.status = seed;
  search.value = state.search;

  function persist() {
    if (!prefs?.patch) return;
    prefs.patch({ table: state });
  }

  function applyFilters(tasks) {
    const rawQ = state.search.trim().toLowerCase();
    const negate = rawQ.startsWith('!');
    const q = negate ? rawQ.slice(1).trimStart() : rawQ;
    return tasks.filter(t => {
      if (state.filters.status.length   && !state.filters.status.includes(t.status)) return false;
      if (state.filters.priority.length && !state.filters.priority.includes((t.priority || '').toLowerCase())) return false;
      if (state.filters.epic.length     && !state.filters.epic.includes(t.epic)) return false;
      if (state.filters.area.length     && !state.filters.area.includes(t.area)) return false;
      if (q) {
        const hay = `${t.id} ${t.title || ''} ${t.branch || ''}`.toLowerCase();
        const matches = hay.includes(q);
        if (negate ? matches : !matches) return false;
      }
      return true;
    });
  }

  function sortTasks(tasks) {
    const col = COLUMNS.find(c => c.key === state.sort.by);
    if (!col) return tasks;
    const sign = state.sort.dir === 'desc' ? -1 : 1;
    return [...tasks].sort((a, b) => {
      const va = col.get(a); const vb = col.get(b);
      if (va < vb) return -1 * sign;
      if (va > vb) return  1 * sign;
      return 0;
    });
  }

  function renderChipRail(backlog) {
    const tasks = backlog.tasks || [];
    const epics = epicIndex(backlog.epics);
    const groups = [
      { kind: 'status',   label: 'Status',   options: Object.keys(TASK_STATUS).filter(k => k !== 'archived'), pretty: prettyStatus, of: t => t.status },
      { kind: 'priority', label: 'Priority', options: Object.keys(PRIORITY), pretty: prettyPriority, of: t => (t.priority || '').toLowerCase() },
      { kind: 'epic',     label: 'Epic',     options: [...epics.keys()], pretty: s => epics.get(s)?.name ?? s, of: t => t.epic },
      { kind: 'area',     label: 'Area',     options: [...new Set(tasks.map(t => t.area).filter(Boolean))].sort(), pretty: s => s, of: t => t.area },
    ];
    for (const [i, g] of groups.entries()) {
      const active = state.filters[g.kind];
      // A pressed value stays offered after its last task or its epic is gone, so it can still be turned off.
      const options = [...g.options, ...active.filter(v => !g.options.includes(v))];
      const entry = chipRows.get(g.kind);
      if (!options.length) {
        if (entry) entry.row.el.hidden = true;
        continue;
      }
      const chips = options.map(value => ({
        value,
        label: g.pretty(value),
        pressed: active.includes(value),
        count: tasks.filter(t => g.of(t) === value).length,
        swatch: g.kind === 'epic' ? (epics.get(value)?.swatch ?? null) : undefined,
      }));
      if (entry) {
        entry.chips = chips;
        entry.row.el.hidden = false;
        entry.row.update(chips);
        continue;
      }
      const row = chipRow({
        label: g.label,
        chips,
        hint: CHIP_CLICK_HINT,
        onToggle: (value, ev) => {
          state.filters[g.kind] = chipClickNext(ev, state.filters[g.kind], value);
          paint(); persist();
        },
      });
      row.el.dataset.kind = g.kind;
      // Groups keep their order whichever gets options first.
      const next = groups.slice(i + 1).map(x => chipRows.get(x.kind)?.row.el).find(Boolean);
      chipRail.insertBefore(row.el, next ?? null);
      chipRows.set(g.kind, { row, chips });
    }
    if (!hasFilters()) clearBtn.remove();
    else if (!clearBtn.isConnected) chipRail.appendChild(clearBtn);
  }

  function hasFilters() {
    return !!(state.filters.status.length || state.filters.priority.length || state.filters.epic.length || state.filters.area.length || state.search);
  }

  function buildFilterHint(totalCount) {
    const parts = [];
    if (state.search) parts.push(`search "${state.search}"`);
    if (state.filters.status.length)   parts.push(`status: ${state.filters.status.map(prettyStatus).join(', ')}`);
    if (state.filters.priority.length) parts.push(`priority: ${state.filters.priority.map(prettyPriority).join(', ')}`);
    if (state.filters.epic.length)     parts.push(`epic: ${state.filters.epic.join(', ')}`);
    if (state.filters.area.length)     parts.push(`area: ${state.filters.area.join(', ')}`);
    const hidden = totalCount;
    const filterDesc = parts.length ? parts.join(' · ') : 'active filters';
    return `${filterDesc} — ${hidden} ${pluralize(hidden, 'task', 'tasks')} hidden`;
  }

  function clearFilters() {
    state.filters = { status: [], priority: [], epic: [], area: [] };
    state.search = '';
    search.value = '';
    paint(); persist();
  }

  // The ID column is as wide as the longest ID on screen, up to ID_MAX_CH (a longer ID wraps; none is ever cut), and
  // never narrower than its own header with the sort arrow; the title column sticks just after it.
  function sizeColumns(tbl, tasks) {
    const longest = tasks.reduce((a, t) => (String(t.id ?? '').length > a.length ? String(t.id) : a), 'ID');
    if (longest !== measured.longest) {
      const probe = h('span', { class: 't-id tbl-probe' }, longest);
      const cap = h('span', { class: 't-id tbl-probe' }, '0'.repeat(ID_MAX_CH));
      tableHost.append(probe, cap);
      const th = tbl.querySelector('th[data-key="id"]');
      const cs = getComputedStyle(th);
      const btn = th.querySelector('.sort-header');
      const head = btn
        ? [...btn.children].reduce((w, c) => w + c.getBoundingClientRect().width, 0)
          + (parseFloat(getComputedStyle(btn).columnGap) || 0) * (btn.children.length - 1)
        : 0;
      const text = Math.max(Math.min(probe.getBoundingClientRect().width, cap.getBoundingClientRect().width), head);
      measured = { longest, px: Math.ceil(text) + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) + 1 };
      probe.remove();
      cap.remove();
    }
    tbl.querySelector('col.tbl-col--id').style.width = `${measured.px}px`;
    placeTitle(tbl);
  }

  // The title takes what the columns that fit whole beside it leave, less the end fade's width: the frame's edge then
  // cuts no column it shows, and the next one starts under the fade as the cue that more wait there. In a frame too
  // narrow for that (data-title-loose) the title keeps its minimum.
  function placeTitle(tbl) {
    const loose = frame.hasAttribute('data-title-loose');
    const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
    const min = TITLE_MIN_REM * rem;
    const room = tableHost.clientWidth - measured.px;
    const widths = COLUMNS.filter((c) => c.width).map((c) => c.width * rem);
    let used = 0;
    let shown = 0;
    if (!loose) {
      const peek = fade.getBoundingClientRect().width;
      while (shown < widths.length && min + used + widths[shown] + (shown + 1 < widths.length ? peek : 0) <= room) used += widths[shown++];
      if (shown < widths.length) used += peek;
    }
    const title = loose ? min : Math.max(min, Math.floor(room - used));
    tbl.querySelector('col.tbl-col--title').style.width = `${title}px`;
    tbl.style.minWidth = `${measured.px + title + FIXED_REM * rem}px`;
    const left = loose ? '' : `${measured.px}px`;
    for (const el of tbl.querySelectorAll('th[data-key="title"], td.tbl-cell--title')) el.style.left = left;
    fadeStart.style.left = `${measured.px + (loose ? 0 : title)}px`;
  }

  // The table is rebuilt on every paint: where the frame was scrolled and what the keyboard was on are noted first and
  // given back after, so another writer's change never loses the user's place.
  function snapshot() {
    const at = document.activeElement;
    const snap = { top: tableHost.scrollTop, left: tableHost.scrollLeft, inside: !!at && tableHost.contains(at), key: null, taskId: null, index: 0 };
    if (!snap.inside) return snap;
    snap.key = at.closest('th')?.dataset.key ?? null;
    const tr = at.closest('tr.tbl-row');
    if (tr) {
      snap.taskId = tr.dataset.taskId;
      snap.index = [...tr.parentNode.children].indexOf(tr);
    }
    return snap;
  }

  function restore(tbl, snap) {
    tableHost.scrollTop = snap.top;
    tableHost.scrollLeft = snap.left;
    if (!snap.inside) return;
    const links = [...tbl.querySelectorAll('tr.tbl-row a.tbl-link')];
    const target = (snap.key && tbl.querySelector(`th[data-key="${snap.key}"] .sort-header`))
      || (snap.taskId != null && links.find((a) => a.closest('tr').dataset.taskId === snap.taskId))
      || (links.length ? links[Math.min(snap.index, links.length - 1)] : tbl.querySelector('th[data-key="id"] .sort-header'));
    target?.focus({ preventScroll: true });
  }

  // A plain click anywhere on a row is a click on its title link (which the detail interceptor opens); a modified
  // click, a click on a control, or the end of a text selection is left alone.
  function onBodyClick(e) {
    if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    if (e.target.closest('a[href], button, input, select, textarea')) return;
    if (String(window.getSelection?.() ?? '')) return;
    e.target.closest('tr.tbl-row')?.querySelector('a.tbl-link')?.click();
  }

  function emptyRow(totalCount) {
    const block = hasFilters()
      ? stateBlock({
        label: 'No match',
        headline: `0 of ${totalCount} ${pluralize(totalCount, 'task', 'tasks')} match.`,
        hint: buildFilterHint(totalCount),
        action: { label: 'Clear filters', onClick: clearFilters },
      })
      : stateBlock({ label: 'Table', headline: 'No tasks yet.', hint: 'Tasks added to the backlog show up here.' });
    return h('tr', {}, h('td', { class: 'tbl-empty', colspan: String(COLUMNS.length) }, block));
  }

  function renderTable(tasks, totalCount, ctx) {
    const snap = snapshot();
    const tbl = h('table', { class: 'tbl' });

    const colgroup = h('colgroup');
    for (const col of COLUMNS) {
      const c = h('col', { class: 'tbl-col--' + col.key });
      if (col.width) c.style.width = `${col.width}rem`;
      colgroup.appendChild(c);
    }

    const trh = h('tr');
    for (const col of COLUMNS) {
      const th = sortHeader({
        key: col.key, label: col.label, sortable: col.sortable, sort: state.sort,
        onSort: (next) => { state.sort = next; paint(); persist(); },
      });
      th.dataset.key = col.key;
      th.classList.add('tbl-th');
      trh.appendChild(th);
    }

    const tbody = h('tbody');
    if (!tasks.length) tbody.appendChild(emptyRow(totalCount));
    for (const t of tasks) {
      const tr = h('tr', { class: 'tbl-row', 'data-task-id': t.id });
      for (const col of COLUMNS) tr.appendChild(h('td', { class: 'tbl-cell tbl-cell--' + col.key }, col.cell(t, ctx)));
      tbody.appendChild(tr);
    }
    tbody.addEventListener('click', onBodyClick);

    tbl.append(colgroup, h('thead', {}, trh), tbody);
    tableHost.replaceChildren(tbl);
    sizeColumns(tbl, tasks);
    restore(tbl, snap);
    fit();
  }

  function paint() {
    const backlog = store.getBacklog() || { tasks: [], epics: [], phases: [] };
    const tasks   = Array.isArray(backlog.tasks) ? backlog.tasks : [];
    const filtered = applyFilters(tasks);
    const sorted   = sortTasks(filtered);

    // "n tasks", and how many show only while a chip, the search or a ?status= link narrows the list.
    setTopbarCount(`${tasks.length} ${pluralize(tasks.length, 'task', 'tasks')}${hasFilters() ? ` · ${filtered.length} visible` : ''}`);
    // Reflect external state changes (e.g. clear button) into the topbar input.
    if (search.value !== state.search) search.value = state.search;
    sortSelect.value = `${state.sort.by}:${state.sort.dir}`;
    renderChipRail(backlog);
    renderTable(sorted, tasks.length, { epics: epicIndex(backlog.epics) });
  }

  paint();
  const unsubBacklog = store.subscribe('backlog', paint);
  // The ID column was measured in whatever font was ready; once the real one is, measure again.
  let alive = true;
  document.fonts?.ready.then(() => { if (!alive) return; measured.longest = null; paint(); });

  return () => {
    alive = false;
    unsubBacklog?.();
    tableHost.removeEventListener('scroll', cue);
    hostObserver?.disconnect();
    railObserver?.disconnect();
    for (const { row } of chipRows.values()) row.destroy();
    chipRows.clear();
  };
}
