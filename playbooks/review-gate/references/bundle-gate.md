# Review Gate — Bundle Mode Detail

Full walkthrough for per-member verdict and descope path when `_get_session_bundle()` is set.

## Per-Member Verdict Flow

### 1. Detect bundle

Call `_get_session_bundle()` at the start of the gate. If it returns a bundle with a non-empty `members` list, use bundle mode. Otherwise fall through to the standard single-task gate — no changes to that flow.

```
bundle = _get_session_bundle()
if bundle and bundle.get("members"):
    # bundle mode
else:
    # standard single-task flow (unchanged)
```

### 2. Shared context

The bundle carries a shared branch, worktree, and (optionally) a shared spec doc path. Compute the diff once from the shared branch — all members share the same changeset in the same worktree.

### 3. Per-member acceptance check

For each `member` in `bundle["members"]`:
- Load the member's task via `backlog_get_task(member)` to obtain its acceptance criteria and spec path.
- Evaluate the shared diff against that member's criteria.
- Assign a verdict: `pass` / `fail`.

Emit a combined report with one row per member:
```
Member   | Verdict | Notes
---------|---------|------
T-001    | PASS    | All 3 ACs met
T-002    | FAIL    | AC-2 missing: error handling not implemented
T-003    | PASS    | —
```

### 4. Record per-member gates

Loop over all members and record each verdict individually. All share the same `spec_path` (the shared spec doc if present, else the individual task's spec path):

```python
for member in bundle["members"]:
    backlog_record_gate(
        member,
        "review-gate",
        verdict="pass" if member_passed else "fail",
        spec_path=shared_spec_path,   # same for all members
        commit_sha=current_sha,
    )
```

### 5. All pass

If all members pass:
- Transition all members to `in-review` (call `backlog_update_task(member, "status", "in-review")` for each).
- Close the gate — report "All N bundle members passed review-gate."

### 6. Any fail — options

For each failing member, present two options:

**Option A — Fix-up:** Address the failing acceptance criteria in the same worktree, then re-run the gate for that member only.

**Option B — Descope:** Remove the member from the bundle and return it to `todo`. See Descope path below.

---

## Descope Path

Descope is always explicit — every step is visible and acknowledged. Never silently remove a member.

### Step 1: Clear the bundle membership

```python
backlog_update_task(member, "bundle", "")
```

`_valid_bundle_slug("")` returns `True`, so an empty string is accepted and means "not in any bundle."

### Step 2: Return member to todo

```python
backlog_update_task(member, "status", "todo")
```

The member re-enters the backlog as a standalone unstarted task.

### Step 3: Clear branch and worktree fields

```python
backlog_update_task(member, "branch", "")
backlog_update_task(member, "worktree", "")
```

These fields belonged to the shared bundle; the descoped task has no branch until it is picked again independently.

### Step 4: Edit the shared spec doc

There is no document-mutation MCP tool by design — edit the markdown file directly:

1. Locate the shared spec doc path (from `bundle["spec_path"]` or the task's `spec` field).
2. Open the file with the `Edit` tool.
3. Find and remove the section that belongs to the descoped member (typically a `## T-XXX` or `### member-id` heading and its content).
4. Save. The spec now documents only the remaining bundle members.

### Step 5: Atomic merge without the descoped member

The branch now represents only the remaining members' work. Merge (or PR) proceeds normally. The descoped member's task is visible in the backlog as `todo` with no branch — ready to be picked independently in a future session.

---

## Edge Cases

**All members fail:** Offer fix-up passes for all, or descope all. If all are descoped, the branch has no remaining members — close the branch (archive or delete) and note it in each member's task body.

**Single-member bundle:** A bundle with one member degenerates to the standard single-task flow. Detect this and skip the per-member report format; run the normal gate steps.

**Partial fix-up then re-run:** After fixing a failing member, call the gate again with just that member's ID as `task_id` to re-evaluate. When it passes, record its gate and transition it to `in-review`. Do not re-evaluate already-passed members.

**Spec doc absent:** If the bundle has no shared spec doc, use each member's individual `spec` path as that member's `spec_path` in `backlog_record_gate`. The descope spec-edit step is skipped if there is no shared doc — note the skip in the descope report.
