// User intent: single definition of which issue fields hold the discovery date and the evidence text.
export function issueDiscovered(issue) {
  return issue?.discovered ?? issue?.created ?? null;
}
export function issueEvidence(issue) {
  return issue?.evidence ?? issue?.symptom ?? '';
}

// A card's evidence is a plain-text preview: the page renders the markdown, the card shows its words without the marks.
export function plainMarkdown(src) {
  return String(src ?? '')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+)/gm, '')
    .replace(/(\*\*|__)(.+?)\1/g, '$2')
    .replace(/(\*|_)(?=\S)(.+?)(?<=\S)\1/g, '$2')
    .replace(/~~(.+?)~~/g, '$1')
    .replace(/`([^`]+)`/g, '$1');
}
