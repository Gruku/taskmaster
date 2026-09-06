# /// script
# requires-python = ">=3.11"
# dependencies = ["fastmcp", "pyyaml"]
# ///

import asyncio
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import urllib.request
import uuid
import webbrowser
from datetime import date, datetime, timezone
from functools import partial
from http import HTTPStatus
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal

import yaml
from fastmcp import FastMCP

from contextlib import contextmanager
from functools import wraps

from taskmaster import store
from taskmaster import yaml_io
from taskmaster.blast_radius import (
    BlastRadiusConfig,
    load_config,
    analyze_predictive,
    analyze_evidence,
)

def _guard_legacy_layout(fn):
    """Turn a refused legacy layout into a tool result, never a traceback.

    `store.open_store` refuses any backlog directory that is not `.taskmaster`
    (there is exactly one store location).  Every MCP tool goes through this
    wrapper so the caller reads the actionable message instead of a stack trace
    or, worse, an empty backlog.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except store.LegacyLayoutError as exc:
            return f"Error: {exc}"

    return wrapper


class _GuardedToolRegistrar:
    """`FastMCP`, with the legacy-layout guard applied to every tool it registers.

    Wrapping at registration is the only chokepoint that cannot be forgotten
    when a new tool is added; everything else on the server is delegated.
    """

    def __init__(self, inner):
        self._inner = inner

    def tool(self, *args, **kwargs):
        decorator = self._inner.tool(*args, **kwargs)

        def register(fn):
            return decorator(_guard_legacy_layout(fn))

        return register

    def __getattr__(self, name):
        return getattr(self._inner, name)


mcp = _GuardedToolRegistrar(FastMCP("taskmaster"))

# Repo/plugin root — this module lives in the taskmaster/ package, one level down.
SCRIPT_DIR = Path(__file__).resolve().parent.parent
ROOT = Path(os.environ.get("TASKMASTER_ROOT", Path.cwd()))
CONFIG_PATH = ROOT / ".taskmaster" / "taskmaster.json"
# Legacy location read-only fallback. Pre-consolidation projects wrote config
# to `.claude/taskmaster.json`; the resolver still honors it so those servers
# keep working until the user runs `backlog_canonicalize_layout`.
LEGACY_CONFIG_PATH = ROOT / ".claude" / "taskmaster.json"

# Version from plugin.json
_plugin_json = SCRIPT_DIR / ".claude-plugin" / "plugin.json"
VERSION = json.loads(_plugin_json.read_text(encoding="utf-8"))["version"] if _plugin_json.exists() else "0.0.0"

# Priority mapping: canonical names ↔ legacy P-codes
PRIORITY_NAMES = ("critical", "high", "medium", "low")
_LEGACY_TO_NAME = {"P0": "critical", "P1": "high", "P2": "medium", "P3": "low"}
_NAME_TO_LEGACY = {v: k for k, v in _LEGACY_TO_NAME.items()}

# v3 layout primitives (schema versions, atomic writes, etc.) live in taskmaster_v3.
# Re-imported here for in-module reference.
from taskmaster.taskmaster_v3 import (
    SCHEMA_V2,
    SCHEMA_V3,
    SCHEMA_V4,
    SCHEMA_DEFAULT,
    TLDR_MAX_CHARS,
    extract_tldr,
    VALID_LANES as _VALID_LANES,
    VALID_GATES as _VALID_GATES,
    VERDICT_GATES as _VERDICT_GATES,
    VALID_GATE_VERDICTS as _VALID_GATE_VERDICTS,
    required_gates as _required_gates,
    blocking_gates as _blocking_gates,
    outstanding_required_gates as _outstanding_required_gates,
    gate_satisfied as _gate_satisfied,
    default_lane as _default_lane,
    compute_gate_state as _compute_gate_state,
    HANDOVER_KINDS,
    HEAVY_FIELDS as _HEAVY_FIELDS,
    detect_schema_version as _detect_schema_version,
    build_handover_doc as _build_handover_doc,
    handover_path as _handover_path,
    supersede_handover_doc as _supersede_handover_doc,
    flag_handover_doc_for_review as _flag_handover_doc_for_review,
    set_handover_status_doc as _set_handover_status_doc,
    sync_handover_index as _sync_handover_index,
    sort_handover_rows as _sort_handover_rows,
    derive_thread_name as _derive_thread_name,
    ISSUE_STATUSES,
    ISSUE_SEVERITIES,
    BUG_STATUSES,
    build_bug_doc as _build_bug_doc,
    apply_bug_updates as _apply_bug_updates,
    assert_bug_archivable as _assert_bug_archivable,
    bug_path as _bug_path,
    sync_bug_index as _sync_bug_index,
    build_issue_doc as _build_issue_doc,
    issue_path as _issue_path,
    apply_issue_updates as _apply_issue_updates,
    sync_issue_index as _sync_issue_index,
    EXTERNAL_SYSTEMS,
    build_tracker_doc as _build_tracker_doc,
    read_tracker as _read_tracker,
    apply_tracker_updates as _apply_tracker_updates,
    list_tracker_ids as _list_tracker_ids,
    sync_tracker_index as _sync_tracker_index,
    make_tracker_id as _make_tracker_id,
    tracker_path as _tracker_path,
    tracker_dir as _tracker_dir,
    linked_tasks_for_tracker as _linked_tasks_for_tracker,
    linked_issues_for_tracker as _linked_issues_for_tracker,
    _validate_tracker as _validate_tracker_fm,
    load_linear_config as _load_linear_config,
    build_idea_doc as _build_idea_doc,
    idea_path as _idea_path,
    apply_idea_updates as _apply_idea_updates,
    build_decision_doc as _build_decision_doc,
    apply_decision_patch as _apply_decision_patch,
    resolve_decision_doc as _resolve_decision_doc,
    drop_decision_doc as _drop_decision_doc,
    link_decision_doc_to_handover as _link_decision_doc_to_handover,
    decision_path as _decision_path,
    continuity_items as _continuity_items,
    load_viewer_prefs,
    save_viewer_prefs,
    list_sessions,
    get_session_detail,
    slim_entity as _slim_entity,
    resolve_sections as _resolve_sections,
    expand_link_ids as _expand_link_ids,
    build_tldr_index as _build_tldr_index,
    BODY_KEY as _BODY_KEY,
    render_frontmatter as _render_frontmatter,
    CANONICAL_SECTIONS as _CANONICAL_SECTIONS,
    rung_for_branch as _rung_for_branch,
    compute_merge_gate_state as _compute_merge_gate_state,
)


_HANDOVER_STATUS_BACKFILL_RAN = False


def _ensure_handover_status_backfilled() -> None:
    """One-shot legacy backfill, at most one write transaction per project."""
    global _HANDOVER_STATUS_BACKFILL_RAN
    if _HANDOVER_STATUS_BACKFILL_RAN:
        return
    bp = _backlog_path()
    if not bp.exists():
        return

    owns_transaction = _active_tx() is None
    if owns_transaction:
        # Read first. A project backfilled in an earlier run must not open
        # BEGIN IMMEDIATE on every handover list and get just to learn that.
        # Only committed state can answer, so this shortcut is skipped when a
        # caller's transaction is open — its dict may hold an uncommitted marker.
        try:
            if _load().get("handover_status_backfilled"):
                _HANDOVER_STATUS_BACKFILL_RAN = True
                return
        except Exception:
            return

    latched = False
    try:
        with _transaction(tool="_ensure_handover_status_backfilled") as data:
            try:
                if not data.get("handover_status_backfilled"):
                    from taskmaster.taskmaster_v3 import backfill_handover_status as _bf
                    tx = _store_tx()
                    flipped = _bf(data, tx.list("handover", include_archived=True))
                    for handover_id, document, body in flipped:
                        tx.put("handover", handover_id, document, body=body)
                    _sync_handover_index_tx(data)
                    _mutate_and_save(data)
                    latched = True
            except Exception:
                # A backfill that cannot read its own inputs stays a no-op, as
                # before. Commit and export failures are raised by the context
                # exit below, outside this guard, so they are never discarded.
                latched = False
    except Exception as exc:
        # The store could not commit or export. This runs from several handover
        # tools, so it must not block them - but it must leave a trace.
        _log_swallowed_error("handover status backfill", exc)
        return

    # Only a transaction this call owned *and* latched proves the durable marker
    # survived. A nested run rides on a caller transaction that can still roll
    # back, so it never sets the flag; the read above is what spares that case
    # the repeated writer lock.
    if owns_transaction and latched:
        _HANDOVER_STATUS_BACKFILL_RAN = True


def _get_open_handovers_for_task(bp: Path, task_id: str) -> list[str]:
    """Open handovers referencing `task_id`, read from the store rows.

    Globbing `handovers/*.md` under-reported whenever the projection was behind
    the rows -- an export that failed its retry, or network storage the store
    cannot export to at all -- so a task looked free of open handovers while
    the store held them.
    """
    rows = _tx_rows("handover") if _active_tx() is not None else _dict_rows(_load(), "handover")
    result = []
    for hid, fm, _body in rows:
        if fm.get("status") == "open" and task_id in (fm.get("task_ids") or []):
            result.append(fm.get("id") or hid)
    return result


def _append_grouped_links_block(
    lines: list[str],
    entity: dict,
    backlog_path: Path,
    *,
    expand_links: bool = False,
) -> None:
    """Append a Plan C grouped `links:` block to `lines` for slim-view rendering.

    Reads `entity.links` (typed array). When `expand_links` is True, swaps bare
    target IDs for `{id} ({tldr})` pills by reading peer entities.
    Emits nothing when there are no typed links.
    """
    from taskmaster.taskmaster_v3 import (
        links_grouped_by_type, read_entity_anywhere,
    )

    grouped = links_grouped_by_type(entity)
    if not grouped:
        return
    lines.append("\n**links:**")
    for ltype in sorted(grouped):
        targets = grouped[ltype]
        if expand_links:
            pills: list[str] = []
            for tgt in targets:
                peer = read_entity_anywhere(backlog_path, tgt) if backlog_path.exists() else None
                tldr = (peer or {}).get("tldr", "") if peer else ""
                pills.append(f"{tgt} ({tldr})" if tldr else tgt)
            lines.append(f"- {ltype}: [{', '.join(pills)}]")
        else:
            lines.append(f"- {ltype}: [{', '.join(targets)}]")


# Link fields expanded by expand_links, per entity kind. Used to give verbose
# (full-frontmatter) reads the same expand_links behavior as slim reads.
_EXPAND_LINK_FIELDS: dict[str, tuple[str, ...]] = {
    "handover": ("task_ids",),
    "issue": ("related_tasks", "fixed_in_task"),
    "idea": ("related_tasks", "related_issues"),
}


def _expand_fm_links(fm: dict, kind: str, backlog_path: Path) -> dict:
    """Return a copy of `fm` with the kind's link fields rewritten from bare IDs
    to readable `id (tldr)` strings. Keeps expand_links behavior identical
    between slim and verbose reads (tm-audit-007). Unknown IDs render bare.
    """
    fields = _EXPAND_LINK_FIELDS.get(kind, ())
    if not fields or not any(fm.get(f) for f in fields):
        return fm
    data = _load()
    tldr_index = _build_tldr_index(
        data, project_root=backlog_path.parent.parent if backlog_path.exists() else None
    )

    def _one(i: str) -> str:
        tldr = tldr_index.get(i)
        return f"{i} ({tldr})" if tldr else i

    out = dict(fm)
    for field in fields:
        val = fm.get(field)
        if not val:
            continue
        if isinstance(val, list):
            out[field] = [_one(str(i)) for i in val]
        else:
            out[field] = _one(str(val))
    return out


def _resolve_paths() -> tuple[Path, Path]:
    """Resolve backlog.yaml and PROGRESS.md paths from config or defaults.

    Priority: .taskmaster/taskmaster.json > .claude/taskmaster.json (legacy)
    > .taskmaster/backlog.yaml > .claude/backlog.yaml (legacy) > ./backlog.yaml

    Guard (tm-audit-001, unconditional — checked before any fallback branch
    so it can't go dead if a later branch starts matching first): refuse to
    resolve when ROOT is literally the plugin's own source directory. A
    backlog.yaml or .taskmaster/ found there is a fixture, not a project.
    """
    if ROOT.resolve(strict=False) == SCRIPT_DIR.resolve(strict=False):
        raise RuntimeError(
            "Refusing to use the taskmaster plugin directory as a project root. "
            "A backlog.yaml adjacent to backlog_server.py is a fixture, not a "
            "project. Run from a project directory or set TASKMASTER_ROOT."
        )
    for cfg_path in (CONFIG_PATH, LEGACY_CONFIG_PATH):
        if cfg_path.exists():
            try:
                config = json.loads(cfg_path.read_text(encoding="utf-8"))
                if cfg_path is LEGACY_CONFIG_PATH:
                    _warn_legacy_layout("config at .claude/taskmaster.json")
                return (
                    ROOT / config.get("backlog_path", "backlog.yaml"),
                    ROOT / config.get("progress_path", "PROGRESS.md"),
                )
            except (json.JSONDecodeError, KeyError):
                pass

    if (ROOT / ".taskmaster" / "backlog.yaml").exists():
        return ROOT / ".taskmaster" / "backlog.yaml", ROOT / ".taskmaster" / "PROGRESS.md"
    if (ROOT / ".claude" / "backlog.yaml").exists():
        _warn_legacy_layout("backlog at .claude/backlog.yaml")
        return ROOT / ".claude" / "backlog.yaml", ROOT / ".claude" / "PROGRESS.md"
    # Legacy: project root (before .taskmaster/ was introduced)
    return ROOT / "backlog.yaml", ROOT / "PROGRESS.md"


from taskmaster.taskmaster_v3 import warn_legacy_layout as _warn_legacy_layout


# Module-level accessors (resolved fresh each call via _load/_save)
def _backlog_path() -> Path:
    return _resolve_paths()[0]


def _progress_path() -> Path:
    backlog, legacy_progress = _resolve_paths()
    try:
        raw = yaml_io.safe_load(backlog.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return legacy_progress
    if _detect_schema_version(raw) >= SCHEMA_V4:
        return backlog.parent / "local" / "PROGRESS.md"
    return legacy_progress


# ── Session identity (unique per MCP server process) ─────
SESSION_ID = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"

import copy as _copy


# ── Store-backed transaction boundary ────────────────────
# SQLite is the runtime authority (see docs/specs/2026-09-04-sqlite-store-design.md).
# One public tool call owns exactly one store transaction; `_load()` hands out the
# transaction's dict, and `_mutate_and_save()` latches it for commit. There is no
# module-level snapshot or file lock any more — those lost writes across threads
# and processes because two callers could read the same baseline and both write.


class _TxFrame:
    """The active transaction for one thread: its dict, latch, renderers and result."""

    __slots__ = (
        "data",
        "latched",
        "renderers",
        "backlog_path",
        "tx",
        "committed",
        "export_warnings",
    )

    def __init__(self, data: dict, backlog_path: "Path"):
        self.data = data
        self.backlog_path = backlog_path
        self.latched = False
        # A stack, not a slot: nested tool calls share this frame, and a nested
        # body's renderer must never become the outer tool's response.
        self.renderers: list = []
        # `export pending: …` notices this transaction raised, appended to the
        # tool result so a caller is never told a write landed when it did not.
        self.export_warnings: list[str] = []
        self.tx = None
        # Filled once the transaction commits: the documents this writer itself
        # committed, keyed by (kind, id).  A response rendered from these can
        # never be spoiled by a later writer, which a fresh re-read could.
        self.committed: dict = {}


class _UnlatchedTransaction(Exception):
    """Internal signal: leave the store transaction without committing."""


# FastMCP runs sync tools in a thread pool, so the active transaction is
# per-thread, never per-module.
_TX_STATE = threading.local()


def _active_tx() -> "_TxFrame | None":
    return getattr(_TX_STATE, "frame", None)


def _configure_store_derivers() -> None:
    """Point the store at this module's pure derivation helpers.

    `store.py` must not import this module (cycle), and `store.reset_for_tests()`
    clears the hooks, so this is re-applied on every store access.
    """
    store.configure_derivers(
        context_builder=_derive_context,
        progress_renderer=_render_progress_dashboard,
    )


def _store_for(backlog_path: "Path | None" = None) -> "store.Store":
    """The store for one backlog path (the resolved project's when omitted)."""
    _configure_store_derivers()
    return store.open_store(backlog_path or _backlog_path())


def _store() -> "store.Store":
    return _store_for()


def _normalize_task(task: dict) -> dict:
    """Backfill `created` and normalize legacy P-code priorities on one task."""
    if not task.get("created"):
        task["created"] = (
            task.get("started") or task.get("completed") or "2025-01-01T00:00"
        )
    priority = task.get("priority", "")
    if priority in _LEGACY_TO_NAME:
        task["priority"] = _LEGACY_TO_NAME[priority]
    return task


_MISSING = object()

_NORMALIZED_FIELDS = ("created", "priority")


def _normalize_loaded(data: dict) -> list[tuple[dict, str, object, object]]:
    """Backfill `created` and normalize legacy P-code priorities in place.

    Returns `(task, field, synthesized, prior)` for every value it invented, so
    a mutating caller can put the entity back the way it found it before the
    write-back diff runs. These are display values, not facts: `created` falls
    back to a fixed sentinel date the task never had.
    """
    invented: list[tuple[dict, str, object, object]] = []
    for epic in data.get("epics", []) or []:
        for task in epic.get("tasks", []) or []:
            prior = {
                field: task.get(field, _MISSING) for field in _NORMALIZED_FIELDS
            }
            _normalize_task(task)
            for field, was in prior.items():
                now = task.get(field, _MISSING)
                if now is not was and now != was:
                    invented.append((task, field, now, was))
    return invented


def _drop_normalization(invented: list[tuple[dict, str, object, object]]) -> None:
    """Undo `_normalize_loaded`'s backfills that nothing else has overwritten.

    The dict write-back diffs against a snapshot taken *before* normalization
    ran, so without this every task the backfill touched counted as edited: one
    `backlog_update_task` produced a second `changes` row and rewrote an
    unrelated task's file, stamping it with a `created` date it never had. On a
    backlog with hundreds of pre-`created` tasks the first write of a session
    rewrote all of them, and "exactly the touched file changed" was false.

    A field the tool itself set no longer holds the synthesized value, so it is
    left alone and persists.
    """
    for task, field, synthesized, prior in invented:
        if task.get(field, _MISSING) != synthesized:
            continue
        if prior is _MISSING:
            task.pop(field, None)
        else:
            task[field] = prior


@contextmanager
def _transaction(*, tool: str, backlog_path: "Path | None" = None):
    """Own one store transaction for the whole of one public tool call.

    Nested calls reuse the active transaction so `_load()` stays identity-stable.
    Leaving the block without `_mutate_and_save()` rolls back, so an early return
    or a validation error can never persist a half-applied mutation.
    """
    prior = _active_tx()
    if prior is not None:
        yield prior.data
        return
    frame = None
    try:
        instance = _store_for(backlog_path)
        with instance.transaction_dict(tool=tool) as data:
            frame = _TxFrame(data, instance.backlog_path / "backlog.yaml")
            frame.tx = store.active_transaction()
            _TX_STATE.frame = frame
            # Cleared up front so a rolled-back transaction cannot leave the
            # previous call's sequence looking like this one's result.
            _TX_STATE.last_seq = None
            _TX_STATE.export_warnings = []
            try:
                invented = _normalize_loaded(data)
                # No context rebuild here: the store's dict loader already
                # derived it, and `_mutate_and_save` re-derives it once on the
                # latch. Doing it on entry as well cost a third full pass over
                # every task for every tool call, mutating or not.
                yield data
                # …and out again before the write-back diff: a backfill is a
                # display value, and persisting it turned one tool call into a
                # rewrite of every task that happened to lack a `created`.
                _drop_normalization(invented)
                if not frame.latched:
                    raise _UnlatchedTransaction
            finally:
                _TX_STATE.frame = None
        if frame is not None and frame.tx is not None:
            frame.committed = dict(frame.tx.committed)
            _TX_STATE.last_seq = frame.tx.seq
            frame.export_warnings = [
                warning
                for warning in frame.tx.warnings
                if warning.startswith("export pending:")
            ]
            _TX_STATE.export_warnings = list(frame.export_warnings)
    except _UnlatchedTransaction:
        pass


def _last_commit_seq() -> int | None:
    """The `changes.seq` this thread's most recent commit ended at.

    The viewer's JSON answers carry it for the same reason tool strings do: a
    caller can tie the response to the exact commit, and a stale reply is
    recognisable instead of merely looking plausible.
    """
    return getattr(_TX_STATE, "last_seq", None)


def _json_with_seq(payload: dict) -> dict:
    seq = _last_commit_seq()
    if seq is not None:
        payload.setdefault("seq", seq)
    notices = getattr(_TX_STATE, "export_warnings", None)
    if notices:
        payload.setdefault("export_pending", list(notices))
    return payload


def _store_tx() -> "store.Transaction":
    """The store transaction behind the active compatibility dict.

    The write-back deliberately ignores entities missing from the dict, so an
    archive can never be expressed by editing the dict alone — every intended
    removal has to go through the transaction explicitly.
    """
    tx = store.active_transaction()
    if tx is None:
        raise RuntimeError(
            "no active store transaction — an explicit archive/delete must run "
            "inside _transaction()"
        )
    return tx


class _AliasExists(Exception):
    """A workspace alias already present when the config lock was taken."""


def _archive_entity(kind: str, ident: str, entity: dict | None = None) -> None:
    """Archive one task/epic/phase in the store, not just in the dict.

    The store row's archive flag is what decides where the projection file
    lives, so a status flip on the dict document is not an archive.
    """
    _store_tx().archive(kind, ident)


def _unarchive_entity(kind: str, ident: str, entity: dict | None = None) -> None:
    """Undo an archive, clearing the dict's `archived` marker as well.

    `put` re-derives the flag from the document when it carries one, so a stale
    `archived` timestamp left in the dict would immediately re-archive the row.
    """
    _store_tx().unarchive(kind, ident)
    if entity is not None:
        entity.pop("archived", None)
        entity.pop("archive_reason", None)


def _apply_archive_transition(
    kind: str, ident: str, entity: dict, *, before: str, after: str
) -> None:
    """Mirror a status transition into or out of `archived` onto the store row."""
    if after == before:
        return
    if after == "archived":
        _archive_entity(kind, ident, entity)
    elif before == "archived":
        _unarchive_entity(kind, ident, entity)


# ── Row access for the non-task kinds ────────────────────────────
# Bugs, issues, handovers, decisions, ideas, notes, areas and trackers live in
# the store like everything else. Reads take their rows off the compatibility
# dict's private `_rows` map; writes take them off the open transaction, so a
# list and the write that follows it always see one snapshot.

_ROW_INDEX_SYNCERS = {
    "bug": ("bugs", _sync_bug_index),
    "issue": ("issues", _sync_issue_index),
    "tracker": ("trackers", _sync_tracker_index),
}


def _dict_rows(data: dict, kind: str, *, include_archived: bool = False) -> list:
    """`(id, doc, body)` rows of one kind from a loaded compatibility dict.

    Archived rows carry `archived: True` on the document itself, so a caller
    that wants only the live set filters here rather than re-globbing a
    directory the store already owns.
    """
    rows = (data.get("_rows") or {}).get(kind) or {}
    out = []
    for ident in sorted(rows):
        doc, body = rows[ident]
        if not include_archived and doc.get("archived"):
            continue
        out.append((ident, doc, body))
    return out


def _dict_row(data: dict, kind: str, ident: str):
    """One `(doc, body)` row, or None. Archived rows are visible."""
    return ((data.get("_rows") or {}).get(kind) or {}).get(ident)


def _tx_rows(kind: str, *, include_archived: bool = False) -> list:
    """`(id, doc, body)` rows of one kind from the open transaction."""
    return _store_tx().list(kind, include_archived=include_archived)


def _tx_doc(kind: str, ident: str) -> tuple[dict, str]:
    """The open transaction's `(document, body)` for one entity.

    Raises KeyError when the entity does not exist, which every caller turns
    into its own not-found message.
    """
    doc = _store_tx().get(kind, ident)
    body = doc.pop(_BODY_KEY, "") or ""
    return doc, body


def _derived_index(kind: str, rows: list) -> list:
    """The index array a kind's rows would produce, without touching the dict.

    Read tools render from this instead of re-syncing the live transaction
    dict — a list is a read and must not leave a mutation behind.
    """
    field, syncer = _ROW_INDEX_SYNCERS[kind]
    holder: dict = {}
    syncer(holder, rows)
    return holder[field]


def _sync_bug_index_tx(data: dict) -> None:
    _sync_bug_index(data, _tx_rows("bug"))


def _sync_issue_index_tx(data: dict) -> None:
    _sync_issue_index(data, _tx_rows("issue"))


def _sync_tracker_index_tx(data: dict) -> None:
    _sync_tracker_index(data, _tx_rows("tracker"))


def _sync_handover_index_tx(data: dict) -> None:
    """Rebuild the handover index and thread registry from the store's rows.

    The transaction is handed in so overflow past the 30-entry cap is archived
    with an explicit `tx.archive` — the file move to `handovers/_archive/<year>/`
    is the exporter's, not a rename behind the store's back.
    """
    tx = _store_tx()
    _sync_handover_index(data, tx.list("handover"), tx=tx)


def _render_after_commit(renderer) -> None:
    """Register a response renderer that runs against this writer's own commit.

    `renderer(committed)` is called after the transaction commits, with the
    `{(kind, id): document}` map the store captured while this writer still held
    the lock.  A fresh re-read would instead show whatever the *next* writer
    left, which reported a perfectly good write as "(not persisted)".
    """
    frame = _active_tx()
    if frame is not None:
        frame.renderers.append(renderer)


def _append_seq(result: str, seq: int | None) -> str:
    """`result [seq N]`, the suffix `_with_seq` puts on a string answer."""
    return f"{result} [seq {seq}]" if seq is not None else result


def _with_seq(result, frame: "_TxFrame"):
    """Stamp the committed `changes.seq` and any export notice onto a result.

    Every mutation is a row in `changes`; naming that row in the response is
    what lets a caller (or a reviewer reading a transcript) tie the answer to
    the exact commit that produced it instead of trusting the prose. An
    `export pending: …` notice rides along for the opposite reason: the commit
    landed but a file the caller can see did not, and silence would read as
    success (spec §3.6).
    """
    seq = getattr(frame.tx, "seq", None) if frame.tx is not None else None
    notices = list(frame.export_warnings)
    if seq is None and not notices:
        return result
    if isinstance(result, dict):
        if seq is not None:
            result.setdefault("seq", seq)
        if notices:
            result.setdefault("export_pending", notices)
        return result
    if not isinstance(result, str):
        return result
    payload = _as_json_result(result)
    if payload is not None:
        # A tool whose result is itself JSON carries both as fields. Appending
        # text would corrupt the payload for every caller that parses it — the
        # Linear tools do exactly that.
        if isinstance(payload, dict):
            if seq is not None:
                payload.setdefault("seq", seq)
            if notices:
                payload.setdefault("export_pending", notices)
            return json.dumps(payload)
        # A JSON array has nowhere to put a field and nowhere safe to put a
        # suffix, so it is left exactly as the tool produced it.
        return result
    for notice in notices:
        result = f"{result} ({notice})"
    return _append_seq(result, seq)


def _as_json_result(result: str):
    """The parsed payload when a tool's string result is itself JSON, else None."""
    stripped = result.lstrip()
    if not stripped[:1] in ("{", "["):
        return None
    try:
        parsed = json.loads(result)
    except ValueError:
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _transactional(tool: str):
    """Wrap a public tool so its whole body runs in one named transaction."""

    def decorate(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            outer = _active_tx()
            if outer is not None:
                # Nested: the outermost tool owns the response. Anything this
                # body registers is discarded when it returns, so a nested
                # renderer can no longer overwrite the outer tool's own.
                depth = len(outer.renderers)
                try:
                    return fn(*args, **kwargs)
                finally:
                    del outer.renderers[depth:]
            if not _backlog_path().exists():
                # No projection yet: behave exactly as before rather than
                # bootstrapping a database next to a missing backlog.
                return fn(*args, **kwargs)
            committed_frame = None
            renderer = None
            with _transaction(tool=tool) as _data:
                result = fn(*args, **kwargs)
                frame = _active_tx()
                if frame is not None and frame.latched:
                    renderer = frame.renderers[-1] if frame.renderers else None
                    committed_frame = frame
            if committed_frame is None:
                return result
            if renderer is not None:
                result = renderer(committed_frame.committed)
            return _with_seq(result, committed_frame)

        return wrapper

    return decorate


def _today() -> str:
    return date.today().isoformat()


def _now() -> str:
    """ISO timestamp with minute precision: YYYY-MM-DDTHH:MM"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


# ── Unified list-read convention ─────────────────────────
# Every list tool/action caps at DEFAULT_LIST_LIMIT rows and emits an overflow
# footer when it truncates, mirroring backlog_list_tasks. limit<=0 means no cap.
DEFAULT_LIST_LIMIT = 50


def _cap_list(entries: list, limit: int) -> tuple[list, int]:
    """Apply the unified limit convention. Returns (capped, overflow_count).

    limit>0 caps at `limit`; limit<=0 returns everything (no cap).
    """
    total = len(entries)
    if limit > 0 and total > limit:
        return entries[:limit], total - limit
    return entries, 0


def _overflow_footer(overflow: int, noun: str) -> str:
    """Standard overflow footer line, or '' when nothing was truncated."""
    if overflow <= 0:
        return ""
    return f"…{overflow} more {noun} — narrow with filters or pass limit=0 for all"


def _validate_date(s: str) -> date | None:
    """Parse YYYY-MM-DD string, return date or None if invalid."""
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _time_remaining(target_date_str: str | None) -> str | None:
    """Return human-readable time remaining/overdue, or None if no target."""
    if not target_date_str:
        return None
    try:
        target = datetime.strptime(str(target_date_str), "%Y-%m-%d").date()
        delta = (target - date.today()).days
        if delta > 0:
            return f"{delta}d remaining"
        elif delta == 0:
            return "due today"
        else:
            return f"{abs(delta)}d overdue"
    except ValueError:
        return None


def _normalize_priority(value: str) -> str:
    """Normalize a priority value: accept both legacy P0-P3 and new names."""
    if value in PRIORITY_NAMES:
        return value
    return _LEGACY_TO_NAME.get(value, value)


def _log_swallowed_error(what: str, exc: BaseException) -> None:
    """Report a failure a tool deliberately survives. Never raises.

    These are best-effort side tasks — a handover backfill, a Linear enqueue —
    whose failure must not break the mutation that triggered them. A bare `pass`
    made them invisible, and the store is the only writer allowed under
    `.taskmaster/`, so the trace goes to stderr, where the MCP host logs it.
    """
    try:
        stamp = datetime.now(timezone.utc).isoformat()
        print(f"{stamp} taskmaster: {what} failed: {exc!r}", file=sys.stderr, flush=True)
    except Exception:  # logging must never break the tool call either
        pass


def _load() -> dict:
    """The compatibility read: the store's dict, or the active transaction's.

    Inside a transaction this returns the very same object every time, so the
    nested loads in complete_task / link expansion / Linear enqueue all mutate
    one shared tree instead of forking a second, stale copy.
    """
    return _load_snapshot()[0]


def _load_snapshot() -> tuple[dict, str]:
    """`_load()` plus the ETag of the snapshot the payload came from.

    A GET that reads the payload and the revision separately can hand out an old
    payload under a newer ETag, and the edit that follows then passes its
    precondition while overwriting the newer state.
    """
    bp = _backlog_path()
    if not bp.exists():
        raise FileNotFoundError(bp)
    data, token, max_seq = _store().load_dict_with_identity()
    _normalize_loaded(data)
    if not data.get("context"):
        regenerate_context(data)
    return data, f"{token}:{max_seq}"


def _write_local_meta_cache(backlog_path: Path, payload: dict) -> None:
    """Write the derived `local/cache/meta.json` convenience file.

    Purely derived and unversioned: nothing in the server, the store or the
    viewer reads it back, and no code path treats it as a source of truth for
    `meta.updated` or anything else. It exists so an external tool can cheaply
    see when the backlog last changed. The bytes leave through the store because
    `store.py` is the only module allowed to write under `.taskmaster/`.
    """
    payload_bytes = (json.dumps(payload, indent=2) + chr(10)).encode("utf-8")
    _store_for(backlog_path).write_local_cache("meta.json", payload_bytes)


def _has_v3_content(data: dict) -> bool:
    """True when the backlog has any v3 narrative-continuity entity content.

    Independent of `schema_version` marker — used by the heuristic that lets
    skill auto-offers fire on backlogs whose marker was never bumped (ISS-001).
    """
    return bool(
        data.get("handovers")
        or data.get("issues")
    )


def _effective_schema_version(data: dict) -> int:
    """schema_version, but treats 'v3 content present, marker missing' as v3.

    Skill gates compare against this so they don't silently skip on backlogs
    that were created with v3 entities before the marker invariant existed.
    """
    declared = _detect_schema_version(data)
    if declared >= SCHEMA_V3:
        return declared
    if _has_v3_content(data):
        return SCHEMA_V3
    return declared


def _find_task(data: dict, task_id: str) -> tuple[dict, dict] | None:
    """Returns (task, epic) or None."""
    for epic in data["epics"]:
        for task in epic.get("tasks", []):
            if task["id"] == task_id:
                return task, epic
    return None


# ── Hot-path task rows ───────────────────────────────────
# The nine task-mutating tools read and write the `("task", id)` row rather
# than relying on the compatibility dict's end-of-transaction diff, so the
# document a tool mutates is the document the store holds, and the response is
# rendered from what the store actually committed.


def _tx_task(data: dict, task_id: str) -> tuple[dict, dict] | None:
    """`(task, epic)` for a hot-path tool, with the task read from its row.

    The dict node is refreshed from the row in place, so navigation helpers
    that walk `data` and the row about to be written cannot disagree — even
    when an earlier writer in this same transaction already touched the task.
    """
    found = _find_task(data, task_id)
    if not found:
        return None
    node, epic = found
    try:
        document = _store_tx().get("task", task_id)
    except (KeyError, RuntimeError):
        # No row yet (a legacy projection, or no transaction at all): the dict
        # node is the only copy there is.
        return node, epic
    document.setdefault("epic", str(epic.get("id") or ""))
    node.clear()
    node.update(document)
    return _normalize_task(node), epic


def _tx_put_task(task: dict, epic: dict | None = None) -> None:
    """Write one task row through the open transaction."""
    document = dict(task)
    if epic is not None:
        document.setdefault("epic", str(epic.get("id") or ""))
    _store_tx().put("task", str(document.get("id") or ""), document)


def _committed_task_field(committed: dict, task_id: str, field: str, default: str = "") -> str:
    """One field of the task this writer committed, as plain text."""
    document = committed.get(("task", task_id))
    if not document or field not in document:
        return default
    return _format_task_field(document[field])


# ── Store-owned entity IO for the generic link/auto-link engine ────────────
# `taskmaster_v3.read_entity_anywhere` / `write_entity_anywhere` dispatch every
# *task* through these two hooks, so the shared link, inverse-sync and
# auto-link machinery commits through a store transaction instead of
# rewriting `tasks/<id>.md` behind the store's back.


def _store_read_entity(backlog_path: Path | None, kind: str, entity_id: str) -> dict | None:
    """A *copy* of the committed (or in-flight) entity document, or None.

    Tasks come off the transaction dict (identity-stable); every other kind
    comes off its store row. Never the live object: `read_entity_anywhere`
    mutates what it returns (it synthesizes a legacy `links` array for
    unmigrated projects) and documents that as read-only, so handing out the
    live document would let the next commit persist that synthesis.
    """
    if kind == "task":
        return _store_read_task(backlog_path, entity_id)
    if _active_tx() is not None:
        try:
            return _store_tx().get(kind, entity_id)
        except KeyError:
            return None
    bp = Path(backlog_path) if backlog_path else _backlog_path()
    if not bp.exists():
        return None
    rows = (_store_for(bp).load_dict().get("_rows") or {}).get(kind) or {}
    row = rows.get(entity_id)
    if row is None:
        return None
    doc, body = row
    entity = deepcopy(doc)
    if body:
        entity[_BODY_KEY] = body
    return entity


def _store_write_entity(backlog_path: Path | None, kind: str, entity: dict) -> None:
    """Persist a whole entity document through the store.

    Joins the caller's transaction when there is one so a link and its inverse
    land in a single commit; opens its own otherwise. Every kind goes through
    here now, so the shared link engine can no longer rewrite an entity file
    behind the store's back.
    """
    if kind == "task":
        _store_write_task(backlog_path, entity)
        return
    document = dict(entity)
    entity_id = document.get("id")
    body = document.pop(_BODY_KEY, None)

    def apply() -> None:
        tx = _store_tx()
        # Upsert, matching the writer this replaced: the link engine and the
        # migration script both hand over whole documents for ids that may not
        # have a row yet.
        try:
            tx.put(kind, entity_id, document, body=body or "")
        except KeyError:
            tx.create(kind, document, body=body or "", requested_id=entity_id)

    if _active_tx() is not None:
        apply()
        return
    with _transaction(
        tool=f"store:write-{kind}",
        backlog_path=Path(backlog_path) if backlog_path else None,
    ) as data:
        apply()
        _mutate_and_save(data)


def _store_read_task(backlog_path: Path | None, task_id: str) -> dict | None:
    """A *copy* of the committed (or in-flight) task document, or None.

    Never the live transaction dict: `read_entity_anywhere` mutates what it
    returns (it synthesizes a legacy `links` array for unmigrated projects) and
    documents that as read-only, so handing out the live document would let the
    next `_mutate_and_save` in the same transaction commit that synthesis.
    `_store_write_task` replaces the whole document, so nothing needs identity.
    """
    frame = _active_tx()
    if frame is not None:
        found = _find_task(frame.data, task_id)
        return deepcopy(found[0]) if found else None
    bp = Path(backlog_path) if backlog_path else _backlog_path()
    if not bp.exists():
        return None
    data = _store_for(bp).load_dict()
    _normalize_loaded(data)
    found = _find_task(data, task_id)
    return deepcopy(found[0]) if found else None


def _store_write_task(backlog_path: Path | None, entity: dict) -> None:
    """Persist a whole task document through the store.

    Joins the caller's transaction when there is one so a link and its inverse
    land in a single commit; opens its own otherwise.
    """
    task_id = entity.get("id")

    def apply(data: dict) -> None:
        found = _find_task(data, task_id)
        if found is None:
            raise KeyError(f"task {task_id!r} not found")
        task = found[0]
        task.clear()
        task.update(entity)
        _mutate_and_save(data)

    frame = _active_tx()
    if frame is not None:
        apply(frame.data)
        return
    with _transaction(
        tool="store:write-task",
        backlog_path=Path(backlog_path) if backlog_path else None,
    ) as data:
        apply(data)


def _auto_link_entity(backlog_path: Path, entity_id: str) -> list[str]:
    """Run inline-mention auto-linking inside one store transaction.

    The inverse `referenced_by` link lands on a *task*, so the target's read and
    write have to share a transaction: reading committed state, letting a peer
    commit, then writing the whole stale document back reverted that peer's
    branch or status change while the link itself survived.
    """
    from taskmaster.taskmaster_v3 import auto_link_on_save  # noqa: PLC0415

    added: list[str] = []
    with _transaction(tool="auto-link") as _data:
        added = auto_link_on_save(backlog_path, entity_id)
    return added


def _configure_entity_io() -> None:
    """Install the task read/write hooks on the shared entity dispatcher."""
    from taskmaster import taskmaster_v3 as _v3  # noqa: PLC0415

    _v3.configure_entity_io(read=_store_read_entity, write=_store_write_entity)


def _auto_link_task_in_tx(data: dict, task_id: str) -> list[str]:
    """Add `references` links for inline ID mentions, inside the open transaction.

    Store-owned entities (tasks, epics, phases) are edited on the transaction
    dict so the link commits atomically with the text that produced it. Targets
    the store does not yet own still round-trip through their own files; spec
    step 3 moves those onto the store too.
    """
    from taskmaster.taskmaster_v3 import (  # noqa: PLC0415 - link helpers
        add_link as _add_link,
        entity_links as _entity_links,
        extract_inline_refs as _extract_inline_refs,
        read_entity_anywhere as _read_entity_anywhere,
        sync_inverse as _sync_inverse,
    )

    found = _find_task(data, task_id)
    if not found:
        return []
    task, _epic = found
    if task.get("auto_link") is False:
        return []
    body = "\n\n".join(
        filter(
            None,
            [
                task.get(_BODY_KEY) or "",
                task.get("notes") or "",
                task.get("review_instructions") or "",
            ],
        )
    )
    refs = _extract_inline_refs(body, self_id=task_id)
    if not refs:
        return []

    bp = _backlog_path()
    existing = {link["target"] for link in _entity_links(task)}
    pending: list[tuple[str, dict | None]] = []
    for target_id in refs:
        if target_id in existing:
            # Any link to this target already exists; auto-detection only adds
            # new targets, so a stronger explicit relation is never downgraded.
            continue
        in_tx_target = _find_in_transaction(data, target_id)
        if in_tx_target is None and _read_entity_anywhere(bp, target_id) is None:
            continue
        _add_link(task, "references", target_id)
        pending.append((target_id, in_tx_target))

    for target_id, in_tx_target in pending:
        if in_tx_target is not None:
            _add_link(in_tx_target, "referenced_by", task_id)
            continue
        try:
            _sync_inverse(bp, source=task_id, target=target_id, type="references")
        except KeyError:
            pass
    return [target_id for target_id, _ in pending]


_MISSING_FIELD = object()
NOT_PERSISTED = "(not persisted)"


def _format_task_field(value) -> str:
    """Render a stored field the way the caller wrote it.

    Structured fields are stored parsed (depends_on and anchors as lists, docs
    as a dict, design_change as a bool) but the tool's response has always
    echoed the caller's flat representation, so keep that shape.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    if isinstance(value, dict):
        return ", ".join(f"{key}:{item}" for key, item in sorted(value.items()))
    return str(value)


def _expected_field(entity: dict, field: str):
    """Snapshot what a tool just wrote, for comparison against the commit.

    The sentinel must keep its identity: deep-copying it produced a different
    object, so a deliberately cleared field compared unequal to itself and a
    successful removal rendered as "(not persisted)".
    """
    stored = entity.get(field, _MISSING_FIELD)
    return stored if stored is _MISSING_FIELD else deepcopy(stored)


def _committed_field_display(
    committed_docs, task_id: str, field: str, expected, kind: str = "task"
) -> str:
    """Render `field` from this writer's own commit, or say it did not land.

    `committed_docs` is the `{(kind, id): document}` map the store captured
    inside the committing transaction.  This never falls back to the caller's
    requested value: echoing a write the store did not keep is exactly the
    failure the committed-state rule exists to catch.  A field the tool
    deliberately removed reads back as an empty string, matching the old
    response for a cleared field.
    """
    document = committed_docs.get((kind, task_id))
    if document is None:
        return NOT_PERSISTED
    committed = document.get(field, _MISSING_FIELD)
    if committed is _MISSING_FIELD or expected is _MISSING_FIELD:
        return "" if committed is expected else NOT_PERSISTED
    if committed != expected:
        return NOT_PERSISTED
    return _format_task_field(committed)


def _find_in_transaction(data: dict, entity_id: str) -> dict | None:
    """The store-owned entity dict for `entity_id`, or None if the store does
    not own that kind yet (bugs, issues, handovers, ideas, ...)."""
    found = _find_task(data, entity_id)
    if found:
        return found[0]
    epic = _find_epic(data, entity_id)
    if epic is not None:
        return epic
    for phase in data.get("phases", []) or []:
        if phase.get("id") == entity_id:
            return phase
    return None


def _find_tasks_by_bundle(data: dict, slug: str) -> list[dict]:
    """All non-archived tasks sharing bundle `slug` (across all epics)."""
    return [t for epic in data.get("epics", [])
              for t in epic.get("tasks", [])
              if t.get("bundle") == slug and t.get("status") != "archived"]


def _bundle_sub_repo_conflict(data: dict, slug: str, sub_repo: str, exclude_id: str = "") -> str | None:
    """Return the ID of the first bundle member whose sub_repo differs from `sub_repo`, or None."""
    for m in _find_tasks_by_bundle(data, slug):
        if m["id"] == exclude_id:
            continue
        if (m.get("sub_repo") or "") != (sub_repo or ""):
            return m["id"]
    return None


def _find_epic(data: dict, epic_id: str) -> dict | None:
    for epic in data["epics"]:
        if epic["id"] == epic_id:
            return epic
    return None


def _find_phase(data: dict, phase_id: str) -> dict | None:
    """Find a phase by ID (exact) or name (case-insensitive, whitespace-normalized)."""
    phases = data.get("phases", [])
    # Exact ID match first
    for ph in phases:
        if ph["id"] == phase_id:
            return ph
    # Fuzzy: case-insensitive name match
    needle = phase_id.strip().lower().replace("-", " ").replace("_", " ")
    for ph in phases:
        name = ph.get("name", "").strip().lower().replace("-", " ").replace("_", " ")
        if name == needle:
            return ph
    # Partial: needle is a substring of the name or vice versa
    for ph in phases:
        name = ph.get("name", "").strip().lower().replace("-", " ").replace("_", " ")
        if needle in name or name in needle:
            return ph
    return None


def _active_phase(data: dict) -> dict | None:
    """Return the currently active phase, or None."""
    for ph in data.get("phases", []):
        if ph.get("status") == "active":
            return ph
    return None


def _phase_task_ids(data: dict, phase_id: str) -> set[str]:
    """Get all task IDs assigned to a phase."""
    ids = set()
    for epic in data["epics"]:
        for t in epic.get("tasks", []):
            if t.get("phase") == phase_id:
                ids.add(t["id"])
    return ids


def _phase_stats(data: dict, phase_id: str) -> dict:
    """Compute stats for a specific phase."""
    counts = {"todo": 0, "in-progress": 0, "in-review": 0, "done": 0, "blocked": 0, "archived": 0}
    for epic in data["epics"]:
        for t in epic.get("tasks", []):
            if t.get("phase") == phase_id:
                s = t.get("status", "todo")
                counts[s] = counts.get(s, 0) + 1
    total = sum(counts.values()) - counts["archived"]
    return {"total": total, **counts}


def _component_rollup(data: dict, epic_id: str) -> dict:
    """Per-component status rollup for an epic, computed on read.

    Returns { <component_key>: {total, done, in-progress, in-review, todo,
    blocked, status}, ..., "_unassigned": {...} }. Components with no tasks
    still appear (status "todo"). `status` is the node color:
      - "done"        : >0 tasks and all done/archived
      - "todo"        : no task started (all todo)
      - "blocked"     : any blocked and none in-progress/in-review
      - "in-progress" : otherwise (work underway)
    """
    epic = _find_epic(data, epic_id)
    declared = list((epic.get("components") or {}) if epic else [])
    buckets: dict[str, dict] = {}

    def _blank() -> dict:
        return {"total": 0, "done": 0, "in-progress": 0, "in-review": 0,
                "todo": 0, "blocked": 0, "archived": 0}

    for key in declared:
        buckets[key] = _blank()
    buckets["_unassigned"] = _blank()

    for t in (epic.get("tasks", []) if epic else []):
        comp = t.get("component")
        key = comp if comp in buckets else "_unassigned"
        st = t.get("status", "todo")
        b = buckets[key]
        b["total"] += 1
        if st in b:
            b[st] += 1

    for b in buckets.values():
        done = b["done"] + b["archived"]
        if b["total"] == 0:
            b["status"] = "todo"
        elif done == b["total"]:
            b["status"] = "done"
        elif b["in-progress"] == 0 and b["in-review"] == 0 and b["blocked"] > 0:
            b["status"] = "blocked"
        elif b["in-progress"] == 0 and b["in-review"] == 0 and done == 0:
            b["status"] = "todo"
        else:
            b["status"] = "in-progress"
    return buckets


def _touch_task(task: dict) -> None:
    """Update last_referenced timestamp on a task."""
    task["last_referenced"] = _now()


def _epic_names(data: dict) -> str:
    return ", ".join(e["id"] for e in data["epics"])


def _validate_area_ref(bp: Path, area: str) -> str | None:
    """Return an error string if `area` doesn't match a known Area id, else None."""
    from taskmaster.taskmaster_v3 import list_area_ids as _list_area_ids
    known_areas = _list_area_ids(bp)
    if area not in known_areas:
        return f"Error: unknown area `{area}`. Valid: {', '.join(known_areas) or '(none defined)'}"
    return None


def _days_since(date_str: str | None) -> str:
    if not date_str:
        return "started date unknown"
    try:
        s = str(date_str)
        # Support both YYYY-MM-DD and YYYY-MM-DDTHH:MM
        d = datetime.fromisoformat(s).date() if "T" in s else datetime.strptime(s, "%Y-%m-%d").date()
        delta = (date.today() - d).days
        return f"{delta}d"
    except ValueError:
        return "?"


def _epic_status_label(status: str) -> str:
    return {"active": "Active", "planned": "Planned", "done": "Done"}.get(status, status.title())


def regenerate_context(data: dict) -> None:
    """Derive `data["context"]` for one tool call.

    Named separately from `_derive_context` so the server-side derivation can be
    counted on its own: the store calls the implementation directly when it
    builds a dict, and that is not a tool-call cost.
    """
    _derive_context(data)


def _derive_context(data: dict) -> None:
    all_tasks = []
    for epic in data["epics"]:
        for t in epic.get("tasks", []):
            all_tasks.append((t, epic))

    in_progress = []
    blocked = []
    done_tasks = []
    status_counts = {"done": 0, "in-progress": 0, "in-review": 0, "todo": 0, "blocked": 0, "archived": 0}

    for t, epic in all_tasks:
        s = t.get("status", "todo")
        status_counts[s] = status_counts.get(s, 0) + 1

        # A hand-edited or half-recovered entity can be missing any of these.
        # The context block is built on every load, so an unguarded index here
        # took down every tool that reads the backlog, not just the dashboard.
        if s in ("in-progress", "in-review"):
            entry = {
                "id": t.get("id", ""),
                "title": t.get("title", ""),
                "epic": epic.get("id", ""),
                "branch": t.get("branch", ""),
            }
            if t.get("locked_by"):
                entry["locked_by"] = t["locked_by"]
            in_progress.append(entry)
        elif s == "blocked":
            blocked.append({
                "id": t.get("id", ""),
                "title": t.get("title", ""),
                "epic": epic.get("id", ""),
                "blockers": t.get("blockers", ""),
            })
        elif s == "done" and t.get("completed"):
            done_tasks.append(t)

    # recent_completed: last 5 by completed date
    done_tasks.sort(key=lambda t: str(t.get("completed", "")), reverse=True)
    recent_completed = [
        {"id": t.get("id", ""), "title": t.get("title", ""),
         "completed": str(t.get("completed", ""))}
        for t in done_tasks[:5]
    ]

    # next_up: top 3 priority todo across active epics, filtered to active phase
    active_ph = _active_phase(data)
    task_statuses: dict[str, str] = {
        t.get("id", ""): t.get("status", "todo") for t, _ in all_tasks
    }
    todo_tasks = []
    for t, epic in all_tasks:
        if t.get("status") != "todo" or epic.get("status") != "active":
            continue
        if active_ph and t.get("phase") != active_ph["id"]:
            continue
        deps = t.get("depends_on", [])
        if isinstance(deps, str):
            deps = [deps]
        if any(task_statuses.get(d, "todo") != "done" for d in deps):
            continue
        todo_tasks.append((t, epic))
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    todo_tasks.sort(key=lambda x: (priority_order.get(x[0].get("priority", "medium"), 9), str(x[0].get("created", ""))))
    next_up = [
        {"id": t["id"], "title": t["title"], "priority": t.get("priority", "medium"), "epic": epic["id"]}
        for t, epic in todo_tasks[:3]
    ]

    # active_epic: epic with most in-progress tasks, tie-break alphabetically
    epic_ip_counts: dict[str, int] = {}
    for t, epic in all_tasks:
        if t.get("status") in ("in-progress", "in-review"):
            epic_ip_counts[epic["id"]] = epic_ip_counts.get(epic["id"], 0) + 1
    if epic_ip_counts:
        active_epic = sorted(epic_ip_counts.items(), key=lambda x: (-x[1], x[0]))[0][0]
    else:
        # fallback: first active epic alphabetically
        active_epics = sorted([e["id"] for e in data["epics"] if e.get("status") == "active"])
        active_epic = active_epics[0] if active_epics else (data["epics"][0]["id"] if data["epics"] else "")

    data["context"] = {
        "active_epic": active_epic,
        "in_progress": in_progress,
        "blocked": blocked,
        "recent_completed": recent_completed,
        "next_up": next_up,
        "stats": {
            "total": sum(status_counts.values()) - status_counts["archived"],
            "done": status_counts["done"],
            "in_progress": status_counts["in-progress"],
            "in_review": status_counts.get("in-review", 0),
            "todo": status_counts["todo"],
            "blocked": status_counts["blocked"],
            "archived": status_counts["archived"],
        },
    }

    # Phase context
    active_ph = _active_phase(data)
    if active_ph:
        ph_stats = _phase_stats(data, active_ph["id"])
        data["context"]["active_phase"] = {
            "id": active_ph["id"],
            "name": active_ph["name"],
            "stats": ph_stats,
            "target_date": active_ph.get("target_date"),
            "start_date": active_ph.get("start_date"),
        }
    else:
        data["context"]["active_phase"] = None

    # Stale tasks
    stale = []
    for ep in data["epics"]:
        for t in ep.get("tasks", []):
            if t.get("status") != "todo":
                continue
            last_ref = t.get("last_referenced")
            if not last_ref:
                continue
            try:
                ref_str = str(last_ref)
                if "T" in ref_str:
                    ref_date = datetime.fromisoformat(ref_str).date()
                else:
                    ref_date = datetime.strptime(ref_str, "%Y-%m-%d").date()
                days_ago = (date.today() - ref_date).days
                if days_ago >= 14:
                    stale.append({"id": t["id"], "title": t["title"], "last_referenced": ref_str, "days_stale": days_ago})
            except (ValueError, TypeError):
                pass
    stale.sort(key=lambda x: x["days_stale"], reverse=True)
    data["context"]["stale"] = stale[:10]


SESSION_LOG_BEGIN = "<!-- taskmaster:session-log -->"
SESSION_LOG_END = "<!-- /taskmaster:session-log -->"


def _render_session_log(entries: list) -> str:
    """The marker-delimited block the store owns inside the changelog section.

    Regenerated whole from the applied log every time, never appended to. The
    export writes PROGRESS.md before its SQL transaction commits, so a failure
    between the two used to leave a paragraph on disk that the retry appended a
    second time; rewriting a delimited region from durable rows makes the retry
    produce the identical file instead.
    """
    paragraphs = []
    for entry in reversed(entries):
        text = (entry.get("text") if isinstance(entry, dict) else entry) or ""
        text = str(text).strip("\n")
        if text:
            paragraphs.append(text)
    body = "\n\n".join(paragraphs)
    return f"{SESSION_LOG_BEGIN}\n\n{body}\n\n{SESSION_LOG_END}\n" if body else (
        f"{SESSION_LOG_BEGIN}\n{SESSION_LOG_END}\n"
    )


def _changelog_section(existing_tail: str, entries: list) -> str:
    """`existing_tail` with the session-log region replaced by `entries`.

    Anything outside the two markers — a hand-written entry, history from
    before the store owned this file — is left exactly as it was found.
    """
    marker = "## Changelog"
    tail = existing_tail[len(marker):] if existing_tail.startswith(marker) else existing_tail
    block = _render_session_log(entries)
    begin = tail.find(SESSION_LOG_BEGIN)
    end = tail.find(SESSION_LOG_END)
    if begin != -1 and end > begin:
        rest = tail[end + len(SESSION_LOG_END):].lstrip("\n")
        suffix = "\n" + rest if rest else ""
        return marker + tail[:begin] + block + suffix
    if not entries:
        return existing_tail or (marker + "\n")
    rest = tail.lstrip("\n")
    suffix = "\n" + rest if rest else ""
    return marker + "\n\n" + block + suffix


def _render_progress_dashboard(
    data: dict, progress_text: str, entries: "list | None" = None
) -> str:
    """Pure renderer: the dashboard for `data` above the changelog section.

    The store calls this after a commit, so PROGRESS.md always reflects
    committed state rather than a caller's in-flight dict.  `entries` is the
    store's applied session log; the region it owns inside the changelog is
    rebuilt from it, so running the export twice writes the same file.
    """
    changelog_marker = "## Changelog"
    idx = progress_text.find(changelog_marker)
    existing_tail = "" if idx == -1 else progress_text[idx:]
    if entries is None:
        changelog_section = existing_tail
    else:
        changelog_section = _changelog_section(existing_tail, entries)

    project_name = data["meta"].get("project", "Project")

    # Build dashboard
    lines = [
        f"# {project_name} Progress\n",
        "> Auto-generated from backlog.yaml — do not edit manually\n",
        "## Dashboard\n",
        "| Workstream | Status | Progress | Current Focus |",
        "|-----------|--------|----------|---------------|",
    ]

    for epic in data["epics"]:
        tasks = epic.get("tasks", [])
        active_tasks = [t for t in tasks if t.get("status") != "archived"]
        done_count = sum(1 for t in active_tasks if t.get("status") == "done")
        total = len(active_tasks)
        # Current focus: first in-progress task
        focus = "—"
        for t in active_tasks:
            if t.get("status") in ("in-progress", "in-review"):
                focus = t["title"]
                break
        lines.append(f"| {epic['name']} | {_epic_status_label(epic.get('status', 'planned'))} | {done_count}/{total} | {focus} |")

    lines.append("")

    # Phase progress
    phases = data.get("phases", [])
    if phases:
        active_ph = _active_phase(data)
        if active_ph:
            ph_stats = _phase_stats(data, active_ph["id"])
            ph_done = ph_stats["done"]
            ph_total = ph_stats["total"]
            remaining = _time_remaining(active_ph.get("target_date"))
            target_info = f" — target: {active_ph['target_date']}" if active_ph.get("target_date") else ""
            if remaining:
                target_info += f" ({remaining})"
            lines.append(f"**Active Phase:** {active_ph['name']} ({ph_done}/{ph_total} done){target_info}")
        # List all phases briefly
        ph_summary = []
        for ph in sorted(phases, key=lambda m: m.get("order", 999)):
            s = ph.get("status", "planned")
            if s == "archived":
                continue
            label = {"active": ">>", "done": "done", "planned": "..."}.get(s, s)
            ph_summary.append(f"{label} {ph['name']}")
        if ph_summary:
            lines.append(f"**Phases:** {' | '.join(ph_summary)}")
        lines.append("")

    ctx = data.get("context", {})
    ip_items = ctx.get("in_progress", [])
    if ip_items:
        ip_str = ", ".join(f"{t['id']} {t['title']}" for t in ip_items)
        lines.append(f"**In Progress:** {ip_str}")
    else:
        lines.append("**In Progress:** —")

    blocked_items = ctx.get("blocked", [])
    if blocked_items:
        bl_str = ", ".join(f"{t['id']} {t['title']}" for t in blocked_items)
        lines.append(f"**Blocked:** {bl_str}")
    else:
        lines.append("**Blocked:** —")

    next_items = ctx.get("next_up", [])
    if next_items:
        nu_str = ", ".join(f"{t['id']} {t['title']} ({t.get('priority', 'medium')})" for t in next_items)
        lines.append(f"**Next Up:** {nu_str}")
    else:
        lines.append("**Next Up:** —")

    lines.append("\n---\n")

    dashboard = "\n".join(lines) + "\n"
    return dashboard + changelog_section


def _mutate_and_save(data: dict) -> None:
    """Latch the active store transaction so its dict is committed on exit.

    Raises outside a transaction, and refuses any dict that is not the active
    transaction's - a foreign dict would be silently dropped, which is exactly
    the write-loss class this migration exists to remove. Projection export and
    the PROGRESS.md dashboard are the store's job once the commit lands.
    """
    frame = _active_tx()
    if frame is None:
        raise RuntimeError(
            "_mutate_and_save() called outside a store transaction; wrap the "
            "mutation in _transaction(tool=...) or @_transactional(...)"
        )
    if data is not frame.data:
        raise RuntimeError(
            "_mutate_and_save() was handed a dict that is not the active store "
            "transaction dict; mutate the dict returned by _load()"
        )
    regenerate_context(data)
    # Derived, unversioned convenience cache the viewer reads; not part of the
    # projection the store owns, so it stays on the server side.
    try:
        _write_local_meta_cache(frame.backlog_path, {"updated": _today()})
    except OSError:
        pass
    frame.latched = True


def _enqueue_linear_push_if_synced(task_id: str, task: dict | None = None) -> None:
    """Best-effort: enqueue a Linear sync push if this project has linear.yaml
    AND the task has a Linear tracker_id. Never raises — Linear sync is
    non-fatal to the local mutation.

    Called from post-mutation hooks (backlog_add_task / update_task /
    complete_task / archive_task). The drain runs separately (manually via
    /linear retry, or eventually automatically via session boundaries).

    Tasks without a tracker_id are silently no-op'd — they're not synced.
    Bootstrap (linear-005) is what links a TM task to a Linear issue by
    populating tracker_id.
    """
    try:
        bp = _backlog_path()
        cfg = _load_linear_config(bp)
        if cfg is None:
            return
        if task is None:
            r = _find_task(_load(), task_id)
            if not r:
                return
            task = r[0]
        tracker_id = task.get("tracker_id")
        if not tracker_id or not str(tracker_id).startswith("linear-"):
            return
        from taskmaster.integrations.linear import worker as _worker
        # Inside the caller's transaction: the queue row and the mutation that
        # produced it commit together, so a rolled-back edit cannot leave a
        # push queued and a committed edit cannot lose one.
        _worker.enqueue(
            _store_tx(), op="task_upsert", target_id=task_id, tracker_id=tracker_id
        )
    except Exception as exc:
        # Sync failures must not break the local mutation -- but a bare `pass`
        # made a broken enqueue invisible: the task changes, no push is queued,
        # and nothing anywhere says why.
        _log_swallowed_error("Linear enqueue", exc)
        # stderr alone is only as durable as the host's console, and nothing
        # reads it back. The same failure goes to `store.log` naming the task
        # that lost its push, which is what `backlog_linear_status` counts.
        try:
            _store_for(_backlog_path()).log_linear_enqueue_failure(task_id, exc)
        except Exception as log_exc:  # the report must not break the write either
            _log_swallowed_error("Linear enqueue failure logging", log_exc)


def _deep_merge(dst: dict, src: dict) -> dict:
    """Recursively merge *src* into *dst* in-place and return *dst*.

    Dict-valued keys are merged recursively; all other values are replaced
    with a deep copy of the source value.
    """
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = deepcopy(v)
    return dst


def _task_context(data: dict, task: dict, epic: dict) -> str:
    """Format task details + epic context for display after pick."""
    lines = [
        f"**Epic:** {epic['name']} — {epic.get('description', '')}",
        f"**Priority:** {task.get('priority', 'medium')}",
    ]
    if task.get("notes"):
        lines.append(f"**Notes:** {task['notes']}")
    if task.get("branch"):
        lines.append(f"**Branch:** {task['branch']}")
    if task.get("blockers"):
        lines.append(f"**Blockers:** {task['blockers']}")
    if task.get("anchors"):
        lines.append(f"**Anchors:** {', '.join(f'`{a}`' for a in task['anchors'])}")
        file_anchors = [a for a in task["anchors"] if not a.startswith(("http", "localhost"))]
        url_anchors = [a for a in task["anchors"] if a.startswith(("http", "localhost"))]
        if file_anchors:
            lines.append(f"  Files: {', '.join(f'`{a}`' for a in file_anchors)}")
        if url_anchors:
            lines.append(f"  URLs: {', '.join(url_anchors)}")

    # Recently completed in same epic
    epic_done = [t for t in epic.get("tasks", []) if t.get("status") == "done"]
    epic_done.sort(key=lambda t: str(t.get("completed", "")), reverse=True)
    if epic_done[:3]:
        lines.append("\n**Recently completed in this epic:**")
        for t in epic_done[:3]:
            lines.append(f"- `{t['id']}` — {t['title']}")

    return "\n".join(lines)


# ── Read-Only Tools ──────────────────────────────────────────


@mcp.tool()
def backlog_status(verbose: bool = False) -> str:
    """Show project dashboard: epic progress table, in-progress tasks, blocked items, next priorities, and stats.

    Args:
        verbose: If True, include archived task count in stats and show up to
            10 stale tasks and all next-up items. Default (slim) mode omits
            archived counts, caps next-up at 5, and caps stale tasks at 3.
    """
    data = _load()
    regenerate_context(data)  # ensure fresh stats without writing
    ctx = data["context"]

    lines = [f"**Schema:** v{_effective_schema_version(data)}\n"]
    if _effective_schema_version(data) < SCHEMA_V4:
        lines.append("Migration available: run `backlog_migrate_v4` for sharded, merge-aware storage.\n")
    lines.append("## Dashboard\n")
    lines.append("| Workstream | Status | Progress | Current Focus |")
    lines.append("|-----------|--------|----------|---------------|")

    for epic in data["epics"]:
        if epic.get("status") == "archived":
            continue
        tasks = epic.get("tasks", [])
        active_tasks = [t for t in tasks if t.get("status") != "archived"]
        done_count = sum(1 for t in active_tasks if t.get("status") == "done")
        total = len(active_tasks)
        focus = "—"
        for t in active_tasks:
            if t.get("status") in ("in-progress", "in-review"):
                focus = t.get("title") or t.get("id") or "—"
                break
        # The dashboard is the first call of every session. One epic with no
        # `name` used to take it down with a KeyError while every neighbouring
        # access here already used `.get`; an unnamed epic is a thing to show,
        # not a reason to show nothing.
        name = epic.get("name") or epic.get("id") or "(unnamed epic)"
        if _epic_stats(data, epic.get("id"))["closeable"]:
            name = f"{name} [closeable]"
        lines.append(f"| {name} | {_epic_status_label(epic.get('status', 'planned'))} | {done_count}/{total} | {focus} |")

    lines.append("")

    # In Progress — split by actual status
    ip = ctx.get("in_progress", [])
    if ip:
        actual_ip = []
        actual_ir = []
        for item in ip:
            result = _find_task(data, item["id"])
            if result and result[0].get("status") == "in-review":
                actual_ir.append((item, result))
            else:
                actual_ip.append((item, result))

        if actual_ip:
            lines.append("**In Progress:**")
            for item, result in actual_ip:
                started = result[0].get("started") if result else None
                lock_label = f" [locked: {item['locked_by']}]" if item.get("locked_by") else ""
                lines.append(f"- `{item['id']}` — {item['title']} ({_days_since(started)}){lock_label}")
        else:
            lines.append("**In Progress:** —")

        if actual_ir:
            lines.append("**In Review (needs your testing):**")
            for item, result in actual_ir:
                started = result[0].get("started") if result else None
                lines.append(f"- `{item['id']}` — {item['title']} ({_days_since(started)})")
    else:
        lines.append("**In Progress:** —")

    # Blocked
    bl = ctx.get("blocked", [])
    if bl:
        lines.append("**Blocked:**")
        for t in bl:
            lines.append(f"- `{t['id']}` — {t['title']}, blocked by: {t.get('blockers', '?')}")
    else:
        lines.append("**Blocked:** —")

    # Next Up — cap at 5 in slim mode
    nu = ctx.get("next_up", [])
    next_up_cap = None if verbose else 5
    if nu:
        lines.append("**Next Up:**")
        for t in (nu if next_up_cap is None else nu[:next_up_cap]):
            lines.append(f"- `{t['id']}` — {t['title']} ({t.get('priority', 'medium')})")
    else:
        lines.append("**Next Up:** —")

    # Stats — omit archived count in slim mode
    s = ctx.get("stats", {})
    stats_line = f"\nTotal: {s.get('total', 0)} | Done: {s.get('done', 0)} | In Progress: {s.get('in_progress', 0)} | In Review: {s.get('in_review', 0)} | Active: {s.get('in_progress', 0) + s.get('in_review', 0)} | Todo: {s.get('todo', 0)} | Blocked: {s.get('blocked', 0)}"
    if verbose and s.get("archived", 0):
        stats_line += f" | Archived: {s['archived']}"
    lines.append(stats_line)

    # Phase info
    phases = data.get("phases", [])
    active_ph = _active_phase(data)
    if active_ph:
        ph_stats = _phase_stats(data, active_ph["id"])
        ph_done = ph_stats["done"]
        ph_total = ph_stats["total"]
        remaining = _time_remaining(active_ph.get("target_date"))
        time_note = f" — {remaining}" if remaining else ""
        lines.append(f"\n**Active Phase:** {active_ph['name']} — {ph_done}/{ph_total} tasks done{time_note}")
        if active_ph.get("description"):
            lines.append(f"  {active_ph['description']}")
    if phases:
        lines.append("\n**Phases:**")
        for ph in sorted(phases, key=lambda m: m.get("order", 999)):
            s = ph.get("status", "planned")
            if s == "archived":
                continue
            ph_st = _phase_stats(data, ph["id"])
            marker = {"active": "▶", "done": "✓", "planned": "○"}.get(s, "?")
            target_note = f", target: {ph.get('target_date')}" if ph.get("target_date") else ""
            lines.append(f"- {marker} **{ph['name']}** ({ph_st['done']}/{ph_st['total']}) — {s}{target_note}")

    # Stale tasks (todo tasks not referenced in 14+ days)
    # cap at 3 in slim mode, 10 in verbose
    stale_cap = 10 if verbose else 3
    stale_tasks = []
    for ep in data["epics"]:
        for t in ep.get("tasks", []):
            if t.get("status") != "todo":
                continue
            last_ref = t.get("last_referenced")
            if not last_ref:
                continue
            try:
                ref_str = str(last_ref)
                if "T" in ref_str:
                    ref_date = datetime.fromisoformat(ref_str).date()
                else:
                    ref_date = datetime.strptime(ref_str, "%Y-%m-%d").date()
                days_ago = (date.today() - ref_date).days
                if days_ago >= 14:
                    stale_tasks.append((t, ep, days_ago))
            except (ValueError, TypeError):
                pass
    if stale_tasks:
        stale_tasks.sort(key=lambda x: x[2], reverse=True)
        lines.append(f"\n**Stale tasks** (not referenced in 14+ days):")
        for t, ep, days in stale_tasks[:stale_cap]:
            lines.append(f"- `{t['id']}` — {t['title']} — stale {days}d ({ep['id']})")
        lines.append("*Still relevant? Archive with `backlog_archive_task`, or refresh with a real update (`backlog_update_task`).*")

    # Verbose: show archived tasks explicitly
    if verbose:
        archived_tasks = []
        for ep in data["epics"]:
            for t in ep.get("tasks", []):
                if t.get("status") == "archived":
                    archived_tasks.append((t, ep))
        if archived_tasks:
            lines.append(f"\n**Archived tasks** ({len(archived_tasks)}):")
            for t, ep in archived_tasks[:20]:
                lines.append(f"- `{t['id']}` — {t['title']} ({ep['id']})")

    return "\n".join(lines)


@mcp.tool()
def backlog_list_tasks(
    epic: str = "",
    status: str = "",
    priority: str = "",
    phase: str = "",
    area: str = "",
    verbose: bool = False,
    limit: int = 50,
) -> str:
    """List tasks with optional filters. Active tasks sort first; output is
    capped at `limit` rows with an overflow footer.

    Args:
        epic: Filter by epic ID
        status: Filter by status: todo, in-progress, in-review, done, blocked
        priority: Filter by priority: critical, high, medium, low
        phase: Filter by phase ID
        area: Filter by area ID
        verbose: If True, include heavy fields (notes) per task entry. Slim
            (default) shows id, title, tldr, priority, epic, and status only.
        limit: Max rows returned (default 50). 0 = no cap.
    """
    data = _load()
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    status_order = {
        "in-progress": 0,
        "in-review": 1,
        "blocked": 2,
        "todo": 3,
        "done": 4,
        "archived": 5,
    }
    results: list[tuple[int, int, str, str, dict]] = []  # (status_rank, priority_rank, created, formatted, task)
    for ep in data["epics"]:
        if epic and ep["id"] != epic:
            continue
        # Hide tasks in archived epics unless explicitly filtering for archived status
        if not status and ep.get("status") == "archived":
            continue
        for t in ep.get("tasks", []):
            if status and t.get("status") != status:
                continue
            # Hide archived tasks unless explicitly filtering for them
            if not status and t.get("status") == "archived":
                continue
            if priority and t.get("priority") != priority:
                continue
            if phase and t.get("phase") != phase:
                continue
            if area and t.get("area") != area:
                continue
            pri = t.get("priority", "medium")
            entry = f"`{t['id']}` — {t['title']} ({pri}, {ep['id']}, {t.get('status', 'todo')})"
            results.append((
                status_order.get(t.get("status", "todo"), 9),
                priority_order.get(pri, 9),
                str(t.get("created", "")),
                entry,
                t,
            ))

    if not results:
        filters = []
        if epic:
            filters.append(f"epic={epic}")
        if status:
            filters.append(f"status={status}")
        if priority:
            filters.append(f"priority={priority}")
        if phase:
            filters.append(f"phase={phase}")
        if area:
            filters.append(f"area={area}")
        return f"No tasks found matching: {', '.join(filters) if filters else 'any'}"

    results.sort(key=lambda x: (x[0], x[1], x[2]))

    total = len(results)
    overflow = 0
    if limit > 0 and total > limit:
        overflow = total - limit
        results = results[:limit]
    header = f"**{total} tasks:**" if not overflow else f"**{total} tasks (showing first {limit}):**"
    footer = (
        f"…{overflow} more tasks — pass status/epic/phase filters or limit=0 for all"
        if overflow
        else ""
    )

    if verbose:
        lines = [header]
        for _, _, _, entry, t in results:
            lines.append(f"- {entry}")
            if t.get("tldr"):
                lines.append(f"  tldr: {t['tldr']}")
            if t.get("notes"):
                lines.append(f"  notes: {t['notes']}")
            if t.get("human_action"):
                lines.append(f"  waiting-on-human: {t['human_action']}")
        if footer:
            lines.append(footer)
        return "\n".join(lines)

    # Slim mode: include tldr inline but omit heavy fields (notes, body)
    lines = [header]
    for _, _, _, entry, t in results:
        tldr = t.get("tldr", "")
        slim_entry = entry
        if tldr:
            slim_entry = f"{entry} — {tldr}"
        if t.get("human_action"):
            slim_entry += f"\n    waiting-on-human: {t['human_action']}"
        lines.append(f"- {slim_entry}")
    if footer:
        lines.append(footer)
    return "\n".join(lines)


@mcp.tool()
def backlog_get_task(
    task_id: str,
    verbose: bool = False,
    sections: list[str] | None = None,
    expand_links: bool = False,
) -> str:
    """Get details for a single task including epic context and related tasks.

    By default returns a slim view (tldr, status, priority, key links) to
    minimise token cost. Use verbose=True for the full body (notes, review
    instructions, docs, spec-review record, epic context). Use sections to
    pull specific named body sections (e.g. ["notes", "spec"]). Use
    expand_links=True to swap dependency/issue IDs for {id, tldr} pills —
    honored in both slim and verbose modes (uniform across all _get tools).

    Args:
        task_id: The task ID (e.g., "ue-plugin-003")
        verbose: If True, include full body fields (notes, review_instructions,
            docs, spec-review, epic context, recently completed tasks).
        sections: Named sections to include (e.g. ["notes", "spec"]).
            Canonical sections for tasks: notes, review_instructions, spec,
            plan, design, analysis, roadmap.
        expand_links: If True, replace bare IDs in depends_on,
            related_issues with {id, tldr} pills.
    """
    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"

    task, epic = result

    bp = _backlog_path()

    # ── sections-only mode ───────────────────────────────────────────────────
    if sections is not None and not sections:
        return "Error: sections=[] requested no sections; pass sections=None for the slim view or name at least one section"
    if sections:
        try:
            sec_data = _resolve_sections(
                task,
                kind="task",
                sections=sections,
                body=task.get(_BODY_KEY, ""),
                project_root=bp.parent.parent if bp.exists() else None,
            )
        except ValueError as exc:
            return f"Error: {exc}"
        lines = [f"## `{task['id']}` — {task['title']}\n"]
        for sec, content in sec_data.items():
            lines.append(f"### {sec}\n{content}")
        return "\n".join(lines)

    # ── slim mode (default) ──────────────────────────────────────────────────
    if not verbose:
        open_handovers = _get_open_handovers_for_task(bp, task_id) if bp.exists() else []
        slim = _slim_entity(task, kind="task", open_handovers=open_handovers or None)

        if expand_links:
            tldr_index = _build_tldr_index(data, project_root=bp.parent.parent if bp.exists() else None)
            for link_field in ("depends_on", "related_issues"):
                if link_field in slim:
                    ids = slim[link_field]
                    if isinstance(ids, str):
                        ids = [x.strip() for x in ids.split(",") if x.strip()]
                    slim[link_field] = _expand_link_ids(ids, tldr_index)

        lines = [f"## `{slim.pop('id')}` — {slim.pop('title', task.get('title', ''))}\n"]
        for k, v in slim.items():
            lines.append(f"**{k}:** {v}")
        # Plan C: emit grouped typed-links block.
        _append_grouped_links_block(lines, task, bp, expand_links=expand_links)
        return "\n".join(lines)

    # ── verbose mode ─────────────────────────────────────────────────────────
    lines = [f"## `{task['id']}` — {task['title']}\n"]

    fields = [
        ("Status", task.get("status", "todo")),
        ("Priority", task.get("priority", "medium")),
        ("Epic", f"{epic['name']} ({epic['id']})"),
        ("Bundle", task.get("bundle", "—")),
        ("Stage", str(task["stage"]) if task.get("stage") is not None else "—"),
        ("Estimate", task.get("estimate", "—")),
        ("Phase", task.get("phase", "—")),
        ("Anchors", ", ".join(task["anchors"]) if task.get("anchors") else "—"),
        ("Sub-repo", task.get("sub_repo", "—")),
        ("Created", str(task.get("created", "—"))),
        ("Started", str(task.get("started", "—"))),
        ("Completed", str(task.get("completed", "—"))),
        ("Branch", task.get("branch", "—")),
        ("Blockers", task.get("blockers", "—")),
        ("Waiting on human", task.get("human_action", "")),
        ("Locked by", task.get("locked_by", "—")),
        ("Review instructions", task.get("review_instructions", "—")),
        ("Notes", task.get("notes", "—")),
    ]
    for label, val in fields:
        if val is not None and str(val) not in ("—", "None", ""):
            lines.append(f"**{label}:** {val}")

    # Show dependencies
    depends_on = task.get("depends_on", [])
    if isinstance(depends_on, str):
        depends_on = [depends_on]
    if depends_on:
        if expand_links:
            tldr_index = _build_tldr_index(data, project_root=bp.parent.parent if bp.exists() else None)
            pills = _expand_link_ids(depends_on, tldr_index)
            lines.append("\n**Depends on:**")
            for pill in pills:
                tldr_str = f" — {pill['tldr']}" if pill.get("tldr") else ""
                lines.append(f"- `{pill['id']}`{tldr_str}")
        else:
            lines.append("\n**Depends on:**")
            for dep_id in depends_on:
                dep_result = _find_task(data, dep_id)
                if dep_result:
                    dep_task, _ = dep_result
                    status = dep_task.get("status", "todo")
                    lines.append(f"- `{dep_id}` — {dep_task['title']} ({status})")
                else:
                    lines.append(f"- `{dep_id}` — NOT FOUND")

    # Show docs references
    task_docs = task.get("docs")
    if task_docs and isinstance(task_docs, dict):
        lines.append("\n**Docs:**")
        for doc_key, doc_path in task_docs.items():
            lines.append(f"- **{doc_key}:** `{doc_path}`")

    # Show epic docs (parent context)
    epic_docs = epic.get("docs")
    if epic_docs and isinstance(epic_docs, dict):
        lines.append("\n**Epic docs:**")
        for doc_key, doc_path in epic_docs.items():
            lines.append(f"- **{doc_key}:** `{doc_path}`")

    # Show spec-review record
    sr = task.get("spec_review")
    if sr and isinstance(sr, dict):
        verdict = sr.get("verdict", "?")
        ts = sr.get("timestamp", "?")
        codex = "yes" if sr.get("codex_used") else "no"
        crit = sr.get("critical_count", 0)
        imp = sr.get("important_count", 0)
        spec_path = sr.get("spec_path", "—")
        lines.append(
            f"\n**Spec review:** {verdict} ({ts}) — codex: {codex}, "
            f"critical: {crit}, important: {imp}, spec: `{spec_path}`"
        )

    # Epic context
    lines.append(f"\n**Epic:** {epic['name']}")
    lines.append(f"**Description:** {epic.get('description', '—')}")

    # Related tasks in same epic
    epic_tasks = epic.get("tasks", [])
    recent_done = [t for t in epic_tasks if t.get("status") == "done"]
    recent_done.sort(key=lambda t: str(t.get("completed", "")), reverse=True)
    if recent_done[:3]:
        lines.append("\n**Recently completed in this epic:**")
        for t in recent_done[:3]:
            lines.append(f"- `{t['id']}` — {t['title']} ({t.get('completed', '?')})")

    next_todo = [t for t in epic_tasks if t.get("status") == "todo" and t["id"] != task_id]
    next_todo.sort(key=lambda t: ({"critical": 0, "high": 1, "medium": 2, "low": 3}.get(t.get("priority", "medium"), 9)))
    if next_todo[:3]:
        lines.append("\n**Next todo in this epic:**")
        for t in next_todo[:3]:
            lines.append(f"- `{t['id']}` — {t['title']} ({t.get('priority', 'medium')})")

    return "\n".join(lines)


# A leftover `local/index.db` from a 5.2.x install, plus the log that shipped
# with it. They are read by nothing now; a rebuild is the one moment we are
# already touching derived state, so it is where they get swept up.
_LEGACY_INDEX_RELPATHS = (Path("local") / "index.db", Path("local") / "index.log")


def _render_derived_report(status: dict, db_file: Path) -> str:
    """Format `Store.derived_status()` as the `backlog_index_status` body."""
    counts = status.get("row_counts") or {}
    rows = " ".join(f"{table}={counts.get(table, 0)}" for table in
                    ("entities", *store.DERIVED_TABLES))
    rebuilt = status.get("rebuilt_at") or "never (kept current per transaction)"
    return "\n".join([
        f"Store: {db_file}",
        f"Rebuilt: {rebuilt}  (loader={yaml_io.LOADER_NAME})",
        f"Rows: {rows}",
    ])


@mcp.tool()
def backlog_index_status(rebuild: bool = False) -> str:
    """Report the state of the store's derived tables (FTS, paths, links, related).

    They live in `.taskmaster/local/store.db` beside the authoritative rows and are
    refreshed inside the transaction of every tool call, so they are never stale.

    Args:
        rebuild: Recompute every derived table from the entity rows before reporting.
            The authoritative tables are not touched and no file is re-read.
    """
    bp = _backlog_path()
    if not bp.exists():
        # Same answer as `backlog_store_status`: a diagnostic must report on a
        # project that has no backlog yet, and must not open a store beside one
        # that does not exist. `rebuild=True` has nothing to rebuild either.
        return f"no backlog found at {bp}"
    st = _store()
    if rebuild:
        # Derived rows only: `entities`, `changes` and `projection` are the
        # authority and a rebuild must never be able to lose one of them.
        st.rebuild_derived()
        for relpath in _LEGACY_INDEX_RELPATHS:
            (bp.parent / relpath).unlink(missing_ok=True)
    return _render_derived_report(st.derived_status(), st.db_path)


def _render_store_report(status: "store.StoreStatus") -> str:
    """Format a `store.StoreStatus` as the `backlog_store_status` body.

    Every field spec §3.8 names gets a line even when it is empty, so a reader
    comparing two reports never has to work out whether a missing line means
    "none" or "this build does not report it".
    """
    def listing(label: str, names) -> str:
        names = list(names)
        shown = f"  {', '.join(names[:10])}" if names else ""
        more = f" (+{len(names) - 10} more)" if len(names) > 10 else ""
        return f"{label}: {len(names)}{shown}{more}"

    lines = [
        f"Store: {status.db_path}",
        f"Root: {status.root}  (resolved via {status.resolution_source or 'unknown'}, "
        f"schema v{status.schema_version}, token {status.creation_token or 'none'})",
        f"Size: db={status.db_size} B  wal={status.wal_size} B  max seq={status.max_seq}",
        listing("Dirty", status.dirty_files),
        listing("Quarantined", status.quarantined_files),
        listing("Corrupt", status.corrupt_files),
        f"Merge conflicts (24 h): {status.merge_conflicts_24h}",
        f"Linear queue: {status.linear_pending} pending",
        f"Warning: {status.warning or 'none'}",
    ]

    lines.append(f"Sessions: {len(status.live_sessions)} live")
    for session in status.live_sessions:
        lines.append(
            f"  {session.get('session')}  pid={session.get('pid')}"
            f"@{session.get('host')}  last_seen={session.get('last_seen')}"
            f"  tool={session.get('current_tool') or '-'}"
        )

    lines.append(f"Changes (last {len(status.recent_changes)}):")
    for change in status.recent_changes:
        lines.append(
            f"  [{change.get('seq')}] {change.get('ts')}  {change.get('tool') or '-'}"
            f"  {change.get('op')} {change.get('kind')}/{change.get('id')}"
        )
    return "\n".join(lines)


@mcp.tool()
def backlog_store_status() -> str:
    """Report the state of the SQLite store (`.taskmaster/local/store.db`).

    The store is the authority: root and how it was resolved, schema version,
    database and WAL size, the last 20 changes, dirty and quarantined
    projection files, live sessions, any filesystem warning, merge conflicts in
    the last 24 hours, databases an earlier recovery moved aside, and the
    pending Linear push count.

    Genuinely read-only: it will not create a store that does not exist yet, and
    it will not move a damaged one aside. Either is reported on the `Warning:`
    line and left for you to act on, because a diagnostic that repairs the
    evidence is worse than one that says nothing.
    """
    bp = _backlog_path()
    if not bp.exists():
        return f"no backlog found at {bp}"
    # No `_configure_store_derivers()`: nothing here exports or regenerates, so
    # the read does not need the derivation hooks and does not install them.
    return _render_store_report(store.read_only_status(bp))


def _render_query_table(description, rows: list, limit: int) -> str:
    """Aligned text table plus the row-count footer `backlog_query` returns."""
    headers = [col[0] for col in description]
    capped = len(rows) > limit
    rows = rows[:limit]
    cells = [[("" if v is None else str(v))[:80] for v in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in cells:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def line(values: list[str]) -> str:
        return "  ".join(v.ljust(widths[i]) for i, v in enumerate(values)).rstrip()

    body = "\n".join([line(headers)] + [line(row) for row in cells])
    footer = f"{limit} rows (capped)" if capped else f"{len(cells)} rows"
    return f"{body}\n{footer}"


@mcp.tool()
def backlog_query(sql: str, limit: int = 50) -> str:
    """Read-only SQL over the backlog store (.taskmaster/local/store.db). Use it to dig deeper
    than the one-line edit hook: closed history for a path, titles, related entities, FTS.

    Tables: entities(kind,id,epic,status,archived,deleted,doc,body,rev,updated_seq) — `doc` is the
      whole document as JSON, so titles and other fields come from json_extract(doc,'$.title')
      entity_paths(kind,id,path,match_kind,source) links(src_kind,src_id,type,dst_kind,dst_id,derived)
      related(a_kind,a_id,b_kind,b_id,via,weight) handover_tasks(handover_id,task_id)
      entity_fts(kind,id,title,body) changes(seq,ts,session,tool,kind,id,op,fields,before,after)
      projection(file,kind,id,content_hash,mtime,size,dirty,quarantined,exported_seq)
      sessions(session,pid,host,started,last_seen,cwd,current_tool)
      linear_queue(seq,op,target_id,tracker_id,payload,state,attempts,last_error,claimed_by,claimed_at)
    kind: task|epic|phase|bug|issue|handover|decision|idea|note|area|tracker. Open statuses: task
    todo|in-progress|blocked|in-review, bug open|adopted, issue open|investigating, handover open.
    Rows with deleted=1 are tombstones — filter them out unless you are reading history.
    Examples:
      SELECT id,status,json_extract(doc,'$.title') AS title FROM entities WHERE kind='bug' AND deleted=0 AND status IN ('open','adopted')
      SELECT e.kind,e.id,e.status FROM entity_paths p JOIN entities e ON e.kind=p.kind AND e.id=p.id WHERE p.path LIKE '%ModelUsageService.cs'
      SELECT id,title FROM entity_fts WHERE entity_fts MATCH 'credit exhaustion' AND kind='handover' ORDER BY bm25(entity_fts) LIMIT 10

    Args:
        sql: one SELECT (or WITH ... SELECT, recursive CTEs allowed). Writes, PRAGMA, ATTACH and
            multiple statements are rejected, as is a forbidden keyword inside a double-quoted
            identifier — quote string values with single quotes. Queries are cut off after 5 s.
        limit: row cap, 1..500 (default 50).
    """
    from taskmaster import query_guard  # noqa: PLC0415

    limit = max(1, min(500, limit))
    con = None
    guard = None
    deadline = None
    started = False
    try:
        statement = query_guard.validate(sql)
        guard = query_guard.Authorizer(query_guard.declared_names(statement))
        st = _store()
        # The tables are the answer here, so nothing else on this path adopts a
        # hand-edited file the way `_load()` does for every other read tool.
        st.scan_for_read()
        con = st.connection
        if con.in_transaction:
            raise ValueError("backlog_query cannot run inside another store transaction")
        # A plain BEGIN takes a read snapshot and no write lock, so a concurrent
        # writer is never blocked by a long query, and the query never sees a
        # half-applied transaction. Every row is fetched before the tool returns:
        # no cursor and no snapshot outlives this call.
        #
        # This is the store's read/write connection -- `store.py` is the only
        # module that opens the database, so there is no `mode=ro` handle to
        # borrow. The authorizer below is therefore load-bearing, not defence in
        # depth: it is the only thing between a crafted statement and a write.
        con.execute("BEGIN")
        started = True
        deadline = query_guard.Deadline(query_guard.QUERY_TIMEOUT_S)
        con.set_authorizer(guard)
        con.set_progress_handler(deadline, query_guard.PROGRESS_INSTRUCTIONS)
        cur = con.execute(f"SELECT * FROM ({statement}) LIMIT {limit + 1}")
        rows = cur.fetchall()
        description = cur.description
        cur.close()
        return _render_query_table(description, rows, limit)
    except (sqlite3.Error, ValueError, OSError, store.LegacyLayoutError) as exc:
        # SQLite reports both an abort and a denial as a bare message with no object,
        # so prefer what the handler and the authorizer actually recorded.
        if deadline is not None and deadline.expired:
            reason = deadline.message
        elif guard is not None and guard.denial:
            reason = f"not authorized: {guard.denial}"
        else:
            reason = str(exc)
        return f"Error: {reason}\n\nSchema: {query_guard.SCHEMA_SUMMARY}"
    finally:
        if con is not None:
            # Both must come off before the rollback: the authorizer would deny
            # the transaction statement, and the handler would abort it.
            con.set_authorizer(None)
            con.set_progress_handler(None, 0)
            if started and con.in_transaction:
                con.rollback()


# Entity kinds `backlog_search` reports; anything else passed in `kinds` is ignored.
_SEARCH_KINDS = ("task", "epic", "bug", "issue", "handover", "decision", "idea")
_SEARCH_LIMIT = 15


def _fts_match_expression(query: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    Every whitespace-separated token becomes a quoted phrase (an embedded `"` is doubled), so
    punctuation like `-`, `:` or a stray quote is data rather than MATCH grammar. Tokens are
    joined by spaces, which FTS5 reads as implicit AND.
    """
    return " ".join('"' + tok.replace('"', '""') + '"' for tok in query.split())


def _render_search_row(row) -> str:
    """One result line. Tasks keep their historic `(priority, epic, status)` tail."""
    eid, kind, status, title, priority, epic = row
    status = status or "todo"
    if kind == "task":
        return f"`{eid}` — {title or ''} ({priority or 'medium'}, {epic or '—'}, {status})"
    return f"`{eid}` — {title or ''} ({kind}, {status})"


def _search_via_index(query: str, kinds: list[str] | None) -> str | None:
    """FTS5 search across every entity kind, or None to tell the caller to fall back.

    Returns None when the store is unreadable or any SQLite error occurs — the substring scan
    can always answer for tasks, so search must never surface an error from here. It reads the
    store's `entity_fts` table and builds nothing: the table is maintained per transaction.
    """
    match = _fts_match_expression(query)
    if not match:
        return None
    # A filter of only unknown kinds searches everything, as it always has.
    selected = [k for k in kinds or () if k in _SEARCH_KINDS] or list(_SEARCH_KINDS)
    con = None
    owns_snapshot = False
    try:
        con = _store().connection
        # The count and the rows are one answer and must come from one snapshot.
        # `_load()` has already released its own, so a commit landing between
        # the two queries produced "1 match" above an empty list.
        owns_snapshot = not con.in_transaction
        if owns_snapshot:
            con.execute("BEGIN")
        # `backlog` and `project` are whole-file documents, not work items: they
        # are indexed so `backlog_query` can reach them, and excluded here so a
        # common word cannot return the entire backlog as one result row.
        where = ("WHERE entity_fts MATCH ? AND e.deleted=0 AND e.kind IN ("
                 + ",".join("?" * len(selected)) + ")")
        params: list[str] = [match, *selected]
        source = ("FROM entity_fts JOIN entities e "
                  f"ON e.kind = entity_fts.kind AND e.id = entity_fts.id {where}")
        total = con.execute(f"SELECT COUNT(*) {source}", params).fetchone()[0]
        if not total:
            # Not "No tasks": this path searches every kind, and `kinds` may
            # have excluded tasks entirely.
            return f"No matches for `{query}`"
        rows = con.execute(
            "SELECT entity_fts.id, e.kind, e.status, entity_fts.title, "
            "json_extract(e.doc,'$.priority') AS priority, e.epic, "
            "bm25(entity_fts) AS rank "
            f"{source} ORDER BY rank LIMIT {int(_SEARCH_LIMIT)}", params).fetchall()
    except (sqlite3.Error, OSError, ValueError, store.LegacyLayoutError):
        return None
    finally:
        if owns_snapshot and con is not None and con.in_transaction:
            con.rollback()

    body = "\n".join(f"- {_render_search_row(tuple(r)[:6])}" for r in rows)
    return f"**{total} match{'es' if total != 1 else ''}** for `{query}`:\n" + body


@mcp.tool()
def backlog_search(query: str, kinds: list[str] | None = None) -> str:
    """Full-text search across every backlog entity — tasks, epics, bugs, issues, handovers,
    decisions and ideas — ranked by relevance (bm25) over the store's FTS index.

    Args:
        query: Search text (case-insensitive). Matched against titles and bodies (notes,
            descriptions, evidence, handover prose). Multiple words are ANDed together.
        kinds: Optional filter, e.g. ["bug", "issue"]. Unknown kinds are ignored; omit for all.
            Valid kinds: task, epic, bug, issue, handover, decision, idea.
    """
    if isinstance(kinds, str):  # tolerate kinds="bug" from a loose caller
        kinds = [kinds]
    # _load() opens the store, which adopts any out-of-band file edit and refreshes the
    # derived tables in the same transaction, so the FTS path below is never behind the files.
    data = _load()

    indexed = _search_via_index(query, kinds)
    if indexed is not None:
        return indexed

    # Fallback: the store is unreadable. Substring scan over tasks only, unchanged.
    q = query.lower()
    scored: list[tuple[int, str]] = []

    for epic in data["epics"]:
        for task in epic.get("tasks", []):
            score = 0
            tid = task.get("id", "")
            title = task.get("title", "")
            notes = task.get("notes", "")
            branch = task.get("branch", "")
            epic_name = epic.get("name", "")
            docs_str = ""
            if isinstance(task.get("docs"), dict):
                docs_str = " ".join(task["docs"].values())

            # Weighted scoring: id/title matches worth more than notes
            if q in tid.lower():
                score += 10
            if q in title.lower():
                score += 8
            if q in epic_name.lower():
                score += 4
            if q in branch.lower():
                score += 3
            if q in docs_str.lower():
                score += 3
            if q in notes.lower():
                score += 1

            if score > 0:
                status = task.get("status", "todo")
                priority = task.get("priority", "medium")
                scored.append((score, f"`{tid}` — {title} ({priority}, {epic['id']}, {status})"))

    if not scored:
        return f"No tasks matching `{query}`"

    scored.sort(key=lambda x: -x[0])
    results = [item for _, item in scored[:_SEARCH_LIMIT]]
    return f"**{len(scored)} match{'es' if len(scored) != 1 else ''}** for `{query}`:\n" + "\n".join(f"- {r}" for r in results)


@mcp.tool()
def backlog_dependencies(task_id: str) -> str:
    """Show the full dependency chain for a task — what it depends on (upstream) and what it unblocks (downstream).

    Args:
        task_id: The task ID (e.g., "cpp-parser-003")
    """
    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"

    task, epic = result
    lines = [f"## Dependencies for `{task_id}` — {task['title']}\n"]

    # Upstream: what this task depends on
    depends_on = task.get("depends_on", [])
    if isinstance(depends_on, str):
        depends_on = [depends_on]

    if depends_on:
        lines.append("**Depends on (upstream):**")
        all_met = True
        for dep_id in depends_on:
            dep_result = _find_task(data, dep_id)
            if dep_result:
                dep_task, dep_epic = dep_result
                status = dep_task.get("status", "todo")
                check = "done" if status == "done" else "pending"
                if status != "done":
                    all_met = False
                lines.append(f"- [{check}] `{dep_id}` — {dep_task['title']} ({status})")
            else:
                all_met = False
                lines.append(f"- [missing] `{dep_id}` — NOT FOUND")
        lines.append(f"\nAll dependencies met: **{'Yes' if all_met else 'No'}**")
    else:
        lines.append("**Depends on:** none")

    # Downstream: what depends on this task
    all_tasks = []
    for ep in data["epics"]:
        for t in ep.get("tasks", []):
            all_tasks.append((t, ep))

    downstream = []
    for t, ep in all_tasks:
        deps = t.get("depends_on", [])
        if isinstance(deps, str):
            deps = [deps]
        if task_id in deps:
            downstream.append((t, ep))

    if downstream:
        lines.append("\n**Unblocks (downstream):**")
        for t, ep in downstream:
            lines.append(f"- `{t['id']}` — {t['title']} ({t.get('status', 'todo')})")
    else:
        lines.append("\n**Unblocks:** nothing")

    return "\n".join(lines)


@mcp.tool()
def backlog_next_available(include_future_phases: bool = False) -> str:
    """Show tasks that are ready to work on — todo tasks in active epics with all dependencies satisfied.
    Sorted by priority, then by creation date. By default only shows tasks from the active phase;
    set include_future_phases=true to see tasks from all phases."""
    data = _load()
    active_ph = _active_phase(data)

    # Build lookup of all task statuses
    task_status: dict[str, str] = {}
    for epic in data["epics"]:
        for task in epic.get("tasks", []):
            task_status[task["id"]] = task.get("status", "todo")

    available: list[tuple[dict, dict]] = []
    blocked_by_deps: list[tuple[dict, dict, list[str]]] = []

    for epic in data["epics"]:
        if epic.get("status") != "active":
            continue
        for task in epic.get("tasks", []):
            if task.get("status") != "todo":
                continue
            # Filter by active phase unless future phases requested
            if active_ph and not include_future_phases and task.get("phase") != active_ph["id"]:
                continue

            # Check dependencies
            deps = task.get("depends_on", [])
            if isinstance(deps, str):
                deps = [deps]

            unmet = [d for d in deps if task_status.get(d, "todo") != "done"]
            if unmet:
                blocked_by_deps.append((task, epic, unmet))
            else:
                available.append((task, epic))

    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    available.sort(key=lambda x: (priority_order.get(x[0].get("priority", "medium"), 9), str(x[0].get("created", ""))))

    lines = ["## Available Tasks\n"]

    if active_ph:
        lines.append(f"*Filtered to phase: **{active_ph['name']}***\n")

    if available:
        lines.append(f"**{len(available)} tasks ready to pick:**")
        for task, epic in available:
            lines.append(f"- `{task['id']}` — {task['title']} ({task.get('priority', 'medium')}, {epic['id']})")
    else:
        if active_ph:
            lines.append(f"No tasks available in phase **{active_ph['name']}** — all tasks are done, in progress, or have unmet dependencies.")
        else:
            lines.append("No tasks available — all todo tasks have unmet dependencies or belong to non-active epics.")

    if blocked_by_deps:
        lines.append(f"\n**{len(blocked_by_deps)} tasks blocked by dependencies:**")
        for task, epic, unmet in blocked_by_deps[:5]:
            unmet_str = ", ".join(f"`{d}`" for d in unmet)
            lines.append(f"- `{task['id']}` — {task['title']} (waiting on {unmet_str})")

    # Show unassigned tasks hint
    if active_ph:
        unassigned = []
        for epic in data["epics"]:
            if epic.get("status") != "active":
                continue
            for task in epic.get("tasks", []):
                if task.get("status") == "todo" and not task.get("phase"):
                    unassigned.append(task)
        if unassigned:
            lines.append(f"\n*{len(unassigned)} todo tasks are not assigned to any phase.*")

    return "\n".join(lines)


@mcp.tool()
def backlog_validate() -> str:
    """Check backlog integrity: dangling dependency refs, missing dates on done tasks,
    docs paths that don't exist on disk, circular deps, and status inconsistencies."""
    data = _load()

    # Build task ID set and lookup
    all_task_ids: set[str] = set()
    all_tasks: list[tuple[dict, dict]] = []
    for epic in data["epics"]:
        for task in epic.get("tasks", []):
            all_task_ids.add(task["id"])
            all_tasks.append((task, epic))

    issues: list[str] = []

    for orphan_id in data.get("_orphan_tasks", []):
        issues.append(
            f"Task `{orphan_id}` names an epic that does not exist "
            "(orphaned `epic:` frontmatter) — fix the task's epic field."
        )

    for task, epic in all_tasks:
        tid = task["id"]

        # 1. Done tasks should have completed date
        if task.get("status") == "done" and not task.get("completed"):
            issues.append(f"`{tid}`: status=done but no `completed` date")

        # 2. In-progress tasks should have started date
        if task.get("status") == "in-progress" and not task.get("started"):
            issues.append(f"`{tid}`: status=in-progress but no `started` date")

        # 3. Dangling dependency references
        deps = task.get("depends_on", [])
        if isinstance(deps, str):
            deps = [deps]
        for dep_id in deps:
            if dep_id not in all_task_ids:
                issues.append(f"`{tid}`: depends_on `{dep_id}` which does not exist")

        # 4. Docs paths that don't exist on disk
        docs = task.get("docs")
        if isinstance(docs, dict):
            for doc_key, doc_path in docs.items():
                # Strip fragment anchor (e.g., #task-6) before path check
                clean_path = doc_path.split("#")[0].strip()
                if not clean_path:
                    issues.append(f"`{tid}`: docs.{doc_key} is empty")
                    continue
                # Skip entries that look like notes rather than paths
                if " " in clean_path and not clean_path.endswith(".md"):
                    issues.append(f"`{tid}`: docs.{doc_key} looks like a note, not a path: `{doc_path[:60]}...`")
                    continue
                full_path = ROOT / clean_path
                if not full_path.exists():
                    issues.append(f"`{tid}`: docs.{doc_key} path not found: `{clean_path}`")

        # 5. Self-dependency
        if tid in deps:
            issues.append(f"`{tid}`: depends on itself")

    # 6. Circular dependency detection (DFS)
    dep_graph: dict[str, list[str]] = {}
    for task, _ in all_tasks:
        deps = task.get("depends_on", [])
        if isinstance(deps, str):
            deps = [deps]
        dep_graph[task["id"]] = [d for d in deps if d in all_task_ids]

    visited: set[str] = set()
    in_stack: set[str] = set()
    cycles_found: list[list[str]] = []

    def dfs(node: str, path: list[str]) -> None:
        if node in in_stack:
            cycle_start = path.index(node)
            cycles_found.append(path[cycle_start:] + [node])
            return
        if node in visited:
            return
        visited.add(node)
        in_stack.add(node)
        for neighbor in dep_graph.get(node, []):
            dfs(neighbor, path + [node])
        in_stack.discard(node)

    for tid in dep_graph:
        if tid not in visited:
            dfs(tid, [])

    for cycle in cycles_found:
        issues.append(f"Circular dependency: {' → '.join(f'`{c}`' for c in cycle)}")

    # 7. Phase validation
    for task, epic in all_tasks:
        tid = task["id"]
        # 8. Phase references that don't exist
        task_ph = task.get("phase")
        if task_ph and not _find_phase(data, task_ph):
            issues.append(f"`{tid}`: phase `{task_ph}` does not exist")

    # 9. Tracker validation: each tracker file's frontmatter is well-formed.
    bp = _backlog_path()
    on_disk_tracker_ids: set[str] = set(_list_tracker_ids(bp))
    for trk_id in on_disk_tracker_ids:
        try:
            fm, _ = _read_tracker(bp, trk_id)
        except OSError as e:
            issues.append(f"tracker `{trk_id}`: cannot read file ({e})")
            continue
        except yaml.YAMLError as e:
            issues.append(f"tracker `{trk_id}`: malformed YAML ({e})")
            continue
        try:
            _validate_tracker_fm(fm)
        except ValueError as e:
            issues.append(f"tracker `{trk_id}`: {e}")

    # 10. Task tracker_id references: must point at a tracker that exists on disk.
    #     Closed-in-Jira trackers stay on disk so this catches typos and bit-rot.
    for task, _epic in all_tasks:
        ref = task.get("tracker_id")
        if ref and ref not in on_disk_tracker_ids:
            issues.append(
                f"`{task['id']}`: tracker_id `{ref}` does not match any tracker file"
            )

    # 11. Issue tracker_id references: same rule as tasks.
    for iss in data.get("issues", []) or []:
        ref = iss.get("tracker_id")
        if ref and ref not in on_disk_tracker_ids:
            issues.append(
                f"issue `{iss.get('id', '?')}`: tracker_id `{ref}` does not match any tracker file"
            )

    # 12. Linear config: validate schema if .taskmaster/linear.yaml exists.
    #     Catches duplicate aliases / token_envs / dangling default_workspace
    #     before they cause runtime sync failures.
    try:
        _load_linear_config(bp)
    except OSError as e:
        issues.append(f"linear.yaml: cannot read file ({e})")
    except yaml.YAMLError as e:
        issues.append(f"linear.yaml: malformed YAML ({e})")
    except ValueError as e:
        issues.append(f"linear.yaml: {e}")

    # Stats summary
    stats = {"total": len(all_tasks), "issues": len(issues)}

    # ── tldr warnings (advisory, not blocking) ─────────────────────────────
    warnings: list[str] = []
    for epic in data.get("epics", []):
        if not epic.get("done_when"):
            warnings.append(
                f"  warning: epic `{epic.get('id', '?')}` has no done_when (pre-4.1 legacy) "
                f"— set one via backlog_update_epic or convert to an area"
            )
        for task in epic.get("tasks", []):
            if not task.get("tldr"):
                warnings.append(
                    f"  warning: task {task['id']} missing tldr — run scripts/backfill_tldr.py"
                )

    # Also scan artifact dirs for missing tldr
    bp = _backlog_path()
    tm_dir = bp.parent
    from taskmaster.taskmaster_v3 import read_task_file as _rtf
    for subdir in ("issues", "handovers", "ideas"):
        d = tm_dir / subdir
        if not d.exists():
            continue
        for path in sorted(d.glob("*.md")):
            try:
                fm, _ = _rtf(path)
            except Exception:
                continue
            if fm.get("id") and not fm.get("tldr"):
                warnings.append(
                    f"  warning: {fm['id']} missing tldr — run scripts/backfill_tldr.py"
                )

    output_parts: list[str] = []
    if issues:
        header = f"**{len(issues)} issue{'s' if len(issues) != 1 else ''} found** across {stats['total']} tasks:\n"
        output_parts.append(header + "\n".join(f"- {i}" for i in issues))
    else:
        output_parts.append(f"All clear — {stats['total']} tasks validated, no issues found.")

    if warnings:
        output_parts.append("## Warnings\n" + "\n".join(warnings))

    return "\n\n".join(output_parts)


# ── Mutating Tools ───────────────────────────────────────────


@mcp.tool()
def backlog_init(project_name: str = "", location: str = "tracked", schema_version: int = 0) -> str:
    """Initialize taskmaster in the current project. Creates config, backlog.yaml, and PROGRESS.md.

    Args:
        project_name: Name for the project. Defaults to the directory name.
        location: Retained for backwards-compatibility. Only "tracked" is accepted —
                  taskmaster always writes to `.taskmaster/` now. Existing
                  `.claude/`-layout projects keep working via the resolver shim;
                  run `backlog_canonicalize_layout` to migrate them.
        schema_version: 0 → use SCHEMA_DEFAULT (v4, sharded per-task storage) —
                  the default for new projects. 2 (single backlog.yaml) and 3
                  (slim index + per-task files) are legacy schemas retained only
                  for migration tooling and tests; do not pick them for a fresh
                  project. v3/v4 init creates the directory layout up front
                  (tasks/, handovers/, issues/, auto/).
    """
    if location == "hidden":
        return (
            "Error: 'hidden' location is no longer supported — taskmaster writes "
            "to `.taskmaster/` now. Re-run with location='tracked' (default), or "
            "if you have an existing `.claude/`-layout project, run "
            "`backlog_canonicalize_layout` to migrate it."
        )
    if location != "tracked":
        return f"Error: location must be 'tracked', got '{location}'"
    if schema_version == 0:
        schema_version = SCHEMA_DEFAULT
    if schema_version not in (SCHEMA_V2, SCHEMA_V3, SCHEMA_V4):
        return f"Error: schema_version must be {SCHEMA_V2}, {SCHEMA_V3}, or {SCHEMA_V4}, got {schema_version}"

    if not project_name:
        project_name = ROOT.name

    # Check if already initialized (check all locations, including legacy .claude/)
    for check_path in [ROOT / ".taskmaster" / "backlog.yaml", ROOT / ".claude" / "backlog.yaml", ROOT / "backlog.yaml"]:
        if check_path.exists():
            rel = check_path.relative_to(ROOT)
            hint = ""
            if check_path.parts[-2:] == (".claude", "backlog.yaml"):
                hint = (
                    "\nNote: this is a legacy `.claude/`-layout project. "
                    "Run `backlog_canonicalize_layout` to migrate it into `.taskmaster/`."
                )
            return (
                f"Already initialized — `backlog.yaml` exists at `{rel}`.\n"
                f"Use `backlog_status` to see the current state.{hint}"
            )

    backlog_rel = ".taskmaster/backlog.yaml"
    progress_rel = (
        ".taskmaster/local/PROGRESS.md"
        if schema_version >= SCHEMA_V4
        else ".taskmaster/PROGRESS.md"
    )

    backlog_abs = ROOT / backlog_rel
    progress_abs = ROOT / progress_rel

    created = []

    # Write config so the server knows where to find files
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config = {"backlog_path": backlog_rel, "progress_path": progress_rel}
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    created.append(".taskmaster/taskmaster.json")

    # Create backlog.yaml
    backlog_abs.parent.mkdir(parents=True, exist_ok=True)
    initial_data = {
        "meta": {
            "project": project_name,
            "schema_version": schema_version,
            "updated": _today(),
        },
        "context": {
            "active_epic": "",
            "in_progress": [],
            "blocked": [],
            "recent_completed": [],
            "next_up": [],
            "stats": {"total": 0, "done": 0, "in_progress": 0, "in_review": 0, "todo": 0, "blocked": 0, "archived": 0},
        },
        "epics": [],
        "phases": [],
    }
    if schema_version >= SCHEMA_V3:
        # v3 adds top-level entity indexes + creates the directory layout.
        initial_data["handovers"] = []
        initial_data["issues"] = []
        # Pre-create directories so first-run tooling has somewhere to write.
        subdirs = ["tasks", "handovers", "issues", "areas"]
        subdirs.extend(["local", "local/cache"] if schema_version >= SCHEMA_V4 else ["auto"])
        for sub in subdirs:
            (backlog_abs.parent / sub).mkdir(parents=True, exist_ok=True)

    if schema_version >= SCHEMA_V4:
        initial_data["meta"].pop("updated", None)
        _write_local_meta_cache(backlog_abs, {"updated": _today()})

    backlog_abs.write_text(
        yaml.dump(initial_data, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    created.append(backlog_rel)

    # Create PROGRESS.md
    progress_content = f"# {project_name} Progress\n\n> Auto-generated from backlog.yaml — do not edit manually\n\n## Dashboard\n\n---\n\n## Changelog\n"
    progress_abs.parent.mkdir(parents=True, exist_ok=True)
    progress_abs.write_text(progress_content, encoding="utf-8")
    created.append(progress_rel)

    schema_label = (
        "v4 (sharded per-task storage)"
        if schema_version >= SCHEMA_V4
        else "v3 (narrative continuity)" if schema_version >= SCHEMA_V3 else "v2 (stable)"
    )
    return (
        f"Initialized taskmaster for **{project_name}** in `.taskmaster/` (trackable in git) on schema {schema_label}.\n"
        f"Created: {', '.join(created)}"
    )


# The row kinds the compatibility dict carries under `_rows`. Tasks, epics and
# phases are not among them — they are counted off the dict's own tree.
_MIGRATION_ROW_KINDS = (
    "bug", "issue", "handover", "decision", "idea", "note", "area", "tracker",
)


def _adopt_project_into_store(tool: str) -> str:
    """Canonicalize the layout if needed, open the store, report what it holds.

    There is no separate v2->v3->v4 file rewrite any more: the store adopts
    whatever schema it finds on first open (design spec decision 9) and every
    later read serves rows, so a migration tool that wrote the projection
    itself would be a second writer racing the one that owns it. What is left
    for these tools to do is the one thing the store cannot do for itself —
    move a `.claude/` or root-layout project to `<root>/.taskmaster`, which the
    store refuses to open — and then report the adopted counts.
    """
    from taskmaster.taskmaster_v3 import canonicalize_layout  # noqa: PLC0415

    unsafe = store.unsafe_storage_reason(ROOT)
    if unsafe:
        return (
            f"Error: refusing to migrate on this storage — {unsafe}. "
            f"Move the project to local disk first."
        )
    summary = canonicalize_layout(ROOT, dry_run=False)
    status = summary["status"]
    if status == "no_backlog":
        return f"Error: no backlog found under {ROOT}. Run `backlog_init` first."
    if status == "ambiguous":
        srcs = ", ".join(summary.get("sources_found", []))
        return (
            f"Error: multiple backlog.yaml files exist ({srcs}). Keep one before "
            f"migrating."
        )
    if status == "conflicts":
        rows = "\n".join(f"  {c['src']}  →  {c['dst']}" for c in summary["conflicts"])
        return (
            "Error: the canonical layout already holds different content. Nothing "
            f"moved. Resolve manually:\n{rows}"
        )
    moved = len(summary.get("moved") or []) if status == "migrated" else 0

    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}. Run `backlog_init` first."
    # Opening the store *is* the migration: the scan imports the projection and
    # the schema is rewritten to v4 under the writer lock.
    data = _load()
    rows = data.get("_rows") or {}
    st = _store().status()

    epics = data.get("epics") or []
    counts = [
        f"task: {sum(len(e.get('tasks') or []) for e in epics)}",
        f"epic: {len(epics)}",
        f"phase: {len(data.get('phases') or [])}",
    ]
    for kind in _MIGRATION_ROW_KINDS:
        total = len(rows.get(kind) or {})
        if total:
            counts.append(f"{kind}: {total}")

    lines = [
        f"Adopted into the store ({tool}).",
        f"- Store: {st.db_path}  (store schema v{st.schema_version}, max seq {st.max_seq})",
        f"- Rows — {', '.join(counts)}",
    ]
    if moved:
        lines.insert(1, f"- Canonicalized `{summary['source']}` → `.taskmaster/`: {moved} file(s) moved.")
    if st.quarantined_files:
        lines.append(f"- Quarantined: {len(st.quarantined_files)} file(s) — {', '.join(st.quarantined_files[:10])}")
    if st.dirty_files:
        lines.append(f"- Dirty: {len(st.dirty_files)} file(s) awaiting export")
    if st.warning:
        lines.append(f"- Warning: {st.warning}")
    lines.append("Run `backlog_store_status` any time for the full report.")
    # Adoption mutates, so its answer names the commit it produced, like every
    # other mutating tool (R6, decision 7). It is not `@_transactional` — the
    # adoption happens inside the store's own bootstrap transaction, which is
    # already closed by the time `_load()` returns — so the seq is taken from
    # the committed state rather than from a frame.
    return _append_seq("\n".join(lines), st.max_seq)


@mcp.tool()
def backlog_migrate_v3() -> str:
    """Adopt this project's backlog into the SQLite store.

    Kept under its historical name so existing skills and docs keep working.
    There is no longer a v2/v3/v4 file rewrite step: the store adopts whatever
    schema it finds when it first opens the project, so this tool moves a
    legacy `.claude/` or root layout into `.taskmaster/` (the only place a store
    may live) and then opens it. Idempotent — running it on an adopted project
    just reports the counts.
    """
    return _adopt_project_into_store("backlog_migrate_v3")


@mcp.tool()
def backlog_migrate_v4() -> str:
    """Adopt this project's backlog into the SQLite store (alias of
    `backlog_migrate_v3` — one adoption path, two historical names)."""
    return _adopt_project_into_store("backlog_migrate_v4")


@mcp.tool()
@_transactional("backlog_backfill_lanes")
def backlog_backfill_lanes(grandfather_active: bool = True) -> str:
    """One-time optional migration: assign a `lane` (by priority) to every task that
    lacks one. For tasks already in-progress/in-review/done, mark their lane's required
    gates skipped(reason="grandfathered") so enforcement never retroactively wedges
    in-flight work. Tasks that already have a lane are left untouched.

    Args:
        grandfather_active: if true, grandfather gates on non-todo tasks (recommended).
    """
    data = _load()
    migrated = 0
    for epic in data.get("epics", []):
        for task in epic.get("tasks", []):
            if task.get("lane"):
                continue
            task["lane"] = _default_lane(task.get("priority", "medium"))
            if grandfather_active and task.get("status") in ("in-progress", "in-review", "done"):
                gates = task.setdefault("gates", {})
                for g in _required_gates(task["lane"]):
                    if not _gate_satisfied(gates.get(g)):
                        gates[g] = {"skipped": True, "reason": "grandfathered",
                                    "by": "migration", "at": _now()}
            task["gate_state"] = _compute_gate_state(task)
            migrated += 1
    if migrated:
        _mutate_and_save(data)
    return f"Backfilled lanes on {migrated} task(s) (grandfather_active={grandfather_active})."


@mcp.tool()
def backlog_canonicalize_layout(dry_run: bool = False) -> str:
    """Migrate the v3 backlog from `.claude/` or root layout into canonical `.taskmaster/`.

    Moves backlog.yaml + the artifact subdirs (tasks, handovers, issues,
    auto, PROGRESS.md, viewer.json) into `.taskmaster/`.
    Idempotent: re-running on a canonical layout is a no-op. Refuses to clobber:
    if a destination file already holds different content, nothing moves and the
    conflicts are reported. After a successful `.claude/` migration, the redundant
    `.claude/taskmaster.json` config is deleted.

    Use this once per project to fix ISS-004 silent divergence between the v3
    handover writer (which uses `bp.parent / "handovers"`) and readers that
    historically hard-coded `.taskmaster/`.

    Args:
        dry_run: When true, returns the move plan without modifying anything.
    """
    from taskmaster.taskmaster_v3 import canonicalize_layout
    # Projection-only (network) storage has no store to migrate into and no
    # writer lock to serialize this move; refuse rather than half-migrate.
    unsafe = store.unsafe_storage_reason(ROOT)
    if unsafe:
        return (
            f"Error: refusing to canonicalize the layout on this storage — "
            f"{unsafe}. Move the project to local disk first."
        )
    summary = canonicalize_layout(ROOT, dry_run=dry_run)
    status = summary["status"]

    if status == "no_backlog":
        return "No backlog.yaml found — nothing to canonicalize."
    if status == "already_canonical":
        return f"Already canonical at `{summary['destination']}` — no changes."
    if status == "ambiguous":
        srcs = ", ".join(summary.get("sources_found", []))
        return (
            f"Ambiguous: multiple backlog.yaml files exist ({srcs}). Resolve by "
            f"keeping only one before canonicalizing."
        )
    if status == "conflicts":
        lines = [
            f"  {c['src']}  →  {c['dst']}" for c in summary["conflicts"]
        ]
        return (
            f"Conflicts — destination already holds different content. "
            f"Nothing moved. Resolve manually:\n" + "\n".join(lines)
        )
    if status == "would_migrate":
        moves = summary.get("would_move", [])
        lines = [f"  {m['src']}  →  {m['dst']}" for m in moves]
        head = (
            f"Dry run: would move {len(moves)} file(s) from `{summary['source']}` "
            f"layout into `{summary['destination']}`."
        )
        if summary["skipped_already_at_dst"]:
            head += (
                f"\n{len(summary['skipped_already_at_dst'])} file(s) already at "
                f"destination would be cleaned up."
            )
        return head + ("\n" + "\n".join(lines) if lines else "")
    # status == "migrated"
    moved = summary["moved"]
    out = [
        f"Canonicalized v3 layout: `{summary['source']}` → `.taskmaster/`.",
        f"Moved {len(moved)} file(s).",
    ]
    if summary["skipped_already_at_dst"]:
        out.append(
            f"Cleaned up {len(summary['skipped_already_at_dst'])} duplicate file(s) "
            f"already at destination."
        )
    if summary["deleted_config"]:
        out.append(f"Deleted redundant config: `{summary['deleted_config']}`.")
    return "\n".join(out)


def _handover_create_in_tx(
    *,
    tldr: str,
    next_action: str = "",
    body: str = "",
    task_ids: list | None = None,
    session_kind: str = "continuity",
    thread: str | None = None,
    when: str | None = None,
    context_size_at_write: str | None = None,
    supersedes: str | None = None,
    branch: str | None = None,
    tip_commit: str | None = None,
    flag_for_review: bool = False,
    review_reason: str = "",
    open_decisions: list | None = None,
    resolved_this_session: list | None = None,
):
    """Create one handover row and everything that must commit with it.

    Returns `(handover_id, superseded_warning | None)`, or an error string. The
    supersession, the review flag and the `open_decisions` back-references all
    ride the caller's transaction, so a handover that names a decision can never
    half-land. Shared with the test seeding shim so both drive one code path.
    """
    tx = _store_tx()
    try:
        document, handover_body = _build_handover_doc(
            tldr=tldr,
            next_action=next_action,
            body=body,
            task_ids=task_ids or [],
            session_kind=session_kind,
            thread=thread,
            when=when,
            context_size_at_write=context_size_at_write,
            supersedes=supersedes,
            branch=branch,
            tip_commit=tip_commit,
            open_decisions=open_decisions,
            resolved_this_session=resolved_this_session,
        )
        hid = tx.create("handover", document, body=handover_body)
    except ValueError as exc:
        return str(exc)

    superseded_warning = None
    if supersedes:
        try:
            old_doc, old_body = _tx_doc("handover", supersedes)
        except KeyError:
            superseded_warning = (
                f"WARNING: supersedes={supersedes} not found on disk; old "
                f"handover not updated."
            )
        else:
            new_doc, new_body = _supersede_handover_doc(old_doc, old_body, new_id=hid)
            tx.put("handover", supersedes, new_doc, body=new_body)

    if flag_for_review:
        flagged_doc, flagged_body = _tx_doc("handover", hid)
        tx.put(
            "handover",
            hid,
            _flag_handover_doc_for_review(flagged_doc, review_reason=review_reason or ""),
            body=flagged_body,
        )

    # A handover that names open decisions back-references itself on each one,
    # inside this same transaction so the pair can never half-land.
    for decision_id in document.get("open_decisions") or []:
        try:
            decision_doc, decision_body = _tx_doc("decision", decision_id)
        except KeyError:
            continue  # decision was deleted; don't fail the handover write
        linked = _link_decision_doc_to_handover(decision_doc, hid)
        if linked is not None:
            tx.put("decision", decision_id, linked, body=decision_body)

    data = _load()
    _sync_handover_index_tx(data)
    _mutate_and_save(data)
    return hid, superseded_warning


@mcp.tool()
@_transactional("backlog_handover_create")
def backlog_handover_create(
    tldr: str,
    next_action: str = "",
    body: str = "",
    task_ids: list[str] | None = None,
    session_kind: str = "end-of-day",
    thread: str = "",
    supersedes: str = "",
    flag_for_review: bool = False,
    options: dict | None = None,
) -> str:
    """Write a session handover — a committed markdown artifact for cross-session
    continuity. Capture end-of-session context: decisions, blockers, where to
    start next. body is freeform markdown (Decisions / Blockers / Where I'd
    start / Open threads).

    Args:
        tldr: One-line summary. Required.
        next_action: One-line "where to start next session."
        body: Markdown body (the four-section narrative).
        task_ids: Tasks this handover relates to (surfaces in pick-task).
        session_kind: One of {", ".join(HANDOVER_KINDS)}.
        thread: Thread this handover belongs to (stable resume token). Auto-derived from bundle/epic/task/tldr when empty.
        supersedes: Optional id of an older handover this one supersedes; the old
            one gets a `superseded_by:` field and a SUPERSEDED callout.
        flag_for_review: When True, flags this handover for retro extraction.
        options: Rarely-set fields — branch, tip_commit (frontmatter git
            context), context_size_at_write (compaction marker), review_reason
            (used only when flag_for_review).
    """
    options = options or {}
    branch = options.get("branch", "") or ""
    tip_commit = options.get("tip_commit", "") or ""
    context_size_at_write = options.get("context_size_at_write", "") or ""
    review_reason = options.get("review_reason", "") or ""
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}. Run `backlog_init` first."
    _ensure_handover_status_backfilled()
    data = _load()
    thread_name = (thread or "").strip()
    if not thread_name:
        bundle = _get_session_bundle() or {}
        thread_name = _derive_thread_name(
            task_ids or [], tldr, data, bundle_slug=bundle.get("slug", "") or ""
        )
    outcome = _handover_create_in_tx(
        tldr=tldr,
        next_action=next_action,
        body=body,
        task_ids=task_ids or [],
        session_kind=session_kind,
        thread=thread_name,
        context_size_at_write=context_size_at_write or None,
        supersedes=supersedes or None,
        branch=branch or None,
        tip_commit=tip_commit or None,
        flag_for_review=flag_for_review,
        review_reason=review_reason,
    )
    if isinstance(outcome, str):
        return f"Error: {outcome}"
    hid, superseded_warning = outcome
    target = _handover_path(bp, hid)
    data = _load()

    # Plan C: auto-detect inline ID mentions, materialize as `references` links.
    try:
        _auto_link_entity(bp, hid)
    except Exception:
        pass

    lines = [
        f"Handover written: {hid}",
        f"- File: {target.relative_to(ROOT)}",
        f"- Path: {target.resolve()}",
        f"- Index entries: {len(data.get('handovers') or [])}",
    ]
    if supersedes and not superseded_warning:
        lines.append(f"- Superseded: {supersedes}")
    if superseded_warning:
        lines.append(f"- {superseded_warning}")
    if flag_for_review:
        lines.append(f"- Flagged for review: {review_reason}")
    lines.append(f"Resume: {thread_name} — {next_action or tldr}")
    return "\n".join(lines)


@mcp.tool()
def backlog_handover_list(
    task_id: str = "",
    session_kind: str = "",
    since: str = "",
    status: str = "all",
    limit: int = DEFAULT_LIST_LIMIT,
    verbose: bool = False,
) -> str:
    """List recent handovers. By default shows slim one-liners (id, date, tldr).

    Reads from the backlog.yaml index, which is bounded to the most recent 30.
    Older handovers are still on disk under handovers/_archive/ but not listed
    here — fetch by id with `backlog_handover_get` if needed.

    Args:
        task_id: If set, only entries whose `task_ids` list contains this id.
        session_kind: If set, only entries with this session_kind
            (e.g. "end-of-day", "context-handoff", "milestone-complete").
        since: ISO date string (YYYY-MM-DD). If set, only entries whose
            date prefix is >= since. Raises ValueError for invalid formats.
        status: One of open, closed, superseded, or "all" (default). Filters
            against the index entry — does not read every file.
        limit: Max entries returned (default 50). 0 = no cap. An overflow footer
            reports how many were hidden.
        verbose: If True, include additional index fields (next_action, task_ids,
            status) per entry. Slim (default) shows id, date, kind, and tldr.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    _ensure_handover_status_backfilled()
    data = _load()
    entries = list(data.get("handovers") or [])

    # Validate `since` before filtering so we fail fast on bad input.
    if since:
        from datetime import date as _date
        try:
            _date.fromisoformat(since)
        except ValueError:
            return f"Error: `since` must be a date in YYYY-MM-DD format, got {since!r}."

    # Apply filters in spec order.
    if task_id:
        entries = [e for e in entries if task_id in e.get("task_ids", [])]
    if session_kind:
        entries = [e for e in entries if e.get("session_kind") == session_kind]
    if since:
        entries = [e for e in entries if e.get("date", e.get("id", "")) >= since]

    from taskmaster.taskmaster_v3 import HANDOVER_STATUSES as _STATUSES
    if status and status != "all":
        if status not in _STATUSES:
            return f"Error: status must be one of {_STATUSES} or 'all', got {status!r}."
        entries = [e for e in entries if e.get("status") == status]

    if not entries:
        filtered = any([task_id, session_kind, since, status != "all"])
        return "No handovers match those filters." if filtered else "No handovers yet."

    # Cap after all filters (limit<=0 = no cap) and report overflow.
    entries, overflow = _cap_list(entries, limit)
    footer = _overflow_footer(overflow, "handovers")

    lines = []
    for e in entries:
        kind = e.get("session_kind", "")
        tag = f" [{kind}]" if kind else ""
        when = e.get("created") or e.get("date") or ""
        when_tag = f" ({when})" if when else ""
        flag = e.get("flag_reason", "")
        flag_tag = f" ▸ FLAGGED: {flag}" if flag else ""
        lines.append(f"- {e['id']}{when_tag}{tag} — {e.get('tldr', '')}{flag_tag}")
        if verbose:
            if e.get("next_action"):
                lines.append(f"  next: {e['next_action']}")
            tids = e.get("task_ids") or []
            if tids:
                lines.append(f"  tasks: {', '.join(tids)}")
            if e.get("status"):
                lines.append(f"  status: {e['status']}")
    if footer:
        lines.append(footer)
    return "\n".join(lines)


@mcp.tool()
def backlog_handover_get(
    handover_id: str,
    verbose: bool = False,
    sections: list[str] | None = None,
    expand_links: bool = False,
) -> str:
    """Read a handover's content.

    By default returns a slim view (frontmatter fields only, no body) to
    minimise token cost. Use verbose=True to include the full markdown body.
    Use sections to pull specific named body sections (decisions, notes,
    blockers, where_id_start). Use expand_links=True to expand task_ids to
    `id (tldr)` — honored in both slim and verbose modes.

    Use when start-session shows a handover tldr that you want to read in full,
    or when picking a task that has linked handovers.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    _ensure_handover_status_backfilled()
    # The row map carries archived handovers too, so the old `_archive/` rglob
    # fallback is gone with the file read it backed up.
    row = _dict_row(_load(), "handover", handover_id)
    if row is None:
        return f"Handover not found: {handover_id}"
    fm, body = row[0], row[1] or ""

    # ── sections-only mode ───────────────────────────────────────────────────
    if sections is not None and not sections:
        return "Error: sections=[] requested no sections; pass sections=None for the slim view or name at least one section"
    if sections:
        try:
            sec_data = _resolve_sections(fm, kind="handover", sections=sections, body=body)
        except ValueError as exc:
            return f"Error: {exc}"
        lines = [f"## Handover: {handover_id}\n"]
        for sec, content in sec_data.items():
            lines.append(f"### {sec}\n{content}")
        return "\n".join(lines)

    # ── verbose mode ─────────────────────────────────────────────────────────
    if verbose:
        vfm = _expand_fm_links(fm, "handover", bp) if expand_links else fm
        fm_lines = [f"  {k}: {v}" for k, v in vfm.items()]
        return "---\n" + "\n".join(fm_lines) + "\n---\n" + body

    # ── slim mode (default) ──────────────────────────────────────────────────
    slim = _slim_entity(fm, kind="handover")

    if expand_links:
        data = _load()
        tldr_index = _build_tldr_index(data, project_root=bp.parent.parent if bp.exists() else None)
        task_ids = slim.get("task_ids") or []
        if task_ids:
            slim["task_ids"] = _expand_link_ids(task_ids, tldr_index)

    lines = [f"## Handover: {slim.pop('id', handover_id)}\n"]
    for k, v in slim.items():
        lines.append(f"**{k}:** {v}")
    # Plan C: emit grouped typed-links block.
    _append_grouped_links_block(lines, fm, bp, expand_links=expand_links)
    return "\n".join(lines)


@mcp.tool()
@_transactional("backlog_handover_resync")
def backlog_handover_resync() -> str:
    """Rebuild the handover index in backlog.yaml from disk.

    Useful after manual edits to the handovers/ directory (deletes, renames),
    or to enforce the 30-entry cap and archive overflow.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    _ensure_handover_status_backfilled()
    data = _load()
    from taskmaster.taskmaster_v3 import backfill_threads as _backfill_threads
    tx = _store_tx()
    backfill = _backfill_threads(tx.list("handover"), backlog_data=data)
    for handover_id, document, body in backfill["stamped"]:
        tx.put("handover", handover_id, document, body=body)
    _sync_handover_index_tx(data)
    _mutate_and_save(data)
    n = len(data.get("handovers") or [])
    extra = f" Backfilled thread on {len(backfill['stamped'])} legacy handover(s)." if backfill["stamped"] else ""
    return f"Handover index resynced — {n} entries in `backlog.yaml`.{extra}"


def _threads_data(bp: Path) -> dict:
    """Backlog state with the thread index present, taking no writer lock to read.

    Listing threads is a read.  Entering a transaction unconditionally queued
    every listing behind the writer and, on projection-only (network) storage
    where the store refuses to write at all, failed outright.  The one-off
    backfill still commits when it is genuinely missing; on a read-only store it
    is derived in memory and left uncommitted.
    """
    data = _load()
    if "threads" in data:
        return data
    try:
        with _transaction(tool="backlog_thread_index_backfill") as tx_data:
            if "threads" not in tx_data:
                _sync_handover_index_tx(tx_data)
                _mutate_and_save(tx_data)
    except RuntimeError:
        # Projection-only storage: serve the derived index without persisting it.
        _sync_handover_index(data, _dict_rows(data, "handover"))
        return data
    return _load()


@mcp.tool()
def backlog_thread_list(include_closed: bool = False) -> str:
    """The thread board — open (and parked) lines of work with their stable
    resume tokens. Resume one with `backlog_thread_resume(<name>)`.

    Args:
        include_closed: Also list closed threads (default False).
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    data = _threads_data(bp)
    from taskmaster.taskmaster_v3 import list_threads as _list_threads
    rows = _list_threads(data)
    if not include_closed:
        rows = [r for r in rows if r["status"] != "closed"]
    if not rows:
        return "No open threads. Write a handover to start one."
    lines = []
    for r in rows:
        stale = f" · {r['staleness_days']}d" if r["staleness_days"] else ""
        park = " [parked]" if r["status"] == "parked" else ""
        branch = f" · {r['branch']}" if r["branch"] else ""
        lines.append(f"- **{r['name']}**{park}{stale}{branch} — {r['tldr']}")
        if r["next_action"]:
            lines.append(f"  next: {r['next_action']}")
        if r["task_ids"]:
            lines.append(f"  tasks: {', '.join(r['task_ids'])}")
    lines.append("\nResume: `backlog_thread_resume(\"<name>\")`")
    return "\n".join(lines)


@mcp.tool()
def backlog_thread_resume(ref: str) -> str:
    """Resume a thread: returns its newest handover in full (frontmatter +
    body) in one call. `ref` is a thread name OR any handover id (stale dated
    slugs still land on the thread's newest handover).
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    data = _threads_data(bp)
    from taskmaster.taskmaster_v3 import resolve_thread as _resolve_thread
    try:
        tname, hid = _resolve_thread(data, bp, ref)
    except KeyError:
        return (f"No thread or handover matches {ref!r}. "
                f"See `backlog_thread_list()` for open threads.")
    row = _dict_row(data, "handover", hid)
    if row is None:
        return f"Thread {tname!r} resolved to {hid}, but no such handover exists."
    fm, body = row
    fm = {key: value for key, value in fm.items() if key != _BODY_KEY}
    body = body or ""
    t = (data.get("threads") or {}).get(tname) or {}
    header = [
        f"# Thread: {tname or '(none — standalone handover)'}",
        f"- status: {t.get('status', 'open')}" if tname else "",
        f"- handovers: {len(t.get('handover_ids') or []) or 1}",
        f"- newest: {hid}",
        "",
    ]
    fm_lines = [f"  {k}: {v}" for k, v in fm.items()]
    return "\n".join(x for x in header if x is not None) + "---\n" + "\n".join(fm_lines) + "\n---\n" + body


@mcp.tool()
@_transactional("backlog_thread_update")
def backlog_thread_update(name: str, status: str, reason: str = "") -> str:
    """Set a thread's status: open / parked / closed. Writing a new handover
    into the thread later auto-reopens it (override expires)."""
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    data = _load()
    if "threads" not in data:
        _sync_handover_index_tx(data)
    from taskmaster.taskmaster_v3 import update_thread_status as _update_thread_status
    try:
        _update_thread_status(data, bp, name=name, status=status, reason=reason)
    except ValueError as exc:
        return f"Error: {exc}"
    except KeyError:
        return f"Error: no thread named {name!r}. See `backlog_thread_list()`."
    _mutate_and_save(data)
    return f"Thread {name} → {status}." + (f" ({reason})" if reason else "")


# ── Plan C: typed-link MCP tools (spec §6) ─────────────────────────────


@mcp.tool()
def backlog_link(
    action: Literal["create", "remove", "query", "validate", "reconcile"],
    source: str = "",
    target: str = "",
    type: str = "",
    note: str = "",
    depth: int = 1,
) -> str:
    """Manage typed links between entities. The server writes the inverse side
    automatically and rejects invalid types, kind mismatches, and depends_on
    cycles.

    Params by action: create(source, target, type, note);
    remove(source, target, type); query(source, target, type, depth);
    validate(); reconcile().
    """
    if action == "create":
        return backlog_link_create(source, target, type, note)
    if action == "remove":
        return backlog_link_remove(source, target, type)
    if action == "query":
        return backlog_link_query(source, target, type, depth)
    if action == "validate":
        return backlog_link_validate()
    if action == "reconcile":
        return backlog_link_reconcile()
    return json.dumps({"error": f"unknown action {action!r}"})


@_transactional("backlog_link_create")
def backlog_link_create(source: str, target: str, type: str, note: str = "") -> str:
    """Create a typed link from `source` to `target`. Server writes the inverse
    on the target side automatically (see spec §6A/§6B).

    Validates: link type is canonical; source/target kinds match the type's
    domain; target entity exists; depends_on writes don't create cycles.
    Idempotent — re-running with the same args is a no-op.
    """
    from taskmaster.taskmaster_v3 import (
        LINK_TYPES, is_valid_link, entity_kind_of,
        read_entity_anywhere, write_entity_anywhere, add_link, entity_links,
        sync_inverse, would_create_cycle,
    )

    backlog_path = _backlog_path()

    if type not in LINK_TYPES:
        return f"Error: invalid link type {type!r} (valid: {sorted(LINK_TYPES)})"

    src_kind = entity_kind_of(source)
    dst_kind = entity_kind_of(target)
    if src_kind is None:
        return f"Error: invalid source ID {source!r}"
    if dst_kind is None:
        return f"Error: invalid target ID {target!r}"
    if not is_valid_link(type, src_kind, dst_kind):
        return (f"Error: invalid link — type {type!r} cannot go from "
                f"{src_kind} ({source}) to {dst_kind} ({target})")

    src_entity = read_entity_anywhere(backlog_path, source)
    if src_entity is None:
        return f"Error: source {source!r} not found"
    dst_entity = read_entity_anywhere(backlog_path, target)
    if dst_entity is None:
        return f"Error: target {target!r} not found"

    # Cycle check on depends_on / blocks (model both as forward edges in a
    # single task→task graph; `blocks` is reversed onto `depends_on`).
    if type in ("depends_on", "blocks"):
        graph: dict[str, list[str]] = {}
        data = _load()
        for epic in data.get("epics", []):
            for task in epic.get("tasks", []):
                tid = task.get("id")
                if not tid:
                    continue
                graph.setdefault(tid, [])
                for link in task.get("links", []) or []:
                    if link.get("type") == "depends_on":
                        graph[tid].append(link["target"])
                    elif link.get("type") == "blocks":
                        # B blocks A == A depends_on B
                        graph.setdefault(link["target"], []).append(tid)
        # Normalize the new edge to a depends_on direction for the check.
        new_src, new_dst = (source, target) if type == "depends_on" else (target, source)
        if would_create_cycle(graph, new_src, new_dst):
            return (f"Error: would create cycle in depends_on chain "
                    f"({new_src} -> {new_dst})")

    added = add_link(src_entity, type, target)
    if added:
        write_entity_anywhere(backlog_path, src_entity)
    try:
        sync_inverse(backlog_path, source=source, target=target, type=type)
    except KeyError as e:
        return f"Error: {e}"

    suffix = "" if added else " (no-op, link already present)"
    note_part = f" -- {note}" if note else ""
    return f"ok: linked {source} -[{type}]-> {target}{suffix}{note_part}"


@_transactional("backlog_link_remove")
def backlog_link_remove(source: str, target: str, type: str = "") -> str:
    """Remove a link (and its inverse) between `source` and `target`.

    If `type` is omitted, removes all link types between the pair.
    """
    from taskmaster.taskmaster_v3 import (
        LINK_TYPES, entity_kind_of, read_entity_anywhere, write_entity_anywhere,
        remove_link, entity_links, sync_inverse,
    )

    backlog_path = _backlog_path()

    if entity_kind_of(source) is None:
        return f"Error: invalid source ID {source!r}"
    if entity_kind_of(target) is None:
        return f"Error: invalid target ID {target!r}"

    src_entity = read_entity_anywhere(backlog_path, source)
    if src_entity is None:
        return f"Error: source {source!r} not found"

    types_to_remove: list[str]
    if type:
        if type not in LINK_TYPES:
            return f"Error: invalid link type {type!r}"
        types_to_remove = [type]
    else:
        types_to_remove = sorted({link["type"] for link in entity_links(src_entity)
                                  if link["target"] == target})

    if not types_to_remove:
        return f"ok: no-op (no links from {source} to {target})"

    removed_any = False
    for t in types_to_remove:
        if remove_link(src_entity, t, target):
            removed_any = True
        try:
            sync_inverse(backlog_path, source=source, target=target, type=t, remove=True)
        except KeyError:
            pass
    if removed_any:
        write_entity_anywhere(backlog_path, src_entity)
        return f"ok: removed {len(types_to_remove)} link(s) between {source} and {target}"
    return f"ok: no-op (links not present between {source} and {target})"


def backlog_link_query(source: str = "", target: str = "", type: str = "",
                       depth: int = 1) -> str:
    """Return links matching the source/target/type filter.

    With depth>1, traverses transitively along the same `type`. Returns a JSON
    array of {source, target, type} entries.
    """
    import json as _json
    from taskmaster.taskmaster_v3 import (
        entity_kind_of, read_entity_anywhere, entity_links,
    )

    backlog_path = _backlog_path()

    def edges_from(entity_id: str) -> list[dict]:
        entity = read_entity_anywhere(backlog_path, entity_id)
        if entity is None:
            return []
        return [{"source": entity_id, "target": link["target"], "type": link["type"]}
                for link in entity_links(entity)]

    def all_edges() -> list[dict]:
        out: list[dict] = []
        data = _load()
        for epic in data.get("epics", []):
            for task in epic.get("tasks", []):
                tid = task.get("id")
                if not tid:
                    continue
                for link in task.get("links", []) or []:
                    out.append({"source": tid, "target": link["target"], "type": link["type"]})
        for sub, prefix in (("handovers", "HND"), ("issues", "ISS"),
                            ("ideas", "IDEA")):
            sub_dir = backlog_path.parent / sub
            if not sub_dir.exists():
                continue
            for fp in sub_dir.glob(f"{prefix}-*.md"):
                eid = fp.stem
                entity = read_entity_anywhere(backlog_path, eid)
                if entity is None:
                    continue
                for link in entity_links(entity):
                    out.append({"source": eid, "target": link["target"], "type": link["type"]})
        return out

    if source and entity_kind_of(source) is None:
        return f"Error: invalid source ID {source!r}"
    if target and entity_kind_of(target) is None:
        return f"Error: invalid target ID {target!r}"

    if source and read_entity_anywhere(backlog_path, source) is None:
        return f"Error: source {source!r} not found"

    if source:
        results = list(edges_from(source))
        if depth > 1 and type:
            seen = {(e["source"], e["target"]) for e in results}
            frontier = [e["target"] for e in results if e["type"] == type]
            for _ in range(depth - 1):
                next_frontier: list[str] = []
                for node in frontier:
                    for edge in edges_from(node):
                        if edge["type"] != type:
                            continue
                        key = (edge["source"], edge["target"])
                        if key in seen:
                            continue
                        seen.add(key)
                        results.append(edge)
                        next_frontier.append(edge["target"])
                frontier = next_frontier
    else:
        results = all_edges()

    if target:
        results = [e for e in results if e["target"] == target]
    if type:
        results = [e for e in results if e["type"] == type]
    return _json.dumps(results)


def backlog_link_validate() -> str:
    """Report link drift: orphan links, asymmetric pairs, depends_on cycles.

    Returns a JSON object {orphans, asymmetric, cycles, archived_targets}.
    Links to archived entities (status: archived) are flagged in
    `archived_targets` but NOT auto-removed.
    """
    import json as _json
    from taskmaster.taskmaster_v3 import (
        REVERSE_TYPE, read_entity_anywhere, entity_links, find_cycle,
    )

    backlog_path = _backlog_path()

    def iter_all_entities():
        data = _load()
        for epic in data.get("epics", []):
            for task in epic.get("tasks", []):
                if task.get("id"):
                    yield task["id"], task
        for sub, prefix in (("handovers", "HND"), ("issues", "ISS"),
                            ("ideas", "IDEA")):
            sub_dir = backlog_path.parent / sub
            if not sub_dir.exists():
                continue
            for fp in sub_dir.glob(f"{prefix}-*.md"):
                eid = fp.stem
                entity = read_entity_anywhere(backlog_path, eid)
                if entity is not None:
                    yield eid, entity

    orphans: list[dict] = []
    asymmetric: list[dict] = []
    archived_targets: list[dict] = []
    depends_graph: dict[str, list[str]] = {}

    entities_by_id: dict[str, dict] = {}
    for eid, ent in iter_all_entities():
        entities_by_id[eid] = ent

    for eid, ent in entities_by_id.items():
        for link in entity_links(ent):
            tgt = link["target"]
            ltype = link["type"]
            if tgt not in entities_by_id:
                orphans.append({"source": eid, "target": tgt, "type": ltype})
                continue
            target_entity = entities_by_id[tgt]
            # Flag links to archived entities as a warning (not auto-removed).
            if target_entity.get("status") == "archived":
                archived_targets.append({"source": eid, "target": tgt, "type": ltype})
            inverse = REVERSE_TYPE.get(ltype)
            if inverse is None:
                continue
            peer_links = entity_links(target_entity)
            if {"type": inverse, "target": eid} not in peer_links:
                asymmetric.append({"source": eid, "target": tgt, "type": ltype,
                                   "missing_inverse": inverse})
            if ltype == "depends_on":
                depends_graph.setdefault(eid, []).append(tgt)
                depends_graph.setdefault(tgt, depends_graph.get(tgt, []))

    cycles: list[list[str]] = []
    # Find up to 5 cycles by iteratively excising one edge of each cycle found.
    graph_copy = {k: list(v) for k, v in depends_graph.items()}
    for _ in range(5):
        cyc = find_cycle(graph_copy)
        if cyc is None:
            break
        cycles.append(cyc)
        # Excise the first edge of the cycle to find further independent ones.
        if len(cyc) >= 2:
            a, b = cyc[0], cyc[1]
            if b in graph_copy.get(a, []):
                graph_copy[a].remove(b)

    return _json.dumps({"orphans": orphans, "asymmetric": asymmetric,
                        "cycles": cycles, "archived_targets": archived_targets})


@_transactional("backlog_link_reconcile")
def backlog_link_reconcile() -> str:
    """Add missing inverse links on peers. Reports unfixable drift.

    Returns JSON {fixed: N, unfixable: [...], cycles: [...]}.
    """
    import json as _json
    from taskmaster.taskmaster_v3 import sync_inverse

    validation = _json.loads(backlog_link_validate())
    fixed = 0
    unfixable: list[dict] = list(validation.get("orphans", []))
    backlog_path = _backlog_path()

    for entry in validation.get("asymmetric", []):
        try:
            sync_inverse(backlog_path,
                         source=entry["source"],
                         target=entry["target"],
                         type=entry["type"])
            fixed += 1
        except (KeyError, ValueError) as e:
            unfixable.append({**entry, "reason": str(e)})

    return _json.dumps({"fixed": fixed, "unfixable": unfixable,
                        "cycles": validation.get("cycles", [])})


@mcp.tool()
@_transactional("backlog_handover_supersede")
def backlog_handover_supersede(old_id: str, new_id: str) -> str:
    """Mark an existing handover as superseded by another.

    Edits the old handover in place: prepends a SUPERSEDED callout, sets
    `superseded_by: <new_id>` in its frontmatter. Use this to repair a
    supersession chain after the fact (e.g., a handover was written without
    `supersedes=`, but should chain off a prior one).

    Both ids must exist on disk. Idempotent on the same `old_id` — calling it
    again with a newer `new_id` updates the pointer instead of stacking
    callouts.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    _ensure_handover_status_backfilled()
    tx = _store_tx()
    if not tx.id_taken("handover", new_id):
        return f"Error: handover not found: {new_id}."
    try:
        old_doc, old_body = _tx_doc("handover", old_id)
    except KeyError:
        return f"Error: handover not found: {old_id}."
    document, new_body = _supersede_handover_doc(old_doc, old_body, new_id=new_id)
    tx.put("handover", old_id, document, body=new_body)
    data = _load()
    _sync_handover_index_tx(data)
    _mutate_and_save(data)
    return f"Superseded {old_id} \u2192 {new_id} ({old_id}.md updated)."


@mcp.tool()
@_transactional("backlog_handover_update_status")
def backlog_handover_update_status(
    handover_id: str,
    status: str,
    reason: str = "",
) -> str:
    """Manually set a handover's status (open / closed / superseded).

    Marks status_user_set: true — subsequent auto-transitions (supersession,
    task-complete, resume) will skip this handover.

    Args:
        handover_id: The handover id (e.g. "2026-05-09-shipped-x").
        status: One of open, closed, superseded.
        reason: Optional free-text rationale stored as `status_reason`.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    _ensure_handover_status_backfilled()
    fm = _handover_set_status(handover_id, status=status, reason=reason)
    if isinstance(fm, str):
        return fm
    data = _load()
    _sync_handover_index_tx(data)
    _mutate_and_save(data)
    return f"Handover {handover_id} \u2192 status={fm['status']} (user-set)."


def _handover_set_status(handover_id: str, *, status: str, reason: str = ""):
    """Apply a user-set status to one handover inside the open transaction.

    Returns the new document, or an error string. Shared by the MCP tool and
    the viewer's POST handler so both run exactly one code path.
    """
    try:
        document, body = _tx_doc("handover", handover_id)
    except KeyError:
        return f"Handover not found: {handover_id}"
    try:
        updated = _set_handover_status_doc(document, status=status, reason=reason)
    except ValueError as exc:
        return f"Error: {exc}"
    _store_tx().put("handover", handover_id, updated, body=body)
    return updated


@mcp.tool()
@_transactional("backlog_issue_create")
def backlog_issue_create(
    title: str,
    severity: str,
    evidence: str = "",
    impact: str = "",
    components: list[str] | None = None,
    location: list[str] | None = None,
    related_tasks: list[str] | None = None,
    discovered_by: str = "",
    body: str = "",
    tldr: str = "",
) -> str:
    """Log a systemic or recurring defect as a first-class Issue.

    Issues require evidence of recurrence, systemic scope, or outstanding
    customer impact — one-off defects should go to `backlog_bug_create` instead.
    A *task* is the unit of work; an *issue* is the unit of broken-ness.
    One issue can spawn multiple fix attempts; one task can close many issues.

    Args:
        title: Required. Short summary.
        severity: One of P0 (data loss/security), P1, P2, P3 (cosmetic).
        evidence: Required. Cite recurrence/systemic/outstanding criterion.
        impact: Why this matters (user-visible consequences).
        components: Tags for which parts of the system are affected.
        location: file:line refs to relevant code.
        related_tasks: Task ids attempting or related to this issue.
        discovered_by: Who/what found it (manual QA, alert, customer report).
        body: Markdown body for repro steps + investigation notes.
        tldr: One-line essence of the issue. Auto-generated from impact or
            title if omitted.
    """
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}. Run `backlog_init` first."
    if severity not in ISSUE_SEVERITIES:
        return f"Error: severity must be one of {ISSUE_SEVERITIES}"
    # tldr: use supplied value, or auto-generate from impact/title
    tldr_autogen = False
    if not tldr:
        tldr = extract_tldr(impact) or title[:TLDR_MAX_CHARS]
        tldr_autogen = True
    try:
        document = _build_issue_doc(
            title=title,
            severity=severity,
            evidence=evidence,
            impact=impact,
            components=components or [],
            location=location or [],
            related_tasks=related_tasks or [],
            discovered_by=discovered_by,
            tldr=tldr,
            tldr_autogen=tldr_autogen,
        )
        iid = _store_tx().create("issue", document, body=body)
    except ValueError as exc:
        return f"Error: {exc}"
    target = _issue_path(bp, iid)

    data = _load()
    _sync_issue_index_tx(data)
    _mutate_and_save(data)

    # Plan C: auto-detect inline ID mentions, materialize as `references` links.
    try:
        _auto_link_entity(bp, iid)
    except Exception:
        pass

    return f"Issue created: {iid} ({severity}) — {title}\nFile: {target.relative_to(ROOT)}"


@mcp.tool()
def backlog_issue_list(
    severity: str = "",
    status: str = "",
    limit: int = DEFAULT_LIST_LIMIT,
    verbose: bool = False,
) -> str:
    """List issues, optionally filtered by severity and/or status.

    Reads from the backlog.yaml index (sorted P0 → P3). Default lists active
    issues regardless of status — pass `status=open` to focus on what still
    needs work.

    Args:
        severity: Filter by severity: P0, P1, P2, P3.
        status: Filter by status: open, investigating, fixed, wontfix, duplicate.
        limit: Max entries returned (default 50). 0 = no cap. An overflow footer
            reports how many were hidden.
        verbose: If True, include body content (repro steps) per entry. Slim
            (default) shows id, severity, status, title, and tldr.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    data = _load()
    rows = _dict_rows(data, "issue")
    docs = {ident: doc for ident, doc, _body in rows}
    bodies = {ident: (body or "") for ident, _doc, body in rows}
    entries = _derived_index("issue", rows)
    if severity:
        entries = [e for e in entries if e.get("severity") == severity]
    if status:
        entries = [e for e in entries if e.get("status") == status]
    entries, overflow = _cap_list(list(entries), limit)
    if not entries:
        return "No issues match."
    lines = []
    for e in entries:
        comps = ", ".join(e.get("components") or [])
        comps_tag = f" [{comps}]" if comps else ""
        line = (
            f"- {e['id']} {e.get('severity', '?')} {e.get('status', '?'):14} "
            f"— {e.get('title', '')}{comps_tag}"
        )
        # In slim mode, enrich with tldr from the row (the index omits tldr).
        fm = docs.get(e["id"]) or {}
        if not verbose:
            tldr = fm.get("tldr", "")
            if tldr:
                line += f" \u2014 {tldr}"
        lines.append(line)
        if verbose:
            if fm.get("tldr"):
                lines.append(f"  tldr: {fm['tldr']}")
            body = bodies.get(e["id"]) or ""
            if body.strip():
                lines.append(f"  body: {body.strip()[:200]}")
    footer = _overflow_footer(overflow, "issues")
    if footer:
        lines.append(footer)
    return "\n".join(lines)


@mcp.tool()
def backlog_issue_get(
    issue_id: str,
    verbose: bool = False,
    sections: list[str] | None = None,
    expand_links: bool = False,
) -> str:
    """Read an issue's content.

    By default returns a slim view (frontmatter fields only, no body) to
    minimise token cost. Use verbose=True for the full body (repro steps,
    investigation notes). Use sections to pull specific named body sections
    (repro, investigation, notes). Use expand_links=True to expand
    related_tasks/fixed_in_task to `id (tldr)` — honored in both slim and
    verbose modes.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    row = _dict_row(_load(), "issue", issue_id)
    if row is None:
        return f"Issue not found: {issue_id}"
    fm, body = row[0], row[1] or ""

    # ── sections-only mode ───────────────────────────────────────────────────
    if sections is not None and not sections:
        return "Error: sections=[] requested no sections; pass sections=None for the slim view or name at least one section"
    if sections:
        try:
            sec_data = _resolve_sections(fm, kind="issue", sections=sections, body=body)
        except ValueError as exc:
            return f"Error: {exc}"
        lines = [f"## Issue: {issue_id}\n"]
        for sec, content in sec_data.items():
            lines.append(f"### {sec}\n{content}")
        return "\n".join(lines)

    # ── verbose mode ─────────────────────────────────────────────────────────
    if verbose:
        vfm = _expand_fm_links(fm, "issue", bp) if expand_links else fm
        fm_lines = [f"  {k}: {v}" for k, v in vfm.items()]
        return "---\n" + "\n".join(fm_lines) + "\n---\n" + body

    # ── slim mode (default) ──────────────────────────────────────────────────
    slim = _slim_entity(fm, kind="issue")

    if expand_links:
        data = _load()
        tldr_index = _build_tldr_index(data, project_root=bp.parent.parent if bp.exists() else None)
        related_tasks = slim.get("related_tasks") or []
        if related_tasks:
            slim["related_tasks"] = _expand_link_ids(related_tasks, tldr_index)
        fixed_in = slim.get("fixed_in_task")
        if fixed_in:
            pills = _expand_link_ids([fixed_in], tldr_index)
            slim["fixed_in_task"] = pills[0] if pills else fixed_in

    lines = [f"## Issue: {slim.pop('id', issue_id)}\n"]
    for k, v in slim.items():
        lines.append(f"**{k}:** {v}")
    # Plan C: emit grouped typed-links block.
    _append_grouped_links_block(lines, fm, bp, expand_links=expand_links)
    return "\n".join(lines)


ISSUE_UPDATE_LIST_FIELDS = {"components", "location", "related_tasks"}
ISSUE_UPDATE_SCALAR_FIELDS = {
    "status", "title", "severity", "impact", "fixed_in_task", "duplicate_of", "body",
}
ISSUE_UPDATE_FIELDS = ISSUE_UPDATE_LIST_FIELDS | ISSUE_UPDATE_SCALAR_FIELDS


@mcp.tool()
@_transactional("backlog_issue_update")
def backlog_issue_update(issue_id: str, field: str, value: str = "") -> str:
    """Set one field on an issue. List fields take a comma-separated value; an
    empty value clears the field.

    field ∈ {status, title, severity, impact, components, location,
    related_tasks, fixed_in_task, duplicate_of, body}. Lifecycle: status=fixed
    needs fixed_in_task (set it in a prior call), status=duplicate needs
    duplicate_of; `resolved` auto-fills when status moves to fixed.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    if field not in ISSUE_UPDATE_FIELDS:
        return f"Error: field {field!r} not allowed. Allowed: {', '.join(sorted(ISSUE_UPDATE_FIELDS))}"
    if field == "status" and value not in ISSUE_STATUSES:
        return f"Error: status must be one of {ISSUE_STATUSES}"
    if field == "severity" and value not in ISSUE_SEVERITIES:
        return f"Error: severity must be one of {ISSUE_SEVERITIES}"
    if field in ISSUE_UPDATE_LIST_FIELDS:
        updates: dict[str, Any] = {field: [x.strip() for x in value.split(",") if x.strip()]}
    else:
        updates = {field: value}
    body = value if field == "body" else ""

    try:
        document, stored_body = _tx_doc("issue", issue_id)
    except KeyError:
        return f"Issue not found: {issue_id}"
    new_body = updates.pop("body", stored_body)
    try:
        fm = _apply_issue_updates(document, **updates)
    except ValueError as exc:
        return f"Error: {exc}"
    _store_tx().put("issue", issue_id, fm, body=new_body)

    data = _load()
    _sync_issue_index_tx(data)
    _mutate_and_save(data)

    # Plan C: auto-detect inline ID mentions on body updates.
    if body:
        try:
            _auto_link_entity(bp, issue_id)
        except Exception:
            pass

    return f"Issue updated: {issue_id} → status={fm['status']}, severity={fm['severity']}"


@mcp.tool()
@_transactional("backlog_issue_resync")
def backlog_issue_resync() -> str:
    """Rebuild the issue index in backlog.yaml from disk."""
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    data = _load()
    _sync_issue_index_tx(data)
    _mutate_and_save(data)
    n = len(data.get("issues") or [])
    return f"Issue index resynced \u2014 {n} entries."


# ── Bug MCP tools ─────────────────────────────────────────────────────────────


@mcp.tool()
@_transactional("backlog_bug_create")
def backlog_bug_create(
    title: str,
    found_in: str = "",
    discovered_by: str = "user",
    severity: str = "",
    components: list[str] | None = None,
    location: list[str] | None = None,
    body: str = "",
) -> str:
    """Log a Bug — the user-flagged sink for defects that don't clear the Issue bar.

    Bugs are project-wide lightweight artifacts (no aging window, no fix-by).
    Use this for one-off defects, cosmetic issues, or anything the user wants
    tracked but that doesn't yet meet the recurring/systemic/outstanding bar.

    Args:
        title: Required. Short summary.
        found_in: Optional task ID where the bug was flagged.
        discovered_by: 'user' (default) or 'claude'. claude is only valid for
            the offer-on-explicit-finding entry point, never for proactive
            AI sightings.
        severity: Optional. P0|P1|P2|P3 if you want a sort hint.
        components: Affected component tags.
        location: file:line refs.
        body: Markdown body for repro/notes.
    """
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}. Run `backlog_init` first."
    result = _bug_create_in_tx(
        title=title,
        found_in=found_in or None,
        discovered_by=discovered_by,
        severity=severity or None,
        components=components or [],
        location=location or [],
        body=body,
    )
    if isinstance(result, str):
        return f"Error: {result}"
    bid, target = result
    return f"Bug created: {bid} \u2014 {title}\nFile: {target.relative_to(ROOT)}"


def _bug_create_in_tx(
    *,
    title: str,
    found_in: str | None,
    discovered_by: str,
    severity: str | None,
    components: list,
    location: list,
    body: str,
):
    """Create one Bug row and refresh the index inside the open transaction.

    Returns `(id, path)` or an error string. Shared by the MCP tool and the
    viewer's POST handler so both run exactly one code path.
    """
    from taskmaster.taskmaster_v3 import build_bug_doc as _build_bug_doc, bug_path as _bug_path
    try:
        document = _build_bug_doc(
            title=title,
            found_in=found_in,
            discovered_by=discovered_by,
            severity=severity,
            components=components,
            location=location,
        )
        bid = _store_tx().create("bug", document, body=body)
    except ValueError as exc:
        return str(exc)
    data = _load()
    _sync_bug_index_tx(data)
    _mutate_and_save(data)
    return bid, _bug_path(_backlog_path(), bid)


@mcp.tool()
def backlog_bug_list(
    status: str = "",
    found_in: str = "",
    limit: int = DEFAULT_LIST_LIMIT,
    include_archive: bool = False,
) -> str:
    """List Bugs from the active set (and optionally archive).

    Defaults to the active set sorted by (status weight asc, discovered desc).

    Args:
        status: Filter by status: open, fixed, shelved, adopted, promoted.
        found_in: Filter by task ID where bug was discovered.
        limit: Max entries returned (default 50). 0 = no cap. An overflow footer
            reports how many were hidden.
        include_archive: If True, also include archived bugs.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    data = _load()
    # A list is a read: derive the index from the rows rather than re-syncing
    # (and thereby mutating) the caller's dict.
    entries = list(_derived_index("bug", _dict_rows(data, "bug")))
    if include_archive:
        active_ids = {e["id"] for e in entries}
        for bid, fm, _body in _dict_rows(data, "bug", include_archived=True):
            if bid in active_ids:
                continue
            entries.append({
                "id": fm.get("id", bid),
                "title": fm.get("title"),
                "status": fm.get("status"),
                "components": fm.get("components"),
                "found_in": fm.get("found_in"),
                "discovered": fm.get("discovered"),
            })
    if status:
        entries = [e for e in entries if e.get("status") == status]
    if found_in:
        entries = [e for e in entries if e.get("found_in") == found_in]
    entries, overflow = _cap_list(entries, limit)
    if not entries:
        return "No bugs match."
    lines = []
    for e in entries:
        comps = ", ".join(e.get("components") or [])
        comps_tag = f" [{comps}]" if comps else ""
        line = f"- {e['id']} {e.get('status', '?'):10} — {e.get('title', '')}{comps_tag}"
        if e.get("found_in"):
            line += f"  (found_in: {e['found_in']})"
        lines.append(line)
    footer = _overflow_footer(overflow, "bugs")
    if footer:
        lines.append(footer)
    return "\n".join(lines)


@mcp.tool()
def backlog_bug_get(bug_id: str, verbose: bool = False) -> str:
    """Read a Bug. Falls through to archive if not in active set.

    Args:
        bug_id: Bug ID (e.g. B-001).
        verbose: If True, return full frontmatter + body. Default is slim view.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    row = _dict_row(_load(), "bug", bug_id)
    if row is None:
        return f"Bug not found: {bug_id}"
    fm, body = row[0], row[1] or ""
    if verbose:
        fm_lines = [f"  {k}: {v}" for k, v in fm.items()]
        return "---\n" + "\n".join(fm_lines) + "\n---\n" + body
    lines = [f"## Bug: {fm['id']}\n"]
    for k in ("title", "status", "severity", "components", "found_in", "discovered", "discovered_by"):
        if fm.get(k) is not None:
            lines.append(f"**{k}:** {fm[k]}")
    return "\n".join(lines)


BUG_UPDATE_LIST_FIELDS = {"components", "location"}
BUG_UPDATE_SCALAR_FIELDS = {
    "status", "title", "severity", "fix_commit", "adopted_into", "promoted_to", "body",
}
BUG_UPDATE_FIELDS = BUG_UPDATE_LIST_FIELDS | BUG_UPDATE_SCALAR_FIELDS


@mcp.tool()
@_transactional("backlog_bug_update")
def backlog_bug_update(bug_id: str, field: str, value: str = "") -> str:
    """Set one field on a Bug. List fields take a comma-separated value; an
    empty value clears the field.

    field ∈ {status, title, severity, components, location, fix_commit,
    adopted_into, promoted_to, body}. Lifecycle (set the companion field first,
    in a prior call): status=fixed needs fix_commit, status=adopted needs
    adopted_into, status=promoted needs promoted_to.
    """
    from taskmaster.taskmaster_v3 import BUG_STATUSES
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    if field not in BUG_UPDATE_FIELDS:
        return f"Error: field {field!r} not allowed. Allowed: {', '.join(sorted(BUG_UPDATE_FIELDS))}"
    if field == "status" and value not in BUG_STATUSES:
        return f"Error: status must be one of {BUG_STATUSES}"
    if field in BUG_UPDATE_LIST_FIELDS:
        updates: dict[str, Any] = {field: [x.strip() for x in value.split(",") if x.strip()]}
    else:
        updates = {field: value}
    result = _bug_update_in_tx(bug_id, updates)
    if isinstance(result, str):
        return result
    return f"Bug updated: {bug_id} \u2014 status={result['status']}"


def _bug_update_in_tx(bug_id: str, updates: dict):
    """Apply field updates to one Bug row inside the open transaction.

    Returns the new document, or an error string. Shared by the MCP tool and
    the viewer's POST handler.
    """
    from taskmaster.taskmaster_v3 import apply_bug_updates as _apply_bug_updates
    try:
        document, stored_body = _tx_doc("bug", bug_id)
    except KeyError:
        return f"Bug not found: {bug_id}"
    updates = dict(updates)
    new_body = updates.pop("body", stored_body)
    try:
        fm = _apply_bug_updates(document, **updates)
    except ValueError as exc:
        return f"Error: {exc}"
    _store_tx().put("bug", bug_id, fm, body=new_body)
    data = _load()
    _sync_bug_index_tx(data)
    _mutate_and_save(data)
    return fm


@mcp.tool()
@_transactional("backlog_bug_archive")
def backlog_bug_archive(bug_id: str) -> str:
    """Move bugs/B-NNN.md to bugs/archive/B-NNN.md.

    Refuses if status is open or shelved. Called automatically by task-close
    for fixed bugs; rarely called directly.

    Args:
        bug_id: Bug ID (e.g. B-001).
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    error = _bug_archive_in_tx(bug_id)
    if error is not None:
        return error
    return f"Bug archived: {bug_id}"


def _bug_archive_in_tx(bug_id: str) -> str | None:
    """Archive one Bug row inside the open transaction; None on success.

    The file move to `bugs/archive/` is the exporter's job — the archive flag
    on the row is what decides where the projection lives.
    """
    from taskmaster.taskmaster_v3 import assert_bug_archivable as _assert_bug_archivable
    tx = _store_tx()
    try:
        document, _body = _tx_doc("bug", bug_id)
    except KeyError:
        return f"Bug not found: {bug_id}"
    try:
        _assert_bug_archivable(document)
    except ValueError as exc:
        return f"Error: {exc}"
    tx.archive("bug", bug_id)
    data = _load()
    _sync_bug_index_tx(data)
    _mutate_and_save(data)
    return None


@mcp.tool()
def backlog_bug_pattern_scan(mode: str = "all") -> str:
    """Run the cross-bug signature scanner and return groups.

    mode: "all" (default), "open_only", "end_of_task" (excludes archive).
    Returns a human-readable digest of candidate groups; groups have >=2 bugs.

    Args:
        mode: Scan scope. "all" includes archive, "end_of_task" excludes it.
    """
    from taskmaster.taskmaster_v3 import scan_bug_patterns as _scan_bug_patterns
    if mode not in {"all", "open_only", "end_of_task"}:
        return f"Error: invalid mode {mode!r} (expected all|open_only|end_of_task)"
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    include_archive = (mode == "all")
    open_only = (mode == "open_only")
    rows = _dict_rows(_load(), "bug", include_archived=include_archive)
    groups = _scan_bug_patterns(rows, open_only=open_only)
    if not groups:
        return "No bug patterns found (need >=2 matching signatures)."
    lines = [f"Found {len(groups)} pattern group(s):"]
    for i, g in enumerate(groups, 1):
        comps = ", ".join(g["signature"]["components"]) or "(no components)"
        toks = ", ".join(g["signature"]["tokens"])
        ids = ", ".join(g["bug_ids"])
        lines.append(f"  {i}. [{comps}] tokens: {toks}")
        lines.append(f"     bugs: {ids}")
    return "\n".join(lines)


@mcp.tool()
@_transactional("backlog_bug_promote")
def backlog_bug_promote(
    bug_ids: list[str],
    title: str,
    severity: str,
    evidence_text: str,
    components: list[str] | None = None,
    body: str = "",
) -> str:
    """Atomic: create an Issue from N Bugs; mark each Bug status=promoted.

    The user must provide evidence_text — this is the systemic/recurring/
    outstanding rationale that the new bar requires. Bugs are marked
    promoted_to=<new ISS-NNN>.

    Args:
        bug_ids: List of Bug IDs to promote (e.g. ["B-001", "B-002"]).
        title: Title for the new Issue.
        severity: Severity for the new Issue (P0-P3).
        evidence_text: Required rationale: cite recurrence/systemic/outstanding.
        components: Component tags for the Issue. Inferred from bugs if omitted.
        body: Markdown body for the new Issue.
    """
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}. Run `backlog_init` first."
    result = _promote_bugs_in_tx(
        bug_ids=list(bug_ids or []),
        title=title,
        severity=severity,
        evidence_text=evidence_text,
        components=components or None,
        body=body,
    )
    if not isinstance(result, str):
        return f"Promoted {len(bug_ids)} bug(s) to {result[0]}."
    return f"Error: {result}"


def _promote_bugs_in_tx(
    *,
    bug_ids: list,
    title: str,
    severity: str,
    evidence_text: str,
    components: list | None,
    body: str,
):
    """Create an Issue from N Bugs and mark each Bug promoted. One transaction.

    Returns `(issue_id,)` on success or an error string. The issue create and
    every bug flip commit together: a half-applied promotion would leave bugs
    pointing at an issue that does not exist.
    """
    from taskmaster.taskmaster_v3 import (
        apply_bug_updates as _apply_bug_updates,
        build_issue_doc as _build_issue_doc_local,
    )
    if not bug_ids:
        return "bug_ids must be non-empty"
    if not evidence_text or not evidence_text.strip():
        return "evidence_text is required (cite recurrence/systemic/outstanding)"

    tx = _store_tx()
    sources: dict = {}
    for bid in bug_ids:
        try:
            sources[bid] = _tx_doc("bug", bid)
        except KeyError:
            return f"bug {bid} not found"

    if components is None:
        comps: set = set()
        for bid in bug_ids:
            for component in sources[bid][0].get("components") or []:
                comps.add(component)
        components = sorted(comps)

    try:
        issue_doc = _build_issue_doc_local(
            title=title,
            severity=severity,
            impact=evidence_text,  # repurpose impact as the evidence narrative
            evidence=evidence_text,
            components=components,
            promoted_from=list(bug_ids),
        )
        iid = tx.create("issue", issue_doc, body=body)
        for bid in bug_ids:
            document, stored_body = sources[bid]
            tx.put(
                "bug",
                bid,
                _apply_bug_updates(document, status="promoted", promoted_to=iid),
                body=stored_body,
            )
    except ValueError as exc:
        return str(exc)

    data = _load()
    _sync_bug_index_tx(data)
    _sync_issue_index_tx(data)
    _mutate_and_save(data)
    return (iid,)


@mcp.tool()
@_transactional("backlog_decision_create")
def backlog_decision_create(
    title: str,
    options: list[str],
    recommendation: int | None = None,
    task_id: str | None = None,
    related_issues: list[str] | None = None,
    branch: str | None = None,
    raised_in: str | None = None,
    body: str = "",
) -> str:
    """Write a decision menu as a first-class entity (`DEC-NNN`).

    Use when ≥2 mutually exclusive paths need user input. Replaces inline
    option lists in chat — the decision survives the session.

    Args:
        title: Short summary (≤80 chars).
        options: At least 2 mutually exclusive paths.
        recommendation: 1-indexed pick from `options`. None if no preference.
        task_id: Optional link to the task this decision blocks.
        related_issues: Optional ISS-NNN list.
        branch: Optional branch context.
        raised_in: Optional handover id that surfaced this decision.
        body: Free-form context (rationale, constraints, links).
    """
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}. Run `backlog_init` first."
    try:
        document = _build_decision_doc(
            title=title,
            options=options,
            recommendation=recommendation,
            task_id=task_id,
            related_issues=related_issues or [],
            branch=branch,
            raised_in=raised_in,
        )
        did = _store_tx().create("decision", document, body=body)
    except ValueError as exc:
        return f"Error: {exc}"
    _mutate_and_save(_load())
    target = _decision_path(bp, did)
    return f"Decision created: {did} \u2014 {title}\nFile: {target.relative_to(ROOT)}"


@mcp.tool()
def backlog_decision(
    action: Literal["list", "get", "resolve", "drop", "update"],
    decision_id: str = "",
    status: str = "open",
    task_id: str = "",
    limit: int = DEFAULT_LIST_LIMIT,
    resolved_with: int | None = None,
    rationale: str = "",
    resolved_in: str = "",
    reason: str = "",
    title: str = "",
    options: list[str] | None = None,
    recommendation: int | None = None,
    body: str = "",
) -> str:
    """Read and transition existing decisions (DEC-NNN). To CREATE a decision use
    backlog_decision_create. Route through the taskmaster:decision skill.

    Params by action: list(status, task_id, limit); get(decision_id);
    resolve(decision_id, resolved_with, rationale, resolved_in);
    drop(decision_id, reason); update(decision_id, title, options,
    recommendation, body).
    """
    if action == "list":
        return backlog_decision_list(status, task_id, limit)
    if action == "get":
        return backlog_decision_get(decision_id)
    if action == "resolve":
        if resolved_with is None:
            return "Error: resolve requires resolved_with (1-indexed option)"
        return backlog_decision_resolve(decision_id, resolved_with, rationale, resolved_in)
    if action == "drop":
        return backlog_decision_drop(decision_id, reason)
    if action == "update":
        return backlog_decision_update(decision_id, title, options, recommendation, body)
    return f"Error: unknown action {action!r}"


def backlog_decision_list(
    status: str = "open", task_id: str = "", limit: int = DEFAULT_LIST_LIMIT
) -> str:
    """List decisions filtered by status. `status='all'` returns every state.
    Caps at `limit` (default 50; 0 = no cap) with an overflow footer."""
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    rows: list[str] = []
    for did, fm, _body in _dict_rows(_load(), "decision"):
        if status != "all" and fm.get("status") != status:
            continue
        if task_id and fm.get("task_id") != task_id:
            continue
        rec = fm.get("recommendation")
        rec_str = f" [rec={rec}]" if rec else ""
        rows.append(f"{did} · {fm.get('status')} · {fm.get('title')}{rec_str}")
    if not rows:
        return f"No decisions matching status={status}."
    rows, overflow = _cap_list(rows, limit)
    footer = _overflow_footer(overflow, "decisions")
    if footer:
        rows.append(footer)
    return "\n".join(rows)


def backlog_decision_get(decision_id: str) -> str:
    """Return full decision frontmatter + body as readable text."""
    row = _dict_row(_load(), "decision", decision_id)
    if row is None:
        return f"Decision not found: {decision_id}"
    fm, body = row[0], row[1] or ""
    lines = [f"{k}: {v}" for k, v in fm.items()]
    return "\n".join(lines) + "\n\n---\n" + body


@_transactional("backlog_decision_resolve")
def backlog_decision_resolve(
    decision_id: str,
    resolved_with: int,
    rationale: str = "",
    resolved_in: str = "",
) -> str:
    """Resolve a decision with a chosen option (1-indexed)."""
    result = _decision_resolve_in_tx(
        decision_id,
        resolved_with=int(resolved_with),
        rationale=rationale,
        resolved_in=resolved_in or None,
    )
    if isinstance(result, str):
        return result
    return (
        f"Decision {decision_id} resolved with option {result['resolved_with']}: "
        f"\"{result['options'][result['resolved_with'] - 1]}\""
    )


def _decision_resolve_in_tx(
    decision_id: str, *, resolved_with: int, rationale: str = "", resolved_in: str | None = None
):
    """Resolve one decision row inside the open transaction; doc or error string."""
    try:
        document, body = _tx_doc("decision", decision_id)
    except KeyError:
        return f"Error: Decision not found: {decision_id}"
    try:
        fm = _resolve_decision_doc(
            document,
            resolved_with=resolved_with,
            rationale=rationale,
            resolved_in=resolved_in,
        )
    except ValueError as exc:
        return f"Error: {exc}"
    _store_tx().put("decision", decision_id, fm, body=body)
    _mutate_and_save(_load())
    return fm


@_transactional("backlog_decision_drop")
def backlog_decision_drop(decision_id: str, reason: str) -> str:
    """Drop a decision with a reason (no option picked)."""
    result = _decision_drop_in_tx(decision_id, reason=reason)
    if isinstance(result, str):
        return result
    return f"Decision {decision_id} dropped: {reason}"


def _decision_drop_in_tx(decision_id: str, *, reason: str):
    """Drop one decision row inside the open transaction; doc or error string."""
    try:
        document, body = _tx_doc("decision", decision_id)
    except KeyError:
        return f"Error: Decision not found: {decision_id}"
    try:
        fm = _drop_decision_doc(document, reason=reason)
    except ValueError as exc:
        return f"Error: {exc}"
    _store_tx().put("decision", decision_id, fm, body=body)
    _mutate_and_save(_load())
    return fm


@_transactional("backlog_decision_update")
def backlog_decision_update(
    decision_id: str,
    title: str = "",
    options: list[str] | None = None,
    recommendation: int | None = None,
    body: str = "",
) -> str:
    """Edit a decision in place (pre-resolution fields only)."""
    patch: dict = {}
    if title:
        patch["title"] = title
    if options:
        patch["options"] = options
    if recommendation is not None:
        patch["recommendation"] = recommendation
    try:
        document, stored_body = _tx_doc("decision", decision_id)
    except KeyError:
        return f"Error: Decision not found: {decision_id}"
    try:
        fm = _apply_decision_patch(document, patch)
    except ValueError as exc:
        return f"Error: {exc}"
    _store_tx().put("decision", decision_id, fm, body=body or stored_body)
    _mutate_and_save(_load())
    return f"Decision {decision_id} updated."


@mcp.tool()
def backlog_continuity_items(
    view: str = "action",
    include_auto_stage: bool = False,
) -> str:
    """Return all continuity items as JSON: {"items": [...], "view": "..."}.

    `view` is informational only — the server returns the full set; the client
    decides grouping (Action / Time / Entity).

    Args:
        view: "action" | "time" | "entity" (echoed in the response).
        include_auto_stage: When True, include auto-stage handovers (debug).
    """
    import json
    bp = _backlog_path()
    if not bp.exists():
        return json.dumps({"items": [], "view": view, "error": "no backlog"})
    # One loaded tree for every kind the rail projects: tasks come from its
    # epics (backlog.yaml has none on v4), and the rest from its `_rows`.
    tree = _load()
    items = _continuity_items(
        bp,
        include_auto_stage=include_auto_stage,
        handover_rows=_dict_rows(tree, "handover"),
        data=tree,
    )
    return json.dumps({"items": items, "view": view}, default=str)


@mcp.tool()
@_transactional("backlog_idea_create")
def backlog_idea_create(
    title: str,
    body: str = "",
    tags: list[str] | None = None,
    status: str = "",
    related_tasks: list[str] | None = None,
    related_issues: list[str] | None = None,
    created_by: str = "Claude",
    tldr: str = "",
) -> str:
    """Log an idea — a lightweight, unvalidated thought. Lighter than a task.

    An *idea* is a parking lot for things you might want to do, explore, or
    revisit. It can grow into a task / issue later via linkage.
    Title is required; everything else is optional. Status is freeform.

    Args:
        title: Required. Short summary.
        body: Markdown body — anything goes (sketches, code blocks, prose).
        tags: Freeform tag strings.
        status: Freeform status ("exploring", "parking-lot", "candidate", "").
        related_tasks: Task ids this idea relates to.
        related_issues: Issue ids this idea relates to.
        created_by: Who logged it ("Claude" by default; "user" when
            invoked via /add-idea).
        tldr: One-line essence of the idea. Auto-generated from body or
            title if omitted.
    """
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}. Run `backlog_init` first."
    # tldr: use supplied value, or auto-generate from body/title
    tldr_autogen = False
    if not tldr:
        tldr = extract_tldr(body) or title[:TLDR_MAX_CHARS]
        tldr_autogen = True
    result = _idea_create_in_tx(
        title=title,
        body=body,
        tags=tags or [],
        status=status,
        related_tasks=related_tasks or [],
        related_issues=related_issues or [],
        created_by=created_by,
        tldr=tldr,
        tldr_autogen=tldr_autogen,
    )
    if isinstance(result, str):
        return f"Error: {result}"
    iid, target = result

    # Plan C: auto-detect inline ID mentions, materialize as `references` links.
    try:
        _auto_link_entity(bp, iid)
    except Exception:
        pass

    try:
        rel = target.relative_to(ROOT)
    except ValueError:
        rel = target
    return f"Idea created: {iid} \u2014 {title}\nFile: {rel}"


def _idea_create_in_tx(
    *,
    title: str,
    body: str = "",
    tags: list | None = None,
    status: str = "",
    related_tasks: list | None = None,
    related_issues: list | None = None,
    created_by: str = "Claude",
    tldr: str = "",
    tldr_autogen: bool = False,
):
    """Create one idea row inside the open transaction; `(id, path)` or an error.

    `ideas/IDEAS.md` is regenerated by the exporter from the idea rows, so
    nothing here writes it. Shared by the MCP tool and the viewer's POST.
    """
    try:
        document = _build_idea_doc(
            title=title,
            tags=tags or [],
            status=status,
            related_tasks=related_tasks or [],
            related_issues=related_issues or [],
            created_by=created_by,
            tldr=tldr,
            tldr_autogen=tldr_autogen,
        )
        iid = _store_tx().create("idea", document, body=body)
    except ValueError as exc:
        return str(exc)
    _mutate_and_save(_load())
    return iid, _idea_path(_backlog_path(), iid)


@mcp.tool()
def backlog_idea_list(
    idea_id: str = "",
    status: str = "",
    tag: str = "",
    archived: bool = False,
    related_task: str = "",
    related_issue: str = "",
    limit: int = DEFAULT_LIST_LIMIT,
    verbose: bool = False,
) -> str:
    """List ideas, optionally filtered. With `idea_id`, returns one full record.

    Without `idea_id`, returns slim one-liners (no body) — newest first. Pass
    `verbose=True` to render the full record per entry (heavier payload). Output
    caps at `limit` with an overflow footer (limit=0 for all). Filters compose as
    AND. By default archived ideas are excluded.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    data = _load()
    if idea_id:
        out = _idea_records(data, idea_id=idea_id)
        if not out:
            return f"Idea not found: {idea_id}"
        rec = out[0]
        body = rec.pop("body", "")
        fm_lines = [f"  {k}: {v}" for k, v in rec.items()]
        return "---\n" + "\n".join(fm_lines) + "\n---\n" + body

    entries = _idea_records(
        data,
        status=status or None,
        tag=tag or None,
        archived=archived,
        related_task=related_task or None,
        related_issue=related_issue or None,
        summary=not verbose,
    )
    if not entries:
        return "No ideas match."
    entries, overflow = _cap_list(entries, limit)
    footer = _overflow_footer(overflow, "ideas")
    if verbose:
        # Full-record mode: render each as a frontmatter+body block.
        blocks = []
        for e in entries:
            body = e.pop("body", "")
            fm_lines = [f"  {k}: {v}" for k, v in e.items()]
            blocks.append("---\n" + "\n".join(fm_lines) + "\n---\n" + body)
        out = "\n\n".join(blocks)
        return f"{out}\n\n{footer}" if footer else out
    lines = []
    for e in entries:
        st = e.get("status") or ""
        st_tag = f" [{st}]" if st else ""
        lines.append(f"- {e['id']} — {e.get('title', '')}{st_tag}")
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def _idea_records(
    data: dict,
    *,
    idea_id: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    archived: bool = False,
    related_task: str | None = None,
    related_issue: str | None = None,
    summary: bool = True,
) -> list[dict]:
    """Idea documents from the store's rows, newest-first, filters ANDed.

    The row map replaces the old `ideas/IDEA-*.md` glob: a list and the write
    that follows it now read one snapshot. `summary=True` omits the body.
    """
    if idea_id:
        row = _dict_row(data, "idea", idea_id)
        if row is None:
            return []
        return [{**row[0], "body": (row[1] or "").rstrip("\n")}]

    out: list[dict] = []
    for _iid, fm, body in _dict_rows(data, "idea", include_archived=True):
        if not archived and fm.get("archived"):
            continue
        if status is not None and (fm.get("status") or "") != status:
            continue
        if tag is not None and tag not in (fm.get("tags") or []):
            continue
        if related_task is not None and related_task not in (fm.get("related_tasks") or []):
            continue
        if related_issue is not None and related_issue not in (fm.get("related_issues") or []):
            continue
        out.append(dict(fm) if summary else {**fm, "body": (body or "").rstrip("\n")})

    def _sort_key(entry: dict) -> tuple:
        match = re.search(r"(\d+)$", entry.get("id", ""))
        return (entry.get("created", ""), int(match.group(1)) if match else 0)

    out.sort(key=_sort_key, reverse=True)
    return out


@mcp.tool()
def backlog_idea_get(
    idea_id: str,
    verbose: bool = False,
    sections: list[str] | None = None,
    expand_links: bool = False,
) -> str:
    """Read an idea's content.

    By default returns a slim view (frontmatter fields only, no body) to
    minimise token cost. Use verbose=True for the full body. Ideas do not
    have canonical body sections, so sections= is not supported (returns
    an error if provided). Use expand_links=True to expand related_tasks
    and related_issues to `id (tldr)` — honored in both slim and verbose modes.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    if sections is not None and not sections:
        return "Error: sections=[] requested no sections; pass sections=None for the slim view or name at least one section"
    if sections:
        return "Error: ideas have no canonical body sections — use verbose=True to read the full body."
    row = _dict_row(_load(), "idea", idea_id)
    if row is None:
        return f"Idea not found: {idea_id}"
    fm, body = row[0], (row[1] or "").rstrip("\n")

    # ── verbose mode ─────────────────────────────────────────────────────────
    if verbose:
        vfm = _expand_fm_links(fm, "idea", bp) if expand_links else fm
        fm_lines = [f"  {k}: {v}" for k, v in vfm.items()]
        return "---\n" + "\n".join(fm_lines) + "\n---\n" + body

    # ── slim mode (default) ──────────────────────────────────────────────────
    slim = _slim_entity(fm, kind="idea")

    if expand_links:
        data = _load()
        tldr_index = _build_tldr_index(data, project_root=bp.parent.parent if bp.exists() else None)
        for link_field in ("related_tasks", "related_issues"):
            ids = slim.get(link_field) or []
            if ids:
                slim[link_field] = _expand_link_ids(ids, tldr_index)

    lines = [f"## Idea: {slim.pop('id', idea_id)} — {slim.pop('title', fm.get('title', ''))}\n"]
    for k, v in slim.items():
        lines.append(f"**{k}:** {v}")
    # Plan C: emit grouped typed-links block.
    _append_grouped_links_block(lines, fm, bp, expand_links=expand_links)
    return "\n".join(lines)


IDEA_UPDATE_LIST_FIELDS = {"tags", "related_tasks", "related_issues"}
IDEA_UPDATE_SCALAR_FIELDS = {"title", "body", "status", "promoted_to"}
IDEA_UPDATE_FIELDS = IDEA_UPDATE_LIST_FIELDS | IDEA_UPDATE_SCALAR_FIELDS | {"archived"}


@mcp.tool()
@_transactional("backlog_idea_update")
def backlog_idea_update(idea_id: str, field: str, value: str = "") -> str:
    """Set one field on an idea. List fields take a comma-separated value; an
    empty value clears the field.

    field ∈ {title, body, status, tags, related_tasks, related_issues,
    promoted_to, archived}. To promote an idea, set promoted_to then set
    archived=true; archived takes true/false.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    if field not in IDEA_UPDATE_FIELDS:
        return f"Error: field {field!r} not allowed. Allowed: {', '.join(sorted(IDEA_UPDATE_FIELDS))}"
    if field == "archived":
        if value.lower() not in ("true", "false"):
            return "Error: archived must be 'true' or 'false'"
        updates: dict[str, Any] = {"archived": value.lower() == "true"}
    elif field in IDEA_UPDATE_LIST_FIELDS:
        updates = {field: [x.strip() for x in value.split(",") if x.strip()]}
    else:
        updates = {field: value}
    body = value if field == "body" else ""

    tx = _store_tx()
    try:
        document, stored_body = _tx_doc("idea", idea_id)
    except KeyError:
        return f"Idea not found: {idea_id}"
    new_body = updates.pop("body", stored_body)
    archived = updates.pop("archived", None)
    try:
        fm = _apply_idea_updates(document, **updates)
    except ValueError as exc:
        return f"Error: {exc}"
    tx.put("idea", idea_id, fm, body=new_body)
    # Archival is never implicit: the flag on the row is what the store acts on,
    # so it moves through the explicit archive/unarchive calls.
    if archived is True:
        tx.archive("idea", idea_id)
    elif archived is False:
        tx.unarchive("idea", idea_id)
    _mutate_and_save(_load())

    # Plan C: auto-detect inline ID mentions on body updates.
    if body:
        try:
            _auto_link_entity(bp, idea_id)
        except Exception:
            pass

    return f"Idea updated: {idea_id} — {fm.get('title', '')}"


@mcp.tool()
def backlog_note(
    action: Literal["create", "list", "get", "update", "archive"],
    note_id: str = "",
    text: str = "",
    pinned: bool | None = None,
    include_archived: bool = False,
    limit: int = DEFAULT_LIST_LIMIT,
) -> str:
    """Manage sticky notes on the user's Desk. Notes are the lightest continuity
    surface: freeform, situational, NOT attached to tasks. Write at most one
    consolidated note per session, only for loose thoughts that fit no other
    entity (task/idea/issue/handover). Never archive or edit user-authored notes
    unless the user explicitly asks.

    Params by action: create(text, pinned); list(include_archived, limit);
    get(note_id); update(note_id, text, pinned); archive(note_id).
    """
    if action == "create":
        return backlog_note_create(text, bool(pinned))
    if action == "list":
        return backlog_note_list(include_archived, limit)
    if action == "get":
        return backlog_note_get(note_id)
    if action == "update":
        return backlog_note_update(note_id, text, pinned)
    if action == "archive":
        return backlog_note_archive(note_id)
    return f"Error: unknown action {action!r}"


@_transactional("backlog_note_create")
def backlog_note_create(text: str, pinned: bool = False) -> str:
    """Write a sticky note onto the user's Desk (dashboard).

    Notes are the lightest continuity surface: freeform, situational,
    NOT attached to tasks. Claude-created notes are stamped author
    "claude" and render visually distinct from the user's own notes.
    Write at most one consolidated note per session, and only for loose
    thoughts that fit no other entity (task/idea/issue/handover).
    """
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}. Run `backlog_init` first."
    result = _note_create_in_tx(text=text, author="claude", pinned=pinned)
    if isinstance(result, str):
        return f"Error: {result}"
    nid, target = result
    try:
        rel = target.relative_to(ROOT)
    except ValueError:
        rel = target
    _render_after_commit(
        lambda committed: f"Note created: {nid}\nFile: {rel}"
        if committed.get(("note", nid)) is not None
        else f"Note created: {NOT_PERSISTED}"
    )
    return f"Note created: {nid}\nFile: {rel}"


def _note_create_in_tx(*, text: str, author: str, pinned: bool):
    """Create one note row inside the open transaction; `(id, path)` or an error."""
    from taskmaster.taskmaster_v3 import (
        build_note_doc as _build_note_doc,
        note_path as _note_path,
    )
    try:
        document, body = _build_note_doc(text=text, author=author, pinned=pinned)
        nid = _store_tx().create("note", document, body=body)
    except ValueError as exc:
        return str(exc)
    _mutate_and_save(_load())
    return nid, _note_path(_backlog_path(), nid)


def _note_update_in_tx(note_id: str, *, text: str | None, pinned: bool | None):
    """Patch one note row inside the open transaction; None on success."""
    from taskmaster.taskmaster_v3 import apply_note_updates as _apply_note_updates
    try:
        document, body = _tx_doc("note", note_id)
    except KeyError:
        return f"Note not found: {note_id}"
    try:
        fm, new_body = _apply_note_updates(document, body, text=text, pinned=pinned)
    except ValueError as exc:
        return f"Error: {exc}"
    _store_tx().put("note", note_id, fm, body=new_body)
    _mutate_and_save(_load())
    return None


def _note_archive_in_tx(note_id: str):
    """Archive one note row inside the open transaction; None on success.

    The document keeps the `archived_at` stamp; the move into `notes/_archive/`
    follows from the row's archive flag, which `tx.archive` owns.
    """
    from taskmaster.taskmaster_v3 import archive_note_doc as _archive_note_doc
    tx = _store_tx()
    try:
        document, body = _tx_doc("note", note_id)
    except KeyError:
        return f"Note not found: {note_id}"
    tx.put("note", note_id, _archive_note_doc(document), body=body)
    tx.archive("note", note_id)
    _mutate_and_save(_load())
    return None


def backlog_note_list(include_archived: bool = False, limit: int = DEFAULT_LIST_LIMIT) -> str:
    """List sticky notes from the user's Desk — pinned first, newest first.

    Returns one line per note: id, author, pin marker, created date, first
    line of text. Caps at `limit` (default 50; 0 = no cap) with an overflow
    footer. Use during session start to surface the user's desk."""
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    notes = _note_records(_load(), include_archived=include_archived)
    if not notes:
        return "Desk is clear — no notes."
    notes, overflow = _cap_list(notes, limit)
    lines = []
    for n in notes:
        first = (n.get("body") or "").strip().splitlines()[0] if n.get("body") else ""
        pin = "📌 " if n.get("pinned") else ""
        arch = " [archived]" if n.get("archived") else ""
        created = str(n.get("created", ""))[:10]
        lines.append(f"- {n['id']} ({n.get('author')}, {created}){arch} — {pin}{first}")
    footer = _overflow_footer(overflow, "notes")
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def _note_records(data: dict, *, include_archived: bool = False) -> list[dict]:
    """Note documents with bodies, pinned first then created desc (id desc tiebreak)."""
    out: list[dict] = []
    for _nid, fm, body in _dict_rows(data, "note", include_archived=include_archived):
        out.append({**fm, "body": (body or "").rstrip("\n")})

    def _num(note: dict) -> int:
        match = re.search(r"(\d+)$", note.get("id", ""))
        return int(match.group(1)) if match else 0

    # Numeric-id tiebreak prevents nondeterminism when created timestamps tie.
    def _key(note: dict) -> tuple:
        return (note.get("created", ""), _num(note))

    pinned = [n for n in out if n.get("pinned")]
    unpinned = [n for n in out if not n.get("pinned")]
    pinned.sort(key=_key, reverse=True)
    unpinned.sort(key=_key, reverse=True)
    return pinned + unpinned


def backlog_note_get(note_id: str) -> str:
    """Read one sticky note in full (frontmatter + complete text)."""
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    row = _dict_row(_load(), "note", note_id)
    if row is None:
        return f"Note not found: {note_id}"
    fm, body = row[0], (row[1] or "").rstrip("\n")
    fm_lines = [f"  {k}: {v}" for k, v in fm.items()]
    return "---\n" + "\n".join(fm_lines) + "\n---\n" + body


@_transactional("backlog_note_update")
def backlog_note_update(note_id: str, text: str = "", pinned: bool | None = None) -> str:
    """Edit a sticky note's text and/or pin state. Author is immutable —
    a user-authored note stays user-authored even if Claude edits it
    (avoid editing user notes unless explicitly asked)."""
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    error = _note_update_in_tx(note_id, text=text or None, pinned=pinned)
    if error is not None:
        return error
    _render_after_commit(
        lambda committed: f"Note updated: {note_id}"
        if committed.get(("note", note_id)) is not None
        else f"Note updated: {NOT_PERSISTED}"
    )
    return f"Note updated: {note_id}"


@_transactional("backlog_note_archive")
def backlog_note_archive(note_id: str) -> str:
    """Archive a sticky note (moves it off the Desk into notes/_archive/).
    Never archive user-authored notes unless the user explicitly asks."""
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    error = _note_archive_in_tx(note_id)
    if error is not None:
        return error
    _render_after_commit(
        lambda committed: f"Note archived: {note_id}"
        if (committed.get(("note", note_id)) or {}).get("archived")
        else f"Note archived: {NOT_PERSISTED}"
    )
    return f"Note archived: {note_id}"


# ── Areas ────────────────────────────────────────────────────────

ALLOWED_AREA_FIELDS = {"name", "description", "anchors"}


@mcp.tool()
@_transactional("backlog_area_create")
def backlog_area_create(
    area_id: str, name: str, description: str = "", anchors: list[str] | None = None
) -> str:
    """Create a new Area — a long-lived subsystem/workstream with NO status
    lifecycle (e.g. "desktop-app", "viewer", "mcp-server").

    Areas group epics and tasks by where they live in the codebase rather
    than by when they finish — an area never completes or archives. If an
    epic can say when it's done, it's an epic; if it can't, it's an area.
    """
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}. Run `backlog_init` first."
    from taskmaster.taskmaster_v3 import (
        area_path as _area_path,
        validate_area_doc as _validate_area_doc,
    )
    fm = {
        "id": area_id,
        "name": name,
        "description": description,
        "anchors": list(anchors) if anchors else [],
        "created": _now(),
    }
    tx = _store_tx()
    try:
        document = _validate_area_doc(fm)
    except ValueError as exc:
        return f"Error: {exc}"
    # Area ids are caller-derived, so a collision is a user error with a
    # readable message rather than an exception out of the transaction.
    if tx.id_taken("area", area_id):
        return f"Error: area `{area_id}` already exists"
    tx.create("area", document, body="")
    _mutate_and_save(_load())
    target = _area_path(bp, area_id)
    try:
        rel = target.relative_to(ROOT)
    except ValueError:
        rel = target
    return f"Area created: {area_id} \u2014 {name}\nFile: {rel}"


@mcp.tool()
def backlog_area_list(limit: int = DEFAULT_LIST_LIMIT) -> str:
    """List all Areas — long-lived subsystems with no status lifecycle.

    Returns one line per area: id, name, anchor count, first line of
    description. Caps at `limit` (default 50; 0 = no cap) with an overflow
    footer."""
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    areas = [dict(doc) for _aid, doc, _body in _dict_rows(_load(), "area")]
    if not areas:
        return "No areas defined."
    areas, overflow = _cap_list(areas, limit)
    lines = []
    for a in areas:
        desc = (a.get("description") or "").strip().splitlines()[0] if a.get("description") else ""
        anchors = a.get("anchors") or []
        suffix = f": {desc}" if desc else ""
        lines.append(f"- {a['id']} — {a.get('name', '')} ({len(anchors)} anchors){suffix}")
    footer = _overflow_footer(overflow, "areas")
    if footer:
        lines.append(footer)
    return "\n".join(lines)


@mcp.tool()
def backlog_area_get(area_id: str) -> str:
    """Read one Area in full (frontmatter + body)."""
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    row = _dict_row(_load(), "area", area_id)
    if row is None:
        return f"Area not found: {area_id}"
    fm, body = row[0], (row[1] or "").rstrip("\n")
    fm_lines = [f"  {k}: {v}" for k, v in fm.items()]
    out = "---\n" + "\n".join(fm_lines) + "\n---"
    if body:
        out += "\n" + body
    return out


@mcp.tool()
@_transactional("backlog_area_update")
def backlog_area_update(area_id: str, field: str, value: str) -> str:
    """Update a single field on an Area.

    Args:
        area_id: The area ID (e.g., "desktop-app", "viewer")
        field: Field to update — one of: name, description, anchors
        value: New value. For anchors, pass a JSON array of path/glob
            strings (e.g., '["viewer/**", "docs/viewer/**"]').
    """
    if field not in ALLOWED_AREA_FIELDS:
        return f"Error: field `{field}` not allowed. Allowed: {', '.join(sorted(ALLOWED_AREA_FIELDS))}"
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    from taskmaster.taskmaster_v3 import apply_area_updates as _apply_area_updates
    if field == "anchors":
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return "Error: anchors value must be a JSON array of strings"
        if not isinstance(parsed, list):
            return "Error: anchors value must be a JSON array of strings"
        updates: dict = {"anchors": parsed}
    else:
        updates = {field: value}
    try:
        document, body = _tx_doc("area", area_id)
    except KeyError:
        return f"Area not found: {area_id}"
    try:
        fm = _apply_area_updates(document, updates)
    except ValueError as exc:
        return f"Error: {exc}"
    _store_tx().put("area", area_id, fm, body=body)
    _mutate_and_save(_load())
    return f"Area updated: {area_id} \u2014 field `{field}`"


@mcp.tool()
def viewer_prefs_get() -> str:
    """Return current viewer prefs as JSON."""
    import json
    prefs = load_viewer_prefs(_backlog_path())
    return json.dumps(prefs, indent=2)


@mcp.tool()
def viewer_prefs_set(patch_json: str) -> str:
    """Deep-merge a JSON patch into the persisted viewer prefs.
    Patch is a JSON object; only the keys present are updated.
    """
    import json
    try:
        patch = json.loads(patch_json)
    except Exception as e:
        return f"Error: invalid JSON ({e})"
    if not isinstance(patch, dict):
        return "Error: patch must be a JSON object"

    bp = _backlog_path()
    prefs = load_viewer_prefs(bp)
    _deep_merge(prefs, patch)
    save_viewer_prefs(bp, prefs)
    return "ok"


@mcp.tool()
@_transactional("backlog_add_task")
def backlog_add_task(
    title: str, epic: str, phase: str = "", priority: str = "medium",
    tldr: str = "", notes: str = "", next_step: str = "",
    depends_on: str = "", bundle: str = "",
    options: dict | None = None,
) -> str:
    """Create a new task under an epic. `phase` is required.

    Common fields are top-level. Rarely-set fields go in `options`:
    docs ("key:path;key:path"), sub_repo, stage (int), estimate (S/M/L),
    anchors (comma-separated globs/URLs), task_id (override the auto id),
    area (existing Area id). depends_on/anchors are comma-separated;
    tldr auto-generates from notes/title when omitted.
    """
    options = options or {}
    docs = options.get("docs", "") or ""
    sub_repo = options.get("sub_repo", "") or ""
    estimate = options.get("estimate", "") or ""
    anchors = options.get("anchors", "") or ""
    task_id = options.get("task_id", "") or ""
    area = options.get("area", "") or ""
    stage = options.get("stage")
    if isinstance(stage, str):
        stage = stage.strip()
        if not stage:
            stage = None
        else:
            try:
                stage = int(stage)
            except ValueError:
                return f"Error: stage must be an integer, got `{stage}`"

    priority = _normalize_priority(priority)
    if priority not in VALID_PRIORITIES:
        return f"Error: invalid priority `{priority}`. Valid: {', '.join(PRIORITY_NAMES)}"

    if area:
        err = _validate_area_ref(_backlog_path(), area)
        if err:
            return err

    data = _load()
    epic_obj = _find_epic(data, epic)
    if not epic_obj:
        return f"Error: epic `{epic}` not found. Valid epics: {_epic_names(data)}"

    # Resolve task ID: caller-supplied or allocated from authoritative state.
    # The old filesystem scan saw only this checkout, so a linked worktree whose
    # files lagged the store handed back an id the store already held — and the
    # compatibility write-back then replaced that task instead of creating one.
    tx = _store_tx()
    if task_id:
        if _find_task(data, task_id) or tx.id_taken("task", task_id):
            return f"Error: task ID `{task_id}` already exists"
        new_id = task_id
    else:
        new_id = tx.allocate_id("task", {"epic": epic})

    # tldr: use supplied value, or auto-generate from notes/title
    tldr_autogen = False
    if not tldr:
        body_source = notes or title
        tldr = extract_tldr(body_source) or title[:TLDR_MAX_CHARS]
        tldr_autogen = True

    new_task: dict = {
        "id": new_id,
        "title": title,
        "tldr": tldr,
        "status": "todo",
        "priority": priority,
        "created": _now(),
        "last_referenced": _now(),
        "notes": notes,
    }
    if tldr_autogen:
        new_task["tldr_autogen"] = True
    if next_step:
        new_task["next_step"] = next_step

    # Optional fields
    if depends_on:
        dep_ids = [d.strip() for d in depends_on.split(",") if d.strip()]
        for dep_id in dep_ids:
            if not _find_task(data, dep_id):
                return f"Error: dependency `{dep_id}` not found"
        new_task["depends_on"] = dep_ids
    if sub_repo:
        new_task["sub_repo"] = sub_repo
    if stage is not None:
        new_task["stage"] = stage
    if estimate:
        new_task["estimate"] = estimate
    if not phase:
        return "Error: `phase` is required — every task must belong to a phase. Use `backlog_phase_status()` to see available phases."
    if not _find_phase(data, phase):
        return f"Error: phase `{phase}` not found. Use `backlog_phase_status()` to see available phases."
    new_task["phase"] = _find_phase(data, phase)["id"]
    if anchors:
        anchor_list = [a.strip() for a in anchors.split(",") if a.strip()]
        new_task["anchors"] = anchor_list
    if bundle:
        if not _valid_bundle_slug(bundle):
            return f"Error: invalid bundle slug `{bundle}` (lowercase kebab, 2-41 chars)."
        conflict = _bundle_sub_repo_conflict(data, bundle, sub_repo)
        if conflict:
            return f"Error: bundle `{bundle}` sub_repo mismatch with member `{conflict}` (one worktree = one repo)."
        new_task["bundle"] = bundle
    if area:
        new_task["area"] = area

    # Parse docs if provided: "plan:path;spec:path"
    if docs:
        parsed_docs = {}
        for pair in docs.split(";"):
            pair = pair.strip()
            if ":" in pair:
                k, v = pair.split(":", 1)
                k, v = k.strip(), v.strip()
                if k in VALID_DOC_KEYS:
                    parsed_docs[k] = v
        if parsed_docs:
            new_task["docs"] = parsed_docs

    # Assign lane (standard; bumped to full for high/critical priority) and
    # initialize gate_state mirror so the slim field tier is populated on creation.
    new_task["lane"] = _default_lane(new_task.get("priority", "medium"))
    new_task["gate_state"] = _compute_gate_state(new_task)
    new_task["merge_gate_state"] = ""   # no merges yet

    if _detect_schema_version(data) >= SCHEMA_V4:
        new_task["epic"] = epic
        # Ordering comes from the transaction's own view of the epic, not from a
        # second filesystem scan that a peer's commit could already have outrun.
        orders: list[float] = []
        for sibling in epic_obj.get("tasks") or []:
            try:
                orders.append(float(sibling["order"]))
            except (KeyError, TypeError, ValueError):
                continue
        new_task["order"] = (max(orders) + 1.0) if orders else 1.0

    # Create the row first: the id was allocated from the store, so the store
    # is where the create belongs. Mirroring it into the dict afterwards keeps
    # navigation in this same call (the budget count below) consistent.
    tx.create("task", new_task, requested_id=new_id)
    if "tasks" not in epic_obj:
        epic_obj["tasks"] = []
    epic_obj["tasks"].append(new_task)

    _mutate_and_save(data)
    _enqueue_linear_push_if_synced(new_id, task=new_task)

    # Soft cap enforcement (only when explicitly set on the epic via `max_tasks`).
    # The previous default of 8 has been lifted — large epics with many tasks are
    # legitimate, and notes/work counts are not capped by default. Set
    # `epic.max_tasks` explicitly if you want a per-epic budget.
    budget_warning = ""
    if "max_tasks" in epic_obj:
        active_count = sum(
            1 for t in epic_obj.get("tasks", [])
            if t.get("status") not in ("archived", "done")
        )
        max_tasks = epic_obj["max_tasks"]
        if active_count > max_tasks:
            budget_warning = (
                f"\n\n**Warning:** Epic `{epic}` now has {active_count} active tasks "
                f"(this epic's `max_tasks` cap: {max_tasks})."
            )

    epic_name = epic_obj["name"]

    def _render(committed: dict) -> str:
        document = committed.get(("task", new_id))
        if document is None:
            return f"Added `{new_id}` — {NOT_PERSISTED}"
        return (
            f"Added `{new_id}` — {document.get('title', '')} "
            f"({document.get('priority', '')}) under {epic_name}" + budget_warning
        )

    _render_after_commit(_render)
    return f"Added `{new_id}` — {title} ({priority}) under {epic_obj['name']}" + budget_warning


def _build_worktree_instruction(
    task_id: str, sub_repo: str, branch: str, worktree: str,
    _path_key: str | None = None,
) -> str:
    """Build a worktree creation instruction for the pick_task response.

    Args:
        task_id:   Task ID (or bundle slug when called from the bundle path).
        sub_repo:  Sub-repo path, or empty string for root repo.
        branch:    Already-recorded branch (skip creation if set).
        worktree:  Already-recorded worktree path (skip creation if set).
        _path_key: Override the slug used to derive path/branch names.
                   When None (default), `task_id` is used as the path key,
                   preserving existing per-task behaviour.  The bundle path
                   passes the bundle slug here so the instruction references
                   `.worktrees/<slug>` / `feature/<slug>` instead of the
                   individual task-id.
    """
    if worktree:
        return f"\n\n**Worktree:** Already recorded at `{worktree}` — verify it exists and work there."

    key = _path_key or task_id
    branch_name = branch or f"feature/{key}"

    if sub_repo:
        wt_path = f"{sub_repo}/.worktrees/{key}"
        cmd = f"cd {sub_repo} && git worktree add .worktrees/{key} -b {branch_name}"
    else:
        wt_path = f"<sub-repo>/.worktrees/{key}"
        cmd = f"git worktree add .worktrees/{key} -b {branch_name}"

    return (
        f"\n\n**REQUIRED — Create a worktree before writing any code:**\n"
        f"```\n{cmd}\n```\n"
        f"Then record it:\n"
        f"```\nbacklog_update_task({task_id}, branch, {branch_name})\n"
        f"backlog_update_task({task_id}, worktree, .worktrees/{key})\n```\n"
        f"All work for this task MUST happen in the worktree, not on the main branch."
    )


@mcp.tool()
@_transactional("backlog_pick_task")
def backlog_pick_task(task_id: str, force: bool = False) -> str:
    """Start working on a task — sets it to in-progress. Idempotent if already in-progress.

    Args:
        task_id: The task ID to pick (e.g., "ue-plugin-003")
        force: Force-claim the task even if locked by another session. Use when a previous
               session ended without releasing the lock.
    """
    data = _load()
    result = _tx_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"

    task, epic = result
    _touch_task(task)
    status = task.get("status", "todo")

    # ── Bundle-aware pick ─────────────────────────────────────────────────────
    slug = task.get("bundle")
    if slug:
        members = _find_tasks_by_bundle(data, slug)
        # validate shared sub_repo
        sub_repos = {(m.get("sub_repo") or "") for m in members}
        if len(sub_repos) > 1:
            return f"Error: bundle `{slug}` spans multiple sub_repos {sub_repos}; cannot pick."
        sub_repo = next(iter(sub_repos))
        branch = f"feature/{slug}"
        worktree = (f"{sub_repo}/.worktrees/{slug}" if sub_repo else f".worktrees/{slug}")
        lane = _strictest_lane([m.get("lane") for m in members])
        # Check for foreign-session locks on any member
        for m in members:
            if m.get("locked_by") and m["locked_by"] != SESSION_ID and not force:
                return (
                    f"Error: `{m['id']}` is a member of bundle `{slug}` "
                    f"locked by another session ({m['locked_by']}). Use force=True to steal."
                )
        # Idempotent re-pick: if all members are already in-progress for this session,
        # just return the bundle context without re-mutating.
        all_bound = all(
            m.get("status") == "in-progress" and m.get("locked_by") == SESSION_ID
            for m in members
        )
        if not all_bound:
            for m in members:
                m["status"] = "in-progress"
                m["started"] = m.get("started") or _now()
                m["locked_by"] = SESSION_ID
                m["branch"] = branch
                m["worktree"] = worktree
                member_found = _find_task(data, m["id"])
                _tx_put_task(m, member_found[1] if member_found else None)
            _mutate_and_save(data)
        _set_session_task(task, epic)  # picked member stays "current task"
        _set_session_bundle({
            "slug": slug,
            "sub_repo": sub_repo,
            "branch": branch,
            "worktree": worktree,
            "members": [m["id"] for m in members],
            "lane": lane,
        })
        member_ids = ", ".join(m["id"] for m in members)
        # Pass empty worktree so the instruction always shows the creation command
        # with `feature/<slug>` and `.worktrees/<slug>` visible to the agent.
        # (The recorded worktree value is stored on every member task separately.)
        instr = _build_worktree_instruction(task_id, sub_repo, branch, "", _path_key=slug)
        return (
            f"Picking bundle `{slug}`: {member_ids}.\n"
            f"Execution lane: {lane}.\n"
            f"{instr}"
        )
    # ── End bundle-aware pick ─────────────────────────────────────────────────

    # Allowed statuses: todo, in-progress (idempotent), in-review (revert to in-progress)
    locked_by = task.get("locked_by")

    if status == "in-progress":
        if locked_by and locked_by != SESSION_ID:
            if not force:
                return (
                    f"Error: task `{task_id}` is locked by another session (`{locked_by}`). "
                    f"It is already in-progress elsewhere. Pick a different task, or use "
                    f"`backlog_pick_task({task_id}, force=true)` to reclaim it for this session."
                )
            # Force-claim: transfer lock to this session
            task["locked_by"] = SESSION_ID
            _tx_put_task(task, epic)
            _mutate_and_save(data)
        # Idempotent: update session state and lock, return details without mutation
        if not locked_by:
            task["locked_by"] = SESSION_ID
            _tx_put_task(task, epic)
            _mutate_and_save(data)
        _set_session_task(task, epic)
        sub_repo = task.get("sub_repo", "")
        branch = task.get("branch", "")
        worktree = task.get("worktree", "")
        worktree_instruction = _build_worktree_instruction(task_id, sub_repo, branch, worktree)
        context_text = _task_context(data, task, epic)
        _render_after_commit(
            lambda committed: f"Already in progress: `{task_id}` — "
            + (_committed_task_field(committed, task_id, "title") or NOT_PERSISTED)
            + "\n\n"
            + context_text
            + worktree_instruction
        )
        # Open handovers stay open automatically under the new model — no resumed transition needed.
        return f"Already in progress: `{task_id}` — {task['title']}\n\n" + context_text + worktree_instruction

    if status not in ("todo", "in-review"):
        # blocked/done tasks cannot be picked — use backlog_update_task to change status first
        return f"Error: task `{task_id}` is `{status}`, expected one of: todo, in-progress, in-review"

    # Surface unmet dependencies (B-050). Pick is an explicit override, so we warn
    # rather than block — but we no longer disagree silently with next_available,
    # which classifies a task with unmet deps as "blocked by dependencies".
    deps = task.get("depends_on", [])
    if isinstance(deps, str):
        deps = [deps]
    unmet_deps: list[str] = []
    for dep_id in deps:
        dep_result = _find_task(data, dep_id)
        dep_status = dep_result[0].get("status", "todo") if dep_result else "todo"
        if dep_status != "done":
            unmet_deps.append(dep_id)
    dep_warning = ""
    if unmet_deps:
        unmet_str = ", ".join(f"`{d}`" for d in unmet_deps)
        dep_warning = (
            f"\n\n⚠️ **Unmet dependencies:** {unmet_str} not yet done. "
            f"Picking anyway (explicit override) — `backlog_next_available` treats this task as blocked."
        )

    task["status"] = "in-progress"
    task["started"] = task.get("started") or _now()
    task["locked_by"] = SESSION_ID

    _tx_put_task(task, epic)
    _mutate_and_save(data)
    _set_session_task(task, epic)

    # Build worktree instruction
    sub_repo = task.get("sub_repo", "")
    branch = task.get("branch", "")
    worktree = task.get("worktree", "")
    worktree_instruction = _build_worktree_instruction(task_id, sub_repo, branch, worktree)
    context_text = _task_context(data, task, epic)

    def _render(committed: dict) -> str:
        document = committed.get(("task", task_id)) or {}
        if document.get("status") != "in-progress":
            head = f"Picked `{task_id}` — {NOT_PERSISTED}"
        else:
            head = f"Picked `{task_id}` — {document.get('title', '')} (locked to this session)"
        return head + dep_warning + "\n\n" + context_text + worktree_instruction

    _render_after_commit(_render)
    # Open handovers stay open automatically under the new model — no resumed transition needed.
    return f"Picked `{task_id}` — {task['title']} (locked to this session)" + dep_warning + "\n\n" + context_text + worktree_instruction


def _append_changelog(
    session_title: str,
    done: str,
    decisions: str,
    issues: str,
    tasks_touched: str,
    auto: bool = False,
    auto_stats: str = "",
) -> str:
    """Queue a changelog entry for PROGRESS.md, to be written by the store.

    The paragraph is handed to the open transaction rather than written here:
    PROGRESS.md gets exactly one writer, the store's export path, so a session
    summary and the task transition that produced it land in the same commit
    and can never half-apply.

    Returns a confirmation message for the tool response.
    """
    title = session_title or "Work session"

    if auto:
        heading = f"### {_today()} — auto"
        entry = f"{heading}\n{auto_stats}\nTasks touched: {tasks_touched}\n"
        if not _queue_changelog_entry(entry):
            return "\nNot logged: no open transaction to commit the changelog with."
        return f"\nSession auto-logged to PROGRESS.md."

    heading = f"### {_today()} — {title}"

    lines = [heading]

    # Done section
    done_items = [d.strip() for d in done.strip().split("\n") if d.strip()] if done.strip() else []
    lines.append("**Done:**")
    if done_items:
        for item in done_items:
            bullet = item if item.startswith("- ") else f"- {item}"
            lines.append(bullet)
    else:
        lines.append("- (no items logged)")

    # Decisions section
    decision_items = [d.strip() for d in decisions.strip().split("\n") if d.strip()] if decisions.strip() else []
    lines.append("")
    lines.append("**Decisions:**")
    if decision_items:
        for item in decision_items:
            bullet = item if item.startswith("- ") else f"- {item}"
            lines.append(bullet)
    else:
        lines.append("- None")

    # Issues section
    issue_items = [d.strip() for d in issues.strip().split("\n") if d.strip()] if issues.strip() else []
    lines.append("")
    lines.append("**Issues:**")
    if issue_items:
        for item in issue_items:
            bullet = item if item.startswith("- ") else f"- {item}"
            lines.append(bullet)
    else:
        lines.append("- None")

    # Tasks touched
    lines.append("")
    lines.append(f"**Tasks touched:** {tasks_touched or 'N/A'}")
    lines.append("")
    lines.append("---")
    lines.append("")

    entry = "\n".join(lines)
    if not _queue_changelog_entry(entry):
        return "\n\nNot logged: no open transaction to commit the changelog with."
    return f"\n\n**Session logged** to PROGRESS.md changelog."


def _queue_changelog_entry(entry: str) -> bool:
    """Commit one changelog paragraph as a row, for the store's export to write.

    False when there is no open transaction to carry it — the caller then
    reports that nothing was logged rather than writing the file behind the
    store. The paragraph is persisted rather than held in memory so a failed
    PROGRESS.md write cannot destroy the only copy of it.
    """
    try:
        tx = _store_tx()
    except RuntimeError:
        return False
    tx.queue_progress_log(entry)
    return True


def _smart_close_handovers_in_tx(data: dict, task_id: str) -> list[str]:
    """Auto-close or flag the open handovers a terminal task belongs to.

    Runs on the caller's transaction so the handover flips commit with the task
    transition that caused them. Best-effort: a handover the planner cannot
    evaluate leaves the task change alone.
    """
    try:
        from taskmaster.taskmaster_v3 import smart_auto_close_handovers as _smart_close
        terminal: set[str] = set()
        for epic in data.get("epics", []):
            for task in epic.get("tasks", []):
                if task.get("status") in ("done", "archived"):
                    terminal.add(task["id"])
        terminal.add(task_id)  # the one we just transitioned
        tx = _store_tx()
        plan = _smart_close(
            tx.list("handover"),
            triggering_task_id=task_id,
            done_or_archived_ids=terminal,
        )
        flipped = plan["closed"] + plan["flagged"]
        for handover_id, document, body in flipped:
            tx.put("handover", handover_id, document, body=body)
    except Exception:
        return []
    if flipped:
        data2 = _load()
        _sync_handover_index_tx(data2)
        _mutate_and_save(data2)
    return [handover_id for handover_id, _doc, _body in flipped]


def _open_bugs_for_task(bp, task_id: str) -> tuple[list[str], list[str]]:
    """Return (open_bugs, fixed_bugs) whose found_in matches task_id.

    Matching is case-insensitive (B-025): a bug filed with found_in="TEST-EPIC-001"
    must still gate task id "test-epic-001". Shared by complete_task and batch_update
    so both honor the same close-gate.
    """
    open_bugs: list[str] = []
    fixed_bugs: list[str] = []
    tid = (task_id or "").casefold()
    rows = _tx_rows("bug") if _active_tx() is not None else _dict_rows(_load(), "bug")
    for bid, bfm, _body in rows:
        if (bfm.get("found_in") or "").casefold() == tid:
            st = bfm.get("status")
            if st == "open":
                open_bugs.append(bid)
            elif st == "fixed":
                fixed_bugs.append(bid)
    return open_bugs, fixed_bugs


def _completion_block_reason(task) -> str:
    """Return a rejection message if a lane'd task has unsatisfied required gates, else ''."""
    if not task.get("lane"):
        return ""   # laneless => exempt (Spec A rollout rule)
    outstanding = _outstanding_required_gates(task)
    if outstanding:
        return (f"Cannot complete `{task['id']}` — outstanding gates for lane "
                f"`{task['lane']}`: {', '.join(outstanding)}. "
                f"Record each (backlog_record_gate) or skip it (backlog_skip_gate).")
    return ""


@mcp.tool()
@_transactional("backlog_complete_task")
def backlog_complete_task(
    task_id: str,
    session_title: str = "",
    done: str = "",
    decisions: str = "",
    issues: str = "",
    tasks_touched: str = "",
    target_status: str = "done",
    human_action: str = "",
    auto_summary: bool = False,
    patchnote: str = "",
    release: str = "",
) -> str:
    """Mark a task as done (or in-review) and optionally log a session summary to the PROGRESS.md changelog.

    When session summary fields are provided, a changelog entry is appended automatically.
    This combines the status transition and session logging into one atomic operation.

    Use target_status="done" (default) when Claude's work is complete and gates passed.
    Use target_status="in-review" ONLY when an action that only the human can perform
    blocks the task (API key, LLM config, account access) — pass it as human_action.

    Accepts tasks that are in-progress, in-review, or blocked.

    Args:
        task_id: The task ID to complete (e.g., "ue-plugin-003")
        session_title: Optional session title (e.g., "C++ Parser: Graph Analysis"). Auto-prefixed with today's date.
        done: Optional newline-separated list of accomplishments for the changelog
        decisions: Optional newline-separated list of decisions made
        issues: Optional newline-separated list of issues encountered. Use "None" if none.
        tasks_touched: Optional comma-separated task IDs that changed status this session
        target_status: Target status — "done" (default) or "in-review" (blocked on a human-only action)
        human_action: Required with target_status="in-review" (unless already set on the task): short imperative describing the human-only blocker, e.g. "add OPENAI_API_KEY to .env". Cleared automatically when the task reaches done.
        auto_summary: If true, generates a lightweight auto-summary instead of the structured format. Pass git stats as the done field.
        patchnote: Optional 1-2 sentence user-facing release-note line describing what shipped. Leave empty for internal/infra tasks.
        release: Optional release bucket this task ships in (e.g., "pre-alpha", "alpha-1.0"). Groups patchnotes for release notes.
    """
    if target_status not in ("done", "in-review"):
        return f"Error: target_status must be 'done' or 'in-review', got '{target_status}'"

    data = _load()
    result = _tx_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"

    task, epic = result
    _touch_task(task)
    status = task.get("status", "todo")

    if status not in ("in-progress", "in-review", "blocked"):
        return f"Error: task `{task_id}` is `{status}`, expected one of: in-progress, in-review, blocked"

    if target_status == "in-review":
        human_action = human_action.strip() or (task.get("human_action") or "").strip()
        if not human_action:
            return ("Error: target_status='in-review' requires human_action — the human-only "
                    "step that blocks this task (e.g. 'add OPENAI_API_KEY to .env'). "
                    "If nothing blocks it, target 'done'.")

    # Bug close-gate (per bug-tier redesign)
    bp = _backlog_path()
    from taskmaster.taskmaster_v3 import assert_bug_archivable as _assert_bug_archivable
    open_bugs, fixed_bugs = _open_bugs_for_task(bp, task_id)
    if open_bugs:
        return (
            f"Cannot complete {task_id} — {len(open_bugs)} open bug(s) linked via found_in: "
            f"{', '.join(open_bugs)}.\n"
            f"Resolve each (fix/adopt/shelve/promote) before closing the task."
        )

    # Gate-completeness guard (Spec A): a lane'd task may only go to `done`
    # once its required gates are satisfied. Laneless tasks are exempt.
    # Does not apply to target_status == "in-review".
    if target_status == "done":
        block = _completion_block_reason(task)
        if block:
            return block

    task["status"] = target_status
    if target_status == "done":
        task["completed"] = _now()
        task.pop("human_action", None)
    else:  # in-review — allowlist above guarantees it
        task["human_action"] = human_action
    task.pop("locked_by", None)

    if patchnote:
        task["patchnote"] = patchnote
    if release:
        task["release"] = release

    _tx_put_task(task, epic)
    _mutate_and_save(data)
    _enqueue_linear_push_if_synced(task_id, task=task)
    if target_status == "done":
        _clear_session_task(task_id)
        # Clear the session bundle when the last non-terminal member completes.
        # Guard: only act if there is a session bundle and the completed task
        # belongs to that bundle's slug.
        _sb = _get_session_bundle()
        _slug = task.get("bundle", "")
        if _sb and _slug and _sb.get("slug") == _slug:
            _remaining = _find_tasks_by_bundle(data, _slug)
            _non_terminal = [
                m for m in _remaining
                if m.get("status") not in ("done",)
            ]
            if not _non_terminal:
                _clear_session_bundle()

    if target_status == "done":
        _smart_close_handovers_in_tx(data, task_id)

    # Archive bugs that were fixed during this task (per bug-tier redesign).
    # Same transaction as the completion: a task that closed its bugs and a
    # rollback of that completion must not leave the bugs filed.
    if target_status == "done" and fixed_bugs:
        archived_any = False
        for bid in fixed_bugs:
            try:
                document, _body = _tx_doc("bug", bid)
            except KeyError:
                continue
            try:
                _assert_bug_archivable(document)
            except ValueError:
                continue
            _store_tx().archive("bug", bid)
            archived_any = True
        if archived_any:
            data3 = _load()
            _sync_bug_index_tx(data3)
            _mutate_and_save(data3)

    # Append changelog entry if session summary provided
    changelog_msg = ""
    if auto_summary:
        changelog_msg = _append_changelog(session_title, done, decisions, issues, tasks_touched, auto=True, auto_stats=done)
    elif session_title or done:
        changelog_msg = _append_changelog(session_title, done, decisions, issues, tasks_touched)

    # Suggest next task in same epic
    next_todo = [t for t in epic.get("tasks", []) if t.get("status") == "todo"]
    next_todo.sort(key=lambda t: ({"critical": 0, "high": 1, "medium": 2, "low": 3}.get(t.get("priority", "medium"), 9)))
    suggestion = ""
    if next_todo:
        n = next_todo[0]
        suggestion = f"\n\n**Next in {epic['name']}:** `{n['id']}` — {n['title']} ({n.get('priority', 'medium')})"

    status_label = "Completed" if target_status == "done" else "Moved to in-review"

    def _render(committed: dict) -> str:
        document = committed.get(("task", task_id)) or {}
        if document.get("status") != target_status:
            return f"{status_label} `{task_id}` — {NOT_PERSISTED}" + changelog_msg + suggestion
        return (
            f"{status_label} `{task_id}` — {document.get('title', '')}"
            + changelog_msg
            + suggestion
        )

    _render_after_commit(_render)
    return f"{status_label} `{task_id}` — {task['title']}" + changelog_msg + suggestion


def backlog_release_notes(release: str = "", group_by: str = "epic", include_unreleased: bool = False) -> str:
    """Aggregate user-facing patchnotes across tasks for a given release bucket.

    Returns markdown output grouping patchnotes by epic or phase — ready to feed into a
    release-notes writer. Only tasks with a non-empty `patchnote` field are included.
    Internal/infra tasks (no patchnote) are omitted by design.

    Args:
        release: Release bucket to filter on (e.g., "pre-alpha", "alpha-1.0"). Empty = all releases.
        group_by: "epic" (default) or "phase" — how to group the patchnotes in the output.
        include_unreleased: If true, also include tasks that have a patchnote but no release tag.
    """
    if group_by not in ("epic", "phase"):
        return f"Error: group_by must be 'epic' or 'phase', got '{group_by}'"

    data = _load()
    phases_by_id = {p["id"]: p for p in data.get("phases", [])}

    # group_key -> list of (task, epic)
    groups: dict[str, list[tuple[dict, dict]]] = {}
    group_labels: dict[str, str] = {}

    for epic in data.get("epics", []):
        for task in epic.get("tasks", []):
            note = task.get("patchnote", "").strip()
            if not note:
                continue
            task_release = task.get("release", "").strip()
            if release:
                if task_release != release:
                    continue
            elif not include_unreleased and not task_release:
                continue

            if group_by == "epic":
                key = epic["id"]
                group_labels[key] = epic.get("name", epic["id"])
            else:
                phase_id = task.get("phase", "")
                key = phase_id or "_no_phase"
                if phase_id and phase_id in phases_by_id:
                    group_labels[key] = phases_by_id[phase_id].get("name", phase_id)
                else:
                    group_labels[key] = "Unphased"

            groups.setdefault(key, []).append((task, epic))

    if not groups:
        filt = f" for release `{release}`" if release else ""
        return f"No patchnotes found{filt}."

    header = f"# Release Notes" + (f" — `{release}`" if release else " — all releases")
    lines = [header, ""]
    for key in sorted(groups.keys(), key=lambda k: group_labels.get(k, k).lower()):
        lines.append(f"## {group_labels[key]}")
        lines.append("")
        for task, _epic in groups[key]:
            tag = f" ({task['id']})"
            lines.append(f"- {task['patchnote'].strip()}{tag}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


VALID_ARCHIVE_REASONS = {"done", "deprecated", "duplicate", "wont-fix", "superseded"}


@mcp.tool()
@_transactional("backlog_archive_task")
def backlog_archive_task(task_id: str, reason: str = "done") -> str:
    """Archive a task — hides it from the board and default listings.
    Tasks with status `done`, `blocked`, or `todo` can be archived. Archiving captures WHY the task
    was archived (e.g., verified and done, deprecated, duplicate, won't fix).
    Note: `todo` tasks require a reason other than "done" (e.g., deprecated, duplicate, wont-fix, superseded).

    Args:
        task_id: The task ID to archive (e.g., "ue-plugin-003")
        reason: Why the task is being archived. One of: done, deprecated, duplicate, wont-fix, superseded. Default: done.
    """
    if reason not in VALID_ARCHIVE_REASONS:
        return f"Error: invalid reason `{reason}`. Valid: {', '.join(sorted(VALID_ARCHIVE_REASONS))}"

    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"

    task, epic = result
    status = task.get("status", "todo")

    if status not in ("done", "blocked", "todo"):
        return f"Error: task `{task_id}` is `{status}`, only `done`, `blocked`, or `todo` tasks can be archived"

    # todo tasks require a non-"done" reason
    if status == "todo" and reason == "done":
        return f"Error: cannot archive a `todo` task with reason `done`. Use one of: deprecated, duplicate, wont-fix, superseded"

    task["status"] = "archived"
    task["archive_reason"] = reason
    task["archived"] = _now()
    task.pop("locked_by", None)
    _archive_entity("task", task_id, task)
    _mutate_and_save(data)
    _enqueue_linear_push_if_synced(task_id, task=task)

    # Smart-close open handovers that reference this task.
    _smart_close_handovers_in_tx(data, task_id)

    return f"Archived `{task_id}` — {task['title']} (reason: {reason})"


# ── Worktree Discovery ───────────────────────────────────


def _git_subprocess_kwargs() -> dict:
    """Common kwargs for git subprocess calls — prevents hangs on Windows."""
    kwargs: dict = {"capture_output": True, "text": True, "timeout": 3}
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""}
    kwargs["env"] = env
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


@mcp.tool()
def backlog_last_session() -> str:
    """Get the most recent session summary from the PROGRESS.md changelog.
    Returns the last changelog entry (everything between the first and second ### headings)."""
    try:
        text = _progress_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return "No PROGRESS.md found."

    changelog_marker = "## Changelog"
    idx = text.find(changelog_marker)
    if idx == -1:
        return "No changelog section found in PROGRESS.md."

    # Find the first ### heading after ## Changelog
    after_changelog = text[idx + len(changelog_marker):]
    first_entry_start = after_changelog.find("### ")
    if first_entry_start == -1:
        return "No session entries found in changelog."

    # Find the second ### heading (end of first entry)
    rest = after_changelog[first_entry_start:]
    second_entry_start = rest.find("### ", 4)  # skip the first "### "
    if second_entry_start == -1:
        # Only one entry — take everything
        entry = rest.strip()
    else:
        entry = rest[:second_entry_start].strip()

    return f"**Last Session:**\n\n{entry}" if entry else "No session entries found in changelog."


# ── Session State (in-memory, per MCP server process) ────

_session_task: dict | None = None  # {"id", "title", "epic", "picked_at"}


def _set_session_task(task: dict, epic: dict) -> None:
    global _session_task
    _session_task = {
        "id": task["id"],
        "title": task["title"],
        "epic": epic["id"],
        "picked_at": datetime.now().isoformat(timespec="seconds"),
    }


def _clear_session_task(task_id: str) -> None:
    global _session_task
    if _session_task and _session_task["id"] == task_id:
        _session_task = None


_session_bundle: dict | None = None  # {"slug","sub_repo","branch","worktree","members":[id],"lane"}


def _get_session_bundle() -> dict | None:
    return _session_bundle


def _set_session_bundle(b: dict | None) -> None:
    global _session_bundle
    _session_bundle = b


def _clear_session_bundle() -> None:
    global _session_bundle
    _session_bundle = None


_BUNDLE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


def _valid_bundle_slug(value: str) -> bool:
    """Empty string clears the bundle (descope); otherwise must be lowercase kebab."""
    return value == "" or bool(_BUNDLE_SLUG_RE.match(value))


def _strictest_lane(lanes: list) -> str:
    """Return the strictest (most gates) lane from a list of lane values (or None/missing)."""
    order = {"express": 0, "standard": 1, "full": 2}
    present = [l for l in lanes if l in order] or ["standard"]
    return max(present, key=lambda l: order[l])


ALLOWED_FIELDS = {"title", "status", "priority", "notes", "branch", "worktree", "blockers", "docs", "depends_on", "sub_repo", "stage", "estimate", "locked_by", "review_instructions", "phase", "anchors", "blast_radius_depth", "patchnote", "release", "tldr", "next_step", "component", "design_change", "lane", "bundle", "area", "human_action"}
VALID_STATUSES = {"todo", "in-progress", "in-review", "done", "archived", "blocked"}
# Spec A Task 11: forward-transition table enforced on lane'd tasks via
# backlog_update_task. Laneless tasks are exempt (old permissive behavior).
# Same-status writes (value == current) bypass the table.
LEGAL_STATUS_TRANSITIONS = {
    "todo":        {"in-progress", "blocked", "archived"},
    "in-progress": {"in-review", "done", "blocked", "todo", "archived"},
    "in-review":   {"done", "in-progress", "blocked", "archived"},
    "blocked":     {"todo", "in-progress", "in-review", "archived"},
    "done":        {"in-review", "archived"},
    "archived":    {"todo"},
}
VALID_PRIORITIES = {"critical", "high", "medium", "low"}
VALID_DOC_KEYS = {"plan", "spec", "roadmap", "design", "analysis"}


@mcp.tool()
@_transactional("backlog_update_task")
def backlog_update_task(
    task_id: str, field: str = "", value: str = "",
    tldr: str = "", next_step: str = "",
) -> str:
    """Update a single field on a task. Status changes trigger appropriate date updates.

    Two calling styles are supported:
    - Field/value style: backlog_update_task(task_id, field, value) — the classic API.
    - Keyword style: backlog_update_task(task_id, tldr="...", next_step="...") — for tldr/next_step.

    Args:
        task_id: The task ID (e.g., "ue-plugin-003")
        field: Field to update — one of: title, status, priority, notes, branch, worktree, blockers,
            docs, depends_on, sub_repo, stage, estimate, locked_by, review_instructions, phase,
            patchnote, release, tldr, next_step, human_action
        value: New value. Format varies by field:
            - docs: "key:path" (e.g., "plan:docs/plans/foo.md")
            - depends_on: comma-separated task IDs (e.g., "cpp-parser-002,cpp-parser-003")
            - stage: integer
            - estimate: size string (e.g., "S", "M", "L")
            - sub_repo: sub-repo directory name for monorepo projects
            - locked_by: session ID to claim the lock, or "" to clear it
            - phase: phase ID to assign, or "" to clear
            - anchors: comma-separated glob patterns/URLs, or "" to clear
            - patchnote: 1-2 sentence user-facing release-note line, or "" to clear
            - release: release bucket this task ships in (e.g., "pre-alpha", "alpha-1.0"), or "" to clear
            - area: area id (must match an existing Area from `backlog_area_list`), or "" to clear
        tldr: One-sentence essence of the task (keyword style only).
        next_step: Concrete immediate action to take on this task (keyword style only).
    """
    # Guard against ambiguous mixed-style calls — silent field drops are worse
    # than an explicit error. Pick one calling convention per call.
    if (tldr or next_step) and (field or value):
        return "Error: use either field/value style or keyword style (tldr=/next_step=), not both"

    # Keyword style: tldr= / next_step= kwargs take precedence over field/value.
    if tldr or next_step:
        data = _load()
        result = _tx_task(data, task_id)
        if not result:
            return f"Error: task `{task_id}` not found"
        task, epic = result
        _touch_task(task)
        written = []
        if tldr:
            task["tldr"] = tldr
            task.pop("tldr_autogen", None)  # caller-supplied tldr is no longer auto-generated
            written.append("tldr")
        if next_step:
            task["next_step"] = next_step
            written.append("next_step")
        expected = {field: _expected_field(task, field) for field in written}
        _tx_put_task(task, epic)
        _mutate_and_save(data)
        _enqueue_linear_push_if_synced(task_id, task=task)
        _render_after_commit(
            lambda committed: f"Updated `{task_id}`: "
            + "; ".join(
                f"{name} → "
                + _committed_field_display(committed, task_id, name, expected[name])
                for name in written
            )
        )
        return f"Updated `{task_id}`: " + "; ".join(
            f"{name} → {tldr if name == 'tldr' else next_step}" for name in written
        )

    # Classic field/value style
    if not field:
        return "Error: provide either `field`/`value` or keyword args `tldr`/`next_step`"
    if field not in ALLOWED_FIELDS:
        return f"Error: field `{field}` not allowed. Allowed: {', '.join(sorted(ALLOWED_FIELDS))}"

    data = _load()
    result = _tx_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"

    task, epic = result
    _touch_task(task)

    if field == "status":
        if value not in VALID_STATUSES:
            return f"Error: invalid status `{value}`. Valid: {', '.join(sorted(VALID_STATUSES))}"
        # Spec A Task 11: enforce the forward-transition table for lane'd tasks
        # only. Laneless tasks keep the old permissive behavior. Same-status
        # writes (value == current) are always allowed — the guard only checks
        # when the status actually changes.
        cur = task.get("status", "todo")
        if value == "in-review" and value != cur and not (task.get("human_action") or "").strip():
            return (f"Error: `in-review` means blocked on a human-only action; set human_action first: "
                    f"backlog_update_task('{task_id}', 'human_action', '<what the human must do>')")
        refusal = illegal_transition_message(task, value)
        if refusal:
            return f"Error: `{task_id}`: {refusal}."
        if task.get("lane") and value != cur and value == "done":
            block = _completion_block_reason(task)
            if block:
                return block
        if value == "done":
            task.pop("human_action", None)
        task["status"] = value
        if value == "in-progress" and not task.get("started"):
            task["started"] = _now()
        elif value == "done" and not task.get("completed"):
            task["completed"] = _now()
        # Note: archived status is allowed via update_task for flexibility (prefer backlog_archive_task)
        # The store's archive flag is what moves the projection file in or out of
        # tasks/archive/, so the transition has to say so explicitly.
        _apply_archive_transition("task", task_id, task, before=cur, after=value)
        # Clear lock when leaving in-progress
        if value not in ("in-progress",):
            task.pop("locked_by", None)
    elif field == "priority":
        value = _normalize_priority(value)
        if value not in VALID_PRIORITIES:
            return f"Error: invalid priority `{value}`. Valid: {', '.join(PRIORITY_NAMES)}"
        task["priority"] = value
    elif field == "docs":
        # Parse "key:path" format, e.g. "plan:docs/plans/2026-03-11-foo.md"
        if ":" not in value:
            return f"Error: docs value must be `key:path` format (e.g., `plan:docs/plans/foo.md`). Valid keys: {', '.join(sorted(VALID_DOC_KEYS))}"
        doc_key, doc_path = value.split(":", 1)
        doc_key = doc_key.strip()
        doc_path = doc_path.strip()
        if doc_key not in VALID_DOC_KEYS:
            return f"Error: invalid docs key `{doc_key}`. Valid: {', '.join(sorted(VALID_DOC_KEYS))}"
        if "docs" not in task or not isinstance(task.get("docs"), dict):
            task["docs"] = {}
        task["docs"][doc_key] = doc_path
    elif field == "depends_on":
        # Comma-separated task IDs, e.g. "cpp-parser-002,cpp-parser-003"
        dep_ids = [d.strip() for d in value.split(",") if d.strip()]
        # Validate all deps exist
        for dep_id in dep_ids:
            if not _find_task(data, dep_id):
                return f"Error: dependency `{dep_id}` not found"
        task["depends_on"] = dep_ids
    elif field == "stage":
        try:
            task["stage"] = int(value)
        except ValueError:
            return f"Error: stage must be an integer, got `{value}`"
    elif field == "locked_by":
        if value == "" or value.lower() == "none":
            task.pop("locked_by", None)
        else:
            task["locked_by"] = value
    elif field == "phase":
        if value == "" or value.lower() == "none":
            task.pop("phase", None)
        else:
            if not _find_phase(data, value):
                return f"Error: phase `{value}` not found"
            task["phase"] = _find_phase(data, value)["id"]
    elif field == "anchors":
        if value == "" or value.lower() == "none":
            task.pop("anchors", None)
        else:
            task["anchors"] = [a.strip() for a in value.split(",") if a.strip()]
    elif field in ("patchnote", "release"):
        if value == "" or value.lower() == "none":
            task.pop(field, None)
        else:
            task[field] = value
    elif field == "blast_radius_depth":
        if value == "" or value.lower() == "none":
            task.pop("blast_radius_depth", None)
        elif value in ("shallow", "deep"):
            task["blast_radius_depth"] = value
        else:
            return f"Error: `blast_radius_depth` must be 'shallow', 'deep', or '' to clear. Got: `{value}`"
    elif field == "tldr":
        # Caller-supplied tldr clears the autogen flag. Empty value is rejected —
        # tldr is required on every task; use the kwarg API or recreate the task
        # to refresh from autogen.
        if not value:
            return "Error: tldr cannot be cleared — provide a non-empty value or use autogen"
        task["tldr"] = value
        task.pop("tldr_autogen", None)
    elif field == "next_step":
        if value == "" or value.lower() == "none":
            task.pop("next_step", None)
        else:
            task["next_step"] = value
    elif field == "lane":
        if value not in _VALID_LANES:
            return (f"Error: invalid lane `{value}`. "
                    f"Valid: {', '.join(_VALID_LANES)}")
        task["lane"] = value
        task["gate_state"] = _compute_gate_state(task)
    elif field == "component":
        if value == "" or value.lower() == "none":
            task.pop("component", None)
        else:
            comps = (epic.get("components") or {})
            if value not in comps:
                declared = ", ".join(sorted(comps)) or "(none declared)"
                return (f"Error: component `{value}` not declared on epic `{epic['id']}`. "
                        f"Declared: {declared}. Add it via backlog_update_epic(<epic>, 'components', ...).")
            task["component"] = value
    elif field == "design_change":
        truthy = value.strip().lower() in ("true", "1", "yes")
        if truthy:
            design = epic.get("design_status", "exploring")
            if design == "locked":
                return (f"Error: epic `{epic['id']}` design is locked — cannot flag a "
                        f"design-change task. To reopen, set the epic to revising "
                        f"(backlog_update_epic('{epic['id']}', 'design_status', 'revising')) "
                        f"and record the reason as a decision (taskmaster:decision).")
            task["design_change"] = True
        else:
            task.pop("design_change", None)
    elif field == "bundle":
        if not _valid_bundle_slug(value):
            return f"Error: invalid bundle slug `{value}` (lowercase kebab, 2-41 chars)."
        if value == "":
            task.pop("bundle", None)
        else:
            conflict = _bundle_sub_repo_conflict(data, value, task.get("sub_repo", ""), exclude_id=task_id)
            if conflict:
                return f"Error: bundle `{value}` sub_repo mismatch with member `{conflict}` (one worktree = one repo)."
            task["bundle"] = value
    elif field == "area":
        if value == "" or value.lower() == "none":
            task.pop("area", None)
        else:
            err = _validate_area_ref(_backlog_path(), value)
            if err:
                return err
            task["area"] = value
    else:
        task[field] = value

    # Plan C: auto-detect inline ID mentions when body-bearing fields change.
    # Runs before the latch so the link and the text that produced it land in
    # one commit instead of a second, post-commit write.
    if field in ("notes", "review_instructions"):
        try:
            _auto_link_task_in_tx(data, task_id)
        except Exception:
            pass

    expected = _expected_field(task, field)
    _tx_put_task(task, epic)
    _mutate_and_save(data)
    _enqueue_linear_push_if_synced(task_id, task=task)

    _render_after_commit(
        lambda committed: f"Updated `{task_id}` field `{field}` → "
        + _committed_field_display(committed, task_id, field, expected)
    )
    return f"Updated `{task_id}` field `{field}` → {value}"


@mcp.tool()
@_transactional("backlog_record_gate")
def backlog_record_gate(
    task_id: str,
    gate: str,
    verdict: str = "",
    status: str = "",
    commit_sha: str = "",
    spec_path: str = "",
    codex_used: bool = False,
    critical_count: int = 0,
    important_count: int = 0,
) -> str:
    """Record the outcome of a pipeline gate on a task. Generalizes spec-review to
    every gate (spec, plan, tests, impl, spec-review, plan-review, design-review,
    review-gate). Status gates take status="done"; review gates take a verdict in
    pass/warn/fail. Overwrites any prior record for that gate.

    Enforces ordering: for a task WITH a lane, an earlier required gate must be
    satisfied (pass/done/skipped) before a later one is recorded.

    Args:
        task_id: Task id.
        gate: One of spec|plan|tests|impl|spec-review|plan-review|design-review|review-gate.
        verdict: pass|warn|fail (for review gates).
        status: done (for status gates).
        commit_sha, spec_path, codex_used, critical_count, important_count: optional meta.
    """
    if gate not in _VALID_GATES:
        return f"Error: invalid gate `{gate}`. Valid: {', '.join(_VALID_GATES)}"

    is_verdict = gate in _VERDICT_GATES
    if is_verdict:
        if verdict not in _VALID_GATE_VERDICTS:
            return (f"Error: gate `{gate}` requires verdict in "
                    f"{', '.join(_VALID_GATE_VERDICTS)}, got `{verdict or '(none)'}`")
    else:
        if status != "done":
            return f"Error: status gate `{gate}` requires status=\"done\", got `{status or '(none)'}`"

    data = _load()
    result = _tx_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"
    task, _epic = result
    _touch_task(task)

    lane = task.get("lane")
    if lane and gate in _VERDICT_GATES:
        req = _blocking_gates(lane)
        gates_now = task.get("gates") or {}
        if gate in req:
            idx = req.index(gate)
            for earlier in req[:idx]:
                if not _gate_satisfied(gates_now.get(earlier)):
                    return (f"Error: cannot record `{gate}` for `{task_id}` — "
                            f"earlier required gate `{earlier}` is not satisfied "
                            f"(pass/skipped). Record or skip it first.")

    rec = {"at": _now()}
    if is_verdict:
        rec["verdict"] = verdict
        if commit_sha:
            rec["commit_sha"] = commit_sha
        if spec_path:
            rec["spec_path"] = spec_path
        rec["codex_used"] = bool(codex_used)
        rec["critical_count"] = int(critical_count)
        rec["important_count"] = int(important_count)
    else:
        rec["status"] = "done"
        if commit_sha:
            rec["commit_sha"] = commit_sha

    task.setdefault("gates", {})[gate] = rec
    task["gate_state"] = _compute_gate_state(task)
    _tx_put_task(task, _epic)
    _mutate_and_save(data)
    outcome = verdict if is_verdict else "done"

    def _render(committed: dict) -> str:
        document = committed.get(("task", task_id)) or {}
        record = (document.get("gates") or {}).get(gate) or {}
        # The outcome has to come out of the commit, not out of the request:
        # reading it back from `outcome` reported a wholly lost write as a
        # successful gate recording.
        stored = record.get("verdict") if is_verdict else record.get("status")
        if stored != outcome:
            return f"Recorded gate `{gate}` for `{task_id}` — {NOT_PERSISTED}"
        state = _committed_task_field(committed, task_id, "gate_state")
        return (
            f"Recorded gate `{gate}` = {stored} for `{task_id}` "
            f"(state: {state or 'laneless'})"
        )

    _render_after_commit(_render)
    return f"Recorded gate `{gate}` = {outcome} for `{task_id}` (state: {task['gate_state'] or 'laneless'})"


def _resolved_merge_targets() -> list[dict]:
    try:
        m = load_project_manifest(_project_root_or_cwd())
        if m is not None:
            return m.merge_targets_resolved()
    except Exception:
        pass
    from taskmaster.project import DEFAULT_MERGE_TARGETS
    return [dict(d) for d in DEFAULT_MERGE_TARGETS]


@mcp.tool()
@_transactional("backlog_record_merge")
def backlog_record_merge(task_id: str, rung: str, sha: str, merged_at: str = "") -> str:
    """Stamp a merge rung on a task: records that the task's branch landed at `rung`
    (e.g. develop|stage|master) at merge commit `sha`. Idempotent overwrite per rung.
    The manual fallback for the PostToolUse merge-recorder hook. Not a pipeline gate —
    no ordering is enforced; a rung label outside the ladder (e.g. "branch:<name>") is
    still recorded to preserve the audit trail.

    Args:
        task_id: Task id.
        rung: Rung label (ladder label or "branch:<name>" for untracked targets).
        sha: Merge commit SHA.
        merged_at: ISO timestamp; defaults to now.
    """
    if not (rung or "").strip():
        return "Error: rung is required"
    if not (sha or "").strip():
        return "Error: sha is required"
    data = _load()
    result = _tx_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"
    task, _epic = result
    _touch_task(task)
    task.setdefault("merge_status", {})[rung] = {
        "merged_at": merged_at or _now(), "merge_commit": sha,
    }
    task["merge_gate_state"] = _compute_merge_gate_state(task, _resolved_merge_targets())
    _tx_put_task(task, _epic)
    _mutate_and_save(data)

    def _render(committed: dict) -> str:
        document = committed.get(("task", task_id)) or {}
        recorded = ((document.get("merge_status") or {}).get(rung) or {}).get(
            "merge_commit", ""
        )
        ladder = _committed_task_field(committed, task_id, "merge_gate_state")
        shown = str(recorded)[:7] if recorded else NOT_PERSISTED
        return (
            f"Recorded merge for rung `{rung}` on `{task_id}` "
            f"(sha={shown}, ladder: {ladder or 'none'})"
        )

    _render_after_commit(_render)
    return f"Recorded merge for rung `{rung}` on `{task_id}` (sha={sha[:7]}, ladder: {task['merge_gate_state'] or 'none'})"


@mcp.tool()
@_transactional("backlog_skip_gate")
def backlog_skip_gate(task_id: str, gate: str, reason: str, by: str = "claude") -> str:
    """Record an explicit, audited skip of a pipeline gate — the ONLY way past a
    required gate without satisfying it. Always succeeds (for a valid gate+reason)
    and is flagged on the dashboard. No silent skips exist anywhere else.

    Args:
        task_id: Task id.
        gate: The gate to skip (see backlog_record_gate for valid names).
        reason: Required non-empty justification (becomes the paper trail).
        by: "claude" or "user".
    """
    if gate not in _VALID_GATES:
        return f"Error: invalid gate `{gate}`. Valid: {', '.join(_VALID_GATES)}"
    if not (reason or "").strip():
        return f"Error: skip_gate requires a non-empty reason (this is the audit trail)."

    data = _load()
    result = _tx_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"
    task, _epic = result
    _touch_task(task)

    task.setdefault("gates", {})[gate] = {
        "skipped": True, "reason": reason.strip(), "by": by, "at": _now(),
    }
    task["gate_state"] = _compute_gate_state(task)
    _tx_put_task(task, _epic)
    _mutate_and_save(data)

    def _render(committed: dict) -> str:
        document = committed.get(("task", task_id)) or {}
        record = (document.get("gates") or {}).get(gate) or {}
        # The audit trail is only worth anything if it is the stored reason,
        # not the one the caller passed in and the store may not have kept.
        if not record.get("skipped"):
            return f"⚠ Skipped gate `{gate}` for `{task_id}` — reason: {NOT_PERSISTED}"
        return (
            f"⚠ Skipped gate `{gate}` for `{task_id}` — "
            f"reason: {record.get('reason', '')} (by {record.get('by', '')})"
        )

    _render_after_commit(_render)
    return f"⚠ Skipped gate `{gate}` for `{task_id}` — reason: {reason.strip()} (by {by})"


@mcp.tool()
@_transactional("backlog_clear_gate")
def backlog_clear_gate(task_id: str, gate: str) -> str:
    """Remove a single gate record from a task and recompute gate_state.

    Args:
        task_id: Task id.
        gate: The gate to clear (see backlog_record_gate for valid names).
    """
    if gate not in _VALID_GATES:
        return f"Error: invalid gate `{gate}`. Valid: {', '.join(_VALID_GATES)}"
    data = _load()
    result = _tx_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"
    task, epic = result
    _touch_task(task)
    gates = task.get("gates") or {}
    if gate not in gates:
        return f"`{task_id}` had no `{gate}` gate record"
    del gates[gate]
    task["gates"] = gates
    task["gate_state"] = _compute_gate_state(task)
    _tx_put_task(task, epic)
    _mutate_and_save(data)

    def _render(committed: dict) -> str:
        document = committed.get(("task", task_id))
        if document is None or gate in (document.get("gates") or {}):
            return f"Cleared gate `{gate}` on `{task_id}` — {NOT_PERSISTED}"
        return f"Cleared gate `{gate}` on `{task_id}`"

    _render_after_commit(_render)
    return f"Cleared gate `{gate}` on `{task_id}`"


@mcp.tool()
def backlog_task_pipeline(task_id: str) -> str:
    """Show a task's lane, its required gate pipeline, and each gate's recorded
    state (pass/done/skipped/fail/pending), plus the one-line gate_state and the
    list of outstanding gates blocking `done`.
    """
    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"
    task, _ = result
    lane = task.get("lane")
    if not lane:
        return f"`{task_id}` is laneless (pre-protocol) — no pipeline enforced."
    gates = task.get("gates") or {}
    lines = [f"## Pipeline `{task_id}` — lane: **{lane}**",
             f"gate_state: `{task.get('gate_state') or '(none)'}`", ""]
    for g in _required_gates(lane):
        rec = gates.get(g)
        if not rec:
            mark = "○ pending"
        elif rec.get("skipped"):
            mark = f"⚠ skipped — {rec.get('reason', '')}"
        elif rec.get("verdict"):
            mark = f"{rec['verdict']}"
        else:
            mark = rec.get("status", "?")
        lines.append(f"- `{g}`: {mark}")
    outstanding = _outstanding_required_gates(task)
    lines.append("")
    lines.append("**Outstanding:** " + (", ".join(outstanding) if outstanding else "none — ready for done ✓"))
    return "\n".join(lines)


VALID_SPEC_REVIEW_VERDICTS = {"pass", "warn", "fail"}


@mcp.tool()
@_transactional("backlog_set_spec_review")
def backlog_set_spec_review(
    task_id: str,
    verdict: str,
    spec_path: str,
    codex_used: bool = False,
    critical_count: int = 0,
    important_count: int = 0,
) -> str:
    """Record a spec-review pass on a task. Thin alias over backlog_record_gate(gate="spec-review");
    also keeps the legacy `spec_review` dict for back-compat. Overwrites any prior record.

    Args:
        task_id: The task ID (e.g., "auth-003")
        verdict: One of "pass", "warn", "fail"
        spec_path: Path to the spec/plan file that was reviewed
        codex_used: True if the optional Codex adversarial pass was run
        critical_count: Number of critical findings
        important_count: Number of important findings
    """
    if verdict not in VALID_SPEC_REVIEW_VERDICTS:
        return (
            f"Error: invalid verdict `{verdict}`. "
            f"Valid: {', '.join(sorted(VALID_SPEC_REVIEW_VERDICTS))}"
        )
    out = backlog_record_gate(
        task_id, "spec-review", verdict=verdict, spec_path=spec_path,
        codex_used=codex_used, critical_count=critical_count,
        important_count=important_count,
    )
    if "Error" in out:
        return out
    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"
    task, _ = result
    task["spec_review"] = {
        "timestamp": _now(),
        "verdict": verdict,
        "codex_used": bool(codex_used),
        "critical_count": int(critical_count),
        "important_count": int(important_count),
        "spec_path": spec_path,
    }
    _mutate_and_save(data)
    return (
        f"Recorded spec-review for `{task_id}`: {verdict} "
        f"(codex={codex_used}, critical={critical_count}, important={important_count})"
    )


@mcp.tool()
@_transactional("backlog_clear_spec_review")
def backlog_clear_spec_review(task_id: str) -> str:
    """Remove the spec-review gate (and legacy spec_review mirror) from a task.
    Use when the spec was significantly revised and the prior review is no longer valid.

    Args:
        task_id: The task ID (e.g., "auth-003")
    """
    out = backlog_clear_gate(task_id, "spec-review")
    data = _load()
    result = _find_task(data, task_id)
    if result:
        task, _ = result
        if "spec_review" in task:
            del task["spec_review"]
            _mutate_and_save(data)
    return out


VALID_EPIC_STATUSES = {"active", "planned", "done", "archived"}
ALLOWED_EPIC_FIELDS = {"name", "status", "description", "docs", "components", "design_status", "done_when", "area"}
VALID_DESIGN_STATUSES = {"exploring", "proposed", "locked", "revising"}
EPIC_DONE_WHEN_REQUIRED_MSG = (
    "Epics are finite: 'done_when' is required. "
    "An epic that can't say when it's done is an area."
)


def _validate_components(components: dict) -> str:
    """Return '' if the components block is well-formed, else an error string.

    Shape: { <key>: { "title": str, "after": [<other keys>] } }.
    `after` edges must reference declared component keys (DAG not enforced here).
    """
    if not isinstance(components, dict):
        return "Error: components must be a JSON object {key: {title, after}}"
    keys = set(components)
    for key, spec in components.items():
        if key == "_unassigned":
            return "Error: `_unassigned` is a reserved component key"
        if key.lower() == "none":
            return "Error: `none` (case-insensitive) is a reserved component key"
        if not isinstance(spec, dict):
            return f"Error: component `{key}` must be an object with title/after"
        if "title" in spec and not isinstance(spec["title"], str):
            return f"Error: component `{key}` title must be a string"
        after = spec.get("after", [])
        if not isinstance(after, list):
            return f"Error: component `{key}` after must be a list of component keys"
        for ref in after:
            if ref == key:
                return f"Error: component `{key}` cannot reference itself in `after`"
            if ref not in keys:
                return f"Error: component `{key}` after references unknown component `{ref}`"
    return ""


@mcp.tool()
@_transactional("backlog_update_epic")
def backlog_update_epic(epic_id: str, field: str, value: str) -> str:
    """Update a single field on an epic.

    Args:
        epic_id: The epic ID (e.g., "cpp-parser", "ue-plugin", "infra")
        field: Field to update — one of: name, status, description, docs
        value: New value. For status, one of: active, planned, done.
            For docs, use "key:path" format (e.g., "design:docs/design/foo.md").
            Valid doc keys mirror task docs: plan, spec, roadmap, design, analysis.
            Pass "key:" (empty path) to remove a single doc entry.
    """
    if field not in ALLOWED_EPIC_FIELDS:
        return f"Error: field `{field}` not allowed. Allowed: {', '.join(sorted(ALLOWED_EPIC_FIELDS))}"

    data = _load()
    epic = _find_epic(data, epic_id)
    if not epic:
        return f"Error: epic `{epic_id}` not found"

    if field == "status":
        if value == "archived":
            return "Error: use `backlog_archive_epic` to archive an epic (it cascades to tasks)"
        if value not in VALID_EPIC_STATUSES:
            return f"Error: invalid epic status `{value}`. Valid: {', '.join(sorted(VALID_EPIC_STATUSES))}"

    if field == "docs":
        # Mirror task docs: "key:path" format, sharing VALID_DOC_KEYS.
        if ":" not in value:
            return (
                f"Error: docs value must be `key:path` format "
                f"(e.g., `design:docs/design/foo.md`). "
                f"Valid keys: {', '.join(sorted(VALID_DOC_KEYS))}"
            )
        doc_key, doc_path = value.split(":", 1)
        doc_key = doc_key.strip()
        doc_path = doc_path.strip()
        if doc_key not in VALID_DOC_KEYS:
            return f"Error: invalid docs key `{doc_key}`. Valid: {', '.join(sorted(VALID_DOC_KEYS))}"
        if "docs" not in epic or not isinstance(epic.get("docs"), dict):
            epic["docs"] = {}
        if doc_path == "":
            epic["docs"].pop(doc_key, None)
            if not epic["docs"]:
                epic.pop("docs", None)
            _mutate_and_save(data)
            return f"Cleared epic `{epic_id}` doc key `{doc_key}`"
        epic["docs"][doc_key] = doc_path
        _mutate_and_save(data)
        return f"Updated epic `{epic_id}` doc `{doc_key}` → `{doc_path}`"

    if field == "components":
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return "Error: components value must be a JSON object {key: {title, after}}"
        err = _validate_components(parsed)
        if err:
            return err
        epic["components"] = parsed
        _mutate_and_save(data)
        return f"Updated epic `{epic_id}` components ({len(parsed)} declared)"

    if field == "design_status":
        if value not in VALID_DESIGN_STATUSES:
            return f"Error: invalid design_status `{value}`. Valid: {', '.join(sorted(VALID_DESIGN_STATUSES))}"
        epic["design_status"] = value
        _mutate_and_save(data)
        return f"Updated epic `{epic_id}` design_status → `{value}`"

    if field == "done_when":
        if not value.strip():
            return f"Error: {EPIC_DONE_WHEN_REQUIRED_MSG}"

    if field == "area":
        if value:
            err = _validate_area_ref(_backlog_path(), value)
            if err:
                return err

    old_value = epic.get(field, "")
    epic[field] = value
    if field == "status":
        # Moving an archived epic back to active must clear the row's archive
        # flag; a document-only status change left the row archived for good.
        _apply_archive_transition(
            "epic", epic_id, epic, before=str(old_value), after=value
        )
    _mutate_and_save(data)
    return f"Updated epic `{epic_id}` field `{field}`: `{old_value}` → `{value}`"


def _archive_epic_cascade(epic: dict, epic_id: str, reason: str) -> int:
    """Archive an epic and every live task under it; returns the cascade count.

    One policy, shared by `backlog_archive_epic` and the batch `update_epic`
    op: a batch archive that skipped the cascade left live tasks pointing at an
    archived epic, and a status flip with no `tx.archive` left the row's archive
    flag at 0 so the projection never moved.
    """
    now = _now()
    epic["status"] = "archived"
    epic["archive_reason"] = reason
    epic["archived"] = now
    _archive_entity("epic", epic_id, epic)
    _store_tx().put("epic", epic_id, {k: v for k, v in epic.items() if k != "tasks"})

    cascaded = 0
    for task in epic.get("tasks", []) or []:
        if task.get("status") != "archived":
            task["status"] = "archived"
            task["archive_reason"] = reason
            task["archived"] = now
            task.pop("locked_by", None)
            _archive_entity("task", task["id"], task)
            # The archive flag alone is not the cascade: without writing the
            # document, a later operation in the same batch refreshes this
            # dict node from a row that still holds the pre-cascade status,
            # lock and missing reason, and silently reverts all three.
            _tx_put_task(task, epic)
            cascaded += 1
    return cascaded


@mcp.tool()
@_transactional("backlog_archive_epic")
def backlog_archive_epic(epic_id: str, reason: str = "done") -> str:
    """Archive an epic and all its tasks — hides the epic from the board and default listings.
    Cascades: every non-archived task in the epic is also archived with the same reason.

    Args:
        epic_id: The epic ID (e.g., "features", "infra")
        reason: Why the epic is being archived. One of: done, deprecated, duplicate, wont-fix, superseded. Default: done.
    """
    if reason not in VALID_ARCHIVE_REASONS:
        return f"Error: invalid reason `{reason}`. Valid: {', '.join(sorted(VALID_ARCHIVE_REASONS))}"

    data = _load()
    epic = _find_epic(data, epic_id)
    if not epic:
        return f"Error: epic `{epic_id}` not found"

    if epic.get("status") == "archived":
        return f"Error: epic `{epic_id}` is already archived"

    cascaded = _archive_epic_cascade(epic, epic_id, reason)

    _mutate_and_save(data)
    return f"Archived epic `{epic_id}` — {epic.get('name', epic_id)} ({cascaded} tasks cascaded, reason: {reason})"


@mcp.tool()
@_transactional("backlog_add_epic")
def backlog_add_epic(
    epic_id: str, name: str, done_when: str, description: str = "",
    status: str = "planned", area: str = "",
) -> str:
    """Create a new epic. Epics group related tasks into workstreams and are
    strictly finite — every epic must say when it's done. If the work can't
    say when it's done, it's an Area (see `backlog_area_create`), not an epic.

    Args:
        epic_id: Short kebab-case identifier (e.g., "auth-system", "perf-opt"). Must be unique. Used as prefix for task IDs.
        name: Human-readable name (e.g., "Authentication System", "Performance Optimization")
        done_when: Required. The concrete condition under which this epic is complete
            (e.g., "auth flow ships to prod with SSO + MFA"). Cannot be empty.
        description: Brief description of the epic's scope and goals
        status: Initial status — one of: active, planned (default: planned)
        area: Optional area id (e.g., "desktop-app") this epic lives under. Must
            match an existing Area from `backlog_area_list`; leave empty if unset.
    """
    if not done_when.strip():
        return f"Error: {EPIC_DONE_WHEN_REQUIRED_MSG}"

    if status not in VALID_EPIC_STATUSES:
        return f"Error: invalid status `{status}`. Valid: {', '.join(sorted(VALID_EPIC_STATUSES))}"

    # Validate epic_id format: lowercase, kebab-case
    if not epic_id or not all(c.isalnum() or c == "-" for c in epic_id) or epic_id != epic_id.lower():
        return f"Error: epic_id must be lowercase kebab-case (e.g., 'auth-system'), got `{epic_id}`"

    if area:
        err = _validate_area_ref(_backlog_path(), area)
        if err:
            return err

    data = _load()

    # Check for duplicate
    if _find_epic(data, epic_id):
        return f"Error: epic `{epic_id}` already exists"

    new_epic = {
        "id": epic_id,
        "name": name,
        "status": status,
        "description": description,
        "created": _now(),
        "tasks": [],
        "done_when": done_when,
    }
    if area:
        new_epic["area"] = area

    data["epics"].append(new_epic)
    _mutate_and_save(data)
    return f"Created epic `{epic_id}` — {name} ({status})"


# ── Phase Tools ──────────────────────────────────────


VALID_PHASE_STATUSES = {"planned", "active", "done", "archived"}
ALLOWED_PHASE_FIELDS = {"name", "status", "description", "order", "target_date", "start_date", "deliverables", "docs"}


@mcp.tool()
@_transactional("backlog_add_phase")
def backlog_add_phase(
    phase_id: str, name: str, description: str = "", order: int | None = None,
    target_date: str = "", start_date: str = "",
) -> str:
    """Create a new phase. Phases are temporal attention scopes for sequential ordering, NOT feature
    groupings — features belong in epics. Only one phase is active at a time; tasks are assigned
    to phases to control focus.

    Args:
        phase_id: Short kebab-case identifier (e.g., "foundation", "mvp", "polish"). Must be unique. Avoid "p1"/"p2" — too similar to priority names.
        name: Human-readable name (e.g., "Foundation", "Core Features", "Polish & Launch")
        description: Brief description of the phase's goals
        order: Position in the sequence (1, 2, 3...). Auto-assigned if omitted.
        target_date: Optional target completion date (YYYY-MM-DD format)
        start_date: Optional start date (YYYY-MM-DD format). Auto-set to today if status is active and omitted.
    """
    # Validate ID format
    if not phase_id or not all(c.isalnum() or c == "-" for c in phase_id) or phase_id != phase_id.lower():
        return f"Error: phase_id must be lowercase kebab-case (e.g., 'foundation', 'mvp'), got `{phase_id}`"

    data = _load()

    # Exact ID match only — fuzzy matching would cause false positives
    if any(ph["id"] == phase_id for ph in data.get("phases", [])):
        return f"Error: phase `{phase_id}` already exists"

    if "phases" not in data:
        data["phases"] = []

    # Auto-assign order
    if order is None:
        existing_orders = [ph.get("order", 0) for ph in data["phases"]]
        order = max(existing_orders, default=0) + 1

    # Validate dates if provided
    if target_date and not _validate_date(target_date):
        return f"Error: target_date must be YYYY-MM-DD format, got `{target_date}`"
    if start_date and not _validate_date(start_date):
        return f"Error: start_date must be YYYY-MM-DD format, got `{start_date}`"

    # Auto-activate if this is the first phase
    status = "planned"
    if not any(ph.get("status") == "active" for ph in data["phases"]):
        status = "active"

    new_phase = {
        "id": phase_id,
        "name": name,
        "status": status,
        "description": description,
        "order": order,
        "created": _now(),
    }
    if target_date:
        new_phase["target_date"] = target_date
    if start_date:
        new_phase["start_date"] = start_date
    elif status == "active":
        new_phase["start_date"] = _today()

    data["phases"].append(new_phase)
    _mutate_and_save(data)

    status_note = f" (auto-activated — first phase)" if status == "active" else ""
    return f"Created phase `{phase_id}` — {name} (order: {order}){status_note}"


@mcp.tool()
@_transactional("backlog_update_phase")
def backlog_update_phase(phase_id: str, field: str, value: str) -> str:
    """Update a single field on a phase.

    Args:
        phase_id: The phase ID (e.g., "foundation", "mvp")
        field: Field to update — one of: name, status, description, order, target_date, start_date
        value: New value. For status: planned, active, done, archived. For order: integer. For dates: YYYY-MM-DD or empty to clear.
    """
    if field not in ALLOWED_PHASE_FIELDS:
        return f"Error: field `{field}` not allowed. Allowed: {', '.join(sorted(ALLOWED_PHASE_FIELDS))}"

    data = _load()
    ph = _find_phase(data, phase_id)
    if not ph:
        return f"Error: phase `{phase_id}` not found"

    if field == "status":
        if value not in VALID_PHASE_STATUSES:
            return f"Error: invalid status `{value}`. Valid: {', '.join(sorted(VALID_PHASE_STATUSES))}"
        # If activating, deactivate any currently active phase
        if value == "active":
            for other_ph in data.get("phases", []):
                if other_ph["id"] != phase_id and other_ph.get("status") == "active":
                    other_ph["status"] = "planned"
            if not ph.get("start_date"):
                ph["start_date"] = _today()
        if value == "done":
            ph["completed"] = _now()
        before_status = str(ph.get("status") or "")
        ph["status"] = value
        if value == "archived":
            ph["archived"] = _now()
        # Phases keep no archive directory, but the row flag still has to agree
        # with the document, and the change log has to record the transition.
        _apply_archive_transition(
            "phase", phase_id, ph, before=before_status, after=value
        )
    elif field == "order":
        try:
            ph["order"] = int(value)
        except ValueError:
            return f"Error: order must be an integer, got `{value}`"
    elif field in ("target_date", "start_date"):
        if value == "":
            ph.pop(field, None)
        else:
            if not _validate_date(value):
                return f"Error: {field} must be YYYY-MM-DD format, got `{value}`"
            ph[field] = value
    elif field == "deliverables":
        # value is a JSON string: {"action": "add"|"remove"|"toggle"|"set", ...}
        try:
            cmd = json.loads(value)
        except (ValueError, TypeError):
            return "Error: deliverables value must be JSON — {\"action\": \"add\", \"text\": \"...\"}"

        deliverables = ph.setdefault("deliverables", [])
        action = cmd.get("action", "")

        if action == "add":
            text = cmd.get("text", "").strip()
            if not text:
                return "Error: deliverable text is required"
            deliverables.append({"text": text, "done": False})
        elif action == "remove":
            idx = cmd.get("index")
            if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(deliverables):
                return f"Error: invalid index {idx} — phase has {len(deliverables)} deliverables"
            deliverables.pop(idx)
        elif action == "toggle":
            idx = cmd.get("index")
            if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(deliverables):
                return f"Error: invalid index {idx} — phase has {len(deliverables)} deliverables"
            deliverables[idx]["done"] = not deliverables[idx]["done"]
        elif action == "set":
            items = cmd.get("items", [])
            ph["deliverables"] = [{"text": str(d.get("text", "")), "done": bool(d.get("done", False))} for d in items]
        else:
            return f"Error: unknown deliverables action `{action}`. Use: add, remove, toggle, set"
    elif field == "docs":
        if ":" not in value:
            return (f"Error: docs value must be `key:path` format "
                    f"(e.g. `design:docs/design/ship.md`). Valid keys: {', '.join(sorted(VALID_DOC_KEYS))}")
        doc_key, doc_path = (s.strip() for s in value.split(":", 1))
        if doc_key not in VALID_DOC_KEYS:
            return f"Error: invalid docs key `{doc_key}`. Valid: {', '.join(sorted(VALID_DOC_KEYS))}"
        if not isinstance(ph.get("docs"), dict):
            ph["docs"] = {}
        if doc_path == "":
            ph["docs"].pop(doc_key, None)
            if not ph["docs"]:
                ph.pop("docs", None)
        else:
            ph["docs"][doc_key] = doc_path
    else:
        ph[field] = value

    _mutate_and_save(data)
    return f"Updated phase `{phase_id}` field `{field}` → {value}"


@mcp.tool()
def backlog_phase_status(phase_id: str = "") -> str:
    """Show detailed progress for a phase. Defaults to the active phase.

    Args:
        phase_id: Phase ID. If omitted, shows the active phase.
    """
    data = _load()

    if phase_id:
        ph = _find_phase(data, phase_id)
        if not ph:
            return f"Error: phase `{phase_id}` not found"
    else:
        ph = _active_phase(data)
        if not ph:
            return "No active phase. Create one with `backlog_add_phase`."

    stats = _phase_stats(data, ph["id"])

    # Get all phases sorted by order for sequential context
    all_phases = sorted(data.get("phases", []), key=lambda p: p.get("order", 999))
    current_idx = next((i for i, p in enumerate(all_phases) if p["id"] == ph["id"]), -1)
    prev_phase = all_phases[current_idx - 1] if current_idx > 0 else None
    next_phase = all_phases[current_idx + 1] if current_idx < len(all_phases) - 1 else None

    # Phase sequence header
    phase_num = current_idx + 1
    total_phases = len(all_phases)
    lines = [f"## Phase {phase_num}/{total_phases}: {ph['name']}\n"]

    if ph.get("description"):
        lines.append(f"{ph['description']}\n")

    # Previous/next phase context
    if prev_phase:
        prev_status = "completed" if prev_phase.get("status") == "done" else prev_phase.get("status", "planned")
        lines.append(f"**Previous:** {prev_phase['name']} ({prev_status})")
    if next_phase:
        lines.append(f"**Next up:** {next_phase['name']} — {next_phase.get('description', 'no description')}")
    if prev_phase or next_phase:
        lines.append("")

    # Retrospective for done phases
    if ph.get("status") == "done":
        lines.append("**Status:** Completed")
        # Duration
        if ph.get("start_date") and ph.get("completed"):
            try:
                start = datetime.strptime(str(ph["start_date"]), "%Y-%m-%d").date()
                comp_str = str(ph["completed"])
                comp = datetime.fromisoformat(comp_str).date() if "T" in comp_str else datetime.strptime(comp_str, "%Y-%m-%d").date()
                duration_days = (comp - start).days
                lines.append(f"**Duration:** {duration_days} days ({ph['start_date']} → {comp_str[:10]})")
            except ValueError:
                if ph.get("completed"):
                    lines.append(f"**Completed:** {str(ph['completed'])[:10]}")
        elif ph.get("completed"):
            lines.append(f"**Completed:** {str(ph['completed'])[:10]}")
        # On-time analysis
        if ph.get("target_date") and ph.get("completed"):
            try:
                target = datetime.strptime(str(ph["target_date"]), "%Y-%m-%d").date()
                comp_str = str(ph["completed"])
                comp = datetime.fromisoformat(comp_str).date() if "T" in comp_str else datetime.strptime(comp_str, "%Y-%m-%d").date()
                delta = (comp - target).days
                if delta < 0:
                    lines.append(f"**On-time:** Yes ({abs(delta)}d early)")
                elif delta == 0:
                    lines.append("**On-time:** Yes (exact)")
                else:
                    lines.append(f"**On-time:** No ({delta}d late)")
            except ValueError:
                pass
        # Count archived tasks as completed work
        total_completed = stats["done"] + stats["archived"]
        lines.append(f"**Tasks completed & archived:** {total_completed}")
        lines.append("")
    else:
        lines.append(f"**Status:** {ph['status']} | **Order:** {ph.get('order', '?')}")
        # Date info
        date_parts = []
        if ph.get("start_date"):
            date_parts.append(f"Started: {ph['start_date']}")
        if ph.get("target_date"):
            date_parts.append(f"Target: {ph['target_date']}")
            remaining = _time_remaining(ph["target_date"])
            if remaining:
                date_parts.append(f"**{remaining}**")
        if date_parts:
            lines.append(" | ".join(date_parts))
        lines.append(f"**Progress:** {stats['done']}/{stats['total']} done")
        if stats["total"] > 0:
            pct = int(stats["done"] / stats["total"] * 100)
            bar_filled = pct // 5
            bar_empty = 20 - bar_filled
            lines.append(f"[{'█' * bar_filled}{'░' * bar_empty}] {pct}%")
        lines.append("")

        # Breakdown by status
        lines.append(f"Done: {stats['done']} | In Progress: {stats['in-progress']} | In Review: {stats['in-review']} | Todo: {stats['todo']} | Blocked: {stats['blocked']}")
        lines.append("")

    # Deliverables checklist
    deliverables = ph.get("deliverables", [])
    if deliverables:
        lines.append("")
        lines.append("**Deliverables:**")
        for i, d in enumerate(deliverables):
            check = "x" if d.get("done") else " "
            lines.append(f"  - [{check}] {d['text']}")
        done_count = sum(1 for d in deliverables if d.get("done"))
        lines.append(f"  ({done_count}/{len(deliverables)} complete)")
        lines.append("")

    # List tasks in this phase grouped by status
    status_groups = ["in-progress", "in-review", "todo", "blocked", "done"]
    if ph.get("status") == "done":
        status_groups.append("archived")
    for status_group in status_groups:
        group_tasks = []
        for epic in data["epics"]:
            for t in epic.get("tasks", []):
                if t.get("phase") == ph["id"] and t.get("status") == status_group:
                    group_tasks.append((t, epic))

        if group_tasks:
            label = "Completed (Archived)" if status_group == "archived" else status_group.replace("-", " ").title()
            lines.append(f"**{label}:**")
            for t, epic in group_tasks:
                pri = t.get("priority", "medium")
                lines.append(f"- `{t['id']}` — {t['title']} ({pri}, {epic['id']})")
            lines.append("")

    # Unassigned tasks hint
    unassigned_count = 0
    for epic in data["epics"]:
        for t in epic.get("tasks", []):
            if t.get("status") not in ("done", "archived") and not t.get("phase"):
                unassigned_count += 1
    if unassigned_count:
        lines.append(f"*{unassigned_count} active tasks are not assigned to any phase.*")

    return "\n".join(lines)


def _epic_stats(data: dict, epic_id: str) -> dict:
    """Status counts for an epic's tasks.

    Unlike _phase_stats (which subtracts archived from total to give an
    active-scope denominator), this keeps archived tasks in `total` so that
    the `done + archived` numerator never exceeds it — epic-level lifetime
    progress, not "what's left to do".
    """
    stats = {"total": 0, "done": 0, "in-progress": 0, "in-review": 0,
             "todo": 0, "blocked": 0, "archived": 0}
    epic = _find_epic(data, epic_id)
    for t in (epic.get("tasks", []) if epic else []):
        st = t.get("status", "todo")
        stats["total"] += 1
        if st in stats:
            stats[st] += 1
    stats["closeable"] = stats["total"] > 0 and stats["done"] + stats["archived"] == stats["total"]
    return stats


@mcp.tool()
def backlog_epic_status(epic_id: str) -> str:
    """Show progress for an epic: status counts, per-component rollup, and the
    design-maturity lock. Derived on read from the epic's tasks — no stored rollup.

    Args:
        epic_id: The epic ID (e.g. "asset-engine").
    """
    data = _load()
    epic = _find_epic(data, epic_id)
    if not epic:
        return f"Error: epic `{epic_id}` not found"

    stats = _epic_stats(data, epic_id)
    roll = _component_rollup(data, epic_id)
    lines = [f"## Epic `{epic_id}` — {epic.get('name', '')}\n"]

    design = epic.get("design_status", "exploring")
    lock = " (locked)" if design == "locked" else ""
    lines.append(f"**Design:** {design}{lock}")
    lines.append(f"**Status:** {epic.get('status', 'active')}")
    if epic.get("done_when"):
        lines.append(f"Done when: {epic['done_when']}")

    done = stats["done"] + stats["archived"]
    if stats["total"]:
        pct = int(done / stats["total"] * 100)
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        lines.append(f"**Progress:** {done}/{stats['total']} done")
        lines.append(f"[{bar}] {pct}%")
    lines.append(
        f"Done: {stats['done']} | Archived: {stats['archived']} | "
        f"In Progress: {stats['in-progress']} | "
        f"In Review: {stats['in-review']} | Todo: {stats['todo']} | Blocked: {stats['blocked']}"
    )
    if stats["closeable"]:
        lines.append(
            f"\n⚑ CLOSEABLE — all {stats['total']} tasks done; archive via backlog_archive_epic"
        )

    glyph = {"done": "█", "in-progress": "▨", "blocked": "✗", "todo": "□"}
    lines.append("\n**Components:**")
    for key, spec in (epic.get("components") or {}).items():
        b = roll.get(key, {})
        title = (spec or {}).get("title", key)
        lines.append(f"- {glyph.get(b.get('status'), '□')} {title} "
                     f"({b.get('done', 0)}/{b.get('total', 0)})")
    if roll.get("_unassigned", {}).get("total"):
        u = roll["_unassigned"]
        lines.append(f"- · unassigned ({u['done']}/{u['total']})")

    # Risk / attention surface (derived). Decision + failed-gate bubbling
    # is added in Spec A / when decisions gain an epic link (extension point).
    attention = []
    for t in epic.get("tasks", []):
        if t.get("status") == "blocked":
            why = t.get("blockers")
            attention.append(f"⏸ {t['id']} blocked" + (f": {why}" if why else ""))
        elif t.get("blockers"):
            attention.append(f"⚠ {t['id']}: {t['blockers']}")
    if attention:
        lines.append("\n**Attention:**")
        lines.extend(f"- {a}" for a in attention)

    return "\n".join(lines)


@mcp.tool()
@_transactional("backlog_advance_phase")
def backlog_advance_phase(force: bool = False) -> str:
    """Complete the active phase and activate the next one in sequence.
    Archives all 'done' tasks in the completed phase. Activates the next 'planned' phase by order.
    Blocks if phase has unchecked deliverables unless force=True.

    Args:
        force: If True, advance even if deliverables are incomplete.
    """
    data = _load()
    active_ph = _active_phase(data)
    if not active_ph:
        return "No active phase to advance."

    ph_stats = _phase_stats(data, active_ph["id"])

    # Block if deliverables are incomplete (unless force=True)
    deliverables = active_ph.get("deliverables", [])
    unchecked = [d for d in deliverables if not d.get("done")]
    if unchecked and not force:
        items = "\n".join(f"  - [ ] {d['text']}" for d in unchecked)
        return (
            f"**Blocked:** {len(unchecked)} unchecked deliverable(s) in phase "
            f"**{active_ph['name']}**:\n{items}\n\n"
            f"Check them off with `backlog_update_phase(phase_id=\"{active_ph['id']}\", "
            f"field=\"deliverables\", value='{{\"action\":\"toggle\",\"index\":N}}')` "
            f"or advance with force=True."
        )

    # Warn if there are incomplete tasks
    incomplete = ph_stats["todo"] + ph_stats["in-progress"] + ph_stats["in-review"] + ph_stats["blocked"]
    warning = ""
    if incomplete > 0:
        warning = (
            f"\n\n**Warning:** {incomplete} tasks in this phase are not done "
            f"(todo: {ph_stats['todo']}, in-progress: {ph_stats['in-progress']}, "
            f"in-review: {ph_stats['in-review']}, blocked: {ph_stats['blocked']}). "
            f"They will remain in their current status but the phase will be marked done."
        )

    # Mark active phase as done
    active_ph["status"] = "done"
    active_ph["completed"] = _now()

    # Archive done tasks in this phase
    archived_count = 0
    for epic in data["epics"]:
        for t in epic.get("tasks", []):
            if t.get("phase") == active_ph["id"] and t.get("status") == "done":
                t["status"] = "archived"
                t["archive_reason"] = "done"
                t["archived"] = _now()
                _archive_entity("task", t["id"], t)
                archived_count += 1

    # Find and activate next planned phase by order
    planned = [ph for ph in data.get("phases", []) if ph.get("status") == "planned"]
    planned.sort(key=lambda m: m.get("order", 999))
    next_ph = planned[0] if planned else None

    if next_ph:
        next_ph["status"] = "active"
        if not next_ph.get("start_date"):
            next_ph["start_date"] = _today()

    _mutate_and_save(data)

    result = f"Completed phase **{active_ph['name']}** — archived {archived_count} done tasks."
    if active_ph.get("start_date"):
        try:
            start = datetime.strptime(str(active_ph["start_date"]), "%Y-%m-%d").date()
            duration = (date.today() - start).days
            result += f" Duration: {duration}d."
        except ValueError:
            pass
    if active_ph.get("target_date"):
        try:
            target = datetime.strptime(str(active_ph["target_date"]), "%Y-%m-%d").date()
            delta = (date.today() - target).days
            if delta <= 0:
                result += " Completed on time."
            else:
                result += f" Completed {delta}d past target."
        except ValueError:
            pass
    if next_ph:
        next_stats = _phase_stats(data, next_ph["id"])
        result += f"\n\nActivated next phase: **{next_ph['name']}** ({next_stats['total']} tasks, order: {next_ph.get('order', '?')})"
        if next_ph.get("description"):
            result += f"\n{next_ph['description']}"
    else:
        result += "\n\nNo more planned phases. Create one with `backlog_add_phase`."

    return result + warning


@mcp.tool()
@_transactional("backlog_batch_update")
def backlog_batch_update(operations: str) -> str:
    """Apply multiple task/epic updates in a single atomic operation. One load/save cycle.

    Use this instead of calling backlog_update_task repeatedly — it's faster and atomic.

    Args:
        operations: Newline-separated list of operations. Each line format:
            "update {task_id} {field} {value}" — update a task field
            "status {task_id} {new_status}" — shorthand for status changes
            "complete {task_id}" — mark task done (shorthand for status done)
            "archive {task_id} [reason]" — archive a task (default reason: done)
            "pick {task_id}" — set task to in-progress with started timestamp
            "update_epic {epic_id} {field} {value}" — update an epic field
    """
    data = _load()
    results: list[str] = []
    errors: list[str] = []
    # One renderer per applied line, so each line reports what the store kept
    # for *its own* entity rather than what the loop believed it had written.
    line_renderers: list = []
    changed = False

    def _status_line(entity_id: str, expected_status: str, reason_field: str = ""):
        def render(committed: dict) -> str:
            document = committed.get(("task", entity_id))
            if document is None or document.get("status") != expected_status:
                return f"`{entity_id}` → {NOT_PERSISTED}"
            if not reason_field:
                return f"`{entity_id}` → {expected_status}"
            # The parenthetical is the stored reason, not the requested one.
            return (
                f"`{entity_id}` → {expected_status} "
                f"({document.get(reason_field, NOT_PERSISTED)})"
            )

        return render

    for line in operations.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 3)  # split into max 4 parts
        if len(parts) < 2:
            errors.append(f"Skipped malformed line: `{line}`")
            continue

        op = parts[0].lower()

        if op == "update" and len(parts) >= 4:
            task_id, field, value = parts[1], parts[2], parts[3]
            if field not in ALLOWED_FIELDS:
                errors.append(f"`{task_id}`: field `{field}` not allowed")
                continue
            result = _tx_task(data, task_id)
            if not result:
                errors.append(f"`{task_id}`: not found")
                continue
            task, epic = result
            # Apply field update using same logic as backlog_update_task
            if field == "status":
                if value not in VALID_STATUSES:
                    errors.append(f"`{task_id}`: invalid status `{value}`")
                    continue
                if value == "in-review" and task.get("status") != "in-review" and not (task.get("human_action") or "").strip():
                    errors.append(f"`{task_id}`: in-review requires human_action — set it first via backlog_update_task")
                    continue
                refusal = illegal_transition_message(task, value)
                if refusal:
                    errors.append(f"`{task_id}`: {refusal}")
                    continue
                if value == "done":
                    task.pop("human_action", None)
                prior_status = task.get("status", "todo")
                task["status"] = value
                if value == "in-progress" and not task.get("started"):
                    task["started"] = _now()
                elif value == "done" and not task.get("completed"):
                    task["completed"] = _now()
                _apply_archive_transition(
                    "task", task_id, task, before=prior_status, after=value
                )
                if value not in ("in-progress",):
                    task.pop("locked_by", None)
            elif field == "priority":
                value = _normalize_priority(value)
                if value not in VALID_PRIORITIES:
                    errors.append(f"`{task_id}`: invalid priority `{value}`")
                    continue
                task["priority"] = value
            elif field == "docs":
                if ":" not in value:
                    errors.append(f"`{task_id}`: docs must be `key:path` format")
                    continue
                doc_key, doc_path = value.split(":", 1)
                if doc_key.strip() not in VALID_DOC_KEYS:
                    errors.append(f"`{task_id}`: invalid docs key `{doc_key.strip()}`")
                    continue
                if "docs" not in task or not isinstance(task.get("docs"), dict):
                    task["docs"] = {}
                task["docs"][doc_key.strip()] = doc_path.strip()
            elif field == "depends_on":
                dep_ids = [d.strip() for d in value.split(",") if d.strip()]
                bad = [d for d in dep_ids if not _find_task(data, d)]
                if bad:
                    errors.append(f"`{task_id}`: dependencies not found: {', '.join(bad)}")
                    continue
                task["depends_on"] = dep_ids
            elif field == "stage":
                try:
                    task["stage"] = int(value)
                except ValueError:
                    errors.append(f"`{task_id}`: stage must be integer")
                    continue
            elif field == "locked_by":
                if value == "" or value.lower() == "none":
                    task.pop("locked_by", None)
                else:
                    task["locked_by"] = value
            elif field == "phase":
                if value == "" or value.lower() == "none":
                    task.pop("phase", None)
                else:
                    if not _find_phase(data, value):
                        errors.append(f"`{task_id}`: phase `{value}` not found")
                        continue
                    task["phase"] = _find_phase(data, value)["id"]
            elif field == "lane":
                # I2: validate lane and recompute gate_state mirror, mirroring
                # backlog_update_task's lane branch exactly.
                if value not in _VALID_LANES:
                    errors.append(
                        f"`{task_id}`: invalid lane `{value}`. "
                        f"Valid: {', '.join(_VALID_LANES)}"
                    )
                    continue
                task["lane"] = value
                task["gate_state"] = _compute_gate_state(task)
            elif field == "area":
                if value == "" or value.lower() == "none":
                    task.pop("area", None)
                else:
                    err = _validate_area_ref(_backlog_path(), value)
                    if err:
                        errors.append(f"`{task_id}`: {err}")
                        continue
                    task["area"] = value
            else:
                task[field] = value
            expected = _expected_field(task, field)
            _tx_put_task(task, epic)
            results.append(f"`{task_id}`.{field} → {value}")
            line_renderers.append(
                lambda committed, tid=task_id, fld=field, exp=expected: (
                    f"`{tid}`.{fld} → "
                    + _committed_field_display(committed, tid, fld, exp)
                )
            )
            changed = True

        elif op == "status" and len(parts) >= 3:
            task_id, new_status = parts[1], parts[2]
            if new_status not in VALID_STATUSES:
                errors.append(f"`{task_id}`: invalid status `{new_status}`")
                continue
            result = _tx_task(data, task_id)
            if not result:
                errors.append(f"`{task_id}`: not found")
                continue
            task, epic = result
            if new_status == "in-review" and task.get("status") != "in-review" and not (task.get("human_action") or "").strip():
                errors.append(f"`{task_id}`: in-review requires human_action — set it first via backlog_update_task")
                continue
            if new_status == "done":
                # Same lifecycle guard + close-gate as backlog_complete_task (B-049).
                # It runs before the transition table so a refused completion
                # keeps naming the states a task may be completed *from*, which
                # is the more actionable half of the same refusal.
                cur_status = task.get("status", "todo")
                if cur_status not in ("in-progress", "in-review", "blocked"):
                    errors.append(f"`{task_id}`: cannot complete from `{cur_status}` (expected in-progress/in-review/blocked)")
                    continue
            refusal = illegal_transition_message(task, new_status)
            if refusal:
                errors.append(f"`{task_id}`: {refusal}")
                continue
            if new_status == "done":
                open_bugs, _ = _open_bugs_for_task(_backlog_path(), task_id)
                if open_bugs:
                    errors.append(f"`{task_id}`: {len(open_bugs)} open bug(s) linked via found_in: {', '.join(open_bugs)}")
                    continue
                # I1: apply Spec-A completion gate (lane'd tasks must satisfy
                # all blocking review gates before reaching done).
                block = _completion_block_reason(task)
                if block:
                    errors.append(f"`{task_id}`: {block}")
                    continue
            prior_status = task.get("status", "todo")
            task["status"] = new_status
            if new_status == "in-progress" and not task.get("started"):
                task["started"] = _now()
            elif new_status == "done":
                task["started"] = task.get("started") or _now()
                if not task.get("completed"):
                    task["completed"] = _now()
                task.pop("human_action", None)
            _apply_archive_transition(
                "task", task_id, task, before=prior_status, after=new_status
            )
            if new_status not in ("in-progress",):
                task.pop("locked_by", None)
            _tx_put_task(task, epic)
            results.append(f"`{task_id}` → {new_status}")
            line_renderers.append(_status_line(task_id, new_status))
            changed = True

        elif op == "complete" and len(parts) >= 2:
            task_id = parts[1]
            result = _tx_task(data, task_id)
            if not result:
                errors.append(f"`{task_id}`: not found")
                continue
            task, epic = result
            # Honor the lifecycle guard + close-gate as backlog_complete_task,
            # backfill `started` so duration analytics stay correct (B-049),
            # and apply the Spec-A completion gate so lane'd tasks with
            # outstanding review gates are rejected (I1).
            cur_status = task.get("status", "todo")
            if cur_status not in ("in-progress", "in-review", "blocked"):
                errors.append(f"`{task_id}`: cannot complete from `{cur_status}` (expected in-progress/in-review/blocked)")
                continue
            open_bugs, _ = _open_bugs_for_task(_backlog_path(), task_id)
            if open_bugs:
                errors.append(f"`{task_id}`: {len(open_bugs)} open bug(s) linked via found_in: {', '.join(open_bugs)}")
                continue
            # I1: apply Spec-A completion gate (lane'd tasks must satisfy
            # all blocking review gates before reaching done).
            block = _completion_block_reason(task)
            if block:
                errors.append(f"`{task_id}`: {block}")
                continue
            task["status"] = "done"
            task["started"] = task.get("started") or _now()
            if not task.get("completed"):
                task["completed"] = _now()
            task.pop("locked_by", None)
            task.pop("human_action", None)
            _tx_put_task(task, epic)
            results.append(f"`{task_id}` → done")
            line_renderers.append(_status_line(task_id, "done"))
            changed = True

        elif op == "archive" and len(parts) >= 2:
            task_id = parts[1]
            reason = parts[2] if len(parts) > 2 else "done"
            result = _tx_task(data, task_id)
            if not result:
                errors.append(f"`{task_id}`: not found")
                continue
            task, epic = result
            already_archived = task.get("status") == "archived"
            task["status"] = "archived"
            task["archive_reason"] = reason
            task.pop("locked_by", None)
            if not already_archived:
                task["archived"] = _now()
            # Archive first, then write: the explicit archive is what records
            # the transition, and a put that had already raised the row's flag
            # would turn it into a silent no-op.
            _archive_entity("task", task_id, task)
            _tx_put_task(task, epic)
            results.append(f"`{task_id}` → archived ({reason})")
            line_renderers.append(_status_line(task_id, "archived", "archive_reason"))
            changed = True

        elif op == "pick" and len(parts) >= 2:
            task_id = parts[1]
            result = _tx_task(data, task_id)
            if not result:
                errors.append(f"`{task_id}`: not found")
                continue
            task, epic = result
            prior_status = task.get("status", "todo")
            if prior_status not in ("todo", "in-progress", "in-review"):
                errors.append(
                    f"`{task_id}`: is `{prior_status}`, expected one of: "
                    "todo, in-progress, in-review"
                )
                continue
            task["status"] = "in-progress"
            if not task.get("started"):
                task["started"] = _now()
            _apply_archive_transition(
                "task", task_id, task, before=prior_status, after="in-progress"
            )
            _tx_put_task(task, epic)
            results.append(f"`{task_id}` → in-progress")
            line_renderers.append(_status_line(task_id, "in-progress"))
            changed = True

        elif op == "update_epic" and len(parts) >= 4:
            epic_id, field, value = parts[1], parts[2], parts[3]
            if field not in ALLOWED_EPIC_FIELDS:
                errors.append(f"epic `{epic_id}`: field `{field}` not allowed")
                continue
            epic = _find_epic(data, epic_id)
            if not epic:
                errors.append(f"epic `{epic_id}`: not found")
                continue
            if field == "status" and value not in VALID_EPIC_STATUSES:
                errors.append(f"epic `{epic_id}`: invalid status `{value}`")
                continue
            if field == "status" and value == "archived":
                # Same policy as backlog_archive_epic: the cascade is the point
                # of archiving an epic, and the batch must not skip it.
                if epic.get("status") == "archived":
                    errors.append(f"epic `{epic_id}`: already archived")
                    continue
                cascaded = _archive_epic_cascade(epic, epic_id, "done")
                results.append(
                    f"epic `{epic_id}`.status → archived "
                    f"({cascaded} tasks cascaded)"
                )
                line_renderers.append(
                    lambda committed, eid=epic_id, n=cascaded: (
                        f"epic `{eid}`.status → "
                        + _committed_field_display(
                            committed, eid, "status", "archived", kind="epic"
                        )
                        + f" ({n} tasks cascaded)"
                    )
                )
                changed = True
                continue
            before_value = str(epic.get(field, "") or "")
            epic[field] = value
            if field == "status":
                _apply_archive_transition(
                    "epic", epic_id, epic, before=before_value, after=value
                )
            expected_epic = _expected_field(epic, field)
            results.append(f"epic `{epic_id}`.{field} → {value}")
            line_renderers.append(
                lambda committed, eid=epic_id, fld=field, exp=expected_epic: (
                    f"epic `{eid}`.{fld} → "
                    + _committed_field_display(committed, eid, fld, exp, kind="epic")
                )
            )
            changed = True

        else:
            errors.append(f"Unknown or malformed: `{line}`")

    if changed:
        _mutate_and_save(data)

    def _summary(lines: list[str]) -> str:
        text = f"**Batch update:** {len(lines)} applied"
        if errors:
            text += f", {len(errors)} errors"
        text += "\n\n"
        if lines:
            text += "**Applied:**\n" + "\n".join(f"- {r}" for r in lines) + "\n"
        if errors:
            text += "\n**Errors:**\n" + "\n".join(f"- {e}" for e in errors) + "\n"
        return text

    _render_after_commit(
        lambda committed: _summary([render(committed) for render in line_renderers])
    )
    return _summary(results)


@mcp.tool()
def backlog_batch_preview(operations: str) -> str:
    """Preview what a batch of task operations would do without writing to disk.

    Args:
        operations: Newline-separated list of operations to preview. Each line is:
            "complete {task_id}" — preview marking a task as done
            "archive {task_id}" — preview archiving a task
            "pick {task_id}" — preview picking a task
            "status {task_id} {new_status}" — preview a status change
    """
    data = _load()
    previews: list[str] = []

    for line in operations.strip().split("\n"):
        parts = line.strip().split()
        if len(parts) < 2:
            previews.append(f"- Skipped malformed line: `{line.strip()}`")
            continue

        op = parts[0].lower()
        task_id = parts[1]
        result = _find_task(data, task_id)

        if not result:
            previews.append(f"- `{task_id}`: NOT FOUND")
            continue

        task, epic = result
        current_status = task.get("status", "todo")

        if op == "complete":
            if current_status in ("in-progress", "in-review", "blocked"):
                previews.append(f"- `{task_id}` ({current_status} → done): {task['title']}")
            else:
                previews.append(f"- `{task_id}`: Cannot complete — currently `{current_status}`")

        elif op == "archive":
            if current_status in ("done", "blocked", "todo"):
                reason = parts[2] if len(parts) > 2 else ("done" if current_status == "done" else "deprecated")
                previews.append(f"- `{task_id}` ({current_status} → archived, reason: {reason}): {task['title']}")
            else:
                previews.append(f"- `{task_id}`: Cannot archive — currently `{current_status}`")

        elif op == "pick":
            if current_status in ("todo", "in-review"):
                previews.append(f"- `{task_id}` ({current_status} → in-progress): {task['title']}")
                # Check dependencies
                deps = task.get("depends_on", [])
                if isinstance(deps, str):
                    deps = [deps]
                unmet = [d for d in deps if _find_task(data, d) and _find_task(data, d)[0].get("status") != "done"]
                if unmet:
                    previews.append(f"  ⚠ Unmet dependencies: {', '.join(f'`{d}`' for d in unmet)}")
            elif current_status == "in-progress":
                previews.append(f"- `{task_id}`: Already in-progress (idempotent)")
            else:
                previews.append(f"- `{task_id}`: Cannot pick — currently `{current_status}`")

        elif op == "status":
            if len(parts) < 3:
                previews.append(f"- `{task_id}`: Missing target status for `status` operation")
                continue
            new_status = parts[2]
            refusal = illegal_transition_message(task, new_status)
            if new_status not in VALID_STATUSES:
                previews.append(f"- `{task_id}`: Invalid status `{new_status}`")
            elif refusal:
                # A preview that promises a move the write refuses is worse than
                # no preview: it sends the operator to a batch that half-applies.
                previews.append(f"- `{task_id}`: {refusal}")
            else:
                previews.append(f"- `{task_id}` ({current_status} → {new_status}): {task['title']}")

        else:
            previews.append(f"- Unknown operation `{op}` for `{task_id}`")

    header = f"**Dry-run preview** ({len(previews)} operations):\n"
    footer = "\n\n*No changes written. Use the actual tools to apply.*"
    return header + "\n".join(previews) + footer


# ── Blast Radius Analysis ───────────────────────────────────


@mcp.tool()
def backlog_blast_radius(task_id: str, mode: str = "predictive", depth_override: str = "", structured: bool = False) -> str:
    """Analyze the blast radius (impact footprint) of a task.

    Two modes:
    - predictive: metadata-only analysis for pick-task time. Fast, no code tracing.
    - evidence: code-level impact analysis for review-gate time. Traces imports, builds dependency graph.

    Args:
        task_id: The task ID to analyze (e.g., "auth-005")
        mode: Analysis mode — "predictive" or "evidence"
        depth_override: Optional depth override — "shallow" (0-1 hop) or "deep" (2 hops). Overrides the adaptive heuristic.
        structured: When True and mode is "predictive", return the raw analysis as a JSON string instead of formatted markdown. Used for detection-fallback pipelines.
    """
    if mode not in ("predictive", "evidence"):
        return f"Error: mode must be 'predictive' or 'evidence', got '{mode}'"

    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"
    task, epic = result

    # Collect all tasks for overlap detection
    all_tasks: list[dict] = []
    for e in data.get("epics", []):
        all_tasks.extend(e.get("tasks", []))

    config = load_config(data.get("meta", {}))
    override = depth_override if depth_override in ("shallow", "deep") else None

    if mode == "predictive":
        analysis = analyze_predictive(task, all_tasks)
        if structured:
            return json.dumps(analysis)
        return _format_predictive(analysis, task.get("priority", "medium"))
    else:
        # Resolve project root for code analysis
        sub_repo = task.get("sub_repo")
        worktree = task.get("worktree")
        if worktree:
            project_root = Path(worktree).resolve() if Path(worktree).is_absolute() else (ROOT / worktree).resolve()
        else:
            project_root = ROOT

        analysis = analyze_evidence(
            task=task,
            all_tasks=all_tasks,
            project_root=project_root,
            config=config,
            base_branch="main",
            depth_override=override,
        )
        return _format_evidence(analysis)


def _format_predictive(analysis: dict, priority: str) -> str:
    """Format predictive analysis results as markdown."""
    lines: list[str] = []

    anchored = analysis["anchored_areas"]
    overlaps = analysis["overlapping_tasks"]

    if priority in ("critical", "high"):
        lines.append("── Predicted Blast Radius ──────────────────────")
        if anchored:
            lines.append("**Anchored areas:**")
            for a in anchored:
                lines.append(f"  - `{a}`")
        else:
            lines.append("**Anchored areas:** None set")

        if overlaps:
            lines.append("")
            lines.append("**Related active work:**")
            for o in overlaps:
                paths = ", ".join(f"`{p}`" for p in o["shared_paths"][:3])
                lines.append(f"  - `{o['task_id']}` \"{o['title']}\" ({o['status']}) — shares {paths}")
        else:
            lines.append("")
            lines.append("**Related active work:** None detected")

        lines.append("────────────────────────────────────────────────")
    else:
        if overlaps:
            overlap_strs = [f"{o['task_id']} ({o['status']})" for o in overlaps[:3]]
            lines.append(f"Blast radius: Overlaps with {', '.join(overlap_strs)}")
        else:
            lines.append("Blast radius: No overlap with active tasks.")

    return "\n".join(lines)


def _format_evidence(analysis: dict) -> str:
    """Format evidence analysis results as markdown."""
    lines: list[str] = []
    stats = analysis["summary_stats"]

    # Summary line for gate table
    parts = []
    if stats["files_changed"]:
        parts.append(f"{stats['total_dependents']} dependents")
    if stats["overlap_count"]:
        parts.append(f"{stats['overlap_count']} overlapping task{'s' if stats['overlap_count'] != 1 else ''}")
    summary = ", ".join(parts) if parts else "no impact detected"
    lines.append(f"**Gate 4 summary:** {summary}")

    if not analysis["changed_files"]:
        lines.append("")
        lines.append("No changed files detected — nothing to analyze.")
        return "\n".join(lines)

    # Detailed report
    lines.append("")
    lines.append("── Blast Radius Report ─────────────────────────")

    # Changed files with fan-out
    lines.append(f"**Changed files ({stats['files_changed']}):**")
    for f in analysis["changed_files"]:
        fan = analysis["fan_out_scores"].get(f, 0)
        depth = analysis["depth_used"].get(f, 0)
        depth_label = {0: "leaf, no trace", 1: "1 hop", 2: "2 hops"}.get(depth, f"{depth} hops")
        lines.append(f"  `{f}` — {fan} dependents ({depth_label})")

    # Dependency graph (affected modules)
    if analysis["dependency_graph"]:
        lines.append("")
        lines.append("**Affected modules:**")
        dir_counts: dict[str, int] = {}
        for deps in analysis["dependency_graph"].values():
            for dep in deps:
                parent = str(Path(dep).parent)
                dir_counts[parent] = dir_counts.get(parent, 0) + 1
        for d, count in sorted(dir_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  - `{d}/` ({count} file{'s' if count != 1 else ''})")

    # Overlapping tasks
    overlaps = analysis["overlapping_tasks"]
    if overlaps:
        lines.append("")
        lines.append("**Overlapping tasks:**")
        for o in overlaps:
            is_in_progress = o["status"] == "in-progress"
            marker = "!!" if is_in_progress else "-"
            lines.append(f"  {marker} `{o['task_id']}` \"{o['title']}\" ({o['status']})")
            paths = ", ".join(f"`{p}`" for p in o["shared_paths"][:5])
            lines.append(f"    Shared paths: {paths}")
            if is_in_progress:
                lines.append(f"    **Risk: Both tasks modifying the same files concurrently**")

    if analysis["truncated"]:
        lines.append("")
        lines.append("*Note: File scan was truncated — results may be incomplete.*")

    lines.append("────────────────────────────────────────────────")

    return "\n".join(lines)


def _compute_recent_events(since_iso: str) -> list:
    """Synthesize a 'since you last looked' event stream from the backlog.

    Plan 4 stub: derive events from backlog state. Plan 5+ may swap in a
    persisted event log.
    Event shape: {kind, at, summary, ref?}
    Kinds: task_closed, task_moved, issue_opened, phase_advanced.
    """
    from datetime import datetime
    try:
        since = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
    except Exception as e:
        raise ValueError(f"invalid since: {e}")

    backlog = _load()
    if not isinstance(backlog.get("tasks"), list):
        backlog = dict(backlog)
        backlog["tasks"] = [
            {**t, "epic": t.get("epic", e.get("id"))}
            for e in (backlog.get("epics") or [])
            for t in (e.get("tasks") or [])
        ]
    events: list = []

    def _parse(s):
        if not s:
            return None
        try:
            return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        except Exception:
            return None

    for t in (backlog.get("tasks") or []):
        completed = _parse(t.get("completed"))
        if completed and completed >= since and t.get("status") in ("done", "completed"):
            events.append({
                "kind": "task_closed",
                "at": t["completed"],
                "summary": f"{t.get('id','')}: {t.get('title','')}",
                "ref": t.get("id"),
            })
        started = _parse(t.get("started"))
        if started and started >= since and t.get("status") in ("in-progress", "in_progress"):
            events.append({
                "kind": "task_moved",
                "at": t["started"],
                "summary": f"{t.get('id','')} → in progress",
                "ref": t.get("id"),
            })

    for ph in (backlog.get("phases") or []):
        advanced = _parse(ph.get("advanced_at") or ph.get("started"))
        if advanced and advanced >= since and ph.get("status") == "active":
            events.append({
                "kind": "phase_advanced",
                "at": ph.get("advanced_at") or ph.get("started"),
                "summary": f"phase {ph.get('id','')}: {ph.get('name','')}",
                "ref": ph.get("id"),
            })

    # Sort newest first, drop None ats.
    events = [e for e in events if e.get("at")]
    events.sort(key=lambda e: e["at"], reverse=True)
    return events


# ── Viewer write path ──────────────────────────────────────────────────────
# Every viewer mutation runs through exactly the same `_transaction` boundary
# as an MCP tool, and the ETag is the store's committed identity rather than a
# file mtime, so a viewer PATCH can no longer race an MCP write.


class ViewerWriteRejected(ValueError):
    """A viewer write the store refuses; carries a field-keyed error map."""

    def __init__(self, errors: dict):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = dict(errors)


class ViewerCompletionBlocked(Exception):
    """A viewer write would complete a task whose blocking gates are outstanding.

    Separate from `ViewerWriteRejected` because it is not a malformed patch: the
    field values are fine and the state of the world is what refuses the move,
    which is a 409, not a 422.
    """


class ViewerPreconditionFailed(Exception):
    """`If-Match` did not match committed state; carries the current revision."""

    def __init__(self, current_etag: str):
        super().__init__("stale")
        self.current_etag = current_etag


def _check_if_match(if_match: str | None) -> None:
    """Fail the write when `If-Match` no longer names committed state.

    Called *inside* the write transaction, after the store has imported and
    taken the writer lock: comparing before the transaction opened left a window
    in which a peer could commit and this request would then overwrite it and
    still answer 200.
    """
    if not if_match:
        return
    current = _viewer_etag()
    if if_match.strip('"') != current:
        raise ViewerPreconditionFailed(current)


def illegal_transition_message(task: dict | None, after: str | None) -> str | None:
    """Why moving `task` to `after` is refused, or None when it is allowed.

    One rule, applied by `backlog_update_task`, by batch and by the viewer, so
    the board cannot make a move the tool refuses. The rule is the one
    `backlog_update_task` has always applied:

    - a same-status write is never a transition, and is always allowed;
    - a task with a lane is held to the full `LEGAL_STATUS_TRANSITIONS` table;
    - a laneless (pre-lane, grandfathered) task keeps the permissive behaviour,
      except that it may still only leave `archived` the way the table says.

    The `archived` row is unconditional because `_apply_archive_transition`
    un-archives on any `archived -> other`: without it a viewer
    `PATCH {"status": "done"}` silently resurrects an archived task.
    """
    before = (task or {}).get("status", "todo")
    if after is None or after == before:
        return None
    if before != "archived" and not (task or {}).get("lane"):
        return None
    legal = LEGAL_STATUS_TRANSITIONS.get(before, set())
    if after in legal:
        return None
    return (
        f"illegal transition `{before}` → `{after}`. "
        f"Legal: {', '.join(sorted(legal)) or '(none)'}"
    )


def _invalid_status_error(patch: dict) -> dict:
    """`{"status": …}` when the write supplies a status that is not one.

    `illegal_transition_message` reads `after is None` as "no status in this
    write" and allows it, so `{"status": null}` skipped the transition table
    entirely and `task.update(patch)` persisted a null the MCP tools reject and
    the board cannot place in any column. An *absent* `status` key is still a
    write that names no status and is untouched here; an explicitly supplied
    one has to be a real status.
    """
    if "status" not in patch:
        return {}
    supplied = patch["status"]
    if supplied in VALID_STATUSES:
        return {}
    shown = "null" if supplied is None else repr(supplied)
    return {
        "status": (
            f"invalid status {shown}. "
            f"Valid: {', '.join(sorted(VALID_STATUSES))}"
        )
    }


def _archived_transition_error(task: dict | None, patch: dict) -> dict:
    """`illegal_transition_message` in the `{field: error}` shape the viewer
    write path collects errors in."""
    message = illegal_transition_message(task, patch.get("status"))
    return {"status": message} if message else {}


def _viewer_etag() -> str:
    """`<creation_token>:<max_seq>` — the store's committed identity.

    Taken from `load_dict_with_identity`, the same call every GET's payload
    comes from, rather than from `status()`. On a network filesystem the store
    runs projection-only and `status()` degrades to an empty token and seq 0, so
    every revision of such a project answered the constant `":0"` — an ETag that
    never moves is worse than none, because a client caches the first payload
    forever and an `If-Match` write always passes.
    """
    bp = _backlog_path()
    if not bp.exists():
        return ""
    try:
        _data, token, max_seq = _store().load_dict_with_identity()
    except store.LegacyLayoutError:
        raise
    except Exception:  # an unreadable store must not break a read-only GET
        return ""
    return f"{token}:{max_seq}"


def _viewer_update_task(
    task_id: str, patch: dict, *, method: str = "PATCH", if_match: str | None = None
) -> dict:
    """Apply a partial update to a task inside one store transaction.

    Mirrors the timestamp stamping the old `taskmaster_v3.update_task` did:
    `started` on the first move off `todo`, `completed` on `done`, neither ever
    overwritten, and `human_action` cleared on `done`.
    """
    from taskmaster.taskmaster_v3 import _now_iso  # noqa: PLC0415
    from taskmaster.taskmaster_v3 import validate_task_write  # noqa: PLC0415

    with _transaction(tool=f"viewer:{method} /api/tasks") as data:
        _check_if_match(if_match)
        found = _find_task(data, task_id)
        if found is None:
            raise KeyError(f"task {task_id} not found")
        task, _epic = found
        errors = validate_task_write(task_id, patch, _backlog_path(), data=data)
        if "_task" in errors:
            raise KeyError(errors["_task"])
        errors.update(_invalid_status_error(patch))
        errors.update(_archived_transition_error(task, patch))
        if errors:
            raise ViewerWriteRejected(errors)
        # The board is not a way around the gates. `backlog_update_task` and
        # `backlog_complete_task` both refuse `-> done` while a lane'd task has
        # outstanding blocking reviews; without this the same move landed by
        # drag-and-drop and the gates were simply skipped.
        if patch.get("status") == "done" and task.get("status") != "done":
            block = _completion_block_reason(task)
            if block:
                raise ViewerCompletionBlocked(block)
        before_status = task.get("status")
        before_epic = task.get("epic") or _epic.get("id")
        task.update(patch)
        after_status = task.get("status")
        moved_to = patch.get("epic")
        if moved_to and moved_to != before_epic:
            # The task has to leave one epic's list and join the other's; leaving
            # it in place with a rewritten `epic` field produced a row whose
            # parent and field disagreed.
            target = next(
                (e for e in data.get("epics") or [] if e.get("id") == moved_to), None
            )
            if target is None:
                raise ViewerWriteRejected({"epic": f"unknown epic: {moved_to}"})
            _epic["tasks"] = [t for t in _epic.get("tasks") or [] if t is not task]
            target.setdefault("tasks", []).append(task)
        if after_status != before_status:
            if after_status == "in-progress" and not task.get("started"):
                task["started"] = _now_iso()
            if after_status == "done" and not task.get("completed"):
                task["completed"] = _now_iso()
        if after_status == "done":
            task.pop("human_action", None)
        task["last_referenced"] = _now_iso()
        _apply_archive_transition(
            "task", task_id, task, before=before_status, after=after_status
        )
        result = deepcopy(task)
        _mutate_and_save(data)
    return result


def _viewer_create_task(payload: dict) -> str:
    """Create a task under an existing epic. Returns the assigned id."""
    from taskmaster.taskmaster_v3 import _now_iso  # noqa: PLC0415
    from taskmaster.taskmaster_v3 import validate_task_write  # noqa: PLC0415

    epic_id = payload.get("epic")
    if not epic_id:
        raise ValueError("epic is required")
    new_id = ""
    with _transaction(tool="viewer:POST /api/tasks") as data:
        errors = validate_task_write("<new>", payload, _backlog_path(), data=data)
        errors.update(_invalid_status_error(payload))
        if errors:
            raise ViewerWriteRejected(errors)
        epic = next(
            (e for e in data.get("epics") or [] if e.get("id") == epic_id), None
        )
        if epic is None:
            raise KeyError(f"epic {epic_id} not found")
        # Same authority as backlog_add_task: the store's tombstone-aware
        # allocator, never a scan of this checkout's task list alone.
        new_id = _store_tx().allocate_id("task", {"epic": epic_id})
        task = {
            "id": new_id,
            "title": payload.get("title", ""),
            "status": payload.get("status", "todo"),
            "priority": payload.get("priority", "medium"),
            "created": _now_iso(),
            "last_referenced": _now_iso(),
        }
        for key, value in payload.items():
            if key not in ("epic", "id"):
                task[key] = value
        epic.setdefault("tasks", []).append(task)
        _mutate_and_save(data)
    return new_id


def _viewer_archive_task(task_id: str, *, if_match: str | None = None) -> None:
    """Soft-delete a task: status flip plus the explicit store archive."""
    with _transaction(tool="viewer:POST /api/tasks/archive") as data:
        _check_if_match(if_match)
        found = _find_task(data, task_id)
        if found is None:
            raise KeyError(f"task {task_id} not found")
        task, _epic = found
        before = task.get("status")
        task["status"] = "archived"
        _apply_archive_transition(
            "task", task_id, task, before=before, after="archived"
        )
        _mutate_and_save(data)


def _load_task_full(task_id: str) -> dict | None:
    """The task detail the viewer renders, derived only from committed store state.

    It used to overlay the store document with the contents of `tasks/<id>.md`.
    That file is a projection: it lags whenever an export is dirty, and in a
    linked worktree it is a different, older file entirely, so the viewer showed
    stale prose under a current ETag and the next edit wrote that prose back.
    The stored body is the same text without the lag.
    Returns None if the task id is unknown.
    """
    return _load_task_full_identified(task_id)[0]


def _load_task_full_identified(task_id: str) -> tuple[dict | None, str]:
    """`_load_task_full` plus the ETag of the one snapshot it was read from."""
    import re

    backlog_path = _backlog_path()
    if not backlog_path.exists():
        return None, ""
    # Route through the store: the v4 projection keeps no task index in
    # backlog.yaml, and a call nested inside an open transaction must see the
    # in-flight tree rather than the last exported file.
    backlog, etag = _load_snapshot()
    tasks = backlog.get("tasks")
    if not isinstance(tasks, list):
        tasks = [
            {**t, "epic": t.get("epic", e.get("id"))}
            for e in (backlog.get("epics") or [])
            for t in (e.get("tasks") or [])
        ]
    index_entry = next((t for t in tasks if t.get("id") == task_id), None)
    if index_entry is None:
        return None, etag
    store_owned = any(
        task.get("id") == task_id
        for epic in (backlog.get("epics") or [])
        for task in (epic.get("tasks") or [])
    )

    out = dict(index_entry)
    out.setdefault("docs", {})
    out.setdefault("description", "")
    out.setdefault("notes", "")
    out.setdefault("review_instructions", "")
    if store_owned:
        body = out.pop(_BODY_KEY, "") or ""
    else:
        # A v3 backlog keeps a slim inline task index and leaves the heavy
        # fields in `tasks/<id>.md`, which the store does not own as a row.
        # There is no committed document to prefer, so the file is still the
        # only source for them.
        body = ""
        legacy_path = backlog_path.parent / "tasks" / f"{task_id}.md"
        if legacy_path.exists():
            raw = legacy_path.read_text(encoding="utf-8")
            match = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.DOTALL)
            if match:
                try:
                    frontmatter = yaml_io.safe_load(match.group(1)) or {}
                except Exception:
                    frontmatter = {}
                body = match.group(2)
                for key in (*_HEAVY_FIELDS, "patchnote", "release",
                            "worktree", "spec_review", "locked_by"):
                    if key in frontmatter:
                        out[key] = frontmatter[key]
            else:
                body = raw
    out["_body"] = body

    # A legacy body still carries `## Description` / `## Notes` sections that v4
    # keeps as fields; when it does, the body wins, exactly as it did before.
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        m = re.match(r"^## +(.+?)\s*$", line)
        if m:
            current = m.group(1).strip().lower()
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    for key in ("description", "notes", "specification", "plan",
                "review instructions", "activity", "patchnote"):
        if key in sections:
            out_key = key.replace(" ", "_")
            out[out_key] = "\n".join(sections[key]).strip()
    return out, etag



def _load_epic_full(epic_id: str) -> dict | None:
    """Epic with heavy fields (description/docs/components) merged from
    epics/<id>.md via load_v3, plus derived status counts, per-component
    rollup, a blocked/blockers attention list, and a slim task list.

    Returns None if the epic id is unknown. Mirrors _load_task_full but
    routes through the store snapshot (committed state) so heavy fields that
    /api/backlog strips are present here.
    """
    return _load_epic_full_identified(epic_id)[0]


def _load_epic_full_identified(epic_id: str) -> tuple[dict | None, str]:
    """`_load_epic_full` plus the ETag of the one snapshot it was read from.

    The route used to take the payload from `_load()` and the revision from a
    second `_viewer_etag()` call. A commit landing between the two handed the
    viewer an old epic under a newer revision, and the edit that followed passed
    its own precondition while overwriting the newer state.
    """
    if not _backlog_path().exists():
        return None, ""
    data, etag = _load_snapshot()
    epic = _find_epic(data, epic_id)
    if epic is None:
        return None, etag

    out = {k: v for k, v in epic.items() if k != "tasks"}
    out.setdefault("description", "")
    out.setdefault("docs", {})
    out.setdefault("components", {})
    out.setdefault("design_status", "exploring")
    out.setdefault("done_when", "")
    out.setdefault("area", None)
    out["stats"] = _epic_stats(data, epic_id)
    out["closeable"] = out["stats"]["closeable"]
    out["component_rollup"] = _component_rollup(data, epic_id)

    attention = []
    for t in epic.get("tasks", []):
        if t.get("status") == "blocked":
            attention.append({"id": t.get("id"), "title": t.get("title"),
                              "blocked": True, "why": t.get("blockers", "")})
        elif t.get("blockers"):
            attention.append({"id": t.get("id"), "title": t.get("title"),
                              "blocked": False, "why": t.get("blockers")})
    out["attention"] = attention

    out["tasks"] = [
        {"id": t.get("id"), "title": t.get("title"),
         "status": t.get("status", "todo"), "component": t.get("component"),
         "priority": t.get("priority"), "phase": t.get("phase"),
         "design_change": t.get("design_change")}
        for t in epic.get("tasks", [])
    ]
    return out, etag


def _load_related_for_task(task_id: str) -> dict | None:
    """Build the related-entities payload for a task: handovers (task_ids),
    issues (task_ids), forward deps, reverse deps.
    Returns None if the task is unknown.
    """
    import re

    backlog_path = _backlog_path()
    if not backlog_path.exists():
        return None
    # Route through _load() so a call nested inside an open transaction sees the
    # in-flight tree the mutation is building, not the last exported file.
    backlog = _load()
    tasks = backlog.get("tasks")
    if not isinstance(tasks, list):
        tasks = [
            {**t, "epic": t.get("epic", e.get("id"))}
            for e in (backlog.get("epics") or [])
            for t in (e.get("tasks") or [])
        ]
    me = next((t for t in tasks if t.get("id") == task_id), None)
    if me is None:
        return None

    def _read_fm(p: Path) -> tuple[dict, str]:
        raw = p.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.DOTALL)
        if not m:
            return {}, raw
        try:
            fm = yaml_io.safe_load(m.group(1)) or {}
        except Exception:
            fm = {}
        return fm, m.group(2)

    sidecar_root = backlog_path.parent

    handovers: list[dict] = []
    handovers_dir = sidecar_root / "handovers"
    if handovers_dir.is_dir():
        for f in sorted(handovers_dir.glob("*.md")):
            fm, body = _read_fm(f)
            tids = list(fm.get("task_ids") or [])
            if task_id in tids:
                handovers.append({
                    "id": fm.get("id") or f.stem,
                    "kind": fm.get("kind"),
                    "session": fm.get("session"),
                    "created": fm.get("created"),
                    "status": fm.get("status", "todo"),
                    "quote": body.strip().splitlines()[0] if body.strip() else "",
                    "_path": str(f),
                })

    issues: list[dict] = []
    issues_dir = sidecar_root / "issues"
    if issues_dir.is_dir():
        for f in sorted(issues_dir.glob("*.md")):
            fm, body = _read_fm(f)
            tids = list(fm.get("task_ids") or [])
            if task_id in tids:
                issues.append({
                    "id": fm.get("id") or f.stem,
                    "severity": fm.get("severity"),
                    "status": fm.get("status"),
                    "title": fm.get("title") or "",
                    "_path": str(f),
                })

    dep_ids = list(me.get("depends_on") or [])
    dependencies = [
        {"id": t["id"], "title": t.get("title", ""), "status": t.get("status", "")}
        for t in tasks if t.get("id") in dep_ids
    ]
    unblocks = [
        {"id": t["id"], "title": t.get("title", ""), "status": t.get("status", "")}
        for t in tasks if task_id in (t.get("depends_on") or [])
    ]

    return {
        "task_id": task_id,
        "handovers": handovers,
        "issues": issues,
        "dependencies": dependencies,
        "unblocks": unblocks,
    }

# ── HTTP Viewer Server ───────────────────────────────────


class ViewerHandler(BaseHTTPRequestHandler):
    """Serves the backlog viewer HTML and YAML data."""

    def handle_one_request(self) -> None:
        """Answer a refused legacy layout with a body, never a bare traceback."""
        # One request must never inherit the sequence or the export notices of
        # the previous one on this thread; only a commit made while serving it
        # may set them.
        _TX_STATE.last_seq = None
        _TX_STATE.export_warnings = []
        try:
            super().handle_one_request()
        except store.LegacyLayoutError as exc:
            try:
                self._send_json(409, {"ok": False, "error": str(exc)})
            except Exception:
                pass

    def do_GET(self) -> None:
        import re
        from urllib.parse import unquote, urlparse
        parsed = urlparse(self.path)
        clean_path = unquote(parsed.path)

        if clean_path in ("/", "/index.html", "/v3", "/v3/", "/v3/index.html"):
            viewer_root = SCRIPT_DIR / "viewer"
            idx = viewer_root / "index.html"
            if not idx.exists():
                self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
            html = idx.read_text(encoding="utf-8")
            # Make relative asset refs absolute under /static/v3/.
            html = html.replace('href="css/', 'href="/static/v3/css/')
            html = html.replace('src="js/', 'src="/static/v3/js/')
            html = html.replace('src="vendor/', 'src="/static/v3/vendor/')
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return
        elif clean_path.startswith("/static/v3/"):
            from urllib.parse import unquote as _unquote
            rel = _unquote(clean_path[len("/static/v3/"):])
            viewer_root = (SCRIPT_DIR / "viewer").resolve()
            target = (viewer_root / rel).resolve()
            if not str(target).startswith(str(viewer_root) + os.sep) and target != viewer_root:
                self.send_response(400); self.send_header("Content-Length", "0"); self.end_headers(); return
            if not target.is_file():
                self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
            ext = target.suffix.lower()
            ctype = {
                ".html":  "text/html; charset=utf-8",
                ".css":   "text/css; charset=utf-8",
                ".js":    "application/javascript; charset=utf-8",
                ".json":  "application/json; charset=utf-8",
                ".svg":   "image/svg+xml",
                ".woff2": "font/woff2",
                ".woff":  "font/woff",
                ".png":   "image/png",
                ".ico":   "image/x-icon",
            }.get(ext, "application/octet-stream")
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return
        elif clean_path == "/v3/dev/edit-demo" or clean_path == "/v3/dev/edit-demo/":
            viewer_root = SCRIPT_DIR / "viewer"
            self._serve_file(viewer_root / "dev" / "edit-demo.html", "text/html")
        elif clean_path == "/api/viewer/prefs":
            self._send_json(200, load_viewer_prefs(_backlog_path()))
            return
        elif clean_path == "/backlog.yaml":
            self._serve_file(_backlog_path(), "text/yaml")
        elif clean_path.startswith("/api/task/"):
            rest = clean_path[len("/api/task/"):].rstrip("/")
            if rest.endswith("/related"):
                task_id = rest[: -len("/related")]
                related = _load_related_for_task(task_id)
                if related is None:
                    self._send_json(404, {"ok": False, "error": f"task {task_id} not found"})
                    return
                self._send_json(200, related)
                return
            if "/" not in rest and rest:
                full, etag = _load_task_full_identified(rest)
                if full is None:
                    self._send_json(404, {"ok": False, "error": f"task {rest} not found"})
                    return
                self._send_json(200, full, etag=etag)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        elif clean_path.startswith("/api/epic/"):
            eid = clean_path[len("/api/epic/"):].rstrip("/")
            if eid and "/" not in eid:
                full, etag = _load_epic_full_identified(eid)
                if full is None:
                    self._send_json(404, {"ok": False, "error": f"epic {eid} not found"})
                    return
                self._send_json(200, full, etag=etag)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        elif clean_path == "/api/backlog":
            self._serve_json()
        elif clean_path == "/api/session":
            self._serve_session()
        elif clean_path == "/api/identity":
            self._serve_identity()
        elif clean_path.startswith("/file/"):
            self._serve_repo_file(clean_path)
        elif self.path.startswith("/api/dashboard/recent-events"):
            import urllib.parse
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            since = (qs.get("since") or [None])[0]
            if not since:
                self._send_json(400, {"ok": False, "error": "missing 'since' query param"})
                return
            try:
                events = _compute_recent_events(since)
            except ValueError as e:
                self._send_json(400, {"ok": False, "error": str(e)})
                return
            self._send_json(200, events)
            return
        elif clean_path == "/api/threads":
            from taskmaster.taskmaster_v3 import list_threads as _list_threads_http
            self._send_json(200, _list_threads_http(_threads_data(_backlog_path())))
            return
        elif clean_path == "/api/sessions":
            snapshot = self._snapshot()
            if snapshot is None:
                self._send_json(200, [])
                return
            data, etag = snapshot
            self._send_json(200, list_sessions(_dict_rows(data, "handover")), etag=etag)
            return
        elif clean_path.startswith("/api/sessions/"):
            sid = clean_path[len("/api/sessions/"):]
            snapshot = self._snapshot()
            detail = (
                None if snapshot is None
                else get_session_detail(sid, _dict_rows(snapshot[0], "handover"))
            )
            if detail is None:
                self._send_json(404, {"ok": False, "error": f"unknown session {sid}"})
                return
            self._send_json(200, detail, etag=snapshot[1])
            return
        elif clean_path.startswith("/api/bugs"):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            include_archive = qs.get("include_archive", ["false"])[0].strip().lower() in ("1", "true", "yes", "on")
            status_filter = qs.get("status", [""])[0]
            found_in_filter = qs.get("found_in", [""])[0]
            snapshot = self._snapshot()
            if snapshot is None:
                self._send_json(200, [])
                return
            data, etag = snapshot
            bugs = []
            for _bid, fm, body in _dict_rows(data, "bug", include_archived=include_archive):
                summary = {k: v for k, v in fm.items() if k != "_body"}
                summary["summary"] = (body or "").strip()
                bugs.append(summary)
            if status_filter:
                bugs = [b for b in bugs if b.get("status") == status_filter]
            if found_in_filter:
                bugs = [b for b in bugs if b.get("found_in") == found_in_filter]
            self._send_json(200, bugs, etag=etag)
            return
        elif clean_path.startswith("/api/issues"):
            from urllib.parse import urlparse, parse_qs
            from taskmaster.taskmaster_v3 import compute_issue_aging, severity_label
            qs = parse_qs(urlparse(self.path).query)
            include_resolved = qs.get("include_resolved", ["true"])[0].lower() != "false"
            snapshot = self._snapshot()
            if snapshot is None:
                self._send_json(200, {"issues": []})
                return
            data, etag = snapshot
            prefs = load_viewer_prefs(_backlog_path())
            aging_cfg = prefs.get("issues", {}).get("aging", {})
            issues = []
            for _iid, fm, body in _dict_rows(data, "issue"):
                if not include_resolved and fm.get("status") in ("fixed", "wontfix"):
                    continue
                try:
                    summary = {k: v for k, v in fm.items() if k != "_body"}
                    summary["severity_label"] = severity_label(summary.get("severity", "P2"))
                    summary["aging"] = compute_issue_aging(dict(fm), aging_cfg)
                    summary["summary"] = (body or "").strip()
                    issues.append(summary)
                except Exception:
                    # One bad issue must not blank the whole screen. ISS-005.
                    continue
            self._send_json(200, {"issues": issues}, etag=etag)
            return
        elif clean_path.startswith("/api/ideas"):
            from urllib.parse import urlparse, parse_qs
            snapshot = self._snapshot()
            if snapshot is None:
                self._send_json(200, {"ideas": []})
                return
            data, etag = snapshot
            qs = parse_qs(urlparse(self.path).query)
            archived = qs.get("archived", ["false"])[0].lower() == "true"
            status = qs.get("status", [""])[0] or None
            tag = qs.get("tag", [""])[0] or None
            related_task = qs.get("related_task", [""])[0] or None
            # Default summary=False on HTTP so the viewer can render detail
            # without a second fetch. MCP callers via backlog_idea_list still
            # default to summary=True (they don't need every body in the list).
            summary = qs.get("summary", ["false"])[0].lower() == "true"
            try:
                limit = int(qs.get("limit", ["100"])[0])
            except (TypeError, ValueError):
                limit = 100
            entries = _idea_records(
                data,
                status=status,
                tag=tag,
                archived=archived,
                related_task=related_task,
                summary=summary,
            )[: max(1, limit)]
            self._send_json(200, {"ideas": entries}, etag=etag)
            return
        elif clean_path == "/api/continuity":
            import json
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            include_auto = qs.get("include_auto_stage", ["0"])[0] in ("1", "true")
            payload = json.loads(backlog_continuity_items(include_auto_stage=include_auto))
            self._send_json(200, payload)
            return
        elif m := re.fullmatch(r"/api/decisions/([A-Za-z0-9_\-]+)", clean_path):
            decision_id = m.group(1)
            snapshot = self._snapshot()
            row = None if snapshot is None else _dict_row(snapshot[0], "decision", decision_id)
            if row is None:
                self._send_json(404, {"ok": False, "error": f"decision {decision_id} not found"})
                return
            fm, body = row[0], row[1] or ""
            self._send_json(200, {**fm, "body": body}, etag=snapshot[1])
            return
        elif m := re.fullmatch(r"/api/handover/([A-Za-z0-9_\-]+)", clean_path):
            handover_id = m.group(1)
            snapshot = self._snapshot()
            row = None if snapshot is None else _dict_row(snapshot[0], "handover", handover_id)
            if row is None:
                self._send_json(404, {"ok": False, "error": f"handover {handover_id} not found"})
                return
            fm, body = row[0], row[1] or ""
            self._send_json(200, {**fm, "body": body}, etag=snapshot[1])
            return
        elif clean_path == "/api/notes":
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            include_archived = qs.get("include_archived", ["0"])[0] in ("1", "true")
            snapshot = self._snapshot()
            if snapshot is None:
                self._send_json(200, {"notes": []})
                return
            data, etag = snapshot
            notes = _note_records(data, include_archived=include_archived)
            self._send_json(200, {"notes": notes}, etag=etag)
            return
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _snapshot(self):
        """`(data, etag)` for one GET, or None when the project has no backlog.

        Every list endpoint reads through this, so a screen built from several
        GETs is built from one committed revision: taking the payload from one
        read and the revision from another let a client cache an old list under
        a newer ETag and then pass its own `If-Match` while overwriting a peer.
        """
        try:
            return _load_snapshot()
        except FileNotFoundError:
            return None

    def _serve_file(self, path: Path, content_type: str) -> None:
        try:
            data = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _serve_session(self) -> None:
        session_data = dict(_session_task) if _session_task else {}
        session_data["session_id"] = SESSION_ID
        self._send_json(200, session_data)

    def _serve_identity(self) -> None:
        """Return the project root and version so callers can verify which project this server serves."""
        self._send_json(200, {"root": str(ROOT.resolve()), "version": VERSION})

    def _serve_repo_file(self, clean_path: str) -> None:
        """Serve a file from the repo root. Renders .md files as styled HTML."""
        rel_path = clean_path[len("/file/"):]  # already unquoted
        # Security: prevent path traversal
        try:
            resolved = (ROOT / rel_path).resolve()
            if not str(resolved).startswith(str(ROOT.resolve())):
                self.send_error(HTTPStatus.FORBIDDEN, "Path traversal blocked")
                return
        except (ValueError, OSError):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return

        if not resolved.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, f"File not found: {rel_path}")
            return

        try:
            content = resolved.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if resolved.suffix.lower() == ".md":
            # Render markdown in a styled HTML page
            from html import escape
            import base64
            b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
            full_path = str(resolved).replace("\\", "/")
            html = _MD_TEMPLATE.replace("{{TITLE}}", escape(rel_path)).replace("{{B64CONTENT}}", b64).replace("{{FULL_PATH}}", full_path)
            body = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        else:
            body = content.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            ext_map = {".yaml": "text/yaml", ".yml": "text/yaml", ".json": "application/json",
                       ".py": "text/plain", ".cpp": "text/plain", ".h": "text/plain",
                       ".cs": "text/plain", ".txt": "text/plain"}
            ct = ext_map.get(resolved.suffix.lower(), "text/plain")
            self.send_header("Content-Type", f"{ct}; charset=utf-8")

        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self) -> None:
        try:
            data, etag = _load_snapshot()
            data.setdefault("meta", {})["_version"] = VERSION
            if not isinstance(data.get("tasks"), list):
                data["tasks"] = [
                    {**t, "epic": t.get("epic", e.get("id"))}
                    for e in (data.get("epics") or [])
                    for t in (e.get("tasks") or [])
                ]
            # Sort phases by order so the viewer always receives them in logical
            # sequence, regardless of YAML insertion order. Phases added out of
            # order (e.g. inserting "1.5" after "2" was written) would otherwise
            # appear in the wrong position in the phase stepper / board grouping.
            if isinstance(data.get("phases"), list):
                data["phases"] = sorted(
                    data["phases"],
                    key=lambda p: (p.get("order") if p.get("order") is not None else 999),
                )
            self._send_json(200, data, etag=etag)
        except store.LegacyLayoutError as exc:
            # A layout the store refuses is a conflict the operator can fix, not
            # a server fault: 500 sent the viewer into its generic error state
            # and hid the one instruction that resolves it.
            self._send_json(409, {"ok": False, "error": str(exc)})
        except Exception as e:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    def do_POST(self):
        import json
        import re

        if self.path == "/api/ideas":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            title = (payload.get("title") or "").strip()
            if not title:
                self._send_json(400, {"ok": False, "error": "title is required"})
                return
            # One store root per process (spec decision 2): resolving the
            # artifact root separately here opened a second store, and the dict
            # `_idea_create_in_tx` loaded then belonged to neither transaction.
            bp = _backlog_path()
            if not bp.exists():
                self._send_json(400, {"ok": False, "error": f"no backlog at {bp}"})
                return
            with _transaction(tool="viewer:POST /api/ideas"):
                result = _idea_create_in_tx(
                    title=title,
                    body=payload.get("body", ""),
                    tags=payload.get("tags") or [],
                    status=payload.get("status", ""),
                    related_tasks=payload.get("related_tasks") or [],
                    related_issues=payload.get("related_issues") or [],
                    created_by=payload.get("created_by", "user"),
                )
            if isinstance(result, str):
                self._send_json(400, {"ok": False, "error": result})
                return
            iid, target = result
            self._send_json(201, {"ok": True, "id": iid, "path": str(target)})
            return

        if self.path == "/api/notes":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            text = (payload.get("text") or "").strip()
            if not text:
                self._send_json(400, {"ok": False, "error": "text is required"})
                return
            bp = _backlog_path()
            if not bp.exists():
                self._send_json(400, {"ok": False, "error": f"no backlog at {bp}"})
                return
            with _transaction(tool="viewer:POST /api/notes"):
                result = _note_create_in_tx(
                    text=text, author="user", pinned=bool(payload.get("pinned", False))
                )
            if isinstance(result, str):
                self._send_json(400, {"ok": False, "error": result})
                return
            self._send_json(201, {"ok": True, "id": result[0]})
            return

        m = re.fullmatch(r"/api/notes/([A-Za-z0-9_\-]+)/(update|archive)", self.path)
        if m:
            note_id, action = m.group(1), m.group(2)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            with _transaction(tool=f"viewer:POST /api/notes/{action}"):
                if action == "archive":
                    error = _note_archive_in_tx(note_id)
                else:
                    text = payload.get("text")
                    pinned = payload.get("pinned")
                    error = _note_update_in_tx(
                        note_id,
                        text=(text.strip() if isinstance(text, str) and text.strip() else None),
                        pinned=(bool(pinned) if pinned is not None else None),
                    )
            if error is not None:
                status = 404 if error.startswith("Note not found") else 400
                self._send_json(status, {"ok": False, "error": error})
                return
            self._send_json(200, {"ok": True, "id": note_id})
            return

        m = re.fullmatch(r"/api/handover/([A-Za-z0-9_\-\.]+)/status", self.path)
        if m:
            handover_id = m.group(1)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            status = payload.get("status", "")
            reason = payload.get("reason", "")
            with _transaction(tool="viewer:POST /api/handovers/status") as data:
                fm = _handover_set_status(handover_id, status=status, reason=reason)
                if not isinstance(fm, str):
                    _sync_handover_index_tx(data)
                    _mutate_and_save(data)
            if isinstance(fm, str):
                code = 404 if fm.startswith("Handover not found") else 400
                self._send_json(code, {"ok": False, "error": fm})
                return
            self._send_json(200, {"ok": True, "id": handover_id, "status": fm["status"]})
            return

        if self.path == "/api/tasks/validate":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw)
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            tid = payload.get("task_id") or "<new>"
            patch = payload.get("patch") or {}
            from taskmaster.taskmaster_v3 import validate_task_write
            # Preview and write must run the same gate against the same state:
            # the store, not the projection files.
            try:
                data = _load()
            except FileNotFoundError:
                data = None
            errors = validate_task_write(tid, patch, data=data)
            errors.update(_invalid_status_error(patch))
            if data is not None and tid != "<new>":
                found = _find_task(data, tid)
                if found is not None:
                    errors.update(_archived_transition_error(found[0], patch))
                    # Preview and write run the same gate: a validate that says
                    # "ok" for a move the write refuses is worse than no preview.
                    if patch.get("status") == "done" and found[0].get("status") != "done":
                        block = _completion_block_reason(found[0])
                        if block:
                            errors["status"] = block
            self._send_json(200, {"ok": len(errors) == 0, "errors": errors})
            return

        # Edit-in-UI: create task, archive task
        if self.path == "/api/tasks":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw)
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            try:
                new_id = _viewer_create_task(payload)
                # Look up the new task to return.
                task = _load_task_full(new_id) or {"id": new_id}
                self._send_json(201, {"ok": True, "task": task})
            except ViewerWriteRejected as e:
                self._send_json(422, {"ok": False, "errors": e.errors})
            except (KeyError, ValueError) as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            except store.LegacyLayoutError as exc:
                self._send_json(409, {"ok": False, "error": str(exc)})
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
            return

        m = re.fullmatch(r"/api/tasks/([A-Za-z0-9_\-]+)/archive", self.path)
        if m:
            task_id = m.group(1)
            try:
                _viewer_archive_task(task_id, if_match=self.headers.get("If-Match"))
                self._send_json(200, {"ok": True}, etag=_viewer_etag())
            except ViewerPreconditionFailed as e:
                self._send_stale(task_id, e.current_etag)
            except KeyError as e:
                self._send_json(404, {"ok": False, "error": str(e)})
            except store.LegacyLayoutError as exc:
                self._send_json(409, {"ok": False, "error": str(exc)})
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
            return

        m = re.fullmatch(r"/api/decisions/([A-Za-z0-9_\-]+)/resolve", self.path)
        if m:
            decision_id = m.group(1)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            resolved_with = payload.get("resolved_with")
            rationale = payload.get("rationale", "")
            if resolved_with is None:
                self._send_json(400, {"ok": False, "error": "resolved_with is required"})
                return
            with _transaction(tool="viewer:POST /api/decisions/resolve"):
                fm = _decision_resolve_in_tx(
                    decision_id, resolved_with=int(resolved_with), rationale=rationale
                )
            if isinstance(fm, str):
                code = 404 if "not found" in fm else 400
                self._send_json(code, {"ok": False, "error": fm})
                return
            self._send_json(200, {"ok": True, "id": decision_id, "status": fm.get("status")})
            return

        m = re.fullmatch(r"/api/decisions/([A-Za-z0-9_\-]+)/drop", self.path)
        if m:
            decision_id = m.group(1)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            reason = payload.get("reason", "")
            with _transaction(tool="viewer:POST /api/decisions/drop"):
                fm = _decision_drop_in_tx(decision_id, reason=reason)
            if isinstance(fm, str):
                code = 404 if "not found" in fm else 400
                self._send_json(code, {"ok": False, "error": fm})
                return
            self._send_json(200, {"ok": True, "id": decision_id, "status": fm.get("status")})
            return

        # ── Bug HTTP routes ───────────────────────────────────────────────────────
        clean_path_post = self.path.split("?")[0].rstrip("/")

        if clean_path_post == "/api/bugs":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            title = (payload.get("title") or "").strip()
            if not title:
                self._send_json(400, {"ok": False, "error": "title is required"})
                return
            bp = _backlog_path()
            if not bp.exists():
                self._send_json(400, {"ok": False, "error": f"no backlog at {bp}"})
                return
            with _transaction(tool="viewer:POST /api/bugs"):
                result = _bug_create_in_tx(
                    title=title,
                    found_in=payload.get("found_in") or None,
                    discovered_by=payload.get("discovered_by", "user"),
                    severity=payload.get("severity") or None,
                    components=payload.get("components") or [],
                    location=payload.get("location") or [],
                    body=payload.get("body", ""),
                )
            if isinstance(result, str):
                self._send_json(400, {"ok": False, "error": result})
                return
            bid, target = result
            self._send_json(201, {"ok": True, "id": bid, "path": str(target)})
            return

        m = re.fullmatch(r"/api/bugs/pattern-scan", clean_path_post)
        if m:
            from taskmaster.taskmaster_v3 import scan_bug_patterns as _scan_bug_patterns_http
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            mode = payload.get("mode", "all")
            include_archive = (mode != "end_of_task")
            snapshot = self._snapshot()
            if snapshot is None:
                self._send_json(200, {"groups": []})
                return
            data, etag = snapshot
            groups = _scan_bug_patterns_http(
                _dict_rows(data, "bug", include_archived=include_archive)
            )
            self._send_json(200, {"groups": groups}, etag=etag)
            return

        m = re.fullmatch(r"/api/bugs/promote", clean_path_post)
        if m:
            bp = _backlog_path()
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            bug_ids = payload.get("bug_ids") or []
            title = (payload.get("title") or "").strip()
            severity = (payload.get("severity") or "").strip()
            evidence_text = (payload.get("evidence_text") or "").strip()
            if not bug_ids:
                self._send_json(400, {"ok": False, "error": "bug_ids is required"})
                return
            if not title:
                self._send_json(400, {"ok": False, "error": "title is required"})
                return
            if not severity:
                self._send_json(400, {"ok": False, "error": "severity is required"})
                return
            if not evidence_text:
                self._send_json(400, {"ok": False, "error": "evidence_text is required"})
                return
            with _transaction(tool="viewer:POST /api/bugs/promote"):
                result = _promote_bugs_in_tx(
                    bug_ids=list(bug_ids),
                    title=title,
                    severity=severity,
                    evidence_text=evidence_text,
                    components=payload.get("components") or None,
                    body=payload.get("body", ""),
                )
            if isinstance(result, str):
                self._send_json(400, {"ok": False, "error": result})
                return
            self._send_json(201, {"ok": True, "issue_id": result[0]})
            return

        m = re.fullmatch(r"/api/bugs/([A-Za-z0-9_\-]+)/archive", clean_path_post)
        if m:
            bug_id = m.group(1)
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)  # consume body
            with _transaction(tool="viewer:POST /api/bugs/archive"):
                error = _bug_archive_in_tx(bug_id)
            if error is not None:
                code = 404 if error.startswith("Bug not found") else 400
                self._send_json(code, {"ok": False, "error": error})
                return
            self._send_json(200, {"ok": True, "id": bug_id})
            return

        m = re.fullmatch(r"/api/bugs/([A-Za-z0-9_\-]+)", clean_path_post)
        if m:
            bug_id = m.group(1)
            from taskmaster.taskmaster_v3 import BUG_STATUSES as _BUG_STATUSES_HTTP
            bp = _backlog_path()
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            updates = {}
            if "status" in payload:
                if payload["status"] not in _BUG_STATUSES_HTTP:
                    self._send_json(400, {"ok": False, "error": f"status must be one of {_BUG_STATUSES_HTTP}"})
                    return
                updates["status"] = payload["status"]
            for field in ("title", "severity", "fix_commit", "adopted_into", "promoted_to", "body"):
                if field in payload and payload[field]:
                    updates[field] = payload[field]
            for field in ("components", "location"):
                if field in payload and payload[field] is not None:
                    updates[field] = payload[field]
            with _transaction(tool="viewer:PATCH /api/bugs"):
                fm = _bug_update_in_tx(bug_id, updates)
            if isinstance(fm, str):
                code = 404 if fm.startswith("Bug not found") else 400
                self._send_json(code, {"ok": False, "error": fm})
                return
            self._send_json(200, {"ok": True, "id": bug_id, "status": fm["status"]})
            return

        self.send_response(404)
        self.end_headers()

    def do_PUT(self):
        import re
        if self.path == "/api/viewer/prefs":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                patch = json.loads(raw)
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            if not isinstance(patch, dict):
                self._send_json(400, {"ok": False, "error": "patch must be a JSON object"})
                return

            bp = _backlog_path()
            prefs = load_viewer_prefs(bp)
            _deep_merge(prefs, patch)
            save_viewer_prefs(bp, prefs)
            self._send_json(200, {"ok": True})
            return

        if m := re.fullmatch(r"/api/tasks/([A-Za-z0-9_\-]+)", self.path):
            task_id = m.group(1)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                full = json.loads(raw)
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            if not isinstance(full, dict):
                self._send_json(400, {"ok": False, "error": "body must be object"})
                return
            try:
                task = _viewer_update_task(
                    task_id, full, method="PUT", if_match=self.headers.get("If-Match")
                )
                self._send_json(200, {"ok": True, "task": task}, etag=_viewer_etag())
            except ViewerPreconditionFailed as e:
                self._send_stale(task_id, e.current_etag)
            except ViewerCompletionBlocked as e:
                self._send_json(409, {"ok": False, "error": str(e)})
            except ViewerWriteRejected as e:
                self._send_json(422, {"ok": False, "errors": e.errors})
            except KeyError as e:
                self._send_json(404, {"ok": False, "error": str(e)})
            except store.LegacyLayoutError as exc:
                self._send_json(409, {"ok": False, "error": str(exc)})
            return

        self.send_response(404)
        self.end_headers()

    def do_PATCH(self):
        import json
        import re
        if m := re.fullmatch(r"/api/tasks/([A-Za-z0-9_\-]+)", self.path):
            task_id = m.group(1)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                patch = json.loads(raw)
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            if not isinstance(patch, dict):
                self._send_json(400, {"ok": False, "error": "patch must be object"})
                return
            try:
                task = _viewer_update_task(
                    task_id, patch, if_match=self.headers.get("If-Match")
                )
                self._send_json(200, {"ok": True, "task": task}, etag=_viewer_etag())
            except ViewerPreconditionFailed as e:
                self._send_stale(task_id, e.current_etag)
            except ViewerCompletionBlocked as e:
                self._send_json(409, {"ok": False, "error": str(e)})
            except ViewerWriteRejected as e:
                self._send_json(422, {"ok": False, "errors": e.errors})
            except KeyError as e:
                self._send_json(404, {"ok": False, "error": str(e)})
            except store.LegacyLayoutError as exc:
                self._send_json(409, {"ok": False, "error": str(exc)})
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
            return

        self.send_error(404)

    def _send_stale(self, task_id: str, current_etag: str) -> None:
        """The unchanged 409 contract, with the revision the write lost to."""
        current, _etag = _load_task_full_identified(task_id)
        self._send_json(409, {
            "ok": False, "error": "stale",
            "current_etag": current_etag,
            "current": current,
        })

    def _send_json(self, status: int, payload: dict, etag: str | None = None):
        """Serialize *payload* as JSON and write the complete HTTP response.

        A successful mutation carries the `changes.seq` its commit ended at, the
        JSON counterpart of the `[seq N]` suffix on a tool's string result.
        """
        if (
            isinstance(payload, dict)
            and payload.get("ok")
            and 200 <= status < 300
            and self.command in ("POST", "PATCH", "PUT", "DELETE")
        ):
            payload = _json_with_seq(dict(payload))
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if etag:
            self.send_header("ETag", f'"{etag}"')
        # JSON API responses are intentionally uncached — no Cache-Control header
        # means browsers apply their default heuristic (usually no-store for XHR).
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """Allow cross-origin preflight for /api/* endpoints only."""
        from urllib.parse import urlparse
        clean_path = urlparse(self.path).path
        if not clean_path.startswith("/api/"):
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass  # suppress HTTP logs in MCP stderr


_MD_TEMPLATE = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{TITLE}}</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#141920;color:#d4dae3;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:14px;line-height:1.6;padding:0}
.topbar{background:#1c222b;border-bottom:1px solid #363e4a;padding:10px 24px;display:flex;align-items:center;gap:10px;position:sticky;top:0;z-index:10}
.topbar a{color:#58a6ff;text-decoration:none;font-size:13px;font-weight:600}
.topbar a:hover{text-decoration:underline}
.topbar .path{color:#97a0ad;font-family:"SFMono-Regular",Consolas,monospace;font-size:12px;flex:1}
.open-editor{background:#232a34;border:1px solid #363e4a;border-radius:4px;padding:4px 10px;font-size:12px !important;white-space:nowrap}
.open-editor:hover{background:#363e4a}
.zoom-controls{display:flex;align-items:center;gap:4px}
.zoom-btn{background:#232a34;border:1px solid #363e4a;border-radius:4px;padding:2px 8px;color:#d4dae3;cursor:pointer;font-size:13px;font-weight:600;line-height:1.4;min-width:26px;text-align:center}
.zoom-btn:hover{background:#363e4a}
.zoom-label{color:#97a0ad;font-size:11px;font-family:"SFMono-Regular",Consolas,monospace;min-width:36px;text-align:center}
.content{max-width:860px;margin:0 auto;padding:32px 24px;transition:font-size 0.15s ease}
h1,h2,h3,h4{color:#d4dae3;margin:20px 0 10px;line-height:1.3}
h1{font-size:1.7em;border-bottom:1px solid #363e4a;padding-bottom:8px}
h2{font-size:1.4em;border-bottom:1px solid #363e4a;padding-bottom:6px}
h3{font-size:1.15em}h4{font-size:1em;color:#97a0ad}
p{margin:8px 0}
a{color:#58a6ff}
code{font-family:"SFMono-Regular",Consolas,monospace;font-size:0.85em;background:#232a34;border:1px solid #363e4a;padding:1px 5px;border-radius:3px;color:#58a6ff}
pre{background:#0d1117;border:1px solid #363e4a;border-radius:6px;padding:14px 18px;overflow-x:auto;margin:12px 0}
pre code{background:none;border:none;padding:0;color:#d4dae3}
ul,ol{margin:8px 0;padding-left:22px}
li{margin:3px 0}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:0.93em}
th{text-align:left;padding:8px 12px;background:#232a34;border:1px solid #363e4a;font-weight:600}
td{padding:8px 12px;border:1px solid #363e4a}
tr:hover td{background:#1c222b}
blockquote{border-left:3px solid #58a6ff;padding:6px 14px;margin:10px 0;color:#97a0ad;background:#1c222b;border-radius:0 4px 4px 0}
hr{border:none;border-top:1px solid #363e4a;margin:20px 0}
strong{color:#d4dae3}
img{max-width:100%}
</style>
</head><body>
<div class="topbar">
  <a href="/">&larr; Backlog</a>
  <span class="path">{{TITLE}}</span>
  <div class="zoom-controls">
    <button class="zoom-btn" id="zoom-out" title="Zoom out">&minus;</button>
    <span class="zoom-label" id="zoom-label">100%</span>
    <button class="zoom-btn" id="zoom-in" title="Zoom in">+</button>
    <button class="zoom-btn" id="zoom-reset" title="Reset zoom">&#x21bb;</button>
  </div>
  <a href="vscode://file/{{FULL_PATH}}" class="open-editor">&#x1F4DD; Open in VSCode</a>
</div>
<div class="content" id="content"></div>
<script>
const raw = decodeURIComponent(atob("{{B64CONTENT}}").split('').map(c=>'%'+('00'+c.charCodeAt(0).toString(16)).slice(-2)).join(''));
document.getElementById('content').innerHTML = marked.parse(raw);

// Zoom
const ZOOM_KEY = 'taskmaster-docs-zoom';
const ZOOM_STEP = 10;
const ZOOM_MIN = 60;
const ZOOM_MAX = 200;
const BASE_SIZE = 14;
let zoomPct = parseInt(localStorage.getItem(ZOOM_KEY) || '100', 10);

function applyZoom() {
  zoomPct = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, zoomPct));
  document.getElementById('content').style.fontSize = (BASE_SIZE * zoomPct / 100) + 'px';
  document.getElementById('zoom-label').textContent = zoomPct + '%';
  localStorage.setItem(ZOOM_KEY, String(zoomPct));
}

document.getElementById('zoom-in').addEventListener('click', () => { zoomPct += ZOOM_STEP; applyZoom(); });
document.getElementById('zoom-out').addEventListener('click', () => { zoomPct -= ZOOM_STEP; applyZoom(); });
document.getElementById('zoom-reset').addEventListener('click', () => { zoomPct = 100; applyZoom(); });
applyZoom();
</script>
</body></html>"""

_viewer_started = False
VIEWER_PORT = 0


def _project_port() -> int:
    """Deterministic port per project root, in range 6800–6899."""
    h = hashlib.md5(str(ROOT.resolve()).encode()).hexdigest()
    return 6800 + int(h, 16) % 100


def _check_identity(port: int) -> bool:
    """Check if an existing server on `port` belongs to this project."""
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/identity", timeout=1)
        data = json.loads(resp.read())
        return Path(data.get("root", "")).resolve() == ROOT.resolve()
    except Exception:
        return False


def _make_server(host: str = "127.0.0.1", port: int = 0):
    """Build the HTTP server without starting it. Returns (server, bound_port)."""
    server = ThreadingHTTPServer((host, port), ViewerHandler)
    return server, server.server_address[1]


def _init_storage() -> None:
    """One-time storage migrations / dir setup. Called by server entry + tests."""
    pass


def _start_viewer_server() -> int:
    """Start the viewer HTTP server on a deterministic per-project port.

    If another session for the same project already owns the port, reuse it.
    If a different project owns it, fall back to an OS-assigned port.
    """
    global _viewer_started, VIEWER_PORT
    if _viewer_started:
        return VIEWER_PORT

    target_port = _project_port()

    # Disable SO_REUSEADDR — on Windows, it allows multiple processes to bind
    # the same port, causing requests to route to the wrong session's server.
    class _ExclusiveServer(ThreadingHTTPServer):
        allow_reuse_address = False

    try:
        server = _ExclusiveServer(("127.0.0.1", target_port), ViewerHandler)
        VIEWER_PORT = target_port
    except OSError:
        # Port taken — check if the existing server serves the same project
        if _check_identity(target_port):
            # Same project, another session already runs the server — reuse it
            _viewer_started = True
            VIEWER_PORT = target_port
            return VIEWER_PORT
        # Different project owns this port — use a random free port
        server = _ExclusiveServer(("127.0.0.1", 0), ViewerHandler)
        VIEWER_PORT = server.server_address[1]

    _init_storage()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _viewer_started = True
    return VIEWER_PORT


# Start on import so the viewer is always available
_start_viewer_server()


@mcp.tool()
def backlog_open_viewer() -> str:
    """Open the backlog kanban board in the default browser. The viewer auto-loads current data."""
    port = _start_viewer_server()
    url = f"http://127.0.0.1:{port}/"
    webbrowser.open(url)
    return f"Opened backlog viewer at {url}"


# --- .taskmaster/project.yaml (Project manifest) ---

from dataclasses import asdict
from taskmaster.project import (
    ProjectManifest,
    SCHEMA_VERSION,
    load_project_manifest,
    load_project_manifest_raw,
    manifest_to_dict,
    project_yaml_path,
    resolve_project_root,
    validate_manifest_dict,
)

_PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _project_root_or_cwd() -> Path:
    """Resolve a project root from ROOT (the module-level cwd anchor) or fall back."""
    root = resolve_project_root(ROOT)
    return root if root is not None else ROOT


def _dig(data: Any, path: str) -> Any:
    if not path.strip():
        return None
    cursor: Any = data
    for match in _PATH_TOKEN.finditer(path):
        key, idx = match.group(1), match.group(2)
        try:
            if key is not None:
                cursor = cursor[key]
            else:
                cursor = cursor[int(idx)]
        except (KeyError, IndexError, TypeError):
            return None
    return cursor


@mcp.tool()
def backlog_project_get() -> dict | None:
    """Return the full .taskmaster/project.yaml as a dict, or None if missing/invalid.

    The returned dict is the EXPANDED form — every dataclass default is filled
    in (e.g. `project.goal == ""` even when the YAML didn't set it). For "is
    this field absent in the source?" queries, use `backlog_project_get_field`
    which reads the raw YAML.
    """
    m = load_project_manifest(_project_root_or_cwd())
    return manifest_to_dict(m) if m is not None else None


@mcp.tool()
def backlog_project_get_field(path: str) -> Any:
    """Read a single field via dotted/indexed path from the RAW YAML.

    Unlike `backlog_project_get`, this reads the source file directly without
    coercing through dataclasses — so absent fields return None rather than
    their schema defaults. Examples:
        "meta.name"
        "repos[0].name"
        "repos[0].branches.protected[0]"

    Returns None if any segment is missing or out of range.
    """
    data = load_project_manifest_raw(_project_root_or_cwd())
    if data is None:
        return None
    return _dig(data, path)


@mcp.tool()
def backlog_project_ship_order() -> list[str]:
    """Return repos in topological dependency order. Empty list if no manifest.

    Raises ValueError if depends_on contains a cycle (caught by validator on load).
    """
    m = load_project_manifest(_project_root_or_cwd())
    return m.ship_order() if m is not None else []


@mcp.tool()
@_transactional("backlog_project_set")
def backlog_project_set(yaml_content: str) -> str:
    """Write .taskmaster/project.yaml with strict validation.

    Raises ValueError if YAML is malformed or schema invalid. The manifest is
    a store-owned entity, so the write commits through a store transaction.
    Returns the absolute path written, followed by the committed `[seq N]`.
    """
    try:
        data = yaml_io.safe_load(yaml_content) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML parse failed: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("project.yaml top-level must be a mapping")
    validate_manifest_dict(data, raise_on_error=True)

    root = _project_root_or_cwd()
    path = project_yaml_path(root)
    _ensure_taskmaster_dir(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_project_manifest(path, data)
    return str(path)


def _write_project_manifest(
    path: Path, document: dict, *, create_only: bool = False
) -> None:
    """Commit `project.yaml` through the store that owns its directory.

    The store knows `project` as a kind and renders the file itself, so the
    manifest can no longer be written behind its back and silently reverted by
    the next scan.

    `create_only` makes this an initialization: the existence check is the
    project *row*, read inside the writer transaction. Checking the projection
    file outside it let two initializers both pass and the second replace the
    first's committed manifest, and let a scaffold land on a real project whose
    first export had failed.
    """
    def apply() -> None:
        tx = _store_tx()
        try:
            tx.get("project", "__project__")
        except KeyError:
            tx.create("project", document, body=None, requested_id="__project__")
        else:
            if create_only:
                raise ValueError(
                    f"{path} already has a project manifest — refusing to "
                    "overwrite (edit it directly)"
                )
            tx.put("project", "__project__", document)

    if _active_tx() is not None:
        # The tool decorator already owns the transaction; joining it is what
        # keeps one public call to one transaction, and stops a second store
        # being opened when the manifest path and `_backlog_path()` disagree.
        apply()
        _mutate_and_save(_load())
        return
    backlog_path = path.parent / "backlog.yaml"
    with _transaction(tool="backlog_project_set", backlog_path=backlog_path) as data:
        apply()
        _mutate_and_save(data)


def _ensure_taskmaster_dir(path: Path) -> None:
    """Raise ValueError if path.parent exists but is not a directory."""
    if path.parent.exists() and not path.parent.is_dir():
        raise ValueError(f"{path.parent} exists but is not a directory")


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "project"


@mcp.tool()
@_transactional("backlog_project_init")
def backlog_project_init(name: str, slug: str = "") -> str:
    """Scaffold a minimal valid project.yaml. Refuses to overwrite.

    Returns a confirmation message including the path written, followed by the
    committed `[seq N]`.
    """
    if not name or not name.strip():
        raise ValueError("name: required (project name must be non-empty)")
    root = _project_root_or_cwd()
    path = project_yaml_path(root)
    if path.exists():
        raise ValueError(f"{path} exists — refusing to overwrite (edit it directly)")
    slug = slug or _slugify(name)
    scaffold = {
        "schema_version": SCHEMA_VERSION,
        "meta": {"name": name, "slug": slug, "kind": "app"},
        "project": {"description": "", "goal": "", "owners": [], "tags": []},
        "repos": [],
        "submodules": [],
        "integrations": {"observability": {"error_trace_ladder": []}, "external": []},
        "conventions": {"narrative_ref": "./CLAUDE.md", "policies": {}},
        "extensions": {},
    }
    _ensure_taskmaster_dir(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, errs = validate_manifest_dict(scaffold)
    if not ok:
        raise ValueError("refusing to write invalid manifest: " + "; ".join(errs))
    _write_project_manifest(path, scaffold, create_only=True)
    return f"Created {path}"


@mcp.tool()
def backlog_project_error_trace_ladder() -> list[dict]:
    """Return the observability error-trace ladder as a list of dicts.

    Empty list if no manifest. Consumed by IDEA-006 Diagnose-Auth-Or-Not.
    """
    m = load_project_manifest(_project_root_or_cwd())
    if m is None:
        return []
    return [asdict(e) for e in m.error_trace_ladder()]


# ── Linear MCP tools (linear-005) ────────────────────────────────────────────
#
# Consolidated behind one action-dispatched tool (tm-audit-020). The taskmaster:
# linear skill is the only correct driver; the per-verb functions below are the
# implementation and stay directly callable in-process (tests, internal callers).


@mcp.tool()
def backlog_linear(
    action: Literal[
        "probe", "bootstrap_apply", "link", "unlink",
        "list", "show", "status", "retry",
    ],
    task_id: str = "",
    external_key: str = "",
    workspace_alias: str = "",
    token_env: str = "",
    team_id: str = "",
    status_mapping: str = "",
    priority_mapping: str = "",
    default_workspace: bool = True,
    tracker_id: str = "",
    target_id: str = "",
) -> str:
    """Drive Taskmaster's Linear sync. Route through the taskmaster:linear skill.

    Params by action: probe(token_env); bootstrap_apply(workspace_alias, team_id,
    token_env, status_mapping, priority_mapping, default_workspace);
    link(task_id, external_key, workspace_alias); unlink(task_id); list();
    show(tracker_id); status(); retry(target_id).
    """
    if action == "probe":
        return backlog_linear_probe(token_env)
    if action == "bootstrap_apply":
        return backlog_linear_bootstrap_apply(
            workspace_alias, team_id, token_env,
            status_mapping, priority_mapping, default_workspace,
        )
    if action == "link":
        return backlog_linear_link(task_id, external_key, workspace_alias)
    if action == "unlink":
        return backlog_linear_unlink(task_id)
    if action == "list":
        return backlog_linear_list()
    if action == "show":
        return backlog_linear_show(tracker_id)
    if action == "status":
        return backlog_linear_status()
    if action == "retry":
        return backlog_linear_retry(target_id)
    return json.dumps({"error": f"unknown action {action!r}"})


def backlog_linear_probe(token_env: str) -> str:
    """Discover Linear workspace structure using a token read from the environment.

    Calls list_teams, then for each team samples list_issue_statuses and list_users.
    Returns a JSON summary suitable for proposing a status/priority mapping to the user.
    Never prints the token. On missing env var: error with a link to the Linear API key page.

    Args:
        token_env: Name of the environment variable holding the Linear API token.
    """
    import json
    import os
    from taskmaster.integrations.linear.client import LinearAPIError, LinearClient

    token = os.environ.get(token_env)
    if not token:
        return json.dumps({
            "error": (
                f"${token_env} is not set. "
                f"Create a Linear personal API key at https://linear.app/settings/api "
                f"then export {token_env}=<token>."
            )
        })

    client = LinearClient(token=token)
    try:
        teams = client.list_teams()
    except LinearAPIError as e:
        return json.dumps({"error": str(e)})

    result = []
    for team in teams:
        tid = team.get("id", "")
        entry: dict = {"id": tid, "name": team.get("name"), "key": team.get("key")}
        try:
            entry["statuses"] = client.list_issue_statuses(tid)
        except LinearAPIError as e:
            entry["statuses_error"] = str(e)
        try:
            entry["users"] = client.list_users(tid)
        except LinearAPIError as e:
            entry["users_error"] = str(e)
        result.append(entry)

    return json.dumps({"teams": result}, indent=2)


def backlog_linear_bootstrap_apply(
    workspace_alias: str,
    team_id: str,
    token_env: str,
    status_mapping: str = "",
    priority_mapping: str = "",
    default_workspace: bool = True,
) -> str:
    """Write (or append) a workspace entry to .taskmaster/linear.yaml.

    If linear.yaml exists: appends the new workspace (errors if alias collides).
    If absent: creates the file with this workspace as the only entry.

    status_mapping / priority_mapping: optional comma-separated tm_value:linear_id pairs
    (e.g. "todo:state-uuid-1,in-progress:state-uuid-2").

    Args:
        workspace_alias: Short identifier for this workspace (e.g. "cm").
        team_id: Linear team UUID for this workspace.
        token_env: Environment variable name holding the Linear API token.
        status_mapping: Comma-separated TM-status:linear-state-id pairs.
        priority_mapping: Comma-separated TM-priority:linear-priority-id pairs.
        default_workspace: If True (default), set this workspace as default_workspace.
    """
    import json
    from taskmaster.taskmaster_v3 import (
        linear_config_path,
        _validate_linear_config,
    )

    if not workspace_alias or not workspace_alias.strip():
        return json.dumps({"error": "workspace_alias is required"})
    if not team_id or not team_id.strip():
        return json.dumps({"error": "team_id is required"})
    if not token_env or not token_env.strip():
        return json.dumps({"error": "token_env is required"})

    def _parse_mapping(raw: str) -> dict:
        """Parse 'a:b,c:d' into {'a': 'b', 'c': 'd'}, deduped, no empty halves."""
        out: dict = {}
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair:
                continue
            parts = pair.split(":", 1)
            if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
                raise ValueError(f"invalid mapping pair {pair!r} — expected tm_value:linear_id")
            out[parts[0].strip()] = parts[1].strip()
        return out

    try:
        sm = _parse_mapping(status_mapping) if status_mapping.strip() else {}
        pm = _parse_mapping(priority_mapping) if priority_mapping.strip() else {}
    except ValueError as e:
        return json.dumps({"error": str(e)})

    bp = _backlog_path()
    cfg_path = linear_config_path(bp)

    ws_entry: dict = {
        "alias": workspace_alias,
        "team_id": team_id,
        "token_env": token_env,
    }
    if sm:
        ws_entry["status_mapping"] = sm
    if pm:
        ws_entry["priority_mapping"] = pm

    def _add_workspace(cfg: dict) -> dict:
        # The collision check, the append and the write are one critical
        # section held by the store: doing them around an unlocked read let two
        # agents adding different aliases both report success with only one
        # addition surviving.
        existing = {ws.get("alias") for ws in cfg.get("workspaces") or []}
        if workspace_alias in existing:
            raise _AliasExists(workspace_alias)
        cfg.setdefault("workspaces", []).append(dict(ws_entry))
        if default_workspace:
            cfg["default_workspace"] = workspace_alias
        _validate_linear_config(cfg)
        return cfg

    try:
        _store_for(bp).update_root_config(cfg_path.name, _add_workspace)
    except _AliasExists:
        return json.dumps({
            "error": f"workspace alias {workspace_alias!r} already exists in linear.yaml"
        })
    except ValueError as e:
        return json.dumps({"error": f"config validation failed: {e}"})

    return json.dumps({
        "ok": True,
        "path": str(cfg_path),
        "workspace": workspace_alias,
        "default": default_workspace,
    })


@_transactional("backlog_linear:link")
def backlog_linear_link(task_id: str, external_key: str, workspace_alias: str = "") -> str:
    """Link an existing TM task to an existing Linear issue by creating a Tracker file.

    Pure local operation — does not push to Linear or fetch from it.
    Sets tracker_id on the task in backlog.yaml.

    Args:
        task_id: Taskmaster task id (e.g. "ts-001").
        external_key: Linear issue identifier (e.g. "ENG-42").
        workspace_alias: Workspace alias from linear.yaml. Uses default_workspace if empty.
    """
    import json

    bp = _backlog_path()
    if not bp.exists():
        return json.dumps({"error": "No backlog found."})

    cfg = _load_linear_config(bp)
    if cfg is None:
        return json.dumps({"error": "linear.yaml not found — run backlog_linear_bootstrap_apply first."})

    try:
        from taskmaster.taskmaster_v3 import get_linear_workspace
        workspace = get_linear_workspace(cfg, workspace_alias or None)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    alias = workspace["alias"]
    data = _load()
    result = _find_task(data, task_id)
    if result is None:
        return json.dumps({"error": f"task {task_id!r} not found in backlog"})
    task, _epic = result

    existing_tracker = task.get("tracker_id")
    if existing_tracker:
        return json.dumps({"error": f"task {task_id!r} already has tracker_id {existing_tracker!r} — unlink first"})

    tracker_id = _make_tracker_id("linear", alias, external_key)
    tx = _store_tx()
    if tx.id_taken("tracker", tracker_id):
        return json.dumps({
            "error": f"tracker {tracker_id} already exists \u2014 it may be linked to another task"
        })

    try:
        tx.create(
            "tracker",
            _build_tracker_doc(
                external_system="linear",
                instance_alias=alias,
                external_key=external_key,
                title=task.get("title", external_key),
                status=task.get("status", "todo"),
            ),
            body="",
        )
    except ValueError as e:
        return json.dumps({"error": f"failed to write tracker: {e}"})

    task["tracker_id"] = tracker_id
    _sync_tracker_index_tx(data)
    _mutate_and_save(data)

    return json.dumps({"ok": True, "tracker_id": tracker_id, "task_id": task_id})


@_transactional("backlog_linear:unlink")
def backlog_linear_unlink(task_id: str) -> str:
    """Clear the tracker_id on a TM task. Does NOT delete the Tracker file.

    Idempotent: if the task has no tracker_id, returns that fact without error.

    Args:
        task_id: Taskmaster task id (e.g. "ts-001").
    """
    import json

    bp = _backlog_path()
    if not bp.exists():
        return json.dumps({"error": "No backlog found."})

    data = _load()
    result = _find_task(data, task_id)
    if result is None:
        return json.dumps({"error": f"task {task_id!r} not found in backlog"})
    task, _epic = result

    existing = task.get("tracker_id")
    if not existing:
        return json.dumps({"ok": True, "note": f"task {task_id!r} had no tracker_id — nothing to unlink"})

    task.pop("tracker_id", None)
    _mutate_and_save(data)
    return json.dumps({"ok": True, "unlinked": existing, "task_id": task_id})


def backlog_linear_list() -> str:
    """Return JSON list of all linear-* trackers from disk.

    Each item: id, external_key, title, status, instance_alias, last_pushed, push_hash.
    Reads from tracker frontmatter; does not hit Linear.
    """
    import json

    bp = _backlog_path()
    if not bp.exists():
        return json.dumps({"trackers": []})

    # `tracker` is a row-backed kind: reading `trackers/*.md` here made a
    # tracker whose export had not landed read as absent (decision 1).
    out = []
    for tid, fm, _body in _dict_rows(_load(), "tracker"):
        if not tid.startswith("linear-"):
            continue
        out.append({
            "id": fm.get("id"),
            "external_key": fm.get("external_key"),
            "title": fm.get("title"),
            "status": fm.get("status"),
            "instance_alias": fm.get("instance_alias"),
            "last_pushed": fm.get("last_pushed"),
            "push_hash": fm.get("push_hash"),
        })

    return json.dumps({"trackers": out}, indent=2)


def backlog_linear_show(tracker_id: str) -> str:
    """Return one tracker's full frontmatter and body as JSON.

    Returns an error-style JSON if the tracker is missing.

    Args:
        tracker_id: Tracker id in linear-<alias>-<key> format (e.g. "linear-cm-eng-42").
    """
    import json

    bp = _backlog_path()
    if not bp.exists():
        return json.dumps({"error": "No backlog found."})

    row = _dict_row(_load(), "tracker", tracker_id)
    if row is None:
        return json.dumps({"error": f"tracker {tracker_id!r} not found"})

    fm, body = row[0], row[1] or ""
    return json.dumps({"frontmatter": fm, "body": body}, indent=2, default=str)


def backlog_linear_status() -> str:
    """Return a summary of the Linear sync queue state.

    Includes: queue depth, oldest pending item's enqueued_at, count of parked
    (permanently failed) items, and the last error message if any. No network calls.
    """
    import json

    bp = _backlog_path()
    if not bp.exists():
        return json.dumps({
            "queue_depth": 0,
            "pending": 0,
            "permanent_failures": 0,
            "oldest_enqueued_at": None,
            "last_error": None,
            "failed_enqueues": 0,
            "warning": None,
        }, indent=2)

    # `done` rows are settled history; the depth a caller acts on is what is
    # still pending plus what is parked awaiting an explicit retry. On a network
    # root with no usable store there are no rows to read, and the queue reads
    # answer empty rather than raising — the warning is the useful part.
    opened = _store_for(bp)
    degraded = opened.projection_only_reason()
    # A `claimed` row is one a drain has in flight: still owed, not settled,
    # so it counts as pending rather than vanishing from the depth.
    rows = opened.linear_rows(states=("pending", "claimed", "failed"))
    pending = [row for row in rows if row["state"] in ("pending", "claimed")]
    parked = [row for row in rows if row["state"] == "failed"]

    stamps = [
        (row["payload"] or {}).get("enqueued_at")
        for row in pending
        if (row["payload"] or {}).get("enqueued_at")
    ]
    oldest_at = min(stamps) if stamps else None

    last_error = None
    for row in reversed(rows):
        if row["last_error"]:
            last_error = row["last_error"]
            break

    return json.dumps({
        "queue_depth": len(rows),
        "pending": len(pending),
        "permanent_failures": len(parked),
        "oldest_enqueued_at": oldest_at,
        "last_error": last_error,
        # Pushes that never became queue rows at all: the enqueue hook survives
        # its own failures so the local write still lands, so this count is the
        # only place a lost push shows up.
        "failed_enqueues": opened.linear_enqueue_failures(),
        "warning": degraded,
    }, indent=2)


def backlog_linear_retry(target_id: str = "") -> str:
    """Drain the Linear sync queue, optionally limiting to one target.

    If target_id is empty: drains all queued items.
    If target_id is provided: drains only items for that target, leaving others intact.

    Requires linear.yaml and the configured token env var.

    Args:
        target_id: Taskmaster task id to retry. Empty = retry all.
    """
    import json
    from taskmaster.integrations.linear.client import LinearAPIError, LinearClient
    from taskmaster.integrations.linear import worker as _worker

    bp = _backlog_path()
    if not bp.exists():
        return json.dumps({"error": "No backlog found."})

    cfg = _load_linear_config(bp)
    if cfg is None:
        return json.dumps({"error": "linear.yaml not found — run backlog_linear_bootstrap_apply first."})

    # A drain is a queue write, and on a network root with no usable store there
    # is no queue to write. Say so before spending a token lookup and an HTTP
    # client on it.
    st = _store_for(bp)
    degraded = st.projection_only_reason()
    if degraded:
        return json.dumps({"error": f"store unavailable on network storage: {degraded}"})

    try:
        from taskmaster.taskmaster_v3 import get_linear_workspace, resolve_linear_token
        workspace = get_linear_workspace(cfg)
        token = resolve_linear_token(workspace)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    try:
        client = LinearClient(token=token)
    except (ValueError, LinearAPIError) as e:
        return json.dumps({"error": f"cannot build Linear client: {e}"})

    data = _load()

    # An explicit retry is the un-park action: every row for the targets being
    # retried goes back to `pending` with a cleared attempt count, so a dead push
    # gets a fresh budget (routine drains never see parked rows at all). Rows for
    # other targets are not touched, so a target-scoped retry cannot lose them
    # and a crash mid-drain leaves them exactly as they were (B-029).
    candidates = [
        row
        for row in st.linear_rows(states=("pending", "claimed", "failed"))
        if not target_id or row["target_id"] == target_id
    ]

    if target_id and not candidates:
        return json.dumps({"error": f"no queued items for target_id {target_id!r}"})

    # A row a drain is pushing right now is deliberately not un-parked: doing so
    # would let the next drain claim a request that is still open at Linear and
    # push it a second time. The caller is told how many it skipped, because a
    # retry that reports nothing drained is otherwise indistinguishable from a
    # retry that had nothing to do.
    requeued = st.linear_requeue(row["seq"] for row in candidates)
    counts = _worker.drain(
        st, client, cfg, backlog_data=data,
        only_targets={target_id} if target_id else None,
    )

    return json.dumps({
        "ok": True,
        "counts": counts,
        "in_flight_skipped": len(candidates) - requeued,
    }, indent=2)


# Importing this module is what makes the shared entity dispatcher store-aware.
_configure_entity_io()


def main() -> None:
    """Entry point for both `taskmaster/backlog_server.py` and the root shim."""
    mcp.run()


if __name__ == "__main__":
    main()
