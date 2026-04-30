import { sparkline } from './sparkline.js';
import { dotMeter } from './dot-meter.js';
import { anchorPills } from './anchor-pills.js';

const KIND_ICON = { gotcha: '⚠', pattern: '◇', 'anti-pattern': '⊘' };
const KIND_TOOLTIP = { gotcha: 'gotcha', pattern: 'pattern', 'anti-pattern': 'anti-pattern' };

function _fmtSince(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function _matches7d(lesson) {
  // Best-effort passive count if the server provides it; fall back to 0.
  return Number(lesson.anchor_matches_7d || 0);
}

export function lessonCard(lesson, { onReinforce } = {}) {
  const shelf = lesson.shelf || 'active';
  const card = document.createElement('article');
  card.className = `lesson-card lesson-card--${shelf}`;
  card.setAttribute('data-lesson-id', lesson.id);

  // ---- head: kind icon · id · title · since
  const head = document.createElement('div');
  head.className = 'lesson-card__head';

  const kind = document.createElement('span');
  kind.className = 'lesson-card__kind';
  kind.textContent = KIND_ICON[lesson.kind] || '◇';
  kind.title = KIND_TOOLTIP[lesson.kind] || lesson.kind || 'pattern';
  head.appendChild(kind);

  const id = document.createElement('span');
  id.className = 'lesson-card__id';
  id.textContent = lesson.id;
  head.appendChild(id);

  const title = document.createElement('span');
  title.className = 'lesson-card__title';
  title.textContent = lesson.title || '(untitled)';
  head.appendChild(title);

  card.appendChild(head);

  // first_seen caption
  const since = document.createElement('div');
  since.className = 'lesson-card__since';
  since.textContent = lesson.created ? `since ${_fmtSince(lesson.created)}` : '';
  card.appendChild(since);

  // ---- signals row: passive (left, dot meter) · active (right, sparkline pill)
  const signals = document.createElement('div');
  signals.className = 'lesson-card__signals';
  signals.appendChild(dotMeter(_matches7d(lesson)));
  signals.appendChild(sparkline(lesson));
  card.appendChild(signals);

  // ---- anchors row
  card.appendChild(anchorPills(lesson));

  // ---- reinforce button
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'lesson-card__reinforce';
  btn.textContent = shelf === 'retired' ? '↑ Revive' : '↑ Reinforce';
  btn.addEventListener('click', async (ev) => {
    ev.stopPropagation();
    if (btn.classList.contains('is-fired')) return;
    btn.disabled = true;
    try {
      const summary = await onReinforce?.(lesson.id);
      btn.classList.add('is-fired');
      btn.textContent = '✓ Reinforced now';
      if (summary) {
        // Update local lesson view in-place
        Object.assign(lesson, summary);
      }
    } catch (e) {
      btn.disabled = false;
      btn.textContent = 'Failed — retry';
      console.error(e);
    }
  });
  card.appendChild(btn);

  return card;
}

export default lessonCard;
