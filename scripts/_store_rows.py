# User intent: one shared way for the maintenance scripts to find a project's
# store and to read committed rows during `--dry-run`, so a dry run reports
# exactly what the transactional run would change without opening a write
# transaction of its own.
"""Read-side helpers shared by the `scripts/` maintenance CLIs."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Put the repo root on sys.path so `taskmaster` resolves when a script is run
# directly rather than with `-m` from the repo root.
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from taskmaster import store  # noqa: E402
from taskmaster.taskmaster_v3 import BODY_KEY  # noqa: E402

Row = tuple[str, dict[str, Any], "str | None"]


def backlog_dir(root: str | None) -> Path:
    """`<root>/.taskmaster` — from `--root` when given, else resolved from cwd."""
    explicit = Path(root).expanduser().resolve() if root else None
    return store.resolve_root(explicit_root=explicit).backlog_path


def require_backlog(root: str | None) -> Path | None:
    """The backlog directory, or None (after reporting) when there is no project.

    A maintenance script never bootstraps a project: an unreadable `--root` is
    a typo far more often than an invitation to create `.taskmaster/`.
    """
    directory = backlog_dir(root)
    if (directory / "backlog.yaml").exists():
        return directory
    print(f"No backlog.yaml under {directory}", file=sys.stderr)
    return None


def rows(
    data: dict[str, Any], kind: str, *, include_archived: bool = False
) -> list[Row]:
    """Committed `(id, doc, body)` rows — the read-only twin of `Transaction.list`.

    `data` is a `Store.load_dict()` snapshot: tasks hang off their epic and the
    remaining kinds arrive under `_rows`. Both shapes carry the body inline, so
    this splits it back out and the planners stay identical across dry-run and
    write paths.
    """
    if kind == "task":
        found: list[Row] = []
        for epic in data.get("epics") or []:
            for task in epic.get("tasks") or []:
                doc = dict(task)
                body = doc.pop(BODY_KEY, None)
                ident = str(doc.get("id") or "")
                if not ident or (doc.get("archived") and not include_archived):
                    continue
                found.append((ident, doc, body))
        return sorted(found, key=lambda row: row[0])
    stored = (data.get("_rows") or {}).get(kind) or {}
    return [
        (ident, dict(doc), body)
        for ident, (doc, body) in sorted(stored.items())
        if include_archived or not doc.get("archived")
    ]
