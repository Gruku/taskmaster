// User intent: creating and editing a task is one honest form on the shared modal — grouped and labelled, dirty only
// when something really changed, silent until the user has had a chance to type, and saving only what was changed so
// a field nobody touched can never be rewritten.
import { h } from '../../util/h.js';
import { icon } from '../icon.js';
import { openModal, confirmDialog } from '../modal.js';
import { runValidation } from './schema.js';
import { normal, sameValue } from './same-value.js';
import { describeWriteError } from './write-errors.js';

const GROUPS = [['basics', 'Basics'], ['tracking', 'Tracking'], ['relations', 'Relations'], ['content', 'Content']];
let seq = 0;

function sentence(spec, error) {
  if (error === 'required') return `${spec.label || spec.key} is required`;
  return error.charAt(0).toUpperCase() + error.slice(1);
}

function contentHint(value) {
  const body = value == null ? '' : String(value);
  if (!body) return '';
  const lines = body.split('\n').length;
  return lines > 1 ? `${lines} lines` : `${body.length} character${body.length === 1 ? '' : 's'}`;
}

// `onSave(draft, { changes })` — `changes` holds only the fields whose value differs from the one the form opened
// with; `draft` is the stored entity with those changes applied, so untouched fields keep exactly what was stored.
// It answers nothing on success (the form closes) or an object to stay open: `error` is shown in the footer, and
// `wait` (a promise of the next answer) holds the form disabled until something outside it — the conflict banner —
// has been dealt with. A throw, or a `wait` that rejects, is worded by describeWriteError.
export function openEntityModal({ schema, mode, initialEntity, onSave, onCancel, onClose,
                                 title, eyebrow, saveLabel = 'Save' }) {
  const initial = initialEntity || {};
  const create = mode === 'create';
  const uid = `eform-${++seq}`;
  const noun = String(schema.label || schema.entity || 'item').toLowerCase();

  let attempted = false;   // a save was tried: every message shows
  let busy = false;        // saving, or held by `wait`
  let closed = false;
  let frozen = [];         // controls disabled for the busy period
  let focusBack = null;

  const modal = openModal({
    title: title ?? `${create ? 'Create' : 'Edit'} ${noun}`,
    eyebrow: eyebrow ?? (create ? '' : initial.id),
    className: 'modal--form',
    initialFocus: () => controlOf(fields[0]),
    onRequestClose: () => {
      if (busy) return false;
      if (!isDirty()) { onCancel?.(); return true; }
      return confirmDialog({
        title: 'Discard changes?', message: `Your edits to this ${noun} will be lost.`,
        confirmLabel: 'Discard', cancelLabel: 'Keep editing', tone: 'critical',
      }).then((discard) => { if (discard) onCancel?.(); return discard; });
    },
  });
  modal.onClosed(() => { closed = true; onClose?.(); });

  // ── Fields ──
  const fields = (schema.fields || []).map((spec) => {
    const coerce = spec.renderer.coerce ?? ((v) => v);
    const value = coerce(initial[spec.key]);
    return { spec, key: spec.key, coerce, value, snapshot: value, touched: false };
  });
  const controlOf = (f) => f?.el.control ?? f?.el;
  const changed = (f) => !sameValue(f.value, f.snapshot);
  // Text still being typed into a field (a chip input's entry) is an edit too, though it is not a value yet.
  const pending = (f) => !!f.el.pending;
  const isDirty = () => fields.some((f) => changed(f) || pending(f));

  function mountControl(f, id) {
    f.errEl = h('div', { class: 'ef-error', id: `${id}-error` });
    f.el = f.spec.renderer.edit({
      ...f.spec,
      value: initial[f.key],
      onChange: (v) => set(f, v),
      onCommit: (v) => set(f, v),
      // No onCancel: Escape in a field with nothing of its own to close belongs to the modal.
      id, describedBy: f.errEl.id, autoFocus: false, entityId: initial.id,
    });
  }

  function fieldBlock(f) {
    const id = `${uid}-${f.key}`;
    mountControl(f, id);
    const label = h('label', { class: 'eform-label', for: id }, f.spec.label || f.key);
    if (f.spec.required) label.appendChild(h('span', { class: 'eform-required' }, 'required'));
    f.wrap = h('div', { class: `eform-field${f.spec.wide ? ' eform-field--wide' : ''}`, 'data-key': f.key }, [label, f.el, f.errEl]);
    return f.wrap;
  }

  // A Content field is a section that folds to its heading, so six documents do not bury the form.
  function sectionBlock(f) {
    const id = `${uid}-${f.key}`;
    mountControl(f, id);
    f.hint = h('span', { class: 'eform-section-hint' });
    f.toggle = h('button', { type: 'button', class: 'eform-section-toggle', 'aria-controls': `${id}-panel` },
      [icon('chevron', { size: 16 }), h('span', { class: 'eform-section-name' }, f.spec.label || f.key), f.hint]);
    // The heading names the section to the eye; the control still gets a label of its own.
    f.panel = h('div', { class: 'eform-section-panel', id: `${id}-panel` },
      [h('label', { class: 'eform-sr', for: id }, f.spec.label || f.key), f.el, f.errEl]);
    f.toggle.addEventListener('click', (e) => {
      expand(f, f.panel.hidden);
      // A pointer opened it to write in it; a keyboard user stays on the heading and Tabs in.
      if (!f.panel.hidden && e.detail > 0) controlOf(f).focus();
    });
    // A new item opens on the section its schema marks `open` — where that kind of item is mostly written.
    expand(f, f.value != null || (create && !!f.spec.open));
    f.wrap = h('div', { class: 'eform-section', 'data-key': f.key }, [h('h4', { class: 'eform-section-head' }, f.toggle), f.panel]);
    return f.wrap;
  }

  function expand(f, open) {
    f.panel.hidden = !open;
    f.toggle.setAttribute('aria-expanded', String(open));
    f.hint.textContent = open ? '' : contentHint(f.value);
  }

  const known = new Set(GROUPS.map(([name]) => name));
  const form = h('div', { class: 'eform' });
  for (const [name, title] of [[null, null], ...GROUPS]) {
    const members = fields.filter((f) => (known.has(f.spec.group) ? f.spec.group : null) === name);
    if (!members.length) continue;
    const headingId = `${uid}-group-${name ?? 'main'}`;
    const layout = name === 'content' ? 'eform-sections' : name === 'relations' ? 'eform-stack' : 'eform-grid';
    form.appendChild(h('section', { class: 'eform-group', 'data-group': name ?? 'main', 'aria-labelledby': title ? headingId : null }, [
      title && h('h3', { class: 'eform-group-title', id: headingId }, title),
      h('div', { class: layout }, members.map(name === 'content' ? sectionBlock : fieldBlock)),
    ]));
  }
  modal.body.appendChild(form);

  // ── Footer ──
  const summary = h('div', { class: 'eform-summary ef-error', role: 'status' });
  const alert = h('div', { class: 'eform-summary ef-error', role: 'alert' });
  const cancelBtn = h('button', { type: 'button', class: 'btn btn--secondary', 'data-cancel': '' }, 'Cancel');
  const saveBtn = h('button', { type: 'button', class: 'btn btn--primary', 'data-save': '', disabled: '' }, saveLabel);
  modal.footer.append(h('div', { class: 'eform-messages' }, [summary, alert]), h('div', { class: 'eform-actions' }, [cancelBtn, saveBtn]));
  cancelBtn.addEventListener('click', () => { modal.requestClose(); });
  saveBtn.addEventListener('click', () => { save(); });
  // Typing that is not a value yet (see `pending`) still changes whether there is anything to save.
  form.addEventListener('input', () => { if (!busy) paint(); });

  // ── State ──
  function set(f, raw) {
    if (closed || busy) return;
    const next = f.coerce(raw);
    if (JSON.stringify(next) === JSON.stringify(f.value)) return;
    f.value = next;
    alert.textContent = '';
    paint();
  }

  const changes = () => Object.fromEntries(fields.filter(changed).map((f) => [f.key, f.value]));
  const draft = () => ({ ...initial, ...changes() });

  // A stored task may hold values this form would refuse today (a renamed status, a free-text estimate). In edit, a
  // field the user did not change is not sent, so it is not judged — except that a required field left empty is
  // still an error. A field the user changed is validated in full. A new item is sent whole, so every field of it is.
  function errors() {
    const all = runValidation({ ...initial, ...Object.fromEntries(fields.map((f) => [f.key, f.value])) }, schema).errors;
    const out = {};
    for (const f of fields) {
      if (create || changed(f)) { if (all[f.key]) out[f.key] = all[f.key]; }
      else if (f.spec.required && normal(f.value) == null) out[f.key] = 'required';
      // Text the field cannot keep would be dropped by the save without a word.
      if (pending(f) && f.el.pendingError) out[f.key] = f.el.pendingError;
    }
    return out;
  }

  function paint() {
    if (closed) return;
    const errs = errors();
    for (const f of fields) {
      const message = (attempted || f.touched) && errs[f.key] ? sentence(f.spec, errs[f.key]) : '';
      f.errEl.textContent = message;
      for (const el of f.wrap.querySelectorAll('[aria-invalid]')) el.removeAttribute('aria-invalid');
      // A field made of several inputs (docs rows) marks the ones at fault itself.
      if (f.el.markInvalid) f.el.markInvalid(!!message);
      else if (message) controlOf(f).setAttribute('aria-invalid', 'true');
      if (f.hint && f.panel.hidden) f.hint.textContent = contentHint(f.value);
    }
    const count = Object.keys(errs).length;
    summary.textContent = attempted && count ? `${count} field${count === 1 ? ' needs' : 's need'} attention` : '';
    saveBtn.disabled = busy || !isDirty();
  }

  // A field is judged once the user has moved on from it to anywhere else in the dialog — another field, or blank
  // space (a click there puts focus on the dialog itself). Leaving for a header or footer button is not that: the
  // button's own action (save, cancel, close) says what happens next. Nor is leaving the window.
  // When the move is a click, the message waits for the release: appearing mid-press it would push the control
  // being clicked out from under the pointer, and the click would land on nothing.
  const movedOn = (to) => !!to && modal.dialog.contains(to) && !modal.header.contains(to) && !modal.footer.contains(to);
  for (const f of fields) {
    f.wrap.addEventListener('focusout', (e) => {
      if (busy || f.touched || f.wrap.contains(e.relatedTarget) || !movedOn(e.relatedTarget)) return;
      f.touched = true;
      modal.afterPress(paint);
    });
  }

  function setBusy(on) {
    if (on === busy) return;
    busy = on;
    if (on) {
      focusBack = modal.dialog.contains(document.activeElement) ? document.activeElement : null;
      // Focus rests on the dialog itself, so a key pressed meanwhile still reaches the modal and not the page.
      modal.dialog.focus();
      frozen = [...modal.dialog.querySelectorAll('button, input, select, textarea')].filter((el) => !el.disabled);
      for (const el of frozen) el.disabled = true;
      // With every control disabled the body is the one thing Tab can reach, so it can still be scrolled from the
      // keyboard — for as long as the conflict banner holds the form, not only for the moment of a save.
      modal.body.tabIndex = 0;
    } else {
      modal.body.removeAttribute('tabindex');
      for (const el of frozen) el.disabled = false;
      frozen = [];
      paint();
      if (!closed && modal.isTop() && focusBack?.isConnected && !focusBack.disabled) focusBack.focus();
    }
  }

  function focusFirstInvalid(errs) {
    const f = fields.find((x) => errs[x.key]);
    if (!f) return;
    if (f.panel?.hidden) expand(f, true);
    // A field made of several inputs (docs rows) can point at the one at fault.
    if (!f.el.focusInvalid?.()) controlOf(f).focus();
  }

  // Some fields hold an entry until they are left (text typed into a chip input). A save from the keyboard never
  // leaves the field, so it is made to hand over what it holds first.
  function flushActiveField() {
    const active = document.activeElement;
    if (!active || !form.contains(active)) return;
    active.blur();
    active.focus();
  }

  async function save() {
    if (busy || closed) return;
    flushActiveField();
    if (!isDirty()) return;
    attempted = true;
    paint();
    const errs = errors();
    if (Object.keys(errs).length) { focusFirstInvalid(errs); return; }
    alert.textContent = '';
    const payload = [draft(), { changes: changes() }];
    setBusy(true);
    saveBtn.textContent = 'Saving…';
    let answer;
    try { answer = await onSave(...payload); } catch (err) { answer = { error: describeWriteError(err, { noun }) }; }
    saveBtn.textContent = saveLabel;
    while (answer && typeof answer === 'object') {
      alert.textContent = answer.error || '';
      if (!answer.wait) { setBusy(false); return; }
      try { answer = await answer.wait; } catch (err) { answer = { error: describeWriteError(err, { noun }) }; }
    }
    modal.close();
  }

  // Ctrl/⌘+Enter saves from anywhere in the dialog. Taken on the way down, so a textarea that uses the same chord
  // to commit an inline edit does not also act on it.
  modal.onKey((e) => {
    if (e.key !== 'Enter' || !(e.ctrlKey || e.metaKey) || e.isComposing) return false;
    save();
    return true;
  });

  paint();
  return () => modal.close();
}
