<!-- User intent: the executable, test-first plan for team-sync-004, the pure team resolver
     (native/team_merge.py) that merges one entity's base/ours/theirs shared states under the
     §6.5 rules, so a Sonnet engineer can build it without further context. -->

# team-sync-004: team resolver `native/team_merge.py` (plan, 2026-10-31)

**Task.** `team-sync-004` (claude-tools backlog), lane full. Done when every row of the spec §6.5
table has a passing unit test, including `git merge-file` body merges and archive-vs-edit keeping both.

**Spec.** `docs/specs/2026-10-31-team-collaboration-native-design.md` rev 3.2: §6.5 (the table),
§3 (local-only fields), §5 (assignee lifecycle, general import rule, plan list-merge), §6.1 (shared
state, canonical form), §6.3 (one resolver call per changed entity).

**Worktree.** `C:\Users\gruku\Files\Claude\taskmaster\.worktrees\team-sync-004`, branch
`feature/team-sync-004`, base `1b7a7f4`. Paths below are relative to the worktree root (the
taskmaster package repo; tests live in `tests/`).

## 1. Scope

In scope: one pure module plus its unit tests. Given an entity's kind and id and its base / ours /
theirs **shared states**, return the merged state and the structured outcomes the caller persists.

Not in scope, and how the module stays out of them:

| Out of scope | Owner | Boundary in this module |
|---|---|---|
| Collision detection and renames | team-ids-002 | `base is None` with both sides present raises `IdCollision`; the named-kind "same entity" merge is the caller passing `EMPTY_BASE` (Step 13) |
| Persisting notes and losing bodies | team-sync-005 | `SyncNote` / `BodyConflict` dataclasses (§2) are the contract |
| Applying merged states, lease release, `team_base` | team-sync-007 | `Resolution.action` and `ClaimMoved` say what to do; nothing is written |
| Shared-state extraction and canonical hash | team-sync-002 | Inputs must already be shared state; local-only fields are refused (Step 1) |
| Plan entity and tool | team-plan-001 | Only the list-merge rule for `entries` lives here (Step 11) |
| Quarantine of unparseable theirs | team-sync-007 | The resolver only ever receives parsed states |

No DB, no coordinator, no filesystem other than temp files for `git merge-file`.

## 2. Public API and data shapes

`Document = tuple[dict, str | None]` (fields, body), as `projection_parse.py:23`.

```python
# taskmaster/native/team_merge.py
class IdCollision(ValueError):          # .kind, .id; message names team-ids (§7)

@dataclass(frozen=True)
class LocalIdentity:                     # who "me" is for the general import rule (§5)
    handle: str
    clone_id: str

@dataclass(frozen=True)
class SyncNote:
    kind: str
    id: str
    code: str              # one of NOTE_CODES below
    field: str | None      # "priority", "gates.review", "docs.plan", "assignment", "body", "archived", or None
    side: str              # "ours" | "theirs": the side whose change did not land (archived_with_edits: the side that archived)
    lost_json: str | None  # migrate.encode(losing value); None = the losing side had no value
    kept_json: str | None  # migrate.encode(value that landed); None = absent
    detail: str | None = None   # body_conflict: the BodyConflict.reason

@dataclass(frozen=True)
class BodyConflict:
    kind: str
    id: str
    reason: str            # "conflict" | "git_unavailable" | "timeout" | "git_error"
    base: str | None
    ours: str | None       # the full losing body: what team_conflicts stores
    theirs: str | None     # what landed

@dataclass(frozen=True)
class ClaimMoved:          # assignee moved away from me, or claimed_on away from this clone
    kind: str
    id: str
    from_handle: str | None
    to_handle: str | None
    from_clone: str | None
    to_clone: str | None

@dataclass(frozen=True)
class Resolution:
    kind: str
    id: str
    state: Document | None          # merged shared state; None = the entity is deleted
    action: str                     # relative to ours: "unchanged" | "update" | "insert" | "delete"
    notes: tuple[SyncNote, ...]     # sorted by (field or "", code)
    conflict: BodyConflict | None   # at most one per entity; a body_conflict note refers to it
    claim_moved: ClaimMoved | None

EMPTY_BASE: Document = ({}, None)
NOTE_CODES = ("field_lost", "assignment_lost", "body_conflict", "archived_with_edits", "tombstone_lifted")
BODY_MERGE_TIMEOUT_S = 5

def resolve(kind, ident, base, ours, theirs, *, me=None, merge_targets=None,
            timeout=BODY_MERGE_TIMEOUT_S) -> Resolution: ...

def merge_body(base, ours, theirs, *, timeout=BODY_MERGE_TIMEOUT_S) -> tuple[str | None, str | None]:
    """(merged body, None) when clean; (theirs, reason) when it could not merge cleanly."""
```

