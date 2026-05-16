// viewer/js/util/h.js
// Shared DOM factory. Mirrors the per-component copies in right-rail.js,
// task-detail-document.js, task-detail-graph.js — those will migrate to this.
//
// Children accept three shapes: a single value (string/element/null/false),
// an array of such values, or trailing varargs. h('div', {}, c1, c2, c3) and
// h('div', {}, [c1, c2, c3]) and h('div', {}, c1) are all valid.
export function h(tag, attrs = {}, ...rest) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') el.className = v;
    else if (k === 'on') for (const [evt, fn] of Object.entries(v)) el.addEventListener(evt, fn);
    else if (k === 'html') el.innerHTML = v;
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  const children = rest.length === 1 ? [].concat(rest[0]) : rest;
  for (const c of children) {
    if (c == null || c === false) continue;
    el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return el;
}
