<!-- User intent: the executable, test-first plan for team-server-002. The operator mints, rotates and
     revokes one API key per teammate on the host; keys are stored only as owner-only hashes; and every
     served request resolves key -> handle -> profile in the coordinator, never from host git or the client. -->

# team-server-002: per-person API keys (plan, 2026-10-31)

**Task.** `team-server-002` (server mode, addendum §11 task 2).
**Design.** `docs/specs/2026-10-31-team-server-002-principals-design.md`. Its decisions D1 to D10 are
binding here. The source is the addendum §3 and §13, the 001 design §5, and rev 3.3 §4.
**Worktree.** `C:\Users\gruku\Files\Claude\taskmaster\.worktrees\team-server-002`, branch
`feature/team-server-002`, base `f691e7d` (team-server-001 plus team-identity-002) and this plan's
commit. **Before Step 3**, rebase onto team-server-001's final reviewed tip if it has moved (design R2).
**Executor.** One engineer, test-first. For each step:

1. Write the step's tests.
2. Run them and watch them fail for the stated reason.
3. Implement.
4. Run the step's tests and the regression subset (§5).
5. Commit with the step's message.

Steps 0 to 7 are ordered by dependency; run them in order. Every decision is already made: if something
here seems wrong, stop and report it rather than redesigning.

**Done when:**

- `taskmaster serve add-user <handle>` mints a key, prints it once, and stores only its SHA-256 in an
  owner-only `local/serve/principals.json`. `rotate-user`, `revoke-user` and `list-users` work, with
  the daemon running or stopped.
- A request with an unknown, malformed or revoked key gets 401. A revoked key is refused on an
  already-open MCP session too, and that session is ended within one sweep.
- Each served write's actor is `{key's handle, agent, clone_id: key_id}`. The client's claims
  are ignored. In team mode, the key's profile is created on first write (or an existing one is
  reused), and host git is never read.
- The operator bootstrap token works only from the host.
- No key or secret appears in any log, error, response or file other than the CLI's one stdout line.

**Out of scope:** the design's §1 table (003 sessions and `caller_scope`, 004 receipt scoping, 005
allowlist, 006 hardening and viewer login, 009, 016).
**Shared files touched minimally:** `taskmaster/coordinator/service.py` (two one-line edits),
`identity_gate.py` (one parameter), `client.py` (one hook), `team_identity.py` (one reserved word).

---

## 1. Files

Create:

| File | Header (`# User intent: …`, first line) |
|---|---|
| `taskmaster/serve/principals.py` | `per-person API keys: minted once, stored only as SHA-256 in an owner-only file on the host, rotated or revoked by the operator, and read by the daemon without a restart.` |
| `taskmaster/serve/identity.py` | `in server mode the coordinator turns the authenticated key into the actor and the person's profile; nothing the client says and nothing in host git decides who wrote.` |
| `tests/test_serve_principals.py` | `per-person keys are minted once, stored only as owner-only hashes, and rotated or revoked by the operator on the host.` |
| `tests/test_serve_keys.py` | `a presented key resolves to its person by id with one constant-time compare, revocation applies from the next request, and the secret never reaches the access token.` |
| `tests/test_serve_app_keys.py` | `the bootstrap token works only on the host, a session belongs to the key that opened it, and revoking a key ends its open sessions.` |
| `tests/test_serve_identity.py` | `the coordinator, not the caller, turns the authenticated key into the actor and the profile, and never consults the host's git identity in server mode.` |
| `tests/test_serve_principals_e2e.py` | `over real HTTP, two teammates' keys write as two people, a revoked key stops working on an open session, and no key ever appears in logs, errors or files besides the client's.` |

Modify:

| File | Change |
|---|---|
| `pyproject.toml`, the four PEP 723 headers (`backlog_server.py:3`, `taskmaster/backlog_server.py:3`, `taskmaster_cli.py:3`, `hooks/merge_recorder_stamp.py:3`), `uv.lock` | `mcp>=1.24,<2` → `mcp>=1.28,<2` (Step 0; design R1) |
| `taskmaster/team_identity.py:23` | `"operator"` added to `RESERVED_HANDLES` (Step 1) |
| `taskmaster/serve/auth.py` | `Principal.key_id`; `KeyAuthenticator`; verifier `subject` (stable `key_id`); `current_key_id`; the shared `is_host_local` (Step 2) |
| `taskmaster/serve/app.py` | host-only operator token; session-owner check; revocation sweep (Step 3) |
| `taskmaster/coordinator/client.py` | `ORIGIN_PROVIDER` hook in `Client.execute` (Step 4) |
| `taskmaster/coordinator/service.py` | `Coordinator._origin(message)`; `admit(..., work.origin)` (Step 4) |
| `taskmaster/coordinator/identity_gate.py` | `admit(self, connection, request, origin=LOCAL)` (Step 4) |
| `taskmaster/serve/daemon.py` | `ServeCoordinator(registry=)`, `_origin`, daemon wiring (Steps 4 and 6) |
| `taskmaster/serve/cli.py` | the four user commands and their usage text (Step 7) |
| `tests/test_serve_auth.py`, `tests/test_serve_app.py`, `tests/test_serve_daemon.py` | expectations for the new `Principal` and `AccessToken` shape (Steps 2, 3 and 6; listed in each step) |
| `tests/test_packaging.py` | `serve add-user --help` never imports the server (Step 7) |

---

## 2. API (exact)

### 2.1 `taskmaster/serve/principals.py`

