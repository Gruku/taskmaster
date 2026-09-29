"""Small shared IPC contract; importing a client never imports the service."""
from contextlib import closing
import functools
import hashlib
import json
from pathlib import Path
import re
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
# The default budget of one sync (IPC, MCP resync, managed-Git pre-sync). Measured on
# CodeMaestro (3,708 projection files; N13 report, "Rehearsal fixes: D1 performance"):
# a warm no-edit full sync takes ~1.3 s, a cold one ~6 s, and the first touch of files
# Git or a copy just wrote ~35-37 s (22 s of it the OS's first open of 3.7k new files).
# 120 s is ~3x that worst case. Callers may pass 1..MAX_SYNC_TIMEOUT; an exhausted
# budget answers `pending`, never a guess.
SYNC_TIMEOUT = 120
# The budget of a request that names none: a client from before budgets were sent waits
# 30 s for the reply, so it keeps the fixed 20 s budget it was built against. Current
# clients always send their budget (and wait budget + REPLY_MARGIN).
ABSENT_SYNC_TIMEOUT = 20
MAX_SYNC_TIMEOUT = 3600
# How much longer than a call's own budget a client waits for the reply.
REPLY_MARGIN = 30


def validate_sync_timeout(timeout):
    if type(timeout) not in (int, float) or not 1 <= timeout <= MAX_SYNC_TIMEOUT:
        raise ValueError(f'sync timeout must be 1..{MAX_SYNC_TIMEOUT} seconds')
    return timeout


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


@functools.lru_cache(maxsize=1)
def _build():
    """The installed package's build: its declared version plus a digest of every source
    file. The version alone repeats across dev reinstalls and a path repeats across
    in-place upgrades; the digest changes exactly when the code a process loads does."""
    package = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(package.rglob('*.py'), key=lambda item: item.relative_to(package).as_posix()):
        data = path.read_bytes()
        digest.update(path.relative_to(package).as_posix().encode('utf-8') + b'\x00')
        digest.update(len(data).to_bytes(8, 'big') + data)
    return _declared_version(package.parent), digest.hexdigest()[:32]


def _declared_version(install_root):
    try:
        return str(json.loads((install_root / '.claude-plugin/plugin.json').read_text(encoding='utf-8'))['version'])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', (install_root / 'pyproject.toml').read_text(encoding='utf-8'))
    except OSError:
        match = None
    return match.group(1) if match else 'unknown'


def build_identity() -> dict:
    version, digest = _build()
    return {'version': version, 'digest': digest}


def valid_build(value) -> bool:
    return (isinstance(value, dict) and set(value) == {'version', 'digest'} and
            all(isinstance(item, str) and 0 < len(item) <= 128 for item in value.values()))


def _version_key(build):
    match = re.match(r'\d+(?:\.\d+)*', build['version'])
    return tuple(int(part) for part in match.group().split('.')) if match else ()


def older(build, than) -> bool:
    """Whether `build` is an older release than `than`. Equal versions (a reinstall or dev
    build) are not ordered: the client that meets the other build is the newer one."""
    return _version_key(build) < _version_key(than)


def describe(build):
    return f"{build['version']}+{build['digest'][:12]}" if valid_build(build) else 'unknown (pre-build handshake)'


def check_handshake(value, identity, nonce, build, *, any_build=False):
    """Every command carries the sender's build and runs only on the same build. Only a
    retirement request (`any_build`) crosses builds; the coordinator then decides."""
    if isinstance(value, dict) and value == dict(identity, nonce=nonce, build=build):
        return
    if isinstance(value, dict) and {k: v for k, v in value.items() if k != 'build'} == dict(identity, nonce=nonce):
        if any_build and valid_build(value.get('build')):
            return
        raise HandshakeError(
            f"coordinator build {describe(build)} differs from the client build {describe(value.get('build'))}; "
            'no command ran. A newer client retires an idle older coordinator; an older client must restart '
            'its session (reload the plugin) to use the current build', may_have_committed=False)
    raise HandshakeError('coordinator root/store/schema/protocol/generation mismatch; reconnect explicitly')
