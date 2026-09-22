"""Pure native import merge policy: real base only; absence is a field value."""
from copy import deepcopy
import json

from taskmaster.native import claims
from taskmaster.native.migrate import encode
from taskmaster.taskmaster_v3 import _three_way_merge_fields

DERIVED_BACKLOG_FIELDS = ("bugs", "issues", "trackers", "handovers", "threads", "thread_meta")
TRACKER_LOCAL_FIELDS = ("last_synced", "last_pushed", "synced_hash", "push_hash", "linear_issue_id")


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
