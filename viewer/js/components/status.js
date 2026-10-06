// User intent: one status language for the whole viewer — a status or priority is always the same shape plus the same
// word, so "Done" can never look like "In progress". The shape carries the hue; the word stays readable text.

const freeze = (table) => Object.freeze(Object.fromEntries(
  Object.entries(table).map(([value, [label, shape, tone]]) => [value, Object.freeze({ label, shape, tone })])));

export const TASK_STATUS = freeze({
  todo: ['Todo', '○', 'neutral'],
  'in-progress': ['In progress', '◐', 'accent'],
  'in-review': ['In review', '▲', 'warning'],
  blocked: ['Blocked', '◆', 'critical'],
  done: ['Done', '●', 'success'],
  archived: ['Archived', '✕', 'neutral'],
});

export const PRIORITY = freeze({
  critical: ['Critical', '◆', 'critical'],
  high: ['High', '▲', 'orange'],
  medium: ['Medium', '●', 'warning'],
  low: ['Low', '○', 'neutral'],
});

// By meaning, as the spec's status table has it: an open bug is not started, a fixed one complete, one adopted into a
// task or promoted to an issue has moved on, a shelved or archived one is dropped. An open bug blocks its task from
// closing, but the alarm is the task's "open bugs blocking close" line, not every bug row.
export const BUG_STATUS = freeze({
  open: ['Open', '○', 'neutral'],
  fixed: ['Fixed', '●', 'success'],
  adopted: ['Adopted', '→', 'neutral'],
  promoted: ['Promoted', '→', 'neutral'],
  shelved: ['Shelved', '✕', 'neutral'],
  archived: ['Archived', '✕', 'neutral'],
});

// By meaning too: an idea being explored is in motion, a candidate or a parked one is not started, a promoted one has
// moved on into its task, a dropped one is dropped. The order is the order a form offers them in.
export const IDEA_STATUS = freeze({
  exploring: ['Exploring', '◐', 'accent'],
  candidate: ['Candidate', '○', 'neutral'],
  'parking-lot': ['Parking lot', '○', 'neutral'],
  promoted: ['Promoted', '→', 'neutral'],
  dropped: ['Dropped', '✕', 'neutral'],
});

// By meaning, as the spec's status table has it: an open issue is not started, one under investigation in motion, a fixed
// one complete, a won't-fix one dropped, a duplicate has moved on into the issue it duplicates. Order = the server's.
export const ISSUE_STATUS = freeze({
  open: ['Open', '○', 'neutral'],
  investigating: ['Investigating', '◐', 'accent'],
  fixed: ['Fixed', '●', 'success'],
  wontfix: ["Won't fix", '✕', 'neutral'],
  duplicate: ['Duplicate', '→', 'neutral'],
});

// A severity reads exactly like the priority of the same name: Critical is one look wherever it appears.
export const SEVERITY = freeze({
  critical: ['Critical', '◆', 'critical'],
  high: ['High', '▲', 'orange'],
  medium: ['Medium', '●', 'warning'],
  low: ['Low', '○', 'neutral'],
});

const SEVERITY_CODES = { p0: 'critical', p1: 'high', p2: 'medium', p3: 'low' };

const STATUS_KINDS = { task: TASK_STATUS, bug: BUG_STATUS, idea: IDEA_STATUS, issue: ISSUE_STATUS };

// None of the viewer's local fonts carries these glyphs, so the stylesheet draws each shape by name;
// the glyph stays in the DOM as the fallback.
const DRAWN = { '○': 'ring', '◐': 'half', '▲': 'triangle', '◆': 'diamond', '●': 'dot', '→': 'arrow', '✕': 'cross' };

// Values arrive from task data and may be anything; one the table does not know is shown as it is, neutral.
function lookup(table, value) {
  if (table && typeof value === 'string' && Object.hasOwn(table, value)) return { ...table[value] };
  return { label: String(value || '—'), shape: '○', tone: 'neutral' };
}

export function statusMeta(kind, value) {
  return lookup(typeof kind === 'string' && Object.hasOwn(STATUS_KINDS, kind) ? STATUS_KINDS[kind] : null, value);
}

export function priorityMeta(value) {
  return lookup(PRIORITY, value);
}

// A marker for a state that has no table of its own (a gate, a review verdict): the caller names the word, shape and tone.
export function marker({ label, shape, tone }) {
  const el = document.createElement('span');
  el.className = `marker marker--${tone}`;
  const shapeEl = document.createElement('span');
  shapeEl.className = 'marker__shape';
  shapeEl.setAttribute('aria-hidden', 'true');
  shapeEl.textContent = shape;
  if (Object.hasOwn(DRAWN, shape)) shapeEl.dataset.shape = DRAWN[shape];
  const wordEl = document.createElement('span');
  wordEl.className = 'marker__word';
  wordEl.textContent = label;
  el.append(shapeEl, wordEl);
  return el;
}

export function statusMarker(kind, value) {
  return marker(statusMeta(kind, value));
}

export function priorityMarker(value) {
  return marker(priorityMeta(value));
}

// Severities arrive as 'P0'…'P3' or as words, in any case and spacing; anything else is no known severity.
export function severityKey(value) {
  if (typeof value !== 'string') return null;
  const v = value.trim().toLowerCase();
  if (Object.hasOwn(SEVERITY_CODES, v)) return SEVERITY_CODES[v];
  return Object.hasOwn(SEVERITY, v) ? v : null;
}

// An unknown severity word is still shown as itself; only no severity at all (missing, blank, not a string) is null.
export function severityMeta(value) {
  const key = severityKey(value);
  if (key) return { ...SEVERITY[key] };
  if (typeof value === 'string' && value.trim()) return { label: value.trim(), shape: '○', tone: 'neutral' };
  return null;
}

export function severityMarker(value) {
  const meta = severityMeta(value);
  return meta ? marker(meta) : null;
}
