// viewer/js/components/edit/forms/idea-form.js
// User intent: a new idea is written in the same labelled form as a task — its own statuses in their own words, and the
// tags already in use offered as the user types, so one idea's "ux" is not the next one's "UX-stuff".
//
// `getIdeas: () => ideas` supplies the ideas loaded on the screen, which the tag suggestions are drawn from.

import { TextField }  from '../fields/text-field.js';
import { MdField }    from '../fields/md-field.js';
import { EnumSelect } from '../fields/enum-select.js';
import { ChipInput }  from '../fields/chip-input.js';
import { IDEA_STATUS } from '../../status.js';

const STATUS_OPTIONS = Object.entries(IDEA_STATUS).map(([value, { label }]) => ({ value, label }));
const MAX_SUGGESTIONS = 8;

export function ideaSchema({ getIdeas }) {
  // Distinct tags in the order they were met, matched on the typed text without regard to case.
  const tagSource = async (q) => {
    const needle = String(q).toLowerCase();
    const ideas = getIdeas();
    const found = new Set();
    for (const idea of Array.isArray(ideas) ? ideas : []) {
      for (const tag of Array.isArray(idea?.tags) ? idea.tags : []) {
        if (typeof tag === 'string' && tag && tag.toLowerCase().includes(needle)) found.add(tag);
        if (found.size === MAX_SUGGESTIONS) return [...found];
      }
    }
    return [...found];
  };

  return {
    entity: 'idea',
    label: 'Idea',
    fields: [
      { key: 'title',  label: 'Title',  renderer: TextField,  group: 'basics', wide: true, required: true, maxLength: 140 },
      { key: 'status', label: 'Status', renderer: EnumSelect, group: 'basics', required: true, options: STATUS_OPTIONS },
      { key: 'tags',   label: 'Tags',   renderer: ChipInput,  group: 'basics', allowFree: true,
        placeholder: 'add a tag…', source: tagSource },
      { key: 'body',   label: 'Body',   renderer: MdField,    group: 'content', open: true },
    ],
    systemManaged: ['id', 'created', 'updated', 'archived', 'promoted_to'],
  };
}
