<!-- User intent: the operator procedure for moving a solo Taskmaster project onto the team server once
     (`serve init pack` on the laptop, `serve init --from` on the host), what each teammate sees afterwards, how to
     undo or give up, and the host-HEAD rules that keep the server's live files safe (team-server-014). -->

# Runbook: moving a solo project to the team server

> **Single use.** The bundle carries the host identity. Install it in one host clone only; delete it on both
> machines once the server runs.

## 1. The sequence

```
operator's laptop (source clone, push rights)       Hetzner host (host clone, read-only code key)
0. git pull; backlog_sync
1. taskmaster serve init pack --root SRC --url https://tm.example.com/mcp --out ~/proj.tmbundle
2. git push origin main                              4. git pull --ff-only (see "Pulling on the host")
3. scp ~/proj.tmbundle host:                         5. taskmaster serve init --root HOST --from proj.tmbundle
                                                     6. serve token / add-user ..., systemctl start taskmaster
```

0. Pull, then run `backlog_sync` so the store takes any `.taskmaster/` changes teammates pushed.
1. `pack` refuses without writing anything when the clone is not ready (the message names the problem). It then
   freezes the laptop (marker and latch), settles the export, snapshots the store, refuses late writes, stops the
   coordinator, re-checks the store and only then untracks `.taskmaster/` in a local commit. Nothing is pushed.
   `--branch NAME` untracks further local branches that also track `.taskmaster/`.
2. Push by hand. `--undo --not-pushed` is the way back until then.
3. The bundle sits outside the repository. Copy it with `scp`.
4. On the host: `git fetch`, run the checks in "Pulling on the host", then `git merge --ff-only @{u}`.
5. `init` refuses unless HEAD carries the bundle's marker and tracks no `.taskmaster/`. It stages, publishes (the
   store last), verifies, makes the first export to `taskmaster-state` and records the latch.
6. Mint the operator token and a key for each teammate (and your own laptop agent, `--role operator`), start the
   service and check `serve export status`. Turn team mode on before any teammate's first write (deploy runbook,
   section 5: `init` leaves it off).

Both commands keep a journal (`.taskmaster/local/serve/init/pack.json`, `init.json`). After a crash, run the same
command again with the same arguments: it resumes from the recorded stage.

## 2. The rollout rule

Before you push the commit that adds `.taskmaster-server.json`:

1. Everyone who works in a clone of this repository must run the first Taskmaster release that has
   `taskmaster serve marker` (or later): a build with the split-brain fence. Older builds don't know the marker.
   They keep opening, or quietly creating, a local backlog, and their work never reaches the server. Check every
   machine, including CI runners and other machines of your own.
2. Anyone with backlog changes that aren't in your store yet (edits in their own local store, or `.taskmaster/`
   changes on unmerged branches) hands them over first. The server starts from your store as it is at the pack,
   and nothing else is migrated.

After the push: anyone still on an older build updates before their next session in this repository. Until they
have their own API key, their taskmaster tools refuse here, and nothing is written locally. When merging the new
main into an older branch, keep the deletion of `.taskmaster/` (`git rm -r --cached .taskmaster`); never add it
back.

## 3. A rejected push

If the push is rejected, don't pull or rebase over the pack commit. Teammates pushed something after your last
fetch. Merging it now would drop their `.taskmaster/` changes, or track `.taskmaster/` again. Instead:

1. `taskmaster serve init pack --undo --not-pushed --root <this clone>`
2. `git pull`
3. `backlog_sync`
4. run the pack again.

## 4. Undo, and giving up

- **`pack --undo --not-pushed`** puts refs, the index and `.gitignore` back, removes the marker and the latch and
  leaves every file under `.taskmaster/` and the bundle where they are. It refuses when a remote-tracking ref
  contains the pack commit, when HEAD is on another branch, or when a branch has commits on top. If the pack
  commit is already public, undoing unfences this laptop while every other clone stays fenced: revert the commit
  with `git revert` and run `taskmaster serve marker clear-latch` in every clone instead.
