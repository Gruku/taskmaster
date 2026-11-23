# local-server-012: which taskmaster a session is connected to (taskmaster-mods, 7.7.0)

Task local-server-012 (claude-tools backlog). Branch feat/conn-status, ships with 7.7.0 beside the Rust relay (local-server-011).

## User rulings

1. The status names the project ("local · CodeMaestro · 7.7.0"), never "this project".
2. It covers three cases: the shared local server ("local · <project> · <version>"), a team clone ("team · <team project>",
   e.g. HAY), and a per-session fallback server ("per-session · <project>", with the fallback reason when one is known).
3. It ships now, in 7.7.0, in the mods.

Rulings from the lead (2026-11-23), each a requirement:

4. The band stays hidden when it has nothing to show (model.ts bandModel), and a normal local connection never makes it show.
5. Fault states win: when tm is offline, connecting or unreadable, the header and band show that exactly as today. The
   connection label shows only when reads succeed; the last known label may sit beside a fault where it fits, never in
   place of it.
6. Per-session from a deliberate opt-out (TASKMASTER_LOCAL_SERVER=0, or the project setting local_server: off) shows in the
   pane header only, never in the band: it is the user's choice, and a band that never goes away would be noise. The band
   shows per-session for every other reason (none known, a store that is not native, server mode, ...) and shows team.
7. The `server` key on backlog_handover_list(format="json") is additive and pinned by a test; the text format the agent
   reads by default does not change.
8. The spec is committed before the build, both on feat/conn-status.

## Where the data comes from

The server reports itself as a `server` object in `backlog_handover_list(format="json")`'s envelope:

    "server": {"mode": "local" | "per-session" | "team", "project": "<name>", "version": "<plugin version>", "reason": "<why per-session, or ''>", "opted_out": <bool>}

- **mode**: `taskmaster serve` with the local profile is `local`; `serve` with the team profile is `team`; any stdio server
  (`uv run backlog_server.py`, whoever started it) is one process for one session, so `per-session`. The daemon sets a
  module flag in backlog_server (as it already does for the viewer) so the tool needs no environment probe.
- **project**: the backlog's `meta.project` (backlog_init defaults it to the directory name), else the root's directory name;
  read once per process.
- **version**: backlog_server.VERSION, what the MCP handshake already reports as serverInfo.version.
- **reason**: per-session only. The static half of the relay's decision (`tm_bridge.fallback_reason`: TASKMASTER_LOCAL_SERVER=0,
  the project setting `local_server: off`, no backlog, a store that is not native, server mode) is recomputed by the stdio
  server itself, once per process; it moves to taskmaster/localserve.py so the bridge and the server share one copy. The
  `opted_out` is true when that reason is the person's own opt-out (the environment switch or the project setting). The
  dynamic half (the local server was lost and relaunching failed, a launch error) lives only in the relay process, which
  passes nothing to its child, and relay/src stays unchanged in 7.7.0, so that case shows "per-session" with no reason.

Why a field on an existing read and not a new tool: the refresh batch (tm.ts fetchSnapshot) already calls
handover_list(json) on every refresh, so the status costs no extra call on a batch whose reads run one at a time with a 3 s
bound each, and a switch to the per-session server mid-session (the relay's fallback) shows at the next refresh with no
trigger of its own. A new read-only tool would change the tool list, the agent surface and the handshake, and add a call to
every refresh (or be read once and miss the mid-session switch). The cost of the field: the docstring gains one sentence,
so the native contracts fixture and the shipped handshake are regenerated (`scripts/native_contract_inventory.py --write`,
`tests/surface_inventory.py --write-handshake`); the tool list is unchanged. The MCP serverInfo would be the natural carrier
but the mods' `$.mcp` API does not expose it.

**Team clones** read through tm_read.py against the team host (7.5.0 today, no `server` field). The mods know a team clone
from the marker (`host.remote()` non-null) and name it from the server's `project` when the host sends one, else from the
clone's directory name. A team host never needs new code for this.

## What the mods show

The snapshot gains an optional `connection` ({mode, project, version, reason}); one pure function in model.ts turns it into
the label: `local · CodeMaestro · 7.7.0`, `team · HAY`, `per-session · CodeMaestro`. Unknown (an older server, demo,
offline before any read) keeps today's words.

- **Every pane's header** (paneFrame's health slot, top right of review, handovers, session and restore): `● local ·
  CodeMaestro · 7.7.0` (success tone), `● team · HAY`, `▲ per-session · CodeMaestro` (warning tone) in place of `● tm ok`;
  offline, connecting, unreadable and demo keep their words (ruling 5). Narrow, the project is cut to 12 then 6 cells (local then
  drops its version), then the label falls back to its mode's own word: `● tm ok` for local, `● team` for team and
  `▲ per-session` for per-session, so the per-session warning never leaves the header (review M4). The last label shown
  beside a fault (`offline · <label>`) goes the same way, to the fault word alone. The three snapshot copies of healthOf become one shared function. Refinement of "the tm-review sidebar header": the sidebar overlay is
  34 cells and drawn only on `m`, while the pane header is always on screen, and the sidebar never covers it.
- **The sidebar** (on `m`): its context block gets the label and, for per-session, the reason, wrapped (the one place with
  room for a long reason).
- **The band**: only for team and per-session, and never for an opted-out per-session (ruling 6). Where line 1 has no
  room for the whole label, the label shortens (project 12, then 6 cells, then the mode word) before the task's title is
  cut (review M3). Line 1 leads with the label (team dim, per-session in the warning tone); with
  nothing else to show, the band still draws for these two, as `<label> · no task bound to this session`. A local
  connection changes nothing: bandModel's null stays null. Offline and connecting lines are unchanged.

## Tests

- Python: the `server` object per mode (stdio with and without a static reason, local profile, team profile), on the legacy
  and native handover_list paths; fallback_reason still answers the same for the bridge after the move; contracts fixture
  and handshake regenerated and their checks green.
- Mods: parse of the envelope with and without `server`; the label function; header health per mode and width; bandModel
  null for local, a band for team and per-session; team label from the marker with no server field.
