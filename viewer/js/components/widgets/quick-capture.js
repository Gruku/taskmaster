import { registerWidget } from '../widget-catalog.js';

export const meta = {
  id: 'quick-capture',
  label: 'Quick capture',
  sizes: ['small', 'medium'],
  defaultSize: 'medium',
  defaultRail: 'right',
};

export async function mount(el, { api }) {
  el.replaceChildren();
  const form = document.createElement('form');
  form.style.cssText = 'display:flex;flex-direction:column;gap:6px;';
  const ta = document.createElement('textarea');
  ta.rows = 3;
  ta.placeholder = 'Capture a thought, todo, or note…';
  ta.style.cssText = 'background:var(--bg-deep);color:var(--ink-1);border:1px solid var(--line-1);border-radius:6px;padding:6px;font-family:var(--font-sans);font-size:12px;resize:vertical;';
  const btn = document.createElement('button');
  btn.type = 'submit';
  btn.textContent = '＋ Capture';
  btn.style.cssText = 'align-self:flex-end;background:transparent;color:var(--ink-1);border:1px solid var(--line-1);border-radius:6px;padding:4px 10px;cursor:pointer;font-size:12px;';
  const status = document.createElement('div');
  status.style.cssText = 'font-size:10px;color:var(--ink-3);min-height:14px;';
  form.append(ta, btn, status);
  el.appendChild(form);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!ta.value.trim()) return;
    status.textContent = 'Saving…';
    try {
      await api.quickCapture(ta.value.trim());
      ta.value = '';
      status.textContent = 'Captured.';
      setTimeout(() => { status.textContent = ''; }, 2000);
    } catch (err) {
      status.textContent = `Error: ${err.message || err}`;
    }
  });
  return () => {};
}

registerWidget({ meta, mount });
