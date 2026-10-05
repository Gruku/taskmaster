// User intent: a whole number (a stage) typed into a number input — and a stored value that is not a number is shown
// in words rather than hidden behind an empty input, so opening the field never looks like it lost something.
import { h } from '../../../util/h.js';
import { bindControl, cancelOnEscape, focusOnMount } from './control.js';

let seq = 0;
// What a number input can show: a finite number, or text that reads as one.
const showable = (v) => (typeof v === 'number' ? Number.isFinite(v)
  : typeof v === 'string' && (v.trim() === '' || Number.isFinite(Number(v))));

export const NumberField = {
  read({ value, readOnly = false }) {
    const cls = ['ef-num']; if (!readOnly) cls.push('ef-editable');
    return h('span', { class: cls.join(' ') }, value == null ? '—' : String(value));
  },
  edit({ value, onChange, onCommit, onCancel, min, max, id, describedBy, autoFocus = true }) {
    const stored = value == null || showable(value) ? null : typeof value === 'object' ? JSON.stringify(value) : String(value);
    const inp = h('input', { type: 'number', class: 'ef-num-input', value: value == null || stored != null ? '' : String(value) });
    bindControl(inp, { id, describedBy });
    if (min != null) inp.setAttribute('min', String(min));
    if (max != null) inp.setAttribute('max', String(max));
    inp.addEventListener('input', () => onChange?.(inp.value));
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); onCommit?.(inp.value); }
      else cancelOnEscape(e, onCancel);
    });
    inp.addEventListener('blur', () => onCommit?.(inp.value));
    focusOnMount(inp, autoFocus, { select: true });
    if (stored == null) return inp;
    // The input cannot hold it, so the field says what is stored and that an untouched field keeps it.
    const note = h('span', { class: 'ef-num-note', id: `ef-num-note-${++seq}` },
      `Current: ${stored} — not a number. It is kept unless you type one.`);
    inp.setAttribute('aria-describedby', [describedBy, note.id].filter(Boolean).join(' '));
    const wrap = h('div', { class: 'ef-num-edit' }, [inp, note]);
    wrap.control = inp;
    return wrap;
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
