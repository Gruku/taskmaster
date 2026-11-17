<!-- User intent: the executable, test-first plan for team-sync-001, the project.yaml `team:` block: every team setting has
     one typed, validated home with safe defaults (sync off), a clone can override only its sync mode, and the team
     protocol can change only through an explicit upgrade, so every later team task reads its settings from here. -->

# team-sync-001: the `project.yaml` `team:` configuration block

**Goal:** `team:` from spec §11 loads with defaults (sync off) through the existing `project.yaml` dataclasses,
`.taskmaster/local/team.yaml` can override only `sync.mode`, invalid values are refused with a clear error, and
`team.protocol` changes only through an explicit upgrade (this task ships the guarded setter and validation; the
`init-team upgrade` skill is team-sync-010).

**Spec:** `docs/specs/2026-10-31-team-collaboration-native-design.md` rev 3.2: §11 (the block), §6 intro and §6.6
(mode, debounce, linger), §5 (claim_wait_s, plan.fade_days), §6.4/§6.7/§6.8 (remote, state_branch), §6.9
(linear_clone), §7 (ref_roots), §12 (protocol/versioning), R1.

**Backlog:** claude-tools `team-sync-001` (full lane, gate `plan-review`). Branch `feature/team-sync-001`, worktree
`C:\Users\gruku\Files\Claude\taskmaster\.worktrees\team-sync-001`. Do not push; do not touch `uv.lock`, `CHANGELOG.md`
or the version (release work comes at the end of the epic).

**Interface contract (other tasks plan against it — do not rename):** `ProjectManifest.team` is a `TeamConfig`;
`manifest.team.sync.mode in {"off","manual","auto"}`, default `"off"`; `team_mode_enabled(root) -> bool` applies the
`local/team.yaml` override. team-identity-001 calls `team_mode_enabled`.

---

## 1. Ground truth (read these before Task 1)

| What | Where | What it means for this task |
|---|---|---|
| Manifest dataclasses | `taskmaster/project.py:66-192` (`ProjectManifest` at 181-192), added in de33c35 | Add `TeamSync`, `TeamPlan`, `TeamConfig` here and a `team` field on `ProjectManifest`. No parallel loader. |
| Validator | `taskmaster/project.py:313-403` (`validate_manifest_dict`), `ValidationError` 283-284, `_check_enum` 287-289 | Team validation is a helper called from here; error strings follow the `"<dotted.path>: <problem>"` style. |
| Coercion | `taskmaster/project.py:421-461` (`_dict_to_dataclass`, `_coerce_field`) | Ignores unknown keys; `None` section → defaults (`_coerce_field` 450-451); `list` with `None` → `[]` (445). Types are **not** checked here, so validation must run first. |
| Soft loaders | `taskmaster/project.py:464-507` | Any validation error makes the **whole** manifest `None` (logged at WARNING). `load_project_manifest_raw` returns the raw dict untouched. |
| Expanded dump | `taskmaster/project.py:510-512` (`manifest_to_dict` = `asdict`) | `backlog_project_get` output will gain a `team` key with defaults; that is the documented "expanded form" (`backlog_server.py:12799-12808`). |
| YAML loader | `taskmaster/yaml_io.py:18-20` (PyYAML, YAML 1.1) | Verified: `mode: off` loads as `False`, `mode: on` as `True`; `yaml.dump({"mode": "off"})` writes `mode: 'off'`, a stored `False` re-renders as `mode: false`. |
| Legacy write | `taskmaster/backlog_server.py:12850-12871` (`backlog_project_set`) → `_write_project_manifest` 12873-12911 (`apply()` 12888-12902: `tx.get` / `tx.create` / `tx.put`) | Validates with `validate_manifest_dict(raise_on_error=True)` (12863), so team validation reaches it for free. Protocol guard goes in `apply()`. |
| Native write | Adapter `taskmaster/native_routing/overview.py:234-248` validates then `call.execute("project.set", {"document": data})`; admission `taskmaster/native/workflow.py:255-271` (pure, no DB: `test_native_workflow.py:628-641` asserts zero statements); handler `_project_set` `workflow.py:1399-1410` reads the stored row inside the writer transaction | Validation reaches admission through `validate_manifest_dict`. The protocol guard needs the stored row, so it belongs in the handler. The upgrade flag is a new `project.set` argument (`_keys` 67-69, `_flag` 46-50). No new operation, so `NATIVE_MAPPING` (`test_native_workflow.py:790-791`) is unchanged. |
| Native reads | `overview.py:189-199` (`_manifest_raw` from the DB row), `native_routing/tasks.py:83-93`, `native_routing/hook_reads.py:86-93` (`project_fields`) | `.team` appears on every native manifest read automatically via `_dict_to_dataclass`. Coordinator code has the DB row's fields in hand; that is what `project_doc=` is for (below). |
| Storage | `native/schema.py:29,42`: project fields are owned by the `configuration` table (66), one row per **top-level** key, value JSON; `native/commands.py:23-27` writes it, `native/queries.py:87-93` reads it | A nested `team:` dict is one `configuration` row (`field='team'`) and round-trips as JSON; unknown nested keys survive because the document is stored verbatim. No schema change. |
| Export | `native/commands.py:113-114` (`project` → `project.yaml`); `store.py:6219-6220` renders `yaml.dump(doc, sort_keys=False)` | Insertion order kept; strings that look like booleans get quoted. |
| Import of hand edits | `projection_parse.classify` `projection_parse.py:42-57` (`project.yaml` → kind `project`); `authored_rows` 181-186 (document verbatim, no filtering); `coordinator/sync_prepare.py:113-141` (parse, then quarantine pattern used for inline tasks at 131-141); `sync_merge.protect_local` `native/sync_merge.py:72-88` (nothing protected for `project`); `merge` 91+ (three-way **per top-level field**) | A hand edit to `team:` imports today with no checks. Add a project-only refusal next to the inline-task one. `team` merges as one field: concurrent DB and file edits of different `team` subkeys conflict rather than merge (accepted, see Risks). |
| `local/` | `taskmaster_v3.py:391-393` (`local_dir`); `coordinator/checkouts.py:152` and `coordinator/sync_files.py:3` never treat `local/` as a projection; `classify("local/team.yaml")` raises | `local/team.yaml` is machine-local, never imported or exported, never shared. No existing generic `local/*.yaml` reader; add one small reader. |
| Root | `taskmaster/root.py:356-388` (`resolve_root`: `TASKMASTER_ROOT` → git common dir → walk-up) | In a linked worktree the root is the main checkout, which is where `local/` lives. The helpers take that root; they never call git. |

