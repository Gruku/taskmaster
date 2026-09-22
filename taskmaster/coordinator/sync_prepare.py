"""Observe/parse/merge outside the writer; produce a revision-fenced sync command.

The caller owns publication coordination and a read snapshot. It must recheck
the Observation before submitting this plan and again before reporting success.
This module does not submit commands, publish files, or fabricate merge bases.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
from pathlib import PurePosixPath

import yaml

from taskmaster import projection_parse
from taskmaster.native import projection, sync
from taskmaster.native.migrate import encode
from taskmaster.native.sync_merge import merge, protect_local
from . import sync_files


@dataclass(frozen=True)
class Prepared:
    file: str
    state: str
    reason: str
    observation: sync_files.Observation | None
    arguments: dict | None


def prepare(snapshot, backlog_dir, rel, *, take_file=False):
    connection = snapshot.connection
    kind, ident = projection_parse.classify(rel)
    observed = sync_files.observe(backlog_dir, rel)
    token = sync.manifest_token(connection, rel)
    cached = {}

    def entity(kind, ident):
        key = kind, ident
        if key not in cached:
            try:
                cached[key] = snapshot.get(*key, include_body=True, include_deleted=True)
            except KeyError:
                cached[key] = None
        return cached[key]

    def lookup(kind, ident):
        current = entity(kind, ident)
        return None if current is None else (current["fields"], current["body"])

    def result(mode, reason, rows=None):
        arguments = None if mode is None else {
            "file": rel, "mode": mode, "reason": reason[:4096], "rows": rows or [],
            "observed_base64": None if observed is None else base64.b64encode(observed.content).decode("ascii"),
            "observed_hash": None if observed is None else observed.digest, "expected_manifest": token}
        return Prepared(rel, mode or "unchanged", reason, observed, arguments)

    if observed is None:
        if take_file:
            raise ValueError(f"cannot take missing file: {rel}; use ordinary sync to repair it")
        return result("repair", "missing file; repair from database, not a domain deletion")
    record = connection.execute("SELECT content_hash,quarantined,quarantine_hash FROM projection WHERE file=?", (rel,)).fetchone()
    base_row = connection.execute("SELECT content FROM projection_base WHERE file=?", (rel,)).fetchone()
    base = None if base_row is None or base_row[0] is None else bytes(base_row[0])
    trusted = base is not None and record is not None and hashlib.sha1(base).hexdigest() == record[0]
    held = projection.held_file(connection, rel)
    if record is not None and observed.digest == record[0] and not held:
        return result(None if trusted else "observe", "file matches the trusted projection")
    if not take_file and record is not None and record[1] and observed.digest == record[2]:
        return Prepared(rel, "quarantined", "unchanged quarantined bytes; repair the file before retrying", observed, None)
    try:
        parsed = projection_parse.projected_file(kind, ident, observed.content, lookup)
        if parsed is None:
            raise ValueError("projection has no importable identity")
        # New files discovered in an archive have the same authored archived bit
        # as the legacy scanner. Existing paths keep their three-way semantics.
        if record is None and any(part in {"archive", "_archive"} for part in PurePosixPath(rel).parts[:-1]):
            for doc, _ in parsed.values():
                doc["archived"] = True
    except (ValueError, TypeError, AttributeError, yaml.YAMLError) as exc:
        return result("quarantine", f"invalid authored projection: {exc}")
    base_rows = None
    if trusted and not take_file:
        try:
            base_rows = projection_parse.projected_file(kind, ident, base, lookup)
        except (ValueError, TypeError, AttributeError, yaml.YAMLError):
            return result("conflict", "verified base bytes cannot be parsed; explicit resolution required")
    rows, overlaps = [], []
    for key, (fields, body) in parsed.items():
        current = entity(*key)
        if current is not None and current["deleted"]:
            return result("conflict", f"file names a deleted/reserved tombstone: {key[0]} {key[1]}")
        if key[0] not in {"backlog", "project"}:
            fields["id"] = key[1]
        fields = protect_local(key[0], fields, current["fields"] if current else None)
        theirs = fields, body
        if current is None:
            if connection.execute("SELECT 1 FROM id_reservations WHERE kind=? AND public_id=?", key).fetchone():
                return result("conflict", f"file names a reserved tombstone: {key[0]} {key[1]}")
            chosen = theirs
        else:
            ours = current["fields"], current["body"]
            if take_file or encode(ours) == encode(theirs):
                chosen = theirs
            elif base_rows is None or key not in base_rows:
                return result("conflict", f"no verified prior base for divergent {key[0]} {key[1]}; explicit resolution required")
            else:
                base_fields, base_body = base_rows[key]
                if key[0] not in {"backlog", "project"}:
                    base_fields["id"] = key[1]
                base_fields = protect_local(key[0], base_fields, current["fields"])
                chosen, conflicts = merge((base_fields, base_body), ours, theirs)
                overlaps.extend(f"{key[0]}:{key[1]}.{field}" for field in conflicts)
        # A large backlog index may contain hundreds of unchanged entities.
        # Only rows we will write need a full replacement/revision precondition;
        # leaving another row untouched cannot overwrite its concurrent edit.
        if current is None or encode(chosen) != encode((current["fields"], current["body"])):
            rows.append({"kind": key[0], "id": key[1], "revision": current["revision"] if current else 0,
                         "fields": chosen[0], "body": chosen[1]})
    if len(rows) > sync.MAX_ROWS:
        return Prepared(rel, "pending", f"more than {sync.MAX_ROWS} changed entities; bounded import refused", observed, None)
    if overlaps:
        return result("conflict", "overlapping edits retained in external history: " + ", ".join(overlaps), rows)
    return result("apply", "explicit file resolution" if take_file else "accepted authored file changes", rows)