Shape rules team-sync-005 and -007 can rely on:

- Every field of every dataclass is a JSON primitive or a `str` of encoded JSON, so
  `dataclasses.asdict` is directly storable. `lost_json`/`kept_json` follow the store's
  convention that SQL NULL is absence and JSON `null` is an explicit null (`native/schema.py:3`),
  encoded with `native/migrate.py:19` `encode`.
- A body conflict produces exactly one `body_conflict` note plus `Resolution.conflict`. The note
  carries no body; team-sync-005 stores the conflict row and links the note to it.
- `ClaimMoved` is not a note. team-sync-007 releases the lease and writes the "who has it now"
  note only when this clone actually holds a lease on that `entity_key` (§5 general import rule);
  the resolver can't see leases, because they are local-only.
- `action`: `insert` = ours absent, state present (new on theirs, or our tombstone lifted);
  `delete` = ours present, state None; `update` = both present and different; `unchanged`
  otherwise. When `state` differs from theirs, the import's own write makes the entity an
  outbound candidate again (`last_seq > pushed_seq`, §6.2), so no extra flag is needed.

Existence inputs: `base=None` means the entity is new (no `team_base` row). With a base, `ours=None`
or `theirs=None` means that side tombstoned it (DB `deleted`, or a path deleted on the branch
without a move, §6.2 step 4).

## 3. What is reused (cite in code comments only where non-obvious)

| Need | Reuse | Where |
|---|---|---|
| Per-key three-way merge, absence as a value | `_three_way_merge_fields(base, ours, theirs)` keeps its **second** argument on overlap, so call it as `(base_t, theirs_t, ours_t)` to get theirs-wins | `taskmaster_v3.py:3933-3956` |
| Token encoding so `False != 0` and missing stays missing | the `tokens()` idea and overlap set in `sync_merge.merge` | `native/sync_merge.py:99-110` |
| Canonical JSON | `encode` | `native/migrate.py:19-20` |
| Body normalisation (one trailing newline is framing) | `normal_body` | `projection_parse.py:64-71` |
| Path id vs declared id | `validate_identity` | `projection_parse.py:162-165` |
| Claim fields (local-only) and terminal statuses | `claims.CLAIM_FIELDS`, `claims.TERMINAL_STATUSES` | `native/claims.py:47,53` |
| Derived backlog sections (local-only) | `DERIVED_BACKLOG_FIELDS` | `native/sync_merge.py:9` |
| Gate records: `task["gates"][gate_name] = record` | `record_gate_doc`, `skip_gate_doc`, `clear_gate_doc` | `native/domain.py:275-340` |
| Merge records: `task["merge_status"][rung] = {merged_at, merge_commit}` | `record_merge_doc` | `native/domain.py:343-352` |
| Derived mirrors to recompute | `compute_gate_state(task)`, `compute_merge_gate_state(task, merge_targets)` | `taskmaster_v3.py:577,617` |
| Timestamp parsing for newest-wins | `_parse_iso8601` | `taskmaster_v3.py:3959` |
| Archive markers: task/epic `status: archived` + `archived: <stamp>` + `archive_reason`; bug/idea `archived: true`; note `archived: true` + `archived_at` | `archive_task_doc`, `_epic_archive`, `bug.archive`, `archive_note_doc` | `native/domain.py:265-272`, `native/workflow.py:1129-1146`, `native/lifecycle.py:205-207`, `taskmaster_v3.py:3675-3680` |
| Bounded subprocess on Windows | `run_bounded` | `bounded_run.py:23` (commit `618b52d`) |
| Git env hygiene (strip `GIT_*`, no prompt) and `CREATE_NO_WINDOW` | copy the 4-line pattern; do not import `coordinator.git` | `coordinator/git.py:64-84` |

