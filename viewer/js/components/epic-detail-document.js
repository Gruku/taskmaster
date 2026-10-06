// User intent: an epic's detail reads like every detail page — its lifecycle and design state as words, progress and a
// status breakdown counted from the very task list it draws, its tasks grouped by status as real links — on the
// /epic/<id> page and in the detail modal alike. Pure of page chrome: it writes nothing outside `container`.
import { mountMarkdown } from './markdown.js';
import {
  designBadge, epicStats, epicProgress, epicBreakdown, isCloseable, epicStatusMeta, STATUS_GROUPS,
} from '../lib/epic-format.js';
import { marker, statusMeta, priorityMarker } from './status.js';
import { linkRow } from './link-row.js';
import { stateBlock } from './empty-state.js';
import { truncate } from '../lib/text.js';
import { h } from '../util/h.js';
import { icon } from './icon.js';
import { mountComponentDiagram } from './component-diagram.js';

// "Closeable" nudges an epic that is still open; a done or archived one needs none.
const OPEN_STATUSES = new Set(['active', 'planned']);
// Finished work stays folded until asked for.
const FOLDED = new Set(['done', 'archived']);
const OTHER = Object.freeze({ label: 'Other', shape: '○', tone: 'neutral' });
const ATTENTION = {
  blocked: { label: 'Blocked', shape: '◆', tone: 'critical' },
  blockers: { label: 'Has blockers', shape: '▲', tone: 'warning' },
};

const groupMeta = (status) => (status === 'other' ? { ...OTHER } : statusMeta('task', status));
// The bucket epicStats() counts a task in, so a group's rows and the breakdown's figure are the same tasks.
const bucketOf = (t) => {
  const s = t.status == null || t.status === '' ? 'todo' : t.status;
  return STATUS_GROUPS.includes(s) ? s : 'other';
};
const words = (v) => (Array.isArray(v) ? v.filter(Boolean).join('; ') : v == null ? '' : String(v));
const section = (cls, title, ...body) => h('section', { class: cls }, [h('h2', { class: 'ed-h' }, title), ...body]);

function header(epic, stats, chrome) {
  const sep = () => h('span', { class: 'ed-sep', 'aria-hidden': 'true' }, '·');
  const tag = (text) => h('span', { class: 'ed-tag' }, text);
  const open = epic.status == null || epic.status === '' || OPEN_STATUSES.has(epic.status);
  const parts = [];
  if (chrome === 'page') {
    const meta = [h('span', { class: 'ed-id' }, String(epic.id)), sep(), h('a', { href: '#/epics' }, 'Epics')];
    if (epic.phase) meta.push(sep(), h('span', {}, String(epic.phase)));
    parts.push(h('div', { class: 'ed-meta' }, meta), h('h1', { class: 'ed-title' }, String(epic.name || epic.id)));
  }
  parts.push(h('div', { class: 'ed-markers' }, [
    marker(epicStatusMeta(epic.status)),
    tag(`Design · ${designBadge(epic.design_status).label}`),
    isCloseable(stats) && open ? tag('Closeable') : null,
    epic.area ? tag(String(epic.area)) : null,
  ]));
  if (epic.done_when) {
    parts.push(h('p', { class: 'ed-done-when' }, [h('span', { class: 'ed-label' }, 'Done when'), String(epic.done_when)]));
  }
  return h('header', { class: 'ed-head' }, parts);
}

function progress(stats) {
  if (!stats.total) return section('ed-progress', 'Progress', stateBlock({ label: 'Tasks', headline: 'No tasks in this epic yet.' }));
  const parts = epicBreakdown(stats);
  const bar = h('div', { class: 'ed-breakdown', 'aria-hidden': 'true' }, parts.map(({ status, count, pct }) => {
    const seg = h('span', { class: `ed-seg ed-seg--${status}`, title: `${groupMeta(status).label}: ${count}` });
    seg.style.width = `${pct}%`;
    return seg;
  }));
  const legend = h('ul', { class: 'ed-legend' }, parts.map(({ status, count }) =>
    h('li', {}, [marker(groupMeta(status)), h('span', { class: 'ed-legend__n' }, String(count))])));
  return section('ed-progress', 'Progress', h('p', { class: 'ed-progress__label' }, epicProgress(stats).label), bar, legend);
}

