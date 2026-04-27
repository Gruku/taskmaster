export const meta = { title: 'Task Detail', icon: '▣', sidebarKey: null };

export async function mount(root, { subpath }) {
  const taskId = subpath[0] || '(no task)';
  const el = document.createElement('div');
  el.className = 'stub';
  el.innerHTML = `
    Task Detail placeholder.
    <div class="stub-meta">id=${escapeHtml(taskId)} — Plan 3 fills in Variant A (document) and Variant B (graph).</div>
  `;
  root.appendChild(el);
  return () => {};
}

function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
