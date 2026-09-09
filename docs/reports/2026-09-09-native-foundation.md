# Database-native foundation evidence

Implementation branch: `feat/database-native-foundation`, based on `e9ea119`.
All writes in this investigation use synthetic temporary databases or existing
marked benchmark copies. CodeMaestro's live project is untouched.

## N00: retained-connection FTS diagnostic

`scripts/reproduce_fts_integrity.py` reproduces the earlier benchmark anomaly
without importing Taskmaster. Create a contentful FTS5 table; run `quick_check`;
insert a document through a second connection; run `integrity_check` on the first.
SQLite reports `malformed inverted index for FTS5 table main.entity_fts`.

On both SQLite 3.45.3 (Python 3.13.1) and 3.47.1 (Python 3.12.9), 16 of 20 matrix
cases report that retained-connection error. WAL versus DELETE journals and a
peer process versus a second connection make no difference. An unrelated rollback,
an explicit read transaction, and a passive checkpoint do not clear it. A normal
FTS table read does clear it, without repairing or rewriting the index. All 40
fresh read-only snapshots report `ok` and find the peer's committed document.

This isolates the failure to retained FTS diagnostic state in these runtimes;
neither Taskmaster's relationship algorithm nor parallel-write rollback is
required. The exact upstream C defect/fixed release is not established here.
The supported diagnostic policy is therefore behavioral: check committed state
through a **fresh read-only connection in one explicit snapshot**. Do not infer
corruption from the old retained-connection benchmark result, suppress genuine
errors, or rebuild authoritative data to clear it.

`taskmaster.integrity.check_database` implements that policy and returns every
integrity and foreign-key diagnostic. It does not create missing files, repair
indexes, use immutable mode, or inspect another connection's uncommitted state.
The benchmark harnesses use it outside timed regions. This does not replace
the store's existing startup/recovery logic; its admission changes belong to N01.

The diagnostic requires SQLite 3.44 or newer: that release added virtual-table
FTS verification to PRAGMA integrity_check ([release notes](https://www.sqlite.org/releaselog/3_44_0.html)).
Older runtimes are refused explicitly instead of returning an incomplete health
claim. This is a diagnostic requirement, not a change to ordinary legacy store
startup. A regression covers the version fence as well as real index damage.

Regression coverage includes peer commits, rollback, deliberately damaged FTS
content (still detected without repair), missing databases, foreign-key errors,
and canonical entity/derived/projection comparisons around the original full
relationship rebuild. The exact-row oracle preserves edge multiplicity, unknown
JSON fields, prose, revisions and tombstones, while excluding physical FTS rowids.

The SQLite [FTS5 integrity command documentation](https://www.sqlite.org/fts5.html#the_integrity_check_command)
describes checking index/content agreement. Our corruption fixture deliberately
breaks that agreement. The retained-connection conclusion above comes from the
local reproducer, not a claim of upstream acknowledgement.

Raw matrix evidence: ignored `test-results/native-foundation/fts-3.45.3.json` and
`fts-3.47.1.json`. The existing performance reports retain their original timings;
no new speedup or tail-latency claim is made by this diagnostic change.

Validation: 129 tests passed across native baseline, store recovery, schema,
transactions, derived queries and merge/derived suites (55.59 seconds).
