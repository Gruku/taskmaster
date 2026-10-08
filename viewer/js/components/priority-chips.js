// User intent: the Kanban Priority filter row speaks in full words with one open-task count per chip.
import { PRIORITY } from './status.js';

// Chip specs for the Priority chipRow: the full word, the open count, pressed while active.
export function priorityChips(active = [], counts = new Map()) {
  return ['critical', 'high', 'medium', 'low'].map((value) => ({
    value,
    label: PRIORITY[value].label,
    pressed: active.includes(value),
    count: counts.get(value) ?? 0,
  }));
}
