<!-- User intent: the task-level design for team-server-013. The team server's store is the authority, so
     its `.taskmaster/` export is committed to a `taskmaster-state` branch on a schedule and optionally pushed
     to a Git remote: the team's backup and history, without ever touching code branches, the working tree or secrets. -->

# team-server-013: state export to `taskmaster-state` (design, 2026-11-01, rev 2)

**Task.** `team-server-013`. Done when the host commits its `.taskmaster/` export to the
`taskmaster-state` branch on a schedule, and optionally pushes it to a Git remote as backup and
history.
**Source.** `docs/specs/2026-10-31-team-server-mode-addendum.md` §8 (export and history), §13
(operations: "backups are the task 13 export commits pushed to a Git remote"); rev 3.3
`2026-10-31-team-collaboration-native-design.md` R3, §6.2 (temporary-index tree building), §6.8
(unattended Git).
**Builds on.** 001 (`ServeDaemon`, `ServeCoordinator`, `local/serve/`), 002 (`principals.json`), 006
(merged), 012 (content uploads in `external_documents`), and the native managed-Git module
`taskmaster/coordinator/git.py` (`generation`, `probe`, `environment`, `QUIET_GIT`).
**Review history.**
- Rev 1 (4b25f5f): the design review came back WARN, with 0 Critical, 5 Important and 14 Minor
  findings (`D:\scratchbook\taskmaster\ts013-review\review.md`).
- Rev 2 (this revision):
  - fixes I1–I5;
  - folds in the Minors the orchestrator listed (M3, M4, M7, M8, M1, M6), plus the cheap M2, M5,
    M9, M10, M11, M13 and M14;
  - backs up `external_documents` (012 review I1);
  - records the user's rulings on Q1–Q7 (§12) and 016's Q5 (§6.3).
- Rev 3 (fix round 2): the scoped re-review at 22c9ee1 closed I1–I4 and left two findings open.
  - RR-I1 (residual I5): a no-op cycle no longer counts as "in sync" while the store owes export,
    for example under a managed-Git pin (§5.1, §7.4).
  - RR-I2: each document's source path and content go through a secret filter, and the 005
    dependency is stated (§4.5, §9).
  - Also: document blobs are written before `publication` is taken, which addresses the
    non-blocking spawn-count note.
