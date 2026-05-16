import { h } from '../../util/h.js';

export function createDecisionCard({ item, decision, onResolve, onDrop }) {
  const root = h('div', { class: 'co-decision' });
  root.appendChild(h('div', { class: 'co-decision__rail' },
    h('span', { class: 'co-chip co-chip--dec' }, 'Decision'),
    h('span', { class: 'co-decision__id' }, `${decision.id} · ${item.title}`),
  ));
  root.appendChild(h('h3', { class: 'co-decision__title' }, decision.title));
  const opts = h('div', { class: 'co-decision__opts' });
  (decision.options || []).forEach((text, i) => {
    const idx = i + 1;
    const rec = decision.recommendation === idx;
    const opt = h('div', {
      class: 'co-decision__opt' + (rec ? ' is-rec' : ''),
      on: { click: () => onResolve?.(idx) },
    },
      h('span', { class: 'co-decision__opt-num' }, `${idx}.`),
      h('span', { class: 'co-decision__opt-text' }, text),
      rec ? h('span', { class: 'co-decision__opt-star' }, '★ rec') : null,
    );
    opts.appendChild(opt);
  });
  root.appendChild(opts);

  const actions = h('div', { class: 'co-decision__actions' });
  if (decision.recommendation) {
    actions.appendChild(h('button', {
      type: 'button',
      class: 'co-decision__primary',
      on: { click: () => onResolve?.(decision.recommendation) },
    }, `Pick option ${decision.recommendation}`));
  }
  actions.appendChild(h('button', {
    type: 'button',
    class: 'co-decision__drop',
    on: { click: () => onDrop?.(decision.id) },
  }, 'Drop'));
  root.appendChild(actions);
  return { root };
}
