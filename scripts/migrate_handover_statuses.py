#!/usr/bin/env python3
"""One-shot CLI: migrate handover status enum from v1 (todo/in-progress/done)
to v2 (open/closed/superseded).

Usage:
    python scripts/migrate_handover_statuses.py /path/to/project/.taskmaster/backlog.yaml

Dry-run (no writes):
    python scripts/migrate_handover_statuses.py /path/to/project/.taskmaster/backlog.yaml --dry-run
"""
import argparse
import sys
from pathlib import Path

# Allow running from the repo root or from within the plugin dir.
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_ROOT))

import yaml
from taskmaster_v3 import migrate_handover_statuses


def _collect_terminal_task_ids(backlog_data: dict) -> set[str]:
    terminal: set[str] = set()
    for epic in backlog_data.get("epics", []):
        for task in epic.get("tasks", []):
            if task.get("status") in ("done", "archived"):
                terminal.add(task["id"])
    return terminal


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate handover statuses to v2 enum.")
    parser.add_argument("backlog_yaml", help="Path to .taskmaster/backlog.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change without writing files.")
    args = parser.parse_args()

    bp = Path(args.backlog_yaml).resolve()
    if not bp.exists():
        print(f"Error: backlog.yaml not found at {bp}", file=sys.stderr)
        sys.exit(1)

    data = yaml.safe_load(bp.read_text(encoding="utf-8")) or {}
    terminal_ids = _collect_terminal_task_ids(data)

    if args.dry_run:
        # Shallow clone to avoid mutating the marker; pass a copy of data.
        import copy
        data_copy = copy.deepcopy(data)
        report = migrate_handover_statuses(data_copy, bp, done_or_archived_ids=terminal_ids)
        print(f"[dry-run] Would migrate {len(report['migrated'])} handover(s):")
        for hid in report["migrated"]:
            print(f"  {hid}")
        return

    report = migrate_handover_statuses(data, bp, done_or_archived_ids=terminal_ids)
    # Persist the idempotency marker back to backlog.yaml.
    bp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"Migrated {len(report['migrated'])} handover(s):")
    for hid in report["migrated"]:
        print(f"  {hid}")
    print("Done. Run `backlog_handover_resync` to rebuild the index.")


if __name__ == "__main__":
    main()