```python
PRINCIPALS_FILE = "principals.json"
LOCK_FILE = "principals.lock"
KEY_RE = re.compile(r"tm_u_([0-9a-f]{16})_([A-Za-z0-9_-]{43})")   # always fullmatch
KEY_ID_RE = re.compile(r"[0-9a-f]{16}")
ROLES = ("member", "operator")
FILE_VERSION = 1
LOCK_SECONDS = 10.0
REPLACE_ATTEMPTS, REPLACE_PAUSE = 20, 0.05
LOG = logging.getLogger("taskmaster.serve")

class PrincipalsRefused(ServeRefused): ...       # exit 2
class PrincipalsBusy(ServeUnavailable): ...      # exit 69

@dataclass(frozen=True)
class Record:
    key_id: str; handle: str; role: str; name: str; emails: tuple[str, ...]; created: str
    sha256: str | None; revoked: str | None; revoked_reason: str | None
    @property
    def active(self) -> bool                     # revoked is None
    @property
    def digest(self) -> bytes | None             # bytes.fromhex(sha256) when active
    def public(self) -> dict                     # every field except sha256; emails as a list

def principals_path(root: Path) -> Path          # auth.serve_directory(root) / PRINCIPALS_FILE
def mint_key(taken: set[str]) -> tuple[str, str]
    # key_id = secrets.token_hex(8), redrawn while in taken; key = f"tm_u_{key_id}_{secrets.token_urlsafe(32)}".
    # Asserts KEY_RE.fullmatch(key). Returns (key, key_id).
def key_digest(key: str) -> str                  # hashlib.sha256(key.encode()).hexdigest()
def check_person(handle, name, emails, role) -> None
    # handle: HANDLE_RE.fullmatch, not in RESERVED_HANDLES or DEGENERATE_NAMES; role in ROLES;
    # profiles.validate("profile.create", {"handle", "name", "emails", "created": "2000-01-01T00:00:00Z"}).
    # Any failure: PrincipalsRefused(<the validator's text>), which never contains a key.
def parse(raw: bytes) -> dict[str, Record]
    # json; {"version": 1, "keys": [...]}; every design §4 rule; insertion-ordered by file order.
    # Failure: PrincipalsRefused(f"principals file is malformed: {reason}"), where reason names the
    # rule and at most a key_id or handle, never a hash.
def load(path: Path) -> dict[str, Record]
    # Missing: {}. Otherwise ownership.verify_private(path); a PermissionError becomes
    # PrincipalsRefused(f"{path} must be owner-only (chmod 600 on Linux): {exc}"). Then parse(path.read_bytes()).
def write(path: Path, records: dict[str, Record]) -> None
    # temp = path.with_name(f"{PRINCIPALS_FILE}.{secrets.token_hex(8)}.tmp"); os.open(O_CREAT|O_EXCL|O_WRONLY|O_BINARY, 0o600);
    # json.dumps({"version": 1, "keys": [...]}, indent=1) + "\n"; fsync; os.replace(temp, path), retried
    # REPLACE_ATTEMPTS times on PermissionError with REPLACE_PAUSE sleeps, then PrincipalsBusy.
    # The temp is unlinked in finally.
@contextmanager
def locked(root: Path)
    # os.open(serve_directory(root)/LOCK_FILE, O_CREAT|O_RDWR, 0o600); write b"\0" when empty;
    # os.lseek(fd, 0, os.SEEK_SET) before every lock attempt and before unlocking (msvcrt locks from
    # the current position; the same order as ownership.py:167-172); then msvcrt.locking(LK_NBLCK, 1) / fcntl.flock(LOCK_EX|LOCK_NB), retried every 0.05 s until LOCK_SECONDS,
    # then PrincipalsBusy("another add-user, rotate-user or revoke-user is running"). Unlock and close in finally.
def write_key_file(path: Path, key: str) -> None
    # os.open(path, O_CREAT|O_EXCL|O_WRONLY|O_BINARY, 0o600) (FileExistsError -> PrincipalsRefused(
    # f"{path} already exists")); write key + "\n"; fsync; on Windows ownership._windows_private(path);
    # then ownership.verify_private(path). Any failure after creation unlinks the file and raises
    # PrincipalsRefused(f"could not write the key file {path}: {exc}"); the message never holds the key.
def add_user(root, handle, *, name=None, emails=(), role="member", key_file=None, now=None) -> tuple[str, Record, bool]
    # check_person(handle, name or handle, list(emails), role). Under locked(root): records = load(path);
    # an active record for handle -> PrincipalsRefused(f"{handle} already has a key ({key_id}); use rotate-user to replace it").
    # reinstated = any revoked record for handle. mint_key(set(records)); when key_file: write_key_file(key_file, key)
    # first, so a failed key file adds no record; append Record(sha256=key_digest(key), created=now or utcnow
    # in CREATED_FORMAT, revoked=None, revoked_reason=None); write. Returns (key, record, reinstated).
def rotate_user(root, handle, *, key_file=None, now=None) -> tuple[str, Record, Record]
    # Under the lock: the active record for handle, else PrincipalsRefused(f"{handle} has no active key").
    # When key_file: write_key_file first (a failure leaves the old key active). Replace the record with
    # revoked=now, revoked_reason="rotated", sha256=None; append a new record copying handle, role, name,
    # emails; one write. Returns (key, new, old).
def revoke_user(root, handle, *, now=None) -> Record
    # Under the lock: the active record, else PrincipalsRefused; set revoked=now, revoked_reason="revoked", sha256=None; write.
def list_users(root) -> list[Record]             # load(principals_path(root)).values(), file order
@dataclass(frozen=True)
class StoreCheck:
    profile: str             # "live" | "deleted" | "absent" | "unknown"
    name: str | None         # the live profile's name
    team_mode: bool | None   # None when the store could not be read
def inspect_store(root, handle) -> StoreCheck
    # with contextlib.closing(protocol.connect(root, readonly=True)) as connection: read
    # entity_core(kind='profile', public_id=handle) (name from the snapshot fields), and
    # team_mode = identity_gate.team_mode_enabled(root, project_doc=identity_gate._project_doc(connection)).
    # Any exception -> StoreCheck("unknown", None, None). The connection is always closed; never writes.

class RegistryUnavailable(Exception): ...        # a transient read error; never cached (design D6, I1)

class PrincipalRegistry:
    """The daemon's read-only view: reloaded when the file changes; empty (fail closed) when the file's
    content or permissions are bad; a transient I/O error refuses one call and is retried on the next."""
    def __init__(self, path: Path | None): ...   # None: always empty (ServeCoordinator default, tests)
    def refresh(self) -> None
        # Under a threading.Lock:
        # 1. try key = (st_mtime_ns, st_size, st_ino) of path; FileNotFoundError -> key = None.
        #    Any other OSError -> raise RegistryUnavailable (nothing stored).
        # 2. key == self._key -> return (cache hit).
        # 3. key None -> records = {}. Otherwise try records = load(path):
        #    - PrincipalsRefused (bad content, or verify_private failed: the file itself is wrong) ->
        #      records = {}, LOG.error("principals file unusable, refusing every member key: %s", exc)
        #      once per key; this IS cached on the key;
        #    - any other OSError (EMFILE, a Windows sharing violation from read_bytes, EIO) ->
        #      LOG.warning("principals file could not be read (%s); refusing this request, retrying on
        #      the next", type(exc).__name__) at most once per minute per exception type, then raise
        #      RegistryUnavailable. self._key and self._records stay as they were.
        # 4. Only now: self._key, self._records = key, records. On a reload after an earlier load:
        #    LOG.info("principals reloaded: %d active keys; no longer active: %s", ...).
        # load() maps only verify_private's PermissionError to PrincipalsRefused; a PermissionError
        # from reading the bytes stays an OSError (transient).
    def lookup(self, key_id: str) -> Record | None
        # refresh() (RegistryUnavailable propagates to the caller, which refuses this one request:
        # KeyAuthenticator -> 401, ServeActorSource -> IdentityRefused(UNAVAILABLE)), then
        # self._records.get(key_id).
    def active_ids(self) -> frozenset[str]
        # refresh() (RegistryUnavailable propagates: the sweep skips the round), then ids of active records.
```

The same pattern in 001's `OperatorTokenAuthenticator` (`auth.py:121-129`, which stores the stat key
before reading) is out of scope here; it is noted for 001's follow-up (design D6).

`load` is the strict reader for the CLI and daemon start. `PrincipalRegistry` is the forgiving
reader for a running daemon.

### 2.2 `taskmaster/serve/auth.py` (changes)

```python
OPERATOR_KEY_ID = "operator"

@dataclass(frozen=True)
class Principal:
    handle: str
    role: str
    key_id: str
OPERATOR = Principal("operator", "operator", OPERATOR_KEY_ID)

_DUMMY_DIGEST = hashlib.sha256(b"taskmaster: no such key").digest()

class KeyAuthenticator:
    def __init__(self, operator: OperatorTokenAuthenticator, registry: PrincipalRegistry): ...
    def authenticate(self, token: str) -> Principal | None
        # match = KEY_RE.fullmatch(token or ""); no match -> self.operator.authenticate(token).
        # try record = registry.lookup(key_id) except RegistryUnavailable: record, unavailable = None, True.
        # digest = record.digest if record and record.active else _DUMMY_DIGEST.
        # ok = hmac.compare_digest(hashlib.sha256(token.encode()).digest(), digest)   # exactly one call, always
        # ok and record and record.active -> Principal(record.handle, record.role, record.key_id), plus
        #   LOG.info("key %s (%s) authenticated", key_id, handle) the first time per key_id in this instance.
        # Otherwise LOG.warning("refused key %s: %s", key_id, reason) with reason "unknown", "revoked",
        #   "wrong secret" or "key list unavailable"; return None.

class ServeTokenVerifier(TokenVerifier):
    async def verify_token(self, token) -> AccessToken | None
        # AccessToken(token=f"key:{p.key_id}", client_id=p.handle, subject=p.key_id, scopes=[], expires_at=None,
        #   claims={"taskmaster_principal": p.handle, "taskmaster_role": p.role, "taskmaster_key_id": p.key_id})

def principal_from_access_token(access_token) -> Principal | None
    # Three claims -> Principal. Only handle and role, with handle == "operator" -> OPERATOR. Otherwise None.
def current_key_id() -> str | None
    # try fastmcp.server.dependencies.get_http_request() except RuntimeError: return None.
    # principal = current_principal(); None -> raise PermissionError("authenticated principal missing").
    # Return principal.key_id.

VIEWER_KEY_ID: ContextVar[str | None] = ContextVar("taskmaster_viewer_key_id", default=None)

def request_key_id() -> str | None
    # The only callable ever installed as client.ORIGIN_PROVIDER (by the daemon):
    # return current_key_id() or VIEWER_KEY_ID.get()

@contextmanager
def viewer_principal(key_id: str)
    # Validates key_id (re.fullmatch(r"operator|[0-9a-f]{16}"), else ValueError); token = VIEWER_KEY_ID.set(key_id);
    # yield; finally VIEWER_KEY_ID.reset(token). For 006's viewer handlers only: they wrap each writing request in it
    # and never assign ORIGIN_PROVIDER or any process-wide value (design D8).

FORWARDING_HEADERS = (b"x-forwarded-for", b"forwarded", b"x-real-ip")
HOST_LOCAL_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})

def is_host_local(scope) -> bool
    # The one shared host-local check (design D7, orchestrator ruling a). Pure; reads no body. Callers:
    # RequirePrincipalMiddleware's bootstrap-token rule, 006's viewer login, task 5's admin check.
    # Never re-implement it elsewhere.
    # 1. client = scope.get("client"); ipaddress.ip_address(client[0]).is_loopback (False on a missing
    #    client or any parse error);
    # 2. no header name (lower-cased bytes) in FORWARDING_HEADERS;
    # 3. exactly one Host header, and its name ("[::1]:7470" -> "::1", "127.0.0.1:7470" -> "127.0.0.1",
    #    "localhost" -> "localhost", compared lower-cased) is in HOST_LOCAL_NAMES. A missing Host -> False.
    # All three must hold.
```

