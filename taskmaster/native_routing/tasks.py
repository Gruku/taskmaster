# User intent: serve the task lifecycle tools an agent uses all day — create, edit,
# pick, complete, archive, gates, merges, spec review and the task reads — from the
# native core (N08), with every answer and refusal worded exactly as the legacy tool.
"""Task-family adapters.

Each command first reproduces the legacy tool's checks, in the legacy order and with
its wording, against one snapshot, so a refusal reads identically and commits
nothing. The native command then re-checks everything inside its own transaction; a
race that makes it refuse surfaces as `Error: <reason>`. Answers are rendered from
the documents the native receipt says were committed, as the legacy renderers render
from the documents the legacy transaction committed.
"""
from __future__ import annotations

from copy import deepcopy

from taskmaster import backlog_server as bs
from taskmaster import dependency_chain
from taskmaster.native import blockers, claims, dependency_graph, domain
from taskmaster.native.workflow import _bugs_found_in
from taskmaster.taskmaster_v3 import (
    VALID_GATE_VERDICTS,
    VALID_GATES,
    VERDICT_GATES,
    dependency_ids,
    entity_kind_of,
    links_grouped_by_type,
    outstanding_required_gates,
    required_gates,
)

from . import reads
from .registry import adapter
from .runtime import error_text

def _run(call, operation, arguments):
    """Execute, turning a core refusal into the tools' `Error: …` sentence."""
    try:
        call.execute(operation, arguments)
    except (ValueError, KeyError) as exc:
        return error_text(exc)
    return None


def _committed(call):
    return reads.committed(call.receipts)


def unchanged_display(call, ident, field, kind="task") -> str:
    """The value a no-op write found stored, from the command's own receipt.

    A no-op only happens when the stored value already equals the normalized
    requested value, so this is the caller's value as the core stored it. An
    entity in neither list (a receipt from before `unchanged` existed) keeps the
    legacy marker rather than guessing from a later read.
    """
    document = reads.unchanged(call.receipts).get((kind, ident))
    if document is None:
        return bs.NOT_PERSISTED
    return bs._format_task_field(document.get(field))


def field_display(call, committed, ident, field, expected, kind="task") -> str:
    """The committed value, or `unchanged (already …)`.

    A native command commits whole or refuses, so an entity absent from its receipts
    was left as it was because the value already matched, not lost. The legacy
    "(not persisted)" marker would misreport that as a failed write.
    """
    if (kind, ident) not in committed:
        return f"unchanged (already `{unchanged_display(call, ident, field, kind)}`)"
    return bs._committed_field_display(committed, ident, field, expected, kind=kind)


def _task_taken(snapshot, task_id) -> bool:
    return snapshot.connection.execute(
        "SELECT 1 FROM entity_core WHERE kind='task' AND public_id=? UNION ALL "
        "SELECT 1 FROM id_reservations WHERE kind='task' AND public_id=? LIMIT 1",
        (task_id, task_id)).fetchone() is not None


def _project_manifest(snapshot):
    from taskmaster.project import ProjectManifest, _dict_to_dataclass, validate_manifest_dict
    entity = reads.get(snapshot, "project", "__project__")
    if entity is None:
        return None
    data = deepcopy(entity["fields"])
    try:
        ok, _errors = validate_manifest_dict(data)
    except Exception:
        return None
    return _dict_to_dataclass(ProjectManifest, data) if ok else None


def _merge_targets(snapshot) -> list[dict]:
    try:
        manifest = _project_manifest(snapshot)
        if manifest is not None:
            return manifest.merge_targets_resolved()
    except Exception:
        pass
    from taskmaster.project import DEFAULT_MERGE_TARGETS
    return [dict(item) for item in DEFAULT_MERGE_TARGETS]


# ── Creation ────────────────────────────────────────────────────────────────


@adapter("backlog_add_task")
def add_task(call, *, title, epic, phase, priority, tldr, notes, next_step, depends_on, bundle, options):
    options = options or {}
    docs = options.get("docs", "") or ""
    sub_repo = options.get("sub_repo", "") or ""
    estimate = options.get("estimate", "") or ""
    anchors = options.get("anchors", "") or ""
    task_id = options.get("task_id", "") or ""
    area = options.get("area", "") or ""
    stage = options.get("stage")
    if isinstance(stage, str):
        stage = stage.strip()
        if not stage:
            stage = None
        else:
            try:
                stage = int(stage)
            except ValueError:
                return f"Error: stage must be an integer, got `{stage}`"
    priority = domain.normalize_priority(priority)
    if priority not in domain.VALID_PRIORITIES:
        return f"Error: invalid priority `{priority}`. Valid: {', '.join(domain.PRIORITY_NAMES)}"
    with call.read() as snapshot:
        if area:
            error = reads.validate_area_ref(snapshot, area)
            if error:
                return error
        epic_entity = reads.get(snapshot, "epic", epic, body=True)
        if epic_entity is None:
            return f"Error: epic `{epic}` not found. Valid epics: {', '.join(e['id'] for e in reads.epics(snapshot))}"
        epic_obj = reads.document(epic_entity)
        if task_id and (reads.find_task(snapshot, task_id) or _task_taken(snapshot, task_id)):
            return f"Error: task ID `{task_id}` already exists"
        dependencies = [d.strip() for d in depends_on.split(",") if d.strip()] if depends_on else []
        for dependency in dependencies:
            if not reads.find_task(snapshot, dependency):
                return f"Error: dependency `{dependency}` not found"
        if not phase:
            return ("Error: `phase` is required — every task must belong to a phase. "
                    "Use `backlog_phase_status()` to see available phases.")
        found_phase = reads.find_phase(snapshot, phase)
        if not found_phase:
            return f"Error: phase `{phase}` not found. Use `backlog_phase_status()` to see available phases."
        anchor_list = [a.strip() for a in anchors.split(",") if a.strip()] if anchors else []
        if bundle:
            if not domain.valid_bundle_slug(bundle):
                return f"Error: invalid bundle slug `{bundle}` (lowercase kebab, 2-41 chars)."
            for member in reads.bundle_members(snapshot, bundle):
                if (member.get("sub_repo") or "") != (sub_repo or ""):
                    return (f"Error: bundle `{bundle}` sub_repo mismatch with member `{member['id']}` "
                            f"(one worktree = one repo).")
    parsed_docs = {}
    if docs:
        for pair in docs.split(";"):
            pair = pair.strip()
            if ":" in pair:
                key, value = pair.split(":", 1)
                if key.strip() in domain.VALID_DOC_KEYS:
                    parsed_docs[key.strip()] = value.strip()
    arguments = {"title": title, "epic": epic, "phase": found_phase["id"], "priority": priority,
                 "tldr": tldr, "notes": notes, "next_step": next_step, "depends_on": dependencies,
                 "bundle": bundle, "docs": parsed_docs, "sub_repo": sub_repo, "estimate": estimate,
                 "anchors": anchor_list, "area": area}
    if stage is not None:
        arguments["stage"] = stage
    if task_id:
        arguments["task_id"] = task_id
    refusal = _run(call, "task.create", arguments)
    if refusal:
        return refusal
    new_id = call.receipts[-1]["affected"][0]["id"]
    budget_warning = ""
    if "max_tasks" in epic_obj:
        with call.read() as snapshot:
            active = sum(1 for t in reads.epic_tasks(snapshot, epic) if t.get("status") not in ("archived", "done"))
        if active > epic_obj["max_tasks"]:
            budget_warning = (f"\n\n**Warning:** Epic `{epic}` now has {active} active tasks "
                              f"(this epic's `max_tasks` cap: {epic_obj['max_tasks']}).")
    document = _committed(call).get(("task", new_id))
    if document is None:
        return call.finish(f"Added `{new_id}` — {bs.NOT_PERSISTED}")
    return call.finish(f"Added `{new_id}` — {document.get('title', '')} ({document.get('priority', '')}) "
                       f"under {epic_obj['name']}" + budget_warning)


