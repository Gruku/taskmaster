<!-- User intent: evidence for the B-083..B-087 cluster — what each defect
     actually was (which is not always what the record said), the fix, the test
     that pins it, and measured numbers rather than claimed ones. -->

# Store bug cluster B-083 … B-087

Branch `fix/store-bugs-b083-b087`, based on `feat/database-native-foundation` at `9bf22a8`.
These are the five defects filed in the 2026-09-07 B-082 read-hang audit and deliberately
left for the next release. They ship on the native branch line, not as a standalone 6.0.3.

All five fixes are in `taskmaster/store.py` and `taskmaster/backlog_server.py`.
`taskmaster/native/` is untouched. No schema version bump, and no new column: nothing here
needed one, so nothing here makes a 6.0.x process drop and rebuild every table.

Tests: `tests/test_store_bug_cluster.py` (14 cases). Each bug's test landed as its own
commit before its fix, and failed at that commit.

**Full suite: 2,523 passed, 1 skipped, 0 failed** (1,023 s, single process, `python -m
pytest tests/ -q`). That reconciles exactly against the 2,509 passed / 1 skipped baseline
measured on `feat/database-native-foundation` at `9bf22a8`: 2,509 + 14 new cases = 2,523,
and the one skip is the live Linear smoke test in both.

Three full-suite runs were needed, and the first two each had one failure:

1. **Run 1 — `test_mixed_public_tool_operations_across_processes_never_lose_a_write`.** A
   real regression from B-083's progress sink, diagnosed and fixed in `3967324` (see B-083
   below). Not flakiness.
2. **Run 2 — `test_two_processes_compat_and_direct_store_transactions_both_survive`.** On-disk
   FTS5 corruption (`malformed inverted index for FTS5 table main.entity_fts`) in the test's
   own temporary store, whose recovery then failed on Windows because the peer process still
   had the file open (`WinError 32` renaming `store.db` aside). Ran 10 times in isolation
   afterwards: 10 passed. Nothing in this cluster touches `entity_fts`, the derived rebuild,
   or the recovery rename, and the test passed in runs 1 and 3 and in a dedicated
   `test_store_concurrency.py` run (10 passed, 442 s). I could not reproduce it and cannot
   attribute it; it is worth pursuing independently. Recording it here rather than calling
   the branch green without mentioning it.

   One part of it *is* attributable and is not about this cluster: **corruption recovery has
   no working path on Windows while a peer process still holds the database open.**
   `_recover_corrupt_database` tried `_rename_database_family("corrupt")`, which failed with
   `WinError 32` (`os.replace` on an open file), then fell back to
   `_backup_database_family("corrupt")`, whose `shutil.copy2` failed with `WinError 33`
   (another process has locked a portion of the file). Both attempts raised, so the tool call
   died with a `PermissionError` rather than with the corruption diagnosis, and the store was
   left as it was. That sequence is in `origin/master` (6.0.2) unchanged. The FTS5 corruption
   that triggered it is a separate, unexplained question.
3. **Run 3 — clean.**

Caveat worth stating plainly: two of three full-suite runs had a concurrency failure, so
"green" here means one clean run plus a diagnosed-and-fixed cause for one of the two
failures and an unexplained cause for the other.

| Commit | What |
|---|---|
| `74d94bb` | test: B-086 |
| `a1d423d` | fix: B-086 |
| `e8ffc6a` | test: B-085 |
| `d446a74` | fix: B-085 |
| `ea22555` | test: B-083, B-084 |
| `281fc15` | test: B-087 |
| `4235c7e` | fix: B-083, B-084 |
| `7d16a26` | fix: B-087 |
| `3967324` | fix: the B-083 progress sink could stall the writer it described |

---

## B-086 — a read-side scan skipped because the store was busy (P3)

**What it actually was.** `_maybe_scan_on_read` set the 2 s throttle clock *on the way in*,
before it knew whether the scan would run. When the writer mutex was unavailable it caught
the busy `RuntimeError` and returned, having already spent the window. On a store that is
busy whenever a read lands — ten sessions on a 2,050-task backlog is exactly that — the
retry never came round while the store was free, so hand edits were never adopted, and the
result was indistinguishable from "nothing changed". Nothing counted the skips and nothing
reported them.

**The fix.** The clock moves only when an attempt completes (including one that proves
there is nothing to import). A skipped attempt sets `_read_scan_pending`, and the next read
goes straight back to the non-blocking mutex attempt rather than re-walking the projection
to rediscover work it already found — so the retry costs a lock probe, not a sweep. That is
the part that makes "do not burn the throttle" affordable: retrying the *sweep* on every
read of a busy store would have re-introduced the cost B-082 removed.

