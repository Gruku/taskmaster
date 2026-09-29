# User intent: keep `backlog_batch_update`'s per-line partial apply on a native
# store — each bad line reports its own error and every good line still commits —
# although the native batch is all-or-nothing (N08 constraint), plus the preview.
"""Batch adapters.

Every line is decided before anything commits, by the same pure line rules the
native `task.batch_line`/`epic.batch_line` operations apply (`native.batch_lines`),
against a local overlay so a line sees the lines before it. Only the accepted lines
go to the native structured batch, as one transaction. If the core still refuses
(a peer changed state in between), nothing commits and the answer says so; a refusal
can therefore never discard the good lines silently.

The tool's `commands=` form is the opposite contract — typed mutations, one
transaction, revision preconditions, all of it or none — so it gets its own path
here (`_structured`) and shares nothing with the line path but the argument check
in `native.batch_commands`.
"""
from __future__ import annotations

from copy import deepcopy

from taskmaster import backlog_server as bs
from taskmaster.native import batch_commands, batch_lines, blockers, claims, domain
from taskmaster.native.contracts import MAX_BATCH_COMMANDS, Conflict
from taskmaster.native.workflow import _bugs_found_in

from . import reads
from .tasks import _dependency_statuses as dependency_statuses, field_display
from .registry import adapter
from .runtime import error_text


def _expected(value):
    return bs._MISSING_FIELD if value is batch_lines.missing() else value


class _Overlay:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.tasks: dict = {}
        self.epics: dict = {}

    def task(self, ident):
        if ident not in self.tasks:
            found = reads.find_task(self.snapshot, ident)
            self.tasks[ident] = deepcopy(reads.get(self.snapshot, "task", ident)["fields"]) if found else None
        return self.tasks[ident]

    def epic(self, ident):
        if ident not in self.epics:
            entity = reads.get(self.snapshot, "epic", ident)
            self.epics[ident] = deepcopy(entity["fields"]) if entity else None
        return self.epics[ident]

    def lookups(self):
        return batch_lines.Lookups(
            task_exists=lambda ident: self.task(ident) is not None,
            find_phase=lambda value: domain.find_phase(reads.phases(self.snapshot), value),
            area_error=lambda value: reads.validate_area_ref(self.snapshot, value),
            open_bugs=lambda ident: _bugs_found_in(self.snapshot.connection, ident)[0],
            keeps_claim=self._keeps_claim, session=bs.SESSION_ID)

    def _keeps_claim(self, doc, before):
        return claims.keeps_claim_through(doc, before, session=bs.SESSION_ID,
                                          connection=self.snapshot.connection)

    def cascade(self, epic_id, now):
        count = 0
        for member in reads.epic_tasks(self.snapshot, epic_id):
            task = self.task(member["id"])
            if task is not None and task.get("status") != "archived":
                self.tasks[member["id"]] = domain.archive_task_doc(task, reason="done")
                self.tasks[member["id"]]["archived"] = now
                count += 1
        epic = domain.archive_epic_doc(self.epics[epic_id], reason="done")
        epic["archived"] = now
        self.epics[epic_id] = epic
        return count


def _summary(lines, errors):
    text = f"**Batch update:** {len(lines)} applied"
    if errors:
        text += f", {len(errors)} errors"
    text += "\n\n"
    if lines:
        text += "**Applied:**\n" + "\n".join(f"- {r}" for r in lines) + "\n"
    if errors:
        text += "\n**Errors:**\n" + "\n".join(f"- {e}" for e in errors) + "\n"
    return text


