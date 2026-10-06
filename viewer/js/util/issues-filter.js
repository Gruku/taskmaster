// User intent: what the Issues board shows for a search and its chips — a search reaches every field a person would look
// for an issue by (legacy fields too), a severity is one word whatever its spelling, and the chip groups AND together.
import { severityKey } from '../components/status.js';
import { legacyLinksToTyped } from '../components/link-pills.js';
import { issueEvidence } from './issue-fields.js';

const RESOLVED = new Set(['fixed', 'wontfix', 'duplicate']);

export function issueSeverity(issue) {
  return severityKey(issue?.severity_label ?? issue?.severity);
}

export function isResolvedIssue(issue) {
  return RESOLVED.has(issue?.status);
}

const list = (value) => (Array.isArray(value) ? value : value == null || value === '' ? [] : [value]);

export function issueMatchesSearch(issue, term) {
  const q = String(term ?? '').trim().toLowerCase();
  if (!q) return true;
  const i = issue ?? {};
  const links = Array.isArray(i.links) && i.links.length ? i.links : legacyLinksToTyped(i, 'issue');
  return [i.id, i.title, issueEvidence(i), i.component, ...list(i.location), ...links.map((l) => l?.target)]
    .filter((v) => v != null && v !== '').join(' ').toLowerCase().includes(q);
}

const promoted = (issue) => Array.isArray(issue?.promoted_from) && issue.promoted_from.length > 0;

export function filterIssues(issues, { search = '', severities = [], components = [], promotedOnly = false } = {}) {
  return (issues ?? []).filter((i) => (!severities.length || severities.includes(issueSeverity(i)))
    && (!components.length || components.includes(i?.component))
    && (!promotedOnly || promoted(i))
    && issueMatchesSearch(i, search));
}
