// bug-card.js — renders a single bug as a card element.
// Mirrors issue-card.js conventions: createElement, data-status attribute,
// status pill as an inline <span> chip (no external status-pill component).
// UI rules: no colored left rail, no box-shadow, no transform on hover.

export function bugCard(bug, { onClick } = {}) {
  const card = document.createElement('article');
  card.className = 'bug-card';
  card.setAttribute('data-bug-id', bug.id);
  card.setAttribute('data-status', bug.status || 'open');

  if (onClick) card.addEventListener('click', () => onClick(bug));

  // ---- head: id · title · status chip
  const head = document.createElement('div');
  head.className = 'bug-card__head';

  const idEl = document.createElement('span');
  idEl.className = 'bug-card__id';
  idEl.textContent = bug.id;
  head.appendChild(idEl);

  const titleEl = document.createElement('span');
  titleEl.className = 'bug-card__title';
  titleEl.textContent = bug.title;
  head.appendChild(titleEl);

  const statusChip = document.createElement('span');
  statusChip.className = 'bug-card__status-chip';
  statusChip.dataset.status = bug.status || 'open';
  statusChip.textContent = bug.status || 'open';
  head.appendChild(statusChip);

  card.appendChild(head);

  // ---- meta row: found_in link · components · discovered date
  const meta = document.createElement('div');
  meta.className = 'bug-card__meta';

  if (bug.found_in) {
    const fi = document.createElement('a');
    fi.className = 'bug-card__found-in';
    fi.href = `#/task/${bug.found_in}`;
    fi.textContent = `found in ${bug.found_in}`;
    fi.addEventListener('click', (ev) => ev.stopPropagation());
    meta.appendChild(fi);
  }

  if (bug.components && bug.components.length) {
    const comps = document.createElement('span');
    comps.className = 'bug-card__components';
    comps.textContent = bug.components.join(', ');
    meta.appendChild(comps);
  }

  if (bug.discovered) {
    const d = document.createElement('time');
    d.className = 'bug-card__discovered';
    d.dateTime = bug.discovered;
    d.textContent = bug.discovered.split('T')[0];
    meta.appendChild(d);
  }

  if (meta.childElementCount > 0) card.appendChild(meta);

  // ---- description (one-liner if present)
  if (bug.description) {
    const desc = document.createElement('div');
    desc.className = 'bug-card__description';
    desc.textContent = bug.description;
    card.appendChild(desc);
  }

  return card;
}

export default bugCard;
