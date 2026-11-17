# User intent: the coordinator, never a prompt, derives this clone's team identity on the first team-mode write and refuses attributed writes it cannot attribute; it stays non-interactive and bounded.
"""Team identity gate for the coordinator's single writer (team-identity-001).

`admit` runs just before `commands.execute` on the writer's connection, outside
any transaction. In solo mode it costs one indexed lookup and one stat.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import socket
import time

from taskmaster import projection_parse
from taskmaster.project import TEAM_LOCAL_RELATIVE, team_mode_enabled
from taskmaster.projection_parse import PROJECT_ID
from taskmaster.team_identity import (
    DEGENERATE_NAMES, GitIdentity, IdentityRefused, TeamIdentity, choose_handle, identity_path, is_attribution_bearing,
    is_degenerate, new_identity, read_identity, refused_file, refused_git, refused_name, refused_profile,
    refused_unsluggable, slug_for, usable_email, write_identity,
)
from taskmaster.native import actor, commands, profiles, schema, upgrade
from taskmaster.native.profiles import known_profiles
from taskmaster.native.queries import Repository
from . import git
from . import scopes
from .actor_resolver import LOCAL
from .git import GitRefused

GIT_TIMEOUT = 5            # per probe
TIMEOUT_BACKOFF = 60.0     # seconds a git timeout is remembered
CALLER_SCOPE = scopes.IDENTITY
LOG = logging.getLogger(__name__)


def _project_doc(connection) -> dict:
    """The database project row's fields, {} when there is none (never project.yaml)."""
    with Repository(connection).snapshot() as snapshot:
        try:
            return snapshot.get("project", PROJECT_ID)["fields"]
        except KeyError:
            return {}


def _git_value(root: Path, key: str) -> str:
    code, out = git.probe(root, "config", "--get", key, ok=(0, 1), timeout=GIT_TIMEOUT)
    return "" if code == 1 else out.decode("utf-8", "replace").strip()


def read_git_identity(root: Path) -> GitIdentity:
    """`git config` user.name and user.email; "" when unset. GitRefused propagates."""
    return GitIdentity(_git_value(root, "user.name"), _git_value(root, "user.email"))


def disk_profiles(backlog_dir: Path) -> dict[str, list[str]]:
    """handle -> emails of the `users/*/profile.yaml` files on disk, read through the
    projection parser. A teammate's profile arrives this way before any sync imports it;
    a file that does not parse is left out of the handle decision, with a log line."""
    users = Path(backlog_dir) / "users"
    try:
        with os.scandir(users) as entries:
            names = sorted(entry.name for entry in entries if entry.is_dir(follow_symlinks=False))
    except FileNotFoundError:
        return {}
    except OSError as exc:
        LOG.warning("team identity: cannot list %s for known profiles: %s", users, exc)
        return {}
    result = {}
    for name in names:
        rel = f"users/{name}/profile.yaml"
        try:
            kind, ident = projection_parse.classify(rel)
            content = (users / name / "profile.yaml").read_bytes()
            doc = projection_parse.projected_file(kind, ident, content)[(kind, ident)][0]
        except FileNotFoundError:
            continue
        except Exception as exc:  # noqa: BLE001 - an unparseable file is only left out of this decision
            LOG.warning("team identity: ignoring %s for the handle choice: %s", rel, exc)
            continue
        emails = doc.get("emails")
        result[ident] = [e for e in emails if isinstance(e, str)] if isinstance(emails, list) else []
    return result


def _profile_arguments(identity: TeamIdentity) -> dict:
    email = usable_email(identity.git_email)
    return {"handle": identity.handle, "name": identity.git_name or identity.handle,
            "emails": [email] if email else [], "created": identity.created}


def _check_profile(arguments: dict) -> None:
    """The profile.create contract, checked before anything is written; raises IdentityRefused."""
    try:
        profiles.validate("profile.create", arguments)
    except ValueError as exc:
        raise refused_profile(str(exc)) from None


def _stat_key(path: Path):
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return None
    return info.st_mtime_ns, info.st_size


