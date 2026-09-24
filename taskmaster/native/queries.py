"""Bounded native entity queries over one explicitly admitted read snapshot."""
import base64
from contextlib import contextmanager
import hashlib
import json

from . import blockers, claims, context as context_shape, cursors, schema
from .budget import Selection
from .db import verified_snapshot
from .migrate import encode, rows

DEFAULT_FIELDS = ("id", "title", "status", "priority", "epic", "phase")
MAX_PAGE = 500

# One continuation refusal for both paged reads. An entity page and a context page
# make the same promise — this cursor describes this question against this
# snapshot — so a caller that handles one handles the other.
CursorInvalid = context_shape.CursorInvalid


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

    def neighbourhood(self, kind, ident, *, limit=50):
        """Distinct `related` neighbours as `(kind, id, via, weight)`, ordered by identity.

        The limit is a page, not a relevance cut: nothing is ranked. Weight is the
        `related` weight summed over its rows (handover multiplicity included).
        """
        self._check()
        page_limit(limit)
        if self.connection.execute("SELECT 1 FROM entity_core WHERE kind=? AND public_id=? AND deleted=0", (kind, ident)).fetchone() is None:
            raise KeyError(f"{kind} {ident} not found")
        from .neighbourhood import neighbours
        found = sorted(neighbours(self.connection, kind, ident).items())
        return self._envelope(items=[{"kind": k, "id": i, "via": via, "weight": weight} for (k, i, via), weight in found[:limit]],
                              truncated=len(found) > limit)

    # The change feed. `domain_events.seq` is `INTEGER PRIMARY KEY AUTOINCREMENT`
    # allocated inside the command's own `BEGIN IMMEDIATE`, and SQLite admits one
    # writer at a time, so seq order is commit order and a reader can never see
    # seq N without every seq below it. That is what makes a bare sequence a
    # sound cursor; the fence it carries lives in `native.cursors`.
    _EVENT_COLUMNS = ("e.seq,e.ts,e.session,e.kind,e.id,e.op,e.fields,"
                      "COALESCE(c.operation,e.tool) operation,"
                      "COALESCE(c.first_seq,e.seq) first_seq,COALESCE(c.final_seq,e.seq) final_seq ")

    def changes_since(self, cursor="", *, kinds=None, ids=None, epic="", limit=100,
                      group_commits=True, since_seq=None):
        """What moved after this cursor, as whole commits in sequence order.

        Answers a resync instead of raising whenever the cursor can no longer be
        honoured, so a routine condition costs the caller one more call rather
        than an error branch in every skill. With neither a cursor nor
        `since_seq` the answer is a cursor at the current sequence and nothing
        else: "start watching from now".
        """
        self._check()
        scope = cursors.scope(kinds, ids, epic, group_commits)
        cursors.page(limit)
        if since_seq is not None and (type(since_seq) is not int or since_seq < 0):
            raise ValueError("since_seq must be a sequence number of 0 or more")
        if cursor and since_seq is not None:
            raise ValueError("pass a cursor or since_seq, not both")
        sequence = int(self.identity["event_high_water"])
        envelope = {"store_id": self.identity["store_id"], "source_digest": self.identity["source_digest"],
                    "sequence": sequence, "scope": scope, "group_commits": group_commits}
        floor = self.change_history_floor()
        try:
            if cursor:
                after = cursors.parse(cursor, store_id=envelope["store_id"], scope=scope,
                                      source_digest=envelope["source_digest"], sequence=sequence,
                                      floor=floor)
            elif since_seq is not None:
                # An explicit sequence takes its scope from this call, so it can
                # never smuggle a wider scope in the way a fabricated cursor could.
                after = min(since_seq, sequence)
                if after < floor:
                    raise cursors.HistoryExpired("change history before this sequence is no longer retained")
            else:
                return cursors.feed(items=[], last_seq=cursors.resume_point(sequence, floor), more=False,
                                    **envelope)
        except cursors.CursorInvalid as exc:
            return cursors.resync(exc, floor=floor, **envelope)
        conditions, args = self._change_scope(scope, after)
        source = "FROM domain_events e LEFT JOIN command_commits c USING(commit_key) WHERE " + " AND ".join(conditions)
        if not group_commits:
            events = rows(self.connection, "SELECT " + self._EVENT_COLUMNS + source + " ORDER BY e.seq LIMIT ?",
                          args + [limit + 1])
            more, events = len(events) > limit, events[:limit]
            items = [cursors.flat(e) for e in events]
            return cursors.feed(items=items, last_seq=events[-1]["seq"] if events else after,
                                more=more, **envelope)
        # A commit is never split across pages: the page is chosen by commit
        # extent, and the continuation advances to the last kept commit's
        # `final_seq`, so an out-of-scope event inside it cannot re-report it.
        groups = rows(self.connection, "SELECT DISTINCT COALESCE(c.first_seq,e.seq) first_seq,"
                      "COALESCE(c.final_seq,e.seq) final_seq " + source + " ORDER BY first_seq LIMIT ?",
                      args + [limit + 1])
        more, groups = len(groups) > limit, groups[:limit]
        if not groups:
            return cursors.feed(items=[], last_seq=after, more=False, **envelope)
        events = rows(self.connection, "SELECT " + self._EVENT_COLUMNS + source +
                      " AND COALESCE(c.first_seq,e.seq)<=? ORDER BY e.seq", args + [groups[-1]["first_seq"]])
        commits = []
        for event in events:
            if not commits or commits[-1]["first_seq"] != event["first_seq"]:
                commits.append(cursors.commit(event, []))
            commits[-1]["changes"].append(cursors.change(event))
        return cursors.feed(items=commits, last_seq=max(g["final_seq"] for g in groups),
                            more=more, **envelope)

    def change_history_floor(self):
        """The oldest sequence a cursor may still resume from.

        Read on every call, never cached: an operator raising the floor must take
        effect on the next question, not on the next process. Nothing in the
        shipped code writes it — no pruner exists (D4) — so it is the contract a
        future pruner honours and the only way expiry is exercised today.

        An unreadable value refuses the call. Defaulting to zero would let a
        corrupt floor admit history the store can no longer vouch for, and a
        replayed change is one an agent acts on twice.
        """
        row = self.connection.execute("SELECT value_json FROM sync_state WHERE key=?", (cursors.FLOOR_KEY,)).fetchone()
        if row is None:
            return 0
        value = json.loads(row[0])
        if type(value) is not int or value < 0:
            raise ValueError(f"{cursors.FLOOR_KEY} is not a sequence number")
        return value

    def _change_scope(self, scope, after):
        """The scope filters as SQL. `epic` is membership *at the time of the event*
        (`cursors.epic_condition`), so a change made while a task was in the epic
        stays reported after it leaves, and the move that took it out is reported.
        """
        _label, kinds, ids, epic, _grouped = scope
        conditions, args = ["e.seq>?"], [after]
        for column, values in (("kind", kinds), ("id", ids)):
            if values:
                conditions.append(f"e.{column} IN ({','.join('?' for _ in values)})")
                args.extend(values)
        if epic:
            conditions.append(cursors.epic_condition(
                "domain_events", "e", "SELECT json_extract(t.epic_json,'$') FROM entity_core c2 "
                "JOIN task_operational t USING(entity_key) WHERE c2.kind='task' AND c2.public_id=e.id"))
            args.extend([epic] * 4)
        return conditions, args

    def sql(self, statement, *, limit=500, timeout=None):
        """Explicit full compatibility snapshot, with materialization counters."""
        self._check()
        from .compatibility import query
        return self._envelope(**query(self.connection, statement, limit=limit, timeout=timeout))

    def document(self, kind, ident, *, sections=None):
        self._check()
        from .documents import retrieve
        return retrieve(self, kind, ident, sections=sections)

    # ── Bounded agent context ───────────────────────────────────────────────
    # One call that answers "what do I need to know to work on X". The blocker
    # set and the byte budget are `native.blockers` and `native.budget`; what
    # lives here is only the store access that feeds them, so the legacy path
    # can feed the same two with its own rows and answer the same shape.

    def _page(self, base, args, offset, limit):
        """One bounded page of a section, and the exact count behind it.

        The count is a `COUNT(*)` over the page's own predicate in this snapshot,
        never the length of the page: a section read with a limit would otherwise
        report "nothing omitted" precisely when it omitted the most.
        """
        total = self.connection.execute(f"SELECT COUNT(*) FROM ({base})", args).fetchone()[0]
        return rows(self.connection, base + " LIMIT ? OFFSET ?", list(args) + [limit, offset]), int(total)

    _OPEN_HANDOVERS = (
        "SELECT c.public_id id, json_extract(x.value_json,'$') next_action, "
        "json_extract(h.date_json,'$') date FROM entity_core c {join}"
        "LEFT JOIN entity_extensions x ON x.entity_key=c.entity_key AND x.field='next_action' "
        "LEFT JOIN handover_operational h ON h.entity_key=c.entity_key "
        "WHERE {where} GROUP BY c.public_id ORDER BY c.public_id")

    def open_handovers(self, task_id="", *, blocking_only=False, offset=0, limit=None):
        """Open handovers, optionally only those naming one task.

        `blocking_only` narrows to the ones carrying a `next_action`, which is what
        makes a handover mandatory context. Narrowing in SQL rather than after a
        page is the point: a task with fifty silent handovers and one that asks for
        an action must not have the asking one paged out of its own blocker list.
        """
        where = ["c.kind='handover'", "c.deleted=0", "c.archived=0",
                 "json_extract(c.status_json,'$')='open'"]
        join, args = "", []
        if task_id:
            join = "JOIN memberships m ON m.entity_key=c.entity_key AND m.field='task_ids' "
            where.append("json_extract(m.value_json,'$')=?")
            args.append(task_id)
        if blocking_only:
            where.append("COALESCE(TRIM(json_extract(x.value_json,'$')),'')<>''")
        base = self._OPEN_HANDOVERS.format(join=join, where=" AND ".join(where))
        return self._page(base, args, offset, context_shape.PAGE if limit is None else limit)

    def _focus_task(self, focus, scope, session):
        """The task this question is about: the one asked for, or the one this
        session holds. A project-scoped question never borrows a session's focus."""
        if focus:
            return focus
        if scope == "project" or not session:
            return ""
        row = self.connection.execute(
            "SELECT c.public_id FROM entity_core c JOIN task_operational t USING(entity_key) "
            "WHERE c.kind='task' AND c.deleted=0 AND c.archived=0 "
            "AND json_extract(t.locked_by_json,'$')=? "
            "AND json_extract(c.status_json,'$') IN ('in-progress','in-review') "
            "ORDER BY c.public_id LIMIT 1", (session,)).fetchone()
        return row[0] if row else ""

    def _dependency_statuses(self, declared):
        if isinstance(declared, blockers.Unknown):
            return {}
        idents = sorted(set(declared))
        if not idents:
            return {}
        placeholders = ",".join("?" for _ in idents)
        found = self.connection.execute(
            "SELECT public_id,json_extract(status_json,'$') FROM entity_core "
            f"WHERE kind='task' AND deleted=0 AND public_id IN ({placeholders})", idents)
        return {ident: status or "unknown" for ident, status in found}

    def _bug_rows(self, focus):
        """Open bugs filed against this task, with the severity the resolver reads.

        `_bugs_found_in` is the one comparison — it refuses a `found_in` shape it
        cannot compare rather than dropping the row — so this goes through it and
        only then reads severities for the ids it named.
        """
        from .workflow import _bugs_found_in
        open_ids, _fixed = _bugs_found_in(self.connection, focus)
        if not open_ids:
            return []
        placeholders = ",".join("?" for _ in open_ids)
        return rows(self.connection,
                    "SELECT c.public_id id,'open' status,json_extract(b.severity_json,'$') severity "
                    "FROM entity_core c LEFT JOIN bug_operational b USING(entity_key) "
                    f"WHERE c.kind='bug' AND c.public_id IN ({placeholders}) ORDER BY c.public_id",
                    open_ids)

    def _claim(self, task, focus, session):
        holder = task.get("locked_by")
        if holder not in (None, "", False) and not isinstance(holder, str):
            raise ValueError(f"locked_by is a {type(holder).__name__}, not a session name")
        return claims.read(task, task_id=focus, session=session,
                           connection=self.connection).as_blocker()

    def _context_facts(self, focus, session):
        """Every mandatory producer, each wrapped so a refusal becomes a blocker."""
        if not focus:
            return None, None
        try:
            entity = self.get("task", focus, include_body=True)
        except KeyError:
            return None, blockers.Facts(task=blockers.Unknown("not_found", focus), dependencies={},
                                        bugs=[], handovers=[], claim=None, session=session)
        task = entity["fields"]
        declared = blockers.declared_dependencies(task)
        return entity, blockers.Facts(
            task=task,
            dependencies=blockers.probe(lambda: self._dependency_statuses(declared)),
            bugs=blockers.probe(lambda: self._bug_rows(focus)),
            handovers=blockers.probe(lambda: self.open_handovers(focus, blocking_only=True)[0]),
            claim=blockers.probe(lambda: self._claim(task, focus, session)),
            session=session)

    def _context_section(self, name, focus, entity, facts, offset):
        """One selected section as `(items, total, provenance)`.

        A section that describes a task is empty when there is no task, rather
        than silently widened to the project: answering a different question is
        worse than answering none.
        """
        if name in context_shape.FOCUS_SECTIONS and entity is None:
            return [], 0, {}
        limit, fields = context_shape.PAGE, (entity or {}).get("fields", {})
        if name in context_shape.DOCUMENT_SECTIONS:
            document = self.document("task", focus, sections=[name])
            provenance = document["provenance"].get(name, {})
            text = document["sections"].get(name)
            declared = bool((fields.get("docs") or {}).get(name)) or text is not None
            items = [{"section": name, "text": text}] if text is not None else []
            return items[offset:offset + limit], int(declared), {
                "imported": bool(provenance), **provenance,
                "unresolved": [u["reason"] for u in document["unresolved"] if u["section"] == name]}
        if name == "body":
            body = (entity or {}).get("body") or ""
            return ([{"text": body}] if body else [])[offset:offset + limit], int(bool(body)), {}
        if name == "links":
            declared = fields.get("links") or []
            declared = declared if isinstance(declared, list) else [declared]
            return declared[offset:offset + limit], len(declared), {}
        if name == "dependencies":
            declared = blockers.declared_dependencies(fields)
            if isinstance(declared, blockers.Unknown):
                return [], 0, {"unreadable": declared.reason}
            idents = sorted(set(declared))
            statuses = facts.dependencies if isinstance(facts.dependencies, dict) else {}
            page = idents[offset:offset + limit]
            titles = self._titles("task", page)
            return ([{"id": i, "status": statuses.get(i, "missing"), "title": titles.get(i)}
                     for i in page], len(idents), {})
        if name == "bugs":
            if focus:
                found = facts.bugs if isinstance(facts.bugs, list) else []
                return found[offset:offset + limit], len(found), {}
            base = ("SELECT c.public_id id,'open' status,json_extract(b.severity_json,'$') severity "
                    "FROM entity_core c LEFT JOIN bug_operational b USING(entity_key) "
                    "WHERE c.kind='bug' AND c.deleted=0 AND c.archived=0 "
                    "AND json_extract(c.status_json,'$')='open' ORDER BY c.public_id")
            return (*self._page(base, [], offset, limit), {})
        if name == "handovers":
            page, total = self.open_handovers(focus, offset=offset, limit=limit)
            return [context_shape.handover_row(row) for row in page], total, {}
        if name == "issues":
            base = ("SELECT c.public_id id,json_extract(c.title_json,'$') title,"
                    "json_extract(i.severity_json,'$') severity FROM entity_core c "
                    "LEFT JOIN issue_operational i USING(entity_key) WHERE c.kind='issue' "
                    "AND c.deleted=0 AND c.archived=0 AND json_extract(c.status_json,'$')='open' "
                    "ORDER BY c.public_id")
            return (*self._page(base, [], offset, limit), {})
        if name == "notes":
            # The whole desk, in `backlog_note list` order: pinned first, then
            # created newest first with the numeric id as the tiebreak.
            base = ("SELECT c.public_id id,COALESCE(d.body,'') text,"
                    "CASE WHEN json_extract(n.pinned_json,'$') THEN 1 ELSE 0 END pinned FROM entity_core c "
                    "JOIN note_operational n USING(entity_key) "
                    "LEFT JOIN entity_documents d ON d.entity_key=c.entity_key "
                    "LEFT JOIN entity_extensions x ON x.entity_key=c.entity_key AND x.field='created' "
                    "WHERE c.kind='note' AND c.deleted=0 AND c.archived=0 "
                    "ORDER BY pinned DESC, COALESCE(json_extract(x.value_json,'$'),'') DESC, "
                    "CAST(substr(c.public_id, instr(c.public_id,'-')+1) AS INTEGER) DESC")
            found, total = self._page(base, [], offset, limit)
            return ([{"id": row["id"], "text": row["text"].rstrip(chr(10)),
                      **({"pinned": True} if row["pinned"] else {})} for row in found], total, {})
        if name == "siblings":
            epic = fields.get("epic")
            if not isinstance(epic, str) or not epic:
                return [], 0, {"reason": "task has no epic"}
            base = ("SELECT c.public_id id,json_extract(c.title_json,'$') title,"
                    "json_extract(c.status_json,'$') status FROM entity_core c "
                    "JOIN task_operational t USING(entity_key) WHERE c.kind='task' "
                    "AND c.deleted=0 AND c.archived=0 AND json_extract(t.epic_json,'$')=? "
                    "AND c.public_id<>? ORDER BY c.public_id")
            return (*self._page(base, [epic, focus], offset, limit), {})
        # `recent`: a top-N question, so the cap is the total. "15 of the 20 most
        # recent were omitted" is actionable; "of 3,412 entities" is not.
        base = ("SELECT kind,id,title,status,last_seq FROM ("
                "SELECT c.kind kind,c.public_id id,json_extract(c.title_json,'$') title,"
                "json_extract(c.status_json,'$') status,c.last_seq last_seq FROM entity_core c "
                "WHERE c.deleted=0 ORDER BY c.last_seq DESC,c.entity_key DESC LIMIT "
                f"{context_shape.RECENT})")
        return (*self._page(base, [], offset, limit), {})

    def _titles(self, kind, idents):
        if not idents:
            return {}
        placeholders = ",".join("?" for _ in idents)
        return {i: t for i, t in self.connection.execute(
            "SELECT public_id,json_extract(title_json,'$') FROM entity_core "
            f"WHERE kind=? AND public_id IN ({placeholders})", [kind] + list(idents))}

    def context(self, focus="", *, scope="session", budget_bytes=8000, include=None,
                cursor="", session=""):
        """What an agent needs to know to work on `focus`, bounded by bytes.

        Mandatory context — what blocks this task — is always complete; only the
        selected sections are budgeted, and what they left out is an exact count
        (D1). With no task in focus nothing was checked, so the answer says so as
        an `unknown` blocker rather than reporting `clear`.
        """
        self._check()
        context_shape.check_scope(scope)
        sections = context_shape.check_include(include, scope)
        context_shape.check_budget(budget_bytes)
        focus = self._focus_task(focus, scope, session)
        sequence = int(self.identity["event_high_water"])
        ident = context_shape.identity(store_id=self.identity["store_id"],
                                       source_digest=self.identity["source_digest"],
                                       sequence=sequence, scope=scope, focus=focus,
                                       include=sections)
        offsets = context_shape.parse(cursor, ident)
        entity, facts = self._context_facts(focus, session)
        resolution = (blockers.resolve(facts) if facts is not None
                      else blockers.Resolution(clear=False, blockers=(context_shape.no_focus(),)))
        selections, provenance = [], {}
        for name in sections:
            offset = offsets.get(name, 0)
            items, total, extra = self._context_section(name, focus, entity, facts, offset)
            selections.append(Selection(name, items, total))
            provenance[name] = {"query": context_shape.source(name, focus), **extra}
        return context_shape.assemble(
            store_id=self.identity["store_id"], sequence=sequence, scope=scope, focus=focus,
            resolution=resolution, selections=selections, offsets=offsets, ident=ident,
            budget_bytes=budget_bytes, provenance=provenance)
