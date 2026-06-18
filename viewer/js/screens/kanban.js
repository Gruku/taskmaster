// Kanban screen — full implementation.
// Mounts the page-head, phase stepper, epic chips, board surface.
// Subscribes to store(backlog) and store(prefs); all writes go through prefs.patch(...).

import { renderCard }                        from '../components/card.js';
import { renderPriorityChips,
         updatePriorityChips }               from '../components/priority-chips.js';
import { renderPhaseStepper }                from '../components/phase-stepper.js';
import { renderEpicChips }                   from '../components/epic-chips.js';
import { applyFilters, sortTasks, groupTasks, epicsForPhase, STATUS_LABELS } from '../lib/filters.js';
import { assignEpicColors }                  from '../lib/epics.js';
import { countActiveTasksByEpic, rankEpics } from '../lib/epic-ranking.js';
import { claimTopbar, tmAction } from '../lib/topbar.js';
import { pluralize } from '../util/pluralize.js';
import { emptyState } from '../components/empty-state.js';
import { openTaskCreateModal } from '../components/edit/task-actions.js';

export const meta = { title: 'Kanban', icon: '▦', sidebarKey: 'kanban' };

const DEFAULT_FILTERS = {
  priorities: [],
  epics: [],
  phase: '__all__',
  group_by: 'status',
  sort: { by: 'priority', dir: 'desc' },
  search: '',
};

