import { registerWidget } from '../widget-catalog.js';

export const meta = {
  id: 'last-session',
  label: 'Last session',
  sizes: ['medium', 'wide'],
  defaultSize: 'medium',
  defaultRail: 'right',
};

export async function mount(el, { api }) {
  el.textContent = 'Loading…';
  let session = null;
  try { session = await api.getLastSession(); } catch (_) { session = null; }
  el.replaceChildren();
  if (!session) {
    const empty = document.createElement('div');
    empty.className = 'widget__empty';
    empty.textContent = 'No prior session yet.';
    el.appendChild(empty);
    return () => {};
  }
  const head = document.createElement('div');
  head.style.cssText = 'font-family:var(--font-mono);font-size:10px;color:var(--ink-3);letter-spacing:0.04em;';
  head.textContent = `${session.id || ''} · ${session.ended_at || session.started_at || ''}`;
  const quote = document.createElement('blockquote');
  quote.style.cssText = 'margin:8px 0;padding:6px 10px;border-left:1px solid var(--line-1);font-family:var(--font-serif);font-style:italic;color:var(--ink-1);font-size:13px;';
  quote.textContent = session.handover_quote || session.title || '—';
  el.append(head, quote);
  return () => {};
}

registerWidget({ meta, mount });
