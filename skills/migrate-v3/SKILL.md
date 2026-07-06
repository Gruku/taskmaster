---
name: migrate-v3
description: "Guided v2 to v3 backlog migration. Invoke when the user says 'upgrade to v3', 'migrate to v3', 'switch to v3', 'enable handovers and lessons', 'enable narrative continuity', or 'I want recap'. Shows pre-flight summary, confirms opt-in, runs migration. Only correct way to migrate — do not call backlog_migrate_v3 directly without the pre-flight gate."
---

# Migrate to v3

Follow the playbook at `../../playbooks/migrate-v3/playbook.md` (relative to
this skill's base directory). Read it in full and execute it exactly.
`references/` paths inside the playbook resolve relative to the playbook's
own directory.
