// Variant A — Document layout for Task Detail.
// Exports `mountTaskDetailDocument(root, { task, related, prefs, onNavigate })`.

import { renderMarkdown } from './markdown.js';
import { mountRightRail } from './right-rail.js';
import { claimTopbar, tmSegmented, tmAction } from '../lib/topbar.js';
import { formatRelative } from '../lib/time.js';
import { mountInlineField } from './edit/inline-field.js';
import { taskSchema } from './edit/forms/task-form.js';
import { renderGatePipeline } from './gate-pipeline.js';

// Inline-edit save callback. Returns either undefined (success) or { error }.
function inlineSave(taskId, fieldKey, ctx) {
  return async (newValue) => {
    try {
      await ctx.api.patchTask(taskId, { [fieldKey]: newValue });
      // Refresh backlog so the change is reflected in store + other screens.
      ctx.store.setBacklog(await ctx.api.backlog());
    } catch (e) {
      if (e && e.code === 409) throw e; // re-throw so inline-field can show conflict banner
      return { error: e.message || String(e) };
    }
  };
}

export function mountTaskDetailDocument(root, ctx) {
  root.innerHTML = '';
  root.classList.add('td-page', 'td-page-A');

  mountTopbar({
    ...ctx,
    onEdit: () => {
      // Late-import to avoid loading edit code on every viewer boot.
      import('./edit/task-actions.js').then(({ openTaskEditModal }) => {
        openTaskEditModal({ store: ctx.store, api: ctx.api, task: ctx.task });
      });
    },
  });
  root.appendChild(renderHeader(ctx));
  const grid = renderGrid(ctx);
  root.appendChild(grid);

  // Fire-and-forget: fetch linked bugs and inject section into the body asynchronously.
  if (ctx.api?.listBugs && ctx.task?.id) {
    mountLinkedBugs(grid.querySelector('main.td-body'), ctx);
  }

  return () => {
    root.innerHTML = '';
    root.classList.remove('td-page', 'td-page-A');
  };
}

function mountTopbar({ prefs, onToggleVariant, task, onEdit, chrome = 'page', actionsHost = null }) {
  // v1 modal (embedded): read-only quick peek — no Document/Graph toggle, no
  // Edit/Archive. The Graph view and edit live on the full route (Open-full).
  if (chrome === 'embedded') return;
  const topbar = claimTopbar();
  if (!topbar) return;
  const view = prefs?.screens?.task_detail?.view === 'B' ? 'B' : 'A';
  const seg = tmSegmented(
    [
      { key: 'A', label: 'Document' },
      { key: 'B', label: 'Graph' },
    ],
    { value: view, onChange: (v) => onToggleVariant?.(v) },
  );
  const editBtn = tmAction({
    icon: '✎', label: 'Edit', title: 'Edit task',
    onClick: () => onEdit?.(),
  });
  const archiveBtn = tmAction({ icon: '✕', label: 'Archive', title: 'Archive task — coming soon', disabled: true });
  topbar.append(seg, editBtn, archiveBtn);
}

function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') el.className = v;
    else if (k === 'on') for (const [evt, fn] of Object.entries(v)) el.addEventListener(evt, fn);
    else if (k === 'html') el.innerHTML = v;
    else el.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null || c === false) continue;
    el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return el;
}

function renderHeader({ task }) {
  return h('div', { class: 'td-ph' }, [
    h('span', { class: 'td-back', on: { click: () => history.back() } }, '‹ back'),
    h('span', { class: 'td-crumb' }, `Tasks / ${task?.epic || ''}`),
  ]);
}

function renderGrid(ctx) {
  return h('div', { class: 'td-grid' }, [
    renderBody(ctx),
    renderRail(ctx),
  ]);
}

function renderBody(ctx) {
  const { task } = ctx;
  if (!task) return h('div', { class: 'td-body td-empty' }, 'task not found');
  const children = [
    renderMeta(task),
    renderTitle(task, ctx),
  ];
  if (task.locked_by) {
    children.push(h('div', { class: 'td-lock-banner', 'data-test': 'lock-banner' },
      [h('span', {}, '🔒 '), h('span', {}, `locked by ${task.locked_by}`)]));
  }
  children.push(renderChips(task, ctx));
  children.push(renderSpecReview(task));
  const gpHtml = renderGatePipeline(task);
  if (gpHtml) {
    const gpSection = h('section', { class: 'td-section td-gate-section', 'data-test': 'gate-pipeline' });
    gpSection.innerHTML = gpHtml;
    children.push(gpSection);
  }
  children.push(renderAutoBanner(task));
  children.push(renderDocsSection(task));
  children.push(renderMdSectionEditable('Specification', 'specification', task, ctx, 'sec-spec'));
  children.push(renderMdSectionEditable('Plan', 'plan', task, ctx, 'sec-plan'));
  children.push(renderMdSectionEditable('Notes', 'notes', task, ctx, 'sec-notes'));
  if (task.status === 'in-review') {
    children.push(renderMdSectionEditable('Review instructions', 'review_instructions', task, ctx, 'sec-review-instructions'));
  }
  children.push(renderActivity(task));
  children.push(renderPatchnote(task));
  children.push(renderDates(task));
  return h('main', { class: 'td-body' }, children.filter(Boolean));
}

