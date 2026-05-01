import { sparkline } from './sparkline.js';
import { dotMeter } from './dot-meter.js';
import { anchorPills } from './anchor-pills.js';

const KIND_ICON = { gotcha: '⚠', pattern: '◇', 'anti-pattern': '⊘' };
const KIND_TOOLTIP = { gotcha: 'gotcha', pattern: 'pattern', 'anti-pattern': 'anti-pattern' };
const FOOT_PILLS_MAX = 3;

function _matches7d(lesson) {
  return Number(lesson.anchor_matches_7d || 0);
}

export function lessonCard(lesson, { onReinforce } = {}) {
  const shelf = lesson.shelf || 'active';
  const card = document.createElement('article');
  card.className = `lesson-card lesson-card--${shelf}`;
  card.setAttribute('data-lesson-id', lesson.id);
  card.setAttribute('role', 'link');
  card.setAttribute('tabindex', '0');

  const navigate = () => { location.hash = `#/lesson/${encodeURIComponent(lesson.id)}`; };
  card.addEventListener('click', (ev) => {
    if (ev.target.closest('.lesson-card__reinforce') || ev.target.closest('a')) return;
    navigate();
  });
  card.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); navigate(); }
  });

  // ---- head: kind · id · sparkline (right)
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

  const spark = sparkline(lesson);
  spark.classList.add('lesson-card__spark');
  head.appendChild(spark);

  card.appendChild(head);

  // ---- title (own row)
  const title = document.createElement('div');
  title.className = 'lesson-card__title';
  title.textContent = lesson.title || '(untitled)';
  card.appendChild(title);

  // ---- summary (only if present)
  const summaryText = (lesson.summary || '').trim();
  if (summaryText) {
    const summary = document.createElement('div');
    summary.className = 'lesson-card__summary';
    summary.textContent = summaryText;
    card.appendChild(summary);
  }

  // ---- anchors row: pills (left) + passive dot-meter (right)
  const anchorsRow = document.createElement('div');
  anchorsRow.className = 'lesson-card__anchors-row';
  anchorsRow.appendChild(anchorPills(lesson));
  const meter = dotMeter(_matches7d(lesson));
  meter.classList.add('lesson-card__passive');
  anchorsRow.appendChild(meter);
  card.appendChild(anchorsRow);

  // ---- foot: related_tasks pills + "N× fired"
  const foot = document.createElement('div');
  foot.className = 'lesson-card__foot';

  const tasks = Array.isArray(lesson.related_tasks) ? lesson.related_tasks : [];
  const visibleTasks = tasks.slice(0, FOOT_PILLS_MAX);
  for (const t of visibleTasks) {
    const pill = document.createElement('span');
    pill.className = 'lesson-card__task-pill';
    pill.textContent = typeof t === 'string' ? t : (t.id || '');
    foot.appendChild(pill);
  }
  if (tasks.length > FOOT_PILLS_MAX) {
    const more = document.createElement('span');
    more.className = 'lesson-card__task-more';
    more.textContent = `+${tasks.length - FOOT_PILLS_MAX} more`;
    foot.appendChild(more);
  }

  const fired = document.createElement('span');
  fired.className = 'lesson-card__fired';
  const ct = lesson.reinforce_count || 0;
  fired.innerHTML = `<strong>${ct}</strong>× fired`;
  foot.appendChild(fired);

  card.appendChild(foot);

  // ---- reinforce button (hover-revealed corner action)
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'lesson-card__reinforce';
  btn.textContent = shelf === 'retired' ? '↑ Revive' : '↑ Reinforce';
  btn.addEventListener('click', async (ev) => {
    ev.stopPropagation();
    if (btn.classList.contains('is-fired')) return;
    btn.disabled = true;
    try {
      await onReinforce?.(lesson.id);
      btn.classList.add('is-fired');
      btn.textContent = '✓ Reinforced now';
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
