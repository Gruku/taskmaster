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

// The titles truncate() put on the name and the content, in order. The link's ::after covers them, so a hover never
// reaches their own title; the row has to carry it (on the link it would be read a second time, as its description).
function cutTitles(nodes) {
  const out = [];
  for (const n of nodes) {
    if (!n || n.nodeType !== 1) continue;
    for (const t of [n, ...n.querySelectorAll('[title]')]) if (t.title) out.push(t.title);
  }
  return out;
}

/**
 * A row (or card) that opens `href`: a real link holding `name`, then `content`, then `controls` beside the link.
 * The whole row is the link's hit area; controls are siblings stacked above it, so they keep their own clicks.
 * `name` is required (string or Node with text): the link's text is its accessible name.
 * `title` goes on the row, never the link, so the link is named once, by its text. Left out, the row takes the titles
 * of anything cut inside the name and content (each `[title]`, in order, one per line), since the hit area hides their
 * own: cut text keeps its words on hover. The controls carry an empty title, so they do not inherit the row's.
 * `name` or `content` that is or holds a control throws; controls go in `controls`.
 */
export function linkRow({ href, name, content = [], controls = [], tag = 'div', className = '', title }) {
  const text = typeof name === 'string' ? name : name?.nodeType ? name.textContent : '';
  if (!text.trim()) throw new TypeError('linkRow: name is required — the link\'s text is its accessible name');
  refuseInteractive(name, 'name');
  for (const c of content) refuseInteractive(c, 'content');

  const row = document.createElement(tag);
  row.className = ['link-row', className].filter(Boolean).join(' ');

  const link = document.createElement('a');
  link.className = 'link-row__link';
  link.setAttribute('href', href);
  link.append(asNode(name));
  const full = title ?? cutTitles([name, ...content]).join('\n');
  if (full) row.title = full;
  row.append(link);

  const body = document.createElement('div');
  body.className = 'link-row__content';
  for (const c of content) if (c != null && c !== false) body.append(asNode(c));
  row.append(body);

  const kept = controls.filter((c) => c != null && c !== false);
  if (kept.length) {
    const side = document.createElement('div');
    side.className = 'link-row__controls';
    side.title = '';
    for (const c of kept) side.append(asNode(c));
    row.append(side);
  }
  return row;
}
