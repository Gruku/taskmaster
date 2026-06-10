import { h } from '../../util/h.js';
import { tiltFor } from '../../lib/desk.js';
import { mountMarkdown } from '../markdown.js';

// A paper sticky note. Light paper on the dark desk; user notes are warm
// yellow paper, claude notes cool blue paper. Static tilt from id hash.
// onPin(note), onArchive(note), onSave(note, newText) are async callbacks.
export function createNoteCard({ note, onPin, onArchive, onSave }) {
  const root = h('article', {
    class: `dk-note dk-note--${note.author === 'claude' ? 'claude' : 'user'}`
           + (note.pinned ? ' is-pinned' : ''),
    'data-note-id': note.id,
  });
  root.style.setProperty('--tilt', tiltFor(note.id) + 'deg');

  const when = relTime(note.created);
  const who = note.author === 'claude' ? '✦ claude' : 'you';

  const pinBtn = h('button', {
    class: 'dk-note__pin', type: 'button',
    title: note.pinned ? 'Unpin' : 'Pin',
    'aria-pressed': note.pinned ? 'true' : 'false',
    on: { click: (e) => { e.stopPropagation(); onPin?.(note); } },
  }, '📌');
  const archiveBtn = h('button', {
    class: 'dk-note__archive', type: 'button', title: 'Archive note',
    on: { click: (e) => { e.stopPropagation(); onArchive?.(note); } },
  }, '✕');

  const head = h('header', { class: 'dk-note__head' },
    h('span', { class: 'dk-note__who' }, who),
    h('span', { class: 'dk-note__when' }, when),
    pinBtn, archiveBtn);

  const body = h('div', { class: 'dk-note__body' });
  // mountMarkdown(el, src) sets el.innerHTML = renderMarkdown(src).
  // When window.marked is absent it falls back to <pre class="md-fallback"> so
  // plain text stays visible in both cases.
  mountMarkdown(body, note.body || '');

  // Click body → inline edit (textarea swap). Esc cancels, Ctrl+Enter / blur saves.
  body.addEventListener('click', () => {
    if (root.classList.contains('is-editing')) return;
    root.classList.add('is-editing');
    const ta = h('textarea', { class: 'dk-note__edit' });
    ta.value = note.body || '';
    let settled = false;            // guard double-fire (Esc keydown → blur)
    const done = async (save) => {
      if (settled) return;
      settled = true;
      root.classList.remove('is-editing');
      ta.replaceWith(body);
      if (save && ta.value.trim() && ta.value !== note.body) {
        await onSave?.(note, ta.value);
      }
    };
    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') done(false);
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) done(true);
    });
    ta.addEventListener('blur', () => done(true));
    body.replaceWith(ta);
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
  });

  root.append(head, body);
  return { root };
}

function relTime(iso) {
  if (!iso) return '';
  const ms = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(ms / 60000);
  if (mins < 60) return `${Math.max(mins, 0)}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}
