# B-092 — a healthy store renamed aside as corrupt

<!-- User intent: record what actually caused released 6.0.2 to destroy healthy
     databases, with measured before/after numbers, so the N01 exit criterion can be
     checked against evidence rather than a single green run. -->

**Branch:** `fix/b092-false-corruption`, on `fix/store-bugs-b083-b087` at `db4d7d7`.
**Commits:** `082cc13` (seam refactor), `24fc3e5` (failing tests), `92073f2` (fix).

## Mechanism

`PRAGMA quick_check` on a connection that has already inspected the FTS5 index
reports

```
malformed inverted index for FTS5 table main.entity_fts
```

as soon as *another* connection commits to that index. N00 established this is
retained runtime state, not damage on disk: across a 20-case matrix on SQLite
3.45.3 and 3.47.1, 16 cases reproduce it, WAL versus DELETE and peer-process
versus second-connection make no difference, and **all 40 fresh read-only
snapshots of the same files report `ok`**
(`docs/reports/2026-09-09-native-foundation.md`, `scripts/reproduce_fts_integrity.py`).

The store's admission path treated that verdict as a finding:

1. `Store._prepare_schema` ran `PRAGMA quick_check` on the writer connection and
   raised `malformed database: quick_check=…`.
2. `"malformed"` is in `_CORRUPTION_MARKERS`, so `_is_corruption` said yes.
3. `_ensure_open_locked` called `_recover_corrupt_database`, which renames the
   live `store.db`, `-wal` and `-shm` to `store.db.corrupt-<timestamp>` and
   rebuilds from the file projection.

The projection is a lagging export of an authoritative database, so the rebuild
drops everything that lives only in SQLite: recorded progress entries, live
session rows, Linear queue claims and the quarantine log. Two MCP servers, a
session plus a hook, or a viewer plus a tool call are enough to produce the peer
commit.

N00 stated the policy — check committed state through a fresh read-only
connection in one explicit snapshot — and scoped the admission change to N01.
N01 was marked complete without making it. `taskmaster.integrity.check_database`
had implemented the policy since N00; nothing on the live path called it.

### Platform asymmetry

On POSIX `os.replace` can rename a file other processes hold open, so the rename
succeeds and the loss is silent. On Windows it fails with `WinError 32` and the
`copy2` fallback with `WinError 33`, so the open aborts with an error instead.
Those failures were the only thing protecting a Windows store; the separate
Windows recovery gap is deliberately left alone, because closing it without this
fix would make the data loss reachable on Windows too.

## The change

`taskmaster/store.py`:

- `_quick_check(connection, limit=None)` — one helper behind all three
  retained-connection probes, naming what such a probe is worth: a cheap
  trigger, not a verdict on the file. Behaviour-neutral (`082cc13`).
- `Store._corruption_is_real(reason)` — calls
  `taskmaster.integrity.check_database`, the existing fresh-read-only-snapshot
  implementation, and returns True only when the committed state agrees the
  database is damaged. It logs both outcomes to `store.log`.
- `Store._discard_retained_connection(connection)` — rolls back and closes this
  thread's connections so a retry runs on a connection with no stale FTS state.
  It renames, copies and rebuilds nothing.

Both places that could reach destructive recovery from a connection verdict now
go through the gate:

- `_prepare_schema`: a non-`ok` `quick_check` raises only if confirmed;
  unconfirmed, the open continues on the same connection, which is unimpaired
  for everything else it does.
- `_ensure_open_locked`: a caught corruption-marker `DatabaseError` routes to
  `_recover_corrupt_database` only if confirmed; unconfirmed, the retained
  connection is dropped and schema preparation is retried once on a new one.

## Genuine corruption is still detected

The distinction encoded is *which connection the verdict comes from*, not
*whether to check*. Nothing is suppressed:

- The confirming snapshot runs a **full** `PRAGMA integrity_check` on a fresh
  read-only connection — strictly more thorough than the `quick_check` that
  triggered it, and FTS-aware since SQLite 3.44.
- A snapshot that reports any non-`ok` row routes to recovery exactly as before,
  including FTS index/content disagreement that leaves the file otherwise
  readable.
- A fresh connection that cannot parse the file at all (`sqlite3.DatabaseError`,
  e.g. "file is not a database") counts as confirmed.
- A runtime older than 3.44 cannot produce an FTS-aware second opinion, so it
  keeps the verdict it already has and recovers as before.
- The byte-level header refusal in `_ensure_open_locked` is untouched: a
  database whose first 16 bytes are not `SQLite format 3\0` is still renamed
  aside without consulting SQLite at all.
- A snapshot that cannot be opened read-only (missing, locked) is *not* treated
  as confirmation — renaming a file that cannot even be read would destroy it on
  a guess.

Two tests pin the second half: `test_genuinely_damaged_database_is_still_detected_and_recovered`
(FTS index damage → renamed aside, rebuilt, writable again, snapshot `ok`
afterwards) and, in `tests/test_store_recovery.py`, the dirty-commit test, which
previously simulated corruption with a raised exception on a healthy file and
now damages the index for real before asserting the dirty row survives in the
backup.

## Measurements

All runs on this worktree, Windows 11, CPython 3.13.1 / SQLite 3.45.3.

`tests/test_store_concurrency.py::test_two_processes_compat_and_direct_store_transactions_both_survive`,
run in **60 isolated pytest invocations** each time:

| Tree | Failures |
|---|---|
| Before the fix (`082cc13`, behaviour-neutral refactor only) | **4 / 60** |
| After the fix (`92073f2`) | **0 / 60** |

Every one of the four pre-fix failures carried the same signature — the worker
process aborting in `_prepare_schema` with
`malformed database: quick_check=malformed inverted index for FTS5 table main.entity_fts`,
followed by `_rename_database_family` failing with `WinError 32`.

`tests/test_store_false_corruption.py` — 3 of its 4 tests fail on the pre-fix
tree at `24fc3e5`, 4 of 4 pass at `92073f2`. Notably
`test_a_real_peer_commit_leaves_the_store_intact` simulates nothing: it commits
to `entity_fts` from a peer connection and reproduced the artifact *and* the
rename on every pre-fix run.

Full suite (`python -m pytest tests/ -q`): base branch **2,523 passed, 1
skipped**; this branch **2,527 passed, 1 skipped** in 15m40s. The four added
tests are the whole difference: `tests/test_store_recovery.py` keeps its count,
with one test renamed to
`test_failed_quick_check_confirmed_on_disk_is_treated_as_corruption`.

## Cost and residual notes

- The confirming snapshot is a *full* `integrity_check`, so it is more expensive
  than the `quick_check` that triggers it — on the 46 MB real-backlog store,
  seconds rather than the ~200 ms cold-open probe. It runs only on a non-`ok`
  verdict, at most twice per open, and is what stands between a peer commit and
  a destroyed database.
- `_ensure_open`'s network-filesystem branch still takes a retained-connection
  `quick_check` at face value, but only to fall back to projection-only mode.
  That is a degradation, not destruction, and is left as it was.
- The Windows recovery gap (`_rename_database_family` failing with `WinError 32`
  while another process holds the file) is untouched and still tracked
  separately. Repairing it before this fix would have made the data loss
  reachable on Windows rather than merely noisy.
