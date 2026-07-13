"""Tests for the v4 sharded storage layout (team-relayout, epic 1)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from taskmaster import taskmaster_v3 as v3  # noqa: E402


class TestV4Constants:
    def test_schema_v4_is_4(self):
        assert v3.SCHEMA_V4 == 4

    def test_v4_greater_than_v3(self):
        assert v3.SCHEMA_V4 > v3.SCHEMA_V3


class TestTaskV4RoundTrip:
    def test_all_fields_go_to_frontmatter(self):
        task = {
            "id": "auth-014", "title": "Login", "status": "todo",
            "epic": "auth", "order": 2.0, "priority": "high",
            "gates": {"spec": "pass"}, v3.BODY_KEY: "## Spec\n\nbody text",
        }
        fm, body = v3.task_v4_to_file(task)
        assert fm["id"] == "auth-014"
        assert fm["epic"] == "auth"
        assert fm["order"] == 2.0
        assert fm["gates"] == {"spec": "pass"}
        assert v3.BODY_KEY not in fm
        assert body == "## Spec\n\nbody text"

    def test_from_file_reattaches_body(self):
        fm = {"id": "auth-014", "title": "Login", "epic": "auth", "order": 2.0}
        task = v3.task_v4_from_file(fm, "prose")
        assert task["id"] == "auth-014"
        assert task[v3.BODY_KEY] == "prose"

    def test_empty_body_omits_body_key(self):
        task = v3.task_v4_from_file({"id": "x", "epic": "e", "order": 1.0}, "")
        assert v3.BODY_KEY not in task

    def test_round_trip_identity(self):
        task = {
            "id": "auth-014", "title": "Login", "status": "todo",
            "epic": "auth", "order": 2.0, v3.BODY_KEY: "body",
        }
        fm, body = v3.task_v4_to_file(task)
        assert v3.task_v4_from_file(fm, body) == task
