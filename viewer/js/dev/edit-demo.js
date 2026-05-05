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
import './../components/edit/fields/text-field-demo.js';
import './../components/edit/fields/md-field-demo.js';
import './../components/edit/fields/enum-select-demo.js';
import './../components/edit/fields/number-field-demo.js';
import './../components/edit/fields/date-field-demo.js';
import './../components/edit/fields/chip-input-demo.js';
import './../components/edit/fields/relation-picker-demo.js';
