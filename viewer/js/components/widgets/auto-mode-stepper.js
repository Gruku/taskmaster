import { registerWidget } from '../widget-catalog.js';

export const meta = {
  id: 'auto-mode-stepper',
  label: 'Auto Mode · stepper',
  sizes: ['medium', 'wide'],
  defaultSize: 'medium',
  defaultRail: 'right',
};

export async function mount(el) {
  el.replaceChildren();
  const note = document.createElement('div');
  note.style.cssText = 'font-size:12px;color:var(--ink-3);font-family:var(--font-sans);';
  note.textContent = '(implemented in Plan 6 — Auto Mode page)';
  el.appendChild(note);
  return () => {};
}

registerWidget({ meta, mount });
