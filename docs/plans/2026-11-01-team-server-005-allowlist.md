<!-- User intent: the executable, test-first plan for team-server-005. A teammate's key reaches only what the MCP
     tools need from the host; every host-admin coordinator method and the Linear probe/bootstrap run only for the
     operator on the host; and no caller can make the daemon read or send an environment secret it names. -->

# team-server-005: remote method allowlist (plan, 2026-11-01)

**Task.** `team-server-005` (server mode, addendum §11 task 5).
**Design.** `docs/specs/2026-11-01-team-server-005-allowlist-design.md`. Its decisions D1 to D10 are
binding here, as are the user's rulings U1 to U5 (design §12). Part B (design §9, Steps 6 and 7) is
in scope (U1) and starts only after 006 provides `taskmaster/serve/tracked.py` (Step 6 precondition).
**Worktree.** `C:\Users\gruku\Files\Claude\taskmaster\.worktrees\team-server-005`, branch
`feature/team-server-005`, base `4aabb2d` (001 and 002 merged into `feat/team-collab`) plus this
plan's commit.
**Executor.** One engineer, test-first. For each step:

1. Write the step's tests.
2. Run them and watch them fail for the stated reason (§4).
3. Implement.
4. Run the step's tests and the regression subset (§5).
5. Commit with the step's message.

Steps 0 to 5 (Part A) are ordered by dependency; run them in order. Steps 6 and 7 (Part B) follow,
once their precondition holds. If Part A is done and 006's `serve/tracked.py` hasn't landed, stop
after Step 5 and report that Part B is waiting on it. Every decision is already made: if something
here seems wrong, stop and report it rather than redesigning.

**Done when:**

- Inside the serve daemon, any coordinator call other than `status`, `execute`, `flush`, `sync`
  (without `take_file` or `worktree`), `sync_job` and `linear_retry` is refused unless the request
  being served is the bootstrap operator on the host. A host process (no origin) still reaches them.
- A teammate's `backlog_resolve_conflict(take="file")`, `backlog_linear(probe)` and
  `backlog_linear(bootstrap_apply)` are refused with the design's D9 text. Nothing changes on disk or
  in the store, and no environment variable is read.
- On a server, `linear_bootstrap` stores only `TASKMASTER_LINEAR_TOKEN` and refuses any other
  `token_env`, for the operator too.
- A tool that loses its request context (a bare thread) cannot reach an admin method.
- `shutdown` and `retire` keep 001's refusals; solo coordinators, solo tools and host CLIs behave
  exactly as before.
- `receipt`, `cancel` and `sync_status` are operator-only (U2); existing `linear.yaml` workspaces that
  name other variables still work, with no warning (U5).
- On a server, a task's or epic's `docs` path is read, or imported, only when it is a git-tracked
  file inside the project and outside `.taskmaster/local/` (Part B).

**Out of scope:** the design's §8 table. **Not touched:** `taskmaster/coordinator/service.py`,
`serve/app.py`, `serve/config.py`, `serve/cli.py`, the store and the projection code.

---

## 1. Files

Create:

| File | Header (`# User intent: …`, first line) |
|---|---|
| `taskmaster/coordinator/allowlist.py` | `on a team server, remote teammates reach only what the MCP tools need from the coordinator; every host-admin method runs only for the host operator, and the Linear token's variable is never named by a caller.` |
| `tests/test_serve_allowlist.py` | `every coordinator method is classified once, and both the serve-process client and the serve coordinator refuse admin methods to anyone but the host operator, failing closed when the principal is unknown.` |
| `tests/test_serve_allowlist_tools.py` | `a teammate's tools cannot adopt host files, probe or bootstrap Linear, or name an environment variable; the operator can, with the fixed token name; solo is unchanged.` |
| `tests/test_serve_allowlist_e2e.py` | `over real HTTP, any tool, today's or a new one, reaches an admin coordinator method only for the operator on the host, and never from a thread that lost its request.` |
| `tests/test_serve_doc_reads.py` (Part B) | `on a team server a task's docs path reads only git-tracked project files, so no caller can read host secrets through a document section.` |

Modify:

| File | Change |
|---|---|
| `taskmaster/coordinator/client.py` | `LOG`; `serving()`; `served_principal()`; `_origin_arguments()`; origin on every `call` and `sync_job`; `execute` delegates to `call`; the gate at the top of `_send` (Step 1) |
| `taskmaster/serve/daemon.py` | `ServeCoordinator.dispatch`: the gate and the `linear_bootstrap` token rule (Step 2); one import |
| `taskmaster/native_routing/overview.py` | `linear`: the probe and bootstrap_apply gate and the token-name mapping (Step 3) |
| `taskmaster/serve/auth.py` | `current_key_id`: the host-local re-check for the operator (Step 4) |
| `taskmaster/taskmaster_v3.py` (`read_doc_section`), `taskmaster/native_routing/documents.py` (`_project_file`) (Part B) | in the serve process, ask 006's `tracked_file_allowed(bs.ROOT, path)` and refuse otherwise (Step 6). No new path-checking code: `taskmaster/serve/tracked.py` is 006's |

---

## 2. API (exact)

### 2.1 `taskmaster/coordinator/allowlist.py`

