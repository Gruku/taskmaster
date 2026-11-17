# User intent: the pure team resolver for multi-user sync. Given base/ours/theirs shared states of one
# entity it decides what lands (spec §6.5), so teammates' concurrent edits merge without losing work.
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from taskmaster.bounded_run import run_bounded
from taskmaster.native import claims
from taskmaster.native.migrate import encode
from taskmaster.native.sync_merge import DERIVED_BACKLOG_FIELDS
from taskmaster.projection_parse import normal_body, validate_identity
from taskmaster.taskmaster_v3 import (
    _parse_iso8601, _three_way_merge_fields, compute_gate_state, compute_merge_gate_state)

Document = tuple[dict, "str | None"]

SHARED_KINDS = ("task", "epic", "phase", "area", "bug", "issue", "handover", "decision", "idea",
                "note", "backlog", "project", "profile", "plan", "alias")
EMPTY_BASE: Document = ({}, None)
NOTE_CODES = ("field_lost", "assignment_lost", "body_conflict", "archived_with_edits", "tombstone_lifted")
BODY_MERGE_TIMEOUT_S = 5


class IdCollision(ValueError):
    """The same new (kind, id) appeared on both sides; team-ids must rename one before merging (§7)."""

    def __init__(self, kind, ident):
        self.kind, self.id = kind, ident
        super().__init__(f"{kind} {ident!r} is new on both sides: team-ids must rename one before the merge")


@dataclass(frozen=True)
class LocalIdentity:
    handle: str
    clone_id: str


@dataclass(frozen=True)
class SyncNote:
    kind: str
    id: str
    code: str
    field: "str | None"
    side: str
    lost_json: "str | None"
    kept_json: "str | None"
    detail: "str | None" = None


@dataclass(frozen=True)
class BodyConflict:
    kind: str
    id: str
    reason: str
    base: "str | None"
    ours: "str | None"
    theirs: "str | None"


@dataclass(frozen=True)
class ClaimMoved:
    kind: str
    id: str
    from_handle: "str | None"
    to_handle: "str | None"
    from_clone: "str | None"
    to_clone: "str | None"


@dataclass(frozen=True)
class Resolution:
    kind: str
    id: str
    state: "Document | None"
    action: str
    notes: tuple
    conflict: "BodyConflict | None"
    claim_moved: "ClaimMoved | None"


def _local_only(kind):
    return ((*claims.CLAIM_FIELDS, "worktree", "branch") if kind == "task" else
            DERIVED_BACKLOG_FIELDS if kind == "backlog" else ())


def _guard(kind, ident, documents):
    """Validate and copy the present inputs; a local-only field here is a caller bug (spec §3)."""
    if kind == "tracker" or kind not in SHARED_KINDS:
        raise ValueError(f"kind {kind!r} is not a shared kind")
    guarded = []
    for document in documents:
        if document is None:
            guarded.append(None)
            continue
        fields, body = deepcopy(document)
        for field in _local_only(kind):
            if field in fields:
                raise ValueError(f"{kind} {ident!r} carries local-only field {field!r}; pass shared state")
        validate_identity(kind, ident, fields)
        gates = fields.get("gates")
        if kind == "task" and "gates" in fields and not (
                isinstance(gates, dict) and all(isinstance(r, dict) for r in gates.values())):
            raise ValueError(f"{kind} {ident!r} field 'gates' must map gate names to records")
        guarded.append((fields, normal_body(body.replace("\r\n", "\n") if isinstance(body, str) else body)))
    return guarded


_MISSING = object()


def _tokens(fields):
    return {key: encode(value) for key, value in fields.items()}


def _doc_token(document):
    return None if document is None else (encode(document[0]), document[1])


def _tok(tokens, key):
    return tokens.get(key, _MISSING)


def _json(token):
    return None if token is _MISSING else token


def _lost_note(kind, ident, field, side, lost, kept, code="field_lost", detail=None):
    """`lost`/`kept` are tokens (encoded JSON) or _MISSING; the note stores None for absence."""
    return SyncNote(kind, ident, code, field, side, _json(lost), _json(kept), detail)


DERIVED_MIRRORS = ("gate_state", "merge_gate_state")
UNION_MAPS = ("gates", "merge_status")
KEYWISE_MAPS = ("docs",)
IDENTITY_LIST_FIELDS = frozenset((
    "depends_on", "task_ids", "related_tasks", "related_issues", "adopted_into", "promoted_to",
    "fixed_in_task", "duplicate_of", "task_id", "bundle", "area",   # ids
    "anchors", "location",                                         # paths
    "components", "tags",                                           # names
    "links", "entries"))                                            # typed links; plan entries


