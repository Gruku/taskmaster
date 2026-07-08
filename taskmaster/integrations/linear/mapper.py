"""Taskmaster → Linear payload mapper (linear-004).

Pure functions. No IO, no network, no LLM. Translates TM entity dicts to
the GraphQL input payloads expected by `LinearClient.issue_upsert` /
`project_upsert`. All resolutions go through the workspace config's lookup
tables (status_mapping, priority_mapping, user_mapping, label_config) — so
mappings are static config, never inferred at call time.

`compute_push_hash` canonicalizes a payload (sorted keys, sorted list members
for set-like fields) and returns a sha256. The worker compares this to the
Tracker's stored `push_hash` to skip API calls when nothing changed since
the last successful push.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


# Fields whose list value is logically a set (order doesn't matter for hashing).
_SET_LIKE_FIELDS = frozenset({"labelIds"})


def tm_task_to_linear_payload(
    task: dict[str, Any],
    workspace: dict[str, Any],
    *,
    linear_issue_id: str | None = None,
) -> dict[str, Any]:
    """Build a Linear `issueCreate` / `issueUpdate` input payload from a TM task.

    If `linear_issue_id` is passed, the payload includes `id` so the client
    routes to `issueUpdate`. Otherwise the payload is for a fresh `issueCreate`.

    Raises ValueError if the task's status is not in workspace.status_mapping —
    bootstrap is supposed to populate that table fully, so a missing entry
    indicates a config bug worth surfacing rather than silently mis-pushing.
    """
    title = task.get("title", "")
    status = task.get("status", "todo")
    status_mapping = workspace.get("status_mapping") or {}
    if status not in status_mapping:
        raise ValueError(
            f"status_mapping has no entry for {status!r}; "
            f"re-run /linear bootstrap to populate it"
        )

    priority_mapping = workspace.get("priority_mapping") or {}
    priority = priority_mapping.get(task.get("priority", ""), 0)

    payload: dict[str, Any] = {
        "title": title,
        "stateId": status_mapping[status],
        "priority": priority,
    }

    if linear_issue_id:
        payload["id"] = linear_issue_id

    owner = task.get("owner")
    user_mapping = workspace.get("user_mapping") or {}
    if owner and owner in user_mapping:
        payload["assigneeId"] = user_mapping[owner]

    label_cfg = workspace.get("label_config") or {}
    tag_to_label = label_cfg.get("tag_to_label_id") or {}
    label_ids = [tag_to_label[t] for t in task.get("tags") or [] if t in tag_to_label]
    if label_ids:
        payload["labelIds"] = label_ids

    return payload


def tm_epic_to_linear_project_payload(
    epic: dict[str, Any],
    workspace: dict[str, Any],
    *,
    linear_project_id: str | None = None,
) -> dict[str, Any]:
    """Build a Linear `projectCreate` / `projectUpdate` input payload from a TM epic."""
    del workspace  # unused in v1 (epic→project is direct; no lookup tables)
    payload: dict[str, Any] = {
        "name": epic.get("name") or epic.get("title") or epic.get("id", ""),
    }
    if epic.get("description"):
        payload["description"] = epic["description"]
    if linear_project_id:
        payload["id"] = linear_project_id
    return payload


def compute_push_hash(payload: dict[str, Any]) -> str:
    """Stable sha256 over the canonicalized payload.

    Canonicalization: sort top-level keys; for set-like fields (see
    _SET_LIKE_FIELDS) sort the list members. Independent of dict insertion
    order so callers can rebuild the payload from any starting point.
    """
    canonical = _canonicalize(payload)
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _canonicalize(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if k in _SET_LIKE_FIELDS and isinstance(v, list):
            out[k] = sorted(v)
        else:
            out[k] = v
    return out
