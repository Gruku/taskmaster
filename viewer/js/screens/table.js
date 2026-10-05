// Table view — Obsidian-Bases-style alternative to Kanban.
// Reads /api/backlog (via store), renders a sortable+filterable table.
// Persisted state lives under prefs.table.

import { claimTopbar, tmSubcount, tmSearch, tmAction } from '../lib/topbar.js';
import { pluralize } from '../util/pluralize.js';
import { formatAbsolute } from '../lib/time.js';
import { emptyState } from '../components/empty-state.js';
import { openTaskCreateModal } from '../components/edit/task-actions.js';
import { chipClickNext, CHIP_CLICK_HINT } from '../util/chip-toggle.js';
import { chipRow } from '../components/chips.js';
import { sortHeader } from '../components/sort-header.js';
import { icon } from '../components/icon.js';
import { TASK_STATUS, PRIORITY } from '../components/status.js';
import { epicSwatch } from '../lib/epics.js';

export const meta = { title: 'Table', icon: '▭', sidebarKey: 'table' };

const COLUMNS = [
  { key: 'id',        label: 'ID',       width: '110px', sortable: true,
    get: t => t.id, render: t => `<span class="t-id">${esc(t.id)}</span>` },
  { key: 'title',     label: 'Title',    width: 'minmax(220px, 1fr)', sortable: true,
    get: t => (t.title || '').toLowerCase(), render: t => esc(t.title || '—') },
  { key: 'status',    label: 'Status',   width: '110px', sortable: true,
    get: t => statusOrder(t.status), render: t => `<span class="t-status t-status--${esc(t.status||'')}">${esc(prettyStatus(t.status))}</span>` },
  { key: 'priority',  label: 'Priority', width: '90px', sortable: true,
    get: t => priorityOrder(t.priority), render: t => `<span class="t-pri t-pri--${esc((t.priority||'').toLowerCase())}">${esc(t.priority || '')}</span>` },
  { key: 'phase',     label: 'Phase',    width: '90px', sortable: true,
    get: t => t.phase || '', render: t => esc(t.phase || '—') },
  { key: 'epic',      label: 'Epic',     width: '140px', sortable: true,
    get: t => t.epic || '', render: t => t.epic ? `<span class="t-epic">${esc(t.epic)}</span>` : '—' },
  { key: 'area',      label: 'Area',     width: '120px', sortable: true,
    get: t => t.area || '', render: t => t.area ? `<span class="t-area">${esc(t.area)}</span>` : '—' },
  { key: 'estimate',  label: 'Size',     width: '60px', sortable: true,
    get: t => sizeOrder(t.estimate), render: t => esc(t.estimate || '—') },
  { key: 'branch',    label: 'Branch',   width: '180px', sortable: false,
    get: t => t.branch || '', render: t => t.branch ? `<code class="t-branch">${esc(t.branch)}</code>` : '—' },
  { key: 'started',   label: 'Started',  width: '110px', sortable: true,
    get: t => t.started || '', render: t => t.started ? (formatAbsolute(t.started, { time: false, year: true }) || esc(t.started)) : '—' },
];

const STATUS_ORDER = { 'in-progress': 0, 'in-review': 1, blocked: 2, todo: 3, done: 4 };
const PRIORITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };
const SIZE_ORDER = { XS: 0, S: 1, M: 2, L: 3, XL: 4 };

