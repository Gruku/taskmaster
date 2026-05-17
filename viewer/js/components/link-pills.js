// Render a grouped links block from an entity's `links` array.
// Shape: entity.links = [{type, target}, ...]
// Renders one row per type, with pill chips for each target.
//
// UI rules (per project CLAUDE.md):
// - No left-colored rails on chips/cards
// - No motion on hover (no transform/translate/scale)
// - No box-shadow for elevation (use surface stepping)
// Hover states change color/border/background only.

const TYPE_LABELS = {
  depends_on:    "Depends on",
  blocks:        "Blocks",
  fixes:         "Fixes",
  fixed_in_task: "Fixed in",
  relates_to:    "Related",
  informed_by:   "Informed by",
  informs:       "Informs",
  supersedes:    "Supersedes",
  superseded_by: "Superseded by",
  duplicate_of:  "Duplicate of",
  duplicates:    "Duplicates",
  references:    "References",
  referenced_by: "Referenced by",
};

function groupByType(links) {
  const out = {};
  for (const link of links || []) {
    if (!out[link.type]) out[link.type] = [];
    out[link.type].push(link.target);
  }
  return out;
}

export function renderLinkPills(entity, opts = {}) {
  const links = entity.links || [];
  if (links.length === 0) return "";
  const grouped = groupByType(links);
  const typeOrder = Object.keys(TYPE_LABELS).filter((t) => grouped[t]);

  const rows = typeOrder.map((type) => {
    const label = TYPE_LABELS[type] || type;
    const chips = grouped[type]
      .map((target) => `<a class="link-pill link-pill-${type}" href="#${target}">${target}</a>`)
      .join(" ");
    return `<div class="link-row"><span class="link-label">${label}</span>${chips}</div>`;
  });
  return `<div class="link-pills">${rows.join("")}</div>`;
}

export function legacyLinksToTyped(entity, kind) {
  // Mirror of the Python translator — used by the viewer when reading
  // pre-migration projects that haven't run migrate_links.py yet.
  const out = [...(entity.links || [])];
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
      ["related_lessons", "informed_by", true],
    ],
    issue: [
      ["related_tasks", "relates_to", true],
      ["fixed_in_task", "fixed_in_task", false],
      ["duplicate_of", "duplicate_of", false],
    ],
    lesson: [
      ["related_tasks", "informs", true],
      ["related_issues", "relates_to", true],
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
    const targets = isList ? raw : [raw];
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
