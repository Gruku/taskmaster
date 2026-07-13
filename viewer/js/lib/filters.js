// Pure-logic filter / sort / group for the kanban board.
// No DOM. Tested via node --test.

export const STATUS_ORDER = ['blocked', 'todo', 'in-progress', 'in-review', 'done'];

const PRIORITY_RANK = { critical: 4, high: 3, medium: 2, low: 1 };
const SIZE_RANK     = { XS: 1, S: 2, M: 3, L: 4, XL: 5 };

export function applyFilters(tasks, f) {
  if (!Array.isArray(tasks)) return [];
  f = f || {};
  const pri    = Array.isArray(f.priorities) ? f.priorities : [];
  const epics  = Array.isArray(f.epics) ? f.epics : [];
  const areas  = Array.isArray(f.areas) ? f.areas : [];
  const phase  = f.phase || null;
  const rawSearch = (f.search || '').trim().toLowerCase();
  const negate = rawSearch.startsWith('!');
  const search = negate ? rawSearch.slice(1).trimStart() : rawSearch;

  return tasks.filter(t => {
    if (pri.length && !pri.includes(String(t.priority || '').toLowerCase())) return false;
    if (epics.length && !epics.includes(t.epic || '__none__')) return false;
    if (areas.length && !areas.includes(t.area || '__none__')) return false;
    if (phase && phase !== '__all__') {
      if (phase === '__orphans__') {
        if (t.phase) return false;
      } else if (t.phase !== phase) {
        return false;
      }
    }
    if (search) {
      const hay = [t.id, t.title, t.branch].filter(Boolean).join(' ').toLowerCase();
      const matches = hay.includes(search);
      if (negate ? matches : !matches) return false;
    }
    return true;
  });
}

export function sortTasks(tasks, sort) {
  const arr = (tasks || []).slice();
  const by  = sort?.by  || 'priority';
  const dir = sort?.dir === 'asc' ? 1 : -1;

  const cmpStr = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

  arr.sort((a, b) => {
    let av, bv;
    switch (by) {
      case 'priority':
        av = PRIORITY_RANK[String(a.priority || '').toLowerCase()] || 0;
        bv = PRIORITY_RANK[String(b.priority || '').toLowerCase()] || 0;
        return (av - bv) * dir;
      case 'size':
        av = SIZE_RANK[String(a.estimate || '').toUpperCase()] || 0;
        bv = SIZE_RANK[String(b.estimate || '').toUpperCase()] || 0;
        return (av - bv) * dir;
      case 'created':
      case 'started':
      case 'completed':
      case 'touched': {
        const field = by === 'touched' ? 'started' : by;
        av = a[field] ? Date.parse(a[field]) : null;
        bv = b[field] ? Date.parse(b[field]) : null;
        if (av == null && bv == null) return 0;
        if (av == null) return 1;
        if (bv == null) return -1;
        return (av - bv) * dir;
      }
      default:
        return cmpStr(a.id, b.id) * dir;
    }
  });
  return arr;
}

/** Returns array of {key, label, tasks} preserving spec order. */
export function groupTasks(tasks, by, phaseOrder) {
  if (by === 'status') {
    return STATUS_ORDER.map(key => ({
      key,
      label: STATUS_LABELS[key],
      tasks: (tasks || []).filter(t => (t.status || 'todo') === key),
    }));
  }
  if (by === 'phase') {
    const order = (Array.isArray(phaseOrder) && phaseOrder.length) ? phaseOrder.slice() : [];
    for (const t of tasks || []) {
      if (t.phase && !order.includes(t.phase)) order.push(t.phase);
    }
    const groups = order.map(key => ({
      key,
      label: key,
      tasks: (tasks || []).filter(t => t.phase === key),
    }));
    const orphans = (tasks || []).filter(t => !t.phase);
    groups.push({ key: '__orphans__', label: 'Orphans', tasks: orphans });
    return groups;
  }
  if (by === 'epic') {
    const seen = new Map();
    for (const t of tasks || []) {
      const k = t.epic || '__none__';
      if (!seen.has(k)) seen.set(k, []);
      seen.get(k).push(t);
    }
    return [...seen.entries()].map(([key, ts]) => ({
      key,
      label: key === '__none__' ? '— no epic —' : key,
      tasks: ts,
    }));
  }
  if (by === 'area') {
    const seen = new Map();
    for (const t of tasks || []) {
      const k = t.area || '__none__';
      if (!seen.has(k)) seen.set(k, []);
      seen.get(k).push(t);
    }
    return [...seen.entries()].map(([key, ts]) => ({
      key,
      label: key === '__none__' ? 'No area' : key,
      tasks: ts,
    }));
  }
  return [{ key: 'all', label: 'All', tasks: tasks || [] }];
}

export const STATUS_LABELS = {
  blocked: 'Blocked',
  todo: 'Todo',
  'in-progress': 'In Progress',
  'in-review': 'Waiting on human',
  done: 'Done',
};

/**
 * Cluster tasks in a single column into an ordered list of render-items.
 * Each item is either { type:'card', task } or { type:'bundle', slug, tasks:[...] }.
 * Bundle items are anchored at the first member's position; subsequent members are skipped.
 * Tasks with falsy bundle values (null, undefined, '') are emitted as card items.
 */
export function clusterBundles(tasks) {
  if (!Array.isArray(tasks)) return [];
  const seen = new Set();
  const result = [];
  for (const task of tasks) {
    const slug = task.bundle || null;
    if (!slug) {
      result.push({ type: 'card', task });
    } else if (!seen.has(slug)) {
      seen.add(slug);
      const members = tasks.filter(t => t.bundle === slug);
      result.push({ type: 'bundle', slug, tasks: members });
    }
    // else: already collected into its bundle group — skip
  }
  return result;
}

/**
 * Restrict an epic list to those that have at least one task matching the active phase filter.
 * - phase null/undefined/'__all__' → returns epics unchanged.
 * - phase '__orphans__' → epics that have a task with no phase set.
 * - phase '<id>' → epics that have a task with t.phase === id.
 */
export function epicsForPhase(epics, tasks, phase) {
  if (!Array.isArray(epics)) return [];
  if (!phase || phase === '__all__') return epics.slice();
  const taskList = Array.isArray(tasks) ? tasks : [];
  const matches = phase === '__orphans__'
    ? taskList.filter(t => !t.phase)
    : taskList.filter(t => t.phase === phase);
  const inScope = new Set(matches.map(t => t.epic).filter(Boolean));
  return epics.filter(ep => inScope.has(ep.id));
}
