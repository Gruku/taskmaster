// Single source of truth for which widgets exist on the dashboard.
// Widget modules are registered here; the catalog feeds the Add-Widget picker
// and the orchestrator's mount loop.

const REGISTRY = new Map();

export function registerWidget(mod) {
  if (!mod || !mod.meta || !mod.meta.id) {
    throw new Error('registerWidget: module must export `meta` with an id');
  }
  if (typeof mod.mount !== 'function') {
    throw new Error(`registerWidget(${mod.meta.id}): module must export an async mount()`);
  }
  REGISTRY.set(mod.meta.id, mod);
}

export function getWidget(id) {
  return REGISTRY.get(id);
}

export function listWidgets() {
  return Array.from(REGISTRY.values()).map(m => m.meta);
}

export function defaultLayout() {
  // Sensible first-run seed. Mirrors the dashboard-v5 mockup.
  return [
    { id: 'sn-0',  type: 'suggested-next',     size: 'medium', rail: 'left',   index: 0 },
    { id: 'pd-0',  type: 'phase-deliverables', size: 'medium', rail: 'left',   index: 1 },
    { id: 'nu-0',  type: 'newly-unblocked',    size: 'medium', rail: 'left',   index: 2 },
    { id: 'wc-0',  type: 'what-changed',       size: 'medium', rail: 'right',  index: 0 },
    { id: 'ls-0',  type: 'last-session',       size: 'medium', rail: 'right',  index: 1 },
    { id: 'oi-0',  type: 'open-issues',        size: 'medium', rail: 'right',  index: 2 },
    { id: 'btp-0', type: 'build-test-pulse',   size: 'small',  rail: 'bottom', index: 0 },
    { id: 'ld-0',  type: 'lessons-digest',     size: 'small',  rail: 'bottom', index: 1 },
    { id: 'rc-0',  type: 'recent-commits',     size: 'small',  rail: 'bottom', index: 2 },
    { id: 'aa-0',  type: 'agent-activity',     size: 'small',  rail: 'bottom', index: 3 },
  ];
}
