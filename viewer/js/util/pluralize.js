// Choose singular vs. plural based on count. Always pass plural explicitly
// for irregulars / -es nouns (match → matches, child → children); the
// singular + 's' default only saves keystrokes for the regular case.
//   pluralize(1, 'issue', 'issues')   → 'issue'
//   pluralize(2, 'issue', 'issues')   → 'issues'
//   pluralize(0, 'match', 'matches')  → 'matches'

export function pluralize(n, singular, plural) {
  return Number(n) === 1 ? singular : (plural || singular + 's');
}

export default pluralize;
