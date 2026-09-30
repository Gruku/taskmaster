# User intent: serve the continuity records an agent files while working — bugs,
# issues, ideas and decisions — from the native core (N08), with every answer and
# refusal worded exactly as the legacy tools and reads rendered by their own code.
"""Bug, issue, idea and decision adapters."""
from __future__ import annotations

from taskmaster import backlog_server as bs
from taskmaster import taskmaster_v3 as v3

from . import reads
from .registry import adapter
from .runtime import error_text


def _run(call, operation, arguments):
    try:
        call.execute(operation, arguments)
    except (ValueError, KeyError) as exc:
        return error_text(exc)
    return None


def _created(call):
    return next(item["id"] for item in call.receipts[-1]["affected"] if item["kind"] in ("bug", "issue", "idea", "decision"))


def _rel(target):
    try:
        return target.relative_to(bs.ROOT)
    except ValueError:
        return target


def _row(call, kind, ident):
    with call.read() as snapshot:
        entity = reads.get(snapshot, kind, ident, body=True)
    return entity


# ── Bugs ────────────────────────────────────────────────────────────────────


@adapter("backlog_bug_create")
def bug_create(call, *, title, found_in, discovered_by, severity, components, location, body):
    backlog = bs._backlog_path()
    if not backlog.exists():
        return f"Error: no backlog found at {backlog}. Run `backlog_init` first."
    arguments = {"title": title, "found_in": found_in or None, "discovered_by": discovered_by,
                 "severity": severity or None, "components": components or [], "location": location or []}
    try:
        v3.build_bug_doc(**arguments)
    except ValueError as exc:
        return f"Error: {exc}"
    refusal = _run(call, "bug.create", dict(arguments, body=body))
    if refusal:
        return refusal
    bid = _created(call)
    return call.finish(f"Bug created: {bid} — {title}\nFile: {v3.bug_path(backlog, bid).relative_to(bs.ROOT)}")


@adapter("backlog_bug_list")
def bug_list(call, *, status, found_in, limit, include_archive):
    if not bs._backlog_path().exists():
        return "No backlog found."
    with call.read() as snapshot:
        return bs._bug_list_text(reads.rows_only(snapshot), status, found_in, limit, include_archive)


@adapter("backlog_bug_get")
def bug_get(call, *, bug_id, verbose):
    if not bs._backlog_path().exists():
        return "No backlog found."
    with call.read() as snapshot:
        return bs._bug_get_text(reads.rows_only(snapshot), bug_id, verbose)


@adapter("backlog_bug_update")
def bug_update(call, *, bug_id, field, value):
    if not bs._backlog_path().exists():
        return "No backlog found."
    if field not in bs.BUG_UPDATE_FIELDS:
        return f"Error: field {field!r} not allowed. Allowed: {', '.join(sorted(bs.BUG_UPDATE_FIELDS))}"
    if field == "status" and value not in v3.BUG_STATUSES:
        return f"Error: status must be one of {v3.BUG_STATUSES}"
    updates = ({field: [x.strip() for x in value.split(",") if x.strip()]} if field in bs.BUG_UPDATE_LIST_FIELDS
               else {field: value})
    entity = _row(call, "bug", bug_id)
    if entity is None:
        return f"Bug not found: {bug_id}"
    patch = dict(updates)
    patch.pop("body", None)
    try:
        document = v3.apply_bug_updates(entity["fields"], **patch)
    except ValueError as exc:
        return f"Error: {exc}"
    refusal = _run(call, "bug.update", {"id": bug_id, "patch": updates})
    if refusal:
        return refusal
    return call.finish(f"Bug updated: {bug_id} — status={document['status']}")


@adapter("backlog_bug_archive")
def bug_archive(call, *, bug_id):
    if not bs._backlog_path().exists():
        return "No backlog found."
    entity = _row(call, "bug", bug_id)
    if entity is None:
        return f"Bug not found: {bug_id}"
    try:
        v3.assert_bug_archivable(entity["fields"])
    except ValueError as exc:
        return f"Error: {exc}"
    refusal = _run(call, "bug.archive", {"id": bug_id})
    if refusal:
        return refusal
    return call.finish(f"Bug archived: {bug_id}")


