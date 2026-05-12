import { issueCard, issueRow } from '../components/issue-card.js';
import { severityLabel } from '../util/severity-label.js';
import { pluralize } from '../util/pluralize.js';
import { emptyState } from '../components/empty-state.js';
import * as api from '../api.js';
import { claimTopbar, tmSubcount, tmSearch, tmSegmented, tmAction } from '../lib/topbar.js';
import { chipClickNext, CHIP_CLICK_HINT } from '../util/chip-toggle.js';

export const meta = { title: 'Issues', icon: '!', sidebarKey: 'issues' };

const SEVERITIES = ['Critical', 'High', 'Medium', 'Low'];

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
  for (const sev of SEVERITIES) {
    const c = document.createElement('span');
    c.className = 'issues__sev-chip';
    c.dataset.sev = sev;
    c.title = CHIP_CLICK_HINT;
    c.textContent = sev;
    c.addEventListener('click', (ev) => {
      const current = [...filters.querySelectorAll('.is-active')].map(el => el.dataset.sev);
      const next = new Set(chipClickNext(ev, current, sev));
      filters.querySelectorAll('.issues__sev-chip').forEach(el => {
        el.classList.toggle('is-active', next.has(el.dataset.sev));
      });
      render();
    });
    filters.appendChild(c);
  }

  // Fix: read persisted view from store.getPrefs(), not prefs.getPrefs()
  const initialView = (store.getPrefs()?.screens?.issues?.view) || 'A';
  const toggle = tmSegmented(
    [
      { key: 'A', label: 'Hybrid' },
      { key: 'B', label: 'Kanban' },
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

  const resolvedShelf = document.createElement('section');
  resolvedShelf.className = 'issues__resolved-shelf';
  const resolvedHeader = document.createElement('header');
  resolvedHeader.className = 'issues__resolved-header';
  const resolvedList = document.createElement('div');
  resolvedList.className = 'issues__resolved-list';
  resolvedList.hidden = true;
  resolvedHeader.addEventListener('click', () => {
    resolvedList.hidden = !resolvedList.hidden;
    resolvedHeader.querySelector('.caret').textContent = resolvedList.hidden ? '▾' : '▴';
  });
  resolvedShelf.appendChild(resolvedHeader);
  resolvedShelf.appendChild(resolvedList);
  screen.appendChild(resolvedShelf);

  root.appendChild(screen);

  let currentView = initialView;
  let searchTerm = '';
  const activeComponents = new Set();

  function _renderComponentChips() {
    const issues = store.getIssues() || [];
    const comps = [...new Set(issues.map(i => i.component).filter(Boolean))].sort();
    compRow.innerHTML = '';
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
      chip.textContent = `${c} · ${count}`;
      if (activeComponents.has(c)) chip.classList.add('is-active');
      chip.addEventListener('click', (ev) => {
        const next = new Set(chipClickNext(ev, activeComponents, c));
        activeComponents.clear();
        for (const k of next) activeComponents.add(k);
        compRow.querySelectorAll('.issues__comp-chip').forEach(el => {
          el.classList.toggle('is-active', activeComponents.has(el.dataset.comp));
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
    return [...filters.querySelectorAll('.is-active')].map(el => el.dataset.sev);
  }

  let _lastChipKey = '';
  function render() {
    const allIssues = store.getIssues() || [];
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

    // View B/C: just collapse columns into a single list (lightweight pass)
    if (currentView === 'C') {
      columns.style.gridTemplateColumns = '1fr';
      openCol.querySelector('.issues__column-name').textContent = 'All open';
      investigatingCol.style.display = 'none';
      // append investigating + open to a single list visually via openList
      for (const i of investigating) {
        openList.insertBefore(
          issueCard(i, { tasksIndex, agingCfg, onTaskClick: id => location.hash = `#/task/${id}` }),
          openList.firstChild,
        );
      }
    } else {
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
