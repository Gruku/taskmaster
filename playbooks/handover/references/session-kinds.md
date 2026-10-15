# Handover Session Kinds

Three values; pick one. The kind drives how much a resuming session loads and how the handover is archived.

| Kind | When | Resume load | Archive |
|---|---|---|---|
| `continuity` | Default. End-of-day, exploration, generic wrap-up. | Frontmatter only. | FIFO past the 30-entry cap. |
| `deep-context` | Pre-compaction safety capture; the user said "near compaction" or "save before compact". | Body loaded, because the current session is about to end. | FIFO past the 30-entry cap. |
| `milestone` | A chunk shipped or direction changed; the body explains which. | Body loaded. | Chained supersession on the same `task_ids`. |

The server also accepts the older names `end-of-day`, `exploration`, `pivot`, `context-handoff` and `milestone-complete` on write and maps them to the three above; write the new names.

## Choosing

1. The user said "near compaction" or "save before compact": `deep-context`.
2. A chunk shipped, direction changed, the plan switched, or "ready for next plan": `milestone`.
3. Otherwise: `continuity`.
