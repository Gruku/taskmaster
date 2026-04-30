// Pure-data SVG layout for the Quest Spine.
// No DOM access. Inputs → outputs only. Renderer imports these.

export const AUTO_STAGES = ['PICK', 'IMPLEMENT', 'REVIEW', 'HANDOVER_STUB', 'COMPLETE'];

/**
 * Compute spine geometry.
 * @param {object} opts
 * @param {string} opts.cursorStage           One of AUTO_STAGES (or null when not running).
 * @param {string[]} opts.completed           Stages already done (subset of AUTO_STAGES).
 * @param {Array<{type:string,status:string}>} opts.subagents  Active subagents on the cursor stage.
 * @param {number} opts.width                 SVG width.
 * @param {number} opts.height                SVG height.
 * @param {number} opts.padding               Top/bottom padding inside the frame.
 * @returns {{nodes: Array, satellites: Array, connectors: Array}}
 */
export function computeSpineLayout(opts) {
  const { cursorStage, completed = [], subagents = [], width, height, padding } = opts;
  const cx = width / 2;
  const usableH = height - padding * 2;
  const step = AUTO_STAGES.length > 1 ? usableH / (AUTO_STAGES.length - 1) : 0;

  const completedSet = new Set(completed);

  const nodes = AUTO_STAGES.map((stage, i) => {
    let state;
    if (completedSet.has(stage)) state = 'done';
    else if (stage === cursorStage) state = 'active';
    else state = 'pending';
    return {
      stage,
      x: cx,
      y: padding + step * i,
      r: state === 'active' ? 18 : 10,
      state,
    };
  });

  const connectors = [];
  for (let i = 0; i < nodes.length - 1; i += 1) {
    const a = nodes[i];
    const b = nodes[i + 1];
    connectors.push({ x1: a.x, y1: a.y + a.r, x2: b.x, y2: b.y - b.r, fromState: a.state });
  }

  // Satellites: branch off the active node with horizontal in/out tangents.
  const active = nodes.find((n) => n.state === 'active');
  const satellites = [];
  if (active && subagents.length) {
    const offsetX = 90;
    const verticalSpan = 60;
    subagents.forEach((sa, idx) => {
      const dy = subagents.length === 1 ? 0
        : (idx - (subagents.length - 1) / 2) * (verticalSpan / Math.max(1, subagents.length - 1));
      const side = idx % 2 === 0 ? 1 : -1;  // alternate sides
      const sx = active.x + side * offsetX;
      const sy = active.y + dy;
      // Cubic bezier with HORIZONTAL in/out tangents:
      //   from (active.x ± active.r, active.y) tangent (±dx, 0)
      //   to (sx ∓ smallR, sy) tangent (∓dx, 0)
      const startX = active.x + side * active.r;
      const startY = active.y;
      const endR = 6;
      const endX = sx - side * endR;
      const endY = sy;
      const dx = Math.abs(endX - startX) * 0.55;
      satellites.push({
        type: sa.type,
        status: sa.status,
        node: { x: sx, y: sy, r: endR },
        bezier: {
          startX, startY, endX, endY,
          c1x: startX + side * dx, c1y: startY,
          c2x: endX - side * dx,   c2y: endY,
        },
      });
    });
  }

  return { nodes, satellites, connectors };
}
