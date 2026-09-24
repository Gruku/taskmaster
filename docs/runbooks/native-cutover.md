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
```

| Exit code | Meaning |
|---|---|
| `0` | Done. A dry run exits 0 when it found nothing to refuse. |
| `2` | Refused. This call changed nothing. |
| `1` | Aborted after the fence went up. The fence stays up; use `--resume` or `--rollback`. |

The command needs the carry-over and quiesce primitives: `taskmaster.native.carryover`
(`snapshot_carryover`, `verify_carryover`, `import_id_state`, `reconcile_progress`,
`read_reservations`) and `taskmaster.native.quiesce`. A build that lacks
any of them refuses before it writes anything and names what is missing.

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

This runs every check and writes nothing: the store is opened read-only. A read-only open of
a WAL database can create an empty `store.db-wal` and a `store.db-shm` index. Neither holds
data, and the store file's bytes do not change. Expected output on a clean copy:

```
cutover dry-run: OK
store_state: {"authority":"legacy",...,"migration_state":"ready","native":false,"schema_version":"1",...}
counts: {"changes":7,"dirty_unexported":0,"entities":6,"export_intents":0,"flagged_conflicts":0,"linear_queue_open":0,"quarantined":0}
quiesce: {"confirm_stopped":false,"live_owner":null,"open_writers":false,"processes":[],...}
would fence: publish meta.migration_state='migrating' with owner/token under the ownership lock
would reconcile: ...
would backup: <project>\.taskmaster\local\backups\pre-native-<UTC ts>.db + manifest (5 projection files)
would backfill: stage 6 entities and 7 changes into native tables
would compare: trial activation, rolled back; verify_carryover must report nothing lost
would activate: schema/protocol markers, authority=native, graph repair, ID import, migration_state=ready
would release: record completion, release the ownership lock
```

For a machine-readable report, add `--json`. Every `refused:` line has to be dealt with
before the real run:

On Windows the scan cannot see a process's working directory, so a Taskmaster plugin server
running for *another* project is also listed. Once you have checked each one, use
`--confirm-stopped`.

| Refusal | Action |
|---|---|
| `a live coordinator or service owns this project` | Stop the service |
| `the store has another open writer` | Some client still has the store open. Go back to step 1 |
| `processes from the launcher inventory ...: pid N ...` | Stop each named process. If you have checked that a match is a false positive, add `--confirm-stopped` |
| `N dirty projection row(s) not yet exported` / `export-intent file(s)` | Start one bridge client, run any tool call (`backlog_status`) so it drains its exports, then stop it again |
| `already a native authority` | Nothing to do |
| `newer than this legacy->native cutover supports` | Wrong binary for this store. Upgrade |
| `a cutover fence is already up` | A previous run was interrupted. See section 4 |
| `malformed ID reservation sidecar` | Fix `.taskmaster/local/id-reservations.json` (a map of kind to a list of ID strings) |
| `warning: process scan: ...` | The scan was partial or impossible. Check the stop list by hand |

## 3. Cut over

```
python -m taskmaster.native.cutover --root <project>
```

Expected output:

```
cutover token 540576eeb4ae4f87a42e529a964c36d2
[fence] done
[reconcile] done
[backup] done
[backfill] done: 6 entities, event high water 7
[compare] done
[activate] done: authority=native; graph repair {'repaired': False, 'rows_compared': 4, ...}
[release] done
cutover run: OK
completed: fence, reconcile, backup, backfill, compare, activate, release
```

**Write down the token.** It proves ownership if you have to resume or roll back later.

What each stage does:

| Stage | Writes | Committed state after it |
|---|---|---|
| `fence` | Sets `meta.migration_state='migrating'`, `migration_owner` and `migration_token`, and creates `native_cutover_journal`, all in one `BEGIN IMMEDIATE`. The coordinator ownership lock (`.taskmaster/local/coordinator/owner.lock`) is held for the whole run | Bridge clients refuse the store |
| `reconcile` | A re-check, under the fence, for unexported projection work. The progress changelog itself is reconciled inside `activate` | Journal row with the counts |
| `backup` | `carryover.snapshot_carryover` (before any marker change), then `backups/pre-native-<UTC ts>.db`, taken with the SQLite online backup, plus `.json`: projection files (path, sha256, size), a copy of the ID-reservation sidecar, the carryover snapshot and the domain digest. The backup is reopened and passes `integrity_check` before its path is journaled | Journal row with the backup path |
| `backfill` | `migrate.backfill` in one transaction; sub-stage checkpoints go into the journal | Native staging `verified`, authority still `legacy` |
| `compare` | A trial of the whole activation transaction, always rolled back: `carryover.verify_carryover` against the backup's carry-over snapshot must be empty | Journal row with the trial's ID import and progress counts |
| `activate` | One transaction: `schema_version=2`, `minimum_client_protocol=2`, `migration_state=ready`, fence owner/token removed, `authority=native`, `state=ready`, `local_state_imported=1`, graph repair, `carryover.import_id_state`, `carryover.reconcile_progress`, then `carryover.verify_carryover`. Any difference rolls the whole switch back. Then the journal row | **The store is native. Roll forward only from here** |
| `release` | Journal row, then the ownership lock is released | Done |

### Verify

- `python -m taskmaster.native.cutover --root <project> --dry-run` refuses with `already a native authority`.
- Start one native-capable client and run `backlog_status`. The first sync after activation
  can be slow; about 350 s was measured on CodeMaestro.
- An old (bridge-only) client has to refuse the store with `Unsupported Taskmaster schema_version=2`.

## 4. Recovery after an interrupted run

Every stage commits on its own, so a crash (an exception, Ctrl-C, a killed process or a power
loss) always leaves the store at a stage boundary recorded in `native_cutover_journal`. Until
`activate` commits, the store is still a legacy authority behind a fence. Ordinary clients
refuse it, and they cannot clear the fence.

**Ownership and takeover rule.** `--resume` and `--rollback` both have to acquire the
coordinator ownership lock. The cutover holds that lock for its entire run, so being able to
acquire it proves the fencing process is gone. The new caller then inherits the journal's
token rather than rotating it, because rotating it would write `meta` and invalidate the
verified staging. The takeover is recorded as a `takeover` journal row. If you pass
`--token`, it has to match the journal. If a live process holds the lock, both commands
refuse. A `migrating` fence with no journal behind it was not created by this command, and
the command will not clear it.

| Crash point | `--resume` | `--rollback` |
|---|---|---|
| During `fence` (before its commit) | Refused (`no cutover journal`). Nothing was written; run the plain command again | Refused (`no cutover fence`). Nothing to undo |
| After `fence`, during or after `reconcile`, `backup`, `backfill` or `compare` | Continues from the next stage and ends native | Clears the fence and journal and restores the previous `migration_*` meta values. Authority stays legacy and the domain rows are identical to before. Native staging is left behind, marked `stale` |
| During `activate` (before its commit) | The activation transaction rolled back; resume re-runs it | Same as the row above |
| After `activate` committed (including during `release`) | Records `release` and finishes | **Refused**, see section 5 |

If `--resume` finds the staging stale, it re-runs `backfill` and `compare` before it
activates.

**Damaged staging.** Before clearing the fence, `--rollback` checks `integrity_check` and
compares the legacy domain digest with the digests journaled at the fence, reconcile and
backup stages. If they don't match and a backup exists, it verifies the backup
(`integrity_check` plus the digest) and restores the store from it with the SQLite backup
API, then clears the fence. If they don't match and there is no backup yet, the cutover
itself has written nothing except the fence. The fence is cleared and a warning names the
difference; look for a pre-bridge client that was not stopped. A store that fails
`integrity_check` with no backup is refused. Recover it by hand from your own copy.

A rollback interrupted part-way can simply be run again.

```
python -m taskmaster.native.cutover --root <project> --resume [--token <token>]
python -m taskmaster.native.cutover --root <project> --rollback [--token <token>]
```

## 5. Post-activation escape hatch (manual, lossy for local state)

Once `activate` has committed, `--rollback` refuses (decision M1 = A). You can roll code back
only to a native-capable version; older binaries are refused by the schema and protocol
markers. Restoring the pre-native backup would throw away every write acknowledged since
activation, so no command does it.

If you have to leave native anyway, the projection files are the durable exchange format:

1. Using a native-capable client, drain every pending export: run a tool call, then check
   `backlog_store_status` until it reports no dirty projections and no pending export.
2. Stop every client (section 1).
3. Check that the projection files form one coherent generation. Check `git status` in the
   project and commit or save them.
4. Move `.taskmaster/local/store.db`, `store.db-wal` and `store.db-shm` aside. Keep them.
5. Start the legacy build. With no store present, it adopts the projection files into a
   fresh legacy store.
6. Check that the tasks, handovers, bugs, issues, decisions, ideas and notes are all there.

What is lost is **DB-local state only**: receipts, sessions, queue leases and Linear queue
state, and the history sequence (`changes`). Everything authored in the projection files
survives. This procedure has not yet been rehearsed; it is part of the N15 copy-only
rehearsal (step 9).

## Files

- The store: `.taskmaster/local/store.db`. The fence is in `meta`, and the journal is in
  `native_cutover_journal`.
- Backups: `.taskmaster/local/backups/pre-native-<UTC ts>.db`, with `.json` (the manifest)
  and `.id-reservations.json`.
- Code: `taskmaster/native/cutover.py`. Tests: `tests/test_native_cutover.py` and
  `tests/test_native_cutover_crash.py`.
