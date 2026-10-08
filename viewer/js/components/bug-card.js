// User intent: a bug in the list is a row that opens the bug — its id, severity, a title that keeps two lines, its status,
// components and age — with "found in" beside the link opening the task, never nested in it; no bug text is markup.
import { linkRow } from './link-row.js';
import { severityMarker, statusMarker } from './status.js';
import { truncate } from '../lib/text.js';
import { formatStamp } from '../lib/time.js';
import { isArchivedBug } from '../util/bugs-filter.js';

function span(className, text) {
  const el = document.createElement('span');
  el.className = className;
  if (text != null) el.textContent = text;
  return el;
}

export function bugRow(bug, { now = Date.now() } = {}) {
  const archived = isArchivedBug(bug);

  const name = span('bug-row__name');
  // The severity cell stays when unset, empty and hidden, so every title starts in the same column.
  const sev = span('bug-row__severity');
  const marker = severityMarker(bug.severity);
  if (marker) sev.append(marker);
  else sev.setAttribute('aria-hidden', 'true');
  name.append(span('bug-row__id', bug.id), sev, truncate(bug.title || 'Untitled', { lines: 2, className: 'bug-row__title' }));

  // One cell for the status and its Archived tag, so the status column is the same column on every row.
  const status = span('bug-row__status');
  status.append(statusMarker('bug', bug.status || 'open'));
  if (archived) status.append(span('list-tag', 'Archived'));
  const content = [status];
  const components = Array.isArray(bug.components) ? bug.components.filter(Boolean) : [];
  if (components.length) content.push(truncate(components.join(', '), { className: 'bug-row__components' }));
  if (bug.discovered) {
    const { text, title } = formatStamp(bug.discovered, now);
    const age = document.createElement('time');
    age.className = 'bug-row__age';
    age.setAttribute('datetime', String(bug.discovered));
    age.textContent = text;
    if (title) age.title = title;
    content.push(age);
  }

  const controls = [];
  if (bug.found_in) {
    const a = document.createElement('a');
    a.className = 'bug-row__found-in';
    a.setAttribute('href', `#/task/${encodeURIComponent(bug.found_in)}`);
    a.textContent = `found in ${bug.found_in}`;
    a.title = a.textContent;   // cut with an ellipsis when long (bugs.css)
    controls.push(a);
  }

  const row = linkRow({
    tag: 'li', className: 'bug-row' + (archived ? ' bug-row--archived' : ''),
    href: `#/bug/${encodeURIComponent(bug.id)}`, name, content, controls,
  });
  row.dataset.bugId = bug.id;
  return row;
}
