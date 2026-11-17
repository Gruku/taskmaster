// Markdown rendering for the app's modules — the module face of js/lib/md-safe.js, the one allow-list sanitiser that the
// sandboxed /file/ page loads as a classic script too. `renderMarkdown(src)` returns an HTML string ready for .innerHTML;
// `mountMarkdown(element, src)` does the assignment; `isSafeUrl(url)` says whether a link target may be followed.
import '../lib/md-safe.js';

export const renderMarkdown = (src) => globalThis.TaskmasterMarkdown.render(src);
export const isSafeUrl = (url) => globalThis.TaskmasterMarkdown.isSafeUrl(url);

export function mountMarkdown(el, src) {
  el.innerHTML = renderMarkdown(src);
}
