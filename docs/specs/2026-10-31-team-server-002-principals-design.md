<!-- User intent: the task-level design for team-server-002. Each teammate's agent reaches the user's
     Taskmaster server with its own API key, which the operator mints and revokes on the host. Every
     write is attributed to the key's person, never to the host's git identity or to anything the client says. -->

# team-server-002: per-person API keys (design, 2026-10-31)

**Task.** `team-server-002`. Done when the operator can mint and revoke a per-person API key, keys
are stored hashed, and every request resolves key → handle → profile through the team-identity-002
actor-resolver seam.
**Source.** `docs/specs/2026-10-31-team-server-mode-addendum.md` §3 (principal model) and §13
(deployment profile); `docs/specs/2026-10-31-team-server-001-serve-design.md` §5 (D5, the seam,
review M4); `docs/specs/2026-10-31-team-collaboration-native-design.md` rev 3.3 §4 (actor);
`docs/plans/2026-10-31-team-identity-002-actor.md` D2 and its plan-review M2.
**Plan.** `docs/plans/2026-10-31-team-server-002-principals.md`.
**Verified against.** FastMCP 3.4.3 and mcp 1.28.1 in the repo venv. The SDK facts this design
relies on are cited with file and line in §6, §8 and §12. They were read from the source, not
spiked, so the Step 3 tests are the first proof of the session-owner check and the idle-deadline
sweep.

## 1. Scope

In scope:

- the key format, minting with show-once output, hashed storage, file permissions;
- `taskmaster serve add-user`, `rotate-user`, `revoke-user` and `list-users`;
- an authenticator that accepts the operator bootstrap token and member keys;
- binding each MCP session to the key that opened it, and revocation that reaches open sessions;
- carrying the authenticated key from the HTTP tool call to the coordinator, and an actor source
  that turns it into `{handle, clone_id}` in place of `LocalCloneSource`;
- a server-mode identity gate that creates or reuses the key's profile and never reads host git
  (identity-002 plan-review M2);
- the operator principal, kept on the host;
- reserving the `operator` handle.

Out of scope, each owned by another task:

| Not here | Task |
|---|---|
| Per-connection `SESSION_ID`, handle-prefixed `caller_scope`, claim ownership checks, same-handle adoption, per-connection `clientInfo` agent | 003 |
| Receipt, queue, cancel and request-id scoping per principal | 004 |
| Remote method allowlist, admin methods loopback-only, `linear_bootstrap` hardening | 005 |
| Rate limits (per principal and pre-auth), body and header timeouts, error sanitizing, viewer login | 006 |
| Refusing host-local sessions, `--force-upgrade` | 009 |
| Deployment how-to, systemd unit and Caddyfile files, Linux lane | 016 |

## 2. Principals

A **principal** is who a request is from. There are two kinds.

| | Operator (bootstrap) | Member key |
|---|---|---|
| Credential | `.taskmaster/local/serve/operator.token` (001, unchanged) | minted by `serve add-user` |
| `handle` | `operator` (reserved, no profile) | the person's handle |
| `role` | `operator` | `member` or `operator` (`--role`) |
| `key_id` | `operator` | 16 lowercase hex |
| Where it works | the host only (D7) | anywhere |
| Writes in team mode | not attribution-bearing ones (D9) | yes, as the person |

`Principal` in `taskmaster/serve/auth.py` gains a field:

```python
@dataclass(frozen=True)
class Principal:
    handle: str
    role: str        # "operator" | "member"
    key_id: str      # "operator" for the bootstrap token, else the member key's id
OPERATOR = Principal("operator", "operator", "operator")
```

`key_id` is not a secret. It is the audit name of a key: it shows in `list-users`, in the log and
in every write's actor (D10).

## 3. Key format and minting

**D1. A key is `tm_u_<key_id>_<secret>`.**

- `key_id` is `secrets.token_hex(8)`: 16 lowercase hex characters, regenerated in the unlikely
  case it is already used in the file.
- `secret` is `secrets.token_urlsafe(32)`: 43 characters from `[A-Za-z0-9_-]`, 256 bits.
- `KEY_RE = re.compile(r"tm_u_([0-9a-f]{16})_([A-Za-z0-9_-]{43})")`, matched with `fullmatch`.
  The key is 65 characters.

Why this shape:

- **The id is inside the key**, so the server finds the one record to check by id (D4) and never
  compares the presented key against every stored hash.
- **The prefix is fixed and distinct from the operator token** (`tm_op_`). The authenticator
  dispatches on it. Secret scanners (GitHub custom patterns, gitleaks) and log redaction can match
  `tm_u_[0-9a-f]{16}_` reliably, and a key pasted into a commit or a chat is recognisable as one.
- **256 bits of randomness** makes a fast hash sufficient (D2).
- **`_` separates the parts**, and both parts have fixed lengths, so the secret's own `_` and `-`
  characters never make the parse ambiguous.

