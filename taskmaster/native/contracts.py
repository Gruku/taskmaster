"""Validated command envelopes; no database or network access during admission."""
import hashlib
import json
import re

from .migrate import encode

MAX_BYTES = 1024 * 1024
MAX_BATCH_COMMANDS = 100
METADATA_FIELDS = frozenset({"title", "priority", "notes", "branch", "worktree", "estimate", "tldr", "next_step"})


class Conflict(ValueError):
    """A retry key or revision cannot safely be applied to current state."""


class CancelledBeforeExecution(RuntimeError):
    """Admission was cancelled before effects; no outcome ambiguity."""


def _identifier(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}", value):
        raise ValueError(f"invalid {name}")


def validate_operation(operation, arguments, *, in_batch=False):
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    if operation == "task.patch":
        if set(arguments) - {"id", "set", "remove"}:
            raise ValueError("unknown task.patch argument")
        _identifier(arguments.get("id"), "task id")
        changes, remove = arguments.get("set", {}), arguments.get("remove", [])
        if not isinstance(changes, dict) or not isinstance(remove, list) or not all(isinstance(f, str) for f in remove):
            raise ValueError("set must be an object and remove a field list")
        if (set(changes) | set(remove)) - METADATA_FIELDS:
            raise ValueError("this command slice only accepts task metadata fields")
        if set(changes) & set(remove):
            raise ValueError("a field cannot be both set and removed")
        if any(value is not None and not isinstance(value, str) for value in changes.values()):
            raise ValueError("metadata values must be text or null")
        if "priority" in changes and changes["priority"] not in ("critical", "high", "medium", "low"):
            raise ValueError("invalid priority")
        if "tldr" in remove or ("tldr" in changes and not changes["tldr"]):
            raise ValueError("tldr cannot be cleared")
    elif operation == "note.create":
        if set(arguments) - {"text", "author", "pinned"}:
            raise ValueError("unknown note.create argument")
        if not isinstance(arguments.get("text"), str) or not arguments["text"].strip():
            raise ValueError("note text is required")
        if "pinned" in arguments and type(arguments["pinned"]) is not bool:
            raise ValueError("pinned must be boolean")
        from taskmaster.taskmaster_v3 import NOTE_AUTHORS
        if arguments.get("author", "claude") not in NOTE_AUTHORS:
            raise ValueError("invalid note author")
    elif operation == "batch" and not in_batch:
        commands = arguments.get("commands")
        if set(arguments) != {"commands"} or not isinstance(commands, list) or not 1 <= len(commands) <= MAX_BATCH_COMMANDS:
            raise ValueError(f"batch requires 1 to {MAX_BATCH_COMMANDS} commands")
        for item in commands:
            if not isinstance(item, dict) or set(item) != {"operation", "arguments"}:
                raise ValueError("invalid batch item")
            validate_operation(item["operation"], item["arguments"], in_batch=True)
    else:
        from . import lifecycle
        if operation not in lifecycle.OPERATIONS:
            raise ValueError(f"unsupported operation: {operation}")
        if in_batch and operation in lifecycle.linear_outbox.OPERATIONS:
            raise ValueError('Linear queue operations require their own durable receipts')
        if in_batch and operation in lifecycle.sync.OPERATIONS:
            raise ValueError('sync imports require their own manifest preconditions and durable receipts')
        if in_batch and operation in lifecycle.graph_repair.OPERATIONS:
            raise ValueError('graph repair is a whole-store maintenance command and runs on its own')
        lifecycle.validate(operation, arguments)


def validate(envelope):
    if not isinstance(envelope, dict) or set(envelope) - {"protocol", "store_id", "caller_scope", "request_id", "operation", "arguments", "expected_revisions"}:
        raise ValueError("invalid command envelope")
    # Copy through canonical JSON so a caller cannot mutate an admitted request.
    encoded = encode(envelope).encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise ValueError(f"command exceeds the encoded byte limit of {MAX_BYTES} bytes (1 MiB)")
    request = json.loads(encoded)
    if type(request.get("protocol")) is not int or request["protocol"] != 2:
        raise ValueError("unsupported command protocol")
    for name in ("store_id", "caller_scope"):
        if not isinstance(request.get(name), str) or not 1 <= len(request[name]) <= 256:
            raise ValueError(f"invalid {name}")
    key = request.get("request_id")
    if key is not None and (not isinstance(key, str) or not 1 <= len(key) <= 256):
        raise ValueError("invalid request_id")
    operation = request.get("operation")
    if not isinstance(operation, str):
        raise ValueError("operation must be a string")
    if (operation.endswith(".create") or operation == "batch") and key is None:
        raise ValueError("request_id is required for creation or batch")
    validate_operation(request.get("operation"), request.get("arguments"))
    expected = request.setdefault("expected_revisions", [])
    if not isinstance(expected, list) or len(expected) > 100:
        raise ValueError("expected_revisions must contain at most 100 preconditions")
    seen = set()
    for item in expected:
        if not isinstance(item, dict) or set(item) != {"kind", "id", "revision"}:
            raise ValueError("invalid expected revision")
        _identifier(item["id"], "revision id")
        if not isinstance(item["kind"], str) or type(item["revision"]) is not int or item["revision"] < 1:
            raise ValueError("invalid expected revision")
        identity = (item["kind"], item["id"])
        if identity in seen:
            raise ValueError("duplicate expected revision")
        seen.add(identity)
    fingerprint = hashlib.sha256(encode({k: v for k, v in request.items() if k != "request_id"}).encode()).hexdigest()
    return request, fingerprint
