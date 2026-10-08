// User intent: single definition of which issue fields hold the discovery date and the evidence text.
export function issueDiscovered(issue) {
  return issue?.discovered ?? issue?.created ?? null;
}
export function issueEvidence(issue) {
  return issue?.evidence ?? issue?.symptom ?? '';
}

// A card's evidence is a plain-text preview: the page renders the markdown, the card shows its words without the marks.
// Marks never eat a word's own characters: code spans are set aside first and put back verbatim, `_` and `__` count as
// emphasis only at word boundaries (CommonMark's intraword rule: test_suite_speed.py, _rebuild_related), and a lone
// `__name__` is read as a Python dunder, not strong text.
const UNDER = /(?<![\p{L}\p{N}_])(__|_)(?=\S)(.+?)(?<=\S)\1(?![\p{L}\p{N}_])/gu;
export function plainMarkdown(src) {
  const code = [];
  return String(src ?? '')
    .replace(/`([^`]+)`/g, (_, c) => `\u0000${code.push(c) - 1}\u0000`)
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+)/gm, '')
    .replace(/\*\*(?=\S)(.+?)(?<=\S)\*\*/g, '$1')
    .replace(/\*(?=\S)(.+?)(?<=\S)\*/g, '$1')
    .replace(UNDER, (m, mark, inner) => (mark === '__' && /^\w+$/.test(inner) ? m : inner))
    .replace(/~~(.+?)~~/g, '$1')
    .replace(/\u0000(\d+)\u0000/g, (_, i) => code[Number(i)]);
}
