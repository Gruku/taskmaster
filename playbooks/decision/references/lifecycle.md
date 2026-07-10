# Decision Lifecycle

## States

- `open` — awaiting user input. Default on creation. Renders on dashboard `Decide` rail.
- `resolved` — option N chosen. Frontmatter: `resolved_with: N`, `resolved_at: <ISO>`, optional `resolved_rationale`.
- `dropped` — no option chosen; circumstances changed. Frontmatter: `dropped_reason: <required>`, `resolved_at: <ISO>`.

## Transitions

```
open --(resolve N)--> resolved   (terminal)
open --(drop reason)--> dropped  (terminal)
```

Terminal states do **not** transition back to `open`. A "reopened" decision is a new decision — link to the prior in the body if relevant.

## Pre-resolution mutability

While `open`, the following may be edited via `backlog_decision(action="update", ...)`:
- `title`
- `options` (list mutates — recommendation index re-validates)
- `recommendation`
- `body`

After resolve/drop, the file is frozen except for `referenced_in` (back-references continue to accrue as new handovers link the decision).
