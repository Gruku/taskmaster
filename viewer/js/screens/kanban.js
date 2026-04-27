// Kanban screen — full implementation.
// Mounts the page-head, phase stepper, epic chips, board surface, and auto-mode strip.
// Subscribes to store(backlog), store(autoState), and store(prefs); all writes go through prefs.patch(...).

import { renderCard }                        from '../components/card.js';
import { renderAutoModeStrip,
         updateAutoModeStrip,
         destroyAutoModeStrip }              from '../components/auto-mode-strip.js';
import { renderPriorityChips,
         updatePriorityChips }               from '../components/priority-chips.js';
import { renderPhaseStepper }                from '../components/phase-stepper.js';
import { renderEpicChips }                   from '../components/epic-chips.js';
import { applyFilters, sortTasks, groupTasks, STATUS_LABELS } from '../lib/filters.js';
import { assignEpicColors }                  from '../lib/epics.js';

export const meta = { title: 'Kanban', icon: '▦', sidebarKey: 'kanban' };

const DEFAULT_FILTERS = {
  priorities: [],
  epics: [],
  phase: '__all__',
  group_by: 'status',
  sort: { by: 'priority', dir: 'desc' },
  search: '',
};

export async function mount(root, { store, prefs }) {
  // ──────────────────────────────────────────────────────────────
  // Local state — sourced from prefs but mutated by UI events.
  // Persisted via prefs.patch({...}) (debounced).
  // ──────────────────────────────────────────────────────────────
  const persisted = (store.getPrefs() && store.getPrefs().kanban && store.getPrefs().kanban.filters) || {};
  const state = {
    filters: { ...DEFAULT_FILTERS, ...persisted },
    density: (store.getPrefs() && store.getPrefs().card_density) || 'full',
  };

  // Layout
  const page = document.createElement('div');
  page.className = 'kanban-page';

  // 1) Auto-mode strip (above page header, hidden when no run)
  const strip = renderAutoModeStrip({
    autoState: store.getAutoState(),
    backlog:   store.getBacklog(),
    onViewAll: () => { location.hash = '#/auto'; },
  });
  page.appendChild(strip);

  // 2) Page header
  const head = document.createElement('div');
  head.className = 'kanban-head';

  const title = document.createElement('span');
  title.className = 'title';
  title.textContent = 'Kanban';
  head.appendChild(title);

  const subcount = document.createElement('span');
  subcount.className = 'subcount';
  subcount.textContent = '… tasks';
  head.appendChild(subcount);

  // Search
  const search = document.createElement('div');
  search.className = 'kanban-search';
  search.innerHTML = `<span class="icon">⌕</span><input placeholder="Find by title, id, or branch…" /><span class="cmp-kbd">⌘K</span>`;
  const searchInput = search.querySelector('input');
  searchInput.value = state.filters.search || '';
  let searchTimer = null;
  searchInput.addEventListener('input', () => {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.filters.search = searchInput.value;
      paint(); savePrefs();
    }, 180);
  });
  head.appendChild(search);

  // Priority chips
  const pri = renderPriorityChips({
    active: state.filters.priorities,
    onToggle: (next) => { state.filters.priorities = next; paint(); savePrefs(); },
  });
  head.appendChild(pri);

  const right = document.createElement('div');
  right.className = 'kanban-head-right';

  // Density toggle
  const dens = document.createElement('div');
  dens.className = 'kanban-density';
  for (const k of ['minimal', 'full']) {
    const b = document.createElement('button');
    b.type = 'button';
    b.dataset.key = k;
    b.textContent = k === 'minimal' ? '▤ minimal' : '▦ full';
    if (state.density === k) b.classList.add('on');
    b.addEventListener('click', () => {
      state.density = k;
      dens.querySelectorAll('button').forEach(x => x.classList.toggle('on', x.dataset.key === k));
      paint(); prefs.patch({ card_density: k });
    });
    dens.appendChild(b);
  }
  right.appendChild(dens);

  // Group dropdown
  const group = document.createElement('select');
  group.className = 'kanban-select';
  for (const opt of [['status','Group: Status'],['phase','Group: Phase'],['epic','Group: Epic']]) {
    const o = document.createElement('option');
    o.value = opt[0]; o.textContent = opt[1];
    if (state.filters.group_by === opt[0]) o.selected = true;
    group.appendChild(o);
  }
  group.addEventListener('change', () => { state.filters.group_by = group.value; paint(); savePrefs(); });
  right.appendChild(group);

  // Sort dropdown
  const sort = document.createElement('select');
  sort.className = 'kanban-select';
  const SORT_OPTS = [
    ['priority:desc', 'Sort: priority ↓'],
    ['priority:asc',  'Sort: priority ↑'],
    ['size:desc',     'Sort: size ↓'],
    ['size:asc',      'Sort: size ↑'],
    ['created:desc',  'Sort: created ↓'],
    ['created:asc',   'Sort: created ↑'],
    ['started:desc',  'Sort: started ↓'],
    ['started:asc',   'Sort: started ↑'],
    ['touched:desc',  'Sort: touched ↓'],
    ['touched:asc',   'Sort: touched ↑'],
  ];
  for (const [v, label] of SORT_OPTS) {
    const o = document.createElement('option');
    o.value = v; o.textContent = label;
    const cur = `${state.filters.sort?.by || 'priority'}:${state.filters.sort?.dir || 'desc'}`;
    if (v === cur) o.selected = true;
    sort.appendChild(o);
  }
  sort.addEventListener('change', () => {
    const [by, dir] = sort.value.split(':');
    state.filters.sort = { by, dir };
    paint(); savePrefs();
  });
  right.appendChild(sort);

  // + Task button (Plan 2 stub: navigates to a hash that future plans will handle).
  const addBtn = document.createElement('button');
  addBtn.className = 'kanban-add-btn';
  addBtn.type = 'button';
  addBtn.textContent = '＋ Task';
  addBtn.addEventListener('click', () => { location.hash = '#/task/new'; });
  right.appendChild(addBtn);

  head.appendChild(right);
  page.appendChild(head);

  // 3) Phase stepper container (rendered in paint())
  const stepperHost = document.createElement('div');
  page.appendChild(stepperHost);

  // 4) Epic chips container
  const epicHost = document.createElement('div');
  page.appendChild(epicHost);

  // 5) Board surface
  const board = document.createElement('div');
  board.className = 'kanban-board';
  const boardGrid = document.createElement('div');
  boardGrid.className = 'kanban-board-grid';
  board.appendChild(boardGrid);
  page.appendChild(board);

  root.appendChild(page);

  // ──────────────────────────────────────────────────────────────
  // PAINT: full repaint from current state + store data.
  // ──────────────────────────────────────────────────────────────
  function paint() {
    const backlog = store.getBacklog() || { tasks: [], epics: [], phases: [] };
    const tasks   = Array.isArray(backlog.tasks) ? backlog.tasks : [];
    const epicsArr  = Array.isArray(backlog.epics) ? backlog.epics : [];
    const phasesArr = Array.isArray(backlog.phases) ? backlog.phases : [];
    const epicColors = assignEpicColors(epicsArr);

    // 1) Apply filters
    const filtered = applyFilters(tasks, state.filters);
    const sorted   = sortTasks(filtered, state.filters.sort);

    // 2) Subcount
    subcount.textContent = `${tasks.length} tasks · ${filtered.length} visible`;

    // 3) Phase stepper data
    const phaseRows = phasesArr.map(ph => {
      const total = tasks.filter(t => t.phase === ph.id).length;
      const done  = tasks.filter(t => t.phase === ph.id && t.status === 'done').length;
      let stat = (ph.status || '').toLowerCase();
      if (!stat) stat = (done >= total && total > 0) ? 'done' : (done > 0 ? 'active' : 'future');
      return { id: ph.id, name: ph.name || ph.id, status: stat, done, total };
    });
    stepperHost.replaceChildren(renderPhaseStepper({
      phases: phaseRows,
      active: state.filters.phase,
      onSelect: (key) => { state.filters.phase = key; paint(); savePrefs(); },
    }));

    // 4) Epic chips data
    const epicRows = epicsArr.map(ep => ({
      id:    ep.id,
      name:  ep.name || ep.id,
      color: epicColors[ep.id],
      count: tasks.filter(t => t.epic === ep.id).length,
    }));
    const filterCount =
      state.filters.priorities.length +
      state.filters.epics.length +
      (state.filters.phase && state.filters.phase !== '__all__' ? 1 : 0) +
      (state.filters.search ? 1 : 0);
    epicHost.replaceChildren(renderEpicChips({
      epics: epicRows,
      active: state.filters.epics,
      filterCount,
      onToggle: (next) => { state.filters.epics = next; paint(); savePrefs(); },
      onClear:  ()    => { state.filters = { ...DEFAULT_FILTERS }; searchInput.value = ''; updatePriorityChips(pri, { active: [] }); paint(); savePrefs(); },
    }));

    // 5) Group + render columns
    const groupKeyArg = state.filters.group_by === 'phase' ? phasesArr.map(p => p.id) : undefined;
    const groups = groupTasks(sorted, state.filters.group_by, groupKeyArg);
    boardGrid.className = 'kanban-board-grid ' + state.filters.group_by;
    boardGrid.replaceChildren();

    for (const g of groups) {
      const col = document.createElement('div');
      col.className = 'kanban-col';
      const head = document.createElement('div');
      head.className = 'kanban-col-head ' + (state.filters.group_by === 'status' ? g.key : '');
      head.innerHTML = `<span class="dot"></span><span class="lbl">${escapeHtml(state.filters.group_by === 'status' ? STATUS_LABELS[g.key] : g.label)}</span><span class="tnum">${g.tasks.length}</span>`;
      col.appendChild(head);

      if (!g.tasks.length) {
        const empty = document.createElement('div');
        empty.className = 'kanban-col-empty';
        empty.textContent = '— filtered out —';
        col.appendChild(empty);
      } else {
        for (const t of g.tasks) {
          col.appendChild(renderCard({
            task: t,
            density: state.density,
            epicColors,
            autoState: store.getAutoState(),
            groupBy: state.filters.group_by,
          }));
        }
      }
      boardGrid.appendChild(col);
    }
  }

  // Persist filter changes via debounced prefs.patch
  function savePrefs() {
    prefs.patch({ kanban: { filters: state.filters } });
  }

  // ──────────────────────────────────────────────────────────────
  // Subscriptions: backlog & autoState
  // ──────────────────────────────────────────────────────────────
  const unsubBacklog = store.subscribe('backlog', () => paint());
  const unsubAuto    = store.subscribe('autoState', (auto) => {
    updateAutoModeStrip(strip, {
      autoState: auto,
      backlog:   store.getBacklog(),
      onViewAll: () => { location.hash = '#/auto'; },
    });
    paint(); // re-render cards so live-blocks attach/detach
  });

  // Initial paint
  paint();

  // Cleanup
  return () => {
    if (searchTimer) clearTimeout(searchTimer);
    unsubBacklog();
    unsubAuto();
    destroyAutoModeStrip(strip);
  };
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
