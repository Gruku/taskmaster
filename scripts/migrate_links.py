"""Translate legacy linkage fields -> typed `links` arrays.

One-shot, idempotent. Walks every entity (tasks, handovers, issues,
lessons, ideas) under <root>/.taskmaster/, calls legacy_links_to_typed
to produce the new array, writes it back, then runs
`backlog_link_reconcile` to fill in any missing inverses.

Usage:
    python -m plugins.taskmaster.scripts.migrate_links --root <project_root>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# backlog_server.py uses bare imports (`import blast_radius`); make sure
# plugins/taskmaster is on sys.path so its sibling modules resolve.
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

# Invoked via `python -m plugins.taskmaster.scripts.migrate_links` from repo root;
# package imports are valid here. Requires plugins/__init__.py and
# plugins/taskmaster/__init__.py (created by Plan A Task 0).
from plugins.taskmaster.taskmaster_v3 import (
    load_v3, save_v3,
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

    counts = {"tasks": 0, "issues": 0, "lessons": 0, "handovers": 0, "ideas": 0}

    # Tasks via load_v3/save_v3.
    data = load_v3(backlog_path)
    for epic in data.get("epics", []):
        for task in epic.get("tasks", []):
            changed, _ = _migrate_one(task, kind="task", drop_legacy=drop_legacy)
            if changed:
                counts["tasks"] += 1
    save_v3(backlog_path, data)

    for sub, prefix, kind in (
        ("handovers", "*",    "handover"),  # handover IDs are date-slug, not HND-NNN
        ("issues",    "ISS",  "issue"),
        ("lessons",   "L",    "lesson"),
        ("ideas",     "IDEA", "idea"),
    ):
        sub_dir = backlog_path.parent / sub
        if not sub_dir.exists():
            continue
        if prefix == "*":
            files = sorted(sub_dir.glob("*.md"))
        else:
            files = sorted(sub_dir.glob(f"{prefix}-*.md"))
        for fp in files:
            eid = fp.stem
            # Read with fallback=False so the migration sees the raw frontmatter
            # and `_migrate_one` actually mutates it. With fallback=True the
            # synthesized `links` would make _migrate_one a no-op.
            entity = read_entity_anywhere(backlog_path, eid, fallback=False)
            if entity is None:
                continue
            changed, _ = _migrate_one(entity, kind=kind, drop_legacy=drop_legacy)
            if changed:
                write_entity_anywhere(backlog_path, entity)
                counts[sub] += 1

    # Reconcile inverses by temporarily redirecting bs._backlog_path.
    from plugins.taskmaster import backlog_server as bs

    original = bs._backlog_path
    try:
        bs._backlog_path = lambda: backlog_path  # type: ignore[assignment]
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
