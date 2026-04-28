// Shared right-rail used by Task Detail Variants A and B.
// `mountRightRail(root, { task, related, onNavigate })` renders six panels:
//   Docs · Lessons in scope · Handovers · Issues · Dependencies + Unblocks · Blockers
// Returns a cleanup function.

export function mountRightRail(root, { task, related, onNavigate }) {
  root.innerHTML = '';
  root.classList.add('td-rail');

  root.appendChild(panelDocs(task));
  root.appendChild(panelLessons(related?.lessons || []));
  root.appendChild(panelHandovers(related?.handovers || []));
  root.appendChild(panelIssues(related?.issues || [], onNavigate));
  root.appendChild(panelDeps(related?.dependencies || [], related?.unblocks || [], onNavigate));
  root.appendChild(panelBlockers(task?.blockers || []));

  return () => { root.innerHTML = ''; };
}

function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') el.className = v;
    else if (k === 'on') for (const [evt, fn] of Object.entries(v)) el.addEventListener(evt, fn);
    else el.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return el;
}

function panelHeader(label) {
  return h('div', { class: 'td-rail-h' }, label);
}

function panelDocs(task) {
  const docs = task?.docs || {};
  const items = Object.entries(docs).map(([type, href]) =>
    h('a', { class: `td-doc td-doc-${type}`, href, target: '_blank', rel: 'noopener' },
      [h('span', { class: 'td-doc-type' }, type), h('span', { class: 'td-doc-path mono' }, href)])
  );
  return h('section', { class: 'td-panel td-panel-docs' },
    [panelHeader('Docs'), ...(items.length ? items : [h('div', { class: 'td-empty' }, 'no docs')])]);
}

function panelLessons(lessons) {
  if (!lessons.length) {
    return h('section', { class: 'td-panel' },
      [panelHeader('Lessons in scope'), h('div', { class: 'td-empty' }, 'no anchor matches')]);
  }
  return h('section', { class: 'td-panel td-panel-lessons' },
    [panelHeader('Lessons in scope'),
     h('div', { class: 'td-rail-hint' }, 'Surfaced via anchor match'),
     ...lessons.map((l) =>
       h('div', { class: 'td-lesson' },
         [h('span', { class: 'mono td-lesson-id' }, l.id),
          h('span', { class: 'td-lesson-title' }, l.title || ''),
          h('span', { class: 'td-lesson-anchor mono' }, (l.anchors || []).join(' · '))]))]);
}

function panelHandovers(handovers) {
  if (!handovers.length) {
    return h('section', { class: 'td-panel' },
      [panelHeader('Handovers'), h('div', { class: 'td-empty' }, 'none')]);
  }
  return h('section', { class: 'td-panel td-panel-handovers' },
    [panelHeader('Handovers'),
     ...handovers.map((ho) =>
       h('div', { class: `td-handover td-handover-${ho.kind || 'mid-task'}` },
         [h('div', { class: 'mono td-handover-id' }, `${ho.id} · ${ho.kind || ''}`),
          h('blockquote', { class: 'serif td-handover-quote' }, `"${ho.quote || ''}"`)]))]);
}

function panelIssues(issues, onNavigate) {
  if (!issues.length) {
    return h('section', { class: 'td-panel' },
      [panelHeader('Issues'), h('div', { class: 'td-empty' }, 'none')]);
  }
  return h('section', { class: 'td-panel td-panel-issues' },
    [panelHeader('Issues'),
     ...issues.map((i) =>
       h('div', { class: `td-issue td-issue-${(i.severity || '').toLowerCase()}` },
         [h('span', { class: 'mono td-issue-id' }, i.id),
          h('span', { class: 'td-issue-title' }, i.title || ''),
          h('span', { class: 'td-issue-sev' }, i.severity || '')]))]);
}

function panelDeps(deps, unblocks, onNavigate) {
  return h('section', { class: 'td-panel td-panel-deps' },
    [panelHeader('Dependencies'),
     deps.length
       ? h('ul', { class: 'td-dep-list' },
           deps.map((d) =>
             h('li', { class: `td-dep td-dep-${d.status}`,
                       on: { click: () => onNavigate?.(d.id) } },
               [h('span', { class: 'mono' }, d.id), ' ', d.title])))
       : h('div', { class: 'td-empty' }, 'no dependencies'),
     h('div', { class: 'td-rail-h td-rail-h-sub' }, 'Unblocks'),
     unblocks.length
       ? h('ul', { class: 'td-dep-list' },
           unblocks.map((d) =>
             h('li', { class: `td-dep td-dep-${d.status}`,
                       on: { click: () => onNavigate?.(d.id) } },
               [h('span', { class: 'mono' }, d.id), ' ', d.title])))
       : h('div', { class: 'td-empty' }, 'this task gates nothing')]);
}

function panelBlockers(blockers) {
  return h('section', { class: 'td-panel td-panel-blockers' },
    [panelHeader('Blockers'),
     blockers.length
       ? h('ul', { class: 'td-blocker-list' },
           blockers.map((b) => h('li', { class: 'td-blocker' }, typeof b === 'string' ? b : (b.text || JSON.stringify(b)))))
       : h('div', { class: 'td-empty' }, 'none')]);
}
