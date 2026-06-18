import { buildBudgetMeter } from './budget-meter.js';

const SUBAGENT_LABELS = {
  'general-purpose': 'G',
  'Explore': 'E',
  'Plan': 'P',
  'code-reviewer': 'R',
  'code-architect': 'A',
};

const METER_FORMAT = {
  tokens: 'num',
  time_seconds: 'duration',
  context: 'pct',
  cost_usd: 'usd',
};
const METER_LABEL = {
  tokens: 'Tokens',
  time_seconds: 'Time',
  context: 'Context',
  cost_usd: 'Cost',
};

export function renderLeftPanel(root, { state }) {
  root.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'aside aside--left';
  wrap.innerHTML = `
    <div class="aside-h">Subagents</div>
    <div class="aside-subagents"></div>
    <div class="aside-h">Hook firings</div>
    <div class="aside-hooks"></div>
  `;
  root.appendChild(wrap);

  const subRoot = wrap.querySelector('.aside-subagents');
  const subagents = state?.subagents ?? [];
  if (!subagents.length) {
    subRoot.innerHTML = '<div class="aside-empty">none</div>';
  } else {
    const running  = subagents.filter((s) => s.status === 'running');
    const finished = subagents.filter((s) => s.status !== 'running');
    for (const s of [...running, ...finished]) {
      const row = document.createElement('div');
      row.className = 'aside-sub' + (s.status === 'running' ? '' : ' done');
      row.innerHTML = `
        <span class="aside-sub-dot" data-status="${escape(s.status)}"></span>
        <span class="aside-sub-type">${SUBAGENT_LABELS[s.type] ?? s.type}</span>
        <span class="aside-sub-msg">${escape(s.msg ?? s.type ?? '')}</span>
      `;
      subRoot.appendChild(row);
    }
  }

  const hooksRoot = wrap.querySelector('.aside-hooks');
  const hooks = state?.hook_counts ?? {};
  if (!Object.keys(hooks).length) {
    hooksRoot.innerHTML = '<div class="aside-empty">none</div>';
  } else {
    for (const [name, n] of Object.entries(hooks)) {
      const row = document.createElement('div');
      row.className = 'aside-hook';
      row.innerHTML = `<span class="aside-hook-name">${escape(name)}</span><span class="aside-hook-n">×${n}</span>`;
      hooksRoot.appendChild(row);
    }
  }
  return () => { root.innerHTML = ''; };
}

export function renderRightPanel(root, { state, meters }) {
  root.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'aside aside--right';
  wrap.innerHTML = `
    <div class="aside-h">Budget</div>
    <div class="aside-budget"></div>
    <div class="aside-h">Tool log</div>
    <div class="aside-tools"></div>
  `;
  root.appendChild(wrap);

  const budgetRoot = wrap.querySelector('.aside-budget');
  for (const [key, m] of Object.entries(meters || {})) {
    budgetRoot.appendChild(buildBudgetMeter({
      label: METER_LABEL[key] ?? key,
      used: m.used, limit: m.limit, pct: m.pct, tier: m.tier,
      format: METER_FORMAT[key] ?? 'num',
    }));
  }

  const toolsRoot = wrap.querySelector('.aside-tools');
  const tools = (state?.tool_log ?? []).slice(-4).reverse();
  if (!tools.length) {
    toolsRoot.innerHTML = '<div class="aside-empty">none</div>';
  } else {
    for (const t of tools) {
      const row = document.createElement('div');
      row.className = 'aside-tool';
      row.innerHTML = `
        <span class="aside-tool-name">${escape(t.name ?? '')}</span>
        <span class="aside-tool-args">${escape(t.args ?? '')}</span>
        <span class="aside-tool-ts">${shortTs(t.ts)}</span>
      `;
      toolsRoot.appendChild(row);
    }
  }
  return () => { root.innerHTML = ''; };
}

function shortTs(iso) {
  if (!iso) return '';
  const m = /T(\d{2}:\d{2}:\d{2})/.exec(iso);
  return m ? m[1] : iso;
}
function escape(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}