# ── Field edits ─────────────────────────────────────────────────────────────


@adapter("backlog_update_task")
def update_task(call, *, task_id, field, value, tldr, next_step):
    if (tldr or next_step) and (field or value):
        return "Error: use either field/value style or keyword style (tldr=/next_step=), not both"
    if tldr or next_step:
        with call.read() as snapshot:
            if not reads.find_task(snapshot, task_id):
                return f"Error: task `{task_id}` not found"
        written = [name for name, given in (("tldr", tldr), ("next_step", next_step)) if given]
        commands = [{"operation": "task.update", "arguments": {"id": task_id, "field": name,
                                                                "value": tldr if name == "tldr" else next_step}}
                    for name in written]
        refusal = _run(call, "batch", {"commands": commands})
        if refusal:
            return refusal
        committed = _committed(call)
        document = committed.get(("task", task_id)) or {}
        if ("task", task_id) not in committed:
            return call.finish(f"No change to `{task_id}`: " + "; ".join(
                f"{name} already `{unchanged_display(call, task_id, name)}`" for name in written))
        return call.finish(f"Updated `{task_id}`: " + "; ".join(
            f"{name} → " + field_display(call, committed, task_id, name, document.get(name, bs._MISSING_FIELD))
            for name in written))
    if not field:
        return "Error: provide either `field`/`value` or keyword args `tldr`/`next_step`"
    if field not in domain.ALLOWED_FIELDS:
        return f"Error: field `{field}` not allowed. Allowed: {', '.join(sorted(domain.ALLOWED_FIELDS))}"
    with call.read() as snapshot:
        found = reads.find_task(snapshot, task_id)
        if not found:
            return f"Error: task `{task_id}` not found"
        task, epic = found
        if field == "depends_on":
            # `+id` / `-id` edit the current list; the command then stores the whole list.
            items = [d.strip() for d in value.split(",") if d.strip()]
            current = bs._dependency_ids(task.get("depends_on")) or []
            try:
                edited = bs._apply_list_edit(current, items)
            except ValueError as exc:
                return f"Error: depends_on: {exc}"
            if edited is not None:
                items = edited
                value = ",".join(edited)
            # Newly named dependencies, however the list was given (see the tool).
            added = [d for d in items if d not in current]
            if added:
                graph = {entity["id"]: list(bs._dependency_ids(entity["fields"].get("depends_on")) or [])
                         for entity in reads.page(snapshot, "task", fields=("id", "depends_on"), include_archived=True)}
                problem = bs._dependency_edit_problem(task_id, added, graph)
                if problem:
                    return f"Error: depends_on: {problem}"
        refusal = _update_refusal(snapshot, task, epic, task_id, field, value)
        if refusal:
            return refusal
        if field == "locked_by":
            return f"Error: {claims.HOLDER_WRITE_REFUSAL}"
        if field == "phase" and value != "" and value.lower() != "none":
            value = reads.find_phase(snapshot, value)["id"]
    refusal = _run(call, "task.update", {"id": task_id, "field": field, "value": value})
    if refusal:
        return refusal
    committed = _committed(call)
    document = committed.get(("task", task_id))
    if document is None:
        return call.finish(f"No change to `{task_id}` field `{field}` — already "
                           f"`{unchanged_display(call, task_id, field)}`")
    expected = document.get(field, bs._MISSING_FIELD)
    return call.finish(f"Updated `{task_id}` field `{field}` → "
                       + bs._committed_field_display(committed, task_id, field, expected))


