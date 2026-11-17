import { h } from '../util/h.js';

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
  // Text nodes and CSSOM only: the label is data, and a strict style-src forbids a style attribute.
  const fill = h('div', { class: 'bmeter-fill' });
  fill.style.width = `${(pct * 100).toFixed(1)}%`;
  return h('div', { class: `bmeter bmeter--${tier}` }, [
    h('div', { class: 'bmeter-row' }, [
      h('span', { class: 'bmeter-label' }, String(label)),
      h('span', { class: 'bmeter-vals' }, [formatVal(used, format), h('span', { class: 'bmeter-sep' }, ' / '), formatVal(limit, format)]),
    ]),
    h('div', { class: 'bmeter-track' }, fill),
  ]);
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
