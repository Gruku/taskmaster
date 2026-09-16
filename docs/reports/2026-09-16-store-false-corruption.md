<!-- User intent: record a reproduced, pre-existing store defect found while
     validating N07 — a healthy database quarantined as corrupt — so it is not
     lost as suite flakiness. Not an N07 change; no fix is attempted here. -->

# A healthy store is quarantined as corrupt under two-process writes

**Status:** reproduced and root-caused; **not fixed**, and not N07's to fix.
Found while running the N07 full suite on `feat/native-n07-lifecycle`. A second
agent independently hit the same failure on an unrelated branch, which is what
prompted the investigation rather than a flaky-test dismissal.

## What happens

`tests/test_store_concurrency.py::test_two_processes_compat_and_direct_store_transactions_both_survive`
fails intermittently. The failing process is the **direct-store** worker:

```
sqlite3.DatabaseError: malformed database: quick_check=malformed inverted index
    for FTS5 table main.entity_fts
```

raised at `taskmaster/store.py:1257` in `_prepare_schema`. That drives
`_ensure_open_locked` → `_recover_corrupt_database` → `_backup_database_family`,
which then fails against the peer still holding the file:

```
PermissionError: [WinError 32] The process cannot access the file because it is
    being used by another process:
    '…\.taskmaster\local\store.db' -> '…\store.db.corrupt-20260916T130055824094Z'
PermissionError: [WinError 33] The process cannot access the file because
    another process has locked a portion of the file
```

Both `WinError 32` (the rename) and `WinError 33` (the `shutil.copy2` fallback)
appear in the same failure.

## The database is not corrupt

The recovery path did produce a quarantine copy. Checked afterwards through a
fresh read-only connection — the check N00 defines as authoritative:

| Check | `store.db.corrupt-…` | live `store.db` |
|---|---|---|
| `PRAGMA quick_check` | ok | ok |
| `PRAGMA integrity_check` | ok | ok |
| `PRAGMA foreign_key_check` | no rows | no rows |
| `INSERT INTO entity_fts(entity_fts) VALUES('integrity-check')` | ok | ok |
| `entities` rows | 5 | 6 |
| `entity_fts` rows | 5 | 6 |

Nothing was corrupt. A healthy authoritative database was renamed aside as
`store.db.corrupt-<timestamp>`.

## Why this is the N00 finding, re-entering through the startup path

`docs/reports/2026-09-09-native-foundation.md` already established that
`malformed inverted index for FTS5 table main.entity_fts` is a **retained-connection
SQLite artifact, not corruption**: 16 of 20 matrix cases reproduce it on SQLite
3.45.3 and 3.47.1, journal mode and peer-process-versus-second-connection make no
difference, and all 40 fresh read-only snapshots report `ok`. Its stated policy is:

> check committed state through a **fresh read-only connection in one explicit
> snapshot**. Do not infer corruption from the old retained-connection benchmark
> result, suppress genuine errors, or rebuild authoritative data to clear it.

and it explicitly scoped itself out of the startup path:

> This does not replace the store's existing startup/recovery logic; its
> admission changes belong to N01.

`_prepare_schema` still runs `PRAGMA quick_check` on a writer connection while a
peer commits, and treats a non-`ok` result as corruption. That is the inference
N00 documented as invalid, and it is wired to a destructive response.

## Blast radius

This is a temp store in a test, but the code path is the ordinary one: any
project opened by two Taskmaster processes at once. The consequences, in order of
severity:

1. A healthy `store.db` is renamed to `store.db.corrupt-<timestamp>` and the
   store rebuilds from projections. Whatever lived only in the database and not
   in the exported files is what is at risk.
2. On Windows the quarantine itself fails against the peer's lock, so the
   operation aborts with a `PermissionError` the caller sees as a crash.
3. It fails *closed* — no silent bad data — but a spurious "your database is
   corrupt" is its own kind of damage.

## Measurements

Isolated runs of the single test, same machine, same session, interleaved:

| Tree | Failures |
|---|---|
| `feat/native-n07-lifecycle` (`c1ef761`), isolated | 5 / 40, then 1 more within 6 attempts |
| `feat/database-native-foundation` (`9bf22a8`, base), isolated | 0 / 40 |
| `feat/native-n07-lifecycle`, inside the full suite | 2 / 2 |

The full-suite rate (2 of 2) is markedly higher than the isolated rate (~12%),
which suggests the trigger is sensitive to machine state a long run produces —
open handles, page cache or temp-directory pressure — rather than to anything in
the test itself.

Fisher exact on 5/40 vs 0/40 is p ≈ 0.027 one-tailed. **That differential is
unexplained and should not be read as attribution.** Three things argue against
the N07 branch causing it:

- The failing process is the direct-store worker, which imports only
  `taskmaster.store`. It executes no line this branch changed.
- The branch diff touches `taskmaster/store.py` not at all and contains no
  occurrence of `entity_fts`.
- `backlog_server` import cost, the one legacy-facing change, is unchanged:
  111–119 µs self and ~1.0 s cumulative on both trees.

The plausible reading is that this is a pre-existing race whose window the branch
shifts rather than creates, and that 40 samples per arm cannot separate a 12%
rate from a 0–9% one. The honest summary is: reproduced on this branch,
not reproduced on base in equal sampling, mechanism unknown.

## What should happen

This belongs with the store's admission and recovery logic — N01's area by N00's
own note — not with N07. Two independent lines:

1. **Stop inferring corruption from a retained-connection FTS diagnostic.**
   Apply N00's documented policy at the startup check: confirm through a fresh
   read-only connection before concluding anything, per
   `taskmaster.integrity.check_database`.
2. **Never quarantine on an unconfirmed diagnostic.** Renaming an authoritative
   database is not a recoverable-by-default action, and the Windows lock failure
   shows the quarantine cannot even be relied on to complete.

Neither is attempted here. Recorded so the observation is not spent.
