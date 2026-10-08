// User intent: an estimate is picked, not typed from memory — a size (S, M, L) or a whole number of days, never both —
// so the board never carries "medium-ish" or "3 d" as an estimate.
import { h } from '../../../util/h.js';
import { bindControl, cancelOnEscape, focusOnMount } from './control.js';

const SIZES = ['S', 'M', 'L'];
const DAYS_RE = /^([1-9]\d*)d$/;
const MESSAGE = 'use S, M, L or a whole number of days';
// What the days input shows: the number of any '<number>d' value, valid or not, so a refused value stays visible.
const TYPED_DAYS_RE = /^(-?\d+(?:\.\d+)?)d$/;
let seq = 0;

// The stored forms are 'S' | 'M' | 'L' | '<n>d'. Anything else comes back as text so validate can refuse it.
function coerce(raw) {
  if (raw == null) return null;
  if (typeof raw === 'number') return Number.isInteger(raw) && raw > 0 ? `${raw}d` : String(raw);
  if (typeof raw !== 'string') return typeof raw === 'object' ? JSON.stringify(raw) : String(raw);
  const text = raw.trim();
  if (text === '') return null;
  if (/^[sml]$/i.test(text)) return text.toUpperCase();
  const days = /^0*(\d+)d$/i.exec(text);
  return days ? `${days[1]}d` : text;
}

export const EstimateField = {
  read({ value, readOnly = false, placeholder = '' }) {
    const v = coerce(value);
    const cls = ['ef-estimate'];
    if (!readOnly) cls.push('ef-editable');
    if (v == null) cls.push('ef-placeholder');
    return h('span', { class: cls.join(' ') }, v == null ? (placeholder || '—') : v);
  },

  // The id goes on the days input: a label click on a size button would press it.
  edit({ value, onChange, onCommit, onCancel, id, describedBy, autoFocus = true, label = 'Estimate' }) {
    const initial = coerce(value);
    let current = initial;
    const buttons = SIZES.map((size) => h('button', { type: 'button', class: 'ef-estimate-size', 'aria-pressed': 'false' }, size));
    const days = h('input', { type: 'number', class: 'ef-estimate-days-input', min: '1', step: '1', inputmode: 'numeric' });
    bindControl(days, { id, describedBy });
    // A stored estimate that is neither a size nor a day count ("2 weeks") cannot be picked here, but it must not be
    // lost by opening the form: it stays as a choice of its own, pressed until another is made, and can be put back.
    const stored = current != null && !SIZES.includes(current) && !TYPED_DAYS_RE.test(current) ? current : null;
    const custom = stored == null ? null
      : h('button', { type: 'button', class: 'ef-estimate-size ef-estimate-custom', 'aria-pressed': 'false', title: stored }, stored);
    const wrap = h('div', { class: 'ef-estimate-edit', role: 'group', 'aria-label': label }, [
      h('span', { class: 'ef-estimate-sizes' }, buttons),
      h('label', { class: 'ef-estimate-days' }, [days, h('span', {}, 'days')]),
      custom,
      custom && h('span', { class: 'ef-estimate-note' }, `Current: ${stored} — not a size or a day count. It is kept unless you pick another.`),
    ]);
    // Shown by the field itself: an entry the browser cannot read as a number never becomes a value a form could judge.
    const unreadable = h('div', { class: 'ef-estimate-unreadable', id: `ef-estimate-unreadable-${++seq}` });
    wrap.appendChild(unreadable);
    // While it shows, the days input is described by it as well as by the form's own message.
    const describe = (bad) => {
      const ids = [describedBy, bad && unreadable.id].filter(Boolean).join(' ');
      if (ids) days.setAttribute('aria-describedby', ids);
      else days.removeAttribute('aria-describedby');
    };
    wrap.control = days;

    function paint({ keepDays = false } = {}) {
      buttons.forEach((b, i) => b.setAttribute('aria-pressed', String(current === SIZES[i])));
      custom?.setAttribute('aria-pressed', String(current === stored));
      if (!keepDays) days.value = TYPED_DAYS_RE.exec(current ?? '')?.[1] ?? '';
    }
    paint();

    custom?.addEventListener('click', () => {
      current = current === stored ? null : stored;
      paint();
      onChange?.(current);
      onCommit?.(current);
    });

    buttons.forEach((b, i) => b.addEventListener('click', () => {
      current = current === SIZES[i] ? null : SIZES[i];
      paint();
      onChange?.(current);
      onCommit?.(current);
    }));
    days.addEventListener('input', () => {
      // A lone "-" or "e" reads as an empty value. It is not a request to clear the estimate: nothing changes, and
      // the field says what it expects.
      const bad = days.validity?.badInput === true;
      unreadable.textContent = bad ? MESSAGE.charAt(0).toUpperCase() + MESSAGE.slice(1) : '';
      describe(bad);
      if (bad) return;
      // What was typed is passed on even when it is not a valid day count, so the form can say so.
      const typed = days.value.trim();
      // An empty days input clears a day count. Beside a size or a kept stored value it was empty all along.
      if (typed === '' && !TYPED_DAYS_RE.test(current ?? '')) return;
      current = typed === '' ? null : `${typed}d`;
      paint({ keepDays: true });
      onChange?.(current);
    });
    // Leaving the field with nothing changed is not a commit: the stored form (a bare 3, a lower-case "m") would go
    // out normalised ("3d", "M") and be written back as an edit nobody made. An inline edit is simply closed.
    const leave = () => { if (current === initial) onCancel?.(); else onCommit?.(current); };
    wrap.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target === days) { e.preventDefault(); leave(); }
      else cancelOnEscape(e, onCancel);
    });
    // Moving between the sizes and the days stays inside the field; only leaving it commits.
    wrap.addEventListener('focusout', (e) => {
      if (e.target === days && !wrap.contains(e.relatedTarget)) leave();
    });
    focusOnMount(days, autoFocus, { select: true });
    return wrap;
  },

  coerce,

  validate(value, { required = false } = {}) {
    const v = coerce(value);
    if (v == null) return required ? 'required' : null;
    return SIZES.includes(v) || DAYS_RE.test(v) ? null : MESSAGE;
  },
};
