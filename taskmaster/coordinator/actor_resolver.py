# User intent: the coordinator, never the client, decides who a command is from: one seam fills handle and clone_id after admission; today from this clone's git-derived identity, in server mode from the caller's token.
"""Actor resolution for native commands (team-identity-002)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from taskmaster.native import actor


@dataclass(frozen=True)
class Origin:
    principal: str | None = None     # transport-level caller; None = this host's own clients


LOCAL = Origin()


class IdentitySource(Protocol):
    def identify(self, connection, request: dict, origin: Origin) -> tuple[str, str]: ...   # (handle, clone_id)


class LocalCloneSource:
    """This clone's identity.yaml through the gate's stat cache; never reads git, never refuses."""
    def __init__(self, gate):
        self.gate = gate

    def identify(self, connection, request, origin):
        identity = self.gate.known()
        return (identity.handle, identity.clone_id) if identity is not None else ("", "")


class ActorResolver:
    def __init__(self, source: IdentitySource):
        self.source = source

    def stamp(self, connection, request: dict, origin: Origin = LOCAL) -> dict:
        advisory = (request.get("actor") or {}).get("agent", "")
        handle, clone_id = self.source.identify(connection, request, origin)
        return {**request, "actor": actor.make(handle, actor.normalize_agent(advisory), clone_id)}
