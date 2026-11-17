# User intent: a served store is a team store, so `serve` brings a store still at native schema 3 (no author column, so
# every member's text framed as ~unattributed) to the current schema when it starts, and says so in its log
# (security review m-4).
from __future__ import annotations

import sqlite3
from contextlib import closing

from taskmaster.native import schema
from test_native_service_build import root  # noqa: F401
from test_serve_principals_e2e import launch, serve_log  # noqa: F401


def _schema(root_):
    with closing(sqlite3.connect(root_ / ".taskmaster/local/store.db")) as connection:
        return int(connection.execute("SELECT value FROM native_manifest WHERE key='schema_version'").fetchone()[0])


def test_serve_upgrades_a_schema_3_store_at_start(root, launch):  # noqa: F811
    assert _schema(root) == 3
    launch()
    assert _schema(root) == schema.VERSION
    assert f"upgraded the store from native schema 3 to {schema.VERSION}" in serve_log(root)


def test_a_current_store_is_left_alone(root, launch):  # noqa: F811
    from taskmaster.serve.daemon import upgrade_served_schema
    assert upgrade_served_schema(root) == {"from": 3, "to": schema.VERSION}
    assert upgrade_served_schema(root) is None
    launch()
    assert "upgraded the store" not in serve_log(root)
