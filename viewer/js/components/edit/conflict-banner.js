// viewer/js/components/edit/conflict-banner.js
// Surfaces 409 conflicts. Two flavors:
//   showFieldConflict — single-field, used by inline-field.js
//   showFullConflict  — multi-field diff, used by entity-modal.js

import { h } from '../../util/h.js';

const HOST_ID = 'conflict-banner-host';

function getHost() {
  const host = document.getElementById(HOST_ID);
  if (!host) throw new Error(`#${HOST_ID} not found`);
  return host;
}

function dismiss(banner) {
  banner.remove();
}

export function showFieldConflict({
  entityKind, entityId, fieldKey, fieldLabel,
  localValue, currentValue, currentEtag,
  onKeepMine, onUseServer,
}) {
  const host = getHost();
  // Replace any existing banner — only one at a time.
  host.replaceChildren();
  const banner = h('div', { class: 'cb-banner cb-field' }, [
    h('div', { class: 'cb-headline' },
      `${entityKind} ${entityId} — "${fieldLabel}" updated by another writer`),
    h('div', { class: 'cb-diff' }, [
      h('div', { class: 'cb-row' }, [
        h('span', { class: 'cb-label' }, 'You: '),
        h('span', { class: 'cb-val cb-val-mine' }, _stringify(localValue)),
      ]),
      h('div', { class: 'cb-row' }, [
        h('span', { class: 'cb-label' }, 'Server: '),
        h('span', { class: 'cb-val cb-val-server' }, _stringify(currentValue)),
      ]),
    ]),
    h('div', { class: 'cb-actions' }, [
      h('button', { type: 'button', class: 'cb-use-server',
                    on: { click: () => { onUseServer(); dismiss(banner); } } },
        'Use server'),
      h('button', { type: 'button', class: 'cb-keep-mine',
                    on: { click: async () => { await onKeepMine(); dismiss(banner); } } },
        'Keep mine'),
    ]),
  ]);
  host.appendChild(banner);
  return () => dismiss(banner);
}

export function showFullConflict({
  entityKind, entityId, localDraft, currentValue, currentEtag, onResolve,
}) {
  const host = getHost();
  host.replaceChildren();
  // Compute per-field diffs.
  const allKeys = new Set([...Object.keys(localDraft || {}), ...Object.keys(currentValue || {})]);
  const decisions = {};
  for (const k of allKeys) {
    if (JSON.stringify(localDraft?.[k] ?? null) === JSON.stringify(currentValue?.[k] ?? null)) continue;
    decisions[k] = 'mine'; // default to keeping local
  }
  const rows = h('div', { class: 'cb-rows' });
  for (const k of Object.keys(decisions)) {
    const row = h('div', { class: 'cb-multi-row' }, [
      h('div', { class: 'cb-key' }, k),
      h('div', { class: 'cb-val cb-val-mine' }, _stringify(localDraft[k])),
      h('div', { class: 'cb-val cb-val-server' }, _stringify(currentValue[k])),
      h('div', { class: 'cb-multi-actions' }, [
        _radio(`cb-d-${k}`, 'mine', 'Keep mine', decisions[k] === 'mine',
               () => { decisions[k] = 'mine'; }),
        _radio(`cb-d-${k}`, 'server', 'Use server', decisions[k] === 'server',
               () => { decisions[k] = 'server'; }),
      ]),
    ]);
    rows.appendChild(row);
  }
  const banner = h('div', { class: 'cb-banner cb-full' }, [
    h('div', { class: 'cb-headline' },
      `${entityKind} ${entityId} updated by another writer — pick fields to keep`),
    rows,
    h('div', { class: 'cb-actions' }, [
      h('button', { type: 'button', class: 'cb-resolve',
                    on: { click: async () => {
                      const merged = { ...currentValue };
                      for (const [k, choice] of Object.entries(decisions)) {
                        merged[k] = choice === 'mine' ? localDraft[k] : currentValue[k];
                      }
                      await onResolve(merged);
                      dismiss(banner);
                    } } }, 'Apply choices'),
    ]),
  ]);
  host.appendChild(banner);
  return () => dismiss(banner);
}

function _stringify(v) {
  if (v == null) return '—';
  if (Array.isArray(v)) return v.join(', ');
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

function _radio(name, value, label, checked, onChange) {
  const id = `${name}-${value}`;
  const lbl = h('label', { for: id, class: 'cb-radio' }, [
    h('input', { type: 'radio', name, id, value }),
    h('span', {}, label),
  ]);
  const input = lbl.querySelector('input');
  input.checked = checked;
  input.addEventListener('change', () => { if (input.checked) onChange(); });
  return lbl;
}
