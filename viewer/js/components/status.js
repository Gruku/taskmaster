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

const STATUS_KINDS = { task: TASK_STATUS };

// None of the viewer's local fonts carries these glyphs, so the stylesheet draws each shape by name;
// the glyph stays in the DOM as the fallback.
const DRAWN = { '○': 'ring', '◐': 'half', '▲': 'triangle', '◆': 'diamond', '●': 'dot', '✕': 'cross' };

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

function marker({ label, shape, tone }) {
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
