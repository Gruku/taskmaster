// User intent: the Issues board — columns that never collapse and scroll inside themselves, one column at a time behind
// tabs on a phone, severity and component chips in the page's filter rail, a labelled View switcher, a real resolved
// shelf, and a list that is read again on every visit and keeps the user's focus when the board changes under it.
import { issueCard, issueRow } from '../components/issue-card.js';
import { columnTabs } from '../components/column-tabs.js';
import { stateBlock } from '../components/empty-state.js';
import { chipRow, filterChip } from '../components/chips.js';
import { filterRail, labelled } from '../components/list-toolbar.js';
import { icon } from '../components/icon.js';
import { ISSUE_STATUS, SEVERITY } from '../components/status.js';
import * as api from '../api.js';
import { claimTopbar, setTopbarCount, tmSearch, tmSegmented } from '../lib/topbar.js';
import { keepFocus } from '../lib/keep-focus.js';
import { chipClickNext, CHIP_CLICK_HINT } from '../util/chip-toggle.js';
import { pluralize } from '../util/pluralize.js';
import { h } from '../util/h.js';
import { groupByStatus, groupBySeverity } from '../util/issues-grouping.js';
import { filterIssues, issueSeverity } from '../util/issues-filter.js';

export const meta = { title: 'Issues', icon: '!', sidebarKey: 'issues' };

const VIEWS = [{ key: 'A', label: 'Hybrid' }, { key: 'B', label: 'Status' }, { key: 'D', label: 'Severity' }, { key: 'C', label: 'List' }];
const PHONE = '(max-width: 768px)';

const statusLabel = (key) => ISSUE_STATUS[key].label;

// The columns a view shows, from the issues the filters let through; `all` decides whether Duplicate has a column.
function columnsFor(view, shown, all) {
  const status = groupByStatus(shown);
  const col = (key, label, items, kind = 'card') => ({ key, label, items, kind });
  if (view === 'B') {
    const cols = [col('open', statusLabel('open'), status.open), col('investigating', statusLabel('investigating'), status.investigating),
      col('fixed', statusLabel('fixed'), status.fixed, 'row'), col('wontfix', statusLabel('wontfix'), status.wontfix, 'row')];
    if (all.some((i) => i?.status === 'duplicate')) cols.push(col('duplicate', statusLabel('duplicate'), status.duplicate, 'row'));
    return cols;
  }
  if (view === 'D') {
    const bySeverity = groupBySeverity([...status.investigating, ...status.open]);
    return Object.keys(SEVERITY).map((key) => col(key, SEVERITY[key].label, bySeverity[key]));
  }
  if (view === 'C') return [col('active', 'Open and investigating', [...status.investigating, ...status.open])];
  return [col('investigating', statusLabel('investigating'), status.investigating), col('open', statusLabel('open'), status.open)];
}

