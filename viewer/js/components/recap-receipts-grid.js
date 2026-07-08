import { renderDiffRow } from './diff-row.js';

const escapeHtml = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export function renderReceiptsGrid(diff) {
  if (diff && diff._unavailable) {
    return (
      `<div class="receipts-grid receipts-grid--unavailable">`
      + `<div class="rcard rcard--note">`
      + `<div class="rcard-h"><span class="ttl">Receipts</span></div>`
      + `<div class="rcard-body"><div class="empty">Snapshots unavailable for this recap — file-level changes can't be shown.</div></div>`
      + `</div>`
      + `</div>`
    );
  }
  return (
    `<div class="receipts-grid">`
    + tasksCard(diff)
    + filesCard(diff)
    + issuesCard(diff)
    + `</div>`
  );
}

function cardShell({ title, count, body }) {
  return (
    `<div class="rcard">`
    + `<div class="rcard-h">`
    + `<span class="ttl">${title}</span>`
    + `<span class="cnt mono">${count}</span>`
    + `</div>`
    + `<div class="rcard-body">${body || `<div class="empty">No changes</div>`}</div>`
    + `</div>`
  );
}

function tasksCard(d) {
  const rows = [];
  for (const t of d.tasks_added || []) {
    rows.push(renderDiffRow({ kind: 'add',
      body: `<span class="id mono">${escapeHtml(t.id)}</span> ${escapeHtml(t.title || '')}` }));
  }
  for (const t of d.tasks_changed || []) {
    const from = t.from && t.from.status;
    const to   = t.to   && t.to.status;
    rows.push(renderDiffRow({ kind: 'mod',
      body: `<span class="id mono">${escapeHtml(t.id)}</span>`
          + `<span class="from">${escapeHtml(from||'')}</span>`
          + `<span class="arrow">→</span>`
          + `<span class="to">${escapeHtml(to||'')}</span>` }));
  }
  for (const t of d.tasks_removed || []) {
    rows.push(renderDiffRow({ kind: 'del',
      body: `<span class="id mono">${escapeHtml(t.id)}</span> ${escapeHtml(t.title || '')}` }));
  }
  return cardShell({
    title: 'Tasks',
    count: rows.length,
    body: rows.join(''),
  });
}

function filesCard(d) {
  const rows = (d.files_touched || []).map(f => {
    const path = typeof f === 'string' ? f : f.path;
    const plus = (typeof f === 'object' && f.plus) || 0;
    const minus = (typeof f === 'object' && f.minus) || 0;
    return (
      `<div class="files-row mod">`
      + `<span class="pre">~</span>`
      + `<span class="path mono">${escapeHtml(path)}</span>`
      + `<span class="churn mono"><span class="plus">+${plus}</span> <span class="minus">-${minus}</span></span>`
      + `</div>`
    );
  });
  return cardShell({ title: 'Files touched', count: rows.length, body: rows.join('') });
}

function issuesCard(d) {
  const rows = [];
  for (const i of d.issues_opened || []) {
    rows.push(renderDiffRow({ kind: 'add',
      body: `<span class="sev ${(i.severity||'').toLowerCase()}">${escapeHtml(i.severity||'')}</span>`
          + `<span class="id mono">${escapeHtml(i.id)}</span> ${escapeHtml(i.title||'')}` }));
  }
  for (const i of d.issues_transitioned || []) {
    rows.push(renderDiffRow({ kind: 'mod',
      body: `<span class="id mono">${escapeHtml(i.id)}</span>`
          + ` <span class="from">${escapeHtml(i.from||'')}</span>`
          + ` <span class="arrow">→</span>`
          + ` <span class="to">${escapeHtml(i.to||'')}</span>` }));
  }
  return cardShell({ title: 'Issues', count: rows.length, body: rows.join('') });
}

export default renderReceiptsGrid;
