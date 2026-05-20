// Project Structure screen — sub-repo cards with worktree mini-trees.
// Calls /api/project-structure on mount with refresh_git=true; subsequent
// navigations away-and-back reuse the cached payload until the user clicks
// the global "Refresh git state" button.

import { getProjectStructure } from '../api.js';
import { claimTopbar, tmAction } from '../lib/topbar.js';

export const meta = { title: 'Project', icon: '⤿', sidebarKey: 'worktrees' };

// Session-scoped cache shared across mount/unmount cycles.
let _cache = null;

const escapeHtml = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const STATUS_GLYPH = {
  'todo':        '○',
  'in-progress': '●',
  'in-review':   '◐',
  'done':        '✓',
};

export async function mount(root) {
  root.innerHTML = `
    <div class="ws-page">
      <div class="ws-grid" data-role="grid">
        <div class="ws-empty">Loading project structure…</div>
      </div>
    </div>
  `;

  const topbar = claimTopbar();
  const refreshBtn = tmAction({
    icon: '↻', label: 'Refresh git state', variant: 'ghost',
    onClick: async () => { _cache = null; await load(root); },
  });
  topbar?.appendChild(refreshBtn);

  await load(root, { useCache: true });
}

async function load(root, { useCache = false } = {}) {
  const grid = root.querySelector('[data-role="grid"]');
  try {
    if (!useCache || !_cache) {
      _cache = await getProjectStructure(true);
    }
    render(grid, _cache);
  } catch (e) {
    grid.innerHTML = `<div class="ws-error">Failed to load: ${escapeHtml(e.message)}</div>`;
  }
}

function render(grid, data) {
  if (!data.sub_repos.length) {
    grid.innerHTML = `<div class="ws-empty">No sub-repos detected under <code>${escapeHtml(data.project.root)}</code>.</div>`;
    return;
  }
  grid.innerHTML = data.sub_repos.map(sr => renderCard(sr)).join('');
  bindCardActions(grid);
}

function renderCard(sr) {
  const kindGlyph = sr.kind === 'submodule' ? '◈' : '▣';
  const currentBranch = sr.current_branch || '—';
  return `
    <div class="ws-card" data-sub-repo="${escapeHtml(sr.path)}">
      <div class="ws-card-head">
        <span class="ws-kind" title="${escapeHtml(sr.kind)}">${kindGlyph}</span>
        <span class="ws-path">${escapeHtml(sr.path)}</span>
        <span class="ws-branch" title="current branch">${escapeHtml(currentBranch)}</span>
        <button class="ws-refresh" type="button"
          data-action="refresh-card" data-sub-repo="${escapeHtml(sr.path)}"
          title="Refresh this sub-repo">↻</button>
      </div>
      <div class="ws-card-body">
        ${renderWorktreeList(sr)}
      </div>
    </div>
  `;
}

function renderWorktreeList(sr) {
  if (!sr.worktrees || !sr.worktrees.length) {
    return `<div class="ws-empty-sub">No worktrees.</div>`;
  }
  // Body filled in by Task 12.
  return sr.worktrees.map(w => `<div class="ws-wt" data-wt="${escapeHtml(w.path)}">${escapeHtml(w.branch || 'detached')}</div>`).join('');
}

function bindCardActions(grid) {
  // Wired in Task 14.
}

export { STATUS_GLYPH, escapeHtml };  // exported for unit-testing convenience