- **A half-done `init`.** There is no `--abort`; the host clone is dedicated. Stop anything running there,
  **delete that clone** before cloning again (a second clone installed from the same bundle would share its
  `host_id`), clone again and run `init` from the start.

## 5. What each clone sees afterwards

| Clone | What happens |
|---|---|
| Operator's laptop (the pack ran here) | Every `.taskmaster/` file, `local/store.db` included, is still on disk. `git status` is clean. Every taskmaster tool refuses with the marker text. Don't check out a branch that still tracks `.taskmaster/` here: Git would overwrite the fenced files with that branch's copies. |
| Teammate, clean clone, `git pull` | Git deletes the formerly tracked `.taskmaster/` files. Ignored `local/` (their old `store.db`) stays. Their next tool call refuses and latches; session-start tells them to use the server's MCP with their key. |
| Teammate with uncommitted edits to a `.taskmaster/` file | Git refuses the pull. They discard or stash those edits (they were never going to reach the server), then pull. |
| Teammate feature branch that changed `.taskmaster/` | Merging the new main gives `CONFLICT (modify/delete)`. Keep the deletion: `git rm -r --cached .taskmaster`, then commit. The pre-commit check allows the staged deletions and refuses staged additions or changes. |
| Any clone checking out a pre-migration commit | Git brings `.taskmaster/` back, overwriting ignored files of the same name. The latch keeps every local store closed until `serve marker clear-latch`. |
| Host clone | Tracks no `.taskmaster/` after `init`, but Git silently overwrites its ignored live files on any checkout or pull of a commit that tracks it. See the next section. |

Anything a teammate kept only in their own local store, or in unpushed `.taskmaster/` edits, is not migrated.

Migrated history has no recorded author. Agents see it wrapped as `~unattributed` text until the operator names its owner with `serve set-user <handle> --owns-pre-team-history`; that person reads it unwrapped, everyone else keeps seeing it wrapped. The cutoff is the migration's last sequence number, recorded in the init journal.

## 6. The host HEAD guard {#host-head}

<a id="host-head"></a>

The daemon refuses to start while the host clone's HEAD tracks `.taskmaster/`. While it runs, every export cycle
checks too: it still writes the backup, but records `host_tracks_taskmaster` in `export.json`, logs an ERROR at
most once an hour and makes `serve export status` exit 1.

**Pulling on the host** (once `init` has run; the very first pull of a host clone made before the push removes
the tracked files by design, so clone after the push or pull before `init`). First `git fetch`. Then `git diff --name-only HEAD @{u} -- .taskmaster` must print nothing
and `git ls-tree @{u} -- .taskmaster` must be empty. Only then run `git merge --ff-only @{u}`. If either prints
something, upstream re-tracked `.taskmaster/`: fix main from the laptop (`git rm -r --cached .taskmaster`, commit,
push) and don't pull.

**Never** run `checkout`, `switch`, `reset` to another commit, `bisect` or `stash` in the host clone. Ignored files
are overwritten without `-f`.

**Recovery after an overwrite.** Stop the daemon. Fix main from the laptop as above, then pull on the host with the
check. A pull back to an untracked commit deletes the re-tracked files from the host's working tree. Start the
daemon: the store is still the authority. Its exporter republishes a missing file at that entity's next change.
Files that Git overwrote, and that still differ, are held as conflicts: resolve them with
`backlog_resolve_conflict(take="store")` from an operator key.

## 7. Open claims and what is not migrated

The pack report lists the claims the laptop holds (and their latest expiry) and what was left behind: tracked
`.taskmaster/` files outside the projection set (such as `harvests/` and `lessons/`) and the local-only files
(`viewer.json`, `snapshots/`, keys). Move those by hand if you want them on the host.
