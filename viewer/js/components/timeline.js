// Chronological timeline with parallel-block clusters.
// Pure-DOM. The render takes session-shaped rows; sessions group together if their
// [start, end] windows overlap (transitively). Independent flat handovers can be
// passed alongside; they render outside any cluster as standalone rows.

import { formatAbsolute } from '../lib/time.js';

/**
 * @typedef {{id:string, start:string, end:string, kind?:string, parent_id?:string|null}} TimelineItem
 */

/** Group sessions whose time windows overlap (transitively). Sessions[] must be
 *  sorted ascending by start time on input. Returns an array of arrays. */
export function clusterParallelSessions(sessions) {
  const sorted = [...sessions].sort((a, b) =>
    new Date(a.start) - new Date(b.start)
  );
  const groups = [];
  let cur = [];
  let curMaxEnd = -Infinity;

  for (const s of sorted) {
    const sStart = +new Date(s.start);
    const sEnd = +new Date(s.end);
    if (cur.length && sStart <= curMaxEnd) {
      cur.push(s);
      if (sEnd > curMaxEnd) curMaxEnd = sEnd;
    } else {
      if (cur.length) groups.push(cur);
      cur = [s];
      curMaxEnd = sEnd;
    }
  }
  if (cur.length) groups.push(cur);
  return groups;
}

/** Render the timeline into `root`. Items shape:
 *    sessions: [{id, start, end, kind:'session', task_ids[], handover_ids[], recap_id}],
 *    handovers: dict id→{viewer_kind, ...}, used to render nested sub-rows
 *    independent: flat handover items not tied to a session
 *  Returns a cleanup function.
 */
export function renderTimeline(root, { sessions, handovers, independent, onSelect, dimmedIds }) {
  root.innerHTML = '';
  const wrapper = document.createElement('div');
  wrapper.className = 'tl';
  const dim = dimmedIds instanceof Set ? dimmedIds : new Set(dimmedIds || []);
  const groups = clusterParallelSessions(sessions);

  for (const group of groups) {
    if (group.length > 1) {
      const par = document.createElement('div');
      par.className = 'par-block';
      const lbl = document.createElement('div');
      lbl.className = 'par-label';
      lbl.textContent = `Parallel · ${formatRange(group)}`;
      par.appendChild(lbl);
      const grid = document.createElement('div');
      grid.className = 'par-grid';
      grid.style.gridTemplateColumns = `repeat(${group.length}, 1fr)`;
      for (const s of group) grid.appendChild(renderSessionContainer(s, handovers, onSelect, dim.has(s.id)));
      par.appendChild(grid);
      wrapper.appendChild(par);
    } else {
      wrapper.appendChild(renderSessionContainer(group[0], handovers, onSelect, dim.has(group[0].id)));
    }
  }

  for (const h of (independent || [])) {
    wrapper.appendChild(renderIndependentHandover(h, onSelect));
  }

  root.appendChild(wrapper);
  return () => { root.innerHTML = ''; };
}

function formatRange(group) {
  const start = new Date(group[0].start);
  const end = new Date(Math.max(...group.map(g => +new Date(g.end))));
  const fmt = (d) => `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
  const a = fmt(start), b = fmt(end);
  return a === b ? a : `${a} → ${b}`;
}

function renderSessionContainer(session, handovers, onSelect, dimmed) {
  const c = document.createElement('div');
  c.className = 'ses-container' + (dimmed ? ' is-dimmed' : '');

  const ho = document.createElement('div');
  ho.className = 'ho';
  ho.dataset.sessionId = session.id;
  ho.innerHTML = sessionHeadHtml(session);
  ho.addEventListener('click', () => onSelect && onSelect({ kind: 'session', id: session.id }));
  c.appendChild(ho);

  const childIds = [...(session.handover_ids || []), session.recap_id].filter(Boolean);
  if (childIds.length) {
    const kids = document.createElement('div');
    kids.className = 'ses-children';
    for (const cid of childIds) {
      const isRecap = cid === session.recap_id;
      const child = document.createElement('div');
      child.className = 'ho-child';
      if (isRecap) {
        child.dataset.recapId = cid;
        child.innerHTML = recapChildHtml(cid);
        child.addEventListener('click', () => onSelect && onSelect({ kind: 'recap', id: cid }));
      } else {
        const h = (handovers || {})[cid];
        child.dataset.handoverId = cid;
        child.innerHTML = handoverChildHtml(cid, h);
        child.addEventListener('click', () => onSelect && onSelect({ kind: 'handover', id: cid }));
      }
      kids.appendChild(child);
    }
    c.appendChild(kids);
  }
  return c;
}

function renderIndependentHandover(h, onSelect) {
  const el = document.createElement('div');
  el.className = 'ho ho-standalone';
  el.dataset.handoverId = h.id;
  el.innerHTML = handoverChildHtml(h.id, h);
  el.addEventListener('click', () => onSelect && onSelect({ kind: 'handover', id: h.id }));
  return el;
}

function sessionTimeLine(s) {
  const isDateOnly = s.time_resolution === 'date-only';
  if (isDateOnly) {
    // Legacy sessions: render the date only, no time, no arrow.
    return formatAbsolute(s.start, { time: false });
  }
  const startStr = shortTime(s.start);
  const endStr   = shortTime(s.end);
  let timeLine = (startStr === endStr) ? startStr : `${startStr} → ${endStr}`;

  // Append duration when meaningful (> 0) and not a date-only session.
  if (s.duration > 0) {
    const minutes = Math.round(s.duration / 60);
    const durStr = minutes >= 60
      ? `${Math.floor(minutes / 60)}h ${minutes % 60}m`
      : `${minutes}m`;
    timeLine += ` · ${durStr}`;
  }
  return timeLine;
}

function sessionHeadHtml(s) {
  return (
    `<div class="ho-head">`
    + `<span class="ho-kind session">SESSION</span>`
    + `<span class="ho-time mono">${escapeHtml(sessionTimeLine(s))}</span>`
    + `</div>`
    + `<div class="ho-title">${escapeHtml(s.id)}</div>`
    + `<div class="ho-foot">`
    + (s.task_ids || []).map(t => `<span class="pill task mono">${escapeHtml(t)}</span>`).join('')
    + `</div>`
  );
}

function handoverChildHtml(id, h) {
  const k = (h && h.viewer_kind) || 'standalone';
  return (
    `<div class="ho-head">`
    + `<span class="ho-kind handover ${k}">${k.toUpperCase()}</span>`
    + `<span class="ho-time mono">${escapeHtml(id)}</span>`
    + `</div>`
    + `<div class="ho-summary">${escapeHtml((h && h.tldr) || '')}</div>`
  );
}

function recapChildHtml(id) {
  return (
    `<div class="ho-head">`
    + `<span class="ho-kind recap">RECAP</span>`
    + `<span class="ho-time mono">${escapeHtml(id)}</span>`
    + `</div>`
  );
}

function shortTime(iso) {
  return formatAbsolute(iso, { date: false });
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
