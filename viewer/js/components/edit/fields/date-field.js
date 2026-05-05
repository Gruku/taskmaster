// viewer/js/components/edit/fields/date-field.js
import { h } from '../../../util/h.js';

const ISO_DATE_RE = /^(\d{4}-\d{2}-\d{2})/;

export const DateField = {
  read({ value, readOnly = false }) {
    const cls = ['ef-date']; if (!readOnly) cls.push('ef-editable');
    return h('span', { class: cls.join(' ') }, value == null ? '—' : String(value).slice(0, 10));
  },
  edit({ value, onChange, onCommit, onCancel }) {
    const inp = h('input', { type: 'date', class: 'ef-date-input' });
    if (value) inp.value = String(value).slice(0, 10);
    inp.addEventListener('input', () => onChange?.(inp.value));
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); onCommit?.(inp.value); }
      else if (e.key === 'Escape') { e.preventDefault(); onCancel?.(); }
    });
    inp.addEventListener('blur', () => onCommit?.(inp.value));
    queueMicrotask(() => inp.focus());
    return inp;
  },
  coerce(raw) {
    if (raw == null || raw === '') return null;
    const m = ISO_DATE_RE.exec(String(raw));
    if (!m) return null;
    const d = new Date(m[1] + 'T00:00:00Z');
    return Number.isFinite(d.getTime()) ? m[1] : null;
  },
  validate(value, { required = false } = {}) {
    if (required && (value == null || value === '')) return 'required';
    if (value != null && value !== '' && DateField.coerce(value) == null) return 'invalid date';
    return null;
  },
};