The uniform archive signal is a truthy `archived` field: `Transaction.replace` derives
`entity_core.archived` from it (`native/commands.py:215-216`).

**List fields with element identity** (from `native/schema.py:16-29` relation columns plus the
task file fields): IDs `depends_on`, `task_ids`, `related_tasks`, `related_issues`, `adopted_into`,
`promoted_to`, `fixed_in_task`, `duplicate_of`, `task_id`, `bundle`, `area`; paths `anchors`,
`location`; names `components`, `tags`; typed links `links` (identity `(type, target)`); plan
`entries` (identity `id`). Element-wise merge applies only when base/ours/theirs values are all
lists (absent base counts as `[]`); any other shape falls back to the scalar rule.

## 4. Merge algorithm (what `resolve` does, in order)

1. **Guard.** Refuse (`ValueError`) kind `tracker` and any kind outside `SHARED_KINDS`
   (task, epic, phase, area, bug, issue, handover, decision, idea, note, backlog, project, profile,
   plan, alias); refuse a present side holding a local-only field (task: `CLAIM_FIELDS`,
   `worktree`, `branch`; backlog: `DERIVED_BACKLOG_FIELDS`); run `validate_identity` on each present
   side; deep-copy inputs; pass each body through `normal_body`.
2. **Existence.** Handle new / tombstone / collision cases (Steps 10, 12, 13) and return early.
3. **Fields.** Tokenize the three field maps. `baseline = _three_way_merge_fields(base_t, theirs_t, ours_t)`.
   `overlap` = keys where ours ≠ base, theirs ≠ base and ours ≠ theirs (the `sync_merge.py:105-108` rule).
4. **Policy pass**, each policy taking its fields out of the generic handling:
   derived mirrors (`gate_state`, `merge_gate_state`: no notes, recomputed in 7) →
   stamps (`updated_by`+`updated_at` as one pair, `last_referenced` alone: newest wins, no note) →
   assignment group (`assignee`, `assignee_source`, `claimed_on` as one unit) →
   archive (one side newly archived) →
   keyed maps (`gates`, `merge_status`: union; `docs`: key-wise) →
   identity lists and plan `entries` →
   everything else in `overlap`: theirs (already in baseline) plus a `field_lost` note.
   Group policies (stamps, assignment) are decided at group level: "changed" means any member changed.
5. **Body.** `merge_body`; a fallback reason yields the conflict and its note.
6. **Archive note** if one side newly archived and the other side changed any field or the body.
7. **Derived.** For tasks: `gate_state = compute_gate_state(merged)` if `gates` or `gate_state` is in
   any side; `merge_gate_state = compute_merge_gate_state(merged, merge_targets)` only when
   `merge_targets is not None`, else keep the baseline value.
8. **ClaimMoved** from ours vs merged (Step 9), independent of whether the fields overlapped.
9. Decode tokens back with `json.loads`, sort notes, set `action`.

## 5. TDD steps

One test file: `tests/test_native_team_merge.py`. Each step: write the tests, run them and watch
them fail for the stated reason, implement, run again green, commit. Helpers in the test file:

```python
ME = LocalIdentity(handle="gruku", clone_id="c-ours")
def task(**fields): return dict({"id": "t-1", "title": "T", "status": "todo"}, **fields)
def note(field, side, lost, kept, code="field_lost", detail=None):
    return SyncNote("task", "t-1", code, field, side,
                    None if lost is ABSENT else encode(lost), None if kept is ABSENT else encode(kept), detail)
```
(`ABSENT = object()` in the test file; `encode` from `taskmaster.native.migrate`.)

