// plugins/taskmaster/viewer/js/lib/epic-format.js
// Pure formatting helpers for the epic detail/list views. No DOM, no imports —
// unit-tested with node:test, consumed by mountEpicDetail + the epics list.

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

export function progressPercent(stats) {
  const total = stats?.total || 0;
  if (!total) return 0;
  const done = (stats.done || 0) + (stats.archived || 0);
  return Math.round((done / total) * 100);
}

// Closeable = every task in the epic is done or archived. Derived client-side
// from stats (same formula as progressPercent) so it's testable without a
// server round-trip; never stored, never auto-archives (see epic B task 4).
export function closeableBadge(stats) {
  const total = stats?.total || 0;
  if (!total) return '';
  const done = (stats?.done || 0) + (stats?.archived || 0);
  if (done !== total) return '';
  return `<span class="epic-closeable" title="All tasks done or archived">Closeable</span>`;
}

export function tasksForComponent(tasks, key) {
  const list = Array.isArray(tasks) ? tasks : [];
  if (key === '_unassigned') return list.filter(t => !t.component);
  return list.filter(t => t.component === key);
}
