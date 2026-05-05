// viewer/js/components/edit/entity-modal.js
// Centered-overlay modal for entity creation + full-edit. See spec §UX patterns
// for lifecycle. Mounts into #entity-modal-host (added to viewer/index.html).

import { h } from '../../util/h.js';
import { runValidation } from './schema.js';

const HOST_ID = 'entity-modal-host';

export function openEntityModal({ schema, mode, initialEntity, onSave, onCancel }) {
  const host = document.getElementById(HOST_ID);
  if (!host) throw new Error(`#${HOST_ID} not found in DOM`);

  // Local draft buffer (modal flow = NO autosave per field).
  const draft = { ...(initialEntity || {}) };
  let saving = false;
  let serverError = null;

  const root = h('div', { class: 'em-overlay', tabindex: '-1' });
  const modal = h('div', { class: 'em-modal', role: 'dialog', 'aria-modal': 'true' });

  // Header
  const header = h('div', { class: 'em-header' }, [
    h('span', { class: 'em-title' }, `${mode === 'create' ? 'Create' : 'Edit'} ${schema.label || schema.entity}`),
    h('button', { type: 'button', class: 'em-close', 'aria-label': 'close',
                  on: { click: () => doCancel() } }, '✕'),
  ]);

  // Body — flat field list for now; Task 13 introduces grouped layouts.
  const body = h('div', { class: 'em-body' });
  const fieldEls = new Map(); // key → { wrap, errEl }

  for (const f of schema.fields || []) {
    const wrap = h('div', { class: 'em-field', 'data-key': f.key }, [
      h('label', { class: 'em-label' }, f.label || f.key),
    ]);
    const renderer = f.renderer;
    const editEl = renderer.edit({
      value: draft[f.key],
      onChange: (v) => { draft[f.key] = renderer.coerce ? renderer.coerce(v) : v; repaintFooter(); },
      onCommit: (v) => { draft[f.key] = renderer.coerce ? renderer.coerce(v) : v; repaintFooter(); },
      onCancel: () => {},
      ...f, // pass through options/min/max/maxLength/etc.
      getBacklog: f.getBacklog, // for relation pickers
    });
    wrap.appendChild(editEl);
    const errEl = h('div', { class: 'em-field-error' });
    wrap.appendChild(errEl);
    body.appendChild(wrap);
    fieldEls.set(f.key, { wrap, errEl });
  }

  // Footer
  const errSummary = h('div', { class: 'em-error-summary' });
  const cancelBtn = h('button', { type: 'button', class: 'em-cancel',
                                  on: { click: () => doCancel() } }, 'Cancel');
  const saveBtn = h('button', { type: 'button', class: 'em-save', disabled: '',
                                on: { click: () => doSave() } }, 'Save');
  const footer = h('div', { class: 'em-footer' }, [
    errSummary,
    h('div', { class: 'em-footer-actions' }, [cancelBtn, saveBtn]),
  ]);

  modal.appendChild(header);
  modal.appendChild(body);
  modal.appendChild(footer);
  root.appendChild(modal);
  host.appendChild(root);
  document.body.classList.add('em-open');

  function repaintFooter() {
    const { valid, errors } = runValidation(draft, schema);
    serverError = null;
    saveBtn.disabled = saving || !valid || !isDirty();
    // Per-field error rendering
    for (const [key, { errEl }] of fieldEls.entries()) {
      errEl.textContent = errors[key] || '';
      errEl.style.display = errors[key] ? '' : 'none';
    }
    const errCount = Object.keys(errors).length;
    errSummary.textContent = errCount === 0 ? '' : `${errCount} field${errCount > 1 ? 's' : ''} need attention`;
  }

  function isDirty() {
    const init = initialEntity || {};
    for (const f of schema.fields || []) {
      const a = init[f.key];
      const b = draft[f.key];
      if (JSON.stringify(a ?? null) !== JSON.stringify(b ?? null)) return true;
    }
    return false;
  }

  async function doSave() {
    if (saving) return;
    const { valid } = runValidation(draft, schema);
    if (!valid) return;
    saving = true; saveBtn.disabled = true; saveBtn.textContent = 'Saving…';
    try {
      const result = await onSave({ ...draft });
      if (result && result.error) {
        serverError = result.error;
        errSummary.textContent = result.error;
        saving = false; saveBtn.disabled = false; saveBtn.textContent = 'Save';
        return;
      }
      doClose();
    } catch (e) {
      serverError = e.message || String(e);
      errSummary.textContent = serverError;
      saving = false; saveBtn.disabled = false; saveBtn.textContent = 'Save';
    }
  }

  function doCancel() {
    if (isDirty() && !window.confirm('Discard changes?')) return;
    onCancel?.();
    doClose();
  }

  function doClose() {
    root.remove();
    document.body.classList.remove('em-open');
    document.removeEventListener('keydown', onKeyDown);
  }

  function onKeyDown(e) {
    if (e.key === 'Escape') { e.preventDefault(); doCancel(); }
  }

  // Backdrop click cancels (with confirm if dirty).
  root.addEventListener('click', (e) => { if (e.target === root) doCancel(); });
  document.addEventListener('keydown', onKeyDown);

  // Focus first input after mount.
  queueMicrotask(() => {
    const firstInput = body.querySelector('input, textarea, select');
    firstInput?.focus();
    repaintFooter();
  });

  return doClose;
}
