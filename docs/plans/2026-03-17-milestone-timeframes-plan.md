# Implementation Plan: Milestone Timeframes and Past Milestone Archive

## Overview

Adds two features to Taskmaster:
1. Optional `target_date` and `start_date` fields on milestones, with time-remaining/overdue display
2. Past-milestone archive experience: viewing completed milestones with retrospective stats, duration, and on-time analysis

Target files: `backlog_server.py` (server) and `backlog-viewer.html` (UI).

---

## Phase 1: Schema Changes (backlog_server.py)

### Step 1.1 — Expand `ALLOWED_MILESTONE_FIELDS`

**File:** `backlog_server.py`, line ~1672

```python
# Before
ALLOWED_MILESTONE_FIELDS = {"name", "status", "description", "order"}
# After
ALLOWED_MILESTONE_FIELDS = {"name", "status", "description", "order", "target_date", "start_date"}
```

### Step 1.2 — Add `target_date` and `start_date` to `backlog_add_milestone`

**File:** `backlog_server.py`, lines ~1676-1723

- Add optional `target_date: str = ""` and `start_date: str = ""` params
- Validate with `_validate_date()` before creating the milestone dict
- Auto-set `start_date` to today if status is "active" and omitted
- Conditionally include in the milestone dict (only if non-empty)

### Step 1.3 — Handle date fields in `backlog_update_milestone`

**File:** `backlog_server.py`, lines ~1727-1763

- Add `elif field in ("target_date", "start_date"):` block
- Allow clearing dates with empty string (`ms.pop(field, None)`)
- Validate non-empty values with `_validate_date()`
- Auto-set `start_date` when status transitions to "active" if not already present

### Step 1.4 — Add `_validate_date` helper

**File:** `backlog_server.py`, near line ~70 (next to `_today()` and `_now()`)

```python
def _validate_date(s: str) -> date | None:
    """Parse YYYY-MM-DD string, return date or None if invalid."""
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None
```

### Step 1.5 — Add `_time_remaining` helper

**File:** `backlog_server.py`, near `_validate_date`

```python
def _time_remaining(target_date_str: str | None) -> str | None:
    """Return human-readable time remaining/overdue, or None if no target."""
    if not target_date_str:
        return None
    try:
        target = datetime.strptime(str(target_date_str), "%Y-%m-%d").date()
        delta = (target - date.today()).days
        if delta > 0:
            return f"{delta}d remaining"
        elif delta == 0:
            return "due today"
        else:
            return f"{abs(delta)}d overdue"
    except ValueError:
        return None
```

### Step 1.6 — Auto-set `start_date` in `backlog_advance_milestone`

**File:** `backlog_server.py`, lines ~1871-1872

When activating the next milestone, set `start_date` if not present.

---

## Phase 2: Server Output Changes (backlog_server.py)

### Step 2.1 — Enhance `backlog_milestone_status` for active milestones

**File:** `backlog_server.py`, lines ~1784-1826

After the status/order line, add date info: start date, target date, and time remaining/overdue.

### Step 2.2 — Enhance `backlog_milestone_status` for done milestones (retrospective)

When `ms["status"] == "done"`, show:
- **Duration:** days from start_date → completed
- **On-time analysis:** completed vs target_date
- **Tasks completed & archived:** count of archived tasks in this milestone

Key insight: for done milestones, tasks have been archived by `backlog_advance_milestone`, so count `archived` tasks as "completed".

### Step 2.3 — Update `backlog_status` to show dates

**File:** `backlog_server.py`, lines ~463-482

- Active milestone line: append time remaining
- Milestone list: show target_date if present

### Step 2.4 — Update `regenerate_progress_dashboard` to include dates

**File:** `backlog_server.py`, lines ~301-320

Show target date and time remaining in PROGRESS.md.

### Step 2.5 — Update `regenerate_context` to include dates

**File:** `backlog_server.py`, lines ~251-261

Add `target_date` and `start_date` to the `active_milestone` context dict.

### Step 2.6 — Update `backlog_advance_milestone` result

**File:** `backlog_server.py`, line ~1876

After completion message, add duration and on-time info.

---

## Phase 3: Viewer Changes (backlog-viewer.html)

### Step 3.1 — CSS for date display and history elements

