import { bugCard } from '../components/bug-card.js';
import { emptyState } from '../components/empty-state.js';
import * as api from '../api.js';
import { claimTopbar, tmSubcount, tmSearch } from '../lib/topbar.js';

export const meta = { title: 'Bugs', icon: '⊘', sidebarKey: 'bugs' };

export async function mount(root, { store, prefs }) {
  // Gotcha: `prefs` is the patch helper, NOT the data object.
  // Read persisted state from store.getPrefs(), then use prefs.patch() to save.
  root.innerHTML = '';
  const screen = document.createElement('section');
  screen.className = 'bugs';

  // ---- topbar (#topbar-actions)
  const topbar = claimTopbar();
  const subcount = tmSubcount('… bugs');
  let searchTerm = '';
  const searchBuilt = tmSearch({
    placeholder: 'Search bugs…',
    onInput: (v) => { searchTerm = v.trim().toLowerCase(); render(); },
  });

  // Filter chips — Open / Shelved / Archive
  const persistedFilters = (store.getPrefs()?.screens?.bugs?.filters) || { open: true, shelved: true, archive: false };
  const filters = { ...persistedFilters };

  const chipRow = document.createElement('div');
  chipRow.className = 'tm-chip-row bugs__filters';

  function makeChip(key, label) {
    const c = document.createElement('button');
    c.type = 'button';
    c.className = 'bugs__chip' + (filters[key] ? ' is-active' : '');
    c.dataset.key = key;
    c.setAttribute('role', 'button');
    c.setAttribute('aria-pressed', String(!!filters[key]));
    c.textContent = label;
    c.addEventListener('click', () => {
      filters[key] = !filters[key];
      c.classList.toggle('is-active', filters[key]);
      c.setAttribute('aria-pressed', String(filters[key]));
      prefs.patch({ screens: { bugs: { filters } } });
      render();
    });
    return c;
  }

  [
    { key: 'open',    label: 'Open' },
    { key: 'shelved', label: 'Shelved' },
    { key: 'archive', label: 'Archive' },
  ].forEach(({ key, label }) => chipRow.appendChild(makeChip(key, label)));

  topbar?.appendChild(subcount);
  topbar?.appendChild(searchBuilt.el);
  topbar?.appendChild(chipRow);

  // ---- list container
  const list = document.createElement('div');
  list.className = 'bugs__list';
  screen.appendChild(list);
  root.appendChild(screen);

  let activeBugs = [];    // open + shelved
  let archiveBugs = [];   // archived bugs (superset returned by include_archive=1)

  async function load() {
    // Fetch active bugs and archive in parallel.
    [activeBugs, archiveBugs] = await Promise.all([
      api.listBugs(),
      api.listBugs({ include_archive: true }),
    ]);
  }

  function render() {
    list.innerHTML = '';
    const visible = [];

    // Active bugs (open / shelved)
    for (const bug of activeBugs) {
      const status = bug.status || 'open';
      if (!filters[status]) continue;
      if (searchTerm && !(bug.title || '').toLowerCase().includes(searchTerm) && !bug.id.toLowerCase().includes(searchTerm)) continue;
      visible.push(bug);
    }

    // Archive bugs — include_archive returns all; de-dup with active set
    if (filters.archive) {
      const seen = new Set(activeBugs.map(b => b.id));
      for (const bug of archiveBugs) {
        if (seen.has(bug.id)) continue;
        if (searchTerm && !(bug.title || '').toLowerCase().includes(searchTerm) && !bug.id.toLowerCase().includes(searchTerm)) continue;
        visible.push(bug);
      }
    }

    // Sort newest-first by discovered date
    visible.sort((a, b) => (b.discovered || '').localeCompare(a.discovered || ''));

    if (!visible.length) {
      list.appendChild(emptyState('No bugs match your filters.'));
    } else {
      for (const bug of visible) {
        list.appendChild(bugCard(bug, { onClick: () => { location.hash = `#/bug/${bug.id}`; } }));
      }
    }

    subcount.textContent = `${visible.length} bug${visible.length === 1 ? '' : 's'}`;
  }

  await load();
  render();
}
