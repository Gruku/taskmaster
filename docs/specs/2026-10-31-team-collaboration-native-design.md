<!-- User intent: rework the July team-collaboration design for the database-native store, and
     add shared planning so a teammate who syncs can see which tasks each person will take next. -->

# Taskmaster team collaboration on the native store (rev 3.3)

**Status:** passed spec review 2026-10-31 (rev 3.2, after one scoped re-review). Rev 3 failed review (1 Critical, 14 Important;
[review](reviews/2026-10-31-team-native-spec-review.md)). This revision answers every Critical
and Important finding; §17 maps each one to where it is handled.
**Supersedes:** rev 2, `claude-tools/docs/superpowers/specs/2026-07-11-taskmaster-team-collaboration-design.md`.
Rev 2's requirements stand unless this document changes them; its mechanics targeted the
file-based v4 store and are replaced here.
**Builds on:** [database-native design](2026-09-09-database-native-design.md) and the shipped
behaviour in [native-store.md](../native-store.md).
**Tracking:** `team-sync-012` (claude-tools backlog); epics `team-identity`, `team-sync`,
`team-ids`, `team-viewer`, plus new `team-plan` (§14).

## 1. Goal and why a revision

Let a small team that all run Taskmaster share one backlog through the project's Git remote,
and see who has claimed what and **who plans to take what next**, without a hosted server and
without giving up local-first, offline work.

Rev 2 assumed files were the authority: a dirty-scoped `save_v4`, `_load()` boundaries, a sync
worker in each MCP server, three-way merges written straight into working files, and
directory-scan ID allocation. Since 7.0 the native store makes SQLite the authority:

- one **coordinator** per repository admits every write; MCP servers, the viewer and CLIs are clients;
- files under `.taskmaster/` are an **export** of the database, and hand edits enter only at `backlog_sync`;
- IDs come from `id_counters`/`id_reservations`, and entities have a stable integer `entity_key`;
- the import path already merges **per entity** (`coordinator/sync_prepare.py:142-190`, via
  `projection_parse.owned_rows`), and `sync_merge.protect_local` keeps claim and tracker fields
  machine-local;
- per-session **claims** (lease, `locked_by = <host>-<pid>-<nonce>`) and managed Git exist.

The sync engine therefore moves into the coordinator, exchanges **entity state** rather than
files, and applies a teammate's changes as native commands.

## 2. Rulings

| ID | Ruling | Source |
|---|---|---|
| R1 | Team sync requires the native store; legacy stores stay solo | User, 2026-10-31 |
| R2 | `assignee` is shared; the session lease stays machine-local | User, 2026-10-31 |
| R3 | Transport is a dedicated orphan branch (`taskmaster-state`); `.taskmaster/` is untracked on code branches | User, 2026-10-31 (rev 2 decision kept) |
| R4 | Plans are a bare ordered list per person; two people may plan the same task (flagged, not refused); old entries fade in the UI and are never removed automatically while open | User, 2026-10-31 |
| R5 | Sequential human IDs; an offline collision renames the unpushed side, and a **rename guard** stops old-ID writes from landing on the teammate's entity. Person-tagged IDs are the documented fallback (§16) | User, 2026-10-31 |
| R6 | Teammates all run Taskmaster (Claude Code + coordinator); no viewer-only participants in the first release | User, 2026-10-31 |
| R7 | The sync engine runs inside the coordinator; a teammate's changes are applied as native commands | This spec |
| R8 | The merge and diff unit is the entity `(kind, public_id)`, not the file | This spec (review I2) |
| R9 | What's pushed is a **canonical** rendering of database state; unchanged entities reuse their base blobs | This spec (review I3) |
| R10 | An entity leaves the shared tree only through a recorded tombstone or move; render failures abort the push | This spec (review I5) |

## 3. Shared tree and machine-local state

Every exported path is classified (from `projection_parse.ENTITY_FILE_SPECS`, `CONFIG_FILES`
and the derived files):

| Path | Kind | Class |
|---|---|---|
| `tasks/*.md`, `tasks/archive/*.md` | task | shared |
| `epics/*.md`, `phases/*.md`, `areas/*.md` | epic, phase, area | shared |
| `bugs/…`, `issues/…` (and `archive/`) | bug, issue | shared |
| `handovers/*.md`, `handovers/_archive/*/*.md`, `handovers/archive/*.md` | handover | shared |
| `decisions/*.md`, `ideas/IDEA-*.md`, `notes/NOTE-*.md`, `notes/_archive/NOTE-*.md` | decision, idea, note | shared |
| `backlog.yaml` | backlog meta plus slim epic and phase rows | shared, minus the derived sections (`DERIVED_BACKLOG_FIELDS`) |
| `project.yaml` | project | shared |
| `users/<handle>/profile.yaml` | **profile** (new kind) | shared, owner-written |
| `users/<handle>/plan.yaml` | **plan** (new kind) | shared, owner-written |
| `aliases/<kind>-<old-id>.yaml` | **alias** (new kind) | shared |
| `trackers/*.md`, `integrations/trackers/*.md`, `linear.yaml` | tracker, config | **local** in the first release (§6.9) |
| `ideas/IDEAS.md`, `PROGRESS.md` | derived | local; each clone re-derives them |
| `local/**` | runtime | local |
| `harvests/`, `lessons/` | not native kinds | not synced; reported by `init-team` if present |

