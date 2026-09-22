"""Snapshot-scoped board reads. Decide 304 before decoding any entity."""
from contextlib import contextmanager
from time import perf_counter

from taskmaster import viewer_dto as dto

DELTA_MAX = 200


def legacy_tasks(data):
    if isinstance(data.get('tasks'), list):
        return [dict(t) for t in data['tasks']]
    return [dict(t, epic=t.get('epic', e['id'])) for e in data.get('epics', []) for t in e.get('tasks', [])]


def flat_sequence(connection):
    """Old flat indexes are authored backlog input, unlike derived v4 indexes."""
    row = connection.execute("SELECT MAX(updated_seq) FROM entities WHERE kind='backlog' "
                             "AND deleted=0 AND json_type(doc,'$.tasks')='array'").fetchone()
    return row[0]


def compatibility(database=None, *, if_none_match=None):
    """The old full payload, excluding its private rows; 304 before tree build."""
    from taskmaster import backlog_server as bs
    from taskmaster.native_routing import reads
    start = perf_counter()
    with snapshot(database) as snap:
        if snap.native:
            seq = snap.native.identity["event_high_water"]
        elif snap.connection is not None:
            seq = snap.connection.execute("SELECT COALESCE(MAX(seq),0) FROM changes").fetchone()[0]
        else:
            seq = snap.seq
        etag = f"c1:{snap.identity}:{seq}:{bs.VERSION}:{bs.SESSION_ID}"
        timings = {"rev": (perf_counter() - start) * 1000, "build": 0}
        if dto.matches(if_none_match, etag):
            return 304, None, etag, timings
        start = perf_counter()
        if snap.native:
            data = reads.tree(snap.native)
        else:
            data = snap.projection if snap.projection is not None else snap.legacy.load_dict()
            bs._normalize_loaded(data)
        data.pop("_rows", None)
        data.setdefault("meta", {})["_version"] = bs.VERSION
        if not isinstance(data.get("tasks"), list):
            data["tasks"] = [dict(t, epic=t.get("epic", e["id"])) for e in data.get("epics", []) for t in e.get("tasks", [])]
        data["phases"] = sorted(data.get("phases", []), key=lambda p: p.get("order") if p.get("order") is not None else 999)
        timings["build"] = (perf_counter() - start) * 1000
        return 200, data, etag, timings


class BoardSnapshot:
    def __init__(self, connection, identity, seq, *, native=None, legacy=None, projection=None, flat=False):
        self.connection, self.identity, self.seq = connection, identity, seq
        self.native, self.legacy, self.projection = native, legacy, projection
        self._legacy_data = None
        self.flat = flat

    def documents(self, kind, fields):
        if self.native is not None:
            items, cursor = [], None
            while True:
                page = self.native.list(kind, fields=fields, include_archived=True, limit=500, cursor=cursor)
                items.extend(e["fields"] for e in page["items"])
                cursor = page["cursor"]
                if cursor is None:
                    break
            if kind in ("epic", "phase"):
                ranks = dict(self.connection.execute(
                    "SELECT public_id,entity_key FROM entity_core WHERE kind=?", (kind,)))
                items.sort(key=lambda e: ranks[e["id"]])
            return items
        if self._legacy_data is None:
            self._legacy_data = self.projection if self.projection is not None else self.legacy.load_dict()
        data = self._legacy_data
        if kind == "task":
            return legacy_tasks(data)
        items = data.get(kind + "s", [])
        if self.connection is not None:
            ranks = dict(self.connection.execute("SELECT id,rowid FROM entities WHERE kind=?", (kind,)))
            items.sort(key=lambda e: ranks.get(e["id"], 0))
        return items

    def full(self):
        return dto.build_board(self.documents("task", dto.TASK_INPUT_FIELDS),
                               self.documents("epic", dto.BOARD_EPIC_FIELDS),
                               self.documents("phase", dto.BOARD_PHASE_FIELDS))

    def changed(self, since):
        table, key, seq = ("entity_core", "public_id", "last_seq") if self.native else ("entities", "id", "updated_seq")
        return self.connection.execute(
            f"SELECT kind,{key},deleted FROM {table} WHERE {seq}>? AND kind IN ('task','epic','phase') "
            f"ORDER BY {seq},{key} LIMIT ?", (since, DELTA_MAX + 1)).fetchall()

    def delta(self, since):
        if self.flat:
            # A legacy inline index owns membership/fields independently of its
            # sidecars. Do not pretend one sidecar row is a complete task delta.
            return None, 'legacy_flat'
        changed = self.changed(since)
        if len(changed) > DELTA_MAX:
            return None, "too_many"
        if any(k == "epic" and deleted for k, _, deleted in changed):
            return None, "epic_removed"
        # An epic entering the board can make previously orphaned tasks visible
        # without changing those task rows. A full snapshot closes that boundary.
        if any(k == "epic" for k, _, _ in changed):
            return None, "epic_changed"
        epics = self.documents("epic", dto.BOARD_EPIC_FIELDS)
        ranks = {e["id"] for e in epics}
        upsert, remove = [], []
        for kind, ident, deleted in changed:
            if kind != "task":
                continue
            if deleted:
                remove.append(ident)
                continue
            if self.native:
                doc = self.native.get("task", ident, fields=dto.TASK_INPUT_FIELDS)["fields"]
            else:
                import json
                doc = json.loads(self.connection.execute(
                    "SELECT doc FROM entities WHERE kind='task' AND id=?", (ident,)).fetchone()[0])
            if doc.get("epic") not in ranks:
                remove.append(ident)
            else:
                upsert.append(dto.task_row(doc))
        result = {"tasks_upsert": upsert, "tasks_remove": remove}
        if any(k == "phase" for k, _, _ in changed):
            result["epics"] = [dto.select(e, dto.BOARD_EPIC_FIELDS) for e in epics]
            result["phases"] = dto.build_board([], [], self.documents("phase", dto.BOARD_PHASE_FIELDS))["phases"]
        return result, None


