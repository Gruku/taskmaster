---
name: migrate-v3
description: "Adopt a pre-6.0 backlog (.claude/ or project-root layout) into the taskmaster store, with a confirm before anything moves. Triggers: 'adopt legacy backlog', 'upgrade to v3', 'migrate to v3'. For a project with no backlog yet, use init-taskmaster."
---

# Adopt a legacy backlog

Follow the playbook at `../../playbooks/migrate-v3/playbook.md` (relative to
this skill's base directory); it holds the tool calls, store rules and
user checkpoints for this workflow. `references/` and `templates/` paths
inside it resolve relative to the playbook's own directory.