Fields excluded from shared state, whatever the file: claim lease fields (`claims.CLAIM_FIELDS`),
tracker push receipts (`TRACKER_LOCAL_FIELDS`), and the machine hints `task.worktree` and
`task.branch` (review M4). Command receipts, domain events and projection jobs stay per clone.

Rev 2's `users/<handle>/sessions/` is dropped. Recent activity comes from shared `updated_by`/
`updated_at` stamps and handover `author` (§4).

## 4. Identity and attribution

- **Clone identity.** `init-team` writes `local/identity.yaml` with `handle`, `clone_id`
  (random, stable for this clone) and `clone_label` (host name, editable), plus
  `confirmed: false` until a skill confirms it.
- **Handle bootstrap** is mechanical in the coordinator and interactive only in skills (rev 2
  §4): it slugs `git user.name`, suffixes on a clash with a different email among known profiles,
  and refuses attribution-bearing writes for degenerate names. The profile is a DB entity
  (kind `profile`) created by a command, never a file the coordinator writes directly.
- **Handle clash between offline joiners** (review I11): two clones that both minted `users/jd`
  before syncing are detected at import because the profiles' email sets don't overlap. The
  unpushed side's handle is renamed (`jd2`) under the same rename guard as IDs (§7), its
  `identity.yaml` is updated, and the skill confirms the result with the user.
- **Actor on every command** (rev 3.3, from the team-identity-002 spec review). The coordinator
  derives the actor `{handle, agent, clone_id}` and never trusts it from the client.
  - **Who sets what.** After `admit()` and before `commands.execute`, one actor-resolver seam fills
    `handle` and `clone_id`, overwriting anything the client sent. Today the seam reads them from
    the identity gate (Git identity); in server mode, from the authenticated token. `clone_id` is an
    opaque *origin* id the resolver supplies; in server mode it names the client, not a database
    copy. The client sends only an advisory `agent`.
  - **Agent value.** The MCP server reads FastMCP `clientInfo.name` once per process and maps it to
    the reserved slugs: `claude-code`→`claude`, `codex-*`→`codex`, anything else→"". The viewer
    and CLIs send "".
  - **Retries.** The actor is stamped *after* `contracts.validate`, so it stays out of the
    idempotency fingerprint (`contracts.py:113`). A retry after a handle change therefore still
    matches its receipt. The receipt records the actor inside `outcome_json`, so `receipts` needs no
    new column. Contracts allow the advisory `agent` key
    explicitly.
  - **Internal envelopes.** The identity gate's `profile.create`, the Linear worker, managed Git
    and merge-stamp replay carry the local clone's actor with agent `taskmaster`. `team.import`
    events carry the importing clone's actor. A teammate's own stamps (`updated_by`, …) still
    survive import unchanged; see shared fields below.
  - **Storage.** `domain_events` gains `actor` (JSON) and `entity_key` columns in an **opt-in schema
    5** step (§12). Solo stores at schema 3 don't store it in `domain_events`; their receipts' `outcome_json` does carry it (plan D6), which 7.4.x replays unchanged. The event
    writer branches on whether the columns exist, and re-checks after the identity gate upgrades
    the store on the same connection.
  - **Backfill.** The schema 5 upgrade backfills `entity_key` for existing rows inside the
    step's single upgrade transaction (`upgrade.py:26-35`). A store is therefore never marked
    schema 5 with rows left unfilled. The backfill goes through `entity_core(kind, public_id)`, which is 1:1 by its UNIQUE constraint.
    Rows whose id is a path or `__backlog__` stay NULL. An index on `entity_key` serves the rename
    primitive (§7). The backfill is timed on a CodeMaestro-sized events table before release.
- **Shared fields** (operational columns): `assignee`, `assignee_source`, `claimed_on` on tasks
  and bugs (§5); `author` on handovers, notes, ideas and decisions as `<handle>` or
  `<handle>/<agent>`; `updated_by` and `updated_at` on every shared entity. A teammate's change
  keeps *their* stamps through import; the import never re-stamps.
- `updated_by`/`updated_at` merge as "newest wins" and never raise a sync note. Clock skew can
  misorder these two cosmetic fields only.
- Legacy `user|claude` author enums migrate to the enabling user's handle at `init-team`.
- `backlog_team_merge_handles(from, into)` is one command rewriting `assignee`, `author`,
  `updated_by` and plan ownership, and adding a handle alias.

## 5. Planning, assignment and claims

Three levels, from soft to firm:

| Level | Stored as | Steering for others | Push |
|---|---|---|---|
| **Planned** | position in the owner's `plan` | Soft: `backlog_pick_task` warns "planned next by gruku (#2)" and proceeds; `backlog_next_available` sorts it after unplanned work | Immediate |
| **Assigned** | `assignee: <handle>`, `assignee_source: assigned` | Firm: pick refuses without `force` | Immediate |
| **Claimed** | `assignee`, `assignee_source` (`claim` or `assigned`), `claimed_on: <clone_id>`, `status: in-progress`, plus the local lease | Firm, including **for the same person on another clone** (review I7) | Immediate, confirmed (below) |

### Plan queue

