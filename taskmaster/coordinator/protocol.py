"""Small shared IPC contract; importing a client never imports the service."""
from contextlib import closing
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


class CoordinatorStopping(ServiceUnavailable):
    """Refused before admission because the owner is stopping; a client waits for its successor."""


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


def package_digest(package: Path) -> str:
    """A digest of every source file of `package`, with CRLF read as LF: a checkout's
    line-ending setting is not a different build."""
    digest = hashlib.sha256()
    for path in sorted(package.rglob('*.py'), key=lambda item: item.relative_to(package).as_posix()):
        data = path.read_bytes().replace(b'\r\n', b'\n')
        digest.update(path.relative_to(package).as_posix().encode('utf-8') + b'\x00')
        digest.update(len(data).to_bytes(8, 'big') + data)
    return digest.hexdigest()[:32]


def _compute_build():
    """The installed package's build: its declared version plus a digest of every source
    file. The version alone repeats across dev reinstalls and a path repeats across
    in-place upgrades; the digest changes exactly when the code a process loads does."""
    package = Path(__file__).resolve().parents[1]
    return declared_version(package.parent), package_digest(package)


def _build():
    return _BUILD


def declared_version(install_root: Path) -> str:
    """The release an install declares, in any of its distribution layouts (Claude plugin,
    Codex plugin snapshot, generated distribution, source checkout)."""
    for manifest in ('.claude-plugin/plugin.json', '.codex-plugin/plugin.json', '.taskmaster-distribution.json'):
        try:
            version = json.loads((install_root / manifest).read_text(encoding='utf-8'))['version']
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if isinstance(version, str) and version.strip():
            return version.strip()
    try:
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', (install_root / 'pyproject.toml').read_text(encoding='utf-8'))
    except OSError:
        match = None
    return match.group(1) if match else 'unknown'


def build_identity() -> dict:
    version, digest = _build()
    return {'version': version, 'digest': digest}


# Taken at import, not at the first Client(): a process reports the code it loaded, even if
# the files are upgraded in place while it runs.
_BUILD = _compute_build()


def valid_build(value) -> bool:
    return (isinstance(value, dict) and set(value) == {'version', 'digest'} and
            all(isinstance(item, str) and 0 < len(item) <= 128 for item in value.values()))


def same_build(first, second) -> bool:
    """The code is the identity: equal digests are one build whatever the version reads."""
    return valid_build(first) and valid_build(second) and first['digest'] == second['digest']


_SEMVER = re.compile(r'[vV]?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?')


def _semver(version):
    match = _SEMVER.fullmatch(version.strip())
    if match is None:
        return None
    core = [int(part) for part in match.group(1).split('.')]
    while len(core) > 1 and core[-1] == 0:
        core.pop()  # 7.0 == 7.0.0
    pre = match.group(2)
    # A final release sorts after all its pre-releases; identifiers compare per SemVer.
    return (tuple(core), (1,) if pre is None else
            (0, tuple((0, int(part), '') if part.isdigit() else (1, 0, part) for part in pre.split('.'))))


def compare_versions(first, second):
    """-1, 0 or 1 by SemVer precedence (build metadata ignored); None when either is unparseable."""
    left, right = _semver(first['version']), _semver(second['version'])
    if left is None or right is None:
        return None
    return (left > right) - (left < right)


def older(build, than) -> bool:
    """Whether `build` is a strictly older release than `than`."""
    return compare_versions(build, than) == -1


def describe(build):
    return f"{build['version']}+{build['digest'][:12]}" if valid_build(build) else 'unknown (pre-build handshake)'


def check_handshake(value, identity, nonce, build, *, any_build=False):
    """Every command carries the sender's build and runs only on the same build. Only a
    retirement request (`any_build`) crosses builds; the coordinator then decides."""
    if not isinstance(value, dict) or {k: v for k, v in value.items() if k != 'build'} != dict(identity, nonce=nonce):
        raise HandshakeError('coordinator root/store/schema/protocol/generation mismatch; reconnect explicitly')
    if same_build(value.get('build'), build) or (any_build and valid_build(value.get('build'))):
        return
    raise HandshakeError(
        f"coordinator build {describe(build)} differs from the client build {describe(value.get('build'))}; "
        'the request was refused. A newer client retires an idle older coordinator; an older client must restart '
        'its session (reload the plugin) to use the current build', may_have_committed=False)
