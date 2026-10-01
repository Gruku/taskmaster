// User intent: an estimate is picked, not typed from memory — a size (S, M, L) or a whole number of days, never both —
// so the board never carries "medium-ish" or "3 d" as an estimate.
import { h } from '../../../util/h.js';
import { bindControl, focusOnMount } from './control.js';

const SIZES = ['S', 'M', 'L'];
const DAYS_RE = /^([1-9]\d*)d$/;
const MESSAGE = 'use S, M, L or a whole number of days';
// What the days input shows: the number of any '<number>d' value, valid or not, so a refused value stays visible.
const TYPED_DAYS_RE = /^(-?\d+(?:\.\d+)?)d$/;

// The stored forms are 'S' | 'M' | 'L' | '<n>d'. Anything else comes back as text so validate can refuse it.
function coerce(raw) {
  if (raw == null) return null;
  if (typeof raw === 'number') return Number.isInteger(raw) && raw > 0 ? `${raw}d` : String(raw);
  if (typeof raw !== 'string') return typeof raw === 'object' ? JSON.stringify(raw) : String(raw);
  const text = raw.trim();
  if (text === '') return null;
  if (/^[sml]$/i.test(text)) return text.toUpperCase();
  const days = /^0*(\d+)d$/i.exec(text);
  return days ? `${days[1]}d` : text;
}

export const EstimateField = {
  read({ value, readOnly = false, placeholder = '' }) {
    const v = coerce(value);
    const cls = ['ef-estimate'];
    if (!readOnly) cls.push('ef-editable');
    if (v == null) cls.push('ef-placeholder');
    return h('span', { class: cls.join(' ') }, v == null ? (placeholder || '—') : v);
  },

  // The id goes on the days input: a label click on a size button would press it.
  edit({ value, onChange, onCommit, onCancel, id, describedBy, autoFocus = true, label = 'Estimate' }) {
    let current = coerce(value);
    const buttons = SIZES.map((size) => h('button', { type: 'button', class: 'ef-estimate-size', 'aria-pressed': 'false' }, size));
    const days = h('input', { type: 'number', class: 'ef-estimate-days-input', min: '1', step: '1', inputmode: 'numeric' });
    bindControl(days, { id, describedBy });
    const wrap = h('div', { class: 'ef-estimate-edit', role: 'group', 'aria-label': label }, [
      h('span', { class: 'ef-estimate-sizes' }, buttons),
      h('label', { class: 'ef-estimate-days' }, [days, h('span', {}, 'days')]),
    ]);
    wrap.control = days;

    function paint({ keepDays = false } = {}) {
      buttons.forEach((b, i) => b.setAttribute('aria-pressed', String(current === SIZES[i])));
      if (!keepDays) days.value = TYPED_DAYS_RE.exec(current ?? '')?.[1] ?? '';
    }
    paint();

    buttons.forEach((b, i) => b.addEventListener('click', () => {
      current = current === SIZES[i] ? null : SIZES[i];
      paint();
      onChange?.(current);
      onCommit?.(current);
    }));
    days.addEventListener('input', () => {
      // What was typed is passed on even when it is not a valid day count, so the form can say so.
      const typed = days.value.trim();
      current = typed === '' ? null : `${typed}d`;
      paint({ keepDays: true });
      onChange?.(current);
    });
    wrap.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target === days) { e.preventDefault(); onCommit?.(current); }
      else if (e.key === 'Escape') { e.preventDefault(); onCancel?.(); }
    });
    // Moving between the sizes and the days stays inside the field; only leaving it commits.
    wrap.addEventListener('focusout', (e) => {
      if (e.target === days && !wrap.contains(e.relatedTarget)) onCommit?.(current);
    });
    focusOnMount(days, autoFocus, { select: true });
    return wrap;
  },

  coerce,

  validate(value, { required = false } = {}) {
    const v = coerce(value);
    if (v == null) return required ? 'required' : null;
    return SIZES.includes(v) || DAYS_RE.test(v) ? null : MESSAGE;
  },
};
