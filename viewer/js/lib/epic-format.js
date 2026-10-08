// User intent: every epic figure the viewer shows — counts, percent, label, breakdown, closeable, lifecycle word — comes
// from this one file, so the Epics list, Epic detail and the Dashboard can never disagree. No DOM, no imports.

const DESIGN_STATUS = {
  exploring: { label: 'Exploring', cls: 'exploring', locked: false },
  proposed:  { label: 'Proposed',  cls: 'proposed',  locked: false },
  locked:    { label: 'Locked',    cls: 'locked',    locked: true  },
  revising:  { label: 'Revising',  cls: 'revising',  locked: false },
};

export function designBadge(status) {
  return DESIGN_STATUS[status] || { label: 'Exploring', cls: 'exploring', locked: false };
}

const COMPONENT_GLYPH = { done: '●', 'in-progress': '◐', blocked: '✗', todo: '○' };

export function componentGlyph(status) {
  return COMPONENT_GLYPH[status] || '○';
}

export const STATUS_GROUPS = Object.freeze(['in-progress', 'in-review', 'blocked', 'todo', 'done', 'archived']);
const BUCKETS = [...STATUS_GROUPS, 'other'];

// The group a task is counted in: a missing or empty status is todo, one outside STATUS_GROUPS is 'other'. Epic detail
// groups its rows with this same rule, so a group's rows and the breakdown's figure are always the same tasks.
export function statusGroupOf(task) {
  const s = task?.status == null || task.status === '' ? 'todo' : task.status;
  return STATUS_GROUPS.includes(s) ? s : 'other';
}

export function epicStats(tasks) {
  const out = { total: 0, todo: 0, 'in-progress': 0, 'in-review': 0, blocked: 0, done: 0, archived: 0, other: 0 };
  for (const t of Array.isArray(tasks) ? tasks : []) {
    if (!t || typeof t !== 'object') continue;
    out[statusGroupOf(t)] += 1;
    out.total += 1;
  }
  return out;
}

export function epicProgress(stats) {
  const total = Math.max(0, stats?.total || 0);
  const done = Math.max(0, stats?.done || 0);
  const archived = Math.max(0, stats?.archived || 0);
  const closed = Math.min(total, done + archived);
  const pct = total ? Math.round((closed / total) * 100) : 0;
  const parts = [`${closed}/${total} closed`];
  if (total) {
    parts.push(`${done} done`);
    if (archived) parts.push(`${archived} archived`);
  }
  return { total, done, archived, closed, pct, label: parts.join(' · ') };
}

export function progressPercent(stats) {
  return epicProgress(stats).pct;
}

// Closeable = every task in the epic is done or archived, derived from stats (the same formula as the progress) and
// never stored; nothing auto-archives an epic.
export function isCloseable(stats) {
  const p = epicProgress(stats);
  return p.total > 0 && p.closed === p.total;
}

export function epicBreakdown(stats) {
  const rows = BUCKETS.map((status) => ({ status, count: Math.max(0, Number(stats?.[status]) || 0) })).filter((r) => r.count > 0);
  const total = rows.reduce((n, r) => n + r.count, 0);
  if (!total) return [];
  const exact = rows.map((r) => (r.count * 100) / total);
  const pct = exact.map(Math.floor);
  let left = 100 - pct.reduce((n, x) => n + x, 0);
  const order = exact.map((x, i) => [x - Math.floor(x), i]).sort((a, b) => b[0] - a[0] || a[1] - b[1]);
  for (const [, i] of order) { if (!left) break; pct[i] += 1; left -= 1; }
  return rows.map((r, i) => ({ ...r, pct: pct[i] }));
}

export function tasksForComponent(tasks, key) {
  const list = Array.isArray(tasks) ? tasks : [];
  if (key === '_unassigned') return list.filter(t => !t.component);
  return list.filter(t => t.component === key);
}

const freeze = (table) => Object.freeze(Object.fromEntries(
  Object.entries(table).map(([value, [label, shape, tone]]) => [value, Object.freeze({ label, shape, tone })])));

// An epic's lifecycle by meaning, as the spec's status table has it: active work is in motion, a planned epic has not
// started, a done one is complete, an archived one is dropped. Drawn through status.js marker(), like every status.
export const EPIC_STATUS = freeze({
  active: ['Active', '◐', 'accent'],
  planned: ['Planned', '○', 'neutral'],
  done: ['Done', '●', 'success'],
  archived: ['Archived', '✕', 'neutral'],
});

export function epicStatusMeta(value) {
  if (value == null || value === '') return { ...EPIC_STATUS.active };
  if (typeof value !== 'string') return { label: '—', shape: '○', tone: 'neutral' };
  if (Object.hasOwn(EPIC_STATUS, value)) return { ...EPIC_STATUS[value] };
  // An unknown status reads like the known ones: "on-hold" → "On hold".
  const words = value.replace(/[-_]+/g, ' ').trim();
  return { label: words ? words[0].toUpperCase() + words.slice(1) : '—', shape: '○', tone: 'neutral' };
}