export async function mount(root, { store, api, prefs }) {
  // ──────────────────────────────────────────────────────────────
  // Local state — sourced from prefs but mutated by UI events.
  // Persisted via prefs.patch({...}) (debounced).
  // ──────────────────────────────────────────────────────────────
  const persisted = (store.getPrefs() && store.getPrefs().kanban && store.getPrefs().kanban.filters) || {};
  const state = {
    filters: { ...DEFAULT_FILTERS, ...persisted },
    density: (store.getPrefs() && store.getPrefs().card_density) || 'full',
    collapsed: new Set((store.getPrefs() && store.getPrefs().kanban && store.getPrefs().kanban.collapsed_columns) || []),
  };

  // Pinned epics (in order) and dropdown sort key — persisted under prefs.kanban
  const persistedKan = (store.getPrefs() && store.getPrefs().kanban) || {};
  state.pinnedEpics = Array.isArray(persistedKan.pinnedEpics) ? persistedKan.pinnedEpics.slice() : [];
  state.epicSort    = (typeof persistedKan.epicSort === 'string') ? persistedKan.epicSort : 'count';

  // Carousel offsets that survive re-renders (filter changes, backlog refresh).
  const stepperViewState = { pastOffset: 0, futureOffset: 0 };

  // Layout
  const page = document.createElement('div');
  page.className = 'kanban-page';

  // 1) Page header — inject into topbar-actions slot
  const head = claimTopbar();

  const subcount = document.createElement('span');
  subcount.className = 'tm-subcount';
  subcount.textContent = '… tasks';
  head.appendChild(subcount);

  // Search
  const search = document.createElement('div');
  search.className = 'tm-search';
  search.innerHTML = `<span class="icon">⌕</span><input placeholder="Find… (prefix ! to exclude)" /><span class="cmp-kbd">⌘K</span>`;
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

  // Density toggle (▤ minimal / ▦ full)
  const dens = document.createElement('div');
  dens.className = 'tm-segmented tm-segmented--icon';
  for (const k of ['minimal', 'full']) {
    const b = document.createElement('button');
    b.type = 'button';
    b.dataset.key = k;
    b.title = k === 'minimal' ? 'Minimal cards' : 'Full cards';
    b.setAttribute('aria-label', b.title);
    b.textContent = k === 'minimal' ? '▤' : '▦';
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

  // + Task button — uses shared .tm-action--primary (Layer 3 unifies primary actions).
  const addBtn = tmAction({
    icon: '+', label: 'Task', variant: 'primary', title: 'Add task',
    onClick: () => openTaskCreateModal({ store, api }),
  });
  right.appendChild(addBtn);

  head.appendChild(right);

  // 3) Unified filter bar — phases on top row, epics on bottom row
  const filterBar = document.createElement('div');
  filterBar.className = 'kanban-filterbar';

  const stepperHost = document.createElement('div');
  stepperHost.className = 'kanban-filterbar-row phase';
  filterBar.appendChild(stepperHost);

  const epicHost = document.createElement('div');
  epicHost.className = 'kanban-filterbar-row epic';
  filterBar.appendChild(epicHost);

  page.appendChild(filterBar);

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

    // Prune persisted/stale epic selections that don't apply to the active
    // phase scope. Catches initial mount with stale prefs as well as backlog
    // changes that remove the last task linking an epic to the active phase.
    const phaseFilter = state.filters.phase;
    const phaseScoped = phaseFilter && phaseFilter !== '__all__';
    if (phaseScoped && state.filters.epics.length) {
      const allowed = new Set(epicsForPhase(epicsArr, tasks, phaseFilter).map(e => e.id));
      const pruned = state.filters.epics.filter(id => allowed.has(id));
      if (pruned.length !== state.filters.epics.length) {
        state.filters.epics = pruned;
        savePrefs();
      }
    }

    // 1) Apply filters
    const filtered = applyFilters(tasks, state.filters);
    const sorted   = sortTasks(filtered, state.filters.sort);

    // 2) Subcount
    subcount.textContent = `${tasks.length} ${pluralize(tasks.length, 'task', 'tasks')} · ${filtered.length} visible`;

    // 3) Phase stepper data — sort by order so non-sequential insertion in
    // the YAML (e.g. phase "1.5" added after "2") doesn't scramble the stepper.
    const phasesOrdered = phasesArr.slice().sort((a, b) => {
      const oa = a.order != null ? a.order : 999;
      const ob = b.order != null ? b.order : 999;
      return oa - ob;
    });
    const phaseRows = phasesOrdered.map(ph => {
      const total = tasks.filter(t => t.phase === ph.id).length;
      const done  = tasks.filter(t => t.phase === ph.id && t.status === 'done').length;
      let stat = (ph.status || '').toLowerCase();
      if (!stat) stat = (done >= total && total > 0) ? 'done' : (done > 0 ? 'active' : 'future');
      return { id: ph.id, name: ph.name || ph.id, status: stat, done, total };
    });
    stepperHost.replaceChildren(renderPhaseStepper({
      phases: phaseRows,
      active: state.filters.phase,
      viewState: stepperViewState,
      // Clicking the currently-selected phase clears the filter back to all-phases.
      // paint() will prune any selected epics that don't apply to the new phase.
      onSelect: (key) => {
        const next = (state.filters.phase === key) ? '__all__' : key;
        state.filters.phase = next;
        paint(); savePrefs();
      },
    }));

    // 4) Epic chips data — when a phase is active, scope to epics that have tasks in that phase.
    const tasksInPhase = phaseScoped
      ? (phaseFilter === '__orphans__' ? tasks.filter(t => !t.phase) : tasks.filter(t => t.phase === phaseFilter))
      : tasks;
    const filterCount =
      state.filters.priorities.length +
      state.filters.epics.length +
      (state.filters.phase && state.filters.phase !== '__all__' ? 1 : 0) +
      (state.filters.search ? 1 : 0);

    // (a) Phase-scoped epic visibility: when a phase is active, restrict to epics
    // that have ≥1 task in that phase. Pinned epics are always included so explicit
    // user intent survives phase switches. (v3-polish-047)
    const epicsVisible = phaseScoped
      ? (() => {
          const inPhaseIds = new Set(epicsForPhase(epicsArr, tasks, phaseFilter).map(e => e.id));
          const pinnedSet  = new Set(state.pinnedEpics);
          return epicsArr.filter(e => inPhaseIds.has(e.id) || pinnedSet.has(e.id));
        })()
      : epicsArr;

    // (c) Sort by phase task count: when a phase is active, use phase-scoped counts
    // for rankEpics so ordering reflects the current view's volume, not global totals.
    // Global counts are preserved as a fallback signal for no-phase mode. (v3-polish-047)
    const activeCounts = countActiveTasksByEpic(phaseScoped ? tasksInPhase : tasks);
    const ranked = rankEpics(epicsVisible.map(ep => ({
      id: ep.id,
      name: ep.name || ep.id,
      color: epicColors[ep.id],
      status: ep.status || 'active',
      last_referenced: ep.last_referenced,
      count: tasksInPhase.filter(t => t.epic === ep.id).length,
    })), activeCounts);

    epicHost.replaceChildren(renderEpicChips({
      epics: ranked,
      selectedIds: state.filters.epics,
      pinnedIds: state.pinnedEpics,
      activeCounts,
      sort: state.epicSort,
      filterCount,
      onToggleEpics: (next) => { state.filters.epics = next; paint(); savePrefs(); },
      onPinToggle: (id, pinned) => {
        const list = state.pinnedEpics.filter(x => x !== id);
        if (pinned) list.push(id);
        state.pinnedEpics = list;
        prefs.patch({ kanban: { pinnedEpics: list } });
        paint();
      },
      onSortChange: (next) => {
        state.epicSort = next;
        prefs.patch({ kanban: { epicSort: next } });
        paint();
      },
      onClearFilters: () => {
        state.filters = { ...DEFAULT_FILTERS };
        state.collapsed = new Set();
        searchInput.value = '';
        updatePriorityChips(pri, { active: [] });
        prefs.patch({ kanban: { collapsed_columns: [] } });
        paint(); savePrefs();
      },
    }));

    // 5) Group + render columns — use phasesOrdered so swimlanes respect logical order
    const groupKeyArg = state.filters.group_by === 'phase' ? phasesOrdered.map(p => p.id) : undefined;
    const groups = groupTasks(sorted, state.filters.group_by, groupKeyArg);
    boardGrid.className = 'kanban-board-grid ' + state.filters.group_by;
    boardGrid.replaceChildren();

    const hasFilters = !!(state.filters.priorities?.length || state.filters.epics?.length ||
      state.filters.search || (state.filters.phase && state.filters.phase !== '__all__'));
    // When the whole board is empty (all tasks filtered out), show count context
    // only in the first non-collapsed column so the message appears once.
    const allEmpty = filtered.length === 0 && hasFilters;
    let countShown = false;

    const clearAllFilters = () => {
      state.filters = { ...DEFAULT_FILTERS };
      state.collapsed = new Set();
      searchInput.value = '';
      updatePriorityChips(pri, { active: [] });
      prefs.patch({ kanban: { collapsed_columns: [] } });
      paint(); savePrefs();
    };

    for (const g of groups) {
      const col = document.createElement('div');
      col.className = 'kanban-col';
      const head = document.createElement('div');
      head.className = 'kanban-col-head ' + (state.filters.group_by === 'status' ? g.key : '');
      head.innerHTML = `<span class="dot"></span><span class="lbl">${escapeHtml(state.filters.group_by === 'status' ? STATUS_LABELS[g.key] : g.label)}</span><span class="tnum">${g.tasks.length}</span>`;
      const toggleBtn = document.createElement('button');
      toggleBtn.type = 'button';
      toggleBtn.className = 'kanban-col-toggle';
      const isCollapsed = state.collapsed.has(g.key);
      toggleBtn.title = isCollapsed ? 'Expand' : 'Collapse';
      toggleBtn.textContent = isCollapsed ? '›' : '‹';
      const toggleCollapsed = () => {
        if (state.collapsed.has(g.key)) state.collapsed.delete(g.key);
        else state.collapsed.add(g.key);
        prefs.patch({ kanban: { collapsed_columns: [...state.collapsed] } });
        const nowCollapsed = state.collapsed.has(g.key);
        col.classList.toggle('collapsed', nowCollapsed);
        toggleBtn.textContent = nowCollapsed ? '›' : '‹';
        toggleBtn.title = nowCollapsed ? 'Expand' : 'Collapse';
        updateGridTemplate();
      };
      toggleBtn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        toggleCollapsed();
      });
      // Click anywhere on a collapsed column body re-expands it.
      col.addEventListener('click', () => {
        if (col.classList.contains('collapsed')) toggleCollapsed();
      });
      head.appendChild(toggleBtn);
      if (isCollapsed) col.classList.add('collapsed');
      col.appendChild(head);

      const colBody = document.createElement('div');
      colBody.className = 'kanban-col-body';
      if (!g.tasks.length) {
        // When the whole board is empty due to filters, show a count message in
        // the first non-collapsed column so the user knows how many tasks are hidden.
        // Other columns just show 'Nothing here' to avoid repetition.
        if (allEmpty && !countShown && !isCollapsed) {
          countShown = true;
          const filterParts = [];
          if (state.filters.search) filterParts.push(`search "${state.filters.search}"`);
          if (state.filters.priorities?.length) filterParts.push(`priority: ${state.filters.priorities.join(', ')}`);
          if (state.filters.epics?.length) filterParts.push(`epic: ${state.filters.epics.join(', ')}`);
          if (state.filters.phase && state.filters.phase !== '__all__') filterParts.push(`phase: ${state.filters.phase}`);
          const hidden = tasks.length;
          const filterDesc = filterParts.length ? filterParts.join(' · ') : 'active filters';
          colBody.appendChild(emptyState({
            headline: `0 of ${tasks.length} ${pluralize(tasks.length, 'task', 'tasks')} match`,
            hint: `${filterDesc} — ${hidden} ${pluralize(hidden, 'task', 'tasks')} hidden`,
            action: { label: 'Clear filters', onClick: clearAllFilters },
          }));
        } else {
          // Honest text: only call out filters if any are active. Otherwise the
          // column is just empty (e.g. nothing in "Done" yet).
          colBody.appendChild(emptyState({
            headline: hasFilters ? 'No tasks match your filters' : 'Nothing here',
          }));
        }
      } else {
        for (const t of g.tasks) {
          colBody.appendChild(renderCard({
            task: t,
            density: state.density,
            epicColors,
            groupBy: state.filters.group_by,
          }));
        }
      }
      col.appendChild(colBody);
      boardGrid.appendChild(col);
    }
    updateGridTemplate();
  }

  function updateGridTemplate(animate = true) {
    // On mobile (< 768px = --bp-md), CSS handles the stacked layout;
    // skip all JS width logic so inline styles don't fight the media query.
    if (window.matchMedia('(max-width: 768px)').matches) return;
    const cols = Array.from(boardGrid.querySelectorAll(':scope > .kanban-col'));
    if (!cols.length) return;
    const isInitial = cols.some(c => !c.style.width || c.style.width === '0px');
    const skipAnim = !animate || isInitial;
    const total = boardGrid.clientWidth;
    const gapPx = parseFloat(getComputedStyle(boardGrid).gap) || 0;
    const totalGap = gapPx * (cols.length - 1);
    const collapsedWidth = 66;
    const collapsedCount = cols.filter(c => c.classList.contains('collapsed')).length;
    const expandedCount = cols.length - collapsedCount;
    const expandedWidth = expandedCount > 0
      ? Math.max(0, (total - totalGap - collapsedCount * collapsedWidth) / expandedCount)
      : 0;
    if (skipAnim) boardGrid.classList.add('no-anim');
    // Floor each width so the running sum never exceeds `total - totalGap`.
    let allotted = 0;
    for (let i = 0; i < cols.length; i++) {
      const c = cols[i];
      const isLast = i === cols.length - 1;
      let w;
      if (c.classList.contains('collapsed')) {
        w = collapsedWidth;
      } else if (isLast) {
        // Give the last expanded column the leftover so rounding never overflows.
        const remainingExpanded = cols.slice(i).filter(x => !x.classList.contains('collapsed')).length;
        const remainingCollapsed = cols.slice(i).filter(x => x.classList.contains('collapsed')).length;
        const usedAfter = remainingCollapsed * collapsedWidth;
        const stillFor = total - totalGap - allotted - usedAfter;
        w = Math.max(0, Math.floor(stillFor / Math.max(1, remainingExpanded)));
      } else {
        w = Math.floor(expandedWidth);
      }
      c.style.width = w + 'px';
      allotted += w;
    }
    if (skipAnim) {
      // force reflow then re-enable transitions
      void boardGrid.offsetHeight;
      boardGrid.classList.remove('no-anim');
    }
  }

  // Persist filter changes via debounced prefs.patch
  function savePrefs() {
    prefs.patch({ kanban: { filters: state.filters } });
  }

  // ──────────────────────────────────────────────────────────────
  // Subscriptions: backlog
  // ──────────────────────────────────────────────────────────────
  const unsubBacklog = store.subscribe('backlog', () => paint());

  // Initial paint
  paint();

  // Recompute column widths on viewport resize without animation.
  const resizeObs = new ResizeObserver(() => updateGridTemplate(false));
  resizeObs.observe(boardGrid);

  // Cleanup
  return () => {
    if (searchTimer) clearTimeout(searchTimer);
    unsubBacklog();
    resizeObs.disconnect();
  };
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