Baseline (2026-10-31, this worktree): the targeted command in §6 passes 313 tests in ~3 min 15 s at `-n 3`.

---

## 2. The block: shape, defaults, validation

### 2.1 Dataclasses (in `taskmaster/project.py`, after `ProjectIdentity`, before `ProjectManifest`)

```python
TEAM_SYNC_MODES = ("off", "manual", "auto")
DEFAULT_TEAM_PROTOCOL = 1
SUPPORTED_TEAM_PROTOCOLS: tuple[int, ...] = (1,)   # protocols this build can sync
DEFAULT_TEAM_REF_ROOTS = ("docs/superpowers",)

@dataclass
class TeamSync:
    mode: str = "off"
    remote: str = "origin"
    state_branch: str = "taskmaster-state"
    debounce_s: int = 30
    claim_wait_s: int = 5
    linger_s: int = 120

    def __post_init__(self) -> None:
        # YAML 1.1 reads an unquoted `mode: off` (the spec's own example) as False.
        if self.mode is False:
            self.mode = "off"

@dataclass
class TeamPlan:
    fade_days: int = 7

@dataclass
class TeamConfig:
    protocol: int = DEFAULT_TEAM_PROTOCOL
    sync: TeamSync = field(default_factory=TeamSync)
    plan: TeamPlan = field(default_factory=TeamPlan)
    linear_clone: str = ""
    ref_roots: list[str] = field(default_factory=lambda: list(DEFAULT_TEAM_REF_ROOTS))
```

`ProjectManifest` gains `team: TeamConfig = field(default_factory=TeamConfig)`, placed after `conventions` and
before `extensions`. Nothing else in `ProjectManifest` changes.

### 2.2 Validation rules

Implement `team_block_errors(document: Mapping) -> list[str]` (looks only at `document.get("team")`) and call it from
`validate_manifest_dict` (append its errors to `errors` just before `ok = not errors`). Absent keys are fine
everywhere; every rule below applies only to a key that is present.