**Show once.** `add-user` and `rotate-user` print the key on stdout, on one line and nothing else,
and print guidance on stderr: *"This key is shown once and is not stored. Give it to <handle>
privately; their agent sends it as `Authorization: Bearer <key>`."* The server keeps only its hash,
so a lost key is replaced with `rotate-user`, never recovered. No command takes a key as an
argument, so keys never land in shell history or in `ps` output. `--key-file` (§5) writes the key
to an owner-only file instead, for when an agent runs the command and its transcript shouldn't
hold the key.

## 4. Hash storage

**D2. The stored value is `sha256(key)` as 64 hex characters, compared with `hmac.compare_digest`.**

- **Why not argon2, scrypt or bcrypt.** Slow KDFs protect low-entropy secrets (passwords) against
  offline guessing. A 256-bit random secret can't be guessed offline at any speed, so a slow hash
  adds nothing. It would cost tens of milliseconds of CPU on every HTTP request, which hands an
  unauthenticated caller a cheap CPU-exhaustion lever before 006's limits exist, and it would add a
  dependency. GitHub and Stripe tokens are handled the same way.
- **Why not HMAC with a pepper.** The pepper would live next to the hashes, owner-only in the same
  directory, so stealing one steals both. A pepper elsewhere (environment, systemd credential) is
  operational weight for no gain at 256 bits.
- **The whole key is hashed**, prefix and id included, so a record can't be satisfied by a secret
  minted for another id.

**D3. The hashes live in a file, `.taskmaster/local/serve/principals.json`, not in a store table.**

Reasons against a table in `store.db`:

- **Export and backup.** The store is exported to `.taskmaster/` projection files, and task 13
  commits that export to `taskmaster-state` and pushes it to a Git remote. A table of key hashes
  would need exclusions in the projection, the export, the backup branch and every future dump.
  One missed path publishes credentials.
- **The single writer.** The daemon's coordinator owns the store. A table written by `add-user`
  would have to go through the coordinator's IPC, which would add an admin write method that task 5
  then has to fence from remote principals. A file needs no IPC at all.
- **Schema.** A new table means a schema step in the opt-in schema 4/5 sequence, for data that
  isn't backlog data.
- **Lifetime.** Restoring a store from backup shouldn't resurrect revoked keys. Keys belong to the
  host, like `operator.token` and `discovery.json`.

The file sits beside `operator.token` in `local/serve/`, which 001 already makes owner-only through
`ownership.private_directory(root, "serve")` (0700 on POSIX, an owner-only DACL on Windows) and
which `.taskmaster/local/.gitignore` already keeps out of Git.

Shape (`version` 1):

```json
{"version": 1,
 "keys": [
   {"key_id": "3f9a0c1e5b7d2a46", "handle": "ann", "role": "member", "name": "Ann Lee",
    "emails": ["ann@example.com"], "created": "2026-10-31T12:00:00Z",
    "sha256": "<64 hex>", "revoked": null, "revoked_reason": null},
   {"key_id": "0b7e19d4c2a85f31", "handle": "bob", "role": "member", "name": "bob", "emails": [],
    "created": "2026-10-31T12:05:00Z", "sha256": null,
    "revoked": "2026-11-02T09:00:00Z", "revoked_reason": "revoked"}
 ]}
```

Rules, checked on every load (CLI and daemon):

- `key_id` matches `[0-9a-f]{16}` and is unique.
- `handle` matches `team_identity.HANDLE_RE` and is neither reserved nor degenerate.
- `role` is `member` or `operator`.
- `created` and `revoked` use `team_identity.CREATED_FORMAT`.
- A record is **active** exactly when `revoked` is null. Then `sha256` must be 64 lowercase hex.
  A revoked record has `sha256` null and a `revoked_reason` of `revoked` or `rotated`.
- At most one active record per handle (D5).
- `name` and `emails` pass the `profile.create` contract (`native/profiles.validate`), because the
  gate creates the profile from them (D9).

Revoked records are kept, with their hash removed. They are the map from a `key_id` seen in
history to the person and the dates it was valid for.

**File permissions.**

- Every write goes to a temp file in the same directory, opened with `O_CREAT|O_EXCL|O_WRONLY` and
  mode `0o600`, then fsynced and published with `os.replace`. The mode doesn't depend on the umask,
  and a reader never sees a half-written file.
- Every read first calls `ownership.verify_private(path)`: on POSIX the owner must be the current
  user and `st_mode & 0o077 == 0`; on Windows the DACL must be owner-only. A failure refuses: the CLI
  exits 2; daemon start exits 2; a running daemon refuses every member key (D6).
- A link in place of the file is refused (`verify_private` checks).
- On Windows `os.replace` fails with `PermissionError` while the daemon has the file open for its
  short read. The writer retries 20 times at 50 ms intervals, then exits 69.

**Mutations are serialised** with an exclusive lock on `local/serve/principals.lock` (0600), taken
non-blocking with `fcntl.flock` or `msvcrt.locking` (the `ownership.Ownership` primitives) and
retried for up to 10 s, then exit 69 with *"another add-user, rotate-user or revoke-user is
running"*. Each mutation reads, changes and writes the whole file under the lock. The daemon never
takes the lock. It only reads, and `os.replace` is atomic.

