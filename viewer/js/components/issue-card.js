// User intent: an issue in a list is a card that opens the issue — its severity, what it blocks, whether it has gone
// stale, where it is and the evidence (three lines, with a real "Show all") — and a resolved issue is a quiet row;
// the task and bug links sit beside the card's link, never inside it, and no issue text is ever parsed as markup (the preview strips its marks).
import { linkRow } from './link-row.js';
import { severityMarker, statusMarker } from './status.js';
import { staleTag } from './stale-tag.js';
import { truncate } from '../lib/text.js';
import { formatStamp } from '../lib/time.js';
import { pluralize } from '../util/pluralize.js';
import { computeBlocksCount } from '../util/issue-blocks.js';
import { issueEvidence, plainMarkdown } from '../util/issue-fields.js';

const issueHref = (id) => `#/issue/${encodeURIComponent(id)}`;

function span(className, text) {
  const el = document.createElement('span');
  el.className = className;
  if (text != null) el.textContent = text;
  return el;
}

const list = (value) => (Array.isArray(value) ? value.filter(Boolean) : []);

function locationText(location) {
  if (Array.isArray(location)) return location.filter(Boolean).join(', ');
  return typeof location === 'string' ? location : '';
}

function refsBlock(issue) {
  const groups = [
    ['Tasks', list(issue.related_tasks), (id) => `#/task/${encodeURIComponent(id)}`],
    ['From bugs', list(issue.promoted_from), (id) => `#/bug/${encodeURIComponent(id)}`],
  ].filter(([, ids]) => ids.length);
  if (!groups.length) return null;
  const refs = document.createElement('div');
  refs.className = 'issue-card__refs';
  for (const [label, ids, href] of groups) {
    refs.append(span('issue-card__refs-label', label));
    for (const id of ids) {
      const a = document.createElement('a');
      a.className = 'issue-card__ref';
      a.setAttribute('href', href(id));
      a.dataset.focus = `ref:${issue.id}:${id}`;
      a.textContent = id;
      refs.append(a);
    }
  }
  return refs;
}

// The clamp is CSS, so only layout knows whether three lines cut anything; the toggle stays hidden until the evidence,
// in the page, overflows. One frame after creation covers a card appended at once; the ResizeObserver covers one
// appended later, one in a hidden phone column (laid out only when shown) and a resize that starts cutting it. The
// observer lets go once the toggle shows, or once it sees the card out of the page after having seen it in (removal
// reports a size change). A not-yet-appended card is kept watched: a browser's first observation can come before the
// screen appends it. A card never appended is collected together with its observer.
function revealWhenCut(card, evidence, toggle) {
  let observer = null;
  let wasIn = false;
  const check = () => {
    if (card.isConnected) wasIn = true;
    if (toggle.hidden && card.isConnected && evidence.scrollHeight > evidence.clientHeight + 1) toggle.hidden = false;
    if (!toggle.hidden || (wasIn && !card.isConnected)) observer?.disconnect();
  };
  const raf = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : (fn) => setTimeout(fn, 16);
  raf(() => { if (card.isConnected) check(); });
  if (typeof ResizeObserver === 'function') {
    observer = new ResizeObserver(check);
    observer.observe(evidence);
  }
}

// aria-controls needs an id with no spaces that no other card shares, even when two cards show the same issue.
let evidenceSeq = 0;
const evidenceId = (id) => `issue-evidence-${String(id ?? '').replace(/[^A-Za-z0-9_-]+/g, '-')}-${++evidenceSeq}`;

/**
 * An open or investigating issue as a link-row card. The screen owns which cards are expanded: `onToggleEvidence(id)`
 * asks it to flip one and redraw. `showStatus` adds the status marker (for views that mix statuses in one list).
 * `revealed`: the screen saw this card's evidence cut when it last drew it, so "Show all" shows at once — a redraw then
 * neither blinks it out for a frame nor loses the focus that was on it.
 */
