# Bug vs Issue: routing at intake

The Issue bar (recurring, systemic, or outstanding) is defined once, in `../../issue/references/issue-bar.md`. When in doubt, file a Bug.

## Word-agnostic intake

Users say "issue" colloquially when they mean "bug". Treat the word as a hint and route on the evidence: when the defect cites recurrence, systemic scope or an outstanding P0/P1, it's an Issue (`taskmaster:issue`); otherwise it's a Bug. Severity alone doesn't qualify a defect as an Issue.

When you file a Bug for someone who said "issue", tell them why, so the override isn't a surprise:

> Logged as **B-NNN**: it reads as a single defect. If it's been recurring or spans components, tell me where and I'll promote it to an Issue.

## Examples

| Finding | Verdict | Reason |
|---|---|---|
| "Modal close button is 1px off" | Bug | Cosmetic, single location, no recurrence. |
| "Login fails for users on Safari 17 only" | Bug | Single platform, workaround exists, no recurrence claim. |
| "Migration drops all rows when run on stage" | Issue | Outstanding: concrete data-loss blast radius. |
| "Path mismatch broke handovers AND ideas AND issues, same root cause" | Issue | Systemic: one class of defect across 3 components. |
| "Tool-call orphaning happened in T-001, T-007, and now T-018" | Issue | Recurring: 2+ prior occurrences cited concretely. |

A cosmetic defect with a working fallback is a Bug even at P3; `issue-bar.md` walks a real example.
