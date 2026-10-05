// viewer/js/components/edit/idea-actions.js
// User intent: creating an idea is the shared entity form, so it is dirty only when something was typed, sends the
// defaults plus what was typed and nothing else, and says a refusal in words.

import { openEntityModal } from './entity-modal.js';
import { describeWriteError } from './write-errors.js';
import { ideaSchema } from './forms/idea-form.js';
import { createIdea } from '../../api.js';

// `onCreated(result)` refreshes the screen's list. The idea has been written by then; a refresh that fails must not
// hold the form open, or a second Save would create the same idea again.
export function openIdeaCreateModal({ store, onCreated }) {
  const schema = ideaSchema({ getIdeas: () => store.getIdeas() });
  openEntityModal({
    schema, mode: 'create',
    // The defaults are where the form starts, not an edit: an untouched form has nothing to save.
    initialEntity: { status: 'exploring', tags: [] },
    onSave: async (draft) => {
      let result;
      try {
        result = await createIdea(draft);
      } catch (e) {
        return { error: describeWriteError(e, { noun: 'idea' }) };
      }
      await Promise.resolve().then(() => onCreated?.(result)).catch(() => {});
    },
    onCancel: () => {},
  });
}
