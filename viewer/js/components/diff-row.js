// Single +/~/- row, used by recap-receipts-grid and sessions detail-rail.
// Pure render: takes plain data, returns an HTML string. The caller injects.

const PREFIX = { add: '+', mod: '~', del: '-' };

export function renderDiffRow({ kind, body }) {
  const k = (kind in PREFIX) ? kind : 'mod';
  return (
    `<div class="diff-row ${k}">`
    + `<span class="pre">${PREFIX[k]}</span>`
    + `<span class="body">${body}</span>`
    + `</div>`
  );
}

export default renderDiffRow;
