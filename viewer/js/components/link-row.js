// User intent: a row or card that opens something is a real link, and its own buttons sit beside that link, never
// inside it — the whole row is the link's hit area, it opens in a new tab like any link, and no control is nested.

const INTERACTIVE = 'a[href], button, input, select, textarea, [tabindex], [contenteditable]';

export function isInteractive(node) {
  if (!node || node.nodeType !== 1) return false;
  return node.matches(INTERACTIVE) || !!node.querySelector(INTERACTIVE);
}

function describe(el) {
  const cls = typeof el.className === 'string' && el.className.trim() ? ` class="${el.className.trim()}"` : '';
  return `<${el.localName}${cls}>`;
}

// Anything that takes focus or a click inside the link would be a control nested in a control; it belongs in `controls`.
function refuseInteractive(node, where) {
  if (!isInteractive(node)) return;
  const el = node.matches(INTERACTIVE) ? node : node.querySelector(INTERACTIVE);
  throw new TypeError(`linkRow: interactive ${describe(el)} in ${where} — pass it in controls`);
}

const asNode = (c) => (typeof c === 'string' ? document.createTextNode(c) : c);

export function linkRow({ href, name, content = [], controls = [], tag = 'div', className = '' }) {
  refuseInteractive(name, 'name');
  for (const c of content) refuseInteractive(c, 'content');

  const row = document.createElement(tag);
  row.className = ['link-row', className].filter(Boolean).join(' ');

  const link = document.createElement('a');
  link.className = 'link-row__link';
  link.setAttribute('href', href);
  link.append(asNode(name ?? ''));
  row.append(link);

  const body = document.createElement('div');
  body.className = 'link-row__content';
  for (const c of content) if (c != null && c !== false) body.append(asNode(c));
  row.append(body);

  const kept = controls.filter((c) => c != null && c !== false);
  if (kept.length) {
    const side = document.createElement('div');
    side.className = 'link-row__controls';
    for (const c of kept) side.append(asNode(c));
    row.append(side);
  }
  return row;
}
