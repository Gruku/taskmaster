"""User intent: an operator who calls a maintenance or sync tool that the native core
does not serve yet, on an activated project, must be told why it cannot run there and
what to do instead — in the tool's own error format — and nothing may change.
"""
from __future__ import annotations

from contextlib import closing
import json
import sqlite3

import pytest

from taskmaster import backlog_server as bs
from taskmaster.native_routing import registry
from native_twins import committed, is_native, make_twins

# (tool, action) -> (call, a phrase the guidance must name: the tool to use instead)
REFUSALS = {
    ("backlog_init", None): (lambda: bs.backlog_init(project_name="again"), "backlog_status"),
    ("backlog_migrate_v3", None): (lambda: bs.backlog_migrate_v3(), "backlog_store_status"),
    ("backlog_migrate_v4", None): (lambda: bs.backlog_migrate_v4(), "backlog_store_status"),
    ("backlog_canonicalize_layout", None): (lambda: bs.backlog_canonicalize_layout(), ".taskmaster/"),
    ("backlog_backfill_lanes", None): (lambda: bs.backlog_backfill_lanes(), "backlog_update_task"),
    ("backlog_index_status", None): (lambda: bs.backlog_index_status(rebuild=True), "backlog_store_status"),
    ("backlog_link", "reconcile"): (lambda: bs.backlog_link(action="reconcile"), 'action="validate"'),
    ("backlog_linear", "bootstrap_apply"): (
        lambda: bs.backlog_linear(action="bootstrap_apply", workspace_alias="cm", team_id="T1", token_env="X"),
        "linear.yaml"),
}
JSON_ROUTERS = {"backlog_link", "backlog_linear"}


@pytest.fixture
def twins(tmp_path, monkeypatch):
    def seed():
        bs.backlog_add_task(title="Seeded", epic="test-epic", phase="dev")
    return make_twins(tmp_path, monkeypatch, seed)


def _state(root):
    with closing(sqlite3.connect(root / ".taskmaster" / "local" / "store.db")) as connection:
        return connection.execute("SELECT value FROM native_manifest WHERE key='state'").fetchone()[0]


def test_every_unrouted_pair_has_operator_guidance():
    from test_native_bypass_gate import inventory_pairs, routed_pairs
    assert set(inventory_pairs()) - routed_pairs() == set(REFUSALS)
    assert set(registry.GUIDANCE) == set(REFUSALS)


@pytest.mark.parametrize("pair", sorted(REFUSALS, key=str), ids=lambda p: f"{p[0]}.{p[1]}")
def test_refusal_says_why_and_what_to_do_and_changes_nothing(twins, pair):
    call, instead = REFUSALS[pair]
    before = committed(twins.native)
    with twins.at(twins.native):
        answer = call()
    if pair[0] in JSON_ROUTERS:
        text = json.loads(answer)["error"]
    else:
        assert answer.startswith("Error: "), answer
        text = answer
    assert "native" in text and instead in text and text.endswith("Nothing was changed."), text
    assert "not yet routed" not in text, text
    assert committed(twins.native) == before
    assert is_native(twins.native) and _state(twins.native) == "ready"


def test_unservable_native_store_refuses_in_each_router_error_shape(twins):
    """A store this runtime cannot serve refuses through the same seam as an unrouted
    tool, so it owes the caller the same shape: the JSON routers parse their answer."""
    with closing(sqlite3.connect(twins.native / ".taskmaster" / "local" / "store.db",
                                 isolation_level=None)) as connection:
        connection.execute("UPDATE native_manifest SET value='cutover' WHERE key='state'")
    with twins.at(twins.native):
        assert "not ready" in json.loads(bs.backlog_link(action="validate"))["error"]
        assert "not ready" in json.loads(bs.backlog_linear(action="status"))["error"]
        text = bs.backlog_status()
    assert text.startswith("Error: ") and "not ready" in text, text
