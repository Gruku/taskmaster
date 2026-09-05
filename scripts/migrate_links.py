"""Translate legacy linkage fields -> typed `links` arrays.

One-shot, idempotent. Walks every entity (tasks, handovers, issues,
ideas) under <root>/.taskmaster/, calls legacy_links_to_typed
to produce the new array, writes it back, then runs
`backlog_link_reconcile` to fill in any missing inverses.

Usage:
    python -m scripts.migrate_links --root <project_root>   (from the repo root)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Put the repo root on sys.path so the taskmaster package resolves when
# this script is run directly (not via -m from the repo root).
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from taskmaster.taskmaster_v3 import (
    read_entity_anywhere, write_entity_anywhere,
    legacy_links_to_typed, set_entity_links,
    _LEGACY_FIELDS_TO_DROP,
    entity_links,
)


def _migrate_one(entity: dict, kind: str, *, drop_legacy: bool) -> tuple[bool, int]:
    """Return (changed, added_count)."""
    before = entity_links(entity)
    after = legacy_links_to_typed(entity, kind=kind)
    if drop_legacy:
        for field in _LEGACY_FIELDS_TO_DROP.get(kind, ()):
            entity.pop(field, None)
    if after != before:
        set_entity_links(entity, after)
        return True, len(after) - len(before)
    return False, 0


def migrate(root: Path, *, drop_legacy: bool = True) -> dict:
    backlog_path = root / ".taskmaster" / "backlog.yaml"
    if not backlog_path.exists():
        raise SystemExit(f"no backlog.yaml at {backlog_path}")

    counts = {"tasks": 0, "issues": 0, "handovers": 0, "ideas": 0}

    # Everything commits through the store: it owns the whole projection now,
    # so a whole-tree rewrite here would be overwritten by the next scan.
    from taskmaster import backlog_server as bs

    original = bs._backlog_path
    try:
        bs._backlog_path = lambda: backlog_path  # type: ignore[assignment]

        data = bs._store_for(backlog_path).load_dict()
        for epic in data.get("epics", []):
            for task in epic.get("tasks", []):
                changed, _ = _migrate_one(task, kind="task", drop_legacy=drop_legacy)
                if changed:
                    write_entity_anywhere(backlog_path, task)
                    counts["tasks"] += 1

        for sub, kind in (
            ("handovers", "handover"),
            ("issues", "issue"),
            ("ideas", "idea"),
        ):
            for eid in sorted((data.get("_rows") or {}).get(kind) or {}):
                # Read with fallback=False so the migration sees the raw
                # frontmatter and `_migrate_one` actually mutates it. With
                # fallback=True the synthesized `links` would make it a no-op.
                entity = read_entity_anywhere(backlog_path, eid, fallback=False)
                if entity is None:
                    continue
                changed, _ = _migrate_one(entity, kind=kind, drop_legacy=drop_legacy)
                if changed:
                    write_entity_anywhere(backlog_path, entity)
                    counts[sub] += 1

        report = json.loads(bs.backlog_link_reconcile())
    finally:
        bs._backlog_path = original  # type: ignore[assignment]

    return {"migrated": counts, "reconcile": report}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True,
                        help="Project root containing .taskmaster/")
    parser.add_argument("--keep-legacy", action="store_true",
                        help="Keep old linkage fields in addition to writing 'links'.")
    args = parser.parse_args(argv)

    summary = migrate(Path(args.root), drop_legacy=not args.keep_legacy)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
