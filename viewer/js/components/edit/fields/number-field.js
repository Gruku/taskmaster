// viewer/js/components/edit/fields/number-field.js
import { h } from '../../../util/h.js';

export const NumberField = {
  read({ value, readOnly = false }) {
    const cls = ['ef-num']; if (!readOnly) cls.push('ef-editable');
    return h('span', { class: cls.join(' ') }, value == null ? '—' : String(value));
  },
  edit({ value, onChange, onCommit, onCancel, min, max }) {
    const inp = h('input', { type: 'number', class: 'ef-num-input', value: value == null ? '' : String(value) });
    if (min != null) inp.setAttribute('min', String(min));
    if (max != null) inp.setAttribute('max', String(max));
    inp.addEventListener('input', () => onChange?.(inp.value));
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); onCommit?.(inp.value); }
      else if (e.key === 'Escape') { e.preventDefault(); onCancel?.(); }
    });
    inp.addEventListener('blur', () => onCommit?.(inp.value));
    queueMicrotask(() => { inp.focus(); inp.select(); });
    return inp;
  },
  coerce(raw) {
    if (raw == null || raw === '') return null;
    const n = Number(raw); return Number.isFinite(n) ? Math.trunc(n) : null;
  },
  validate(value, { required = false, min = null, max = null } = {}) {
    if (required && value == null) return 'required';
    if (value != null) {
      if (min != null && value < min) return `must be ≥ ${min}`;
      if (max != null && value > max) return `must be ≤ ${max}`;
    }
    return null;
  },
};
