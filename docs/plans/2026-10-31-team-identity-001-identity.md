<!-- User intent: the executable, test-first plan for team-identity-001. In team mode the coordinator
     gives this clone a handle and clone_id on its first write, records them in local/identity.yaml,
     creates the shared `profile` entity by a native command, and refuses attributed writes for placeholder git names. -->

# team-identity-001: identity in the coordinator (plan, 2026-10-31)

**Task.** `team-identity-001` (claude-tools backlog, epic `team-identity`, full lane).
**Spec.** `docs/specs/2026-10-31-team-collaboration-native-design.md` rev 3.2: §4 (identity), §3 (`users/<handle>/profile.yaml` is a shared, owner-written `profile` kind), §12 (additive migration).
**Worktree.** `C:\Users\gruku\Files\Claude\taskmaster\.worktrees\team-identity-001`, branch `feature/team-identity-001`, base `1b7a7f4`.
**Executor.** One engineer, test-first: write each step's tests, watch them fail, implement, watch them pass, commit. Steps are ordered by dependency, so run them in order.

**Done when** the first team-mode write derives a handle and a clone_id, writes `local/identity.yaml` with `confirmed: false`, and creates the profile entity by a native command. Degenerate git names (empty, whitespace, `Your Name` and similar) must refuse attribution-bearing writes with an error that names the bootstrap skill.

**Out of scope:** the command-envelope `actor` (team-identity-002), `assignee` fields (003), `author` fields (004), merge-handles (005), the start-session confirmation UI (006), and handle clashes between offline joiners (team-ids-007). In scope: a handle suffix when a *known local profile* has the same slug and a different email.

---

## 1. Decisions (spec gaps resolved here; plan review may overrule any of them)