def _render(report, committed, call):
    kind = report[0]
    if kind == "field":
        _kind, ident, field, expected = report
        return f"`{ident}`.{field} → " + field_display(call, committed, ident, field, _expected(expected))
    if kind == "status":
        _kind, ident, status, reason_field = report
        document = committed.get(("task", ident))
        if document is None:  # Committed whole or refused: absent means already in that status.
            return f"`{ident}` → " + field_display(call, committed, ident, "status", status)
        if document.get("status") != status:
            return f"`{ident}` → {bs.NOT_PERSISTED}"
        if not reason_field:
            return f"`{ident}` → {status}"
        return f"`{ident}` → {status} ({document.get(reason_field, bs.NOT_PERSISTED)})"
    if kind == "epic-field":
        _kind, ident, field, expected = report
        return f"epic `{ident}`.{field} → " + field_display(
            call, committed, ident, field, _expected(expected), kind="epic")
    _kind, ident, cascaded = report
    return (f"epic `{ident}`.status → "
            + bs._committed_field_display(committed, ident, "status", "archived", kind="epic")
            + f" ({cascaded} tasks cascaded)")


def _structured(call, commands, expected_revisions):
    """The `commands=` form: one native batch, all of it or none of it.

    Separate from the line path on purpose. Nothing here pre-validates against an
    overlay and nothing here reports per-item outcomes: a refusal or a failed
    precondition applied nothing, and the answer says exactly that rather than
    summarizing a partial apply the caller never asked for.
    """
    if any(item["operation"] == "task.merge" for item in commands):
        # `merge_targets` is reserved to the call: the ladder is the manifest's,
        # resolved here exactly as `backlog_record_merge` resolves it.
        from .tasks import _merge_targets
        with call.read() as snapshot:
            targets = _merge_targets(snapshot)
        commands = [{"operation": item["operation"],
                     "arguments": {**item.get("arguments", {}), "merge_targets": targets}}
                    if item["operation"] == "task.merge" else item for item in commands]
    try:
        receipt = call.execute("batch", {"commands": commands}, expected=expected_revisions or [])
    except Conflict as exc:
        return batch_commands.refused("precondition_failed", error_text(exc)[len("Error: "):])
    except (ValueError, KeyError) as exc:
        return batch_commands.refused("refused", error_text(exc)[len("Error: "):])
    return call.finish(batch_commands.receipt_answer(receipt, len(commands)))


@adapter("backlog_batch_update")
def batch_update(call, *, operations, commands=None, expected_revisions=None, atomic=None):
    form_refusal = batch_commands.check(operations, commands, expected_revisions, atomic)
    if form_refusal is not None:
        return form_refusal
    if commands is not None:
        return _structured(call, commands, expected_revisions)
    return _lines(call, operations)


def _lines(call, operations):
    errors, reports, commands = [], [], []
    now = domain.now_stamp()
    with call.read() as snapshot:
        overlay = _Overlay(snapshot)
        lookups = overlay.lookups()
        for line, op, parts in batch_lines.parse(operations):
            kind = batch_lines.classify(op, parts)
            if kind == "malformed":
                errors.append(f"Skipped malformed line: `{line}`")
                continue
            if kind is None:
                errors.append(f"Unknown or malformed: `{line}`")
                continue
            if kind == "task":
                arguments = batch_lines.task_line_arguments(op, parts)
                ident = arguments["id"]
                if not (op == "update" and arguments["field"] not in domain.ALLOWED_FIELDS) and not (
                        op == "status" and arguments["status"] not in domain.VALID_STATUSES):
                    task = overlay.task(ident)
                else:
                    task = None
                outcome = batch_lines.apply_task_line(arguments, task, lookups, now=now)
                if outcome.error:
                    errors.append(outcome.error)
                    continue
                overlay.tasks[ident] = outcome.doc
                commands.append({"operation": "task.batch_line", "arguments": arguments})
                reports.append((outcome.report[0], ident, *outcome.report[1:]))
                continue
            epic_id, field, value = parts[1], parts[2], parts[3]
            outcome = batch_lines.apply_epic_line(epic_id, field, value,
                                                  overlay.epic(epic_id) if field in domain.ALLOWED_EPIC_FIELDS else None)
            if outcome.error:
                errors.append(outcome.error)
                continue
            commands.append({"operation": "epic.batch_line",
                             "arguments": {"id": epic_id, "field": field, "value": value}})
            if outcome.cascade:
                reports.append(("epic-archive", epic_id, overlay.cascade(epic_id, now)))
            else:
                overlay.epics[epic_id] = outcome.doc
                reports.append(("epic-field", epic_id, outcome.report[1], outcome.report[2]))
    if not commands:
        return _summary([], errors)
    if len(commands) > MAX_BATCH_COMMANDS:
        return (f"Error: this batch has {len(commands)} applicable lines, and a native store applies at most "
                f"{MAX_BATCH_COMMANDS} in one transaction. Nothing was applied; split the batch.")
    try:
        call.execute("batch", {"commands": commands})
    except (ValueError, KeyError) as exc:
        return (f"Error: the batch was refused as a whole and nothing was applied "
                f"(state changed since the lines were checked): {error_text(exc)[len('Error: '):]}")
    committed = reads.committed(call.receipts)
    return call.finish(_summary([_render(report, committed, call) for report in reports], errors))


