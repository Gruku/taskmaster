import { addWidget, removeWidget, moveWidget } from './dashboard-grid.js';
import { listWidgets, getWidget } from './widget-catalog.js';

export function createEditMode({ root, api, prefs, refresh }) {
  let editing = false;

  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'dash-edit-toggle';
  toggle.textContent = '✎ Edit layout';
  toggle.setAttribute('aria-pressed', 'false');
  toggle.addEventListener('click', () => {
    editing = !editing;
    toggle.setAttribute('aria-pressed', String(editing));
    toggle.textContent = editing ? '✓ Done' : '✎ Edit layout';
    root.dataset.edit = editing ? '1' : '0';
  });

  async function onRemove(instanceId) {
    const layout = (prefs.dashboard && prefs.dashboard.layout) || [];
    const next = removeWidget(layout, instanceId);
    await api.savePrefs({ dashboard: { layout: next } });
    prefs.dashboard.layout = next;
    refresh();
  }

  async function onAdd(rail, type) {
    const layout = (prefs.dashboard && prefs.dashboard.layout) || [];
    const next = addWidget(layout, type, { rail });
    await api.savePrefs({ dashboard: { layout: next } });
    prefs.dashboard.layout = next;
    refresh();
  }

  async function onMove(instanceId, target) {
    const layout = (prefs.dashboard && prefs.dashboard.layout) || [];
    const next = moveWidget(layout, instanceId, target);
    await api.savePrefs({ dashboard: { layout: next } });
    prefs.dashboard.layout = next;
    refresh();
  }

  function isEditing() { return editing; }

  return { toggle, onRemove, onAdd, onMove, isEditing };
}

export function createAddTile({ rail, onAdd }) {
  const tile = document.createElement('button');
  tile.type = 'button';
  tile.className = 'dash-add-tile';
  tile.textContent = '＋ Add widget';
  tile.addEventListener('click', () => {
    showPicker(tile, rail, onAdd);
  });
  return tile;
}

function showPicker(anchor, rail, onAdd) {
  const existing = document.querySelector('.dash-picker');
  if (existing) existing.remove();
  const picker = document.createElement('div');
  picker.className = 'dash-picker';
  for (const meta of listWidgets()) {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'dash-picker__item';
    item.innerHTML = `<span class="dash-picker__item-label">${meta.label}</span><span class="dash-picker__item-sub">${meta.id}</span>`;
    item.addEventListener('click', async () => {
      picker.remove();
      await onAdd(rail, meta.id);
    });
    picker.appendChild(item);
  }
  document.body.appendChild(picker);
  const r = anchor.getBoundingClientRect();
  picker.style.left = `${r.left}px`;
  picker.style.top = `${r.bottom + 4}px`;
  setTimeout(() => {
    document.addEventListener('mousedown', function close(e) {
      if (!picker.contains(e.target)) {
        picker.remove();
        document.removeEventListener('mousedown', close);
      }
    });
  }, 0);
}

export function attachRailDropTarget(railEl, rail, onMove) {
  railEl.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    railEl.classList.add('is-drop-target');
  });
  railEl.addEventListener('dragleave', () => {
    railEl.classList.remove('is-drop-target');
  });
  railEl.addEventListener('drop', async (e) => {
    e.preventDefault();
    railEl.classList.remove('is-drop-target');
    const id = e.dataTransfer.getData('text/plain');
    if (!id) return;
    // Find drop index by counting widgets above the cursor.
    const widgets = Array.from(railEl.querySelectorAll('.widget'));
    const cursorY = e.clientY;
    let index = widgets.length;
    for (let i = 0; i < widgets.length; i++) {
      const r = widgets[i].getBoundingClientRect();
      if (cursorY < r.top + r.height / 2) { index = i; break; }
    }
    await onMove(id, { rail, index });
  });
}
