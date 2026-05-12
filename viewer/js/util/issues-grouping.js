// Pure groupers for the Issues screen kanban views.
// Status grouping mirrors the issue-lifecycle states; Severity grouping
// mirrors the four severity labels used by the rest of the viewer.

export function groupByStatus(issues) {
  const out = { open: [], investigating: [], fixed: [], wontfix: [] };
  for (const i of issues) {
    if (i.status in out) out[i.status].push(i);
  }
  return out;
}

export function groupBySeverity(issues) {
  const out = { Critical: [], High: [], Medium: [], Low: [] };
  for (const i of issues) {
    const lbl = i.severity_label;
    if (lbl && lbl in out) out[lbl].push(i);
  }
  return out;
}
