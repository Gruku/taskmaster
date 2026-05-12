import { issueCard, issueRow } from '../components/issue-card.js';
import { severityLabel } from '../util/severity-label.js';
import { pluralize } from '../util/pluralize.js';
import { emptyState } from '../components/empty-state.js';
import * as api from '../api.js';
import { claimTopbar, tmSubcount, tmSearch, tmSegmented, tmAction } from '../lib/topbar.js';
import { chipClickNext, CHIP_CLICK_HINT } from '../util/chip-toggle.js';
import { groupByStatus, groupBySeverity } from '../util/issues-grouping.js';

export const meta = { title: 'Issues', icon: '!', sidebarKey: 'issues' };

const SEVERITIES = ['Critical', 'High', 'Medium', 'Low'];

function _renderSeverityChips(filters, counts, activeSet, onChange) {
  // Remove only our own chips — the "Severity:" label sibling is preserved.
  filters.querySelectorAll('.issues__sev-chip').forEach(el => el.remove());

  const allChip = document.createElement('button');
  allChip.type = 'button';
  allChip.className = 'issues__sev-chip issues__sev-chip--all' + (activeSet.size === 0 ? ' is-active' : '');
  allChip.setAttribute('role', 'button');
  allChip.setAttribute('aria-pressed', String(activeSet.size === 0));
  allChip.title = 'Show all severities';
  allChip.innerHTML = `<span class="lbl">All</span><span class="count">${counts.__all}</span>`;
  allChip.addEventListener('click', () => onChange(new Set()));
  filters.appendChild(allChip);

  for (const sev of SEVERITIES) {
    const c = document.createElement('button');
    c.type = 'button';
    c.className = 'issues__sev-chip' + (activeSet.has(sev) ? ' is-active' : '');
    c.dataset.sev = sev;
    c.setAttribute('role', 'button');
    c.setAttribute('aria-pressed', String(activeSet.has(sev)));
    c.title = CHIP_CLICK_HINT;
    c.innerHTML = `
      <span class="dot" aria-hidden="true"></span>
      <span class="lbl">${sev}</span>
      <span class="count">${counts[sev] || 0}</span>`;
    c.addEventListener('click', (ev) => {
      const current = [...activeSet];
      const next = new Set(chipClickNext(ev, current, sev));
      onChange(next);
    });
    filters.appendChild(c);
  }
}

