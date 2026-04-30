// Pure layout engine for the dashboard bento. No DOM, no I/O.

const RAILS = ['left', 'right', 'bottom'];

function normalizeInstance(inst, fallbackIndex) {
  return {
    id: inst.id,
    type: inst.type,
    size: inst.size || 'medium',
    rail: RAILS.includes(inst.rail) ? inst.rail : 'left',
    index: typeof inst.index === 'number' ? inst.index : fallbackIndex,
  };
}

export function computePlacements(layout) {
  const normalized = (layout || []).map((inst, i) => normalizeInstance(inst, i));
  // Stable order by (rail, index, original position).
  const ordered = normalized
    .map((inst, i) => ({ inst, i }))
    .sort((a, b) => {
      if (a.inst.rail !== b.inst.rail) return RAILS.indexOf(a.inst.rail) - RAILS.indexOf(b.inst.rail);
      if (a.inst.index !== b.inst.index) return a.inst.index - b.inst.index;
      return a.i - b.i;
    })
    .map(({ inst }) => inst);

  return ordered.map((inst) => ({
    instance: inst,
    gridArea: null, // Rails are flex columns; bottom is a 4-col grid. No explicit gridArea today.
  }));
}

export function addWidget(layout, type, options = {}) {
  const rail = options.rail || 'left';
  const size = options.size || 'medium';
  const id = options.id || `${type}-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e4).toString(36)}`;
  const railSiblings = (layout || []).filter((i) => (i.rail || 'left') === rail);
  const index = railSiblings.length;
  const next = [...(layout || []), { id, type, size, rail, index }];
  return next;
}

export function removeWidget(layout, instanceId) {
  return (layout || []).filter((i) => i.id !== instanceId).map((inst, idx) => ({ ...inst, index: idx }));
}

export function moveWidget(layout, instanceId, target) {
  const list = (layout || []).map((i) => ({ ...i }));
  const item = list.find((i) => i.id === instanceId);
  if (!item) return layout;
  const newRail = target.rail || item.rail || 'left';
  const newIndex = typeof target.index === 'number' ? target.index : 0;
  // Strip from old rail
  const others = list.filter((i) => i.id !== instanceId);
  const sameRail = others.filter((i) => (i.rail || 'left') === newRail)
    .sort((a, b) => (a.index ?? 0) - (b.index ?? 0));
  const otherRails = others.filter((i) => (i.rail || 'left') !== newRail);
  const reflowed = [];
  let inserted = false;
  for (let i = 0; i < sameRail.length; i++) {
    if (i === newIndex) { reflowed.push({ ...item, rail: newRail, index: reflowed.length }); inserted = true; }
    reflowed.push({ ...sameRail[i], index: reflowed.length });
  }
  if (!inserted) reflowed.push({ ...item, rail: newRail, index: reflowed.length });
  return [...otherRails, ...reflowed];
}

export const __RAILS__ = RAILS;