def _update_refusal(snapshot, task, epic, task_id, field, value):
    if field == "status":
        if value not in domain.VALID_STATUSES:
            return f"Error: invalid status `{value}`. Valid: {', '.join(sorted(domain.VALID_STATUSES))}"
        current = task.get("status", "todo")
        if value == "in-review" and value != current and not (task.get("human_action") or "").strip():
            return (f"Error: `in-review` means blocked on a human-only action; set human_action first: "
                    f"backlog_update_task('{task_id}', 'human_action', '<what the human must do>')")
        refusal = domain.illegal_transition_message(task, value)
        if refusal:
            return f"Error: `{task_id}`: {refusal}."
        if task.get("lane") and value != current and value == "done":
            block = domain.completion_block_reason(task)
            if block:
                return block
    elif field == "priority":
        normalized = domain.normalize_priority(value)
        if normalized not in domain.VALID_PRIORITIES:
            return f"Error: invalid priority `{normalized}`. Valid: {', '.join(domain.PRIORITY_NAMES)}"
    elif field == "docs":
        if ":" not in value:
            return (f"Error: docs value must be `key:path` format (e.g., `plan:docs/plans/foo.md`). "
                    f"Valid keys: {', '.join(sorted(domain.VALID_DOC_KEYS))}")
        key = value.split(":", 1)[0].strip()
        if key not in domain.VALID_DOC_KEYS:
            return f"Error: invalid docs key `{key}`. Valid: {', '.join(sorted(domain.VALID_DOC_KEYS))}"
    elif field == "depends_on":
        for dependency in [d.strip() for d in value.split(",") if d.strip()]:
            if not reads.find_task(snapshot, dependency):
                return f"Error: dependency `{dependency}` not found"
    elif field == "stage":
        try:
            int(value)
        except ValueError:
            return f"Error: stage must be an integer, got `{value}`"
    elif field == "phase":
        if value != "" and value.lower() != "none" and not reads.find_phase(snapshot, value):
            return f"Error: phase `{value}` not found"
    elif field == "blast_radius_depth":
        if value not in ("", "shallow", "deep") and value.lower() != "none":
            return f"Error: `blast_radius_depth` must be 'shallow', 'deep', or '' to clear. Got: `{value}`"
    elif field == "tldr":
        if not value:
            return "Error: tldr cannot be cleared — provide a non-empty value or use autogen"
    elif field == "lane":
        from taskmaster.taskmaster_v3 import VALID_LANES
        if value not in VALID_LANES:
            return f"Error: invalid lane `{value}`. Valid: {', '.join(VALID_LANES)}"
    elif field == "component":
        if value != "" and value.lower() != "none":
            components = epic.get("components") or {}
            if value not in components:
                declared = ", ".join(sorted(components)) or "(none declared)"
                return (f"Error: component `{value}` not declared on epic `{epic['id']}`. "
                        f"Declared: {declared}. Add it via backlog_update_epic(<epic>, 'components', ...).")
    elif field == "design_change":
        if value.strip().lower() in ("true", "1", "yes") and epic.get("design_status", "exploring") == "locked":
            return (f"Error: epic `{epic['id']}` design is locked — cannot flag a "
                    f"design-change task. To reopen, set the epic to revising "
                    f"(backlog_update_epic('{epic['id']}', 'design_status', 'revising')) "
                    f"and record the reason as a decision (taskmaster:decision).")
    elif field == "bundle":
        if not domain.valid_bundle_slug(value):
            return f"Error: invalid bundle slug `{value}` (lowercase kebab, 2-41 chars)."
        if value:
            for member in reads.bundle_members(snapshot, value):
                if member["id"] != task_id and (member.get("sub_repo") or "") != (task.get("sub_repo", "") or ""):
                    return (f"Error: bundle `{value}` sub_repo mismatch with member `{member['id']}` "
                            f"(one worktree = one repo).")
    elif field == "area":
        if value != "" and value.lower() != "none":
            return reads.validate_area_ref(snapshot, value)
    return None


def _dependency_statuses(snapshot, task) -> dict:
    """`bs._dependency_statuses`: the declared ids that resolve, with their status.

    An id left out is one the resolver reports as unresolved, so this never
    supplies a status of its own for it.
    """
    declared = blockers.declared_dependencies(task)
    if isinstance(declared, blockers.Unknown):
        return {}
    statuses = {}
    for ident in declared:
        found = reads.find_task(snapshot, ident)
        if found:
            statuses[ident] = found[0].get("status", "todo")
    return statuses


# ── Claims ──────────────────────────────────────────────────────────────────


@adapter("backlog_pick_task")
def pick_task(call, *, task_id, force, ttl_seconds):
    session = bs.SESSION_ID
    try:
        ttl = claims.ttl_seconds(ttl_seconds)
    except ValueError as exc:
        return error_text(exc)
    with call.read() as snapshot:
        found = reads.find_task(snapshot, task_id)
        if not found:
            return f"Error: task `{task_id}` not found"
        task, epic = found
        epic["tasks"] = reads.epic_tasks(snapshot, epic["id"])
        status = task.get("status", "todo")
        slug = task.get("bundle")
        members = reads.bundle_members(snapshot, slug) if slug else []
        unmet = blockers.unmet_dependencies(task, _dependency_statuses(snapshot, task))
    if slug:
        return _bundle_pick(call, task, epic, slug, members, session=session, force=force, ttl=ttl)
    locked_by = task.get("locked_by")
    context_text = bs._task_context({}, task, epic)
    instruction = bs._build_worktree_instruction(task_id, task.get("sub_repo", ""), task.get("branch", ""),
                                                 task.get("worktree", ""))
    if status == "in-progress":
        if locked_by and locked_by != session and not force:
            with call.read() as snapshot:
                state = claims.read(task, task_id=task_id, session=session, connection=snapshot.connection)
            return claims.lock_refusal(task_id, state)
        bs._set_session_task(task, epic)
        if locked_by == session:
            return call.finish(f"Already in progress: `{task_id}` — {task['title']}\n\n" + context_text + instruction)
        refusal = _run(call, "task.pick", {"id": task_id, "session": session,
                                          "force": bool(force), "ttl_seconds": ttl})
        if refusal:
            return refusal
        title = bs._committed_task_field(_committed(call), task_id, "title") or bs.NOT_PERSISTED
        return call.finish(f"Already in progress: `{task_id}` — {title}\n\n" + context_text + instruction)
    if status not in ("todo", "in-review"):
        return f"Error: task `{task_id}` is `{status}`, expected one of: todo, in-progress, in-review"
    if claims.foreign_holder(task, session) and not force:
        with call.read() as snapshot:
            state = claims.read(task, task_id=task_id, session=session, connection=snapshot.connection)
        return claims.lock_refusal(task_id, state, status)
    warning = ""
    if unmet:
        warning = (f"\n\n⚠️ **Unmet dependencies:** {', '.join(f'`{d}`' for d in unmet)} not yet done. "
                   f"Picking anyway (explicit override) — `backlog_next_available` treats this task as blocked.")
    refusal = _run(call, "task.pick", {"id": task_id, "session": session,
                                      "force": bool(force), "ttl_seconds": ttl})
    if refusal:
        return refusal
    task["status"] = "in-progress"
    bs._set_session_task(task, epic)
    document = _committed(call).get(("task", task_id)) or {}
    if document.get("status") != "in-progress":
        head = f"Picked `{task_id}` — {bs.NOT_PERSISTED}"
    else:
        head = f"Picked `{task_id}` — {document.get('title', '')} (locked to this session)"
    return call.finish(head + warning + "\n\n" + context_text + instruction)