export function issueCard(issue, { tasksIndex = {}, agingCfg = {}, expanded = false, revealed = false, onToggleEvidence,
  showStatus = false, now = Date.now() } = {}) {
  const name = span('issue-card__name');
  const line = span('issue-card__line');
  line.append(span('issue-card__id', issue.id));
  const sev = severityMarker(issue.severity_label ?? issue.severity);
  if (sev) line.append(sev);
  name.append(line, span('issue-card__title', issue.title ?? ''));

  const content = [];
  const meta = document.createElement('div');
  meta.className = 'issue-card__meta';
  if (showStatus) meta.append(statusMarker('issue', issue.status));
  const blocks = computeBlocksCount(issue, tasksIndex ?? {});
  if (blocks > 0) meta.append(span('issue-card__blocks', `Blocks ${blocks} ${pluralize(blocks, 'task', 'tasks')}`));
  const stale = staleTag(issue, agingCfg ?? {}, now);
  if (stale) meta.append(stale);
  const where = locationText(issue.location);
  if (where) meta.append(truncate(where, { className: 'issue-card__location' }));
  if (meta.childNodes.length) content.push(meta);

  const controls = [];
  const text = plainMarkdown(issueEvidence(issue));
  let evidence = null;
  let toggle = null;
  if (text) {
    evidence = truncate(text, { lines: 3, tag: 'p', className: 'issue-card__evidence' });
    evidence.id = evidenceId(issue.id);
    // Both classes: `.truncate` alone is the one-line cut, so an expanded card showing all of it drops it too.
    if (expanded) evidence.classList.remove('truncate', 'truncate--3');
    content.push(evidence);

    toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'btn btn--ghost btn--sm issue-card__more';
    toggle.setAttribute('aria-controls', evidence.id);
    toggle.setAttribute('aria-expanded', String(!!expanded));
    toggle.dataset.focus = `evidence:${issue.id}`;
    toggle.textContent = expanded ? 'Show less' : 'Show all';
    toggle.hidden = !expanded && !revealed;
    toggle.addEventListener('click', () => onToggleEvidence?.(issue.id));
    controls.push(toggle);
  }
  controls.push(refsBlock(issue));

  const card = linkRow({ tag: 'article', className: 'issue-card', href: issueHref(issue.id), name, content, controls });
  card.dataset.issueId = issue.id;
  card.dataset.status = issue.status || 'open';
  if (toggle?.hidden) revealWhenCut(card, evidence, toggle);
  return card;
}

/**
 * A resolved (fixed, won't-fix, duplicate) issue as a one-line link row: id, severity, title, status, when.
 * `narrow` is for a place as narrow as a phone (a board column): the title takes its own line, status and date wrap.
 */
export function issueRow(issue, { now = Date.now(), narrow = false } = {}) {
  const name = document.createDocumentFragment();
  name.append(span('issue-row__id', issue.id));
  const sev = severityMarker(issue.severity_label ?? issue.severity);
  if (sev) name.append(sev);
  const title = truncate(issue.title ?? '', { className: 'issue-row__title' });
  name.append(title);

  const content = [statusMarker('issue', issue.status)];
  const stamp = issue.resolved ?? issue.updated;
  let when = null;
  if (stamp) {
    const { text, title: full } = formatStamp(stamp, now);
    when = document.createElement('time');
    when.className = 'issue-row__when';
    when.dateTime = String(stamp);
    when.textContent = text;
    if (full) when.title = full;
    content.push(when);
  }

  // The name is a fragment so its three parts are the link's own grid items; linkRow cannot read titles out of a
  // fragment it has already emptied, so the link's title is gathered here the way linkRow would.
  const linkTitle = [title.title, when?.title].filter(Boolean).join('\n');
  const row = linkRow({ tag: 'div', className: narrow ? 'issue-row issue-row--narrow' : 'issue-row', href: issueHref(issue.id), name, content, title: linkTitle });
  row.dataset.issueId = issue.id;
  row.dataset.status = issue.status || '';
  return row;
}

export default issueCard;