## 5. CLI

```
taskmaster serve add-user    <handle> --root DIR [--name "Ann Lee"] [--email ann@example.com]...
                                                 [--role member|operator] [--key-file PATH]
taskmaster serve rotate-user <handle> --root DIR [--key-file PATH]
taskmaster serve revoke-user <handle> --root DIR
taskmaster serve list-users           --root DIR [--json]
```

- **`add-user`** validates the handle, name and emails (§4 rules), refuses a handle that already has
  an active key (*"ann already has a key (3f9a…); use rotate-user to replace it"*), mints a key,
  appends the record and prints the key once (§3). `--name` defaults to the handle and `--role` to
  `member`.
  - A handle whose only records are revoked is **reinstated**: it gets a new record and key, and
    stderr says so.
  - **Profile check (best effort).** If the store can be opened read-only
    (`protocol.connect(root, readonly=True)`), `add-user` looks up `entity_core` for
    `kind='profile' AND public_id=<handle>`. A deleted profile refuses (*"profile ann was deleted
    from this backlog; choose another handle"*). A live profile is reported (*"uses the existing
    profile ann (Ann Lee)"*), and then `--name` and `--email` are ignored with a note, because the
    gate never edits an existing profile. If the store can't be read, the check is skipped with a
    note. The gate (D9) is the authority either way.
  - **Team mode check (review M3).** Through the same read-only connection, when
    `team_mode_enabled(root, project_doc=...)` is false, stderr warns: *"team mode is off for this
    project: ann's writes will be attributed in receipts only, and no profile will be created, until
    team mode is on"*. That is a warning only; the key is still minted. The connection is closed in
    a `finally` (`contextlib.closing`).
- **`--key-file PATH` (add-user and rotate-user; review M4)** writes the key to `PATH` instead of
  stdout, so the agent or terminal that runs the command never has the key in its transcript.
  - The file is created exclusively (`O_CREAT|O_EXCL|O_WRONLY`, mode `0o600`) and refuses an
    existing path. The content is the key plus a newline.
  - On Windows the file gets `ownership._windows_private`'s owner-only DACL, and `verify_private`
    is checked after the write.
  - stdout then prints only `key for ann written to PATH (owner-only)`. The record is written to
    `principals.json` only after the key file has been fsynced: if the key file fails, no record is
    added.
  - The operator moves the file to the teammate privately and deletes it.
- **`rotate-user`** needs an active key. In one locked write it marks the old record revoked with
  reason `rotated`, appends a new record that copies `handle`, `role`, `name` and `emails`, and
  prints the new key once. The old key stops working on the daemon's next request (D6). There is no
  overlap window: for a small team, one re-paste beats a period with two valid keys.
- **`revoke-user`** needs an active key. It sets `revoked`, `revoked_reason: "revoked"` and
  `sha256: null`, and prints `revoked ann (3f9a0c1e5b7d2a46)`. The profile and all history stay.
- **`list-users`** prints one line per record: handle, role, key id, created, and `active`,
  `revoked <date>` or `rotated <date>`. `--json` prints the records without `sha256`. No command
  ever prints a hash.
- **All four work whether or not the daemon is running**, because they touch only the file. They
  never import `taskmaster.backlog_server`.
- **Exit codes**: 0 done; 2 refused (bad or reserved handle, already active, unknown or inactive
  handle, unsafe or malformed file, not a project); 69 lock or replace timeout.

**D5. One active key per person.** A person who works from two machines puts the same key in both
configs. Separate per-device keys (`--label`) would give finer revocation, but they would need a
key-to-person table, and `rotate-user` and `revoke-user` would need a key selector. That can be
added later without changing the file's `version` 1 shape, because records are already keyed by
`key_id`.

## 6. Authentication

**D4. Lookup by id, then one constant-time compare.**

```python
class KeyAuthenticator:                       # replaces OperatorTokenAuthenticator in the daemon
    def __init__(self, operator: OperatorTokenAuthenticator, registry: PrincipalRegistry): ...
    def authenticate(self, token: str) -> Principal | None:
        match = KEY_RE.fullmatch(token or "")
        if match is None:
            return self.operator.authenticate(token)          # 001 path, unchanged
        record = self.registry.lookup(match.group(1))       # dict lookup by key_id; None if unknown
        digest = record.digest if record and record.active else _DUMMY_DIGEST
        ok = hmac.compare_digest(hashlib.sha256(token.encode()).digest(), digest)
        return Principal(record.handle, record.role, record.key_id) if ok and record and record.active else None
```

- **No enumeration.** The registry is a `dict[key_id, Record]`. Exactly one `compare_digest` runs
  per request, whether the id is known, unknown or revoked, so neither the count of keys nor the
  presented secret shapes the timing. Key ids aren't secret, so a timing difference in the dict
  lookup reveals nothing worth having.
- **One refusal, no oracle.** Unknown id, wrong secret, revoked key and malformed token all give
  the same 401 body as in 001. Only the log tells them apart (D10).

**D6. The registry reloads on change; revocation reaches open sessions.**

`PrincipalRegistry(path)` caches the parsed file on its `(st_mtime_ns, st_size, st_ino)`, which
is 001's rotation pattern. Each `lookup` makes one `stat`; `os.replace` always produces a new inode
and size or mtime, so the next request after `revoke-user` sees the change.

- A missing file means no members, which is normal.
- **A refusal caused by the file itself is cached.** If the file fails the §4 checks or
  `verify_private` (both surface as `PrincipalsRefused`), the registry stores the new stat key with
  **no** records. Every member key is then refused (fail closed) until the file changes, with one
  ERROR per stat key. The operator token keeps working, so the operator can repair the file.
- **A transient I/O error is not cached (review I1).** `EMFILE` under a connection flood, a Windows
  sharing violation while `add-user` replaces the file, or any other `OSError` from `stat`, `open`
  or `read` is a different case:
  - the registry keeps both its previous stat key and its previous records, unchanged;
  - the lookup that hit the error refuses that one request: `lookup` returns `None`, which gives a
    401;
  - the next lookup stats and reads again;
  - `active_ids` raises `RegistryUnavailable`, and the sweep (point 3 below) skips that round. A
    transient error therefore never ends a live session.

  It logs one WARNING per error kind per minute. `load` turns only `verify_private`'s
  `PermissionError` into `PrincipalsRefused`. A `PermissionError` from the read itself, which is
  how Windows reports a sharing violation, stays an `OSError` and is transient.
- 001's `OperatorTokenAuthenticator` (`auth.py:121-129`) stores the stat key before reading in the
  same way. A one-off `OSError` there locks the operator out until `serve token --rotate`. That is
  out of scope here and noted for 001's follow-up: the same split, where only `ServeRefused` is
  cached.

A revoked key is cut off in four places:

1. **Every HTTP request re-authenticates.** Starlette's `AuthenticationMiddleware` runs
   `verify_token` on each request, so the next request is refused with 401. That includes every
   later POST on an MCP session that the key opened before it was revoked.
2. **Sessions are bound to the key that opened them.** mcp 1.28 records an owner for each stateful
   session, `authorization_context(user)` = `(client_id, issuer, subject)`
   (`mcp/server/streamable_http_manager.py:234-279`, `bearer_auth.py:28-43`). Any request with
   another credential gets *"Session not found"* (404). `ServeTokenVerifier` sets
   `subject=key_id`, so a session opened by Ann's key can't be continued with Bob's key, or with
   Ann's rotated key. The SDK supplies this for free, but 002 depends on it, so the `mcp` floor
   rises to `>=1.28` and a test pins the behaviour.
3. **Open sessions are ended.** A lifespan task in `build_app` runs every
   `REVOCATION_SWEEP_SECONDS` (2 s). It refreshes the registry, and for each session whose owner's
   `subject` is a member key id that is no longer active, it sets that transport's
   `idle_scope.deadline = anyio.current_time()`. The SDK's own idle path then drops and terminates
   the session (`streamable_http_manager.py:306-311`), which also closes a long-lived GET (SSE)
   stream that would otherwise never send another request. The sweep runs on the event loop, so it
   never races the manager. When `active_ids` raises `RegistryUnavailable` (a transient I/O
   error, see above), the round is skipped. At startup `build_app` checks that the session manager
   has `_session_owners` and `_server_instances`, and that `StreamableHTTPServerTransport.__init__`
   assigns `idle_scope` (`"idle_scope" in __init__.__code__.co_names`; it is set at
   `mcp/server/streamable_http.py:192`), and raises `INTERNALS_CHANGED` if any of them is missing, like D10 in 001.
4. **The writer rechecks.** The actor source (D8) looks the key up again when it stamps the command,
   after dequeue and before `commands.execute`. A request that authenticated just before the
   revocation and was still queued is refused with `IdentityRefused` (*"Refused: this API key was
   revoked. Nothing was written."*) and commits nothing. `stamp` runs on replays too, so a revoked
   key's retry of a committed request is refused as well; it gets no answers at all. A command
   already executing finishes, which is the existing contract for any accepted command.

**AccessToken shape.** `ServeTokenVerifier.verify_token` returns
`AccessToken(token=f"key:{key_id}", client_id=handle, subject=key_id, scopes=[], expires_at=None,
claims={"taskmaster_principal": handle, "taskmaster_role": role, "taskmaster_key_id": key_id})`.
`principal_from_access_token` reads the three claims. A token with only 001's two claims (handle
and role) maps to `key_id="operator"` when the handle is `operator`, and to `None` otherwise. The
raw key never enters the `AccessToken`, so nothing downstream can log it.

**The subject is a stable contract (orchestrator ruling c, the 002/006 seam).**
`access_token.subject` is always exactly the principal's `key_id`, with no prefix, case change or
fingerprint:

- `operator` for the bootstrap token;
- the 16 lowercase hex characters for a member key. They are the same across requests, restarts
  and registry reloads, and they change only when `rotate-user` mints a new key.

006's rate limits and in-flight caps key on it, and so do the SDK's session-owner check and D6's
sweep. `token` (`key:<key_id>`) and the `taskmaster_key_id` claim carry the same value, but
consumers key on `subject`. Changing this form is a cross-task contract change. A Step 2 test
pins it.

## 7. The operator principal

**D7. The bootstrap token works only on the host.** 001 left this open (review M4). The token
was meant for the host. A remote request that carries it most likely means it was copied off the
host, and it would get the principal that task 5 will let reach admin paths.

- **One shared check: `taskmaster.serve.auth.is_host_local(scope) -> bool`** (orchestrator ruling a).
  It is a pure function of the ASGI scope and reads no body. It has two callers:
  - `RequirePrincipalMiddleware`'s bootstrap-token rule (here);
  - 006's viewer login, so the operator token can't sign in to the viewer from a public origin.

  Task 5's admin check reuses it too. No caller keeps its own copy. It lives in `auth.py`, not
  `app.py`, so the viewer can import it without importing the MCP app.
- `RequirePrincipalMiddleware` (`app.py`) already runs after authentication. For a principal with
  `key_id == "operator"` it also requires `is_host_local(scope)`. All three of these must hold:
  - `scope["client"][0]` is a loopback address. uvicorn's proxy-headers handling rewrites this from
    `X-Forwarded-For` when the peer is `127.0.0.1`, so a request through Caddy shows the remote
    address here.
  - None of the headers `x-forwarded-for`, `forwarded` or `x-real-ip` is present. Caddy's
    `reverse_proxy` always sets `X-Forwarded-For`, and by default it replaces a client-sent one.
  - The `Host` header's name is `127.0.0.1`, `localhost` or `[::1]`. Through Caddy it is the public
    domain.
- Otherwise the answer is 403 `{"error": "the operator bootstrap token works only on the host; use
  a key from taskmaster serve add-user"}`, without reading the body, and the log line is
  `refused operator token from <peer>: not host-local`. The peer comes from `X-Forwarded-For` and
  is therefore caller-controlled, so it goes through 001's `serve.app.log_safe`, which escapes
  control characters.
- Each signal on its own can be defeated by a different proxy setup. Requiring all three means a
  proxy has to strip the forwarding headers *and* rewrite `Host` to loopback before the check
  fails open, and the deployment how-to (016) tells the operator not to configure Caddy that way.
- **The operator's own laptop agent** uses `taskmaster serve add-user <me> --role operator`. Role
  `operator` on a member key is recorded and exposed in `Principal.role`, for 003's "a caller may
  renew or release only its own sessions, unless it is the operator". It never unlocks admin
  methods: task 5 restricts those to the bootstrap principal on the host (`key_id == "operator"`
  and host-local), never to a role.
- `operator` is added to `team_identity.RESERVED_HANDLES`, so `add-user operator` is refused, the
  handle bootstrap never derives it, and `profile.create` refuses it. An existing profile named
  `operator` in some store (`users/operator/profile.yaml`, from a solo or team store made before
  this task) is left alone, because the reserved set is checked only when a profile is created.
  The consequences:
  - Such a person can't get a key under that handle, because `add-user operator` is refused. The
    operator picks a new handle; `backlog_team_merge_handles` can fold the old one in later.
  - Their earlier history keeps the handle.
  - Bootstrap and host-local writes are stamped `operator` with `clone_id: "operator"`. In a store
    that has such a profile, those stamps aren't that person's writes. The key id tells them apart.
  - `serve` logs a WARNING at start when a live `operator` profile exists: *"profile 'operator'
    predates the reserved handle; bootstrap-token writes are not that person's"*.
- The operator token stays stored in plain text, as 001 wrote it. It is host-only, it is readable
  only by the service user, and `serve token` must be able to show it. Anyone who can read it can
  already read `store.db` directly.

## 8. Per-request identity: from the HTTP tool call to the actor

**The path today.** A tool runs in FastMCP's thread pool (contextvars are copied by
`anyio.to_thread.run_sync`, `fastmcp/utilities/async_utils.py:31`). It reaches the in-process
coordinator through `Client.execute` → loopback IPC `execute` → `Coordinator.submit(envelope)`, with
origin `LOCAL`. `get_access_token()` reads the current HTTP request's `scope["user"]` per MCP
message (`fastmcp/server/dependencies.py:481-489`), so in a tool call it is that request's key,
not the session's first one.