def _bundle_pick(call, task, epic, slug, members, *, session, force, ttl):
    sub_repos = {(m.get("sub_repo") or "") for m in members}
    if len(sub_repos) > 1:
        return f"Error: bundle `{slug}` spans multiple sub_repos {sub_repos}; cannot pick."
    sub_repo = next(iter(sub_repos))
    branch = f"feature/{slug}"
    worktree = f"{sub_repo}/.worktrees/{slug}" if sub_repo else f".worktrees/{slug}"
    lane = domain.strictest_lane([m.get("lane") for m in members])
    for member in members:
        if claims.foreign_holder(member, session) and not force:
            return (f"Error: `{member['id']}` is a member of bundle `{slug}` "
                    f"locked by another session ({member['locked_by']}). Use force=True to steal.")
    bound = all(m.get("status") == "in-progress" and m.get("locked_by") == session for m in members)
    if not bound:
        refusal = _run(call, "task.pick", {"id": task["id"], "session": session,
                                           "force": bool(force), "ttl_seconds": ttl})
        if refusal:
            return refusal
    bs._set_session_task(task, epic)
    bs._set_session_bundle({"slug": slug, "sub_repo": sub_repo, "branch": branch, "worktree": worktree,
                            "members": [m["id"] for m in members], "lane": lane})
    instruction = bs._build_worktree_instruction(task["id"], sub_repo, branch, "", _path_key=slug)
    return call.finish(f"Picking bundle `{slug}`: {', '.join(m['id'] for m in members)}.\n"
                       f"Execution lane: {lane}.\n{instruction}")


# ── Completion and archive ──────────────────────────────────────────────────


@adapter("backlog_complete_task")
def complete_task(call, *, task_id, session_title, done, decisions, issues, tasks_touched, target_status,
                  human_action, auto_summary, patchnote, release):
    if target_status not in ("done", "in-review"):
        return f"Error: target_status must be 'done' or 'in-review', got '{target_status}'"
    with call.read() as snapshot:
        found = reads.find_task(snapshot, task_id)
        if not found:
            return f"Error: task `{task_id}` not found"
        task, epic = found
        epic_tasks = reads.epic_tasks(snapshot, epic["id"])
        status = task.get("status", "todo")
        if status not in ("in-progress", "in-review", "blocked"):
            return f"Error: task `{task_id}` is `{status}`, expected one of: in-progress, in-review, blocked"
        if target_status == "in-review":
            human_action = human_action.strip() or (task.get("human_action") or "").strip()
            if not human_action:
                return ("Error: target_status='in-review' requires human_action — the human-only "
                        "step that blocks this task (e.g. 'add OPENAI_API_KEY to .env'). "
                        "If nothing blocks it, target 'done'.")
        open_bugs, _fixed = _bugs_found_in(snapshot.connection, task_id)
        if open_bugs:
            return (f"Cannot complete {task_id} — {len(open_bugs)} open bug(s) linked via found_in: "
                    f"{', '.join(open_bugs)}.\nResolve each (fix/adopt/shelve/promote) before closing the task.")
        if target_status == "done":
            block = domain.completion_block_reason(task)
            if block:
                return block
    # The session paragraph commits with the transition, as a row the drain moves
    # into PROGRESS.md exactly once (N11 S7-S9); the legacy tool queues the same text.
    changelog, changelog_msg = "", ""
    if auto_summary:
        changelog = bs._changelog_entry(session_title, done, decisions, issues, tasks_touched, auto=True,
                                        auto_stats=done)
        changelog_msg = bs.CHANGELOG_AUTO_LOGGED
    elif session_title or done:
        changelog = bs._changelog_entry(session_title, done, decisions, issues, tasks_touched)
        changelog_msg = bs.CHANGELOG_LOGGED
    refusal = _run(call, "task.complete", {"id": task_id, "target_status": target_status,
                                            "human_action": human_action, "patchnote": patchnote,
                                            "release": release, "changelog": changelog})
    if refusal:
        return refusal
    if target_status == "done":
        bs._clear_session_task(task_id)
        session_bundle = bs._get_session_bundle()
        slug = task.get("bundle", "")
        if session_bundle and slug and session_bundle.get("slug") == slug:
            with call.read() as snapshot:
                if not [m for m in reads.bundle_members(snapshot, slug) if m.get("status") not in ("done",)]:
                    bs._clear_session_bundle()
    next_todo = [t for t in epic_tasks if t.get("status") == "todo"]
    next_todo.sort(key=lambda t: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(t.get("priority", "medium"), 9))
    suggestion = ""
    if next_todo:
        n = next_todo[0]
        suggestion = f"\n\n**Next in {epic['name']}:** `{n['id']}` — {n['title']} ({n.get('priority', 'medium')})"
    label = "Completed" if target_status == "done" else "Moved to in-review"
    document = _committed(call).get(("task", task_id)) or {}
    if document.get("status") != target_status:
        return call.finish(f"{label} `{task_id}` — {bs.NOT_PERSISTED}" + changelog_msg + suggestion)
    return call.finish(f"{label} `{task_id}` — {document.get('title', '')}" + changelog_msg + suggestion)