export function mount(root, { store, prefs }) {
  // `prefs` is the patch helper; what was saved is read from the store.
  const saved = store.getPrefs()?.screens?.issues ?? {};
  let view = VIEWS.some((v) => v.key === saved.view) ? saved.view : 'A';
  let promotedOnly = saved.promotedFromBug === true;
  let severities = [];
  let components = [];
  let search = '';
  let failed = false;
  let alive = true;
  let shelfOpen = false;
  const expandedIds = new Set();
  const selectedByView = {};   // the phone tab picked in each view, for as long as the screen is mounted

  root.replaceChildren();
  const screen = h('section', { class: 'issues' });

  // ── Row 2: search and the one view control ──
  const topbar = claimTopbar();
  const searchBuilt = tmSearch({ placeholder: 'Search issues…', onInput: (v) => { search = v; paint(); } });
  const searchInput = searchBuilt.input;
  const viewSwitch = tmSegmented(VIEWS, { value: view, onChange: setView });
  topbar?.append(searchBuilt.el, labelled({ label: 'View', control: viewSwitch }));

  // ── The filter rail: Severity, Component, From a bug, Clear ──
  const rail = filterRail({ onClear: clear });
  const severityRow = chipRow({
    label: 'Severity', chips: severityChips([]), hint: CHIP_CLICK_HINT,
    onToggle: (value, ev) => { severities = chipClickNext(ev, severities, value); paint(); },
  });
  const componentRow = chipRow({
    label: 'Component', chips: [], hint: CHIP_CLICK_HINT,
    onToggle: (value, ev) => { components = chipClickNext(ev, components, value); paint(); },
  });
  componentRow.el.dataset.grow = '';
  const promotedChip = filterChip({
    label: 'From a bug', value: 'promoted', pressed: promotedOnly, count: 0, title: 'Only issues promoted from a bug',
    onToggle: () => { promotedOnly = !promotedOnly; persist({ promotedFromBug: promotedOnly }); paint(); },
  });
  rail.add(severityRow.el, componentRow.el, promotedChip);

  const notice = h('div', { class: 'issues__notice', role: 'status' });
  notice.hidden = true;
  const stateHost = h('div', { class: 'issues__state' });

  // ── The board: phone tabs, then the columns, which scroll sideways inside themselves ──
  const tabs = columnTabs({ label: 'Issue columns', columns: [], selected: null, onSelect: (key) => {
    selectedByView[view] = key;
    paintPanels();
  } });
  const colsEl = h('div', { class: 'issues-board__cols' });
  const board = h('div', { class: 'issues-board' }, tabs.el, colsEl);
  const sections = new Map();   // column key → its section, reused across paints and views
  let shownCols = [];
  let drawnView = null;

  // ── The resolved shelf ──
  const shelfText = h('span', { class: 'issues-shelf__label' });
  const shelfToggle = h('button', { type: 'button', class: 'issues-shelf__toggle', 'aria-expanded': 'false',
    'aria-controls': 'issues-shelf-list', 'data-focus': 'issues-shelf' }, icon('chevron', { size: 14 }), shelfText);
  const shelfList = h('div', { id: 'issues-shelf-list' });
  shelfList.hidden = true;
  shelfToggle.addEventListener('click', () => {
    shelfOpen = !shelfOpen;
    shelfToggle.setAttribute('aria-expanded', String(shelfOpen));
    shelfList.hidden = !shelfOpen;
  });
  const shelf = h('section', { class: 'issues-shelf' }, h('h2', { class: 'issues-shelf__head' }, shelfToggle), shelfList);

  screen.append(rail.el, notice, stateHost, board, shelf);
  root.append(screen);

  const mq = typeof matchMedia === 'function' ? matchMedia(PHONE) : null;
  const onMedia = () => paint();
  mq?.addEventListener?.('change', onMedia);

  function persist(patch) {
    prefs?.patch?.({ screens: { issues: patch } });
  }

  function setView(next) {
    view = next;
    persist({ view });
    paint();
  }

  function clear() {
    severities = [];
    components = [];
    if (promotedOnly) { promotedOnly = false; persist({ promotedFromBug: false }); }
    search = '';
    searchInput.value = '';
    searchInput.dispatchEvent(new Event('input', { bubbles: true }));
    paint();
  }

  // Only the latest request is applied: a slower earlier reply never overwrites a newer list or says it failed.
  let loadSeq = 0;
  function load() {
    const seq = ++loadSeq;
    failed = false;
    paint();
    api.getIssues({ includeResolved: true }).then((data) => {
      if (!alive || seq !== loadSeq) return;
      store.setIssues(data?.issues ?? []);   // the subscription repaints
    }, (e) => {
      if (!alive || seq !== loadSeq) return;
      console.error('issues load failed', e);
      failed = true;
      paint();
    });
  }

  // A pressed value no issue has any more is still offered, at 0, so it can be released.
  function severityChips(issues) {
    const counts = new Map();
    for (const i of issues) {
      const key = issueSeverity(i);
      if (key) counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return Object.keys(SEVERITY).map((key) => ({ value: key, label: SEVERITY[key].label, count: counts.get(key) ?? 0,
      pressed: severities.includes(key) }));
  }

  function componentChips(issues) {
    const counts = new Map();
    for (const i of issues) if (i?.component) counts.set(i.component, (counts.get(i.component) ?? 0) + 1);
    for (const c of components) if (!counts.has(c)) counts.set(c, 0);
    return [...counts.keys()].sort().map((c) => ({ value: c, label: c, count: counts.get(c), pressed: components.includes(c) }));
  }

  // The chip's own paint is private to chips.js; these are the parts a new count or toggle changes.
  function paintPromotedChip(count) {
    promotedChip.setAttribute('aria-pressed', String(promotedOnly));
    promotedChip.disabled = count === 0 && !promotedOnly;
    promotedChip.querySelector('.chip__count').textContent = String(count);
  }

  function paintRail(issues) {
    rail.setClearable(severities.length > 0 || components.length > 0 || promotedOnly || search.trim() !== '');
    severityRow.update(severityChips(issues));
    const comps = componentChips(issues);
    componentRow.el.hidden = comps.length === 0;
    componentRow.update(comps);
    paintPromotedChip(issues.filter((i) => Array.isArray(i?.promoted_from) && i.promoted_from.length > 0).length);
  }

  function showState(block) {
    board.hidden = true;
    shelf.hidden = true;
    stateHost.hidden = false;
    stateHost.replaceChildren(block);
  }

  function section(key) {
    let s = sections.get(key);
    if (s) return s;
    const panelId = `issues-col-${key}`;
    const name = h('span', { class: 'issues-col__name' });
    const count = h('span', { class: 'issues-col__count' });
    const head = h('h2', { class: 'issues-col__head', id: `${panelId}-head` }, name, count);
    const list = h('div', { class: 'issues-col__list' });
    // The browser scrolls the focused link, not the card around it; the whole card (its ring) must clear the column's fade. Phone lists do not scroll themselves, so they are left to the page.
    list.addEventListener('focusin', (e) => { if (list.scrollHeight > list.clientHeight && e.target.matches?.(':focus-visible')) e.target.closest?.('.issue-card')?.scrollIntoView({ block: 'nearest', inline: 'nearest' }); });
    s = { key, el: h('section', { class: 'issues-col', id: panelId }, head, list), head, name, count, list };
    sections.set(key, s);
    return s;
  }

  // The panel contract of columnTabs: tab panels only on a phone with two or more columns, else each column is
  // labelled by its own head and shows.
  function paintPanels() {
    const phone = !!mq?.matches && shownCols.length >= 2;
    const keys = shownCols.map((c) => c.key);
    const selected = keys.includes(selectedByView[view]) ? selectedByView[view] : keys[0];
    for (const c of shownCols) {
      const s = sections.get(c.key);
      if (phone) {
        s.el.setAttribute('role', 'tabpanel');
        s.el.setAttribute('aria-labelledby', `${s.el.id}-tab`);
        s.el.hidden = c.key !== selected;
      } else {
        s.el.removeAttribute('role');
        s.el.setAttribute('aria-labelledby', s.head.id);
        s.el.hidden = false;
      }
    }
    return selected;
  }

  function drawBoard(shown, all) {
    const tasksIndex = Object.fromEntries((store.getBacklog()?.tasks ?? []).map((t) => [t.id, t]));
    const agingCfg = store.getPrefs()?.issues?.aging ?? {};
    const showStatus = view === 'D' || view === 'C';
    const onToggleEvidence = (id) => {
      if (expandedIds.has(id)) expandedIds.delete(id);
      else expandedIds.add(id);
      paint();
    };
    // Evidence already measured as cut keeps its "Show all" through a redraw of the same view, so focus on it can be put
    // back; another view lays its cards out at another width and measures them afresh.
    const revealed = new Set(view !== drawnView ? [] : [...colsEl.querySelectorAll('.issue-card__more:not([hidden])')]
      .map((b) => b.closest('.issue-card')?.dataset.issueId));
    drawnView = view;
    shownCols = columnsFor(view, shown, all);
    for (const c of shownCols) {
      const s = section(c.key);
      s.name.textContent = c.label;
      s.count.textContent = String(c.items.length);
      s.list.replaceChildren(...(c.items.length
        ? c.items.map((i) => (c.kind === 'row' ? issueRow(i, { narrow: true }) : issueCard(i, {
          tasksIndex, agingCfg, expanded: expandedIds.has(i.id), revealed: revealed.has(i.id), onToggleEvidence, showStatus,
        })))
        : [h('p', { class: 'issues-col__empty' }, 'None')]));
    }
    // Move sections only when the view's set or order changed: a moved node drops its focus, and the sideways scroll
    // of the columns must survive a poll.
    const want = shownCols.map((c) => sections.get(c.key).el);
    const have = [...colsEl.children];
    if (want.length !== have.length || want.some((el, i) => el !== have[i])) {
      const x = colsEl.scrollLeft;
      colsEl.replaceChildren(...want);
      colsEl.scrollLeft = x;
    }
    const selected = paintPanels();
    for (const c of shownCols) {
      const { list } = sections.get(c.key);
      if (!list.dataset.edge) { list.dataset.edge = '1'; list.addEventListener('scroll', markEdges, { passive: true }); edgeObserver?.observe(list); }
    }
    requestAnimationFrame(markEdges);
    tabs.update({
      columns: shownCols.map((c) => ({ key: c.key, label: c.label, count: c.items.length, panelId: `issues-col-${c.key}` })),
      selected,
    });
  }

  // A cue for what is cut: data-more marks a scroller with content past its far edge, and the CSS fades that edge.
  const edgeOf = (el, vertical) => (vertical ? el.scrollTop + el.clientHeight < el.scrollHeight - 1
    : el.scrollLeft + el.clientWidth < el.scrollWidth - 1);
  function markEdges() {
    colsEl.toggleAttribute('data-more', edgeOf(colsEl, false));
    for (const s of sections.values()) s.list.toggleAttribute('data-more', edgeOf(s.list, true));
  }
  colsEl.addEventListener('scroll', markEdges, { passive: true });
  const edgeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(markEdges) : null;
  edgeObserver?.observe(colsEl);

  function drawShelf(shown) {
    if (view === 'B') { shelf.hidden = true; return; }
    const status = groupByStatus(shown);
    const resolved = [...status.fixed, ...status.wontfix, ...status.duplicate];
    shelfText.textContent = `Resolved · ${resolved.length} ${pluralize(resolved.length, 'issue', 'issues')}`;
    shelfList.replaceChildren(...resolved.map((i) => issueRow(i)));
    shelf.hidden = resolved.length === 0;
  }

  function draw() {
    const all = store.getIssues();
    const issues = Array.isArray(all) ? all : null;
    paintRail(issues ?? []);
    notice.hidden = !(failed && issues);
    if (!issues) {
      setTopbarCount('');
      showState(failed
        ? stateBlock({ state: 'error', label: 'Issues', headline: 'Could not load issues.', action: { label: 'Try again', onClick: load } })
        : stateBlock({ state: 'loading', busy: true, headline: 'Loading issues…' }));
      return;
    }
    const narrowed = severities.length > 0 || components.length > 0 || promotedOnly || search.trim() !== '';
    const shown = filterIssues(issues, { search, severities, components, promotedOnly });
    setTopbarCount(`${issues.length} ${pluralize(issues.length, 'issue', 'issues')}${narrowed && shown.length < issues.length ? ` · ${shown.length} visible` : ''}`);
    if (!issues.length) return showState(stateBlock({ label: 'Issues', headline: 'No issues recorded yet.' }));
    if (!shown.length) {
      return showState(stateBlock({ label: 'No matches', headline: 'No issues match these filters.', action: { label: 'Clear filters', onClick: clear } }));
    }
    stateHost.replaceChildren();
    stateHost.hidden = true;
    board.hidden = false;
    drawBoard(shown, issues);
    drawShelf(shown);
  }

  // Focus in the screen is put back on the same control in the fresh DOM, or handed on, never dropped to <body>.
  function paint() {
    // A debounced search (or Clear's own input event) can fire after the screen is gone; the topbar is the next screen's.
    if (!alive) return;
    const back = keepFocus(screen, {
      fallback: () => screen.querySelector('.issues-col:not([hidden]) a[href]') ?? stateHost.querySelector('button') ?? searchInput,
    });
    draw();
    back();
  }

  notice.append(h('span', {}, 'Could not refresh issues — showing the list loaded earlier.'),
    h('button', { type: 'button', class: 'btn btn--secondary btn--sm', on: { click: load } }, 'Try again'));

  const unsubscribe = [store.subscribe('issues', paint), store.subscribe('backlog', paint)];
  load();

  return () => {
    alive = false;
    for (const off of unsubscribe) off();
    mq?.removeEventListener?.('change', onMedia);
    edgeObserver?.disconnect();
    tabs.destroy();
    severityRow.destroy();
    componentRow.destroy();
  };
}