The `subject` contract (design §6, orchestrator ruling c): `verify_token` sets
`subject = principal.key_id` verbatim (`"operator"`, or 16 lowercase hex). The value is never
prefixed, re-cased or hashed. 006's rate limits and in-flight caps key on it.

**Logging rule.** Every new log line that includes request-derived text passes it through 001's
`serve.app.log_safe`: the peer, method, path, the handle shown with a key id, and exception type
names. `auth.py` and `principals.py` can't import `app` at module level, because `app` imports
`auth`. They import `log_safe` inside the logging function, or from wherever 001 finally defines
it if that module is importable without a cycle. Never copy it.

`OperatorTokenAuthenticator` is unchanged.

### 2.3 `taskmaster/serve/app.py` (changes)

```python
REVOCATION_SWEEP_SECONDS = 2.0
OPERATOR_REMOTE_REFUSED = ("the operator bootstrap token works only on the host; "
                           "use a key from taskmaster serve add-user")

class RequirePrincipalMiddleware:
    # Unchanged for unauthenticated requests. For an AuthenticatedUser whose access token has
    # subject == OPERATOR_KEY_ID and not auth.is_host_local(scope):
    #   LOG.warning("refused operator token from %s: not host-local", log_safe(peer)).
    #   peer is scope["client"][0], which uvicorn took from the caller-controlled X-Forwarded-For.
    #   403 JSON {"error": OPERATOR_REMOTE_REFUSED}, Cache-Control: no-store, sent with _no_receive.

def end_revoked_sessions(manager, registry) -> list[str]
    # active = registry.active_ids(). For sid, owner in list(manager._session_owners.items()):
    #   subject = owner.subject; skip None and OPERATOR_KEY_ID; if subject not in active:
    #   transport = manager._server_instances.get(sid); if transport and transport.idle_scope is not None:
    #     transport.idle_scope.deadline = anyio.current_time(); ended.append(sid);
    #     LOG.info("ended session %s of revoked key %s", log_safe(sid[:8]), log_safe(subject))
    # Returns ended.

def build_app(server, authenticator, *, path=..., allowed_hosts=(), middleware=(),
              session_idle_seconds=..., registry=None, sweep_seconds=REVOCATION_SWEEP_SECONDS)
    # As in 001. In the wrapped lifespan, after the session manager exists, check_session_internals(manager):
    #   isinstance(getattr(manager, "_session_owners", None), dict), isinstance(getattr(manager,
    #   "_server_instances", None), dict), and "idle_scope" in StreamableHTTPServerTransport.__init__.__code__.co_names
    #   (mcp/server/streamable_http.py:192); any failure raises RuntimeError(INTERNALS_CHANGED).
    #   if registry is not None: start a task looping `await anyio.sleep(sweep_seconds);
    #   end_revoked_sessions(manager, registry)`. RegistryUnavailable skips the round silently (the registry
    #   already warned; a transient read error must never end sessions, design D6). Any other exception is
    #   logged at ERROR and never raised. Cancel the task on lifespan exit.
```

`INTERNALS_CHANGED`'s text gains `, and sessions must record their owner and idle scope (mcp>=1.28)`.

### 2.4 `taskmaster/coordinator/client.py`, `service.py`, `identity_gate.py`

```python
# client.py, next to AUTOSTART_ALLOWED:
ORIGIN_PROVIDER = None      # Callable[[], str | None]; only the serve daemon assigns it (auth.request_key_id)

# Client.execute: after validation, before the try:
provider = ORIGIN_PROVIDER
principal = provider() if provider is not None else None
extra = {} if principal is None else {'origin': {'principal': principal}}
... self.call('execute', envelope=request, visibility=self.visibility, **extra)

# service.py, Coordinator:
def _origin(self, message):
    return LOCAL                      # session coordinators ignore any origin field
# dispatch 'execute':  receipt = self.submit(message.get('envelope'), origin=self._origin(message)).result()
# _writer:              if self.team_identity.admit(connection, work.request, work.origin):

# identity_gate.py:
from .actor_resolver import LOCAL
def admit(self, connection, request, origin=LOCAL) -> bool:   # body unchanged; origin unused here
```

The provider is called once per `execute`, before `call`'s retry loop, so retries send identical
arguments.

### 2.5 `taskmaster/serve/identity.py`

```python
REVOKED = "Refused: this API key was revoked. Nothing was written."
NOT_A_PERSON = ("Refused: team mode attributes every shared change to a person, and the operator "
                "bootstrap token and host-local callers are not one. Use your own key from "
                "`taskmaster serve add-user`. Nothing was written.")
UNAVAILABLE = "Refused: the server could not read its key list just now; retry. Nothing was written."
OPERATOR_ACTOR = ("operator", "operator")

class ServeActorSource:
    def __init__(self, registry: PrincipalRegistry): ...
    def record(self, origin: Origin) -> Record | None
        # origin.principal in (None, OPERATOR_KEY_ID) -> None.
        # try r = registry.lookup(origin.principal) except RegistryUnavailable: raise IdentityRefused(UNAVAILABLE).
        # r is None or not r.active -> raise IdentityRefused(REVOKED).
    def identify(self, connection, request, origin) -> tuple[str, str]
        # r = self.record(origin); None -> OPERATOR_ACTOR; else (r.handle, r.key_id).

class ServeIdentityGate(IdentityGate):
    def __init__(self, root, *, store_id, source: ServeActorSource, checkpoint=lambda stage: None): ...
    def admit(self, connection, request, origin=LOCAL) -> bool
        # not is_attribution_bearing(...) -> False; not self._team_mode(connection) -> False;
        # self._replayed(connection, request) -> False.
        # r = self.source.record(origin)            # may raise IdentityRefused(REVOKED)
        # r is None -> raise IdentityRefused(NOT_A_PERSON).
        # row = entity_core(kind='profile', public_id=r.handle):
        #   live -> self._upgrade(connection); return False
        #   deleted -> raise refused_profile(f"profile {r.handle!r} was deleted from this backlog")
        #   absent -> arguments = {"handle": r.handle, "name": r.name, "emails": list(r.emails), "created": r.created};
        #             _check_profile(arguments); self._upgrade(connection);
        #             request_id = self._request_id(connection, SimpleNamespace(clone_id=r.key_id, handle=r.handle));
        #             commands.execute(connection, {"protocol": 2, "store_id": self.store_id, "caller_scope": CALLER_SCOPE,
        #               "request_id": request_id, "operation": "profile.create", "arguments": arguments,
        #               "expected_revisions": [], "actor": actor.make(r.handle, actor.INTERNAL_AGENT, r.key_id)});
        #             return True
        # Never calls read_git_identity, write_identity, disk_profiles or socket.gethostname.
```

### 2.6 `taskmaster/serve/daemon.py` (changes)

```python
class ServeCoordinator(Coordinator):
    def __init__(self, root, *, registry=None, **kwargs):
        registry = registry if registry is not None else PrincipalRegistry(None)
        source = ServeActorSource(registry)
        super().__init__(root, actor_source=source, **kwargs)
        self.team_identity = ServeIdentityGate(self.root, store_id=self.identity["store_id"], source=source,
                                               checkpoint=lambda stage: self.checkpoint(stage))
    def _origin(self, message):
        value = message.get("origin")
        if value is None: return LOCAL
        # exactly {"principal": str} with re.fullmatch(r"operator|[0-9a-f]{16}", s), else
        # ValueError("invalid origin")
        return Origin(principal=value["principal"])
    # retire and dispatch: unchanged from 001