Consecutive skips are counted on `Store.read_scan_skips`, warn (`RuntimeWarning`) every
fifth time, and are reported by `backlog_store_status` as `Read-scan skips: N  (store busy:
hand edits are not being adopted)`. The count is in-memory per process — it cannot be read
out of the database, so a report built by a process that never opened this store honestly
reads 0. `read_only_status` stays a pure read: the new field comes from the `Store` object,
not from a write.

**Pinned by.** `test_busy_read_scan_does_not_burn_the_throttle_and_is_counted` (two
back-to-back reads while another thread holds the mutex must both attempt, then a read after
release must adopt the hand edit), and
`test_repeated_busy_read_scans_are_reported_by_store_status`.

---

## B-085 — an export that can never land while its target is quarantined (P2)

**What it actually was — and what the record got wrong.** The record says the row
"re-renders and re-logs on every write transaction". It does not: `_drain_dirty` filters on
`dirty=1 AND quarantined=0`, so a row that is both is never re-queued. Measured on the
fixture: after the entity was left dirty-and-quarantined, further transactions touching
*other* entities produced zero extra renders of it and zero extra log lines.

Two real defects sit underneath that description, and the second is worse than the one
filed:

1. **Re-render per write to that entity.** Every write to the quarantined entity rendered
   the whole document again and appended another identical `projection export suppressed
   for quarantined <rel>` line. Measured: 20 writes → 20 renders, 20 log lines. Unbounded
   growth of `store.log` for as long as the file stays broken.
2. **The row was stranded for good, and the divergence was silent.**
   `_replace_projection` writes no `projection_base` row when it suppresses, and
   `_merge_dirty_external_edit` returned silently when there was no base. So when the user
   repaired the file, the store kept a committed edit it would never write and the file kept
   content the store would never import — two divergent versions, for the life of the
   project, with nothing said. This is a correctness bug, not a waste bug, and it is why the
   fix goes further than the record's "bound retries".

**The fix.**

- `_export_blocked_by_quarantine` stops the second and later suppressions at one indexed
  lookup, with the same `export pending: <rel> is quarantined` warning and no render and no
  log line. The first suppression still goes the long way round — that is what records the
  debt. The lookup is gated behind a single `EXISTS` per transaction, so an adoption
  exporting 2,050 entities does not pay for a check that will answer "no" every time.
- A missing or unparseable merge base is no longer a silent `return`: it merges against an
  empty base, which routes the disagreement through the same local-wins rule as every other
  external edit and records it as a merge (so it shows up in `merge_conflicts_24h`). A
  repair that happens to agree with the store clears the quarantine too, rather than leaving
  a healthy file marked broken and its export stranded.
- `backlog_store_status` reports `Stuck exports` — files the database holds newer content
  for and cannot write — separately from the existing `Dirty` and `Quarantined` listings.

**Measured** (fixture: 4-file projection, 20 writes to a quarantined task, median of 3 runs):

| | renders of the blocked entity | `store.log` suppression lines | wall clock, 20 writes |
|---|---|---|---|
| before | 20 | 20 | 486 ms |
| after | 1 | 1 | 536 ms |

The wall clock is **~10 % slower, not faster**, and that is the honest result on this
fixture: the documents are tiny, so the render the guard saves costs less than the
transaction's `fsync`, while the guard's own lookup is visible. The win is the bounded
`store.log` and the correctness fix, not throughput. On a large document the saved render
scales with document size and the guard does not, so the sign flips somewhere — I did not
measure where, and do not claim it.

**Pinned by.** `test_writes_to_a_quarantined_entity_render_once_not_once_per_write`,
`test_a_stuck_export_is_named_by_store_status`,
`test_a_repaired_file_lets_the_stranded_export_land`.

---

## B-083 — non-transaction writers waited blind for the full 30 s (P2)

**What it actually was.** `update_root_config`, `linear_mark`, `linear_requeue`,
`linear_claim` and `rebuild_derived` called `_writer_mutex()` with no timeout, so
`BUSY_TIMEOUT_MS` (30 s). The caller had no way to ask for less, heard nothing while it
waited, and the diagnostic it eventually got reported the 30 s default even when the caller
had waited for something else.

**The fix.**

- All five take `timeout_ms: int | None = None`.
- `_busy_diagnostic` takes the wait the caller actually served and the operation's name, so
  the message reads `store busy for 0.4s while update_root_config waited; probable holders:
  <session> (<tool>)`. The `store busy for ` prefix is load-bearing (`_maybe_scan_on_read`
  and `_begin_immediate` both match it) and is unchanged. `transaction()` passes its `tool`
  the same way.
