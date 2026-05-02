import * as api from '../api.js';
import { claimTopbar, tmAction } from '../lib/topbar.js';

export const meta = { title: 'Lesson', icon: '✦', sidebarKey: 'lessons' };

const KIND_ICON = { gotcha: '⚠', pattern: '◇', 'anti-pattern': '⊘' };
const KIND_LABEL = { gotcha: 'Gotcha', pattern: 'Pattern', 'anti-pattern': 'Anti-pattern' };

function _fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function _fmtRel(iso, now = new Date()) {
  if (!iso) return '—';
  const ms = now.getTime() - new Date(iso).getTime();
  const d = Math.floor(ms / 86_400_000);
  if (d <= 0) {
    const h = Math.floor(ms / 3_600_000);
    return h <= 0 ? 'now' : `${h}h ago`;
  }
  return `${d}d ago`;
}

export async function mount(root, { params, store, prefs, subpath }) {
  const id = subpath?.[0] || params?.id || null;
  root.innerHTML = '';
  root.classList.add('lesson-detail');

  if (!id) {
    root.innerHTML = `<div class="ld-empty">No lesson selected. <a href="#/lessons">Back to Lessons</a>.</div>`;
    claimTopbar();
    return () => { root.classList.remove('lesson-detail'); };
  }

  // Persist last-viewed for sidebar return-trip.
  if (prefs?.patch) prefs.patch({ ui: { last_lesson_id: id } });

  // Look up from store first; fetch all if absent.
  let lessons = store?.getLessons?.() || [];
  if (lessons.length === 0) {
    try {
      const data = await api.getLessons();
      lessons = data.lessons || [];
      store?.setLessons?.(lessons);
    } catch (e) {
      const empty = document.createElement('div');
      empty.className = 'ld-empty';
      empty.textContent = `Could not load lessons: ${e.message}`;
      root.replaceChildren(empty);
      claimTopbar();
      return () => { root.classList.remove('lesson-detail'); };
    }
  }
  let lesson = lessons.find(l => l.id === id);
  if (!lesson) {
    const empty = document.createElement('div');
    empty.className = 'ld-empty';
    empty.textContent = `Lesson ${id} not found. `;
    const back = document.createElement('a');
    back.href = '#/lessons';
    back.textContent = 'Back to Lessons';
    empty.append(back, '.');
    root.replaceChildren(empty);
    claimTopbar();
    return () => { root.classList.remove('lesson-detail'); };
  }

  // Topbar: just the reinforce action (back lives in the page header crumb row).
  const topbar = claimTopbar();
  const reinforceBtn = tmAction({
    icon: '↑',
    label: lesson.shelf === 'retired' ? 'Revive' : 'Reinforce',
    variant: 'primary',
    title: 'Add a reinforcement event',
  });
  topbar?.append(reinforceBtn);

  reinforceBtn.addEventListener('click', async () => {
    if (reinforceBtn.classList.contains('is-fired')) return;
    reinforceBtn.classList.add('is-fired');
    reinforceBtn.setAttribute('aria-disabled', 'true');
    try {
      await api.reinforceLesson(lesson.id, { source: 'user' });
      const fresh = await api.getLessons();
      store?.setLessons?.(fresh.lessons || []);
      lesson = (fresh.lessons || []).find(l => l.id === id) || lesson;
      render();
    } catch (e) {
      console.error('reinforceLesson failed', e);
      reinforceBtn.classList.remove('is-fired');
      reinforceBtn.removeAttribute('aria-disabled');
    }
  });

  function render() {
    root.replaceChildren();

    // Crumb row: ‹ Lessons / shelf-name
    const crumb = document.createElement('div');
    crumb.className = 'ld-crumb';
    const back = document.createElement('a');
    back.className = 'ld-back';
    back.href = '#/lessons';
    back.textContent = '‹ Lessons';
    const sep = document.createElement('span');
    sep.className = 'ld-crumb-sep';
    sep.textContent = '/';
    const shelfCrumb = document.createElement('span');
    shelfCrumb.className = 'ld-crumb-shelf';
    shelfCrumb.textContent = (lesson.shelf || 'active').replace(/^./, c => c.toUpperCase());
    crumb.append(back, sep, shelfCrumb);
    root.appendChild(crumb);

    // Header: kind icon · id · created
    const head = document.createElement('header');
    head.className = 'ld-head';
    const meta = document.createElement('div');
    meta.className = 'ld-meta';
    const kind = document.createElement('span');
    kind.className = 'ld-kind';
    kind.textContent = `${KIND_ICON[lesson.kind] || '◇'} ${KIND_LABEL[lesson.kind] || lesson.kind || 'pattern'}`;
    const idEl = document.createElement('span');
    idEl.className = 'ld-id';
    idEl.textContent = lesson.id;
    const created = document.createElement('span');
    created.className = 'ld-created';
    created.textContent = `since ${_fmtDate(lesson.created)}`;
    const shelfPill = document.createElement('span');
    shelfPill.className = `ld-shelf ld-shelf--${lesson.shelf || 'active'}`;
    shelfPill.textContent = lesson.shelf || 'active';
    meta.append(kind, idEl, shelfPill, created);
    head.appendChild(meta);

    const title = document.createElement('h1');
    title.className = 'ld-title';
    title.textContent = lesson.title || '(untitled)';
    head.appendChild(title);
    root.appendChild(head);

    // Body grid: main column + side column
    const grid = document.createElement('div');
    grid.className = 'ld-grid';

    // ---- main column
    const main = document.createElement('div');
    main.className = 'ld-main';

    if (lesson.summary) {
      const summary = document.createElement('section');
      summary.className = 'ld-summary';
      const h = document.createElement('h2');
      h.className = 'ld-h';
      h.textContent = 'Summary';
      const body = document.createElement('p');
      body.className = 'ld-body';
      body.textContent = lesson.summary;
      summary.append(h, body);
      main.appendChild(summary);
    }

    // anchor section
    const anchorsSec = document.createElement('section');
    anchorsSec.className = 'ld-anchors';
    const ah = document.createElement('h2');
    ah.className = 'ld-h';
    ah.textContent = 'When this fires';
    anchorsSec.appendChild(ah);
    const files = (lesson.triggers && lesson.triggers.files) || [];
    if (files.length) {
      const list = document.createElement('div');
      list.className = 'ld-anchor-list';
      for (const f of files) {
        const pill = document.createElement('code');
        pill.className = 'ld-anchor-pill';
        pill.textContent = f;
        list.appendChild(pill);
      }
      anchorsSec.appendChild(list);
    } else {
      const empty = document.createElement('p');
      empty.className = 'ld-empty-line';
      empty.textContent = 'No file anchors — applies to any file.';
      anchorsSec.appendChild(empty);
    }
    main.appendChild(anchorsSec);

    // events history
    const events = lesson.reinforce_events || [];
    if (events.length) {
      const histSec = document.createElement('section');
      histSec.className = 'ld-history';
      const hh = document.createElement('h2');
      hh.className = 'ld-h';
      hh.textContent = `Reinforcement history · ${events.length}`;
      histSec.appendChild(hh);
      const ul = document.createElement('ul');
      ul.className = 'ld-events';
      const sorted = [...events].sort((a, b) => (b.at || '').localeCompare(a.at || ''));
      for (const ev of sorted) {
        const li = document.createElement('li');
        li.className = 'ld-event';
        const when = document.createElement('span');
        when.className = 'ld-event-when';
        when.textContent = _fmtRel(ev.at);
        const src = document.createElement('span');
        src.className = 'ld-event-src';
        src.textContent = ev.source || 'user';
        const note = document.createElement('span');
        note.className = 'ld-event-note';
        note.textContent = ev.note || '';
        li.append(when, src, note);
        ul.appendChild(li);
      }
      histSec.appendChild(ul);
      main.appendChild(histSec);
    }

    grid.appendChild(main);

    // ---- side column (related)
    const side = document.createElement('aside');
    side.className = 'ld-side';

    const sideMeta = document.createElement('section');
    sideMeta.className = 'ld-side-block';
    const sh = document.createElement('h2');
    sh.className = 'ld-h';
    sh.textContent = 'Signals';
    sideMeta.appendChild(sh);
    const dl = document.createElement('dl');
    dl.className = 'ld-dl';
    const rows = [
      ['Reinforce count', `${lesson.reinforce_count || 0}×`],
      ['Last reinforced', _fmtRel(lesson.last_reinforced)],
      ['Shelf', lesson.shelf || 'active'],
    ];
    for (const [k, v] of rows) {
      const dt = document.createElement('dt'); dt.textContent = k;
      const dd = document.createElement('dd'); dd.textContent = v;
      dl.append(dt, dd);
    }
    sideMeta.appendChild(dl);
    side.appendChild(sideMeta);

    const tasks = lesson.related_tasks || [];
    const issues = lesson.related_issues || [];
    if (tasks.length || issues.length) {
      const rel = document.createElement('section');
      rel.className = 'ld-side-block';
      const rh = document.createElement('h2');
      rh.className = 'ld-h';
      rh.textContent = 'Related';
      rel.appendChild(rh);
      const list = document.createElement('div');
      list.className = 'ld-rel-list';
      for (const t of tasks) {
        const tid = typeof t === 'string' ? t : (t.id || '');
        if (!tid) continue;
        const a = document.createElement('a');
        a.className = 'ld-rel-pill';
        a.href = `#/task/${encodeURIComponent(tid)}`;
        a.textContent = tid;
        list.appendChild(a);
      }
      for (const i of issues) {
        const iid = typeof i === 'string' ? i : (i.id || '');
        if (!iid) continue;
        const a = document.createElement('a');
        a.className = 'ld-rel-pill ld-rel-pill--issue';
        a.href = `#/issue/${encodeURIComponent(iid)}`;
        a.textContent = iid;
        list.appendChild(a);
      }
      rel.appendChild(list);
      side.appendChild(rel);
    }

    grid.appendChild(side);
    root.appendChild(grid);
  }

  render();

  return () => {
    root.classList.remove('lesson-detail');
  };
}
