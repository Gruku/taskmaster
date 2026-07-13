# Handover Threads — Design

**Date:** 2026-07-13
**Status:** Approved (brainstorm dialogue, this session)
**Scope:** taskmaster (source of truth: `C:\Users\gruku\Files\Claude\taskmaster`)

## Problem

1. **Resume is token-courier work.** The durable resume token today is the per-handover dated slug (`2026-07-11-team-relayout-...`). Every new handover mints a new token; the user copies it out of the session and stores it in Telegram because no taskmaster surface holds it reliably. The token works well for orientation — the problem is that it *changes every time*.
2. **"Latest" assumes one linear thread.** `start-session` surfaces the latest open handover (`backlog_handover_list(status="open", limit=1)`-style). With ~5 parallel Claude sessions writing handovers concurrently, "latest" never identifies the thread the user means, so the flow is always bypassed.
3. **Sessions view models time, not structure.** `list_sessions()` synthesizes sessions by greedy 30-minute-gap clustering with throwaway `SES-NNNN` ids; `parallel_with` is pure timestamp overlap. Handovers can carry `branch`/`tip_commit` but nothing reads them. Parallel work is the norm and the view can't show it as such.
4. **Known rot on the same surfaces** (found during exploration, fix in-scope):
   - Viewer resume-prompt copy button is rendered but never wired (`viewer/js/screens/sessions.js:356-360`; `lib/copy.js` unused).
   - Viewer status menu offers `todo/in-progress/done` but the server validator (`update_handover_status`, `taskmaster_v3.py:1557-1584`) only accepts `open/closed/superseded` → the POST 400s. Dead legacy-enum wiring.

## Decisions (from dialogue)

- Resume happens **from anywhere** (monorepo); location does not identify the thread.
- **Both resume paths**: paste a name, or pick from a list — **on-demand only, never injected by the SessionStart hook**.
- Typical working set: **~5 parallel threads**.
- Viewer Sessions screen job: **board + diary** (live open threads on top, timeline below).
- **Thread is a first-class entity** — lightweight registry entry, not a new file type (approach 1; thread-as-file rejected as a competing narrative surface, thread-as-epic rejected as lossy).

## Design

### Thread entity

A thread is a named, durable chain of handovers representing one line of work.

- **Storage:** thread registry inside the handover index in `backlog.yaml`. No per-thread file.
- **Source of truth:** the `thread:` frontmatter field on handover files. The registry is a rebuildable index — `backlog_handover_resync` reconstructs threads from frontmatter (keeps team-collab state-branch merges safe).
- **Registry entry shape:**
  ```yaml
  threads:
    team-relayout:
      status: open          # open | parked | closed
      handover_ids: [...]   # chronological
      task_ids: [...]       # union of member handovers'
      created: ...
      last_touched: ...
  ```
- **Naming:** short, stable, human-memorable slug. Auto-derived at handover-create time from (in order) explicit `thread` arg → active bundle slug → epic of the linked tasks → primary task id. Overridable. Same name resumes forever.
- **Lifecycle:** `open` (on board) → `parked` (deliberately shelved, off board but listed under a fold) → `closed` (thread finished; set when its handovers close via smart-auto-close, or explicitly). Reopening = writing a new handover into the thread (auto-reopens).

### Handover changes

- New frontmatter field `thread: <slug>` (required going forward; absent on legacy files).
- `backlog_handover_create` gains `thread` param; appends to the registry, auto-creating the thread. Supersession stays as-is but now chains *within* a thread by default (the chained-supersession task-overlap heuristic becomes "same thread").
- **Migration:** one-time backfill — group existing handovers into threads by supersession chains + shared task_ids/epic; unresolvable singles get `thread: <primary-task-or-date-slug>`. Runs inside `backlog_handover_resync`.

### Resume flow

- **New MCP tool `backlog_thread_resume(name)`** → resolves thread → newest handover → returns full body (verbose) in one call. Accepts a thread name **or** a legacy/dated handover slug (resolves via that handover's `thread` field, then returns the thread's newest — so stale Telegram tokens still land correctly).
- **New MCP tool `backlog_thread_list()`** → the board as data: per open thread `{name, status, tldr, next_action, task_ids, branch, last_touched, staleness}`. Slim. (`parked` behind a flag.)
- **Playbooks:**
  - `start-session`: "Where you left off" = the open-threads board (one line per thread), replacing latest-open-handover. The `handover_list(status="open", limit=1)` suggestion and `backlog_handover_latest` alias are **deleted**.
  - Paste path: any prompt containing a thread name or handover slug → skills call `backlog_thread_resume` directly.
  - `end-session` / `handover`: final output always ends with a copy-ready line: `Resume: <thread-name> — <next_action>`. That line is the Telegram artifact; it never goes stale.
- **No hook injection.** The SessionStart hook does not mention threads or the board. Board appears only via explicit start-session / "what's open" / a pasted name.

### Viewer — Sessions screen (board + diary)

- **Top: thread board.** One card per open thread: name, tldr, next action, task ids, branch, staleness. A **copy-resume button** (wired via existing `lib/copy.js`) copying `Resume: <thread-name> — <next_action>`. Parked threads under a fold. Card styling per design rules: no colored left rails, no hover motion, no box-shadows.
- **Below: diary timeline, lanes = threads.** `list_sessions()`'s 30-min-gap clustering and `parallel_with` time-heuristic are **removed**; the timeline groups by thread lane, parallelism reads structurally from overlapping thread activity. Throwaway `SES-NNNN` ids die; lanes key on thread names.
- **Rot fixes:** wire the copy button; replace the `todo/in-progress/done` status menu with `open/closed/superseded` (handover) and `open/parked/closed` (thread) so the POSTs stop 400ing.
- API: `GET /api/threads` (board), `/api/sessions` reshaped to thread lanes.

### Team-collab compatibility

Registry rebuilds from handover frontmatter, so state-branch merges never conflict on the index. Thread-name collisions across teammates: last-touched wins on the board; no namespacing (YAGNI).

### Out of scope

- Thread-level notes/narrative (rejected — competing surface).
- Hook-driven resume prompts.
- Cross-project threads.

## Testing

- Unit: thread registry build/rebuild from frontmatter (incl. legacy files without `thread`), auto-naming precedence, lifecycle transitions, resume resolution (thread name, dated slug, unknown name), migration grouping.
- Existing `test_handover_parallel_smoke.py` extended: two threads, board shows both, resume picks per-thread newest.
- Viewer: route-mocked specs for board render, copy button, status menus (per ISS-025, only route-mocked e2e is a trustworthy gate).

## Key code anchors

| Concern | Where |
|---|---|
| Handover id/write | `taskmaster/taskmaster_v3.py:1328-1427` |
| Status enums + validator | `taskmaster_v3.py:1282-1325, 1557-1584` |
| Session synthesis (to be replaced) | `taskmaster_v3.py:3663-3801` |
| MCP handover tools | `backlog_server.py:2013, 2111, 2198, 2308, 2666, 2689` |
| Sessions screen / timeline | `viewer/js/screens/sessions.js`, `viewer/js/components/timeline.js` |
| Playbooks | `playbooks/{handover,start-session,end-session}/playbook.md` |