- A `plan` entity per handle (`public_id = handle`), exported as `users/<handle>/plan.yaml`:
  an ordered list of `{id, added}`. `added` only drives fading; users see a bare list (R4).
  Entries reference tasks by `entity_key` internally and render the public ID, so a rename
  follows automatically.
- Tool: `backlog_plan(action="show"|"add"|"remove"|"move", task_ids="", position=None, handle="")`.
  `show` reads anyone's plan (default: all, as one compact block); writes act on the caller's own
  plan only. Skills phrase it as "plan X, Y next".
- Owner-only writes keep plans conflict-free across people. The same person's two clones
  list-merge: theirs' order wins, local-only additions append, removals on either side apply.
- **Picking a task removes it from the picker's own plan.**
- **Finished work drops off without a cross-person write:** readers hide entries whose task is
  done or archived, and the owner's coordinator removes them from its own plan after the import
  that shows it.
- **Open entries are never removed automatically (R4).** Entries older than
  `team.plan.fade_days` (default 7) render faded. A task assigned to or claimed by someone else
  stays in the plan, flagged "taken by jd", until the owner removes it.
- **Contested:** a task in two or more plans shows "also planned by …" everywhere. It is never
  refused; the first confirmed claim wins.

### Assignee lifecycle (review I8)

| Event | `assignee` | `assignee_source` | `claimed_on` |
|---|---|---|---|
| Pick an unassigned task | me | `claim` | this clone |
| Pick a task assigned to me | me | `assigned` (kept) | this clone |
| Release, or status back to `todo` | cleared if `claim`; kept if `assigned` | cleared with assignee | cleared |
| `done` / `archived` | kept (history) | kept | cleared |
| Explicit assign or reassign | the target | `assigned` | cleared |
| Pick with `force` over someone else | me | `claim` | this clone; the old holder gets a note |

**General import rule:** whenever an import changes `assignee` away from me, or `claimed_on` away
from this clone, on a task this clone holds a lease for, the lease is released in the same
command and a sync note says who has it now.

### Confirmed claims (review I9)

`backlog_pick_task` commits the claim locally, triggers an immediate push and waits up to
`team.sync.claim_wait_s` (default 5 s) for it. The result says `claim: confirmed` or
`claim: provisional (<reason: offline | timeout | push rejected, retrying>)`.

- The `pick-task` skill proceeds on a provisional claim, because offline work is supported, and
  tells the user the claim may lose.
- If a later import loses the race, `backlog_context`, `backlog_claim(action="status")` and the
  next write on the task return a `claim` blocker naming the winner, until the session
  acknowledges it. The loser's work isn't discarded; the skill offers to hand it over or stop.
- No expiry across people. In-progress tasks with no `updated_at` change for 5 days are flagged
  stale for humans.

### What a teammate sees

`start-session` prints, after its dashboard: teammates' active claims, their plan heads (first
three entries), contested items involving you, unconfirmed claims of yours, and unread sync
notes. The viewer's Team screen (§10) shows the full picture. `backlog_context` reports a
teammate's claim on the focus task as a `claim` blocker and a teammate's plan as a non-blocking notice.

## 6. Sync engine

Lives in the coordinator (R7). Off unless `team.sync.mode` is `auto` or `manual` (§11).

### 6.1 Shared state, base and canonical form

- **Shared state of an entity** = its fields and body after removing every local-only field
  (§3), with references expressed as public IDs. It is computed from DB rows, never from files.
- **Canonical form** (R9): the shared state serialized as JSON with sorted keys, lists in stored
  order, bodies LF-normalized (`projection_parse.normal_body`). Its hash identifies an entity's
  content independently of rendering, line endings or the clone's `entity_key`s.
- **Base**, per clone, in a new table `team_base(kind, public_id, path, blob_sha, state_hash,
  state_json)`: the shared state of each entity as of the last state-branch commit this clone
  merged, with the path and blob that represent it there. `sync_state` holds `team.base_commit`.
  The base commit is pinned under the local ref `refs/taskmaster/team-base`, so `git gc` and a
  remote force-push can't remove it (review I4).
- **Canonical renderer** for the shared tree (R9): LF line endings; `backlog.yaml` epics and
  phases ordered by their `order` field, then `public_id` (never `entity_key`); deterministic key
  order; archive paths derived from the entity's own dates, never the clock. It differs from
  the working-file exporter only in these inputs, and is versioned as `team_render`.

### 6.2 Outbound: what changed locally

1. **Candidates:** entities whose `last_seq` is above `team.pushed_seq`. That is cheap SQL, with no
   full scan. The outbound pass reads one snapshot at sequence S; `team.pushed_seq` only ever
   advances to that **S**, never to the high-water mark at push time, so a write committed while
   the push is in flight remains a candidate for the next cycle (review N2).
2. For each candidate, compute the canonical shared state. If its hash equals the base row's,
   skip it, even if a clone with a newer `team_render` would render other bytes. Byte
   differences alone never cause a push, so clones can't flap (review I3).
3. Render only the changed entities' files. Build the tree from the base commit in a temporary
   `GIT_INDEX_FILE`: `read-tree`, then `update-index` for changed blobs, then `write-tree`.
   Unchanged entities keep their base blobs (review M1).
4. **Removals only by record (R10):** a path leaves the tree only when the entity has a DB
   tombstone, or when it moved (archive), in which case the old path is removed and the new one
   added. An entity that fails to render, or that the path guard refuses, **aborts the cycle**
   with a pending notice. The tree is never "whatever rendered" (review I5).