@adapter("backlog_archive_task")
def archive_task(call, *, task_id, reason):
    if reason not in domain.VALID_ARCHIVE_REASONS:
        return f"Error: invalid reason `{reason}`. Valid: {', '.join(sorted(domain.VALID_ARCHIVE_REASONS))}"
    with call.read() as snapshot:
        found = reads.find_task(snapshot, task_id)
    if not found:
        return f"Error: task `{task_id}` not found"
    task, _epic = found
    status = task.get("status", "todo")
    if status not in ("done", "blocked", "todo"):
        return f"Error: task `{task_id}` is `{status}`, only `done`, `blocked`, or `todo` tasks can be archived"
    if status == "todo" and reason == "done":
        return ("Error: cannot archive a `todo` task with reason `done`. "
                "Use one of: deprecated, duplicate, wont-fix, superseded")
    refusal = _run(call, "task.archive", {"id": task_id, "reason": reason})
    if refusal:
        return refusal
    return call.finish(f"Archived `{task_id}` — {task['title']} (reason: {reason})")


# ── Gates, merges and spec review ───────────────────────────────────────────


def _gate_task(call, task_id):
    with call.read() as snapshot:
        return reads.find_task(snapshot, task_id)


@adapter("backlog_record_gate")
def record_gate(call, *, task_id, gate, verdict, status, commit_sha, spec_path, codex_used,
                critical_count, important_count):
    if gate not in VALID_GATES:
        return f"Error: invalid gate `{gate}`. Valid: {', '.join(VALID_GATES)}"
    is_verdict = gate in VERDICT_GATES
    if is_verdict and verdict not in VALID_GATE_VERDICTS:
        return f"Error: gate `{gate}` requires verdict in {', '.join(VALID_GATE_VERDICTS)}, got `{verdict or '(none)'}`"
    if not is_verdict and status != "done":
        return f"Error: status gate `{gate}` requires status=\"done\", got `{status or '(none)'}`"
    found = _gate_task(call, task_id)
    if not found:
        return f"Error: task `{task_id}` not found"
    try:
        domain.record_gate_doc(found[0], gate=gate, verdict=verdict, status=status)
    except ValueError as exc:
        return f"Error: {exc}"
    refusal = _run(call, "task.gate", {"id": task_id, "gate": gate, "verdict": verdict, "status": status,
                                        "commit_sha": commit_sha, "spec_path": spec_path,
                                        "codex_used": bool(codex_used), "critical_count": int(critical_count),
                                        "important_count": int(important_count)})
    if refusal:
        return refusal
    return call.finish(_gate_answer(_committed(call), task_id, gate, verdict if is_verdict else "done", is_verdict))


def _gate_answer(committed, task_id, gate, outcome, is_verdict):
    document = committed.get(("task", task_id)) or {}
    record = (document.get("gates") or {}).get(gate) or {}
    stored = record.get("verdict") if is_verdict else record.get("status")
    if stored != outcome:
        return f"Recorded gate `{gate}` for `{task_id}` — {bs.NOT_PERSISTED}"
    state = bs._committed_task_field(committed, task_id, "gate_state")
    return f"Recorded gate `{gate}` = {stored} for `{task_id}` (state: {state or 'laneless'})"


@adapter("backlog_skip_gate")
def skip_gate(call, *, task_id, gate, reason, by):
    if gate not in VALID_GATES:
        return f"Error: invalid gate `{gate}`. Valid: {', '.join(VALID_GATES)}"
    if not (reason or "").strip():
        return "Error: skip_gate requires a non-empty reason (this is the audit trail)."
    if not _gate_task(call, task_id):
        return f"Error: task `{task_id}` not found"
    refusal = _run(call, "task.gate_skip", {"id": task_id, "gate": gate, "reason": reason, "by": by})
    if refusal:
        return refusal
    document = _committed(call).get(("task", task_id)) or {}
    record = (document.get("gates") or {}).get(gate) or {}
    if not record.get("skipped"):
        return call.finish(f"⚠ Skipped gate `{gate}` for `{task_id}` — reason: {bs.NOT_PERSISTED}")
    return call.finish(f"⚠ Skipped gate `{gate}` for `{task_id}` — "
                       f"reason: {record.get('reason', '')} (by {record.get('by', '')})")


@adapter("backlog_clear_gate")
def clear_gate(call, *, task_id, gate):
    if gate not in VALID_GATES:
        return f"Error: invalid gate `{gate}`. Valid: {', '.join(VALID_GATES)}"
    found = _gate_task(call, task_id)
    if not found:
        return f"Error: task `{task_id}` not found"
    if gate not in (found[0].get("gates") or {}):
        return f"`{task_id}` had no `{gate}` gate record"
    refusal = _run(call, "task.gate_clear", {"id": task_id, "gate": gate})
    if refusal:
        return refusal
    return call.finish(_clear_answer(_committed(call), task_id, gate))


def _clear_answer(committed, task_id, gate):
    document = committed.get(("task", task_id))
    if document is None or gate in (document.get("gates") or {}):
        return f"Cleared gate `{gate}` on `{task_id}` — {bs.NOT_PERSISTED}"
    return f"Cleared gate `{gate}` on `{task_id}`"


@adapter("backlog_record_merge")
def record_merge(call, *, task_id, rung, sha, merged_at):
    if not (rung or "").strip():
        return "Error: rung is required"
    if not (sha or "").strip():
        return "Error: sha is required"
    with call.read() as snapshot:
        found = reads.find_task(snapshot, task_id)
        targets = _merge_targets(snapshot)
    if not found:
        return f"Error: task `{task_id}` not found"
    refusal = _run(call, "task.merge", {"id": task_id, "rung": rung, "sha": sha, "merged_at": merged_at,
                                         "merge_targets": targets})
    if refusal:
        return refusal
    committed = _committed(call)
    document = committed.get(("task", task_id)) or {}
    recorded = ((document.get("merge_status") or {}).get(rung) or {}).get("merge_commit", "")
    ladder = bs._committed_task_field(committed, task_id, "merge_gate_state")
    shown = str(recorded)[:7] if recorded else bs.NOT_PERSISTED
    return call.finish(f"Recorded merge for rung `{rung}` on `{task_id}` (sha={shown}, ladder: {ladder or 'none'})")


