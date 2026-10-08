// Markdown rendering — wraps the global `marked` library with an allow-list sanitiser. Task text is written by
// people and by agents, and callers assign the result with innerHTML, so the sanitiser is the security boundary:
// what it returns must be inert — no script, no event handler, no remote load, no hostile link.
//
// `renderMarkdown(src)` returns an HTML string ready to inject via .innerHTML.
// `mountMarkdown(element, src)` is a convenience that does the assignment.

const HTML_NS = 'http://www.w3.org/1999/xhtml';

// Elements that may stay. Anything else is unwrapped (its children are kept and sanitised in turn)…
const ALLOWED_TAGS = new Set([
  'a', 'abbr', 'b', 'blockquote', 'br', 'code', 'del', 'em', 'h1', 'h2', 'h3', 'h4',
  'h5', 'h6', 'hr', 'i', 'li', 'ol', 'p', 'pre', 's', 'span', 'strong',
  'sub', 'sup', 'table', 'tbody', 'td', 'th', 'thead', 'tr', 'ul',
]);
// …except these, which go with everything inside them: their content is code, a remote load, a form control,
// or text the parser reads differently the second time (the caller's innerHTML is a second parse).
const DROPPED_TAGS = new Set([
  'script', 'style', 'iframe', 'frame', 'frameset', 'object', 'embed', 'applet', 'img', 'picture', 'source',
  'video', 'audio', 'track', 'canvas', 'svg', 'math', 'template', 'noscript', 'noembed', 'noframes', 'xmp',
  'plaintext', 'listing', 'textarea', 'title', 'select', 'option', 'input', 'button', 'link', 'meta', 'base', 'head',
]);
// Attributes that may stay, per element; each value is checked by the function it maps to.
const keep = () => true;
const ALLOWED_ATTRS = {
  a: { href: isSafeUrl, title: keep },
  abbr: { title: keep },
  code: { class: (v) => /^language-[\w+#.-]+$/.test(v) },
  td: { align: isAlign },
  th: { align: isAlign },
  ol: { start: (v) => /^-?\d+$/.test(v) },
};
const SAFE_SCHEMES = new Set(['http', 'https', 'mailto']);

export function renderMarkdown(src) {
  if (src == null || src === '') return '';
  const text = typeof src === 'string' ? src : String(src);
  // Without `marked`, or when rendering fails, the text is shown escaped rather than not at all.
  if (typeof window === 'undefined' || !window.marked) return `<pre class="md-fallback">${escapeHtml(text)}</pre>`;
  try {
    const clean = sanitise(window.marked.parse(text, { breaks: true, gfm: true }));
    // The caller parses this string again. If a second pass would still change it, the two parses disagree
    // about the markup, and that disagreement is where an injection hides — show the source as text instead.
    if (sanitise(clean) === clean) return markTasks(clean);
  } catch (e) {
    console.error('markdown render failed', e);
  }
  return `<pre class="md-fallback">${escapeHtml(text)}</pre>`;
}

export function mountMarkdown(el, src) {
  el.innerHTML = renderMarkdown(src);
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

function isAlign(value) {
  return /^(left|right|center)$/i.test(value);
}

// The URL a browser would see: it ignores leading control characters and spaces, and tabs and newlines
// anywhere, so they are removed before looking. No scheme means a relative or fragment reference.
function visible(url) {
  return url.replace(/[\u0000-\u0020\u007f-\u009f\u00a0\u1680\u2000-\u200f\u2028-\u202f\u205f-\u2064\u3000\ufeff]/g, '');
}

function schemeOf(url) {
  const seen = visible(url);
  const m = /^([a-z][a-z0-9+.-]*):/i.exec(seen);
  if (m) return m[1].toLowerCase();
  // A colon before any path, query or fragment separator that did not parse as a scheme is not trusted either.
  return /^[^/?#]*:/.test(seen) ? 'invalid' : null;
}

function isSafeUrl(url) {
  const scheme = schemeOf(url);
  return scheme === null || SAFE_SCHEMES.has(scheme);
}

// http(s) and protocol-relative links leave the viewer.
function isExternal(url) {
  const scheme = schemeOf(url);
  return scheme === 'http' || scheme === 'https' || (scheme === null && /^[/\\]{2}/.test(visible(url)));
}

function sanitise(html) {
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  sanitiseChildren(tpl.content);
  return tpl.innerHTML;
}

function sanitiseChildren(parent) {
  for (const node of [...parent.childNodes]) {
    if (node.nodeType === 3) continue;                       // text
    if (node.nodeType !== 1) { node.remove(); continue; }    // comments, processing instructions, CDATA
    const tag = node.localName;
    // A task-list checkbox is kept, bare, until the markup has been checked; markTasks then swaps it for a mark.
    if (isCheckbox(node)) { bareCheckbox(node); continue; }
    if (node.namespaceURI !== HTML_NS || DROPPED_TAGS.has(tag)) { node.remove(); continue; }
    sanitiseChildren(node);
    if (!ALLOWED_TAGS.has(tag)) { node.replaceWith(...node.childNodes); continue; }
    const allowed = ALLOWED_ATTRS[tag] ?? {};
    for (const attr of [...node.attributes]) {
      if (!Object.hasOwn(allowed, attr.name) || !allowed[attr.name](attr.value)) node.removeAttributeNode(attr);
    }
    if (tag === 'a' && node.hasAttribute('href') && isExternal(node.getAttribute('href'))) {
      node.setAttribute('rel', 'noopener noreferrer');
      node.setAttribute('target', '_blank');
    }
  }
}

function isCheckbox(node) {
  return node.namespaceURI === HTML_NS && node.localName === 'input' && (node.getAttribute('type') ?? '').toLowerCase() === 'checkbox';
}

function bareCheckbox(node) {
  const checked = node.hasAttribute('checked');
  for (const attr of [...node.attributes]) node.removeAttributeNode(attr);
  node.setAttribute('type', 'checkbox');
  node.setAttribute('disabled', '');
  if (checked) node.setAttribute('checked', '');
}

// Plans are checkbox step lists. A form control cannot stay, so each checkbox becomes an inert mark that still
// says whether the step is done — drawn by the stylesheet, and spoken through its hidden text. The class is
// added here, after every authored class has been stripped, so task text cannot fake a mark.
function markTasks(html) {
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  // An ordered list's marker hangs left of its text: the indent grows with the digits of its last number.
  for (const ol of tpl.content.querySelectorAll('ol')) {
    const start = Number.parseInt(ol.getAttribute('start') ?? '1', 10);
    const last = Math.max(Math.abs(Number.isFinite(start) ? start : 1) + ol.children.length - 1, 1);
    const digits = String(last).length;
    if (digits > 1) ol.setAttribute('data-digits', String(Math.min(digits, 6)));
  }
  for (const box of tpl.content.querySelectorAll('input')) {
    const done = box.hasAttribute('checked');
    const mark = document.createElement('span');
    mark.className = `md-task md-task--${done ? 'done' : 'open'}`;
    const label = document.createElement('span');
    label.className = 'md-task__label';
    label.textContent = done ? 'done: ' : 'to do: ';
    mark.appendChild(label);
    box.replaceWith(mark);
  }
  return tpl.innerHTML;
}
