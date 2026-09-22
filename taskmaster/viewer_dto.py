"""Pure b1 board wire contract, shared by both storage authorities."""
from copy import deepcopy

DTO_VERSION = "b1"
BOARD_KINDS = ("task", "epic", "phase")
# The historical flat layout authors its task index in the backlog row. Its
# adapter includes that extra row in revisions and always resyncs after changes.
LEGACY_FLAT_INPUT_KINDS = BOARD_KINDS + ("backlog",)
BOARD_TASK_FIELDS = (
    "id", "title", "status", "priority", "epic", "phase", "area", "branch",
    "estimate", "created", "started", "completed", "bundle", "docs", "tracker_id",
    "spec_review", "depends_on", "sub_repo", "lane", "gate_state", "merge_gate_state",
    "human_action", "blockers_count", "archived_reason", "order",
)
BOARD_EPIC_FIELDS = ("id", "name", "status", "last_referenced", "done_when", "design_status", "color")
BOARD_PHASE_FIELDS = ("id", "name", "status", "order")
TASK_INPUT_FIELDS = tuple(f for f in BOARD_TASK_FIELDS if f != "blockers_count") + ("blockers",)


def select(doc, fields):
    return {key: deepcopy(doc[key]) for key in fields if key in doc and doc[key] is not None}


def task_row(doc):
    from taskmaster.backlog_server import _normalize_task
    row = select(_normalize_task(dict(doc)), BOARD_TASK_FIELDS)
    if "spec_review" in row:
        review = row["spec_review"]
        row["spec_review"] = select(review, ("verdict",)) if isinstance(review, dict) else {}
    if "blockers" in doc:
        blockers = doc["blockers"]
        row["blockers_count"] = len(blockers) if isinstance(blockers, list) else 0
    return row


def build_board(task_docs, epic_docs, phase_docs):
    epics = [select(doc, BOARD_EPIC_FIELDS) for doc in epic_docs]
    ranks = {e["id"]: i for i, e in enumerate(epics)}
    tasks = [task_row(t) for t in task_docs if t.get("epic") in ranks]
    tasks.sort(key=lambda t: (ranks[t["epic"]], float(t.get("order") or 0), str(t["id"])))
    phases = [select(doc, BOARD_PHASE_FIELDS) for doc in phase_docs]
    phases.sort(key=lambda p: p.get("order", 999))
    return {"tasks": tasks, "epics": epics, "phases": phases}


def revision(identity, seq, version):
    return f"{DTO_VERSION}:{identity}:{seq}:{version}"


def token_sequence(token, identity, current, version):
    try:
        dto, store_id, seq, release = token.split(":")
        seq = int(seq)
        if seq < 0:
            return None, "bad_token"
    except (ValueError, TypeError, AttributeError):
        return None, "bad_token"
    if store_id != identity:
        return None, "store_changed"
    if dto != DTO_VERSION or release != version:
        return None, "version_changed"
    if seq > current:
        return None, "bad_token"
    return seq, None


def matches(header, etag):
    # If-None-Match uses weak comparison, including lists and the wildcard.
    return any(value.strip().removeprefix("W/").strip('"') in (etag, "*")
               for value in (header or "").split(","))
