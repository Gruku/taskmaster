<!-- User intent: on a team server, a teammate's branch, worktree and merge records describe their own checkout, so
     they live per person and per clone instead of overwriting the one shared task field, and every Git operation on
     a teammate's work runs in that teammate's clone, never on the host. -->

# team-server-011: per-clone machine-local fields and client-side managed Git (design, 2026-11-07)

**Task.** `team-server-011` (not in the go-live set). Done when:
- `worktree`, `branch` and merge records written by a teammate live in a per-principal, per-clone table, and reads
  show each caller their own;
- managed Git runs client-side, never on the host, and a ratchet proves it.

**Source.** `docs/specs/2026-10-31-team-server-mode-addendum.md` §8 ("Machine-local fields"), §5 ("Git checks").
**Rulings.** `docs/handoffs/2026-11-01-team-server-lanes/rulings.md`. None names 011 directly. The ones that bind:
- the host holds only the backup push key and a read-only code key;
- 005 made `git_run`, `git_recover`, `git_status` and `sync(worktree=…)` operator-only;
- 005 C3 made blast-radius evidence operator-only;
- 014 untracked `.taskmaster/` on code branches.

**Builds on** (feat/team-collab `87977af`):
- 003: `RemoteSession`, `ChannelSession`, `Requester`, `Transaction.replace`'s requester hook.
- 007: `hook_reads.tasks_for_branch` / `choose_task` (D3a), `remote/`.
- 009: `server_marker` (latch location).
- 005: `coordinator/allowlist.py`.
- 014: `git_hook.check` server mode.

**Clustered with.** `team-server-008` (`docs/specs/2026-11-07-team-server-008-merge-gate-design.md`). The seam is
settled in 008 §11 and mirrored in §10 here.
**Plan.** Embedded, §14.
**Review history.** Rev 1 (`b1cebc92`) failed design review on I5 and the shared S1, plus 5 Minors
(`D:\scratchbook\taskmaster\ts008-review\design-review.md`). Rev 2 settles them:

