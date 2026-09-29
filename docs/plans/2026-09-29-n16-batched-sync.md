<!-- User intent: a full sync must work on a project of any size (N16's 10x dataset is ~37,000
     projection files) by scanning in bounded batches, with exactly the outcomes an unbatched
     sync would give; the MAX_FILES refusal left big projects permanently unsynchronizable. -->

# N16: batched full sync

Status: design note written before the change, 2026-09-29.

## Problem

`sync_worker._synchronize` refused a full sync when more than `sync.MAX_FILES` (10,000)
projection paths were selected ("bounded scan refused"). Such a project could never complete a
full sync, so the managed-Git barrier refused every Git operation there as well. Named resync
already chunks its paths (`native_routing/resync.py`). The refusal was the only bound: the
per-file loop itself was never the memory problem. Two whole-set steps loaded bytes for every
file (`_unchanged_rule` fetched every retained base blob; `checkouts.detect` loaded every base
blob through `published()` and kept the bytes of every differing file), and two membership tests
were quadratic in the number of paths (`rel not in selected` on a list, and `pending()` on the
`unresolved`/`notices` lists).

## Design

**One sync operation, scanned in batches.** A batched full sync is still one operation under one
sync id: one `sync.begin`, one Git classification, one writer pause, one publication, one
completion check and one `sync.finish`. The batch is a unit of reading and memory only. Nothing
per-batch is durable except what an unbatched sync already makes durable: import receipts, drift
holds, and the fingerprint cache.

- **Batch unit:** `sync_worker.BATCH_FILES` paths (default `sync.MAX_FILES`, i.e. 10,000), taken
  as consecutive slices of the selected path list.
- **Ordering:** unchanged from the unbatched sync. Discovered files come first, in
  `ENTITY_FILE_SPECS` order and sorted per directory, then paths the store records that were not
  discovered (missing files, in `file` order). Batches are consecutive slices of that list, so
  the per-file order of imports, notices and receipts is the unbatched order.
- **Path enumeration covers every file:** discovery, the recorded-path union (now a set), the
  missing-file (deletion) candidates and duplicate detection all run over the whole set, as
  before. That list holds paths only.

**Whole-set judgements stay whole-set.** They are made memory-bounded, not split per batch:

- *Missing and deleted files*: selected = discovered and recorded. A deletion in any batch is a
  selected path whose file is absent; `prepare` turns it into a repair, as before.
- *Git classification (`checkouts.detect`)*: still one pass over every selected path, before any
  import and against one HEAD observation. It streams: each file is read, reduced to its digests
  (sha1, LF/CRLF variants, Git blob ids) and dropped. `classify` and `hold` take those digests
  instead of bytes, and the published generation is read as hashes, not blobs. Release
  consumption, the drift warning, `in_progress` and history walks see the whole set exactly as
  before. Memory is O(paths) digests, never O(bytes).
- *Fingerprint cache / generation*: one `Scan` for the whole sync, as before. It is additionally
  persisted after each batch (`save_scan`), so an interrupted sync keeps what it learned.
- *Unchanged rule*: per batch. Only that batch's `projection`/`projection_base` rows are loaded,
  so base blobs are hashed per batch. Flag and drift holds are whole-set sets of paths.
- *Quarantine, flag and drift holds; derived-index check; completion check*: after the writer
  pause, over the whole set, as before. The completion check was already one file at a time.
- *Time budget and retry contract (track A)*: unchanged. The budget is checked before every file
  in every batch. Running out returns `pending` naming the file with "retry the same sync id",
  before the writer pause (`captured` stays false), so neither `_only_drift` nor the Git barrier
  can accept it.
- *Durable sync-operation record*: one `sync.begin` with the same input, and one `sync.finish`
  holding the summary of every batch. `synchronized` is written only after the last batch,
  publication and the completion check.

**Crash semantics: safely restartable, not resumed.** No batch cursor is persisted. A retry under
the same sync id runs `sync.begin` again, which is idempotent. A `complete` record replays the
stored result. Otherwise the retry rescans from the first batch:

- A file whose import committed now carries its new base, so `prepare` returns `unchanged` and
  it is never imported twice. An import whose reply was lost has the same request key
  (`sha256(arguments)`) and replays its receipt.
- The per-batch fingerprint cache turns the rescan of finished batches into stat calls.
- A crash can never leave `synchronized`: that state exists only as the `sync.finish` record,
  written after every batch.

A resumable cursor was rejected. Skipping batches the retry did not rescan would judge edits
made since then only at completion ("differs from the published generation"). An unbatched retry
would import them, so a cursor changes the outcome.

**Named syncs (`files=[...]`)** keep the `MAX_FILES` input limit (one request envelope). Resync
already chunks.

No user-visible sync semantics change: the result shape, notices, warnings, states and receipts
are the unbatched ones.
