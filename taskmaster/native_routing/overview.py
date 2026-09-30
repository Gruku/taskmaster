# User intent: serve the remaining normal-path tools — dashboard, search, read-only
# SQL, store status, blast radius, the project manifest and the local Linear actions —
# from the native core (N08), with the legacy wording and no legacy store open.
"""Dashboard, search, SQL, diagnostics, project and Linear adapters."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import socket
import sqlite3

from taskmaster import backlog_server as bs
from taskmaster import query_guard
from taskmaster import store
from taskmaster import taskmaster_v3 as v3
from taskmaster.native import compatibility
from taskmaster.native import projection as outbox

from . import reads
from .registry import adapter
from .runtime import error_text


def _run(call, operation, arguments):
    try:
        call.execute(operation, arguments)
    except (ValueError, KeyError) as exc:
        return error_text(exc)
    return None


# ── Dashboard, blast radius, search ─────────────────────────────────────────


@adapter("backlog_status")
def status(call, *, verbose):
    with call.read() as snapshot:
        return bs._status_text(reads.dashboard_tree(snapshot), verbose)


@adapter("backlog_blast_radius")
def blast_radius(call, *, task_id, mode, depth_override, structured):
    if mode not in ("predictive", "evidence"):
        return f"Error: mode must be 'predictive' or 'evidence', got '{mode}'"
    with call.read() as snapshot:
        return bs._blast_radius_text(reads.tree(snapshot, context=False), task_id, mode, depth_override, structured)


@adapter("backlog_search")
def search(call, *, query, kinds):
    if isinstance(kinds, str):
        kinds = [kinds]
    match = bs._fts_match_expression(query)
    selected = [k for k in kinds or () if k in bs._SEARCH_KINDS] or list(bs._SEARCH_KINDS)
    if match:
        try:
            return bs._search_index_text(call.connection, query, selected, match, native=True)
        except (sqlite3.Error, ValueError):
            pass
    with call.read() as snapshot:
        return bs._search_fallback_text(reads.tree(snapshot, context=False), query)


# ── Read-only SQL ───────────────────────────────────────────────────────────


@adapter("backlog_query")
def query(call, *, sql, limit):
    """The public SQL names answered from an explicit compatibility snapshot.

    This is the allowlisted compatibility materialization N04 built: it rebuilds the
    legacy tables in a private in-memory database per call, so it costs a full
    snapshot. The answer and its errors keep the tool's shape.
    """
    limit = max(1, min(500, limit))
    guard = deadline = None
    try:
        statement = query_guard.validate(sql)
        guard = query_guard.Authorizer(query_guard.declared_names(statement))
        deadline = query_guard.Deadline(query_guard.QUERY_TIMEOUT_S)
        with call.read():
            # Wrapped as the tool wraps it, so columns and caps come out the same.
            result = compatibility.query(call.connection, f"SELECT * FROM ({statement})", limit=limit,
                                         authorizer=guard, deadline=deadline)
    except (sqlite3.Error, ValueError, OSError) as exc:
        if deadline is not None and deadline.expired:
            reason = deadline.message
        elif guard is not None and guard.denial:
            reason = f"not authorized: {guard.denial}"
        else:
            reason = str(exc)
        return f"Error: {reason}\n\nSchema: {query_guard.SCHEMA_SUMMARY}"
    rows = list(result["rows"]) + ([None] if result["truncated"] else [])
    return bs._render_query_table([(name,) for name in result["columns"]], rows, limit)


# ── Store status ────────────────────────────────────────────────────────────


def _native_status(call) -> store.StoreStatus:
    database = Path(call.connection.execute("PRAGMA database_list").fetchone()[2])
    resolution = store.resolve_location(bs._backlog_path())
    with call.read() as snapshot:
        connection = snapshot.connection
        meta = dict(connection.execute("SELECT key,value FROM meta WHERE key IN ('creation_token','schema_version')"))
        dirty = tuple(r[0] for r in connection.execute("SELECT file FROM projection WHERE dirty=1 ORDER BY file"))
        quarantined = tuple(r[0] for r in connection.execute(
            "SELECT file FROM projection WHERE quarantined=1 ORDER BY file"))
        # An export the drain still owes — quarantined, flagged, or a write that
        # failed or has not run yet — is stuck.
        stuck = tuple(sorted({r[0] for r in connection.execute(
            "SELECT file FROM projection WHERE dirty=1 AND quarantined=1 UNION "
            "SELECT file FROM projection_jobs WHERE state IN ('pending','claimed','conflict')")}))
        flagged = outbox.flagged_files(connection)
        seq = int(connection.execute("SELECT COALESCE(MAX(seq),0) FROM domain_events").fetchone()[0])
        recent = tuple({**dict(zip(("seq", "ts", "session", "tool", "kind", "id", "op"), row))}
                       for row in connection.execute(
                           "SELECT seq,ts,session,tool,kind,id,op FROM domain_events ORDER BY seq DESC LIMIT 20"))
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        live = []
        for row in connection.execute("SELECT session,pid,host,started,last_seen,cwd,current_tool FROM sessions "
                                      "ORDER BY last_seen DESC"):
            session = dict(zip(("session", "pid", "host", "started", "last_seen", "cwd", "current_tool"), row))
            if (session["last_seen"] or "") >= cutoff or (session["current_tool"] and session["host"] == socket.gethostname()
                                                          and store._local_pid_alive(session["pid"])):
                live.append(session)
        queued = int(connection.execute(
            "SELECT COUNT(*) FROM linear_queue WHERE state IN ('pending','claimed')").fetchone()[0])
        exporter = _exporter_lease(outbox.lease(connection))
    wal = Path(f"{database}-wal")
    backups = tuple(sorted(name for name in os.listdir(database.parent) if name.startswith("store.db.corrupt-")))
    return store.StoreStatus(
        root=resolution.root, db_path=database, creation_token=meta.get("creation_token", ""), max_seq=seq,
        dirty_files=dirty, quarantined_files=quarantined, resolution_source=resolution.source,
        schema_version=int(meta.get("schema_version", 0)), db_size=database.stat().st_size,
        wal_size=wal.stat().st_size if wal.exists() else 0, recent_changes=recent, live_sessions=tuple(live),
        merge_conflicts_24h=0, warning=store._network_filesystem_reason(resolution.root) or resolution.filesystem_warning,
        corrupt_files=backups, linear_pending=queued, stuck_exports=stuck, flagged_files=flagged,
        read_scan_skips=0, exporter_lease=exporter)


_OWNER_PROCESS = re.compile(r".*:(\d+)@([^:@]+)")


def _exporter_lease(lease: dict, now: float | None = None) -> str:
    """Who holds the projection exporter lease, and whether its process still runs.

    A holder that died keeps the lease until it expires, and every caller that
    needs an export waits behind it (bounded) until then (scope §5.4). The drain
    names its process in the owner (`…:<pid>@<host>`). The line reports what a pid
    check can tell and no more: a reused pid, or one hostname shared by two
    machines, reads as running. The lease's own expiry is what recovery trusts.
    """
    import time
    now = time.time() if now is None else now
    owner, generation = lease.get("owner"), lease.get("generation")
    until = float(lease.get("until") or 0)
    if not owner or until <= now:
        return "free" + (f" (last holder {owner}, generation {generation})" if owner else "")
    match = _OWNER_PROCESS.fullmatch(owner)
    if match is None:
        holder = "holder process unknown"
    elif match.group(2) != socket.gethostname():
        holder = f"pid {match.group(1)} on host {match.group(2)}, not checkable from here"
    elif store._local_pid_alive(int(match.group(1))):
        holder = f"pid {match.group(1)} running on this host"
    else:
        holder = (f"pid {match.group(1)} not running on this host; its claimed exports are recovered "
                  "once the lease expires")
    return f"held by {owner}, generation {generation}, expires in {until - now:.0f}s, {holder}"


@adapter("backlog_store_status")
def store_status(call):
    backlog = bs._backlog_path()
    if not backlog.exists():
        return f"no backlog found at {backlog}"
    return bs._render_store_report(_native_status(call))


# ── Project manifest ────────────────────────────────────────────────────────


def _manifest_raw(call):
    from taskmaster.project import validate_manifest_dict
    with call.read() as snapshot:
        entity = reads.get(snapshot, "project", "__project__")
    if entity is None:
        return None
    data = deepcopy(entity["fields"])
    try:
        ok, _errors = validate_manifest_dict(data)
    except Exception:
        return None
    return data if ok else None


@adapter("backlog_project_get")
def project_get(call):
    from taskmaster.project import manifest_to_dict
    manifest = bs._manifest_from_raw(_manifest_raw(call))
    return manifest_to_dict(manifest) if manifest is not None else None


@adapter("backlog_project_get_field")
def project_get_field(call, *, path):
    data = _manifest_raw(call)
    return None if data is None else bs._dig(data, path)


@adapter("backlog_project_ship_order")
def project_ship_order(call):
    manifest = bs._manifest_from_raw(_manifest_raw(call))
    return manifest.ship_order() if manifest is not None else []


@adapter("backlog_project_error_trace_ladder")
def project_error_trace_ladder(call):
    manifest = bs._manifest_from_raw(_manifest_raw(call))
    return [] if manifest is None else [asdict(entry) for entry in manifest.error_trace_ladder()]


def _project_path():
    from taskmaster.project import project_yaml_path
    return project_yaml_path(bs._project_root_or_cwd())


@adapter("backlog_project_set")
def project_set(call, *, yaml_content):
    from taskmaster import yaml_io
    from taskmaster.project import validate_manifest_dict
    import yaml
    try:
        data = yaml_io.safe_load(yaml_content) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML parse failed: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("project.yaml top-level must be a mapping")
    validate_manifest_dict(data, raise_on_error=True)
    path = _project_path()
    bs._ensure_taskmaster_dir(path)
    call.execute("project.set", {"document": data})
    return call.finish(str(path))


@adapter("backlog_project_init")
def project_init(call, *, name, slug):
    from taskmaster.project import SCHEMA_VERSION, validate_manifest_dict
    if not name or not name.strip():
        raise ValueError("name: required (project name must be non-empty)")
    path = _project_path()
    with call.read() as snapshot:
        exists = reads.get(snapshot, "project", "__project__") is not None
    if exists:
        raise ValueError(f"{path} exists — refusing to overwrite (edit it directly)")
    scaffold = {
        "schema_version": SCHEMA_VERSION,
        "meta": {"name": name, "slug": slug or bs._slugify(name), "kind": "app"},
        "project": {"description": "", "goal": "", "owners": [], "tags": []},
        "repos": [], "submodules": [],
        "integrations": {"observability": {"error_trace_ladder": []}, "external": []},
        "conventions": {"narrative_ref": "./CLAUDE.md", "policies": {}},
        "extensions": {},
    }
    bs._ensure_taskmaster_dir(path)
    ok, errors = validate_manifest_dict(scaffold)
    if not ok:
        raise ValueError("refusing to write invalid manifest: " + "; ".join(errors))
    call.execute("project.set", {"document": scaffold, "create_only": True})
    return call.finish(f"Created {path}")


# ── Linear (local actions only) ─────────────────────────────────────────────


@adapter("backlog_linear", actions=("probe", "bootstrap_apply", "link", "unlink", "list", "show", "status", "retry"),
         unknown=lambda action: json.dumps({"error": f"unknown action {action!r}"}))
def linear(call, *, action, task_id, external_key, workspace_alias, token_env, team_id, status_mapping,
           priority_mapping, default_workspace, tracker_id, target_id, request_id, caller_scope):
    if action == "probe":
        # An external query against Linear's API; it reads no store and no projection.
        return bs.backlog_linear_probe(token_env)
    backlog = bs._backlog_path()
    if action == "list":
        if not backlog.exists():
            return json.dumps({"trackers": []})
        with call.read() as snapshot:
            return bs._linear_list_text(reads.rows_only(snapshot))
    if action == "show":
        if not backlog.exists():
            return json.dumps({"error": "No backlog found."})
        with call.read() as snapshot:
            return bs._linear_show_text(reads.rows_only(snapshot), tracker_id)
    if action == "status":
        return _linear_status(call, backlog)
    if action == "bootstrap_apply":
        return _linear_bootstrap(call, workspace_alias, team_id, token_env, status_mapping, priority_mapping,
                                 default_workspace)
    if action == 'retry':
        try:
            return json.dumps(call.client.linear_retry(caller_scope=caller_scope or call.session,
                                                      request_id=request_id or None, target_id=target_id))
        except (ValueError, KeyError) as exc:
            return json.dumps({'error': str(exc)})
    if not backlog.exists():
        return json.dumps({"error": "No backlog found."})
    if action == "unlink":
        return _linear_unlink(call, task_id)
    return _linear_link(call, backlog, task_id, external_key, workspace_alias)


def _linear_bootstrap(call, workspace_alias, team_id, token_env, status_mapping, priority_mapping,
                      default_workspace):
    # linear.yaml is authored configuration: the coordinator rewrites it atomically
    # under its publication boundary (N13 step 7), never this process.
    try:
        entry = bs._linear_workspace_entry(workspace_alias, team_id, token_env, status_mapping, priority_mapping)
        return json.dumps(call.client.linear_bootstrap(entry=entry, default_workspace=bool(default_workspace)))
    except (ValueError, KeyError) as exc:
        return json.dumps({"error": exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)})


def _linear_status(call, backlog):
    if not backlog.exists():
        return bs._linear_status_text([], 0, None)
    with call.read() as snapshot:
        rows = [store.linear_row(dict(zip(
            ("seq", "op", "target_id", "tracker_id", "payload", "state", "attempts", "last_error", "claimed_by",
             "claimed_at"), row))) for row in snapshot.connection.execute(
            "SELECT seq,op,target_id,tracker_id,payload,state,attempts,last_error,claimed_by,claimed_at "
            "FROM linear_queue WHERE state IN ('pending','claimed','failed') ORDER BY seq")]
    database = Path(call.connection.execute("PRAGMA database_list").fetchone()[2])
    return bs._linear_status_text(rows, store.count_linear_enqueue_failures(database.parent), None)


def _linear_link(call, backlog, task_id, external_key, workspace_alias):
    # linear.yaml is configuration beside the store, not a projection of it.
    config = v3.load_linear_config(backlog)
    if config is None:
        return json.dumps({"error": "linear.yaml not found — run backlog_linear_bootstrap_apply first."})
    try:
        workspace = v3.get_linear_workspace(config, workspace_alias or None)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    alias = workspace["alias"]
    with call.read() as snapshot:
        found = reads.find_task(snapshot, task_id)
        if found is None:
            return json.dumps({"error": f"task {task_id!r} not found in backlog"})
        existing = found[0].get("tracker_id")
        if existing:
            return json.dumps({"error": f"task {task_id!r} already has tracker_id {existing!r} — unlink first"})
        tracker_id = v3.make_tracker_id("linear", alias, external_key)
        taken = snapshot.connection.execute(
            "SELECT 1 FROM entity_core WHERE kind='tracker' AND public_id=? UNION ALL "
            "SELECT 1 FROM id_reservations WHERE kind='tracker' AND public_id=? LIMIT 1",
            (tracker_id, tracker_id)).fetchone()
    if taken:
        return json.dumps({"error": f"tracker {tracker_id} already exists — it may be linked to another task"})
    try:
        v3.build_tracker_doc(external_system="linear", instance_alias=alias, external_key=external_key,
                             title=found[0].get("title", external_key), status=found[0].get("status", "todo"))
    except ValueError as exc:
        return json.dumps({"error": f"failed to write tracker: {exc}"})
    refusal = _run(call, "linear.link", {"task_id": task_id, "external_key": external_key, "workspace_alias": alias})
    if refusal:
        return json.dumps({"error": refusal[len("Error: "):]})
    return call.finish(json.dumps({"ok": True, "tracker_id": tracker_id, "task_id": task_id}))


def _linear_unlink(call, task_id):
    with call.read() as snapshot:
        found = reads.find_task(snapshot, task_id)
    if found is None:
        return json.dumps({"error": f"task {task_id!r} not found in backlog"})
    existing = found[0].get("tracker_id")
    if not existing:
        return json.dumps({"ok": True, "note": f"task {task_id!r} had no tracker_id — nothing to unlink"})
    refusal = _run(call, "linear.unlink", {"task_id": task_id})
    if refusal:
        return json.dumps({"error": refusal[len("Error: "):]})
    return call.finish(json.dumps({"ok": True, "unlinked": existing, "task_id": task_id}))


@adapter("backlog_validate")
def validate(call):
    # Trackers and artifact tldrs are rows here, not files: a native store never
    # re-reads its exports, so there is no file-level malformation to report.
    with call.read() as snapshot:
        data = reads.tree(snapshot)
        trackers = {ident: doc for ident, doc, _body in reads.rows(snapshot, "tracker", include_archived=True)}
        missing_tldr = [ident for kind in ("issue", "handover", "idea")
                        for ident, doc, _body in reads.rows(snapshot, kind) if not doc.get("tldr")]
    return bs._validate_text(data, trackers, [], missing_tldr, bs._backlog_path())


# ── Derived-table status and the graph repair (N14) ─────────────────────────


def _graph_report(report) -> list[str]:
    differences = sum(t["missing"] + t["spurious"] for t in report["tables"].values())
    if report["clean"]:
        state = "clean"
    elif report["repaired"]:
        state = f"{differences} differences, repaired"
    else:
        state = f"{differences} differences, not repaired (rebuild=True repairs them)"
    lines = [f"Graph check: {state}"]
    for table, facts in report["tables"].items():
        if facts["missing"] or facts["spurious"]:
            lines.append(f"  {table}: missing {facts['missing']}, spurious {facts['spurious']}")
            for side in ("missing", "spurious"):
                lines.extend(f"    {side} {tuple(row)}" for row in facts["examples"][side])
    lines.append(f"Cost: {report['entities']} entities, {report['rows_compared']} rows compared, "
                 f"{report['seconds']:.3f}s")
    return lines


@adapter("backlog_index_status")
def index_status(call, *, rebuild, verify):
    """Row counts for the derived tables; `verify`/`rebuild` run the graph oracle.

    Only this explicit maintenance call runs the full oracle: commands keep the
    graph tables current incrementally and no read consults it.
    """
    from taskmaster.native import graph_repair
    backlog = bs._backlog_path()
    if not backlog.exists():
        return f"no backlog found at {backlog}"
    report = None
    if rebuild:
        try:
            report = call.execute("graph.repair", {})["result"]
        except (ValueError, KeyError) as exc:
            return error_text(exc)
    elif verify:
        with call.read() as snapshot:
            report = graph_repair.verify(snapshot)
    with call.read() as snapshot:
        connection = snapshot.connection
        counts = {table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                  for table in store.DERIVED_TABLES if table != "entity_fts"}
        # The native search table serves the public `entity_fts` name.
        counts["entity_fts"] = int(connection.execute("SELECT COUNT(*) FROM document_search").fetchone()[0])
        counts["entities"] = int(connection.execute("SELECT COUNT(*) FROM entity_core WHERE deleted=0").fetchone()[0])
        # The last repair's run time, clean or not, as legacy shows its last rebuild.
        repaired = connection.execute("SELECT value FROM native_manifest WHERE key=?",
                                      (graph_repair.CHECKED_AT,)).fetchone()
    database = Path(call.connection.execute("PRAGMA database_list").fetchone()[2])
    text = bs._render_derived_report({"row_counts": counts, "rebuilt_at": repaired[0] if repaired else None}, database)
    return "\n".join([text, *(_graph_report(report) if report is not None else [])])
