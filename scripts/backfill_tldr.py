# User intent: one-shot backfill of missing `tldr` fields on legacy tasks,
# issues and ideas — committed through the SQLite store so the projection keeps
# exactly one writer, and idempotent so a second run is a no-op.
"""Backfill `tldr` on tasks, issues and ideas that predate the field.

Handovers are skipped: they have required a `tldr` at write time since v3.

Usage:
    python -m scripts.backfill_tldr [--root <project_root>] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts import _store_rows  # noqa: E402
from taskmaster import store  # noqa: E402
from taskmaster.taskmaster_v3 import backfill_tldr  # noqa: E402

TOOL = "scripts/backfill_tldr"
KINDS = ("task", "issue", "idea")

Plan = list[tuple[str, str, dict, "str | None"]]


def plan(read_rows) -> Plan:
    """`(kind, id, new_doc, body)` for every live row still missing a tldr.

    `read_rows(kind)` yields `(id, doc, body)` — `Transaction.list` on the write
    path, the committed snapshot on the dry-run path — so both report the same
    set from one consistent view.
    """
    planned: Plan = []
    for kind in KINDS:
        for ident, doc, body in read_rows(kind):
            new_doc, changed = backfill_tldr(dict(doc), body or "")
            if changed:
                planned.append((kind, ident, new_doc, body))
    return planned


def _report(planned: Plan, *, dry_run: bool) -> None:
    verb = "would backfill" if dry_run else "backfilled"
    for kind, ident, doc, _body in planned:
        print(f"  {verb} {kind} {ident}: {doc['tldr']!r}")
    if not planned:
        print("Nothing to do: every task, issue and idea already has a tldr.")
        return
    tail = " (dry-run, nothing written)" if dry_run else ""
    print(f"\nDone. {len(planned)} entities {verb}{tail}.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill missing tldr fields.")
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
        _report(plan(lambda kind: _store_rows.rows(data, kind)), dry_run=True)
        return 0

    with store.open_store(directory).transaction(tool=TOOL) as tx:
        planned = plan(tx.list)
        for kind, ident, doc, body in planned:
            tx.put(kind, ident, doc, body=body)
    _report(planned, dry_run=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
