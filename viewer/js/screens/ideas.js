// User intent: the Ideas list in the Reality Reprojection skin — link rows that select in place and deep-link,
// status chips + a Tags filter + a real Show archived toggle, "New idea" in row 1, a markdown detail pane, states in words.

import { api } from '../api.js';
import { claimTopbar, claimTopbarPrimary, setTopbarCount, tmSearch, tmAction } from '../lib/topbar.js';
import { keepFocus } from '../lib/keep-focus.js';
import { truncate } from '../lib/text.js';
import { formatStamp } from '../lib/time.js';
import { stateBlock } from '../components/empty-state.js';
import { chipRow, filterChip } from '../components/chips.js';
import { filterRail } from '../components/list-toolbar.js';
import { tagFilter, collectTags } from '../components/tag-filter.js';
import { linkRow } from '../components/link-row.js';
import { statusMarker } from '../components/status.js';
import { mountMarkdown } from '../components/markdown.js';
import { linkPillsEl, linkRoute, legacyLinksToTyped } from '../components/link-pills.js';
import { icon } from '../components/icon.js';
import { openIdeaCreateModal } from '../components/edit/idea-actions.js';
import { chipClickNext, CHIP_CLICK_HINT } from '../util/chip-toggle.js';
import { pluralize } from '../util/pluralize.js';
import { h } from '../util/h.js';
import { applyIdeasFilters, ideaStatusChips } from '../util/ideas-filter.js';

export { applyIdeasFilters };

export const meta = { title: 'Ideas', icon: '💡', sidebarKey: 'ideas' };

const PHONE = '(max-width: 768px)';
const isPhone = () => typeof matchMedia === 'function' && matchMedia(PHONE).matches;

