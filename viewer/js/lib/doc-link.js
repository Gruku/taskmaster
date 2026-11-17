// User intent: one way to turn a task's or epic's doc value (teammate-supplied text) into a link: an http(s) URL opens
// as itself; anything else is a project path served by the viewer's /file/ route, encoded segment by segment so a `#`,
// `?` or dot segment in it can't change which URL is fetched (security review m-10).

export function docLink(value) {
  const text = String(value ?? '').trim();
  if (/^https?:\/\//i.test(text)) return { href: text, target: '_blank', rel: 'noopener noreferrer' };
  const segments = text.replace(/^[/\\]+/, '').split('/').filter((s) => s !== '' && s !== '.' && s !== '..');
  return { href: `/file/${segments.map(encodeURIComponent).join('/')}`, target: '_blank', rel: 'noopener' };
}