```

`ServeDaemon._start` (new or changed lines only, in order):

- After `ensure_operator_token`: `ppath = principals_path(root)`; `records = load(ppath)` (strict:
  `PrincipalsRefused` → exit 2); `registry = PrincipalRegistry(ppath)`;
  `authenticator = self.authenticator or KeyAuthenticator(OperatorTokenAuthenticator(token_path(root)),
  registry)`; `LOG.info("principals: %d active keys", ...)`.
- Next to the autostart pin: `self._previous_origin = client.ORIGIN_PROVIDER`;
  `client.ORIGIN_PROVIDER = request_key_id`. `_teardown` restores it the same way. No other code
  assigns it (design D8).
- `self.coordinator = ServeCoordinator(root, registry=registry)`.
- After the coordinator starts, in `with contextlib.closing(protocol.connect(root, readonly=True)) as
  connection:` (the connection is always closed):
  - unless `identity_gate.team_mode_enabled(root, project_doc=identity_gate._project_doc(connection))`
    (read through the module, so tests' `team_on` monkeypatch applies), `LOG.warning("team mode is off
    for %s: writes are attributed in receipts only; turn on team mode to record people on every event",
    root)`;
  - when a live `entity_core` row `kind='profile' AND public_id='operator'` exists,
    `LOG.warning("profile 'operator' predates the reserved handle; bootstrap-token writes are not that
    person's")` (design D7).
  An exception here is logged at WARNING and never fails the start.
- `build_app(..., registry=registry)`.
- The ready line gains `"principals_file": str(ppath)`.

### 2.7 `taskmaster/serve/cli.py` (changes)

`main` dispatches `argv[0]` in `{"token", "add-user", "rotate-user", "revoke-user", "list-users"}` to
`token_main`, `add_user_main`, `rotate_user_main`, `revoke_user_main` and `list_users_main`. Each
takes `--root` (required, resolved like `token_main`: it must exist and hold `.taskmaster/local`,
else exit 2). `add-user`, `rotate-user` and `revoke-user` take a positional `handle`; `add-user`
also takes `--name`, `--email` (`action="append"`, default `[]`) and `--role`
(`choices=ROLES`, default `member`). `add-user` and `rotate-user` take `--key-file PATH` (design §5,
review M4). `list-users` takes `--json`. No command has an option that *reads* a key.

- `add-user`:
  - Call `check = inspect_store(root, handle)`:
    - `check.profile == "deleted"` → stderr `profile {handle} was deleted from this backlog; choose
      another handle`, exit 2;
    - `"live"` → stderr `uses the existing profile {handle} ({name})`, plus `--name/--email are
      ignored for an existing profile` when either was given;
    - `"unknown"` → stderr `could not read the store to check for an existing profile; the server
      checks on first write`.
  - `check.team_mode is False` → stderr `team mode is off for this project: {handle}'s writes will
    be attributed in receipts only, and no profile will be created, until team mode is on` (warning
    only; review M3).
  - Then call `add_user(..., key_file=args.key_file)`.
    - Without `--key-file`: print the key alone on stdout and the show-once text (design §3) on
      stderr.
    - With it: stdout `key for {handle} written to {path} (owner-only)`, and the key is printed
      nowhere.
    - When reinstated, also stderr `reinstating {handle}; earlier keys stay revoked`.
- `rotate-user`:
  - Without `--key-file`: the key on stdout, plus `rotated {handle}: {old.key_id} ->
    {new.key_id}` and the show-once text on stderr.
  - With `--key-file`: only the written-to line on stdout, and the `rotated` line on stderr.
- `revoke-user`: `revoked {handle} ({key_id})` on stdout.
- `list-users`: per record, `f"{handle:<32} {role:<8} {key_id} {created} {state}"` where state is
  `active`, `revoked <revoked>` or `rotated <revoked>`. `--json` gives
  `json.dumps([r.public() for r in records], indent=1)`. `(no users)` when there are none.
- `ServeRefused` (and its subclasses) → `taskmaster serve: {exc}` on stderr and `exc.exit_code`.

The `.principals` and `.auth` imports stay inside the functions, so `--help` imports neither the
server nor the store.

---

## 3. TDD steps

New test files start with their `# User intent:` header (§1). Use `tmp_path` and the twins
fixtures, never the live `.taskmaster/`. Native roots come from `tests/test_native_service_build.py`
(`root`, `request`); the in-process daemon fixture is `served` in `tests/test_serve_daemon.py:34`
(import it as that file imports `root`). `GitStub`, `_value` and `_schema` come from
`tests/test_native_service_identity.py`. The `team_on` pattern is
`monkeypatch.setattr(identity_gate, "team_mode_enabled", lambda root, *, project_doc=None: True)`.

### Step 0: raise the `mcp` floor (design R1)

- `pyproject.toml` `dependencies`: `mcp>=1.24,<2` becomes `mcp>=1.28,<2`. Make the same change on line 3 of
  the four PEP 723 headers, byte-identical (`tests/test_packaging.py:55`).
- Run `uv lock`, then `uv lock --check`. The resolved mcp stays 1.28.1 and only the `taskmaster`
  package's metadata changes. If anything else moves, or the network is needed and unreachable,
  stop and report.

Check: `tests/test_packaging.py -k "script_headers or third_party or lock"` passes.

Commit: `build: require mcp>=1.28, whose sessions are bound to the credential that opened them`.

### Step 1: reserve `operator`; the principals file (`team_identity.py`, `serve/principals.py`)

Tests in `tests/test_serve_principals.py`. The fixture `project` creates
`tmp_path/.taskmaster/local` and returns `tmp_path`.

- `test_operator_is_a_reserved_handle`: `"operator" in RESERVED_HANDLES`;
  `profiles.validate("profile.create", {...handle "operator"...})` raises `ValueError`;
  `choose_handle("operator", "", {})[0] != "operator"`.
- `test_minted_key_has_the_documented_shape`: for 50 `mint_key(set())` calls, `KEY_RE.fullmatch(key)`
  holds, `len(key) == 65`, group 1 is the returned `key_id`, and every key is distinct.
- `test_mint_redraws_a_taken_id`: monkeypatch `principals.secrets.token_hex` to return `"a"*16`, then
  `"b"*16`. `mint_key({"a"*16})` returns id `"b"*16`.
- `test_add_user_stores_only_the_hash`:
  - `key, record, reinstated = add_user(project, "ann", name="Ann Lee", emails=["ann@example.com"])`.
  - `reinstated is False`, `record.active`, `record.sha256 == hashlib.sha256(key.encode()).hexdigest()`.
  - The file bytes contain neither `key` nor its secret part (group 2), but do contain the hash.
  - `load(path)[record.key_id] == record`.
- `test_principals_file_is_never_readable_by_others`: after each of `add_user`, `rotate_user` and
  `revoke_user`:
  - `ownership.verify_private(path)` passes (all OSes);
  - on POSIX, `path.stat().st_mode & 0o777 == 0o600`, the lock file is `0o600` and the directory is
    `0o700`;
  - no `*.tmp` is left behind.
  - Also with `os.umask(0o022)` set around the calls (POSIX; restored in `finally`): still `0o600`.
- `test_loose_permissions_refuse` (POSIX only): `chmod 0o644`. `load` raises `PrincipalsRefused`
  matching `owner-only`, with `exit_code == 2`, and `add_user(project, "bob")` refuses the same way
  without writing.
- `test_unverifiable_permissions_refuse` (all OSes): monkeypatch `principals.ownership.verify_private`
  to raise `PermissionError("x")`. `load` raises `PrincipalsRefused` matching `owner-only`.
- `test_a_link_in_place_of_the_file_refuses` (skip when `os.symlink` raises `OSError`, as on
  Windows without the privilege): symlink `principals.json` to a 0600 file elsewhere; `load` refuses.
- `test_malformed_file_refuses` (parametrized, each written with 0600 through `principals.write`
  or raw bytes): not JSON; `version` 2; a bad `key_id`; a duplicate `key_id`; a reserved handle;
  role `admin`; an active record with `sha256` null; a revoked record with a hash; two active
  records for one handle. Each raises `PrincipalsRefused` matching `malformed`, and no message
  contains a 64-hex string.
- `test_one_active_key_per_handle`: a second `add_user(project, "ann")` raises matching
  `rotate-user`, and the file is unchanged byte for byte.
- `test_bad_people_refuse` (parametrized): handle `Ann`, `a_b`, `taskmaster`, `operator`, `admin`;
  name `"your name"`; email `not-an-email`; role `root`. Each raises `PrincipalsRefused`, and the
  file isn't created.
- `test_rotate_replaces_the_key_in_one_write`:
  - `key2, new, old = rotate_user(project, "ann")`.
  - `old.revoked_reason == "rotated"` and `old.sha256 is None`.
  - `new` copies handle, role, name and emails, and has a new `key_id`.
  - Neither key's text is in the file, and exactly one record for `ann` is active.
- `test_revoke_keeps_the_record_without_its_hash`: `revoke_user` returns the record with
  `revoked_reason == "revoked"`, and `list_users` still lists it.
- `test_revoke_and_rotate_need_an_active_key`: on an unknown or revoked handle, both raise
  `PrincipalsRefused`.
- `test_add_after_revoke_reinstates`: `add_user` returns `reinstated is True` and a new `key_id`,
  and the old record stays revoked.
- `test_concurrent_mutations_serialise`: 8 threads behind a `threading.Barrier(8)` each
  `add_user(project, f"user{i}")`. All 8 records load, and the file parses.
- `test_held_lock_times_out_with_exit_69`: monkeypatch `LOCK_SECONDS = 0.2`. Hold the lock in the
  test (enter `locked(project)` on another thread and wait on an event). `add_user` raises
  `PrincipalsBusy`, with `exit_code == 69`.
- `test_replace_contention_is_retried`: monkeypatch `principals.os.replace` to raise `PermissionError`
  twice, then call through. `add_user` succeeds. Always raising gives `PrincipalsBusy`.
- `test_inspect_store`:
  - `StoreCheck("absent", None, False)` on the native `root` fixture for `ann`;
  - with `team_on`, `team_mode is True`;
  - `StoreCheck("unknown", None, None)` for a `tmp_path` project without `store.db`.
  - Spy on `protocol.connect`'s returned connection and assert it was closed in every case.
- `test_key_file_is_owner_only_and_exclusive`:
  - `add_user(project, "ann", key_file=kf)` writes the key and a newline to `kf`, and
    `ownership.verify_private(kf)` passes. On POSIX the mode is `0o600`, also under
    `os.umask(0o022)`.
  - A second call to an existing `kf` raises `PrincipalsRefused` matching `already exists`, adds no
    record, and leaves `kf` unchanged.
  - Monkeypatch `principals.os.fsync` to raise for the key file. No record is added, `kf` is
    removed, and the error text doesn't contain the key.
  - `rotate_user(project, "ann", key_file=kf2)` that fails the same way leaves the old key active.
- `test_lock_rewinds_before_locking`: spy on `principals.os.lseek`. `locked(project)` calls
  `lseek(fd, 0, SEEK_SET)` before the lock call and before the unlock call.

Commit: `feat(serve): owner-only principals file with hashed per-person keys; reserve the operator handle`.

### Step 2: the key authenticator and the access token (`serve/auth.py`, `PrincipalRegistry`)

Tests in `tests/test_serve_keys.py`. The fixture `project` is as in Step 1, plus two separate
statements:

```python
ensure_operator_token(project)
op = OperatorTokenAuthenticator(token_path(project))
```

- `test_a_minted_key_authenticates_as_its_person`: `KeyAuthenticator(op, PrincipalRegistry(path))`
  returns `Principal("ann", "member", record.key_id)`. A `--role operator` user gets role `operator`.
- **`test_an_unknown_key_is_refused`**. Each of these returns `None`:
  - a well-formed key with an id that isn't in the file;
  - the right id with a wrong secret;
  - the key with one character changed;
  - the key with `x` appended;
  - the key truncated;
  - `""`;
  - `"tm_u_"`;
  - an upper-cased key.
- `test_the_operator_token_still_authenticates`: the bootstrap token returns `OPERATOR`, and a member
  key never goes to the operator path (spy on `op.authenticate`: not called for a `KEY_RE` match).
- **`test_lookup_is_by_id_with_one_constant_time_compare`**:
  - Build a registry of 50 users, then replace its internal mapping with a `dict` subclass whose
    `__iter__`, `values` and `items` raise `AssertionError`, leaving `get` working.
  - Wrap `auth.hmac.compare_digest` with a recorder.
  - A valid key, an unknown id and a revoked key each record exactly one call, with two 32-byte
    digests.
- **`test_a_revoked_key_is_refused_from_the_next_call`**: one authenticator instance; the key works;
  `revoke_user`; the same key returns `None`. No restart and no `utime` bump are needed (`os.replace`
  gives a new inode). The same holds for `rotate_user`, where the new key then works.
- `test_registry_fails_closed_and_keeps_the_operator`: overwrite the file with `b"{"` (0600) and
  bump its mtime. Every member key is refused, the operator token still returns `OPERATOR`, and
  exactly one ERROR record says `refusing every member key`. Repairing the file restores the keys.
- **`test_a_transient_read_error_is_not_cached`** (review I1, P1):
  - Ann's key works. Make one change (`revoke_user(project, "bob")`) so the next lookup must
    reload.
  - Monkeypatch `principals.load` to raise `OSError(errno.EMFILE, "Too many open files")` exactly
    once, then call through.
  - The next `authenticate(ann_key)` returns `None`, and logs one WARNING `could not be read` and
    `refused key <id>: key list unavailable`.
  - The call after that returns Ann's principal, **without the file changing in between** (assert
    its stat key is identical).
  - Repeat with `PermissionError(13, "sharing violation")` raised by `Path.read_bytes` (the Windows
    sharing-violation shape). It isn't cached either.
  - With a raising `os.stat` (not `FileNotFoundError`), `registry.active_ids()` raises
    `RegistryUnavailable`, and the next call succeeds.
