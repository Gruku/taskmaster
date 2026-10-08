// User intent: every field can be tied to its visible label and its error message, whatever control it is built from,
// and a form — not each field — decides where focus goes when twenty fields mount at once.

// `id` goes on the control a label click should reach; `describedBy` names the element holding its error or hint.
export function bindControl(control, { id, describedBy } = {}) {
  if (id) control.id = id;
  if (describedBy) control.setAttribute('aria-describedby', describedBy);
  return control;
}

// Escape is a field's own key only when it has an edit to cancel: inline editing passes `onCancel`, a form does not,
// and an unclaimed Escape is left for the modal around the field.
export function cancelOnEscape(e, onCancel) {
  if (e.key !== 'Escape' || !onCancel) return false;
  e.preventDefault();
  onCancel();
  return true;
}

// Inline editing mounts one control and wants the caret in it; a form passes autoFocus: false.
export function focusOnMount(control, autoFocus, { select = false } = {}) {
  if (!autoFocus) return;
  queueMicrotask(() => {
    control.focus();
    if (select) control.select?.();
  });
}
