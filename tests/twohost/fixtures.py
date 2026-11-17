# User intent: one reusable way for twohost modules to stand up the team (a seeded host store, a daemon started through the launcher, ann, bob and the operator each in their own machine) so a scenario is just its assertions (015 A2).
"""Team world builder shared by tests/test_twohost_*.py."""
from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from taskmaster.serve.auth import ensure_operator_token
from taskmaster.serve.principals import add_user

from .evidence import Evidence
from .host import Host, sweep_leftovers
from .member import Member
from .world import Machine

TASK_TITLES = ("Build the thing", "Test the thing", "Document the thing", "Ship the thing", "Review the thing")


@dataclass
class Team:
    base: Path
    root: Path
    host: Host
    ann: Member
    bob: Member
    operator: Member
    evidence: Evidence
    # Out of the repr: pytest prints a failing test's Team, and these are live keys (security review m-6).
    keys: dict = field(default_factory=dict, repr=False)

    @property
    def operator_token(self) -> str:
        return self.operator.key


def seed_host_root(base: Path, patch, *, tasks: int = len(TASK_TITLES)) -> Path:
    """A native project (one epic, one phase, `tasks` tasks) under `base`, built through the real tools."""
    from native_twins import make_twins
    from taskmaster import backlog_server as bs

    def seed():
        bs.backlog_project_init(name="Twohost Project")      # a migrated host already has its project.yaml
        for title in TASK_TITLES[:tasks]:
            bs.backlog_add_task(title=title, epic="test-epic", phase="dev")
    twins = make_twins(base / "twins", patch, seed, visibility=None)
    _upgrade_to_current_schema(twins.native)
    return twins.native


def _upgrade_to_current_schema(root: Path) -> None:
    """A served project is at the current schema (migrated stores are team-mode, schema 5); the twin fixture's is 3, which
    has no `actor` column, so every teammate's text would read as unattributed (team-server-027 design 3.1)."""
    import sqlite3
    from taskmaster.native import upgrade
    connection = sqlite3.connect(root / ".taskmaster" / "local" / "store.db", isolation_level=None)
    try:
        upgrade.upgrade(connection)
    finally:
        connection.close()


@contextlib.contextmanager
def team(base: Path, patch, *, flags=(), fault: str | None = None, name: str | None = None, clock_file: Path | None = None,
         keeper_seconds: float | None = None, root: Path | None = None, build: str | None = None):
    """Seed (or reuse `root`), start the daemon through the launcher, give ann and bob keys; sweep and stop on exit."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    sweep_leftovers(base)
    root = root or seed_host_root(base, patch)
    # Unnamed worlds take their evidence name from the module's mktemp dir (s1, s20, ...), not one shared "host".
    evidence = Evidence.from_env(name or (base.parent.name if base.name == "run" else base.name))
    host_machine = Machine.create(base, "host", root=root)
    host = Host(host_machine, base, flags=list(flags), fault=fault, clock_file=clock_file, keeper_seconds=keeper_seconds,
                name=name or "host", build=build)
    keys = {handle: add_user(root, handle, name=handle.title(), emails=[f"{handle}@twohost.invalid"])[0]
            for handle in ("ann", "bob")}
    operator_token = ensure_operator_token(root)[0]
    try:
        host.start()
        members = {handle: Member(Machine.create(base, handle), host, key, evidence) for handle, key in keys.items()}
        operator = Member(host_machine, host, operator_token, evidence)
        yield Team(base, root, host, members["ann"], members["bob"], operator, evidence, keys)
    finally:
        host.stop()
        evidence.flush()


def parse(text: str):
    """A tool answer as JSON when it is, else the text."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def rows(text: str) -> list[list[str]]:
    """The cells of a backlog_query answer (columns aligned by 2+ spaces), header excluded, row-count footer dropped."""
    import re
    lines = [line for line in text.splitlines() if line.strip() and not re.fullmatch(r"\d+ rows?", line.strip())]
    return [re.split(r"\s{2,}", line.strip()) for line in lines[1:]]
