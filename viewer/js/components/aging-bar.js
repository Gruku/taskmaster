// Severity-tiered aging bar. Two responsibilities:
//   1) Pure-data: computeAgingTier(issue, cfg, now?) → { percent, tier }
//   2) DOM: agingBar(issue, cfg) returns a styled <div class="aging-bar">

export function computeAgingTier(issue, cfg, now = new Date()) {
  const label = issue.severity_label || 'Medium';
  const baseDays = Number(cfg[label] ?? 60);
  const start = issue.discovered || issue.created;
  if (!start) return null;
  const started = new Date(start);
  const ageDays = (now.getTime() - started.getTime()) / 86_400_000;
  let percent = baseDays > 0 ? (ageDays / baseDays) * 100 : 0;
  percent = Math.max(0, Math.min(percent, 200));
  let tier;
  if (percent < 25) tier = 'Fresh';
  else if (percent < 60) tier = 'Aging';
  else tier = 'Stale';
  return { percent, tier };
}

export function agingBar(issue, cfg) {
  const result = computeAgingTier(issue, cfg);
  if (!result) return null;
  const { percent, tier } = result;
  const wrap = document.createElement('div');
  wrap.className = `aging-bar aging-bar--${tier.toLowerCase()}`;
  wrap.setAttribute('data-tier', tier);

  const track = document.createElement('div');
  track.className = 'aging-bar__track';
  const fill = document.createElement('div');
  fill.className = 'aging-bar__fill';
  fill.style.width = `${Math.min(percent, 100)}%`;
  track.appendChild(fill);

  const chip = document.createElement('span');
  chip.className = `aging-bar__chip aging-bar__chip--${tier.toLowerCase()}`;
  chip.textContent = tier;

  wrap.appendChild(track);
  wrap.appendChild(chip);
  return wrap;
}

export default agingBar;
