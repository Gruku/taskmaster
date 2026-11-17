<!-- User intent: durable record of the team-identity-002 spec-review gate so the plan and later reviews can check its findings. -->
# Spec review: team-identity-002 (actor envelope + domain_events columns)

Date: 2026-10-31. Reviewer: fresh-context Opus. Spec: rev 3.2 §4, §12, §15. Recorded verdict: **warn**
(0 Critical, 4 Important, 3 Minor).

## Gate A
| Item | Result | Note |
|---|---|---|
| Goal | PASS | Concrete and testable |
| Approach | WARN | §4 says the envelope gains `actor`; the identity-001 plan says the coordinator stamps it (I1) |
| Scope | WARN | Not stated: `entity_key`'s purpose and backfill, solo-store behaviour, whether `changes_since`/`backlog_query` expose actor |
| Risk | WARN | §16 says nothing on actor, server mode or schema placement |
| Target files | WARN | Real touch points: `native/events.py`, `native/contracts.py:78`, `coordinator/service.py:277-280`, `coordinator/identity_gate.py`, `coordinator/adapter.py:50`, `backlog_server.py` |

## Important
- **I1. The coordinator derives the actor.** It fills `handle` and `clone_id` after `admit()` and before `commands.execute` (`service.py:277-279`), overwriting anything the client sent. One actor-resolver seam: Git identity now, token in server mode. The client sends only an advisory `agent`. `clone_id` becomes an opaque origin id that the resolver supplies.
- **I2. Schema placement.** Recommended: a new opt-in schema 5 step (`VERSION=5`, `SUPPORTED={3,4,5}`). The alternatives are folding the columns into 3→4 while 001 is unpushed, or an unversioned ALTER, which breaks "never on open". Solo stores at schema 3 carry the actor but don't store it. `events.append` branches on whether the columns exist, and drops that cache when the gate upgrades mid-life. Tests: a solo store stays at 3, and 7.4.x still opens it.
- **I3. Keep the actor out of the receipt fingerprint.** `contracts.validate` hashes all fields except `request_id` (`contracts.py:113`), so a retry after a handle change would Conflict (`receipts.py:15-16`). Stamp the actor after validation, record it on the receipt, and give it an explicit contract entry (`contracts.py:78` rejects unknown keys).
- **I4. `entity_key` backfill and index.** team-ids-004's rename rewrites events by `entity_key` (spec:375-376). Backfill it in the upgrade transaction via `entity_core(kind, public_id)`, and time that on a CodeMaestro-sized table. Path and `__backlog__` rows stay NULL. Add an index.

## Minor
- M1. Define the actor for imported and internal envelopes: `profile.create`, the Linear worker, managed Git and merge-stamp replay. 004 still preserves a teammate's `updated_by`.
- M2. "Agent from the MCP client": capture FastMCP `clientInfo.name` once per server process and map it to the D7 slugs (`claude-code`→`claude`, `codex-*`→`codex`, unknown→"").
- M3. Consider storing the actor once on `command_commits` rather than on every event.
