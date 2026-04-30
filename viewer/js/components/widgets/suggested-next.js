import { registerWidget } from '../widget-catalog.js';
import { renderFullCard } from '../card.js';

export const meta = {
  id: 'suggested-next',
  label: 'Suggested next',
  sizes: ['medium', 'wide'],
  defaultSize: 'medium',
  defaultRail: 'left',
};

export async function mount(el, { store }) {
  function pick(backlog) {
    const tasks = (backlog.tasks || []).filter(t => t.status === 'ready' || t.status === 'todo');
    // Highest priority, then smallest size (so it's actionable now).
    const order = { Critical: 0, High: 1, Medium: 2, Low: 3 };
    return tasks.sort((a, b) => (order[a.priority] ?? 9) - (order[b.priority] ?? 9))[0];
  }

  function render() {
    const backlog = (store.getBacklog && store.getBacklog()) || { tasks: [] };
    const t = pick(backlog);
    el.replaceChildren();
    if (!t) {
      const empty = document.createElement('div');
      empty.className = 'widget__empty';
      empty.textContent = 'Nothing queued.';
      el.appendChild(empty);
      return;
    }
    const card = renderFullCard(t, { backlog });
    el.appendChild(card);
    const reasons = document.createElement('div');
    reasons.className = 'widget__reasons';
    reasons.style.cssText = 'margin-top:8px;font-size:11px;color:var(--ink-3);';
    reasons.textContent = `Reason: ${t.priority || 'Medium'} priority · ${t.estimate || 'M'} size · status ${t.status}`;
    el.appendChild(reasons);
  }

  render();
  const unsub = store.subscribe ? store.subscribe('backlog', render) : () => {};
  return () => unsub();
}

registerWidget({ meta, mount });