- `test_a_transient_error_keeps_the_previous_records`: after a successful load, make
  `principals.load` raise `OSError` on every call, then change the file. `lookup` keeps raising
  `RegistryUnavailable`, the authenticator keeps refusing, and `registry._records` still holds the
  old records. Once `load` works again, the new file is loaded.
- `test_registry_without_a_file_is_empty`: `PrincipalRegistry(missing).active_ids() == frozenset()`,
  with no ERROR logged; `PrincipalRegistry(None)` is the same.
- `test_refusals_and_first_use_are_logged_by_key_id_only` (caplog at DEBUG on `taskmaster.serve`):
  - A valid key twice logs one `key <id> (ann) authenticated`.
  - A wrong secret logs `refused key <id>: wrong secret`.
  - A revoked key logs `refused key <id>: revoked`.
  - An unknown id logs `refused key <id>: unknown`.
  - No record contains the key, its secret part, or `sha256` of it.
- `test_logged_request_text_goes_through_log_safe`: spy on `serve.app.log_safe`. Each new refusal and
  first-use line passes its interpolated values through it. A wrong-secret request whose key id is
  valid and whose `Host` holds `\r\n` produces no raw control character in any record.
- `test_verifier_access_token_names_the_key_not_the_secret`:
  - `verify_token(key)` gives `token == f"key:{key_id}"`, `client_id == "ann"`,
    `subject == key_id`, and `claims` with the three entries.
  - The key and its secret aren't in `repr(access)` or `access.model_dump_json()`.
- **`test_subject_is_the_key_id_in_a_stable_form`** (orchestrator ruling c; 006 keys rate limits and
  in-flight caps on it):
  - For Ann's key, `subject` is exactly `record.key_id`: it fully matches `[0-9a-f]{16}`, with no
    prefix.
  - It is identical across two `verify_token` calls, across a fresh `KeyAuthenticator` and
    registry (a restart), and across a registry reload caused by adding Bob.
  - After `rotate_user`, the new key's subject is the new `key_id` and differs from the old one.
  - The bootstrap token's subject is exactly `"operator"`.
- **`test_is_host_local`** (orchestrator ruling a; the function itself, parametrized ASGI scopes):
  - true:
    - client `("127.0.0.1", 5)` with `Host: 127.0.0.1:7470`;
    - client `("::1", 5)` with `Host: [::1]:7470`;
    - client `("127.0.0.1", 5)` with `Host: localhost`;
    - client `("127.0.0.1", 5)` with `Host: LOCALHOST:7470`;
  - false:
    - client `("203.0.113.9", 5)`;
    - a missing `client`;
    - client `("not-an-ip", 5)`;
    - `Host: tm.example.com`;
    - a missing `Host`;
    - two `Host` headers;
    - each of `x-forwarded-for`, `forwarded` and `x-real-ip` present, also with an empty value and
      with upper-case header-name bytes.
  - It never calls `receive` (it takes only the scope) and never raises on malformed input.
- `test_principal_from_access_token_reads_three_claims`: the three claims round-trip; the two 001
  claims with handle `operator` give `OPERATOR`; two claims with handle `ann` give `None`.