**D8. The key id travels as the IPC origin, and the coordinator resolves it.**

- **Client side.** `taskmaster/coordinator/client.py` gains a module hook,
  `ORIGIN_PROVIDER: Callable[[], str | None] | None = None`, next to 001's `AUTOSTART_ALLOWED`.
  `Client.execute` adds `origin={"principal": value}` to the IPC message when the provider returns a
  value. The daemon installs `taskmaster.serve.auth.request_key_id` in `start()` and restores the
  old value in `close()`. **That assignment is the only one ever made to `ORIGIN_PROVIDER`.**
- **Per-request sources (re-review of ruling b).** `ORIGIN_PROVIDER` is one process-wide callable,
  so it must never hold a per-request value. 002 defines both per-request sources in
  `taskmaster/serve/auth.py`, and the installed provider chains them on each call:

  ```python
  VIEWER_KEY_ID: ContextVar[str | None] = ContextVar("taskmaster_viewer_key_id", default=None)

  def request_key_id() -> str | None:          # what the daemon installs as ORIGIN_PROVIDER
      return current_key_id() or VIEWER_KEY_ID.get()

  @contextmanager
  def viewer_principal(key_id: str):           # for 006's viewer handlers
      token = VIEWER_KEY_ID.set(key_id)
      try:
          yield
      finally:
          VIEWER_KEY_ID.reset(token)
  ```

  - `current_key_id()` is the MCP source. It returns the principal's `key_id` inside an
    authenticated MCP HTTP request. It reads FastMCP's per-request context, which is a contextvar
    copied into each tool's worker thread. Outside one it returns `None`: internal workers, tests,
    and the viewer.
  - It **raises** `PermissionError("authenticated principal missing")` inside an HTTP request
    whose user has no taskmaster claims. `RequirePrincipalMiddleware` makes that unreachable, so
    reaching it is a bug, and it fails closed rather than writing as the host.
  - `VIEWER_KEY_ID` is the viewer source. Its default is `None`, so with no viewer request in
    context, `request_key_id()` falls back to `LOCAL`, as before.
  - MCP wins when both are set. That can't happen in practice: viewer handlers don't run inside an
    MCP request.