def _identity(field, element):
    """Hashable identity of a list element, or None when this element has none."""
    if field == "links":
        return (element.get("type"), element.get("target")) if isinstance(element, dict) else None
    if field == "entries":
        return ("id", element.get("id")) if isinstance(element, dict) and "id" in element else None
    return element if isinstance(element, str) else None


def _merge_identity_list(field, base, ours, theirs):
    """Theirs' order and dicts; removals on either side apply; ours-only additions append. None = no identity."""
    ids = {}
    for name, values in (("base", base), ("ours", ours), ("theirs", theirs)):
        if not isinstance(values, list):
            return None
        keys = [_identity(field, e) for e in values]
        if any(k is None for k in keys):
            return None
        ids[name] = keys
    in_base, in_ours, in_theirs = set(ids["base"]), set(ids["ours"]), set(ids["theirs"])
    merged = [e for e, k in zip(theirs, ids["theirs"]) if not (k in in_base and k not in in_ours)]
    merged += [e for e, k in zip(ours, ids["ours"]) if k not in in_base and k not in in_theirs]
    seen, unique = set(), []
    for element in merged:
        key = _identity(field, element)
        if key not in seen:
            seen.add(key)
            unique.append(element)
    return unique


def _map_value(tokens, field):
    """Decoded map under `field`: ({}, True) if absent, (dict, True) if a dict, (None, False) otherwise."""
    if field not in tokens:
        return {}, True
    value = json.loads(tokens[field])
    return (value, True) if isinstance(value, dict) else (None, False)


def _merge_map(kind, ident, field, base, ours, theirs, union, notes):
    """Per-key three-way over a dict field. Returns the merged dict; union=True keeps a changed record over a clear."""
    result = {}
    for key in sorted(set(base) | set(ours) | set(theirs)):
        b, o, t = (_tok_value(m, key) for m in (base, ours, theirs))
        if o == t or o == b:
            chosen = t
        elif t == b:
            chosen = o
        else:
            lost_side = "ours"
            chosen = t
            if union and t is _MISSING:
                chosen = o
                lost_side = "theirs"
            lost, kept = (o, chosen) if lost_side == "ours" else (t, chosen)
            notes.append(_lost_note(kind, ident, f"{field}.{key}", lost_side, lost, kept))
        if chosen is not _MISSING:
            result[key] = json.loads(chosen)
    return result


def _tok_value(mapping, key):
    return encode(mapping[key]) if key in mapping else _MISSING


def merge_body(base, ours, theirs, *, timeout=BODY_MERGE_TIMEOUT_S):
    """(merged body, None) when clean; (theirs, reason) when it could not merge cleanly."""
    if ours == theirs:
        return ours, None
    if ours == base:
        return theirs, None
    if theirs == base:
        return ours, None
    found = shutil.which("git")
    if not found:
        return theirs, "git_unavailable"
    # Same hygiene as coordinator/git.py:64-84 (not imported: this module stays free of coordinator state).
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        paths = []
        try:
            for name, text in (("ours", ours), ("base", base), ("theirs", theirs)):
                path = Path(directory) / name
                path.write_bytes(b"" if text is None else text.encode("utf-8") + b"\n")
                paths.append(str(path))
        except UnicodeEncodeError:
            return theirs, "git_error"
        try:
            done = run_bounded(
                [found, "merge-file", "-p", "-q", "-L", "ours", "-L", "base", "-L", "theirs", *paths],
                timeout=timeout, cwd=directory, env=env, **flags)
        except subprocess.TimeoutExpired:
            return theirs, "timeout"
        except OSError:
            return theirs, "git_unavailable"
    if done.returncode == 0:
        try:
            return normal_body(done.stdout.decode("utf-8")), None
        except UnicodeDecodeError:
            return theirs, "git_error"
    return theirs, "conflict" if 0 < done.returncode < 128 else "git_error"


def _recompute_derived(fields, documents, merge_targets):
    """gate_state / merge_gate_state are mirrors of the merged records, not merged values."""
    if any("gates" in d[0] or "gate_state" in d[0] for d in documents):
        value = compute_gate_state(fields)
        if value or "gate_state" in fields:
            fields["gate_state"] = value
    if merge_targets is not None and any("merge_status" in d[0] or "merge_gate_state" in d[0] for d in documents):
        value = compute_merge_gate_state(fields, merge_targets)
        if value or "merge_gate_state" in fields:
            fields["merge_gate_state"] = value


