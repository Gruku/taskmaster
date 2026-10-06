// Pure groupers for the Issues board's columns.
// Status grouping has every issue-lifecycle state the server knows (duplicate included); Severity grouping uses the
// lower-case severity keys of status.js, so a 'P2' and a 'Medium' land in the same column.
import { issueSeverity } from './issues-filter.js';

export function groupByStatus(issues) {
  const out = { open: [], investigating: [], fixed: [], wontfix: [], duplicate: [] };
  for (const i of issues ?? []) {
    if (Object.hasOwn(out, i?.status)) out[i.status].push(i);
  }
  return out;
}

export function groupBySeverity(issues) {
  const out = { critical: [], high: [], medium: [], low: [] };
  for (const i of issues ?? []) {
    const key = issueSeverity(i);
    if (key) out[key].push(i);
  }
  return out;
}
