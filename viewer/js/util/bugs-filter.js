// User intent: what the Bugs list shows and in what order — every bug status is a filter (fixed and adopted bugs used to
// be unreachable), archived bugs come in only through their own toggle, and the saved filter survives its old shape.
import { BUG_STATUS, statusMeta, severityKey, SEVERITY } from '../components/status.js';

export const BUG_SORTS = [{ value: 'newest', label: 'Newest first' }, { value: 'oldest', label: 'Oldest first' },
  { value: 'severity', label: 'Severity' }, { value: 'id', label: 'ID' }];

const DEFAULT_STATUSES = ['open', 'shelved'];
const SEVERITY_RANK = Object.fromEntries(Object.keys(SEVERITY).map((k, i) => [k, i]));

const statusOf = (bug) => bug?.status || 'open';

export function isArchivedBug(bug) {
  return bug?.archived === true || bug?.status === 'archived';
}

// `saved` is prefs.screens.bugs. The legacy shape was { filters: { open, shelved, archive } }.
export function bugPrefs(saved) {
  const s = saved && typeof saved === 'object' ? saved : {};
  const legacy = s.filters && typeof s.filters === 'object' ? s.filters : null;
  let statuses;
  let archived;
  if (Array.isArray(s.statuses)) {
    // 'archived' is the toggle's, never a chip: kept here it could never be released.
    statuses = s.statuses.filter((v) => typeof v === 'string' && v !== 'archived');
    archived = s.archived === true;
  } else if (legacy) {
    statuses = DEFAULT_STATUSES.filter((k) => legacy[k]);
    archived = !!legacy.archive;
  } else {
    statuses = [...DEFAULT_STATUSES];
    archived = s.archived === true;
  }
  const sort = BUG_SORTS.some((o) => o.value === s.sort) ? s.sort : 'newest';
  return { statuses, archived, sort };
}

const haystack = (bug) => [bug.id, bug.title, ...(Array.isArray(bug.components) ? bug.components : [])]
  .filter((v) => v != null).join(' ').toLowerCase();

export function filterBugs(bugs, { statuses = [], archived = false, search = '' } = {}) {
  const q = String(search ?? '').trim().toLowerCase();
  return (bugs ?? []).filter((b) => (archived || !isArchivedBug(b))
    // No chip is ever 'archived', so a bug whose status is archived is admitted by the toggle alone.
    && (!statuses.length || statuses.includes(statusOf(b)) || (archived && statusOf(b) === 'archived'))
    && (!q || haystack(b).includes(q)));
}

const stamp = (bug) => {
  const ms = Date.parse(bug.discovered ?? '');
  return Number.isNaN(ms) ? null : ms;
};
const byId = (a, b) => String(a.id ?? '').localeCompare(String(b.id ?? ''), undefined, { numeric: true });

// Undated bugs go last either way; ties fall back to the id, newest (highest) first.
function byDate(dir) {
  return (a, b) => {
    const x = stamp(a);
    const y = stamp(b);
    if (x !== y) {
      if (x == null) return 1;
      if (y == null) return -1;
      return dir * (x - y);
    }
    return -byId(a, b);
  };
}

const newest = byDate(-1);
const SORTS = {
  newest,
  oldest: byDate(1),
  severity: (a, b) => {
    const rank = (bug) => SEVERITY_RANK[severityKey(bug.severity)] ?? Object.keys(SEVERITY).length;
    return rank(a) - rank(b) || newest(a, b);
  },
  id: byId,
};

export function sortBugs(bugs, sort) {
  return [...(bugs ?? [])].sort(SORTS[sort] ?? newest);
}

// `bugs` are those the archived toggle admits; a pressed status none of them has is still offered, at 0, so it can be
// released. Archived is never a chip: the toggle owns it.
export function bugStatusChips(bugs, pressed = []) {
  const counts = new Map();
  for (const b of bugs ?? []) counts.set(statusOf(b), (counts.get(statusOf(b)) ?? 0) + 1);
  for (const v of pressed ?? []) if (!counts.has(v)) counts.set(v, 0);
  counts.delete('archived');
  const known = Object.keys(BUG_STATUS).filter((v) => counts.has(v));
  const unknown = [...counts.keys()].filter((v) => !Object.hasOwn(BUG_STATUS, v)).sort();
  return [...known, ...unknown].map((value) => ({
    value, label: statusMeta('bug', value).label, count: counts.get(value), pressed: (pressed ?? []).includes(value),
  }));
}
