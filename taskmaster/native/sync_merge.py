"""Pure native import merge policy: real base only; absence is a field value."""
from copy import deepcopy
import json

from taskmaster.native import claims
from taskmaster.native.migrate import encode
from taskmaster.taskmaster_v3 import EPIC_HEAVY_FIELDS, PHASE_HEAVY_FIELDS, _three_way_merge_fields

DERIVED_BACKLOG_FIELDS = ("bugs", "issues", "trackers", "handovers", "threads", "thread_meta")
TRACKER_LOCAL_FIELDS = ("last_synced", "last_pushed", "synced_hash", "push_hash", "linear_issue_id")


_MISSING = object()
_OWNER = {
    "title": "the display title mirrors the name in backlog.yaml; rename it there or with backlog_update_{kind}",
    "claim": "claims are local; use the claim tools",
    "tracker": "local Linear push receipts are not authored",
    "derived": "a derived index rebuilt from the entity documents; edit those documents",
    "heavy": "owned by {kind}s/<id>.md",
    "slim": "owned by backlog.yaml; edit it there or with backlog_update_{kind}",
}


def _protected(kind):
    return (claims.CLAIM_FIELDS if kind == "task" else
            TRACKER_LOCAL_FIELDS if kind == "tracker" else
            DERIVED_BACKLOG_FIELDS if kind == "backlog" else ())


def _same(left, right):
    return (left is _MISSING) == (right is _MISSING) and (left is _MISSING or encode(left) == encode(right))


def unapplied(file_kind, key, authored, base, chosen, *, base_known):
    """Authored field changes the chosen row will not hold, with who owns them.

    `authored`/`base` are what the file and its verified base literally say,
    before split ownership. A value equal to the base was not authored now (a
    stale mirror or claim is not an edit). Without a verified base, locally
    maintained fields cannot be told apart from stale copies and are skipped.
    """
    kind = key[0]
    names = set(authored) | (set(base) if base is not None else set())
    if base_known and base is None:
        base = {}
    found = []
    for name in sorted(names - {"id"}):
        value = authored.get(name, _MISSING)
        if base is not None and _same(value, base.get(name, _MISSING)):
            continue
        if base is None and name in _protected(kind):
            continue
        if file_kind in {"epic", "phase"} and name == "title":
            display = chosen.get("name") or chosen.get("title")
            expected = _MISSING if display in (None, "") else display
        else:
            expected = chosen.get(name, _MISSING)
        if _same(value, expected):
            continue
        heavy = EPIC_HEAVY_FIELDS if kind == "epic" else PHASE_HEAVY_FIELDS if kind == "phase" else ()
        owner = ("title" if file_kind in {"epic", "phase"} and name == "title" else
                 "slim" if file_kind in {"epic", "phase"} else
                 "heavy" if name in heavy else
                 "claim" if kind == "task" and name in claims.CLAIM_FIELDS else
                 "tracker" if kind == "tracker" and name in TRACKER_LOCAL_FIELDS else
                 "derived" if kind == "backlog" and name in DERIVED_BACKLOG_FIELDS else None)
        hint = "" if owner is None else " (" + _OWNER[owner].format(kind=kind) + ")"
        found.append(f"{kind}:{key[1]}.{name}{hint}")
    return found


def protect_local(kind, fields, current):
    """A file cannot resurrect an exported claim or overwrite local push receipts."""
    result = deepcopy(fields)
    current = current or {}
    protected = (claims.CLAIM_FIELDS if kind == "task" else
                 TRACKER_LOCAL_FIELDS if kind == "tracker" else
                 DERIVED_BACKLOG_FIELDS if kind == "backlog" else ())
    for name in protected:
        if name in current:
            result[name] = deepcopy(current[name])
        else:
            result.pop(name, None)
    if kind == "task" and (result.get("status") in claims.TERMINAL_STATUSES
                            or current.get("status") in claims.TERMINAL_STATUSES
                            or result.get("archived") or current.get("archived")):
        claims.released(result)
    return result


def merge(base, ours, theirs):
    """Return merged document and overlapping authored field names.

    Feed canonical JSON tokens to the established per-field merge: Python's
    bool/int equality must not mistake false for 0. Missing keys remain absent,
    not null. On overlap keep the database value and report the conflict; the
    caller must retain the external bytes before recording any accepted change.
    """
    def tokens(document):
        fields, body = document
        return {**{key: encode(value) for key, value in fields.items()}, "_body": encode(body)}

    base_tokens, our_tokens, their_tokens = map(tokens, (base, ours, theirs))
    missing = object()
    overlap = sorted(key for key in set(base_tokens) | set(our_tokens) | set(their_tokens)
                     if our_tokens.get(key, missing) != their_tokens.get(key, missing)
                     and our_tokens.get(key, missing) != base_tokens.get(key, missing)
                     and their_tokens.get(key, missing) != base_tokens.get(key, missing))
    combined = {key: json.loads(value) for key, value in
                _three_way_merge_fields(base_tokens, our_tokens, their_tokens).items()}
    body = combined.pop("_body")
    return (combined, body), overlap
