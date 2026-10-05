// User intent: a sortable column header is a real button that says which way the table is sorted — to the eye with
// an arrow, and to a screen reader with aria-sort on the one sorted column.
import { icon } from './icon.js';

export function nextSort(sort, key) {
  if (sort && sort.by === key) return { by: key, dir: sort.dir === 'asc' ? 'desc' : 'asc' };
  return { by: key, dir: 'asc' };
}

export function sortHeader({ key, label, sortable = true, sort, onSort }) {
  // Refused when built: a header that only fails when clicked would ship looking like it sorts.
  if (sortable && typeof onSort !== 'function') throw new TypeError(`sortHeader: "${key}" is sortable but has no onSort`);
  const th = document.createElement('th');
  th.setAttribute('scope', 'col');
  th.className = 'sort-th';
  const text = document.createElement('span');
  text.className = 'sort-header__label';
  text.textContent = label;
  if (!sortable) { th.append(text); return th; }

  const active = !!sort && sort.by === key;
  const dir = active ? (sort.dir === 'desc' ? 'desc' : 'asc') : 'none';
  if (active) th.setAttribute('aria-sort', dir === 'asc' ? 'ascending' : 'descending');

  const arrow = document.createElement('span');
  arrow.className = `sort-header__dir sort-header__dir--${dir}`;
  arrow.append(icon(active ? 'chevron' : 'sort', { size: 12 }));

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'sort-header';
  btn.append(text, arrow);
  btn.addEventListener('click', () => onSort(nextSort(sort, key)));
  th.append(btn);
  return th;
}