- `test_current_key_id_outside_a_request_is_none`: called from a plain thread, it returns `None`.
- `test_request_key_id_chains_the_per_request_sources`:
  - Outside any request, `request_key_id()` is `None`.
  - Inside `viewer_principal(ann_id)` it is `ann_id`, and after the block it is `None` again
    (reset, not left set).
  - Nested blocks restore the outer value.
  - `viewer_principal("bad")` raises `ValueError`.
  - Two threads, each entering `viewer_principal` with a different id behind a
    `threading.Barrier(2)`, each read only their own id.

Update in `tests/test_serve_auth.py`:

- `test_verifier_access_token_carries_the_principal_not_the_secret` expects
  `token == "key:operator"`, `subject == "operator"`, and the three claims.
- `test_principal_from_access_token` expects `Principal("operator", "operator", "operator")`.

Commit: `feat(serve): member keys authenticate by id with one constant-time compare and reload on change`.

### Step 3: host-only operator token, session binding, revocation sweep (`serve/app.py`)

Rebase onto team-server-001's final tip first if it has moved (design R2).

Tests in `tests/test_serve_app_keys.py`:

- The app is `build_app(toy_server(), KeyAuthenticator(op, registry), allowed_hosts=("tm.example.com",),
  registry=registry, sweep_seconds=0.2)` in `running_app`.
- `whoami` returns `dataclasses.asdict(current_principal())`.
- A helper `post(url, token, headers={})` sends `raw_initialize`'s body with extra headers.

The tests:

- `test_member_key_reaches_the_tool`: `call_tool(url+"/mcp", ann_key, "whoami") == {"handle": "ann",
  "role": "member", "key_id": ann_id}`.
- `test_operator_token_is_refused_when_forwarded` (parametrized):
  - with `X-Forwarded-For: 203.0.113.9`;
  - with `Forwarded: for=203.0.113.9`;
  - with `X-Real-IP: 203.0.113.9`;
  - with `Host: tm.example.com`.

  Each gives 403 with body `{"error": OPERATOR_REMOTE_REFUSED}`, and one WARNING
  `not host-local`. The same requests with a member key are not 403.
- `test_operator_token_works_on_the_host`: a plain loopback `initialize` with the operator token
  is 200.
- `test_the_middleware_uses_the_shared_check`: monkeypatch `auth.is_host_local` to return `False`.
  A plain loopback operator request is then 403, which proves the middleware calls the shared
  function rather than a copy.
- `test_operator_refusal_never_reads_the_body`: call the middleware directly with an authenticated
  operator scope carrying `x-forwarded-for` and a `receive` that raises. The first message is a 403.
- **`test_a_session_belongs_to_the_key_that_opened_it`**:
  - `initialize` with Ann's key and take `mcp-session-id`.
  - A `tools/list` POST on that session id with Bob's key is 404 ("Session not found").
  - The same POST with Ann's key is 200.
  - After `rotate_user(ann)`, the new key on the old session is 404 too.
- **`test_a_revoked_key_is_refused_on_an_open_session`**:
  - `initialize` with Ann's key, then a `tools/list` on the session (200).
  - `revoke_user(project, "ann")`.
  - The next POST on the same session with the same key is 401, with the 001 body.
- **`test_revoking_a_key_ends_its_open_sessions`**:
  - Open one session each for Ann and Bob, then revoke Ann.
  - Within 3 s, Ann's session id is gone from `streamable_asgi(app, "/mcp").session_manager._server_instances`
    and Bob's is still present.
  - The log has `ended session <8> of revoked key <ann_id>`.
- `test_build_app_requires_session_internals` (parametrized; review M2). `check_session_internals`
  raises `RuntimeError` matching `record their owner and idle scope` in each of these cases:
  - a manager stand-in without `_session_owners`;
  - one without `_server_instances`;
  - one whose `_session_owners` isn't a dict;
  - a transport class whose `__init__` doesn't assign `idle_scope` (monkeypatch
    `app.StreamableHTTPServerTransport` with a stub class).

  A real built app starts cleanly, which proves the check accepts mcp 1.28.1.
- `test_sweep_errors_are_logged_not_raised`: a registry whose `active_ids` raises `ValueError` once.
  The app keeps serving, and one ERROR is logged.
- **`test_a_transient_registry_error_never_ends_sessions`** (review I1): open sessions for Ann and
  Bob, then make `active_ids` raise `RegistryUnavailable` for 1 s (several sweeps). Both sessions
  are still in `_server_instances`, and no `ended session` line is logged.

Update in `tests/test_serve_app.py`:

- `test_valid_token_reaches_the_tool_with_the_operator_principal` expects `"key_id": "operator"` too.
- `test_unknown_host_header_is_misdirected`'s `Host: tm.example.com` case now uses a member key
  (`add_user` in the fixture project). With the operator token it is 403, which also isn't 421.

Commit: `feat(serve): the bootstrap token stays on the host, sessions belong to one key, revocation ends open sessions`.

### Step 4: the origin path to the coordinator and the actor source (`client.py`, `service.py`, `identity_gate.py`, `serve/identity.py`, `ServeCoordinator`)

Tests in `tests/test_serve_identity.py`. Fixtures: `root` (native); `registry = PrincipalRegistry(principals_path(root))`
after `add_user(root, "ann")` and `add_user(root, "bob")`;
`owner = ServeCoordinator(root, registry=registry).start()`, closed in teardown.

- `test_origin_names_the_actor`: `owner.submit(request(...), origin=Origin(principal=ann_id)).result(30)`
  gives a receipt actor of `{"handle": "ann", "agent": "", "clone_id": ann_id}`. Bob's id gives Bob.
- **`test_a_client_sent_handle_is_ignored`**:
  - The envelope carries `actor={"handle": "mallory", "agent": "claude", "clone_id": "evil-clone-1"}`
    with origin Ann.
  - The receipt actor is `{"handle": "ann", "agent": "claude", "clone_id": ann_id}`; the agent is
    the only advisory part kept.
  - Over IPC: `Client(root, autostart=False).call("execute", envelope=..., visibility="native",
    origin={"principal": ann_id})` gives the same.
- `test_local_origin_is_the_operator`: `Client.execute` with no provider gives actor
  `{"handle": "operator", "agent": "", "clone_id": "operator"}`.
- `test_invalid_origin_is_refused` (parametrized): `{"principal": "../x"}`, `{"principal": 5}`,
  `{"principal": ann_id, "handle": "x"}`, `"ann"`, `{"principal": "A"*16}`. Each makes the IPC call
  raise `ValueError` matching `invalid origin`, and nothing commits (`receipt` state is `unknown`).
- `test_session_coordinator_ignores_an_origin`: a plain `Coordinator(root, actor_source=TokenSource())`,
  the identity-002 test's fake. Over IPC with `origin={"principal": "tok-ann"}`, the actor is
  `("", "")`: `LOCAL`, because the base `_origin` ignores the field.
- **`test_a_revoked_key_is_refused_at_the_writer`**:
  - Set `owner.checkpoint = lambda stage: revoke_user(root, "ann") if stage == "dequeued" else None`
    (first call only).
  - `submit` with origin Ann raises `IdentityRefused` matching `revoked`.
  - The `receipt` state is `unknown`, and `command_receipts` gained no row.
- `test_an_unknown_key_id_is_refused_at_the_writer`: origin `"f"*16` raises `IdentityRefused`.
- `test_client_sends_the_origin_only_with_a_provider`:
  - Monkeypatch `client_module.ORIGIN_PROVIDER = lambda: ann_id` and capture `Client._send`'s keyword
    arguments: `origin == {"principal": ann_id}`.
  - With the provider `None`, there is no `origin` key, and the message equals what `Client.execute`
    sent before this task: same keys and same values.
  - A provider that raises `PermissionError` propagates, and `_send` isn't called.
- `test_admit_accepts_an_origin` (regression): `IdentityGate.admit(connection, request)` and
  `admit(connection, request, Origin(principal="x"))` behave identically on a solo store.

Commit: `feat(coordinator): served writes carry the authenticated key as their origin; the serve actor source resolves it`.

### Step 5: the server-mode identity gate (`ServeIdentityGate`)

Tests in `tests/test_serve_identity.py` with `team_on`, and `git_stub` replaced by a stub that
raises `AssertionError` on any call, to prove host git is never read. Also monkeypatch
`identity_gate.socket.gethostname`, `identity_gate.write_identity` and
`identity_gate.disk_profiles` to raise.

