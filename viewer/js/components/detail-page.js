// User intent: one detail template for tasks, issues and bugs — a quiet Technical meta line, the title as the page's
// only h1, a row of markers, labelled sections of rendered text and a rail of what the record relates to — so the three
// pages read as one, in both themes and at phone width.
import { h } from '../util/h.js';
import { renderMarkdown } from './markdown.js';
import { formatStamp } from '../lib/time.js';
import { copyToClipboard } from '../lib/copy.js';
import { icon } from './icon.js';

const COPIED_MS = 1500;

// The meta line: parts in order (null, false and '' dropped; a string becomes a span), a '·' between them that screen
// readers skip.
export function detailMeta(parts, { test = 'meta' } = {}) {
  const line = h('div', { class: 'td-meta', 'data-test': test });
  parts.filter((p) => p != null && p !== false && p !== '').forEach((part, i) => {
    if (i) line.appendChild(h('span', { class: 'td-sep', 'aria-hidden': 'true' }, '·'));
    line.appendChild(typeof part === 'string' ? h('span', {}, part) : part);
  });
  return line;
}

// A date a person reads, the exact instant in its title; null when the value is not a date.
export function stampEl(iso, { prefix = '' } = {}) {
  const stamp = formatStamp(typeof iso === 'string' ? iso : null);
  if (!stamp.title) return null;
  const time = h('time', { datetime: iso, title: stamp.title }, stamp.text);
  return prefix ? h('span', { class: 'td-meta__created' }, `${prefix} `, time) : time;
}

// A button that copies `value` and says so in a live region. `timers` (a Set) collects its reset timer for the caller
// to clear on unmount. `tag` names the marker-row fact the button is (a branch, a worktree).
export function copyButton({ value, label, children = [], className = '', focus, test, tag, timers }) {
  const status = h('span', { class: 'td-copy__status', role: 'status' });
  const glyph = h('span', { class: 'td-copy__icon' }, icon('copy', { size: 14 }));
  const btn = h('button', {
    type: 'button', class: `td-copy ${className}`.trim(), 'data-tag': tag, 'data-test': test, 'data-focus': focus, 'aria-label': label,
  }, [...children, glyph, status]);
  let timer;
  btn.addEventListener('click', async () => {
    const ok = await copyToClipboard(value);
    // Said in words, in a live region: the change is not carried by colour alone.
    status.textContent = ok ? 'Copied' : 'Copy failed';
    glyph.replaceChildren(icon(ok ? 'check' : 'alert', { size: 14 }));
    clearTimeout(timer);
    timers?.delete(timer);
    timer = setTimeout(() => { status.textContent = ''; glyph.replaceChildren(icon('copy', { size: 14 })); }, COPIED_MS);
    timers?.add(timer);
  });
  return btn;
}

// The id as a copy button, the way every detail page opens its meta line.
export function copyId({ id, noun, timers }) {
  return copyButton({
    value: id, label: `Copy ${noun} id`, className: 'td-id', focus: 'copy:id', test: `${noun}-id`, timers,
    children: [h('span', { class: 'td-id-text' }, id || '—')],
  });
}

export function detailTitle(text) {
  return h('h1', { class: 'td-title', 'data-test': 'title' }, text || '(untitled)');
}

export function detailHead({ meta, title, after = [] }) {
  return h('header', { class: 'td-head' }, [meta, title, ...after]);
}

// A secondary fact in the marker row: a Technical key and its value.
export function detailTag(name, label, value) {
  return h('span', { class: 'td-tag', 'data-tag': name },
    [h('span', { class: 'td-tag__k' }, label), h('span', { class: 'td-tag__v' }, value)]);
}

export function sectionHeading(label, level = 2, extra = null) {
  return h('div', { class: 'td-section-head' }, [h(`h${level}`, { class: 'td-section-h' }, label), extra]);
}

export function markdownBody(source) {
  const el = h('div', { class: 'md-body' });
  // renderMarkdown sanitises; it is the only path record text takes into innerHTML.
  el.innerHTML = renderMarkdown(typeof source === 'string' ? source : '');
  return el;
}

export function detailSection({ key, label, level = 2, body, extra = null, test }) {
  return h('section', { class: 'td-section', 'data-section': key, 'data-test': test ?? `sec-${key}` },
    [sectionHeading(label, level, extra), body]);
}

// Labelled dates; null when none of them is a date.
export function datesList(cells) {
  const items = cells.map(([label, iso]) => [label, stampEl(iso)]).filter(([, el]) => el);
  if (!items.length) return null;
  return h('dl', { class: 'td-dates', 'data-test': 'dates' },
    items.map(([label, el]) => h('div', { class: 'td-date' }, [h('dt', {}, label), h('dd', {}, el)])));
}

// Body and rail; with no panels there is no rail and the body takes the width.
export function detailGrid({ body, panels = [], railLabel = 'Related' }) {
  const rail = panels.length ? h('aside', { class: 'td-rail', 'data-test': 'rail', 'aria-label': railLabel }, panels) : null;
  return h('div', { class: `td-grid${rail ? '' : ' td-grid--solo'}` }, [body, rail]);
}

// The same markup as right-rail.js's task panels, for rails that are not a task's.
export function railPanel({ name, label, level = 2, children = [] }) {
  return h('section', { class: `td-panel td-panel-${name}`, 'data-panel': name },
    [h(`h${level}`, { class: 'td-rail-h' }, label), ...children]);
}

export function railGroup({ name, label, level = 2, body }) {
  return h('div', { class: 'td-rail-group', 'data-sub': name }, [h(`h${level + 1}`, { class: 'td-rail-sub' }, label), body]);
}
