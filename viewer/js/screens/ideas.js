// Ideas screen — list/detail views with chip filters for status + tags,
// archived toggle, frontmatter sidebar with click-through links, and a
// "Create Idea" button in the topbar.
//
// Convention: mirrors issues.js / lessons.js patterns throughout.
// No colored left rails (user pref); status uses tinted background pills.

import * as api from '../api.js';
import { claimTopbar, tmSubcount, tmSearch, tmAction } from '../lib/topbar.js';
import { pluralize } from '../util/pluralize.js';
import { emptyState } from '../components/empty-state.js';
import { chipClickNext, CHIP_CLICK_HINT } from '../util/chip-toggle.js';

export const meta = { title: 'Ideas', icon: '💡', sidebarKey: 'ideas' };

// ─── pure filter helper (exported for unit tests) ────────────────────────────

/**
 * Filter and sort ideas.
 *
 * @param {Array}  ideas           - raw idea objects
 * @param {object} opts
 * @param {string[]} opts.statuses - active status filters (OR); empty = all
 * @param {string[]} opts.tags     - active tag filters (AND with statuses); empty = all
 * @param {boolean} opts.includeArchived - include archived ideas (default false)
 * @returns {Array} filtered + sorted ideas (newest created first)
 */
export function applyIdeasFilters(ideas, { statuses = [], tags = [], includeArchived = false } = {}) {
  const filtered = ideas.filter(idea => {
    if (!includeArchived && idea.archived) return false;
    if (statuses.length > 0 && !statuses.includes(idea.status)) return false;
    if (tags.length > 0 && !tags.every(t => (idea.tags || []).includes(t))) return false;
    return true;
  });
  // Sort newest first by created date string (ISO-8601 lex-sort is fine)
  return filtered.slice().sort((a, b) => {
    const da = a.created || '';
    const db = b.created || '';
    return db < da ? -1 : db > da ? 1 : 0;
  });
}

// ─── status pill color map ────────────────────────────────────────────────────
// Freeform statuses: apply tinted-background pills; unknown → neutral.
const STATUS_COLORS = {
  exploring:   { bg: 'rgba(110, 168, 255, 0.12)', color: '#6ea8ff',  border: 'rgba(110, 168, 255, 0.28)' },
  candidate:   { bg: 'rgba(95, 205, 184, 0.12)',  color: '#5fcdb8',  border: 'rgba(95, 205, 184, 0.28)' },
  'parking-lot': { bg: 'rgba(214, 164, 95, 0.12)', color: '#d6a45f', border: 'rgba(214, 164, 95, 0.28)' },
  promoted:    { bg: 'rgba(160, 127, 224, 0.12)', color: '#a07fe0',  border: 'rgba(160, 127, 224, 0.28)' },
  dropped:     { bg: 'rgba(140, 140, 149, 0.10)', color: '#7c8290',  border: 'rgba(140, 140, 149, 0.20)' },
};

function statusPill(status) {
  const el = document.createElement('span');
  el.className = 'idea-row__status-pill';
  el.textContent = status || '—';
  const colors = STATUS_COLORS[status] || { bg: 'rgba(255,255,255,0.06)', color: '#a8a8ae', border: 'rgba(255,255,255,0.12)' };
  el.style.background = colors.bg;
  el.style.color = colors.color;
  el.style.border = `1px solid ${colors.border}`;
  return el;
}

// ─── relative time ────────────────────────────────────────────────────────────
function relativeTime(iso) {
  if (!iso) return '';
  const now = Date.now();
  const then = new Date(iso).getTime();
  const diff = now - then;
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}