- **The contract with 006 (ruling b, as clarified).** Viewer-write attribution is 006's, and 006
  only ever does this: inside each viewer request handler that writes, after its login check, it
  runs the write under `with auth.viewer_principal(principal.key_id):`.
  - That sets the contextvar for that request's context only, and resets it on exit.
  - 006 never assigns `client.ORIGIN_PROVIDER`, `VIEWER_KEY_ID`'s default, or any module-level or
    process-wide value.
  - If the viewer's handler thread does the write on another thread, it propagates the context
    (`contextvars.copy_context().run`). It doesn't set the value there by hand.

  Then two concurrent requests, one MCP and one viewer, or two viewer users, each stamp their own
  `key_id`. Neither can be credited with the other's write. 002 adds no viewer code.
- **Coordinator side.** `Coordinator.dispatch`'s `execute` branch becomes
  `self.submit(message.get('envelope'), origin=self._origin(message))`. The base `_origin` returns
  `LOCAL` and ignores the field, so a session coordinator behaves exactly as before.
  `ServeCoordinator._origin` validates `message.get("origin")`: absent means `LOCAL`; otherwise it
  must be `{"principal": s}`, where `s` fully matches `operator|[0-9a-f]{16}`, else `ValueError`.
  This is the only `service.py` change besides D9's one argument.
