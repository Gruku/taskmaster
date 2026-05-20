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

function renderSubmoduleInfo(sr) {
  if (sr.kind !== 'submodule' || !sr.submodule_info) return '';
  const info = sr.submodule_info;
  const sha = (info.pinned_sha || '').slice(0, 8) || '—';
  const drift = (info.drift_ahead || info.drift_behind)
    ? `<span class="ws-drift">drift +${info.drift_ahead || 0} / -${info.drift_behind || 0}</span>`
    : `<span class="ws-drift ws-drift-clean">in sync</span>`;
  return `
    <div class="ws-submodule-info">
      <span class="ws-sm-label">pinned</span>
      <code class="ws-sm-sha">${escapeHtml(sha)}</code>
      ${drift}
    </div>
  `;
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
        ${renderSubmoduleInfo(sr)}
        ${renderWorktreeList(sr)}
      </div>
    </div>
  `;
}

function renderWorktreeList(sr) {
  if (!sr.worktrees || !sr.worktrees.length) {
    return `<div class="ws-empty-sub">No worktrees.</div>`;
  }
  return sr.worktrees.map(w => renderWorktreeRow(w, sr)).join('');
}

function renderWorktreeRow(w, sr) {
  const branchLabel = w.branch || '(detached)';
  const statusGlyph = renderStatusGlyph(w.git_state, sr.integration_branches || []);
  const ladder = renderMergeLadder(w.git_state, sr.integration_branches || []);
  const counts = renderCountChips(w.git_state);
  return `
    <details class="ws-wt" data-wt-path="${escapeHtml(w.path)}">
      <summary class="ws-wt-head">
        <span class="ws-wt-glyph">⤿</span>
        <span class="ws-wt-branch">${escapeHtml(branchLabel)}</span>
        <span class="ws-wt-status">${statusGlyph}</span>
        <span class="ws-wt-ladder">${ladder}</span>
        <span class="ws-wt-counts">${counts}</span>
      </summary>
      <div class="ws-wt-body">
        ${renderTaskList(w.tasks)}
        ${renderHandoverList(w.handovers)}
      </div>
    </details>
  `;
}

function renderStatusGlyph(gitState, integrationBranches) {
  if (!gitState || !gitState.merge_ladder) return '';
  // ✓→<highest> where highest is the highest-ranked integration branch in which
  // the worktree branch IS merged. Rank order is already encoded by
  // integration_branches (server returns rank-sorted lowest→highest).
  let highest = null;
  for (const b of integrationBranches) {
    if (gitState.merge_ladder[b]) highest = b;
  }
  if (highest) {
    return `<span class="ws-merged" title="merged into ${escapeHtml(highest)}">✓→${escapeHtml(highest)}</span>`;
  }
  return `<span class="ws-unmerged" title="not merged into any integration branch">⊘</span>`;
}

function renderMergeLadder(gitState, integrationBranches) {
  if (!gitState || !gitState.merge_ladder) return '';
  return integrationBranches.map(b => {
    const merged = gitState.merge_ladder[b];
    const cls = merged ? 'ws-rung-on' : 'ws-rung-off';
    const mark = merged ? '✓' : '✘';
    return `<span class="ws-rung ${cls}" title="${escapeHtml(b)}: ${merged ? 'merged' : 'not merged'}">${escapeHtml(b)}${mark}</span>`;
  }).join(' ');
}

function renderCountChips(gitState) {
  if (!gitState) return '';
  const bits = [];
  if (gitState.ahead)        bits.push(`<span class="ws-count" title="commits ahead">+${gitState.ahead}</span>`);
  if (gitState.behind)       bits.push(`<span class="ws-count" title="commits behind">-${gitState.behind}</span>`);
  if (gitState.dirty_files)  bits.push(`<span class="ws-count" title="dirty files">~${gitState.dirty_files}</span>`);
  return bits.join('');
}

function renderTaskList(tasks) {
  if (!tasks || !tasks.length) return '';
  const rows = tasks.map(t => {
    const glyph = STATUS_GLYPH[t.status] || '·';
    return `
      <a class="ws-task" href="#/kanban?focus=${encodeURIComponent(t.id)}"
         data-task-id="${escapeHtml(t.id)}">
        <span class="ws-task-glyph">${glyph}</span>
        <span class="ws-task-id">${escapeHtml(t.id)}</span>
        <span class="ws-task-title">${escapeHtml(t.title || '')}</span>
      </a>
    `;
  }).join('');
  return `<div class="ws-sub-list ws-sub-tasks"><div class="ws-sub-h">Tasks</div>${rows}</div>`;
}

function renderHandoverList(handovers) {
  if (!handovers || !handovers.length) return '';
  const rows = handovers.map(h => {
    const when = (h.created || h.date || '').slice(0, 10);
    return `
      <a class="ws-ho" href="#/sessions?id=${encodeURIComponent(h.id)}"
         data-handover-id="${escapeHtml(h.id)}">
        <span class="ws-ho-when">${escapeHtml(when)}</span>
        <span class="ws-ho-id">${escapeHtml(h.id)}</span>
      </a>
    `;
  }).join('');
  return `<div class="ws-sub-list ws-sub-handovers"><div class="ws-sub-h">Handovers</div>${rows}</div>`;
}

function bindCardActions(grid) {
  grid.querySelectorAll('[data-action="refresh-card"]').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault(); e.stopPropagation();
      const path = btn.dataset.subRepo;
      btn.disabled = true;
      btn.textContent = '…';
      try {
        // Hit the server with a fresh payload (bypasses the screen-local cache;
        // server-side LRU still returns cached unless .gitmodules changed, but
        // refresh_git=true forces git work to actually re-run).
        const fresh = await getProjectStructure(true);
        _cache = fresh;
        // Re-render only this card to preserve other cards' expanded state.
        const sr = fresh.sub_repos.find(s => s.path === path);
        if (sr) {
          const card = btn.closest('.ws-card');
          if (card) card.outerHTML = renderCard(sr);
        }
        // Re-bind actions on the (possibly replaced) DOM.
        bindCardActions(grid);
      } catch (err) {
        btn.disabled = false;
        btn.textContent = '↻';
        btn.title = 'Refresh failed: ' + err.message;
      }
    });
  });
}

export { STATUS_GLYPH, escapeHtml };  // exported for unit-testing convenience
