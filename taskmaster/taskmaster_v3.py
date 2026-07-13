"""Taskmaster v3 — narrative-continuity layout helpers.

This module is intentionally framework-free (no fastmcp imports) so it can be
tested in isolation. It owns:

- Schema version constants and detection.
- Atomic file writes.
- (Future slices) Frontmatter parsing, per-task file I/O, v3 load/save, migration.

`backlog_server.py` re-exports the symbols it needs from here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

# The plugin's own source root (repo root — this module lives in the
# taskmaster/ package, one level down). Used by _resolve_artifact_root()'s
# guard (tm-audit-001) to refuse treating this directory as a project root.
_PLUGIN_DIR = Path(__file__).resolve().parent.parent

# Schema versions
# v2: single backlog.yaml with epics/tasks inline. (Legacy: missing version implies v2.)
# v3: slim backlog.yaml index + per-task files in tasks/ + handovers/ + issues/.
SCHEMA_V2 = 2
SCHEMA_V3 = 3
SCHEMA_V4 = 4
SCHEMA_DEFAULT = SCHEMA_V2  # what new backlogs get unless v3 is explicitly requested


def detect_schema_version(data: dict) -> int:
    """Return the schema version of a loaded backlog dict.

    Missing version implies v2 (legacy). v3 backlogs are required to declare
    `meta.schema_version: 3` explicitly.
    """
    return int(data.get("meta", {}).get("schema_version", SCHEMA_V2))


def atomic_write(path: Path, content: str) -> None:
    """Write file atomically: write to tmp + rename. Prevents corruption on crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


# ── tldr extraction ───────────────────────────────────────────

TLDR_MAX_CHARS = 200

# Canonical section names per entity type.
# For tasks: 'notes' and 'review_instructions' are inline frontmatter fields;
# 'spec'/'plan'/'design'/'analysis'/'roadmap' are resolved from task.docs.<key>
# (external file paths in the docs dict).
CANONICAL_SECTIONS: dict[str, tuple[str, ...]] = {
    "task": ("notes", "review_instructions", "spec", "plan", "design", "analysis", "roadmap"),
    "handover": ("decisions", "notes", "blockers", "where_id_start"),
    "issue": ("repro", "investigation", "notes"),
    "epic": ("notes", "design", "spec", "roadmap", "analysis"),
    "phase": ("notes", "design", "roadmap"),
}

TASK_INLINE_SECTIONS: frozenset[str] = frozenset({"notes", "review_instructions"})
TASK_DOC_SECTIONS: frozenset[str] = frozenset({"spec", "plan", "design", "analysis", "roadmap"})

_HEADING_RE = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE_RE = re.compile(r"\s+")


def extract_tldr(body: str | None) -> str | None:
    """Extract a tldr from markdown body text.

    Strategy: strip markdown headings, collapse whitespace, take the first
    sentence (split on .!?), cap at TLDR_MAX_CHARS with an ellipsis if needed.
    Returns None if the body is empty or all whitespace.
    """
    if not body or not body.strip():
        return None
    text = _HEADING_RE.sub("", body).strip()
    text = _WHITESPACE_RE.sub(" ", text)
    if not text:
        return None
    parts = _SENTENCE_END_RE.split(text, maxsplit=1)
    first = parts[0].strip()
    if len(first) > TLDR_MAX_CHARS:
        first = first[: TLDR_MAX_CHARS - 1].rstrip() + "…"
    return first or None


def backfill_tldr(frontmatter: dict[str, Any], body: str = "") -> tuple[dict[str, Any], bool]:
    """If frontmatter lacks a tldr, generate one and mark tldr_autogen=True.

    Returns (frontmatter, changed). Source priority: body → title. Never overwrites
    an existing tldr. Idempotent.
    """
    if frontmatter.get("tldr"):
        return frontmatter, False
    new_fm = dict(frontmatter)
    tldr = extract_tldr(body) or (frontmatter.get("title") or "")[:TLDR_MAX_CHARS]
    if not tldr:
        return frontmatter, False
    new_fm["tldr"] = tldr
    new_fm["tldr_autogen"] = True
    return new_fm, True


SLIM_FIELDS: dict[str, tuple[str, ...]] = {
    "task": (
        "id", "title", "tldr", "next_step", "status", "priority",
        "estimate", "phase", "epic", "bundle", "component", "design_change",
        "lane", "gate_state", "area",
        "skip_merge_gate", "merge_gate_freshness", "merge_gate_state",
        "depends_on", "related_issues",
        "started", "completed", "branch", "worktree",
        "blockers", "open_handovers",
        "tldr_autogen",
    ),
    "issue": (
        "id", "title", "tldr", "severity", "status", "components",
        "impact", "location", "related_tasks", "fixed_in_task",
        "duplicate_of", "discovered", "resolved", "tldr_autogen",
    ),
    "handover": (
        "id", "tldr", "next_action", "task_ids", "session_kind",
        "status", "status_changed", "status_reason",
        "created", "supersedes", "superseded_by", "flag_for_review",
        "flag_reason",
        "tldr_autogen",
    ),
    "idea": (
        "id", "title", "tldr", "status", "tags",
        "created_by", "related_tasks", "related_issues",
        "tldr_autogen",
    ),
    "note": ("id", "author", "created", "pinned", "archived"),
    "epic": ("id", "name", "status", "design_status", "created", "done_when", "area"),
    "phase": ("id", "name", "status", "order", "created",
              "target_date", "start_date", "completed", "deliverables"),
}


def slim_entity(
    entity: dict[str, Any],
    kind: str,
    *,
    open_handovers: list[str] | None = None,
) -> dict[str, Any]:
    """Return the slim view of an entity dict."""
    if kind not in SLIM_FIELDS:
        raise ValueError(f"Unknown entity kind: {kind!r}")
    out: dict[str, Any] = {}
    for key in SLIM_FIELDS[kind]:
        if key == "open_handovers":
            continue
        if key in entity and entity[key] not in (None, "", [], {}):
            out[key] = entity[key]
    if kind == "task":
        docs = entity.get("docs") or {}
        if docs:
            out["docs_available"] = sorted(docs.keys())
        if open_handovers:
            out["open_handovers"] = open_handovers
    return out


_SECTION_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _split_body_by_heading(body: str) -> dict[str, str]:
    """Split a markdown body into sections keyed by slugified heading text.

    Slugification: strip punctuation (apostrophes, commas, etc.), lowercase,
    replace spaces with underscores.  This matches canonical section names like
    ``where_id_start`` to heading text like ``## Where I'd start``.
    """
    if not body:
        return {}
    matches = list(_SECTION_HEADING_RE.finditer(body))
    if not matches:
        return {}
    out: dict[str, str] = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        key = re.sub(r"[^\w\s]", "", m.group(1)).strip().lower().replace(" ", "_")
        out[key] = body[start:end].strip()
    return out


def resolve_sections(
    entity: dict[str, Any],
    *,
    kind: str,
    sections: list[str],
    body: str,
    project_root: Path | None = None,
) -> dict[str, str]:
    """Return a dict mapping section name → content for requested sections."""
    canon = CANONICAL_SECTIONS.get(kind, ())
    for s in sections:
        if s not in canon:
            raise ValueError(f"{s!r} is not a canonical section for kind={kind!r}")

    out: dict[str, str] = {}

    if kind == "task":
        for s in sections:
            if s in TASK_INLINE_SECTIONS:
                v = entity.get(s)
                if v:
                    out[s] = v if isinstance(v, str) else str(v)
            elif s in TASK_DOC_SECTIONS:
                doc_path = (entity.get("docs") or {}).get(s)
                if not doc_path:
                    continue
                resolved = (project_root / doc_path) if project_root else Path(doc_path)
                if resolved.exists():
                    out[s] = resolved.read_text(encoding="utf-8")
                else:
                    out[s] = f"(unresolved: {doc_path})"
        return out

    body_sections = _split_body_by_heading(body)
    for s in sections:
        if s in body_sections:
            out[s] = body_sections[s]
    return out


def expand_link_ids(
    ids: list[str] | dict[str, list[str]],
    tldr_index: dict[str, str],
) -> list[dict[str, str | None]] | dict[str, list[dict[str, str | None]]]:
    """Expand bare ID arrays into {id, tldr} pills.

    Accepts either a flat list (returns list) or a grouped dict (returns dict).
    Unknown IDs get tldr=None.
    """
    if isinstance(ids, dict):
        return {key: expand_link_ids(vals, tldr_index) for key, vals in ids.items()}  # type: ignore[return-value]
    return [{"id": i, "tldr": tldr_index.get(i)} for i in ids]


def build_tldr_index(data: dict[str, Any], project_root: Path | None = None) -> dict[str, str]:
    """Build {entity_id → tldr} index across tasks, issues, handovers, ideas."""
    idx: dict[str, str] = {}
    for epic in data.get("epics", []):
        for task in epic.get("tasks", []):
            tid = task.get("id")
            if tid and task.get("tldr"):
                idx[tid] = task["tldr"]
    if project_root is None:
        return idx
    tm_dir = project_root / ".taskmaster"
    for subdir in ("tasks", "issues", "handovers", "ideas"):
        d = tm_dir / subdir
        if not d.exists():
            continue
        for path in sorted(d.glob("*.md")):
            try:
                fm, _ = read_task_file(path)
                eid = fm.get("id")
                if eid and fm.get("tldr"):
                    idx[eid] = fm["tldr"]
            except Exception:
                continue
    return idx


_legacy_warned: set[str] = set()


def warn_legacy_layout(detail: str) -> None:
    """Emit a one-shot deprecation warning for `.claude/`-layout projects.

    Each detail tag fires once per process so a long-running server doesn't
    spam stderr. The warning points at `backlog_canonicalize_layout`, the
    existing migrator that moves the layout into canonical `.taskmaster/`.
    """
    import sys
    if detail in _legacy_warned:
        return
    _legacy_warned.add(detail)
    sys.stderr.write(
        f"taskmaster: deprecated {detail} — run `backlog_canonicalize_layout` "
        f"to migrate; .claude/ support will be removed in a future release.\n"
    )


def _resolve_artifact_root() -> Path:
    """Resolve the parent directory of backlog.yaml (and its artifact subdirs)
    from CWD, using the same priority chain as `backlog_server._resolve_paths()`.

    Why this exists (ISS-004): the writer functions take `backlog_path` and use
    `bp.parent / "<artifact>"`. The CWD-flavor reader functions (`load_issue`,
    `list_sessions`, etc.) used to hard-code
    `Path(".taskmaster") / "<artifact>"` — silently diverging from the writer
    on `.claude/`-layout and root-layout projects. This helper returns the same
    parent dir the writer's resolver would, so readers and writers agree.

    Resolution order: `.taskmaster/` → `.claude/` (legacy, with warning)
    → project root → fallback `.taskmaster/`.

    Guard (tm-audit-001, unconditional — checked before any fallback branch
    so it can't go dead if a later branch starts matching first): refuse to
    resolve when cwd is literally the plugin's own source directory. A
    backlog.yaml or .taskmaster/ found there is a fixture, not a project.
    """
    if Path.cwd().resolve(strict=False) == _PLUGIN_DIR.resolve(strict=False):
        raise RuntimeError(
            "Refusing to use the taskmaster plugin directory as a project root. "
            "A backlog.yaml adjacent to backlog_server.py is a fixture, not a "
            "project. Run from a project directory or set TASKMASTER_ROOT."
        )
    cwd = Path.cwd()
    if (cwd / ".taskmaster" / "backlog.yaml").exists():
        return cwd / ".taskmaster"
    if (cwd / ".claude" / "backlog.yaml").exists():
        warn_legacy_layout("artifact root at .claude/")
        return cwd / ".claude"
    if (cwd / "backlog.yaml").exists():
        return cwd
    return cwd / ".taskmaster"


# ── Markdown + YAML frontmatter ─────────────────────────────────

_FRONTMATTER_FENCE = "---"

# Fast regex form of the frontmatter split, used by the handover/session
# readers (`list_sessions`, `_load_handover_full`) that only need the YAML block.
_MD_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown document into (frontmatter dict, body str).

    A document with frontmatter looks like:

        ---
        key: value
        ---
        body text

    The opening fence must be the very first line. The closing fence is the
    next line that consists of exactly `---`. Everything after the closing
    fence is the body (leading newline stripped).

    Documents without frontmatter return ({}, original_text). Empty or
    whitespace-only frontmatter returns ({}, body). CRLF line endings are
    normalized to LF on the body side.
    """
    if not text:
        return {}, ""

    # Normalize line endings so split is consistent on Windows.
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith(_FRONTMATTER_FENCE + "\n") and normalized != _FRONTMATTER_FENCE:
        return {}, normalized

    # Find closing fence
    lines = normalized.split("\n")
    if lines[0] != _FRONTMATTER_FENCE:
        return {}, normalized
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i] == _FRONTMATTER_FENCE:
            close_idx = i
            break
    if close_idx is None:
        # No closing fence — treat whole thing as body.
        return {}, normalized

    fm_text = "\n".join(lines[1:close_idx])
    body = "\n".join(lines[close_idx + 1 :])
    # Drop a single leading newline that often follows the closing fence.
    if body.startswith("\n"):
        body = body[1:]

    if not fm_text.strip():
        return {}, body

    parsed = yaml.safe_load(fm_text) or {}
    if not isinstance(parsed, dict):
        # Frontmatter must be a mapping; anything else is malformed input.
        raise ValueError("Frontmatter must be a YAML mapping")
    return parsed, body


def render_frontmatter(frontmatter: dict[str, Any], body: str) -> str:
    """Render a (frontmatter, body) pair as a markdown document.

    Empty frontmatter dict produces a body-only document (no fences).
    Body is normalized to end with exactly one trailing newline, and any
    leading newlines are stripped — `parse_frontmatter` drops one leading
    newline after the closing fence, so stripping here keeps the
    render->parse->render round-trip idempotent (no spurious disk diffs).
    """
    body_norm = body.strip("\n") + "\n" if body else ""
    if not frontmatter:
        return body_norm
    fm_text = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False, allow_unicode=True)
    fm_text = fm_text.rstrip("\n")
    return f"{_FRONTMATTER_FENCE}\n{fm_text}\n{_FRONTMATTER_FENCE}\n{body_norm}"


# ── Task file I/O ──────────────────────────────────────────────


def read_task_file(path: Path) -> tuple[dict[str, Any], str]:
    """Read a v3 task file at `path`. Returns (frontmatter, body)."""
    return parse_frontmatter(path.read_text(encoding="utf-8"))


def write_task_file(path: Path, frontmatter: dict[str, Any], body: str) -> None:
    """Write a v3 task file atomically."""
    atomic_write(path, render_frontmatter(frontmatter, body))


# ── v3 layout: load + save ─────────────────────────────────────

# Fields that move OUT of backlog.yaml's task entries into per-task files
# (frontmatter). Everything else stays in the slim index.
HEAVY_FIELDS: tuple[str, ...] = (
    "description",
    "notes",
    "docs",
    "review_instructions",
    "gates",
    "merge_status",
)

EPIC_HEAVY_FIELDS: tuple[str, ...] = ("description", "docs", "components")
PHASE_HEAVY_FIELDS: tuple[str, ...] = ("description", "docs")

# Special key on in-memory task dicts holding the markdown body of the
# per-task file (the prose sections written by users / skills). Not persisted
# to backlog.yaml; survives load/save roundtrip.
BODY_KEY = "_body"


# --- Spec A: lanes & gates -------------------------------------------------
VALID_LANES = ("full", "standard", "express")

LANE_GATES = {
    "full":     ("spec", "spec-review", "plan", "plan-review", "tests", "impl", "review-gate"),
    "standard": ("spec", "design-review", "tests", "impl", "review-gate"),
    "express":  ("impl", "review-gate"),
}

STATUS_GATES  = ("spec", "plan", "tests", "impl")
VERDICT_GATES = ("spec-review", "plan-review", "design-review", "review-gate")
VALID_GATES   = STATUS_GATES + VERDICT_GATES
VALID_GATE_VERDICTS = ("pass", "warn", "fail")

_HIGH_STAKES_PRIORITIES = ("critical", "high")


def default_lane(priority: str) -> str:
    """Lane assigned on task creation. high/critical earn full ceremony; else standard."""
    return "full" if (priority or "") in _HIGH_STAKES_PRIORITIES else "standard"


def required_gates(lane):
    """Ordered required gates for a lane. Laneless/unknown -> () (no pipeline, exempt)."""
    return LANE_GATES.get(lane or "", ())


def gate_satisfied(rec) -> bool:
    """A gate counts toward completion if skipped, done, or verdict==pass. warn/fail do not."""
    if not rec:
        return False
    if rec.get("skipped"):
        return True
    if rec.get("status") == "done":
        return True
    if rec.get("verdict") == "pass":
        return True
    return False


def blocking_gates(lane):
    """Gates that BLOCK completion for a lane = its review/verdict gates, in order.
    Status gates (spec/plan/tests/impl) are progress markers, not blockers."""
    return tuple(g for g in required_gates(lane) if g in VERDICT_GATES)


def outstanding_required_gates(task) -> list:
    """Blocking (review/verdict) gates for the task's lane that are not yet satisfied.
    Status gates are informational progress markers and never block completion."""
    gates = task.get("gates") or {}
    return [g for g in blocking_gates(task.get("lane")) if not gate_satisfied(gates.get(g))]


def compute_gate_state(task) -> str:
    """One-line slim mirror of pipeline position. '' for laneless tasks.
    Walks blocking gates only: status gates are progress markers, not pipeline blockers."""
    lane = task.get("lane")
    blk = blocking_gates(lane)
    if not blk:
        return ""
    gates = task.get("gates") or {}
    for g in blk:
        rec = gates.get(g)
        if rec and rec.get("verdict") == "fail":
            return f"blocked@{g}"
    for g in blk:
        if not gate_satisfied(gates.get(g)):
            return f"{g}:pending"
    last = blk[-1]
    rec = gates.get(last) or {}
    if rec.get("skipped"):
        outcome = "skipped"
    else:
        outcome = rec.get("verdict") or rec.get("status") or "done"
    return f"{last}:{outcome}"


# --- Spec B: merge ladder ---------------------------------------------------
def merge_rungs(merge_targets) -> tuple:
    """Ordered rung labels from a resolved merge_targets list[dict]. () if none."""
    return tuple((r.get("label") or "") for r in (merge_targets or []) if r.get("label"))


def rung_for_branch(branch, merge_targets):
    """Map a branch name to its rung label by string match across aliases. None if unmatched."""
    if not branch:
        return None
    for r in (merge_targets or []):
        if branch in (r.get("branches") or []) or branch == r.get("label"):
            return r.get("label")
    return None


def compute_merge_gate_state(task, merge_targets) -> str:
    """Slim mirror: highest rung in the ladder for which merge_status is recorded. '' if none."""
    status = task.get("merge_status") or {}
    if not status:
        return ""
    reached = ""
    for label in merge_rungs(merge_targets):
        if label in status:
            reached = label
    return reached


# ── Typed links (Plan C / spec §6) ────────────────────────────

# Canonical link types and their inverses. Every link written on the
# source side has a corresponding inverse written on the target side
# by sync_inverse(). relates_to is its own inverse.
REVERSE_TYPE: dict[str, str] = {
    "depends_on":    "blocks",
    "blocks":        "depends_on",
    "fixes":         "fixed_in_task",
    "fixed_in_task": "fixes",
    "relates_to":    "relates_to",
    "supersedes":    "superseded_by",
    "superseded_by": "supersedes",
    "duplicate_of":  "duplicates",
    "duplicates":    "duplicate_of",
    "references":    "referenced_by",
    "referenced_by": "references",
}
LINK_TYPES: tuple[str, ...] = tuple(REVERSE_TYPE.keys())

# Entity-kind dispatch by ID prefix. Longest prefix wins (IDEA before I-).
ENTITY_KIND_BY_PREFIX: dict[str, str] = {
    "T":    "task",
    "ISS":  "issue",
    "HND":  "handover",
    "IDEA": "idea",
}
# Order longest-first for prefix matching.
_PREFIX_ORDER: tuple[str, ...] = ("IDEA", "ISS", "HND", "T")

# (source_kind, target_kind) pairs allowed per link type. "*" = any.
LINK_TYPE_DOMAIN: dict[str, tuple[str, str]] = {
    "depends_on":    ("task",     "task"),
    "blocks":        ("task",     "task"),
    "fixes":         ("task",     "issue"),
    "fixed_in_task": ("issue",    "task"),
    "supersedes":    ("handover", "handover"),
    "superseded_by": ("handover", "handover"),
    "duplicate_of":  ("issue",    "issue"),
    "duplicates":    ("issue",    "issue"),
    "relates_to":    ("*",        "*"),
    "references":    ("*",        "*"),
    "referenced_by": ("*",        "*"),
}


_HANDOVER_DATE_SLUG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9\-]+$")


def entity_kind_of(entity_id: str | None) -> str | None:
    """Map an entity ID to its kind, or None if unknown.

    Recognizes:
      - HND-NNN, T-NNN, ISS-NNN, IDEA-NNN prefixed IDs
      - Date-slug handover IDs (YYYY-MM-DD-<slug>) — the actual on-disk format
    """
    if not entity_id or not isinstance(entity_id, str):
        return None
    for prefix in _PREFIX_ORDER:
        if entity_id.startswith(prefix + "-"):
            return ENTITY_KIND_BY_PREFIX[prefix]
    # Date-slug handover IDs (the production handover format).
    if _HANDOVER_DATE_SLUG_RE.match(entity_id):
        return "handover"
    return None


def is_valid_link(link_type: str, source_kind: str, target_kind: str) -> bool:
    """Return True if a link of `link_type` may go from source_kind to target_kind."""
    if link_type not in LINK_TYPE_DOMAIN:
        return False
    expected_src, expected_dst = LINK_TYPE_DOMAIN[link_type]
    if expected_src != "*" and expected_src != source_kind:
        return False
    if expected_dst != "*" and expected_dst != target_kind:
        return False
    return True


LINK_FIELD: str = "links"


def entity_links(entity: dict) -> list[dict]:
    """Return a shallow copy of the entity's links array (empty list if absent)."""
    raw = entity.get(LINK_FIELD) or []
    return [dict(link) for link in raw]


