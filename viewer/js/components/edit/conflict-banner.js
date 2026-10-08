// viewer/js/components/edit/conflict-banner.js
// Surfaces 409 conflicts. Two flavors:
//   showFieldConflict — single-field, used by inline-field.js
//   showFullConflict  — multi-field diff, used by task-actions.js
// User intent: when someone else changed what the user is editing, say so in words that read in both themes, name each
// field the way the form does, offer a named choice per field, and push any open dialog down rather than cover it.

import { h } from '../../util/h.js';
import { sameValue } from './same-value.js';
import { marker } from '../status.js';
import { truncate } from '../../lib/text.js';

const HOST_ID = 'conflict-banner-host';
const HEIGHT_PROP = '--conflict-banner-height';

// Each banner numbers its ids and radio names, so a banner shown after another never shares one with it.
let seq = 0;
// The banner on screen and the function that stops tracking its height.
let shown = null;

function getHost() {
  const host = document.getElementById(HOST_ID);
  if (!host) throw new Error(`#${HOST_ID} not found`);
  return host;
}

function forget() {
  shown?.stop();
  shown = null;
}

function dismiss(banner) {
  if (shown?.banner === banner) forget();
  banner.remove();
}

// The modal overlay reads the banner's height from the root, so a dialog opens below it rather than under it.
function track(banner) {
  const root = document.documentElement.style;
  const measure = () => {
    if (!banner.isConnected) { if (shown?.banner === banner) forget(); return; }
    root.setProperty(HEIGHT_PROP, `${Math.ceil(banner.getBoundingClientRect().height)}px`);
  };
  const Observer = globalThis.ResizeObserver;
  const observer = typeof Observer === 'function' ? new Observer(measure) : null;
  observer?.observe(banner);
  window.addEventListener('resize', measure);
  shown = {
    banner,
    stop() {
      observer?.disconnect();
      window.removeEventListener('resize', measure);
      root.removeProperty(HEIGHT_PROP);
    },
  };
  measure();
}

function show(banner) {
  const host = getHost();
  // Replace any existing banner — only one at a time.
  forget();
  host.replaceChildren(banner);
  track(banner);
}

function headline(id, sentence) {
  return h('div', { class: 'cb-headline', id, role: 'alert' }, [
    marker({ label: 'Conflict', shape: '▲', tone: 'warning' }),
    h('span', { class: 'cb-sentence' }, sentence),
  ]);
}

// One side of a difference: its tag ("Yours", "Saved") over the value, cut to three lines with the full text kept.
function side(tag, value, which, text = conflictValueText) {
  return h('div', { class: `cb-side cb-side-${which}` }, [
    h('span', { class: 'cb-tag' }, tag),
    truncate(text(value), { lines: 3, tag: 'div', className: `cb-val cb-val-${which}` }),
  ]);
}

const capitalise = (s) => s.charAt(0).toUpperCase() + s.slice(1);
// 'depends_on' → 'Depends on', for a field the caller gave no label.
const sentenceCase = (key) => capitalise(String(key).replace(/[_-]+/g, ' ').trim());

export function showFieldConflict({
  entityKind, entityId, fieldKey, fieldLabel,
  localValue, currentValue,
  onKeepMine, onUseServer, text = conflictValueText,
}) {
  const id = `cb-${++seq}`;
  const banner = h('div', { class: 'cb-banner cb-field', role: 'region', 'aria-labelledby': `${id}-headline` }, [
    headline(`${id}-headline`, `"${fieldLabel}" on ${entityKind} ${entityId} was changed by someone else`),
    h('div', { class: 'cb-diff' }, [
      side('Yours', localValue, 'mine', text),
      side('Saved', currentValue, 'server', text),
    ]),
    h('div', { class: 'cb-actions' }, [
      h('button', { type: 'button', class: 'cb-use-server btn btn--secondary',
                    on: { click: () => { onUseServer(); dismiss(banner); } } },
        'Use server'),
      h('button', { type: 'button', class: 'cb-keep-mine btn btn--primary',
                    on: { click: async () => { await onKeepMine(); dismiss(banner); } } },
        'Keep mine'),
    ]),
  ]);
  show(banner);
  return () => dismiss(banner);
}

