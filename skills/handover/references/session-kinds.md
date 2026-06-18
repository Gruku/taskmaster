# Handover Session Kinds

Four values. Pick exactly one. Kind drives resume-load behavior and archive policy.

| Kind | When | Resume load | Archive |
|---|---|---|---|
| `continuity` | Default. End-of-day, exploration, generic wrap-up. | Frontmatter only. | FIFO past cap-30. |
| `deep-context` | Pre-compaction safety capture (≥200k tokens; user said "near compaction"). | **Body loaded** — current session about to die. | FIFO past cap-30. |
| `milestone` | Chunk shipped OR direction changed. Body explains which. | **Body loaded.** | Chained supersession on same `task_ids`. |
Legacy values `end-of-day`, `exploration`, `pivot`, `context-handoff`, `milestone-complete` still accepted on write — they map to the new names. Read code should always see the normalized value.

## Choosing the right kind

1. User said "near compaction" / "300k" / "save before compact"? → `deep-context`.
2. Chunk shipped, direction changed, plan switch, "ready for next plan"? → `milestone`.
3. Otherwise → `continuity`.