- **The source.** `ServeActorSource(registry)` implements `IdentitySource`:
  - origin `LOCAL` or principal `operator` gives `("operator", "operator")`;
  - an active member key id gives `(record.handle, key_id)`;
  - an unknown or revoked id raises `IdentityRefused` (D6, point 4).
  `ServeCoordinator(root, registry=...)` passes it to `Coordinator(..., actor_source=...)`, which is
  exactly the seam identity-002 D2 built and tested with a fake token source.
- **The client's claims are ignored.** `ActorResolver.stamp` already overwrites `handle` and
  `clone_id` and keeps only the advisory `agent`. Nothing the client sends (envelope `actor`, tool
  arguments, headers, `clientInfo`) can name a handle. The origin is set by the daemon's own
  `Client` from the authenticated request, never from the remote caller's payload.
- **Trust boundary.** The IPC listener is loopback-only behind the discovery token in the
  owner-only `local/coordinator/`. A process that can read that token runs as the service user, and
  can already open `store.db` for writing, so letting it name an origin grants nothing new.
  Task 9 refuses host-local sessions.

**D9. A server-mode identity gate.**

`IdentityGate.admit(connection, request)` becomes `admit(connection, request, origin=LOCAL)`, and
`_writer` passes `work.origin`. The base class ignores the new argument. In
`taskmaster/serve/identity.py`, `ServeIdentityGate(IdentityGate)` overrides `admit`, and
`ServeCoordinator.__init__` installs it in place of `self.team_identity`:

1. Like the base: not attribution-bearing → `False`; not team mode (`_team_mode`, unchanged) →
   `False`; a replayed `request_id` → `False` (identity-001 D15).
2. Resolve the origin through the same registry as D8:
   - `LOCAL` or `operator` raises `IdentityRefused("Refused: team mode attributes every shared
     change to a person, and the operator bootstrap token and host-local callers are not one. Use
     your own key from `taskmaster serve add-user`. Nothing was written.")`.
   - A revoked or unknown id raises the D6 refusal.
