// Map raw severity codes to user-facing words.
// Words only — never P0/P1 in the UI.

const MAP = { P0: 'Critical', P1: 'High', P2: 'Medium', P3: 'Low' };

export function severityLabel(code) {
  return MAP[code] || code || 'Medium';
}

export default severityLabel;