@adapter("backlog_set_spec_review")
def set_spec_review(call, *, task_id, verdict, spec_path, codex_used, critical_count, important_count):
    if verdict not in bs.VALID_SPEC_REVIEW_VERDICTS:
        return f"Error: invalid verdict `{verdict}`. Valid: {', '.join(sorted(bs.VALID_SPEC_REVIEW_VERDICTS))}"
    found = _gate_task(call, task_id)
    if not found:
        return f"Error: task `{task_id}` not found"
    try:
        domain.record_gate_doc(found[0], gate="spec-review", verdict=verdict)
    except ValueError as exc:
        return f"Error: {exc}"
    refusal = _run(call, "task.spec_review", {"id": task_id, "verdict": verdict, "spec_path": spec_path,
                                               "codex_used": bool(codex_used), "critical_count": int(critical_count),
                                               "important_count": int(important_count)})
    if refusal:
        return refusal
    return call.finish(f"Recorded spec-review for `{task_id}`: {verdict} "
                       f"(codex={codex_used}, critical={critical_count}, important={important_count})")


@adapter("backlog_clear_spec_review")
def clear_spec_review(call, *, task_id):
    found = _gate_task(call, task_id)
    if not found:
        return f"Error: task `{task_id}` not found"
    had_gate = "spec-review" in (found[0].get("gates") or {})
    refusal = _run(call, "task.spec_review_clear", {"id": task_id})
    if refusal:
        return refusal
    if not had_gate:
        return call.finish(f"`{task_id}` had no `spec-review` gate record")
    return call.finish(_clear_answer(_committed(call), task_id, "spec-review"))


# ── Reads ───────────────────────────────────────────────────────────────────


@adapter("backlog_task_pipeline")
def task_pipeline(call, *, task_id):
    found = _gate_task(call, task_id)
    if not found:
        return f"Error: task `{task_id}` not found"
    task = found[0]
    lane = task.get("lane")
    if not lane:
        return f"`{task_id}` is laneless (pre-protocol) — no pipeline enforced."
    gates = task.get("gates") or {}
    lines = [f"## Pipeline `{task_id}` — lane: **{lane}**", f"gate_state: `{task.get('gate_state') or '(none)'}`", ""]
    for gate in required_gates(lane):
        record = gates.get(gate)
        if not record:
            mark = "○ pending"
        elif record.get("skipped"):
            mark = f"⚠ skipped — {record.get('reason', '')}"
        elif record.get("verdict"):
            mark = f"{record['verdict']}"
        else:
            mark = record.get("status", "?")
        lines.append(f"- `{gate}`: {mark}")
    outstanding = outstanding_required_gates(task)
    lines.append("")
    lines.append("**Outstanding:** " + (", ".join(outstanding) if outstanding else "none — ready for done ✓"))
    return "\n".join(lines)


def _tldr_index(snapshot, ids) -> dict:
    """`build_tldr_index` for exactly the ids being expanded."""
    index = {}
    for ident in ids:
        for kind, archived_ok in (("task", True), ("issue", False), ("handover", False), ("idea", True)):
            entity = reads.get(snapshot, kind, ident)
            if entity is None or (entity["archived"] and not archived_ok):
                continue
            if kind == "task" and reads.epic_of(snapshot, entity["fields"]) is None and entity["archived"]:
                continue
            if entity["fields"].get("tldr"):
                index[ident] = entity["fields"]["tldr"]
    return index


def _links_block(snapshot, lines, task, *, expand_links, peers):
    grouped = links_grouped_by_type(task, "task")
    if not grouped:
        return
    lines.append("\n**links:**")
    for link_type in sorted(grouped):
        targets = grouped[link_type]
        if expand_links:
            pills = []
            for target in targets:
                kind = entity_kind_of(target)
                peer = reads.get(snapshot, kind, target) if kind and peers else None
                tldr = (peer["fields"].get("tldr", "") if peer else "") or ""
                pills.append(f"{target} ({tldr})" if tldr else target)
            lines.append(f"- {link_type}: [{', '.join(pills)}]")
        else:
            lines.append(f"- {link_type}: [{', '.join(targets)}]")


@adapter("backlog_get_task")
def get_task(call, *, task_id, verbose, sections, expand_links, provenance):
    from taskmaster.taskmaster_v3 import expand_link_ids, render_sections, slim_entity
    from . import documents
    backlog = bs._backlog_path()
    with call.read() as snapshot:
        found = reads.find_task(snapshot, task_id)
        if not found:
            return f"Error: task `{task_id}` not found"
        task, epic = found
        if sections is not None and not sections:
            return ("Error: sections=[] requested no sections; pass sections=None for the slim view "
                    "or name at least one section")
        if sections:
            try:
                content, facts = documents.sections_with_provenance(
                    snapshot, "task", task["id"], sections, documents.project_root())
            except ValueError as exc:
                return f"Error: {exc}"
            return render_sections(f"## `{task['id']}` — {task['title']}", content,
                                   facts if provenance else None)
        if not verbose:
            # `Snapshot.open_handovers` is the one reader of that question; the
            # slim view names every one, so it asks for the whole list, not a page.
            handovers = ([row["id"] for row in snapshot.open_handovers(task_id, limit=-1)[0]]
                         if backlog.exists() else [])
            slim = slim_entity(task, kind="task", open_handovers=handovers or None)
            if expand_links:
                for link_field in ("depends_on", "related_issues"):
                    if link_field in slim:
                        ids = slim[link_field]
                        if isinstance(ids, str):
                            ids = [x.strip() for x in ids.split(",") if x.strip()]
                        slim[link_field] = expand_link_ids(ids, _tldr_index(snapshot, ids))
            lines = [f"## `{slim.pop('id')}` — {slim.pop('title', task.get('title', ''))}\n"]
            for key, value in slim.items():
                lines.append(f"**{key}:** {value}")
            _links_block(snapshot, lines, task, expand_links=expand_links, peers=backlog.exists())
            return "\n".join(lines)
        return _verbose_task(snapshot, task, epic, task_id, expand_links=expand_links)


