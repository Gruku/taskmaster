// viewer/js/dev/edit-demo.js
// Dev-only page — wires up each field renderer with sample data.
// Components register themselves into the demo via registerDemo(name, fn).
import { h } from '../util/h.js';

const sections = [];
export function registerDemo(name, mountFn) {
  sections.push({ name, mountFn });
}

import './../components/edit/fields/text-field-demo.js';
import './../components/edit/fields/md-field-demo.js';
import './../components/edit/fields/enum-select-demo.js';
import './../components/edit/fields/number-field-demo.js';
import './../components/edit/fields/date-field-demo.js';
import './../components/edit/fields/chip-input-demo.js';
import './../components/edit/fields/relation-picker-demo.js';

// Defer render until after all demo-file registerDemo() calls have completed.
// The circular import (demo files ← edit-demo.js) means the for-loop must not
// run inline — queueMicrotask ensures all synchronous module init finishes first.
queueMicrotask(() => {
  const mount = document.getElementById('demo-mount');
  for (const { name, mountFn } of sections) {
    const sec = h('section', {}, [h('h2', {}, name)]);
    mount.appendChild(sec);
    mountFn(sec);
  }
});