@adapter("backlog_bug_pattern_scan")
def bug_pattern_scan(call, *, mode):
    if mode not in {"all", "open_only", "end_of_task"}:
        return f"Error: invalid mode {mode!r} (expected all|open_only|end_of_task)"
    if not bs._backlog_path().exists():
        return "No backlog found."
    with call.read() as snapshot:
        return bs._bug_pattern_text(reads.rows_only(snapshot), mode)


@adapter("backlog_bug_promote")
def bug_promote(call, *, bug_ids, title, severity, evidence_text, components, body):
    backlog = bs._backlog_path()
    if not backlog.exists():
        return f"Error: no backlog found at {backlog}. Run `backlog_init` first."
    bug_ids = list(bug_ids or [])
    if not bug_ids:
        return "Error: bug_ids must be non-empty"
    if not evidence_text or not evidence_text.strip():
        return "Error: evidence_text is required (cite recurrence/systemic/outstanding)"
    with call.read() as snapshot:
        sources = {}
        for bid in bug_ids:
            entity = reads.get(snapshot, "bug", bid)
            if entity is None:
                return f"Error: bug {bid} not found"
            sources[bid] = entity["fields"]
    derived = components or None
    if derived is None:
        derived = sorted({c for bid in bug_ids for c in (sources[bid].get("components") or [])})
    try:
        v3.build_issue_doc(title=title, severity=severity, impact=evidence_text, evidence=evidence_text,
                           components=derived, promoted_from=list(bug_ids))
        for bid in bug_ids:
            v3.apply_bug_updates(sources[bid], status="promoted", promoted_to="ISS-000")
    except ValueError as exc:
        return f"Error: {exc}"
    arguments = {"bug_ids": bug_ids, "title": title, "severity": severity, "evidence_text": evidence_text,
                 "body": body}
    if components:
        arguments["components"] = list(components)
    refusal = _run(call, "bug.promote", arguments)
    if refusal:
        return refusal
    issue_id = next(item["id"] for item in call.receipts[-1]["affected"] if item["kind"] == "issue")
    return call.finish(f"Promoted {len(bug_ids)} bug(s) to {issue_id}.")


# ── Issues ──────────────────────────────────────────────────────────────────


@adapter("backlog_issue_create")
def issue_create(call, *, title, severity, evidence, impact, components, location, related_tasks,
                 discovered_by, body, tldr):
    backlog = bs._backlog_path()
    if not backlog.exists():
        return f"Error: no backlog found at {backlog}. Run `backlog_init` first."
    if severity not in v3.ISSUE_SEVERITIES:
        return f"Error: severity must be one of {v3.ISSUE_SEVERITIES}"
    autogen = False
    if not tldr:
        tldr = v3.extract_tldr(impact) or title[:v3.TLDR_MAX_CHARS]
        autogen = True
    arguments = {"title": title, "severity": severity, "evidence": evidence, "impact": impact,
                 "components": components or [], "location": location or [],
                 "related_tasks": related_tasks or [], "discovered_by": discovered_by, "tldr": tldr,
                 "tldr_autogen": autogen}
    try:
        v3.build_issue_doc(**arguments)
    except ValueError as exc:
        return f"Error: {exc}"
    refusal = _run(call, "issue.create", dict(arguments, body=body))
    if refusal:
        return refusal
    iid = _created(call)
    return call.finish(f"Issue created: {iid} ({severity}) — {title}\nFile: {v3.issue_path(backlog, iid).relative_to(bs.ROOT)}")


@adapter("backlog_issue_list")
def issue_list(call, *, severity, status, limit, verbose):
    if not bs._backlog_path().exists():
        return "No backlog found."
    with call.read() as snapshot:
        return bs._issue_list_text(reads.rows_only(snapshot), severity, status, limit, verbose)


