import { registerWidget } from '../widget-catalog.js';

export const meta = {
  id: 'build-test-pulse',
  label: 'Build & test pulse',
  sizes: ['small', 'medium'],
  defaultSize: 'small',
  defaultRail: 'bottom',
};

export async function mount(el, { api }) {
  let pulse = { build: 'unknown', tests: { passed: 0, failed: 0, total: 0 }, ts: null };
  try { pulse = await api.getBuildTestPulse(); } catch (_) { /* keep defaults */ }
  el.replaceChildren();
  const dot = (color) => `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:6px;vertical-align:middle;"></span>`;
  const buildColor = pulse.build === 'pass' ? '#5fcdb8' : pulse.build === 'fail' ? '#e87a85' : '#8a93a3';
  const testColor  = (pulse.tests.failed || 0) === 0 ? '#5fcdb8' : '#e87a85';
  const wrap = document.createElement('div');
  wrap.style.cssText = 'display:flex;flex-direction:column;gap:4px;font-size:12px;';
  wrap.innerHTML = `
    <div>${dot(buildColor)}<span>Build: <strong>${pulse.build}</strong></span></div>
    <div>${dot(testColor)}<span>Tests: ${pulse.tests.passed}/${pulse.tests.total} passed</span></div>
    <div style="font-family:var(--font-mono);color:var(--ink-3);font-size:10px;">${pulse.ts || ''}</div>
  `;
  el.appendChild(wrap);
  return () => {};
}

registerWidget({ meta, mount });