def _verbose_task(snapshot, task, epic, task_id, *, expand_links):
    from taskmaster.taskmaster_v3 import expand_link_ids
    lines = [f"## `{task['id']}` — {task['title']}\n"]
    fields = [
        ("Status", task.get("status", "todo")), ("Priority", task.get("priority", "medium")),
        ("Epic", f"{epic['name']} ({epic['id']})"), ("Bundle", task.get("bundle", "—")),
        ("Stage", str(task["stage"]) if task.get("stage") is not None else "—"),
        ("Estimate", task.get("estimate", "—")), ("Phase", task.get("phase", "—")),
        ("Anchors", ", ".join(task["anchors"]) if task.get("anchors") else "—"),
        ("Sub-repo", task.get("sub_repo", "—")), ("Created", str(task.get("created", "—"))),
        ("Started", str(task.get("started", "—"))), ("Completed", str(task.get("completed", "—"))),
        ("Branch", task.get("branch", "—")), ("Blockers", task.get("blockers", "—")),
        ("Waiting on human", task.get("human_action", "")), ("Locked by", task.get("locked_by", "—")),
        ("Review instructions", task.get("review_instructions", "—")), ("Notes", task.get("notes", "—")),
    ]
    for label, value in fields:
        if value is not None and str(value) not in ("—", "None", ""):
            lines.append(f"**{label}:** {value}")
    depends_on = dependency_ids(task.get("depends_on"))
    if depends_on is None:
        lines.append(bs._unreadable_depends_on_heading(task.get("depends_on")))
        depends_on = []
    if depends_on:
        lines.append("\n**Depends on:**")
        if expand_links:
            for pill in expand_link_ids(depends_on, _tldr_index(snapshot, depends_on)):
                tldr = f" — {pill['tldr']}" if pill.get("tldr") else ""
                lines.append(f"- `{pill['id']}`{tldr}")
        else:
            for dependency in depends_on:
                found = reads.find_task(snapshot, dependency)
                if found:
                    lines.append(f"- `{dependency}` — {found[0]['title']} ({found[0].get('status', 'todo')})")
                else:
                    lines.append(f"- `{dependency}` — NOT FOUND")
    if task.get("docs") and isinstance(task.get("docs"), dict):
        lines.append("\n**Docs:**")
        for key, path in task["docs"].items():
            lines.append(f"- **{key}:** `{path}`")
    if epic.get("docs") and isinstance(epic.get("docs"), dict):
        lines.append("\n**Epic docs:**")
        for key, path in epic["docs"].items():
            lines.append(f"- **{key}:** `{path}`")
    review = task.get("spec_review")
    if review and isinstance(review, dict):
        lines.append(
            f"\n**Spec review:** {review.get('verdict', '?')} ({review.get('timestamp', '?')}) — "
            f"codex: {'yes' if review.get('codex_used') else 'no'}, critical: {review.get('critical_count', 0)}, "
            f"important: {review.get('important_count', 0)}, spec: `{review.get('spec_path', '—')}`")
    lines.append(f"\n**Epic:** {epic['name']}")
    lines.append(f"**Description:** {epic.get('description', '—')}")
    epic_tasks = reads.epic_tasks(snapshot, epic["id"])
    recent = sorted((t for t in epic_tasks if t.get("status") == "done"),
                    key=lambda t: str(t.get("completed", "")), reverse=True)
    if recent[:3]:
        lines.append("\n**Recently completed in this epic:**")
        for t in recent[:3]:
            lines.append(f"- `{t['id']}` — {t['title']} ({t.get('completed', '?')})")
    upcoming = [t for t in epic_tasks if t.get("status") == "todo" and t["id"] != task_id]
    upcoming.sort(key=lambda t: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(t.get("priority", "medium"), 9))
    if upcoming[:3]:
        lines.append("\n**Next todo in this epic:**")
        for t in upcoming[:3]:
            lines.append(f"- `{t['id']}` — {t['title']} ({t.get('priority', 'medium')})")
    return "\n".join(lines)


@adapter("backlog_list_tasks")
def list_tasks(call, *, epic, status, priority, phase, area, verbose, limit, waiting_on_human):
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    status_order = {"in-progress": 0, "in-review": 1, "blocked": 2, "todo": 3, "done": 4, "archived": 5}
    results = []
    with call.read() as snapshot:
        for ep in reads.epics(snapshot):
            if epic and ep["id"] != epic:
                continue
            if not status and ep.get("status") == "archived":
                continue
            for t in reads.epic_tasks(snapshot, ep["id"]):
                if status and t.get("status") != status:
                    continue
                if not status and t.get("status") == "archived":
                    continue
                if priority and t.get("priority") != priority:
                    continue
                if phase and t.get("phase") != phase:
                    continue
                if area and bs._task_area(t, ep) != area:
                    continue
                if waiting_on_human and not t.get("human_action"):
                    continue
                pri = t.get("priority", "medium")
                entry = f"`{t['id']}` — {t['title']} ({pri}, {ep['id']}, {t.get('status', 'todo')})"
                results.append((status_order.get(t.get("status", "todo"), 9), priority_order.get(pri, 9),
                                str(t.get("created", "")), entry, t))
    if not results:
        filters = [f"{name}={value}" for name, value in (("epic", epic), ("status", status),
                                                         ("priority", priority), ("phase", phase),
                                                         ("area", area)) if value]
        if waiting_on_human:
            filters.append("waiting_on_human")
        return f"No tasks found matching: {', '.join(filters) if filters else 'any'}"
    results.sort(key=lambda x: (x[0], x[1], x[2]))
    total = len(results)
    overflow = 0
    if limit > 0 and total > limit:
        overflow = total - limit
        results = results[:limit]
    header = f"**{total} tasks:**" if not overflow else f"**{total} tasks (showing first {limit}):**"
    footer = f"…{overflow} more tasks — pass status/epic/phase filters or limit=0 for all" if overflow else ""
    lines = [header]
    for _, _, _, entry, t in results:
        if verbose:
            lines.append(f"- {entry}")
            if t.get("tldr"):
                lines.append(f"  tldr: {t['tldr']}")
            if t.get("notes"):
                lines.append(f"  notes: {t['notes']}")
            if t.get("human_action"):
                lines.append(f"  waiting-on-human: {t['human_action']}")
        else:
            slim = f"{entry} — {t['tldr']}" if t.get("tldr", "") else entry
            if t.get("human_action"):
                slim += f"\n    waiting-on-human: {t['human_action']}"
            lines.append(f"- {slim}")
    if footer:
        lines.append(footer)
    return "\n".join(lines)


