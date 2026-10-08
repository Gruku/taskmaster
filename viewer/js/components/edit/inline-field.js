// viewer/js/components/edit/inline-field.js
// Read↔edit wrapper for one field on one entity. Used by detail screens.

import { h } from '../../util/h.js';
import { fieldByKey, isSystemManaged } from './schema.js';
import { store } from '../../store.js';
import { describeWriteError, lostRace } from './write-errors.js';
import { sameValue } from './same-value.js';

const DEBOUNCE_MS = 600;
const TICK_MS = 800;   // how long the success tick, and the word "Saved", stay
const SAY_MS = 100;    // a field drawn anew says a carried "Saved" this long after it mounts, so the change is heard
let seq = 0;

export function mountInlineField(parent, {
  schema, fieldKey, entity, onSave,
  readOnly = false, getBacklog, messageHost,
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
  let tickTimer = null;   // clears the success tick; any newer status cancels it

  const wrap = h('span', { class: 'if-wrap', 'data-key': fieldKey });
  wrap.disposeInline = () => {
    disposed = true;
    dismissConflict?.();
    if (saveTimer) clearTimeout(saveTimer);
    clearTimeout(tickTimer);
    clearTimeout(sayTimer);
    if (mode === 'edit') { mode = 'read'; store.endEdit(currentEntity.id); }
  };
  parent.appendChild(wrap);

  // The glyph sits beside the field, where saving and saved take no line of their own. It is never read: the message
  // says a failure in words, and it would otherwise join the name of a heading the field sits in.
  const status = h('span', { class: 'if-status', 'aria-hidden': 'true' });
  parent.appendChild(status);
  // Why a save failed, as words beside the field: a tooltip never reaches the keyboard or a touch screen. It is an
  // alert so it is announced, and it describes the control while the editor is open. It stays said once the editor
  // closes, until the field is opened again or a save goes through.
  // `messageHost` takes it when the field sits somewhere words must not, such as a heading whose text names a dialog.
  const message = h('span', { class: 'ef-error if-error', id: `if-error-${++seq}`, role: 'alert' });
  (messageHost ?? parent).appendChild(message);
  // Saving and saved in words, for whoever cannot see the glyph: a polite status, off screen, beside the message.
  const said = h('span', { class: 'if-said', role: 'status' });
  (messageHost ?? parent).appendChild(said);
  // A document drawn again (another writer's change, or the reload after an editor closes) asks what the field was
  // still saying and says it again in the new one: shown, not announced a second time.
  wrap.refusal = () => (mode === 'read' ? message.textContent : '');
  wrap.sayRefusal = (text) => {
    if (mode !== 'read' || !text) return;
    setStatus('error', text, { quiet: true });
  };
  // A save that goes through ends the edit, and the document drawn again replaces this field before "Saved" can be
  // heard; the new field says it, a moment after it mounts (words present at mount are not announced).
  let sayTimer = null;
  wrap.saved = () => mode === 'read' && said.textContent === 'Saved';
  wrap.saySaved = () => {
    if (mode !== 'read') return;
    clearTimeout(sayTimer);
    sayTimer = setTimeout(() => {
      if (disposed || mode !== 'read') return;
      setStatus('ok');
      tickTimer = setTimeout(() => setStatus(''), TICK_MS);
    }, SAY_MS);
  };
  let editor = null;    // the open editor, as the renderer returned it
  let control = null;   // the focusable control of the open editor

  paint();

  function paint() {
    editor = control = null;
    wrap.replaceChildren();
    if (mode === 'read') {
      const el = renderer.read({
        value: currentEntity[fieldKey],
        readOnly: ro,
        placeholder: fieldSpec.placeholder,
        ...fieldSpec,
      });
      // A link inside rendered markdown is followed, not treated as a request to edit.
      if (!ro) el.addEventListener('click', (e) => { if (!e.target.closest?.('a[href]')) enterEdit(); });
      wrap.appendChild(el);
    } else {
      const el = renderer.edit({
        value: currentEntity[fieldKey],
        onChange: (v) => {
          pendingValue = renderer.coerce ? renderer.coerce(v) : v;
          scheduleSave();
        },
        onCommit: (v) => {
          // The browser blurs a focused control as it is taken away, and a text control commits on blur: a closed
          // editor commits nothing — not the draft Escape cancelled, and not a second end to the edit.
          if (mode !== 'edit') return;
          pendingValue = renderer.coerce ? renderer.coerce(v) : v;
          flushSave().then((saved) => {
            if (!saved || disposed) return;
            mode = 'read';
            currentEntity[fieldKey] = pendingValue;
            paint();
            store.endEdit(currentEntity.id);
          });
        },
        onCancel: cancel,
        getBacklog,
        entityId: currentEntity.id,
        ...fieldSpec,
      });
      editor = el;
      control = el.control ?? el;
      // Inline there is no visible label to point at; a control that does not name itself takes its field's label.
      if (fieldSpec.label && !control.hasAttribute('aria-label') && !control.hasAttribute('aria-labelledby')) {
        control.setAttribute('aria-label', fieldSpec.label);
      }
      wrap.appendChild(el);
    }
  }

  function showMessage(text) {
    message.textContent = text || '';
    const ids = (control?.getAttribute('aria-describedby') || '').split(' ').filter((id) => id && id !== message.id);
    if (text) ids.push(message.id);
    if (!control) return;
    if (ids.length) control.setAttribute('aria-describedby', ids.join(' '));
    else control.removeAttribute('aria-describedby');
  }

  function cancel() {
    if (mode !== 'edit') return;   // a control that blurs as it is taken away cancels nothing a second time
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
    if (conflicted) setStatus('');  // the banner it points to goes with it
    conflicted = false;
    dismissConflict?.();
    pendingValue = currentEntity[fieldKey];
    mode = 'read';
    paint();
    store.endEdit(currentEntity.id);
  }

  // A refused save is said beside the field. An editor that shows the value it asked for (a select commits as it
  // changes) goes back to the stored one, so it never looks as if the change took; text being typed stays the user's.
  function refuse(msg) {
    setStatus('error', msg);
    if (mode !== 'edit' || !editor?.reset) return;
    editor.reset();
    pendingValue = currentEntity[fieldKey];
    // Left already: nothing will blur it again, and an open editor holds back live updates to the task.
    if (!editor.contains(document.activeElement)) cancel();
  }

  function enterEdit() {
    if (ro) return;
    setStatus('');
    pendingValue = currentEntity[fieldKey];
    mode = 'edit';
    store.beginEdit(currentEntity.id);
    paint();
  }

  function scheduleSave() {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => flushSave({ auto: true }), DEBOUNCE_MS);
  }

  // `auto`: the debounced save at a pause in typing, which says only "Saved"; a commit also says "Saving…".
  async function flushSave({ auto = false } = {}) {
    if (disposed || conflicted) return false;
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
    // A commit during autosave must wait and then drain the newest draft. Do
    // not drop it, close the editor early, or issue concurrent stale writes.
    if (inFlight) {
      if (!await inFlight || disposed) return false;
      return flushSave({ auto });
    }
    const v = pendingValue;
    if (sameValue(v, currentEntity[fieldKey])) return true;
    const saving = saveValue(v, { auto });
    inFlight = saving;
    let saved;
    try { saved = await saving; }
    finally { if (inFlight === saving) inFlight = null; }
    if (!saved || disposed) return false;
    return sameValue(v, pendingValue) || await flushSave({ auto });
  }

  async function saveValue(v, { auto = false } = {}) {
    setStatus('saving', undefined, { say: !auto });
    try {
      const result = await onSave(v);
      if (result && result.error) {
        refuse(result.error);
        return false;
      }
      currentEntity[fieldKey] = v;
      setStatus('ok');
      tickTimer = setTimeout(() => setStatus(''), TICK_MS);
      return true;
    } catch (e) {
      // Only a 409 that names the revision it lost to is a conflict; any other is the server refusing the write,
      // and falls through to the error below with its reason, leaving the stored revision as it was.
      if (lostRace(e)) {
        // Stale write — surface conflict banner.
        conflicted = true;
        if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
        const { showFieldConflict, optionText } = await import('./conflict-banner.js');
        if (disposed) return false;
        dismissConflict = showFieldConflict({
          entityKind: schema.entity || 'entity',
          entityId: currentEntity.id || '?',
          fieldKey, fieldLabel: fieldSpec.label || fieldKey,
          localValue: pendingValue,
          currentValue: e.current?.[fieldKey],
          text: optionText(fieldSpec),
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
            setStatus('');
            currentEntity[fieldKey] = e.current?.[fieldKey];
            store.setEtag(`task:${currentEntity.id}`, e.current_etag);
            pendingValue = currentEntity[fieldKey];
            mode = 'read';
            paint();
            store.endEdit(currentEntity.id);
          },
        });
        setStatus('error', 'Conflict — see banner');
        return false;
      }
      refuse(describeWriteError(e));
      return false;
    }
  }

  // `quiet` says it without announcing it: the live region is off before the words land.
  // `say: false` shows the glyph without the words (an autosave's "Saving…").
  function setStatus(kind, msg, { quiet = false, say = true } = {}) {
    clearTimeout(tickTimer);
    if (quiet) message.setAttribute('aria-live', 'off');
    else message.removeAttribute('aria-live');
    status.replaceChildren();
    status.className = 'if-status';
    showMessage(kind === 'error' ? (msg || 'Save failed') : '');
    said.textContent = !say ? '' : kind === 'saving' ? 'Saving…' : kind === 'ok' ? 'Saved' : '';
    if (!kind) return;
    if (kind === 'saving')  status.appendChild(h('span', { class: 'if-status-saving' }, '●'));
    if (kind === 'ok')      status.appendChild(h('span', { class: 'if-status-ok' }, '✓'));
    // A field whose words have a line of their own (messageHost) needs no glyph floating beside the editor.
    if (kind === 'error' && !messageHost) {
      // The message beside the field says it in words; the glyph is not read a second time.
      const x = h('span', { class: 'if-status-error', title: msg || 'save failed', 'aria-hidden': 'true' }, '✕');
      status.appendChild(x);
    }
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
      message.remove();
      said.remove();
    },
  };
}
