// User intent: a Dashboard sticky note — solid coloured paper in both themes — that can be read in full, edited,
// pinned and archived by keyboard, and that never loses typed text when the server refuses the save.
import { h } from '../../util/h.js';
import { icon } from '../icon.js';
import { tiltFor } from '../../lib/desk.js';
import { formatStamp } from '../../lib/time.js';
import { mountMarkdown } from '../markdown.js';

// User notes are warm paper, Claude notes cool paper; the static tilt comes from the id hash.
// onPin(note), onArchive(note), onSave(note, text) resolve true when the write went through, false when it was refused.
// `expanded` opens the note already expanded (when it overflows); onExpand(open) reports each expand and collapse.
export function createNoteCard({ note, onPin, onArchive, onSave, expanded = false, onExpand }) {
  const whoId = `dk-note-who-${note.id}`;
  const bodyId = `dk-note-body-${note.id}`;
  const root = h('article', {
    class: `dk-note dk-note--${note.author === 'claude' ? 'claude' : 'user'}`
           + (note.pinned ? ' is-pinned' : ''),
    'data-note-id': note.id,
    'aria-labelledby': whoId,
  });
  root.style.setProperty('--tilt', tiltFor(note.id) + 'deg');

  const stamp = formatStamp(note.created);
  const editBtn = h('button', {
    class: 'dk-note__edit btn btn--ghost btn--icon btn--sm', type: 'button', 'aria-label': 'Edit note',
    on: {
      // While the editor is open, pressing Edit keeps focus in it rather than saving and re-opening on stale text.
      mousedown: (e) => { if (root.classList.contains('is-editing')) e.preventDefault(); },
      click: () => openEditor(note.body || ''),
    },
  }, icon('edit', { size: 16 }));
  const pinBtn = h('button', {
    class: 'dk-note__pin btn btn--ghost btn--sm', type: 'button',
    'aria-pressed': note.pinned ? 'true' : 'false',
    on: { click: () => onPin?.(note) },
  }, note.pinned ? 'Unpin' : 'Pin');
  const archiveBtn = h('button', {
    class: 'dk-note__archive btn btn--ghost btn--icon btn--sm', type: 'button', 'aria-label': 'Archive note',
    on: { click: () => onArchive?.(note) },
  }, icon('archive', { size: 16 }));

  const head = h('header', { class: 'dk-note__head' },
    h('span', { class: 'dk-note__who', id: whoId }, note.author === 'claude' ? 'Claude' : 'You'),
    h('time', { class: 'dk-note__when', datetime: note.created || '', title: stamp.title }, stamp.text),
    // One group, so on a narrow note the controls wrap together under the author and time.
    h('div', { class: 'dk-note__tools' }, editBtn, pinBtn, archiveBtn));

  const body = h('div', { class: 'dk-note__body', id: bodyId });
  // Without window.marked this falls back to escaped <pre>, so the text stays visible either way.
  mountMarkdown(body, note.body || '');
  const fade = h('span', { class: 'dk-note__fade', 'aria-hidden': 'true' });
  const more = h('button', {
    class: 'dk-note__more btn btn--ghost btn--sm', type: 'button',
    'aria-expanded': 'false', 'aria-controls': bodyId,
    on: { click: () => setExpanded(!root.classList.contains('is-expanded')) },
  }, 'Show more');

  function setExpanded(open) {
    const was = root.classList.contains('is-expanded');
    root.classList.toggle('is-expanded', open);
    more.setAttribute('aria-expanded', open ? 'true' : 'false');
    more.textContent = open ? 'Show less' : 'Show more';
    if (was !== open) onExpand?.(open);
  }
  if (expanded) setExpanded(true);

  // The clamp is measured with the note collapsed for that instant (no paint in between), so an expanded note keeps
  // its Show less while its text still needs the room, and loses it when the text fits.
  function measure() {
    if (root.classList.contains('is-editing')) return;
    const open = root.classList.contains('is-expanded');
    if (open) root.classList.remove('is-expanded');
    const clamped = body.scrollHeight > body.clientHeight + 1;
    if (open) root.classList.add('is-expanded');
    root.classList.toggle('is-clamped', clamped);
    if (clamped) {
      if (fade.parentNode !== body) body.append(fade);
      if (!more.isConnected) body.after(more);
    } else {
      if (open) setExpanded(false);
      fade.remove();
      more.remove();
    }
  }

  // Whatever takes focus inside a clamped body (a link Tab reaches) opens the note, so it is never focused out of sight.
  body.addEventListener('focusin', (e) => {
    if (!root.classList.contains('is-clamped') || root.classList.contains('is-expanded')) return;
    setExpanded(true);
    body.scrollTop = 0;
    e.target.scrollIntoView?.({ block: 'nearest' });
  });

  // A link in the body follows its href; a click anywhere else on it edits.
  body.addEventListener('click', (e) => {
    if (e.target.closest?.('a')) return;
    openEditor(note.body || '');
  });

  // Escape cancels, Ctrl/⌘+Enter or leaving the editor saves.
  function openEditor(text, { focus = true } = {}) {
    if (root.classList.contains('is-editing')) return;
    root.classList.add('is-editing');
    const ta = h('textarea', { class: 'dk-note__editor', 'aria-label': 'Edit note' });
    ta.value = text;
    let settled = false;            // one outcome per editor: Escape's replaceWith also fires blur
    const done = async (save, { refocus = true } = {}) => {
      if (settled) return;
      settled = true;
      root.classList.remove('is-editing');
      ta.replaceWith(body);
      if (refocus && editBtn.isConnected) editBtn.focus();
      if (!save || !ta.value.trim() || ta.value === note.body) return;
      const ok = await onSave?.(note, ta.value);
      // Refused: the typed text comes back in the editor, which takes focus only if focus has not moved on.
      if (ok === false) {
        const active = document.activeElement;
        openEditor(ta.value, { focus: !active || active === editBtn || active === document.body });
      }
    };
    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); done(false); }
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); done(true); }
    });
    // Focus already went where the person sent it; leaving saves without pulling it back.
    ta.addEventListener('blur', () => done(true, { refocus: false }));
    body.replaceWith(ta);
    if (!focus) return;
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
  }

  root.append(head, body);

  const onResize = () => {
    if (!root.isConnected) { window.removeEventListener('resize', onResize); return; }
    measure();
  };
  window.addEventListener('resize', onResize);
  requestAnimationFrame(() => { if (root.isConnected) measure(); });

  return { root, measure };
}
