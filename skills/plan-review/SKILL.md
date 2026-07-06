---
name: plan-review
description: "Adversarial review of a task's implementation plan before writing tests. This skill should be used when the user says 'review this plan', 'plan review', 'challenge my plan', or 'is this plan solid'. The full-lane gate between PLAN and WRITE_TESTS. Does not review code — use taskmaster:review-gate for post-implementation review."
---

# Plan Review

Follow the playbook at `../../playbooks/plan-review/playbook.md` (relative to
this skill's base directory). Read it in full and execute it exactly.
`references/` paths inside the playbook resolve relative to the playbook's
own directory.
