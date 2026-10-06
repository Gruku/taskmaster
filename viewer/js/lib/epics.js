// User intent: epics are told apart by a categorical swatch from the theme's `--cat-N` tokens, never by a hex
// the theme cannot adjust, so every epic colour reads correctly in dark and light.

export function activeEpic({tasks = [], epics = []} = {}) {
  const counts = new Map();
  for (const t of tasks) {
    if (t.status === 'in-progress' || t.status === 'in-review') counts.set(t.epic, (counts.get(t.epic) || 0) + 1);
  }
  const lexical = (a, b) => a < b ? -1 : a > b ? 1 : 0;
  if (counts.size) return [...counts].sort((a, b) => b[1] - a[1] || lexical(a[0], b[0]))[0][0];
  return epics.filter(e => e.status === 'active').map(e => e.id).sort(lexical)[0] || epics[0]?.id || '';
}

/** The one place an epic record's own `color` is read: honoured only when it names one of the six swatches
 *  (N, 'N', 'cat-N' or '--cat-N'); a hex or a name is ignored because the theme cannot adjust it. */
function ownSwatch(epic) {
  const c = epic?.color;
  if (Number.isInteger(c)) return c >= 1 && c <= 6 ? c : null;
  if (typeof c !== 'string') return null;
  const m = /^(?:(?:--)?cat-)?([1-6])$/.exec(c.trim());
  return m ? Number(m[1]) : null;
}

/** The epic's categorical swatch (1–6, for `--cat-N`): its own swatch when it names one, else its position
 *  among the epics with an id, wrapping after 6; null when not found. */
export function epicSwatch(epicId, epics) {
  if (!epicId || !Array.isArray(epics)) return null;
  const withId = epics.filter((ep) => ep && ep.id);
  const at = withId.findIndex((ep) => ep.id === epicId);
  return at < 0 ? null : (ownSwatch(withId[at]) ?? (at % 6) + 1);
}

/** {epicId → {name, swatch}} for every epic with an id, in order; the same swatches `epicSwatch` gives. */
export function epicIndex(epics) {
  const index = new Map();
  if (!Array.isArray(epics)) return index;
  let at = 0;
  for (const ep of epics) {
    if (!ep || !ep.id) continue;
    if (!index.has(ep.id)) {
      const name = typeof ep.name === 'string' && ep.name !== '' ? ep.name : ep.id;
      index.set(ep.id, { name, swatch: ownSwatch(ep) ?? (at % 6) + 1 });
    }
    at += 1;
  }
  return index;
}

/** {epicId → swatch number 1–6} for the epic list. */
export function assignEpicColors(epics) {
  const map = {};
  for (const [id, { swatch }] of epicIndex(epics)) map[id] = swatch;
  return map;
}

export function epicColor(epicId, colorMap) {
  if (!epicId || !colorMap) return null;
  return colorMap[epicId] ?? null;
}

/** Inline style string pointing --epic at the swatch's categorical token; '' leaves the token default. */
export function epicCssVar(swatch) {
  return Number.isInteger(swatch) && swatch >= 1 && swatch <= 6 ? `--epic: var(--cat-${swatch})` : '';
}
