---
name: handover
description: "Write a session handover into .taskmaster/handovers/. Invoke when the user says 'write a handover', 'wrap up', 'for tomorrow', 'before compaction', 'context handoff', or 'continue where we left off'. This is the only correct way to write a handover — do not call backlog_handover_create directly."
---

# Handover

Follow the playbook at `../../playbooks/handover/playbook.md` (relative to
this skill's base directory). Read it in full and execute it exactly.
`references/` paths inside the playbook resolve relative to the playbook's
own directory.
