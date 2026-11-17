<!-- User intent: move an existing solo Taskmaster store onto the team server once, before any teammate gets a key:
     the store and its files land in the host clone intact, code branches stop tracking .taskmaster/ without anyone
     losing local files or history, and the split-brain marker fences every other clone (team-server-014). -->

# team-server-014: `serve init --from` migration and the `.taskmaster/` untrack flow (design, 2026-11-07, rev 3)

**Task.** `team-server-014`. Done when an existing solo store (native schema 3, 4 or 5) can be moved into a server
host clone, code branches stop tracking `.taskmaster/`, and the 009 server marker is written and committed (not
pushed).
**Source.** `docs/specs/2026-10-31-team-server-mode-addendum.md` §9 (also §6, §8, §13).
**Binding rulings** (`docs/handoffs/2026-11-01-team-server-lanes/rulings.md`):
- 014 commits without pushing; the marker is committed and pushed from the operator's laptop.
- The host holds only the backup push key and a read-only code-repo key.
- 014's output carries the written rollout rule for builds older than 009.

**Builds on** (all on `feat/team-collab` e5dc2bb):
- 009: `server_marker`, `server_fence` (host lease, `host-id`, latch, G1-G4, the pre-commit validator).
- 013: `state_export` (`offline_owner`, `drain`, `export_once`, `is_export_branch`, `check_settings`, the `Git`
  runner, the scheduler).
- 012: documents live in `external_documents`, inside `store.db`, so no import step is needed.
- The native cutover's backup helpers (`native/cutover.py`).

**Verified against.** Git 2.48.1 for Windows.
- The design probe, `D:\scratchbook\taskmaster\ts014\probe_untrack.py`, covers the untrack and teammates' clones.
- The review probes, `D:\scratchbook\taskmaster\ts014-review\p1`, `p2` and `p4`, show:
  - Git overwrites ignored files on checkout or pull (I1);
  - the validator lists staged deletions (I2).

**Review history.**
- Rev 1 (3455276): the design review came back FAIL, with 1 Critical, 4 Important and 10 Minor findings
  (`D:\scratchbook\taskmaster\ts014-review\design-review.md`).