- `store.set_wait_observer(sink)` is a seam for progress: a waiting caller emits a
  `WriterWait(operation, waited, deadline, holders)` at 0.5 s and every 2 s after. The store
  has no MCP `Context` and no business having one, so the tool layer decides what a wait
  looks like; `backlog_server._report_writer_wait` writes to stderr, where the MCP host logs
  it — the same channel `_log_swallowed_error` already uses. An observer that raises is
  ignored: a progress report must never turn a wait into a failure.

**Deviation from the record.** The record asks for "MCP progress". FastMCP progress needs a
`Context` that none of these call sites has, and threading one through `store.py` would
invert the layering the module is built on. The seam plus the stderr sink is what is
reachable today; a future `Context`-aware tool can register a different sink without the
store changing.

**The sink must never block (found by the stress test, fixed in `3967324`).** The first
cut wrote each notice inline with `print(..., flush=True)`. That is a blocking call: a host
that has stopped draining stderr, or a harness that collects the pipe only at exit, stalls
it, and a caller already queued for the lock then waits on a *log line*. The 8-process
stress test filled its 64 KB pipe and a writer overran a 30 s deadline to 82.4 s. Notices
now go to a bounded `queue.Queue(maxsize=64)` drained by a daemon thread and are dropped
when it is full; only the first notice of a wait names holders, so a contended store is not
asked for a second connection every two seconds by every waiter; and both wait loops
re-check the deadline immediately after the notice, so an observer cannot carry a caller
past the deadline it was given.

**Pinned by.** `test_non_transaction_writers_accept_a_deadline` (all five return in under
5 s with a 150 ms deadline while another thread holds the lock — before, each waited 30 s),
`test_a_blocked_writer_names_the_operation_and_the_holder`,
`test_a_waiting_writer_reports_progress_to_an_observer`, and
`test_the_writer_wait_sink_never_blocks_its_caller` (200 notices against a stderr that never
returns must complete in under 2 s).

---

## B-084 — `_writer_mutex` polled at 20 ms with no fairness (P3)

**What it actually was.** The wait was `time.sleep(0.02)` in an unordered loop, so with
several waiters the next one in was whoever's timer happened to fire first. Reproduced:
eight threads arriving 50 ms apart were served in the order `[3, 4, 0, 5, 1, 6, …]` — the
last arrivals first. Nothing bounds how many times a given waiter is overtaken, which is the
starvation the record describes.

**The fix.**

- `_ArrivalGate`: threads within a process take a ticket on arrival and exactly one of them
  contends for the file lock at a time. That makes in-process handover strictly FIFO — an
  MCP server is several threads, so this is most of the real contention — and drops the
  process to one poller instead of one per thread. A waiter that times out leaves the queue
  so the thread behind it is not stuck behind a ticket nobody will serve; a waiter that
  pauses to send a progress notice keeps its ticket, because re-queueing between slices
  would put it back behind everyone who arrived meanwhile.
- The cross-process poll is jittered (`×0.5…1.5`) so peers stop retrying in lockstep, and
  shortens the longer this caller has already waited (`0.04 / (1 + elapsed)`, floor 2 ms,
  cap 50 ms), so age counts for something. Cross-process order is still not guaranteed —
  file locks give no queue — but a process that has waited twenty seconds no longer has the
  same odds as one that arrived a moment ago.

**Pinned by.** `test_writer_mutex_hands_the_lock_over_in_arrival_order` — eight real
threads, real contention, exact FIFO asserted.

---

## B-087 — what one read cost (P3)

**What it actually was.** Three things, on every read of the compatibility dict:

1. On a cache publish the whole dict was `copy.deepcopy`'d twice — once into `_CACHE`, once
   for the caller.
2. `_entity_rows_from_connection` JSON-decoded every bug, issue, handover, decision, idea,
   note, area and tracker in the project, on every read, including reads that name none of
   them.
3. A single-entity read (`_store_read_entity`, and `backlog_get_task` behind it) built the
   whole dict to reach one row.

(The record's "twice per read on a cache hit" is not quite right: an exact cache hit already
sets `publish=False` and copies once. The double copy is the publish path — a first read, or
any read after a write.)

**The fix.**

- The cache keeps the object the call already owns (freshly loaded, or the deep copy
  `_refresh_cached_dict` already made) and the caller gets one copy.
