#!/usr/bin/env python3
# User intent: move handover statuses off the legacy v1 enum once, through the
# store, so the rewritten handovers and the idempotency marker on backlog.yaml
# land in a single commit and the index arrays rebuild themselves.
"""Migrate handover status from v1 (todo/in-progress/done) to v2
(open/closed/superseded).

Usage:
    python -m scripts.migrate_handover_statuses [--root <project_root>] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts import _store_rows  # noqa: E402
from taskmaster import store  # noqa: E402
from taskmaster.taskmaster_v3 import migrate_handover_statuses  # noqa: E402

TOOL = "scripts/migrate_handover_statuses"
Row = tuple[str, dict[str, Any], "str | None"]


def _terminal_task_ids(
    rows: Iterable[tuple[str, Mapping[str, Any], "str | None"]],
) -> set[str]:
    """Ids of tasks a handover may be auto-closed against."""
    return {
        ident for ident, doc, _body in rows
        if doc.get("status") in ("done", "archived")
    }


def _report(migrated: list[Row], *, dry_run: bool) -> None:
    if not migrated:
        print("Nothing to do: handover statuses are already on the v2 enum.")
        return
    prefix = "[dry-run] Would migrate" if dry_run else "Migrated"
    print(f"{prefix} {len(migrated)} handover(s):")
    for hid, doc, _body in migrated:
        print(f"  {hid} -> {doc['status']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate handover statuses to the v2 enum."
    )
    parser.add_argument("--root", default=None,
                        help="Project root containing .taskmaster/ "
                             "(default: resolved from the current directory)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing.")
    args = parser.parse_args(argv)

    directory = _store_rows.require_backlog(args.root)
    if directory is None:
        return 2

    if args.dry_run:
        data = store.open_store(directory).load_dict()
        # A copy: the planner stamps the idempotency marker on what it is given.
        backlog_doc = {
            key: value for key, value in data.items()
            if not key.startswith("_") and key not in ("epics", "phases", "context")
        }
        report = migrate_handover_statuses(
            backlog_doc,
            _store_rows.rows(data, "handover", include_archived=True),
            done_or_archived_ids=_terminal_task_ids(
                _store_rows.rows(data, "task", include_archived=True)
            ),
        )
        _report(report["migrated"], dry_run=True)
        return 0

    with store.open_store(directory).transaction(tool=TOOL) as tx:
        backlog_rows: list[Row] = tx.list("backlog")
        if not backlog_rows:
            print("No backlog row in the store; nothing to migrate.",
                  file=sys.stderr)
            return 2
        backlog_id, backlog_doc, backlog_body = backlog_rows[0]
        report = migrate_handover_statuses(
            backlog_doc,
            tx.list("handover", include_archived=True),
            done_or_archived_ids=_terminal_task_ids(
                tx.list("task", include_archived=True)
            ),
        )
        # The planner carries each row's body through, so committing the plan
        # cannot erase a handover's narrative.
        for hid, doc, body in report["migrated"]:
            tx.put("handover", hid, doc, body=body)
        # The marker rides the same commit as the rewrites it guards.
        tx.put("backlog", backlog_id, backlog_doc, body=backlog_body)

    _report(report["migrated"], dry_run=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
