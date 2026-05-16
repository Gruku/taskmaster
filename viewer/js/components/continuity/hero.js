import { h } from '../../util/h.js';
import { createDecisionCard } from './decision-card.js';

/**
 * Hero surface — renders a decision card if the hero item is a decision,
 * or a lightweight handover resume card otherwise.
 *
 * @param {Object} opts
 * @param {Object|null} opts.item      Hero continuity item (from pickHero)
 * @param {Object|null} [opts.decision]  Full decision doc (if item.type==='decision')
 * @param {Function} [opts.onResolve]  (optionIndex) => void  — decision resolved
 * @param {Function} [opts.onDrop]     (decisionId) => void   — decision dropped
 */
export function createHero({ item, decision, onResolve, onDrop }) {
  if (!item) {
    const empty = h('div', { class: 'co-hero co-hero--empty' },
      h('p', { class: 'co-hero__empty-msg' }, 'No pending decisions — you\'re clear.'),
    );
    return { root: empty };
  }

  if (item.type === 'decision' && decision) {
    const wrap = h('div', { class: 'co-hero co-hero--decision' });
    const card = createDecisionCard({ item, decision, onResolve, onDrop });
    wrap.appendChild(card.root);
    return { root: wrap };
  }

  // Handover resume fallback.
  const root = h('div', { class: 'co-hero co-hero--resume' },
    h('div', { class: 'co-hero__resume-head' },
      h('span', { class: 'co-chip co-chip--han' }, 'Resume'),
      h('span', { class: 'co-hero__resume-title' }, item.title),
    ),
    item.next ? h('p', { class: 'co-hero__resume-next' }, item.next) : null,
    item.where ? h('p', { class: 'co-hero__resume-where' }, item.where) : null,
  );
  return { root };
}
