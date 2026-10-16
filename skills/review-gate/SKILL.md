---
name: review-gate
description: "Run and record a backlog task's review gate before it closes to done: code review against the spec, tests and build. Use on a tracked task for 'run the review gate', 'is this ready?', 'check my work', 'I think this is done'. Spec or design review is taskmaster:spec-review; untracked diffs and PRs get an ordinary code review."
---

# Review Gate

Follow the playbook at `../../playbooks/review-gate/playbook.md` (relative to
this skill's base directory); it holds the tool calls, store rules and
user checkpoints for this workflow. `references/` and `templates/` paths
inside it resolve relative to the playbook's own directory.
