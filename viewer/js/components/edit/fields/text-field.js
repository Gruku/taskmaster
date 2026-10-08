// viewer/js/components/edit/fields/text-field.js
import { h } from '../../../util/h.js';
import { bindControl, cancelOnEscape, focusOnMount } from './control.js';

export const TextField = {
  read({ value, readOnly = false, placeholder = '' }) {
    const empty = value == null || value === '';
    const text = empty ? (placeholder || '—') : String(value);
    const cls = ['ef-text'];
    if (!readOnly) cls.push('ef-editable');
    if (empty) cls.push('ef-placeholder');
    return h('span', { class: cls.join(' ') }, text);
  },

  edit({ value, onChange, onCommit, onCancel, maxLength, required, id, describedBy, autoFocus = true }) {
    const input = h('input', {
      type: 'text',
      class: 'ef-text-input',
      value: value == null ? '' : String(value),
    });
    if (maxLength != null) input.setAttribute('maxlength', String(maxLength));
    if (required) input.setAttribute('required', '');
    bindControl(input, { id, describedBy });
    input.addEventListener('input', () => onChange?.(input.value));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); onCommit?.(input.value); }
      else cancelOnEscape(e, onCancel);
    });
    input.addEventListener('blur', () => onCommit?.(input.value));
    focusOnMount(input, autoFocus, { select: true });
    return input;
  },

  coerce(raw) {
    if (raw == null) return null;
    const trimmed = String(raw).trim();
    return trimmed === '' ? null : trimmed;
  },

  validate(value, { required = false, maxLength = null } = {}) {
    if (required && (value == null || value === '')) return 'required';
    if (maxLength != null && value != null && String(value).length > maxLength) {
      return `too long (max ${maxLength})`;
    }
    return null;
  },
};
