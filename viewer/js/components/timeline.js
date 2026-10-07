// User intent: the Sessions timeline — every session and handover is a real button that leads with its title (the
// tldr) and keeps the slug as a subline, sessions whose windows overlap sit together in a parallel block, and the row
// shown in the rail is marked. Built from nodes: row data is never markup.

import { formatAbsolute, formatDurationCompact } from '../lib/time.js';
import { truncate } from '../lib/text.js';
import { h } from '../util/h.js';
import { handoverStatusMarker } from './right-rail.js';

/**
 * @typedef {{id:string, start:string, end:string, kind?:string, parent_id?:string|null}} TimelineItem
 */

/** Group sessions whose time windows overlap (transitively).
 *
 *  Algorithm requires ascending-by-start input to walk in a single pass, so we
 *  sort internally. The returned cluster array is then reversed so that the
 *  newest cluster is first (matching the server's DESC ordering at
 *  taskmaster_v3.py:3884). Members WITHIN each cluster remain ascending so the
 *  parallel-block grid reads left-to-right in chronological order. */
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
  groups.reverse(); // newest cluster first; members within stay ASC
  return groups;
}

// 'mid-task' → 'Mid-task'; a handover with no kind is just a handover.
export function kindLabel(kind) {
  const k = String(kind ?? '').trim();
  return k ? k.charAt(0).toUpperCase() + k.slice(1) : 'Handover';
}

/** Render the timeline into `root`.
 *    sessions: [{id, start, end, tldr?, task_ids[], handover_ids[]}] — the handovers to show under each
 *    handovers: id → {viewer_kind, status, tldr}
 *    onSelect({ kind: 'session' | 'handover', id }, button)
 *    selected: { kind, id } | null — the row the open rail shows, marked aria-current. Rows say they control the rail
      only while one is open: with none, `#right-rail` does not exist and the reference would point at nothing.
 *  Returns a cleanup function.
 */
export function renderTimeline(root, { sessions, handovers, onSelect, selected = null }) {
  const ctx = { handovers: handovers || {}, onSelect, selected };
  const container = (s) => sessionContainer(s, ctx);
  const wrapper = h('div', { class: 'tl' });
  for (const group of clusterParallelSessions(sessions || [])) {
    if (group.length > 1) {
      wrapper.append(h('div', { class: 'par-block' },
        h('div', { class: 'par-label' }, `Parallel · ${formatRange(group)}`),
        h('div', { class: 'par-grid' }, group.map(container))));
    } else {
      wrapper.append(container(group[0]));
    }
  }
  root.replaceChildren(wrapper);
  return () => { root.replaceChildren(); };
}

function formatRange(group) {
  const start = new Date(group[0].start);
  const end = new Date(Math.max(...group.map(g => +new Date(g.end))));
  const fmt = (d) => `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
  const a = fmt(start), b = fmt(end);
  return a === b ? a : `${a} → ${b}`;
}

// A row of the timeline: a button holding spans only, so nothing in it is a control of its own.
function row(kind, id, attrs, { onSelect, selected }, children) {
  const btn = h('button', { type: 'button', ...attrs, 'aria-controls': selected ? 'right-rail' : null }, children);
  if (selected && selected.kind === kind && selected.id === id) btn.setAttribute('aria-current', 'true');
  btn.addEventListener('click', () => onSelect?.({ kind, id }, btn));
  return btn;
}

// The title is the tldr when there is one, and then the id follows as the slug.
const titleAndSlug = (tldr, id) => [
  truncate(tldr || id, { lines: 2, className: 'ho-title' }),
  tldr ? truncate(id, { className: 'ho-slug' }) : null,
];

function sessionContainer(session, ctx) {
  const tasks = session.task_ids || [];
  const head = row('session', session.id, { class: 'ho', 'data-session-id': session.id }, ctx, [
    h('span', { class: 'ho-head' },
      h('span', { class: 'ho-kind' }, 'Thread'),
      h('span', { class: 'ho-time' }, sessionTimeLine(session))),
    ...titleAndSlug(session.tldr, session.id),
    tasks.length ? h('span', { class: 'ho-tasks' }, tasks.map((t) => h('span', { class: 'ho-task' }, String(t)))) : null,
  ]);
  const childIds = session.handover_ids || [];
  return h('div', { class: 'ses-container' },
    head,
    childIds.length ? h('div', { class: 'ses-children' }, childIds.map((cid) => handoverRow(cid, ctx.handovers[cid] || {}, ctx))) : null);
}

function handoverRow(id, meta, ctx) {
  const status = meta.status || 'open';
  return row('handover', id, { class: 'ho-child', 'data-handover-id': id }, ctx, [
    h('span', { class: 'ho-head' },
      h('span', { class: 'ho-kind' }, kindLabel(meta.viewer_kind)),
      h('span', { class: 'ho-status' }, handoverStatusMarker(status))),
    ...titleAndSlug(meta.tldr, id),
  ]);
}

// A session that ends on a later local day than it starts shows both dates — bare times would contradict its duration.
function spansDays(start, end) {
  const a = new Date(start), b = new Date(end);
  if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime())) return false;
  return a.toDateString() !== b.toDateString();
}

export function sessionTimeLine(s) {
  const isDateOnly = s.time_resolution === 'date-only';
  if (isDateOnly) {
    // Legacy sessions: render the date only, no time, no arrow.
    return formatAbsolute(s.start, { time: false });
  }
  const multiDay = spansDays(s.start, s.end);
  const startStr = multiDay ? formatAbsolute(s.start) : shortTime(s.start);
  const endStr   = multiDay ? formatAbsolute(s.end)   : shortTime(s.end);
  let timeLine = (startStr === endStr) ? startStr : `${startStr} → ${endStr}`;

  // Append duration when meaningful (> 0) and not a date-only session.
  if (s.duration > 0) {
    timeLine += ` · ${formatDurationCompact(s.duration * 1000)}`;
  }
  return timeLine;
}

function shortTime(iso) {
  return formatAbsolute(iso, { date: false });
}
