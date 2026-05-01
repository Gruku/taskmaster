/**
 * Render a single horizontal budget bar.
 * @param {object} opts
 * @param {string} opts.label
 * @param {number} opts.used
 * @param {number} opts.limit
 * @param {number} opts.pct        Pre-computed pct from server.
 * @param {string} opts.tier       'ok' | 'warn' | 'crit'
 * @param {string} opts.format     'num' | 'duration' | 'usd' | 'pct'
 * @returns {HTMLElement}
 */
export function buildBudgetMeter({ label, used, limit, pct, tier, format = 'num' }) {
  const el = document.createElement('div');
  el.className = `bmeter bmeter--${tier}`;
  el.innerHTML = `
    <div class="bmeter-row">
      <span class="bmeter-label">${label}</span>
      <span class="bmeter-vals">${formatVal(used, format)}<span class="bmeter-sep"> / </span>${formatVal(limit, format)}</span>
    </div>
    <div class="bmeter-track">
      <div class="bmeter-fill" style="width: ${(pct * 100).toFixed(1)}%"></div>
    </div>
  `;
  return el;
}

function formatVal(v, fmt) {
  switch (fmt) {
    case 'duration':
      if (v >= 3600) return `${(v / 3600).toFixed(1)}h`;
      if (v >= 60) return `${Math.floor(v / 60)}m`;
      return `${v}s`;
    case 'usd': return `$${Number(v).toFixed(2)}`;
    case 'pct': return `${(v * 100).toFixed(0)}%`;
    case 'num':
    default:
      if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
      return String(v);
  }
}