export function mount(root, { store, subpath = [] } = {}) {
  let statuses = [];
  let includeArchived = false;
  let search = '';
  let failed = false;
  let loading = false;
  let loadSeq = 0;
  let alive = true;
  let selectedId = subpath?.[0] ? decodeURIComponent(subpath[0]) : null;

  root.replaceChildren();
  const screen = h('section', { class: 'ideas' });

  // ── Row 1: the primary action; Row 2: search only ──
  const newBtn = tmAction({
    icon: 'plus', label: 'New idea', variant: 'primary', title: 'Create a new idea',
    onClick: () => openIdeaCreateModal({ store, onCreated: loadIdeas }),
  });
  // claimTopbar() empties #topbar-primary too, so it goes first.
  const topbar = claimTopbar();
  claimTopbarPrimary()?.append(newBtn);
  const searchBuilt = tmSearch({ placeholder: 'Search ideas…', onInput: (v) => { search = v; paint(); } });
  const searchInput = searchBuilt.input;
  topbar?.append(searchBuilt.el);

  // ── The filter rail ──
  const all = () => store.getIdeas() || [];
  const admittedIdeas = () => all().filter((i) => includeArchived || !i.archived);
  const rail = filterRail({ onClear: clear });
  const statusRow = chipRow({
    label: 'Status', chips: ideaStatusChips([], statuses), hint: CHIP_CLICK_HINT,
    onToggle: (value, ev) => { statuses = chipClickNext(ev, statuses, value); paint(); },
  });
  const tags = tagFilter({ getTags: () => collectTags(admittedIdeas()), onChange: () => paint() });
  const archivedChip = filterChip({
    label: 'Show archived', value: 'archived', pressed: false, count: 0,
    onToggle: () => { includeArchived = !includeArchived; paint(); },
  });
  rail.add(statusRow.el, tags.el, archivedChip);

  const notice = h('div', { class: 'ideas__notice', role: 'status', hidden: true },
    h('span', {}, 'Could not refresh ideas — showing the list loaded earlier.'),
    h('button', { type: 'button', class: 'btn btn--ghost btn--sm', on: { click: () => loadIdeas() } }, 'Try again'));
  const list = h('ul', { class: 'ideas__list', 'aria-label': 'Ideas' });
  const stateHost = h('div', { class: 'ideas__state' });
  const pane = h('div', { class: 'ideas__pane' });
  const content = h('div', { class: 'ideas__content' }, list, pane);
  screen.append(rail.el, notice, stateHost, content);
  root.append(screen);

  function clear() {
    statuses = [];
    includeArchived = false;
    tags.clear();
    search = '';
    searchInput.value = '';
    paint();
  }

  function paint() {
    if (!alive) return;
    const spare = () => list.querySelector('a[href]') ?? stateHost.querySelector('button') ?? searchInput;
    const restore = [keepFocus(screen, { fallback: spare })];
    draw();
    for (const back of restore) back();
    tags.update();
  }

  function draw() {
    const cached = store.getIdeas();
    const ideas = cached && (cached.length || (!loading && !failed)) ? cached : null;   // an empty cache while loading (or failed) is no cache
    const tagSel = tags.selected();
    // Show archived widens the list, so it makes Clear available but is not a narrowing filter for the count.
    const narrowed = statuses.length > 0 || tagSel.length > 0 || search.trim() !== '';
    rail.setClearable(narrowed || includeArchived);
    notice.hidden = !(failed && ideas);
    const archivedCount = (ideas || []).filter((i) => i.archived).length;
    archivedChip.setAttribute('aria-pressed', String(includeArchived));
    archivedChip.disabled = archivedCount === 0 && !includeArchived;
    const countEl = archivedChip.querySelector('.chip__count');
    if (countEl) countEl.textContent = String(archivedCount);

    if (!ideas) {
      setTopbarCount('');
      statusRow.update(ideaStatusChips([], statuses));
      list.replaceChildren();
      showDetail(null);
      return showState(failed
        ? stateBlock({ state: 'error', label: 'Ideas', headline: 'Could not load ideas.', action: { label: 'Try again', onClick: loadIdeas } })
        : stateBlock({ state: 'loading', busy: true, headline: 'Loading ideas…' }));
    }
    const admitted = admittedIdeas();
    statusRow.update(ideaStatusChips(admitted, statuses));
    const shown = applyIdeasFilters(ideas, { statuses, tags: tagSel, includeArchived, search: search.trim() });
    const total = admitted.length;   // what the archived toggle admits (brief: 4 → 5 after a create)
    setTopbarCount(`${total} ${pluralize(total, 'idea', 'ideas')}${narrowed ? ` · ${shown.length} visible` : ''}`);
    list.replaceChildren(...shown.map(ideaRow));
    if (!ideas.length) showState(stateBlock({ label: 'Ideas', headline: 'No ideas yet.', hint: 'Use “New idea” to capture one.' }));
    else if (!total) showState(stateBlock({ label: 'Ideas', headline: 'Every idea is archived.', action: { label: 'Show archived', onClick: () => { includeArchived = true; paint(); } } }));
    else if (!shown.length) showState(stateBlock({ label: 'No matches', headline: 'No ideas match these filters.', action: { label: 'Clear filters', onClick: clear } }));
    else showState(null);
    list.hidden = !shown.length;
    showDetail(selectedId);
  }

  function showState(block) {
    stateHost.replaceChildren(...(block ? [block] : []));
    stateHost.hidden = !block;
  }

  // linkRow takes one Node as its name: the id and the cut title go in a fragment.
  function rowName(idea) {
    const f = document.createDocumentFragment();
    f.append(h('span', { class: 'idea-row__id' }, idea.id), truncate(idea.title || 'Untitled', { lines: 2, className: 'idea-row__title' }));
    return f;
  }

  function ideaRow(idea) {
    const content = [];
    if (idea.status) content.push(statusMarker('idea', idea.status));
    const t = idea.tags || [];
    if (t.length) {
      const wrap = h('span', { class: 'idea-row__tags' }, t.slice(0, 3).map((x) => h('span', { class: 'list-tag' }, x)));
      if (t.length > 3) wrap.append(h('span', { class: 'idea-row__more', title: t.slice(3).join(', ') }, `+${t.length - 3}`));
      content.push(wrap);
    }
    if (idea.archived) content.push(h('span', { class: 'list-tag' }, 'Archived'));
    const age = formatStamp(idea.created);
    content.push(h('time', { class: 'idea-row__age', datetime: idea.created || '', title: age.title || '' }, age.text || ''));
    const row = linkRow({
      tag: 'li',
      className: 'idea-row' + (idea.archived ? ' idea-row--archived' : ''),
      href: linkRoute(idea.id),
      name: rowName(idea),
      content,
    });
    const a = row.querySelector('a[href]');
    a.dataset.id = idea.id;
    if (idea.id === selectedId) a.setAttribute('aria-current', 'true');
    a.addEventListener('click', (ev) => {
      if (ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
      ev.preventDefault();
      select(idea.id);
    });
    return row;
  }

  function rowLink(id) {
    return [...list.querySelectorAll('a[data-id]')].find((a) => a.dataset.id === id) ?? null;
  }

  function select(id) {
    selectedId = id;
    history.replaceState(history.state, '', `#/ideas/${encodeURIComponent(id)}`);
    for (const a of list.querySelectorAll('a[aria-current]')) a.removeAttribute('aria-current');
    rowLink(id)?.setAttribute('aria-current', 'true');
    showDetail(id);
    if (isPhone()) pane.querySelector('h2')?.focus();
  }

  function deselect() {
    const id = selectedId;
    selectedId = null;
    history.replaceState(history.state, '', '#/ideas');
    rowLink(id)?.removeAttribute('aria-current');
    showDetail(null);
    rowLink(id)?.focus();
  }

  function showDetail(id) {
    const ideas = store.getIdeas();
    content.classList.toggle('ideas__content--detail-open', !!id && !!ideas);
    if (!id || !ideas) { pane.replaceChildren(); pane.hidden = true; return; }
    pane.hidden = false;
    const idea = ideas.find((i) => i.id === id);
    pane.replaceChildren(idea ? detail(idea)
      : stateBlock({ state: 'missing', label: 'Not found', headline: `${id} is not in this project.` }));
  }

  function detail(idea) {
    const headId = `ideas-detail-title-${idea.id}`;
    // data-focus keys let keepFocus find these again after a redraw rebuilds the pane (the list fallback is hidden on phones).
    const back = h('button', { type: 'button', class: 'btn btn--ghost btn--sm ideas-detail__back', 'data-focus': 'ideas-detail-back', on: { click: deselect } },
      icon('chevron', { size: 14 }), h('span', {}, 'Back to ideas'));
    const tech = h('div', { class: 'ideas-detail__tech' }, h('span', { class: 'ideas-detail__id' }, idea.id));
    if (idea.status) tech.append(statusMarker('idea', idea.status));
    if (idea.archived) tech.append(h('span', { class: 'list-tag' }, 'Archived'));
    const title = h('h2', { class: 'ideas-detail__title', id: headId, tabindex: '-1', 'data-focus': 'ideas-detail-title' }, idea.title || 'Untitled');

    const main = h('div', { class: 'ideas-detail__main' });
    main.append(idea.body ? mountMarkdownInto(idea.body) : h('p', { class: 'ideas-detail__empty' }, 'No description.'));

    const dl = h('dl', { class: 'ideas-detail__dl' });
    const term = (label, value) => { if (value) dl.append(h('dt', {}, label), h('dd', {}, value)); };
    const stamp = (v) => { if (!v) return null; const s = formatStamp(v); return h('span', { title: s.title || '' }, s.text || ''); };
    term('Created', stamp(idea.created));
    term('Updated', stamp(idea.updated));
    term('Status', idea.status && statusMarker('idea', idea.status));
    term('Tags', (idea.tags || []).length && h('span', { class: 'ideas-detail__tags' }, idea.tags.map((x) => h('span', { class: 'list-tag' }, x))));
    const side = h('div', { class: 'ideas-detail__side' }, dl);
    const links = idea.links?.length ? idea.links : legacyLinksToTyped(idea, 'idea');
    if (links.length) side.append(h('h3', { class: 'ideas-detail__h' }, 'Links'), linkPillsEl(links));
    if (idea.promoted_to) {
      side.append(h('h3', { class: 'ideas-detail__h' }, 'Promoted to'),
        h('a', { class: 'link-pill', href: linkRoute(idea.promoted_to) }, idea.promoted_to));
    }
    return h('section', { class: 'ideas-detail', 'aria-labelledby': headId },
      back, tech, title, h('div', { class: 'ideas-detail__grid' }, main, side));
  }

  function mountMarkdownInto(src) {
    const el = h('div', { class: 'ideas-detail__body md' });
    mountMarkdown(el, src);
    return el;
  }

  function loadIdeas() {
    const seq = ++loadSeq;
    failed = false;
    loading = true;
    paint();
    return api.get('/api/ideas?archived=true&summary=false').then((data) => {
      if (!alive || seq !== loadSeq) return;
      loading = false;
      store.setIdeas(data?.ideas ?? (Array.isArray(data) ? data : []));   // the subscription repaints
    }, (e) => {
      if (!alive || seq !== loadSeq) return;
      loading = false;
      if (e?.code === 404) { store.setIdeas([]); return; }
      console.error('ideas load failed', e);
      failed = true;
      paint();
    });
  }

  const unsubscribe = store.subscribe?.('ideas', paint);
  loadIdeas();

  return () => {
    alive = false;
    unsubscribe?.();
    statusRow.destroy?.();
    rail.el.remove();   // removing the Tags anchor closes its popover
    screen.remove();
  };
}