@adapter("backlog_issue_get")
def issue_get(call, *, issue_id, verbose, sections, expand_links):
    backlog = bs._backlog_path()
    if not backlog.exists():
        return "No backlog found."
    with call.read() as snapshot:
        return bs._issue_get_text(reads.rows_only(snapshot), issue_id, verbose, sections, expand_links, backlog,
                                  reads.NativeLinks(snapshot, backlog))


@adapter("backlog_issue_update")
def issue_update(call, *, issue_id, field, value):
    if not bs._backlog_path().exists():
        return "No backlog found."
    if field not in bs.ISSUE_UPDATE_FIELDS:
        return f"Error: field {field!r} not allowed. Allowed: {', '.join(sorted(bs.ISSUE_UPDATE_FIELDS))}"
    if field == "status" and value not in v3.ISSUE_STATUSES:
        return f"Error: status must be one of {v3.ISSUE_STATUSES}"
    if field == "severity" and value not in v3.ISSUE_SEVERITIES:
        return f"Error: severity must be one of {v3.ISSUE_SEVERITIES}"
    updates = ({field: [x.strip() for x in value.split(",") if x.strip()]} if field in bs.ISSUE_UPDATE_LIST_FIELDS
               else {field: value})
    entity = _row(call, "issue", issue_id)
    if entity is None:
        return f"Issue not found: {issue_id}"
    patch = dict(updates)
    patch.pop("body", None)
    try:
        document = v3.apply_issue_updates(entity["fields"], **patch)
    except ValueError as exc:
        return f"Error: {exc}"
    refusal = _run(call, "issue.update", {"id": issue_id, "patch": updates})
    if refusal:
        return refusal
    return call.finish(f"Issue updated: {issue_id} → status={document['status']}, severity={document['severity']}")


# ── Ideas ───────────────────────────────────────────────────────────────────


@adapter("backlog_idea_create")
def idea_create(call, *, title, body, tags, status, related_tasks, related_issues, created_by, tldr):
    backlog = bs._backlog_path()
    if not backlog.exists():
        return f"Error: no backlog found at {backlog}. Run `backlog_init` first."
    autogen = False
    if not tldr:
        tldr = v3.extract_tldr(body) or title[:v3.TLDR_MAX_CHARS]
        autogen = True
    arguments = {"title": title, "tags": tags or [], "status": status, "related_tasks": related_tasks or [],
                 "related_issues": related_issues or [], "created_by": created_by, "tldr": tldr,
                 "tldr_autogen": autogen}
    try:
        v3.build_idea_doc(**arguments)
    except ValueError as exc:
        return f"Error: {exc}"
    refusal = _run(call, "idea.create", dict(arguments, body=body))
    if refusal:
        return refusal
    iid = _created(call)
    return call.finish(f"Idea created: {iid} — {title}\nFile: {_rel(v3.idea_path(backlog, iid))}")


@adapter("backlog_idea_list")
def idea_list(call, *, idea_id, status, tag, archived, related_task, related_issue, limit, verbose):
    if not bs._backlog_path().exists():
        return "No backlog found."
    with call.read() as snapshot:
        return bs._idea_list_text(reads.rows_only(snapshot), idea_id, status, tag, archived, related_task,
                                  related_issue, limit, verbose)


@adapter("backlog_idea_get")
def idea_get(call, *, idea_id, verbose, sections, expand_links):
    backlog = bs._backlog_path()
    if not backlog.exists():
        return "No backlog found."
    if sections is not None and not sections:
        return ("Error: sections=[] requested no sections; pass sections=None for the slim view "
                "or name at least one section")
    if sections:
        return "Error: ideas have no canonical body sections — use verbose=True to read the full body."
    with call.read() as snapshot:
        return bs._idea_get_text(reads.rows_only(snapshot), idea_id, verbose, expand_links, backlog,
                                 reads.NativeLinks(snapshot, backlog))


