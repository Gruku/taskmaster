// viewer/js/components/edit/inline-field.js
// Read↔edit wrapper for one field on one entity. Used by detail screens.

import { h } from '../../util/h.js';
import { fieldByKey, isSystemManaged } from './schema.js';

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
  let inFlight = false;

  const wrap = h('span', { class: 'if-wrap', 'data-key': fieldKey });
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
          flushSave().then(() => {
            mode = 'read';
            currentEntity[fieldKey] = pendingValue;
            paint();
          });
        },
        onCancel: () => {
          if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
          pendingValue = currentEntity[fieldKey];
          mode = 'read';
          paint();
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
    paint();
  }

  function scheduleSave() {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(flushSave, DEBOUNCE_MS);
  }

  async function flushSave() {
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
    const v = pendingValue;
    if (sameValue(v, currentEntity[fieldKey])) return;
    inFlight = true;
    setStatus('saving');
    try {
      const result = await onSave(v);
      if (result && result.error) {
        setStatus('error', result.error);
        return;
      }
      currentEntity[fieldKey] = v;
      setStatus('ok');
      setTimeout(() => setStatus(''), 800);
    } catch (e) {
      if (e && e.code === 409) {
        // Stale write — surface conflict banner.
        const { showFieldConflict } = await import('./conflict-banner.js');
        showFieldConflict({
          entityKind: schema.entity || 'entity',
          entityId: currentEntity.id || '?',
          fieldKey, fieldLabel: fieldSpec.label || fieldKey,
          localValue: v,
          currentValue: e.current?.[fieldKey],
          currentEtag: e.current_etag,
          onKeepMine: async () => {
            // Update local etag and re-PATCH.
            const { store } = await import('../../store.js');
            store.setEtag(`task:${currentEntity.id}`, e.current_etag);
            try { await onSave(v); } catch (e2) { setStatus('error', e2.message); return; }
            currentEntity[fieldKey] = v;
            paint();
          },
          onUseServer: async () => {
            currentEntity[fieldKey] = e.current?.[fieldKey];
            const { store } = await import('../../store.js');
            store.setEtag(`task:${currentEntity.id}`, e.current_etag);
            paint();
          },
        });
        setStatus('error', 'stale — see banner');
        return;
      }
      setStatus('error', e.message || String(e));
    } finally {
      inFlight = false;
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
      if (saveTimer) clearTimeout(saveTimer);
      wrap.remove();
      status.remove();
    },
  };
}
