// User intent: the bug page reads as the shared detail template — its summary and location finally shown, status and
// severity as markers, where it was found and went in the rail — and a missing or failed load is said in words, never
// with the server's text. Task 9 brings the actions back as forms.
import * as api from '../api.js';
import { claimTopbar } from '../lib/topbar.js';
import { h } from '../util/h.js';
import { statusMarker, severityMarker } from '../components/status.js';
import { linkRoute } from '../components/link-pills.js';
import { stateBlock } from '../components/empty-state.js';
import {
  detailMeta, stampEl, copyId, copyButton, detailTitle, detailHead, detailTag, markdownBody, detailSection, datesList,
  detailGrid, railPanel, railGroup,
} from '../components/detail-page.js';

export const meta = { title: 'Bug', icon: '⊘', sidebarKey: 'bugs' };

const ROOT_CLASSES = ['td-doc', 'td-doc--page', 'td-page', 'dp-page', 'dp-page--bug'];
const TO_BUGS = { label: 'Open Bugs', href: '#/bugs' };

const hasText = (v) => typeof v === 'string' && v.trim() !== '';
const textList = (v) => (Array.isArray(v) ? v : hasText(v) ? [v] : []).filter(hasText);

// A marker with a visually hidden key, so a screen reader hears "Severity Medium", not a bare word.
function markerHost(field, key, marker) {
  return h('span', { class: 'td-marker-host', 'data-field': field }, [h('span', { class: 'dp-key' }, key), marker]);
}

function markers(bug, timers) {
  const row = h('div', { class: 'td-markers', 'data-test': 'chips' });
  row.appendChild(markerHost('status', 'Status', statusMarker('bug', bug.status || 'open')));
  // No default: a bug filed without a severity shows none rather than an invented "Medium".
  const severity = severityMarker(bug.severity);
  if (severity) row.appendChild(markerHost('severity', 'Severity', severity));
  const components = textList(bug.components);
  if (components.length) row.appendChild(detailTag('components', 'Components', components.join(', ')));
  if (hasText(bug.fix_commit)) {
    row.appendChild(copyButton({
      value: bug.fix_commit, label: `Copy fix commit ${bug.fix_commit}`, className: 'td-tag', tag: 'fix_commit', timers,
      children: [h('span', { class: 'td-tag__key' }, 'Fix commit'), h('code', {}, bug.fix_commit)],
    }));
  }
  if (bug.archived) row.appendChild(detailTag('archived', 'Archived', 'yes'));
  return row;
}

function body(bug) {
  const sections = [];
  if (hasText(bug.summary)) sections.push(detailSection({ key: 'summary', label: 'Summary', body: markdownBody(bug.summary) }));
  const paths = textList(bug.location);
  if (paths.length) {
    sections.push(detailSection({ key: 'location', label: 'Location', body: h('ul', { class: 'dp-paths' }, paths.map((p) => h('li', {}, h('code', {}, p)))) }));
  }
  if (!sections.length) sections.push(h('p', { class: 'td-empty' }, 'Nothing written for this bug yet.'));
  return h('div', { class: 'td-body' }, [...sections, datesList([['Discovered', bug.discovered]])]);
}

function taskRow(id, tasks) {
  const task = tasks.find((t) => t?.id === id);
  return h('ul', { class: 'td-dep-list' }, h('li', {}, h('a', { class: 'td-dep', href: linkRoute(id) }, [
    h('span', { class: 'td-dep__id' }, id),
    task?.title ? h('span', { class: 'td-dep__title' }, task.title) : null,
    task?.status ? statusMarker('task', task.status) : null,
  ])));
}

function rail(bug, tasks) {
  const groups = [];
  if (hasText(bug.found_in)) groups.push(railGroup({ name: 'found-in', label: 'Found in', body: taskRow(bug.found_in, tasks) }));
  if (hasText(bug.adopted_into)) groups.push(railGroup({ name: 'adopted-into', label: 'Adopted into', body: taskRow(bug.adopted_into, tasks) }));
  if (hasText(bug.promoted_to)) {
    groups.push(railGroup({
      name: 'promoted-to', label: 'Promoted to',
      body: h('a', { class: 'link-pill', href: linkRoute(bug.promoted_to) }, [
        h('span', { class: 'link-pill__label' }, 'Issue'), h('span', { class: 'link-pill__id' }, bug.promoted_to),
      ]),
    }));
  }
  return groups.length ? [railPanel({ name: 'relations', label: 'Relations', children: groups })] : [];
}

function page(bug, { tasks, timers }) {
  const head = detailHead({
    meta: detailMeta([
      copyId({ id: bug.id, noun: 'bug', timers }),
      h('a', { href: '#/bugs' }, 'Bugs'),
      hasText(bug.found_in) ? h('span', {}, ['found in ', h('a', { href: linkRoute(bug.found_in) }, bug.found_in)]) : null,
      stampEl(bug.discovered, { prefix: 'discovered' }),
      hasText(bug.discovered_by) ? h('span', {}, `reported by ${bug.discovered_by}`) : null,
    ].filter(Boolean)),
    title: detailTitle(bug.title),
    after: [markers(bug, timers)],
  });
  return [head, detailGrid({ body: body(bug), panels: rail(bug, tasks) })];
}

export function mount(root, { params, subpath, store }) {
  const id = subpath?.[0] || params?.id || null;
  const timers = new Set();
  let disposed = false;
  root.classList.add(...ROOT_CLASSES);
  claimTopbar();

  // Set by Try again: whatever the retried read paints next takes focus, so it is never left on <body>.
  let refocus = false;
  function takeFocus(el) {
    if (!refocus) return;
    refocus = false;
    if (!el) return;
    if (!el.matches('a[href], button') && !el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
    el.focus();
  }

  function showLoading() {
    const loading = stateBlock({ headline: 'Loading…', busy: true });
    loading.setAttribute('tabindex', '-1');
    root.replaceChildren(loading);
    return loading;
  }

  function retry() {
    showLoading().focus();
    refocus = true;
    void load();
  }

  async function load() {
    let bug;
    try {
      bug = await api.getBug(id);
    } catch (e) {
      if (disposed) return;
      if (e?.code !== 404) {
        root.replaceChildren(stateBlock({
          state: 'error', label: id, headline: 'Could not load this bug',
          hint: 'Something went wrong while loading it. Try again in a moment.', action: { label: 'Try again', onClick: retry },
        }));
        takeFocus(root.querySelector('.tm-empty button'));
        return;
      }
      bug = null;
    }
    if (disposed) return;
    if (!bug || typeof bug !== 'object' || Array.isArray(bug)) {
      root.replaceChildren(stateBlock({
        state: 'missing', label: id, headline: 'Bug not found', hint: 'It may have been archived or renamed.', action: TO_BUGS,
      }));
      takeFocus(root.querySelector('.tm-empty a[href]'));
      return;
    }
    const backlog = store?.getBacklog?.();
    const tasks = Array.isArray(backlog?.tasks) ? backlog.tasks : [];
    root.replaceChildren(...page(bug, { tasks, timers }));
    takeFocus(root.querySelector('h1'));
  }

  if (!id) {
    root.replaceChildren(stateBlock({ state: 'empty', label: 'Bug', headline: 'No bug open', hint: 'Pick one from the Bugs list.', action: TO_BUGS }));
  } else {
    showLoading();
    void load();
  }

  return () => {
    disposed = true;
    timers.forEach(clearTimeout);
    timers.clear();
    root.classList.remove(...ROOT_CLASSES);
  };
}
