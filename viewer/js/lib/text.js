// User intent: text cut short with an ellipsis must never lose its words — whatever the cut, the full text is in the
// element's title, so a hover (or an assistive tool) always has it.

const CLAMP = { 1: '', 2: 'truncate--2', 3: 'truncate--3' };

export function truncate(text, { lines = 1, tag = 'span', className = '' } = {}) {
  if (typeof lines !== 'number' || !Object.hasOwn(CLAMP, lines)) throw new RangeError(`truncate: lines must be 1, 2 or 3 (got ${lines})`);
  const full = String(text ?? '');
  const el = document.createElement(tag);
  el.className = ['truncate', CLAMP[lines], className].filter(Boolean).join(' ');
  el.textContent = full;
  el.title = full;
  return el;
}