@contextmanager
def snapshot(database=None):
    from taskmaster import backlog_server as bs
    if database is not None:
        from taskmaster.native_routing.viewer import _open
        with _open(database) as call, call.read() as snap:
            seq = snap.connection.execute(
                "SELECT COALESCE(MAX(last_seq),0) FROM entity_core WHERE kind IN ('task','epic','phase')").fetchone()[0]
            yield BoardSnapshot(snap.connection, snap.identity["store_id"], seq, native=snap)
    else:
        legacy = bs._store()
        legacy.scan_for_read()
        if legacy._network_projection_only:
            data, identity, seq = legacy.load_dict_with_identity()
            yield BoardSnapshot(None, identity, seq, legacy=legacy, projection=data)
            return
        connection = legacy.connection
        owns = not connection.in_transaction
        if owns:
            connection.execute("BEGIN")
        try:
            identity = connection.execute("SELECT value FROM meta WHERE key='creation_token'").fetchone()[0]
            seq = connection.execute(
                "SELECT COALESCE(MAX(updated_seq),0) FROM entities WHERE kind IN ('task','epic','phase')").fetchone()[0]
            flat_seq = flat_sequence(connection)
            yield BoardSnapshot(connection, identity, max(seq, flat_seq or 0), legacy=legacy, flat=flat_seq is not None)
        finally:
            if owns:
                connection.rollback()


def response(database=None, *, since=None, if_none_match=None):
    from taskmaster import backlog_server as bs
    start = perf_counter()
    with snapshot(database) as snap:
        etag = dto.revision(snap.identity, snap.seq, bs.VERSION)
        timings = {"rev": (perf_counter() - start) * 1000, "build": 0}
        sequence, reason = dto.token_sequence(since, snap.identity, snap.seq, bs.VERSION) if since is not None else (None, None)
        if (since is None and dto.matches(if_none_match, etag)) or (since is not None and not reason and sequence == snap.seq):
            return 304, None, etag, timings
        start = perf_counter()
        result = None
        if since is not None and not reason:
            if snap.projection is not None:
                reason = "projection_only"
            else:
                result, reason = snap.delta(sequence)
        if result is None:
            result = snap.full()
            if reason:
                result["resync"] = reason
        else:
            result["since"] = since
        result.update(revision=etag, cursor=etag)
        timings["build"] = (perf_counter() - start) * 1000
        return 200, result, etag, timings
