# User intent: give the agent a derived, disposable SQLite index of the whole backlog
# (tasks, bugs, issues, handovers, decisions, ideas) so file paths, links and text can be
# queried fast. Files stay canonical — deleting index.db must always be safe.
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

import yaml

from taskmaster.taskmaster_v3 import (
    REVERSE_TYPE,
    SCHEMA_V4,
    detect_schema_version,
    load_v3,
    load_v4,
    parse_frontmatter,
)

SCHEMA_VERSION = 2
DB_RELPATH = Path("local") / "index.db"
# The MCP server rebuilds inside `_load()` while hooks and tools read; WAL lets
# those readers through, and the timeout absorbs the brief write-lock overlaps.
BUSY_TIMEOUT_MS = 2000

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sources(file TEXT PRIMARY KEY, mtime REAL, size INTEGER, kind TEXT);
CREATE TABLE IF NOT EXISTS entities(id TEXT PRIMARY KEY, kind TEXT, status TEXT, title TEXT, epic TEXT,
  phase TEXT, lane TEXT, repo TEXT, priority TEXT, created TEXT, updated TEXT,
  archived INTEGER DEFAULT 0, file TEXT);
CREATE TABLE IF NOT EXISTS entity_paths(entity_id TEXT, path TEXT, match_kind TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS links(src TEXT, type TEXT, dst TEXT, derived INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS handovers(id TEXT PRIMARY KEY, thread TEXT, tldr TEXT, next_action TEXT,
  session_kind TEXT, branch TEXT, tip_commit TEXT, supersedes TEXT);
CREATE TABLE IF NOT EXISTS handover_tasks(handover_id TEXT, task_id TEXT);
CREATE TABLE IF NOT EXISTS related(a TEXT, b TEXT, via TEXT, weight INTEGER);
CREATE VIRTUAL TABLE IF NOT EXISTS entity_fts USING fts5(id UNINDEXED, kind UNINDEXED, title, body,
  tokenize='porter unicode61');
CREATE INDEX IF NOT EXISTS ix_paths_path ON entity_paths(path);
CREATE INDEX IF NOT EXISTS ix_entities_kind_status ON entities(kind, status);
CREATE INDEX IF NOT EXISTS ix_entities_repo ON entities(repo);
CREATE UNIQUE INDEX IF NOT EXISTS ux_links ON links(src, type, dst);
CREATE INDEX IF NOT EXISTS ix_links_dst ON links(dst);
CREATE INDEX IF NOT EXISTS ix_related_a ON related(a);
"""

TABLES = (
    "meta", "sources", "entities", "entity_paths", "links",
    "handovers", "handover_tasks", "related", "entity_fts",
)

# (kind, glob relative to the artifact root). Order is the build order, so the
# backlog index — which carries every task and epic — is always ingested first.
SOURCE_GLOBS: tuple[tuple[str, str], ...] = (
    ("backlog", "backlog.yaml"),
    ("project", "project.yaml"),
    ("task", "tasks/*.md"),
    ("task", "tasks/archive/*.md"),
    ("epic", "epics/*.md"),
    ("phase", "phases/*.md"),
    ("bug", "bugs/*.md"),
    ("bug", "bugs/archive/*.md"),
    ("issue", "issues/*.md"),
    ("handover", "handovers/*.md"),
    ("handover", "handovers/archive/*.md"),
    ("decision", "decisions/*.md"),
    ("idea", "ideas/*.md"),
)

_CODE_EXTS = (
    "py", "ts", "tsx", "js", "jsx", "cs", "csproj", "md", "yaml", "yml", "json", "toml",
    "html", "css", "scss", "sql", "sh", "ps1", "cpp", "h", "hpp", "c", "rs", "go",
    "java", "kt", "swift", "uasset", "ini", "cfg", "txt",
)
# Longest alternative first plus a trailing word guard, so `page.tsx` cannot be
# truncated to `page.ts`, `x.csproj` to `x.cs`, or `abc.jsonl` to `abc.json`.
_EXT_ALT = "|".join(sorted(_CODE_EXTS, key=lambda e: (-len(e), e)))
_PROSE_PATH_RE = re.compile(
    r"(?<![\w:/\\])((?:[A-Za-z0-9_.-]+/){1,}[A-Za-z0-9_.-]+\.(?:" + _EXT_ALT
    + r"))(?![A-Za-z0-9])(?::\d+)?"
)
# Transcript paths are never project sources. Dropped explicitly rather than by
# relying on `jsonl` being absent from the extension list.
_EXCLUDED_PATH_SUFFIXES = (".jsonl",)
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S*")
_LINE_SUFFIX_RE = re.compile(r":\d+$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:$")


@dataclass
class IndexReport:
    built_at: str
    full_rebuild: bool
    files_ingested: int
    pending_files: list[str] = field(default_factory=list)
    stale: bool = False
    row_counts: dict[str, int] = field(default_factory=dict)
    elapsed_ms: int = 0
    errors: list[str] = field(default_factory=list)


# ── Path helpers ────────────────────────────────────────────────


def resolve_backlog_path(target: Path) -> Path:
    """Accept a project root, a `.taskmaster/` dir, or a backlog.yaml path."""
    target = Path(target)
    if target.is_file():
        return target
    if (target / ".taskmaster" / "backlog.yaml").exists():
        return target / ".taskmaster" / "backlog.yaml"
    return target / "backlog.yaml"


def db_path(backlog_path: Path) -> Path:
    """Location of the derived index for the backlog at `backlog_path`."""
    return Path(backlog_path).parent / DB_RELPATH


def normalize_task_anchor(anchor: str, sub_repo: str | None) -> tuple[str, str]:
    """Normalize a task anchor to (project-root-relative path, 'exact'|'glob')."""
    p = str(anchor).replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if sub_repo and not p.startswith(f"{sub_repo}/"):
        p = f"{sub_repo}/{p}"
    if p.endswith("/"):
        return p + "**", "glob"
    if "*" in p or "?" in p:
        return p, "glob"
    return p, "exact"


def normalize_location(loc: str) -> str:
    """Normalize a bug/issue `location` entry: drop `:LINE`, `\\`→`/`, leading `./`."""
    p = str(loc).replace("\\", "/")
    p = _LINE_SUFFIX_RE.sub("", p)
    while p.startswith("./"):
        p = p[2:]
    return p


def extract_prose_paths(text: str) -> list[str]:
    """Repo-relative code paths mentioned in prose, unique, first-appearance order.

    URLs are stripped before matching so a host+path (`x.y/a/b.py`) can never be
    mistaken for a repo path, and absolute Windows paths are dropped.
    """
    if not text:
        return []
    stripped = _URL_RE.sub(" ", text)
    out: list[str] = []
    seen: set[str] = set()
    for m in _PROSE_PATH_RE.finditer(stripped):
        p = m.group(1)
        if "://" in p or p.endswith(_EXCLUDED_PATH_SUFFIXES):
            continue
        first = p.split("/", 1)[0]
        if _WINDOWS_DRIVE_RE.match(first) or first == "Users":
            continue
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def infer_repo(paths: list[str], repos: list[tuple[str, str]]) -> str | None:
    """Name of the repo whose (longest) path prefix contains one of `paths`."""
    best_name: str | None = None
    best_len = -1
    for name, prefix in repos:
        if not prefix:
            continue
        if any(p.startswith(prefix + "/") for p in paths) and len(prefix) > best_len:
            best_name, best_len = name, len(prefix)
    return best_name


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a path glob to an anchored regex (`**` crosses `/`, `*` does not)."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("".join(out) + r"\Z")


# ── Loading ─────────────────────────────────────────────────────


def load_backlog_data(backlog_path: Path) -> dict:
    """Load a backlog, dispatching v3/v4 without importing the MCP server."""
    backlog_path = Path(backlog_path)
    raw = yaml.safe_load(backlog_path.read_text(encoding="utf-8")) or {}
    if detect_schema_version(raw) >= SCHEMA_V4:
        return load_v4(backlog_path)
    return load_v3(backlog_path)


def _load_repos(backlog_path: Path) -> list[tuple[str, str]]:
    """`[(name, normalized path prefix)]` from project.yaml, empty when absent."""
    pf = Path(backlog_path).parent / "project.yaml"
    if not pf.exists():
        return []
    try:
        raw = yaml.safe_load(pf.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    repos: list[tuple[str, str]] = []
    for entry in raw.get("repos") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        prefix = str(entry.get("path") or "").replace("\\", "/")
        while prefix.startswith("./"):
            prefix = prefix[2:]
        prefix = prefix.rstrip("/")
        if name and prefix:
            repos.append((str(name), prefix))
    return repos


# ── Database open / rebuild ─────────────────────────────────────


def open_ro(backlog_path: Path) -> sqlite3.Connection:
    """Open the derived index read-only. Raises FileNotFoundError if missing."""
    p = db_path(backlog_path)
    if not p.exists():
        raise FileNotFoundError(f"index database not found: {p}")
    con = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
    con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return con


def last_report(backlog_path: Path) -> IndexReport | None:
    """The report written by the most recent build, or None."""
    try:
        con = open_ro(backlog_path)
    except (FileNotFoundError, sqlite3.DatabaseError):
        return None
    try:
        row = con.execute("select value from meta where key='last_report'").fetchone()
    except sqlite3.DatabaseError:
        return None
    finally:
        con.close()
    if not row:
        return None
    try:
        return IndexReport(**json.loads(row[0]))
    except (TypeError, ValueError):
        return None


def _open_build_db(path: Path, *, force_full: bool = False) -> tuple[sqlite3.Connection, bool]:
    """Open (or recreate) the index for writing. Returns (connection, full_rebuild)."""
    full = force_full or not path.exists()
    if path.exists() and not force_full:
        probe = sqlite3.connect(path)
        try:
            row = probe.execute("select value from meta where key='schema_version'").fetchone()
            if row is None or str(row[0]) != str(SCHEMA_VERSION):
                full = True
        except sqlite3.DatabaseError:
            full = True
        finally:
            probe.close()
    if full:
        for leftover in (path, path.with_suffix(".db-wal"), path.with_suffix(".db-shm")):
            leftover.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA_SQL)
    return con, full


def _ensure_local_dir(local: Path) -> None:
    local.mkdir(parents=True, exist_ok=True)
    gi = local / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")


# ── Row writers ─────────────────────────────────────────────────


def _delete_entity(con: sqlite3.Connection, eid: str) -> None:
    con.execute("delete from entities where id=?", (eid,))
    con.execute("delete from entity_paths where entity_id=?", (eid,))
    # Only rows this entity declared; inbound rows belong to the other file.
    con.execute("delete from links where src=? and derived=0", (eid,))
    con.execute("delete from handovers where id=?", (eid,))
    con.execute("delete from handover_tasks where handover_id=?", (eid,))
    con.execute("delete from entity_fts where id=?", (eid,))
    con.execute("delete from related where a=? or b=?", (eid, eid))


def _put_entity(con: sqlite3.Connection, row: dict[str, Any]) -> None:
    cols = ("id", "kind", "status", "title", "epic", "phase", "lane", "repo",
            "priority", "created", "updated", "archived", "file")
    con.execute(
        f"insert or replace into entities({','.join(cols)}) values ({','.join('?' * len(cols))})",
        tuple(row.get(c) for c in cols),
    )


def _put_paths(con: sqlite3.Connection, eid: str, entries: list[tuple[str, str, str]]) -> None:
    seen: set[str] = set()
    for path, match_kind, source in entries:
        if not path or path in seen:
            continue
        seen.add(path)
        con.execute(
            "insert into entity_paths(entity_id, path, match_kind, source) values (?,?,?,?)",
            (eid, path, match_kind, source),
        )


def _put_link(con: sqlite3.Connection, src: str, ltype: str, dst: str) -> None:
    """Record a declared (`derived=0`) link. Its inverse comes from the closure pass."""
    if not src or not dst or not ltype:
        return
    con.execute(
        "insert or ignore into links(src, type, dst, derived) values (?,?,?,0)", (src, ltype, dst)
    )


def _close_reverse_links(con: sqlite3.Connection) -> None:
    """Rebuild every reverse link from the declared rows.

    A reverse row lives under the *target* entity's `src`, so it is owned by no
    file and cannot be deleted per entity. Wiping `derived=1` wholesale and
    re-deriving keeps the table from accumulating mirrors of links that have
    since been removed from their source file. A row that both sides declare is
    stored once as `derived=0` and survives the wipe.
    """
    con.execute("delete from links where derived=1")
    for ltype, inverse in REVERSE_TYPE.items():
        con.execute(
            "insert or ignore into links(src, type, dst, derived)"
            " select dst, ?, src, 1 from links where type=? and derived=0",
            (inverse, ltype),
        )


def _put_typed_links(con: sqlite3.Connection, eid: str, entity: dict[str, Any]) -> None:
    for link in _as_list(entity.get("links")):
        if isinstance(link, dict):
            _put_link(con, eid, str(link.get("type") or ""), str(link.get("target") or ""))


def _put_fts(con: sqlite3.Connection, eid: str, kind: str, title: str, parts: list[Any]) -> None:
    body = "\n".join(str(p) for p in parts if p)
    con.execute(
        "insert into entity_fts(id, kind, title, body) values (?,?,?,?)",
        (eid, kind, title or "", body),
    )


def _as_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _as_list(value: Any) -> list[Any]:
    """Coerce a field that should be a list. A bare string becomes one element.

    Hand-edited frontmatter routinely writes `location: api/src/x.py` instead of
    a YAML list; iterating that string would index it character by character.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


# ── Per-kind ingestion ──────────────────────────────────────────


def _ingest_task(con, task: dict[str, Any], epic_id: str | None, schema: int,
                 repos: list[tuple[str, str]]) -> None:
    tid = task.get("id")
    if not tid:
        return
    _delete_entity(con, tid)
    sub_repo = task.get("sub_repo") or None
    anchors = [normalize_task_anchor(a, sub_repo) for a in _as_list(task.get("anchors")) if a]
    exact_paths = [p for p, kind in anchors if kind == "exact"]
    status = _as_str(task.get("status"))
    # In v3 the task itself lives in backlog.yaml and `tasks/<id>.md` only carries
    # heavy fields, so that file's removal must not look like the task's removal.
    source_file = f"tasks/{tid}.md" if schema >= SCHEMA_V4 else "backlog.yaml"
    _put_entity(con, {
        "id": tid,
        "kind": "task",
        "status": status,
        "title": _as_str(task.get("title")),
        "epic": epic_id,
        "phase": _as_str(task.get("phase")),
        "lane": _as_str(task.get("lane")),
        "repo": sub_repo or infer_repo(exact_paths, repos),
        "priority": _as_str(task.get("priority")),
        "created": _as_str(task.get("created")),
        "updated": _as_str(task.get("last_referenced") or task.get("completed")
                           or task.get("started") or task.get("created")),
        "archived": 1 if status == "archived" or task.get("archived") else 0,
        "file": source_file,
    })
    _put_paths(con, tid, [(p, kind, "anchors") for p, kind in anchors])
    _put_typed_links(con, tid, task)
    for dep in _as_list(task.get("depends_on")):
        _put_link(con, tid, "depends_on", str(dep))
    _put_fts(con, tid, "task", _as_str(task.get("title")) or "",
             [task.get("notes"), task.get("description"), task.get("review_instructions")])


def _ingest_epic(con, epic: dict[str, Any]) -> None:
    eid = epic.get("id")
    if not eid:
        return
    _delete_entity(con, eid)
    status = _as_str(epic.get("status"))
    _put_entity(con, {
        "id": eid,
        "kind": "epic",
        "status": status,
        "title": _as_str(epic.get("name")),
        "epic": eid,
        "phase": _as_str(epic.get("phase")),
        "lane": None,
        "repo": None,
        "priority": _as_str(epic.get("priority")),
        "created": _as_str(epic.get("created")),
        "updated": _as_str(epic.get("updated") or epic.get("created")),
        "archived": 1 if status == "archived" or epic.get("archived") else 0,
        "file": "backlog.yaml",  # the epic definition; epics/<id>.md is a body only
    })
    _put_typed_links(con, eid, epic)
    _put_fts(con, eid, "epic", _as_str(epic.get("name")) or "", [epic.get("description")])


def _ingest_defect(con, kind: str, fm: dict[str, Any], body: str, rel_file: str,
                   repos: list[tuple[str, str]], *, archived: bool) -> None:
    """Ingest a bug or an issue — both carry `location[]` plus prose paths."""
    eid = fm.get("id") or Path(rel_file).stem
    _delete_entity(con, eid)
    located = [normalize_location(loc) for loc in _as_list(fm.get("location")) if loc]
    located_set = set(located)
    prose = [p for p in extract_prose_paths(body) if p not in located_set]
    status = _as_str(fm.get("status"))
    created = _as_str(fm.get("discovered"))
    _put_entity(con, {
        "id": eid,
        "kind": kind,
        "status": status,
        "title": _as_str(fm.get("title")),
        "epic": None,
        "phase": None,
        "lane": None,
        "repo": infer_repo(located + prose, repos),
        "priority": _as_str(fm.get("severity")),
        "created": created,
        "updated": _as_str(fm.get("updated") or fm.get("status_changed") or created),
        "archived": 1 if archived or status == "archived" else 0,
        "file": rel_file,
    })
    _put_paths(con, eid,
               [(p, "exact", "location") for p in located] + [(p, "exact", "prose") for p in prose])
    _put_typed_links(con, eid, fm)
    if fm.get("adopted_into"):
        _put_link(con, eid, "relates_to", str(fm["adopted_into"]))
    for tid in _as_list(fm.get("related_tasks")):
        _put_link(con, eid, "relates_to", str(tid))
    _put_fts(con, eid, kind, _as_str(fm.get("title")) or "",
             [fm.get("impact"), fm.get("evidence"), body])


def _ingest_handover(con, fm: dict[str, Any], body: str, rel_file: str,
                     repos: list[tuple[str, str]], *, archived: bool) -> None:
    hid = fm.get("id") or Path(rel_file).stem
    _delete_entity(con, hid)
    prose = extract_prose_paths(body)
    tldr = _as_str(fm.get("tldr")) or ""
    _put_entity(con, {
        "id": hid,
        "kind": "handover",
        "status": _as_str(fm.get("status")),
        "title": tldr,
        "epic": None,
        "phase": None,
        "lane": None,
        "repo": infer_repo(prose, repos),
        "priority": None,
        "created": _as_str(fm.get("date")),
        "updated": _as_str(fm.get("status_changed") or fm.get("created") or fm.get("date")),
        "archived": 1 if archived else 0,
        "file": rel_file,
    })
    con.execute(
        "insert or replace into handovers(id, thread, tldr, next_action, session_kind, branch,"
        " tip_commit, supersedes) values (?,?,?,?,?,?,?,?)",
        (hid, _as_str(fm.get("thread")), tldr, _as_str(fm.get("next_action")),
         _as_str(fm.get("session_kind")), _as_str(fm.get("branch")),
         _as_str(fm.get("tip_commit")), _as_str(fm.get("supersedes"))),
    )
    for tid in _as_list(fm.get("task_ids")):
        con.execute("insert into handover_tasks(handover_id, task_id) values (?,?)", (hid, str(tid)))
    _put_paths(con, hid, [(p, "exact", "prose") for p in prose])
    _put_typed_links(con, hid, fm)
    if fm.get("supersedes"):
        _put_link(con, hid, "supersedes", str(fm["supersedes"]))
    _put_fts(con, hid, "handover", tldr, [fm.get("next_action"), body])


def _ingest_note_entity(con, kind: str, fm: dict[str, Any], body: str, rel_file: str,
                        repos: list[tuple[str, str]]) -> None:
    """Ingest a decision or an idea — frontmatter plus prose paths, no locations."""
    eid = fm.get("id") or Path(rel_file).stem
    _delete_entity(con, eid)
    prose = extract_prose_paths(body)
    created = _as_str(fm.get("created") or fm.get("created_at"))
    _put_entity(con, {
        "id": eid,
        "kind": kind,
        "status": _as_str(fm.get("status")),
        "title": _as_str(fm.get("title")),
        "epic": None,
        "phase": None,
        "lane": None,
        "repo": infer_repo(prose, repos),
        "priority": None,
        "created": created,
        "updated": _as_str(fm.get("updated") or created),
        "archived": 0,
        "file": rel_file,
    })
    _put_paths(con, eid, [(p, "exact", "prose") for p in prose])
    _put_typed_links(con, eid, fm)
    if fm.get("task_id"):
        _put_link(con, eid, "relates_to", str(fm["task_id"]))
    for tid in _as_list(fm.get("related_tasks")):
        _put_link(con, eid, "relates_to", str(tid))
    _put_fts(con, eid, kind, _as_str(fm.get("title")) or "", [fm.get("options"), body])


# ── Derived edges ───────────────────────────────────────────────


def _recompute_related(con: sqlite3.Connection) -> None:
    con.execute("delete from related")
    exact: dict[str, set[str]] = defaultdict(set)
    globs: dict[str, list[re.Pattern[str]]] = defaultdict(list)
    rows = con.execute(
        "select entity_id, path, match_kind from entity_paths"
        " where source in ('anchors','location')"
    ).fetchall()
    for eid, path, match_kind in rows:
        if match_kind == "glob":
            globs[eid].append(_glob_to_regex(path))
        else:
            exact[eid].add(path)

    shared: dict[tuple[str, str], set[str]] = defaultdict(set)
    by_path: dict[str, set[str]] = defaultdict(set)
    for eid, paths in exact.items():
        for path in paths:
            by_path[path].add(eid)
    for path, eids in by_path.items():
        if len(eids) < 2:
            continue
        for a, b in combinations(sorted(eids), 2):
            shared[(a, b)].add(path)
    for geid, patterns in globs.items():
        for eid, paths in exact.items():
            if eid == geid:
                continue
            key = (geid, eid) if geid < eid else (eid, geid)
            for path in paths:
                if any(pat.match(path) for pat in patterns):
                    shared[key].add(path)
    for (a, b), paths in shared.items():
        con.execute("insert into related(a, b, via, weight) values (?,?,'path',?)", (a, b, len(paths)))

    groups: dict[str, set[str]] = defaultdict(set)
    for hid, tid in con.execute("select handover_id, task_id from handover_tasks"):
        groups[hid].add(tid)
    handover_weight: dict[tuple[str, str], int] = defaultdict(int)
    for tids in groups.values():
        for a, b in combinations(sorted(tids), 2):
            handover_weight[(a, b)] += 1
    for (a, b), weight in handover_weight.items():
        con.execute("insert into related(a, b, via, weight) values (?,?,'handover',?)", (a, b, weight))


# ── Build ───────────────────────────────────────────────────────


def _scan_sources(root: Path) -> dict[str, tuple[float, int, str]]:
    """Map artifact-root-relative file → (mtime, size, kind) for every source file."""
    found: dict[str, tuple[float, int, str]] = {}
    for kind, pattern in SOURCE_GLOBS:
        if "*" in pattern:
            parent = root / Path(pattern).parent
            if not parent.is_dir():
                continue
            paths = sorted(parent.glob(Path(pattern).name))
        else:
            candidate = root / pattern
            paths = [candidate] if candidate.is_file() else []
        for p in paths:
            try:
                st = p.stat()
            except OSError:
                continue
            found[p.relative_to(root).as_posix()] = (st.st_mtime, st.st_size, kind)
    return found


def build_index(backlog_path: Path, data: dict | None = None, *,
                budget_s: float | None = None) -> IndexReport:
    """Bring the derived index at `.taskmaster/local/index.db` up to date.

    Only changed source files are re-ingested; rows for vanished files are
    dropped. When `budget_s` is exceeded the build stops early and the report
    is marked stale with the untouched files listed in `pending_files`.
    """
    started = time.perf_counter()
    backlog_path = Path(backlog_path)
    root = backlog_path.parent
    dbp = db_path(backlog_path)
    _ensure_local_dir(dbp.parent)
    con, full_rebuild = _open_build_db(dbp)
    errors: list[str] = []
    ingested = 0
    pending: list[str] = []
    stale = False
    try:
        repos = _load_repos(backlog_path)
        on_disk = _scan_sources(root)
        known = {row[0]: (row[1], row[2], row[3])
                 for row in con.execute("select file, mtime, size, kind from sources")}

        changed = [rel for rel, (mtime, size, _kind) in on_disk.items()
                   if rel not in known or known[rel][:2] != (mtime, size)]
        # Preserve SOURCE_GLOBS order so backlog.yaml (all tasks and epics) leads.
        order = {rel: i for i, rel in enumerate(on_disk)}
        changed.sort(key=lambda rel: order[rel])

        # A repo-definition edit changes repo inference for *every* entity, not
        # just the tasks, so it re-ingests the whole tree.
        if "project.yaml" in changed:
            changed = sorted(on_disk, key=lambda rel: order[rel])
        backlog_wide = any(rel in ("backlog.yaml", "project.yaml") for rel in changed)

        removed = [f for f in known if f not in on_disk]
        for rel in removed:
            if known[rel][2] in ("task", "epic"):
                # In v3 the entity still lives in backlog.yaml; only its heavy
                # fields are gone. Reconcile through the backlog, never by file.
                backlog_wide = True
            else:
                for (eid,) in con.execute("select id from entities where file=?", (rel,)).fetchall():
                    _delete_entity(con, eid)
            con.execute("delete from sources where file=?", (rel,))

        def _data() -> dict:
            nonlocal data
            if data is None:
                data = load_backlog_data(backlog_path)
            return data

        over_budget = budget_s is not None and time.perf_counter() - started > budget_s
        if backlog_wide and not over_budget:
            _ingest_backlog(con, _data(), repos)

        for i, rel in enumerate(changed):
            if budget_s is not None and time.perf_counter() - started > budget_s:
                pending = changed[i:]
                stale = True
                break
            try:
                _ingest_source(con, root, rel, on_disk[rel][2], repos, _data,
                               backlog_wide=backlog_wide)
            except (OSError, ValueError, yaml.YAMLError) as exc:
                errors.append(f"{rel}: {exc}")
                continue
            mtime, size, kind = on_disk[rel]
            con.execute(
                "insert or replace into sources(file, mtime, size, kind) values (?,?,?,?)",
                (rel, mtime, size, kind),
            )
            ingested += 1

        if not stale and (full_rebuild or ingested or removed):
            _close_reverse_links(con)
            _recompute_related(con)

        # Meta rows go in before the counts so `row_counts['meta']` is accurate;
        # `last_report` is seeded empty and filled once the report exists.
        built_at_dt = datetime.now(timezone.utc)
        built_at = built_at_dt.isoformat(timespec="seconds")
        meta_rows = [
            ("schema_version", str(SCHEMA_VERSION)),
            ("built_at", built_at),
            ("built_at_epoch", str(built_at_dt.timestamp())),
            ("last_report", ""),
        ]
        # Taken over the sources actually ingested, never over what is on disk:
        # a budget-truncated build must not claim the newest file is covered.
        mtime_max = con.execute("select max(mtime) from sources").fetchone()[0]
        if mtime_max is not None:
            meta_rows.append(("source_mtime_max", str(mtime_max)))
        for key, value in meta_rows:
            con.execute("insert or replace into meta(key, value) values (?,?)", (key, value))

        report = IndexReport(
            built_at=built_at,
            full_rebuild=full_rebuild,
            files_ingested=ingested,
            pending_files=pending,
            stale=stale,
            row_counts={t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in TABLES},
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            errors=errors,
        )
        con.execute("update meta set value=? where key='last_report'", (json.dumps(asdict(report)),))
        con.commit()
        return report
    finally:
        con.close()


def _ingest_backlog(con, data: dict, repos: list[tuple[str, str]]) -> None:
    """Re-ingest every task and epic from the loaded backlog, and only those.

    Runs whenever `backlog.yaml` or `project.yaml` changed — repo inference
    follows repo-definition edits — or when a task/epic detail file appeared or
    vanished. Tasks and epics have no per-entity file to disappear in v3, so the
    reconciliation of deleted ones is only reachable from here.
    """
    schema = detect_schema_version(data)
    live: set[str] = set()
    for epic in data.get("epics", []):
        _ingest_epic(con, epic)
        live.add(epic.get("id"))
        for task in epic.get("tasks", []):
            _ingest_task(con, task, epic.get("id"), schema, repos)
            live.add(task.get("id"))
    gone = [eid for (eid,) in con.execute(
        "select id from entities where kind in ('task','epic')") if eid not in live]
    for eid in gone:
        _delete_entity(con, eid)


def _ingest_source(con, root: Path, rel: str, kind: str, repos: list[tuple[str, str]],
                   data_fn, *, backlog_wide: bool) -> None:
    """Ingest one changed source file into the open index connection."""
    if kind in ("backlog", "project"):
        return  # handled wholesale by _ingest_backlog before the file loop
    if kind in ("task", "epic"):
        if backlog_wide:
            return  # already re-ingested wholesale from the backlog index
        entity_id = Path(rel).stem
        data = data_fn()
        schema = detect_schema_version(data)
        for epic in data.get("epics", []):
            if kind == "epic" and epic.get("id") == entity_id:
                _ingest_epic(con, epic)
                return
            for task in epic.get("tasks", []):
                if kind == "task" and task.get("id") == entity_id:
                    _ingest_task(con, task, epic.get("id"), schema, repos)
                    return
        return
    if kind == "phase":
        return  # phases are backlog structure, not indexed entities

    fm, body = parse_frontmatter((root / rel).read_text(encoding="utf-8"))
    archived = "/archive/" in f"/{rel}"
    if kind in ("bug", "issue"):
        _ingest_defect(con, kind, fm, body, rel, repos, archived=archived)
    elif kind == "handover":
        _ingest_handover(con, fm, body, rel, repos, archived=archived)
    elif kind in ("decision", "idea"):
        _ingest_note_entity(con, kind, fm, body, rel, repos)


# ── CLI ─────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    force_full = "--full" in args
    args = [a for a in args if a != "--full"]
    target = Path(args[0]) if args else Path.cwd()
    backlog_path = resolve_backlog_path(target)
    if not backlog_path.exists():
        print(f"no backlog found at {backlog_path}", file=sys.stderr)
        return 1
    if force_full:
        dbp = db_path(backlog_path)
        if dbp.exists():
            dbp.unlink()
    print(json.dumps(asdict(build_index(backlog_path)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
