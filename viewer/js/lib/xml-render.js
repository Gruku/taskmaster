// XML tag rendering for continuity dashboard content.
//
// Recognized tags surface as styled chips (inline) or as labelled blocks
// (block). Unrecognized tags are left as plain text — we don't want to
// HTML-inject arbitrary content, and most strings carry no tags at all.
import { h } from '../util/h.js';

// Tag → display label and visual class. The class hooks into co-xtag--<key>
// rules in continuity.css.
export const KNOWN_TAGS = {
  'lesson-candidate': { label: 'Lesson candidate', cls: 'lc'  },
  'thinking':         { label: 'Thinking',         cls: 'th'  },
  'example':          { label: 'Example',          cls: 'ex'  },
  'system-reminder':  { label: 'System reminder',  cls: 'sr'  },
  'decision':         { label: 'Decision',         cls: 'dec' },
  'issue':            { label: 'Issue',            cls: 'iss' },
};

const KNOWN_NAMES = Object.keys(KNOWN_TAGS).join('|');
const TAG_PATTERN = `<(${KNOWN_NAMES})>([\\s\\S]*?)</\\1>`;
// Non-global detector used by hasKnownTags — keeps that path stateless.
const DETECT_RE = new RegExp(TAG_PATTERN);

// Iteration uses a fresh /g regex per call so shared lastIndex state can
// never leak across callers (concurrent or nested).
function makeGlobalRe() { return new RegExp(TAG_PATTERN, 'g'); }

/** True if the string contains at least one recognized XML tag. */
export function hasKnownTags(s) {
  if (typeof s !== 'string' || !s) return false;
  return DETECT_RE.test(s);
}

/**
 * Inline rendering: returns an array of DOM nodes (text + chips) suitable for
 * appending into a one-line container. Each tag becomes a small chip with the
 * label and a short inner preview as the title attribute.
 *
 * @param {string} text  raw text potentially containing recognized tags
 * @param {Object} [opts]
 * @param {number} [opts.maxInnerChars=80]  inner text length used in chip title tooltip
 * @returns {Node[]}
 */
export function renderInline(text, { maxInnerChars = 80 } = {}) {
  const out = [];
  if (typeof text !== 'string' || !text) return out;
  const re = makeGlobalRe();
  let lastIdx = 0;
  let m;
  while ((m = re.exec(text)) != null) {
    const [full, name, inner] = m;
    if (m.index > lastIdx) {
      out.push(document.createTextNode(text.slice(lastIdx, m.index)));
    }
    const meta = KNOWN_TAGS[name];
    const trimmedInner = (inner || '').trim();
    const preview = trimmedInner.length > maxInnerChars
      ? trimmedInner.slice(0, maxInnerChars - 1) + '…'
      : trimmedInner;
    const chip = h('span', {
      class: `co-xtag co-xtag--${meta.cls}`,
      title: preview || meta.label,
    }, meta.label);
    out.push(chip);
    lastIdx = m.index + full.length;
  }
  if (lastIdx < text.length) {
    out.push(document.createTextNode(text.slice(lastIdx)));
  }
  return out;
}

/**
 * Block rendering: returns a single container with paragraphs for plain text
 * and labelled blocks for recognized tags. Suitable for handover bodies and
 * decision rationales shown in an expanded panel.
 *
 * @param {string} text
 * @returns {HTMLElement}
 */
export function renderBlock(text) {
  const wrap = h('div', { class: 'co-xblock' });
  if (typeof text !== 'string' || !text) return wrap;
  const re = makeGlobalRe();
  let lastIdx = 0;
  let m;
  function appendText(slice) {
    if (!slice) return;
    // Preserve paragraph structure: split on blank lines, each paragraph is a <p>.
    const paras = slice.split(/\n{2,}/);
    for (const p of paras) {
      const t = p.trim();
      if (!t) continue;
      wrap.appendChild(h('p', { class: 'co-xblock__p' }, t));
    }
  }
  while ((m = re.exec(text)) != null) {
    const [full, name, inner] = m;
    appendText(text.slice(lastIdx, m.index));
    const meta = KNOWN_TAGS[name];
    const block = h('div', { class: `co-xblock__tag co-xblock__tag--${meta.cls}` },
      h('div', { class: 'co-xblock__tag-label' }, meta.label),
      h('div', { class: 'co-xblock__tag-body' }, (inner || '').trim()),
    );
    wrap.appendChild(block);
    lastIdx = m.index + full.length;
  }
  appendText(text.slice(lastIdx));
  return wrap;
}
