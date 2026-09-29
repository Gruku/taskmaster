<!-- User intent: one guide that lets an operator migrate a project to the native store safely and
     run it afterwards, and lets agents using the Taskmaster MCP tools read receipts, sync and Git
     obligations, context/delta tools and claims correctly. Links to the cutover runbook rather
     than repeating it. -->

# The native store

This release adds a second storage mode, the **native authority**. A project uses it only after
an operator runs the cutover described in the [native cutover runbook](runbooks/native-cutover.md).
Every existing project stays on the **legacy** store (the 6.0.x SQLite store) until then, and
this build keeps serving legacy stores as before. Nothing migrates a project automatically.

> **Rehearse on a copy first.** Run the cutover on a disposable copy of the project before the
> real one, and keep that copy until the real cutover is verified. After activation the only way
> back is the manual escape hatch.

In short, on a native store:

- **One process writes.** A repository coordinator owns the database. MCP servers, the viewer
  and the CLIs are its clients and start it on demand.
- **Files follow the database.** A write commits to the database first; the Markdown and YAML
  files under `.taskmaster/` are exported afterwards, in the background.
- **Hand edits are imported only at an explicit sync.** Ordinary tool calls do not scan the
  files.
- **Git is either managed or treated as drift.** Commits and checkouts of `.taskmaster/` go
  through the managed Git command. Git operations that bypass it are detected and held, never
  imported silently.