```python
OPERATOR = "operator"                          # == serve.auth.OPERATOR_KEY_ID (a test holds them equal)
SERVER_LINEAR_TOKEN_ENV = "TASKMASTER_LINEAR_TOKEN"

METHODS = frozenset({"retire", "status", "sync", "sync_job", "sync_status", "linear_bootstrap", "linear_retry",
                     "receipt", "execute", "flush", "cancel", "git_run", "git_status", "git_recover", "shutdown"})
REMOTE = frozenset({"status", "execute", "flush", "sync", "sync_job", "linear_retry"})
SERVER_REFUSED = frozenset({"shutdown", "retire"})      # 001 refuses them for every caller (design D5)
OPERATOR_ONLY = METHODS - REMOTE - SERVER_REFUSED       # sync_status, receipt, cancel, git_run, git_status,
                                                        # git_recover, linear_bootstrap

ADMIN_REFUSED = ("{what} is a host operator action on a team server: run it on the host (the taskmaster CLI, "
                 "or the operator bootstrap token over loopback); nothing was changed")
TAKE_FILE_WHAT = "adopting a flagged file from the host's disk (sync take_file)"
TAKE_FILE_HINT = '; backlog_resolve_conflict(take="store") is available to everyone'
WORKTREE_WHAT = "syncing a host checkout (sync worktree)"
UNKNOWN_WHAT = "an unclassified coordinator method"
WHAT = {
    "linear_bootstrap": "adding a Linear workspace (linear_bootstrap)",
    "git_run": "managed Git on the host clone (git_run)",
    "git_recover": "managed Git recovery on the host clone (git_recover)",
    "git_status": "reading the host clone's managed Git state (git_status)",
    "receipt": "reading a request's receipt by id (receipt)",
    "sync_status": "reading a sync's state by id (sync_status)",
    "cancel": "cancelling a queued request by id (cancel)",
}
LINEAR_TOKEN_ENV_REFUSED = (
    "on a team server the Linear token is read only from $TASKMASTER_LINEAR_TOKEN, which the operator sets in "
    "the daemon's environment; pass token_env=\"TASKMASTER_LINEAR_TOKEN\" or leave it empty; "
    "linear.yaml was not changed")

def principal_of(origin) -> str | None
    # {"principal": <str>} (exactly that key) -> the str; anything else -> None. Shape validation stays 002's _origin.
def host_only_sync(arguments) -> str | None
    # For method "sync": TAKE_FILE_WHAT when arguments.get("take_file", False) is not False (fail closed on any
    # non-False value); WORKTREE_WHAT when arguments.get("worktree") is not None; else None. take_file wins.
def refusal(method, arguments, principal) -> str | None
    # None when principal == OPERATOR, or method in SERVER_REFUSED, or (method in REMOTE and not host_only_sync).
    # Else ADMIN_REFUSED.format(what=...) with what = host_only_sync(...) for "sync" (plus TAKE_FILE_HINT for
    # take_file), WHAT[method] for OPERATOR_ONLY, UNKNOWN_WHAT otherwise. `arguments` is any mapping (the message).
def action_refusal(what, principal) -> str | None
    # None when principal == OPERATOR, else ADMIN_REFUSED.format(what=what). For tool actions (design D6).
def server_token_env(token_env) -> tuple[str, str | None]
    # The tool side (design D7): "" or SERVER_LINEAR_TOKEN_ENV -> (SERVER_LINEAR_TOKEN_ENV, None);
    # anything else -> (token_env, LINEAR_TOKEN_ENV_REFUSED).
def bootstrap_entry_refusal(entry) -> str | None
    # The coordinator side: None only when entry is a dict whose "token_env" == SERVER_LINEAR_TOKEN_ENV exactly;
    # else LINEAR_TOKEN_ENV_REFUSED.
def log_method(method) -> str          # method if it is in METHODS, else "other"
def log_key(principal) -> str          # principal if it fullmatches r"operator|[0-9a-f]{16}", "-" for None, else "invalid"
```

No imports from `taskmaster.serve`, `client` or `service`; only `re`.

### 2.2 `taskmaster/coordinator/client.py` (changes)

```python
import logging
from . import allowlist
LOG = logging.getLogger('taskmaster.coordinator.client')   # propagates to the handlers 001 puts on taskmaster.coordinator

def serving() -> bool:
    # ORIGIN_PROVIDER is not None. Never calls the provider (Part B uses it inside document reads).

def served_principal() -> tuple[bool, str | None]:
    # (False, None) when ORIGIN_PROVIDER is None; else (True, ORIGIN_PROVIDER()). A raising provider propagates.

def _origin_arguments(arguments: dict) -> dict:
    # Provider None: `arguments` unchanged (the same object).
    # Provider installed: "origin" in arguments -> ValueError("origin is set by the serve daemon, never by a caller");
    #   principal = provider() (raising propagates); principal None -> arguments unchanged;
    #   else dict(arguments, origin={"principal": principal}).

class Client:
    def call(self, method, *, wait=None, **arguments):
        arguments = _origin_arguments(arguments)        # first statement: once, before the retry loop
        ...                                             # unchanged
    def execute(self, envelope):
        # validation and the request_id check unchanged; the provider block is deleted;
        # return self.call('execute', envelope=request, visibility=self.visibility) inside the existing try/except.
    def sync_job(self, *, deadline, sync_id=None, files=None, margin=1.5):
        extra = _origin_arguments({})                   # in the calling thread, before the worker starts
        # attempt() calls self._sync_job(deadline, sync_id, files, margin, extra)
    def _sync_job(self, deadline, sync_id, files, margin, extra):
        # unchanged, except: self._send(record, 'sync_job', sync_id=..., files=..., wait_seconds=..., **extra)
    def _send(self, record, method, *, wait=None, identity=None, **arguments):
        if ORIGIN_PROVIDER is not None:                 # first statement: only the serve daemon installs a provider
            principal = allowlist.principal_of(arguments.get('origin'))
            text = allowlist.refusal(method, arguments, principal)
            if text is not None:
                LOG.warning('refused %s in the serve process for key %s',
                            allowlist.log_method(method), allowlist.log_key(principal))
                raise ValueError(text)
        ...                                             # unchanged
```

