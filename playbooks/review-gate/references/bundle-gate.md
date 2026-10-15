# Review Gate: Bundle Mode

When a task belongs to a bundle, its members share one branch, one worktree and usually one spec. The gate reviews the shared diff once and judges each member on its own acceptance criteria.

## Detect the bundle

The task's `bundle` field (shown by `backlog_get_task`) or the pick response line `Picking bundle <slug>: ids` marks bundle mode. For the member list, call `backlog_claim(action="status", task_id=<id>)` and read `members`. No bundle, or a bundle of one member: run the standard single-task gate.

## Per-member verdict

1. Compute the diff once from the shared branch.
2. For each member, `backlog_get_task(member)` for its acceptance criteria and spec path, then judge the shared diff against them: `pass` or `fail`.
3. Report one row per member:

```
Member   | Verdict | Notes
---------|---------|------
T-001    | PASS    | All 3 ACs met
T-002    | FAIL    | AC-2 missing: error handling not implemented
T-003    | PASS    | -
```

4. Record each member's gate with the shared spec path (or the member's own when there is no shared spec):
   `backlog_record_gate(member, "review-gate", verdict="pass"|"fail", spec_path=<spec>, commit_sha=<HEAD>)`.

Members stay `in-progress`; end-session closes them to `done`.

## A member fails

Two options, the user's call:

- Fix-up: address the failing criteria in the same worktree, then re-run the gate with just that member's id. Passed members aren't re-evaluated.
- Descope: remove the member from the bundle and return it to `todo`. Descope is always explicit, since a silent removal would leave a task looking done with its work missing.

## Descope procedure

Four separate calls, because each field is its own update:

```
backlog_update_task(member, "bundle", "")
backlog_update_task(member, "status", "todo")
backlog_update_task(member, "branch", "")
backlog_update_task(member, "worktree", "")
```

An empty `bundle` means "not in any bundle". The branch and worktree belonged to the bundle; the descoped task gets its own when it is picked again.

If there is a shared spec doc, edit the file directly to remove the descoped member's section (no tool mutates spec documents). Without a shared doc, skip this and say so in the descope report. The branch then carries only the remaining members' work and merges as usual.

## Edge cases

- All members fail: fix-up passes for all, or descope all. If all are descoped, the branch has no members left; note that in each member's task and let the user decide what happens to the branch.
- Partial fix-up: re-gate only the member that was fixed.
