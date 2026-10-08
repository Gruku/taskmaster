// User intent: the bug page reads as the shared detail template — its summary and location finally shown, status and
// severity as markers, where it was found and went in the rail — and a missing or failed load is said in words, never
// with the server's text. Its actions are in-app forms: Mark fixed leads in topbar row 1, refusals are said in words.
import * as api from '../api.js';
import { claimTopbar, claimTopbarPrimary, tmAction } from '../lib/topbar.js';
import { openModalCount, topModal } from '../components/modal.js';
import { openMarkFixed, openAdopt, openPromote, shelveBug } from '../components/edit/bug-actions.js';
import { describeWriteError } from '../components/edit/write-errors.js';
import { h } from '../util/h.js';
import { statusMarker, severityMarker } from '../components/status.js';
import { linkRoute } from '../components/link-pills.js';
import { stateBlock } from '../components/empty-state.js';
import {
  detailMeta, stampEl, copyId, copyButton, detailTitle, detailHead, detailTag, markdownBody, detailSection,
  detailGrid, railPanel, railGroup,
} from '../components/detail-page.js';

export const meta = { title: 'Bug', icon: '⊘', sidebarKey: 'bugs' };

const ROOT_CLASSES = ['td-doc', 'td-doc--page', 'td-page', 'dp-page', 'dp-page--bug'];
const TO_BUGS = { label: 'Open Bugs', href: '#/bugs' };
const ACTIONABLE = new Set(['open', 'shelved']);

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
  return h('div', { class: 'td-body' }, [...sections]);
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

// The secondary actions; Mark fixed is topbar row 1. `act(name, button)` runs one.
function actionRow(bug, act) {
  const button = (name, label) => {
    const el = h('button', { type: 'button', class: 'btn btn--secondary', 'data-action': name }, label);
    el.addEventListener('click', () => act(name, el));
    return el;
  };
  return h('div', { class: 'dp-actions', role: 'group', 'aria-label': 'Bug actions' }, [
    (bug.status || 'open') === 'open' ? button('shelve', 'Shelve') : null,
    button('adopt', 'Adopt into task'),
    button('promote', 'Promote to issue'),
    h('div', { class: 'dp-actions__message', role: 'alert' }),
  ]);
}

function page(bug, { tasks, timers, act }) {
  const head = detailHead({
    meta: detailMeta([
      copyId({ id: bug.id, noun: 'bug', timers }),
      h('a', { href: '#/bugs' }, 'Bugs'),
      hasText(bug.found_in) ? h('span', {}, ['found in ', h('a', { href: linkRoute(bug.found_in) }, bug.found_in)]) : null,
      stampEl(bug.discovered, { prefix: 'discovered' }),
      hasText(bug.discovered_by) ? h('span', {}, `reported by ${bug.discovered_by}`) : null,
    ].filter(Boolean)),
    title: detailTitle(bug.title),
    after: [markers(bug, timers), act && ACTIONABLE.has(bug.status || 'open') ? actionRow(bug, act) : null].filter(Boolean),
  });
  return [head, detailGrid({ body: body(bug), panels: rail(bug, tasks) })];
}

// Promote moves to the new issue and the button that had focus goes with this page: focus follows to the next
// page's title (or its settled empty state) rather than falling to <body>, unless the user has already moved it
// anywhere (the sidebar included) — only focus left on <body> is taken.
function focusNextPage(root) {
  const old = root.querySelector('h1');
  let obs = null;
  const stop = () => { obs?.disconnect(); clearTimeout(timer); };
  const timer = setTimeout(stop, 5000);
  obs = new MutationObserver(() => {
    const target = [...root.querySelectorAll('h1, .tm-empty:not([aria-busy="true"])')].find((el) => el !== old);
    if (!target) return;
    stop();
    const active = document.activeElement;
    if (active && active !== document.body) return;
    if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
    target.focus();
  });
  obs.observe(root, { childList: true, subtree: true });
}

export function mount(root, { params, subpath, store }) {
  const id = subpath?.[0] || params?.id || null;
  const timers = new Set();
  let disposed = false;
  let shelving = false;
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
    claimTopbarPrimary();
    return loading;
  }

  function retry() {
    showLoading().focus();
    refocus = true;
    void load();
  }

  // After a write: re-read and repaint, focus on the heading. Row 1's Mark fixed is bound to the bug as it was,
  // so it goes now and comes back only if the re-read paints a bug that can still be marked fixed.
  function done() {
    if (disposed) return;
    claimTopbarPrimary();
    refocus = true;
    void load();
  }

  function paintPrimary(bug) {
    const slot = claimTopbarPrimary();
    if (!slot || !ACTIONABLE.has(bug.status || 'open')) return;
    slot.appendChild(tmAction({
      icon: 'check', label: 'Mark fixed', variant: 'primary', title: 'Mark this bug fixed',
      onClick: () => openMarkFixed({ bug, onDone: done }),
    }));
  }

  async function act(name, el, bug) {
    if (name === 'adopt') openAdopt({ bug, getBacklog: () => store?.getBacklog?.(), onDone: done });
    else if (name === 'promote') {
      openPromote({ bug, onDone: (issueId) => {
        if (disposed) return;
        if (!issueId) { done(); return; }
        focusNextPage(root);
        location.hash = `#/issue/${encodeURIComponent(issueId)}`;
      } });
    } else if (name === 'shelve') {
      // One shelve at a time: from the confirm until the answer the row is busy and its buttons are off.
      if (shelving) return;
      shelving = true;
      const row = el.closest('.dp-actions');
      const msg = row?.querySelector('.dp-actions__message');
      if (msg) msg.textContent = '';
      const setBusy = (busy) => {
        if (!row) return;
        if (busy) row.setAttribute('aria-busy', 'true');
        else row.removeAttribute('aria-busy');
        row.querySelectorAll('button').forEach((b) => { b.disabled = busy; });
      };
      // undefined is a saved shelve: the row stays busy until done() repaints the page. A throw is a refusal too,
      // said in words below, so the row never stays off and the rejection never escapes.
      let answer;
      try {
        answer = await shelveBug({ bug, onConfirm: () => setBusy(true) });
      } catch (e) {
        answer = { error: describeWriteError(e, { noun: 'bug' }) };
      } finally {
        shelving = false;
      }
      if (disposed) return;
      if (answer?.error || answer?.cancelled) {
        setBusy(false);
        if (answer.error && msg) msg.textContent = answer.error;
        el.focus();
      } else done();
    }
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
        claimTopbarPrimary();
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
      claimTopbarPrimary();
      takeFocus(root.querySelector('.tm-empty a[href]'));
      return;
    }
    const backlog = store?.getBacklog?.();
    const tasks = Array.isArray(backlog?.tasks) ? backlog.tasks : [];
    root.replaceChildren(...page(bug, { tasks, timers, act: (name, el) => act(name, el, bug) }));
    paintPrimary(bug);
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
    claimTopbarPrimary();
    // An action form left open is asked to close once: a clean one goes, a typed one asks to discard.
    if (openModalCount() > 0) topModal()?.requestClose();
    timers.forEach(clearTimeout);
    timers.clear();
    root.classList.remove(...ROOT_CLASSES);
  };
}
