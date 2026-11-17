<!-- User intent: in a teammate's clone the merge gate must never let a gated merge through just because the team
     server didn't answer (block, with the user's explicit approval as the only bypass), merge stamps must reach the
     server as commands, and every Git question (tip, ancestry, branch) is answered in the teammate's own clone. -->

# team-server-008: merge gate fails closed, merge stamps as commands, Git checks client-side (design, 2026-11-07)

**Task.** `team-server-008` (go-live set). Done when:
- the merge gate blocks, with an explicit bypass, whenever the server gives no usable answer;
- `merge_recorder_stamp` sends merge stamps to the server as commands;
- ancestry and branch checks run in client hooks, and their results reach the server as arguments.

**Source.** `docs/specs/2026-10-31-team-server-mode-addendum.md` §5 (merge gate fails closed; stamps as commands; Git
checks re-review new-2).
**Binding rulings** (`docs/handoffs/2026-11-01-team-server-lanes/rulings.md`): 008 is in the go-live set; no teammate
key is issued before it merges. The host holds only a backup push key and a read-only code key, so it never runs a
teammate's Git.
**Builds on** (feat/team-collab `87977af`):
- 007: `/read/v1/`, `remote/` client (`ReadUnavailable` kinds, `hook_deadline`, `hookside`), `hook_reads` views,
  the `on_remote_unavailable` and `_send_remote_stamp` seams (007 design §13.1).
- 003: `SessionRegistry.current`, channel sessions (`<handle>@read`), `Requester`.
- 005: the coordinator allowlist and tool gate; 006: the serve layers; 009: `server_marker`.
- The solo merge-stamp queue and replayer, `native_routing/merge_stamps.py`.
**Clustered with.** `team-server-011` (`docs/specs/2026-11-07-team-server-011-client-git-design.md`). The shared seam
is settled in §11 here and mirrored in 011 §10.
**Plan.** Embedded, §16.
**Review history.** Rev 1 (`fc469b04`) failed design review: I1-I4 and S1 blocking, plus 7 Minors
(`D:\scratchbook\taskmaster\ts008-review\design-review.md`). Rev 2 settles them:

