// User intent: a task's docs are a small map of "what it is" → "where it is". They are edited as rows and saved as that
// same map — never as a list — and a row that cannot become a map entry is flagged, not silently dropped.
import { h } from '../../../util/h.js';
import { icon } from '../../icon.js';
import { bindControl, cancelOnEscape, focusOnMount } from './control.js';

const isRow = (x) => x != null && typeof x === 'object' && !Array.isArray(x) && 'key' in x;
// "Path or URL" → "path or URL": a placeholder reads as a hint, not a heading.
const hint = (label) => label.charAt(0).toLowerCase() + label.slice(1);
const text = (v) => (v == null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v)).trim();

// Rows from whatever is stored or being edited: the map itself, the editor's own rows, or a legacy list of
// "type: path" strings. Blank rows are not rows. A list entry without a type keeps its text and an empty type.
function toRows(raw) {
  if (typeof raw === 'string') return toRows([raw]);
  if (raw == null || typeof raw !== 'object') return [];
  const rows = Array.isArray(raw)
    ? raw.map((item) => {
      if (isRow(item)) return { key: text(item.key), value: text(item.value) };
      const s = text(item);
      const m = /^([\w.-]+):(?!\/\/)\s*(.+)$/.exec(s);
      return m ? { key: m[1], value: m[2] } : { key: '', value: s };
    })
    : Object.entries(raw).map(([k, v]) => ({ key: text(k), value: text(v) }));
  return rows.filter((r) => r.key || r.value);
}

// The first row that keeps these rows from being a map — its index, the part at fault ('key' or 'value') and why —
// or null when they are one. Blank rows are not rows and are skipped. A row is named by what it holds, not by a
// number: the editor's blank rows would make any count disagree with the row labels.
function fault(rows) {
  const seen = new Set();
  for (const [i, raw] of rows.entries()) {
    const r = { key: text(raw.key), value: text(raw.value) };
    if (!r.key && !r.value) continue;
    if (!r.key) return { i, part: 'key', message: `"${r.value}" needs a type` };
    if (!r.value) return { i, part: 'value', message: `"${r.key}" needs a path or URL` };
    if (seen.has(r.key)) return { i, part: 'key', message: `"${r.key}" is used twice` };
    seen.add(r.key);
  }
  return null;
}
const problem = (rows) => fault(rows)?.message ?? null;

export const KeyValueField = {
  read({ value, readOnly = false, placeholder = '' }) {
    const rows = toRows(value);
    const cls = ['ef-chips'];
    if (!readOnly) cls.push('ef-editable');
    if (!rows.length) return h('span', { class: [...cls, 'ef-placeholder'].join(' ') }, placeholder || 'none');
    return h('span', { class: cls.join(' ') }, rows.map((r) => h('span', { class: 'ef-chip' }, `${r.key || '?'}: ${r.value}`)));
  },

  // There is always a row to type in (a blank one is not an entry), so the label always has an input to reach:
  // the id goes on the first row's type input and `wrapper.control` follows it as rows come and go. The message is
  // about the rows as a whole and names the row at fault, so every row input is described by it.
  edit({ value, onChange, onCommit, onCancel, id, describedBy, autoFocus = true,
    label = 'Entries', keyLabel = 'Type', valueLabel = 'Path or URL', addLabel = 'Add row' }) {
    const rows = [];
    const list = h('div', { class: 'ef-kv-rows' });
    const add = h('button', { type: 'button', class: 'btn btn--secondary btn--sm ef-kv-add' }, [icon('plus', { size: 14 }), addLabel]);
    const wrap = h('div', { class: 'ef-kv', role: 'group', 'aria-label': label }, [list, add]);
    let bound = null;
    Object.defineProperty(wrap, 'control', { get: () => bound });

    const current = () => rows.map(({ key, value: v }) => ({ key, value: v }));
    const emit = () => onChange?.(current());

    // Row numbers, the remove buttons' names and the labelled control all follow the rows as they come and go.
    function sync() {
      rows.forEach((r, i) => {
        r.keyInput.setAttribute('aria-label', `${keyLabel}, row ${i + 1}`);
        r.valueInput.setAttribute('aria-label', `${valueLabel}, row ${i + 1}`);
        r.remove.setAttribute('aria-label', `Remove ${r.key.trim() || `row ${i + 1}`}`);
      });
      const target = rows[0].keyInput;
      if (target === bound) return;
      for (const attr of ['id', 'aria-invalid']) {
        const had = bound?.getAttribute(attr);
        bound?.removeAttribute(attr);
        if (attr === 'aria-invalid' && had != null) target.setAttribute(attr, had);
      }
      bound = bindControl(target, { id });
    }

    function addRow({ key = '', value: v = '' } = {}) {
      const keyInput = h('input', { type: 'text', class: 'ef-text-input ef-kv-key', placeholder: hint(keyLabel), autocomplete: 'off', value: key });
      const valueInput = h('input', { type: 'text', class: 'ef-text-input ef-kv-value', placeholder: hint(valueLabel), autocomplete: 'off', value: v });
      const remove = h('button', { type: 'button', class: 'btn btn--ghost btn--icon ef-kv-remove' }, icon('dismiss', { size: 14 }));
      bindControl(keyInput, { describedBy });
      bindControl(valueInput, { describedBy });
      const row = { key, value: v, keyInput, valueInput, remove, el: h('div', { class: 'ef-kv-row' }, [keyInput, valueInput, remove]) };
      keyInput.addEventListener('input', () => { row.key = keyInput.value; sync(); emit(); });
      valueInput.addEventListener('input', () => { row.value = valueInput.value; emit(); });
      remove.addEventListener('click', () => {
        const i = rows.indexOf(row);
        if (rows.length === 1) {
          // The last row is emptied, not taken away.
          row.key = row.value = keyInput.value = valueInput.value = '';
        } else {
          rows.splice(i, 1);
          row.el.remove();
        }
        sync();
        (rows[i] ?? rows[i - 1]).keyInput.focus();
        emit();
      });
      rows.push(row);
      list.appendChild(row.el);
      return row;
    }

    for (const r of toRows(value)) addRow(r);
    if (!rows.length) addRow();
    sync();

    // A form that refused the rows puts focus on the input at fault rather than on the first row.
    wrap.focusInvalid = () => {
      const at = fault(rows);
      if (!at) return false;
      rows[at.i][at.part === 'key' ? 'keyInput' : 'valueInput'].focus();
      return true;
    };

    add.addEventListener('click', () => {
      const row = addRow();
      sync();
      row.keyInput.focus();
    });
    wrap.addEventListener('keydown', (e) => {
      // Enter in a row must not press a button somewhere else in the form.
      if (e.key === 'Enter' && e.target.tagName === 'INPUT') e.preventDefault();
      else cancelOnEscape(e, onCancel);
    });
    wrap.addEventListener('focusout', (e) => {
      if (!wrap.contains(e.relatedTarget)) onCommit?.(current());
    });
    focusOnMount(bound, autoFocus);
    return wrap;
  },

  // A map when the rows make one; otherwise the rows themselves, so validate can say what is wrong with them.
  coerce(raw) {
    const rows = toRows(raw);
    return problem(rows) ? rows : Object.fromEntries(rows.map((r) => [r.key, r.value]));
  },

  validate(value, { required = false } = {}) {
    const rows = toRows(value);
    const wrong = problem(rows);
    if (wrong) return wrong;
    return required && !rows.length ? 'required' : null;
  },
};
