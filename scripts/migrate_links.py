# User intent: translate legacy linkage fields into typed `links` arrays once,
# inside a single store transaction, so the read that plans the migration and
# the writes that apply it see one snapshot and no concurrent edit is lost.
"""Translate legacy linkage fields -> typed `links` arrays.

One-shot and idempotent. Reads every entity through the store, converts the
legacy fields, fills in the missing inverse link on each peer, and commits the
lot in one transaction. Entities whose only change is a dropped legacy field
are written too, so a re-run has nothing left to do.

Usage:
    python -m scripts.migrate_links [--root <project_root>] [--dry-run]
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts import _store_rows  # noqa: E402
from taskmaster import store  # noqa: E402
from taskmaster.taskmaster_v3 import (  # noqa: E402
    _LEGACY_FIELDS_TO_DROP,
    REVERSE_TYPE,
    add_link,
    entity_links,
    legacy_links_to_typed,
    set_entity_links,
)

TOOL = "scripts/migrate_links"
# Kinds carrying legacy linkage fields, mapped to the report's plural key.
LEGACY_KINDS = {"task": "tasks", "issue": "issues",
                "handover": "handovers", "idea": "ideas"}
# Every kind that can hold a `links` array. The wider set matters for the
# inverse pass: a link into a bug or a decision is a real peer to fix up, not
# the orphan the old directory-walking reconciler called it.
LINK_KINDS = ("task", "bug", "issue", "handover", "decision", "idea",
              "note", "area", "tracker")


@dataclass
class Entity:
    kind: str
    ident: str
    doc: dict[str, Any]
    body: str | None
    before: dict[str, Any] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return self.doc != self.before


def _collect(read_rows) -> dict[str, Entity]:
    """`{id: Entity}` across every link-bearing kind, from one snapshot."""
    entities: dict[str, Entity] = {}
    for kind in LINK_KINDS:
        for ident, doc, body in read_rows(kind):
            if ident in entities:
                continue  # ids are kind-prefixed; a clash is not ours to resolve
            entities[ident] = Entity(
                kind=kind, ident=ident, doc=dict(doc), body=body,
                before=copy.deepcopy(dict(doc)),
            )
    return entities


def _migrate_one(doc: dict[str, Any], kind: str, *, drop_legacy: bool) -> bool:
    """Fold this entity's legacy fields into its `links`. True when links grew."""
    before = entity_links(doc)
    after = legacy_links_to_typed(doc, kind=kind)
    if drop_legacy:
        for name in _LEGACY_FIELDS_TO_DROP.get(kind, ()):
            doc.pop(name, None)
    if after != before:
        set_entity_links(doc, after)
        return True
    return False


def _reconcile_inverses(entities: dict[str, Entity]) -> tuple[int, list[dict]]:
    """Add the missing inverse of every link onto its peer. `(fixed, orphans)`."""
    fixed = 0
    orphans: list[dict] = []
    for ident in sorted(entities):
        for link in entity_links(entities[ident].doc):
            link_type = link.get("type")
            target = link.get("target")
            inverse = REVERSE_TYPE.get(link_type)
            if inverse is None or not target:
                continue
            peer = entities.get(target)
            if peer is None:
                orphans.append({"source": ident, "target": target,
                                "type": link_type})
                continue
            if add_link(peer.doc, inverse, ident):
                fixed += 1
    return fixed, orphans


def plan(read_rows, *, drop_legacy: bool) -> tuple[list[Entity], dict[str, Any]]:
    """The entities this run would write, plus the summary to print."""
    entities = _collect(read_rows)
    counts = dict.fromkeys(LEGACY_KINDS.values(), 0)
    for entity in entities.values():
        plural = LEGACY_KINDS.get(entity.kind)
        if plural and _migrate_one(entity.doc, entity.kind,
                                   drop_legacy=drop_legacy):
            counts[plural] += 1
    fixed, orphans = _reconcile_inverses(entities)
    changed = sorted((e for e in entities.values() if e.changed),
                     key=lambda e: e.ident)
    summary = {
        "migrated": counts,
        "reconcile": {"fixed": fixed, "orphans": orphans},
        "status": "no changes" if not changed
                  else f"{len(changed)} entities updated",
    }
    return changed, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy links.")
    parser.add_argument("--root", default=None,
                        help="Project root containing .taskmaster/ "
                             "(default: resolved from the current directory)")
    parser.add_argument("--keep-legacy", action="store_true",
                        help="Keep the old linkage fields alongside 'links'.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing.")
    args = parser.parse_args(argv)

    directory = _store_rows.require_backlog(args.root)
    if directory is None:
        return 2
    drop_legacy = not args.keep_legacy

    if args.dry_run:
        data = store.open_store(directory).load_dict()
        changed, summary = plan(
            lambda kind: _store_rows.rows(data, kind), drop_legacy=drop_legacy
        )
        summary["dry_run"] = True
        summary["would_write"] = [entity.ident for entity in changed]
    else:
        with store.open_store(directory).transaction(tool=TOOL) as tx:
            changed, summary = plan(tx.list, drop_legacy=drop_legacy)
            for entity in changed:
                tx.put(entity.kind, entity.ident, entity.doc, body=entity.body)
        summary["written"] = [entity.ident for entity in changed]

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