Sections: [receipts](#receipts) · [sync](#sync) · [Git](#git) · [migration](#migration) ·
[service recovery](#service-recovery) · [context, deltas and claims](#context-deltas-and-claims) ·
[compatibility](#compatibility) · [known limitations](#known-limitations) ·
[reference](#reference).

## Receipts

A mutating tool's reply carries two separate facts: whether the database committed (the
**commit receipt**) and whether the files have caught up (the **projection receipt**). They can
disagree, and the reply says so.

| You see | Meaning | What to do |
|---|---|---|
| `... [seq N]` at the end of a text reply, or `"seq": N` in a JSON reply | The write committed. `N` is its sequence in the store's history | Nothing. It is durable |
| `(export pending: ...)` before the `[seq N]`, or an `"export_pending": [...]` list in JSON | The write committed, but the file export has not landed yet | Nothing, for the write. The export is retried. Check the file only if you need it on disk now |
| `No change to <task> field <field> — already <value>` (no `[seq]`) | The stored value already equalled what you asked for. Nothing was written | Nothing. It is not a failure |
| `unchanged (already <value>)` inside a longer reply | The same, for one field of a composite reply | Nothing |
| `Error: coordinator disconnected; ... request_id=..., caller_scope=...` (JSON tools: `{"error": ..., "may_have_committed": true, ...}`) | The connection to the coordinator was lost after the command was handed over, so it may have committed | Read the entity before retrying (see below) |

**Export-pending wordings on native.** The plain case is
`export pending: committed through sequence N; background export queued`: the background exporter
has not run yet. Others name a reason, such as `export pending: <file> is flagged`,
`export pending: <file> is quarantined`, `export pending: publisher busy`,
`export pending: coordinator stopping` and `export pending: <file> refused (<reason>)`.
`local/PROGRESS.md` uses `export pending: local/PROGRESS.md — retried on next call`. The
wordings come from `taskmaster/coordinator/adapter.py`, `taskmaster/coordinator/service.py`
and `taskmaster/native/projection.py`.

**No-op replies are grounded in the transaction.** The value in a `No change ... already ...`
reply is the value the command itself found stored, read inside its transaction (the receipt's
`unchanged` list). A peer's later write cannot change what the reply claims. The two no-op
wordings are emitted by `backlog_update_task` (`taskmaster/native_routing/tasks.py`). On a legacy
store `backlog_update_task` always writes and always answers `Updated ... [seq N]`.

**An identical value can still commit.** Every task update also refreshes `last_referenced`,
which has minute precision. An update that repeats the current value in the same minute as the
last one is a no-op; the same update after the minute has rolled over commits the new
`last_referenced` and answers `Updated ... [seq N]`. Both answers are true. See
[known limitations](#known-limitations).

**Retrying after a lost reply.** Each MCP tool call gets a fresh request id, so calling the same
tool again is a new command, not a replay of the first. Before retrying a write that answered
`may_have_committed: true`, read the entity (`backlog_get_task`, or `backlog_changes_since`
with a cursor taken earlier) to see whether it landed. Creation tools in particular would
create a second entity. The coordinator keeps a receipt per request id, and the error names
it (`request_id=..., caller_scope=...`), so an operator can look it up with the Python client
(`Client(root).receipt(caller_scope, request_id)`, `taskmaster/coordinator/client.py`).
`backlog_linear(action="retry")` is the one MCP tool that accepts a `request_id` to replay.

## Sync

### When hand edits are imported

On a native store, ordinary tool calls read and write the database only. They never scan
`.taskmaster/` for edits (design decision D06). A hand edit to a projection file becomes store
state only at one of these explicit sync points:

| Sync point | What it imports |
|---|---|
| `backlog_handover_resync()` | Every handover file |
| `backlog_issue_resync()` | Every issue file |
| `backlog_resolve_conflict(file=<path>, take="file")` | That one file, if it is flagged or quarantined (the tool refuses any other file) |
| A managed Git `commit` or `checkout` | Every projection file (a full sync runs first) |
| A full sync through the Python client (below) | Every projection file |

Until then the store keeps its own version. If the exporter later needs to write that file and
finds it changed on disk, it does not overwrite it: it flags the file, keeps both versions and
pauses exports to it until you choose one with `backlog_resolve_conflict`.

**For agents:** change backlog state through the tools, not by editing files. If you did edit a
handover or issue file by hand, run the matching resync. For any other file, tell the user a
sync is needed; there is no general sync MCP tool.

### Running a full sync

There is no dedicated sync CLI. From the project directory, using the environment Taskmaster
runs in (for a source checkout, `uv run --project <taskmaster checkout>`):

```
uv run --project <taskmaster> python -c "import json, sys; from taskmaster.coordinator.client import Client; print(json.dumps(Client('.').sync(request_id=sys.argv[1]), indent=1, default=str))" my-sync-1
```

Pick your own request id and keep it: it is the sync's identity (with the caller scope,
`explicit-sync` by default).

The reply's `state` is `synchronized` or `pending`:

- `imports` lists the domain writes the sync made (edited files imported).
- `observed` counts files whose published bytes were only recorded as a merge base. That changes
  no task data.
- `observes` lists observes whose outcome the budget left unsettled.
- `notices` explains every file left pending, such as a quarantined or flagged file, drift, or
  the budget running out.

### Time budget and "retry the same sync id"

A sync has a budget of 120 s by default (`SYNC_TIMEOUT`, `taskmaster/coordinator/protocol.py`).
Callers of the Python client can pass `timeout=` from 1 to 3,600 s. When the budget runs out,
the sync stops taking new files and answers `pending` with notices such as
`time budget exhausted or coordinator stopping; retry the same sync id`. A write the budget
interrupted is reported as one of three outcomes:

- `committed`: its receipt exists.
- `not_committed`: it was cancelled before the writer ran it.
- `uncertain`: the writer is still running it, or it was interrupted and left no receipt.

**Retrying under the same sync id is always safe.** Files it already imported are found imported
and reported unchanged, and the rest continue. Retrying under a new id is also safe, but loses
the link to the first attempt's receipts. To look up a sync without re-running it, use
`Client(root).sync_status(caller_scope, request_id)`.

The target for a reply is the budget plus about 5 s. It is a target, not a bound: looking up an
interrupted write's outcome, and the final publication and completion check, are not budgeted
(see the runbook's *Verify* section).

### Batched full sync

A full sync has no project-size limit. It is one operation (one begin, one Git classification,
one writer pause, one publication and one completion check) that reads its files in batches of
10,000 paths (`batch_size()` in `taskmaster/coordinator/sync_worker.py`). Judgements about the
whole set, such as Git drift, still see every path. The sync fingerprint cache is saved after
each batch, so an interrupted sync that is retried re-reads only what changed.

Measured on synthetic copies with the cache seeded by the cutover: 3,700 files in 15.2 s cold
and 2.6 s with no edits; 37,010 files in 25–34 s. The steady no-edit sync on a
CodeMaestro-sized project takes about 3 s.

**The sync fingerprint cache** (`local/cache/sync-fingerprints.json`) lets a sync skip a file
whose volume, file id, size, modification time and change time are all unchanged, for up to an
hour. On Windows the change time is NTFS ChangeTime, so a rewrite that restores the
modification time is still seen. A write through a memory mapping moves no timestamp, so a sync
can miss it until the cache entry ages out. A named sync (`files=[...]`) and `take="file"` always
read the bytes, and managed Git never uses the cache.

### Linked worktrees

Every linked worktree of a checkout shares the main checkout's store. The main checkout's files
are published continuously. A linked worktree receives them only when it is synced or when a
managed Git operation runs in it: `Client(root).sync(worktree=<path>)`, or `git_cli` run from
inside the worktree. At most 32 linked worktrees are tracked.

## Git

### Managed Git

On a native store, commit and check out `.taskmaster/` with the managed Git command. It runs from
the project (or a linked worktree) directory:

```
uv run --project <taskmaster> python -m taskmaster.coordinator.git_cli commit -m "chore: log session"
uv run --project <taskmaster> python -m taskmaster.coordinator.git_cli checkout <ref>
uv run --project <taskmaster> python -m taskmaster.coordinator.git_cli status
uv run --project <taskmaster> python -m taskmaster.coordinator.git_cli recover [--acknowledge-quiescent] [--accept-outcome] [--release-drift {import,take-published} [--worktree W]]
```

What a managed operation does (`taskmaster/coordinator/git.py`):

1. It refuses if Git holds a lock (`index.lock`, `HEAD.lock` or the branch's ref lock):
   `Git lock present; another Git process may be running`.
2. It runs a full sync. If the files are not synchronized (a quarantined, flagged or pending
   file), it refuses with `projections are not synchronized; resolve the listed paths first`.
3. It reads every projection file in full and compares it with the published generation
   (`generation()`). A mismatch refuses with `projection files differ from the published generation`.
4. **commit** stages and commits exactly the projection generation (plus tracked projection files
   it no longer contains, as deletions) with `git commit --only`. Anything else you have staged
   stays staged and out of the commit. The commit gets a `Taskmaster-Op: <id>` trailer.
   **checkout** checks the ref out and imports what it brought.
5. It verifies the result and records it, so a retry with the same `--request-id` replays the
   result instead of running Git again.

Commit code changes with plain Git as usual, leaving `.taskmaster/` out of them. The managed
commit is the only supported way to commit `.taskmaster/` on a native store. The first managed
commit after a cutover typically carries a regenerated `backlog.yaml` (see the runbook).

`git_cli` prints the request id to stderr first. If its reply is lost, re-run the same command
with `--request-id <id>`; Git is not run twice. It exits 0 only for `completed`, `clear` or `accepted`.

**Optional pre-commit check.** Taskmaster never installs hooks. You can call its validator from
your own pre-commit hook: `python -m taskmaster.coordinator.git_hook pre-commit`. It refuses a
commit that stages `.taskmaster/` files outside a managed operation, and says how to commit them
or unstage them (`git restore --staged .taskmaster`).

### Bypassed Git is drift

A `git checkout`, `git switch`, `git reset`, `git merge` or `git rebase` run directly moves
projection files without the coordinator. The next full sync or managed operation compares HEAD
and the reflog with what it last observed:

- **Plain commits** of authored edits are imported as ordinary edits. If their projection files
  are not the published ones, a warning says the commit may mix generations.
- **Files Git itself changed** (by a checkout, reset or rewrite) are **drift**. They are neither
  imported nor overwritten, and the sync reports them as pending with this guidance:
  `managed checkout drift: the checked-out file differs from the published generation and is not
  imported or overwritten; restore the published file (e.g. check the previous branch out again),
  adopt it with sync take_file, or run git recover --release-drift import (sync imports it) or
  take-published (it receives the published file; its bytes are retained)`.

Drift holds a managed commit until it is resolved (a managed checkout may proceed past drift
that Git itself left). To resolve it, choose one of these:

- restore the published file, for example by checking the previous branch out again;
- import that file as it is: `Client('.').sync(files=['<path under .taskmaster>'], take_file=True)`
  with the Python client ([Running a full sync](#running-a-full-sync)). `backlog_resolve_conflict`
  does not apply: it takes only flagged or quarantined files;
- `git_cli recover --release-drift import` to let the next sync import the held bytes;
- `git_cli recover --release-drift take-published` to keep the held bytes on record and write
  the published file.

The detection is heuristic where Git's own records are. An authored edit that happens to equal
the HEAD blob is held as drift, and with the reflog disabled or expired, every Git-changed path
is drift.

### Interrupted managed operations

A managed operation that was interrupted leaves a pin: the exporter, `flush`, sync and Linear
bootstrap wait until it is resolved. `git_cli status` shows it. On restart, the coordinator
retires the recorded Git process and reconciles the outcome from HEAD, the ref, the index and the
locks.

- If the outcome can be proven, the pin clears by itself.
- If it cannot, the operation stays `ambiguous` or pinned. Inspect the repository, then run
  `git_cli recover --acknowledge-quiescent` (no Git or hook process of it is left) and, for an
  ambiguous state you have inspected, `--accept-outcome`.
- On POSIX there is no process-containment boundary: an interrupted operation always needs
  `--acknowledge-quiescent`.

**`.git/index.lock`.** Managed Git never deletes a Git lock. After an interrupted commit whose HEAD
and branch did not move, a leftover `index.lock` is reported as
`stale .git/index.lock left by the retired Git process; remove it after inspection`. Check that
no Git process is running, delete the file, and retry. Any other leftover lock makes the outcome
`ambiguous` (`Git locks remain after quiescence: ...`).

## Migration

The [cutover runbook](runbooks/native-cutover.md) is the procedure. What it requires, in brief:

1. **Stop every client** on the launcher stop list: MCP servers, the viewer, hooks, maintenance
   scripts. Taskmaster 6.0.2 and older cannot read the migration fence, so stopping them is what
   protects the store.
2. **Run one tool call just before stopping the last client.** Its scan adopts recent changes,
   such as a `git pull`, so the dry run can name any file that no longer parses. From then until
   the cutover ends, do not pull, check out or edit files in the project.
3. **Dry run:**
   `python -m taskmaster.native.cutover --root <project> --dry-run`. Deal with every `refused:` line.
4. **Quarantine preflight.** The cutover refuses while any projection file is quarantined or
   flagged, because native managed Git would refuse in that state afterwards. It names each
   file with its reason. Repair each one in place, keeping its content and its own line endings,
   and re-adopt it with a bridge client. A file quarantined by an older build's rule clears
   only after its modification time changes (touch it). Re-adopting a repaired file changes
   `backlog.yaml`. See
   [Repairing quarantined files](runbooks/native-cutover.md#repairing-quarantined-files-before-the-cutover).
5. **`--confirm-stopped`** is for process matches you have checked by hand. On Windows the
   process scan cannot see a process's working directory, so a Taskmaster server running for
   another project is listed too. The flag turns the finding into a warning; it does not
   re-check.
6. **Cut over:** `python -m taskmaster.native.cutover --root <project>`. Write down the token.
7. **Verify** as the runbook describes, then commit `.taskmaster/` with the first managed commit.

**Rollback limits.**

- Before activation commits, `--rollback` clears the fence and native staging. It restores
  nothing, so it cannot lose a write. It refuses if the store changed since the cutover last
  recorded it, and offers `--resume`, `--rollback --clear-orphan-fence` or a manual restore.
  Before the first backup it compares database rows only, not files.
- After activation, `--rollback` refuses: the only direction is forward. You can run only
  native-capable builds against the store. The manual escape hatch (runbook section 5)
  re-adopts the projection files into a fresh legacy store. It keeps every authored document
  and loses database-only state: receipts, sessions, queue leases, Linear queue state and the
  history sequence.

**Cost.** On a CodeMaestro copy (about 3,700 entities) the cutover took about 31 s and the first
sync after it 4.4–4.65 s. Legacy adoption of a very large legacy project is the slow part (about
87 min at 10× CodeMaestro's size); see [known limitations](#known-limitations).

## Service recovery

### The coordinator

The first client that needs to write starts the coordinator: `python -m
taskmaster.coordinator.service --root <project>`, a detached child process (windowless on Windows). Reads do not need
it. Its files are in `.taskmaster/local/coordinator/`:

| File | Purpose |
|---|---|
| `owner.lock` | The ownership lock, an OS byte lock. The OS releases it when the process exits, including a crash |
| `discovery.json` | Port, token and identity. Clients check that it is private (owner-only) and matches the store's root, schema and protocol |
| `service.log` | Rotating log (1 MB, 2 backups). Read it first when the coordinator will not start |

A coordinator exits after 300 s with no pending work, no sync and no managed Git operation
(`TASKMASTER_SERVICE_IDLE_SECONDS` overrides this). The next write starts a new one. A cold start
takes about 6 s, and a first write from a fresh process about 8 s (measured; process spawn
included).

**After an upgrade.** <!-- TODO(N17): replace with the final behaviour of the coordinator
build-version handshake (a parallel change): after an upgrade, an old idle coordinator retires. -->
A build-version check in the coordinator handshake is being added so that, after an upgrade,
an old idle coordinator retires. Until that behaviour is final, stop the old coordinator
yourself after upgrading (below).

To stop it cleanly, for example before a cutover step or an upgrade:

```
uv run --project <taskmaster> python -c "from taskmaster.coordinator.client import Client; print(Client('.', autostart=False).shutdown())"
```

**There is no writer fallback.** If the coordinator cannot be reached, writes fail; they never
fall back to a second writer. The errors are:

| Error | Meaning | Action |
|---|---|---|
| `coordinator startup unavailable; inspect .taskmaster/local/coordinator/service.log; no writer fallback` | The coordinator did not come up within about 15 s | Read `service.log` |
| `repository coordinator is running but not responding; retry later` | The lock is held but the process does not answer | Wait and retry. If it persists, stop that process |
| `discovery root/store/schema/protocol mismatch; no writer fallback` | A coordinator from a different build owns the project | Stop it (it also exits when idle), and run one build |
| `coordinator IPC capacity reached; retry later` | More concurrent requests than the coordinator admits | Retry |
| `coordinator disconnected; retry the same request_id to recover its receipt` | The connection dropped after the command was sent. It may have committed (`may_have_committed`) | See [Receipts](#receipts) |

### Stale locks and leases

| Lock | How it clears |
|---|---|
| `owner.lock` | Released when the owning process exits. It cannot outlive a dead process; a "stuck" lock is a live but hung process. Stop that process |
| Exporter lease | Held in the store for 30 s at a time and renewed while exporting. A dead holder's lease expires, and the next exporter takes over and re-queues every job the old one had claimed. `backlog_store_status` shows it on its `Exporter lease` line. Nothing to do by hand |
| Task claim | See [claims](#claims). `backlog_claim(action="release", task_id=...)` frees an expired one; `backlog_pick_task(task_id, force=True)` takes one over |
| Managed Git pin | See [interrupted managed operations](#interrupted-managed-operations) |
| `.git/index.lock` | Reported, never removed. See the same section |

Liveness checks use pids. A reused pid reads as running, so the lease expiry or claim TTL is
what recovery relies on.

### `backlog_store_status` on native

It reports the store path, root and how it was resolved, schema version, database and WAL size,
the highest sequence, and these lines: `Dirty`, `Quarantined`, `Stuck exports` (including
projection jobs still `pending`, `claimed` or `conflict`), `Flagged`, `Exporter lease` (native
only), `Corrupt`, `Linear queue`, live sessions and the last 20 changes. It does not report the
coordinator itself. `Merge conflicts (24 h)` and `Read-scan skips` always read 0 on a native
store: native has no read-side scan and does not count merges.

### Escape hatch

If a native project has to go back to legacy after activation, follow
[runbook section 5](runbooks/native-cutover.md#5-post-activation-escape-hatch-manual-lossy-for-local-state).
It is manual and loses database-only state, as described under [Migration](#migration). No
environment variable or flag runs a native store without the coordinator.

## Context, deltas and claims

These tools work on both stores and share one presentation layer, so their answers have the same
shape on legacy and native.

### `backlog_context`

`backlog_context(focus="", scope="session", budget_bytes=8000, include=None, cursor="")` answers
"what do I need to know before acting" in one call.

- `scope` is `task`, `session` or `project`. The default sections are: `task` → dependencies,
  handovers, bugs, links, body; `session` → dependencies, handovers, bugs, notes, recent;
  `project` → handovers, issues, notes, recent. `include` replaces them. Valid names: `spec`,
  `plan`, `body`, `links`, `handovers`, `bugs`, `issues`, `notes`, `dependencies`, `siblings`,
  `recent`. `include=[]` asks for the blockers only.
- `mandatory` is never trimmed. `mandatory.clear` is true only when `mandatory.blockers` is
  empty. Blocker kinds are `gate`, `dependency`, `bug`, `handover`, `human_action`, `claim` and
  `unknown`. **Treat `unknown` as blocked**: it means a check could not answer.
- Every open bug whose `found_in` names the task blocks, whatever its severity. That is the same
  rule `backlog_complete_task` refuses on, so a clear context means the close will not refuse on
  a bug.
- `selected` is trimmed to `budget_bytes` (1 to 1,048,576). `budget.omitted` says exactly what
  was left out. If even the blockers exceed the budget, `budget.over_budget` is true and
  `selected` is empty.
- `cursor` continues a trimmed answer. If the store or the question has moved on, the cursor is
  refused (`... ask again without it`); ask again without it.

### `backlog_changes_since`

`backlog_changes_since(cursor="", kinds=None, ids=None, epic="", limit=100, group_commits=True,
since_seq=None)` answers "what moved since I last looked".

- Call it with no cursor to get a cursor for "now". Keep it, and pass it back later with the
  same filters.
- The answer names the entities and the **field names** that changed, never their values.
  Re-read what you care about.
- On native, changes are grouped by commit (`commits[]`). On legacy there are no commit rows, so
  each change is its own commit.
- `limit` (1–500) pages the answer. It is not part of the cursor's scope, but `kinds`, `ids`,
  `epic` and `group_commits` are.
- **`resync_required: true` is not an error and never means "nothing changed".** It comes with
  a `reason` (`cursor_unreadable`, `store_rebuilt`, `history_rewound`, `scope_changed` or
  `history_expired`) and a fresh cursor. Re-read what you care about, then continue from the new
  cursor. A cursor never crosses from a legacy store to a native one: after a cutover, every old
  cursor answers `resync_required`.

### Claims

A claim says which session is working on a task. `backlog_pick_task(task_id, force=False,
ttl_seconds=0)` takes it; `backlog_claim(action="renew"|"release"|"status", task_id,
ttl_seconds)` renews, releases or lists it.

- The default lease is 4 hours (`ttl_seconds=0`). Allowed values are 60 s to 7 days. Renewing
  sets the expiry to now plus the TTL.
- The holder is the session (`<host>-<pid>-<nonce>`). **Only the claim tools write
  `locked_by`**; `backlog_update_task(field="locked_by")` and the batch equivalent refuse.
- Moving a task to `done` or `archived` always releases its claim.
- A claim expires early only when its holder is proven dead (same host, process gone). A holder
  that cannot be checked is treated as live until the TTL runs out.
- `backlog_pick_task` and `backlog_next_available` never hand out a task another session holds,
  **even if that claim has expired**. The refusal says when it has expired and that
  `backlog_claim(action="release", task_id=...)` frees it without force. `backlog_context` reports
  an expired peer claim as clear, so the two can differ; the pick is the authority.
- A task in a bundle renews and releases with the rest of its bundle.
- After a pause or a compaction, renew your claim (`backlog_claim(action="renew", task_id=...)`).

### Documents

`backlog_document(kind, entity_id, sections=None, provenance=False)` reads an entity's prose,
whole or by section. `backlog_document_import(kind="task", entity_id, sections=None)` imports a
task's declared external documents into the store (1 MiB each, inside the project only). It works
only on a native store; on legacy it refuses, because the files are read directly there.

## Compatibility

| Client | Store | Result |
|---|---|---|
| This build | Legacy | Runs as a 6.0.3-compatible bridge client. New tools work; `backlog_document_import` and `backlog_batch_update(commands=...)` refuse ("native-authority stores only"). Nothing is migrated |
| This build | Native | Routes every tool through the native core and the coordinator |
| 6.0.3 (bridge) | Native | Refuses cleanly: `Unsupported Taskmaster schema_version=2; this client supports 1. Upgrade the client; the database has not been rebuilt.` |
| 6.0.2 or older (pre-bridge) | Native | **Not refused cleanly**: 6.0.2 fails with `IntegrityError` and leaves `store.recovery.lock` behind (N15 rehearsal). Stop every such process before the cutover and never start one afterwards |
| Any bridge client | During a cutover | Refuses: `Taskmaster migration state is 'migrating'; access refused until migration completes.` |
| A different native build | Native | Must match the store's schema (2) and protocol (2), else `Unsupported native authority schema or protocol`. A running coordinator from another build refuses clients with a protocol mismatch (above) |

**Run one build per project.** Upgrade every host (Claude plugin cache, Codex plugin, manual
servers) together, and stop the old coordinator, before using a native store.

**Hooks of an older version fail open.** An older build's hooks refuse a native store the same
way its server does, and the merge-gate hook allows the operation whenever it cannot decide
(`hooks/merge_gate.py`). In a session still running old hooks, merge-gate enforcement is
therefore off. Restart sessions after upgrading.

**Tools that refuse on a native store** (`taskmaster/native_routing/registry.py`), each with
guidance and `Nothing was changed`: `backlog_init` (already initialized), `backlog_migrate_v3`
and `backlog_migrate_v4` (no migration needed), `backlog_canonicalize_layout` (already canonical),
`backlog_backfill_lanes` (set lanes per task instead), and `backlog_link(action="reconcile")`
(use `backlog_link(action="validate")`). Every other tool, including `backlog_index_status`,
the handover and issue resyncs and `backlog_linear` bootstrap and retry, has a native
implementation.

**Tool changes that apply to both stores**: five new tools (`backlog_context`,
`backlog_changes_since`, `backlog_document`, `backlog_document_import`, `backlog_claim`) and
three extended ones (`backlog_get_task(provenance=)`, `backlog_pick_task(ttl_seconds=)`,
`backlog_batch_update(commands=, expected_revisions=, atomic=)`). No tool was renamed or
removed.

## Known limitations

Measured on one Windows 11 machine unless stated. None of the timings includes model or network
latency.

**Expected, by design:**

- **Write latency under contention.** Writes meet the 250 ms p95 budget at 1 client and mostly at
  4. At 8–12 concurrent clients they miss it (up to about 1.6 s p95 in the worst runs), because
  every write queues behind one writer that syncs to disk (`synchronous=FULL`). Durability was not
  relaxed to buy latency. The database core's own p95 stays at 2–5 ms. Tail figures at 8 or more
  clients varied up to 2× between repeat runs.
- **Managed Git reads every file.** `generation()` does a full read on every managed commit or
  checkout (6.9–7.6 s on a CodeMaestro copy of 3,708 files), because a memory-mapped write moves no
  timestamp and no fingerprint can vouch for it.
- **Hand edits wait for a sync.** See [Sync](#sync).
- **Cold start.** About 6 s to start a coordinator and about 8 s to a first write from a fresh
  process.
- **No rollback after activation**, and the escape hatch loses database-only state.
- **Retries of MCP tool calls are not deduplicated.** See [Receipts](#receipts).
- **The Windows process scan lists other projects' servers.** Use `--confirm-stopped` after
  checking.
- **Interrupted managed Git on POSIX** always needs an operator's `--acknowledge-quiescent`; a
  reboot is not detected on any platform.
- **Git locks are never removed automatically.**
- **Before the cutover's first backup, `--rollback` compares rows only**, not files. It restores
  nothing, so no write is lost.
- **Identical-value updates** commit or not depending on the wall-clock minute (see
  [Receipts](#receipts)).

**Open follow-ups:**

- A racing `backlog_link` create can answer `linked` with no `[seq N]` and no no-op marker. The
  reply's "already present" check reads before the command runs, so when another session creates
  the same link in between, the command is a no-op but the reply does not say so. The link is
  there either way.
- Every full sync repeats `sync.apply` events for files already in conflict.
- Linked-worktree sync loads all of the worktree's published bytes at once; it is not batched
  like the main checkout's full sync.
- The dashboard's full board read on a CodeMaestro-sized project is 164 ms p95, over its 100 ms
  budget.
- A cold first sync or managed commit on a freshly copied or cloned project took 40–88 s in the
  acceptance harness. The unverified hypothesis is that a copy changes every file's change time,
  so the fingerprint cache misses on every file. Whether a cold 37,010-file sync fits the 120 s
  budget is not measured.
- Legacy adoption of very large projects is slow and grows roughly quadratically: 58 s at
  CodeMaestro's size, 551 s at 3×, about 87 min at 10×. The cutover depends on it; there is no
  direct native import.
- The 10× acceptance run was not done: 10× read and write latencies, and the check that answer
  time grows with the required output rather than unrelated data, are unmeasured.
- Task files with no epic or status (four exist in CodeMaestro) are unreachable by every tool,
  on both stores.
- A legacy `backlog_link create` was seen not persisting on a CodeMaestro copy (N14, D1), and the
  canonical `target_kind` fallback is still open.
- Taskmaster 6.0.2 and older fail uncleanly on a native store (see [Compatibility](#compatibility)).
- The Codex manifest sets `tool_timeout_sec: 30`, below the 120 s sync budget. A resync on a large
  project can outlast the host's tool timeout even though the sync itself continues.

## Reference

**Environment variables**

| Variable | Effect |
|---|---|
| `TASKMASTER_ROOT` | Overrides root resolution (else the Git common directory, else the nearest ancestor with `.taskmaster/`) |
| `TASKMASTER_SERVICE_IDLE_SECONDS` | Coordinator idle timeout (default 300) |
| `TASKMASTER_METRICS` | Opt-in metrics: `1` in memory, or an absolute `.jsonl` path (one file per process) |

`TASKMASTER_MANAGED_GIT` is set internally for a managed Git child so the pre-commit validator
can recognize it. Do not set it yourself.

**Files**

| Path | What it is |
|---|---|
| `.taskmaster/local/store.db` | The store |
| `.taskmaster/local/coordinator/` | `owner.lock`, `discovery.json`, `service.log` |
| `.taskmaster/local/cache/sync-fingerprints.json` | Sync fingerprint cache (one hour) |
| `.taskmaster/local/backups/pre-native-*` | Cutover backups (see the runbook's *Pruning backups*) |
| `.taskmaster/local/PROGRESS.md` | Rendered progress log; machine-local, not committed |

**Deeper material:** [design](specs/2026-09-09-database-native-design.md),
[compatibility inventory](specs/2026-09-09-native-compatibility.md),
[implementation plan and ledger](plans/2026-09-09-database-native.md), and the N09–N16 reports in
[`docs/reports/`](reports/).
