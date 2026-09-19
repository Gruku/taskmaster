"""Bounded native entity queries over one explicitly admitted read snapshot."""
import base64
from contextlib import contextmanager
import hashlib
import json

from . import schema
from .db import verified_snapshot
from .migrate import encode, rows

DEFAULT_FIELDS = ("id", "title", "status", "priority", "epic", "phase")
MAX_PAGE = 500


class CursorInvalid(ValueError):
    """Start a new snapshot; this continuation no longer describes this scope."""


def page_limit(limit):
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise ValueError(f"limit must be an integer from 1 to {MAX_PAGE}")
    return limit


def _fields(fields):
    if fields is None:
        return None
    if not isinstance(fields, (list, tuple)) or not all(isinstance(f, str) and len(f) <= 256 for f in fields):
        raise ValueError("fields must be a list of field names")
    if len(fields) > 100:
        raise ValueError("at most 100 fields may be selected")
    return tuple(dict.fromkeys(fields))


def _core_columns(fields, prefix=""):
    names = ["entity_key", "kind", "public_id", "revision", "last_seq", "archived", "deleted"]
    names += [f + "_json" for f in schema.CORE_FIELDS if fields is None or f in fields]
    return ",".join(f'{prefix}"{name}"' for name in names)


class Repository:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def snapshot(self):
        with verified_snapshot(self.connection) as identity:
            query = Snapshot(self.connection, identity)
            try:
                yield query
            finally:
                query.active = False