class IdentityGate:
    def __init__(self, root: Path, *, store_id: str, checkpoint=lambda stage: None, clock=time.monotonic):
        self.root = Path(root)
        self.backlog_dir = self.root / ".taskmaster"
        self.store_id, self.checkpoint, self.clock = store_id, checkpoint, clock
        self._team_key, self._team = object(), False
        self._file_key, self._identity = None, None
        self._backoff_until, self._backoff_reason = None, None

    def current(self) -> TeamIdentity | None:
        return self._identity

    def admit(self, connection, request, origin=LOCAL) -> bool:
        """Run before commands.execute. True when it committed a profile (the caller sets export_needed).
        Raises IdentityRefused. Requires no open transaction and leaves none."""
        if not is_attribution_bearing(request.get("operation"), request.get("arguments") or {}):
            return False
        if not self._team_mode(connection):
            return False
        if self._replayed(connection, request):   # D15: before anything that can refuse
            return False
        identity = self._load_identity()
        if identity is not None:
            return self._ensure_profile(connection, identity)
        found = self._read_git()
        if is_degenerate(found.name):
            raise refused_name(found.name)
        base = slug_for(found.name, found.email)
        if not base:
            raise refused_unsluggable(found.name)
        if base in DEGENERATE_NAMES:
            raise refused_profile(f"the handle {base!r} derived from git config is a placeholder word")
        _check_profile({"handle": base, "name": found.name, "emails": [], "created": "2000-01-01T00:00:00Z"})
        # Team mode is on, so the store moves to schema 4 before teammates' profiles are
        # read: the sync that follows can then import the ones that arrived on disk (D21).
        self._upgrade(connection)
        with Repository(connection).snapshot() as snapshot:
            known = known_profiles(snapshot)
        for handle, emails in disk_profiles(self.backlog_dir).items():
            known[handle] = list(dict.fromkeys(known.get(handle, []) + emails))
        handle, _ = choose_handle(base, usable_email(found.email), known)
        identity = new_identity(handle, found, clone_label=socket.gethostname(), now=datetime.now(timezone.utc))
        _check_profile(_profile_arguments(identity))
        write_identity(self.backlog_dir, identity)
        self._file_key, self._identity = _stat_key(identity_path(self.backlog_dir)), identity
        self.checkpoint("identity_written")
        return self._ensure_profile(connection, identity)

    def _team_mode(self, connection) -> bool:
        """Cached on the project row's last_seq and local/team.yaml's stat (D16)."""
        row = connection.execute("SELECT last_seq FROM entity_core WHERE kind='project' AND public_id=?",
                                 (PROJECT_ID,)).fetchone()
        key = (row[0] if row else None, _stat_key(self.root / TEAM_LOCAL_RELATIVE))
        if key != self._team_key:
            self._team = bool(team_mode_enabled(self.root, project_doc=_project_doc(connection)))
            self._team_key = key
        return self._team

    def known(self) -> TeamIdentity | None:
        """The local identity file as the actor source reads it: no git, no refusals."""
        try:
            return self._load_identity()
        except (IdentityRefused, OSError):
            return None

    def _load_identity(self) -> TeamIdentity | None:
        path = identity_path(self.backlog_dir)
        key = _stat_key(path)
        if key is None:
            self._file_key, self._identity = None, None
            return None
        if key != self._file_key:
            try:
                identity = read_identity(self.backlog_dir)
            except ValueError as exc:
                raise refused_file(str(exc)) from None
            if identity is None:   # removed between the stat and the read
                self._file_key, self._identity = None, None
                return None
            self._file_key, self._identity = key, identity
        return self._identity

    def _replayed(self, connection, request) -> bool:
        """A request with a durable receipt already committed; its replay is never refused (D15)."""
        if request.get("request_id") is None:
            return False
        return connection.execute(
            "SELECT 1 FROM command_receipts WHERE store_id=? AND caller_scope=? AND request_id=?",
            (self.store_id, request["caller_scope"], request["request_id"])).fetchone() is not None

    def _read_git(self) -> GitIdentity:
        now = self.clock()
        if self._backoff_until is not None and now < self._backoff_until:
            raise refused_git(self._backoff_reason)
        try:
            return read_git_identity(self.root)
        except GitRefused as exc:
            if "timed out" in str(exc):
                self._backoff_until, self._backoff_reason = now + TIMEOUT_BACKOFF, str(exc)
            raise refused_git(str(exc)) from None

    def _upgrade(self, connection) -> None:
        version = connection.execute("SELECT value FROM native_manifest WHERE key='schema_version'").fetchone()[0]
        if int(version) < schema.VERSION:
            upgrade.upgrade(connection)
            self.checkpoint("schema_upgraded")

    def _ensure_profile(self, connection, identity: TeamIdentity) -> bool:
        """Create this clone's profile row when it is missing. Decided on the row itself on
        every write (one indexed lookup), so a row that disappeared is recreated."""
        handle = identity.handle
        row = connection.execute("SELECT deleted FROM entity_core WHERE kind='profile' AND public_id=?",
                                 (handle,)).fetchone()
        if row is not None and not row[0]:
            self._upgrade(connection)   # only after the refusals above: a store a dev build left at 4 moves to 5
            return False
        if row is not None:
            raise refused_profile(f"profile {handle!r} was deleted from this backlog")
        arguments = _profile_arguments(identity)
        _check_profile(arguments)
        self._upgrade(connection)
        rel = f"users/{handle}/profile.yaml"
        email = usable_email(identity.git_email).casefold()
        if email and connection.execute("SELECT 1 FROM projection WHERE file=?", (rel,)).fetchone() is None:
            on_disk = disk_profiles(self.backlog_dir).get(handle)
            if on_disk is not None and email in (item.casefold() for item in on_disk):
                # This person's profile is on disk from another clone and not imported yet:
                # the next sync imports it, so no competing row is created here.
                return False
        envelope = {"protocol": 2, "store_id": self.store_id, "caller_scope": CALLER_SCOPE,
                    "request_id": self._request_id(connection, identity), "operation": "profile.create",
                    "arguments": arguments, "expected_revisions": [],
                    "actor": actor.make(identity.handle, actor.INTERNAL_AGENT, identity.clone_id)}
        commands.execute(connection, envelope)
        return True

    def _request_id(self, connection, identity: TeamIdentity) -> str:
        """Derived from identity.yaml, so a retry after a crash replays its receipt; a new id
        once an earlier create committed and its row is gone (recreation)."""
        base = f"profile-{identity.clone_id}-{identity.handle}"
        like = base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "-r%"
        done = connection.execute(
            "SELECT COUNT(*) FROM command_receipts WHERE store_id=? AND caller_scope=? "
            "AND (request_id=? OR request_id LIKE ? ESCAPE '\\')",
            (self.store_id, CALLER_SCOPE, base, like)).fetchone()[0]
        return base if done == 0 else f"{base}-r{done + 1}"