def set_entity_links(entity: dict, links: list[dict]) -> None:
    """Replace the entity's links array, dropping the field when empty."""
    if not links:
        entity.pop(LINK_FIELD, None)
    else:
        entity[LINK_FIELD] = [dict(link) for link in links]


def add_link(entity: dict, link_type: str, target: str) -> bool:
    """Idempotently add a {type, target} entry. Returns True if added, False if dup."""
    current = entity_links(entity)
    needle = {"type": link_type, "target": target}
    if needle in current:
        return False
    current.append(needle)
    set_entity_links(entity, current)
    return True


def remove_link(entity: dict, link_type: str, target: str) -> bool:
    """Remove a single {type, target} entry. Returns True if removed, False if absent."""
    current = entity_links(entity)
    needle = {"type": link_type, "target": target}
    if needle not in current:
        return False
    current.remove(needle)
    set_entity_links(entity, current)
    return True


def links_grouped_by_type(entity: dict) -> dict[str, list[str]]:
    """Return {type: [target_id, ...]} grouped view. Used by slim-view rendering."""
    grouped: dict[str, list[str]] = {}
    for link in entity_links(entity):
        grouped.setdefault(link["type"], []).append(link["target"])
    return grouped


def find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """Return a closed-loop cycle (first node repeated at end) or None.

    `graph` is a dict {source_id: [target_id, ...]} representing depends_on
    edges. DFS-based detection with grey/black coloring: returns the first
    cycle found.

    Algorithm: For each unvisited node, perform iterative DFS using a stack.
    Mark nodes WHITE (unvisited), GRAY (in current DFS path), BLACK (done).
    Encountering a GRAY neighbor means we've found a back-edge → cycle.
    Reconstruct the cycle by walking parent pointers from current node back
    to the GRAY ancestor.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {node: WHITE for node in graph}
    parent: dict[str, str | None] = {node: None for node in graph}

    def dfs(start: str) -> list[str] | None:
        stack: list[tuple[str, int]] = [(start, 0)]
        color[start] = GRAY
        while stack:
            node, idx = stack[-1]
            neighbors = graph.get(node, [])
            if idx >= len(neighbors):
                color[node] = BLACK
                stack.pop()
                continue
            stack[-1] = (node, idx + 1)
            nxt = neighbors[idx]
            if color.get(nxt, WHITE) == GRAY:
                # Self-edge: nxt == node and nxt is GRAY.
                if nxt == node:
                    return [node, node]
                # Build cycle by walking parents from `node` back to `nxt`.
                cycle = [nxt]
                cur: str | None = node
                while cur is not None and cur != nxt:
                    cycle.append(cur)
                    cur = parent.get(cur)
                cycle.append(nxt)
                cycle.reverse()
                return cycle
            if color.get(nxt, WHITE) == WHITE:
                color[nxt] = GRAY
                parent[nxt] = node
                stack.append((nxt, 0))
        return None

    for node in list(graph.keys()):
        if color[node] == WHITE:
            found = dfs(node)
            if found:
                return found
    return None


def would_create_cycle(graph: dict[str, list[str]], source: str, target: str) -> bool:
    """Return True if adding `source -> target` to `graph` introduces a cycle."""
    if source == target:
        return True
    augmented = {node: list(targets) for node, targets in graph.items()}
    augmented.setdefault(source, []).append(target)
    augmented.setdefault(target, augmented.get(target, []))
    return find_cycle(augmented) is not None


# Match a known prefix (IDEA|ISS|HND|T|L) followed by '-' and 1+ digits,
# optionally wrapped in [[...]] or preceded by '@'. Anchored on a non-word
# boundary on the left so "noT-001" doesn't match.
_INLINE_REF_RE = re.compile(
    r"(?:(?<=^)|(?<=[^A-Za-z0-9_]))"               # left boundary
    r"(?:\[\[|@)?"                                  # optional [[ or @
    r"(IDEA-\d+|ISS-\d+|HND-\d+|T-\d+|\d{4}-\d{2}-\d{2}-[a-z0-9\-]+)"  # captured ID (incl. date-slug handover)
    r"(?:\]\])?"                                    # optional ]]
)


def extract_inline_refs(body: str | None, *, self_id: str | None = None) -> list[str]:
    """Scan markdown body for entity ID mentions.

    Recognized patterns:
      - Bare: T-001, ISS-007, HND-012, IDEA-005
      - Wiki: [[T-001]]
      - Mention: @T-001

    Returns IDs in first-seen order, deduped. Excludes self_id when supplied.
    Case-sensitive — uppercase prefixes only.
    """
    if not body:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in _INLINE_REF_RE.finditer(body):
        eid = match.group(1)
        if entity_kind_of(eid) is None:
            continue
        if eid in seen:
            continue
        if self_id and eid == self_id:
            continue
        seen.add(eid)
        out.append(eid)
    return out


# Per-kind legacy field → typed link mapping.
# (field_name, link_type, value_is_list) tuples per entity kind.
_LEGACY_LINK_RULES: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "task": (
        ("depends_on",      "depends_on",  True),
        ("related_issues",  "relates_to",  True),
    ),
    "issue": (
        ("related_tasks",  "relates_to",    True),
        ("fixed_in_task",  "fixed_in_task", False),
        ("duplicate_of",   "duplicate_of",  False),
    ),
    "handover": (
        ("supersedes",     "supersedes",    True),
        ("superseded_by",  "superseded_by", True),
    ),
    "idea": (
        ("related_tasks", "relates_to", True),
    ),
}


def legacy_links_to_typed(entity: dict, kind: str) -> list[dict]:
    """Translate legacy linkage fields on `entity` into a typed `links` array.

    Existing `entity['links']` entries are preserved. The output is deduped.
    Does not mutate the entity in place — caller decides when to assign.
    """
    out: list[dict] = list(entity_links(entity))
    seen = {(link["type"], link["target"]) for link in out}
    rules = _LEGACY_LINK_RULES.get(kind, ())
    for field, link_type, is_list in rules:
        raw = entity.get(field)
        if raw is None or raw == [] or raw == "":
            continue
        targets = raw if is_list else [raw]
        for tgt in targets:
            if not tgt:
                continue
            key = (link_type, tgt)
            if key in seen:
                continue
            seen.add(key)
            out.append({"type": link_type, "target": tgt})
    return out


_LEGACY_FIELDS_TO_DROP: dict[str, tuple[str, ...]] = {
    "task":     ("depends_on", "related_issues"),
    "issue":    ("related_tasks", "fixed_in_task", "duplicate_of"),
    "handover": ("supersedes", "superseded_by"),
    "idea":     ("related_tasks",),
}


def _fallback_links_if_absent(entity: dict, kind: str) -> None:
    """If entity has no `links` array but has legacy fields, synthesize a
    virtual `links` array. Used by read_entity_anywhere for read-fallback
    on unmigrated projects. Does not write back.
    """
    if entity.get(LINK_FIELD):
        return
    synthesized = legacy_links_to_typed(entity, kind=kind)
    if synthesized:
        entity[LINK_FIELD] = synthesized


def task_file_path(backlog_path: Path, task_id: str) -> Path:
    """Resolve the per-task file path given the backlog.yaml path.

    Both live in the same parent directory (e.g. .taskmaster/), so a backlog
    at .taskmaster/backlog.yaml resolves to .taskmaster/tasks/<id>.md.
    """
    return backlog_path.parent / "tasks" / f"{task_id}.md"


def epic_file_path(backlog_path: Path, epic_id: str) -> Path:
    """Per-epic file path: .taskmaster/backlog.yaml -> .taskmaster/epics/<id>.md."""
    return backlog_path.parent / "epics" / f"{epic_id}.md"


def phase_file_path(backlog_path: Path, phase_id: str) -> Path:
    """Per-phase file path: .taskmaster/backlog.yaml -> .taskmaster/phases/<id>.md."""
    return backlog_path.parent / "phases" / f"{phase_id}.md"


def _remove_entity_file(path: Path) -> None:
    """Delete a stale per-entity body file if present (so a cleared last heavy
    field doesn't resurrect on the next load). No-op if absent."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _split_task_for_v3(task: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Split an in-memory task dict into (slim, heavy_fm, body).

    - slim: stays in backlog.yaml. Always includes id + title.
    - heavy_fm: frontmatter fields for the per-task file (id mirrored for sanity).
    - body: markdown body for the per-task file.
    """
    slim: dict[str, Any] = {}
    heavy: dict[str, Any] = {}
    body = ""
    for key, value in task.items():
        if key == BODY_KEY:
            body = value or ""
        elif key in HEAVY_FIELDS:
            if value not in (None, "", [], {}):
                heavy[key] = value
        else:
            slim[key] = value
    # Always mirror id+title into frontmatter for human readability of the file.
    if "id" in slim:
        heavy.setdefault("id", slim["id"])
    if "title" in slim:
        heavy.setdefault("title", slim["title"])
    return slim, heavy, body


def _merge_task_from_v3(slim: dict[str, Any], heavy_fm: dict[str, Any], body: str) -> dict[str, Any]:
    """Reverse of _split_task_for_v3: combine slim + frontmatter + body into a task dict."""
    merged = dict(slim)
    for key in HEAVY_FIELDS:
        if key in heavy_fm:
            merged[key] = heavy_fm[key]
    if body:
        merged[BODY_KEY] = body
    return merged


def task_v4_to_file(task: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """v4: split a whole task dict into (frontmatter, body).

    Unlike v3, NO field stays in backlog.yaml - every key except the prose
    body lives in the per-task file's frontmatter (id, title, status, epic,
    order, gates, ...). Body is the BODY_KEY value.
    """
    fm: dict[str, Any] = {}
    body = ""
    for key, value in task.items():
        if key == BODY_KEY:
            body = value or ""
        else:
            fm[key] = value
    return fm, body


def task_v4_from_file(fm: dict[str, Any], body: str) -> dict[str, Any]:
    """v4: reverse of task_v4_to_file. Body attaches under BODY_KEY only when
    non-empty (keeps bodyless tasks free of an empty _body key)."""
    task = dict(fm)
    if body:
        task[BODY_KEY] = body
    return task


def _split_entity_for_v3(
    entity: dict[str, Any], heavy_fields: tuple[str, ...]
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Generic version of _split_task_for_v3 for any entity kind.

    Returns (slim, heavy_fm, body). Mirrors id + a display title into the
    frontmatter for human readability (epics/phases use `name`, tasks `title`).
    """
    slim: dict[str, Any] = {}
    heavy: dict[str, Any] = {}
    body = ""
    for key, value in entity.items():
        if key == BODY_KEY:
            body = value or ""
        elif key in heavy_fields:
            if value not in (None, "", [], {}):
                heavy[key] = value
        else:
            slim[key] = value
    if "id" in slim:
        heavy.setdefault("id", slim["id"])
    display = slim.get("name") or slim.get("title")
    if display:
        heavy.setdefault("title", display)
    return slim, heavy, body


def _merge_entity_from_v3(
    slim: dict[str, Any], heavy_fm: dict[str, Any], body: str, heavy_fields: tuple[str, ...]
) -> dict[str, Any]:
    """Reverse of _split_entity_for_v3. Only pulls declared heavy_fields back
    (the id/title frontmatter mirror is readability-only and ignored)."""
    merged = dict(slim)
    for key in heavy_fields:
        if key in heavy_fm:
            merged[key] = heavy_fm[key]
    if body:
        merged[BODY_KEY] = body
    return merged


def load_v3(backlog_path: Path) -> dict[str, Any]:
    """Load a v3 backlog: slim index + per-task files merged.

    Returns the same dict shape as the v2 loader (epics[].tasks[] with all
    fields present in-memory), so existing read code keeps working unchanged.

    Per-task files that don't exist yet are tolerated — that task simply has
    no heavy fields (it was created in v3 mode and hasn't been edited yet).
    """
    data = yaml.safe_load(backlog_path.read_text(encoding="utf-8")) or {}
    for epic in data.get("epics", []):
        new_tasks: list[dict[str, Any]] = []
        for slim_task in epic.get("tasks", []):
            tid = slim_task.get("id")
            if not tid:
                new_tasks.append(slim_task)
                continue
            tf = task_file_path(backlog_path, tid)
            if tf.exists():
                fm, body = read_task_file(tf)
                new_tasks.append(_merge_task_from_v3(slim_task, fm, body))
            else:
                new_tasks.append(slim_task)
        epic["tasks"] = new_tasks

    # Merge per-epic heavy bodies (doc-bearing epics)
    for epic in data.get("epics", []):
        eid = epic.get("id")
        if not eid:
            continue
        ef = epic_file_path(backlog_path, eid)
        if ef.exists():
            fm, body = read_task_file(ef)
            epic_meta = {k: v for k, v in epic.items() if k != "tasks"}
            merged = _merge_entity_from_v3(epic_meta, fm, body, EPIC_HEAVY_FIELDS)
            merged["tasks"] = epic.get("tasks", [])
            epic.clear()
            epic.update(merged)

    # Merge per-phase heavy bodies
    for phase in data.get("phases", []):
        pid = phase.get("id")
        if not pid:
            continue
        pf = phase_file_path(backlog_path, pid)
        if pf.exists():
            fm, body = read_task_file(pf)
            merged = _merge_entity_from_v3(phase, fm, body, PHASE_HEAVY_FIELDS)
            phase.clear()
            phase.update(merged)

    return data


def migrate_v2_to_v3(backlog_path: Path) -> dict[str, Any]:
    """Convert a v2 backlog at `backlog_path` to v3 in place.

    - Reads the v2 single-file backlog.
    - Sets `meta.schema_version = 3`.
    - Calls save_v3, which strips heavy fields into per-task files and also
      writes per-epic (epics/<id>.md) and per-phase (phases/<id>.md) body
      files, then writes the slim index back to backlog.yaml.

    Idempotent: re-running on a v3 backlog returns a 'no-op' summary.

    Returns:
        Summary dict with keys:
          - status: "migrated" | "already_v3"
          - tasks_total: int
          - task_files_written: list[str] (relative paths) — all body files
            written, including epics/<id>.md and phases/<id>.md, not just tasks
          - schema_before / schema_after
    """
    raw = yaml.safe_load(backlog_path.read_text(encoding="utf-8")) or {}
    before = detect_schema_version(raw)
    if before >= SCHEMA_V3:
        return {
            "status": "already_v3",
            "tasks_total": sum(len(e.get("tasks", [])) for e in raw.get("epics", [])),
            "task_files_written": [],
            "schema_before": before,
            "schema_after": before,
        }

    raw.setdefault("meta", {})["schema_version"] = SCHEMA_V3

    # Determine which files will get written so we can report them. This must
    # mirror save_v3's write conditions for tasks, epics, and phases.
    files_to_write: list[Path] = []
    for epic in raw.get("epics", []):
        for task in epic.get("tasks", []):
            tid = task.get("id")
            if not tid:
                continue
            _, heavy_fm, body = _split_task_for_v3(task)
            has_heavy = any(k in heavy_fm for k in HEAVY_FIELDS) or bool(body)
            if has_heavy:
                files_to_write.append(task_file_path(backlog_path, tid))
    for epic in raw.get("epics", []):
        eid = epic.get("id")
        if eid:
            _, eheavy, ebody = _split_entity_for_v3(
                {k: v for k, v in epic.items() if k != "tasks"}, EPIC_HEAVY_FIELDS
            )
            if any(k in eheavy for k in EPIC_HEAVY_FIELDS) or ebody:
                files_to_write.append(epic_file_path(backlog_path, eid))
    for phase in raw.get("phases", []):
        pid = phase.get("id")
        if pid:
            _, pheavy, pbody = _split_entity_for_v3(phase, PHASE_HEAVY_FIELDS)
            if any(k in pheavy for k in PHASE_HEAVY_FIELDS) or pbody:
                files_to_write.append(phase_file_path(backlog_path, pid))

    save_v3(backlog_path, raw)

    return {
        "status": "migrated",
        "tasks_total": sum(len(e.get("tasks", [])) for e in raw.get("epics", [])),
        "task_files_written": [str(p.relative_to(backlog_path.parent)) for p in files_to_write],
        "schema_before": before,
        "schema_after": SCHEMA_V3,
    }


# ── v3 layout canonicalization (.claude/ or root → .taskmaster/) ─────────

