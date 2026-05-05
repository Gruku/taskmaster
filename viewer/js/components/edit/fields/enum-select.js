// viewer/js/components/edit/fields/enum-select.js
import { h } from '../../../util/h.js';

export const EnumSelect = {
  read({ value, options = [], readOnly = false, placeholder = '' }) {
    const match = options.find(o => o.value === value);
    const label = match ? match.label : (value == null || value === '' ? (placeholder || '—') : String(value));
    const cls = ['ef-enum'];
    if (!readOnly) cls.push('ef-editable');
    if (value == null || value === '') cls.push('ef-placeholder');
    return h('span', { class: cls.join(' ') }, label);
  },

  edit({ value, options = [], onChange, onCommit, onCancel }) {
    const sel = h('select', { class: 'ef-enum-select' });
    for (const opt of options) {
      const o = h('option', { value: opt.value }, opt.label);
      if (opt.value === value) o.selected = true;
      sel.appendChild(o);
    }
    sel.addEventListener('change', () => {
      onChange?.(sel.value);
      onCommit?.(sel.value);
    });
    sel.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); onCancel?.(); }
    });
    queueMicrotask(() => sel.focus());
    return sel;
  },

  coerce(raw) {
    if (raw == null) return null;
    const v = String(raw).trim();
    return v === '' ? null : v;
  },

  validate(value, { required = false, options = [] } = {}) {
    if (required && (value == null || value === '')) return 'required';
    if (value != null && value !== '' && !options.some(o => o.value === value)) {
      return 'invalid value';
    }
    return null;
  },
};
