// SVG hexagon glyph for severity. One <symbol> def block goes once into the page;
// individual cards reference via <svg><use href="#sev-hex"/></svg>.
//
// Sizes per severity (px width/height of the rendered SVG):
//   Critical: 18, High: 16, Medium: 14, Low: 12

const SIZE_BY_LABEL = { Critical: 18, High: 16, Medium: 14, Low: 12 };
const COLOR_BY_LABEL = {
  Critical: 'var(--sev-critical, #e87a85)',
  High:     'var(--sev-high, #e8a34d)',
  Medium:   'var(--sev-medium, #c8b75a)',
  Low:      'var(--sev-low, #888c95)',
};

let _defsInjected = false;

export function injectSeverityDefs(doc = document) {
  if (_defsInjected) return;
  _defsInjected = true;
  const svg = doc.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', '0');
  svg.setAttribute('height', '0');
  svg.setAttribute('aria-hidden', 'true');
  svg.style.position = 'absolute';
  svg.innerHTML = `
    <defs>
      <symbol id="sev-hex" viewBox="0 0 20 20">
        <polygon points="10,2 17,6 17,14 10,18 3,14 3,6"
                 fill="currentColor" fill-opacity="0.55"
                 stroke="currentColor" stroke-width="1.8"
                 stroke-linejoin="round"/>
      </symbol>
    </defs>`;
  doc.body.appendChild(svg);
}

export function severityGlyph(label) {
  injectSeverityDefs();
  const size = SIZE_BY_LABEL[label] || 14;
  const color = COLOR_BY_LABEL[label] || COLOR_BY_LABEL.Medium;
  const el = document.createElement('span');
  el.className = 'sev-glyph';
  el.setAttribute('data-severity', label);
  el.style.color = color;
  el.style.display = 'inline-flex';
  el.style.width = `${size}px`;
  el.style.height = `${size}px`;
  el.innerHTML = `<svg width="${size}" height="${size}" aria-label="${label} severity"><use href="#sev-hex"/></svg>`;
  return el;
}

export default severityGlyph;