# Items the canonicalizer moves. Anything outside this list is left alone —
# .claude/ in particular holds Claude Code's own files (settings.json, hooks/,
# etc.) which must never be touched.
_CANONICALIZE_ITEMS: tuple[str, ...] = (
    "backlog.yaml",
    "PROGRESS.md",
    "viewer.json",
    "tasks",
    "handovers",
    "issues",
    "trackers",
    "auto",
    "areas",
)


def _detect_layout_sources(project_root: Path) -> list[tuple[str, Path]]:
    """Return all layouts that hold a backlog.yaml. Order: claude, canonical, root."""
    found: list[tuple[str, Path]] = []
    if (project_root / ".claude" / "backlog.yaml").exists():
        found.append(("claude", project_root / ".claude"))
    if (project_root / ".taskmaster" / "backlog.yaml").exists():
        found.append(("canonical", project_root / ".taskmaster"))
    if (project_root / "backlog.yaml").exists():
        found.append(("root", project_root))
    return found


def _enumerate_moves(
    source_dir: Path, canonical_dir: Path, project_root: Path
) -> list[tuple[Path, Path]]:
    """Walk every file under each known artifact item and pair with its canonical
    target. Directories aren't moved as wholes — we descend so that auto/ and
    other dirs that already exist at the destination merge cleanly.
    """
    moves: list[tuple[Path, Path]] = []
    for item in _CANONICALIZE_ITEMS:
        src = source_dir / item
        if not src.exists():
            continue
        if src.is_file():
            moves.append((src, canonical_dir / item))
            continue
        for sub in src.rglob("*"):
            if sub.is_file():
                rel = sub.relative_to(src)
                moves.append((sub, canonical_dir / item / rel))
    return moves