- `test_first_write_creates_the_keys_profile`:
  - Ann's first `task.patch` with origin Ann commits.
  - A `profile` row `ann` exists with `name == "Ann Lee"`, `emails == ["ann@example.com"]` and
    `created == record.created`.
  - Its `profile.create` receipt has `caller_scope="coordinator-identity"`,
    `request_id == f"profile-{ann_id}-ann"` and actor `{"handle": "ann", "agent": "taskmaster",
    "clone_id": ann_id}`.
  - The store is at schema 5 (`_schema(root) == 5`).
  - No `local/identity.yaml` was written.
- `test_existing_profile_is_reused`: create Ann's profile first with Ann's key. After
  `rotate_user`, a write with the new key creates no second `profile.create`, and the actor's
  `clone_id` is the new id.
- `test_deleted_profile_refuses`: mark the profile row deleted, as
  `test_native_service_actor.py:156` (`test_deleted_profile_refuses_and_keeps_schema_4`) does. The
  next attribution-bearing write raises `IdentityRefused` matching `was deleted`.
- `test_operator_and_local_writes_need_a_person_in_team_mode`: an attribution-bearing write with
  origin `operator`, and one through `Client.execute` (`LOCAL`), each raise `IdentityRefused` matching
  `add-user`, and nothing commits.
- `test_exempt_operations_pass_for_the_operator`: a `task.claim_renew` (or a `sync.*` internal
  write, as `test_internal_sync_writes_carry_taskmaster` builds it) with origin `LOCAL` commits, with
  actor `operator`.
- `test_solo_mode_creates_no_profile`: without `team_on`, Ann's write commits with actor Ann in the
  receipt, no profile row exists, and the schema stays 3.
- `test_revoked_key_refuses_before_any_profile_is_made`: revoke Ann before her first write. It
  raises `IdentityRefused` matching `revoked`, and no profile row or schema change appears.
- `test_replay_makes_no_second_profile`: Ann's first write commits and creates her profile. A retry
  of the same `request_id` returns the original receipt, and `command_receipts` gains no second
  `profile.create` row, because `_replayed` is checked before the profile step (identity-001 D15).
- `test_a_revoked_keys_replay_is_refused_without_side_effects`: commit with Ann, revoke Ann, then
  retry the same `request_id` with origin Ann. `stamp` runs before `commands.execute` on every
  request, replays included, so it raises `IdentityRefused` matching `revoked`. The original
  receipt and the event count are unchanged. This is intended: a revoked key gets no answers at
  all, and over HTTP it can't even authenticate.

Commit: `feat(serve): server-mode identity gate creates or reuses the key's profile and never reads host git`.

### Step 6: daemon wiring and end-to-end over HTTP (`serve/daemon.py`)

Tests in `tests/test_serve_principals_e2e.py`, using the `served` fixture pattern
(`tests/test_serve_daemon.py:34`) with `team_on` applied before the daemon starts. Ann and Bob are
added with `add_user` before start, unless a test says otherwise.

