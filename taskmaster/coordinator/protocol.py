"""Small shared IPC contract; importing a client never imports the service."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from taskmaster.native import contracts, db, schema

# N13 adds explicit import/barrier operations. A stale N12 owner must refuse the
# new client handshake rather than accepting only part of its synchronization.
# 3: managed Git; an owner without the durable publication pin must refuse.
SERVICE_PROTOCOL = 4
MAX_MESSAGE_BYTES = contracts.MAX_BYTES + 8192
# Receipts include committed fields for up to 100 existing entities. Their
# output can exceed a small metadata request without accepting a larger input.
MAX_RESPONSE_BYTES = 64 * 1024 * 1024


class ServiceUnavailable(RuntimeError):
    def __init__(self, message, *, request_id=None, caller_scope=None, may_have_committed=True):
        super().__init__(message)
        self.request_id = request_id
        self.caller_scope = caller_scope
        # A transport failure is not evidence of rollback. Only an explicit
        # pre-admission refusal/cancellation may safely narrow this answer.
        self.may_have_committed = may_have_committed

    def public_payload(self):
        result = {'error': str(self), 'may_have_committed': self.may_have_committed}
        if self.request_id is not None:
            result.update(request_id=self.request_id, caller_scope=self.caller_scope)
        return result


class HandshakeError(ServiceUnavailable):
    pass


def connect(root: Path, *, readonly=False):
    path = root / '.taskmaster/local/store.db'
    uri = path.as_uri() + ('?mode=ro' if readonly else '?mode=rw')
    connection = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=30)
    connection.execute('PRAGMA busy_timeout=30000')
    if readonly:
        connection.execute('PRAGMA query_only=ON')
    return connection


def identify(root: Path) -> dict:
    root = Path(root).resolve(strict=True)
    with closing(connect(root, readonly=True)) as connection:
        connection.execute('BEGIN')
        return identify_connection(root, connection)


def identify_connection(root: Path, connection) -> dict:
    """Fence the exact database handle about to be used, not a separate path read."""
    state = db.assert_native(connection)
    return {'root': str(root), 'store_id': state['store_id'], 'schema': schema.VERSION,
            'protocol': schema.PROTOCOL, 'service_protocol': SERVICE_PROTOCOL}


def encode(value, *, limit=MAX_MESSAGE_BYTES) -> bytes:
    raw = json.dumps(value, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(raw) > limit:
        raise ValueError('coordinator message exceeds byte limit')
    return raw


def check_handshake(value, identity, nonce):
    if not isinstance(value, dict) or value != dict(identity, nonce=nonce):
        raise HandshakeError('coordinator root/store/schema/protocol/generation mismatch; reconnect explicitly')
