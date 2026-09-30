"""One task and its related entities from one coherent snapshot."""
from taskmaster import viewer_board
from taskmaster.native import claims


def task_etag(connection, identity, task_id, *, native=False):
    table, ident, seq = ("entity_core", "public_id", "last_seq") if native else ("entities", "id", "updated_seq")
    row = connection.execute(f"SELECT {seq} FROM {table} WHERE kind='task' AND {ident}=? AND deleted=0", (task_id,)).fetchone()
    sequence = row[0] if row else None
    if not native:
        flat = connection.execute("SELECT MAX(e.updated_seq) FROM entities e, json_each(e.doc,'$.tasks') t "
                                  "WHERE e.kind='backlog' AND e.deleted=0 AND json_extract(t.value,'$.id')=?",
                                  (task_id,)).fetchone()[0]
        if flat is not None:
            sequence = max(sequence or 0, flat)
    return f"t1:{identity}:{sequence}" if sequence is not None else ""


def legacy_etag(task_id):
    from taskmaster import backlog_server as bs
    store = bs._store()
    if store._network_projection_only:
        return bs._viewer_etag()
    connection = store.connection
    identity = connection.execute("SELECT value FROM meta WHERE key='creation_token'").fetchone()[0]
    return task_etag(connection, identity, task_id)


def write_response(task_id, database=None):
    """A current raw task and its tag, read together after a successful write."""
    from taskmaster import backlog_server as bs
    from taskmaster.native_routing import reads
    with viewer_board.snapshot(database) as snapshot:
        if snapshot.native:
            found = reads.find_task(snapshot.native, task_id)
        else:
            data = snapshot.projection if snapshot.projection is not None else snapshot.legacy.load_dict()
            found = bs._find_task(data, task_id)
        tag = (task_etag(snapshot.connection, snapshot.identity, task_id, native=snapshot.native is not None)
               if snapshot.connection is not None else f"{snapshot.identity}:{snapshot.seq}")
        return (bs._normalize_task(dict(found[0])) if found else None), tag


def related_from(task_id, task, tasks, rows, backlog_path):
    from taskmaster import backlog_server as bs, taskmaster_v3 as v3
    handovers, issues = [], []
    for ident, (doc, body) in sorted(rows.get("handover", {}).items()):
        if doc.get("archived") or task_id not in list(doc.get("task_ids") or []):
            continue
        handovers.append({"id": doc.get("id") or ident, "kind": doc.get("kind"), "session": doc.get("session"),
                          "created": doc.get("created"), "status": doc.get("status", "todo"),
                          "quote": body.strip().splitlines()[0] if body and body.strip() else "",
                          "_path": str(v3.handover_path(backlog_path, ident))})
    for ident, (doc, _) in sorted(rows.get("issue", {}).items()):
        if doc.get("archived") or task_id not in list(doc.get("task_ids") or []):
            continue
        issues.append({"id": doc.get("id") or ident, "severity": doc.get("severity"), "status": doc.get("status"),
                       "title": doc.get("title") or "", "_path": str(v3.issue_path(backlog_path, ident))})
    dependencies, unblocks = bs._related_dependencies(tasks, task, task_id)
    return {"task_id": task_id, "handovers": handovers, "issues": issues, "dependencies": dependencies, "unblocks": unblocks}


def native_inputs(snap, task_id):
    from taskmaster import backlog_server as bs
    from taskmaster.native_routing import reads
    found = reads.find_task(snap, task_id)
    if found is None:
        return None
    task, epic = found
    tasks = {task_id: task}
    wanted = set(bs._dependency_ids(task.get("depends_on")) or [])
    wanted.update(r[0] for r in snap.connection.execute(
        "SELECT c.public_id FROM dependencies d JOIN entity_core c USING(entity_key) "
        "WHERE c.kind='task' AND c.deleted=0 AND d.field='depends_on' AND d.target_id=?", (task_id,)))
    for ident in wanted:
        entity = reads.find_task(snap, ident)
        if entity:
            tasks[ident] = entity[0]
    ranks = dict(snap.connection.execute("SELECT public_id,entity_key FROM entity_core WHERE kind='epic'"))
    ordered = sorted(tasks.values(), key=lambda t: (ranks[t["epic"]], float(t.get("order") or 0), t["id"]))
    rows = {"handover": {}, "issue": {}}
    for kind, ident in snap.connection.execute(
            "SELECT DISTINCT c.kind,c.public_id FROM memberships m JOIN entity_core c USING(entity_key) "
            "WHERE c.kind IN ('handover','issue') AND c.deleted=0 AND m.field='task_ids' AND m.target_id=?", (task_id,)):
        entity = snap.get(kind, ident, include_body=True)
        rows[kind][ident] = (entity["fields"], entity["body"])
    # Legacy issue/task_ids is an extension (the authored issue relation is
    # related_tasks). Preserve the old viewer's task_ids lookup as well.
    for (ident,) in snap.connection.execute(
            "SELECT DISTINCT c.public_id FROM entity_extensions x JOIN entity_core c USING(entity_key) "
            "JOIN json_each(x.value_json) j WHERE c.kind='issue' AND c.deleted=0 "
            "AND x.field='task_ids' AND json_type(x.value_json)='array' AND j.value=?", (task_id,)):
        entity = snap.get("issue", ident, include_body=True)
        rows["issue"][ident] = (entity["fields"], entity["body"])
    return {"epics": [dict(epic, tasks=[task])]}, task, ordered, rows


def read(task_id, database=None, *, compatibility=False):
    from taskmaster import backlog_server as bs
    with viewer_board.snapshot(database) as snapshot:
        if snapshot.native:
            inputs = native_inputs(snapshot.native, task_id)
            if inputs is None:
                return None
            data, task, tasks, rows = inputs
        else:
            data = snapshot.projection if snapshot.projection is not None else snapshot.legacy.load_dict()
            bs._normalize_loaded(data)
            tasks = viewer_board.legacy_tasks(data)
            task = next((t for t in tasks if t.get("id") == task_id), None)
            if task is None:
                return None
            rows = data.get("_rows", {})
            if snapshot.flat:
                import json
                sidecar = snapshot.connection.execute("SELECT doc,body FROM entities WHERE kind='task' AND id=? AND deleted=0",
                                                      (task_id,)).fetchone()
                task = dict(task)
                if sidecar:
                    fields = json.loads(sidecar[0])
                    for key in (*bs._HEAVY_FIELDS, 'patchnote', 'release', 'worktree', 'spec_review',
                                'locked_by', 'claim_expires', 'claim_expires_for'):
                        if key in fields:
                            task[key] = fields[key]
                    task[bs._BODY_KEY] = sidecar[1]
                # Use the stored sidecar from this snapshot, not a later file read.
                data = {'tasks': [task], 'epics': [{'id': task.get('epic'), 'tasks': [task]}]}
        etag = (task_etag(snapshot.connection, snapshot.identity, task_id, native=snapshot.native is not None)
                if snapshot.connection is not None else f"{snapshot.identity}:{snapshot.seq}")
        full, _ = bs._task_full_from(data, etag, bs._backlog_path(), task_id)
        claim = claims.read(task, task_id=task_id, session=bs.SESSION_ID, connection=snapshot.connection).as_dict()
        return {"task": dict(full, claim=claim) if compatibility else claims.without_claim_fields(full),
                "related": related_from(task_id, task, tasks, rows, bs._backlog_path()),
                "claim": claim, "etag": etag}