export function showFullConflict({
  entityKind, entityId, localDraft, currentValue, labels = {}, texts = {}, onResolve, onDismiss,
}) {
  const id = `cb-${++seq}`;
  // Compute per-field diffs.
  const allKeys = new Set([...Object.keys(localDraft || {}), ...Object.keys(currentValue || {})]);
  const decisions = {};
  for (const k of allKeys) {
    if (sameValue(localDraft?.[k], currentValue?.[k])) continue;
    decisions[k] = 'mine'; // default to keeping local
  }
  const rows = h('div', { class: 'cb-rows' });
  for (const k of Object.keys(decisions)) {
    const keyId = `${id}-key-${k}`;
    const name = `${id}-${k}`;
    const text = Object.hasOwn(texts, k) ? texts[k] : conflictValueText;
    const row = h('div', { class: 'cb-multi-row' }, [
      h('div', { class: 'cb-key', id: keyId }, Object.hasOwn(labels, k) ? labels[k] : sentenceCase(k)),
      side('Yours', localDraft[k], 'mine', text),
      side('Saved', currentValue[k], 'server', text),
      h('div', { class: 'cb-multi-actions', role: 'radiogroup', 'aria-labelledby': keyId }, [
        _radio(name, 'mine', 'Keep mine', decisions[k] === 'mine',
               () => { decisions[k] = 'mine'; }),
        _radio(name, 'server', 'Use server', decisions[k] === 'server',
               () => { decisions[k] = 'server'; }),
      ]),
    ]);
    rows.appendChild(row);
  }
  const banner = h('div', { class: 'cb-banner cb-full', role: 'region', 'aria-labelledby': `${id}-headline` }, [
    headline(`${id}-headline`,
      `${capitalise(String(entityKind))} ${entityId} was changed by someone else — choose what to keep for each field`),
    rows,
    h('div', { class: 'cb-actions' }, [
      // Stepping back is always possible: the form behind keeps the edits and can be saved again.
      h('button', { type: 'button', class: 'cb-dismiss btn btn--secondary',
                    on: { click: () => { dismiss(banner); onDismiss?.(); } } }, 'Dismiss'),
      h('button', { type: 'button', class: 'cb-resolve btn btn--primary',
                    on: { click: async () => {
                      const merged = { ...currentValue };
                      for (const [k, choice] of Object.entries(decisions)) {
                        merged[k] = choice === 'mine' ? localDraft[k] : currentValue[k];
                      }
                      // Held while the choices are written: a second press would write them twice, and a dismiss
                      // would hand the form back mid-write.
                      for (const el of banner.querySelectorAll('button, input')) el.disabled = true;
                      try { await onResolve(merged); } finally { dismiss(banner); }
                    } } }, 'Apply choices'),
    ]),
  ]);
  show(banner);
  return () => dismiss(banner);
}

// A value as the banner shows it: empties are a dash, a list is joined, a map is one `key: value` line per entry.
export function conflictValueText(v) {
  if (v == null || v === '') return '—';
  if (Array.isArray(v)) return v.length ? v.join(', ') : '—';
  if (typeof v === 'object') {
    const entries = Object.entries(v);
    if (!entries.length) return '—';
    return entries.map(([k, x]) => `${k}: ${typeof x === 'string' ? x : JSON.stringify(x)}`).join('\n');
  }
  return String(v);
}

// A choice field's value as the form words it: the matching option's label, so a lost race on Status reads
// "In progress", not "in-progress". Options are read per call because some specs compute them with a getter.
export function optionText(spec) {
  const one = (v) => {
    const opts = spec?.options;
    const hit = Array.isArray(opts) ? opts.find((o) => o?.value === v) : undefined;
    return hit && hit.label != null ? String(hit.label) : conflictValueText(v);
  };
  return (v) => (Array.isArray(v) ? (v.length ? v.map(one).join(', ') : conflictValueText(v)) : one(v));
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
