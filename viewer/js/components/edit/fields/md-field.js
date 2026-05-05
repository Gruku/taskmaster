// viewer/js/components/edit/fields/md-field.js
import { h } from '../../../util/h.js';

export const MdField = {
  read({ value, readOnly = false, placeholder = '' }) {
    const empty = value == null || String(value).trim() === '';
    const text = empty ? (placeholder || 'no content') : String(value);
    const cls = ['ef-md'];
    if (!readOnly) cls.push('ef-editable');
    if (empty) cls.push('ef-placeholder');
    const div = h('div', { class: cls.join(' ') });
    if (empty) {
      div.textContent = text;
    } else {
      // Minimal: replace newlines with <br>. The full md renderer used in
      // task-detail-document.js's renderMdSection stays for the non-edit
      // read view; this is the in-place inline read form.
      div.innerHTML = text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/\n/g, '<br>');
    }
    return div;
  },

  edit({ value, onChange, onCommit, onCancel, rows = 6, maxLength, required }) {
    const ta = h('textarea', {
      class: 'ef-md-textarea',
      rows: String(rows),
    });
    ta.value = value == null ? '' : String(value);
    if (maxLength != null) ta.setAttribute('maxlength', String(maxLength));
    if (required) ta.setAttribute('required', '');
    ta.addEventListener('input', () => onChange?.(ta.value));
    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        onCommit?.(ta.value);
      } else if (e.key === 'Escape') {
        e.preventDefault();
        onCancel?.();
      }
    });
    ta.addEventListener('blur', () => onCommit?.(ta.value));
    queueMicrotask(() => { ta.focus(); ta.select(); });
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