| Path | Rule | Error text (exact prefix; tests match on it) | Why / consumer |
|---|---|---|---|
| `team` | mapping, or `null` (= absent) | `team: must be a mapping` | — |
| `team.protocol` | `type is int`, `>= 1`. **Not** limited to `SUPPORTED_TEAM_PROTOCOLS` | `team.protocol: must be an integer >= 1` | §11/M6: an old clone must still load a newer branch's manifest so it can refuse to sync and say so; the sync engine checks `team_protocol_supported`. |
| `team.sync`, `team.plan` | mapping or `null` | `team.sync: must be a mapping` / `team.plan: must be a mapping` | — |
| `team.sync.mode` | `False` (unquoted `off`) or one of `TEAM_SYNC_MODES`. `True` gets a hint | `team.sync.mode: 'x' is not one of ['off', 'manual', 'auto']`; for `True`: `team.sync.mode: an unquoted on/yes/true reads as a YAML boolean; write "manual" or "auto"` | §6: the engine is off unless `manual`/`auto`. |
| `team.sync.remote` | `str`, matches `^[A-Za-z0-9][A-Za-z0-9._/-]*$` (remote **name**, not URL) | `team.sync.remote: must be a git remote name (letters, digits, . _ / -; not starting with -)` | §6.4/§6.8 pass it to `git fetch/push` argv; the file is shared, so a value starting with `-` would be an option injected by a teammate. URLs differ per clone and could carry credentials. |
| `team.sync.state_branch` | `str`, matches `^[A-Za-z0-9][A-Za-z0-9._/-]*$`, and none of: `..`, `//`, `@{`, ends with `/`, `.` or `.lock`, a `/`-component starting with `.`, equals `HEAD` | `team.sync.state_branch: must be a valid branch name` | §6.4 orphan branch, §6.8 guard-hook allow-rule on `refs/heads/<state_branch>`. A subset of `git check-ref-format`, without spawning git. |
| `team.sync.debounce_s` | `type is int`, `>= 1` | `team.sync.debounce_s: must be an integer >= 1` | §6.6 debounced push; 0 would push on every write. |
| `team.sync.claim_wait_s` | `type is int`, `0 <= v <= 30` | `team.sync.claim_wait_s: must be an integer from 0 to 30` | §5: `backlog_pick_task` blocks a tool call for this long; MCP clients time out. |
| `team.sync.linger_s` | `type is int`, `>= 0` | `team.sync.linger_s: must be an integer >= 0` | §6.6 coordinator lifetime. |
| `team.plan.fade_days` | `type is int`, `>= 1` | `team.plan.fade_days: must be an integer >= 1` | §5 plan fading. |
| `team.linear_clone` | `str`, no whitespace or control characters, `len <= 128`; `""` allowed | `team.linear_clone: must be a clone id (no spaces), or empty` | §6.9 single Linear clone. clone_id format belongs to team-identity-001, so only the obviously wrong is refused. |
| `team.ref_roots` | `list` (not `null`; `[]` allowed) of non-empty `str`; each a relative POSIX path: no leading `/`, no drive (`X:`), no `\`, no `..` segment | `team.ref_roots: must be a list of relative paths`; per item `team.ref_roots[i]: must be a relative path inside the repository` | §7 rewrites `[[old-id]]` in these files; a shared setting must not point the rewriter outside the repo. `null` is refused because the coercer would turn it into `[]`, not the default. |
| Unknown keys under `team`, `team.sync`, `team.plan` | accepted and preserved | — | Forward compatibility (§16 already anticipates `team.ids`); a newer build's keys must not break an older clone's manifest. |

"Integer" means `type(v) is int` everywhere: booleans and floats are refused (floats would also make the §6.1
canonical form unstable, §16 risk).

### 2.3 Local override (`.taskmaster/local/team.yaml`)

Shape (mirrors the shared block, so the setting has one name, `team.sync.mode`, in both files):

```yaml
sync:
  mode: manual
```

- Missing file, empty file, `{}` or `sync: {}` / `sync: null` → no override.
- Top-level value not a mapping, any key other than `sync`, any key under `sync` other than `mode`, or an invalid
  `mode` (same rule as §2.2, including the unquoted-`off` case) → `TeamConfigError` naming the file and the keys:
  `local/team.yaml: only sync.mode can be set per clone (found: remote)` /
  `local/team.yaml: sync.mode: 'x' is not one of ['off', 'manual', 'auto']`.
- The override applies **only when `project.yaml` has a `team:` mapping** (decision D3).

### 2.4 Protocol guard

Effective protocol of a document: `document["team"]["protocol"]` when it is a valid int, else
`DEFAULT_TEAM_PROTOCOL` (no manifest, no `team`, or no `protocol` all read as 1). Rule: **any write that changes the
effective protocol is refused unless it is an explicit upgrade.** That covers 1→2, absent→2, and dropping
`protocol: 2` (or the whole block) back to the default.

An explicit upgrade is accepted only when all hold: a stored manifest exists; the new protocol is greater than the
stored one; it is in `SUPPORTED_TEAM_PROTOCOLS`; and the document equals the stored one apart from `team.protocol`
(compare canonical JSON, `json.dumps(sort_keys=True)`, after removing `team.protocol` from both and dropping a
`team` mapping left empty by that removal).

Error texts (exact prefixes; shared by every site so legacy and native read the same):

- no upgrade: `team.protocol changes only through 'init-team upgrade' (stored {old}, requested {new}); keep protocol: {old}`
- not higher: `team.protocol upgrade must raise the protocol (stored {old}, requested {new})`
- unsupported: `team.protocol {new} is not supported by this taskmaster build (supports {list}); install a newer taskmaster before upgrading`
- extra changes: `a team.protocol upgrade changes only team.protocol; apply other edits with a normal project.set first`
- no manifest: `a team.protocol upgrade needs an existing project manifest`

Sites:

1. Native `project.set` handler (`workflow.py:1399-1410`), with the new argument `team_protocol_upgrade` (bool,
   default `False`). `create_only` + upgrade is refused with the "no manifest" text when no row exists, and is
   otherwise irrelevant (create_only already refuses an existing row).
2. Legacy `_write_project_manifest.apply()` (`backlog_server.py:12888-12902`): always `upgrade=False` (R1: team sync
   needs the native store, so the legacy store never upgrades).
3. Native hand-edit import (`sync_prepare.py`, after the inline-task block ending at 141): for `kind == "project"`
   only, `team_import_refusal(current_fields, authored_doc)` returns a reason, which quarantines the file exactly like
   the inline-task case (`take_file` raises `ValueError(f"{rel} cannot be taken: {reason}")`; unchanged quarantined
   bytes return `still_quarantined`). The same helper also refuses a hand edit whose `team` block fails §2.2, so a bad
   shared value never enters the DB silently.

Not guarded, on purpose: the legacy store's file scan (solo, R1), adoption/cutover (no prior state to protect), and the
future `team.import` from a teammate (team-sync-006/007 must accept a supported upgrade arriving on the branch and
refuse to sync on an unsupported one; it gets `team_protocol_supported` for that). The guard lives in these three
write paths, never in the transaction layer, so `team.import` is not blocked by it.

---

## 3. Final interface (all in `taskmaster/project.py`)

```python
TEAM_SYNC_MODES: tuple[str, ...]                 # ("off", "manual", "auto")
DEFAULT_TEAM_PROTOCOL: int                       # 1
SUPPORTED_TEAM_PROTOCOLS: tuple[int, ...]        # (1,)
TEAM_LOCAL_RELATIVE: Path                        # Path(".taskmaster") / "local" / "team.yaml"

