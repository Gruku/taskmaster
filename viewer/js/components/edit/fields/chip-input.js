// viewer/js/components/edit/fields/chip-input.js
import { h } from '../../../util/h.js';
import { icon } from '../../icon.js';
import { bindControl, cancelOnEscape, focusOnMount } from './control.js';
import { openPopover } from '../../popover.js';
import { marker } from '../../status.js';

const MAX_DROPDOWN = 8;
let seq = 0;

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

  // The id goes on the inner text input, which is `wrapper.control`. `label` names the suggestion list.
  edit({ value, source, onChange, onCommit, onCancel, allowFree = false, placeholder = 'add…', id, describedBy, autoFocus = true, label }) {
    const draft = Array.isArray(value) ? [...value] : [];
    const wrap = h('div', { class: 'ef-chip-input' });
    const chipsBox = h('div', { class: 'ef-chip-list' });
    const inputBox = h('div', { class: 'ef-chip-input-row' });
    // A combobox: the suggestions are a list it controls, and the highlighted one is announced while focus stays here.
    const input = h('input', {
      type: 'text', class: 'ef-chip-input-text', placeholder, autocomplete: 'off',
      role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': 'false',
    });
    bindControl(input, { id, describedBy });
    wrap.control = input;
    // Text typed but not yet a chip. A form counts it as an edit; where entries must come from the list it cannot be
    // kept as it stands, and `pendingError` says so.
    Object.defineProperty(wrap, 'pending', { get: () => input.value.trim() });
    wrap.pendingError = allowFree ? null : 'pick an entry from the list, or clear the text';
    inputBox.appendChild(input);
    wrap.appendChild(chipsBox);
    wrap.appendChild(inputBox);

    const optionId = `ef-chip-${++seq}-option`;
    let list = null;      // the open suggestion popover
    let highlighted = -1;
    let suggestions = [];

    function paintChips() {
      chipsBox.replaceChildren(...draft.map((v) => {
        const chip = h('span', { class: 'ef-chip' });
        chip.appendChild(h('span', { class: 'ef-chip-label' }, _displayLabel(v)));
        const x = h('button', { type: 'button', class: 'ef-chip-x', 'aria-label': `Remove ${_displayLabel(v)}` }, icon('dismiss', { size: 12 }));
        x.addEventListener('click', (e) => {
          e.preventDefault();
          const i = draft.indexOf(v);
          if (i >= 0) {
            draft.splice(i, 1);
            paintChips();
            // The pressed button is gone: focus stays in the field, on the chip that took its place, else the one
            // before it, else the text input.
            const xs = chipsBox.querySelectorAll('.ef-chip-x');
            (xs[i] ?? xs[i - 1] ?? input).focus();
            onChange?.([...draft]);
          }
        });
        chip.appendChild(x);
        return chip;
      }));
    }
    paintChips();

    // However the list closes (a pick, Escape, a press elsewhere, the field taken away), nothing is left highlighted.
    function forget() {
      list = null;
      suggestions = [];
      highlighted = -1;
      input.removeAttribute('aria-activedescendant');
    }

    function closeList() {
      if (list) list.close();
      else forget();
    }

    function highlight(i) {
      highlighted = i;
      const rows = list.el.querySelectorAll('[role="option"]');
      rows.forEach((r, idx) => {
        r.classList.toggle('ef-chip-dd-active', idx === i);
        r.setAttribute('aria-selected', String(idx === i));
      });
      input.setAttribute('aria-activedescendant', rows[i].id);
      // A list taller than the popover allows scrolls inside itself; the highlighted row stays in sight.
      rows[i].scrollIntoView?.({ block: 'nearest' });
    }

    async function refreshDropdown() {
      const q = input.value.trim();
      if (!q) { closeList(); return; }
      let raw = [];
      try { raw = (await source?.(q)) || []; } catch (e) { raw = []; }
      // An answer to a query that has since been changed or abandoned, or that arrives after focus left the field (or
      // the field was taken away), must not reopen the list.
      if (q !== input.value.trim() || input.ownerDocument.activeElement !== input) return;
      // Filter out already-chosen items.
      const next = raw.filter(s => !draft.some(d => _val(d) === _val(s))).slice(0, MAX_DROPDOWN);
      if (!next.length) { closeList(); return; }
      const rows = next.map((s, i) => {
        // One line per suggestion; the full text is the tooltip when it had to be cut.
        const row = h('div', { class: 'ef-chip-dd-row', role: 'option', id: `${optionId}-${i}`, title: _displayLabel(s) });
        row.appendChild(h('span', { class: 'ef-chip-dd-val' }, _displayLabel(s)));
        // A status is the shared shape plus word ({ label, shape, tone }); any other hint is plain text.
        if (s.marker) row.appendChild(h('span', { class: 'ef-chip-dd-hint' }, marker(s.marker)));
        else if (s.hint) row.appendChild(h('span', { class: 'ef-chip-dd-hint' }, s.hint));
        // Picked on the press, which keeps focus in the input.
        row.addEventListener('mousedown', (e) => { e.preventDefault(); commitChoice(s); });
        return row;
      });
      if (list) {
        // Typing on redraws the open list rather than opening another.
        list.el.replaceChildren(...rows);
        list.reposition();
      } else {
        const handle = openPopover({
          anchor: input, content: rows, role: 'listbox', label: label ? `${label} suggestions` : 'Suggestions', focus: 'none',
          minWidth: 'anchor', className: 'ef-chip-dropdown',
          onClose: () => { if (list === handle) forget(); },
        });
        list = handle;
      }
      suggestions = next;
      highlight(0);
    }

    function commitChoice(s) {
      draft.push(_val(s));
      paintChips();
      input.value = '';
      closeList();
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
        highlight(Math.min(highlighted + 1, suggestions.length - 1));
      } else if (e.key === 'ArrowUp' && suggestions.length) {
        e.preventDefault();
        highlight(Math.max(highlighted - 1, 0));
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (highlighted >= 0 && suggestions[highlighted]) commitChoice(suggestions[highlighted]);
        else if (allowFree) commitFree();
      } else if (e.key === 'Tab' && suggestions.length) {
        e.preventDefault();
        commitChoice(suggestions[highlighted >= 0 ? highlighted : 0]);
      } else if (e.key === 'Escape') {
        // The key is this field's while it has something of its own to close: the list, or a half-typed entry.
        if (input.value || list) { e.preventDefault(); input.value = ''; closeList(); }
        else cancelOnEscape(e, onCancel);
      } else if (e.key === 'Backspace' && !input.value && draft.length) {
        e.preventDefault();
        draft.pop();
        paintChips();
        onChange?.([...draft]);
      }
    });
    input.addEventListener('blur', () => {
      // Text typed where free entries are allowed is an entry the user meant: it becomes a chip rather than being
      // dropped by a Save pressed straight after typing.
      if (allowFree) commitFree();
      closeList();
      // Slight delay so a mousedown on dropdown row still fires.
      setTimeout(() => onCommit?.([...draft]), 80);
    });
    focusOnMount(input, autoFocus);
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
