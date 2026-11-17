<!-- User intent: keep the rev-3 team spec's review findings on disk so the spec can be revised
     against them in a later session without the original conversation. -->

# Spec review: team collaboration rev 3, 2026-10-31

Spec: [2026-10-31-team-collaboration-native-design.md](../2026-10-31-team-collaboration-native-design.md) at `2e35c4f`.
Task: `team-sync-012` (claude-tools backlog). Gate `spec-review` = **fail** (critical 1, important 14, codex no).
Reviewer: one fresh-context Opus agent; C1 and I3 spot-checked against code by the orchestrator.

| Gate | Result |
|---|---|
| A, spec sanity | WARN: no risks/open-questions section; no target files/modules (renderer, import command, merge module, coordinator sync thread) |
| B, adversarial | FAIL: 1 Critical, 14 Important, 6 Minor |
| C, Codex | SKIP |
| D, blast radius | WARN: no anchors set; manual overlap with `store-perf-001` (native read path under the coordinator) |

## Critical

**C1. A collision rename leaves the old ID owned by a different entity (§7, §14).** After the
rename, theirs owns `(kind, old_id)`, so the alias fallback never fires. A session told "created
auth-007" keeps writing to `auth-007`, and those writes silently land on the teammate's task.
`linear_queue.target_id` (`native/workflow.py:437`) and `domain_events(kind,id,seq)` are keyed by
the public ID too. Fix options: IDs that can't collide (provisional or reserved blocks), or keep
sequential IDs and add a guard that refuses old-ID writes on the renamed clone; rekey the Linear
queue and history to `entity_key` either way.

## Important

- **I1. The rename touches more than the spec says.** Relations render from `value_json`
  (`native/queries.py:121`); `target_id` binds dangling refs (`native/commands.py:198`,
  `native/migrate.py:95-128`). The rename also has to cover `task.epic`/`phase` columns, plan entries,
  the `replace()` identity-immutability check, `document_search`, projection rows and paths,
  `id_reservations` and `linear_queue`. Rename before inserting theirs. Inventory every table and
  test each one.
- **I2. Merge by `(kind, id)`, not by path.** Archive moves files (`store.py:5980-5995`), epics and
  phases span `backlog.yaml` plus `epics/<id>.md`, and `backlog.yaml` holds many entities. The
  existing import merges per entity via `projection_parse.owned_rows` (`coordinator/sync_prepare.py:142-190`).
  Take ours from DB rows; limit theirs to `git diff-tree base..theirs`.
- **I3. The renderer isn't canonical, so clones flap.** Epic and phase order follows the per-clone
  `entity_key` (`native_routing/projection.py:140-144`); line endings follow local files (`:88-93`);
  archive years come from `datetime.now()` (`commands.py:126-128`). Specify a canonical shared
  render (LF, public_id/order sort, no clock) and compute outbound per entity.
- **I4. Losing a push reply inverts "theirs wins".** If the push lands but the reply is lost,
  your own commit comes back as "theirs" and beats your newer edits. Record `team.pending_commit`
  before pushing; adopt it as base if the remote contains it; pin the base under a local ref.
- **I5. Absence becomes deletion.** Paths the renderer skips (path-guard refusals at
  `projection.py:159-165`, unreachable tasks, render errors) are broadcast as deletes, against
  the import's own rule that absence is not deletion (`sync_prepare.py:150-153`). Delete only on
  a tombstone or a known move; abort the push on any render failure.
- **I6. Joining with an existing backlog contradicts §7.** With an empty base, every shared ID is a
  "collision". Use the code-branch merge-base tree as the base, or replace with a backup.
- **I7. Same person, two machines can both claim.** Leases are local (`native/claims.py:250-266`),
  and assignee = me passes on the second machine. Add a shared `claimed_on: <clone-id>`.
- **I8. Assignee lifecycle is unspecified** on release, back-to-todo, done, and reassign while a
  lease is held. Distinguish a claim-derived assignee from a deliberate one; release the local
  lease whenever an import changes the assignee away from me.
- **I9. A claim-race loser has already started.** Pick hands out the worktree while the claim is
  still `sync: pending`. Wait a bounded time for the push to confirm, and return confirmed or provisional.
- **I10. The shared-tree inventory doesn't match the native kinds.** `harvests/` isn't a native kind;
  `areas/` is missing; `trackers/`, `linear.yaml`, `ideas/IDEAS.md` and `lessons/` are unclassified;
  `profile.yaml` has no DB kind (which breaks R6). Classify every exported path; add a `profile` kind.
- **I11. Named kinds and handles collide with no rule.** Epic, phase and area IDs are caller-chosen
  (`commands.py:41`); handovers are date+tldr; two offline joiners can both mint `users/jd`.
  Define same-entity merge vs suffix-rename; detect handle clashes by profile email.
- **I12. Linear.** `team.import` must enqueue upserts for linked tasks on the Linear-owning clone;
  rekey the queue; decide whether trackers are shared.
- **I13. Untracking hurts other clones and old branches.** A teammate's pull deletes their files
  (drift); pre-untrack branches collide with the untracked files. Define the join/untrack
  sequence and how old branches are handled.
- **I14. Unattended Git.** Covers `commit.gpgSign` prompts, pre-push hooks on every debounce, and
  Windows GCM ignoring `GIT_TERMINAL_PROMPT`. `coordinator/git.py:64-70` strips `GIT_SSH_COMMAND`/`GIT_ASKPASS`.
  Use `--no-gpg-sign`, a stated hook policy, `GCM_INTERACTIVE=never`, SSH `BatchMode=yes`, and pass
  through the auth env vars.

## Minor (batched; not blocking)

- **M1.** Render cost: ~0.84 s per 5k entities; cache blobs per `(entity_key, revision)`; use a
  temporary `GIT_INDEX_FILE` with `write-tree` rather than `mktree`.
- **M2.** `sync_merge.merge` keeps ours on overlap (`taskmaster_v3.py:3936-3953`), so the team
  resolver needs its own rule; three-way merge bodies with `git merge-file` before parking the loser;
  restoring on archive-vs-edit overrides the field merge; plans could list-merge.
- **M3.** One large `team.import` blocks the writer (the existing import caps at `MAX_ROWS = 100`);
  retries can starve.
- **M4.** Tombstones and reservations aren't shared, so deleted IDs can be re-minted;
  `task.worktree`/`branch` are machine hints.
- **M5.** The idle-exit wording contradicts itself; the viewer's 60 s fetch keeps the coordinator
  alive; `updated_at` is exposed to clock skew.
- **M6.** A `team_protocol` bump locks out older clones silently; make bumps explicit.

## Re-review (scoped), rev 3.1 `e16d78f` → fixes in rev 3.2 `dddbdda`

Same reviewer, scoped to the blocking findings. Every finding above was FIXED except C1, which
was PARTIAL. The revision introduced two new Importants. All three were fixed in rev 3.2 with
no further review round (per the review policy). **Final gate: pass, 0 Critical, 0 Important open.**

- **C1 remainder:** writes that *reference* a displaced ID (`depends_on`, links, plan entries,
  `[[id]]` prose) were not guarded, and the guard could be dismissed by an agent. Fix: the guard
  checks every ID-valued argument, and only the user acknowledges it (viewer, or the
  TTY-only CLI).
- **N1:** outbound could push before inbound finished, reverting teammates' changes. Fix:
  outbound runs only when the base commit equals the fetched tip; unparseable remote entities are
  quarantined, so they can't stall the base.
- **N2:** `pushed_seq` could skip writes made during a push. Fix: it advances to the outbound
  snapshot sequence only.
