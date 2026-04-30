// Active-signal sparkline. Renders a small inline SVG line summarizing
// reinforce_events over the last N days, plus the lifetime count and
// the relative "last fired" timestamp.

const DEFAULT_DAYS = 30;
const W = 64;
const H = 16;

function _bucket(events, days, now) {
  const buckets = new Array(days).fill(0);
  for (const e of events) {
    const t = new Date(e.at).getTime();
    const ageDays = Math.floor((now.getTime() - t) / 86_400_000);
    if (ageDays < 0 || ageDays >= days) continue;
    buckets[days - 1 - ageDays] += 1;
  }
  return buckets;
}

function _relTime(iso, now) {
  if (!iso) return '—';
  const ms = now.getTime() - new Date(iso).getTime();
  const d = Math.floor(ms / 86_400_000);
  if (d <= 0) {
    const h = Math.floor(ms / 3_600_000);
    return h <= 0 ? 'now' : `${h}h`;
  }
  return `${d}d`;
}

export function sparkline(lesson, { days = DEFAULT_DAYS, now = new Date() } = {}) {
  const events = lesson.reinforce_events || [];
  const buckets = _bucket(events, days, now);
  const max = Math.max(1, ...buckets);
  const stepX = W / Math.max(1, days - 1);
  const points = buckets.map((v, i) => {
    const x = i * stepX;
    const y = H - (v / max) * (H - 2) - 1;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');

  const wrap = document.createElement('span');
  wrap.className = 'sparkline-pill';
  wrap.innerHTML = `
    <svg class="sparkline-svg" width="${W}" height="${H}" aria-hidden="true">
      <polyline points="${points}" fill="none" stroke="var(--gold, #d9b35a)" stroke-width="1.4" stroke-linejoin="round"/>
    </svg>
    <span class="sparkline-count">${(lesson.reinforce_count || 0)}×</span>
    <span class="sparkline-last">${_relTime(lesson.last_reinforced, now)}</span>
  `;
  return wrap;
}

export default sparkline;
