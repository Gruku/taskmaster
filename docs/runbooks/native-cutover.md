<!-- User intent: the operator procedure for cutting a Taskmaster project over from the legacy
     store to the native authority with `python -m taskmaster.native.cutover`, including
     recovery from a crash at every stage and the post-activation escape hatch (M1 = A). -->

# Runbook: native cutover

> **Activating a live project is a release decision.** Nothing in this runbook authorizes
> running the cutover against a real project. Until that decision is made, run it only on
> disposable copies marked `.benchmark-copy`. Never point it at a CodeMaestro checkout.

The command is a CLI, not an MCP tool, because a running MCP server is itself a client that has
to be stopped first.

```
python -m taskmaster.native.cutover --root <project> [--dry-run | --resume | --rollback]
                                    [--confirm-stopped] [--token <token>] [--json]
                                    [--discard-writes-since-backup] [--clear-orphan-fence]
```

`--discard-writes-since-backup` and `--clear-orphan-fence` are only valid with `--rollback`.

Every run prints a report, including when it fails (`--json` prints it as JSON). After a
failure the report carries `fence`, the fence state re-read from the store, and `next`/`hint`,
the action that state calls for.

| Exit code | Meaning |
|---|---|
| `0` | Done. A dry run exits 0 when it found nothing to refuse. Also returned, with a `warning`, when the run or rollback committed but a later step (such as releasing the lock) failed. |
| `1` | Failed while a cutover fence is up, or after activation committed but before `release` was recorded. Use `--resume` or `--rollback`, as the hint says. |
| `2` | Refused. This call changed nothing. A fence left by an earlier run may still be up; the report's `fence` field says so. |
| `3` | Failed before any fence was published, for example `database is locked`. The store's authority is unchanged. Fix the cause and re-run. |

The command needs the carry-over and quiesce primitives: `taskmaster.native.carryover`
(`snapshot_carryover`, `verify_carryover`, `import_id_state`, `reconcile_progress`,
`read_reservations`) and `taskmaster.native.quiesce`. A build that lacks any of them refuses
before it writes anything and names what is missing.

## 1. Stop every client (the launcher stop list)

Pre-bridge binaries cannot read the migration fence, so the stop list is what actually
protects the store; the fence is not enough on its own. This inventory comes from
`docs/handoffs/2026-09-09-native-client-fencing.md`.

| Surface | How it launches | What to do |
|---|---|---|
| Claude plugin | `.mcp.json`: `uv run ${CLAUDE_PLUGIN_ROOT}/backlog_server.py` | Close every Claude Code session on the project, or restart its MCP servers after upgrading the plugin cache |
| Codex plugin | `.codex-plugin/plugin.json`: `uv run backlog_server.py` | Close the Codex host connection |
| Local/manual Python | root `backlog_server.py` imports `taskmaster.backlog_server` | Stop each manual server |
| HTTP viewer | started by the same server module | Stop its owning server, and check that the viewer port no longer answers |
| Edit/merge hooks | system-Python scripts under `hooks/` | Do not edit files or run git merges in the project during the cutover |
| Maintenance scripts | TLDR backfill, handover-status migration, link migration | Make sure none is running or scheduled |
| Linear | server worker and `Store.linear_*` | Stops with the server; do not restart it until the cutover is verified |

Updating files on disk does not change a process that is already running. Stop the process.

## 2. Dry run

```
python -m taskmaster.native.cutover --root <project> --dry-run
```

This runs the checks and writes nothing:

- The store is opened read-only, so the store file and its WAL keep their exact bytes.
- A read-only open of a WAL database can create an empty `store.db-wal` and a `store.db-shm`
  index when neither exists. Neither file holds data.
- `open_writers` is **not** probed in a dry run, because its read-write probe connection can
  checkpoint a leftover WAL into the store file. The report says `"not probed in dry run"`, and
  the real run does probe it.
- `live_owner` may take and immediately release the byte lock on
  `.taskmaster/local/coordinator/owner.lock` when that file exists. This changes no file bytes.

Expected output on a clean copy:

```
cutover dry-run: OK
store_state: {"authority":"legacy",...,"migration_state":"ready","native":false,"schema_version":"1",...}
counts: {"changes":7,"dirty_unexported":0,"entities":6,"export_intents":0,"flagged_conflicts":0,"linear_queue_open":0,"quarantined":0}
quiesce: {"confirm_stopped":false,"live_owner":null,"open_writers":"not probed in dry run","processes":[],...}
warning: open writers not probed in dry run (the probe can checkpoint the WAL); the real run probes them
would fence: publish meta.migration_state='migrating' with owner/token under the ownership lock
would reconcile: re-check unexported projection work under the fence
would backup: <project>\.taskmaster\local\backups\pre-native-<UTC ts>.db + manifest (5 projection files)
would backfill: stage 6 entities and 7 changes into native tables
would compare: trial activation, rolled back; verify_carryover must report nothing lost
would activate: one transaction: schema_version=2, minimum_client_protocol=2, migration_state=ready, authority=native, graph repair, ID import, progress reconcile, verify_carryover (any loss rolls it back)
would release: record completion, release the ownership lock
```