@adapter("backlog_idea_update")
def idea_update(call, *, idea_id, field, value):
    if not bs._backlog_path().exists():
        return "No backlog found."
    if field not in bs.IDEA_UPDATE_FIELDS:
        return f"Error: field {field!r} not allowed. Allowed: {', '.join(sorted(bs.IDEA_UPDATE_FIELDS))}"
    if field == "archived":
        if value.lower() not in ("true", "false"):
            return "Error: archived must be 'true' or 'false'"
        updates = {"archived": value.lower() == "true"}
    elif field in bs.IDEA_UPDATE_LIST_FIELDS:
        updates = {field: [x.strip() for x in value.split(",") if x.strip()]}
    else:
        updates = {field: value}
    entity = _row(call, "idea", idea_id)
    if entity is None:
        return f"Idea not found: {idea_id}"
    patch = {k: v for k, v in updates.items() if k not in ("body", "archived")}
    try:
        document = v3.apply_idea_updates(entity["fields"], **patch)
    except ValueError as exc:
        return f"Error: {exc}"
    refusal = _run(call, "idea.update", {"id": idea_id, "patch": updates})
    if refusal:
        return refusal
    return call.finish(f"Idea updated: {idea_id} — {document.get('title', '')}")


# ── Decisions ───────────────────────────────────────────────────────────────


@adapter("backlog_decision_create")
def decision_create(call, *, title, options, recommendation, task_id, related_issues, branch, raised_in, body):
    backlog = bs._backlog_path()
    if not backlog.exists():
        return f"Error: no backlog found at {backlog}. Run `backlog_init` first."
    arguments = {"title": title, "options": options, "recommendation": recommendation, "task_id": task_id,
                 "related_issues": related_issues or [], "branch": branch, "raised_in": raised_in}
    try:
        v3.build_decision_doc(**arguments)
    except ValueError as exc:
        return f"Error: {exc}"
    refusal = _run(call, "decision.create", dict(arguments, body=body))
    if refusal:
        return refusal
    did = _created(call)
    return call.finish(f"Decision created: {did} — {title}\nFile: {v3.decision_path(backlog, did).relative_to(bs.ROOT)}")


@adapter("backlog_decision", actions=("list", "get", "resolve", "drop", "update"))
def decision(call, *, action, decision_id, status, task_id, limit, resolved_with, rationale, resolved_in, reason,
             title, options, recommendation, body):
    if action == "list":
        if not bs._backlog_path().exists():
            return "No backlog found."
        with call.read() as snapshot:
            return bs._decision_list_text(reads.rows_only(snapshot), status, task_id, limit)
    if action == "get":
        with call.read() as snapshot:
            return bs._decision_get_text(reads.rows_only(snapshot), decision_id)
    if action == "resolve" and resolved_with is None:
        return "Error: resolve requires resolved_with (1-indexed option)"
    entity = _row(call, "decision", decision_id)
    if entity is None:
        return f"Error: Decision not found: {decision_id}"
    try:
        if action == "resolve":
            document = v3.resolve_decision_doc(entity["fields"], resolved_with=int(resolved_with),
                                               rationale=rationale, resolved_in=resolved_in or None)
        elif action == "drop":
            v3.drop_decision_doc(entity["fields"], reason=reason)
        else:
            patch = {}
            if title:
                patch["title"] = title
            if options:
                patch["options"] = options
            if recommendation is not None:
                patch["recommendation"] = recommendation
            v3.apply_decision_patch(entity["fields"], patch)
    except ValueError as exc:
        return f"Error: {exc}"
    if action == "resolve":
        arguments = {"id": decision_id, "resolved_with": int(resolved_with), "rationale": rationale}
        if resolved_in:
            arguments["resolved_in"] = resolved_in
        refusal = _run(call, "decision.resolve", arguments)
        answer = (f"Decision {decision_id} resolved with option {document['resolved_with']}: "
                  f"\"{document['options'][document['resolved_with'] - 1]}\"")
    elif action == "drop":
        refusal = _run(call, "decision.drop", {"id": decision_id, "reason": reason})
        answer = f"Decision {decision_id} dropped: {reason}"
    else:
        arguments = {"id": decision_id, "patch": patch}
        if body:
            arguments["body"] = body
        refusal = _run(call, "decision.update", arguments)
        answer = f"Decision {decision_id} updated."
    if refusal:
        return refusal
    return call.finish(answer)
