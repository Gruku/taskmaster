// User intent: the Dashboard's quick-add note — a blank paper slot that never grabs focus on its own, and keeps what
// was typed until the server has actually saved it.
import { h } from '../../util/h.js';

// Enter commits, Shift+Enter inserts a newline. onCreate(text) resolves true when the note was saved.
export function createComposer({ onCreate }) {
  const ta = h('textarea', {
    class: 'dk-composer__input',
    placeholder: 'Write a note…',
    rows: 1,
    'aria-label': 'Write a note',
  });
  const root = h('div', { class: 'dk-note dk-composer' }, ta);

  let busy = false;
  ta.addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
    e.preventDefault();
    const sent = ta.value;
    const text = sent.trim();
    if (!text || busy) return;
    busy = true;
    try {
      const ok = await onCreate?.(text);
      // Cleared only when saved, and only if nothing new was typed while it saved.
      if (ok === true && ta.value === sent) {
        ta.value = '';
        ta.rows = 1;
      }
    } finally {
      busy = false;
    }
  });
  // Grow with content (no scrollbars inside a "paper" note).
  ta.addEventListener('input', () => {
    ta.rows = Math.min(8, Math.max(1, ta.value.split('\n').length));
  });

  return { root, focus: () => ta.focus() };
}