### 2.3 `taskmaster/serve/daemon.py` (`ServeCoordinator.dispatch`)

```python
from taskmaster.coordinator import allowlist
from .app import log_safe        # daemon already imports build_app from .app

    def dispatch(self, message):
        if isinstance(message, dict):
            method = message.get("method")
            if method == "shutdown":                              # 001, unchanged
                check_handshake(message.get("identity"), self.identity, self.nonce, self.build)
                raise ValueError(SERVE_SHUTDOWN_REFUSED)
            check_handshake(message.get("identity"), self.identity, self.nonce, self.build,
                            any_build=method == "retire")         # the base dispatch's own order
            origin = self._origin(message)                        # 002; "invalid origin" on any method now
            principal = allowlist.OPERATOR if origin is LOCAL else origin.principal
            text = allowlist.refusal(method, message, principal)
            if text is None and method == "linear_bootstrap":
                text = allowlist.bootstrap_entry_refusal(message.get("entry"))
            if text is not None:
                LOG.warning("refused %s for key %s", log_safe(method), log_safe(principal))
                raise ValueError(text)
        return super().dispatch(message)
```

### 2.4 `taskmaster/native_routing/overview.py` (`linear`)

```python
from taskmaster.coordinator import allowlist
from taskmaster.coordinator import client as coordinator_client

# First lines of linear(...), before `if action == "probe"`:
    if action in ("probe", "bootstrap_apply"):
        serving, principal = coordinator_client.served_principal()
        if serving:
            refused = allowlist.action_refusal(f"backlog_linear {action}", principal)
            if refused is None:
                token_env, refused = allowlist.server_token_env(token_env)
            if refused is not None:
                return json.dumps({"error": refused})
```

### 2.5 `taskmaster/serve/auth.py` (`current_key_id`)

```python
def current_key_id() -> str | None:
    from fastmcp.server.dependencies import get_http_request
    try:
        request = get_http_request()
    except RuntimeError:
        return None
    principal = current_principal()
    if principal is None:
        raise PermissionError("authenticated principal missing")
    if principal.key_id == OPERATOR_KEY_ID and not is_host_local(request.scope):
        raise PermissionError("the operator bootstrap token works only on the host")
    return principal.key_id
```

### 2.6 Part B: the served-path rule

```python
# taskmaster/coordinator/allowlist.py (addition)
SERVED_DOC_REFUSED = "{path} is not a git-tracked project file; a team server reads only tracked files"

# taskmaster/serve/tracked.py is 006's (precondition). 005 relies on exactly this signature and contract (design §9):
#   tracked_file_allowed(root: Path, rel_path: str) -> bool
#   False for absolute, drive, UNC, NUL or backslash paths; resolved = (root / rel_path).resolve() must lie inside
#   root.resolve(); the RESOLVED root-relative POSIX form must not be under ".taskmaster/local/" or ".git/" and must
#   be in TrackedFiles(root).get(); any OSError/ValueError -> False.

# The glue, identical at both call sites; no other path logic is added:
def _served_root_refusal(path: str) -> str | None:        # private helper in each module, or one in documents.py
    from taskmaster.coordinator import client as coordinator_client
    if not coordinator_client.serving():                   # never calls the provider
        return None                                        # solo: unchanged
    import sys
    from taskmaster.serve.tracked import tracked_file_allowed   # lazy, only after serving() is true
    server = sys.modules.get("taskmaster.backlog_server")
    root = getattr(server, "ROOT", None)
    if root is None:
        return allowlist.SERVED_DOC_REFUSED.format(path=path)  # fail closed
    return None if tracked_file_allowed(Path(root), path) else allowlist.SERVED_DOC_REFUSED.format(path=path)

# taskmaster_v3.read_doc_section(doc_path, project_root): FIRST statement, whatever project_root is (None included):
#   text = _served_root_refusal(doc_path); if text: return (f"(refused: {text})",
#   {"source": "refused", "path": doc_path, "imported": False}).  When allowed in the serve process, the file read
#   is (Path(bs.ROOT) / doc_path), never project_root / doc_path or Path(doc_path).
# documents._project_file(root, path): first statement: text = _served_root_refusal(path); if text: raise _Unreadable(text).
#   When allowed in the serve process, read (Path(bs.ROOT) / path).
```

---

## 3. TDD steps

Shared fixtures: `root` from `test_native_service_build` (a native store); `serve_owner` from
`test_serve_coordinator`; `twins` from `test_native_linear_bootstrap`; `make_twins`, `_seed`, `_flag`,
`_hand_edit`, `_flagged`, `_path` and `REL` from `test_native_resolve_take_file`; `launch` from
`test_serve_principals_e2e`; `call_tool` from `serve_helpers`. A member id below is a
`[0-9a-f]{16}` string such as `"a" * 16`; tests that need a real key use `add_user`.

### Step 0: the table (`coordinator/allowlist.py`)

Tests in `tests/test_serve_allowlist.py`:

- **`test_every_dispatched_method_is_classified`** (design D10):
  - Read `inspect.getsource(Coordinator.dispatch)` and collect `re.findall(r"method == '(\w+)'", src)`.
  - The set equals `allowlist.METHODS`.
  - `REMOTE`, `SERVER_REFUSED` and `OPERATOR_ONLY` are pairwise disjoint and their union is `METHODS`.
