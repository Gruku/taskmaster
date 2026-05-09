// Archived tasks — list of all tasks with status === 'archived', grouped by epic.
// Read-only. No filters apart from search.

import { claimTopbar } from '../lib/topbar.js';
import { pluralize } from '../util/pluralize.js';
import { emptyState } from '../components/empty-state.js';

export const meta = { title: 'Archived', icon: '⌫', sidebarKey: 'archived' };

export async function mount(root, { store }) {
  const page = document.createElement('div');
  page.className = 'archived-page';

  const head = claimTopbar();
  const subcount = document.createElement('span');
  subcount.className = 'tm-subcount';
  subcount.textContent = '… archived tasks';
  head.appendChild(subcount);

  const search = document.createElement('div');
  search.className = 'tm-search';
  search.innerHTML = `<span class="icon">⌕</span><input placeholder="Filter by title or id…" /><span class="cmp-kbd">⌘K</span>`;
  const searchInput = search.querySelector('input');
  let q = '';
  let timer = null;
  searchInput.addEventListener('input', () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => { q = searchInput.value.trim().toLowerCase(); paint(); }, 180);
  });
  head.appendChild(search);

  const list = document.createElement('div');
  list.className = 'archived-list';
  page.appendChild(list);

  root.appendChild(page);

  function paint() {
    const backlog = store.getBacklog() || { tasks: [], epics: [] };
    const tasks = (Array.isArray(backlog.tasks) ? backlog.tasks : [])
      .filter(t => String(t.status || '').toLowerCase() === 'archived');

    const filtered = q
      ? tasks.filter(t => `${t.id} ${t.title || ''}`.toLowerCase().includes(q))
      : tasks;

    subcount.textContent = `${filtered.length} ${pluralize(filtered.length, 'archived task', 'archived tasks')}`;

    list.replaceChildren();
    if (!filtered.length) {
      list.appendChild(emptyState({
        headline: q ? `No archived tasks match "${q}"` : 'No archived tasks yet',
      }));
      return;
    }

    // Group by epic id; "(no epic)" bucket for orphans.
    const groups = new Map();
    for (const t of filtered) {
      const key = t.epic || '__none__';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(t);
    }

    for (const [epicId, items] of groups) {
      const grp = document.createElement('section');
      grp.className = 'arch-group';
      const h = document.createElement('h3');
      h.className = 'arch-group-h';
      h.textContent = epicId === '__none__' ? '— no epic —' : epicId;
      const cnt = document.createElement('span');
      cnt.className = 'arch-group-count';
      cnt.textContent = `${items.length}`;
      h.appendChild(cnt);
      grp.appendChild(h);

      for (const t of items) {
        const row = document.createElement('a');
        row.className = 'arch-row';
        row.href = `#/task/${encodeURIComponent(t.id)}`;
        row.innerHTML = `
          <span class="arch-id">${escapeHtml(t.id)}</span>
          <span class="arch-title">${escapeHtml(t.title || '')}</span>
          <span class="arch-meta">${t.phase ? escapeHtml(t.phase) : '—'}</span>
          <span class="arch-reason">${escapeHtml(t.archived_reason || '')}</span>
        `;
        grp.appendChild(row);
      }
      list.appendChild(grp);
    }
  }

  const unsub = store.subscribe('backlog', paint);
  paint();

  return () => {
    if (timer) clearTimeout(timer);
    unsub();
  };
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
