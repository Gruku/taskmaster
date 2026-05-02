# v3-polish-013 — End-to-end UI/UX review

> Task: v3-polish-013 (high, L). Walkthrough of every viewer screen + shared chrome.
> Started: 2026-05-02
> Server: http://127.0.0.1:8765/v3/
> Fixture: `.fixture-kanban/`

## Severity legend

- **bug** — broken behavior (data not loading, layout breaks, dead clicks, uninterpolated templates)
- **inconsistency** — drift between this screen and others (button styles, empty-state copy, header structure, date format)
- **friction** — works but awkward (confusing affordances, hidden state, no feedback)
- **gap** — missing affordance (no loading state, no empty state, no error state)
- **polish** — visual nit (spacing, color, copy tone)

## Per-screen findings

| Screen | URL | File | Findings |
|---|---|---|---|
| Dashboard | `/v3/#/dashboard` | [dashboard.md](dashboard.md) | 11 |
| Kanban | `/v3/#/kanban` | [kanban.md](kanban.md) | 18 |
| Table | `/v3/#/table` | [table.md](table.md) | 15 |
| Sessions | `/v3/#/sessions` | [sessions.md](sessions.md) | 11 |
| Recap | `/v3/#/recap` | [recap.md](recap.md) | 20 |
| Lessons | `/v3/#/lessons` | [lessons.md](lessons.md) | 14 |
| Issues | `/v3/#/issues` | [issues.md](issues.md) | 17 |
| Auto Mode | `/v3/#/auto` | [auto.md](auto.md) | 16 |
| Task detail | `/v3/#/task/<id>` | [task-detail.md](task-detail.md) | 18 |
| Lesson detail | `/v3/#/lesson/<id>` | [lesson-detail.md](lesson-detail.md) | 14 |
| Issue detail | `/v3/#/issue/<id>` | [issue-detail.md](issue-detail.md) | 15 |
| **Total** | | | **169** |

> **Process note:** First parallel-agent pass crashed the server (Python stdlib `HTTPServer` is single-threaded). The 6 affected screens (kanban, table, recap, task-detail, lesson-detail, issue-detail) were re-audited sequentially on a fresh server. All findings above are now runtime-verified.

## Synthesis

See **[synthesis.md](synthesis.md)** for cross-cutting themes, recommended new polish tasks, and re-prioritization of the existing 12.
