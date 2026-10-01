// viewer/js/components/edit/fields/enum-select.js
import { h } from '../../../util/h.js';
import { icon } from '../../icon.js';
import { statusMarker, priorityMarker } from '../../status.js';
import { bindControl, focusOnMount } from './control.js';

const MARKERS = {
  status: (value) => statusMarker('task', value),
  priority: (value) => priorityMarker(value),
};

export const EnumSelect = {
  // `marker: 'status' | 'priority'` shows the value as its shape plus word instead of the option label.
  read({ value, options = [], readOnly = false, placeholder = '', marker }) {
    const empty = value == null || value === '';
    const cls = ['ef-enum'];
    if (!readOnly) cls.push('ef-editable');
    if (empty) cls.push('ef-placeholder');
    if (!empty && Object.hasOwn(MARKERS, marker)) {
      return h('span', { class: cls.join(' ') }, MARKERS[marker](value));
    }
    const match = options.find(o => o.value === value);
    const label = match ? match.label : (empty ? (placeholder || '—') : String(value));
    return h('span', { class: cls.join(' ') }, label);
  },

  // Returns a wrapper so the arrow can be an icon instead of a coloured background image;
  // the <select> itself is `wrapper.control`.
  edit({ value, options = [], onChange, onCommit, onCancel, id, describedBy, autoFocus = true }) {
    const sel = h('select', { class: 'ef-enum-select' });
    for (const opt of options) {
      const o = h('option', { value: opt.value }, opt.label);
      if (opt.value === value) o.selected = true;
      sel.appendChild(o);
    }
    bindControl(sel, { id, describedBy });
    sel.addEventListener('change', () => {
      onChange?.(sel.value);
      onCommit?.(sel.value);
    });
    sel.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); onCancel?.(); }
    });
    focusOnMount(sel, autoFocus);
    const wrap = h('span', { class: 'ef-select' }, [sel, icon('chevron', { size: 16 })]);
    wrap.control = sel;
    return wrap;
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
