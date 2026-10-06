// User intent: flag an issue that is still open and in the stale range for its severity (the aging tier Stale) with a
// short "stale Nd" tag, so a forgotten issue stands out in a list — never one resolved, fresh, undated or future-dated.
import { marker, SEVERITY, severityKey } from './status.js';
import { computeAgingTier } from './aging-bar.js';
import { issueDiscovered } from '../util/issue-fields.js';

const DAY_MS = 86_400_000;
const UNRESOLVED = new Set(['open', 'investigating']);

export function staleDays(issue, now = Date.now()) {
  const start = issueDiscovered(issue);
  if (!start) return null;
  const started = Date.parse(start);
  if (Number.isNaN(started)) return null;
  return Math.floor((now - started) / DAY_MS);
}

export function staleTag(issue, agingCfg = {}, now = Date.now()) {
  if (!issue || !UNRESOLVED.has(issue.status)) return null;
  const days = staleDays(issue, now);
  // A discovery date in the future (clock skew, a typo) would read "stale -3d"; it is no count at all.
  if (days === null || days < 0) return null;
  // The server's tier wins; without one, the viewer ages the issue by its severity's window (Medium when unknown).
  const tier = typeof issue.aging?.tier === 'string'
    ? issue.aging.tier
    : computeAgingTier({ ...issue, severity_label: SEVERITY[severityKey(issue.severity)]?.label ?? 'Medium' },
      agingCfg ?? {}, new Date(now))?.tier;
  if (tier !== 'Stale') return null;
  const el = marker({ label: `stale ${days}d`, shape: '▲', tone: 'warning' });
  el.classList.add('stale-tag');
  el.title = `Open ${days} days — in the stale range for its severity`;
  return el;
}