export async function mount(root, { store, prefs }) {
  // Gotcha: `prefs` is the patch helper, NOT the data object.
  // Read persisted state from store.getPrefs(), then use prefs.patch() to save.
  root.innerHTML = '';
  const screen = document.createElement('section');
  screen.className = 'issues';

  // ---- topbar (#topbar-actions)
  const topbar = claimTopbar();
  const subcount = tmSubcount('… issues');
  const searchBuilt = tmSearch({
    placeholder: 'Search issues…',
    onInput: (v) => { searchTerm = v.trim().toLowerCase(); render(); },
  });

  // Severity chip row stays as a screen-local element (filters, not a top-level
  // control) but we move it into the topbar visually so it sits with the rest.
  const filters = document.createElement('div');
  filters.className = 'tm-chip-row issues__filters';
  // Persistent label — _renderSeverityChips() removes only its own chips, keeping this label.
  const sevLabel = document.createElement('span');
  sevLabel.className = 'issues__chip-row-label';
  sevLabel.textContent = 'Severity:';
  filters.appendChild(sevLabel);
  // Chips are built (and rebuilt on each render) by _renderSeverityChips() below.

  // Fix: read persisted view from store.getPrefs(), not prefs.getPrefs()
  const initialView = (store.getPrefs()?.screens?.issues?.view) || 'A';
  const toggle = tmSegmented(
    [
      { key: 'A', label: 'Hybrid' },
      { key: 'B', label: 'Status' },
      { key: 'D', label: 'Severity' },
      { key: 'C', label: 'List' },
    ],
    { value: initialView, onChange: setView },
  );
  const newBtn = tmAction({
    icon: '+', label: 'Issue', variant: 'primary',
    title: 'New issue — coming soon',
    disabled: true,
  });

  // Component chip-row (populated dynamically once issues load).
  const compRow = document.createElement('div');
  compRow.className = 'tm-chip-row issues__components';
  // Persistent label — _renderComponentChips() removes only its own chips, keeping this label.
  const compLabel = document.createElement('span');
  compLabel.className = 'issues__chip-row-label';
  compLabel.textContent = 'Components:';
  compRow.appendChild(compLabel);

  topbar?.appendChild(subcount);
  topbar?.appendChild(searchBuilt.el);
  topbar?.appendChild(filters);
  topbar?.appendChild(compRow);
  topbar?.appendChild(toggle);
  topbar?.appendChild(newBtn);

  // ---- columns + resolved shelf
  const columns = document.createElement('div');
  columns.className = 'issues__columns';
  const investigatingCol = document.createElement('div');
  investigatingCol.className = 'issues__column';
  investigatingCol.innerHTML = `
  <h2 class="issues__column-header">
    <span class="issues__column-name">Investigating</span>
    <span class="issues__column-tagline">— actively under triage</span>
    <span class="issues__column-count" data-count></span>
  </h2>`;
  const investigatingList = document.createElement('div');
  investigatingCol.appendChild(investigatingList);

  const openCol = document.createElement('div');
  openCol.className = 'issues__column';
  openCol.innerHTML = `
  <h2 class="issues__column-header">
    <span class="issues__column-name">Open</span>
    <span class="issues__column-tagline">— confirmed, not yet started</span>
    <span class="issues__column-count" data-count></span>
  </h2>`;
  const openList = document.createElement('div');
  openCol.appendChild(openList);

  columns.appendChild(investigatingCol);
  columns.appendChild(openCol);
  screen.appendChild(columns);

  // ---- Status kanban shell (view B) — built once, toggled per render
  const kanban = document.createElement('div');
  kanban.className = 'issues__columns issues__columns--kanban';
  kanban.style.display = 'none';
  screen.appendChild(kanban);

  const KANBAN_STATUS_COLS = [
    { key: 'open',          label: 'Open',          tagline: '— confirmed, not yet started', density: 'card' },
    { key: 'investigating', label: 'Investigating',  tagline: '— actively under triage',     density: 'card' },
    { key: 'fixed',         label: 'Fixed',          tagline: '— resolved',                  density: 'row'  },
    { key: 'wontfix',       label: 'Wontfix',        tagline: '— closed without fix',        density: 'row'  },
  ];

  function _buildKanbanCol({ key, label, tagline }) {
    const col = document.createElement('div');
    col.className = 'issues__kanban-col';
    col.dataset.key = key;
    col.innerHTML = `
      <h2 class="issues__column-header">
        <span class="issues__column-name">${label}</span>
        <span class="issues__column-tagline">${tagline}</span>
        <span class="issues__column-count" data-count></span>
      </h2>`;
    const body = document.createElement('div');
    body.className = 'issues__kanban-col-body';
    col.appendChild(body);
    return { col, body };
  }

  const statusKanbanCols = {};
  for (const desc of KANBAN_STATUS_COLS) {
    const { col, body } = _buildKanbanCol(desc);
    statusKanbanCols[desc.key] = { col, body, density: desc.density };
    kanban.appendChild(col);
  }

  const KANBAN_SEVERITY_COLS = [
    { key: 'Critical', label: 'Critical', tagline: '— must-fix' },
    { key: 'High',     label: 'High',     tagline: '— prioritize' },
    { key: 'Medium',   label: 'Medium',   tagline: '— upcoming' },
    { key: 'Low',      label: 'Low',      tagline: '— backlog' },
  ];

  const sevKanbanCols = {};
  for (const desc of KANBAN_SEVERITY_COLS) {
    const { col, body } = _buildKanbanCol(desc);
    col.style.display = 'none';
    sevKanbanCols[desc.key] = { col, body };
    kanban.appendChild(col);
  }

  function _showKanbanCols(active /* 'status' | 'severity' */) {
    for (const k of Object.keys(statusKanbanCols)) {
      statusKanbanCols[k].col.style.display = (active === 'status') ? '' : 'none';
    }
    for (const k of Object.keys(sevKanbanCols)) {
      sevKanbanCols[k].col.style.display = (active === 'severity') ? '' : 'none';
    }
  }

  const resolvedShelf = document.createElement('section');
  resolvedShelf.className = 'issues__resolved-shelf';
  const resolvedHeader = document.createElement('header');
  resolvedHeader.className = 'issues__resolved-header';
  const resolvedList = document.createElement('div');
  resolvedList.className = 'issues__resolved-list';
  resolvedList.hidden = true;
  resolvedHeader.setAttribute('role', 'button');
  resolvedHeader.setAttribute('tabindex', '0');
  resolvedHeader.setAttribute('aria-expanded', 'false');
  const toggleResolved = () => {
    resolvedList.hidden = !resolvedList.hidden;
    resolvedHeader.setAttribute('aria-expanded', String(!resolvedList.hidden));
    resolvedHeader.querySelector('.caret').textContent = resolvedList.hidden ? '▾' : '▴';
  };
  resolvedHeader.addEventListener('click', toggleResolved);
  resolvedHeader.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); toggleResolved(); }
  });
  resolvedShelf.appendChild(resolvedHeader);
  resolvedShelf.appendChild(resolvedList);
  screen.appendChild(resolvedShelf);

  root.appendChild(screen);

  let currentView = initialView;
  let searchTerm = '';
  const activeComponents = new Set();
  const activeSevs = new Set();
  function setActiveSevs(next) {
    activeSevs.clear();
    for (const v of next) activeSevs.add(v);
    render();
  }

  function _renderComponentChips() {
    const issues = store.getIssues() || [];
    const comps = [...new Set(issues.map(i => i.component).filter(Boolean))].sort();
    // Remove only our own chips — the "Components:" label is preserved.
    compRow.querySelectorAll('.issues__comp-chip').forEach(el => el.remove());
    const sevs = activeFilters();
    for (const c of comps) {
      // Count issues that pass the current search + severity filters but ignore the
      // component filter so inactive chips show their potential hit count.
      const count = issues.filter(i => {
        if (!_matchesSearch(i)) return false;
        if (sevs.length > 0 && !sevs.includes(i.severity_label || severityLabel(i.severity))) return false;
        return i.component === c;
      }).length;
      const chip = document.createElement('span');
      chip.className = 'issues__comp-chip';
      chip.dataset.comp = c;
      chip.title = CHIP_CLICK_HINT;
      chip.setAttribute('role', 'button');
      chip.setAttribute('aria-pressed', String(activeComponents.has(c)));
      chip.textContent = `${c} · ${count}`;
      if (activeComponents.has(c)) chip.classList.add('is-active');
      chip.addEventListener('click', (ev) => {
        const next = new Set(chipClickNext(ev, activeComponents, c));
        activeComponents.clear();
        for (const k of next) activeComponents.add(k);
        compRow.querySelectorAll('.issues__comp-chip').forEach(el => {
          const isActive = activeComponents.has(el.dataset.comp);
          el.classList.toggle('is-active', isActive);
          el.setAttribute('aria-pressed', String(isActive));
        });
        render();
      });
      compRow.appendChild(chip);
    }
  }

  function _matchesComponent(i) {
    if (activeComponents.size === 0) return true;
    return activeComponents.has(i.component);
  }

  function _matchesSearch(i) {
    if (!searchTerm) return true;
    const hay = [
      i.id || '',
      i.title || '',
      i.symptom || '',
      i.component || '',
      ...(i.location || []),
      ...(i.related_tasks || []).map(t => typeof t === 'string' ? t : (t.id || '')),
    ].join(' ').toLowerCase();
    return hay.includes(searchTerm);
  }

  function setView(v) {
    currentView = v;
    prefs.patch({ screens: { issues: { view: v } } });
    render();
  }

  function activeFilters() {
    return [...activeSevs];
  }

  let _lastChipKey = '';
  function render() {
    const allIssues = store.getIssues() || [];

    // Compute per-severity counts from allIssues (pre-filter) and rebuild chips.
    const counts = { __all: allIssues.length, Critical: 0, High: 0, Medium: 0, Low: 0 };
    for (const i of allIssues) {
      const lbl = i.severity_label || severityLabel(i.severity);
      if (lbl in counts) counts[lbl]++;
    }
    _renderSeverityChips(filters, counts, activeSevs, setActiveSevs);

    // Re-render chips when components change, OR when search/severity changes (counts depend on both).
    const chipKey = [...new Set(allIssues.map(i => i.component).filter(Boolean))].sort().join('|')
      + '::' + searchTerm + '::' + activeFilters().join(',');
    if (chipKey !== _lastChipKey) {
      _renderComponentChips();
      _lastChipKey = chipKey;
    }
    const issues = allIssues.filter(i => {
      if (!_matchesSearch(i)) return false;
      if (!_matchesComponent(i)) return false;
      const sevs = activeFilters();
      if (sevs.length === 0) return true;
      return sevs.includes(i.severity_label || severityLabel(i.severity));
    });
    const filterActive = !!searchTerm || activeFilters().length > 0 || activeComponents.size > 0;
    subcount.textContent = filterActive
      ? `${issues.length} of ${allIssues.length} ${pluralize(allIssues.length, 'issue', 'issues')}`
      : `${allIssues.length} ${pluralize(allIssues.length, 'issue', 'issues')}`;

    const backlogTasks = store.getBacklog()?.tasks || [];
    const tasksIndex = Object.fromEntries(backlogTasks.map(t => [t.id, t]));
    // Fix: read aging config from store.getPrefs(), not prefs.getPrefs()
    const agingCfg = store.getPrefs()?.issues?.aging || {};

    investigatingList.innerHTML = '';
    openList.innerHTML = '';
    resolvedList.innerHTML = '';

    const investigating = issues.filter(i => i.status === 'investigating');
    const open          = issues.filter(i => i.status === 'open');
    const resolved      = issues.filter(i => i.status === 'fixed' || i.status === 'wontfix');

    for (const i of investigating) {
      investigatingList.appendChild(issueCard(i, { tasksIndex, agingCfg, onTaskClick: id => location.hash = `#/task/${id}` }));
    }
    for (const i of open) {
      openList.appendChild(issueCard(i, { tasksIndex, agingCfg, onTaskClick: id => location.hash = `#/task/${id}` }));
    }
    for (const i of resolved) {
      resolvedList.appendChild(issueRow(i));
    }

    if (investigating.length === 0 && open.length === 0 && filterActive) {
      openList.appendChild(emptyState({
        headline: 'No open issues match your filters',
        hint: 'Try clearing a severity chip or the search box.',
      }));
    } else if (investigating.length === 0 && open.length === 0 && resolved.length === 0) {
      openList.appendChild(emptyState({
        headline: 'No issues yet',
        hint: 'Issues appear here as you investigate or fix bugs.',
      }));
    }
    resolvedHeader.innerHTML = `<span class="caret">▾</span> Resolved · ${resolved.length} ${pluralize(resolved.length, 'issue', 'issues')}`;

    if (currentView === 'D') {
      // Severity kanban: 4 columns (Critical / High / Medium / Low).
      // Resolved (fixed + wontfix) rendered in the resolved shelf below, not as columns.
      columns.style.display = 'none';
      kanban.style.display = '';
      _showKanbanCols('severity');
      const activeIssues = issues.filter(i => i.status === 'open' || i.status === 'investigating');
      const grouped = groupBySeverity(activeIssues);
      for (const desc of KANBAN_SEVERITY_COLS) {
        const { body } = sevKanbanCols[desc.key];
        body.innerHTML = '';
        const items = grouped[desc.key];
        body.parentElement.querySelector('[data-count]').textContent = String(items.length);
        if (items.length === 0) {
          body.appendChild(emptyState({ headline: 'None', hint: '' }));
          continue;
        }
        for (const i of items) {
          body.appendChild(issueCard(i, {
            tasksIndex, agingCfg,
            onTaskClick: id => location.hash = `#/task/${id}`,
            suppressSeverityChip: true,
          }));
        }
      }
      // Resolved shelf renders fixed + wontfix issues.
      resolvedShelf.style.display = '';
      resolvedList.innerHTML = '';
      for (const i of resolved) resolvedList.appendChild(issueRow(i));
      resolvedHeader.innerHTML = `<span class="caret">${resolvedList.hidden ? '▾' : '▴'}</span> Resolved · ${resolved.length} ${pluralize(resolved.length, 'issue', 'issues')}`;
      return;
    }

    if (currentView === 'B') {
      // Status kanban: 4 columns (Open / Investigating / Fixed / Wontfix).
      columns.style.display = 'none';
      kanban.style.display = '';
      _showKanbanCols('status');
      // Clear bodies
      for (const k of Object.keys(statusKanbanCols)) {
        statusKanbanCols[k].body.innerHTML = '';
      }
      const grouped = groupByStatus(issues);
      for (const desc of KANBAN_STATUS_COLS) {
        const { body, density } = statusKanbanCols[desc.key];
        const items = grouped[desc.key];
        body.parentElement.querySelector('[data-count]').textContent = String(items.length);
        if (items.length === 0) {
          body.appendChild(emptyState({ headline: 'None', hint: '' }));
          continue;
        }
        for (const i of items) {
          if (density === 'card') {
            body.appendChild(issueCard(i, { tasksIndex, agingCfg, onTaskClick: id => location.hash = `#/task/${id}` }));
          } else {
            body.appendChild(issueRow(i));
          }
        }
      }
      resolvedShelf.style.display = 'none'; // resolved is inline as own columns
      return;
    }

    // Restore defaults for Hybrid / List paths
    columns.style.display = '';
    kanban.style.display = 'none';
    resolvedShelf.style.display = '';

    if (currentView === 'C') {
      // List view: collapse to a single column
      columns.style.gridTemplateColumns = '1fr';
      openCol.querySelector('.issues__column-name').textContent = 'All open';
      investigatingCol.style.display = 'none';
      for (const i of investigating) {
        openList.insertBefore(
          issueCard(i, { tasksIndex, agingCfg, onTaskClick: id => location.hash = `#/task/${id}` }),
          openList.firstChild,
        );
      }
    } else {
      // Hybrid view (default): Investigating + Open columns side by side.
      columns.style.gridTemplateColumns = '1fr 1.6fr';
      investigatingCol.style.display = '';
      openCol.querySelector('.issues__column-name').textContent = 'Open';
      investigatingCol.querySelector('[data-count]').textContent = String(investigating.length);
      openCol.querySelector('[data-count]').textContent = String(open.length);
    }
  }

  if (!store.getIssues() || store.getIssues().length === 0) {
    const data = await api.getIssues({ includeResolved: true });
    store.setIssues(data.issues);
  }
  render();
  return () => {};
}
