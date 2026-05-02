// Table view — Obsidian-Bases-style alternative to Kanban.
// Reads /api/backlog (via store), renders a sortable+filterable table.
// Persisted state lives under prefs.table.

import { claimTopbar, tmSubcount, tmSearch, tmAction } from '../lib/topbar.js';
import { pluralize } from '../util/pluralize.js';
import { formatAbsolute } from '../lib/time.js';
import { emptyState } from '../components/empty-state.js';

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
  { key: 'estimate',  label: 'Size',     width: '60px', sortable: true,
    get: t => sizeOrder(t.estimate), render: t => esc(t.estimate || '—') },
  { key: 'branch',    label: 'Branch',   width: '180px', sortable: false,
    get: t => t.branch || '', render: t => t.branch ? `<code class="t-branch">${esc(t.branch)}</code>` : '—' },
  { key: 'started',   label: 'Started',  width: '110px', sortable: true,
    get: t => t.started || '', render: t => t.started ? formatDate(t.started) : '—' },
];

const STATUS_LABELS = { todo: 'Todo', in_progress: 'In Progress', in_review: 'In Review', done: 'Done', blocked: 'Blocked' };
const STATUS_ORDER = { in_progress: 0, in_review: 1, blocked: 2, todo: 3, done: 4 };
const PRIORITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };
const SIZE_ORDER = { XS: 0, S: 1, M: 2, L: 3, XL: 4 };

function statusOrder(s)  { return STATUS_ORDER[s] ?? 99; }
function priorityOrder(p){ return PRIORITY_ORDER[(p||'').toLowerCase()] ?? 99; }
function sizeOrder(s)    { return SIZE_ORDER[s] ?? 99; }
function prettyStatus(s) { return STATUS_LABELS[s] || s || ''; }
function esc(v) { return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function formatDate(iso) {
  if (!iso) return '—';
  const out = formatAbsolute(iso, { time: false, year: true });
  return out || esc(iso);
}

const DEFAULT_STATE = {
  sort: { by: 'priority', dir: 'asc' },   // priority asc → critical first
  search: '',
  filters: { status: [], priority: [], epic: [] },
};

export async function mount(root, { store, prefs }) {
  root.innerHTML = '';
  const screen = document.createElement('section');
  screen.className = 'tbl-screen';

  // ── Topbar (#topbar-actions) ─────────────────────────────────
  const topbar = claimTopbar();
  const subcount = tmSubcount('… tasks');
  const searchBuilt = tmSearch({
    placeholder: 'Filter by title, id, or branch…',
    onInput: (v) => { state.search = v; paint(); persist(); },
  });
  const search = searchBuilt.input;
  const newTaskBtn = tmAction({
    icon: '+', label: 'Task', variant: 'primary', title: 'Add task',
    onClick: () => { window.location.hash = '#/task/new'; },
  });
  topbar?.appendChild(subcount);
  topbar?.appendChild(searchBuilt.el);
  topbar?.appendChild(newTaskBtn);

  // ── Filter chip rail ──────────────────────────────────────────
  const chipRail = document.createElement('div');
  chipRail.className = 'tbl-chips';
  screen.appendChild(chipRail);

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
    const q = state.search.trim().toLowerCase();
    return tasks.filter(t => {
      if (state.filters.status.length   && !state.filters.status.includes(t.status)) return false;
      if (state.filters.priority.length && !state.filters.priority.includes((t.priority || '').toLowerCase())) return false;
      if (state.filters.epic.length     && !state.filters.epic.includes(t.epic)) return false;
      if (q) {
        const hay = `${t.id} ${t.title || ''} ${t.branch || ''}`.toLowerCase();
        if (!hay.includes(q)) return false;
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
    chipRail.innerHTML = '';
    const groups = [
      { kind: 'status',   label: 'Status',   options: ['todo','in_progress','in_review','blocked','done'], pretty: prettyStatus },
      { kind: 'priority', label: 'Priority', options: ['critical','high','medium','low'], pretty: s => s[0].toUpperCase() + s.slice(1) },
      { kind: 'epic',     label: 'Epic',     options: (backlog.epics || []).map(e => e.id), pretty: s => s },
    ];
    for (const g of groups) {
      if (!g.options.length) continue;
      const wrap = document.createElement('div');
      wrap.className = 'tbl-chip-group';
      const lab = document.createElement('span');
      lab.className = 'tbl-chip-label';
      lab.textContent = g.label;
      wrap.appendChild(lab);
      for (const opt of g.options) {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'tbl-chip';
        chip.dataset.kind = g.kind;
        chip.dataset.value = opt;
        const active = state.filters[g.kind].includes(opt);
        chip.classList.toggle('is-active', active);
        chip.textContent = g.pretty(opt);
        chip.addEventListener('click', () => {
          const arr = state.filters[g.kind];
          const i = arr.indexOf(opt);
          if (i >= 0) arr.splice(i, 1); else arr.push(opt);
          paint(); persist();
        });
        wrap.appendChild(chip);
      }
      chipRail.appendChild(wrap);
    }
    // Clear button
    const hasFilters = state.filters.status.length || state.filters.priority.length || state.filters.epic.length || state.search;
    if (hasFilters) {
      const clear = document.createElement('button');
      clear.type = 'button';
      clear.className = 'tbl-chip tbl-chip--clear';
      clear.textContent = '× Clear';
      clear.addEventListener('click', () => {
        state.filters = { status: [], priority: [], epic: [] };
        state.search = '';
        search.value = '';
        paint(); persist();
      });
      chipRail.appendChild(clear);
    }
  }

  function renderTable(tasks) {
    tableHost.innerHTML = '';
    const tbl = document.createElement('table');
    tbl.className = 'tbl';
    tbl.style.gridTemplateColumns = COLUMNS.map(c => c.width).join(' ');

    // Header row
    const thead = document.createElement('thead');
    const trh = document.createElement('tr');
    for (const col of COLUMNS) {
      const th = document.createElement('th');
      th.dataset.key = col.key;
      th.className = 'tbl-th';
      const isActive = state.sort.by === col.key;
      const arrow = isActive ? (state.sort.dir === 'desc' ? '↓' : '↑') : '';
      th.innerHTML = `<span class="tbl-th-label">${esc(col.label)}</span>${arrow ? `<span class="tbl-th-arrow">${arrow}</span>` : ''}`;
      if (col.sortable) {
        th.classList.add('is-sortable');
        if (isActive) th.classList.add('is-active');
        th.addEventListener('click', () => {
          if (state.sort.by === col.key) {
            state.sort.dir = state.sort.dir === 'asc' ? 'desc' : 'asc';
          } else {
            state.sort.by = col.key;
            state.sort.dir = 'asc';
          }
          paint(); persist();
        });
      }
      trh.appendChild(th);
    }
    thead.appendChild(trh);
    tbl.appendChild(thead);

    // Body
    const tbody = document.createElement('tbody');
    if (!tasks.length) {
      const hasFilters = state.filters.status.length || state.filters.priority.length || state.filters.epic.length || state.search;
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = COLUMNS.length;
      td.className = 'tbl-empty';
      td.appendChild(emptyState({
        headline: hasFilters ? 'No tasks match your filters' : 'No tasks yet',
        hint: hasFilters ? 'Try clearing a chip or the search box.' : null,
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
    renderTable(sorted);
  }

  paint();
  const unsubBacklog = store.subscribe('backlog', paint);

  return () => { unsubBacklog?.(); };
}
