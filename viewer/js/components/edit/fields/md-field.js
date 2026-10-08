// viewer/js/components/edit/fields/md-field.js
import { h } from '../../../util/h.js';
import { renderMarkdown } from '../../markdown.js';
import { bindControl, cancelOnEscape, focusOnMount } from './control.js';

export const MdField = {
  read({ value, readOnly = false, placeholder = '' }) {
    const empty = value == null || String(value).trim() === '';
    const cls = ['ef-md'];
    if (!readOnly) cls.push('ef-editable');
    if (empty) {
      cls.push('ef-placeholder');
      return h('div', { class: cls.join(' ') }, placeholder || 'no content');
    }
    cls.push('md-body');
    // renderMarkdown sanitises; it is the only path task text takes into innerHTML.
    return h('div', { class: cls.join(' '), html: renderMarkdown(String(value)) });
  },

  edit({ value, onChange, onCommit, onCancel, rows = 6, maxLength, required, id, describedBy, autoFocus = true }) {
    const ta = h('textarea', {
      class: 'ef-md-textarea',
      rows: String(rows),
    });
    ta.value = value == null ? '' : String(value);
    if (maxLength != null) ta.setAttribute('maxlength', String(maxLength));
    if (required) ta.setAttribute('required', '');
    bindControl(ta, { id, describedBy });
    ta.addEventListener('input', () => onChange?.(ta.value));
    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        onCommit?.(ta.value);
      } else cancelOnEscape(e, onCancel);
    });
    ta.addEventListener('blur', () => onCommit?.(ta.value));
    focusOnMount(ta, autoFocus, { select: true });
    return ta;
  },

  coerce(raw) {
    if (raw == null) return null;
    const trimmed = String(raw).replace(/[\s\n]+$/, '').replace(/^[\s\n]+/, '');
    return trimmed === '' ? null : trimmed;
  },

  validate(value, { required = false } = {}) {
    if (required && (value == null || value === '')) return 'required';
    return null;
  },
};