@adapter("backlog_dependencies")
def dependencies(call, *, task_id, depth):
    depth_error = dependency_chain.depth_error(depth)
    if depth_error:
        return depth_error
    with call.read() as snapshot:
        found = reads.find_task(snapshot, task_id)
        if not found:
            return f"Error: task `{task_id}` not found"
        task, _epic = found
        lines = [f"## Dependencies for `{task_id}` — {task['title']}\n"]
        depends_on = blockers.declared_dependencies(task)
        if isinstance(depends_on, blockers.Unknown):
            lines.append("**Depends on (upstream):**")
            lines.append(bs._unreadable_dependencies_line(depends_on))
            lines.append("\nAll dependencies met: **No**")
        elif depends_on:
            lines.append("**Depends on (upstream):**")
            statuses = {}
            for dependency in depends_on:
                found_dependency = reads.find_task(snapshot, dependency)
                if found_dependency:
                    dep_status = found_dependency[0].get("status", "todo")
                    statuses[dependency] = dep_status
                    check = "done" if dep_status == "done" else "pending"
                    lines.append(f"- [{check}] `{dependency}` — {found_dependency[0]['title']} ({dep_status})")
                else:
                    lines.append(f"- [missing] `{dependency}` — NOT FOUND")
            all_met = not blockers.unmet_dependencies(task, statuses)
            lines.append(f"\nAll dependencies met: **{'Yes' if all_met else 'No'}**")
        else:
            lines.append("**Depends on:** none")
        downstream = reads.dependent_tasks(snapshot, task_id)
        if downstream:
            lines.append("\n**Unblocks (downstream):**")
            for t in downstream:
                lines.append(f"- `{t['id']}` — {t['title']} ({t.get('status', 'todo')})")
        else:
            lines.append("\n**Unblocks:** nothing")
        if depth > 1:
            lines.extend(_dependency_chain(snapshot, task_id, depth))
    return "\n".join(lines)


def _dependency_chain(snapshot, task_id, depth) -> list[str]:
    """`backlog_dependencies`' transitive sections: a breadth-first walk per
    direction with one indexed query per level over canonical `dependencies`,
    rendered as the legacy walk renders."""
    def describe(ident):
        task = reads.find_task(snapshot, ident)[0]
        return task["title"], task.get("status", "todo")

    deadline = dependency_chain.Deadline()
    out = []
    for label, checks in (("upstream", True), ("downstream", False)):
        walked = dependency_graph.traverse(snapshot.connection, task_id, depth, label, deadline)
        out.extend(dependency_chain.lines(label, walked, depth, describe, checks=checks))
    return out


@adapter("backlog_next_available")
def next_available(call, *, include_future_phases):
    with call.read() as snapshot:
        active = next((p for p in reads.phases(snapshot) if p.get("status") == "active"), None)
        epics = [(ep, reads.epic_tasks(snapshot, ep["id"])) for ep in reads.epics(snapshot)]
    statuses = {t["id"]: t.get("status", "todo") for _ep, tasks in epics for t in tasks}
    available, blocked, claimed = [], [], []
    for ep, tasks in epics:
        if ep.get("status") != "active":
            continue
        for t in tasks:
            if t.get("status") != "todo":
                continue
            if active and not include_future_phases and t.get("phase") != active["id"]:
                continue
            unmet = blockers.unmet_dependencies(t, statuses)
            holder = claims.foreign_holder(t, bs.SESSION_ID)
            if unmet:
                blocked.append((t, ep, unmet))
            elif holder:
                claimed.append((t, holder))
            else:
                available.append((t, ep))
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    available.sort(key=lambda x: (order.get(x[0].get("priority", "medium"), 9), str(x[0].get("created", ""))))
    lines = ["## Available Tasks\n"]
    if active:
        lines.append(f"*Filtered to phase: **{active['name']}***\n")
    if available:
        lines.append(f"**{len(available)} tasks ready to pick:**")
        for t, ep in available:
            lines.append(f"- `{t['id']}` — {t['title']} ({t.get('priority', 'medium')}, {ep['id']})")
    elif active:
        lines.append(f"No tasks available in phase **{active['name']}** — all tasks are done, in progress, "
                     f"or have unmet dependencies.")
    else:
        lines.append("No tasks available — all todo tasks have unmet dependencies or belong to non-active epics.")
    if blocked:
        lines.append(f"\n**{len(blocked)} tasks blocked by dependencies:**")
        for t, _ep, unmet in blocked[:5]:
            lines.append(f"- `{t['id']}` — {t['title']} (waiting on {', '.join(f'`{d}`' for d in unmet)})")
    lines.extend(bs._claimed_lines(claimed))
    if active:
        unassigned = [t for ep, tasks in epics if ep.get("status") == "active"
                      for t in tasks if t.get("status") == "todo" and not t.get("phase")]
        if unassigned:
            lines.append(f"\n*{len(unassigned)} todo tasks are not assigned to any phase.*")
    return "\n".join(lines)
