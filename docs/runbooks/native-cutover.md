<!-- User intent: the operator procedure for cutting a Taskmaster project over from the legacy
     store to the native authority with `python -m taskmaster.native.cutover`, including
     recovery from a crash at every stage and the post-activation escape hatch (M1 = A). -->

# Runbook: native cutover

> **Rehearse on a copy first.** Before cutting over a real project, run this whole procedure on
> a disposable copy of it (a clone with `hooksPath` outside the copy works) and deal with every
> refusal there. Activation is one-way: after it commits, the only way back is the manual
> escape hatch in section 5. See also the [native store guide](../native-store.md) for what
> changes for agents and operators afterwards.

The command is a CLI, not an MCP tool, because a running MCP server is itself a client that has
to be stopped first.

```
python -m taskmaster.native.cutover --root <project> [--dry-run | --resume | --rollback]
                                    [--confirm-stopped] [--token <token>] [--json]
                                    [--clear-orphan-fence]
```

`--clear-orphan-fence` is only valid with `--rollback`.

`python -m` needs a source checkout and its dev venv. From an installed plugin, run
`uv run <plugin>/taskmaster_cli.py cutover --root <project> ...` with the same arguments.
It runs in the plugin's own uv environment. See
[release-packaging.md](release-packaging.md#how-the-package-runs).

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

**Just before you stop the last client, run one tool call** (for example `backlog_handover_list`).
Its scan adopts every file changed since the last one, such as a `git pull`, so a file that no
longer parses is quarantined, and the dry run can name it, before the fence goes up.

**From that tool call until the cutover ends, do not pull, check out, add or edit files in the
project.** The fenced run catches only some later changes, and those cost a `--rollback`. It
never sees a new file added after its scan. See "Under the fence" in
[Repairing quarantined files before the cutover](#repairing-quarantined-files-before-the-cutover).

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
would reconcile: scan the files and flush pending legacy exports under the fence (as one admitted bridge client)
would backup: <project>\.taskmaster\local\backups\pre-native-<UTC ts>.db + manifest (5 projection files)
would backfill: stage 6 entities and 7 changes into native tables
would compare: trial activation, rolled back; verify_carryover must report nothing lost
would activate: one transaction: schema_version=2, minimum_client_protocol=2, migration_state=ready, authority=native, graph repair, ID import, progress reconcile, verify_carryover (any loss rolls it back), then merge bases for projection files whose bytes hash to their recorded digest
would release: record completion, release the ownership lock
```

Deal with every `refused:` line before the real run:

| Refusal | Action |
|---|---|
| `a live coordinator or service owns this project` | Stop the service |
| `the store has another open writer` (real run only) | Some client still has the store open. Go back to step 1 |
| `processes from the launcher inventory ...: pid N ...` | Stop each named process. If you have checked that a match is a false positive, add `--confirm-stopped` |
| `the legacy handover-status backfill has not run on this store` | Start one bridge client, run `backlog_handover_list` once (it runs the one-shot backfill), then stop it again. A native store never runs that backfill |
| `N projection file(s) are quarantined, and native managed Git refuses while any is: <path> (<reason>); ...` | Repair each named file, content preserved, and re-adopt it. See [Repairing quarantined files before the cutover](#repairing-quarantined-files-before-the-cutover) |
| `N projection file(s) are flagged (the file and the store both changed), and native managed Git holds on them too: ...` | Resolve each with `backlog_resolve_conflict`. See the same section |
| `malformed ID reservation sidecar` | Fix `.taskmaster/local/id-reservations.json`, which must map each kind to a list of ID strings |
| `already a native authority` | Nothing to do |
| `newer than this legacy->native cutover supports` | Wrong binary for this store. Upgrade |
| `a cutover fence is already up` / `a cutover journal already exists` | A previous run was interrupted. See section 4. A fresh run never reuses an old journal |
| `warning: N dirty projection row(s) ... / export-intent file(s): the cutover flushes them itself` | Nothing to do. This is not a refusal: the `reconcile` stage exports them under the fence, acting as one admitted bridge client, because no external client can once the fence is up. Anything it cannot export is carried into the native store with its dirty flag, and activation queues it as native export jobs so the native exporter writes it on its next drain. It is never dropped |
| `warning: process scan: ...` | The scan was partial or impossible. Check the stop list by hand |

On Windows the scan cannot see a process's working directory, so a Taskmaster plugin server
running for *another* project is also listed. Check each match, then use `--confirm-stopped`.

### Repairing quarantined files before the cutover

The legacy store quarantines a projection file it cannot parse: it keeps the file exactly as
written, imports nothing from it, and lists it under `Quarantined` in `backlog_store_status`.
After activation, native managed Git operations (commit, checkout) refuse with "projections are
not synchronized" for as long as any file is quarantined, and they hold on flagged files too
(a file and the store both changed; `Flagged` in `backlog_store_status`). So the cutover, and
its dry run, refuse to start while the store has any quarantined or flagged file. They name each
quarantined file with the reason the legacy store recorded (the same line it wrote to
`.taskmaster/local/store.log`). This includes a `project.yaml` that never parsed: it has no
projection row, so `backlog_store_status` does not list it, but the store's quarantine log keeps
it and the cutover names it.

Repair each file in place and keep its content. Start one bridge client for this, and stop it
again before the real run.

1. Run `backlog_store_status` and note the `Quarantined:` files. The dry run lists the same files.
2. Fix the cause the reason names:
   - **`missing or invalid frontmatter`**: the file has no `---` frontmatter block, or an
     empty one. For a markdown handover, add a minimal block above the unchanged text. For
     `handovers/_archive/2026/2026-03-04-session-notes.md`:

     ```
     ---
     id: 2026-03-04-session-notes
     date: '2026-03-04'
     tldr: One line saying what the session was about
     next_action: ''
     task_ids: []
     session_kind: continuity
     status: closed
     archived: true
     ---
     <the original text, unchanged>
     ```

     The keys are what `build_handover_doc` (`taskmaster/taskmaster_v3.py`) always writes,
     without the creation timestamps and empty lists it adds:
     - `id` must equal the file name without `.md`. A different `id` is quarantined again.
     - `date` is the `YYYY-MM-DD` prefix of the id.
     - `session_kind` is one of `continuity`, `deep-context`, `milestone`, `auto-stage` or
       `task-complete`.
     - `status` is one of `open`, `closed` or `superseded`. Use `closed` for an archived
       handover. With a status set, the handover is not owed the legacy status backfill.
     - `archived: true` is what the exporter writes into every file under
       `handovers/_archive/`. A file repaired in place is imported from its frontmatter, so
       without this key the handover comes back as live.
   - **`git conflict markers`**: resolve the whole-line `<<<<<<<` / `>>>>>>>` conflict. A body
     line of `=======` alone is not a conflict marker in this build
     (`taskmaster/projection_parse.py`, `_CONFLICT_MARKER`). Released builds up to and
     including 6.0.3 quarantined it anyway (CodeMaestro's B-339). Such a file needs no edit
     under this build, only the touch in step 3.
   - **`... path id ... does not match frontmatter id ...`**: make `id` equal the file name.
   - **A `project.yaml` YAML error**: fix the YAML. The file is a plain mapping, with no
     frontmatter.

   **Keep the file's own line endings.** If the file uses CRLF (check with
   `git ls-files --eol <path>`, or look for `\r\n` in its bytes), write the added frontmatter
   in CRLF too; if it uses LF, write LF. The parser accepts either and normalizes them, so a
   mixed file still parses, but you would leave a file with mixed line endings in the
   repository. CodeMaestro's files are CRLF. An editor that saves the whole file in one style
   is fine; a script that prepends a block with `\n` to a CRLF file is not.
3. Run any tool, for example `backlog_handover_list`. Its scan re-reads the changed file and
   adopts it.

   **A file quarantined by an older build's rule clears only after you touch it.** The scan
   skips a quarantined file whose modification time and size still equal the stamp recorded
   when it was quarantined (`Store._quarantine_stamp_matches` in `taskmaster/store.py`); it
   re-reads the file only when either changes. A file you edited in step 2 has changed. A file
   you did not need to edit (the B-339 case above: its bytes parse under this build, but an
   older build quarantined them) keeps its old stamp and stays quarantined. Update its
   modification time without changing its bytes, then run the tool call again:

   ```
   python -c "import os, sys; os.utime(sys.argv[1])" .taskmaster/bugs/B-339.md
   ```
4. Run `backlog_store_status` again and confirm that it lists no `Quarantined:` and no
   `Flagged:` files, then re-run the dry run.

**Expect `backlog.yaml` to change.** Re-adopting a repaired entity that `backlog.yaml`
indexes (a bug, for example) puts its index entry back, so the store exports a new
`backlog.yaml`. The repair itself does not touch that file, so `git status` shows
`M .taskmaster/backlog.yaml` after the re-adopt or after the cutover's `reconcile` flush. In
the CodeMaestro rehearsal the difference was exactly B-339's 11-line entry under `bugs:`. This is
expected: commit it with the repaired files, or let the first managed commit after the
cutover (`git_run`, see [the native store guide](../native-store.md#git)) include it.

**When the repaired file is flagged instead.** If the store changed the entity while its file
was quarantined (the export was suppressed; `backlog_store_status` lists the file under
`Stuck exports`), step 3 does not adopt the repair. The store keeps its version, the file stays
as you wrote it, and the file is **flagged**, because nothing records which of the two was meant.
Then `backlog_resolve_conflict` is the next step:

- `backlog_resolve_conflict(file="<path>")` shows both versions.
- `take="store"` writes the store's version over the file. Use it when the repair only restored
  what the store already holds.
- `take="file"` imports the file as it is on disk.

Either way the replaced text is kept in the change log. `backlog_resolve_conflict` does nothing
for a file that is still quarantined: repair it first (steps 2 and 3).

**Under the fence.** The preflight runs only on a fresh run. The `reconcile` stage always scans
the files on disk (whether or not exports are pending) and checks again, and `compare` checks the
recorded state just before activation. A file that stopped parsing after the preflight (a
`git pull`, an editor) or a flag raised by the flush aborts the run there, with exit code 1,
naming the files, and nothing is activated. Run `--rollback`, repair the files as above with a
bridge client, and start a fresh cutover. `--resume` cannot help, because the fence refuses the
bridge client that re-adopts a repair.

The drift check compares only the files the store already tracks. A tracked file changed after
the backup differs from the backup's archive, so the run treats it as drift and re-runs
`reconcile`, with its scan. The cutover does not see:

- a tracked file changed between the reconcile scan and the backup;
- a tracked file changed after the last drift check, which runs just before `activate`;
- a **new** file added after the reconcile scan (for example a headerless handover brought in by
  a `git pull`), because no drift check looks at untracked files.

Native sync finds these only after activation: it quarantines a file that does not parse, and
you repair it as described next. To avoid this, change nothing in the project from the final
tool call before the cutover until the cutover ends (section 1).

**After activation.** Native sync quarantines a file it cannot parse, and managed Git refuses
until it is fixed. Edit the file: the next sync re-parses the changed bytes and, when they parse,
clears the quarantine. If the store has no merge base for that file (activation seeds bases only
for files whose bytes match what the legacy store recorded) and the repaired bytes differ from
the store's version, the sync flags the file instead of importing it; resolve it with
`backlog_resolve_conflict`.

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
| `reconcile` | Scans the projection files and flushes pending legacy exports and export intents itself, under the fence: one no-op legacy write transaction admitted by `migration_owner`, the same scan and drain a bridge client's next call runs. It always runs, even with nothing pending, so the held-file check after it sees the files as they are on disk (about 2.5 s on a CodeMaestro-sized store). The session rows the flush writes for itself are put back as they were. Whatever still cannot be exported is carried, and a warning lists it; `activate` queues it as native projection jobs (as native sync queues an entity). The progress changelog itself is reconciled inside `activate`, after the final backfill | Journal row with the counts before and after, and what was carried |
| `backup` | All taken while the cutover holds the write lock, so they describe one committed state: `carryover.snapshot_carryover` (before any marker change), `backups/pre-native-<UTC ts>.db` (the SQLite online backup, made by a read-only connection), `pre-native-<UTC ts>.projection.zip` (the **projection set**, meaning the files the store tracks in its `projection` table: `backlog.yaml` and every document file; other files under `.taskmaster/` are the user's and are never archived, compared or restored; with the archive's sha256 and size recorded) and a `.json` manifest: projection files (path, sha256, size), a copy of the ID-reservation sidecar, the carry-over snapshot and the domain digest. The files are fsynced, and the backup is reopened and must pass `integrity_check` | Journal row with the backup path, the archive, the sidecar hash **and the carry-over snapshot**, so resume never depends on the manifest |
| `backfill` | `migrate.backfill` in one transaction; sub-stage checkpoints go into the journal | Native staging `verified`; authority still `legacy` |
| `compare` | A trial of the whole activation transaction, always rolled back: `carryover.verify_carryover` against the journaled snapshot must be empty | Journal row with the trial's ID import and progress counts |
| `activate` | One transaction: `schema_version=2`, `minimum_client_protocol=2`, `migration_state=ready`, fence owner/token removed, `authority=native`, `state=ready`, `local_state_imported=1`, graph repair, `carryover.import_id_state`, `carryover.reconcile_progress`, `carryover.verify_carryover`, then merge-base seeding (below), journal row. Any carry-over difference rolls the whole switch back | **The store is native. Roll forward only from here** |
| `release` | Journal row, then the ownership lock is released | Done |

### Verify

- `python -m taskmaster.native.cutover --root <project> --dry-run` refuses with `already a native authority`.
- Start one native-capable client and run `backlog_status`.
- The first explicit sync after activation is fast when activation could seed its merge bases.
  Activation reads every projection file that has no base and hashes it itself. When the bytes
  hash to the recorded `content_hash`, it stores them as the file's `projection_base`. It never
  replaces an existing base, and it skips quarantined, flagged and drift files. The seeded paths
  are journaled on the `activate` row, and `verify_carryover(..., seeded_bases=...)` accepts
  exactly those. After commit, the same reads seed the sync fingerprint cache
  (`local/cache/sync-fingerprints.json`, valid for an hour).
- Measured on a CodeMaestro copy (3,704 files): seeding adds about 9 s to `activate`, and the
  first sync then takes 4.4 s in one round. Before N16 it took 626 s unbounded, or about 680 s
  over six 120 s rounds. Only files that really differ are imported, and only held files stay
  pending. A file whose bytes differ from its digest gets no base, so its first sync takes the
  ordinary path.
- The sync result reports `imports` (domain writes) and `observed` (published bytes recorded as
  a base) separately. A write the time budget runs out on gets a grace period. The grace and
  the completion receipt share one 5 s allowance (the receipt always gets at least 0.5 s), so
  the target is a reply within the budget plus about 5 s. That is a target, not a bound: two
  steps are not budgeted. A write the budget ran out on still has its outcome looked up
  (a queue cancel and a receipt read). And after the last file, the final publication and the
  completion check re-read the published files. These normally take a few seconds, but a
  slow disk or a large store can push the reply past the target.
  The write is then reported as one of three outcomes:
  - `committed`: its receipt exists.
  - `not_committed`: it was cancelled before the writer ran it.
  - `uncertain`: the writer is still running it, or was interrupted and left no receipt. This
    is the only case where you need to inspect the receipt.

  Unsettled observes are listed under `observes`, never as imports.
- Known limit of the sync fingerprint cache (N13, N16):
  - What the cache trusts: a file whose volume, file id, size, mtime and change time are all
    unchanged is not read again for up to an hour (`CACHE_TTL`). On Windows the change time is
    NTFS ChangeTime. `lstat`'s `st_ctime` there is the creation time.
  - What it now catches: an in-place rewrite whose mtime is restored moves the change time, so
    it is read and imported at once.
  - What it can miss: a write through a memory mapping moves no timestamp, not even
    ChangeTime, so a sync cannot see it until the cache ages out. That delays the import; it
    does not lose the edit. A named resync (`files=...`) or `take_file` reads the bytes at
    once.
  - Where the cache is never used: managed Git's generation check reads every file, so such
    an edit is refused before any commit or checkout touches it.
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
| After `fence`, during or after `reconcile`, `backup`, `backfill` or `compare` | Continues from the next stage and ends native | If the store still matches what the cutover recorded (see below): clears the fence and journal, restores the previous `migration_*` meta values and drops native staging, so a fresh cutover starts from nothing. It **restores nothing**, so it never loses a write; authority stays legacy. Otherwise it refuses and offers three ways on |
| During `activate` (before its commit) | The activation transaction rolled back; resume re-runs it | Same as the row above |
| After `activate` committed (including during `release`) | Records `release` and finishes. **No quiesce and no ownership lock needed**: native clients may already be running, and the fence is gone | **Refused**, see section 5 |

If `--resume` finds the staging stale, it re-runs `backfill` and `compare` before it
activates.

**Writes that leaked through the fence are absorbed, not lost.** Before `backfill`,
`compare` and `activate`, the run checks for legacy writes since the latest backup: any
domain row (retained tables such as `sessions`, `projection` and `meta` included), a changed
`id-reservations.json`, or a projection file that differs from the latest archive. If it finds any, it records a `drift` journal row naming them, then
re-runs `reconcile` (which flushes any export the leaked write left pending), `backup` (a fresh backup, archive and carry-over snapshot, committed
with its journal row) and `backfill` under the fence. The writes then carry into the native
store, and the report's `warnings` and `drift_absorbed` say so. If a client keeps writing, the
run stops after 3 absorptions in one invocation with `a client is still writing`. The count
is journaled, so a crash inside the invocation does not reset it. Stop the client, then run
`--resume`: it absorbs the last writes and completes. (A cap across invocations would trap
those writes behind a permanent refusal.) Alternatively, `--rollback --clear-orphan-fence`
leaves the store legacy with every write kept.

Resume never writes projection files itself. The only file writes during a cutover are those
of the legacy exporter in the `reconcile` flush, and they happen before the backup that
archives them.

### What rollback does when the store changed

`--rollback` **restores nothing**, so it can never lose a write. Under one `BEGIN IMMEDIATE`
on the live store, it clears the fence only when the store matches what the cutover recorded:

- the store passes `integrity_check`; and
- **once a backup exists:** the legacy domain rows (retained tables such as `sessions`,
  `projection`, `meta` and `linear_queue` included) have the latest backup's digest, or a
  reconcile digest journaled after it; `id-reservations.json` has the latest backup's hash;
  and every projection file (the files the store tracks) has the hash in the latest
  backup's archive, compared by path; or
- **before any backup:** the legacy domain rows have the fence-time or a reconcile-time
  digest. The sidecar and the files are not compared, because nothing recorded them yet.

A match does not prove no client touched the project. It proves the rollback leaves the
store exactly as the cutover last recorded it, and since nothing is restored, every write
made meanwhile is still in place either way. When the store matches, the rollback restores the
previous `migration_*` meta values, drops the journal and native staging (including the
native graph indexes on legacy tables), and commits.

Anything else refuses, names what differs, and offers three ways on:

1. `--resume`: go native, keeping every write. The cutover absorbs them. This is the
   recommended option.
2. `--rollback --clear-orphan-fence`: stay legacy, keeping every row and file as it is now.
3. The manual restore below: discard everything written since the backup. It copies what it
   replaces aside first.

| Refusal | What it means |
|---|---|
| `the store changed since the latest backup: entities: 0 added, 0 removed, 1 changed (e.g. [["bug","B-001"]]) ...` | A client wrote through the fence |
| `projection files differ from the latest backup's archive: 1 changed (backlog.yaml); 1 added (bugs/B-010.md)` | A client wrote files through the fence |
| `id-reservations.json changed since the latest backup` | A client reserved IDs through the fence |
| `the projection files cannot be compared: ... archive ... is missing` / `... (the backup ... is unavailable)` | A recorded backup file is gone, so option 3 is not offered. `--resume` takes a fresh backup (even when nothing else changed); `--rollback --clear-orphan-fence` stays legacy |
| `the store fails integrity_check; nothing was changed ...` | The store is damaged. Neither `--resume` nor activation proceeds (both check integrity). The message names the way on that exists: the manual restore below when a cutover backup exists; section 5 (the escape hatch) when the store is already native; otherwise a repair with SQLite's tools (such as the `sqlite3` shell's `.recover`) or your own copy |
| `already a native authority ... Escape hatch` | Activation committed; see section 5 |

A rollback is one transaction, so an interrupted rollback has changed nothing: run it again.
After a successful rollback, a fresh cutover starts cleanly.

```
python -m taskmaster.native.cutover --root <project> --resume [--token <token>]
python -m taskmaster.native.cutover --root <project> --rollback [--token <token>]
```

### Manual restore from a backup

This discards everything written since the backup and returns the project to exactly the
state the backup recorded. For a backup taken with nothing pending, that is the pre-cutover
state. It uses only Python's standard library and the cutover command. Run it with **every
Taskmaster client stopped** (section 1):

1. Save the script below as `restore_backup.py` and run
   `python restore_backup.py <project>`. It takes the latest backup recorded in the cutover
   journal; pass a specific `backups/pre-native-<ts>.db` as a second argument to pick another.
   It refuses unless the store is a legacy authority behind a cutover fence
   (`migration_state = 'migrating'`). It also refuses if another process holds the
   coordinator ownership lock or has the store open for writing. It verifies the backup and
   its projection archive. It copies the current store aside with the SQLite backup API, plus
   every projection file it replaces and any `export-intent.*.json`, into
   `backups/aside-<ts>/`, deleting nothing. Then it puts the backup's store, projection files
   and ID-reservation sidecar in place.
2. Clear the fence the restored store carries. The backup was taken while the fence was up:

   ```
   python -m taskmaster.native.cutover --root <project> --rollback --clear-orphan-fence
   ```

   This keeps every row as restored, drops the journal and native staging, and returns the
   `migration_*` keys to their pre-cutover values.
3. Verify: `python -m taskmaster.native.cutover --root <project> --dry-run` reports
   `"authority":"legacy"` and `"migration_state":"ready"`. Then start one bridge client and
   run `backlog_status`.

`.taskmaster/PROGRESS.md` and `local/PROGRESS.md` are not restored; they are rendered files, not
store state. They may still mention sessions whose writes the restore discarded, until the
next session's changelog rewrites the region. Edit those lines out by hand if they mislead.

The script (`tests/test_native_cutover.py::test_the_documented_manual_restore_returns_the_pre_cutover_state`
runs this exact block):

```python manual-restore
# Manual restore of a Taskmaster store from a pre-native backup. Stop every client first.
import contextlib, datetime, os, pathlib, shutil, sqlite3, sys, zipfile

root = pathlib.Path(sys.argv[1]).resolve()
base = root / ".taskmaster"
local = base / "local"
store = local / "store.db"


def read_only(path):
    return contextlib.closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True))


# 0. Guards: only an unfinished cutover's store, and nothing else holding it.
with read_only(store) as db:
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    authority = db.execute("SELECT value FROM native_manifest WHERE key='authority'").fetchone() \
        if "native_manifest" in tables else None
    fence = db.execute("SELECT value FROM meta WHERE key='migration_state'").fetchone()
if authority and authority[0] == "native":
    sys.exit("refusing: this store is a native authority (activation committed); see section 5, the escape hatch")
if not fence or fence[0] != "migrating":
    sys.exit("refusing: no cutover fence is up (migration_state is not 'migrating'); nothing to undo")
owner_lock = local / "coordinator" / "owner.lock"
if owner_lock.exists():  # Held until this process exits, like the coordinator's own lock.
    held = os.open(owner_lock, os.O_RDWR)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(held, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("refusing: a coordinator or cutover holds the ownership lock; stop it first")
guard = sqlite3.connect(store, isolation_level=None, timeout=0)
try:
    guard.execute("BEGIN IMMEDIATE")  # No other writer may commit while the store is copied aside.
except sqlite3.OperationalError:
    sys.exit("refusing: another connection is writing to the store; stop every Taskmaster client")

# 1. The backup: the argument, else the latest one the cutover journaled.
if len(sys.argv) > 2:
    backup = pathlib.Path(sys.argv[2]).resolve()
else:
    backup = pathlib.Path(guard.execute(
        "SELECT json_extract(detail_json,'$.path') FROM native_cutover_journal "
        "WHERE stage='backup' AND status='done' ORDER BY seq DESC LIMIT 1").fetchone()[0])
archive = backup.with_suffix(".projection.zip")
sidecar = backup.with_name(backup.stem + ".id-reservations.json")

# 2. Verify the backup and its projection archive before touching anything.
with read_only(backup) as db:
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok", f"{backup} fails integrity_check"
with zipfile.ZipFile(archive) as zipped:
    assert zipped.testzip() is None, f"{archive} is damaged"
    names = set(zipped.namelist())

# 3. Copy the current store aside (SQLite backup API), and every file the restore replaces.
aside = local / "backups" / ("aside-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
aside.mkdir(parents=True)
with read_only(store) as source, contextlib.closing(sqlite3.connect(aside / "store.db")) as copy:
    source.backup(copy)
tracked = [row[0] for row in guard.execute("SELECT file FROM projection")]
for rel in sorted(set(tracked) | names):
    if (base / rel).is_file():
        (aside / "files" / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base / rel, aside / "files" / rel)
if (local / "id-reservations.json").exists():
    shutil.copy2(local / "id-reservations.json", aside / "id-reservations.json")
for intent in local.glob("export-intent.*.json"):  # Export intents of the discarded writes.
    (aside / "export-intents").mkdir(exist_ok=True)
    shutil.move(intent, aside / "export-intents" / intent.name)
guard.rollback()
guard.close()

# 4. Files the current store tracks that the backup's archive lacks were written after it.
for rel in tracked:
    if rel not in names and (base / rel).is_file():
        (base / rel).unlink()

# 5. The backup's store replaces the current one (its WAL and index go first).
for suffix in ("-wal", "-shm"):
    (local / ("store.db" + suffix)).unlink(missing_ok=True)
shutil.copyfile(backup, store)

# 6. The backup's projection files and ID-reservation sidecar.
with zipfile.ZipFile(archive) as zipped:
    zipped.extractall(base)
if sidecar.exists():
    shutil.copyfile(sidecar, local / "id-reservations.json")
else:
    (local / "id-reservations.json").unlink(missing_ok=True)
print(f"restored {backup}; the previous store and files are in {aside}")
```

### A fence with no journal

A `migration_state='migrating'` with no `native_cutover_journal` table was not left by a
cutover run that got as far as its first commit. It could come from a future or foreign
migrator, or from a hand edit. `--resume` and a plain `--rollback` both refuse it. Once you
are sure no migration is in progress (step 1 stop list, no other migrator running):

```
python -m taskmaster.native.cutover --root <project> --rollback --clear-orphan-fence
```

This clears the fence under the ownership lock, after the quiesce and integrity checks,
**without comparing the store to a backup**. It keeps every row as it is, restores the
`migration_*` keys to their pre-cutover values when a journal records them (otherwise it
removes them), drops the journal and native staging, and reports the values it removed. If the command is
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
   fresh legacy store. **Use this build or later.** Released builds up
   to and including 6.0.3 treat any body line containing `=======` (a setext heading
   underline such as `=========`) as a Git conflict marker. They quarantine the file, so it is
   never adopted: CodeMaestro's B-339 hit this. This build matches only whole `<<<<<<<`/`>>>>>>>`
   marker lines (commit `6cf42b6`, N13 D2). If you must adopt with 6.0.3 or earlier,
   `backlog_store_status` lists the quarantined files. Recover each one by editing the
   offending line (for example `---` instead of `=========`), then run any tool so it is
   imported. `backlog_resolve_conflict` does not apply: it resolves flagged files, not
   quarantined ones. Other quarantine causes, such as a handover with no frontmatter, are
   repaired as in [Repairing quarantined files before the cutover](#repairing-quarantined-files-before-the-cutover).
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
CodeMaestro passed with this build (N15 report, *escape*): 3,711 of 3,711 authored documents
re-adopted. With 6.0.3 it re-quarantined B-339, as step 5 describes.

## Pruning backups

Every backup stage writes a `pre-native-<ts>.db` with its `.json`, `.projection.zip` and
`.id-reservations.json`. Each drift absorption takes another backup, so a cutover that absorbed
writes leaves several. Every manual restore leaves an `aside-<ts>/` folder. None of them are
removed automatically. Once the cutover has activated and you have verified the native store
(section 3, "Verify"), they are no longer needed for recovery: after activation the only
recovery is the escape hatch, which uses the projection files. With every client stopped, keep
the newest `pre-native-*` set as an archive and delete the rest. Backups taken within the
same second are named `<ts>.db`, `<ts>-1.db`, `<ts>-2.db` and so on, so the command orders them
by timestamp and then by that number, never by name:

```
python -c "import pathlib,re,sys; d=pathlib.Path(sys.argv[1])/'.taskmaster'/'local'/'backups'; key=lambda p: (lambda m: (m[1], int(m[2] or 0)))(re.fullmatch(r'pre-native-(\d{8}T\d{6}Z)(?:-(\d+))?', p.stem)); s=sorted(d.glob('pre-native-*.db'), key=key); [f.unlink() for db in s[:-1] for f in d.glob(db.stem+'.*')]" <project>
```

Delete `aside-*` folders once you no longer need what they hold.

## Files

- The store: `.taskmaster/local/store.db`. The fence is in `meta`, and the journal is in
  `native_cutover_journal`.
- Backups: `.taskmaster/local/backups/pre-native-<UTC ts>.db`, with `.json` (the manifest),
  `.projection.zip` (the archived projection files) and `.id-reservations.json`. A manual
  restore copies what it replaces into `backups/aside-<UTC ts>/`.
- Code: `taskmaster/native/cutover.py`. Tests: `tests/test_native_cutover.py`,
  `tests/test_native_cutover_crash.py` and `tests/test_native_cutover_quarantine.py`. Every twins activation in the test suite runs the
  carry-over oracle by default (`TASKMASTER_TWINS_VERIFY=1`, set in `tests/conftest.py`, +2.3%
  suite time); set it to `0` to opt out.
