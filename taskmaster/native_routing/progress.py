# User intent: the one seam through which the native drain renders PROGRESS.md (N11).
# The protocol track (S6) calls it at the end of every drain; the changelog track
# (S7-S9) owns this module and fills it in. Until then it does nothing.
"""Local PROGRESS.md export on native stores (seam; S8 renders it)."""
from __future__ import annotations

from pathlib import Path
import sqlite3


def export(connection: sqlite3.Connection, backlog_dir: Path, session: str) -> list[str]:
    """Render PROGRESS.md if its changelog or dashboard is due; return `export pending` notices.

    Called by `native_routing.projection.drain` after the projection jobs, with no
    transaction open. A no-op until the changelog track implements it.
    """
    return []
