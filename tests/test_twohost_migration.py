# User intent: the real solo-to-server move end to end on two machines - `serve init pack` on the laptop, push, clone, `serve init --from` on the host, then the daemon - proves the laptop is fenced, nothing private travels in the bundle, the claim and the store survive, and records the bundle's Windows ACL for the reviewer (team-server-015 S0/S8).
from __future__ import annotations

import contextlib
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from twohost.laptop import migrated_world
from twohost.member import run_git, stdio_calls

pytestmark = [pytest.mark.twohost, pytest.mark.slow, pytest.mark.real_service_process,
              pytest.mark.xdist_group("heavy_processes"), pytest.mark.timeout(600)]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("s0") / "run"
    base.mkdir()
    try:
        yield migrated_world(base, patch)
    finally:
        patch.undo()


def test_pack_and_init_both_succeed_and_the_bundle_is_left_for_the_operator_to_delete(world):
    assert world["packed"].returncode == 0, (world["packed"].stdout, world["packed"].stderr)
    assert world["init"].returncode == 0, (world["init"].stdout, world["init"].stderr)
    assert world["bundle"].exists() and world["host_bundle"].exists()      # the tools never delete the operator's files
    for path in (world["bundle"], world["host_bundle"]):                    # runbook step: delete it on both machines
        path.unlink()
    assert not world["bundle"].exists() and not world["host_bundle"].exists()


def test_the_host_store_is_private_and_the_untrack_commit_and_marker_are_on_main(world):
    host, laptop = world["host"], world["laptop"]
    local = host.root / ".taskmaster" / "local"
    assert (local / "store.db").is_file()
    if os.name != "nt":
        assert stat.S_IMODE((local / "store.db").stat().st_mode) == 0o600
        assert stat.S_IMODE(local.stat().st_mode) == 0o700
    for machine in (laptop, host):
        assert run_git(machine, machine.root, "ls-files", ".taskmaster") == ""        # the untrack commit is on main
        assert ".taskmaster-server.json" in run_git(machine, machine.root, "ls-files")  # the marker is committed


def test_the_laptops_old_store_is_fenced_and_a_stdio_start_there_is_refused(world):
    laptop = world["laptop"]
    answers = stdio_calls(laptop.env, laptop.root, [("backlog_status", {}), ("backlog_list_tasks", {})])
    assert all("Taskmaster server" in a for a in answers), answers
    assert (laptop.root / ".taskmaster" / "local" / "store.db").exists()    # still on disk, only fenced


def test_no_laptop_session_row_or_free_page_residue_travelled_in_the_bundles_store(world):
    store = world["host"].root / ".taskmaster" / "local" / "store.db"
    session_id = world["session_id"]
    assert session_id, "the laptop seed should have recorded a session"
    with contextlib.closing(sqlite3.connect(store)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert connection.execute("PRAGMA freelist_count").fetchone()[0] == 0
        holders = set()
        for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            for column in [row[1] for row in connection.execute(f"PRAGMA table_info('{table}')")]:
                if connection.execute(f"SELECT 1 FROM '{table}' WHERE CAST({column} AS TEXT) LIKE ? LIMIT 1",
                                      (f"%{session_id}%",)).fetchone():
                    holders.add(table)
    # accepted (015 Q2/Q3): laptop-era session ids stay inside the old change history; no live table keeps one
    assert holders <= {"changes", "domain_events"}, holders
    for needle in (str(world["laptop"].root), str(world["laptop"].root).replace("\\", "/")):
        assert needle.encode() not in store.read_bytes(), f"the laptop's path {needle} is in the installed store"


def test_the_laptop_held_claim_keeps_its_holder_and_is_not_released_on_the_host(world):
    store = world["host"].root / ".taskmaster" / "local" / "store.db"
    with contextlib.closing(sqlite3.connect(store)) as connection:
        held = connection.execute(
            "SELECT c.public_id, json_extract(o.locked_by_json,'$') FROM entity_core c JOIN task_operational o "
            "ON o.entity_key=c.entity_key WHERE c.kind='task' AND COALESCE(json_extract(o.locked_by_json,'$'),'')<>''"
        ).fetchall()
    assert [row[0] for row in held] == ["test-epic-001"], held
    assert held[0][1], held


def test_the_daemon_starts_on_the_migrated_root_and_serves_its_tasks(world):
    from taskmaster.serve.auth import ensure_operator_token
    from taskmaster.serve.principals import add_user
    from twohost.host import Host
    from twohost.member import Member
    from twohost.world import Machine
    host_machine = world["host"]
    daemon = Host(host_machine, world["base"], name="s0host")
    key = add_user(host_machine.root, "ann", name="Ann", emails=["ann@twohost.invalid"])[0]
    ensure_operator_token(host_machine.root)
    try:
        daemon.start()
        ann = Member(Machine.create(world["base"], "ann"), daemon, key)
        text = ann.call("backlog_list_tasks", {"limit": 10})
        assert "Build the thing" in text and "Review the thing" in text, text
    finally:
        daemon.stop()


@pytest.mark.gap("team-server-ACL")
@pytest.mark.skipif(os.name != "nt", reason="the DACL pin is Windows-only")
def test_p_acl_the_windows_bundle_acl_grants_a_principal_beyond_owner_system_and_administrators(world):
    """Pin (P-ACL): the bundle `serve init pack` wrote inherits its folder's ACL on Windows (the follow-up that
    applies `ownership._windows_private` flips this). The icacls output is kept as evidence."""
    acl = world["acl"]
    assert acl, "the bundle's icacls output was not captured"
    from twohost.evidence import Evidence
    Evidence.from_env().write("icacls-bundle.txt", acl)
    names = {line.split(":")[0].strip().split("\\")[-1].lower() for line in acl.splitlines() if ":" in line}
    names -= {os.environ.get("USERNAME", "").lower(), "system", "administrators", "nt authority\\system", ""}
    assert names, acl
