/**
 * component-graph-layout.js
 *
 * Pure-data layout engine for the C2 live component diagram.
 * No DOM, no SVG element creation — computes coordinates and path strings only.
 *
 * Exported: computeComponentLayout(input) → { nodes, edges, width, height }
 */

const NODE_W = 132;
const NODE_H = 56;
const COL_GAP = 56;   // horizontal gap between ranks
const ROW_GAP = 20;   // vertical gap between stacked same-rank nodes
const PAD = 16;        // canvas padding

/**
 * Compute a layered (longest-path-rank) layout for a component DAG.
 *
 * @param {object} input
 *   components: { <key>: { title: string, after: string[] } }
 *   rollup:     { <key>: { status?, total?, done? } }  — optional per key
 *   width?:     number  (default 760)
 *   height?:    number  (default 300)
 * @returns {{ nodes, edges, width, height }}
 */
export function computeComponentLayout(input) {
  const { components = {}, rollup = {} } = input;

  const keys = Object.keys(components);

  // ── Append synthetic _unassigned node if rollup signals one ──────────────
  const synthKeys = [...keys];
  const unassignedRollup = rollup._unassigned;
  const hasUnassigned = unassignedRollup && (unassignedRollup.total ?? 0) > 0;
  if (hasUnassigned) synthKeys.push('_unassigned');

  if (synthKeys.length === 0) {
    return { nodes: [], edges: [], width: PAD * 2, height: PAD * 2 };
  }

  // ── Build a safe `after` map (skip unknown refs) ──────────────────────────
  const compSet = new Set(synthKeys);

  function getAfter(key) {
    if (key === '_unassigned') return [];
    const comp = components[key];
    if (!comp) return [];
    return (comp.after || []).filter(dep => compSet.has(dep) && dep !== '_unassigned');
  }

  // ── Rank by longest dependency path (memoized DFS with cycle guard) ───────
  const rankCache = new Map();
  const inProgress = new Set();

  function rank(key) {
    if (rankCache.has(key)) return rankCache.get(key);
    if (inProgress.has(key)) return 0; // cycle — break with 0
    inProgress.add(key);
    const deps = getAfter(key);
    const r = deps.length === 0
      ? 0
      : 1 + Math.max(...deps.map(dep => rank(dep)));
    inProgress.delete(key);
    rankCache.set(key, r);
    return r;
  }

  for (const key of synthKeys) rank(key);

  // Give _unassigned its own trailing rank (max + 1) so it sits isolated
  if (hasUnassigned) {
    const maxRank = Math.max(...[...rankCache.values()].filter((_, i) => synthKeys[i] !== '_unassigned'));
    rankCache.set('_unassigned', isFinite(maxRank) ? maxRank + 1 : 0);
  }

  // ── Group nodes by rank ───────────────────────────────────────────────────
  const byRank = new Map(); // rank → [key, ...]
  for (const key of synthKeys) {
    const r = rankCache.get(key);
    if (!byRank.has(r)) byRank.set(r, []);
    byRank.get(r).push(key);
  }

  // ── Compute canvas dimensions ─────────────────────────────────────────────
  const numRanks = byRank.size;
  const maxNodesInRank = Math.max(...[...byRank.values()].map(g => g.length));

  const computedWidth  = PAD * 2 + numRanks * NODE_W + Math.max(0, numRanks - 1) * COL_GAP;
  const computedHeight = PAD * 2 + maxNodesInRank * NODE_H + Math.max(0, maxNodesInRank - 1) * ROW_GAP;
  const canvasHeight   = Math.max(input.height ?? 300, computedHeight);
  const canvasWidth    = Math.max(input.width  ?? 760, computedWidth);

  // ── Position nodes ────────────────────────────────────────────────────────
  const nodeMap = new Map(); // key → node object

  const sortedRanks = [...byRank.keys()].sort((a, b) => a - b);

  for (const r of sortedRanks) {
    const group = byRank.get(r);
    const x = PAD + r * (NODE_W + COL_GAP);

    // vertically center the column within canvas
    const colH = group.length * NODE_H + Math.max(0, group.length - 1) * ROW_GAP;
    const startY = PAD + (canvasHeight - PAD * 2 - colH) / 2;

    group.forEach((key, idx) => {
      const y = startY + idx * (NODE_H + ROW_GAP);
      const isUnassigned = key === '_unassigned';
      const r_val = rankCache.get(key);
      const ro = rollup[key] || {};

      nodeMap.set(key, {
        id:         key,
        x,
        y,
        w:          NODE_W,
        h:          NODE_H,
        rank:       r_val,
        row:        idx,
        title:      isUnassigned ? '_unassigned' : (components[key]?.title ?? key),
        status:     ro.status  ?? 'todo',
        count:      ro.done    ?? 0,
        total:      ro.total   ?? 0,
        unassigned: isUnassigned,
        isolated:   isUnassigned,
      });
    });
  }

  // ── Build edges ───────────────────────────────────────────────────────────
  const edges = [];

  for (const key of synthKeys) {
    if (key === '_unassigned') continue;
    for (const dep of getAfter(key)) {
      const from = nodeMap.get(dep);
      const to   = nodeMap.get(key);
      if (!from || !to) continue;

      // Cubic-bezier from right edge of `from` to left edge of `to`
      // Matches dependency-graph.js makeEdge formula (horizontal pull at 60%)
      const fx = from.x + from.w;
      const fy = from.y + from.h / 2;
      const tx = to.x;
      const ty = to.y + to.h / 2;
      const dx = (tx - fx) * 0.6;
      const path = `M ${fx.toFixed(1)} ${fy.toFixed(1)} C ${(fx + dx).toFixed(1)} ${fy.toFixed(1)}, ${(tx - dx).toFixed(1)} ${ty.toFixed(1)}, ${tx.toFixed(1)} ${ty.toFixed(1)}`;

      edges.push({ from: dep, to: key, path });
    }
  }

  return {
    nodes:  [...nodeMap.values()],
    edges,
    width:  canvasWidth,
    height: canvasHeight,
  };
}
