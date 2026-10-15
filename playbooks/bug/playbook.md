# Bug

Bugs are the light defect tier: anything the user wants tracked that doesn't meet the Issue bar (recurring, systemic, or outstanding P0/P1; the bar lives in `../issue/references/issue-bar.md`). They have no aging window or fix-by date. When unsure, file a Bug: promoting to an Issue later is one `backlog_bug_promote` call, and demoting has no tool.

Write Bugs through this playbook rather than calling `backlog_bug_create` / `backlog_bug_update` cold. The playbook adds the disposition choice and keeps defects you noticed yourself from being logged without the user's say-so.

A defect you can fix in a moment inside the current task needs no Bug: fix it and mention it in the commit.

## What to do

Per-entry-point steps are in `references/entry-point-flows.md`; routing between Bug and Issue is in `references/bug-vs-issue.md`.

- **`log-bug`**: the user asks to log a defect (or says "issue" with no recurring or systemic evidence). Draft the fields from the conversation, create the Bug, and reply with the id plus a one-line disposition offer.
- **`offer-on-explicit-finding`**: you hit a real defect outside the current task's scope. Offer once, in one line, to log it; log it only if the user agrees.
- **`disposition`**: fix-now, spawn-task, shelve, or promote. Offered at creation and when a task with open Bugs is closing.
- **`update-status`**: shelve, reopen, adopt, promote, or mark fixed.
- **`triage-review`**: list open and shelved Bugs, and say which open ones are blocking a task from closing.

## Store rules the tools enforce

```
open ─► fixed (needs fix_commit) | adopted (needs adopted_into: T-NNN)
     ─► promoted (needs promoted_to: ISS-NNN) | shelved
shelved ─► open | adopted | promoted
```

- `backlog_bug_update` sets one field per call. Set the companion field first, then `status`, because validation reads the stored row.
- A task can't move to `done` while a Bug found in it is still `open`.
- `backlog_bug_promote` needs `bug_ids`, `title`, `severity` and `evidence_text`.

Writes: a result ending in `[seq N]` is committed; `(export pending: …)` means committed with the file export still queued; `No change … already …` without `[seq]` means the value was already stored. `backlog_store_status` shows dirty or quarantined files.
