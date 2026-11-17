# User intent: on a team server the project manifest and Linear links are the operator's (user rulings on the security
# review's m-9 and m-7): a member's tool call or commands= batch is refused with nothing written, the operator's works.
from __future__ import annotations

import json

import yaml

from serve_helpers import open_connection
from test_native_service_build import root  # noqa: F401
from test_serve_principals_e2e import launch  # noqa: F401

MANIFEST = yaml.safe_dump({"schema_version": 1, "meta": {"name": "Hijacked", "slug": "hijacked"},
                           "team": {"sync": {"mode": "off"}}})


def _call(world, token, tool, args):
    connection = open_connection(world.url, token)
    try:
        return connection.call(tool, args)
    finally:
        connection.close()


def test_a_member_cannot_write_the_manifest_or_link_linear(launch):  # noqa: F811
    world = launch()
    ann = world.keys["ann"].key
    before = (world.root / ".taskmaster/project.yaml").read_bytes() if (world.root / ".taskmaster/project.yaml").exists() else None
    for tool, args in (("backlog_project_set", {"yaml_content": MANIFEST}),
                       ("backlog_project_init", {"name": "Hijack"})):
        answer = _call(world, ann, tool, args)
        assert answer.startswith("Error") and "host operator action" in answer, (tool, answer)
    for action in ("link", "unlink"):
        answer = json.loads(_call(world, ann, "backlog_linear", {"action": action, "task_id": "x-001",
                                                                "external_key": "ENG-1"}))
        assert "host operator action" in answer["error"], answer
    batch = _call(world, ann, "backlog_batch_update", {"commands": [
        {"operation": "project.set", "arguments": {"document": {"schema_version": 1,
                                                                "meta": {"name": "H", "slug": "h"}}}}]})
    assert "host operator action" in batch, batch
    after = (world.root / ".taskmaster/project.yaml").read_bytes() if (world.root / ".taskmaster/project.yaml").exists() else None
    assert after == before


def test_the_tool_side_refusal_spares_the_operator_and_solo_use(monkeypatch):
    from taskmaster.coordinator import client
    from taskmaster.native_routing import overview
    monkeypatch.setattr(client, "ORIGIN_PROVIDER", None)
    assert overview._manifest_refusal() is None                              # not serving: solo behaviour
    monkeypatch.setattr(client, "ORIGIN_PROVIDER", lambda: "operator")
    assert overview._manifest_refusal() is None
    assert overview.served_linear_guard("link", "")[1] is None
    monkeypatch.setattr(client, "ORIGIN_PROVIDER", lambda: "0123456789abcdef")
    assert "host operator action" in overview._manifest_refusal()
    assert "host operator action" in overview.served_linear_guard("unlink", "")[1]
