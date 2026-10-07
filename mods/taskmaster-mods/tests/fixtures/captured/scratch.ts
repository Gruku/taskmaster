// User intent: real Taskmaster MCP replies, captured by scripts/capture_fixtures.py, so the parsers are tested
// against what the server actually says (scratch store). GENERATED: rerun the script to refresh.

export const SCRATCH = {
  "meta": {
    "store": "scratch",
    "captured": "2026-10-06",
    "redacted": false,
    "taskId": "fx-001"
  },
  "replies": {
    "init": {
      "tool": "backlog_init",
      "args": {
        "project_name": "tm-tui-fixtures"
      },
      "isError": false,
      "text": "Initialized taskmaster for **tm-tui-fixtures** in `.taskmaster/` (trackable in git) on schema v4 (sharded per-task storage).\nCreated: .taskmaster/taskmaster.json, .taskmaster/backlog.yaml, .taskmaster/local/PROGRESS.md"
    },
    "add_phase": {
      "tool": "backlog_add_phase",
      "args": {
        "phase_id": "fx-phase",
        "name": "Fixtures"
      },
      "isError": false,
      "text": "Created phase `fx-phase` — Fixtures (order: 1) (auto-activated — first phase) [seq 3]"
    },
    "add_epic": {
      "tool": "backlog_add_epic",
      "args": {
        "epic_id": "fx",
        "name": "Fixtures",
        "done_when": "never"
      },
      "isError": false,
      "text": "Created epic `fx` — Fixtures (planned) [seq 4]"
    },
    "add_task": {
      "tool": "backlog_add_task",
      "args": {
        "title": "Scratch review task (with parentheses) — and a dash",
        "epic": "fx",
        "phase": "fx-phase",
        "priority": "high"
      },
      "isError": false,
      "text": "Added `fx-001` — Scratch review task (with parentheses) — and a dash (high) under Fixtures [seq 5]"
    },
    "pick": {
      "tool": "backlog_pick_task",
      "args": {
        "task_id": "fx-001"
      },
      "isError": false,
      "text": "Picked `fx-001` — Scratch review task (with parentheses) — and a dash (locked to this session)\n\n**Epic:** Fixtures — \n**Priority:** high\n\n**REQUIRED — Create a worktree before writing any code:**\n```\ngit worktree add .worktrees/fx-001 -b feature/fx-001\n```\nThen record it:\n```\nbacklog_update_task(fx-001, branch, feature/fx-001)\nbacklog_update_task(fx-001, worktree, .worktrees/fx-001)\n```\nAll work for this task MUST happen in the worktree, not on the main branch. [seq 6]"
    },
    "set_human_action": {
      "tool": "backlog_update_task",
      "args": {
        "task_id": "fx-001",
        "field": "human_action",
        "value": "Check the scratch thing\nsecond line of the check"
      },
      "isError": false,
      "text": "Updated `fx-001` field `human_action` → Check the scratch thing\nsecond line of the check [seq 7]"
    },
    "to_in_review": {
      "tool": "backlog_update_task",
      "args": {
        "task_id": "fx-001",
        "field": "status",
        "value": "in-review"
      },
      "isError": false,
      "text": "Updated `fx-001` field `status` → in-review [seq 8]"
    },
    "list_in_review": {
      "tool": "backlog_list_tasks",
      "args": {
        "status": "in-review",
        "limit": 0
      },
      "isError": false,
      "text": "**1 tasks:**\n- `fx-001` — Scratch review task (with parentheses) — and a dash (high, fx, in-review) — Scratch review task (with parentheses) — and a dash\n    waiting-on-human: Check the scratch thing\nsecond line of the check"
    },
    "get_in_review": {
      "tool": "backlog_get_task",
      "args": {
        "task_id": "fx-001"
      },
      "isError": false,
      "text": "## `fx-001` — Scratch review task (with parentheses) — and a dash\n\n**tldr:** Scratch review task (with parentheses) — and a dash\n**status:** in-review\n**priority:** high\n**phase:** fx-phase\n**epic:** fx\n**lane:** full\n**gate_state:** spec-review:pending\n**started:** 2026-10-06T19:24\n**human_action:** Check the scratch thing\nsecond line of the check\n**tldr_autogen:** True"
    },
    "pipeline": {
      "tool": "backlog_task_pipeline",
      "args": {
        "task_id": "fx-001"
      },
      "isError": false,
      "text": "## Pipeline `fx-001` — lane: **full**\ngate_state: `spec-review:pending`\n\n- `spec`: ○ pending\n- `spec-review`: ○ pending\n- `plan`: ○ pending\n- `plan-review`: ○ pending\n- `tests`: ○ pending\n- `impl`: ○ pending\n- `review-gate`: ○ pending\n\n**Outstanding:** spec-review, plan-review, review-gate"
    },
    "continuity_review": {
      "tool": "backlog_continuity_items",
      "args": {
        "action_class": "review",
        "limit": 0
      },
      "isError": false,
      "text": "{\"view\": \"action\", \"total\": 1, \"items\": [{\"id\": \"fx-001\", \"type\": \"task\", \"title\": \"Scratch review task (with parentheses) \\u2014 and a dash\", \"where\": \"fx\", \"next\": \"in-review\", \"action_class\": \"review\", \"timestamp\": \"2026-10-06T19:24\", \"age_days\": -0.08282776928240741, \"task_id\": \"fx-001\", \"branch\": \"\"}]}"
    },
    "complete_missing": {
      "tool": "backlog_complete_task",
      "args": {
        "task_id": "zz-missing-999",
        "done": "Signed off in review queue"
      },
      "isError": false,
      "text": "Error: task `zz-missing-999` not found"
    },
    "back_status": {
      "tool": "backlog_update_task",
      "args": {
        "task_id": "fx-001",
        "field": "status",
        "value": "in-progress"
      },
      "isError": false,
      "text": "Updated `fx-001` field `status` → in-progress [seq 9]"
    },
    "back_clear_human_action": {
      "tool": "backlog_update_task",
      "args": {
        "task_id": "fx-001",
        "field": "human_action",
        "value": ""
      },
      "isError": false,
      "text": "Updated `fx-001` field `human_action` →  [seq 10]"
    },
    "back_clear_again": {
      "tool": "backlog_update_task",
      "args": {
        "task_id": "fx-001",
        "field": "human_action",
        "value": ""
      },
      "isError": false,
      "text": "Updated `fx-001` field `human_action` → (not persisted)"
    },
    "back_next_step": {
      "tool": "backlog_update_task",
      "args": {
        "task_id": "fx-001",
        "next_step": "Back from review: tighten the copy"
      },
      "isError": false,
      "text": "Updated `fx-001`: next_step → Back from review: tighten the copy [seq 11]"
    },
    "in_review_without_human_action": {
      "tool": "backlog_update_task",
      "args": {
        "task_id": "fx-001",
        "field": "status",
        "value": "in-review"
      },
      "isError": false,
      "text": "Error: `in-review` means blocked on a human-only action; set human_action first: backlog_update_task('fx-001', 'human_action', '<what the human must do>')"
    },
    "claim_status": {
      "tool": "backlog_claim",
      "args": {
        "action": "status",
        "task_id": "fx-001"
      },
      "isError": false,
      "text": "{\"task_id\": \"fx-001\", \"holder\": \"\", \"expires_at\": \"\", \"live\": null, \"expired\": false, \"state\": \"released\", \"ok\": true}"
    },
    "complete": {
      "tool": "backlog_complete_task",
      "args": {
        "task_id": "fx-001",
        "done": "Signed off in review queue"
      },
      "isError": false,
      "text": "Cannot complete `fx-001` — outstanding gates for lane `full`: spec-review, plan-review, review-gate. Record each (backlog_record_gate) or skip it (backlog_skip_gate)."
    },
    "complete_again": {
      "tool": "backlog_complete_task",
      "args": {
        "task_id": "fx-001",
        "done": "Signed off in review queue"
      },
      "isError": false,
      "text": "Cannot complete `fx-001` — outstanding gates for lane `full`: spec-review, plan-review, review-gate. Record each (backlog_record_gate) or skip it (backlog_skip_gate)."
    }
  }
} as const