@adapter("backlog_batch_preview")
def batch_preview(call, *, operations):
    previews = []
    with call.read() as snapshot:
        for line in operations.strip().split("\n"):
            parts = line.strip().split()
            if len(parts) < 2:
                previews.append(f"- Skipped malformed line: `{line.strip()}`")
                continue
            op, task_id = parts[0].lower(), parts[1]
            found = reads.find_task(snapshot, task_id)
            if not found:
                previews.append(f"- `{task_id}`: NOT FOUND")
                continue
            task = found[0]
            current = task.get("status", "todo")
            if op == "complete":
                if current in ("in-progress", "in-review", "blocked"):
                    previews.append(f"- `{task_id}` ({current} → done): {task['title']}")
                else:
                    previews.append(f"- `{task_id}`: Cannot complete — currently `{current}`")
            elif op == "archive":
                if current in ("done", "blocked", "todo"):
                    reason = parts[2] if len(parts) > 2 else ("done" if current == "done" else "deprecated")
                    previews.append(f"- `{task_id}` ({current} → archived, reason: {reason}): {task['title']}")
                else:
                    previews.append(f"- `{task_id}`: Cannot archive — currently `{current}`")
            elif op == "pick":
                holder = claims.foreign_holder(task, bs.SESSION_ID)
                if holder and current in ("todo", "in-progress", "in-review"):
                    # The refusal the batch line gives, before it is applied.
                    previews.append(f"- `{task_id}`: {claims.batch_pick_refusal(task_id, holder)}")
                elif current in ("todo", "in-review"):
                    previews.append(f"- `{task_id}` ({current} → in-progress): {task['title']}")
                    # The resolver's answer, as `backlog_pick_task` warns it.
                    unmet = blockers.unmet_dependencies(task, dependency_statuses(snapshot, task))
                    if unmet:
                        previews.append(f"  ⚠ Unmet dependencies: {', '.join(f'`{d}`' for d in unmet)}")
                elif current == "in-progress":
                    previews.append(f"- `{task_id}`: Already in-progress (idempotent)")
                else:
                    previews.append(f"- `{task_id}`: Cannot pick — currently `{current}`")
            elif op == "status":
                if len(parts) < 3:
                    previews.append(f"- `{task_id}`: Missing target status for `status` operation")
                    continue
                new_status = parts[2]
                refusal = domain.illegal_transition_message(task, new_status)
                if new_status not in domain.VALID_STATUSES:
                    previews.append(f"- `{task_id}`: Invalid status `{new_status}`")
                elif refusal:
                    previews.append(f"- `{task_id}`: {refusal}")
                else:
                    previews.append(f"- `{task_id}` ({current} → {new_status}): {task['title']}")
            else:
                previews.append(f"- Unknown operation `{op}` for `{task_id}`")
    return (f"**Dry-run preview** ({len(previews)} operations):\n" + "\n".join(previews)
            + "\n\n*No changes written. Use the actual tools to apply.*")
