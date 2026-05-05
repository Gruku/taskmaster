// viewer/js/components/edit/fields/chip-input.js
import { h } from '../../../util/h.js';

const MAX_DROPDOWN = 8;

export const ChipInput = {
  read({ value, readOnly = false, placeholder = '' }) {
    const items = Array.isArray(value) ? value : [];
    if (items.length === 0) {
      const cls = ['ef-chips', 'ef-placeholder'];
      if (!readOnly) cls.push('ef-editable');
      return h('span', { class: cls.join(' ') }, placeholder || 'none');
    }
    const cls = ['ef-chips']; if (!readOnly) cls.push('ef-editable');
    const wrap = h('span', { class: cls.join(' ') });
    for (const v of items) {
      wrap.appendChild(h('span', { class: 'ef-chip' }, _displayLabel(v)));
    }
    return wrap;
  },

  edit({ value, source, onChange, onCommit, onCancel, allowFree = false, placeholder = 'add…' }) {
    const draft = Array.isArray(value) ? [...value] : [];
    const wrap = h('div', { class: 'ef-chip-input' });
    const chipsBox = h('div', { class: 'ef-chip-list' });
    const inputBox = h('div', { class: 'ef-chip-input-row' });
    const input = h('input', { type: 'text', class: 'ef-chip-input-text', placeholder });
    const dropdown = h('div', { class: 'ef-chip-dropdown', style: 'display:none' });
    inputBox.appendChild(input);
    inputBox.appendChild(dropdown);
    wrap.appendChild(chipsBox);
    wrap.appendChild(inputBox);

    let highlighted = -1;
    let suggestions = [];

    function paintChips() {
      chipsBox.replaceChildren(...draft.map((v) => {
        const chip = h('span', { class: 'ef-chip' });
        chip.appendChild(h('span', { class: 'ef-chip-label' }, _displayLabel(v)));
        const x = h('button', { type: 'button', class: 'ef-chip-x', 'aria-label': 'remove' }, '✕');
        x.addEventListener('click', (e) => {
          e.preventDefault();
          const i = draft.indexOf(v);
          if (i >= 0) {
            draft.splice(i, 1);
            paintChips();
            onChange?.([...draft]);
          }
        });
        chip.appendChild(x);
        return chip;
      }));
    }
    paintChips();

    async function refreshDropdown() {
      const q = input.value.trim();
      if (!q) { dropdown.style.display = 'none'; suggestions = []; return; }
      let raw = [];
      try { raw = (await source(q)) || []; } catch (e) { raw = []; }
      // Filter out already-chosen items.
      suggestions = raw.filter(s => !draft.some(d => _val(d) === _val(s))).slice(0, MAX_DROPDOWN);
      if (!suggestions.length) { dropdown.style.display = 'none'; return; }
      dropdown.replaceChildren(...suggestions.map((s, i) => {
        const row = h('div', { class: 'ef-chip-dd-row' + (i === 0 ? ' ef-chip-dd-active' : '') });
        row.appendChild(h('span', { class: 'ef-chip-dd-val' }, _displayLabel(s)));
        if (s.hint) row.appendChild(h('span', { class: 'ef-chip-dd-hint' }, s.hint));
        row.addEventListener('mousedown', (e) => { e.preventDefault(); commitChoice(s); });
        return row;
      }));
      highlighted = 0;
      dropdown.style.display = '';
    }

    function commitChoice(s) {
      draft.push(_val(s));
      paintChips();
      input.value = '';
      dropdown.style.display = 'none';
      suggestions = [];
      onChange?.([...draft]);
      input.focus();
    }

    function commitFree() {
      const v = input.value.trim();
      if (!v) return;
      if (draft.includes(v)) { input.value = ''; return; }
      draft.push(v);
      paintChips();
      input.value = '';
      onChange?.([...draft]);
    }

    input.addEventListener('input', refreshDropdown);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown' && suggestions.length) {
        e.preventDefault();
        highlighted = Math.min(highlighted + 1, suggestions.length - 1);
        _paintHighlight(dropdown, highlighted);
      } else if (e.key === 'ArrowUp' && suggestions.length) {
        e.preventDefault();
        highlighted = Math.max(highlighted - 1, 0);
        _paintHighlight(dropdown, highlighted);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (highlighted >= 0 && suggestions[highlighted]) commitChoice(suggestions[highlighted]);
        else if (allowFree) commitFree();
      } else if (e.key === 'Tab' && suggestions.length) {
        e.preventDefault();
        commitChoice(suggestions[highlighted >= 0 ? highlighted : 0]);
      } else if (e.key === 'Escape') {
        e.preventDefault();
        if (input.value) { input.value = ''; dropdown.style.display = 'none'; }
        else onCancel?.();
      } else if (e.key === 'Backspace' && !input.value && draft.length) {
        e.preventDefault();
        draft.pop();
        paintChips();
        onChange?.([...draft]);
      }
    });
    input.addEventListener('blur', () => {
      // Slight delay so a mousedown on dropdown row still fires.
      setTimeout(() => onCommit?.([...draft]), 80);
    });
    queueMicrotask(() => input.focus());
    return wrap;
  },

  coerce(raw) {
    if (!Array.isArray(raw)) return [];
    const out = [];
    const seen = new Set();
    for (const v of raw) {
      const t = typeof v === 'string' ? v.trim() : _val(v);
      if (t && !seen.has(t)) { seen.add(t); out.push(t); }
    }
    return out;
  },

  validate(value, { required = false, minCount = null } = {}) {
    const arr = Array.isArray(value) ? value : [];
    if (required && arr.length === 0) return 'required';
    if (minCount != null && arr.length < minCount) return `need at least ${minCount}`;
    return null;
  },
};

function _val(s) { return typeof s === 'string' ? s : (s && s.value); }
function _displayLabel(s) { return typeof s === 'string' ? s : (s && (s.label || s.value)) || ''; }
function _paintHighlight(dropdown, i) {
  const rows = dropdown.querySelectorAll('.ef-chip-dd-row');
  rows.forEach((r, idx) => r.classList.toggle('ef-chip-dd-active', idx === i));
}
