#!/usr/bin/env python3
"""PreCompact hook: write a snapshot of the current backlog so post-compact
recap reflects the pre-compact state.

This script is intentionally robust: it never errors out on the user (a
broken hook shouldn't block their workflow). On any problem it prints a
short note to stderr and exits 0.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

# Hooks run from the project root (cwd) — locate this plugin's modules
# relative to the script file, then load taskmaster_v3.
_PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN_DIR))

try:
    import taskmaster_v3 as v3
except Exception as exc:  # pragma: no cover - defensive
    sys.stderr.write(f"taskmaster snapshot hook: failed to import taskmaster_v3: {exc}\n")
    sys.exit(0)


def _find_backlog(start: Path) -> Path | None:
    """Walk up from `start` looking for a backlog.yaml in the usual places."""
    cur = start
    for _ in range(10):  # walk-up limit
        for rel in (".taskmaster/backlog.yaml", ".claude/backlog.yaml", "backlog.yaml"):
            p = cur / rel
            if p.exists():
                return p
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def main() -> int:
    cwd = Path(os.environ.get("TASKMASTER_ROOT", os.getcwd()))
    bp = _find_backlog(cwd)
    if bp is None:
        # No backlog → nothing to snapshot. Silent success.
        return 0

    try:
        raw = yaml.safe_load(bp.read_text(encoding="utf-8")) or {}
        data = v3.load_v3(bp) if v3.detect_schema_version(raw) >= v3.SCHEMA_V3 else raw
        snap = v3.take_snapshot(data)
        v3.write_snapshot(bp, snap)
    except Exception as exc:
        sys.stderr.write(f"taskmaster snapshot hook: {exc}\n")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
