// Kanban screen — full implementation.
// Mounts the page-head, phase strip, epic chips, board surface.
// Subscribes to store(backlog) and store(prefs); all writes go through prefs.patch(...).

import { renderCard }                        from '../components/card.js';
import { priorityChips }                     from '../components/priority-chips.js';
import { phaseStrip }                        from '../components/phase-strip.js';
import { epicChips }                         from '../components/epic-chips.js';
import { openEpicOptions }                   from '../components/epic-dropdown.js';
import { chipRow }                           from '../components/chips.js';
import { icon }                              from '../components/icon.js';
import { chipClickNext }                     from '../util/chip-toggle.js';
import { applyFilters, sortTasks, groupTasks, epicsForPhase, STATUS_LABELS, clusterBundles, countOpen, OPEN_COUNT_HINT } from '../lib/filters.js';
import { renderBundleFrame } from '../components/bundle-frame.js';
import { epicIndex }                         from '../lib/epics.js';
import { claimTopbar, claimTopbarPrimary, setTopbarCount, tmAction, tmSearch, tmSegmented } from '../lib/topbar.js';
import { pluralize } from '../util/pluralize.js';
import { emptyState } from '../components/empty-state.js';
import { openTaskCreateModal } from '../components/edit/task-actions.js';

export const meta = { title: 'Kanban', icon: '▦', sidebarKey: 'kanban' };

