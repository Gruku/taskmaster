// User intent: one inline icon set for the whole viewer — the Reality Reprojection utility pack plus the few glyphs it lacks, drawn to the same rules (24 box, 2.25 stroke, round caps).
const NS = 'http://www.w3.org/2000/svg';

export const ICONS = {
  // ── Reality Reprojection pack (inner markup of docs/specs/assets/reality-reprojection-2026-10-01/icons/<name>.svg; ink fills → currentColor) ──
  polarity: '<circle cx="12" cy="12" r="9"></circle><path d="M12 3 A9 9 0 0 1 12 21 Z" fill="currentColor"></path>',
  grid: '<rect x="4" y="4" width="7" height="7" rx="1"></rect><rect x="13" y="4" width="7" height="7" rx="1"></rect><rect x="4" y="13" width="7" height="7" rx="1"></rect><rect x="13" y="13" width="7" height="7" rx="1"></rect>',
  chevron: '<path d="M9 5 L16 12 L9 19"></path>',
  arrow: '<path d="M3.5 12 H13"></path><path d="M12 5.5 L20 12 L12 18.5 Z" fill="currentColor"></path>',
  check: '<path d="M4.5 12.5 L9.5 17.5 L19.5 6.5"></path>',
  copy: '<rect x="4" y="8" width="11" height="12" rx="1"></rect><path d="M8 8 V4 H19 V16 H15"></path>',
  dismiss: '<path d="M5.5 5.5 L18.5 18.5 M18.5 5.5 L5.5 18.5"></path>',
  document: '<path d="M6 3 H15 L19 7 V21 H6 Z"></path><path d="M14 3 V8 H19"></path><path d="M9 12 H16 M9 15.5 H16 M9 19 H13" stroke-width="2"></path>',
  edit: '<path d="M4 20 L4 16 L16 4 L20 8 L8 20 Z"></path><path d="M13 7 L17 11"></path>',
  external: '<path d="M9 5 H19 V15"></path><path d="M19 5 L9.5 14.5"></path><path d="M14 19 H5 V10"></path>',
  folder: '<path d="M3 6 H10 L12 9 H21 V19 H3 Z"></path>',
  minus: '<path d="M4 12 H20"></path>',
  more: '<rect x="3.75" y="10.625" width="3.5" height="2.75" rx="0.5" fill="currentColor" stroke="none"></rect><rect x="10.25" y="10.625" width="3.5" height="2.75" rx="0.5" fill="currentColor" stroke="none"></rect><rect x="16.75" y="10.625" width="3.5" height="2.75" rx="0.5" fill="currentColor" stroke="none"></rect>',
  plus: '<path d="M12 4 V20 M4 12 H20"></path>',
  search: '<circle cx="10.5" cy="10.5" r="6"></circle><path d="M15 15 L20 20"></path>',
  // sliders: the source fills the knobs with the light ground (#f5f3ed) to mask the track; here the knob takes the surface it sits on.
  sliders: '<path d="M4 7 H20 M4 12 H20 M4 17 H20"></path><rect x="6.75" y="4.5" width="3.5" height="5" rx="0.75" fill="var(--surface-ground)"></rect><rect x="13.75" y="9.5" width="3.5" height="5" rx="0.75" fill="var(--surface-ground)"></rect><rect x="8.75" y="14.5" width="3.5" height="5" rx="0.75" fill="var(--surface-ground)"></rect>',

  // ── Viewer glyphs ──
  kanban: '<rect x="3.5" y="4" width="4.5" height="16" rx="1"></rect><rect x="9.75" y="4" width="4.5" height="10" rx="1"></rect><rect x="16" y="4" width="4.5" height="13" rx="1"></rect>',
  table: '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"></rect><path d="M3.5 10h17M3.5 14.75h17M9.5 10v9.5"></path>',
  alert: '<path d="M12 4.5 20.5 19h-17Z"></path><path d="M12 10v4.25M12 16.9v.1"></path>',
  bug: '<rect x="8" y="8" width="8" height="11" rx="4"></rect><path d="M9.5 8a2.5 2.5 0 0 1 5 0M4 13h4M16 13h4M5 7.5l3 2.5M19 7.5 16 10M5 19l3-2.5M19 19l-3-2.5"></path>',
  idea: '<path d="M8.5 14a5.5 5.5 0 1 1 7 0c-.7.6-1 1.3-1 3h-5c0-1.7-.3-2.4-1-3Z"></path><path d="M10 20h4"></path>',
  archive: '<rect x="3.5" y="5" width="17" height="4" rx="1"></rect><path d="M5 9v9.5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4"></path>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"></path>',
  sort: '<path d="M8 9.5 12 5.5 16 9.5M8 14.5 12 18.5 16 14.5"></path>',
};

export function icon(name, { size = 20, label } = {}) {
  if (!Object.hasOwn(ICONS, name)) throw new Error(`unknown icon: ${name}`);
  const inner = ICONS[name];
  const el = document.createElementNS(NS, 'svg');
  el.setAttribute('viewBox', '0 0 24 24');
  el.setAttribute('width', String(size));
  el.setAttribute('height', String(size));
  el.setAttribute('fill', 'none');
  el.setAttribute('stroke', 'currentColor');
  el.setAttribute('stroke-width', '2.25');
  el.setAttribute('stroke-linecap', 'round');
  el.setAttribute('stroke-linejoin', 'round');
  if (label) { el.setAttribute('role', 'img'); el.setAttribute('aria-label', label); }
  else el.setAttribute('aria-hidden', 'true');
  el.classList.add('icon');
  el.innerHTML = inner;
  return el;
}
