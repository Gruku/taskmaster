// User intent: the Dashboard opens on four counts — in progress, waiting on you, open issues, open bugs — each a link to
// where they are; a count that could not be read says so rather than showing a wrong number.
import { h } from '../../util/h.js';

export const SUMMARY_LINKS = [
  { key: 'inProgress', label: 'In progress', href: '#/table?status=in-progress' },
  { key: 'waiting', label: 'Waiting on you', href: '#/table?status=in-review' },
  { key: 'issues', label: 'Open issues', href: '#/issues' },
  { key: 'bugs', label: 'Open bugs', href: '#/bugs' },
];

const OPEN_ISSUE = new Set(['open', 'investigating']);

// Tasks always count (an unloaded board is nothing in progress); an issue or bug list that was not read is null.
export function summaryCounts({ tasks, issues, bugs }) {
  const list = Array.isArray(tasks) ? tasks : [];
  const withStatus = (status) => list.filter((t) => t?.status === status).length;
  return {
    inProgress: withStatus('in-progress'),
    waiting: withStatus('in-review'),
    issues: Array.isArray(issues) ? issues.filter((i) => OPEN_ISSUE.has(i?.status)).length : null,
    bugs: Array.isArray(bugs) ? bugs.filter((b) => b?.status === 'open').length : null,
  };
}

export function createSummaryStrip() {
  const numbers = {};
  const anchors = {};
  const items = SUMMARY_LINKS.map(({ key, label, href }) => {
    numbers[key] = h('span', { class: 'dk-stat__n' }, '—');
    // The space keeps the link's name "2 In progress"; between flex items it is not drawn.
    anchors[key] = h('a', { class: 'dk-stat', href }, numbers[key], ' ', h('span', { class: 'dk-stat__label' }, label));
    return h('li', {}, anchors[key]);
  });
  const root = h('nav', { class: 'dk-summary', 'aria-label': 'Project summary' }, h('ul', {}, items));

  // Rewrites the numbers only, so a focused link keeps its focus across a redraw.
  function update(counts = {}) {
    for (const { key } of SUMMARY_LINKS) {
      const n = counts[key];
      const known = typeof n === 'number';
      numbers[key].textContent = known ? String(n) : '—';
      if (known) anchors[key].removeAttribute('title');
      else anchors[key].setAttribute('title', 'Not loaded');
    }
  }

  return { root, update };
}
