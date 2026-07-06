---
name: review-gate
description: "Run quality checks on a task's implementation before marking it ready for user testing. Invoke when the user says 'is this ready?', 'run the review gate', 'check my work', or 'I think this is done'. Reviews code for defects and spec adherence, runs tests and build, transitions task to in-review. For pre-implementation design review, use taskmaster:spec-review instead."
---

# Review Gate

Follow the playbook at `../../playbooks/review-gate/playbook.md` (relative to
this skill's base directory). Read it in full and execute it exactly.
`references/` paths inside the playbook resolve relative to the playbook's
own directory.
