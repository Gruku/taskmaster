// Click-to-copy helper. Adds a green-flash via class swap.
//
// Usage:
//   const span = document.createElement('span');
//   span.textContent = 'v3-014';
//   span.classList.add('cmp-copy');
//   bindCopy(span, 'v3-014');

const FLASH_MS = 1200;

export async function copyToClipboard(text) {
  if (!text) return false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) {
    // fall through to legacy
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:absolute;left:-9999px;top:0;opacity:0;';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
    return true;
  } catch (e) {
    console.error('copy failed', e);
    return false;
  }
}

export function bindCopy(el, text, opts = {}) {
  const flashClass = opts.flashClass || 'cmp-flash-copied';
  el.style.cursor = 'copy';
  el.addEventListener('click', async (ev) => {
    ev.stopPropagation();
    const ok = await copyToClipboard(text);
    if (ok) {
      el.classList.add(flashClass);
      setTimeout(() => el.classList.remove(flashClass), FLASH_MS);
    }
  });
}
