# Native core implementation evidence

## N03: additive staging schema and backfill

New `taskmaster/native/{schema,db,migrate}.py` supplies operational JSON-text
columns, separate documents/extensions/configuration, stable integer identities,
ordered shape-preserving relations, interned paths, event/receipt/outbox schema,
and verified read snapshot admission. SQL NULL means absent; JSON `null` means
an authored null. Legacy cache columns are retained separately for equivalence
diagnostics, not used as native field owners. Historical event payloads and the
AUTOINCREMENT high-water mark are retained without invented command groups.

Backfill owns one `BEGIN IMMEDIATE`, including DDL, data, comparison and the
verified marker. Exceptions and process death at seven stages leave no partial
schema. Repeated backfill preserves integer identities. Legacy domain writes
invalidate staging in their own transaction; an already-admitted reader can
finish its older snapshot. Unknown schemas/protocols are refused before writes
and rechecked after acquiring the writer lock.

This is **staging**, with legacy tables still the sole write authority. No Store
startup path invokes it. The bridge schema/protocol markers remain unchanged;
native staging has a separate version/protocol manifest. Activation requires
the later coordinator and migration gates. The existing projection, base,
session, queue and meta tables remain authoritative and untouched. Reservation
and crash-intent sidecars remain byte-identical; their coordinated cutover is
part of N15, not an implied file migration in N03.

Validation on Python 3.12.9 / SQLite 3.47.1:

- 117 focused migration/schema, bridge admission, API inventory and FTS tests
  passed; ignored JUnit: `test-results/native-foundation/n03-focused.xml`.
- 157 affected Store schema, transaction, recovery, round-trip and ID-allocation
  integration tests passed; `test-results/native-foundation/n03-integration.xml`.
- Rehearsal script `scripts/native_backfill_rehearsal.py` requires a marked
  benchmark copy and makes a new SQLite backup before writing anything.
- Existing isolated fixture: 3,559 entities, event high-water 4,188, first
  backfill 5.08 seconds, repeat 7.77 seconds. These are single diagnostic runs,
  including equivalence checks, not latency percentiles or performance acceptance.
- Both passes produced SHA-256
  `e732dc8c4a0448ba0715a1a12de3f3e14b6c2df50e4347f778e2f39e90d33f60`.
  Fresh-connection database/FTS integrity returned `ok`; no FK violations.
- Ignored raw report: `test-results/native-n03-rehearsal-20260912/report.json`.
  Source baseline `766914c6386c4971ae00581d1198e3d38a5d0e34` plus the N03 worktree
  changes; no live project, installed plugin or external service was modified.

N04–N17 and the final default-branch merge remain outstanding. The user's merge
boundary is the complete rewrite, not this intermediate step.