| Finding | Settled in |
|---|---|
| S1 (the hook channel's clone) | D2: the clone is read from whichever session is bound; 008 D5 puts it on the hook channel |
| I5 (a restore dropped members' branch mappings, so the gate allowed those branches) | new D10: branch rows are exported and restored. This overrides the accepted Q5 for those rows only |

Minors 8-12 are all folded in:
- 8: D8 names the exact host Git argv shapes;
- 9: D4 skips the touch when an update is fully diverted;
- 10: no cap refusal and no eviction (D3, D7);
- 11: D2 names `bs._bound_session()`, and old R1 is dropped;
- 12: the §5 row.

## 1. Problem

A native task row has one `branch` and one `worktree` (`schema.KINDS["task"]`, `task_operational`). In solo they
describe the one checkout. On a team server they describe whichever teammate wrote last:

- **Pick instructions mislead.** `_build_worktree_instruction` tells the next picker "Already recorded at
  `.worktrees/x` — verify it exists and work there" (`backlog_server.py:8310`). That is someone else's directory on
  someone else's machine.
- **Branch names collide** across people (`fix/login`, `wip`), and the gate and recorder see one shared field. 007's
  D3a answers `ambiguous` rather than judge one teammate's merge by another's gate. 008 turns `ambiguous` into a block,
  so collisions now cost a manual approval.
- **A bundle pick overwrites** every member's `branch` and `worktree` with the picker's (`workflow.py:890`).
- **Free-text `worktree` reaches host Git.** 005 C3 found blast-radius evidence walking a task's `worktree` on the
  host; it is operator-only now, but the field still invites it.
- **A merge stamp has no provenance** beyond the event's actor (team-identity-002): nothing says which clone merged.
  008 G5 notes that the shared ladder records a local, unpushed merge.

"Managed Git" (`coordinator/git.py`, `git_cli`) commits and checks out `.taskmaster/` projections under the
coordinator's pin. In server mode, teammates' clones have no projections (014 untrack) and no coordinator (009 fence),
so managed Git has nothing to manage there. The host's own managed Git is operator-only (005). What is left is to make
the remaining host Git paths provably unreachable from a teammate's request (§6), and to give the teammate-side CLI a
clear answer instead of the fence's refusal.

## 2. Scope

In scope:
- clone identity, client and server (D1, D2);
- the `clone_fields` table (D3);
- diverting members' `branch`/`worktree` writes to it (D4), and overlaying them on reads (D5);
- clone-aware branch-to-task lookup for the hooks (D6);
- per-clone merge records (D7);
- the host Git inventory, its ratchet, and the teammate-side `taskmaster git` answer (D8);
- exporting and restoring members' branch rows with 013's state export (D10).

Out of scope:

| Not here | Owner |
|---|---|
| Gate fail-closed, stamp route, client ancestry, stamp queue | 008 |
| The export branch in managed Git's drift walk | 022 (§9) |
| Viewer and list views showing per-clone values | follow-up (G1) |
| Backing up `worktree` and `merge:*` rows in the state export | not planned (Q5): machine-local. `branch` rows are exported (D10) |
| Seeding clone rows during `serve init --from` | none: shared fields keep the operator's values (G4) |
| Mod writes | 007 follow-up (Q5) |

## 3. Decisions

### D1. Clone identity

- **Format:** `c-` followed by 16 lowercase hex characters, random (`secrets.token_hex(8)`).
- **Where:** `<root>/.git/taskmaster-clone.json`, beside 009's latch (`server_marker.latch_path`). `root` is
  `resolve_root(cwd).root` (the main checkout), so every linked worktree of a clone shares one id. The content is
  `{"clone": "c-…", "created": "<UTC>"}`, written owner-only (0600; the owner-only DACL on Windows through
  `ownership`), and published exclusively (`O_CREAT|O_EXCL`), so two hooks racing on first use agree.
- **API:** `taskmaster/remote/clone.py` (stdlib), `clone_id(root, *, create=True) -> str | None`. It answers None when
  `<root>/.git` is not a directory (a submodule's `.git` file, 009 M7), when the file is a link or foreign-owned, or
  when it is unreadable. None means "the person's default clone" (D2).
- **Not a secret.** It names a clone; it authenticates nothing. The handle always comes from the key.
- **Shown by** `taskmaster remote clone-id`, which prints the id, creating it if needed. 016's how-to uses it (D2).

### D2. How a call names its clone

| Caller | Clone |
|---|---|
| Hooks (`merge-gate`, `merge-target`, `merge-stamp`) | `clone_id(root)`, sent as the `clone` parameter that 008 defined (008 §11) |
| An MCP connection | the optional request header `X-Taskmaster-Clone`, read once when `SessionRegistry.bind` creates the `RemoteSession` (new field `clone: str = ""`). A value that fails `^c-[0-9a-f]{16}$` is ignored and logged. A different value on a later request of the same transport is ignored, because the first one wins |
| The viewer, the read channel | `""` |
| Solo, host-local, the operator | not used (D4) |

`""` is **the person's default clone**: one row set per handle that every one of their clones falls back to.

How a teammate sets the header (Q1): the MCP config is per project directory in Claude Code's local scope. 016's
how-to gives the line `claude mcp add --transport http -s local taskmaster <url> --header "Authorization: Bearer <key>"
--header "X-Taskmaster-Clone: $(taskmaster remote clone-id)"`. Without the header, everything still works per person:
MCP writes land in the default row, and lookups fall back to it.

The clone travels to the coordinator in the **command envelope**:
- `coordinator/adapter.py:execute` adds `"clone"` when the bound session has a non-empty one. The adapter holds only
  the session id string (`self.session`), so it reads the session object from `backlog_server._bound_session()`. That
  is a `RemoteSession` (MCP, clone from the header) or the hook `ChannelSession`, whose `clone` 008 D5 fills from the
  `merge-stamp` body (S1). Both expose `clone`, and every other session answers `""`;
- `contracts.validate` accepts an optional `clone` matching the pattern. It is part of the request, so it is in the
  payload hash and replays with the receipt;
- a caller can only ever name its own rows, because the handle in the key (D3) comes from `transaction.requester`,
  never from the envelope.

### D3. The table: `native/clone_fields.py`

```
CREATE TABLE IF NOT EXISTS clone_fields(
  handle TEXT NOT NULL, clone TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, field TEXT NOT NULL,
  value_json TEXT NOT NULL CHECK(json_valid(value_json)), updated_at TEXT NOT NULL, updated_seq INTEGER NOT NULL,
  PRIMARY KEY(handle, clone, kind, id, field))
CREATE INDEX IF NOT EXISTS ix_clone_fields_branch ON clone_fields(field, json_extract(value_json,'$'), kind, id)
  WHERE field = 'branch'
```

- **Created lazily** by `ensure(connection)` inside the coordinator's write transaction, on the first diverted write.
  That is the `native/checkouts.py` precedent for machine-local tables; there is no schema-version bump (Q4).
- **Missing table reads as empty**, so old stores and solo stores are untouched and need no upgrade.
- **Functions:**
  - `ensure`;
  - `put(connection, handle, clone, kind, id, field, value, seq)`, where `value=None` deletes the row;
  - `rows_for(connection, kind, id)`;
  - `own(connection, handle, clone, kind, id) -> {field: value}`: the clone row, else the default row (`clone = ""`);
  - `branch_matches(connection, branch) -> [(handle, clone, id)]`.
- **Fields:** `branch` and `worktree` (text), and `merge:<rung>` (`{"merge_commit", "merged_at"}`, D7).
- **Bounds:**
  - a value is at most 4 KiB, else `ValueError`;
  - a handle may hold at most 64 distinct clone ids. Past that, a write for a new clone id goes to the person's
    default row (`clone = ""`) instead, and the reply says so. Nothing is refused or evicted, because evicting a
    branch row would quietly ungate that branch (D10's concern);
  - at most 1000 rows per (handle, clone).
- **Not readable through `backlog_query`:** `query_guard` reads only its compatibility views, and the read endpoint
  gives no tool that returns these rows except through D5's text.
- **No domain event for a fully diverted write.** When an update changes nothing but `branch`/`worktree`, D4 skips
  the `domain.touch` too, so the shared document, its revision and the event log stay as they were. Teammates' views,
  the projection and `changes` see nothing (G3). An update that also changes another field writes that field, with
  its touch and event, as today. The receipt lists the clone writes (`outcome["clone_fields"]`), so the tool can say
  so. The export's branch file (D10) carries the rows.

### D4. Diverting members' writes (`Transaction.create` / `Transaction.replace`)

The one choke point every task write passes. 003 already checks the requester there (`commands.py:228`).

- **When:** `kind == "task"`, `self.requester is not None`, and `not self.requester.may_force_any` (a member; the
  operator keeps today's shared writes, Q6).
- **What:** `clone_fields.divert(self, ident, before, after) -> after`. For each of `branch` and `worktree` that
  differs between `before` and `after`:
  - it puts the new value under `(requester.handle, request.get("clone", ""), "task", ident, field)`; an empty or null
    value deletes the row;
  - it restores the field in `after` to its stored value, so the shared document doesn't change;
  - it records the write in `transaction.clone_writes`, which `execute` copies to `outcome["clone_fields"]`;
  - when `after` then equals `before` apart from the touch fields (`last_referenced` and the like `domain.touch`
    sets), it writes nothing to the shared document. `divert` runs before the touch is kept: the command functions
    (`_task_update` and the batch line) call `divert` first and touch only when something shared still changes.
- **Covers** `task.update`, `task.batch_line`, `task.viewer_update`, `task.viewer_create`, `task.create`, and the
  bundle path of `task.pick` (`workflow.py:890`), because they all end in `create` or `replace`.
- **Replies.** The native adapters that report a branch or worktree change (`update_task`, the batch-line renderer)
  check `outcome["clone_fields"]` and say `branch recorded for your clone (c-1a2b…; teammates keep their own)`. An
  update that diverted everything and changed nothing else says so, rather than "no change".
- **Unchanged:** solo, host-local and the operator, because `requester` is None or `may_force_any`. A test asserts
  byte-identical documents and events for those callers.

### D5. Reading: the caller's overlay

`clone_fields.overlay(connection, doc, handle, clone) -> doc` replaces `branch` and `worktree` with the caller's clone
row, else their default row, else the shared value. It is called right after `reads.find_task` in exactly these
adapters:

| Adapter | What changes |
|---|---|
| `backlog_pick_task` (single and bundle) | `_build_worktree_instruction` gets the caller's `branch`/`worktree`, so "Already recorded at …" names only the caller's own worktree; a teammate's never shows |
| `backlog_get_task` | the slim and verbose views show the caller's values, plus one line `Branches by person: ann: feature/x; bob (2 clones): fix/y` from `rows_for` (branch names only; other people's worktree paths never) |
| `backlog_context` | the task section shows the caller's values |

The caller is `bs._bound_session()` (handle, clone). No overlay applies outside a serve daemon. Lists, the viewer, the
projection, the export and `backlog_query` keep the shared field (G1).

### D6. Branch to task, for the hooks: clone-aware candidates

- **Solo:** `hook_reads.tasks_for_branch(con, identity, branch)` stays exactly as it is (the solo recorder and 007's
  golden views call it).
- **Server:** a new `hook_reads.branch_candidates(con, identity, branch, principal, clone) -> (candidates, own)`.
  - The candidates are the union of:
    - `tasks_for_branch`'s answer (the shared field);
    - the live, unarchived tasks with any `clone_fields` branch row equal to `branch`, from **any** person.
  - Each candidate is `(id, fields)`, ordered by id.
  - `own` is the set of ids where the caller's handle has a matching row for `clone` or for `""`.
  - The views call it whenever they have a principal.

`choose_task(candidates, principal=None, own=frozenset())` keeps D3a and adds one rule ahead of it: **exactly one
candidate in `own` → that task, `mine: true`.** `_is_callers` also counts a candidate in `own` as the caller's.

Why every person's rows are searched, not only the caller's: the gate must still find a teammate's task when the
caller merges the teammate's branch. Searching only the caller's rows would let anyone merge a colleague's ungated
branch, which 008 Q3 rejects. What changes is that each person's own mapping wins over a collision, so `ambiguous`
becomes rare.

`merge_gate_view`, `merge_target_view` and 008's `merge_stamp_plan` already take `principal` and `clone` (008 §11).
With a principal, each calls `branch_candidates` instead of `tasks_for_branch`, and passes `own` to `choose_task`.

### D7. Per-clone merge records

- **What `_task_merge` writes:** when the command has a member requester, `_task_merge` also puts
  `merge:<rung> = {"merge_commit": sha, "merged_at": merged_at}` for `(handle, request.get("clone", ""), "task", id)`.
  This sits next to the shared `merge_status`, which stays exactly as today and as 008 writes it.
- **Shown in** `backlog_get_task` as `Merges from your clone: develop abc1234 (2026-11-07T10:15)`.
- **It never fails the merge write.** If the row can't be written (a bound, D3), it is skipped and logged. Raising
  inside `_task_merge` would abort the shared stamp.
- **The clone is the hook's.** A hook stamp arrives through 008's route on the hook channel, whose session carries the
  body's `clone` (S1, D2), so the record lands under the clone that merged, not the default row.
- **What it's for** is provenance: which clone recorded which commit. It doesn't change the gate, the ladder or
  `merge_gate_state` (Q3).
- **Order:** it is independent of 008's `expect_commit`. With 008 present, the record is written only when the CAS
  passes, because both are in the same transaction.

### D8. Managed Git runs client-side: the host Git inventory

Every place the server process can start `git`, with its stance after 008 and 011:

| Path | Started by | Stance |
|---|---|---|
| `git_run`, `git_recover`, `git_status` (`coordinator/git.py`) | coordinator methods | operator-only (005 allowlist); members refused |
| `sync(worktree=…)`, `sync take_file` (`checkouts.resolve`) | coordinator method | operator-only (005) |
| checkout drift detection (`checkouts.detect`, `history_blobs`) | host sync | operator-only (host sync is operator-only, 012 Step 1) |
| blast-radius evidence (`blast_radius.py`) | `backlog_blast_radius(mode="evidence")` | operator-only (005 C3). After D4, a member's `worktree` never reaches the shared field it walks |
| merge-stamp replay (`merge_stamps.git_is_ancestor`) | `registry._replay_merge_stamps` | not in a serve daemon (008 D9) |
| merge-stamp ancestry | 008's route | client-side (008 D7) |
| merge-gate tip | the hook | client-side (007) |
| `git_eol.crlf_for`, `tracked_local_files` (`check-attr`, `ls-files` on `.taskmaster/` paths) | host file writes (PROGRESS.md, config) | allowed: the host's own files, read-only Git |
| state export (`serve/state_export.py`) | export cycle | allowed: the host's own backup branch (013), not member-triggered |

**The ratchet** is `tests/test_serve_no_member_git.py`:
- it runs a member key over MCP through every tool 005's gate lets a member call (the gate's own member list), plus
  every `/read/v1/` view, the `tools` batch and `/write/v1/merge-stamp`, against an in-process `ServeDaemon`;
- `subprocess.Popen` is wrapped to record every `git` argv;
- the test fails on any argv outside `ALLOWED_HOST_GIT`, which lists the exact shapes the host runs for its own
  files (`git_eol.py`, `root.py`):

  | argv after `git` | cwd |
  |---|---|
  | `rev-parse --show-toplevel --git-common-dir` | under the host's `.taskmaster/` |
  | `config --list --show-origin -z` | under the host's `.taskmaster/` |
  | `ls-files -z --eol -- .` | under the host's `.taskmaster/` |
  | `ls-files -z --full-name -- local` | under the host's `.taskmaster/` |
  | `check-attr -z --stdin text eol`, every stdin path starting `.taskmaster/` | the host root |
  | `-C <dir> rev-parse <flag>` | `<dir>` inside the host root |

  Every cwd and `-C` path must resolve inside the host root. A shape, cwd or stdin path outside the table fails;
- a new tool that runs Git for a member fails it;
- before each assertion the test drains the in-process projection jobs (`Coordinator.flush` through the committed
  sequence), so Git calls made for an earlier request's export aren't charged to the next one.

**The teammate-side CLI.** In a server-mode clone, `taskmaster git commit|checkout|status|recover` (`git_cli.main`)
answers
`{"state": "not-managed", "detail": "server mode: this clone holds no Taskmaster projections, so there is nothing for
managed Git to do; use plain git (git commit, git checkout). The team's state is backed up on the server's
taskmaster-state branch.", "checkout": "<root>"}`. It exits 0 for `status` and 2 for the others. It checks with
`server_marker.server_mode(root)` before constructing a `Client`, which today ends in the fence's generic refusal. It
doesn't run Git on the user's behalf (Q7). `git-hook pre-commit` is unchanged (014's server-mode rule).

### D9. Unchanged

- Solo stores, host-local calls and the operator: documents, events, replies.
- The read views' `data` shapes (`mine` keeps its meaning, extended by D6).
- The projection format.
- 008's wire.

### D10. Branch rows survive a restore (review I5)

After D4, a member's branch mapping lives only in `clone_fields`. 013's restore rebuilds the store from the exported
projection, which doesn't hold the table. Without the rows the gate finds no task for those branches and answers
ALLOW, which is a verdict, so fail-closed doesn't help. Q5 is therefore overridden for `branch` rows. `worktree` paths
and `merge:*` records stay out: they are machine-local, and losing them doesn't open the gate.

- **Export** (`serve/state_export.py`, the payload assembly beside the documents):
  - one file, `clone-fields/branches.json`. `clone-fields` joins `STATIC_TOP`;
  - content: a JSON list, sorted, of `{"handle", "clone", "kind": "task", "id", "branch"}` for every `branch` row of a
    live task. The file is absent when there are none;
  - each value is checked as it is written: the handle with `HANDLE_RE`, the clone with the clone pattern (or `""`),
    the id with `_identifier`, the branch with `hook_reads.ref_problem`. A row that fails goes to the export's
    existing `dropped` list with its rule;
  - branch names and handles aren't secrets, and the file passes through `tree_path_refusal`/`body_refusal` like
    every other blob.
- **Restore:**
  - `state_export.restore_branches(owner, root, source, *, branch=...)`, called by `serve export restore-documents`
    after the documents. One runbook step (013 §8.1 step 7) now restores both, and the report gains
    `branches_restored`, `branches_refused` and `branches_failed`;
  - each row is submitted as a new operation `clone.restore` (`{"handle", "clone", "kind", "id", "field": "branch",
    "value"}`) through `owner.submit(envelope, origin=LOCAL)`, as `restore_documents` submits `document.import`;
  - `clone.restore` is host-operator-only:
    - the command refuses unless `transaction.requester is None` (host-local);
    - `allowlist.refusal` refuses an `execute` envelope carrying it, or a batch holding it, for anyone but the
      operator, as `_document_import_refusal` does for restores;
  - a row whose task no longer exists is skipped and reported.
- **013's design §8.3** ("what a restore does not bring back") is updated in the same step: 011's `branch` rows do
  come back; worktree paths and merge records don't.
- **Taken back to solo:** a store returned to solo reads only the shared field (`tasks_for_branch`), so member-mapped
  branches are ungated there. That is G6. No supported path takes a team store back to solo today.

## 4. Failure modes

| Condition | Behaviour |
|---|---|
| No `.git` directory (submodule) or clone file unreadable | `clone_id` None: hooks send no `clone`; rows use the default clone |
| No `X-Taskmaster-Clone` header | MCP writes go to the person's default clone; hooks find them through the fallback |
| Malformed header | ignored and logged; default clone |
| Table missing (no diverted write yet) | reads empty; lookups fall back to the shared field |
| 65th clone id for a handle | the write goes to the person's default row, and the reply says so; D7 skips its record and logs |
| The host is restored from the 013 export | members' branch rows come back (D10), so their branches stay gated |
| A member clears `branch` | their row is deleted; the shared field is untouched |
| The task is archived or deleted | rows stay; `tasks_for_branch` only returns live, unarchived tasks, as today |
| Two people, same branch name, each with a row | each caller gets their own task (`own`) |
| A teammate merges a branch only another person's row maps | that task is the only candidate, `mine: false`, gated (008 teammate wording) |

## 5. Threat model

| Attacker | Wants to | Stopped by |
|---|---|---|
| A member key | write another person's clone rows | the handle comes from `transaction.requester`; the envelope's `clone` only selects among the caller's own rows |
| same | hide a task from the gate by mapping its branch elsewhere | D6 searches every person's rows and the shared field. A member's own mapping wins only for their own merges, and it wins in both directions: when they merge a teammate's branch under a colliding name, their own task's gate applies, not the teammate's. That is accepted, because the gate is no boundary against members, who can already rewrite the shared `branch` |
| same | grow the table without bound | 4 KiB per value, 64 clones per handle (then the default row), 1000 rows per clone |
| same | make the host run Git on a path or SHA | the D8 ratchet |
| same | read another person's worktree paths | D5 shows others' branch names only; `backlog_query` can't reach the table |

## 6. Reuse map

| Need | Reused | New |
|---|---|---|
| lazy machine-local table | the `native/checkouts.py` pattern (`ensure`, `CREATE TABLE IF NOT EXISTS`) | `native/clone_fields.py` |
| write choke point | `Transaction.create`/`replace` (003's requester hook) | `clone_fields.divert` |
| branch lookup | `hook_reads.tasks_for_branch` (unchanged, called for the shared part), `choose_task`, `_is_callers` (upgraded in place) | `branch_candidates` |
| owner-only file | `ownership.private_directory_at` / `verify_private`, `server_marker._publish`'s exclusive publish | `remote/clone.py` |
| session state | `RemoteSession` (+ `clone`), `bind` | none |
| envelope | `coordinator/adapter.py:execute`, `contracts.validate` | one optional key |
| host Git refusals | 005 `allowlist` (unchanged) | the ratchet test |

## 7. Files

New:
- `taskmaster/native/clone_fields.py`, `taskmaster/remote/clone.py`;
- tests `tests/test_clone_fields.py`, `tests/test_remote_clone.py`, `tests/test_serve_clone_fields.py`,
  `tests/test_serve_no_member_git.py`.

Changed:
- `taskmaster/native/commands.py` (`Transaction`, `execute` outcome), `taskmaster/native/contracts.py` (envelope
  `clone`), `taskmaster/native/workflow.py` (`_task_merge`);
- `taskmaster/coordinator/adapter.py` (envelope `clone`), `taskmaster/serve/sessions.py` (`RemoteSession.clone`,
  `bind`), `taskmaster/backlog_server.py` (`_bound_session` clone accessor);
- `taskmaster/native_routing/tasks.py` (`pick_task`, bundle pick, `get_task`, `update_task` reply),
  `taskmaster/native_routing/context.py`, `taskmaster/native_routing/hook_reads.py` (`branch_candidates`,
  `choose_task`, `_is_callers`, and the views' calls);
- `hooks/merge_gate_decide.py`, `hooks/merge_recorder_stamp.py` (send `clone`);
- `taskmaster/serve/state_export.py`, `taskmaster/serve/cli.py` (D10 export and restore),
  `taskmaster/coordinator/allowlist.py` (`clone.restore` is operator-only),
  `docs/specs/2026-11-01-team-server-013-state-export-design.md` §8.3;
- `taskmaster/coordinator/git_cli.py` (D8 answer), `taskmaster/remote/cli.py` + `taskmaster_cli.py` (`remote
  clone-id`);
- `tests/fixtures/native_contracts.json` only if a tool's documentation changes (none planned).

## 8. Known gaps

- **G1. Lists and the viewer show the shared field.** A member's branch is visible to teammates only through
  `backlog_get_task`'s "Branches by person" line. A viewer column is a follow-up.
- **G2. Worktree paths and per-clone merge records are not in the state export** (Q5). A rebuilt host loses them,
  and teammates re-record worktrees on the next pick or update. Branch rows are exported and restored (D10), so the
  gate is unaffected.
- **G3. No domain event for a diverted write.** `backlog_changes_since` and the audit trail don't show branch or
  worktree changes by members. The receipt has them.
- **G4. The migration doesn't seed rows.** Shared `branch`/`worktree` values from the solo era stay shared, and the
  lookup still finds them.
- **G5. Without the header, one person's clones share the default row.** Two clones of one person with different
  worktrees for one task show the last write. Hooks, which always send their clone, are unaffected.

- **G6. A store taken back to solo** ignores member branch rows (D10). It isn't a supported path.

## 9. team-server-022 (the export branch in managed Git's drift walk): not absorbed

022 is about the **host's** managed Git: `checkouts.history_blobs` walks `log --all --reflog` in the host clone, and
since 013 that walk includes the `taskmaster-state` history (013 §7.5, "known cost"). 011 is about the opposite side of
the line: keeping teammates' Git off the host. 011 doesn't touch `history_blobs`, `movement` or `detect`, and nothing
in it makes the exclusion easier or harder.

The fix is small and self-contained: exclude the export ref from the walk (`--exclude=refs/heads/<export branch>`
ahead of `--all`, using the configured export branch name), plus one test. Whether `--reflog` also honours
`--exclude` needs a probe on the Git in use; if it doesn't, that ref's reflog needs its own exclusion. It could ride in this lane as a trailing step if the
orchestrator wants, but it isn't part of 011's design or plan.

## 10. The seam with 008 (mirror of 008 §11)

- **008 owns:**
  - the gate's fail-closed behaviour, the launcher and its texts;
  - `/write/v1/merge-stamp`, `Client.write`, `remote/stamps.py` and the client queue;
  - `expect_commit`;
  - no host replay in a serve daemon;
  - `merge_stamp_plan`;
  - **the `clone` wire parameter**: optional `clone` (`^c-[0-9a-f]{16}$`) on `GET merge-gate`, `GET merge-target`
    and the `merge-stamp` body, passed to `hook_reads` as a `clone=None` keyword;
  - **the hook channel carries the clone (S1):** `ChannelSession.clone`, filled by `current()` from the route's
    validated body `clone` (008 D5).
- **011 owns:**
  - clone identity (`remote/clone.py`), the hooks sending `clone`, and the MCP header;
  - `clone_fields`, the diversion and the overlay;
  - the clone-aware `branch_candidates`/`choose_task`, which 008's views and `merge_stamp_plan` switch to when they have
    a principal (a one-line change in each);
  - per-clone merge records in `_task_merge`, keyed on the envelope's `clone`, which the adapter takes from the bound
    session (D2), the hook `ChannelSession` included;
  - the host Git inventory and ratchet;
  - exporting and restoring branch rows (D10).
- **Both edit, in different functions:**
  - `hook_reads.py`;
  - `merge_gate_decide.py` / `merge_recorder_stamp.py`, where 011 adds one `clone` value per request or entry;
  - `workflow.py` `task.merge`, where 008 adds an argument key and 011 adds a write in `_task_merge`;
  - `serve/sessions.py`, where 008 adds the hook channel and its `clone`, and 011 adds the header in `bind`;
  - **011 then 008:** 011's Step 3 adapter test uses a stub hook `ChannelSession` with a `clone` field. If 008 isn't in,
    011 adds that `clone: str = ""` field itself, exactly as 008 D5 defines it, and 008 then only fills it.
- **Order:**
  - 008 then 011 (the default; 008 is go-live): Step 2 below sends `clone` from the hooks, on 008's existing parameter.
  - 011 then 008: Step 2 below first adds the `clone` parameter to `merge-gate` and `merge-target` exactly as 008 §11
    defines it. 008's Steps 7-8 then accept it on `merge-stamp`, carry it on the hook channel (S1), and send it.
  - Either way, Step 7's provenance test uses `backlog_record_merge` over MCP when 008 isn't in yet, and the
    `merge-stamp` route when it is.

## 11. Open questions (defaults are the recommendations)

- **Q1. How MCP calls name their clone.**
  - The optional `X-Taskmaster-Clone` header with a per-person default *(default)*.
  - A `clone` argument on the affected tools: agents would have to carry it, and it widens three tool contracts.
  - Per-person rows only, with no clone: simplest, but one person's two machines collide (G5 for everyone).
- **Q2. Members' `branch`/`worktree` writes are diverted only** *(default)*, versus also setting the shared field
  when it is empty. Writing both would put one person's machine-local values back in front of the whole team.
- **Q3. Per-clone merge records kept** as provenance *(default; the task title names them)*, versus dropping D7 and
  relying on the event actor alone. D7 is one upsert and one display line.
- **Q4. Lazy table, no schema bump** *(default; the `checkouts` precedent)*, versus native schema 6 through
  `upgrade_native`. A bump would make every solo store take an upgrade for a server-only table.
- **Q5. Worktree paths and merge records stay out of the state export** *(default; machine-local, re-recorded on
  use)*. **`branch` rows are exported** (D10, the orchestrator's override after review I5), because losing them
  would ungate those branches.
- **Q6. The operator writes shared fields** *(default; the host operator is the "solo" owner of the team store)*,
  versus diverting the operator too.
- **Q7. `taskmaster git` in a teammate clone answers "not managed" and runs nothing** *(default)*, versus passing
  through to plain Git. A passthrough would be a second front door to Git with its own surprises.

## 12. Risks

- **R1. Envelope change.** `clone` is a new optional envelope key. `test_native_contracts.py` runs in Step 3. Receipt
  replay is unaffected, because the adapter mints a fresh `request_id` for every call.
- **R2. Two lookups.** `tasks_for_branch` (solo) and `branch_candidates` (server) could drift. `branch_candidates`
  calls `tasks_for_branch` for the shared part, so the shared rule exists once. 007's golden fixtures pin the solo
  answers.
- **R3. Header availability** in Claude Code's HTTP MCP config (`--header` on `claude mcp add`). The header is
  optional, and without it everything degrades to per person (G5).
- **R4. Rebase friction with 008** in `hook_reads.py` and the two hooks. §10 assigns functions; the second lane
  rebases textually.

## 13. Rollback

Revert the step commits. The table is created lazily, and an unreverted store keeps an unused `clone_fields` table:
harmless, because no reader of a reverted build knows it. Shared fields were never changed by members after D4, so
reverting shows the pre-011 values.

## 14. Implementation plan (TDD)

For each step: write the named tests first and watch them fail, then implement, run the proving check, and commit.
- Tests run from the worktree with `C:\Users\gruku\Files\Claude\taskmaster\.venv\Scripts\python -m pytest …`, in the
  foreground, at `-n 2` at most, with `--basetemp` under `D:\scratchbook\taskmaster\ts011\`. Scratch also goes there.
- Don't wait on a background pytest (memory `teammate-background-notice-stall`).

**Step 1. Clone identity (client).**
Files: `remote/clone.py`, `remote/cli.py`, `taskmaster_cli.py`. Tests first, in `tests/test_remote_clone.py`:
- the first call creates `c-<16hex>` in `<root>/.git/taskmaster-clone.json`, owner-only, and later calls and a linked
  worktree of the same clone return the same id;
- two processes racing on first use agree (exclusive publish);
- a submodule (`.git` is a file), a linked or foreign-owned file, or an unreadable one gives None;
- `create=False` never writes;
- `taskmaster remote clone-id` prints it.

Proving check: the file, `test_remote_locate_keys.py`, `test_server_marker.py`.

**Step 2. Hooks send `clone`.**
Files: `hooks/merge_gate_decide.py`, `hooks/merge_recorder_stamp.py`. If 008 isn't merged yet, `serve/read_api.py`
also gains the `clone` parameter on `merge-gate`/`merge-target`, per 008 §11: validated, passed as `clone=`, ignored by
the views until Step 4.

Tests first, in `tests/test_hooks_server_mode.py` (extended):
- with a clone id present, the server's access log or a spy on `hook_reads` sees `clone=c-…` on each hook request,
  and the `merge-stamp` body carries it when 008 is in;
- with no `.git` directory, no `clone` is sent;
- a malformed `clone` parameter is 400.

Proving check: the file, `test_serve_read_api.py`, and `test_serve_write_api.py` when present.

**Step 3. The table and the envelope.**
Files: `native/clone_fields.py`, `native/contracts.py`, `coordinator/adapter.py`, `native/commands.py` (outcome key
only). Tests first, in `tests/test_clone_fields.py` (direct SQLite on a native fixture store):
- `ensure` is idempotent;
- `put`, `own` (clone row, then default row, then nothing) and `rows_for`;
- `branch_matches` uses the partial index (`EXPLAIN QUERY PLAN`);
- the bounds: 4 KiB, 64 clones, 1000 rows;
- reads on a store without the table are empty and create nothing;
- `contracts.validate` accepts a well-formed `clone` and refuses a malformed one;
- an envelope without `clone` is unchanged byte for byte (solo and host callers);
- the adapter takes the clone from `bs._bound_session()`: a stub `RemoteSession` with a clone and a stub hook
  `ChannelSession` with one both put it in the envelope, and a viewer session puts none.

Proving check: the file, `test_native_contracts.py`, `test_native_receipts*.py` (whatever exists for receipts;
`ls tests/test_native_*receipt*` first).

**Step 4. Clone-aware lookup.**
Files: `native_routing/hook_reads.py`. Tests first, in `tests/test_hook_views.py` (extended):
- solo shape and answers unchanged: 007's golden fixtures stay green;
- server:
  - two people each map `fix/login` to their own task, and each gets their own with `mine: true`;
  - a third person gets `ambiguous`;
  - a branch only ann's row maps, merged by bob, gives ann's task with `mine: false` (not ungated);
  - the default row stands in for a clone with no row;
  - shared-field-only tasks are still found;
- `merge_gate_view`, `merge_target_view` and (with 008) `merge_stamp_plan` honour it.

Proving check: the file, `test_serve_read_api.py`, `test_hooks_server_mode.py`.

**Step 5. Diverting writes.**
Files: `native/commands.py`, `serve/sessions.py`, `backlog_server.py`, `native_routing/tasks.py`. Tests first, in
`tests/test_serve_clone_fields.py` (in-process `ServeDaemon`, member keys, the `X-Taskmaster-Clone` header set on the
MCP client):
- a member's `backlog_update_task(field="branch")` leaves the task document and its events unchanged, writes the
  clone row, and replies with the clone sentence;
- clearing deletes the row only;
- without the header, the default row is written;
- a malformed header is ignored and logged;
- the batch-line form and the viewer update divert the same way;
- a member's bundle pick writes rows for every member task and leaves the shared fields;
- the operator's writes and every solo write are byte-identical to today: documents, events and replies, compared on
  a fixture;
- the 65th clone id goes to the default row, with the reply saying so;
- **a fully diverted update** (only `branch`) leaves the task's revision, `last_referenced` and the event log exactly
  as they were, while an update of `branch` plus `priority` writes the priority, with its touch and one event, and
  diverts the branch.

Proving check: the file, `test_session_scope.py`, `test_claim_ownership.py`, `test_serve_sessions*.py`,
`test_native_batch*.py`, `test_serve_viewer.py`.

**Step 6. The overlay.**
Files: `native_routing/tasks.py`, `native_routing/context.py`. Tests first, in `tests/test_serve_clone_fields.py`:
- after ann records `.worktrees/a` and bob picks the same task, bob's pick instruction doesn't mention `.worktrees/a`,
  and ann's re-pick says "Already recorded at `.worktrees/a`";
- `backlog_get_task` shows each caller their own branch, and the "Branches by person" line lists both people's branch
  names and no worktree path;
- `backlog_context` matches;
- solo replies are unchanged (existing `get_task`, `pick_task` and context tests).

Proving check: the file, `test_pick_task*.py`, `test_native_context*.py`, `test_get_task*.py` (list them with `ls`
first), `test_server_instructions.py`.

**Step 7. Per-clone merge records.**
Files: `native/workflow.py`. Tests first, in `tests/test_serve_clone_fields.py`:
- **S1, through the route:** a hook stamp sent with `clone=c-…` (008's `merge-stamp`, or `backlog_record_merge` over
  MCP with the header when 008 isn't in yet) writes `merge:<rung>` under that clone, not the default row. The shared
  `merge_status` is exactly what it is without 011;
- with the bound reached, the record is skipped and logged, and the shared stamp still lands;
- with 008 in, a stamp the CAS refuses writes no record;
- `backlog_get_task` shows "Merges from your clone";
- the operator and solo write no row.

Proving check: the file, `test_record_merge.py`, `test_merge_stamp_contract.py`.

**Step 8. Host Git inventory, ratchet and the teammate CLI.**
Files: `coordinator/git_cli.py`, `tests/test_serve_no_member_git.py`.
- **Ratchet:**
  - the member tool list comes from 005's gate, not a copy;
  - fixture tasks have `worktree` and `sub_repo` set so evidence paths would trigger if reachable;
  - every allowlisted call runs once with plausible arguments, from a table that a meta-test checks covers every
    member tool;
  - the recorded `git` argv outside `ALLOWED_HOST_GIT` must be empty.
- **CLI:**
  - in a clone with a marker, each subcommand prints `not-managed` with the D8 text;
  - `status` exits 0 and the others exit 2;
  - no coordinator client is constructed (patched to fail);
  - solo behaviour is unchanged (`test_native_git_managed.py` minus its known pre-existing failure, rulings).

Proving check: the two test files, `test_native_git_managed.py`, `test_server_fence*.py`.

**Step 9. Branch rows in the state export (D10).**
Files: `serve/state_export.py`, `serve/cli.py` (the report fields), `native/workflow.py` (`clone.restore`),
`coordinator/allowlist.py`, `docs/specs/2026-11-01-team-server-013-state-export-design.md` §8.3.

Tests first, in `tests/test_serve_clone_fields.py` and the 013 export tests (`tests/test_serve_state_export*.py`; `ls`
first):
- an export with member branch rows writes `clone-fields/branches.json`, sorted, with exactly those rows; worktree and
  `merge:*` rows are absent; a malformed row is in `dropped`;
- **the round trip** (the review's ask):
  - ann maps `feature/a` to task T in her clone only;
  - T's review-gate is stale;
  - export, then rebuild a host from the export per 013 §8.1 (steps 5-7);
  - `merge_gate_view` for ann and `feature/a` still finds T, and the hook blocks;
  - a control run without `restore_branches` shows `task: None`, which proves the test can fail;
- `clone.restore` from a member key over MCP (inside `backlog_batch_update(commands=)`) and as a raw execute is
  refused with the operator text, and from the host-local owner it writes;
- `restore-documents` reports `branches_restored`;
- an export commit from before 011 (no `clone-fields/`) restores with zero branches and no error.

Proving check: the files above, every `tests/test_serve_*export*.py`, `test_serve_allowlist*.py`.

**Step 10. Regression set.**
In slices, foreground: `test_hook_views.py`, `test_hooks_server_mode.py`, `test_merge_gate_*.py`,
`test_merge_recorder_hook.py`, `test_merge_stamp_contract.py`, `test_record_merge.py`, `test_native_routing_hooks.py`,
every `tests/test_serve_*.py`, `test_session_scope.py`, `test_claim_ownership.py`, `test_native_contracts.py`,
`test_native_git_managed.py`, `test_server_fence*.py`, `test_remote_*.py`, `test_packaging.py`, the 013 export tests,
plus the four new files. Report each slice's counts and every failure with its output. The full suite is not run on this machine
(rulings); the orchestrator runs it once on feat/team-collab before release.