class TeamSync / TeamPlan / TeamConfig           # §2.1; ProjectManifest.team: TeamConfig

class TeamConfigError(ValidationError): ...      # invalid team block or local/team.yaml
class TeamProtocolError(ValueError): ...         # guard refusals (§2.4)

def team_block_errors(document: Mapping) -> list[str]
def team_config_from_document(document: Mapping | None) -> TeamConfig
    # validates only the team block; raises TeamConfigError; None/{}/no team -> TeamConfig()
def local_team_mode(root: Path) -> str | None
    # the local/team.yaml override, or None; raises TeamConfigError
def load_team_config(root: Path, *, project_doc: Mapping | None = None) -> TeamConfig
    # effective config: project_doc if given, else <root>/.taskmaster/project.yaml; override applied (D3); raises
def team_mode_enabled(root: Path, *, project_doc: Mapping | None = None) -> bool
    # load_team_config(...).sync.mode in ("manual", "auto"); never raises: any error logs a WARNING and returns False
def team_protocol_supported(protocol: int) -> bool
def effective_team_protocol(document: Mapping | None) -> int
def check_team_protocol_change(before: Mapping | None, after: Mapping, *, upgrade: bool = False) -> None
    # raises TeamProtocolError
def with_team_protocol(document: Mapping, protocol: int) -> dict
    # deep copy with team.protocol set (creates team: if absent); never mutates the input
def team_import_refusal(current: Mapping | None, authored: Mapping) -> str | None
    # reason to quarantine a hand-edited project.yaml (invalid team block or protocol change), else None
