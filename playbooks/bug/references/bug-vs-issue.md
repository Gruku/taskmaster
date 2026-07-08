# Bug vs Issue — Routing Reference

This page is canonical. When in doubt, file a Bug.

## The bar for Issue

An Issue requires **at least one** of:

1. **Recurring** — cite ≥2 prior occurrences (Bug IDs, task IDs, or session/handover references).
2. **Systemic** — cite ≥2 affected components OR a class-of-defect ("path mismatch across all v3 readers", "every parallel tool-call collapse").
3. **Outstanding** — P0/P1 with concrete blast-radius: data loss, security exposure, prod outage, complete user-flow block.

Severity alone is NOT a path. A "P3 because cosmetic" is a Bug, period. A P2 only qualifies if it's also recurring or systemic.

## Word-agnostic intake

Users say "issue" colloquially when they mean "bug". The router treats the word as a hint, not an instruction. Routing is decided by the evidence.

**Algorithm:**
1. Parse the finding.
2. Check for cited evidence of the bar.
3. If evidence present → Issue. If not → Bug + the explainer echo:

> "Logged as **B-NNN**. You said 'issue' but I filed it as a Bug — it reads as a single defect, not a recurring/systemic pattern. If it's actually been showing up elsewhere or you want it tracked at the Issue tier, tell me what's recurring/systemic about it and I'll promote B-NNN → ISS-NNN."

## Examples

| Finding | Verdict | Reason |
|---|---|---|
| "Modal close button is 1px off" | Bug | Cosmetic, single-location, no recurrence. |
| "Login fails for users on Safari 17 only" | Bug | Single-platform, workaround exists, no recurrence claim. |
| "Migration drops all rows when run on stage" | Issue | Outstanding — concrete data-loss blast radius. |
| "Path mismatch broke handovers AND ideas AND issues — same root cause" | Issue | Systemic — class-of-defect, ≥3 components. |
| "Tool-call orphaning happened in T-001, T-007, and now T-018" | Issue | Recurring — ≥2 prior occurrences cited concretely. |

## Anti-example — `ISS-015`

Before the redesign, this was filed as a P3 Issue:

> Handover status defaults to "open" but viewer expects "todo". Viewer has `|| 'todo'` fallback so filter still works; visual defect is the unstyled status pill.

This is the **canonical anti-example**. Under the new bar:
- Not recurring (single location).
- Not systemic (one file, one default).
- Not outstanding (cosmetic only, no functional impact).

Under the new model, this is a Bug — `B-NNN` with severity P3 if any. Not a tracked Issue with an aging window.
