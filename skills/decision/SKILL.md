---
name: decision
description: "Write/resolve/drop project decisions in .taskmaster/decisions/. Invoke when Claude is about to write an inline option menu (≥2 mutually exclusive paths) — route through this skill instead of writing Options: in chat. Also invoke for 'choose between', 'pick an option', 'decide on', 'open question', 'branching path', 'resolve DEC-X', 'drop DEC-X', 'list open decisions'. Only correct way — do not call backlog_decision_create directly."
---

# Decision

Follow the playbook at `../../playbooks/decision/playbook.md` (relative to
this skill's base directory). Read it in full and execute it exactly.
`references/` and `templates/` paths inside the playbook resolve relative
to the playbook's own directory.
