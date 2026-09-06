# User intent: translate legacy linkage fields into typed `links` arrays once,
# inside a single store transaction, so the read that plans the migration and
# the writes that apply it see one snapshot and no concurrent edit is lost.
"""Translate legacy linkage fields -> typed `links` arrays.

One-shot and idempotent. Reads every entity through the store, converts the
legacy fields, fills in the missing inverse link on each peer, and commits the
lot in one transaction. Entities whose only change is a dropped legacy field
are written too, so a re-run has nothing left to do.

`--restore-scalars` is the reverse rescue: it rebuilds the scalar fields an
older run of this script deleted, reading them back out of the typed links.
There is exactly one writer either way. A project that has a store is repaired
through a single `tx.put` transaction, which re-exports every touched file
from the store's own renderer; a projection with no store — a pre-6.0.0
backlog nobody has opened since — is spliced on disk instead, line by line, so
adopting SQLite is never a precondition for getting a dependency graph back.

Usage:
    python -m scripts.migrate_links [--root <project_root>] [--dry-run]
    python -m scripts.migrate_links --restore-scalars [--root <r>] [--dry-run]
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

import yaml  # noqa: E402

from scripts import _store_rows  # noqa: E402
from taskmaster import store  # noqa: E402
from taskmaster.taskmaster_v3 import (  # noqa: E402
    _LEGACY_FIELDS_TO_DROP,
    REVERSE_TYPE,
    add_link,
    entity_links,
    legacy_links_to_typed,
    parse_frontmatter,
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


# ── Scalar restore ─────────────────────────────────────────────────────────
#
# Recovery for backlogs migrated before 6.0.1, when this script still deleted
# fields the server reads. The typed links survived, so the scalars can be read
# back out of them. The edit is a line splice rather than a YAML round-trip: a
# re-dump would reflow quoting, folding and key order across a 50k-line
# backlog, and a rescue tool that rewrites the whole file is a worse risk than
# the bug it repairs.

# `{kind: ((scalar_field, link_type, value_is_list), ...)}` — the inverse of
# `_LEGACY_LINK_RULES`, restricted to the fields the server reads back.
# `related_issues` and `related_tasks` are display-only and stay dropped.
RESTORE_RULES: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "task":  (("depends_on", "depends_on", True),),
    "issue": (("fixed_in_task", "fixed_in_task", False),
              ("duplicate_of", "duplicate_of", False)),
}

_TOP_KEY_RE = re.compile(r"^[A-Za-z_][\w-]*:")
_ITEM_ID_RE = re.compile(r"^(\s*)- id: (\S+)\s*$")
_FENCE = "---"


def scalar_restore_plan(doc: dict[str, Any], kind: str) -> dict[str, Any]:
    """`{field: value}` this entity is missing, read out of its typed links.

    An existing non-empty scalar always wins: the file is the record the server
    has been reading, and a link that disagrees with it is drift to report, not
    to resolve by overwriting live data.
    """
    plan: dict[str, Any] = {}
    links = entity_links(doc)
    for name, link_type, is_list in RESTORE_RULES.get(kind, ()):
        current = doc.get(name)
        if current not in (None, "", [], {}):
            continue
        targets = [link["target"] for link in links
                   if link.get("type") == link_type and link.get("target")]
        if not targets:
            continue
        plan[name] = targets if is_list else targets[0]
    return plan


def _eol(line: str) -> str:
    """The line terminator of `line`, defaulting to LF for an unterminated one."""
    return line[len(line.rstrip("\r\n")):] or "\n"


def _render_field(name: str, value: Any, indent: str, eol: str) -> list[str]:
    """`name: value` as physical lines, indented and terminated to match."""
    dumped = yaml.safe_dump({name: value}, default_flow_style=False,
                            sort_keys=False, allow_unicode=True)
    return [f"{indent}{line}{eol}" for line in dumped.rstrip("\n").split("\n")]


def _insertion_point(lines: list[str], start: int, stop: int, indent: str) -> int:
    """Where a new key goes in the frontmatter: before its `links:` key.

    Every entity this mode touches has a `links:` key — that is where the value
    is read from — so the anchor exists, and the restored field lands next to
    the links it came from. Frontmatter is small and machine-rendered, so a
    continuation line can never masquerade as the key: any wrapped scalar is
    indented, and this matches only at column zero.
    """
    for i in range(start, stop):
        if lines[i].rstrip("\r\n") == f"{indent}links:":
            return i
    return stop


def _apply_edits(lines: list[str], edits: list[tuple[int, list[str]]]) -> str:
    """Splice `(index, new_lines)` insertions, later ones first."""
    out = list(lines)
    for at, new_lines in sorted(edits, reverse=True):
        out[at:at] = new_lines
    return "".join(out)


def _verify_backlog(new_text: str, before: dict, plans: dict[str, dict]) -> None:
    """Re-parse and prove the splice added the plan and nothing else.

    Byte surgery on a generated file is only safe if it is checked, so the
    write is gated on the result parsing back to exactly the old data plus the
    planned keys. A mismatch aborts before anything reaches disk.
    """
    expected = copy.deepcopy(before)
    for epic in expected.get("epics") or []:
        for task in (epic or {}).get("tasks") or []:
            if isinstance(task, dict) and str(task.get("id")) in plans:
                task.update(plans[str(task.get("id"))])
    if yaml.safe_load(new_text) != expected:
        raise RuntimeError("restore would have changed more than the scalars")


def restore_backlog_text(text: str) -> tuple[str, list[str]]:
    """Splice the missing task scalars into `backlog.yaml`. `(new_text, ids)`."""
    data = yaml.safe_load(text) or {}
    plans: dict[str, dict[str, Any]] = {}
    for epic in data.get("epics") or []:
        for task in (epic or {}).get("tasks") or []:
            if not isinstance(task, dict):
                continue
            plan = scalar_restore_plan(task, "task")
            if plan:
                plans[str(task.get("id"))] = plan
    if not plans:
        return text, []

    lines = text.splitlines(keepends=True)
    # The `epics:` block only — `context.in_progress`, the issue index and the
    # bug index all carry `- id:` items too, and none of them own task scalars.
    start = end = None
    for i, line in enumerate(lines):
        if start is None:
            if line.rstrip("\r\n") == "epics:":
                start = i
            continue
        if _TOP_KEY_RE.match(line):
            end = i
            break
    if start is None:
        return text, []
    if end is None:
        end = len(lines)

    # Anchor on the item's own `- id:` line and insert straight after it.
    # Scanning forward for a friendlier anchor is not safe here: a task's
    # `tldr` or `next_step` is often a multi-line quoted scalar carrying blank
    # lines and arbitrary text, so neither "the block ends at the first line
    # that dedents" nor "insert before the `links:` line" survives real data.
    # Key order inside a mapping carries no meaning, so second place is fine.
    edits: list[tuple[int, list[str]]] = []
    done: set[str] = set()
    for i in range(start + 1, end):
        match = _ITEM_ID_RE.match(lines[i].rstrip("\r\n"))
        # Exactly two spaces: a direct child of `tasks:`. Epics sit at column
        # zero and every nested list inside a task is indented further.
        if match is None or match.group(1) != "  ":
            continue
        ident = match.group(2)
        if ident not in plans or ident in done:
            continue
        eol = _eol(lines[i])
        new_lines: list[str] = []
        for name, value in plans[ident].items():
            new_lines += _render_field(name, value, "    ", eol)
        edits.append((i + 1, new_lines))
        done.add(ident)

    new_text = _apply_edits(lines, edits)
    _verify_backlog(new_text, data, plans)
    return new_text, sorted(done)


def restore_entity_text(text: str, kind: str) -> tuple[str, dict[str, Any]]:
    """Splice the missing scalars into one markdown file. `(new_text, plan)`.

    Serves both `issues/<id>.md` and the v4 `tasks/<id>.md` shard: they are the
    same document shape, so the only thing that differs is which fields the
    kind owns.
    """
    frontmatter, body = parse_frontmatter(text)
    plan = scalar_restore_plan(frontmatter, kind)
    if not plan:
        return text, {}

    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != _FENCE:
        return text, {}
    close = next((i for i in range(1, len(lines))
                  if lines[i].rstrip("\r\n") == _FENCE), None)
    if close is None:
        return text, {}

    at = _insertion_point(lines, 1, close, "")
    eol = _eol(lines[at - 1])
    new_lines: list[str] = []
    for name, value in plan.items():
        new_lines += _render_field(name, value, "", eol)
    new_text = _apply_edits(lines, [(at, new_lines)])

    got_fm, got_body = parse_frontmatter(new_text)
    if got_fm != {**frontmatter, **plan} or got_body != body:
        raise RuntimeError("restore would have changed more than the scalars")
    return new_text, plan


def _restore_summary(touched: dict[str, set[str]], *, via: str,
                     dry_run: bool) -> dict[str, Any]:
    """The one summary shape both restore paths print, so runs compare."""
    names = sorted(touched["tasks"] | touched["issues"])
    summary: dict[str, Any] = {
        "via": via,
        "restored": {kind: len(ids) for kind, ids in touched.items()},
        "status": "no changes" if not names
                  else f"{len(names)} entities updated",
    }
    summary["would_write" if dry_run else "written"] = names
    if dry_run:
        summary["dry_run"] = True
    return summary


def restore_plan_rows(read_rows) -> list[Entity]:
    """The entities a store-backed restore would write, from one snapshot.

    Archived rows are included deliberately. The old migration stripped them
    too, and a task that comes back from the archive with no `depends_on` is
    the same silent unblocking this repairs — just deferred until someone
    unarchives it.
    """
    changed: list[Entity] = []
    for kind in ("task", "issue"):
        for ident, doc, body in read_rows(kind):
            fields = scalar_restore_plan(doc, kind)
            if not fields:
                continue
            changed.append(Entity(kind=kind, ident=ident,
                                  doc={**doc, **fields}, body=body,
                                  before=dict(doc)))
    return sorted(changed, key=lambda entity: entity.ident)


def restore_scalars_in_store(directory: Path, *, dry_run: bool) -> dict[str, Any]:
    """Rebuild the dropped scalars through the store, one transaction.

    This is the path for any project that has already adopted 6.0.0, which is
    every project that has been opened once — the store bootstraps itself on
    first read. Writing through `tx.put` rather than splicing the files buys
    the writer mutex, a change-log row per entity, and a re-export that
    rewrites each `tasks/<id>.md` from the store's own renderer, so the
    projection cannot drift from the rows the gates actually read.
    """
    store_obj = store.open_store(directory)
    if dry_run:
        # `load_dict` filters only tombstones, so archived rows are already in
        # the snapshot; `include_archived` here keeps `rows` from dropping
        # them again and makes the dry run count what the write run writes.
        data = store_obj.load_dict()

        def read_rows(kind):
            return _store_rows.rows(data, kind, include_archived=True)

        changed = restore_plan_rows(read_rows)
    else:
        with store_obj.transaction(tool=TOOL) as tx:
            def read_rows(kind):
                return tx.list(kind, include_archived=True)

            changed = restore_plan_rows(read_rows)
            for entity in changed:
                tx.put(entity.kind, entity.ident, entity.doc, body=entity.body)

    touched: dict[str, set[str]] = {"tasks": set(), "issues": set()}
    for entity in changed:
        touched[f"{entity.kind}s"].add(entity.ident)
    return _restore_summary(touched, via="store", dry_run=dry_run)


def restore_scalars_in_files(directory: Path, *, dry_run: bool) -> dict[str, Any]:
    """Rebuild the dropped scalars across one storeless file projection.

    Both task layouts are swept, and neither is detected from the schema
    version: a task carries its `links` wherever its own layout puts them — in
    the `backlog.yaml` epic tree under v3, in a `tasks/<id>.md` shard under v4
    — and each site is repaired only where a link exists and its scalar does
    not. Sweeping both keeps a half-migrated project consistent for free.
    """
    # Defence in depth. `main` routes a project with a store to the store
    # path, and this restates the invariant next to the raw writes it guards:
    # nothing may splice the projection behind a live store's back.
    if (directory / "local" / "store.db").exists():
        raise RuntimeError("a store exists; restore through the store instead")

    touched: dict[str, set[str]] = {"tasks": set(), "issues": set()}

    # Bytes throughout: text mode would translate line endings on Windows and
    # rewrite every line of a file this mode is meant to leave alone.
    backlog = directory / "backlog.yaml"
    new_text, ids = restore_backlog_text(backlog.read_bytes().decode("utf-8"))
    if ids:
        touched["tasks"].update(ids)
        if not dry_run:
            backlog.write_bytes(new_text.encode("utf-8"))

    # `archive/` too: the old migration stripped archived entities as well, and
    # an unarchived task with no `depends_on` is the same bug arriving late.
    for kind, subdir in (("task", "tasks"), ("issue", "issues")):
        paths = sorted((directory / subdir).glob("*.md")) + \
                sorted((directory / subdir / "archive").glob("*.md"))
        for path in paths:
            new_text, plan = restore_entity_text(
                path.read_bytes().decode("utf-8"), kind)
            if not plan:
                continue
            touched[f"{kind}s"].add(path.stem)
            if not dry_run:
                path.write_bytes(new_text.encode("utf-8"))

    return _restore_summary(touched, via="files", dry_run=dry_run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy links.")
    parser.add_argument("--root", default=None,
                        help="Project root containing .taskmaster/ "
                             "(default: resolved from the current directory)")
    parser.add_argument("--keep-legacy", action="store_true",
                        help="Keep the old linkage fields alongside 'links'.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing.")
    parser.add_argument("--restore-scalars", action="store_true",
                        help="Recovery mode: rebuild task.depends_on, "
                             "issue.fixed_in_task and issue.duplicate_of from "
                             "the typed links where the scalar is missing. "
                             "Writes through the store when one exists and "
                             "splices the files when none does.")
    args = parser.parse_args(argv)

    directory = _store_rows.require_backlog(args.root)
    if directory is None:
        return 2

    if args.restore_scalars:
        # Never two writers. A project that has a store is repaired through
        # it; only a projection with no store is edited on disk.
        restore = (restore_scalars_in_store
                   if (directory / "local" / "store.db").exists()
                   else restore_scalars_in_files)
        print(json.dumps(restore(directory, dry_run=args.dry_run), indent=2))
        return 0

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