function taskRow(t) {
  const id = String(t.id);
  return linkRow({
    tag: 'li',
    className: 'ed-task',
    href: `#/task/${encodeURIComponent(id)}`,
    name: h('span', { class: 'ed-task__name' }, [h('span', { class: 't-id' }, id), ' ', truncate(t.title || id, { lines: 2 })]),
    content: t.priority ? [priorityMarker(t.priority)] : [],
  });
}

function taskGroups(tasks) {
  const groups = h('div', { class: 'ed-groups' });
  for (const status of [...STATUS_GROUPS, 'other']) {
    const list = tasks.filter((t) => bucketOf(t) === status);
    if (!list.length) continue;
    const group = h('details', { class: 'ed-group', 'data-status': status }, [
      h('summary', { class: 'ed-group__head' },
        [icon('chevron', { size: 16 }), marker(groupMeta(status)), h('span', { class: 'ed-group__n' }, String(list.length))]),
      h('ul', { class: 'ed-task-list' }, list.map(taskRow)),
    ]);
    group.open = !FOLDED.has(status);
    groups.appendChild(group);
  }
  return section('ed-tasks', 'Tasks', groups);
}

function attention(items) {
  return section('ed-attention', 'Attention', h('ul', { class: 'ed-attn' }, items.map((a) => {
    const why = words(a.why);
    return h('li', {}, [
      marker(a.blocked ? ATTENTION.blocked : ATTENTION.blockers),
      h('a', { href: `#/task/${encodeURIComponent(String(a.id))}`, title: a.title ? String(a.title) : null }, String(a.id)),
      why ? truncate(why) : null,
    ]);
  })));
}

function docsList(docs) {
  return section('ed-docs-block', 'Docs', h('ul', { class: 'ed-docs' }, Object.entries(docs).map(([key, path]) =>
    h('li', {}, [
      h('a', { href: `/file/${path}`, target: '_blank', rel: 'noopener' }, key),
      h('span', { class: 't-tech' }, String(path)),
    ]))));
}

// chrome: 'page' (route screen: the id · Epics line and the title) | 'embedded' (the modal: its heading shows the name).
// Every task link is a real href: the page's detail interceptor and the modal's own link handler route them, so
// onNavigate is accepted and unused. onComponentNav(componentKey) is handed to the architecture map.
// Lifecycle contract: callers MUST invoke the returned dispose() before re-mounting on the same container — it is the
// only handle that disconnects the architecture map's ResizeObserver.
export function mountEpicDetail(container, { epic, store, onNavigate, onComponentNav, chrome = 'page' } = {}) {
  container.classList.add('ed-root');
  container.replaceChildren();

  // Figures come from the list this document draws, so the label, the breakdown and the groups always agree.
  const tasks = (Array.isArray(epic.tasks) ? epic.tasks : []).filter((t) => t && typeof t === 'object');
  const stats = epicStats(tasks);

  const main = h('div', { class: 'ed-main' });
  const side = h('aside', { class: 'ed-side' });
  container.append(header(epic, stats, chrome), h('div', { class: 'ed-grid' }, [main, side]));

  const narrative = [epic.description, epic._body].filter(Boolean).join('\n\n');
  if (narrative) {
    const md = h('div', { class: 'ed-md' });
    mountMarkdown(md, narrative);
    main.appendChild(section('ed-narrative', 'Design', md));
  }
  main.appendChild(progress(stats));
  if (stats.total) main.appendChild(taskGroups(tasks));

  // C2 — Epic Architecture Map: blocks in dependency-rank order, each holding the component's task cards.
  let disposeDiagram = () => {};
  const comps = epic.components || {};
  if (Object.keys(comps).length) {
    const canvas = h('div', { class: 'ed-diagram__canvas' });
    main.appendChild(section('ed-diagram', 'Architecture', canvas));
    disposeDiagram = mountComponentDiagram(canvas, {
      components: comps,
      rollup: epic.component_rollup || {},
      tasks,
      onComponentNav,
    });
  }

  const flagged = (Array.isArray(epic.attention) ? epic.attention : []).filter((a) => a && a.id != null);
  if (flagged.length) side.appendChild(attention(flagged));
  const docs = epic.docs && typeof epic.docs === 'object' ? epic.docs : {};
  if (Object.keys(docs).length) side.appendChild(docsList(docs));

  return () => { disposeDiagram(); container.classList.remove('ed-root'); container.replaceChildren(); };
}