- Rev 2 (this revision) fixes them:
  - **C1** (writes that were paused at stop still committed): §6.2 steps 8-9, D12, M3.
  - **I1** (Git overwrites the host's ignored files): §10, §10.1, D13.
  - **I2** (the pre-commit validator blocks the deletion merge): §10, D14.
  - **I3** (a rejected push loses teammates' changes): P12, §6.6, §9.
  - **I4** (resume was unreachable): §6.1, §6.4, §7.1, §7.2.
  - All ten Minors are folded in, each tagged mN at its place.
- Rev 3: the re-review settled C1, I1, I2 and I3. Its two findings on I4 are fixed here:
  - **R1:** resume at `bundled` or `refs` runs no coordinator and no settle, only the read-only check (§6.4).
  - **R2:** the `.gitignore` sha256 is journalled before any ref moves (§6.1 P4, §6.2 step 5, §6.5).

  Both are tested in §16 Step 6.
- O1-O6 were accepted as recommended (§15).

**Plan.** Embedded in §16; the regression set is §17.

## 1. Problem

A project that has run solo keeps its backlog in the operator's `store.db` and tracks the store's export
(`.taskmaster/backlog.yaml`, `tasks/…`) on its code branches. Moving it to the team server means three changes,
and each one can lose data or fork the backlog if it is done by hand:

- **The store has to arrive whole.** That means `store.db` together with the projection files that match its
  records. Copying a live WAL database gives a torn copy. Copying only the database leaves the host's exporter
  without its files, and 013's export reads those files.
- **Code branches have to stop tracking `.taskmaster/`.** Otherwise:
  - every clone keeps a copy that looks authoritative;
  - Git overwrites the host's live projection files whenever the host checks out a commit that tracks them;
  - a plain `git rm` would delete the operator's working copies.
- **The marker has to fence every other clone.** That includes the operator's laptop, the moment its store stops
  being the authority.

Two facts from the deployment shape the design. The host can't push code, and it can't reach the laptop. The
operator's laptop has push rights, and it holds the source store.

## 2. Shape: one command on each machine

```
operator's laptop (source clone, push rights)          Hetzner host (host clone, read-only code key)
──────────────────────────────────────────────          ─────────────────────────────────────────────
0. git pull; backlog_sync (teammates' .taskmaster/ changes in)
1. taskmaster serve init pack --root SRC \
     --url https://tm.example.com/mcp --out ~/proj.tmbundle
   · pre-flight (nothing written on refusal)
   · freeze: marker + latch written → the laptop is fenced
   · settle, snapshot, refuse late writes, stop, re-check
   · bundle; untrack commit on the current branch (plumbing, local)
2. git push origin main            (operator, by hand; if it is rejected: §6.6)
3. scp ~/proj.tmbundle host:                           4. git pull --ff-only     (read-only key; the host
                                                          tracks no .taskmaster/ from here on)
                                                       5. taskmaster serve init --root HOST --from proj.tmbundle
                                                          · pre-flight: HEAD carries the bundle's marker
                                                          · stage → publish → verify → export (013) → done
                                                       6. serve token / add-user …, systemctl start taskmaster
```

`--from` takes the **bundle** that `pack` made from the existing project, not a project path. The addendum wrote
`--from <existing project>`, but the host can't read the laptop's disk. The pack also has to run where the push
rights are, because the untrack commit is made there.

## 3. Decisions

- **D1. Two commands, one bundle.** `serve init pack` runs in the source clone. It freezes the store, snapshots it
  into a single zip bundle and makes the untrack commit. `serve init --from <bundle>` runs in the host clone and
  installs that bundle. Both commands commit locally and never push (ruling).
- **D2. The freeze is the marker.** `pack` writes `.taskmaster-server.json` and the latch into the source clone
  before it settles and takes the snapshot. It holds the host lease and its own coordinator while it does so. From
  that moment every other process on the laptop is fenced (009 G1-G3). The pack process stays exempt because it
  holds the lease (009 §6). Writes that reach the pack's coordinator from clients connected before the freeze are
  handled by D12.
- **D3. `host_id` is minted by `pack`** (`server_marker.new_host_id()`). It goes into the marker and the bundle
  manifest, and **never** into the laptop's `local/serve/host-id`, so the laptop can't run `taskmaster serve`
  (009 F5). `init` installs that id on the host with `ensure_host_id(root, value=…)`.
- **D4. The untrack is a plumbing commit.**
  - It runs `ls-tree` on the branch tip's root, drops the `.taskmaster` entry, sets the `.gitignore` and marker
    blobs, then `mktree`, `commit-tree` and `update-ref` with a compare-and-swap (CAS).
  - On the checked-out branch, `reset -q HEAD -- <the three paths>` and `checkout-index -f -- .gitignore` follow.
  - No working-tree file under `.taskmaster/` is touched, and no hook runs.
  - A porcelain `git commit` would be refused by taskmaster's own pre-commit hook, because staged deletions count
    as staged projection files (`git_hook.check`).
- **D5. Branches.** The checked-out branch is always untracked. Further local branches only with `--branch B`
  (repeatable), never by default (O2). Every other branch that still tracks `.taskmaster/` is listed in the
  report.
- **D6. The host must already carry the untrack before it gets the store.** `init` refuses unless:
  - the host clone's HEAD has the bundle's marker;
  - HEAD descends from the pack's commit on its current branch;
  - the index tracks nothing under `.taskmaster/`.
- **D7. Export after the untrack, not before.** 013's seam note said to call `export_once` before untracking.
  That ordering existed so the backlog would never live in only one place. Here it never does: when the pack
  commit lands, the data is in the fenced laptop store, in the bundle, and in the untrack commit's parent. `init`
  still runs `export_once` as its last step, which gives the first `taskmaster-state` baseline.
- **D8. Bytes are carried exactly.** The bundle carries projection files byte for byte, including CRLF from a
  Windows `autocrlf` checkout, and `mtime_ns` is restored best effort. The host's exporter treats line endings
  alone as agreement (`projection._judge`), so nothing gets flagged.
- **D9. Nothing that belongs to the operator is deleted.**
  - On the laptop, `pack` leaves the store, `.taskmaster/` and `local/` in place. They are fenced, not removed
    (O3).
  - `undo` removes only files the journal proves it wrote.
  - `init` never deletes the bundle.
- **D10. Resumable by journal.** Each command keeps a stage journal in `.taskmaster/local/serve/init/`
  (`pack.json` and `init.json`, written with `principals.replace_private`). Every pre-flight check that the
  command's own earlier writes would trip is journal-aware (§6.1). Rerunning the same command continues from the
  recorded stage, and every write step is idempotent.
- **D11. Submodule roots and separate git dirs are refused on both sides.** The latch needs `<root>/.git/` to be a
  directory (009 M7). In a submodule, `fence_roots` returns the outer repo's modules dir, so the clone would not
  be latched.
- **D12. Late writes are refused, not drained (C1).** `Coordinator.stop()` drains the queue, and the pause gate
  opens on stop (`service.py:261, 271`). So after the snapshot, `pack` calls a new
  `owner.refuse_writes(reason)`. Every queued, paused and later write then fails with `ServiceUnavailable` before
  `commands.execute`. This covers Linear settles and sync jobs too, because both submit through the queue
  (`linear_worker.py:102, 120`, `sync_jobs.py:131`). Then `pack` stops and closes the coordinator, *before* any
  ref moves. It reopens the store read-only, with the lease still held, and requires `MAX(seq)` and the projection
  watermark to equal the snapshot's `high`. A mismatch means something committed in the window, and the pack
  redoes the snapshot (§6.2).
- **D13. The host clone has a standing HEAD guard (I1).** Git treats ignored files as expendable, so any checkout
  or pull of a commit that tracks `.taskmaster/` silently overwrites live files (review probes p1, p2).
  - **Detection.** `untrack.head_tracks_taskmaster(root)` is checked at daemon start (refused) and on every
    export cycle (alarm, and `serve export status` exits 1). It is active only when a marker is present (§10.1).
  - **Prevention.** The runbook's pull rule.
- **D14. The validator allows deletions in server mode (I2).** In server mode, `git_hook.check` refuses only staged
  additions, modifications, renames and type changes (`--diff-filter=ACMRT`) under `.taskmaster/`. Its advice
  becomes `git rm -r --cached .taskmaster`. Without this, a fenced clone can't commit a conflicted merge of the new
  main, and the old advice (`git restore --staged .taskmaster`) re-tracks `.taskmaster/` (§10). Solo mode keeps
  today's behaviour.

## 4. Safety invariants (acceptance criteria)

| # | Invariant | How it holds |
|---|---|---|
| M1 | **Never pushes, never rewrites history.** | No `push`, `fetch` or `--force` in the 014 argv allowlist (§7.4). Refs move only forward: a new commit whose single parent is the old tip, via `update-ref <ref> <new> <old>` (CAS). `--undo` moves a ref back only from the recorded new commit to its recorded parent, and only while unpushed (§6.5). Remote-tracking refs are never written. |
| M2 | **Never deletes the operator's files.** | On the laptop, the working tree changes in exactly two paths, `.gitignore` and `.taskmaster-server.json`, and the index changes in exactly three. No `rm`, `checkout <branch>`, `reset --hard`, `clean` or `stash`. A test fingerprints every file under the source root before and after (m9: excluding `.git/` and `.taskmaster/local/serve/init/`). |
| M3 | **No write lands in the solo store after the snapshot, and no client is told a write succeeded that the bundle lacks (C1).** | The freeze (D2). Then settle, `pause_writer()` and `BEGIN IMMEDIATE` around the copy. Then `refuse_writes`, stop and close, and a read-only re-check of `MAX(seq)` and the watermark against `high`, under the lease (D12). |
| M4 | **The bundle is the store, and nothing else.** | It holds `store.db` (an SQLite online backup), the projection set, two known config files and `local/PROGRESS.md`. Every member is listed with its sha256 and size in the manifest. No keys and no `local/serve/` (§5). |
| M5 | **The host gets exactly the bundle, or refuses.** | The zip is validated (§5.3). The store passes `integrity_check`, `db.assert_native`, store_id, seq and watermark checks, and each projection record's hash matches its file. The store file is published last, by exclusive link. |
| M6 | **No hook or repository code runs.** | Every 014 git call goes through the allowlisted runner with `core.hooksPath=<empty private dir>` (013 G6). Plumbing commands only, plus `reset` and `checkout-index` for exactly the listed paths. |
| M7 | **Fenced on both sides when done, and the host stays untracked.** | Laptop: the marker in the working tree and the latch, and no `host-id`. Host: the marker in HEAD, `host-id` equal to it, the latch, and the D13 guard. The 009 daemon start check passes on the host and fails on the laptop. |
| M8 | **Solo mode unchanged.** | New code runs only from `taskmaster serve init …`. The shared helpers it extends keep their old behaviour by default; their existing tests prove it. Specifically: `refuse_writes` is never called outside `pack`; the D13 guard needs a marker; the D14 filter applies only in server mode. |
| M9 | **Refusals write nothing.** | Every refusal in §6.1 and §8.1 comes before the first write. For `pack`, a refusal after the freeze but before any ref moves undoes the freeze (marker and latch removed) and exits 2. A test for each refusal checks that the root's fingerprint is unchanged (scope as in M2). |

## 5. The bundle

### 5.1 Contents

A zip file (`zipfile`, deflated). It is created by exclusive create, owner-only (0600 on POSIX), and must sit
outside the source repository's working tree. Members:

| Member | What | Role |
|---|---|---|
| `manifest.json` (first member) | §5.2 | — |
| `store.db` | `sqlite3.Connection.backup` of `.taskmaster/local/store.db`, taken under §6.2 step 7 | store |
| `tree/<rel>` for each row of `projection` whose `file NOT LIKE 'local/%'` | the file on disk, byte-exact | `projection` |
| `tree/taskmaster.json`, `tree/linear.yaml` when present | `.taskmaster/` config files outside the projection set (`git_hook.NOT_PROJECTIONS`, `linear_config.CONFIG`) | `config` |
| `tree/local/PROGRESS.md` when present | carried as is. The host coordinator's progress writer (`native_routing/progress.py`) re-renders its store-owned region at its next render: the next completion, or the first drain after start that owes one (m8). The rest of the file is the user's | `progress` |

Never carried: anything else under `local/`, including `serve/` (keys and principals), `cache/`, `viewer.json`,
`snapshots/`, `id-reservations.json` (which only the legacy store uses) and logs. Also never carried: tracked
`.taskmaster/` files outside the projection set, such as `harvests/` and `lessons/`. Both groups are listed in the
report as "not migrated" (O1).

**Single use (m7).** The bundle carries the host identity. Installing it in two host clones gives two clones the
same `host_id`, and with it two daemons that can each serve a fork. The report and the runbook say:
- install it once;
- before any reinstall, the old host clone must be stopped and deleted;
- delete the bundle on both machines once the server runs (O6).

### 5.2 Manifest

```json
{
  "format": 1,
  "kind": "taskmaster-server-bundle",
  "created": "2026-11-07T10:00:00+00:00",
  "source_build": {"version": "…", "digest": "…"},
  "store_id": "…",
  "schema_version": 4,
  "high_seq": 18234,
  "exported_through": 18234,
  "marker": {"url": "https://tm.example.com/mcp", "host_id": "<32 hex>"},
  "current_branch": "main",
  "commits": {"main": "<40 hex>"},
  "store": {"sha256": "…", "size": 8425472},
  "tree": [{"path": "backlog.yaml", "role": "projection", "sha256": "…", "size": 1234, "mtime_ns": 0}],
  "counts": {"task": 812, "epic": 40, "document": 12},
  "claims": [{"task": "x-001", "holder": "laptop-1234-abcd0123", "expires_at": "…"}],
  "team_mode": true,
  "not_migrated": {"tracked": ["harvests/a.md"], "tracked_total": 31, "local": ["viewer.json", "snapshots/"]}
}
```

Notes:
- `commits` maps each branch untracked by the pack to its new commit. It is built before the bundle is written
  and moved after (§6.2).
- Lists are capped at 200 entries, with a `*_total` count beside them.
- No absolute paths or remote URLs are recorded.
- Claim holder ids are recorded as the store holds them. Host-local holders embed the laptop's hostname
  (`claims.SESSION_SHAPE`); that is allowed, because the same ids are already inside `store.db` in the bundle
  (m10).

### 5.3 Validation (`bundle.open_bundle`, host side; also run by `pack` on its own output)

The bundle is refused, before anything is extracted, when:
- the file is not a zip;
- `manifest.json` is missing or larger than 16 MiB, or isn't a JSON object;
- `format` is above 1 ("made by a newer build: update the plugin on the host"), or `kind` is wrong;
- `schema_version` is not in `native.schema.SUPPORTED` ("the store is newer than this build");
- the marker fails `server_marker.parse`;
- `current_branch` isn't a key of `commits`;
- the member set isn't exactly `{manifest.json, store.db} ∪ {tree/<p>}`;
- a member is a directory entry, a symlink (`external_attr` type `0o120000`) or a duplicate;
- a path fails `projection.safe_relative`;
- a `projection` path starts with `local/`;
- a `config` path isn't in `CONFIG_FILES`, or a `progress` path isn't `local/PROGRESS.md`;
- a zip member's `file_size` differs from the manifest's;
- there are more than 200 000 members.

During extraction (`Bundle.extract(staging)`):
- each member is streamed with a hard cap equal to its manifest size;
- each member is hashed while it is written;
- a mismatch removes the staging directory and refuses.

## 6. `taskmaster serve init pack` (the source clone)

```
taskmaster serve init pack --root SRC --url URL --out FILE [--branch B]... [--flush-timeout 120]
taskmaster serve init pack --undo --not-pushed --root SRC
```

### 6.1 Pre-flight (read-only; any refusal exits 2 and writes nothing)

**Journal ownership (I4).** When `pack.json` exists in a stage other than `done` or `undone`, three kinds of thing
count as **journal-owned**, and the checks below skip them:
- the marker file, when its sha256 equals the journal's `marker_sha256`;
- a branch tip, when it equals that branch's recorded new commit;
- the `--out` file, when its sha256 equals the journal's `bundle_sha256`.

A rerun must also pass the same `--url`, `--out` and `--branch` set as the journal recorded. Otherwise it refuses
and names `--undo`. A journal in `done` makes `pack` print the report again (exit 0). A journal in `undone` is
ignored, and the run starts fresh.

| # | Refused when | Text names |
|---|---|---|
| P1 | `SRC` is not a git top level (`rev-parse --show-toplevel` ≠ SRC), is a linked worktree (git dir ≠ common dir), has a `.git` that isn't a directory, or is a submodule (`--show-superproject-working-tree` isn't empty) | the root kind; D11 |
| P2 | `fence_roots(SRC)` shows a marker or latch that isn't journal-owned (a journal-owned marker's latch is also accepted) | "already in server mode" |
| P3 | HEAD is detached or unborn; a merge, rebase, cherry-pick, revert or bisect is in progress (`MERGE_HEAD`, `rebase-*`, `CHERRY_PICK_HEAD`, `REVERT_HEAD`, `BISECT_LOG`); on a rerun, HEAD isn't the journal's `current_branch` | the state; "finish or abort it" |
| P4 | `.gitignore` has staged or unstaged changes, unless the journal is at `refs` and the file is journal-owned. Journal-owned (R2) means one of two things. Either its sha256 equals `gitignore_before_sha256`, which `pack` records before any ref moves: this is the crash state after `update-ref`, before the sync. Or its bytes, LF-normalised, equal the `.gitignore` blob of the recorded new commit, LF-normalised: this is the state after `checkout-index`. Separately, P4 also refuses when a `.taskmaster-server.json` exists that isn't journal-owned | the path |
| P5 | No store: `.taskmaster/local/store.db` is missing. Or the store isn't native (`cutover.classify`: legacy authority, staging, a fenced migration, or `invalid`/`newer`), or its native `schema_version` isn't in `SUPPORTED` | the next step: a files-only or legacy project runs the native cutover first (`docs/runbooks/native-cutover.md`); a newer store needs a newer plugin |
| P6 | `server_marker.url_problem(url)` | the problem |
| P7 | `--out` exists and isn't journal-owned, its parent doesn't exist, or it resolves inside SRC | the path |
| P8 | `git var GIT_COMMITTER_IDENT` fails (no identity) | "set user.name and user.email" |
| P9 | The repository is `sha256` or `reftable` (013 can't export it on the host) | 013's texts |
| P10 | A `--branch B` doesn't exist, is a symbolic ref, is checked out in any worktree (case-insensitive on Windows, as 013), equals the current branch, is an export branch (`state_export.is_export_branch`), or its tip has a marker and isn't journal-owned | the branch and reason |
| P11 | The current branch, or a `--branch`, has `.taskmaster` in its root tree as anything but a tree (mode ≠ 040000) | the branch |
| P12 | **(I3)** The current branch has an upstream (`rev-parse --symbolic-full-name @{u}`), and that upstream's last-fetched tip isn't an ancestor of HEAD (or, on a rerun, of the recorded old tip) | "your branch is behind its upstream: `git fetch`, merge it, run `backlog_sync` so the store takes any `.taskmaster/` changes teammates pushed, then pack. A pack over unmerged upstream changes would lose them when you merge after the push." With no upstream configured, a warning in the report instead |

Two refusals come later, before the freeze (exit 69, nothing written):
- the host lease is held: "another pack is running";
- `offline_owner` can't take the store because a session coordinator owns it: 009's `OWNED_TEXT`, which tells the
  operator to run `taskmaster coordinator stop --root SRC`.

### 6.2 Steps

1. Pre-flight (§6.1).
2. Create `.taskmaster/local/serve/init/` (`private_directory_at`). Take `server_fence.host_lease(SRC)` and keep it
   for the whole run, through step 13. `host_lease` is 009's alias of `claim_host`; this design uses that name
   everywhere (m8).
3. `with state_export.offline_owner(SRC, lease=lease) as owner:` (§12: the `lease` parameter is new).
4. **Settle.** Read `high`, then call `owner.flush(high, timeout=flush_timeout)`. The result must be
   `state == "exported"` with no notices. A held, flagged, quarantined or drift file, a managed-Git pin, owed
   PROGRESS or a timeout each refuse with flush's own notice text (exit 2). Before the freeze that writes
   nothing; on a resume after the freeze, it undoes the freeze (§6.4).
   Then read the projection set (`cutover.projection_reads`). Each file's sha1 (or its LF or CRLF variant) must
   equal its `projection.content_hash`, and a missing file refuses.
5. **Journal** (fresh run only) `pack.json`: `{stage: "frozen", url, host_id, out, current_branch,
   branches: {name: old_tip}, store_id, marker_sha256, gitignore_before_sha256, started}`.
   `gitignore_before_sha256` is the sha256 of the working `.gitignore`, which P4 proved clean, or `null` when the
   file is absent. It is recorded here, before any ref moves (R2).
6. **Freeze** (fresh run only). `server_marker.publish_marker(SRC, url, host_id)` writes the marker and the latch
   (§12). On a resume the marker is already there and `publish_marker` only refreshes a missing latch. Nothing else
   on the laptop can open the store from here on. A write that was in flight at the freeze is caught by step 7's
   `high2` check.
7. **Snapshot.**
   - `owner.pause_writer()`.
   - On `owner._connect()`, run `BEGIN IMMEDIATE`. Read `high2`. If `high2 ≠ high`, roll back, resume, re-settle
     (step 4) and retry. At most 3 tries; then undo the freeze and exit 2 ("the store kept changing; stop every
     session").
   - `cutover.copy_database(store, temp)` (§12) into `serve/init/store-<nonce>.db`. Roll back the transaction.
8. **Refuse late writes and stop (C1).** Call `owner.refuse_writes("the backlog moved to the team server at <url>;
   nothing was written")`, then `owner.resume_writer()`. Leave `offline_owner`, which stops and closes the
   coordinator and keeps the lease. Every queued or paused item fails, and its client gets the error rather than a
   receipt.
9. **Re-check.** With no coordinator running and the lease held, open `store.db` read-only and require
   `MAX(domain_events.seq) == high` and `projection.watermark == high`. On a mismatch (a command that was already
   past the pause gate committed after the copy), delete the temp copy and go back to step 3. This re-check shares
   the 3-try budget of step 7.
   Journal: `stage: "snapshot", high`.
10. **Verify the snapshot.** `integrity_check`, `assert_native`, store_id, `MAX(seq) == high` and
    `projection.watermark == high` on the temp copy. Also collect counts, claims, `team_mode_enabled` and the
    `not_migrated` lists.
11. **Build the commits** (§7.1) for the current branch and each `--branch`. These are objects only; no ref moves.
    Journal: `stage: "commits_built", commits: {…}`.
12. **Write the bundle.** Write `--out` + `.partial` (exclusive, 0600) and fsync. Validate it with `open_bundle`,
    then `os.replace` it to `--out` (this replaces a journal-owned earlier bundle). Delete the temp copy. Journal:
    `stage: "bundled", bundle_sha256`.
13. **Move refs and sync** (§7.2). Journal: `stage: "refs"` before the first move. After the sync, set
    `stage: "done"`. Release the lease. Print the report
    (§6.6). Exit 0.

### 6.3 Why the order is freeze, settle, snapshot, refuse, stop, re-check, then refs

- **The freeze comes first.** New clients then fail in their own process at G3. Only clients already connected to
  the pack's coordinator can still reach its `dispatch`, which runs in the exempt pack process.
- **Those late clients are covered by the next three steps.** `pause_writer` holds their commands before commit.
  `refuse_writes` turns them into errors instead of letting stop drain them. The read-only re-check proves that
  nothing slipped past the gate.
- **The refs move last.** They move only after the store is provably the bundle's, so a published commit always
  corresponds to a complete bundle.

### 6.4 Resume (I4)

Resume always takes the lease (step 2) first. It never redoes the freeze (steps 5 and 6), except to refresh a
missing latch. What follows depends on the stage (R1):

- **Before the bundle exists** (`frozen`, `snapshot`, `commits_built`): resume opens the owner (step 3) and settles
  (step 4). A settle refusal undoes the freeze (marker and latch removed, `stage: "undone"`) and exits 2. No ref
  has moved at these stages, so this is always possible.
- **Once the bundle exists** (`bundled`, `refs`): resume **never starts a coordinator and never settles**. C1's
  order needs no coordinator alive while refs move, and a settle refusal here could strand a half-moved set of
  refs. Resume runs only the step-9 read-only check, with the lease held: `MAX(seq)` and the projection watermark
  must equal the journal's `high`.

Then:

| Journal stage | Rerun does |
|---|---|
| `frozen` | from step 7 |
| `snapshot`, `commits_built` | from step 7. The snapshot is retaken, so it is never older than the latest settle. Commits are rebuilt from the same old tips, and the journal records the new ids; the old objects are left for gc |
| `bundled` | the read-only check. If it passes and `--out` is journal-owned, go to step 13. If the check fails (only a pre-009 build could have written through the fence), or the bundle is gone, go back to step 3 and settle. No ref has moved, so a settle refusal there still undoes the freeze. The new bundle replaces the old, journal-owned one |
| `refs` | the read-only check. A mismatch exits 1 and changes nothing: "the store was written after the bundle (a pre-009 build?); `--undo --not-pushed`, then pack again". On a pass, step 13 runs, with no coordinator. Each branch whose tip is its recorded new commit is done. One whose tip is its recorded old commit is moved now. Anything else exits 1: "B moved since the pack; resolve it by hand, or `--undo`". The current branch's sync (§7.2) always runs again, because it is idempotent; a crash between `update-ref` and `reset` heals this way |
| `done` | prints the report, exit 0 |

### 6.5 `--undo`

Undo requires `--not-pushed`, the operator's statement that no pack commit has left the machine. The automatic
check below sees only remote-tracking refs, and a push by URL, or to a remote that isn't tracked, escapes it (m1).
The undo text warns plainly: if the commit is public, undo unfences the laptop while every other clone is fenced
and the host may already serve.

It refuses (exit 2) when:
- any recorded new commit is contained in a remote-tracking ref (`for-each-ref --contains <new> refs/remotes/`).
  Text: "already pushed; revert the commit with `git revert` and run `serve marker clear-latch` in every clone
  instead";
- HEAD isn't the journal's `current_branch` (m2);
- a moved branch has commits on top of its recorded new commit.

Otherwise:
1. Take the lease, then CAS each moved branch whose tip equals its recorded new commit back to its old tip.
2. On the current branch, run `reset -q HEAD -- .taskmaster .gitignore .taskmaster-server.json`, which puts the
   index entries back. Then run `checkout-index -f -- .gitignore`, but only if the working `.gitignore` is
   journal-owned in P4's sense (m3, R2). If it already matches `gitignore_before_sha256`, it is left alone.
3. Remove the marker if it is journal-owned, then `clear_latch`.
4. Journal: `stage: "undone"`. The bundle is left where it is, and the report names it.

### 6.6 Report (stdout, JSON with `--json`, else text)

The report contains:
- the bundle path and sha256, with the single-use note (§5.1);
- store_id, schema, high seq, counts, and whether team mode is on;
- the branches untracked (old → new);
- other branches that still track `.taskmaster/`, local and remote-tracking (O2);
- not migrated (§5.1);
- open claims and their latest expiry (O4);
- the P12 warning, if there is no upstream;
- the push command to run (`git push <remote> <branch>…`);
- the host command (`scp …` and `taskmaster serve init --root <host clone> --from <file>`);
- the §11 rollout rule, verbatim.

It also carries this rule (I3):

> **If the push is rejected, don't pull or rebase over the pack commit.** Teammates pushed something after your
> last fetch. Merging it now would drop their `.taskmaster/` changes, or track `.taskmaster/` again. Instead:
> 1. `taskmaster serve init pack --undo --not-pushed --root SRC`;
> 2. `git pull`;
> 3. `backlog_sync`;
> 4. run the pack again.

The report also warns about the laptop (m2): don't check out a branch that still tracks `.taskmaster/` in this
clone. Git would overwrite the fenced files with that branch's copies, though the store and the bundle are
unaffected.

## 7. The untrack transform (`serve/untrack.py`)

### 7.1 Per branch (pure objects, nothing moves)

For tip `T` of `refs/heads/B`. A tip that is journal-owned, meaning it is already the recorded new commit, is
already untracked and is skipped (I4).

1. `ls-tree -z T^{tree}`: the root entries only. If a `.taskmaster-server.json` entry exists, refuse (P10).
2. Drop the entry named `.taskmaster` if there is one; a branch without it still gets the marker.
3. **`.gitignore`.** If an entry exists, read its blob (`cat-file blob`). If any line, stripped, is one of
   `.taskmaster`, `.taskmaster/`, `/.taskmaster` or `/.taskmaster/`, keep the blob. Otherwise:
   - append `/.taskmaster/` in the blob's own line ending (CRLF if the blob holds `\r\n`);
   - add a line break first if the blob doesn't end with one;
   - `hash-object -w --no-filters --stdin`.

   If there is no `.gitignore` entry, create `/.taskmaster/\n`.
4. Marker blob: `server_marker.render(Marker(url, host_id))`, through `hash-object -w --no-filters --stdin`.
5. `mktree -z` → `tree`. Then `commit-tree tree -p T -F -`.
   - **Signing** follows the operator's `commit.gpgSign`: `-S` when it is true, else `--no-gpg-sign`. The commit
     goes to their real remote.
   - Signing variables (`GNUPGHOME`, `GPG_TTY`, `GPG_AGENT_INFO`, `SSH_AUTH_SOCK`, `SSH_AGENT_PID`) are inherited:
     the managed probe scrubs only `GIT_*`, so they pass through without being named (m6, as built). Other
     non-`GIT_*` variables are inherited too; `fsmonitor` is off and `core.hooksPath` is an empty directory.
   - A signing failure fails the commit build. That is a refusal after the freeze with no ref moved, so the freeze
     is undone, and the exit is 2 with "signing failed (<redacted stderr>): fix signing or set
     `commit.gpgSign=false` for this repository, then pack again".

   Message:
   ```
   chore: serve this project's backlog from a Taskmaster server

   Untracks .taskmaster/ (the backlog now lives on the team server and is backed up on the
   taskmaster-state branch there) and adds .taskmaster-server.json, which fences every clone.

   Taskmaster-Migration: 1
   Taskmaster-Store: <store_id>
   ```

### 7.2 Moving and syncing

- **Moves.** The `--branch` refs go first and the current branch last, each by
  `update-ref -m "taskmaster serve init pack" refs/heads/B <new> <T>`. If it fails because the CAS lost, nothing
  more is moved and the run exits 1 (resumable, §6.4).
- **Sync** (the current branch only; it runs on every pass through step 13, because it is idempotent):
  - `reset -q HEAD -- .taskmaster .gitignore .taskmaster-server.json`. Index entries under `.taskmaster/` go, and
    the other two match HEAD. Other staged changes stay staged. An unmatched pathspec exits 0 (review probe p4).
  - `checkout-index -f -- .gitignore` writes the working `.gitignore` with the repository's own EOL conversion.
  - The marker's working file is already the exact bytes from the freeze (LF). Git reports it clean under any
    `autocrlf` (probe). The sync doesn't rewrite it.

### 7.3 What it never touches

- **Files.** It never touches any file under `.taskmaster/`, any other working-tree file, or the working tree of
  any branch other than the checked-out one.
- **Refs and history.** It never touches remote-tracking refs, tags or history, and never touches a branch checked
  out in a linked worktree (P10).

### 7.4 Argv allowlist (`UntrackGit`, built on the shared runner in §12)

The runner allows only these:

- `rev-parse` with `--show-toplevel`, `--absolute-git-dir`, `--git-common-dir`,
  `--show-superproject-working-tree`, `--show-object-format` or `--show-ref-format`;
- `rev-parse --verify -q` with exactly one of: `<refs/heads/B>^{commit}`, `<refs/remotes/...>^{commit}`,
  `HEAD^{commit}`, `HEAD:.taskmaster-server.json`, `HEAD:.taskmaster` (the D13 guard), `@{u}^{commit}`;
- `rev-parse --symbolic-full-name @{u}`;
- `symbolic-ref -q HEAD`, and `symbolic-ref -q refs/heads/<B>` (all read-only; added while building);
- `for-each-ref --format=%(refname)` over `refs/heads/` or `refs/remotes/`, and with `--contains <hex>`;
- `worktree list --porcelain`;
- `status --porcelain=v1 -z --untracked-files=no -- .gitignore`;
- `ls-tree -z <hex>`, and `ls-tree -r --name-only -z <hex> -- .taskmaster`;
- `ls-files -z -- .taskmaster`;
- `cat-file blob <hex>`;
- `hash-object -w --no-filters --stdin`;
- `mktree -z`;
- `commit-tree <hex> -p <hex> (-S|--no-gpg-sign) -F -`;
- `update-ref -m <msg> refs/heads/<planned B> <hex> <hex>`;
- `reset -q HEAD -- .taskmaster .gitignore .taskmaster-server.json`, exactly;
- `checkout-index -f -- .gitignore`, exactly;
- `var GIT_COMMITTER_IDENT`;
- `config --get --type=bool commit.gpgSign`;
- `merge-base --is-ancestor <hex> (<hex>|HEAD)`;
- `check-ignore -q -- .taskmaster/backlog.yaml`.

`ENV_KEYS` is empty: signing variables are inherited (see §7.1).

Everything else raises before Git runs. That includes every `push`, `fetch`, `pull`, `rm`, `checkout`, `switch`,
`commit`, `merge`, `clean`, `stash` and `gc`, and every `reset` other than the exact form above.

## 8. `taskmaster serve init --from` (the host clone)

```
taskmaster serve init --root HOST --from FILE [--export-branch taskmaster-state] [--export-push REMOTE | --no-export]
```

### 8.1 Pre-flight (read-only; refusals exit 2, a held lease or owner exits 69)

| # | Refused when |
|---|---|
| H1 | `HOST` fails the P1 checks (git top level, main checkout, `.git` directory, not a submodule) |
| H2 | `bundle.open_bundle(FILE)` refuses (§5.3) |
| H3 | HEAD has no `.taskmaster-server.json`, or its **parsed** marker (`server_marker.parse`, which accepts CRLF; m5) differs from the manifest's `url`/`host_id`, or the working-tree marker's parsed value differs from HEAD's. Text: "pull the commit `pack` made (push it from the laptop first)" |
| H4 | `manifest.commits[current_branch]` isn't an ancestor of HEAD, or is missing from the repository. Other recorded commits (unpushed `--branch` ones) are ignored (m4). Text as H3 |
| H5 | `ls-files -- .taskmaster` isn't empty, or `head_tracks_taskmaster(HOST)` is true: the host is on a commit that still tracks `.taskmaster/` (D6, D13) |
| H6 | The target already has a store or backlog, and no `init.json` journal names this bundle's sha256. A store or backlog means: `local/store.db*` exists, or any path under `.taskmaster/` other than `local/serve/…` exists (the operator may have run `serve token` before). Text: "already initialized" |
| H7 | `local/serve/host-id` exists and differs from the manifest's `host_id` |
| H8 | Export on (the default): `state_export.check_settings(Settings(HOST, branch, DEFAULT_INTERVAL, push))` refuses, or `refs/heads/<branch>` already exists. A restore uses 013 §8.1, never `init` |
| H9 | `init.json` exists for a different bundle sha256 |

If `check-ignore` says `.taskmaster/backlog.yaml` isn't ignored, that is a warning only (the report says it).

### 8.2 Steps

1. Pre-flight (§8.1).
2. `private_directory(HOST, "serve")`, then `.taskmaster/local/serve/init/`. Take `host_lease(HOST)`; a held lease
   exits 69.
3. Journal `init.json`: `{stage: "staging", bundle_sha256, store_id, host_id, started}`.
4. **Stage.** Extract into `serve/init/staging/` (`Bundle.extract`). Check the staging copy with
   `integrity_check`, `assert_native`, store_id, `MAX(seq) == high_seq`, `watermark == exported_through`, and
   projection rows (non-local) equal to the manifest's projection paths, with the hashes as in §6.2 step 4.
   Journal: `stage: "staged"`.
5. **Publish.**
   - For each `tree/` file: create its parent directories, then place it by exclusive `os.link` from staging. A
     file that is already there with identical bytes counts as placed (resume); different bytes refuse (exit 1).
     Restore `mtime_ns` best effort.
   - Then `ensure_host_id(HOST, value=host_id)`.
   - Then the store, **last**: `os.link(staging/store.db, local/store.db)`, which is exclusive.
   - Journal: `stage: "published"`. The staging directory is removed (it is init's own).
6. **Verify.** `with offline_owner(HOST, lease=lease) as owner:` check that `owner.identity["store_id"]` equals the
   manifest's. Then `owner.flush(high_seq)` must be `exported` with no notices. Journal: `stage: "verified"`.
7. **Export** (unless `--no-export`). Inside the same owner: `se.drain(owner)`, then
   `se.export_once(owner, settings, push=settings.push is not None)`. The state must be `committed`. A push failure
   is reported, and the stage still advances: the commit is local and 013 retries the push. Journal:
   `stage: "exported", commit`.
8. `server_marker.record_latch(HOST, marker)`. Journal: `stage: "done"`. Release the lease. Print the report
   (§8.4). Exit 0.

### 8.3 Resume

| Journal stage | Rerun does |
|---|---|
| `staging`, `staged` | removes `serve/init/staging/` (init's own) and redoes step 4 |
| `published` | if `store.db` is missing, redoes step 5 (identical files count as placed). If present, checks store_id and `MAX(seq) == high_seq`. More events than that refuse (exit 1): "the store was written after a partial init; nothing more is done" |
| `verified`, `exported` | redoes step 7. 013's CAS and lineage guards make a second cycle `unchanged`, or a fast-forward |
| `done` | prints the report, exit 0 |

The host-id check and the "already initialized" refusal are journal-aware (H6, H7), so a rerun isn't refused by
its own earlier writes.

There is no `--abort`; the host clone is dedicated. To give up a half-done init:
1. stop anything running there;
2. **delete that clone** before cloning again (m7: a second clone installed from the same bundle would share its
   `host_id`);
3. clone again and run `init` from the start.

The 016 how-to says so.

### 8.4 Report

The report contains:
- store_id, schema, high seq, counts, team mode and the source build;
- the export commit, plus the push state when `--export-push` was given;
- open claims (laptop holders) and their latest expiry (O4);
- not migrated;
- whether `.taskmaster/` is ignored;
- the §10.1 host pull rule;
- next steps: `serve token`, `serve add-user <handle>` for each teammate and for the operator's own laptop agent
  (`--role operator`), `systemctl start taskmaster`, and `serve export status`;
- "this bundle is single-use; delete it here and on the laptop once the server is running" (m7, O6);
- the §11 rule, verbatim.

## 9. Atomicity at a glance

| Dies, or stops, at | Laptop state | Host state | Next |
|---|---|---|---|
| pack, before the freeze | unchanged (an empty `serve/init/` at most) | — | rerun |
| pack, frozen to bundled | marker and latch in the working tree: fenced; no refs moved | — | rerun (resumes, settles first), or `--undo` |
| pack, refs partly moved | some branches untracked, the current one maybe not; still fenced | — | rerun (finishes, re-syncs), or `--undo` |
| pack done, not pushed | fenced; local commit | — | push; or `--undo` |
| **push rejected (I3)** | fenced; local commit; upstream has new commits | — | never pull over the pack commit: `--undo --not-pushed`, `git pull`, `backlog_sync`, pack again (§6.6) |
| pushed, host not pulled | fenced; teammates who pull get fenced and redirected, and the server isn't up yet | — | pull on the host, then init. Teammates wait for a key (§10) |
| init, staging | — | `serve/init/` only; the lease drops with the process | rerun |
| init, published | — | files and store in place; nothing has served | rerun (verifies) |
| init, verified or exported | — | store verified; export maybe missing | rerun (exports) |
| **later: the host checks out a commit that tracks `.taskmaster/` (I1)** | — | Git has overwritten live files; the daemon won't start; the export alarms | §10.1 recovery |

What is atomic: each ref move (CAS), the bundle's publication (`os.replace`), the store's publication (exclusive
`os.link`) and every journal write. Everything else is idempotent on rerun.

## 10. After the switch

Probe results (`D:\scratchbook\taskmaster\ts014\probe_untrack.py` and the review's `p1`, `p2`, `p4`; Git 2.48.1,
`core.autocrlf=true` on the source):

| Clone | What happens | Probe |
|---|---|---|
| **Operator's laptop** (the pack ran here) | Every `.taskmaster/` file, `local/store.db` included, is still on disk. `git status` is clean; the working `.gitignore` is CRLF and clean. Every taskmaster tool refuses with 009's marker text, and the daemon refuses (no `host-id`). Checking out a branch that still tracks `.taskmaster/` overwrites these fenced, ignored files with that branch's copies (the I1 behaviour). The store and the bundle are unaffected, and the report warns about it (m2). | `src status: ''`, files kept |
| **Teammate, clean clone**, `git pull` | Git deletes the formerly tracked `.taskmaster/` files from the working tree. Ignored `local/` (their old `store.db`) stays. Their next tool call refuses and latches; session-start tells them to use the server's MCP with their key. | `mate files: ['local/store.db']` |
| **Teammate with uncommitted edits to a `.taskmaster/` file** | Git refuses the pull ("Your local changes … would be overwritten by merge"). They discard or stash those edits (they were never going to reach the server), then pull. | `mate2 pull rc=1` |
| **Teammate feature branch that changed `.taskmaster/` files**, merging the new main | `CONFLICT (modify/delete)`. Resolve by keeping the deletion: `git rm -r --cached .taskmaster`, then commit. With D14, the fenced clone's pre-commit validator allows the staged deletions and refuses only staged additions or changes under `.taskmaster/`, with that same advice. Keeping their side instead would re-track `.taskmaster/` on main, and §10.1 then catches it on the host. | `UD .taskmaster/tasks/t1.yaml`; p4 |
| **Any clone checking out a pre-migration commit** (old tag, bisect) | Git brings `.taskmaster/` back, overwriting ignored files of the same name. The latch keeps every local store closed until `serve marker clear-latch` (009 §5). | `mate old checkout`, p1 |
| **Host clone** | It tracks no `.taskmaster/` after init. But **Git silently overwrites its ignored live files** on any checkout or pull of a commit that tracks `.taskmaster/`: an old commit (p1), or an upstream that re-tracked it (p2). Both exit 0. §10.1 guards against it. | p1, p2 |

Anything a teammate kept only in their own local store, or in unpushed `.taskmaster/` edits, is not migrated. The
rollout rule (§11) asks for it to be handed over before the pack.

### 10.1 The host HEAD guard (D13, I1)

`untrack.head_tracks_taskmaster(root) -> str | None` returns HEAD's commit id when
`rev-parse --verify -q HEAD:.taskmaster` succeeds, and `None` otherwise. It runs through `UntrackGit`, so the
exporter's own allowlist doesn't change. It is consulted only when `server_marker.server_mode(root)` returns a
marker, so solo and test roots are unaffected:

- **Daemon start.** `ServeDaemon._check_marker`, right after the `host_id` binding passes. A tracked
  `.taskmaster` at HEAD gives `ServeRefused` (exit 2): "the host clone's HEAD (<commit>) tracks .taskmaster/; Git
  may have overwritten live projection files. Don't serve until HEAD is back on an untracked commit:
  `docs/runbooks/team-server-migration.md#host-head`".
- **Every export cycle.** `StateExporter`'s cycle checks it before the guards. When it is tracked, the cycle still
  runs: held files keep their last blob (013), so the backup stays right for everything else. But it records
  `host_tracks_taskmaster: <commit>` in `export.json` and logs an ERROR at most once an hour (013's
  `WARN_EVERY`). `serve export status` exits 1 while that field is set, which the 016 health check watches. The
  field clears on the first cycle that finds HEAD untracked.

Runbook rules (`team-server-migration.md`):

- **Pulling on the host.** First `git fetch`. Then `git diff --name-only HEAD @{u} -- .taskmaster` must print
  nothing, and `git ls-tree @{u} -- .taskmaster` must be empty. Only then run `git merge --ff-only @{u}`. If either
  check prints something, upstream re-tracked `.taskmaster/`: fix main from the laptop
  (`git rm -r --cached .taskmaster`, commit, push) and don't pull.
- **Never** run `checkout`, `switch`, `reset` to another commit, `bisect` or `stash` in the host clone. `-f` isn't
  the only danger; ignored files are overwritten without it.
- **Recovery after an overwrite.** Stop the daemon. Fix main from the laptop as above, then pull on the host with
  the check. A pull back to an untracked commit deletes the re-tracked files from the host's working tree. Start
  the daemon: the store is still the authority. Its exporter republishes a missing file at that entity's next
  change. Files that git overwrote, and that still differ, are held as conflicts: resolve them with
  `backlog_resolve_conflict(take="store")` from an operator key. A bulk "restore every projection file from the
  store" command is a follow-up (§14).

## 11. Rollout rule (printed by `pack` and `init`; 016's how-to repeats it)

`migration.ROLLOUT_RULE`, formatted with `{floor}` (O5):

> **Before you push the commit that adds `.taskmaster-server.json`:**
>
> 1. Everyone who works in a clone of this repository must run taskmaster {floor} or later: a build with the
>    split-brain fence, where `taskmaster serve marker show` exists. Older builds don't know the marker. They keep
>    opening, or quietly creating, a local backlog, and their work never reaches the server. Check every machine,
>    including CI runners and other machines of your own.
> 2. Anyone with backlog changes that aren't in your store yet (edits in their own local store, or `.taskmaster/`
>    changes on unmerged branches) hands them over first. The server starts from your store as it is at the pack,
>    and nothing else is migrated.
>
> **After the push:** anyone still on an older build updates before their next session in this repository. Until
> they have their own API key, their taskmaster tools refuse here, and nothing is written locally. When merging
> the new main into an older branch, keep the deletion of `.taskmaster/` (`git rm -r --cached .taskmaster`); never
> add it back.

## 12. Reuse map

| Need | Reused from | Change (in place, old behaviour kept by default) |
|---|---|---|
| Fence exemption while the store is open | `server_fence.host_lease` (alias of `claim_host`, 009) | none |
| In-process store owner | `state_export.offline_owner` (013) | new `lease=None` keyword. When given, it uses that lease and doesn't release it on exit |
| Refusing late writes (C1) | `coordinator/service.Coordinator` | new `refuse_writes(reason)`. Under `guard` it sets `self.refusing` and notifies `admission`. `submit` raises `ServiceUnavailable(reason)` while it is set. `_writer`, after the admission gate and before `team_identity.admit` / `commands.execute`, fails the work with `ServiceUnavailable(reason)`. Default `None`: no change for any other caller |
| Drain before export; one export cycle; export-branch test; settings checks | `state_export.drain`, `export_once`, `is_export_branch`, `check_settings`, `Settings` | none |
| Allowlisted git runner (hooksPath override, `QUIET_GIT`, scrubbed env, bounded `probe`) | `state_export.Git` | extract `run`/`text`/`_config_ok`/`temp_name` into `taskmaster/serve/gitrun.py: AllowlistedGit(root, directory, record=None)` with an abstract `refusal(args, env)`. `state_export.Git` subclasses it with its allowlist unchanged (013's spy test stays green). `UntrackGit` is the second subclass |
| Host HEAD guard (D13) | `ServeDaemon._check_marker` (009), `StateExporter` cycle, `export.json`, `status` (013) | one call each to `untrack.head_tracks_taskmaster`; `export.json` gains `host_tracks_taskmaster`; `status` exits 1 on it |
| Pre-commit validator in server mode (D14) | `coordinator/git_hook.check` (009) | in server mode, list staged paths with `--diff-filter=ACMRT`, so deletions pass; the refusal text says `git rm -r --cached .taskmaster`. Solo listing and texts unchanged |
| Marker write plus latch refresh | `serve/cli._marker_write`'s body | moved into `server_marker.publish_marker(root, url, host_id) -> Path`; `_marker_write` calls it |
| Installing the bundle's host id | `server_fence.ensure_host_id` | new `value=None` keyword: equal → return; missing or malformed → write it (exclusive create, owner-only); different → `ValueError` |
| Online SQLite copy | `cutover.write_backup`'s copy core | extract `cutover.copy_database(source, target) -> {"sha256", "size"}` (read-only source, `.partial`, `os.replace`, fsync, `integrity_check`); `write_backup` calls it |
| Reading the projection set once per file | `cutover._projection_reads` | public name `projection_reads` (the old name kept as an alias) |
| Store kind and schema | `cutover.classify`, `native.db.assert_native`, `native.schema.SUPPORTED` | none |
| Watermark and record hashes | `native.projection.watermark`, `projection._digest` rules (LF/CRLF variants) | none (use `_lf`/`_crlf` through a small public `matches_record(data, hash)` if needed) |
| Atomic owner-only JSON | `serve.principals.replace_private` | none |
| Private dirs | `coordinator.ownership.private_directory`, `private_directory_at` | none |
| Path safety | `native.projection.safe_relative`, `projection_paths.safe_path` | none |
| Team mode, claims | `project.team_mode_enabled`, `native.claims.read` | none |
| Build version | `coordinator.protocol.build_identity` | none |

New files:
- `taskmaster/serve/gitrun.py`;
- `taskmaster/serve/bundle.py`, for the format and validation;
- `taskmaster/serve/untrack.py`, for the transform, `UntrackGit` and `head_tracks_taskmaster`;
- `taskmaster/serve/migration.py`, for `pack`, `undo`, `init_from`, the journals, the reports and `ROLLOUT_RULE`;
- `docs/runbooks/team-server-migration.md`, the operator runbook that 016 links (§10.1 rules included).

`serve/cli.py` gains `init` in its dispatch table.

The 009 ratchet (`tests/test_server_fence_choke.py`) also gets one entry for `serve/untrack.py`
(`private_directory` for `serve/init/`; it never opens the store). Entries for the new `sqlite3.connect` sites: the
step-9 re-check and the staging and snapshot copies. They read outside any coordinator while the lease is held. If
`copy_database` moves an existing site, its entry moves with it.

## 13. Seams

| Task | Seam |
|---|---|
| **009** | Uses `write_marker` (through `publish_marker`), `record_latch`, `ensure_host_id`, `host_lease`, `fence_roots` and `refusal`, as 009 §13.1 says. Deviations: the marker is minted by `pack` (D3), so `ensure_host_id` gains `value=`; `git_hook.check` changes in server mode (D14); `_check_marker` gains the D13 guard. 009's open question ("014 should add a test for the init marker-host path beyond the lease") is answered by §16 Step 6's daemon-binding check. |
| **013** | Uses `offline_owner(lease=)`, `drain`, `export_once`, `is_export_branch` and `check_settings`. The cycle and status gain the D13 alarm. Order deviates from 013 §9 (D7). `init` never adopts (`--adopt` stays a restore tool), and refuses an existing export branch (H8). The `--from-state` restore wrapper that 013 §3 assigned to 014 is **not** built: a restore is 013 §8.1 plus 016's how-to (§14). |
| **012** | Documents travel inside `store.db` (`external_documents`). No re-import, and `imported_by` and `origin` survive untouched. |
| **005, 011** | None at runtime. Managed-Git settings travel unchanged. A pending managed-Git marker refuses the pack (flush reports the pin). Laptop worktree and branch fields in the store describe the laptop until 011 moves them per clone (reported, not changed). |
| **Coordinator core** | `refuse_writes` is the only `service.py` change: additive, and unused outside `pack`. |
| **016** | The how-to covers, in order: every teammate on a fenced build; teammates hand over pending backlog changes; fetch, merge and `backlog_sync` on the laptop; `pack`; push (and the rejected-push rule); clone or pull on the host; `scp`; `init`; keys; `systemctl start`; delete the bundle. It also covers the §10.1 host pull rule and recovery, giving up a half-done init (§8.3: delete the clone first), `--undo --not-pushed` before the push, and `serve export status` watching `host_tracks_taskmaster`. The Linux lane runs this task's test files. |

## 14. Out of scope

- A `--from-state` restore wrapper. 013 §8.1 (archive, cutover, `restore-documents`, `export run --adopt`) already
  rebuilds a lost host, and two paths into the same host state would drift.
- Migrating a legacy or files-only project directly. It goes through the native cutover first (P5).
- Merging other clones' local stores into the server. The rule (§11) covers it.
- Rewriting history to purge `.taskmaster/` from old commits.
- Pushing, fetching or pulling, at any point. The host pull stays a runbook step with its §10.1 check.
- Turning team mode on or upgrading the schema. The identity gate does that on the first team write.
- Releasing laptop claims (O4).
- A bulk "restore projection files from the store" command for after an I1 overwrite. It is a follow-up to file,
  not built here.

## 15. Open questions (accepted 2026-11-07 as recommended)

- **O1. Non-store files under `.taskmaster/`.** Untrack them with the rest and list them as not migrated.
- **O2. Which branches get the untrack commit.** The checked-out branch plus explicit `--branch`; every other
  branch still tracking `.taskmaster/` is listed.
- **O3. The laptop's old store and `.taskmaster/` files.** Left on disk, fenced.
- **O4. Claims held by laptop sessions.** Left to expire within their TTL, and reported.
- **O5. The version floor in the rollout rule.** `FENCE_RELEASE`, a constant set to the release that ships 009.
  Until then it reads "the first release with `taskmaster serve marker`".
- **O6. Bundle protection.** A plain zip, owner-only, moved with `scp` and deleted on both machines after init.

## 16. Implementation plan (TDD)

Rules for every step:
- Run tests with `C:\Users\gruku\Files\Claude\taskmaster\.venv\Scripts\python -m pytest`, from the worktree, in the
  foreground, with `-n 2` at most and `--basetemp D:\scratchbook\taskmaster\ts014\pt<step>`. Never the full suite.
- Each step is one commit with its tests, red first.
- `load_server_module` binds one root per process, so any test that runs `pack` and `init` against two roots
  drives at least one of them as a subprocess (`python -I taskmaster_cli.py serve init …`).
- Fixtures: `test_native_service.root`, `native_git_helpers.init_repo`/`git`/`install_hook`, and the
  `native_twins` connection helpers (as `test_serve_state_export_cycle.py` uses them).
- Fingerprints exclude `.git/` and `.taskmaster/local/serve/init/` (m9). Pre-flight may create empty
  directories (`.taskmaster/local/`, `serve/init/no-hooks`); M9 is about files, refs and the index.

**Step 0. Baseline.** Run the §17 set and record the counts in the lane notes. Report anything already red; don't
edit it.

**Step 1. Shared seams (no behaviour change by default).**
Files: `serve/gitrun.py` (new), `serve/state_export.py`, `server_fence.py`, `server_marker.py`, `serve/cli.py`
(`_marker_write`), `native/cutover.py`, `coordinator/service.py` (`refuse_writes`). Tests first:
- `ensure_host_id(value=)`: missing → written and owner-only; equal → unchanged (mtime); malformed → replaced;
  different → `ValueError` with nothing written;
- `offline_owner(root, lease=held)` doesn't call `claim_host` (spy) and leaves the lease held after exit; without
  `lease`, today's behaviour;
- `publish_marker` writes the canonical marker and refreshes a stale latch; `serve marker write` output is
  unchanged;
- `copy_database` copies a WAL-mode store that has a live writer connection, passes `integrity_check`, and leaves
  no `.partial`;
- `AllowlistedGit`: a subclass refusal blocks before any process starts (spy on `managed.probe`);
- `refuse_writes`, tested in `tests/test_native_service.py`'s style:
  - with the writer paused, two queued writes plus one submitted afterwards each fail with the reason, and
    `MAX(seq)` is unchanged after `close()`;
  - a later `submit` raises;
  - a coordinator that never calls it behaves as today: the existing service tests stay green.

Proving check: `tests/test_serve_state_export*.py`, `tests/test_serve_export_cli.py`,
`tests/test_server_fence.py`, `tests/test_server_marker.py`, `tests/test_serve_marker_cli.py`,
`tests/test_native_cutover*.py`, `tests/test_native_service*.py`.

**Step 2. The validator in server mode (D14).** Files: `coordinator/git_hook.py`. Tests first, extending 009's
`git_hook` cases in `tests/test_server_fence_entrypoints.py`:
- on a marker clone, a merge commit that stages only deletions under `.taskmaster/` passes `check`, even with
  conflicts resolved elsewhere in the tree;
- a staged addition or modification under `.taskmaster/` is refused, and the text contains
  `git rm -r --cached .taskmaster` and not `git restore --staged`;
- solo mode (no marker): deletions are still refused unmanaged, with today's text, so `test_native_git_hook.py`
  stays green.

Proving check: `tests/test_native_git_hook.py`, `tests/test_server_fence_entrypoints.py`.

**Step 3. Bundle format.** Files: `serve/bundle.py`. Tests first in `tests/test_serve_bundle.py`:
- `write_bundle` then `open_bundle` round trip, byte-exact (CRLF members too). The output is created by exclusive
  create and is 0600 on POSIX (skip the mode check on Windows);
- each §5.3 refusal, one case each, with nothing extracted: not a zip; manifest missing, too big or not an object;
  format 2; wrong kind; schema 6; a bad marker; `current_branch` not in `commits`; an extra member; a missing
  member; a directory entry; a symlink attribute; a duplicate name; `../x`; `/x`; `a\b`; `C:x`; a `local/x`
  projection; an unknown config name; a size mismatch;
- `extract` with a member whose bytes don't hash to the manifest's value → refused, and the staging dir removed;
- a member larger than its declared size is cut at the cap and refused (the spy counts bytes read).

**Step 4. Untrack transform and the HEAD guard.** Files: `serve/untrack.py`. Tests first in
`tests/test_serve_untrack.py`, on temp repos with `core.autocrlf` true and false (parametrized):
- the new commit's tree differs from the parent's in exactly three root entries (`.taskmaster` gone, `.gitignore`
  changed, marker added). Its single parent is the old tip, and its message carries the trailers;
- after the current-branch sync, `status --porcelain=v1 --untracked-files=all` is empty, and every file under the
  root (`.taskmaster/**` and `local/**` included) is byte-identical to before, except `.gitignore` and the new
  marker. Running the sync a second time changes nothing;
- `.gitignore`: absent → created; CRLF blob → CRLF line appended; no trailing newline → one added; already
  ignoring (each of the four spellings) → blob id unchanged;
- a `--branch` that isn't checked out moves, with no working-tree or index change (fingerprint). A branch checked
  out in a linked worktree, a symbolic ref, an export-shaped branch and a branch whose `.taskmaster` is a blob are
  each refused before any object is written. A tip equal to a journal-recorded new commit is skipped;
- a lost CAS (the ref is moved by hand between build and move) leaves that ref untouched and moves nothing after
  it;
- signing (m6): with `commit.gpgSign=true` and `gpg.program` pointing at a script that exits 1, the build raises
  the signing refusal. With a script that records its environment, `GNUPGHOME` is passed through (other
  variables are inherited too, as built);
- no hook runs: `install_hook` puts sentinel-writing `pre-commit`, `post-index-change`, `reference-transaction`
  and `post-checkout` hooks in place, and no sentinel appears;
- the argv spy (`record=`): every call is on the §7.4 list. Calling `run` with `push`, `fetch`, `rm`,
  `reset --hard`, `checkout main`, `merge`, `clean -fd` or `stash` raises before any process starts;
- `head_tracks_taskmaster`: `None` after the untrack. It returns the commit id after checking out the parent or
  after fast-forwarding to an upstream commit that re-adds `.taskmaster/`;
- teammate and host behaviour, as executable documentation of §10:
  - a clean clone's pull removes the tracked files and keeps `local/store.db`;
  - a modified tracked file makes the pull fail;
  - a feature-branch merge conflicts modify/delete;
  - checking out the old commit brings the files back, and `server_fence.refusal` returns the latch text (the
    latch is written by a first refusal at the new commit);
  - **I1:** an ignored `.taskmaster/tasks/t1.yaml` holding `LIVE` reads the old bytes after `git checkout <parent>`
    (rc 0), and after a `pull --ff-only` that re-tracks it (rc 0).

**Step 5. `pack`: happy path, pre-flight and late writes.** Files: `serve/migration.py`. Tests first in
`tests/test_serve_migration_pack.py`, on a native fixture store with tasks, one external document and team mode
on, in a git repo:
- `pack` writes:
  - the bundle (validates; store_id, schema, `high_seq`, counts and claims match the source);
  - the marker and latch;
  - the commit;
  - `pack.json` at `done` with `high` and `gitignore_before_sha256` (recorded at `frozen`).

  No `local/serve/host-id` exists on the source afterwards;
- after `pack`, a subprocess `protocol.connect(SRC)` gets `ServerModeRefused`, and `ServeDaemon` start on SRC exits
  2 (no host-id). That is the M7 laptop half;
- each P1-P12 refusal, one test each. Exit 2, and the fingerprint is unchanged. P12 cases:
  - upstream ahead → refused;
  - upstream an ancestor → passes;
  - no upstream → warning in the report;
- a hand-edited projection file (the exporter flags it) → refused at settle with flush's notice, before the
  freeze: no marker;
- the host lease held by another process → exit 69; the store owned by a session `Coordinator` → exit 69 with
  `OWNED_TEXT`;
- **C1:** a write is submitted through the owner after the snapshot, while the writer is paused (the
  `migration.CHECKPOINT` hook at `snapshot_taken`). Its future fails with the `refuse_writes` reason. After
  `pack`, the laptop's `MAX(seq)` equals the bundle's `high_seq`, and the refs moved only after the coordinator
  stopped (a spy on `update-ref` sees `owner.server is None or stopping`);
- **step 9 re-check:** a commit injected straight into the store between the copy and the re-check (the checkpoint
  hook, raw connection) makes the pack redo the snapshot. The bundle's `high_seq` includes it;
- the not-migrated lists name a tracked `harvests/x.md` and a local `viewer.json`.

**Step 6. `pack` resume and `--undo`.** Tests first, same file, with the checkpoint hook raising at each journal
stage:
- for each of `frozen`, `snapshot`, `commits_built`, `bundled`, "after the first of two ref moves" and "after the
  current branch's `update-ref`, before `reset`":
  - a rerun with the same arguments passes pre-flight (the marker, `.gitignore`, out file and branch tips are
    journal-owned);
  - the rerun finishes and gives the result of an uninterrupted run (same tree ids; the bundle validates; refs as
    planned; status clean);
  - R1: for `bundled` and both ref-move crash points, the rerun starts no coordinator (a spy on
    `offline_owner`/`Coordinator.start` records no call) and doesn't settle. For the other stages it does settle;
  - R2: at "after the current branch's `update-ref`, before `reset`", the working `.gitignore` still has its
    pre-pack bytes. P4 accepts it through `gitignore_before_sha256`, and the rerun's sync makes status clean. A
    second crash right after `checkout-index` is accepted through the new-blob comparison. A `.gitignore` edited
    by hand in either state is refused;
- `refs` resume with an event appended to the store (raw connection, simulating a pre-009 writer) → exit 1 with
  the "written after the bundle" text, and no ref, index or file changed;
- a resume whose settle finds a flagged file (before any ref moved) undoes the freeze and exits 2;
- a rerun with a different `--url` → exit 2 naming `--undo`;
- `--undo --not-pushed` after each stage, including `done`:
  - refs, the index and the working `.gitignore` are as before;
  - the marker and latch are gone;
  - every file under `.taskmaster/` is untouched;
  - the bundle is still there;
- `--undo` refused: without `--not-pushed`; when `refs/remotes/origin/main` (made with `update-ref` in the test)
  contains the new commit; when the branch has a commit on top; when HEAD is on another branch.

**Step 7. `init --from`: happy path and pre-flight.** Tests first in `tests/test_serve_migration_init.py`. Two
repos: the source is packed in a subprocess, and the host is a clone of the source that then pulls from it
(`git fetch` + `merge --ff-only`):
- `init` places every projection file byte-exact. `local/store.db` opens natively with the source's store_id,
  entity counts, document bytes and `imported_by`;
- `host-id` equals the marker's; the latch is written; `taskmaster-state` exists with
  `Taskmaster-Store: <store_id>`, and its tree equals the projection set plus `external-documents/`;
- with `--export-push backup` to a bare repo, the backup has the branch;
- M7 host half: `server_marker.server_mode(HOST).host_id == server_fence.read_host_id(HOST)`. An in-process
  `ServeDaemon` with export off starts and stops cleanly on HOST (the 009 binding check; this answers 009's open
  question);
- each H1-H9 refusal, one test each, with the fingerprint unchanged. Specific cases:
  - H6's allowance holds: a pre-existing `local/serve/operator.token` doesn't refuse;
  - H3 passes with a CRLF working marker (m5);
  - H4 ignores a missing `--branch` commit (m4);
- a held lease (another process holding `host_lease`) → exit 69.

**Step 8. `init` resume.** Tests first, same file, with the checkpoint hook at each stage:
- each stage's rerun finishes, and the result equals an uninterrupted init;
- `done` → a rerun prints the report, exit 0;
- a different bundle with a partial journal → exit 2 (H9);
- an event appended to the published store before the rerun → exit 1 with the "written after a partial init"
  text, and nothing else changed.

**Step 9. The host HEAD guard in the daemon and the exporter (D13).** Files: `serve/daemon.py`
(`_check_marker`), `serve/state_export.py` (cycle, `export.json`, `status`). Tests first in
`tests/test_serve_daemon.py` and `tests/test_serve_state_export_cycle.py`:
- a marker root whose HEAD tracks `.taskmaster` → daemon start exits 2 with the D13 text;
- the same without a marker → starts as today;
- an export cycle on such a root still commits, records `host_tracks_taskmaster`, and logs one ERROR. `serve export
  status` exits 1 naming it. After HEAD is untracked again, the next cycle clears it and status exits 0.

Before writing the tests, check whether 009's or 013's fixtures combine a marker with a tracked `.taskmaster/`. If
they do, they model a pre-014 host: change them to untrack, and say so in the commit.
Proving check: `tests/test_serve_daemon.py`, `tests/test_serve_state_export*.py`, `tests/test_serve_export_cli.py`,
`tests/test_server_fence_entrypoints.py`.

**Step 10. CLI.** Files: `serve/cli.py`. Tests first in `tests/test_serve_init_cli.py`:
- `serve init pack …`, `serve init pack --undo --not-pushed …` and `serve init --from …` map to the functions,
  with exit codes 0, 1, 2 and 69 as in §6 and §8;
- `--json` output parses;
- the rollout rule and the rejected-push rule appear verbatim in the pack report, and the rollout rule and the
  host pull rule in the init report;
- `python -I taskmaster_cli.py serve init --help` imports neither uvicorn nor `taskmaster.backlog_server` (the 009
  Step 10 pattern);
- the module docstring's usage lines list the new commands.

Proving check: `tests/test_serve_cli.py`, `tests/test_packaging.py`.

**Step 11. Runbook, regression and fresh-eyes check.**
- Write `docs/runbooks/team-server-migration.md`. It covers §2's sequence (with step 0), the rejected-push rule,
  §10's table, and the §10.1 host pull rule and recovery (anchor `#host-head`). It also covers §11's rule,
  `--undo --not-pushed`, giving up a half-done init, and single use of the bundle.
- Run §17.
- Then one fresh reviewer, Opus, because this path is destructive-adjacent, checks the code against this spec
  before the review gate.

## 17. Regression set

Run in the foreground, `-n 2`, before Step 1 (baseline) and after Step 10:

- `tests/test_serve_*.py`, in slices as 013 did (memory pressure on this machine);
- `tests/test_server_fence*.py`, `tests/test_server_marker.py`;
- `tests/test_native_cutover*.py`, `tests/test_native_git_hook.py`, `tests/test_native_git*.py`,
  `tests/test_native_service*.py`;
- `tests/test_session_start_hook.py`, `tests/test_packaging.py`, `tests/test_root_git_discovery.py`.

The full suite runs once on `feat/team-collab` before release (rulings), not in this lane.

## 18. Post-review notes (Codex round)

- **Snapshot lock.** The snapshot copy and `refuse_writes` run inside the coordinator's `execution` lock with the writer paused, so a command already past admission finishes first and none starts until writes are refused.
- **Machine-local rows.** The bundle's copy of the store drops `sync_state` `checkout.*` records (absolute paths of this laptop's checkouts); the source store is untouched and the host registers its own checkouts.
- **Known limit, Windows.** The bundle is created owner-only (0600) on POSIX. On Windows the mode does not set an owner-only ACL; put `--out` in a directory only you can read. Not built.
- **POSIX permissions.** `init` extracts and installs the store 0600 and creates `.taskmaster/local` and `serve/` 0700 via `private_directory_at`.
- **Links.** `init` refuses to publish through a symlink or junction under `.taskmaster/`, or one that resolves outside the clone.
- **`.gitignore`.** The pack refuses a working `.gitignore` that HEAD lacks (it would be overwritten by the index copy).