5. `commit-tree` with the remote tip as parent; the message lists the changed IDs.

### 6.3 Inbound: what changed remotely

1. `git diff-tree --name-status base..theirs` lists the changed paths. Each maps to the entity
   that owns it (`projection_parse.classify`; `backlog.yaml` rows via `owned_rows`). Only those
   entities are parsed, so a move shows up as one entity with a new path, not as a delete
   plus an add (review I2).
2. Per changed entity: base = `team_base.state_json`; ours = shared state from DB rows; theirs =
   parsed and canonicalized. The team resolver (§6.5) produces the merged state.
3. Apply in batches of at most `sync.MAX_ROWS` (100) entities per `team.import` command
   (review M3). Each command carries expected revisions and, in one transaction: applies
   renames first (§7), then the merged states, binds dangling refs, writes notes and
   conflicts, enqueues Linear work (§6.9), and updates those entities' `team_base` rows to
   theirs. Because base is per entity, a crash between batches leaves applied entities based
   correctly and unapplied ones still pending.
4. If an expected revision moved because a local write raced the batch, that batch is recomputed
   (up to 3 times), then merged inside the writer slot so it can't starve (review M3).
5. When every batch has landed, `team.base_commit` and the base ref advance to theirs.
6. The normal projection then exports the result to working files.

**Unparseable theirs entities (review N1).** A theirs file that fails to parse or validate
quarantines that one entity instead of stalling the import. A `team_quarantine` row and a note
are written. The entity's `team_base` row takes theirs' path and blob but keeps the last good
state, marked quarantined. Outbound never rewrites that path (theirs' blob is carried
unchanged), and local edits to the entity are held, not pushed, until it is resolved. The base
commit still advances, so the clone keeps syncing everything else. A later valid version from
the branch clears the quarantine as a normal change; `backlog_team_sync(take_local=<id>)`
resolves it in favour of the local state.

### 6.4 A sync cycle and the push protocol

1. **Fetch** `refs/heads/<state_branch>`. Network calls hold no DB transaction and no repository
   lock, and go through `run_bounded` with the environment in §6.8.
2. **Adopt our own lost push first (review I4).** If `team.pending_commit` is set and theirs
   equals it or descends from it (`merge-base --is-ancestor`), apply the pending entity states to
   `team_base`, advance the base to the pending commit, and clear the pending marker. Only
   then compare against theirs. Otherwise our own pushed values would come back as "theirs".
3. If theirs differs from the base: inbound (§6.3).
4. **Outbound runs only when `team.base_commit` equals the fetched tip** (inbound complete), and
   builds its tree from that commit (review N1). If inbound didn't finish (stopping coordinator,
   retries exhausted), the cycle ends without pushing and the next one resumes the import.
   Then outbound (§6.2); if nothing changed, stop.
5. Record `team.pending_commit` and the pending entity states, **then** push fast-forward only
   (`git push <remote> <commit>:refs/heads/<state_branch>`).
6. On success: adopt pending as base (as in step 2), set `team.pushed_seq`. On rejection
   (non-fast-forward): go back to step 1. On a lost reply: the next cycle's step 2 resolves it.

### 6.5 Team resolver

`sync_merge.merge` keeps ours on overlap (`taskmaster_v3.py:3936-3953`); the team resolver is
its own module (`native/team_merge.py`) built on the same field splitting:

| Case | Resolution |
|---|---|
| Disjoint fields | Both land |
| Same scalar field | Theirs wins; the losing value goes to a sync note |
| `status` | Theirs wins the value; gate records and merge records union |
| List fields | Element-wise three-way merge when the list has identity (IDs, paths); otherwise theirs wins, with a note |
| Body edited on both sides | Three-way text merge (`git merge-file -p` on temp files). A clean merge lands with no note; a conflicted one takes theirs, and the full losing body goes to `team_conflicts` and is linked from the note |
| Archive vs edit | Field-wise: the edit's fields land **and** the entity stays archived, with a note offering to restore it |
| Tombstone vs edit | The edit wins; the tombstone is lifted, with a note |
| `updated_by`/`updated_at` | Newest wins, no note |
| `assignee`/`claimed_on` | Theirs wins (first confirmed claim); the general import rule (§5) releases our lease |
| Plan (same owner, two clones) | List-merge (§5) |
| New entity on one side | Inserted |
| Same new `(kind, id)` on both sides | §7 |

"Theirs wins" is "first to the branch wins": after step 2 of the cycle, theirs never contains
our own unadopted push.

### 6.6 Triggers and coordinator lifetime

- **Immediate** after a command that changes `assignee`, `claimed_on`, claim status or a plan.
- **Debounced** (`team.sync.debounce_s`, default 30) after any other shared change.
- **Inbound fetch** at session start; before `backlog_pick_task` and `backlog_next_available`
  (bounded to 3 s; on timeout they answer from local state and say so); on the viewer's Team
  screen every 60 s; and on `backlog_team_sync()`.
- **Lifetime (review M5):** unpushed shared changes count as pending work for up to
  `team.sync.linger_s` (default 120 s) of retries after the last write. After that, an unreachable remote no
  longer blocks idle exit, and the next coordinator start recomputes and retries. A fetch that
  imports nothing does not reset the idle timer, so a viewer poll alone can't keep the
  coordinator alive. Unpushed changes show in `backlog_store_status` and `backlog_team_status`.

