# Bug: entry-point flows

## log-bug

Trigger: the user asks to log a defect ("log a bug", "I found a bug", "track this defect", "this is a bug"), or says "issue" when no recurring or systemic evidence is present (see `bug-vs-issue.md`).

The user asked for the Bug, so write it first and let them adjust afterwards.

1. Draft the fields from the conversation:
   - `title`: the first sentence describing the defect, up to 80 characters
   - `found_in`: the in-progress task id if there is one, else empty
   - `discovered_by`: `user` when the user reported it; `claude` when you noticed it yourself and they agreed to log it (the offer below)
   - `components`: from the epic or folder, or from file paths if cited
   - `location`: `file:line` if mentioned
   - `severity`: leave empty unless the user volunteers one
   - `body`: a `## Repro` and `## Expected` skeleton from the conversation (`../templates/bug-body.md`)
2. Call `backlog_bug_create` with those fields.
3. Reply in one line: `Logged B-NNN — <title>. Leave it open, fix it now, spawn a task, or shelve it?` If the user already said what they want ("shelve this for later"), apply that disposition straight away and skip the question.

## offer-on-explicit-finding

Offer when the defect is outside this task's scope and the user would plausibly want it kept: a test failure in unrelated code, a second defect found while root-causing, or the user remarking that something is wrong without asking you to fix it. In-scope defects get fixed as part of the work. Unrelated ones noticed while reading get one mention and no offer. Offer once per finding, because repeat offers interrupt the user.

Offer in one inline line and wait for the answer, since the user owns what goes into their backlog:

> I noticed <one-line symptom>. Want me to log it as a Bug (found_in: T-XXX)?

On yes, run `log-bug` with `discovered_by="claude"`. On no, drop it for the rest of the session.

## disposition

Run at Bug creation and when a task with open Bugs is closing.

| Choice | Action | Bug state |
|---|---|---|
| fix-now | Fix it as part of the current task. After the fix commit lands, set `fix_commit=<sha>`, then `status=fixed`. | `open` until the commit exists |
| spawn-task | `backlog_add_task` returns `T-XXX`. Set `adopted_into=T-XXX`, then `status=adopted`. | `adopted` |
| shelve | `status=shelved`. | `shelved` |
| promote | The Bug clears the Issue bar. Collect the evidence citation, then call `backlog_bug_promote(bug_ids=["B-XXX"], title=..., severity=..., evidence_text=...)`. | `promoted` |

## update-status

Trigger: "mark B-XXX fixed", "shelve B-XXX", "promote B-XXX", "close B-XXX", "fixed B-XXX in commit".

1. Take the Bug id from the user's message, or ask "Which bug? (B-NNN)".
2. Check the target status's required field against the state machine in `../playbook.md`.
3. `backlog_bug_update` takes one `field`/`value` per call. Set the companion field first, then the status, for example `backlog_bug_update(bug_id, "fix_commit", "<sha>")` then `backlog_bug_update(bug_id, "status", "fixed")`. The same pattern applies to `adopted_into` → `adopted` and `promoted_to` → `promoted`.

## triage-review

Trigger: "list open bugs", "triage bugs", "show me shelved bugs".

1. `backlog_bug_list(status="open")` and `backlog_bug_list(status="shelved")`; show two grouped sections.
2. For each open Bug attached to a task that isn't done yet, mark it as gating that task.
3. Offer to adopt or promote any of them, and apply the disposition the user picks.
