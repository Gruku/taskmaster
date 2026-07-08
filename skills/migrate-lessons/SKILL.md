---
name: migrate-lessons
description: "One-time migration of legacy .taskmaster/lessons/ files into assistant memory and repo instruction files after upgrading to taskmaster 4.x. Invoke when the user says 'migrate lessons', 'convert lessons to memory', 'what happened to lessons', or when start-session detects L-*.md files under .taskmaster/lessons/ in a 4.x project."
---

# Migrate Lessons

Follow the playbook at `../../playbooks/migrate-lessons/playbook.md` (relative to
this skill's base directory). Read it in full and execute it exactly.
`references/` paths inside the playbook resolve relative to the playbook's
own directory.
