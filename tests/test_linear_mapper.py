"""Tests for the TM → Linear payload mapper (linear-004).

Pure functions: no IO, no network. Translates Taskmaster entity dicts to
Linear GraphQL input payloads using the workspace config's lookup tables.
"""
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from integrations.linear.mapper import (  # noqa: E402
    compute_push_hash,
    tm_epic_to_linear_project_payload,
    tm_task_to_linear_payload,
)


# ── Fixtures ───────────────────────────────────────────────────


def _workspace() -> dict:
    return {
        "alias": "cm",
        "team_id": "team-uuid",
        "token_env": "TASKMASTER_LINEAR_TOKEN_CM",
        "status_mapping": {
            "todo": "state-todo-id",
            "in-progress": "state-progress-id",
            "in-review": "state-review-id",
            "done": "state-done-id",
            "blocked": "state-todo-id",  # collapse to todo
        },
        "priority_mapping": {
            "critical": 1,  # Urgent
            "high": 2,
            "medium": 3,
            "low": 4,
        },
        "user_mapping": {
            "Volodymyr": "user-vol-id",
        },
        "label_config": {
            "tm_managed_prefix": "tm:",
            "tag_to_label_id": {
                "backend": "label-backend-id",
                "perf": "label-perf-id",
            },
        },
    }


def _task() -> dict:
    return {
        "id": "linear-001",
        "title": "Extend Tracker entity for push-dominant sync",
        "status": "in-progress",
        "priority": "high",
        "owner": "Volodymyr",
        "tags": ["backend"],
        "tracker_id": None,
    }


# ── tm_task_to_linear_payload ──────────────────────────────────


def test_mapper_carries_title():
    payload = tm_task_to_linear_payload(_task(), _workspace())
    assert payload["title"] == "Extend Tracker entity for push-dominant sync"


def test_mapper_resolves_status_to_state_id():
    payload = tm_task_to_linear_payload(_task(), _workspace())
    assert payload["stateId"] == "state-progress-id"


def test_mapper_raises_on_unmapped_status():
    """Unmapped status indicates the workspace is mis-configured. Failing loud
    here (rather than silently pushing without stateId) makes the
    bootstrap-fill-status-mapping requirement load-bearing."""
    task = _task()
    task["status"] = "exotic"
    with pytest.raises(ValueError, match="status_mapping.*exotic"):
        tm_task_to_linear_payload(task, _workspace())


def test_mapper_resolves_priority_to_int():
    payload = tm_task_to_linear_payload(_task(), _workspace())
    assert payload["priority"] == 2  # high → 2


def test_mapper_unknown_priority_defaults_to_zero():
    """Linear treats priority 0 as None. Safer default than guessing."""
    task = _task()
    task["priority"] = "exotic"
    payload = tm_task_to_linear_payload(task, _workspace())
    assert payload["priority"] == 0


def test_mapper_no_priority_field_defaults_to_zero():
    task = _task()
    del task["priority"]
    payload = tm_task_to_linear_payload(task, _workspace())
    assert payload["priority"] == 0


def test_mapper_resolves_owner_to_assignee():
    payload = tm_task_to_linear_payload(_task(), _workspace())
    assert payload["assigneeId"] == "user-vol-id"


def test_mapper_omits_assignee_when_no_owner():
    task = _task()
    del task["owner"]
    payload = tm_task_to_linear_payload(task, _workspace())
    assert "assigneeId" not in payload


def test_mapper_omits_assignee_when_owner_unmapped():
    """Unmapped owner is non-fatal — push as unassigned rather than blocking."""
    task = _task()
    task["owner"] = "Unknown Person"
    payload = tm_task_to_linear_payload(task, _workspace())
    assert "assigneeId" not in payload


def test_mapper_maps_tags_to_label_ids():
    payload = tm_task_to_linear_payload(_task(), _workspace())
    assert payload["labelIds"] == ["label-backend-id"]


def test_mapper_skips_unknown_tags():
    """Unknown tags don't fail the push — they just don't get a label.
    Bootstrap manages the tag↔label dictionary; new tags simply mean
    'not yet labelled in Linear'."""
    task = _task()
    task["tags"] = ["backend", "novel-tag"]
    payload = tm_task_to_linear_payload(task, _workspace())
    assert payload["labelIds"] == ["label-backend-id"]


def test_mapper_includes_id_when_linear_issue_id_passed():
    """Update mode: caller passes the existing Linear issue id (read from the
    Tracker file). Client uses presence of id to choose issueUpdate vs Create."""
    payload = tm_task_to_linear_payload(
        _task(), _workspace(), linear_issue_id="existing-uuid",
    )
    assert payload["id"] == "existing-uuid"


def test_mapper_omits_id_when_not_passed():
    payload = tm_task_to_linear_payload(_task(), _workspace())
    assert "id" not in payload


# ── tm_epic_to_linear_project_payload ──────────────────────────


def test_epic_mapper_carries_name_and_description():
    epic = {"id": "linear-sync", "name": "Linear ↔ Taskmaster Sync", "description": "Mirror TM to Linear"}
    payload = tm_epic_to_linear_project_payload(epic, _workspace())
    assert payload["name"] == "Linear ↔ Taskmaster Sync"
    assert payload["description"] == "Mirror TM to Linear"


def test_epic_mapper_includes_id_when_existing():
    epic = {"id": "linear-sync", "name": "x"}
    payload = tm_epic_to_linear_project_payload(
        epic, _workspace(), linear_project_id="proj-uuid",
    )
    assert payload["id"] == "proj-uuid"


def test_epic_mapper_omits_description_when_missing():
    epic = {"id": "linear-sync", "name": "x"}
    payload = tm_epic_to_linear_project_payload(epic, _workspace())
    assert "description" not in payload


# ── compute_push_hash ──────────────────────────────────────────


def test_push_hash_is_deterministic():
    payload = {"title": "x", "stateId": "s1", "priority": 2}
    assert compute_push_hash(payload) == compute_push_hash(payload)


def test_push_hash_independent_of_key_order():
    """Canonicalization sorts keys before hashing — so dict equality, not
    Python's insertion-order, drives identity."""
    a = {"title": "x", "stateId": "s1", "priority": 2}
    b = {"priority": 2, "title": "x", "stateId": "s1"}
    assert compute_push_hash(a) == compute_push_hash(b)


def test_push_hash_changes_on_value_change():
    a = {"title": "x", "stateId": "s1"}
    b = {"title": "x", "stateId": "s2"}
    assert compute_push_hash(a) != compute_push_hash(b)


def test_push_hash_changes_on_label_order_only_when_set_differs():
    """labelIds are an ordered list in the payload, but logically a set.
    Reordering the same labels must NOT change the hash, otherwise the
    skip-if-unchanged optimization fails on every push."""
    a = {"labelIds": ["a", "b"]}
    b = {"labelIds": ["b", "a"]}
    assert compute_push_hash(a) == compute_push_hash(b)