function renderDates(task) {
  const cells = [
    ['Created',   task.created],
    ['Started',   task.started],
    ['Completed', task.completed],
  ];
  return h('section', { class: 'td-dates', 'data-test': 'dates' },
    cells.map(([lbl, abs]) =>
      h('div', { class: 'td-date-cell' }, [
        h('span', { class: 'lbl' }, lbl),
        h('span', { class: 'abs mono' }, abs || '—'),
        h('span', { class: 'rel' }, abs ? relativeFromNow(abs) : ''),
      ])));
}

function relativeFromNow(iso) {
  return formatRelative(iso);
}

function renderActivity(task) {
  const lines = task.activity || task.activity_lines;
  if (!lines || !lines.length) return null;
  return h('section', { class: 'td-section', 'data-test': 'sec-activity' }, [
    h('div', { class: 'td-section-h' }, 'Latest activity'),
    h('ul', { class: 'td-activity' },
      lines.slice(0, 8).map((l) => h('li', { class: 'mono' }, l))),
  ]);
}

function renderPatchnote(task) {
  if (task.status !== 'done') return null;
  if (!task.patchnote) return null;
  return renderMdSection('Patchnote', task.patchnote, 'sec-patchnote');
}

function renderDocsSection(task) {
  const docs = task.docs || {};
  const entries = Object.entries(docs);
  if (!entries.length) return null;
  return h('section', { class: 'td-section', 'data-test': 'sec-docs' }, [
    h('div', { class: 'td-section-h' }, 'Docs'),
    h('div', { class: 'td-doc-chips' },
      entries.map(([type, href]) =>
        h('a', { class: 'td-doc-chip', href, target: '_blank', rel: 'noopener' },
          [h('span', { class: 'type' }, type), h('span', {}, href)]))),
  ]);
}

function renderMdSection(label, body, dataTest) {
  if (!body || !String(body).trim()) return null;
  return h('section', { class: 'td-section', 'data-test': dataTest }, [
    h('div', { class: 'td-section-h' }, label),
    h('div', { class: 'md-body', html: renderMarkdown(body) }),
  ]);
}

function renderMdSectionEditable(label, fieldKey, task, ctx, dataTest) {
  const schema = taskSchema({ getBacklog: () => ctx.store?.getBacklog() });
  const wrap = h('section', { class: 'td-section td-inline-host', 'data-test': dataTest });
  wrap.appendChild(h('div', { class: 'td-section-h' }, label));
  mountInlineField(wrap, {
    schema, fieldKey, entity: task,
    onSave: inlineSave(task.id, fieldKey, ctx),
  });
  return wrap;
}

function renderAutoBanner(task) {
  const am = task.auto_mode;
  if (!am || !am.running) return null;
  const pct = Math.max(0, Math.min(100, Math.round((am.progress || 0) * 100)));
  return h('div', { class: 'td-auto-banner', 'data-test': 'auto-banner' }, [
    h('span', { class: 'td-auto-step' }, am.step || 'auto-mode running'),
    h('div', { class: 'td-auto-bar' }, h('span', { style: `width:${pct}%` })),
    h('span', { class: 'td-auto-elapsed' }, am.elapsed || ''),
  ]);
}

function renderSpecReview(task) {
  const sr = task.spec_review;
  if (!sr || !sr.verdict) return null;
  const note = sr.codex_note || '';
  return h('div', { class: 'td-spec-block', 'data-test': 'spec-review' }, [
    h('span', { class: `td-spec-badge ${sr.verdict}`,
                on: { click: (e) => e.currentTarget.nextElementSibling?.classList.toggle('open') } },
      sr.verdict.toUpperCase()),
    h('span', { class: 'td-codex-note serif' }, note),
  ]);
}

