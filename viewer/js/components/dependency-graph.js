// Pure-data graph layout helper for Task Detail Variant B.
// Inputs are plain objects; outputs are plain objects suitable for SVG rendering.
//
// Input shape:
//   {
//     center:    { id, title, status, priority?, estimate?, time_in_status?, progress? },
//     upstream:  [ TaskRef, ... ]   // depths 1..2 (L-1, L-2)
//     downstream:[ TaskRef, ... ]   // depths 1..2 (L+1, L+2)
//     width:     number             // canvas width in px (default 800)
//     height:    number             // canvas height in px (default 320)
//   }
//
//   TaskRef = { id, title, status, depth, priority?, estimate?, time_in_status?, row? }
//   `depth` is positive for both upstream and downstream; the function negates
//   for upstream when assigning columns.
//
// Output shape:
//   {
//     columns: [{depth, x, label}, ...]                       // 5 entries, depths -2..+2
//     nodes:   [{id, x, y, w, h, depth, faded, isCenter, ...meta}, ...]
//     edges:   [{from, to, path, sameRow}]                    // SVG path strings
//   }

const DEFAULT_W = 800;
const DEFAULT_H = 320;
const NODE_W = 100;
const NODE_H = 60;
const CENTER_W = 120;
const CENTER_H = 80;
const ROW_GAP = 14;

export function computeGraphLayout(input) {
  const width = input?.width ?? DEFAULT_W;
  const height = input?.height ?? DEFAULT_H;
  const centerY = height / 2;
  const colSpacing = width / 5;

  const columns = [-2, -1, 0, 1, 2].map((depth) => ({
    depth,
    x: colSpacing * (depth + 2) + colSpacing / 2,
    label: depth === 0 ? 'L0' : `L${depth >= 0 ? '+' : ''}${depth}`,
  }));

  const nodes = [];
  const edges = [];

  if (!input || !input.center) {
    return { columns, nodes, edges };
  }

  // Center node
  const centerCol = columns.find((c) => c.depth === 0);
  nodes.push({
    id: input.center.id,
    x: centerCol.x - CENTER_W / 2,
    y: centerY - CENTER_H / 2,
    w: CENTER_W,
    h: CENTER_H,
    depth: 0,
    column: 0,
    faded: false,
    isCenter: true,
    title: input.center.title || '',
    status: input.center.status || '',
    priority: input.center.priority || null,
    estimate: input.center.estimate || null,
    time_in_status: input.center.time_in_status || null,
    progress: input.center.progress ?? null,
    step: input.center.step ?? null,
  });

  // Upstream and downstream lanes — dedupe ids that appear on both sides
  // (upstream wins) and drop any ref that collides with the center.
  const upstreamIds = new Set((input.upstream || []).map((n) => n.id));
  const downstreamFiltered = (input.downstream || []).filter(
    (n) => !upstreamIds.has(n.id) && n.id !== input.center.id
  );
  const upstreamFiltered = (input.upstream || []).filter(
    (n) => n.id !== input.center.id
  );
  placeSide(upstreamFiltered, -1, nodes, edges, columns, centerY, input.center.id);
  placeSide(downstreamFiltered, +1, nodes, edges, columns, centerY, input.center.id);

  return { columns, nodes, edges };
}

function placeSide(side, sign, nodes, edges, columns, centerY, centerId) {
  // Group by depth (1 and 2) — the caller passes depth as positive integers.
  const byDepth = new Map();
  for (const n of side) {
    const d = Math.max(1, Math.min(2, n.depth || 1));
    if (!byDepth.has(d)) byDepth.set(d, []);
    byDepth.get(d).push(n);
  }

  for (const [depth, items] of byDepth) {
    const col = columns.find((c) => c.depth === sign * depth);
    const totalH = items.length * NODE_H + (items.length - 1) * ROW_GAP;
    let y = centerY - totalH / 2;
    for (const item of items) {
      const node = {
        id: item.id,
        x: col.x - NODE_W / 2,
        y,
        w: NODE_W,
        h: NODE_H,
        depth: sign * depth,
        column: sign * depth,
        faded: depth === 2,
        isCenter: false,
        title: item.title || '',
        status: item.status || '',
        priority: item.priority || null,
        estimate: item.estimate || null,
        time_in_status: item.time_in_status || null,
      };
      nodes.push(node);
      y += NODE_H + ROW_GAP;

      // Edge: outermost (L-2/L+2) connects through middle (L-1/L+1), else
      // connects directly to the center.
      if (depth === 1) {
        const edge = makeEdge(node, findCenterById(nodes, centerId), sign);
        edges.push(edge);
      } else {
        // Connect L+/-2 to its sibling at L+/-1 if present, else to center.
        const mid = findNodeAtDepth(nodes, sign * 1);
        const target = mid || findCenterById(nodes, centerId);
        edges.push(makeEdge(node, target, sign));
      }
    }
  }
}

function findCenterById(nodes, id) {
  return nodes.find((n) => n.isCenter && n.id === id);
}
function findNodeAtDepth(nodes, depth) {
  return nodes.find((n) => n.depth === depth);
}

function makeEdge(from, to, sign) {
  // Upstream edges flow left→center (from is to the LEFT of to), downstream
  // edges flow center→right (from is to the RIGHT of to). Either way we draw
  // from `from` toward `to` with horizontal pull at 60% on each side.
  const fx = from.x + (sign < 0 ? from.w : 0);
  const fy = from.y + from.h / 2;
  const tx = to.x + (sign < 0 ? 0 : to.w);
  const ty = to.y + to.h / 2;
  const dx = (tx - fx) * 0.6;
  const cp1 = { x: fx + dx, y: fy };
  const cp2 = { x: tx - dx, y: ty };
  const path = `M ${fx.toFixed(1)} ${fy.toFixed(1)} C ${cp1.x.toFixed(1)} ${cp1.y.toFixed(1)}, ${cp2.x.toFixed(1)} ${cp2.y.toFixed(1)}, ${tx.toFixed(1)} ${ty.toFixed(1)}`;
  return {
    from: from.id,
    to: to.id,
    path,
    sameRow: Math.abs(fy - ty) < 0.5,
  };
}
