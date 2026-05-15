// Shared task card. Renders Minimal or Full density.
//
// Usage:
//   const el = renderCard({ task, density: 'full', epicColors, autoState, groupBy, now });
//
// Inputs:
//   task         — backlog task object (v3 schema)
//   density      — 'minimal' | 'full'
//   epicColors   — {epicId → hex} from lib/epics.js#assignEpicColors
//   autoState    — current auto-mode state object (or null) — used to attach a live block
//   groupBy      — 'status' | 'phase' | 'epic' (drives whether status pill renders)
//   now          — ms (for time-in-status) — defaults to Date.now()

import { formatTimeInStatus, classifyTimeInStatus, isoToMs, formatElapsed } from '../lib/time.js';
import { epicColor, epicCssVar } from '../lib/epics.js';
import { bindCopy } from '../lib/copy.js';
import { isAutoRunning } from '../lib/auto-state.js';
import { renderAutoModeLiveBlock } from './auto-mode-live-block.js';

const PRIORITY_LABELS = { critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low' };
const STATUS_LABELS   = { blocked: 'Blocked', todo: 'Todo', 'in-progress': 'In Progress', 'in-review': 'In Review', done: 'Done' };

export function renderCard({ task, density = 'full', epicColors = {}, autoState = null, groupBy = 'status', now = Date.now() } = {}) {
  if (!task || !task.id) return document.createComment('empty card');

  const card = document.createElement('div');
  card.className = 'card-task ' + density;
  card.dataset.taskId = task.id;

  const isAuto = !!(isAutoRunning(autoState) && autoState.cursor && autoState.cursor.task_id === task.id);
  if (isAuto) card.classList.add('auto');

  // Recently-moved highlight: 24h after status change (spec §3.6).
  const startedMs = isoToMs(task.started);
  if (startedMs && (now - startedMs) < 24 * 60 * 60 * 1000) {
    card.classList.add('recent');
  }

  // Inline epic CSS variables.
  card.setAttribute('style', epicCssVar(epicColor(task.epic, epicColors)));

  const body = document.createElement('div');
  body.className = 'card-body';
  card.appendChild(body);

  // Click navigates to task detail.
  card.addEventListener('click', (ev) => {
    if (ev.target.closest('.card-id') || ev.target.closest('.card-branch') || ev.target.closest('.cmp-icon-btn')) return;
    location.hash = '#/task/' + encodeURIComponent(task.id);
  });

  // ── Meta line: id · priority · size · time-in-status ──
  const meta = document.createElement('div');
  meta.className = 'card-meta';

  const id = document.createElement('span');
  id.className = 'card-id';
  id.innerHTML = `<span class="label-text">${escapeHtml(task.id)}</span><span class="copy-glyph">⧉</span>`;
  bindCopy(id, task.id);
  meta.appendChild(id);

  const sep = document.createElement('span');
  sep.style.cssText = 'color:var(--ink-3); opacity:0.4;';
  sep.textContent = '·';
  meta.appendChild(sep);

  const pri = String(task.priority || 'medium').toLowerCase();
  const priEl = document.createElement('span');
  priEl.className = 'card-pri ' + pri;
  priEl.textContent = PRIORITY_LABELS[pri] || PRIORITY_LABELS.medium;
  meta.appendChild(priEl);

  if (task.estimate) {
    const sz = document.createElement('span');
    sz.className = 'card-size';
    sz.textContent = task.estimate;
    meta.appendChild(sz);
  }

  // Time-in-status — anchored at started (or created).
  const tisAnchor = startedMs || isoToMs(task.created);
  const tisText   = formatTimeInStatus(tisAnchor, now);
  if (tisText) {
    const tis = document.createElement('span');
    tis.className = 'card-tis ' + classifyTimeInStatus(tisAnchor, now);
    tis.textContent = tisText;
    meta.appendChild(tis);
  }
  body.appendChild(meta);

  // ── Title ──
  const title = document.createElement('div');
  title.className = 'card-title';
  title.textContent = task.title || '(untitled)';
  body.appendChild(title);

  // Minimal density stops here (plus auto live block + status pill if grouped non-status).
  if (density === 'minimal') {
    if (groupBy !== 'status' && task.status) appendStatusPill(body, task.status);
    if (isAuto) appendLiveBlock(card, autoState);
    return card;
  }

  // ── Chip row: epic · spec-review · deps · sub-repo ──
  const chipRow = document.createElement('div');
  chipRow.className = 'card-chip-row';
  let chipRowHasContent = false;

  if (task.epic) {
    const ec = document.createElement('span');
    ec.className = 'card-epic-chip';
    ec.innerHTML = `<span class="swatch"></span>${escapeHtml(task.epic)}`;
    chipRow.appendChild(ec);
    chipRowHasContent = true;
  }
  if (task.spec_review) {
    const verdict = task.spec_review.verdict || task.spec_review;
    const known = ['pass', 'warn', 'fail'];
    if (known.includes(verdict)) {
      const sb = document.createElement('span');
      sb.className = 'card-spec-badge ' + verdict;
      const glyph = verdict === 'pass' ? '✓' : verdict === 'warn' ? '!' : '✗';
      sb.textContent = `${glyph} spec`;
      chipRow.appendChild(sb);
      chipRowHasContent = true;
    }
  }
  if (typeof task.depends_on_unmet_count === 'number' && task.depends_on_unmet_count > 0) {
    const db = document.createElement('span');
    db.className = 'card-dep-badge' + (task.status !== 'done' ? ' blocking' : '');
    db.textContent = `↳ ${task.depends_on_unmet_count} unmet`;
    chipRow.appendChild(db);
    chipRowHasContent = true;
  } else if (Array.isArray(task.depends_on) && task.depends_on.length) {
    const db = document.createElement('span');
    db.className = 'card-dep-badge';
    db.textContent = `↳ ${task.depends_on.length}`;
    chipRow.appendChild(db);
    chipRowHasContent = true;
  }
  if (task.sub_repo) {
    const sr = document.createElement('span');
    sr.className = 'card-subrepo-chip';
    sr.textContent = task.sub_repo;
    chipRow.appendChild(sr);
    chipRowHasContent = true;
  }
  if (groupBy !== 'status' && task.status) {
    appendStatusPill(chipRow, task.status);
    chipRowHasContent = true;
  }
  if (chipRowHasContent) body.appendChild(chipRow);

  // ── Footer: branch + action icons ──
  const footer = document.createElement('div');
  footer.className = 'card-footer';

  const branch = document.createElement('span');
  branch.className = 'card-branch' + (task.branch ? '' : ' empty');
  branch.innerHTML = `<span class="glyph">⎇</span><span class="branch-text">${escapeHtml(task.branch || '— no branch —')}</span>${task.branch ? '<span class="copy-glyph">⧉</span>' : ''}`;
  if (task.branch) bindCopy(branch, task.branch);
  footer.appendChild(branch);

  const actions = document.createElement('span');
  actions.className = 'card-actions';
  if (task.docs && Object.keys(task.docs).length) {
    const docsBtn = document.createElement('button');
    docsBtn.className = 'cmp-icon-btn';
    docsBtn.title = 'Open primary doc';
    docsBtn.textContent = '📄';
    docsBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const first = Object.values(task.docs)[0];
      if (first) window.open(first, '_blank', 'noopener');
    });
    actions.appendChild(docsBtn);
  }
  footer.appendChild(actions);
  body.appendChild(footer);

  // ── Callout: blocked + unmet deps ──
  if (task.status === 'blocked' && Array.isArray(task.blockers) && task.blockers.length) {
    const callout = document.createElement('div');
    callout.className = 'card-callout warn';
    callout.textContent = `⛔ blocked: ${task.blockers.length} unmet`;
    body.appendChild(callout);
  }

  // ── Auto-mode live block ──
  if (isAuto) appendLiveBlock(card, autoState);

  return card;
}

function appendStatusPill(parent, status) {
  const pill = document.createElement('span');
  pill.className = 'card-status-pill ' + status;
  pill.innerHTML = `<span class="dot"></span>${STATUS_LABELS[status] || status}`;
  parent.appendChild(pill);
}

function appendLiveBlock(card, autoState) {
  const block = renderAutoModeLiveBlock({ autoState });
  if (block) card.appendChild(block);
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// Plan 4 dashboard widgets import these by name; thin density-bound aliases over renderCard.
export const renderMinimalCard = (task, opts = {}) => renderCard({ task, density: 'minimal', ...opts });
export const renderFullCard    = (task, opts = {}) => renderCard({ task, density: 'full',    ...opts });
