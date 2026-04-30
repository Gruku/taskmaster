// Common chrome for every dashboard widget.
// Returns { root, body, destroy(removeFromLayout: boolean) } so the screen can mount widget content.

export function createWidgetFrame({ instance, label, onRemove, onSizeCycle }) {
  const root = document.createElement('section');
  root.className = `widget widget--${instance.size || 'medium'}`;
  root.dataset.instanceId = instance.id;
  root.dataset.widgetType = instance.type;

  const drag = document.createElement('button');
  drag.type = 'button';
  drag.className = 'widget__drag';
  drag.title = 'Drag to reorder';
  drag.setAttribute('aria-label', 'Drag handle');
  drag.textContent = '⋮⋮';
  drag.draggable = true;
  drag.addEventListener('dragstart', (e) => {
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', instance.id);
    root.classList.add('is-dragging');
  });
  drag.addEventListener('dragend', () => root.classList.remove('is-dragging'));

  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'widget__remove';
  remove.title = 'Remove widget';
  remove.setAttribute('aria-label', 'Remove widget');
  remove.textContent = '×';
  remove.addEventListener('click', (e) => {
    e.stopPropagation();
    if (typeof onRemove === 'function') onRemove(instance.id);
  });

  const head = document.createElement('header');
  head.className = 'widget__head';
  const labelEl = document.createElement('span');
  labelEl.className = 'widget__label';
  labelEl.textContent = label;
  head.appendChild(labelEl);

  if (typeof onSizeCycle === 'function') {
    const sizeBtn = document.createElement('button');
    sizeBtn.type = 'button';
    sizeBtn.className = 'widget__size';
    sizeBtn.title = 'Cycle size';
    sizeBtn.textContent = '◐';
    sizeBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      onSizeCycle(instance.id);
    });
    head.appendChild(sizeBtn);
  }

  const body = document.createElement('div');
  body.className = 'widget__body';

  root.append(drag, remove, head, body);

  return {
    root,
    body,
    setLabel(text) { labelEl.textContent = text; },
    setSize(size) {
      root.classList.remove('widget--small', 'widget--medium', 'widget--wide');
      root.classList.add(`widget--${size}`);
    },
  };
}
