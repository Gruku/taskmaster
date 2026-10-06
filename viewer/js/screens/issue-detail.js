// User intent: the issue page reads as the shared detail template — severity and status as markers, a stale tag instead
// of an aging bar, rendered sections, links in the rail — and a missing or failed load is said in words, never with the
// server's text.
import * as api from '../api.js';
import { claimTopbar } from '../lib/topbar.js';
import { h } from '../util/h.js';
import { issueDiscovered, issueEvidence } from '../util/issue-fields.js';
import { statusMarker, severityMarker } from '../components/status.js';
import { staleTag } from '../components/stale-tag.js';
import { linkPillsEl, legacyLinksToTyped } from '../components/link-pills.js';
import { stateBlock } from '../components/empty-state.js';
import {
  detailMeta, stampEl, copyId, detailTitle, detailHead, markdownBody, detailSection, datesList, detailGrid, railPanel, railGroup,
} from '../components/detail-page.js';

export const meta = { title: 'Issue', icon: '!', sidebarKey: 'issues' };

const ROOT_CLASSES = ['td-doc', 'td-doc--page', 'td-page', 'dp-page', 'dp-page--issue'];
const TO_ISSUES = { label: 'Open Issues', href: '#/issues' };

const hasText = (v) => typeof v === 'string' && v.trim() !== '';
const textList = (v) => (Array.isArray(v) ? v : hasText(v) ? [v] : []).filter(hasText);

// A marker with a visually hidden key, so a screen reader hears "Severity High", not a bare word.
function markerHost(field, key, marker) {
  return h('span', { class: 'td-marker-host', 'data-field': field }, [h('span', { class: 'dp-key' }, key), marker]);
}

function markers(issue, agingCfg) {
  const row = h('div', { class: 'td-markers', 'data-test': 'chips' });
  const severity = severityMarker(issue.severity_label ?? issue.severity);
  if (severity) row.appendChild(markerHost('severity', 'Severity', severity));
  row.appendChild(markerHost('status', 'Status', statusMarker('issue', issue.status || 'open')));
  const stale = staleTag(issue, agingCfg);
  if (stale) row.appendChild(h('span', { class: 'td-marker-host', 'data-tag': 'stale' }, stale));
  return row;
}

function body(issue) {
  const sections = [];
  const evidence = issueEvidence(issue);
  if (hasText(evidence)) sections.push(detailSection({ key: 'evidence', label: 'Evidence', body: markdownBody(evidence) }));
  const steps = textList(issue.repro);
  if (steps.length) {
    sections.push(detailSection({
      key: 'repro', label: `Reproduction · ${steps.length} ${steps.length === 1 ? 'step' : 'steps'}`,
      body: h('ol', { class: 'dp-steps' }, steps.map((s) => h('li', {}, s))),
    }));
  }
  if (hasText(issue.impact)) sections.push(detailSection({ key: 'impact', label: 'Impact', body: markdownBody(issue.impact) }));
  if (hasText(issue.summary)) sections.push(detailSection({ key: 'notes', label: 'Notes', body: markdownBody(issue.summary) }));
  const paths = textList(issue.location);
  if (paths.length) {
    sections.push(detailSection({ key: 'location', label: 'Location', body: h('ul', { class: 'dp-paths' }, paths.map((p) => h('li', {}, h('code', {}, p)))) }));
  }
  if (!sections.length) sections.push(h('p', { class: 'td-empty' }, 'Nothing written for this issue yet.'));
  return h('div', { class: 'td-body' }, [...sections, datesList([['Discovered', issueDiscovered(issue)], ['Resolved', issue.resolved]])]);
}

function page(issue, { agingCfg, timers }) {
  const head = detailHead({
    meta: detailMeta([
      copyId({ id: issue.id, noun: 'issue', timers }),
      h('a', { href: '#/issues' }, 'Issues'),
      stampEl(issueDiscovered(issue), { prefix: 'discovered' }),
    ]),
    title: detailTitle(issue.title),
    after: [markers(issue, agingCfg)],
  });
  const links = Array.isArray(issue.links) && issue.links.length ? issue.links : legacyLinksToTyped(issue, 'issue');
  const panels = links?.length
    ? [railPanel({ name: 'relations', label: 'Relations', children: [railGroup({ name: 'links', label: 'Links', body: linkPillsEl(links) })] })]
    : [];
  return [head, detailGrid({ body: body(issue), panels })];
}

export function mount(root, { params, store, prefs, subpath }) {
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

  function retry() {
    const loading = stateBlock({ headline: 'Loading…', busy: true });
    loading.setAttribute('tabindex', '-1');
    root.replaceChildren(loading);
    loading.focus();
    refocus = true;
    void load(true);
  }

  async function load(fetchFirst = false) {
    let issue = fetchFirst ? null : (store?.getIssues?.() || []).find((i) => i.id === id);
    if (!issue) {
      // Not cached: the cache is empty, or the issue was made after it was filled.
      try {
        const data = await api.getIssues({ includeResolved: true });
        if (disposed) return;
        const issues = data?.issues || [];
        store?.setIssues?.(issues);
        issue = issues.find((i) => i.id === id);
      } catch {
        if (disposed) return;
        root.replaceChildren(stateBlock({
          state: 'error', label: id, headline: 'Could not load this issue',
          hint: 'Something went wrong while loading it. Try again in a moment.', action: { label: 'Try again', onClick: retry },
        }));
        takeFocus(root.querySelector('.tm-empty button'));
        return;
      }
    }
    if (!issue) {
      root.replaceChildren(stateBlock({
        state: 'missing', label: id, headline: 'Issue not found', hint: 'It may have been resolved and archived, or renamed.', action: TO_ISSUES,
      }));
      takeFocus(root.querySelector('.tm-empty a[href]'));
      return;
    }
    root.replaceChildren(...page(issue, { agingCfg: store?.getPrefs?.()?.issues?.aging ?? {}, timers }));
    takeFocus(root.querySelector('h1'));
    // Only once painted: remembering an id that does not load would send a bare #/issue to a dead end.
    prefs?.patch?.({ ui: { last_issue_id: id } });
  }

  if (!id) {
    root.replaceChildren(stateBlock({ state: 'empty', label: 'Issue', headline: 'No issue open', hint: 'Pick one from the Issues board.', action: TO_ISSUES }));
  } else {
    // Said while the first read runs, so the page is never a blank mount.
    root.replaceChildren(stateBlock({ headline: 'Loading…', busy: true }));
    void load();
  }

  return () => {
    disposed = true;
    timers.forEach(clearTimeout);
    timers.clear();
    root.classList.remove(...ROOT_CLASSES);
  };
}
