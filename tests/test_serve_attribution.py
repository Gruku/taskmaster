# User intent: on a team server a record names its author by the caller's key, never by text the caller passed or a
# "claude"/"user" literal, so bob can't file an idea or skip a gate as ann (security review m-2).
from __future__ import annotations

import re

from native_twins import committed
from serve_helpers import open_connection
from test_native_service_build import root  # noqa: F401
from test_serve_principals_e2e import launch  # noqa: F401


def _ident(text, pattern):
    found = re.search(pattern, text)
    assert found, text
    return found.group(0)


def test_records_name_the_keys_handle_not_the_callers_text(launch):  # noqa: F811
    world = launch()
    bob = open_connection(world.url, world.keys["bob"].key)
    try:
        idea = _ident(bob.call("backlog_idea_create", {"title": "Bob's idea", "created_by": "ann"}), r"IDEA-\d+")
        refused = bob.call("backlog_bug_create", {"title": "Bob's bug", "discovered_by": "ann"})
        assert refused.startswith("Error") and "discovered_by must be one of" in refused, refused   # an enum, no name
        note = _ident(bob.call("backlog_note", {"action": "create", "text": "bob's note"}), r"NOTE-\d+")
        skipped = bob.call("backlog_skip_gate", {"task_id": "test-epic-001", "gate": "review-gate",
                                                 "reason": "bob skips", "by": "ann"})
        assert not skipped.startswith("Error"), skipped
    finally:
        bob.close()
    state = committed(world.root)
    assert state[("idea", idea)][0]["created_by"] == "bob"
    assert state[("note", note)][0]["author"] == "bob"
    gates = state[("task", "test-epic-001")][0]["gates"]
    assert gates["review-gate"]["by"] == "bob", gates
