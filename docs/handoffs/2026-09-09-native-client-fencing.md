# Database-native bridge: local implementation and cutover contract

Branch: `feat/database-native-foundation`. N00 baseline commit: `c940697`.
This is local implementation evidence, not an installed bridge or native rollout.

Validation: 292 tests passed in 85.65 seconds across baseline/admission, schema,
recovery, read path, public server integration, both hooks, maintenance scripts,
Linear claim/config writes and committed viewer state. The new admission suite
includes 72 cold/warm method/fence combinations, six public entry points, both
hook connectors and the pre-admitted writer-lock race. `git diff --check` passed.

## Admission behavior

`taskmaster.admission` is standard-library-only so MCP, viewer, scripts and
system-Python hooks share one policy. A newer/invalid schema, an unsupported
`minimum_client_protocol`, or any `migration_state` other than `ready` raises
`UnsupportedStoreError`. This is a RuntimeError, deliberately outside SQLite's
corruption/recovery exception class. Legacy absent protocol/migration markers
mean protocol 0/ready. Missing meta still belongs to legacy bootstrap handling;
the future migrator must retain meta for admission to work.

Store connection acquisition checks before journal configuration, registration,
schema preparation or recovery. Warm handles recheck rather than caching the
schema version. Transactions recheck after BEGIN IMMEDIATE, so waiting behind
a migration cannot admit a previously checked writer. Heartbeat/session writes
check inside their own short write transaction. Queue operations use the same
admission. Root configuration checks under the repository mutex; derived cache
writes check admission and reuse the enclosing transaction where one exists.

The hooks open one read snapshot and check it before reading legacy tables.
Their established fallback/advisory behavior is preserved; a refusal is not
permission to import or rebuild the database. Read-only store diagnostics also
refuse unsupported schemas. Shutdown checkpoints skip incompatible stores.

The previous unsupported-schema rebuild is removed. The in-place rebuild for
**proven corruption with a forensic backup** remains a separately named path,
with its own admission check. Future unprojected tables, session rows, queue
state, ID reservations and portable projection bytes survive refusal tests.

Schema preparation records `bridge_client_protocol=1` and the supported
capabilities. These describe the preparing binary, **not proof that every
running client has upgraded**. Cold fast-path readers do not write capability
metadata just to advertise themselves.

## Migration ownership lifecycle required by N15

1. Inventory every launcher below, stop pre-bridge processes, install the bridge
   in every active host, restart, and verify refusal using a disposable fixture.
   An old executable already in memory is not protected by updating files.
2. Acquire the existing repository writer/recovery mutex and BEGIN IMMEDIATE;
   validate readiness and publish `migration_state=migrating` with the migration
   owner/token. Commit the fence while retaining repository ownership. Bridge
   readers/commands now refuse. A marker is not a substitute for stopping old
   clients, which cannot interpret it.
3. Back up and backfill under the later migration implementation's transactional
   rules. Retain meta and protocol markers throughout. Publish the new schema,
   required protocol and ready state atomically with activation. Release the
   repository mutex only after completing the operation.
4. A crash leaves a migration fence. Ordinary clients cannot clear or take over
   it. A recovery/migration command must prove ownership, examine the journal
   and either resume or restore the pre-activation authority. This command is
   part of N15; this bridge intentionally does not implement takeover/migration.

Read snapshots admitted before a fence may finish against the old committed
snapshot. A write admitted before a fence owns SQLite's writer lock and finishes
before the migrator can publish it. No claim is made that arbitrary external
sqlite3 connections, or pre-bridge binaries, are fenced by this Python API.

## Launcher inventory inspected locally

| Surface | Launch/admission path | Required cutover action |
|---|---|---|
| Claude plugin | `.mcp.json`: `uv run ${CLAUDE_PLUGIN_ROOT}/backlog_server.py` | Upgrade selected cache, restart all host MCP processes |
| Codex plugin | `.codex-plugin/plugin.json`: `uv run backlog_server.py` from plugin cwd | Upgrade selected cache, restart host connection |
| Local/manual Python | root `backlog_server.py` imports `taskmaster.backlog_server` | Stop/restart manual servers against bridge code |
| HTTP viewer | started by the same server module | Stop each owning server; verify served process version |
| Edit/merge hooks | system Python scripts under `hooks/`, shared admission module | Upgrade complete hook package, not just server file |
| Maintenance scripts | backfill TLDR, handover-status migration, link migration use Store | Pin bridge checkout before running; no old scheduled launchers |
| Linear | server worker and `Store.linear_*` | Stop drains with server; restart under bridge after verification |

Observed cache directories on 2026-09-09: Codex Taskmaster 6.0.1 and 6.0.2;
Claude Taskmaster 5.1.0, 5.2.0, 6.0.0, 6.0.1 and 6.0.2. Directory presence does
not establish active process identity. No caches, host settings, processes or
CodeMaestro files were changed. Active-process verification remains an explicit
rollout prerequisite. N02 supplies the exhaustive API/field compatibility matrix.