- `test_operator_constant_matches_serve`: `allowlist.OPERATOR == serve.auth.OPERATOR_KEY_ID`.
- **`test_refusal_table`** (parametrized over every method in `METHODS` plus `"bogus"` and `None`, and
  over principals `"a"*16`, `None`, `"operator"`):
  - The operator always gets `None`.
  - `SERVER_REFUSED` methods always get `None`.
  - For the member and `None`: `REMOTE` methods get `None`; `OPERATOR_ONLY` methods get text
    containing `WHAT[method]` and `"nothing was changed"`; `"bogus"` and `None` get text containing
    `UNKNOWN_WHAT`.
- `test_host_only_sync` (member principal):
  - `{"take_file": True, "files": ["x"]}` gives text with `TAKE_FILE_WHAT` and ending with
    `TAKE_FILE_HINT`.
  - `{"take_file": 1}`, `{"take_file": "yes"}` and `{"take_file": None}` are refused too.
  - `{"worktree": "/srv/x"}` gives `WORKTREE_WHAT`.
  - `{}`, `{"take_file": False}` and `{"take_file": False, "worktree": None}` are allowed.
- `test_server_token_env`: `""` and `"TASKMASTER_LINEAR_TOKEN"` give `("TASKMASTER_LINEAR_TOKEN",
  None)`. `"OPS_LINEAR_TOKEN"`, `"AWS_SECRET_ACCESS_KEY"` and `"taskmaster_linear_token"` give the
  refusal.
- `test_bootstrap_entry_refusal`: only `{"token_env": "TASKMASTER_LINEAR_TOKEN", ...}` gives `None`;
  `None`, `"x"`, `{}`, `{"token_env": ""}` and `{"token_env": "OPS"}` give `LINEAR_TOKEN_ENV_REFUSED`.
- `test_log_helpers`: `log_method("git_run") == "git_run"`, `log_method("x\n[fake]") == "other"`;
  `log_key("a"*16)`, `log_key("operator")`, `log_key(None) == "-"`, `log_key("../x") == "invalid"`.
- `test_allowlist_imports_nothing_from_serve`: in a fresh interpreter (`subprocess.run([sys.executable,
  "-c", "import taskmaster.coordinator.allowlist, sys; print(any(m.startswith('taskmaster.serve') for m in
  sys.modules))"])`), the output is `False`.

Commit: `feat(coordinator): one table classifies every coordinator method for team servers`.

### Step 1: the serve-process client (`client.py`)