### 6.7 Enabling and joining (reviews I6, I13)

- **Precondition for both:** `.taskmaster/` is untracked in every local branch head
  (`git ls-tree` per head). If any tracks it, `init-team` refuses and lists the branches.
  Untracking an existing shared history is **out of scope for the first release** (§16). The
  user's tracked projects are local-only, so this costs nothing today, and it avoids the
  teammate-pull deletion and old-branch collisions the review found.
- **`init-team` (first person):** creates `profile` and `plan` entities, renders the full
  canonical tree, creates the orphan branch, pushes it after confirmation, writes `team_base`,
  and turns sync on.
- **`init-team join` (teammate):** fetches the branch.
  - **No local backlog:** writes the tree into `.taskmaster/`, creates a born-native store
    through the adoption path, seeds `id_counters` from the highest IDs present, and fills
    `team_base` from the fetched tree.
  - **Existing local backlog:** the skill shows a dry-run summary and asks. **Replace** (the
    default) backs up `store.db` and the files to `local/backups/` and joins as above. **Merge**
    runs one inbound cycle with an empty base, under a join-specific rule: a `(kind, id)` present
    on both sides is the **same entity**, merged field-wise with theirs winning and notes. It is
    not a collision, because there is no base to say it's new. Collision renames apply only once
    a base exists.

### 6.8 Unattended Git (review I14)

Every coordinator Git call runs non-interactively and says so in its errors:

- `commit-tree --no-gpg-sign`. State-branch commits are machine commits; signing them is
  out of scope. A project that requires signed refs can't use team sync in the first release,
  and `init-team` says so.
- `git push --no-verify`. The state branch carries no code, and pre-push test hooks would run
  every 30 s. `init-team` states this in its confirmation.
