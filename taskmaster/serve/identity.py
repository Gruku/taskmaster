# User intent: in server mode the coordinator turns the authenticated key into the actor and the person's profile; nothing the client says and nothing in host git decides who wrote.
"""Server-mode actor source: the key id carried as the command's origin decides who wrote."""
from __future__ import annotations

from types import SimpleNamespace

from taskmaster.coordinator.actor_resolver import LOCAL, Origin
from taskmaster.coordinator.identity_gate import CALLER_SCOPE, IdentityGate, _check_profile
from taskmaster.native import actor, commands
from taskmaster.team_identity import IdentityRefused, imports_a_document, is_attribution_bearing, refused_profile

from .auth import OPERATOR_KEY_ID
from .principals import PrincipalRegistry, RegistryUnavailable, Record

REVOKED = "Refused: this API key was revoked. Nothing was written."
NOT_A_PERSON = ("Refused: team mode attributes every shared change to a person, and the operator "
                "bootstrap token and host-local callers are not one. Use your own key from "
                "`taskmaster serve add-user`. Nothing was written.")
UNAVAILABLE = "Refused: the server could not read its key list just now; retry. Nothing was written."
OPERATOR_ACTOR = ("operator", "operator")


class ServeActorSource:
    def __init__(self, registry: PrincipalRegistry):
        self.registry = registry

    def record(self, origin: Origin) -> Record | None:
        if origin.principal in (None, OPERATOR_KEY_ID):
            return None
        try:
            found = self.registry.lookup(origin.principal)
        except RegistryUnavailable:
            raise IdentityRefused(UNAVAILABLE) from None
        if found is None or not found.active:
            raise IdentityRefused(REVOKED)
        return found

    def identify(self, connection, request, origin) -> tuple[str, str]:
        found = self.record(origin)
        return OPERATOR_ACTOR if found is None else (found.handle, found.key_id)


class ServeIdentityGate(IdentityGate):
    """Team mode in server mode: the key's person gets a profile on their first write. Host git, the
    machine name, identity.yaml and users/*/profile.yaml are never consulted."""

    def __init__(self, root, *, store_id, source: ServeActorSource, checkpoint=lambda stage: None):
        super().__init__(root, store_id=store_id, checkpoint=checkpoint)
        self.source = source

    def admit(self, connection, request, origin=LOCAL) -> bool:
        operation, arguments = request.get("operation"), request.get("arguments") or {}
        member = origin.principal not in (None, OPERATOR_KEY_ID)
        # A member's document import needs a person whatever its caller-set `source` says (012, Codex finding 2).
        if not (is_attribution_bearing(operation, arguments) or (member and imports_a_document(operation, arguments))):
            return False
        if not self._team_mode(connection):
            return False
        if self._replayed(connection, request):          # identity-001 D15: before anything that can refuse
            return False
        found = self.source.record(origin)               # may raise IdentityRefused(REVOKED)
        if found is None:
            raise IdentityRefused(NOT_A_PERSON)
        row = connection.execute("SELECT deleted FROM entity_core WHERE kind='profile' AND public_id=?",
                                 (found.handle,)).fetchone()
        if row is not None and not row[0]:
            self._upgrade(connection)
            return False
        if row is not None:
            raise refused_profile(f"profile {found.handle!r} was deleted from this backlog")
        arguments = {"handle": found.handle, "name": found.name, "emails": list(found.emails),
                     "created": found.created}
        _check_profile(arguments)
        self._upgrade(connection)
        request_id = self._request_id(connection, SimpleNamespace(clone_id=found.key_id, handle=found.handle))
        commands.execute(connection, {
            "protocol": 2, "store_id": self.store_id, "caller_scope": CALLER_SCOPE, "request_id": request_id,
            "operation": "profile.create", "arguments": arguments, "expected_revisions": [],
            "actor": actor.make(found.handle, actor.INTERNAL_AGENT, found.key_id)})
        return True
