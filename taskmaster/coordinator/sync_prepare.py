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
from taskmaster.native import checkouts, metrics, projection, sync
from taskmaster.native.migrate import encode
from taskmaster.native.sync_merge import merge, protect_local, unapplied
from . import checkouts as checkouts_view, sync_files


@dataclass(frozen=True)
class Prepared:
    file: str
    state: str
    reason: str
    observation: sync_files.Observation | None
    arguments: dict | None


def prepare(snapshot, backlog_dir, rel, *, take_file=False, checkout=None, scan=None):
    """`checkout` names a linked checkout: its own base, holds and token replace the
    main checkout's manifest, and a linked file is never judged by main's bytes."""
    connection = snapshot.connection
    kind, ident = projection_parse.classify(rel)
    # A sync's scan records the fingerprint of what it read (same checks as observe).
    observed = sync_files.observe(backlog_dir, rel) if scan is None else scan.observe(rel)
    token = sync.manifest_token(connection, rel) if checkout is None else checkouts.token(connection, checkout, rel)
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
        if arguments is not None and checkout is not None:
            arguments["checkout"] = checkout
        return Prepared(rel, mode or "unchanged", reason, observed, arguments)

    if observed is None:
        if take_file:
            raise ValueError(f"cannot take missing file: {rel}; use ordinary sync to repair it")
        if checkout is not None:
            return Prepared(rel, "unchanged", "missing in the linked checkout; publication writes it", None, None)
        return result("repair", "missing file; repair from database, not a domain deletion")
    if checkout is None:
        record = connection.execute("SELECT content_hash,quarantined,quarantine_hash FROM projection WHERE file=?", (rel,)).fetchone()
        base_row = connection.execute("SELECT content FROM projection_base WHERE file=?", (rel,)).fetchone()
        base = None if base_row is None or base_row[0] is None else bytes(base_row[0])
        trusted = base is not None and record is not None and hashlib.sha1(base).hexdigest() == record[0]
        held = projection.held_file(connection, rel)
    else:
        found, hold = checkouts.base(connection, checkout, rel), checkouts.hold(connection, checkout, rel)
        base = None if found is None else found[1]
        record = None if found is None and hold is None else (
            None if found is None else found[0], hold is not None and hold[0] == "quarantined",
            None if hold is None else hold[1])
        trusted = found is not None
        held = None if hold is None else hold[0]
        if found is None and not take_file:
            published = connection.execute("SELECT content_hash FROM projection WHERE file=?", (rel,)).fetchone()
            content = observed.content
            reference = checkouts_view.main_base(connection, rel) if published is not None else None
            if published is not None and (published[0] in {
                    projection._digest(content), projection._digest(projection._lf(content)),
                    projection._digest(projection._crlf(content))} or (
                    reference is not None and projection._lf(reference) == projection._lf(content))):
                # Identical bytes may establish this checkout's base; nothing to import.
                return Prepared(rel, "establish", "file equals the published generation", observed, None)
    if record is not None and observed.digest == record[0] and not held:
        return result(None if trusted else "observe", "file matches the trusted projection")
    if (not take_file and not held and record is not None and base is not None and trusted
            and projection._lf(observed.content) == projection._lf(base)):
        # D7: Git's eol conversion rewrote only the base's line endings. Equal text is the
        # base, never drift or an import: main records these bytes as the published ones;
        # a linked checkout's publication records them as its new base.
        if checkout is not None:
            return Prepared(rel, "establish", "file equals this checkout's base up to line endings", observed, None)
        return result("observe", "file equals the published bytes up to line endings")
    # Unchanged quarantined bytes are parsed again: a parser fix (D2: prose that
    # merely resembles a conflict marker) must make them eligible without a repair.
    # Only a still-failing parse is skipped without recording a new quarantine.
    unchanged_quarantine = not take_file and record is not None and record[1] and observed.digest == record[2]
    still_quarantined = Prepared(rel, "quarantined", "unchanged quarantined bytes; repair the file before retrying",
                                 observed, None)
    if metrics.ENABLED:
        metrics.add("files_parsed")
    try:
        authored = projection_parse.authored_rows(kind, ident, observed.content)
        parsed = projection_parse.owned_rows(kind, authored, lookup)
        if parsed is None:
            raise ValueError("projection has no importable identity")
        # New files discovered in an archive have the same authored archived bit
        # as the legacy scanner. Existing paths keep their three-way semantics.
        if record is None and any(part in {"archive", "_archive"} for part in PurePosixPath(rel).parts[:-1]):
            for doc, _ in parsed.values():
                doc["archived"] = True
    except (ValueError, TypeError, AttributeError, yaml.YAMLError) as exc:
        if take_file:
            # An explicit resolution keeps the last good state rather than
            # recording a quarantine the caller did not ask for.
            raise ValueError(f"{rel} cannot be taken: it does not parse ({exc})") from None
        if unchanged_quarantine:
            return still_quarantined
        return result("quarantine", f"invalid authored projection: {exc}")
    # Tasks are owned by their task documents. A legacy inline `epics[].tasks`
    # entry is a stub, never a whole row: importing it would replace the task.
    inline = sorted(ident for kind_, ident in parsed if kind_ == "task") if kind == "backlog" else []
    if inline:
        named = ", ".join(inline[:5]) + (f" and {len(inline) - 5} more" if len(inline) > 5 else "")
        reason = (f"backlog.yaml names task rows under epics[].tasks ({named}); tasks are owned by "
                  f"their task documents, so remove the inline tasks and edit tasks/<id>.md instead")
        if take_file:
            raise ValueError(f"{rel} cannot be taken: {reason}")
        if unchanged_quarantine:
            return still_quarantined
        return result("quarantine", f"invalid authored projection: {reason}")
    base_rows = authored_base = None
    if trusted:
        try:
            authored_base = projection_parse.authored_rows(kind, ident, base)
            if not take_file:
                base_rows = projection_parse.owned_rows(kind, authored_base, lookup)
        except (ValueError, TypeError, AttributeError, yaml.YAMLError):
            if not take_file:
                return result("conflict", "verified base bytes cannot be parsed; explicit resolution required")
            authored_base = None
    rows, overlaps, dropped = [], [], []
    if kind == "backlog" and authored_base is not None:
        dropped.extend(f"{key[0]}:{key[1]} (removed; absence is not a deletion, archive it with the tools)"
                       for key in sorted(set(authored_base) - set(authored)) if key[0] in {"epic", "phase"})
    for key, (fields, body) in parsed.items():
        current = entity(*key)
        if current is not None and current["deleted"]:
            if take_file:
                raise ValueError(f"{rel} cannot be taken: it names a deleted/reserved tombstone: {key[0]} {key[1]}")
            return result("conflict", f"file names a deleted/reserved tombstone: {key[0]} {key[1]}")
        if key[0] not in {"backlog", "project"}:
            fields["id"] = key[1]
        fields = protect_local(key[0], fields, current["fields"] if current else None)
        # Compare like with like: a stored body may keep the trailing newline the
        # parser strips from both the base and the file (D4).
        theirs = fields, projection_parse.normal_body(body)
        if current is None:
            if connection.execute("SELECT 1 FROM id_reservations WHERE kind=? AND public_id=?", key).fetchone():
                if take_file:
                    raise ValueError(f"{rel} cannot be taken: it names a reserved tombstone: {key[0]} {key[1]}")
                return result("conflict", f"file names a reserved tombstone: {key[0]} {key[1]}")
            chosen = theirs
        else:
            ours = current["fields"], projection_parse.normal_body(current["body"])
            if take_file or encode(ours) == encode(theirs):
                chosen = theirs
            elif checkout is not None and base is None:
                return Prepared(rel, "pending", "no trusted base in this checkout for divergent "
                                f"{key[0]} {key[1]}; take the file with sync take_file or release it to receive "
                                "the published version", observed, None)
            elif base_rows is None or key not in base_rows:
                return result("conflict", f"no verified prior base for divergent {key[0]} {key[1]}; explicit resolution required")
            else:
                base_fields, base_body = base_rows[key]
                if key[0] not in {"backlog", "project"}:
                    base_fields["id"] = key[1]
                base_fields = protect_local(key[0], base_fields, current["fields"])
                chosen, conflicts = merge((base_fields, projection_parse.normal_body(base_body)), ours, theirs)
                overlaps.extend(f"{key[0]}:{key[1]}.{field}" for field in conflicts)
            if chosen[1] == ours[1]:
                # Unchanged prose keeps its stored bytes; normalising is not an edit.
                chosen = chosen[0], current["body"]
        # Never report as accepted what the chosen row will not hold (D3).
        dropped.extend(unapplied(kind, key, authored[key][0],
                                 None if authored_base is None or key not in authored_base else authored_base[key][0],
                                 chosen[0], base_known=authored_base is not None))
        # A large backlog index may contain hundreds of unchanged entities.
        # Only rows we will write need a full replacement/revision precondition;
        # leaving another row untouched cannot overwrite its concurrent edit.
        if current is None or encode(chosen) != encode((current["fields"], current["body"])):
            rows.append({"kind": key[0], "id": key[1], "revision": current["revision"] if current else 0,
                         "fields": chosen[0], "body": chosen[1]})
    if len(rows) > sync.MAX_ROWS:
        return Prepared(rel, "pending", f"more than {sync.MAX_ROWS} changed entities; bounded import refused", observed, None)
    if dropped:
        reason = (f"{rel} changes what this file does not own, so sync cannot apply it: " + "; ".join(dropped)
                  + ". The file is kept and flagged; make the change where it is owned, or restore the file")
        if take_file:
            raise ValueError(f"{rel} cannot be taken: {reason}")
        if overlaps:
            reason += "; overlapping edits retained in external history: " + ", ".join(overlaps)
        return result("conflict", reason, rows)
    if overlaps:
        return result("conflict", "overlapping edits retained in external history: " + ", ".join(overlaps), rows)
    return result("apply", "explicit file resolution" if take_file else "accepted authored file changes", rows)
