// Epic color palette — spec §5. Auto-assigned in declaration order
// unless the epic record carries an explicit `color` field.

export const EPIC_PALETTE = [
  '#6ea8ff', // viewer-redesign (blue)
  '#b585e8', // narrative-continuity (purple)
  '#5fcdb8', // filter-bar (teal)
  '#e8a34d', // migration-tooling (amber)
  '#e87a85', // blast-radius (coral)
  '#a8c958', // spec-review (lime)
];

const FALLBACK = '#7c8290'; // var(--ink-3)

/** Build {epicId → hexColor} for the epic list. */
export function assignEpicColors(epics) {
  const map = {};
  if (!Array.isArray(epics)) return map;
  let idx = 0;
  for (const ep of epics) {
    if (!ep || !ep.id) continue;
    if (ep.color) {
      map[ep.id] = ep.color;
    } else {
      map[ep.id] = EPIC_PALETTE[idx % EPIC_PALETTE.length];
      idx += 1;
    }
  }
  return map;
}

export function epicColor(epicId, colorMap) {
  if (!epicId) return FALLBACK;
  return (colorMap && colorMap[epicId]) || FALLBACK;
}

/** Inline style string defining --epic and --epic-soft (14% alpha tint). */
export function epicCssVar(hex) {
  const c = hex || FALLBACK;
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(c);
  let rgbStr = '124, 130, 144';
  if (m) {
    rgbStr = `${parseInt(m[1], 16)}, ${parseInt(m[2], 16)}, ${parseInt(m[3], 16)}`;
  }
  return `--epic: ${c}; --epic-soft: rgba(${rgbStr}, 0.14)`;
}