def canonicalize_layout(
    project_root: Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Migrate a v3 backlog from `.claude/` or root layout into canonical `.taskmaster/`.

    Moves only the artifact items in `_CANONICALIZE_ITEMS` — other files in
    `.claude/` (Claude Code's own settings.json, hooks/, etc.) are untouched.

    Behavior:
      - Idempotent: re-running on an already-canonical layout is a no-op.
      - Refuses to clobber: a destination file with different content from the
        source aborts the migration with a `conflicts` summary; nothing moves.
      - When source and destination already hold the same bytes, the source
        copy is removed (cleanup of a partially-completed prior run).
      - `.taskmaster/auto/` may already exist (e.g. from before the migrator
        landed); enumeration is per-file so the merge is automatic.
      - `.claude/taskmaster.json` is deleted after a successful migration —
        the config it held (`backlog_path`, `progress_path`) is now redundant.

    Returns a summary dict with keys: status, source, destination, moved,
    skipped_already_at_dst, conflicts, deleted_config, removed_source_dir,
    dry_run. `status` is one of: no_backlog, already_canonical, ambiguous,
    conflicts, would_migrate (dry_run), migrated.
    """
    project_root = Path(project_root).resolve()
    canonical_dir = project_root / ".taskmaster"

    summary: dict[str, Any] = {
        "status": None,
        "source": None,
        "destination": str(canonical_dir),
        "moved": [],
        "skipped_already_at_dst": [],
        "conflicts": [],
        "deleted_config": None,
        "removed_source_dir": None,
        "dry_run": dry_run,
    }

    sources = _detect_layout_sources(project_root)
    if not sources:
        summary["status"] = "no_backlog"
        return summary

    legacy = [(kind, path) for kind, path in sources if kind != "canonical"]
    canonical_present = any(kind == "canonical" for kind, _ in sources)

    if canonical_present and not legacy:
        summary["status"] = "already_canonical"
        return summary

    if len(legacy) > 1:
        summary["status"] = "ambiguous"
        summary["sources_found"] = [k for k, _ in legacy]
        return summary

    if canonical_present and legacy:
        # Both layouts hold a backlog.yaml — refuse, the caller has to pick one.
        summary["status"] = "ambiguous"
        summary["sources_found"] = ["canonical"] + [k for k, _ in legacy]
        return summary

    source_kind, source_dir = legacy[0]
    summary["source"] = source_kind

    moves = _enumerate_moves(source_dir, canonical_dir, project_root)

    # Conflict + already-at-dst pass.
    plan: list[tuple[Path, Path]] = []
    for src, dst in moves:
        if dst.exists():
            try:
                same = src.read_bytes() == dst.read_bytes()
            except OSError:
                same = False
            rel_dst = str(dst.relative_to(project_root))
            if same:
                summary["skipped_already_at_dst"].append(rel_dst)
            else:
                summary["conflicts"].append({
                    "src": str(src.relative_to(project_root)),
                    "dst": rel_dst,
                })
        else:
            plan.append((src, dst))

    if summary["conflicts"]:
        summary["status"] = "conflicts"
        return summary

    if dry_run:
        summary["status"] = "would_migrate"
        summary["would_move"] = [
            {
                "src": str(s.relative_to(project_root)),
                "dst": str(d.relative_to(project_root)),
            }
            for s, d in plan
        ]
        return summary

    # Execute: move new files, delete duplicates already at dst.
    for src, dst in plan:
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(src), str(dst))
        summary["moved"].append({
            "src": str(src.relative_to(project_root)),
            "dst": str(dst.relative_to(project_root)),
        })
    for rel_dst in summary["skipped_already_at_dst"]:
        src_path = source_dir / Path(rel_dst).relative_to(canonical_dir.relative_to(project_root))
        if src_path.exists() and src_path.is_file():
            src_path.unlink()

    # Cleanup: remove now-empty artifact subdirs in source, then the source dir
    # itself if it still has only foreign content (or none).
    for item in _CANONICALIZE_ITEMS:
        d = source_dir / item
        if d.exists() and d.is_dir():
            for child in sorted(d.rglob("*"), reverse=True):
                if child.is_dir():
                    try:
                        child.rmdir()
                    except OSError:
                        pass
            try:
                d.rmdir()
            except OSError:
                pass

    if source_kind == "claude":
        config = source_dir / "taskmaster.json"
        if config.exists():
            config.unlink()
            summary["deleted_config"] = str(config.relative_to(project_root))
        # Don't try to remove .claude/ itself — Claude Code keeps settings here.
    elif source_kind == "root":
        # Root layout had no wrapping dir; nothing more to clean up.
        pass

    summary["status"] = "migrated"
    return summary


# ── Handovers ──────────────────────────────────────────────────

# Canonical session_kind values (free-form string in storage; these are
# the well-known ones that may get special treatment elsewhere).
# "task-complete" is eligible for smart auto-close (all task_ids done + no live next_action).
HANDOVER_KINDS = ("continuity", "deep-context", "milestone", "auto-stage", "task-complete")

_LEGACY_KIND_MAP = {
    "end-of-day": "continuity",
    "exploration": "continuity",
    "context-handoff": "deep-context",
    "milestone-complete": "milestone",
    "pivot": "milestone",
}


def _normalize_session_kind(kind: str) -> str:
    return _LEGACY_KIND_MAP.get(kind, kind)

# Three-state lifecycle for handovers — see specs/2026-05-09-handover-status-design.md
HANDOVER_STATUSES = ("open", "closed", "superseded")


def _default_handover_status(session_kind: str) -> str:
    """auto-stage handovers are bookkeeping checkpoints — born closed.
    All other kinds default to open so they surface in start-session glance."""
    return "closed" if session_kind == "auto-stage" else "open"


# Index cap — `handovers:` array in backlog.yaml is bounded.
HANDOVER_INDEX_CAP = 30

# Map storage-side handover kinds to viewer-side display kinds (spec §5).
# Storage kinds live in handover frontmatter (`session_kind`); the viewer renders
# them via this mapping for kind-pill colour, kind-filter chips, and right-rail header.
HANDOVER_KIND_TO_VIEWER_KIND = {
    "continuity":    "wrap",
    "deep-context":  "mid-task",
    "milestone":     "checkpoint",
    "auto-stage":    "standalone",
    "task-complete": "wrap",  # task-complete is a lightweight wrap-up variant
}

VIEWER_HANDOVER_KINDS = ("mid-task", "checkpoint", "wrap", "standalone")


def slugify(text: str, max_len: int = 40) -> str:
    """Reduce arbitrary text to a URL-safe slug.

    Lowercase, alnum + hyphens only, collapsed runs, length-capped. Empty
    input falls back to 'untitled' so the resulting handover id is always
    a valid filename.
    """
    if not text:
        return "untitled"
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    if not s:
        return "untitled"
    return s[:max_len].rstrip("-") or "untitled"


def make_handover_id(date_str: str, tldr: str) -> str:
    """Build a handover id from a date and tldr text."""
    return f"{date_str}-{slugify(tldr)}"


def handover_path(backlog_path: Path, handover_id: str) -> Path:
    """Resolve the file path for a given handover id."""
    return backlog_path.parent / "handovers" / f"{handover_id}.md"


def handover_dir(backlog_path: Path) -> Path:
    return backlog_path.parent / "handovers"


def write_handover(
    backlog_path: Path,
    *,
    tldr: str,
    next_action: str = "",
    body: str = "",
    task_ids: list[str] | None = None,
    session_kind: str = "continuity",
    when: str | None = None,
    context_size_at_write: str | None = None,
    supersedes: str | None = None,
    branch: str | None = None,
    tip_commit: str | None = None,
    open_decisions: list[str] | None = None,
    resolved_this_session: list[str] | None = None,
) -> tuple[str, Path]:
    """Write a new handover file.

    Returns (handover_id, file_path). The id is `<date>-<slug-of-tldr>`. If
    a handover with the same id already exists the slug gets a numeric
    suffix to avoid clobbering same-day handovers with similar tldrs.
    """
    if not tldr or not tldr.strip():
        raise ValueError("handover tldr is required")
    session_kind = _normalize_session_kind(session_kind)
    if session_kind not in HANDOVER_KINDS:
        raise ValueError(
            f"session_kind must be one of {HANDOVER_KINDS}, got {session_kind!r}"
        )
    when = when or date.today().isoformat()
    base_id = make_handover_id(when, tldr)
    target = handover_path(backlog_path, base_id)
    final_id = base_id
    suffix = 2
    while target.exists():
        final_id = f"{base_id}-{suffix}"
        target = handover_path(backlog_path, final_id)
        suffix += 1

    fm: dict[str, Any] = {
        "id": final_id,
        "date": when,
        # Microsecond precision so same-second writes order deterministically.
        "created": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "tldr": tldr.strip(),
        "next_action": (next_action or "").strip(),
        "task_ids": list(task_ids or []),
        "session_kind": session_kind,
    }
    fm["status"] = _default_handover_status(session_kind)
    fm["status_changed"] = fm["created"]
    fm["status_user_set"] = False
    if context_size_at_write:
        fm["context_size_at_write"] = context_size_at_write
    if supersedes:
        fm["supersedes"] = supersedes
    if branch:
        fm["branch"] = branch
    if tip_commit:
        fm["tip_commit"] = tip_commit
    fm["open_decisions"] = list(open_decisions or [])
    fm["resolved_this_session"] = list(resolved_this_session or [])

    write_task_file(target, fm, body)
    for did in fm["open_decisions"]:
        try:
            link_decision_to_handover(backlog_path, did, final_id)
        except FileNotFoundError:
            pass  # decision was deleted; don't fail the handover write
    return final_id, target


def read_handover(backlog_path: Path, handover_id: str) -> tuple[dict[str, Any], str]:
    """Read a handover file by id. Raises FileNotFoundError if missing."""
    return read_task_file(handover_path(backlog_path, handover_id))


def list_handover_ids(backlog_path: Path) -> list[str]:
    """List handover ids on disk, newest-first.

    Sort key per file: (id date-prefix, `created` ISO timestamp, file mtime,
    id) — descending. The id's `YYYY-MM-DD` prefix is the authoritative
    user-supplied `when=` date and leads the sort so that batch writes
    sharing a `created` timestamp still order by their intended date
    rather than falling back to alphabetical-by-slug (ISS-010). `created`
    and mtime remain as tiebreakers for same-day writes; id alpha is the
    final fallback.
    """
    d = handover_dir(backlog_path)
    if not d.exists():
        return []
    entries: list[tuple[str, str, float, str]] = []
    for p in d.glob("*.md"):
        created = ""
        try:
            fm, _ = read_task_file(p)
            created = str(fm.get("created") or "")
        except (OSError, ValueError):
            pass
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        date_prefix = p.stem[:10]
        entries.append((date_prefix, created, mtime, p.stem))
    entries.sort(reverse=True)
    return [stem for _, _, _, stem in entries]


def latest_handover_id(backlog_path: Path) -> str | None:
    ids = list_handover_ids(backlog_path)
    return ids[0] if ids else None


_SUPERSESSION_CALLOUT_RE = re.compile(
    r"^> \*\*SUPERSEDED \d{4}-\d{2}-\d{2} by \["
)


def _strip_supersession_callout(body: str) -> str:
    """Return `body` with any leading SUPERSEDED callout block stripped.

    A callout block is one or more contiguous lines starting with `>` whose
    first line matches `_SUPERSESSION_CALLOUT_RE`, optionally followed by a
    single blank line. If no callout matches, the body is returned unchanged.
    """
    if not body:
        return body
    body_lines = body.splitlines(keepends=True)
    if not body_lines or not _SUPERSESSION_CALLOUT_RE.match(body_lines[0]):
        return body
    end = 0
    while end < len(body_lines) and body_lines[end].startswith(">"):
        end += 1
    if end < len(body_lines) and body_lines[end].strip() == "":
        end += 1
    return "".join(body_lines[end:])


def apply_supersession(backlog_path: Path, *, old_id: str, new_id: str) -> Path:
    """Mark `old_id` as superseded by `new_id`.

    Edits the old handover in place:
      1. Sets `superseded_by: new_id` in the frontmatter.
      2. Prepends a callout block at the top of the body, OR rewrites the
         existing callout if one is already present (idempotent for a
         single old → many-newer chain).

    Returns the old handover's path. Raises FileNotFoundError if either id
    is missing on disk.
    """
    new_path = handover_path(backlog_path, new_id)
    if not new_path.exists():
        raise FileNotFoundError(new_id)
    old_path = handover_path(backlog_path, old_id)
    if not old_path.exists():
        raise FileNotFoundError(old_id)

    fm, body = read_handover(backlog_path, old_id)
    fm["superseded_by"] = new_id

    if not fm.get("status_user_set"):
        fm["status"] = "superseded"
        fm["status_changed"] = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        fm["status_reason"] = f"superseded by {new_id}"

    today = date.today().isoformat()
    callout = (
        f"> **SUPERSEDED {today} by [{new_id}](./{new_id}.md).**\n"
        f"> The next session should read the newer handover instead. "
        f"This file kept as a checkpoint reference.\n\n"
    )

    write_task_file(old_path, fm, callout + _strip_supersession_callout(body))
    return old_path


def apply_handover_review_flag(
    backlog_path: Path,
    *,
    handover_id: str,
    review_reason: str,
) -> Path:
    """Stamp `flag_for_review: true` + `review_reason` onto an existing handover.

    Used to flag the active handover for a session for later follow-up review.
    Idempotent — re-applying overwrites the `review_reason` and leaves the
    body untouched. Raises FileNotFoundError if the handover doesn't exist
    on disk.
    """
    target = handover_path(backlog_path, handover_id)
    if not target.exists():
        raise FileNotFoundError(handover_id)
    fm, body = read_handover(backlog_path, handover_id)
    fm["flag_for_review"] = True
    fm["review_reason"] = review_reason or ""
    write_task_file(target, fm, body)
    return target


def update_handover_status(
    backlog_path: Path,
    *,
    handover_id: str,
    status: str,
    reason: str = "",
) -> tuple[dict[str, Any], Path]:
    """Explicit user-driven status change. Sets status_user_set: true so
    subsequent auto-transitions skip this handover.

    Passing an empty `reason` preserves any existing `status_reason` rather
    than clearing it. Pass an explicit non-empty value to overwrite.

    Raises ValueError on bad enum, FileNotFoundError if missing.
    """
    if status not in HANDOVER_STATUSES:
        raise ValueError(f"status must be one of {HANDOVER_STATUSES}, got {status!r}")
    target = handover_path(backlog_path, handover_id)
    if not target.exists():
        raise FileNotFoundError(handover_id)
    fm, body = read_handover(backlog_path, handover_id)
    fm["status"] = status
    fm["status_changed"] = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    fm["status_user_set"] = True
    if reason:
        fm["status_reason"] = reason
    write_task_file(target, fm, body)
    return fm, target




# ── Parallel-handover smart-close ─────────────────────────────────────────────

_TASK_ID_RE = re.compile(r"\bT-\d+\b")

# session_kinds that are eligible for auto-close when all criteria met.
_AUTO_CLOSE_ELIGIBLE_KINDS: frozenset[str] = frozenset({"task-complete", ""})


def _next_action_references_live_tasks(
    next_action: str, done_or_archived_ids: set[str]
) -> bool:
    """Return True if `next_action` mentions any task ID not in
    `done_or_archived_ids`. Empty string → no live references → False."""
    if not next_action or not next_action.strip():
        return False
    mentioned = set(_TASK_ID_RE.findall(next_action))
    live = mentioned - done_or_archived_ids
    return bool(live)


def smart_auto_close_handovers(
    backlog_path: Path,
    *,
    triggering_task_id: str,
    done_or_archived_ids: set[str],
) -> dict[str, list[str]]:
    """Apply the smart auto-close rule to open handovers that include
    `triggering_task_id` in their task_ids.

    Auto-close only when ALL true:
      1. All task_ids in the handover are in done_or_archived_ids.
      2. next_action is empty OR mentions only done/archived task IDs.
      3. session_kind is "task-complete" or null/absent.

    Otherwise: leave open and flag with a reason.

    Returns:
        {"closed": [...list of ids auto-closed...],
         "flagged": [...list of ids kept open with flag_reason stamped...]}
    """
    closed: list[str] = []
    flagged: list[str] = []

    for hid in list_handover_ids(backlog_path):
        try:
            fm, body = read_handover(backlog_path, hid)
        except (OSError, ValueError):
            continue

        # Only consider open handovers that include the triggering task.
        if fm.get("status") != "open":
            continue
        task_ids: list[str] = fm.get("task_ids") or []
        if triggering_task_id not in task_ids:
            continue
        if fm.get("status_user_set"):
            continue

        # Evaluate the three criteria.
        all_tasks_terminal = all(t in done_or_archived_ids for t in task_ids)
        next_action: str = (fm.get("next_action") or "").strip()
        next_action_live = _next_action_references_live_tasks(next_action, done_or_archived_ids)
        session_kind: str = fm.get("session_kind") or ""
        kind_eligible = session_kind in _AUTO_CLOSE_ELIGIBLE_KINDS

        if all_tasks_terminal and not next_action_live and kind_eligible:
            # All criteria met — auto-close.
            fm["status"] = "closed"
            fm["status_changed"] = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            fm["status_reason"] = f"auto-closed: all task_ids done, triggering task {triggering_task_id}"
            fm.pop("flag_reason", None)
            write_task_file(handover_path(backlog_path, hid), fm, body)
            closed.append(hid)
        else:
            # Build a human-readable flag reason for start-session surfacing.
            reasons: list[str] = []
            if not all_tasks_terminal:
                live_ids = [t for t in task_ids if t not in done_or_archived_ids]
                reasons.append(f"task_ids still open: {', '.join(live_ids)}")
            if next_action_live:
                live_refs = set(_TASK_ID_RE.findall(next_action)) - done_or_archived_ids
                reasons.append(f"next_action references {', '.join(sorted(live_refs))}")
            if not kind_eligible:
                reasons.append(f"session_kind={session_kind!r} preserved for context")
            flag_reason = "; ".join(reasons)
            fm["flag_reason"] = flag_reason
            write_task_file(handover_path(backlog_path, hid), fm, body)
            flagged.append(hid)

    return {"closed": closed, "flagged": flagged}


def flag_open_reason(backlog_path: Path, handover_id: str) -> str | None:
    """Return the `flag_reason` string for an open handover, or None if absent.

    Returns None for closed/superseded handovers — those are not flagged.
    """
    try:
        fm, _ = read_handover(backlog_path, handover_id)
    except (OSError, ValueError):
        return None
    if fm.get("status") != "open":
        return None
    return fm.get("flag_reason") or None


def backfill_handover_status(backlog_data: dict[str, Any], backlog_path: Path) -> list[str]:
    """One-time pass: stamp `status: open` on every handover lacking the field,
    plus archived handovers, then mark the backlog as backfilled.

    No-op if `handover_status_backfilled` is already truthy. Returns the list
    of handover ids that were modified.
    """
    if backlog_data.get("handover_status_backfilled"):
        return []
    flipped: list[str] = []
    handovers_root = handover_dir(backlog_path)
    archive_root = handovers_root / "_archive"
    candidates: list[Path] = []
    if handovers_root.exists():
        candidates.extend(p for p in handovers_root.glob("*.md"))
    if archive_root.exists():
        candidates.extend(archive_root.rglob("*.md"))
    for path in candidates:
        try:
            fm, body = read_task_file(path)
        except (OSError, ValueError):
            continue
        if "status" in fm:
            continue
        try:
            mtime_iso = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(
                timespec="microseconds"
            )
        except OSError:
            mtime_iso = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        fm["status"] = "open"
        fm["status_changed"] = mtime_iso
        fm["status_reason"] = "backfilled by handover-status migration"
        fm["status_user_set"] = False
        write_task_file(path, fm, body)
        flipped.append(path.stem)
    backlog_data["handover_status_backfilled"] = True
    return flipped


_LEGACY_TO_OPEN = frozenset({"todo", "in-progress"})

# Marker key in backlog.yaml root so migration is idempotent.
_MIGRATION_V2_KEY = "handover_status_v2_migrated"


def migrate_handover_statuses(
    backlog_data: dict[str, Any],
    backlog_path: Path,
    *,
    done_or_archived_ids: set[str],
) -> dict[str, list[str]]:
    """One-shot migration: translate old three-state enum to new three-state enum.

    Mapping:
      - "todo" | "in-progress"  →  "open"
      - "done" + superseded_by  →  "superseded"
      - "done" + smart-close eligible  →  "closed"
      - "done" + NOT eligible  →  "open"  (context still relevant)

    Idempotent: no-op if `_MIGRATION_V2_KEY` is truthy in backlog_data.
    Returns {"migrated": [list of ids changed]}.
    """
    if backlog_data.get(_MIGRATION_V2_KEY):
        return {"migrated": []}

    migrated: list[str] = []
    handovers_root = handover_dir(backlog_path)
    archive_root = handovers_root / "_archive"
    candidates: list[Path] = []
    if handovers_root.exists():
        candidates.extend(p for p in handovers_root.glob("*.md"))
    if archive_root.exists():
        candidates.extend(archive_root.rglob("*.md"))

    for path in candidates:
        try:
            fm, body = read_task_file(path)
        except (OSError, ValueError):
            continue

        old_status = fm.get("status", "")
        # Skip handovers already on the new enum.
        if old_status in HANDOVER_STATUSES:
            continue

        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")

        if old_status in _LEGACY_TO_OPEN:
            fm["status"] = "open"
            fm["status_changed"] = now
            fm["status_reason"] = "migrated from legacy enum"
        elif old_status == "done":
            if fm.get("superseded_by"):
                fm["status"] = "superseded"
                fm["status_changed"] = now
                fm["status_reason"] = "migrated: had superseded_by"
            else:
                # Check smart-close eligibility inline (no file writes during check).
                task_ids: list[str] = fm.get("task_ids") or []
                next_action: str = (fm.get("next_action") or "").strip()
                session_kind: str = fm.get("session_kind") or ""
                all_terminal = all(t in done_or_archived_ids for t in task_ids)
                next_action_live = _next_action_references_live_tasks(
                    next_action, done_or_archived_ids
                )
                kind_eligible = session_kind in _AUTO_CLOSE_ELIGIBLE_KINDS
                if all_terminal and not next_action_live and kind_eligible:
                    fm["status"] = "closed"
                    fm["status_reason"] = "migrated: smart-close eligible"
                else:
                    fm["status"] = "open"
                    fm["status_reason"] = "migrated: context still relevant"
                fm["status_changed"] = now
        else:
            # Unknown status — default to open and flag.
            fm["status"] = "open"
            fm["status_changed"] = now
            fm["status_reason"] = f"migrated from unknown status {old_status!r}"

        write_task_file(path, fm, body)
        migrated.append(path.stem)

    backlog_data[_MIGRATION_V2_KEY] = True
    return {"migrated": migrated}


# Fields kept in the backlog.yaml `handovers:` index entry.
_HANDOVER_INDEX_FIELDS = (
    "id", "date", "created", "tldr", "next_action",
    "task_ids", "session_kind", "status", "flag_reason",
)


def _handover_index_entry(fm: dict[str, Any]) -> dict[str, Any]:
    """Project a handover frontmatter dict to its slim index entry."""
    return {f: fm.get(f) for f in _HANDOVER_INDEX_FIELDS if fm.get(f) is not None}


def archive_handover(backlog_path: Path, handover_id: str) -> Path:
    """Move a handover file from handovers/ to handovers/_archive/<year>/.

    The year is parsed from the id prefix (YYYY-...). If the id doesn't
    follow that pattern, archive under handovers/_archive/unknown/.
    Returns the new path.
    """
    src = handover_path(backlog_path, handover_id)
    if not src.exists():
        raise FileNotFoundError(handover_id)
    year = handover_id[:4] if re.match(r"^\d{4}", handover_id) else "unknown"
    dest_dir = handover_dir(backlog_path) / "_archive" / year
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    os.replace(src, dest)
    return dest


def sync_handover_index(
    backlog_data: dict[str, Any],
    backlog_path: Path,
    cap: int = HANDOVER_INDEX_CAP,
) -> dict[str, Any]:
    """Populate backlog_data['handovers'] from disk; archive overflow.

    Reads all handover files (excluding _archive/), sorts newest-first,
    keeps the first `cap` as index entries in backlog_data, and archives
    the rest. Mutates backlog_data in place and returns it for chaining.
    """
    ids = list_handover_ids(backlog_path)
    keep_ids = ids[:cap]
    overflow_ids = ids[cap:]

    entries: list[dict[str, Any]] = []
    for hid in keep_ids:
        try:
            fm, _ = read_handover(backlog_path, hid)
        except (OSError, ValueError):
            continue
        entries.append(_handover_index_entry(fm))

    backlog_data["handovers"] = entries

    for hid in overflow_ids:
        try:
            archive_handover(backlog_path, hid)
        except (OSError, FileNotFoundError):
            continue

    return backlog_data


# ── Issues ─────────────────────────────────────────────────────

ISSUE_STATUSES = ("open", "investigating", "fixed", "wontfix", "duplicate")
ISSUE_SEVERITIES = ("P0", "P1", "P2", "P3")
_SEVERITY_RANK = {s: i for i, s in enumerate(ISSUE_SEVERITIES)}  # P0=0 most-severe

# Index entry slim metadata kept in backlog.yaml for fast dashboard render.
_ISSUE_INDEX_FIELDS = (
    "id",
    "title",
    "status",
    "severity",
    "components",
    "related_tasks",
    "tracker_id",
)


def issue_path(backlog_path: Path, issue_id: str) -> Path:
    return backlog_path.parent / "issues" / f"{issue_id}.md"


def issue_dir(backlog_path: Path) -> Path:
    return backlog_path.parent / "issues"


def list_issue_ids(backlog_path: Path) -> list[str]:
    """List issue ids on disk, sorted numerically by the trailing number."""
    d = issue_dir(backlog_path)
    if not d.exists():
        return []

    def _rank(p: Path) -> int:
        m = re.search(r"(\d+)$", p.stem)
        return int(m.group(1)) if m else -1

    files = sorted(d.glob("ISS-*.md"), key=_rank)
    return [p.stem for p in files]


def next_issue_id(backlog_path: Path) -> str:
    """Allocate the next ISS-NNN id (zero-padded, 3+ digits)."""
    existing = list_issue_ids(backlog_path)
    nums = []
    for ident in existing:
        m = re.search(r"(\d+)$", ident)
        if m:
            nums.append(int(m.group(1)))
    n = (max(nums) + 1) if nums else 1
    return f"ISS-{n:03d}"


# ── Bugs ───────────────────────────────────────────────────────

BUG_STATUSES = ("open", "fixed", "shelved", "adopted", "promoted")
BUG_SEVERITIES = ("P0", "P1", "P2", "P3")  # OPTIONAL on bugs
DISCOVERED_BY_VALUES = ("user", "claude")

_BUG_INDEX_FIELDS = (
    "id",
    "title",
    "status",
    "severity",
    "components",
    "found_in",
    "discovered",
)


def bug_dir(backlog_path: Path, archived: bool = False) -> Path:
    base = backlog_path.parent / "bugs"
    return base / "archive" if archived else base


def bug_path(backlog_path: Path, bug_id: str, archived: bool = False) -> Path:
    return bug_dir(backlog_path, archived=archived) / f"{bug_id}.md"


def list_bug_ids(backlog_path: Path, include_archive: bool = False) -> list[str]:
    """List bug ids on disk, sorted numerically by the trailing number.

    By default, returns only active bugs. Archive entries are returned in the
    same list (after active) when include_archive=True.
    """
    def _rank(p: Path) -> int:
        m = re.search(r"(\d+)$", p.stem)
        return int(m.group(1)) if m else -1

    active = sorted(bug_dir(backlog_path).glob("B-*.md"), key=_rank) if bug_dir(backlog_path).exists() else []
    if not include_archive:
        return [p.stem for p in active]
    arch_dir = bug_dir(backlog_path, archived=True)
    archived = sorted(arch_dir.glob("B-*.md"), key=_rank) if arch_dir.exists() else []
    return [p.stem for p in active] + [p.stem for p in archived]


def next_bug_id(backlog_path: Path) -> str:
    """Allocate the next B-NNN id (zero-padded, 3+ digits).

    Counts both active and archive when allocating so IDs are never reused.
    """
    existing = list_bug_ids(backlog_path, include_archive=True)
    nums = []
    for ident in existing:
        m = re.search(r"(\d+)$", ident)
        if m:
            nums.append(int(m.group(1)))
    n = (max(nums) + 1) if nums else 1
    return f"B-{n:03d}"


def _validate_bug(fm: dict[str, Any]) -> None:
    """Raise ValueError if frontmatter violates Bug invariants."""
    status = fm.get("status")
    if status not in BUG_STATUSES:
        raise ValueError(f"status must be one of {BUG_STATUSES}, got {status!r}")
    sev = fm.get("severity")
    if sev is not None and sev not in BUG_SEVERITIES:
        raise ValueError(f"severity must be one of {BUG_SEVERITIES} or null, got {sev!r}")
    disc_by = fm.get("discovered_by")
    if disc_by not in DISCOVERED_BY_VALUES:
        raise ValueError(f"discovered_by must be one of {DISCOVERED_BY_VALUES}, got {disc_by!r}")
    if status == "fixed" and not fm.get("fix_commit"):
        raise ValueError("status=fixed requires fix_commit to be set")
    if status == "adopted" and not fm.get("adopted_into"):
        raise ValueError("status=adopted requires adopted_into to be set")
    if status == "promoted" and not fm.get("promoted_to"):
        raise ValueError("status=promoted requires promoted_to to be set")


def write_bug(
    backlog_path: Path,
    *,
    title: str,
    found_in: str | None = None,
    discovered_by: str = "user",
    severity: str | None = None,
    components: list[str] | None = None,
    location: list[str] | None = None,
    body: str = "",
    bug_id: str | None = None,
    status: str = "open",
) -> tuple[str, Path]:
    """Create a new Bug file. Returns (id, path)."""
    if not title or not title.strip():
        raise ValueError("bug title is required")
    bid = bug_id or next_bug_id(backlog_path)
    fm: dict[str, Any] = {
        "id": bid,
        "title": title.strip(),
        "status": status,
        "severity": severity,
        "components": list(components or []),
        "found_in": found_in,
        "discovered": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "discovered_by": discovered_by,
        "location": list(location or []),
        "fix_commit": None,
        "adopted_into": None,
        "promoted_to": None,
        "links": [],
    }
    _validate_bug(fm)
    bug_dir(backlog_path).mkdir(parents=True, exist_ok=True)
    target = bug_path(backlog_path, bid)
    write_task_file(target, fm, body)
    return bid, target


def read_bug(backlog_path: Path, bug_id: str) -> tuple[dict[str, Any], str]:
    """Read a Bug. Falls through to archive if not active."""
    p = bug_path(backlog_path, bug_id)
    if not p.exists():
        p = bug_path(backlog_path, bug_id, archived=True)
    return read_task_file(p)


def update_bug(
    backlog_path: Path,
    bug_id: str,
    **updates: Any,
) -> tuple[dict[str, Any], str]:
    """Apply partial updates to a Bug's frontmatter, validate, and rewrite."""
    fm, body = read_bug(backlog_path, bug_id)
    new_body = updates.pop("body", body)
    fm.update({k: v for k, v in updates.items() if v is not None})
    _validate_bug(fm)
    # Determine target — preserve archive vs active based on where it lives.
    target = bug_path(backlog_path, bug_id)
    if not target.exists():
        target = bug_path(backlog_path, bug_id, archived=True)
    write_task_file(target, fm, new_body)
    return fm, new_body


def archive_bug(backlog_path: Path, bug_id: str) -> Path:
    """Move bugs/B-NNN.md → bugs/archive/B-NNN.md.

    Idempotent (no-op if already in archive). Refuses if status=open or shelved —
    archive is only for terminal-resolved bugs (fixed/adopted/promoted).
    """
    active = bug_path(backlog_path, bug_id)
    archived = bug_path(backlog_path, bug_id, archived=True)
    if archived.exists() and not active.exists():
        return archived  # already there
    fm, _ = read_bug(backlog_path, bug_id)
    if fm["status"] in ("open", "shelved"):
        raise ValueError(f"cannot archive bug with status={fm['status']} (must be fixed/adopted/promoted)")
    archived.parent.mkdir(parents=True, exist_ok=True)
    active.rename(archived)
    return archived


def _bug_index_entry(fm: dict[str, Any]) -> dict[str, Any]:
    return {f: fm.get(f) for f in _BUG_INDEX_FIELDS if fm.get(f) is not None}


def sync_bug_index(
    backlog_data: dict[str, Any],
    backlog_path: Path,
) -> dict[str, Any]:
    """Rebuild backlog_data['bugs'] from disk (active only — archive is opaque).

    Sorted by (status weight ascending, discovered descending) so open ones
    surface first and most-recent within a status group.
    """
    status_weight = {"open": 0, "shelved": 1, "adopted": 2, "promoted": 3, "fixed": 4}
    entries: list[dict[str, Any]] = []
    for bid in list_bug_ids(backlog_path, include_archive=False):
        try:
            fm, _ = read_bug(backlog_path, bid)
        except (OSError, ValueError):
            continue
        entries.append(_bug_index_entry(fm))
    entries.sort(key=lambda e: (status_weight.get(e.get("status", "open"), 99), -1 * _discovered_rank(e)))
    backlog_data["bugs"] = entries
    return backlog_data


def _discovered_rank(entry: dict[str, Any]) -> int:
    """Convert ISO-8601 discovered timestamp to an integer for sort comparison."""
    s = entry.get("discovered") or ""
    try:
        return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").timestamp())
    except (ValueError, TypeError):
        return 0


def _bug_signature(fm: dict[str, Any]) -> tuple | None:
    """Compute (components_tuple, tokens_tuple) signature for pattern-matching.

    Returns None if the signal is too thin (fewer than 3 token-tokens after
    stripping). Tokens are <3-char tokens dropped, numeric literals dropped,
    duplicates removed, then sorted alphabetically.
    """
    title = (fm.get("title") or "").lower()
    raw = re.findall(r"[a-z]+", title)  # letters only; drops digits and punct
    tokens = sorted({t for t in raw if len(t) >= 3})
    if len(tokens) < 3:
        return None
    comps = tuple(sorted(set(c.lower() for c in (fm.get("components") or []))))
    return (comps, tuple(tokens))


def scan_bug_patterns(
    backlog_path: Path,
    include_archive: bool = True,
    open_only: bool = False,
) -> list[dict[str, Any]]:
    """Return a list of pattern groups: [{signature, bug_ids: [B-001, B-007, ...]}, ...].

    Only groups with ≥2 bugs are returned. Includes archive by default so that
    historical resolved bugs contribute to recurrence counts.

    Two bugs cluster together when they share the same component set AND their
    title token sets overlap by Jaccard ≥ 0.5. The canonical signature for a
    group uses the intersection of token sets (i.e. the tokens all members share).
    """
    # Collect (bid, comps_tuple, tokens_frozenset) for each bug with a valid sig.
    entries: list[tuple[str, tuple, frozenset]] = []
    for bid in list_bug_ids(backlog_path, include_archive=include_archive):
        try:
            fm, _ = read_bug(backlog_path, bid)
        except (OSError, ValueError):
            continue
        if open_only and fm.get("status") != "open":
            continue
        sig = _bug_signature(fm)
        if sig is None:
            continue
        comps, tokens_tuple = sig
        entries.append((bid, comps, frozenset(tokens_tuple)))

    # Union-Find clustering by component equality + Jaccard ≥ 0.5.
    parent: dict[int, int] = {i: i for i in range(len(entries))}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            _, ci, ti = entries[i]
            _, cj, tj = entries[j]
            if ci != cj:
                continue
            intersection = ti & tj
            union_size = len(ti | tj)
            jaccard = len(intersection) / union_size if union_size else 0.0
            if jaccard >= 0.5:
                union(i, j)

    # Aggregate clusters.
    clusters: dict[int, list[int]] = {}
    for i in range(len(entries)):
        root = find(i)
        clusters.setdefault(root, []).append(i)

    result = []
    for root, members in clusters.items():
        if len(members) < 2:
            continue
        bug_ids = [entries[i][0] for i in members]
        # Canonical signature: intersection of all token sets in group + shared comps.
        shared_tokens = entries[members[0]][2].copy()
        for i in members[1:]:
            shared_tokens &= entries[i][2]
        comps = entries[members[0]][1]
        result.append({
            "signature": {"components": list(comps), "tokens": sorted(shared_tokens)},
            "bug_ids": bug_ids,
        })
    return result


def promote_bugs_to_issue(
    backlog_path: Path,
    *,
    bug_ids: list[str],
    title: str,
    severity: str,
    evidence_text: str,
    components: list[str] | None = None,
    body: str = "",
) -> str:
    """Atomic: create an Issue from N Bugs, mark each Bug as promoted.

    The new Issue gets a `promoted_from: [B-NNN, ...]` frontmatter field
    in addition to the standard fields. The matched Bugs each get
    status=promoted and promoted_to=<the new Issue ID>.
    """
    if not bug_ids:
        raise ValueError("bug_ids must be non-empty")
    if not evidence_text or not evidence_text.strip():
        raise ValueError("evidence_text is required (cite recurrence/systemic/outstanding)")

    # Aggregate components from source bugs if not given.
    if components is None:
        comps_set: set[str] = set()
        for bid in bug_ids:
            fm, _ = read_bug(backlog_path, bid)
            for c in fm.get("components") or []:
                comps_set.add(c)
        components = sorted(comps_set)

    iss_id, _ = write_issue(
        backlog_path,
        title=title,
        severity=severity,
        impact=evidence_text,  # repurpose impact field as evidence narrative
        components=components,
        body=body,
    )
    # Backfill the new evidence and promoted_from fields on the issue file.
    fm, b = read_issue(backlog_path, iss_id)
    fm["evidence"] = evidence_text.strip()
    fm["promoted_from"] = list(bug_ids)
    _validate_issue(fm)
    write_task_file(issue_path(backlog_path, iss_id), fm, b)

    # Mark each source bug as promoted.
    for bid in bug_ids:
        update_bug(backlog_path, bid, status="promoted", promoted_to=iss_id)

    return iss_id


DECISION_STATUSES = ("open", "resolved", "dropped")


def decision_dir(backlog_path: Path) -> Path:
    """Return the `decisions/` dir alongside backlog.yaml."""
    return backlog_path.parent / "decisions"


def list_decision_ids(backlog_path: Path) -> list[str]:
    """List decision ids on disk, sorted numerically by trailing number."""
    d = decision_dir(backlog_path)
    if not d.exists():
        return []

    def _rank(p: Path) -> int:
        m = re.search(r"(\d+)$", p.stem)
        return int(m.group(1)) if m else -1

    files = sorted(d.glob("DEC-*.md"), key=_rank)
    return [p.stem for p in files]


def next_decision_id(backlog_path: Path) -> str:
    """Allocate the next DEC-NNN id (zero-padded, 3+ digits)."""
    existing = list_decision_ids(backlog_path)
    nums = [int(re.search(r"(\d+)$", x).group(1)) for x in existing
            if re.search(r"(\d+)$", x)]
    n = (max(nums) + 1) if nums else 1
    return f"DEC-{n:03d}"


def decision_path(backlog_path: Path, decision_id: str) -> Path:
    return decision_dir(backlog_path) / f"{decision_id}.md"


def _validate_decision(fm: dict[str, Any]) -> None:
    """Raise ValueError if frontmatter violates decision invariants."""
    status = fm.get("status")
    if status not in DECISION_STATUSES:
        raise ValueError(f"status must be one of {DECISION_STATUSES}, got {status!r}")
    opts = fm.get("options") or []
    if not isinstance(opts, list) or len(opts) < 2:
        raise ValueError("decision must have at least 2 options")
    rec = fm.get("recommendation")
    if rec is not None:
        if not isinstance(rec, int) or not (1 <= rec <= len(opts)):
            raise ValueError(f"recommendation must be 1..{len(opts)}, got {rec!r}")
    rw = fm.get("resolved_with")
    if status == "resolved" and not rw:
        raise ValueError("status=resolved requires resolved_with to be set (1..N)")
    # resolved_with must stay in range even after options shrink (B-026): a later
    # update_decision that drops options below resolved_with would otherwise persist
    # an out-of-bounds index that crashes options[resolved_with - 1] on read.
    if rw not in (None, ""):
        if not isinstance(rw, int) or isinstance(rw, bool) or not (1 <= rw <= len(opts)):
            raise ValueError(f"resolved_with must be 1..{len(opts)}, got {rw!r}")
    if status == "dropped" and not fm.get("dropped_reason"):
        raise ValueError("status=dropped requires dropped_reason")


def write_decision(
    backlog_path: Path,
    *,
    title: str,
    options: list[str],
    recommendation: int | None = None,
    task_id: str | None = None,
    related_issues: list[str] | None = None,
    branch: str | None = None,
    raised_in: str | None = None,
    body: str = "",
    decision_id: str | None = None,
    status: str = "open",
) -> tuple[str, Path]:
    """Create a new decision file. Returns (id, path)."""
    if not title or not title.strip():
        raise ValueError("decision title is required")
    did = decision_id or next_decision_id(backlog_path)
    fm: dict[str, Any] = {
        "id": did,
        "title": title.strip(),
        "status": status,
        "options": list(options),
        "recommendation": recommendation,
        "task_id": task_id,
        "related_issues": list(related_issues or []),
        "branch": branch,
        "resolved_with": None,
        "resolved_rationale": None,
        "dropped_reason": None,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "resolved_at": None,
        "raised_in": raised_in,
        "referenced_in": [],
        "resolved_in": None,
    }
    _validate_decision(fm)
    target = decision_path(backlog_path, did)
    write_task_file(target, fm, body)
    return did, target


def read_decision(backlog_path: Path, decision_id: str) -> tuple[dict[str, Any], str]:
    """Read a decision file by id. Raises FileNotFoundError if missing."""
    return read_task_file(decision_path(backlog_path, decision_id))


def update_decision(
    backlog_path: Path,
    decision_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Apply a field-level patch to a decision. Returns the new frontmatter."""
    fm, body = read_decision(backlog_path, decision_id)
    if fm["status"] in ("resolved", "dropped") and patch.get("status") == "open":
        raise ValueError(f"cannot reopen terminal decision {decision_id}")
    fm.update(patch)
    _validate_decision(fm)
    write_task_file(decision_path(backlog_path, decision_id), fm, body)
    return fm


def resolve_decision(
    backlog_path: Path,
    decision_id: str,
    *,
    resolved_with: int,
    rationale: str | None = None,
    resolved_in: str | None = None,
) -> dict[str, Any]:
    """Flip a decision to resolved with a chosen option (1-indexed)."""
    fm, body = read_decision(backlog_path, decision_id)
    if not (1 <= int(resolved_with) <= len(fm.get("options") or [])):
        raise ValueError(
            f"resolved_with must be 1..{len(fm['options'])}, got {resolved_with}"
        )
    fm["status"] = "resolved"
    fm["resolved_with"] = int(resolved_with)
    fm["resolved_rationale"] = (rationale or "").strip() or None
    fm["resolved_at"] = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    if resolved_in:
        fm["resolved_in"] = resolved_in
    _validate_decision(fm)
    write_task_file(decision_path(backlog_path, decision_id), fm, body)
    return fm


def drop_decision(
    backlog_path: Path,
    decision_id: str,
    *,
    reason: str,
) -> dict[str, Any]:
    """Mark a decision as dropped with a reason."""
    if not reason or not reason.strip():
        raise ValueError("drop reason is required")
    fm, body = read_decision(backlog_path, decision_id)
    fm["status"] = "dropped"
    fm["dropped_reason"] = reason.strip()
    fm["resolved_at"] = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    _validate_decision(fm)
    write_task_file(decision_path(backlog_path, decision_id), fm, body)
    return fm


def link_decision_to_handover(
    backlog_path: Path,
    decision_id: str,
    handover_id: str,
) -> dict[str, Any]:
    """Append a handover id to the decision's referenced_in (idempotent)."""
    fm, body = read_decision(backlog_path, decision_id)
    refs = list(fm.get("referenced_in") or [])
    if handover_id not in refs:
        refs.append(handover_id)
        fm["referenced_in"] = refs
        write_task_file(decision_path(backlog_path, decision_id), fm, body)
    return fm


def _validate_issue(fm: dict[str, Any]) -> None:
    """Raise ValueError if frontmatter violates the issue invariants."""
    status = fm.get("status")
    if status not in ISSUE_STATUSES:
        raise ValueError(f"status must be one of {ISSUE_STATUSES}, got {status!r}")
    sev = fm.get("severity")
    if sev not in ISSUE_SEVERITIES:
        raise ValueError(f"severity must be one of {ISSUE_SEVERITIES}, got {sev!r}")
    if status == "fixed" and not fm.get("fixed_in_task"):
        raise ValueError("status=fixed requires fixed_in_task to be set")
    if status == "duplicate" and not fm.get("duplicate_of"):
        raise ValueError("status=duplicate requires duplicate_of to be set")
    # NEW: evidence field is required per bug-tier redesign.
    evidence = (fm.get("evidence") or "").strip()
    if not evidence:
        raise ValueError("evidence is required — cite recurrence/systemic/outstanding criterion")


def write_issue(
    backlog_path: Path,
    *,
    title: str,
    severity: str,
    impact: str = "",
    evidence: str = "",
    components: list[str] | None = None,
    location: list[str] | None = None,
    related_tasks: list[str] | None = None,
    discovered: str | None = None,
    discovered_by: str = "",
    body: str = "",
    issue_id: str | None = None,
    status: str = "open",
    tldr: str = "",
    tldr_autogen: bool = False,
    tracker_id: str | None = None,
) -> tuple[str, Path]:
    """Create a new issue file. Returns (id, path)."""
    if not title or not title.strip():
        raise ValueError("issue title is required")
    # evidence fallback: legacy callers pass impact only; treat impact as evidence.
    if not evidence or not evidence.strip():
        if not impact or not impact.strip():
            raise ValueError("evidence (or impact) is required for Issue creation")
        evidence = impact
    iid = issue_id or next_issue_id(backlog_path)
    from datetime import datetime, timezone
    default_discovered = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fm: dict[str, Any] = {
        "id": iid,
        "title": title.strip(),
        "status": status,
        "severity": severity,
        "components": list(components or []),
        "impact": impact.strip(),
        "evidence": evidence.strip(),
        "location": list(location or []),
        "discovered": discovered or default_discovered,
        "discovered_by": discovered_by,
        "resolved": None,
        "related_tasks": list(related_tasks or []),
        "fixed_in_task": None,
        "duplicate_of": None,
        "promoted_from": [],
        "tldr": tldr,
        "tracker_id": tracker_id or None,
    }
    if tldr_autogen:
        fm["tldr_autogen"] = True
    _validate_issue(fm)
    write_task_file(issue_path(backlog_path, iid), fm, body)
    return iid, issue_path(backlog_path, iid)


def read_issue(backlog_path: Path, issue_id: str) -> tuple[dict[str, Any], str]:
    return read_task_file(issue_path(backlog_path, issue_id))


def update_issue(
    backlog_path: Path,
    issue_id: str,
    **updates: Any,
) -> tuple[dict[str, Any], str]:
    """Apply partial updates to an issue's frontmatter, validate, and rewrite.

    Body is preserved unchanged unless `body=` is passed.
    """
    fm, body = read_issue(backlog_path, issue_id)
    new_body = updates.pop("body", body)
    fm.update({k: v for k, v in updates.items() if v is not None})
    if fm.get("status") == "fixed" and not fm.get("resolved"):
        fm["resolved"] = date.today().isoformat()
    _validate_issue(fm)
    write_task_file(issue_path(backlog_path, issue_id), fm, new_body)
    return fm, new_body


def _issue_index_entry(fm: dict[str, Any]) -> dict[str, Any]:
    return {f: fm.get(f) for f in _ISSUE_INDEX_FIELDS if fm.get(f) is not None}


def sync_issue_index(
    backlog_data: dict[str, Any],
    backlog_path: Path,
) -> dict[str, Any]:
    """Rebuild backlog_data['issues'] from disk.

    Sorted by (severity asc, id asc) so P0s float to the top of the list.
    No archive/cap — issues are bounded by reality, not policy.
    """
    entries: list[dict[str, Any]] = []
    for iid in list_issue_ids(backlog_path):
        try:
            fm, _ = read_issue(backlog_path, iid)
        except (OSError, ValueError):
            continue
        entries.append(_issue_index_entry(fm))
    entries.sort(key=lambda e: (_SEVERITY_RANK.get(e.get("severity", "P3"), 99), e.get("id", "")))
    backlog_data["issues"] = entries
    return backlog_data


# ── Trackers ───────────────────────────────────────────────────
#
# A tracker is a local read-only mirror of an external issue (Jira in V1).
# It is NOT an epic and carries no epic semantics — just a reference + cache.
# The reverse map (tracker → linked tasks/issues) is derived on demand from
# the index, never stored on the tracker, to eliminate sync-drift bugs.
#
# `external_key` is stored verbatim from the source (e.g. "CM-101"). The id
# lowercases it for determinism. Always reconstruct ids via make_tracker_id,
# never by string-joining stored frontmatter fields directly.

EXTERNAL_SYSTEMS: tuple[str, ...] = ("jira", "linear")

# Each registered external system has a fixed sync direction. Pull-dominant
# systems (Jira) treat the external as upstream and mirror it locally read-only.
# Push-dominant systems (Linear) treat the local TM state as source of truth and
# mirror outbound on every mutation. The runtime worker picks its codepath from
# this map; the field is denormalized onto each tracker file as a hint for
# humans reading the file, but the map is the source of truth.
_SYNC_DIRECTION_BY_SYSTEM: dict[str, str] = {
    "jira": "pull",
    "linear": "push",
}

# Sentinel for update_tracker: distinguishes "field not passed" from
# "field passed as None to clear it".
_TRACKER_UNSET: Any = object()

_TRACKER_INDEX_FIELDS = (
    "id",
    "external_system",
    "external_key",
    "instance_alias",
    "title",
    "status",
    "url",
    "last_synced",
)


def tracker_path(backlog_path: Path, tracker_id: str) -> Path:
    return backlog_path.parent / "trackers" / f"{tracker_id}.md"


def tracker_dir(backlog_path: Path) -> Path:
    return backlog_path.parent / "trackers"


def make_tracker_id(external_system: str, instance_alias: str, external_key: str) -> str:
    """Build the deterministic tracker id: <system>-<alias>-<key-lowercased>.

    Re-pulling the same external key from the same instance always yields the
    same id, so trackers can be upserted by id without dedup logic.
    """
    if not external_system or not str(external_system).strip():
        raise ValueError("external_system is required")
    if not instance_alias or not str(instance_alias).strip():
        raise ValueError("instance_alias is required")
    if not external_key or not str(external_key).strip():
        raise ValueError("external_key is required")
    return f"{str(external_system).strip().lower()}-{str(instance_alias).strip().lower()}-{str(external_key).strip().lower()}"


def list_tracker_ids(backlog_path: Path) -> list[str]:
    d = tracker_dir(backlog_path)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.md"))


def _validate_tracker(fm: dict[str, Any]) -> None:
    """Raise ValueError if frontmatter violates the tracker invariants."""
    for field in ("id", "external_system", "external_key", "instance_alias", "title", "status"):
        if not fm.get(field):
            raise ValueError(f"tracker frontmatter missing required field: {field}")
    sys_name = str(fm["external_system"]).lower()
    if sys_name not in EXTERNAL_SYSTEMS:
        raise ValueError(
            f"external_system must be one of {EXTERNAL_SYSTEMS}, got {fm['external_system']!r}"
        )
    sd = fm.get("sync_direction")
    if sd is not None:
        expected_sd = _SYNC_DIRECTION_BY_SYSTEM.get(sys_name)
        if sd != expected_sd:
            raise ValueError(
                f"sync_direction {sd!r} does not match system map for {sys_name!r} "
                f"(expected {expected_sd!r})"
            )
    expected = make_tracker_id(fm["external_system"], fm["instance_alias"], fm["external_key"])
    if fm["id"] != expected:
        raise ValueError(
            f"tracker id {fm['id']!r} does not match deterministic format "
            f"<system>-<alias>-<key-lowercased> (expected {expected!r})"
        )


def write_tracker(
    backlog_path: Path,
    *,
    external_system: str,
    instance_alias: str,
    external_key: str,
    title: str,
    status: str,
    assignee: str | None = None,
    url: str | None = None,
    last_synced: str | None = None,
    synced_hash: str | None = None,
    last_pushed: str | None = None,
    push_hash: str | None = None,
    sync_direction: str | None = None,
    body: str = "",
) -> tuple[str, Path]:
    """Upsert a tracker file. Returns (id, path).

    Trackers are upsert-by-id — calling write_tracker with the same
    (external_system, instance_alias, external_key) triple always overwrites
    the same path. Callers (e.g. `backlog_jira_pull`) that hash payloads to
    skip unchanged writes do that gating before calling this.

    `sync_direction` is accepted for signature compatibility but ignored —
    the on-disk value is always re-derived from `_SYNC_DIRECTION_BY_SYSTEM`
    so it can never drift from the system's direction policy.
    `last_pushed` and `push_hash` are populated by push-dominant workers
    (e.g. Linear) after a successful outbound mutation.
    """
    del sync_direction  # always derived from external_system
    if not title or not str(title).strip():
        raise ValueError("tracker title is required")
    tid = make_tracker_id(external_system, instance_alias, external_key)
    es_lc = str(external_system).strip().lower()
    fm: dict[str, Any] = {
        "id": tid,
        "external_system": es_lc,
        "external_key": str(external_key).strip(),
        "instance_alias": str(instance_alias).strip(),
        "title": str(title).strip(),
        "status": str(status).strip() if status else "",
        "assignee": assignee or None,
        "url": url or None,
        "sync_direction": _SYNC_DIRECTION_BY_SYSTEM.get(es_lc),
        "last_synced": last_synced,
        "synced_hash": synced_hash,
        "last_pushed": last_pushed,
        "push_hash": push_hash,
    }
    _validate_tracker(fm)
    path = tracker_path(backlog_path, tid)
    write_task_file(path, fm, body)
    return tid, path


def read_tracker(backlog_path: Path, tracker_id: str) -> tuple[dict[str, Any], str]:
    return read_task_file(tracker_path(backlog_path, tracker_id))


def update_tracker(
    backlog_path: Path,
    tracker_id: str,
    **updates: Any,
) -> tuple[dict[str, Any], str]:
    """Apply partial updates to a tracker's frontmatter, validate, and rewrite.

    Body is preserved unchanged unless `body=` is passed. The id and the three
    fields it derives from (external_system, external_key, instance_alias) are
    immutable — passing them in updates is silently ignored.

    Passing a field as None clears it (for nullable fields like assignee, url,
    last_synced, synced_hash). To leave a field untouched, omit it entirely.
    """
    fm, body = read_tracker(backlog_path, tracker_id)
    if fm.get("id") != tracker_id:
        raise ValueError(
            f"on-disk tracker id {fm.get('id')!r} does not match requested {tracker_id!r}"
        )
    _validate_tracker(fm)
    new_body = updates.pop("body", body)
    for immutable in ("id", "external_system", "external_key", "instance_alias", "sync_direction"):
        updates.pop(immutable, None)
    fm.update(updates)
    _validate_tracker(fm)
    write_task_file(tracker_path(backlog_path, tracker_id), fm, new_body)
    return fm, new_body


def _tracker_index_entry(fm: dict[str, Any]) -> dict[str, Any]:
    return {f: fm.get(f) for f in _TRACKER_INDEX_FIELDS if fm.get(f) is not None}


def sync_tracker_index(
    backlog_data: dict[str, Any],
    backlog_path: Path,
) -> dict[str, Any]:
    """Rebuild backlog_data['trackers'] from disk, sorted by id.

    No archive/cap — trackers are bounded by the JQL response. A separate
    `jira archive` command (V1.5) handles decluttering.
    """
    entries: list[dict[str, Any]] = []
    for tid in list_tracker_ids(backlog_path):
        try:
            fm, _ = read_tracker(backlog_path, tid)
        except (OSError, ValueError, yaml.YAMLError):
            continue
        entries.append(_tracker_index_entry(fm))
    entries.sort(key=lambda e: e.get("id", ""))
    backlog_data["trackers"] = entries
    return backlog_data


def linked_tasks_for_tracker(
    backlog_data: dict[str, Any], tracker_id: str
) -> list[dict[str, Any]]:
    """Derive the tasks that link to this tracker by scanning the index.

    Reverse map is computed on demand — never stored on the tracker file.
    """
    out: list[dict[str, Any]] = []
    for epic in backlog_data.get("epics", []):
        for task in epic.get("tasks", []):
            if task.get("tracker_id") == tracker_id:
                out.append(task)
    return out


def linked_issues_for_tracker(
    backlog_data: dict[str, Any], tracker_id: str
) -> list[dict[str, Any]]:
    """Derive the issues that link to this tracker by scanning the index."""
    return [iss for iss in backlog_data.get("issues", []) if iss.get("tracker_id") == tracker_id]


# ── Linear config (linear-002) ─────────────────────────────────
#
# `.taskmaster/linear.yaml` declares Linear workspaces this project pushes to.
# Tokens are never stored; the file names env vars that hold them, so the
# committed config carries no secrets. Multi-workspace within a single project
# is allowed; each tracker remembers its `workspace_alias`.
#
# Shape:
#   workspaces:
#     - alias: cm
#       team_id: <linear-team-uuid>
#       token_env: TASKMASTER_LINEAR_TOKEN_CM
#       status_mapping: {todo: Todo, in-progress: In Progress, ...}
#       priority_mapping: {critical: 1, high: 2, medium: 3, low: 4}
#       user_mapping: {<TM-owner-string>: <linear-user-id>}
#       label_config: {tm_managed_prefix: "tm:"}
#   default_workspace: cm
#
# Validator catches missing required fields, duplicate aliases, duplicate
# token_envs, and dangling default_workspace references at load time so
# misconfigurations fail loud rather than silently routing pushes to the
# wrong workspace.


def linear_config_path(backlog_path: Path) -> Path:
    return backlog_path.parent / "linear.yaml"


def load_linear_config(backlog_path: Path) -> dict[str, Any] | None:
    """Load `.taskmaster/linear.yaml`. Returns None if the file is missing
    (Linear sync is opt-in per project). Raises ValueError on schema violation."""
    path = linear_config_path(backlog_path)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    _validate_linear_config(cfg)
    return cfg


def _validate_linear_config(cfg: dict[str, Any]) -> None:
    workspaces = cfg.get("workspaces")
    if not isinstance(workspaces, list) or not workspaces:
        raise ValueError("linear.yaml must declare at least one workspace under 'workspaces'")

    seen_aliases: set[str] = set()
    seen_token_envs: set[str] = set()
    for ws in workspaces:
        if not isinstance(ws, dict):
            raise ValueError(f"linear.yaml workspace entry must be a mapping, got {type(ws).__name__}")
        for field in ("alias", "team_id", "token_env"):
            if not ws.get(field):
                raise ValueError(f"linear.yaml workspace missing required field: {field}")
        alias = str(ws["alias"])
        if "-" in alias:
            # tracker_ids are "linear-<alias>-<key>" and parsed with a
            # bounded split; a hyphen in the alias makes that parse ambiguous
            # and routes pushes to the wrong (or no) workspace (B-032).
            raise ValueError(
                f"linear.yaml workspace alias {alias!r} must not contain '-' "
                f"(it breaks tracker_id parsing of 'linear-<alias>-<key>')"
            )
        if alias in seen_aliases:
            raise ValueError(f"linear.yaml workspace alias {alias!r} is duplicated")
        seen_aliases.add(alias)
        token_env = str(ws["token_env"])
        if token_env in seen_token_envs:
            raise ValueError(
                f"linear.yaml token_env {token_env!r} is duplicated across workspaces "
                f"(two workspaces would share the same token)"
            )
        seen_token_envs.add(token_env)

    default_ws = cfg.get("default_workspace")
    if default_ws and default_ws not in seen_aliases:
        raise ValueError(
            f"linear.yaml default_workspace {default_ws!r} references unknown workspace "
            f"(known aliases: {sorted(seen_aliases)})"
        )


def get_linear_workspace(cfg: dict[str, Any], alias: str | None = None) -> dict[str, Any]:
    """Resolve a workspace config by alias. If `alias` is None, use `default_workspace`.

    Raises ValueError if neither path yields a known workspace.
    """
    target = alias or cfg.get("default_workspace")
    if not target:
        raise ValueError(
            "no workspace alias passed and linear.yaml has no default_workspace; "
            "pass an explicit alias or set default_workspace"
        )
    for ws in cfg.get("workspaces", []):
        if ws.get("alias") == target:
            return ws
    raise ValueError(f"linear.yaml has no workspace with alias {target!r}")


def resolve_linear_token(workspace: dict[str, Any]) -> str:
    """Read the Linear API token from the env var named by `workspace.token_env`.

    The token is read at call time, never cached on disk. If the env var is
    missing the operator gets a canonical message pointing to the Linear
    API-key page; same shape as the Jira variant.
    """
    env_name = workspace.get("token_env")
    if not env_name:
        raise ValueError("workspace config missing token_env")
    token = os.environ.get(env_name)
    if not token:
        raise ValueError(
            f"${env_name} is not set.\n"
            f"Create a Linear personal API key at https://linear.app/settings/api "
            f"then export {env_name}=<token>."
        )
    return token


# ── Ideas ────────────────────────────────────────────────────

def idea_path(backlog_path: Path, idea_id: str) -> Path:
    return backlog_path.parent / "ideas" / f"{idea_id}.md"


def idea_dir(backlog_path: Path) -> Path:
    return backlog_path.parent / "ideas"


def ideas_index_path(backlog_path: Path) -> Path:
    return backlog_path.parent / "ideas" / "IDEAS.md"


def list_idea_ids(backlog_path: Path) -> list[str]:
    """List idea ids on disk, sorted numerically by trailing number."""
    d = idea_dir(backlog_path)
    if not d.exists():
        return []

    def _rank(p: Path) -> int:
        m = re.search(r"(\d+)$", p.stem)
        return int(m.group(1)) if m else -1

    files = sorted(d.glob("IDEA-*.md"), key=_rank)
    return [p.stem for p in files]


def next_idea_id(backlog_path: Path) -> str:
    """Allocate the next IDEA-NNN id (zero-padded, 3+ digits)."""
    existing = list_idea_ids(backlog_path)
    nums: list[int] = []
    for ident in existing:
        m = re.search(r"(\d+)$", ident)
        if m:
            nums.append(int(m.group(1)))
    n = (max(nums) + 1) if nums else 1
    return f"IDEA-{n:03d}"


# Required frontmatter fields validated on every write.
_IDEA_REQUIRED_FIELDS = ("id", "title", "created", "created_by")


def _validate_idea(fm: dict[str, Any]) -> None:
    """Raise ValueError if frontmatter violates idea invariants.

    Idea schema is intentionally minimal — only id/title/created/created_by
    are required. Everything else is optional, freeform passthrough.
    """
    for key in _IDEA_REQUIRED_FIELDS:
        if key not in fm or fm[key] in (None, ""):
            raise ValueError(f"idea field {key!r} is required")
    if not isinstance(fm.get("title"), str) or not fm["title"].strip():
        raise ValueError("idea title must be a non-empty string")


def _idea_index_line(fm: dict[str, Any]) -> str:
    """Render one IDEAS.md line for an idea record."""
    iid = fm["id"]
    title = fm["title"]
    created = fm.get("created", "")
    # "2026-05-09T14:30:00Z" → "2026-05-09 14:30"
    short = created[:16].replace("T", " ") if isinstance(created, str) else ""
    if fm.get("archived"):
        return f"- {short} — [{iid}]({iid}.md) — ~~{title}~~ _(archived)_"
    status = fm.get("status") or ""
    suffix = f" _({status})_" if status else ""
    return f"- {short} — [{iid}]({iid}.md) — {title}{suffix}"


def _read_ideas_index(backlog_path: Path) -> list[str]:
    """Return the data lines (non-header) of IDEAS.md, newest-first preserved."""
    p = ideas_index_path(backlog_path)
    if not p.exists():
        return []
    return [line for line in p.read_text(encoding="utf-8").splitlines() if line.startswith("- ")]


def _write_ideas_index(backlog_path: Path, lines: list[str]) -> None:
    """Write IDEAS.md with the canonical header + the supplied data lines."""
    p = ideas_index_path(backlog_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = "# Ideas\n\n" + "\n".join(lines) + ("\n" if lines else "")
    atomic_write(p, body)


def _index_upsert_line(lines: list[str], idea_id: str, new_line: str) -> list[str]:
    """Replace the line for `idea_id` if present; otherwise prepend (newest first)."""
    out: list[str] = []
    found = False
    for line in lines:
        if f"[{idea_id}](" in line:
            out.append(new_line)
            found = True
        else:
            out.append(line)
    if not found:
        out.insert(0, new_line)
    return out


def write_idea(
    backlog_path: Path,
    *,
    title: str,
    body: str = "",
    tags: list[str] | None = None,
    status: str = "",
    related_tasks: list[str] | None = None,
    related_issues: list[str] | None = None,
    created_by: str = "Claude",
    idea_id: str | None = None,
    tldr: str = "",
    tldr_autogen: bool = False,
) -> tuple[str, Path]:
    """Create a new idea file. Returns (id, path).

    All fields beyond `title` are optional. `created` is auto-stamped as
    ISO-8601 UTC. Side effect: appends/updates the IDEAS.md index line.
    """
    if not title or not title.strip():
        raise ValueError("idea title is required")
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if idea_id:
        # Caller-supplied id — overwrite-friendly, single attempt.
        iid = idea_id
        target = idea_path(backlog_path, iid)
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        # Race-safe id allocation: bump-and-retry until exclusive create
        # succeeds. Two concurrent writers can't both grab the same IDEA-NNN.
        idea_dir(backlog_path).mkdir(parents=True, exist_ok=True)
        for _ in range(64):
            candidate = next_idea_id(backlog_path)
            candidate_target = idea_path(backlog_path, candidate)
            try:
                candidate_target.touch(exist_ok=False)
                iid, target = candidate, candidate_target
                break
            except FileExistsError:
                continue
        else:
            raise RuntimeError("could not allocate IDEA-NNN id after 64 attempts")
    fm: dict[str, Any] = {
        "id": iid,
        "title": title.strip(),
        "created": created,
        "created_by": created_by or "Claude",
        "status": status or "",
        "tags": list(tags or []),
        "related_tasks": list(related_tasks or []),
        "related_issues": list(related_issues or []),
        "promoted_to": None,
        "archived": False,
        "tldr": tldr,
    }
    if tldr_autogen:
        fm["tldr_autogen"] = True
    _validate_idea(fm)
    write_task_file(target, fm, body)
    lines = _index_upsert_line(_read_ideas_index(backlog_path), iid, _idea_index_line(fm))
    _write_ideas_index(backlog_path, lines)
    return iid, target


def read_idea(backlog_path: Path, idea_id: str) -> tuple[dict[str, Any], str]:
    fm, body = read_task_file(idea_path(backlog_path, idea_id))
    return fm, body.rstrip("\n")


def update_idea(
    backlog_path: Path,
    idea_id: str,
    **updates: Any,
) -> tuple[dict[str, Any], str]:
    """Patch an idea's frontmatter and/or body. Returns (fm, body) post-write.

    Body is preserved unchanged unless `body=` is passed. The IDEAS.md line
    for this idea is rewritten in place to reflect the new title / status /
    archived flag.
    """
    target = idea_path(backlog_path, idea_id)
    if not target.exists():
        raise FileNotFoundError(f"Idea not found: {idea_id}")
    fm, body = read_idea(backlog_path, idea_id)
    new_body = updates.pop("body", body)
    # Pass-through merge — accepts None values for promoted_to (un-promote).
    for k, v in updates.items():
        fm[k] = v
    _validate_idea(fm)
    write_task_file(target, fm, new_body)
    lines = _index_upsert_line(_read_ideas_index(backlog_path), idea_id, _idea_index_line(fm))
    _write_ideas_index(backlog_path, lines)
    return fm, new_body


def list_ideas(
    backlog_path: Path,
    *,
    idea_id: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    archived: bool = False,
    related_task: str | None = None,
    related_issue: str | None = None,
    limit: int | None = None,
    summary: bool = True,
) -> list[dict[str, Any]]:
    """List ideas with optional filters.

    Default sort is newest-first by `created`. Pass `idea_id` to fetch one
    record (body always included). Without `idea_id`, `summary=True` (the
    default) omits the body to keep list payloads small; pass `summary=False`
    to include body on every entry — useful for callers that want to render
    detail without a second fetch (e.g. the viewer).

    Filters compose as AND. `archived` defaults to False; pass True to
    include archived ideas in the result set.
    """
    if idea_id:
        target = idea_path(backlog_path, idea_id)
        if not target.exists():
            return []
        fm, body = read_idea(backlog_path, idea_id)
        return [{**fm, "body": body}]

    out: list[dict[str, Any]] = []
    for iid in list_idea_ids(backlog_path):
        try:
            fm, body = read_idea(backlog_path, iid)
        except (OSError, ValueError):
            continue
        if not archived and fm.get("archived"):
            continue
        if status is not None and (fm.get("status") or "") != status:
            continue
        if tag is not None and tag not in (fm.get("tags") or []):
            continue
        if related_task is not None and related_task not in (fm.get("related_tasks") or []):
            continue
        if related_issue is not None and related_issue not in (fm.get("related_issues") or []):
            continue
        if summary:
            out.append(fm)
        else:
            out.append({**fm, "body": body})

    def _sort_key(e: dict[str, Any]) -> tuple[str, int]:
        iid = e.get("id", "")
        m = re.search(r"(\d+)$", iid)
        num = int(m.group(1)) if m else 0
        return (e.get("created", ""), num)

    out.sort(key=_sort_key, reverse=True)
    if limit is not None:
        out = out[: max(0, limit)]
    return out


# ── Notes (sticky) ────────────────────────────────────────────
# A note is the lightest entity: freeform markdown body + author + pin.
# No title, no status, no links. Viewer-created notes are author "user";
# MCP-created notes are author "claude". Archive = move to _archive/.

NOTE_AUTHORS = ("user", "claude")


def note_dir(backlog_path: Path) -> Path:
    return backlog_path.parent / "notes"


def note_archive_dir(backlog_path: Path) -> Path:
    return note_dir(backlog_path) / "_archive"


def note_path(backlog_path: Path, note_id: str, archived: bool = False) -> Path:
    base = note_archive_dir(backlog_path) if archived else note_dir(backlog_path)
    return base / f"{note_id}.md"


def _resolve_note_path(backlog_path: Path, note_id: str) -> Path:
    """Return the existing path for a note (live first, then archive)."""
    live = note_path(backlog_path, note_id)
    if live.exists():
        return live
    archived = note_path(backlog_path, note_id, archived=True)
    if archived.exists():
        return archived
    raise FileNotFoundError(f"Note not found: {note_id}")


def list_note_ids(backlog_path: Path, include_archived: bool = False) -> list[str]:
    """Note ids on disk, sorted numerically.

    Archived ids always counted by next_note_id; only returned here when
    include_archived=True.
    """

    def _rank(p: Path) -> int:
        m = re.search(r"(\d+)$", p.stem)
        return int(m.group(1)) if m else -1

    dirs = [note_dir(backlog_path)]
    if include_archived:
        dirs.append(note_archive_dir(backlog_path))
    out: list[Path] = []
    for d in dirs:
        if d.exists():
            out.extend(d.glob("NOTE-*.md"))
    return [p.stem for p in sorted(out, key=_rank)]


def next_note_id(backlog_path: Path) -> str:
    """Allocate the next NOTE-NNN id. Considers live AND archived notes so
    archiving never causes id reuse."""
    nums: list[int] = []
    for ident in list_note_ids(backlog_path, include_archived=True):
        m = re.search(r"(\d+)$", ident)
        if m:
            nums.append(int(m.group(1)))
    n = (max(nums) + 1) if nums else 1
    return f"NOTE-{n:03d}"


def _validate_note(fm: dict[str, Any], body: str) -> None:
    if not body or not body.strip():
        raise ValueError("note text is required")
    if fm.get("author") not in NOTE_AUTHORS:
        raise ValueError(f"note author must be one of {NOTE_AUTHORS}")


def write_note(
    backlog_path: Path,
    *,
    text: str,
    author: str = "claude",
    pinned: bool = False,
    note_id: str | None = None,
) -> tuple[str, Path]:
    """Create a sticky note. Returns (id, path). Body = the note text."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fm: dict[str, Any] = {
        "id": "PENDING",
        "author": author,
        "created": now,
        "updated": now,
        "pinned": bool(pinned),
        "archived": False,
        "archived_at": None,
    }
    _validate_note(fm, text)
    if note_id:
        nid = note_id
        target = note_path(backlog_path, nid)
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        note_dir(backlog_path).mkdir(parents=True, exist_ok=True)
        for _ in range(64):
            candidate = next_note_id(backlog_path)
            candidate_target = note_path(backlog_path, candidate)
            try:
                candidate_target.touch(exist_ok=False)
                nid, target = candidate, candidate_target
                break
            except FileExistsError:
                continue
        else:
            raise RuntimeError("could not allocate NOTE-NNN id after 64 attempts")
    fm["id"] = nid
    write_task_file(target, fm, text.strip())
    return nid, target


def read_note(backlog_path: Path, note_id: str) -> tuple[dict[str, Any], str]:
    fm, body = read_task_file(_resolve_note_path(backlog_path, note_id))
    return fm, body.rstrip("\n")


def update_note(
    backlog_path: Path,
    note_id: str,
    *,
    text: str | None = None,
    pinned: bool | None = None,
) -> tuple[dict[str, Any], str]:
    """Patch a note's text and/or pin state. Bumps `updated`. Author and
    created are immutable."""
    target = _resolve_note_path(backlog_path, note_id)
    fm, body = read_task_file(target)
    body = body.rstrip("\n")
    new_body = body if text is None else text.strip()
    if pinned is not None:
        fm["pinned"] = bool(pinned)
    fm["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _validate_note(fm, new_body)
    write_task_file(target, fm, new_body)
    return fm, new_body


def archive_note(backlog_path: Path, note_id: str) -> dict[str, Any]:
    """Archive a note: set flags and move the file to _archive/."""
    live = note_path(backlog_path, note_id)
    if not live.exists():
        raise FileNotFoundError(f"Note not found: {note_id}")
    fm, body = read_task_file(live)
    fm["archived"] = True
    fm["archived_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dest = note_path(backlog_path, note_id, archived=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    write_task_file(dest, fm, body.rstrip("\n"))
    live.unlink()
    return fm


def list_notes(backlog_path: Path, include_archived: bool = False) -> list[dict[str, Any]]:
    """All notes with bodies, pinned first then created desc (id desc tiebreak)."""
    out: list[dict[str, Any]] = []
    for nid in list_note_ids(backlog_path, include_archived=include_archived):
        try:
            fm, body = read_note(backlog_path, nid)
        except (OSError, ValueError):
            continue
        if not include_archived and fm.get("archived"):
            continue
        out.append({**fm, "body": body})

    def _num(n: dict[str, Any]) -> int:
        m = re.search(r"(\d+)$", n.get("id", ""))
        return int(m.group(1)) if m else 0

    # Numeric-id tiebreak prevents nondeterminism when created timestamps tie (1-s resolution).
    def _key(n: dict[str, Any]) -> tuple[str, int]:
        return (n.get("created", ""), _num(n))

    pinned = [n for n in out if n.get("pinned")]
    unpinned = [n for n in out if not n.get("pinned")]
    pinned.sort(key=_key, reverse=True)
    unpinned.sort(key=_key, reverse=True)
    return pinned + unpinned


# ── Areas ────────────────────────────────────────────────────────
# An Area is a long-lived subsystem/workstream (e.g. "desktop-app", "viewer")
# with NO status lifecycle — it doesn't finish, so it has no done/archived
# state. Epics and tasks reference an area by a plain `area` scalar field.
# Areas never archive; renaming happens through update_area.

_AREA_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def area_dir(backlog_path: Path) -> Path:
    return backlog_path.parent / "areas"


def area_path(backlog_path: Path, area_id: str) -> Path:
    return area_dir(backlog_path) / f"{area_id}.md"


def list_area_ids(backlog_path: Path) -> list[str]:
    d = area_dir(backlog_path)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.md"))


def _validate_area(fm: dict[str, Any]) -> None:
    area_id = fm.get("id", "")
    if not area_id or not _AREA_ID_RE.match(area_id):
        raise ValueError(
            f"area id must be lowercase kebab-case (e.g., 'desktop-app'), got `{area_id}`"
        )
    if not str(fm.get("name") or "").strip():
        raise ValueError("area name is required")
    if "status" in fm:
        raise ValueError(
            "areas have no status field — they are long-lived subsystems, not lifecycle-tracked"
        )


def write_area(backlog_path: Path, fm: dict[str, Any], body: str = "") -> Path:
    """Create a new Area sidecar file. Returns the written path.

    `fm` must already be fully assembled (id, name, description, anchors,
    created) by the caller. Raises ValueError if invalid or if the id is
    already taken.
    """
    _validate_area(fm)
    target = area_path(backlog_path, fm["id"])
    if target.exists():
        raise ValueError(f"area `{fm['id']}` already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    write_task_file(target, fm, body)
    return target


def read_area(backlog_path: Path, area_id: str) -> tuple[dict[str, Any], str]:
    target = area_path(backlog_path, area_id)
    if not target.exists():
        raise FileNotFoundError(f"Area not found: {area_id}")
    fm, body = read_task_file(target)
    return fm, body.rstrip("\n")


def update_area(
    backlog_path: Path, area_id: str, updates: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    """Patch one or more fields on an Area. `id` and `created` are immutable;
    `status` is rejected — areas have no status field."""
    target = area_path(backlog_path, area_id)
    if not target.exists():
        raise FileNotFoundError(f"Area not found: {area_id}")
    fm, body = read_task_file(target)
    body = body.rstrip("\n")
    if "status" in updates:
        raise ValueError(
            "areas have no status field — they are long-lived subsystems, not lifecycle-tracked"
        )
    for key in ("id", "created"):
        if key in updates:
            raise ValueError(f"area `{key}` is immutable")
    fm.update(updates)
    _validate_area(fm)
    write_task_file(target, fm, body)
    return fm, body


def list_areas(backlog_path: Path) -> list[dict[str, Any]]:
    """All areas with bodies, sorted by id."""
    out: list[dict[str, Any]] = []
    for aid in list_area_ids(backlog_path):
        try:
            fm, body = read_area(backlog_path, aid)
        except (OSError, ValueError):
            continue
        out.append({**fm, "body": body})
    out.sort(key=lambda a: a.get("id", ""))
    return out


# ── ViewerPrefs ────────────────────────────────────────────────

VIEWER_PREFS_SCHEMA_VERSION = 1

VIEWER_PREFS_DEFAULTS = {
    "schema_version": VIEWER_PREFS_SCHEMA_VERSION,
    "use_v3": False,          # serve v3 viewer shell at root when True
    "theme": "dark",          # dark | light
    "card_density": "full",   # full | minimal
    "zoom": 1.0,              # 1.5x baked into source CSS as of T2.24α
    "screens": {
        # Per-screen view toggles (Variant A / B). Default A everywhere except dashboard which has no B.
        "task_detail": {"view": "A"},
        "kanban":      {"view": "A"},
        "sessions":    {"view": "A"},   # diary | lanes | by_task; "A" maps to diary
        "issues":      {"view": "A"},   # hybrid | kanban | list
    },
    "dashboard": {
        # Widget catalog. Each entry: {id, type, size: small|medium|wide, rail: left|right|bottom, index: int}
        "layout": [],
    },
    "ui": {
        "sidebar_collapsed": False,   # icons-only sidebar when True
    },
    "kanban": {
        "filters": {           # last applied; restored on viewer open
            "priorities": [],
            "epics": [],
            "phase": None,
            "group_by": "status",
            "sort": {"by": "priority", "dir": "desc"},
            "search": "",
        },
        "collapsed_columns": [],   # column keys (status / phase id / epic id) currently collapsed
    },
    "issues": {
        "aging": {             # base period in days, severity-tiered
            "Critical": 14,
            "High": 30,
            "Medium": 60,
            "Low": 120,
        },
    },
}


def viewer_prefs_path() -> Path:
    return _resolve_artifact_root() / "viewer.json"

def load_viewer_prefs() -> dict:
    """Load viewer prefs, creating the file with defaults on first call.
    Unknown top-level keys are preserved across reads (forward-compat).
    Missing keys are filled from VIEWER_PREFS_DEFAULTS (deep-merged).
    """
    import json
    from copy import deepcopy
    p = viewer_prefs_path()
    if not p.exists():
        prefs = deepcopy(VIEWER_PREFS_DEFAULTS)
        atomic_write(p, json.dumps(prefs, indent=2))
        return prefs
    try:
        raw = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        # A corrupt prefs file must never take the viewer down (observed:
        # trailing garbage from a concatenation accident). Quarantine it and
        # start over from defaults.
        import sys
        print(f"[taskmaster] corrupt viewer.json ({exc}); resetting to defaults", file=sys.stderr)
        try:
            p.replace(p.with_suffix(".json.corrupt"))
        except OSError:
            pass
        prefs = deepcopy(VIEWER_PREFS_DEFAULTS)
        atomic_write(p, json.dumps(prefs, indent=2))
        return prefs

    # Deep-merge defaults under the loaded data so missing nested keys appear.
    def _merge(default, loaded):
        if isinstance(default, dict) and isinstance(loaded, dict):
            out = dict(loaded)  # preserve unknown keys
            for k, v in default.items():
                if k not in out:
                    out[k] = deepcopy(v)
                else:
                    out[k] = _merge(v, out[k])
            return out
        return loaded

    return _merge(VIEWER_PREFS_DEFAULTS, raw)

def save_viewer_prefs(prefs: dict) -> None:
    import json
    p = viewer_prefs_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps(prefs, indent=2))


def save_v3(backlog_path: Path, data: dict[str, Any]) -> None:
    """Save a v3 backlog: slim index → backlog.yaml; heavy fields → per-task files.

    Per-task files are written only when there is heavy content or a body.
    A task with all-empty heavy fields gets no file (keeps directory tidy).
    Existing per-task files for tasks that no longer have heavy content are
    left alone — explicit task deletion handles cleanup.
    """
    slim_data: dict[str, Any] = {**data}
    slim_data["epics"] = []
    for epic in data.get("epics", []):
        tasks = epic.get("tasks", [])
        epic_meta = {k: v for k, v in epic.items() if k != "tasks"}
        slim_meta, epic_heavy, epic_body = _split_entity_for_v3(epic_meta, EPIC_HEAVY_FIELDS)
        eid = slim_meta.get("id")
        if eid and (any(k in epic_heavy for k in EPIC_HEAVY_FIELDS) or epic_body):
            write_task_file(epic_file_path(backlog_path, eid), epic_heavy, epic_body)
            slim_epic = {**slim_meta, "tasks": []}
        else:
            # No per-epic file written (no id, or no heavy content): keep the
            # full meta inline so heavy fields are never silently dropped.
            # Delete any stale body file so a cleared last heavy field can't
            # resurrect on the next load. Guard on a truthy id — no None.md.
            if eid:
                _remove_entity_file(epic_file_path(backlog_path, eid))
            slim_epic = {**epic_meta, "tasks": []}
        for task in tasks:
            slim_task, heavy_fm, body = _split_task_for_v3(task)
            slim_epic["tasks"].append(slim_task)
            tid = slim_task.get("id")
            if not tid:
                continue
            if any(k in heavy_fm for k in HEAVY_FIELDS) or bool(body):
                write_task_file(task_file_path(backlog_path, tid), heavy_fm, body)
            else:
                # No heavy content: delete any stale per-task body file so a
                # cleared last heavy field can't resurrect on the next load.
                # tid is truthy here (guarded by `if not tid: continue` above).
                _remove_entity_file(task_file_path(backlog_path, tid))
        slim_data["epics"].append(slim_epic)

    if "phases" in slim_data:
        slim_phases: list[dict[str, Any]] = []
        for phase in data.get("phases", []):
            slim_phase, phase_heavy, phase_body = _split_entity_for_v3(phase, PHASE_HEAVY_FIELDS)
            pid = slim_phase.get("id")
            if pid and (any(k in phase_heavy for k in PHASE_HEAVY_FIELDS) or phase_body):
                write_task_file(phase_file_path(backlog_path, pid), phase_heavy, phase_body)
                slim_phases.append(slim_phase)
            else:
                # No per-phase file written: keep the full phase dict inline so
                # heavy fields are never silently dropped. Delete any stale body
                # file so a cleared last heavy field can't resurrect on the next
                # load. Guard on a truthy id — no None.md.
                if pid:
                    _remove_entity_file(phase_file_path(backlog_path, pid))
                slim_phases.append(phase)
        slim_data["phases"] = slim_phases

    atomic_write(
        backlog_path,
        yaml.dump(slim_data, default_flow_style=False, sort_keys=False, allow_unicode=True),
    )


# ---- Sessions ------------------------------------------------------------


def _parse_iso8601(s) -> "datetime":
    from datetime import datetime, timezone
    if isinstance(s, datetime):
        if s.tzinfo is None:
            return s.replace(tzinfo=timezone.utc)
        return s
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _handover_time(h: dict):
    """Return the best-available timestamp for a handover, as a tz-aware datetime.

    Prefers the full-precision `created` ISO timestamp written by the v3 handover
    skill. Falls back to `date` (which may be either a full ISO timestamp or a
    date-only string) for legacy handovers written before `created` was added.
    """
    raw = h.get("created") or h.get("date")
    if raw is None:
        raise ValueError(f"handover {h.get('id')!r} has neither 'created' nor 'date'")
    return _parse_iso8601(raw)


def list_sessions() -> list[dict]:
    """Synthesise sessions from on-disk handover files.

    Algorithm: load every handover, sort by date asc, then greedily group
    consecutive handovers that share at least one task_id AND occur within
    SESSION_GAP_MINUTES (default 30). Each group becomes one session.

    Returns: list of dicts (newest first):
      {id, start, end, duration, time_resolution, handover_ids[],
       task_ids[], parallel_with[]}

    `time_resolution` is "full" when at least one grouped handover carried a
    precise `created` ISO timestamp, else "date-only" — the viewer uses this to
    avoid fabricating a clock time from a midnight-UTC date.
    """
    from datetime import timedelta
    SESSION_GAP_MINUTES = 30
    handovers_dir = _resolve_artifact_root() / "handovers"
    if not handovers_dir.exists():
        return []
    raw: list[dict] = []
    for p in sorted(handovers_dir.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
            m = _MD_FRONTMATTER_RE.match(text)
            if not m:
                continue
            fm = yaml.safe_load(m.group(1)) or {}
            if "id" not in fm or ("date" not in fm and "created" not in fm):
                continue
            raw.append(fm)
        except Exception:
            continue
    raw.sort(key=lambda h: _handover_time(h))

    groups: list[list[dict]] = []
    for h in raw:
        h_t = _handover_time(h)
        h_tids = set(h.get("task_ids") or [])
        attached = False
        if groups:
            tail = groups[-1][-1]
            tail_t = _handover_time(tail)
            tail_tids = set(tail.get("task_ids") or [])
            within_gap = (h_t - tail_t) <= timedelta(minutes=SESSION_GAP_MINUTES)
            shared_tasks = bool(h_tids & tail_tids)
            if within_gap and shared_tasks:
                groups[-1].append(h)
                attached = True
        if not attached:
            groups.append([h])

    sessions: list[dict] = []
    for idx, group in enumerate(groups, start=1):
        sid = f"SES-{idx:04d}"
        start = _handover_time(group[0])
        end = _handover_time(group[-1])
        tids: list[str] = []
        for h in group:
            for t in (h.get("task_ids") or []):
                if t not in tids:
                    tids.append(t)
        # A session whose handovers all lack `created` only has date-level
        # resolution; the viewer should render the date without a time.
        time_resolution = "full" if any(h.get("created") for h in group) else "date-only"
        sessions.append({
            "id": sid,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "duration": int((end - start).total_seconds()),
            "time_resolution": time_resolution,
            "handover_ids": [h["id"] for h in group],
            "handovers": [
                {
                    "id": h["id"],
                    "status": h.get("status", "open"),
                    "viewer_kind": HANDOVER_KIND_TO_VIEWER_KIND.get(h.get("session_kind"), "standalone"),
                    "tldr": h.get("tldr", ""),
                }
                for h in group
            ],
            "task_ids": tids,
            "parallel_with": [],   # filled below
        })

    # Mark parallel sessions: any pair with overlapping [start,end] windows.
    # Windows are expanded by SESSION_GAP_MINUTES so that two single-handover
    # sessions that were close in time but different in task scope are flagged.
    from datetime import timedelta as _td
    _gap = _td(minutes=SESSION_GAP_MINUTES)
    for i, s in enumerate(sessions):
        s_start = _parse_iso8601(s["start"])
        s_end = _parse_iso8601(s["end"]) + _gap
        for j, o in enumerate(sessions):
            if i == j:
                continue
            o_start = _parse_iso8601(o["start"])
            o_end = _parse_iso8601(o["end"]) + _gap
            if s_start <= o_end and o_start <= s_end:
                s["parallel_with"].append(o["id"])

    sessions.sort(key=lambda s: s["start"], reverse=True)
    return sessions


def _load_handover_full(handover_id: str) -> dict | None:
    """Load a handover's frontmatter + body_md by id."""
    p = _resolve_artifact_root() / "handovers" / f"{handover_id}.md"
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    m = _MD_FRONTMATTER_RE.match(text)
    if not m:
        return None
    fm = yaml.safe_load(m.group(1)) or {}
    body = text[m.end():].strip()
    fm["resume_prompt"] = body          # body is the resume prompt artifact
    fm["viewer_kind"] = HANDOVER_KIND_TO_VIEWER_KIND.get(
        fm.get("session_kind"), "standalone"
    )
    return fm


def get_session_detail(session_id: str) -> dict | None:
    """Bundle one session with its handovers and task ids."""
    sessions = list_sessions()
    target = next((s for s in sessions if s["id"] == session_id), None)
    if target is None:
        return None
    handovers = []
    for hid in target["handover_ids"]:
        h = _load_handover_full(hid)
        if h is not None:
            handovers.append(h)
    return {
        "session": target,
        "handovers": handovers,
        "task_ids": target["task_ids"],
    }


def load_issue(issue_id: str) -> dict:
    """Load an issue by id from <backlog-parent>/issues/<id>.md in CWD.

    Returns a dict with frontmatter fields plus '_body'.
    """
    p = _resolve_artifact_root() / "issues" / f"{issue_id}.md"
    fm, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    fm["_body"] = body
    return fm


def list_issue_ids_cwd() -> list[str]:
    """List issue ids from <backlog-parent>/issues/ in the current working directory."""
    import re as _re
    d = _resolve_artifact_root() / "issues"
    if not d.exists():
        return []

    def _rank(p: Path) -> int:
        m = _re.search(r"(\d+)$", p.stem)
        return int(m.group(1)) if m else -1

    return [p.stem for p in sorted(d.glob("ISS-*.md"), key=_rank)]


SEVERITY_LABEL = {"P0": "Critical", "P1": "High", "P2": "Medium", "P3": "Low"}


def severity_label(p: str) -> str:
    """Map raw severity code to user-facing word."""
    return SEVERITY_LABEL.get(p, p)


def compute_issue_aging(issue: dict, aging_cfg: dict, now=None) -> dict:
    """Return {'percent': float, 'tier': 'Fresh'|'Aging'|'Stale'} given issue + cfg.

    Tier rules (per spec §3.14):
        Fresh:  0 <= pct < 25
        Aging: 25 <= pct < 60
        Stale: pct >= 60

    `percent` may exceed 100 for very stale issues; clamp at 200 for display.
    """
    from datetime import datetime, timezone

    if now is None:
        now = datetime.now(timezone.utc)

    label = severity_label(issue.get("severity", "P2"))
    base_days = float(aging_cfg.get(label, 60))
    discovered_raw = issue.get("discovered")
    if not discovered_raw:
        return {"percent": 0.0, "tier": "Fresh"}
    # Accept both ISO datetime ("YYYY-MM-DDTHH:MM:SSZ") and date-only
    # ("YYYY-MM-DD"); the writer's default emits date-only, so we must tolerate
    # it here. ISS-005.
    discovered = None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            discovered = datetime.strptime(discovered_raw, fmt).replace(tzinfo=timezone.utc)
            break
        except (ValueError, TypeError):
            continue
    if discovered is None:
        return {"percent": 0.0, "tier": "Fresh"}
    age_days = (now - discovered).total_seconds() / 86400.0

    pct = (age_days / base_days) * 100.0 if base_days > 0 else 0.0
    pct = max(0.0, min(pct, 200.0))
    if pct < 25:
        tier = "Fresh"
    elif pct < 60:
        tier = "Aging"
    else:
        tier = "Stale"
    return {"percent": pct, "tier": tier}


# ── Edit-in-UI write primitives (v3-edit Phase A) ──────────────────

import contextlib

_threadlocal_locks: dict[str, "threading.Lock"] = {}


@contextlib.contextmanager
def with_file_lock(path: Path):
    """Per-file mutex for write paths.

    Uses a `.lock` sidecar adjacent to the target file. Falls back to a
    threading-local lock if the `filelock` package isn't available — local
    use is single-process so this is acceptable; future cross-process
    safety lands when filelock becomes a hard dep.
    """
    try:
        from filelock import FileLock
        lock = FileLock(str(path) + ".lock", timeout=5)
        with lock:
            yield
    except ImportError:
        import threading
        lock = _threadlocal_locks.setdefault(str(path), threading.Lock())
        with lock:
            yield


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _find_task_in_yaml(data: dict, task_id: str) -> tuple[dict, dict] | None:
    """Return (epic_dict, task_dict) for a v2-nested layout, or None."""
    for epic in data.get("epics") or []:
        for t in epic.get("tasks") or []:
            if t.get("id") == task_id:
                return epic, t
    return None


def update_task(task_id: str, patch: dict, backlog_path: Path | None = None) -> dict:
    """Apply a partial update to a task. Returns the new task dict.

    - Auto-stamps `started` on first transition into `in-progress` (or any
      non-todo state from `todo`).
    - Auto-stamps `completed` on transition into `done`.
    - Never overwrites `started`/`completed` once set.
    """
    bp = backlog_path or _resolve_backlog_path()
    with with_file_lock(bp):
        data = yaml.safe_load(bp.read_text(encoding="utf-8")) or {}
        found = _find_task_in_yaml(data, task_id)
        if found is None:
            raise KeyError(f"task {task_id} not found")
        epic, task = found
        before_status = task.get("status")
        for k, v in patch.items():
            task[k] = v
        after_status = task.get("status")
        if after_status != before_status:
            if after_status == "in-progress" and not task.get("started"):
                task["started"] = _now_iso()
            if after_status == "done" and not task.get("completed"):
                task["completed"] = _now_iso()
        task["last_referenced"] = _now_iso()
        atomic_write(bp, yaml.safe_dump(data, sort_keys=False))
        return dict(task)


def create_task(payload: dict, backlog_path: Path | None = None) -> str:
    """Create a new task under the given epic. Returns assigned id."""
    bp = backlog_path or _resolve_backlog_path()
    epic_id = payload.get("epic")
    if not epic_id:
        raise ValueError("epic is required")
    with with_file_lock(bp):
        data = yaml.safe_load(bp.read_text(encoding="utf-8")) or {}
        epic = next((e for e in (data.get("epics") or []) if e.get("id") == epic_id), None)
        if epic is None:
            raise KeyError(f"epic {epic_id} not found")
        existing_ids = {t.get("id") for t in (epic.get("tasks") or [])}
        # Generate next id like e1-002.
        n = 1
        while f"{epic_id}-{n:03d}" in existing_ids:
            n += 1
        new_id = f"{epic_id}-{n:03d}"
        new_task = {
            "id": new_id,
            "title": payload.get("title", ""),
            "status": payload.get("status", "todo"),
            "priority": payload.get("priority", "medium"),
            "created": _now_iso(),
            "last_referenced": _now_iso(),
        }
        # Pass through other supplied fields (phase, anchors, depends_on, etc).
        for k, v in payload.items():
            if k not in ("epic", "id"):
                new_task[k] = v
        epic.setdefault("tasks", []).append(new_task)
        atomic_write(bp, yaml.safe_dump(data, sort_keys=False))
        return new_id


def archive_task(task_id: str, backlog_path: Path | None = None) -> None:
    """Soft-delete: set status to 'archived'. The existing
    backlog_archive_task MCP tool already does this for v2 backlogs;
    we mirror the behavior here so HTTP shares the code path."""
    update_task(task_id, {"status": "archived"}, backlog_path=backlog_path)


def _resolve_backlog_path() -> Path:
    """Lazy import of backlog_server's resolver to avoid circular import."""
    from taskmaster.backlog_server import _backlog_path
    return _backlog_path()


def validate_task_write(task_id: str, patch: dict, backlog_path: Path | None = None) -> dict[str, str]:
    """Run cross-entity validation for a proposed task write.

    Returns a dict { field: error_message }. Empty dict means valid.
    Pure function — does not persist.
    """
    bp = backlog_path or _resolve_backlog_path()
    data = yaml.safe_load(bp.read_text(encoding="utf-8")) or {}
    errors: dict[str, str] = {}

    # Build helper maps.
    epic_ids = {e.get("id") for e in (data.get("epics") or []) if e.get("id")}
    phase_ids = {p.get("id") for p in (data.get("phases") or []) if p.get("id")}
    all_tasks: list[dict] = []
    for e in data.get("epics") or []:
        for t in e.get("tasks") or []:
            all_tasks.append(t)
    task_ids = {t.get("id") for t in all_tasks if t.get("id")}

    # Locate the task being patched.
    me = next((t for t in all_tasks if t.get("id") == task_id), None)
    if me is None and task_id != "<new>":
        errors["_task"] = f"task {task_id} not found"
        return errors

    # Compose the proposed state.
    proposed = {**(me or {}), **patch}  # noqa: F841 — kept for future cross-field rules

    # Epic must exist.
    if "epic" in patch and patch["epic"] and patch["epic"] not in epic_ids:
        errors["epic"] = f"unknown epic: {patch['epic']}"

    # Area must exist (areas live in files, not `data`).
    if "area" in patch and patch["area"] and patch["area"] not in list_area_ids(bp):
        errors["area"] = f"unknown area: {patch['area']}"

    # Phase must exist if set.
    if "phase" in patch and patch["phase"] and patch["phase"] not in phase_ids:
        errors["phase"] = f"unknown phase: {patch['phase']}"

    # Deps: each must exist; no self-dep; no cycle.
    if "depends_on" in patch:
        deps = patch["depends_on"] or []
        for d in deps:
            if d == task_id:
                errors["depends_on"] = "cannot depend on itself"
                break
            if d not in task_ids:
                errors["depends_on"] = f"unknown task in depends_on: {d}"
                break
        if "depends_on" not in errors:
            # Cycle detection: BFS from each dep — if any path reaches task_id, cycle.
            adj = {t.get("id"): list(t.get("depends_on") or []) for t in all_tasks if t.get("id")}
            adj[task_id] = list(deps)  # simulate the proposed state
            if _has_cycle_to(adj, task_id):
                errors["depends_on"] = "introduces a dependency cycle"

    return errors


def _has_cycle_to(adj: dict, target: str) -> bool:
    """True if `target` is reachable from any of its own deps under `adj`."""
    seen = set()
    stack = list(adj.get(target, []))
    while stack:
        cur = stack.pop()
        if cur == target:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(adj.get(cur, []))
    return False


def compute_etag(path: Path) -> str:
    """Stable, cheap ETag derived from file mtime + content hash.

    Returns an 16-hex-char string suitable for HTTP ETag headers.
    """
    if not path.exists():
        return ""
    st = path.stat()
    h = hashlib.sha1()
    h.update(str(st.st_mtime_ns).encode())
    # Also hash content so two writes with identical content (e.g. same byte
    # body) collapse to the same etag — desirable for cache stability.
    h.update(path.read_bytes())
    return h.hexdigest()[:16]


# ----------------------------------------------------------------------------
# Continuity adapter — projects all entities onto a single ContinuityItem shape.
# ----------------------------------------------------------------------------

CONTINUITY_TYPES = ("decision", "handover", "task", "branch", "idea", "issue")
ACTION_CLASSES = ("decide", "resume", "review", "clean-up", "ambient")


def _age_days(iso_ts: "str | datetime | date | None", now: datetime | None = None) -> float:
    if not iso_ts:
        return 0.0
    if isinstance(iso_ts, datetime):
        ts = iso_ts
    elif isinstance(iso_ts, date):
        ts = datetime(iso_ts.year, iso_ts.month, iso_ts.day, tzinfo=timezone.utc)
    else:
        try:
            ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return 0.0
    now = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() / 86400.0


RESUME_RECENT_DONE_CAP = 5


def _handover_to_item(fm: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    age = _age_days(fm.get("created"), now)
    status = fm.get("status", "open")
    # Open handovers (any age) surface as resume; closed / superseded default to
    # ambient. The collection step promotes the most-recent N closed handovers
    # to resume via RESUME_RECENT_DONE_CAP so the rail still shows a usable
    # recent history. (Plan B's age<=7 cutoff was dropped during the master
    # merge in favor of the looser 3.3.0 continuity polish rule.)
    if status == "open":
        action_class = "resume"
    else:
        action_class = "ambient"
    task_ids = fm.get("task_ids") or []
    return {
        "id": fm.get("id"),
        "type": "handover",
        "title": fm.get("tldr") or "",
        "where": fm.get("branch") or (task_ids[0] if task_ids else ""),
        "next": fm.get("next_action") or "",
        "action_class": action_class,
        "status": status,
        "timestamp": fm.get("created") or fm.get("date") or "",
        "age_days": age,
        "task_id": task_ids[0] if task_ids else None,
        "branch": fm.get("branch"),
    }


def _decision_to_item(fm: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    age = _age_days(fm.get("created_at"), now)
    status = fm.get("status", "open")
    action_class = "decide" if status == "open" else "ambient"
    rec = fm.get("recommendation")
    rec_str = ""
    if rec and fm.get("options"):
        try:
            rec_str = f"rec: {fm['options'][int(rec) - 1]}"
        except (IndexError, ValueError):
            rec_str = ""
    return {
        "id": fm.get("id"),
        "type": "decision",
        "title": fm.get("title") or "",
        "where": fm.get("task_id") or fm.get("branch") or "",
        "next": rec_str or f"{len(fm.get('options') or [])} options",
        "action_class": action_class,
        "timestamp": fm.get("created_at") or "",
        "age_days": age,
        "task_id": fm.get("task_id"),
        "branch": fm.get("branch"),
    }


def _task_to_item(task: dict[str, Any], task_id: str, now: datetime | None = None) -> dict[str, Any]:
    last = task.get("last_referenced") or task.get("started") or task.get("created")
    age = _age_days(last, now)
    status = task.get("status", "todo")
    if status == "in-review":
        action_class = "review"
    elif status == "in-progress" and age <= 3:
        action_class = "resume"
    elif status == "in-progress" and age >= 7:
        action_class = "clean-up"
    else:
        action_class = "ambient"
    return {
        "id": task_id,
        "type": "task",
        "title": task.get("title") or task_id,
        "where": task.get("epic") or "",
        "next": task.get("status") or "",
        "action_class": action_class,
        "timestamp": last or "",
        "age_days": age,
        "task_id": task_id,
        "branch": task.get("branch") or "",
    }


def _issue_to_item(issue: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    sev = issue.get("severity", "P3")
    age = _age_days(issue.get("discovered"), now)
    status = issue.get("status", "open")
    if status != "open":
        action_class = "ambient"
    elif sev in ("P0", "P1"):
        action_class = "review"
    elif age >= 14:
        action_class = "clean-up"
    else:
        action_class = "ambient"
    return {
        "id": issue.get("id"),
        "type": "issue",
        "title": issue.get("title") or "",
        "where": ",".join(issue.get("components") or []),
        "next": f"{sev} · {status}",
        "action_class": action_class,
        "timestamp": issue.get("discovered") or "",
        "age_days": age,
        "task_id": None,
        "branch": None,
    }


def _idea_to_item(idea: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    age = _age_days(idea.get("created"), now)
    status = idea.get("status", "raw")
    action_class = "clean-up" if status == "brainstorm" and age >= 7 else "ambient"
    return {
        "id": idea.get("id"),
        "type": "idea",
        "title": idea.get("title") or "",
        "where": status,
        "next": status,
        "action_class": action_class,
        "timestamp": idea.get("created") or "",
        "age_days": age,
        "task_id": None,
        "branch": None,
    }


def continuity_items(
    backlog_path: Path,
    *,
    include_auto_stage: bool = False,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Project all backlog entities to a unified ContinuityItem list."""
    items: list[dict[str, Any]] = []

    # Handovers. Promote the most-recent N done handovers to 'resume' so the
    # rail surfaces useful recent history alongside currently-open ones.
    handover_items: list[dict[str, Any]] = []
    for hid in list_handover_ids(backlog_path):
        try:
            fm, _ = read_handover(backlog_path, hid)
        except (OSError, ValueError):
            continue
        if not include_auto_stage and fm.get("session_kind") == "auto-stage":
            continue
        handover_items.append(_handover_to_item(fm, now))
    handover_items.sort(key=lambda it: it.get("timestamp") or "", reverse=True)
    done_promoted = 0
    for it in handover_items:
        if it.get("status") == "closed" and done_promoted < RESUME_RECENT_DONE_CAP:
            it["action_class"] = "resume"
            done_promoted += 1
    items.extend(handover_items)

    # Decisions.
    for did in list_decision_ids(backlog_path):
        try:
            fm, _ = read_decision(backlog_path, did)
        except (OSError, ValueError):
            continue
        items.append(_decision_to_item(fm, now))

    # Tasks (from backlog.yaml epics).
    try:
        data = yaml.safe_load(backlog_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        data = {}
    for epic in data.get("epics", []) or []:
        for t in epic.get("tasks", []) or []:
            tid = t.get("id")
            if not tid:
                continue
            items.append(_task_to_item(t, tid, now))

    # Issues.
    for iid in list_issue_ids(backlog_path):
        try:
            fm, _ = read_issue(backlog_path, iid)
        except (OSError, ValueError):
            continue
        items.append(_issue_to_item(fm, now))

    # Ideas.
    for idid in list_idea_ids(backlog_path):
        try:
            fm, _ = read_idea(backlog_path, idid)
        except (OSError, ValueError):
            continue
        items.append(_idea_to_item(fm, now))

    return items


# ── Plan C: typed-link dispatchers, symmetric sync, auto-link ────

# Per-kind path helpers used by the dispatcher. They all live next to
# backlog.yaml under <kind>s/<id>.md.
_ENTITY_PATH_HELPERS: dict[str, Any] = {
    "handover": handover_path,
    "issue":    issue_path,
    "idea":     idea_path,
}


def read_entity_anywhere(
    backlog_path: Path,
    entity_id: str,
    *,
    fallback: bool = True,
) -> dict | None:
    """Read any entity (task/issue/handover/idea) by ID. Returns its
    in-memory dict (frontmatter for non-task entities; merged dict for tasks).

    Body content for non-task entities is stored under BODY_KEY for round-trip.
    Returns None when the entity is unknown.

    Read-fallback: when fallback=True (default), synthesizes a virtual `links`
    array from legacy fields when no `links` is present (unmigrated projects).
    The fallback is read-only — does not write back. Pass fallback=False to
    get the raw entity (used by the migration script).
    """
    kind = entity_kind_of(entity_id)
    if kind is None:
        return None
    if kind == "task":
        data = load_v3(backlog_path)
        for epic in data.get("epics", []):
            for task in epic.get("tasks", []):
                if task.get("id") == entity_id:
                    if fallback:
                        _fallback_links_if_absent(task, "task")
                    return task
        return None
    reader = {
        "handover": read_handover,
        "issue":    read_issue,
        "idea":     read_idea,
    }[kind]
    try:
        fm, body = reader(backlog_path, entity_id)
    except FileNotFoundError:
        return None
    fm = dict(fm)
    if body:
        fm[BODY_KEY] = body
    if fallback:
        _fallback_links_if_absent(fm, kind)
    return fm


def write_entity_anywhere(backlog_path: Path, entity: dict) -> None:
    """Persist an entity's frontmatter + body via the right writer.

    For tasks: round-trips through load_v3/save_v3 so the slim index stays
    consistent. For non-task entities: writes the per-entity markdown file
    via write_task_file (path lookup by kind). The body is read from
    entity[BODY_KEY] (popped to avoid persisting that key as frontmatter).
    """
    entity_id = entity.get("id")
    kind = entity_kind_of(entity_id)
    if kind is None:
        raise ValueError(f"unknown entity kind for id={entity_id!r}")
    if kind == "task":
        data = load_v3(backlog_path)
        for epic in data.get("epics", []):
            tasks = epic.get("tasks", [])
            for i, task in enumerate(tasks):
                if task.get("id") == entity_id:
                    # Strip body-key before persisting (save_v3 routes it through
                    # _split_task_for_v3 which already understands BODY_KEY).
                    tasks[i] = dict(entity)
                    save_v3(backlog_path, data)
                    return
        raise KeyError(f"task {entity_id!r} not found")
    # Non-task: split frontmatter vs body, then write via the path helper.
    fm = dict(entity)
    body = fm.pop(BODY_KEY, "") or ""
    path_helper = _ENTITY_PATH_HELPERS[kind]
    target = path_helper(backlog_path, entity_id)
    write_task_file(target, fm, body)


def sync_inverse(
    backlog_path: Path,
    source: str,
    target: str,
    type: str,
    *,
    remove: bool = False,
) -> None:
    """Write (or remove) the inverse link on the target entity.

    Used by `backlog_link_create`/`backlog_link_remove` to keep both sides in
    lockstep. Raises KeyError if the target entity does not exist (caller
    decides whether to surface the error or treat it as a soft warning).
    """
    if type not in REVERSE_TYPE:
        raise ValueError(f"unknown link type {type!r}")
    target_entity = read_entity_anywhere(backlog_path, target)
    if target_entity is None:
        raise KeyError(f"target entity {target!r} not found")
    inverse_type = REVERSE_TYPE[type]
    if remove:
        changed = remove_link(target_entity, inverse_type, source)
    else:
        changed = add_link(target_entity, inverse_type, source)
    if changed:
        write_entity_anywhere(backlog_path, target_entity)


def auto_link_on_save(backlog_path: Path, entity_id: str) -> list[str]:
    """Scan an entity's body and add `references` links for inline mentions.

    Rules (spec §6C):
      - Skip when entity frontmatter has `auto_link: false`.
      - Add a `references` link for each unique mention not already linked.
      - Existing explicit link types (anything stronger than `references`)
        are never overwritten — auto-detection only adds NEW targets, never
        modifies existing links regardless of type strength.
      - Self-references are skipped.
      - Targets that don't exist are skipped (logged via return value).
      - Writes the inverse `referenced_by` on each target.

    For tasks (no markdown body in the traditional sense), the scan
    concatenates `notes` + `review_instructions` fields as a synthetic body.

    Returns the list of newly-added target IDs.
    """
    entity = read_entity_anywhere(backlog_path, entity_id)
    if entity is None:
        return []
    if entity.get("auto_link") is False:
        return []

    if entity_kind_of(entity_id) == "task":
        body = "\n\n".join(filter(None, [
            entity.get(BODY_KEY) or "",
            entity.get("notes") or "",
            entity.get("review_instructions") or "",
        ]))
    else:
        body = entity.get(BODY_KEY, "") or ""

    refs = extract_inline_refs(body, self_id=entity_id)
    if not refs:
        return []

    existing_targets = {link["target"] for link in entity_links(entity)}
    added: list[str] = []
    for target_id in refs:
        if target_id in existing_targets:
            # Any link to this target already exists; auto-detection only adds
            # new targets, never modifies existing links (regardless of type
            # strength). This protects stronger explicit relations.
            continue
        # Confirm target exists; skip silently if not.
        target_entity = read_entity_anywhere(backlog_path, target_id)
        if target_entity is None:
            continue
        add_link(entity, "references", target_id)
        added.append(target_id)

    if added:
        write_entity_anywhere(backlog_path, entity)
        for target_id in added:
            try:
                sync_inverse(backlog_path, source=entity_id,
                             target=target_id, type="references")
            except KeyError:
                pass
    return added
