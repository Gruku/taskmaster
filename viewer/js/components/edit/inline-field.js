// viewer/js/components/edit/inline-field.js
// Read↔edit wrapper for one field on one entity. Used by detail screens.

import { h } from '../../util/h.js';
import { fieldByKey, isSystemManaged } from './schema.js';
import { store } from '../../store.js';

const DEBOUNCE_MS = 600;

export function mountInlineField(parent, {
  schema, fieldKey, entity, onSave,
  readOnly = false, getBacklog,
}) {
  const fieldSpec = fieldByKey(schema, fieldKey);
  if (!fieldSpec) throw new Error(`field ${fieldKey} not in schema`);
  const renderer = fieldSpec.renderer;
  // System-managed fields never get an edit affordance.
  const ro = readOnly || isSystemManaged(fieldKey, schema);

  let currentEntity = { ...(entity || {}) };
  let mode = 'read';
  let pendingValue = currentEntity[fieldKey];
  let saveTimer = null;
  let inFlight = null;
  let disposed = false;
  let conflicted = false;
  let dismissConflict;

  const wrap = h('span', { class: 'if-wrap', 'data-key': fieldKey });
  wrap.disposeInline = () => {
    disposed = true;
    dismissConflict?.();
    if (saveTimer) clearTimeout(saveTimer);
    if (mode === 'edit') { mode = 'read'; store.endEdit(currentEntity.id); }
  };
  parent.appendChild(wrap);

  const status = h('span', { class: 'if-status' });
  parent.appendChild(status);

  paint();

  function paint() {
    wrap.replaceChildren();
    if (mode === 'read') {
      const el = renderer.read({
        value: currentEntity[fieldKey],
        readOnly: ro,
        placeholder: fieldSpec.placeholder,
        ...fieldSpec,
      });
      if (!ro) el.addEventListener('click', enterEdit);
      wrap.appendChild(el);
    } else {
      const el = renderer.edit({
        value: currentEntity[fieldKey],
        onChange: (v) => {
          pendingValue = renderer.coerce ? renderer.coerce(v) : v;
          scheduleSave();
        },
        onCommit: (v) => {
          pendingValue = renderer.coerce ? renderer.coerce(v) : v;
          flushSave().then((saved) => {
            if (!saved || disposed) return;
            mode = 'read';
            currentEntity[fieldKey] = pendingValue;
            paint();
            store.endEdit(currentEntity.id);
          });
        },
        onCancel: () => {
          if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
          conflicted = false;
          dismissConflict?.();
          pendingValue = currentEntity[fieldKey];
          mode = 'read';
          paint();
          store.endEdit(currentEntity.id);
        },
        getBacklog,
        ...fieldSpec,
      });
      wrap.appendChild(el);
    }
  }

  function enterEdit() {
    if (ro) return;
    pendingValue = currentEntity[fieldKey];
    mode = 'edit';
    store.beginEdit(currentEntity.id);
    paint();
  }

  function scheduleSave() {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(flushSave, DEBOUNCE_MS);
  }

  async function flushSave() {
    if (disposed || conflicted) return false;
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
    // A commit during autosave must wait and then drain the newest draft. Do
    // not drop it, close the editor early, or issue concurrent stale writes.
    if (inFlight) {
      if (!await inFlight || disposed) return false;
      return flushSave();
    }
    const v = pendingValue;
    if (sameValue(v, currentEntity[fieldKey])) return true;
    const saving = saveValue(v);
    inFlight = saving;
    let saved;
    try { saved = await saving; }
    finally { if (inFlight === saving) inFlight = null; }
    if (!saved || disposed) return false;
    return sameValue(v, pendingValue) || await flushSave();
  }

  async function saveValue(v) {
    setStatus('saving');
    try {
      const result = await onSave(v);
      if (result && result.error) {
        setStatus('error', result.error);
        return false;
      }
      currentEntity[fieldKey] = v;
      setStatus('ok');
      setTimeout(() => setStatus(''), 800);
      return true;
    } catch (e) {
      if (e && e.code === 409) {
        // Stale write — surface conflict banner.
        conflicted = true;
        if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
        const { showFieldConflict } = await import('./conflict-banner.js');
        if (disposed) return false;
        dismissConflict = showFieldConflict({
          entityKind: schema.entity || 'entity',
          entityId: currentEntity.id || '?',
          fieldKey, fieldLabel: fieldSpec.label || fieldKey,
          localValue: pendingValue,
          currentValue: e.current?.[fieldKey],
          currentEtag: e.current_etag,
          onKeepMine: async () => {
            if (disposed) return;
            // Drain the current draft, including typing during either save;
            // the value rejected by the first request may already be obsolete.
            store.setEtag(`task:${currentEntity.id}`, e.current_etag);
            conflicted = false;
            if (!await flushSave() || disposed) return;
            mode = 'read';
            paint();
            store.endEdit(currentEntity.id);
          },
          onUseServer: async () => {
            if (disposed) return;
            conflicted = false;
            if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
            currentEntity[fieldKey] = e.current?.[fieldKey];
            store.setEtag(`task:${currentEntity.id}`, e.current_etag);
            pendingValue = currentEntity[fieldKey];
            mode = 'read';
            paint();
            store.endEdit(currentEntity.id);
          },
        });
        setStatus('error', 'stale — see banner');
        return false;
      }
      setStatus('error', e.message || String(e));
      return false;
    }
  }

  function setStatus(kind, msg) {
    status.replaceChildren();
    status.className = 'if-status';
    if (!kind) return;
    if (kind === 'saving')  status.appendChild(h('span', { class: 'if-status-saving' }, '●'));
    if (kind === 'ok')      status.appendChild(h('span', { class: 'if-status-ok' }, '✓'));
    if (kind === 'error') {
      const x = h('span', { class: 'if-status-error', title: msg || 'save failed' }, '✕');
      status.appendChild(x);
    }
  }

  function sameValue(a, b) {
    return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
  }

  return {
    update(newEntity) {
      currentEntity = { ...(newEntity || {}) };
      if (mode === 'read') paint();
    },
    destroy() {
      wrap.disposeInline();
      wrap.remove();
      status.remove();
    },
  };
}