| # | Decision | Reason |
|---|---|---|
| D1 | The hook is the coordinator writer, `Coordinator._writer` just before `commands.execute` (`taskmaster/coordinator/service.py:272`). Every write reaches that line: MCP tools, the viewer, CLIs, sync and the Linear worker all go through `Coordinator.submit` (`service.py:189`). | `native/commands.py:1-6` forbids file I/O in the command layer, and a tool-layer hook would miss the viewer and CLIs. |
| D2 | The profile is created by its own native command, `profile.create`, run by the gate with `commands.execute` on the writer's connection. It commits just before the caller's command. The gate never calls `self.submit`. | The spec requires the profile to be "created by a command". Submitting from the writer thread would deadlock on its own queue. |
| D3 | `local/identity.yaml` is written first and the profile command runs second. On any later write, if the identity file exists but its profile doesn't, the gate creates the profile from the file. | This makes the handle sticky even across a crash between the two steps. |
| D4 | The gate runs only for attribution-bearing operations. Every operation counts as one, unknown future operations included, except the machine-local set: `sync.*`, `linear.claim`, `linear.settle`, `linear.link`, `linear.unlink`, `graph.repair`, `projection.resolve`, `task.claim_renew`, `task.claim_release`, `document.import` and `profile.create`. A `batch` is attribution-bearing if any item is. | These operations touch only local state (§3: claim leases and trackers are local). Exempting them keeps `backlog_sync` and the Linear worker working with a bad git name and spares them a git probe. |
| D5 | Email identifies the person. If any known profile lists this clone's email (case-insensitive), the clone reuses that handle. Otherwise it takes the slug, and a profile with that slug (necessarily with another email) moves it to `slug2`, `slug3` and so on. Rev 2's "same user.name on a second machine appends the new email" is not automatic: a different email gets a suffix, and claiming an existing handle is 006's confirmation step. | Appending an email to someone else's profile on a name match alone would merge two people silently. |
| D6 | Slug: NFKD-fold to ASCII, casefold, split on non-alphanumerics. One word gives the word; several give the first initial plus the last word. Cut to 30 characters. If nothing ASCII survives (Cyrillic, CJK), fall back to the email's local part (before `+`, alphanumerics only). | This matches rev 2's `John Doe → jdoe`. It keeps handles within `contracts._identifier` (`native/contracts.py:21`) and leaves room for a two-digit suffix. |
| D7 | Handles match `^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$` (lowercase, at most 32 characters). Reserved slugs take a suffix like a clash: `unassigned`, `claude`, `codex`, `user`, `taskmaster`. | Lowercase keeps `users/<handle>/` safe on case-insensitive filesystems. `unassigned` is a viewer chip (§10), `claude`/`codex` are agent names in `<handle>/<agent>` (§4), and `user` is the legacy author enum (§4). |
| D8 | A degenerate name never falls back to the email. Degenerate means empty, whitespace only, no letter or digit, or a placeholder from `DEGENERATE_NAMES` (§3.1). | The spec says such names refuse. Only a real but non-Latin name falls back (D6). |
| D9 | If git can't be read (not on PATH, a probe error, or the 5 s timeout), the gate refuses the same way, with the reason. Only a timeout is cached, for 60 s, so a stalled git doesn't hold the writer for 10 s on every write. A missing or degenerate name is re-probed on the next write, so `git config user.name …` followed by a retry works without a restart. | Keeps the server non-interactive and bounded. |
| D10 | An `identity.yaml` that fails to parse or validate is refused with its own message and is never overwritten. The gate never rewrites an existing identity file. | It may hold a choice the user made through a skill (006). |
| D11 | (Revised at plan review; user ruling 2026-10-31.) The `profile` kind is an additive native migration from schema 3 to 4 with no new tables: `schema.KINDS["profile"] = {}`, so its fields live in `entity_extensions`. This build supports both versions (`schema.SUPPORTED = {3, 4}`). Reads and writes on a store at 3 work exactly as today, and only the profile kind needs 4. New stores are still born at 3 (`schema.BASE_VERSION = 3`). | The native design requires that "unknown newer schemas fail closed without DB or projection mutation" (`docs/specs/2026-09-09-database-native-design.md` §10). A 7.4.0 build accepts only native schema 3 (`native/db.py:35-39`), so a store at 4 is refused before any read or write instead of failing with a `KeyError` on profile rows (`native/queries.py:82`, `:105`). Solo stores stay at 3, so they remain downgradable to 7.4.x (user ruling). |
| D21 | (User ruling.) The upgrade is lazy: it happens only when team mode is turned on, never at coordinator start. The point chosen is the identity gate. When `admit` is about to create this clone's profile (the first schema-4 row), it first calls `native.upgrade.upgrade(connection)`, one transaction that sets the manifest from 3 to 4 with no DDL. Any later team task needing schema 4 or above enters through the same function. Each later schema change adds its own step and version (003 → 5, …). | **Why the gate and not the `project.set` that turns team mode on:** `project.set` isn't the only way team mode turns on, because team-sync-001's D3 lets `local/team.yaml` switch `mode` when the project has a `team:` block. `team_mode_enabled`, which reads that file, is the single definition, and the command layer can't read files (`commands.py:1-6`). The gate already runs in the single writer, outside a transaction. Upgrading right before the first schema-4 row also means a refused bootstrap (for example a degenerate name) leaves the store at 3. It's equally safe: D22 refuses any profile row on a store below 4, whatever the path. |
| D22 | The invariant is enforced where rows are written: `schema.KIND_SINCE = {"profile": 4}`, and `migrate._put_entity` (the path for both create and import) refuses a kind whose minimum schema is above the store's manifest version. | No path, whether a command, an import or a future bulk join, can put a profile row into a store that an older build would still open. |
| D23 | Cross-build retirement still works when versions differ. This build's coordinator identity always carries `schema: 4` (`protocol.py:85` reports the build's `schema.VERSION`, not the store's), while a 7.4.0 coordinator carries 3. The client compares `schema` in discovery only when the build is the same, and sends `retire` with the running coordinator's own `schema` value. | Otherwise a 7.4.0 coordinator would fail the identity match (`client.py:96`; `protocol.py:192` on the old side, which can't change), and the new client would wait out the 300 s idle timeout instead of retiring it. |
| D12 | `users/<handle>/profile.yaml` is a whole-document YAML mapping rendered like `project.yaml` (`store.py:6219-6220`), with fields `id`, `name`, `emails`, `created`. | §3 names a `.yaml` file. These are rev 2's fields ("display name, git email list, created"); clone ids stay local. |
| D13 | The legacy store doesn't scan `users/`. `Store._ENTITY_FILE_SPECS` filters out native-only kinds. | Team mode refuses legacy stores (R1), and a legacy parse of YAML as frontmatter would quarantine the file. |
| D14 | The error names `taskmaster:init-team` through one constant, `team_identity.BOOTSTRAP_SKILL`. | §4 and §8 make `init-team` the skill that sets identity. It doesn't exist yet (team-sync-010), and 006 can re-point the constant in one place. |
| D15 | A replay of a request that already has a durable receipt bypasses the gate. | A retried, already-committed command must never come back as refused. |
| D16 | (Revised at plan review.) Team mode is decided from the database's project row, never from the exported `project.yaml`. The gate reads `entity_core.last_seq` of `("project", "__project__")` and caches the answer on `(that last_seq, stat of .taskmaster/local/team.yaml)`. On a change it reloads the row's fields and calls `team_mode_enabled(root, project_doc=fields)`; with no project row it passes `{}`, so no file is read. | The DB is the authority on a native store, and the export can lag a `project.set`. Two cheap lookups per write keep solo mode free of YAML parsing. |
| D17 | Profiles never take part in resolving a bare ID to a kind: `relations._kind_for_id` and `migrate._reference` exclude `kind='profile'`. | Otherwise a link to `jdoe` (or a dangling link written earlier) would bind to a person. |
| D18 | `profile.create` is internal to `backlog_batch_update(commands=…)` (`native/batch_commands.py:28`). | Only the identity bootstrap creates profiles in this task. |
| D19 | `clone_id` is `secrets.token_hex(8)` (16 hex characters). `clone_label` is `socket.gethostname()`. | Random and stable per clone (§4), and short enough to paste into `team.linear_clone` (§11). |
| D20 | §4 says `init-team` writes `identity.yaml`. Here the gate writes it on the first team-mode write, and `init-team` (team-sync-010) will reuse `team_identity` and the gate's bootstrap rather than duplicate them. | The task's done-when puts the derivation on the first team-mode write. |

---

## 2. Dependency: `team_mode_enabled(root)` from team-sync-001

At `1b7a7f4`, `taskmaster/project.py` has no team helper; team-sync-001 is being built in parallel. Its final interface, as confirmed at plan review:

```python
# taskmaster/project.py (owned by team-sync-001)
def team_mode_enabled(root: Path, *, project_doc: Mapping | None = None) -> bool
def load_team_config(root: Path, *, project_doc: Mapping | None = None) -> ...
```

Here `project_doc` is the project row's fields. Under team-sync-001's D3, a `local/team.yaml` mode override applies only when the project already has a `team:` block. The gate always passes `project_doc` from the database (D16), so `project.yaml` is never read on this path.

**Shim (this task).** Don't edit `project.py`, so the two branches can't conflict. In `taskmaster/coordinator/identity_gate.py`:

```python
try:
    from taskmaster.project import team_mode_enabled as _project_team_mode   # team-sync-001
except ImportError:   # shim until team-sync-001 lands; delete this branch when it does
    _project_team_mode = None

def team_mode_enabled(root: Path, *, project_doc: Mapping | None = None) -> bool:
    if _project_team_mode is not None:
        return bool(_project_team_mode(root, project_doc=project_doc))
    # Shim: only the project row; the local/team.yaml override is team-sync-001's to implement.
    if project_doc is None:
        return False
    team = project_doc.get("team")
    sync = team.get("sync") if isinstance(team, dict) else None
    return isinstance(sync, dict) and sync.get("mode") in ("manual", "auto")
```

The gate's own reader: `_project_doc(connection) -> dict` returns `Repository(connection).snapshot().get("project", PROJECT_ID)["fields"]`, or `{}` on `KeyError`. It runs in a read snapshot that is closed before `commands.execute`.

**Reconciliation.** Whichever task merges second deletes the `except ImportError` branch and the shim body in the same merge. Tests enable team mode in two ways, and both pass under either implementation:

- Unit tests monkeypatch `identity_gate.team_mode_enabled`.
- End-to-end tests set the project row through the native `project.set` command (`native/workflow.py:1402`), or the `backlog_project_set` tool, to `team.sync.mode: manual`.

One test proves the export is ignored: a hand-written `project.yaml` saying `manual` while the DB row says `off` leaves the gate off. The D3 override behaviour stays team-sync-001's to test. If its final signature differs, adjust the import line and the one call site.

---

## 3. API

### 3.1 `taskmaster/team_identity.py` (new; pure apart from the identity file; no socket, no coordinator imports)

Header: `# User intent: one definition of who this clone is in team mode — handle rules, the local identity file and the refusal text — shared by the coordinator gate, the profile command and the later init-team/start-session flows.`

```python
BOOTSTRAP_SKILL = "taskmaster:init-team"
IDENTITY_REL = "local/identity.yaml"            # under .taskmaster/
IDENTITY_VERSION = 1
HANDLE_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?")
RESERVED_HANDLES = frozenset({"unassigned", "claude", "codex", "user", "taskmaster"})
DEGENERATE_NAMES = frozenset({            # compared after whitespace collapse + casefold
    "your name", "yourname", "your full name", "full name", "name", "first last",
    "firstname lastname", "first name last name", "user", "username", "user name",
    "unknown", "anonymous", "nobody", "none", "null", "root", "admin", "administrator",
    "me", "test", "git", "changeme", "todo", "xxx"})
PLACEHOLDER_EMAIL_DOMAINS = frozenset({"example.com", "example.org", "example.net", "localhost"})
MAX_SUFFIX = 99

class IdentityRefused(ValueError): ...            # crosses IPC by type name (step 5)

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
    created: str         # "%Y-%m-%dT%H:%M:%SZ"
    git_name: str = ""
    git_email: str = ""
    def to_document(self) -> dict: ...
    @classmethod
    def from_document(cls, data) -> "TeamIdentity": ...   # ValueError on any invalid field

def normalize_name(raw: str) -> str                 # " ".join(raw.split())
def is_degenerate(name: str) -> bool                # D8
def usable_email(raw: str) -> str                   # "" for empty / no "@" / placeholder domain
def slug_for(name: str, email: str = "") -> str     # D6; "" when nothing usable
def choose_handle(base: str, email: str, profiles: Mapping[str, Sequence[str]]) -> tuple[str, bool]
    # (handle, already_exists). D5 then D7; IdentityRefused past MAX_SUFFIX
def new_identity(handle: str, git: GitIdentity, *, clone_label: str, now: datetime) -> TeamIdentity
def read_identity(backlog_dir: Path) -> TeamIdentity | None      # None when absent; ValueError when invalid
def write_identity(backlog_dir: Path, identity: TeamIdentity) -> None
    # unique temp name "identity.yaml.tmp.<pid>.<hex>", fsync, os.replace; mkdir local/ if missing
def is_attribution_bearing(operation: str, arguments: dict) -> bool   # D4
def refused_name(name: str) -> IdentityRefused
def refused_unsluggable(name: str) -> IdentityRefused
def refused_git(reason: str) -> IdentityRefused
def refused_file(reason: str) -> IdentityRefused
def refused_exhausted(base: str) -> IdentityRefused
```

`is_attribution_bearing` builds its exempt set lazily from the source sets, so it can't drift: `native.sync.OPERATIONS | native.linear_outbox.OPERATIONS | native.graph_repair.OPERATIONS | {native.projection.RESOLVE, "linear.link", "linear.unlink", "task.claim_renew", "task.claim_release", "document.import", "profile.create"}`. For `batch` it recurses into `arguments["commands"]`.

### 3.2 `local/identity.yaml`

```yaml
# Taskmaster team identity for this clone. Machine-local: never synced or committed.
version: 1
handle: jdoe
clone_id: 3f9a2c4e5b6d7e8f
clone_label: GRUKU-PC
confirmed: false
created: '2026-10-31T12:00:00Z'
source:
  git_name: John Doe
  git_email: jd@corp.io
```

Validation in `from_document`:

- `version == 1`
- `handle` matches `HANDLE_RE`
- `clone_id` matches `[A-Za-z0-9][A-Za-z0-9_-]{7,63}`
- `clone_label` is text
- `confirmed` is a bool
- `created` is text
- `source` is optional; if present it's a mapping of text

Unknown top-level keys are allowed, because 006 may add some. The gate never rewrites the file (D10).

### 3.3 Slug and handle examples

| git user.name / email | handle | Rule |
|---|---|---|
| `John Doe` | `jdoe` | first initial + last word |
| `Gruku` | `gruku` | one word |
| `Mary Jane Watson` | `mwatson` | first initial + last word |
| `Jean-Luc Picard` | `jpicard` | hyphen splits words |
| `José Ñúñez` | `jnunez` | NFKD ASCII fold |
| `O'Brien` | `obrien` | `o` + `brien` |
| `  JOHN   DOE ` | `jdoe` | whitespace collapse, casefold |
| `Straße Müller` | `smuller` | casefold `ß → ss` |
| `A` × 40 | `a` × 30 | cut to 30 |
| `Володимир` / `v.dmytriiev+tm@corp.io` | `vdmytriiev` | email local-part fallback |
| `李小龙` / empty email | refused (unsluggable) | nothing usable |
| `John Doe`, profile `jdoe` has `other@x.io` | `jdoe2` | clash, different email |
| `John Doe`, profiles `jdoe`, `jdoe2` both other emails | `jdoe3` | next free |
| `John Doe` / `JD@corp.io`, profile `jdoe` has `jd@corp.io` | `jdoe` (exists) | same email, reuse |
| `John Doe` / `jd@corp.io`, profile `johnny` has `jd@corp.io` | `johnny` (exists) | any profile with the email wins (D5) |
| `John Doe` / no email, profile `jdoe` exists | `jdoe2` | no email never matches |
| `Claude` | `claude2` | reserved (D7) |
| `Your Name`, ``, `   `, `???`, `First Last` | refused (degenerate) | D8 |

**Stickiness.** Once `identity.yaml` exists with a valid handle, the gate doesn't read git config for identity again in this process or any later one. A later `git config user.name "Johnny D"` changes nothing: same handle, same file bytes, no new profile.

### 3.4 Refusal text (exact; built by the `refused_*` factories)

Common tail: `Run the taskmaster:init-team skill to set your team identity, or set a real name with git config user.name "<your name>", then retry. Nothing was written.`

- Degenerate: `Refused: team mode attributes every shared change to a person, and this clone has no identity yet. git user.name is {shown}, which can't be used as a handle. ` + tail. `{shown}` is `empty` for an empty or whitespace-only value, otherwise the value in double quotes.
- Unsluggable: `Refused: team mode needs a handle for this clone, but neither git user.name "{name}" nor user.email gives one (handles use a-z and 0-9). ` + tail.
- Git unreadable: `Refused: team mode needs this clone's identity, but git config could not be read ({reason}). ` + tail.
- Bad file: `Refused: .taskmaster/local/identity.yaml is unreadable ({reason}). Fix or delete that file, or run the taskmaster:init-team skill, then retry. Nothing was written.`
- Exhausted: `Refused: no free handle from "{base}" up to {base}99 among this backlog's profiles. Run the taskmaster:init-team skill to choose one, then retry. Nothing was written.`

Routed tools render this as `Error: Refused: …` (`native_routing/runtime.py:9-12`, and the handlers' `except ValueError`).

### 3.5 `taskmaster/native/profiles.py` (new; a native operation family like `native/documents.py`)

Header: `# User intent: the shared per-person profile (users/<handle>/profile.yaml) exists only as a native entity created by a command, never a file anything writes directly.`

```python
OPERATIONS = {"profile.create"}
def validate(operation, arguments): ...
    # keys == {"handle", "name", "emails", "created"}
    # handle: HANDLE_RE and not reserved; name: str, 1..200 chars after strip, not is_degenerate
    # emails: list[str], <= 20 items, each <= 320 chars and containing "@"
    # created: matches \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z
def apply(transaction, operation, arguments):
    transaction.create("profile", {"name": ..., "emails": [...], "created": ...}, None,
                       requested_id=arguments["handle"])     # store stamps id (commands.py:189-192)
def known_profiles(snapshot) -> dict[str, list[str]]:
    # handle -> emails, paging snapshot.list("profile", fields=["emails"], limit=MAX_PAGE, cursor=...)
```

An existing handle already raises from `_reserve_id` (`commands.py:79-80`, "profile ID `x` already exists").

### 3.6 `taskmaster/coordinator/identity_gate.py` (new)

Header: `# User intent: the coordinator, never a prompt, derives this clone's team identity on the first team-mode write and refuses attributed writes it cannot attribute; it stays non-interactive and bounded.`

```python
GIT_TIMEOUT = 5            # per probe
TIMEOUT_BACKOFF = 60.0     # seconds a git timeout is remembered
CALLER_SCOPE = "coordinator-identity"

def team_mode_enabled(root: Path, *, project_doc: Mapping | None = None) -> bool   # §2 shim
def _project_doc(connection) -> dict                # DB project row fields, {} when absent (§2)
def read_git_identity(root: Path) -> GitIdentity
    # git.probe(root, "config", "--get", "user.name", ok=(0, 1), timeout=GIT_TIMEOUT), same for user.email;
    # exit 1 means unset (""); decode utf-8 "replace", strip. GitRefused propagates.

class IdentityGate:
    def __init__(self, root: Path, *, store_id: str, checkpoint=lambda stage: None, clock=time.monotonic): ...
    def admit(self, connection, request) -> bool:
        """Run before commands.execute. True when it committed a profile (the caller sets export_needed).
        Raises IdentityRefused. Requires no open transaction and leaves none."""
    def current(self) -> TeamIdentity | None        # cached identity; team-identity-002 stamps the actor from this
```

`admit` in order:

1. If `not is_attribution_bearing(op, args)`, return `False`.
2. If team mode is off, return `False`. Team mode is `team_mode_enabled(self.root, project_doc=_project_doc(connection))`, cached on the project row's `last_seq` and the `local/team.yaml` stat (D16).
3. Load `identity.yaml` through a cache keyed on its `(mtime_ns, size)`. On `ValueError` or an invalid document, raise `refused_file`. If an identity is present, `return self._ensure_profile(connection, identity)`.
4. If `command_receipts` has `(store_id, caller_scope, request_id)`, return `False` (D15).
5. Read git, honouring the timeout backoff:
   - `GitRefused` raises `refused_git(str(exc))`.
   - `is_degenerate(name)` raises `refused_name`.
   - An empty `slug_for(name, email)` raises `refused_unsluggable`.
6. `profiles = known_profiles(snapshot)`, read in a `Repository(connection).snapshot()` block that is closed before going on.
7. `handle, _ = choose_handle(base, usable_email(email), profiles)`.
8. `identity = new_identity(handle, git, clone_label=socket.gethostname(), now=utc_now)`, then `write_identity(...)`, then `self.checkpoint("identity_written")`, then cache it.
9. `return self._ensure_profile(connection, identity)`.

`_ensure_profile` works like this:

- If the handle is in `self._ensured`, return `False`.
- If the profile exists (`snapshot.get("profile", handle)` doesn't raise `KeyError`), add the handle and return `False`.
- Otherwise, if the store is below schema 4, first run `upgrade.upgrade(connection)` (D21) and `self.checkpoint("schema_upgraded")`. Then run `commands.execute(connection, envelope)` and return `True`. The envelope is `{protocol: 2, store_id, caller_scope: CALLER_SCOPE, request_id: f"profile-{clone_id}-{handle}", operation: "profile.create", arguments: {handle, name: identity.git_name or handle, emails: [usable_email(identity.git_email)] if usable else [], created: identity.created}, expected_revisions: []}`.

The envelope is built entirely from `identity.yaml`, so a retry after a crash is byte-identical and replays its receipt. A crash between the upgrade and the profile leaves the store at 4 with no profile. That's correct, because team mode is on, and the next write creates the profile.

---

## 4. TDD steps

Each step: write the tests, run them and see them fail for the stated reason, implement, run the step's tests and the regression subset (§6), then commit with the step's message. New test files start with a `# User intent:` header. Use `tmp_path` and the native twin fixtures, never the live `.taskmaster/`.

### Step 1: identity rules and the local file (`taskmaster/team_identity.py`)

Tests in `tests/test_team_identity.py`:

- `test_slug_examples`: parametrized over every non-refused row of §3.3 that has no profiles. `slug_for(name, email)` equals the handle column.
- `test_slug_is_empty_when_nothing_is_usable`: `slug_for("李小龙", "")` and `slug_for("Володимир", "you@example.com")` are both `""`.
- `test_degenerate_names` (parametrized):
  - True for `""`, `"   "`, `"Your Name"`, `"your  name"`, `"YOUR NAME"`, `"Name"`, `"user"`, `"root"`, `"???"`, `"-"` and `"First Last"`.
  - False for `"John Doe"`, `"Gruku"`, `"Володимир"` and `"Test User"`.
- `test_placeholder_emails_are_unusable`: `usable_email` maps `"you@example.com"`, `""` and `"nobody"` to `""`, and leaves `"jd@corp.io"` unchanged.
- `test_choose_handle_takes_a_free_slug`: `choose_handle("jdoe", "jd@corp.io", {})` is `("jdoe", False)`.
- `test_choose_handle_suffixes_on_a_clash_with_another_email`: `{"jdoe": ["other@x.io"]}` gives `("jdoe2", False)`. Adding `"jdoe2": ["z@x.io"]` gives `("jdoe3", False)`.
- `test_choose_handle_reuses_the_profile_holding_the_email`: `{"jdoe": ["JD@corp.io"]}` with `jd@corp.io` gives `("jdoe", True)`, and `{"johnny": ["jd@corp.io"]}` gives `("johnny", True)`.
- `test_choose_handle_without_an_email_never_reuses`: `choose_handle("jdoe", "", {"jdoe": []})` is `("jdoe2", False)`.
- `test_reserved_slugs_take_a_suffix`: `claude` gives `claude2` and `unassigned` gives `unassigned2`.
- `test_choose_handle_refuses_past_the_last_suffix`: with `jdoe` and `jdoe2`…`jdoe99` all held by other emails, it raises `IdentityRefused`. The message contains `BOOTSTRAP_SKILL` and `Nothing was written`.
- `test_new_identity_shape`:
  - `confirmed is False`.
  - `clone_id` matches `[0-9a-f]{16}`, and two calls give different ids.
  - `created` ends in `Z`.
  - `git_name` and `git_email` are copied over.
- `test_identity_file_round_trips(tmp_path)`:
  - `write_identity` then `read_identity` gives an equal `TeamIdentity`.
  - The text contains `confirmed: false` and the header comment.
  - No `identity.yaml.tmp.*` is left behind.
  - It creates `local/` when missing.
- `test_read_identity_absent_is_none`.
- `test_read_identity_rejects_invalid_documents`: parametrized over a list document, handle `JDoe`, a missing `clone_id`, `confirmed: "no"` and `version: 2`. Each raises `ValueError`.
- `test_attribution_bearing_operations`:
  - True: `task.create`, `task.patch`, `note.create`, `task.pick`, `project.set`, `handover.create`, `future.op`, and `batch` of `[task.patch]`.
  - False: every member of the D4 exempt set, and `batch` of `[linear.link]`.
- `test_refusals_name_the_skill_and_say_nothing_was_written`: every factory returns an `IdentityRefused` (a `ValueError`) containing `taskmaster:init-team` and `Nothing was written`. `refused_name("")` contains `is empty`, and `refused_name("Your Name")` contains `"Your Name"`.

Commit: `feat(identity): handle rules, local identity file and refusal text`.

### Step 2: native schema 4 (additive migration, fail closed for older builds)

Changes:

| # | File:line | Change |
|---|---|---|
| a | `taskmaster/native/schema.py:9` | `VERSION = 4` (the highest schema this build supports). Add `BASE_VERSION = 3` (what new stores are born at), `SUPPORTED = frozenset({3, 4})` and `KIND_SINCE = {"profile": 4}`. `PROTOCOL` stays 2: the command envelope doesn't change (`contracts.py:85`). |
| b | `taskmaster/native/schema.py:126-140` (`upgrade_staging`) | unchanged behaviour: staging 1 and 2 upgrade to `BASE_VERSION`. Change the guard `VERSION != 3` to `BASE_VERSION != 3`. |
| c | `taskmaster/native/schema.py` (new function) | `upgrade_native(connection, version)`, with the caller owning the transaction. Version 3 → 4 has no DDL (the profile rows live in `entity_extensions`). Its docstring says each later schema change adds its own step and version (D21). |
| d | `taskmaster/native/db.py:35-39` (`manifest`) | accept `str(v) for v in schema.SUPPORTED` when `authority == "native"`. Staging (legacy authority) still accepts `BASE_VERSION`, plus 1 and 2 with `allow_prior_staging`. The refusal text stays exactly `Unsupported native staging schema or protocol`, because existing tests match it (`tests/test_native_migration.py:163`). |
| e | `taskmaster/native/migrate.py:242` (`backfill`) | write `schema_version=schema.BASE_VERSION`, so twins, cutover activations and born-native stores start at 3. |
| f | `taskmaster/native/migrate.py:61-64` (`_put_entity`) | when `kind in schema.KIND_SINCE`, read `native_manifest.schema_version` and raise `UnsupportedStoreError(f"{kind} rows need native schema {n}; this store is at {v}. They are created once team mode is on. Nothing was written.")` if it's lower (D22). |
| g | `taskmaster/native/upgrade.py` (new) | `upgrade(connection, to=schema.VERSION) -> {"from": v, "to": w}`. It requires no open transaction, then runs `BEGIN IMMEDIATE`, `db.manifest(connection, authorities=("native",))`, and returns without writing when already at `to` or above. Otherwise it calls `schema.upgrade_native` once per step, then `migrate._put_manifest(schema_version=to)`, then commits. Any failure rolls back, leaving the store at its old version. Only the coordinator's writer thread calls it (the identity gate, Step 5). Header: `# User intent: move an activated native store to a newer schema only when a team feature needs it, atomically and under the single writer, so solo stores stay downgradable and older builds refuse upgraded ones cleanly.` |
| h | `taskmaster/coordinator/client.py:96` (`_discovery`) | compare `root`, `store_id`, `protocol` and `service_protocol` always, and `schema` only when `same_build(record.get("build"), self.build)`. In `_retire` (`:282`), send the identity with `schema=record["schema"]` so an older coordinator's `check_handshake` (`protocol.py:192`) accepts the retire (D23). |
| i | `docs/native-store.md:577-590` (Compatibility table) | describe both versions (rows below), and add one sentence under "Run one build per project": "Turning team mode on upgrades the store to native schema 4; 7.4.x can't open it after that, and turning team mode off again doesn't revert it." |

Compatibility rows for (i), replacing the "A different native build" row:

| Client | Store | Result |
|---|---|---|
| This build | Native schema 3 (solo, or team mode never turned on) | Full read and write. Never upgraded except by turning team mode on |
| This build | Native schema 4 (team mode was turned on) | Full read and write |
| 7.4.x or older 7.x | Native schema 3 | Works as before; downgrading a solo store is supported |
| 7.4.x or older 7.x | Native schema 4 | Refuses cleanly: `Error: Unsupported native staging schema or protocol`; nothing is changed |
| A different native build | Native | Must support the store's native schema (3 or 4), authority schema (2) and protocol (2), else `Unsupported native ... schema or protocol`. A running coordinator of another build is retired or refused by the build handshake |

Unchanged, and confirmed by the tests below:

- `PROTOCOL` and the service protocol.
- The legacy → native cutover (`native/cutover.py:223-240`, `:708-722`). It checks the authority `meta.schema_version` (1 → 2), not the native manifest, and the stores it activates are born at `BASE_VERSION` (3) through backfill.
- Coordinator start (`service.py:140-167`): it never upgrades.
- Born-native init (`native/fresh.py`), which goes through backfill.
- `native_routing/gate.py:73-84`, which keeps reaching `assert_native`.

Tests in `tests/test_native_schema_upgrade.py`:

- `test_new_stores_are_born_at_schema_3`: a native twin's `native_manifest.schema_version == "3"`.
- `test_schema_3_store_reads_and_writes_as_today_and_is_never_upgraded_by_start`:
  - On a store at 3, `Coordinator(root).start()`, a `task.patch` through the client, a snapshot read and `gate.native_database(...)` all succeed.
  - After `close()` and a second start, the manifest is still `'3'`.
- `test_upgrade_moves_3_to_4_once_and_changes_nothing_else`:
  - `upgrade.upgrade(connection)` returns `{"from": 3, "to": 4}`.
  - `iterdump()` minus the manifest row is unchanged.
  - A second call returns `{"from": 4, "to": 4}` and writes nothing.
- `test_interrupted_upgrade_leaves_schema_3`: monkeypatch `schema.upgrade_native` to raise. `upgrade.upgrade` raises, and the manifest stays `'3'`.
- `test_the_schema_3_check_accepts_3_and_refuses_4`: simulate the older check in-process by monkeypatching `schema.VERSION = 3` and `schema.SUPPORTED = frozenset({3})`. At 7.4.0 `native/db.py:35-39` is otherwise this same code (verified with `git show 6ef360b:taskmaster/native/db.py`).
  - A store written by this build and left at 3 passes `db.assert_native`, `verified_snapshot` and a `task.patch`.
  - After `upgrade.upgrade` (run with the real constants), each of the following raises `UnsupportedStoreError` matching `Unsupported native staging schema or protocol`, with `iterdump()` identical before and after: `db.assert_native` inside `BEGIN`, `verified_snapshot`, `commands.execute` of a `task.patch`, and `gate.native_database(backlog_dir)` (which raises `NativeUnavailable` with the same text).
- `test_released_7_4_0_build_opens_schema_3_and_refuses_schema_4` (`slow`): the real older build.
  - `git archive 6ef360b taskmaster` through `run_bounded`, extracted into `tmp_path / "old"`.
  - A helper runs `sys.executable -I -c` with `sys.path` set to that directory first. It opens the twin's `store.db`, runs `assert_native` inside `BEGIN`, then lists task ids through `Repository(connection).snapshot()`, and prints the result or the exception type and message.
  - Schema 3 (a store this build wrote to: one `task.patch` through this build's coordinator first): 7.4.0 prints the task ids, including the patched title.
  - Then `upgrade.upgrade`: 7.4.0 prints `UnsupportedStoreError: Unsupported native staging schema or protocol`, and the database file's SHA-256 is unchanged by that run.
- `test_newer_build_retires_a_coordinator_of_the_previous_schema`: adapt `tests/test_native_service_build.py:107` (`test_different_build_retires_idle_owner_and_new_owner_serves`). The old owner publishes a discovery record with `schema: 3` and handshakes against an identity with `schema: 3`. Monkeypatch its `identity["schema"]` before `start()`. The new client retires it and the new owner serves a command.

Commit: `feat(native): schema 4 for team stores, opt-in upgrade, fail-closed for older builds`.

### Step 3: register the `profile` kind (one coherent step)

Every place a native kind is registered, with what changes:

| # | File:line | Change |
|---|---|---|
| a | `taskmaster/native/schema.py:16-30` (`KINDS`) | add `"profile": {}` (D11). No DDL; the version bump is Step 2. |
| b | `taskmaster/native/commands.py:41` (`NAMED_KINDS`) | append `"profile"` (the caller supplies the handle; `_reserve_id` at `:75-89` then requires a requested id). |
| c | `taskmaster/native/commands.py:112-129` (`projection_path`) | `if kind == "profile": return f"users/{ident}/profile.yaml"` (no archive variant). |
| d | `taskmaster/store.py:6219-6220` (`render_entity_file`) | `if kind in ("project", "profile")`: same `yaml.dump(..., sort_keys=False, allow_unicode=True)`; oracle `None`. The native drain uses it unchanged (`native_routing/projection.py:79`, `:328`). |
| e | `taskmaster/projection_parse.py:27-39` (`ENTITY_FILE_SPECS`) | append `("profile", ("users/*/profile.yaml",))`; add `NATIVE_ONLY_KINDS = ("profile",)`. |
| f | `taskmaster/projection_parse.py:42-57` (`classify`) | identity from a new public `path_ident(kind, path) -> str \| None`: `path.parts[1]` for `profile` and only if it matches `team_identity.HANDLE_RE`, else `path.stem`. A `None` ident falls through to the existing `ValueError`. |
| g | `taskmaster/projection_parse.py:168-191` (`authored_rows`) | `kind == "profile"`: conflict-marker check, `yaml_io.safe_load`, mapping required (`"profile.yaml must be a mapping"`), `validate_identity`, return `{("profile", ident): (doc, None)}`. `owned_rows`/`projected_file` need no change. |
| h | `taskmaster/coordinator/sync_files.py:108` (`discover`) | replace `PurePosixPath(rel).stem` with `projection_parse.path_ident(kind, PurePosixPath(rel))`, and skip a match whose ident is `None`. The wildcard-parent branch at `:99-102` already lists `users/*`. |
| i | `taskmaster/store.py:4426` (`Store._ENTITY_FILE_SPECS`) | filter out `NATIVE_ONLY_KINDS` (D13); `:4508`'s `path.stem` then never sees a profile. |
| j | `taskmaster/native/relations.py:77-80` (`_kind_for_id`) and `taskmaster/native/migrate.py:106-107` (`_reference`) | add `AND kind<>'profile'` to the id-only lookup (D17). It still seeks `ix_entity_core_public_id` (`neighbourhood.py:35`). |
| k | `taskmaster/native/lifecycle.py:12`, `:36-37`, `:58-71`, `:147-159` | import `profiles`; add `profiles.OPERATIONS` to `OPERATIONS`; route `validate` and `apply` to it, as `documents` is routed. |
| l | `taskmaster/native/batch_commands.py:28-38` (`INTERNAL_OPERATIONS`) | `"profile.create": "A profile is created by the coordinator's team identity bootstrap."` (D18). |
| m | `tests/conftest.py:362-375` (`_GUARDED_DIRS`) | add `"users"`. Any write to `users/<handle>/profile.yaml` from production code other than the drain then fails the suite, which enforces "never a file the coordinator writes directly". |
| n | `tests/fixtures/native_contracts.json` (`field_ownership`) | add `"profile"`: `id`, `title`, `status`, `archived`, `deleted`, `rev`, `updated_seq` map to `entity_core`; `name`, `emails`, `created` map to `entity_extensions`; `unknown_field_owner` is `entity_extensions`. Read by `tests/test_native_schema.py:39-45`. |

These need no change; the engineer confirms each with the round-trip test below:

- `native/sync.py:150` and `:160`: kind and identity checks are generic over `schema.KINDS` and `classify`.
- `native/sync.py:325-333`: import creates with `requested_id`.
- `native/sync.py:188-200`: no counter for prefix-less kinds.
- `coordinator/sync_prepare.py:36`, `:162`: generic.
- `native/projection.py`: the outbox is kind-agnostic, and `:654` creates the parent directory.
- `native/cutover.py:766` filters by `schema.KINDS`.
- `native/queries.py:134-149` validates kinds against `schema.KINDS`.
- `native/compatibility.py:38-40` materialises every reconstructed entity into the `entities` SQL view.
- `taskmaster_v3.LINK_ENDPOINT_KINDS` (`taskmaster_v3.py:652-654`) stays without `profile`, because profiles aren't link endpoints.

Tests in `tests/test_native_profile_kind.py`:

- `test_profile_kind_is_registered_without_ddl`:
  - `schema.KINDS["profile"] == {}`.
  - `schema.owner("profile", "emails") == "entity_extensions"`.
  - `schema.KIND_SINCE["profile"] == 4 == schema.VERSION`.
  - `"profile" in commands.NAMED_KINDS`.
- `test_projection_path_for_a_profile`: `projection_path("profile", "jdoe", False)` and `(..., True)` both equal `"users/jdoe/profile.yaml"`.
- `test_classify_profile_paths`:
  - `classify("users/jdoe/profile.yaml") == ("profile", "jdoe")`.
  - `ValueError` for `users/JDoe/profile.yaml`, `users/profile.yaml`, `users/a/b/profile.yaml`, `users/jdoe/plan.yaml` and `users/-x/profile.yaml`.
- `test_profile_bytes_parse_as_a_yaml_mapping`:
  - `projected_file("profile", "jdoe", b"id: jdoe\nname: John Doe\nemails:\n- jd@corp.io\ncreated: '2026-10-31T12:00:00Z'\n")` gives `{("profile", "jdoe"): ({"id": "jdoe", "name": "John Doe", "emails": ["jd@corp.io"], "created": "2026-10-31T12:00:00Z"}, None)}`.
  - A mismatched `id`, a top-level list and conflict markers each raise `ValueError`.
- `test_render_profile_round_trips_through_the_parser`: `content, oracle = store.render_entity_file("profile", doc, None)`. Then `oracle is None`, `yaml.safe_load(content) == doc` and `projected_file("profile", "jdoe", content)` recovers `doc`.
- `test_legacy_store_never_scans_profile_files`: `"profile" not in {k for k, _ in store.Store._ENTITY_FILE_SPECS}`.
- `test_sync_discovery_keys_a_profile_by_its_handle(tmp_path)`: write `.taskmaster/users/jdoe/profile.yaml` and `.taskmaster/users/JD/profile.yaml`. `sync_files.discover(backlog_dir).files` maps `"users/jdoe/profile.yaml"` to `("profile", "jdoe")` and has no `JD` entry.
- `test_profile_create_validation` (parametrized; `contracts.validate` on a full envelope raises `ValueError`): handle `claude`, handle `JDoe`, name `Your Name`, `emails` as a string, an extra argument, `created` of `"yesterday"`.
- `test_profile_create_is_refused_through_batch_commands`: `"profile.create" in batch_commands.INTERNAL_OPERATIONS`.
- `test_link_targets_never_resolve_to_a_profile`: on a native twin holding profile `jdoe`, `relations._kind_for_id(connection, "jdoe") == "task"` and `migrate._reference(connection, "links", {"target": "jdoe"})[0] == "task"`.
- `test_profile_files_are_guarded_from_direct_writes`: `conftest._guard_path(backlog_dir / "users/jdoe/profile.yaml") == backlog_dir`.
- **`test_profile_round_trips_command_db_file_import`** (the acceptance test for this step). Twins are born at schema 3, so profile tests first run `upgrade.upgrade(native_connection(root))` (a shared `upgraded` fixture). Use the `twins` fixture pattern of `tests/test_native_sync_tool.py:30-38` (seed, first `backlog_sync`) and the `compatibility_client` of `tests/native_coordinator_helpers.py:17-23`.
  1. Command: `client.execute` a `profile.create` envelope (`caller_scope="profile-tests"`, `request_id="p1"`, handle `jdoe`, name `John Doe`, emails `["jd@corp.io"]`, created `2026-10-31T12:00:00Z`). The receipt's `affected` names `("profile", "jdoe")`.
  2. DB: `Snapshot.get("profile", "jdoe")["fields"] == {"id": "jdoe", "name": "John Doe", "emails": ["jd@corp.io"], "created": "2026-10-31T12:00:00Z"}`, and there is one `domain_events` row with `kind='profile'`, `op='create'` and `tool='profile.create'`.
  3. Exported file: `.taskmaster/users/jdoe/profile.yaml` exists, and `yaml.safe_load` of it equals those fields.
  4. Import with nothing edited: `bs.backlog_sync()` starts `Sync complete`, and `_counted(answer)["imported"] == 0`.
  5. Import of a hand edit: replace `name: John Doe` with `name: John Q. Doe`, then `backlog_sync`. The import count is 1, the DB `name` is `John Q. Doe`, and a second `backlog_sync` imports 0.
  6. Import of a new file: write `users/asmith/profile.yaml` (`id: asmith`, `name: Ann Smith`, `emails: [as@corp.io]`, `created`), then `backlog_sync`. `Snapshot.get("profile", "asmith")` exists.
  7. Replay: the same envelope again returns the identical receipt, and there's still one profile `jdoe`.
- `test_profile_rows_need_schema_4`: on a twin left at schema 3, `commands.execute` of a `profile.create` raises `UnsupportedStoreError` matching `need native schema 4`, and the event high water is unchanged (D22). A `sync.apply` importing `users/x/profile.yaml` on that store is refused the same way.
- `test_profile_rows_appear_in_the_sql_compatibility_view`: after the create, `snapshot.sql("SELECT kind,id FROM entities WHERE kind='profile'")["rows"] == [["profile", "jdoe"]]` (or tuples, whatever `sql` returns).

Commit: `feat(native): profile kind with users/<handle>/profile.yaml projection and profile.create`.

### Step 4: read git identity through the bounded runner

Tests in `tests/test_identity_gate.py` (first part):

- `test_read_git_identity_uses_the_repository_config(tmp_path)`:
  - `git init`, then `git config user.name "John Doe"` and `git config user.email jd@corp.io` (local scope, through `run_bounded`).
  - `read_git_identity(tmp_path) == GitIdentity("John Doe", "jd@corp.io")`.
  - Mark it `slow`, per `tests/conftest.py:31`.
- `test_read_git_identity_runs_only_bounded_config_probes(monkeypatch)`: record `coordinator.git.probe` calls. There are exactly two, `("config", "--get", "user.name")` and `("config", "--get", "user.email")`, each with `timeout=GIT_TIMEOUT` and `ok=(0, 1)`.
- `test_unset_git_values_read_as_empty(monkeypatch)`: `probe` returns `(1, b"")`, which gives `GitIdentity("", "")`.
- `test_git_probe_failure_propagates(monkeypatch)`: `probe` raises `GitRefused("git config timed out")`, and `read_git_identity` raises the same.

Implement `read_git_identity` and `team_mode_enabled` (§2 shim) in `identity_gate.py`. Don't use `subprocess.run`: `coordinator/git.py:80-91` `probe` already wraps `run_bounded` with `GIT_TERMINAL_PROMPT=0`, stdin `DEVNULL` and a hidden window.

Commit: `feat(identity): bounded git identity probe and team-mode check`.

### Step 5: the gate (`IdentityGate`), against a real native store without the service

Fixture: a native twin root (`make_twins(tmp_path, monkeypatch, seed, visibility=None)` as in `tests/test_native_service.py:17-21`). The writer connection is `coordinator.protocol.connect(root)`. Monkeypatch `identity_gate.team_mode_enabled` to `lambda root, *, project_doc=None: True` (except where a test says otherwise) and `identity_gate.read_git_identity` to a stub that counts its calls. Build requests as `task.patch` envelopes like `tests/test_native_service.py:24-27`. Twins start at schema 3; a test that pre-creates a profile through an envelope runs `upgrade.upgrade(connection)` first (D22).

Tests:

- `test_first_team_mode_write_bootstraps_identity_and_profile`:
  - `admit(...)` returns `True`.
  - `.taskmaster/local/identity.yaml` reads back with handle `jdoe`, `confirmed is False`, a 16-hex `clone_id`, and `source.git_name == "John Doe"`.
  - The DB has profile `jdoe` with `emails == ["jd@corp.io"]`.
  - Its `domain_events.session == "coordinator-identity"`.
  - No transaction is left open (`not connection.in_transaction`).
- `test_first_team_mode_write_upgrades_the_store_to_schema_4`: the twin starts at 3. After `admit`, the manifest is `'4'`, and the upgrade committed before the profile: checkpoint order is `identity_written`, then `schema_upgraded`, then the profile event.
- `test_refused_bootstrap_leaves_schema_3`: under a degenerate stub, `admit` raises and the manifest stays `'3'`.
- `test_crash_after_upgrade_before_profile_recovers`: the `checkpoint` raises at `"schema_upgraded"`. The store is at 4 with no profile, and the next `admit` creates the profile.
- `test_second_write_reuses_the_cached_identity`: a second `admit` returns `False`, the git stub was called once, and there's still one profile.
- `test_identity_is_sticky_across_a_git_name_change`: bootstrap, then build a new `IdentityGate` (a restart) with the stub now returning `Johnny D`. `admit` returns `False`, the git stub isn't called, `identity.yaml` bytes are unchanged, and there's no profile `jd`.
- `test_known_profile_with_another_email_suffixes_the_handle`: pre-create profile `jdoe` with `other@x.io` through a `profile.create` envelope. Bootstrap gives handle `jdoe2` and creates profile `jdoe2`.
- `test_known_profile_with_the_same_email_is_reused_without_a_create`: pre-create `jdoe` with `jd@corp.io`. Bootstrap gives handle `jdoe` and `admit` returns `False`: no new event and one profile.
- `test_degenerate_git_name_refuses_attribution_bearing_writes` (parametrized `""`, `"   "`, `"Your Name"`):
  - `IdentityRefused` matching `taskmaster:init-team`.
  - No `identity.yaml`, no profile, and `domain_events` max seq unchanged.
- `test_degenerate_name_does_not_block_machine_local_operations`: a `sync.job` or `linear.settle` request (whichever is easiest to build) under a degenerate stub. `admit` returns `False` and the git stub isn't called.
- `test_fixing_the_git_name_lets_the_retry_through`: a degenerate stub refuses; switch the stub to `John Doe`; the same request now bootstraps.
- `test_unreadable_git_refuses_and_backs_off_after_a_timeout`: the stub raises `GitRefused("git config timed out")`.
  - Refused, with `could not be read (git config timed out)`.
  - A second `admit` within 60 s (injected `clock`) refuses without calling the stub.
  - After the clock advances 61 s, the stub is called again.
- `test_team_mode_off_is_a_no_op`: `team_mode_enabled` returns False under a degenerate stub. `admit` returns `False`, nothing is written and git isn't probed.
- `test_gate_passes_the_db_project_row`: record the `project_doc` that `team_mode_enabled` receives. After a `project.set` command with `document = {schema_version: 1, meta: {name, slug}, team: {sync: {mode: manual}}}`, it equals that row's fields. On a store with no project row, it is `{}`.
- `test_team_mode_follows_the_db_row_without_restart`: don't monkeypatch `team_mode_enabled`, so the shim or team-sync-001's real function runs. With the row at `mode: off`, a write passes with no identity. After a `project.set` to `manual`, the next write bootstraps.
- `test_exported_project_yaml_is_not_read`: with the DB row at `mode: off`, hand-write `.taskmaster/project.yaml` saying `manual`. The gate stays off, and `team_mode_enabled` never sees that file's content.
- `test_team_mode_cache_follows_the_project_row`: two `admit` calls with no project change read the row's fields once. A `project.set` in between (a new `last_seq`) reloads them.
- `test_crash_between_identity_file_and_profile_recovers`:
  - The `checkpoint` raises at `"identity_written"`, so `admit` raises.
  - `identity.yaml` exists and there's no profile.
  - A new gate creates profile `jdoe` from the file without calling git. The `created` and `request_id` are derived from the file.
- `test_invalid_identity_file_is_refused_and_kept`: write `handle: JDoe`. `IdentityRefused` matches `identity.yaml is unreadable`, and the file bytes are unchanged.
- `test_identity_file_edit_is_picked_up_without_restart`: bootstrap `jdoe`, rewrite the file with handle `johnd` (valid, new mtime). The next `admit` creates profile `johnd` and `current().handle == "johnd"`.
- `test_replay_of_a_committed_request_is_never_refused`: commit a request with `commands.execute`, delete `identity.yaml`, switch the stub to degenerate. `admit` on the same request returns `False` without raising.
- `test_gate_never_prompts`: the stub asserts nothing reads stdin (`monkeypatch.setattr("sys.stdin", None)`), and `read_git_identity` calls `probe` without `stdin`.

Commit: `feat(coordinator): identity gate derives handle and profile on the first team-mode write`.

### Step 6: wire the gate into the coordinator and the client

Changes:

- `taskmaster/coordinator/service.py:79-121` (`__init__`): `self.team_identity = IdentityGate(self.root, store_id=self.identity["store_id"], checkpoint=lambda stage: self.checkpoint(stage))`. The lambda is there because tests replace `owner.checkpoint` after construction (`tests/test_native_sync_tool.py:72`).
- `service.py:262-272` (`_writer`): after `connection = self._connect()` and before `commands.execute`, add `if self.team_identity.admit(connection, work.request): self.export_needed.set()`. A raised `IdentityRefused` takes the existing `except BaseException` path (`:279-287`), which fails the future with that exception. No receipt is written.
- `taskmaster/coordinator/client.py:134-139`: add `'IdentityRefused': IdentityRefused` to the error map, and include it in the `may_have_committed = False` branch. Unmapped types currently become `ServiceUnavailable` (`:137`), which tells the caller to retry, and that's wrong here.

Tests in `tests/test_native_service_identity.py`, through a real `Coordinator` and the bs tools (twins at the native root; git stubbed via `identity_gate.read_git_identity`; team mode via a `project.set` of `team.sync.mode: manual` in one test and monkeypatch elsewhere):

- `test_first_team_mode_tool_write_creates_identity_profile_and_task`:
  - `bs.backlog_add_task(title="Team task", epic="test-epic", phase="dev")` succeeds.
  - `identity.yaml` has `confirmed: false`.
  - The manifest is `'4'`.
  - Profile `jdoe` is in the DB.
  - After `client.flush`, `.taskmaster/users/jdoe/profile.yaml` exists.
  - The task exists.
  - `bs.backlog_changes_since(...)` answers without error.
- `test_degenerate_name_refusal_reaches_the_tool_caller`:
  - `bs.backlog_add_task(...)` returns text starting `Error: Refused:` and containing `taskmaster:init-team`, and not containing `same request_id`.
  - No task, no receipt for the request, no `identity.yaml`.
- `test_refusal_crosses_ipc_as_identity_refused`: `Client.execute(envelope)` raises `team_identity.IdentityRefused` with `may_have_committed is False`.
- `test_backlog_sync_still_runs_with_a_degenerate_name`: `bs.backlog_sync()` starts `Sync complete`.
- `test_solo_mode_is_unchanged`: with no `team:` block and a degenerate stub, `backlog_add_task` succeeds. There's no `identity.yaml` and no profile, the stub is never called, and the manifest stays `'3'` across a coordinator restart.

Commit: `feat(coordinator): admit writes through the identity gate; IdentityRefused crosses IPC`.

---

## 5. Files

Create:

- `taskmaster/team_identity.py`
- `taskmaster/native/profiles.py`
- `taskmaster/coordinator/identity_gate.py`
- `taskmaster/native/upgrade.py`
- `tests/test_team_identity.py`
- `tests/test_native_schema_upgrade.py`
- `tests/test_native_profile_kind.py`
- `tests/test_identity_gate.py`
- `tests/test_native_service_identity.py`

Modify:

- `taskmaster/native/schema.py`
- `taskmaster/native/db.py`
- `taskmaster/native/commands.py`
- `taskmaster/native/lifecycle.py`
- `taskmaster/native/batch_commands.py`
- `taskmaster/native/relations.py`
- `taskmaster/native/migrate.py`
- `taskmaster/projection_parse.py`
- `taskmaster/store.py`
- `taskmaster/coordinator/sync_files.py`
- `taskmaster/coordinator/service.py`
- `taskmaster/coordinator/client.py`
- `tests/conftest.py`
- `tests/fixtures/native_contracts.json`
- `docs/native-store.md` (Compatibility table only, Step 2i)

Not touched: `taskmaster/project.py` (team-sync-001's file, §2), `backlog_server.py` (no new tool), and the skills.

## 6. Commands

Run these from the worktree root in Git Bash. `uv` builds a worktree-local venv on first use; it's ignored, so the tree stays clean.

Step tests:

```bash
uv run --extra dev python -m pytest tests/test_team_identity.py tests/test_native_schema_upgrade.py tests/test_native_profile_kind.py \
  tests/test_identity_gate.py tests/test_native_service_identity.py -q -p no:cacheprovider \
  --basetemp="$TEMP/tm-id001"
```

Regression subset (after steps 2, 3 and 6, and before reporting done):

```bash
uv run --extra dev python -m pytest tests/test_native_schema.py tests/test_projection_parse.py \
  tests/test_native_sync_files.py tests/test_native_sync_prepare.py tests/test_native_sync_tool.py \
  tests/test_native_relations.py tests/test_native_graph_repair.py tests/test_native_workflow.py \
  tests/test_native_contracts.py tests/test_native_commands.py tests/test_native_lifecycle.py \
  tests/test_native_projection_drain.py tests/test_native_service.py tests/test_native_service_routing.py \
  tests/test_native_routing_batch.py tests/test_native_compatibility_sql.py tests/test_store_projection.py \
  tests/test_native_migration.py tests/test_native_cutover.py tests/test_native_init.py tests/test_native_service_build.py \n  tests/test_native_routing_hooks.py tests/test_bounded_run.py -n 3 -q -p no:cacheprovider --basetemp="$TEMP/tm-id001-reg"
```

Don't edit sources while a run is in progress: the build fingerprint changes and about 13 real-coordinator tests fail spuriously. Leave the full suite (about 20–30 minutes at `-n 3`) to the review gate.

## 7. Risks

- **R1 Turning team mode on is one-way for downgrades.** Solo stores stay at 3 and remain openable by 7.4.x. The first team-mode write upgrades the store to 4, after which 7.4.x refuses it cleanly, and turning team mode off doesn't revert it. The release notes must say so, and that every teammate upgrades before enabling (§11's protocol gate already requires the new build for sync). Never run a dev-build coordinator in team mode against a real store; tests use twins.
- **R2 team-sync-001 interface.** The gate depends on `team_mode_enabled(root, *, project_doc=...)` as confirmed at plan review, and always passes the DB row. If the final signature differs, only the §2 import line and the one call site change. The `local/team.yaml` override (their D3) is theirs; the D16 cache already keys on that file's stat.
- **R3 Writer latency.** The first team-mode write runs two git probes under `execution` (normally about 50 ms each; the worst case is 2 × 5 s, then backed off). Later writes cost one indexed `entity_core` lookup and two `stat` calls.
- **R4 Merge overlap.** `store-perf-001` also changes the writer path in `service.py`. The gate is a two-line insertion at `:271`; rebase onto it if it lands first.
- **R5 Profiles surface in generic feeds.** The `entities` SQL view and `backlog_changes_since` will show `kind='profile'` rows. This is intended (shared entities); `backlog_search` filters by explicit kinds (`backlog_server.py:3255`), so it's unaffected.
- **R6 Bypass guard.** Adding `users` to `_GUARDED_DIRS` could flag an existing test that writes under `users/`. Checked at `1b7a7f4`: none does (the only `users` hits are Linear API payloads in `test_linear_*.py`).

## 8. Left for later tasks (mention, don't build)

- `profile.update`: adding an email, renaming the display name, or claiming an existing handle from the confirmation UI (006).
- Owner-only enforcement for `users/<handle>/` on import (team-sync).
- `IdentityGate.current()` as the source of `actor = {handle, agent, clone_id}` (002).
- `init-team` (team-sync-010) calling the bootstrap directly.
- A `docs/native-store.md` entry for the `profile` kind once the epic ships.