| Finding | Settled in |
|---|---|
| I1 (branch parser) | D11 |
| I2 (watchdog vs. the host's kill) | D3, with the Step 0a result in §9 G1 |
| I3 (fail-open exits in `decide`) | D3.6, D3.7 |
| I4 (the approval is taskmaster's own, so it is hardened here) | D12, with the residual in G8 |
| S1 (the hook channel's clone) | D5, §11 |

Minors 1-6 are folded in (D4, D3, D11, D8, D8, Step 4). Minor 7 is folded in as G9, but its suggestion of logging old
User-Agents on the read API is dropped.

**Spike result (Step 0a, run 2026-11-07, Claude Code 2.1.319, `claude -p --model haiku` in
`D:\scratchbook\taskmaster\ts008\spike-timeout\`).** Two runs, each with a Bash PreToolUse hook that sleeps 12 s and
then exits 2:
- with `timeout: 3`, the hook was killed (it never wrote its "finished" file) and the Bash command **ran**;
- with `timeout: 20`, the exit 2 landed and the command was **blocked**, with the hook's stderr shown to the agent.

So a killed hook allows the tool call, and the block must land inside the configured timeout (D3).

## 1. Problem

007 moved the three hooks' reads to the server and kept every hook outcome fail-open, so nothing got stricter before
008. Three things are left:

1. **The merge gate falls open.**
   - `merge_gate_decide.on_remote_unavailable` returns `ALLOW` for every failure kind and for `ambiguous`.
   - `decide()` answers `ALLOW` on any exception in the server branch (`merge_gate_decide.py:305-309`).
   - `_verdict` allows when the branch tip can't be read (`:213-216`).
   - The launcher allows when the decision process exits non-zero, prints nothing or prints something unrecognized
     (`merge_gate.py:154-169`).
   - The launcher runs the decision as a second Python process with no timeout. Under load, two interpreter starts
     plus a server read can pass the host's 10 s hook kill (`hooks.json`, 007 §16 R5). A killed hook is not a block.
   - The command parser (`merge_gate.parse_src_branch`, copied in `merge_recorder.py`) takes the last non-flag token
     after the word "merge", and `GIT_MERGE_RE` also matches `merge-base`, `merge-tree` and `merge-file`.
     - `git merge feature/x -m msg` asks about `msg`.
     - `… && git push` asks about `push`, and so does `…; echo done` about `done`. Both are allowed.
     - `-m "Merge feature/x: add thing"` asks about `thing"`.
     - A read-only `git merge-base` is gated.

     While the gate fails open this is harmless; a fail-closed gate is only as good as its parser.
   - The approval file that bypasses a block can be written by the agent itself, and any "Approve" answer to any
     question counts (D12).
2. **Merge stamps go nowhere.** `merge_recorder_stamp._send_remote_stamp` only logs (`:195-198`).
3. **Ancestry is decided on the host.** The solo replayer decides with `git merge-base --is-ancestor` in the store's
   own clone (`merge_stamps.git_is_ancestor`, `:336-347`, called from `native_apply`, `:358`), and the serve process
   replays its own queue after any native call (`registry._replay_merge_stamps`, `:133`). On a team server, a
   teammate's merge commit usually isn't in the host clone, and addendum §5 forbids the host to decide from its own
   clone whether a teammate's commit exists.

## 2. Scope

In scope:

- the gate's server-mode decisions and texts (D1-D4);
- the launcher in server mode: decision in-process, one deadline, a watchdog (D3);
- a `/write/v1/merge-stamp` route that applies a stamp under the caller's principal (D5, D6);
- a compare-and-set argument on `task.merge` and `backlog_record_merge` (D6);
- client-side ancestry and the two-phase stamp (D7);
- the client stamp queue in the user's cache dir, reusing the solo queue (D8);
- no host-side Git for a teammate's stamp, and no host replay in a serve daemon (D9);
- the `clone` wire parameter, which 011 gives meaning (§11).

Out of scope:

| Not here | Owner |
|---|---|
| The per-clone table; clone ids; branch lookup by clone; per-clone merge records | 011 |
| `edit_resurface`, the mods, the read views' data | 007 (unchanged) |
| Mod writes to the server | follow-up (007 ruling Q5) |
| Gating merges the user runs in their own shell (`!git merge`) or outside Claude Code | never gated, solo included |
| Deployment notes (the bypass, `set-key`) | 016 how-to |
| Security review of the new write route | 015 |

## 3. What fail-closed protects

| Situation | Today (007) | After 008 |
|---|---|---|
| Server unreachable, slow, refusing the key, wrong version, 5xx, unreadable reply | ALLOW + log | BLOCK naming the cause; the user may approve |
| Marker unreadable, no key | ALLOW + log | BLOCK naming the fix |
| Several tasks on the branch, none the caller's | ALLOW + log | BLOCK naming the candidates |
| A crash in the decision code (server mode) | ALLOW | BLOCK |
| Tip of the branch unreadable here (server mode) | ALLOW | BLOCK |
| The hook would pass the host's kill | killed, so allowed (Step 0a) | the hook timeout is 30 s and the watchdog blocks at 9 s after the launcher starts, which leaves 21 s for start-up (D3) |
| Root not found, or the view's `data` malformed or not fresh from the server | ALLOW | BLOCK (D3.6, D3.7) |
| A merge the parser can't read with confidence | allowed or misread | BLOCK (D11) |
| A merge in a compound command (`&& git push`, `; echo`, `\| tail`) or with `-m <msg>` | the wrong token is checked | every merged branch is checked (D11) |
| `git merge-base` and other read-only `merge-*` commands | gated | not gated (D11) |
| No task on the branch, policy off, `skip_merge_gate`, fresh pass | ALLOW | ALLOW (unchanged: there is a verdict) |
| Solo project (no marker, no latch) | today | today, byte for byte |

The rule is: **in server mode, ALLOW needs a verdict from a fresh server answer.** Anything else blocks. A block is
never a dead end, because the existing approval flow is the explicit bypass (D2).

## 4. Decisions

### D1. Blocks per failure kind (`merge_gate_decide.on_remote_unavailable`)

007 made this the single function 008 changes. It returns `BLOCK:-: <text>`. The `-` task slot marks "no verdict",
which the launcher words differently (D2). Texts, pinned one per kind in Step 2:

| `kind` | Text (after `BLOCK:-: `) |
|---|---|
| `unreachable` | `Taskmaster server unreachable; retry or bypass explicitly ({message}).` (the addendum's wording first) |
| `busy` | `the Taskmaster server is rate limiting this key; retry in {n} s or bypass explicitly.` (`n` from `retry_after`, else "a few") |
| `server` | `the Taskmaster server could not read its store; retry or bypass explicitly.` |
| `bad-response` | `the Taskmaster server's answer was unreadable ({message}); retry or bypass explicitly.` |
| `refused` | `the Taskmaster server did not accept this key: it may have been revoked or rotated, or the server's key list was briefly unavailable. Retry; if the operator gave you a new key, run taskmaster remote set-key; or bypass explicitly.` (007 D13's wording; never "re-enter your key") |
| `no-key` | `no key for the Taskmaster server at {origin}: run taskmaster remote set-key --url <the marker's url>, or bypass explicitly.` |
| `config` | `this clone's .taskmaster-server.json (or its latch in .git/) can't be read ({message}); fix it, or bypass explicitly.` |
| `version` | `the Taskmaster server's read API is not one this plugin speaks; upgrade the taskmaster plugin, or bypass explicitly.` |
| `bad-request` | `the Taskmaster server refused the merge gate's request ({message}); bypass explicitly and report it.` |
| `ambiguous` | `several tasks use branch {src} ({ids}) and none of them is yours, so the gate can't tell which review-gate applies; set the branch on the task you are merging (backlog_update_task), or bypass explicitly.` |
| `internal` (a Python exception in the server branch) | `the merge gate failed ({type}); retry, or bypass explicitly and report it.` |
| `tip` (D4) | `the tip of {src} can't be read in this clone; retry, or bypass explicitly.` |
| `ref` (D4) | `the branch name {src!r} can't be sent to the Taskmaster server ({problem}); bypass explicitly.` (`problem` from the shared `ref_problem`, D4) |
| `unparsed` (D11) | `the merge gate can't tell with confidence which branch this command merges ({why}); run the merge as a plain git merge <branch> command, or bypass explicitly.` |

`{message}` is the client's own message: it never holds the key (007 D10). Every block also writes one line to the
server hook log, as today.

### D2. The bypass is the approval flow, hardened (D12)

- `merge_gate.py` already blocks with exit 2 and asks the agent to put an Approve/Deny question to the user.
  `taskmaster_merge_approve.py` writes `~/.claude/taskmaster-merge-approve-<session>` on "Approve", and the gate honours
  it for 60 s. That is the explicit bypass.
- Both scripts are taskmaster's own (this repo, `hooks/`). guard-hooks has a separate `guard-approve-*` namespace,
  which this task doesn't touch. So 008 hardens the flow (D12) instead of only documenting it.
- For a `-` (no-verdict) block, `merge_gate.block` uses a second guidance text, `UNAVAILABLE_GUIDANCE`:

  > merge-gate blocked this merge: {text}
  > This clone uses a Taskmaster team server, and without its answer the merge gate can't tell whether this branch's
  > task has a passing review-gate, so it blocks instead of guessing. Either retry the merge once the server answers,
  > or ask the user whether to merge without the gate: call AskUserQuestion with one short question that starts with
  > `[merge-gate {branch}]` and says why, and two options labelled "Approve" (merge anyway; the approval lasts 60 s) and
  > "Deny" (don't merge). [the rest of today's approval sentences, with the same `[merge-gate {branch}]` rule]

  A verdict block (`BLOCK:<id>: …`) keeps today's text.
- **No environment-variable bypass.** The agent can set one as easily as the user, so it wouldn't be explicit. The
  approval file is the same kind of thing, which is why D12 binds it to the user's answer and refuses the obvious ways
  of writing it; the residual is G8 (Q1).
- A verdict block on a task that isn't the caller's (`task.mine` false in the view) appends: ` This is a teammate's
  task; its review-gate still applies.`

### D3. The launcher in server mode: in-process, one deadline, a watchdog

`merge_gate.py` changes only after its prefilter (D11: the raw text names `git` and `merge` or `pull`), so other
commands still spawn nothing.

0. **The hook timeout is 30 s** (`hooks/hooks.json`, the `merge_gate.py` PreToolUse entry, today 10 s). Step 0a
   showed that a hook killed at its timeout lets the Bash command run, so the block has to land inside it.
   - **The margin:** the watchdog fires 9 s after `_STARTED` (the launcher's first statement), and the host kills at
     30 s after it spawns `run_hook.sh`. That leaves 21 s for the shells and the interpreter to start. 007 §16 R5
     measured 5-7 s for one interpreter start under load, so the margin is at least 14 s.
   - **The cost:** none on ordinary commands, which pass the prefilter in milliseconds. A merge waits at most about
     9 s plus start-up when the server is silent.
   - `tests/test_hooks_json.py` pins the value.
   - Solo's subprocess decision gains `timeout=9.0` (today none). On expiry the solo answer is ALLOW, as before, but
     it now arrives inside the hook's own time.

1. **Mode check.** `merge_gate_decide.server_clone(cwd) -> bool`, a new helper:
   - resolve the root with `taskmaster.root.resolve_root`, then call `server_marker.server_mode(root)`;
   - a `Marker` means True. `None` means False. `ServerConfigError` means True;
   - if resolution raises, True when a `.taskmaster-server.json` exists in `cwd` or any parent, else False;
   - the solo path imports no `taskmaster.remote` (007's test stays).
2. **Solo.** Run the subprocess exactly as today: fail-open, same argv, same parsing.
3. **Server mode.** `merge_gate.py` imports `merge_gate_decide` (same `hooks/` directory) and calls
   `decide(src, cwd, started=_STARTED, server=True)` once per branch D11 found, with `cwd` set to the `-C` directory
   when the command names one (D11). `_STARTED` is `_STARTED` is `merge_gate.py`'s own first statement. That saves one
   interpreter start (007 §16 R5 measured 5-7 s for one start under load), and the read deadline becomes
   `hook_deadline(5.0, started=_STARTED)`, at most 8 s from the launcher's start.
4. **Watchdog.** Right after the prefilter accepts a named-branch merge, before any `taskmaster` import, arm a
   daemon `threading.Timer` for `_STARTED + 9.0 s`, so a slow import or mode check is covered too. When it fires and
   no decision is out yet:
   - if the mode check already answered solo, it does nothing (solo stays fail-open);
   - if the mode is server, or still unknown and a `.taskmaster-server.json` exists in `cwd` or a parent (a stdlib walk,
     no Git), it writes the `UNAVAILABLE_GUIDANCE` block with the text `the merge gate got no answer within 9 s; retry
     or bypass explicitly.`, flushes, and calls `os._exit(2)`;
   - otherwise it does nothing.

   A lock and a `decided` flag make the timer and the main thread mutually exclusive, so exactly one of them answers.
5. **The approval check still runs first** for every block, so an approved merge is never held by the watchdog.
   `block()` calls `check_approval` before writing.
6. **An unexpected answer in server mode is a block.** `decide(src, cwd, *, started=None, server=None)` takes the
   launcher's mode, so the two can't disagree. `server=None` (the subprocess and direct callers) computes it with
   `server_clone`. With `server=True`:
   - `project_root` returning None or raising blocks with `config`
     (`this clone's team-server root can't be found ({reason})`). Today it falls back to `_walk_up_for_backlog`, which
     looks for a `.taskmaster/` that a teammate clone doesn't have;
   - any exception before or inside the server branch blocks with `internal` (D1);
   - in the launcher, an exception around the in-process call blocks with `internal`.
7. **Only a definitive, fresh answer can ALLOW.** `decide_remote`:
   - requires `result.source == "server"`. `merge-gate` is uncacheable today (`CACHE_POLICY`), but a later policy
     change must not open the gate quietly; anything else blocks with `bad-response` ("not a fresh server answer");
   - checks the view's shape before `decide_from_view`, through `hook_reads.check_merge_gate_view(data) -> str | None`.
     `policy_required` and `ambiguous` must be bools, and `candidates` a list of strings. `task` must be null or a
     dict with a string `id`, a bool `mine` and `skip_merge_gate`, `merge_gate_freshness` in `{"strict", "any"}`, and
     `review_gate` either null or a dict with string `verdict` and `commit_sha`. A problem blocks with `bad-response`
     naming the field. The same function is the server's own test oracle (Step 1).

`_verdict` takes `strict=False`; `decide_from_view(…, strict=True)` in server mode. The other bound: `_git_rev_parse`
moves to `bounded_run.run_bounded` (today `subprocess.run(timeout=10)`, which can hang on Windows through a `git.exe`
grandchild, memory `windows-subprocess-timeout-hang`). It takes `timeout=`, and the server branch passes
`max(0.5, deadline − now)`.

### D4. Local checks before any request

- **Ref names.** The whole of `read_api.valid_ref`'s rule moves to `native_routing/hook_reads.py` (stdlib) as
  `ref_problem(value) -> str | None`:
  - `REF_RE`;
  - no `..`;
  - no leading `-` or `/`;
  - no trailing `/`;
  - at most 255 characters.

  `read_api.valid_ref` becomes a wrapper that raises `Refused` on a problem, so one rule serves both sides.
  `decide_remote` checks `src` before sending. A problem blocks with kind `ref`, because the gate can't ask about it
  (007 §6 M12). `stamp_remote` logs "not recorded: branch name not sendable" and doesn't queue.
- **Tip.** In server mode with freshness `strict`, an unresolvable tip blocks with kind `tip` (Q2).

### D5. The stamp wire: `POST /write/v1/merge-stamp`

A new module `serve/write_api.py`:
- `WriteApi(ReadApi)` reuses 007's plumbing (`_guarded`, `_error`, `_principal`, `_read_store`, `_envelope`) and adds
  only its own routes;
- the daemon builds it next to `ReadApi` and passes both route lists to `build_app(routes=…)`;
- `config.validate` refuses a `--path` under `/write`, as it does for `/read`.

| | |
|---|---|
| Routes | `POST /write/v1/merge-stamp`; a catch-all `/write/{rest:path}` answering `unsupported-version` / `unknown-view` / `method-not-allowed` exactly as `ReadApi.unknown` does |
| Layers | every 001/006 layer (D5 of 007): 401 before any body read, Host/Origin guard, `PrincipalLimits` (one token per request) |
| Body | `Content-Length` required and at most 4 KiB (`WRITE_MAX_BYTES`), else 411/413 before reading; JSON object; keys within `{"branch", "target", "sha", "merged_at", "clone", "checked"}`, any other key 400 |
| Fields | `branch`, `target`: `valid_ref`. `sha`: `^[0-9a-f]{40}([0-9a-f]{24})?$`. `merged_at`: `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$` (the store's minute format, UTC, D10). `clone`: optional, `^c-[0-9a-f]{16}$` (§11). `checked`: optional, exactly `{"against": <sha>}` |
| Session | the tool call runs under a new channel, `<handle>@hook`: `claims.CHANNELS` gains `"hook"`, and `SessionRegistry.current()` answers a `ChannelSession` when the ASGI scope carries `HOOK_CHANNEL_KEY`, as it does for `READ_CHANNEL_KEY`. **The clone rides along (S1):** `ChannelSession` gains `clone: str = ""`; the route puts the validated body `clone` in the scope under `HOOK_CLONE_KEY` next to `HOOK_CHANNEL_KEY`; `current()` copies it into the hook channel's `ChannelSession`. 008's code reads it nowhere; 011's envelope does (011 D2) |
| Reply | the v1 envelope (`{"v": 1, "view": "merge-stamp", "store_id", "revision", "generated_at", "data"}`) with `data.outcome` one of `recorded`, `already`, `check`, `no-task`, `ambiguous`, `refused` (D6) |
| Errors | 007 D5's error envelope and codes; messages through `scrub` |
| Headers | `Cache-Control: no-store`, `X-Request-Id` |

The client gains `Client.write(view, body, *, deadline) -> ReadResult`:
- it POSTs JSON to `server.write_base + view`, where `ServerRef.write_base` is `origin + "/write/v1/"`;
- it reuses `_fetch`, `_exchange` and `_envelope`, and never touches the cache or its negative entries;
- `WRITE_VIEWS = ("merge-stamp",)`, with a 6 s budget, capped by the hook deadline.

### D6. Applying a stamp on the server: the server compares, it never decides ancestry

`WriteApi.merge_stamp`:

1. Validate the body (D5), then read the plan in one read transaction:
   `hook_reads.merge_stamp_plan(con, branch, target, principal, clone=None) -> {"ambiguous", "candidates", "task_id",
   "rung", "current"}`. It reuses `tasks_for_branch`, `choose_task` and `merge_ladder` as `merge_target_view` does,
   plus `current`: the chosen task's `merge_status[rung]` as `{"merge_commit", "merged_at"}`, or `null`. **The server
   resolves the task and the rung at apply time** (007 M12), so a queued stamp uses today's ladder.
2. Answer without writing:
   - no task: `{"outcome": "no-task"}`;
   - ambiguous: `{"outcome": "ambiguous", "candidates": [...]}`;
   - `current.merge_commit == sha`: `{"outcome": "already", "task_id", "rung"}`;
   - `current` exists and `checked` is absent, or `checked.against != current.merge_commit`:
     `{"outcome": "check", "task_id", "rung", "current": {...}}`. Only the client can tell whether its commit is older,
     because only it has the commits.
3. Otherwise write, with the route's ASGI scope flagged for the `hook` channel:

   ```
   call_tool("backlog_record_merge",
             {"task_id", "rung", "sha", "merged_at", "expect_commit": current.merge_commit if current else ""},
             run_middleware=True)
   ```

   This runs under 005's tool gate, 003's session, 004's scoping and 006's sanitizer, exactly as over MCP. The tool's
   answer decides the outcome:
   - text starting `Recorded merge for rung` gives `{"outcome": "recorded", "task_id", "rung"}`;
   - any other text: re-read the plan once. If `current` moved (a stamp landed in between), answer `check` with the
     new `current`; if it didn't, answer `{"outcome": "refused", "message": <scrubbed tool text>}`.
   - The route loops at most twice, so it holds no store lock and runs no unbounded retry.

**`expect_commit`**, a compare-and-set added to the existing command (no new operation):
- `task.merge` arguments allow an optional `expect_commit` (text) (`workflow.py` `_keys`).
- `_task_merge` compares it with the rung's stored `merge_commit` (`""` when none) before `record_merge_doc`, and
  raises `Conflict("rung `<r>` of `<id>` records <current or nothing>, not <expected or nothing>")` on a mismatch.
  That check is inside the coordinator's write transaction, so it is atomic with the write.
- `backlog_record_merge(task_id, rung, sha, merged_at="", expect_commit: str | None = None)`:
  - `None` keeps today's idempotent overwrite;
  - text passes through to the command (native adapter, `native_routing/tasks.py:record_merge`);
  - the legacy body compares the same way, for parity;
  - one docstring line: "expect_commit: record only if the rung's current merge commit is this one ('' = none
    recorded); otherwise refused. Used by team-server hooks."

### D7. Client-side ancestry: the two-phase stamp

`taskmaster/remote/stamps.py` (new, stdlib) holds `remote_apply(session, log, deadline) -> apply(entry)`, an `apply`
for `merge_stamps.replay_queue` with its exact contract: it returns `"applied"`, `"dropped"`, `"retry"` or
`("reject", reason)`, or raises `merge_stamps.Unavailable`. For one entry it loops at most 3 rounds:

| Server `outcome` | Client action |
|---|---|
| `recorded` | `"applied"` |
| `already` | `"dropped"` (log "already recorded") |
| `no-task` | `"dropped"` (log "no task uses branch …", as the solo recorder returns silently today) |
| `ambiguous` | `("reject", "several tasks use branch … (ids)")` |
| `refused` | `("reject", message)` |
| `check` | `merge_stamps.superseded(entry, current, merge_stamps.git_is_ancestor(entry["repo"], timeout=…))`. True: `"dropped"` (log "superseded by <current>"). False: set `checked = {"against": current.merge_commit}` and send again |
| 3 rounds of `check` | `"retry"` |
| `ReadUnavailable` `unreachable`, `busy`, `server`, `bad-response`, `refused`, `no-key`, `config`, `version` | raise `Unavailable(kind: message)`: the entry stays queued (a fixed key or an upgraded plugin replays it) |
| `ReadUnavailable` `bad-request` | `("reject", message)` |

- **The ancestry verdict comes from the teammate's own clone.** `git_is_ancestor` runs `git -C <repo> merge-base
  --is-ancestor`, where `repo` is the clone the merge happened in (stored in the entry).
- `superseded`'s existing fallback applies when Git can't answer, for example when the recorded commit is a teammate's
  that this clone lacks: `merged_at` decides, and a same-minute record wins. That's today's rule, unchanged.
- `git_is_ancestor(root, timeout=10)` gains the `timeout` keyword, so the hook passes its remaining budget (at least
  0.5 s).
- The result that travels is `checked.against`: "I compared my commit with this recorded one and mine is not older".
  The server only checks that the recorded commit is still that one.

### D8. The client queue (the solo queue, reused)

`merge_stamps` is upgraded in place so that one module serves both queues:

- `QueueDir(path, log_path)`, a tiny `os.PathLike` wrapper. `_local(where)` returns `where` itself for a `QueueDir`,
  and `<backlog_dir>/local` otherwise, so every solo caller is unchanged. `hook_log` writes to `log_path`, which the
  client sets to hookside's `hooks/hook.log`. That keeps one log per server, and a client entry never logs into
  `.taskmaster/`.
- `replay_queue(where, apply, *, log=None, deadline=None, required=REQUIRED, label=None, max_age=None)`.
  - The client passes `required=("branch", "target", "sha", "repo")`.
  - It also passes a label of `branch→target sha[:12]`.
  - It passes `max_age=30 days`.
  - The defaults keep the solo behaviour.
- The client queue lives in `<cache>/remote/<server_id>/stamps/`, created with `dirs.private_chain` (owner-only).
  - It is per server, not per key, so a rotated key replays what the old one queued; entries hold no secret.
  - `hookside.stamp_queue(server) -> QueueDir | None` returns it. None (a link or a foreign owner) means the stamp
    isn't queued, and one log line says so.
- Entry: `{"branch", "target", "sha", "merged_at", "repo", "queued_at"}`, plus `clone` once 011 lands (§11).
- **Age cap**: with `max_age` set, `replay_queue` rejects an entry whose `queued_at` is older than that. The client
  sets 30 days, so its queue can't grow for ever while a key stays refused; solo passes none.

`merge_recorder_stamp.stamp_remote` becomes:

1. Take every merged branch from the shared parser (D11; one stamp per branch of an octopus merge), and check each
   with `ref_problem` (D4).
2. Build the entry: `merged_at` is UTC minute precision (D10), and `repo` is `taskmaster.root._git_checkout_root(cwd)`,
   falling back to the root.
3. Run `remote_apply` for the current stamp within `CURRENT_STAMP_SECONDS` (4 s).
4. On `Unavailable` or `"retry"`, `enqueue` it and log "queued for the server".
5. Then `replay_queue` the older entries until `_STARTED + STAMP_BUDGET_SECONDS` (7 s). This is today's solo ordering:
   the merge that just happened goes first.

`merge-target` is no longer read by the recorder. The view stays served (007 D2: removing a v1 view is v2).

### D9. No host Git for a teammate's stamp

- The write route never calls `git_is_ancestor`, `native_apply` or `replay`. A spy test fails if any `git` process
  starts while a stamp is applied (Step 7).
- **No host replay in a serve daemon.** `registry._replay_merge_stamps` returns at once when
  `coordinator.client.serving()` is true. The host clone's hooks don't run there (no agent works on the host, and
  014 D13 guards the host HEAD), so its queue stays empty. If it ever isn't, the operator drains it with
  `backlog_record_merge`. Otherwise a member's MCP call could start a host `git merge-base` (Q6).
- 011 §6 inventories the remaining host Git paths and pins them with a ratchet.

### D10. Time

- Client entries carry `merged_at` in UTC, in the store's minute format (`YYYY-MM-DDTHH:MM`), from
  `time.gmtime()`. Today the solo `now_stamp` uses local time.
- The host runs UTC (016: Hetzner, Ubuntu). Teammates in other zones would otherwise compare wrongly in `superseded`'s
  fallback.
- 007's recorder stub wrote seconds plus `Z`; that format never reached a store.

### D11. Parsing the command: one shared parser

`hooks/merge_command.py` (new, stdlib) replaces `merge_gate.parse_src_branch`, `merge_recorder.parse_src_branch`
(today a copy of the first) and both copies of `GIT_MERGE_RE`.

- **`prefilter(command) -> bool`**: the raw text contains `git` and one of `merge` or `pull`. It is a substring test,
  so the hot path stays builtins-only and spawn-free, as today.
- **`parse(command) -> Parsed(merges: list[Merge], unsure: str | None)`**, where
  `Merge(kind: "merge" | "pull", branches: list[str], directory: str | None)`.
  1. **Tokenize**, after a quote-aware pass that turns unquoted newlines into `;`, with exactly:

     ```python
     lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
     lexer.whitespace_split = True
     lexer.commenters = ""
     tokens = list(lexer)
     ```

     - `whitespace_split` keeps a branch name one token even when it holds a character outside shlex's word set:
       `fix@v2`, `feat+x`, `release#1` and the refspec `+feature/x:feature/x` each stay whole (rev 3, review I1-r2).
       `&&`, `||`, `;`, `|`, `&`, `(` and `)` still split, also when glued to words (`a&&git push`, `x;echo y`).
     - `commenters = ""` stops shlex dropping everything after a `#` inside a word. Bash keeps `release#1` whole, and
       so does this.
     - A token that **starts** with `#` (outside quotes) starts a comment, as in Bash. It and the rest of its line are
       dropped.
     - Unbalanced quotes (`ValueError`) give `unsure`.
  2. **Split into segments** at `;`, `&&`, `||`, `|`, `&`, `(` and `)`.

     **Redirections are dropped by a rule over the tokens.** With the step 1 setup, a redirection always comes out as
     separate tokens. Probed:

     | Input | Tokens |
     |---|---|
     | `2>/dev/null` | `2`, `>`, `/dev/null` |
     | `2>&1` | `2`, `>&`, `1` |
     | `feature/x>out.log` | `feature/x`, `>`, `out.log` |
     | `>>log` | `>>`, `log` |
     | `&>log` | `&>`, `log` |

     The rule:
     - An operator token `>`, `>>`, `>&` or `<`, and also `<&`, `&>`, `&>>` and `>|`, is dropped together with the
       **next** token (its target).
     - A **digit-only** token **right before** such an operator is dropped too (the file descriptor).
     - Every other token stays.

     So `git merge feature/x 2>&1 | tail -5` keeps exactly `feature/x`, and `git merge feature/x>out.log` keeps
     `feature/x`.

     The tokens don't record whether a digit was glued to the operator. `git merge x 2 >f` therefore also drops the
     `2`, which Bash would pass as a branch. That is accepted, because `2` is not a plausible branch name.
  3. **In each segment**, skip leading `NAME=value` assignments and the wrappers `env`, `command`, `builtin`, `exec`,
     `time` and `nice`. The program counts as Git when its basename, without `.exe`, is `git`.
  4. **Git's global options** are skipped with their values:
     - `-C <dir>`, which sets `directory`, and several `-C` compose;
     - `-c <k=v>`, `--config-env[=]`, `--git-dir[=]`, `--work-tree[=]`, `--namespace[=]`, `--exec-path[=]`;
     - `--no-pager`, `-P`, `-p`, `--paginate`, `--bare`, `--no-replace-objects`, `--no-optional-locks` and the
       `--*-pathspecs` flags.

     An unknown global option gives `unsure`.
  5. **The subcommand must be exactly `merge` or `pull`.** `merge-base`, `merge-tree`, `merge-file`, `mergetool` and
     everything else is not a merge.
  6. **`merge` arguments.**
     - Skip the options that take a separate value: `-m`, `-F`, `-s`, `-X`, `--message`, `--file`, `--strategy`,
       `--strategy-option`, `--into-name` and `--cleanup`. Their `=` and attached forms (`-mmsg`) carry the value
       inside the token.
     - `--` ends the options.
     - `--abort`, `--continue`, `--quit` and `--skip` merge nothing.
     - Every remaining positional is a branch: an octopus merge gates each one.
     - `-` (the previous branch) gives `unsure`.
     - Anonymous refs (`ANON_REF_RE`: SHAs, `HEAD~`, `@{…}`, `FETCH_HEAD`…) are skipped, as today.
     - No positional at all means the merge takes the upstream. It is not gated (Q8).
  7. **`pull` arguments.**
     - Skip the value options `-s`, `-X`, `--strategy`, `--strategy-option`, `--depth`, `--deepen`, `--shallow-since`,
       `--shallow-exclude`, `-j`, `--jobs`, `--upload-pack`, `-o`, `--server-option`, `--negotiation-tip` and
       `--refmap`.
     - The first positional is the repository. Each later one is a refspec `[+]src[:dst]`, and its `src` is gated.
     - A pull with no refspec updates the current branch from its upstream. It is not gated (Q8).
  8. **`unsure`** is also set when:
     - a segment holds `$(` or a backtick, or is `eval`, `xargs`, `sh -c`, `bash -c`, `cmd /c`, `pwsh -c` or
       `powershell -Command` with an argument containing `git` and `merge` or `pull`;
     - a segment's program contains `$` and a later token is exactly `merge` or `pull`.
- **Names checked for each branch** (the gate allows only when every name of every branch allows):
  - the name as written;
  - `refs/heads/X` checks `X`;
  - `refs/remotes/<r>/X` and `<r>/X` check `X` too, when `<r>` is one of the clone's remotes. `git remote` runs once
    through `run_bounded`, only when a name has a `/`.

  Merging a fetched teammate branch as `origin/feature/x` is the usual way to integrate one on a team, and today it
  asks about a branch no task uses.
- **Who uses it.**
  - `merge_gate.py` gates every branch of every merge segment.
  - In server mode, `unsure` blocks with kind `unparsed` (D1). In solo it allows, keeping solo fail-open, and solo's
    gate now also catches the `-m msg` and compound-command merges it used to miss (Q8).
  - `merge_recorder.py` stamps every branch of every merge segment that succeeded, reading HEAD in the segment's `-C`
    directory.
- **Tests.** The command table in Step 3 is the contract. Every row of the review's probe table is in it.

### D12. The approval flow, hardened

Both scripts are taskmaster's own, so 008 changes them. It is one flow for solo and server, so solo gets the same
hardening.

1. **The writer** (`taskmaster_merge_approve.py`) records an approval only when all of these hold:
   - an answer is exactly "Approve" (trimmed, any case, as today) to a question whose text (its key in
     `tool_response.answers`) starts with `[merge-gate <branch>]`;
   - the hook input has a real `session_id` matching `[A-Za-z0-9_-]{1,128}`. There is no more `-default` fallback.

   It appends `{"branch": <branch>, "at": <epoch>}` to `~/.claude/taskmaster-merge-approve-<session>` with one
   `O_APPEND` write. An "Approve" to any other question writes nothing.
2. **One name for the block and the approval** (rev 3). The name is the branch **as written in the command**, the
   D11 branch the block was raised on: `origin/feature/x` stays `origin/feature/x`, even when the verdict came from
   `feature/x`. The block text's `[merge-gate {branch}]`, the writer's recorded `branch`, and the `branch` argument of
   `check_approval` are all that same string. So rerunning the same command after an approval always matches.
   `check_approval` runs once per written branch, before that branch's decision; an octopus merge needs an approval
   for each branch that blocked.
3. **The reader** (`merge_gate.check_approval(path, branch)`) honours only a line for the branch being merged, at most
   60 s old by its `at`, in a file whose mtime is also inside the window. It needs a real `session_id`. A file whose
   lines have all expired is removed, as today's expiry cleanup does.
4. **Obvious forgeries are refused.** `merge_gate.py` exits 2 for any Bash command whose text contains
   `taskmaster-merge-approve`, in every mode and before the prefilter, with: `the merge-gate approval file is written
   only by the approval hook, from the user's answer; ask the user instead.` That is one substring test.
5. **Not added: a Write/Edit guard on the path.** Every Write and Edit would pay an interpreter start (5-7 s under
   load, 007 R5) for a check that an obfuscated shell command defeats anyway (Q9). This is the residual, G8.
6. **Block texts** in both modes name the `[merge-gate <branch>]` rule (D2), with the written name (item 2).

## 5. Failure modes

| Condition | `merge_gate_decide` (server mode) | `merge_recorder_stamp` (server mode) |
|---|---|---|
| no marker | solo, unchanged | solo, unchanged |
| marker or latch unreadable | BLOCK `config` | log, not queued (no server to queue for) |
| no key / refused | BLOCK `no-key` / `refused` | queued; replayed after `set-key` |
| unreachable, deadline, 429, 5xx, bad reply, version | BLOCK per kind | queued |
| 400 | BLOCK `bad-request` | rejected (`stamps/merge-stamps-rejected.jsonl`), logged |
| ambiguous | BLOCK `ambiguous` | rejected, logged with the candidates |
| no task on the branch | ALLOW | dropped, logged |
| fresh pass at the tip / `any` freshness / skip | ALLOW | n/a |
| stale or failing gate | today's verdict BLOCK (+ teammate note) | n/a |
| branch name `ref_problem` refuses | BLOCK `ref` | logged, not queued |
| tip unreadable | BLOCK `tip` | n/a (the SHA is an argument) |
| exception | BLOCK `internal` | logged, never raises (today's rule) |
| launcher passes 9 s | watchdog BLOCK (the host's kill is at 30 s, D3.0) | n/a (PostToolUse; the recorder already bounds itself to 7 s) |
| root not found | BLOCK `config` | log, not queued |
| `data` malformed, or `source` not `server` | BLOCK `bad-response` | `bad-response`: queued |
| command not parseable with confidence | BLOCK `unparsed` | logged, nothing stamped |
| octopus merge | each branch gated; any block blocks | one stamp per branch |
| `git -C <dir> merge X` | judged in `<dir>` | stamped from `<dir>` |
| `git merge origin/feature/x` | also checks `feature/x` | also stamps `feature/x`'s task |
| recorded commit unknown to this clone | n/a | `merged_at` fallback (D7) |
| a stamp landed between plan and write | n/a | `check` again (CAS), at most 3 rounds, then queued |

## 6. Threat model (additions to 007 §3)

| Attacker | Wants to | Stopped by |
|---|---|---|
| A member key | stamp a merge on someone else's task | Same power as over MCP: `backlog_record_merge` is a member tool today, and the route runs it through the tool gate. A stamp carries no claim. The audit event names the actor (team-identity-002) |
| same | make the host run Git on a chosen SHA | the route never runs Git (D9 spy); arguments are a SHA pattern and ref names only |
| same | overwrite a newer stamp by lying in `checked.against` | it can only overwrite the commit it names, which it could do over MCP with plain `backlog_record_merge`; nothing new |
| same | flood writes | one rate token per request; 4 KiB bodies; at most two plan reads per request |
| An agent | bypass the gate on its own | Partly. The approval is bound to the user's AskUserQuestion answer to a `[merge-gate <branch>]` question, in a real session, for that branch, for 60 s, and a Bash command naming the approval file is refused (D12). **Residual (G8):** an agent that deliberately writes the file another way (the Write tool, an obfuscated shell command) can still forge an approval. A check running as the same user is no boundary against that user's own agent. Routed to 015's security review |
| An agent | slip a merge past the parser (`&& git push`, `-m msg`, `$(…)`, `sh -c`) | D11: compound commands are split, option values skipped, and anything unreadable blocks in server mode |
| A slow network or a loaded machine | turn the gate into a hook kill (= allow, Step 0a) | 30 s hook timeout, 8 s read deadline, 9 s watchdog, one interpreter (D3): at least 14 s of margin over the measured start-up |

## 7. Reuse map

| Need | Reused and upgraded | New |
|---|---|---|
| failure kinds, deadline, transport | `remote/client.py` (`Client.write` beside `read`, same `_fetch`) | none |
| locate, key, logs | `remote/hookside.py` (+ `stamp_queue`), `remote/locate.py` (+ `write_base`) | none |
| queue, claim, lock, replay | `native_routing/merge_stamps.py` (`QueueDir`, `required`, `label`, `timeout`) | none |
| supersede rule | `merge_stamps.superseded` (unchanged, now run client-side) | none |
| server plumbing | `serve/read_api.py` (`WriteApi` subclasses `ReadApi`) | `serve/write_api.py` |
| task and rung choice | `hook_reads.tasks_for_branch`, `choose_task`, `merge_ladder` | `merge_stamp_plan` |
| write | `backlog_record_merge` → `task.merge` (+ `expect_commit`) | none |
| bypass | `merge_gate.py` approval flow and `taskmaster_merge_approve.py` (hardened, D12) | one guidance text |
| ref rule | `read_api.valid_ref`'s rule moved to `hook_reads.ref_problem` | none |
| command parsing | the two `parse_src_branch` copies and `GIT_MERGE_RE`, folded into one | `hooks/merge_command.py` |
| bounded Git call | `bounded_run.run_bounded` (now also for `_git_rev_parse` and `git remote`) | none |
| the client's apply | | `remote/stamps.py` (about 80 lines) |

## 8. Files

New:
- `taskmaster/serve/write_api.py`, `taskmaster/remote/stamps.py`, `hooks/merge_command.py`;
- tests `tests/test_merge_gate_server_mode.py`, `tests/test_serve_write_api.py`, `tests/test_remote_stamps.py`,
  `tests/test_merge_command.py`.

Changed:
- `hooks/hooks.json` (the merge-gate timeout, 30 s);
- `hooks/merge_gate.py`, `hooks/merge_gate_decide.py`, `hooks/merge_recorder.py`, `hooks/merge_recorder_stamp.py`,
  `hooks/taskmaster_merge_approve.py`;
- `taskmaster/native_routing/hook_reads.py` (`REF_RE`, `merge_stamp_plan`), `taskmaster/native_routing/merge_stamps.py`,
  `taskmaster/native_routing/registry.py`, `taskmaster/native_routing/tasks.py` (`record_merge`);
- `taskmaster/native/workflow.py` (`task.merge`), `taskmaster/backlog_server.py` (`backlog_record_merge` signature
  and legacy body);
- `taskmaster/native/claims.py` (`CHANNELS`), `taskmaster/serve/sessions.py` (`HOOK_CHANNEL_KEY`, `HOOK_CLONE_KEY`,
  `ChannelSession.clone`), `taskmaster/serve/read_api.py` (`valid_ref` wraps `ref_problem`), `taskmaster/serve/daemon.py` (builds `WriteApi`),
  `taskmaster/serve/config.py` (`/write` prefix);
- `taskmaster/remote/client.py`, `taskmaster/remote/locate.py`, `taskmaster/remote/hookside.py`;
- tests: `tests/test_hooks_server_mode.py` (007's fail-open tests flip), `tests/test_merge_gate_hook.py`,
  `tests/test_merge_approve_hook.py`, `tests/test_merge_recorder_hook.py`, `tests/test_hooks_json.py`,
  `tests/test_record_merge.py`, `tests/test_merge_stamp_contract.py`;
- the `backlog_record_merge` entry in `tests/fixtures/native_contracts.json`, which records each tool's documentation
  and parameters.

## 9. Known gaps

- **G1. A hook killed before its own watchdog.** Step 0a confirmed that a killed PreToolUse hook lets the command
  run. With the 30 s timeout (D3.0), a kill needs more than 21 s between the host spawning `run_hook.sh` and the
  launcher's first statement, against 5-7 s measured under load. The residual is a machine stalled for over 20 s. It
  is accepted for go-live and logged nowhere, because the process is gone.
- **G2. A broken plugin fails open.** When `run_hook.sh` finds no script or no interpreter, it exits 0 by its own
  policy, solo and server alike. A broken guard must not deny every Bash call.
- **G3. Merges outside Claude Code** (the user's shell, other tools) are not gated, as in solo.
- **G4. A branch name `ref_problem` refuses** is gated only by the user's approval, and its stamp isn't recorded.
- **G5. The shared ladder records a teammate's local, unpushed merge,** as solo records an unpushed merge. 011 adds a
  per-clone record (011 D7).
- **G6. `backlog_record_merge` over MCP** stays the manual overwrite it is today (no `expect_commit` unless the caller
  passes one).
- **G7. Clocks.** The fallback compares minutes in UTC; skewed clocks can order two same-minute stamps wrongly, as they
  can in solo.
- **G8. The approval file can still be forged deliberately.** D12 binds an approval to the user's answer and refuses
  Bash commands that name the file. An agent running as the user can still write it through the Write tool or an
  obfuscated shell command, and no check running as that same user can stop that. The gate guards against mistakes
  and instruction drift, not against a hostile agent on the teammate's own machine. Routed to 015's security review as
  a known limit.
- **G9. Out of the client's reach.**
  - A teammate on a plugin older than 008 gets 007's fail-open gate.
  - A disabled or uninstalled plugin gates nothing.
  - 016's how-to pins the minimum plugin version for teammates.
- **G10. Merges the parser doesn't gate on purpose:**
  - `git merge` or `git pull` with no named branch (the upstream of the current branch);
  - Git aliases (`git m feature/x`);
  - a merge run inside a script file.

  Commands with `$(…)`, `sh -c`, `eval` and similar block in server mode (D11).

## 10. Open questions (defaults are the recommendations)

- **Q1. Bypass.** Only the approval flow, hardened (D12) *(default)*, versus also an environment variable. The agent
  can set a variable as easily as the user. The approval file has the same weakness against a deliberate forgery
  (G8), which D12 narrows but can't close.
- **Q2. Unreadable tip in server mode.** Block *(default)*, versus allow as solo does. A merge of an unresolvable ref
  fails anyway, so a block costs little; allowing would be a fail-open path left inside a fail-closed gate.
- **Q3. A teammate's task on the branch.** Block with the teammate wording *(default)*, versus ignoring tasks that
  aren't the caller's. Ignoring them would let anyone merge a colleague's ungated branch.
- **Q4. Ambiguous stamp.** Reject and log *(default)*, versus stamping every candidate.
- **Q5. `merged_at` in UTC** for client stamps *(default)*, versus local time as solo writes it.
- **Q6. The serve daemon stops replaying its own queue** *(default)*, versus replaying with host Git. Default because
  of addendum §5.
- **Q7. Build order with 011.** 008 first *(default; 008 is go-live and 011 isn't)*. 011 first also works by §11's
  rule.
- **Q8. What the shared parser gates.**
  - Named branches of `merge` and named refspecs of `pull` *(default)*.
  - A bare `git pull` or `git merge` is not gated: it integrates the current branch's upstream, which is not a task
    branch. Gating it would block every plain `git pull` while the server is down.
  - In solo, the new parser also gates the `-m msg` and compound-command merges it used to miss. That is stricter,
    and arguably a fix.
- **Q9. No Write/Edit guard on the approval path** *(default)*, versus a PreToolUse entry on Write|Edit|MultiEdit.
  The guard would charge every edit an interpreter start (5-7 s under load) for a check that a shell command defeats
  anyway.

## 11. The seam with 011 (settled here, mirrored in 011 §10)

Both tasks move Git work to the client and both touch merge stamps. Ownership:

| Piece | Owner | Notes |
|---|---|---|
| Gate fail-closed, launcher, bypass, texts, parser, approval (D1-D4, D11, D12) | 008 | 011 touches none of it |
| **The hook channel carries the clone (S1):** `ChannelSession.clone`, `HOOK_CLONE_KEY`, filled by `current()` from the route's validated body `clone` | **008** | 011 D2 reads the clone from whichever session is bound (`RemoteSession` or the hook `ChannelSession`), so a hook stamp's per-clone record lands under its real clone |
| `/write/v1/merge-stamp`, `Client.write`, `remote/stamps.py`, the client queue (D5, D7, D8) | 008 | |
| `expect_commit` on `task.merge` / `backlog_record_merge` (D6) | 008 | |
| No host replay in a serve daemon (D9) | 008 | 011's ratchet relies on it |
| `merge_stamp_plan` | 008 | 011 makes its candidate search clone-aware (011 D6: with a principal it calls `branch_candidates`) |
| **The `clone` wire parameter**: optional `clone` (`^c-[0-9a-f]{16}$`) on `GET merge-gate`, `GET merge-target` and the `merge-stamp` body, validated and passed to `hook_reads` as `clone=None` keywords that 008's code ignores | **008 defines it** | so 011 changes no wire contract |
| Clone identity (`remote/clone.py`), hooks sending `clone`, the MCP header | 011 | |
| `clone_fields` table, the write diversion, the read overlay | 011 | |
| Clone-aware `branch_candidates` / `choose_task(own=)` | 011 | `tasks_for_branch` stays the solo lookup; 011 switches the three server views (`merge_gate_view`, `merge_target_view`, `merge_stamp_plan`) to `branch_candidates`, one line each |
| Per-clone merge record written by `task.merge` | 011 | keyed on the envelope's `clone`; independent of `expect_commit` |
| Host Git inventory and ratchet | 011 | covers 008's route through D9's spy |
| 022 (export branch out of the drift walk) | neither | host-side managed Git; 011 §9 explains |

**Files both edit**, and how a second lane rebases:
- `hook_reads.py`: 008 adds `REF_RE` and `merge_stamp_plan`, and threads `clone` keywords; 011 adds
  `branch_candidates`, extends `choose_task`, and changes one call line in each server view.
- `merge_gate_decide.py` / `merge_recorder_stamp.py`: 008 rewrites the server branches; 011 adds one `clone` argument
  to each request and entry.
- `workflow.py` `task.merge`: 008 adds `expect_commit` to `_keys`; 011 writes the per-clone record in `_task_merge`
  from `transaction.request["clone"]`. These are adjacent lines.
- `serve/sessions.py`: 008 adds the `hook` channel; 011 adds the clone header to `bind`. These are different branches
  of `current()`.

**Order.**
- 008 then 011 *(default)*: 011's Step 2 only sends `clone` from the hooks.
- 011 then 008: 011's Step 2 first adds the `clone` parameter to `merge-gate` and `merge-target` exactly as the table
  above defines it (validated, passed as `clone=`). 008's Step 7 then accepts `clone` in the stamp body the same way
  and puts it on the hook channel, and 008's Step 8 sends it from the hook when `taskmaster.remote.clone` exists (it
  does, after 011).
- Interleaved: either order per step pair.

## 12. Risks

- **R1. Watchdog and approval race:** the user approves during a slow read. `check_approval` runs before every block,
  the watchdog's too, so a retry after approval is allowed.
- **R2. In-process import cost.** `merge_gate_decide` imports `taskmaster.native_routing.hook_reads` and
  `taskmaster.remote`. Step 0b measures the cold import under `-X importtime`. The watchdog is armed before the import
  (D3.4), so a slow import is a block in server mode, never a kill.
- **R3. Tool text prefix** (`Recorded merge for rung`) as the success signal. It is this build's own tool; a test pins
  it, and any other text re-reads the plan instead of guessing.
- **R4. 007's fail-open tests** flip in Step 2. They are rewritten, not deleted, so each failure kind keeps a test.
- **R5. Description budgets.** The `backlog_record_merge` docstring grows by one line;
  `test_skill_description_budgets.py` and `test_server_instructions.py` run in Step 6.

## 13. Rollback

Revert the step commits. There is no schema change: `expect_commit` is an optional argument, and the queue files live
in the user's cache dir. With 008 reverted, server-mode hooks are back to 007's fail-open behaviour, and no key may be
issued (go-live rule).

## 14. Regression set (Step 10)

`test_merge_gate_hook.py`, `test_merge_gate_decide.py`, `test_merge_recorder_hook.py`, `test_merge_stamp_contract.py`,
`test_record_merge.py`, `test_merge_approve_hook.py`, `test_native_routing_hooks.py`, `test_hooks_server_mode.py`,
`test_hook_views.py`, `test_remote_client.py`, `test_remote_cache.py`, `test_serve_read_api.py`, every
`tests/test_serve_*.py` in slices, `test_session_scope.py`, `test_claim_ownership.py`, `test_packaging.py`,
`test_hooks_json.py`, `test_hook_shims.py`, `test_skill_description_budgets.py`, `test_server_instructions.py`,
`test_native_contracts.py`, plus the four new files. The full suite is not run on this machine (rulings: test runs);
the orchestrator runs it once on feat/team-collab before release.

## 15. Not changed

`edit_resurface`, the mods, `tm_read.py`, the read views' `data` shapes, the cache policy, `run_hook.sh`. The solo
hooks change only where they share code with server mode: the parser (D11, Q8), the approval flow (D12), the solo
decision's 9 s timeout (D3.0) and `run_bounded` for the tip.

## 16. Implementation plan (TDD)

For each step: write the named tests first and watch them fail, then implement, run the proving check, and commit.
- Tests run from the worktree with `C:\Users\gruku\Files\Claude\taskmaster\.venv\Scripts\python -m pytest …`, in the
  foreground, at `-n 2` at most, with `--basetemp` under `D:\scratchbook\taskmaster\ts008\`. The bare `python` is the
  wrong interpreter (memory `suite-interpreter-trap`).
- Scratch and spikes also go under `D:\scratchbook\taskmaster\ts008\`.
- Don't wait on a background pytest (memory `teammate-background-notice-stall`).

**Step 0. Spikes (not committed; results go in Step 1's commit message).**
- (a) **Done during design** (header, "Spike result"): a killed PreToolUse hook lets the command run. If a later
  Claude Code changes this, re-run `D:\scratchbook\taskmaster\ts008\spike-timeout\` with
  `env -u ANTHROPIC_API_KEY claude -p "<prompt>" --model haiku --allowedTools=Bash`; the prompt must come before the
  variadic `--allowedTools`.
- (b) `python -X importtime hooks/merge_gate.py` cold, with an in-process import of `merge_gate_decide` and
  `taskmaster.remote.client`. Record the total.

**Step 1. `ref_problem`, `check_merge_gate_view`, `merge_stamp_plan` and the `clone` keywords.**
Files: `native_routing/hook_reads.py`, `serve/read_api.py`. Tests first, in `tests/test_hook_views.py` (extended):
- `ref_problem` refuses everything `valid_ref` refused, with the same names: the subset, `..`, a leading `-` or `/`,
  a trailing `/`, and over 255 characters;
- `check_merge_gate_view` accepts every view the fixture stores produce, and names the field for each malformed case
  (`policy_required` missing or not a bool, a non-dict `task`, `id` not a string, a bad `merge_gate_freshness`, a
  non-dict `review_gate`);
- `merge_stamp_plan` on the 007 fixture store:
  - a ladder rung and `branch:<x>`;
  - `current` null, then the recorded commit after a stamp;
  - D3a ambiguous and no-task;
- the read views and `merge_stamp_plan` accept `clone=` and answer exactly as without it;
- `read_api.valid_ref` still refuses what it refused (its existing tests unchanged).

Proving check: `test_hook_views.py`, `test_serve_read_api.py`.

**Step 2. The gate fails closed (`merge_gate_decide`).**
Files: `hooks/merge_gate_decide.py`. Tests first: `tests/test_merge_gate_server_mode.py` (new) for the unit cases,
using a fake `hookside.connect` and a fake client that raises each kind; `tests/test_hooks_server_mode.py`, where
007's three fail-open tests are rewritten into fail-closed ones against the real daemon.
- Each D1 kind gives exactly its `BLOCK:-: ` text, plus a hook-log line. `ambiguous` names the candidates.
- An exception inside `decide_remote` gives `internal`. A strict tip miss gives `tip`. A name `ref_problem` refuses
  gives `ref`, and no request is made.
- **I3:**
  - `server=True` with `project_root` returning None, and with it raising, gives `config`;
  - a fake client answering a malformed `data` gives `bad-response` for each malformed shape from Step 1;
  - a fake client answering `source="cache"` or `"stale"` gives `bad-response`;
  - `server=True` with a solo-looking root still takes the server branch.
- A verdict block on a task with `mine: false` carries the teammate sentence.
- Policy off, no task, skip, `any` freshness and a fresh pass still ALLOW.
- `server_clone`: marker, latch only, invalid marker (True), no marker (False), resolution failing with a marker file
  in a parent (True).
- The silent-server test (007's 8 s test) now expects a block, still within 9.5 s.
- The solo tests: `test_merge_gate_decide.py` unchanged and green; the solo path still never imports
  `taskmaster.remote`.

Proving check: the two files plus `test_merge_gate_decide.py`.

**Step 3. The shared command parser.**
Files: `hooks/merge_command.py`. Tests first, in `tests/test_merge_command.py`: a table of real command lines, each
with its expected `Parsed`. The review's probe rows are all in it:

| Command | Expected |
|---|---|
| `git merge --no-ff feature/x -m "Merge feature/x: add thing"` | merge `[feature/x]` |
| `git merge --no-ff feature/x -m msg` | merge `[feature/x]` |
| `git merge feature/x && git push` | merge `[feature/x]` |
| `git merge feature/x; echo done` | merge `[feature/x]` |
| `git merge feature/x \| tail -5` | merge `[feature/x]` |
| `git merge-base main feature/x` | no merge |
| `git merge-tree a b`, `git mergetool`, `git commit -m "merge fix"` | no merge |
| `git -C ../other merge feature/y` | merge `[feature/y]`, directory `../other` |
| `git -c core.editor=true merge --no-edit feature/x` | merge `[feature/x]` |
| `git merge -mmsg feature/x`, `git merge --message=msg feature/x`, `git merge -s ours -X theirs feature/x` | merge `[feature/x]` |
| `git merge a b c` (octopus) | merge `[a, b, c]` |
| `git merge -- feature/x` | merge `[feature/x]` |
| `git merge --abort`, `git merge --continue` | no merge |
| `git merge HEAD~1`, `git merge 1a2b3c4d` | no gated branch (anonymous) |
| `git merge -` | unsure |
| `timeout 60 git merge feature/x`, `timeout -s KILL 60 ...`, `nohup ...`, `stdbuf -oL ...`, `sudo ...`, `sudo -u bob ...`, `exec -a foo ...` | merge `[feature/x]` (wrappers with value options, review I2) |
| `if git merge feature/x; then echo ok; fi`, `{ git merge feature/x; }`, `! git merge feature/x`, `while ! git merge feature/x; do ...; done` | merge `[feature/x]` (shell reserved words are skipped like wrappers) |
| `unknownwrap --flag git merge feature/x`, `stdbuf -oL xargs git pull` | unsure (a non-git program runs git later on the line: server mode blocks it as `unparsed`) |
| `git merge` (no branch), `git pull`, `git pull origin` | no gated branch (Q8) |
| `git pull origin feature/x`, `git pull --rebase origin +feature/x:feature/x` | pull `[feature/x]` (the refspec is one token, then `[+]src[:dst]` gives `feature/x`) |
| `git pull origin +a:a b:b` | pull `[a, b]` |
| `git merge fix@v2` | merge `[fix@v2]` (one token) |
| `git merge feat+x` | merge `[feat+x]` (one token) |
| `git merge release#1` | merge `[release#1]` (one token; then `ref_problem` refuses `#`, so server mode blocks with `ref`) |
| `git merge feature/x # merge it` | merge `[feature/x]` (the comment is dropped) |
| `git merge feature/x 2>&1 \| tail -5` | merge `[feature/x]` (`2`, `>&`, `1` dropped) |
| `git merge feature/x 2>/dev/null` | merge `[feature/x]` (`2`, `>`, `/dev/null` dropped) |
| `git merge feature/x >out.log`, `git merge feature/x>out.log` | merge `[feature/x]` (`>`, `out.log` dropped; the glued form tokenizes the same) |
| `git merge feature/x >>log`, `git merge feature/x &>log`, `git merge feature/x <in` | merge `[feature/x]` |
| `git merge feature/x&&git push`, `git merge feature/x;echo y` | merge `[feature/x]` |
| `git merge a b c 2>&1` (octopus) | merge `[a, b, c]` |
| `cd sub && git merge feature/x` | merge `[feature/x]` |
| `GIT_EDITOR=true git merge feature/x` | merge `[feature/x]` |
| `git merge feature/x\ngit push` (a newline) | merge `[feature/x]` |
| `git merge "feature/x` (unbalanced) | unsure |
| `$(echo git) merge feature/x`, `` `git merge feature/x` ``, `sh -c "git merge feature/x"`, `eval "git merge feature/x"`, `echo feature/x \| xargs git merge` | unsure |
| `$GIT merge feature/x` | unsure |
| `git --unknown-global merge feature/x` | unsure |
| `echo "git merge is fine"` | no merge (no git segment; not unsure, because no shell construct) |

Plus: `prefilter` is false for `ls`, `git status` and `npm run merge-check` (none of them names both `git` and `merge` or `pull`), and
true for every row above that names `git` and `merge` or `pull`. Name normalization: `refs/heads/x` → `x`;
`origin/feature/x` → also `feature/x` when `origin` is a remote of a temp repo, and not when it isn't.

Proving check: the new file.

**Step 4. The launcher.**
Files: `hooks/merge_gate.py`, `hooks/hooks.json`. Tests first, in `tests/test_merge_gate_hook.py` (extended), running
the hook as a subprocess with real stdin, and in `tests/test_hooks_json.py`:
- `hooks.json`: the `merge_gate.py` entry's `timeout` is 30, and the other entries are unchanged;
- the parser's rows reach `decide`: `-m msg`, `&& git push` and an octopus gate every named branch, and
  `git merge-base` spawns no decision at all;
- server mode: `unsure` exits 2 with the `unparsed` text, and solo exits 0;
- `git -C <other clone> merge X` is judged in the other clone;
- `git merge origin/feature/x` blocks on `feature/x`'s stale gate;
- In a teammate clone (007's `teammate` fixture), a stale gate exits 2 with the verdict text, and an unreachable
  server exits 2 with `UNAVAILABLE_GUIDANCE`.
- The decision runs in-process: `merge_gate.main()` is called in the test process, with stdin patched and
  `subprocess.run` patched to fail the test if it is called in server mode.
- **Watchdog**: a wrapper script written to `tmp_path` imports `merge_gate`, replaces `merge_gate_decide.decide` with
  a 30 s sleep, and calls `main()`. Run as a subprocess, it exits 2 with the watchdog text between 8.8 s and 9.8 s
  after start. The watchdog calls `os._exit`, so it never runs inside the pytest process. No test hook lives in
  production code. In a solo project, the same wrapper has the subprocess decision replaced by an 11 s sleep: the
  solo `timeout=9.0` gives ALLOW at about 9 s, and the watchdog doesn't block.
- A fresh approval file lets the merge through even when the server is down, and the watchdog doesn't override it.
- Solo: a solo project's decision still runs as a subprocess with today's exit codes, and a crashing decision still
  exits 0.
- A non-merge command still spawns nothing (the existing hot-path test).

Proving check: `test_merge_gate_hook.py`, `test_merge_approve_hook.py`, `test_hooks_server_mode.py`,
`test_hooks_json.py`, `test_hook_shims.py`.

**Step 5. The approval flow, hardened.**
Files: `hooks/taskmaster_merge_approve.py`, `hooks/merge_gate.py`. Tests first, in `tests/test_merge_approve_hook.py`
(today's tests rewritten where they relied on the `default` session or on any question counting):
- "Approve" to `[merge-gate feature/x] Merge anyway?` writes an approval for `feature/x`;
- "Approve" to `Proceed with merge?` or `Pick a color` writes nothing;
- "Deny" writes nothing;
- a missing or malformed `session_id` writes nothing;
- the gate honours the approval for `feature/x` within 60 s, not for `feature/y`, and not after 60 s;
- **the same name everywhere:** `git merge origin/feature/x` blocks on `feature/x`'s stale gate with the text
  `[merge-gate origin/feature/x]`. An "Approve" to that question lets the identical command through, while an
  approval recorded for `feature/x` does not. In an octopus merge with two blocking branches, approving one still
  blocks on the other;
- a file whose lines have all expired is removed;
- `touch ~/.claude/taskmaster-merge-approve-s1`, `echo x > …taskmaster-merge-approve…` and `cp a taskmaster-merge-approve-s1`
  are refused with exit 2 and the D12 text, in solo and in server mode;
- an unrelated Bash command is unaffected;
- both block texts name the `[merge-gate <branch>]` rule.

Proving check: `test_merge_approve_hook.py`, `test_merge_gate_hook.py`.

**Step 6. `expect_commit`.**
Files: `native/workflow.py`, `native_routing/tasks.py`, `backlog_server.py`. Tests first, in
`tests/test_record_merge.py` (extended) and a native case in `tests/test_merge_stamp_contract.py`:
- `expect_commit` equal to the current commit records;
- a different one refuses with the named text and changes nothing;
- `""` means "nothing recorded yet";
- `None` keeps today's overwrite;
- native and legacy answer alike;
- a batch can't smuggle a mismatched `expect_commit` (the command validates inside batches the same way).

Proving check: the two files, `test_native_contracts.py` (with the `backlog_record_merge` fixture entry updated for
the new parameter and docstring line), `test_skill_description_budgets.py`, `test_server_instructions.py`.

**Step 7. The write route.**
Files: `serve/write_api.py`, `serve/daemon.py`, `serve/config.py`, `serve/sessions.py`, `native/claims.py`.
Tests first, in `tests/test_serve_write_api.py` (new; in-process `ServeDaemon` and `serve_helpers` as
`test_serve_read_api.py` does):
- no key is 401 before the body; the operator off-host is 403; a wrong Host is 421; a missing Content-Length is 411;
  over 4 KiB is 413; unknown keys, a bad SHA, a bad `merged_at` and a bad `clone` are 400 without echo;
- `/write/v2/…` is 404 `unsupported-version`; `GET merge-stamp` is 405;
- every `outcome`:
  - `recorded` (first stamp of a rung);
  - `already`;
  - `check` (a recorded commit and no `checked`);
  - `recorded` with a matching `checked.against`;
  - `check` again when `against` is stale;
  - `no-task`, `ambiguous`, `refused` (a stub tool text);
- the race: a second stamp lands between plan and write (a hook in the route between the read and the tool call), and
  the answer is `check` with the new current, never an overwrite;
- the write runs as `<handle>@hook`, and the domain event's actor names the member;
- **S1:** inside the tool call, `bs.current_session()` is a hook `ChannelSession` whose `clone` equals the body's
  `clone`, and it is `""` when the body has none;
- **D9 spy**: `subprocess.Popen` and `bounded_run.run_bounded` patched to fail on any argv whose program is `git`,
  across every outcome;
- a `--path /write/x` config is refused.

Proving check: the new file plus every `tests/test_serve_*.py` (two slices), `test_session_scope.py`,
`test_claim_ownership.py`.

**Step 8. The client stamp path.**
Files: `native_routing/merge_stamps.py`, `remote/stamps.py`, `remote/client.py`, `remote/locate.py`,
`remote/hookside.py`, `hooks/merge_recorder.py`, `hooks/merge_recorder_stamp.py`, `native_routing/registry.py`.

Tests first, in `tests/test_remote_stamps.py` (new) and `tests/test_hooks_server_mode.py`:
- `QueueDir`: enqueue, take, finish and reject work in a plain directory, and nothing appears under any `.taskmaster/`.
  The solo `merge_stamps` tests (`test_native_routing_hooks.py`) are unchanged and green.
- `required`, `label`, and `max_age`: client entries over 30 days are rejected, and solo with no `max_age` keeps
  everything. The client's queue log lines land in `hooks/hook.log`, the only log file per server.
- `merge_recorder.py` uses the shared parser: an octopus merge gives one stamp per branch, a `-C` merge reads HEAD in
  that directory, and `… && git push` stamps `feature/x`, not `push`.
- `remote_apply` against a fake client, one case per row of the D7 table:
  - `check` with an ancestor commit (in a real temp repo) gives `dropped`;
  - `check` with a descendant re-sends with `against`;
  - an unknown commit falls back to `merged_at`;
  - three `check`s give `retry`;
  - each unavailable kind raises `Unavailable`, and `bad-request` rejects.
- `git_is_ancestor(..., timeout=)` is honoured.
- End to end, against the real daemon:
  - a merge in the teammate clone records on the host task, with no `.taskmaster/` in the clone;
  - with the server down, it queues under the cache dir and replays on the next stamp;
  - a stamp older than the recorded one is dropped by ancestry computed in the clone;
  - a branch `ref_problem` refuses is logged and not queued.
- `registry._replay_merge_stamps` does nothing in a serve daemon (`client.serving()` patched true) even with a
  non-empty host queue.
- `merged_at` is UTC minute format.

Proving check: the two files plus `test_merge_recorder_hook.py`, `test_merge_stamp_contract.py`,
`test_native_routing_hooks.py`, `test_remote_client.py`.

**Step 9. Docs touch.** The docstrings of `merge_gate.py`, `merge_gate_decide.py` and `merge_recorder_stamp.py` say
what server mode does: fail-closed, stamps as commands, the queue location. 007's design §12 rows that read "(008: …)"
are now true; this design is the record. No runbook change: 016's how-to owns user docs and gets a §9 G1/D2 note
through the tracker.

**Step 10. Regression set** (§14), foreground, in slices. Report each slice's counts and every failure with its output.
Don't mark done on a partial run.