```

Native command: `project.set` accepts `{"document": dict, "create_only": bool, "team_protocol_upgrade": bool}`.
No MCP tool exposes `team_protocol_upgrade` yet (team-sync-010 decides the tool surface).

`root` is the project root that owns `.taskmaster/` (what `resolve_root().root` returns: the main checkout, also from a
linked worktree). Coordinator-side callers on a native store pass the DB row's fields as `project_doc`
(`reads.get(snapshot, "project", "__project__")["fields"]` or `hook_reads.project_fields`), because `project.yaml` is an
export and can lag the DB or carry an unsynced hand edit. Without `project_doc`, the file is read.

---

## 4. Decisions (spec left these open)

| # | Decision | Reason |
|---|---|---|
| D1 | Unquoted `mode: off` (YAML `False`) is accepted and reads as `"off"`; `True` (`on`/`yes`) is refused with a hint. Stored documents are never rewritten. | The spec's own example is unquoted; refusing it would make §11 invalid as written, and the import/write paths store documents verbatim today. |
| D2 | `local/team.yaml` holds `sync: {mode: ...}`; anything else is an error. | One dotted name in both files; the spec allows only `mode` per clone. |
| D3 | The local override applies only when `project.yaml` has a `team:` mapping. | A local file must not switch on sync for a project nobody ran `init-team` on. |
| D4 | `team_mode_enabled` fails closed (False + WARNING); `load_team_config` raises. | A gate must not raise into callers' hot paths, and "off" is the safe failure; callers that need the reason call `load_team_config`. |
| D5 | `team_mode_enabled`/`load_team_config` validate only the team block, so an unrelated manifest error (say a bad `repos` entry) does not switch sync off. `ProjectManifest.team` keeps the existing soft-loader behaviour (whole manifest `None` on any error). | A typo elsewhere must not silently stop a team's sync; the dataclass path stays consistent with the existing loader. |
| D6 | Unknown keys inside `team:` are accepted and preserved; `local/team.yaml` is strict. | Shared file: forward compatibility across builds. Local file: the spec allows one key. |
| D7 | Validation accepts protocols above `SUPPORTED_TEAM_PROTOCOLS`. | §11/M6: an old clone must load the manifest to refuse sync and name the fix. |
| D8 | Absent protocol = 1, and any change of the effective protocol needs the upgrade path, including removing the block. | One rule with no special cases; removal is a downgrade. |
| D9 | The upgrade is `project.set` with `team_protocol_upgrade: true`; it must raise the protocol to a supported value and change nothing else. No new MCP parameter yet. | Smallest surface (no new native operation or inventory entry); a protocol-only write is auditable; the tool surface is team-sync-010's call. |
| D10 | Guard sites: native `project.set`, legacy `_write_project_manifest`, native hand-edit import. Not the legacy scan, adoption or `team.import`. | §2.4. |
| D11 | Integers only; bounds as in §2.2. | Canonical-form stability (§16) and the blocking `claim_wait_s`. |
| D12 | `remote` is a remote name; `state_branch` is checked against a `check-ref-format` subset; neither may start with `-`. | Shared values reach git argv on every clone. |
| D13 | `ref_roots` must be relative POSIX paths inside the repo; `null` refused, `[]` allowed. | §7 rewrites these files on teammates' clones. |
| D14 | The helpers take the project root and never run git. | Pure and cheap; `resolve_root` already maps a linked worktree to the main checkout. |
| D15 | A hand-edited `project.yaml` with an invalid `team` block is quarantined (new); other invalid manifest fields keep today's behaviour. | The requirement "invalid values are rejected with a clear error" for a shared file; widening that to other fields is out of scope. |

**Spec gap to raise (not solved here):** §11 says a clone whose build does not support the branch's protocol "names
the version to install". An old build cannot know which release introduced protocol N. Either `init-team upgrade`
also writes the minimum version into the shared block (a §11 change), or the message says "install a taskmaster that
supports team protocol N". Owner: team-sync-006/010. This plan adds no such field.

---

## 5. Files

| File | Change |
|---|---|
| `taskmaster/project.py` | Dataclasses, `ProjectManifest.team`, constants, exceptions, the helpers in §3, `team_block_errors` call inside `validate_manifest_dict`. Add `import json`, `import re` and `from collections.abc import Mapping` at the existing import sites. |
| `taskmaster/native/workflow.py` | Admission (255-271): allowed keys gain `team_protocol_upgrade`, checked with `_flag`. Handler `_project_set` (1399-1410): call `check_team_protocol_change(entity_fields_or_None, document, upgrade=...)` before `create`/`replace`. |
| `taskmaster/backlog_server.py` | `_write_project_manifest.apply()` (12888-12902): read the existing doc (drop `BODY_KEY`) and call `check_team_protocol_change(existing_or_None, document)` before `create`/`put`. |
| `taskmaster/coordinator/sync_prepare.py` | After 141: project-only `team_import_refusal` → quarantine (same shape as the inline-task block). |
| `tests/test_project_team.py` | **New**, starts with a `"""User intent: ..."""` docstring header. Pure unit tests for Tasks 1–4. |
| `tests/test_native_workflow.py` | Task 5 tests beside `test_project_settings_*` (598-641). |
| `tests/test_native_routing_overview.py` | Task 6 twins tests beside `test_project_manifest_tools_match` (75-100). |
| `tests/test_native_sync_prepare.py` | Task 7 tests, with a fixture that seeds a manifest. |

No change: `overview.py` adapters (they already validate with `validate_manifest_dict`), `projection_parse`, the
renderer, the native schema, MCP tool signatures and docstrings, `tests/fixtures/native_contracts.json`.

---

## 6. Test command

Targeted (the full suite takes ~20–30 min; run it only if the orchestrator asks):

```
uv run --extra dev pytest tests/test_project_team.py tests/test_project_dataclasses.py tests/test_project_loader.py tests/test_project_helpers.py tests/test_project_mcp_tools.py tests/test_project_manifest_tools.py tests/test_merge_targets_manifest.py tests/test_merge_targets_mcp.py tests/test_native_workflow.py tests/test_native_sync_prepare.py tests/test_native_routing_overview.py tests/test_native_routing_hooks.py tests/test_native_bypass_gate.py -q -n 3 -p no:cacheprovider --basetemp=%TEMP%\tm-team-sync-001
```

(`--extra dev` brings in xdist; plain `uv run pytest -n 3` fails with "unrecognized arguments". Use a unique
`--basetemp` so pytest doesn't spend minutes deleting an old one. For one failing test drop `-n 3` and add `-x`.)
Inner loop for Tasks 1–4: `uv run --extra dev pytest tests/test_project_team.py -q -p no:cacheprovider`.

---

## 7. Tasks (test-first: write the tests, run them red, implement, run green, commit)

Commit after each task: `git add` only the files that task touched, message `feat(team-sync-001): <what>` (tests
alongside code), ending with `Co-Authored-By: Claude <noreply@anthropic.com>`. No push.

### Task 1 — dataclasses and defaults

Tests in `tests/test_project_team.py` (helper: write a `project.yaml` under `tmp_path/.taskmaster/` with a minimal
valid manifest `{"schema_version": 1, "meta": {"name": "Demo", "slug": "demo"}}` plus whatever the test adds):

- [ ] `test_team_defaults_match_spec_section_11`: `TeamConfig()` has protocol 1, sync `off/origin/taskmaster-state/30/5/120`, plan.fade_days 7, linear_clone `""`, ref_roots `["docs/superpowers"]`; two instances don't share the `ref_roots` list.
- [ ] `test_manifest_without_team_block_loads_with_sync_off`: `load_project_manifest(root).team == TeamConfig()` and `.team.sync.mode == "off"`; `load_project_manifest_raw(root)` has no `"team"` key (raw stays unchanged).
- [ ] `test_existing_manifests_still_validate_and_load` (parametrized over copies of the `backlog_project_init` scaffold, `backlog_server.py:12940-12949`; `MANIFEST` from `test_native_routing_overview.py:19-25`; and the full sample in `tests/test_project_loader.py:test_load_full_manifest_round_trips`): `validate_manifest_dict(doc) == (True, [])`; the loaded manifest's `meta.name`, `repos`, `submodules` and `conventions.policies` match the input; `load_project_manifest_raw` returns a dict equal to `doc` (no `team` injected); `team == TeamConfig()`.
- [ ] `test_full_team_block_round_trips`: every §11 field set to a non-default valid value → each attribute matches, and `manifest_to_dict(m)["team"]` equals the input block.
- [ ] `test_partial_team_block_fills_defaults`: `team: {sync: {mode: manual}}` → mode `manual`, everything else default.
- [ ] `test_null_sections_take_defaults`: `team: null`, `team: {sync: null}`, `team: {plan: null}` each → defaults, and validate ok.
- [ ] `test_unquoted_off_reads_as_off`: YAML text `team:\n  sync:\n    mode: off\n` → validate ok, `.team.sync.mode == "off"`.
- [ ] `test_unknown_team_keys_are_preserved`: `team: {future: 1, sync: {mode: auto, later: x}}` → validate ok; raw dict keeps both keys; `.team.sync.mode == "auto"`.

Implement §2.1. Red → green → commit.

### Task 2 — validation

- [ ] `test_invalid_team_values_are_rejected_with_the_field_path`, parametrized `(team_block, expected_prefix)`, at least: `team: 42`; `protocol: 0`, `"1"`, `True`, `1.0`; `sync: []`; `mode: "sometimes"`; `mode: True` (hint text); `mode: None`; `remote: "-oProxyCommand=x"`, `"git@host:r.git"`, `""`; `state_branch` values `"a..b"`, `"x.lock"`, `"-x"`, `"a/"`, `".hidden/x"`, `"HEAD"`, `"a b"`; `debounce_s: 0`, `2.5`, `False`; `claim_wait_s: -1`, `31`; `linger_s: -1`; `plan: {fade_days: 0}`; `linear_clone: "a b"`, `5`; `ref_roots: None`, `"docs"`, `["/etc"]`, `["C:/x"]`, `["../up"]`, `["docs\\x"]`, `[""]`. Each: `validate_manifest_dict(doc)` returns `ok False` and one error starting with the prefix; `validate_manifest_dict(doc, raise_on_error=True)` raises `ValidationError`; `load_project_manifest` on that file returns `None` (soft loader, unchanged behaviour).
- [ ] `test_valid_edge_values_pass`: `claim_wait_s: 0` and `30`, `linger_s: 0`, `ref_roots: []`, `state_branch: "team/state"`, `remote: "upstream"`.
- [ ] `test_protocol_above_supported_still_validates`: `protocol: 2` → ok, `.team.protocol == 2`, `team_protocol_supported(2) is False`, `team_protocol_supported(1) is True`.
- [ ] `test_project_set_rejects_invalid_team_with_clear_error` (put it in `tests/test_project_mcp_tools.py`, using its `tmp_taskmaster` fixture from `tests/conftest.py:271`): `backlog_project_set` with `mode: sometimes` raises `ValueError` whose text contains `team.sync.mode: 'sometimes' is not one of`.

Implement `team_block_errors` and the call in `validate_manifest_dict`. Commit.

### Task 3 — effective config, local override, `team_mode_enabled`

- [ ] `test_local_override_changes_only_mode`: shared `team: {sync: {mode: auto, debounce_s: 9}}`, local `sync: {mode: manual}` → `load_team_config(root).sync.mode == "manual"`, `debounce_s == 9`.
- [ ] `test_local_override_rejects_other_keys` (param: `{remote: x}`, `{sync: {mode: auto, remote: x}}`, `[1]`, `"auto"`): `load_team_config` raises `TeamConfigError` whose text starts `local/team.yaml:`.
- [ ] `test_local_override_invalid_mode_is_a_clear_error`: `sync: {mode: sometimes}` → `TeamConfigError` containing `sync.mode: 'sometimes' is not one of`.
- [ ] `test_local_override_absent_or_empty_means_no_override` (missing file, empty file, `{}`, `sync: {}`, `sync: null`) → shared mode wins.
- [ ] `test_local_override_ignored_without_team_block`: no `team:` in project.yaml, local `auto` → `team_mode_enabled(root) is False`.
- [ ] `test_team_mode_enabled_truth_table`, parametrized over shared mode {absent block, off, manual, auto} × local {absent, off, manual, auto} → enabled iff the effective mode is manual/auto (with the D3 row for "absent block").
- [ ] `test_team_mode_enabled_fails_closed` (param: bad local file, invalid team block, project.yaml that is not YAML, project.yaml whose top level is a list): returns `False` and `caplog` has one WARNING naming the cause; `load_team_config` raises `TeamConfigError` for the first two.
- [ ] `test_unrelated_manifest_error_does_not_disable_team_mode`: valid `team: {sync: {mode: auto}}` plus `repos: [{name: a}]` (no path) → `team_mode_enabled(root) is True` while `load_project_manifest(root) is None` (D5).
- [ ] `test_project_doc_wins_over_the_file`: file says `auto`, `project_doc={"team": {"sync": {"mode": "off"}}, ...}` → `False`; and with the file deleted, `project_doc` with `auto` → `True`.
- [ ] `test_no_project_yaml_means_off`: empty `.taskmaster/` → `False`, `load_team_config(root) == TeamConfig()`.

Implement `team_config_from_document`, `local_team_mode`, `load_team_config`, `team_mode_enabled`. Read files with
`yaml_io.safe_load` and `encoding="utf-8"`. Commit.

### Task 4 — protocol guard helpers (pure)

- [ ] `test_effective_protocol_defaults_to_one` (None, `{}`, no team, team without protocol, `protocol: "x"`) → 1; `protocol: 3` → 3.
- [ ] `test_protocol_change_without_upgrade_is_refused` (param before/after: 1→2, absent→2, 2→absent block, 2→team without protocol): `TeamProtocolError` starting `team.protocol changes only through 'init-team upgrade' (stored A, requested B)`.
- [ ] `test_unchanged_protocol_passes` (absent→absent, absent→`protocol: 1`, 1→1 with other team edits, None before + doc without team).
- [ ] `test_upgrade_raises_to_a_supported_protocol` (monkeypatch `taskmaster.project.SUPPORTED_TEAM_PROTOCOLS` to `(1, 2)`): stored 1 → `with_team_protocol(stored, 2)` with `upgrade=True` passes.
- [ ] `test_upgrade_refusals` (with `(1, 2)` patched): 2→1 "must raise"; 1→3 "is not supported by this taskmaster build"; before `None` "needs an existing project manifest"; 1→2 plus a `meta.name` change "changes only team.protocol"; stored without a `team` block → `with_team_protocol(stored, 2)` passes (the empty-team rule in §2.4).
- [ ] `test_with_team_protocol_does_not_mutate_input`: input dict deep-equal before/after; result has the protocol; creates `team` when absent.
- [ ] `test_team_import_refusal`: protocol change → reason containing `init-team upgrade`; invalid team value → reason containing `team.sync.mode`; valid mode edit → `None`.

Implement the §2.4 helpers; the guard functions must read `SUPPORTED_TEAM_PROTOCOLS` at call time (module global
lookup), so the monkeypatch works. Commit.

### Task 5 — native `project.set` wiring

In `tests/test_native_workflow.py` (use the existing `workspace` fixture, `run`, `fields`, `MANIFEST`):

- [ ] Add a case to the parametrize of `test_project_settings_refuse_a_manifest_the_loader_cannot_parse`: `{**MANIFEST, "team": {"sync": {"mode": "sometimes"}}}` (refused at admission, zero statements).
- [ ] `test_project_settings_store_the_team_block_as_one_document`: set `{**MANIFEST, "team": FULL_TEAM}` → `fields(...)["team"] == FULL_TEAM`; `SELECT field FROM configuration WHERE field='team'` returns one row.
- [ ] `test_project_settings_refuse_a_team_protocol_change_without_upgrade`: create with `protocol: 1`, then `protocol: 2` → `ValueError` matching `init-team upgrade`; stored protocol still 1.
- [ ] `test_project_settings_upgrade_changes_only_the_protocol` (monkeypatch `taskmaster.project.SUPPORTED_TEAM_PROTOCOLS = (1, 2)`): `run(..., "project.set", {"document": with_team_protocol(stored, 2), "team_protocol_upgrade": True}, ...)` → stored protocol 2; an upgrade carrying a `meta.name` change is refused and nothing changes.
- [ ] `test_project_set_admission_checks_the_upgrade_flag`: `"team_protocol_upgrade": "yes"` → `ValueError("team_protocol_upgrade must be boolean")` with zero statements; an unknown argument still refused.

Implement the admission and handler changes. Commit.

### Task 6 — legacy path and twins parity

In `tests/test_native_routing_overview.py` (existing `twins` fixture):

- [ ] `test_team_block_round_trips_through_both_stores`: `twins.same("backlog_project_set", yaml_content=yaml.safe_dump({**MANIFEST, "team": FULL_TEAM}))`; `twins.same("backlog_project_get")`; `twins.same("backlog_project_get_field", path=p)` for `team.sync.mode`, `team.ref_roots[0]`, `team.plan.fade_days`; `twins.assert_state_matches()`; `twins.assert_files_match()`; for each root, `load_project_manifest(root).team.sync.mode == "manual"` read back from the exported file.
- [ ] `test_manifest_without_team_reads_team_defaults_on_both_stores`: `backlog_project_set(MANIFEST)`; `twins.same("backlog_project_get")` and its `["team"]["sync"]["mode"] == "off"`; `backlog_project_get_field(path="team")` is `None` on both (raw stays unchanged).
- [ ] `test_team_protocol_guard_matches_on_both_stores`: set `protocol: 1`, then try `protocol: 2` on each root (`twins.at(root)`, `pytest.raises(ValueError)`); both messages contain `team.protocol changes only through 'init-team upgrade' (stored 1, requested 2)`; state still matches.

Implement the `_write_project_manifest.apply()` guard. If the native message arrives wrapped, assert with `in`, not
equality, and say so in the commit message. Commit.

### Task 7 — hand-edit import guard

In `tests/test_native_sync_prepare.py`, add a fixture `project_twins` seeding
`bs.backlog_project_set(yaml.safe_dump({**MANIFEST, "team": {"protocol": 1, "sync": {"mode": "off"}}}))` with
`engine_oracle=True`, and a `setup_base`-style helper parametrized on `rel="project.yaml"`:

- [ ] `test_hand_edited_team_protocol_is_quarantined`: rewrite the file with `protocol: 2` → `prepare(...).state == "quarantine"`, reason contains `init-team upgrade`; `prepare(..., take_file=True)` raises `ValueError` starting `project.yaml cannot be taken:`; after `commit_only(connection, "sync.apply", plan.arguments)` the stored protocol is still 1.
- [ ] `test_hand_edited_invalid_team_value_is_quarantined`: `mode: sometimes` → quarantine, reason contains `team.sync.mode`.
- [ ] `test_hand_edited_team_mode_imports`: `mode: manual` plus an unknown `team.future: 1` → state `apply`, `plan.arguments["rows"][0]["fields"]["team"]` has `sync.mode == "manual"` and `future == 1`.
- [ ] `test_unquoted_off_hand_edit_imports`: file text with `mode: off` unquoted (from `manual`) → `apply`, and `team_config_from_document(row_fields).sync.mode == "off"`.

Implement the `sync_prepare.py` block. Keep it free of extra reads: `entity("project", ident or "__project__")` is
already cached by `prepare`, and the helper is pure (store-perf-001 overlap, spec §15). Commit.

### Task 8 — final check

- [ ] Run the §6 targeted command; all green (313 baseline plus the new tests).
- [ ] `git diff master --stat` touches only §5's files plus this plan.
- [ ] Report: tests run and counts, any message-wrapping note from Task 6, and anything noticed but not done.

---

## 8. Risks

- **Whole-manifest soft failure.** A bad `team` value makes `load_project_manifest` return `None`, which also hides
  `repos`, merge targets and policies. Mitigated: every tool write and every native hand-edit import now refuses it
  (D15); `team_mode_enabled` reads the block on its own (D5). A legacy-store hand edit can still introduce it (solo
  stores only).
- **An existing `team:` key with another meaning** now fails validation. None in this repo (searched); user projects
  unknown. Low.
- **File vs DB on native stores.** `team_mode_enabled(root)` without `project_doc` reads the exported file, which can
  lag or carry an unsynced hand edit. Coordinator callers must pass `project_doc` (§3); flag to team-identity-001.
- **Merge granularity.** `team` is one top-level field in the three-way import merge, so a DB edit to `sync.mode` and a
  file edit to `plan.fade_days` conflict instead of merging. Rare; the existing conflict path reports it.
- **Stored `False` renders as `mode: false`.** Harmless for reads (D1); init-team (team-sync-010) should write strings.
- **Twins message parity.** Native handler errors may come back wrapped; Task 6 tolerates that.
- **Overlap with store-perf-001** in `sync_prepare.py`: the added check is pure and project-only.

## 9. Out of scope

The `init-team` / `init-team upgrade` skill and any MCP parameter for the upgrade (team-sync-010); the sync engine's
use of each setting (team-sync-002…008); naming a version to install for an unsupported protocol (spec gap, §4);
validating clone_id format beyond §2.2 (team-identity-001); caching the local file read.
