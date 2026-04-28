// Markdown rendering — wraps the global `marked` library with a small allowlist
// sanitiser so user-authored markdown can't smuggle script/style/iframe tags.
//
// `renderMarkdown(src)` returns an HTML string ready to inject via .innerHTML.
// `mountMarkdown(element, src)` is a convenience that does the assignment.

const ALLOWED_TAGS = new Set([
  'a', 'abbr', 'b', 'blockquote', 'br', 'code', 'em', 'h1', 'h2', 'h3', 'h4',
  'h5', 'h6', 'hr', 'i', 'img', 'li', 'ol', 'p', 'pre', 'span', 'strong',
  'sub', 'sup', 'table', 'tbody', 'td', 'th', 'thead', 'tr', 'ul',
]);
const ALLOWED_ATTRS = new Set(['href', 'title', 'src', 'alt', 'class']);

export function renderMarkdown(src) {
  if (!src) return '';
  if (typeof window === 'undefined' || !window.marked) {
    // Fallback for environments without `marked` loaded — escape and wrap.
    return `<pre class="md-fallback">${escapeHtml(src)}</pre>`;
  }
  const html = window.marked.parse(src, { breaks: true, gfm: true });
  return sanitise(html);
}

export function mountMarkdown(el, src) {
  el.innerHTML = renderMarkdown(src);
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

function sanitise(html) {
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  const walker = document.createTreeWalker(tpl.content, NodeFilter.SHOW_ELEMENT);
  const toRemove = [];
  let node = walker.nextNode();
  while (node) {
    const tag = node.tagName.toLowerCase();
    if (!ALLOWED_TAGS.has(tag)) {
      toRemove.push(node);
    } else {
      for (const attr of [...node.attributes]) {
        if (!ALLOWED_ATTRS.has(attr.name)) node.removeAttribute(attr.name);
        if (attr.name === 'href' && /^javascript:/i.test(attr.value)) {
          node.removeAttribute(attr.name);
        }
      }
    }
    node = walker.nextNode();
  }
  for (const n of toRemove) {
    while (n.firstChild) n.parentNode.insertBefore(n.firstChild, n);
    n.remove();
  }
  return tpl.innerHTML;
}
