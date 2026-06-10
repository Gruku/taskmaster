import { h } from '../../util/h.js';

// Quick-add composer styled as a blank paper note. Enter commits,
// Shift+Enter inserts a newline. Stays focused after submit for rapid capture.
export function createComposer({ onCreate }) {
  const ta = h('textarea', {
    class: 'dk-composer__input',
    placeholder: 'Write a note…',
    rows: 1,
    'aria-label': 'Write a note',
  });
  const root = h('div', { class: 'dk-note dk-composer' }, ta);

  ta.addEventListener('keydown', async (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      const text = ta.value.trim();
      if (!text) return;
      ta.value = '';
      ta.rows = 1;
      await onCreate?.(text);
      ta.focus();
    }
  });
  // Grow with content (no scrollbars inside a "paper" note).
  ta.addEventListener('input', () => {
    ta.rows = Math.min(8, Math.max(1, ta.value.split('\n').length));
  });

  return { root, focus: () => ta.focus() };
}
