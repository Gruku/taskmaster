// User intent: single definition of which issue fields hold the discovery date and the evidence text.
export function issueDiscovered(issue) {
  return issue?.discovered ?? issue?.created ?? null;
}
export function issueEvidence(issue) {
  return issue?.evidence ?? issue?.symptom ?? '';
}
