# User intent: build the solo "laptop" project (L) the 014 migration starts from - an epic, five tasks, a handover, an uploaded doc and a claim held on the laptop, tracked in Git with a bare origin (O) - so S0 runs the real pack and init CLIs against the shape a real user has (015 A5 S0).
"""The solo laptop world for the migration scenario."""
from __future__ import annotations

import contextlib
import os
import sqlite3
import subprocess
from pathlib import Path

from .member import machine_cli, run_git
from .world import Machine

URL = "http://127.0.0.1:7470/mcp"        # the marker's server URL (a loopback http URL is allowed by 009)


def seed_laptop(base: Path, patch) -> tuple[Path, str]:
    """A native project tracked on `main`: returns (root, the laptop-era session id that holds the claim)."""
    import migration_helpers as mh
    from native_twins import make_twins
    from taskmaster import backlog_server as bs

    def seed():
        bs.backlog_project_init(name="Laptop Project")
        import yaml
        from taskmaster.project import project_yaml_path
        manifest = yaml.safe_load(project_yaml_path(Path(bs._project_root_or_cwd())).read_text(encoding="utf-8")) or {}
        manifest["team"] = {"sync": {"mode": "manual"}}               # a team project: uploads credit the person
        bs.backlog_project_set(yaml.safe_dump(manifest))
        for title in ("Build the thing", "Test the thing", "Document the thing", "Ship the thing", "Review the thing"):
            bs.backlog_add_task(title=title, epic="test-epic", phase="dev")
        bs.backlog_update_task("test-epic-001", "docs", "spec:docs/specs/x.md")    # declared, so it exports and restores
        bs.backlog_handover_create(tldr="Laptop-era handover", next_action="carry on", body="Seeded on the laptop.")
        bs.backlog_pick_task(task_id="test-epic-001")
    twins = make_twins(Path(base) / "laptop-twins", patch, seed, visibility=None)
    from taskmaster import store
    store.reset_for_tests()
    root = mh.make_source(twins.native, with_document=True, tracked_extras=True)
    with contextlib.closing(sqlite3.connect(root / ".taskmaster" / "local" / "store.db")) as connection:
        sessions = [row[0] for row in connection.execute("SELECT session FROM sessions")]
    return root, (sessions[0] if sessions else "")


def laptop_machine(base: Path, root: Path) -> Machine:
    return Machine.create(base, "laptop", root=root)


def make_origin(base: Path, laptop: Machine) -> Path:
    """A bare origin O, wired as the laptop's `origin` with `main` pushed."""
    origin = Path(base) / "origin.git"
    origin.mkdir(parents=True, exist_ok=True)
    run_git(laptop, origin, "init", "-q", "--bare", "-b", "main")
    run_git(laptop, laptop.root, "remote", "add", "origin", str(origin))
    run_git(laptop, laptop.root, "push", "-q", "origin", "main")
    return origin


def clone_host(base: Path, origin: Path, name: str = "hostclone") -> Machine:
    machine = Machine.create(base, name)
    clone = Path(base) / "machines" / name / "clone"
    done = subprocess.run(["git", "clone", "-q", str(origin), str(clone)], env=machine.env, capture_output=True, text=True,
                          timeout=120)
    assert done.returncode == 0, done.stderr
    machine.root = clone
    return machine


def migrated_world(base: Path, patch) -> dict:
    """The runbook's sequence on two machines: seed L, push to O, `serve init pack`, push, clone H, `serve init --from`.
    Returns the machines, paths and CLI results (the bundles are left in place for the caller)."""
    base = Path(base)
    root, session_id = seed_laptop(base, patch)
    laptop = laptop_machine(base, root)
    origin = make_origin(base, laptop)
    bundle = laptop.home / "proj.tmbundle"
    packed = machine_cli(laptop, "serve", "init", "pack", "--root", str(root), "--url", URL, "--out", str(bundle),
                         "--json", timeout=300)
    acl = None
    if os.name == "nt" and bundle.exists():
        acl = subprocess.run(["icacls", str(bundle)], capture_output=True, text=True, timeout=30).stdout
    run_git(laptop, root, "push", "-q", "origin", "main")
    hostclone = clone_host(base, origin)
    host_bundle = hostclone.home / "proj.tmbundle"
    host_bundle.write_bytes(bundle.read_bytes())                  # the runbook's `scp`
    initialised = machine_cli(hostclone, "serve", "init", "--root", str(hostclone.root), "--from", str(host_bundle),
                              "--json", timeout=300)
    return {"base": base, "laptop": laptop, "origin": origin, "bundle": bundle, "host": hostclone,
            "host_bundle": host_bundle, "packed": packed, "init": initialised, "session_id": session_id, "acl": acl}