function statusOrder(s)  { return STATUS_ORDER[s] ?? 99; }
function priorityOrder(p){ return PRIORITY_ORDER[(p||'').toLowerCase()] ?? 99; }
function sizeOrder(s)    { return SIZE_ORDER[s] ?? 99; }
function prettyStatus(s) { return TASK_STATUS[s]?.label || s || ''; }
function prettyPriority(p) { return PRIORITY[p]?.label || p || ''; }
function esc(v) { return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

const DEFAULT_STATE = {
  sort: { by: 'priority', dir: 'asc' },   // priority asc → critical first
  search: '',
  filters: { status: [], priority: [], epic: [], area: [] },
};

export async function mount(root, { store, api, prefs }) {
  root.innerHTML = '';
  const screen = document.createElement('section');
  screen.className = 'tbl-screen';

  // ── Topbar (#topbar-actions) ─────────────────────────────────
  const topbar = claimTopbar();
  const subcount = tmSubcount('… tasks');
  const searchBuilt = tmSearch({
    placeholder: 'Filter… (prefix ! to exclude)',
    onInput: (v) => { state.search = v; paint(); persist(); },
  });
  const search = searchBuilt.input;
  const newTaskBtn = tmAction({
    icon: 'plus', label: 'Task', variant: 'primary', title: 'Add task',
    onClick: () => openTaskCreateModal({ store, api }),
  });
  topbar?.appendChild(subcount);
  topbar?.appendChild(searchBuilt.el);
  topbar?.appendChild(newTaskBtn);

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
  // lays every row out again from its full set.
  let railWidth = null;
  const railObserver = window.ResizeObserver ? new ResizeObserver((entries) => {
    const w = entries.at(-1).contentRect.width;
    if (w === railWidth) return;
    railWidth = w;
    for (const { row, chips } of chipRows.values()) row.update(chips);
  }) : null;
  railObserver?.observe(chipRail);

  // ── Table mount ───────────────────────────────────────────────
  const tableHost = document.createElement('div');
  tableHost.className = 'tbl-host';
  screen.appendChild(tableHost);

  root.appendChild(screen);

  // Hydrate state from prefs
  const persisted = (store.getPrefs() || {}).table || {};
  const state = {
    sort:    { ...DEFAULT_STATE.sort, ...(persisted.sort || {}) },
    search:  persisted.search || '',
    filters: {
      status:   [...(persisted.filters?.status   || [])],
      priority: [...(persisted.filters?.priority || [])],
      epic:     [...(persisted.filters?.epic     || [])],
      area:     [...(persisted.filters?.area     || [])],
    },
  };
  search.value = state.search;

  function persist() {
    if (!prefs?.patch) return;
    prefs.patch({ table: state });
  }

  function rowClick(taskId) {
    window.location.hash = '#/task/' + encodeURIComponent(taskId);
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
    const epics = backlog.epics || [];
    const epicName = new Map(epics.filter(e => e && e.id).map(e => [e.id, e.name || e.id]));
    const groups = [
      { kind: 'status',   label: 'Status',   options: Object.keys(TASK_STATUS).filter(k => k !== 'archived'), pretty: prettyStatus, of: t => t.status },
      { kind: 'priority', label: 'Priority', options: Object.keys(PRIORITY), pretty: prettyPriority, of: t => (t.priority || '').toLowerCase() },
      { kind: 'epic',     label: 'Epic',     options: [...epicName.keys()], pretty: s => epicName.get(s) || s, of: t => t.epic },
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
        swatch: g.kind === 'epic' ? epicSwatch(value, epics) : undefined,
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

  function renderTable(tasks, totalCount) {
    // The table is rebuilt on every paint, so the header the keyboard was on gets the focus back.
    const focusedKey = tableHost.contains(document.activeElement) ? document.activeElement.closest('th')?.dataset.key : null;
    tableHost.innerHTML = '';
    const tbl = document.createElement('table');
    tbl.className = 'tbl';
    tbl.style.gridTemplateColumns = COLUMNS.map(c => c.width).join(' ');

    // Header row
    const thead = document.createElement('thead');
    const trh = document.createElement('tr');
    for (const col of COLUMNS) {
      const th = sortHeader({
        key: col.key, label: col.label, sortable: col.sortable, sort: state.sort,
        onSort: (next) => { state.sort = next; paint(); persist(); },
      });
      th.dataset.key = col.key;
      th.classList.add('tbl-th');
      trh.appendChild(th);
    }
    thead.appendChild(trh);
    tbl.appendChild(thead);

    // Body
    const tbody = document.createElement('tbody');
    if (!tasks.length) {
      const filtered = hasFilters();
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = COLUMNS.length;
      td.className = 'tbl-empty';
      td.appendChild(emptyState({
        headline: filtered ? `0 of ${totalCount} ${pluralize(totalCount, 'task', 'tasks')} match` : 'No tasks yet',
        hint: filtered ? buildFilterHint(totalCount) : null,
        action: filtered ? { label: 'Clear filters', onClick: clearFilters } : null,
      }));
      tr.appendChild(td);
      tbody.appendChild(tr);
    } else {
      for (const t of tasks) {
        const tr = document.createElement('tr');
        tr.className = 'tbl-row';
        tr.dataset.taskId = t.id;
        tr.tabIndex = 0;
        for (const col of COLUMNS) {
          const td = document.createElement('td');
          td.className = 'tbl-cell tbl-cell--' + col.key;
          td.innerHTML = col.render(t);
          tr.appendChild(td);
        }
        tr.addEventListener('click', () => rowClick(t.id));
        tr.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); rowClick(t.id); }
        });
        tbody.appendChild(tr);
      }
    }
    tbl.appendChild(tbody);
    tableHost.appendChild(tbl);
    if (focusedKey) tbl.querySelector(`th[data-key="${focusedKey}"] .sort-header`)?.focus({ preventScroll: true });
  }

  function paint() {
    const backlog = store.getBacklog() || { tasks: [], epics: [], phases: [] };
    const tasks   = Array.isArray(backlog.tasks) ? backlog.tasks : [];
    const filtered = applyFilters(tasks);
    const sorted   = sortTasks(filtered);

    subcount.textContent = `${tasks.length} ${pluralize(tasks.length, 'task', 'tasks')} · ${filtered.length} visible`;
    // Reflect external state changes (e.g. clear button) into the topbar input.
    if (search.value !== state.search) search.value = state.search;
    renderChipRail(backlog);
    renderTable(sorted, tasks.length);
  }

  paint();
  const unsubBacklog = store.subscribe('backlog', paint);

  return () => {
    unsubBacklog?.();
    railObserver?.disconnect();
    for (const { row } of chipRows.values()) row.destroy();
    chipRows.clear();
  };
}
