// Links between entities, from an entity's `links` array: [{type, target}, ...].
// One pill per link: `<a class="link-pill">` holding a Technical label, a space, and the id.
// `linkPillsEl(links)` builds the nodes (ids go in as text, never as markup); `renderLinkPills(entity)` is the same
// markup as an escaped string for the screens that still mount one and rewrite its `#<id>` hrefs themselves.

const TYPE_LABELS = {
  depends_on:    "Depends on",
  blocks:        "Blocks",
  fixes:         "Fixes",
  fixed_in_task: "Fixed in",
  relates_to:    "Related",
  supersedes:    "Supersedes",
  superseded_by: "Superseded by",
  duplicate_of:  "Duplicate of",
  duplicates:    "Duplicates",
  references:    "References",
  referenced_by: "Referenced by",
};

// Links in display order — known types first, in the order above — as [{type, label, target}].
// Anything that is not a {type, target} pair is skipped: link data is written by many hands.
function ordered(links) {
  const valid = (Array.isArray(links) ? links : []).filter((l) =>
    l && typeof l === 'object' && typeof l.type === 'string' && l.type
    && ((typeof l.target === 'string' && l.target) || typeof l.target === 'number'));
  const rank = (type) => {
    const i = Object.keys(TYPE_LABELS).indexOf(type);
    return i < 0 ? Infinity : i;
  };
  return valid
    .map((l, i) => ({ type: l.type, label: Object.hasOwn(TYPE_LABELS, l.type) ? TYPE_LABELS[l.type] : l.type, target: String(l.target), i }))
    .sort((x, y) => rank(x.type) - rank(y.type) || x.i - y.i);
}

// Where an id leads. The prefix says what it names; an id with no known prefix is a task.
export function linkRoute(target) {
  const id = String(target ?? '');
  const enc = encodeURIComponent(id);
  if (/^ISS-/i.test(id)) return `#/issue/${enc}`;
  if (/^B-\d/i.test(id)) return `#/bug/${enc}`;
  if (/^IDEA-/i.test(id)) return `#/ideas/${enc}`;
  return `#/task/${enc}`;
}

const typeClass = (type) => `link-pill-${type.replace(/[^a-z0-9_-]/gi, '')}`;

// The pills as nodes, or null when there is nothing to link.
export function linkPillsEl(links) {
  const list = ordered(links);
  if (!list.length) return null;
  const wrap = document.createElement('div');
  wrap.className = 'link-pills';
  for (const { type, label, target } of list) {
    const a = document.createElement('a');
    a.className = `link-pill ${typeClass(type)}`;
    a.setAttribute('href', linkRoute(target));
    const labelEl = document.createElement('span');
    labelEl.className = 'link-pill__label';
    labelEl.textContent = label;
    const idEl = document.createElement('span');
    idEl.className = 'link-pill__id';
    idEl.textContent = target;
    a.append(labelEl, ' ', idEl);
    wrap.appendChild(a);
  }
  return wrap;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

export function renderLinkPills(entity, opts = {}) {
  const list = ordered(entity?.links);
  if (!list.length) return "";
  const pills = list.map(({ type, label, target }) =>
    `<a class="link-pill ${typeClass(type)}" href="#${esc(target)}"><span class="link-pill__label">${esc(label)}</span> <span class="link-pill__id">${esc(target)}</span></a>`);
  return `<div class="link-pills">${pills.join("")}</div>`;
}

export function legacyLinksToTyped(entity, kind) {
  // Mirror of the Python translator — used by the viewer when reading
  // pre-migration projects that haven't run migrate_links.py yet.
  const out = Array.isArray(entity.links) ? [...entity.links] : [];
  const seen = new Set(out.map((l) => `${l.type}:${l.target}`));
  const push = (type, target) => {
    if (!target) return;
    const key = `${type}:${target}`;
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ type, target });
  };
  const rules = {
    task: [
      ["depends_on", "depends_on", true],
      ["related_issues", "relates_to", true],
    ],
    issue: [
      ["related_tasks", "relates_to", true],
      ["fixed_in_task", "fixed_in_task", false],
      ["duplicate_of", "duplicate_of", false],
    ],
    handover: [
      ["supersedes", "supersedes", true],
      ["superseded_by", "superseded_by", true],
    ],
    idea: [["related_tasks", "relates_to", true]],
  };
  for (const [field, type, isList] of rules[kind] || []) {
    const raw = entity[field];
    if (raw == null || raw === "" || (Array.isArray(raw) && raw.length === 0)) continue;
    const targets = isList && Array.isArray(raw) ? raw : [raw];
    for (const t of targets) push(type, t);
  }
  return out;
}

export function countLinks(entity, kind) {
  const links = entity.links && entity.links.length
    ? entity.links
    : legacyLinksToTyped(entity, kind);
  return links.length;
}