- That copy is `_copy_plain`, not `copy.deepcopy`. A document out of `json.loads` has no
  shared references and no cycles, and deepcopy's memo is most of its cost. Anything that is
  not an *exact* `dict`/`list`/scalar still goes through `copy.deepcopy`, so whatever a
  deriver has attached keeps its semantics.
- `_LazyEntityRows` decodes a kind when a caller first names it. The rows are still
  **fetched inside the caller's snapshot** — deferring the query would let a concurrent
  commit land between the payload and the identity stamped on it, which is the invariant
  `load_dict_with_identity` exists to keep. Its `__deepcopy__` shares the undecoded text and
  copies only what has already been handed out, so `transaction_dict`'s snapshot stays
  cheap.
- `Store.entity_row(kind, ident)` does the same scan-then-read under one indexed lookup and
  returns a fresh document. `_store_read_entity` uses it.

**The mutability constraint holds.** Nothing shared is handed out. `_CACHE` holds an object
that is never returned; the caller always gets `_copy_plain` of it.
`test_a_read_hands_back_state_no_other_caller_shares` mutates a task title and a decoded bug
document from one read and asserts the next read is unaffected; `entity_row` is asserted to
return a copy the same way.

**Measured** (2,050 tasks + 400 non-task entities, warm reads, median of 15, scan throttled
out so the numbers are the copy/decode cost):

| | warm `load_dict()` |
|---|---|
| before (eager rows, `copy.deepcopy`) | 21.4 ms |
| after (lazy rows, one `_copy_plain`) | **9.1 ms** |
| after, caller then touches every kind | 9.7 ms |

Single-entity read: **8.0 ms → 0.37 ms** (`load_dict()["_rows"]["bug"][id]` vs
`entity_row("bug", id)`).

The record's ~0.16 s figure for the copies is larger than the 21.4 ms measured here; the
fixture's documents are smaller than the real backlog's, so treat the ratio (2.4×, and 21×
for a single-entity read) as the transferable number, not the absolute.

---

## Things the five records missed

- **B-085's real blast radius is data divergence, not log noise, and it is shipped.** A
  repaired file and a store holding an unwritten edit stayed permanently out of step with
  nothing reported. The record only asked to bound the retries. Both halves of the defect —
  `_replace_projection`'s quarantine early-return writing no `projection_base` row, and
  `_merge_dirty_external_edit`'s `if base_row is None … return` — were introduced together in
  `446b612` ("feat(store): add SQLite authority and projection sync", 2026-09-04) and are
  present verbatim in `origin/master` at 6.0.2. This is a released defect, not a
  branch-only one, and deserves its own record and its own changelog line rather than being
  folded into B-085's.
- **`_merge_dirty_external_edit` swallowed a missing base generally**, not only after a
  quarantine. Any path that leaves `projection_base` empty while the row is dirty had the
  same silent no-op.
- **`backlog_get_task` still builds the whole dict** and was left that way. Its slim view
  needs the epic, the task's neighbours and `_build_tldr_index`, so a single-row read does
  not cover it; it benefits from the cheaper copy and from never decoding the non-task kinds
  it does not use. A genuine single-row `backlog_get_task` is a separate piece of work.
- **`_recover_export_intents` globs `export-intent.*.json` on every transaction** (noted in
  B-087's own Notes as low cost). Still true, still unchanged.
- **The 8-process stress test caught a regression this cluster introduced, and it was not
  flakiness.** `test_mixed_public_tool_operations_across_processes_never_lose_a_write`
  failed on the first full-suite run with a worker reporting `store busy for 82.4s while
  backlog_decision_create waited` — against a 30 s deadline. The cause was the B-083
  progress sink: it wrote each notice inline with `print(..., flush=True)`, the test
  collects the worker pipes only at exit, the 64 KB pipe filled, and the write blocked
  inside the wait loop. A writer queued for the lock was left waiting on a log line. Fixed
  in `3967324` (bounded queue, daemon drain thread, drop when full; holders only on the
  first notice; deadline re-checked immediately after every notice) and pinned by
  `test_the_writer_wait_sink_never_blocks_its_caller`. The stress suite passes on a clean
  re-run (10 passed, 442 s). Two earlier one-off failures of the same test, which I had
  written off as flakiness, were almost certainly the same defect at a lower fill level.
  Lesson worth keeping: *any* new inline write on a hot path is a blocking call.
- **`httpx` is not installed by `pip install -e .`** but five Linear test modules import it,
  so a fresh venv cannot collect the suite at all until it is added by hand. `pyproject.toml`
  declares no dev extra and no dev dependency group.
- **No `CHANGELOG.md` entry.** The cluster ships inside the database-native release and the
  version it lands under is not settled, so the entry is left for whoever cuts it.