STAMP_GROUPS = (("updated_by", "updated_at"), ("last_referenced",))
ASSIGNMENT_GROUP = ("assignee", "assignee_source", "claimed_on")
ARCHIVE_FIELDS = ("archived", "archive_reason", "archived_reason", "archived_at")


def _set(merged, key, token):
    if token is _MISSING:
        merged.pop(key, None)
    else:
        merged[key] = token


def _group_changed(group, base, side):
    return any(_tok(side, k) != _tok(base, k) for k in group)


def _group_same(group, left, right):
    return all(_tok(left, k) == _tok(right, k) for k in group)


def _stamp_time(tokens, key):
    try:
        return _parse_iso8601(json.loads(tokens[key]))
    except Exception:
        return None


def _newest_group(group, bt, ot, tt, merged):
    """Newest wins when both sides changed (tie or unparseable: theirs); a one-sided change just lands."""
    o_changed, t_changed = _group_changed(group, bt, ot), _group_changed(group, bt, tt)
    if not (o_changed or t_changed):
        return
    source = tt
    if o_changed and not t_changed:
        source = ot
    elif o_changed and t_changed and not _group_same(group, ot, tt):
        ours_time, theirs_time = _stamp_time(ot, group[-1]), _stamp_time(tt, group[-1])
        if ours_time is not None and (theirs_time is None or ours_time > theirs_time):
            source = ot
    for key in group:
        _set(merged, key, _tok(source, key))


def _group_json(tokens):
    present = {k: json.loads(tokens[k]) for k in ASSIGNMENT_GROUP if k in tokens}
    return encode(present) if present else _MISSING


def _assignment_group(kind, ident, bt, ot, tt, merged, notes):
    """The three assignment fields move as one unit; mixing sources would forge a claim."""
    group = ASSIGNMENT_GROUP
    o_changed, t_changed = _group_changed(group, bt, ot), _group_changed(group, bt, tt)
    if not (o_changed or t_changed):
        return
    source = ot if o_changed and not t_changed else tt
    for key in group:
        _set(merged, key, _tok(source, key))
    if o_changed and t_changed and not _group_same(group, ot, tt):
        notes.append(_lost_note(kind, ident, "assignment", "ours", _group_json(ot), _group_json(tt),
                                code="assignment_lost"))


def _is_archived(tokens):
    return "archived" in tokens and bool(json.loads(tokens["archived"]))


def _archive_policy(kind, ident, base, ours, theirs, bt, ot, tt, merged, overlap, handled, notes):
    new_ours = _is_archived(ot) and not _is_archived(bt)
    new_theirs = _is_archived(tt) and not _is_archived(bt)
    status_archived = lambda t: "status" in t and json.loads(t["status"]) == "archived"
    if new_ours and new_theirs:  # archive vs archive: theirs, silently
        # Only the stamps (and a shared "archived" status) are silent; reasons follow the scalar rule.
        keys = ("archived", "archived_at") + (("status",) if status_archived(ot) and status_archived(tt) else ())
        for key in keys:
            _set(merged, key, _tok(tt, key))
            handled.add(key)
        return
    if new_ours == new_theirs:
        return
    side, other_side = ("ours", "theirs") if new_ours else ("theirs", "ours")
    arch, other = (ot, tt) if new_ours else (tt, ot)
    keys = ARCHIVE_FIELDS + (("status",) if status_archived(arch) else ())
    for key in keys:
        if _tok(arch, key) != _tok(bt, key):
            _set(merged, key, _tok(arch, key))
            if key in overlap:
                notes.append(_lost_note(kind, ident, key, other_side, _tok(other, key), _tok(arch, key)))
                handled.add(key)
    other_doc = theirs if new_ours else ours
    edited = (any(_tok(other, k) != _tok(bt, k) for k in set(other) | set(bt))
              or other_doc[1] != base[1])
    if edited:
        notes.append(_lost_note(kind, ident, "archived", side, _MISSING, _tok(arch, "archived"),
                                code="archived_with_edits"))


def _claim_moved(kind, ident, me, ours_fields, fields):
    if me is None:
        return None
    mine_handle = ours_fields.get("assignee") == me.handle
    mine_clone = ours_fields.get("claimed_on") == me.clone_id
    if (mine_handle and fields.get("assignee") != ours_fields.get("assignee")) or (
            mine_clone and fields.get("claimed_on") != ours_fields.get("claimed_on")):
        return ClaimMoved(kind, ident, ours_fields.get("assignee"), fields.get("assignee"),
                          ours_fields.get("claimed_on"), fields.get("claimed_on"))
    return None


