// Passive-signal 5-dot meter for anchor-match intensity over the last 7 days.
// Ambient only — does NOT participate in shelf placement.
// Input: matches7d (number) → dots filled from left to right.

import { pluralize } from '../util/pluralize.js';

const DOTS = 5;

function _dotsFilledFor(count) {
  if (count <= 0) return 0;
  if (count < 5) return 1;
  if (count < 15) return 2;
  if (count < 30) return 3;
  if (count < 60) return 4;
  return 5;
}

export function dotMeter(matches7d) {
  const filled = _dotsFilledFor(matches7d);
  const wrap = document.createElement('span');
  wrap.className = 'dot-meter';
  wrap.setAttribute('aria-label', `${matches7d} anchor ${pluralize(matches7d, 'match', 'matches')} in last 7 days`);
  for (let i = 0; i < DOTS; i++) {
    const d = document.createElement('span');
    d.className = `dot-meter__dot ${i < filled ? 'is-on' : 'is-off'}`;
    wrap.appendChild(d);
  }
  const cap = document.createElement('span');
  cap.className = 'dot-meter__caption';
  cap.textContent = `${matches7d} ${pluralize(matches7d, 'match', 'matches')} · 7d`;
  wrap.appendChild(cap);
  return wrap;
}

export { _dotsFilledFor };
export default dotMeter;
