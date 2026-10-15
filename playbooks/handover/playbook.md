# Handover

Write a handover so the next session can resume without re-exploring: what shipped, where execution stands, what to do first, and which files matter and why. Write it through `backlog_handover_create`, which indexes it and supersedes older open handovers in the same line of work; this playbook adds the file annotations and fields that the bare tool call would miss.

Write one when the user asks, when end-session calls for it, or before a compaction the user mentions. Write it straight away, without a draft-and-approve round, unless the user asked to see a draft or you found nothing to put in it (then ask what it should cover).

## Fields

- **`session_kind`** (`references/session-kinds.md`): `continuity` by default; `milestone` when a chunk shipped or direction changed; `deep-context` for a pre-compaction capture.
- **`task_ids`:** the in-progress task for milestone and deep-context; the last task touched for continuity; `[]` for exploration. Don't invent one.
- **`thread`:** reuse the name if this session resumed from a thread; otherwise leave it empty and the server derives it. Set it only when the user names the line of work.
- **`supersedes`:** normally empty, because the server supersedes older open handovers of the same thread itself. Set it only for the cases in `references/supersession.md` (a renamed thread, or taking over work that shares no task id).
- **`tldr`** (past tense: what shipped) and **`next_action`** (imperative: what the next session does first), each at most 100 characters.
- Rarely-set fields go in `options`: `branch`, `tip_commit`, `context_size_at_write`, `review_reason`. A review flag buffered upstream: `flag_for_review=true` with `options={"review_reason": "..."}`.

## Body

Fill `templates/body.md`; drop empty sections and leave no `{placeholders}`. Decisions from end-session's sweep go in its "Open decisions" / "Resolved this session" sections as `[[DEC-NNN]]` links; the body is what carries them.

For "Files of interest", list what this session edited, what it read, and what the task's anchors and docs point to (`references/auto-extraction.md`), each with what it is and why the next session needs it; a bare path sends the next session back to re-explore. Then run source 7 in `references/auto-extraction.md`, the index reverse lookup: its open tasks are candidates for `task_ids`, and for open bugs and issues whose location you edited, ask in one line whether this session fixed them. Change a status only on the user's answer.

## Write and confirm

Call `backlog_handover_create` with `tldr`, `next_action`, `body`, `task_ids`, `session_kind`, `supersedes`, `thread`, `flag_for_review` and `options`. Show any `WARNING` line from the result above the paste block (a hand-set status that blocked auto-supersession is the usual one). Then end the reply with this paste block and nothing after it; the user copies it into a chat as the durable pointer:

    ```
    <tldr>
    <absolute path from the server's `- Path:` line>
    Resume: <thread> — <next_action>
    ```

The `Resume:` line is the server's last line, verbatim. Pasting the thread name into a later session resumes it through `backlog_thread_resume`.

## Status changes

- Close or reopen one handover: `backlog_handover_update_status(<id>, "closed" | "open", reason)`.
- Park, close or reopen a whole line of work: `backlog_thread_update(<name>, "parked" | "closed" | "open", reason)`.
- Triage: walk open handovers older than 14 days, newest first, up to 20 per run, offering close or skip (`references/triage.md`).
