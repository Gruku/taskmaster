// User intent: an open decision is settled from the Dashboard with real buttons — each option, the recommended pick,
// Drop — and when the server refuses, the card says why in words and lets the person try again.
import { h } from '../../util/h.js';
import { describeWriteError } from '../edit/write-errors.js';

export function createDecisionCard({ item, decision, onResolve, onDrop }) {
  const root = h('div', { class: 'co-decision', 'data-item-id': decision.id });
  root.appendChild(h('div', { class: 'co-decision__rail' },
    h('span', { class: 'co-chip' }, 'Decision'),
    // The item's title only when it says something the heading below does not.
    h('span', { class: 'co-decision__id' }, item.title && item.title !== decision.title ? `${decision.id} · ${item.title}` : decision.id),
  ));
  root.appendChild(h('h3', { class: 'co-decision__title' }, decision.title));

  let errorEl = null;
  // Every button waits while one write runs; a refusal is said on the card and the buttons come back. Disabling the
  // pressed button can drop its focus, so a refusal hands focus back to it unless the person has moved on.
  async function run(write, pressed) {
    const buttons = [...root.querySelectorAll('button')];
    for (const b of buttons) b.disabled = true;
    errorEl?.remove();
    errorEl = null;
    try {
      await write();
    } catch (e) {
      if (!root.isConnected) return;
      errorEl = h('p', { class: 'co-error', role: 'alert' }, describeWriteError(e, { noun: 'decision' }));
      root.appendChild(errorEl);
      for (const b of buttons) b.disabled = false;
      const active = document.activeElement;
      if (!active || active === document.body || active === pressed) pressed?.focus();
      return;
    }
    // A write that went through redraws the band; a card it replaced is left alone.
    if (root.isConnected) for (const b of buttons) b.disabled = false;
  }
  const press = (write) => (ev) => run(write, ev.currentTarget);

  const opts = h('div', { class: 'co-decision__opts' });
  (decision.options || []).forEach((text, i) => {
    const idx = i + 1;
    const rec = decision.recommendation === idx;
    opts.appendChild(h('button', {
      type: 'button',
      class: 'co-decision__opt' + (rec ? ' is-rec' : ''),
      on: { click: press(() => onResolve?.(idx)) },
    },
      h('span', { class: 'co-decision__opt-num' }, `${idx}.`),
      h('span', { class: 'co-decision__opt-text' }, text),
      rec ? h('span', { class: 'co-decision__rec' }, 'Recommended') : null,
    ));
  });
  root.appendChild(opts);

  const actions = h('div', { class: 'co-decision__actions' });
  if (decision.recommendation) {
    actions.appendChild(h('button', {
      type: 'button',
      class: 'btn btn--primary btn--sm co-decision__primary',
      on: { click: press(() => onResolve?.(decision.recommendation)) },
    }, `Pick option ${decision.recommendation}`));
  }
  actions.appendChild(h('button', {
    type: 'button',
    class: 'btn btn--ghost btn--sm co-decision__drop',
    on: { click: press(() => onDrop?.(decision.id)) },
  }, 'Drop'));
  root.appendChild(actions);
  return { root };
}
