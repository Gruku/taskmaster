"""Exact, deliberately unoptimized comparison helpers for native migration tests."""
from collections import Counter
import hashlib
import json


def canonical_rows(connection):
    """Keep entity identity, bodies, unknown fields, tombstones and duplicate edges.

    Exclude FTS rowids and physical ordering, which are implementation details.
    Call outside benchmark timing, from an explicitly chosen read snapshot.
    """
    entities = []
    for row in connection.execute(
        "SELECT kind,id,epic,status,archived,deleted,doc,body,rev,updated_seq FROM entities"
    ):
        values = list(row)
        values[6] = json.dumps(json.loads(values[6]), sort_keys=True, ensure_ascii=False)
        entities.append(tuple(values))
    result = {"entities": Counter(entities)}
    for table in ("related", "entity_paths", "links", "handover_tasks"):
        result[table] = Counter(tuple(row) for row in connection.execute(f"SELECT * FROM {table}"))
    result["fts"] = Counter(tuple(row) for row in connection.execute(
        "SELECT kind,id,title,body FROM entity_fts"))
    return result


def projection_hashes(root):
    """Exact portable bytes, excluding runtime locks, DB, caches and heartbeats."""
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()
            and path.relative_to(root).parts[0] != "local"}
