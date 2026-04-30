// Mono pills showing lesson anchor file patterns, prefixed with a small "When:" label.
// Reads from lesson.triggers.files. Empty triggers render "When: (any file)" in muted ink.

export function anchorPills(lesson) {
  const triggers = (lesson.triggers && lesson.triggers.files) || [];
  const wrap = document.createElement('div');
  wrap.className = 'anchor-pills';

  const label = document.createElement('span');
  label.className = 'anchor-pills__label';
  label.textContent = 'When:';
  wrap.appendChild(label);

  if (triggers.length === 0) {
    const empty = document.createElement('span');
    empty.className = 'anchor-pills__empty';
    empty.textContent = '(any file)';
    wrap.appendChild(empty);
    return wrap;
  }
  for (const pat of triggers) {
    const pill = document.createElement('code');
    pill.className = 'anchor-pills__pill';
    pill.textContent = pat;
    wrap.appendChild(pill);
  }
  return wrap;
}

export default anchorPills;