function renderChips(task, ctx) {
  const epicColorVar = `--epic-1`;
  const priClass = (task.priority || '').toLowerCase();

  const schema = taskSchema({ getBacklog: () => ctx.store?.getBacklog() });

  // Status pill — now editable via EnumSelect inline
  const statusWrap = h('span', { class: 'td-status-pill td-inline-host' });
  mountInlineField(statusWrap, {
    schema, fieldKey: 'status', entity: task,
    onSave: inlineSave(task.id, 'status', ctx),
  });

  // Priority pill — same pattern
  const priWrap = h('span', { class: `td-pri-pill ${priClass === 'critical' ? 'crit' : priClass} td-inline-host` });
  mountInlineField(priWrap, {
    schema, fieldKey: 'priority', entity: task,
    onSave: inlineSave(task.id, 'priority', ctx),
  });

  const chips = [
    statusWrap,
    priWrap,
    task.estimate ? h('span', { class: 'td-size-chip' }, task.estimate) : null,
    task.epic ? h('span', { class: 'td-epic-chip', style: `--epic-1: var(${epicColorVar})` },
      [h('span', { class: 'td-swatch' }), h('span', {}, task.epic)]) : null,
    task.branch ? h('span', { class: 'td-branch', 'data-test': 'branch', on: { click: (e) => copyToChip(e.currentTarget, task.branch) } },
      [h('span', { class: 'td-id-text' }, `⎇ ${task.branch}`)]) : null,
    task.worktree ? h('span', { class: 'td-worktree', on: { click: (e) => copyToChip(e.currentTarget, task.worktree) } },
      [h('span', { class: 'td-id-text' }, `⌂ ${task.worktree}`)]) : null,
    task.release ? h('span', { class: 'td-release' }, task.release) : null,
    task.sub_repo ? h('span', { class: 'td-subrepo' }, `· ${task.sub_repo}`) : null,
  ].filter(Boolean);
  return h('div', { class: 'td-chips', 'data-test': 'chips' }, chips);
}

async function copyToChip(el, value) {
  try { await navigator.clipboard.writeText(value); } catch {}
  el.classList.add('copied');
  setTimeout(() => el.classList.remove('copied'), 900);
}

function renderMeta(task) {
  return h('div', { class: 'td-doc-meta', 'data-test': 'meta' }, [
    h('span', { class: 'td-id', 'data-test': 'task-id',
                on: { click: (e) => copyToChip(e.currentTarget, task.id || '') } },
      [h('span', { class: 'td-id-text' }, task.id || '—')]),
    h('span', { class: 'td-sep' }, '·'),
    h('span', {}, task.epic || ''),
    h('span', { class: 'td-sep' }, '·'),
    h('span', {}, task.phase || ''),
    h('span', { class: 'td-sep' }, '·'),
    h('span', {}, `created ${task.created || ''}`),
  ]);
}

function renderTitle(task, ctx) {
  const wrap = h('h1', { class: 'td-title', 'data-test': 'title' });
  const schema = taskSchema({ getBacklog: () => ctx.store?.getBacklog() });
  mountInlineField(wrap, {
    schema, fieldKey: 'title', entity: task,
    onSave: inlineSave(task.id, 'title', ctx),
  });
  return wrap;
}

function renderRail(ctx) {
  const aside = h('aside', { class: 'td-rail-mount', 'data-test': 'rail' });
  mountRightRail(aside, ctx);
  return aside;
}

// ── Linked Bugs subsection (async, appended after sync render) ──────────────
async function mountLinkedBugs(bodyEl, ctx) {
  if (!bodyEl) return;
  try {
    const bugs = await ctx.api.listBugs({ found_in: ctx.task.id });
    if (!bugs || !bugs.length) return;

    const section = h('section', { class: 'td-linked-bugs', 'data-test': 'linked-bugs' });

    const heading = h('div', { class: 'td-section-h td-linked-bugs__heading' }, 'Linked bugs');
    const openCount = bugs.filter((b) => b.status === 'open').length;
    if (openCount > 0) {
      const warn = h('span', { class: 'td-linked-bugs__blocker' },
        `${openCount} open bug${openCount === 1 ? '' : 's'} blocking close`);
      heading.appendChild(warn);
    }
    section.appendChild(heading);

    const ul = h('ul', { class: 'td-linked-bugs__list' });
    for (const b of bugs) {
      const li = document.createElement('li');
      li.className = 'td-linked-bugs__item';

      const a = document.createElement('a');
      a.className = 'td-linked-bugs__link';
      a.href = `#/bug/${b.id}`;
      const idSpan = document.createElement('span');
      idSpan.className = 'mono';
      idSpan.textContent = b.id;
      a.appendChild(idSpan);
      a.appendChild(document.createTextNode(' — ' + (b.title || '')));

      const pill = document.createElement('span');
      pill.className = `td-linked-bugs__status td-linked-bugs__status--${b.status}`;
      pill.textContent = b.status;

      li.appendChild(a);
      li.appendChild(pill);
      ul.appendChild(li);
    }
    section.appendChild(ul);

    // Insert before the dates footer if it exists; otherwise append.
    const datesEl = bodyEl.querySelector('.td-dates');
    if (datesEl) {
      bodyEl.insertBefore(section, datesEl);
    } else {
      bodyEl.appendChild(section);
    }
  } catch (e) {
    // listBugs failures are non-fatal — task detail still renders without bugs
    console.warn('listBugs failed in task-detail:', e);
  }
}