**Verified against.** Git 2.48.1 for Windows, with two spikes under `D:\scratchbook\taskmaster\ts013\`
and the reviewer's probes under `ts013-review\`:
- `spike_plumbing.py`. A temporary-index commit-tree export left HEAD, the symbolic ref, the index
  digest and `status -z` byte-identical. `hash-object --no-filters` stored exact CRLF bytes under
  `core.autocrlf=true`. A create-CAS on an existing ref failed. `update-ref` moves a checked-out
  branch without complaint (G2). A non-fast-forward push was rejected with `!`.
- `spike_growth.py`. On a copy of claude-tools' real backlog (592 files, 1.19 MB), a one-file export
  took 0.47 s and added 8.4 KiB of loose objects. The repository holding the first export plus 200
  one-file commits packed to 716 KiB after `git gc`.
- Reviewer probes:
  - a forged `Taskmaster-Export` trailer on a code branch (I2);
  - `update-ref` following a symbolic ref (I2);
  - `maintenance run --task=loose-objects` leaving refs, reflogs and worktree admin data unchanged
    (I4);
  - `git archive` applying autocrlf (M4);
  - `check-ref-format --branch @{-1}` expanding to another branch (M1).

## 1. Problem

In server mode the host's SQLite store is the only authority (addendum §2). Nothing else holds the
team's backlog: teammates' clones no longer carry `.taskmaster/` once 014 untracks it, and after
012 an uploaded document exists only in the store's `external_documents` table. A lost disk loses
both.

The coordinator already keeps a verified file export in `.taskmaster/` (the projection outbox,
`native/projection.py`). What's missing is a step that takes snapshots of that export, plus the
stored documents, into Git history and, optionally, ships them off the machine.

The path writes Git objects and pushes, so most of this design covers what it must never do.

## 2. Safety invariants

These are the acceptance criteria. Each is backed by a test in §10.

| # | Invariant | How it holds |
|---|---|---|
| G1 | **Locally, the only ref it writes is `refs/heads/<export-branch>`** (and that ref's reflog). It never writes HEAD, the real index or the working tree. A successful push also updates Git's remote-tracking ref `refs/remotes/<remote>/<b>`. | Plumbing only (§4). `update-index` and `write-tree` always run with `GIT_INDEX_FILE` set to a private temporary file under `local/serve/export/`. Every argv passes the allowlist (§4.4). A test fingerprints HEAD, the symbolic ref, the index digest, `status --porcelain=v1 -z`, `for-each-ref` (minus the export ref and exactly that remote-tracking ref), every `logs/refs/**` file except the export ref's, and `.git/worktrees/*`, before and after a full cycle with push and maintenance. |
| G2 | **Moves the export ref only fast-forward, by compare-and-swap, from a tip proven to be this store's export history, while no managed-Git operation can run.** | `update-ref --no-deref <ref> <new> <old>` with the tip read under `publication`, which is held from the guards through `update-ref` (I1, §4.2). Managed Git holds `publication` for its whole operation (`git.py:757-763`), so no `git_run checkout` can interleave. `<new>`'s only parent is `<old>`. The tip must pass the lineage guard (§4.3): recorded, or reached through export-shaped commits; never on the strength of one trailer. Refused for a symbolic ref, a branch checked out in any worktree, or a default branch. Out of scope: an operator running shell Git by hand in the host clone during the sub-second window. |
| G3 | **Pushes only `refs/heads/<b>:refs/heads/<b>`, never forced, only to the operator's named remote.** | Fixed refspec without `+`. No `--force`, `--force-with-lease`, `--mirror`, `--all`, `--tags`, `--delete` or `--prune`, enforced by the argv allowlist. A remote with `remote.<name>.mirror=true` is refused at start. `-c push.followTags=false -c push.recurseSubmodules=no`. A rejection is reported and never retried with force. |
| G4 | **Commits only store-owned data, never secrets.** | Projection part: `projection` rows `NOT LIKE 'local/%'` (the set `git.generation` reads). Document part: `external_documents` rows, at generated paths (§4.5). A second, independent filter runs over every tree path. It drops any path with a `local` first component, any component starting with `.`, any `git~1` component, any path containing `\n` or `\r` or starting with `"` (`--stdin-paths` would split or C-unquote it), and the names `operator.token`, `principals.json`, `principals.lock`, `discovery.json`, `serve.log`, `export.json`, `store.db*`, `*.token` and `*.key`. A dropped path is logged at WARNING and listed in the commit body; it is never committed. A document row is also checked on its *source*: its recorded `path`, its task's declared `docs[section]` path, and its body. It is skipped and reported when either path or the body fails §4.5's secret filter (RR-I2). Projection blobs come from a generation-verified read through `safe_path`/`check_component` (no symlinks; regular files ≤ `MAX_PROJECTION_BYTES`). Document blobs come from the store row. Mode is always `100644`. |
| G5 | **Configured only by the operator.** | Settings come from the `serve` process's flags only, plus the host clone's own Git and SSH config and the daemon's own environment for remote URLs and credentials. 013 ignores `project.yaml` `team.sync.*`, which members can edit through project tools and which configures the deferred Git sync, and the §6 marker, which is tracked. Tool arguments and HTTP requests play no part. No MCP tool, coordinator IPC method or HTTP route triggers, configures or reads the exporter. |
| G6 | **Runs no repository code and never prompts.** | `-c core.hooksPath=<empty private dir>` on every call. This overrides a repository-config `core.hooksPath`, and silences `post-index-change`, `reference-transaction` and `pre-push`. `push --no-verify`. `commit-tree --no-gpg-sign`. A fixed author and committer through the environment. `GIT_TERMINAL_PROMPT=0` and `GCM_INTERACTIVE=never`. SSH runs in batch mode (§6.1). No `fetch`, `checkout`, `reset`, `add`, `commit`, `stash`, `clean`, `merge`, `rebase`, `prune`, `reflog`, `gc` or `pack-refs`. The only maintenance is `maintenance run --task=loose-objects` (§7.5). |
| G7 | **Never records a remote URL or credentials.** | The remote URL is never read into Python state: `remote get-url --push` is used for its exit code only, with stdout discarded. Push output is parsed from porcelain status lines only (the `To <url>` line is dropped). Git's stderr and stdout are redacted before anything builds a message: every `scheme://…` token and every scp-style `user@host:path` token becomes `<remote:NAME>`. The exporter's runner calls `probe(..., ok=<all codes>)` and formats its own errors, so `probe`'s unredacted `GitRefused` text is never raised on this path. Messages are capped at 500 characters. Only the remote *name* appears in logs, `export.json` and `status`. |
| G8 | **Never blocks or fails a tool call, and never writes `store.db`.** | Read-only connections only. `publication` is held for the read and build phases (§5.2): about 3–7 s on a 3.7k-file store, and only when something changed. It is acquired with `PUBLICATION_TIMEOUT`. While it is held, writes keep committing and only file export waits. Push and maintenance run without it. An export failure is logged and retried; it never stops the daemon. |
| G9 | **Solo mode unchanged.** | Only a process started by `taskmaster serve` runs it. `ServeConfig.export_interval` defaults to 0 (off), so existing in-process daemon tests see nothing new; only the CLI turns it on (Q1). Session coordinators, `taskmaster git`, hooks and the managed-Git path behave as before. |
| G10 | **One store per branch** (I3). | `export.json` records this store's `store_id`. A tip whose `Taskmaster-Store` is another store is accepted only by an explicit, recorded adoption (`serve export run --adopt`, §5.1), and refused ever after. The CLI's default-on export is off when the root is a linked worktree. |

## 3. Scope

In scope:

- the scheduled exporter thread in the serve daemon;
- commit mechanics, including the document part;
- the optional push;
- `serve export status`, `serve export run [--adopt]` and `serve export restore-documents`;
- serve flags;
- the restore procedure and a test that proves it.

| Not here | Owner |
|---|---|
| `serve init --from`, untracking `.taskmaster/` on code branches, a `--from-state` restore wrapper | 014 |
| Split-brain marker and the serve-ownership fence | 009 |
| systemd unit, deployment how-to (backup repo and key, §6.3; full gc and reflog policy, §7.5), Linux CI lane | 016 |
| Export health over the read endpoint or `backlog_store_status` | not in 013 (Q6, ruled) |
| SQLite `.backup` snapshots; backing up `local/serve/` (keys) | not planned (§8.3) |
| Reviving rev 3.3 Git sync on this branch | deferred epic (§4.1) |

## 4. Branch mechanics

### 4.1 Where the branch lives and what it holds

- **Repository.** The host clone itself (addendum §8; Q7 ruled), ref `refs/heads/taskmaster-state`,
  or `--export-branch`. It is an orphan history: the first export is a root commit. Repository
  formats:
  - a `sha256` object format (`rev-parse --show-object-format`) is refused at start, because blob
    ids are compared as SHA-1;
  - a `reftable` ref format (`rev-parse --show-ref-format`) is refused too, because the CAS and the
    stale-lock handling assume files refs.
  Both refusals carry a clear message.
- **Layout: data at the tree root**, with no `.taskmaster/` prefix (Q3 ruled):
  - projection files: `backlog.yaml`, `project.yaml`, `tasks/…`;
  - stored documents: `external-documents/…` (§4.5).
  This matches rev 3.3 §6.7. A `.taskmaster/` ls-tree check on the branch is empty. Restore is
  `git archive` into `.taskmaster/` (§8).
- **Bytes.** The working exporter's bytes exactly (`hash-object --no-filters`), not rev 3.3's
  canonical render. The `Taskmaster-Export: 1` trailer is the format version. A revived Git sync
  would need its own canonical tree or a version bump.
- **Commit.**
  - Author and committer: `Taskmaster export <taskmaster-export@localhost.invalid>`, dated now.
  - Subject: `taskmaster export: seq <N>, <F> files, <D> documents`.
  - Body: `+added ~modified -removed` counts, then any held or dropped paths (at most 20 listed).
  - Trailers:
    - `Taskmaster-Export: 1`;
    - `Taskmaster-Store: <store_id>`;
    - `Taskmaster-Seq: <N>` (`exported_through` at the read);
    - `Taskmaster-Build: <build>`;
    - `Taskmaster-Held: <k>` when k > 0;
    - `Taskmaster-Adopted-From: <store_id>` on the first commit after an adoption.

### 4.2 One cycle

```
pre-check (no lock): owner.publication_refusal() → deferred; skip when nothing changed (§5.1)
document snapshot (no lock): one read transaction over external_documents, filtered (§4.5);
    blobs not already in the parent tree are written now, in one batched
    hash-object -w --no-filters --stdin-paths over doc-*.tmp files in local/serve/export/,
    each id checked against the id computed in Python; the tmp files are then removed
acquire publication (PUBLICATION_TIMEOUT); else deferred
  re-check: owner.stopping → stop; owner.publication_refusal() or owner.git_active → deferred
  guards (§4.3), reading tip = rev-parse --verify -q refs/heads/<b>          (I1: under the lock)
  read phase
    through = outbox.exported_through(conn); high = MAX(domain_events.seq)
    caught_up = not progress.owes_through(conn, high) and outbox.watermark(conn) >= high   (§5.1)
    gen, files, mismatched = git.generation(owner, through)                  (reused, unchanged)
    projection blobs not already in the parent tree:
        git hash-object -w --no-filters --stdin-paths     (.taskmaster/<rel> per line)
        each returned id (exact disk bytes) must be in gen.blobs[rel] ({exact, LF} of the verified read);
        anything else aborts the cycle
  check owner.stopping again; if set, abandon (nothing written but unreferenced objects)
  build phase
    entries = projection generation files
            + held: each mismatched rel keeps its parent-tree blob; a new one is omitted   (Q2 ruled)
            + document entries
            - G4 filter
    GIT_INDEX_FILE=<local/serve/export/index-<pid>-<n>.tmp> git update-index -z --add --index-info
    tree = git write-tree (same GIT_INDEX_FILE); if tree == parent's tree: no commit
    commit = git commit-tree --no-gpg-sign <tree> [-p <tip>] -F -     (message on stdin)
    git update-ref --no-deref -m "taskmaster export" refs/heads/<b> <commit> <tip|0{40}>
  record export.json (commit, through, store binding, held ages, caught_up → last_ok or deferred_since, §7.1)
release publication
push phase (§6), no lock
maintenance phase (§7.5), no lock, at most once a day
```

- **Lock span (I1).** `publication` covers the guards, both read phases and all four build calls.
  The build calls are sub-second plumbing, so the span grows by well under 1 s over rev 1.
- **CAS.** If something else moved the ref, the CAS fails. The tip is read under the lock, so only
  shell Git run by hand can do that. The cycle ends with `commit_failed` and doesn't retry inside
  itself. Objects already written are unreferenced and inert.
- **Crash safety.** No durable marker is needed: nothing here touches files Git could half-write,
  and `update-ref` is atomic.
- **Stray temporary indexes.** At the start of each cycle, the exporter deletes files in
  `local/serve/export/` that match exactly `index-*.tmp` or `doc-*.tmp`, and only after an `lstat` shows a regular
  file (not a link and not a reparse point). It never recurses and never deletes anything else.
- **Stale `<ref>.lock`.** One left by a killed `update-ref` makes the cycle fail with
  `ref lock present: <path>; remove it if no Git process is running`. The exporter never deletes it.

### 4.3 Guards (under `publication`, before the read phase)

The cycle refuses (`commit_failed`), logs, and backs off when any of these is true:

1. `repository(root)` fails: the root isn't the Git top level (`git.py:100`).
2. **Symbolic ref** (I2). `symbolic-ref -q refs/heads/<b>` succeeds. `update-ref --no-deref` is
   also always used.
3. **Checked out** (I1). Any worktree has `refs/heads/<b>` checked out, per
   `worktree list --porcelain`. On Windows the comparison is case-insensitive, because loose refs on
   NTFS are.
4. **Default branch** (I2). `<b>` is `main`, `master`, `init.defaultBranch`
   (`config --get init.defaultBranch`), or the push remote's HEAD branch (`symbolic-ref -q
   refs/remotes/<remote>/HEAD`). These are also refused at start.
5. **Lineage** (I2). The ref exists and its tip isn't provably this store's export history. Let
   `rec` be `export.json.commit` for this `store_id`:
   - **With a record.** The tip equals `rec`, or `merge-base --is-ancestor rec tip` holds and every
     commit in `rev-list --first-parent rec..tip` is export-shaped. Anything else is refused.
   - **Without a record** (first run, or `export.json` lost). Every commit in
     `rev-list --first-parent tip`, down to the root, must be export-shaped. This one walk is
     cached by writing the record once it passes.
   - **Export-shaped** means:
     - exactly one parent, or none for the root;
     - a `Taskmaster-Export` trailer;
     - a top-level tree holding only store names: `backlog.yaml`, `project.yaml`, the top
       directories of `projection_parse.ENTITY_FILE_SPECS`, and `external-documents`;
     - no `.taskmaster` entry.
   - The walk reads `rev-list --first-parent --format=%H%x00%T%x00%(trailers:key=Taskmaster-Export,valueonly)`
     and the root trees through one `cat-file --batch`. It reads top-level entries only, so it stays
     cheap at about 35k commits.
   - A forged trailer on a code branch fails the tree-shape test at that branch's own tip.
6. **Store binding** (I3, G10). The tip's `Taskmaster-Store` must be this store. A different store
   is accepted only during `serve export run --adopt` (§5.1). The adoption is logged at WARNING and
   recorded in `export.json` as `adopted: {from_store, commit, at}`, and the next commit carries
   `Taskmaster-Adopted-From`. Once a record exists, a foreign-store tip is refused with "another
   store (<id>) is exporting to <b>; stop it or choose another --export-branch". Without `--adopt`,
   a foreign tip with no record is refused with "run `serve export run --adopt` if this is a
   restore".
7. `<b>` fails validation. The exporter checks `project.valid_branch_name(<b>)` (rejects `@{`,
   `..` and the rest), then `check-ref-format refs/heads/<b>` without `--branch`, so `@{-1}` can't
   expand. Git's output is never used; the input string is. This check also runs at start.

### 4.4 Argv allowlist

`state_export` sends every Git call through one runner. The runner refuses, by raising before any
process starts, anything other than these:

- `rev-parse` (`--verify -q <rev>`, `--show-toplevel`, `--absolute-git-dir`, `--git-common-dir`,
  `--show-object-format`, `--show-ref-format`);
- `symbolic-ref -q refs/heads/<b>` and `symbolic-ref -q refs/remotes/<remote>/HEAD`;
- `worktree list --porcelain`;
- `rev-list --first-parent --format=<fixed> <range>` and `merge-base --is-ancestor <a> <b>`;
- `cat-file --batch` and `ls-tree <commit>` (top level only);
- `for-each-ref --format=<fixed> refs/heads/<b>`;
- `check-ref-format refs/heads/<b>`;
- `remote get-url --push <remote>`, with stdout discarded;
- `config --get`, for `remote.<remote>.mirror`, `core.sshCommand` and `init.defaultBranch` only;
- `hash-object -w --no-filters --stdin-paths` (projection paths under `.taskmaster/`, or `doc-*.tmp`
  files under `local/serve/export/`);
- `update-index -z --add --index-info` and `write-tree`, both only with `GIT_INDEX_FILE` matching
  `local/serve/export/index-*.tmp`;
- `commit-tree --no-gpg-sign`;
- `update-ref --no-deref -m <msg> refs/heads/<b> <new> <old>`, with exactly three operands and the
  configured ref;
- `push --no-verify --porcelain <remote> refs/heads/<b>:refs/heads/<b>`;
- `maintenance run --task=loose-objects --quiet`.

A spy test records every argv from a full cycle, with push and maintenance, and checks it against
this list. A second test feeds forbidden argv and expects a refusal:

- `push --force` and a `+` refspec;
- `update-ref` without `--no-deref`, or on `HEAD`;
- `update-index` without `GIT_INDEX_FILE`;
- `gc --auto`;
- `config --get user.name`.

The runner reuses `git.probe` (bounded through `run_bounded`, `CREATE_NO_WINDOW`, `GIT_*` scrubbed).
`probe` gains one keyword, `env=None`, merged after the scrub. It accepts only these keys and raises
on any other, so `GIT_DIR` can never come back in:

- `GIT_INDEX_FILE`;
- `GIT_AUTHOR_*` and `GIT_COMMITTER_*`;
- `GCM_INTERACTIVE`;
- `GIT_SSH_COMMAND`, `GIT_SSH` and `GIT_SSH_VARIANT`.

### 4.5 Stored documents (`external_documents`, 012 I1)

After 012, `backlog_document_import(content=…)` uploads live only in `external_documents`
(`schema.py:79`: `entity_key, section, path, body, content_hash, imported_seq`). File imports live
there too. Every row whose entity isn't deleted is exported:

- **Blob.** `external-documents/<kind>/<public_id>/<section>.md` holds the row's `body` encoded as
  UTF-8. It is already LF-normalized by the import, and its SHA-256 is `content_hash`.
  - `kind`, `public_id` and `section` come from `entity_core` and the row, and must pass the
    store's identifier check and `section ∈ TASK_DOC_SECTIONS`.
  - A row that fails is dropped with a warning (G4).
  - The row's `path` is never used as a tree path. It is data in the manifest.
- **Manifest (the 012/013 shared restore contract, orchestrator ruling).**
  `external-documents/index.json` is sorted, with LF line endings, one entry per blob:
  `{kind, id, section, path, sha256, source, imported_by, imported_seq}`.
  - `path` is the declared path.
  - `sha256` is the row's `content_hash`.
  - `source` is `upload` or `file`, and nothing else. It comes, with `imported_by` (the handle, or
    null), from the `document.import` event at `imported_seq`, as 012's `retrieve` provenance reads
    them.
  - For a document that was itself restored, the event has `origin="restore"`, and 013 reads the
    `source` and `imported_by` that the restore passed in and 012 recorded. A second-generation
    backup therefore keeps the original values.
  - Before 012, `source` is `file` and `imported_by` is null.
- **Secret filter on the source (RR-I2).** A document can hold the text of any host file it was
  imported from. Before 005 Part B, a member path import reads any file under the project root
  (`native_routing/documents.py:74-95`). Operator and solo-mode history can hold such imports
  regardless. So 013 checks each row itself, independently of how it was imported. A row is
  **skipped**, so that neither its blob nor its manifest entry is exported, when any of the
  following holds:
  - **Its recorded `path` or its task's current declared `docs[section]` path fails.** Paths are
    normalised first (`\` becomes `/`, a leading `./` is dropped) and compared
    case-insensitively. A path fails when it:
    - is absolute, has a drive letter, or has a `..` component;
    - has any component starting with `.` (`.git/config`, `.env`, `.env.local`, `.ssh/…`, `.aws/…`,
      `.npmrc`, `.netrc`, `.taskmaster/…`);
    - has any `local` or `git~1` component;
    - has a final name in the denylist: `operator.token`, `principals.*`, `discovery.json`,
      `export.json`, `serve.log`, `store.db*`, `*.token`, `*.key`, `*.pem`, `*.p12`, `*.pfx`,
      `*.jks`, `*.keystore`, `*.kdbx`, `id_rsa*`, `id_dsa*`, `id_ecdsa*`, `id_ed25519*`,
      `credentials`, `credentials.*`, `secrets.*`, `*.env`.
  - **Its body contains a private-key armour line** (`-----BEGIN ` … `PRIVATE KEY-----`). This
    cheap content check catches a key pasted under a harmless name.

  A skipped row is logged at WARNING and listed in the commit body as `dropped document:
  <kind>/<id>/<section> (<rule>)`. The path itself is never printed. It is also counted in
  `export.json` (`dropped_documents`) and in `status`. It stays in the store; only the backup
  omits it. A legitimate document whose source sits under a dot directory (for example
  `.github/`) is skipped as well. That is accepted as the safe default, and the report names the
  rule so the operator can move it.
- **Snapshot.** Rows and manifest come from one read transaction, taken **before** `publication`
  in a pre-lock phase (§4.2). Documents aren't written by the projection exporter, so the lock adds
  nothing to their consistency. New blobs are written in one batched `hash-object --stdin-paths`
  over private `doc-*.tmp` files, so a first export or a bulk import doesn't lengthen the lock
  span. Documents are never held forward: the store row is the source.
- **Restore.** `taskmaster serve export restore-documents --root R --from <commit>`, run with the
  daemon stopped, re-imports the documents.
  - It reads the manifest and blobs from the commit with `cat-file --batch`, checks each blob's
    SHA-256 against `sha256` (a mismatch refuses that entry), and re-applies the source filter
    (§4.5), so a backup written by an older build can't bring a secret back.
  - It starts a `ServeCoordinator` in-process (as `serve export run` does) and submits one
    `document.import(restore=True)` command per entry through the normal command path, as the
    host-local operator, passing the entry's `section`, `path`, body, `source` and `imported_by`.
  - Per the shared contract, 012 owns `restore=True`. The command accepts it only from the
    host-local operator (origin `LOCAL`; refused for any remote principal). The ordinary path would
    refuse an operator re-import of an upload as `NOT_A_PERSON` and refuse `source="file"`, so
    `restore=True` is the only path. 012 records `origin="restore"` together with the original
    `imported_by` and `source`.
  - Until 012 lands, `restore-documents` refuses with "needs 012's `document.import(restore=…)`",
    rather than importing documents without their provenance. The step 10 restore test is then
    marked to require it.
  - It reuses the existing command. There is no direct SQL.
  - Re-running it is a no-op, because identical prose writes nothing (`commands.py:241-242`).

## 5. Schedule and triggers

### 5.1 When a cycle runs

- **Interval.** `--export-interval SECONDS`: CLI default 900 (Q1 ruled), 0 turns it off, range
  60–86400. The first cycle runs `min(60, interval)` s after the daemon is ready. Later cycles run
  `interval` after the previous cycle started.
- **Linked worktree (I3).** When the root is a linked worktree (`git_dir != common_dir`), the CLI
  default is 0. Only an explicit `--export-interval` turns export on there, and G10's store binding
  still applies.
- **Refusal first (RR-I1).** The pre-check reads `owner.publication_refusal()`, which needs no lock
  because it reads `git_pin`. When it is set, the cycle is `deferred` (and `deferred_since` is set
  if empty), whatever else matches.
- **Skip when unchanged.** Before taking any lock, a read-only connection compares
  `exported_through` and `MAX(external_documents.imported_seq)` with `export.json`, and the ref tip
  with its `commit`. When all match and nothing is held, nothing is committed. A push may still be
  owed (§6).
- **"In sync" requires that the store owes no export (RR-I1).** Every cycle, no-op or commit,
  computes `caught_up`. Let `high = MAX(domain_events.seq)`. Then `caught_up` holds when **both**
  of these hold:
  - `not progress.owes_through(conn, high)`, the reviewer's check (`service.py:411`), which the
    coordinator's own flush barrier uses for PROGRESS debt;
  - `outbox.watermark(conn) >= high`. `outbox.watermark` is a public, read-only name for
    `native/projection.py`'s `_through`, which recomputes the
    everything-below-the-oldest-unexported-job watermark without writing it.

  `owes_through` alone reads only `progress.pending.*` keys and the seeding flags. Projection jobs
  that a pin leaves pending don't show there, so the watermark term is what makes the pinned case
  fail. Both reads are lock-free, in one read transaction.
  - A successful cycle with `caught_up` stamps `last_ok` and clears `deferred_since`.
  - A successful cycle without it records state `behind`, and sets `deferred_since` if empty. It
    doesn't stamp `last_ok`.
  - A pin, or any other stall in projection export, therefore ages into status exit 1 (§7.4),
    instead of producing endless green no-ops that mirror a stalled export.
  - Held (drift) entities are already left out of the watermark; they are reported through
    `held`.
  - The normal sub-second lag after a write can make one cycle `behind`. It only matters once
    `deferred_since` is older than `stale_after`, and the next caught-up cycle clears it.
- **Starvation.** `threading.RLock` isn't fair. After three consecutive busy deferrals, the next
  acquire waits 60 s instead of `PUBLICATION_TIMEOUT`.
- **Stop.**
  - `ServeDaemon.request_stop` signals the exporter at once, so it overlaps uvicorn's 10 s
    graceful window.
  - The exporter checks `owner.stopping` right after acquiring `publication` and between the read
    and build phases.
  - `_teardown` joins the exporter for at most 10 s before it stops the coordinator, which covers
    an in-flight read phase (about 6 s at 3.7k files). This stays within systemd's 30 s
    `TimeoutStopSec`.
  - A push still running is abandoned on a daemon thread: its child either completes a
    fast-forward or is killed with the unit's cgroup.
  - There is no shutdown export: the backup protects against losing this disk, and the next start
    exports first.
- **On demand, offline.**
  `taskmaster serve export run --root R [--export-branch B] [--export-push REMOTE] [--adopt]`:
  - When any coordinator or the daemon owns the store, it exits 69: "stop the daemon first".
  - Otherwise it starts a `ServeCoordinator` in-process, exactly as `ServeDaemon` does and through
    009's fence once that lands. It waits for its startup drain (`flush` through
    `MAX(domain_events.seq)`), runs one cycle and the push, and stops.
  - `--adopt` permits the one recorded foreign-store adoption (§4.3 guard 6) and exists only here,
    never as a daemon flag.
  - A live "export now" while the daemon runs is deferred (Q4 ruled).

### 5.2 Lock cost

The span is `git.generation`'s verified full read, the same cost N16 measured and accepted for
managed Git (3.1–6.2 s for 3,708 files). Add to that:

- `hash-object` for changed projection blobs only (documents are read and written before the lock);
- four sub-second plumbing calls.

While it runs, a foreground `flush` waits its usual 10 s and may answer
`export pending: publisher busy`. That is the existing notice; writes still commit.

At the 900 s default, and only when something changed, that holds `publication` for under 1% of
the time.

## 6. Push

### 6.1 Configuration

- `--export-push REMOTE` names a remote already configured in the host clone.
- The name is validated with `project.py`'s `_valid_remote`, made public as `valid_remote_name`
  (reused, not copied).
- `git remote get-url --push REMOTE` must succeed. Only its exit code is used (G7).
- `config --get remote.REMOTE.mirror` must be unset or false.
- Otherwise `serve` exits 2. There is no push unless the flag is given.
- **SSH (M6).** The exporter passes `GIT_SSH_COMMAND`, `GIT_SSH` and `GIT_SSH_VARIANT` from the
  daemon's own environment explicitly through `env=`, because `environment()` scrubs every `GIT_*`
  variable. It adds `-c core.sshCommand="ssh -o BatchMode=yes"` only when neither those variables
  nor `config --get core.sshCommand` is set. Otherwise the operator's value stands (`-c` would
  override it).

### 6.2 Behaviour

- A push is attempted after every commit, and in any cycle where the local tip differs from
  `export.json`'s `pushed`.
- The runner calls push with `ok=(0, 1, 128)`. It parses only the porcelain status lines (`=`,
  `*`, `+`, `-`, `!`) and redacts stderr itself (G7).
- **Success** records `pushed = tip`.
- **Rejection** (`!`, `[rejected]`, `non-fast-forward`, `fetch first`) sets state `push_blocked`
  with this guidance: *"the remote's <b> has history this branch lacks. Inspect it
  (`git log <remote>/<b>` after a manual fetch) and fetch it into the local branch only if it is
  this server's own history (a restore that skipped §8 step 3); otherwise point --export-push at a
  different backup repo. The exporter never force-pushes."*
- **Other failures** (network, auth, a 120 s timeout): `push_failed`, with the redacted output.

### 6.3 Backup repository and credentials (016 Q5 ruled)

- Backups go to a **dedicated backup repository**, never the code repository's `origin`. Its
  deploy key is **scoped to that one repository** and has write access to it only.
- systemd `ProtectHome=yes` hides `/home`, so the key sits outside it, owned by `taskmaster`:
  `/etc/taskmaster/backup_ed25519`, mode 0600.
- The host clone's config holds the SSH command:
  `core.sshCommand = ssh -i /etc/taskmaster/backup_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes -o UserKnownHostsFile=/etc/taskmaster/known_hosts`.
  Alternatively it goes in the unit's `Environment=GIT_SSH_COMMAND=…`.
- HTTPS with an embedded token is discouraged.
- The backup repository should be as private as the server: it carries everything teammates write,
  uploaded documents included.
- 016's how-to records all of this. 013 doesn't try to prove that a URL isn't `origin`, because it
  never reads URLs (G7).

## 7. Failure, retry, backoff, growth, status

### 7.1 Failure classes

| Failure | State in `export.json` | Next attempt |
|---|---|---|
| Publisher busy, pinned (`publication_refusal`), or `git_active` | `deferred`; `deferred_since` set on the first deferral | 60 s (60 s acquire after 3 in a row) |
| `hash-object` id mismatch, guard refusal (§4.3), Git error, stale ref lock | `commit_failed`, `failures += 1` | `min(interval, 60·2^(failures-1))` ±10% jitter |
| Push failure or rejection | `push_failed` / `push_blocked`, `push_failures += 1` | `min(3600, 60·2^(n-1))` ±10%; commits continue locally |
| Succeeded but the store owes export (`caught_up` false: pin, stalled or lagging export) | `behind`; failure counters reset; `deferred_since` set if empty; `last_ok` **not** stamped | schedule |
| Success with `caught_up`, including a no-op cycle | counters reset, `deferred_since` cleared, `last_ok` stamped | schedule |

### 7.2 Logging

Logger `taskmaster.serve.export` (001's handlers):

- The first failure is logged at WARNING; while it keeps failing, once an hour; recovery at INFO.
- A deferral that passes the staleness threshold (§7.4) is logged at WARNING, under the same
  once-an-hour rule (I5).
- Every commit is logged at INFO: commit, seq, files, documents, held count, pushed yes or no.
- Messages are redacted per G7.

### 7.3 `export.json`

- Lives at `local/serve/export.json`, owner-only, written atomically with temp-file-then-`os.replace`
  (the pattern at `serve/principals.py:209`, factored there into a shared helper and reused).
- Advisory for scheduling. For guards 5 and 6 it is the lineage and store record; losing it means
  the next cycle re-walks the history (guard 5 without a record), which this store's own history
  passes.
- Fields: `store_id`, `branch`, `remote` (name or null), `commit`, `through`, `docs_seq`,
  `pushed`, `state`, `failures`, `push_failures`, `deferred_since`, `last_ok`, `last_attempt`,
  `next_attempt`, `last_error` (redacted), `held` (`{path: held_since}`, at most 20), `adopted`,
  `caught_up`, and `dropped_documents` (`[{kind, id, section, rule}]`, at most 20, plus a count).

### 7.4 Status (I5)

`taskmaster serve export status --root R` prints `export.json` plus a live `rev-parse` of the branch.
It never opens the store, so it works while the daemon runs. Let
`stale_after = max(2·interval, 3600)` seconds.

| Exit | Meaning |
|---|---|
| 0 | Healthy |
| 1 | Failing: `failures` > 0, or `push_failures` > 0, or `push_blocked`, or `deferred_since` older than `stale_after`, or `last_ok` older than `stale_after`, or any held path older than 24 h |
| 2 | Not configured |

- The `last_ok` rule catches every silent stall: publisher starvation, a managed-Git pin, or any
  stalled projection export. `last_ok` means "the backup matched a store that owed no export, as
  of", and a no-op cycle stamps it only when `caught_up` (§5.1, RR-I1).
- Status reports the held count and the oldest held age, `caught_up`, and the dropped-document
  count. Dropped documents alone don't cause exit 1: they are a deliberate safety skip, reported
  for the operator to act on.

### 7.5 Growth and maintenance (I4)

Spike numbers, 592 files:

- A one-file commit adds about 8.4 KiB of loose objects.
- At most one commit per interval, and only when something changed. At 900 s the worst case is
  96 a day, about 0.8 MiB of loose objects a day.
- Packed: about 1 KiB per commit amortized; about 35 MB in a worst-case year.

Maintenance:

- At most once a day, after a successful commit, the exporter runs
  `git -c core.hooksPath=<empty> maintenance run --task=loose-objects --quiet`. It runs in the
  foreground (no `--auto`, so nothing detaches), bounded to 600 s, outside `publication`.
- The reviewer's probe shows this task packs loose objects and leaves `for-each-ref`, every reflog
  and `.git/worktrees/*` unchanged. Its `prune-packed` step removes only loose objects already in
  a pack, on the next run.
- No `gc`, `reflog expire`, `prune`, `worktree prune` or `pack-refs` runs from the exporter.
  Managed Git's `QUIET_GIT` reasoning (`git.py:697-699`) stands.
- Full gc, reflog expiry and pruning of the host clone are operator housekeeping, documented in
  016's how-to.
- The reflog adds about 150 B per commit.
- History is never rewritten or squashed (G3).
- **Known cost.** Managed Git's drift check walks `log --all --reflog` (`checkouts.py:249`), and
  now includes the export history. The root layout keeps its result correct. In server mode
  managed Git doesn't run for teammates, and the host clone tracks no `.taskmaster/` after 014. An
  exclusion is a follow-up, not part of 013.

## 8. Restore

### 8.1 Rebuilding a lost host (documented in 016's how-to; proven by §10 step 10)

1. Clone the code repository to the new host path as the `taskmaster` user.
2. `git remote add backup <backup-repo-url>`, and configure the deploy key (§6.3).
3. `git fetch backup refs/heads/taskmaster-state:refs/heads/taskmaster-state`. This makes the
   restored host's export a fast-forward of the remote (§6.2).
4. Pick a commit: the tip, or an older one from
   `git log --format='%H %ci %(trailers:key=Taskmaster-Seq,valueonly)' taskmaster-state`.
5. Materialize the projection files byte-exactly. Git for Windows defaults to `core.autocrlf=true`,
   which `archive` would apply (M4).
   - `mkdir .taskmaster`
   - `git -c core.autocrlf=false -c core.eol=lf archive <commit> -- . ':(exclude)external-documents' | tar -x -C .taskmaster`
6. Build the store from the files. Opening the store adopts them, then
   `taskmaster cutover --root <project>` makes it native (`docs/runbooks/native-cutover.md`).
7. `taskmaster serve export restore-documents --root <project> --from <commit>` re-imports the
   stored documents (§4.5).
8. Re-issue credentials: `serve token`, then `add-user` per teammate. Keys are never in the backup
   (G4), so a lost host's keys die with it.
9. `taskmaster serve export run --root <project> --adopt --export-push backup` records the
   adoption of the old store's branch (G10) and makes the first fast-forward commit. Then start
   `serve` with the same `--export-branch` and `--export-push`.

### 8.2 Partial loss

If the host clone survives but `store.db` is corrupt, run steps 4–9 from the local branch.

### 8.3 What a restore does not bring back

Everything that is neither a projection file nor a stored document:

- `domain_events` history (change feeds restart);
- command receipts, claims and sessions;
- `sync_state`;
- 011's per-clone `worktree` paths and `merge:*` records (machine-local). 011's `branch` rows do come back: the
  export holds `clone-fields/branches.json` and `serve export restore-documents` re-submits each row as the
  operator-only `clone.restore` (011 D10), so the merge gate still finds those branches;
- `local/` in general.

A hard-deleted entity's ID can be reallocated after a restore, because `id_counters` re-seed from
the IDs present. Archived entities are files, so they are kept.

Non-store files under `.taskmaster/` (`harvests/`, `lessons/`) aren't exported (Q5 ruled).

## 9. Seams and overlap with parallel lanes

| Lane | Seam | Files both touch |
|---|---|---|
| **014** (`serve init --from`, untrack) | 014 calls `state_export.export_once(owner, settings, push=...)` once the store is in the host clone and **before** untracking `.taskmaster/` on code branches. 014's branch walk excludes the export branch through `state_export.is_export_branch(root, ref)`, which applies guard 5's export-shape test, not the trailer alone (I2). A `--from-state <commit>` wrapper over §8.1 steps 5–7 belongs to 014. `hash-object --stdin-paths` reads ignored paths, so 014's `.gitignore` change doesn't affect the export. | `serve/cli.py` (sub-command table) |
| **012** (content import) | 013 exports `external_documents` (§4.5) under the shared restore contract: the manifest records `{section, path, sha256, source, imported_by, imported_seq}`. 012 owns `document.import(restore=True)`: host-local operator only, keeps `imported_by`, records `source="restore"`. `restore-documents` calls it and refuses until 012 has landed. Merge order: 012 before 013, or 013's restore tests stay skipped until it does. | none (013 reads the table and events; 012 edits `native_routing/documents.py`, `native/documents.py`) |
| **009** (split-brain fence) | `serve export run` and `restore-documents` open the store only through the same serve-ownership entry as the daemon. Until 009 lands that is `ServeCoordinator(...).start()`. | `serve/daemon.py` (start/teardown order), `serve/cli.py` |
| **005** (allowlist; Part B tracked-only member reader) | None at runtime. The exporter calls `git.generation` and `git.probe` in-process. It adds no coordinator IPC method and never uses `Client`. `restore-documents` submits ordinary `document.import` commands from the host, offline, as the local operator. **Dependency (RR-I2):** 005 Part B, which limits member path imports to tracked files through `serve.tracked.read_tracked_file`, isn't in 013's base. Until it merges, a member can import any project file into `external_documents`. 013's source filter (§4.5) works either way and is the backup's own guard, but it is defence in depth, not a substitute. **Merge order: 005 before 013.** The rule applies to the 013 merge, not to its tests, which plant rows by direct SQL. | none |
| **003** (sessions), **007** (read endpoint) | None functional. | `serve/config.py`, `serve/daemon.py`, `serve/cli.py`: textual conflicts only |
| **016** (CI/deploy) | `ExecStart` gains `--export-push backup`. The how-to covers the dedicated backup repo and its scoped deploy key (§6.3), the §8.1 restore steps, operator gc and reflog housekeeping (§7.5), and `serve export status` as a health check. The Linux lane runs the 013 test files. | none in code |
| `coordinator/git.py` | `probe(..., env=None)`, with the key allowlist from §4.4. | `git.py` |
| `native/projection.py` | `watermark(connection)`, a public read-only name for `_through` (RR-I1). No behaviour change. | `native/projection.py` |
| `serve/principals.py` | The atomic JSON write is factored into a shared helper. | `serve/principals.py` |
| `project.py` | `_valid_remote` and `_valid_state_branch` get public names (`valid_remote_name`, `valid_branch_name`); the old names stay as aliases. | `project.py` |

New files: `taskmaster/serve/state_export.py`, `tests/test_serve_state_export.py`,
`tests/test_serve_export_cli.py`.

## 10. Implementation plan (TDD)

Conventions:

- Run from the worktree with
  `C:\Users\gruku\Files\Claude\taskmaster\.venv\Scripts\python -m pytest <files> -q`, in the
  foreground. Never use bare `python`, which picks up the wrong interpreter (B-100).
- Scratch output goes to `D:\scratchbook\taskmaster\ts013\`.
- Real Git, in disposable temp repositories: `native_git_helpers.init_repo`, `install_hook`, and
  the `root`/`request` fixtures from `test_native_service`. Mark the coordinator-backed tests
  `xdist_group('heavy_processes')`.
- Each step writes its tests first, watches them fail, then implements.

1. **`git.probe` extra environment.**
   - `GIT_INDEX_FILE` passed through `env=` reaches Git.
   - `GIT_DIR` set in `os.environ` is still scrubbed.
   - `env={'GIT_DIR': …}` raises.
   - Existing behaviour is unchanged without `env`.
   - *Proving check:* `tests/test_native_git_generation.py`, `tests/test_native_git_managed.py`.
2. **Tree policy (G4), pure.** `tree_entries(rows, docs, parent_tree, mismatched)`:
   - drops `local/`, dot components, `git~1`, every denylisted name, and paths containing `\n` or
     `\r` or starting with `"`;
   - keeps the parent blob for a mismatched path and omits a new mismatched one;
   - places documents at `external-documents/<kind>/<id>/<section>.md`, and drops a document row
     with a bad id or section;
   - never uses a document's `path` as a tree path;
   - **source filter (RR-I2).** A table of recorded and declared paths each skips the row:
     `.env`, `.env.local`, `config/.env`, `.git/config`, `.taskmaster/local/serve/operator.token`,
     `principals.json`, `store.db`, `deploy/server.pem`, `keys/id_ed25519`, `secrets.yaml`,
     `..\outside.md`, `C:/x.md` and `./.ENV`. Each table entry is checked both as the recorded
     `path` and as the declared `docs[section]` path. `docs/specs/x.md` and `docs/plans/y.md` pass.
     A body with a `-----BEGIN OPENSSH PRIVATE KEY-----` line is skipped under a harmless path;
   - reports held, dropped and dropped-document lists.
3. **Plumbing builder (G1, G6).** `build_commit(...)`:
   - with `core.autocrlf=true` set, a CRLF file round-trips byte-exact;
   - the tree is at the root;
   - a staged user file and an unstaged edit survive (the G1 fingerprint);
   - a `.taskmaster/` listed in `.gitignore` still exports;
   - an equal tree produces no commit;
   - **hooks (M7).** `install_hook` plants `post-index-change`, `reference-transaction` and
     `pre-push`, each writing a sentinel. The repository config also sets `core.hooksPath` to a
     second directory of sentinel hooks. After a full commit, push and maintenance cycle, no
     sentinel exists. A control run without the `-c` override creates one, so the test can fail;
   - the fixed author works with no `user.name`;
   - the trailers are present.
4. **Guards and CAS (G2, G10).**
   - **Concurrent checkout (I1).** A concurrent `git_run(kind='checkout', ref='taskmaster-state')`
     started while the exporter holds `publication` either waits or is refused, and HEAD is never
     moved underneath. A checkout done before the cycle makes it refuse (guard 3), case-
     insensitively on Windows.
   - **Forged trailer (I2).** A forged-trailer commit on a non-checked-out `master` is refused
     twice: by name (guard 4), and with `--export-branch work`, a code branch whose tip carries the
     trailer, by shape (guard 5).
   - A `refs/heads/taskmaster-state` that is a symref to `victim` is refused, and `victim` is
     unchanged.
   - With a record: a tip that descends through export-shaped commits is accepted. A tip that
     descends through one non-export commit is refused.
   - Without a record: an all-export chain is accepted and recorded.
   - **Two stores (I3).** Two coordinators, on a main checkout and a linked worktree, both export
     to one branch. After the first commit, the second store is refused ("another store") and the
     branch holds only the first store's commits. With `--adopt`, the second store adopts exactly
     once and the first store is then refused.
   - The CLI default is off on a linked-worktree root.
   - `@{-1}` and `HEAD` are refused as `--export-branch`.
   - A `sha256` repository is refused at start.
   - A planted `<ref>.lock` is reported and left in place.
   - A stray `index-1-1.tmp` is removed, and `other.tmp` and a junction or symlink named
     `index-2-2.tmp` are left alone.
5. **`export_once` with a real `Coordinator`.**
   - The first export commits every generation file plus the documents and manifest.
   - A no-change run takes no lock (spy on `owner.publication`) and stamps `last_ok`.
   - After `epic.update`, there is one new commit, and `Taskmaster-Seq` equals `exported_through`.
   - A hand-edited projection file is held forward with `Taskmaster-Held: 1`, and `held_since` is
     recorded.
   - An archive move removes the old path and adds the new one.
   - A `document.import` adds or changes exactly that blob and its manifest entry, with SHA-256
     equal to `content_hash`.
   - A held `publication`, a `git_pin` or `git_active` makes the cycle `deferred`.
   - **Pinned store (RR-I1).**
     - Set `owner.git_pin` and make a projection-only write (`epic.update`).
     - Every following cycle is `deferred`, never a no-op success, and `last_ok` stays unchanged.
     - With the pin cleared but the exporter paused, so the watermark trails `MAX(seq)`, a cycle
       is `behind` and `last_ok` is still not stamped.
     - With the watermark caught up but a planted `progress.pending.*` key, so `owes_through` is
       true, a cycle is `behind`.
     - Once the export catches up, the next cycle commits the write, stamps `last_ok` and clears
       `deferred_since`.
   - **Document from `.env` (RR-I2).** Two task documents are imported through the real
     `document.import` command, which a path import from the project root already allows today:
     - one from `.env`, holding `TOKEN=abc`;
     - one whose task declares `docs[spec]` as `.taskmaster/local/serve/operator.token`.

     A third row is planted by direct SQL with
     `path='.taskmaster/local/serve/operator.token'`. Assert:
     - none of the three appears in the tree, the manifest or any blob (`git grep -e TOKEN=abc` over
       every export commit finds nothing);
     - each is listed as `dropped document` with its rule, and `export.json.dropped_documents`
       counts 3;
     - a sibling `docs/specs/x.md` document is exported.
   - `stopping` set after the acquire abandons the cycle.
   - A file changed between `generation` and `hash-object` aborts the cycle.
   - The CRLF file's `--no-filters` id is a member of `gen.blobs[rel]` (R2).
   - **G4 end to end (M8).** A projection row `tasks/x.key` is planted by direct SQL in the
     fixture, with a matching disk file. Its path is absent from the tree and listed in the body.
   - **G5 end to end (M8).** `project.yaml` with `team.sync.remote`, `state_branch` and
     `debounce_s` set changes neither the branch, the remote nor the schedule.
6. **Push (G3, G7).** Against a bare remote in a temp directory:
   - A fast-forward push records `pushed`.
   - A rewound local branch is rejected: the state is `push_blocked`, the remote is unchanged, and
     the guidance text is the §6.2 text.
   - A missing remote or `mirror=true` is refused at configuration.
   - **Redaction (M3).** With a remote URL of `https://u:secret@127.0.0.1:9/x.git`, and with a
     failing remote `ssh://git@host.invalid/x.git`, neither `u:secret`, `127.0.0.1:9/x.git` nor
     `host.invalid` appears in `export.json`, in `taskmaster.serve.export` log records (caplog),
     in status output, or in any raised exception's text.
   - `GIT_SSH_COMMAND` in the daemon's environment reaches the push child (a fake command writes a
     sentinel). A repository `core.sshCommand` is not overridden.
   - The §4.4 argv spy over a full commit, push and maintenance cycle passes, and the
     forbidden-argv refusals hold.
7. **Scheduler, backoff, maintenance.** `StateExporter` with an injected clock, runner and
   `export_once`:
   - first run at `min(60, interval)`; the interval; the skip;
   - commit backoff capped at the interval;
   - push backoff capped at 3600 s while commits continue;
   - counters reset;
   - three busy deferrals lead to a 60 s acquire;
   - maintenance at most once a day, in the foreground, outside the lock;
   - after maintenance, `for-each-ref`, every `logs/refs/**` file and `.git/worktrees/*` are
     unchanged (I4);
   - `request_stop` reaches the exporter; teardown joins within 10 s while a fake read phase runs;
   - the log-once-an-hour rule, including the stale-deferral warning.
8. **CLI.** In `tests/test_serve_export_cli.py`:
   - **`status` exit codes.** 0 when healthy, and after a no-op cycle. 1 for each of: failures,
     push failures, `push_blocked`, `deferred_since` older than `stale_after`, `last_ok` older than
     `stale_after`, and a path held for more than 24 h (I5). 2 when not configured. No URL in the
     output.
   - **Pinned store reaches exit 1 (RR-I1).** Drive step 5's pinned scenario under a fake clock
     past `stale_after`. Status exits 1 and names `deferred` (pin) or `behind`. It returns to 0 after
     the caught-up cycle. A store that is idle and caught up stays at 0 indefinitely, because
     no-op cycles keep stamping `last_ok`.
   - Dropped documents are reported in status output without changing the exit code.
   - `run` exits 69 while a `Coordinator` owns the root. When nothing owns it, `run` drains,
     commits, pushes and exits 0.
   - `run --adopt` records `adopted`.
   - `restore-documents` refuses an entry whose blob doesn't match `sha256`, and an entry whose
     path fails the source filter. It submits `restore=True` carrying the original `imported_by`
     and `source`, and it is refused when run against a daemon or with a remote origin.
     It refuses outright on a build without 012's `restore` argument.
   - `serve --help` still doesn't import `backlog_server` (`tests/test_packaging.py`).
9. **Daemon integration.**
   - `ServeConfig` gains `export_interval=0` (off in code), `export_branch` (`taskmaster-state`)
     and `export_push=None`.
   - `validate` checks ranges, names and the default-branch refusal.
   - `--export-push` on a root that isn't a Git top level exits 2.
   - A CLI default-on on a non-Git root, or on a linked worktree, logs one line and doesn't start
     the exporter.
   - The exporter starts after `coordinator.start()`, is signalled by `request_stop`, and is joined
     in `_teardown` before the coordinator stops.
   - The ready line gains `"export": {"branch", "interval", "push"}`.
   - An in-process daemon with `export_interval=60` and a fake clock produces a commit.
   - *Proving check:* `tests/test_serve_daemon.py`, `tests/test_serve_cli.py`,
     `tests/test_serve_config.py`.
10. **Restore round trip (§8.1).**
    - Populate a store: tasks, an archived task, a handover, and two task documents (one imported
      from a file, one uploaded through 012's `content=` path, or by direct `document.import`
      before 012 lands). Export it and push.
    - Into a fresh temp repository:
      1. `remote add`, then fetch the branch from the bare remote;
      2. `git -c core.autocrlf=false -c core.eol=lf archive` into `.taskmaster/`;
      3. adopt and cut over with `test_native_cutover.py`'s helpers;
      4. `restore-documents`;
      5. `serve export run --adopt`.
    - Assert:
      - every restored projection file is **byte-equal** to the source export (M4);
      - the entity listing (`kind`, `id`, title, status) equals the source's;
      - each document's `body` and `content_hash` equal the source's, and
        `backlog_document(provenance=True)` serves them with `origin="restore"`;
      - **`imported_by` survives the restore.** The uploaded document's `imported_by` equals the
        member handle that uploaded it at the source, not `operator`. Its `source` stays `upload`,
        and the file-imported one stays `file`;
      - a second export of the restored store writes a manifest whose `source` and `imported_by`
        equal the first manifest's;
      - a document the source filter dropped is absent after the restore, and the restore output
        says so;
      - `ls-tree -r` of every export commit holds no `local/` path and no denylisted name;
      - the adoption made a fast-forward push.
11. **Linux.** None of these tests is Windows-only. POSIX-only assertions are skip-marked on
    Windows: `0600` on `local/serve/export/` and `export.json`, and `maintenance` not detaching. The
    case-insensitive checked-out test is skip-marked on POSIX. 016's lane runs both files.

**Regression set** (all must pass before review):

- `tests/test_serve_state_export.py`, `tests/test_serve_export_cli.py`;
- `tests/test_serve_daemon.py`, `tests/test_serve_cli.py`, `tests/test_serve_config.py`,
  `tests/test_serve_coordinator.py`;
- `tests/test_native_git_managed.py`, `tests/test_native_git_generation.py`,
  `tests/test_native_git_checkouts.py`, `tests/test_native_git_crash.py`;
- `tests/test_native_cutover.py`;
- `tests/test_native_projection.py`, `tests/test_native_projection_drain.py` (the `watermark` alias);
- `tests/test_project_team.py` (the validator renames);
- `tests/test_packaging.py` (no new third-party import).

## 11. Risks

- **R1. `generation` cost under `publication` on large stores.** It is bounded by the interval, and
  skipped entirely when idle. Step 5 measures it on the fixture.
- **R2. Coupling to `git.generation` internals.** The check relies on `blobs[rel]` being the sorted
  `{exact, LF}` blob ids of the verified read. Step 5 pins this with an assertion.
- **R3. Operator Git configuration.** Global `url.*.insteadOf` or a credential helper are the
  operator's own choice and apply to the push only. G6's `-c` overrides cover hooks, signing and
  prompts.
- **R4. `ENTITY_FILE_SPECS` drift.** A new projection kind directory added later would make guard
  5 call new export commits "not export-shaped". A test asserts that every top directory of the
  current generation is in the shape set, so the drift fails CI instead of the host.

## 12. Decisions (user rulings, 2026-11-01)

All of rev 1's recommendations were accepted:

| Q | Ruling |
|---|---|
| Q1 | The `serve` CLI exports by default: 900 s, local commits only, no push without `--export-push`. `ServeConfig` stays off by default. Rev 2 adds: off by default on a linked worktree (I3). |
| Q2 | A projection file that differs from the store is held forward (last committed blob, `Taskmaster-Held`, warning). Rev 2 adds a status exit 1 after 24 h held (I5). |
| Q3 | Data sits at the tree root, with no `.taskmaster/` prefix. Stored documents sit under `external-documents/`. |
| Q4 | Live "export now" is deferred. `serve export run` covers it offline. |
| Q5 | Non-store files under `.taskmaster/` are excluded. |
| Q6 | No export health for remote callers in 013. |
| Q7 | The branch lives in the host clone, with G2/G10's guards. The bare-repository alternative remains the fallback. |
| 016 Q5 | Backups go to a dedicated backup repository with a repo-scoped deploy key (§6.3). |
| 012 I1 | The backup includes `external_documents` (§4.5), proven by the restore round trip. |

No open questions remain for 013.
