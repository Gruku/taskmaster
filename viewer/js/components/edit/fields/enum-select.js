// viewer/js/components/edit/fields/enum-select.js
import { h } from '../../../util/h.js';
import { icon } from '../../icon.js';
import { statusMarker, priorityMarker } from '../../status.js';
import { bindControl, cancelOnEscape, focusOnMount } from './control.js';

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
    const empty = value == null || value === '';
    // A select always shows some option. A stored value that is not on the list, or no value where the list has no
    // blank, gets an option of its own — disabled, so it can be seen and left but not picked — instead of the first
    // option standing in for it.
    if (!options.some(o => o.value === (empty ? '' : value))) {
      const stand = h('option', { value: empty ? '' : String(value), disabled: '' }, empty ? 'Select…' : String(value));
      stand.selected = true;
      sel.appendChild(stand);
    }
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
    // With its list open the browser keeps Escape for the list; this only sees the key when the list is closed.
    sel.addEventListener('keydown', (e) => cancelOnEscape(e, onCancel));
    // Leaving without a choice (Tab, a click elsewhere) is a cancel too: an inline picker left open would hold the
    // task's edit lease, and with it every live update. A form passes no onCancel, and its select simply stays.
    const opened = sel.value;
    sel.addEventListener('blur', () => { if (sel.value === opened) onCancel?.(); });
    focusOnMount(sel, autoFocus);
    const wrap = h('span', { class: 'ef-select' }, [sel, icon('chevron', { size: 16 })]);
    wrap.control = sel;
    // Back to the value it opened with: a choice the server refused is not left showing as if it took.
    wrap.reset = () => { sel.value = opened; };
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