// ─── Create Idea modal ────────────────────────────────────────────────────────
function openCreateIdeaModal({ onSave }) {
  const host = document.getElementById('entity-modal-host');
  if (!host) {
    // Fallback: append a host if missing (shouldn't happen in normal flow)
    const h = document.createElement('div');
    h.id = 'entity-modal-host';
    document.body.appendChild(h);
  }

  const overlay = document.createElement('div');
  overlay.className = 'em-overlay';
  overlay.tabIndex = -1;

  const modal = document.createElement('div');
  modal.className = 'em-modal';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-label', 'Create Idea');

  // Header
  const header = document.createElement('div');
  header.className = 'em-header';
  const titleEl = document.createElement('span');
  titleEl.className = 'em-title';
  titleEl.textContent = 'Create Idea';
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'em-close';
  closeBtn.setAttribute('aria-label', 'close');
  closeBtn.textContent = '✕';
  header.appendChild(titleEl);
  header.appendChild(closeBtn);

  // Body
  const body = document.createElement('div');
  body.className = 'em-body';

  // Title field (required)
  const titleField = document.createElement('div');
  titleField.className = 'em-field';
  const titleLabel = document.createElement('label');
  titleLabel.className = 'em-label';
  titleLabel.textContent = 'Title *';
  const titleInput = document.createElement('input');
  titleInput.type = 'text';
  titleInput.className = 'em-input';
  titleInput.placeholder = 'Short descriptive title…';
  titleInput.required = true;
  const titleErr = document.createElement('div');
  titleErr.className = 'em-field-error';
  titleField.appendChild(titleLabel);
  titleField.appendChild(titleInput);
  titleField.appendChild(titleErr);

  // Status field (freeform)
  const statusField = document.createElement('div');
  statusField.className = 'em-field';
  const statusLabel = document.createElement('label');
  statusLabel.className = 'em-label';
  statusLabel.textContent = 'Status';
  const statusSelect = document.createElement('select');
  statusSelect.className = 'em-input';
  const statusOptions = ['exploring', 'candidate', 'parking-lot', 'promoted', 'dropped'];
  const blankOpt = document.createElement('option');
  blankOpt.value = '';
  blankOpt.textContent = '— pick a status —';
  statusSelect.appendChild(blankOpt);
  for (const s of statusOptions) {
    const opt = document.createElement('option');
    opt.value = s;
    opt.textContent = s;
    statusSelect.appendChild(opt);
  }
  statusField.appendChild(statusLabel);
  statusField.appendChild(statusSelect);

  // Tags field
  const tagsField = document.createElement('div');
  tagsField.className = 'em-field';
  const tagsLabel = document.createElement('label');
  tagsLabel.className = 'em-label';
  tagsLabel.textContent = 'Tags';
  const tagsHint = document.createElement('span');
  tagsHint.className = 'em-label-hint';
  tagsHint.textContent = ' (comma-separated)';
  tagsLabel.appendChild(tagsHint);
  const tagsInput = document.createElement('input');
  tagsInput.type = 'text';
  tagsInput.className = 'em-input';
  tagsInput.placeholder = 'ux, perf, ai…';
  tagsField.appendChild(tagsLabel);
  tagsField.appendChild(tagsInput);

  // Body field
  const bodyField = document.createElement('div');
  bodyField.className = 'em-field';
  const bodyLabel = document.createElement('label');
  bodyLabel.className = 'em-label';
  bodyLabel.textContent = 'Body';
  const bodyTextarea = document.createElement('textarea');
  bodyTextarea.className = 'em-input em-textarea';
  bodyTextarea.placeholder = 'Describe the idea in detail…';
  bodyTextarea.rows = 5;
  bodyField.appendChild(bodyLabel);
  bodyField.appendChild(bodyTextarea);

  body.appendChild(titleField);
  body.appendChild(statusField);
  body.appendChild(tagsField);
  body.appendChild(bodyField);

  // Footer
  const errSummary = document.createElement('div');
  errSummary.className = 'em-error-summary';
  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'em-cancel';
  cancelBtn.textContent = 'Cancel';
  const saveBtn = document.createElement('button');
  saveBtn.type = 'button';
  saveBtn.className = 'em-save';
  saveBtn.textContent = 'Create';
  saveBtn.disabled = true;

  const footerActions = document.createElement('div');
  footerActions.className = 'em-footer-actions';
  footerActions.appendChild(cancelBtn);
  footerActions.appendChild(saveBtn);

  const footer = document.createElement('div');
  footer.className = 'em-footer';
  footer.appendChild(errSummary);
  footer.appendChild(footerActions);

  modal.appendChild(header);
  modal.appendChild(body);
  modal.appendChild(footer);
  overlay.appendChild(modal);
  document.getElementById('entity-modal-host').appendChild(overlay);
  document.body.classList.add('em-open');

  function validate() {
    const valid = titleInput.value.trim().length > 0;
    saveBtn.disabled = !valid;
    titleErr.textContent = '';
    return valid;
  }

  titleInput.addEventListener('input', validate);

  function doClose() {
    overlay.remove();
    document.body.classList.remove('em-open');
    document.removeEventListener('keydown', onKeyDown);
  }

  function doCancel() {
    const dirty = titleInput.value.trim() || bodyTextarea.value.trim() || tagsInput.value.trim();
    if (dirty && !window.confirm('Discard new idea?')) return;
    doClose();
  }

  cancelBtn.addEventListener('click', doCancel);
  closeBtn.addEventListener('click', doCancel);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) doCancel(); });

  async function doSave() {
    if (!validate()) {
      titleErr.textContent = 'Title is required.';
      titleInput.focus();
      return;
    }
    const payload = {
      title: titleInput.value.trim(),
      status: statusSelect.value || 'exploring',
      tags: tagsInput.value.trim()
        ? tagsInput.value.split(',').map(t => t.trim()).filter(Boolean)
        : [],
      body: bodyTextarea.value.trim() || '',
    };
    saveBtn.disabled = true;
    saveBtn.textContent = 'Creating…';
    errSummary.textContent = '';
    try {
      await onSave(payload);
      doClose();
    } catch (e) {
      errSummary.textContent = e.message || 'Failed to create idea.';
      saveBtn.disabled = false;
      saveBtn.textContent = 'Create';
    }
  }

  saveBtn.addEventListener('click', doSave);

  function onKeyDown(e) {
    if (e.key === 'Escape') { e.preventDefault(); doCancel(); }
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); doSave(); }
  }
  document.addEventListener('keydown', onKeyDown);

  // Focus title after mount
  queueMicrotask(() => titleInput.focus());

  return doClose;
}

