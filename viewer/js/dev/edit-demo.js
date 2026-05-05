// viewer/js/dev/edit-demo.js
// Dev-only page — wires up each field renderer with sample data.
// Components register themselves into the demo via registerDemo(name, fn).
import { h } from '../util/h.js';

const sections = [];
export function registerDemo(name, mountFn) {
  sections.push({ name, mountFn });
}

const mount = document.getElementById('demo-mount');
for (const { name, mountFn } of sections) {
  const sec = h('section', {}, [h('h2', {}, name)]);
  mount.appendChild(sec);
  mountFn(sec);
}
// Subsequent tasks add `import './fields/text-field-demo.js'` etc. above.