- `GIT_TERMINAL_PROMPT=0`, `GCM_INTERACTIVE=never`, and SSH `BatchMode=yes` (via
  `-c core.sshCommand` when `GIT_SSH_COMMAND` isn't set). Unlike `coordinator/git.py:64-70`'s
  scrub, the auth variables `GIT_SSH_COMMAND`, `GIT_ASKPASS`, `SSH_AUTH_SOCK` and `GCM_*` pass
  through. A credential prompt the coordinator can't answer surfaces as
  `offline: authentication required — run 'git fetch <remote>' once in a terminal`.

### 6.9 Interaction with existing native features

- **Managed Git:** with `.taskmaster/` untracked, checkouts never move projection files and drift
  detection has nothing to see. `git_cli commit` refuses in team mode with "the backlog is
  shared on <state_branch>; run backlog_team_sync". The merge recorder is unchanged.
- **Linked worktrees:** unchanged; the state branch never appears in a worktree.
- **Linear (review I12):** trackers and `linear.yaml` stay local, and `project.yaml` names one
  `team.linear_clone`. Only that clone pushes to Linear; any other clone with trackers configured
  refuses to push and says which clone owns Linear. On the owning clone, `team.import` enqueues
  an upsert for every imported change to a linked task, so teammates' edits reach Linear.
  `linear_queue` gains `target_key` and is read by it (rename-safe).
- **Change feeds:** imports are local commits, so `backlog_changes_since` and viewer ETags see
  them, with `actor` set to the remote author.
- **Guard hooks:** one argv allow-rule for pushes to `refs/heads/<state_branch>` only (rev 2 §6).

## 7. IDs: collisions, rename guard, aliases

- **Only unpushed IDs can collide.** An ID already on the branch is in every clone's base before
  that clone can push, and pushes are fast-forward only.
- **Detection** (in `team.import`, only once a base exists): an entity in theirs whose
  `(kind, id)` matches a local entity that has no `team_base` row.
- **Numbered kinds** (tasks `<prefix>-NNN`, `B-`, `ISS-`, `IDEA-`, `DEC-`, `NOTE-`): the
  local entity is renamed to the next free number above both sides' high water, **before**
  theirs is inserted, in the same transaction.
- **Named kinds** (epic, phase and area IDs chosen by the caller, `commands.py:41`): treated as
  the **same entity**, merged field-wise with theirs winning and notes. Two people who both
  create epic `auth` almost always mean the same epic, and a rename would be worse. Handover
  IDs (date + slug) take a `-2` suffix rename. Handles: §4.

### Rename primitive (review I1)

One function, `native/rename.py`, used by collisions and handle merges. In order:

1. `entity_core.public_id`, after relaxing the `replace()` identity check (`commands.py` ~203)
   for this primitive only.
2. Referrers in all four relation tables: `target_id` **and** `value_json`, because
   `native/queries.py:121` renders from `value_json`.
3. Operational JSON columns that hold IDs (`task.epic`, `task.phase`, bundle membership and the
   like), enumerated from `schema.owner()`.
4. `[[old-id]]` refs in `entity_documents` and `external_documents`; `document_search` rows.
5. `projection_jobs`, projection state and file identity: the old file is retired and the new
   path exported, so the working-file exporter never sees two entities on one path.
6. `id_reservations` (the old ID stays reserved, now by theirs) and `id_counters`.
7. `linear_queue` (by `target_key`) and `domain_events` (rows with the old ID and the entity's
   `entity_key` are rewritten).
8. Plan entries need nothing, because they're keyed by `entity_key`.
9. An `alias` entity `{kind, old_id, new_id, reason, displaced: true}`.

A schema test fails if any table or JSON column that can hold a public ID is missing from
the rename inventory, so new tables can't silently escape it.

### Rename guard (review C1)

After a collision, the old ID belongs to the teammate's entity, so a plain alias would never
fire. On the renaming clone only (no other clone ever saw our entity under the old ID):

- **Every write that names a displaced ID is refused**, whether the ID is the target or appears
  in any ID-valued argument or field: `depends_on`, typed links, `related_tasks`, handover
  `task_ids`, `epic`, plan entries, `backlog_link`, and `[[auth-007]]` in new or edited prose.
  The check runs in command validation over the same inventory the rename primitive uses
  (§7), so it can't drift from it. The refusal reads `renamed on sync: your auth-007 is now
  auth-012; auth-007 is jd's task "<title>". Retry with auth-012, or pass
  confirm_ids="auth-007" to mean jd's task.`
- **Only the user lifts the guard.** Rename notes can't be dismissed through MCP tools, because
  `backlog_team_notes(action="dismiss")` refuses them. They are acknowledged in the viewer's
  notes inbox, or with `taskmaster_cli.py team ack <note>`, which requires an interactive
  terminal (it refuses without a TTY). An agent can therefore never clear the guard to get past
  its own refusal; `confirm_ids` is the per-call way through, and it is visible in the transcript.
- **Reads** by the old ID return theirs, with a leading notice line naming the renamed entity.
- Sessions holding a lease on the renamed entity keep it, because leases follow `entity_key`.
  Their next call gets the notice once.
- Branches, worktrees and commit messages that use the old ID are listed in the note, never
  renamed.

**Plain aliases** (no displacement, e.g. a handle merge) resolve normally: any lookup of an ID
that no entity owns falls back to the alias. Taskmaster-written prose uses `[[id]]` so renames
can rewrite it. `[[old-id]]` in `team.ref_roots` files (default `docs/superpowers`) are rewritten
by a projection-style job with an expected-hash check. A file edited since then gets a note
instead of a rewrite.

## 8. Commands and tools added

| Surface | Purpose |
|---|---|
| `backlog_team_sync(wait=True)` | Run a cycle now; returns pushed/pulled counts, notes, pending reasons |
| `backlog_team_status()` | Remote, base commit, pending commit, last push/pull, unpushed count, offline reason, unread notes |
| `backlog_team_notes(action="list"\|"dismiss", ids="")` | Read and dismiss sync notes; rename notes are refused here and acknowledged only by the user (viewer or interactive CLI, §7) |
| `backlog_plan(...)` | §5 |
| `backlog_team_merge_handles(from_handle, into_handle)` | §4 |
| `backlog_pick_task` / `backlog_next_available` | Plan warnings, assignee and `claimed_on` steering, the inbound pre-fetch, confirmed/provisional claims |
| `init-team` skill | Enable or join (§6.7) |

All writes are native commands with request ids and receipts, like the existing tools.

## 9. New state per clone

| Table | Holds |
|---|---|
| `team_base` | Per-entity base (§6.1) |
| `team_pending` | Entity states of the commit being pushed (§6.4) |
| `team_notes` | Sync notes and their dismissal; exported read-only to `local/sync/notes/` |
| `team_conflicts` | Full losing bodies; exported to `local/sync/conflicts/` |
| `team_quarantine` | Theirs entities that failed to parse, with the held local state (§6.3) |
| `sync_state` keys | `team.base_commit`, `team.pending_commit`, `team.pushed_seq`, last fetch/push times and errors |

## 10. Viewer

Standing rules: no coloured left rails, no hover motion, no shadows; surface stepping; chip rows
through `chipClickNext`. All data comes from board/detail DTOs; there are no new file reads.

1. **Cards:** assignee chip; plan badge (`planned · gruku #2`, several if contested); a
   `provisional` marker on unconfirmed claims; stale claims with a tinted full-perimeter border.
2. **Assignee filter** chip row (each teammate plus "unassigned").
3. **Team screen** (`js/screens/team.js`): one column per person with claimed, in-progress and
   then their plan in order. Faded entries are muted, contested entries show the other handles,
   and taken entries show who has them. Below that: latest handover per person, recent activity
   from `updated_by`, the stale-claim list, and a sync-health panel (unpushed count, offline
   reason, pending commit) with the notes inbox (losing bodies open from `team_conflicts`;
   rename notes show the guard).
4. `[[id]]` renders as an alias-resolving link wherever prose renders.

## 11. `project.yaml`

```yaml
team:
  protocol: 1
  sync:
    mode: off               # off | manual | auto
    remote: origin
    state_branch: taskmaster-state
    debounce_s: 30
    claim_wait_s: 5
    linger_s: 120
  plan:
    fade_days: 7
  linear_clone: ""          # clone_id allowed to push to Linear
  ref_roots:
    - docs/superpowers
```

`project.yaml` is shared, so these settings travel with the branch. Only `mode` can be
overridden per clone (`local/team.yaml`). `team.protocol` changes only through an explicit
`init-team upgrade`, run by a person after confirming that every teammate has updated. A clone
whose build doesn't support the branch's protocol refuses to sync and names the version to
install (review M6).

## 12. Migration and versioning

- Schema steps are opt-in. A store moves past 3 only when team mode first needs the step, so
  solo stores stay at 3 and remain openable by 7.4.x. Schema 4 (team-identity-001) adds the
  profile kind. Schema 5 (team-identity-002) adds `domain_events.actor` and `entity_key`, with
  the `entity_key` backfill and index (§4). Each later step adds its own version.
- Additive native schema migration: operational columns
  `assignee`, `assignee_source`, `claimed_on`, `author`, `updated_by`, `updated_at`; kinds
  `plan`, `profile`, `alias`; `linear_queue.target_key`; tables from §9. Released as a minor
  version. Legacy stores are unaffected, and team mode refuses on them (R1).
- Rev 2's v4 relayout (`team-relayout`, shipped as 5.0.0) is the export and shared-tree format
  and needs no change.

## 13. Testing and acceptance

- **Unit:**
  - the team resolver, one test per row of §6.5;
  - canonical form and renderer: equal states give equal hashes across clones, `entity_key`
    orders and line-ending settings;
  - outbound never removes a path without a tombstone or move, and aborts on a render failure;
  - the rename inventory schema test;
  - the rename primitive, one test per inventory row;
  - the rename guard: a target write is refused; a reference in `depends_on`, a link, a plan
    entry or `[[id]]` prose is refused; `confirm_ids` is allowed; the read notice appears; MCP
    dismissal is refused; CLI ack without a TTY is refused; viewer ack lifts the guard;
  - `pushed_seq`: a write committed during an in-flight push is pushed by the next cycle;
  - an outbound pass with an incomplete inbound pushes nothing; an unparseable theirs entity
    is quarantined, the base still advances, its path is carried unchanged and local edits are held;
  - assignee lifecycle, one test per row;
  - plan rules: owner-only, pick removes, terminal hidden, fading, contested, two-clone list-merge;
  - actor stamping, and stamps preserved through import.
- **Two clones and a bare origin through real coordinators** (the gate):
  - disjoint edits both land;
  - same field → theirs plus a note;
  - a clean body merge gives no note; a conflicted body is preserved;
  - claim race: the first confirmed claim wins, the loser's lease is released, and its next
    write returns the blocker;
  - offline double claim, resolved on reconnect;
  - the same person on two clones can't both claim;
  - duplicate mint: renamed, refs and Linear queue follow, and the guard fires on the old ID;
  - an epic created on both sides becomes one entity;
  - a lost push reply (push lands, reply dropped) doesn't overwrite later local edits;
  - a coordinator killed between import batches recovers without a double apply;
  - a local write racing `team.import` triggers a recompute;
  - different `entity_key` orders and line endings on two clones push nothing back and forth;
  - a render failure pushes nothing;
  - a push only ever touches the state ref;
  - joining with an existing backlog: both Replace and Merge.
- **Planning scenario:** A plans three tasks and syncs; B's start-session lists them; B's pick of
  A's #1 warns and proceeds; both plan one task → contested in both views; B finishes A's planned
  task → hidden for both and removed from A's plan after A's next sync.
- **Unattended Git:** a fake credential helper that would prompt, a signing config, and a
  pre-push hook that fails. The cycle reports offline or skips the hook as specified, and never
  hangs past its bound.
- **Viewer:** route-mocked Playwright specs for the Team screen, chips and the notes inbox.
- **Scale:** outbound with 1 changed entity in a 5k-entity backlog renders 1 entity and runs
  `update-index` once.
- One local `pytest` command; network-free (file-URL bare origin).

## 14. Epic re-cut

Rev 2's `todo` tasks are re-cut after this spec passes review; none has started.

| Epic | Change |
|---|---|
| `team-identity` | 001–002 move into the coordinator, with `clone_id` and the `profile` kind; 003 becomes operational columns plus the assignee lifecycle; 004 adds `author` and stamp preservation; 005 is one command; 006 is unchanged |
| `team-sync` | 001 stays; 002 becomes the canonical renderer plus the temp-index tree build; 003 becomes per-entity `team_base` diffing; 004 becomes `team_merge.py`; 005 becomes `team_notes`/`team_conflicts`; 006–007 become the cycle, push protocol and batched `team.import`; 008 becomes confirmed claims; 009 stays; 010 adds `join` (Replace/Merge); 011 stays as the gate; new: unattended Git environment; new: Linear single-clone ownership |
| `team-ids` | 001 becomes the `alias` kind; 002–003 merge into rename-in-import; 004 becomes the rename primitive plus the inventory schema test; new: rename guard; 005–006 stay |
| `team-viewer` | Adds plan columns, badges and the provisional marker; data from DTOs |
| **`team-plan`** (new) | Plan entity and tool, pick/next steering, start-session block, `backlog_context` notice |

Order: identity → sync (with plan, which needs immediate push) → ids → viewer.
Team-usable = identity + sync + plan + ids, since collisions are possible from the first offline mint.

## 15. Target modules

| Area | Modules |
|---|---|
| Canonical state and renderer | new `native/team_state.py` (shared-state extraction, canonical hash); `native_routing/projection.py` (canonical render mode) |
| Resolver | new `native/team_merge.py`, reusing `projection_parse` and `sync_merge` field splitting |
| Import command and rename | `native/commands.py` (`team.import`), new `native/rename.py`, `native/schema.py` (migration) |
| Actor (team-identity-002) | `coordinator/service.py` (resolver seam after admit), `coordinator/identity_gate.py`, `coordinator/adapter.py`, `native/contracts.py`, `native/events.py`, `native/receipts.py`, `native/schema.py` + `native/upgrade.py` (schema 5), `backlog_server.py` (clientInfo capture) |
| Cycle, Git and lifetime | new `coordinator/team_sync.py`, `coordinator/service.py` (pending work, linger), `bounded_run.py` |
| Claims and plan | `native/claims.py`, new `native/plan.py`, `native_routing/` adapters for pick/next/context |
| Linear | `native/workflow.py`, `coordinator/linear_worker.py` |
| Tools and skills | `backlog_server.py`; skills `init-team`, `start-session`, `pick-task` (claude-tools plugin) |
| Viewer | `viewer_dto.py`, `viewer_board.py`, `viewer/js/screens/team.js` |
| Guard hooks | claude-tools `plugins/guard-hooks` allow-rule |

Overlap: `store-perf-001` (in progress) changes the native read path under the coordinator
mutex; `team.import` batches and the pre-pick fetch must keep the reads it fixes free of scans.

## 16. Risks, open questions and out of scope

**Risks**

- **Rename inventory drift.** A future table that stores public IDs could escape the rename.
  Mitigation: the inventory schema test (§7). Residual risk: free text outside `[[id]]`.
- **The canonical form isn't as canonical as assumed.** A field with unstable list order or a
  float would cause spurious pushes. Mitigation: the two-clone no-flap test, and treating any
  spurious push found in practice as a bug.
- **The rename guard depends on acknowledgment.** A user who never dismisses rename notes keeps
  the guard indefinitely. That's safe, if noisy; the start-session block lists them.
- **Push rejections under heavy contention.** Every clone retries from fetch; with a handful of
  people and 30 s debounce this should stay rare. Measure it in the gate test with 4 clones.
- **Projects that track `.taskmaster/`** can't enable team mode until a later untrack flow exists.

**Open questions** (for plan review, not blocking this spec)

- Should tombstones and retired IDs be shared, so a deleted ID can never be re-minted by another
  clone (review M4)? The current answer is no: a re-mint after a deletion is a fresh entity.
- Should `team_render` changes trigger a one-time re-render commit? The current answer is no:
  state hashes make that unnecessary.

**Out of scope:** a hosted server; roles; @mentions and notification routing; leases across
people; epic assignment; plan targets/notes (R4); state-branch history compaction; viewer-only
teammates (R6); untracking an existing shared `.taskmaster/` history; signed state-branch commits;
shared trackers and multi-clone Linear; **person-tagged IDs** (`auth-007-jd`), the documented
fallback if the rename guard proves too intrusive in practice, possibly as a per-project
`team.ids: tagged` option.

**Rejected:**

- **Pushing working files** (rev 2): unsynced hand edits leak, and reading races the exporter.
- **Merging by path** (rev 3): moves and multi-entity files make the path the wrong unit (R8).
- **Byte comparison of renders** (rev 3): per-clone ordering and line endings make clones flap (R9).
- **"Tree = whatever rendered"** (rev 3): absences become deletions (R10).
- **Event shipping:** sequences are per clone, and a teammate would need every clone's history.
- **A deferred renumber marker** (rev 2 §5): the coordinator serializes writes; the real hazard
  was stale references, which the guard handles.
- **Plans as `assignee` with status `todo`:** that conflates intent with commitment and can't express order.
- **A shared `plans.yaml`:** many writers on one file.
- **Code-branch lane** (R3): rev 2 §14's reasons still apply.

## 17. Review findings map

| Finding | Where handled |
|---|---|
| Gate A: risks, target files | §16, §15 |
| C1 rename hands the old ID to another entity | §7 rename guard; R5 |
| I1 rename scope | §7 rename primitive and inventory test |
| I2 path vs entity | R8, §6.3 |
| I3 non-canonical render | R9, §6.1, §6.2 step 2 |
| I4 lost push reply | §6.4 steps 2, 5, 6; base ref |
| I5 absence as deletion | R10, §6.2 step 4 |
| I6 join with existing backlog | §6.7 Replace/Merge |
| I7 same person, two clones | §5 `claimed_on` |
| I8 assignee lifecycle | §5 table and general import rule |
| I9 claim-race loser already started | §5 confirmed claims |
| I10 shared-tree inventory | §3 table; `profile` kind |
| I11 named kinds and handles | §7 named kinds; §4 handle clash |
| I12 Linear | §6.9 |
| I13 untracking | §6.7 precondition; §16 out of scope |
| I14 unattended Git | §6.8 |
| Re-review: C1 remainder (reference writes, agent-dismissable guard) | §7 rename guard |
| Re-review N1: outbound not gated on a complete inbound; unparseable theirs | §6.4 step 4; §6.3 quarantine |
| Re-review N2: `pushed_seq` drops writes | §6.2 step 1 |
| M1–M6 (batched) | M1 §6.2 step 3; M2 §6.5; M3 §6.3 steps 3–4; M4 §3 and §16; M5 §6.6; M6 §11 |