- **`test_two_keys_write_as_two_people`**:
  - Ann and Bob each `backlog_add_task` over HTTP (`call_tool(url, key, ...)`, titles `A1` and `B1`).
  - `_value(root, "SELECT actor FROM domain_events WHERE actor IS NOT NULL")`, decoded, contains
    `{"handle": "ann", "clone_id": ann_id}` and `{"handle": "bob", "clone_id": bob_id}`, with
    agent `""` (or the first client's agent; design §10). The task events of `A1` carry Ann's actor
    and those of `B1` Bob's.
  - Profiles `ann` and `bob` exist.
- **`test_a_client_sent_handle_is_ignored_over_http`**:
  - Ann calls `backlog_add_task` with extra headers `X-Taskmaster-Handle: bob` and
    `X-Forwarded-User: bob`, and with `clientInfo.name = "bob"` (an `httpx`-level `initialize`, or
    `fastmcp.Client(..., client_info=Implementation(name="bob", version="1"))`).
  - The resulting events carry Ann's handle and key id.
- `test_operator_token_cannot_write_as_a_person_in_team_mode`: the bootstrap token's
  `backlog_add_task` returns text with `add-user`, and the task count is unchanged. Reads with it
  work.
- **`test_a_revoked_key_is_refused_on_an_open_session_over_http`**:
  - In one `async with fastmcp.Client(url, auth=ann_key)` block: one write succeeds, then
    `revoke_user(root, "ann")`, then the next `call_tool` raises.
  - The task count is unchanged, and `serve.log` has `refused key <ann_id>: revoked`.
- `test_a_key_added_while_running_works_without_a_restart`: `add_user(root, "cy")` after start; Cy's
  `backlog_list_tasks` succeeds.
- **`test_a_key_never_appears_in_logs_or_errors`**:
  - Set `caplog.set_level(logging.DEBUG)` on the root logger and capture `capfd`.
  - Run, collecting every HTTP response's status line, headers and body as text:
    - Ann's valid call;
    - a raw POST with Ann's id and a wrong secret;
    - Bob's key after `revoke_user`;
    - a malformed `tm_u_zz`;
    - Ann's key on a non-MCP path;
    - Ann's key with `Host: evil.example` (421).
  - Also call `ServeActorSource.record` with a revoked origin and keep the `IdentityRefused` text.
  - Then neither Ann's nor Bob's key, nor either secret part, appears in:
    - caplog's text;
    - `serve.log`;
    - `capfd` output;
    - the daemon's ready-line stdout;
    - any HTTP text;
    - the refusal text;
    - `principals.json`;
    - any `domain_events` or `command_receipts` row.
  - This step uses only the `principals` functions. The CLI half of this check is
    `test_cli_never_leaks_a_key_beyond_its_one_line` in Step 7, which builds the commands (review P2).
- `test_unsafe_principals_file_refuses_start` (POSIX `chmod 0o644`, plus an all-OS
  `verify_private` monkeypatch): `ServeDaemon.start()` raises `ServeRefused` with `exit_code == 2`,
  and no coordinator owns the store afterwards (`ownership_held(root) is False`).
- `test_principals_file_breaking_at_runtime_fails_closed`: with the daemon running, write `b"{"`
  (0600). Ann's next call is 401, the operator token still lists tasks, and `serve.log` has
  `refusing every member key`.
- `test_team_mode_off_is_warned`: without `team_on`, `serve.log` has `team mode is off`; with it,
  it doesn't.
- **`test_concurrent_mcp_and_viewer_writes_stamp_their_own_key`** (re-review of ruling b):
  - With the daemon running and `client_module.ORIGIN_PROVIDER is auth.request_key_id`, start two
    writes concurrently behind a `threading.Barrier(2)`:
    - Ann's MCP `backlog_add_task` over HTTP (title `M1`);
    - a simulated viewer request on another thread: `with auth.viewer_principal(bob_id):` then
      `Client(root, autostart=False).execute(...)` adding task `V1`, the way 006's handler will.
  - Repeat 20 rounds with fresh titles. In every round, `M1`'s events carry Ann's `key_id` and
    `V1`'s carry Bob's. No event is ever stamped `operator` or with the other's key.
  - Afterwards `ORIGIN_PROVIDER` is still `auth.request_key_id`, never replaced, and
    `VIEWER_KEY_ID.get()` on the test thread is `None`.
- `test_origin_provider_is_restored`: after `request_stop` and join, `client_module.ORIGIN_PROVIDER`
  is what it was before start (`None`).
- `test_ready_line_names_the_principals_file`: `line["principals_file"].endswith("principals.json")`.

Update in `tests/test_serve_daemon.py`: `test_ready_line_is_json_without_the_token` gains the
`principals_file` assertion if it checks the exact key set; otherwise leave it.

Commit: `feat(serve): the daemon authenticates member keys and attributes every write to the key's person`.

### Step 7: the CLI (`serve/cli.py`, `tests/test_serve_cli.py`, `tests/test_packaging.py`)

Tests added to `tests/test_serve_cli.py`, through `cli.main([...])` with `capsys`:

- `test_add_user_prints_the_key_once`:
  - `main(["add-user", "ann", "--root", p, "--name", "Ann Lee", "--email", "ann@example.com"]) == 0`.
  - stdout is exactly one line, `KEY_RE.fullmatch(line)`.
  - stderr contains `shown once`.
  - The file holds the hash and not the line.
- `test_add_user_refusals` (parametrized → exit 2 with the message):
  - an existing active handle → `rotate-user`;
  - `operator` → `reserved`;
  - `Ann` → `handle`;
  - a non-project root → `not a taskmaster project`.
- `test_add_user_reports_the_store_check`: monkeypatch `principals.inspect_store` to return, in
  turn:
  - `StoreCheck("deleted", None, True)`: exit 2, nothing written;
  - `("live", "Ann Lee", True)` with `--name X`: exit 0, stderr says `existing profile` and
    `ignored`;
  - `("unknown", None, None)`: exit 0, stderr `server checks on first write`, and no team-mode line;
  - `("absent", None, False)`: exit 0, stderr `team mode is off`, and the key is still minted
    (review M3).
- `test_key_file_keeps_the_key_off_stdout` (review M4):
  - `main(["add-user", "ann", "--root", p, "--key-file", kf]) == 0`. stdout and stderr hold no
    `KEY_RE` match, and stdout says `written to`.
  - `kf` holds one `KEY_RE` line whose hash is Ann's record.
  - On POSIX the mode is `0o600`; `verify_private(kf)` passes everywhere.
  - An existing `kf` exits 2 and adds no record.
  - `rotate-user ann --key-file kf2` behaves the same way.
- **`test_cli_never_leaks_a_key_beyond_its_one_line`** (moved here from Step 6; review P2):
  - Run `add-user cy`, `list-users`, `list-users --json`, `rotate-user cy`, `revoke-user cy`, a
    refused `add-user cy` and a refused `rotate-user nobody`, all through `cli.main`, capturing
    stdout and stderr with `capfd` and the root logger with caplog at DEBUG.
  - Each key Cy ever had appears exactly once: on the stdout of the call that minted it.
  - No key or secret appears in stderr, in any other call's output, in caplog, in
    `principals.json` or in `serve.log` (if present).
- `test_add_user_takes_no_key_argument`: `main(["add-user", "ann", "--root", p, "--key", "x"])`
  exits 2 (argparse), and no file is written.
- `test_rotate_and_revoke`:
  - `rotate-user ann` prints a new key and `rotated ann: <old> -> <new>`.
  - `revoke-user ann` prints `revoked ann (<id>)`.
  - Both on an unknown handle exit 2.
- `test_list_users_never_prints_a_hash`: after add, rotate and revoke, `list-users` and
  `list-users --json` show `active`, `rotated` and `revoked` states. No 64-hex string and no
  `sha256` key appears, and the JSON parses to records with `key_id`, `handle`, `role`, `created`,
  `revoked` and `revoked_reason`.
- `test_lock_timeout_exits_69`: hold `locked(p)` on a thread with `LOCK_SECONDS = 0.2`;
  `add-user` exits 69.
- `test_user_commands_need_no_running_daemon`: all four commands succeed on a project whose store no
  coordinator owns, and none starts one (`ownership_held(root) is False` afterwards).
- Extend `test_end_to_end_mcp_tool_call_over_http`, or add a sibling with the same child-process
  pattern:
  - Run `taskmaster_cli.py serve add-user ann --root <root>` as a child and read the key from its
    stdout.
  - Start the served child.
  - A `backlog_list_tasks` call with that key succeeds.

Changes to `tests/test_packaging.py`:

- Add `test_serve_user_commands_never_import_the_server`: a child running
  `taskmaster_cli.py serve add-user --help` prints `CLEAN`, as `test_serve_help_never_imports_the_server`
  does.
- Add `("serve", "list-users")` to the dispatch parameters if that table checks sub-commands.

Update the `cli.py` module docstring: the four usage lines (design §5), the show-once rule, and
*"The operator bootstrap token works only on the host. Give your own laptop's agent a key from
add-user (--role operator)."* (001 review M4; 016's how-to repeats it).

Commit: `feat(serve): add-user, rotate-user, revoke-user and list-users`.

---

## 4. Order of work inside a step

Write the tests first, and confirm they fail for the right reason:

- Steps 1, 2 and 5: `ImportError` or `AttributeError` on the new names.
- Step 3: the forwarded operator request is 200; the revoked session survives the sweep.
- Step 4: `submit` with an origin attributes to `("", "")`, and `ServeCoordinator` has no `_origin`.
- Step 6: Ann's and Bob's writes both carry `operator`, or the host's identity.
- Step 7: an unknown sub-command exits 2.

Don't edit sources while a run is in progress: the build fingerprint changes and real-coordinator
tests fail spuriously (about 13 of them).

## 5. Commands

Run from the worktree root (`C:\Users\gruku\Files\Claude\taskmaster\.worktrees\team-server-002`)
with the repo venv's interpreter. Bare `python` on this machine is the wrong interpreter.
Put every temp file, `--basetemp` and spike under `D:\scratchbook\taskmaster\ts002\` (`/d/scratchbook/taskmaster/ts002/`
in Git Bash). It is a user rule, because C: is full. Never use the C: temp or scratchpad. Use a fresh
sub-directory per run.

Step tests:

```bash
/c/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python -m pytest tests/test_serve_principals.py \
  tests/test_serve_keys.py tests/test_serve_app_keys.py tests/test_serve_identity.py \
  tests/test_serve_principals_e2e.py tests/test_serve_cli.py -q -p no:cacheprovider --basetemp="/d/scratchbook/taskmaster/ts002/step-<n>"
```

Regression subset (after Steps 0, 2, 4, 6 and 7, and before reporting done):

```bash
/c/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python -m pytest tests/test_serve_config.py \
  tests/test_serve_auth.py tests/test_serve_app.py tests/test_serve_coordinator.py tests/test_serve_daemon.py \
  tests/test_actor_resolver.py tests/test_identity_gate.py tests/test_team_identity.py \
  tests/test_native_service_actor.py tests/test_native_service_identity.py tests/test_native_service.py \
  tests/test_native_service_build.py tests/test_packaging.py tests/test_server_instructions.py \
  -n 3 -q -p no:cacheprovider --basetemp="/d/scratchbook/taskmaster/ts002/reg-<n>"
```

Leave the full suite to the review gate.

## 6. Risks (detail in design §12)

- **R1. SDK internals** (`_session_owners`, `idle_scope`, owner `subject`): pinned by
  `test_a_session_belongs_to_the_key_that_opened_it`, `test_revoking_a_key_ends_its_open_sessions`
  and `test_build_app_requires_session_internals`, plus the `mcp>=1.28` floor.
- **R2. 001 is under review.** Rebase before Step 3. If 001's fixes change `RequirePrincipalMiddleware`,
  `build_app`'s signature or `ServeCoordinator`, re-run Steps 3, 4 and 6.
- **R3. 006 in parallel** edits `app.py`. Keep the D7 branch self-contained in
  `RequirePrincipalMiddleware`. The branch that lands second merges both.
- **R4. `service.py` and `identity_gate.py` are shared** with other team-collab branches. Only the
  two lines and one parameter in §2.4 change.
- **R5. Windows `os.replace` contention** is retried (`test_replace_contention_is_retried`).

## 6a. Rollback

- Revert the step commits, newest first.
- On a host, delete `.taskmaster/local/serve/principals.json`, or revoke every user. Profiles the
  gate created stay as ordinary profiles.
- No schema, projection or export format changes beyond the existing opt-in upgrade.

## 7. Left for later tasks (mention, don't build)

- Per-connection `SESSION_ID`, `caller_scope` prefix, claim ownership with the operator-role
  override, same-handle adoption, the per-connection `clientInfo` agent (003).
- Receipt, `cancel` and `receipt` scoping per principal (004).
- Admin methods only for `key_id == "operator"` on the host (005; it calls `auth.is_host_local`).
- 006 (orchestrator rulings a–c):
  - rate limits and in-flight caps keyed on `access_token.subject` (the `key_id`);
  - pre-auth limits;
  - error sanitizing;
  - viewer login, which calls `auth.is_host_local` for the bootstrap token;
  - viewer-write attribution, by wrapping each writing viewer request in
    `auth.viewer_principal(key_id)`. It never assigns `client.ORIGIN_PROVIDER`, `VIEWER_KEY_ID`'s
    default, or any global value.

  002 builds none of these.
- Per-device keys (`--label`), if wanted (design D5).
- The deployment how-to's key section (016; design §12a, review M6):
  - run `add-user` and the other user commands as the service user (`sudo -u taskmaster`);
  - test D7 with real Caddy, including a client sending the hop-by-hop `Connection:
    X-Forwarded-For`;
  - never strip `X-Forwarded-For` or rewrite `Host`;
  - never set Caddy's `log_credentials`;
  - `--key-file` for handing keys over;
  - `.mcp.json` with `Bearer ${TASKMASTER_TOKEN}`.
- 001 follow-up: `OperatorTokenAuthenticator` caches a transient `OSError` as "no token" (design D6);
  apply the same split as `PrincipalRegistry`.