Tests in `tests/test_serve_allowlist.py`. Fixture `owner`: a plain `Coordinator(root).start()` (the
client side doesn't need a serve coordinator), closed in teardown. Two spies:

- a `_send` spy, as in 002's `test_client_sends_the_origin_only_with_a_provider`, records
  `(method, arguments)` for every call and then delegates to the real `_send`;
- a dispatch spy, `monkeypatch.setattr(owner, "dispatch", recorder)` delegating to the original,
  records the methods the coordinator actually received. "Nothing was sent" is asserted on it.

Module constant `ARGS`, shared by Steps 1, 2 and 5. Past the gate, each entry stops at the method's
own validation, so nothing touches Git or `linear.yaml`:

```python
ARGS = {
    "git_run": dict(kind="not-a-kind", caller_scope="t", request_id="r", timeout=5),
    "git_recover": dict(acknowledge_quiescent="x"),
    "git_status": {},
    "receipt": dict(caller_scope="t", request_id="r"),
    "sync_status": dict(caller_scope="t", request_id="r"),
    "cancel": dict(caller_scope="t", request_id="r"),
    "linear_bootstrap": dict(entry={"alias": "", "team_id": "", "token_env": "TASKMASTER_LINEAR_TOKEN"},
                             default_workspace=False),
    "sync:take_file": dict(files=["tasks/none.md"], take_file=True, import_files=True, through=0,
                           caller_scope="t", request_id="r", timeout=5),
    "sync:worktree": dict(worktree="<root>/no-such-checkout", files=None, take_file=False, import_files=True, through=0,
                          caller_scope="t", request_id="r", timeout=5),
}   # "sync:*" keys call method "sync"; "<root>" is replaced by str(root)
```

- **`test_origin_travels_on_every_method_with_a_provider`**:
  - Provider `lambda: "a"*16`.
  - `client.status()`, `client.flush(0)` and `client.sync(files=None)` each reach `_send` with
    `origin == {"principal": "a"*16}`, except the internal `_probe` `status`, which has no origin.
  - `client.execute(...)` sends the origin once per call, and a transport retry re-sends the same
    one: make the spy raise `ConnectionResetError` on the first `_send` whose method is `execute`
    (the probe's `status` goes first and passes), and both `execute` attempts carry an equal origin.
- `test_no_provider_sends_what_it_sent_before`: with the provider `None`, `execute`, `sync`, `status`
  and `git_status` send no `origin` key, and `execute`'s arguments are exactly `{"envelope",
  "visibility"}` (002's assertion, kept).
- **`test_a_caller_supplied_origin_is_refused_in_the_daemon`**:
  - With a provider installed, `client.call("execute", envelope=..., visibility="native",
    origin={"principal": "operator"})` raises `ValueError` matching `never by a caller`.
  - The dispatch spy saw no `execute`.
  - With the provider `None`, the same call passes the origin through, as 002's tests rely on.
- **`test_admin_methods_are_refused_in_the_daemon_without_the_operator`** (parametrized over every
  `ARGS` key and over providers `lambda: "a"*16` and `lambda: None`):
  - `client.call(method, **ARGS[key])` raises `ValueError` matching `host operator action`.
  - The dispatch spy saw none of these methods: the refusal happens before the admin message is
    sent (the probe's `status` may reach the coordinator; it is remote-class).
- `test_remote_methods_pass_without_a_principal`: with the provider `lambda: None`, `status`,
  `flush(0)`, `sync(files=None)` and `execute` succeed.
- **`test_the_operator_passes_the_client_gate`**: with the provider `lambda: "operator"`, every
  `ARGS` call reaches the coordinator (the dispatch spy records it) and either answers or raises an
  error that does not match `host operator action` (the tmp root need not be a Git repository). A
  plain `Coordinator` ignores the origin.
- **`test_sync_job_resolves_the_origin_in_the_calling_thread`**:
  - Provider: a function that returns `"a"*16` only on the test's thread
    (`threading.get_ident() == main`), and `None` elsewhere.
  - `client.sync_job(deadline=time.monotonic() + 30)` reaches `_send("sync_job", ...)` with
    `origin == {"principal": "a"*16}`.
- `test_a_raising_provider_sends_nothing`: a provider raising `PermissionError` makes
  `client.status()` raise `PermissionError`, and the dispatch spy sees nothing (002's rule, now on
  every method).
- `test_served_principal`: `(False, None)` without a provider; `(True, "a"*16)` and `(True, None)`
  with providers that return those. `serving()` is `False` without a provider and `True` with a
  provider that raises when called (it is never called).
- `test_client_refusal_is_logged_without_arguments`:
  - With `caplog` on `taskmaster.coordinator` at WARNING, a refused
    `client.git_run(kind="not-a-kind", message="SECRET-MSG")` logs
    `refused git_run in the serve process for key aaaaaaaaaaaaaaaa`.
  - No record contains `SECRET-MSG`.

Commit: `feat(coordinator): in the serve daemon every IPC call carries its principal and admin methods need the host operator`.

### Step 2: the serve coordinator (`ServeCoordinator.dispatch`)

Tests in `tests/test_serve_allowlist.py`, over real IPC: `owner = serve_owner(root)` and
`client = Client(root, autostart=False)`, with **no** provider (a host process). The test passes
`origin=` explicitly, which a provider-less client allows (Step 1).

- **`test_serve_coordinator_refuses_admin_methods_to_a_member`** (parametrized over every `ARGS` key):
  - `client.call(method, origin={"principal": "a"*16}, **ARGS[key])` raises `ValueError` matching
    `host operator action`.
  - `.taskmaster/linear.yaml` doesn't exist afterwards, and `SELECT MAX(seq) FROM domain_events` is
    unchanged.
- **`test_a_host_process_is_the_operator`**: the same calls with no origin pass the gate. Each
  answers normally or raises the method's own validation error, never one matching `host operator
  action` (for example, `git_run` refuses `not-a-kind`). The same holds with
  `origin={"principal": "operator"}`.
- `test_remote_methods_pass_for_a_member`: `status`, `flush(through=0)` and `sync` (no take_file) with
  a member origin succeed.
- **`test_bootstrap_token_name_is_fixed_on_a_server`**:
  - With no origin (the operator), `linear_bootstrap` with an entry naming `OPS_LINEAR_TOKEN` raises
    `ValueError` equal to `LINEAR_TOKEN_ENV_REFUSED`, and `linear.yaml` is absent.
  - With `TASKMASTER_LINEAR_TOKEN`, it answers `{"ok": True, ...}`, and the file names exactly
    `token_env: TASKMASTER_LINEAR_TOKEN`.
  - An `entry` of `None` is refused with the same text.
- `test_invalid_origin_is_refused_on_any_method`: `client.call("status", origin={"principal": "../x"})`
  raises `ValueError` matching `invalid origin`.
- **`test_shutdown_and_retire_keep_001s_refusals`**:
  - With a member origin, `shutdown` raises `ValueError` equal to `SERVE_SHUTDOWN_REFUSED`.
  - A `retire` sent with a `FUTURE` build in its identity and a member origin returns 001's
    `{"state": "refused", "reason": SERVE_RETIRE_REFUSED}`.
  - Use `client._send(record, "retire", identity=..., origin=...)` with `record = client._discovery()`,
    as `test_serve_coordinator.py` does.
- `test_session_coordinator_has_no_gate`: a plain `Coordinator(root)`: `client.call("git_status",
  origin={"principal": "a"*16})` answers.
- `test_coordinator_refusal_is_logged`: `caplog` holds `refused git_status for key aaaaaaaaaaaaaaaa`.

Regression to watch: `test_serve_coordinator.py` and `test_serve_identity.py` pass unchanged.

Commit: `feat(serve): the serve coordinator refuses admin methods to anyone but the host operator and fixes the Linear token name`.

### Step 3: tool gates (`overview.py`) and the take-file refusal through its tool

Tests in `tests/test_serve_allowlist_tools.py`. Each test sets `client_module.ORIGIN_PROVIDER` with
`monkeypatch` to simulate the daemon; the twins' coordinator is a session coordinator, so only the
client-side gate and the tool gate act here (the coordinator side is Step 2's).

- **`test_a_member_cannot_probe_linear`** (provider `lambda: "a"*16`):
  - Monkeypatch `taskmaster.integrations.linear.client.LinearClient` to raise if constructed, and set
    `SECRET_X=s3cr3t` with `monkeypatch.setenv`.
  - `json.loads(bs.backlog_linear(action="probe", token_env="SECRET_X"))["error"]` contains
    `backlog_linear probe is a host operator action`.
  - No `LinearClient` was built.
- **`test_a_member_cannot_bootstrap_linear`**:
  - `_bootstrap(twins)` under the member provider gives an `error` containing
    `backlog_linear bootstrap_apply is a host operator action`.
  - `_config_path(twins)` is absent, and `_leftovers(twins) == []`.
- **`test_the_operator_bootstraps_with_the_fixed_name`** (provider `lambda: "operator"`):
  - `_bootstrap(twins, token_env="")` gives `{"ok": True, ...}`.
  - `linear.yaml`'s `ops` workspace has `token_env: TASKMASTER_LINEAR_TOKEN`.
- **`test_the_operator_cannot_name_another_variable`**:
  - `_bootstrap(twins, token_env="OPS_LINEAR_TOKEN")` gives `error ==
    LINEAR_TOKEN_ENV_REFUSED`.
  - `probe` with `token_env="AWS_SECRET_ACCESS_KEY"` gives the same, and no `LinearClient` was
    built.
- `test_the_operator_probe_reads_the_fixed_name`:
  - With `TASKMASTER_LINEAR_TOKEN=lin_test` set and `LinearClient` replaced by a fake whose
    `list_teams` returns `[]`, `probe` with `token_env=""` gives `{"teams": []}`.
  - The fake was built with `token="lin_test"`.
- **`test_solo_linear_is_unchanged`**: without a provider, `_bootstrap(twins)` (token
  `OPS_LINEAR_TOKEN`) writes that name, as `test_native_linear_bootstrap` expects.
- **`test_a_member_cannot_take_a_flagged_file`**:
  - Fixture `take_twins = make_twins(tmp_path, monkeypatch, _seed)`; `edited = _flag(take_twins,
    _hand_edit)` with no provider.
  - Then, with the provider `lambda: "a"*16`, `bs.backlog_resolve_conflict(file=REL, take="file")`
    starts with `Error:` and contains `adopting a flagged file from the host's disk` and
    `take="store") is available to everyone`.
  - `_flagged(take_twins) == [REL]`, and `_path(take_twins).read_bytes() == edited`.
- **`test_a_member_can_take_the_store_version`**: under the same member provider, `take="store"`
  resolves (`"kept the store version"`), and the flag is gone.
- `test_the_operator_takes_a_flagged_file`: with the provider `lambda: "operator"`, `take="file"`
  answers `Resolved {REL}: kept the file version.`

Commit: `feat(serve): Linear probe and bootstrap are operator-only on a team server and read only TASKMASTER_LINEAR_TOKEN`.

### Step 4: re-check host-locality for the operator (`serve/auth.py`)

Tests in `tests/test_serve_allowlist.py`. Build a Starlette `Request` from a scope (as
`test_serve_auth.py` builds scopes for `is_host_local`). Monkeypatch
`fastmcp.server.dependencies.get_http_request` to return it, and `auth.current_principal` to
return the principal under test.

- **`test_operator_key_id_requires_a_host_local_request`**: `OPERATOR` with scope
  `client=("203.0.113.5", 4000)` and `Host: tm.example.com` makes `current_key_id()` raise
  `PermissionError` matching `works only on the host`. With `client=("127.0.0.1", 4000)` and
  `Host: 127.0.0.1:7470`, it returns `"operator"`.
- `test_member_key_id_needs_no_host_check`: a member principal with the public scope returns its key id.
- `test_outside_a_request_is_none`: `get_http_request` raising `RuntimeError` returns `None` (unchanged).
- Regression: `test_serve_principals_e2e.py` (the operator token over loopback still writes and is
  refused in team mode as before).

Commit: `feat(serve): the operator principal is re-checked as host-local wherever it is read`.

### Step 5: end to end over HTTP, and the ratchets

Tests in `tests/test_serve_allowlist_e2e.py`, using `launch` (an in-thread daemon on `127.0.0.1`,
two member keys and the operator token, team mode on as its default; none of these tests writes through `execute`).

- **`test_a_member_is_refused_linear_and_take_file_over_http`**:
  - `call_tool(world.url, world.keys["ann"].key, "backlog_linear", {"action": "bootstrap_apply", ...})`
    returns `error` text containing `host operator action`. The same holds for `probe`.
  - `linear.yaml` is absent.
- **`test_the_operator_bootstraps_over_loopback`**: the same call with `world.operator` and
  `token_env: ""` writes `token_env: TASKMASTER_LINEAR_TOKEN`. With `"OPS_LINEAR_TOKEN"` it is
  refused with `LINEAR_TOKEN_ENV_REFUSED`.
- **`test_any_tool_reaching_an_admin_method_needs_the_operator`** (design D10):
  - After `world = launch()`, register a throwaway tool on the served module's instance,
    `sys.modules["taskmaster.backlog_server"].mcp._inner` (the one the daemon serves; FastMCP lists
    tools per request, so a tool added after start is served):
    `t005_reach(method: str, threaded: bool = False) -> str`.
    - It calls `Client(root, autostart=False).call(method, **ARGS[method])`.
    - It answers `"refused"` when the `ValueError` contains `host operator action`, and
      `"passed:<ExcType or ok>"` otherwise.
    - With `threaded=True`, the call runs on a bare `threading.Thread`, and the tool returns that
      thread's answer.
  - Remove the tool in teardown (`bs.mcp._inner.remove_tool("t005_reach")`).
  - It takes an `ARGS` key (Step 1) rather than a method name, and closes over `world.root`.
  - Over HTTP, for every `ARGS` key:
    - Ann's key gets `"refused"`.
    - The operator token gets `"passed:…"` when `threaded=False`, and `"refused"` when
      `threaded=True` (design D4).
  - For `status`, every caller gets `passed:ok`.
- `test_refusals_survive_a_member_flood_without_side_effects`: 20 alternating refused calls from Ann
  and Bob leave `linear.yaml` absent, `domain_events` unchanged, and the daemon still serving
  `backlog_list_tasks`.
- **`test_only_the_client_posts_to_the_coordinator`** (design D10): every `taskmaster/**/*.py`
  containing `"/rpc"` or `'/rpc'` is one of `coordinator/client.py`, `coordinator/service.py` and
  `serve/config.py`.
- `test_serve_log_names_refusals_without_arguments`: after Ann's `t005_reach("git_run")`, `serve.log`
  contains `refused git_run in the serve process for key <Ann's key id>`. It contains neither
  `not-a-kind` nor any key secret (`world.keys[...].secret`). The tool gate (Step 3) does not log;
  its refusal goes only to the caller.

Commit: `test(serve): any tool reaches admin coordinator methods only as the host operator, end to end`.

### Step 6 (Part B): the served-path rule

**Precondition.** Part B starts only once 006 has merged into `feat/team-collab` with
`taskmaster/serve/tracked.py` exposing `TrackedFiles` and `tracked_file_allowed(root, rel_path) ->
bool` (design §7, §9). Rebase this branch onto that merge first. If the module or the function is
missing, or has another signature, stop and report; don't copy it, re-implement it or import it
from the viewer.

Tests in `tests/test_serve_doc_reads.py`. Fixture `served` is `twins` (a native store) made a git work
tree as 006's viewer fixture does:

- `git init`;
- commit `docs/spec.md`, a `.gitignore` holding `.taskmaster/local/`, and three symlinks (where the
  platform allows; those cases are skipped elsewhere and run in 016's Linux lane):
  `docs/tok -> ../.taskmaster/local/serve/operator.token`, `docs/env -> ../.env`, and
  `docs/out -> <a file outside the root>`;
- write, untracked: `.env`, `notes.md` and `.taskmaster/local/serve/operator.token`, each holding a
  distinct marker string;
- write a file outside the root holding its own marker.

The provider is set to `lambda: "a"*16` (serving).

- **`test_the_shared_rule`** (a direct unit test of 006's `tracked_file_allowed` on `served`'s root,
  parametrized; it guards the viewer's `/file/` too):
  - `True`: `docs/spec.md`.
  - `False`: the absolute outside path; `../outside.txt`; `docs/../../outside.txt`; `C:/x` and
    `C:\x`; `\\host\share\x`; `docs\spec.md`; `docs/spec.md` followed by a NUL byte; `.env`; `notes.md`;
    `.taskmaster/local/serve/operator.token`; `.git/config`; and the tracked symlinks `docs/tok`,
    `docs/env`, `docs/out`.
- **`test_a_served_docs_path_reads_only_tracked_files`** (parametrized over every `False` path above):
  - Each is set with `backlog_update_task(field="docs", value="spec:<path>")`.
  - `backlog_get_task(sections=["spec"])` contains `(refused:` and none of the marker strings.
  - `backlog_document(kind="task", ..., sections=["spec"], provenance=True)` shows source `refused`.
- `test_a_tracked_doc_still_reads`: `spec:docs/spec.md` returns its content, with provenance
  `filesystem`.
- **`test_a_served_import_refuses_untracked_and_symlinked_files`** (parametrized over `.env` and the
  tracked symlink `docs/tok`): `backlog_document_import(kind="task", entity_id=...,
  sections=["spec"])` answers `Error:` naming the refusal. The store holds no imported prose for the
  task, and no marker string appears in the answer.
- **`test_served_read_without_backlog_yaml_is_refused`** (review I3): with the provider set,
  monkeypatch `documents.project_root` to return `None` (what it returns when `backlog.yaml` is
  missing; moving the file aside instead sends the tool down the legacy path before any document
  read, per the re-review). For `spec:<absolute outside file>` and `spec:.env`, `backlog_get_task(sections=["spec"])`
  is refused and shows no marker. `spec:docs/spec.md` still reads, because the root is `bs.ROOT`.
- `test_no_served_root_fails_closed`: with `taskmaster.backlog_server.ROOT` monkeypatched to `None`,
  a served read of `docs/spec.md` is refused.
- **`test_solo_reads_are_unchanged`**: without a provider, an absolute in-project path and an
  untracked `notes.md` read as before, and `taskmaster.serve.tracked` is not imported by the read
  (`sys.modules` before and after, in a fresh interpreter). The outside-file read keeps today's
  behaviour in solo; it is the user's own machine.
- `test_tracked_files_failure_fails_closed`: with `TrackedFiles.get` returning `frozenset()` (git
  missing), every served read is refused.

Commit: `fix(serve): on a team server, task documents are read only from git-tracked project files`.

### Step 7 (Part B): over HTTP

In `tests/test_serve_allowlist_e2e.py`:

- **`test_a_member_cannot_read_host_files_through_docs`**: with `launch`, the root made a git work tree
  as in Step 6, Ann's key sets `spec:<absolute outside file>`, `spec:.env` and (where symlinks are
  available) `spec:docs/tok` in turn, then calls `backlog_get_task(sections=["spec"])`. No marker
  appears in any answer.

Commit: `test(serve): a teammate cannot read host files through a task's docs path, end to end`.

---

## 4. Order of work inside a step

Write the tests first, and confirm they fail for the right reason:

- Step 0: `ModuleNotFoundError` (`taskmaster.coordinator.allowlist`).
- Step 1:
  - `served_principal` is an `AttributeError`.
  - `status` and `flush` reach `_send` without an origin.
  - `git_status` reaches `_send` under a member provider.
- Step 2: a member-origin `git_status` answers instead of raising; `linear_bootstrap` with
  `OPS_LINEAR_TOKEN` writes `linear.yaml`.
- Step 3: the member `probe` builds a `LinearClient`, which the guard raises on; the member
  `take="file"` resolves the flag.
- Step 4: the off-host operator scope returns `"operator"`.
- Step 5: after Steps 1 to 4 most of these pass at once. Confirm the throwaway-tool test fails when
  `Client._send`'s gate is commented out locally, then restore it; don't commit that probe.
- Step 6: `test_the_shared_rule` passes already (it is 006's code); the read tests fail because the
  outside file's marker is returned.

Don't edit sources while a test run is in progress: the build fingerprint changes and
real-coordinator tests fail spuriously (about 13 of them).

## 5. Commands

Run from the worktree root (`C:\Users\gruku\Files\Claude\taskmaster\.worktrees\team-server-005`) with
the repo venv's interpreter. Bare `python` on this machine is the wrong interpreter. Put every temp
file, `--basetemp` and spike under `D:\scratchbook\taskmaster\ts005\` (`/d/scratchbook/taskmaster/ts005/`
in Git Bash), with a fresh sub-directory per run. Never use the C: temp or scratchpad.

Step tests:

```bash
/c/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python -m pytest tests/test_serve_allowlist.py \
  tests/test_serve_allowlist_tools.py tests/test_serve_allowlist_e2e.py \
  -q -p no:cacheprovider --basetemp="/d/scratchbook/taskmaster/ts005/step-<n>"
```

Regression subset (after Steps 1, 2, 3 and 5, and before reporting done):

```bash
/c/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python -m pytest tests/test_serve_config.py \
  tests/test_serve_auth.py tests/test_serve_app.py tests/test_serve_app_keys.py tests/test_serve_keys.py \
  tests/test_serve_coordinator.py tests/test_serve_daemon.py tests/test_serve_identity.py \
  tests/test_serve_principals.py tests/test_serve_principals_e2e.py tests/test_serve_cli.py \
  tests/test_native_service.py tests/test_native_service_build.py tests/test_native_service_identity.py \
  tests/test_native_service_actor.py tests/test_native_service_adapter.py tests/test_native_service_sync.py \
  tests/test_native_service_linear.py tests/test_native_linear_bootstrap.py tests/test_linear_tools.py \
  tests/test_native_resolve_take_file.py tests/test_native_resync.py tests/test_native_projection_resolve.py \
  tests/test_packaging.py tests/test_server_instructions.py \
  -n 3 -q -p no:cacheprovider --basetemp="/d/scratchbook/taskmaster/ts005/reg-<n>"
```

Part B adds `tests/test_serve_doc_reads.py`, `tests/test_server_task_detail.py`, every
`tests/test_native_*document*` file and 006's viewer tests (`tests/test_serve_viewer.py`, which
exercise `/file/` through the same shared rule) to both lists.

**Merge check with 006.** Whichever of 005 and 006 lands second runs, after its merge:

- both regression subsets;
- 006's `tests/test_serve_sanitize.py`;
- this plan's `tests/test_serve_allowlist_e2e.py`.

The e2e refusal texts must arrive unchanged through 006's sanitizer (design §7).

Leave the full suite to the review gate.

## 6. Risks (detail in design §10)

- **R1. A new IPC sender or an in-process coordinator call.** Pinned by
  `test_only_the_client_posts_to_the_coordinator` and `test_every_dispatched_method_is_classified`.
- **R2. FastMCP stops propagating the request context into tool threads.** Then everything is
  refused for everyone, which is closed. 002's attribution e2e tests fail first.
- **R3. 006 in parallel.** `serve/daemon.py`'s import block may conflict textually. 006's plan
  touches neither `client.py` nor `auth.py` nor `overview.py`. If 006 lands first, re-run Steps 2
  and 5 after the rebase.
- **R4. 002's tests that pass `origin=` by hand** run without a provider, so Step 1's
  caller-origin refusal does not reach them. If one runs inside a daemon, it fails loudly; stop
  and report rather than loosening the rule.
- **R5. Windows symlinks** in Step 6 need developer mode; that case is skipped where unavailable,
  and the Linux lane (016) runs it.

## 6a. Rollback

Revert the step commits, newest first. No schema, store, projection or config-format change. A
`linear.yaml` entry written on a server names `TASKMASTER_LINEAR_TOKEN`, which stays valid.

## 7. Left for later tasks (mention, don't build)

- Opening `receipt`, `cancel` and `sync_status` to remote principals, scoped by principal (004). It
  moves them from `OPERATOR_ONLY` to `REMOTE` in `allowlist.py`; the origin already travels.
- Per-connection sessions and `caller_scope` prefixes (003).
- The how-to line telling the operator to set `TASKMASTER_LINEAR_TOKEN` in the systemd unit (016).
  There is no `--linear-token-env` flag (U3).
- An operation-level allowlist inside `execute`; validating `merge_stamps.git_is_ancestor` arguments
  (015's review).
- Content-based `backlog_document_import` for remote callers (012).