Deal with every `refused:` line before the real run:

| Refusal | Action |
|---|---|
| `a live coordinator or service owns this project` | Stop the service |
| `the store has another open writer` (real run only) | Some client still has the store open. Go back to step 1 |
| `processes from the launcher inventory ...: pid N ...` | Stop each named process. If you have checked that a match is a false positive, add `--confirm-stopped` |
| `N dirty projection row(s) not yet exported` / `export-intent file(s)` | Start one bridge client, run any tool call (`backlog_status`) so it drains its exports, then stop it again |
| `the legacy handover-status backfill has not run on this store` | Start one bridge client, run `backlog_handover_list` once (it runs the one-shot backfill), then stop it again. A native store never runs that backfill |
| `malformed ID reservation sidecar` | Fix `.taskmaster/local/id-reservations.json`, which must map each kind to a list of ID strings |
| `already a native authority` | Nothing to do |
| `newer than this legacy->native cutover supports` | Wrong binary for this store. Upgrade |
| `a cutover fence is already up` / `a cutover journal already exists` | A previous run was interrupted. See section 4. A fresh run never reuses an old journal |
| `warning: process scan: ...` | The scan was partial or impossible. Check the stop list by hand |

On Windows the scan cannot see a process's working directory, so a Taskmaster plugin server
running for *another* project is also listed. Check each match, then use `--confirm-stopped`.

## 3. Cut over

```
python -m taskmaster.native.cutover --root <project>
```

Expected output:

```
cutover token 72ad0908753e4e93824e741ce8a27aa5
[fence] done
[reconcile] done
[backup] done
[backfill] done: 6 entities, event high water 7
[compare] done
[activate] done: authority=native; graph repair {...}; ids {'high_water': {...,'bug': 9,...}, 'reservations': 1}
[release] done
cutover run: OK
completed: fence, reconcile, backup, backfill, compare, activate, release
```

**Write down the token.** It proves ownership if you have to resume or roll back later.

What each stage does:

| Stage | Writes | Committed state after it |
|---|---|---|
| `fence` | Sets `meta.migration_state='migrating'`, `migration_owner` and `migration_token`, and creates `native_cutover_journal`, all in one `BEGIN IMMEDIATE`. The coordinator ownership lock (`.taskmaster/local/coordinator/owner.lock`) is held for the whole run | Bridge clients refuse the store |
| `reconcile` | Re-checks, under the fence, that no projection export is still pending. The progress changelog itself is reconciled inside `activate`, after the final backfill | Journal row with the counts |
| `backup` | `carryover.snapshot_carryover` (before any marker change), then `backups/pre-native-<UTC ts>.db` via the SQLite online backup, plus `pre-native-<UTC ts>.projection.zip` (every projection file, with the archive's sha256 and size recorded) and a `.json` manifest: projection files (path, sha256, size), a copy of the ID-reservation sidecar, the carry-over snapshot and the domain digest. The files are fsynced, and the backup is reopened and must pass `integrity_check` | Journal row with the backup path, the archive, the sidecar hash **and the carry-over snapshot**, so resume never depends on the manifest |
| `backfill` | `migrate.backfill` in one transaction; sub-stage checkpoints go into the journal | Native staging `verified`; authority still `legacy` |
| `compare` | A trial of the whole activation transaction, always rolled back: `carryover.verify_carryover` against the journaled snapshot must be empty | Journal row with the trial's ID import and progress counts |
| `activate` | One transaction: `schema_version=2`, `minimum_client_protocol=2`, `migration_state=ready`, fence owner/token removed, `authority=native`, `state=ready`, `local_state_imported=1`, graph repair, `carryover.import_id_state`, `carryover.reconcile_progress`, `carryover.verify_carryover`, journal row. Any carry-over difference rolls the whole switch back | **The store is native. Roll forward only from here** |
| `release` | Journal row, then the ownership lock is released | Done |

### Verify

- `python -m taskmaster.native.cutover --root <project> --dry-run` refuses with `already a native authority`.
- Start one native-capable client and run `backlog_status`. The first sync after activation
  can be slow; about 350 s was measured on CodeMaestro.
- An old (bridge-only) client has to refuse the store with `Unsupported Taskmaster schema_version=2`.
  This is tested in `test_pre_native_clients_are_refused_by_an_activated_store`.

## 4. Recovery after an interrupted run

Each database change a stage makes commits in the same transaction as that stage's journal
row, so the store's committed state is always the state after some recorded stage. Files
outside the store are different: a crash can leave a `.partial` backup, a half-written
manifest, or an unused `pre-native-*` file behind. These are harmless. The backup a resume or
rollback relies on is the one named in the journal, and it is verified before use. Until
`activate` commits, the store is still a legacy authority behind a fence. Ordinary clients
refuse it, and they cannot clear the fence.

**Ownership and takeover rule.** Before activation, `--resume` and `--rollback` must acquire
the coordinator ownership lock. The cutover holds that lock for its entire run, so being able
to acquire it proves the fencing process is gone. The new caller then inherits the journal's
token rather than rotating it, because rotating it would write `meta` and invalidate the
verified staging. The takeover is recorded as a `takeover` journal row. If you pass
`--token`, it must match the journal. If a live process holds the lock, both commands refuse.

| Crash point | `--resume` | `--rollback` |
|---|---|---|
| During `fence` (before its commit) | Refused (`no cutover journal`). Nothing was written; run the plain command again | Refused (`no cutover fence`). Nothing to undo |
| After `fence`, during or after `reconcile`, `backup`, `backfill` or `compare` | Continues from the next stage and ends native | Clears the fence and journal and restores the previous `migration_*` meta values. Authority stays legacy and the domain rows are identical to before the cutover (or to the latest backup, if leaked writes were absorbed into it). Native staging is left behind, marked `stale` |
| During `activate` (before its commit) | The activation transaction rolled back; resume re-runs it | Same as the row above |
| After `activate` committed (including during `release`) | Records `release` and finishes. **No quiesce and no ownership lock needed**: native clients may already be running, and the fence is gone | **Refused**, see section 5 |

If `--resume` finds the staging stale, it re-runs `backfill` and `compare` before it
activates.

**Writes that leaked through the fence are absorbed, not lost.** Before `backfill`,
`compare` and `activate`, the run checks for legacy writes since the latest backup: any
domain row (retained tables such as `sessions`, `projection` and `meta` included) or a changed
`id-reservations.json`. If it finds any, it records a `drift` journal row naming them, then
re-runs `reconcile`, `backup` (a fresh backup, archive and carry-over snapshot, committed
with its journal row) and `backfill` under the fence. The writes then carry into the native
store, and the report's `warnings` and `drift_absorbed` say so. If a client keeps writing, the
run stops after 3 absorptions with `a client is still writing`. Stop it, then `--resume`.

### What rollback does when the store changed

The whole rollback is **one transaction on one connection** (`BEGIN IMMEDIATE`). The diff,
the pre-rollback copy, the file restore, the row restore from the attached backup, marking the
staging stale, and clearing the fence all happen before a single commit. While that write
lock is held, no other writer can commit, so no write can land between the copy and the
restore. The pre-rollback copy is taken by a second, read-only connection while the lock is
held, so it contains exactly the committed state being replaced.

Before it clears the fence, `--rollback` checks `integrity_check` and compares the legacy
domain digest with the digests journaled at the fence, reconcile and backup stages.

- **Matches:** it only clears the fence and journal.
- **Differs, no backup yet:** the cutover itself has written nothing but the fence, so the
  change came from a client that ignored it. The fence is cleared, and a warning says so.
- **Differs, backup exists, the store is healthy:** rollback compares the store with the
  backup row by row, and the projection files on disk with the archived generation.
  **It refuses** if either of these differs:
  - `entities`, `changes`, `linear_queue`, `projection`, or a user `meta` key (anything
    except the cutover's own `migration_*`/`cutover_*` keys: the session changelog, Linear
    receipts, and so on);
  - any projection file (changed, added or removed).

  The message names the tables, counts and example keys, such as
  `entities: 0 added, 0 removed, 1 changed (e.g. [["bug","B-001"]])`, and the files, such as
  `projection files differ from the backup: 1 changed (backlog.yaml); 1 added (bugs/B-010.md)`.
  Those are writes from an unstopped client.
  - To **keep** them, stop that client and run `--resume`. The cutover absorbs them, as
    described above, and they roll forward into the native store.
  - To **discard** them, run `--rollback --discard-writes-since-backup`. The archived
    projection files are put back, files added since are deleted, and the rows are restored.

  If only disposable or derived tables differ (`sessions`, graph tables, FTS), rollback
  restores without the flag and prints a warning.
- **The store fails `integrity_check`** (only genuine corruption: `SQLITE_CORRUPT` or
  `SQLITE_NOTADB`): it rebuilds the store's indexes, restores the rows from the verified
  backup without the flag, and checks integrity again. If that still fails, it refuses and
  names the backup for a manual restore. Other errors, such as `database is locked` or an I/O
  error, are never treated as corruption; the rollback stops and changes nothing.

**Before any restore**, the current store is saved as `backups/pre-rollback-<UTC ts>.db`.
If the projection files are restored, the divergent ones are saved first as
`backups/pre-rollback-<UTC ts>.projection.zip`, together with `_pre_rollback_files.json`,
which lists what was changed, added and removed. If the store is too damaged for the backup
API, the database file and its `-wal` are copied as they are and fsynced; the `-shm` index is
never copied. The report names both copies and lists what was discarded.

A rollback interrupted part-way, even by a killed process, can simply be run again. The
database side is one transaction. The file restore happens inside that unit, before the
commit, and repeating it is harmless.

```
python -m taskmaster.native.cutover --root <project> --resume [--token <token>]
python -m taskmaster.native.cutover --root <project> --rollback [--token <token>] [--discard-writes-since-backup]
```

### Getting discarded writes back from a pre-rollback copy

A discard is recoverable by hand. With every client stopped (section 1):

1. Keep the current store: copy `.taskmaster/local/store.db` (and any `store.db-wal`)
   somewhere safe.
2. To take back the whole pre-rollback state, replace `store.db` with
   `backups/pre-rollback-<ts>.db`, delete `store.db-wal` and `store.db-shm`, and restore the
   files from `backups/pre-rollback-<ts>.projection.zip` into `.taskmaster/`, deleting the
   paths it lists under `removed`. The store is then in its fenced, pre-rollback state again.
   Run `--resume` to carry those writes forward, or `--rollback` again.
3. To take back single documents, open the copy read-only (`sqlite3
   backups/pre-rollback-<ts>.db`) and read the rows you need from `entities`, or extract the
   file from the `.projection.zip`. Re-enter them through a bridge client. Never write rows
   into a live store by hand.

### A fence with no journal

A `migration_state='migrating'` with no `native_cutover_journal` table was not left by a
cutover run that got as far as its first commit. It could come from a future or foreign
migrator, or from a hand edit. `--resume` and a plain `--rollback` both refuse it. Once you
are sure no migration is in progress (step 1 stop list, no other migrator running):

```
python -m taskmaster.native.cutover --root <project> --rollback --clear-orphan-fence
```

This removes only `migration_state`, `migration_owner` and `migration_token`, under the
ownership lock, after the quiesce checks, and reports the values it removed. If the command is
unavailable, the manual equivalent with every client stopped is
`DELETE FROM meta WHERE key IN ('migration_state','migration_owner','migration_token')`. Take a
copy of `store.db` first.

## 5. Post-activation escape hatch (manual, lossy for local state)

Once `activate` has committed, `--rollback` refuses (decision M1 = A). You can roll code back
only to a native-capable version; older binaries are refused by the schema and protocol
markers. Restoring the pre-native backup would throw away every write acknowledged since
activation, so no command does it.

If you have to leave native anyway, the projection files are the durable exchange format:

1. Using a native-capable client, drain every pending export: run a tool call, then check
   `backlog_store_status` until it reports no dirty projections and no pending export (no
   `projection_jobs` in state `pending`, `claimed` or `conflict`).
2. Stop every client (section 1).
3. Check that the projection files form one coherent generation. Check `git status` in the
   project and commit or save them.
4. Move `.taskmaster/local/store.db`, `store.db-wal` and `store.db-shm` aside. Keep them.
5. Start the legacy build. With no store present, it adopts the projection files into a
   fresh legacy store.
6. Check that the tasks, handovers, bugs, issues, decisions, ideas and notes are all there.

What is lost is **DB-local state only**: receipts, sessions, queue leases and Linear queue
state, and the history sequence (`changes`). Everything authored in the projection files
survives. This is automated in
`test_escape_hatch_recovers_every_authored_document_into_a_fresh_legacy_store`: a cut-over
project gets native writes, and its files, without the store, are adopted by a fresh legacy
store that holds every authored document: every kind, archived items, prose bodies and unknown
fields. The documents match field for field, except two things a fresh adoption adds or
derives, which are not authored state: the backlog row's derived index keys, and the
`meta.projection_schema` stamp the legacy importer writes. The copy-only rehearsal on
CodeMaestro (N15 step 9) is still outstanding.

## Files

- The store: `.taskmaster/local/store.db`. The fence is in `meta`, and the journal is in
  `native_cutover_journal`.
- Backups: `.taskmaster/local/backups/pre-native-<UTC ts>.db`, with `.json` (the manifest),
  `.projection.zip` (the archived projection files) and `.id-reservations.json`. Rollback
  copies are `pre-rollback-<UTC ts>.db`, plus `.projection.zip` when files were restored.
- Code: `taskmaster/native/cutover.py`. Tests: `tests/test_native_cutover.py` and
  `tests/test_native_cutover_crash.py`. Every twins activation in the test suite runs the
  carry-over oracle by default (`TASKMASTER_TWINS_VERIFY=1`, set in `tests/conftest.py`, +2.3%
  suite time); set it to `0` to opt out.