Tests that run real Git carry `@pytest.mark.slow` (`tests/conftest.py:30-32`: "real git/bash
subprocess"); it is not deselected by default. Tests that must not reach Git monkeypatch
`team_merge.run_bounded` with a function that fails the test if called.

### Step 0: skeleton and guards
Create the module (user-intent header, dataclasses, constants, `resolve` handling only the
identical case and the guards).
- `test_identical_sides_are_unchanged`: B = (task(priority="low"), "body"); `resolve("task","t-1",B,B,B)`
  → `state == B`, `action == "unchanged"`, `notes == ()`, `conflict is None`, `claim_moved is None`.
- `test_local_only_fields_are_refused`: parametrize ours fields with `locked_by`, `claim_expires`,
  `claim_expires_for`, `worktree`, `branch` → `pytest.raises(ValueError, match=field)`; also kind
  `backlog` with `bugs: []` → ValueError.
- `test_tracker_and_unknown_kinds_are_refused`: `"tracker"`, `"widget"` → ValueError.
- `test_declared_id_must_match`: theirs `id: "t-2"` for ident `t-1` → ValueError.

### Step 1: §6.5 row "Disjoint fields → both land"
- `test_disjoint_fields_both_land`: base `task(priority="low", tldr="x")`; ours title `"A"` and drops
  `tldr`; theirs `priority="high"` and adds `estimate="M"` → fields
  `{"id":"t-1","title":"A","status":"todo","priority":"high","estimate":"M"}`, `notes == ()`,
  `action == "update"`.
- `test_field_edit_and_body_edit_on_different_sides_both_land`: ours edits a field, theirs edits the
  body only → both land; `run_bounded` spy never called.

### Step 2: §6.5 row "Same scalar field → theirs wins, losing value to a note"
- `test_same_scalar_theirs_wins_with_note`: base low, ours `"high"`, theirs `"critical"` →
  `priority == "critical"`, `notes == (note("priority","ours","high","critical"),)`.
- `test_same_new_value_on_both_sides_has_no_note`.
- `test_false_and_zero_are_different_values`: ours `x=False`, theirs `x=0`, base `x=None` →
  `x == 0`, one note with `lost_json == "false"`, `kept_json == "0"`.
- `test_removed_versus_changed_is_an_overlap`: ours drops `tldr`, theirs changes it → theirs value,
  note `lost_json is None`.
- `test_whole_record_dicts_are_atomic`: `spec_review` dict changed differently on both sides →
  theirs' dict whole (no key mixing), one `field_lost` note on `spec_review`.

### Step 3: §6.5 row "`status` → theirs wins the value; gate and merge records union"
- `test_status_theirs_wins_and_gates_union`: lane `full`; base `status="in-progress"`,
  `gates={"spec-review": PASS}`, `gate_state="plan-review:pending"`; ours `status="in-review"`,
  adds `gates["plan-review"]=PASS`, `gate_state="review-gate:pending"`; theirs `status="blocked"`,
  adds `gates["tests"]={"at":"…","status":"done"}` → `status == "blocked"`; `gates` has all three
  records; `gate_state == "review-gate:pending"`; `notes == (note("status","ours","in-review","blocked"),)`.
- `test_gate_state_is_recomputed_not_merged`: base gates `{}`, `gate_state="spec-review:pending"`;
  ours records `spec-review` PASS (`gate_state="plan-review:pending"`); theirs **skips**
  `plan-review` (`{"skipped":True,"reason":"r","by":"jd","at":"…"}`, its `gate_state` unchanged) →
  `gate_state == "review-gate:pending"` (neither side's value), no notes.
- `test_same_gate_on_both_sides_keeps_theirs_record_and_notes_ours`: both record `plan-review`
  (ours pass, theirs fail) → theirs record; `gate_state == "blocked@plan-review"`;
  `notes == (note("gates.plan-review","ours",OURS_REC,THEIRS_REC),)`.
- `test_gate_cleared_on_one_side_and_changed_on_other_keeps_the_record`: ours clears `spec-review`,
  theirs replaces it → theirs record kept, note `field="gates.spec-review"`, `side="ours"`,
  `lost_json is None`. Evidence is never dropped by a merge.
- `test_merge_records_union_and_merge_gate_state_recomputed`: `merge_targets=[{"label":"develop"},{"label":"master"}]`;
  ours adds rung `develop`, theirs adds rung `master` → both rungs; `merge_gate_state == "master"`; no notes.
- `test_merge_gate_state_kept_without_targets`: same inputs, `merge_targets=None` → baseline value, no note.
- `test_docs_map_merges_key_wise`: ours adds `docs.plan`, theirs adds `docs.review` → both keys; same
  key changed on both → theirs plus note `field="docs.plan"`.

### Step 4: §6.5 row "List fields: element-wise with identity, else theirs with a note"
- `test_depends_on_merges_element_wise`: base `["a","b"]`, ours `["a","b","c"]`, theirs `["b","d"]`
  → `["b","d","c"]` (theirs order, ours additions appended, theirs' removal of `a` applies), no notes.
- `test_removal_on_ours_applies`: base `["a","b"]`, ours `["b"]`, theirs `["a","b","e"]` → `["b","e"]`.
- `test_links_identity_is_type_and_target`: base `[{"type":"references","target":"ISS-1"}]`; ours adds
  `{"type":"blocks","target":"t-9"}`; theirs adds `{"type":"references","target":"ISS-2"}` → three
  links in order theirs then ours' addition.
- `test_anchor_paths_merge_element_wise` (parametrize over `anchors`, `components`, `tags`, `task_ids`).
- `test_list_without_identity_theirs_wins_with_note`: field `checklist` base `["x"]`, ours
  `["x","y"]`, theirs `["x","z"]` → `["x","z"]`, `note("checklist","ours",["x","y"],["x","z"])`.
- `test_identity_field_with_scalar_value_uses_scalar_rule`: `bundle` `"a"` vs `"b"` → theirs, one note.

### Step 5: §6.5 row "Body edited on both sides → `git merge-file -p`"
Implement `merge_body`. Fast paths first: equal sides → that body; one side equal to base → the
other side; only otherwise call Git. Call:
`[git, "merge-file", "-p", "-q", "-L", "ours", "-L", "base", "-L", "theirs", ours_path, base_path, theirs_path]`
on files in a `tempfile.TemporaryDirectory()`, each written as UTF-8 bytes with one trailing `\n`
(`None` → empty file), `cwd` = the temp dir, env = os.environ minus `GIT_*` plus
`GIT_TERMINAL_PROMPT=0`, `creationflags=CREATE_NO_WINDOW` on Windows, through
`run_bounded(..., timeout=timeout)`. Exit 0 → decode stdout UTF-8 and `normal_body` it; 1-127 →
`"conflict"`; anything else → `"git_error"`; `shutil.which("git")` None or `OSError` →
`"git_unavailable"`; `subprocess.TimeoutExpired` → `"timeout"`; a `UnicodeDecodeError` on the output
→ `"git_error"`. Every non-clean outcome takes theirs.
- `test_clean_body_merge_lands_without_note` (slow): base `"one\ntwo\nthree"`, ours `"ONE\ntwo\nthree"`,
  theirs `"one\ntwo\nTHREE"` → body `"ONE\ntwo\nTHREE"`, `notes == ()`, `conflict is None`.
- `test_conflicted_body_takes_theirs_and_preserves_ours` (slow): base `"a\nb\nc"`, ours `"a\nX\nc"`,
  theirs `"a\nY\nc"` → body `"a\nY\nc"`;
  `conflict == BodyConflict("task","t-1","conflict","a\nb\nc","a\nX\nc","a\nY\nc")`;
  `notes == (note("body","ours",ABSENT,ABSENT,code="body_conflict",detail="conflict"),)`.
- `test_body_merge_runs_through_run_bounded` (slow): wrap `team_merge.run_bounded` with a recording
  spy → called once, `args[0][1] == "merge-file"`, `kwargs["timeout"] == 7` when `resolve(..., timeout=7)`.
- `test_git_missing_falls_back_to_conflict`: monkeypatch `team_merge.shutil.which` → None →
  body theirs, `conflict.reason == "git_unavailable"`, note `detail == "git_unavailable"`.
- `test_merge_timeout_falls_back_to_conflict`: `run_bounded` raises `subprocess.TimeoutExpired` → `"timeout"`.
- `test_git_error_exit_falls_back_to_conflict`: `run_bounded` returns `CompletedProcess(args, 255, b"", b"err")` → `"git_error"`.
- `test_clean_merge_keeps_unicode_and_framing` (slow): bodies with `"é"` and no trailing newline;
  result has `"é"` and no trailing newline.

### Step 6: §6.5 row "Archive vs edit → the edit lands and the entity stays archived, with a note"
Archive fields: `archived`, `archive_reason`, `archived_reason`, `archived_at`, plus `status` when
the archiving side's status is `archived`. On overlap in these fields the archiving side wins.
- `test_archive_vs_edit_keeps_both` (parametrize ours-archives / theirs-archives): base
  `task(priority="low")`; archiving side sets `status="archived"`, `archived="2026-10-31T10:00"`,
  `archive_reason="wont-fix"`; editing side sets `priority="high"`, `title="T2"` and edits the body
  → status archived, the archive stamp and reason, priority high, title T2, the edited body;
  `notes == (note("archived", ARCHIVING_SIDE, ABSENT, "2026-10-31T10:00", code="archived_with_edits"),)`.
- `test_archive_beats_a_status_edit_with_note`: theirs sets `status="in-progress"`, ours archives
  → `status == "archived"`; notes are the `archived_with_edits` note (side ours) and
  `note("status","theirs","in-progress","archived")`.
- `test_archive_vs_archive_has_no_note`: both archive with different stamps → theirs' archive fields, `notes == ()`.
- `test_bug_archive_flag_counts_as_archive`: kind `bug`, archiving side sets `archived: True` → same outcome.

### Step 7: §6.5 row "Tombstone vs edit → the edit wins; the tombstone is lifted, with a note"
- `test_theirs_tombstone_vs_our_edit`: base B, ours edited, theirs None → `state == ours`,
  `action == "unchanged"`, `notes == (SyncNote("task","t-1","tombstone_lifted",None,"theirs",None,None),)`.
- `test_our_tombstone_vs_their_edit`: ours None, theirs edited → `state == theirs`, `action == "insert"`,
  tombstone_lifted note with `side="ours"`.
- `test_tombstone_vs_untouched_side_applies`: theirs None, ours == base → `state is None`,
  `action == "delete"`, no notes; ours None, theirs == base → `state is None`, `action == "unchanged"`.
- `test_tombstone_on_both_sides`: → `state is None`, `action == "unchanged"`, no notes.

### Step 8: §6.5 row "`updated_by`/`updated_at` → newest wins, no note"
Only one side changed → that side's pair. Both changed → the pair with the later `_parse_iso8601`
time; tie or unparseable → theirs. Same rule for `last_referenced` alone.
- `test_newest_stamp_pair_wins_without_note`: ours `("gruku","2026-10-31T10:00:00Z")`, theirs
  `("jd","2026-10-31T09:00:00Z")`, both changed → ours pair; `notes == ()`.
- `test_stamp_pair_is_never_mixed`: ours changes only `updated_at` (newer), theirs changes both
  (older) → ours' `updated_at` with ours' `updated_by` (base value), not jd's.
- `test_last_referenced_newest_wins_without_note`.
- `test_unparseable_stamp_prefers_theirs`.

### Step 9: §6.5 row "`assignee`/`claimed_on` → theirs wins; the general import rule releases our lease"
The three fields move as one unit. `claim_moved` is set when `me` is given and ours had
`assignee == me.handle` or `claimed_on == me.clone_id`, and the merged state no longer has the same
value for that field.
- `test_their_claim_wins_and_reports_claim_moved`: base unassigned; ours
  `assignee="gruku", assignee_source="claim", claimed_on="c-ours", status="in-progress"`; theirs the
  same with `jd`/`c-jd` → theirs group; `claim_moved == ClaimMoved("task","t-1","gruku","jd","c-ours","c-jd")`;
  `notes == (note("assignment","ours",OURS_GROUP,THEIRS_GROUP,code="assignment_lost"),)` where a
  group is the encoded dict of the three fields (absent members omitted).
- `test_assignment_group_is_atomic`: ours changes only `assignee_source`, theirs changes only
  `claimed_on` → the whole theirs group, one `assignment_lost` note.
- `test_same_person_other_clone_reports_claim_moved`: theirs `assignee="gruku", claimed_on="c-laptop"`
  → `ClaimMoved(..., "gruku", "gruku", "c-ours", "c-laptop")`.
- `test_teammate_force_pick_over_idle_claim`: base == ours == my claim; theirs jd's claim → no notes,
  `claim_moved` set.
- `test_no_claim_moved_without_me_or_when_not_mine`: `me=None` → None; ours held by `jd` → None.
- `test_bug_assignment_uses_the_same_rule`: kind `bug`.

### Step 10: §6.5 row "New entity on one side → inserted"
- `test_new_on_theirs_is_inserted`: base None, ours None → `state == theirs`, `action == "insert"`, no notes.
- `test_new_on_ours_is_kept`: base None, theirs None → `state == ours`, `action == "unchanged"`.
- `test_all_absent_is_an_error`: all None → ValueError.

### Step 11: §6.5 row "Plan (same owner, two clones) → list-merge (§5)"
Kind `plan`, field `entries` (list of `{"id","added"}`), identity `id`, the Step 4 algorithm: theirs'
order wins, ours-only additions append, removals on either side apply, an entry present in theirs
keeps theirs' dict.
- `test_plan_two_clones_list_merge`: base `[a,b,c]`; ours `[a,c,x]` (removed b, added x); theirs
  `[c,a,b,y]` (reordered, added y) → `[c,a,y,x]` by id, `notes == ()`.
- `test_plan_shared_entry_keeps_theirs_added`: same id with different `added` → theirs' dict.
- `test_plan_without_base_unions`: `EMPTY_BASE` → theirs' entries then ours' extras.

### Step 12: §6.5 row "Same new `(kind, id)` on both sides → §7"
- `test_same_new_id_on_both_sides_raises_id_collision`: base None, both present →
  `IdCollision` with `.kind == "task"`, `.id == "t-1"`, message containing `"team-ids"`.

### Step 13: the named-kind path team-ids-002 will call
- `test_named_kind_merge_with_empty_base`: `resolve("epic","auth",EMPTY_BASE,ours,theirs)` with both
  sides sharing `name`, differing on `order`, and ours alone having `area` → theirs' `order` plus a
  `field_lost` note, ours' `area` lands, `action == "update"`.

### Step 14: determinism and the whole table
- `test_notes_are_sorted_and_stable`: an input producing three notes returns them sorted by
  `(field or "", code)`, and two calls give equal results.
- Run the targeted command (§7) and confirm every row in §6 below maps to a passing test.

Commit after each step or pair of steps (`feat(team-sync): …`), code and its tests together.

## 6. §6.5 row → test map (the done-when check)

| §6.5 row | Tests (Step) |
|---|---|
| Disjoint fields | 1 |
| Same scalar field | 2 |
| `status` + gate/merge records union | 3 |
| List fields | 4 |
| Body edited on both sides (clean, conflicted, fallbacks) | 5 |
| Archive vs edit | 6 |
| Tombstone vs edit | 7 |
| `updated_by`/`updated_at` | 8 |
| `assignee`/`claimed_on` | 9 |
| Plan, two clones | 11 |
| New entity on one side | 10 |
| Same new `(kind, id)` on both sides | 12 (signal), 13 (named-kind path) |

## 7. Files and commands

- Create `taskmaster/native/team_merge.py`.
- Create `tests/test_native_team_merge.py`.
- Modify nothing else. CHANGELOG is written at release.

Targeted run (about 15 s, mostly startup):

```
python -m pytest tests/test_native_team_merge.py -q
```

Neighbours, once at the end, to show the reused code is untouched:

```
python -m pytest tests/test_native_team_merge.py tests/test_native_sync_merge.py tests/test_bounded_run.py tests/test_native_claims.py -q
```

The full suite is slow (see the repo's test-suite-speed notes) and isn't needed for a new,
unimported module; the review gate decides whether to run it.

## 8. Decisions the spec left open

1. **Theirs-wins by argument swap.** `_three_way_merge_fields(base, theirs, ours)` instead of a new
   merge loop. Reason: one merge rule, already tested for absence semantics.
2. **Identity lists are an allowlist** (§3 above), including `tags` and `components`. Reason: the
   spec says "IDs, paths"; names are set-like in the same way, and an unknown list stays on the safe
   theirs-plus-note rule.
3. **Keyed maps.** `gates` and `merge_status` merge per key with union bias (a record changed on one
   side beats a clear on the other; same key on both → theirs' record whole, ours in a note). `docs`
   merges per key with the scalar rule. Every other dict is atomic. Reason: gate evidence must
   survive; record-shaped dicts such as `spec_review` must never mix two records.
4. **`last_referenced` is newest-wins without a note**, like the stamp pair. Reason: `touch()` stamps
   it on nearly every write (`native/domain.py:231`), so the scalar rule would note every concurrent edit.
5. **Newest-wins only when both sides changed**; a tie or unparseable stamp goes to theirs. Reason:
   a one-sided change is unambiguous and immune to clock skew.
6. **Assignment is one unit with an `assignment_lost` note**, and `ClaimMoved` is a separate outcome.
   Reason: mixing `assignee` from one side with `claimed_on` from the other forges a claim, and only
   the caller knows whether a lease exists to release.
7. **Archive vs edit:** the archiving side's archive fields and `status` win on overlap; archive vs
   archive takes theirs silently. Reason: the spec says the entity stays archived; a status edit is
   the only way the edit could undo that.
8. **Collisions raise `IdCollision`**; the named-kind merge is a call with `EMPTY_BASE`. Reason: the
   rename must happen before the merge in the same transaction (§7), so a silent merge here would
   hide a missed rename.
9. **Every Git failure takes the conflict path** (theirs lands, ours kept whole), with the reason
   recorded; timeout 5 s. Reason: nothing is lost, and team.import may run inside the writer slot (§6.3 step 4).
10. **No `coordinator` import**: the Git env pattern is copied from `coordinator/git.py:64-84`.
    Reason: the module is specified as pure and must not pull in coordinator state.
11. **Local-only fields are refused, not stripped**, with the list defined here until team-sync-002's
    `team_state.py` exists, which then owns it and `team_merge` imports it. Reason: a raw DB row
    reaching the resolver is a caller bug that would leak leases into the shared tree.
12. **Plan field name `entries`.** team-plan-001 must use it or change the constant. Reason: the plan
    entity doesn't exist yet and the merge needs a field name.
13. **Long text fields other than the body (`notes`, `tldr`) use the scalar rule.** Reason: the spec
    names only the body for text merge; revisit if notes prove noisy.

## 9. Risks

- **Body merges under the writer mutex.** If team-sync-007 resolves inside the writer slot, each
  conflicting body costs a Git process (bounded by the timeout). Resolve outside the slot where possible.
- **Cross-entity invariants aren't checked here.** A union of `depends_on` can create a cycle across
  two entities, and an archived epic's tasks are resolved one by one. team-sync-007's import validation owns that.
- **Field-name coupling.** `assignee`, `assignee_source`, `claimed_on`, `updated_by`, `updated_at`
  (team-identity-003) and `entries` (team-plan-001) don't exist yet; the tests pin the names.
- **Git `merge.*` user config.** With `-p` and a temp dir outside any repo, only global config
  applies; none of it changes a clean `merge-file` result. The clean-merge test would catch a regression.