const DEFAULT_FILTERS = {
  priorities: [],
  epics: [],
  areas: [],
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


  // Layout
  const page = document.createElement('div');
  page.className = 'kanban-page';

  // 1) Page header — inject into topbar-actions slot
  const head = claimTopbar();
  setTopbarCount('… tasks');
  claimTopbarPrimary()?.append(tmAction({
    icon: 'plus', label: 'Task', variant: 'primary', title: 'Add task',
    onClick: () => openTaskCreateModal({ store, api }),
  }));

  // Search
  const { el: search, input: searchInput } = tmSearch({
    placeholder: 'Find… (prefix ! to exclude)',
    ariaLabel: 'Find tasks',
    value: state.filters.search || '',
    onInput: (value) => {
      state.filters.search = value;
      paint(); savePrefs();
    },
  });
  // Filters are reset from the board too; that path empties the field without an input event.
  const resetSearchField = () => {
    searchInput.value = '';
    search.classList.remove('tm-search--has-value');
  };
  head.appendChild(search);

  // Row 2: each control is a direct child of #topbar-actions so Filters can park them one by one.
  const dens = tmSegmented([
    { key: 'minimal', label: 'Minimal', title: 'Minimal cards' },
    { key: 'full', label: 'Full', title: 'Full cards' },
  ], {
    value: state.density,
    onChange: (k) => { state.density = k; paint(); prefs.patch({ card_density: k }); },
  });
  dens.setAttribute('role', 'group');
  dens.setAttribute('aria-label', 'Card density');
  head.appendChild(dens);

  // A labelled select: the visible label names the control, the chevron is the shared edit-field one.
  const field = (labelText, options, current, onChange) => {
    const label = document.createElement('label');
    label.className = 'kanban-field';
    const text = document.createElement('span');
    text.className = 'kanban-field__label';
    text.textContent = labelText;
    const wrap = document.createElement('span');
    wrap.className = 'ef-select';
    const select = document.createElement('select');
    select.className = 'ef-enum-select';
    for (const [value, name] of options) {
      const o = document.createElement('option');
      o.value = value; o.textContent = name;
      if (value === current) o.selected = true;
      select.appendChild(o);
    }
    select.addEventListener('change', () => onChange(select.value));
    wrap.append(select, icon('chevron', { size: 16 }));
    label.append(text, wrap);
    return label;
  };

  head.appendChild(field('Group',
    [['status', 'Status'], ['phase', 'Phase'], ['epic', 'Epic'], ['area', 'Area']],
    state.filters.group_by,
    (v) => { state.filters.group_by = v; paint(); savePrefs(); }));

  const SORT_OPTS = [
    ['priority:desc', 'Priority: high first'],
    ['priority:asc',  'Priority: low first'],
    ['size:desc',     'Size: largest first'],
    ['size:asc',      'Size: smallest first'],
    ['created:desc',  'Created: newest first'],
    ['created:asc',   'Created: oldest first'],
    ['started:desc',  'Started: newest first'],
    ['started:asc',   'Started: oldest first'],
    ['touched:desc',  'Touched: newest first'],
    ['touched:asc',   'Touched: oldest first'],
  ];
  head.appendChild(field('Sort', SORT_OPTS,
    `${state.filters.sort?.by || 'priority'}:${state.filters.sort?.dir || 'desc'}`,
    (v) => { const [by, dir] = v.split(':'); state.filters.sort = { by, dir }; paint(); savePrefs(); }));

  // 3) Unified filter bar — phases on top row, epics on bottom row
  const filterBar = document.createElement('div');
  filterBar.className = 'kanban-filterbar';

  // Built once at mount so focus and an open More survive every paint; paint only updates it.
  // Picking the active value again returns to all phases; paint() prunes epics that don't apply.
  const strip = phaseStrip({
    onSelect: (key) => {
      state.filters.phase = (state.filters.phase === key) ? '__all__' : key;
      paint(); savePrefs();
    },
  });
  const phaseHost = document.createElement('div');
  phaseHost.className = 'kanban-filterbar-row phase';
  phaseHost.appendChild(strip.el);
  filterBar.appendChild(phaseHost);

  // Built once at mount like the strip; paint() only calls update().
  const filters = document.createElement('div');
  filters.className = 'kanban-filters';
  const priRow = chipRow({
    label: 'Priority', chips: priorityChips(state.filters.priorities), hint: OPEN_COUNT_HINT,
    onToggle: (value, ev) => { state.filters.priorities = chipClickNext(ev, state.filters.priorities, value); paint(); savePrefs(); },
  });
  priRow.el.classList.add('kanban-filters__priority');
  const epicRow = chipRow({
    label: 'Epic', chips: [], hint: OPEN_COUNT_HINT,
    onToggle: (value, ev) => {
      state.filters.epics = value === '__all__' ? [] : chipClickNext(ev, state.filters.epics, value);
      paint(); savePrefs();
    },
  });
  epicRow.el.classList.add('kanban-filters__epic');
  state.showArchivedEpics = false;
  let epicOptions = null;     // the open popover handle, so cleanup can close it
  let epicOptionsData = { epics: [], counts: new Map() };
  const optionsBtn = document.createElement('button');
  optionsBtn.type = 'button';
  optionsBtn.className = 'btn btn--ghost btn--icon btn--sm epic-options-btn';
  optionsBtn.setAttribute('aria-label', 'Epic options');
  optionsBtn.title = 'Epic options';
  optionsBtn.appendChild(icon('sliders'));
  optionsBtn.addEventListener('click', () => {
    // A second press closes the popover rather than reopening it (and losing its search text and scroll).
    if (epicOptions?.isOpen()) { epicOptions.close('toggle', { returnFocus: true }); epicOptions = null; return; }
    epicOptions = openEpicOptions({
      anchor: optionsBtn,
      epics: epicOptionsData.epics,
      counts: epicOptionsData.counts,
      pinnedIds: state.pinnedEpics,
      sort: state.epicSort,
      showArchived: state.showArchivedEpics,
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
      onShowArchived: (on) => { state.showArchivedEpics = !!on; paint(); },
    });
  });
  const clearBtn = document.createElement('button');
  clearBtn.type = 'button';
  clearBtn.className = 'btn btn--ghost btn--sm kanban-clear';
  clearBtn.hidden = true;
  clearBtn.append(icon('dismiss', { size: 14 }), document.createTextNode('Clear filters'));
  clearBtn.addEventListener('click', () => { clearAllFilters(); searchInput.focus(); });
  filters.append(priRow.el, epicRow.el, optionsBtn, clearBtn);
  filterBar.appendChild(filters);

  function clearAllFilters() {
    state.filters = { ...DEFAULT_FILTERS, group_by: state.filters.group_by, sort: state.filters.sort };
    state.collapsed = new Set();
    resetSearchField();
    prefs.patch({ kanban: { collapsed_columns: [] } });
    paint(); savePrefs();
  }

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
    const index = epicIndex(epicsArr);

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

    // Bundle totals across all columns (used to render "N of M here" in bundle frames)
    const bundleTotals = {};
    for (const t of sorted) {
      if (t.bundle) bundleTotals[t.bundle] = (bundleTotals[t.bundle] || 0) + 1;
    }

    // 2) Row-1 count: "· m visible" only while a filter or search narrows the board.
    const hasFilters = !!(state.filters.priorities?.length || state.filters.epics?.length ||
      state.filters.areas?.length || state.filters.search || (state.filters.phase && state.filters.phase !== '__all__'));
    setTopbarCount(`${tasks.length} ${pluralize(tasks.length, 'task', 'tasks')}${hasFilters ? ` · ${filtered.length} visible` : ''}`);

    // 3) Phase strip data — sort by order so non-sequential insertion in
    // the YAML (e.g. phase "1.5" added after "2") doesn't scramble the strip.
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
      return { id: ph.id, name: ph.name || ph.id, status: stat, done, total, archived_reason: ph.archived_reason };
    });
    strip.update({ phases: phaseRows, active: state.filters.phase });

    // 4) Epic chips data — when a phase is active, scope to epics that have tasks in that phase.
    const tasksInPhase = phaseScoped
      ? (phaseFilter === '__orphans__' ? tasks.filter(t => !t.phase) : tasks.filter(t => t.phase === phaseFilter))
      : tasks;
    const filterCount =
      state.filters.priorities.length +
      state.filters.epics.length +
      (state.filters.areas?.length || 0) +
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

    // Counts are open tasks in the current phase scope, the same map for chips and Epic options.
    const epicCounts = countOpen(tasksInPhase, 'epic');
    const epicsForChips = epicsVisible.map(ep => ({
      id: ep.id,
      name: ep.name || ep.id,
      status: ep.status || 'active',
      last_referenced: ep.last_referenced,
      swatch: index.get(ep.id)?.swatch,
    }));
    epicOptionsData = { epics: epicsForChips, counts: epicCounts };
    priRow.update(priorityChips(state.filters.priorities, countOpen(tasksInPhase, 'priority')));
    epicRow.update(epicChips({
      epics: epicsForChips,
      selectedIds: state.filters.epics,
      pinnedIds: state.pinnedEpics,
      counts: epicCounts,
      sort: state.epicSort,
      showArchived: state.showArchivedEpics,
    }));
    clearBtn.hidden = filterCount === 0;

    // 5) Group + render columns — use phasesOrdered so swimlanes respect logical order
    const groupKeyArg = state.filters.group_by === 'phase' ? phasesOrdered.map(p => p.id) : undefined;
    const groups = groupTasks(sorted, state.filters.group_by, groupKeyArg);
    boardGrid.className = 'kanban-board-grid ' + state.filters.group_by;
    boardGrid.replaceChildren();

    // When the whole board is empty (all tasks filtered out), show count context
    // only in the first non-collapsed column so the message appears once.
    const allEmpty = filtered.length === 0 && hasFilters;
    let countShown = false;

    for (const g of groups) {
      const col = document.createElement('div');
      col.className = 'kanban-col';
      const head = document.createElement('div');
      head.className = 'kanban-col-head ' + (state.filters.group_by === 'status' ? g.key : '');
      head.innerHTML = `<span class="dot"></span><span class="lbl">${escapeHtml(state.filters.group_by === 'status' ? STATUS_LABELS[g.key]
        : state.filters.group_by === 'epic' ? (epicsArr.find((e) => e.id === g.key)?.name || g.label) : g.label)}</span><span class="tnum">${g.tasks.length}</span>`;
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
          if (state.filters.areas?.length) filterParts.push(`area: ${state.filters.areas.join(', ')}`);
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
        for (const item of clusterBundles(g.tasks)) {
          if (item.type === 'bundle') {
            colBody.appendChild(renderBundleFrame(
              { slug: item.slug, tasks: item.tasks, total: bundleTotals[item.slug] },
              { density: state.density, epicIndex: index, groupBy: state.filters.group_by }));
          } else {
            colBody.appendChild(renderCard({ task: item.task, density: state.density, epicIndex: index, groupBy: state.filters.group_by }));
          }
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
    unsubBacklog();
    resizeObs.disconnect();
    strip.destroy();
    priRow.destroy();
    epicRow.destroy();
    epicOptions?.close?.();
  };
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