**File:** `backlog-viewer.html`, after line ~713

New styles for:
- `.ms-date-info` (base), `.ms-overdue` (red), `.ms-due-soon` (amber)
- `.ms-step.ms-done` (greyed, clickable, hover/selected states)
- `.ms-history-panel`, `.ms-history-header`, `.ms-history-stat`
- `.ms-ontime` (green), `.ms-late` (red)

### Step 3.2 — Update milestone banner to show dates

**File:** `backlog-viewer.html`, `renderMilestones()` ~lines 1891-1898

For active/planned milestones: show target date with time remaining/overdue.
For done milestones: show completed date vs target (on-time/late).

### Step 3.3 — Retrospective view for done milestones in the banner

When a done milestone is selected, render a different banner:
- "Completed Milestone" label
- Total tasks completed (archived + done count)
- Duration (start → completed)
- On-time status
- Full progress bar in done color
- Description if present

### Step 3.4 — Show archived tasks when viewing done milestones

**File:** `backlog-viewer.html`, `renderEpics()` ~line 1968 and `render()` ~line 2067

When selected milestone is done, include archived tasks and temporarily treat them as "done" for display (shallow copy, don't mutate originals).

### Step 3.5 — Milestone history section

Add a "History" toggle below the steps indicator that expands a timeline of completed milestones with:
- Name, task count, duration, completion date
- Clickable to select that milestone and view its retrospective

Only visible when there are completed milestones.

---

## Phase 4: Backwards Compatibility

### No migration needed

Both `target_date` and `start_date` are optional. All code uses `.get()` with graceful fallbacks:

- `backlog_add_milestone` defaults both to `""` (omitted from YAML)
- `backlog_update_milestone` handles them only when explicitly set
- `backlog_milestone_status` conditionally shows date info only when present
- Viewer checks field existence before rendering
- `_time_remaining()` returns `None` when no target date

### Done milestones without `start_date`

Pre-existing done milestones won't have `start_date`. Retrospective omits duration in that case, showing only the `completed` timestamp.

### Archived tasks in retrospective

Done milestone tasks were archived by `backlog_advance_milestone`. The retrospective treats `archived` count as "completed" for display purposes.

---

## Phase 5: Testing Checklist

1. Create milestone with `target_date` and `start_date` — verify in YAML
2. Update milestone dates via `backlog_update_milestone` — verify clear with `""` works
3. `backlog_milestone_status` with dates — check time remaining display
4. `backlog_milestone_status` for overdue milestone — check overdue display
5. Advance milestone — verify `start_date` auto-set on next milestone
6. `backlog_milestone_status` for done milestone — check retrospective output
7. Viewer: banner shows dates for active milestone
8. Viewer: click done milestone in steps — retrospective banner appears
9. Viewer: archived tasks display in kanban when done milestone selected
10. Legacy backlog.yaml (no date fields) — verify no errors

---

## Summary of Changes by File

### `backlog_server.py` — 9 locations

| Location | Lines | Change |
|----------|-------|--------|
| Helpers | ~70 | Add `_validate_date()` and `_time_remaining()` |
| `regenerate_context` | ~251-261 | Include dates in active_milestone context |
| `regenerate_progress_dashboard` | ~301-320 | Show target date in PROGRESS.md |
| `backlog_status` | ~463-482 | Show dates in milestone listing |
| `ALLOWED_MILESTONE_FIELDS` | ~1672 | Add `target_date`, `start_date` |
| `backlog_add_milestone` | ~1676-1723 | Accept and store date params |
| `backlog_update_milestone` | ~1727-1763 | Handle date field updates with validation |
| `backlog_milestone_status` | ~1767-1826 | Date display + retrospective for done |
| `backlog_advance_milestone` | ~1830-1885 | Auto-set start_date, retrospective in result |

### `backlog-viewer.html` — 5 locations

| Location | Lines | Change |
|----------|-------|--------|
| CSS | ~713 | Date display, history panel, done milestone styles |
| HTML | ~1603 | Milestone history container |
| `renderMilestones()` | ~1862-1932 | Date in banner, retrospective, history section |
| `renderEpics()` | ~1968 | Show archived tasks for done milestones |
| `render()` | ~2067 | Same archived task filter for done milestones |
