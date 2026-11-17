# User intent: one definition of who this clone is in team mode — handle rules, the local identity file and the refusal text — shared by the coordinator gate, the profile command and the later init-team/start-session flows.
"""Team identity rules (team-identity-001). Pure apart from reading and writing the
machine-local identity file; no socket, git or coordinator imports."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import secrets
from typing import Mapping, Sequence
import unicodedata

import yaml

from taskmaster import yaml_io

BOOTSTRAP_SKILL = "taskmaster:init-team"
IDENTITY_REL = "local/identity.yaml"            # under .taskmaster/
IDENTITY_VERSION = 1
HANDLE_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?")
# 027: framing says "stored ... by {who}", so these read as a trusted voice. Added after 1.0, so a registry that
# already holds one loads with a warning instead of refusing every key (principals._record).
NEWLY_RESERVED = frozenset({"system", "you", "assistant", "human", "anthropic"})
RESERVED_HANDLES = frozenset({"unassigned", "claude", "codex", "user", "taskmaster", "operator"}) | NEWLY_RESERVED
DEGENERATE_NAMES = frozenset({            # compared after whitespace collapse + casefold
    "your name", "yourname", "your full name", "full name", "name", "first last",
    "firstname lastname", "first name last name", "user", "username", "user name",
    "unknown", "anonymous", "nobody", "none", "null", "root", "admin", "administrator",
    "me", "test", "git", "changeme", "todo", "xxx"})
PLACEHOLDER_EMAIL_DOMAINS = frozenset({"example.com", "example.org", "example.net", "localhost"})
MAX_SUFFIX = 99
SLUG_LIMIT = 30
CREATED_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_CLONE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,63}")
_HEADER = "# Taskmaster team identity for this clone. Machine-local: never synced or committed.\n"


class IdentityRefused(ValueError):
    """An attribution-bearing write this clone cannot attribute; nothing was written."""


@dataclass(frozen=True)
class GitIdentity:
    name: str            # raw `git config user.name`, stripped ("" when unset)
    email: str           # raw `git config user.email`, stripped


@dataclass(frozen=True)
class TeamIdentity:
    handle: str
    clone_id: str
    clone_label: str
    confirmed: bool
    created: str         # CREATED_FORMAT
    git_name: str = ""
    git_email: str = ""

    def to_document(self) -> dict:
        return {"version": IDENTITY_VERSION, "handle": self.handle, "clone_id": self.clone_id,
                "clone_label": self.clone_label, "confirmed": self.confirmed, "created": self.created,
                "source": {"git_name": self.git_name, "git_email": self.git_email}}

    @classmethod
    def from_document(cls, data) -> "TeamIdentity":
        if not isinstance(data, dict):
            raise ValueError("identity.yaml must be a mapping")
        if type(data.get("version")) is not int or data["version"] != IDENTITY_VERSION:
            raise ValueError(f"identity.yaml version must be {IDENTITY_VERSION}")
        handle = data.get("handle")
        if not isinstance(handle, str) or not HANDLE_RE.fullmatch(handle):
            raise ValueError("handle must be lowercase a-z, 0-9 and inner hyphens, at most 32 characters")
        clone_id = data.get("clone_id")
        if not isinstance(clone_id, str) or not _CLONE_ID_RE.fullmatch(clone_id):
            raise ValueError("clone_id is missing or invalid")
        if not isinstance(data.get("clone_label"), str):
            raise ValueError("clone_label must be text")
        if type(data.get("confirmed")) is not bool:
            raise ValueError("confirmed must be true or false")
        if not isinstance(data.get("created"), str):
            raise ValueError("created must be text")
        source = data.get("source", {})
        if source is None:
            source = {}
        if not isinstance(source, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in source.items()):
            raise ValueError("source must be a mapping of text")
        return cls(handle=handle, clone_id=clone_id, clone_label=data["clone_label"], confirmed=data["confirmed"],
                   created=data["created"], git_name=source.get("git_name", ""), git_email=source.get("git_email", ""))


def normalize_name(raw: str) -> str:
    return " ".join((raw or "").split())


def is_degenerate(name: str) -> bool:
    """Empty, whitespace only, no letter or digit, or a known placeholder (D8)."""
    normal = normalize_name(name).casefold()
    if not normal or not any(char.isalnum() for char in normal):
        return True
    return normal in DEGENERATE_NAMES


def usable_email(raw: str) -> str:
    email = (raw or "").strip()
    local, at, domain = email.rpartition("@")
    if not at or not local or not domain or domain.casefold() in PLACEHOLDER_EMAIL_DOMAINS:
        return ""
    return email


def _ascii_words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.casefold()).encode("ascii", "ignore").decode("ascii")
    return [word for word in re.split(r"[^a-z0-9]+", folded) if word]


def slug_for(name: str, email: str = "") -> str:
    """D6: one word gives the word, several give first initial + last word, cut to 30.
    A real name with nothing ASCII falls back to the email's local part; a degenerate
    name never does (D8). "" when nothing usable."""
    if is_degenerate(name):
        return ""
    words = _ascii_words(name)
    if words:
        slug = words[0] if len(words) == 1 else words[0][0] + words[-1]
        return slug[:SLUG_LIMIT]
    address = usable_email(email)
    if not address:
        return ""
    local = address.rpartition("@")[0].partition("+")[0]
    return "".join(_ascii_words(local))[:SLUG_LIMIT]


def choose_handle(base: str, email: str, profiles: Mapping[str, Sequence[str]]) -> tuple[str, bool]:
    """(handle, already_exists). Any profile listing this email wins (D5); otherwise the
    first free of base, base2 … base99 that is not reserved (D7)."""
    wanted = (email or "").casefold()
    if wanted:
        for handle in sorted(profiles):
            if any(isinstance(item, str) and item.casefold() == wanted for item in profiles[handle] or ()):
                return handle, True
    for number in range(1, MAX_SUFFIX + 1):
        candidate = base if number == 1 else f"{base}{number}"
        if candidate not in profiles and candidate not in RESERVED_HANDLES:
            return candidate, False
    raise refused_exhausted(base)


def new_identity(handle: str, git: GitIdentity, *, clone_label: str, now: datetime) -> TeamIdentity:
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc)
    return TeamIdentity(handle=handle, clone_id=secrets.token_hex(8), clone_label=clone_label, confirmed=False,
                        created=now.strftime(CREATED_FORMAT), git_name=git.name, git_email=git.email)


def identity_path(backlog_dir: Path) -> Path:
    return Path(backlog_dir) / IDENTITY_REL


def read_identity(backlog_dir: Path) -> TeamIdentity | None:
    """None when absent; ValueError when unreadable or invalid."""
    path = identity_path(backlog_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read: {exc}") from exc
    try:
        data = yaml_io.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML: {exc}") from exc
    return TeamIdentity.from_document(data)


def write_identity(backlog_dir: Path, identity: TeamIdentity) -> None:
    path = identity_path(backlog_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _HEADER + yaml.safe_dump(identity.to_document(), default_flow_style=False, sort_keys=False,
                                       allow_unicode=True)
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{secrets.token_hex(4)}")
    try:
        with temp.open("wb") as stream:
            stream.write(content.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            temp.unlink()
        except OSError:
            pass
        raise


_EXEMPT = None


def _exempt() -> frozenset:
    """Machine-local operations (D4), built from their source sets so they cannot drift."""
    global _EXEMPT
    if _EXEMPT is None:
        from taskmaster.native import graph_repair, linear_outbox, projection, sync
        _EXEMPT = frozenset(sync.OPERATIONS | linear_outbox.OPERATIONS | graph_repair.OPERATIONS | {
            projection.RESOLVE, "linear.link", "linear.unlink", "task.claim_renew", "task.claim_release",
            "document.import", "profile.create"})
    return _EXEMPT


def imports_a_document(operation: str, arguments: dict) -> bool:
    """Whether the command imports a document (not a restore), also inside a batch. On a team server a member
    key's import needs a person whatever its `source` says, since the caller controls that field (team-server-012,
    Codex review finding 2); the serve identity gate asks this alongside is_attribution_bearing."""
    if operation == "batch":
        items = (arguments or {}).get("commands") if isinstance(arguments, dict) else None
        return isinstance(items, list) and any(
            isinstance(item, dict) and imports_a_document(item.get("operation"), item.get("arguments") or {})
            for item in items)
    return operation == "document.import" and not (isinstance(arguments, dict) and "restore" in arguments)


def is_attribution_bearing(operation: str, arguments: dict) -> bool:
    if operation == "batch":
        items = (arguments or {}).get("commands") or []
        return any(isinstance(item, dict) and is_attribution_bearing(item.get("operation"), item.get("arguments") or {})
                   for item in items)
    if operation == "document.import" and isinstance(arguments, dict):
        # An upload is a person's prose, so team mode needs a person (team-server-012 M5). A file import is a
        # machine-local copy, and a restore replays 013's backup as the host operator (D10): both stay exempt.
        return arguments.get("source") == "upload" and "restore" not in arguments
    return operation not in _exempt()


_TAIL = (f'Run the {BOOTSTRAP_SKILL} skill to set your team identity, or set a real name with '
         f'git config user.name "<your name>", then retry. Nothing was written.')


def refused_name(name: str) -> IdentityRefused:
    shown = "empty" if not (name or "").strip() else f'"{name}"'
    return IdentityRefused("Refused: team mode attributes every shared change to a person, and this clone has no "
                           f"identity yet. git user.name is {shown}, which can't be used as a handle. " + _TAIL)


def refused_unsluggable(name: str) -> IdentityRefused:
    return IdentityRefused(f'Refused: team mode needs a handle for this clone, but neither git user.name "{name}" '
                           "nor user.email gives one (handles use a-z and 0-9). " + _TAIL)


def refused_git(reason: str) -> IdentityRefused:
    return IdentityRefused(f"Refused: team mode needs this clone's identity, but git config could not be read "
                           f"({reason}). " + _TAIL)


def refused_profile(reason: str) -> IdentityRefused:
    return IdentityRefused(f"Refused: team mode can't create this clone's profile ({reason}). " + _TAIL)


def refused_file(reason: str) -> IdentityRefused:
    return IdentityRefused(f"Refused: .taskmaster/{IDENTITY_REL} is unreadable ({reason}). Fix or delete that file, "
                           f"or run the {BOOTSTRAP_SKILL} skill, then retry. Nothing was written.")


def refused_exhausted(base: str) -> IdentityRefused:
    return IdentityRefused(f'Refused: no free handle from "{base}" up to {base}{MAX_SUFFIX} among this backlog\'s '
                           f"profiles. Run the {BOOTSTRAP_SKILL} skill to choose one, then retry. Nothing was written.")
