import { issueCard, issueRow } from '../components/issue-card.js';
import { severityLabel } from '../util/severity-label.js';
import * as api from '../api.js';

export const meta = { title: 'Issues', icon: '!', sidebarKey: 'issues' };

const SEVERITIES = ['Critical', 'High', 'Medium', 'Low'];

export async function mount(root, { store, prefs }) {
  // Gotcha: `prefs` is the patch helper, NOT the data object.
  // Read persisted state from store.getPrefs(), then use prefs.patch() to save.
  root.innerHTML = '';
  const screen = document.createElement('section');
  screen.className = 'issues';

  // ---- header
  const header = document.createElement('header');
  header.className = 'issues__header';
  header.innerHTML = '<h1>Issues</h1>';
  const filters = document.createElement('div');
  filters.className = 'issues__filters';
  for (const sev of SEVERITIES) {
    const c = document.createElement('span');
    c.className = 'issues__sev-chip';
    c.dataset.sev = sev;
    c.textContent = sev;
    c.addEventListener('click', () => {
      c.classList.toggle('is-active');
      render();
    });
    filters.appendChild(c);
  }
  header.appendChild(filters);

  const toggle = document.createElement('div');
  toggle.className = 'lessons__view-toggle';     // reuse same toggle style
  for (const v of [['A', 'Hybrid'], ['B', 'Kanban'], ['C', 'List']]) {
    const b = document.createElement('button');
    b.dataset.view = v[0]; b.textContent = v[1];
    b.addEventListener('click', () => setView(v[0]));
    toggle.appendChild(b);
  }
  header.appendChild(toggle);
  screen.appendChild(header);

  // ---- columns + resolved shelf
  const columns = document.createElement('div');
  columns.className = 'issues__columns';
  const investigatingCol = document.createElement('div');
  investigatingCol.className = 'issues__column';
  investigatingCol.innerHTML = '<header class="issues__column-header">Investigating</header>';
  const investigatingList = document.createElement('div');
  investigatingCol.appendChild(investigatingList);

  const openCol = document.createElement('div');
  openCol.className = 'issues__column';
  openCol.innerHTML = '<header class="issues__column-header">Open</header>';
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

  // Fix: read persisted view from store.getPrefs(), not prefs.getPrefs()
  let currentView = (store.getPrefs()?.screens?.issues?.view) || 'A';
  function setView(v) {
    currentView = v;
    prefs.patch({ screens: { issues: { view: v } } });
    render();
  }

  function activeFilters() {
    return [...filters.querySelectorAll('.is-active')].map(el => el.dataset.sev);
  }

  function render() {
    for (const b of toggle.querySelectorAll('button')) {
      b.classList.toggle('is-active', b.dataset.view === currentView);
    }
    const issues = (store.getIssues() || []).filter(i => {
      const sevs = activeFilters();
      if (sevs.length === 0) return true;
      return sevs.includes(i.severity_label || severityLabel(i.severity));
    });

    const tasksIndex = store.getTasksIndex ? store.getTasksIndex() : {};
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
    resolvedHeader.innerHTML = `<span class="caret">▾</span> Resolved · ${resolved.length} issues`;

    // View B/C: just collapse columns into a single list (lightweight pass)
    if (currentView === 'C') {
      columns.style.gridTemplateColumns = '1fr';
      openCol.querySelector('.issues__column-header').textContent = 'All open';
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
      openCol.querySelector('.issues__column-header').textContent = 'Open';
    }
  }

  if (!store.getIssues() || store.getIssues().length === 0) {
    const data = await api.getIssues({ includeResolved: true });
    store.setIssues(data.issues);
  }
  render();
  return () => {};
}