class Snapshot:
    def __init__(self, connection, identity):
        self.connection, self.identity, self.active = connection, identity, True

    def _check(self):
        if not self.active or not self.connection.in_transaction:
            raise RuntimeError("query requires an active repository snapshot")

    def _envelope(self, **data):
        return {"store_id": self.identity["store_id"], "sequence": int(self.identity["event_high_water"]), **data}

    def _assemble(self, cores, fields, include_body=False):
        self._check()
        fields = _fields(fields)
        if not cores:
            return []
        keys = [c["entity_key"] for c in cores]
        placeholders = ",".join("?" for _ in keys)
        result = {c["entity_key"]: {"kind": c["kind"], "id": c["public_id"], "revision": c["revision"],
                  "last_seq": c["last_seq"], "archived": bool(c["archived"]), "deleted": bool(c["deleted"]),
                  "fields": {f: json.loads(c[f + "_json"]) for f in schema.CORE_FIELDS
                             if (fields is None or f in fields) and c[f + "_json"] is not None}} for c in cores}
        kinds = {c["kind"] for c in cores}
        for table, owned in schema.OPERATIONS.items():
            wanted = [f for f in owned if fields is None or f in fields]
            if not wanted or not any(table in schema.KINDS[k] for k in kinds):
                continue
            columns = ",".join(f'"{f}_json"' for f in wanted)
            for row in rows(self.connection, f"SELECT entity_key,{columns} FROM {table} WHERE entity_key IN ({placeholders})", keys):
                result[row["entity_key"]]["fields"].update({f: json.loads(row[f + "_json"]) for f in wanted if row[f + "_json"] is not None})
        for table in ("entity_extensions", "configuration"):
            wanted = None if fields is None else [f for f in fields if any(schema.owner(k, f) == table for k in kinds)]
            if wanted == []:
                continue
            suffix = "" if wanted is None else f" AND field IN ({','.join('?' for _ in wanted)})"
            for key, field, value in self.connection.execute(f"SELECT entity_key,field,value_json FROM {table} WHERE entity_key IN ({placeholders}){suffix}", keys + (wanted or [])):
                result[key]["fields"][field] = json.loads(value)
        if include_body or fields is None or any(f in fields for f in ("body", "_body")):
            columns = ["entity_key"] + (["body"] if include_body else [])
            columns += [f + "_json" for f in ("body", "_body") if fields is None or f in fields]
            for row in rows(self.connection, f"SELECT {','.join(columns)} FROM entity_documents WHERE entity_key IN ({placeholders})", keys):
                target = result[row["entity_key"]]
                if include_body:
                    target["body"] = row["body"]
                for f in ("body", "_body"):
                    if row.get(f + "_json") is not None:
                        target["fields"][f] = json.loads(row[f + "_json"])
        for table in schema.RELATIONS:
            wanted = [f for k in kinds for groups in (schema.KINDS[k], schema.COMMON)
                      for f in groups.get(table, "").split() if fields is None or f in fields]
            wanted = sorted(set(wanted))
            if not wanted:
                continue
            where = f"s.entity_key IN ({placeholders}) AND s.field IN ({','.join('?' for _ in wanted)})"
            relation_rows = rows(self.connection,
                f"SELECT s.entity_key,s.field,s.shape,r.ordinal,r.value_json,p.path FROM field_shapes s "
                f"LEFT JOIN {table} r ON r.entity_key=s.entity_key AND r.field=s.field "
                f"LEFT JOIN paths p ON p.path_key=r.path_key WHERE {where} ORDER BY s.entity_key,s.field,r.ordinal", keys + wanted)
            for row in relation_rows:
                target = result[row["entity_key"]]["fields"]
                field = row["field"]
                if row["shape"] == "list":
                    target.setdefault(field, [])
                if row["ordinal"] is not None:
                    value = json.loads(row["value_json"]) if row["value_json"] is not None else row["path"]
                    if row["shape"] == "list":
                        target[field].append(value)
                    else:
                        target[field] = value
        # Field order is observable — frontmatter and several tool answers render
        # a document in its dict order — and the legacy store hands every document
        # out with sorted keys (its JSON is written with sort_keys). Nested values
        # are already sorted by `encode`, so only the top level needs ordering.
        for entity in result.values():
            entity["fields"] = dict(sorted(entity["fields"].items()))
        return [result[key] for key in keys]

    def get(self, kind, ident, *, fields=None, include_body=False, include_deleted=False):
        self._check()
        fields = _fields(fields)
        if kind not in schema.KINDS:
            raise ValueError(f"unknown entity kind: {kind}")
        core = rows(self.connection, "SELECT " + _core_columns(fields) + " FROM entity_core WHERE kind=? AND public_id=?" +
                    ("" if include_deleted else " AND deleted=0"), (kind, ident))
        if not core:
            raise KeyError(f"{kind} {ident} not found")
        return self._assemble(core, fields, include_body)[0]

    def _where(self, kind, status, epic, phase, include_archived, include_deleted):
        conditions, args = [], []
        if kind is not None:
            if kind not in schema.KINDS:
                raise ValueError(f"unknown entity kind: {kind}")
            conditions.append("c.kind=?")
            args.append(kind)
        if not include_archived:
            conditions.append("c.archived=0")
        if not include_deleted:
            conditions.append("c.deleted=0")
        if status is not None:
            conditions.append("json_extract(c.status_json,'$')=?")
            args.append(status)
        for field, value in (("epic", epic), ("phase", phase)):
            if value is not None:
                conditions.append(f"c.entity_key IN (SELECT entity_key FROM task_operational WHERE json_extract({field}_json,'$')=?)")
                args.append(value)
        return conditions or ["1"], args

    def _continuation(self, cursor, scope):
        fingerprint = hashlib.sha256(encode(scope).encode()).hexdigest()
        identity = [self.identity["store_id"], self.identity["event_high_water"], self.identity["source_digest"], fingerprint]
        if cursor is None:
            return identity, None
        try:
            if not isinstance(cursor, str) or len(cursor) > 4096:
                raise ValueError()
            value = json.loads(base64.b64decode(cursor.encode(), altchars=b"-_", validate=True))
            if value["identity"] != identity or not isinstance(value["after"], list) or len(value["after"]) != 2 or not all(isinstance(v, str) for v in value["after"]):
                raise ValueError()
            return identity, value["after"]
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise CursorInvalid("cursor scope or snapshot changed; start a new page") from None

    def list(self, kind=None, *, status=None, epic=None, phase=None, fields=DEFAULT_FIELDS,
             include_archived=False, include_deleted=False, limit=50, cursor=None):
        self._check()
        page_limit(limit)
        fields = _fields(fields)
        identity, after = self._continuation(cursor, ["entities", kind, status, epic, phase, fields, include_archived, include_deleted])
        conditions, args = self._where(kind, status, epic, phase, include_archived, include_deleted)
        if after is not None:
            conditions.append("(c.kind,c.public_id)>(?,?)")
            args.extend(after)
        cores = rows(self.connection, "SELECT " + _core_columns(fields, "c.") + " FROM entity_core c WHERE " + " AND ".join(conditions) + " ORDER BY c.kind,c.public_id LIMIT ?", args + [limit + 1])
        more, cores = len(cores) > limit, cores[:limit]
        continuation = None
        if more:
            continuation = base64.urlsafe_b64encode(encode({"identity": identity, "after": [cores[-1]["kind"], cores[-1]["public_id"]]}).encode()).decode()
        return self._envelope(items=self._assemble(cores, fields), cursor=continuation)

    def summary(self, kind=None, *, status=None, epic=None, phase=None, include_archived=False, include_deleted=False):
        self._check()
        conditions, args = self._where(kind, status, epic, phase, include_archived, include_deleted)
        values = rows(self.connection, "SELECT c.kind,json_extract(c.status_json,'$') status,COUNT(*) count "
                      "FROM entity_core c WHERE " + " AND ".join(conditions) + " GROUP BY c.kind,c.status_json ORDER BY c.kind,c.status_json", args)
        return self._envelope(groups=values, total=sum(v["count"] for v in values))

    def search(self, text, *, kind=None, limit=50, include_archived=True):
        self._check()
        from .search import search
        return self._envelope(items=search(self.connection, text, kind=kind, limit=limit, include_archived=include_archived))

    def relations(self, kind, ident, *, field, limit=50):
        self._check()
        page_limit(limit)
        table = schema.owner(kind, field)
        if table not in schema.RELATIONS:
            raise ValueError(f"{field} is not a relation field")
        core = self.connection.execute("SELECT entity_key FROM entity_core WHERE kind=? AND public_id=? AND deleted=0", (kind, ident)).fetchone()
        if core is None:
            raise KeyError(f"{kind} {ident} not found")
        values = rows(self.connection, f"SELECT r.ordinal,r.value_json,p.path,r.target_kind,r.target_id,r.target_key FROM {table} r "
                      "LEFT JOIN paths p USING(path_key) WHERE r.entity_key=? AND r.field=? ORDER BY r.ordinal LIMIT ?", (core[0], field, limit + 1))
        return self._envelope(items=[{"ordinal": v["ordinal"], "value": json.loads(v["value_json"]) if v["value_json"] is not None else v["path"],
                                     "target_kind": v["target_kind"], "target_id": v["target_id"], "resolved": v["target_key"] is not None} for v in values[:limit]],
                              truncated=len(values) > limit)

    def references_to(self, kind, ident, *, limit=50):
        self._check()
        page_limit(limit)
        parts = [f"SELECT c.kind,c.public_id id,r.field,r.ordinal FROM {table} r JOIN entity_core c USING(entity_key) "
                 "WHERE r.target_kind=? AND r.target_id=? AND c.deleted=0" for table in schema.RELATIONS]
        values = rows(self.connection, " UNION ALL ".join(parts) + " ORDER BY kind,id,field,ordinal LIMIT ?", [kind, ident] * len(parts) + [limit + 1])
        return self._envelope(items=values[:limit], truncated=len(values) > limit)

    def sql(self, statement, *, limit=500, timeout=None):
        """Explicit full compatibility snapshot, with materialization counters."""
        self._check()
        from .compatibility import query
        return self._envelope(**query(self.connection, statement, limit=limit, timeout=timeout))

    def document(self, kind, ident, *, sections=None):
        self._check()
        from .documents import retrieve
        return retrieve(self, kind, ident, sections=sections)