def _merge_present(kind, ident, base, ours, theirs, me, merge_targets, timeout):
    bt, ot, tt = (_tokens(d[0]) for d in (base, ours, theirs))
    merged = _three_way_merge_fields(bt, tt, ot)  # second argument wins on overlap: theirs
    keys = set(bt) | set(ot) | set(tt)
    overlap = {k for k in keys
               if _tok(ot, k) != _tok(tt, k) and _tok(ot, k) != _tok(bt, k) and _tok(tt, k) != _tok(bt, k)}
    notes = []
    handled = set()

    for key in DERIVED_MIRRORS:  # recomputed from the merged records below, never merged
        handled.add(key)

    for group in STAMP_GROUPS:
        _newest_group(group, bt, ot, tt, merged)
        handled.update(group)
    _assignment_group(kind, ident, bt, ot, tt, merged, notes)
    handled.update(ASSIGNMENT_GROUP)
    _archive_policy(kind, ident, base, ours, theirs, bt, ot, tt, merged, overlap, handled, notes)

    for field in (*UNION_MAPS, *KEYWISE_MAPS):
        maps = [_map_value(t, field) for t in (bt, ot, tt)]
        if all(ok for _, ok in maps) and len({_tok(t, field) for t in (bt, ot, tt)}) > 1:
            combined = _merge_map(kind, ident, field, *(m for m, _ in maps), field in UNION_MAPS, notes)
            if combined or field in merged:
                merged[field] = encode(combined)
            handled.add(field)

    for field in sorted(overlap & IDENTITY_LIST_FIELDS):
        lists = [json.loads(t[field]) if field in t else None for t in (bt, ot, tt)]
        lists[0] = [] if lists[0] is None else lists[0]
        combined = _merge_identity_list(field, *lists) if None not in lists[1:] else None
        if combined is not None:
            merged[field] = encode(combined)
            handled.add(field)

    for key in sorted(overlap - handled):
        notes.append(_lost_note(kind, ident, key, "ours", _tok(ot, key), _tok(tt, key)))

    conflict = None
    body, reason = merge_body(base[1], ours[1], theirs[1], timeout=timeout)
    if reason:
        conflict = BodyConflict(kind, ident, reason, base[1], ours[1], theirs[1])
        notes.append(SyncNote(kind, ident, "body_conflict", "body", "ours", None, None, reason))

    fields = {key: json.loads(merged[key]) for key in sorted(merged)}
    if kind == "task":
        _recompute_derived(fields, (base, ours, theirs), merge_targets)
    state = (fields, body)
    action = "unchanged" if _doc_token(state) == _doc_token(ours) else "update"
    notes.sort(key=lambda n: (n.field or "", n.code))
    claim_moved = _claim_moved(kind, ident, me, ours[0], fields)
    return Resolution(kind, ident, state, action, tuple(notes), conflict, claim_moved)


def resolve(kind, ident, base, ours, theirs, *, me=None, merge_targets=None,
            timeout=BODY_MERGE_TIMEOUT_S) -> Resolution:
    base, ours, theirs = _guard(kind, ident, (base, ours, theirs))

    def done(state, action, *notes, claim_moved=None):
        return Resolution(kind, ident, state, action, tuple(notes), None, claim_moved)

    if base is None:  # new entity: one side inserts it; both sides is a collision team-ids must rename first
        if ours is None and theirs is None:
            raise ValueError(f"{kind} {ident!r} is absent on every side")
        if ours is not None and theirs is not None:
            raise IdCollision(kind, ident)
        return done(theirs, "insert") if ours is None else done(ours, "unchanged")
    if ours is None and theirs is None:
        return done(None, "unchanged")
    if ours is None or theirs is None:  # a tombstone against an edit: the edit wins
        present, side = (theirs, "ours") if ours is None else (ours, "theirs")
        if _doc_token(present) == _doc_token(base):
            if ours is None:
                return done(None, "unchanged")
            return done(None, "delete", claim_moved=_claim_moved(kind, ident, me, ours[0], {}))
        return done(present, "insert" if ours is None else "unchanged",
                    SyncNote(kind, ident, "tombstone_lifted", None, side, None, None))
    return _merge_present(kind, ident, base, ours, theirs, me, merge_targets, timeout)