3. **Profile: auto-created, or an existing one reused.** It mirrors
   `IdentityGate._ensure_profile`, with every input taken from the key's record:
   - a live `profile` row with the handle → `False`;
   - a deleted row → `refused_profile("profile 'ann' was deleted from this backlog")`;
   - no row → upgrade the store to the current schema (as the base does), then `profile.create`
     with `{handle, name, emails, created: record.created}`. The envelope has
     `caller_scope="coordinator-identity"`, `request_id=f"profile-{key_id}-{handle}"` (with the
     base's `-rN` suffix after a recreation) and `actor = {handle, "taskmaster", key_id}`. The gate
     returns `True`, so the writer sets `export_needed`.
4. It never calls `read_git_identity`, `socket.gethostname`, `write_identity` or `disk_profiles`:
   server mode has no per-clone identity file, and no teammate profiles arrive on disk, because
   there is no sync.

Why auto-create rather than require a profile first:

- It is team-identity-001's rule: the coordinator creates the profile on the person's first
  attribution-bearing write, and no one hand-writes profiles.
- `add-user` stays a file-only operation that never needs the coordinator. That avoids an admin
  write method (task 5's surface), and it works with the daemon stopped.
- A profile that already exists, for example from a migrated solo or team store, is reused
  unchanged. The operator chose the handle, so the operator is asserting that it is the same
  person.

**Team mode itself is unchanged.** On a served store that isn't in team mode, the gate does
nothing, and the actor lands only in receipts (schema 3; identity-002 Step 6). `serve` logs a
WARNING at start in that case: *"team mode is off for <root>: writes are attributed in receipts
only; turn on team mode to record people on every event"*. Task 14's `serve init` turns team mode
on.

## 9. Audit

**D10. The key id is the actor's `clone_id`.** Rev 3.3 §4 already defines `clone_id` as *"an opaque
origin id the resolver supplies; in server mode it names the client"*. A key id fits its shape
(`[A-Za-z0-9][A-Za-z0-9_-]{7,63}`; 16 hex characters, and `operator` is 8). So every receipt's
`outcome_json` and every schema-5 `domain_events.actor` records `{handle, agent, clone_id: key_id}`,
with no schema change. After a rotation, events show which of a person's keys acted.

The log, `taskmaster.serve` (journald and `serve.log`):

- the first successful authentication per key id per process, at INFO:
  `key 3f9a0c1e5b7d2a46 (ann) authenticated`;
- each refusal, at WARNING, with peer, method, path, and one reason: `missing`, `malformed`,
  `unknown key 3f9a…`, `revoked key 3f9a…`, `wrong secret for key 3f9a…`, or `operator token not
  host-local`. The key id appears only when the token matched `KEY_RE`, so it is 16 hex characters
  and can't inject log text;
- a registry reload: `principals reloaded: 3 active keys; no longer active: 3f9a0c1e5b7d2a46`;
- each session ended by the sweep: `ended session <first 8 of id> of revoked key 3f9a…`.

**Never logged, returned or stored**: a key or its secret part, a hash, or the `Authorization`
header. 002's own refusal texts (`IdentityRefused`, the 401 and 403 bodies, CLI errors) contain no
key, no hash and no filesystem path. Sanitizing *other* error text is task 6.

Key lifecycle (`created`, `revoked`, `revoked_reason`) is recorded in `principals.json` itself.
The CLI doesn't write to `serve.log`, which the daemon's `RotatingFileHandler` owns: two processes
rotating one file corrupt it.

## 10. Seams with 003, 005 and 006

**What 003 picks up.** 002 delivers:

- `current_principal()` → `Principal(handle, role, key_id)` in every tool call;
- one MCP session ↔ one key (D6, point 2);
- the `Origin(principal=key_id)` path to the coordinator;
- `ServeActorSource` and the registry for handle lookups.

003 owns:

- per-connection `SESSION_ID = <handle>-<nonce>` (a contextvar keyed by MCP session id, in place of
  the module global at `backlog_server.py:628`);
- the `caller_scope` prefix;
- claim-session ownership checks with the operator override (read `Principal.role`);
- same-handle adoption;
- the agent per connection. Today `_CLIENT_AGENT` is captured from the first `initialize` of the
  process, so until 003 every principal's actor carries the first client's agent. That is advisory
  and wrong only in that one field.

Until 003, every HTTP caller still shares one claim session (001 known gap); 002 changes only who
writes are attributed to.

**004** scopes receipts, `cancel` and `receipt` lookups by principal. Until then, a principal that
knows another's `caller_scope` and `request_id` can read that receipt.

**005.** Admin methods go to the bootstrap principal on the host only. That is `key_id ==
"operator"` plus `is_host_local(scope)`, which 002 already enforces for the whole token. Never a
member key with role `operator`.

**006 (planned in parallel).** 002 doesn't add rate limits, pre-auth limits, timeouts, error
sanitizing, viewer auth or viewer-write attribution. The seams 006 builds on (orchestrator
rulings a–c):

- **Per-principal rate limits and in-flight caps** go in `build_app(middleware=[...])`, after
  auth. They key on `scope["user"].access_token.subject`, which is exactly the `key_id`, in the
  stable form §6 fixes.
- **Pre-auth limits** use 001's `outer_middleware` and `uvicorn_options`. 002's authentication
  cost is one `stat`, one dict lookup and one SHA-256, so it needs no special treatment.
- **Viewer login** exchanges a key for a cookie through `KeyAuthenticator.authenticate`. When the
  principal is the bootstrap operator, it also requires `serve.auth.is_host_local(scope)` (D7), so
  the operator token can't sign in from a public origin.
- **Viewer-write attribution is 006's.** It wraps each writing viewer request in
  `auth.viewer_principal(key_id)`, which sets 002's `VIEWER_KEY_ID` contextvar for that request
  only. It never assigns `client.ORIGIN_PROVIDER` or any global value (D8). Until 006 ships, viewer writes on the host are `LOCAL`, so in team mode they are
  refused by D9 (§13 gap).
- **Log lines.** Every 002 log line that includes request-derived text uses 001's
  `serve.app.log_safe`. That covers the peer, method, path, a handle derived from a key id, and an
  exception type. Key ids and handles come from the validated file, but they go through it too, for
  one rule.
- **Error sanitizing** may rewrite 002's refusal texts, but they are already free of secrets and
  paths. The 401 body stays identical for every key failure (D4), and 006 shouldn't add detail
  that tells an unknown key from a revoked one.

## 11. Rollback

- `revoke-user` every member, or delete `local/serve/principals.json`; the daemon refuses member
  keys from the next request on.
- Revert the commits. Profiles the gate created stay, as ordinary profiles, and actors already
  recorded keep their key ids.
- No schema change is made beyond the existing opt-in upgrade the gate already performs.

## 12. Risks

- **R1. SDK internals.** D6 relies on `StreamableHTTPSessionManager._session_owners`, on each
  transport's `idle_scope`, and on the owner check comparing `subject`. `build_app` fails loudly if
  `_session_owners` is missing, and two tests pin the binding and the sweep. The pin moves to
  `mcp>=1.28,<2`, the version verified here. That is a header, `pyproject.toml` and `uv.lock` edit
  like 001's Step 0, and the resolved version stays 1.28.1.
- **R2. Merge overlap with team-server-001 review fixes.** 002 edits `auth.py`, `app.py`,
  `daemon.py` and `cli.py`, all of which are under review in 001. Rebase onto 001's final tip
  before Step 3. The step tests catch drift.
- **R3. Merge overlap with team-server-006**, which will also edit `app.py` (middleware) and
  probably `RequirePrincipalMiddleware`'s refusal text. 002 touches that middleware only to add
  the D7 branch. Whichever branch lands second keeps both.
- **R4. Shared `service.py`.** The two one-line edits (`_origin`, the `admit` argument) are in
  code that team-collab branches also change. Keep both edits minimal.
- **R5. Windows replace contention** (§4) is retried; production is Linux. A Linux run is task 16.
- **R6. A proxy that strips forwarding headers and rewrites `Host` to loopback** would make D7 fail
  open. Documented for 016. A mitigating fact: the operator token still never leaves the host by
  design.

## 12a. Notes for 016 (deployment how-to and Linux lane; review M6)

- **Run `add-user`, `rotate-user`, `revoke-user` and `list-users` as the service user**:
  `sudo -u taskmaster …`. Otherwise `principals.json` or its lock is created under another owner,
  and the daemon refuses the file (D6) or the CLI refuses it next time.
- **Test with real Caddy**, not only with forged headers. Check that:
  - a request through Caddy shows the remote address in `scope["client"]` and carries
    `X-Forwarded-For`;
  - the bootstrap token through Caddy gets 403 (D7).
- **The hop-by-hop case.** A client sending `Connection: X-Forwarded-For` (or `Connection: Host`)
  asks the proxy to drop that header before forwarding. The Linux lane must show that Caddy still
  sets its own `X-Forwarded-For` and the public `Host` on such a request, so the bootstrap token
  remains refused. If Caddy honours the strip, the how-to must add `header_up X-Forwarded-For
  {remote_host}`, and D7's `Host` check is the remaining guard.
- **Never set Caddy's `log_credentials`.** Caddy's access log would then record the
  `Authorization` header, and so every key.

## 13. Known gaps left by design

- In team mode, writes through the host-loopback viewer are refused until 006 wraps them in
  `auth.viewer_principal(key_id)`. Reads still work.
- Only `Client.execute` carries the origin. `sync`, `git_run`, `linear_*` and `receipt` over IPC
  stay `LOCAL`; task 5 fences the admin ones and task 4 scopes `receipt`.
- One key per person (D5).
- The agent field is per process until 003.