// ─── main mount function ──────────────────────────────────────────────────────

export async function mount(root, { store, prefs }) {
  // Gotcha: `prefs` is the patch helper, NOT the data object.
  // Read persisted state from store.getPrefs(), then use prefs.patch() to save.
  root.innerHTML = '';
  const screen = document.createElement('section');
  screen.className = 'ideas';

  // ---- topbar
  const topbar = claimTopbar();
  const subcount = tmSubcount('… ideas');
  const searchBuilt = tmSearch({
    placeholder: 'Search ideas…',
    onInput: (v) => { searchTerm = v.trim().toLowerCase(); render(); },
  });

  // Status chip row (dynamic, populated after data loads)
  const statusRow = document.createElement('div');
  statusRow.className = 'tm-chip-row ideas__status-chips';

  // Tag chip row (dynamic)
  const tagRow = document.createElement('div');
  tagRow.className = 'tm-chip-row ideas__tag-chips';

  // Archived toggle chip
  const archivedChip = document.createElement('span');
  archivedChip.className = 'ideas__archived-chip';
  archivedChip.textContent = 'Show archived';
  archivedChip.title = 'Toggle archived ideas';
  archivedChip.addEventListener('click', () => {
    includeArchived = !includeArchived;
    archivedChip.classList.toggle('is-active', includeArchived);
    archivedChip.textContent = includeArchived ? 'Hide archived' : 'Show archived';
    render();
  });

  // Create Idea button — explicit user request, use tmAction with onClick
  const newBtn = tmAction({
    icon: '+',
    label: 'New Idea',
    variant: 'primary',
    title: 'Create a new idea',
    onClick: () => {
      openCreateIdeaModal({
        onSave: async (payload) => {
          const result = await createIdea(payload);
          // Refetch list to show the new idea
          const data = await getIdeas();
          store.setIdeas(data.ideas || data);
          render();
          return result;
        },
      });
    },
  });

  topbar?.appendChild(subcount);
  topbar?.appendChild(searchBuilt.el);
  topbar?.appendChild(statusRow);
  topbar?.appendChild(tagRow);
  topbar?.appendChild(archivedChip);
  topbar?.appendChild(newBtn);

  // ---- content area: list + detail split (or just list)
  const contentEl = document.createElement('div');
  contentEl.className = 'ideas__content';
  screen.appendChild(contentEl);

  const listEl = document.createElement('div');
  listEl.className = 'ideas__list';
  contentEl.appendChild(listEl);

  const detailEl = document.createElement('div');
  detailEl.className = 'ideas__detail';
  detailEl.hidden = true;
  contentEl.appendChild(detailEl);

  root.appendChild(screen);

  // ---- state
  let searchTerm = '';
  let includeArchived = false;
  const activeStatuses = new Set();
  const activeTags = new Set();
  let selectedId = null;

  // ---- chip renderers
  let _lastStatusChipKey = '';
  function _renderStatusChips() {
    const all = store.getIdeas() || [];
    const statuses = [...new Set(all.map(i => i.status).filter(Boolean))].sort();
    const key = statuses.join('|') + '::' + searchTerm;
    if (key === _lastStatusChipKey) return;
    _lastStatusChipKey = key;
    statusRow.innerHTML = '';
    for (const s of statuses) {
      const count = all.filter(i => !i.archived && i.status === s && _matchesSearch(i)).length;
      const chip = document.createElement('span');
      chip.className = 'ideas__status-chip';
      chip.dataset.status = s;
      chip.title = CHIP_CLICK_HINT;
      chip.textContent = `${s} · ${count}`;
      if (activeStatuses.has(s)) chip.classList.add('is-active');
      chip.addEventListener('click', (ev) => {
        const next = new Set(chipClickNext(ev, activeStatuses, s));
        activeStatuses.clear();
        for (const k of next) activeStatuses.add(k);
        statusRow.querySelectorAll('.ideas__status-chip').forEach(el => {
          el.classList.toggle('is-active', activeStatuses.has(el.dataset.status));
        });
        render();
      });
      statusRow.appendChild(chip);
    }
  }

  let _lastTagChipKey = '';
  function _renderTagChips() {
    const all = store.getIdeas() || [];
    // Collect all tags, sorted by frequency
    const freq = new Map();
    for (const idea of all) {
      for (const t of (idea.tags || [])) {
        freq.set(t, (freq.get(t) || 0) + 1);
      }
    }
    const tags = [...freq.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12).map(e => e[0]);
    const key = tags.join('|') + '::' + searchTerm;
    if (key === _lastTagChipKey) return;
    _lastTagChipKey = key;
    tagRow.innerHTML = '';
    for (const t of tags) {
      const count = all.filter(i => !i.archived && (i.tags || []).includes(t) && _matchesSearch(i)).length;
      const chip = document.createElement('span');
      chip.className = 'ideas__tag-chip';
      chip.dataset.tag = t;
      chip.title = CHIP_CLICK_HINT;
      chip.textContent = `${t} · ${count}`;
      if (activeTags.has(t)) chip.classList.add('is-active');
      chip.addEventListener('click', (ev) => {
        const next = new Set(chipClickNext(ev, activeTags, t));
        activeTags.clear();
        for (const k of next) activeTags.add(k);
        tagRow.querySelectorAll('.ideas__tag-chip').forEach(el => {
          el.classList.toggle('is-active', activeTags.has(el.dataset.tag));
        });
        render();
      });
      tagRow.appendChild(chip);
    }
  }

  function _matchesSearch(idea) {
    if (!searchTerm) return true;
    const hay = [
      idea.id || '',
      idea.title || '',
      idea.body || '',
      idea.status || '',
      ...(idea.tags || []),
    ].join(' ').toLowerCase();
    return hay.includes(searchTerm);
  }

  // ---- list renderer
  function render() {
    const all = store.getIdeas() || [];
    _renderStatusChips();
    _renderTagChips();

    const filtered = applyIdeasFilters(
      all.filter(i => _matchesSearch(i)),
      {
        statuses: [...activeStatuses],
        tags: [...activeTags],
        includeArchived,
      },
    );

    const filterActive = !!searchTerm || activeStatuses.size > 0 || activeTags.size > 0 || includeArchived;
    subcount.textContent = filterActive
      ? `${filtered.length} of ${all.length} ${pluralize(all.length, 'idea', 'ideas')}`
      : `${all.length} ${pluralize(all.length, 'idea', 'ideas')}`;

    listEl.innerHTML = '';

    if (filtered.length === 0 && filterActive) {
      listEl.appendChild(emptyState({
        headline: 'No ideas match your filters',
        hint: 'Try clearing a status chip, tag chip, or the search box.',
      }));
    } else if (filtered.length === 0) {
      listEl.appendChild(emptyState({
        headline: 'No ideas yet',
        hint: 'Click "+ New Idea" in the toolbar to capture your first idea.',
      }));
    } else {
      for (const idea of filtered) {
        listEl.appendChild(renderRow(idea));
      }
    }

    // Re-select current item if still in filtered list
    if (selectedId) {
      const row = listEl.querySelector(`[data-id="${selectedId}"]`);
      if (row) row.classList.add('is-selected');
      else showDetail(null);
    }
  }

  function renderRow(idea) {
    const row = document.createElement('div');
    row.className = 'idea-row' + (idea.archived ? ' idea-row--archived' : '');
    row.dataset.id = idea.id;
    row.tabIndex = 0;
    row.setAttribute('role', 'button');
    row.setAttribute('aria-label', idea.title);

    // id badge
    const idEl = document.createElement('span');
    idEl.className = 'idea-row__id';
    idEl.textContent = idea.id || '—';

    // title
    const titleEl = document.createElement('span');
    titleEl.className = 'idea-row__title';
    titleEl.textContent = idea.title || 'Untitled';

    // status pill
    const pill = statusPill(idea.status);

    // tags
    const tagsEl = document.createElement('span');
    tagsEl.className = 'idea-row__tags';
    for (const t of (idea.tags || [])) {
      const chip = document.createElement('span');
      chip.className = 'idea-row__tag';
      chip.textContent = t;
      tagsEl.appendChild(chip);
    }

    // created
    const whenEl = document.createElement('span');
    whenEl.className = 'idea-row__when';
    whenEl.textContent = relativeTime(idea.created);
    whenEl.title = idea.created || '';

    row.appendChild(idEl);
    row.appendChild(titleEl);
    row.appendChild(pill);
    row.appendChild(tagsEl);
    row.appendChild(whenEl);

    row.addEventListener('click', () => selectIdea(idea.id));
    row.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectIdea(idea.id); }
    });
    return row;
  }

  function selectIdea(id) {
    selectedId = id;
    listEl.querySelectorAll('.idea-row').forEach(r => {
      r.classList.toggle('is-selected', r.dataset.id === id);
    });
    const idea = (store.getIdeas() || []).find(i => i.id === id);
    if (idea) showDetail(idea);
  }

  function showDetail(idea) {
    detailEl.innerHTML = '';
    if (!idea) {
      detailEl.hidden = true;
      contentEl.classList.remove('ideas__content--detail-open');
      return;
    }
    detailEl.hidden = false;
    contentEl.classList.add('ideas__content--detail-open');

    // Back button (mobile / narrow)
    const backBtn = document.createElement('button');
    backBtn.type = 'button';
    backBtn.className = 'ideas-detail__back';
    backBtn.textContent = '← Back';
    backBtn.addEventListener('click', () => {
      selectedId = null;
      showDetail(null);
      listEl.querySelectorAll('.idea-row').forEach(r => r.classList.remove('is-selected'));
    });
    detailEl.appendChild(backBtn);

    // Head
    const head = document.createElement('div');
    head.className = 'ideas-detail__head';

    const metaRow = document.createElement('div');
    metaRow.className = 'ideas-detail__meta';

    const idBadge = document.createElement('span');
    idBadge.className = 'ideas-detail__id';
    idBadge.textContent = idea.id || '—';
    metaRow.appendChild(idBadge);
    metaRow.appendChild(statusPill(idea.status));
    if (idea.archived) {
      const archBadge = document.createElement('span');
      archBadge.className = 'ideas-detail__archived-badge';
      archBadge.textContent = 'archived';
      metaRow.appendChild(archBadge);
    }
    head.appendChild(metaRow);

    const titleH = document.createElement('h2');
    titleH.className = 'ideas-detail__title';
    titleH.textContent = idea.title || 'Untitled';
    head.appendChild(titleH);

    detailEl.appendChild(head);

    // Grid: main body + sidebar
    const grid = document.createElement('div');
    grid.className = 'ideas-detail__grid';

    const main = document.createElement('div');
    main.className = 'ideas-detail__main';

    if (idea.body) {
      const bodyEl = document.createElement('div');
      bodyEl.className = 'ideas-detail__body';
      // Render markdown if marked is available, otherwise plain text
      if (typeof window !== 'undefined' && window.marked) {
        bodyEl.innerHTML = window.marked.parse(idea.body);
      } else {
        bodyEl.textContent = idea.body;
      }
      main.appendChild(bodyEl);
    } else {
      const noBody = document.createElement('p');
      noBody.className = 'ideas-detail__nobody';
      noBody.textContent = 'No description.';
      main.appendChild(noBody);
    }

    // Sidebar
    const side = document.createElement('div');
    side.className = 'ideas-detail__side';

    // Frontmatter block
    const fmBlock = document.createElement('div');
    fmBlock.className = 'ideas-detail__side-block';

    const fmH = document.createElement('div');
    fmH.className = 'ideas-detail__side-h';
    fmH.textContent = 'Details';
    fmBlock.appendChild(fmH);

    const dl = document.createElement('dl');
    dl.className = 'ideas-detail__dl';

    function addTerm(label, value) {
      if (!value && value !== 0) return;
      const dt = document.createElement('dt');
      dt.textContent = label;
      const dd = document.createElement('dd');
      if (typeof value === 'string') {
        dd.textContent = value;
      } else {
        dd.appendChild(value);
      }
      dl.appendChild(dt);
      dl.appendChild(dd);
    }

    addTerm('Created', idea.created ? new Date(idea.created).toLocaleDateString() : null);
    addTerm('Updated', idea.updated ? new Date(idea.updated).toLocaleDateString() : null);
    addTerm('Status', idea.status || null);

    if ((idea.tags || []).length > 0) {
      const tagWrap = document.createElement('span');
      for (const t of idea.tags) {
        const tc = document.createElement('span');
        tc.className = 'idea-row__tag';
        tc.textContent = t;
        tagWrap.appendChild(tc);
      }
      addTerm('Tags', tagWrap);
    }

    fmBlock.appendChild(dl);
    side.appendChild(fmBlock);

    // Related tasks
    const relTasks = idea.related_tasks || [];
    if (relTasks.length > 0) {
      const block = document.createElement('div');
      block.className = 'ideas-detail__side-block';
      const h = document.createElement('div');
      h.className = 'ideas-detail__side-h';
      h.textContent = 'Related Tasks';
      block.appendChild(h);
      const list = document.createElement('div');
      list.className = 'ideas-detail__rel-list';
      for (const id of relTasks) {
        const a = document.createElement('a');
        a.className = 'ideas-detail__rel-pill';
        a.href = `#/kanban/${id}`;
        a.textContent = id;
        list.appendChild(a);
      }
      block.appendChild(list);
      side.appendChild(block);
    }

    // Related issues
    const relIssues = idea.related_issues || [];
    if (relIssues.length > 0) {
      const block = document.createElement('div');
      block.className = 'ideas-detail__side-block';
      const h = document.createElement('div');
      h.className = 'ideas-detail__side-h';
      h.textContent = 'Related Issues';
      block.appendChild(h);
      const list = document.createElement('div');
      list.className = 'ideas-detail__rel-list';
      for (const id of relIssues) {
        const a = document.createElement('a');
        a.className = 'ideas-detail__rel-pill ideas-detail__rel-pill--issue';
        a.href = `#/issues/${id}`;
        a.textContent = id;
        list.appendChild(a);
      }
      block.appendChild(list);
      side.appendChild(block);
    }

    // Related lessons
    const relLessons = idea.related_lessons || [];
    if (relLessons.length > 0) {
      const block = document.createElement('div');
      block.className = 'ideas-detail__side-block';
      const h = document.createElement('div');
      h.className = 'ideas-detail__side-h';
      h.textContent = 'Related Lessons';
      block.appendChild(h);
      const list = document.createElement('div');
      list.className = 'ideas-detail__rel-list';
      for (const id of relLessons) {
        const a = document.createElement('a');
        a.className = 'ideas-detail__rel-pill ideas-detail__rel-pill--lesson';
        a.href = `#/lessons/${id}`;
        a.textContent = id;
        list.appendChild(a);
      }
      block.appendChild(list);
      side.appendChild(block);
    }

    // Promoted to
    if (idea.promoted_to) {
      const block = document.createElement('div');
      block.className = 'ideas-detail__side-block';
      const h = document.createElement('div');
      h.className = 'ideas-detail__side-h';
      h.textContent = 'Promoted To';
      block.appendChild(h);
      const a = document.createElement('a');
      a.className = 'ideas-detail__rel-pill';
      a.href = `#/kanban/${idea.promoted_to}`;
      a.textContent = idea.promoted_to;
      block.appendChild(a);
      side.appendChild(block);
    }

    grid.appendChild(main);
    grid.appendChild(side);
    detailEl.appendChild(grid);
  }

  // ─── API helpers ────────────────────────────────────────────────────────────

  async function getIdeas() {
    const r = await fetch('/api/ideas');
    if (!r.ok) {
      if (r.status === 404) return { ideas: [] };
      throw new Error(`getIdeas failed: ${r.status}`);
    }
    return r.json();
  }

  async function createIdea(payload) {
    const r = await fetch('/api/ideas', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.error || `createIdea failed: ${r.status}`);
    }
    return r.json();
  }

  // ─── initial load ────────────────────────────────────────────────────────────
  if (!store.getIdeas() || store.getIdeas().length === 0) {
    try {
      const data = await getIdeas();
      store.setIdeas(data.ideas || data || []);
    } catch (e) {
      console.error('Ideas: initial load failed', e);
      store.setIdeas([]);
    }
  }
  render();

  return () => {};
}
